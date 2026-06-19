"""
自定义画布节点编辑器（支持缩放/平移）—— Stage 1：渲染 + 缩放 + 平移 + 选中 + 拖动节点。

为何自绘：DearPyGui 的 node_editor 控件不支持缩放；而绘图层（drawlist）可以按任意
比例绘制。于是我们把节点/端口/连线/参数都"画"出来（参数以"标签: 值"文本画在节点内，
所以缩放后依然可见、可一眼看全），编辑参数时再弹出小输入框（后续 Stage）。

坐标系：世界坐标存于 graph.positions；屏幕坐标 = 世界*zoom + 偏移(ox,oy)。
后续 Stage：Stage2 连线创建/删除，Stage3 点击参数行弹出编辑，Stage4 实时状态上色。
"""
from __future__ import annotations

from typing import Optional

import dearpygui.dearpygui as dpg

from ..core import Graph
from ..core.types import PortKind
from ..layout import estimate_size, layered_layout, needs_layout

# 节点内部行高（世界坐标，与 layout.estimate_size 的高度公式一致）
HEADER = 36
ROW_IO = 24
ROW_P = 30
PAD = 16

# 颜色
C_NODE = (44, 48, 58, 255)
C_NODE_SEL = (60, 66, 80, 255)
C_BORDER = (90, 96, 108, 255)
C_BORDER_SEL = (255, 184, 64, 255)
C_TITLE = (232, 234, 240, 255)
C_PARAM = (170, 176, 188, 255)
C_EXEC = (210, 210, 140, 255)
C_DATA = (120, 180, 240, 255)
C_LINK_EXEC = (150, 150, 160, 255)
C_LINK_DATA = (110, 150, 210, 255)
C_HUD = (150, 156, 168, 255)


class CanvasEditor:
    def __init__(self, graph: Optional[Graph] = None):
        self.graph = graph or Graph(name="未命名流程")
        self.zoom = 1.0
        self.ox = 40.0
        self.oy = 40.0
        self.sizes: dict[str, tuple] = {}
        self.selected: set[str] = set()

        self.drawlist = "canvas_drawlist"
        self.cw = 1260
        self.ch = 720

        self._mode = None            # 'drag' | 'pan'
        self._drag_node = None
        self._node_start = (0, 0)
        self._pan_start = (0, 0)
        self._current_path = None

    # ==================== 入口 ====================
    def run(self):
        dpg.create_context()
        self._setup_fonts()
        self.build_ui()
        dpg.create_viewport(title="AOE4 Flow Editor", width=1320, height=820)
        dpg.setup_dearpygui()
        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()
        dpg.set_viewport_resize_callback(self._on_viewport_resize)
        self.fit_view()
        dpg.start_dearpygui()
        dpg.destroy_context()

    def _setup_fonts(self, size: int = 18):
        import os, glob
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        cands = ["simhei.ttf", "Deng.ttf", "msyh.ttc", "msyh.ttf", "simsun.ttc"]
        path = next((os.path.join(fonts_dir, c) for c in cands
                     if os.path.exists(os.path.join(fonts_dir, c))), None)
        if path is None:
            hits = glob.glob(os.path.join(fonts_dir, "msyh*.tt*")) or glob.glob(os.path.join(fonts_dir, "sim*.tt*"))
            path = hits[0] if hits else None
        if path:
            with dpg.font_registry():
                f = dpg.add_font(path, size)   # 新版 DPG 字形范围自动包含，无需 range_hint
            dpg.bind_font(f)

    # ==================== UI ====================
    def build_ui(self):
        with dpg.window(tag="main_window"):
            with dpg.menu_bar():
                with dpg.menu(label="文件"):
                    dpg.add_menu_item(label="新建", callback=self._on_new)
                    dpg.add_menu_item(label="打开...", callback=lambda: dpg.show_item("c_open"))
                    dpg.add_menu_item(label="保存", callback=self._on_save)
                    dpg.add_menu_item(label="另存为...", callback=lambda: dpg.show_item("c_save"))
                with dpg.menu(label="编辑"):
                    dpg.add_menu_item(label="自动排版", callback=self._on_autolayout)
                    dpg.add_menu_item(label="删除选中节点 (Del)", callback=self._delete_selected)
                with dpg.menu(label="视图"):
                    dpg.add_menu_item(label="适应窗口 (F)", callback=lambda: self.fit_view())
                    dpg.add_menu_item(label="重置缩放", callback=self._reset_view)
                self.status_id = dpg.add_text("", tag="c_status")
            with dpg.child_window(tag="canvas_host", border=False):
                dpg.add_drawlist(width=self.cw, height=self.ch, tag=self.drawlist)

        with dpg.handler_registry():
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            dpg.add_mouse_down_handler(dpg.mvMouseButton_Left, callback=self._on_mouse_down)
            dpg.add_mouse_drag_handler(dpg.mvMouseButton_Left, callback=self._on_drag)
            dpg.add_mouse_release_handler(dpg.mvMouseButton_Left, callback=self._on_release)
            dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self._delete_selected)
            dpg.add_key_press_handler(dpg.mvKey_F, callback=lambda: self.fit_view())

        self._file_dialogs()
        self._ensure_layout()
        self._recompute_sizes()
        self.redraw()

    def _file_dialogs(self):
        with dpg.file_dialog(directory_selector=False, show=False, tag="c_open",
                             width=600, height=400, callback=self._on_open_selected):
            dpg.add_file_extension(".json")
        with dpg.file_dialog(directory_selector=False, show=False, tag="c_save",
                             width=600, height=400, default_filename="flow.flow.json",
                             callback=self._on_save_selected):
            dpg.add_file_extension(".json")

    def _on_viewport_resize(self):
        w = max(400, dpg.get_viewport_client_width())
        h = max(300, dpg.get_viewport_client_height() - 40)
        self.cw, self.ch = w, h
        dpg.configure_item(self.drawlist, width=w, height=h)
        self.redraw()

    # ==================== 坐标变换 ====================
    def w2s(self, wx, wy):
        return (wx * self.zoom + self.ox, wy * self.zoom + self.oy)

    def s2w(self, sx, sy):
        return ((sx - self.ox) / self.zoom, (sy - self.oy) / self.zoom)

    def _recompute_sizes(self):
        self.sizes = {nid: estimate_size(self.graph.nodes[nid]) for nid in self.graph.nodes}

    def _ensure_layout(self):
        if needs_layout(self.graph):
            layered_layout(self.graph)

    # ==================== 端口/几何 ====================
    def _node_rows(self, node):
        ins = [p for p in node.inputs]
        outs = [p for p in node.outputs]
        return ins, outs

    def _port_world(self, nid):
        """返回 {(port_name, dir): (wx, wy)}，dir in 'in'/'out'。"""
        node = self.graph.nodes[nid]
        wx, wy = self.graph.positions.get(nid, (0, 0))
        ww, wh = self.sizes[nid]
        ins, outs = self._node_rows(node)
        res = {}
        for i, p in enumerate(ins):
            y = wy + HEADER + i * ROW_IO + ROW_IO / 2
            res[(p.name, "in")] = (wx, y, p)
        base = HEADER + len(ins) * ROW_IO + len(node.params) * ROW_P
        for k, p in enumerate(outs):
            y = wy + base + k * ROW_IO + ROW_IO / 2
            res[(p.name, "out")] = (wx + ww, y, p)
        return res

    # ==================== 绘制 ====================
    def redraw(self):
        if not dpg.does_item_exist(self.drawlist):
            return
        dpg.delete_item(self.drawlist, children_only=True)
        z = self.zoom

        # 先画连线（在节点下层）
        port_cache = {nid: self._port_world(nid) for nid in self.graph.nodes}
        for kind, edges, color in (("exec", self.graph.exec_edges, C_LINK_EXEC),
                                   ("data", self.graph.data_edges, C_LINK_DATA)):
            for e in edges:
                src = port_cache.get(e.src_id, {}).get((e.src_port, "out"))
                dst = port_cache.get(e.dst_id, {}).get((e.dst_port, "in"))
                if not src or not dst:
                    continue
                x1, y1 = self.w2s(src[0], src[1])
                x2, y2 = self.w2s(dst[0], dst[1])
                dxc = max(40.0, abs(x2 - x1) * 0.5)
                dpg.draw_bezier_cubic((x1, y1), (x1 + dxc, y1), (x2 - dxc, y2), (x2, y2),
                                      color=color, thickness=max(1.0, 2.0 * z), parent=self.drawlist)

        # 再画节点
        for nid in self.graph.nodes:
            self._draw_node(nid, port_cache[nid])

        # HUD（不随缩放）
        dpg.draw_text((10, 8), f"缩放 {int(self.zoom * 100)}%  |  滚轮缩放 · 空白拖动平移 · F 适应窗口",
                      size=15, color=C_HUD, parent=self.drawlist)

    def _draw_node(self, nid, ports):
        node = self.graph.nodes[nid]
        wx, wy = self.graph.positions.get(nid, (0, 0))
        ww, wh = self.sizes[nid]
        x1, y1 = self.w2s(wx, wy)
        x2, y2 = self.w2s(wx + ww, wy + wh)
        z = self.zoom
        sel = nid in self.selected
        dpg.draw_rectangle((x1, y1), (x2, y2), rounding=6 * z,
                           fill=(C_NODE_SEL if sel else C_NODE),
                           color=(C_BORDER_SEL if sel else C_BORDER),
                           thickness=(2.0 if sel else 1.0), parent=self.drawlist)
        # 标题
        dpg.draw_text((x1 + 10 * z, y1 + 9 * z), node.title, size=15 * z, color=C_TITLE, parent=self.drawlist)
        dpg.draw_line((x1, y1 + HEADER * z), (x2, y1 + HEADER * z), color=C_BORDER, thickness=1.0, parent=self.drawlist)

        # 端口圆点 + 标签
        for (pname, pdir), (pwx, pwy, p) in ports.items():
            px, py = self.w2s(pwx, pwy)
            col = C_EXEC if p.kind == PortKind.EXEC else C_DATA
            dpg.draw_circle((px, py), 4 * z, fill=col, color=col, parent=self.drawlist)
            if pdir == "in":
                dpg.draw_text((px + 8 * z, py - 8 * z), p.display, size=12 * z, color=C_PARAM, parent=self.drawlist)
            else:
                tw = len(p.display) * 7 * z + 8 * z
                dpg.draw_text((px - tw, py - 8 * z), p.display, size=12 * z, color=C_PARAM, parent=self.drawlist)

        # 参数行（标签: 值），画在输入端口之后
        base = HEADER + len(node.inputs) * ROW_IO
        for j, spec in enumerate(node.params):
            py = wy + base + j * ROW_P + ROW_P / 2
            sx, sy = self.w2s(wx + 10, py)
            txt = f"{spec.label}: {self._fmt_value(node.values.get(spec.key))}"
            dpg.draw_text((sx, sy - 8 * z), txt, size=12 * z, color=C_PARAM, parent=self.drawlist)

    @staticmethod
    def _fmt_value(v) -> str:
        if isinstance(v, bool):
            return "是" if v else "否"
        if isinstance(v, (list, tuple)):
            if v and isinstance(v[0], str):     # 模板路径列表
                import os
                return "、".join(os.path.basename(str(x)) for x in v) or "（空）"
            return ",".join(str(x) for x in v)
        s = "" if v is None else str(v)
        return s if len(s) <= 22 else s[:21] + "…"

    # ==================== 命中测试 ====================
    def _node_at(self, sx, sy):
        wx, wy = self.s2w(sx, sy)
        hit = None
        for nid in self.graph.nodes:        # 后画的在上层，遍历取最后命中
            nx, ny = self.graph.positions.get(nid, (0, 0))
            nw, nh = self.sizes[nid]
            if nx <= wx <= nx + nw and ny <= wy <= ny + nh:
                hit = nid
        return hit

    # ==================== 交互 ====================
    def _mouse(self):
        return dpg.get_drawing_mouse_pos()

    def _on_wheel(self, sender, app_data):
        if not self._mouse_over_canvas():
            return
        mx, my = self._mouse()
        wx, wy = self.s2w(mx, my)
        factor = 1.1 ** app_data
        self.zoom = max(0.2, min(3.0, self.zoom * factor))
        self.ox = mx - wx * self.zoom
        self.oy = my - wy * self.zoom
        self.redraw()

    def _on_mouse_down(self, sender, app_data):
        if self._mode is not None or not self._mouse_over_canvas():
            return
        mx, my = self._mouse()
        nid = self._node_at(mx, my)
        if nid:
            self.selected = {nid}
            self._mode = "drag"
            self._drag_node = nid
            self._node_start = self.graph.positions.get(nid, (0, 0))
        else:
            self.selected = set()
            self._mode = "pan"
            self._pan_start = (self.ox, self.oy)
        self.redraw()

    def _on_drag(self, sender, app_data):
        if self._mode is None:
            return
        _, dx, dy = app_data
        if self._mode == "drag" and self._drag_node:
            sx, sy = self._node_start
            self.graph.positions[self._drag_node] = (sx + dx / self.zoom, sy + dy / self.zoom)
        elif self._mode == "pan":
            self.ox = self._pan_start[0] + dx
            self.oy = self._pan_start[1] + dy
        self.redraw()

    def _on_release(self, sender, app_data):
        self._mode = None
        self._drag_node = None

    def _mouse_over_canvas(self) -> bool:
        return dpg.is_item_hovered(self.drawlist) if dpg.does_item_exist(self.drawlist) else False

    # ==================== 视图 ====================
    def fit_view(self):
        if not self.graph.nodes:
            return
        xs, ys, xe, ye = 1e9, 1e9, -1e9, -1e9
        for nid in self.graph.nodes:
            x, y = self.graph.positions.get(nid, (0, 0))
            w, h = self.sizes[nid]
            xs, ys = min(xs, x), min(ys, y)
            xe, ye = max(xe, x + w), max(ye, y + h)
        gw, gh = max(1, xe - xs), max(1, ye - ys)
        self.zoom = max(0.2, min(1.5, min((self.cw - 60) / gw, (self.ch - 60) / gh)))
        self.ox = 30 - xs * self.zoom
        self.oy = 30 - ys * self.zoom
        self.redraw()

    def _reset_view(self):
        self.zoom, self.ox, self.oy = 1.0, 40.0, 40.0
        self.redraw()

    def _on_autolayout(self):
        layered_layout(self.graph)
        self._recompute_sizes()
        self.fit_view()
        self._set_status("已自动排版")

    # ==================== 增删/存读 ====================
    def _delete_selected(self, *args):
        if not self.selected:
            return
        for nid in list(self.selected):
            self.graph.nodes.pop(nid, None)
            self.graph.positions.pop(nid, None)
            self.graph.exec_edges = [e for e in self.graph.exec_edges if e.src_id != nid and e.dst_id != nid]
            self.graph.data_edges = [e for e in self.graph.data_edges if e.src_id != nid and e.dst_id != nid]
        self.selected = set()
        self._recompute_sizes()
        self.redraw()

    def _on_new(self):
        self.graph = Graph(name="未命名流程")
        self.selected = set()
        self._recompute_sizes()
        self.redraw()

    def _on_open_selected(self, sender, app_data):
        import os
        path = app_data.get("file_path_name")
        if path and os.path.exists(path):
            self.load(path)

    def _on_save_selected(self, sender, app_data):
        path = app_data.get("file_path_name")
        if path:
            self.save(path)

    def _on_save(self):
        if self._current_path:
            self.save(self._current_path)
        else:
            dpg.show_item("c_save")

    def load(self, path):
        import os
        self.graph = Graph.load(path)
        self.selected = set()
        self._ensure_layout()
        self._recompute_sizes()
        self._current_path = path
        self.fit_view()
        self._set_status(f"已打开 {os.path.basename(path)} | 节点 {len(self.graph.nodes)}")

    def save(self, path):
        import os
        self.graph.save(path)
        self._current_path = path
        self._set_status(f"已保存 {os.path.basename(path)}")

    def _set_status(self, text):
        if dpg.does_item_exist("c_status"):
            dpg.set_value("c_status", text)

"""
DearPyGui 节点编辑器（Phase 1）。

能力：
- 从注册表生成调色板，点击添加节点；按 ParamSpec 自动生成参数控件。
- 画布上拖拽连线：自动校验 执行↔执行 / 数据↔数据、且方向为 输出→输入，写回 Graph。
- 删除选中节点（Delete 键）并清理相关连线。
- 打开 / 保存流程图 JSON（含节点坐标）。

设计：DPG 的 node_attribute 不区分"执行/数据"，故本类自行记录每个连接点的
(节点, 端口, 种类exec|data, 方向in|out)，在连线回调里据此校验并落到 Graph 的
exec_edges / data_edges。

UI 构建与渲染解耦：build_ui() 只创建控件，可在手动渲染若干帧的"无头冒烟测试"中复用；
run() 才真正开窗口进入事件循环。
"""
from __future__ import annotations

import os
from typing import Optional

import dearpygui.dearpygui as dpg

from ..core import Graph, create_node, registry
from ..core.types import PortKind
from ..layout import layered_layout, needs_layout

# 端口标记：执行用三角、数据用圆点，便于一眼区分
_EXEC_MARK = ">"
_DATA_MARK = "o"


class EditorApp:
    def __init__(self, graph: Optional[Graph] = None):
        self.graph = graph or Graph(name="未命名流程")
        self._uid = 0  # 新建节点的 id 计数

        # DPG 与图的双向映射
        self.node_to_dpg: dict[str, int] = {}
        self.dpg_to_node: dict[int, str] = {}
        self.attr_info: dict[int, dict] = {}            # attr_id -> {node, port, kind, dir}
        self.port_to_attr: dict[tuple, int] = {}        # (node_id, port, dir) -> attr_id
        self.link_info: dict[int, dict] = {}            # link_id -> {kind, src, src_port, dst, dst_port}

        self.editor_id: Optional[int] = None
        self.status_id: Optional[int] = None
        self._current_path: Optional[str] = None

    # ==================== 公开入口 ====================
    def run(self):
        dpg.create_context()
        self.build_ui()
        # 视口标题用英文：DPG/GLFW 的 OS 标题栏对中文会乱码（界面内中文由字体正常渲染）
        dpg.create_viewport(title="AOE4 Flow Editor", width=1280, height=800)
        dpg.setup_dearpygui()
        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()

    # ==================== 中文字体 ====================
    def _setup_fonts(self, size: int = 18):
        """加载含中文字形的系统字体并全局绑定，否则中文显示为问号。"""
        import glob
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        # 优先 .ttf（DearPyGui 的 stb 字体后端对 .ttc 集合支持不稳），再退回 .ttc
        candidates = ["simhei.ttf", "Deng.ttf", "msyh.ttc", "msyh.ttf", "simsun.ttc", "msyhl.ttc"]
        path = next((os.path.join(fonts_dir, c) for c in candidates
                     if os.path.exists(os.path.join(fonts_dir, c))), None)
        if path is None:  # 兜底：目录里任意一个中文常见字体
            hits = glob.glob(os.path.join(fonts_dir, "msyh*.tt*")) or glob.glob(os.path.join(fonts_dir, "sim*.tt*"))
            path = hits[0] if hits else None
        if path is None:
            self._set_status("未找到中文字体，界面中文可能显示为问号")
            return
        with dpg.font_registry():
            f = dpg.add_font(path, size)   # 新版 DPG 字形范围自动包含，无需 range_hint
        dpg.bind_font(f)

    # ==================== UI 构建 ====================
    def build_ui(self):
        self._setup_fonts()
        with dpg.window(tag="main_window"):
            with dpg.menu_bar():
                with dpg.menu(label="文件"):
                    dpg.add_menu_item(label="新建", callback=self._on_new)
                    dpg.add_menu_item(label="打开...", callback=self._show_open_dialog)
                    dpg.add_menu_item(label="保存", callback=self._on_save)
                    dpg.add_menu_item(label="另存为...", callback=self._show_save_dialog)
                with dpg.menu(label="编辑"):
                    dpg.add_menu_item(label="自动排版", callback=self._auto_layout)
                    dpg.add_menu_item(label="删除选中节点/连线 (Del)", callback=self._delete_selected)
                self.status_id = dpg.add_text("", tag="status_text")

            with dpg.group(horizontal=True):
                # 左侧调色板
                with dpg.child_window(width=240, tag="palette"):
                    dpg.add_text("节点库")
                    dpg.add_separator()
                    self._build_palette()
                # 右侧画布
                with dpg.child_window(tag="canvas"):
                    self.editor_id = dpg.add_node_editor(
                        callback=self._on_link,
                        delink_callback=self._on_delink,
                        minimap=True,
                        minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
                    )

        # Delete 键删除选中节点
        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self._delete_selected)

        self._file_dialogs()
        self._populate_from_graph()
        self._set_status(f"流程: {self.graph.name}  |  节点 {len(self.graph.nodes)}")

    def _build_palette(self):
        by_cat: dict[str, list] = {}
        for type_id, cls in registry().items():
            by_cat.setdefault(cls.category, []).append((type_id, cls))
        for cat in sorted(by_cat):
            with dpg.tree_node(label=cat, default_open=True):
                for type_id, cls in sorted(by_cat[cat], key=lambda x: x[1].title):
                    dpg.add_button(label=cls.title, width=-1, user_data=type_id,
                                   callback=self._on_palette_click)

    def _file_dialogs(self):
        with dpg.file_dialog(directory_selector=False, show=False, tag="open_dialog",
                             width=600, height=400, callback=self._on_open_selected):
            dpg.add_file_extension(".json")
        with dpg.file_dialog(directory_selector=False, show=False, tag="save_dialog",
                             width=600, height=400, default_filename="flow.flow.json",
                             callback=self._on_save_selected):
            dpg.add_file_extension(".json")

    # ==================== 从图生成节点/连线 ====================
    def _populate_from_graph(self):
        if needs_layout(self.graph):      # 节点都堆在原点时自动排版，避免叠在左上角
            layered_layout(self.graph)
        for node_id in self.graph.nodes:
            self._render_node(node_id)
        for e in self.graph.exec_edges:
            self._render_link("exec", e.src_id, e.src_port, e.dst_id, e.dst_port)
        for e in self.graph.data_edges:
            self._render_link("data", e.src_id, e.src_port, e.dst_id, e.dst_port)

    def _render_node(self, node_id: str):
        node = self.graph.nodes[node_id]
        pos = self.graph.positions.get(node_id, (0, 0))
        with dpg.node(label=f"{node.title}", parent=self.editor_id, pos=pos) as dpg_node:
            self.node_to_dpg[node_id] = dpg_node
            self.dpg_to_node[dpg_node] = node_id

            # 输入端口（执行 + 数据），左侧
            for port in node.inputs:
                kind = "exec" if port.kind == PortKind.EXEC else "data"
                mark = _EXEC_MARK if kind == "exec" else _DATA_MARK
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as a:
                    dpg.add_text(f"{mark} {port.display}")
                self._reg_attr(a, node_id, port.name, kind, "in")

            # 参数（静态，无连接点）
            for spec in node.params:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    self._add_param_widget(node, spec)

            # 输出端口，右侧
            for port in node.outputs:
                kind = "exec" if port.kind == PortKind.EXEC else "data"
                mark = _EXEC_MARK if kind == "exec" else _DATA_MARK
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as a:
                    dpg.add_text(f"{port.display} {mark}")
                self._reg_attr(a, node_id, port.name, kind, "out")

    def _reg_attr(self, attr_id, node_id, port, kind, direction):
        self.attr_info[attr_id] = {"node": node_id, "port": port, "kind": kind, "dir": direction}
        self.port_to_attr[(node_id, port, direction)] = attr_id

    def _render_link(self, kind, src_id, src_port, dst_id, dst_port):
        a_out = self.port_to_attr.get((src_id, src_port, "out"))
        a_in = self.port_to_attr.get((dst_id, dst_port, "in"))
        if a_out is None or a_in is None:
            return
        link = dpg.add_node_link(a_out, a_in, parent=self.editor_id)
        self.link_info[link] = {"kind": kind, "src": src_id, "src_port": src_port,
                                "dst": dst_id, "dst_port": dst_port}

    # ==================== 参数控件 ====================
    def _add_param_widget(self, node, spec):
        val = node.values.get(spec.key)
        ud = (node, spec)
        label = spec.label
        w = 170
        if spec.ptype == "int":
            dpg.add_input_int(label=label, default_value=int(val or 0), width=w,
                              callback=self._param_cb, user_data=ud)
        elif spec.ptype == "float":
            dpg.add_input_float(label=label, default_value=float(val or 0.0), width=w,
                                step=spec.step or 0, callback=self._param_cb, user_data=ud)
        elif spec.ptype == "bool":
            dpg.add_checkbox(label=label, default_value=bool(val), callback=self._param_cb, user_data=ud)
        elif spec.ptype == "enum":
            items = [str(c) for c in (spec.choices or [])]
            dpg.add_combo(items, label=label, default_value=str(val), width=w,
                          callback=self._param_cb, user_data=ud)
        else:
            # str / regex / key / keys / region / point / color / template / templates
            dpg.add_input_text(label=label, default_value=self._to_text(val), width=w,
                               callback=self._param_cb, user_data=ud)

    @staticmethod
    def _to_text(val) -> str:
        if isinstance(val, (list, tuple)):
            return ",".join(str(x) for x in val)
        return "" if val is None else str(val)

    def _param_cb(self, sender, app_data, user_data):
        node, spec = user_data
        node.values[spec.key] = self._parse_value(spec, app_data)

    @staticmethod
    def _parse_value(spec, app_data):
        t = spec.ptype
        if t in ("int",):
            return int(app_data)
        if t in ("float",):
            return float(app_data)
        if t in ("bool",):
            return bool(app_data)
        if t in ("enum", "str", "regex", "key", "keys"):
            return app_data
        if t in ("region", "point", "color"):
            parts = [p.strip() for p in str(app_data).split(",") if p.strip() != ""]
            out = []
            for p in parts:
                try:
                    out.append(int(p))
                except ValueError:
                    out.append(p)
            return out
        if t == "template":
            return app_data
        if t == "templates":
            return [p.strip() for p in str(app_data).replace("\n", ",").split(",") if p.strip()]
        return app_data

    # ==================== 连线回调 ====================
    def _on_link(self, sender, app_data):
        a1, a2 = app_data
        i1, i2 = self.attr_info.get(a1), self.attr_info.get(a2)
        if not i1 or not i2:
            return
        # 规整为 (输出, 输入)
        if i1["dir"] == "out" and i2["dir"] == "in":
            out_attr, in_attr, out_i, in_i = a1, a2, i1, i2
        elif i2["dir"] == "out" and i1["dir"] == "in":
            out_attr, in_attr, out_i, in_i = a2, a1, i2, i1
        else:
            self._set_status("连线无效：必须从输出连到输入")
            return
        if out_i["kind"] != in_i["kind"]:
            self._set_status("连线无效：执行线与数据线不能混接")
            return

        kind = out_i["kind"]
        if kind == "data":
            # 一个数据输入只能有一个来源：移除旧连接
            self._remove_links_into(in_i["node"], in_i["port"])
            self.graph.connect_data(out_i["node"], out_i["port"], in_i["node"], in_i["port"])
        else:
            self.graph.connect_exec(out_i["node"], out_i["port"], in_i["node"], in_i["port"])

        link = dpg.add_node_link(out_attr, in_attr, parent=self.editor_id)
        self.link_info[link] = {"kind": kind, "src": out_i["node"], "src_port": out_i["port"],
                                "dst": in_i["node"], "dst_port": in_i["port"]}
        self._set_status(f"已连接 {out_i['node']}.{out_i['port']} -> {in_i['node']}.{in_i['port']}")

    def _on_delink(self, sender, app_data):
        link = app_data
        self._drop_link(link)

    def _drop_link(self, link):
        info = self.link_info.pop(link, None)
        if info:
            self._remove_graph_edge(info)
        if dpg.does_item_exist(link):
            dpg.delete_item(link)

    def _remove_graph_edge(self, info):
        if info["kind"] == "exec":
            self.graph.exec_edges = [
                e for e in self.graph.exec_edges
                if not (e.src_id == info["src"] and e.src_port == info["src_port"]
                        and e.dst_id == info["dst"] and e.dst_port == info["dst_port"])
            ]
        else:
            self.graph.data_edges = [
                e for e in self.graph.data_edges
                if not (e.dst_id == info["dst"] and e.dst_port == info["dst_port"])
            ]

    def _remove_links_into(self, node_id, port):
        for link, info in list(self.link_info.items()):
            if info["kind"] == "data" and info["dst"] == node_id and info["dst_port"] == port:
                self._drop_link(link)

    # ==================== 增删节点 ====================
    def _new_node_id(self, type_id: str) -> str:
        base = type_id.split(".")[-1]
        self._uid += 1
        nid = f"{base}_{self._uid}"
        while nid in self.graph.nodes:
            self._uid += 1
            nid = f"{base}_{self._uid}"
        return nid

    def _on_palette_click(self, sender, app_data, user_data):
        self._add_node(user_data)

    def _add_node(self, type_id: str, pos=None):
        node = create_node(type_id)
        node_id = self._new_node_id(type_id)
        self.graph.add(node_id, node, pos or (30 + 20 * (self._uid % 10), 30 + 20 * (self._uid % 10)))
        self._render_node(node_id)
        self._set_status(f"已添加 {node.title} ({node_id})")
        return node_id

    def _delete_selected(self, *args):
        if self.editor_id is None:
            return
        # 先删选中的连线，再删选中的节点
        for link in dpg.get_selected_links(self.editor_id):
            self._drop_link(link)
        for dpg_node in dpg.get_selected_nodes(self.editor_id):
            node_id = self.dpg_to_node.get(dpg_node)
            if node_id:
                self._remove_node(node_id)

    def _auto_layout(self, *args):
        layered_layout(self.graph)
        self._reset_canvas(self.graph)
        self._set_status("已自动排版")

    def _remove_node(self, node_id: str):
        # 删除相关连线
        for link, info in list(self.link_info.items()):
            if info["src"] == node_id or info["dst"] == node_id:
                self._drop_link(link)
        # 从图移除节点与其边
        self.graph.nodes.pop(node_id, None)
        self.graph.positions.pop(node_id, None)
        self.graph.exec_edges = [e for e in self.graph.exec_edges
                                 if e.src_id != node_id and e.dst_id != node_id]
        self.graph.data_edges = [e for e in self.graph.data_edges
                                 if e.src_id != node_id and e.dst_id != node_id]
        # 清理 DPG 项与映射
        dpg_node = self.node_to_dpg.pop(node_id, None)
        if dpg_node is not None:
            self.dpg_to_node.pop(dpg_node, None)
            if dpg.does_item_exist(dpg_node):
                dpg.delete_item(dpg_node)
        for key in [k for k, v in self.attr_info.items() if v["node"] == node_id]:
            self.attr_info.pop(key, None)
        for key in [k for k in self.port_to_attr if k[0] == node_id]:
            self.port_to_attr.pop(key, None)
        self._set_status(f"已删除节点 {node_id}")

    # ==================== 存读 ====================
    def _sync_positions(self):
        for node_id, dpg_node in self.node_to_dpg.items():
            if dpg.does_item_exist(dpg_node):
                x, y = dpg.get_item_pos(dpg_node)
                self.graph.positions[node_id] = (x, y)

    def _on_new(self, *args):
        self._reset_canvas(Graph(name="未命名流程"))

    def _reset_canvas(self, graph: Graph):
        if self.editor_id is not None and dpg.does_item_exist(self.editor_id):
            dpg.delete_item(self.editor_id, children_only=True)
        self.graph = graph
        self.node_to_dpg.clear(); self.dpg_to_node.clear()
        self.attr_info.clear(); self.port_to_attr.clear(); self.link_info.clear()
        self._populate_from_graph()
        self._set_status(f"流程: {self.graph.name}  |  节点 {len(self.graph.nodes)}")

    def _show_open_dialog(self, *args):
        dpg.show_item("open_dialog")

    def _show_save_dialog(self, *args):
        dpg.show_item("save_dialog")

    def _on_open_selected(self, sender, app_data):
        path = app_data.get("file_path_name")
        if path and os.path.exists(path):
            self.load(path)

    def _on_save_selected(self, sender, app_data):
        path = app_data.get("file_path_name")
        if path:
            self.save(path)

    def _on_save(self, *args):
        if self._current_path:
            self.save(self._current_path)
        else:
            self._show_save_dialog()

    def load(self, path: str):
        self._reset_canvas(Graph.load(path))
        self._current_path = path
        self._set_status(f"已打开 {os.path.basename(path)}  |  节点 {len(self.graph.nodes)}")

    def save(self, path: str):
        self._sync_positions()
        self.graph.save(path)
        self._current_path = path
        self._set_status(f"已保存 {os.path.basename(path)}")

    # ==================== 杂项 ====================
    def _set_status(self, text: str):
        if self.status_id is not None and dpg.does_item_exist(self.status_id):
            dpg.set_value(self.status_id, text)

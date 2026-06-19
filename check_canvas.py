"""
自绘画布编辑器无头冒烟：渲染若干帧、坐标变换往返、缩放/平移/命中测试/拖动/删除。
运行：python check_canvas.py
"""
from __future__ import annotations

import dearpygui.dearpygui as dpg

from flow.editor.canvas import CanvasEditor
from flow.flows.villager import build_villager_graph
import flow.nodes  # noqa: F401


def check(name, cond):
    print(("  ok  " if cond else "  FAIL") + f"  {name}")
    return cond


def main():
    ok = True
    g = build_villager_graph()
    n0 = len(g.nodes)
    app = CanvasEditor(g)

    dpg.create_context()
    app._setup_fonts()
    app.build_ui()
    dpg.create_viewport(title="canvas-smoke", width=900, height=600)
    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()
    for _ in range(3):
        dpg.render_dearpygui_frame()

    ok &= check(f"为 {n0} 个节点估算了尺寸", len(app.sizes) == n0)

    # 坐标变换往返
    sx, sy = app.w2s(123.0, 456.0)
    wx, wy = app.s2w(sx, sy)
    ok &= check("世界<->屏幕 变换往返一致", abs(wx - 123.0) < 1e-6 and abs(wy - 456.0) < 1e-6)

    # 缩放：以某点为中心缩放后，该点世界坐标不变
    app.zoom, app.ox, app.oy = 1.0, 40.0, 40.0
    before = app.s2w(400, 300)
    app._mouse = lambda: (400, 300)            # 模拟鼠标在 (400,300)
    app._mouse_over_canvas = lambda: True
    app._on_wheel(None, 1)                      # 放大一档
    after = app.s2w(400, 300)
    ok &= check("以光标为中心缩放，光标处世界坐标不变",
                abs(before[0] - after[0]) < 1e-6 and abs(before[1] - after[1]) < 1e-6)
    ok &= check("缩放档位已变化", abs(app.zoom - 1.0) > 1e-6)

    # 命中测试：节点中心应命中该节点
    nid = next(iter(g.nodes))
    nx, ny = g.positions[nid]
    nw, nh = app.sizes[nid]
    cx, cy = app.w2s(nx + nw / 2, ny + nh / 2)
    ok &= check("命中测试定位到节点", app._node_at(cx, cy) == nid)

    # 拖动：模拟按下+拖拽，节点世界坐标按 delta/zoom 改变
    app._mouse = lambda: (cx, cy)
    app._on_mouse_down(None, None)
    ok &= check("按下选中了该节点", app.selected == {nid})
    start = g.positions[nid]
    app._on_drag(None, [0, 100, 0])             # 屏幕右移 100px
    moved = g.positions[nid]
    ok &= check("拖动按 delta/zoom 平移世界坐标",
                abs((moved[0] - start[0]) - 100 / app.zoom) < 1e-6)
    app._on_release(None, None)
    ok &= check("释放后退出拖动模式", app._mode is None)

    # 平移：空白处拖动改变偏移
    app._mouse = lambda: (5, 5)                 # HUD 区域附近的空白
    app._node_at = lambda sx, sy: None          # 强制判定为空白
    ox0 = app.ox
    app._on_mouse_down(None, None)
    app._on_drag(None, [0, 50, 30])
    ok &= check("空白拖动平移画布", abs(app.ox - (ox0 + 50)) < 1e-6 or app._mode == "pan")
    app._on_release(None, None)

    # 删除选中节点
    app.selected = {nid}
    app._delete_selected()
    ok &= check("删除选中节点", nid not in g.nodes)

    dpg.destroy_context()
    print("\n" + ("画布冒烟全部通过 OK" if ok else "画布冒烟存在失败 FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

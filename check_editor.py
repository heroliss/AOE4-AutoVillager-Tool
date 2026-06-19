"""
编辑器无头冒烟测试：构建 UI、手动渲染若干帧、模拟连线/增删、存读往返，全程不需人工交互。
需在 3.13 venv 运行：.venv\\Scripts\\python check_editor.py
"""
from __future__ import annotations

import os
import tempfile

import dearpygui.dearpygui as dpg

from flow.editor import EditorApp
from flow.flows.villager import build_villager_graph
import flow.nodes  # noqa: F401


def check(name, cond):
    print(("  ok  " if cond else "  FAIL") + f"  {name}")
    return cond


def main():
    ok = True
    g = build_villager_graph()
    n0, e0, d0 = len(g.nodes), len(g.exec_edges), len(g.data_edges)

    app = EditorApp(g)
    dpg.create_context()
    app.build_ui()
    dpg.create_viewport(title="smoke", width=600, height=400)
    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()
    for _ in range(3):
        dpg.render_dearpygui_frame()

    ok &= check(f"渲染出 {n0} 个节点", len(app.node_to_dpg) == n0)
    ok &= check(f"渲染出 {e0 + d0} 条连线", len(app.link_info) == e0 + d0)

    # 添加节点
    nid = app._add_node("debug.log")
    ok &= check("添加节点进入图", nid in app.graph.nodes)

    # 模拟有效执行连线：tick.out -> 新日志节点.in
    out_attr = app.port_to_attr.get(("tick", "out", "out"))
    in_attr = app.port_to_attr.get((nid, "in", "in"))
    before = len(app.graph.exec_edges)
    app._on_link(None, (out_attr, in_attr))
    ok &= check("有效执行连线已写入图", len(app.graph.exec_edges) == before + 1)

    # 模拟非法连线：执行输出 -> 数据输入，应被拒绝
    data_in_attr = app.port_to_attr.get(("if_win", "cond", "in"))
    e_before, d_before = len(app.graph.exec_edges), len(app.graph.data_edges)
    app._on_link(None, (out_attr, data_in_attr))
    ok &= check("执行/数据混接被拒绝", len(app.graph.exec_edges) == e_before and len(app.graph.data_edges) == d_before)

    # 删除刚加的节点，相关连线一并清理
    app._remove_node(nid)
    ok &= check("删除节点后图中移除", nid not in app.graph.nodes)
    ok &= check("删除节点后无悬空连线", all(nid not in (i["src"], i["dst"]) for i in app.link_info.values()))

    for _ in range(2):
        dpg.render_dearpygui_frame()

    # 存读往返
    tmp = os.path.join(tempfile.gettempdir(), "editor_smoke.flow.json")
    app.save(tmp)
    ok &= check("已写出 JSON", os.path.exists(tmp))
    app.load(tmp)
    ok &= check("读回后节点数一致", len(app.node_to_dpg) == len(app.graph.nodes))
    ok &= check("读回后连线数一致", len(app.link_info) ==
                len(app.graph.exec_edges) + len(app.graph.data_edges))
    ok &= check("坐标已随保存写入", all(p in app.graph.positions for p in app.graph.nodes))

    dpg.destroy_context()
    print("\n" + ("编辑器冒烟全部通过 OK" if ok else "编辑器冒烟存在失败 FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

"""
启动节点编辑器。

    python run_editor.py [flow.json]            # 默认：网页编辑器（LiteGraph + pywebview，缩放/右键/节点内参数）
    python run_editor.py --classic [flow.json]  # 备用：DPG 经典 node_editor（无缩放）
    python run_editor.py --canvas  [flow.json]  # 备用：DPG 自绘画布（缩放，开发中）

可选位置参数：要打开的流程图 JSON 路径；不给则新建空白流程。
诊断：设环境变量 AOE4_EDITOR_DEBUG=1 可在网页编辑器中打开 WebView2 开发者工具。
"""
from __future__ import annotations

import argparse
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flow.core import Graph
import flow.nodes  # noqa: F401  注册全部节点


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="要打开的流程图 JSON")
    ap.add_argument("--classic", action="store_true", help="经典 DPG 节点编辑器（参数在节点内，无缩放）")
    ap.add_argument("--canvas", action="store_true", help="DPG 自绘画布（缩放，开发中）")
    args = ap.parse_args()

    path = args.file if args.file and os.path.exists(args.file) else None
    graph = Graph.load(path) if path else None

    if args.classic:
        from flow.editor import EditorApp
        app = EditorApp(graph)
        if path:
            app._current_path = path
        app.run()
    elif args.canvas:
        from flow.editor.canvas import CanvasEditor
        app = CanvasEditor(graph)
        if path:
            app._current_path = path
        app.run()
    else:
        # 默认：网页节点编辑器（LiteGraph + pywebview，支持缩放/右键菜单/节点内可编辑参数）
        from flow.editor.webhost import launch
        launch(graph, path)


if __name__ == "__main__":
    main()

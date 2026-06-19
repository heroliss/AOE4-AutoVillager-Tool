"""
启动节点编辑器。

    python run_editor.py [flow.json]            # 经典编辑器（node_editor，参数在节点内，无缩放）
    python run_editor.py --canvas [flow.json]   # 自绘画布（支持滚轮缩放/平移，开发中）

可选位置参数：要打开的流程图 JSON 路径；不给则新建空白流程。
（DearPyGui 在 Python 3.14 上验证可用，直接用系统 python 即可。）
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
    ap.add_argument("--canvas", action="store_true", help="使用支持缩放的自绘画布编辑器")
    args = ap.parse_args()

    graph = Graph.load(args.file) if args.file and os.path.exists(args.file) else None

    if args.canvas:
        from flow.editor.canvas import CanvasEditor
        app = CanvasEditor(graph)
    else:
        from flow.editor import EditorApp
        app = EditorApp(graph)
    if args.file and os.path.exists(args.file):
        app._current_path = args.file
    app.run()


if __name__ == "__main__":
    main()

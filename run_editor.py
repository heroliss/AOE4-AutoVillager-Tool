"""
启动节点编辑器。

    python run_editor.py [flows\\villager.flow.json]

可选参数：要打开的流程图 JSON 路径；不给则新建空白流程。
（DearPyGui 在 Python 3.14 上验证可用，直接用系统 python 即可。）
"""
from __future__ import annotations

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flow.core import Graph
from flow.editor import EditorApp
import flow.nodes  # noqa: F401  注册全部节点


def main():
    graph = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        graph = Graph.load(sys.argv[1])
    app = EditorApp(graph)
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        app._current_path = sys.argv[1]
    app.run()


if __name__ == "__main__":
    main()

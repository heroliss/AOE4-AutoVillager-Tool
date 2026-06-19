"""
无界面运行器：直接加载一个流程图并循环执行（编辑器就绪前用它在真实游戏中验证）。

用法::

    python run_headless.py                         # 跑 flows/villager.flow.json
    python run_headless.py --file flows/jin.flow.json
    python run_headless.py --dry-run               # 只打日志、不真正发按键（安全演练）
    python run_headless.py --interval 0.1 --max-ticks 100

说明：
- 真实运行需在游戏中，且"输入屏蔽"节点需要管理员权限（否则该步静默跳过）。
- 模板/区域等参数来自流程图本身；模板路径相对 templates/ 目录，故本脚本会切到仓库根目录。
- 按 Ctrl+C 退出。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# 切到脚本所在目录（仓库根），保证 "templates/xxx.png" 等相对路径可解析
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flow.core import Graph, Executor, ExecutionContext
import flow.nodes  # noqa: F401  注册全部节点

_BUILDERS = {
    "villager": "flow.flows.villager:build_villager_graph",
    "trade_cart": "flow.flows.trade_cart:build_trade_cart_graph",
    "jin": "flow.flows.jin:build_jin_graph",
}


def _load_graph(file: str | None, name: str | None) -> Graph:
    if name:
        mod, fn = _BUILDERS[name].split(":")
        import importlib
        return getattr(importlib.import_module(mod), fn)()
    if file and os.path.exists(file):
        return Graph.load(file)
    # 回退：内置出农
    from flow.flows.villager import build_villager_graph
    return build_villager_graph()


def _warmup_ocr() -> None:
    try:
        import numpy as np
        from ocr_util import get_ocr_reader
        t = time.time()
        get_ocr_reader().readtext(np.zeros((32, 96, 3), dtype="uint8"), detail=0)
        print(f"  OCR 预热完成，耗时 {time.time() - t:.2f}s")
    except Exception as e:
        print(f"  OCR 预热跳过：{e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="flows/villager.flow.json", help="流程图 JSON 路径")
    ap.add_argument("--builtin", choices=list(_BUILDERS), help="改用内置构建器（忽略 --file）")
    ap.add_argument("--dry-run", action="store_true", help="只打日志，不真正发送按键/屏蔽输入")
    ap.add_argument("--interval", type=float, default=0.1, help="循环间隔(秒)")
    ap.add_argument("--max-ticks", type=int, default=None, help="最多运行多少帧后退出")
    ap.add_argument("--warmup", action="store_true", help="启动时预热 OCR 模型")
    args = ap.parse_args()

    graph = _load_graph(args.file, args.builtin)

    print("=" * 56)
    print(f"  流程: {graph.name}  |  节点 {len(graph.nodes)}")
    print(f"  模式: {'演练(dry-run，不发按键)' if args.dry_run else '实际运行'}  |  间隔 {args.interval}s")
    if not args.dry_run:
        print("  提示: 实际运行需在游戏中；输入屏蔽需管理员权限")
    print("  按 Ctrl+C 退出")
    print("=" * 56)

    if args.warmup and not args.dry_run:
        _warmup_ocr()

    def on_log(level, msg, node_id):
        print(f"[{level}] {msg}")

    ctx = ExecutionContext(on_log=on_log, dry_run=args.dry_run)
    executor = Executor(graph)
    try:
        executor.run(ctx, interval=args.interval, max_ticks=args.max_ticks)
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()

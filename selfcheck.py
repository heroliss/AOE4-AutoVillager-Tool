"""
引擎 + 节点库 + 出农流程的自检（无需游戏/截图/OCR/按键）。

做法：构建真实的"普通出农"图，把 6 个感知节点（窗口/遮挡/村民/人口/食物/TC）
换成返回固定值的桩节点，在 dry_run 模式下跑一帧——这样能端到端验证
执行流分支、数据流取值、产能计算与操作时序，而操作节点只记日志、不真正发按键。

运行：python selfcheck.py
"""
from __future__ import annotations

import os

from flow.core import Graph, Executor, ExecutionContext, create_node, registry, DataNode
import flow.nodes  # noqa: F401  注册全部节点
from flow.flows.villager import build_villager_graph


class Stub(DataNode):
    """返回固定输出的桩数据节点，用于替换真实感知节点。"""

    def __init__(self, outs: dict):
        super().__init__()
        self._outs = outs

    def evaluate(self, ctx, inputs):
        return dict(self._outs)


def run_with_stubs(stub_map: dict, dry_run=True) -> tuple[list[str], ExecutionContext]:
    g = build_villager_graph()
    for nid, outs in stub_map.items():
        g.nodes[nid] = Stub(outs)
    logs: list[str] = []
    ctx = ExecutionContext(on_log=lambda lv, msg, nid: logs.append(msg), dry_run=dry_run)
    Executor(g).run_tick(ctx)
    return logs, ctx


HAPPY = {
    "win": {"in_game": True, "active": True},
    "occ": {"blocked": False, "in_transition": False, "clear": True, "confidence": 0.02, "state": "未遮挡"},
    "vill": {"found": False, "confidence": 0.05, "which": -1},
    "pop": {"value": 50, "value2": 200, "ok": True},     # 50/200 -> 空位 150
    "food": {"value": 300, "value2": None, "ok": True},
    "tc": {"count": 1, "ok": True},
}


def check(name: str, cond: bool) -> bool:
    print(("  ok  " if cond else "  FAIL") + f"  {name}")
    return cond


def main() -> None:
    ok = True
    print("== 节点注册 ==")
    ok &= check(f"已注册 {len(registry())} 个节点 (>=30)", len(registry()) >= 30)

    print("== 出农流程：正常路径 (无村民/资源充足/1个TC) ==")
    logs, ctx = run_with_stubs(HAPPY)
    queue_logs = [m for m in logs if "按键 q" in m]
    ok &= check("触发排队出农", len(queue_logs) == 1)
    ok &= check("出兵数量 = min(3*1, 150, 300//50) = 3", queue_logs and "x3" in queue_logs[0])
    ok &= check("选中所有TC(H)", any("按键 h" in m for m in logs))
    ok &= check("存编组 Ctrl+0", any("ctrl+0" in m for m in logs))
    ok &= check("取消编组 Ctrl+Alt+0", any("ctrl+alt+0" in m for m in logs))
    ok &= check("帧末已释放操作锁", ctx._lock_held is False)
    ok &= check("帧末已解除输入屏蔽", ctx._block_active is False)

    print("== 跳过路径：检测到村民正在生产 ==")
    logs, _ = run_with_stubs({**HAPPY, "vill": {"found": True, "confidence": 0.9, "which": 0}})
    ok &= check("不触发排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：不在游戏中 ==")
    logs, _ = run_with_stubs({**HAPPY, "win": {"in_game": False, "active": False}})
    ok &= check("不触发任何操作", not any("[干跑]" in m for m in logs))

    print("== 跳过路径：UI 遮挡 ==")
    logs, _ = run_with_stubs({**HAPPY, "occ": {"blocked": True, "in_transition": False, "clear": False, "confidence": 0.9, "state": "完全遮挡"}})
    ok &= check("遮挡时不排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：食物不足 ==")
    logs, _ = run_with_stubs({**HAPPY, "food": {"value": 30, "value2": None, "ok": True}})
    ok &= check("食物不足不排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：TC 检测失败（验证锁/屏蔽兜底释放）==")
    logs, ctx = run_with_stubs({**HAPPY, "tc": {"count": 0, "ok": False}})
    ok &= check("TC失败不排队", not any("按键 q" in m for m in logs))
    ok &= check("TC失败后仍释放了操作锁", ctx._lock_held is False)
    ok &= check("TC失败后仍解除了输入屏蔽", ctx._block_active is False)

    print("== JSON 序列化往返 ==")
    g = build_villager_graph()
    data = g.to_dict()
    g2 = Graph.from_dict(data)
    ok &= check("往返结构一致", g2.to_dict() == data)
    ok &= check(f"节点数 {len(g.nodes)} / 执行连线 {len(g.exec_edges)} / 数据连线 {len(g.data_edges)}", True)

    # 落盘默认模板，供后续编辑器/headless 使用
    os.makedirs("flows", exist_ok=True)
    g.save("flows/villager.flow.json")
    print("已写出 flows/villager.flow.json")

    print("\n" + ("全部通过 OK" if ok else "存在失败 FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

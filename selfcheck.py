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
from flow.flows.trade_cart import build_trade_cart_graph
from flow.flows.jin import build_jin_graph
from flow.flows.combined import build_combined_graph


class Stub(DataNode):
    """返回固定输出的桩数据节点，用于替换真实感知节点。"""

    def __init__(self, outs: dict):
        super().__init__()
        self._outs = outs

    def evaluate(self, ctx, inputs):
        return dict(self._outs)


def run_with_stubs(build, stub_map: dict, dry_run=True) -> tuple[list[str], ExecutionContext]:
    g = build()
    for nid, outs in stub_map.items():
        g.nodes[nid] = Stub(outs)
    logs: list[str] = []
    ctx = ExecutionContext(on_log=lambda lv, msg, nid: logs.append(msg), dry_run=dry_run)
    Executor(g).run_tick(ctx)
    return logs, ctx


HAPPY = {
    "win": {"active": True, "pixel_ok": True},
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
    logs, ctx = run_with_stubs(build_villager_graph, HAPPY)
    queue_logs = [m for m in logs if "按键 q" in m]
    ok &= check("触发排队出农", len(queue_logs) == 1)
    ok &= check("出兵数量 = min(3*1, 150, 300//50) = 3", queue_logs and "x3" in queue_logs[0])
    ok &= check("选中所有TC(H)", any("按键 h" in m for m in logs))
    ok &= check("存编组 Ctrl+0", any("ctrl+0" in m for m in logs))
    ok &= check("取消编组 Ctrl+Alt+0", any("ctrl+alt+0" in m for m in logs))
    ok &= check("帧末已释放操作锁", ctx._lock_held is False)
    ok &= check("帧末已解除输入屏蔽", ctx._block_active is False)

    print("== 跳过路径：检测到村民正在生产 ==")
    logs, _ = run_with_stubs(build_villager_graph, {**HAPPY, "vill": {"found": True, "confidence": 0.9, "which": 0}})
    ok &= check("不触发排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：不在游戏中 ==")
    logs, _ = run_with_stubs(build_villager_graph, {**HAPPY, "win": {"in_game": False, "active": False}})
    ok &= check("不触发任何操作", not any("[干跑]" in m for m in logs))

    print("== 跳过路径：UI 遮挡 ==")
    logs, _ = run_with_stubs(build_villager_graph, {**HAPPY, "occ": {"blocked": True, "in_transition": False, "clear": False, "confidence": 0.9, "state": "完全遮挡"}})
    ok &= check("遮挡时不排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：队列图标半透明UI动画（transition_guard）==")
    logs, _ = run_with_stubs(build_villager_graph,
                             {**HAPPY, "vill": {"found": False, "in_transition": True, "which": -1}})
    ok &= check("UI渐变中不排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：食物不足 ==")
    logs, _ = run_with_stubs(build_villager_graph, {**HAPPY, "food": {"value": 30, "value2": None, "ok": True}})
    ok &= check("食物不足不排队", not any("按键 q" in m for m in logs))

    print("== 跳过路径：TC 检测失败（验证锁/屏蔽兜底释放）==")
    logs, ctx = run_with_stubs(build_villager_graph, {**HAPPY, "tc": {"count": 0, "ok": False}})
    ok &= check("TC失败不排队", not any("按键 q" in m for m in logs))
    ok &= check("TC失败后仍释放了操作锁", ctx._lock_held is False)
    ok &= check("TC失败后仍解除了输入屏蔽", ctx._block_active is False)

    # ==================== 市场出商人 ====================
    print("== 市场出商人：正常路径（黄金充足，单一市场）==")
    tc_stub = {
        "win": {"active": True, "pixel_ok": True},
        "occ": {"blocked": False, "in_transition": False, "clear": True, "confidence": 0.02},
        "vill": {"found": False, "confidence": 0.05, "which": -1},
        "pop": {"value": 50, "value2": 200, "ok": True},
        "food": {"value": 600, "value2": None, "ok": True},  # 资源=黄金
    }
    logs, _ = run_with_stubs(build_trade_cart_graph, tc_stub)
    q = [m for m in logs if "按键 q" in m]
    ok &= check("触发出商人", len(q) == 1)
    ok &= check("数量 = min(5, 150, 600//100=6) = 5", q and "x5" in q[0])
    ok &= check("选中市场(G键)", any("按键 g" in m for m in logs))

    # ==================== 金朝双出农 ====================
    JIN = {
        "win": {"active": True, "pixel_ok": True},
        "occ": {"blocked": False, "in_transition": False, "clear": True, "confidence": 0.02},
        "vill": {"found": False, "confidence": 0.05, "which": -1},  # 村民+乡骑均不在队列
        "pop": {"value": 50, "value2": 200, "ok": True},
        "food": {"value": 300, "value2": None, "ok": True},
        "gold": {"value": 300, "value2": None, "ok": True},
        "tc": {"count": 1, "ok": True},
    }
    print("== 金朝：黄金充足 -> 优先出乡骑 ==")
    logs, _ = run_with_stubs(build_jin_graph, JIN)
    ok &= check("出乡骑(W键) x2 = min(2,150,300//80=3)", any("按键 w x2" in m for m in logs))
    ok &= check("不出村民(Q键)", not any("按键 q" in m for m in logs))

    print("== 金朝：黄金不足 -> 回退出村民 ==")
    logs, _ = run_with_stubs(build_jin_graph, {**JIN, "gold": {"value": 50, "value2": None, "ok": True}})
    ok &= check("不出乡骑(W键)", not any("按键 w" in m for m in logs))
    ok &= check("回退出村民(Q键) x3 = min(3,150,300//50=6)", any("按键 q x3" in m for m in logs))

    # ==================== 统一生产（含各段开关）====================
    COMBINED = {
        "win": {"active": True, "pixel_ok": True},
        "occ": {"blocked": False, "in_transition": False, "clear": True, "confidence": 0.02},
        "q_vill": {"found": False, "in_transition": False, "which": -1},
        "q_xq": {"found": False, "in_transition": False, "which": -1},
        "q_cart": {"found": False, "in_transition": False, "which": -1},
        "pop": {"value": 50, "value2": 200, "ok": True},     # 空位 150
        "food": {"value": 300, "value2": None, "ok": True},
        "gold": {"value": 800, "value2": None, "ok": True},
        "wood": {"value": 800, "value2": None, "ok": True},  # 商人段会读木头(默认成本0=不限制)
        "tc": {"count": 1, "ok": True},
        "market": {"count": 1, "ok": True},                  # 商人段数市场：默认有1个市场
    }
    print("== 统一生产：默认(村民开/乡骑关/商人关) -> 只出村民 ==")
    logs, _ = run_with_stubs(build_combined_graph, COMBINED)
    ok &= check("出村民 q x3 = min(3*1,150,300//50=6)", any("按键 q x3" in m for m in logs))
    ok &= check("不出乡骑(无W键)", not any("按键 w" in m for m in logs))
    ok &= check("不选市场(无J键，商人段关闭)", not any("按键 j" in m for m in logs))

    print("== 统一生产：三段全开 -> 村民+乡骑+商人都出 ==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "sw_xq": {"value": True}, "sw_cart": {"value": True},
                              "food": {"value": 500, "value2": None, "ok": True}})  # 三段都吃食物，给足
    # 注：三段共享人口池(空位)，村民+乡骑+商人共用食物池，乡骑+商人共用黄金池，逐段扣减后结转。
    #   各段按计划数排：村民3、乡骑2、商人2（c_per_cart 默认=2）。
    #   空位链 150→村民用3剩147→乡骑用2剩145→商人；
    #   食物链 500→村民用150剩350→乡骑(65×2)剩220→商人(60×2)剩100（220//60=3≥计划2）；
    #   黄金链 800→乡骑(50×2)剩700→商人(60×2)（700//60=11≥计划2）。
    ok &= check("出乡骑 e x2 = min(2*1, 剩余空位147, 剩余食物350//65=5, 黄金800//50=16)", any("按键 e x2" in m for m in logs))
    ok &= check("选市场 J 键(商人段开启)", any("按键 j" in m for m in logs))
    ok &= check("出商人 q x2 = min(2*1, 剩余空位145, 剩余食物220//60=3, 剩余黄金700//60=11)", any("按键 q x2" in m for m in logs))
    ok &= check("出村民 q x3", any("按键 q x3" in m for m in logs))

    print("== 统一生产：乡骑受食物限制（乡骑吃食物65+黄金50；食物只够1个）==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "sw_vill": {"value": False}, "sw_xq": {"value": True},
                              "food": {"value": 70, "value2": None, "ok": True}})  # 70//65=1
    ok &= check("乡骑食物只够1个 e x1 = min(计划2, 食物70//65=1, 黄金800//50=16)", any("按键 e x1" in m for m in logs))
    ok &= check("食物只够1个则不出2个乡骑", not any("按键 e x2" in m for m in logs))

    print("== 统一生产：关闭村民段 -> 该段被跳过 ==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "sw_vill": {"value": False}})
    ok &= check("村民段关闭则不出村民", not any("按键 q x3" in m for m in logs))

    print("== 统一生产：队列已有村民 -> 跳过村民段(不影响其它) ==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "q_vill": {"found": True, "in_transition": False, "which": 0},
                              "sw_cart": {"value": True}})
    ok &= check("队列已有村民则不再出村民", not any("按键 q x3" in m for m in logs))
    ok &= check("但商人段仍正常(选市场J)", any("按键 j" in m for m in logs))

    print("== 统一生产：商人受食物限制（商人吃60肉60金；食物只够1个）==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "sw_vill": {"value": False}, "sw_xq": {"value": False},
                              "sw_cart": {"value": True},
                              "food": {"value": 80, "value2": None, "ok": True}})  # 80//60=1
    ok &= check("商人食物只够1个 q x1 = min(计划2, 食物80//60=1, 黄金800//60=13)", any("按键 q x1" in m for m in logs))
    ok &= check("食物只够1个则不出2个商人", not any("按键 q x2" in m for m in logs))

    print("== 统一生产：只开商人但没有市场 -> 自动把出商人开关设为关并提示 ==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "sw_vill": {"value": False}, "sw_xq": {"value": False},
                              "sw_cart": {"value": True}, "market": {"count": 0, "ok": False}})
    ok &= check("没市场仍按J确认了一次", any("按键 j" in m for m in logs))
    ok &= check("没市场则不排商人(产量0、不按键)", not any("按键 q" in m for m in logs))
    ok &= check("没市场自动把出商人设为关并提示", any("设为「关」" in m for m in logs))

    print("== 统一生产：食物不足(买不起1个) -> 跳过整个操作区(不抢锁/不按H/不排队) ==")
    logs, ctx = run_with_stubs(build_combined_graph,
                               {**COMBINED, "food": {"value": 30, "value2": None, "ok": True}})  # 30 < 村民单价50
    ok &= check("食物不足不出村民", not any("按键 q" in m for m in logs))
    ok &= check("食物不足时根本不进操作区(不按H选TC)", not any("按键 h" in m for m in logs))
    ok &= check("食物不足时不存编组(无 Ctrl+0)", not any("ctrl+0" in m for m in logs))
    ok &= check("食物不足后未持有操作锁/屏蔽", ctx._lock_held is False and ctx._block_active is False)

    print("== 统一生产：人口已满 -> 同样跳过整个操作区 ==")
    logs, _ = run_with_stubs(build_combined_graph,
                             {**COMBINED, "pop": {"value": 200, "value2": 200, "ok": True}})  # 空位=0
    ok &= check("人口满时不进操作区(无 Ctrl+0)", not any("ctrl+0" in m for m in logs))
    ok &= check("人口满时不出村民", not any("按键 q" in m for m in logs))

    print("== 自动排版：主线横向铺开、支线左上汇入、无重叠 ==")
    from flow.layout import mainline_layout, no_overlaps, estimate_size
    for name, build in (("出农", build_villager_graph), ("出商人", build_trade_cart_graph),
                        ("金朝", build_jin_graph), ("统一", build_combined_graph)):
        gg = build()
        mainline_layout(gg)
        bad = no_overlaps(gg)
        xs = [gg.positions[n][0] for n in gg.nodes]
        ys = [gg.positions[n][1] for n in gg.nodes]
        ws = [estimate_size(gg.nodes[n]) for n in gg.nodes]
        width = max(x + w for x, (w, h) in zip(xs, ws)) - min(xs)
        height = max(y + h for y, (w, h) in zip(ys, ws)) - min(ys)
        ok &= check(f"{name}: 无节点重叠", not bad)
        ok &= check(f"{name}: 主线横向铺开 版面 {int(width)}x{int(height)}（宽>高）", width > height)

    print("== JSON 序列化往返 ==")
    g = build_villager_graph()
    data = g.to_dict()
    g2 = Graph.from_dict(data)
    ok &= check("往返结构一致", g2.to_dict() == data)
    ok &= check(f"节点数 {len(g.nodes)} / 执行连线 {len(g.exec_edges)} / 数据连线 {len(g.data_edges)}", True)

    # 只落盘一个内置流程：统一生产（村民/乡骑/商人三段，用内部开关启停各段）。
    # 其余 villager/trade_cart/jin 仅作为上面的引擎逻辑单元测试用例，不再作为内置流程分发。
    from flow.layout import mainline_layout
    os.makedirs("flows", exist_ok=True)
    gg = build_combined_graph()
    mainline_layout(gg)
    gg.save("flows/combined.flow.json")
    print("已写出 flows/combined.flow.json")

    print("\n" + ("全部通过 OK" if ok else "存在失败 FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

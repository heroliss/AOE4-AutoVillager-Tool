"""
默认流程：金朝双出农（村民 + 乡骑，共用一次选中全部TC）。

验证设计对复杂逻辑的表达力：
- 队列占用检测用"多模板任一命中"（村民 或 乡骑 都算占用）。
- 只选一次全部TC，随后按优先级决定出哪种：
    优先乡骑：乡骑开关 且 黄金≥阈值 且 乡骑产能>0  -> 出乡骑
    否则回退村民：村民开关 且 村民产能>0          -> 出村民
- 每种单位各有：开关 / 每批数量 / 资源成本 / 上限（上限默认 -1 未启用，
  因按单位类型计数需后续接入；结构已就位）。

区域/模板/按键多为占位默认值，需用编辑器按实际填入（尤其 templates/xiangqi.png）。
"""
from __future__ import annotations

from ..core import Graph, create_node

WIN_PIXEL = [2526, 1405]
WIN_COLOR = [26, 32, 46]
BLOCKED_REGION = [265, 950, 280, 970]
QUEUE_REGION = [10, 970, 500, 1025]
POP_REGION = [50, 1140, 150, 1170]
FOOD_REGION = [50, 1222, 140, 1248]
GOLD_REGION = [50, 1180, 140, 1206]
TC = {
    "icon_region": [444, 1212, 492, 1259],
    "single_region": [300, 1140, 354, 1194],
    "single_template": "templates/tc_single.png",
    "numbered_templates": [f"templates/tc_number_{i}.png" for i in range(1, 7)],
}

# 乡骑（优先）
XQ_KEY = "w"
XQ_PER_TC = 2
XQ_COST = 80          # 每个乡骑黄金成本（占位）
XQ_GOLD_THRESHOLD = 100
XQ_CAP = -1
# 村民（回退）
V_KEY = "q"
V_PER_TC = 3
V_COST = 50
V_CAP = -1


def build_jin_graph() -> Graph:
    g = Graph(name="金朝双出农")

    def add(nid, type_id, params=None):
        node = create_node(type_id)
        if params:
            node.values.update(params)
        g.add(nid, node)
        return node

    # —— 入口与快速门 ——
    add("tick", "event.on_tick", {"interval": 0.1})
    add("mod", "sense.modifier_down")          # 检测是否按住修饰键（按住＝人在手动操作）
    add("if_mod", "control.if")                # 按住则暂停（真分支不接＝结束本帧），否则继续
    add("win", "sense.window_check", {"pixel": WIN_PIXEL, "color": WIN_COLOR})
    add("if_win", "control.if")

    # —— 队列占用：村民 或 乡骑（多模板任一命中）——
    add("occ", "game.occlusion", {"region": BLOCKED_REGION, "template": "templates/blocked.png"})
    add("vill", "sense.template_match",
        {"region": QUEUE_REGION, "threshold": 0.6, "transition_guard": True,
         "templates": ["templates/cunmin.png", "templates/xiangqi.png"]})
    add("or_trans", "logic.or")
    add("if_blocked", "control.if")
    add("if_trans", "control.if")
    add("if_vill", "control.if")

    # —— 资源识别：人口 + 食物 + 黄金 ——
    add("prefetch", "control.prefetch_full")
    add("pop", "sense.ocr_number", {"region": POP_REGION, "regex": r"(\d+)[/\\|](\d+)"})
    add("food", "sense.ocr_number", {"region": FOOD_REGION, "regex": r"(\d+)"})
    add("gold", "sense.ocr_number", {"region": GOLD_REGION, "regex": r"(\d+)"})
    add("if_popok", "control.if")

    add("slots", "math.arith", {"op": "-"})
    add("c_zero", "data.const_number", {"value": 0})
    add("cmp_slots", "logic.compare", {"op": ">"})
    add("if_slots", "control.if")

    # —— 操作区：加锁 + 屏蔽 + 存编组 + 选中所有TC（仅一次）+ 计数 ——
    add("lock", "control.lock_acquire")
    add("block_begin", "control.input_block_begin", {"max_duration": 3.0})
    add("save_sel", "action.press_key", {"key": "0", "modifiers": "ctrl"})
    add("relmod1", "action.release_modifiers")
    add("select_tc", "action.press_key", {"key": "h"})
    add("tc", "game.tc_count", TC)
    add("if_tcok", "control.if")

    # —— 两种单位的产能 ——
    add("c_xq_per", "data.const_number", {"value": XQ_PER_TC})
    add("planned_xq", "math.arith", {"op": "*"})
    add("produce_xq", "game.produce_count", {"cost_per_unit": XQ_COST, "cap": XQ_CAP})
    add("c_v_per", "data.const_number", {"value": V_PER_TC})
    add("planned_v", "math.arith", {"op": "*"})
    add("produce_v", "game.produce_count", {"cost_per_unit": V_COST, "cap": V_CAP})

    # —— 开关与阈值 ——
    add("sw_xq", "data.switch", {"on": True})
    add("sw_v", "data.switch", {"on": True})
    add("c_gold_thr", "data.const_number", {"value": XQ_GOLD_THRESHOLD})
    add("cmp_gold", "logic.compare", {"op": ">="})
    add("cmp_xq_cnt", "logic.compare", {"op": ">"})
    add("and_xq1", "logic.and")
    add("and_xq2", "logic.and")
    add("if_xq", "control.if")

    add("cmp_v_cnt", "logic.compare", {"op": ">"})
    add("and_v", "logic.and")
    add("if_v", "control.if")

    # —— 出兵（两分支汇合到统一收尾）——
    add("queue_xq", "action.press_key",
        {"key": XQ_KEY, "shift_batch": True, "batch_size": 5, "post_escape": True})
    add("queue_v", "action.press_key",
        {"key": V_KEY, "shift_batch": True, "batch_size": 5, "post_escape": True})
    add("restore", "action.press_key", {"key": "0"})
    add("disband", "action.press_key", {"key": "0", "modifiers": "ctrl,alt"})
    add("relmod2", "action.release_modifiers")
    add("block_end", "control.input_block_end")
    add("delay", "control.delay", {"seconds": 3.0})
    add("unlock", "control.lock_release")

    # ==================== 执行流 ====================
    # 注：条件分支不接任何节点即表示该情况下"结束本帧"（执行器走到无连出的出口自然停止）。
    g.connect_exec("tick", "out", "if_mod", "in")
    g.connect_exec("if_mod", "false", "if_win", "in")   # 没按修饰键 -> 继续；按住(真)不接 = 暂停
    g.connect_exec("if_win", "true", "if_blocked", "in")
    g.connect_exec("if_blocked", "false", "if_trans", "in")
    g.connect_exec("if_trans", "false", "if_vill", "in")
    g.connect_exec("if_vill", "false", "prefetch", "in")
    g.connect_exec("prefetch", "out", "if_popok", "in")
    g.connect_exec("if_popok", "true", "if_slots", "in")
    g.connect_exec("if_slots", "true", "lock", "in")
    g.connect_exec("lock", "ok", "block_begin", "in")
    g.connect_exec("block_begin", "out", "save_sel", "in")
    g.connect_exec("save_sel", "out", "relmod1", "in")
    g.connect_exec("relmod1", "out", "select_tc", "in")
    g.connect_exec("select_tc", "out", "if_tcok", "in")
    g.connect_exec("if_tcok", "true", "if_xq", "in")
    # 优先乡骑
    g.connect_exec("if_xq", "true", "queue_xq", "in")
    g.connect_exec("if_xq", "false", "if_v", "in")
    # 回退村民
    g.connect_exec("if_v", "true", "queue_v", "in")
    # 汇合收尾
    g.connect_exec("queue_xq", "out", "restore", "in")
    g.connect_exec("queue_v", "out", "restore", "in")
    g.connect_exec("restore", "out", "disband", "in")
    g.connect_exec("disband", "out", "relmod2", "in")
    g.connect_exec("relmod2", "out", "block_end", "in")
    g.connect_exec("block_end", "out", "delay", "in")
    g.connect_exec("delay", "out", "unlock", "in")

    # ==================== 数据流 ====================
    g.connect_data("mod", "down", "if_mod", "cond")
    g.connect_data("win", "in_game", "if_win", "cond")
    g.connect_data("occ", "blocked", "if_blocked", "cond")
    g.connect_data("occ", "in_transition", "or_trans", "a")
    g.connect_data("vill", "in_transition", "or_trans", "b")
    g.connect_data("or_trans", "result", "if_trans", "cond")
    g.connect_data("vill", "found", "if_vill", "cond")
    g.connect_data("pop", "ok", "if_popok", "cond")

    g.connect_data("pop", "value2", "slots", "a")
    g.connect_data("pop", "value", "slots", "b")
    g.connect_data("slots", "value", "cmp_slots", "a")
    g.connect_data("c_zero", "value", "cmp_slots", "b")
    g.connect_data("cmp_slots", "result", "if_slots", "cond")

    g.connect_data("tc", "ok", "if_tcok", "cond")

    # 乡骑产能 = min(每TC*TC数, 空位, 黄金//成本)
    g.connect_data("c_xq_per", "value", "planned_xq", "a")
    g.connect_data("tc", "count", "planned_xq", "b")
    g.connect_data("planned_xq", "value", "produce_xq", "planned")
    g.connect_data("slots", "value", "produce_xq", "available_slots")
    g.connect_data("gold", "value", "produce_xq", "resource")

    # 村民产能 = min(每TC*TC数, 空位, 食物//成本)
    g.connect_data("c_v_per", "value", "planned_v", "a")
    g.connect_data("tc", "count", "planned_v", "b")
    g.connect_data("planned_v", "value", "produce_v", "planned")
    g.connect_data("slots", "value", "produce_v", "available_slots")
    g.connect_data("food", "value", "produce_v", "resource")

    # 乡骑条件 = 开关 且 黄金≥阈值 且 乡骑产能>0
    g.connect_data("gold", "value", "cmp_gold", "a")
    g.connect_data("c_gold_thr", "value", "cmp_gold", "b")
    g.connect_data("produce_xq", "count", "cmp_xq_cnt", "a")
    g.connect_data("c_zero", "value", "cmp_xq_cnt", "b")
    g.connect_data("sw_xq", "value", "and_xq1", "a")
    g.connect_data("cmp_gold", "result", "and_xq1", "b")
    g.connect_data("and_xq1", "result", "and_xq2", "a")
    g.connect_data("cmp_xq_cnt", "result", "and_xq2", "b")
    g.connect_data("and_xq2", "result", "if_xq", "cond")
    g.connect_data("produce_xq", "count", "queue_xq", "count")

    # 村民条件 = 开关 且 村民产能>0
    g.connect_data("produce_v", "count", "cmp_v_cnt", "a")
    g.connect_data("c_zero", "value", "cmp_v_cnt", "b")
    g.connect_data("sw_v", "value", "and_v", "a")
    g.connect_data("cmp_v_cnt", "result", "and_v", "b")
    g.connect_data("and_v", "result", "if_v", "cond")
    g.connect_data("produce_v", "count", "queue_v", "count")

    return g

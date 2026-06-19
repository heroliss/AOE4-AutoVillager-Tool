"""
统一生产流程：在同一张图里、同一帧内依次生产【村民 / 乡骑 / 商队】，每段都有独立开关。

设计要点（回答"能否同图同时跑、阶段复用"）：
- 共享前段只跑一次：修饰键/窗口/遮挡/渐变判断 -> 整屏预取 -> 取锁 -> 屏蔽 -> 存编组。
- 之后三段生产串成接力，每段：[开关?] -> [队列已有该单位?] -> 选建筑 -> 产能计算 -> 排队；
  段内任一"跳过"都接到【下一段】（而非结束本帧），所以关掉/跳过某段不影响其它段。
- 取锁/屏蔽/存编组/收尾对整批生产只做一次（一次输入屏蔽窗口内做完所有生产）。
- 传感器（截图/OCR/模板）逐帧记忆化：多段都读"人口/空位"等也只算一次。

开关默认：村民=开、乡骑=关、商队=关（默认行为≈普通出农；用户在节点上翻开关即可启停各段）。
区域/模板/按键/成本均为占位默认值，需用编辑器按实际填写（尤其乡骑/商队模板）。
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


def build_combined_graph() -> Graph:
    g = Graph(name="统一生产(村民/乡骑/商队)")

    def add(nid, type_id, params=None):
        node = create_node(type_id)
        if params:
            node.values.update(params)
        g.add(nid, node)
        return node

    # ==================== 共享前段（每帧一次）====================
    add("tick", "event.on_tick", {"interval": 0.1})
    add("mod", "sense.modifier_down")
    add("if_mod", "control.if")
    add("win", "sense.window_check", {"pixel": WIN_PIXEL, "color": WIN_COLOR})
    add("if_win", "control.if")
    add("occ", "game.occlusion", {"region": BLOCKED_REGION, "template": "templates/blocked.png"})
    add("if_blocked", "control.if")
    add("if_trans", "control.if")
    add("prefetch", "control.prefetch_full")

    # 共享传感器（数据，按需且逐帧记忆化）
    add("pop", "sense.ocr_number", {"region": POP_REGION, "regex": r"(\d+)[/\\|](\d+)"})
    add("slots", "math.arith", {"op": "-"})       # 空位 = 人口上限 - 当前人口
    add("food", "sense.ocr_number", {"region": FOOD_REGION, "regex": r"(\d+)"})
    add("gold", "sense.ocr_number", {"region": GOLD_REGION, "regex": r"(\d+)"})
    add("tc", "game.tc_count", TC)
    add("c_one", "data.const_number", {"value": 1})

    # 操作锁 / 输入屏蔽 / 存当前编组（整批生产共用一次）
    add("lock", "control.lock_acquire")
    add("block_begin", "control.input_block_begin", {"max_duration": 3.0})
    add("save_sel", "action.press_key", {"key": "0", "modifiers": "ctrl"})
    add("relmod1", "action.release_modifiers")

    # ==================== 村民段 ====================
    add("sw_vill", "data.switch", {"on": True})            # 开关：是否生产村民
    add("if_sw_vill", "control.if")
    add("q_vill", "sense.template_match",
        {"region": QUEUE_REGION, "templates": ["templates/cunmin.png"], "threshold": 0.6, "transition_guard": True})
    add("if_qvill", "control.if")
    add("sel_tc_v", "action.press_key", {"key": "h"})       # 选中所有TC
    add("c_per_v", "data.const_number", {"value": 3})
    add("plan_v", "math.arith", {"op": "*"})
    add("prod_v", "game.produce_count", {"cost_per_unit": 50, "cap": -1})
    add("queue_v", "action.press_key", {"key": "q", "post_escape": True})

    # ==================== 乡骑段 ====================
    add("sw_xq", "data.switch", {"on": False})              # 开关：是否生产乡骑（金朝）
    add("if_sw_xq", "control.if")
    add("q_xq", "sense.template_match",
        {"region": QUEUE_REGION, "templates": ["templates/xiangqi.png"], "threshold": 0.6, "transition_guard": True})
    add("if_qxq", "control.if")
    add("sel_tc_x", "action.press_key", {"key": "h"})
    add("c_per_x", "data.const_number", {"value": 2})
    add("plan_x", "math.arith", {"op": "*"})
    add("prod_x", "game.produce_count", {"cost_per_unit": 80, "cap": -1})
    add("queue_x", "action.press_key", {"key": "w", "post_escape": True})

    # ==================== 商队段 ====================
    add("sw_cart", "data.switch", {"on": False})            # 开关：是否生产商队（市场）
    add("if_sw_cart", "control.if")
    add("q_cart", "sense.template_match",
        {"region": QUEUE_REGION, "templates": ["templates/trade_cart.png"], "threshold": 0.6, "transition_guard": True})
    add("if_qcart", "control.if")
    add("sel_market", "action.press_key", {"key": "g"})     # 选中市场
    add("c_per_cart", "data.const_number", {"value": 5})
    add("plan_cart", "math.arith", {"op": "*"})
    add("prod_cart", "game.produce_count", {"cost_per_unit": 100, "cap": -1})
    add("queue_cart", "action.press_key", {"key": "q", "post_escape": True})

    # ==================== 收尾（整批一次）====================
    add("restore", "action.press_key", {"key": "0"})
    add("disband", "action.press_key", {"key": "0", "modifiers": "ctrl,alt"})
    add("relmod2", "action.release_modifiers")
    add("block_end", "control.input_block_end")
    add("delay", "control.delay", {"seconds": 3.0})
    add("unlock", "control.lock_release")

    # ==================== 执行流 ====================
    # 共享前段（任一守卫不过 -> 结束本帧；出口不接即结束）
    g.connect_exec("tick", "out", "if_mod", "in")
    g.connect_exec("if_mod", "false", "if_win", "in")       # 没按修饰键才继续
    g.connect_exec("if_win", "true", "if_blocked", "in")    # 在游戏中才继续
    g.connect_exec("if_blocked", "false", "if_trans", "in") # 未遮挡才继续
    g.connect_exec("if_trans", "false", "prefetch", "in")   # 非渐变才继续
    g.connect_exec("prefetch", "out", "lock", "in")
    g.connect_exec("lock", "ok", "block_begin", "in")       # 占用中(busy)则结束本帧
    g.connect_exec("block_begin", "out", "save_sel", "in")
    g.connect_exec("save_sel", "out", "relmod1", "in")
    g.connect_exec("relmod1", "out", "if_sw_vill", "in")

    # 村民段：开关 -> 队列检查 -> 选TC -> 排队；任一跳过都接到乡骑段
    g.connect_exec("if_sw_vill", "true", "if_qvill", "in")
    g.connect_exec("if_sw_vill", "false", "if_sw_xq", "in")
    g.connect_exec("if_qvill", "false", "sel_tc_v", "in")   # 队列没有村民才生产
    g.connect_exec("if_qvill", "true", "if_sw_xq", "in")
    g.connect_exec("sel_tc_v", "out", "queue_v", "in")
    g.connect_exec("queue_v", "out", "if_sw_xq", "in")

    # 乡骑段
    g.connect_exec("if_sw_xq", "true", "if_qxq", "in")
    g.connect_exec("if_sw_xq", "false", "if_sw_cart", "in")
    g.connect_exec("if_qxq", "false", "sel_tc_x", "in")
    g.connect_exec("if_qxq", "true", "if_sw_cart", "in")
    g.connect_exec("sel_tc_x", "out", "queue_x", "in")
    g.connect_exec("queue_x", "out", "if_sw_cart", "in")

    # 商队段
    g.connect_exec("if_sw_cart", "true", "if_qcart", "in")
    g.connect_exec("if_sw_cart", "false", "restore", "in")
    g.connect_exec("if_qcart", "false", "sel_market", "in")
    g.connect_exec("if_qcart", "true", "restore", "in")
    g.connect_exec("sel_market", "out", "queue_cart", "in")
    g.connect_exec("queue_cart", "out", "restore", "in")

    # 收尾
    g.connect_exec("restore", "out", "disband", "in")
    g.connect_exec("disband", "out", "relmod2", "in")
    g.connect_exec("relmod2", "out", "block_end", "in")
    g.connect_exec("block_end", "out", "delay", "in")
    g.connect_exec("delay", "out", "unlock", "in")

    # ==================== 数据流 ====================
    g.connect_data("mod", "down", "if_mod", "cond")
    g.connect_data("win", "in_game", "if_win", "cond")
    g.connect_data("occ", "blocked", "if_blocked", "cond")
    g.connect_data("occ", "in_transition", "if_trans", "cond")

    # 空位 = 人口上限(value2) - 当前人口(value)
    g.connect_data("pop", "value2", "slots", "a")
    g.connect_data("pop", "value", "slots", "b")

    # 村民段数据
    g.connect_data("sw_vill", "value", "if_sw_vill", "cond")
    g.connect_data("q_vill", "found", "if_qvill", "cond")
    g.connect_data("c_per_v", "value", "plan_v", "a")
    g.connect_data("tc", "count", "plan_v", "b")
    g.connect_data("plan_v", "value", "prod_v", "planned")
    g.connect_data("slots", "value", "prod_v", "available_slots")
    g.connect_data("food", "value", "prod_v", "resource")
    g.connect_data("prod_v", "count", "queue_v", "count")

    # 乡骑段数据
    g.connect_data("sw_xq", "value", "if_sw_xq", "cond")
    g.connect_data("q_xq", "found", "if_qxq", "cond")
    g.connect_data("c_per_x", "value", "plan_x", "a")
    g.connect_data("tc", "count", "plan_x", "b")
    g.connect_data("plan_x", "value", "prod_x", "planned")
    g.connect_data("slots", "value", "prod_x", "available_slots")
    g.connect_data("gold", "value", "prod_x", "resource")
    g.connect_data("prod_x", "count", "queue_x", "count")

    # 商队段数据（市场数按 1 计；空位仍受人口限制）
    g.connect_data("sw_cart", "value", "if_sw_cart", "cond")
    g.connect_data("q_cart", "found", "if_qcart", "cond")
    g.connect_data("c_per_cart", "value", "plan_cart", "a")
    g.connect_data("c_one", "value", "plan_cart", "b")
    g.connect_data("plan_cart", "value", "prod_cart", "planned")
    g.connect_data("slots", "value", "prod_cart", "available_slots")
    g.connect_data("gold", "value", "prod_cart", "resource")
    g.connect_data("prod_cart", "count", "queue_cart", "count")

    return g

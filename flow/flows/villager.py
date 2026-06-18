"""
默认流程之一：普通出农（复刻当前 main.py 主循环的逻辑）。

流程骨架（执行流）::

    每帧触发 -> 修饰键暂停门 -> [游戏中?] -> [遮挡?] -> [渐变?] -> [有村民生产?]
      -> 整屏预取 -> [人口识别成功?] -> [食物足?] -> [有空位?]
      -> 获取操作锁 -> 输入屏蔽开始 -> 存编组(Ctrl+0) -> 释放修饰键 -> 选中所有TC(H)
      -> [检测到TC?] -> 排队出农(Shift+Q, 数量=产能) -> 恢复选中(0) -> 取消编组(Ctrl+Alt+0)
      -> 释放修饰键 -> 输入屏蔽结束 -> 延时(UI更新) -> 释放操作锁

数据流：窗口/遮挡/村民模板/人口OCR/食物OCR/TC计数 的结果分别喂给各条件与产能计算。

模板路径用相对 templates/ 目录；真实运行时由调用方保证工作目录或后续做路径解析。
"""
from __future__ import annotations

from ..core import Graph, create_node

# —— 可调默认值（对应旧 config 的核心项）——
QUEUE_REGION = [10, 970, 500, 1025]
BLOCKED_REGION = [265, 950, 280, 970]
POP_REGION = [50, 1140, 150, 1170]
FOOD_REGION = [50, 1222, 140, 1248]
TC_ICON_REGION = [444, 1212, 492, 1259]
SINGLE_TC_REGION = [300, 1140, 354, 1194]
WIN_PIXEL = [2526, 1405]
WIN_COLOR = [26, 32, 46]

VILLAGER_TEMPLATE = "templates/cunmin.png"
BLOCKED_TEMPLATE = "templates/blocked.png"
SINGLE_TC_TEMPLATE = "templates/tc_single.png"
NUMBERED_TEMPLATES = [f"templates/tc_number_{i}.png" for i in range(1, 7)]

PER_TC = 3
MIN_FOOD = 50
FOOD_PER_VILLAGER = 50
POST_OP_DELAY = 3.0
TC_SELECT_KEY = "h"
QUEUE_KEY = "q"


def build_villager_graph() -> Graph:
    g = Graph(name="普通出农")

    def add(nid, type_id, params=None, pos=(0, 0)):
        node = create_node(type_id)
        if params:
            for k, v in params.items():
                node.values[k] = v
        g.add(nid, node, pos)
        return node

    # —— 入口与快速门 ——
    add("tick", "event.on_tick", {"interval": 0.1})
    add("pause", "control.pause_if_modifier")
    add("win", "sense.window_check", {"pixel": WIN_PIXEL, "color": WIN_COLOR})
    add("if_win", "control.if")
    add("end_win", "control.end")

    # —— 生产队列状态（遮挡三态 + 村民图标）——
    add("occ", "game.occlusion", {"region": BLOCKED_REGION, "template": BLOCKED_TEMPLATE})
    add("vill", "sense.template_match",
        {"region": QUEUE_REGION, "templates": [VILLAGER_TEMPLATE], "threshold": 0.6})
    add("if_blocked", "control.if")
    add("end_blocked", "control.end")
    add("if_trans", "control.if")
    add("end_trans", "control.end")
    add("if_vill", "control.if")
    add("end_vill", "control.end")

    # —— 资源识别（慢路径，先整屏预取）——
    add("prefetch", "control.prefetch_full")
    add("pop", "sense.ocr_number",
        {"region": POP_REGION, "regex": r"(\d+)[/\\|](\d+)"})
    add("food", "sense.ocr_number",
        {"region": FOOD_REGION, "regex": r"(\d+)", "allowlist": "0123456789OolIsS "})
    add("if_popok", "control.if")
    add("end_popfail", "control.end")

    # 食物是否充足
    add("c_minfood", "data.const_number", {"value": MIN_FOOD})
    add("cmp_food", "logic.compare", {"op": ">="})
    add("if_foodok", "control.if")
    add("end_foodlow", "control.end")

    # 人口空位 = 上限 - 当前
    add("slots", "math.arith", {"op": "-"})
    add("c_zero", "data.const_number", {"value": 0})
    add("cmp_slots", "logic.compare", {"op": ">"})
    add("if_slots", "control.if")
    add("end_full", "control.end")

    # —— 操作区（加锁 + 输入屏蔽）——
    add("lock", "control.lock_acquire")
    add("end_busy", "control.end")
    add("block_begin", "control.input_block_begin", {"max_duration": 3.0})
    add("save_sel", "action.press_key", {"key": "0", "modifiers": "ctrl"})
    add("relmod1", "action.release_modifiers")
    add("select_tc", "action.press_key", {"key": TC_SELECT_KEY})
    add("tc", "game.tc_count",
        {"icon_region": TC_ICON_REGION, "single_region": SINGLE_TC_REGION,
         "single_template": SINGLE_TC_TEMPLATE, "numbered_templates": NUMBERED_TEMPLATES})
    add("if_tcok", "control.if")
    add("end_tcfail", "control.end")

    # 产能：planned = PER_TC * tc.count；count = min(planned, 空位, 食物//成本)
    add("c_pertc", "data.const_number", {"value": PER_TC})
    add("planned", "math.arith", {"op": "*"})
    add("produce", "game.produce_count", {"cost_per_unit": FOOD_PER_VILLAGER, "cap": -1})

    # 排队出农 + 恢复 + 收尾
    add("queue", "action.press_key",
        {"key": QUEUE_KEY, "shift_batch": True, "batch_size": 5, "post_escape": True})
    add("restore", "action.press_key", {"key": "0"})
    add("disband", "action.press_key", {"key": "0", "modifiers": "ctrl,alt"})
    add("relmod2", "action.release_modifiers")
    add("block_end", "control.input_block_end")
    add("delay", "control.delay", {"seconds": POST_OP_DELAY})
    add("unlock", "control.lock_release")

    # ==================== 执行流连线 ====================
    g.connect_exec("tick", "out", "pause", "in")
    g.connect_exec("pause", "out", "if_win", "in")
    g.connect_exec("if_win", "true", "if_blocked", "in")
    g.connect_exec("if_win", "false", "end_win", "in")
    g.connect_exec("if_blocked", "true", "end_blocked", "in")
    g.connect_exec("if_blocked", "false", "if_trans", "in")
    g.connect_exec("if_trans", "true", "end_trans", "in")
    g.connect_exec("if_trans", "false", "if_vill", "in")
    g.connect_exec("if_vill", "true", "end_vill", "in")
    g.connect_exec("if_vill", "false", "prefetch", "in")
    g.connect_exec("prefetch", "out", "if_popok", "in")
    g.connect_exec("if_popok", "true", "if_foodok", "in")
    g.connect_exec("if_popok", "false", "end_popfail", "in")
    g.connect_exec("if_foodok", "true", "if_slots", "in")
    g.connect_exec("if_foodok", "false", "end_foodlow", "in")
    g.connect_exec("if_slots", "true", "lock", "in")
    g.connect_exec("if_slots", "false", "end_full", "in")
    g.connect_exec("lock", "ok", "block_begin", "in")
    g.connect_exec("lock", "busy", "end_busy", "in")
    g.connect_exec("block_begin", "out", "save_sel", "in")
    g.connect_exec("save_sel", "out", "relmod1", "in")
    g.connect_exec("relmod1", "out", "select_tc", "in")
    g.connect_exec("select_tc", "out", "if_tcok", "in")
    g.connect_exec("if_tcok", "true", "queue", "in")
    g.connect_exec("if_tcok", "false", "end_tcfail", "in")
    g.connect_exec("queue", "out", "restore", "in")
    g.connect_exec("restore", "out", "disband", "in")
    g.connect_exec("disband", "out", "relmod2", "in")
    g.connect_exec("relmod2", "out", "block_end", "in")
    g.connect_exec("block_end", "out", "delay", "in")
    g.connect_exec("delay", "out", "unlock", "in")

    # ==================== 数据流连线 ====================
    g.connect_data("win", "in_game", "if_win", "cond")
    g.connect_data("occ", "blocked", "if_blocked", "cond")
    g.connect_data("occ", "in_transition", "if_trans", "cond")
    g.connect_data("vill", "found", "if_vill", "cond")
    g.connect_data("pop", "ok", "if_popok", "cond")

    g.connect_data("food", "value", "cmp_food", "a")
    g.connect_data("c_minfood", "value", "cmp_food", "b")
    g.connect_data("cmp_food", "result", "if_foodok", "cond")

    g.connect_data("pop", "value2", "slots", "a")   # 上限
    g.connect_data("pop", "value", "slots", "b")    # 当前
    g.connect_data("slots", "value", "cmp_slots", "a")
    g.connect_data("c_zero", "value", "cmp_slots", "b")
    g.connect_data("cmp_slots", "result", "if_slots", "cond")

    g.connect_data("tc", "ok", "if_tcok", "cond")

    g.connect_data("c_pertc", "value", "planned", "a")
    g.connect_data("tc", "count", "planned", "b")
    g.connect_data("planned", "value", "produce", "planned")
    g.connect_data("slots", "value", "produce", "available_slots")
    g.connect_data("food", "value", "produce", "resource")
    g.connect_data("produce", "count", "queue", "count")

    return g

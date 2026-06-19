"""
单一单位生产流程的参数化构建器。

"普通出农"和"市场出商队"结构相同、仅参数不同（检测模板、资源区域、按键、成本等），
因此抽成一个按配置生成图的函数；金朝双出农则在此骨架上扩展生产尾段。

cfg 字段见 default_cfg()。
"""
from __future__ import annotations

from ..core import Graph, create_node


def default_cfg() -> dict:
    return {
        "name": "单位生产",
        "win_pixel": [2526, 1405],
        "win_color": [26, 32, 46],
        "blocked_region": [265, 950, 280, 970],
        "blocked_template": "templates/blocked.png",
        "queue_region": [10, 970, 500, 1025],
        "occupancy_templates": ["templates/cunmin.png"],  # 队列占用检测（可多模板=任一命中）
        "occupancy_threshold": 0.6,
        "pop_region": [50, 1140, 150, 1170],
        "resource_region": [50, 1222, 140, 1248],
        "resource_regex": r"(\d+)",
        "resource_allowlist": "0123456789OolIsS ",
        "min_resource": 50,
        "cost_per_unit": 50,
        "per_building": 3,           # 每个建筑排队数量
        "building": {                # 多建筑计数（如多TC）；None 表示按单建筑、不计数
            "icon_region": [444, 1212, 492, 1259],
            "single_region": [300, 1140, 354, 1194],
            "single_template": "templates/tc_single.png",
            "numbered_templates": [f"templates/tc_number_{i}.png" for i in range(1, 7)],
        },
        "select_key": "h",
        "queue_key": "q",
        "shift_batch": True,
        "post_op_delay": 3.0,
    }


def build_single_type(cfg: dict) -> Graph:
    g = Graph(name=cfg["name"])

    def add(nid, type_id, params=None):
        node = create_node(type_id)
        if params:
            node.values.update(params)
        g.add(nid, node)
        return node

    # —— 入口与快速门 ——
    add("tick", "event.on_tick", {"interval": 0.1})
    add("pause", "control.pause_if_modifier")
    add("win", "sense.window_check", {"pixel": cfg["win_pixel"], "color": cfg["win_color"]})
    add("if_win", "control.if")
    add("end_win", "control.end")

    # —— 队列占用状态 ——
    add("occ", "game.occlusion", {"region": cfg["blocked_region"], "template": cfg["blocked_template"]})
    add("vill", "sense.template_match",
        {"region": cfg["queue_region"], "templates": cfg["occupancy_templates"],
         "threshold": cfg["occupancy_threshold"]})
    add("if_blocked", "control.if")
    add("end_blocked", "control.end")
    add("if_trans", "control.if")
    add("end_trans", "control.end")
    add("if_vill", "control.if")
    add("end_vill", "control.end")

    # —— 资源识别 ——
    add("prefetch", "control.prefetch_full")
    add("pop", "sense.ocr_number", {"region": cfg["pop_region"], "regex": r"(\d+)[/\\|](\d+)"})
    add("food", "sense.ocr_number",
        {"region": cfg["resource_region"], "regex": cfg["resource_regex"],
         "allowlist": cfg["resource_allowlist"]})
    add("if_popok", "control.if")
    add("end_popfail", "control.end")

    add("c_minfood", "data.const_number", {"value": cfg["min_resource"]})
    add("cmp_food", "logic.compare", {"op": ">="})
    add("if_foodok", "control.if")
    add("end_foodlow", "control.end")

    add("slots", "math.arith", {"op": "-"})
    add("c_zero", "data.const_number", {"value": 0})
    add("cmp_slots", "logic.compare", {"op": ">"})
    add("if_slots", "control.if")
    add("end_full", "control.end")

    # —— 操作区 ——
    add("lock", "control.lock_acquire")
    add("end_busy", "control.end")
    add("block_begin", "control.input_block_begin", {"max_duration": 3.0})
    add("save_sel", "action.press_key", {"key": "0", "modifiers": "ctrl"})
    add("relmod1", "action.release_modifiers")
    add("select_b", "action.press_key", {"key": cfg["select_key"]})

    use_count = cfg.get("building") is not None
    if use_count:
        b = cfg["building"]
        add("tc", "game.tc_count",
            {"icon_region": b["icon_region"], "single_region": b["single_region"],
             "single_template": b["single_template"], "numbered_templates": b["numbered_templates"]})
        add("if_tcok", "control.if")
        add("end_tcfail", "control.end")

    add("c_pertc", "data.const_number", {"value": cfg["per_building"]})
    add("planned", "math.arith", {"op": "*"})
    add("produce", "game.produce_count", {"cost_per_unit": cfg["cost_per_unit"], "cap": -1})

    add("queue", "action.press_key",
        {"key": cfg["queue_key"], "shift_batch": cfg["shift_batch"], "batch_size": 5, "post_escape": True})
    add("restore", "action.press_key", {"key": "0"})
    add("disband", "action.press_key", {"key": "0", "modifiers": "ctrl,alt"})
    add("relmod2", "action.release_modifiers")
    add("block_end", "control.input_block_end")
    add("delay", "control.delay", {"seconds": cfg["post_op_delay"]})
    add("unlock", "control.lock_release")

    # ==================== 执行流 ====================
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
    g.connect_exec("relmod1", "out", "select_b", "in")
    if use_count:
        g.connect_exec("select_b", "out", "if_tcok", "in")
        g.connect_exec("if_tcok", "true", "queue", "in")
        g.connect_exec("if_tcok", "false", "end_tcfail", "in")
    else:
        g.connect_exec("select_b", "out", "queue", "in")
    g.connect_exec("queue", "out", "restore", "in")
    g.connect_exec("restore", "out", "disband", "in")
    g.connect_exec("disband", "out", "relmod2", "in")
    g.connect_exec("relmod2", "out", "block_end", "in")
    g.connect_exec("block_end", "out", "delay", "in")
    g.connect_exec("delay", "out", "unlock", "in")

    # ==================== 数据流 ====================
    g.connect_data("win", "in_game", "if_win", "cond")
    g.connect_data("occ", "blocked", "if_blocked", "cond")
    g.connect_data("occ", "in_transition", "if_trans", "cond")
    g.connect_data("vill", "found", "if_vill", "cond")
    g.connect_data("pop", "ok", "if_popok", "cond")

    g.connect_data("food", "value", "cmp_food", "a")
    g.connect_data("c_minfood", "value", "cmp_food", "b")
    g.connect_data("cmp_food", "result", "if_foodok", "cond")

    g.connect_data("pop", "value2", "slots", "a")
    g.connect_data("pop", "value", "slots", "b")
    g.connect_data("slots", "value", "cmp_slots", "a")
    g.connect_data("c_zero", "value", "cmp_slots", "b")
    g.connect_data("cmp_slots", "result", "if_slots", "cond")

    if use_count:
        g.connect_data("tc", "ok", "if_tcok", "cond")
        g.connect_data("c_pertc", "value", "planned", "a")
        g.connect_data("tc", "count", "planned", "b")
    else:
        # 单建筑：planned = per_building * 1
        add("c_one", "data.const_number", {"value": 1})
        g.connect_data("c_pertc", "value", "planned", "a")
        g.connect_data("c_one", "value", "planned", "b")

    g.connect_data("planned", "value", "produce", "planned")
    g.connect_data("slots", "value", "produce", "available_slots")
    g.connect_data("food", "value", "produce", "resource")
    g.connect_data("produce", "count", "queue", "count")

    return g

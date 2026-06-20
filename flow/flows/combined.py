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
    g = Graph(name="统一生产(村民/乡骑/商队)",
              description="同一帧内依次生产 村民/乡骑/商队，每段由一个开关节点单独启停（默认只开村民）。\n"
                          "前段做一次共享判断（暂停/窗口/遮挡）与整屏预取，三段复用人口/资源/TC 等识别结果。\n"
                          "区域/模板/按键/成本均为占位默认值，请按你的分辨率与实际界面在节点上调整。")

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
    add("queue_v", "action.press_key", {"key": "q"})
    add("esc_v", "action.press_key", {"key": "esc"})     # 排完按 ESC 取消选中

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
    add("queue_x", "action.press_key", {"key": "w"})
    add("esc_x", "action.press_key", {"key": "esc"})

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
    add("queue_cart", "action.press_key", {"key": "q"})
    add("esc_cart", "action.press_key", {"key": "esc"})

    # ==================== 收尾（整批一次）====================
    add("restore", "action.press_key", {"key": "0"})
    add("disband", "action.press_key", {"key": "0", "modifiers": "ctrl,alt"})
    add("relmod2", "action.release_modifiers")
    add("block_end", "control.input_block_end")
    add("delay", "control.delay", {"seconds": 3.0})
    add("unlock", "control.lock_release")

    # ==================== 控制面板：把最常用的开关/数值置顶（并起好名字），普通用户不进图也能调 ====================
    # 每项 [节点id, 参数键, 面板显示名]。
    g.panel = [
        ["sw_vill", "on", "出村民"],
        ["sw_xq", "on", "出乡骑(金朝)"],
        ["sw_cart", "on", "出商队(市场)"],
        ["c_per_v", "value", "每个TC出村民数"],
        ["c_per_x", "value", "每个TC出乡骑数"],
        ["c_per_cart", "value", "每个市场出商队数"],
        ["win", "hdr", "HDR模式"],
        ["tick", "interval", "循环间隔(秒)"],
        ["delay", "seconds", "每轮等待(秒)"],
        ["queue_v", "key", "村民生产键"],
        ["queue_x", "key", "乡骑生产键"],
        ["queue_cart", "key", "商队生产键"],
        ["sel_market", "key", "选中市场键"],
    ]

    # ==================== 可视化分组：把三段(+共享/收尾)框起来，一眼看清结构 ====================
    g.groups = [
        # 资源/识别类传感器都放“共享前段”：每帧只识别一次、多段按需拉取（人口/空位、食物、黄金、TC），
        # 放一起对齐、视觉一致（食物虽只村民用，但与其它资源对齐更清楚）。
        {"title": "共享前段（每帧一次）", "color": "#4a8a8a",
         "members": ["tick", "mod", "if_mod", "win", "if_win", "occ", "if_blocked", "if_trans",
                     "prefetch", "pop", "slots", "food", "gold", "tc",
                     "lock", "block_begin", "save_sel", "relmod1"]},
        {"title": "村民段", "color": "#3a6ea5",
         "members": ["sw_vill", "if_sw_vill", "q_vill", "if_qvill", "sel_tc_v",
                     "c_per_v", "plan_v", "prod_v", "queue_v", "esc_v"]},
        {"title": "乡骑段（金朝）", "color": "#8a5a9a",
         "members": ["sw_xq", "if_sw_xq", "q_xq", "if_qxq", "sel_tc_x",
                     "c_per_x", "plan_x", "prod_x", "queue_x", "esc_x"]},
        {"title": "商队段（市场）", "color": "#a5793a",
         "members": ["sw_cart", "if_sw_cart", "q_cart", "if_qcart", "sel_market",
                     "c_per_cart", "c_one", "plan_cart", "prod_cart", "queue_cart", "esc_cart"]},
        {"title": "收尾（整批一次）", "color": "#5a9367",
         "members": ["restore", "disband", "relmod2", "block_end", "delay", "unlock"]},
    ]

    # ==================== 节点说明（编辑器里展示，帮助看懂每个节点的作用）====================
    g.notes.update({
        "tick": "每帧触发：整个流程的循环入口，按「循环间隔」决定多久跑一轮。",
        "mod": "检测是否按住 Shift/Ctrl/Alt——用于“人在手动操作时自动暂停”。",
        "if_mod": "按住修饰键则本帧到此结束（走 false 才继续），相当于手动接管时让路。",
        "win": "检测当前是否在游戏内（窗口标题 + 一个特征像素颜色）。",
        "if_win": "不在游戏中（或没激活）就本帧结束，避免对着别的窗口乱按。",
        "occ": "三态遮挡检测：生产相关区域是否被 UI 盖住 / 正在渐变。",
        "if_blocked": "被遮挡则本帧结束（这时识别/点击都不可靠）。",
        "if_trans": "UI 正在渐入渐出动画时本帧结束，避免误判。",
        "prefetch": "整屏截图一次并缓存，后面所有识别都复用它，省去多次截图。",
        "pop": "识别人口「当前/上限」：数值=当前，数值2=上限。",
        "slots": "人口空位 = 上限 - 当前，作为可生产数量的上限之一。",
        "food": "识别当前食物存量（约束村民产量）。",
        "gold": "识别当前黄金存量（约束乡骑/商队产量）。",
        "tc": "统计当前有几个城镇中心(TC)，用于“每个TC各排一批”。",
        "c_one": "常量 1：商队按“市场数=1”计划（一般只一个市场）。",
        "lock": "抢占操作锁：多开/多脚本时避免同时抢操作。占用中则本帧结束。",
        "block_begin": "开始屏蔽鼠标键盘：操作期间防止人为误触打断。",
        "save_sel": "Ctrl+0：把当前选中的单位暂存为编组 0，操作完再恢复。",
        "relmod1": "松开 Shift/Ctrl/Alt，避免修饰键粘连影响后续按键。",
        "sw_vill": "【开关】是否生产村民。关掉则整段跳过。",
        "if_sw_vill": "村民开关：开→进入村民段；关→直接跳到乡骑段。",
        "q_vill": "检测生产队列里是否已经有村民（避免重复排队）。",
        "if_qvill": "队列已有村民则跳过本段。",
        "sel_tc_v": "按 H 选中所有城镇中心。",
        "c_per_v": "每个城镇中心一次排几个村民。",
        "plan_v": "计划数 = 每TC数量 × TC个数。",
        "prod_v": "实际产量 = min(计划, 人口空位, 食物÷单价)。",
        "queue_v": "按 Q 排队生产村民（按实际产量次数）。",
        "esc_v": "按 ESC 取消选中（排完村民后清掉选择，避免影响后续）。",
        "sw_xq": "【开关】是否生产乡骑（金朝特色）。默认关。",
        "if_sw_xq": "乡骑开关：开→进入乡骑段；关→跳到商队段。",
        "q_xq": "检测队列里是否已有乡骑。",
        "if_qxq": "队列已有乡骑则跳过本段。",
        "sel_tc_x": "按 H 选中所有城镇中心。",
        "c_per_x": "每个城镇中心一次排几个乡骑。",
        "plan_x": "计划数 = 每TC数量 × TC个数。",
        "prod_x": "实际产量 = min(计划, 人口空位, 黄金÷单价)。",
        "queue_x": "按 W 排队生产乡骑。",
        "esc_x": "按 ESC 取消选中。",
        "sw_cart": "【开关】是否生产商队（市场出商队）。默认关。",
        "if_sw_cart": "商队开关：开→进入商队段；关→进入收尾。",
        "q_cart": "检测队列里是否已有商队。",
        "if_qcart": "队列已有商队则跳过本段。",
        "sel_market": "按 G 选中市场。",
        "c_per_cart": "每个市场一次排几个商队。",
        "plan_cart": "计划数 = 每市场数量 × 市场个数(此处常量1)。",
        "prod_cart": "实际产量 = min(计划, 人口空位, 黄金÷单价)。",
        "queue_cart": "按 Q 排队生产商队。",
        "esc_cart": "按 ESC 取消选中。",
        "restore": "按 0 恢复操作前暂存的编组选择。",
        "disband": "Ctrl+Alt+0：解散临时编组，避免污染玩家的编组。",
        "relmod2": "再次松开修饰键，确保收尾干净。",
        "block_end": "结束输入屏蔽，把鼠标键盘还给玩家。",
        "delay": "等待若干秒再进入下一轮，避免空转太频繁。",
        "unlock": "释放操作锁。",
    })

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
    g.connect_exec("queue_v", "out", "esc_v", "in")
    g.connect_exec("esc_v", "out", "if_sw_xq", "in")

    # 乡骑段
    g.connect_exec("if_sw_xq", "true", "if_qxq", "in")
    g.connect_exec("if_sw_xq", "false", "if_sw_cart", "in")
    g.connect_exec("if_qxq", "false", "sel_tc_x", "in")
    g.connect_exec("if_qxq", "true", "if_sw_cart", "in")
    g.connect_exec("sel_tc_x", "out", "queue_x", "in")
    g.connect_exec("queue_x", "out", "esc_x", "in")
    g.connect_exec("esc_x", "out", "if_sw_cart", "in")

    # 商队段
    g.connect_exec("if_sw_cart", "true", "if_qcart", "in")
    g.connect_exec("if_sw_cart", "false", "restore", "in")
    g.connect_exec("if_qcart", "false", "sel_market", "in")
    g.connect_exec("if_qcart", "true", "restore", "in")
    g.connect_exec("sel_market", "out", "queue_cart", "in")
    g.connect_exec("queue_cart", "out", "esc_cart", "in")
    g.connect_exec("esc_cart", "out", "restore", "in")

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

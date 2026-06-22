"""
统一生产流程：在同一张图里、同一帧内依次生产【村民 / 乡骑 / 商人】，每段都有独立开关。

设计要点（回答"能否同图同时跑、阶段复用"）：
- 共享前段只跑一次：修饰键/窗口/遮挡/渐变判断 -> 取锁 -> 屏蔽 -> 存编组。
- 之后三段生产串成接力，每段：[开关?] -> [队列已有该单位?] -> 选建筑 -> 产能计算 -> 排队；
  段内任一"跳过"都接到【下一段】（而非结束本帧），所以关掉/跳过某段不影响其它段。
- 取锁/屏蔽/存编组/收尾对整批生产只做一次（一次输入屏蔽窗口内做完所有生产）。
- 传感器（截图/OCR/模板）逐帧记忆化：多段都读"人口/空位"等也只算一次。

开关默认：村民=开、乡骑=关、商人=关（默认行为≈普通出农；用户在节点上翻开关即可启停各段）。
区域/模板/按键/成本均为占位默认值，需用编辑器按实际填写（尤其乡骑/商人模板）。
"""
from __future__ import annotations

from ..core import Graph, create_node

WIN_PIXEL = [2526, 1405]
WIN_COLOR = [26, 32, 46]
BLOCKED_REGION = [265, 950, 280, 970]
QUEUE_REGION = [10, 970, 500, 1025]
POP_REGION = [50, 1140, 150, 1170]
FOOD_REGION = [50, 1222, 140, 1248]
GOLD_REGION = [47, 1327, 137, 1353]
WOOD_REGION = [47, 1300, 137, 1326]   # 占位：木头数字区域，请按你的界面调整（仅商人用木头时才需要）
TC = {
    "icon_region": [444, 1212, 492, 1259],
    "single_region": [300, 1140, 354, 1194],
    "single_template": "templates/tc_single.png",
    "numbered_templates": [f"templates/tc_number_{i}.png" for i in range(1, 7)],
}


def build_combined_graph() -> Graph:
    g = Graph(name="统一生产(村民/乡骑/商人)",
              description="同一帧内依次生产 村民/乡骑/商人，每段由一个开关节点单独启停（默认只开村民）。\n"
                          "前段做一次共享判断（暂停/窗口/遮挡）；三段复用人口/资源/TC 等识别结果"
                          "（每个区域每帧只截一次、自动缓存共享，无需整屏预取）。\n"
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
    add("and_win", "logic.and")    # 窗口激活 且 像素点检测通过 = 确实在游戏中
    add("if_win", "control.if")
    add("occ", "game.occlusion", {"region": BLOCKED_REGION, "template": "templates/blocked.png"})
    add("if_blocked", "control.if")
    add("if_trans", "control.if")

    # 共享传感器（数据，按需且逐帧记忆化）
    add("pop", "sense.ocr_number", {"region": POP_REGION, "regex": r"(\d+)[/\\|](\d+)"})
    add("slots", "math.arith", {"op": "-"})       # 空位 = 人口上限 - 当前人口
    add("food", "sense.ocr_number", {"region": FOOD_REGION, "regex": r"(\d+)"})
    add("gold", "sense.ocr_number", {"region": GOLD_REGION, "regex": r"(\d+)"})
    add("wood", "sense.ocr_number", {"region": WOOD_REGION, "regex": r"(\d+)"})  # 木头：默认不参与限制（成本0），仅在某单位需要木头时启用
    add("tc", "game.tc_count", TC)
    add("c_one", "data.const_number", {"value": 1})

    # ============ 生产门控（前置短路）============
    # 操作区（抢锁/屏蔽输入/Ctrl+0 存编组/0 恢复/Ctrl+Alt+0 解散）对整批只做一次，
    # 但必须先确认「真的有东西要生产」才进——否则队列里已有单位在造（最常见的稳态）时，
    # 每帧仍抢锁、屏蔽输入、折腾编组 0，等于不停骚扰玩家键鼠/选择。
    #
    # 判定原则：产量 = min(每TC数×TC数量, 人口空位, 资源÷单价)。其中【人口空位】和【资源够不够买1个】
    #   都不依赖按 H 选 TC，可以在进操作区【之前】就判——任一为0则产量必为0，直接跳过本段/结束本帧。
    #   只有【TC 数量】依赖按 H（面板选中后才数得到），所以 TC 不在门控里读（否则会出现“选TC前读到TC=0→
    #   永不生产”的顺序死结），真正乘 TC 数的产量仍在各段内、选完 TC 后才算。
    #
    # 于是门控逐段短路判：开关开? → 队列空? → 资源≥单价? → （共用）人口空位>0? → 进操作区。
    #   任一不满足就看下一段；三段都无活 / 人口已满＝本帧结束。资源比较顺带把对应 OCR 提前算好缓存，
    #   等于把资源识别耗时挪到了抢锁/屏蔽之前（屏蔽窗口更短）。队列占用的稳态下只跑一次廉价模板匹配、不读资源/人口。
    add("c_zero", "data.const_number", {"value": 0})   # 用于「人口空位 > 0」比较
    add("pre_sw_vill", "control.if")
    add("pre_q_vill", "control.if")
    add("pre_sw_xq", "control.if")
    add("pre_q_xq", "control.if")
    add("pre_sw_cart", "control.if")
    add("pre_q_cart", "control.if")
    add("cmp_slots", "logic.compare", {"op": ">"})     # 人口空位 > 0 ?（三段共用一次）
    add("pre_slots", "control.if")
    # 资源够不够再进操作区：产量 = min(每TC数×TC数, 人口空位, 资源÷单价)，只要资源连 1 个都买不起（资源<单价）产量必是0。
    # 资源/人口都【不依赖按H选TC】，可在抢锁/屏蔽【之前】就判掉——避免“食物/黄金不足却照样进操作区、按H、排0个”白白骚扰键鼠。
    # 这几个资源比较同时把对应 OCR 提前算好缓存（取代原 warm_food/warm_gold 预读节点：既当门控又当预热）。
    add("c_cost_v", "data.const_number", {"value": 50})    # 村民单价(食物)：资源<它则产量必为0
    add("cmp_food_v", "logic.compare", {"op": ">="})       # 食物 ≥ 村民单价 ?
    add("pre_food_v", "control.if")
    # 乡骑同时吃【食物65 + 黄金50】——两种都够才能出1个，门控两道都要过(任一不够就跳过乡骑段)。
    add("c_cost_x_food", "data.const_number", {"value": 65})  # 乡骑单价(食物)
    add("cmp_food_x", "logic.compare", {"op": ">="})         # 食物 ≥ 乡骑食物单价 ?
    add("pre_food_x", "control.if")
    add("c_cost_x", "data.const_number", {"value": 50})    # 乡骑单价(黄金)
    add("cmp_gold_x", "logic.compare", {"op": ">="})       # 黄金 ≥ 乡骑黄金单价 ?
    add("pre_gold_x", "control.if")
    add("c_cost_cart", "data.const_number", {"value": 60})   # 商人单价(黄金)；不同国家不同，放到了控制面板可调
    add("cmp_gold_cart", "logic.compare", {"op": ">="})    # 黄金 ≥ 商人单价 ?
    add("pre_gold_cart", "control.if")
    # 商人木头成本：默认 0 = 不参与限制（安全：木头区域没配好也不会误把商人卡成0）。
    # 需要时把它设为实际值(如60)并把上面的「木头数字区域」设对，商人产量就会再受 木头÷成本 限制。
    add("c_cost_cart_wood", "data.const_number", {"value": 0})

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
    add("sel_tc_v", "action.press_key", {"key": "h"})       # 选中所有TC（计数节点内置“按键后自动重试重截”，无需固定延时）
    add("c_per_v", "data.const_number", {"value": 3})
    add("plan_v", "math.arith", {"op": "*"})
    add("prod_v", "game.produce_count", {"cost_per_unit": 50, "cap": -1})
    add("queue_v", "action.press_key", {"key": "q"})

    # ==================== 乡骑段 ====================
    add("sw_xq", "data.switch", {"on": False})              # 开关：是否生产乡骑（金朝）
    add("if_sw_xq", "control.if")
    add("q_xq", "sense.template_match",
        {"region": QUEUE_REGION, "templates": ["templates/xiangqi.jpg"], "threshold": 0.6, "transition_guard": True})
    add("if_qxq", "control.if")
    add("sel_tc_x", "action.press_key", {"key": "h"})       # 同村民段：计数节点内置“按键后自动重试重截”，无需固定延时
    add("c_per_x", "data.const_number", {"value": 2})
    add("plan_x", "math.arith", {"op": "*"})
    add("prod_x", "game.produce_count", {"cost_per_unit": 80, "cap": -1})
    add("queue_x", "action.press_key", {"key": "w"})

    # ==================== 商人段 ====================
    add("sw_cart", "data.switch", {"on": False})            # 开关：是否生产商人（市场）
    add("if_sw_cart", "control.if")
    add("q_cart", "sense.template_match",
        {"region": QUEUE_REGION, "templates": ["templates/businessman.png"], "threshold": 0.6, "transition_guard": True})
    add("if_qcart", "control.if")
    add("sel_market", "action.press_key", {"key": "j"})     # 选中所有市场（默认绑定 J）
    add("c_per_cart", "data.const_number", {"value": 2})
    add("plan_cart", "math.arith", {"op": "*"})
    add("prod_cart", "game.produce_count", {"cost_per_unit": 60, "cap": -1})   # 成本由 c_cost_cart 喂入(下方连线)，此为兜底
    add("queue_cart", "action.press_key", {"key": "q"})

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
        ["sw_cart", "on", "出商人(市场)"],
        ["c_per_v", "value", "每个TC出村民数"],
        ["c_per_x", "value", "每个TC出乡骑数"],
        ["c_per_cart", "value", "每个市场出商人数"],
        ["c_cost_v", "value", "村民成本(食物)"],
        ["c_cost_x_food", "value", "乡骑成本(食物)"],
        ["c_cost_x", "value", "乡骑成本(黄金)"],
        ["c_cost_cart", "value", "商人成本(黄金)"],
        ["c_cost_cart_wood", "value", "商人成本(木头·0=不限)"],
        ["win", "hdr", "HDR模式"],
        ["tick", "interval", "循环间隔(秒)"],
        ["delay", "seconds", "每轮等待(秒)"],
        ["queue_v", "key", "村民生产键"],
        ["queue_x", "key", "乡骑生产键"],
        ["queue_cart", "key", "商人生产键"],
        ["sel_market", "key", "选中市场键"],
    ]

    # ==================== 可视化分组：把三段(+共享/收尾)框起来，一眼看清结构 ====================
    g.groups = [
        # 资源/识别类传感器都放“共享前段”：每帧只识别一次、多段按需拉取（人口/空位、食物、黄金、TC），
        # 放一起对齐、视觉一致（食物虽只村民用，但与其它资源对齐更清楚）。
        {"title": "共享前段（每帧一次）", "color": "#4a8a8a",
         "members": ["tick", "mod", "if_mod", "win", "and_win", "if_win", "occ", "if_blocked", "if_trans",
                     "pop", "slots", "food", "gold", "wood", "tc",
                     "lock", "block_begin", "save_sel", "relmod1"]},
        {"title": "村民段", "color": "#3a6ea5",
         "members": ["sw_vill", "if_sw_vill", "q_vill", "if_qvill", "sel_tc_v",
                     "c_per_v", "plan_v", "prod_v", "queue_v"]},
        {"title": "乡骑段（金朝）", "color": "#8a5a9a",
         "members": ["sw_xq", "if_sw_xq", "q_xq", "if_qxq", "sel_tc_x",
                     "c_per_x", "plan_x", "prod_x", "queue_x"]},
        {"title": "商人段（市场）", "color": "#a5793a",
         "members": ["sw_cart", "if_sw_cart", "q_cart", "if_qcart", "sel_market",
                     "c_per_cart", "c_one", "plan_cart", "prod_cart", "queue_cart"]},
        {"title": "生产门控（开关+队列空+资源够+人口空位）", "color": "#7a6a4a",
         "members": ["c_zero", "pre_sw_vill", "pre_q_vill",
                     "pre_sw_xq", "pre_q_xq",
                     "pre_sw_cart", "pre_q_cart",
                     "c_cost_v", "cmp_food_v", "pre_food_v",
                     "c_cost_x_food", "cmp_food_x", "pre_food_x",
                     "c_cost_x", "cmp_gold_x", "pre_gold_x",
                     "c_cost_cart", "cmp_gold_cart", "pre_gold_cart", "c_cost_cart_wood",
                     "cmp_slots", "pre_slots"]},
        {"title": "收尾（整批一次）", "color": "#5a9367",
         "members": ["restore", "disband", "relmod2", "block_end", "delay", "unlock"]},
    ]

    # ==================== 节点说明（编辑器里展示，帮助看懂每个节点的作用）====================
    g.notes.update({
        "tick": "每帧触发：整个流程的循环入口，按「循环间隔」决定多久跑一轮。",
        "mod": "检测是否按住 Shift/Ctrl/Alt——用于“人在手动操作时自动暂停”。",
        "if_mod": "修饰键被按下：本帧到此结束（你在手动操作时让路，松开即继续）。",
        "win": "游戏窗口检测：分别输出「窗口激活」(标题像游戏) 与「像素点检测通过」(特征像素颜色符合)。",
        "and_win": "「与」：窗口激活 且 像素点检测通过 → 才算确实在游戏中。两项分开，便于按需单独使用。",
        "if_win": "不在游戏中：本帧到此结束，避免对着别的窗口乱按。",
        "occ": "三态遮挡检测：生产相关区域是否被 UI 盖住 / 正在渐变。",
        "if_blocked": "界面被遮挡：本帧结束（这时识别/点击都不可靠）。",
        "if_trans": "界面渐变中：UI 渐入渐出动画时本帧结束，避免误判。",
        "pop": "识别人口「当前/上限」：数值=当前，数值2=上限。",
        "slots": "人口空位 = 上限 - 当前，作为可生产数量的上限之一。",
        "food": "识别当前食物存量（约束村民产量）。",
        "gold": "识别当前黄金存量（约束乡骑/商人产量）。",
        "wood": "识别当前木头存量。默认不参与限制（商人木头成本=0）；某单位需要木头时，把对应成本设为实际值并把本区域设对即可启用。",
        "tc": "统计当前有几个城镇中心(TC)，用于“每个TC各排一批”。",
        "c_one": "常量 1：商人按“市场数=1”计划（一般只一个市场）。",
        "lock": "抢占操作锁：多开/多脚本时避免同时抢操作。占用中则本帧结束。",
        "block_begin": "开始屏蔽鼠标键盘：操作期间防止人为误触打断。",
        "save_sel": "Ctrl+0：把当前选中的单位暂存为编组 0，操作完再恢复。",
        "relmod1": "松开 Shift/Ctrl/Alt，避免修饰键粘连影响后续按键。",
        "c_zero": "常量 0：用于门控「人口空位 > 0」比较。",
        "pre_sw_vill": "门控·村民开关开着吗？开→看村民队列；关→看乡骑段。",
        "pre_q_vill": "门控·村民队列空吗？空→去看“人口有空位吗”；已在造→跳过本段（不抢锁）。",
        "pre_sw_xq": "门控·乡骑开关开着吗？开→看乡骑队列；关→看商人段。",
        "pre_q_xq": "门控·乡骑队列空吗？空→去看“人口有空位吗”；已在造→看商人段。",
        "pre_sw_cart": "门控·商人开关开着吗？关→本帧结束（无活）。",
        "pre_q_cart": "门控·商人队列空吗？空→去看“人口有空位吗”；已在造→本帧结束。",
        "c_cost_v": "常量·村民单价(食物，默认50)：食物不到这个数连1个都买不起、产量必为0。不同国家不同，已放到控制面板；同时供门控和产能计算。",
        "cmp_food_v": "门控·食物 ≥ 村民单价？顺带把食物OCR提前算好（挪出屏蔽窗口）。",
        "pre_food_v": "门控·食物够→看人口空位；不够（产量必为0）→跳过村民段，看乡骑（不进操作区、不按H、不排0个）。",
        "c_cost_x_food": "常量·乡骑单价(食物，默认65)。乡骑同时吃食物+黄金；不同国家不同，已放到控制面板；同时供门控和产能计算。",
        "cmp_food_x": "门控·食物 ≥ 乡骑食物单价？顺带预热食物OCR。",
        "pre_food_x": "门控·食物够→再看黄金；不够→跳过乡骑段，看商人。",
        "c_cost_x": "常量·乡骑单价(黄金，默认50)。乡骑同时吃食物+黄金；不同国家不同，已放到控制面板；同时供门控和产能计算。",
        "cmp_gold_x": "门控·黄金 ≥ 乡骑黄金单价？顺带预热黄金OCR。",
        "pre_gold_x": "门控·食物+黄金都够→看人口空位；黄金不够→跳过乡骑段，看商人。",
        "c_cost_cart": "常量·商人单价(黄金，默认60)。不同国家不同——已放到控制面板，可按你的国家调。同时供门控判断和产能计算。",
        "c_cost_cart_wood": "常量·商人单价(木头，默认0=不限制)。安全起见默认不看木头；需要时设为实际值(如60)并把「木头数字区域」设对即可启用。已放到控制面板。",
        "cmp_gold_cart": "门控·黄金 ≥ 商人单价？",
        "pre_gold_cart": "门控·黄金够→看人口空位；不够→本帧结束。",
        "cmp_slots": "门控·人口空位 > 0？（三段共用：人口已满时谁都产不了，直接结束本帧）。",
        "pre_slots": "门控·有空位→开操作区(lock) 开始生产；人口已满→本帧到此结束（不抢锁/不屏蔽/不动编组）。",
        "sw_vill": "【开关】是否生产村民。关掉则整段跳过。",
        "if_sw_vill": "村民开关：开→进入村民段；关→直接跳到乡骑段。",
        "q_vill": "检测生产队列里是否已经有村民（避免重复排队）。",
        "if_qvill": "队列已有村民则跳过本段。",
        "sel_tc_v": "按 H 选中所有城镇中心。⚠必须在游戏里把“选所有TC”绑定到 H。"
                    "按完不必再放延时——下游「多TC计数」节点会在面板刷新出来前自动重试重截。",
        "c_per_v": "每个城镇中心一次排几个村民。",
        "plan_v": "计划数 = 每TC数量 × TC个数。",
        "prod_v": "实际产量 = min(计划, 人口空位, 食物÷单价)。并把“用剩的人口空位”结转给乡骑段——多段共享人口池、逐段扣减，避免同帧重复占用。村民这帧没真生产(关/已在造)时产0、空位原样传下去。",
        "queue_v": "按 Q 排队生产村民（按实际产量次数）。",
        "sw_xq": "【开关】是否生产乡骑（金朝特色）。默认关。",
        "if_sw_xq": "乡骑开关：开→进入乡骑段；关→跳到商人段。",
        "q_xq": "检测队列里是否已有乡骑。",
        "if_qxq": "队列已有乡骑则跳过本段。",
        "sel_tc_x": "按 H 选中所有城镇中心。按完不必再放延时——「多TC计数」节点会自动重试重截。",
        "c_per_x": "每个城镇中心一次排几个乡骑。",
        "plan_x": "计划数 = 每TC数量 × TC个数。",
        "prod_x": "实际产量 = min(计划, 剩余空位, 剩余食物÷食物单价, 黄金÷黄金单价)。乡骑同时吃食物+黄金：食物从村民段结转(共用食物池)、黄金为黄金链起点；再把用剩的空位/黄金结转给商人段。乡骑没真生产时产0、预算透传。",
        "queue_x": "按 W 排队生产乡骑。",
        "sw_cart": "【开关】是否生产商人（市场出商人）。默认关。",
        "if_sw_cart": "商人开关：开→进入商人段；关→进入收尾。",
        "q_cart": "检测队列里是否已有商人。",
        "if_qcart": "队列已有商人则跳过本段。",
        "sel_market": "按 J 选中所有市场。⚠需在游戏里把“选所有市场”绑定到 J。",
        "c_per_cart": "每个市场一次排几个商人。",
        "plan_cart": "计划数 = 每市场数量 × 市场个数(此处常量1)。",
        "prod_cart": "实际产量 = min(计划, 剩余人口空位, 剩余黄金÷单价[, 木头÷木头单价])。空位/黄金均来自乡骑段结转——所以即使村民/乡骑同帧在产，商人仍按“真正剩下的”池子独立生产。木头默认不限制(成本0)。",
        "queue_cart": "按 Q 排队生产商人。",
        "restore": "按 0 恢复操作前暂存的编组选择。",
        "disband": "Ctrl+Alt+0：解散临时编组，避免污染玩家的编组。",
        "relmod2": "再次松开修饰键，确保收尾干净。",
        "block_end": "结束输入屏蔽，把鼠标键盘还给玩家。",
        "delay": "本轮操作后等待若干秒再进下一轮——【关键：防重复生产】。网络卡顿时按键要零点几到1~2秒甚至更久才真正生效，"
                 "等待太短会以为“还没造”而重复按、重复生产。也给玩家留出手动临时取消生产的窗口（想多留就把秒数调大，如几十秒）。",
        "unlock": "释放操作锁。",
    })

    # ==================== 执行流 ====================
    # 共享前段（任一守卫不过 -> 结束本帧；出口不接即结束）
    g.connect_exec("tick", "out", "if_mod", "in")
    g.connect_exec("if_mod", "false", "if_win", "in")       # 没按修饰键才继续
    g.connect_exec("if_win", "true", "if_blocked", "in")    # 在游戏中才继续
    g.connect_exec("if_blocked", "false", "if_trans", "in") # 未遮挡才继续
    # 非渐变才继续 -> 直接进入生产门控（区域截图按需进行、逐帧自动缓存，无需整屏预取）
    g.connect_exec("if_trans", "false", "pre_sw_vill", "in")

    # 生产门控：逐段「开关→队列空→资源够」短路；任一段全部满足→跳到共用的「人口空位>0?」→进操作区。
    # 资源/人口都不依赖按H选TC，所以能在抢锁/屏蔽【之前】判掉：产量必为0（资源不足/人口已满）时根本不进操作区。
    # 每段任一条件不满足→看下一段；三段都没活 / 人口已满＝本帧结束（出口不接即结束）。不在门控里读 TC。
    g.connect_exec("pre_sw_vill", "true", "pre_q_vill", "in")
    g.connect_exec("pre_sw_vill", "false", "pre_sw_xq", "in")
    g.connect_exec("pre_q_vill", "true", "pre_sw_xq", "in")     # 已在造 → 跳过本段，看乡骑
    g.connect_exec("pre_q_vill", "false", "pre_food_v", "in")   # 队列空 → 看食物够不够买1个村民
    g.connect_exec("pre_food_v", "true", "pre_slots", "in")     # 食物够 → 看人口空位
    g.connect_exec("pre_food_v", "false", "pre_sw_xq", "in")    # 食物不足（产量必为0）→ 跳过村民段，看乡骑
    g.connect_exec("pre_sw_xq", "true", "pre_q_xq", "in")
    g.connect_exec("pre_sw_xq", "false", "pre_sw_cart", "in")
    g.connect_exec("pre_q_xq", "true", "pre_sw_cart", "in")
    g.connect_exec("pre_q_xq", "false", "pre_food_x", "in")     # 队列空 → 先看食物够不够买1个乡骑
    g.connect_exec("pre_food_x", "true", "pre_gold_x", "in")    # 食物够 → 再看黄金
    g.connect_exec("pre_food_x", "false", "pre_sw_cart", "in")  # 食物不足 → 跳过乡骑段，看商人
    g.connect_exec("pre_gold_x", "true", "pre_slots", "in")     # 食物+黄金都够 → 看人口空位
    g.connect_exec("pre_gold_x", "false", "pre_sw_cart", "in")  # 黄金不足 → 跳过乡骑段，看商人
    g.connect_exec("pre_sw_cart", "true", "pre_q_cart", "in")   # false 不接 = 本帧结束
    g.connect_exec("pre_q_cart", "false", "pre_gold_cart", "in")  # 队列空 → 看黄金够不够买1个商人
    g.connect_exec("pre_gold_cart", "true", "pre_slots", "in")  # false 不接 = 黄金不足，本帧结束
    g.connect_exec("pre_slots", "true", "lock", "in")           # 有空位 → 开操作区开始生产；false 不接 = 人口已满，本帧结束

    g.connect_exec("lock", "ok", "block_begin", "in")       # 占用中(busy)则结束本帧
    g.connect_exec("block_begin", "out", "save_sel", "in")
    g.connect_exec("save_sel", "out", "relmod1", "in")
    g.connect_exec("relmod1", "out", "if_sw_vill", "in")

    # 村民段：开关 -> 队列检查 -> 选TC -> 排队；任一跳过都接到乡骑段
    g.connect_exec("if_sw_vill", "true", "if_qvill", "in")
    g.connect_exec("if_sw_vill", "false", "if_sw_xq", "in")
    g.connect_exec("if_qvill", "false", "sel_tc_v", "in")   # 队列没有村民才生产
    g.connect_exec("if_qvill", "true", "if_sw_xq", "in")
    g.connect_exec("sel_tc_v", "out", "queue_v", "in")     # 按H -> 直接数TC并排队（计数节点自带按键后重试重截）
    g.connect_exec("queue_v", "out", "if_sw_xq", "in")   # 出完村民 -> 进乡骑段（最后统一恢复选择，不按 ESC）

    # 乡骑段
    g.connect_exec("if_sw_xq", "true", "if_qxq", "in")
    g.connect_exec("if_sw_xq", "false", "if_sw_cart", "in")
    g.connect_exec("if_qxq", "false", "sel_tc_x", "in")
    g.connect_exec("if_qxq", "true", "if_sw_cart", "in")
    g.connect_exec("sel_tc_x", "out", "queue_x", "in")    # 按H -> 直接数TC并排队（计数节点自带按键后重试重截）
    g.connect_exec("queue_x", "out", "if_sw_cart", "in")

    # 商人段
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
    g.connect_data("win", "active", "and_win", "a")
    g.connect_data("win", "pixel_ok", "and_win", "b")
    g.connect_data("and_win", "result", "if_win", "cond")
    g.connect_data("occ", "blocked", "if_blocked", "cond")
    g.connect_data("occ", "in_transition", "if_trans", "cond")

    # 空位 = 人口上限(value2) - 当前人口(value)
    g.connect_data("pop", "value2", "slots", "a")
    g.connect_data("pop", "value", "slots", "b")

    # ——— 产能计算：多段共享同一“预算池”，逐段扣减后结转给下一段 ———
    # 同一帧里依次跑 村民→乡骑→商人；它们共用人口（空位），乡骑与商人共用黄金。
    # 关键修复：以前三段都读“没扣减过”的同一个空位/黄金，前段排了之后后段仍按旧值排 → 超额、被游戏静默
    #   拒绝（实测“村民在产时商人怎么都不产、非得等村民这轮产完才和村民一起产”，正是这种同帧重复占用所致）。
    # 现在：① 空位串成链：村民拿到“空位”，输出“剩余空位”给乡骑，乡骑再把剩余给商人；
    #       ② 资源按物理种类固定到通道并各自串链：食物=资源1（村民→乡骑），黄金=资源3（乡骑→商人），木头=资源2（商人）。
    #          —— 乡骑同时吃食物65+黄金50，所以它的食物从村民段结转、黄金作为黄金链的起点；商人的黄金再从乡骑结转。
    #       ③ 每段把“开关/队列已占用”喂给产能节点：本段这帧其实不生产时产量记 0、预算原样透传，不误占池子。
    #   于是村民在产时（村民这段 busy→产0、空位/食物原样传下去），乡骑/商人仍能用到真正剩下的空位/资源，独立生产。

    # 村民段数据（吃食物；空位预算链的起点）
    g.connect_data("sw_vill", "value", "if_sw_vill", "cond")
    g.connect_data("q_vill", "found", "if_qvill", "cond")
    g.connect_data("c_per_v", "value", "plan_v", "a")
    g.connect_data("tc", "count", "plan_v", "b")
    g.connect_data("plan_v", "value", "prod_v", "planned")
    g.connect_data("slots", "value", "prod_v", "available_slots")   # 人口空位预算（链头）
    g.connect_data("food", "value", "prod_v", "food")               # 村民吃食物
    g.connect_data("c_cost_v", "value", "prod_v", "food_cost")      # 成本同源：门控与产能用同一个常量(可在面板按国家调)
    g.connect_data("sw_vill", "value", "prod_v", "switch")           # 段关→产0、预算透传
    g.connect_data("q_vill", "found", "prod_v", "busy")             # 队列已在造村民→产0、预算透传
    g.connect_data("prod_v", "count", "queue_v", "count")

    # 乡骑段数据（同时吃食物65+黄金50；空位与食物从村民段结转，黄金是黄金链起点）
    g.connect_data("sw_xq", "value", "if_sw_xq", "cond")
    g.connect_data("q_xq", "found", "if_qxq", "cond")
    g.connect_data("c_per_x", "value", "plan_x", "a")
    g.connect_data("tc", "count", "plan_x", "b")
    g.connect_data("plan_x", "value", "prod_x", "planned")
    g.connect_data("prod_v", "slots_left", "prod_x", "available_slots")  # 空位 = 村民用剩的
    g.connect_data("prod_v", "food_left", "prod_x", "food")              # 食物从村民段结转（村民和乡骑共用食物池）
    g.connect_data("c_cost_x_food", "value", "prod_x", "food_cost")      # 乡骑食物单价(65)
    g.connect_data("gold", "value", "prod_x", "gold")                    # 黄金（黄金链起点，村民不吃黄金）
    g.connect_data("c_cost_x", "value", "prod_x", "gold_cost")           # 乡骑黄金单价(50)
    g.connect_data("sw_xq", "value", "prod_x", "switch")
    g.connect_data("q_xq", "found", "prod_x", "busy")
    g.connect_data("prod_x", "count", "queue_x", "count")

    # 商人段数据（市场数按 1 计；空位与黄金从乡骑段结转；可选再吃木头）
    g.connect_data("sw_cart", "value", "if_sw_cart", "cond")
    g.connect_data("q_cart", "found", "if_qcart", "cond")
    g.connect_data("c_per_cart", "value", "plan_cart", "a")
    g.connect_data("c_one", "value", "plan_cart", "b")
    g.connect_data("plan_cart", "value", "prod_cart", "planned")
    g.connect_data("prod_x", "slots_left", "prod_cart", "available_slots")   # 空位 = 乡骑用剩的（=村民也用剩的）
    g.connect_data("prod_x", "gold_left", "prod_cart", "gold")               # 黄金 = 乡骑用剩的
    g.connect_data("c_cost_cart", "value", "prod_cart", "gold_cost")         # 商人黄金单价
    g.connect_data("wood", "value", "prod_cart", "wood")                     # 木头（默认成本0=不限制）
    g.connect_data("c_cost_cart_wood", "value", "prod_cart", "wood_cost")    # 商人木头单价
    g.connect_data("sw_cart", "value", "prod_cart", "switch")
    g.connect_data("q_cart", "found", "prod_cart", "busy")
    g.connect_data("prod_cart", "count", "queue_cart", "count")

    # 生产门控数据（简化）：开关/队列复用各段已有节点（按帧记忆化）；产能/食物/TC 一概不读。
    # 人口空位 > 0 三段共用一次（slots = 人口上限 - 当前，已在共享前段算好）。
    g.connect_data("sw_vill", "value", "pre_sw_vill", "cond")
    g.connect_data("q_vill", "found", "pre_q_vill", "cond")
    g.connect_data("sw_xq", "value", "pre_sw_xq", "cond")
    g.connect_data("q_xq", "found", "pre_q_xq", "cond")
    g.connect_data("sw_cart", "value", "pre_sw_cart", "cond")
    g.connect_data("q_cart", "found", "pre_q_cart", "cond")
    g.connect_data("slots", "value", "cmp_slots", "a")
    g.connect_data("c_zero", "value", "cmp_slots", "b")
    g.connect_data("cmp_slots", "result", "pre_slots", "cond")

    # 门控·资源够不够买1个：资源 ≥ 单价。这几个比较同时把对应 OCR 提前算好缓存（值仍在各段内正式使用），
    # 等于把资源识别的耗时挪到了抢锁/屏蔽之前——既当门控又当预热。
    g.connect_data("food", "value", "cmp_food_v", "a")
    g.connect_data("c_cost_v", "value", "cmp_food_v", "b")
    g.connect_data("cmp_food_v", "result", "pre_food_v", "cond")
    g.connect_data("food", "value", "cmp_food_x", "a")          # 乡骑食物门控
    g.connect_data("c_cost_x_food", "value", "cmp_food_x", "b")
    g.connect_data("cmp_food_x", "result", "pre_food_x", "cond")
    g.connect_data("gold", "value", "cmp_gold_x", "a")
    g.connect_data("c_cost_x", "value", "cmp_gold_x", "b")
    g.connect_data("cmp_gold_x", "result", "pre_gold_x", "cond")
    g.connect_data("gold", "value", "cmp_gold_cart", "a")
    g.connect_data("c_cost_cart", "value", "cmp_gold_cart", "b")
    g.connect_data("cmp_gold_cart", "result", "pre_gold_cart", "cond")

    return g

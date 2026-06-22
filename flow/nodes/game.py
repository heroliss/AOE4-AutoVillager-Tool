"""
游戏专用的"重逻辑"节点：三态遮挡检测、多TC计数、产能计算。

这三者算法内聚（尤其遮挡检测自带跨帧状态机），拆成连线反而更难懂，
因此各自封装为单个节点，内部沿用既有 villager_training_detector / tc_counter 的算法，
但区域/模板/阈值等参数全部来自节点自身。
"""
from __future__ import annotations

import time

from ..core import DataNode, ParamSpec, DataType, data_in, data_out, register
from . import _imaging


# ==================== 三态遮挡检测 ====================
@register
class Occlusion(DataNode):
    """检测某区域是否被 UI 遮挡：完全遮挡 / 完全未遮挡 / 渐变中（含稳定性与误判修正）。"""

    type_id = "game.occlusion"
    category = "游戏"
    title = "三态遮挡检测"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("blocked", DataType.BOOL, label="遮挡",
                 help="那块区域被完全盖住（如打开了某个面板）。此时识别不可靠，流程应跳过本帧。"),
        data_out("in_transition", DataType.BOOL, label="渐变中",
                 help="UI 正在渐入/渐出动画、或读数还没稳定。此时识别会误判，流程应跳过本帧。"),
        data_out("clear", DataType.BOOL, label="未遮挡",
                 help="区域干净、可放心识别（= 既非遮挡也非渐变）。三态里的“正常”态。"),
        data_out("confidence", DataType.NUMBER, label="置信度",
                 help="与“遮挡模板”的匹配分(0~1)：越高越像被遮挡。用于判定 遮挡/渐变/未遮挡 三态的分界。"),
        data_out("state", DataType.STRING, label="状态",
                 help="给人看的状态文字：完全遮挡 / 未遮挡 / 渐变中，可能带“(不稳定)”或“误判修正->未遮挡”。仅展示、不参与判断。"),
    ]
    params = [
        ParamSpec("region", "检测区域", "region", default=[265, 950, 280, 970],
                  help="要监测是否被遮挡的小块区域（一般取生产队列那一块）。"),
        ParamSpec("template", "遮挡模板", "template", default="",
                  help="该区域“被遮挡时”长什么样的模板图，用来比对。"),
        ParamSpec("match_threshold", "完全遮挡阈值", "float", default=0.7, minimum=0.0, maximum=1.0, step=0.01,
                  help="匹配分 ≥ 它 → 判为“完全遮挡”。"),
        ParamSpec("transition_threshold", "渐变下限阈值", "float", default=0.1, minimum=0.0, maximum=1.0, step=0.01,
                  help="匹配分 < 它 → 判为“未遮挡”；介于本阈值与“完全遮挡阈值”之间 → “渐变中”。"),
        ParamSpec("stable_threshold", "稳定所需次数", "int", default=2, minimum=1, maximum=10,
                  help="同一状态要连续出现这么多次才算“稳定”，否则先当“渐变中”——防 UI 抖动误判。"),
        ParamSpec("transition_repeat", "渐变误判次数", "int", default=3, minimum=1, maximum=10, advanced=True,
                  help="落在渐变区但连续这么多次几乎不变 → 判定为“场景色误判”、当作未遮挡。调试用。"),
        ParamSpec("change_threshold", "渐变误判变化阈", "float", default=0.05, minimum=0.0, maximum=1.0, step=0.01, advanced=True,
                  help="配合上一项：相邻帧匹配分变化 < 它才算“几乎不变”。调试用。"),
    ]

    def __init__(self):
        super().__init__()
        self._last_state = None
        self._stable_count = 0
        self._transition_count = 0
        self._last_transition_conf = 0.0

    def evaluate(self, ctx, inputs):
        region = self.values["region"]
        img = inputs.get("image")
        if img is None:
            img = ctx.capture_region(region)
        gray = _imaging.to_gray(img)

        left, top, right, bottom = region
        tmpl = _imaging.resize_to(_imaging.load_gray(self.values["template"]),
                                  right - left, bottom - top)
        conf = _imaging.best_match(gray, tmpl)

        m_thr = self.values["match_threshold"]
        t_thr = self.values["transition_threshold"]

        if conf >= m_thr:
            cur, status = "blocked", "完全遮挡"
        elif conf < t_thr:
            cur, status = "clear", "未遮挡"
        else:
            cur, status = "transition", "渐变中"

        # 稳定性检测
        if cur == self._last_state:
            self._stable_count += 1
        else:
            self._last_state = cur
            self._stable_count = 1

        blocked = in_transition = False
        if self._stable_count < self.values["stable_threshold"]:
            in_transition = True
            self._transition_count = 0
            status = f"{status}(不稳定)"
        elif cur == "blocked":
            blocked = True
            self._transition_count = 0
        elif cur == "clear":
            self._transition_count = 0
        else:  # transition
            change = abs(conf - self._last_transition_conf)
            self._transition_count += 1
            if self._transition_count >= self.values["transition_repeat"] and change < self.values["change_threshold"]:
                status = "误判修正->未遮挡"  # 场景色恰落在渐变区
            else:
                in_transition = True
            self._last_transition_conf = conf

        clear = not blocked and not in_transition
        self.live = {"confidence": conf, "state": status}
        if ctx.preview_enabled:
            self.live["preview"] = _imaging.encode_preview(img)
        return {"blocked": blocked, "in_transition": in_transition,
                "clear": clear, "confidence": conf, "state": status}


# ==================== 多TC计数（单次检测）====================
@register
class TcCount(DataNode):
    """一次性检测当前 TC 数量。重试/缓存/冷却交由流程图用变量与条件表达。"""

    type_id = "game.tc_count"
    category = "游戏"
    title = "多TC计数"
    outputs = [
        data_out("count", DataType.NUMBER, label="数量",
                 help="当前选中的城镇中心个数。⚠靠识别“TC 面板”得到——必须先按键选中所有 TC、面板出来后才数得到。"
                      "本节点已【内置“按键后自动重试重截”】（见参数 重试次数/重试间隔）：按 H 后面板还没刷新出来时，"
                      "它会小步重截+重匹配，一识别到立即返回——所以【不需要】在它前面再放固定延时节点。"),
        data_out("ok", DataType.BOOL, label="成功",
                 help="是否成功识别到 TC（重试若干次仍没识别到 / 还没建 TC / 被遮挡 时为否）。"),
    ]
    params = [
        ParamSpec("icon_region", "多TC检测区域", "region", default=[444, 1212, 492, 1259],
                  help="TC 面板上显示数量的小图标区域（≥2 个 TC 时这里会有数字）。"),
        ParamSpec("single_region", "单TC预检测区域", "region", default=[300, 1140, 354, 1194],
                  help="先快速判断“是不是只有 1 个 TC”的区域，命中就直接返回 1、省去数字匹配。"),
        ParamSpec("single_template", "单TC模板", "template", default="",
                  help="单个 TC 时该区域的模板图。"),
        ParamSpec("numbered_templates", "数字模板(按序)", "templates", default=[],
                  help="tc_number_1.png, tc_number_2.png ... 顺序排列；序号N对应 1+N 个TC"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.7, minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("crop_size", "数字裁剪尺寸", "int", default=20, minimum=4, maximum=64),
        ParamSpec("early_exit", "提前退出阈值", "float", default=0.95, minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("retry_max", "重试次数", "int", default=8, minimum=0, maximum=30,
                  help="按“选所有TC键”后若没立刻识别到 TC 面板（UI 还在刷新），最多再重截+重匹配这么多次；"
                       "一旦识别到就立即返回，UI 快时几乎零等待。设 0=不重试（回到一次性检测）。"),
        ParamSpec("retry_interval", "重试间隔(秒)", "float", default=0.02, minimum=0.0, maximum=0.5, step=0.01,
                  help="每次重试之间等待多久（默认 0.02 秒）。越小越快越费 CPU；UI 刷新慢可调大。"
                       "最坏耗时 ≈ 重试次数 × 本间隔，仅在迟迟没识别到时才会用满。"),
    ]

    _crop_cache: dict = {}

    def _load_crop(self, path: str, size: int):
        key = (path, size)
        if key in self._crop_cache:
            return self._crop_cache[key]
        g = _imaging.load_gray(path)
        if g is None or g.shape[0] < size or g.shape[1] < size:
            self._crop_cache[key] = None
            return None
        crop = g[0:size, 0:size]
        self._crop_cache[key] = crop
        return crop

    def _detect_once(self, ctx, thr, fresh):
        """单次检测当前 TC 数量。fresh=True 时绕过本帧截图缓存、重新截屏（供重试用）。
        返回 (结果dict, 路径文字)。"""
        # 1) 单TC预检测
        single_tmpl = _imaging.load_gray(self.values["single_template"])
        if single_tmpl is not None:
            sg = _imaging.to_gray(ctx.capture_region(self.values["single_region"], fresh=fresh))
            if _imaging.best_match(sg, single_tmpl) >= thr:
                return {"count": 1, "ok": True}, "单TC"

        # 2) 多TC：阶段1 判断是否有 TC 图标
        numbered = self.values["numbered_templates"] or []
        icon_gray = _imaging.to_gray(ctx.capture_region(self.values["icon_region"], fresh=fresh))
        if not numbered or _imaging.best_match(icon_gray, _imaging.load_gray(numbered[0])) < thr:
            return {"count": 0, "ok": False}, "未检测到TC"

        # 3) 阶段2：左上角精确匹配数字
        size = self.values["crop_size"]
        if icon_gray.shape[0] < size or icon_gray.shape[1] < size:
            return {"count": 1, "ok": True}, "区域过小"
        crop = icon_gray[0:size, 0:size]
        best_n, best_c = 1, 0.0
        for i, path in enumerate(numbered, start=1):
            tmpl_crop = self._load_crop(path, size)
            if tmpl_crop is None:
                break
            import cv2
            res = cv2.matchTemplate(crop, tmpl_crop, cv2.TM_CCOEFF_NORMED)
            _, mv, _, _ = cv2.minMaxLoc(res)
            if mv > best_c:
                best_c, best_n = mv, i
                if mv >= self.values["early_exit"]:
                    break
        count = 1 + best_n
        return {"count": count, "ok": True}, f"多TC(n={best_n})"

    def evaluate(self, ctx, inputs):
        thr = self.values["threshold"]

        # 首次检测用本帧缓存（与同帧其它读取共享）；没识别到——多半是按 H 后 TC 面板还没刷新出来——
        # 就小步重试：等一小会、强制重截(fresh)、重匹配，直到识别到或用尽重试次数。识别到立即返回。
        out, path = self._detect_once(ctx, thr, fresh=False)
        tries = 1
        if not ctx.dry_run:   # 干跑(无游戏)不重试，避免 selfcheck 空等
            retry_max = int(self.values.get("retry_max", 0) or 0)
            interval = float(self.values.get("retry_interval", 0.0) or 0.0)
            while not out["ok"] and tries <= retry_max:
                if interval > 0:
                    time.sleep(interval)
                out, path = self._detect_once(ctx, thr, fresh=True)
                tries += 1

        self.live = {"count": out["count"], "path": path, "tries": tries}
        if ctx.preview_enabled:   # 预览 TC 图标区域（刚在 _detect_once 截过、走帧缓存，开销很小）
            self.live["preview"] = _imaging.encode_preview(ctx.capture_region(self.values["icon_region"]))
        return out


# ==================== 产能计算 ====================
@register
class ProduceCount(DataNode):
    """计算实际可生产数量，并把“用掉后剩余的空位/资源”结转给下一段。

    产量 = min(计划, 空位, 各资源//各自成本, 上限−当前)，取整且 ≥0；某成本 ≤0（或资源不接）视为“不看该资源”。
    【多段共享同一池子的关键】村民/乡骑/商人在同一帧依次生产时，人口（空位）三段共用、黄金乡骑与商人共用。
    若每段都读“没扣减过”的同一个空位/资源旧值，前段排了之后后段仍按旧值排 → 超额、被游戏静默拒绝
    （表现为“前一个单位在产时后一个怎么都不产”）。所以本节点除了算 count，还输出“剩余空位/剩余资源”，
    把它接到【下一段】对应输入，池子就会被逐段正确扣减。
    再配合“开关/已占用”输入：本段其实不会生产时（段关、或队列已在造该单位）产量记 0、预算原样透传给下一段。
    """

    type_id = "game.produce_count"
    category = "游戏"
    title = "产能计算"
    # 四种资源直接对应游戏里的【食物/木头/黄金/石头】；某资源不接、或其成本 ≤0 = 不看该资源。
    RES = ("food", "wood", "gold", "stone")
    RES_LABEL = {"food": "食物", "wood": "木头", "gold": "黄金", "stone": "石头"}
    inputs = [
        data_in("planned", DataType.NUMBER, label="计划数",
                help="本来想造多少（如 每TC数×TC个数）。最终产量不会超过它。"),
        data_in("available_slots", DataType.NUMBER, label="空位",
                help="人口空位（上限−当前）。不接=不受人口限制。多段串联时接【上一段】的“剩余空位”。"),
        data_in("food", DataType.NUMBER, label="食物",
                help="当前食物量。受 食物÷食物成本 限制。不接=不看食物。多段共用食物时接上一段“剩余食物”。"),
        data_in("gold", DataType.NUMBER, label="黄金",
                help="当前黄金量。受 黄金÷黄金成本 限制。不接=不看黄金。多段共用黄金时接上一段“剩余黄金”。"),
        data_in("wood", DataType.NUMBER, label="木头", advanced=True,
                help="当前木头量。受 木头÷木头成本 限制。不接=不看木头。"),
        data_in("stone", DataType.NUMBER, label="石头", advanced=True,
                help="当前石头量。受 石头÷石头成本 限制。不接=不看石头。"),
        data_in("food_cost", DataType.NUMBER, label="食物成本",
                help="造 1 个消耗多少食物。接了就用它（覆盖下方“食物单位成本”参数）——可由一个常量同时喂给本节点和门控比较、放面板按国家调。≤0=不看食物。"),
        data_in("gold_cost", DataType.NUMBER, label="黄金成本",
                help="造 1 个消耗多少黄金（覆盖参数“黄金单位成本”）。≤0 或不接=不看黄金。"),
        data_in("wood_cost", DataType.NUMBER, label="木头成本", advanced=True,
                help="造 1 个消耗多少木头（覆盖参数“木头单位成本”）。≤0 或不接=不看木头。"),
        data_in("stone_cost", DataType.NUMBER, label="石头成本", advanced=True,
                help="造 1 个消耗多少石头（覆盖参数“石头单位成本”）。≤0 或不接=不看石头。"),
        data_in("switch", DataType.BOOL, label="开关", advanced=True,
                help="本段是否启用（接段开关）。为否则产量=0、把空位/资源预算原样传给下一段。不接=启用。"),
        data_in("busy", DataType.BOOL, label="已占用", advanced=True,
                help="该单位是否已在队列里生产（接队列检测的“命中”）。为真则产量=0、预算原样透传。不接=未占用。"),
        data_in("current_count", DataType.NUMBER, label="当前数量", advanced=True,
                help="已有数量，配合“数量上限”用（产量不超过 上限−当前）。一般不接。"),
    ]
    outputs = [
        data_out("count", DataType.NUMBER, label="数量",
                 help="实际可生产数 = min(计划, 空位, 各资源÷各自成本, 上限−当前)，取整且 ≥0。接到“按键”的“数量”即排这么多次。"),
        data_out("slots_left", DataType.NUMBER, label="剩余空位",
                 help="本段用掉后剩下的人口空位。接到【下一段】产能计算的“空位”，多段共享人口池而不重复占用。不接=忽略。"),
        data_out("food_left", DataType.NUMBER, label="剩余食物",
                 help="食物扣掉本段消耗后的剩余。多段共用食物（如村民和乡骑都吃食物）时接到下一段“食物”。"),
        data_out("gold_left", DataType.NUMBER, label="剩余黄金",
                 help="黄金扣掉本段消耗后的剩余。多段共用黄金（如乡骑和商人都吃黄金）时接到下一段“黄金”。"),
        data_out("wood_left", DataType.NUMBER, label="剩余木头", advanced=True, help="木头扣掉本段消耗后的剩余。"),
        data_out("stone_left", DataType.NUMBER, label="剩余石头", advanced=True, help="石头扣掉本段消耗后的剩余。"),
    ]
    params = [
        ParamSpec("food_cost", "食物单位成本", "float", default=0.0, minimum=0.0,
                  help="造 1 个要花多少食物（村民≈50）。被“食物成本”输入覆盖。0=不看食物。"),
        ParamSpec("gold_cost", "黄金单位成本", "float", default=0.0, minimum=0.0,
                  help="造 1 个要花多少黄金。被“黄金成本”输入覆盖。0=不看黄金。"),
        ParamSpec("wood_cost", "木头单位成本", "float", default=0.0, minimum=0.0,
                  help="造 1 个要花多少木头。被“木头成本”输入覆盖。0=不看木头。", advanced=True),
        ParamSpec("stone_cost", "石头单位成本", "float", default=0.0, minimum=0.0,
                  help="造 1 个要花多少石头。被“石头成本”输入覆盖。0=不看石头。", advanced=True),
        ParamSpec("cap", "数量上限", "int", default=-1, help="-1 表示不启用上限（需配合“当前数量”输入）。", advanced=True),
    ]

    def evaluate(self, ctx, inputs):
        planned = inputs.get("planned") or 0
        sw = inputs.get("switch")
        active = (True if sw is None else bool(sw)) and not bool(inputs.get("busy"))

        slots = inputs.get("available_slots")
        amts = {nm: inputs.get(nm) for nm in self.RES}
        costs = {}
        for nm in self.RES:
            c = inputs.get(nm + "_cost")          # 接了“X成本”输入就用它，否则回落到参数
            costs[nm] = self.values[nm + "_cost"] if c is None else c

        count = planned
        if slots is not None:
            count = min(count, slots)
        for nm in self.RES:
            a, c = amts[nm], costs[nm]
            if a is not None and c and c > 0:
                count = min(count, int(a // c))
        cap = self.values["cap"]
        current = inputs.get("current_count")
        if cap is not None and cap >= 0 and current is not None:
            count = min(count, cap - current)
        count = max(0, int(count))
        if not active:               # 本段不该生产：产量0，预算原样透传（不占用人口/资源池）
            count = 0

        out = {"count": count, "slots_left": None if slots is None else slots - count}
        for nm in self.RES:
            a, c = amts[nm], costs[nm]
            out[nm + "_left"] = None if a is None else a - (count * c if (c and c > 0) else 0)
        self.live = {"count": count}
        return out

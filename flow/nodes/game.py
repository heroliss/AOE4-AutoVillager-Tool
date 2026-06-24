"""
游戏专用的"重逻辑"节点：三态遮挡检测、选中建筑计数（原“多TC计数”，已通用化）、产能计算。

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
    """检测某区域是否被 UI 遮挡，输出 完全遮挡 / 未遮挡 / 渐变中 三态。
    判据：与“遮挡模板”的匹配分定三态分界；落在中间“灰区”时，再用【帧间像素变化】分辨
    是真·渐入渐出动画（画面在动→渐变中、跳过本帧）还是场景色恰好落在灰区（静止→当未遮挡可读）。
    比旧的“离散状态连续 N 次才算稳定”更贴近直觉：画面没动就不会被误报“不稳定”。"""

    type_id = "game.occlusion"
    category = "游戏"
    title = "三态遮挡检测"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("blocked", DataType.BOOL, label="遮挡",
                 help="那块区域被完全盖住（如打开了某个面板）。此时识别不可靠，流程应跳过本帧。"),
        data_out("in_transition", DataType.BOOL, label="渐变中",
                 help="匹配分落在“灰区”且画面正在变化（UI 渐入/渐出动画）。此时识别会误判，流程应跳过本帧。"),
        data_out("clear", DataType.BOOL, label="未遮挡",
                 help="区域干净、可放心识别（= 既非遮挡也非渐变）。三态里的“正常”态。"),
        data_out("confidence", DataType.NUMBER, label="置信度",
                 help="与“遮挡模板”的匹配分(0~1)：越高越像被遮挡。≥完全遮挡阈值=遮挡、<渐变下限阈值=未遮挡；"
                      "落在中间“灰区”时再结合“画面变化”分辨渐变/未遮挡。"),
        data_out("state", DataType.STRING, label="状态",
                 help="给人看的状态文字：完全遮挡 / 未遮挡 / 渐变中，灰区里可能带“(画面在动 Δ…)”或“(灰区静止)”。仅展示、不参与判断。"),
    ]
    params = [
        ParamSpec("region", "检测区域", "region", default=[265, 950, 280, 970],
                  help="要监测是否被遮挡的小块区域（一般取生产队列那一块）。"),
        ParamSpec("template", "遮挡模板", "template", default="",
                  help="该区域“被遮挡时”长什么样的模板图，用来比对。"),
        ParamSpec("match_threshold", "完全遮挡阈值", "float", default=0.7, minimum=0.0, maximum=1.0, step=0.01,
                  help="匹配分 ≥ 它 → 判为“完全遮挡”。"),
        ParamSpec("transition_threshold", "渐变下限阈值", "float", default=0.1, minimum=0.0, maximum=1.0, step=0.01,
                  help="匹配分 < 它 → 直接判“未遮挡”（最可靠：长得完全不像遮挡物，生产动画等也落这档、不受运动误伤）；"
                       "介于本阈值与“完全遮挡阈值”之间＝“灰区/渐变区”，此时再看“画面变化阈值”分辨真渐变还是场景巧合。"),
        ParamSpec("motion_eps", "画面变化阈值", "float", default=0.02, minimum=0.0, maximum=1.0, step=0.005,
                  help="相邻两帧、这块区域的平均像素变化（0~1，≈变化灰度÷255）。【仅在灰区里】用它分辨：\n"
                       "≥它＝画面在动 → 判“渐变中”（渐入/渐出动画，跳过本帧）；<它＝画面静止 → 当“未遮挡”可读。\n"
                       "静止 UI 通常≈0，渐变动画时远大于它。误报渐变就调大，漏判慢动画就调小。"),
        ParamSpec("hysteresis", "遮挡判定滞回", "float", default=0.05, minimum=0.0, maximum=0.5, step=0.01, advanced=True,
                  help="防“遮挡↔未遮挡”在阈值线上反复横跳：一旦判为遮挡，要等匹配分掉到（完全遮挡阈值−本值）才解除。0=不滞回。"),
    ]

    def __init__(self):
        super().__init__()
        self._prev_gray = None     # 上一帧本区域灰度图（副本），用于帧间差分＝“画面有没有动”的直接度量
        self._was_blocked = False  # 上一帧是否判为遮挡，用于遮挡阈值的滞回消抖

    def _frame_motion(self, gray):
        """与上一帧本区域做差，返回平均像素变化(0~1)；首帧/尺寸不一致返回 None。"""
        prev = self._prev_gray
        if prev is None or gray is None or prev.shape != gray.shape:
            return None
        import cv2
        import numpy as np
        return float(np.mean(cv2.absdiff(gray, prev))) / 255.0

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

        # 帧间运动量：本区域相邻两帧的平均像素变化(0~1)。这是“画面在不在动”的直接度量，
        # 取代旧的“离散三态连续相同 N 次”——后者在阈值边界会把静止画面误判成“在变”而永远(不稳定)。
        motion = self._frame_motion(gray)              # 首帧无上一帧 → None
        self._prev_gray = None if gray is None else gray.copy()

        m_thr = self.values["match_threshold"]
        t_thr = self.values["transition_threshold"]
        eps = self.values["motion_eps"]
        hyst = self.values["hysteresis"]

        # 遮挡判定带滞回：未遮挡→遮挡需 conf≥阈值；已遮挡→需掉到 阈值−滞回 才解除，防边界横跳。
        enter = m_thr if not self._was_blocked else (m_thr - hyst)
        is_blocked = conf >= enter
        self._was_blocked = is_blocked

        # 画面在动吗？首帧(motion=None)无从比较 → 视为静止（信任本帧），正是“刚进游戏没动就别报不稳定”。
        moving = motion is not None and motion >= eps

        blocked = in_transition = clear = False
        if is_blocked:
            blocked = True                  # 匹配分高＝面板确实盖住，决断，不看运动
            status = "完全遮挡"
        elif conf < t_thr:
            clear = True                    # 长得完全不像遮挡物＝干净，决断（生产动画等也落这里，故不会被运动误伤）
            status = "未遮挡"
        elif moving:
            in_transition = True            # 灰区+画面在动＝真·渐入/渐出动画 → 跳过本帧
            status = f"渐变中(画面在动 Δ{motion:.3f})"
        else:
            clear = True                    # 灰区+画面静止＝场景色恰落在灰区(非遮挡)，可放心识别（旧“误判修正”的直判版）
            status = "未遮挡(灰区静止)"

        self.live = {"confidence": conf, "state": status,
                     "motion": None if motion is None else round(motion, 4)}
        if ctx.preview_enabled:
            self.live["preview"] = _imaging.encode_preview(img)
            m_txt = "—" if motion is None else f"{motion:.3f}"
            self.live["preview_label"] = f"{status}  match {conf:.2f}  Δ{m_txt}"
        return {"blocked": blocked, "in_transition": in_transition,
                "clear": clear, "confidence": conf, "state": status}


# ==================== 选中建筑计数（单次检测）====================
@register
class BuildingCount(DataNode):
    """一次性检测当前【选中的某类建筑】数量（城镇中心 / 市场 / …）。
    算法与建筑无关：单个建筑预检测 + 多个时数字模板匹配，按键后自动重试重截。
    重试/缓存/冷却交由流程图用变量与条件表达。"""

    type_id = "game.building_count"
    aliases = ("game.tc_count",)   # 旧名「多TC计数」，兼容已保存的流程
    category = "游戏"
    title = "选中建筑计数"
    outputs = [
        data_out("count", DataType.NUMBER, label="数量",
                 help="当前选中的该类建筑个数（城镇中心/市场/…，由“建筑名称”指明）。"
                      "⚠靠识别“数量面板”得到——必须先按“全选该建筑”的键、面板出来后才数得到。"
                      "本节点已【内置“按键后自动重试重截”】（见参数 重试次数/重试间隔）：按键后面板还没刷新出来时，"
                      "它会小步重截+重匹配，一识别到立即返回——所以【不需要】在它前面再放固定延时节点。"),
        data_out("ok", DataType.BOOL, label="成功",
                 help="是否成功识别到该建筑（重试若干次仍没识别到 / 还没建该建筑 / 被遮挡 时为否）。"
                      "可直接接到“出兵段”的开关上：识别不到（如没有市场）就不进该段。"),
        data_out("conf", DataType.NUMBER, label="置信度", advanced=True,
                 help="本次“决定结果的那一档”的模板匹配分数(0~1)，用于调试/调参：没数到时看它有多接近“匹配阈值”，"
                      "据此决定是把阈值调低还是重截模板。开「🖼预览」还能看到 single/count 两块各自的分数。"),
    ]
    params = [
        ParamSpec("building_name", "建筑名称", "str", default="城镇中心",
                  help="仅用于日志/预览显示，便于区分这是数哪种建筑（如 城镇中心 / 市场）。不影响识别。"),
        ParamSpec("icon_region", "数量图标区域", "region", default=[444, 1212, 492, 1259],
                  help="面板上显示数量的小图标区域（选中 ≥2 个该建筑时这里会有数字）。"),
        ParamSpec("single_region", "单个预检测区域", "region", default=[300, 1140, 354, 1194],
                  help="先快速判断“是不是只有 1 个”的区域，命中就直接返回 1、省去数字匹配。"),
        ParamSpec("single_template", "单个模板", "template", default="",
                  help="只有 1 个该建筑时、上面那块区域的模板图。"),
        ParamSpec("numbered_templates", "数字模板(按序)", "templates", default=[],
                  help="number_1.png, number_2.png ... 顺序排列；序号N对应 1+N 个建筑（如 tc_number_1 / market_number_1 ...）"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.8, minimum=0.0, maximum=1.0, step=0.01,
                  help="匹配分数≥它才算命中。启用“滑动余量”后真匹配普遍 0.95+，故默认提到 0.8——能更稳地把“无此建筑”判成 0（少误判）。"
                       "没数到又看着挺像就调低些；总误判就调高。开「🖼预览」看实时分数再定。"),
        ParamSpec("slide_margin", "滑动余量(px)", "int", default=6, minimum=0, maximum=40,
                  help="匹配时把检测区域【向四周各扩大】这么多像素，让模板能在里面小幅滑动找到最佳对齐位置——"
                       "这能极大改善“截图几乎一样、分数却很低”的问题（同尺寸硬匹配对 1~2px 错位极敏感）。"
                       "设 0=不留余量(旧行为)。一般 4~8 即可。"),
        ParamSpec("retry_max", "重试次数", "int", default=8, minimum=0, maximum=30,
                  help="按“选所有TC键”后若没立刻识别到 TC 面板（UI 还在刷新），最多再重截+重匹配这么多次；"
                       "一旦识别到就立即返回，UI 快时几乎零等待。设 0=不重试（回到一次性检测）。"),
        ParamSpec("retry_interval", "重试间隔(秒)", "float", default=0.02, minimum=0.0, maximum=0.5, step=0.01,
                  help="每次重试之间等待多久（默认 0.02 秒）。越小越快越费 CPU；UI 刷新慢可调大。"
                       "最坏耗时 ≈ 重试次数 × 本间隔，仅在迟迟没识别到时才会用满。"),
    ]

    @staticmethod
    def _f(v):
        return "—" if v is None else f"{v:.2f}"

    def _grab(self, ctx, region, margin, fresh):
        """截取检测区域，并【向四周各扩大 margin 像素】——给模板留出滑动余量，
        让 matchTemplate 能在区域内小幅平移、找到最佳对齐，从而对 1~2px 错位稳健（关键修复）。"""
        l, t, r, b = region
        return _imaging.to_gray(ctx.capture_region([l - margin, t - margin, r + margin, b + margin], fresh=fresh))

    def _detect_once(self, ctx, thr, fresh):
        """单次检测当前选中建筑数量。fresh=True 时绕过本帧截图缓存、重新截屏（供重试用）。
        返回 (结果dict, 路径文字)。顺带把各阶段匹配置信度记到 self._dbg，供预览/调试显示。

        匹配策略：在【略放大的搜索区】里滑动整张模板取最高分（不再用“同尺寸硬匹配 + 左上角裁剪”那套对
        错位极敏感的老办法）。数量识别 = 让每个数字模板各自滑动匹配、分最高者即当前数量。"""
        name = self.values.get("building_name") or "建筑"
        thr = float(thr)
        margin = int(self.values.get("slide_margin", 6) or 0)
        dbg = {"single": None, "icon": None, "decided": 0.0, "thr": thr}
        self._dbg = dbg

        # 1) 单个预检测：单建筑模板在(放大的)单建筑区域里滑动匹配
        single_tmpl = _imaging.load_gray(self.values["single_template"])
        if single_tmpl is not None:
            ss = _imaging.best_match(self._grab(ctx, self.values["single_region"], margin, fresh), single_tmpl)
            dbg["single"] = ss
            if ss >= thr:
                dbg["decided"] = ss
                return {"count": 1, "ok": True}, f"单{name}(single {ss:.2f}≥{thr:.2f})"

        # 2) 数量：每个数字模板在(放大的)数量区域里滑动匹配，取最高分者即当前数量
        numbered = self.values["numbered_templates"] or []
        if not numbered:
            dbg["decided"] = dbg["single"] or 0.0
            return {"count": 0, "ok": False}, f"未检测到{name}(无数字模板)"
        search = self._grab(ctx, self.values["icon_region"], margin, fresh)
        best_n, best_c = 0, 0.0
        for i, path in enumerate(numbered, start=1):
            sc = _imaging.best_match(search, _imaging.load_gray(path))
            if sc > best_c:
                best_c, best_n = sc, i
        dbg["icon"] = best_c
        dbg["decided"] = max(dbg["single"] or 0.0, best_c)
        if best_c < thr:
            return ({"count": 0, "ok": False},
                    f"未检测到{name}(single {self._f(dbg['single'])} / count {self._f(best_c)} 均 < {thr:.2f})")
        count = 1 + best_n
        return {"count": count, "ok": True}, f"{name}×{count}(count {best_c:.2f})"

    def evaluate(self, ctx, inputs):
        thr = self.values["threshold"]

        # 首次检测用本帧缓存（与同帧其它读取共享）；没识别到——多半是按全选键后面板还没刷新出来——
        # 就小步重试：等一小会、强制重截(fresh)、重匹配，直到识别到或用尽重试次数。识别到立即返回。
        out, path = self._detect_once(ctx, thr, fresh=False)
        tries = 1
        if not out["ok"] and not ctx.dry_run:   # 干跑(无游戏)不重试，避免 selfcheck 空等
            retry_max = int(self.values.get("retry_max", 0) or 0)
            interval = float(self.values.get("retry_interval", 0.0) or 0.0)
            while not out["ok"] and tries <= retry_max:
                # 先【立刻】重截一张重匹配：按键后面板通常已经刷新，省掉之前“先睡 interval 再截”那段白等；
                # 只有这张仍没识别到、且还有重试次数时，才睡一小会儿等下一帧 UI。
                out, path = self._detect_once(ctx, thr, fresh=True)
                tries += 1
                if out["ok"] or tries > retry_max:
                    break
                if interval > 0:
                    time.sleep(interval)

        dbg = getattr(self, "_dbg", {}) or {}
        out["conf"] = round(float(dbg.get("decided") or 0.0), 3)   # 暴露“决定本次结果的那一档”置信度，便于调参/排查
        self.live = {"count": out["count"], "path": path, "tries": tries,
                     "single": dbg.get("single"), "count_conf": dbg.get("icon")}
        if ctx.preview_enabled:
            # 左右并排预览“单建筑预检测区域(1x)”和“数量图标区域(Nx)”——这两块各自独立；
            # 置信度走 preview_label(编辑器里以清晰文字显示，不再烤进图里被裁切)。单个没数到多半是 1x 那块没对准/分低。
            items = []
            sr = self.values.get("single_region")
            if sr:
                items.append(("1x", ctx.capture_region(sr)))
            items.append(("Nx", ctx.capture_region(self.values["icon_region"])))
            self.live["preview"] = _imaging.encode_preview_stack(items)
            self.live["preview_label"] = (f"single {self._f(dbg.get('single'))} · "
                                          f"count {self._f(dbg.get('icon'))} (阈值{self._f(thr)})")
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

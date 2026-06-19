"""
游戏专用的"重逻辑"节点：三态遮挡检测、多TC计数、产能计算。

这三者算法内聚（尤其遮挡检测自带跨帧状态机），拆成连线反而更难懂，
因此各自封装为单个节点，内部沿用既有 villager_training_detector / tc_counter 的算法，
但区域/模板/阈值等参数全部来自节点自身。
"""
from __future__ import annotations

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
        data_out("blocked", DataType.BOOL, label="遮挡"),
        data_out("in_transition", DataType.BOOL, label="渐变中"),
        data_out("clear", DataType.BOOL, label="未遮挡"),
        data_out("confidence", DataType.NUMBER, label="置信度"),
        data_out("state", DataType.STRING, label="状态"),
    ]
    params = [
        ParamSpec("region", "检测区域", "region", default=[265, 950, 280, 970]),
        ParamSpec("template", "遮挡模板", "template", default=""),
        ParamSpec("match_threshold", "完全遮挡阈值", "float", default=0.7, minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("transition_threshold", "渐变下限阈值", "float", default=0.1, minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("stable_threshold", "稳定所需次数", "int", default=2, minimum=1, maximum=10),
        ParamSpec("transition_repeat", "渐变误判次数", "int", default=3, minimum=1, maximum=10),
        ParamSpec("change_threshold", "渐变误判变化阈", "float", default=0.05, minimum=0.0, maximum=1.0, step=0.01),
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
        return {"blocked": blocked, "in_transition": in_transition,
                "clear": clear, "confidence": conf, "state": status}


# ==================== 多TC计数（单次检测）====================
@register
class TcCount(DataNode):
    """一次性检测当前 TC 数量。重试/缓存/冷却交由流程图用变量与条件表达。"""

    type_id = "game.tc_count"
    category = "游戏"
    title = "多TC计数"
    outputs = [data_out("count", DataType.NUMBER, label="数量"), data_out("ok", DataType.BOOL, label="成功")]
    params = [
        ParamSpec("icon_region", "多TC检测区域", "region", default=[444, 1212, 492, 1259]),
        ParamSpec("single_region", "单TC预检测区域", "region", default=[300, 1140, 354, 1194]),
        ParamSpec("single_template", "单TC模板", "template", default=""),
        ParamSpec("numbered_templates", "数字模板(按序)", "templates", default=[],
                  help="tc_number_1.png, tc_number_2.png ... 顺序排列；序号N对应 1+N 个TC"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.7, minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("crop_size", "数字裁剪尺寸", "int", default=20, minimum=4, maximum=64),
        ParamSpec("early_exit", "提前退出阈值", "float", default=0.95, minimum=0.0, maximum=1.0, step=0.01),
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

    def evaluate(self, ctx, inputs):
        thr = self.values["threshold"]

        # 1) 单TC预检测
        single_tmpl = _imaging.load_gray(self.values["single_template"])
        if single_tmpl is not None:
            sg = _imaging.to_gray(ctx.capture_region(self.values["single_region"]))
            if _imaging.best_match(sg, single_tmpl) >= thr:
                self.live = {"count": 1, "path": "单TC"}
                return {"count": 1, "ok": True}

        # 2) 多TC：阶段1 判断是否有 TC 图标
        numbered = self.values["numbered_templates"] or []
        icon_gray = _imaging.to_gray(ctx.capture_region(self.values["icon_region"]))
        if not numbered or _imaging.best_match(icon_gray, _imaging.load_gray(numbered[0])) < thr:
            self.live = {"count": 0, "path": "未检测到TC"}
            return {"count": 0, "ok": False}

        # 3) 阶段2：左上角精确匹配数字
        size = self.values["crop_size"]
        if icon_gray.shape[0] < size or icon_gray.shape[1] < size:
            self.live = {"count": 1, "path": "区域过小"}
            return {"count": 1, "ok": True}
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
        self.live = {"count": count, "path": f"多TC(n={best_n})"}
        return {"count": count, "ok": True}


# ==================== 产能计算 ====================
@register
class ProduceCount(DataNode):
    """计算实际可生产数量 = min(计划, 空位, 资源//成本, 上限-当前)，结果 >=0 取整。"""

    type_id = "game.produce_count"
    category = "游戏"
    title = "产能计算"
    inputs = [
        data_in("planned", DataType.NUMBER, label="计划数"),
        data_in("available_slots", DataType.NUMBER, label="空位"),
        data_in("resource", DataType.NUMBER, label="资源"),
        data_in("current_count", DataType.NUMBER, label="当前数量"),
    ]
    outputs = [data_out("count", DataType.NUMBER, label="数量")]
    params = [
        ParamSpec("cost_per_unit", "单位资源成本", "float", default=50.0, minimum=0.0),
        ParamSpec("cap", "数量上限", "int", default=-1, help="-1 表示不启用上限"),
    ]

    def evaluate(self, ctx, inputs):
        count = inputs.get("planned") or 0
        slots = inputs.get("available_slots")
        if slots is not None:
            count = min(count, slots)
        resource = inputs.get("resource")
        cost = self.values["cost_per_unit"]
        if resource is not None and cost > 0:
            count = min(count, int(resource // cost))
        cap = self.values["cap"]
        current = inputs.get("current_count")
        if cap is not None and cap >= 0 and current is not None:
            count = min(count, cap - current)
        count = max(0, int(count))
        self.live = {"count": count}
        return {"count": count}

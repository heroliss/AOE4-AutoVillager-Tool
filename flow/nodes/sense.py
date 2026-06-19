"""
感知节点：截图、模板匹配（单/多模板）、OCR 数字、像素颜色、窗口检测。

这些节点把原 screenshot_util / ocr_util / game_detector 的能力拆成可配置、可连线的
单元；区域/模板/阈值/缩放等参数全部落在节点上。
"""
from __future__ import annotations

import re
from typing import Any

from ..core import (
    DataNode, ParamSpec, DataType,
    data_in, data_out, register,
)
from . import _imaging


# ==================== 截图 ====================
@register
class ScreenCapture(DataNode):
    type_id = "sense.capture"
    category = "感知"
    title = "截图"
    outputs = [data_out("image", DataType.IMAGE, label="图像")]
    params = [ParamSpec("region", "区域", "region", default=[0, 0, 100, 100],
                        help="(left, top, right, bottom)；若已预取整屏则自动切片复用")]

    def evaluate(self, ctx, inputs):
        return {"image": ctx.capture_region(self.values["region"])}


# ==================== 像素颜色 ====================
@register
class PixelColor(DataNode):
    type_id = "sense.pixel_color"
    category = "感知"
    title = "像素颜色"
    outputs = [data_out("match", DataType.BOOL, label="匹配"), data_out("color", DataType.COLOR, label="颜色")]
    params = [
        ParamSpec("point", "坐标", "point", default=[0, 0]),
        ParamSpec("color", "目标颜色", "color", default=[0, 0, 0]),
        ParamSpec("tolerance", "容差", "int", default=0, minimum=0, maximum=255),
    ]

    def evaluate(self, ctx, inputs):
        x, y = self.values["point"]
        got = ctx.get_pixel(int(x), int(y))
        exp = tuple(self.values["color"])
        tol = self.values["tolerance"]
        match = all(abs(g - e) <= tol for g, e in zip(got, exp))
        self.live = {"color": got, "match": match}
        return {"match": match, "color": got}


# ==================== 窗口检测 ====================
@register
class WindowCheck(DataNode):
    type_id = "sense.window_check"
    category = "感知"
    title = "游戏窗口检测"
    outputs = [data_out("in_game", DataType.BOOL, label="在游戏中"), data_out("active", DataType.BOOL, label="窗口激活")]
    params = [
        ParamSpec("keywords", "窗口标题关键词", "str",
                  default="Age of Empires IV,帝国时代IV,帝国时代4",
                  help="逗号分隔，命中任一即视为游戏窗口"),
        ParamSpec("pixel", "检测像素坐标", "point", default=[2526, 1405]),
        ParamSpec("color", "检测像素颜色", "color", default=[26, 32, 46]),
        ParamSpec("tolerance", "颜色容差", "int", default=0, minimum=0, maximum=255),
    ]

    def evaluate(self, ctx, inputs):
        title = ctx.foreground_title().lower()
        kws = [k.strip().lower() for k in self.values["keywords"].split(",") if k.strip()]
        active = any(k in title for k in kws)
        in_game = False
        if active:
            x, y = self.values["pixel"]
            got = ctx.get_pixel(int(x), int(y))
            exp = tuple(self.values["color"])
            tol = self.values["tolerance"]
            in_game = all(abs(g - e) <= tol for g, e in zip(got, exp))
        self.live = {"active": active, "in_game": in_game}
        return {"in_game": in_game, "active": active}


# ==================== 模板匹配（单/多模板）====================
@register
class TemplateMatch(DataNode):
    type_id = "sense.template_match"
    category = "感知"
    title = "模板匹配"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("found", DataType.BOOL, label="命中"),
        data_out("confidence", DataType.NUMBER, label="置信度"),
        data_out("which", DataType.NUMBER, label="命中序号"),     # 命中的模板序号（0 起），用于多模板
        data_out("in_transition", DataType.BOOL, label="渐变中"),  # 半透明UI渐入渐出（需开启 transition_guard）
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 100],
                  help="未连入 image 时，按此区域自行截图"),
        ParamSpec("templates", "模板图片", "templates", default=[],
                  help="一个或多个模板路径；任一命中即 found=真，输出最高置信度"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.6,
                  minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("transition_guard", "半透明UI抑制", "bool", default=False,
                  help="开启后用最近置信度变化模式识别UI渐入渐出，期间 in_transition=真、found=假，"
                       "避免在UI动画时误判（移植自旧 villager_training_detector 的策略1/2/3）"),
    ]

    def __init__(self):
        super().__init__()
        from collections import deque
        self._recent = deque(maxlen=5)  # 最近置信度历史，用于半透明UI检测

    def evaluate(self, ctx, inputs):
        img = inputs.get("image")
        if img is None:
            img = ctx.capture_region(self.values["region"])
        gray = _imaging.to_gray(img)

        best_conf, best_idx = 0.0, -1
        for i, path in enumerate(self.values["templates"] or []):
            conf = _imaging.best_match(gray, _imaging.load_gray(path))
            if conf > best_conf:
                best_conf, best_idx = conf, i
        found = best_conf >= self.values["threshold"]

        in_transition = False
        if self.values["transition_guard"]:
            in_transition = self._detect_transition(best_conf)
            if in_transition:
                found = False

        self.live = {"confidence": best_conf, "found": found,
                     "which": best_idx, "in_transition": in_transition}
        return {"found": found, "confidence": best_conf,
                "which": best_idx, "in_transition": in_transition}

    def _detect_transition(self, conf: float) -> bool:
        """三策略识别 UI 渐入渐出动画（移植自旧实现）。"""
        self._recent.append(conf)
        if len(self._recent) < 3:
            return False
        lo, hi = min(self._recent), max(self._recent)
        rng = hi - lo
        # 策略1：中等置信度 + 快速变化
        if 0.3 <= conf < 0.65 and rng > 0.1:
            return True
        # 策略2：置信度从高位突然下降
        if conf < 0.5 and hi > 0.6 and rng > 0.2:
            return True
        # 策略3：连续下降的尾声
        if conf < 0.4:
            last3 = list(self._recent)[-3:]
            declining = all(last3[i + 1] <= last3[i] + 0.05 for i in range(len(last3) - 1))
            if declining and (last3[0] - last3[-1]) > 0.1:
                return True
        return False


# ==================== OCR 数字 ====================
@register
class OcrNumber(DataNode):
    type_id = "sense.ocr_number"
    category = "感知"
    title = "OCR数字"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("value", DataType.NUMBER, label="数值"),
        data_out("value2", DataType.NUMBER, label="数值2"),  # 第二个捕获组（如人口 当前/上限）
        data_out("ok", DataType.BOOL, label="成功"),
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 40],
                  help="要识别数字的屏幕区域（左,上,右,下）。用区域框选工具更直观"),
        ParamSpec("scale", "缩放比例", "float", default=1.0, minimum=0.1, maximum=4.0, step=0.1,
                  help="识别前把截图放大的倍数；小字放大到 2~3 倍通常更准"),
        ParamSpec("allowlist", "字符白名单", "str", default="0123456789/\\|OolIsS ",
                  help="只允许识别这些字符，能显著提高准确率（数字一般用 0-9 加常见混淆字符）"),
        ParamSpec("regex", "提取正则", "str", default=r"(\d+)",
                  help="第1组->value，第2组->value2；如人口用 (\\d+)[/\\\\|](\\d+)"),
        ParamSpec("aggregate", "多数字聚合", "enum", default="first",
                  choices=["first", "sum"],
                  help="first=取正则首个匹配；sum=所有数字求和（村民总数用）"),
    ]

    # OCR 常见误识别纠正（O->0 等）
    _FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "s": "5"})

    def evaluate(self, ctx, inputs):
        img = inputs.get("image")
        if img is None:
            img = ctx.capture_region(self.values["region"])
        img = _imaging.to_rgb(img)

        scale = self.values["scale"]
        if scale != 1.0:
            import cv2
            h, w = img.shape[:2]
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))

        allow = self.values["allowlist"] or None
        results = ctx.ocr(img, allowlist=allow, detail=0)
        raw = " ".join(results).strip()
        cleaned = raw.translate(self._FIX)

        if self.values["aggregate"] == "sum":
            nums = [int(n) for n in re.findall(r"\d+", cleaned.replace(" ", ""))]
            total = sum(nums)
            self.live = {"raw": raw, "value": total}
            return {"value": total, "value2": None, "ok": bool(nums)}

        m = re.search(self.values["regex"], cleaned.replace(" ", ""))
        if not m:
            self.live = {"raw": raw, "value": None}
            return {"value": None, "value2": None, "ok": False}
        v1 = int(m.group(1))
        v2 = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
        self.live = {"raw": raw, "value": v1, "value2": v2}
        return {"value": v1, "value2": v2, "ok": True}

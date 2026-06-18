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
    outputs = [data_out("image", DataType.IMAGE)]
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
    outputs = [data_out("match", DataType.BOOL), data_out("color", DataType.COLOR)]
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
    outputs = [data_out("in_game", DataType.BOOL), data_out("active", DataType.BOOL)]
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
    inputs = [data_in("image", DataType.IMAGE)]
    outputs = [
        data_out("found", DataType.BOOL),
        data_out("confidence", DataType.NUMBER),
        data_out("which", DataType.NUMBER),  # 命中的模板序号（0 起），用于多模板
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 100],
                  help="未连入 image 时，按此区域自行截图"),
        ParamSpec("templates", "模板图片", "templates", default=[],
                  help="一个或多个模板路径；任一命中即 found=真，输出最高置信度"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.6,
                  minimum=0.0, maximum=1.0, step=0.01),
    ]

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
        self.live = {"confidence": best_conf, "found": found, "which": best_idx}
        return {"found": found, "confidence": best_conf, "which": best_idx}


# ==================== OCR 数字 ====================
@register
class OcrNumber(DataNode):
    type_id = "sense.ocr_number"
    category = "感知"
    title = "OCR数字"
    inputs = [data_in("image", DataType.IMAGE)]
    outputs = [
        data_out("value", DataType.NUMBER),
        data_out("value2", DataType.NUMBER),  # 第二个捕获组（如人口 当前/上限）
        data_out("ok", DataType.BOOL),
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 40]),
        ParamSpec("scale", "缩放比例", "float", default=1.0, minimum=0.1, maximum=4.0, step=0.1),
        ParamSpec("allowlist", "字符白名单", "str", default="0123456789/\\|OolIsS "),
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

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
    outputs = [
        data_out("match", DataType.BOOL, label="匹配",
                 help="主要输出：该点颜色是否与「目标颜色」相符（在容差内）。"),
        data_out("color", DataType.COLOR, label="颜色", advanced=True,
                 help="进阶：该点的实际颜色(RGB)，用于调试/取色，一般不接。"),
    ]
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
    outputs = [
        data_out("active", DataType.BOOL, label="窗口激活",
                 help="前台窗口标题是否像游戏（标题包含任一关键词）。"),
        data_out("pixel_ok", DataType.BOOL, label="像素点检测通过",
                 help="检测像素点的颜色是否符合（只在游戏对局内该处才是这个颜色）。"
                      "通常把它和「窗口激活」一起接到「与」节点，再接「分支」，作为“确实在游戏中”的判据。"),
    ]
    params = [
        ParamSpec("keywords", "窗口标题关键词", "str",
                  default="Age of Empires IV,帝国时代IV,帝国时代4",
                  help="逗号分隔；当前窗口标题（忽略大小写）只要【包含】其中任一关键词，就视为游戏窗口。"
                       "默认给三个是因为不同语言/版本标题不同（英文「Age of Empires IV」、简中「帝国时代IV」「帝国时代4」）——"
                       "这与原程序一致，是「包含任一关键词」而非精确匹配整串标题。"),
        ParamSpec("hdr", "HDR模式", "bool", default=False,
                  help="开/关 HDR 时同一处像素的颜色会不同，故各配一组。开启则用下面「HDR」那组坐标/颜色，否则用「SDR」那组。"),
        ParamSpec("pixel", "检测像素坐标(SDR)", "point", default=[2526, 1405],
                  help="只在游戏内才会呈现特定颜色的一个像素点坐标（基于 2560x1440，其它分辨率需调整）。"),
        ParamSpec("color", "检测像素颜色(SDR)", "color", default=[26, 32, 46],
                  help="SDR（HDR关闭）下该像素应有的颜色；与实测颜色按「颜色容差」比较。"),
        ParamSpec("pixel_hdr", "检测像素坐标(HDR)", "point", default=[2526, 1405],
                  help="HDR 模式下的检测坐标（通常与 SDR 相同，可单独调整）。"),
        ParamSpec("color_hdr", "检测像素颜色(HDR)", "color", default=[65, 78, 105],
                  help="HDR（开启）下该像素应有的颜色。"),
        ParamSpec("tolerance", "颜色容差", "int", default=0, minimum=0, maximum=255,
                  help="颜色匹配允许的误差；0＝必须完全相同（与原程序一致）。"),
    ]

    def evaluate(self, ctx, inputs):
        title = ctx.foreground_title().lower()
        kws = [k.strip().lower() for k in self.values["keywords"].split(",") if k.strip()]
        active = any(k in title for k in kws)
        hdr = self.values["hdr"]
        x, y = self.values["pixel_hdr"] if hdr else self.values["pixel"]
        exp = tuple(self.values["color_hdr"] if hdr else self.values["color"])
        got = ctx.get_pixel(int(x), int(y))
        tol = self.values["tolerance"]
        pixel_ok = all(abs(g - e) <= tol for g, e in zip(got, exp))
        self.live = {"active": active, "pixel_ok": pixel_ok}
        return {"active": active, "pixel_ok": pixel_ok}


# ==================== 模板匹配（单/多模板）====================
@register
class TemplateMatch(DataNode):
    type_id = "sense.template_match"
    category = "感知"
    title = "模板匹配"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("found", DataType.BOOL, label="命中",
                 help="主要输出：区域里是否找到了模板（最高置信度≥匹配阈值）。通常只用这个，接到「分支」即可。"),
        data_out("confidence", DataType.NUMBER, label="置信度", advanced=True,
                 help="进阶/调试：最高的匹配相似度(0~1)。用于调「匹配阈值」时观察，一般不接。"),
        data_out("which", DataType.NUMBER, label="命中序号", advanced=True,
                 help="进阶：命中的是第几张模板（0 起；未命中=-1）。仅多模板时想区分命中了哪张才用。"),
        data_out("in_transition", DataType.BOOL, label="渐变中", advanced=True,
                 help="进阶：仅当开启「半透明UI抑制」时有意义——正处于UI渐入渐出动画期间为真（此时 found 强制为假）。"),
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 100],
                  help="未连入 image 时，按此区域自行截图"),
        ParamSpec("templates", "模板图片", "templates", default=[],
                  help="一个或多个模板路径；任一命中即 found=真，输出最高置信度"),
        ParamSpec("threshold", "匹配阈值", "float", default=0.6,
                  minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("transition_guard", "半透明UI抑制", "bool", default=False, advanced=True,
                  help="进阶：开启后用最近置信度变化模式识别UI渐入渐出，期间 in_transition=真、found=假，"
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
        if ctx.preview_enabled:
            self.live["preview"] = _imaging.encode_preview(img)   # 节点上预览“它看到的区域”
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
def _ocr_text(ctx, img, scale: float, allowlist) -> str:
    """对一张图按缩放比例做 OCR，返回拼接后的原始文本（OcrText / OcrNumber 共用）。"""
    img = _imaging.to_rgb(img)
    if scale and scale != 1.0:
        import cv2
        h, w = img.shape[:2]
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    allow = (allowlist or None)
    results = ctx.ocr(img, allowlist=allow, detail=0)
    return " ".join(results).strip()


# ==================== OCR 文本（通用）====================
@register
class OcrText(DataNode):
    """识别文本：读取屏幕指定区域里的文字，原样输出为字符串（通用 OCR）。

    需要数字时改用「识别数字」（它在本节点基础上加了字符纠正+正则抽取）；想从这里的文本里抠数字，
    可接「提取数字」节点。小字识别不准时把「缩放比例」调到 2~3 倍通常更好。
    «字符白名单»留空＝认任意字符；填了就只认这些字符（能显著提高在已知字符集下的准确率）。
    """

    type_id = "sense.ocr_text"
    category = "感知"
    title = "识别文本"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("text", DataType.STRING, label="文本", help="主要输出：认出的文字（多段会用空格拼接）。"),
        data_out("ok", DataType.BOOL, label="成功", advanced=True, help="进阶：是否认到了非空文本。"),
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 200, 40],
                  help="要识别文字的屏幕区域（左,上,右,下）。用区域框选工具更直观"),
        ParamSpec("scale", "缩放比例", "float", default=1.0, minimum=0.1, maximum=4.0, step=0.1,
                  help="识别前把截图放大的倍数；小字放大到 2~3 倍通常更准"),
        ParamSpec("allowlist", "字符白名单", "str", default="", advanced=True,
                  help="进阶：留空＝认任意字符；填了就只认这些字符（已知字符集时更准）。"),
    ]

    def evaluate(self, ctx, inputs):
        img = inputs.get("image")
        if img is None:
            img = ctx.capture_region(self.values["region"])
        raw = _ocr_text(ctx, img, self.values["scale"], self.values["allowlist"])
        self.live = {"raw": raw}
        if ctx.preview_enabled:
            self.live["preview"] = _imaging.encode_preview(img)
        return {"text": raw, "ok": bool(raw)}


# ==================== OCR 数字 ====================
@register
class OcrNumber(DataNode):
    """识别数字：读取屏幕指定区域里的数字（人口、资源、计数等），输出可参与运算的数值。

    它是「识别文本」的数字特化版：先 OCR 认字、把常见误识别纠正回数字（O→0、l/I→1、S→5），
    再从文本里抠出所有数字。最通用的输出是「数字列表」——区域里有几个数字就给几个，
    接「列表取值」「列表聚合(求和/取最值)」等通用节点即可灵活取用。为方便常见场景，本节点也
    直接给出便捷输出「数值/数值2」(=按「提取正则」抠到的第1/2个，如人口「12/20」→12、20)。

    几个常见疑问：
    · 白名单里为什么有 O o l I s S 这些字母？——它们是数字最常见的误识别（0↔O、1↔l/I、5↔S），
      列进白名单让 OCR 不至于丢字，识别后再统一纠正回数字，准确率更高。
    · 区域里有多个数字怎么办？——用「数字列表」输出，接「列表聚合」选 求和/最大/最小/平均/计数，
      或接「列表取值」按序号取第几个。便捷输出「数值」默认给列表里第一个。
    · 小字识别不准？——把「缩放比例」调到 2~3 倍通常更准。
    """

    type_id = "sense.ocr_number"
    category = "感知"
    title = "识别数字"
    inputs = [data_in("image", DataType.IMAGE, label="图像")]
    outputs = [
        data_out("numbers", DataType.LIST, label="数字列表",
                 help="主要输出：区域里认出的所有数字（已纠正）。接「列表取值/列表聚合」最通用。"),
        data_out("value", DataType.NUMBER, label="数值",
                 help="便捷输出：「提取正则」第1组（默认＝列表里第一个数字）。常见场景直接用它。"),
        data_out("value2", DataType.NUMBER, label="数值2", advanced=True,
                 help="进阶：「提取正则」第2组（如人口「12/20」的上限 20）。"),
        data_out("ok", DataType.BOOL, label="成功", advanced=True, help="进阶：是否抠到了数字。"),
        data_out("text", DataType.STRING, label="文本", advanced=True, help="进阶：OCR 原始文本（调试用）。"),
    ]
    params = [
        ParamSpec("region", "区域", "region", default=[0, 0, 100, 40],
                  help="要识别数字的屏幕区域（左,上,右,下）。用区域框选工具更直观"),
        ParamSpec("scale", "缩放比例", "float", default=1.0, minimum=0.1, maximum=4.0, step=0.1,
                  help="识别前把截图放大的倍数；小字放大到 2~3 倍通常更准"),
        ParamSpec("allowlist", "字符白名单", "str", default="0123456789/\\|OolIsS ", advanced=True,
                  help="进阶：只允许 OCR 认这些字符以提高准确率。含 O o l I s S 是因为它们是 0 1 5 的常见误识别，"
                       "认出后会自动纠正回数字；若要识别其它字符可自行增减。"),
        ParamSpec("regex", "提取正则", "str", default=r"(\d+)", advanced=True,
                  help="进阶：决定便捷输出「数值/数值2」怎么抠：第1组->数值，第2组->数值2；如人口用 (\\d+)[/\\\\|](\\d+)。"
                       "「数字列表」始终给出所有数字，不受此影响。"),
    ]

    # OCR 常见误识别纠正（O->0 等）
    _FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "s": "5"})

    def evaluate(self, ctx, inputs):
        img = inputs.get("image")
        if img is None:
            img = ctx.capture_region(self.values["region"])
        raw = _ocr_text(ctx, img, self.values["scale"], self.values["allowlist"])
        cleaned = raw.translate(self._FIX).replace(" ", "")

        numbers = [int(n) for n in re.findall(r"\d+", cleaned)]
        m = re.search(self.values["regex"], cleaned)
        v1 = int(m.group(1)) if m else (numbers[0] if numbers else None)
        v2 = int(m.group(2)) if (m and m.lastindex and m.lastindex >= 2) else None
        self.live = {"raw": raw, "value": v1, "value2": v2, "numbers": numbers}
        if ctx.preview_enabled:
            self.live["preview"] = _imaging.encode_preview(img)
        return {"numbers": numbers, "value": v1, "value2": v2,
                "ok": bool(numbers), "text": raw}

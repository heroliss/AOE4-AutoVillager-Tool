"""
节点共用的图像处理小工具：模板灰度缓存、灰度转换、模板匹配。

集中放置，供 sense / game 等节点复用，避免重复 I/O 与代码。
"""
from __future__ import annotations

import os
from typing import Optional

_gray_cache: dict[str, "any"] = {}


def load_gray(path: str):
    """加载模板为灰度图（带缓存）。找不到返回 None。"""
    if not path:
        return None
    cached = _gray_cache.get(path)
    if cached is not None:
        return cached
    if not os.path.exists(path):
        return None
    import cv2
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is not None:
        _gray_cache[path] = img
    return img


def to_gray(img):
    """numpy 图转灰度（已是单通道则原样返回）。"""
    import cv2
    if img is None:
        return None
    if getattr(img, "ndim", 2) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def to_rgb(img):
    """BGR -> RGB（供 easyocr 使用）。"""
    import cv2
    if img is None:
        return None
    if getattr(img, "ndim", 2) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img


def best_match(img_gray, tmpl_gray) -> float:
    """灰度模板匹配，返回最高置信度（0~1）。模板超过截图尺寸时自动等比缩小。"""
    if img_gray is None or tmpl_gray is None:
        return 0.0
    import cv2
    ih, iw = img_gray.shape[:2]
    th, tw = tmpl_gray.shape[:2]
    if th > ih or tw > iw:
        scale = min((ih * 0.9) / th, (iw * 0.9) / tw)
        if scale <= 0:
            return 0.0
        tmpl_gray = cv2.resize(tmpl_gray, (max(1, int(tw * scale)), max(1, int(th * scale))),
                               interpolation=cv2.INTER_AREA)
    result = cv2.matchTemplate(img_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return round(float(max_val), 4)


def resize_to(tmpl_gray, width: int, height: int):
    """把模板缩放到指定尺寸（遮挡检测用：模板需与小检测区同尺寸）。"""
    if tmpl_gray is None:
        return None
    th, tw = tmpl_gray.shape[:2]
    if th == height and tw == width:
        return tmpl_gray
    import cv2
    return cv2.resize(tmpl_gray, (width, height))


def encode_preview(img, max_dim: int = 220) -> str:
    """把 BGR numpy 图编码为 base64 PNG（仅 data 部分），供编辑器在节点上预览“它看到的画面”。
    下采样到最长边 ≤ max_dim 控制体积；灰度图也兼容；失败返回空串。"""
    if img is None:
        return ""
    try:
        import cv2
        import base64
        h, w = img.shape[:2]
        if max(h, w) > max_dim and max(h, w) > 0:
            s = max_dim / float(max(h, w))
            img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        return ""

"""
执行上下文（ExecutionContext）：节点运行时共享的服务与状态。

节点不再像旧代码那样 `import config` 直接读全局，而是通过 ctx 取服务，
这样参数都来自节点自身，模块之间彻底解耦，也便于测试。

提供：
- 按帧记忆缓存（memo）：节点输出每帧只算一次。
- 帧缓存：capture_region 按区域缓存；capture_full 整屏只抓一次，
  region_from_full 直接切片复用（"截一次、切多块"的性能基础）。
- 黑板变量（vars）：跨帧持久，用于缓存如 cached_tc_count。
- 截图 / OCR / 按键 / 取色 / 输入屏蔽 / 文件锁 等服务（惰性导入既有模块）。
- 日志与实时状态回调，供 UI 订阅。

重型依赖（mss/cv2/easyocr/pydirectinput）均惰性导入，仅在真正调用对应服务时加载，
因此在没有装这些库的环境也能导入引擎、跑纯逻辑测试。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Optional


class ExecutionContext:
    def __init__(
        self,
        on_log: Optional[Callable[[str, str, Optional[str]], None]] = None,
        on_state: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self.vars: dict[str, Any] = {}          # 黑板：跨帧持久
        self.tick_index: int = 0
        self.dt: float = 0.0
        self.cancel: bool = False

        self._memo: dict[tuple, Any] = {}       # 按帧：节点输出缓存 (node_id, port) -> value
        self._region_cache: dict[tuple, Any] = {}
        self._full_frame = None                 # 按帧：整屏截图缓存

        self._on_log = on_log
        self._on_state = on_state

    # ==================== 帧生命周期 ====================
    def begin_tick(self, dt: float = 0.0) -> None:
        self.tick_index += 1
        self.dt = dt
        self._memo.clear()
        self._region_cache.clear()
        self._full_frame = None

    # ==================== 记忆缓存（执行器使用）====================
    def memo_has(self, key: tuple) -> bool:
        return key in self._memo

    def memo_get(self, key: tuple) -> Any:
        return self._memo.get(key)

    def memo_set(self, key: tuple, value: Any) -> None:
        self._memo[key] = value

    # ==================== 日志 / 实时状态 ====================
    def log(self, level: str, message: str, node_id: Optional[str] = None) -> None:
        if self._on_log:
            self._on_log(level, message, node_id)
        else:
            print(f"[{level}] {message}")

    def emit_state(self, node_id: str, state: dict) -> None:
        if self._on_state:
            self._on_state(node_id, state)

    # ==================== 截图服务 ====================
    def capture_region(self, region):
        """截取指定区域（BGR numpy），按帧按区域缓存。"""
        key = tuple(region)
        if key in self._region_cache:
            return self._region_cache[key]
        # 若本帧已抓过整屏，直接切片复用，避免再次截图
        if self._full_frame is not None:
            img = self._slice_full(region)
        else:
            from screenshot_util import capture_region_np
            left, top, right, bottom = region
            img = capture_region_np(left, top, right, bottom)
        self._region_cache[key] = img
        return img

    def capture_full(self):
        """整屏截图（BGR numpy），按帧只抓一次。"""
        if self._full_frame is None:
            import numpy as np
            from screenshot_util import get_sct
            sct = get_sct()
            mon = sct.monitors[1]  # 主显示器
            self._full_origin = (mon["left"], mon["top"])
            shot = sct.grab(mon)
            self._full_frame = np.array(shot)[:, :, :3]  # BGRA -> BGR
        return self._full_frame

    def _slice_full(self, region):
        ox, oy = getattr(self, "_full_origin", (0, 0))
        left, top, right, bottom = region
        return self._full_frame[top - oy:bottom - oy, left - ox:right - ox]

    # ==================== 取色服务 ====================
    def get_pixel(self, x: int, y: int):
        """读取屏幕像素颜色 (r, g, b)，优先用 Windows GetPixel（绕过截图，<0.1ms）。"""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        get_pixel = getattr(user32, "GetPixelW", None) or getattr(user32, "GetPixelA", None)
        if get_pixel is None:
            img = self.capture_region((x, y, x + 1, y + 1))
            b, g, r = img[0, 0]
            return (int(r), int(g), int(b))
        get_pixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        get_pixel.restype = wintypes.DWORD
        hdc = user32.GetDC(None)
        try:
            val = get_pixel(hdc, x, y)
        finally:
            user32.ReleaseDC(None, hdc)
        if val == 0xFFFFFFFF:
            return (0, 0, 0)
        return (val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)

    # ==================== OCR 服务 ====================
    def ocr(self, image, allowlist: Optional[str] = None, detail: int = 0):
        from ocr_util import get_ocr_reader
        reader = get_ocr_reader()
        if allowlist:
            return reader.readtext(image, detail=detail, allowlist=allowlist)
        return reader.readtext(image, detail=detail)

    # ==================== 按键 / 鼠标服务 ====================
    def input(self):
        """返回 pydirectinput 模块（已在 input_config 中配置好 PAUSE/FAILSAFE）。"""
        import input_config  # noqa: F401  确保配置生效
        import pydirectinput
        return pydirectinput

    def release_modifiers(self) -> None:
        pdi = self.input()
        for key in ("shift", "ctrl", "alt"):
            pdi.keyUp(key)

    # ==================== 输入屏蔽 / 文件锁 ====================
    @contextmanager
    def input_block(self, max_duration: float = 3.0):
        from input_blocker import input_blocked
        with input_blocked(max_duration=max_duration) as ok:
            yield ok

    def acquire_lock(self) -> bool:
        from lock import acquire_lock
        return acquire_lock()

    def release_lock(self) -> None:
        from lock import release_lock
        release_lock()

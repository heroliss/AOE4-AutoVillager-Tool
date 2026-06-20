"""
执行上下文（ExecutionContext）：节点运行时共享的服务与状态。

节点不再像旧代码那样 `import config` 直接读全局，而是通过 ctx 取服务，
这样参数都来自节点自身，模块之间彻底解耦，也便于测试。

提供：
- 按帧记忆缓存（memo）：节点输出每帧只算一次。
- 帧缓存：capture_region 按区域缓存；prefetch_full 整屏只抓一次，
  之后 capture_region 命中缓存直接切片复用（"截一次、切多块"的性能基础）。
- 黑板变量（vars）：跨帧持久，用于缓存如 cached_tc_count。
- 截图 / OCR / 按键 / 取色 / 输入屏蔽 / 文件锁 / 修饰键检测 等服务。
- 干跑模式（dry_run）：操作类节点只记日志、不真正发按键，便于无游戏环境验证整图。
- 日志与实时状态回调，供 UI 订阅。

重型依赖（mss/cv2/easyocr/pydirectinput）均惰性导入，仅在真正调用对应服务时加载，
因此在没有装这些库的环境也能导入引擎、跑纯逻辑测试。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

# 修饰键虚拟键码
_VK = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12}


class ExecutionContext:
    def __init__(
        self,
        on_log: Optional[Callable[[str, str, Optional[str]], None]] = None,
        on_state: Optional[Callable[[str, dict], None]] = None,
        dry_run: bool = False,
    ) -> None:
        self.vars: dict[str, Any] = {}          # 黑板：跨帧持久
        self.tick_index: int = 0
        self.dt: float = 0.0
        self.cancel: bool = False
        self.dry_run: bool = dry_run            # True 时操作节点只记日志

        self._memo: dict[tuple, Any] = {}       # 按帧：节点输出缓存 (node_id, port) -> value
        self._region_cache: dict[tuple, Any] = {}
        self._full_frame = None                 # 按帧：整屏截图缓存
        self._full_origin = (0, 0)
        self._sct = None                        # 本帧 mss 实例（当前线程自建、帧末关闭，见 _capture_sct）

        self._block_active = False              # 输入屏蔽是否生效中
        self._lock_held = False                 # 文件锁是否持有中

        self._on_log = on_log
        self._on_state = on_state

    # ==================== 帧生命周期 ====================
    def begin_tick(self, dt: float = 0.0) -> None:
        self.tick_index += 1
        self.dt = dt
        self._memo.clear()
        self._region_cache.clear()
        self._full_frame = None

    def cleanup_tick(self) -> None:
        """一帧结束后的安全清理：确保输入屏蔽与文件锁不会跨帧泄漏。"""
        if self._block_active:
            self.block_input_stop()
        if self._lock_held:
            self.release_lock()
        if self._sct is not None:               # 本帧 mss 实例当帧关闭：与创建在同一线程，释放 GDI/内存，不泄漏
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

    def _capture_sct(self):
        """本帧用的 mss 实例：在【当前线程】惰性创建，帧末 cleanup_tick 关闭。
        不能跨线程复用——Windows 下 mss 的 GDI DC 有线程亲和性，跨线程 grab 会 BitBlt 失败；
        也不能用 thread-local 缓存——pywebview 每次 js_api 调用都换线程，旧实例随死线程滞留、
        每帧泄漏整屏位图。每帧“同线程创建+关闭”：既不跨线程、又当帧释放 GDI/内存，不泄漏。"""
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    # ==================== 记忆缓存（执行器使用）====================
    def memo_has(self, key: tuple) -> bool:
        return key in self._memo

    def memo_get(self, key: tuple) -> Any:
        return self._memo.get(key)

    def memo_set(self, key: tuple, value: Any) -> None:
        self._memo[key] = value

    def memo_snapshot(self) -> dict:
        """本帧已解析的数据输出 (node_id, port) -> value 的拷贝（供编辑器“运行可视化”显示数据线上的值）。"""
        return dict(self._memo)

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
    def prefetch_full(self):
        """整屏截图并缓存（按帧只抓一次）。之后 capture_region 会直接切片复用。"""
        if self._full_frame is None:
            import numpy as np
            sct = self._capture_sct()   # 当前线程自建、帧末关闭，避免 pywebview 换线程导致的泄漏/BitBlt 失败
            mon = sct.monitors[1]  # 主显示器
            self._full_origin = (mon["left"], mon["top"])
            shot = sct.grab(mon)
            self._full_frame = np.array(shot)[:, :, :3]  # BGRA -> BGR
        return self._full_frame

    def capture_region(self, region):
        """截取指定区域（BGR numpy），按帧按区域缓存；若已预取整屏则切片复用。"""
        key = tuple(region)
        cached = self._region_cache.get(key)
        if cached is not None:
            return cached
        if self._full_frame is not None:
            ox, oy = self._full_origin
            left, top, right, bottom = region
            img = self._full_frame[top - oy:bottom - oy, left - ox:right - ox]
        else:
            import numpy as np
            sct = self._capture_sct()   # 同上：本帧自有 mss，不走 thread-local（会随 pywebview 线程泄漏）
            left, top, right, bottom = region
            shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
            img = np.array(shot)[:, :, :3]  # BGRA -> BGR
        self._region_cache[key] = img
        return img

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

    def foreground_title(self) -> str:
        """当前前台窗口标题。"""
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def modifiers_pressed(self, which=("shift", "ctrl", "alt")) -> bool:
        """是否有指定修饰键被物理按下。"""
        import ctypes
        user32 = ctypes.windll.user32
        for name in which:
            vk = _VK.get(name)
            if vk and (user32.GetAsyncKeyState(vk) & 0x8000):
                return True
        return False

    # ==================== OCR 服务 ====================
    def ocr(self, image, allowlist: Optional[str] = None, detail: int = 0):
        from ocr_util import get_ocr_reader
        reader = get_ocr_reader()
        if allowlist:
            return reader.readtext(image, detail=detail, allowlist=allowlist)
        return reader.readtext(image, detail=detail)

    # ==================== 按键 / 鼠标服务 ====================
    def input(self):
        """返回 pydirectinput 模块（已在 input_config 中配置 PAUSE/FAILSAFE）。"""
        import input_config  # noqa: F401  确保配置生效
        import pydirectinput
        return pydirectinput

    def release_modifiers(self) -> None:
        if self.dry_run:
            return
        pdi = self.input()
        for key in ("shift", "ctrl", "alt"):
            pdi.keyUp(key)

    # ==================== 输入屏蔽 ====================
    def block_input_start(self, max_duration: float = 3.0) -> bool:
        if self.dry_run:
            self._block_active = True
            return True
        from input_blocker import _set_block
        ok = _set_block(True)
        self._block_active = True
        return ok

    def block_input_stop(self) -> None:
        self._block_active = False
        if self.dry_run:
            return
        from input_blocker import _set_block
        _set_block(False)

    # ==================== 文件锁 ====================
    def acquire_lock(self) -> bool:
        if self.dry_run:
            self._lock_held = True
            return True
        from lock import acquire_lock
        ok = acquire_lock()
        self._lock_held = ok
        return ok

    def release_lock(self) -> None:
        self._lock_held = False
        if self.dry_run:
            return
        from lock import release_lock
        release_lock()

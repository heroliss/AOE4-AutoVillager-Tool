"""
控制流节点：整屏预取、定时门、修饰键暂停门、输入屏蔽作用域、文件锁。

这些把原主循环里的 continue / 每3秒检查 / 修饰键暂停 / BlockInput / 文件锁
显式化为可连线的节点。输入屏蔽与文件锁用"开始/结束"两个节点表达作用域，
执行器在每帧结束时兜底释放，避免跨帧泄漏。

约定：执行流走到"没有连出的出口"即结束本帧（无需专门的"结束"节点）——
例如条件节点的某个分支不接任何节点，就表示该情况下本帧到此为止。
"""
from __future__ import annotations

import time

from ..core import ControlNode, ParamSpec, exec_in, exec_out, register


@register
class PrefetchFull(ControlNode):
    """整屏截图并缓存一次；之后下游所有"截图(区域)"直接切片复用，避免多次截图。"""
    type_id = "control.prefetch_full"
    category = "控制"
    title = "整屏预取"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        ctx.prefetch_full()
        return {}, "out"


@register
class EveryNSeconds(ControlNode):
    """定时门：距上次通过满足间隔则走 due，否则走 skip（跨帧记时）。"""
    type_id = "control.every"
    category = "控制"
    title = "定时门"
    inputs = [exec_in("in")]
    outputs = [exec_out("due", label="到点"), exec_out("skip", label="未到")]
    params = [ParamSpec("interval", "间隔(秒)", "float", default=3.0, minimum=0.0, maximum=600.0, step=0.1)]

    def __init__(self):
        super().__init__()
        self._last = 0.0

    def execute(self, ctx, inputs):
        now = time.time()
        if now - self._last >= self.values["interval"]:
            self._last = now
            return {}, "due"
        return {}, "skip"


@register
class PauseIfModifier(ControlNode):
    """若用户正按住修饰键则结束本帧（暂停自动操作），否则继续。"""
    type_id = "control.pause_if_modifier"
    category = "控制"
    title = "修饰键暂停门"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]
    params = [ParamSpec("keys", "监测修饰键", "str", default="shift,ctrl,alt")]

    def execute(self, ctx, inputs):
        which = tuple(k.strip() for k in self.values["keys"].split(",") if k.strip())
        if ctx.modifiers_pressed(which):
            ctx.log("INFO", "检测到修饰键，暂停")
            return {}, None
        return {}, "out"


@register
class InputBlockBegin(ControlNode):
    """开始屏蔽物理输入（需管理员权限）。与"输入屏蔽结束"成对使用。"""
    type_id = "control.input_block_begin"
    category = "控制"
    title = "输入屏蔽开始"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]
    params = [ParamSpec("max_duration", "最长屏蔽(秒)", "float", default=3.0, minimum=0.5, maximum=10.0, step=0.5)]

    def execute(self, ctx, inputs):
        ctx.block_input_start(self.values["max_duration"])
        return {}, "out"


@register
class InputBlockEnd(ControlNode):
    type_id = "control.input_block_end"
    category = "控制"
    title = "输入屏蔽结束"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        ctx.block_input_stop()
        return {}, "out"


@register
class LockAcquire(ControlNode):
    """获取操作锁：成功走 ok，已被占用走 busy（防止并发执行操作）。"""
    type_id = "control.lock_acquire"
    category = "控制"
    title = "获取操作锁"
    inputs = [exec_in("in")]
    outputs = [exec_out("ok", label="成功"), exec_out("busy", label="占用中")]

    def execute(self, ctx, inputs):
        return {}, ("ok" if ctx.acquire_lock() else "busy")


@register
class LockRelease(ControlNode):
    type_id = "control.lock_release"
    category = "控制"
    title = "释放操作锁"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        ctx.release_lock()
        return {}, "out"


@register
class Delay(ControlNode):
    """等待指定秒数（如操作后等 UI 更新）。"""
    type_id = "control.delay"
    category = "控制"
    title = "延时"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]
    params = [ParamSpec("seconds", "秒", "float", default=0.0, minimum=0.0, maximum=10.0, step=0.1)]

    def execute(self, ctx, inputs):
        s = self.values["seconds"]
        if s > 0 and not ctx.dry_run:
            time.sleep(s)
        return {}, "out"

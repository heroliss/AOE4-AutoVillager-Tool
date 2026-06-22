"""
控制流节点：整屏预取、定时门、修饰键检测、输入屏蔽作用域、文件锁。

这些把原主循环里的 continue / 每3秒检查 / 修饰键暂停 / BlockInput / 文件锁
显式化为可连线的节点。输入屏蔽与文件锁用"开始/结束"两个节点表达作用域，
执行器在每帧结束时兜底释放，避免跨帧泄漏。

约定：执行流走到"没有连出的出口"即结束本帧（无需专门的"结束"节点）——
例如「分支」节点的某个出口不接任何节点，就表示该情况下本帧到此为止。
"""
from __future__ import annotations

import time

from ..core import ControlNode, DataNode, ParamSpec, DataType, exec_in, exec_out, data_in, data_out, register


@register
class PrefetchFull(ControlNode):
    """整屏截图并缓存一次；之后下游所有"截图(区域)"直接切片复用。

    ⚠ 一般用不到，多数情况下反而更慢——保留它只为兼容老流程/特殊场景。原因：在 Windows 下
    每次截屏（无论大小）都要和桌面合成器(DWM)同步一次，约 8ms 的固定开销；整屏截图还要额外
    传输/拷贝整块位图（2560×1440 实测约 62ms）。而本工具识别的区域都很小（合计约占屏幕 1%），
    每个区域单独截、按帧自动缓存共享（见 ExecutionContext.capture_region），只有 2~3 次小截图，
    远比整屏一次便宜。只有当一帧要读「很多」个不同区域（多到累计截图次数 >整屏一次）时，
    预取整屏才划算。"""
    type_id = "control.prefetch_full"
    category = "控制"
    title = "整屏预取"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        ctx.prefetch_full()
        return {}, "out"


@register
class PrefetchData(ControlNode):
    """预读：进入「时间敏感区」(如输入屏蔽)之前，先把接进来的若干数据值算好并缓存（逐帧记忆化），
    把它们的识别/计算耗时挪出敏感区。本节点只做"提前求值"，按原样放行执行流，不读值、不分支、不改任何行为。

    原理：执行器在执行一个节点【前】会先求值它的全部数据输入——所以只要把要在屏蔽内用到的值
    （如食物/黄金 OCR）接到本节点，执行流走到这里时它们就被算好并缓存；真正进操作区时同帧免费取，
    屏蔽窗口更短、玩家更不易察觉自动操作。

    放置要点：接在「确定要生产之后、抢锁/屏蔽之前」，且只预热【本段真正会用到】的值——
    否则会为用不到的值白白付识别耗时（如默认只出村民时不应预热黄金）。"""
    type_id = "control.prefetch_data"
    category = "控制"
    title = "预读"
    inputs = [
        exec_in("in"),
        data_in("v1", DataType.ANY, label="值1", help="要提前算好并缓存的值（接 OCR/识别等耗时输出）。不接=忽略。"),
        data_in("v2", DataType.ANY, label="值2", help="同上，可多预热一个值。不接=忽略。"),
        data_in("v3", DataType.ANY, label="值3", help="同上，可多预热一个值。不接=忽略。"),
    ]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        # 数据输入已在执行前被求值并记忆化（见 Executor._resolve_inputs）——到这里即已"预热"，无需再做任何事。
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
class ModifierDown(DataNode):
    """检测此刻是否按住了指定修饰键（Shift/Ctrl/Alt），输出「按住修饰键」(是/否)。

    常用于"人在手动操作时暂停自动化"：把输出接到「分支」的条件——按住时走「真」（下游不接＝
    本帧结束＝暂停），没按时走「假」（继续）。这样它和其它检测节点一致：只负责"看一眼给个是/否"，
    真正的分岔交给「分支」。
    """
    type_id = "sense.modifier_down"
    category = "感知"
    title = "修饰键检测"
    outputs = [data_out("down", DataType.BOOL, label="按住修饰键",
                        help="此刻是否按住了所监测的任一修饰键（接「分支」的条件即可实现「按住则暂停」）。")]
    params = [ParamSpec("keys", "监测修饰键", "keys", default="shift,ctrl,alt",
                        help="从下拉选择要监测的修饰键组合；监测其中任一是否被按住。")]

    def evaluate(self, ctx, inputs):
        which = tuple(k.strip() for k in self.values["keys"].split(",") if k.strip())
        return {"down": bool(ctx.modifiers_pressed(which))}


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

"""
基础节点：事件入口、常量/开关、比较、逻辑（与/或/非）、算式、取最小最大、
条件分支、变量读写、日志。

这些与游戏无关、无重型依赖，是搭建任何流程的骨架，也用于引擎自测。
"""
from __future__ import annotations

import operator
from typing import Any, Optional

from ..core import (
    DataNode, ControlNode, ParamSpec, DataType,
    exec_in, exec_out, data_in, data_out, register,
)


# ==================== 事件入口 ====================
@register
class OnTick(ControlNode):
    type_id = "event.on_tick"
    category = "事件"
    title = "每帧触发"
    inputs = []
    outputs = [exec_out("out")]
    params = [ParamSpec("interval", "循环间隔(秒)", "float", default=0.1,
                        minimum=0.0, maximum=5.0, step=0.01,
                        help="每帧之间的最短间隔，用于平衡响应速度与 CPU 占用")]

    def execute(self, ctx, inputs):
        return {}, "out"


# ==================== 常量 / 开关 ====================
@register
class ConstNumber(DataNode):
    type_id = "data.const_number"
    category = "数据"
    title = "常量(数值)"
    outputs = [data_out("value", DataType.NUMBER)]
    params = [ParamSpec("value", "数值", "float", default=0.0)]

    def evaluate(self, ctx, inputs):
        return {"value": self.values["value"]}


@register
class Switch(DataNode):
    type_id = "data.switch"
    category = "数据"
    title = "开关(布尔)"
    outputs = [data_out("value", DataType.BOOL)]
    params = [ParamSpec("on", "开启", "bool", default=True)]

    def evaluate(self, ctx, inputs):
        return {"value": bool(self.values["on"])}


# ==================== 比较 / 逻辑 ====================
_COMPARE_OPS = {
    "<": operator.lt, "<=": operator.le, ">": operator.gt,
    ">=": operator.ge, "==": operator.eq, "!=": operator.ne,
}


@register
class Compare(DataNode):
    type_id = "logic.compare"
    category = "逻辑"
    title = "比较"
    inputs = [data_in("a", DataType.NUMBER), data_in("b", DataType.NUMBER)]
    outputs = [data_out("result", DataType.BOOL)]
    params = [ParamSpec("op", "运算符", "enum", default="<",
                        choices=["<", "<=", ">", ">=", "==", "!="])]

    def evaluate(self, ctx, inputs):
        a, b = inputs.get("a"), inputs.get("b")
        if a is None or b is None:
            return {"result": False}
        return {"result": bool(_COMPARE_OPS[self.values["op"]](a, b))}


@register
class LogicAnd(DataNode):
    type_id = "logic.and"
    category = "逻辑"
    title = "与"
    inputs = [data_in("a", DataType.BOOL), data_in("b", DataType.BOOL)]
    outputs = [data_out("result", DataType.BOOL)]

    def evaluate(self, ctx, inputs):
        return {"result": bool(inputs.get("a")) and bool(inputs.get("b"))}


@register
class LogicOr(DataNode):
    type_id = "logic.or"
    category = "逻辑"
    title = "或"
    inputs = [data_in("a", DataType.BOOL), data_in("b", DataType.BOOL)]
    outputs = [data_out("result", DataType.BOOL)]

    def evaluate(self, ctx, inputs):
        return {"result": bool(inputs.get("a")) or bool(inputs.get("b"))}


@register
class LogicNot(DataNode):
    type_id = "logic.not"
    category = "逻辑"
    title = "非"
    inputs = [data_in("a", DataType.BOOL)]
    outputs = [data_out("result", DataType.BOOL)]

    def evaluate(self, ctx, inputs):
        return {"result": not bool(inputs.get("a"))}


# ==================== 算式 / 取最值 ====================
_ARITH_OPS = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
    "/": lambda a, b: a / b if b else 0.0,
    "//": lambda a, b: a // b if b else 0,
}


@register
class Arithmetic(DataNode):
    type_id = "math.arith"
    category = "算式"
    title = "算式"
    inputs = [data_in("a", DataType.NUMBER), data_in("b", DataType.NUMBER)]
    outputs = [data_out("value", DataType.NUMBER)]
    params = [ParamSpec("op", "运算符", "enum", default="+",
                        choices=["+", "-", "*", "/", "//"])]

    def evaluate(self, ctx, inputs):
        a = inputs.get("a") or 0
        b = inputs.get("b") or 0
        return {"value": _ARITH_OPS[self.values["op"]](a, b)}


@register
class MinNode(DataNode):
    type_id = "math.min"
    category = "算式"
    title = "取最小"
    inputs = [data_in("a", DataType.NUMBER), data_in("b", DataType.NUMBER),
              data_in("c", DataType.NUMBER), data_in("d", DataType.NUMBER)]
    outputs = [data_out("value", DataType.NUMBER)]

    def evaluate(self, ctx, inputs):
        vals = [v for v in (inputs.get("a"), inputs.get("b"),
                            inputs.get("c"), inputs.get("d")) if v is not None]
        return {"value": min(vals) if vals else 0}


@register
class ClampNode(DataNode):
    type_id = "math.clamp"
    category = "算式"
    title = "钳制"
    inputs = [data_in("value", DataType.NUMBER)]
    outputs = [data_out("value", DataType.NUMBER)]
    params = [ParamSpec("lo", "下限", "float", default=0.0),
              ParamSpec("hi", "上限", "float", default=999999.0)]

    def evaluate(self, ctx, inputs):
        v = inputs.get("value") or 0
        return {"value": max(self.values["lo"], min(self.values["hi"], v))}


# ==================== 条件分支 ====================
@register
class If(ControlNode):
    type_id = "control.if"
    category = "控制"
    title = "条件"
    inputs = [exec_in("in"), data_in("cond", DataType.BOOL)]
    outputs = [exec_out("true", label="真"), exec_out("false", label="假")]

    def execute(self, ctx, inputs):
        return {}, ("true" if bool(inputs.get("cond")) else "false")


# ==================== 变量读写（跨帧黑板）====================
@register
class SetVar(ControlNode):
    type_id = "control.set_var"
    category = "控制"
    title = "写变量"
    inputs = [exec_in("in"), data_in("value", DataType.ANY)]
    outputs = [exec_out("out")]
    params = [ParamSpec("name", "变量名", "str", default="x")]

    def execute(self, ctx, inputs):
        ctx.vars[self.values["name"]] = inputs.get("value")
        return {}, "out"


@register
class GetVar(DataNode):
    type_id = "data.get_var"
    category = "数据"
    title = "读变量"
    outputs = [data_out("value", DataType.ANY)]
    params = [ParamSpec("name", "变量名", "str", default="x"),
              ParamSpec("default", "默认值", "float", default=0.0)]

    def evaluate(self, ctx, inputs):
        return {"value": ctx.vars.get(self.values["name"], self.values["default"])}


# ==================== 调试 ====================
@register
class LogNode(ControlNode):
    type_id = "debug.log"
    category = "调试"
    title = "日志"
    inputs = [exec_in("in"), data_in("value", DataType.ANY)]
    outputs = [exec_out("out")]
    params = [ParamSpec("message", "文本", "str", default="")]

    def execute(self, ctx, inputs):
        msg = self.values["message"]
        val = inputs.get("value")
        if val is not None:
            msg = f"{msg} {val}" if msg else str(val)
        ctx.log("INFO", msg)
        return {}, "out"

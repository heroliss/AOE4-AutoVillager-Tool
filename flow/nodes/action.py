"""
操作节点：按键（可附带修饰键 / 重复若干次）、释放修饰键、鼠标点击。

所有操作在 ctx.dry_run 时只记日志、不真正发送输入，便于无游戏环境验证整图。
"""
from __future__ import annotations

from ..core import ControlNode, ParamSpec, DataType, exec_in, exec_out, data_in, register


def _parse_mods(text: str) -> list[str]:
    return [m.strip().lower() for m in (text or "").split(",") if m.strip()]


@register
class PressKey(ControlNode):
    """按键：按下某个键，可附带修饰键(Ctrl/Shift/…)，重复「数量」次（次数可由「数量」输入连入）。

    说明：AOE4 里对生产按钮按住 Shift 点一下会一次排 5 个（这是游戏自带设定，与本工具无关）；
    若想用它，把「修饰键」设为 Shift 并相应调整次数即可。本节点不再内置“每5个”的批量开关——
    那只对生产单位有意义，且用在编组(Ctrl+数字)等操作上反而会出错。
    """

    type_id = "action.press_key"
    category = "操作"
    title = "按键"
    inputs = [exec_in("in"), data_in("count", DataType.NUMBER, label="数量")]
    outputs = [exec_out("out")]
    params = [
        ParamSpec("key", "按键", "key", default="q"),
        ParamSpec("modifiers", "修饰键", "keys", default="", help="从下拉选择按键时要附带的修饰键组合（如 Ctrl+Shift）。"),
        ParamSpec("repeat", "重复次数", "int", default=1, minimum=1, maximum=100,
                  help="按几下；未连入「数量」时用此值，连入则用其数值。"),
        ParamSpec("post_escape", "结束后按ESC", "bool", default=False),
    ]

    def execute(self, ctx, inputs):
        n = inputs.get("count")
        n = int(n) if n is not None else int(self.values["repeat"])
        key = self.values["key"]
        mods = _parse_mods(self.values["modifiers"])

        if n <= 0:
            return {}, "out"

        if ctx.dry_run:
            desc = ("+".join(mods + [key])) if mods else key
            ctx.log("INFO", f"[干跑] 按键 {desc} x{n}"
                            + ("，再按ESC" if self.values["post_escape"] else ""))
            return {}, "out"

        pdi = ctx.input()
        for m in mods:
            pdi.keyDown(m)
        for _ in range(n):
            pdi.press(key)
        for m in reversed(mods):
            pdi.keyUp(m)

        if self.values["post_escape"]:
            pdi.press("escape")
        return {}, "out"


@register
class ReleaseModifiers(ControlNode):
    type_id = "action.release_modifiers"
    category = "操作"
    title = "释放修饰键"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]

    def execute(self, ctx, inputs):
        if ctx.dry_run:
            ctx.log("INFO", "[干跑] 释放 shift/ctrl/alt")
        else:
            ctx.release_modifiers()
        return {}, "out"


@register
class MouseClick(ControlNode):
    type_id = "action.mouse_click"
    category = "操作"
    title = "鼠标点击"
    inputs = [exec_in("in")]
    outputs = [exec_out("out")]
    params = [
        ParamSpec("point", "坐标", "point", default=[0, 0]),
        ParamSpec("button", "按键", "enum", default="left", choices=["left", "right", "middle"]),
        ParamSpec("clicks", "次数", "int", default=1, minimum=1, maximum=10),
    ]

    def execute(self, ctx, inputs):
        x, y = self.values["point"]
        btn = self.values["button"]
        clicks = self.values["clicks"]
        if ctx.dry_run:
            ctx.log("INFO", f"[干跑] {btn}键点击 ({x},{y}) x{clicks}")
            return {}, "out"
        pdi = ctx.input()
        pdi.moveTo(int(x), int(y))
        for _ in range(clicks):
            pdi.click(button=btn)
        return {}, "out"

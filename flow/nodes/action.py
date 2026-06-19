"""
操作节点：按键（支持修饰键 / 重复 / Shift 批量）、释放修饰键、鼠标点击。

所有操作在 ctx.dry_run 时只记日志、不真正发送输入，便于无游戏环境验证整图。
"""
from __future__ import annotations

from ..core import ControlNode, ParamSpec, DataType, exec_in, exec_out, data_in, register


def _parse_mods(text: str) -> list[str]:
    return [m.strip().lower() for m in (text or "").split(",") if m.strip()]


@register
class PressKey(ControlNode):
    type_id = "action.press_key"
    category = "操作"
    title = "按键"
    inputs = [exec_in("in"), data_in("count", DataType.NUMBER, label="数量")]
    outputs = [exec_out("out")]
    params = [
        ParamSpec("key", "按键", "key", default="q"),
        ParamSpec("modifiers", "修饰键", "keys", default="", help="逗号分隔，如 ctrl,shift"),
        ParamSpec("repeat", "重复次数", "int", default=1, minimum=1, maximum=100,
                  help="未连入 count 时使用此值"),
        ParamSpec("shift_batch", "Shift批量", "bool", default=False,
                  help="开启后每 batch_size 个用一次 Shift+键（AOE4 一次排5个）"),
        ParamSpec("batch_size", "每批数量", "int", default=5, minimum=1, maximum=20),
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
            extra = " Shift批量" if self.values["shift_batch"] else ""
            ctx.log("INFO", f"[干跑] 按键 {desc} x{n}{extra}"
                            + ("，再按ESC" if self.values["post_escape"] else ""))
            return {}, "out"

        pdi = ctx.input()
        if self.values["shift_batch"]:
            bs = self.values["batch_size"]
            for _ in range(n // bs):
                pdi.keyDown("shift"); pdi.press(key); pdi.keyUp("shift")
            for _ in range(n % bs):
                pdi.press(key)
        else:
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

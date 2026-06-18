"""
端口（Port）与参数（ParamSpec）的类型定义。

设计要点：
- 端口分两类（PortKind）：
    EXEC —— 执行流端口，决定"先做什么再做什么"（白色实线）。
    DATA —— 数据流端口，惰性传值（彩色虚线）。
- DATA 端口带数据类型（DataType），用于编辑器连线时的类型校验。
- ParamSpec 是节点参数的"声明式"描述：编辑器据此自动生成配置控件，
  序列化时据此读写，无需为每个节点手写配置界面。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PortKind(Enum):
    EXEC = "exec"   # 执行流：控制顺序
    DATA = "data"   # 数据流：传值


class DataType(Enum):
    ANY = "any"
    IMAGE = "image"     # numpy 图像（BGR 或灰度）
    NUMBER = "number"   # int / float
    BOOL = "bool"
    STRING = "string"
    REGION = "region"   # (left, top, right, bottom)
    POINT = "point"     # (x, y)
    COLOR = "color"     # (r, g, b)


@dataclass
class Port:
    name: str                       # 端口在节点内的唯一标识
    kind: PortKind
    dtype: DataType = DataType.ANY  # 仅对 DATA 端口有意义
    label: str = ""                 # UI 显示名（留空则用 name）

    @property
    def display(self) -> str:
        return self.label or self.name


# —— 便捷构造函数（让节点定义更简洁）——
def exec_in(name: str = "in", label: str = "") -> Port:
    return Port(name, PortKind.EXEC, label=label)


def exec_out(name: str = "out", label: str = "") -> Port:
    return Port(name, PortKind.EXEC, label=label)


def data_in(name: str, dtype: DataType = DataType.ANY, label: str = "") -> Port:
    return Port(name, PortKind.DATA, dtype=dtype, label=label)


def data_out(name: str, dtype: DataType = DataType.ANY, label: str = "") -> Port:
    return Port(name, PortKind.DATA, dtype=dtype, label=label)


# 参数类型集合。编辑器据此决定用哪种控件：
#   基础：int / float / bool / str / enum / regex
#   带内嵌工具：region(框选) / point(取点) / color(吸色) / point_color(取点+吸色)
#                template(模板截取) / templates(多模板) / key(快捷键捕获) / keys(组合键)
PARAM_TYPES = {
    "int", "float", "bool", "str", "enum", "regex",
    "region", "point", "color", "point_color",
    "template", "templates", "key", "keys",
}


@dataclass
class ParamSpec:
    key: str                                   # 参数键（节点内唯一）
    label: str                                 # UI 显示名
    ptype: str                                 # 见 PARAM_TYPES
    default: Any = None
    minimum: Optional[float] = None            # int/float 下限
    maximum: Optional[float] = None            # int/float 上限
    step: Optional[float] = None               # 滑块步进
    choices: Optional[list] = None             # enum 选项 [(value, label), ...] 或 [value, ...]
    help: str = ""

    def __post_init__(self):
        if self.ptype not in PARAM_TYPES:
            raise ValueError(f"未知参数类型: {self.ptype!r}（节点参数 {self.key!r}）")

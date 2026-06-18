"""
节点基类与注册表。

两种节点：
- DataNode   —— 纯数据节点（无执行端口）。实现 evaluate()，由执行器在被下游
                "拉取"时调用，结果按帧记忆化（同一帧只算一次，这是"整屏截一次、
                多处切片复用"等性能特性的来源）。
- ControlNode —— 控制节点（带执行端口）。实现 execute()，在执行流到达时被调用，
                返回 (数据输出, 下一个执行出口名)。条件/分支通过选择不同出口实现。

节点用 @register 注册到全局 type_id → 类 的映射，编辑器与序列化据此创建实例。
"""
from __future__ import annotations

from typing import Any, Optional

from .types import Port, ParamSpec, PortKind


class Node:
    """节点基类。子类用类属性声明端口与参数。"""

    type_id: str = ""          # 全局唯一标识，如 "sense.template_match"
    category: str = "misc"     # 调色板分组，如 "感知" / "操作" / "逻辑"
    title: str = ""            # UI 显示标题（中文）

    inputs: list[Port] = []
    outputs: list[Port] = []
    params: list[ParamSpec] = []

    def __init__(self) -> None:
        # 参数当前值（从默认值初始化），序列化即保存这个字典
        self.values: dict[str, Any] = {p.key: p.default for p in self.params}
        # 运行时状态，供编辑器实时显示（如置信度/识别值/通过失败/耗时）
        self.live: dict[str, Any] = {}

    # —— 端口辅助 ——
    @classmethod
    def has_exec(cls) -> bool:
        return any(p.kind == PortKind.EXEC for p in (*cls.inputs, *cls.outputs))

    @classmethod
    def exec_outs(cls) -> list[str]:
        return [p.name for p in cls.outputs if p.kind == PortKind.EXEC]

    @classmethod
    def data_inputs(cls) -> list[Port]:
        return [p for p in cls.inputs if p.kind == PortKind.DATA]

    @classmethod
    def first_exec_out(cls) -> Optional[str]:
        outs = cls.exec_outs()
        return outs[0] if outs else None

    # —— 子类按需重写其一 ——
    def evaluate(self, ctx, inputs: dict[str, Any]) -> dict[str, Any]:
        """纯数据节点：根据数据输入计算数据输出，返回 {输出端口名: 值}。"""
        return {}

    def execute(self, ctx, inputs: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
        """控制节点：执行副作用，返回 (数据输出, 下一个执行出口名 或 None=停止)。"""
        return {}, self.first_exec_out()

    def param(self, key: str) -> Any:
        return self.values.get(key)


class DataNode(Node):
    """纯数据节点：只实现 evaluate()。"""

    def execute(self, ctx, inputs):  # pragma: no cover - 数据节点不应走执行流
        raise RuntimeError(f"{self.type_id} 是数据节点，不能出现在执行流上")


class ControlNode(Node):
    """控制节点：只实现 execute()。"""

    def evaluate(self, ctx, inputs):
        # 控制节点的数据输出在 execute() 时写入记忆缓存，这里不应被直接拉取
        return {}


# ==================== 注册表 ====================

_REGISTRY: dict[str, type[Node]] = {}


def register(cls: type[Node]) -> type[Node]:
    """节点类装饰器：注册到全局表。"""
    if not cls.type_id:
        raise ValueError(f"节点 {cls.__name__} 缺少 type_id")
    if cls.type_id in _REGISTRY and _REGISTRY[cls.type_id] is not cls:
        raise ValueError(f"重复的节点 type_id: {cls.type_id}")
    _REGISTRY[cls.type_id] = cls
    return cls


def create_node(type_id: str) -> Node:
    if type_id not in _REGISTRY:
        raise KeyError(f"未注册的节点类型: {type_id}")
    return _REGISTRY[type_id]()


def registry() -> dict[str, type[Node]]:
    """返回注册表副本（供编辑器构建调色板）。"""
    return dict(_REGISTRY)

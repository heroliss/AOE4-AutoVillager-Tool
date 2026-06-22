"""
执行器（Executor）：每帧沿执行流遍历图，按需惰性求值数据流。

一帧（tick）的执行：
1. 从入口节点（event.*）开始，沿执行线（exec_edges）依次访问控制节点。
2. 访问某节点前，先解析它的数据输入：递归向上游"拉取"数据输出，
   结果按 (node_id, port) 记忆化，同一帧内只算一次。
3. 控制节点 execute() 返回下一个执行出口名；据此找到下一节点，直到出口为空。

性能：数据节点（截图/匹配/OCR/算式）只在被取到的执行路径需要时才求值；
一个数据输出扇出给多个下游时只算一次（如整屏截图切多块）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .graph import Graph
from .context import ExecutionContext
from .node import Node


class Executor:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    # ==================== 数据流：惰性拉取 ====================
    def _resolve(self, ctx: ExecutionContext, node_id: str, port: str) -> Any:
        key = (node_id, port)
        if ctx.memo_has(key):
            return ctx.memo_get(key)

        node = self.graph.nodes[node_id]
        inputs = self._resolve_inputs(ctx, node_id, node)

        if node.has_exec():
            # 控制节点的数据输出应在其 execute() 时写入；若尚未执行则视为未就绪
            ctx.memo_set(key, None)
            return None

        if ctx.profile_enabled:
            t0 = time.perf_counter()
            outputs = node.evaluate(ctx, inputs)
            ctx.profile_record(node_id, (time.perf_counter() - t0) * 1000.0)
        else:
            outputs = node.evaluate(ctx, inputs)
        for out_name, val in outputs.items():
            ctx.memo_set((node_id, out_name), val)
        # 不再把 outputs 存进 node.live：该字段从不被读取，却会让每个数据节点常驻持有上一帧的
        # 输出（截图节点＝一整屏 numpy 图），白白占内存。值已记忆化在 ctx._memo（按帧清空）。
        return ctx.memo_get(key)

    def _resolve_inputs(self, ctx: ExecutionContext, node_id: str, node: Node) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for port in node.data_inputs():
            src = self.graph.data_source(node_id, port.name)
            inputs[port.name] = self._resolve(ctx, src[0], src[1]) if src else None
        return inputs

    def _execute_node(self, ctx: ExecutionContext, node_id: str, node: Node, inputs: dict):
        """执行一个控制节点，必要时计时。自身耗时只含 execute()——数据输入的求值
        （截图/OCR 等开销大头）已在 _resolve_inputs 里按各自的数据节点单独计时，不重复计。"""
        if ctx.profile_enabled:
            t0 = time.perf_counter()
            result = node.execute(ctx, inputs)
            ctx.profile_record(node_id, (time.perf_counter() - t0) * 1000.0)
            return result
        return node.execute(ctx, inputs)

    # ==================== 执行流：单帧 ====================
    def run_tick(self, ctx: ExecutionContext, dt: float = 0.0) -> None:
        ctx.graph = self.graph   # 少数节点需触达兄弟节点（如「关闭开关」改写另一个开关）
        ctx.begin_tick(dt)
        try:
            self._walk(ctx)
        finally:
            # 无论本帧如何结束，都释放可能持有的输入屏蔽与文件锁，避免跨帧泄漏
            ctx.cleanup_tick()

    def _walk(self, ctx: ExecutionContext) -> None:
        node_id: Optional[str] = self.graph.entry_id()
        guard = 0
        max_steps = len(self.graph.nodes) * 4 + 16  # 防御无限循环

        while node_id is not None:
            guard += 1
            if guard > max_steps:
                ctx.log("ERROR", f"执行步数超过上限({max_steps})，疑似存在环，已中断本帧")
                break

            node = self.graph.nodes[node_id]
            inputs = self._resolve_inputs(ctx, node_id, node)
            outputs, next_port = self._execute_node(ctx, node_id, node, inputs)

            # 控制节点的数据输出也写入记忆缓存，供下游拉取
            for out_name, val in outputs.items():
                ctx.memo_set((node_id, out_name), val)

            if next_port is None:
                break
            target = self.graph.exec_target(node_id, next_port)
            node_id = target[0] if target else None

    # ==================== 主循环（headless 用）====================
    def run(self, ctx: ExecutionContext, interval: float = 0.1, max_ticks: Optional[int] = None) -> None:  # noqa: D401
        count = 0
        try:
            while not ctx.cancel:
                start = time.time()
                self.run_tick(ctx, dt=interval)
                count += 1
                if max_ticks is not None and count >= max_ticks:
                    break
                remaining = interval - (time.time() - start)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            ctx.close_capture()   # 关掉本线程复用的 mss（与创建/grab 在同一线程）


class TraceExecutor(Executor):
    """带“执行轨迹记录”的执行器：供编辑器“运行可视化”用——记录本帧实际经过了哪些执行节点、
    各自走了哪个出口。数据线上的值则在帧结束后从 ctx.memo_snapshot() 读取。"""

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self.trace_path: list[str] = []        # 本帧按顺序经过的执行节点 id
        self.trace_ports: dict[str, str] = {}  # node_id -> 选择的出口名
        self.trace_data: set[str] = set()      # 本帧被求值过的【纯数据节点】id（供数据节点也能命中断点）

    def run_tick(self, ctx: ExecutionContext, dt: float = 0.0) -> None:
        self.trace_path = []
        self.trace_ports = {}
        self.trace_data = set()
        super().run_tick(ctx, dt)

    def _resolve(self, ctx: ExecutionContext, node_id: str, port: str):
        # 纯数据节点（算式/匹配/OCR…）不走执行流、不进 trace_path，原来断点永远命中不到。
        # 这里把本帧实际被拉取求值过的数据节点记下来，断点检查时与 trace_path 合并即可命中。
        val = super()._resolve(ctx, node_id, port)
        node = self.graph.nodes.get(node_id)
        if node is not None and not node.has_exec():
            self.trace_data.add(node_id)
        return val

    def _walk(self, ctx: ExecutionContext) -> None:
        node_id: Optional[str] = self.graph.entry_id()
        guard = 0
        max_steps = len(self.graph.nodes) * 4 + 16

        while node_id is not None:
            guard += 1
            if guard > max_steps:
                ctx.log("ERROR", f"执行步数超过上限({max_steps})，疑似存在环，已中断本帧")
                break

            node = self.graph.nodes[node_id]
            inputs = self._resolve_inputs(ctx, node_id, node)
            outputs, next_port = self._execute_node(ctx, node_id, node, inputs)
            self.trace_path.append(node_id)
            if next_port is not None:
                self.trace_ports[node_id] = next_port

            for out_name, val in outputs.items():
                ctx.memo_set((node_id, out_name), val)

            if next_port is None:
                break
            target = self.graph.exec_target(node_id, next_port)
            node_id = target[0] if target else None

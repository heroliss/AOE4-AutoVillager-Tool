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

        outputs = node.evaluate(ctx, inputs)
        for out_name, val in outputs.items():
            ctx.memo_set((node_id, out_name), val)
        node.live.update({"outputs": outputs})
        return ctx.memo_get(key)

    def _resolve_inputs(self, ctx: ExecutionContext, node_id: str, node: Node) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for port in node.data_inputs():
            src = self.graph.data_source(node_id, port.name)
            inputs[port.name] = self._resolve(ctx, src[0], src[1]) if src else None
        return inputs

    # ==================== 执行流：单帧 ====================
    def run_tick(self, ctx: ExecutionContext, dt: float = 0.0) -> None:
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
            outputs, next_port = node.execute(ctx, inputs)

            # 控制节点的数据输出也写入记忆缓存，供下游拉取
            for out_name, val in outputs.items():
                ctx.memo_set((node_id, out_name), val)

            if next_port is None:
                break
            target = self.graph.exec_target(node_id, next_port)
            node_id = target[0] if target else None

    # ==================== 主循环（headless 用）====================
    def run(self, ctx: ExecutionContext, interval: float = 0.1, max_ticks: Optional[int] = None) -> None:
        count = 0
        while not ctx.cancel:
            start = time.time()
            self.run_tick(ctx, dt=interval)
            count += 1
            if max_ticks is not None and count >= max_ticks:
                break
            remaining = interval - (time.time() - start)
            if remaining > 0:
                time.sleep(remaining)

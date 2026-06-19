"""
流程图自动排版（纯 Python，不依赖 DearPyGui）。

采用分层（longest-path layering）布局：把执行线与数据线合并成有向无环图，
每个节点的层 = 其所有前驱层的最大值 + 1；同层节点纵向排开。
于是入口/常量/传感器在最左，沿执行链向右展开，得到一张可读的初始版面，
用户再按需微调即可。
"""
from __future__ import annotations

from collections import deque


def layered_layout(graph, x_gap: float = 300.0, y_gap: float = 170.0,
                   x0: float = 30.0, y0: float = 30.0) -> None:
    """就地计算并写入 graph.positions。"""
    nodes = list(graph.nodes)
    if not nodes:
        return

    succ = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}
    for e in list(graph.exec_edges) + list(graph.data_edges):
        if e.src_id in succ and e.dst_id in indeg:
            succ[e.src_id].append(e.dst_id)
            indeg[e.dst_id] += 1

    # Kahn 拓扑序
    indeg2 = dict(indeg)
    q = deque([n for n in nodes if indeg2[n] == 0])
    topo = []
    while q:
        n = q.popleft()
        topo.append(n)
        for m in succ[n]:
            indeg2[m] -= 1
            if indeg2[m] == 0:
                q.append(m)
    for n in nodes:                 # 兜底：万一有环，未排到的补在末尾
        if n not in topo:
            topo.append(n)

    # 最长路径分层
    layer = {n: 0 for n in nodes}
    for n in topo:
        for m in succ[n]:
            if layer[m] < layer[n] + 1:
                layer[m] = layer[n] + 1

    by_layer: dict[int, list] = {}
    for n in nodes:
        by_layer.setdefault(layer[n], []).append(n)

    for lv in sorted(by_layer):
        for i, n in enumerate(by_layer[lv]):
            graph.positions[n] = (x0 + lv * x_gap, y0 + i * y_gap)


def needs_layout(graph) -> bool:
    """所有节点坐标都为原点（或缺失）时，认为需要自动排版。"""
    if not graph.nodes:
        return False
    return all(tuple(graph.positions.get(n, (0, 0))) == (0, 0) for n in graph.nodes)

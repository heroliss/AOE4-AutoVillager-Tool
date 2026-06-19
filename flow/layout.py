"""
流程图自动排版（纯 Python，不依赖 DearPyGui）。

高级分层布局（Sugiyama 思路 + 尺寸感知 + 折叠）：
1. 分层：合并执行线/数据线为 DAG，按最长路径给每个节点分层（保留执行顺序）。
2. 层内排序：用重心法（barycenter）做若干轮上下扫描，减少连线交叉。
3. 折叠：把很长的层序列折成多列（每列若干层，纵向堆叠），避免"一直朝一个方向太长"。
4. 坐标：按各节点的"估算尺寸"分配间距——列宽取该列最宽层、行高取该行最高层，
   因此不同大小的节点也不会重叠（可用 no_overlaps() 客观校验）。

尺寸默认按"标题/端口/参数文字宽度 + 行数"估算（中文按宽字符计）；编辑器也可注入
基于 DearPyGui 实测尺寸的 size_fn 以更精确。
"""
from __future__ import annotations

from collections import deque


def _text_w(s: str) -> float:
    # 中日韩等宽字符按 17px，其余按 9px（对应字体大小 18）
    return sum(17.0 if ord(c) > 0x2E80 else 9.0 for c in s)


def estimate_size(node) -> tuple[float, float]:
    """估算节点在画布上的宽高（像素）。"""
    title_w = _text_w(node.title) + 46
    port_w = max([_text_w(p.display) + 46 for p in (list(node.inputs) + list(node.outputs))] or [0])
    param_w = max([_text_w(p.label) + 196 for p in node.params] or [0])  # 标签 + 输入框宽度
    w = max(160.0, title_w, port_w, param_w)
    rows_io = len(node.inputs) + len(node.outputs)
    h = 36 + rows_io * 24 + len(node.params) * 30 + 16
    return (w, float(h))


def layered_layout(graph, size_fn=None, node_gap: float = 30.0, band_gap: float = 34.0,
                   col_gap: float = 60.0, x0: float = 40.0, y0: float = 40.0,
                   max_per_col: int = 9) -> None:
    """就地计算并写入 graph.positions（尺寸感知 + 交叉缩减 + 折叠成多列）。"""
    nodes = list(graph.nodes)
    if not nodes:
        return
    sizes = {n: (size_fn(n) if size_fn else estimate_size(graph.nodes[n])) for n in nodes}

    # —— 构图（去重）——
    succ = {n: [] for n in nodes}
    pred = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}
    seen = set()
    for e in list(graph.exec_edges) + list(graph.data_edges):
        if e.src_id in succ and e.dst_id in indeg and (e.src_id, e.dst_id) not in seen:
            seen.add((e.src_id, e.dst_id))
            succ[e.src_id].append(e.dst_id)
            pred[e.dst_id].append(e.src_id)
            indeg[e.dst_id] += 1

    # —— 拓扑序 + 最长路径分层 ——
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
    for n in nodes:                      # 兜底：有环时未排到的补末尾
        if n not in topo:
            topo.append(n)
    layer = {n: 0 for n in nodes}
    for n in topo:
        for m in succ[n]:
            if layer[m] < layer[n] + 1:
                layer[m] = layer[n] + 1

    # 把"无前驱的源节点"（常量/传感器等）右移到紧邻其最早的消费者，
    # 避免它们全堆在第 0 层导致首层过宽；末端死路节点(无后继)保持原位。
    for n in nodes:
        if indeg[n] == 0 and succ[n]:
            layer[n] = max(0, min(layer[s] for s in succ[n]) - 1)
    max_layer = max(layer.values())

    layers: dict[int, list] = {}
    for n in nodes:
        layers.setdefault(layer[n], []).append(n)
    L = sorted(layers)
    order = {lv: list(layers[lv]) for lv in L}
    index: dict[str, int] = {}

    def reindex():
        for lv in L:
            for i, n in enumerate(order[lv]):
                index[n] = i

    reindex()

    def bary(n, nbr_map, adj_layer):
        nb = [index[x] for x in nbr_map[n] if layer[x] == adj_layer]
        return sum(nb) / len(nb) if nb else float(index[n])

    # —— 重心法减少交叉（上下各扫描数轮）——
    for _ in range(4):
        for lv in L:
            if (lv - 1) in layers:
                order[lv].sort(key=lambda n: bary(n, pred, lv - 1))
        reindex()
        for lv in reversed(L):
            if (lv + 1) in layers:
                order[lv].sort(key=lambda n: bary(n, succ, lv + 1))
        reindex()

    # —— 折叠成多列：列数自适应，使版面宽高大致均衡 ——
    n_layers = max_layer + 1
    layer_w = {lv: (sum(sizes[n][0] for n in order[lv]) + node_gap * (len(order[lv]) - 1))
               for lv in L}
    layer_h = {lv: max(sizes[n][1] for n in order[lv]) for lv in L}
    avg_w = sum(layer_w.values()) / len(layer_w) + col_gap
    avg_h = sum(layer_h.values()) / len(layer_h) + band_gap
    # 单列总高 ≈ n_layers*avg_h；ncols 列时 宽≈ncols*avg_w、高≈(n_layers/ncols)*avg_h，
    # 令二者相等解出 ncols。
    ncols = max(1, round((n_layers * avg_h / avg_w) ** 0.5))
    per_col = max(1, min(max_per_col, -(-n_layers // ncols)))  # ceil 且不超过 max_per_col
    ncols = -(-n_layers // per_col)
    nbands = per_col

    band_h = [0.0] * nbands
    col_w = [0.0] * ncols
    for lv in L:
        c, r = divmod(lv, per_col)
        band_h[r] = max(band_h[r], layer_h[lv])
        col_w[c] = max(col_w[c], layer_w[lv])

    band_y = [0.0] * nbands
    acc = y0
    for r in range(nbands):
        band_y[r] = acc
        acc += band_h[r] + band_gap
    col_x = [0.0] * ncols
    accx = x0
    for c in range(ncols):
        col_x[c] = accx
        accx += col_w[c] + col_gap

    # —— 落位：层内左到右、按列纵向堆叠、行内垂直居中 ——
    for lv in L:
        c, r = divmod(lv, per_col)
        x = col_x[c]
        for n in order[lv]:
            w, h = sizes[n]
            graph.positions[n] = (x, band_y[r] + (band_h[r] - h) / 2.0)
            x += w + node_gap


def needs_layout(graph) -> bool:
    """所有节点坐标都为原点（或缺失）时，认为需要自动排版。"""
    if not graph.nodes:
        return False
    return all(tuple(graph.positions.get(n, (0, 0))) == (0, 0) for n in graph.nodes)


def no_overlaps(graph, size_fn=None) -> list:
    """返回相互重叠的节点对（用估算尺寸）；空列表表示无重叠。用于排版校验。"""
    rects = []
    for n in graph.nodes:
        x, y = graph.positions.get(n, (0, 0))
        w, h = (size_fn(n) if size_fn else estimate_size(graph.nodes[n]))
        rects.append((n, x, y, w, h))
    bad = []
    for i in range(len(rects)):
        ni, x1, y1, w1, h1 = rects[i]
        for j in range(i + 1, len(rects)):
            nj, x2, y2, w2, h2 = rects[j]
            if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                bad.append((ni, nj))
    return bad

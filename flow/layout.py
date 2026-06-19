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


def mainline_layout(graph, size_fn=None, node_gap: float = 26.0, branch_gap: float = 26.0,
                    col_gap: float = 56.0, row_gap: float = 80.0, x0: float = 40.0, y0: float = 40.0) -> None:
    """主线+分支式排版：执行流(控制节点)排成一条主线（蛇形折行），每个节点的数据来源
    节点作为"分支"竖直堆叠在其正上方。这样主线一眼可辨，数据连线大多朝同一方向短距汇入。

    - 列号：控制节点按执行流最长路径定列；数据节点归到其消费者所在列（堆在其上方）。
    - 折行：列按目标宽度折成多行，奇数行反向（蛇形），使行间衔接短。
    - 行内：控制节点对齐到该行底部（形成水平主线），数据分支自下而上按"距主线层数"堆叠。
    """
    nodes = list(graph.nodes)
    if not nodes:
        return
    sz = {n: (size_fn(n) if size_fn else estimate_size(graph.nodes[n])) for n in nodes}

    def is_exec(nid):
        nd = graph.nodes[nid]
        return any(p.kind.value == "exec" for p in list(nd.inputs) + list(nd.outputs))

    spine_set = {n for n in nodes if is_exec(n)}

    # —— 执行流邻接 + 最长路径列号（仅控制节点）——
    exsucc = {n: [] for n in nodes}
    ind = {n: 0 for n in nodes}
    for e in graph.exec_edges:
        if e.src_id in exsucc and e.dst_id in ind:
            exsucc[e.src_id].append(e.dst_id)
            ind[e.dst_id] += 1
    col = {n: 0 for n in spine_set}
    ind2 = {n: ind[n] for n in spine_set}
    q = deque(n for n in spine_set if ind2[n] == 0)
    topo = []
    while q:
        n = q.popleft()
        topo.append(n)
        for m in exsucc[n]:
            if m in ind2:
                ind2[m] -= 1
                if ind2[m] == 0:
                    q.append(m)
    for n in spine_set:
        if n not in topo:
            topo.append(n)
    for n in topo:
        for m in exsucc[n]:
            if m in col and col[m] < col[n] + 1:
                col[m] = col[n] + 1

    # —— 数据节点：列=消费者列；depth=到主线的数据链层数（越大越靠上）——
    cons = {n: [] for n in nodes}
    for e in list(graph.data_edges) + list(graph.exec_edges):
        if e.src_id in cons and e.dst_id in graph.nodes:
            cons[e.src_id].append(e.dst_id)
    cmemo, dmemo = {}, {}

    def dcol(n):
        if n in spine_set:
            return col[n]
        if n in cmemo:
            return cmemo[n]
        cmemo[n] = 0
        cs = cons.get(n, [])
        cmemo[n] = min((dcol(c) for c in cs), default=0)
        return cmemo[n]

    def ddepth(n):
        if n in spine_set:
            return 0
        if n in dmemo:
            return dmemo[n]
        dmemo[n] = 1
        cs = cons.get(n, [])
        dmemo[n] = 1 + max((ddepth(c) for c in cs), default=0)
        return dmemo[n]

    ncols = (max(col.values()) if col else 0) + 1
    columns = [[] for _ in range(ncols)]   # 每列：(节点, 是否主线, depth)
    for n in nodes:
        c = col[n] if n in spine_set else dcol(n)
        columns[c].append(n)

    # 每列度量：宽=列内最宽；主线块高 + 数据块高
    col_w = [0.0] * ncols
    spineH = [0.0] * ncols
    dataH = [0.0] * ncols
    col_spine = [[] for _ in range(ncols)]
    col_data = [[] for _ in range(ncols)]
    for c in range(ncols):
        for n in columns[c]:
            col_w[c] = max(col_w[c], sz[n][0])
            (col_spine if n in spine_set else col_data)[c].append(n)
        col_data[c].sort(key=lambda n: -ddepth(n))   # 越深越靠上
        col_spine[c].sort(key=lambda n: col[n])
        if col_spine[c]:
            spineH[c] = sum(sz[n][1] for n in col_spine[c]) + node_gap * (len(col_spine[c]) - 1)
        if col_data[c]:
            dataH[c] = sum(sz[n][1] for n in col_data[c]) + node_gap * (len(col_data[c]) - 1)
    colH = [spineH[c] + (branch_gap if dataH[c] and spineH[c] else 0) + dataH[c] for c in range(ncols)]

    # —— 折行：选每行列数使版面大致均衡 ——
    avg_w = sum(col_w) / ncols + col_gap
    max_h = max(colH) if colH else 1.0
    cols_per_row = max(1, round((ncols * (max_h + row_gap) / max(1.0, avg_w)) ** 0.5))
    rows = [list(range(i, min(i + cols_per_row, ncols))) for i in range(0, ncols, cols_per_row)]

    # —— 落位：主线对齐到各行底部；数据分支自下而上堆在其上方 ——
    y = y0
    for r, row_cols in enumerate(rows):
        rowH = max((colH[c] for c in row_cols), default=0.0)
        # 蛇形：奇数行反向放置（视觉上行间衔接更短）
        placed = list(reversed(row_cols)) if (r % 2) else row_cols
        x = x0
        for c in placed:
            spine_top = y + rowH - spineH[c]      # 主线块顶部（块底贴行底）
            yy = spine_top
            for n in col_spine[c]:
                w, h = sz[n]
                graph.positions[n] = (x + (col_w[c] - w) / 2.0, yy)
                yy += h + node_gap
            # 数据分支：堆在主线上方
            yb = spine_top - branch_gap
            for n in reversed(col_data[c]):       # 靠近主线的(depth小)在下
                w, h = sz[n]
                yb -= h
                graph.positions[n] = (x + (col_w[c] - w) / 2.0, yb)
                yb -= node_gap
            x += col_w[c] + col_gap
        y += rowH + row_gap


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

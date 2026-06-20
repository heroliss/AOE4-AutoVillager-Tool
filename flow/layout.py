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

import math
from collections import deque

# LiteGraph 的节点高度常量（须与 vendor/litegraph.js 一致）：本体高 = 标题 + 端口行 + 控件行。
# estimate_size 必须按"实际会画出的控件数（含采集按钮）"算高，否则排版预留 < 实际渲染 -> 重叠。
TITLE_H = 30.0        # NODE_TITLE_HEIGHT：标题条（画在 pos.y 之上）
SLOT_H = 20.0         # NODE_SLOT_HEIGHT：每个端口行高
WIDGET_H = 20.0       # NODE_WIDGET_HEIGHT：每个控件行高（实际占用 WIDGET_H+4）
MIN_W_WIDGETS = 210.0  # 含控件时 LiteGraph 的最小宽 NODE_WIDTH(140)*1.5
BODY_MARGIN = 6.0     # LiteGraph.computeSize 末尾固定 size[1]+=6 的边距；不算进来会少 6px 导致轻微重叠

# 节点下方"附属卡片"（模板缩略图网格 + 用户描述）的尺寸常量，必须与 editor.js 的画法保持一致，
# 否则自动排版预留的高度与实际渲染对不上、会重叠或留白。（编辑/增删图片用节点上的"编辑列表…"按钮。）
THUMB = 44.0          # 缩略图边长
THUMB_GAP = 6.0       # 缩略图间距
CARD_PAD = 8.0        # 卡片内边距
CARD_GAP = 4.0        # 卡片与节点之间的缝
NOTE_LH = 16.0        # 描述行高
DIVIDER = 6.0         # 描述与列表之间的分隔
PREVIEW_CAP = 12      # 最多显示的图片行数（多出来用 "+N" 表示）


def _text_w(s: str) -> float:
    # 中日韩等宽字符按 17px，其余按 9px（对应字体大小 18）
    return sum(17.0 if ord(c) > 0x2E80 else 9.0 for c in s)


def _button_count(node) -> int:
    """该节点会被 editor.js(addParamWidget) 额外加多少个"采集"按钮控件——必须与前端规则一致。"""
    color_keys = {s.key for s in node.params if getattr(s, "ptype", None) == "color"}
    n = 0
    for s in node.params:
        t = getattr(s, "ptype", None)
        if t in ("template", "templates"):      # 选择图片… + 截取模板…
            n += 2
        elif t == "region":                      # 框选区域…
            n += 1
        elif t == "point":                       # 取点…（若配套有颜色参数则由"取点吸色"代劳，不另放）
            if s.key.replace("pixel", "color") not in color_keys:
                n += 1
        elif t in ("color", "key", "keys"):      # 取点吸色… / 捕获按键… / 选择修饰键…
            n += 1
    return n


def estimate_size(node) -> tuple[float, float]:
    """估算节点"本体"（标题+端口+控件）在画布上的宽高（像素），不含下方附属卡片。

    与 LiteGraph.computeSize 同公式：高 = 标题 + max(入,出)行*SLOT + 控件数*(WIDGET+4)+8，
    控件数含参数控件【和】采集按钮，否则按钮多出来的高度没预留 -> 与下方节点重叠。
    """
    title_w = _text_w(node.title) + 46
    port_w = max([_text_w(p.display) + 46 for p in (list(node.inputs) + list(node.outputs))] or [0])
    param_w = max([_text_w(p.label) + 196 for p in node.params] or [0])  # 标签 + 输入框宽度
    w = max(160.0, title_w, port_w, param_w)
    n_widgets = len(node.params) + _button_count(node)
    if n_widgets:
        w = max(w, MIN_W_WIDGETS)
    rows = max(len(node.inputs), len(node.outputs), 1)
    body = rows * SLOT_H
    if n_widgets:
        body += n_widgets * (WIDGET_H + 4) + 8
    body += BODY_MARGIN     # 与 LiteGraph 末尾的 size[1]+=6 对齐
    return (w, TITLE_H + body)


def _template_paths(node) -> list:
    """节点上 template/templates 参数当前指向的图片路径（用于估算预览高度）。"""
    out = []
    for s in node.params:
        if getattr(s, "ptype", None) in ("template", "templates"):
            v = node.values.get(s.key)
            if isinstance(v, (list, tuple)):
                out += [p for p in v if p]
            elif v:
                out += [p for p in str(v).split(",") if p.strip()]
    return out


def _card_h(node, note: str, width: float) -> float:
    """节点下方附属卡片（描述 + 模板缩略图网格）的总占高；无内容返回 0。与 editor.js 的卡片画法对应。"""
    inner = max(1.0, width - 2 * CARD_PAD)
    note_h = 0.0
    if note:
        lines = 0
        prefix = _text_w("📝 ")
        for i, para in enumerate(str(note).split("\n")):
            tw = (prefix if i == 0 else 0.0) + _text_w(para)
            lines += max(1, math.ceil(tw / inner))
        note_h = lines * NOTE_LH
    paths = _template_paths(node)
    prev_h = 0.0
    if paths:
        per_row = max(1, int(inner // (THUMB + THUMB_GAP)))
        shown = min(len(paths), PREVIEW_CAP)
        rows = math.ceil(shown / per_row)
        prev_h = rows * (THUMB + THUMB_GAP)
        if len(paths) > shown:
            prev_h += NOTE_LH        # "+N 张" 提示行
    if note_h == 0 and prev_h == 0:
        return 0.0
    total = CARD_PAD + note_h + prev_h + CARD_PAD
    if note_h and prev_h:
        total += DIVIDER
    return CARD_GAP + total


def full_size(graph, nid, size_fn=None) -> tuple[float, float]:
    """节点的"占位尺寸" = 本体（含标题）+ 下方附属卡片。排版与重叠校验都用它，给预览/描述留空间。"""
    node = graph.nodes[nid]
    w, h = (size_fn(nid) if size_fn else estimate_size(node))
    return (w, h + _card_h(node, graph.notes.get(nid, ""), w))


def layered_layout(graph, size_fn=None, node_gap: float = 30.0, band_gap: float = 34.0,
                   col_gap: float = 60.0, x0: float = 40.0, y0: float = 40.0,
                   max_per_col: int = 9) -> None:
    """就地计算并写入 graph.positions（尺寸感知 + 交叉缩减 + 折叠成多列）。"""
    nodes = list(graph.nodes)
    if not nodes:
        return
    sizes = {n: full_size(graph, n, size_fn) for n in nodes}

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


def mainline_layout(graph, size_fn=None, node_gap: float = 40.0, branch_gap: float = 48.0,
                    col_gap: float = 80.0, x0: float = 40.0, y0: float = 40.0) -> None:
    """主线+分支式排版：执行流(控制节点)排成一条【始终向右、不折行】的笔直主线；每个节点的
    数据来源放在它【更靠前的列、主线下方】，使数据连线一律从左向右、且不跨越白色主线地汇入；
    支线再按"接入消费者的连接点高度"对齐排列，尽量避免连线扭麻花。

    - 主线列号：控制节点按执行流最长路径定列，单行从左到右铺开（宽就宽，但主线一眼可辨）。
    - 支线列号：数据节点放在"消费者列 - 1"，链式来源逐级再向左 —— 来源在前、消费在后。
    - 落位：先放主线（各列块顶对齐成一条水平线）；再从右往左放支线（保证消费者已就位），
      一律挂到主线下方、按消费者输入连接点高度排序——接入点高者贴近主线、低者更靠下。
      （数据端口都在节点"进入"口之下，支线从下方汇入才不会与主线交叉。）
    """
    nodes = list(graph.nodes)
    if not nodes:
        return
    sz = {n: full_size(graph, n, size_fn) for n in nodes}

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

    # —— 分组感知：把“有归属分组”的数据节点限制在其分组(脊柱)所在的列区间内 ——
    # 否则一个只被很靠后的段消费的共享数据节点（如商队用的常量）会被甩到很右，
    # 导致它所在分组的外框横跨整张图、与其它分组框相互重叠（分组就失去意义了）。
    gindex = {}                          # node_id -> 分组下标
    for gi, gr in enumerate(getattr(graph, "groups", []) or []):
        for m in (gr.get("members", []) if isinstance(gr, dict) else []):
            gindex[m] = gi
    gspan = {}                           # 分组下标 -> (脊柱最小列, 脊柱最大列)
    for m in spine_set:
        gi = gindex.get(m)
        if gi is not None:
            lo, hi = gspan.get(gi, (col[m], col[m]))
            gspan[gi] = (min(lo, col[m]), max(hi, col[m]))

    # —— 数据节点：列 = 其(数据)消费者最左列 - 1（来源在前），链式逐级再左移；按分组列区间钳制 ——
    dcons = {n: [] for n in nodes}      # 数据来源 -> 其数据消费者列表
    for e in graph.data_edges:
        if e.src_id in dcons and e.dst_id in graph.nodes:
            dcons[e.src_id].append(e.dst_id)
    cmemo = {}

    def dcol(n):
        if n in spine_set:
            return col[n]
        if n in cmemo:
            return cmemo[n]
        cmemo[n] = 0                    # 防御环：先占位
        cs = dcons.get(n, [])
        c = min((dcol(c2) for c2 in cs), default=1) - 1
        span = gspan.get(gindex.get(n))   # 有归属分组且该组有脊柱节点 -> 钳制到该组列区间内
        if span:
            c = max(span[0], min(span[1], c))
        cmemo[n] = c
        return c

    for n in nodes:
        if n not in spine_set:
            col[n] = dcol(n)

    # 归一化列号从 0 开始（数据链可能产生负列）
    base = min(col.values())
    for n in col:
        col[n] -= base

    # —— 列 x 坐标 + 列宽 ——
    by_col = {}
    for n in nodes:
        by_col.setdefault(col[n], []).append(n)
    cols_sorted = sorted(by_col)
    col_w = {c: max(sz[n][0] for n in by_col[c]) for c in cols_sorted}
    col_x, x = {}, x0
    for c in cols_sorted:
        col_x[c] = x
        x += col_w[c] + col_gap

    # —— 先放主线：各列控制节点块顶对齐到同一条线，形成一条笔直主线 ——
    Y_SPINE = 0.0
    col_spine = {c: sorted((n for n in by_col[c] if n in spine_set), key=lambda n: col[n])
                 for c in cols_sorted}
    spine_top, spine_bot = {}, {}
    for c in cols_sorted:
        yy = Y_SPINE
        for n in col_spine[c]:
            w, h = sz[n]
            graph.positions[n] = (col_x[c] + (col_w[c] - w) / 2.0, yy)
            yy += h + node_gap
        spine_top[c] = Y_SPINE
        spine_bot[c] = (yy - node_gap) if col_spine[c] else Y_SPINE

    # 估算某输入端口的纵坐标（用于按"连接点上下位置"对齐，避免连线扭麻花）
    def in_port_y(node_id, port_name):
        nd = graph.nodes[node_id]
        idx = next((i for i, p in enumerate(nd.inputs) if p.name == port_name), 0)
        return graph.positions[node_id][1] + 30.0 + idx * 24.0 + 12.0

    def anchor_y(n):
        # 该来源接入的各消费者"输入连接点"的平均高度（消费者在右、已就位）
        ys = [in_port_y(e.dst_id, e.dst_port) for e in graph.data_edges
              if e.src_id == n and e.dst_id in graph.positions]
        return sum(ys) / len(ys) if ys else Y_SPINE

    # —— 再放数据支线：从右往左逐列（保证消费者已就位），一律挂在主线【下方】，按"接入消费者
    #    连接点的高度"排序——接入点高者贴近主线、低者更靠下。全部在下方是为了不跨越白色主线：
    #    数据输入端口都在节点的执行口(进入)之下，支线在下方汇入才不会与主线交叉、不扭麻花。——
    for c in reversed(cols_sorted):
        da = [n for n in by_col[c] if n not in spine_set]
        if not da:
            continue
        da.sort(key=anchor_y)
        yb = spine_bot[c] + branch_gap
        for n in da:
            w, h = sz[n]
            graph.positions[n] = (col_x[c] + (col_w[c] - w) / 2.0, yb)
            yb += h + node_gap

    # 整体平移，使最上沿（主线）对齐到 y0
    if graph.positions:
        dy = y0 - min(p[1] for p in graph.positions.values())
        for n in graph.positions:
            px, py = graph.positions[n]
            graph.positions[n] = (px, py + dy)


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
        w, h = full_size(graph, n, size_fn)
        rects.append((n, x, y, w, h))
    bad = []
    for i in range(len(rects)):
        ni, x1, y1, w1, h1 = rects[i]
        for j in range(i + 1, len(rects)):
            nj, x2, y2, w2, h2 = rects[j]
            if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                bad.append((ni, nj))
    return bad

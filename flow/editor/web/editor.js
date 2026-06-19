// AOE4 Flow Editor —— LiteGraph 前端胶水
// 职责：注册 LiteGraph 节点类型并建图；编辑后回传保存。
// 启动数据（节点定义/初始流程/内置列表）由 Python 主动 push 进来（window.__bootstrap__），
// 若未收到再回退到 js_api 拉取。连线 exec/data 归类、参数类型回解析都在 Python 侧完成。

const ED = (function () {
  let graph, canvas, defs = [];
  let typeKeyByType = {};   // our type_id -> LiteGraph 注册名
  let defByType = {};       // our type_id -> 定义（含 help/参数说明），用于选中节点时显示说明
  let helpEl = null;
  let seq = 1;
  let booted = false;
  // 撤销/重做（快照式）
  let undoStack = [], redoStack = [], snapTimer = null, suppressSnap = false, building = false;

  // ---- 错误可视化（界面里看不到控制台，所以把错误显示出来）----
  function showError(msg) {
    try { console.error(msg); } catch (e) {}
    let el = document.getElementById("errbox");
    if (!el) {
      el = document.createElement("div");
      el.id = "errbox";
      el.style.cssText = "position:absolute;left:10px;bottom:10px;max-width:70%;max-height:45%;" +
        "overflow:auto;background:#3a1212;color:#ffd7d7;border:1px solid #a33;border-radius:6px;" +
        "padding:8px 10px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;z-index:9999;";
      el.title = "点击关闭";
      el.onclick = () => el.remove();
      document.body.appendChild(el);
    }
    el.textContent = "⚠ 出错（点击关闭）：\n" + msg;
  }
  window.addEventListener("error", (e) =>
    showError((e.message || e.error || "脚本错误") + "\n  " + (e.filename || "") + ":" + (e.lineno || "")));
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    showError("Promise 被拒绝：" + (r && (r.stack || r.message) || r));
  });

  // 连线按类型上色
  function setupColors() {
    const C = LGraphCanvas.link_type_colors;
    C["exec"] = "#C9C97A";
    C["number"] = "#7AB0EE";
    C["bool"] = "#E0A85A";
    C["string"] = "#9AD08A";
    C["image"] = "#C792DF";
    C["region"] = "#69b0a0"; C["point"] = "#69b0a0"; C["color"] = "#cf8a6a";
  }

  function slotType(p) {
    if (p.kind === "exec") return "exec";
    return p.dtype === "any" ? 0 : p.dtype;   // 0 = 通配
  }

  // 文本像素宽（CJK 17、其余 9），与 layout.py 的估算一致，保证编辑器节点宽度=排版预留宽度
  function textW(s) {
    let w = 0;
    for (const c of String(s == null ? "" : s)) w += c.charCodeAt(0) > 0x2E80 ? 17 : 9;
    return w;
  }
  // 节点最小宽度：容纳 标题 / 端口名 / "参数名+输入框"，与 layout.estimate_size 同公式
  function nodeMinWidth(def) {
    if (!def) return 160;
    let w = textW(def.title) + 46;
    for (const p of (def.inputs || []).concat(def.outputs || [])) w = Math.max(w, textW(p.label) + 46);
    for (const p of (def.params || [])) w = Math.max(w, textW(p.label) + 196);
    return Math.max(160, w);
  }

  // 精简/定制 LiteGraph 菜单与交互（去重、去掉用不到的 group/subgraph、连线菜单不再混入"添加节点"）
  function installEditorTweaks() {
    // 连线中点菜单：只留"删除连线"（不再混入与空白处重复的 Add Node）
    LGraphCanvas.prototype.showLinkMenu = function (link, e) {
      const that = this;
      new LiteGraph.ContextMenu(["删除连线"], {
        event: e, title: "连线",
        callback: (v) => { if (v === "删除连线") that.graph.removeLink(link.id); },
      });
      return false;
    };
    // 画布空白右键：只留"添加节点"（去掉 Add Group / Align / 子图等）
    LGraphCanvas.prototype.getCanvasMenuOptions = function () {
      return [{ content: "添加节点", has_submenu: true, callback: LGraphCanvas.onMenuAdd }];
    };
    // 节点右键：精简到 克隆 / 删除（去掉 Inputs/Outputs/Properties/Title/Mode/Resize/Collapse/Pin/Colors/Shapes 等堆叠项）
    LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
      const opts = [];
      if (node.clonable !== false)
        opts.push({ content: "克隆", callback: LGraphCanvas.onMenuNodeClone });
      opts.push({
        content: "删除",
        disabled: !(node.removable !== false && !node.block_delete),
        callback: LGraphCanvas.onMenuNodeRemove,
      });
      return opts;
    };
    // 值编辑浮框：去掉 OK 按钮，输入即"实时生效"；回车/失焦/点外部即关闭。
    LGraphCanvas.prototype.prompt = function (title, value, callback, event, multiline) {
      const that = this;
      const dialog = document.createElement("div");
      dialog.className = "graphdialog rounded";
      dialog.innerHTML = multiline
        ? "<span class='name'></span><textarea autofocus class='value'></textarea>"
        : "<span class='name'></span><input autofocus type='text' class='value'/>";
      let closed = false;
      dialog.close = function () {
        if (closed) return;             // 失焦/点外部/回车可能重复触发，幂等避免 removeChild 抛错
        closed = true;
        if (that.prompt_box === dialog) that.prompt_box = null;
        dialog.remove();                // 安全：已移除时为 no-op
      };
      const canvasEl = this.canvas;
      canvasEl.parentNode.appendChild(dialog);
      if (this.ds.scale > 1) dialog.style.transform = "scale(" + this.ds.scale + ")";
      if (that.prompt_box) that.prompt_box.close();
      that.prompt_box = dialog;

      dialog.querySelector(".name").innerText = title || "";
      const input = dialog.querySelector(".value");
      input.value = value == null ? "" : value;

      const commit = () => { try { if (callback) callback(input.value); } catch (e) {} that.dirty_canvas = true; };
      input.addEventListener("input", commit);          // 实时生效
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();                            // 不触发画布快捷键/撤销
        if (e.keyCode === 27) dialog.close();           // Esc 关闭
        else if (e.keyCode === 13 && !multiline) { commit(); dialog.close(); e.preventDefault(); }  // 回车提交并关闭
      });
      input.addEventListener("focusout", () => { commit(); dialog.close(); });  // 失焦提交并关闭

      const rect = canvasEl.getBoundingClientRect();
      if (event) {
        dialog.style.left = (event.clientX - rect.left - 20) + "px";
        dialog.style.top = (event.clientY - rect.top) + "px";
      } else {
        dialog.style.left = (canvasEl.width * 0.5 - 40) + "px";
        dialog.style.top = (canvasEl.height * 0.5) + "px";
      }
      setTimeout(() => { input.focus(); if (!multiline) input.select(); }, 10);
      return dialog;
    };

    // 超长文本参数在节点上"截断显示"（带省略号），不改变真实值（编辑/保存仍是完整值）。
    // 按"该控件可用值宽 = 节点宽 - 参数名宽 - 留白"计算可显示字数，避免与参数名重叠。
    const _origDrawWidgets = LGraphCanvas.prototype.drawNodeWidgets;
    LGraphCanvas.prototype.drawNodeWidgets = function (node, posY, ctx, active_widget) {
      const saved = [];
      for (const w of (node.widgets || [])) {
        if ((w.type === "text" || w.type === "string") && w.value != null) {
          const avail = node.size[0] - textW(w.name) - 40;
          const maxChars = Math.max(3, Math.floor(avail / 8));
          const sv = String(w.value);
          if (sv.length > maxChars) { saved.push([w, w.value]); w.value = sv.slice(0, maxChars) + "…"; }
        }
      }
      try { _origDrawWidgets.call(this, node, posY, ctx, active_widget); }
      finally { for (const s of saved) s[0].value = s[1]; }
    };

    // 右键菜单过高时"内部滚动"而非整体平移：LiteGraph 给菜单根加了 wheel 监听把整个菜单
    // top 上下移动，这里在菜单构造期吞掉 wheel/mousewheel 监听，并设最大高度+overflow，
    // 让滚轮滚动内容。（用临时替换 addEventListener 实现，构造结束即还原。）
    if (!LiteGraph.__menuScrollPatched) {
      LiteGraph.__menuScrollPatched = true;
      const OrigCM = LiteGraph.ContextMenu;
      const CM = function (values, options) {
        const proto = Element.prototype, origAdd = proto.addEventListener;
        proto.addEventListener = function (type, fn, opts) {
          if (type === "wheel" || type === "mousewheel") return;
          return origAdd.call(this, type, fn, opts);
        };
        try { OrigCM.call(this, values, options); }
        finally { proto.addEventListener = origAdd; }
        if (this.root) {
          this.root.style.maxHeight = "84vh";
          this.root.style.overflowY = "auto";
          this.root.style.overflowX = "hidden";
        }
        return this;
      };
      CM.prototype = OrigCM.prototype;
      for (const k in OrigCM) { try { CM[k] = OrigCM[k]; } catch (e) {} }
      LiteGraph.ContextMenu = CM;
    }

    // 多选后整体拖动：点击"已在多选中的节点"且无修饰键时，保留整个多选（默认会清空只留这个，导致只拖动一个）
    const _origProcNodeSel = LGraphCanvas.prototype.processNodeSelected;
    LGraphCanvas.prototype.processNodeSelected = function (node, e) {
      if (node && this.selected_nodes && this.selected_nodes[node.id] &&
          Object.keys(this.selected_nodes).length > 1 &&
          !(e && (e.shiftKey || e.ctrlKey || e.metaKey))) {
        if (this.onNodeSelected) this.onNodeSelected(node);
        return;   // 保留多选，使后续拖动移动全部选中节点
      }
      return _origProcNodeSel.call(this, node, e);
    };

    // 右键菜单过高时内部滚动（而不是整体上下移动）
    if (!document.getElementById("aoe4-style")) {
      const st = document.createElement("style");
      st.id = "aoe4-style";
      st.textContent = ".litecontextmenu{max-height:84vh!important;overflow-y:auto!important;overflow-x:hidden;}";
      document.head.appendChild(st);
    }

    // 编辑值的浮动输入框：点击其外部即关闭（符合"所有弹窗点外部关闭"的预期）
    if (!window.__dlgCloseHooked) {
      window.__dlgCloseHooked = true;
      document.addEventListener("pointerdown", (e) => {
        document.querySelectorAll(".graphdialog").forEach((d) => {
          if (!d.contains(e.target) && typeof d.close === "function") d.close();
        });
      }, true);
    }
  }

  function addParamWidget(node, p) {
    // 构造期（new base_class）createNode 尚未给 node.properties 赋初值，需自行兜底，
    // 否则末尾 node.properties[key]=... 会抛 TypeError，导致带参数的节点整体创建失败。
    if (!node.properties) node.properties = {};
    const cb = (v) => { if (!node.properties) node.properties = {}; node.properties[p.key] = v; scheduleSnap(); };
    let w;
    if (p.ptype === "int")
      w = node.addWidget("number", p.label, Number(p.default ?? 0), cb, { step: 10, precision: 0 });
    else if (p.ptype === "float")
      w = node.addWidget("number", p.label, Number(p.default ?? 0), cb, { step: (p.step || 0.1) * 10, precision: 2 });
    else if (p.ptype === "bool")
      w = node.addWidget("toggle", p.label, !!p.default, cb);
    else if (p.ptype === "enum")
      w = node.addWidget("combo", p.label, String(p.default ?? ""), cb, { values: (p.choices || []).map(String) });
    else
      w = node.addWidget("text", p.label, p.default == null ? "" : String(p.default), cb);
    w._key = p.key;                  // 保存时用作参数键（不动 w.name，它是显示标签）
    node.properties[p.key] = w.value;
  }

  function registerTypes(list) {
    defs = list || [];
    // 只保留本工具的节点：清掉 litegraph 自带的 const/subgraph/group 等，避免"添加节点"菜单混乱、与本工具结构重复
    LiteGraph.registered_node_types = {};
    LiteGraph.Nodes = {};
    let okN = 0, failN = 0, lastErr = "";
    for (const def of defs) {
      try {
        const key = def.category + "/" + def.title;   // 顶层菜单直接按中文分类（事件/数据/逻辑/…）
        typeKeyByType[def.type] = key;
        defByType[def.type] = def;
        const D = def;
        const Ctor = function () {
          this.title = D.title;
          for (const p of D.inputs) this.addInput(p.name, slotType(p), { label: p.label });
          for (const p of D.params) addParamWidget(this, p);
          for (const p of D.outputs) this.addOutput(p.name, slotType(p), { label: p.label });
          this.size[0] = Math.max(this.size[0] || 0, nodeMinWidth(D));  // 加宽容纳"参数名+值"，与排版预留一致
          this._typeId = D.type;
          if (!this._id) this._id = D.type.split(".").pop() + "_" + (seq++);
        };
        Ctor.title = def.title;
        if (!LiteGraph.registered_node_types[key])
          LiteGraph.registerNodeType(key, Ctor);
        okN++;
      } catch (err) {
        failN++; lastErr = (def && def.type) + " → " + (err && (err.stack || err.message) || err);
      }
    }
    if (failN) showError(`注册节点类型失败 ${failN}/${defs.length}，最后一个：\n${lastErr}`);
    return okN;
  }

  function buildGraph(flow) {
    if (!graph) return 0;
    building = true;   // 建图期间抑制撤销快照
    graph.clear();
    const idMap = {};
    const missing = {};
    let added = 0, lastErr = "";
    for (const nd of flow.nodes || []) {
      try {
        const key = typeKeyByType[nd.type];
        if (!key) { missing[nd.type] = (missing[nd.type] || 0) + 1; continue; }
        const n = LiteGraph.createNode(key);
        if (!n) { missing[nd.type] = (missing[nd.type] || 0) + 1; continue; }
        n._id = nd.id;
        n._typeId = nd.type;
        n.pos = [nd.pos ? nd.pos[0] : 0, nd.pos ? nd.pos[1] : 0];
        for (const w of (n.widgets || [])) {
          if (nd.params && w._key in nd.params) {
            let v = nd.params[w._key];
            w.value = (w.type === "combo") ? String(v) : v;
            n.properties[w._key] = w.value;
          }
        }
        graph.add(n);
        idMap[nd.id] = n;
        added++;
      } catch (err) {
        lastErr = (nd && nd.type) + " → " + (err && (err.stack || err.message) || err);
      }
    }
    for (const e of flow.edges || []) {
      try {
        const a = idMap[e.src], b = idMap[e.dst];
        if (!a || !b) continue;
        const so = a.findOutputSlot(e.src_port);
        const si = b.findInputSlot(e.dst_port);
        if (so >= 0 && si >= 0) a.connect(so, b, si);
      } catch (err) { /* 单条连线失败不致命 */ }
    }
    graph.setDirtyCanvas(true, true);
    const miss = Object.keys(missing);
    if (miss.length)
      showError("未注册的节点类型（未渲染）：" + miss.map((t) => t + "×" + missing[t]).join(", "));
    if (lastErr) showError("建图时有节点失败，最后一个：\n" + lastErr);
    building = false;
    return added;
  }

  function collect() {
    const nodes = [], edges = [];
    for (const n of graph._nodes) {
      const params = {};
      for (const w of (n.widgets || [])) params[w._key] = w.value;
      nodes.push({ id: n._id, type: n._typeId, pos: [Math.round(n.pos[0]), Math.round(n.pos[1])], params });
    }
    for (const k in graph.links) {
      const l = graph.links[k];
      if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b) continue;
      edges.push({
        src: a._id, src_port: a.outputs[l.origin_slot].name,
        dst: b._id, dst_port: b.inputs[l.target_slot].name,
      });
    }
    return { name: graph._aoe4_name || "未命名流程", nodes, edges };
  }

  function setStatus(t) { document.getElementById("status").textContent = t; }

  // minScale：可读下限。适应窗口按钮用 0.15（真·全图）；载入用较大的下限（可读，
  // 大图放不下时锚定左上角=流程起点，用户再平移/缩放）。
  function fit(minScale) {
    const ns = graph._nodes;
    if (!ns.length) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of ns) {
      x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1] - 20);
      x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, n.pos[1] + n.size[1]);
    }
    const cw = canvas.canvas.width, ch = canvas.canvas.height;
    const lo = (typeof minScale === "number") ? minScale : 0.15;
    const s = Math.max(lo, Math.min(1.4, Math.min((cw - 60) / (x1 - x0), (ch - 60) / (y1 - y0))));
    canvas.ds.scale = s;
    canvas.ds.offset = [-x0 + 30 / s, -y0 + 30 / s];
    canvas.setDirty(true, true);
  }

  // ---- 与 Python 交互 ----
  function api() {
    if (!(window.pywebview && window.pywebview.api))
      throw new Error("pywebview.api 尚不可用");
    return window.pywebview.api;
  }

  function load(flow) {
    graph._aoe4_name = flow.name;
    undoStack = []; redoStack = [];   // 新流程：清空撤销历史
    const added = buildGraph(flow);
    const total = (flow.nodes || []).length;
    fit(0.5);   // 载入用可读下限；适应窗口按钮(ED.fit())仍为真·全图
    setStatus(`流程：${flow.name} ｜ 节点 ${added}/${total}`);
    snapshotNow();   // 记录初始快照，作为撤销的基线
  }

  // ---- 启动：优先用 Python push 进来的数据 ----
  function boot(data) {
    if (booted) return;
    booted = true;
    try {
      const okN = registerTypes(data.defs);
      const sel = document.getElementById("builtin");
      for (const p of (data.builtin || [])) {
        const o = document.createElement("option"); o.value = p; o.textContent = p; sel.appendChild(o);
      }
      if (data.flow) load(data.flow);
      else setStatus(`已就绪 ｜ 已注册 ${okN} 种节点（右键空白处添加）`);
    } catch (err) {
      showError("启动失败：\n" + (err && (err.stack || err.message) || err));
    }
  }
  window.__bootstrap__ = function (data) { boot(data); return true; };

  const self = {
    async save() {
      try {
        const p = await api().save(collect());
        setStatus(p ? `已保存 ${p}` : "已取消保存");
      } catch (err) { showError("保存失败：" + (err.stack || err)); }
    },
    async saveAs() {
      try {
        const p = await api().save_as(collect());
        setStatus(p ? `已保存 ${p}` : "已取消");
      } catch (err) { showError("另存为失败：" + (err.stack || err)); }
    },
    async open() {
      try {
        const flow = await api().open_dialog();
        if (flow) load(flow);
      } catch (err) { showError("打开失败：" + (err.stack || err)); }
    },
    async openBuiltin(path) {
      if (!path) return;
      try {
        const flow = await api().open_path(path);
        if (flow) load(flow);
      } catch (err) { showError("打开内置流程失败：" + (err.stack || err)); }
    },
    async autolayout() {
      try {
        const flow = await api().autolayout(collect());
        if (flow) load(flow);
      } catch (err) { showError("自动排版失败：" + (err.stack || err)); }
    },
    fit,
  };

  function resize() {
    const w = document.getElementById("wrap");
    const c = document.getElementById("graph");
    c.width = w.clientWidth; c.height = w.clientHeight;
    if (canvas) { canvas.resize(); canvas.setDirty(true, true); }
  }

  // ---- 选中节点时显示中文说明（节点简介 + 各参数用法）----
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function setupHelpPanel() {
    helpEl = document.createElement("div");
    helpEl.id = "helpbox";
    helpEl.style.cssText = "position:absolute;right:10px;bottom:10px;max-width:340px;max-height:50%;" +
      "overflow:auto;background:#23272fee;color:#cfd3da;border:1px solid #3a404a;border-radius:6px;" +
      "padding:8px 10px;font:12px/1.6 'Microsoft YaHei',sans-serif;display:none;z-index:50;";
    document.body.appendChild(helpEl);
    canvas.onNodeSelected = showNodeHelp;
    canvas.onNodeDeselected = () => { helpEl.style.display = "none"; };
  }

  function showNodeHelp(node) {
    if (!helpEl) return;
    const d = defByType[node && node._typeId];
    if (!d) { helpEl.style.display = "none"; return; }
    let html = `<div style="font-weight:bold;color:#e6e9ee;margin-bottom:2px">${esc(d.title)}</div>`;
    if (d.help) html += `<div style="color:#9aa3af;margin-bottom:4px">${esc(d.help)}</div>`;
    const ps = (d.params || []).filter((p) => p.help);
    if (ps.length) {
      html += `<div style="color:#7f8895;border-top:1px solid #3a404a;margin-top:4px;padding-top:4px">参数说明</div>`;
      for (const p of ps)
        html += `<div style="margin-top:2px"><b style="color:#bcd">${esc(p.label)}</b>：${esc(p.help)}</div>`;
    }
    helpEl.innerHTML = html;
    helpEl.style.display = "block";
  }

  // ---- 撤销/重做（对整图做 JSON 快照；buildGraph/applySnapshot 期间抑制）----
  function snapshotNow() {
    if (suppressSnap || building || !graph) return;
    const s = JSON.stringify(collect());
    if (undoStack.length && undoStack[undoStack.length - 1] === s) return;
    undoStack.push(s);
    if (undoStack.length > 100) undoStack.shift();
    redoStack = [];
  }
  function scheduleSnap() { clearTimeout(snapTimer); snapTimer = setTimeout(snapshotNow, 250); }
  function applySnapshot(s) {
    suppressSnap = true;
    const cam = [canvas.ds.scale, canvas.ds.offset[0], canvas.ds.offset[1]];  // 保持视角不变
    // 折叠状态不进撤销：重建后沿用当前折叠状态，避免撤销把折叠的节点又展开
    const collapsed = new Set();
    for (const n of graph._nodes) if (n.flags && n.flags.collapsed) collapsed.add(n._id);
    buildGraph(JSON.parse(s));
    for (const n of graph._nodes) if (collapsed.has(n._id)) { n.flags = n.flags || {}; n.flags.collapsed = true; }
    canvas.ds.scale = cam[0]; canvas.ds.offset = [cam[1], cam[2]];
    canvas.setDirty(true, true);
    suppressSnap = false;
  }
  function undo() {
    if (undoStack.length < 2) return;
    redoStack.push(undoStack.pop());
    applySnapshot(undoStack[undoStack.length - 1]);
    setStatus("已撤销");
  }
  function redo() {
    if (!redoStack.length) return;
    const s = redoStack.pop();
    undoStack.push(s);
    applySnapshot(s);
    setStatus("已重做");
  }

  // ---- 右键连线任意位置：找出离光标最近的连线（采样贝塞尔曲线）----
  function distToSeg(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - x1) * dx + (py - y1) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }
  function linkNear(gx, gy) {
    if (!graph) return null;
    const thr = 14 / (canvas.ds.scale || 1);   // 命中阈值（图坐标）
    let best = null, bestD = thr;
    for (const k in graph.links) {
      const l = graph.links[k]; if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b) continue;
      let p0, p3;
      try { p0 = a.getConnectionPos(false, l.origin_slot); p3 = b.getConnectionPos(true, l.target_slot); }
      catch (e) { continue; }
      // 采样数随连线长度增加（含水平凸起），并用"点到线段"距离覆盖采样间隙——
      // 否则长的/竖向的连线采样点太稀，点在线上也会漏判。
      const span = Math.hypot(p3[0] - p0[0], p3[1] - p0[1]) + Math.abs(p3[0] - p0[0]) * 0.5;
      const steps = Math.max(24, Math.min(200, Math.round(span / 10)));
      let prev = canvas.computeConnectionPoint(p0, p3, 0);
      for (let i = 1; i <= steps; i++) {
        const cur = canvas.computeConnectionPoint(p0, p3, i / steps);
        const d = distToSeg(gx, gy, prev[0], prev[1], cur[0], cur[1]);
        if (d < bestD) { bestD = d; best = l; }
        prev = cur;
      }
    }
    return best;
  }
  function onRightDown(e) {
    if (e.button !== 2 || !graph || !canvas) return;
    let off;
    try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;  // 节点上交给 LiteGraph
    const link = linkNear(off[0], off[1]);
    if (link) { e.preventDefault(); e.stopImmediatePropagation(); canvas.showLinkMenu(link, e); }
  }

  function start() {
    try {
      setupColors();
      installEditorTweaks();
      graph = new LGraph();
      canvas = new LGraphCanvas("#graph", graph);
      canvas.allow_searchbox = false;   // 关闭双击/Shift 弹出的搜索框（易误触；加节点统一走右键空白处"添加节点"）
      setupHelpPanel();
      // 撤销触发点：连线变化 / 增删节点 / 移动节点（参数改动在 addParamWidget 的回调里）
      graph.onConnectionChange = scheduleSnap;
      graph.onNodeAdded = scheduleSnap;
      graph.onNodeRemoved = scheduleSnap;
      canvas.onNodeMoved = scheduleSnap;
      // 右键任意位置点中连线 -> 删除连线菜单（捕获阶段，先于 LiteGraph 的右键菜单）
      canvas.canvas.addEventListener("pointerdown", onRightDown, true);
      // 兜底：任何鼠标交互结束后尝试快照（snapshotNow 用 JSON 比对去重，无变化不入栈）
      canvas.canvas.addEventListener("pointerup", scheduleSnap);
      // Ctrl+Z 撤销 / Ctrl+Y 或 Ctrl+Shift+Z 重做（编辑输入框内已 stopPropagation，不会误触）
      document.addEventListener("keydown", (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        const k = e.key.toLowerCase();
        if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
        else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      });
      resize();
      window.addEventListener("resize", resize);
      // 用 ResizeObserver 让窗口拖拽改变大小时内容实时刷新（window resize 在部分情况下不够即时）
      try { new ResizeObserver(resize).observe(document.getElementById("wrap")); } catch (e) {}
      window.__bootReady = true;       // 供 Python 可选地 push 启动数据（__bootstrap__）
      setStatus("正在连接后端…");
      pullWhenReady(0);                // 主路径：轮询直到 api 就绪再拉取（pywebview 注入 api 有延迟）
    } catch (err) {
      showError("初始化画布失败：\n" + (err && (err.stack || err.message) || err));
    }
  }

  // pywebview 把 js_api 方法注入 window.pywebview.api 有延迟（且 api 对象可能先于方法出现），
  // 因此必须轮询到 get_defs 真的是函数再拉取，否则会 "a.get_defs is not a function"。
  async function pullWhenReady(tries) {
    if (booted) return;
    const a = window.pywebview && window.pywebview.api;
    if (!a || typeof a.get_defs !== "function") {
      if (tries > 200) {  // 约 40 秒仍不就绪才报错
        showError("无法连接后端：window.pywebview.api.get_defs 始终不可用。\n" +
                  "（pywebview/WebView2 注入失败？可设 AOE4_EDITOR_DEBUG=1 开开发者工具排查）");
        return;
      }
      setTimeout(() => pullWhenReady(tries + 1), 200);
      return;
    }
    try {
      const d = await a.get_defs();
      const b = await a.list_builtin();
      const f = await a.get_flow();
      boot({ defs: d, builtin: b, flow: f });
    } catch (err) {
      showError("拉取启动数据失败：\n" + (err && (err.stack || err.message) || err));
    }
  }

  window.addEventListener("DOMContentLoaded", start);
  return self;
})();

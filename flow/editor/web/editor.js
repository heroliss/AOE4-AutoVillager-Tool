// AOE4 Flow Editor —— LiteGraph 前端胶水
// 职责：从 Python 取"节点定义/流程"，注册 LiteGraph 节点类型并建图；编辑后回传保存。
// 连线 exec/data 的归类、参数类型回解析都在 Python 侧完成（见 webhost.py），前端只忠实搬运。

const ED = (function () {
  let graph, canvas, defs = [];
  let typeKeyByType = {};   // our type_id -> LiteGraph 注册名
  let seq = 1;

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

  function addParamWidget(node, p) {
    const cb = (v) => { node.properties[p.key] = v; };
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
    defs = list;
    for (const def of list) {
      const key = "aoe4/" + def.category + "/" + def.title;
      typeKeyByType[def.type] = key;
      const D = def;
      function Ctor() {
        this.title = D.title;
        for (const p of D.inputs) this.addInput(p.name, slotType(p), { label: p.label });
        for (const p of D.params) addParamWidget(this, p);
        for (const p of D.outputs) this.addOutput(p.name, slotType(p), { label: p.label });
        this._typeId = D.type;
        if (!this._id) this._id = D.type.split(".").pop() + "_" + (seq++);
      }
      Ctor.title = def.title;
      LiteGraph.registerNodeType(key, Ctor);
    }
  }

  function buildGraph(flow) {
    graph.clear();
    const idMap = {};
    for (const nd of flow.nodes || []) {
      const key = typeKeyByType[nd.type];
      if (!key) continue;
      const n = LiteGraph.createNode(key);
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
    }
    for (const e of flow.edges || []) {
      const a = idMap[e.src], b = idMap[e.dst];
      if (!a || !b) continue;
      const so = a.findOutputSlot(e.src_port);
      const si = b.findInputSlot(e.dst_port);
      if (so >= 0 && si >= 0) a.connect(so, b, si);
    }
    graph.setDirtyCanvas(true, true);
    fit();
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

  function fit() {
    const ns = graph._nodes;
    if (!ns.length) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of ns) {
      x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1] - 20);
      x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, n.pos[1] + n.size[1]);
    }
    const cw = canvas.canvas.width, ch = canvas.canvas.height;
    const s = Math.max(0.15, Math.min(1.4, Math.min((cw - 60) / (x1 - x0), (ch - 60) / (y1 - y0))));
    canvas.ds.scale = s;
    canvas.ds.offset = [-x0 + 30 / s, -y0 + 30 / s];
    canvas.setDirty(true, true);
  }

  // ---- 与 Python 交互 ----
  async function api() { return window.pywebview.api; }

  async function load(flow) {
    graph._aoe4_name = flow.name;
    buildGraph(flow);
    setStatus(`流程：${flow.name} ｜ 节点 ${(flow.nodes || []).length}`);
  }

  const self = {
    async save() {
      const p = await (await api()).save(collect());
      setStatus(p ? `已保存 ${p}` : "已取消保存");
    },
    async saveAs() {
      const p = await (await api()).save_as(collect());
      setStatus(p ? `已保存 ${p}` : "已取消");
    },
    async open() {
      const flow = await (await api()).open_dialog();
      if (flow) load(flow);
    },
    async openBuiltin(path) {
      if (!path) return;
      const flow = await (await api()).open_path(path);
      if (flow) load(flow);
    },
    async autolayout() {
      const flow = await (await api()).autolayout(collect());
      if (flow) load(flow);
    },
    fit,
  };

  async function init() {
    setupColors();
    graph = new LGraph();
    canvas = new LGraphCanvas("#graph", graph);
    resize();
    window.addEventListener("resize", resize);

    const a = await api();
    registerTypes(await a.get_defs());
    // 填充内置流程下拉
    const sel = document.getElementById("builtin");
    for (const p of await a.list_builtin()) {
      const o = document.createElement("option"); o.value = p; o.textContent = p; sel.appendChild(o);
    }
    load(await a.get_flow());
  }

  function resize() {
    const w = document.getElementById("wrap");
    const c = document.getElementById("graph");
    c.width = w.clientWidth; c.height = w.clientHeight;
    if (canvas) canvas.resize();
  }

  function waitApi() {
    if (window.pywebview && window.pywebview.api) init();
    else setTimeout(waitApi, 50);
  }
  window.addEventListener("DOMContentLoaded", waitApi);

  return self;
})();

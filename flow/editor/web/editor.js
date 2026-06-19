// AOE4 Flow Editor —— LiteGraph 前端胶水
// 职责：注册 LiteGraph 节点类型并建图；编辑后回传保存。
// 启动数据（节点定义/初始流程/内置列表）由 Python 主动 push 进来（window.__bootstrap__），
// 若未收到再回退到 js_api 拉取。连线 exec/data 归类、参数类型回解析都在 Python 侧完成。

const ED = (function () {
  let graph, canvas, defs = [];
  let typeKeyByType = {};   // our type_id -> LiteGraph 注册名
  let defByType = {};       // our type_id -> 定义（含 help/参数说明），用于选中节点时显示说明
  let helpEl = null;
  let selectedNode = null;   // 当前选中的节点（用于参数变动时刷新“已修改”列表）
  let seq = 1;
  let booted = false;
  // 当前流程的元信息：名称/说明/文件路径/是否内置只读（保存时内置会被改为另存）
  let flowMeta = { name: "", desc: "", path: null, readonly: false };
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
    C["exec"] = "#FFFFFF";   // 执行流＝白线（与虚幻蓝图一致）
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
    // 节点右键：精简到 克隆 / 删除 + 节点自定义项（去掉 Inputs/Outputs/Properties/Title/Mode/Resize/
    // Collapse/Pin/Colors/Shapes 等堆叠项）。务必调用 node.getExtraMenuOptions，否则“添加/编辑描述”不出现。
    LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
      const opts = [];
      if (node.clonable !== false)
        opts.push({ content: "克隆", callback: LGraphCanvas.onMenuNodeClone });
      opts.push({
        content: "删除",
        disabled: !(node.removable !== false && !node.block_delete),
        callback: LGraphCanvas.onMenuNodeRemove,
      });
      if (node.getExtraMenuOptions) {
        const extra = node.getExtraMenuOptions(this, opts);   // nodeExtraMenu 直接 push 进 opts
        if (Array.isArray(extra)) for (const e of extra) opts.push(e);
      }
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

  function addParamWidget(node, p, def) {
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
    else if (p.ptype === "keys") {
      // 修饰键：直接用下拉菜单（值在 Python 侧与 csv 互转）。容纳已存的非标准组合。
      const vals = MOD_LABELS.slice();
      const dv = p.default == null ? "（无）" : String(p.default);
      if (!vals.includes(dv)) vals.unshift(dv);
      w = node.addWidget("combo", p.label, dv, cb, { values: vals });
    }
    else
      w = node.addWidget("text", p.label, p.default == null ? "" : String(p.default), cb);
    w._key = p.key;                  // 保存时用作参数键（不动 w.name，它是显示标签）
    node.properties[p.key] = w.value;

    // 设一个值进控件并同步（用于"吸色"顺带回填同节点的坐标参数）。
    const setByKey = (key, val) => {
      const w2 = (node.widgets || []).find((x) => x._key === key);
      if (w2) { w2.value = val; if (node.properties) node.properties[key] = val; return true; }
      return false;
    };
    // 关键：按钮回调发生在画布鼠标交互未结束时（画布仍持有指针捕获），若此刻直接弹
    // 系统【模态】框/全屏覆盖层会冲突、"只闪一下"。统一延迟到交互结束后再调后端。
    const defer = (openingMsg, runner, onResult) => {
      setStatus(openingMsg);
      setTimeout(() => {
        Promise.resolve(runner()).then(onResult)
          .catch((e) => showError("采集失败：" + (e && (e.stack || e.message) || e)));
      }, 120);
    };
    const apply = (val, msg) => { w.value = val; cb(val); if (canvas) canvas.setDirty(true, true); setStatus(msg); };
    const mkBtn = (label, run) => {
      const btn = node.addWidget("button", label, null, run);
      btn._noSave = true;            // 按钮不是参数，保存时跳过（collect 按 _key 取值）
    };
    const csv = (arr) => (arr || []).join(",");

    // 各参数类型挂上对应的"点一下就采集"按钮（采集时编辑器会自动让开、截到游戏画面）。
    if (p.ptype === "template" || p.ptype === "templates") {
      const multi = p.ptype === "templates";
      // 从已有图片文件选择（系统文件框）
      mkBtn("选择图片…", () => defer("正在打开图片选择框…", () => api().pick_templates(multi), (paths) => {
        if (!paths || !paths.length) { setStatus("已取消选择图片"); return; }
        const picked = paths.join(",");
        apply((multi && w.value) ? (w.value + "," + picked) : picked, "已选择 " + paths.length + " 张图片");
      }));
      // 直接在游戏画面上框选裁出模板
      mkBtn("截取模板…", () => defer("框选模板区域…（Enter 保存 / Esc 取消）", () => api().capture_template(), (path) => {
        if (!path) { setStatus("已取消截取模板"); return; }
        apply((multi && w.value) ? (w.value + "," + path) : path, "已截取模板：" + path);
      }));
      // 多图：打开"列表编辑器"，逐条增删/排序更方便
      if (multi) mkBtn("编辑列表…", () => editImageList(node, w, p));
    } else if (p.ptype === "region") {
      mkBtn("框选区域…", () => defer("框选区域…（Enter 确认 / Esc 取消）", () => api().pick_region(), (box) => {
        if (!box) { setStatus("已取消框选"); return; }
        apply(csv(box), "已框选区域：" + csv(box));
      }));
    } else if (p.ptype === "point") {
      // 若本节点存在配套的颜色参数（color↔pixel / color_hdr↔pixel_hdr），坐标由「取点吸色」一并采集，
      // 这里就不再单独放「取点」按钮（如游戏窗口检测）。仅当是独立坐标时才显示。
      const pairedColor = (def && def.params || []).some(
        (q) => q.ptype === "color" && q.key === p.key.replace("pixel", "color"));
      if (!pairedColor) {
        mkBtn("取点…", () => defer("点击取点…（Esc 取消）", () => api().pick_point(), (pt) => {
          if (!pt) { setStatus("已取消取点"); return; }
          apply(csv(pt), "已取点：" + csv(pt));
        }));
      }
    } else if (p.ptype === "color") {
      mkBtn("取点吸色…", () => defer("点击取色（坐标+颜色）…（Esc 取消）", () => api().pick_color(), (r) => {
        if (!r) { setStatus("已取消吸色"); return; }
        apply(csv(r.color), "已吸色：" + csv(r.color));
        // 顺带回填同节点配套的坐标参数（color↔pixel / color_hdr↔pixel_hdr）。
        if (r.point && setByKey(p.key.replace("color", "pixel"), csv(r.point))) {
          if (canvas) canvas.setDirty(true, true);
        }
      }));
    } else if (p.ptype === "key") {
      mkBtn("捕获按键…", () => defer("请按下按键…（Esc 取消）", () => api().pick_key(), (k) => {
        if (!k) { setStatus("已取消捕获"); return; }
        apply(k, "已捕获按键：" + k);
      }));
    }
    // 注：修饰键(keys)已是下拉控件（见上方控件创建），无需额外按钮。

    // 图片参数：在节点下方的卡片里画出模板缩略图预览（值变化即刷新；见 nodeDrawForeground）。
    if (p.ptype === "template" || p.ptype === "templates")
      (node._previewKeys = node._previewKeys || []).push(p.key);
  }

  // 与 layout.py 的卡片尺寸常量保持一致（排版预留高度 = 这里画出的高度）。
  // 模板按"缩略图网格"画（仅图片，不显示文件名——增删/查看名字用节点上的"编辑列表…"按钮）。
  const CARD = { TH: 44, GAP: 6, PAD: 8, CGAP: 4, NOTE_LH: 16, DIV: 6, CAP: 12 };
  // 修饰键下拉选项（友好标签；Python 侧与 csv "ctrl,shift" 互转）。
  const MOD_LABELS = ["（无）", "Shift", "Ctrl", "Alt", "Win",
    "Ctrl+Shift", "Ctrl+Alt", "Shift+Alt", "Ctrl+Shift+Alt"];

  // ---- 模板缩略图：本地文件不让网页直接读，由 Python 读成 data URL 回传，这里缓存 ----
  const imgCache = {};   // path -> HTMLImageElement | "loading" | "fail"
  function getThumb(path) {
    if (!path) return null;
    const c = imgCache[path];
    if (c === "loading" || c === "fail") return null;
    if (c) return c;
    imgCache[path] = "loading";
    try {
      Promise.resolve(api().image_data_url(path)).then((url) => {
        if (!url) { imgCache[path] = "fail"; return; }
        const im = new Image();
        im.onload = () => { imgCache[path] = im; if (canvas) canvas.setDirty(true, true); };
        im.onerror = () => { imgCache[path] = "fail"; };
        im.src = url;
      }).catch(() => { imgCache[path] = "fail"; });
    } catch (e) { delete imgCache[path]; }   // api 暂不可用：清掉标记，下帧再试
    return null;
  }
  function wrapText(ctx, text, maxW) {
    const out = [];
    for (const para of String(text).split("\n")) {
      let line = "";
      for (const ch of para) {
        if (ctx.measureText(line + ch).width > maxW && line) { out.push(line); line = ch; }
        else line += ch;
      }
      out.push(line);
    }
    return out;
  }
  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  // 收集本节点所有图片参数当前指向的路径。
  function nodePreviewPaths(node) {
    const out = [];
    for (const k of (node._previewKeys || [])) {
      const w = (node.widgets || []).find((x) => x._key === k);
      if (w && w.value) for (const p of String(w.value).split(",").map((s) => s.trim())) if (p) out.push(p);
    }
    return out;
  }
  function baseName(p) { return String(p).split(/[\\/]/).pop(); }
  // 在节点上画：①已改参数的橙色小点；②节点下方“附属卡片”（描述 📝 + 模板缩略图网格）。
  function nodeDrawForeground(ctx) {
    if (this.flags && this.flags.collapsed) return;
    // ① 已修改参数标记：在该参数控件行右侧画橙点（last_y 是 LiteGraph 画该控件时记下的 y）
    for (const w of (this.widgets || [])) {
      if (paramChanged(w) && w.last_y != null) {
        ctx.save();
        ctx.fillStyle = "#e6a23c";
        ctx.beginPath();
        ctx.arc(this.size[0] - 7, w.last_y + (LiteGraph.NODE_WIDGET_HEIGHT || 20) / 2, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }
    // ② 附属卡片
    const note = this._note || "";
    const paths = nodePreviewPaths(this);
    if (!note && !paths.length) return;
    const W = this.size[0], inner = Math.max(1, W - 2 * CARD.PAD);
    ctx.font = "12px 'Microsoft YaHei',sans-serif";
    const noteLines = note ? wrapText(ctx, "📝 " + note, inner) : [];
    const shown = Math.min(paths.length, CARD.CAP);
    const perRow = Math.max(1, Math.floor(inner / (CARD.TH + CARD.GAP)));
    const rows = paths.length ? Math.ceil(shown / perRow) : 0;
    const extraLine = paths.length > shown ? CARD.NOTE_LH : 0;
    let bodyH = noteLines.length * CARD.NOTE_LH + rows * (CARD.TH + CARD.GAP) + extraLine;
    if (noteLines.length && rows) bodyH += CARD.DIV;
    const cardY = this.size[1] + CARD.CGAP, cardH = CARD.PAD * 2 + bodyH;
    // 卡片背景（圆角 + 细边，和节点连成一体的观感）
    ctx.save();
    roundRect(ctx, 0, cardY, W, cardH, 6);
    ctx.fillStyle = "#1b1f27"; ctx.fill();
    ctx.strokeStyle = "#3a404a"; ctx.lineWidth = 1; ctx.stroke();
    let y = cardY + CARD.PAD;
    if (noteLines.length) {
      ctx.fillStyle = "#c9b87a"; ctx.font = "12px 'Microsoft YaHei',sans-serif";
      for (const ln of noteLines) { ctx.fillText(ln, CARD.PAD, y + 11); y += CARD.NOTE_LH; }
      if (rows) {
        y += CARD.DIV / 2;
        ctx.strokeStyle = "#2c323c"; ctx.beginPath();
        ctx.moveTo(CARD.PAD, y); ctx.lineTo(W - CARD.PAD, y); ctx.stroke();
        y += CARD.DIV / 2;
      }
    }
    let x = CARD.PAD, c = 0;
    for (let i = 0; i < shown; i++) {
      const pth = paths[i], im = getThumb(pth);
      roundRect(ctx, x, y, CARD.TH, CARD.TH, 4);
      ctx.fillStyle = "#11141a"; ctx.fill();
      if (im) {
        ctx.save(); ctx.clip();
        const r = Math.min(CARD.TH / im.width, CARD.TH / im.height);
        const dw = im.width * r, dh = im.height * r;
        ctx.drawImage(im, x + (CARD.TH - dw) / 2, y + (CARD.TH - dh) / 2, dw, dh);
        ctx.restore();
        roundRect(ctx, x, y, CARD.TH, CARD.TH, 4);
      } else {
        ctx.fillStyle = "#666"; ctx.font = "11px sans-serif";
        ctx.fillText(imgCache[pth] === "fail" ? "?" : "…", x + CARD.TH / 2 - 3, y + CARD.TH / 2 + 4);
      }
      ctx.strokeStyle = "#3a404a"; ctx.lineWidth = 1; ctx.stroke();
      x += CARD.TH + CARD.GAP;
      if (++c >= perRow) { c = 0; x = CARD.PAD; y += CARD.TH + CARD.GAP; }
    }
    if (extraLine) {
      if (c !== 0) y += CARD.TH + CARD.GAP;
      ctx.fillStyle = "#7f8895"; ctx.font = "11px sans-serif";
      ctx.fillText("+" + (paths.length - shown) + " 张", CARD.PAD, y + 11);
    }
    ctx.restore();
  }
  // 节点右键菜单追加“编辑描述/清除描述”。
  function nodeExtraMenu(_canvas, options) {
    const node = this;
    options.push(null, {
      content: node._note ? "编辑描述…" : "添加描述…",
      callback: () => editNote(node),
    });
    if (node._note) options.push({
      content: "清除描述",
      callback: () => { node._note = ""; if (canvas) canvas.setDirty(true, true); scheduleSnap(); },
    });
  }
  function editNote(node) {
    document.getElementById("notedlg")?.remove();
    const box = document.createElement("div");
    box.id = "notedlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(420px,90vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:130;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    box.innerHTML = "<b style='color:#e6c07b'>节点描述</b>（说明这个节点的作用，仅展示、不影响运行）<br>" +
      "<textarea id='notetext' style='width:100%;height:84px;margin-top:8px;background:#15171c;color:#cfd3da;" +
      "border:1px solid #444;border-radius:4px;padding:6px;font:13px/1.5 \"Microsoft YaHei\",sans-serif;box-sizing:border-box'></textarea>" +
      "<div style='margin-top:8px;text-align:right'>" +
      "<button id='noteok' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>确定</button> " +
      "<button id='notecancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
    document.body.appendChild(box);
    const ta = box.querySelector("#notetext");
    ta.value = node._note || "";
    ta.focus();
    box.querySelector("#noteok").onclick = () => {
      node._note = ta.value.trim();
      box.remove();
      if (canvas) canvas.setDirty(true, true);
      scheduleSnap();
    };
    box.querySelector("#notecancel").onclick = () => box.remove();
  }

  // 多图参数的"列表编辑器"：逐条看缩略图+路径，可删除/上移/下移，并直接添加（选图/截模板）。
  // 所有改动即时写回控件（节点上的列表预览同步刷新），点关闭即结束。
  function editImageList(node, widget, p) {
    document.getElementById("imglist")?.remove();
    let paths = String(widget.value || "").split(",").map((s) => s.trim()).filter(Boolean);
    const box = document.createElement("div");
    box.id = "imglist";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(520px,94vw);" +
      "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
      "padding:14px 16px;z-index:140;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    document.body.appendChild(box);
    const btnCss = "background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:2px 9px;cursor:pointer;margin-left:5px";
    const apply = () => {           // 即时写回控件 + 标脏 + 刷新画布
      widget.value = paths.join(",");
      if (node.properties) node.properties[p.key] = widget.value;
      if (canvas) canvas.setDirty(true, true);
      scheduleSnap(); refreshDirty();
    };
    function render() {
      let h = "<b style='color:#e6c07b'>编辑图片列表</b>（" + esc(p.label) + "，共 " + paths.length + " 张）" +
        "<div style='margin-top:8px'>";
      if (!paths.length) h += "<div style='color:#6b727d'>（空，点下方按钮添加图片）</div>";
      paths.forEach((pth, i) => {
        h += "<div data-i='" + i + "' style='display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #2c323c'>" +
          "<img class='th' data-p='" + esc(pth) + "' style='width:40px;height:40px;object-fit:contain;flex:none;" +
          "background:#11141a;border:1px solid #3a404a;border-radius:4px'/>" +
          "<span style='flex:1;word-break:break-all;color:#aeb6c2' title='" + esc(pth) + "'>" + esc(baseName(pth)) + "</span>" +
          "<button data-act='up' " + (i === 0 ? "disabled" : "") + " style='" + btnCss + "'>↑</button>" +
          "<button data-act='down' " + (i === paths.length - 1 ? "disabled" : "") + " style='" + btnCss + "'>↓</button>" +
          "<button data-act='del' style='" + btnCss + ";color:#e88'>删除</button></div>";
      });
      h += "</div><div style='margin-top:12px;text-align:right'>" +
        "<button id='il_pick' style='" + btnCss + "'>＋ 选择图片</button>" +
        "<button id='il_cap' style='" + btnCss + "'>＋ 截取模板</button>" +
        "<button id='il_close' style='" + btnCss + ";border-color:#5a6' >关闭</button></div>";
      box.innerHTML = h;
      // 缩略图：本地文件不让网页直接读，向 Python 取 data URL（已缓存的直接复用 getThumb 的结果）
      box.querySelectorAll("img.th").forEach((im) => {
        const pp = im.getAttribute("data-p"), c = imgCache[pp];
        if (c && c !== "loading" && c !== "fail") im.src = c.src;
        else Promise.resolve(api().image_data_url(pp)).then((u) => { if (u) im.src = u; }).catch(() => {});
      });
      box.querySelectorAll("[data-act]").forEach((b) => {
        b.onclick = () => {
          const i = +b.closest("[data-i]").getAttribute("data-i"), act = b.getAttribute("data-act");
          if (act === "del") paths.splice(i, 1);
          else if (act === "up" && i > 0) { const t = paths[i - 1]; paths[i - 1] = paths[i]; paths[i] = t; }
          else if (act === "down" && i < paths.length - 1) { const t = paths[i + 1]; paths[i + 1] = paths[i]; paths[i] = t; }
          apply(); render();
        };
      });
      box.querySelector("#il_pick").onclick = () => {
        setStatus("正在打开图片选择框…");
        Promise.resolve(api().pick_templates(true)).then((arr) => {
          if (arr && arr.length) { paths = paths.concat(arr); apply(); render(); setStatus("已添加 " + arr.length + " 张图片"); }
          else setStatus("已取消选择图片");
        }).catch((e) => showError("选择图片失败：" + (e && (e.stack || e.message) || e)));
      };
      box.querySelector("#il_cap").onclick = () => {
        setStatus("框选模板区域…（Enter 保存 / Esc 取消）");
        Promise.resolve(api().capture_template()).then((path) => {
          if (path) { paths.push(path); apply(); render(); setStatus("已截取模板：" + path); }
          else setStatus("已取消截取模板");
        }).catch((e) => showError("截取模板失败：" + (e && (e.stack || e.message) || e)));
      };
      box.querySelector("#il_close").onclick = () => box.remove();
    }
    render();
  }

  // ---- 未保存修改标记（全局 ●未保存 + 每个参数的橙点/恢复）----
  let savedSig = null;           // 全局：上次保存/载入时 collect() 的签名
  let savedParams = {};          // 每参数基线：nodeId -> { key: 基线值 }
  // 规范化签名：按 id/连线排序后再 JSON，使"点选节点导致的 z 序变化"(LiteGraph bringToFront 会重排
  // graph._nodes)不被误判为"未保存"。只有真正的参数/连线/位置/名称改动才算改动。
  function curSig() {
    try {
      const c = collect();
      const nodes = c.nodes.slice().sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      const edges = c.edges.slice().sort((a, b) => {
        const ka = a.src + "|" + a.src_port + "|" + a.dst + "|" + a.dst_port;
        const kb = b.src + "|" + b.src_port + "|" + b.dst + "|" + b.dst_port;
        return ka < kb ? -1 : ka > kb ? 1 : 0;
      });
      return JSON.stringify({ name: c.name, description: c.description, panel: c.panel, nodes, edges });
    } catch (e) { return null; }
  }
  function markSaved() {         // 保存/载入后：把“当前”设为基线，清除所有标记
    savedParams = {};
    for (const n of graph._nodes) {
      const m = {}; for (const w of (n.widgets || [])) if (w._key) m[w._key] = w.value;
      savedParams[n._id] = m;
    }
    savedSig = curSig();
    attachBaselineRefs();
    showDirty(false);
  }
  function attachBaselineRefs() { // 撤销/重做/排版会重建控件 -> 从 savedParams 重新挂上各控件的基线值
    for (const n of graph._nodes) {
      const m = savedParams[n._id] || {};
      for (const w of (n.widgets || [])) if (w._key) w._saved = (w._key in m) ? m[w._key] : w.value;
    }
  }
  function paramChanged(w) { return !!(w && w._key && w._saved !== undefined && String(w.value) !== String(w._saved)); }
  function refreshDirty() { showDirty(savedSig !== null && curSig() !== savedSig); }
  function showDirty(d) {
    const el = document.getElementById("dirty");
    if (el) el.textContent = d ? "●未保存" : "";
    try { document.title = (d ? "*" : "") + "AOE4 Flow Editor"; } catch (e) {}
  }
  function labelOf(node, key) {
    const d = defByType[node && node._typeId];
    const p = d && (d.params || []).find((q) => q.key === key);
    return (p && p.label) || key;
  }
  function revertParam(node, key) {   // 把某参数恢复为基线（上次保存/载入）值
    const w = (node.widgets || []).find((x) => x._key === key);
    if (!w || w._saved === undefined) return;
    w.value = w._saved;
    if (node.properties) node.properties[key] = w.value;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap();
    refreshDirty();                 // 立刻更新顶部“未保存”（不等 250ms 防抖快照）
    showNodeHelp(node);             // 刷新说明面板里的“已修改”列表
  }
  function revertNode(node) {
    for (const w of (node.widgets || [])) if (paramChanged(w)) revertParam(node, w._key);
    refreshDirty();
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
          for (const p of D.params) addParamWidget(this, p, D);
          for (const p of D.outputs) this.addOutput(p.name, slotType(p), { label: p.label });
          this.size[0] = Math.max(this.size[0] || 0, nodeMinWidth(D));  // 加宽容纳"参数名+值"，与排版预留一致
          this._typeId = D.type;
          if (!this._id) this._id = D.type.split(".").pop() + "_" + (seq++);
        };
        Ctor.title = def.title;
        Ctor.prototype.onDrawForeground = nodeDrawForeground;   // 节点下方画描述+模板缩略图
        Ctor.prototype.getExtraMenuOptions = nodeExtraMenu;     // 右键菜单加“编辑描述”
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
        n._note = nd.note || "";
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
      for (const w of (n.widgets || [])) if (w._key) params[w._key] = w.value;  // 跳过按钮等无_key控件
      const nd = { id: n._id, type: n._typeId, pos: [Math.round(n.pos[0]), Math.round(n.pos[1])], params };
      if (n._note) nd.note = n._note;
      nodes.push(nd);
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
    return { name: flowMeta.name || "未命名流程", description: flowMeta.desc || "", nodes, edges };
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

  function load(flow, opts) {
    const clean = !opts || opts.clean !== false;   // 打开/内置=干净基线；自动排版=保留原基线(版面变了仍算未保存)
    const keepHistory = !!(opts && opts.keepHistory);   // 自动排版：保留撤销历史（这样排版可被 Ctrl+Z 撤销）
    flowMeta = {
      name: flow.name || "未命名流程",
      desc: flow.description || "",
      // 自动排版回传仍带 path/readonly；缺失时沿用当前值
      path: ("path" in flow) ? flow.path : flowMeta.path,
      readonly: ("readonly" in flow) ? !!flow.readonly : flowMeta.readonly,
    };
    if (!keepHistory) { undoStack = []; redoStack = []; }   // 新流程：清空撤销历史（排版除外）
    const added = buildGraph(flow);
    const total = (flow.nodes || []).length;
    fit(0.5);   // 载入用可读下限；适应窗口按钮(ED.fit())仍为真·全图
    setStatus(`流程：${flowMeta.name} ｜ 节点 ${added}/${total}`);
    updateFlowMeta();
    snapshotNow();   // 记录初始快照，作为撤销的基线
    if (clean) markSaved();                         // 刚载入＝与磁盘一致，清除“未保存”标记
    else { attachBaselineRefs(); refreshDirty(); }  // 排版后保留原基线，重新挂上控件引用
  }

  // 顶部工具栏：显示当前流程名 + 来源（内置只读 / 我的流程）；说明作为悬浮提示。
  function updateFlowMeta() {
    const el = document.getElementById("flowmeta");
    if (!el) return;
    let tag = "";
    if (flowMeta.readonly) tag = " · <span class='ro'>内置·只读</span>";
    else if (flowMeta.path) tag = " · 我的流程";
    el.innerHTML = "📄 " + esc(flowMeta.name) + tag;
    el.title = (flowMeta.readonly ? "内置流程（只读）：保存会另存到「我的流程」。\n\n" : "") +
               (flowMeta.desc || "（暂无流程说明，点顶部「流程信息…」添加）");
  }

  // 顶部下拉：列出内置/我的流程，按【中文流程名】显示（值仍是完整相对路径，供 openBuiltin 打开）。
  // 后端返回 [{path, name}]；按路径前缀分组成「内置流程（只读）」「我的流程」。
  function fillFlowList(list) {
    const sel = document.getElementById("builtin");
    if (!sel) return;
    // 首项是"提示标签"：disabled 故不可被选中，仅作为下拉收起时的按钮文案（选完会重置回它）。
    sel.innerHTML = "<option value='' disabled selected>打开内置流程…</option>";
    const groups = { "flows": [], "user_flows": [] };
    for (const it of (list || [])) {
      const item = (typeof it === "string") ? { path: it, name: it } : it;   // 兼容旧的纯字符串
      const dir = String(item.path).split("/")[0];
      (groups[dir] || (groups[dir] = [])).push(item);
    }
    const mk = (label, items) => {
      if (!items.length) return;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const item of items) {
        const o = document.createElement("option");
        o.value = item.path;
        o.textContent = item.name || item.path.split("/").pop().replace(/\.flow\.json$/, "");
        og.appendChild(o);
      }
      sel.appendChild(og);
    };
    mk("内置流程（只读）", groups["flows"]);
    mk("我的流程", groups["user_flows"]);
  }

  // ---- 启动：优先用 Python push 进来的数据 ----
  function boot(data) {
    if (booted) return;
    booted = true;
    try {
      const okN = registerTypes(data.defs);
      fillFlowList(data.builtin || []);
      if (data.flow) load(data.flow);
      else setStatus(`已就绪 ｜ 已注册 ${okN} 种节点（右键空白处添加）`);
    } catch (err) {
      showError("启动失败：\n" + (err && (err.stack || err.message) || err));
    }
  }
  window.__bootstrap__ = function (data) { boot(data); return true; };

  // 保存/另存成功后：清除“未保存”、刷新元信息（保存后一定写到可写的用户文件，故不再只读），
  // 并刷新下拉列表（新另存的“我的流程”会出现在列表里）。
  async function afterSaved(p) {
    flowMeta.path = p;
    flowMeta.readonly = false;
    markSaved();
    updateFlowMeta();
    try { fillFlowList(await api().list_builtin()); } catch (e) {}
  }

  const self = {
    async save() {
      try {
        const p = await api().save(collect());
        if (p) await afterSaved(p);
        setStatus(p ? `已保存 ${p}` : "已取消保存");
      } catch (err) { showError("保存失败：" + (err.stack || err)); }
    },
    async saveAs() {
      try {
        const p = await api().save_as(collect());
        if (p) await afterSaved(p);
        setStatus(p ? `已保存 ${p}` : "已取消");
      } catch (err) { showError("另存为失败：" + (err.stack || err)); }
    },
    editFlowInfo,
    async open() {
      try {
        const flow = await api().open_dialog();
        if (flow) load(flow);
      } catch (err) { showError("打开失败：" + (err.stack || err)); }
    },
    async openBuiltin(path) {
      const sel = document.getElementById("builtin");
      if (!path) return;
      try {
        const flow = await api().open_path(path);
        if (flow) load(flow);
      } catch (err) { showError("打开流程失败：" + (err.stack || err)); }
      finally { if (sel) sel.selectedIndex = 0; }   // 重置回提示项，下拉像按钮一样可重复选同一项
    },
    async autolayout() {
      try {
        const flow = await api().autolayout(collect());
        if (!flow) return;
        snapshotNow();                              // 先把“排版前”快照确保进撤销栈
        load(flow, { clean: false, keepHistory: true });   // 只动版面：保留“未保存”判定且可撤销
        setStatus("已自动排版（可 Ctrl+Z 撤销）");
      } catch (err) { showError("自动排版失败：" + (err.stack || err)); }
    },
    fit,
    help: toggleHelp,
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
    canvas.onNodeSelected = (n) => { selectedNode = n; showNodeHelp(n); };
    canvas.onNodeDeselected = () => { selectedNode = null; helpEl.style.display = "none"; };
  }

  // 选中节点的说明面板：只放“该节点专属”的内容（用途 + 需要解释的端口/参数），
  // 不重复通用的连线模型与操作说明——后者集中在顶部“帮助”里（见 self.help）。
  function portLine(p) {
    const tag = p.kind === "exec" ? "<span style='color:#ddd'>[执行]</span>"
                                  : "<span style='color:#7fbf7f'>[数据]</span>";
    return `<div style="margin-top:2px">${tag} <b style="color:#bcd">${esc(p.label || p.name)}</b>：${esc(p.help)}</div>`;
  }

  function showNodeHelp(node) {
    if (!helpEl) return;
    const d = defByType[node && node._typeId];
    if (!d) { helpEl.style.display = "none"; return; }
    const sub = "color:#7f8895;border-top:1px solid #3a404a;margin-top:6px;padding-top:4px";
    let html = `<div style="font-weight:bold;color:#e6e9ee;margin-bottom:2px">${esc(d.title)}</div>`;
    const doc = d.doc || d.help || "";
    if (doc) html += `<div style="color:#9aa3af;white-space:pre-line">${esc(doc)}</div>`;
    // 仅展示“含义不直观、带专门说明”的端口（如分支的 条件/真/假），不堆叠每一个端口
    const ports = (d.inputs || []).concat(d.outputs || []).filter((p) => p.help);
    if (ports.length) {
      html += `<div style="${sub}">端口说明</div>`;
      for (const p of ports) html += portLine(p);
    }
    const ps = (d.params || []).filter((p) => p.help);
    if (ps.length) {
      html += `<div style="${sub}">参数说明</div>`;
      for (const p of ps)
        html += `<div style="margin-top:2px"><b style="color:#bcd">${esc(p.label)}</b>：${esc(p.help)}</div>`;
    }
    helpEl.innerHTML = html;
    // 已修改（未保存）的参数：列出 旧→新 并给“恢复”链接（每项 + 全部）
    const changed = (node.widgets || []).filter(paramChanged);
    if (changed.length) {
      const box = document.createElement("div");
      box.style.cssText = sub;
      let h2 = "<span style='color:#e6a23c'>已修改（未保存）</span> " +
               "<a href='#' data-revert='*' style='color:#7fb0ee;text-decoration:none'>[全部恢复]</a>";
      for (const w of changed)
        h2 += `<div style="margin-top:3px"><b style="color:#bcd">${esc(labelOf(node, w._key))}</b> ` +
              `<a href="#" data-revert="${esc(w._key)}" style="color:#7fb0ee;text-decoration:none">[恢复]</a>` +
              `<br><span style="color:#7f8895">旧：${esc(w._saved)} → 新：${esc(w.value)}</span></div>`;
      box.innerHTML = h2;
      helpEl.appendChild(box);
      box.querySelectorAll("[data-revert]").forEach((a) => {
        a.onclick = (e) => {
          e.preventDefault();
          const k = a.getAttribute("data-revert");
          if (k === "*") revertNode(node); else revertParam(node, k);
        };
      });
    }
    helpEl.style.display = "block";
  }

  // 编辑流程名称 + 说明（内置流程也可改，保存时会另存）。
  function editFlowInfo() {
    document.getElementById("flowdlg")?.remove();
    const box = document.createElement("div");
    box.id = "flowdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(460px,92vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:130;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    const inputCss = "width:100%;margin:4px 0 10px;background:#15171c;color:#cfd3da;border:1px solid #444;" +
      "border-radius:4px;padding:6px;font:13px/1.5 \"Microsoft YaHei\",sans-serif;box-sizing:border-box";
    box.innerHTML = "<b style='color:#e6c07b'>流程信息</b>（名称与整体说明，仅展示、不影响运行）<br>" +
      "<div style='margin-top:8px;color:#9aa3af'>名称</div>" +
      `<input id='flowname_in' style='${inputCss}'/>` +
      "<div style='color:#9aa3af'>说明</div>" +
      `<textarea id='flowdesc_in' style='${inputCss};height:96px'></textarea>` +
      "<div style='text-align:right'>" +
      "<button id='flowok' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>确定</button> " +
      "<button id='flowcancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
    document.body.appendChild(box);
    const nameIn = box.querySelector("#flowname_in"), descIn = box.querySelector("#flowdesc_in");
    nameIn.value = flowMeta.name || ""; descIn.value = flowMeta.desc || "";
    nameIn.focus();
    box.querySelector("#flowok").onclick = () => {
      flowMeta.name = nameIn.value.trim() || "未命名流程";
      flowMeta.desc = descIn.value.trim();
      box.remove();
      updateFlowMeta();
      setStatus(`流程：${flowMeta.name}`);
      scheduleSnap(); refreshDirty();   // 名称/说明计入“未保存”
    };
    box.querySelector("#flowcancel").onclick = () => box.remove();
  }

  // 顶部“帮助”：集中放连线模型图例 + 常用操作（避免在每个节点面板里重复）
  let helpModal = null;
  function toggleHelp() {
    if (helpModal) { helpModal.remove(); helpModal = null; return; }
    helpModal = document.createElement("div");
    helpModal.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);" +
      "width:min(640px,92vw);max-height:80%;overflow:auto;background:#23272f;color:#cfd3da;" +
      "border:1px solid #3a404a;border-radius:8px;padding:14px 18px;z-index:100;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.7 'Microsoft YaHei',sans-serif;";
    // 一段线样色块 + 文字，用于颜色图例
    const wire = (color, label) =>
      `<span style="display:inline-block;width:24px;border-top:3px solid ${color};` +
      `vertical-align:middle;margin:0 4px 0 10px"></span>${label}`;
    helpModal.innerHTML =
      "<div style='display:flex;align-items:center;margin-bottom:6px'>" +
      "<b style='font-size:15px;color:#e6e9ee;flex:1'>编辑器帮助</b>" +
      "<span id='helpclose' style='cursor:pointer;color:#8b909a;font-size:18px;padding:0 4px'>✕</span></div>" +
      "<div style='color:#9aa3af'><b style='color:#e6c07b'>连线模型（类虚幻蓝图）</b><br>" +
      "· <b style='color:#fff'>白线＝执行流</b>：决定先做什么、再做什么、走哪条路。入口是「每帧触发」；" +
      "某个出口不接任何节点，就表示那种情况下<b>本帧到此结束</b>（无需专门的“结束”节点）。<br>" +
      "· <b style='color:#7fbf7f'>彩线＝数据流</b>：传递“值”，下游用到时才向上游取，一个输出可同时连给多处；" +
      "不同颜色＝不同类型（数值/是否/图像/区域…）。<br>" +
      "· <b>判断与分支</b>：检测/比较类节点只输出一个“是/否”（彩线）；把它接到「分支」的“条件”，" +
      "再从「真」「假」两个出口分别往后连——判断本身和“分岔”是分开的两件事。</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>彩线颜色＝数据类型</b>（连什么类型就显什么色）<br>" +
      wire("#7AB0EE", "数值") + wire("#E0A85A", "是否(布尔)") + wire("#9AD08A", "文本") +
      wire("#C792DF", "图像") + wire("#69b0a0", "区域/坐标") + wire("#cf8a6a", "颜色") + "<br>" +
      "<span style='color:#7f8895'>当前流程的数据多是数字与是/否，所以你主要看到蓝、黄两色；用到其它类型时会出现对应颜色。</span></div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>常用操作</b><br>" +
      "· 右键空白处：添加节点　· 滚轮：缩放　· 拖动空白：平移<br>" +
      "· 拖动节点标题：移动　· Ctrl+拖动空白：框选多个　· 选中多个后可整体拖动<br>" +
      "· 单击参数输入框：直接编辑，<b>实时生效</b>（无需确认按钮）<br>" +
      "· 右键连线（线上任意处）：删除连线　· 双击节点标题：折叠/展开<br>" +
      "· Ctrl+Z 撤销　· Ctrl+Y 或 Ctrl+Shift+Z 重做<br>" +
      "· 顶部按钮：自动排版（重新理顺布局）/ 适应窗口 / 保存</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>在游戏画面上直接采集</b>（节点里相应参数下方的按钮）<br>" +
      "· <b>框选区域…</b>：按住左键拖出矩形，Enter 确认（区域参数）<br>" +
      "· <b>取点…/吸色…</b>：移动有放大镜，点一下取坐标/颜色（吸色会顺带回填配套坐标）<br>" +
      "· <b>截取模板…</b>：框选游戏画面裁出小图存为模板（图片参数）<br>" +
      "· <b>选择图片…</b>：从已有图片文件选模板　· <b>捕获按键…</b>：按一下记下按键<br>" +
      "<span style='color:#7f8895'>点按钮后编辑器会自动最小化让开、截到游戏画面，采完自动恢复；Esc 取消。</span></div>";
    document.body.appendChild(helpModal);
    helpModal.querySelector("#helpclose").onclick = toggleHelp;
  }

  // ---- 撤销/重做（对整图做 JSON 快照；buildGraph/applySnapshot 期间抑制）----
  function snapshotNow() {
    if (suppressSnap || building || !graph) return;
    const s = JSON.stringify(collect());
    if (undoStack.length && undoStack[undoStack.length - 1] === s) return;
    undoStack.push(s);
    if (undoStack.length > 100) undoStack.shift();
    redoStack = [];
    refreshDirty();
    // 选中的节点参数有改动时，同步刷新右下角“已修改”列表（橙点本就实时随重绘更新）
    if (selectedNode && helpEl && helpEl.style.display !== "none") showNodeHelp(selectedNode);
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
    attachBaselineRefs();   // 重建控件后重新挂基线引用，保证“已修改”橙点正确
    canvas.ds.scale = cam[0]; canvas.ds.offset = [cam[1], cam[2]];
    canvas.setDirty(true, true);
    suppressSnap = false;
  }
  function undo() {
    if (undoStack.length < 2) return;
    redoStack.push(undoStack.pop());
    applySnapshot(undoStack[undoStack.length - 1]);
    refreshDirty();
    setStatus("已撤销");
  }
  function redo() {
    if (!redoStack.length) return;
    const s = redoStack.pop();
    undoStack.push(s);
    applySnapshot(s);
    refreshDirty();
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
    if (link) {
      e.preventDefault();
      e.stopImmediatePropagation();
      // 先关掉已打开的任何菜单，否则我们 stopImmediatePropagation 会让旧菜单的"点外部关闭"失效而叠加
      try { LiteGraph.closeAllContextMenus(window); } catch (err) {}
      canvas.showLinkMenu(link, e);
    }
  }

  function start() {
    try {
      setupColors();
      installEditorTweaks();
      graph = new LGraph();
      canvas = new LGraphCanvas("#graph", graph);
      canvas.allow_searchbox = false;   // 关闭双击/Shift 弹出的搜索框（易误触；加节点统一走右键空白处"添加节点"）
      canvas.show_info = false;          // 隐藏左下角 T/I/N/V/FPS 调试信息（对普通用户无意义）
      canvas.render_connections_border = false;  // 连线不画深色描边——避免"一条线两种颜色(深/浅)"的观感
      canvas.render_canvas_border = false;  // 不画画布边框（背景里那条蓝色细线矩形）
      setupHelpPanel();
      // 撤销触发点：连线变化 / 增删节点 / 移动节点（参数改动在 addParamWidget 的回调里）
      graph.onConnectionChange = scheduleSnap;
      graph.onNodeAdded = scheduleSnap;
      // 删除节点：除快照外，隐藏右下角说明面板（否则被删节点的说明会残留）
      graph.onNodeRemoved = () => { scheduleSnap(); selectedNode = null; if (helpEl) helpEl.style.display = "none"; };
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

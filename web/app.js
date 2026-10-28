/* KGRAG 前端：上传 / 建图 / 图谱可视化 / 问答 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const TYPE_COLORS = {
    DOC: "#f472b6", DEPT: "#4f9cf9", SYSTEM: "#a78bfa", POLICY: "#f59e0b",
    ROLE: "#34d399", ORG: "#94a3b8", METRIC: "#22d3ee",
  };
  const TYPE_NAMES = {
    DOC: "文档", DEPT: "部门", SYSTEM: "系统", POLICY: "制度",
    ROLE: "角色", ORG: "组织", METRIC: "指标",
  };
  const REL_COLORS = { 负责: "#f59e0b", 适用: "#4f9cf9", 相关: "#a78bfa", 共现: "#2f4258" };

  const state = {
    files: [],
    graph: null,
    graphLimit: 120,
    showCooc: true,
    selected: null,
    hovered: null,
    useGraph: true,
  };

  /* ---------------- API ---------------- */
  async function req(method, url, body, headers) {
    const opt = { method, headers: { ...(headers || {}) } };
    if (body !== undefined && body !== null) {
      if (body instanceof Blob || body instanceof Uint8Array) {
        opt.body = body;
      } else {
        opt.headers["Content-Type"] = "application/json";
        opt.body = JSON.stringify(body);
      }
    }
    const res = await fetch(url, opt);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  /* ---------------- 状态栏 ---------------- */
  async function loadStatus() {
    try {
      const s = await req("GET", "/api/status");
      const el = $("#status");
      el.classList.remove("err");
      el.classList.add("ok");
      $("#status-text").textContent =
        `${s.files} 文档 · ${s.chunks} 切块 · ${s.nodes} 实体${s.llm ? " · LLM 已启用" : " · 规则抽取"}`;
    } catch (err) {
      const el = $("#status");
      el.classList.remove("ok");
      el.classList.add("err");
      $("#status-text").textContent = "后端未连接";
    }
  }

  /* ---------------- Tabs ---------------- */
  function initTabs() {
    $("#tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tab]");
      if (!btn) return;
      const tab = btn.dataset.tab;
      $$("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + tab));
      if (tab === "graph") { loadGraph(); }
      if (tab === "qa") { loadFiles(); loadHistory(); }
      if (tab === "build") { loadBuildPreview(); }
    });
  }

  /* ---------------- 上传 ---------------- */
  function initUpload() {
    const dz = $("#dropzone");
    const input = $("#file-input");

    dz.addEventListener("click", (e) => {
      if (e.target.closest("label")) return;
      input.click();
    });
    input.addEventListener("change", () => { uploadFiles(Array.from(input.files || [])); input.value = ""; });
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("hover"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("hover"); }));
    dz.addEventListener("drop", (e) => uploadFiles(Array.from(e.dataTransfer.files || [])));

    $("#btn-refresh-files").addEventListener("click", loadFiles);
  }

  async function uploadFiles(files) {
    if (!files.length) return;
    const box = $("#upload-progress");
    const bar = box.querySelector(".bar");
    const label = box.querySelector("span");
    box.hidden = false;
    const ok = [], fail = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      label.textContent = `上传中 ${i + 1}/${files.length}：${f.name}`;
      bar.style.width = `${Math.round((i / files.length) * 100)}%`;
      try {
        const meta = await req("POST", "/api/upload", f, {
          "X-Filename": encodeURIComponent(f.name),
          "Content-Type": "application/octet-stream",
        });
        ok.push(meta);
      } catch (err) {
        fail.push(`${f.name}：${err.message}`);
      }
    }
    bar.style.width = "100%";
    label.textContent = fail.length
      ? `完成：成功 ${ok.length}，失败 ${fail.length}（${fail.join("；")}）`
      : `完成：成功上传 ${ok.length} 份文档`;
    setTimeout(() => { box.hidden = true; bar.style.width = "0"; }, 2200);
    await loadFiles();
    await loadStatus();
  }

  async function loadFiles() {
    try {
      const data = await req("GET", "/api/files");
      state.files = data.files || [];
      renderFileTable();
      renderFileFilter();
    } catch (err) { /* ignore */ }
  }

  function renderFileTable() {
    const tbody = $("#file-table tbody");
    if (!state.files.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">还没有文档，先上传一份试试</td></tr>';
      return;
    }
    tbody.innerHTML = state.files.map((f) => `
      <tr>
        <td>${esc(f.name)}</td>
        <td>${esc(f.title || "-")}</td>
        <td class="num">${f.chunks}</td>
        <td class="num">${(f.chars / 1000).toFixed(1)}k</td>
        <td><span class="tag">已索引</span></td>
      </tr>`).join("");
  }

  function renderFileFilter() {
    const sel = $("#file-filter");
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部文档</option>' +
      state.files.map((f) => `<option value="${esc(f.file_id)}">${esc(f.title || f.name)}</option>`).join("");
    if (state.files.some((f) => f.file_id === cur)) sel.value = cur;
  }

  /* ---------------- 建图 ---------------- */
  function initBuild() {
    $("#btn-build").addEventListener("click", buildGraph);
  }

  async function loadBuildPreview() {
    try {
      const s = await req("GET", "/api/graph");
      if (s.nodes && s.nodes.length) renderBuildStats(s);
    } catch (err) { /* ignore */ }
  }

  async function buildGraph() {
    const btn = $("#btn-build");
    const msg = $("#build-msg");
    btn.disabled = true;
    msg.textContent = "构建中：实体抽取 → 三元组抽取 → 图合并 → 社区发现…";
    try {
      const s = await req("POST", "/api/build", {
        use_llm: $("#use-llm").checked,
        reset: $("#rebuild-reset").checked,
      });
      renderBuildStats(s);
      msg.textContent = `构建完成：${s.nodes.length} 个实体，${s.edge_count ?? s.edges.length} 条关系，${s.communities.length} 个社区`;
      state.graph = null;
      await loadStatus();
    } catch (err) {
      msg.textContent = "构建失败：" + err.message;
    } finally {
      btn.disabled = false;
    }
  }

  function renderBuildStats(stats) {
    $("#build-stats").hidden = false;
    $("#build-detail").hidden = false;
    $("#st-nodes").textContent = stats.stats?.nodes ?? stats.nodes.length;
    $("#st-edges").textContent = stats.stats?.edges ?? stats.edges.length;
    $("#st-triplets").textContent = stats.stats?.triplets ?? "-";
    $("#st-time").textContent = (stats.elapsed_ms || 0) + "ms";

    const types = stats.type_distribution || {};
    const rels = stats.relation_distribution || {};
    const top = stats.top_entities || [];
    $("#chips-types").innerHTML = Object.entries(types).map(([k, v]) =>
      `<span>${TYPE_NAMES[k] || k} <b>${v}</b></span>`).join("") || '<span>无</span>';
    $("#chips-rels").innerHTML = Object.entries(rels).map(([k, v]) =>
      `<span>${esc(k)} <b>${v}</b></span>`).join("") || '<span>无</span>';
    $("#chips-top").innerHTML = top.map((t) =>
      `<span>${esc(t.name)} <b>${t.degree}</b></span>`).join("") || '<span>无</span>';
  }

  /* ---------------- 图谱可视化 ---------------- */
  const canvas = $("#graph-canvas");
  const ctx = canvas ? canvas.getContext("2d") : null;
  const view = { cx: 0, cy: 0, k: 1 };
  let sim = { nodes: [], edges: [], alpha: 0, raf: 0 };

  function initGraph() {
    if (!canvas) return;
    $("#btn-focus").addEventListener("click", focusSearch);
    $("#node-search").addEventListener("keydown", (e) => { if (e.key === "Enter") focusSearch(); });
    $("#graph-limit").addEventListener("change", (e) => {
      state.graphLimit = Number(e.target.value || 120);
      loadGraph();
    });
    $("#show-cooc").addEventListener("change", (e) => {
      state.showCooc = e.target.checked;
      if (state.graph) startSim(state.graph);
    });

    let dragging = null, panning = null;
    const toWorld = (px, py) => ({
      x: (px - canvas.clientWidth / 2) / view.k + view.cx,
      y: (py - canvas.clientHeight / 2) / view.k + view.cy,
    });
    const pick = (wx, wy) => {
      let best = null, bestD = 14 / view.k;
      for (const n of sim.nodes) {
        const d = Math.hypot(n.x - wx, n.y - wy);
        if (d < Math.max(bestD, 6)) { best = n; bestD = d; }
      }
      return best;
    };

    canvas.addEventListener("mousedown", (e) => {
      const r = canvas.getBoundingClientRect();
      const p = toWorld(e.clientX - r.left, e.clientY - r.top);
      const n = pick(p.x, p.y);
      if (n) {
        dragging = n;
        canvas.classList.add("dragging");
        selectNode(n);
      } else {
        panning = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy, moved: false };
      }
    });
    window.addEventListener("mousemove", (e) => {
      if (dragging) {
        const r = canvas.getBoundingClientRect();
        const p = toWorld(e.clientX - r.left, e.clientY - r.top);
        dragging.x = p.x; dragging.y = p.y; dragging.fx = p.x; dragging.fy = p.y;
        draw();
      } else if (panning) {
        const dx = e.clientX - panning.x, dy = e.clientY - panning.y;
        if (Math.hypot(dx, dy) > 3) panning.moved = true;
        view.cx = panning.cx - dx / view.k;
        view.cy = panning.cy - dy / view.k;
        draw();
      }
    });
    window.addEventListener("mouseup", (e) => {
      if (dragging) { delete dragging.fx; delete dragging.fy; dragging = null; canvas.classList.remove("dragging"); }
      if (panning) {
        if (!panning.moved) clearSelection();
        panning = null;
      }
    });
    canvas.addEventListener("mousemove", (e) => {
      if (dragging || panning) return;
      const r = canvas.getBoundingClientRect();
      const p = toWorld(e.clientX - r.left, e.clientY - r.top);
      const n = pick(p.x, p.y);
      if (n !== state.hovered) { state.hovered = n; canvas.style.cursor = n ? "pointer" : "grab"; draw(); }
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      const p = toWorld(e.clientX - r.left, e.clientY - r.top);
      const k2 = Math.min(3.2, Math.max(0.35, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
      view.cx = p.x - (p.x - view.cx) * (view.k / k2);
      view.cy = p.y - (p.y - view.cy) * (view.k / k2);
      view.k = k2;
      draw();
    }, { passive: false });

    window.addEventListener("resize", () => { resizeCanvas(); draw(); });
    resizeCanvas();
  }

  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 800, h = canvas.clientHeight || 560;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  async function loadGraph() {
    if (!canvas) return;
    try {
      const data = await req("GET", `/api/graph?limit=${state.graphLimit}`);
      state.graph = data;
      if (!data.nodes || !data.nodes.length) {
        $("#graph-msg").textContent = "暂无图谱，请先到「构建图谱」生成";
        sim = { nodes: [], edges: [], alpha: 0, raf: 0 };
        draw();
        return;
      }
      startSim(data);
      renderLegend(data);
      $("#graph-msg").textContent =
        `${data.nodes.length} 节点 / ${data.edges.length} 边 · 社区 ${data.communities?.length || 0}`;
    } catch (err) {
      $("#graph-msg").textContent = "加载失败：" + err.message;
    }
  }

  function startSim(data) {
    const nodes = data.nodes.map((n) => ({
      ...n,
      x: view.cx + (Math.random() - 0.5) * 420,
      y: view.cy + (Math.random() - 0.5) * 320,
      vx: 0, vy: 0,
    }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const edges = (data.edges || [])
      .filter((e) => state.showCooc || e.rel !== "共现")
      .map((e) => ({ ...e, s: byId.get(e.source), t: byId.get(e.target) }))
      .filter((e) => e.s && e.t);

    sim = { nodes, edges, alpha: 1, raf: 0 };
    state.selected = null;
    renderSide(null);

    cancelAnimationFrame(sim.raf);
    let iter = 0;
    const step = () => {
      const alpha = sim.alpha;
      // 斥力
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          const d = Math.sqrt(d2);
          const rep = 2600 / d2;
          a.vx += (dx / d) * rep * alpha; a.vy += (dy / d) * rep * alpha;
          b.vx -= (dx / d) * rep * alpha; b.vy -= (dy / d) * rep * alpha;
        }
      }
      // 弹簧
      for (const e of edges) {
        const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
        const d = Math.hypot(dx, dy) || 1;
        const len = e.rel === "共现" ? 130 : 95;
        const f = (d - len) * 0.012;
        e.s.vx += (dx / d) * f; e.s.vy += (dy / d) * f;
        e.t.vx -= (dx / d) * f; e.t.vy -= (dy / d) * f;
      }
      // 向心 + 阻尼
      for (const n of nodes) {
        n.vx += (view.cx - n.x) * 0.0025;
        n.vy += (view.cy - n.y) * 0.0025;
        n.vx *= 0.86; n.vy *= 0.86;
        if (n.fx !== undefined) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; }
        else { n.x += n.vx; n.y += n.vy; }
      }
      sim.alpha *= 0.975;
      draw();
      iter++;
      if (iter < 340 && sim.alpha > 0.02) sim.raf = requestAnimationFrame(step);
    };
    step();
  }

  function nodeColor(n) { return TYPE_COLORS[n.type] || "#8ba0b4"; }
  function nodeRadius(n) { return 4 + Math.min(10, n.degree || 0) * 1.05; }

  function draw() {
    if (!ctx) return;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(W / 2, H / 2);
    ctx.scale(view.k, view.k);
    ctx.translate(-view.cx, -view.cy);

    const sel = state.selected, hov = state.hovered;
    const linked = new Set();
    if (sel) linked.add(sel.id);
    if (sel) for (const e of sim.edges) {
      if (e.source === sel.id) linked.add(e.target);
      if (e.target === sel.id) linked.add(e.source);
    }

    // 边
    for (const e of sim.edges) {
      const active = !sel || linked.has(e.source) && linked.has(e.target);
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      ctx.strokeStyle = active ? (REL_COLORS[e.rel] || "#4a5c72") : "rgba(74,92,114,.18)";
      ctx.globalAlpha = e.rel === "共现" ? (active ? 0.55 : 0.15) : (active ? 0.9 : 0.2);
      ctx.lineWidth = (e.weight || 1) > 2 ? 1.6 : 1;
      if (e.rel === "共现") ctx.setLineDash([3, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.globalAlpha = 1;

    // 节点
    for (const n of sim.nodes) {
      const dim = sel && !linked.has(n.id);
      ctx.beginPath();
      ctx.arc(n.x, n.y, nodeRadius(n), 0, Math.PI * 2);
      ctx.fillStyle = nodeColor(n);
      ctx.globalAlpha = dim ? 0.2 : 1;
      ctx.fill();
      if (n === hov || n === sel) {
        ctx.lineWidth = 2 / view.k;
        ctx.strokeStyle = "#fff";
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // 标签
    ctx.font = "11px 'Microsoft YaHei', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const n of sim.nodes) {
      const important = (n.degree || 0) >= 2 || n === hov || n === sel ||
        (sel && linked.has(n.id));
      if (!important) continue;
      const r = nodeRadius(n);
      const label = n.name.length > 14 ? n.name.slice(0, 13) + "…" : n.name;
      ctx.fillStyle = sel && !linked.has(n.id) ? "rgba(230,237,243,.15)" : "rgba(230,237,243,.92)";
      ctx.fillText(label, n.x, n.y + r + 10);
    }
    ctx.restore();
  }

  function renderLegend(data) {
    const types = new Set((data.nodes || []).map((n) => n.type));
    const el = $("#legend");
    const parts = [];
    for (const t of Object.keys(TYPE_COLORS)) {
      if (!types.has(t)) continue;
      parts.push(`<span><i style="background:${TYPE_COLORS[t]}"></i>${TYPE_NAMES[t] || t}</span>`);
    }
    parts.push('<span><i style="background:#33465c"></i>共现边</span>');
    el.innerHTML = parts.join("");
  }

  function focusSearch() {
    const q = ($("#node-search").value || "").trim();
    if (!q || !sim.nodes.length) return;
    const n = sim.nodes.find((x) => x.name === q) || sim.nodes.find((x) => x.name.includes(q));
    if (!n) { $("#graph-msg").textContent = `未找到实体「${q}」`; return; }
    view.cx = n.x; view.cy = n.y;
    selectNode(n);
    draw();
  }

  function selectNode(n) {
    state.selected = n;
    renderSide(n);
    draw();
  }

  function clearSelection() {
    state.selected = null;
    renderSide(null);
    draw();
  }

  function renderSide(n) {
    const side = $("#graph-side");
    if (!n) { side.innerHTML = '<p class="empty">点击节点查看详情</p>'; return; }
    const color = nodeColor(n);
    const rels = (state.graph?.edges || []).filter((e) => e.source === n.id || e.target === n.id);
    const groups = {};
    for (const e of rels) {
      const other = e.source === n.id ? e.target : e.source;
      (groups[e.rel] = groups[e.rel] || []).push(other);
    }
    const relHtml = Object.entries(groups).map(([rel, list]) => `
      <h3 style="margin-top:12px">${esc(rel)} <span class="muted">(${list.length})</span></h3>
      <ul>${list.slice(0, 12).map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`).join("");
    const evi = (n.evidence || []).slice(0, 2).map((s) => `<div class="evi">${esc(s)}</div>`).join("");
    side.innerHTML = `
      <span class="type-pill" style="background:${color}22;color:${color}">${TYPE_NAMES[n.type] || n.type}</span>
      <h3>${esc(n.name)}</h3>
      <p class="muted">出现 ${n.chunk_count || (n.chunk_ids || []).length} 次 · 关联 ${rels.length} 条</p>
      ${relHtml}
      ${evi}`;
  }

  /* ---------------- 问答 ---------------- */
  function initQA() {
    $("#btn-ask").addEventListener("click", ask);
    $("#question").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ask(); }
    });
    $("#use-graph").addEventListener("change", (e) => { state.useGraph = e.target.checked; });
    $("#btn-clear-history").addEventListener("click", async () => {
      await req("DELETE", "/api/history").catch(() => {});
      loadHistory();
    });
  }

  async function ask() {
    const q = $("#question").value.trim();
    if (!q) { $("#question").focus(); return; }
    const btn = $("#btn-ask");
    btn.disabled = true;
    btn.textContent = "检索中…";
    try {
      const res = await req("POST", "/api/ask", {
        question: q,
        file_id: $("#file-filter").value || null,
        use_graph: state.useGraph,
      });
      renderAnswer(res);
      await loadHistory();
      await loadStatus();
    } catch (err) {
      renderAnswer({ answer: "问答失败：" + err.message, citations: [], routes: {}, mode: "error" });
    } finally {
      btn.disabled = false;
      btn.textContent = "提问";
    }
  }

  function renderAnswer(res) {
    $("#answer-card").hidden = false;
    $("#answer-text").textContent = res.answer || "（空）";
    $("#answer-mode").textContent = res.mode || "-";
    const meta = [];
    if (res.latency_ms !== undefined) meta.push(`${res.latency_ms} ms`);
    if (res.candidates !== undefined) meta.push(`${res.candidates} 个候选切块`);
    if (res.cached) meta.push("命中缓存");
    $("#answer-meta").textContent = meta.join(" · ");
    $("#qa-mode-hint").textContent = res.use_graph === false
      ? "检索：向量 / BM25 / 位置 → 加权融合（未启用图谱路）"
      : "检索：向量 / BM25 / 实体关系 / 位置 → 加权融合";

    $("#answer-routes").innerHTML = Object.entries(res.routes || {}).map(([k, v]) =>
      `<span>${routeName(k)} <b>${v}</b></span>`).join("");

    const cites = (res.citations || []).map((c, i) => `
      <li>
        <b>[${i + 1}] ${esc(c.doc_title || c.doc_id || "文档")}</b>
        <span class="muted"> · ${esc(c.heading || "正文")} · ${esc(c.chunk_id || "")}</span>
        <p>${esc(c.text || "")}</p>
      </li>`).join("");
    $("#answer-cites").innerHTML = cites || '<li class="empty">没有检索到相关段落</li>';
    $("#answer-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function routeName(k) {
    return { vector: "向量", bm25: "BM25", entity: "实体关系", position: "位置" }[k] || k;
  }

  async function loadHistory() {
    try {
      const data = await req("GET", "/api/history?limit=20");
      const list = data.history || [];
      const el = $("#history-list");
      if (!list.length) { el.innerHTML = '<li class="empty">暂无记录</li>'; return; }
      el.innerHTML = list.map((h) => `
        <li>
          <div class="q">${esc(h.question)}</div>
          <div class="meta">${esc((h.answer || "").slice(0, 90))}${(h.answer || "").length > 90 ? "…" : ""} · ${esc(h.created_at || "")}</div>
        </li>`).join("");
    } catch { /* ignore */ }
  }

  /* ---------------- utils ---------------- */
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function boot() {
    initTabs();
    initUpload();
    initBuild();
    initGraph();
    initQA();
    loadStatus();
    loadFiles();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
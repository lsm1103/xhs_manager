/* XHS Studio 控制台 · P0
 *
 * 只读。所有数据来自 /console/api/*，页面不发任何写请求。
 * 零构建、零依赖——和 tts_studio 同一套范式。
 */

const main = document.getElementById("nav") && document.getElementById("main");
const nav = document.getElementById("nav");

let view = "tasks";
let taskId = null;
let taskFilter = "all";
let cache = { tasks: null, runs: null, task: {}, tools: null };

const BUCKETS = [
  ["act", "待处理"],
  ["err", "出错"],
  ["run", "进行中"],
  ["done", "已完成"],
];
const BUCKET_TONE = { act: "wait", err: "bad", run: "run", done: "ok" };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* 相对时间：列表里「3 分钟前」比一个绝对时间戳好扫 */
function ago(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const min = Math.round((Date.now() - then) / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.round(h / 24);
  return d < 30 ? `${d} 天前` : new Date(iso).toISOString().slice(0, 10);
}

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* 非 JSON 错误体 */ }
    throw new Error(detail);
  }
  return res.json();
}

function head(title, sub, acts = "") {
  return `<div class="head"><div><h1>${title}</h1><div class="sub">${sub}</div></div>
    ${acts ? `<div class="acts">${acts}</div>` : ""}</div>`;
}

function fileUrl(p) {
  return `/console/api/file?path=${encodeURIComponent(p)}`;
}

/* ── 任务台 ── */
function renderTasks(data) {
  const { tasks, counts } = data;
  const opts = [["all", `全部 ${tasks.length}`]]
    .concat(BUCKETS.filter(([k]) => counts[k]).map(([k, l]) => [k, `${l} ${counts[k]}`]));

  const list = tasks.filter((t) => taskFilter === "all" || t.bucket === taskFilter);

  let rows = "", lastBucket = null;
  for (const t of list) {
    if (taskFilter === "all" && t.bucket !== lastBucket) {
      lastBucket = t.bucket;
      const label = (BUCKETS.find(([k]) => k === t.bucket) || [, t.bucket])[1];
      rows += `<tr class="groupdiv"><td colspan="5">${label} · ${counts[t.bucket]}</td></tr>`;
    }
    const cls = t.bucket === "act" ? "act" : t.bucket === "err" ? "err" : "";
    rows += `<tr class="clickable ${cls}" data-task="${esc(t.id)}" tabindex="0" role="link">
      <td><span class="ttl">${esc(t.title)}</span>
        <span class="meta">${esc(t.id.slice(0, 8))}${t.orphan ? " · 游离任务" : ""}</span></td>
      <td><span class="tag ${t.format === "video" ? "vid" : "art"}">${t.format === "video" ? "视频" : "图文"}</span></td>
      <td><span class="tag ${BUCKET_TONE[t.bucket] || "off"}">${esc(t.state_label)}</span></td>
      <td class="mono">${esc(t.output)}</td>
      <td class="mono">${ago(t.updated_at)}</td>
    </tr>`;
  }

  if (!list.length) {
    rows = `<tr><td colspan="5" class="empty">这个筛选下没有任务</td></tr>`;
  }

  return head("任务台",
      `待处理 ${counts.act} · 出错 ${counts.err} · 进行中 ${counts.run} · 已完成 ${counts.done}`) +
    `<div class="bar">
      <div class="seg"><span class="lbl">筛选</span><div class="opts">${opts
        .map(([k, l]) => `<button data-tf="${k}" aria-pressed="${taskFilter === k}">${l}</button>`)
        .join("")}</div></div>
      <span class="spacer"></span><span class="count">${list.length} 条</span>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th style="width:42%">任务</th><th>形态</th><th>状态</th><th>产出</th><th>更新</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

/* ── 任务详情 ── */
function renderTask(t, siblings) {
  const i = siblings.findIndex((x) => x.id === t.id);
  const prev = siblings[i - 1], next = siblings[i + 1];

  /* 一致性校验：四个总时长并排。这一条是为一次真实事故加的——
     校准后的场景时长没落库，画面按 195 秒排、音频只有 168 秒。 */
  let check = "";
  if (t.consistency) {
    const c = t.consistency;
    const cells = c.items.map((it) => `<div><span class="k">${it.label}</span>
      <span class="v${it.value == null ? " na" : ""}">${it.value == null ? "—" : it.value.toFixed(1) + "s"}</span></div>`).join("");
    check = `<div class="check">${cells}
      <div><span class="k">最大漂移</span>
        <span class="v ${c.measured < 2 ? "na" : c.pass ? "good" : "bad"}">${
          c.measured < 2 ? "—" : c.drift.toFixed(2) + "s" + (c.pass ? " ✓" : " ✗")}</span></div></div>`;
    if (c.measured >= 2 && !c.pass) {
      check = `<div class="banner">
        <div><div class="t">时长对不上：最大漂移 ${c.drift.toFixed(2)}s</div>
          <div class="d">${c.items.filter((x) => x.value != null)
            .map((x) => `${x.label} ${x.value.toFixed(1)}s`).join(" · ")}</div>
          <div class="fix">画面时间轴和音频不是同一个时长，成片会从中途开始音画错位。
            多半是场景时长校准后没有落库——重跑 materializing 再重新组合。</div>
        </div></div>` + check;
    }
  }

  const stageHtml = t.stages.map((s) => {
    const node = s.failed ? "bad" : s.state === "done" ? "" : s.state === "current" ? "wait" : "todo";
    const tag = s.failed ? ["失败", "bad"]
      : s.state === "done" ? ["完成", "ok"]
      : s.state === "current" ? ["进行中", "run"] : ["未开始", "off"];
    const links = [];
    if (s.html_path) links.push(`<a href="${fileUrl(s.html_path)}" target="_blank" rel="noopener">打开组合页</a>`);
    if (s.output_path) links.push(`<a href="${fileUrl(s.output_path)}" target="_blank" rel="noopener">打开成片</a>`);
    return `<div class="sub-row${s.failed ? " failed" : ""}">
      <span class="en">${esc(s.stage)}</span>
      <span class="d"><b class="hl">${esc(s.label)}</b> · ${esc(s.detail)}
        ${links.length ? ` · ${links.join(" · ")}` : ""}</span>
      <span class="t"><span class="tag ${tag[1]}">${tag[0]}</span></span>
    </div>`;
  }).join("");

  const video = t.stages.find((s) => s.output_path);
  const player = video ? `<div class="player">
      <video src="${fileUrl(video.output_path)}" controls preload="metadata"></video>
      <div class="pmeta"><code>${esc(video.output_path)}</code></div>
    </div>` : "";

  const scenes = t.scenes.length ? `<section class="block">
      <h3>逐场景 <span class="cnt">${t.scenes.length}</span></h3>
      <div class="tablewrap"><table>
        <thead><tr><th>#</th><th>版面</th><th>时长</th><th>屏幕文字</th><th>旁白</th><th>时间标记</th></tr></thead>
        <tbody>${t.scenes.map((s) => `<tr>
          <td class="mono">${s.index}</td>
          <td class="mono">${esc(s.layout)}</td>
          <td class="mono">${s.duration == null ? "—" : s.duration + "s"}</td>
          <td><span class="ttl">${esc(s.main)}</span>${s.sub ? `<span class="meta">${esc(s.sub)}</span>` : ""}</td>
          <td class="narr">${esc(s.narration)}</td>
          <td class="mono">${s.marks ? s.marks + " 句" : "—"}</td>
        </tr>`).join("")}</tbody></table></div>
    </section>` : "";

  const pubs = t.publications.length ? `<section class="block">
      <h3>发布</h3>
      <div class="tablewrap"><table>
        <thead><tr><th>平台</th><th>状态</th><th>标题</th><th>结果</th></tr></thead>
        <tbody>${t.publications.map((p) => `<tr>
          <td class="mono">${esc(p.platform)}</td>
          <td><span class="tag ${p.status === "published" ? "ok" : p.status === "failed" ? "bad" : "off"}">${esc(p.status)}</span></td>
          <td>${esc(p.title)}</td>
          <td class="mono">${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">查看</a>`
            : esc(p.error || "—")}</td>
        </tr>`).join("")}</tbody></table></div>
    </section>` : "";

  const pager = `<div class="acts">
      <button class="btn" data-goto="${prev ? esc(prev.id) : ""}" ${prev ? "" : "disabled"}>← 上一条</button>
      <button class="btn" data-goto="${next ? esc(next.id) : ""}" ${next ? "" : "disabled"}>下一条 →</button>
    </div>`;

  const runLine = t.run
    ? `run ${t.run.id.slice(0, 8)} · ${t.run.date} · ${t.run.status}` : "无关联运行";

  return `<button class="back" data-view="tasks">← 任务台</button>` +
    head(esc(t.title),
      `${esc(t.id.slice(0, 8))} · 视频 · ${esc(t.state_label)} · ${runLine}${t.orphan ? " · 游离任务" : ""}`,
      pager) +
    (t.run && t.run.error ? `<div class="banner"><div>
        <div class="t">流水线失败</div><div class="d">${esc(t.run.error)}</div></div></div>` : "") +
    check +
    `<section class="block"><h3>角度</h3>
      <p class="prose">${esc(t.angle)}</p>
      <p class="prose dim">为什么是现在：${esc(t.why_now)} · 面向：${esc(t.audience)}</p></section>` +
    `<section class="block"><h3>流水线</h3><div class="sub">${stageHtml}</div></section>` +
    (player ? `<section class="block"><h3>成片</h3>${player}</section>` : "") +
    scenes + pubs;
}

/* ── 运行列表 ── */
function renderRuns(runs) {
  const rows = runs.map((r) => `<tr class="${r.error ? "err" : ""}">
    <td class="mono">${esc(r.id.slice(0, 8))}</td>
    <td class="mono">${esc(r.date)}</td>
    <td><span class="tag ${r.status === "completed" ? "ok" : r.status === "failed" ? "bad" : "run"}">${esc(r.status)}</span></td>
    <td class="mono">${r.trends}</td><td class="mono">${r.topics}</td>
    <td class="mono">${r.videos}</td><td class="mono">${r.published}</td>
    <td class="mono">${esc(r.trigger)}</td>
    <td class="mono">${r.error ? `<span style="color:var(--bad)">${esc(r.error.slice(0, 60))}</span>` : "—"}</td>
  </tr>`).join("");

  return head("流水线运行", `${runs.length} 次运行`) +
    `<div class="tablewrap"><table>
      <thead><tr><th>ID</th><th>日期</th><th>状态</th><th>信号</th><th>选题</th>
        <th>视频</th><th>已发</th><th>触发</th><th>错误</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

/* ── 工具体检 ── */
const TOOL_TONE = { ok: "ok", degraded: "wait", down: "bad", unknown: "off" };
const TOOL_LABEL = { ok: "正常", degraded: "降级", down: "不可用", unknown: "未知" };

function renderTools(data) {
  const { probes, counts } = data;

  const rows = probes.map((p) => {
    /* 采集后端链带 13 个平台的明细，摊开比一句「降级 2」有用 */
    const chain = p.facts && p.facts.platforms
      ? `<div class="chainlist">${Object.entries(p.facts.platforms)
          .sort((a, b) => ["down", "degraded", "unknown", "ok"].indexOf(a[1])
                        - ["down", "degraded", "unknown", "ok"].indexOf(b[1]))
          .map(([name, st]) => `<span class="${st}">${esc(name)}</span>`).join("")}</div>`
      : "";
    return `<div class="tool ${p.status}">
      <div class="n">${esc(p.name)} <span class="tag ${TOOL_TONE[p.status]}">${TOOL_LABEL[p.status]}</span></div>
      <div><span class="x">${esc(p.detail)}</span>
        ${p.fix ? `<span class="fix">→ ${esc(p.fix)}</span>` : ""}${chain}</div>
      <div class="ms">${p.elapsed_ms} ms</div>
    </div>`;
  }).join("");

  const bad = counts.down + counts.unknown;
  return head("工具体检",
      `${probes.length} 项 · 不可用 ${counts.down} · 降级 ${counts.degraded} · 正常 ${counts.ok}`
      + (data.cached ? " · 缓存结果" : ` · 探测耗时 ${data.elapsed_ms}ms`),
      `<button class="btn" id="recheck">重新体检</button>`) +
    (bad ? "" : `<div class="ok-note">所有外部依赖正常。</div>`) +
    `<div class="tools">${rows}</div>`;
}

/* ── 路由 ── */
async function render() {
  const el = document.getElementById("main");
  try {
    if (view === "tasks") {
      cache.tasks = cache.tasks || await api("/console/api/tasks");
      el.innerHTML = renderTasks(cache.tasks);
    } else if (view === "task") {
      cache.tasks = cache.tasks || await api("/console/api/tasks");
      cache.task[taskId] = cache.task[taskId] || await api(`/console/api/tasks/${taskId}`);
      el.innerHTML = renderTask(cache.task[taskId], cache.tasks.tasks);
    } else if (view === "runs") {
      cache.runs = cache.runs || await api("/console/api/runs");
      el.innerHTML = renderRuns(cache.runs.runs);
    } else if (view === "tools") {
      cache.tools = cache.tools || await api("/console/api/tools");
      el.innerHTML = renderTools(cache.tools);
    }
  } catch (e) {
    el.innerHTML = `<div class="banner"><div><div class="t">加载失败</div>
      <div class="d">${esc(e.message)}</div>
      <div class="fix">确认服务还在跑：<code>uvicorn xhs_manager.api:app</code></div></div></div>`;
  }

  nav.querySelectorAll("button[data-view]").forEach((b) => {
    const on = b.dataset.view === view || (view === "task" && b.dataset.view === "tasks");
    b.setAttribute("aria-current", on ? "page" : "false");
  });
  if (cache.tools) {
    const tb = document.getElementById("badge-tools");
    const n = cache.tools.counts.down + cache.tools.counts.unknown;
    tb.textContent = n;
    tb.hidden = !n;
  }
  if (cache.tasks) {
    const badge = document.getElementById("badge-act");
    const n = cache.tasks.counts.act + cache.tasks.counts.err;
    badge.textContent = n;
    badge.hidden = !n;
    badge.className = "badge " + (cache.tasks.counts.err ? "bad" : "act");
  }
  window.scrollTo(0, 0);
}

function go(next, id) {
  view = next;
  taskId = id ?? null;
  location.hash = next === "task" ? `#/task/${id}` : `#/${next}`;
  render();
}

function fromHash() {
  const m = /^#\/task\/(.+)$/.exec(location.hash);
  if (m) { view = "task"; taskId = m[1]; return; }
  const v = (location.hash || "#/tasks").slice(2);
  view = ["tasks", "runs", "tools"].includes(v) ? v : "tasks";
  taskId = null;
}

nav.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) go(b.dataset.view);
});

document.getElementById("main").addEventListener("click", (e) => {
  const goto = e.target.closest("[data-goto]");
  if (goto && goto.dataset.goto) return go("task", goto.dataset.goto);

  const back = e.target.closest("[data-view]");
  if (back) return go(back.dataset.view);

  if (e.target.closest("a")) return;              // 让产物链接正常打开

  const row = e.target.closest("[data-task]");
  if (row) return go("task", row.dataset.task);

  const tf = e.target.closest("[data-tf]");
  if (tf) { taskFilter = tf.dataset.tf; return render(); }

  if (e.target.id === "recheck") {
    e.target.textContent = "体检中…";
    e.target.disabled = true;
    cache.tools = null;
    api("/console/api/tools?refresh=true")
      .then((d) => { cache.tools = d; render(); })
      .catch(() => render());
  }
});

document.getElementById("main").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest("[data-task]");
  if (row) { e.preventDefault(); go("task", row.dataset.task); }
});

/* j / k 在任务之间跳，Esc 回列表——处理一串任务时不用来回点 */
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
  if (e.key === "Escape" && view === "task") return go("tasks");
  if (view !== "task" || !cache.tasks) return;
  const list = cache.tasks.tasks;
  const i = list.findIndex((x) => x.id === taskId);
  if (e.key === "j" && list[i + 1]) go("task", list[i + 1].id);
  if (e.key === "k" && list[i - 1]) go("task", list[i - 1].id);
});

window.addEventListener("hashchange", () => { fromHash(); render(); });

fromHash();
render();

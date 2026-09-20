/* XHS Studio 控制台
 *
 * 数据来自 /console/api/*。读随便读；写只有四个动作——
 * 批准、打回、撤销排期、重跑某个阶段，都要按两下。
 * 零构建、零依赖——和 tts_studio 同一套范式。
 */

const main = document.getElementById("nav") && document.getElementById("main");
const nav = document.getElementById("nav");

let view = "tasks";
let taskId = null;
let taskFilter = "all";
let cache = { tasks: null, runs: null, task: {}, tools: null,
              signals: null, assets: null, tts: null };
let sigPlatform = "all";
let ttsModel = null;          // 生成表单当前选的模型

const BUCKETS = [
  ["act", "待处理"],
  ["err", "出错"],
  ["run", "进行中"],
  ["done", "已完成"],
  ["off", "已收起"],
];
const BUCKET_TONE = { act: "wait", err: "bad", run: "run", done: "ok", off: "off" };

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
  /* no-store：控制台显示的是活的状态。浏览器缓存一个任务详情，
     就会让人对着一份旧快照去按批准。 */
  const res = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" });
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

/* ── 写动作 ──────────────────────────────────────────────
   批准会真的把片子推出去，所以两道约束写死在这里：
   1) 每个写请求都带 X-Console-Action。跨源页面发不出自定义头，
      预检也过不了——整类 CSRF 就挡在这一行。
   2) 每个按钮按两下才生效，而且确认就长在按钮原地。
      弹到屏幕另一头的确认框，人只会盲点。 */

const TOKEN_KEY = "xhs.console.token";

async function post(path, body) {
  const headers = { "Content-Type": "application/json", "X-Console-Action": "1" };
  let tok = null;
  try { tok = localStorage.getItem(TOKEN_KEY); } catch (_) { /* 隐私模式 */ }
  if (tok) headers["X-Console-Token"] = tok;
  const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await res.json(); } catch (_) { /* 空响应体 */ }
  if (!res.ok) throw new Error(data.detail || `请求失败（${res.status}）`);
  return data;
}

async function del(path) {
  const headers = { "X-Console-Action": "1" };
  let tok = null;
  try { tok = localStorage.getItem(TOKEN_KEY); } catch (_) { /* 隐私模式 */ }
  if (tok) headers["X-Console-Token"] = tok;
  const res = await fetch(path, { method: "DELETE", headers });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* 空响应体 */ }
    throw new Error(detail);
  }
  return res.json();
}

/* 本地时间 → datetime-local 的值。排期输入框用本人所在时区，
   显示成 UTC 只会让人排错时间。 */
function localInput(d) {
  const off = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
}

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit",
                                 hour: "2-digit", minute: "2-digit" });
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
      rows += `<tr class="groupdiv"><td colspan="6">${label} · ${counts[t.bucket]}</td></tr>`;
    }
    const cls = t.bucket === "act" ? "act" : t.bucket === "err" ? "err"
      : t.bucket === "off" ? "off" : "";
    rows += `<tr class="clickable ${cls}" data-task="${esc(t.id)}" tabindex="0" role="link">
      <td><span class="ttl">${esc(t.title)}</span>
        <span class="meta">${esc(t.id.slice(0, 8))}${t.orphan ? " · 游离任务" : ""}</span></td>
      <td><span class="tag ${t.format === "video" ? "vid" : "art"}">${t.format === "video" ? "视频" : "图文"}</span></td>
      <td><span class="tag ${BUCKET_TONE[t.bucket] || "off"}">${esc(t.state_label)}</span></td>
      <td class="mono">${esc(t.output)}</td>
      <td class="mono">${ago(t.updated_at)}</td>
      <td>${t.format === "video" ? markMenu(t.state, t.id) : ""}</td>
    </tr>`;
  }

  if (!list.length) {
    rows = `<tr><td colspan="6" class="empty">这个筛选下没有任务</td></tr>`;
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
      <thead><tr><th style="width:38%">任务</th><th>形态</th><th>状态</th><th>产出</th><th>更新</th><th>标记</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

/* ── 任务详情 ── */
/* 按两下才生效的按钮。第二下之前它长这样：[确认批准] [取消] */
function act(kind, label, tone, confirm) {
  return `<button class="btn ${tone}" data-act="${kind}" data-confirm="${esc(confirm)}">${label}</button>`;
}

/* 人工标记：不想要的片子自己收起来。
   用 select 而不是一排按钮——这不是危险动作，不值得占三个按钮的位置，
   而且它要能显示「当前是什么标记」。 */
const MARKS = [["", "未标记"], ["dropped", "放弃"], ["expired", "已过期"]];

function markMenu(state, id) {
  const cur = MARKS.some((m) => m[0] === state) ? state : "";
  return `<select class="mark${cur ? " on" : ""}" data-mark${id ? `="${esc(id)}"` : ""}>
    ${MARKS.map(([v, label]) =>
      `<option value="${v}"${v === cur ? " selected" : ""}>${label}</option>`).join("")}
  </select>`;
}

/* 人工发布清单。
   自动发布封过一次号，所以这一块不驱动任何浏览器——
   它只是把要填的东西摆出来，一键复制，你自己去发。 */

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // 非安全上下文拿不到 clipboard API，退回老办法
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } finally { ta.remove(); }
  }
  const was = btn.textContent;
  btn.textContent = "已复制";
  btn.classList.add("copied");
  setTimeout(() => { btn.textContent = was; btn.classList.remove("copied"); }, 1400);
}

function field(label, value, hint) {
  return `<div class="cfield">
    <div class="chead">
      <span class="k">${label}</span>
      ${hint ? `<span class="hint">${hint}</span>` : ""}
      <span class="cbtns"><button class="btn tiny" data-copy="${esc(value)}">复制</button></span>
    </div>
    <div class="cval">${esc(value) || "<span class=\"dim\">（空）</span>"}</div>
  </div>`;
}

function renderChecklist(t) {
  const c = t.checklist;
  if (!c) return "";

  const pub = (t.publications || []).find((p) => p.platform === "xiaohongshu");
  const done = pub && pub.status === "published";
  const failed = pub && pub.status === "failed";

  if (done) {
    return `<section class="block"><h3>发布</h3>
      <div class="plan">
        <div><span class="k">状态</span><span class="tag ok">已发布</span></div>
        <div><span class="k">方式</span><span class="v">${esc(pub.method || "—")}</span></div>
        <div><span class="k">时间</span><span class="v">${when(pub.published_at)}</span></div>
      </div>
      ${pub.url ? `<p class="prose"><a href="${esc(pub.url)}" target="_blank"
         rel="noopener">${esc(pub.url)}</a></p>` : ""}
      <div class="acts" data-pub="${esc(pub.id)}">
        ${act("unpublish", "撤销「已发布」", "", "确认撤销？")}
      </div></section>`;
  }

  const mb = c.file_size ? (c.file_size / 1024 / 1024).toFixed(1) + " MB" : "";
  const dur = c.duration ? `${Math.floor(c.duration / 60)}:${String(
    Math.round(c.duration % 60)).padStart(2, "0")}` : "";

  return `<section class="block checklist"><h3>人工发布清单</h3>
    <p class="prose dim">这一步不碰浏览器。把下面几项复制到小红书，发完回来点「我已发布」。</p>
    ${failed ? `<div class="banner"><div>
        <div class="t">上次自动发布失败</div>
        <div class="d">${esc((pub.error || "").slice(0, 260))}</div>
        <div class="fix">已经改成人工发布。按下面的清单自己发一次就行。</div>
      </div></div>` : ""}

    ${field("标题", c.title, `${c.title_len} 字${c.title_len > 20 ? " · 超过 20 字会被截断" : ""}`)}
    ${field("正文（已含标签）", c.body, `${c.tags.length} 个标签`)}

    <div class="cfield">
      <div class="chead">
        <span class="k">视频文件</span>
        <span class="hint">${[dur, mb].filter(Boolean).join(" · ")}</span>
        <span class="cbtns">
          <button class="btn tiny" data-copy="${esc(c.video_path)}">复制路径</button>
          <a class="btn tiny" href="${fileUrl(c.video_path)}" download>下载</a>
        </span>
      </div>
      <div class="cval mono">${esc(c.video_path)}</div>
    </div>
    ${c.cover_path ? `<div class="cfield">
      <div class="chead"><span class="k">封面</span>
        <span class="cbtns">
          <button class="btn tiny" data-copy="${esc(c.cover_path)}">复制路径</button>
          <a class="btn tiny" href="${fileUrl(c.cover_path)}" target="_blank" rel="noopener">查看</a>
        </span>
      </div>
      <div class="cval mono">${esc(c.cover_path)}</div></div>` : ""}

    ${c.publication_id ? `<div class="sched">
        <label>发布后的链接（可不填）
          <input id="pub-url" placeholder="https://www.xiaohongshu.com/explore/..."></label>
      </div>
      <div class="acts" data-pub="${esc(c.publication_id)}">
        ${act("published", "我已发布", "primary", "确认已发布？")}
        ${failed ? act("retry", "清掉失败记录", "", "确认清掉？") : ""}
      </div>`
      : `<p class="prose dim">还没有发布记录——这支片子还没走到发布阶段。</p>`}
  </section>`;
}

/* 发布审批。这一块是整个控制台唯一会对外产生后果的地方。 */
function renderApproval(t) {
  const a = t.approval, p = t.plan;
  if (!a && !p) return "";

  const live = p && p.status === "scheduled";
  /* 撤销过排期之后还要能重新排。审批本身没被打回，缺的只是一个时间。 */
  const canSchedule = a && (a.status === "pending"
    || (a.status === "approved" && p && p.status === "cancelled"));
  let body;

  if (live) {
    const late = new Date(p.allowed_until).getTime() < Date.now();
    body = `<div class="plan">
        <div><span class="k">排期</span><span class="v">${when(p.scheduled_at)}</span></div>
        <div><span class="k">窗口截止</span><span class="v${late ? " bad" : ""}">${when(p.allowed_until)}</span></div>
        <div><span class="k">状态</span><span class="tag ${late ? "bad" : "wait"}">${late ? "已错过窗口" : "等待发布"}</span></div>
      </div>
      <p class="prose dim">到点后由 worker 发布。${late
        ? "窗口已过，worker 不会再发——要发得重新排期。"
        : "现在还能收回。"}</p>
      <div class="acts" data-plan="${esc(p.id)}">
        ${act("cancel", "撤销排期", "danger", "确认撤销？")}
      </div>`;
  } else if (canSchedule) {
    const again = a.status === "approved";
    const dflt = localInput(new Date(Date.now() + 30 * 60000));
    body = `<p class="prose">${again
        ? "上一次排期已撤销。重新选个时间就能再排一次。"
        : "批准之后，worker 会在排期时间把这支片子发出去。"}
        窗口过了就不发——宁可晚一天，也不半夜推出去。</p>
      <div class="sched">
        <label>发布时间<input type="datetime-local" id="sched-at" value="${dflt}"></label>
        <label>窗口<select id="sched-win">
          <option value="1">1 小时</option>
          <option value="2" selected>2 小时</option>
          <option value="6">6 小时</option>
          <option value="24">24 小时</option>
        </select></label>
      </div>
      <div class="acts" data-approval="${esc(a.id)}">
        ${act("approve", again ? "重新排期" : "批准并排期", "primary", "确认排期？")}
        ${act("reject", "打回", "", "确认打回？")}
      </div>`;
  } else {
    const tone = { approved: "ok", rejected: "bad" }[a.status] || "off";
    const label = { approved: "已批准", rejected: "已打回", pending: "待审批" }[a.status] || a.status;
    const extra = p && p.status === "cancelled" ? " · 排期已撤销" : "";
    body = `<div class="plan">
        <div><span class="k">审批</span><span class="tag ${tone}">${label}${extra}</span></div>
      </div>
      <p class="prose dim">${a.status === "rejected"
        ? "打回后要重新提交审批：<code>cli promote</code>。"
        : "没有生效中的排期。"}</p>`;
  }

  return `<section class="block approval"><h3>发布审批</h3>${body}</section>`;
}

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
    /* 重跑按钮只长在能重跑的阶段上。发布不在其中——它得走审批。
       失败的那一行给实心按钮：出错时你要找的就是它。 */
    const can = (t.rerunnable || []).some((r) => r.stage === s.stage);
    const rerun = can
      ? `<span class="t"><button class="btn tiny${s.failed ? " primary" : ""}"
           data-act="rerun" data-stage="${esc(s.stage)}"
           data-confirm="确认重跑？">重跑</button></span>`
      : `<span class="t"></span>`;
    return `<div class="sub-row${s.failed ? " failed" : ""}">
      <span class="en">${esc(s.stage)}</span>
      <span class="d"><b class="hl">${esc(s.label)}</b> · ${esc(s.detail)}
        ${links.length ? ` · ${links.join(" · ")}` : ""}</span>
      <span class="t"><span class="tag ${tag[1]}">${tag[0]}</span></span>
      ${rerun}
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
      ${markMenu(t.state)}
      <button class="btn" data-goto="${prev ? esc(prev.id) : ""}" ${prev ? "" : "disabled"}>← 上一条</button>
      <button class="btn" data-goto="${next ? esc(next.id) : ""}" ${next ? "" : "disabled"}>下一条 →</button>
    </div>`;

  const runLine = t.run
    ? `run ${t.run.id.slice(0, 8)} · ${t.run.date} · ${t.run.status}` : "无关联运行";

  return `<button class="back" data-view="tasks">← 任务台</button>` +
    head(esc(t.title),
      `${esc(t.id.slice(0, 8))} · 视频 · ${esc(t.state_label)} · ${runLine}${t.orphan ? " · 游离任务" : ""}`,
      pager) +
    `<div class="actmsg" id="actmsg" hidden></div>` +
    (t.run && t.run.error ? `<div class="banner"><div>
        <div class="t">流水线失败</div><div class="d">${esc(t.run.error)}</div></div></div>` : "") +
    check +
    `<section class="block"><h3>角度</h3>
      <p class="prose">${esc(t.angle)}</p>
      <p class="prose dim">为什么是现在：${esc(t.why_now)} · 面向：${esc(t.audience)}</p></section>` +
    renderChecklist(t) +
    renderApproval(t) +
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

/* ── 采集信号 ── */
const PLATFORM_CN = { xiaohongshu: "小红书", bilibili: "B站", v2ex: "V2EX",
                      wechat: "公众号", weibo: "微博", toutiao: "头条",
                      zhihu: "知乎", douyin: "抖音", twitter: "X", baidu: "百度" };

function renderSignals(data) {
  const list = sigPlatform === "all"
    ? data.signals : data.signals.filter((s) => s.platform === sigPlatform);

  const opts = [["all", `全部 ${data.signals.length}`]].concat(
    data.platforms.map((p) => [p, PLATFORM_CN[p] || p]));

  const rows = list.map((s) => {
    /* 超过 90 天标出来：热度分只看互动量、不看时效，
       去年的帖子会稳稳排在榜首 */
    const stale = s.age_days != null && s.age_days >= 90;
    const when = s.published_at
      ? `${s.published_at.slice(0, 10)} <span style="color:${stale ? "var(--warn)" : "var(--dim)"}">· ${s.age_days} 天前</span>`
      : `<span style="color:var(--dim)">未提供</span>`;
    return `<tr>
      <td><span class="ttl">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}</span>
        <span class="meta">${esc(s.author || "—")}</span></td>
      <td><span class="tag ${s.via === "站内" ? "vid" : "off"}">${esc(PLATFORM_CN[s.platform] || s.platform)}</span></td>
      <td class="mono">${esc(s.engagement)}</td>
      <td class="mono">${when}</td>
      <td class="mono">${s.heat == null ? "—" : s.heat.toFixed(0)}</td>
      <td class="mono">${esc(s.via)}</td>
    </tr>`;
  }).join("");

  return head("采集信号", `${data.platforms.length} 平台 · 共 ${data.signals.length} 条`) +
    `<div class="bar">
      <div class="seg"><span class="lbl">平台</span><div class="opts">${opts
        .map(([k, l]) => `<button data-plat="${k}" aria-pressed="${sigPlatform === k}">${l}</button>`)
        .join("")}</div></div>
      <span class="spacer"></span><span class="count">${list.length} 条</span>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th style="width:44%">标题</th><th>平台</th><th>互动</th>
        <th>发布</th><th>热度</th><th>通道</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="empty">没有信号</td></tr>`}</tbody>
    </table></div>`;
}

/* ── 资产 ── */
function renderAssets(data) {
  const groups = data.groups.map((g) => `
    <section class="block">
      <h3><a href="#/task/${esc(g.topic_id)}">${esc(g.title)}</a>
        <span class="tag ${BUCKET_TONE[g.bucket] || "off"}">${esc(g.state_label)}</span></h3>
      <div class="sub">${g.items.map((a) => `
        <div class="sub-row">
          <span class="en">${esc(a.kind)}</span>
          <span class="d"><code>${esc(a.path)}</code>${a.meta ? ` · ${esc(a.meta)}` : ""}</span>
          <span class="t"><a href="${fileUrl(a.path)}" target="_blank" rel="noopener">打开</a></span>
        </div>`).join("")}</div>
    </section>`).join("");

  return head("资产", `${data.groups.length} 个任务 · ${data.total} 个产物`) +
    (groups || `<div class="loading">还没有产物</div>`);
}

/* ── 工具体检 ── */
/* ── 配音 ──
   四个模型的加载语义本来就不一样，界面上如实区分而不是抹平：
   edge 无模型（可用性检查），voxcpm2 每次调用自行加载，
   indextts2/omnivoice 是常驻子进程——卸载是真的杀进程。 */

const KIND_CN = { stateless: "云端", cli: "命令行", worker: "常驻进程" };
const TTS_TONE = { ready: "ok", loading: "run", error: "bad", unloaded: "off" };
const TTS_LABEL = { ready: "就绪", loading: "加载中", error: "出错", unloaded: "未加载" };

/* 波形：库里存的是下采样后的峰值，直接画成竖条，不用重新解码音频 */
function waveform(peaks) {
  if (!peaks || !peaks.length) return "";
  const step = Math.max(1, Math.floor(peaks.length / 180));
  const bars = [];
  for (let i = 0; i < peaks.length; i += step) {
    const h = Math.max(2, Math.round(peaks[i] * 100));
    bars.push(`<i style="height:${h}%"></i>`);
  }
  return `<div class="wave">${bars.join("")}</div>`;
}

function ttsModelCard(m) {
  const tone = TTS_TONE[m.status] || "off";
  const caps = [
    m.supports_voice ? "预置音色" : null,
    m.supports_ref ? "音色克隆" : null,
    m.supports_emotion ? "情感控制" : null,
  ].filter(Boolean);
  const busy = m.status === "loading";
  const btn = m.status === "ready"
    ? `<button class="btn tiny danger" data-tts="unload" data-model="${esc(m.id)}"
         data-confirm="${m.resident ? "确认杀进程？" : "确认卸载？"}">卸载</button>`
    : `<button class="btn tiny" data-tts="load" data-model="${esc(m.id)}"
         ${busy ? "disabled" : ""}>${busy ? "加载中…" : "加载"}</button>`;
  return `<div class="mcard${m.status === "ready" ? " on" : ""}">
    <div class="mtop">
      <span class="mname">${esc(m.name)}</span>
      <span class="tag ${tone}">${TTS_LABEL[m.status] || esc(m.status)}</span>
    </div>
    <div class="mmeta">${KIND_CN[m.kind] || esc(m.kind)}${
      m.resident ? " · 常驻" : ""}${
      m.load_elapsed ? ` · 加载 ${m.load_elapsed}s` : ""}${
      caps.length ? " · " + caps.join(" / ") : ""}</div>
    <div class="mnote">${esc(m.note)}</div>
    ${m.error ? `<div class="merr">${esc(m.error)}</div>` : ""}
    <div class="macts">${btn}</div>
  </div>`;
}

function ttsForm(models, refs) {
  const ready = models.filter((m) => m.status === "ready");
  if (!ready.length) {
    return `<section class="block"><h3>生成</h3>
      <p class="prose dim">还没有就绪的模型。先在上面加载一个——
        edge 是云端服务，秒开；indextts2 要 54 秒，但加载后常驻。</p></section>`;
  }
  const cur = ready.find((m) => m.id === ttsModel) || ready[0];
  ttsModel = cur.id;

  const voice = cur.supports_voice && cur.voices.length
    ? `<label>音色<select id="tts-voice">${cur.voices.map((v) =>
        `<option value="${esc(v)}">${esc(v.replace("zh-CN-", "").replace("Neural", ""))}</option>`
      ).join("")}</select></label>` : "";
  const ref = cur.supports_ref
    ? `<label>参考音色${cur.id === "indextts2" ? "（必填）" : ""}
        <select id="tts-ref"><option value="">${
          cur.id === "indextts2" ? "— 请选择 —" : "不使用"}</option>${
          refs.map((r) => `<option value="${esc(r.name)}">${esc(r.name)}${
            r.duration ? ` · ${r.duration.toFixed(1)}s` : ""}</option>`).join("")
        }</select></label>` : "";
  const emo = cur.supports_emotion && cur.emotions.length
    ? `<label>情感<select id="tts-emo"><option value="">不指定</option>${
        cur.emotions.map((e) => `<option value="${esc(e)}">${esc(e)}</option>`).join("")
      }</select></label>` : "";
  const instruct = cur.id === "voxcpm2" || cur.id === "omnivoice"
    ? `<label>指令<input id="tts-instruct" placeholder="如：语气轻快的女声"></label>` : "";

  return `<section class="block"><h3>生成</h3>
    <div class="sched">
      <label>模型<select id="tts-pick">${ready.map((m) =>
        `<option value="${esc(m.id)}"${m.id === cur.id ? " selected" : ""}>${esc(m.name)}</option>`
      ).join("")}</select></label>
      ${voice}${ref}${emo}${instruct}
      <label>语速<input id="tts-speed" type="number" step="0.05" min="0.5" max="2" value="1"></label>
    </div>
    <textarea id="tts-text" class="ttstext" rows="3"
      placeholder="要合成的文本…"></textarea>
    <div class="acts"><button class="btn primary" id="tts-go">生成</button></div>
  </section>`;
}

function renderTts(data) {
  const { models, items, refs } = data;
  const ready = models.filter((m) => m.status === "ready").length;
  const secs = items.reduce((a, b) => a + (b.duration || 0), 0);

  const rows = items.map((g) => {
    const bad = g.status !== "ok";
    const stats = [
      g.duration ? `${g.duration.toFixed(1)}s` : null,
      g.elapsed ? `耗时 ${g.elapsed}s` : null,
      g.rtf ? `RTF ${g.rtf}` : null,
      g.sample_rate ? `${(g.sample_rate / 1000).toFixed(1)}kHz` : null,
      g.ref_audio ? `参考 ${esc(g.ref_audio)}` : null,
      g.voice ? esc(g.voice.replace("zh-CN-", "")) : null,
    ].filter(Boolean).join(" · ");
    return `<div class="gen${bad ? " failed" : ""}">
      <div class="gtop">
        <span class="tag ${bad ? "bad" : "vid"}">${esc(g.model_id)}</span>
        <span class="gtime">${ago(g.created_at)}</span>
        <button class="btn tiny danger" data-tts="drop" data-gen="${esc(g.id)}"
          data-confirm="确认删除？">删除</button>
      </div>
      <div class="gtext">${esc(g.text)}</div>
      ${bad ? `<div class="merr">${esc(g.error || "生成失败")}</div>`
            : `${waveform(g.waveform)}
               <audio src="/tts${esc(g.audio_url)}" controls preload="none"></audio>`}
      <div class="gmeta">${stats}</div>
    </div>`;
  }).join("");

  return head("配音",
    `${models.length} 个模型 · 就绪 ${ready} · 历史 ${items.length} 条 · 合计 ${
      Math.round(secs)}s`,
    `<button class="btn" id="tts-refresh">刷新</button>`) +
    `<div class="actmsg" id="actmsg" hidden></div>` +
    `<section class="block"><h3>模型</h3>
      <div class="mcards">${models.map(ttsModelCard).join("")}</div></section>` +
    ttsForm(models, refs) +
    `<section class="block"><h3>历史 <span class="cnt">${items.length}</span></h3>
      ${items.length ? `<div class="gens">${rows}</div>`
        : `<p class="prose dim">还没有生成记录。</p>`}</section>`;
}

async function loadTts() {
  const [models, gens, refs] = await Promise.all([
    api("/tts/api/models"), api("/tts/api/generations?limit=40"), api("/tts/api/refs"),
  ]);
  return { models: models.models, items: gens.items, refs: refs.items };
}

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
    } else if (view === "signals") {
      cache.signals = cache.signals || await api("/console/api/signals");
      el.innerHTML = renderSignals(cache.signals);
    } else if (view === "assets") {
      cache.assets = cache.assets || await api("/console/api/assets");
      el.innerHTML = renderAssets(cache.assets);
    } else if (view === "tts") {
      cache.tts = cache.tts || await loadTts();
      el.innerHTML = renderTts(cache.tts);
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

/* 导航只改 hash，渲染统一交给 hashchange。
   两边都调 render() 的话每次点击会渲染两次——接口请求翻倍，
   而且 `cache.x = cache.x || await api()` 两次都看到空缓存，
   缓存等于没生效。 */
function go(next, id) {
  disarm();
  const hash = next === "task" ? `#/task/${id}` : `#/${next}`;
  if (location.hash === hash) {
    // hash 没变就不会有 hashchange，这时自己渲染
    view = next;
    taskId = id ?? null;
    return render();
  }
  location.hash = hash;
}

function fromHash() {
  const m = /^#\/task\/(.+)$/.exec(location.hash);
  if (m) { view = "task"; taskId = m[1]; return; }
  const v = (location.hash || "#/tasks").slice(2);
  view = ["tasks", "runs", "tools", "signals", "assets", "tts"].includes(v) ? v : "tasks";
  taskId = null;
}

nav.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (b) go(b.dataset.view);
});

/* 第一下武装、第二下执行。武装状态 6 秒后自己解除——
   点了一半走开，回来不该还留着一个一碰就发的按钮。 */
let armed = null;
let armTimer = null;

function disarm() {
  if (armed) {
    armed.textContent = armed.dataset.label;
    armed.classList.remove("armed");
    armed = null;
  }
  clearTimeout(armTimer);
}

function arm(btn) {
  disarm();
  btn.dataset.label = btn.textContent;
  btn.textContent = btn.dataset.confirm;
  btn.classList.add("armed");
  armed = btn;
  armTimer = setTimeout(disarm, 6000);
}

function say(msg, bad) {
  const el = document.getElementById("actmsg");
  if (!el) return;
  el.textContent = msg;
  el.className = "actmsg" + (bad ? " bad" : " ok");
  el.hidden = false;
}

async function runAction(btn) {
  const kind = btn.dataset.act;
  const box = btn.closest("[data-approval],[data-plan]");
  const body = {};
  let path;

  if (kind === "approve") {
    path = `/console/api/approvals/${box.dataset.approval}/approve`;
    const at = document.getElementById("sched-at");
    if (at && at.value) body.scheduled_at = at.value;   // 本地时间，后端按本机时区解析
    body.window_hours = Number(document.getElementById("sched-win").value);
  } else if (kind === "reject") {
    path = `/console/api/approvals/${box.dataset.approval}/reject`;
  } else if (kind === "cancel") {
    path = `/console/api/plans/${box.dataset.plan}/cancel`;
  } else if (kind === "rerun") {
    path = `/console/api/tasks/${taskId}/rerun`;
    body.stage = btn.dataset.stage;
  } else if (kind === "published" || kind === "retry" || kind === "unpublish") {
    const pid = btn.closest("[data-pub]").dataset.pub;
    if (kind === "published") {
      path = `/console/api/publications/${pid}/mark-published`;
      const u = document.getElementById("pub-url");
      if (u && u.value.trim()) body.url = u.value.trim();
    } else {
      // 撤销「已发布」和清掉失败记录都是把记录复位，
      // 但前者改的是一条事实记录，要显式说明
      path = `/console/api/publications/${pid}/retry`;
      if (kind === "unpublish") body.force = true;
    }
  } else return;

  btn.disabled = true;
  try {
    const out = await post(path, body);
    const done = {
      approve: () => `已排期 ${when(out.scheduled_at)}，窗口到 ${when(out.allowed_until)}`,
      reject: () => "已打回，不会发布",
      cancel: () => `已撤销排期${out.cancelled_items ? "，并从队列里撤下发布" : ""}`,
      rerun: () => `已把「${btn.closest(".sub-row").querySelector(".hl").textContent}」放回队列，等 worker 领走`,
      published: () => out.url ? `已记为发布：${out.url}` : "已记为发布",
      retry: () => "失败记录已清掉，清单可以重新用了",
      unpublish: () => "已撤销「已发布」标记",
    }[kind]();
    cache.task[taskId] = null;
    cache.tasks = null;
    await render();
    say(done, false);
  } catch (err) {
    btn.disabled = false;
    say(err.message, true);
  }
}

/* 配音页的动作。加载不需要二次确认（慢但无害），
   卸载和删除需要——卸载常驻模型是真的把子进程杀掉。 */
async function runTtsAction(btn) {
  const kind = btn.dataset.tts;
  btn.disabled = true;
  try {
    let msg;
    if (kind === "load") {
      btn.textContent = "加载中…";
      const out = await post(`/tts/api/models/${btn.dataset.model}/load`);
      if (out.status === "error") throw new Error(out.error || "加载失败");
      msg = `${out.name} 已就绪${out.load_elapsed ? `，用时 ${out.load_elapsed}s` : ""}`;
    } else if (kind === "unload") {
      const out = await post(`/tts/api/models/${btn.dataset.model}/unload`);
      msg = `${out.name} 已卸载`;
    } else if (kind === "drop") {
      await del(`/tts/api/generations/${btn.dataset.gen}`);
      msg = "已删除这条记录和对应音频";
    } else return;
    cache.tts = null;
    await render();
    say(msg, false);
  } catch (err) {
    // 失败也要重画：加载失败后模型会变成 error 状态，卡片上要能看到原因
    cache.tts = null;
    await render();
    say(err.message, true);
  }
}

async function generateTts() {
  const btn = document.getElementById("tts-go");
  const text = document.getElementById("tts-text").value.trim();
  if (!text) return say("先写点要念的文本", true);

  const pick = (id) => { const el = document.getElementById(id); return el ? el.value : null; };
  const body = {
    model_id: document.getElementById("tts-pick").value,
    text,
    voice: pick("tts-voice") || null,
    ref_audio: pick("tts-ref") || null,
    emotion: pick("tts-emo") || null,
    instruct: pick("tts-instruct") || null,
    speed: Number(pick("tts-speed") || 1),
  };

  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const out = await post("/tts/api/generate", body);
    cache.tts = null;
    await render();
    say(`生成完成 · ${out.duration ? out.duration.toFixed(1) + "s" : "?"}`
        + `${out.rtf ? ` · RTF ${out.rtf}` : ""}`, false);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "生成";
    say(err.message, true);
  }
}

document.getElementById("main").addEventListener("click", (e) => {
  const cp = e.target.closest("[data-copy]");
  if (cp) { e.stopPropagation(); return copyText(cp.dataset.copy, cp); }

  const tts = e.target.closest("[data-tts]");
  if (tts) {
    if (!tts.dataset.confirm) return runTtsAction(tts);     // 加载：直接执行
    if (armed === tts) { disarm(); return runTtsAction(tts); }
    return arm(tts);
  }
  if (e.target.id === "tts-go") return generateTts();
  if (e.target.id === "tts-refresh") { cache.tts = null; return render(); }

  const action = e.target.closest("[data-act]");
  if (action) {
    if (armed === action) { disarm(); return runAction(action); }
    return arm(action);
  }
  disarm();

  const goto = e.target.closest("[data-goto]");
  if (goto && goto.dataset.goto) return go("task", goto.dataset.goto);

  const back = e.target.closest("[data-view]");
  if (back) return go(back.dataset.view);

  if (e.target.closest("a")) return;              // 让产物链接正常打开

  if (e.target.closest("[data-mark]")) return;   // 行里的标记下拉不跳转

  const row = e.target.closest("[data-task]");
  if (row) return go("task", row.dataset.task);

  const tf = e.target.closest("[data-tf]");
  if (tf) { taskFilter = tf.dataset.tf; return render(); }

  const plat = e.target.closest("[data-plat]");
  if (plat) { sigPlatform = plat.dataset.plat; return render(); }

  if (e.target.id === "recheck") {
    e.target.textContent = "体检中…";
    e.target.disabled = true;
    cache.tools = null;
    api("/console/api/tools?refresh=true")
      .then((d) => { cache.tools = d; render(); })
      .catch(() => render());
  }
});

/* 换模型就换一套参数：edge 给音色列表，indextts2 给参考音频和情感。
   把不适用的字段留在界面上，只会让人填了不起作用的东西。 */
document.getElementById("main").addEventListener("change", async (e) => {
  const mark = e.target.closest("[data-mark]");
  if (mark) {
    // 列表页的下拉带 id，详情页的不带（就是当前这条）
    const id = mark.dataset.mark || taskId;
    const label = mark.options[mark.selectedIndex].textContent;
    try {
      await post(`/console/api/tasks/${id}/state`, { state: mark.value });
      cache.tasks = null;
      cache.task[id] = null;
      await render();
      say(mark.value ? `已标记为「${label}」` : "已撤销标记", false);
    } catch (err) {
      cache.tasks = null;
      await render();
      say(err.message, true);
    }
    return;
  }

  if (e.target.id !== "tts-pick") return;
  const text = document.getElementById("tts-text").value;
  ttsModel = e.target.value;
  render().then(() => {
    const box = document.getElementById("tts-text");
    if (box) box.value = text;          // 重画表单不该把写好的文本冲掉
  });
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

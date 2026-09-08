let MODELS = [];

const $ = s => document.querySelector(s);
const api = (u, o) => fetch(u, o).then(async r => {
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d;
});

// ── Tab 切换 ──
document.querySelectorAll('.tab[data-tab]').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab[data-tab]').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('#tab-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'history') loadHistory();
    if (t.dataset.tab === 'models') renderModels();
  };
});

// ── 模型 ──
async function loadModels() {
  MODELS = (await api('/api/models')).models;
  const sel = $('#g-model'), flt = $('#h-filter');
  sel.innerHTML = MODELS.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
  flt.innerHTML = '<option value="">全部模型</option>' +
    MODELS.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
  renderModels();
  onModelChange();
}

function renderModels() {
  $('#model-list').innerHTML = MODELS.map(m => `
    <div class="card">
      <div class="card-hd">
        <div>
          <span class="card-name">${m.name}</span>
          <span class="tag">${m.kind}</span>
          ${m.resident ? '<span class="tag">常驻内存</span>' : ''}
        </div>
        <div>
          <span class="pill ${m.status}">${m.status}</span>
          ${m.status === 'ready'
            ? `<button class="mini danger" onclick="toggleModel('${m.id}','unload')">释放</button>`
            : `<button class="mini" onclick="toggleModel('${m.id}','load')">加载</button>`}
        </div>
      </div>
      <div style="font-size:12px;color:#777">${m.note}</div>
      <div class="meta">
        ${m.load_elapsed != null ? `<span>加载耗时 <b>${m.load_elapsed}s</b></span>` : ''}
        ${m.voices.length ? `<span>预置音色 <b>${m.voices.length}</b></span>` : ''}
        ${m.emotions.length ? `<span>情感维度 <b>${m.emotions.length}</b></span>` : ''}
        <span>参考克隆 <b>${m.supports_ref ? '支持' : '不支持'}</b></span>
        ${m.error ? `<span style="color:#c33">${m.error}</span>` : ''}
      </div>
    </div>`).join('');
}

async function toggleModel(id, act) {
  const btns = document.querySelectorAll('#model-list button');
  btns.forEach(b => b.disabled = true);
  const m = MODELS.find(x => x.id === id);
  if (m && act === 'load') { m.status = 'loading'; renderModels(); }
  try {
    const r = await api(`/api/models/${id}/${act}`, { method: 'POST' });
    Object.assign(MODELS.find(x => x.id === id), r);
  } catch (e) { alert(e.message); }
  renderModels(); onModelChange();
}

// ── 生成表单联动 ──
function onModelChange() {
  const m = MODELS.find(x => x.id === $('#g-model').value);
  if (!m) return;
  $('#g-model-state').textContent = m.status;
  $('#g-model-state').className = 'pill ' + m.status;
  $('#g-note').textContent = m.note;

  $('#row-voice').hidden = !m.supports_voice;
  $('#row-ref').hidden = !m.supports_ref;
  $('#row-emotion').hidden = !m.emotions.length;
  $('#row-alpha').hidden = !m.emotions.length;
  $('#row-instruct').hidden = m.id !== 'voxcpm2';

  if (m.supports_voice)
    $('#g-voice').innerHTML = m.voices.map(v => `<option>${v}</option>`).join('');
  $('#emo-chips').innerHTML = m.emotions
    .map(e => `<button class="chip" onclick="$('#g-emotion').value='${e}'">${e}</button>`).join('');
  if (m.supports_ref) loadRefs();
}
$('#g-model').onchange = onModelChange;
$('#g-alpha').oninput = e => $('#alpha-val').textContent = e.target.value;

// ── 参考音频 ──
async function loadRefs() {
  const { items } = await api('/api/refs');
  $('#g-ref').innerHTML = items.length
    ? items.map(r => `<option value="${r.name}">${r.name} (${(r.duration||0).toFixed(1)}s)</option>`).join('')
    : '<option value="">（无，请先上传）</option>';
  onRefChange();
}
$('#g-ref').onchange = onRefChange;
function onRefChange() {
  const v = $('#g-ref').value, p = $('#g-ref-play');
  p.hidden = !v; if (v) p.src = '/refs/' + v;
}
$('#g-upload').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  $('#g-status').textContent = '上传中…';
  try { await api('/api/upload-ref', { method: 'POST', body: fd }); await loadRefs();
        $('#g-ref').value = f.name; onRefChange(); $('#g-status').textContent = '已上传'; }
  catch (err) { $('#g-status').textContent = '上传失败: ' + err.message; }
};

// ── 生成 ──
$('#g-run').onclick = async () => {
  const m = MODELS.find(x => x.id === $('#g-model').value);
  const text = $('#g-text').value.trim();
  if (!text) return alert('请输入文本');
  if (m.status !== 'ready') return alert(`${m.name} 未就绪，请先到「模型」页加载`);

  const body = { model_id: m.id, text, speed: 1.0 };
  if (m.supports_voice) body.voice = $('#g-voice').value;
  if (m.supports_ref && $('#g-ref').value) body.ref_audio = $('#g-ref').value;
  if (m.emotions.length) {
    if ($('#g-emotion').value.trim()) body.emotion = $('#g-emotion').value.trim();
    body.emo_alpha = parseFloat($('#g-alpha').value);
  }
  if (m.id === 'voxcpm2' && $('#g-instruct').value.trim())
    body.instruct = $('#g-instruct').value.trim();

  $('#g-run').disabled = true;
  const t0 = Date.now();
  const tick = setInterval(() => {
    $('#g-status').textContent = `生成中… ${((Date.now()-t0)/1000).toFixed(0)}s`;
  }, 500);
  try {
    const r = await api('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#g-status').textContent = `完成 ${r.elapsed}s (RTF ${r.rtf})`;
    $('#g-result').innerHTML = renderGen(r, false);
    drawWaves();
  } catch (e) {
    $('#g-status').textContent = '失败: ' + e.message;
  } finally { clearInterval(tick); $('#g-run').disabled = false; }
};

// ── 历史 ──
async function loadHistory() {
  const f = $('#h-filter').value;
  const { items } = await api('/api/generations?limit=50' + (f ? '&model_id=' + f : ''));
  $('#history-list').innerHTML = items.length
    ? items.map(g => renderGen(g, true)).join('')
    : '<div class="empty">还没有生成记录</div>';
  drawWaves();
}
$('#h-filter').onchange = loadHistory;

function renderGen(g, del) {
  const name = (MODELS.find(m => m.id === g.model_id) || {}).name || g.model_id;
  if (g.status !== 'ok')
    return `<div class="card"><div class="card-hd"><span class="card-name">${name}</span>
      <span class="pill error">失败</span></div>
      <div class="gen-text">${esc(g.text)}</div>
      <div class="meta" style="color:#c33">${esc(g.error||'')}</div></div>`;

  const cfg = [];
  if (g.voice) cfg.push(g.voice);
  if (g.ref_audio) cfg.push('ref: ' + g.ref_audio);
  if (g.params?.emotion) cfg.push('emo: ' + g.params.emotion);
  if (g.params?.emo_alpha != null && g.params.emotion) cfg.push('α ' + g.params.emo_alpha);
  if (g.params?.instruct) cfg.push('「' + g.params.instruct + '」');

  return `<div class="card">
    <div class="card-hd">
      <div><span class="card-name">${name}</span>
        ${cfg.map(c => `<span class="tag">${esc(c)}</span>`).join('')}</div>
      <div>
        <span class="pill">RTF ${g.rtf ?? '-'}</span>
        ${del ? `<button class="mini danger" onclick="delGen('${g.id}')">删除</button>` : ''}
      </div>
    </div>
    <div class="gen-item">
      <div class="gen-info">
        <div class="gen-text">${esc(g.text)}</div>
        <div class="meta">
          <span>时长 <b>${(g.duration||0).toFixed(1)}s</b></span>
          <span>耗时 <b>${g.elapsed}s</b></span>
          <span>${(g.sample_rate/1000).toFixed(1)}kHz</span>
          <span>${((g.file_size||0)/1024).toFixed(0)}KB</span>
        </div>
      </div>
      <div class="gen-wave">
        <canvas class="wave" data-wave="${esc(JSON.stringify(g.waveform||[]))}"></canvas>
        <audio controls preload="none" src="${g.audio_url}"></audio>
      </div>
    </div></div>`;
}

async function delGen(id) {
  if (!confirm('删除这条记录和音频文件？')) return;
  await api('/api/generations/' + id, { method: 'DELETE' });
  loadHistory();
}

// ── 波形绘制 ──
function drawWaves() {
  document.querySelectorAll('canvas.wave').forEach(c => {
    let peaks; try { peaks = JSON.parse(c.dataset.wave); } catch { return; }
    if (!peaks || !peaks.length) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const x = c.getContext('2d'); x.scale(dpr, dpr);
    x.clearRect(0, 0, w, h);
    const bw = w / peaks.length, mid = h / 2;
    const grad = x.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, '#818cf8'); grad.addColorStop(.5, '#4f46e5'); grad.addColorStop(1, '#818cf8');
    x.fillStyle = grad;
    peaks.forEach((p, i) => {
      const bh = Math.max(1, p * (h - 6));
      x.fillRect(i * bw, mid - bh / 2, Math.max(0.6, bw - 0.4), bh);
    });
  });
}
window.addEventListener('resize', drawWaves);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

loadModels();

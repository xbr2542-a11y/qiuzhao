const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const FAMILY_LABELS = { general: '通用', soe: '央国企', data: '数据分析', trade: '外贸', semi: '半导体' };
const TIER_LABELS = {
  1: { text: '专业不限', cls: 'ok' },
  2: { text: '专业相关', cls: 'primary' },
  3: { text: '专业受限', cls: 'danger' },
  0: { text: '专业要求未标注', cls: '' },
};
const TASK_LABELS = {
  'collect.guopin': '国聘 · 找岗位',
  'collect.ncss': '24365 国家平台 · 找岗位',
  'collect.bigtech': '大厂校招官网 · 找岗位',
  'collect.wechat': '公众号 · 找岗位',
  'collect.workday': '外企官网 · 找岗位',
  'analyze.jobs': '重新计算匹配度',
  'demo.pipeline': '自检任务',
};
const TASK_STATUS_LABELS = {
  queued: '排队中', running: '进行中', paused: '已暂停', stopped: '已停止',
  done: '已完成', failed: '失败', interrupted: '中断',
};
const OVERSEAS_LABELS = {
  not_mentioned: '未提及', travel_only: '仅海外出差', short_term_assignment: '短期外派',
  long_term_assignment: '长期外派', mandatory_overseas_assignment: '强制境外驻派',
  overseas_local_position: '海外当地岗位', unclear: '描述不清，需复核',
};
const REVIEW_LABELS = {
  pending: '正常', need_review: '待人工复核', excluded: '已排除', kept: '已保留',
};
const JOB_TYPE_OPTIONS = ["采购/寻源", "供应链/物流", "B端解决方案销售", "工业品销售",
                          "海外销售", "外贸BD", "国际业务", "技术销售", "项目型销售", "商务拓展"];
// 岗位职能：和"销售类型"是两个维度——采购、供应链不是销售岗，但同样是目标方向
const FUNCTION_LABELS = {
  procurement: '采购/寻源', supply_chain: '供应链/物流', sales: '销售/商务',
  trade: '外贸/国际业务', data: '数据分析', other: '其他',
};
const OVERSEAS_PREF_LABELS = {
  unset: '未设置', no_long_term: '不接受长期境外外派', accept_short_term: '可接受短期外派',
  accept_travel: '可接受海外出差（不接受驻外）', accept_long_term: '可接受长期外派',
  ask: '逐岗位确认',
};

const state = { profile: {}, resumes: [], jobs: [], stats: {}, facets: {},
  tasks: [], taskTypes: [], families: {}, statuses: [], runtime: {}, visible: 60,
  allJobs: [], offline: false, cachedAt: '' };

// ---------------------------------------------------------------- 离线缓存
// 岗位数据缓存在浏览器本地：服务没启动时依然能浏览和筛选，只是不能采集。
const CACHE_KEY = 'qiuzhao_cache_v1';
const CACHE_FIELDS = ['id', 'company', 'title', 'city', 'family', 'degree_req', 'job_type',
  'source', 'url', 'deadline', 'salary', 'match_score', 'major_tier', 'filter_note',
  'industry', 'published_at', 'days_left', 'status', 'company_nature', 'experience_req',
  'job_function', 'sales_type', 'review_status', 'industry_subtrack', 'candidate_level',
  'overseas_assignment_status', 'salary_max', 'match_tags', 'risk_tags'];

function saveCache() {
  try {
    // 只缓存前 2500 条（列表本身按匹配度排过序）。
    // 全量 6000+ 条会把 localStorage 撑爆，浏览器直接抛 QuotaExceededError，
    // 离线缓存等于形同虚设。
    const trimmed = (state.allJobs || []).slice(0, 2500).map((job) => {
      const slim = {};
      CACHE_FIELDS.forEach((key) => { slim[key] = job[key]; });
      return slim;
    });
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      savedAt: new Date().toLocaleString(), jobs: trimmed,
      profile: state.profile, facets: state.facets, stats: state.stats,
    }));
  } catch (error) {
    // 配额还是不够就退到更小的缓存，至少保住离线能看岗位
    try {
      localStorage.removeItem(CACHE_KEY);
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        savedAt: new Date().toLocaleString(),
        jobs: (state.allJobs || []).slice(0, 600), profile: state.profile,
      }));
    } catch (inner) { /* 实在存不下就算了，不影响在线使用 */ }
  }
}

function loadCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data.jobs || !data.jobs.length) return false;
    state.allJobs = data.jobs;
    state.jobs = data.jobs;
    state.profile = data.profile || {};
    state.facets = data.facets || {};
    state.stats = data.stats || {};
    state.cachedAt = data.savedAt || '';
    return true;
  } catch (error) {
    return false;
  }
}

function showBanner(text, isError) {
  $('#offline-text').textContent = text;
  $('#offline-bar').classList.toggle('error', Boolean(isError));
  $('#offline-bar').hidden = false;
}

function hideBanner() { $('#offline-bar').hidden = true; }

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
const post = (path, body) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
});

function jobFilters() {
  const params = new URLSearchParams();
  const map = { q: $('#q'), degree: $('#f-degree'), function: $('#f-function'),
    job_type: $('#f-jobtype'), source: $('#f-source'), sort: $('#f-sort') };
  for (const [key, el] of Object.entries(map)) {
    if (!el) continue;
    let value = el.value.trim();
    // "推荐"排序是纯前端逻辑，后台不认识这个值，导出时退化成按匹配分
    if (key === 'sort' && value === 'smart') value = 'score';
    if (value) params.set(key, value);
  }
  // 城市：手输的优先，其次才是下拉选中的
  const city = $('#f-city-text').value.trim() || $('#f-city').value.trim();
  if (city) params.set('city', city);
  if ($('#f-open').checked) params.set('open_only', '1');
  if ($('#f-majorfree').checked) params.set('hide_major_block', '1');
  if ($('#f-myCity').checked) {
    const cities = (state.profile.prefer_cities || []).join(',');
    if (cities) params.set('city_in', cities);
  }
  return params;
}

/* 快捷方向：把最常用的几类岗位做成一排按钮，点一下就筛。
   以前这些散在 4 个下拉里（方向/赛道/销售类型/只看目标岗），
   互相叠加，用户点了没反应也不知道是哪一条生效了。 */
const QUICK_DIRECTIONS = [
  { key: '', label: '全部', test: () => true },
  { key: 'soe', label: '央国企', test: (j) => j.family === 'soe' },
  { key: 'supply', label: '采购 · 供应链',
    test: (j) => ['procurement', 'supply_chain'].includes(j.job_function) },
  { key: 'trade', label: '外贸 · 海外',
    test: (j) => j.family === 'trade' || String(j.sales_type || '').includes('外贸') },
  { key: 'sales', label: 'B端销售', test: (j) => j.job_function === 'sales' },
  { key: 'data', label: '数据分析', test: (j) => j.family === 'data' },
  { key: 'semi', label: '半导体', test: (j) => j.family === 'semi' },
];
state.quick = '';

function renderQuickChips() {
  const all = state.allJobs || [];
  const counts = {};
  QUICK_DIRECTIONS.forEach((d) => {
    if (!d.key) return;
    counts[d.key] = all.filter((job) => d.test(job) && job.review_status !== 'excluded').length;
  });
  $('#quick-chips').innerHTML = QUICK_DIRECTIONS.map((d) => {
    const n = d.key ? ` <em>${counts[d.key] || 0}</em>` : '';
    return `<button class="chip${state.quick === d.key ? ' on' : ''}" data-quick="${d.key}"
      >${esc(d.label)}${n}</button>`;
  }).join('') + '<span class="chip-hint">← 点一下只筛这一类岗位</span>';
}

/* ---------------------------------------------------------------- 渲染 */
function renderHero() {
  const s = state.stats || {};
  const good = (state.jobs || []).filter((j) => j.match_score >= 60).length;
  const open = (state.jobs || []).filter((j) => j.days_left === null || j.days_left >= 0).length;
  const bits = [
    `岗位池 <b>${s.total ?? 0}</b> 个`,
    `当前筛出 <b class="accent">${state.jobs.length}</b> 个`,
    `匹配度 60+ <b>${good}</b> 个`,
    `还能投 <b>${open}</b> 个`,
  ];
  $('#hero').innerHTML = bits.map((b) => `<span class="stat">${b}</span>`).join('');
}

/** 把当前生效的筛选项列成一排可点掉的标签。
    以前只在标题上写一行字，用户分不清"点了没反应"是条件叠加还是真没数据。 */
function activeFilters() {
  const out = [];
  const q = $('#q').value.trim();
  if (q) out.push({ text: `关键词：${q}`, clear: () => { $('#q').value = ''; } });
  const city = $('#f-city-text').value.trim() || $('#f-city').value;
  if (city) out.push({ text: `城市：${city}`,
    clear: () => { $('#f-city-text').value = ''; $('#f-city').value = ''; } });
  const dir = QUICK_DIRECTIONS.find((d) => d.key === state.quick);
  if (dir && dir.key) out.push({ text: `方向：${dir.label}`, clear: () => { state.quick = ''; } });
  if ($('#f-function').value) {
    const fn = $('#f-function').value;
    out.push({ text: `职能：${FUNCTION_LABELS[fn] || fn}`,
      clear: () => { $('#f-function').value = ''; } });
  }
  const simple = [
    ['#f-degree', '学历'], ['#f-jobtype', '类型'], ['#f-source', '来源'],
  ];
  simple.forEach(([sel, label]) => {
    const el = $(sel);
    if (el.value) out.push({ text: `${label}：${el.value}`, clear: () => { el.value = ''; } });
  });
  if ($('#f-track').value) {
    const option = $('#f-track').selectedOptions[0];
    if (option) out.push({ text: `赛道：${option.textContent.replace(/（\d+）$/, '')}`,
      clear: () => { $('#f-track').value = ''; } });
  }
  if ($('#f-sales').value) out.push({ text: `销售类型：${$('#f-sales').value}`,
    clear: () => { $('#f-sales').value = ''; } });
  if ($('#f-review').value) out.push({
    text: `复核：${REVIEW_LABELS[$('#f-review').value] || $('#f-review').value}`,
    clear: () => { $('#f-review').value = ''; } });
  if ($('#f-overseas').value) out.push({
    text: `外派：${OVERSEAS_LABELS[$('#f-overseas').value] || $('#f-overseas').value}`,
    clear: () => { $('#f-overseas').value = ''; } });
  [['#f-majorfree', '隐藏专业不符'], ['#f-open', '只看还能投的'],
   ['#f-myCity', '只看意向城市'], ['#f-noOverseas', '排除强制外派'],
   ['#f-aimAt', '只看对口岗位']].forEach(([sel, label]) => {
    const el = $(sel);
    if (el && el.checked) out.push({ text: label, clear: () => { el.checked = false; } });
  });
  return out;
}

function renderActiveFilters() {
  const list = activeFilters();
  const box = $('#active-filters');
  if (!list.length) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = list.map((item, index) =>
    `<button class="afilter" data-clear-filter="${index}">${esc(item.text)} ✕</button>`).join('')
    + '<span class="muted" style="font-size:12px">点标签可以单独取消这条条件</span>';
  box.querySelectorAll('[data-clear-filter]').forEach((el) => {
    el.addEventListener('click', () => {
      list[Number(el.dataset.clearFilter)].clear();
      applyFilters();
    });
  });
}

function deadlineText(job) {
  if (!job.deadline) return '<span>截止时间未标注</span>';
  const left = job.days_left;
  if (left === null || left === undefined) return `<span>截止 ${esc(job.deadline)}</span>`;
  if (left < 0) return `<span class="tag danger">已截止 ${esc(job.deadline)}</span>`;
  const cls = left <= 3 ? 'danger' : left <= 7 ? 'warn' : '';
  return `<span class="tag ${cls}">剩 ${left} 天 · ${esc(job.deadline)} 截止</span>`;
}

function scoreClass(score) {
  if (!score) return 'none';
  if (score >= 75) return 'high';
  if (score >= 50) return 'mid';
  if (score >= 30) return 'low';
  return 'none';
}

function renderJobs() {
  const list = state.jobs || [];
  $('#jobs-empty').hidden = list.length > 0;
  const shown = list.slice(0, state.visible);
  $('#more-wrap').hidden = list.length <= shown.length;
  $('#btn-more').textContent = `显示更多岗位（还有 ${list.length - shown.length} 条）`;
  $('#job-list').innerHTML = shown.map((job) => {
    const tier = TIER_LABELS[job.major_tier] || TIER_LABELS[0];
    const tags = [
      job.job_function && FUNCTION_LABELS[job.job_function]
        ? `<span class="tag ok">${esc(FUNCTION_LABELS[job.job_function])}</span>` : '',
      job.industry_subtrack ? `<span class="tag primary" title="${esc(job.classify_evidence || '')}">${esc(job.industry_subtrack)}</span>` : '',
      job.city ? `<span class="tag">${esc(job.city)}</span>` : '',
      job.degree_req ? `<span class="tag">${esc(job.degree_req)}</span>` : '',
      job.job_type ? `<span class="tag">${esc(job.job_type)}</span>` : '',
      `<span class="tag ${tier.cls}" title="${esc(job.filter_note || '')}">${tier.text}</span>`,
      job.family === 'soe' ? '<span class="tag primary">央国企</span>' : '',
      ['mandatory_overseas_assignment', 'long_term_assignment'].includes(job.overseas_assignment_status)
        ? `<span class="tag danger" title="${esc(job.overseas_assignment_evidence || '')}">${
          job.overseas_assignment_status === 'mandatory_overseas_assignment' ? '强制驻外' : '长期外派'}</span>`
        : (job.overseas_assignment_status === 'travel_only'
          ? '<span class="tag warn">海外出差</span>' : ''),
    ].filter(Boolean);
    // 标签最多显示 4 个：以前一张卡片能挂 8~9 个标签，扫一眼根本看不出重点
    const extra = tags.length > 4 ? `<span class="tag">+${tags.length - 4}</span>` : '';
    const tagHtml = tags.slice(0, 4).join('') + extra;
    const initial = (job.company || '?').trim().charAt(0);
    return `<article class="job-card" data-id="${job.id}">
      <div class="job-main">
        <div class="job-head">
          <h3 class="job-title">${esc(job.title)}</h3>
          ${job.salary ? `<span class="salary">${esc(job.salary)}</span>` : ''}
        </div>
        <div class="job-company">
          <span class="logo">${esc(initial)}</span>
          <span>${esc(job.company)}</span>
        </div>
        <div class="job-tags">${tagHtml}</div>
        <div class="job-foot">
          ${deadlineText(job)}
          <span>来源 ${esc(job.source)}</span>
          ${job.published_at ? `<span>发布 ${esc(job.published_at.slice(0, 10))}</span>` : ''}
        </div>
      </div>
      <div class="job-side">
        <div class="score ${scoreClass(job.match_score)}">${job.match_score || 0}<small>匹配度</small></div>
        <button class="btn tiny" data-detail="${job.id}">详情</button>
        ${job.url ? `<button class="btn tiny primary" data-open="${esc(job.url)}">去官网投递</button>` : ''}
      </div>
    </article>`;
  }).join('');
}

function renderResumes() {
  $('#resume-list').innerHTML = (state.resumes || []).map((item) => {
    const p = item.parsed || {};
    const rows = [
      ['方向', (item.families || []).map((f) => FAMILY_LABELS[f] || f).join('、') || '未指定'],
      ['文件', item.filename],
      ['姓名', p.full_name], ['学校', p.school], ['专业', p.major], ['学历', p.degree],
      ['手机', p.phone], ['邮箱', p.email],
      ['技能', (p.skills || []).join('、')],
      ['上传', item.uploaded_at],
    ].filter(([, v]) => v);
    const tracks = state.tracksDef || [];
    const intent = `<div class="intent-box">
      <div class="intent-title">求职意向（决定匹配结果，可随时改）</div>
      <div class="muted">目标行业（多选）</div>
      <div class="checks">${tracks.map((t) => `<label><input type="checkbox" data-intent-track="${item.id}"
        value="${esc(t.code)}" ${(item.target_industries||[]).includes(t.code) ? 'checked' : ''}/> ${esc(t.name)}</label>`).join('')}</div>
      <div class="muted" style="margin-top:10px">目标岗位方向（多选）</div>
      <div class="checks">${JOB_TYPE_OPTIONS.map((t) => `<label><input type="checkbox" data-intent-job="${item.id}"
        value="${esc(t)}" ${(item.target_job_types||[]).includes(t) ? 'checked' : ''}/> ${esc(t)}</label>`).join('')}</div>
      <div class="grid" style="margin-top:12px">
        <label>目标城市（逗号分隔）<input id="intent-cities-${item.id}" value="${esc((item.target_cities||[]).join('，'))}" /></label>
        <label>外派偏好<select id="intent-overseas-${item.id}">
          ${Object.entries(OVERSEAS_PREF_LABELS).map(([v, l]) =>
            `<option value="${v}" ${item.overseas_preference === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>
      </div>
      <button class="btn tiny primary" data-save-intent="${item.id}" style="margin-top:10px">保存求职意向</button>
    </div>`;
    return `<div class="card-item">
      <h3>${esc(item.name)} ${item.is_default ? '<span class="tag ok">默认</span>' : ''}</h3>
      <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      ${intent}
      <div class="row" style="margin-top:12px">
        <button class="btn tiny" data-default-resume="${item.id}">设为默认</button>
        <button class="btn tiny ghost" data-delete-resume="${item.id}">删除</button>
      </div>
    </div>`;
  }).join('') || '<div class="empty"><p>还没有上传简历。</p></div>';
}

function renderProfile() {
  const p = state.profile || {};
  ['full_name', 'school', 'major', 'degree', 'grad_date', 'political', 'phone', 'email']
    .forEach((key) => { const el = $('#p-' + key); if (el) el.value = p[key] || ''; });
  $('#p-prefer_cities').value = (p.prefer_cities || []).join('，');
  $('#p-veteran').checked = Boolean(p.veteran);
}

function renderApps() {
  const apps = state.applications || [];
  if (!apps.length) {
    $('#apps-body').innerHTML = '<div class="empty"><div class="empty-ico">📭</div>'
      + '<h3>还没有投递记录</h3><p>在岗位详情里点「标记已投递」并填写依据，这里会记录完整时间线。</p>'
      + '<p class="muted">注意：只有拿到平台返回的申请编号或确认邮件，才能标记为「已投递」。</p></div>';
    return;
  }
  const labels = state.stageLabels || {};
  $('#apps-body').innerHTML = apps.map((a) => `
    <div class="card-item" style="margin-bottom:12px">
      <h3>${esc(a.title)} <span class="tag primary">${esc(a.stage_label || a.application_status)}</span></h3>
      <div class="job-company"><span class="logo">${esc((a.company || '?').charAt(0))}</span>
        <span>${esc(a.company)} · ${esc(a.city || '')} · ${esc(a.resume_name || '未指定简历')}</span></div>
      <dl>
        <dt>状态来源</dt><dd>${esc(a.status_source || 'unknown')}（置信度：${esc(a.status_confidence || 'unconfirmed')}）</dd>
        <dt>依据</dt><dd>${esc(a.evidence || '—')}</dd>
        <dt>投递时间</dt><dd>${esc(a.submitted_at || '未确认投递时间')}</dd>
        <dt>最近检查</dt><dd>${esc(a.last_checked_at || '—')}</dd>
      </dl>
      <div class="row" style="margin-top:10px">
        <select class="stage-select" data-app-id="${a.id}" data-job-id="${a.job_id}">
          ${Object.entries(labels).map(([v, l]) =>
            `<option value="${v}" ${a.application_status === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}
        </select>
        ${a.url ? `<button class="btn tiny" data-open="${esc(a.url)}">打开岗位页</button>` : ''}
      </div>
    </div>`).join('');
}

function renderTasks() {
  const tasks = state.tasks || [];
  if (!tasks.length) {
    $('#task-list').innerHTML = '<div class="empty"><p>还没有运行过任务。</p></div>';
    return;
  }
  $('#task-list').innerHTML = tasks.map((task) => {
    const total = task.total || 0;
    const percent = total ? Math.round((task.progress / total) * 100)
      : (task.status === 'done' ? 100 : 0);
    const barClass = ['done', 'failed', 'paused'].includes(task.status) ? task.status : '';
    const actions = [];
    if (['queued', 'running'].includes(task.status)) actions.push(['pause', '暂停']);
    if (task.status === 'paused') actions.push(['resume', '继续']);
    if (['queued', 'running', 'paused'].includes(task.status)) actions.push(['stop', '停止']);
    if (['failed', 'stopped', 'interrupted'].includes(task.status)) actions.push(['retry', '重跑']);
    actions.push(['logs', '日志']);
    return `<div class="task-row">
      <div class="info">
        <div class="name">${esc(TASK_LABELS[task.type] || task.type)}</div>
        <div class="meta">${esc(task.message || '')} ${task.started_at ? '· 开始 ' + esc(task.started_at.slice(5, 16)) : ''}</div>
        <div class="bar ${barClass}"><i style="width:${percent}%"></i></div>
      </div>
      <span class="pill ${task.status}">${TASK_STATUS_LABELS[task.status] || task.status}</span>
      <div class="row">${actions.map(([action, label]) =>
        `<button class="btn tiny${action === 'logs' ? ' ghost' : ''}" data-task="${action}" data-id="${task.id}">${label}</button>`).join('')}</div>
    </div>`;
  }).join('');
}

function renderFilterOptions() {
  const keep = (sel, entries, placeholder, key) => {
    const el = $(sel); const current = el.value;
    el.innerHTML = `<option value="">${placeholder}</option>` +
      entries.map((item) => `<option value="${esc(item[0])}">${esc(item[1])}</option>`).join('');
    el.value = current;
    void key;
  };
  // 岗位职能（采购/供应链/销售/外贸…）：按数量从多到少排
  const fnRows = (state.facets?.functions || [])
    .filter((f) => FUNCTION_LABELS[f.job_function])
    .sort((a, b) => (FUNCTION_LABELS[a.job_function] === '其他' ? 1 : 0)
      - (FUNCTION_LABELS[b.job_function] === '其他' ? 1 : 0) || b.n - a.n);
  keep('#f-function', fnRows.map((f) => [f.job_function, `${FUNCTION_LABELS[f.job_function]}（${f.n}）`]),
    '全部职能');
  const counts = state.facets?.city_counts || {};
  keep('#f-city', (state.facets?.cities || []).map((c) =>
    [c, counts[c] ? `${c}（${counts[c]}）` : c]), '全部城市');
  keep('#f-degree', (state.facets?.degrees || []).map((d) => [d, d]), '学历不限');
  keep('#f-jobtype', (state.facets?.job_types || []).map((t) => [t, t]), '类型不限');
  keep('#f-source', (state.facets?.sources || []).map((s) => [s, s]), '全部来源');
  keep('#f-track', (state.facets?.tracks || []).map((t) =>
    [t.code, `${t.name || t.code}（${t.n}）`]), '全部赛道');
  keep('#f-sales', (state.facets?.sales_types || []).map((s) =>
    [s.sales_type, `${s.sales_type}（${s.n}）`]), '全部销售类型');
}

function renderSystemInfo() {
  const box = $('#sys-info');
  if (!box) return;
  const rt = state.runtime || {};
  const src = (state.sources || []);
  const rows = [
    ['数据存放位置', `本机 ${rt.project_root || ''}\\data\\qiuzhao.db`],
    ['岗位来源', src.map((s) => `${s.name}（${(state.facets?.sources || []).includes(s.name) ? '已采集' : '未采集'}）`).join('、') || '—'],
    ['运行环境', [rt.node ? 'Node 已就绪' : 'Node 缺失', rt.chrome ? 'Chrome 已就绪' : 'Chrome 缺失'].join(' · ')],
  ];
  box.innerHTML = `<dl class="kv">${rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
}

function renderFoot() {
  const p = state.profile || {};
  const bits = [];
  if (p.major) bits.push(`专业：${p.major}`);
  if (p.degree) bits.push(p.degree);
  if (p.veteran) bits.push('退役士兵');
  $('#foot-line').textContent = bits.join(' · ') || '档案还没填完整';
}

function renderAll() {
  renderHero(); renderQuickChips(); renderJobs(); renderResumes(); renderProfile();
  renderApps(); renderTasks(); renderFilterOptions(); renderSystemInfo();
  renderActiveFilters(); renderFoot();
}

/* ---------------------------------------------------------------- 数据加载 */
/**
 * 本地筛选：全部在浏览器里算，不发任何请求。
 * 这样即使没网、甚至后台服务没启动，只要页面开着就能筛。
 */
function applyFilters() {
  const all = state.allJobs || [];
  const q = $('#q').value.trim().toLowerCase();
  const city = ($('#f-city-text').value.trim() || $('#f-city').value || '').trim();
  const degree = $('#f-degree').value;
  const jobType = $('#f-jobtype').value;
  const source = $('#f-source').value;
  const hideBlocked = $('#f-majorfree').checked;
  const openOnly = $('#f-open').checked;
  const myCityOnly = $('#f-myCity').checked;
  const track = $('#f-track').value;
  const salesType = $('#f-sales').value;
  const review = $('#f-review').value;
  const overseas = $('#f-overseas').value;
  const jobFunction = $('#f-function').value;
  const aimOnly = $('#f-aimAt').checked;
  const noOverseas = $('#f-noOverseas').checked;
  const sort = $('#f-sort').value || 'score';
  const mine = state.profile.prefer_cities || [];
  const today = new Date().toISOString().slice(0, 10);
  const quick = QUICK_DIRECTIONS.find((d) => d.key === state.quick) || QUICK_DIRECTIONS[0];

  const filtered = all.filter((job) => {
    if (q) {
      const blob = `${job.title || ''}${job.company || ''}${job.jd_text || ''}`.toLowerCase();
      if (!blob.includes(q)) return false;
    }
    if (city && !(job.city || '').includes(city)) return false;
    if (myCityOnly && mine.length && !mine.some((name) => (job.city || '').includes(name))) return false;
    if (degree && !(job.degree_req || '').includes(degree)) return false;
    if (jobType && job.job_type !== jobType) return false;
    if (source && !(job.source || '').includes(source)) return false;
    if (hideBlocked && job.major_tier === 3) return false;
    if (openOnly && job.deadline && job.deadline < today) return false;
    // ---- 快捷方向（一排按钮）：一次只生效一个 ----
    if (quick.key && !quick.test(job)) return false;
    // ---- 赛道 / 销售类型 / 外派 / 复核状态 ----
    if (track && job.industry_track !== track) return false;
    if (salesType && !(job.sales_type || '').includes(salesType)) return false;
    if (jobFunction && job.job_function !== jobFunction) return false;
    if (review && job.review_status !== review) return false;
    if (overseas && job.overseas_assignment_status !== overseas) return false;
    if (aimOnly) {
      // 「对口」= 销售方向命中，或采购/供应链这类职能方向命中
      const st = job.sales_type || '';
      const isSalesAim = st.includes('解决方案型') || st.includes('外贸BD') || st.includes('采购')
        || job.job_function === 'procurement' || job.job_function === 'supply_chain'
        || job.job_function === 'sales' || job.job_function === 'trade';
      if (!isSalesAim) return false;
      if (job.review_status === 'excluded') return false;
    }
    if (noOverseas && ['mandatory_overseas_assignment', 'long_term_assignment']
        .includes(job.overseas_assignment_status)) return false;
    return true;
  });

  const sorters = {
    // 推荐：先把"对口 + 已确认"的岗位顶到前面。
    // 岗位池为了做大而收录了很多行政/客服类岗位，如果只按匹配分数排，
    // 首屏全是和求职方向无关的岗位，用户会直接觉得"这工具没用"。
    smart: (a, b) => {
      const rank = (job) => {
        let value = 0;
        if (job.review_status === 'pending') value -= 40;
        if (job.review_status === 'excluded') value += 60;
        if (['procurement', 'supply_chain', 'sales', 'trade', 'data'].includes(job.job_function)) {
          value -= 25;
        }
        return value;
      };
      return (rank(a) - rank(b)) || ((b.match_score || 0) - (a.match_score || 0));
    },
    score: (a, b) => (b.match_score || 0) - (a.match_score || 0),
    deadline: (a, b) => (a.deadline || '9999-99-99').localeCompare(b.deadline || '9999-99-99'),
    published: (a, b) => (b.published_at || '').localeCompare(a.published_at || ''),
    salary: (a, b) => (b.salary_max || 0) - (a.salary_max || 0),
  };
  state.jobs = filtered.slice().sort(sorters[sort] || sorters.smart);
  state.visible = 60;
  renderHero(); renderActiveFilters(); renderJobs(); renderApps();
}

/** 从本地服务拉最新数据；失败就退回离线缓存并明确提示，绝不静默失败。 */
async function refreshFromServer() {
  let data;
  try {
    data = await api('/api/state');
  } catch (error) {
    // 这一支才是"连不上后台服务"
    const hasCache = loadCache();
    state.offline = true;
    if (hasCache) {
      showBanner(`离线浏览中（数据缓存于 ${state.cachedAt}）。要采集新岗位，请双击桌面「秋招管家」启动服务，再点右侧重试。`, false);
      renderAll();
      applyFilters();
    } else {
      showBanner('本地服务未启动：请双击桌面「秋招管家」图标，等出现黑色命令行窗口后点右侧重试。', true);
    }
    return false;
  }
  Object.assign(state, {
    profile: data.profile, resumes: data.resumes, stats: data.stats,
    facets: data.facets, tasks: data.tasks, taskTypes: data.task_types,
    families: data.families, statuses: data.statuses, runtime: data.runtime,
    applications: data.applications || [], stageLabels: data.stage_labels || {},
    tracksDef: data.tracks_def || [], reminders: data.reminders || [],
    allJobs: data.jobs || [], offline: false,
  });
  hideBanner();
  saveCache();
  // 数据拿到了，渲染出错是另一类问题，不能误报成"服务没启动"
  try {
    renderAll();
    applyFilters();
  } catch (error) {
    showBanner(`页面渲染出错：${error.message}（数据已获取，问题在前端）。`, true);
  }
  return true;
}

async function refreshTasks() {
  try {
    const data = await api('/api/tasks?limit=30');
    state.tasks = data.tasks || [];
    renderTasks();
  } catch (error) { /* 轮询失败静默 */ }
}

/* ---------------------------------------------------------------- 交互 */
function switchView(view) {
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  $$('.view').forEach((section) => section.classList.toggle('active', section.id === 'view-' + view));
}

async function runCollect(reset, sourceArg) {
  const chosen = sourceArg || $('#collect-source').value || 'guopin';
  const sources = chosen === 'all' ? ['guopin', 'ncss', 'bigtech', 'wechat', 'workday'] : [chosen];
  const button = $('#btn-collect');
  button.disabled = true;
  const original = button.textContent;
  button.textContent = '正在搜索岗位…';
  try {
    const limits = { guopin: 120, ncss: 60, bigtech: 40, wechat: 20, workday: 15 };
    const created = [];
    for (const [index, source] of sources.entries()) {
        const data = await post('/api/collect/run', {
          source, limit: limits[source] || 30,
          // 点「开始找岗位」只做增量补充，绝不清空已有岗位（清空必须显式操作）
          reset: false,
        });
      created.push(data.task.id);
    }
    switchView('system');
    await refreshTasks();
    const timer = setInterval(async () => {
      await refreshTasks();
      const mine = (state.tasks || []).filter((t) => created.includes(t.id));
      const finished = mine.filter((t) => ['done', 'failed', 'stopped', 'interrupted'].includes(t.status));
      const running = mine.find((t) => ['queued', 'running', 'paused'].includes(t.status));
      if (mine.length && finished.length === mine.length) {
        clearInterval(timer);
        button.disabled = false;
        button.textContent = original;
        await refreshFromServer();
        switchView('jobs');
        const collected = mine.reduce((sum, t) => sum + (t.result?.collected || 0), 0);
        const inserted = mine.reduce((sum, t) => sum + (t.result?.inserted || 0), 0);
        $('#foot-line').textContent = `本次抓取 ${collected} 条，新增 ${inserted} 条`;
      } else if (running) {
        button.textContent = `搜索中（${running.progress}/${running.total || '?'}）`;
      }
    }, 2500);
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    alert('启动失败：' + error.message);
  }
}

async function showTaskLogs(taskId) {
  const data = await api('/api/tasks/' + taskId);
  $('#log-panel').hidden = false;
  $('#log-title').textContent = `运行日志 · 任务 #${taskId}`;
  $('#task-log').innerHTML = (data.logs || []).map((row) => {
    const cls = row.level === 'error' ? 'log-error' : row.level === 'warn' ? 'log-warn' : '';
    return `<span class="${cls}">[${esc((row.ts || '').slice(11))}] ${esc(row.message)}</span>`;
  }).join('\n') || '暂无日志';
}

function openDrawer(html) { $('#drawer-body').innerHTML = html; $('#drawer').classList.add('open'); }

  async function showJobDetail(id) {
    let job = (state.jobs || []).find((j) => String(j.id) === String(id));
    // 列表接口不返回 JD 全文，打开详情时再单独取一次
    try {
      const detail = await api('/api/jobs/' + id);
      if (detail && detail.job) job = { ...job, ...detail.job };
    } catch (error) { /* 拉不到就用列表里的数据 */ }
    if (!job) return;
  const rows = [
    ['公司', job.company], ['岗位', job.title], ['城市', job.city], ['薪资', job.salary],
    ['岗位职能', FUNCTION_LABELS[job.job_function] || ''],
    ['行业赛道', job.industry_subtrack], ['销售类型', job.sales_type],
    ['应届分级', job.candidate_level],
    ['外派情况', `${OVERSEAS_LABELS[job.overseas_assignment_status] || job.overseas_assignment_status || '未提及'}`],
    ['外派证据', job.overseas_assignment_evidence],
    ['匹配标签', (job.match_tags || []).join('、')],
    ['风险标签', (job.risk_tags || []).join('、')],
    ['识别依据', job.classify_evidence],
    ['复核状态', REVIEW_LABELS[job.review_status] || job.review_status],
    ['学历要求', job.degree_req], ['经验要求', job.experience_req], ['岗位类型', job.job_type],
    ['专业要求', job.major_req], ['筛选判断', job.filter_note], ['行业', job.industry],
    ['截止时间', job.deadline], ['发布时间', job.published_at], ['来源', job.source],
    ['投递入口', job.url],
  ].filter(([, v]) => v);
  openDrawer(`
    <h2 style="margin:0 0 6px;font-size:19px">${esc(job.title)}</h2>
    <div class="muted">${esc(job.company)}</div>
    <div id="match-block"><p class="muted">正在计算简历匹配…</p></div>
    <div class="row" style="margin:14px 0">
      <div class="score ${scoreClass(job.match_score)}" style="width:68px;height:68px">${job.match_score || 0}<small>匹配度</small></div>
      <div>
        ${job.url ? `<button class="btn primary" data-open="${esc(job.url)}">去官网投递</button>` : ''}
        <button class="btn" data-applied="${job.id}">我投过了，记一笔</button>
        <button class="btn" data-review="${job.id}" data-value="kept">保留</button>
        <button class="btn" data-review="${job.id}" data-value="excluded">排除</button>
        <button class="btn" data-review="${job.id}" data-value="need_review">待确认</button>
      </div>
    </div>
    <dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    ${job.jd_text ? `<h3 style="font-size:14px;margin:18px 0 8px">岗位描述</h3>
      <div class="jd">${esc(job.jd_text)}</div>` : ''}
  `);
  // 异步补上简历匹配结果（推荐哪份简历、命中什么、缺什么、有什么风险）
  api('/api/jobs/' + id + '/match').then((m) => {
    const block = document.getElementById('match-block');
    if (!block) return;
    const best = m.recommended;
    if (!best) { block.innerHTML = '<p class="muted">还没有上传简历，无法匹配。</p>'; return; }
    const levelText = { high: '高', medium: '中', low: '低', unknown: '信息不足' }[best.match_level] || best.match_level;
    const levelCls = best.match_level === 'high' ? 'ok' : best.match_level === 'low' ? 'danger' : 'warn';
    block.innerHTML = `<div class="match-box">
      <div class="match-head">
        <span class="tag primary">推荐简历：${esc(best.resume_name)}</span>
        <span class="tag ${levelCls}">匹配度 ${best.match_score}（${levelText}）</span>
      </div>
      ${best.matched_requirements.length ? `<div class="muted mt">命中</div><ul class="reasons">${best.matched_requirements.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${best.missing_requirements.length ? `<div class="muted mt">缺失/未体现</div><ul class="reasons">${best.missing_requirements.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${best.risk_tags.length ? `<div class="muted mt">风险</div><ul class="reasons">${best.risk_tags.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${(best.unknown_dimensions || []).length ? `<div class="muted mt">信息不足的维度：${esc(best.unknown_dimensions.join('、'))}</div>` : ''}
      ${m.matches.length > 1 ? `<div class="muted mt">其他简历：${m.matches.slice(1).map((x) => `${esc(x.resume_name)} ${x.match_score}`).join('、')}</div>` : ''}
    </div>`;
  }).catch((error) => {
    const block = document.getElementById('match-block');
    if (block) block.innerHTML = `<p class="muted">匹配失败：${esc(error.message)}</p>`;
  });
}

/* ---------------------------------------------------------------- 事件 */
document.addEventListener('click', async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const dataset = target.dataset;
  try {
    if (dataset.open) {
      // 直接在浏览器开新标签页，比让后台服务调系统默认程序打开可靠
      const opened = window.open(dataset.open, '_blank', 'noopener');
      if (!opened) {
        const result = await post('/api/open', { url: dataset.open });
        if (!result.ok) {
          alert('打不开这个链接：' + (result.error || '未知原因') + '\n\n地址：\n' + dataset.open);
        }
      }
    }
    if (dataset.detail) await showJobDetail(dataset.detail);
    if (dataset.quick !== undefined) { state.quick = dataset.quick; applyFilters(); }
    if (dataset.applied) {
      const id = dataset.applied;
      const job = (state.allJobs || []).find((item) => String(item.id) === String(id)) || {};
      // 「有没有真的投出去」必须由证据决定，不能让点一下按钮就变成已投递。
      const evidence = prompt(
        `记录投递：${job.title || ''} · ${job.company || ''}\n\n`
        + '请填写投递依据（平台申请编号、投递成功页面提示、确认邮件时间等）。\n'
        + '留空则记为「未确认」，不会算作已投递。', '');
      if (evidence === null) return;
      await post('/api/applications', {
        job_id: Number(id), status: 'submitted', source: evidence ? 'user_input' : 'unknown',
        confidence: evidence ? 'confirmed' : 'unconfirmed', evidence: evidence || '',
        note: '',
      });
      const target = (state.allJobs || []).find((item) => String(item.id) === String(id));
      if (target) target.status = evidence ? '已投递' : '待投';
      $('#drawer').classList.remove('open');
      await refreshFromServer();
    }
  if (dataset.review) {
    await post('/api/jobs/update', { id: dataset.review, review_status: dataset.value });
    const job = (state.allJobs || []).find((item) => String(item.id) === String(dataset.review));
    if (job) job.review_status = dataset.value;
    $('#drawer').classList.remove('open');
    applyFilters();
  }
  if (dataset.saveIntent) {
    const id = dataset.saveIntent;
    const industries = $$(`input[data-intent-track="${id}"]:checked`).map((el) => el.value);
    const jobTypes = $$(`input[data-intent-job="${id}"]:checked`).map((el) => el.value);
    const cities = ($('#intent-cities-' + id).value || '').split(/[，,、\s]+/).filter(Boolean);
    const overseas = $('#intent-overseas-' + id).value;
    await post(`/api/resumes/${id}/intent`, {
      target_industries: industries, target_job_types: jobTypes,
      target_cities: cities, overseas_preference: overseas,
    });
    showBanner('求职意向已保存，匹配结果会按新意向重新计算。', false);
    await refreshFromServer();
  }
  if (dataset.task) {
    const action = dataset.task;
    if (action === 'logs') await showTaskLogs(dataset.id);
    else { await post(`/api/tasks/${dataset.id}/${action}`, {}); await refreshTasks(); }
  }
  if (dataset.defaultResume) {
    const resume = state.resumes.find((r) => String(r.id) === dataset.defaultResume);
    await post('/api/resume/update', { id: resume.id, name: resume.name,
      families: resume.families, is_default: true });
    await refreshFromServer();
  }
  if (dataset.deleteResume) {
    if (confirm('删除这份简历？文件也会从本地删除。')) {
      await post('/api/resume/delete', { id: dataset.deleteResume });
      await refreshFromServer();
    }
  }
  } catch (error) {
    // 绝不静默失败：出错要让用户看见原因，而不是"点了没反应"
    showBanner(`操作失败：${error.message || error}。若是连接错误，请双击桌面「秋招管家」启动本地服务后重试。`, true);
  }
});

document.addEventListener('change', async (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.classList.contains('stage-select')) {
    const stageLabels = state.stageLabels || {};
    const label = stageLabels[target.value] || target.value;
    const evidence = prompt(`把状态改成「${label}」。\n\n请填写依据（例如：平台申请编号、收到面试邮件的时间）。\n没有依据的状态会记为「未确认」。`, '');
    const confidence = evidence ? 'confirmed' : 'unconfirmed';
    try {
      await post('/api/applications', {
        job_id: Number(target.dataset.jobId), status: target.value,
        source: evidence ? 'user_input' : 'unknown', confidence, evidence: evidence || '',
        note: '',
      });
      await refreshFromServer();
    } catch (error) {
      showBanner('更新投递状态失败：' + (error.message || error), true);
      await refreshFromServer();
    }
    return;
  }
});

$$('.nav-item').forEach((item) => item.addEventListener('click', () => switchView(item.dataset.view)));
// 筛选全部在本地算，点一下立刻出结果，不请求后台。
// 注意：文本框只监听 change 的话，必须失焦才生效（用户会以为"没反应"），
// 所以文本框同时监听 input（边打字边筛，做了防抖）。
let filterTimer = null;
const debouncedFilter = () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(applyFilters, 250);
};
// 城市有两个控件（下拉 + 输入框）。它们必须互斥：
// 否则在下拉里选了城市，程序却还在用输入框里的旧值，看起来就是"筛选没反应"。
$('#f-city').addEventListener('change', () => {
  if ($('#f-city').value) $('#f-city-text').value = '';
  applyFilters();
});
$('#f-city-text').addEventListener('input', () => {
  if ($('#f-city-text').value.trim()) $('#f-city').value = '';
  debouncedFilter();
});
$$('#filterbar select, #filterbar2 select').forEach((el) => {
  if (el.id !== 'f-city') el.addEventListener('change', applyFilters);
});
$$('#filterbar input[type=checkbox], #filterbar2 input[type=checkbox]')
  .forEach((el) => el.addEventListener('change', applyFilters));
$$('#filterbar input[type=text], #filterbar2 input[type=text], #filterbar input:not([type])')
  .forEach((el) => {
  if (el.id === 'f-city-text') return;  // 上面已单独绑定
  el.addEventListener('input', debouncedFilter);
  el.addEventListener('change', applyFilters);
});
let searchTimer = null;
$('#q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applyFilters, 200);
});
$('#btn-clear').addEventListener('click', () => {
  $$('#filterbar select, #filterbar2 select').forEach((el) => { el.value = ''; });
  $('#f-sort').value = 'smart';
  $$('#filterbar input[type=checkbox], #filterbar2 input[type=checkbox]')
    .forEach((el) => { el.checked = false; });
  $$('#filterbar input[type=text], #filterbar2 input[type=text], #filterbar input:not([type])')
    .forEach((el) => { el.value = ''; });
  $('#f-city-text').value = '';
  $('#q').value = '';
  state.quick = '';
  applyFilters();
});
// 「更多筛选」默认收起：一屏能看到 10 多个下拉是"看不懂"的主要来源
$('#btn-more-filter').addEventListener('click', () => {
  const panel = $('#filterbar2');
  panel.hidden = !panel.hidden;
  $('#btn-more-filter').textContent = panel.hidden ? '更多筛选 ▾' : '收起筛选 ▴';
});
$('#btn-more').addEventListener('click', () => {
  state.visible += 60;
  renderJobs();
});
$('#btn-collect').addEventListener('click', () => runCollect(true, null));
$('#btn-collect-2').addEventListener('click', () => runCollect(true, null));
$('#btn-export').addEventListener('click', () => {
  window.location.href = '/api/export.xlsx?' + jobFilters().toString();
});
$('#btn-upload').addEventListener('click', async () => {
  const file = $('#r-file').files[0];
  const msg = $('#r-msg');
  if (!file) { msg.className = 'msg err'; msg.textContent = '请先选择文件'; return; }
  const families = $$('#r-families input:checked').map((el) => el.value);
  const base64 = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  msg.className = 'msg'; msg.textContent = '上传解析中…';
  try {
    await post('/api/resume/upload', {
      name: $('#r-name').value.trim() || file.name.replace(/\.[^.]+$/, ''),
      families, is_default: $('#r-default').checked, filename: file.name, data: base64,
    });
    msg.className = 'msg ok'; msg.textContent = '已上传并解析完成';
    $('#r-file').value = '';
    await refreshFromServer();
  } catch (error) {
    msg.className = 'msg err'; msg.textContent = '上传失败：' + error.message;
  }
});
$('#btn-save-profile').addEventListener('click', async () => {
  const payload = {};
  ['full_name', 'school', 'major', 'degree', 'grad_date', 'political', 'phone', 'email']
    .forEach((key) => { payload[key] = $('#p-' + key).value.trim(); });
  payload.prefer_cities = $('#p-prefer_cities').value.trim();
  payload.veteran = $('#p-veteran').checked ? 1 : 0;
  await post('/api/profile', payload);
  await post('/api/analyze/run', {});
  $('#p-msg').className = 'msg ok';
  $('#p-msg').textContent = '已保存，正在后台重新计算匹配度…';
  await refreshFromServer();
});
$('#btn-task-collect').addEventListener('click', () => runCollect(false, null));
$('#btn-task-analyze').addEventListener('click', async () => {
  await post('/api/analyze/run', {});
  $('#task-msg').className = 'msg ok';
  $('#task-msg').textContent = '已开始重新计算匹配度';
  await refreshTasks();
});
$('#btn-refresh').addEventListener('click', async () => {
  await refreshFromServer();
  await refreshTasks();
  $('#task-msg').className = 'msg ok';
  $('#task-msg').textContent = '已刷新';
});
$('#drawer-close').addEventListener('click', () => $('#drawer').classList.remove('open'));
$('#drawer').addEventListener('click', (event) => {
  if (event.target === $('#drawer')) $('#drawer').classList.remove('open');
});

$('#r-families').innerHTML = Object.entries(FAMILY_LABELS).map(([value, label]) =>
  `<label><input type="checkbox" value="${value}" /> ${label}</label>`).join('');

$('#btn-reconnect').addEventListener('click', async () => {
  $('#btn-reconnect').textContent = '连接中…';
  const ok = await refreshFromServer();
  $('#btn-reconnect').textContent = '重试连接';
  if (!ok) alert('还是连不上本地服务。请确认已经双击桌面「秋招管家」图标，并保持那个黑色命令行窗口开着。');
});

// 启动顺序：先用本地缓存立刻显示（秒开，不需要任何网络），再去后台取最新数据
(async () => {
  if (loadCache()) {
    renderAll();
  }
  await refreshFromServer();
})();
setInterval(() => { if ($('#view-system').classList.contains('active')) refreshTasks(); }, 2500);

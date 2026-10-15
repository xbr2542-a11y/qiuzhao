/* 秋招管家 · 浏览器演示版
 *
 * 这里把 app/match.py 的匹配引擎逐条移植到前端，app/web/app.js 的筛选逻辑也一并搬过来。
 * 目的：让人在手机上就能真的点筛选、切换简历、看分数变化，而不是看几张截图。
 *
 * 与后端版本的关系：
 *   - 维度、权重、打分规则、硬冲突降级，全部照搬，没有简化成"大概算算"。
 *   - 唯一的差异是 JD 文本在导出时截断到 420 字，技能关键词的命中会比本地全量略少。
 */

'use strict';

// ---------------------------------------------------------------- 常量（对齐 match.py）

const WEIGHTS = {
  skill: 18, education: 12, experience: 10, industry: 16, job_type: 14,
  product: 8, language: 6, location: 10, overseas: 6,
};

const SKILL_VOCAB = ['Excel', 'SQL', 'Python', 'Tableau', 'Power BI', 'SPSS', 'SAS', 'R',
  'Word', 'PowerPoint', 'PPT', 'CAD', 'SketchUp', 'V-Ray', 'Photoshop',
  'Office', 'WPS', 'AI', 'ChatGPT', 'DeepSeek', '数据分析', '数据可视化',
  '英语', 'CET-4', 'CET-6', '雅思', '托福', '日语', '韩语', '俄语',
  '西班牙语', '法语', '德语', '阿拉伯语'];

const JOB_TYPE_ALIASES = {
  'B端解决方案销售': ['解决方案型B端销售', '销售支持（含开发职责）'],
  '工业品销售': ['解决方案型B端销售'],
  '海外销售': ['外贸BD销售'],
  '外贸BD': ['外贸BD销售'],
  '国际业务': ['外贸BD销售'],
  '技术销售': ['解决方案型B端销售'],
  '项目型销售': ['解决方案型B端销售'],
  '商务拓展': ['解决方案型B端销售'],
};

const FUNCTION_LABELS = {
  procurement: '采购/寻源', supply_chain: '供应链/物流与计划', sales: 'B端销售',
  trade: '外贸/国际业务', data: '数据分析', other: '其他',
};

const FUNCTION_TARGET_WORDS = {
  procurement: ['采购', '寻源', '供应商', 'sourcing', 'procurement'],
  supply_chain: ['供应链', '物流', '仓储', '库存', '计划', '关务', '报关', '单证'],
  data: ['数据', '分析', 'BI'],
};

const DEGREE_RANK = { '中专': 1, '中技': 1, '高中': 1, '大专': 2, '专科': 2,
  '本科': 3, '学士': 3, '硕士': 4, '研究生': 4, '博士': 5 };

const OVERSEAS_ACCEPT = {
  accept_long_term: ['mandatory_overseas_assignment', 'long_term_assignment',
    'short_term_assignment', 'travel_only', 'not_mentioned'],
  accept_short_term: ['short_term_assignment', 'travel_only', 'not_mentioned'],
  accept_travel: ['travel_only', 'not_mentioned'],
  no_long_term: ['travel_only', 'short_term_assignment', 'not_mentioned'],
  decline_all: ['not_mentioned'],
  ask: [],
  unset: [],
};

const OVERSEAS_LABELS = {
  not_mentioned: '未提及外派', travel_only: '仅海外出差', short_term_assignment: '短期外派',
  long_term_assignment: '长期外派', mandatory_overseas_assignment: '强制境外驻派',
  overseas_local_position: '海外当地岗位',
};

const REVIEW_LABELS = { pending: '已确认对口', need_review: '待人工复核', excluded: '已排除' };

// ---------------------------------------------------------------- 匹配引擎（移植自 app/match.py）

function resumeView(resume) {
  return {
    id: resume.id,
    name: resume.name || '未命名简历',
    skills: (resume.skills || []).filter(Boolean),
    languages: resume.languages || [],
    education: resume.education || '',
    target_industries: resume.target_industries || [],
    target_job_types: resume.target_job_types || [],
    target_cities: resume.target_cities || [],
    overseas_preference: resume.overseas_preference || 'unset',
  };
}

function dimSkill(job, rv) {
  const jd = `${job.title || ''} ${job.jd_text || ''} ${job.major_req || ''}`.toLowerCase();
  const required = SKILL_VOCAB.filter((s) => jd.includes(s.toLowerCase()));
  if (!required.length) return { score: null, matched: [], missing: ['岗位未明确技能要求'] };
  if (!rv.skills.length) {
    return { score: null, matched: [],
      missing: [`简历未填写技能，无法比对：${required.slice(0, 4).join('、')}`] };
  }
  const mine = new Set(rv.skills.map((s) => s.toLowerCase()));
  const hit = required.filter((s) => mine.has(s.toLowerCase()));
  const miss = required.filter((s) => !mine.has(s.toLowerCase()));
  return {
    score: Math.round((100 * hit.length) / required.length),
    matched: hit.length ? [`技能命中：${hit.join('、')}`] : [],
    missing: miss.length ? [`岗位提到但简历未体现：${miss.join('、')}`] : [],
  };
}

function dimEducation(job, rv) {
  const req = (job.degree_req || '').trim();
  const mine = rv.education || '';
  if (!req) return { score: null, matched: [], missing: ['岗位未标注学历要求'], risks: [] };
  const rank = (text) => Math.max(0, ...Object.entries(DEGREE_RANK)
    .filter(([name]) => text.includes(name)).map(([, r]) => r));
  const reqRank = rank(req);
  const myRank = rank(mine);
  if (!myRank) {
    return { score: null, matched: [], missing: ['简历未解析出学历，请补充'], risks: [] };
  }
  if (reqRank > myRank) {
    return { score: 0, matched: [], missing: [`学历要求 ${req}`],
      risks: [`学历不符：岗位要求${req}，简历为${mine}`] };
  }
  return { score: 100, matched: [`学历满足：${mine} ≥ ${req}`], missing: [], risks: [] };
}

function dimExperience(job, rv) {
  const level = job.candidate_level || '';
  const req = job.experience_req || '';
  if (level === '应届可投' || level === '实习') {
    return { score: 100, matched: [`接受应届生：${level}`], missing: [], risks: [] };
  }
  if (level.startsWith('资深')) {
    return { score: 40, matched: [], missing: [req || '要求多年经验'],
      risks: ['岗位偏资深，应届生竞争力弱'] };
  }
  if (!level) return { score: null, matched: [], missing: ['岗位未标注经验要求'], risks: [] };
  return { score: 60, matched: [`经验档位：${level}`], missing: [], risks: [] };
}

function dimIndustry(job, rv) {
  const track = job.industry_track || '';
  const targets = rv.target_industries || [];
  if (!track) return { score: null, matched: [], missing: ['岗位未归入任何目标赛道'] };
  if (!targets.length) return { score: null, matched: [], missing: ['简历未设置目标行业，无法比对'] };
  if (targets.includes(track)) {
    return { score: 100, matched: [`行业匹配：${job.industry_subtrack || track}`], missing: [] };
  }
  return { score: 40, matched: [],
    missing: [`岗位赛道（${job.industry_subtrack || track}）不在简历目标行业内`] };
}

function dimJobType(job, rv) {
  const salesType = job.sales_type || '';
  const targets = rv.target_job_types || [];
  const risks = [];
  const fn = job.job_function || '';
  if (FUNCTION_TARGET_WORDS[fn]) {
    const label = FUNCTION_LABELS[fn] || fn;
    const words = FUNCTION_TARGET_WORDS[fn];
    if (targets.some((t) => words.some((w) => t.includes(w)))) {
      return { score: 100, matched: [`岗位职能匹配：${label}`], missing: [], risks };
    }
    if (!targets.length) {
      return { score: null, matched: [], missing: ['简历未设置目标岗位方向'], risks };
    }
    return { score: 60, matched: [], missing: [`岗位职能（${label}）不在目标方向内`], risks };
  }
  if (!salesType) return { score: null, matched: [], missing: ['岗位未识别销售类型'], risks };
  if (salesType === '非销售岗位') {
    risks.push('非销售岗位，与求职方向不符');
    return { score: 0, matched: [], missing: ['岗位属于非销售类'], risks };
  }
  if (!targets.length) {
    return { score: null, matched: [], missing: ['简历未设置目标岗位方向'], risks };
  }
  const hit = targets.filter((t) => (JOB_TYPE_ALIASES[t] || []).some((a) => salesType.includes(a)));
  if (hit.length) return { score: 100, matched: [`岗位方向匹配：${hit.join('、')}`], missing: [], risks };
  return { score: 40, matched: [], missing: [`岗位方向（${salesType}）不在目标内`], risks };
}

function dimProduct(job) {
  const subtrack = job.industry_subtrack || '';
  const tags = job.match_tags || [];
  if (!subtrack && !tags.length) {
    return { score: null, matched: [], missing: ['岗位未提取到产品/客户信息'] };
  }
  if (tags.some((t) => t.includes('B端') || t.includes('外贸'))) {
    return { score: 80, matched: ['岗位带 B端/外贸属性标签'], missing: [] };
  }
  if (subtrack) return { score: 70, matched: [`产品方向：${subtrack}`], missing: [] };
  return { score: null, matched: [], missing: ['产品与客户类型信息不足'] };
}

function dimLanguage(job, rv) {
  const req = job.language_requirement || '';
  if (!req) return { score: null, matched: [], missing: [] };
  const required = req.split(/[、,，\s]+/).filter(Boolean);
  if (!required.length) return { score: null, matched: [], missing: [] };
  if (!rv.languages.length) {
    return { score: null, matched: [], missing: [`岗位要求语言（${req}），简历未填写语言能力`] };
  }
  const hit = required.filter((w) => rv.languages.some((m) => m.includes(w) || w.includes(m)));
  if (hit.length) return { score: 100, matched: [`语言满足：${hit.join('、')}`], missing: [] };
  return { score: 30, matched: [], missing: [`岗位要求语言（${req}），简历未体现`] };
}

function dimLocation(job, rv) {
  const city = job.city || '';
  const targets = rv.target_cities || [];
  if (!city) return { score: null, matched: [], missing: ['岗位未标注工作地点'] };
  if (!targets.length) return { score: null, matched: [], missing: ['简历未设置目标城市'] };
  const hit = targets.filter((c) => c && city.includes(c));
  if (hit.length) return { score: 100, matched: [`地点命中：${hit.join('、')}`], missing: [] };
  return { score: 40, matched: [], missing: [`工作地点（${city}）不在目标城市内`] };
}

function dimOverseas(job, rv) {
  const status = job.overseas_assignment_status || 'not_mentioned';
  const pref = rv.overseas_preference || 'unset';
  const allowed = OVERSEAS_ACCEPT[pref] || [];
  const label = OVERSEAS_LABELS[status] || status;
  if (!allowed.length) {
    return {
      score: status === 'not_mentioned' ? 80 : 60,
      matched: status === 'not_mentioned' ? [] : [`外派情况：${label}`],
      missing: [], risks: [],
    };
  }
  if (allowed.includes(status) || status === 'not_mentioned') {
    return { score: 100, matched: [`外派情况可接受：${label}`], missing: [], risks: [] };
  }
  return { score: 0, matched: [], missing: [`外派要求：${label}`],
    risks: [`外派冲突：岗位为${label}，你的偏好是不接受`] };
}

/** 单个岗位 × 单份简历，返回分数 + 每个维度的依据。 */
function matchJob(job, resume) {
  const rv = resumeView(resume);
  const matched = [];
  const missing = [];
  const risks = [];
  const details = [];
  const unknown = [];
  let total = 0;
  let weightSum = 0;

  const add = (dim, result, weight) => {
    details.push({ dim, score: result.score, matched: result.matched, missing: result.missing });
    matched.push(...result.matched);
    missing.push(...result.missing);
    (result.risks || []).forEach((r) => risks.push(r));
    if (result.score === null || result.score === undefined) unknown.push(dim);
    else { total += result.score * weight; weightSum += weight; }
  };

  add('技能匹配', dimSkill(job, rv), WEIGHTS.skill);
  add('学历匹配', dimEducation(job, rv), WEIGHTS.education);
  add('经验匹配', dimExperience(job, rv), WEIGHTS.experience);
  add('行业方向', dimIndustry(job, rv), WEIGHTS.industry);
  add('岗位类型', dimJobType(job, rv), WEIGHTS.job_type);
  add('产品/客户', dimProduct(job), WEIGHTS.product);
  add('语言要求', dimLanguage(job, rv), WEIGHTS.language);
  add('工作地点', dimLocation(job, rv), WEIGHTS.location);
  add('外派要求', dimOverseas(job, rv), WEIGHTS.overseas);

  let score = weightSum ? Math.floor(total / weightSum) : 0;
  // 硬冲突必须在排序上体现：只挂风险标签不够，否则 90 分的"强制驻外"会排在前面
  const hard = risks.filter((r) => r.startsWith('学历不符') || r.startsWith('外派冲突')
    || r.startsWith('非销售'));
  if (hard.length) score = Math.max(0, score - 25 * hard.length);

  let level;
  if (weightSum < 40) level = 'unknown';
  else if (score >= 75) level = 'high';
  else if (score >= 55) level = 'medium';
  else level = 'low';
  if (hard.length && level !== 'unknown') level = 'low';

  return { score, level, matched, missing, risks, unknown, dimensions: details, resume: rv.name };
}

// ---------------------------------------------------------------- 筛选

const state = {
  data: null,
  resumeId: null,
  quick: '',
  sort: 'smart',
  visible: 30,
  jobs: [],
  scores: new Map(),
  filters: { city: '', fn: '', degree: '', jobType: '', openOnly: false, majorFree: false },
};

const QUICK_DIRECTIONS = [
  { key: '', label: '全部', test: () => true },
  { key: 'soe', label: '央国企', test: (j) => j.family === 'soe' },
  { key: 'supply', label: '采购 · 供应链',
    test: (j) => j.job_function === 'procurement' || j.job_function === 'supply_chain' },
  { key: 'trade', label: '外贸 · 海外', test: (j) => j.job_function === 'trade' },
  { key: 'sales', label: 'B端销售', test: (j) => j.job_function === 'sales' },
  { key: 'data', label: '数据分析', test: (j) => j.job_function === 'data' },
];

function normalizeCity(token) {
  let t = (token || '').trim().replace(/^[（(]|[）)]$/g, '');
  if (!t) return '';
  for (const sep of ['-', '—', '－', '（', '(']) {
    if (t.includes(sep)) t = t.split(sep)[0].trim();
  }
  // 「北京市」和「北京」要合成同一个选项，否则下拉里会出现两个北京
  if (t.length > 2 && t.endsWith('市')) t = t.slice(0, -1);
  return t;
}

function computeFacets(jobs) {
  const fn = new Map();
  const city = new Map();
  const degree = new Map();
  const jobType = new Map();
  const bump = (map, key) => { if (key) map.set(key, (map.get(key) || 0) + 1); };
  jobs.forEach((j) => {
    bump(fn, j.job_function);
    bump(degree, j.degree_req);
    bump(jobType, j.job_type);
    (j.city || '').split(/[/、,\s]+/).forEach((t) => bump(city, normalizeCity(t)));
  });
  const asRows = (map, limit) => [...map.entries()]
    .sort((a, b) => b[1] - a[1]).slice(0, limit)
    .map(([key, n]) => ({ key, n }));
  return { functions: asRows(fn, 10), cities: asRows(city, 26),
    degrees: asRows(degree, 12), jobTypes: asRows(jobType, 6) };
}

function scoreOf(job) {
  return state.scores.get(job.id) || { score: 0, level: 'unknown' };
}

/** 这个岗位是否落在"当前这份简历"的目标方向上。
    以前这里判断的是一组写死的职能，换哪份简历结果都一样，
    "切换简历看排序变化"就成了空话。 */
function onTarget(job, resume) {
  const targets = (resume && resume.target_job_types) || [];
  if (!targets.length) return false;
  const words = FUNCTION_TARGET_WORDS[job.job_function];
  if (words && targets.some((t) => words.some((w) => t.includes(w)))) return true;
  const salesType = job.sales_type || '';
  return targets.some((t) => (JOB_TYPE_ALIASES[t] || []).some((a) => salesType.includes(a)));
}

function filteredJobs() {
  const quick = QUICK_DIRECTIONS.find((d) => d.key === state.quick) || QUICK_DIRECTIONS[0];
  const resume = state.data.resumes.find((r) => r.id === state.resumeId);
  const f = state.filters;
  const today = new Date().toISOString().slice(0, 10);
  const list = state.data.jobs.filter((job) => {
    if (f.city && !(job.city || '').includes(f.city)) return false;
    if (f.fn && job.job_function !== f.fn) return false;
    if (f.degree && !(job.degree_req || '').includes(f.degree)) return false;
    if (f.jobType && job.job_type !== f.jobType) return false;
    if (f.majorFree && job.major_tier === 3) return false;
    if (f.openOnly && job.deadline && job.deadline < today) return false;
    if (!quick.test(job)) return false;
    return true;
  });
  const sc = (j) => scoreOf(j).score;
  const sorters = {
    // 推荐：先把对口且已确认的顶到前面，再按匹配分排
    smart: (a, b) => {
      const rank = (j) => {
        let v = 0;
        if (j.review_status === 'pending') v -= 40;
        // 对口权的权重必须大于"已确认复核状态"，否则会出现
        // "不属于目标方向但状态为 pending"的岗位压住目标岗位
        if (onTarget(j, resume)) v -= 60;
        return v;
      };
      return (rank(a) - rank(b)) || (sc(b) - sc(a));
    },
    score: (a, b) => sc(b) - sc(a),
    deadline: (a, b) => (a.deadline || '9999-99-99').localeCompare(b.deadline || '9999-99-99'),
    salary: (a, b) => (b.salary_max || 0) - (a.salary_max || 0),
  };
  return list.slice().sort(sorters[state.sort] || sorters.smart);
}

// ---------------------------------------------------------------- 渲染

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function scoreClass(score) {
  if (score >= 75) return 'high';
  if (score >= 55) return 'mid';
  if (score > 0) return 'low';
  return '';
}

function renderChips() {
  const all = state.data.jobs;
  const counts = {};
  QUICK_DIRECTIONS.forEach((d) => {
    if (!d.key) return;
    // 数量要和点下去之后的筛选结果一致，所以这里不再单独排除 excluded
    counts[d.key] = all.filter((j) => d.test(j)).length;
  });
  $('#chips').innerHTML = QUICK_DIRECTIONS.map((d) => {
    const n = d.key ? ` <em>${counts[d.key] || 0}</em>` : '';
    return `<button class="chip${state.quick === d.key ? ' on' : ''}" data-quick="${d.key}">`
      + `${esc(d.label)}${n}</button>`;
  }).join('');
}

function renderFilterOptions() {
  const facets = state.data.facets;
  const fill = (id, rows, placeholder, labeler) => {
    const el = $(id);
    const current = el.value;
    el.innerHTML = `<option value="">${placeholder}</option>`
      + rows.map((r) => `<option value="${esc(r.key)}">${esc(labeler(r))}</option>`).join('');
    el.value = current;
  };
  fill('#f-fn', facets.functions, '全部职能',
    (r) => `${FUNCTION_LABELS[r.key] || r.key}（${r.n}）`);
  fill('#f-city', facets.cities, '全部城市', (r) => `${r.key}（${r.n}）`);
  fill('#f-degree', facets.degrees, '学历不限', (r) => r.key);
  fill('#f-jobtype', facets.jobTypes, '类型不限', (r) => r.key);
}

function renderStats() {
  const list = state.jobs;
  const good = list.filter((j) => scoreOf(j).score >= 60).length;
  const today = new Date().toISOString().slice(0, 10);
  const open = list.filter((j) => !j.deadline || j.deadline >= today).length;
  $('#stats').innerHTML = [
    `演示岗位池 <b>${state.data.jobs.length}</b> 个`,
    `当前筛出 <b class="accent">${list.length}</b> 个`,
    `匹配 60+ <b>${good}</b> 个`,
    `还能投 <b>${open}</b> 个`,
  ].map((t) => `<span>${t}</span>`).join('');
}

function renderActiveFilters() {
  const f = state.filters;
  const out = [];
  const quick = QUICK_DIRECTIONS.find((d) => d.key === state.quick);
  if (quick && quick.key) out.push({ text: `方向：${quick.label}`, clear: () => { state.quick = ''; } });
  if (f.fn) out.push({ text: `职能：${FUNCTION_LABELS[f.fn] || f.fn}`,
    clear: () => { f.fn = ''; $('#f-fn').value = ''; } });
  if (f.city) out.push({ text: `城市：${f.city}`, clear: () => { f.city = ''; $('#f-city').value = ''; } });
  if (f.degree) out.push({ text: `学历：${f.degree}`, clear: () => { f.degree = ''; $('#f-degree').value = ''; } });
  if (f.jobType) out.push({ text: `类型：${f.jobType}`, clear: () => { f.jobType = ''; $('#f-jobtype').value = ''; } });
  if (f.openOnly) out.push({ text: '只看还能投的', clear: () => { f.openOnly = false; $('#f-open').checked = false; } });
  if (f.majorFree) out.push({ text: '隐藏专业不符', clear: () => { f.majorFree = false; $('#f-majorfree').checked = false; } });
  const box = $('#active-filters');
  if (!out.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
  box.style.display = 'flex';
  box.innerHTML = out.map((o, i) => `<button class="afilter" data-clear="${i}">${esc(o.text)} ✕</button>`).join('');
  box.querySelectorAll('[data-clear]').forEach((el) => {
    el.addEventListener('click', () => { out[Number(el.dataset.clear)].clear(); refresh(); });
  });
}

function renderJobs() {
  const list = state.jobs;
  const shown = list.slice(0, state.visible);
  $('#jobs').innerHTML = shown.map((job) => {
    const s = scoreOf(job);
    const tags = [
      job.job_function && FUNCTION_LABELS[job.job_function]
        ? `<span class="tag ok">${esc(FUNCTION_LABELS[job.job_function])}</span>` : '',
      job.industry_subtrack ? `<span class="tag primary">${esc(job.industry_subtrack)}</span>` : '',
      job.city ? `<span class="tag">${esc(job.city)}</span>` : '',
      job.degree_req ? `<span class="tag">${esc(job.degree_req)}</span>` : '',
      job.family === 'soe' ? '<span class="tag primary">央国企</span>' : '',
      ['mandatory_overseas_assignment', 'long_term_assignment'].includes(job.overseas_assignment_status)
        ? `<span class="tag danger">${job.overseas_assignment_status === 'mandatory_overseas_assignment' ? '强制驻外' : '长期外派'}</span>`
        : (job.overseas_assignment_status === 'travel_only' ? '<span class="tag warn">海外出差</span>' : ''),
    ].filter(Boolean);
    const tagHtml = tags.slice(0, 3).join('') + (tags.length > 3 ? `<span class="tag">+${tags.length - 3}</span>` : '');
    return `<article class="job-card" data-id="${job.id}">
      <div class="job-top">
        <div class="job-main">
          <h3 class="job-title">${esc(job.title)}</h3>
          <div class="job-company">${esc(job.company)}
            ${job.salary ? `<span class="salary">${esc(job.salary)}</span>` : ''}</div>
          <div class="tags">${tagHtml}</div>
        </div>
        <div class="score ${scoreClass(s.score)}">${s.score}<small>匹配</small></div>
      </div>
      <div class="job-foot">
        <span>来源 ${esc(job.source)}</span>
        <span>${esc(REVIEW_LABELS[job.review_status] || job.review_status || '')}</span>
        <button class="btn" style="margin-left:auto" data-detail="${job.id}">看匹配明细</button>
      </div>
    </article>`;
  }).join('') || '<div class="empty">没有符合条件的岗位，试着放宽筛选条件。</div>';
  $('#more').style.display = list.length > shown.length ? 'block' : 'none';
  $('#more').textContent = `显示更多（还有 ${list.length - shown.length} 个）`;
}

function showDetail(id) {
  const job = state.data.jobs.find((j) => String(j.id) === String(id));
  const resume = state.data.resumes.find((r) => r.id === state.resumeId);
  if (!job) return;
  const m = matchJob(job, resume);
  const rows = m.dimensions.map((d) => {
    const na = d.score === null || d.score === undefined;
    return `<div class="dim-row">
      <span class="dim-name">${esc(d.dim)}</span>
      <span class="dim-bar${na ? ' na' : ''}"><i style="width:${na ? 100 : d.score}%"></i></span>
      <span class="dim-score">${na ? '信息不足' : d.score}</span>
    </div>`;
  }).join('');
  const listBlock = (title, items, cls) => (items.length
    ? `<div class="section-title">${title}</div><ul class="reason-list ${cls || ''}">`
      + items.map((x) => `<li>${esc(x)}</li>`).join('') + '</ul>' : '');
  $('#drawer-body').innerHTML = `
    <h2 style="margin:0 0 4px;font-size:18px">${esc(job.title)}</h2>
    <div style="color:var(--ink-2);font-size:13px">${esc(job.company)} · ${esc(job.city || '')}
      ${job.salary ? ' · ' + esc(job.salary) : ''}</div>
    <div style="display:flex;align-items:center;gap:14px;margin:16px 0 6px">
      <div class="score ${scoreClass(m.score)}" style="width:66px;height:66px;font-size:21px">
        ${m.score}<small>匹配度</small></div>
      <div style="font-size:13px;color:var(--ink-2)">
        按「${esc(resume.name)}」计算<br>
        <span style="color:var(--ink-3);font-size:12px">
          ${m.unknown.length ? `信息不足的维度：${esc(m.unknown.join('、'))}` : '所有维度都有依据'}</span>
      </div>
    </div>
    <div class="section-title">各维度得分</div>
    ${rows}
    ${listBlock('命中', m.matched)}
    ${listBlock('缺失 / 未体现', m.missing)}
    ${listBlock('风险', m.risks)}
    ${job.jd_text ? `<div class="section-title">岗位描述${job.jd_truncated ? '（演示版节选）' : ''}</div>
      <div class="jd">${esc(job.jd_text)}</div>` : ''}
  `;
  $('#drawer').classList.add('open');
}

function refresh() {
  state.jobs = filteredJobs();
  state.visible = 30;
  renderChips(); renderStats(); renderActiveFilters(); renderJobs();
}

function recomputeScores() {
  const resume = state.data.resumes.find((r) => r.id === state.resumeId);
  state.scores = new Map();
  state.data.jobs.forEach((job) => {
    const m = matchJob(job, resume);
    state.scores.set(job.id, { score: m.score, level: m.level });
  });
}

// ---------------------------------------------------------------- 启动

async function boot() {
  const res = await fetch('demo/data.json');
  state.data = await res.json();
  state.data.facets = computeFacets(state.data.jobs);
  state.resumeId = state.data.resumes[0].id;

  $('#resume-select').innerHTML = state.data.resumes
    .map((r) => `<option value="${r.id}">${esc(r.name)} — ${esc(r.note)}</option>`).join('');
  $('#generated-at').textContent = state.data.generated_at || '';
  renderFilterOptions();
  recomputeScores();
  refresh();
}

document.addEventListener('click', (event) => {
  const el = event.target;
  if (!(el instanceof HTMLElement)) return;
  if (el.dataset.quick !== undefined) { state.quick = el.dataset.quick; refresh(); }
  if (el.dataset.detail) showDetail(el.dataset.detail);
  if (el.id === 'drawer' || el.id === 'drawer-close') $('#drawer').classList.remove('open');
  if (el.id === 'more') { state.visible += 30; renderJobs(); }
});

$('#resume-select').addEventListener('change', (e) => {
  state.resumeId = Number(e.target.value);
  recomputeScores();
  refresh();
});

[['#f-fn', 'fn'], ['#f-city', 'city'], ['#f-degree', 'degree'], ['#f-jobtype', 'jobType']]
  .forEach(([sel, key]) => $(sel).addEventListener('change', (e) => {
    state.filters[key] = e.target.value; refresh();
  }));

$('#f-sort').addEventListener('change', (e) => { state.sort = e.target.value; refresh(); });
$('#f-open').addEventListener('change', (e) => { state.filters.openOnly = e.target.checked; refresh(); });
$('#f-majorfree').addEventListener('change', (e) => { state.filters.majorFree = e.target.checked; refresh(); });
$('#btn-reset').addEventListener('click', () => {
  state.quick = '';
  state.sort = 'smart';
  state.filters = { city: '', fn: '', degree: '', jobType: '', openOnly: false, majorFree: false };
  $('#f-fn').value = ''; $('#f-city').value = ''; $('#f-degree').value = '';
  $('#f-jobtype').value = ''; $('#f-sort').value = 'smart';
  $('#f-open').checked = false; $('#f-majorfree').checked = false;
  refresh();
});

boot().catch((err) => {
  $('#jobs').innerHTML = `<div class="empty">数据加载失败：${esc(err.message)}</div>`;
});

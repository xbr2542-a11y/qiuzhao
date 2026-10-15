// 用真实 Chrome 打开各公司招聘官网，识别背后用的是哪套招聘系统。
// 直接抓 JSON 接口会被部分站点的证书/反爬挡住，用浏览器渲染更可靠。
// 用法：node tools/ats_probe_browser.cjs
const path = require('path');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (error) {
  console.error('[失败] 无法加载 playwright:', error.message);
  process.exit(2);
}

const TARGETS = [
  ['英特尔 Intel', 'https://jobs.intel.com/en/search-jobs'],
  ['AMD', 'https://jobs.amd.com/'],
  ['英伟达 NVIDIA', 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'],
  ['高通 Qualcomm', 'https://careers.qualcomm.com/careers'],
  ['应用材料 Applied Materials', 'https://careers.appliedmaterials.com/careers'],
  ['泛林 Lam Research', 'https://careers.lamresearch.com/'],
  ['KLA', 'https://careers.kla.com/careers'],
  ['英飞凌 Infineon', 'https://www.infineon.com/cms/en/careers/'],
  ['意法半导体 ST', 'https://www.st.com/content/st_com/en/about/careers.html'],
  ['安森美 onsemi', 'https://www.onsemi.com/careers'],
  ['微芯 Microchip', 'https://www.microchip.com/en-us/about/careers'],
  ['瑞萨 Renesas', 'https://www.renesas.com/en/about/careers'],
  ['泰科 TE Connectivity', 'https://careers.te.com/'],
  ['霍尼韦尔 Honeywell', 'https://careers.honeywell.com/us/en'],
  ['西门子 Siemens', 'https://jobs.siemens.com/careers'],
  ['丹纳赫 Danaher', 'https://jobs.danaher.com/global/en'],
  ['赛默飞 Thermo Fisher', 'https://jobs.thermofisher.com/global/en'],
  ['巴斯夫 BASF', 'https://www.basf.com/global/en/careers'],
  ['马士基 Maersk', 'https://www.maersk.com/careers'],
  ['嘉吉 Cargill', 'https://careers.cargill.com/en'],
  ['宝洁 P&G', 'https://www.pgcareers.com/global/en'],
  ['联合利华 Unilever', 'https://careers.unilever.com/'],
];

const SIGNATURES = [
  ['Workday', /([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com\/[A-Za-z0-9_\-/]*)/i],
  ['SuccessFactors', /([a-z0-9-]+\.successfactors\.(?:com|eu)[A-Za-z0-9_\-/]*)/i],
  ['Greenhouse', /(job-boards\.greenhouse\.io\/[a-z0-9-]+|boards\.greenhouse\.io\/[a-z0-9-]+)/i],
  ['Lever', /(jobs\.lever\.co\/[a-z0-9-]+)/i],
  ['SmartRecruiters', /(jobs\.smartrecruiters\.com\/[a-z0-9-]+|careers\.smartrecruiters\.com\/[a-z0-9-]+)/i],
  ['iCIMS', /([a-z0-9-]+\.icims\.com)/i],
  ['Phenom', /([a-z0-9-]+\.phenompeople\.com|phenom\.cloud)/i],
  ['Avature', /([a-z0-9-]+\.avature\.net)/i],
  ['Oracle ORC', /([a-z0-9-]+\.oraclecloud\.com\/hcmUI\/CandidateExperience[A-Za-z0-9_\-/]*)/i],
  ['Workday-hosted-jobs', /myworkdayjobs\.com/i],
];

async function main() {
  const root = process.env.QIUZHAO_ROOT || path.resolve(__dirname, '..');
  const context = await chromium.launchPersistentContext(
    process.env.QIUZHAO_PROFILE || path.join(root, 'data', 'chrome-profile'),
    { channel: process.env.QIUZHAO_CHANNEL || 'chrome', headless: true, locale: 'zh-CN' });
  const found = [];
  for (const [name, url] of TARGETS) {
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 35000 });
      await page.waitForTimeout(3500);
      const html = await page.content();
      const hits = [];
      for (const [label, pattern] of SIGNATURES) {
        const match = html.match(pattern);
        if (match && !hits.includes(label + ':' + match[1])) hits.push(label + ':' + match[1]);
      }
      const summary = hits.slice(0, 2).join('、') || '未识别';
      console.log(`${name.padEnd(22)} ${hits.length ? '✔' : '✘'} ${summary}`);
      if (hits.length) found.push([name, url, summary]);
    } catch (error) {
      console.log(`${name.padEnd(22)} ✘ 打不开：${error.message.slice(0, 60)}`);
    } finally {
      await page.close();
    }
  }
  console.log('\n=== 可用 Workday 的公司 ===');
  found.filter(([, , s]) => s.includes('Workday')).forEach(([n, , s]) => console.log('  ' + n + ' -> ' + s));
  await context.close();
}

main().catch((error) => {
  console.error('[失败]', error.message);
  process.exit(1);
});

// 浏览器探针：用真实 Chrome 打开页面，等渲染完，输出可见文本/链接/接口请求。
// 用法：node tools/browser_probe.cjs <url> [--wait 4000] [--save out.html] [--links 40] [--api]
const fs = require('fs');
const path = require('path');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (error) {
  console.error('[失败] 无法加载 playwright:', error.message);
  process.exit(2);
}

function arg(name, fallback) {
  const index = process.argv.indexOf('--' + name);
  return index > -1 ? process.argv[index + 1] : fallback;
}

async function main() {
  const url = process.argv[2];
  if (!url) {
    console.error('用法: node tools/browser_probe.cjs <url> [--wait 4000] [--save out.html]');
    process.exit(1);
  }
  const waitMs = Number(arg('wait', 4000));
  const saveName = arg('save', '');
  const linkLimit = Number(arg('links', 30));
  const showApi = process.argv.includes('--api');

  const root = process.env.QIUZHAO_ROOT || path.resolve(__dirname, '..');
  const channel = process.env.QIUZHAO_CHANNEL || 'chrome';
  const userDataDir = process.env.QIUZHAO_PROFILE || path.join(root, 'data', 'chrome-profile');

  const context = await chromium.launchPersistentContext(userDataDir, {
    channel,
    headless: !process.argv.includes('--headed'),
    locale: 'zh-CN',
    viewport: { width: 1440, height: 900 },
  });
  const page = context.pages()[0] || (await context.newPage());

  const apiCalls = [];
  page.on('response', (response) => {
    const type = response.request().resourceType();
    const target = response.url();
    if ((type === 'xhr' || type === 'fetch') && apiCalls.length < 40) {
      apiCalls.push(`${response.status()} ${type} ${target.slice(0, 160)}`);
    }
  });

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(waitMs);

  const title = await page.title();
  const text = (await page.evaluate(() => document.body.innerText || '')).replace(/\n{2,}/g, '\n').trim();
  const links = await page.evaluate((limit) => Array.from(document.querySelectorAll('a[href]'))
    .map((a) => `${(a.innerText || '').trim().slice(0, 40)} -> ${a.href}`)
    .filter((line) => line.length > 6)
    .slice(0, limit), linkLimit);

  console.log('标题:', title);
  console.log('可见文本长度:', text.length);
  console.log('--- 可见文本前 1200 字 ---');
  console.log(text.slice(0, 1200));
  console.log('--- 链接 ---');
  links.forEach((line) => console.log('  ' + line));
  if (showApi) {
    console.log('--- 接口请求 ---');
    apiCalls.forEach((line) => console.log('  ' + line));
  }
  if (saveName) {
    const target = path.join(root, '.tmp', 'probe', saveName);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, await page.content(), 'utf8');
    console.log('已保存 HTML:', target);
  }
  await context.close();
}

main().catch((error) => {
  console.error('[失败]', error.message);
  process.exit(1);
});

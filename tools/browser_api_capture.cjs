// 抓取页面上的 JSON 接口：打开页面后记录所有返回 JSON 的响应，打印地址与片段。
// 用法：node tools/browser_api_capture.cjs <url> [--wait 8000] [--min 2000] [--match 岗位]
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
    console.error('用法: node tools/browser_api_capture.cjs <url> [--wait 8000]');
    process.exit(1);
  }
  const waitMs = Number(arg('wait', 8000));
  const minBytes = Number(arg('min', 1500));
  const matchWord = arg('match', '');

  const root = process.env.QIUZHAO_ROOT || path.resolve(__dirname, '..');
  const channel = process.env.QIUZHAO_CHANNEL || 'chrome';
  const userDataDir = process.env.QIUZHAO_PROFILE || path.join(root, 'data', 'chrome-profile');

  const context = await chromium.launchPersistentContext(userDataDir, {
    channel,
    headless: true,
    locale: 'zh-CN',
    viewport: { width: 1440, height: 900 },
  });
  const page = context.pages()[0] || (await context.newPage());
  const hits = [];
  const allRequests = [];

  page.on('request', (request) => {
    const type = request.resourceType();
    if (type === 'xhr' || type === 'fetch') {
      allRequests.push(`${request.method()} ${request.url().slice(0, 200)}`);
    }
  });

  page.on('response', async (response) => {
    try {
      const type = response.request().resourceType();
      if (type !== 'xhr' && type !== 'fetch') return;
      const contentType = (response.headers()['content-type'] || '').toLowerCase();
      if (!contentType.includes('json')) return;
      const body = await response.text();
      if (body.length < minBytes) return;
      if (matchWord && !body.includes(matchWord)) return;
      hits.push({
        url: response.url(),
        method: response.request().method(),
        postData: response.request().postData() || '',
        size: body.length,
        body,
      });
    } catch (error) {
      /* 忽略读取失败 */
    }
  });

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(waitMs);

  console.log(`=== 捕获到 ${hits.length} 个 JSON 响应 ===`);
  hits.sort((a, b) => b.size - a.size);
  hits.slice(0, 8).forEach((hit, index) => {
    console.log(`\n[${index + 1}] ${hit.method} ${hit.url}`);
    console.log(`    大小 ${hit.size} 字节`);
    if (hit.postData) {
      console.log(`    请求体 ${hit.postData.slice(0, 500)}`);
    }
    console.log(`    片段 ${hit.body.slice(0, 400)}`);
  });
  console.log('\n=== 所有 XHR/Fetch 请求 ===');
  [...new Set(allRequests)].slice(0, 40).forEach((line) => console.log('  ' + line));

  await context.close();
}

main().catch((error) => {
  console.error('[失败]', error.message);
  process.exit(1);
});

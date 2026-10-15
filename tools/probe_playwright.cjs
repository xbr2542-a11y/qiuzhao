// 浏览器自检：验证 Playwright 能驱动本机已安装的 Chrome / Edge
const fs = require('fs');
const path = require('path');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (error) {
  console.error('[失败] 无法加载 playwright 模块:', error.message);
  console.error('请确认已通过 NODE_PATH 指向包含 playwright 的 node_modules。');
  process.exit(2);
}

async function main() {
  const root = process.env.QIUZHAO_ROOT || path.resolve(__dirname, '..');
  const outDir = path.join(root, 'data', 'probe');
  fs.mkdirSync(outDir, { recursive: true });

  const channel = process.env.QIUZHAO_CHANNEL || 'chrome';
  const headed = process.argv.includes('--headed');
  console.log(`启动浏览器: channel=${channel} headless=${!headed}`);

  const browser = await chromium.launch({ channel, headless: !headed });
  const context = await browser.newContext({ locale: 'zh-CN' });
  const page = await context.newPage();
  await page.setContent('<h1 id="t">秋招管家 · 浏览器自检通过</h1><p id="ua"></p>');
  const text = await page.textContent('#t');
  const ua = await page.evaluate(() => navigator.userAgent);
  const shot = path.join(outDir, 'probe.png');
  await page.screenshot({ path: shot });
  await browser.close();

  console.log('页面文本:', text);
  console.log('UA:', ua);
  console.log('浏览器版本:', browser.version());
  console.log('截图:', shot);
  console.log('PLAYWRIGHT_OK');
}

main().catch((error) => {
  console.error('[失败]', error.message);
  process.exit(1);
});

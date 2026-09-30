import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9335;
const TMP_DIR = path.resolve('.tmp_chrome_rec');

async function getWsUrl() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      if (res.ok) {
        const list = await res.json();
        const page = list.find(p => p.type === 'page');
        if (page && page.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
      }
    } catch (e) {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error("Could not connect to Chrome debug port");
}

async function record(targetUrl, framesDir) {
  if (fs.existsSync(TMP_DIR)) fs.rmSync(TMP_DIR, { recursive: true, force: true });
  fs.mkdirSync(TMP_DIR, { recursive: true });
  fs.mkdirSync(framesDir, { recursive: true });

  const chrome = spawn(CHROME_PATH, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${TMP_DIR}`,
    '--window-size=1420,840',
    '--disable-gpu',
    '--no-sandbox',
    targetUrl
  ]);

  try {
    const wsUrl = await getWsUrl();
    const ws = new WebSocket(wsUrl);
    await new Promise(r => ws.onopen = r);

    let msgId = 1;
    function send(method, params = {}) {
      return new Promise((resolve) => {
        const id = msgId++;
        const handler = (evt) => {
          const msg = JSON.parse(evt.data);
          if (msg.id === id) {
            ws.removeEventListener('message', handler);
            resolve(msg.result);
          }
        };
        ws.addEventListener('message', handler);
        ws.send(JSON.stringify({ id, method, params }));
      });
    }

    async function evaluate(expression) {
      return await send('Runtime.evaluate', { expression, returnByValue: true });
    }

    async function wait(ms) {
      return new Promise(r => setTimeout(r, ms));
    }

    let frameCount = 0;
    async function snap(name) {
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      const filename = path.join(framesDir, `${String(frameCount++).padStart(3, '0')}_${name}.png`);
      fs.writeFileSync(filename, Buffer.from(shot.data, 'base64'));
      console.log(`Captured frame: ${filename}`);
    }

    await send('Page.enable');
    const fullUrl = targetUrl + (targetUrl.includes('?') ? '&' : '?') + 'theme=dark&style=claude';
    console.log("Navigating to:", fullUrl);
    await send('Page.navigate', { url: fullUrl });

    // Wait until bootstrap finishes and DATA is loaded
    for (let i = 0; i < 50; i++) {
      const res = await evaluate('typeof DATA !== "undefined" && DATA !== null');
      if (res && res.result && res.result.value) {
        console.log("DATA loaded successfully!");
        break;
      }
      await wait(150);
    }
    await wait(1200);

    // 1. Initial State: Dark theme, Dismiss setup, Changes card in right rail
    await evaluate(`
      state.themeMode = 'dark';
      applyThemeMode();
      renderThemePick();
      send('setup_hide');
      document.documentElement.style.setProperty('--rail-w', '380px');
      state.railSection = 'changes';
      state.railFile = -1;
      renderRail();
    `);
    await wait(600);
    await snap('01_changes_card');

    // 2. Open Diff for first file (UserService.java)
    await evaluate(`
      openFile(0);
    `);
    await wait(500);
    await snap('02_diff_view');

    // 3. Switch to 'now' tab in diff viewer
    await evaluate(`
      const tabs = document.querySelectorAll('.pv-tabs button');
      if (tabs && tabs[1]) tabs[1].click();
    `);
    await wait(500);
    await snap('03_diff_now_tab');

    // 4. Switch back to 'diff' tab
    await evaluate(`
      const tabs = document.querySelectorAll('.pv-tabs button');
      if (tabs && tabs[0]) tabs[0].click();
    `);
    await wait(500);
    await snap('04_diff_back_tab');

    // 5. Switch to Tasks tab
    await evaluate(`
      const railTabs = document.querySelectorAll('.rail-tab');
      if (railTabs && railTabs[1]) railTabs[1].click();
    `);
    await wait(500);
    await snap('05_tasks_tab');

    // 6. Click 'بدء التنفيذ التتابعي'
    await evaluate(`
      const btn = document.querySelector('.plan-seq-btn');
      if (btn) btn.click();
    `);
    await wait(600);
    await snap('06_seq_running');

    // 7. Complete step 2 and simulate advance to step 3 with checkmark
    await evaluate(`
      if (DATA && DATA.plan) {
        DATA.plan.step = 3;
        DATA.plan.verified = 2;
        DATA.plan.steps[1].status = 'verified';
        DATA.plan.steps[2].status = 'in_progress';
        DATA.plan.steps[2].current = true;
        renderRail();
        toast('✓ Step 2 verified. Running step 3/5: Login and issue a JWT');
      }
    `);
    await wait(600);
    await snap('07_seq_step3_advanced');

    ws.close();
  } finally {
    chrome.kill();
  }
}

const [targetUrl, framesDir] = process.argv.slice(2);
record(targetUrl, framesDir).catch(err => {
  console.error(err);
  process.exit(1);
});

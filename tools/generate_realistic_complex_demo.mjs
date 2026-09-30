import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';

const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9336;
const TMP_DIR = path.resolve('.tmp_chrome_complex');
const FRAMES_DIR = path.resolve('.tmp_complex_frames');

const HTML_CONTENT = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #090d16;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    padding: 24px;
    color: #e2e8f0;
  }
  .window {
    width: 1080px;
    height: 660px;
    background: #0f172a;
    border-radius: 12px;
    border: 1px solid #334155;
    box-shadow: 0 25px 60px -15px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.05);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .titlebar {
    height: 42px;
    background: #1e293b;
    border-bottom: 1px solid #334155;
    display: flex;
    align-items: center;
    padding: 0 16px;
    justify-content: space-between;
    user-select: none;
  }
  .dots { display: flex; gap: 8px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .dot-red { background: #ef4444; }
  .dot-yellow { background: #eab308; }
  .dot-green { background: #22c55e; }
  .title {
    font-size: 12.5px;
    font-weight: 600;
    color: #94a3b8;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .title span { color: #38bdf8; font-weight: 700; }
  .badge-session {
    font-size: 11px;
    background: #0284c7;
    color: #fff;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
  }
  .subbar {
    height: 34px;
    background: #131d31;
    border-bottom: 1px solid #1e293b;
    display: flex;
    align-items: center;
    padding: 0 16px;
    gap: 20px;
    font-size: 11.5px;
    color: #64748b;
  }
  .subbar-item { display: flex; align-items: center; gap: 6px; }
  .subbar-item b { color: #f1f5f9; font-weight: 600; }
  .subbar-item .tag {
    background: #1e293b;
    color: #38bdf8;
    padding: 1px 6px;
    border-radius: 4px;
    border: 1px solid #334155;
  }
  .terminal {
    flex: 1;
    padding: 20px 24px;
    overflow-y: auto;
    font-size: 13px;
    line-height: 1.55;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .prompt-line {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  .prompt-path { color: #38bdf8; font-weight: 700; }
  .prompt-cmd { color: #f8fafc; font-weight: 600; }
  .banner-box {
    color: #38bdf8;
    font-size: 11px;
    line-height: 1.25;
    padding: 6px 12px;
    background: #091220;
    border-left: 3px solid #38bdf8;
    border-radius: 4px;
  }
  .step-box {
    background: #131d31;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 12px 14px;
  }
  .step-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 8px;
  }
  .diff-table {
    width: 100%;
    font-size: 12px;
    border-collapse: collapse;
    margin-top: 6px;
  }
  .diff-table tr td { padding: 2px 8px; }
  .diff-add { background: rgba(34, 197, 94, 0.15); color: #4ade80; }
  .diff-del { background: rgba(239, 68, 68, 0.15); color: #f87171; }
  .diff-meta { color: #38bdf8; }
  .diff-file { color: #94a3b8; font-weight: 700; }
  .error-box {
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid #ef4444;
    border-radius: 6px;
    padding: 10px 14px;
    color: #fca5a5;
  }
  .repair-box {
    background: rgba(168, 85, 247, 0.08);
    border: 1px solid #a855f7;
    border-radius: 6px;
    padding: 10px 14px;
    color: #d8b4fe;
  }
  .success-box {
    background: rgba(34, 197, 94, 0.1);
    border: 1px solid #22c55e;
    border-radius: 6px;
    padding: 10px 14px;
    color: #86efac;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .cursor {
    display: inline-block;
    width: 8px;
    height: 15px;
    background: #38bdf8;
    vertical-align: middle;
    animation: blink 1s infinite;
  }
  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
</style>
</head>
<body>
<div class="window">
  <div class="titlebar">
    <div class="dots">
      <div class="dot dot-red"></div>
      <div class="dot dot-yellow"></div>
      <div class="dot dot-green"></div>
    </div>
    <div class="title">
      <span>AI CODE ENGINEER</span> • Autonomous Refactor & Self-Healing Pipeline
    </div>
    <div class="badge-session">ACTIVE AGENT</div>
  </div>

  <div class="subbar">
    <div class="subbar-item">Workspace: <b>spring-auth-service</b></div>
    <div class="subbar-item">Model: <span class="tag">deepseek-r1 / ollama</span></div>
    <div class="subbar-item">Zero-Trust: <b style="color:#4ade80">ENFORCED</b></div>
    <div class="subbar-item">Auto-Verify: <b style="color:#38bdf8">ON</b></div>
  </div>

  <div class="terminal" id="term"></div>
</div>
</body>
</html>`;

const STAGES = [
  // Stage 1: User prompt typed
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
    <span class="cursor"></span>
  </div>
  `,

  // Stage 2: AST Analysis & Indexing
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
  </div>
  <div class="step-box">
    <div class="step-header" style="color:#38bdf8">
      <span>🔍 AST SYMBOL INDEXER</span>
      <span>[0.04s]</span>
    </div>
    <div style="color:#94a3b8">
      Parsed 16 symbols across 3 target files:
      <span style="color:#f1f5f9">SecurityConfig.java, JwtAuthFilter.java, RedisTokenStore.java</span>
    </div>
  </div>
  `,

  // Stage 3: Multi-file Cryptographic Diff Proposal
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
  </div>
  <div class="step-box">
    <div class="step-header" style="color:#c084fc">
      <span>✍️ PROPOSED CRYPTOGRAPHIC PATCH (3 Files)</span>
      <span style="font-size:11px;background:#3b0764;color:#e9d5ff;padding:2px 8px;border-radius:4px">SHA-256: e83b109c...</span>
    </div>
    <table class="diff-table">
      <tr><td colspan="2" class="diff-file">--- a/src/main/java/config/SecurityConfig.java</td></tr>
      <tr><td colspan="2" class="diff-file">+++ b/src/main/java/config/SecurityConfig.java</td></tr>
      <tr><td class="diff-meta">@@ -34,3 +34,8 @@</td><td class="diff-meta">public SecurityFilterChain filterChain(HttpSecurity http)</td></tr>
      <tr class="diff-del"><td>-</td><td>http.authorizeHttpRequests(auth -&gt; auth.anyRequest().authenticated());</td></tr>
      <tr class="diff-add"><td>+</td><td>http.authorizeHttpRequests(auth -&gt; auth.requestMatchers("/api/admin/**").hasRole("ADMIN")</td></tr>
      <tr class="diff-add"><td>+</td><td>    .anyRequest().authenticated()).addFilterBefore(jwtAuthFilter(), UsernamePasswordAuthFilter.class);</td></tr>
    </table>
  </div>
  `,

  // Stage 4: Test Run 1 fails (triggers self-healing loop)
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
  </div>
  <div class="step-box">
    <div class="step-header" style="color:#c084fc">
      <span>✍️ PROPOSED CRYPTOGRAPHIC PATCH (3 Files)</span>
      <span style="font-size:11px;background:#3b0764;color:#e9d5ff;padding:2px 8px;border-radius:4px">SHA-256: e83b109c...</span>
    </div>
    <div style="color:#94a3b8;font-size:12px;margin-bottom:8px">Applied non-destructive checkpoint commit [session-8f2a]</div>
  </div>
  <div class="error-box">
    <div style="font-weight:700;display:flex;justify-content:space-between;margin-bottom:4px">
      <span>⚙️ RUN #1: mvn -B test</span>
      <span style="color:#ef4444">FAILED (exit 1)</span>
    </div>
    <div style="font-size:12px">[ERROR] NoSuchBeanDefinitionException: No qualifying bean of type 'RedisTemplate'</div>
    <div style="font-size:11.5px;color:#cbd5e1;margin-top:4px">🔄 FEEDBACK LOOP TRIGGERED: Feeding compiler & test trace back to model...</div>
  </div>
  `,

  // Stage 5: Autonomous Repair Round 2
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
  </div>
  <div class="repair-box">
    <div style="font-weight:700;display:flex;justify-content:space-between;margin-bottom:4px">
      <span>🔄 AUTONOMOUS REPAIR ROUND 2/3</span>
      <span style="color:#c084fc">Synthesizing Fix</span>
    </div>
    <div style="font-size:12px">Configuring missing @Bean RedisTemplate&lt;String, Object&gt; in SecurityConfig.java...</div>
    <table class="diff-table" style="margin-top:6px">
      <tr class="diff-add"><td>+</td><td>@Bean public RedisTemplate&lt;String, Object&gt; redisTemplate(RedisConnectionFactory factory) {</td></tr>
      <tr class="diff-add"><td>+</td><td>    RedisTemplate&lt;String, Object&gt; template = new RedisTemplate&lt;&gt;();</td></tr>
      <tr class="diff-add"><td>+</td><td>    template.setConnectionFactory(factory); return template;</td></tr>
    </table>
  </div>
  <div style="color:#38bdf8;font-size:12px">⚙️ Re-executing test suite: mvn -B test...</div>
  `,

  // Stage 6: Full Verification & Green Success
  `
  <div class="prompt-line">
    <span class="prompt-path">PS D:\\spring-auth-service&gt;</span>
    <span class="prompt-cmd">agent "Implement Redis token blacklist & RBAC in SecurityConfig and verify with mvn test"</span>
  </div>
  <div class="repair-box">
    <div style="font-weight:700">🔄 AUTONOMOUS REPAIR ROUND 2: Configured missing @Bean RedisTemplate</div>
  </div>
  <div class="success-box">
    <div>
      <div style="font-weight:700;font-size:13.5px">✅ BUILD SUCCESS: All 18 tests verified</div>
      <div style="font-size:11.5px;color:#cbd5e1;margin-top:4px">JUnit XML evidence verified in 1.4s • Surefire report: target/surefire-reports/*.xml</div>
      <div style="font-size:11.5px;color:#a7f3d0;margin-top:2px">🌿 Git Checkpoint [session-8f2a] verified • Clean rollback available</div>
    </div>
    <div style="font-size:20px">🎉</div>
  </div>
  `
];

async function main() {
  if (fs.existsSync(TMP_DIR)) fs.rmSync(TMP_DIR, { recursive: true, force: true });
  if (fs.existsSync(FRAMES_DIR)) fs.rmSync(FRAMES_DIR, { recursive: true, force: true });
  fs.mkdirSync(TMP_DIR, { recursive: true });
  fs.mkdirSync(FRAMES_DIR, { recursive: true });

  // Start simple static HTTP server
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(HTML_CONTENT);
  });
  await new Promise(r => server.listen(9337, '127.0.0.1', r));

  const chrome = spawn(CHROME_PATH, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${TMP_DIR}`,
    '--window-size=1160,720',
    '--disable-gpu',
    '--no-sandbox',
    'http://127.0.0.1:9337/'
  ]);

  try {
    // Connect to CDP
    let wsUrl = '';
    for (let i = 0; i < 40; i++) {
      try {
        const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
        if (res.ok) {
          const list = await res.json();
          const p = list.find(x => x.type === 'page');
          if (p && p.webSocketDebuggerUrl) { wsUrl = p.webSocketDebuggerUrl; break; }
        }
      } catch (e) {}
      await new Promise(r => setTimeout(r, 200));
    }

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

    await send('Page.enable');
    await new Promise(r => setTimeout(r, 1200));

    for (let idx = 0; idx < STAGES.length; idx++) {
      const stageHtml = JSON.stringify(STAGES[idx]);
      await send('Runtime.evaluate', {
        expression: `document.getElementById('term').innerHTML = ${stageHtml};`
      });
      await new Promise(r => setTimeout(r, 300));
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      const fpath = path.join(FRAMES_DIR, `frame_${idx}.png`);
      fs.writeFileSync(fpath, Buffer.from(shot.data, 'base64'));
      console.log(`Captured complex frame ${idx}: ${fpath}`);
    }

    ws.close();
  } finally {
    chrome.kill();
    server.close();
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});

/* Execution and its results: the right rail's Changes/Tasks/Checks cards, the diff painter behind
   them, the file viewer sheet, and the Activity log with the raw stream under it.
   Every command shown here was run by the server; nothing in this file executes or applies. */
function renderRail() {
  const rail = $('rail'); rail.innerHTML = '';
  const a = DATA.artifact || {};
  const r = DATA.review || {};

  if (!state.railSection) {
    state.railSection = (DATA.plan && (!r.files || !r.files.length)) ? 'tasks' : 'changes';
  }

  const rtabs = el('div', 'rail-tabs');
  const sections = [
    ['changes', 'Changes', (r.files || []).length ? (r.files || []).length : ''],
    ['tasks', 'Tasks', DATA.plan ? `${DATA.plan.step}/${DATA.plan.total}` : ''],
    ['checks', 'Checks & Sources', ''],
  ];
  for (const [id, label, count] of sections) {
    const active = state.railSection === id;
    const btn = el('button', 'rail-tab' + (active ? ' on' : ''),
      `<span>${label}</span>`
      + (count ? `<span class="tab-badge">${count}</span>` : '')
      + (!active && state.unread && state.unread[id] ? '<span class="unread-dot"></span>' : ''));
    btn.onclick = () => {
      state.railSection = id;
      if (state.unread) state.unread[id] = false;
      if (id !== 'changes') state.railFile = -1;
      renderRail();
    };
    rtabs.appendChild(btn);
  }
  rail.appendChild(rtabs);

  if (state.railSection === 'changes') {
    if (state.railFile >= 0 && r.files && r.files[state.railFile]) {
      rail.appendChild(railPreviewCard(r));
    } else {
      const art = el('div', 'card');
      art.innerHTML = (DATA.banner && DATA.banner.text
        ? `<div class="auto-note">${esc(DATA.banner.text)}</div>` : '') +
        `<span class="state${tone(a.tone)}">● ${esc(a.state)}</span>
        <div class="t">${esc(a.title)}</div><div class="d">${esc(a.detail)}</div>`;
      if ((r.files || []).length) {
        const list = el('div', 'rail-files');
        r.files.forEach((f, i) => {
          const row = el('button', 'rfile',
            kindTag(f.kind) + fileCaption(f, true) + diffStat(f));
          row.onclick = () => openFile(i);
          list.appendChild(row);
        });
        art.appendChild(list);
        art.appendChild(el('div', 'hr'));
        changeActions(r, art);
      } else {
        art.appendChild(el('div', 'pv-empty-files quiet', 'No file changes proposed or written yet.'));
        art.appendChild(el('div', 'hr'));
        changeActions(r, art);
      }
      rail.appendChild(art);
    }
  } else if (state.railSection === 'tasks') {
    if (DATA.plan) {
      const p = el('div', 'card tasks-card');
      const total = DATA.plan.total || 0;
      const verified = DATA.plan.verified || 0;
      const steps = DATA.plan.steps || [];
      const needingAttention = steps.filter(s => s.status === 'failed' || s.status === 'rejected' || s.status === 'needs_review' || (s.current && DATA.artifact && DATA.artifact.state === 'VERIFICATION_FAILED')).length;
      const pct = Math.round((verified / Math.max(1, total)) * 100);
      p.innerHTML = `<h5>Plan · step-by-step</h5><div class="t" style="font-size:12.5px">${esc(DATA.plan.name)} — Step ${DATA.plan.step} of ${total} · ${verified} verified · ${needingAttention} needing attention</div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="meta"><span>${verified} verified</span><span>${needingAttention ? needingAttention + ' needing attention' : esc(DATA.plan.note)}</span></div><div class="hr"></div>`;
      const seqDiv = el('div', 'plan-seq-controls');
      if (!state.sequential) {
        const startBtn = el('button', 'solid plan-seq-btn', '▶ Start sequential');
        const complete = steps.length > 0 && steps.every(s => s.status === 'verified');
        startBtn.disabled = complete || steps.length === 0;
        startBtn.title = complete ? 'Plan complete — all steps are verified' : 'Run plan steps sequentially';
        startBtn.onclick = () => startSequential();
        seqDiv.appendChild(startBtn);
      } else {
        const stopBtn = el('button', 'line-btn plan-seq-btn running', '■ Stop sequential');
        stopBtn.title = 'Stop sequential execution';
        stopBtn.onclick = () => stopSequential();
        seqDiv.appendChild(stopBtn);
      }
      p.appendChild(seqDiv);
      (DATA.resume || []).slice(0, 3).forEach(row => {
        const strip = el('div', 'plan-resume');
        strip.appendChild(el('span', 'quiet', '⏸ ' + esc(row.task)
                                + (row.turn ? ` · turn ${row.turn}` : '')));
        const btn = el('button', 'line-btn', 'Resume');
        btn.title = 'Continue this task from where it stopped';
        btn.onclick = () => send('resume_task', { run_id: row.run_id });
        strip.appendChild(btn);
        p.appendChild(strip);
      });
      if (DATA.plan.goal) {
        const strings = DATA.plan.strings || {};
        const goal = el('div', 'plan-goal');
        goal.appendChild(el('div', 't', '🎯 ' + esc(strings.goal || 'Goal') + ': ' + esc(DATA.plan.goal)));
        const crit = DATA.plan.verdicts || [];
        if (crit.length) {
          goal.appendChild(el('div', 't quiet', esc(strings.criteria || 'Acceptance criteria')));
          const shown = el('div', 'plan-criteria');
          crit.forEach((row) => {
            const line = el('div', 'plan-criterion ' + esc(row.verdict));
            // The words are the server's, in the language the task was asked in, and the row opens with a
            // criterion number — a weak character that cannot decide a paragraph direction. Without this an
            // Arabic criterion sits in an LTR line and its punctuation ends up on the wrong side.
            line.dir = 'auto';
            line.innerHTML = `<b>${esc(String(row.number))}</b> ${esc(row.text)}`
              + ` <span class="crit-v v-${esc(row.verdict)}">${esc(row.word)}</span>`
              + (row.steps.length ? ` <span class="quiet">· steps ${esc(row.steps.join(', '))}</span>` : '')
              + (row.why ? ` <span class="quiet">· ${esc(row.why)}</span>` : '');
            shown.appendChild(line);
          });
          goal.appendChild(shown);
          if (DATA.plan.verdictNote) {
            const note = el('div', 'quiet plan-verdict-note', esc(DATA.plan.verdictNote));
            note.dir = 'auto';
            goal.appendChild(note);
          }
        }
        p.appendChild(goal);
      }
      const list = el('div', 'tasks-list');
      for (const s of DATA.plan.steps) {
        const isDone = s.status === 'verified';
        const isNow = !isDone && (s.current || s.id === DATA.plan.step);
        const row = el('div', 'task-item ' + (isDone ? 'done' : isNow ? 'now' : 'pending'));
        row.appendChild(el('span', 'task-status-icon', isDone ? '✓' : isNow ? '⏳' : String(s.id)));
        row.appendChild(el('span', 'task-title', esc(s.title)));
        if ((s.accepts || []).length) {
          row.appendChild(el('span', 'task-crit quiet', '▸ ' + s.accepts.join(',')));
        }
        if (s.unproven) {
          const badge = el('span', 'task-unproven', '!');
          badge.title = (DATA.plan.strings || {}).unproven || 'unproven';
          row.appendChild(badge);
        }
        if (!isDone) {
          const exec = el('button', 'step-exec-btn', 'Run step ▶');
          exec.title = 'Run this step in chat';
          exec.onclick = (e) => { e.stopPropagation(); runPlanStep(s); };
          row.appendChild(exec);
        }
        list.appendChild(row);
      }
      p.appendChild(list);
      rail.appendChild(p);
    } else {
      const empty = el('div', 'card empty-tasks-card');
      empty.innerHTML = `<div class="empty-icon">📋</div>
        <div class="t">No active plan</div>
        <div class="d">Attach a markdown or text plan to execute steps one-by-one.</div>`;
      const attachBtn = el('button', 'line-btn', '＋ Attach Plan');
      attachBtn.style.marginTop = '12px';
      attachBtn.onclick = () => send('pick_plan');
      empty.appendChild(attachBtn);
      rail.appendChild(empty);
    }
  } else if (state.railSection === 'checks') {
    const rs = DATA.runStatus || {};
    const cfg = DATA.runConfig || {};
    const many = (DATA.targets || []).length > 1;
    const sb = DATA.sandbox || {};
    const pl = DATA.policy || {};
    const folderName = (DATA.project && DATA.project.name) || 'Project';

    const c = el('div', 'card');
    const appCmd = (cfg.app && cfg.app.command) || 'npm start';
    const testCmd = (DATA.recipe) || (cfg.test && cfg.test.command) || 'npm test';
    const buildCmd = (cfg.build && cfg.build.command) || 'npm run build';
    const currentCmd = state.runActionChoice === 'app' ? appCmd : state.runActionChoice === 'build' ? buildCmd : testCmd;

    c.innerHTML = `<h5>Run &amp; Checks</h5>
      <div class="run-action-bar">
        <button class="run-act-btn ${state.runActionChoice === 'app' ? 'primary' : ''}" id="run-app" ${rs.canRunApp ? '' : 'disabled'}>▶ Run App</button>
        <button class="run-act-btn ${state.runActionChoice !== 'app' && state.runActionChoice !== 'build' ? 'primary' : ''}" id="run" ${rs.canRunTests ? '' : 'disabled'}>🧪 Run Tests</button>
        <button class="run-act-btn ${state.runActionChoice === 'build' ? 'primary' : ''}" id="run-build" ${rs.canBuild ? '' : 'disabled'}>🔨 Build</button>
      </div>
      <div class="cmd-preview-box" title="Command that will be executed">
        <span>📁 <b>${esc(folderName)}</b>: <code style="font-size:11px">${esc(currentCmd)}</code></span>
      </div>
      ${rs.disabledMessage ? `<div class="disabled-banner"><b>⚠️</b><span>${esc(rs.disabledMessage)}</span></div>` : ''}
      ${many ? `<button class="pill" id="target" style="width:100%;justify-content:space-between;margin-top:8px">${esc(DATA.targetLabel || 'choose a module')}${ICON.chev}</button>` : ''}
      ${DATA.recipes && DATA.recipes.length ? `<button class="pill" id="recipe" style="width:100%;justify-content:space-between;margin-top:7px">${esc(DATA.recipe || 'choose a test recipe')}${ICON.chev}</button>` : ''}
      
      <div class="row" style="margin-top:9px">
        <button class="line-btn" style="flex:1" id="fix">🔧 Run &amp; Fix</button>
        <button class="line-btn" style="flex:1" id="readiness-btn">🔍 Readiness</button>
        <button class="line-btn" id="run-config-btn" title="Edit run settings">⚙</button>
      </div>

      <label class="switch" style="margin-top:10px">
        <input type="checkbox" id="sandboxOn" ${sb.on ? 'checked' : ''} ${sb.available ? '' : 'disabled'}> Run in Docker (optional)
      </label>
      <input id="sandboxImage" placeholder="image@sha256:…" value="${esc(sb.image || '')}" dir="ltr"
        ${sb.on && sb.available ? '' : 'disabled'} style="width:100%;font-family:Consolas,monospace;margin-top:5px">
      <div class="meta" dir="auto">${esc(sb.note || rs.dockerNote || '')}</div>
      ${DATA.fixRounds && DATA.fixRounds.spent ? `<div class="meta" style="margin-top:7px"><span>Fix round ${Number(DATA.fixRounds.spent) || 0} of ${Number(DATA.fixRounds.of) || 0}</span></div>` : ''}
      ${(pl.rows || []).length ? `<details class="policy-card"><summary dir="auto">${esc(pl.heading || '')}</summary>
        ${pl.rows.map(r => `<div class="policy-row" dir="auto"><span class="p-act">${esc(r.action)}</span>
          ${r.editable ? `<span class="p-ver${r.declared ? ' set' : ''}">${esc((pl.words || {})[r.verdict] || r.verdict)}</span>
          <span class="p-btns">${['allow', 'ask', 'deny'].map(v =>
            `<button class="line-btn p-set" data-a="${esc(r.action)}" data-v="${v}" ${r.verdict === v ? 'disabled' : ''}>${esc((pl.words || {})[v] || v)}</button>`).join('')}
            ${r.declared ? `<button class="line-btn p-clear" data-a="${esc(r.action)}" title="reset">↺</button>` : ''}</span>`
            : `<span class="p-managed">${esc(r.managed || '')}</span>`}
        </div>`).join('')}
        <div class="meta" dir="auto">${esc(pl.note || '')}</div>
        <div class="meta" dir="auto">${esc(pl.lift || '')}</div></details>` : ''}
      ${DATA.runInfo ? `<div class="d" style="margin-top:9px">${esc(DATA.runInfo)}</div>` : ''}`;

    c.querySelector('#run-app').onclick = () => {
      state.runActionChoice = 'app';
      send('run_app');
    };
    c.querySelector('#run').onclick = () => {
      state.runActionChoice = 'tests';
      send('run', { fix: false });
    };
    c.querySelector('#run-build').onclick = () => {
      state.runActionChoice = 'build';
      send('run_build');
    };
    c.querySelector('#fix').onclick = () => {
      state.runActionChoice = 'tests';
      send('run', { fix: true });
    };
    c.querySelector('#readiness-btn').onclick = () => readinessDrawer();
    c.querySelector('#run-config-btn').onclick = () => runConfigDrawer();
    if (c.querySelector('#recipe')) {
      c.querySelector('#recipe').onclick = () => choose('recipe', DATA.recipe, DATA.recipes);
    }
    if (many && c.querySelector('#target')) {
      c.querySelector('#target').onclick = () => choose('target', DATA.targetLabel, DATA.targets.map(row => row.label));
    }
    c.querySelector('#sandboxOn').onchange = (e) => send('sandbox', { on: e.target.checked });
    c.querySelector('#sandboxImage').onchange = (e) => send('sandbox', { image: e.target.value });
    // The action and the verdict travel as data attributes, never as text read back off the row: a
    // label is a sentence and a sentence changes with the language, while this has to name a class.
    c.querySelectorAll('.p-set').forEach(b => {
      b.onclick = () => send('set_policy', { action: b.dataset.a, verdict: b.dataset.v });
    });
    c.querySelectorAll('.p-clear').forEach(b => {
      b.onclick = () => send('set_policy', { action: b.dataset.a });
    });
    rail.appendChild(c);

    // Active Service or Last Job Failure Card
    const lastJob = DATA.lastJob;
    const activeSvc = (DATA.services || []).find(s => s.status === 'failed') || (DATA.services || [])[0];
    const diag = (lastJob && lastJob.diagnosis && lastJob.diagnosis.kind !== 'none') ? lastJob.diagnosis : (activeSvc && activeSvc.diagnosis && activeSvc.diagnosis.kind !== 'none' ? activeSvc.diagnosis : null);
    if (diag) {
      const fixCard = el('div', 'fix-card');
      const isBatch = DATA.repairBatch && DATA.repairBatch.status === 'in_progress';
      fixCard.innerHTML = `<div class="summary">❌ ${esc(diag.summary)}</div>
        <div class="suggestion">${esc(diag.suggestion || '')}</div>
        <div style="display:flex;gap:6px;margin-top:4px">
          ${isBatch ? `<div class="muted" style="align-self:center">Attempt ${DATA.repairBatch.current_attempt} of ${DATA.repairBatch.max_attempts} in progress…</div>
          <button class="fix-card-btn" id="fix-stop-btn" style="background:var(--bad)">⏹ Stop</button>` : `
          <button class="fix-card-btn" id="fix-btn-single">🔧 Fix (1 attempt)</button>
          <button class="fix-card-btn" id="fix-btn-batch" style="background:var(--accent-hover)">⚡ Fix batch (up to 3)</button>`}
        </div>`;
      if (isBatch) {
        fixCard.querySelector('#fix-stop-btn').onclick = () => send('stop');
      } else {
        fixCard.querySelector('#fix-btn-single').onclick = () => {
          send('fix_errors', { source: (lastJob ? lastJob.type : 'service'), diagnosis: diag, output: (lastJob ? lastJob.output : ''), batch_approved: false });
        };
        fixCard.querySelector('#fix-btn-batch').onclick = () => {
          send('fix_errors', { source: (lastJob ? lastJob.type : 'service'), diagnosis: diag, output: (lastJob ? lastJob.output : ''), batch_approved: true, max_attempts: 3 });
        };
      }
      rail.appendChild(fixCard);
    }

    // App Preview Card
    const readySvc = (DATA.services || []).find(s => s.ready && s.url);
    if (readySvc) {
      const prevCard = el('div', 'card preview-card');
      prevCard.innerHTML = `<h5>App Preview</h5>
        <div class="preview-row">
          <span>🟢 App Ready: <a class="preview-url" href="${esc(readySvc.url)}" target="_blank">${esc(readySvc.url)}</a></span>
          <button class="line-btn" id="btn-open-preview" style="font-size:11px">Open ↗</button>
        </div>
        <div class="api-tester">
          <div style="font-size:11.5px;font-weight:600;display:flex;justify-content:space-between">
            <span>API Request Tester</span>
          </div>
          <div class="api-inputs">
            <select class="api-method" id="api-method">
              <option value="GET">GET</option>
              <option value="POST">POST</option>
              <option value="PUT">PUT</option>
              <option value="DELETE">DELETE</option>
            </select>
            <input class="api-url-input" id="api-url" value="${esc(readySvc.url)}/">
            <button class="line-btn" id="btn-api-send" style="font-size:11px">Send</button>
          </div>
          <div class="api-response-box hidden" id="api-resp-box"></div>
        </div>`;
      prevCard.querySelector('#btn-open-preview').onclick = () => window.open(readySvc.url, '_blank');
      prevCard.querySelector('#btn-api-send').onclick = async () => {
        const method = prevCard.querySelector('#api-method').value;
        const url = prevCard.querySelector('#api-url').value;
        const box = prevCard.querySelector('#api-resp-box');
        box.classList.remove('hidden');
        box.textContent = 'Sending request…';
        try {
          const res = await api('/api/action', { type: 'api_test', method, url });
          const r = res && res.result;
          if (r) {
            box.textContent = `Status: ${r.status} (${r.duration}s)\n\n${r.body || '(empty response)'}`;
          } else {
            box.textContent = 'No response received.';
          }
        } catch (err) {
          box.textContent = 'Error: ' + err.message;
        }
      };
      rail.appendChild(prevCard);
    }

    // Custom Terminal Command Section
    if (DATA.project) {
      const customCard = el('div', 'card custom-cmd-card');
      customCard.innerHTML = `<h5>Run custom command</h5>
        <div class="custom-cmd-row">
          <input class="custom-cmd-input" id="custom-cmd-input" placeholder="e.g. mvn clean, npm run lint" />
          <input class="custom-cmd-sub" id="custom-cmd-sub" placeholder="subdir (opt)" title="Subdirectory within project (optional)" />
          <button class="run-act-btn primary" id="custom-cmd-run" style="flex:none;padding:6px 12px">▶ Run</button>
          <button class="icon-btn" id="custom-cmd-fav" title="Save as favorite" style="flex:none">★</button>
        </div>
        <div class="cmd-chips" id="custom-cmd-chips"></div>`;
      const inp = customCard.querySelector('#custom-cmd-input');
      const subInp = customCard.querySelector('#custom-cmd-sub');
      const runBtn = customCard.querySelector('#custom-cmd-run');
      const favBtn = customCard.querySelector('#custom-cmd-fav');
      const chipsDiv = customCard.querySelector('#custom-cmd-chips');
      
      const history = (DATA.cmdHistory && DATA.cmdHistory.history) || [];
      const favorites = (DATA.cmdHistory && DATA.cmdHistory.favorites) || [];
      favorites.forEach(fav => {
        const chip = el('button', 'cmd-chip', `★ ${esc(fav.name || fav.command)}`);
        chip.onclick = () => { inp.value = fav.command; subInp.value = fav.subdir || ''; };
        chipsDiv.appendChild(chip);
      });
      history.slice(0, 3).forEach(h => {
        const chip = el('button', 'cmd-chip', esc(h.command));
        chip.onclick = () => { inp.value = h.command; subInp.value = h.subdir || ''; };
        chipsDiv.appendChild(chip);
      });

      runBtn.onclick = () => {
        const cmd = inp.value.trim();
        if (!cmd) return toast('Please enter a command to run');
        send('run_custom', { command: cmd, subdir: subInp.value.trim() });
      };
      favBtn.onclick = () => {
        const cmd = inp.value.trim();
        if (!cmd) return toast('Please enter a command first');
        const name = prompt('Favorite name:', cmd);
        if (name) send('save_favorite_cmd', { command: cmd, subdir: subInp.value.trim(), name });
      };
      rail.appendChild(customCard);
    }

    // Terminal Panel
    if ((DATA.services && DATA.services.length) || lastJob) {
      const termCard = el('div', 'card terminal-card');
      const termSvc = (DATA.services || [])[0];
      const title = termSvc ? `Service: ${termSvc.name}` : (lastJob ? `Output: ${lastJob.type}` : 'Terminal');
      const statusBadge = termSvc ? termSvc.status : (lastJob ? (lastJob.success ? 'passed' : 'failed') : 'ready');
      const isRunning = termSvc && (termSvc.status === 'starting' || termSvc.status === 'ready' || termSvc.status === 'running');

      termCard.innerHTML = `<div class="terminal-head">
        <div class="terminal-title">
          <i class="dot ${isRunning ? 'run' : statusBadge === 'passed' || statusBadge === 'ready' ? 'ok' : 'bad'}"></i>
          <span>${esc(title)}</span>
        </div>
        <div class="terminal-actions">
          ${(lastJob && !lastJob.success) ? `<button id="term-debug" style="color:var(--bad-ink);background:var(--bad-bg)">🔍 Debug</button>` : ''}
          ${isRunning ? `<button id="term-stop" title="Stop service">⏹ Stop</button>` : ''}
          ${termSvc ? `<button id="term-restart" title="Restart service">↻ Restart</button>` : ''}
          <button id="term-copy" title="Copy output log">📋 Copy</button>
        </div>
      </div>
      <div class="terminal-box" id="term-box">
        <pre style="margin:0;font-family:inherit;font-size:inherit;white-space:pre-wrap">${esc(lastJob && lastJob.output ? lastJob.output : (state.terminalLog || 'Waiting for output…'))}</pre>
        <button class="terminal-jump-btn" id="term-jump">↓ Latest</button>
      </div>`;

      if (termCard.querySelector('#term-debug')) {
        termCard.querySelector('#term-debug').onclick = () => {
          send('diagnose_terminal', { output: lastJob.output, command: lastJob.command, exit_code: lastJob.exit_code, cwd: lastJob.cwd });
        };
      }
      if (isRunning) {
        termCard.querySelector('#term-stop').onclick = () => send('stop_app', { id: termSvc.id });
      }
      if (termSvc) {
        termCard.querySelector('#term-restart').onclick = () => send('restart_app', { id: termSvc.id });
      }
      termCard.querySelector('#term-copy').onclick = () => {
        const text = termCard.querySelector('pre').textContent;
        navigator.clipboard.writeText(text);
        toast('Terminal log copied to clipboard');
      };

      const termBox = termCard.querySelector('#term-box');
      const jumpBtn = termCard.querySelector('#term-jump');
      termBox.addEventListener('scroll', () => {
        if (termBox.scrollHeight - termBox.scrollTop - termBox.clientHeight > 80) {
          jumpBtn.classList.add('show');
        } else {
          jumpBtn.classList.remove('show');
        }
      });
      jumpBtn.onclick = () => {
        termBox.scrollTop = termBox.scrollHeight;
        jumpBtn.classList.remove('show');
      };

      termBox.addEventListener('mouseup', () => {
        const sel = window.getSelection().toString().trim();
        if (sel && sel.length > 5) {
          state.selectedTerminalText = sel;
          if (!termCard.querySelector('#term-analyze-btn')) {
            const analyzeBtn = el('button', 'solid', '🔎 Analyze selection');
            analyzeBtn.id = 'term-analyze-btn';
            analyzeBtn.style.fontSize = '10.5px';
            analyzeBtn.style.padding = '2px 7px';
            analyzeBtn.onclick = () => {
              send('diagnose_terminal', { output: state.selectedTerminalText, is_selection: true });
            };
            termCard.querySelector('.terminal-actions').prepend(analyzeBtn);
          }
        }
      });

      // Completion Summary Box
      if (lastJob) {
        const summary = el('div', 'exec-summary');
        const outcome = lastJob.success ? '✅ Passed' : '❌ Failed';
        summary.innerHTML = `<div><b>Execution Summary:</b> ${outcome} (exit ${lastJob.exit_code}, ${lastJob.duration}s)</div>
          <div><b>Ran:</b> <span class="mono">${esc(lastJob.command || lastJob.type)}</span></div>
          <div><b>Working Dir:</b> <span class="mono">${esc(lastJob.cwd || '.')}</span></div>
          <div class="step-next"><b>Next Step:</b> ${lastJob.success ? 'Service/build verified. You may proceed with testing or code changes.' : 'Check the diagnosis card below or click "Prepare fix" to repair.'}</div>`;
        termCard.appendChild(summary);
      }

      rail.appendChild(termCard);
    }

    // On-demand Terminal Diagnosis Card
    if (DATA.diagnosis) {
      const diagData = DATA.diagnosis;
      const diagCard = el('div', 'card diag-card');
      diagCard.innerHTML = `<div class="diag-head">
        <span>🔍 Diagnosis: ${esc(diagData.what_failed)}</span>
        <span class="muted" style="font-size:11px">${diagData.is_selection ? 'Selection' : 'Exit ' + diagData.exit_code}</span>
      </div>
      <div class="diag-sec"><b>Likely Cause:</b> ${esc(diagData.likely_cause)}</div>
      <div class="diag-sec"><b>Evidence:</b> <span class="mono">${esc(diagData.evidence)}</span></div>
      ${diagData.facts && diagData.facts.length ? `<div class="diag-sec"><b>Confirmed Facts:</b><ul style="margin:2px 0 0 16px">${diagData.facts.map(f => `<li>${esc(f)}</li>`).join('')}</ul></div>` : ''}
      ${diagData.uncertainties && diagData.uncertainties.length ? `<div class="diag-sec"><b>Uncertainties:</b><ul style="margin:2px 0 0 16px">${diagData.uncertainties.map(u => `<li>${esc(u)}</li>`).join('')}</ul></div>` : ''}
      <div class="diag-sec"><b>Practical Solutions:</b>
        ${(diagData.solutions || []).map(s => `<div class="diag-sol-item">${s.order}. <b>${esc(s.action)}</b>: ${esc(s.suggestion)}</div>`).join('')}
      </div>
      <div class="diag-acts">
        <button class="primary" id="diag-prep-fix">🔧 Prepare fix</button>
        <button id="diag-run-again">↻ Run again</button>
        <button id="diag-copy">📋 Copy diagnosis</button>
      </div>`;
      
      diagCard.querySelector('#diag-prep-fix').onclick = () => {
        const text = `Please investigate and fix the following issue:\nCommand: ${diagData.command}\nCause: ${diagData.likely_cause}\nEvidence: ${diagData.evidence}`;
        const promptEl = $('prompt');
        if (promptEl) {
          promptEl.value = text;
          promptEl.focus();
        }
        toast('Fix instructions copied to message prompt');
      };
      diagCard.querySelector('#diag-run-again').onclick = () => {
        if (diagData.command) send('run_custom', { command: diagData.command, subdir: '' });
      };
      diagCard.querySelector('#diag-copy').onclick = () => {
        navigator.clipboard.writeText(JSON.stringify(diagData, null, 2));
        toast('Diagnosis copied to clipboard');
      };
      rail.appendChild(diagCard);
    }

    const s = el('div', 'card');
    s.innerHTML = `<h5>Sources</h5>
      <div class="link quiet">${ICON.file} ${esc(DATA.project ? 'Project: ' + DATA.project.name : 'This chat has no project')}</div>
      <button class="link" id="notes">🧾 Project notes <span class="r">${esc(DATA.memory.info || '')}</span></button>
      <div class="hr"></div>
      <button class="link" id="settings">${ICON.gear} Settings <span class="r">›</span></button>`;
    s.querySelector('#notes').onclick = () => openSettings('notes');
    s.querySelector('#settings').onclick = () => openSettings();
    if ((DATA.branch || {}).key) {
      const group = (DATA.projects || []).find((g) => g.key === DATA.branch.key);
      const info = el('button', 'link', '📁 Project settings & status <span class="r">›</span>');
      info.onclick = () => projectDrawer(group || { key: DATA.branch.key, name: DATA.project.name, path: DATA.project.path });
      s.insertBefore(info, s.querySelector('.hr'));
      const graph = el('button', 'link', '🕸️ Dependency graph <span class="r">›</span>');
      graph.onclick = () => graphSheet();
      s.insertBefore(graph, s.querySelector('.hr'));
    }
    rail.appendChild(s);
  }
}

/* Unified diff lines carry no line numbers of their own; the @@ -old,+new @@ header does.
   Counting array positions instead numbered the header lines and drifted from the file. */
function diffRows(lines) {
  let oldLine = 0, newLine = 0;
  return lines.map((line) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) { oldLine = +hunk[1]; newLine = +hunk[2]; return { cls: 'hunk', ln: '', tx: line }; }
    if (line.startsWith('---') || line.startsWith('+++')) return { cls: 'hunk', ln: '', tx: line };
    if (line.startsWith('+')) return { cls: 'add', ln: newLine++, tx: line };
    if (line.startsWith('-')) return { cls: 'del', ln: oldLine++, tx: line };
    oldLine += 1; newLine += 1;
    return { cls: '', ln: newLine - 1, tx: line };
  });
}

/* One diff painter, two surfaces: the rail preview and the sheet it shares its markup with. They
   used to be the same loop inlined into a pane, which left the rail with nothing to draw without
   leaving Chat. */
function paintDiff(host, lines) {
  host.innerHTML = '';
  diffRows(lines).forEach((row) => {
    const node = el('div', 'dline ' + row.cls,
      `<span class="ln">${row.ln === '' ? '' : row.ln}</span><span class="tx">${esc(row.tx) || ' '}</span>`);
    node.dir = 'auto';
    host.appendChild(node);
  });
}

/* Whole-file contents, numbered. The stored `after` is the file as this task wrote it and `before`
   is what the task replaced, so the preview never needs to read the disk to show a file. */
function paintCode(host, lines) {
  host.innerHTML = '';
  lines.forEach((tx, i) => {
    const node = el('div', 'dline', `<span class="ln">${i + 1}</span><span class="tx">${esc(tx) || ' '}</span>`);
    node.dir = 'auto';
    host.appendChild(node);
  });
}

/* A chip and a rail row both mean "show me this file", and both are answered by the side viewer.
   Below 1180px the rail is display:none, so the same card opens in a sheet laid over the chat: the
   pane that used to catch these clicks is gone, and reading a change never leaves Chat. */
function openFile(index) {
  const r = DATA.review || {};
  /* A chip that is no longer in the change set means the page is behind the server, so the click
     has to say that rather than return silently — a silent no-return is what made this look like a
     broken button. */
  if (!r.files || !r.files[index]) { toast('That file is no longer in the change set.', 'bad'); return; }
  state.railSection = 'changes';
  if (state.unread) state.unread.changes = false;
  state.railFile = index;
  state.railTab = 'diff';
  // The reply carries this file's diff, so the viewer is drawn only once the server has answered.
  api('/api/action', { type: 'select_file', index })
    .then((reply) => {
      if (reply && reply.state) render(reply.state);   // the rail branch repaints itself here
      if (railHidden()) viewerSheet();
    })
    .catch((e) => toast(String(e.message || e), 'bad'));
}

function railHidden() { return getComputedStyle($('rail')).display === 'none'; }

/* The one change viewer, on two surfaces. SHEET is non-null only while a sheet is open, and it is
   what the preview's tab buttons repaint into — a sheet that kept showing the last file after a tab
   click would be the pane's old bug wearing a scrim. */
let SHEET = null;
let SETTINGS = null;

/* A profile or a provider change moves the connection row underneath an open tab: the endpoint, the
   key variable and whether approval is needed all belong to the server. Only the Connection tab is
   repainted, because the others hold text the user is typing and a snapshot must not eat it. */
function connectionSignature() {
  const c = DATA.connection || {};
  return [c.kind, c.endpoint, c.profile, DATA.provider.mode, DATA.provider.model].join('|');
}

/* The rows are the server's, so a save that changed them has to repaint the tab that lists them.
   The value field survives the repaint for the same reason the key field does: the next row may be
   half-typed while this one lands. */
function overrideSignature() {
  const o = DATA.overrides || {};
  return (o.rows || []).map((r) => [r.target, r.key, r.value, r.state].join(':')).join('|');
}

function syncSettings() {
  if (!SETTINGS || (SETTINGS.tab !== 'connection' && SETTINGS.tab !== 'overrides')) return;
  if (SETTINGS.signature === connectionSignature() + overrideSignature()) return;
  // Only the key field is text the server never echoes back. The value box is not preserved on
  // purpose: this repaint runs when a row actually landed, and a box still holding the value that
  // just saved is a box waiting to save it twice. A refused row changes nothing, so no repaint runs.
  const typed = document.querySelector('#key');
  const keep = typed ? typed.value : '';
  SETTINGS.show(SETTINGS.tab);
  const again = document.querySelector('#key');
  if (again) again.value = keep;
  SETTINGS.signature = connectionSignature() + overrideSignature();
}

function refreshPreview() {
  if (!SHEET) { renderRail(); return; }
  SHEET.host.innerHTML = '';
  SHEET.host.appendChild(railPreviewCard(DATA.review || {}));
}

function viewerSheet() {
  if (SHEET) { refreshPreview(); return; }
  const r = DATA.review || {};
  // The header is the server's own task line and file count, so the sheet is a labelled dialog.
  const box = sheet(r.title, r.detail);
  box.classList.add('wide');
  const host = el('div', 'content pv-sheet');
  box.appendChild(host);
  const close = modal(box, () => { SHEET = null; state.railFile = -1; renderRail(); });
  SHEET = { host: host, close: close };
  refreshPreview();
}

/* The server's "come and look" nudge — a proposal became ready, a chat block became a proposal, a
   fix round produced one. It used to switch the whole window to the Changes pane; with the pane gone
   the same intent opens the side viewer on the newest file and leaves the user on the screen they
   were reading. `asClick` is the deep link, where opening the sheet is the point of the URL; an
   arriving proposal must not pop a dialog over somebody's typing. */
function showView(value, asClick) {
  if (value !== 'review' && value !== 'preview') { switchView(value); return; }
  state.previewWant = asClick ? 'sheet' : 'rail';
  takePreviewOffer();
}

/* The event is emitted by the worker before the snapshot that carries the proposal, so the intent is
   recorded and taken up by the next render — otherwise it would open the viewer on the last task's
   file list. */
function takePreviewOffer() {
  if (!state.previewWant) return;
  const want = state.previewWant;
  state.previewWant = '';
  const r = DATA && DATA.review;
  if (!r || !r.files || !r.files.length) return;
  state.railFile = 0;
  state.railTab = 'diff';
  if (want === 'sheet' && railHidden()) viewerSheet(); else renderRail();
}

/* Three fates for a file, one badge each. `D` has to be visibly its own thing: a chip that reads
   "~ Modified" over a removal is the card lying about the one change you cannot read back. */
function kindTag(kind) {
  if (kind === 'A') return '<span class="chip-tag created">+ Created</span>';
  if (kind === 'D') return '<span class="chip-tag deleted">− Removed</span>';
  return '<span class="chip-tag modified">~ Modified</span>';
}

/* How much goes in and how much comes out. The controller already computes both for every change in
   the set (`review.files[].add` / `.del`) and the preview header has been drawing them for one file
   at a time; on a card that lists four files the numbers are the whole reason to read the list
   before opening anything. */
function diffStat(f) {
  return `<span class="dstat"><span class="p">+${(f && f.add) || 0}</span>`
    + `<span class="n">−${(f && f.del) || 0}</span></span>`;
}

function diffTotals(files) {
  let add = 0, del = 0;
  for (const f of (files || [])) { add += f.add || 0; del += f.del || 0; }
  return { add, del };
}

function fileCaption(f, rail = false) {
  const summary = f.summary || f.description || `${f.kind === 'A' ? 'Created' : f.kind === 'D' ? 'Removed' : 'Updated'} file content`;
  return `<span class="file-caption"><${rail ? 'code' : 'span'} class="chip-name">${esc(f.path)}</${rail ? 'code' : 'span'}>`
    + `<span class="file-summary" dir="auto" title="${esc(summary)}">${esc(summary)}</span></span>`;
}

/* What this proposal breaks in the files it does not touch. Every sentence is built server-side from
   the symbol index and the task's own language, so this draws textContent only: a finding names paths
   and identifiers that came out of the repository, and a card that approved a change was never a
   place to let that markup through. */
function impactBlock(r) {
  const imp = r.impact || {};
  const lines = imp.lines || [];
  if (!lines.length) return null;
  const box = el('div', 'impact-block');
  box.dir = 'auto';
  const head = el('div', 'impact-head');
  head.textContent = imp.heading || '';
  box.appendChild(head);
  for (const line of lines) {
    const row = el('div', 'impact-line');
    row.textContent = line;
    box.appendChild(row);
  }
  return box;
}

function chipCard(r) {
  const card = el('div', 'chat-task-card');
  card.dir = 'auto';
  /* The state line is the server's, so an Arabic task reads Arabic here and English there is
     only ever the chrome: `+ Created` names a badge, not a sentence. */
  const a = DATA.artifact || {};
  const auto = DATA.banner && DATA.banner.text;
  card.title = a.title || '';
  card.appendChild(el('div', 'chat-task-head',
    `<span class="state${tone(a.tone)}">● ${esc(a.state)}</span>`
    /* A write nobody clicked for has to be legible on the card itself, not only in the amber note
       beside the rail's file list — this is the card that sits in the conversation, and the
       conversation is where the operator was standing when the files changed. */
    + (auto ? '<span class="chip-tag auto">⚡ Auto-Applied</span>' : '')
    + `<span class="file-count">${r.files.length} ${r.files.length === 1 ? 'file' : 'files'} changed</span>`
    + diffStat(diffTotals(r.files))));
  const row = el('div', 'chat-file-chips');
  const key = r.id || JSON.stringify(r.files.map(f => f.path));
  const expanded = state.expandedProposal === key;
  r.files.slice(0, expanded ? r.files.length : 3).forEach((f, i) => {
    const chip = el('button', 'file-chip',
      `<span class="chip-icon">${ICON.file}</span>` + fileCaption(f)
      + kindTag(f.kind) + diffStat(f)
      + `<span class="chip-action">↗ View diff</span>`);
    chip.title = (a.written ? 'Inspect what was written to this file' : 'Inspect the proposed changes to this file');
    chip.onclick = () => openFile(i);
    row.appendChild(chip);
  });
  card.appendChild(row);
  if (r.files.length > 3) {
    const more = el('button', 'more-files', expanded ? 'Show fewer files' : `+${r.files.length - 3} more files`);
    more.setAttribute('aria-expanded', String(expanded));
    more.onclick = () => { state.expandedProposal = expanded ? '' : key; state.lockScroll = true; renderThread(); };
    card.appendChild(more);
  }
  const impact = impactBlock(r);
  if (impact) card.appendChild(impact);
  changeActions(r, card, true);
  return card;
}

/* The four decisions about a change set, built once. The pane that used to hold them is gone, so the
   rail's artifact card and the preview ask for the same controls. Roll back follows `canRollback` rather
   than the pane's `canMutate`, because an interrupted apply is exactly when the escape must be on screen. */
function changeActions(r, host, compact = false) {
  const bar = el('div', 'pv-acts');
  const apply = el('button', 'solid', 'Apply changes');
  apply.disabled = !r.canApply;
  apply.onclick = () => send('apply');
  /* Declining is offered in exactly the window Apply is, because it means the same thing about the
     clock: a proposal still waiting for an answer. It writes nothing and discards nothing — the diff
     stays on screen and the refusal goes into the task's own record. */
  const reject = el('button', 'line-btn', 'Reject');
  reject.disabled = !r.canApply;
  reject.onclick = () => send('reject');
  const verify = el('button', 'line-btn', 'Check syntax');
  const undo = el('button', 'line-btn', '↩ Roll back');
  verify.disabled = !r.canMutate;
  undo.disabled = !r.canRollback;
  verify.onclick = () => send('verify');
  undo.onclick = () => send('rollback', {});
  if (r.rejected) {
    apply.title = 'You declined this proposal. Reopen it for review before applying it.';
    reject.title = 'Already declined';
    const reopen = el('button', 'line-btn', r.reopenLabel || 'Reopen for review');
    reopen.disabled = !r.canReopen;
    reopen.onclick = () => send('reopen');
    bar.appendChild(reopen);
  }
  if (!compact || r.pending || r.canApply || r.rejected) bar.append(apply, reject);
  if (!compact) bar.appendChild(verify);
  if (!compact || r.canRollback || DATA.artifact.written) bar.appendChild(undo);
  host.appendChild(bar);
}

function railPreviewCard(r) {
  const file = r.files[state.railFile];
  const view = r.view || {};
  const card = el('div', 'card rail-preview');
  const tabs = [['diff', 'Diff'], ['now', 'Now'], ['was', 'Was'], ['checks', 'Checks']];
  card.innerHTML = `<div class="pv-head"><span class="badge ${file.kind}">${file.kind}</span>
      <code title="${esc(file.path)}">${esc(file.path)}</code>
      <span class="num">${diffStat(file)}</span></div>
    <div class="pv-note">${file.kind === 'A' ? '+ Created by this task'
      : file.kind === 'D' ? '− Removed by this task. Was shows what it held.'
      : '~ Modified by this task'}
      · ${DATA.artifact.written ? 'on disk' : 'proposed, not written'}</div>
    <div class="dtabs pv-tabs"></div><div class="dcode pv-body"></div><div class="hr"></div>`;
  const bar = card.querySelector('.pv-tabs');
  for (const [id, label] of tabs) {
    const b = el('button', id === state.railTab ? 'on' : '', label);
    b.onclick = () => { state.railTab = id; refreshPreview(); };
    bar.appendChild(b);
  }
  const body = card.querySelector('.pv-body');
  const lines = state.railTab === 'diff' ? (view.diff || [])
    : state.railTab === 'now' ? (view.after || [])
    : state.railTab === 'checks' ? (view.checks || []) : (view.before || []);
  if (!lines.length) {
    /* An empty "Was" tab is not a missing file — it is a file this task created. Saying so keeps
       the tab from reading as a load failure. */
    body.innerHTML = '';
    body.appendChild(el('div', 'pv-empty', state.railTab === 'was'
      ? 'This file did not exist before this task.'
      : 'No recorded contents for this tab.'));
  } else if (state.railTab === 'diff') {
    paintDiff(body, lines);
  } else if (state.railTab === 'checks') {
    // Server-built sentences, plain text: the checks the model promised, not results.
    body.innerHTML = '';
    body.appendChild(el('div', 'pv-checks', esc(lines.join('\n'))));
  } else {
    paintCode(body, lines);
  }
  const close = el('button', 'line-btn', '✕ Close preview');
  close.onclick = () => { if (SHEET) SHEET.close(); else { state.railFile = -1; renderRail(); } };
  card.appendChild(close);
  changeActions(r, card);
  return card;
}

/* Live build output: streamed straight to the Activity view, never stored on the server,
   so a 5 000-line build cannot ride along in every later snapshot. Replayed after DATA.log
   when a state render rebuilds the list, and cleared when the next job starts. */
const LIVE = [];
const LIVE_MAX = 300;
function pushChunk(msg) {
  const entry = { ts: msg.ts, kind: 'out', text: msg.text, stream: true };
  LIVE.push(entry);
  if (LIVE.length > LIVE_MAX) LIVE.shift();
  appendLog(entry);
  /* The same line inside the row that is running it: "what is it doing" and "what has it printed so
     far" belong to one place. Appended rather than re-rendered — rebuilding the thread per line would
     fight the reader's scroll for the whole build. */
  const host = document.querySelector('.steprow.live.open .st-out');
  if (host) {
    const stick = atBottom(host);
    host.appendChild(el('span', '', esc(msg.text)));
    while (host.children.length > LIVE_MAX) host.removeChild(host.firstChild);
    if (stick) toBottom(host);
  }
}
/* Two lists, one question: is this row a thing that happened to the task, or is it transcript? The
   system rows stay in Activity proper — a connection, an error, a cap it admitted, a turn milestone.
   Build output as it arrives, and the audit rows a reopened task rebuilds from its own record, go into
   the collapsed block: that is the difference between a list a reader scans and four hundred divs
   nobody scrolls through twice.

   A row is classified by a flag the server put on it, never by guessing at its kind name, so a kind
   this window has not met still shows up — in whichever list it was marked as. */
const RAW_MAX = 300;
let rawDirty = false;

function rawOpen() { return $('raw').open; }
function rawRows() { return DATA.log.filter((entry) => entry.audit).concat(LIVE); }

function rawNode(entry) {
  return el('div', 'r', `<span class="ts">${esc(entry.ts || '')}</span>`
    + `<span class="k">${esc(entry.kind || '')}</span><span class="m">${esc(entry.text || '')}</span>`);
}

function rawHead(count) {
  $('rawhead').textContent = count
    ? `Debug / Raw stream · ${count} line(s)` : 'Debug / Raw stream · nothing this task';
}

function paintRaw(rows) {
  const body = $('rawbody');
  body.innerHTML = '';
  const held = rows.slice(-RAW_MAX);
  for (const entry of held) body.appendChild(rawNode(entry));
  if (rows.length > held.length) {
    body.insertBefore(el('div', 'r', `<span class="m">… ${rows.length - held.length} earlier line(s) `
      + 'are not held in this window</span>'), body.firstChild);
  }
  rawHead(held.length);
  $('raw').hidden = !rows.length;
  rawDirty = false;
}

function appendRaw(entry) {
  const body = $('rawbody');
  const stick = atBottom(body);
  body.appendChild(rawNode(entry));
  while (body.children.length > RAW_MAX) body.removeChild(body.firstChild);
  rawHead(body.children.length);
  $('raw').hidden = false;
  if (stick) toBottom(body);
}

/* Opening the block is the only moment its contents cost anything, so a block that stayed shut while
   forty lines arrived is painted once, here, rather than forty times on the way to being unseen. */
$('raw').addEventListener('toggle', () => {
  if (rawOpen() && rawDirty) paintRaw(rawRows());
});

function appendLog(entry) {
  if (entry.audit || entry.stream) {
    // Transcript, not a status note: the block a reader opens on purpose.
    if (rawOpen()) appendRaw(entry); else rawDirty = true;
    return;
  }
  const log = $('log');
  if (!log.children.length) log.innerHTML = '';
  const host = log.parentElement;
  const stick = atBottom(host);
  const row = el('div', 'r', `<span class="ts">${esc(entry.ts || '')}</span><span class="k">${esc(entry.kind || '')}</span><span class="m">${esc(entry.text || '')}</span>`);
  log.appendChild(row);
  // A reader who scrolled up inside Activity is reading it, not watching it arrive.
  if (stick) toBottom(host);
}
function renderLog() {
  const log = $('log'); log.innerHTML = '';
  /* The cap is the server's and so is the admission: a log that got shorter on its own reads as a task
     that did less than it did. */
  if (DATA.log_note) log.appendChild(el('div', 'r', `<span class="m">${esc(DATA.log_note)}</span>`));
  for (const entry of DATA.log) if (!entry.audit) appendLog(entry);
  const rows = rawRows();
  rawHead(Math.min(rows.length, RAW_MAX));
  $('raw').hidden = !rows.length;
  // A closed block is counted, not rebuilt: forty lines arriving is forty appends avoided.
  if (rawOpen()) paintRaw(rows); else rawDirty = true;
  if (!log.children.length && !DATA.log.length && !LIVE.length) {
    log.appendChild(el('div', 'm', 'Nothing has happened yet in this task.'));
  }
}

function markTabs(view) {
  for (const b of $('seg').children) {
    const active = b.dataset.view === view;
    b.classList.toggle('on', active);
    b.setAttribute('aria-selected', String(active));
    const dot = b.querySelector('.unread-dot');
    if (dot) dot.remove();
    if (!active && b.dataset.view === 'details' && state.unread && state.unread.activity) {
      b.appendChild(el('span', 'unread-dot'));
    }
  }
  for (const id of ['task', 'details']) $('view-' + id).classList.toggle('on', id === view);
}

function switchView(view) {
  state.view = view;
  if (view === 'details' && state.unread) state.unread.activity = false;
  markTabs(view);
}

function setBusy(busy, cancellable) {
  state.busy = busy;
  paintStatus();
  // The counter belongs to the in-flight request alone: started when one is, stopped when it is not.
  // A window that opens onto a running job reaches here through the first snapshot, so it counts too.
  if (busy) startClock(); else stopClock();
  if (busy && (LIVE.length || STREAM)) { LIVE.length = 0; STREAM = ''; renderLog(); }
  if (DATA) {
    DATA.busy = busy; DATA.cancellable = !!cancellable;
    // The typing line means "a request is in flight". Once the window is not busy it has to go,
    // or a reply that arrives over SSE leaves a phantom wait behind it until the next full render.
    if (!busy && DATA.pending) { DATA.pending = null; renderThread(); }
    renderComposer();
  }
}

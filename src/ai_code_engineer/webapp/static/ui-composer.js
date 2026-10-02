/* The request editor and the queue: the composer and its mode row, what the Send button means right
   now, the first-run card, and the strip that holds the messages typed during a running task.
   Queueing asks the server to run a message later; nothing here starts work on its own. */
function renderComposer() {
  const bar = $('cbar'); bar.innerHTML = '';
  const add = (node) => bar.appendChild(node);
  updatePromptPlaceholder();
  if (DATA.project) {
    const bound = !!(DATA.branch || {}).bound;
    /* Which folder this branch is on, always visible where the message is typed: the sidebar can
       be collapsed, and a name alone does not tell two same-named projects apart. */
    const folderName = (DATA.project && DATA.project.name) || (DATA.project && DATA.project.path ? DATA.project.path.split(/[\\/]/).filter(Boolean).pop() : 'project');
    const where = el('button', 'pill path mono', ICON.file + ' ' + esc(folderName));
    where.title = DATA.project.path + '\nClick to copy the full path';
    where.onclick = () => { navigator.clipboard.writeText(DATA.project.path); toast('Project folder copied'); };
    add(where);
    const git = gitChip();
    if (git) add(git);
    const restore = restorePill();
    if (restore) add(restore);
    /* A chat that was dropped onto a folder reads it and nothing more: the server refuses both the
       mode and the switch there, so drawing them as live controls would be a broken affordance. */
    if (!bound) add(autoPill());
  }
  add(el('button', 'pill', ICON.plus + ' Plan')).onclick = () => send('pick_plan');
  if (DATA.plan) {
    const p = el('span', 'pill on', '📄 ' + esc(DATA.plan.name) + (DATA.plan.step ? ` · step ${DATA.plan.step}/${DATA.plan.total}` : '') + '<span class="x">×</span>');
    p.onclick = () => send('clear_plan'); add(p);
  }
  add(el('button', 'pill soft', esc(DATA.provider.mode))).onclick = () => choose('mode', DATA.provider.mode, DATA.provider.modes);
  add(el('button', 'pill mono', esc(shortModel(DATA.provider.model)) + ICON.chev)).onclick = () => choose('model', DATA.provider.model, DATA.provider.models);
  const curlPill = el('button', 'pill soft mono', 'cURL');
  curlPill.title = 'Copy current request as cURL command';
  curlPill.onclick = () => {
    const promptText = ($('prompt')?.value || '').trim() || 'Hello! Confirm you are working.';
    const c = DATA.connection || {};
    const ep = (c.endpoint || c.default_endpoint || '').replace(/\/+$/, '');
    const mdl = DATA.provider.model || 'gemini-2.0-flash';
    const k = c.key_present ? '$' + (c.key_env || 'API_KEY') : 'YOUR_API_KEY';
    const escapedPrompt = JSON.stringify(promptText);
    const cmd = `curl ${ep}/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${k}" \\\n  -d '{\\n    "model": "${mdl}",\\n    "messages": [{"role": "user", "content": ${escapedPrompt}}]\\n  }'`;
    navigator.clipboard.writeText(cmd);
    toast('Copied cURL command for ' + shortModel(mdl));
  };
  add(curlPill);
  if (DATA.busy && DATA.cancellable) {
    /* Stop shares the `.send` look but not the `.send` handle: while a task runs it is the first
       button of that class in the bar, so anything that finds the composer's button by class —
       a test, a macro, an assistive tool — presses Stop and cancels a model turn. */
    const s = el('button', 'send stop', 'Stop');
    s.id = 'stop';
    s.onclick = () => { if (state.sequential) stopSequential(); else send('stop'); }; add(s);
  }
  const sendBtn = el('button', 'send', (DATA.busy ? 'Queue ' : 'Send ') + ICON.up);
  sendBtn.id = 'send';
  sendBtn.title = DATA.busy
    ? 'A task is running — this adds your message to the queue and it starts when that task ends'
    : 'Send';
  sendBtn.onclick = submit; add(sendBtn);
  const ta = $('prompt');
  /* Typable during a run, because typing is how the next message gets written; Send is the button
     that changes meaning, not the box. */
  ta.disabled = false;
  // The selected mode supplies the placeholder; an active run overrides it.
  if (DATA.busy) ta.placeholder = 'Ask the next thing — it queues until this task ends.';
  $('composer').classList.toggle('busy', !!DATA.busy);
}
const shortModel = (m) => (m || 'no model').replace(/^.*?\//, '').slice(0, 30);

/* git is decoration, so it stays quiet rather than guessing: no chip for a folder that is
   not a repository, and no claim of "clean" when the count never came back.
   It is still a button, because it is the only way to start a task on its own branch: the click
   asks git to create `agent/task-…` and move HEAD onto it, and the server confirms the name
   before anything moves. Once this window has moved HEAD, the chip is the way back. */
function gitChip() {
  const g = DATA.git || {};
  if (!g.repo) return null;
  const ref = g.branch || (g.detached ? String(g.head || '').slice(0, 7) : '');
  if (!ref) return null;
  const counted = typeof g.dirty === 'number';
  const back = g.base && g.base !== ref ? g.base : '';
  /* A task branch carries the task's own words, so its tail is what tells two of them apart. The
     whole name stays in the title: an untrimmed 44-character pill measured wider than every other
     control on its line of the composer. */
  const shown = ref.length > 22 ? '\u2026' + ref.slice(-21) : ref;
  const chip = el('button', 'pill git mono ' + (!counted ? 'soft' : (g.dirty ? 'dirty' : 'clean')));
  chip.innerHTML = ICON.branch + '<span>' + esc(shown + (back ? ' \u21a9' : '')) + '</span>' +
                   (counted && g.dirty ? '<b>' + g.dirty + '</b>' : '');
  chip.title = (back ? 'Back to ' + back + ' — this task started there\nOn ' + ref
                     : (g.detached ? 'Detached HEAD at ' : 'Branch ') + ref) + (counted
    ? (g.dirty ? ' \u00b7 ' + g.dirty + ' file(s) changed since the last commit'
               : ' \u00b7 nothing changed since the last commit')
    : ' \u00b7 git did not answer in time') +
    (back || g.detached ? '' : '\nClick to put this task on its own git branch.');
  chip.onclick = () => send('git_branch', { back: back });
  return chip;
}

/* The escalation, not a second undo: the server offers this only after the session's own rollback
   refused because something edited the files afterwards, and only for the files that proposal wrote.
   It is drawn apart from the chip because it destroys current work rather than moving a pointer. */
function restorePill() {
  const offer = (DATA.git || {}).restore;
  if (!offer || !offer.paths) return null;
  const commit = String(offer.commit || '');
  const b = el('button', 'pill restore', '\u26a0 ' + offer.paths + ' \u00b7 git ' + esc(commit));
  b.title = 'Restore those file(s) from commit ' + commit + '. This replaces what is in them now,'
            + ' including any edit made after the task wrote them.';
  b.onclick = () => send('git_restore');
  return b;
}

/* The badge is the only thing that says what Send will do. A project branch opens in chat
   mode on purpose, so a greeting is answered instead of turned into a rejected diff. */
/* Auto-Apply is the one switch that writes without asking, so it is drawn where the message is
   typed, named for what it does, and reads as the amber "this needs your attention" pair rather
   than as a decoration. It belongs to the folder in front, not to the window. */
function autoPill() {
  const on = !!(DATA.settings && DATA.settings.auto_apply);
  const b = el('button', 'pill auto ' + (on ? 'on' : 'off'));
  b.innerHTML = ICON.spark + ' Auto-Apply: ' + (on ? 'ON' : 'OFF');
  b.title = on
    ? 'ON for this folder: what the model proposes writes itself, then the project command runs. ' +
      'It still asks before emptying a file you wrote, and a block you clicked still waits for ' +
      'Apply. Click to go back to reviewing every proposal.'
    : 'OFF: nothing is written until you click Apply. Click to let this folder write itself.';
  b.onclick = () => send('set_auto_apply', { value: !on });
  return b;
}

/* The three positions on the one axis that decides what Send may become. The server owns the rule and
   the words of every refusal; this table is only what choosing each one means, so the badge cannot
   advertise a position the controller would then turn down. */
const MODES = [
  ['chat', 'Chat', 'Answer in prose. Reads the project as context, writes nothing.'],
  ['read', 'Read-only', 'Read it, search it and map it, and explain what is wrong. Builds no proposal and '
    + 'writes nothing, and your project\u2019s own command runs only when you approve that one.'],
  ['change', 'Change', 'Propose a diff you review before any file is written.'],
];
const modeRow = (value) => MODES.find((row) => row[0] === value) || MODES[0];

function modeMenu() {
  const branch = DATA.branch || {};
  const s = sheet('What should Send do?', branch.projectName || 'This conversation');
  const list = el('div', 'content');
  for (const [value, name, desc] of MODES) {
    const b = el('button', 'cmd' + (DATA.composer === value ? ' on' : ''),
      `<span>${esc(name)}</span><span class="g">${DATA.composer === value ? 'current' : ''}</span>`);
    b.onclick = () => { close(); if (DATA.composer !== value) send('set_composer', { value }); };
    list.append(b, el('p', 'cmd-help', esc(desc)));
  }
  s.appendChild(list);
  const close = modal(s);
}

function updatePromptPlaceholder() {
  const branch = DATA.branch || {};
  const change = DATA.composer === 'change', reading = DATA.composer === 'read';
  if (branch.bound) {
    $('prompt').placeholder = change
      ? 'Describe the change for ' + branch.projectName + '. Review the proposal before applying it.'
      : reading
        ? 'Ask about ' + branch.projectName + '. I will inspect and explain without writing.'
        : 'Describe your goal for ' + branch.projectName + '. I will return an ordered plan and write nothing.';
    return;
  }
  const ta = $('prompt');
  /* The one field where the user decides what Send will do, so it has to describe the folder's
     actual behaviour: with the switch on, "review before applying" is a promise this window will
     not keep. */
  const autoOn = !!(DATA.settings && DATA.settings.auto_apply);
  if (ta) ta.placeholder = change
    ? (autoOn
      ? 'Describe the change you want. This folder writes itself, then runs your command.'
      : 'Describe the change you want. Nothing is written until you click Apply.')
    : reading
      ? 'Ask what is wrong here, where it is, and what a fix would touch. Nothing is written.'
      : branch.key ? 'Ask about ' + branch.projectName + ' — it answers in prose and writes nothing.'
        : 'Ask for a plan, task breakdown, or technical guidance. Choose a project for repository context.';
}

function submit() {
  const text = $('prompt').value.trim();
  // An empty Send means "the next step", and that is only true in step-by-step mode, which the
  // settings block carries. A top-level flag of that name never arrives from the server, and
  // reading it here silently disabled the gesture.
  if (!text && !((DATA.settings || {}).chained)) return;
  /* A reference travels as a row number and nothing else: the server pulls the quoted words out of
     its own record. A queued message resolves it now, while the thread the index points into is the
     one on screen, rather than whenever the queue happens to drain. */
  const quote = state.quote >= 0 ? { quote_of: state.quote } : {};
  const step = state.planStepId ? { step_id: state.planStepId } : {};
  state.planStepId = null;
  if (DATA.busy) {
    /* A running task used to swallow this: start_plan returned with no message and the typed text
       was gone. Now it queues, and the strip above the composer shows it as one line. The
       confirmation is the server's toast, because that is the side that knows the language. */
    send('queue_add', { text, ...quote, ...step });
    $('prompt').value = '';
    autosize();
    state.quote = -1; renderQuote();
    return;
  }
  send('send', { text, ...quote, ...step });
  $('prompt').value = '';
  autosize();
  state.quote = -1; renderQuote();
}
function autosize() {
  const ta = $('prompt'); ta.style.height = 'auto';
  ta.style.height = Math.min(240, Math.max(50, ta.scrollHeight)) + 'px';
}

/* The first-run card, over the composer. Each row says its status in a word as well as a colour:
   "green" is not an answer for someone who cannot see it, and this is the first screen a new operator
   reads. Nothing here probes the machine — the rows come from the server, and only a click re-runs
   them. */
const SETUP_MARK = { ok: 'OK', warn: 'WATCH', bad: 'BLOCKED', info: 'NOTE' };

function renderSetup() {
  const box = $('setup');
  if (!box) return;
  box.innerHTML = '';
  const card = DATA.setup || {};
  const rows = card.rows || [];
  if (!card.show || !rows.length) return;
  const btn = (label, title, run) => {
    const b = el('button', 'line-btn');
    b.textContent = label; b.title = title; b.onclick = run;
    return b;
  };
  const head = el('div', 'setup-head');
  const title = el('b'); title.textContent = 'Set up this machine';
  const tally = el('span', 'setup-tally');
  // The server writes the tally: the card's rows already arrive as its sentences in its language, and
  // a count line assembled here would be the one part of the card in a second voice.
  tally.textContent = card.tally || '';
  head.append(title, tally,
    btn('Run the checks', 'Ask this machine what it can reach: the provider, its model list, and the '
        + 'command the folder answers to.', () => send('setup_check')),
    btn('Run the offline proof', 'A proposal applied, checked and rolled back in a temporary folder. '
        + 'No model is asked, and none of your files are touched.', () => send('setup_demo')),
    btn("Don't show this again", 'The card stops appearing at launch. Everything it says stays in '
        + 'Settings.', () => send('setup_hide')));
  box.appendChild(head);
  for (const row of rows) {
    const line = el('div', 'setup-row');
    const badge = el('span', 'setup-badge ' + (SETUP_MARK[row.status] ? row.status : 'info'));
    badge.textContent = SETUP_MARK[row.status] || 'NOTE';
    const body = el('div', 'setup-body');
    const text = el('div', 'setup-text'); text.textContent = row.text || '';
    body.appendChild(text);
    if (row.advice) {
      const advice = el('div', 'setup-advice');
      advice.textContent = row.advice;
      body.appendChild(advice);
    }
    line.append(badge, body);
    box.appendChild(line);
  }
}

/* Messages sent during a running task, one line each. The strip is where the promise lives: a
   queued message starts by itself the moment the current task ends, so what is waiting has to be
   readable without opening anything, and reversible without asking twice. */
function renderQueue() {
  const box = $('queue'); box.innerHTML = '';
  const q = DATA.queue || {};
  const items = q.items || [];
  if (!items.length && !q.elsewhere) return;
  if (q.held) {
    const held = el('div', 'qrow held', '<span class="qk">⏸</span>'
      + `<span class="qt">${esc(q.held_note || '')}</span>`);
    const act = el('div', 'qacts');
    const resume = el('button', 'qbtn', '▶');
    resume.title = 'Let the queue run again';
    resume.onclick = () => send('queue_resume', {});
    act.appendChild(resume); held.appendChild(act);
    box.appendChild(held);
  }
  for (const item of items) {
    const row = el('div', 'qrow');
    row.dir = 'auto';
    // Every sentence here arrives from the server, because only it knows what language the task
    // was asked in; the strip supplies the glyphs and the buttons.
    const when = item.restored ? q.when_restored
      : item.detached ? q.when_detached : q.when;
    row.innerHTML = `<span class="qk">${item.detached ? '↗' : '⏳'}</span>`
      + `<span class="qt">${esc(item.text)}</span><span class="qwhen">${esc(when || '')}</span>`;
    const act = el('div', 'qacts');
    const btn = (label, title, fn) => {
      const b = el('button', 'qbtn', label); b.title = title; b.onclick = fn; act.appendChild(b);
    };
    btn('▶', 'Run this one next', () => send('queue_now', { id: item.id }));
    btn('✎', 'Edit the message before it runs', () => editQueued(row, item));
    btn('↗', 'Ask it in a chat of its own, on this project', () => send('queue_chat', { id: item.id }));
    btn('✕', 'Remove it from the queue', () => send('queue_drop', { id: item.id }));
    row.appendChild(act);
    box.appendChild(row);
  }
  if (q.elsewhere) {
    const note = el('div', 'qnote qnote-clickable', esc(q.elsewhere_note || '1 waiting in another chat — click to switch'));
    note.style.cursor = 'pointer';
    note.title = 'Switch to the waiting chat';
    note.onclick = () => {
      if (DATA.queue && DATA.queue.chat) {
        const destKind = DATA.queue.kind || (String(DATA.queue.chat).startsWith('c-') ? 'chat' : 'session');
        send('open', { id: DATA.queue.chat, kind: destKind });
      }
    };
    box.appendChild(note);
  }
}

function editQueued(row, item) {
  row.innerHTML = '';
  const input = el('input', 'qedit');
  input.value = item.text; input.dir = 'auto'; input.spellcheck = false;
  const act = el('div', 'qacts');
  const save = el('button', 'qbtn', 'Save');
  save.onclick = () => send('queue_edit', { id: item.id, text: input.value });
  const cancel = el('button', 'qbtn', 'Cancel');
  cancel.onclick = () => renderQueue();
  act.append(save, cancel);
  row.append(input, act);
  input.focus(); input.select();
}

function runPlanStep(s) {
  const text = (s && s.title) || `Execute step ${(s && s.id) || ''}`;
  const ta = $('prompt');
  if (ta) {
    ta.value = text;
    autosize();
    if (ta.focus) ta.focus();
  }
  state.planStepId = (s && s.id) || null;
  submit();
  toast('Running: ' + text);
}

function startSequential() {
  if (!DATA || !DATA.plan || !DATA.plan.steps || !DATA.plan.steps.length) {
    toast('No plan steps available to execute.');
    return;
  }
  const pendingStep = DATA.plan.steps.find(s => s.status !== 'verified');
  if (!pendingStep) {
    toast('All plan steps are already verified! ✓');
    return;
  }
  state.sequential = true;
  state.seqStepId = pendingStep.id;
  const autoOn = !!(DATA.settings && DATA.settings.auto_apply);
  if (!autoOn) {
    send('set_auto_apply', { value: true });
  }
  if (DATA.composer !== 'change') {
    send('set_composer', { value: 'change' });
  }
  toast(`Starting sequential execution: Step ${pendingStep.id}/${DATA.plan.total}`);
  runPlanStep(pendingStep);
  renderRail();
}

function stopSequential() {
  state.sequential = false;
  state.seqStepId = null;
  if (state.seqTimer) {
    clearTimeout(state.seqTimer);
    state.seqTimer = null;
  }
  send('stop');
  toast('Sequential execution stopped.');
  renderRail();
}

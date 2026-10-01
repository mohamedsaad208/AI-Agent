/* The window's own surfaces: toasts, the modal and sheet machinery, the questions the agent asks
   (a confirmation, a folder), the settings drawer with the model list and the override rows, the
   command palette, and the preferences -- style, theme, sidebar, rail width.
   These are the window's choices rather than the agent's, so this is where a local preference lives. */
/* --------------------------------- toasts -------------------------------- */
function toast(text, level) {
  const t = el('div', 'toast' + tone(level), `<i></i><span>${esc(text)}</span><button>×</button>`);
  t.querySelector('button').onclick = () => close();
  $('toasts').appendChild(t);
  const timer = setTimeout(close, 4200);
  function close() { clearTimeout(timer); t.classList.add('out'); setTimeout(() => t.remove(), 200); }
}

/* --------------------------------- modals -------------------------------- */
const FOCUSABLE = 'button, input, textarea, select, [tabindex]:not([tabindex="-1"])';

function modal(node, onClose) {
  const scrim = el('div', 'scrim');
  node.setAttribute('role', 'dialog');
  node.setAttribute('aria-modal', 'true');
  scrim.appendChild(node);
  /* Dismissal is a click on the scrim that did not begin as a press elsewhere. Listening for
     mousedown here dismissed a dialog that mounted under the pointer mid-click — the flash where
     a sheet appeared and vanished in the same frame. The 200 ms guard covers the other half: the
     click that opened this dialog is still on its way out of the event queue. */
  const mountedAt = Date.now();
  scrim.addEventListener('click', (event) => {
    if (event.target === scrim && Date.now() - mountedAt > 200) done();
  });
  $('modal-root').appendChild(scrim);
  const opener = document.activeElement;
  const focusables = () => [...node.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled && el.offsetParent);
  (focusables()[0] || node).focus({ preventScroll: true });

  function onKey(event) {
    if (event.key === 'Escape') { event.preventDefault(); done(); return; }
    if (event.key !== 'Tab') return;
    // A dialog the user can Tab out of is a dialog that hides the rest of the app.
    const items = focusables();
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  scrim.addEventListener('keydown', onKey);
  let closed = false;
  function done() {
    if (closed) return;
    closed = true;
    scrim.classList.add('out');
    setTimeout(() => scrim.remove(), 140);
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    if (onClose) onClose();
  }
  return done;
}
function sheet(title, subtitle) {
  const s = el('div', 'sheet');
  if (title || subtitle) s.appendChild(el('header', '', (title ? `<h3>${esc(title)}</h3>` : '') + (subtitle ? `<p>${esc(subtitle)}</p>` : '')));
  return s;
}

/* The server withdraws a question it stopped waiting for, and the window has to forget it too.
   The id is matched by walking the mounted sheets rather than by a selector, because the id is a
   string from the network and a CSS escape of one stray quote would throw on every later event. */
function retractAsk(id) {
  const wanted = String(id || '');
  if (!wanted) return;
  for (const scrim of [...$('modal-root').children]) {
    const box = scrim.firstElementChild;
    if (box && box.dataset.ask === wanted) scrim.remove();
  }
}

function askConfirm(msg) {
  const s = sheet(msg.title, msg.message);
  s.dataset.ask = msg.id;
  if (msg.warning) s.appendChild(el('div', 'content')).appendChild(el('div', 'warnbox', esc(msg.warning)));
  const foot = el('footer');
  const no = el('button', 'line-btn', esc(msg.cancel || 'Cancel'));
  const yes = el('button', 'solid', esc(msg.confirm || 'Continue'));
  const close = modal(s);
  no.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: false }); };
  yes.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: true }); };
  foot.append(no, yes);
  /* A third answer, and it says so on the wire with ok:false beside it: every other reader of a
     confirm treats a reply as yes/no, and this one is "not this round, and do not ask again". Only
     the fix offer sends `alt`, and only the web window has a batch for it to mean. */
  if (msg.alt) {
    const alt = el('button', 'line-btn', esc(msg.alt));
    alt.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: false, alt: true }); };
    foot.insertBefore(alt, yes);
  }
  s.appendChild(foot);
  setTimeout(() => yes.focus(), 40);
}

function askFolder(msg) {
  const s = sheet(msg.title || 'Choose a folder', msg.hint || '');
  s.dataset.ask = msg.id;
  const content = el('div', 'content');
  const tree = el('div', 'tree');
  content.appendChild(tree);
  const foot = el('footer');
  const cancel = el('button', 'line-btn', 'Cancel');
  const pick = el('button', 'solid', 'Choose here');
  pick.disabled = true;
  let chosen = null;
  const close = modal(s);

  let create = null, nameField = null;
  if (msg.mustexist === false) {
    // "New project folder" needs a name, not an existing directory: the whole point is a
    // folder that does not exist yet.
    nameField = el('input');
    nameField.placeholder = 'my-project';
    nameField.spellcheck = false;
    const field = el('div', 'field', '<label>New folder name</label>');
    field.appendChild(nameField);
    content.insertBefore(field, tree);
    create = el('button', 'line-btn', 'Create here');
    create.disabled = true;
    nameField.addEventListener('input', () => {
      const name = nameField.value.trim();
      create.disabled = !name || !chosen;
      create.textContent = name ? 'Create ' + name : 'Create here';
    });
    create.onclick = () => {
      const name = nameField.value.trim();
      const base = jump.value.trim() || chosen;
      if (!name || !base) return;
      close(); api('/api/confirm', { id: msg.id, ok: true, path: base + '/' + name });
    };
    nameField.focus();
  }

  function refreshCreate() {
    if (!create) return;
    const name = (nameField.value || '').trim();
    create.disabled = !name || !chosen;
    create.textContent = name ? 'Create ' + name : 'Create here';
  }

  /* The picker opens wherever the home folder is, so it needs two extra ways out: the mounted
     roots once a drive root has no parent to go up to, and a path you can type — a project that
     does not exist yet cannot be reached by clicking through folders that do. */
  const where = el('div', 'where');
  const jump = el('input', 'jump');
  jump.placeholder = 'Or type a folder path, e.g. D:\\AI\\spring-project';
  jump.spellcheck = false;
  const drives = el('div', 'drives');
  const head = el('div', 'fshead');
  head.append(where, jump, drives);
  content.insertBefore(head, tree);
  const usePath = el('button', 'line-btn', 'Use this path');
  usePath.disabled = true;
  usePath.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: true, path: jump.value.trim() }); };

  async function load(path) {
    tree.innerHTML = '<div class="empty">Loading…</div>';
    let data;
    try {
      const filesQ = (msg.files && msg.files.length) ? '&files=' + encodeURIComponent(msg.files.join(',')) : '';
      data = await api('/api/fs?path=' + encodeURIComponent(path || '') + filesQ);
    } catch (e) {
      tree.innerHTML = '';
      toast('That folder could not be read', 'bad');
      return;
    }
    tree.innerHTML = '';
    where.textContent = data.path;
    where.title = data.path;
    if (data.parent) {
      const up = el('div', 'trow', '<span class="chev">↰</span><span class="nm">..</span>');
      up.onclick = () => load(data.parent); tree.appendChild(up);
    }
    drives.innerHTML = '';
    for (const root of (data.roots || [])) {
      if (data.parent || root === data.path) continue;
      const chip = el('button', 'drive', esc(root));
      chip.onclick = () => load(root);
      drives.appendChild(chip);
    }
    chosen = data.path;
    pick.disabled = false;
    refreshCreate();
    for (const dir of data.dirs) {
      const row = el('div', 'trow', `<span class="chev">▸</span><span class="nm">📁 ${esc(dir.name)}</span>`);
      row.onclick = () => load(dir.path); tree.appendChild(row);
    }
    for (const file of (data.files || [])) {
      const row = el('div', 'trow leaf-file', `<span class="chev"></span><span class="nm">📄 ${esc(file.name)}</span>`);
      tree.appendChild(row);
      if (msg.files) row.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: true, path: file.path }); };
    }
  }
  jump.addEventListener('input', () => {
    const typed = jump.value.trim();
    usePath.disabled = !typed;
    if (create) {
      const name = nameField.value.trim();
      create.disabled = !name || (!chosen && !typed);
      create.textContent = name ? 'Create ' + name : 'Create here';
    }
  });
  jump.addEventListener('keydown', (e) => { if (e.key === 'Enter' && jump.value.trim()) load(jump.value.trim()); });
  load(msg.path || '');
  cancel.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: false }); };
  pick.onclick = () => { close(); api('/api/confirm', { id: msg.id, ok: true, path: chosen }); };
  foot.append(cancel);
  if (create) foot.append(create);
  foot.append(usePath, pick);
  s.append(content, foot);
}

function choose(kind, current, options) {
  const s = sheet(kind === 'model' ? 'Choose a model' : kind === 'mode' ? 'Choose a provider' : 'Choose a command');
  const list = el('div', 'content');
  const entries = (options || []).map((opt) => ({
    value: typeof opt === 'string' ? opt : opt.id,
    desc: typeof opt === 'string' ? '' : (opt.description || ''),
  }));
  let query = '';
  // Filtering happens here, against what this sheet already holds. Round-tripping a keystroke to
  // the server narrowed a list the server keeps in memory, and cost a state push per character.
  const matches = () => {
    const q = query.trim().toLowerCase();
    return !q ? entries
      : entries.filter((item) => (item.value + ' ' + item.desc).toLowerCase().includes(q));
  };
  function paint() {
    list.innerHTML = '';
    const kept = matches();
    for (const item of kept) {
      const b = el('button', 'cmd' + (item.value === current ? ' on' : ''),
        `<span>${esc(item.value)}</span><span class="g">${esc(item.desc.slice(0, 46))}</span>`);
      b.onclick = () => { close(); send('set_' + kind, { value: item.value }); };
      list.appendChild(b);
    }
    if (!kept.length) {
      list.appendChild(el('div', 'empty', entries.length ? 'No model matches that search.'
        : 'Nothing loaded yet. Try Refresh in Settings.'));
    }
    if (count) {
      count.textContent = kept.length === entries.length ? entries.length + ' models'
        : kept.length + ' of ' + entries.length + ' models';
    }
  }
  let count = null;
  if (kind === 'model' && entries.length > 6) {
    const head = el('div', 'field pickfilter');
    const input = el('input');
    input.placeholder = 'Search models — name, size, cloud';
    input.spellcheck = false;
    count = el('span', 'g');
    input.oninput = () => { query = input.value; paint(); };
    input.onkeydown = (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const hit = matches()[0];
      if (hit) { close(); send('set_model', { value: hit.value }); }
    };
    head.append(input, count);
    s.appendChild(head);
  }
  s.appendChild(list);
  paint();
  const close = modal(s);
}

function openSettings(tab) {
  const s = sheet('Settings', ''); s.classList.add('wide');
  const st = DATA.settings;
  const tabs = el('div', 'tabs');
  const body = el('div', 'content');
  body.style.maxHeight = '60vh';
  s.append(tabs, body);
  const close = modal(s, () => { SETTINGS = null; });
  const sections = {
    project: () => `
      <div class="field"><label>Project folder</label><input readonly value="${esc(st.project || '')}"></div>
      <div class="field"><label>Attached plan</label><input readonly value="${esc(st.plan || '')}"></div>
      <label class="switch"><input type="checkbox" id="chained" ${st.chained ? 'checked' : ''}> Step-by-step plan mode: work one step at a time</label>
      <label class="switch"><input type="checkbox" id="autoapply" ${st.auto_apply ? 'checked' : ''} ${st.project && !st.bound ? '' : 'disabled'}> ${st.bound ? 'Auto-Apply belongs to the project branch — open ' + esc(String(st.project || '').split(/[\\/]/).filter(Boolean).pop() || 'it') + ' in the sidebar to change it' : 'Auto-Apply in this folder: what the model proposes writes itself, then the project command runs'}</label>
      <div class="field" style="margin-top:14px"><label>Request timeout (seconds)</label>
        <input type="number" id="timeout" min="30" max="900" step="30" value="${esc(st.timeout)}"></div>`,
    models: () => `
      <div class="field"><label>Filter models by name</label><input id="filter" placeholder="qwen"></div>
      <div class="field"><label>${esc(st.model_info || '')}</label></div>`,
    notes: () => {
      const an = DATA.autoNotes || {};
      return `
      <div class="field"><label>Handwritten project notes — sent with every task in this folder</label>
        <textarea id="memory" ${st.project ? '' : 'disabled placeholder="Choose a project folder first."'}>${esc(st.memory || '')}</textarea>
        <div class="hint">${esc(st.memory_info || '')}</div></div>
      <div class="hr" style="margin:14px 0"></div>
      <div class="field">
        <label>Automatic project summary (grounded in files and verification proofs)</label>
        <label class="switch"><input type="checkbox" id="auto-notes-toggle" ${an.enabled ? 'checked' : ''}> Automatically observe and summarize project stack, files, and verification proofs</label>
        <div class="hint">Updated: ${esc(an.updated_at ? new Date(an.updated_at).toLocaleString() : 'Never')}</div>
        ${an.purpose_and_stack ? `<div class="hint"><b>Observed Stack:</b> ${esc(an.purpose_and_stack)}</div>` : ''}
        ${an.verification_results ? `<div class="hint"><b>Verification:</b> ${esc(an.verification_results)}</div>` : ''}
      </div>`;
    },
    overrides: () => {
      const o = DATA.overrides || {};
      const rows = (o.rows || []).map((r) => `
        <div class="ovrow"><span class="ovk">${esc(r.display)}</span>
          <span class="ovm">${esc(r.state === 'in force' ? r.target : r.why)}`
          + `${r.by ? ' \u00b7 ' + esc(r.by) + ' ' + esc(r.at || '') : ''}</span>
          ${r.state === 'in force' ? `<button class="link" data-drop="${esc(r.target)}|${esc(r.key)}">Remove</button>` : ''}</div>`).join('');
      const keys = (o.keys || []).map((k) => `<option value="${esc(k.key)}">${esc(k.key)}`
        + `${k.number ? ' (' + k.low + ' to ' + k.high + ')' : ''}</option>`).join('');
      const targets = (o.targets || []).map((v) => `<option value="${esc(v)}"`
        + `${v === o.kind ? ' selected' : ''}>${esc(v === '*' ? 'every provider' : v)}</option>`).join('');
      return `
      <div class="field"><label>Rows in force. Only this program writes them, and it signs each one.</label>
        ${rows || '<div class="ovrow"><span class="ovm">Nothing is overridden.</span></div>'}
        <div class="hint">${esc(o.note || '')}</div>
        <div class="hint">${esc(o.path || '')}</div></div>
      <div class="field"><label>Provider this row speaks for, or every provider</label>
        <select id="ov-target">${targets}</select></div>
      <div class="field"><label>Field to change</label><select id="ov-key">${keys}</select></div>
      <div class="field"><label>New value</label><input id="ov-value" placeholder="600"></div>
      <button class="solid" id="ov-save">Save row</button>`;
    },
    connection: () => {
      const c = DATA.connection || {};
      const rows = (list, selected) => list.map((v) =>
        `<option value="${esc(v)}"${v === selected ? ' selected' : ''}>${esc(v)}</option>`).join('');
      return `
      <div class="field"><label>Profile — a file in profiles/, read at pick time</label>
        <select id="profile"><option value="">${esc('None (choose the row below)')}</option>${
          rows(c.profiles || [], c.profile || '')}</select>
        <div class="hint">A profile sets provider, model and endpoint. It carries the name of the
          key variable, never a key.</div></div>
      <div class="field"><label>Provider</label>
        <select id="mode">${rows(DATA.provider.modes || [], DATA.provider.mode)}</select></div>
      <div class="field"><label>Endpoint — where this provider's requests go</label>
        <input id="endpoint" value="${esc(c.endpoint || '')}" placeholder="${esc(c.default_endpoint || 'https://example/v1')}">
        <div class="hint">${esc(connectionHint(c))}</div></div>
      <div class="field"><label>${esc(c.key_env || 'API key')} — never written to disk</label>
        <input type="password" id="key" placeholder="${esc(c.key_present ? 'found in the environment; paste to override for this session' : 'paste for this session only')}">
        <div class="hint">${esc(c.needs_key ? 'This provider requires a key.' : 'This provider needs no key.')}</div></div>
      ${c.consent ? `<label class="switch"><input type="checkbox" id="consent" ${st.consent ? 'checked' : ''}>
        Allow this public / synthetic code to be sent to that address</label>` : ''}`;
    },
  };
  function connectionHint(c) {
    if (c.shape === 'ollama') return 'Ollama on this device. A model tagged "cloud" is still refused without approval.';
    if (c.consent) return 'Outside this device, so it needs your approval on every task and the address must be https.';
    return 'On this device. Nothing leaves it.';
  }
  function show(name) {
    for (const b of tabs.children) b.classList.toggle('on', b.dataset.tab === name);
    body.innerHTML = sections[name]();
    if (SETTINGS) SETTINGS.tab = name;
    body.querySelector('#chained')?.addEventListener('change', (e) => send('set_chained', { value: e.target.checked }));
    body.querySelector('#autoapply')?.addEventListener('change', (e) => send('set_auto_apply', { value: e.target.checked }));
    body.querySelector('#timeout')?.addEventListener('change', (e) => send('set_timeout', { value: +e.target.value }));
    body.querySelector('#filter')?.addEventListener('input', (e) => send('set_filter', { value: e.target.value }));
    body.querySelector('#memory')?.addEventListener('change', (e) => send('save_memory', { text: e.target.value }));
    body.querySelector('#auto-notes-toggle')?.addEventListener('change', (e) => send('toggle_auto_notes', { enabled: e.target.checked }));
    body.querySelector('#profile')?.addEventListener('change', (e) => send('set_profile', { value: e.target.value }));
    body.querySelector('#mode')?.addEventListener('change', (e) => send('set_mode', { value: e.target.value }));
    // change, not input: the endpoint is checked on the server and a rejected value must not be
    // sent on every keystroke, because a valid one re-fetches that provider's model list.
    body.querySelector('#endpoint')?.addEventListener('change', (e) => send('set_endpoint', { value: e.target.value }));
    body.querySelector('#key')?.addEventListener('input', (e) => send('set_key', { value: e.target.value }));
    body.querySelector('#consent')?.addEventListener('change', (e) => send('set_consent', { value: e.target.checked }));
    body.querySelector('#ov-save')?.addEventListener('click', () => send('set_override', {
      target: body.querySelector('#ov-target').value,
      key: body.querySelector('#ov-key').value,
      value: body.querySelector('#ov-value').value}));
    for (const button of body.querySelectorAll('[data-drop]')) {
      button.addEventListener('click', () => {
        const [target, key] = button.dataset.drop.split('|');
        send('unset_override', { target: target, key: key });
      });
    }
  }
  for (const [name, label] of [['project', 'Project & plan'], ['models', 'Models'], ['notes', 'Notes'], ['connection', 'Connection'], ['overrides', 'Overrides']]) {
    const b = el('button', '', label); b.dataset.tab = name; b.onclick = () => show(name); tabs.appendChild(b);
  }
  const foot = el('footer');
  const refresh = el('button', 'line-btn', 'Refresh models'); refresh.onclick = () => send('refresh_models');
  /* "Don't show this again" hides the card, and the note on that button promises the answers stay
     here — so the way back has to be a button, not a memory of where the card was. */
  const checks = el('button', 'line-btn', 'Setup checks');
  checks.title = 'Ask this machine what it can reach, and show the first-run card again.';
  checks.onclick = () => send('setup_check');
  const done = el('button', 'solid', 'Done'); done.onclick = close;
  foot.append(refresh, checks, done); s.appendChild(foot);
  SETTINGS = { show: show, tab: tab || 'project',
                   signature: connectionSignature() + overrideSignature() };
  show(tab || 'project');
}

/* ------------------------------- command bar ------------------------------ */
function palette() {
  const s = el('div', 'sheet palette');
  const input = el('input', 'cmdinput'); input.placeholder = 'Type a command…';
  const list = el('div', 'cmdlist');
  s.append(input, list);
  const close = modal(s);
  const commands = [
    ['New chat (standalone, no project)', () => send('new_chat')],
    ['Chat mode — answer in prose', () => send('set_composer', { value: 'chat' })],
    ['Read-only mode — explain, write nothing', () => send('set_composer', { value: 'read' })],
    ['Change mode — propose a reviewed diff', () => send('set_composer', { value: 'change' })],
    ['Setup checks — what this machine can reach', () => send('setup_check')],
    ['Move this chat into a project…', () => chatMenu({ id: DATA.current, title: DATA.header.title },
      (DATA.branch || {}).key || '')],
    ['Open a project folder', () => send('pick_project')],
    ['New project folder', () => send('new_project')], ['Attach a plan', () => send('pick_plan')],
    ['Try the sample project', () => send('example')],
  ];
  if (lastAsked()) commands.push(['Ask your last message again', askAgain]);
  commands.push(
    ['Run the project command', () => send('run', { fix: false })],
    ['Run and fix', () => send('run', { fix: true })], ['Check syntax', () => send('verify')],
    ['Roll back changes', () => send('rollback')], ['Apply changes', () => send('apply')],
    ['Stop the current task', () => send('stop')], ['Settings', () => openSettings()],
    ['Style: Claude warm', () => setStyle('claude')], ['Style: Codex charcoal', () => setStyle('codex')],
    ['Style: Linear indigo', () => setStyle('linear')], ['Theme: light', () => { state.themeMode = 'light'; applyThemeMode(); renderThemePick(); }],
    ['Theme: dark', () => { state.themeMode = 'dark'; applyThemeMode(); renderThemePick(); }],
  );
  let shown = commands.slice(), at = 0;
  function draw() {
    list.innerHTML = '';
    shown.forEach(([label, run], i) => {
      const b = el('button', 'cmd' + (i === at ? ' on' : ''), esc(label));
      b.onclick = () => { close(); run(); }; b.onmouseenter = () => { at = i; draw(); };
      list.appendChild(b);
    });
  }
  input.oninput = () => { const q = input.value.toLowerCase(); shown = commands.filter((c) => c[0].toLowerCase().includes(q)); at = 0; draw(); };
  input.onkeydown = (e) => {
    if (e.key === 'ArrowDown') { at = Math.min(shown.length - 1, at + 1); draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { at = Math.max(0, at - 1); draw(); e.preventDefault(); }
    else if (e.key === 'Enter') { close(); const hit = shown[at]; if (hit) hit[1](); e.preventDefault(); }
    else if (e.key === 'Escape') close();
  };
  draw(); setTimeout(() => input.focus(), 30);
}
function setStyle(style) {
  state.style = style; document.documentElement.dataset.style = style;
  savePrefs({ style });
  send('set_style', { style });
}
function savePrefs(patch) {
  const saved = JSON.parse(localStorage.getItem('ui.prefs') || '{}');
  localStorage.setItem('ui.prefs', JSON.stringify({ ...saved, ...patch }));
}

/* The button that folds the sidebar away lives inside it, so the header carries the way back.
   Both that button and Ctrl+B come through here, so the two cannot drift apart. */
const sidebarCollapsed = () => document.documentElement.classList.contains('side-collapsed');
function setSidebar(collapsed) {
  document.documentElement.classList.toggle('side-collapsed', !!collapsed);
  savePrefs({ collapsed: !!collapsed });
  sendQuiet('set_collapsed', { value: !!collapsed });
}
const toggleSidebar = () => setSidebar(!sidebarCollapsed());

function setupRailResizer() {
  const saved = localStorage.getItem('rail-width');
  if (saved) document.documentElement.style.setProperty('--rail-w', saved);
  const resizer = $('rail-resizer');
  if (!resizer) return;
  let startX = 0, startW = 0, dragging = false;

  resizer.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    startX = e.clientX;
    startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail-w'), 10) || 322;
    document.body.classList.add('resizing');
    resizer.classList.add('dragging');
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    e.preventDefault();
  });

  function onMouseMove(e) {
    if (!dragging) return;
    const delta = startX - e.clientX;
    const maxW = Math.min(760, Math.floor(window.innerWidth * 0.65));
    const newW = Math.max(260, Math.min(maxW, startW + delta));
    document.documentElement.style.setProperty('--rail-w', newW + 'px');
  }

  function onMouseUp() {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing');
    resizer.classList.remove('dragging');
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    const finalW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail-w'), 10) || 322;
    localStorage.setItem('rail-width', finalW + 'px');
  }

  resizer.addEventListener('dblclick', () => {
    document.documentElement.style.setProperty('--rail-w', '322px');
    localStorage.removeItem('rail-width');
    toast('Rail width reset to default');
  });
}

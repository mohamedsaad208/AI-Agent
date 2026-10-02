/* Projects and navigation: the sidebar tree of branches, the project drawer that measures a folder,
   the module graph behind the Sources card, the icon and chat menus, and the header the selected
   branch writes.
   Choosing a folder is a `pick_project` / `bind_chat` action -- this file never touches a path. */
function renderThemePick() {
  const pick = $('themepick'); pick.innerHTML = '';
  const modes = [['light', '☀'], ['dark', '☾'], ['auto', '◐']];
  for (const [mode, glyph] of modes) {
    const b = el('button', state.themeMode === mode ? 'on' : '', glyph);
    b.title = mode + ' theme';
    b.onclick = () => { state.themeMode = mode; applyThemeMode(); renderThemePick(); };
    pick.appendChild(b);
  }
}
function applyThemeMode() {
  const mode = state.themeMode || 'light';
  const dark = mode === 'auto' ? matchMedia('(prefers-color-scheme: dark)').matches : mode === 'dark';
  state.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.theme = state.theme;
  savePrefs({ theme: mode });
  send('set_theme', { theme: state.theme, mode });
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (state.themeMode === 'auto') applyThemeMode(); });

function relTime(iso) {
  if (!iso) return '';
  const mins = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'now'; if (mins < 60) return Math.round(mins) + 'm';
  if (mins < 1440) return Math.round(mins / 60) + 'h';
  return Math.round(mins / 1440) + 'd';
}
const DOT = { CHECKS_PASSED: 'ok', WAITING_APPROVAL: 'warn', APPLIED_UNVERIFIED: 'warn',
  VERIFICATION_FAILED: 'bad', VERIFICATION_BLOCKED: 'warn', PARTIAL_APPLY: 'bad', APPLYING: 'bad',
  ROLLED_BACK: '', CANCELLED: '', BLOCKED: 'bad' };

/* A project granted in this session has no mark yet, so ask once — and only for a project the
   user just picked. A restored branch is marked as asked without prompting, because a modal on
   every launch would be worse than the plain initials avatar. */
function offerIcon(data) {
  state.iconAsked = state.iconAsked || {};
  const branch = data.branch || {};
  if (!state.booted) {
    state.booted = true;
    if (branch.key) state.iconAsked[branch.key] = true;
    return;
  }
  if (branch.kind !== 'project' || !branch.key || state.iconAsked[branch.key]) return;
  const group = (data.projects || []).find((g) => g.key === branch.key);
  if (!group || group.icon) return;
  state.iconAsked[branch.key] = true;
  /* The click that picked this project is still settling when render() reaches here, so a dialog
     mounted on this tick is dismissed by that same event — the picker appears and vanishes at
     once. Deferring it past the pointer events is what makes the offer visible; the branch check
     is so a deferred picker never opens over a project the user has already left. */
  clearTimeout(state.iconTimer);
  let tries = 0;
  const ask = () => {
    /* A picker that lands while a sentence is being typed is the same glitch with a new face: the
       dialog steals the caret, and the keystrokes that follow go to its buttons. So it waits for a
       quiet moment, and gives up rather than queueing behind someone who is working. */
    if (handsBusy() || document.querySelector('.scrim')) {
      if (++tries < 5) state.iconTimer = setTimeout(ask, 1200);
      return;
    }
    if ((DATA.branch || {}).key === branch.key) iconPicker(group);
  };
  state.iconTimer = setTimeout(ask, 400);
}

function renderNav() {
  const nav = $('nav'); nav.innerHTML = '';
  const q = (state.query || '').toLowerCase();
  const proj = el('div', 'sec');
  proj.appendChild(el('h4', '', '<span>Projects</span><span class="add" id="add-project" title="Add or open project folder">＋</span>'));
  proj.querySelector('#add-project').onclick = () => send('pick_project');
  for (const group of DATA.projects) {
    if (q && !(group.name.toLowerCase().includes(q) || group.chats.some((c) => c.title.toLowerCase().includes(q)))) continue;
    // Nothing is open until the user opens it, so the tree starts collapsed on every load. A
    // search expands the projects it matched because a hidden result is not a result. Once a
    // node carries a stored choice that choice wins outright — an extra `|| branch in front`
    // clause here used to make the active project uncloseable: the click stored false while the
    // clause kept re-opening it.
    const chosen = state.expanded[group.key];
    const matched = !!q && group.chats.some((c) => c.title.toLowerCase().includes(q));
    const open = chosen === undefined ? matched : chosen;
    const isBusyProj = !!(DATA.busy && DATA.branch && DATA.branch.key === group.key);
    const hasUnread = (group.chats || []).some((c) => c.unread || (DATA.queue && DATA.queue.chat === c.id));
    const unreadDot = hasUnread ? '<span class="unread-dot" title="Unread activity"></span>' : '';
    const spinDot = isBusyProj ? '<span class="spin-dot" title="Task running in this project"></span>' : '';
    const node = el('div', 'node' + (open ? '' : ' closed') + (isBusyProj ? ' running' : ''),
      '<span class="av project-icon" aria-hidden="true">'
      + esc(group.icon || '📁') + '</span>'
      + `<span class="nm">${esc(group.name)}</span>`
      + unreadDot + spinDot
      + '<button class="dots node-add" title="New chat in this project">＋</button>'
      + '<button class="dots node-settings" title="Project settings">⚙</button>'
      + '<button class="dots node-menu" title="Project options">⋯</button>'
      + '<span class="chev">▾</span>');
    node.onclick = () => { state.expanded[group.key] = !open; renderNav(); };
    node.title = group.path + '\nDrop a chat here to let it read this project';
    node.querySelector('.node-add').title = 'New chat in ' + group.name + ' — reads this project, writes nothing';
    node.querySelector('.node-add').onclick = (e) => {
      e.stopPropagation();
      send('new_chat_in', { project: group.key });
    };
    node.querySelector('.node-settings').onclick = (e) => {
      e.stopPropagation();
      projectDrawer(group);
    };
    node.querySelector('.node-menu').title = 'Project options';
    node.querySelector('.node-menu').onclick = (e) => { e.stopPropagation(); projectMenu(group); };
    // The drop target is the node itself: a chat moved here reads this folder from then on.
    node.ondragover = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; node.classList.add('drop'); };
    node.ondragleave = () => node.classList.remove('drop');
    node.ondrop = (e) => {
      e.preventDefault(); node.classList.remove('drop');
      const id = e.dataTransfer.getData('text/chat');
      if (!id) return;
      send('bind_chat', { chat: id, project: group.key });
      toast('Moved into ' + group.name);
    };
    proj.appendChild(node);
    if (!open) continue;
    for (const chat of group.chats) {
      if (q && !chat.title.toLowerCase().includes(q)) continue;
      proj.appendChild(leaf(chat, chat.kind || 'session', group.key));
    }
  }
  if (!DATA.projects.length) proj.appendChild(el('div', 'empty', 'No project yet. Open or create a folder to work on real files.'));
  nav.appendChild(proj);

  const chats = el('div', 'sec');
  chats.appendChild(el('h4', '', '<span>Chats · no project</span>'));
  const loose = DATA.chats.filter((c) => !q || c.title.toLowerCase().includes(q));
  for (const chat of loose) chats.appendChild(leaf(chat, 'chat', ''));
  if (!loose.length) chats.appendChild(el('div', 'empty', 'New chat starts on its own: no folder, no context, nothing to write.'));
  nav.appendChild(chats);
}

/* One sidebar row. A chat leaf drags onto a project node to bind itself to that folder, and
   the same command is on its menu, because a drag is not reachable from a keyboard. */
function leaf(chat, kind, projectKey) {
  const isBusyChat = !!(DATA.busy && chat.id === DATA.current);
  const spinDot = isBusyChat ? '<span class="spin-dot" title="Task running in this chat"></span>' : '';
  const leaf = el('div', 'leaf' + (chat.id === DATA.current ? ' on' : '') + (isBusyChat ? ' running' : ''),
    `<i class="dot ${chat.busy || isBusyChat ? 'run' : (DOT[chat.state] || '')}"></i><span class="t">${esc(chat.title)}</span>`
    + spinDot
    + `<time>${relTime(chat.updated)}</time>`);
  leaf.onclick = () => send('open', { id: chat.id, kind });
  if (kind === 'chat') {
    leaf.draggable = true;
    leaf.ondragstart = (e) => {
      e.dataTransfer.setData('text/chat', chat.id);
      e.dataTransfer.effectAllowed = 'move';
      leaf.classList.add('dragging');
    };
    leaf.ondragend = () => leaf.classList.remove('dragging');
    const menu = el('button', 'dots', '⋯');
    menu.title = 'Move this chat';
    menu.onclick = (e) => { e.stopPropagation(); chatMenu(chat, projectKey); };
    leaf.appendChild(menu);
  }
  return leaf;
}

function projectMenu(group) {
  const s = sheet(group.name, group.path);
  const list = el('div', 'content');
  const rows = [
    ['Project settings & status…', () => projectDrawer(group)],
    ['New chat in this project', () => send('new_chat_in', { project: group.key })],
    ['Choose project mark…', () => iconPicker(group)],
  ];
  if ((DATA.branch || {}).key === group.key) rows.push(['Leave the project (standalone chat)', () => send('new_chat')]);
  rows.push(['Remove project from list…', () => removeProjectConfirm(group)]);
  for (const [label, run] of rows) {
    const b = el('button', 'cmd', `<span>${esc(label)}</span><span class="g"></span>`);
    b.onclick = () => { close(); run(); };
    list.appendChild(b);
  }
  s.appendChild(list);
  const close = modal(s);
}

function removeProjectConfirm(group) {
  const s = sheet('Remove project', 'Remove "' + group.name + '" from the sidebar? Your files on disk will NOT be deleted.');
  const foot = el('footer');
  const cancel = el('button', 'line-btn', 'Cancel');
  const removeBtn = el('button', 'solid', 'Remove from sidebar');
  const close = modal(s);
  cancel.onclick = () => close();
  removeBtn.onclick = () => {
    close();
    send('remove_project', { project: group.key });
    toast('Removed ' + group.name + ' from project list');
  };
  foot.append(cancel, removeBtn);
  s.appendChild(foot);
}

/* -------------------------------- project drawer ------------------------------- */
/* One drawer per project: what folder it really is, what it remembers, what the next request
   will cost, and which command it will run. The numbers come from the server because the map is
   measured, not guessed — and measuring it is the one slow part, so it is fetched on open. */
function drawRow(title, value, used, total) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return el('div', 'meter', `<span class="t">${esc(title)}</span>`
    + `<span class="bar"><i style="width:${pct}%"></i></span>`
    + `<span class="v mono">${esc(value)}</span>`);
}

async function projectDrawer(group) {
  const s = sheet('Project · ' + group.name, String(group.path || ''));
  const body = el('div', 'content');
  body.appendChild(el('div', 'quiet', 'Measuring this folder…'));
  s.appendChild(body);
  const close = modal(s);
  let info;
  try {
    info = await api('/api/project?project=' + encodeURIComponent(group.key));
  } catch (err) {
    body.innerHTML = '';
    body.appendChild(el('div', 'warnbox', esc(String(err.message || err))));
    return;
  }
  const n = (value) => Number(value || 0).toLocaleString('en-US');
  body.innerHTML = '';

  const root = el('div', 'dsec');
  root.appendChild(el('h6', '', 'Root'));
  root.appendChild(el('div', 'mono pathline', esc(info.path)
    + (info.exists ? '' : '  (this folder is not on disk right now)')));
  const open = el('button', 'line-btn', 'Open in Explorer');
  open.disabled = !info.exists;
  open.onclick = () => { send('reveal', { project: info.key }); toast('Opened in the file manager'); };
  root.appendChild(open);
  body.appendChild(root);

  const notes = el('div', 'dsec');
  notes.appendChild(el('h6', '', 'Notes'));
  const area = el('textarea', 'notes');
  area.value = info.notes || '';
  area.rows = 5;
  area.maxLength = info.notes_limit;
  const count = el('div', 'quiet small');
  const show = () => { count.textContent = `${n(area.value.length)} / ${n(info.notes_limit)} chars`; };
  area.oninput = show; show();
  notes.appendChild(area);
  notes.appendChild(count);
  notes.appendChild(el('div', 'quiet small',
    'Saved as ' + esc(info.notes_file) + ' in ' + esc(info.notes_dir)
    + ' — outside the project, so a proposal can never rewrite it.'));
  const save = el('button', 'solid', 'Save notes');
  save.onclick = async () => {
    await send('save_memory', { text: area.value });
    toast('Project notes saved');
    show();
  };
  notes.appendChild(save);
  body.appendChild(notes);

  const ctx = el('div', 'dsec');
  const c = info.context;
  ctx.appendChild(el('h6', '', 'Context budget'));
  ctx.appendChild(drawRow('Repository map', n(c.map) + ' chars', c.map, c.budget));
  ctx.appendChild(drawRow('Project notes', n(c.notes) + ' chars', c.notes, c.budget));
  ctx.appendChild(drawRow('This conversation' + (c.bound ? '' : ' (none here)'),
    n(c.turns) + ' chars', c.turns, c.budget));
  ctx.appendChild(drawRow('System prompt', n(c.system) + ' chars', c.system, c.budget));
  ctx.appendChild(drawRow('Remaining', n(c.remaining) + ' chars', c.remaining, c.budget));
  ctx.appendChild(el('div', 'quiet small',
    `${n(c.used)} of ${n(c.budget)} chars ≈ ${n(c.est_tokens)} tokens (chars ÷ 4, estimate)`
    + ` · ${n(c.files)} files indexed`));
  body.appendChild(ctx);

  const tool = el('div', 'dsec');
  tool.appendChild(el('h6', '', 'Toolchain'));
  if (!info.toolchain.detected.length) {
    tool.appendChild(el('div', 'quiet', 'No build tool this app knows how to run was detected here.'));
  }
  for (const recipe of info.toolchain.detected) {
    const on = recipe.name === info.toolchain.selected;
    const row = el('button', 'cmd recipe' + (on ? ' on' : ''),
      `<span>${esc(recipe.label)}${on ? ' · selected' : ''}</span><span class="g mono">${esc(recipe.command)}</span>`);
    row.onclick = () => { send('set_recipe', { value: recipe.label }); close(); };
    tool.appendChild(row);
  }
  if (info.toolchain.selected) {
    tool.appendChild(el('div', 'quiet small',
      'Runs with a ' + n(info.toolchain.timeout) + 's limit; success is judged by '
      + esc(info.toolchain.proof) + '. Provider requests time out at '
      + n(info.toolchain.request_timeout) + 's.'));
  }
  body.appendChild(tool);
}

/* The module graph. The server walks the folder and answers with nodes, edges and a column for each
   module, and this only draws what it said: no layout maths, no simulation, no library. The columns are
   build order, so the leftmost box is the first thing that compiles and an arrow points left, at what a
   module needs — which is why the subtitle says it before the reader has to guess it from the shape. */
function graphSvg(data) {
  const COL = 186, ROW = 58, TOP = 26, LEFT = 14, BOX = 150, BOXH = 38;
  const columns = {};
  let deepest = 1;
  (data.nodes || []).forEach((node) => {
    const key = Number(node.column || 0);
    (columns[key] = columns[key] || []).push(node);
    if (key + 1 > deepest) deepest = key + 1;
  });
  const rows = Math.max(1, ...Object.values(columns).map((list) => list.length));
  const place = {};
  Object.keys(columns).forEach((key) => {
    columns[key].forEach((node, index) => {
      place[node.name] = { x: LEFT + Number(key) * COL, y: TOP + index * ROW };
    });
  });
  const width = LEFT + (deepest - 1) * COL + BOX + LEFT;
  const height = TOP + rows * ROW + 10;
  const clip = (name) => (name.length > 19 ? name.slice(0, 18) + '…' : name);
  const lines = (data.edges || []).map((edge) => {
    const from = place[edge.from], to = place[edge.to];
    if (!from || !to) return '';
    const x1 = from.x, y1 = from.y + BOXH / 2, x2 = to.x + BOX, y2 = to.y + BOXH / 2;
    const lean = Math.max(28, (x1 - x2) / 2);
    const heavy = Math.min(5, 1 + Number(edge.count || 1) * 0.7);
    return `<path class="gedge" marker-end="url(#ghead)" stroke-width="${heavy.toFixed(1)}" `
      + `d="M${x1} ${y1} C${x1 - lean} ${y1}, ${x2 + lean} ${y2}, ${x2} ${y2}"><title>`
      + `${esc(edge.from)} → ${esc(edge.to)} (${esc(String(edge.count))} files)</title></path>`;
  }).join('');
  const boxes = (data.nodes || []).map((node) => {
    const at = place[node.name];
    if (!at) return '';
    return `<g class="gnode"><rect x="${at.x}" y="${at.y}" width="${BOX}" height="${BOXH}" rx="7">`
      + `<title>${esc(node.name)} — ${esc(String(node.files))} indexed file(s)</title></rect>`
      + `<text x="${at.x + 9}" y="${at.y + 23}">${esc(clip(String(node.name)))}</text></g>`;
  }).join('');
  return `<svg class="graph" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" `
    + `role="img" aria-label="${esc(data.caption || 'Module dependency graph')}">`
    + `<defs><marker id="ghead" viewBox="0 0 9 9" refX="8" refY="4.5" markerWidth="7" `
    + `markerHeight="7" orient="auto-start-reverse"><path class="ghead" d="M0 0 L9 4.5 L0 9 z"/>`
    + `</marker></defs>${lines}${boxes}</svg>`;
}

async function graphSheet() {
  const s = sheet('Dependency graph', 'Left builds first · an arrow points at what a module needs');
  s.classList.add('wide');
  const body = el('div', 'content');
  body.appendChild(el('div', 'quiet', 'Mapping this folder…'));
  s.appendChild(body);
  modal(s);
  let data;
  try {
    const reply = await api('/api/action', { type: 'show_graph' });
    data = reply && reply.result;
    if (reply && reply.state) render(reply.state);
  } catch (err) {
    body.innerHTML = '';
    body.appendChild(el('div', 'warnbox', esc(String(err.message || err))));
    return;
  }
  body.innerHTML = '';
  if (!data || !(data.nodes || []).length) {
    /* The server always answers in words when there is nothing to draw; this is the click that must not
       open an empty box and leave the reader wondering whether the project has no structure. */
    body.appendChild(el('div', 'quiet', esc((data || {}).note || 'The window answered with no graph.')));
    return;
  }
  body.appendChild(el('div', 'quiet', esc(data.caption || '')));
  body.appendChild(el('div', 'gscroll', graphSvg(data)));
  const list = el('div', 'gmods');
  (data.nodes || []).forEach((node) => list.appendChild(el('div', 'gmod',
    `<b>${esc(String(node.name))}</b> <span class="quiet mono">`
    + `${esc(String(node.files))} file(s) · column ${esc(String(Number(node.column || 0) + 1))}</span>`)));
  body.appendChild(list);
}

async function readinessDrawer() {
  const s = sheet('Project Readiness Check', 'Toolchain & Environment verification');
  const body = el('div', 'content');
  body.appendChild(el('div', 'quiet', 'Checking installed tools and project configs…'));
  s.appendChild(body);
  modal(s);

  let data;
  try {
    const res = await api('/api/action', { type: 'get_readiness' });
    data = (res && res.result) || {};
  } catch (err) {
    body.innerHTML = `<div class="warnbox">${esc(err.message)}</div>`;
    return;
  }

  body.innerHTML = '';
  const toolsSec = el('div', 'dsec');
  toolsSec.appendChild(el('h6', '', 'Runtimes & Toolchains'));
  const toolsList = el('div', 'readiness-list');
  (data.tools || []).forEach(t => {
    const row = el('div', 'readiness-item');
    row.innerHTML = `<span><b>${esc(t.name)}</b></span>
      <span class="mono quiet">${esc(t.available ? '✓ installed' : '✗ not found')}</span>`;
    toolsList.appendChild(row);
  });
  toolsSec.appendChild(toolsList);
  body.appendChild(toolsSec);

  if (data.wrappers && data.wrappers.length) {
    const wrapSec = el('div', 'dsec');
    wrapSec.appendChild(el('h6', '', 'Project Wrappers'));
    wrapSec.appendChild(el('div', 'quiet small', 'Found wrappers: ' + data.wrappers.map(w => esc(w)).join(', ')));
    body.appendChild(wrapSec);
  }

  const envSec = el('div', 'dsec');
  envSec.appendChild(el('h6', '', 'Environment (.env)'));
  const env = data.env || {};
  envSec.appendChild(el('div', 'quiet small', `Has .env: ${env.has_env ? 'Yes ✓' : 'No ✗'} · Has .env.example: ${env.has_example ? 'Yes ✓' : 'No ✗'}`));
  if (env.missing_keys && env.missing_keys.length) {
    envSec.appendChild(el('div', 'warn small', 'Missing keys in .env: ' + env.missing_keys.map(k => esc(k)).join(', ')));
  }
  body.appendChild(envSec);

  if (data.recommendations && data.recommendations.length) {
    const recSec = el('div', 'dsec');
    recSec.appendChild(el('h6', '', 'Recommendations'));
    data.recommendations.forEach(r => {
      recSec.appendChild(el('div', 'quiet small', '💡 ' + esc(r)));
    });
    body.appendChild(recSec);
  }
}

async function runConfigDrawer() {
  const s = sheet('Run & Test Configuration', 'Configure run, test, and build commands for this project');
  const body = el('div', 'content');
  body.appendChild(el('div', 'quiet', 'Loading configuration…'));
  s.appendChild(body);
  const close = modal(s);

  let cfg;
  try {
    const res = await api('/api/action', { type: 'get_run_config' });
    cfg = (res && res.result) || {};
  } catch (err) {
    body.innerHTML = `<div class="warnbox">${esc(err.message)}</div>`;
    return;
  }

  body.innerHTML = '';
  const appSec = el('div', 'dsec');
  appSec.innerHTML = `<h6>App (Run)</h6>
    <label class="quiet small">Run Command</label>
    <input class="cfg-input" id="cfg-app-cmd" value="${esc((cfg.app && cfg.app.command) || '')}" style="width:100%;margin-bottom:6px">
    <label class="quiet small">Working Directory</label>
    <input class="cfg-input" id="cfg-app-cwd" value="${esc((cfg.app && cfg.app.cwd) || '.')}" style="width:100%;margin-bottom:6px">
    <label class="quiet small">Port (e.g. 3000, 8080)</label>
    <input class="cfg-input" type="number" id="cfg-app-port" value="${esc((cfg.app && cfg.app.port) || 0)}" style="width:100%">`;
  body.appendChild(appSec);

  const testSec = el('div', 'dsec');
  testSec.innerHTML = `<h6>Tests</h6>
    <label class="quiet small">Test Command</label>
    <input class="cfg-input" id="cfg-test-cmd" value="${esc((cfg.test && cfg.test.command) || '')}" style="width:100%;margin-bottom:6px">
    <label class="quiet small">Working Directory</label>
    <input class="cfg-input" id="cfg-test-cwd" value="${esc((cfg.test && cfg.test.cwd) || '.')}" style="width:100%">`;
  body.appendChild(testSec);

  const buildSec = el('div', 'dsec');
  buildSec.innerHTML = `<h6>Build</h6>
    <label class="quiet small">Build Command</label>
    <input class="cfg-input" id="cfg-build-cmd" value="${esc((cfg.build && cfg.build.command) || '')}" style="width:100%;margin-bottom:6px">
    <label class="quiet small">Working Directory</label>
    <input class="cfg-input" id="cfg-build-cwd" value="${esc((cfg.build && cfg.build.cwd) || '.')}" style="width:100%">`;
  body.appendChild(buildSec);

  const saveBtn = el('button', 'solid', 'Save Configuration');
  saveBtn.onclick = async () => {
    const updated = {
      version: 1,
      app: {
        command: $('cfg-app-cmd').value.trim(),
        cwd: $('cfg-app-cwd').value.trim() || '.',
        port: parseInt($('cfg-app-port').value, 10) || 0,
      },
      test: {
        command: $('cfg-test-cmd').value.trim(),
        cwd: $('cfg-test-cwd').value.trim() || '.',
      },
      build: {
        command: $('cfg-build-cmd').value.trim(),
        cwd: $('cfg-build-cwd').value.trim() || '.',
      },
      services: (cfg.services || []),
    };
    await send('save_run_config', { config: updated });
    toast('Run configuration saved');
    close();
  };
  body.appendChild(saveBtn);
}

/* The palette is served by the controller, so a mark the app does not know about cannot be
   painted into the sidebar no matter what the registry file contains. */
function iconPicker(group) {
  const s = sheet('Project mark', group.name);
  const grid = el('div', 'iconpick');
  for (const icon of (DATA.icons || [])) {
    const b = el('button', 'icon' + (icon === group.icon ? ' on' : ''), esc(icon));
    b.onclick = () => { close(); send('set_icon', { project: group.key, value: icon }); };
    grid.appendChild(b);
  }
  const body = el('div', 'content');
  body.appendChild(grid);
  s.appendChild(body);
  const close = modal(s);
}

function chatMenu(chat, projectKey) {
  const s = sheet('Move this chat', chat.title);
  const list = el('div', 'content');
  const rows = [];
  for (const group of DATA.projects) {
    if (group.key === projectKey) continue;
    rows.push([group.name, () => send('bind_chat', { chat: chat.id, project: group.key })]);
  }
  if (projectKey) rows.push(['Leave the project (standalone chat)', () => send('bind_chat', { chat: chat.id, project: '' })]);
  if (!rows.length) list.appendChild(el('div', 'empty', 'No other project to move it into.'));
  for (const [label, run] of rows) {
    const b = el('button', 'cmd', `<span>${esc(label)}</span><span class="g">reads its context</span>`);
    b.onclick = () => { close(); run(); };
    list.appendChild(b);
  }
  s.appendChild(list);
  const close = modal(s);
}

/* A project mark keeps the same colour for the same folder name. The lightness is pinned to one
   end of the scale on purpose: at 46% a name that happens to hash to a yellow hue reaches only
   2.5:1 under white and 4.3:1 under near-black, so no ink is readable at all. At these two
   lightness values every one of the 360 hues clears AA against the ink returned with it. */
function avatar(name) {
  let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return document.documentElement.dataset.theme === 'dark'
    ? { bg: `hsl(${h} 42% 32%)`, ink: '#ffffff' }
    : { bg: `hsl(${h} 42% 72%)`, ink: '#171612' };
}

function renderHeader() {
  $('title').textContent = DATA.header.title || 'New chat';
  $('subtitle').textContent = DATA.header.subtitle || '';
  if ($('attach-top')) $('attach-top').classList.toggle('hidden', !DATA.project);

  const btnPlan = $('mode-btn-plan');
  const btnChange = $('mode-btn-change');
  const btnRead = $('mode-btn-read');
  const declared = DATA.declared || {};
  if (btnRead) {
    btnRead.classList.toggle('sealed', !!declared.sealed);
    btnRead.innerHTML = (declared.sealed ? ICON.lock + ' ' : '') + 'Read-only';
    btnRead.title = declared.sealed
      ? (declared.note || (declared.by ? 'Read-only set by ' + declared.by : 'This folder is locked to read-only'))
      : 'Read-only Mode: read and explain code, write nothing';
  }
  if (btnPlan && btnChange && btnRead) {
    const isChange = DATA.composer === 'change';
    const isRead = DATA.composer === 'read' || !!(DATA.declared && DATA.declared.sealed);
    const isPlan = !isChange && !isRead;
    btnPlan.classList.toggle('on', isPlan);
    btnChange.classList.toggle('on', isChange);
    btnRead.classList.toggle('on', isRead);

    btnPlan.onclick = () => {
      if (DATA.composer !== 'chat') send('set_composer', { value: 'chat' });
    };
    btnChange.onclick = () => {
      if (DATA.composer !== 'change') send('set_composer', { value: 'change' });
    };
    btnRead.onclick = () => {
      if (DATA.composer !== 'read') send('set_composer', { value: 'read' });
    };
  }

  // The snapshot carries the activity line because the server keeps it current: a status written
  // mid-run has to survive the next push, which is exactly what used to wipe it.
  state.status = DATA.status || '';
  paintStatus();
  state.stage = DATA.stage || null;
  paintStage();
  markTabs(state.view);
}

/* The activity strip under the header. It used to be a 2px shimmer with no words in it while the
   live progress line was written into the header subtitle instead, so the next state push erased a
   status that was still true and the header read "Connecting to the model…" where it should name
   the folder and the step. */
function paintStatus() {
  const bar = $('busybar');
  const line = state.status || '';
  bar.textContent = line;
  bar.dir = ARABIC_RUN.test(line) ? 'rtl' : 'auto';
  bar.classList.toggle('busy', !!state.busy);
  bar.classList.toggle('hidden', !line && !state.busy);
}

/* The workflow strip: the sentence the server chose for the task's language, then the eight names behind
   it so the position can be checked rather than felt. It is drawn from the session record, so a reload
   mid-run puts it back exactly where it was. */
function paintStage() {
  const bar = $('stagestrip');
  const st = state.stage || {};
  bar.textContent = '';
  bar.dir = ARABIC_RUN.test(st.line || '') ? 'rtl' : 'auto';
  const said = document.createElement('span');
  said.className = 'stage-line';
  said.textContent = st.line || '';
  bar.appendChild(said);
  (st.steps || []).forEach((row) => {
    const mark = document.createElement('span');
    mark.className = 'stage-mark' + (row.reached ? ' on' : '');
    mark.title = row.label;
    bar.appendChild(mark);
  });
  bar.classList.toggle('hidden', !st.current);
}

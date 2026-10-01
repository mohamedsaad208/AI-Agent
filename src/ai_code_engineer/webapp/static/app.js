/* Client for the local UI server. Everything is rendered from one state object that
   the server pushes deltas for over SSE, so the DOM never holds its own truth. */
'use strict';

let TOKEN = new URLSearchParams(location.search).get('t') || '';
const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
/* Tones and levels arrive from the server and land inside a class="" attribute, so they are
   looked up rather than interpolated: an unexpected value renders bare instead of styling. */
const TONES = new Set(['idle', 'ok', 'warn', 'bad']);
const tone = (name) => (TONES.has(name) ? ` ${name}` : '');

/* One rule for every surface that follows its own end: follow only when the reader was already there.
   `renderThread` measured this before rebuilding; the arriving stream and the Activity list scrolled
   unconditionally on every chunk, so a line higher up could not be read while a job was printing. */
const BOTTOM = 80;
function atBottom(host) { return host.scrollTop + host.clientHeight >= host.scrollHeight - BOTTOM; }
function toBottom(host) { host.scrollTop = host.scrollHeight; }

const ICON = {
  file: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3v5h5"/><path d="M6 3h8l5 5v13H6z"/></svg>',
  plus: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 5v14M5 12h14"/></svg>',
  gear: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/></svg>',
  chev: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M6 9l6 6 6-6"/></svg>',
  up: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  spark: '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>',
  branch: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="17.5" cy="8" r="2.2"/><path d="M6 7.7v10.8M17.5 10.2c0 3.2-2.4 4.6-5 5.1-1.9.4-3.3.9-4.3 1.6"/></svg>',
  copy: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 5.5H6a1.5 1.5 0 0 0-1.5 1.5v9"/></svg>',
  reply: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5 4 10l5 5"/><path d="M4 10h9a6 6 0 0 1 6 6v3"/></svg>',
  lock: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></svg>',
};

const state = {
  view: 'task', busy: false, cancellable: false, expanded: {}, style: 'claude', theme: 'light',
  status: '',
  // Which change the rail is previewing (an index into review.files, because the server's own
  // selection is what carries the diff), and which of its tabs is showing.
  railFile: -1, railTab: 'diff',
  railSection: '',
  unread: { changes: false, tasks: false, activity: false },
  lastFilesCount: -1, lastPlanStep: -1, lastLogLen: -1,
  sequential: false, seqStepId: null, seqTimer: null, wasBusy: false, planStepId: null,
  // An open "come and look" intent waiting for the snapshot that carries the change set.
  previewWant: '',
  // The one step row the thread has open. Held here rather than in the DOM because `renderThread`
  // rebuilds every row on each state push — and because the server holds what is inside it.
  openStep: '',
  // Which message the next one answers. An index, like the copy button's row, because the quotation
  // is read out of the server's own record — this window only says which message it meant.
  quote: -1,
  expandedProposal: '',
};

/* True when the user's hands are on something: a field with text in it, or an open dialog.
   Two things key off this — a global shortcut must not fire over typing, and a dialog must not
   pop up in the middle of a sentence. */
function handsBusy() {
  const tag = (document.activeElement || {}).tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  return !!document.querySelector('.scrim');
}

/* ------------------------------- transport ------------------------------- */
async function api(path, body) {
  const res = await fetch(path + (path.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(TOKEN), {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // Read the body once: a Response cannot be consumed twice, and a 403 that threw on the second
    // read would report a TypeError instead of the reason the server gave.
    const text = await res.text();
    if (res.status === 403) windowGoneDeaf(text);
    throw new Error(text);
  }
  return res.json();
}
/* Every action answers with the full state, so one apply covers both the optimistic
   click and the server's verdict on it. */
const send = (type, payload) => api('/api/action', { type, ...payload })
  .then((reply) => { if (reply && reply.state) render(reply.state); })
  .catch((e) => toast(String(e.message || e), 'bad'));
/* For actions whose only effect the client already knows about: re-rendering on every
   keystroke would rebuild the composer for nothing. */
const sendQuiet = (type, payload) => api('/api/action', { type, ...payload }).catch(() => {});

/* The token is minted per launch, so a restart leaves an already-open tab showing real history that
   answers no click — every request refused, one 4.2 s toast per click, the page looking perfectly
   alive. That is the failure a user cannot diagnose: a reported "View button does nothing" turned out
   to be this. The notice stays on screen and names the fix, and it appears once, not per click. */
let DEAF = false;
function windowGoneDeaf(reason) {
  if (DEAF) return;
  DEAF = true;
  const t = el('div', 'toast bad', `<i></i><span>${esc(reason || 'Not authorised for this UI session.')}</span>`
    + `<button data-reload>Reload</button><button>Close</button>`);
  t.querySelector('[data-reload]').onclick = () => location.reload();
  t.querySelector('button:last-child').onclick = () => t.remove();
  $('toasts').appendChild(t);
}

/* An EventSource cannot read the body of a 403, so a link whose session ended looks exactly like a
   network blip — and re-dialling it every 1.5 s is silent forever. Counted on a stale token: 33
   rejected requests before anyone looked. After a few, the window has to admit it is deaf. */
const STREAM_RETRY_LIMIT = 4;
let streamRetries = 0;

function connectEvents() {
  const src = new EventSource('/api/events?t=' + encodeURIComponent(TOKEN));
  /* A dropped stream reconnects on its own, and every event it missed was a whole snapshot. The
     window then keeps showing the last one it saw — measured live: the review card said "Apply
     changes" was unavailable while the server was sitting on a pending proposal waiting for that
     very click. Re-reading the bootstrap when a stream opens costs one request and ends the drift. */
  src.onopen = () => { streamRetries = 0; api('/api/bootstrap').then(render).catch(() => {}); };
  src.onmessage = (row) => {
    let msg; try { msg = JSON.parse(row.data); } catch { return; }
    applyEvent(msg);
  };
  src.onerror = () => {
    if (++streamRetries >= STREAM_RETRY_LIMIT) {
      streamGoneQuiet();
      src.close();
      return;
    }
    setTimeout(() => src.close() || connectEvents(), 1500);
  };
}

function streamGoneQuiet() {
  windowGoneDeaf('Lost the connection to the agent. Reload this page to open it again.');
}

function applyEvent(msg) {
  switch (msg.kind) {
    case 'state': render(msg.data); break;
    case 'status': state.status = msg.text; paintStatus(); break;
    case 'log': appendLog(msg.entry || msg); break;
    case 'log_chunk': pushChunk(msg); break;
    case 'message': if ((msg.message || {}).role === 'assistant') STREAM = '';
                    state.data.messages.push(msg.message); renderThread(); break;
    case 'token': appendToken(msg.text); break;
    case 'busy': setBusy(msg.value, msg.cancellable); break;
    case 'confirm': drawAsk(msg); break;
    case 'retract': retractAsk(msg.id); break;
    case 'toast': toast(msg.text, msg.level); break;
    case 'folder': drawAsk(msg); break;
    case 'view': showView(msg.value); break;
  }
}

/* A question is a server-side object with an id the answer has to carry, so the same sheet can reach
   the client twice: on the event that raised it, and inside every snapshot afterwards. The ids ever
   drawn are remembered, which is what keeps the two paths from stacking two sheets on one waiter —
   D29's duplicate row, one layer up. Ids are random per question, so a re-raised ask is a new id. */
const ASKS_DRAWN = new Set();
function drawAsk(msg) {
  if (!msg || !msg.id || ASKS_DRAWN.has(msg.id)) return;
  ASKS_DRAWN.add(msg.id);
  if (msg.kind === 'folder') askFolder(msg); else askConfirm(msg);
}

/* --------------------------------- render -------------------------------- */
let DATA = null;
function render(data) {
  DATA = data;
  state.data = data;
  /* A new task, a rollback or a switch of project empties the file list, and a preview holding an
     index into it would be pointing at nothing. */
  if (state.railFile >= (((data.review || {}).files) || []).length) state.railFile = -1;
  /* A reference is an index into this thread, so a new task, a rolled-back one or a switched project
     empties it — a banner quoting a message that is no longer on screen would quote the wrong thing. */
  if (state.quote >= ((data.messages || []).length)) state.quote = -1;
  /* A row opened because it was *running* has nothing to show once the job ends and nobody has fetched
     its stored block: it closes rather than sitting open and empty. */
  if (state.openStep && !data.busy && !(data.step_detail && data.step_detail.id === state.openStep)) {
    state.openStep = '';
  }
  const filesCount = (((data.review || {}).files) || []).length;
  if (state.lastFilesCount >= 0 && filesCount > state.lastFilesCount && state.railSection !== 'changes') {
    state.unread.changes = true;
  }
  state.lastFilesCount = filesCount;
  const planStep = (data.plan || {}).step;
  if (state.lastPlanStep >= 0 && planStep !== state.lastPlanStep && state.railSection !== 'tasks') {
    state.unread.tasks = true;
  }
  state.lastPlanStep = planStep;
  const logLen = (data.log || []).length;
  if (state.lastLogLen >= 0 && logLen > state.lastLogLen && state.view !== 'details') {
    state.unread.activity = true;
  }
  state.lastLogLen = logLen;
  /* A question opened before this page connected — or before the UI was restarted — arrives here
     rather than as an event, and it is still holding a worker. */
  for (const ask of (data.asks || [])) drawAsk(ask);
  applyServerDraft(data.draft);
  // The server keeps the collapsed state so a relaunch opens the way the window was left; the
  // boot script already applied the local copy, and after this the two agree by construction.
  if (!state.prefsSeen && data.prefs) { state.prefsSeen = true; setSidebar(data.prefs.collapsed); }
  offerIcon(data);
  // Style and theme are the window's own choice, applied before first paint by the boot
  // script; a state push from the server must not snap them back.
  if (state.openStep) state.lockScroll = true;
  renderThemePick(); renderNav(); renderHeader(); renderThread(); renderComposer();
  renderQueue(); renderSetup(); renderQuote();
  renderRail(); renderLog();
  syncSettings();
  // A proposal that arrived on this snapshot was already asked to show itself.
  takePreviewOffer();
  setBusy(data.busy, data.cancellable);

  const wasBusy = !!state.wasBusy;
  state.wasBusy = !!data.busy;
  if (state.sequential && wasBusy && !data.busy) {
    const doneId = state.seqStepId;
    if (doneId) {
      send('complete_step', { step_id: doneId });
    }
    if (state.seqTimer) clearTimeout(state.seqTimer);
    state.seqTimer = setTimeout(() => {
      if (!state.sequential) return;
      const steps = (DATA && DATA.plan && DATA.plan.steps) || [];
      const next = steps.find(s => s.id !== doneId && s.status !== 'verified');
      if (next) {
        state.seqStepId = next.id;
        toast(`الخطوة التالية (${next.id}/${DATA.plan.total}): ${next.title}`);
        runPlanStep(next);
      } else {
        state.sequential = false;
        state.seqStepId = null;
        toast('🎉 اكتملت جميع خطوات الخطة بنجاح!', 'good');
        renderRail();
      }
    }, 1500);
  }
}

/* The server owns the draft only when it puts one there itself — "Try sample project"
   and an auto-advanced plan step. Echoing the client's own keystrokes back would
   fight the caret, so anything we typed ourselves is ignored here. */
function applyServerDraft(draft) {
  const box = $('prompt');
  const wanted = draft || '';
  if (wanted === box.value || wanted === state.lastDraftSent) return;
  box.value = wanted;
  autosize();
}

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

/* A block that opens by naming a file — "# path: src/main.py", "// calculator.js",
   "<!-- notes.md -->", or the bare name on its own first line — is the model handing over a
   whole file rather than a snippet. Only a name with an extension counts, and only alone on
   that line, so a comment that happens to mention src/old.py stays a comment. */
const BLOCK_LABEL = /^(?:path|file|filename)\s*[:=]\s*(.+)$/i;
function blockTarget(code) {
  let head = String(code).replace(/^\s+/, '').split('\n')[0] || '';
  head = head.replace(/^(?:#+|\/\/+|<!--|--+|%%?|;+|!+|\*\*+)\s*/, '')
             .replace(/\s*(?:-->|\*\*+)$/, '').trim();
  const named = BLOCK_LABEL.exec(head);
  const raw = (named ? named[1] : head).trim().replace(/^["'`]+|["'`]+$/g, '');
  if (!raw || raw.length > 200 || /\s/.test(raw)) return '';
  if (raw.startsWith('/') || raw.includes('\\') || /^[a-zA-Z]:/.test(raw) || raw.includes('..')) return '';
  return /\.[A-Za-z][A-Za-z0-9]{0,7}$/.test(raw) ? raw : '';
}

/* The header line is a label, not content: it comes off before the block becomes a file,
   which is what a person pasting the block by hand would do too. */
function blockBody(text, target) {
  const body = String(text).replace(/ /g, ' ');
  if (blockTarget(body) !== target) return body;
  const cut = body.indexOf('\n');
  return cut < 0 ? '' : body.slice(cut + 1);
}

function mdToHtml(text) {
  const parts = String(text).split('```');
  let out = '';
  parts.forEach((chunk, i) => {
    if (i % 2 === 1) {
      const nl = chunk.indexOf('\n');
      const lang = nl > 0 ? chunk.slice(0, nl).trim() : '';
      const code = nl > 0 ? chunk.slice(nl + 1) : chunk;
      const target = blockTarget(code);
      /* The button writes nothing. It opens the same WAITING_APPROVAL proposal a model's
         proposal opens, so Apply, the SHA-256 guard and rollback stay the only road to the
         disk — and it appears only when this branch actually has a folder to write in. */
      const apply = target && DATA.project
        ? `<span class="fname" title="${esc(target)}">${esc(target)}</span>` +
          `<button data-block="${esc(target)}" title="Propose writing this block to ${esc(target)}. You review the diff before anything is written.">` +
          ICON.spark + ' Apply to File</button>'
        : '';
      out += `<div class="code"><div class="bar"><b>${esc(lang || 'code')}</b>${apply}<button data-copy>Copy</button></div><pre>${hl(code.replace(/\n$/, ''))}</pre></div>`;
      return;
    }
    out += inline(chunk);
  });
  return out;
}
function inline(chunk) {
  // ATX headings end at their newline, even without a blank line before the next list.
  const blocks = chunk.replace(/\r\n/g, '\n')
    .replace(/^[ \t]{0,3}(#{1,6}[ \t]+[^\n]*)$/gm, '\n\n$1\n\n');
  return esc(blocks).trim().split(/\n{2,}/).map((block) => {
    const lines = block.split('\n');
    if (/^\s*[-*]\s+/.test(lines[0]) || /^\s*\d+[.)]\s+/.test(lines[0])) {
      const tag = /^\s*\d+[.)]/.test(lines[0]) ? 'ol' : 'ul';
      return `<${tag}>` + lines.filter(Boolean).map((l) => `<li>${rich(l.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''))}</li>`).join('') + `</${tag}>`;
    }
    if (/^#{1,6}\s/.test(lines[0])) return `<h3>${rich(lines[0].replace(/^#{1,6}\s+/, ''))}</h3>`;
    return `<p>${rich(block)}</p>`;
  }).join('');
}
const rich = (s) => s
  .replace(/`([^`]+)`/g, '<code class="inl">$1</code>')
  .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<i>$2</i>');
const KEYWORDS = /(?:public|private|protected|class|interface|return|if|else|throw|new|import|package|void|static|final|def|self|None|True|False|for|while|in|not|and|or|const|let|async|await|function|try|except|catch)/;
/* One pass: highlighting the result of a previous replace() would re-scan the markup
   we just inserted, and `class` is itself a keyword. */
const TOKENS = new RegExp('([^\\S\\n]*\\/\\/[^\\n]*|#[^\\n]*)|(&quot;[^&\\n]*?&quot;|&#39;[^&\\n]*?&#39;)|\\b(' + KEYWORDS.source + ')\\b', 'g');
function hl(code) {
  return esc(code).replace(TOKENS, (all, comment, str, kw) =>
    comment ? `<span class="tok-c">${comment}</span>`
      : str ? `<span class="tok-s">${str}</span>`
        : `<span class="tok-k">${kw}</span>`);
}

/* A tool row leads with its author label — "Tool", "Changes" — which is a strong LTR run, so
   dir="auto" on the row resolves to LTR even when the notice after it is Arabic. The row's own
   text has to decide, from the same code-point range labels.py tests server-side. */
const ARABIC_RUN = /[؀-ۿ]/;

function renderThread() {
  const scroller = $('scroller');
  const savedScroll = scroller ? scroller.scrollTop : 0;
  const stick = atBottom(scroller);
  const thread = $('thread'); thread.innerHTML = '';
  // One live card replaces the current task's proposal/apply rows; its files come from the session.
  const files = (DATA.review || {}).files || [];
  const lastUser = DATA.messages.reduce((last, m, i) => m.role === 'user' ? i : last, -1);
  const decisions = DATA.messages.map((m, i) => i > lastUser && m.step &&
    ['propose', 'applied'].includes(m.step.action) ? i : -1).filter(i => i >= 0);
  const chipAt = files.length ? (decisions.length ? decisions[decisions.length - 1]
    : DATA.messages.reduce((last, m, i) => (m.role === 'tool' ? i : last), DATA.messages.length - 1)) : -1;
  // Only the newest step row is "the running one", and only while a job is live.
  const lastStep = DATA.messages.reduce((last, m, i) => (m.step ? i : last), -1);
  DATA.messages.forEach((m, i) => {
    // The current proposal changes state in one card. Earlier tasks keep their audit rows.
    if (files.length && decisions.includes(i) && i !== chipAt) return;
    const msg = el('div', 'msg ' + (m.role === 'user' ? 'me' : m.role === 'tool' ? 'sys' : ''));
    msg.id = 'message-' + i;
    const pic = el('div', 'pic', m.role === 'user' ? 'Y' : m.role === 'tool' ? '⚙' : 'A');
    const body = el('div', 'body');
    /* The row's two actions join a line that already exists rather than opening one of their own: an
       answer has its name and minute above the bubble, a prompt has the bubble itself, and only a tool
       note has nothing to join. Placed once, into `slot`, so a row cannot get the icons twice — which
       is also why they sit beside the bubble instead of inside it: `appendToken` rewrites the bubble's
       innerHTML on every streamed chunk, and a handler in there would be deleted by the next one. */
    let slot = body;
    if (i === chipAt && decisions.includes(i)) {
      // The card header below owns this row's actions.
    } else if (m.step) {
      const step = stepRow(m, i === lastStep);
      slot = step.querySelector('.st-line');
      body.appendChild(step);
    } else if (m.role === 'tool') {
      const note = el('div', 'tool-line', `<span class="tool"><b>${esc(m.author)}</b> ${esc(m.text)}</span>`);
      slot = note;
      note.dir = ARABIC_RUN.test(m.text) ? 'rtl' : 'auto';
      body.appendChild(note);
    } else {
      // dir="auto" lets the browser choose from the first strong character, so an Arabic answer
      // reads right-to-left while an English one is untouched — no per-message detection in JS.
      const reply = splitReply(m.text);
      const isStepPrompt = m.role === 'user' && (/^(?:Implement step|Execute step|الخطوة)\s+\d+/i.test((reply.text || '').trim())) && (reply.text || '').length > 140;
      const bub = el('div', 'bub');
      if (isStepPrompt) {
        const lines = reply.text.trim().split('\n');
        const firstLine = lines[0];
        const rest = lines.slice(1).join('\n').trim();
        const card = el('div', 'step-prompt-card');
        const head = el('div', 'step-prompt-head');
        head.innerHTML = `<span>📋 ${esc(firstLine)}</span>`;
        if (rest) {
          const toggle = el('button', 'step-prompt-toggle', 'Show details ▾');
          const bodyDetails = el('div', 'step-prompt-body hidden', esc(rest));
          toggle.onclick = () => {
            const isHidden = bodyDetails.classList.toggle('hidden');
            toggle.textContent = isHidden ? 'Show details ▾' : 'Hide details ▴';
          };
          head.appendChild(toggle);
          card.append(head, bodyDetails);
        } else {
          card.appendChild(head);
        }
        bub.appendChild(card);
      } else {
        bub.innerHTML = mdToHtml(reply.text);
      }
      if (reply.preview) bub.prepend(replyPill(reply.preview,
        Number.isInteger(m.replyTo) && m.replyTo >= 0 && m.replyTo < i ? m.replyTo : replyTarget(reply.preview, i)));
      bub.dir = 'auto';
      if (m.role === 'assistant') {
        slot = el('div', 'mhead');
        slot.appendChild(el('span', 'who', `${esc(m.author)} · ${esc(m.time || '')}`));
        body.append(slot, bub);
      } else {
        slot = el('div', 'bubline');
        slot.appendChild(bub);
        body.appendChild(slot);
      }
    }
    if (i === chipAt) {
      const card = chipCard(DATA.review);
      body.appendChild(card);
      for (const index of decisions.filter(index => index !== chipAt)) {
        const anchor = el('span', 'reply-anchor');
        anchor.id = 'message-' + index;
        card.prepend(anchor);
      }
      if (decisions.includes(i)) slot = card.querySelector('.chat-task-head');
    }
    /* Offered on every row that has words in it, the operator's own included: "answer that" does not
       care who said it, and a prompt you typed is exactly the text you want on the clipboard somewhere
       else. */
    if ((m.text || '').trim()) slot.appendChild(msgActions(i));
    msg.append(pic, body); thread.appendChild(msg);
  });
  if (DATA.pending || STREAM) {
    const msg = el('div', 'msg');
    /* While an answer is arriving it is plain text in here, and markdown appears when the finished
       message lands: reparsing the whole reply per line would repaint on every one of them. The
       bubble is drawn from the text that has arrived when there is any, and from the waiting line
       before there is — which is what lets a stream show an answer without claiming a job is running. */
    msg.innerHTML = `<div class="pic">A</div><div class="body"><div class="bub" dir="auto">`
      + `<span class="typing"><i></i><i></i><i></i> `
      + `<span class="typing-line">${esc(STREAM || DATA.pending)}</span>`
      + `<span class="typing-clock"></span></span></div></div>`;
    thread.appendChild(msg);
    paintClock();
  }
  thread.querySelectorAll('[data-copy]').forEach((b) => {
    b.onclick = () => { navigator.clipboard.writeText(b.closest('.code').querySelector('pre').innerText); toast('Copied to clipboard'); };
  });
  /* Wired here rather than inside mdToHtml: appendToken repaints the streaming bubble's
     innerHTML on every chunk, and a handler attached there would go with it. */
  thread.querySelectorAll('[data-block]').forEach((b) => {
    b.onclick = () => {
      if (DATA.busy) { toast('Wait for the running task, then apply that block'); return; }
      const code = blockBody(b.closest('.code').querySelector('pre').innerText, b.dataset.block);
      if (!code.trim()) { toast('That block has nothing left to write'); return; }
      send('apply_block', { path: b.dataset.block, content: code });
      toast('Proposing ' + b.dataset.block + ' — review the diff, then Apply');
    };
  });
  if (state.lockScroll) {
    if (scroller) scroller.scrollTop = savedScroll;
    state.lockScroll = false;
  } else if (stick) {
    toBottom(scroller);
  }
}

/* The wait, counted. This is the app's only repeating timer, and it measures this window's own wait:
   the request left here and the answer has not come back. Nothing server-side is redrawn per second,
   which is what keeps a long build from costing snapshots, and it stops with the job that started it. */
let TICK = null, WAITED = 0;
function clockFace(seconds) {
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}
function paintClock() {
  const face = document.querySelector('.typing-clock');
  // The clock is a sibling of the line the stream rewrites, for the same reason the copy button is:
  // anything inside `.typing-line` is deleted by the next arriving chunk.
  if (face) face.textContent = '⏱ ' + clockFace(WAITED);
}
function startClock() {
  stopClock();
  WAITED = 0;
  TICK = setInterval(() => { WAITED += 1; paintClock(); }, 1000);
}
function stopClock() { if (TICK !== null) { clearInterval(TICK); TICK = null; } }

/* The answer that is arriving right now, whole lines from the server, kept out of DATA.messages for
   the same reason LIVE is: a snapshot goes out on every other event, and a half-finished reply must
   not be stored, hashed or exported as if it were one. Cleared when the next job starts. */
let STREAM = '';
const STREAM_MAX = 20000;
function appendToken(text) {
  const scroller = $('scroller');
  const stick = atBottom(scroller);          // measured before the line grows, as renderThread measures it
  STREAM = (STREAM ? STREAM + '\n' + text : text).slice(-STREAM_MAX);
  if (!document.querySelector('.typing-line')) {
    renderThread();                                  // the first chunk has to create its own bubble
    return;
  }
  document.querySelector('.typing-line').textContent = STREAM;
  if (stick) toBottom(scroller);
}

function msgActions(index) {
  const row = el('div', 'macts');
  const copy = el('button', 'mact', ICON.copy);
  copy.dataset.copyRow = index;
  copy.title = 'Copy this message';
  copy.setAttribute('aria-label', 'Copy this message');
  const quote = el('button', 'mact', ICON.reply);
  quote.dataset.quoteRow = index;
  quote.title = 'Answer this message';
  quote.setAttribute('aria-label', 'Answer this message');
  quote.setAttribute('aria-pressed', state.quote === index ? 'true' : 'false');
  const again = el('button', 'mact', '↻');
  again.dataset.againRow = index;
  again.title = 'Put this message back in composer to ask again';
  again.setAttribute('aria-label', 'Put this message back in composer to ask again');
  row.append(copy, quote, again);
  return row;
}

/* The message the next one answers, shown above the box it will be sent from. The row keeps only the
   index: the server reads the quotation out of its own record, so a reference can say what was
   actually said rather than what this window happens to hold. */
const QUOTE_WHO = { user: 'you', assistant: 'the agent', tool: 'a notice from the tool' };

function splitReply(text) {
  const match = String(text || '').match(/^> \[(?:In reference to |بالإشارة إلى )[^\n]*?: "([^\n]*)"\]\s*\n/);
  return match ? { text: text.slice(match[0].length), preview: match[1] }
    : { text: text || '', preview: '' };
}

function replyTarget(preview, before) {
  const words = preview.replace(/…$/, '').replace(/\s+/g, ' ').trim();
  for (let i = before - 1; i >= 0; i--) {
    if (words && String(DATA.messages[i].text || '').replace(/\s+/g, ' ').trim().startsWith(words)) return i;
  }
  return -1;
}

function replyPill(preview, index) {
  const pill = el('button', 'reply-pill', `↩ <span dir="auto">${esc(preview)}</span>`);
  pill.title = index >= 0 ? 'Go to original message' : 'Original message is not in this conversation';
  pill.disabled = index < 0;
  pill.onclick = () => {
    const target = $('message-' + index);
    if (target) {
      target.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
      target.animate([{ background: 'var(--hover)' }, { background: 'transparent' }], { duration: 900 });
    }
  };
  return pill;
}

function renderQuote() {
  const box = $('quote');
  box.innerHTML = '';
  const message = state.quote >= 0 ? (DATA.messages || [])[state.quote] : null;
  if (!message) state.quote = -1;
  /* The row that is being answered says so. Nothing rebuilds the thread here — the banner is the only
     thing that changes when you pick a row — so the pressed state is written onto the buttons that are
     already in the DOM, or the control would keep insisting it had not been used. */
  for (const button of document.querySelectorAll('#thread [data-quote-row]')) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.quoteRow) === state.quote));
  }
  if (!message) {
    return;
  }
  const words = (message.text || '').replace(/\s+/g, ' ').trim();
  const shown = words.length > 180 ? words.slice(0, 180) + '…' : words;
  box.appendChild(replyPill(shown, state.quote));
  const close = el('button', 'quote-off', '✕');
  close.title = 'Stop referring to this message';
  close.onclick = () => { state.quote = -1; renderQuote(); };
  box.appendChild(close);
}

function quoteRow(index) {
  state.quote = state.quote === index ? -1 : index;
  renderQuote();
  if (state.quote >= 0) $('prompt').focus();
}

/* One thing the agent did, as a row that can be opened. The sentence and its glyph are the server's
   (`labels.step_line`), and so is the answer to "is there anything behind this one" — `step.detail`,
   because a chevron that opens onto nothing teaches the reader to stop opening rows. The running row
   is the one exception the client decides for itself: while a command is going, its detail is the
   output streaming in, which no stored record holds yet. */
function stepRow(m, isLast) {
  const step = m.step;
  const live = !!(DATA.busy && isLast && step.action === 'executing');
  const open = state.openStep === step.id;
  const row = el('div', 'steprow' + (open ? ' open' : '') + (live ? ' live' : ''));
  const head = el('button', 'st-head',
    `<span class="st-txt">${esc(m.text)}</span>`
    + ((open || live || step.detail) ? `<span class="st-chev">${ICON.chev}</span>` : ''));
  head.dir = ARABIC_RUN.test(m.text) ? 'rtl' : 'auto';
  head.title = (open || live || step.detail)
    ? 'What this step has behind it' : '';
  head.onclick = () => toggleStep(step.id, live);
  const line = el('div', 'st-line');
  line.appendChild(head);
  row.appendChild(line);
  if (open) row.appendChild(stepBody(step, live));
  return row;
}

function toggleStep(id, live) {
  state.lockScroll = true;
  if (state.openStep === id) {
    state.openStep = '';
    if (!live) sendQuiet('step_detail', { id: '' });   // the server stops carrying the block
    renderThread();
    return;
  }
  state.openStep = id;
  // A running row needs no round trip, and asking would answer "nothing stored yet" about a command
  // that is visibly still printing.
  if (live) renderThread(); else send('step_detail', { id });
}

function stepBody(step, live) {
  const box = el('div', 'st-body');
  if (live) {
    const out = el('div', 'st-out');
    out.innerHTML = LIVE.map((entry) => `<span>${esc(entry.text)}</span>`).join('');
    box.appendChild(out);
    return box;
  }
  const detail = (DATA.step_detail && DATA.step_detail.id === step.id) ? DATA.step_detail : null;
  if (!detail) return box;                       // the reply has not landed yet
  if (detail.note) { box.appendChild(el('div', 'st-note', esc(detail.note))); return box; }
  for (const [title, lines] of (detail.sections || [])) {
    const section = el('div', 'st-sec');
    section.appendChild(el('h6', '', esc(title)));
    const out = el('div', 'st-out');
    (lines || []).forEach((line) => out.appendChild(el('span', '', esc(line))));
    section.appendChild(out);
    box.appendChild(section);
  }
  const paths = detail.files || [];
  if (paths.length) {
    const list = el('div', 'st-files');
    paths.forEach((path) => list.appendChild(stepFileRow(path)));
    box.appendChild(list);
  }
  return box;
}

/* A file named by a step is the same control as a chip: it opens the side viewer, because reading a
   change has one door in this window now. */
function stepFileRow(path) {
  const known = ((DATA.review || {}).files || []).find((f) => f.path === path);
  const row = el('button', 'rfile', (known ? kindTag(known.kind) : '') + `<code>${esc(path)}</code>`);
  row.onclick = () => {
    const at = ((DATA.review || {}).files || []).findIndex((f) => f.path === path);
    if (at < 0) { toast('That file is no longer in the change set.', 'bad'); return; }
    openFile(at);
  };
  return row;
}

/* One listener for the whole thread, attached once: renderThread rebuilds every row on each state
   push, and a handler bound to the button itself would be rebuilt with it. The index is read from
   the dataset and resolved against DATA.messages, so what lands on the clipboard is the message
   text the server wrote — never the rendered markup, and never a stale row. */
$('thread').addEventListener('click', (event) => {
  const copy = event.target.closest('[data-copy-row]');
  if (copy) {
    const message = DATA.messages[Number(copy.dataset.copyRow)];
    if (!message) return;
    navigator.clipboard.writeText(message.text || '');
    toast('Copied to clipboard');
    return;
  }
  const again = event.target.closest('[data-again-row]');
  if (again) {
    const message = DATA.messages[Number(again.dataset.againRow)];
    if (!message || !message.text) return;
    const ta = $('prompt');
    if (ta) {
      ta.value = message.text;
      autosize();
      sendQuiet('set_draft', { text: message.text });
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
      toast('Message loaded into composer');
    }
    return;
  }
  const quoted = event.target.closest('[data-quote-row]');
  if (quoted) quoteRow(Number(quoted.dataset.quoteRow));
});

/* The last thing you sent, back in the box. A model that stalls or answers nonsense is answered by
   asking again, and at temperature 0 "again" means the same bytes — so this refills rather than
   fires: nothing leaves without your Enter, and a half-typed message is never overwritten. */
function lastAsked() {
  for (let i = DATA.messages.length - 1; i >= 0; i--) {
    const m = DATA.messages[i];
    if (m.role === 'user' && (m.text || '').trim()) return m.text;
  }
  return '';
}

function askAgain() {
  const ta = $('prompt');
  const text = lastAsked();
  // No refusal while a task runs: the composer stayed typable through the whole run once the queue
  // landed, so pressing Enter after this refill queues the repeat instead of being swallowed.
  if (!text) { toast('Nothing has been sent in this chat yet', 'warn'); return; }
  if (ta.value.trim() && ta.value.trim() !== text.trim()) {
    toast('Your message is still unsent — send it, or clear the box first', 'warn');
    ta.focus();
    return;
  }
  ta.value = text;
  autosize();
  // The server keeps the unsent text to warn before a project switch discards it, so the refill
  // has to be the draft too — and marked as ours, or the echo guard would drop it.
  state.lastDraftSent = text;
  sendQuiet('set_draft', { text });
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  toast('The last message is back in the box — press Send to ask again');
}

function renderComposer() {
  const bar = $('cbar'); bar.innerHTML = '';
  const add = (node) => bar.appendChild(node);
  add(modeBadge());
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
  // modeBadge() has already chosen the normal placeholder for this mode; a run overrides it.
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

function modeBadge() {
  const branch = DATA.branch || {};
  const change = DATA.composer === 'change', reading = DATA.composer === 'read';
  const name = branch.projectName ? ' · ' + esc(branch.projectName) : '';
  const b = el('button', 'pill mode ' + (reading ? 'read' : change ? 'change' : 'chat'));
  b.innerHTML = modeRow(DATA.composer)[1] + name + ICON.chev;
  if (branch.bound) {
    // A bound chat is not a mode choice: the folder's remembered mode and switch stay with the
    // project branch, and asking for Change here is refused. So no chevron and no click.
    b.innerHTML = modeRow(DATA.composer)[1] + name;
    b.title = 'A chat moved into ' + (branch.projectName || 'a project') + ' answers in prose and reads '
      + 'that folder as context. A message that asks for files is still planned as a proposal you '
      + 'approve, but the mode and Auto-Apply belong to the project — open it in the sidebar to change them.';
    b.onclick = () => toast('That belongs to the project. Open ' + (branch.projectName || 'it') + ' in the sidebar.');
    const ta0 = $('prompt');
    if (ta0) ta0.placeholder = 'Ask about ' + branch.projectName + ' — or ask it for a change you will approve.';
    return b;
  }
  b.title = modeRow(DATA.composer)[2] + ' Click to choose what the next Send will do.';
  const declared = DATA.declared || {};
  if (declared.sealed) {
    // A folder can carry a position written by another surface — the other window, or a terminal that
    // never opened one. Without the lock here that seal looks like a button that forgot to work, so
    // the badge says who set it and when before it says why the click did nothing.
    b.classList.add('sealed');
    b.insertAdjacentHTML('afterbegin', ICON.lock + ' ');
    if (declared.note) b.title = declared.note;
    else if (declared.by) b.title = modeRow(DATA.composer)[2] + ' Set by ' + declared.by + '.';
  }
  b.onclick = () => {
    if (!DATA.project) { toast('Choose a project first — then Chat, Read-only and Change each mean something'); return; }
    modeMenu();
  };
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
        : 'Ask anything. Choose a project to work on its files.';
  return b;
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
        startBtn.title = 'Run plan steps sequentially';
        startBtn.onclick = () => startSequential();
        seqDiv.appendChild(startBtn);
      } else {
        const stopBtn = el('button', 'line-btn plan-seq-btn running', '■ Stop sequential');
        stopBtn.title = 'Stop sequential execution';
        stopBtn.onclick = () => stopSequential();
        seqDiv.appendChild(stopBtn);
      }
      p.appendChild(seqDiv);
      const list = el('div', 'tasks-list');
      for (const s of DATA.plan.steps) {
        const isDone = s.status === 'verified';
        const isNow = !isDone && (s.current || s.id === DATA.plan.step);
        const row = el('div', 'task-item ' + (isDone ? 'done' : isNow ? 'now' : 'pending'));
        row.appendChild(el('span', 'task-status-icon', isDone ? '✓' : isNow ? '⏳' : String(s.id)));
        row.appendChild(el('span', 'task-title', esc(s.title)));
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

/* --------------------------------- wiring -------------------------------- */
setupRailResizer();
$('seg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) switchView(b.dataset.view); });
$('new-chat').onclick = () => send('new_chat');
$('collapse').onclick = toggleSidebar;
$('sidebar-expand').onclick = toggleSidebar;
if ($('attach-top')) $('attach-top').onclick = () => send('pick_plan');
if ($('app-settings-btn')) $('app-settings-btn').onclick = openSettings;

const scroller = $('scroller');
const jumpBtn = $('jump-latest');
if (scroller && jumpBtn) {
  scroller.addEventListener('scroll', () => {
    if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > 180) {
      jumpBtn.classList.add('show');
    } else {
      jumpBtn.classList.remove('show');
    }
  });
  jumpBtn.onclick = () => {
    toBottom(scroller);
    jumpBtn.classList.remove('show');
  };
}

window.addEventListener('beforeunload', (e) => {
  if (DATA && DATA.busy) {
    const proj = (DATA.project && DATA.project.name) || 'the active project';
    const msg = `Tasks are still running in ${proj}. Are you sure you want to leave?`;
    e.preventDefault();
    e.returnValue = msg;
    return msg;
  }
});

$('search').addEventListener('input', (e) => { state.query = e.target.value; renderNav(); });
$('prompt').addEventListener('input', () => {
  autosize();
  // The server needs the unsent text to warn before a project switch discards it.
  clearTimeout(state.draftTimer);
  state.lastDraftSent = $('prompt').value;
  state.draftTimer = setTimeout(() => sendQuiet('set_draft', { text: $('prompt').value }), 400);
});
$('prompt').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
});
addEventListener('keydown', (e) => {
  /* AltGr on an Arabic layout is reported as Ctrl+Alt, so an unguarded `ctrlKey` test turned the
     key that types ÷ into the command palette and Ctrl+Alt+7 into a hidden sidebar. Held-down
     repeats are excluded for the same reason: one keypress, one panel. */
  if (e.altKey || e.repeat || handsBusy()) return;
  const key = (e.key || '').toLowerCase();
  const mod = e.ctrlKey || e.metaKey;
  /* Shift is part of the binding, not a modifier to ignore: without the `!e.shiftKey` guards
     Ctrl+Shift+K matched the plain Ctrl+K branch first and opened the palette on the way to
     starting a chat. */
  if (mod && key === 'k' && !e.shiftKey) { e.preventDefault(); palette(); }
  if (mod && key === 'k' && e.shiftKey) { e.preventDefault(); send('new_chat'); }
  if (mod && key === 'b' && !e.shiftKey) { e.preventDefault(); toggleSidebar(); }
});

const PARAMS = new URLSearchParams(location.search);
TOKEN = PARAMS.get('t') || '';

connectEvents();
api('/api/bootstrap').then((data) => {
  render(data);
  // Lets a design review open straight onto one screen of one style: ?style=codex&theme=dark&view=review
  const saved = JSON.parse(localStorage.getItem('ui.prefs') || '{}');
  state.themeMode = PARAMS.get('theme') || saved.theme || 'light';
  renderThemePick();
  if (PARAMS.get('view')) showView(PARAMS.get('view'), true);
  requestAnimationFrame(() => document.documentElement.classList.remove('booting'));
}).catch((e) => toast('Could not reach the local server: ' + e.message, 'bad'));

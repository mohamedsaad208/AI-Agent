/* The page's own foundation and its one link to the server: the DOM helpers every other file
   uses, the single `state` object, the SSE transport, the event dispatch, and `render` -- the one
   place a server snapshot becomes a screen.
   Everything else is drawn from that state and never keeps a truth of its own. */
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

/* The id of the last frame this page read. A reconnect the browser makes itself carries that in a
   header, but the reconnect *here* is a new EventSource, which carries nothing — so the page names
   the id in the URL and the server replays whatever fell in the gap. Without that, a blip mid-run
   silently costs the window every event between the last snapshot and the reconnection. */
let lastEventId = '';

function connectEvents() {
  const url = '/api/events?t=' + encodeURIComponent(TOKEN)
    + (lastEventId ? '&last=' + encodeURIComponent(lastEventId) : '');
  const src = new EventSource(url);
  /* A dropped stream reconnects on its own, and every event it missed was a whole snapshot. The
     window then keeps showing the last one it saw — measured live: the review card said "Apply
     changes" was unavailable while the server was sitting on a pending proposal waiting for that
     very click. Re-reading the bootstrap when a stream opens costs one request and ends the drift. */
  src.onopen = () => { streamRetries = 0; api('/api/bootstrap').then(render).catch(() => {}); };
  src.onmessage = (row) => {
    if (row.lastEventId) lastEventId = row.lastEventId;
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
    // Engine events that bear on the permanent run row, taken live rather than waiting for the
    // next full snapshot: a stage move repaints the badge, a read steering instruction decrements
    // the waiting count the Steer field shows.
    case 'stage_changed': applyStageCode(msg.stage); break;
    case 'steer':
      if (DATA && DATA.steering_waiting) { DATA.steering_waiting -= 1; paintRunBar(); }
      break;
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
  renderPlanColumn();
  renderRail(); renderLog(); takePreviewOffer();
  paintRunBar();
  syncSettings();
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

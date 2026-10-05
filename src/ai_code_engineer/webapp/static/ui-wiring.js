/* The last file, and the only one that does anything at load time.
   Every listener is attached here in one place, after the declarations above exist, so the
   initialisation order of the window is this file's order and nothing else's. */
/* --------------------------------- wiring -------------------------------- */
setupRailResizer();
$('seg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) switchView(b.dataset.view); });
$('new-chat').onclick = () => send('new_chat');
$('collapse').onclick = toggleSidebar;
$('sidebar-expand').onclick = toggleSidebar;
if ($('attach-top')) $('attach-top').onclick = () => send('pick_plan');
if ($('app-settings-btn')) $('app-settings-btn').onclick = () => openSettings('project');

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
  /* One-key approvals. They fire only over a live proposal the window would let you Apply — the
     same `canApply` the buttons read — and only with no modifier, so they never collide with the
     Ctrl bindings above or with typing (the `handsBusy` guard at the top already holds for fields). */
  if (!mod && !e.shiftKey && DATA.review && DATA.review.canApply) {
    if (key === 'y') { e.preventDefault(); send('apply'); }
    else if (key === 'n') { e.preventDefault(); send('reject'); }
    else if (key === 'd') { e.preventDefault(); openFile(DATA.review.selected || 0); }
  }
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

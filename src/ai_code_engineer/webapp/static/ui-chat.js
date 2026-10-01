/* The conversation: markdown and code highlighting, the message thread, the reply and quotation
   controls, the step rows that open on a click, and the streamed answer as it is written.
   The sentences come from the server; this file only ever arranges them. */
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
          const toggle = el('button', 'step-prompt-toggle', ICON.chev);
          toggle.title = 'Show details';
          toggle.setAttribute('aria-label', 'Show details');
          toggle.setAttribute('aria-expanded', 'false');
          const bodyDetails = el('div', 'step-prompt-body hidden', esc(rest));
          toggle.onclick = () => {
            const isHidden = bodyDetails.classList.toggle('hidden');
            toggle.title = isHidden ? 'Show details' : 'Hide details';
            toggle.setAttribute('aria-label', toggle.title);
            toggle.setAttribute('aria-expanded', String(!isHidden));
            toggle.style.transform = isHidden ? '' : 'rotate(180deg)';
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

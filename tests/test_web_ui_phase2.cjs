// Pure rendering tests: no browser, network, or project writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/ai_code_engineer/webapp/static/app.js'), 'utf8');
class Element {
  constructor(tag, cls, html) { Object.assign(this, {tag, cls, html, children: [], attributes: {}}); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  setAttribute(key, value) { this.attributes[key] = value; }
}
const context = vm.createContext({
  el: (tag, cls, html) => new Element(tag, cls, html),
  esc: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  tone: () => '', ICON: {file: ''}, state: {expandedProposal: ''},
  DATA: {artifact: {state: 'Pending'}, banner: {}, messages: []},
  renderThread: () => {}, openFile: index => { context.opened = index; }, send: () => {},
});
for (const name of ['kindTag', 'diffStat', 'diffTotals', 'fileCaption', 'chipCard', 'changeActions', 'splitReply', 'replyTarget']) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
}
const files = Array.from({length:5}, (_, i) => ({path:`file${i}.js`, summary:'Added <safe> behavior', kind:'M', add:i+1, del:i}));
const review = {id:'proposal-1', pending:true, canApply:true, files};
const walk = node => [node, ...node.children.flatMap(walk)];
const find = (node, cls) => walk(node).filter(n => n.cls === cls);
let card = context.chipCard(review);
assert.equal(find(card, 'file-chip').length, 3);
assert.match(find(card, 'chat-task-head')[0].html, /5 files changed/);
assert.match(find(card, 'chat-task-head')[0].html, /\+15/);
assert.match(find(card, 'chat-task-head')[0].html, /−10/);
assert.match(find(card, 'file-chip')[0].html, /&lt;safe>/);
find(card, 'more-files')[0].onclick();
card = context.chipCard(review);
assert.equal(find(card, 'file-chip').length, 5);
find(card, 'file-chip')[4].onclick();
assert.equal(context.opened, 4);
assert.equal(find(card, 'more-files')[0].attributes['aria-expanded'], 'true');
find(card, 'more-files')[0].onclick();
assert.equal(find(context.chipCard(review), 'file-chip').length, 3);
context.DATA.artifact.written = true;
context.DATA.banner.text = 'Auto-applied';
card = context.chipCard({...review, pending:false, canApply:false, canRollback:true});
assert.match(find(card, 'chat-task-head')[0].html, /Auto-Applied/);
assert.deepEqual(find(card, 'pv-acts')[0].children.map(n => n.html), ['↩ Roll back']);
assert.equal(find(context.chipCard({...review, id:'new-proposal'}), 'file-chip').length, 3);
for (const prefix of ['In reference to the agent', 'بالإشارة إلى الوكيل']) {
  const reply = context.splitReply(`> [${prefix}: "A quoted sentence"]\n\nExplain it`);
  assert.equal(reply.preview, 'A quoted sentence');
  assert.equal(reply.text, 'Explain it');
}
assert.equal(context.splitReply('> Ordinary markdown quote').preview, '');
context.DATA.messages = [{text:'Original sentence with more words'}, {text:'Something else'}];
assert.equal(context.replyTarget('Original sentence…', 2), 0);
assert.equal(context.replyTarget('Missing message', 2), -1);
console.log('Phase 2 rendering: compact files, totals, actions, escaping, navigation, and quotes passed.');

// Phase 3 UI tests: Resizable rail, Tasks tab with [نفذ دي], and unread activity dots.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { source, css, html } = require('./ui_source.cjs');

// 1. Contract tests on index.html and app.css
assert.ok(html.includes('id="rail-resizer"'), 'index.html has rail-resizer');
assert.ok(css.includes('.rail-resizer'), 'app.css has .rail-resizer');
assert.ok(css.includes('.unread-dot'), 'app.css has .unread-dot');
assert.ok(css.includes('.rail-tabs'), 'app.css has .rail-tabs');
assert.ok(css.includes('.step-exec-btn'), 'app.css has .step-exec-btn');
assert.ok(css.includes('.task-item'), 'app.css has .task-item');

// 2. Pure rendering tests in vm context
class Element {
  constructor(tag, cls, html) {
    this._html = html;
    Object.assign(this, { tag, cls, children: [], attributes: {} });
  }
  set innerHTML(val) { this.children = []; this._html = val; }
  get innerHTML() { return this._html ?? ''; }
  set html(val) { this._html = val; }
  get html() { return this._html ?? ''; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  setAttribute(key, value) { this.attributes[key] = value; }
  focus() {}
}

const submitted = [];
const context = vm.createContext({
  el: (tag, cls, html) => new Element(tag, cls, html),
  esc: value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  tone: () => '',
  ICON: { file: '', gear: '', chev: '' },
  state: {
    railFile: -1,
    railTab: 'diff',
    railSection: 'tasks',
    unread: { changes: false, tasks: true, activity: false }
  },
  DATA: {
    artifact: { state: 'Pending', title: 'Test Artifact' },
    banner: {},
    review: { files: [] },
    plan: {
      name: 'plan.md',
      step: 2,
      total: 3,
      verified: 1,
      note: 'In progress',
      steps: [
        { id: 1, title: 'Setup database', status: 'verified' },
        { id: 2, title: 'Implement endpoints', current: true },
        { id: 3, title: 'Write tests' }
      ]
    },
    recipes: [],
    project: { name: 'Demo' },
    branch: {}
  },
  $: (id) => {
    if (!context._elements[id]) context._elements[id] = new Element('div', id);
    return context._elements[id];
  },
  _elements: {},
  autosize: () => {},
  submit: () => { submitted.push(context._elements['prompt']?.value); },
  toast: () => {},
  send: () => {},
  openSettings: () => {},
  openFile: (i) => {},
  kindTag: (k) => '',
  fileCaption: (f) => '',
  diffStat: () => '',
  changeActions: () => {},
  railPreviewCard: () => new Element('div', 'preview')
});

for (const name of ['runPlanStep', 'startSequential', 'stopSequential', 'planWide',
                    'planEmptyCard', 'planCard', 'renderRail']) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 2), context);
}

// 1. Initial render with 'changes' section active and 'tasks' unread
context.state.railSection = 'changes';
context.state.unread = { changes: false, tasks: true, activity: false };
context.renderRail();

const rail = context.$('rail');
const walk = node => [node, ...node.children.flatMap(walk)];
const find = (node, cls) => walk(node).filter(n => (n.cls || '').split(' ').includes(cls));

// Check rail tabs rendered
const tabs = find(rail, 'rail-tab');
assert.equal(tabs.length, 3, 'Three rail tabs: changes, tasks, checks');
assert.ok(tabs[0].cls.includes('on'), 'Changes tab is currently active');

// Check unread dot rendered for inactive Tasks tab
assert.ok(tabs[1].html.includes('unread-dot'), 'Inactive Tasks tab shows unread dot');

// 2. Click Tasks tab: switches section and clears unread
tabs[1].onclick();
assert.equal(context.state.railSection, 'tasks');
assert.equal(context.state.unread.tasks, false, 'Unread flag cleared for tasks');

// In Tasks view, active Tasks tab has no unread dot
const tasksTab = find(context.$('rail'), 'rail-tab')[1];
assert.ok(tasksTab.cls.includes('on'), 'Tasks tab is now active');
assert.ok(!tasksTab.html.includes('unread-dot'), 'Active Tasks tab does not show dot');

// Check sequential controls button before start
const seqBtns = find(context.$('rail'), 'plan-seq-btn');
assert.equal(seqBtns.length, 1, 'Sequential execution button rendered');
assert.ok(seqBtns[0].html.includes('Start sequential'), 'Start sequential button label present');

// Start sequential execution
seqBtns[0].onclick();
assert.equal(context.state.sequential, true, 'Sequential state is now active');
assert.equal(context.state.seqStepId, 2, 'Next step picked is step 2');

// Re-render and check that the button switched to Stop
context.renderRail();
const stopBtns = find(context.$('rail'), 'plan-seq-btn');
assert.equal(stopBtns.length, 1);
assert.ok(stopBtns[0].cls.includes('running'), 'Sequential button has running class');
assert.ok(stopBtns[0].html.includes('Stop sequential'), 'Stop sequential button label present');

// Click stop button
stopBtns[0].onclick();
assert.equal(context.state.sequential, false, 'Sequential state cleared after stop');

// Check task items
const items = find(context.$('rail'), 'task-item');
assert.equal(items.length, 3, '3 tasks in plan list');
assert.ok(items[0].cls.includes('done'), 'Step 1 marked done');
assert.ok(items[1].cls.includes('now'), 'Step 2 marked current');
assert.ok(items[2].cls.includes('pending'), 'Step 3 marked pending');

// Check execute buttons on pending/now steps
const execBtns = find(context.$('rail'), 'step-exec-btn');
assert.equal(execBtns.length, 2, '2 actionable steps have execute buttons');

// Test clicking [نفذ دي]
execBtns[0].onclick({ stopPropagation: () => {} });
assert.equal(submitted.length, 2);
assert.equal(submitted[1], 'Implement endpoints');

console.log('Phase 3 verification passed: Resizable handle, Tasks tab, execute buttons, and unread dots.');

const assert = require('node:assert/strict');
const vm = require('node:vm');
const { source } = require('./ui_source.cjs');
const context = vm.createContext({});
const escStart = source.indexOf('const esc =');
vm.runInContext(source.slice(escStart, source.indexOf('\n/*', escStart)), context);
const start = source.indexOf('function inline(');
const end = source.indexOf('const KEYWORDS', start);
vm.runInContext(source.slice(start, end), context);
for (const newline of ['\n', '\r\n']) {
  const html = context.inline(['## Tasks', '### Setup', '1. **Create project**', '   - Add dependencies',
    '2) Create service', '## Details', '3. Add JWT', '<script>alert(1)</script>'].join(newline));
  for (const content of ['<h3>Tasks</h3>', '<h3>Setup</h3>', 'Create project', 'Add dependencies',
    'Create service', '<h3>Details</h3>', 'Add JWT', '&lt;script&gt;']) assert.ok(html.includes(content), content);
  assert.ok(!html.includes('<script>'));
}
console.log('Plan Markdown: headings, adjacent lists, Windows newlines, and escaping passed.');

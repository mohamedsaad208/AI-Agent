// The window's own files, read the way the page reads them.
//
// The front end is seven plain scripts loaded in order now. A rendering test that reads one of them
// stops covering the other six the day a function changes file, so every harness asks for the whole
// page instead of naming a script -- the same reason tests/test_webapp.py has ui_script().
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', 'src', 'ai_code_engineer', 'webapp', 'static');
const UI_SCRIPTS = ['ui-core.js', 'ui-projects.js', 'ui-chat.js', 'ui-composer.js',
                    'ui-run.js', 'ui-dialogs.js', 'ui-wiring.js'];

module.exports = {
  STATIC,
  UI_SCRIPTS,
  // Normalised to \n on purpose. These harnesses slice a function out of the source by looking for a
  // line that is just `}`, and on a Windows checkout (`core.autocrlf=true`) every line ends `\r\n`, so
  // the marker is never found and the function never reaches the VM. The wheel and CI see `\n`.
  source: UI_SCRIPTS.map((name) => fs.readFileSync(path.join(STATIC, name), 'utf8'))
    .join('\n').replace(/\r\n/g, '\n'),
  css: fs.readFileSync(path.join(STATIC, 'app.css'), 'utf8').replace(/\r\n/g, '\n'),
  html: fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8').replace(/\r\n/g, '\n'),
};

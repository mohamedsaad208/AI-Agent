# Task: Fix 6 Specific UI, Tool Validation, and Layout Issues

Implement the following 6 fixes across the codebase:

### Issue 1: Chat Message Layout (User message styling, alignment, icons)
In `src/ai_code_engineer/webapp/static/app.css`:
Update `.msg.me` and `.bubline` for user messages so that:
- `.msg.me`: `flex-direction: row;` (so avatar 'Y' stays on the left).
- `.msg.me .body`: `align-items: flex-end;` (so the user message bubble aligns to the right).
- `.msg.me .bubline`: `flex-direction: row-reverse;` (so action buttons / hotkey icons `.macts` sit to the left of the user message bubble).

### Issue 2: Plan Check Bar Width & Placement (Move to tab in Right Rail)
In `src/ai_code_engineer/webapp/static/app.css` and `src/ai_code_engineer/webapp/static/ui-run.js`:
- Hide `.plan-col` by default (`.plan-col { display: none !important; }`) and remove `var(--plan-w)` from `.app` grid template columns so the chat area expands to take the full center space:
  `.app { grid-template-columns: var(--side-w) minmax(0, 1fr) var(--rail-w); height: 100vh }`
  `html.side-collapsed .app { grid-template-columns: 62px minmax(0, 1fr) var(--rail-w) }`
- Ensure the plan checklist is viewed in the Right Rail under the Tasks / Plan tab (`state.railSection === 'tasks'`).

### Issue 3: Fix list_files MalformedCall Error & English Language Default
- In `src/ai_code_engineer/contracts.py`:
  Define `TASK_STATE_KEYS = ('status', 'constraints', 'decisions', 'evidence', 'open_issues', 'next_step', 'files_examined', 'folded', 'acceptance', 'goal')`.
  In `ToolContract.validate(envelope)`:
  Strip/pop `TASK_STATE_KEYS` from the body before checking unclaimed extra fields. This prevents models (like qwen2.5-coder) from failing tool validation with `MalformedCall: list_files does not accept: acceptance, constraints, decisions...` when they include task state scratchpad fields alongside tool actions.
- In `src/ai_code_engineer/engine.py`:
  In `parse_action` or turn processing: if action dictionary contains any `TASK_STATE_KEYS`, merge them into `state` via `taskstate.merge(state, action)` so the model's scratchpad progress is preserved.
- In `src/ai_code_engineer/webapp/controller.py`:
  Ensure default language for policy texts, actions, and UI notices is English (allow, ask, deny), and `self.arabic` defaults to False unless explicitly configured.

### Issue 4: Fix File List Bubble Distortion & Overflow (Image 2)
In `src/ai_code_engineer/webapp/static/app.css`:
- On `.st-head`: change `border-radius` from `99px` to `var(--r-m, 8px)` or `10px` so multi-line file lists don't produce a distorted oval border that cuts into text.
- Add `word-break: break-word; overflow-wrap: anywhere;` to `.st-head` and `.st-txt` so long paths wrap cleanly without escaping the bubble border.

### Issue 5: Right Rail Design & Overflow Fixes (Image 3)
In `src/ai_code_engineer/webapp/static/app.css`:
- `.custom-cmd-row`: add `flex-wrap: wrap; gap: 6px;`.
- `.custom-cmd-input`: `min-width: 0; flex: 1 1 120px;`.
- `.custom-cmd-sub`: `min-width: 0; max-width: 80px; flex: 0 1 auto;`.
- `.link .r`: `overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px;`.
- `.card .link`: `min-width: 0; max-width: 100%; overflow: hidden;`.

### Issue 6: Fix Settings Dialog Opening Blank (Image 4)
- In `src/ai_code_engineer/webapp/static/ui-dialogs.js`:
  In `openSettings(tab)`: ensure if `tab` is not a string (e.g. MouseEvent from onclick handler), default it to `'project'`:
  `if (typeof tab !== 'string') tab = 'project';`
- In `src/ai_code_engineer/webapp/static/ui-wiring.js`:
  `$('app-settings-btn').onclick = () => openSettings('project');`

Run all relevant tests and make sure the test suite remains 100% green.
Strict typing, clean architecture, standard library only.

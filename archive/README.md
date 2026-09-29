# archive/

Dead material the repository keeps for a measured claim but no longer develops from.

Nothing moved here during the phase-2 cleanup, and that is a finding rather than an oversight:
`spring-rpoject/` and `project-2/` — the two folders the cleanup list asked to relocate — are
referenced **by absolute path from the tool's own history**:

```
.agent-projects.json      "path": "D:\\AI\\AI-Agent\\spring-rpoject"   (the granted-project registry)
.agent-chats/<id>/chat.json   "key": "d:\\ai\\ai-agent\\project-2"     (four saved conversations)
```

Moving them would leave the sidebar pointing at folders that no longer exist and would break the one
thing the dogfood run was explicitly asked to preserve: opening the window afterwards and reading the
whole chat history of each project. A typo in a directory name is cheaper than a dangling history.

So: rename on disk only if you also rewrite `.agent-projects.json` and every `chat.json` that names
the old path, and that is a deliberate step rather than a cleanup side effect.

## What else the list asked for and already existed

| proposed | actual |
| --- | --- |
| `examples/` | exists — `examples/demo2`, `examples/demo_repo`, both driven by the tests |
| `sandbox/` for experiments | exists — probe scripts, cited by `docs/` as the source of measured claims |
| `tools/` for internal development aids | exists — contrast checker, dogfood ledger, demo/GIF generators |
| `docs/` | exists, and is where every plan and status document lives |
| review `.gitignore` for local runtime files | already covers `.agent-*`, `.screens/`, `.design-preview/*.png`, `__pycache__/`, `build/`, `dist/`, `.venv/`, `.env` |

## What does belong here

A folder the agent was pointed at, whose session is closed, and whose history has been exported or
does not need to be readable in the window. Move it here, and record in its `README` which chats
referenced it, so the next cleanup does not have to rediscover the coupling.

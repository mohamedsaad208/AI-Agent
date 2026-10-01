"""A scripted controller so the interface is reviewable before the real one exists.

Nothing here reads or writes a project. It answers the same five methods the eventual
engine-backed controller will, with canned data that mirrors a real task, so design
decisions can be made against a running window instead of a screenshot.
"""
from __future__ import annotations

import difflib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config, git_integration, intent, labels, modes, overrides, repair, runner, setup
from ..errors import PolicyError
from .controller import MODES, PROJECT_ICONS as ICONS

BEFORE = """package com.demo.users;

import org.springframework.stereotype.Service;
import java.util.UUID;

@Service
public class UserService {

    private final UserRepository repo;

    public UserService(UserRepository repo) {
        this.repo = repo;
    }

    public User create(String email) {
        return repo.save(new User(UUID.randomUUID(), email));
    }
}
"""

AFTER = """package com.demo.users;

import java.util.Optional;
import org.springframework.stereotype.Service;
import java.util.UUID;

@Service
public class UserService {

    private final UserRepository repo;

    public UserService(UserRepository repo) {
        this.repo = repo;
    }

    public User register(RegisterRequest req) {
        if (repo.existsByEmail(req.email())) {
            throw new DuplicateEmailException(req.email());
        }
        return repo.save(User.from(req));
    }

    public User create(String email) {
        return repo.save(new User(UUID.randomUUID(), email));
    }
}
"""

CTRL_BEFORE = """@PostMapping("/register")
public ResponseEntity<UserDto> register(@RequestBody RegisterRequest req) {
    return ResponseEntity.ok(dto(userService.create(req.email())));
}
"""
CTRL_AFTER = """@PostMapping("/register")
public ResponseEntity<UserDto> register(@RequestBody RegisterRequest req) {
    return ResponseEntity.ok(dto(userService.register(req)));
}

@ExceptionHandler(DuplicateEmailException.class)
public ResponseEntity<Void> duplicate(DuplicateEmailException exc) {
    return ResponseEntity.status(HttpStatus.CONFLICT).build();
}
"""
EXC = """package com.demo.users;

public class DuplicateEmailException extends RuntimeException {
    public DuplicateEmailException(String email) {
        super("An account already exists for " + email);
    }
}
"""

FILES = [
    {"path": "src/main/java/com/demo/users/UserService.java", "before": BEFORE, "after": AFTER},
    {"path": "src/main/java/com/demo/users/RegisterController.java", "before": CTRL_BEFORE, "after": CTRL_AFTER},
    {"path": "src/main/java/com/demo/users/DuplicateEmailException.java", "before": None, "after": EXC},
]

# The reasoning row, scripted. A thinking model answers twice and only one answer is the action, so the
# preview needs the other half visible to be designed against: capped, redacted, and opened as its own
# section rather than folded into the envelope.
REASONING_SAMPLE = ("The register flow needs the email check before the token mint, so the typed error "
                    "has to live outside the service or the controller cannot map it to 409.\n"
                    "Adding a guard inside save() would leave the duplicate row half-written, so the "
                    "check belongs at the top of register().\n"
                    "The password is never in this file — the store hashes it, so nothing to redact here.")

# The module graph, scripted. `show_graph` answers with this so the sheet, the column layout, the edge
# widths and the caption's caveat clauses can all be reviewed in the preview window with no engine and no
# folder attached. The back edge from `common-lib` to `auth` is deliberate: a shared library that imports
# a service is the cycle this project's own dogfood target really has, and a graph that only ever draws a
# clean tree never shows what it does when the layout cannot be exact.
GRAPH_NODES = [
    {"name": "common-lib", "files": 9, "column": 0},
    {"name": "auth", "files": 6, "column": 1},
    {"name": "users-service", "files": 12, "column": 2},
    {"name": "api-gateway", "files": 5, "column": 3},
]
GRAPH_EDGES = [
    {"from": "users-service", "to": "common-lib", "count": 6},
    {"from": "auth", "to": "common-lib", "count": 4},
    {"from": "api-gateway", "to": "users-service", "count": 3},
    {"from": "users-service", "to": "auth", "count": 2},
    {"from": "common-lib", "to": "auth", "count": 1},
]
GRAPH_COLUMNS = 4
GRAPH_HIDDEN = 2

# The state names and their tones come from labels.py rather than a copy of them here, because a
# second table drifts: this one had no BLOCKED row and no DISCOVERING tone, so the two previews of
# a blocked task disagreed with the real window.
STATES = labels.STATES
TONE = labels.TONE


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _diff(before: str, after: str, name: str) -> list[str]:
    return list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     fromfile="a/" + name, tofile="b/" + name, lineterm=""))


class FakeController:
    """In-memory only. Restarts clean, which is exactly what a design review wants."""

    def __init__(self) -> None:
        self.prefs = {"style": "claude", "theme": "light", "collapsed": False}
        self.state = "WAITING_APPROVAL"
        self.view = "task"
        self.busy = False
        self.cancellable = False
        self.chained = True
        # The container switch is scripted as if this machine had Docker: on a machine without it the
        # preview would show one frozen sentence, and the four answers are the thing to review here.
        # The real window's `available` comes from `runner.sandbox_available()`.
        self.sandbox_on = False
        self.sandbox_image = ""
        self.composer = "chat"
        # The scripted twin of the controller's `declared` block: the folder's own position, kept by
        # whichever surface wrote it. The preview needs the field or the lock on the badge cannot be
        # reviewed at all, and a surface nobody has seen is exactly what a design review should catch.
        self.declared = {"mode": "", "by": "", "at": ""}
        # The real window keys the composer placeholder and the auto-write note off this switch, so
        # the preview has to carry a live one or neither can be reviewed here.
        self.auto_apply = False
        # The chip is reviewable only if the scripted window can move it, so the branch is state
        # here rather than a literal in snapshot(): see the `git_branch` action below.
        self.branch = "main"
        self.git_base = ""
        # The restore pill is drawn from here. The real window only offers a restore after a rollback
        # refuses because something edited the files afterwards, which a scripted window has no disk
        # to reproduce — so `rollback` here raises the offer on purpose, to keep the surface reviewable.
        self.git_restore_offer = None
        # A declined proposal keeps its diff on screen and loses the offer to write it. The preview has
        # to answer `reject` like the real window does, or the card gets reviewed against a button the
        # shipped thing will not press.
        self.declined = False
        self.timeout = 300
        self.recipe = "Maven test"
        # Three modules so the monorepo picker can be reviewed here: the scripted window answers to
        # what the real one would list, and choosing a module changes the commands offered.
        self.targets = [{"path": ".", "label": "demo2 (whole project)",
                         "recipes": ["Maven test", "Maven compile"]},
                        {"path": "backend", "label": "backend", "recipes": ["Maven test"]},
                        {"path": "frontend", "label": "frontend", "recipes": ["npm test",
                                                                              "Node test runner"]}]
        self.target = "."
        # The model picker is a list you search, so the preview needs a catalog and a filter that
        # behaves like the real view — the settings used to hand back one constant.
        self.mode = "Ollama"
        # The same table the real window builds, so the preview is reviewing the provider list and
        # not a shorter copy of it.
        self.modes = list(MODES)
        self.catalogs = {label: (OLLAMA_MODELS if kind.shape == "ollama" else
                                 FREE_MODELS if label == config.free_mode(kind) else
                                 PAID_MODELS if label == config.paid_mode(kind) else SERVED_MODELS)
                         for label, kind in config.mode_rows()}
        self.endpoints = {kind.key: config.default_endpoint(kind) for kind in config.KINDS
                           if config.default_endpoint(kind)}
        self.profile = ""
        self.catalog_source = {label: "live" for label in MODES}
        self.model = "qwen2.5-coder:1.5b"
        self.selections = {self.mode: self.model}
        self.model_filter = ""
        self.draft = ""
        self.consent = False
        self.key = ""
        self.memory = "Java 17, Spring Boot 3.2. Do not add dependencies. " \
                      "Keep controllers thin; put rules in the service layer."
        self.memory_info = "412 chars saved · demo2-8f2a.md · sent with every task here"
        # The counts a provider reports for the scripted task, in the shape the real snapshot sends.
        # The numbers are the ones the context-window comment had to measure by hand.
        self.metrics = {"prompt_tokens": 2050, "completion_tokens": 180}
        self.step = 2
        self.runs = 0
        self.fix_round = 0
        self.auto_fix = False
        self.tab = "diff"
        self.pending: str | None = None
        # The activity strip draws from this, so the preview needs a live-looking line of its own.
        self.status_line = "Turn 3/12: asking qwen2.5-coder:1.5b..."
        # The rows the Overrides section shows, held in memory: a preview that wrote to this machine's
        # real signed file would be a preview that changed how the real window runs.
        self.override_rows = [{"target": "ollama", "key": "timeout_seconds", "value": 600,
                               "state": "in force", "by": "cli", "at": "2026-09-30 09:40", "why": ""},
                              {"target": "*", "key": "context_chars", "value": 40000,
                               "state": "in force", "by": "web", "at": "2026-09-30 09:12", "why": ""},
                              {"target": "*", "key": "endpoint", "value": "",
                               "state": "refused", "by": "", "at": "",
                               "why": "per-provider"}]
        # Two scripted rows so the strip can be laid out and reviewed: a plain wait, and one the
        # user has sent to a chat of its own.
        self.queue = [
            {"id": "q1", "text": "And while you are in there, add a test for the duplicate case.",
             "at": "14:04:11", "detached": False},
            {"id": "q2", "text": "Why does the controller return 409 instead of 400?",
             "at": "14:04:32", "detached": True},
        ]
        self.queue_held = False
        self.log = [{"ts": "14:02:11", "kind": "plan", "text": "Reading attached plan: plan.md"}]
        self.log_dropped = 0
        # The one step row the preview has open. The real window fetches it from the session's records;
        # here it is scripted, so the chevron, the sections and the file rows can be laid out.
        self.step_detail: dict | None = None
        self.messages = [
            {"role": "assistant", "author": "AI Code Engineer", "time": "14:02",
             "text": "Project **demo2** is attached, so this becomes a reviewable change request. "
                     "Nothing is written to disk until you approve the proposal."},
            {"role": "user", "author": "You", "time": "14:02",
             "text": "Implement step 2: register the user and reject duplicate emails. Keep the existing controller shape."},
            # The step rows the engine now announces. Four of them, because the question this window
            # exists to answer is what the thread looks like while a task works. Each carries the same
            # `step` handle the real controller attaches, or the chevrons cannot be reviewed here at all.
            {"role": "tool", "author": "Steps", "text": "\U0001f4c1 Scanning project files...",
             "step": {"id": "st-1", "action": "list_files", "fields": {"count": 42},
                      "detail": labels.step_has_detail("list_files", {"count": 42})}},
            # No digest on this one, so it draws without a chevron: the preview has to show what a row
            # with nothing behind it looks like, or that state gets designed blind.
            {"role": "tool", "author": "Steps",
             "text": "\U0001f4d6 Reading file: src/main/java/com/demo/users/UserRepository.java",
             "step": {"id": "st-2", "action": "read_file",
                      "fields": {"path": "src/main/java/com/demo/users/UserRepository.java"},
                      "detail": labels.step_has_detail("read_file", {"path": "x"})}},
            {"role": "tool", "author": "Steps", "text": "\U0001f50d Searching code: existsByEmail",
             "step": {"id": "st-3", "action": "search_code",
                      "fields": {"query": "existsByEmail", "count": 2},
                      "detail": labels.step_has_detail("search_code", {"count": 2})}},
            {"role": "tool", "author": "Steps",
             # Built through `labels` rather than typed out, because the preview is what a reviewer reads
             # before approving a row's shape: a scripted sentence the engine could not produce reviews a
             # fiction. The count is the record's own length so the two cannot disagree.
             "text": labels.step_line(False, "model_reasoning", count=len(REASONING_SAMPLE),
                                      detail=REASONING_SAMPLE),
             "step": {"id": "st-r", "action": "model_reasoning",
                      "fields": {"count": len(REASONING_SAMPLE), "detail": REASONING_SAMPLE},
                      "detail": labels.step_has_detail("model_reasoning", {"detail": "x"})}},
            {"role": "tool", "author": "Steps",
             "text": "\u270d\ufe0f Proposed changes for 3 file(s): UserService.java, RegisterController.java, "
                     "DuplicateEmailException.java",
             "step": {"id": "st-4", "action": "propose",
                      "fields": {"count": 3, "names": ["UserService.java", "RegisterController.java",
                                                       "DuplicateEmailException.java"]},
                      "detail": labels.step_has_detail("propose", {"names": ["UserService.java"]})}},
            {"role": "tool", "author": "Steps",
             "text": "\u2699\ufe0f Ran mvn -B test — failed · exit 1 · 41.2s · 14 tests",
             "step": {"id": "st-5", "action": "executed", "fields": {"command": "mvn -B test"},
                      "detail": labels.step_has_detail("executed", {"command": "mvn -B test"})}},
            {"role": "assistant", "author": "AI Code Engineer", "time": "14:03",
             "text": "I read `UserRepository.java` and `RegisterRequest.java` first, then proposed 3 files. "
                     "Duplicate emails now raise a typed error the controller maps to **409**.\n\n"
                     "```java\n// duplicate guard added before persisting\n"
                     "public User register(RegisterRequest req) {\n"
                     "  if (repo.existsByEmail(req.email())) {\n"
                     "    throw new DuplicateEmailException(req.email());\n"
                     "  }\n  return repo.save(User.from(req));\n}\n```\n\n"
                     "- `RegisterController.java` keeps the existing method shape.\n"
                     "- A new exception type means no string matching in the controller."},
            {"role": "tool", "author": "Changes",
             "text": "3 files · UserService.java, RegisterController.java, DuplicateEmailException.java"},
        ]
        self._replies: dict[str, threading.Event] = {}
        self._answers: dict[str, dict] = {}
        self._emit = None
        # The first-run card, built from the real `setup` rows so the preview cannot drift from the
        # sentences that actually ship. Only the provider and model lines are scripted: they are the
        # two a machine without Ollama running would otherwise leave blank on a design surface.
        self.setup_open = True
        self.setup_rows = self._script_setup()
        self.setup_demo: dict | None = None

    def _script_setup(self) -> list[dict]:
        rows = setup.audit(repo="", provider="ollama",
                           endpoint=config.default_endpoint(config.OLLAMA),
                           model=self.model, probe=False)
        out = []
        for row in rows:
            if row["id"] == "provider":
                row = {"id": "provider", "status": "ok", "advice": "",
                       "text": "Ollama answers at " + config.default_endpoint(config.OLLAMA) + "."}
            elif row["id"] == "model":
                row = {"id": "model", "status": "ok", "advice": "",
                       "text": "11 models (10 of them local). Using " + self.model + "."}
            elif row["id"] == "demo":
                row = dict(row, text="The offline proof has not been run yet.")
            out.append(row)
        return out

    # ----------------------------- contract -----------------------------
    @property
    def reading_only(self) -> bool:
        """The same question the real controller asks every write and every run gate."""
        return intent.read_only(self.composer)

    def snapshot(self) -> dict:
        return {
            "prefs": self.prefs, "busy": self.busy, "cancellable": self.cancellable, "pending": self.pending,
            "status": self.status_line,
            "current": "s-1", "header": {"title": "Spring Boot authentication",
                                         "subtitle": "demo2 · step %d of 5 · %s" % (self.step, self.model)},
            "project": {"name": "demo2", "path": "D:\\AI\\AI-Agent\\examples\\demo2"},
            # The branch in front and the composer under it are what the header, the mode badge
            # and the drop targets read; a preview without them shows none of that furniture.
            "branch": {"kind": "project", "key": "demo2", "id": "s-1", "bound": False,
                       "projectName": "demo2"},
            "composer": self.composer,
            "declared": {"sealed": self.declared["mode"] == intent.READ,
                         "mode": self.declared["mode"], "by": intent.source(self.declared["by"]),
                         "at": self.declared["at"],
                         "note": (intent.followed(self.declared["by"], self.declared["at"])
                                  if self.declared["mode"] == intent.READ
                                  and not intent.read_only(self.composer) else "")},
            "icons": list(ICONS),
            "git": dict({"repo": True, "branch": self.branch, "detached": False,
                         "head": "9f3c21a", "dirty": 2},
                        **({"base": self.git_base} if self.git_base else {}),
                        **({"restore": self.git_restore_offer}
                           if self.git_restore_offer else {})),
            "banner": ({"count": len(FILES),
                        "text": labels.applied_note(arabic=False, count=len(FILES))}
                       if self.auto_apply and self.state in labels.MUTABLE_STATES
                       else {"count": 0, "text": ""}),
            "plan": {"name": "plan.md", "step": self.step, "total": 5, "verified": self.step - 1,
                     "note": "Send works on step %d" % self.step,
                     "steps": [{"id": 1, "title": "Project foundation", "status": "verified", "current": False},
                               {"id": 2, "title": "Register a user", "status": "in_progress", "current": self.step == 2},
                               {"id": 3, "title": "Login and issue a JWT", "status": "pending", "current": self.step == 3},
                               {"id": 4, "title": "Protect the routes", "status": "pending", "current": self.step == 4},
                               {"id": 5, "title": "Refresh-token rotation", "status": "pending", "current": self.step == 5}]},
            "provider": {"mode": self.mode, "modes": self.modes, "model": self.model,
                         "models": self.visible_models(), "metrics": dict(self.metrics)},
            "connection": self.connection_info(),
            "overrides": self.overrides_info(),
            "recipes": self.target_recipes(), "recipe": self.recipe,
            "targets": [{"path": row["path"], "label": row["label"]} for row in self.targets],
            "target": self.target, "targetLabel": self.target_label(),
            "canRun": self.state in {"APPLIED_UNVERIFIED", "CHECKS_PASSED", "VERIFICATION_FAILED", "VERIFICATION_BLOCKED"},
            "runInfo": "No command has run yet." if not self.runs else
                       f"{self.runs} run(s). Last: Maven test — passed (exit 0, 41.2s)",
            "runWarning": labels.run_warning(arabic=False),
            "sandbox": {"on": bool(self.sandbox_on), "image": self.sandbox_image, "available": True,
                        "note": labels.note(runner.sandbox_state(self.sandbox_on,
                                                                 self.sandbox_image, True))},
            # The loop's budget, from the same constant the real controller reads it from: a field the
            # preview never sends is a field the window is never drawn with.
            "fixRounds": {"of": repair.MAX_FIX_ROUNDS, "spent": self.fix_round},
            "memory": {"info": "412 chars saved"},
            "settings": {"project": "D:\\AI\\AI-Agent\\examples\\demo2", "plan": "plan.md", "chained": self.chained, "auto_apply": self.auto_apply,
                         "timeout": self.timeout, "model_info": self._model_info(),
                         "memory": self.memory,
                         "memory_info": self.memory_info,
                         "consent": self.consent},
            "draft": self.draft,
            "queue": {"items": self.queue, "held": self.queue_held, "elsewhere": 1,
                      **labels.queue_notes(False, 1, False)},
            "artifact": labels.artifact_card(self.state, arabic=False, count=len(FILES),
                                             project="demo2",
                                             summary="Duplicate emails now raise a typed error the "
                                                     "controller maps to 409; the existing create() "
                                                     "behaviour is untouched.",
                                             written=self.state in labels.MUTABLE_STATES,
                                             has_project=True, rejected=self.declined),
            # The preview is where a card like this gets reviewed, so it has to carry the same shape the
            # real controller sends — including the counts the header prints.
            "setup": {"show": self.setup_open, "rows": self.setup_rows,
                      "counts": setup.counts(self.setup_rows),
                      "tally": setup.tally(setup.counts(self.setup_rows)),
                      "demo": self.setup_demo, "busy": False},
            "review": self._review(), "messages": self.messages, "log": self.log,
            "log_dropped": self.log_dropped, "log_note": "", "step_detail": self.step_detail,
            "projects": [{"key": "demo2", "name": "demo2", "initials": "d2",
                          "path": "D:\\AI\\AI-Agent\\examples\\demo2",
                          "chats": [{"id": "s-1", "title": "Spring Boot authentication", "state": self.state,
                                     "updated": _ago(120), "busy": self.busy},
                                    {"id": "s-2", "title": "Add refresh-token rotation", "state": "APPLIED_UNVERIFIED",
                                     "updated": _ago(300), "busy": False},
                                    {"id": "s-3", "title": "Fix password hashing", "state": "CHECKS_PASSED",
                                     "updated": _ago(1440), "busy": False}]},
                         {"key": "demo_repo", "name": "demo_repo", "initials": "dr",
                          "path": "D:\\AI\\AI-Agent\\examples\\demo_repo",
                          "chats": [{"id": "s-4", "title": "Fix add in calculator.py", "state": "CHECKS_PASSED",
                                     "updated": _ago(4300), "busy": False},
                                    {"id": "s-5", "title": "Extract a UserService", "state": "ROLLED_BACK",
                                     "updated": _ago(5760), "busy": False}]}],
            "chats": [{"id": "c-1", "title": "Explain Maven surefire reports", "updated": _ago(360)},
                      {"id": "c-2", "title": "Best way to gate a plan step?", "updated": _ago(2880)}],
            "runStatus": {"canRun": True, "canRunApp": True, "canRunTests": True, "canBuild": True, "reasons": [], "disabledMessage": ""},
            "services": [],
            "runConfig": {},
            "lastJob": None,
            "readiness": None,
        }

    def _review(self) -> dict:
        reading = self.reading_only
        files = []
        for change in FILES:
            lines = _diff(change["before"] or "", change["after"], change["path"])
            add = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            dele = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            files.append({"path": Path(change["path"]).name, "kind": "A" if change["before"] is None else "M",
                          "add": add, "del": dele})
        chosen = FILES[min(self._file, len(FILES) - 1)]
        name = Path(chosen["path"]).name
        return {
            "state": STATES.get(self.state, self.state), "tone": TONE.get(self.state, ""),
            "title": "Implement step 2: register the user and reject duplicate emails",
            "detail": f"{len(FILES)} files · attached plan plan.md · proposal 8f2a…c41b",
            "canApply": self.state == "WAITING_APPROVAL" and not reading and not self.declined,
            "canMutate": self.state in labels.MUTABLE_STATES,
            # Roll back answers to a wider set than the other two, because an interrupted apply is
            # exactly when the escape has to be on screen. Without this field the preview window —
            # the one the design is reviewed in — shows a button that can never light up.
            "canRollback": (self.state in labels.MUTABLE_STATES | labels.INTERRUPTED_STATES) and not reading,
            "rejected": self.declined,
            "canReopen": self.state == "WAITING_APPROVAL" and self.declined and not reading,
            "files": files, "selected": self._file, "tab": self.tab,
            "view": {"diff": _diff(chosen["before"] or "", chosen["after"], name),
                     "before": (chosen["before"] or "").splitlines(),
                     "after": chosen["after"].splitlines(),
                     "checks": ["Proposed checks (not execution results):",
                                "• mvn -B test passes", "• duplicate email returns 409",
                                "• existing create() behaviour unchanged",
                                "", "Latest check: not run yet." if not self.runs else
                                f"Latest check: passed · {self.runs} run(s) recorded"]},
        }

    _file = 0

    def visible_models(self, mode: str | None = None) -> list[dict]:
        """The catalog as filtered — the same read-only view `controller.visible_models` computes.

        The preview has to narrow the way the real window does, or the filter gets reviewed here as
        a box that lists everything and shipped as one that loses models.
        """
        query = self.model_filter.strip().casefold()
        entries = self.catalogs.get(mode if mode is not None else self.mode, [])
        if not query:
            return list(entries)
        return [entry for entry in entries
                if query in " ".join([entry.get("id", ""), entry.get("name", ""),
                                      entry.get("description", "")]).casefold()]

    def _model_info(self) -> str:
        entry = next((item for item in self.catalogs.get(self.mode, [])
                      if item["id"] == self.model), None)
        if entry:
            return entry["name"] + " — " + entry["description"]
        loaded = len(self.catalogs.get(self.mode, []))
        shown = len(self.visible_models())
        if shown != loaded:
            return (f'{shown} of {loaded} models match "{self.model_filter.strip()}". '
                    "Clear the filter to see the rest.")
        return f"{loaded} models available. Select one from the list."

    def overrides_info(self) -> dict:
        """The preview's own rows, kept in memory.

        Reviewing the Overrides section against a scripted list is what makes the design checkable
        without touching this machine's real signing key or its real settings.
        """
        return {"rows": overrides.spoken(self.override_rows, arabic=False),
                "keys": overrides.fields(),
                "targets": [overrides.EVERY] + [kind.key for kind in config.KINDS],
                "path": "(preview) " + overrides.FILE, "kind": self.kind().key,
                "note": overrides.scope(arabic=False)}

    def kind(self):
        return config.MODE_KIND.get(self.mode, config.DEFAULT_KIND)

    def set_override(self, payload: dict) -> None:
        """Sign nothing here: the preview stores the same shape the real window writes to disk."""
        target = str(payload.get("target", "")).strip().casefold() or overrides.EVERY
        key = str(payload.get("key", "")).strip().casefold()
        raw = payload.get("value", "")
        field = next((item for item in overrides.fields() if item["key"] == key), None)
        if field is None or (target == overrides.EVERY and key in overrides.PER_PROVIDER):
            self.status_line = overrides.bad_value(key, "unknown" if field is None else
                                                   "per-provider", arabic=False)
            return
        if field["number"]:
            try:
                value = int(str(raw).strip())
            except ValueError:
                self.status_line = overrides.bad_value(key, "not a number", arabic=False)
                return
            low, high = config.LIMITS[key]
            if not low <= value <= high:
                self.status_line = f"{key} must be between {low} and {high}."
                return
        else:
            value = str(raw).strip()
        self.override_rows = [row for row in self.override_rows
                              if not (row["target"] == target and row["key"] == key)]
        self.override_rows.insert(0, {"target": target, "key": key, "value": value, "state": "in force",
                                      "by": "web", "at": "just now", "why": ""})
        self.status_line = overrides.written({"key": key, "value": value, "target": target},
                                             arabic=False)

    def unset_override(self, payload: dict) -> None:
        target = str(payload.get("target", "")).strip().casefold() or overrides.EVERY
        key = str(payload.get("key", "")).strip().casefold()
        before = len(self.override_rows)
        self.override_rows = [row for row in self.override_rows
                              if not (row["target"] == target and row["key"] == key)]
        self.status_line = (overrides.removed(key, arabic=False) if len(self.override_rows) < before
                            else overrides.absent(key, arabic=False))

    def connection_info(self) -> dict:
        """The same block the real controller sends, so the Connection tab is reviewable here.

        The scripted window never contacts a provider: an endpoint change re-reads the row's own
        rules and nothing else, which is what makes it safe to click in a preview.
        """
        kind = config.MODE_KIND.get(self.mode, config.DEFAULT_KIND)
        endpoint = self.endpoints.get(kind.key, "") or config.default_endpoint(kind)
        return {"kind": kind.key, "label": kind.label, "endpoint": endpoint,
                "default_endpoint": config.default_endpoint(kind), "cloud": kind.cloud, "shape": kind.shape,
                "needs_key": kind.needs_key, "key_env": kind.key_env,
                "consent": config.needs_consent(kind, endpoint),
                "paid": self.mode == config.paid_mode(kind),
                "profile": self.profile, "profiles": config.profile_names(),
                "source": self.catalog_source.get(self.mode, ""),
                "key_present": bool(self.key.strip())}

    def set_profile(self, label: str) -> None:
        """Move the preview onto a profile's row — the same three fields the real window sets."""
        self.profile = label
        if not label:
            return
        try:
            settings = config.load_profile(label)
        except Exception:                                  # noqa: BLE001 - a preview reports, never raises
            return
        self.mode = config.mode_for(settings.provider, settings.model) or self.mode
        if settings.endpoint:
            self.endpoints[config.MODE_KIND[self.mode].key] = settings.endpoint
        self.model = settings.model

    def project_info(self, key: str) -> dict:
        """One drawer payload per scripted project, with the same keys the real one sends.

        The drawer reads every field it draws, so an answer that is missing one shows up as a
        blank instead of an error and the drift goes unnoticed.
        """
        if key not in PROJECT_INFO:
            raise PolicyError("Unknown project.")
        return dict(PROJECT_INFO[key])

    def list_dir(self, path: str, want_files=None) -> dict:
        root = Path(path or Path.home())
        if not root.is_dir():
            root = Path.home()
        dirs = sorted((p for p in root.iterdir() if _visible_dir(p)), key=lambda p: p.name.casefold())
        files = sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower() in {".md", ".txt"}),
                       key=lambda p: p.name.casefold()) if want_files else []
        return {"path": str(root), "parent": str(root.parent) if root.parent != root else None,
                "dirs": [{"name": p.name, "path": str(p)} for p in dirs[:300]],
                "files": [{"name": p.name, "path": str(p)} for p in files[:300]]}

    def set_reply(self, request_id: str, reply: dict) -> None:
        self._answers[request_id] = reply
        waiter = self._replies.pop(request_id, None)
        if waiter:
            waiter.set()

    # ----------------------------- actions -----------------------------
    def action(self, type: str, payload: dict, emit) -> dict | None:
        self._emit = emit
        if type == "set_override":
            self.set_override(payload)
            return None
        if type == "unset_override":
            self.unset_override(payload)
            return None
        if type == "send":
            return self._send(payload.get("text", ""), emit, payload.get("quote_of"))
        if type == "apply":
            if self.reading_only:
                return self._refuse(intent.no_write("Apply"))
            if self.declined:
                return self._refuse("This proposal was declined. Reopen it for review first.")
            return self._confirm_then("apply", emit)
        if type == "reopen":
            if self.reading_only:
                return self._refuse(intent.no_write("Reopen"))
            if self.state == "WAITING_APPROVAL" and self.declined:
                self.declined = False
                self.status_line = "Proposal reopened for review. Nothing was written."
                self._note(emit, "proposal_reopened", self.status_line)
            return None
        if type == "reject":
            if self.reading_only:
                # The preview refuses a refusal for the same reason the real window does: the seal is a
                # position about this folder, not about whichever diff is on screen.
                return self._refuse(intent.no_write("Reject"))
            self.declined = True
            text = labels.rejected_note(arabic=False, count=len(FILES))
            self.messages.append({"role": "tool", "author": "Tool", "text": text, "time": _clock()})
            emit({"kind": "message", "message": self.messages[-1]})
            self._note(emit, "rejected", text)
            self.status_line = "Proposal declined. Nothing was written."
            return None
        if type == "run":
            return self._run(payload.get("fix"), emit)
        if type == "rollback":
            if self.reading_only:
                return self._refuse(intent.no_write("Roll back"))
            self.state = "ROLLED_BACK"
            self._note(emit, "rolled_back", "Task changes rolled back.")
            self.git_restore_offer = {"commit": "9f3c21a", "paths": len(FILES)}
        elif type == "git_restore":
            if self.reading_only:
                # The preview has to refuse the escalation exactly like the real window does, or the
                # design gets reviewed against a button the shipped thing will not press.
                return self._refuse(intent.no_write("Restoring files from git"))
            offer = self.git_restore_offer or {"commit": "9f3c21a", "paths": len(FILES)}
            self.git_restore_offer = None
            text = labels.restore_done(arabic=False, commit=offer["commit"],
                                       restored=[item["path"] for item in FILES], skipped=[])
            self.messages.append({"role": "tool", "author": "Git", "text": text, "time": _clock()})
            emit({"kind": "message", "message": self.messages[-1]})
            self._note(emit, "git_restore", text)
        elif type == "git_branch":
            if self.reading_only:
                # A switch rewrites the tracked files, so a read-only preview refuses it for the same
                # reason the controller does.
                return self._refuse(intent.no_write("Switching branches"))
            # The scripted branch comes from the same generator the real window uses, so reviewing
            # the chip here shows the name a user would actually get.
            back = str(payload.get("back", "")).strip()
            if back:
                self.branch, self.git_base = back, ""
                text = labels.branch_switched(arabic=False, branch=back)
            else:
                self.git_base = self.branch
                self.branch = git_integration.task_branch_name("Review the preview script",
                                                              "fakesession01")
                text = labels.branch_started(arabic=False, branch=self.branch, back=self.git_base)
            self.messages.append({"role": "tool", "author": "Git", "text": text, "time": _clock()})
            emit({"kind": "message", "message": self.messages[-1]})
            self._note(emit, "git_branch", text)
        elif type == "verify":
            self.state = "VERIFICATION_BLOCKED"
            self._note(emit, "verify", "Syntax checks finished. Project tests have not run.")
        elif type == "stop":
            self.busy = self.cancellable = False
            self.pending = None
            self.state = "CANCELLED"
            self._note(emit, "cancelled", "Task cancelled. No project files were changed.")
        elif type == "set_style":
            self.prefs["style"] = payload.get("style", "claude")
        elif type == "set_theme":
            self.prefs["theme"] = payload.get("theme", "light")
        elif type == "pick_project":
            self._ask("folder", {"title": "Choose a project folder", "hint": "Nothing is read until you approve a proposal."}, emit)
        elif type == "select_file":
            self._file = int(payload.get("index", 0))
        elif type == "select_tab":
            self.tab = str(payload.get("tab", "diff"))
        elif type == "queue_add":
            text = self.with_quote(str(payload.get("text", "")).strip(), payload.get("quote_of"))
            if text:
                self.queue.append({"id": "q%d" % (len(self.queue) + 3), "text": text,
                                   "at": "now", "detached": False})
                self.queue_held = False
        elif type == "queue_edit":
            for item in self.queue:
                if item["id"] == payload.get("id"):
                    item["text"] = str(payload.get("text", "")).strip()
            self.queue_held = False
        elif type == "queue_drop":
            self.queue = [item for item in self.queue if item["id"] != payload.get("id")]
        elif type == "queue_now":
            picked = [item for item in self.queue if item["id"] == payload.get("id")]
            if picked:
                self.queue.remove(picked[0])
                self.queue.insert(0, picked[0])
            self.queue_held = False
        elif type == "queue_chat":
            for item in self.queue:
                if item["id"] == payload.get("id"):
                    item["detached"] = True
            self.queue_held = False
        elif type == "queue_resume":
            self.queue_held = False
        elif type == "set_chained":
            self.chained = bool(payload.get("value"))
        elif type == "sandbox":
            # Both halves in one action, exactly as the real window sends them: the sentence under the
            # box is the thing being reviewed, and it changes on the tick and on every keystroke.
            if "on" in payload:
                self.sandbox_on = bool(payload.get("on"))
            if "image" in payload:
                self.sandbox_image = str(payload.get("image") or "")
        elif type == "set_auto_apply":
            # The pill next to Send and the composer placeholder both key off this, so a preview
            # that ignored the click could not be used to review either of them.
            if self.reading_only and not bool(payload.get("value")):
                pass                  # turning a switch that is already off asks nothing of anyone
            elif self.reading_only:
                self._refuse(intent.no_auto_apply())
            else:
                self.auto_apply = bool(payload.get("value"))
        elif type == "set_composer":
            self.composer = intent.normalise(payload.get("value"))
            if self.composer in (intent.READ, intent.CHANGE):
                # Read and Change are the two positions that say what happens to the folder's files,
                # so either is written down — the same rule the real window keeps in `modes`.
                self.declared = {"mode": self.composer, "by": modes.WEB, "at": _clock()}
            # The switch belongs to Change mode, exactly as it does in the real window: a folder
            # moved to Read-only mid-session has to stop showing "writes itself" on the next pill.
            self.auto_apply = self.auto_apply and self.composer == "change"
        elif type == "set_timeout":
            self.timeout = int(payload.get("value") or 300)
        elif type == "set_recipe":
            self.recipe = str(payload.get("value") or self.recipe)
        elif type == "set_target":
            self.set_target(str(payload.get("value") or ""))
        elif type == "set_filter":
            self.model_filter = str(payload.get("value", ""))
        elif type == "set_model":
            self.model = str(payload.get("value", ""))
            self.selections[self.mode] = self.model
        elif type == "set_mode":
            value = str(payload.get("value", ""))
            if value in self.modes:
                self.selections[self.mode] = self.model
                self.mode, self.model_filter, self.consent = value, "", False
                self.model = self.selections.get(value, "")
        elif type == "set_endpoint":
            # Checked by the same rule the real window uses, so a rejected URL is refused here too
            # and the tab can be reviewed against a refusal rather than only against a success.
            kind = config.MODE_KIND.get(self.mode, config.DEFAULT_KIND)
            text = str(payload.get("value", "")).strip() or config.default_endpoint(kind)
            try:
                self.endpoints[kind.key] = config.check_endpoint(kind, text)
            except PolicyError as exc:
                emit({"kind": "toast", "text": str(exc)})
            else:
                emit({"kind": "toast", "text": "%s now points at %s" % (kind.label, self.endpoints[kind.key])})
        elif type == "set_profile":
            self.set_profile(str(payload.get("value", "")))
        elif type == "refresh_models":
            emit({"kind": "toast",
                  "text": "%d models listed for %s in this preview — the real window asks the "
                          "provider." % (len(self.visible_models()), self.mode)})
        elif type == "set_draft":
            self.draft = str(payload.get("text", ""))
        elif type == "set_key":
            self.key = str(payload.get("value", ""))
        elif type == "set_consent":
            self.consent = bool(payload.get("value"))
        elif type == "set_collapsed":
            self.prefs["collapsed"] = bool(payload.get("value"))
        elif type == "save_memory":
            self.memory = str(payload.get("text", ""))
            self.memory_info = ("%d chars saved · demo2-8f2a.md · sent with every task here"
                                % len(self.memory)) if self.memory else "Notes cleared for this folder."
        elif type == "apply_block":
            # The block button writes nothing here either: it opens a proposal, which is the state
            # the review cards are drawn for.
            if self.reading_only:
                return self._refuse(intent.no_proposal())
            self.state = "WAITING_APPROVAL"
            emit({"kind": "toast", "text": "Proposing %s — review the diff, then Apply"
                  % str(payload.get("path", "that file"))})
        elif type == "setup_check":
            self.setup_open = True
            emit({"kind": "toast", "text": "Checks done — 4 ok, 1 to watch, 0 blocking. "
                                           "The provider rows are scripted here."})
        elif type == "setup_demo":
            # Scripted, and it says so: the preview never writes anywhere, so the proof it shows is the
            # sentence the real run prints rather than a run of its own.
            self.setup_demo = {"proposal_apply_rollback": "passed", "llm_used": False,
                               "static_checks": {"status": "unverified"},
                               "note": "Scripted in this preview: the real proof runs in a temporary "
                                       "folder."}
            self.setup_rows = [setup.demo_row(self.setup_demo, arabic=False) if item["id"] == "demo" else item
                               for item in self.setup_rows]
            self.setup_open = True
            emit({"kind": "state", "data": self.snapshot()})
        elif type == "setup_hide":
            # "Don't show this again" is a decision the preview has to be able to demonstrate, not only
            # the real window: the note under the button promises it stops appearing at launch.
            self.setup_open = False
        elif type == "step_detail":
            self.open_step(str(payload.get("id", "")))
        elif type == "show_graph":
            # The one scripted verb that answers with data rather than with an event: the sheet is drawn
            # from the reply, so the preview has to hand back the same shape the real window does.
            return self.open_graph()
        elif type == "run_app":
            return {"name": "app", "started": True}
        elif type == "stop_app":
            return {"stopped": True}
        elif type == "restart_app":
            return {"restarted": True}
        elif type == "stop_all_apps":
            return {"stopped_all": True}
        elif type == "run_build":
            return {"success": True, "exit_code": 0, "output": "Build passed"}
        elif type == "get_run_config":
            return {}
        elif type == "save_run_config":
            return {"saved": True}
        elif type == "get_readiness":
            return {"tools": [], "wrappers": [], "configs": [], "recommendations": []}
        elif type == "service_lines":
            return {"lines": [], "status": "running"}
        elif type == "api_test":
            return {"status": 200, "headers": {}, "body": "OK"}
        elif type == "fix_errors":
            return {"status": "fix_requested"}
        elif type in PREVIEW_ONLY:
            emit({"kind": "toast", "text": PREVIEW_ONLY[type]})
        else:
            # An action nobody scripted used to fall off the end silently, which is how the model
            # filter sat dead in this window for a week: say it out loud instead.
            emit({"kind": "toast", "text": "Not scripted in this preview: " + str(type)[:40]})
        return None

    def quoted(self, index: object) -> tuple[str, str] | None:
        """The preview's copy of the reference lookup, from its own scripted thread.

        Same rule as the real window: the quotation is read out of the record rather than sent in, so
        what the reviewed prompt shows is a message this conversation actually had.
        """
        try:
            position = int(index)
        except (TypeError, ValueError):
            return None
        rows = self.messages
        if not 0 <= position < len(rows):
            return None
        words = " ".join(str(rows[position].get("text", "")).split())
        if not words:
            return None
        return str(rows[position].get("role", "assistant")), words[:labels.QUOTE_CHARS]

    def with_quote(self, text: str, index: object) -> str:
        quoted = self.quoted(index)
        if not quoted:
            return text
        return labels.quote_reference(False, quoted[0], quoted[1]) + text

    def _send(self, text: str, emit, quote_of: object = None) -> None:
        text = self.with_quote(text, quote_of)
        if not text:
            return None
        self.messages.append({"role": "user", "author": "You", "time": _clock(), "text": text})
        emit({"kind": "message", "message": self.messages[-1]})
        if self.reading_only:
            return self._analyse(emit)
        self.busy = self.cancellable = True
        self.declined = False        # a new message means a new proposal, which is answerable again
        self.pending = "connecting to the model…"
        emit({"kind": "busy", "value": True, "cancellable": True})

        def work():
            for line, note in [("plan", "Reading attached plan: plan.md"),
                               ("read", "Read 6 files · 18.4k characters"),
                               ("propose", "Model produced a proposal envelope")]:
                time.sleep(0.7)
                self._note(emit, line, note)
                # The step sentences arrive in the thread as the task works, and the same line is
                # what the strip shows — that pairing is the feature being reviewed here. Each row
                # carries a step handle so its chevron can be opened mid-run, not only after it.
                action = "read_file" if line == "read" else "list_files"
                fields = ({"path": "src/main/java/com/demo/users/UserRepository.java",
                           "digest": "8f2a5c31"} if action == "read_file" else {"count": 42})
                text = labels.step_line(False, action, **fields)
                self.status_line = text
                self.messages.append({"role": "tool", "author": "Steps", "text": text,
                                      "step": {"id": "live-%d" % len(self.messages),
                                               "action": action, "fields": fields,
                                               "detail": labels.step_has_detail(action, fields)}})
                emit({"kind": "message", "message": self.messages[-1]})
                emit({"kind": "status", "text": text})
            self.pending = "proposing changes…"
            emit({"kind": "status", "text": "Connecting to the model and preparing changes…"})
            # The envelope arriving in pieces, into Activity: what a reader watches during a long turn
            # on a CPU model, and the same sink the real window streams a proposal turn to.
            for fragment in ('{"action": "propose", "summary": "Guard the duplicate email",',
                             '"changes": [{"path": "UserService.java"},',
                             '{"path": "RegisterController.java"}]}'):
                time.sleep(0.25)
                emit({"kind": "log_chunk", "ts": _clock(), "text": fragment})
            time.sleep(0.4)

            self.pending = None
            self.busy = self.cancellable = False
            self.state = "WAITING_APPROVAL"
            reply = {"role": "assistant", "author": "AI Code Engineer", "time": _clock(),
                     "text": "Here is the smallest change that satisfies step 2. Three files, and the "
                             "existing `create()` path is untouched.\n\n"
                             "- A typed `DuplicateEmailException` keeps the controller free of string matching.\n"
                             "- The repository gains `existsByEmail`, so the guard is one query."}
            self.messages.append(reply)
            emit({"kind": "message", "message": reply})
            emit({"kind": "toast", "text": "Proposal ready — review the changes, then apply them if you want."})
            emit({"kind": "state", "data": self.snapshot()})

        threading.Thread(target=work, name="ui-fake-job", daemon=True).start()

    def _refuse(self, text: str) -> None:
        """A gate the preview cannot show is a gate nobody reviewed — so refusals land on screen."""
        self.status_line = text
        self._emit({"kind": "toast", "text": text})

    def _analyse(self, emit) -> None:
        """Read-only's scripted answer: the folder was read, the refusal is its own row, and no
        proposal state is entered. Everything the mode promises has to be visible here."""
        self._note(emit, "read", "Read 6 files · 18.4k characters")
        self.messages.append({"role": "tool", "author": "Tool", "time": _clock(),
                              "text": intent.no_proposal()})
        emit({"kind": "message", "message": self.messages[-1]})
        reply = {"role": "assistant", "author": "AI Code Engineer", "time": _clock(),
                 "text": "The duplicate-email guard lives in `UserService.create()`, and it compares "
                         "strings in two places that disagree about the field name.\n"
                         "That is the whole of what I found; the fix would touch `UserService.java` "
                         "and its test."}
        # Streamed the way the real window streams: whole lines, one at a time, into the bubble the
        # client draws from whatever has arrived. This is the only place an answer landing piece by
        # piece can be reviewed at the pace a person reads at. No `busy` claim — Read-only starts no
        # job, and the preview has always had to show that.
        def work():
            for line in reply["text"].split("\n"):
                time.sleep(0.35)
                emit({"kind": "token", "ts": _clock(), "text": line})
            self.messages.append(reply)
            emit({"kind": "message", "message": reply})
            emit({"kind": "state", "data": self.snapshot()})

        threading.Thread(target=work, name="ui-fake-answer", daemon=True).start()

    def _confirm_then(self, type: str, emit) -> None:
        answer = self._ask("confirm", {"title": "Apply changes",
                                       "message": "Write 3 file(s) to D:\\AI\\AI-Agent\\examples\\demo2?\n"
                                                  "You can roll back afterwards as long as the files are not edited later.",
                                       "warning": "Removes most of an existing file:\n• UserService.java keeps 12 of 26 lines",
                                       "confirm": "Apply"}, emit)
        if answer.get("ok"):
            self.state = "APPLIED_UNVERIFIED"
            self._note(emit, "apply", "Applied 3 file(s).")
            emit({"kind": "toast", "text": "Changes applied. You can run the project command now."})

    def target_row(self, path: str) -> dict:
        return next((row for row in self.targets if row["path"] == path), self.targets[0])

    def target_label(self) -> str:
        return self.target_row(self.target)["label"]

    def target_recipes(self) -> list:
        return self.target_row(self.target)["recipes"]

    def set_target(self, label: str) -> None:
        """The same rule the real window follows: pick by label, and the commands change with it."""
        chosen = next((row for row in self.targets if row["label"] == label), None)
        if chosen and chosen["path"] != self.target:
            self.target = chosen["path"]
            if self.recipe not in chosen["recipes"]:
                self.recipe = chosen["recipes"][0]

    def _run(self, fix, emit) -> None:
        if self.reading_only:
            # The one action in this mode that runs anything, so it asks per command. The refusal has
            # to be reviewable here too: a preview that only shows the yes path hides the whole rule.
            answer = self._ask("confirm", {"title": "Run this command?",
                                           "message": intent.run_ask("mvn -B test", project="demo2"),
                                           "confirm": "Run it"}, emit)
            if not answer.get("ok"):
                return self._refuse(intent.run_declined())
            # A round's output is a proposal, and this mode builds none: the scripted chip never moves.
            fix = False
        self.busy = True
        # The scripted command always passes, so a loop has to be driven by hand: "Run & fix" spends
        # the budget the same way the real window does — reset, then one round per further run — so
        # the chip can be reviewed at any value the real one can reach.
        if fix:
            self.fix_round, self.auto_fix = 0, True
        elif self.auto_fix:
            self.fix_round = min(self.fix_round + 1, repair.MAX_FIX_ROUNDS)
        emit({"kind": "busy", "value": True, "cancellable": False})

        def work():
            emit({"kind": "status", "text": "Running Maven test in demo2…"})
            time.sleep(1.1)
            self._note(emit, "run", "mvn -B test → exit 0 · 41.2s · 14 tests, 0 failures")
            self.runs += 1
            self.state = "CHECKS_PASSED"
            if self.step == 2:
                self.step = 3
                emit({"kind": "toast", "text": "Plan step 2/5 verified. Starting step 3: Login and issue a JWT"})
            self.busy = False
            emit({"kind": "busy", "value": False, "cancellable": False})
            emit({"kind": "state", "data": self.snapshot()})

        threading.Thread(target=work, name="ui-fake-run", daemon=True).start()

    def _note(self, emit, kind: str, text: str) -> None:
        row = {"ts": _clock(), "kind": kind, "text": text}
        self.log.append(row)
        emit({"kind": "log", **row})

    def open_graph(self) -> dict:
        """The preview's copy of `controller.open_graph` — same shape, scripted contents.

        The caption is written by `labels` here exactly as the real window writes it, so the sentence
        under a reviewed graph is the sentence a real project gets.
        """
        return {"nodes": list(GRAPH_NODES), "edges": list(GRAPH_EDGES),
                "columns": GRAPH_COLUMNS, "cyclic": True, "hidden": GRAPH_HIDDEN,
                "caption": labels.graph_caption(
                    False, nodes=len(GRAPH_NODES), edges=len(GRAPH_EDGES), cyclic=True,
                    hidden=GRAPH_HIDDEN)}

    def open_step(self, step_id: str) -> None:
        """The preview's copy of `controller.open_step` — same block shape, scripted contents.

        Every action the real window can open gets a block here, because this is the window the design
        is reviewed in: a row that cannot be opened in the preview is a row nobody has ever seen open.
        """
        self.step_detail = None
        if not step_id:
            # The client's close is a fetch for the empty id, and the real window answers it by showing
            # nothing. Answering it with "this step is not in the task" would review a different window.
            return
        row = next(((m.get("step") or {}) for m in self.messages
                    if (m.get("step") or {}).get("id") == step_id), None)
        if not row:
            # The same shape the real window answers with when the page is behind the task.
            self.step_detail = {"id": step_id, "sections": [], "files": [],
                                "note": labels.step_missing_line(arabic=False)}
            return
        action = str(row.get("action", ""))
        fields = row.get("fields") or {}
        block = {"id": step_id, "sections": [], "files": [], "note": ""}
        if action == "executed":
            block["sections"] = [
                [labels.detail_section(False, "command"), [str(fields.get("command", ""))]],
                [labels.detail_section(False, "result"), ["failed · exit 1 · 41.2s · 14 tests"]],
                [labels.detail_section(False, "problems"),
                 ["[ERROR] Tests run: 14, Failures: 2, Errors: 0",
                  "[ERROR]   UserRegistrationTest.rejectsDuplicateEmail:48 expected 409 but was 400"]],
                [labels.detail_section(False, "output"),
                 ["[INFO] BUILD FAILURE", "[INFO] Total time:  41.182 s",
                  "[INFO] Finished at: 2026-09-28T14:06:31+03:00"]]]
        elif action in {"propose", "applied"}:
            block["files"] = [str(name) for name in fields.get("names") or []]
        elif action == "search_code":
            block["sections"] = [[labels.detail_section(False, "search"),
                                  [str(fields.get("query", "")),
                                   "%d match(es)" % int(fields.get("count") or 0)]]]
        elif action == "list_files":
            block["sections"] = [[labels.detail_section(False, "files"),
                                  ["%d file(s) in the folder" % int(fields.get("count") or 0)]]]
        elif action == "model_reasoning":
            block["sections"] = [[labels.detail_section(False, "reasoning"),
                                  str(fields.get("detail") or "").splitlines()]]
        elif action == "read_file":
            block["sections"] = [[labels.detail_section(False, "read"),
                                  [str(fields.get("path", "")), "sha256 8f2a5c31"]]]
        self.step_detail = block

    def _ask(self, kind: str, payload: dict, emit) -> dict:
        """Push a modal request to the browser and block this thread until it answers."""
        request_id = secrets.token_hex(8)
        waiter = threading.Event()
        self._replies[request_id] = waiter
        emit({"kind": kind, "id": request_id, **payload})
        if kind == "folder":
            return {}
        waiter.wait(timeout=1800)
        return self._answers.pop(request_id, {}) or {}


"""Model catalogs the preview lists, shaped like the real ones (``id``/``name``/``description``),
so the filter the user types into has something to match against."""
OLLAMA_MODELS = [
    {"id": "qwen2.5-coder:1.5b", "name": "qwen2.5-coder:1.5b",
     "description": "Runs locally on your device.  |  0.99 GB"},
    {"id": "qwen2.5-coder:3b", "name": "qwen2.5-coder:3b",
     "description": "Runs locally on your device.  |  1.99 GB"},
    {"id": "qwen3:4b", "name": "qwen3:4b",
     "description": "Runs locally on your device.  |  2.50 GB"},
    {"id": "codegemma:2b", "name": "codegemma:2b",
     "description": "Runs locally on your device.  |  1.61 GB"},
    {"id": "codellama:13b", "name": "codellama:13b",
     "description": "Runs locally on your device.  |  7.37 GB"},
    {"id": "granite-code:3b", "name": "granite-code:3b",
     "description": "Runs locally on your device.  |  1.96 GB"},
    {"id": "llama3.1:70b-cloud", "name": "llama3.1:70b-cloud",
     "description": "Ollama cloud model — internet and Ollama account access required.  |  43.0 GB"},
]
FREE_MODELS = [
    {"id": "deepseek/deepseek-coder:free", "name": "DeepSeek Coder",
     "description": "16k context · free variant"},
    {"id": "qwen/qwen-2.5-coder-32b-instruct:free", "name": "Qwen2.5 Coder 32B",
     "description": "128k context · free variant"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B",
     "description": "131k context · free variant"},
]
PAID_MODELS = [
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
     "description": "200k context · $3/M input"},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "description": "128k context · $2.50/M input"},
]
# What an OpenAI-shaped server lists: identifiers and nothing else, which is exactly what a real
# `/models` response carries. Pricing is not in that payload, so the preview does not invent any.
SERVED_MODELS = [
    {"id": "qwen2.5-coder-3b-instruct", "name": "qwen2.5-coder-3b-instruct",
     "description": "Listed by this provider's /models endpoint. Pricing is not reported there — "
                    "check the service."},
    {"id": "deepseek-coder", "name": "deepseek-coder",
     "description": "Listed by this provider's /models endpoint. Pricing is not reported there — "
                    "check the service."},
]
MODELS = OLLAMA_MODELS

"""Gestures this window answers with a sentence rather than a state change.

They are the folder and plan pickers, the sidebar's navigation and the sample — each interesting
part is a dialog or a disk read the scripted thread has no equivalent of. A silent no-op is the
failure this list exists to prevent: something that looked broken in review would pass review.
"""
PREVIEW_ONLY = {
    "new_project": "Preview only: the real window creates a new empty folder and grants it.",
    "pick_plan": "Preview only: the real window browses that project for a plan file.",
    "clear_plan": "Preview only: this scripted thread keeps its attached plan.",
    "new_chat": "Preview only: a new chat starts with an empty thread.",
    "new_chat_in": "Preview only: the real window opens a fresh chat on that project.",
    "bind_chat": "Preview only: moving a chat onto a project is a sidebar drag.",
    "open": "Preview only: opening a saved task reads its session from disk.",
    "reveal": "Preview only: this opens Explorer on the granted folder.",
    "remove_project": "Preview only: the real window removes the project from the sidebar.",
    "example": "Preview only: the sample fills the composer with a real task.",
    "set_icon": "Preview only: the icon is saved with that project's preferences.",
    "complete_step": "Preview only: marks a plan step verified in the ledger.",
}

"""One drawer payload per scripted project. Every key the real controller sends is here,
because a missing field in the preview renders as a blank rather than an error."""
PROJECT_INFO = {
    "demo2": {
        "key": "demo2", "name": "demo2", "path": "D:\\AI\\AI-Agent\\examples\\demo2",
        "exists": True, "icon": "☕", "notes": "Spring Boot 3, Java 17. Tests are JUnit 5 and run "
        "through Maven; the surefire reports are the proof a run passed.",
        "notes_limit": 4000, "notes_file": "demo2-8f2a.md",
        "notes_dir": "D:\\AI\\AI-Agent\\.agent-memory",
        "context": {"system": 2180, "context": 6120, "turns": 4890, "kept": 3, "used": 13190,
                    "budget": 24000, "remaining": 10810, "est_tokens": 3298, "map": 6120,
                    "notes": 128, "files": 41, "bound": True},
        "toolchain": {
            "detected": [{"name": "maven-test", "label": "Maven test", "command": "mvn -B test"},
                         {"name": "maven-compile", "label": "Maven compile",
                          "command": "mvn -B -DskipTests compile"}],
            "selected": "maven-test", "timeout": 1500, "proof": "surefire XML",
            "request_timeout": 300},
    },
    "demo_repo": {
        "key": "demo_repo", "name": "demo_repo", "path": "D:\\AI\\AI-Agent\\examples\\demo_repo",
        "exists": True, "icon": "🐍", "notes": "",
        "notes_limit": 4000, "notes_file": "demo_repo-1c4d.md",
        "notes_dir": "D:\\AI\\AI-Agent\\.agent-memory",
        "context": {"system": 2180, "context": 1740, "turns": 0, "kept": 0, "used": 3920,
                    "budget": 24000, "remaining": 20080, "est_tokens": 980, "map": 1740,
                    "notes": 0, "files": 12, "bound": False},
        "toolchain": {
            "detected": [{"name": "python-unittest", "label": "Python unittest",
                          "command": "python -m unittest discover -s tests -v"}],
            "selected": "python-unittest", "timeout": 600,
            "proof": "the command's own summary line", "request_timeout": 300},
    },
}


def _visible_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    name = path.name
    return not (name.startswith(".") or name.casefold() in
                {"node_modules", "__pycache__", "venv", ".venv", "library", "windows", "program files"})


def _clock() -> str:
    return datetime.now().strftime("%H:%M")

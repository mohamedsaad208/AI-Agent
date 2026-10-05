"""Context builder: which files reach the model, in what shape, and what they are allowed to cost.

The loop that plans a change cannot ask the model to guess filenames out of a truncated repository
map, so this module answers three questions in one deterministic pass: *which* ranked files are worth
sending, *how much* of each fits the window that is left, and *what the model is told* about the shape
it is being handed. A file that fits goes whole and is labelled a snapshot the run never opened; a file
that does not goes as the block around the declaration the task named, and says so — where it opened,
that the file continues, and that a proposal against it is refused until the model reads it.

Budget enforcement is the same pass, not a check afterwards. Every block is charged whole, header
included, against the remaining characters before it is kept, because a ceiling with an uncounted label
in it is not a ceiling. Two numbers therefore bound one run: `retrieval_budget` caps what the ranked
files may spend at all, and the state block is sized from the room the built prompt actually has left,
so the record that exists to stop drift is never the thing that causes it.

Nothing here writes a session, announces a step, or calls a provider. `build` returns the pieces and
what each was chosen for; the caller appends them and records whatever they caused.

"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import memory_summarizer, prompts, repo_scanner, semantic, symbols
from .errors import PolicyError

# How many files the ranked context may carry. Three was the old cap, chosen because the rule that
# filled it was "the operator named this file" and a person rarely names four; a score can put five
# plausible files in front of a model, and a sixth costs budget for a guess. The real limit stays the
# budget, not this number.
MAX_CONTEXT_FILES = 5

# How many of those slots a *partial* file may take. A budget too small for a whole file used to mean
# the model never saw that file at all; an excerpt gives it the declarations around the ranked symbol
# instead. Three is the ceiling because an excerpt is a hint, not a read — it cannot carry a proposal
# on its own (the engine's `prepare_changes` still refuses a file that was never read whole), and a
# prompt made of four half-files reads like a repository nobody finished showing.
MAX_EXCERPTS = 3

# How much of the plan a step's retrieval is allowed to read as a bag of names. The task is capped by
# the caller because it is a document the model has to answer; the seed is only scored for the words it
# contains, and a goal tree of that length would name every file in the repository at the weakest score
# and crowd out the one the step is about.
SEED_CHARS = 1200

# The smallest room a task-state block may be given before it is given none. `taskstate.block` spends
# its whole header the moment it writes anything, so a room smaller than that buys no block at all —
# and a block truncated into its own header is a record that reads as corruption.
MIN_STATE_ROOM = 300
STATE_BLOCK_MIN = 600
STATE_BLOCK_MAX = 3000
STATE_BLOCK_SHARE = 8

FILE = "file"
EXCERPT = "excerpt"

SNAPSHOT_HEAD = "\nFile snapshot (untrusted data, already read):\n"


@dataclass
class Piece:
    """One file's contribution to the prompt, charged for before it was kept.

    `block` is the exact text to append, header and all, and `kind` is the only thing that separates
    the two shapes: a whole snapshot carries the file's `sha256` so the caller can record it as a file
    that arrived, while an excerpt carries its `lines`, `from_line` and `truncated` so the caller can
    record it as a file that did not.
    """

    path: str
    kind: str
    block: str
    reason: str
    symbol: str
    sha256: str = ""
    lines: int = 0
    from_line: int = 0
    truncated: bool = False


def chosen_reason(entry: dict) -> str:
    """Why retrieval picked this file, as the code `labels` turns into a sentence.

    Both shapes — the whole file that fitted and the block that did not — must write this the same way,
    or the same file is explained one way in one project and another way in a bigger one. The layer the
    project index supplied qualifies the code in brackets; nothing else may be appended there, because
    `labels` looks the code up as a key and an unrecognised key speaks no reason at all.
    """
    why = str(entry.get("why", ""))
    layer = str(entry.get("layer", ""))
    return f"{why} [{layer}]" if layer else why


def excerpt_block(name: str, line: int, more: bool, text: str) -> str:
    """A fragment that admits it is a fragment.

    The three admissions are the point: which line it opened on, that the file continues past what is
    shown, and that this is not a read. A model that cannot tell an excerpt from a file it has seen
    proposes against the missing half of it.
    """
    return ("\nFile excerpt (untrusted data, partial): " + name + ", from line "
            + str(line) + (" — the file continues past what is shown here" if more else "")
            + ". This file was NOT read in full; read_file it before proposing it.\n" + text)


def seed_text(task: str, *, step: int | None = None, goal: str = "",
              criteria=(), accepts=()) -> str:
    """What retrieval is scored against: the typed task, plus the plan a step number cannot say.

    A run that implements step four of a plan is not the same question as the plan, and the sentence
    the operator typed is often only "step 4" — the names the files live in are in the goal and in the
    criteria that step accepts. An empty `accepts` is not a step that needs nothing: it is a ledger that
    never said, so the whole criterion list seeds it rather than the silence the number alone gives.
    Without a step there is nothing to add and the task is scored alone.
    """
    if step is None:
        return task
    wanted = [str(item) for item in (criteria or [])]
    accepted = [wanted[number - 1] for number in (accepts or [])
                if isinstance(number, int) and 1 <= number <= len(wanted)]
    return " ".join([task, str(goal or "")] + (accepted or wanted))[:SEED_CHARS]


def rank_files(*, rows, seed: str, settings, root, index=None,
               limit: int = MAX_CONTEXT_FILES) -> list[dict]:
    """The ranked files, architecture-boosted when the project has been scanned.

    When `index` is present `index_boost` re-ranks the symbol rows so that files whose architectural
    layer matches the task intent surface first; when it is absent — the scanner has not run yet — this
    falls back silently to `symbols.rank`, so the planning loop is never worse than it was before.

    The optional semantic layer adds meaning-close files the lexical ranker missed, merged into the same
    budget rather than after it. It costs nothing unless the operator installed FastEmbed and pointed it
    at a local model folder, and an optional index must never break retrieval, so any failure in it is
    the no-op it was before the call.
    """
    entries = list(repo_scanner.index_boost(rows, seed, index, limit=limit) if index is not None
                   else symbols.rank(rows, seed, limit=limit))
    try:
        if semantic.available(settings):
            seen = {entry["path"] for entry in entries}
            for hit in semantic.rank(settings, root, seed, rows, limit=limit - len(entries)):
                if hit["path"] not in seen:
                    entries.append(hit)
                    seen.add(hit["path"])
    except Exception:   # noqa: BLE001 — an optional index must never break retrieval
        pass
    return entries


def build(*, ws, settings, rows, seed: str, used_chars: int, observed_count: int = 0,
          reference: dict | None = None, index=None) -> list[Piece]:
    """The pieces to append, in rank order, and never past the window this run was given.

    `used_chars` is what the base messages already cost, which is what leaves `retrieval_budget` for
    code; `observed_count` is the number of files the model has genuinely opened, and those slots are
    spent before ranked files are, so retrieval tops the prompt up to `MAX_CONTEXT_FILES` rather than
    doubling it. A file equal to the attached plan's path is skipped: it is already in the prompt as
    reference.

    Each candidate is read and measured before it is kept. Too big whole, it becomes an excerpt when one
    of the excerpt slots is free and the block around its ranked symbol fits what remains — and neither
    shape is a read: the pieces are what the caller records, and only a file the model asked for itself
    enters the set a proposal is honoured against.
    """
    remaining = prompts.retrieval_budget(settings, used_chars)
    ranked = rank_files(rows=rows, seed=seed, settings=settings, root=ws.root, index=index,
                        limit=MAX_CONTEXT_FILES)
    pieces: list[Piece] = []
    excerpts = 0
    for entry in ranked:
        name = entry["path"]
        if reference and name == reference["path"]:
            continue
        if observed_count + len(pieces) >= MAX_CONTEXT_FILES:
            break
        try:
            item = ws.read(name)
        except PolicyError:
            continue
        encoded = json.dumps(item)
        full_block = SNAPSHOT_HEAD + encoded
        if len(full_block) > remaining:
            # Skipping an oversized file used to be the entire answer, which is the failure a small
            # local model pays for: it proposes against a file it has never seen a line of, in a project
            # whose map said the file was right there.
            if excerpts >= MAX_EXCERPTS:
                continue
            text, line, more = symbols.snippet(item["content"],
                                               entry["symbol"] or name.rsplit("/", 1)[-1])
            if not text:
                # Nothing in the file is named by the chosen symbol, so no window can be justified —
                # and falling back to "show the head of it" is the guess this path replaced.
                continue
            block = excerpt_block(name, line, more, text)
            # Charged whole, header included: the ceiling this loop works under is a character count,
            # and a label that is not counted is a ceiling with a hole in it.
            if len(block) > remaining:
                continue
            remaining -= len(block)
            excerpts += 1
            pieces.append(Piece(path=name, kind=EXCERPT, block=block,
                                reason=chosen_reason(entry), symbol=entry["symbol"],
                                lines=len(text.splitlines()), from_line=line, truncated=more))
            continue
        remaining -= len(full_block)
        pieces.append(Piece(path=name, kind=FILE, block=full_block,
                            reason=chosen_reason(entry), symbol=entry["symbol"],
                            sha256=item["sha256"]))
    return pieces


def state_budget(settings, system_chars: int, template_chars: int) -> int:
    """How many characters the task-state block may spend, sized from the room the prompt has left.

    The state block rides inside the base, so it can never be the thing that pushes the first turn past
    the window it shares — that is the drift it exists to prevent, and a raised "Initial context
    exceeds" at the smallest legal budget is that bug made fatal. A window with no room left gets no
    block and still sends the task.
    """
    budget = min(STATE_BLOCK_MAX, max(STATE_BLOCK_MIN,
                                      settings.context_chars // STATE_BLOCK_SHARE))
    room = settings.context_chars - system_chars - template_chars
    return 0 if room < MIN_STATE_ROOM else min(budget, room)


def total_chars(messages) -> int:
    """What a message list costs the window, counted the way the engine's own check counts it."""
    return sum(len(message["content"]) for message in messages)


# How much of the window the compass may spend, before its own token ceiling gets a say. The share
# is smaller than the state block's because the compass is read every turn of every task while the
# state block is one run's record, and a block that remembers too much is the reason the current
# task gets truncated.
COMPASS_SHARE = 8
COMPASS_MIN_ROOM = 400


def compass_tokens(settings, used_chars: int) -> int:
    """How many tokens of memory this window can afford, and zero when it cannot afford any.

    `used_chars` is everything the base already carries — system prompt, task, repository map, the
    user's own notes and the recorded history — because the compass rides inside that same block and
    is charged the same way. A window with less than a paragraph of room gets nothing: the task is
    the thing the memory exists to serve, and an over-full compass is how a project starts answering
    its own notes instead of its request.
    """
    room = int(settings.context_chars) - max(0, int(used_chars))
    if room < COMPASS_MIN_ROOM:
        return 0
    return min(memory_summarizer.COMPASS_TOKENS,
               memory_summarizer.tokens_for_chars(
                   min(int(settings.context_chars) // COMPASS_SHARE, room)))

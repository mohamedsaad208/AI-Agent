"""What a refused action says back to the model.

One table, and the most-edited prose in the engine: every row here exists because a small local
model ran into the same wall three times in a row and the sentence it got back was true but
useless. The generic "choose again" line is the fallback, and each named case replaces it with
the one thing the model can act on.

advice_for is pure: it reads the refusal text and the task and returns a sentence. The stale-read
case stays in the loop because answering it means opening a file.
"""
from __future__ import annotations

import re

PROPOSE_SHAPE = ('{"action":"propose","summary":"...","checks":["..."],'
                 '"changes":[{"path":"...","content":"<entire file, not a fragment>"}]}'
                 ' or for a small change to an existing file:'
                 ' {"path":"...","edits":[{"search":"<exact current text>","replace":"<new text>"}]}'
                 ' or to remove a file for good: {"path":"...","delete":true})')

# The two shapes a rejection is written in: which file the refusal named, and which files the
# task named. The difference between them is the "the model drifted to another file" case.
REJECTED_PATH = re.compile(r"in (\S+) matches nothing")
PATH_IN_TASK = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,5}")


def advice_for(error: str, task: str) -> str:
    """The next step to suggest for one refusal, or the generic one when nothing specific fits."""
    error = str(error or "")
    recoverable = ("A rejected action is not a failure. Choose exactly one action again, "
                   "for example: " + PROPOSE_SHAPE)
    if "unchanged file" in error:
        # Seen twice in the ecommerce run: a fix round proposed the failing file back byte
        # for byte, three times in a row, and every rejection came with advice about JSON
        # shape. True, and useless. What was missing is that the text is already on disk.
        recoverable = ("The content you proposed is identical to the file already on disk, "
                       "so it cannot change what the build reported. Propose content that "
                       "differs: name the line you add, remove or rewrite.")
    elif "empty search block" in error:
        # The repair round on JwtService.java: told the import line was wrong, the model
        # answered with {"search": "", "replace": "import …"} twice in a row. It wanted to
        # add a line, and the only hole in the edit contract is that an empty anchor is
        # not allowed — which is true, and says nothing about what to write instead.
        recoverable = ("An edit has to quote text that is already in the file. To add a "
                       "line, search for the existing line it belongs next to and replace "
                       "that line with itself plus yours; to change a line, search for "
                       "that line exactly as the file shows it, indentation included.")
    elif "text files are accessible" in error:
        # Both turns lost to this in the ecommerce run: the sentence named nothing, the
        # advice said "choose an action again", and a 3 B model did exactly that. A
        # refused *name* is not a shape problem, so say which of the two it is.
        recoverable = ("That file name is one this tool cannot write, and reformatting "
                       "the proposal will not change it. If the task truly requires this "
                       "exact file, return action=blocked and name the file; otherwise "
                       "propose a file whose name this tool accepts.")
    else:
        drift = REJECTED_PATH.search(error)
        named = PATH_IN_TASK.findall(task)
        if drift and named and not any(one.lower() in drift.group(1).lower()
                                       for one in named):
            # Measured on M3: asked to create `ApiResponse.java`, the model spent its turns
            # editing `ServerTimestampFilter.java` — the file the *previous* task in this
            # same chat had touched. The engine caught it; only the sentence back to the
            # model was missing.
            recoverable = ("That edit targets " + drift.group(1) + ", a file this task "
                           "never named. The task named " + ", ".join(named[:3]) +
                           ". Propose that file, or block and say why another is needed.")
    return recoverable

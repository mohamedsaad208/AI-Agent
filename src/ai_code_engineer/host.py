"""The seam between the two windows: four verbs, and the one question they must ask alike.

Tk and the web window agree on nothing about presentation — one owns a `StringVar` and a
`messagebox`, the other a snapshot dict and an SSE channel. What they must not be allowed to
disagree about is *what a moment means*. Four verbs cover everything either window does to show
itself, and the review that named them counted the drift in the old tree: setting a status
(`self.status.set()` ×52 against `self.status =` ×72), adding a conversation row
(`chat_message` ×15 against `_add` ×19), asking (`messagebox.*` ×6 against `self.confirm` ×6) and
streaming (`events.put` ×7 against `_emit` ×13).

Those four are the contract below. A window implements them and nothing else may reach the
screen, so a sentence written for one window cannot fail to exist in the other.

The second half of this file is the part that had actually drifted. Both windows assemble their
own Apply dialog, and they had diverged in a way no test could see: the web window passes
`repair.must_ask()`'s reason into the dialog and the Tk window shows only the removal notice, so
the same proposal warned about different things depending on which window happened to be open —
and Tk has no Auto-Apply or `must_ask` call at all. One builder, one answer.
"""
from __future__ import annotations


class Host:
    """What a window owes the code that runs behind it.

    Deliberately not an ABC: `gui.AgentApp` already inherits from Tk, and forcing both
    presentation layers under one base is the rewrite this module exists to make unnecessary.
    The names are the contract, and `tests/test_host.py` holds both windows to them.
    """

    def say(self, text: str) -> None:
        """Put one sentence on the window's status surface."""
        raise NotImplementedError

    def line(self, role: str, author: str, text: str) -> None:
        """Add one row to the conversation."""
        raise NotImplementedError

    def ask(self, title: str, message: str, warning: str = "", ok_label: str = "Continue") -> bool:
        """Ask the user something and block for the answer. `warning` is the part that must never
        be dropped by the window that finds it inconvenient."""
        raise NotImplementedError

    def stream(self, event: dict) -> None:
        """Forward one progress event to whatever is watching."""
        raise NotImplementedError


def apply_prompt(session: dict, notice: str = "", reason: str = "", again: str = "") -> dict:
    """The Apply dialog as data: {title, message, warning, ok_label}.

    `notice` is `repair.removal_notice()` — the files this proposal empties or deletes — and
    `reason` is `repair.must_ask()`, the one case where writing needs saying twice because the
    proposal would erase work. They overlap: an emptying proposal is also a must-ask one, so the
    reason is appended only where it would otherwise be the only thing the user reads.

    `again` is the repair loop's notice, which names a command that executes the project's own
    code and so belongs in the same breath as the write it follows.
    """
    changes = (session or {}).get("changes", []) or []
    root = (session or {}).get("root", "")
    body = notice.rstrip("\n")
    # A dialog that says "Write 2 file(s)" about a proposal that removes one of them is the card
    # lying about the irreversible half, so the two verbs are counted apart.
    removed = sum(1 for change in changes if change.get("delete"))
    written = len(changes) - removed
    lead = " and ".join(filter(None, [f"Write {written}" if written else "",
                                      f"remove {removed}" if removed else ""])) or "Write 0"
    message = ((body + "\n\n" if body else "") + lead + " file(s) to\n" + str(root) +
               (again if again else "") +
               (("\n\n" + reason) if reason and not body else "") +
               "\n\nYou can roll back afterwards as long as the files are not edited later.")
    return {"title": "Apply changes", "message": message, "warning": reason,
            "ok_label": "Apply changes"}

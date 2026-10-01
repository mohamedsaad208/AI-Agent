"""The request queue: what a held message is, and what the strip says about it.

One rule shapes this whole module: **a row leaves the queue only once a job has taken it.**
`start_plan` refuses for reasons the queue cannot fix — no model selected, the folder is gone, the
message too long — and a drain that popped first lost the typed message with nothing but a status
line to show for it; two create tasks disappeared that way in the ecommerce run. So nothing here
starts work, and nothing here removes a row. The controller owns the list, the held flag, the claim
inside `_drain_queue`, and the RLock that makes that claim one step; this file owns the shapes.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from ..engine import MAX_TASK_CHARS
from ..labels import asked_of, queue_notes

RESTORED_LIMIT = 20


def split_request(item: dict) -> tuple[str, str]:
    """The asked text and the frozen quotation of one row.

    New rows carry both fields apart. Rows written by an older window carry only the composed text,
    so the quotation is decoded back out of it — and only when the two halves actually add up to it,
    because guessing there would split a message that is not a reply.
    """
    text = item.get("text", "")
    asked, reference = item.get("asked"), item.get("reference")
    if isinstance(asked, str) and isinstance(reference, str) and reference + asked == text:
        return asked, reference
    asked = asked_of(text)
    return asked, text[:-len(asked)] if asked else ""


def row(*, task: str, asked: str, chat: str, branch: str, project: str, at: str) -> dict:
    """One held message. The reference is the quotation prefix of the composed text.

    A reference is resolved at the click, not when the queue drains: by then the thread may have
    grown, and an index kept that long would point at a different row.
    """
    return {"id": uuid.uuid4().hex[:8], "text": task, "asked": asked,
            "reference": task[:-len(asked)], "chat": chat, "branch": branch,
            "project": project, "at": at}


def is_duplicate(rows: list[dict], task: str, chat: str) -> bool:
    """The same message twice in a row, in the same conversation, is one queue line."""
    return any(item.get("text") == task and item.get("chat") == chat for item in rows)


def too_long(task: str) -> bool:
    """Whether this message may be held at all."""
    return len(task) > MAX_TASK_CHARS


def restore(saved: object) -> list[dict]:
    """The rows a reboot left behind, validated and held for an explicit approval.

    A queued change request can be pointed at files that moved on since it was written, so every
    restored row carries `restored: True` and waits for the operator to press the strip's ▶;
    nothing here resumes itself.
    """
    rows: list[dict] = []
    if not isinstance(saved, list):
        return rows
    for item in saved[:RESTORED_LIMIT]:
        if (isinstance(item, dict) and isinstance(item.get("text"), str)
                and item["text"].strip() and len(item["text"]) <= MAX_TASK_CHARS):
            asked, reference = split_request(item)
            rows.append({**item, "asked": asked, "reference": reference, "restored": True})
    return rows


def apply_edit(item: dict, text: str) -> None:
    """Rewrite one row's message, and re-derive its halves from what is left."""
    item["text"] = (text or "").strip()[:MAX_TASK_CHARS]
    item["asked"] = asked_of(item["text"])
    item["reference"] = item["text"][:-len(item["asked"])] if item["asked"] else ""


def detach(rows: list[dict], item_id: str) -> list[dict]:
    """Send one row to a conversation of its own: to the front, asked in no chat.

    The branch cannot move while a job runs — `_select_branch` refuses so a chained apply cannot find
    itself pointed at another folder — so a row detached mid-task waits for the switch, and then
    opens there. It never runs in the chat it left.
    """
    for index, item in enumerate(rows):
        if item.get("id") == item_id:
            rest = rows[:index] + rows[index + 1:]
            return [{**item, "detached": True, "chat": ""}] + rest
    return rows


def to_front(rows: list[dict], item_id: str) -> list[dict]:
    """Run this one next."""
    ids = [item.get("id") for item in rows]
    if item_id not in ids:
        return rows
    index = ids.index(item_id)
    return [rows[index]] + rows[:index] + rows[index + 1:]


def release_restored(rows: Iterable[dict]) -> None:
    """A press on the strip is the approval every restored row was waiting for.

    It covers the whole batch rather than the one clicked, because the batch is the unit the operator
    is resuming: pressing ▶ beside "2 waiting" and having one of them run is the surprise.
    """
    for item in rows:
        item.pop("restored", None)


def view(rows: list[dict], *, chat: str, held: bool, arabic: bool,
         ask_pending: bool) -> dict:
    """What the strip shows: the messages, and whether they are held.

    The sentences are built here for the same reason the banner's are: the client cannot tell what
    language the task was asked in, and a strip that mixed English status lines into an Arabic
    conversation would be the drift this project keeps having to undo.

    Items queued for another conversation stay in the list — they belong to that chat and will run
    when the user goes back to it — but they are not drawn here, because an invisible line that fires
    later is exactly the surprise this strip exists to avoid.
    """
    here = [{"id": item.get("id", ""), "text": item.get("text", ""),
             "detached": bool(item.get("detached")), "at": item.get("at", ""),
             "restored": bool(item.get("restored"))}
            for item in rows if item.get("chat") == chat or item.get("detached")]
    other_items = [item for item in rows
                   if item.get("chat") not in {chat, ""} and not item.get("detached")]
    elsewhere = len(other_items)
    first_other = other_items[0] if other_items else None
    target_chat = first_other.get("chat") if first_other else None
    target_kind = "chat" if (target_chat and str(target_chat).startswith("c-")) else "session"
    return {"items": here, "held": bool(held), "elsewhere": elsewhere,
            "chat": target_chat, "kind": target_kind,
            **queue_notes(arabic, elsewhere, ask_pending)}

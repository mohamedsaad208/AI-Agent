"""Configuration the program signs, so an edit made outside it is seen rather than followed.

The request was a file a person can change without touching a profile or a window's code — and
"encrypted, so nobody can edit it except from inside the program". Encryption is the part this tool
cannot deliver honestly: the standard library has no cipher, the key would have to sit on the same
account as the file it protects, and the contents are not secret — a credential *value* is refused on
the way in, so what remains is a model name, an address and four numbers.

What does answer the fear is integrity. Every row is signed with a per-install secret, and a file
whose rows were added, reordered, retyped or deleted by hand is **not obeyed**: the tool names the rows
it refused and carries on with the profile. Honest scope, in the words the file itself prints — this is
tamper evidence, not access control. The owner of this account can rewrite both files, and nothing on a
local filesystem stops them. What an edit from outside the program cannot do is change behaviour
quietly.

Nothing here decides what a value *means*: ``config.py`` owns the provider table, the ranges and the
one ``validate``. This module owns the file, the signature and the refusal.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from . import redaction
from .labels import say

FILE = ".agent-overrides.json"
KEY_FILE = ".agent-overrides.key"

# The target that means "for every provider". A row naming one provider beats it.
EVERY = "*"

# The keys a row may carry, and the JSON type each is stored as. `provider` is deliberately absent:
# which vendor answers a task is decided by the row on screen or the profile being read, and a side
# file able to swap it is the same failure ``providers.py`` refuses upstream fallbacks for — the model
# that answered would not be the one that was reviewed.
TYPES = {"model": str, "endpoint": str, "api_key_env": str, "max_turns": int,
         "timeout_seconds": int, "context_chars": int, "output_tokens": int,
         "fast_model": str, "strong_model": str, "semantic_model_dir": str}

# A row with target `EVERY` states a preference that holds for every provider. An address and a key
# variable belong to one provider, so a wildcard row for either would silently aim a different vendor's
# traffic at it.
PER_PROVIDER = ("endpoint", "api_key_env")

# normcase(path) -> ((mtime_ns, size, key bytes), (accepted rows, refusals)). A snapshot goes out on
# every streamed log line, so a read must not re-HMAC the disk each time — the same idiom `modes` uses.
_cache: dict[str, tuple[tuple, tuple[list, list]]] = {}
# normcase(key path) -> (mtime_ns, size, bytes). The key is read on every snapshot's cache check, so it
# is cached too: a hot path that stats and reads a file per call is a hot path that shifts a race.
_keys: dict[str, tuple[int, int, bytes]] = {}


def path(app_dir) -> Path:
    return Path(app_dir) / FILE


def key_path(app_dir) -> Path:
    return Path(app_dir) / KEY_FILE


# ---------------------------------------------------------------- the signature ----------------------------------------------------------------
def _secret(app_dir, create: bool = False) -> bytes:
    """This install's signing key, or ``b""`` when there is none and none was asked for.

    Read from a file the program creates and never ships. A key written in the code would be a key in
    the repository, which is the mistake the profiles already refuse to make about API keys.

    Cached by the file's stamp because ``config`` consults this on the resolution order, and the
    resolution order runs on every snapshot a window pushes — a per-push read of a file nobody writes
    is a cost with no answer behind it.
    """
    file = key_path(app_dir)
    stamp = os.path.normcase(str(file))
    try:
        info = file.stat()
    except OSError:
        _keys.pop(stamp, None)
        return _create_secret(app_dir, stamp) if create else b""
    held = _keys.get(stamp)
    if held and held[0] == (info.st_mtime_ns, info.st_size):
        return held[2]
    try:
        secret = bytes.fromhex(file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        _keys.pop(stamp, None)
        return _create_secret(app_dir, stamp) if create else b""
    _keys[stamp] = (info.st_mtime_ns, info.st_size, secret)
    return secret


def _create_secret(app_dir, stamp: str) -> bytes:
    secret = secrets.token_hex(32)
    file = key_path(app_dir)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(secret + "\n", encoding="utf-8")
    try:
        os.chmod(file, 0o600)     # Windows has no per-user mode; the attempt is the whole promise there
    except OSError:
        pass
    raw = bytes.fromhex(secret)
    _keys[stamp] = (file.stat().st_mtime_ns, file.stat().st_size, raw)
    return raw


def _canonical(rows: list) -> bytes:
    """The bytes that get signed: the list as stored, one key order per row, no whitespace.

    Not sorted here. ``_write`` already stores the rows in one order it computes itself, so signing
    the stored list means a hand-edit that only *reordered* them changes the bytes and breaks the
    signature — which is an edit, and the whole reason the file is signed.
    """
    return json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signature(secret: bytes, rows: list) -> str:
    return hmac.new(secret, _canonical(rows), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- the document ----------------------------------------------------------------
def document() -> dict:
    """The block the file carries beside its rows: what may be set, and to what.

    Every limit here is *read* from ``config`` instead of written out again. A file that stated its own
    ranges would be a second table, and the second table is what eventually disagrees with the
    validator.
    """
    from . import config        # `config` reads this module; one of the two directions has to wait

    return {
        "what": ("Configuration rows the program signs. Change them from inside the program - "
                 "Settings then Overrides, or `agent overrides` - never by editing this file. Rows the "
                 "program cannot verify are refused and named, and it runs on the profile instead."),
        "why not encrypted": ("These contents are not secret and a cipher that this install can open "
                              "on its own cannot keep a person out of their own file, so the file is "
                              "signed instead: an edit made outside the program is seen and refused "
                              "rather than followed."),
        "order": ["what you typed into the window for this run",
                  "a signed row in this file",
                  "the profile being read",
                  "the provider's environment variable",
                  "the provider's own row inside the program"],
        "keys": {key: (f"a number, {config.LIMITS[key][0]} to {config.LIMITS[key][1]}"
                       if key in config.LIMITS else "text, and never a credential value")
                 for key in sorted(TYPES)},
        "targets": {key: f"applies when the provider being used is {key}"
                    for key in sorted(config.BY_KEY)},
        EVERY: "applies to every provider, and loses to a row that names one",
        "not here": ["the provider - the choice on the screen or in the profile, not a preference",
                     "an API key - only the name of the variable holding one, exactly as a profile does",
                     "an address for a provider whose profile already spells one out: a row can supply "
                     "an address nobody wrote down, but it never redirects one somebody did"],
    }


# ---------------------------------------------------------------- the rules ----------------------------------------------------------------
def _refusable(key: str, value, target: str) -> str:
    """Why this row cannot be used, or "" when it can. Keys and types — ranges are `validate`'s."""
    if key not in TYPES:
        return "unknown"
    if target == EVERY and key in PER_PROVIDER:
        return "per-provider"
    wanted = TYPES[key]
    if wanted is int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return "not a number"
        try:
            int(str(value).strip())
        except ValueError:
            return "not a number"
        return ""
    if not isinstance(value, str):
        return "not text"
    return "empty" if not value.strip() else ""


# ---------------------------------------------------------------- reading ----------------------------------------------------------------
def _read(app_dir) -> tuple[list, list]:
    """The rows this file signs, and the rows it refused with the reason for each.

    A bad signature refuses the whole file rather than one row: the signature covers the row set, so
    deleting or reordering a row is an edit too, and the safe reading of a file nobody can account for
    is "this one is not mine".
    """
    file = path(app_dir)
    stamp = os.path.normcase(str(file))
    try:
        info = file.stat()
    except OSError:
        _cache.pop(stamp, None)
        return [], []
    secret = _secret(app_dir)
    now = ((info.st_mtime_ns, info.st_size), secret)
    cached = _cache.get(stamp)
    if cached and cached[0] == now:
        return cached[1]
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        # A file the operating system will not hand over is not a statement about the configuration,
        # so nothing is refused and nothing is applied — the same direction `modes` reads an unreadable
        # declaration in.
        _cache[stamp] = (now, ([], []))
        return [], []
    accepted: list = []
    refused: list = []
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
        refused.append(_refused(EVERY, "*", "unreadable"))
    if isinstance(raw, dict):
        stored = raw.get("rows")
        if not isinstance(stored, list):
            refused.append(_refused(EVERY, "*", "no-rows"))
        elif not secret or not hmac.compare_digest(str(raw.get("signature") or ""),
                                                   _signature(secret, stored)):
            refused.append(_refused(EVERY, "*", "changed-outside"))
        else:
            for row in stored:
                if not isinstance(row, dict):
                    continue
                target = str(row.get("target") or EVERY).strip().casefold() or EVERY
                key = str(row.get("key") or "").strip().casefold()
                why = _refusable(key, row.get("value"), target)
                if why:
                    refused.append(_refused(target, key, why))
                else:
                    accepted.append({"target": target, "key": key,
                                     "value": int(row["value"]) if TYPES[key] is int
                                     else str(row["value"]).strip(),
                                     "at": str(row.get("at") or "")[:32],
                                     "by": str(row.get("by") or "")[:40]})
    elif raw is not None:
        refused.append(_refused(EVERY, "*", "wrong-shape"))
    _cache[stamp] = (now, (accepted, refused))
    return accepted, refused


def _refused(target: str, key: str, why: str) -> dict:
    return {"target": target, "key": key, "why": why}


def rows(app_dir) -> list:
    """Every row the file signs and the schema still knows."""
    return [dict(row) for row in _read(app_dir)[0]]


def refusals(app_dir) -> list:
    """What this file asked for that the program will not do — the named half of refuse-and-report."""
    return [dict(row) for row in _read(app_dir)[1]]


def note(app_dir, key: str, why: str, target: str = EVERY) -> None:
    """Record a refusal only the merge could find, so one list still tells the whole story.

    The rows are individually valid, but a read can still refuse them as a set — a schema that shrank
    between versions is the way. Dropping them silently would be the exact failure this file exists to
    prevent, and raising would stop a task that has nothing to do with the row.
    """
    cached = _cache.get(os.path.normcase(str(path(app_dir))))
    if cached:
        cached[1][1].append(_refused(target, key, why))


def values(app_dir, provider: str) -> dict:
    """The rows that speak for this provider: the wildcard first, so a row naming it wins."""
    merged: dict = {}
    for row in sorted(rows(app_dir), key=lambda item: (item["target"] != EVERY, item["at"])):
        if row["target"] == EVERY or row["target"] == provider:
            merged[row["key"]] = row["value"]
    return merged


def listed(app_dir) -> list[dict]:
    """Rows and refusals together, newest first, for a person asking what is in force."""
    kept = [{**row, "state": "in force", "why": ""} for row in rows(app_dir)]
    refused = [{**row, "state": "refused", "value": ""} for row in refusals(app_dir)]
    return sorted(kept + refused, key=lambda row: row.get("at", ""), reverse=True)


# ---------------------------------------------------------------- writing ----------------------------------------------------------------
def _write(app_dir, kept: list) -> None:
    from .engine import atomic_json        # `engine` reads `config`, which reads this module

    # One order, decided here and signed here: signing a list and then storing a re-ordered copy of it
    # would leave the program unable to verify its own file.
    ordered = sorted(kept, key=lambda row: (row["target"], row["key"]))
    atomic_json(path(app_dir), {"read first": document(), "rows": ordered,
                                "signature": _signature(_secret(app_dir, create=True), ordered)})
    _cache.pop(os.path.normcase(str(path(app_dir))), None)


def ensure(app_dir) -> list[str]:
    """Create the key and the file on first run. An existing file is never rewritten.

    Born as the schema rather than as an empty object: `{}` documents nothing and would be rewritten
    every launch, while a file that lists its own keys, their ranges and the resolution order is the
    documentation a person actually opens.
    """
    if path(app_dir).exists():
        return []
    key_exists = key_path(app_dir).exists()
    _secret(app_dir, create=True)
    _write(app_dir, [])
    return [KEY_FILE, FILE] if not key_exists else [FILE]


def put(app_dir, target: str, key: str, value, by: str = "", *, arabic: bool = False) -> dict:
    """Sign one row into the file, or raise with the reason this value was refused.

    The value is judged by the program's own ``validate``, so a row cannot state a limit the tool would
    then refuse at run time — and the sentence that comes back is the validator's, not a second copy of
    the rules written here. The refusals this module writes itself are written in the asking window's
    language; the validator's are English everywhere in this tool, and one more half-translation would
    only make the two windows disagree about a range.
    """
    from . import config

    target = str(target or EVERY).strip().casefold() or EVERY
    key = str(key or "").strip().casefold()
    if target != EVERY and config.kind_for(target) is None:
        raise config.AgentError(unknown_target(target, arabic=arabic))
    why = _refusable(key, value, target)
    if why:
        raise config.AgentError(bad_value(key, why, arabic=arabic))
    cleaned = {"target": target, "key": key, "at": time.strftime("%Y-%m-%d %H:%M"),
               "by": str(by or "")[:40],
               "value": int(str(value).strip()) if TYPES[key] is int else str(value).strip()}
    if TYPES[key] is str and redaction.redact(cleaned["value"]) != cleaned["value"]:
        raise config.AgentError(credential_refused(key, arabic=arabic))
    kind = config.kind_for(target) or config.DEFAULT_KIND
    config.validate(_candidate(config, kind, key, cleaned["value"]))
    kept = [row for row in rows(app_dir)
            if not (row["target"] == target and row["key"] == key)]
    kept.append(cleaned)
    _write(app_dir, kept)
    return cleaned


def _candidate(config, kind, key: str, value):
    """A Settings for one provider row with this one value in it, for the validator to judge.

    The provider comes from the row's own target, because that is the only rule set the address has to
    satisfy: a row for Groq is judged by Groq's https rule, not by whichever row the window is on.
    """
    from dataclasses import replace

    return replace(config.Settings(), provider=kind.key, **{key: value})


def delete(app_dir, target: str, key: str) -> bool:
    """Take one row out of the file; whether it was there is the answer."""
    target = str(target or EVERY).strip().casefold() or EVERY
    key = str(key or "").strip().casefold()
    before = rows(app_dir)
    kept = [row for row in before if not (row["target"] == target and row["key"] == key)]
    if len(kept) == len(before):
        return False
    _write(app_dir, kept)
    return True


# ---------------------------------------------------------------- the sentences ----------------------------------------------------------------
def fields() -> list[dict]:
    """What the row form offers: every key, whether it takes a number, and the range it is judged by.

    The ranges are read out of ``config.LIMITS`` rather than repeated here, so a drawer cannot advertise
    a number the validator then refuses — the failure the request timeout already had to be fixed for.
    """
    from . import config

    out = []
    for key in sorted(TYPES):
        low, high = config.LIMITS.get(key, (None, None))
        out.append({"key": key, "number": TYPES[key] is int, "low": low, "high": high,
                    "per_provider": key in PER_PROVIDER})
    return out


def unknown_target(target: str, *, arabic: bool) -> str:
    from . import config

    return say(arabic,
               en=f"A target is a provider — {', '.join(sorted(config.BY_KEY))} — or {EVERY} for "
                  f"all of them, not {target}.",
               ar=f"الهدف يكون اسم بروفايدر — {'، '.join(sorted(config.BY_KEY))} — أو {EVERY} لكل "
                  f"البروفايدرات، مش {target}.")


def bad_value(key: str, why: str, *, arabic: bool) -> str:
    from . import config

    reasons = {"unknown": (f"There is no configuration field named {key} to override.",
                           f"مفيش خانة إعداد اسمها {key} تتغيّر."),
               "per-provider": (f"{key} belongs to one provider, so it cannot be set for all of them "
                                "at once. Name the provider.",
                                f"{key} بتاع بروفايدر واحد، فمش ممكن يتظبط لكل البروفايدرات مع بعض. "
                                "اكتب اسم البروفايدر."),
               "not a number": (f"{key} is a whole number.", f"{key} لازم يكون رقم كامل."),
               "not text": (f"{key} is a piece of text.", f"{key} لازم يكون نص."),
               "empty": (f"{key} cannot be blank. Remove the row instead of emptying it.",
                         f"{key} ما ينفعش فاضي. امسح السطر بدل ما تسيّبه فاضي.")}
    en, ar = reasons.get(why, (f"{key} cannot be stored: {why}.", f"{key} ما يتخزنش: {why}."))
    if why == "unknown":
        en += " The file lists what may be set: " + ", ".join(sorted(TYPES)) + "."
        ar += " الملف بيقارن اللي ممكن يتغيّر: " + "، ".join(sorted(TYPES)) + "."
    if why == "not a number":
        low, high = config.LIMITS.get(key, (None, None))
        if low is not None:
            en += f" Between {low} and {high}."
            ar += f" بين {low} و {high}."
    return say(arabic, en=en, ar=ar)


def credential_refused(key: str, *, arabic: bool) -> str:
    return say(arabic,
               en=f"{key} looks like a credential, and no key goes in this file. Name the environment "
                  f"variable that holds it, exactly as a profile does.",
               ar=f"{key} بيشبه مفتاح، ومفيش مفتاح بيتكتب في الملف ده. اكتب اسم متغير البيئة اللي "
                  f"فيه، زي البروفايدر بالظبط.")


def written(row: dict, *, arabic: bool) -> str:
    """What a save costs the operator to believe: the row, what it now overrides, and where it stops."""
    return say(arabic,
               en=f"{row['key']} = {row['value']} for "
                  + ("every provider" if row["target"] == EVERY else row["target"])
                  + ". Signed, and read by both windows and the terminal from now on.",
               ar=f"{row['key']} = {row['value']} لـ "
                  + ("كل البروفايدرات" if row["target"] == EVERY else row["target"])
                  + ". اتوقع واتقرأ من النافذتين ومن التيرمنال من دلوقتي.")


def removed(key: str, *, arabic: bool) -> str:
    return say(arabic,
               en=f"{key} is no longer overridden. The profile, the environment variable and the "
                  f"provider's own row answer in its place, in that order.",
               ar=f"{key} بقى مش متغيّر. البروفايدر، وبعده متغير البيئة، وبعده صف البروفايدر نفسه "
                  f"هي اللي ترد، بالترتيب ده.")


def absent(key: str, *, arabic: bool) -> str:
    return say(arabic, en=f"There was no row for {key} to remove.",
               ar=f"ما كانش في سطر لـ {key} يتشال.")


def nothing_yet(*, arabic: bool) -> str:
    return say(arabic,
               en="Nothing is overridden. Every value comes from its profile, its provider's "
                  "environment variable, or the provider's own row.",
               ar="مفيش حاجة متغيّرة. كل قيمة جايّة من البروفايدر، أو من متغير البيئة تبعه، أو من صفه "
                  "نفسه.")


def spoken(rows: list, *, arabic: bool) -> list[dict]:
    """One mapping from a row to what a surface may print — used by the file and by the preview.

    A refusal code is internal; the sentence is ``why_text``'s, in the language the operator asked in,
    and a drawer that drew the code would show an English identifier inside an Arabic window. A refused
    row is not drawn with its value, because the program is not using that value.
    """
    out = []
    for row in rows:
        item = dict(row)
        if item["state"] != "in force":
            item["why"] = why_text(item.get("why", ""), arabic=arabic)
            item["display"] = says(item, arabic=arabic)
        else:
            item["display"] = f"{row['key']} = {row['value']}"
        out.append(item)
    return out


def reported(app_dir, *, arabic: bool) -> list[dict]:
    """The rows on disk, spoken."""
    return spoken(listed(app_dir), arabic=arabic)


def says(row: dict, *, arabic: bool) -> str:
    """What a refused row is called: a row-level one names its key, a file-level one names the file.

    A wildcard target is spelled out because `*` alone in a sentence reads like a mistake rather than
    like "this row cannot apply to every provider at once".
    """
    if row["key"] == EVERY:
        return say(arabic, en="the whole file", ar="الملف كله")
    target = (say(arabic, en="every provider", ar="كل البروفايدرات")
              if row["target"] == EVERY else row["target"])
    return f"{row['key']} for {target}"


def refused_line(refused: list, *, arabic: bool) -> str:
    """One line naming every row the program would not obey — a refusal without a name reads as a bug."""
    if not refused:
        return ""
    parts = [f"{says(row, arabic=arabic)}: {why_text(row['why'], arabic=arabic)}"
             for row in refused]
    head = say(arabic, en="Overrides not used:", ar="تعديلات ما اتطبقتش:")
    return head + " " + ("؛ " if arabic else "; ").join(parts)


def why_text(why: str, *, arabic: bool) -> str:
    from . import config

    known = {"changed-outside": (f"the rows were changed outside the program, so none of them are "
                                 f"trusted — re-save what you want from Settings or `agent overrides`",
                                 f"السطور اتغيرت من برة البرنامج، فولا واحد فيهم محل ثقة — أعد الحفظ "
                                 f"من Settings أو `agent overrides`"),
             "unreadable": ("the file could not be read as the JSON the program writes",
                            "الملف ما اتقرش كـ JSON اللي البرنامج بيكتبه"),
             "wrong-shape": ("the file is not the shape the program writes",
                             "الملف مش بالشكل اللي البرنامج بيكتبه"),
             "no-rows": ("the file holds no row list", "الملف مفيهوش قائمة سطور"),
             "no-longer-valid": ("the rows fit a version of the rules that is not this one, so none of "
                                 "them were applied",
                                 "السطور دي ماشية مع قواعد مش قواعد النسخة دي، فولا واحد اتطبق"),
             "unknown": ("no configuration field by that name", "مفيش خانة إعداد بالاسم ده"),
             "per-provider": ("it belongs to one provider", "هي بتاعة بروفايدر واحد"),
             "not a number": ("not a whole number", "مش رقم كامل"),
             "not text": ("not text", "مش نص"),
             "empty": ("empty", "فاضي")}
    en, ar = known.get(why, (why, why))
    return say(arabic, en=en, ar=ar)


def scope(*, arabic: bool) -> str:
    """What the file is, in the words the settings drawer and `agent overrides --list` both print."""
    return say(arabic,
               en="These rows are written only by the program and signed, so an edit made in another "
                  "editor is seen and refused rather than followed. It is tamper evidence, not a "
                  "password: the owner of this account can still rewrite the file by hand, and what "
                  "they lose by doing it is every override in it.",
               ar="السطور دي البرنامج لوحده اللي بيكتبها وبيوقعها، فتعديل أي محرر تاني ليها بيظهر "
                  "ويترفض بدل ما ينفذ. دي علامة على التغيير مش كلمة سر: صاحب الحساب ده لسه يقدر "
                  "يعيد كتابة الملف بإيده، واللي بيخسره لما يعمل كده هو كل تعديل في الملف.")

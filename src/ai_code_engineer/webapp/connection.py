"""How a model connection is *shown*: the catalog view, the one-line description, the consent answer.

The row list itself is not here — `config.py` owns the provider table, and this file reads it. What
lives here is the presentation the controller used to build inline: which entries a filter leaves
standing, what the window says about the model on screen, and whether a task on this row leaves the
device. No function here writes anything, and none imports the controller, so neither the RLock nor a
second copy of any of it can follow.

The one rule worth reading before changing this file: **a filter is a view, never a mutation.**
`filter_models` used to write the subset back into `self.catalogs`, so searching the list cost you
every dropped entry until the next Refresh — and it then cleared `self.model` when the query stopped
matching it. Typing three letters could deselect the model running your task.
"""
from __future__ import annotations

from .. import config


def filter_models(entries: list[dict], query: str) -> list[dict]:
    """The catalog as asked for, in a list nobody else holds.

    The recommended default stays first when a search matches it, because a filtered list the operator
    picks from should still offer the model this machine was measured on.
    """
    query = query.strip().casefold()
    if not query:
        return list(entries)
    kept = [entry for entry in entries
            if query in " ".join([entry.get("id", ""), entry.get("name", ""),
                                  entry.get("description", "")]).casefold()]
    kept.sort(key=lambda entry: entry.get("id") != config.DEFAULT_MODEL)
    return kept


def model_info(entry: dict | None, *, loaded: int, shown: int, query: str,
               endpoint: str) -> str:
    """One line about what is selected, or about what the list currently holds.

    An unfiltered list says where it came from, because the address is the thing that changed when a
    row was pointed elsewhere. A filtered one says what the filter cost, so "4 models" is never read
    as "this provider only has 4".
    """
    if entry:
        text = entry["name"] + " — " + entry["description"]
        if entry["id"] in config.RECOMMENDED:
            text += " · ★ " + config.RECOMMENDED[entry["id"]]
        return text
    if shown != loaded:
        return (f"{shown} of {loaded} models match \"{query.strip()}\". "
                "Clear the filter to see the rest.")
    return f"{loaded} models available at {endpoint}. Select one from the list."


def cloud_choice(kind: config.Kind, mode: str, entry: dict | None, endpoint: str) -> tuple[bool, bool]:
    """``(cloud, paid)`` for the row on screen — one answer, used by all three send paths.

    Two things can make a task leave the device: the provider row itself, and a model entry the
    catalog marked cloud (an Ollama "cloud" tag answers over the internet from a local URL).
    """
    paid = kind.free_only and mode == config.paid_mode(kind)
    cloud = config.needs_consent(kind, endpoint) or bool(entry and entry.get("cloud"))
    return cloud, paid


def info(*, kind: config.Kind, mode: str, endpoint: str, default_endpoint: str, profile: str,
         profiles: list[str], source: str, key_present: bool) -> dict:
    """The connection as the settings drawer draws it: where this row is, and what it still wants.

    `key_present` is whether a key exists, never the key itself — this dict is shipped to the browser
    on every state push, and a credential has no path out of the process through it.
    """
    return {"kind": kind.key, "label": kind.label, "endpoint": endpoint,
            "default_endpoint": default_endpoint, "cloud": kind.cloud, "shape": kind.shape,
            "needs_key": kind.needs_key, "key_env": kind.key_env or "",
            "consent": config.needs_consent(kind, endpoint), "paid": mode.endswith(" \u00b7 Paid"),
            "profile": profile, "profiles": profiles, "source": source,
            "key_present": key_present}

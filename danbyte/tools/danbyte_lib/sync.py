"""``danbyte-lib sync-check``: overlays an upstream change broke.

Run after merging NetBox's ``master``. Reports, per overlay:

- its upstream file is gone (renamed or deleted upstream);
- a patched, removed or ``after``/``before`` name upstream no longer has;
- an addition upstream now has itself (drop it from the overlay, or turn it
  into a patch);
- a Danbyte-only base under ``danbyte/types-new/`` that upstream now has.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import merge as m
from .repo import TYPES_NEW, Repo


@dataclass
class Finding:
    rel: str
    message: str
    severity: str = "error"


def run(repo: Repo) -> list[Finding]:
    out: list[Finding] = []
    for e in repo.entries(("device", "module", "rack-type")):
        if e.upstream_rel is None:
            continue
        if e.upstream is None:
            out.append(Finding(e.rel, e.error or f"upstream {e.upstream_rel} can't be read"))
            continue
        for section, name, msg in m.check(e.upstream, e.data):
            if "already exists upstream" in msg:
                out.append(Finding(e.rel, f"{section}: upstream now has {name!r}; drop the add: "
                                          "or make it a patch", "warning"))
            else:
                out.append(Finding(e.rel, f"{section}: {msg}"))
        if e.upstream_rel.startswith(TYPES_NEW + "/"):
            sub = e.upstream_rel[len(TYPES_NEW) + 1:]
            if (repo.root / sub).is_file():
                out.append(Finding(e.rel, f"upstream now has {sub}; point upstream: at it and "
                                          "drop the base", "warning"))
    return out

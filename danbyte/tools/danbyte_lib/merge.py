"""The merge rule: upstream NetBox YAML + a Danbyte overlay -> what Danbyte imports.

This module is the one place the rule lives. ``danbyte-lib preview`` prints its
result and Danbyte's importer is meant to reuse it, so it depends on nothing but
the standard library and PyYAML.

An overlay (``danbyte/overlays/<Vendor>/<model>.yaml``, schema
``danbyte/schema/overlay.schema.json``) looks like::

    upstream: device-types/Cisco/C9300-24P.yaml
    meta: {category: switch, family: Catalyst 9300}
    front_image: true                       # a NetBox key, overriding upstream
    model3d: {file: assets/Cisco/C9300-24P.glb}
    interfaces:                             # patched by name
      StackPort1/1: {type: cisco-stackwise-480}
    power-ports:
      add:
        - {name: StackPower1/A, type: cisco-stackpower, why: "..."}
    module-bays:
      PS-A: {modules: [PWR-C1-350WAC-P]}
      add:
        - {name: FAN 3, position: "3", after: FAN 2, why: "..."}
      remove:
        - {name: Old Bay, why: "..."}

The rule, applied to a deep copy of the upstream document:

1. ``upstream`` and ``format`` are the overlay's own and never reach the result.
2. Any other top-level key that is not a component list (``model3d``,
   ``rack_mount``, ``meta``, or a NetBox key such as ``front_image``) replaces
   the upstream value whole. New keys go after upstream's.
3. A component list (``interfaces``, ``module-bays``, ... see
   ``COMPONENT_KEYS``) is a mapping, applied in this order:

   a. ``remove:`` drops the named upstream components;
   b. every other key names an upstream component and maps to the fields to
      set on it (the fields given replace upstream's; ``why`` is dropped);
   c. ``add:`` appends new components (``why``, ``after`` and ``before`` are
      dropped), at the end of the list, or right after / before the component
      named by ``after`` / ``before``. A list upstream lacks is created.

   The upstream list's order is kept; nothing is ever matched by position.
4. Names that don't exist upstream (patch, remove, after/before), or an
   addition whose name already exists, are errors: ``MergeError`` lists them
   all. ``sync-check`` reports exactly these after an upstream sync.

``merge`` never touches the files; ``load_yaml`` reads YAML without turning
dates into ``datetime.date`` (lifecycle dates stay strings, as the schema
wants).
"""
from __future__ import annotations

import copy
from typing import Any, Iterable

import yaml

#: Component lists an overlay may patch, in NetBox's key spelling.
COMPONENT_KEYS = (
    "console-ports",
    "console-server-ports",
    "power-ports",
    "power-outlets",
    "interfaces",
    "front-ports",
    "rear-ports",
    "module-bays",
    "device-bays",
    "inventory-items",
)
#: Keys of the overlay itself; never copied into the result.
OVERLAY_KEYS = ("upstream", "format")
#: Reserved keys inside a component mapping (everything else is a name).
RESERVED = ("add", "remove")
#: Fields of an ``add:`` / patch entry that steer the merge, not the result.
STEERING = ("why", "after", "before")
#: Top-level keys Danbyte adds; NetBox ignores them.
DANBYTE_KEYS = ("model3d", "rack_mount", "meta")
#: Component fields Danbyte adds (component list -> fields).
DANBYTE_COMPONENT_FIELDS = {"module-bays": ("modules",), "interfaces": ("stack",)}


class MergeError(ValueError):
    """The overlay names things the upstream document doesn't have.

    ``problems`` is a list of ``(section, name, message)``.
    """

    def __init__(self, problems: list[tuple[str, str, str]]):
        self.problems = problems
        super().__init__("; ".join(f"{s}: {m}" for s, _, m in problems))


# -- YAML -------------------------------------------------------------------


class _Loader(yaml.SafeLoader):
    """SafeLoader without the timestamp resolver: ``2027-10-31`` stays a str."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(text_or_stream) -> Any:
    """Parse YAML safely, keeping dates as strings."""
    return yaml.load(text_or_stream, Loader=_Loader)  # noqa: S506 - SafeLoader subclass


def load_file(path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return load_yaml(fh)


# -- the merge --------------------------------------------------------------


def _names(items: Iterable[dict]) -> list[str]:
    return [str(i.get("name")) for i in items if isinstance(i, dict)]


def _strip(entry: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in entry.items() if k not in STEERING}


def merge_section(section: str, upstream_list, patch, problems: list | None = None) -> list:
    """Apply one component mapping to an upstream list (see module doc, rule 3).

    Collects problems into ``problems`` when given (and carries on), else
    raises ``MergeError``.
    """
    own = problems is None
    problems = [] if own else problems
    items = [copy.deepcopy(i) for i in (upstream_list or [])]
    if patch is None:
        patch = {}
    if not isinstance(patch, dict):
        problems.append((section, "", "must be a mapping of names, add: and remove:"))
        if own and problems:
            raise MergeError(problems)
        return items

    for entry in patch.get("remove") or []:
        name = str(entry.get("name"))
        idx = _names(items)
        if name not in idx:
            problems.append((section, name, f"remove: {name!r} is not upstream"))
            continue
        del items[idx.index(name)]

    for name, fields in patch.items():
        if name in RESERVED:
            continue
        name = str(name)
        idx = _names(items)
        if name not in idx:
            problems.append((section, name, f"{name!r} is not upstream"))
            continue
        target = items[idx.index(name)]
        for k, v in (fields or {}).items():
            if k in STEERING:
                continue
            target[k] = copy.deepcopy(v)

    for entry in patch.get("add") or []:
        name = str(entry.get("name"))
        idx = _names(items)
        if name in idx:
            problems.append((section, name, f"add: {name!r} already exists upstream"))
            continue
        new = _strip(entry)
        anchor = entry.get("after", entry.get("before"))
        if anchor is None:
            items.append(new)
            continue
        anchor = str(anchor)
        if anchor not in idx:
            where = "after" if "after" in entry else "before"
            problems.append((section, name, f"add: {name!r} {where} {anchor!r}, which is not there"))
            items.append(new)
            continue
        pos = idx.index(anchor) + (1 if "after" in entry else 0)
        items.insert(pos, new)

    if own and problems:
        raise MergeError(problems)
    return items


def merge(upstream: dict, overlay: dict, *, strict: bool = True) -> dict:
    """Merge ``overlay`` onto ``upstream`` and return the result (a new dict).

    With ``strict`` (the default) any name problem raises ``MergeError``; with
    ``strict=False`` problems are skipped and the best-effort result returned.
    """
    if not isinstance(upstream, dict) or not isinstance(overlay, dict):
        raise TypeError("upstream and overlay must be mappings")
    out = copy.deepcopy(upstream)
    problems: list[tuple[str, str, str]] = []
    for key, value in overlay.items():
        if key in OVERLAY_KEYS:
            continue
        if key in COMPONENT_KEYS:
            out[key] = merge_section(key, upstream.get(key), value, problems)
        else:
            out[key] = copy.deepcopy(value)
    if strict and problems:
        raise MergeError(problems)
    return out


def check(upstream: dict, overlay: dict) -> list[tuple[str, str, str]]:
    """The name problems of an overlay against an upstream document."""
    problems: list[tuple[str, str, str]] = []
    for key, value in overlay.items():
        if key in COMPONENT_KEYS:
            merge_section(key, upstream.get(key), value, problems)
    return problems


def additions(overlay: dict) -> list[dict]:
    """Every add / remove / patch in an overlay, for review and the catalog:
    ``{section, action, name, why}``."""
    out = []
    for key in COMPONENT_KEYS:
        patch = overlay.get(key)
        if not isinstance(patch, dict):
            continue
        for e in patch.get("remove") or []:
            out.append({"section": key, "action": "remove", "name": e.get("name"), "why": e.get("why")})
        for name, fields in patch.items():
            if name in RESERVED:
                continue
            row = {"section": key, "action": "change", "name": name,
                   "fields": sorted(k for k in (fields or {}) if k not in STEERING)}
            if (fields or {}).get("why"):
                row["why"] = fields["why"]
            out.append(row)
        for e in patch.get("add") or []:
            out.append({"section": key, "action": "add", "name": e.get("name"), "why": e.get("why")})
    return out


def strip_danbyte(doc: dict) -> dict:
    """The NetBox-only part of a merged document: Danbyte keys removed."""
    out = {k: copy.deepcopy(v) for k, v in doc.items() if k not in DANBYTE_KEYS}
    for section, fields in DANBYTE_COMPONENT_FIELDS.items():
        for item in out.get(section) or []:
            if isinstance(item, dict):
                for f in fields:
                    item.pop(f, None)
    return out


def rewrite_paths(doc: dict, old: str, new: str) -> dict:
    """A copy of ``doc`` with every ``model3d`` path starting ``old`` moved to
    ``new`` (``assets/`` <-> a pack's ``models/``)."""
    out = copy.deepcopy(doc)
    block = out.get("model3d")
    if not isinstance(block, dict):
        return out

    def move(v):
        return new + v[len(old):] if isinstance(v, str) and v.startswith(old) else v

    for k in ("file", "far"):
        if k in block:
            block[k] = move(block[k])
    renders = block.get("renders")
    if isinstance(renders, dict):
        for k in ("front", "rear", "iso"):
            if k in renders:
                renders[k] = move(renders[k])
    return out

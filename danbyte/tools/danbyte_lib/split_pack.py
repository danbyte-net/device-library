"""``danbyte-lib split-pack``: turn an edit-in-place model pack into overlays.

Today's packs ship upstream NetBox YAML edited in place (``model3d:`` added,
bays given ``modules:``, ports added with a ``# Danbyte overlay: …`` comment),
plus ``accessory-types/``, ``rack-kits/``, ``rack-types/`` and ``models/``.
This diffs every type against its upstream file and writes:

- ``danbyte/overlays/`` and ``danbyte/module-overlays/``: what the pack adds or
  changes, patched by name; each addition's ``why`` comes from the ``# danbyte:``
  or ``# Danbyte overlay:`` comment above it (an unmarked addition right after
  a marked one shares its comment), else from ``--rules``, else a placeholder
  that names the pack;
- ``danbyte/types-new/``: the NetBox part of a type upstream doesn't have,
  with an overlay on top of it;
- ``danbyte/pluggables/``, ``danbyte/racks/``: the pack's accessories and racks;
- ``danbyte/assets/``: the pack's ``models/`` files;
- ``danbyte/packs/<id>/pack.yaml``.

Each overlay is checked by merging it back: the result must equal the pack's
file (``meta`` aside, ``models/`` read as ``assets/``). The source folder is
only read.
"""
from __future__ import annotations

import fnmatch
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import merge as m
from . import yamlio
from .repo import Repo

COMMENT = re.compile(r"^\s*#\s*danbyte(?:\s+overlay)?\s*:\s*(.+?)\s*$", re.I)
TOP_KEY = re.compile(r"^([A-Za-z0-9_-]+)\s*:")
ITEM = re.compile(r"^\s*-\s+(\{)?\s*name\s*:\s*(.+?)\s*$")
PACK_MODELS = "models/"
ASSETS = "assets/"


@dataclass
class Report:
    written: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    placeholder_whys: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


# -- comments ---------------------------------------------------------------


def _scalar(text: str, flow: bool) -> str:
    if flow:
        text = re.split(r"[,}]", text, maxsplit=1)[0]
    try:
        v = m.load_yaml(text)
    except Exception:
        v = text
    return str(v).strip()


def comment_whys(text: str) -> tuple[dict[tuple[str, str], str], dict[str, str], list[str]]:
    """Reasons from ``# danbyte:`` / ``# Danbyte overlay:`` comments:
    ``({(section, name): why}, {top-level key: why}, header)``. A comment right
    above a list item belongs to that item; one above or just inside a
    top-level key (before any item) belongs to the key; comments before the
    first key are the file's header."""
    whys: dict[tuple[str, str], str] = {}
    key_whys: dict[str, str] = {}
    header: list[str] = []
    section, pending, seen_key = None, None, False
    for line in text.splitlines():
        c = COMMENT.match(line)
        if c:
            if not seen_key:
                header.append(c.group(1))
            else:
                pending = c.group(1)
            continue
        if not line.strip() or line.lstrip().startswith("#") or line.strip() == "---":
            continue
        k = TOP_KEY.match(line)
        if k:
            section, seen_key = k.group(1), True
            if pending:
                key_whys.setdefault(section, pending)
            pending = None
            continue
        it = ITEM.match(line)
        if it and section:
            if pending:
                whys[(section, _scalar(it.group(2), bool(it.group(1))))] = pending
            pending = None
            continue
        if pending and section:
            key_whys.setdefault(section, pending)
        pending = None
    return whys, key_whys, header


# -- rules ------------------------------------------------------------------


def _rules_doc(path: Path | None):
    return (m.load_file(path) or []) if path is not None else []


def load_rules(path: Path | None) -> list[dict]:
    """The ``{match, meta, why, pack}`` rules: the file's list, or its
    ``rules:`` when it also defines ``packs:``."""
    doc = _rules_doc(path)
    rules = doc.get("rules") or [] if isinstance(doc, dict) else doc
    if not isinstance(rules, list):
        raise ValueError(f"{path}: a list of {{match, meta, why, pack}} rules")
    return rules


def load_pack_defs(path: Path | None) -> dict[str, dict]:
    """``packs:`` of a rules file - ``{id: {name, description, requires}}`` -
    the library packs one source folder is split into. Empty: one pack,
    named as the source."""
    doc = _rules_doc(path)
    defs = doc.get("packs") or {} if isinstance(doc, dict) else {}
    if not isinstance(defs, dict):
        raise ValueError(f"{path}: packs: a mapping of pack id to {{name, requires}}")
    return {str(k): (v or {}) for k, v in defs.items()}


def packs_for(rules: list[dict], key: str) -> list[str]:
    """The packs an entry (``<kind>/<Vendor>/<NAME>``) goes into: the
    ``pack`` (an id or a list) of the last rule that fits and names one."""
    out: list[str] = []
    for rule in rules:
        if rule.get("pack") and fnmatch.fnmatchcase(key, str(rule.get("match", ""))):
            got = rule["pack"]
            out = [str(x) for x in (got if isinstance(got, list) else [got])]
    return out


def rules_for(rules: list[dict], key: str) -> tuple[dict, dict]:
    """Merged ``meta`` and ``why`` of every rule whose ``match`` glob fits
    ``key`` (``<kind>/<Vendor>/<NAME>``)."""
    meta, why = {}, {}
    for rule in rules:
        if fnmatch.fnmatchcase(key, str(rule.get("match", ""))):
            meta.update(rule.get("meta") or {})
            why.update(rule.get("why") or {})
    return meta, why


def rule_why(why: dict, section: str, name: str) -> str | None:
    for pattern, text in why.items():
        if fnmatch.fnmatchcase(f"{section}/{name}", pattern):
            return text
    return None


def rule_key_why(why: dict, key: str) -> str | None:
    """A rule's reason for a top-level key (a pattern without ``/``)."""
    for pattern, text in why.items():
        if "/" not in pattern and fnmatch.fnmatchcase(key, pattern):
            return text
    return None


def guess_module_kind(model: str) -> str | None:
    s = model.upper()
    if "STACK-KIT" in s:
        return "stack-kit"
    if s.startswith(("PWR", "PSU")) or "-PWR-" in s:
        return "psu"
    if "FAN" in s:
        return "fan"
    if "-NM-" in s:
        return "network-module"
    if "-SUP-" in s:
        return "supervisor"
    if "-LC-" in s:
        return "line-card"
    return None


# -- the diff ---------------------------------------------------------------


class Unconvertible(ValueError):
    pass


def diff_section(section: str, up: list, doc: list, *, why_of) -> dict:
    """The component mapping that turns ``up`` into ``doc`` (see merge rule 3).
    ``why_of(section, name, action, inherited)`` returns ``why`` text."""
    up = up or []
    doc = doc or []
    upn = [str(i.get("name")) for i in up]
    docn = [str(i.get("name")) for i in doc]
    for label, names in (("upstream", upn), ("pack", docn)):
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise Unconvertible(f"{section}: duplicate names in the {label} file: {', '.join(dup)}")
    common_up = [n for n in upn if n in docn]
    common_doc = [n for n in docn if n in upn]
    if common_up != common_doc:
        raise Unconvertible(f"{section}: the pack reorders upstream components")
    out: dict = {}
    removes = [n for n in upn if n not in docn]
    if removes:
        def successor(n):
            # A removal's comment sits where the item was: above the next one kept.
            for later in upn[upn.index(n) + 1:]:
                if later in docn:
                    return later
            return None
        out["remove"] = [{"name": n, "why": why_of(section, n, "remove", None, successor(n))}
                         for n in removes]
    for item in doc:
        name = str(item.get("name"))
        if name not in upn:
            continue
        u = up[upn.index(name)]
        gone = [k for k in u if k not in item]
        if gone:
            raise Unconvertible(f"{section}: {name}: the pack drops {', '.join(gone)}")
        fields = {k: v for k, v in item.items() if k != "name" and u.get(k) != v}
        if fields:
            w = why_of(section, name, "change", None, None)
            if w:
                fields["why"] = w
            out[name] = fields
    adds = []
    last_why = None
    for i, item in enumerate(doc):
        name = str(item.get("name"))
        if name in upn:
            last_why = None
            continue
        entry = dict(item)
        w = why_of(section, name, "add", last_why, None)
        entry["why"] = w
        last_why = w
        tail = all(str(x.get("name")) not in upn for x in doc[i:])
        if not tail:
            if i > 0:
                entry["after"] = docn[i - 1]
            else:
                entry["before"] = docn[i + 1]
        adds.append(entry)
    if adds:
        out["add"] = adds
    return out


def diff(up: dict, doc: dict, *, why_of) -> dict:
    """The overlay body (without ``upstream``) that turns ``up`` into ``doc``."""
    out: dict = {}
    gone = [k for k in up if k not in doc]
    if gone:
        raise Unconvertible(f"the pack drops upstream keys: {', '.join(gone)}")
    for key, value in doc.items():
        if key in m.COMPONENT_KEYS:
            if not isinstance(value, list):
                raise Unconvertible(f"{key}: not a list")
            sec = diff_section(key, up.get(key), value, why_of=why_of)
            if sec:
                out[key] = sec
        elif key not in up or up[key] != value:
            out[key] = value
    return out


def normalise(doc: dict) -> dict:
    """A pack type as the merge should reproduce it: ``models/`` read as
    ``assets/``."""
    return m.rewrite_paths(doc, PACK_MODELS, ASSETS)


def comparable(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("meta",) + m.OVERLAY_KEYS}


# -- the run ----------------------------------------------------------------

ORDER = ("format", "upstream", "meta")


def _ordered(overlay: dict) -> dict:
    first = {k: overlay[k] for k in ORDER if k in overlay}
    comps = {k: overlay[k] for k in overlay if k in m.COMPONENT_KEYS}
    danbyte = {k: overlay[k] for k in ("model3d", "rack_mount") if k in overlay}
    rest = {k: v for k, v in overlay.items() if k not in first and k not in comps and k not in danbyte}
    return {**first, **rest, **comps, **danbyte}


def _rewrite_pluggable(doc: dict) -> dict:
    """``models/`` read as ``assets/``: the model and, where it has them,
    the part's photos."""
    return m.rewrite_paths(dict(doc), PACK_MODELS, ASSETS)


def _write_if(path: Path, text: str, force: bool, report: Report, repo: Repo) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") != text and not force:
        report.problems.append(f"{repo.rel(path)} exists and differs; --force to overwrite")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def split_pack(repo: Repo, folder: Path, *, rules_path: Path | None = None,
               dry_run: bool = False, force: bool = False) -> Report:
    folder = Path(folder).resolve()
    report = Report()
    rules = load_rules(rules_path)
    manifest = m.load_file(folder / "pack.yaml") if (folder / "pack.yaml").is_file() else {}
    pack_id = str(manifest.get("id") or folder.name)
    version = str(manifest.get("version") or "")
    found: dict[str, dict[str, str]] = {"device": {}, "module": {}, "pluggable": {},
                                         "rack-kit": {}, "rack-type": {}}
    dan = repo.danbyte

    def emit(path: Path, doc: dict, schema: str, comments=(), key_notes=None) -> None:
        text = "\n".join([yamlio.SCHEMA_LINE.format(rel=yamlio.schema_ref(path, dan, schema)),
                          *[f"# {c}" for c in comments]]) + "\n" + yamlio.dump(doc, key_notes)
        if dry_run or _write_if(path, text, force, report, repo):
            report.written.append(repo.rel(path) if path.is_relative_to(repo.root) else str(path))

    # device and module types
    for kind, sub, out_dir, schema in (
            ("device", "device-types", "overlays", "overlay.schema.json"),
            ("module", "module-types", "module-overlays", "module-overlay.schema.json")):
        for src in sorted((folder / sub).glob("*/*.y*ml")):
            vendor, stem = src.parent.name, src.stem
            key = f"{kind}/{vendor}/{stem}"
            text = src.read_text(encoding="utf-8")
            doc = m.load_yaml(text)
            whys, key_whys, header = comment_whys(text)
            meta, rule_whys = rules_for(rules, key)
            up_rel = f"{sub}/{vendor}/{src.name}"
            comments = []
            if not (repo.root / up_rel).is_file():
                base_rel = f"danbyte/types-new/{sub}/{vendor}/{src.name}"
                base = m.strip_danbyte(doc)
                base_text = "---\n" + yamlio.dump(base, flow=False, spacing=False)
                if not dry_run:
                    _write_if(repo.root / base_rel, base_text, force, report, repo)
                report.bases.append(base_rel)
                report.notes.append(f"{key}: not upstream ({up_rel}); NetBox part kept as {base_rel}")
                up_rel, up = base_rel, base
                comments.append("Danbyte-only hardware: the NetBox part is in the upstream: base, "
                                "for offering to NetBox later.")
            else:
                up = m.load_file(repo.root / up_rel)

            def why_of(section, name, action, inherited, alt, _w=whys, _r=rule_whys, _k=key):
                w = _w.get((section, name)) or (inherited if action == "add" else None) \
                    or (_w.get((section, alt)) if action == "remove" and alt else None) \
                    or rule_why(_r, section, name)
                if w or action == "change":
                    return w
                report.placeholder_whys.append(f"{_k}: {section}/{name}")
                return f"Danbyte pack {pack_id} {version}: {action} (no reason recorded in the pack)"

            target = normalise(doc)
            try:
                body = diff(up, target, why_of=why_of)
            except Unconvertible as exc:
                report.problems.append(f"{key}: not converted: {exc}")
                continue
            if kind == "module":
                meta = {"category": "module", **meta}
                if "module_kind" not in meta:
                    mk = guess_module_kind(str(doc.get("part_number") or doc.get("model") or stem))
                    if mk:
                        meta["module_kind"] = mk
            overlay = _ordered({"format": "danbyte-library/v1", "upstream": up_rel,
                                **({"meta": meta} if meta else {}), **body})
            back = m.merge(up, overlay)
            if comparable(back) != comparable(target):
                report.problems.append(f"{key}: the overlay does not merge back to the pack file")
                continue
            notes = {}
            for k in overlay:
                if k in m.COMPONENT_KEYS or k in ORDER:
                    continue
                w = key_whys.get(k) or rule_key_why(rule_whys, k)
                if w:
                    notes[k] = w
            if not comments:
                comments = [f"Pack note: {h}" for h in header]
            emit(dan / out_dir / vendor / f"{stem}.yaml", overlay, schema, comments, notes)
            found[kind][doc.get("part_number") or stem] = f"{vendor}/{stem}"
            found[kind].setdefault(stem, f"{vendor}/{stem}")
            report.count(kind)

    # pluggables (a pack's accessory-types)
    for src in sorted((folder / "accessory-types").glob("*/*.y*ml")):
        vendor, stem = src.parent.name, src.stem
        doc = m.load_file(src)
        meta, _ = rules_for(rules, f"pluggable/{vendor}/{stem}")
        meta = {"category": "pluggable", **meta}
        out = {"format": "danbyte-library/v1", "meta": meta, **_rewrite_pluggable(doc)}
        emit(dan / "pluggables" / vendor / f"{stem}.yaml", out, "pluggable.schema.json")
        found["pluggable"][str(doc.get("name") or stem)] = f"{vendor}/{stem}"
        report.count("pluggable")

    # rack kits and rack types
    for src in sorted((folder / "rack-kits").glob("*/*/kit.yaml")):
        vendor, name = src.parent.parent.name, src.parent.name
        doc = m.load_file(src)
        meta, _ = rules_for(rules, f"rack-kit/{vendor}/{name}")
        out = {"format": "danbyte-library/v1", "meta": {"category": "rack-kit", **meta},
               **_rewrite_pluggable(doc)}
        emit(dan / "racks" / "kits" / vendor / name / "kit.yaml", out, "rack-kit.schema.json")
        found["rack-kit"][str(doc.get("name") or name)] = f"{vendor}/{name}"
        report.count("rack-kit")
    for src in sorted((folder / "rack-types").glob("*/*.y*ml")):
        vendor, stem = src.parent.name, src.stem
        doc = m.load_file(src)
        meta, _ = rules_for(rules, f"rack-type/{vendor}/{stem}")
        up_rel = f"rack-types/{vendor}/{src.name}"
        if (repo.root / up_rel).is_file():
            up = m.load_file(repo.root / up_rel)
            body = {k: v for k, v in doc.items() if up.get(k) != v}
            out = {"format": "danbyte-library/v1", "upstream": up_rel,
                   "meta": {"category": "rack", **meta}, **body}
        else:
            out = {"format": "danbyte-library/v1", "meta": {"category": "rack", **meta}, **doc}
        emit(dan / "racks" / "types" / vendor / f"{stem}.yaml", out, "rack-type.schema.json")
        # pack.yaml may name it by model or by file name.
        found["rack-type"][str(doc.get("model") or stem)] = f"{vendor}/{stem}"
        found["rack-type"].setdefault(stem, f"{vendor}/{stem}")
        report.count("rack-type")

    # assets
    for src in sorted((folder / "models").rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(folder / "models").as_posix()
        dst = dan / "assets" / rel
        report.assets.append(f"danbyte/assets/{rel}")
        if dry_run:
            continue
        if dst.exists() and dst.read_bytes() != src.read_bytes() and not force:
            report.problems.append(f"danbyte/assets/{rel} exists and differs; --force to overwrite")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # close-up copies for the website (release/models/<Vendor>/<PART>.hq.glb)
    for src in sorted((folder / "release" / "models").rglob("*.hq.glb")):
        rel = src.relative_to(folder / "release" / "models").as_posix()
        dst = dan / "assets-hq" / rel
        report.assets.append(f"danbyte/assets-hq/{rel}")
        if dry_run:
            continue
        if dst.exists() and dst.read_bytes() != src.read_bytes() and not force:
            report.problems.append(f"danbyte/assets-hq/{rel} exists and differs; --force to overwrite")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # pack.yaml
    listed = manifest.get("types") or {}
    types: dict[str, list[str]] = {}
    for old, kind in (("device", "device"), ("module", "module"), ("accessory", "pluggable"),
                      ("rack-kit", "rack-kit"), ("rack_kit", "rack-kit"),
                      ("rack-type", "rack-type"), ("rack_type", "rack-type"), ("rack", "rack-type")):
        for name in listed.get(old) or []:
            ref = found[kind].get(str(name))
            if ref is None:
                report.problems.append(f"pack.yaml lists {old} {name}, which was not converted")
                continue
            types.setdefault(kind, [])
            if ref not in types[kind]:
                types[kind].append(ref)
    for kind, refs in found.items():
        for ref in sorted(set(refs.values())):
            if ref not in types.get(kind, []):
                report.notes.append(f"{kind} {ref} is in the pack folder but not in pack.yaml; added")
                types.setdefault(kind, []).append(ref)
    defs = load_pack_defs(rules_path)

    def pack_file(pid: str, name: str, its: dict[str, list[str]], extra: dict) -> None:
        pack = {"format": "danbyte-library/v1", "id": pid, "name": name, "version": version}
        for k in ("license", "author", "description", "blender", "spec"):
            value = extra.get(k, manifest.get(k)) if k == "description" else manifest.get(k)
            if value is not None:
                pack[k] = value
        if extra.get("requires"):
            pack["requires"] = [str(x) for x in extra["requires"]]
        pack["types"] = {k: sorted(v) for k, v in its.items() if v}
        emit(dan / "packs" / pid / "pack.yaml", pack, "pack.schema.json")

    if not defs:
        pack_file(pack_id, manifest.get("name") or pack_id, types, {})
    else:
        # One source folder, several library packs: each entry goes where
        # the rules send it (an entry two packs share is listed by both).
        split: dict[str, dict[str, list[str]]] = {pid: {} for pid in defs}
        for kind, refs in types.items():
            for ref in refs:
                targets = packs_for(rules, f"{kind}/{ref}")
                if not targets:
                    report.problems.append(f"{kind} {ref}: no rule gives it a pack")
                for pid in targets:
                    if pid not in defs:
                        report.problems.append(f"{kind} {ref}: pack {pid} is not defined under packs:")
                        continue
                    split[pid].setdefault(kind, []).append(ref)
        for pid, extra in defs.items():
            for need in extra.get("requires") or []:
                if str(need) not in defs:
                    report.problems.append(f"pack {pid}: requires {need}, which is not defined")
            if not any(split[pid].values()):
                report.problems.append(f"pack {pid}: no entry goes into it")
                continue
            pack_file(pid, str(extra.get("name") or pid), split[pid], extra)
    for extra in sorted(p.name for p in folder.iterdir()
                        if p.name not in ("pack.yaml", "device-types", "module-types", "accessory-types",
                                          "rack-kits", "rack-types", "models")):
        report.notes.append(f"{extra}: only its models/*.hq.glb are copied" if extra == "release"
                            else f"{extra}: not part of the library layout; not copied")
    return report

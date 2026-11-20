"""``danbyte-lib build-pack``: the zip Danbyte installs.

A library pack (``danbyte/packs/<id>/pack.yaml``) names entries; Danbyte's
pack import reads a folder of complete files::

    pack.yaml
    device-types/<Vendor>/<PART>.yaml
    module-types/<Vendor>/<PART>.yaml
    accessory-types/<Vendor>/<NAME>.yaml
    rack-kits/<Vendor>/<NAME>/kit.yaml
    rack-types/<Vendor>/<NAME>.yaml
    models/<Vendor>/...            (every model and photo the files name)

A bundle (``danbyte/bundles/<id>/bundle.yaml``) becomes one zip with a
``bundle.yaml`` and each included pack as such a folder under ``packs/``.

This builds that folder from the library: each device and module overlay
merged over its upstream file, the other kinds as they are, ``assets/``
paths written as ``models/``. The zip is deterministic - same library, same
bytes - so its checksum can be published.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from . import merge as m
from . import yamlio
from .repo import Repo

#: Library kind -> (folder in the pack, key under ``types:`` in its pack.yaml).
LAYOUT = {
    "device": ("device-types", "device"),
    "module": ("module-types", "module"),
    "pluggable": ("accessory-types", "accessory"),
    "rack-kit": ("rack-kits", "rack_kit"),
    "rack-type": ("rack-types", "rack"),
}
#: pack.yaml keys that are the library's own, not the pack's.
LIBRARY_KEYS = ("format", "types")
#: A fixed time for every file, so the zip's bytes depend on its content only.
STAMP = (2020, 1, 1, 0, 0, 0)


class PackBuildError(Exception):
    """A pack that can't be built: the problems, one per line."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def pack_doc(repo: Repo, pack_id: str) -> dict:
    for path, data, error in repo.packs():
        if path.parent.name == pack_id or (data or {}).get("id") == pack_id:
            if error or not data:
                raise PackBuildError([f"{repo.rel(path)}: {error or 'empty'}"])
            return data
    raise PackBuildError([f"no pack named {pack_id} under danbyte/packs/"])


def _models(doc) -> set[str]:
    """Every ``models/...`` path a pack file names."""
    found: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith("models/"):
            found.add(v)

    walk(doc)
    return found


def pack_files(repo: Repo, pack_id: str) -> tuple[dict[str, bytes], dict]:
    """The pack's files by pack-relative path, and its facts
    (``{id, name, version, license, author, counts}``)."""
    data = pack_doc(repo, pack_id)
    problems: list[str] = []
    files: dict[str, bytes] = {}
    types: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    assets: set[str] = set()
    for kind, refs in (data.get("types") or {}).items():
        if kind not in LAYOUT:
            problems.append(f"types.{kind}: not a kind a pack carries")
            continue
        folder, key = LAYOUT[kind]
        for ref in refs or []:
            try:
                e = repo.find(str(ref), (kind,))
            except Exception as exc:  # noqa: BLE001 - reported with the others
                problems.append(f"{kind} {ref}: {exc}")
                continue
            if e.error:
                problems.append(f"{e.rel}: {e.error}")
                continue
            try:
                doc = e.merged(strict=True) if kind in ("device", "module") else dict(e.data)
            except m.MergeError as exc:
                problems.append(f"{e.rel}: {exc}")
                continue
            doc = {k: v for k, v in doc.items() if k not in m.OVERLAY_KEYS}
            doc = m.rewrite_paths(doc, "assets/", "models/")
            vendor, _, name = str(ref).partition("/")
            rel = (f"{folder}/{vendor}/{name}/kit.yaml" if kind == "rack-kit"
                   else f"{folder}/{vendor}/{name}.yaml")
            files[rel] = ("---\n" + yamlio.dump(doc)).encode("utf-8")
            types.setdefault(key, []).append(name)
            counts[kind] = counts.get(kind, 0) + 1
            assets |= _models(doc)
    for rel in sorted(assets):
        src = repo.asset("assets/" + rel[len("models/"):])
        if not src.is_file():
            problems.append(f"{rel}: named by the pack but not in danbyte/assets/")
            continue
        files[rel] = src.read_bytes()
    if problems:
        raise PackBuildError(problems)
    manifest = {k: v for k, v in data.items() if k not in LIBRARY_KEYS}
    manifest["types"] = types
    files["pack.yaml"] = yamlio.dump(manifest).encode("utf-8")
    facts = {k: str(data.get(k) or "") for k in ("id", "name", "version", "license", "author")}
    facts["id"] = facts["id"] or pack_id
    facts["description"] = str(data.get("description") or "")
    facts["requires"] = [str(x) for x in data.get("requires") or []]
    facts["counts"] = counts
    return files, facts


def zip_bytes(files: dict[str, bytes], top: str) -> bytes:
    """``files`` as a zip under one top folder, the same bytes every time."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in sorted(files):
            info = zipfile.ZipInfo(f"{top}/{rel}", date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[rel])
    return buf.getvalue()


def build(repo: Repo, pack_id: str, out: Path) -> dict:
    """Write ``<id>-<version>.zip`` into ``out``. Answers
    ``{id, name, version, license, author, counts, file, size, sha256, files}``."""
    files, facts = pack_files(repo, pack_id)
    name = f"{facts['id']}-{facts['version'] or '0'}"
    blob = zip_bytes(files, name)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.zip"
    path.write_bytes(blob)
    return {**facts, "file": path.name, "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(), "files": len(files)}


# -- bundles ----------------------------------------------------------------


def _version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in v.replace("-", ".").split("."))


def bundle_doc(repo: Repo, bundle_id: str) -> dict:
    for path, data, error in repo.bundles():
        if path.parent.name == bundle_id:
            if error or not data:
                raise PackBuildError([f"{repo.rel(path)}: {error or 'empty'}"])
            return data
    raise PackBuildError([f"no bundle named {bundle_id} under danbyte/bundles/"])


def bundle_files(repo: Repo, bundle_id: str) -> tuple[dict[str, bytes], dict]:
    """A bundle's files - ``bundle.yaml`` and ``packs/<pack id>/...`` - and
    its facts (``{id, name, description, version, includes, packs, counts}``).
    Its version is its own, or the highest among its packs."""
    data = bundle_doc(repo, bundle_id)
    files: dict[str, bytes] = {}
    problems: list[str] = []
    packs: list[dict] = []
    counts: dict[str, int] = {}
    for pid in data.get("includes") or []:
        try:
            pfiles, facts = pack_files(repo, str(pid))
        except PackBuildError as exc:
            problems += [f"{pid}: {line}" for line in exc.problems]
            continue
        for rel, blob in pfiles.items():
            files[f"packs/{pid}/{rel}"] = blob
        packs.append({"id": facts["id"], "name": facts["name"], "version": facts["version"]})
        for kind, n in facts["counts"].items():
            counts[kind] = counts.get(kind, 0) + n
    if problems:
        raise PackBuildError(problems)
    version = str(data.get("version") or "") or max(
        (p["version"] for p in packs if p["version"]), key=_version_key, default="0")
    manifest = {"id": bundle_id, "name": str(data.get("name") or bundle_id)}
    if data.get("description"):
        manifest["description"] = str(data["description"])
    manifest["version"] = version
    manifest["packs"] = packs
    files["bundle.yaml"] = yamlio.dump(manifest).encode("utf-8")
    facts = {"id": bundle_id, "name": manifest["name"], "description": str(data.get("description") or ""),
             "version": version, "includes": [p["id"] for p in packs], "packs": packs, "counts": counts}
    return files, facts


def build_bundle(repo: Repo, bundle_id: str, out: Path) -> dict:
    """Write ``<id>-<version>.zip`` into ``out``; answers the bundle's facts
    with ``file``, ``size``, ``sha256`` and ``files``."""
    files, facts = bundle_files(repo, bundle_id)
    name = f"{bundle_id}-{facts['version']}"
    blob = zip_bytes(files, name)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.zip"
    path.write_bytes(blob)
    return {**facts, "file": path.name, "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(), "files": len(files)}

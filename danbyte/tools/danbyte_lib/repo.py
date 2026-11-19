"""Where things live in the library repository, and the entries in it."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from . import merge as m

#: Entry kind -> its folder under ``danbyte/``.
KIND_DIRS = {
    "device": "overlays",
    "module": "module-overlays",
    "pluggable": "pluggables",
    "rack-kit": "racks/kits",
    "rack-type": "racks/types",
}
#: Entry kind -> its schema file in ``danbyte/schema/``.
KIND_SCHEMAS = {
    "device": "overlay.schema.json",
    "module": "module-overlay.schema.json",
    "pluggable": "pluggable.schema.json",
    "rack-kit": "rack-kit.schema.json",
    "rack-type": "rack-type.schema.json",
    "pack": "pack.schema.json",
}
#: Upstream folder for overlay kinds, and the Danbyte-only base folder.
UPSTREAM_DIRS = {"device": "device-types", "module": "module-types", "rack-type": "rack-types"}
TYPES_NEW = "danbyte/types-new"
YAML_EXT = (".yaml", ".yml")


class RepoError(RuntimeError):
    pass


def find_root(start: str | os.PathLike | None = None) -> Path:
    """The repository root: the first folder up from ``start`` (default the
    working directory, or ``$DANBYTE_LIB_ROOT``) holding ``danbyte/schema``."""
    env = os.environ.get("DANBYTE_LIB_ROOT")
    if start is None and env:
        start = env
    p = Path(start or os.getcwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "danbyte" / "schema" / "overlay.schema.json").is_file():
            return cand
    raise RepoError(f"no library repository (danbyte/schema/) above {p}; pass --root")


@dataclass
class Entry:
    """One library entry: an overlay, pluggable, rack kit or rack type."""

    kind: str
    vendor: str
    name: str
    path: Path            # absolute
    rel: str              # repository-relative, posix
    data: dict = field(default_factory=dict)
    upstream_rel: str | None = None
    upstream: dict | None = None
    error: str | None = None   # why it couldn't be loaded

    @property
    def ref(self) -> str:
        return f"{self.vendor}/{self.name}"

    @property
    def id(self) -> str:
        return f"{self.kind}/{self.vendor}/{self.name}"

    def merged(self, strict: bool = True) -> dict:
        """The record Danbyte imports: upstream + overlay, or the file itself."""
        data = {k: v for k, v in self.data.items() if k not in m.OVERLAY_KEYS}
        if self.upstream is not None:
            return m.merge(self.upstream, self.data, strict=strict)
        return data


class Repo:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = find_root(root) if root is None else Path(root).resolve()
        self.danbyte = self.root / "danbyte"
        if not (self.danbyte / "schema").is_dir():
            raise RepoError(f"{self.root} has no danbyte/schema/")

    # paths ---------------------------------------------------------------
    def rel(self, path: str | os.PathLike) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def asset(self, rel_to_danbyte: str) -> Path:
        """An overlay's asset path (relative to ``danbyte/``) on disk."""
        return self.danbyte / rel_to_danbyte

    def entry_path(self, kind: str, ref: str) -> Path:
        base = self.danbyte / KIND_DIRS[kind]
        if kind == "rack-kit":
            return base / ref / "kit.yaml"
        return base / f"{ref}.yaml"

    def classify(self, rel: str) -> str | None:
        """What a repository-relative path is: an entry kind, ``pack``,
        ``asset``, ``types-new``, ``upstream``, ``schema``, ``tools`` or None."""
        rel = rel.replace(os.sep, "/")
        if rel.startswith("danbyte/packs/") and rel.endswith("/pack.yaml"):
            return "pack"
        if rel.startswith("danbyte/bundles/") and rel.endswith("/bundle.yaml"):
            return "bundle"
        if rel.startswith("danbyte/assets/"):
            return "asset"
        if rel.startswith(TYPES_NEW + "/"):
            return "types-new"
        if rel.startswith("danbyte/schema/"):
            return "schema"
        if rel.startswith("danbyte/tools/"):
            return "tools"
        for kind, d in KIND_DIRS.items():
            if rel.startswith(f"danbyte/{d}/") and rel.endswith(YAML_EXT):
                if kind == "rack-kit" and not rel.endswith("/kit.yaml"):
                    return None
                return kind
        if rel.split("/", 1)[0] in ("device-types", "module-types", "rack-types"):
            return "upstream"
        return None

    # loading -------------------------------------------------------------
    def load_entry(self, kind: str, path: Path) -> Entry:
        rel = self.rel(path)
        base = self.danbyte / KIND_DIRS[kind]
        parts = path.relative_to(base).parts
        if kind == "rack-kit":
            vendor, name = (parts[0], parts[1]) if len(parts) == 3 else ("", parts[0])
        else:
            vendor = parts[0] if len(parts) > 1 else ""
            name = Path(parts[-1]).stem
        e = Entry(kind=kind, vendor=vendor, name=name, path=path, rel=rel)
        try:
            data = m.load_file(path)
        except Exception as exc:  # yaml errors carry the line
            e.error = f"not valid YAML: {exc}"
            return e
        if not isinstance(data, dict):
            e.error = "not a mapping"
            return e
        e.data = data
        up = data.get("upstream")
        if kind in UPSTREAM_DIRS and isinstance(up, str):
            e.upstream_rel = up
            up_path = self.root / up
            if not up_path.is_file():
                e.error = f"upstream file {up} is missing"
                return e
            try:
                e.upstream = m.load_file(up_path)
            except Exception as exc:
                e.error = f"upstream {up} is not valid YAML: {exc}"
        return e

    def entries(self, kinds: tuple[str, ...] | None = None) -> Iterator[Entry]:
        for kind in kinds or tuple(KIND_DIRS):
            base = self.danbyte / KIND_DIRS[kind]
            if not base.is_dir():
                continue
            pattern = "kit.yaml" if kind == "rack-kit" else "*.y*ml"
            for path in sorted(base.rglob(pattern)):
                if path.is_file() and path.suffix in YAML_EXT:
                    yield self.load_entry(kind, path)

    def find(self, ref: str, kinds: tuple[str, ...] | None = None) -> Entry:
        """An entry by ``Vendor/NAME`` (or ``kind/Vendor/NAME``, or a path)."""
        p = Path(ref)
        cands = [p] if p.is_absolute() else [Path.cwd() / p, self.root / p]
        path = next((c.resolve() for c in cands if p.suffix in YAML_EXT and c.is_file()), None)
        if path is not None:
            kind = self.classify(self.rel(path))
            if kind in KIND_DIRS:
                return self.load_entry(kind, path)
            raise RepoError(f"{ref} is not a library entry")
        head, _, rest = ref.partition("/")
        if head in KIND_DIRS and rest.count("/") == 1:
            kinds, ref = (head,), rest
        found = [k for k in (kinds or tuple(KIND_DIRS)) if self.entry_path(k, ref).is_file()]
        if not found:
            raise RepoError(f"no entry named {ref}")
        if len(found) > 1:
            raise RepoError(f"{ref} is ambiguous ({', '.join(found)}); prefix the kind, e.g. {found[0]}/{ref}")
        return self.load_entry(found[0], self.entry_path(found[0], ref))

    def packs(self) -> list[tuple[Path, dict | None, str | None]]:
        out = []
        base = self.danbyte / "packs"
        if not base.is_dir():
            return out
        for path in sorted(base.glob("*/pack.yaml")):
            try:
                data = m.load_file(path)
                out.append((path, data if isinstance(data, dict) else None,
                            None if isinstance(data, dict) else "not a mapping"))
            except Exception as exc:
                out.append((path, None, f"not valid YAML: {exc}"))
        return out


    def bundles(self) -> list[tuple[Path, dict | None, str | None]]:
        """Every ``danbyte/bundles/<id>/bundle.yaml`` as (path, data, error)."""
        out = []
        base = self.danbyte / "bundles"
        if not base.is_dir():
            return out
        for path in sorted(base.glob("*/bundle.yaml")):
            try:
                data = m.load_file(path)
                out.append((path, data if isinstance(data, dict) else None,
                            None if isinstance(data, dict) else "not a mapping"))
            except Exception as exc:
                out.append((path, None, f"not valid YAML: {exc}"))
        return out


def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        out.append(ch if ch.isalnum() and ch.isascii() else "-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "x"

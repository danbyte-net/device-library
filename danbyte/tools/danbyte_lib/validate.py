"""``danbyte-lib validate``: schema, names against upstream, the merged record
against NetBox's own schema, and the glb hand-over checks."""
from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import glb as g
from . import merge as m
from . import schemas
from .repo import KIND_DIRS, KIND_SCHEMAS, TYPES_NEW, Entry, Repo

PORT_SECTIONS = ("interfaces", "console-ports", "console-server-ports", "power-ports",
                 "power-outlets", "front-ports", "rear-ports")
RACK_KINDS = ("blanking", "cable-manager", "shelf")


@dataclass
class Result:
    rel: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _names(doc: dict, sections) -> set[str]:
    out = set()
    for s in sections:
        for item in doc.get(s) or []:
            if isinstance(item, dict) and item.get("name") is not None:
                out.add(str(item["name"]))
    return out


def _budget_of(entry: Entry) -> str | None:
    if entry.kind == "device":
        return "chassis"
    if entry.kind == "module":
        return "module"
    if entry.kind == "rack-kit":
        return "rack-kit"
    if entry.kind == "pluggable":
        kind = entry.data.get("kind")
        if kind in RACK_KINDS:
            return "rack-part"
        return kind if kind in g.BUDGETS else None
    return None


class Validator:
    def __init__(self, repo: Repo, *, assets: bool = True):
        self.repo = repo
        self.assets = assets
        self.schema_dir = repo.danbyte / "schema"
        self.upstream_schema_dir = repo.root / "schema"

    # -- entries -----------------------------------------------------------
    def entry(self, e: Entry) -> Result:
        r = Result(e.rel)
        if e.error and e.data == {}:
            r.errors.append(e.error)
            return r
        v = schemas.validator(self.schema_dir, KIND_SCHEMAS[e.kind])
        r.errors += [f"schema: {x}" for x in schemas.messages(v, e.data)]
        if e.error:
            r.errors.append(e.error)
            return r
        stem = Path(e.rel).stem if e.kind != "rack-kit" else e.name
        if e.kind == "pluggable" and e.data.get("name") not in (None, stem):
            r.warnings.append(f"name {e.data.get('name')!r} differs from the file name {stem!r}")
        if e.kind == "rack-kit" and e.data.get("name") not in (None, stem):
            r.warnings.append(f"name {e.data.get('name')!r} differs from its folder {stem!r}")

        merged = e.data
        if e.upstream is not None:
            if Path(e.upstream_rel).stem != stem:
                r.warnings.append(f"file name {stem!r} differs from its upstream {Path(e.upstream_rel).name!r}")
            for section, _name, msg in m.check(e.upstream, e.data):
                r.errors.append(f"{section}: {msg}")
            merged = m.merge(e.upstream, e.data, strict=False)
            if e.kind in ("device", "module"):
                r.errors += [f"merged (NetBox schema): {x}" for x in schemas.netbox_messages(
                    self.upstream_schema_dir, self.schema_dir, e.kind, m.strip_danbyte(merged))]
        if e.kind in ("device", "module"):
            self._danbyte_refs(merged, r)
        if e.kind == "pluggable":
            self._pluggable(e.data, r)
        if self.assets:
            self._assets(e, merged, r)
        return r

    def _danbyte_refs(self, merged: dict, r: Result) -> None:
        bays = _names(merged, ("module-bays",))
        block = merged.get("model3d") if isinstance(merged.get("model3d"), dict) else {}
        renders = block.get("renders") if isinstance(block.get("renders"), dict) else {}
        for bay in (renders.get("modules") or {}):
            if str(bay) not in bays:
                r.errors.append(f"model3d.renders.modules: bay {bay!r} is not in module-bays")
        for led, spec in (block.get("leds") or {}).items():
            if not isinstance(spec, dict):
                continue
            globs = spec.get("bays") or []
            src = str(spec.get("source") or "")
            if src.startswith("modules.status:"):
                globs = [src.split(":", 1)[1].strip()]
            for gl in globs:
                if not any(fnmatch.fnmatchcase(b, gl) for b in bays):
                    r.warnings.append(f"model3d.leds.{led}: bays {gl!r} match no module bay")
        meta = merged.get("meta") if isinstance(merged.get("meta"), dict) else {}
        if meta.get("category") == "module" and not meta.get("module_kind"):
            r.warnings.append("meta: a module should say its module_kind")

    def _pluggable(self, data: dict, r: Result) -> None:
        cable = data.get("cable") if isinstance(data.get("cable"), dict) else {}
        parts = {str(p).rstrip("=").lower() for p in data.get("parts") or []}
        for part in (cable.get("min_bend_by_part") or {}):
            if str(part).rstrip("=").lower() not in parts:
                r.warnings.append(f"cable.min_bend_by_part: {part!r} is not one of parts")
        if cable.get("profile") == "zipcord" and "pitch_mm" in cable and "jacket_mm" in cable \
                and cable["pitch_mm"] < cable["jacket_mm"]:
            r.warnings.append("cable.pitch_mm: narrower than the jacket; Danbyte uses the jacket")
        if cable.get("profile") == "ribbon" and "pitch_mm" in cable and "jacket_mm" in cable \
                and cable["pitch_mm"] < cable["jacket_mm"]:
            r.errors.append("cable.pitch_mm: a ribbon's pitch can't be narrower than its jacket")

    # -- assets ------------------------------------------------------------
    def _file(self, rel: str | None, key: str, r: Result) -> Path | None:
        if not isinstance(rel, str):
            return None
        p = self.repo.asset(rel)
        if not p.is_file():
            r.errors.append(f"{key}: {rel} is missing (danbyte/{rel})")
            return None
        if g.is_lfs_pointer(p):
            r.notes.append(f"{key}: {rel} is a Git LFS pointer; run `git lfs pull` to check it")
            return None
        return p

    def _assets(self, e: Entry, merged: dict, r: Result) -> None:
        block = merged.get("model3d") if isinstance(merged.get("model3d"), dict) else None
        if not block:
            return
        budget = _budget_of(e)
        full = self._file(block.get("file"), "model3d.file", r)
        far = self._file(block.get("far"), "model3d.far", r)
        reports = {}
        for key, path in (("model3d.file", full), ("model3d.far", far)):
            if path is None:
                continue
            rep = g.check(path, budget, far=(key == "model3d.far"))
            reports[key] = rep
            r.errors += [f"{key} ({path.name}): {p}" for p in rep.problems]
            r.warnings += [f"{key} ({path.name}): {p}" for p in rep.warnings]
        renders = block.get("renders") if isinstance(block.get("renders"), dict) else {}
        for face in ("front", "rear", "iso"):
            if face in renders:
                self._file(renders[face], f"model3d.renders.{face}", r)
        want = renders.get("model_sha256")
        if want and full is not None and g.sha256(full) != want:
            r.errors.append(f"model3d.renders.model_sha256 does not match {block.get('file')} "
                            "(re-render the photos or update the hash)")
        rep = reports.get("model3d.file")
        if rep is None:
            return
        if e.kind in ("device", "module"):
            self._names(merged, block, rep, reports.get("model3d.far"), r)
        elif e.kind == "pluggable":
            kind = e.data.get("kind")
            conn = str(e.data.get("connector") or "")
            if kind == "transceiver" and conn and f"port:{conn}" not in rep.names:
                r.warnings.append(f"model: a transceiver should have a port:{conn} empty for its cord")
            if kind in ("plug", "cord") and "cable" not in rep.names:
                r.warnings.append("model: a cord or plug should end in a `cable` empty")

    def _names(self, merged: dict, block: dict, rep: g.GlbReport, far: g.GlbReport | None,
               r: Result) -> None:
        bays = _names(merged, ("module-bays",))
        ports = _names(merged, PORT_SECTIONS)
        got_bays = rep.prefixed("bay")
        got_empty = rep.prefixed("empty")
        got_ports = rep.prefixed("port")
        covers = {n[len("port:"):] for n in rep.prefixed("cover") if n.startswith("port:")}
        led_ports = {n[len("port:"):] for n in rep.prefixed("led") if n.startswith("port:")}
        for label, found in (("bay:", got_bays), ("empty:", got_empty)):
            extra = sorted(found - bays)
            if extra:
                r.errors.append(f"model: {label} names not in module-bays: {', '.join(extra[:6])}")
        lack = sorted(bays - got_bays)
        if lack:
            r.errors.append(f"model: module bays without a bay: anchor: {', '.join(lack[:6])}")
        if got_bays != got_empty:
            r.warnings.append("model: bay: and empty: names differ (every bay should have a cover)")
        for label, found in (("port:", got_ports), ("cover:port:", covers), ("led:port:", led_ports)):
            extra = sorted(found - ports)
            if extra:
                r.errors.append(f"model: {label} names not in the YAML's ports: {', '.join(extra[:6])}")
        missing = sorted(ports - got_ports)
        if missing:
            r.warnings.append(f"model: {len(missing)} YAML port(s) without a port: empty: {', '.join(missing[:4])}")
        leds = {n for n in rep.prefixed("led") if not n.startswith("port:")}
        bound = set((block.get("leds") or {}).keys())
        for k in sorted(bound - leds - {"port:*"}):
            r.warnings.append(f"model3d.leds.{k}: no led:{k} mesh in the model")
        for k in sorted(leds - bound):
            r.warnings.append(f"model: led:{k} has no binding in model3d.leds (it stays off)")
        if far is not None and not far.lfs_pointer:
            for prefix in ("bay", "empty", "led"):
                a = {n for n in rep.prefixed(prefix) if not n.startswith("port:")}
                b = {n for n in far.prefixed(prefix) if not n.startswith("port:")}
                if a != b:
                    r.warnings.append(f"model3d.far: {prefix}: names differ from the full model")

    # -- packs, bases, stray assets ----------------------------------------
    def pack(self, path: Path, data: dict | None, err: str | None) -> Result:
        r = Result(self.repo.rel(path))
        if err:
            r.errors.append(err)
            return r
        v = schemas.validator(self.schema_dir, "pack.schema.json")
        r.errors += [f"schema: {x}" for x in schemas.messages(v, data)]
        if data.get("id") not in (None, path.parent.name):
            r.errors.append(f"id {data.get('id')!r} differs from its folder {path.parent.name!r}")
        for kind, refs in (data.get("types") or {}).items():
            if kind not in KIND_DIRS or not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, str) and not self.repo.entry_path(kind, ref).is_file():
                    r.errors.append(f"types.{kind}: {ref} is not in danbyte/{KIND_DIRS[kind]}/")
        return r

    def base(self, path: Path) -> Result:
        """A Danbyte-only NetBox base under ``danbyte/types-new/``."""
        rel = self.repo.rel(path)
        r = Result(rel)
        try:
            doc = m.load_file(path)
        except Exception as exc:
            r.errors.append(f"not valid YAML: {exc}")
            return r
        sub = rel[len(TYPES_NEW) + 1:]
        kind = "device" if sub.startswith("device-types/") else "module" if sub.startswith("module-types/") else None
        if kind is None:
            r.errors.append("types-new holds device-types/ and module-types/ only")
            return r
        r.errors += [f"NetBox schema: {x}" for x in schemas.netbox_messages(
            self.upstream_schema_dir, self.schema_dir, kind, doc)]
        if (self.repo.root / sub).exists():
            r.warnings.append(f"upstream now has {sub}; point the overlay at it and drop this base")
        users = [e for e in self.repo.entries((kind,)) if e.upstream_rel == rel]
        if not users:
            r.warnings.append("no overlay uses this base")
        return r

    # -- selection ---------------------------------------------------------
    def select(self, paths: list[str]) -> tuple[list[Entry], list[Path], list[Path], bool]:
        """Entries, packs and bases to check for ``paths`` (repository files,
        changed or given). Returns ``(entries, packs, bases, everything)``."""
        repo = self.repo
        rels = set()
        for p in paths:
            pp = Path(p)
            if not pp.is_absolute():
                pp = (Path.cwd() / pp) if (Path.cwd() / pp).exists() else (repo.root / pp)
            try:
                rels.add(repo.rel(pp))
            except ValueError:
                continue
        kinds = {rel: repo.classify(rel) for rel in rels}
        if any(k in ("schema", "tools") for k in kinds.values()):
            return [], [], [], True
        all_entries = None
        entries: dict[str, Entry] = {}
        packs, bases = [], []
        for rel, kind in kinds.items():
            path = repo.root / rel
            if kind in KIND_DIRS:
                if path.is_file():
                    e = repo.load_entry(kind, path)
                    entries[e.rel] = e
            elif kind == "pack":
                if path.is_file():
                    packs.append(path)
            elif kind in ("types-new", "upstream", "asset"):
                if kind == "types-new" and path.is_file() and path.suffix in (".yaml", ".yml"):
                    bases.append(path)
                if all_entries is None:
                    all_entries = list(repo.entries())
                for e in all_entries:
                    if kind in ("types-new", "upstream") and e.upstream_rel == rel:
                        entries[e.rel] = e
                    if kind == "asset" and rel[len("danbyte/"):] in _asset_refs(e.data):
                        entries[e.rel] = e
        return list(entries.values()), packs, bases, False

    def run(self, paths: list[str] | None = None) -> list[Result]:
        everything = not paths
        entries, packs, bases = [], [], []
        if paths:
            entries, packs, bases, everything = self.select(paths)
        if everything:
            entries = list(self.repo.entries())
            packs = [p for p, _, _ in self.repo.packs()]
            bases = sorted((self.repo.root / TYPES_NEW).rglob("*.y*ml")) if (self.repo.root / TYPES_NEW).is_dir() else []
        out = [self.entry(e) for e in entries]
        pk = {p: (d, err) for p, d, err in self.repo.packs()}
        out += [self.pack(p, *pk.get(p, (None, "missing"))) for p in packs]
        out += [self.base(b) for b in bases]
        return out


def _asset_refs(data: dict) -> set[str]:
    block = data.get("model3d") if isinstance(data.get("model3d"), dict) else {}
    out = {block.get("file"), block.get("far")}
    renders = block.get("renders") if isinstance(block.get("renders"), dict) else {}
    out |= {renders.get(k) for k in ("front", "rear", "iso")}
    return {x for x in out if isinstance(x, str)}


def changed_files(repo: Repo, base: str) -> list[str]:
    """Files changed between ``base`` and the working tree (committed or not)."""
    cmd = ["git", "-C", str(repo.root), "diff", "--name-only", f"{base}...HEAD"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.split()
    local = subprocess.run(["git", "-C", str(repo.root), "diff", "--name-only", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.split()
    return sorted(set(out) | set(local))

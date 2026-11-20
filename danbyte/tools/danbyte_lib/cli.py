"""``danbyte-lib``: one command line for everyone working on the library."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import __version__
from . import merge as m
from . import yamlio
from .repo import KIND_DIRS, KIND_SCHEMAS, UPSTREAM_DIRS, Repo, RepoError


def _repo(args) -> Repo:
    return Repo(args.root) if args.root else Repo()


# -- new --------------------------------------------------------------------


def cmd_new(args) -> int:
    repo = _repo(args)
    ref = args.ref.strip("/")
    if ref.count("/") != 1:
        print("give Vendor/NAME, e.g. Cisco/C9300-24P", file=sys.stderr)
        return 2
    vendor, name = ref.split("/")
    kind = args.kind
    if kind is None:
        if (repo.root / "device-types" / vendor / f"{name}.yaml").is_file():
            kind = "device"
        elif (repo.root / "module-types" / vendor / f"{name}.yaml").is_file():
            kind = "module"
        else:
            print(f"no upstream device-types/{ref}.yaml or module-types/{ref}.yaml; "
                  "pass --kind (pluggable, rack-kit, rack-type, pack, or device/module for a "
                  "Danbyte-only base)", file=sys.stderr)
            return 2
    path = repo.danbyte / "packs" / name / "pack.yaml" if kind == "pack" else repo.entry_path(kind, ref)
    if path.exists() and not args.force:
        print(f"{repo.rel(path)} exists (--force to overwrite)", file=sys.stderr)
        return 1
    doc: dict = {"format": "danbyte-library/v1"}
    comments = []
    if kind in ("device", "module"):
        up = f"{UPSTREAM_DIRS[kind]}/{vendor}/{name}.yaml"
        if not (repo.root / up).is_file():
            base = f"danbyte/types-new/{up}"
            if not (repo.root / base).is_file():
                print(f"{up} is not upstream: write the NetBox YAML as {base} first", file=sys.stderr)
                return 2
            up = base
        doc["upstream"] = up
        doc["meta"] = {"category": "switch"} if kind == "device" else {"category": "module"}
        doc["model3d"] = {"file": f"assets/{vendor}/{name}.glb", "far": f"assets/{vendor}/{name}.far.glb"}
        comments = ["Patch upstream components by name; every add: entry needs a why:.",
                    "Preview the merge: danbyte-lib preview " + ref]
    elif kind == "pluggable":
        doc.update({"meta": {"category": "pluggable"}, "name": name, "kind": "transceiver",
                    "connector": "lc", "fits": [], "parts": [name],
                    "model3d": {"file": f"assets/{vendor}/{name}.glb"}})
    elif kind == "rack-kit":
        doc.update({"meta": {"category": "rack-kit"}, "name": name, "frame": "cabinet",
                    "model3d": {"file": f"assets/{vendor}/{name}.kit.glb",
                                "far": f"assets/{vendor}/{name}.far.glb"}})
    elif kind == "rack-type":
        doc.update({"meta": {"category": "rack"}, "manufacturer": vendor, "model": name,
                    "u_height": 42, "width": 19, "look": {}})
    elif kind == "pack":
        doc.update({"id": name, "name": name, "version": "0.1.0", "license": "CC-BY-4.0",
                    "author": "", "types": {}})
    yamlio.write(path, doc, danbyte_dir=repo.danbyte, schema=KIND_SCHEMAS[kind], comments=comments)
    print(f"wrote {repo.rel(path)}")
    return 0


# -- validate ---------------------------------------------------------------


def cmd_validate(args) -> int:
    from . import glb
    from .validate import Validator, changed_files

    repo = _repo(args)
    paths = list(args.paths)
    if args.changed_from:
        paths += changed_files(repo, args.changed_from)
        if not paths:
            print("nothing changed")
            return 0
    v = Validator(repo, assets=not args.skip_assets)
    results = v.run(paths or None)
    bad = warned = 0
    if not glb.pillow_available() and not args.skip_assets:
        print("(Pillow is not installed: blank-texture checks skipped)")
    for r in sorted(results, key=lambda r: r.rel):
        failed = bool(r.errors) or (args.strict and bool(r.warnings))
        bad += failed
        warned += bool(r.warnings)
        if failed or r.warnings or not args.quiet:
            print(f"{'FAIL' if failed else 'ok  '} {r.rel}")
        for x in r.errors:
            print(f"     error: {x}")
        for x in r.warnings:
            print(f"     warn:  {x}")
        if args.verbose:
            for x in r.notes:
                print(f"     note:  {x}")
    print(f"\n{len(results) - bad} of {len(results)} files valid"
          + (f", {warned} with warnings" if warned else ""))
    return 1 if bad else 0


# -- preview ----------------------------------------------------------------


def cmd_preview(args) -> int:
    repo = _repo(args)
    e = repo.find(args.ref)
    if e.error and not e.data:
        print(f"{e.rel}: {e.error}", file=sys.stderr)
        return 1
    if e.error:
        print(f"{e.rel}: {e.error}", file=sys.stderr)
        return 1
    try:
        doc = e.merged(strict=True)
    except m.MergeError as exc:
        print(f"{e.rel}: {exc}", file=sys.stderr)
        return 1
    if args.pack_paths:
        doc = m.rewrite_paths(doc, "assets/", "models/")
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write("---\n" + yamlio.dump(doc))
    return 0


# -- split-pack -------------------------------------------------------------


def cmd_split_pack(args) -> int:
    from .split_pack import split_pack

    repo = _repo(args)
    rep = split_pack(repo, Path(args.folder), rules_path=Path(args.rules) if args.rules else None,
                     dry_run=args.dry_run, force=args.force)
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {len(rep.written)} files: " + ", ".join(f"{v} {k}" for k, v in sorted(rep.counts.items())))
    print(f"{len(rep.bases)} Danbyte-only base(s) in danbyte/types-new/, {len(rep.assets)} asset(s)")
    for n in rep.notes:
        print(f"  note: {n}")
    if rep.placeholder_whys:
        print(f"  {len(rep.placeholder_whys)} addition(s) without a reason in the pack (placeholder why:):")
        for p in rep.placeholder_whys:
            print(f"    {p}")
    for p in rep.problems:
        print(f"  problem: {p}")
    return 1 if rep.problems else 0


# -- build-catalog ----------------------------------------------------------


def cmd_build_catalog(args) -> int:
    from . import catalog

    repo = _repo(args)
    today = dt.date.fromisoformat(args.today) if args.today else None
    out = Path(args.out)
    res = catalog.build(repo, out, today=today, copy_assets=not args.no_assets,
                        build_packs=not args.no_packs)
    print(f"{res['entries']} entries, {res['assets']} assets copied, {res['packs']} pack zip(s) built into {out}")
    for p in res["problems"]:
        print(f"  problem: {p}")
    bad = catalog.check(repo, out)
    for p in bad:
        print(f"  schema: {p}")
    if not bad:
        print("catalog.json, vendors.json and every item match catalog.schema.json")
    return 1 if bad or (res["problems"] and args.strict) else 0


# -- build-pack -------------------------------------------------------------


def cmd_build_pack(args) -> int:
    from . import build_pack

    repo = _repo(args)
    bundle_ids = {p.parent.name for p, _d, _e in repo.bundles()}
    ids = args.pack or ([p.parent.name for p, _d, _e in repo.packs()] + sorted(bundle_ids))
    bad = 0
    for pack_id in ids:
        try:
            if pack_id in bundle_ids:
                res = build_pack.build_bundle(repo, pack_id, Path(args.out))
            else:
                res = build_pack.build(repo, pack_id, Path(args.out))
        except build_pack.PackBuildError as exc:
            bad += 1
            print(f"{pack_id}: not built", file=sys.stderr)
            for line in exc.problems:
                print(f"  problem: {line}", file=sys.stderr)
            continue
        kinds = ", ".join(f"{v} {k}" for k, v in sorted(res["counts"].items()))
        print(f"{Path(args.out) / res['file']}: {res['files']} files, "
              f"{res['size'] / 1e6:.1f} MB ({kinds})")
        print(f"  sha256 {res['sha256']}")
    return 1 if bad else 0


# -- sync-check -------------------------------------------------------------


def cmd_sync_check(args) -> int:
    from . import sync

    repo = _repo(args)
    found = sync.run(repo)
    for f in found:
        print(f"{f.severity:7} {f.rel}: {f.message}")
    errors = sum(f.severity == "error" for f in found)
    print(f"\n{errors} overlay problem(s), {len(found) - errors} warning(s)")
    return 1 if errors else 0


# -- docs -------------------------------------------------------------------


def cmd_docs(args) -> int:
    from . import docs

    repo = _repo(args)
    out = Path(args.out) if args.out else repo.danbyte / "docs" / "reference"
    stale = docs.write(repo.danbyte / "schema", out, check=args.check)
    if args.check:
        if stale:
            print("out of date (run danbyte-lib docs): " + ", ".join(stale))
            return 1
        print("reference docs are up to date")
        return 0
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="danbyte-lib", description="Work on the Danbyte device library.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--root", help="the repository root (default: found from the working directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="create an overlay (or pluggable, rack, pack) with its schema header")
    s.add_argument("ref", help="Vendor/NAME, e.g. Cisco/C9300-24P")
    s.add_argument("--kind", choices=[*KIND_DIRS, "pack"])
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("validate", help="schema + upstream names + merged NetBox schema + glb checks")
    s.add_argument("paths", nargs="*", help="files to check (default: everything)")
    s.add_argument("--changed-from", metavar="REF", help="check what changed since a git ref")
    s.add_argument("--skip-assets", action="store_true", help="don't open glb / image files")
    s.add_argument("--strict", action="store_true", help="warnings fail too")
    s.add_argument("-q", "--quiet", action="store_true", help="print only files with findings")
    s.add_argument("-v", "--verbose", action="store_true", help="print notes too")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("preview", help="print the merged record (what Danbyte imports)")
    s.add_argument("ref", help="Vendor/NAME, kind/Vendor/NAME or a file path")
    s.add_argument("--pack-paths", action="store_true", help="show asset paths as in a pack zip (models/)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("split-pack", help="turn an edit-in-place pack folder into overlays + assets")
    s.add_argument("folder")
    s.add_argument("--rules", help="YAML rules: meta and why per glob")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true", help="overwrite files that differ")
    s.set_defaults(func=cmd_split_pack)

    s = sub.add_parser("build-catalog", help="generate catalog.json, items/, vendors.json and assets")
    s.add_argument("--out", default="_catalog")
    s.add_argument("--today", help="build date for lifecycle states (YYYY-MM-DD)")
    s.add_argument("--no-assets", action="store_true", help="don't copy glb / webp files")
    s.add_argument("--no-packs", action="store_true", help="don't build the pack zips")
    s.add_argument("--strict", action="store_true", help="skipped entries or missing assets fail")
    s.set_defaults(func=cmd_build_catalog)

    s = sub.add_parser("build-pack", help="build the zip Danbyte installs from a pack")
    s.add_argument("pack", nargs="*", help="pack or bundle id; every pack and bundle when left out")
    s.add_argument("--out", default="_packs")
    s.set_defaults(func=cmd_build_pack)

    s = sub.add_parser("sync-check", help="overlays whose patched names upstream no longer has")
    s.set_defaults(func=cmd_sync_check)

    s = sub.add_parser("docs", help="generate danbyte/docs/reference/ from the schemas")
    s.add_argument("--out")
    s.add_argument("--check", action="store_true", help="fail if the committed pages are stale")
    s.set_defaults(func=cmd_docs)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except RepoError as exc:
        print(f"danbyte-lib: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

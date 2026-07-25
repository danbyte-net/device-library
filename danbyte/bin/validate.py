#!/usr/bin/env python3
"""Validate every bundle in danbyte/types/ against the schema.

Also checks two things a JSON Schema can't express, and which are the mistakes
that actually happen:

1. A front port naming a rear port that isn't in the same bundle. The importer
   drops it with a warning, so a bundle shipping that way is quietly incomplete.
2. A referenced image path that doesn't exist in the repo. Photo-port markers
   are meaningless without the photo they were placed on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "danbyte/schema/danbyte-device-v1.json").read_text())


def check(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        bundle = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"not valid JSON: {exc}"]
    try:
        jsonschema.validate(bundle, SCHEMA)
    except jsonschema.ValidationError as exc:
        loc = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        problems.append(f"{loc}: {exc.message}")

    comps = bundle.get("components") or {}
    rears = {r.get("name") for r in comps.get("rear_ports") or []}
    for fp in comps.get("front_ports") or []:
        if fp.get("rear_port") not in rears:
            problems.append(
                f"front port {fp.get('name')!r} names rear port "
                f"{fp.get('rear_port')!r}, which isn't in this bundle"
            )

    for side, val in (bundle.get("images") or {}).items():
        if isinstance(val, str) and not (ROOT / val).exists():
            problems.append(f"{side} image {val!r} is missing from the repo")
    return problems


def main() -> int:
    files = sorted((ROOT / "danbyte/types").rglob("*.danbyte.json"))
    if not files:
        print("No bundles found — nothing to check.")
        return 0
    failed = 0
    for f in files:
        problems = check(f)
        rel = f.relative_to(ROOT)
        if problems:
            failed += 1
            print(f"FAIL {rel}")
            for p in problems:
                print(f"       {p}")
        else:
            print(f"ok   {rel}")
    print(f"\n{len(files) - failed}/{len(files)} bundles valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

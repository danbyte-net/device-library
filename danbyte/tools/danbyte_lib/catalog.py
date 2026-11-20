"""``danbyte-lib build-catalog``: the generated files the website reads.

Writes into an output folder::

    catalog.json                 one row per entry (schema: catalog.schema.json)
    items/<vendor>/<slug>.json   the row plus the merged record (#/$defs/item)
    vendors.json                 counts per vendor (#/$defs/vendors)
    assets/...                   the glb / far glb / webp files the rows name

The site never parses YAML; everything it sorts, filters and shows is here.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

from . import merge as m
from . import yamlio
from . import ports, schemas
from .repo import Entry, Repo, slugify

FORMAT = "danbyte-library/catalog/v1"
KIND_CATEGORY = {"module": "module", "pluggable": "pluggable", "rack-type": "rack", "rack-kit": "rack-kit"}
LIFECYCLE_KEYS = ("release_date", "end_of_sale", "end_of_security_updates", "end_of_support")


def _date(s: str) -> dt.date:
    parts = [int(x) for x in s.split("-")]
    return dt.date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)


def lifecycle_state(life: dict, today: dt.date) -> str:
    """current / end-of-sale / end-of-support / unknown on ``today``."""
    dates = {k: life[k] for k in LIFECYCLE_KEYS if isinstance(life.get(k), str)}
    if not dates:
        return "unknown"
    if "end_of_support" in dates and _date(dates["end_of_support"]) <= today:
        return "end-of-support"
    if "end_of_sale" in dates and _date(dates["end_of_sale"]) <= today:
        return "end-of-sale"
    return "current"


def _git_date(root: Path, paths: list[str]) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%cs", "--", *paths],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def row_of(repo: Repo, e: Entry, packs: dict[str, list[dict]], today: dt.date) -> tuple[dict, dict]:
    """``(row, record)`` of one entry."""
    record = e.merged(strict=False)
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    vendor = str(record.get("manufacturer") or e.vendor)
    if e.kind == "pluggable":
        model = str(record.get("name") or e.name)
    elif e.kind == "rack-kit":
        model = str(record.get("name") or e.name)
    else:
        model = str(record.get("model") or e.name)
    part = record.get("part_number")
    slug = str(record.get("slug") or "") or slugify(f"{vendor}-{part or e.name}")
    category = meta.get("category") or KIND_CATEGORY.get(e.kind) or "other"
    block = record.get("model3d") if isinstance(record.get("model3d"), dict) else {}
    renders = block.get("renders") if isinstance(block.get("renders"), dict) else {}
    life = dict(meta.get("lifecycle") or {})
    life["state"] = lifecycle_state(life, today)
    weight = None
    if _num(record.get("weight")) is not None and record.get("weight_unit"):
        weight = {"value": record["weight"], "unit": str(record["weight_unit"])}
    draws = [_num(p.get("maximum_draw")) for p in record.get("power-ports") or []]
    draws = [d for d in draws if d]
    vendor_slug = slugify(vendor)
    photos = {k: renders[k] for k in ("front", "rear", "iso") if isinstance(renders.get(k), str)}
    row = {
        "id": e.id,
        "kind": e.kind,
        "slug": slug,
        "vendor": vendor,
        "vendor_slug": vendor_slug,
        "model": model,
        "part_number": str(part) if part is not None else None,
        "family": meta.get("family"),
        "category": category,
        "module_kind": meta.get("module_kind"),
        "pluggable_kind": record.get("kind") if e.kind == "pluggable" else None,
        "roles": list(meta.get("roles") or []),
        "summary": meta.get("summary"),
        "u_height": _num(record.get("u_height")),
        "airflow": record.get("airflow"),
        "weight": weight,
        "max_power_w": sum(draws) if draws else None,
        "ports": ports.summary(record),
        "module_bays": [{"name": str(b.get("name")),
                         "position": str(b["position"]) if b.get("position") is not None else None,
                         "modules": list(b.get("modules") or [])}
                        for b in record.get("module-bays") or [] if isinstance(b, dict)],
        "pluggable": ({"connector": record.get("connector"), "fits": list(record.get("fits") or []),
                       "parts": list(record.get("parts") or []), "standard": record.get("standard"),
                       "wavelength_nm": record.get("wavelength_nm"),
                       "cable_profile": (record.get("cable") or {}).get("profile")}
                      if e.kind == "pluggable" else None),
        "lifecycle": life,
        "has_3d": isinstance(block.get("file"), str),
        "has_far": isinstance(block.get("far"), str),
        "has_photos": bool(photos),
        "thumbnail": photos.get("iso"),
        "model_url": block.get("file"),
        "far_url": block.get("far"),
        "models": {
            "far": block.get("far"),
            "standard": block.get("file"),
            "hq": block.get("hq") if isinstance(block.get("hq"), str) else _hq_beside(repo, block),
        },
        "photos": photos,
        "packs": packs.get(e.id, []),
        "links": list(meta.get("links") or []),
        "upstream": e.upstream_rel if e.upstream_rel and not e.upstream_rel.startswith("danbyte/") else None,
        "source": e.rel,
        "updated": _git_date(repo.root, [e.rel] + ([e.upstream_rel] if e.upstream_rel else [])),
        "item": f"items/{vendor_slug}/{slug}.json",
        "definition": f"items/{vendor_slug}/{slug}.yaml",
    }
    return row, record


def _packs(repo: Repo) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for _path, data, err in repo.packs():
        if err or not data:
            continue
        info = {k: str(data.get(k) or "") for k in ("id", "name", "version", "license", "author")}
        for kind, refs in (data.get("types") or {}).items():
            for ref in refs or []:
                out.setdefault(f"{kind}/{ref}", []).append(info)
    return out


def _hq_beside(repo: Repo, block: dict) -> str | None:
    """The close-up copy of a model, by convention: ``assets/V/P.glb`` has
    its ``assets-hq/V/P.hq.glb`` when one was made."""
    file = block.get("file")
    if not isinstance(file, str) or not file.startswith("assets/") or not file.endswith(".glb"):
        return None
    rel = "assets-hq/" + file[len("assets/"):-len(".glb")] + ".hq.glb"
    return rel if repo.asset(rel).is_file() else None


def build(repo: Repo, out: Path, *, today: dt.date | None = None, copy_assets: bool = True,
          build_packs: bool = False) -> dict:
    """Build the catalog into ``out``. Returns ``{entries, items, assets,
    packs, problems}``. ``build_packs`` also writes each pack's zip (the
    file Danbyte installs) under ``packs/`` and lists them in ``packs.json``."""
    today = today or dt.date.today()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    packs = _packs(repo)
    rows, problems, assets = [], [], set()
    used: set[str] = set()
    for e in repo.entries():
        if e.error and not e.data:
            problems.append(f"{e.rel}: {e.error} - skipped")
            continue
        try:
            row, record = row_of(repo, e, packs, today)
        except Exception as exc:  # a broken entry must not stop the build
            problems.append(f"{e.rel}: {exc} - skipped")
            continue
        if row["item"] in used:
            row["slug"] = f"{row['slug']}-{e.kind}"
            row["item"] = f"items/{row['vendor_slug']}/{row['slug']}.json"
            row["definition"] = f"items/{row['vendor_slug']}/{row['slug']}.yaml"
        used.add(row["item"])
        item = {"format": FORMAT, **row, "record": record, "additions": m.additions(e.data)}
        path = out / row["item"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # The same record as YAML: the definition file Danbyte imports.
        (out / row["definition"]).write_text("---\n" + yamlio.dump(record), encoding="utf-8")
        rows.append(row)
        for key in ("model_url", "far_url"):
            if row[key]:
                assets.add(row[key])
        if row["models"]["hq"]:
            assets.add(row["models"]["hq"])
        assets.update(row["photos"].values())
    rows.sort(key=lambda r: (r["vendor"].lower(), r["kind"], r["model"].lower()))
    catalog = {"format": FORMAT, "generated": today.isoformat(), "count": len(rows), "entries": rows}
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    vendors: dict[str, dict] = {}
    for r in rows:
        v = vendors.setdefault(r["vendor_slug"], {"vendor": r["vendor"], "slug": r["vendor_slug"], "count": 0,
                                                  "categories": {}, "families": set(), "with_3d": 0})
        v["count"] += 1
        v["categories"][r["category"]] = v["categories"].get(r["category"], 0) + 1
        if r["family"]:
            v["families"].add(r["family"])
        v["with_3d"] += int(r["has_3d"])
    vlist = [{**v, "families": sorted(v["families"])} for _, v in sorted(vendors.items())]
    (out / "vendors.json").write_text(json.dumps({"format": FORMAT, "generated": today.isoformat(),
                                                  "vendors": vlist}, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    copied = 0
    if copy_assets:
        for rel in sorted(assets):
            src = repo.asset(rel)
            if not src.is_file():
                problems.append(f"asset {rel} is missing - not copied")
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    built = []
    if build_packs:
        from . import build_pack

        for path, _data, _error in repo.packs():
            try:
                res = build_pack.build(repo, path.parent.name, out / "packs")
            except build_pack.PackBuildError as exc:
                problems += [f"pack {path.parent.name}: {line}" for line in exc.problems]
                continue
            built.append({"kind": "pack",
                          **{k: res[k] for k in ("id", "name", "description", "version", "license",
                                                 "author", "requires", "counts", "size", "sha256")},
                          "includes": [], "file": f"packs/{res['file']}"})
        info = {b["id"]: b for b in built}
        for path, _data, _error in repo.bundles():
            try:
                res = build_pack.build_bundle(repo, path.parent.name, out / "packs")
            except build_pack.PackBuildError as exc:
                problems += [f"bundle {path.parent.name}: {line}" for line in exc.problems]
                continue
            inside = [info[i] for i in res["includes"] if i in info]
            built.append({"kind": "bundle",
                          **{k: res[k] for k in ("id", "name", "description", "version", "counts",
                                                 "size", "sha256", "includes")},
                          # A bundle's licences and authors are its packs'.
                          "license": ", ".join(sorted({p["license"] for p in inside if p["license"]})),
                          "author": ", ".join(sorted({p["author"] for p in inside if p["author"]})),
                          "requires": [], "file": f"packs/{res['file']}"})
    (out / "packs.json").write_text(json.dumps({"format": FORMAT, "generated": today.isoformat(),
                                                "packs": built}, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    return {"entries": len(rows), "assets": copied, "packs": len(built), "problems": problems}


def check(repo: Repo, out: Path) -> list[str]:
    """Validate built files against catalog.schema.json."""
    folder = repo.danbyte / "schema"
    problems = []
    cat = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
    problems += [f"catalog.json: {x}" for x in schemas.messages(
        schemas.validator(folder, "catalog.schema.json"), cat)]
    vend = json.loads((out / "vendors.json").read_text(encoding="utf-8"))
    problems += [f"vendors.json: {x}" for x in schemas.messages(
        schemas.validator(folder, "catalog.schema.json", "/$defs/vendors"), vend)]
    packs_file = out / "packs.json"
    if packs_file.is_file():
        problems += [f"packs.json: {x}" for x in schemas.messages(
            schemas.validator(folder, "catalog.schema.json", "/$defs/packs"),
            json.loads(packs_file.read_text(encoding="utf-8")))]
    iv = schemas.validator(folder, "catalog.schema.json", "/$defs/item")
    for row in cat.get("entries", []):
        p = out / row["item"]
        if not p.is_file():
            problems.append(f"{row['item']}: missing")
            continue
        problems += [f"{row['item']}: {x}" for x in schemas.messages(iv, json.loads(p.read_text(encoding="utf-8")))]
    return problems

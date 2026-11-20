"""Loading the Danbyte schemas (and upstream NetBox's) and validating with them."""
from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match
from referencing import Registry, Resource


def _registry(folder: Path, decimals: bool = False) -> tuple[Registry, dict[str, dict]]:
    docs: dict[str, dict] = {}
    registry = Registry()
    for f in sorted(folder.glob("*.json")):
        text = f.read_text(encoding="utf-8")
        doc = json.loads(text, parse_float=Decimal) if decimals else json.loads(text)
        if not isinstance(doc, dict) or "$schema" not in doc:
            continue
        docs[f.name] = doc
        uri = doc.get("$id") or f.name
        registry = registry.with_resource(uri, Resource.from_contents(doc))
    return registry, docs


@lru_cache(maxsize=8)
def danbyte(folder: str) -> tuple[Registry, dict[str, dict]]:
    return _registry(Path(folder))


@lru_cache(maxsize=4)
def netbox(folder: str) -> tuple[Registry, dict[str, dict]]:
    return _registry(Path(folder), decimals=True)


def _decimals(doc):
    """Floats as Decimal, as upstream's own test does (``multipleOf: 0.01``
    fails on binary floats)."""
    if isinstance(doc, float):
        return Decimal(str(doc))
    if isinstance(doc, dict):
        return {k: _decimals(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_decimals(v) for v in doc]
    return doc


def validator(folder: Path, name: str, pointer: str = "") -> Draft202012Validator:
    registry, docs = danbyte(str(folder))
    schema = docs[name]
    if pointer:
        schema = {"$ref": f"{schema.get('$id', name)}#{pointer}"}
    return Draft202012Validator(schema, registry=registry)


def _short(msg: str, limit: int = 240) -> str:
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _where(err: ValidationError) -> str:
    return "/".join(str(p) for p in err.absolute_path) or "(top)"


def messages(v: Draft202012Validator, doc) -> list[str]:
    """Readable problems of ``doc`` against ``v``, one line each."""
    out = []
    for err in sorted(v.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        if err.context:
            inner = best_match(err.context)
            out.append(f"{_where(inner)}: {_short(inner.message)}")
        else:
            out.append(f"{_where(err)}: {_short(err.message)}")
    return list(dict.fromkeys(out))


def extension_types(folder: Path) -> dict[str, set[str]]:
    """Danbyte's extension port types by component class (common.schema.json)."""
    _, docs = danbyte(str(folder))
    ext = docs["common.schema.json"]["$defs"].get("extensionTypes", {}).get("properties", {})
    return {cls: set(spec.get("enum", [])) for cls, spec in ext.items()}


#: NetBox component list -> its class name in extension types.
SECTION_CLASS = {
    "console-ports": "console-port", "console-server-ports": "console-server-port",
    "power-ports": "power-port", "power-outlets": "power-outlet", "interfaces": "interface",
    "front-ports": "front-port", "rear-ports": "rear-port", "module-bays": "module-bay",
    "device-bays": "device-bay", "inventory-items": "inventory-item",
}
NETBOX_IDS = {
    "device": "urn:devicetype-library:device-type",
    "module": "urn:devicetype-library:module-type",
    "rack-type": "urn:devicetype-library:rack-type",
}


def netbox_messages(upstream_schema_dir: Path, danbyte_schema_dir: Path, kind: str, doc) -> list[str]:
    """Problems of a NetBox-only document (Danbyte keys stripped) against
    upstream's own schema. Danbyte extension types are accepted."""
    if not (upstream_schema_dir / "devicetype.json").is_file():
        return []
    registry, _ = netbox(str(upstream_schema_dir))
    v = Draft202012Validator({"$ref": NETBOX_IDS[kind]}, registry=registry)
    ext = extension_types(danbyte_schema_dir)
    out = []
    for err in v.iter_errors(_decimals(doc)):
        errs = list(err.context) if err.context else [err]
        keep = []
        for e in errs:
            path = list(e.absolute_path)
            if (len(path) == 3 and path[2] == "type"
                    and e.instance in ext.get(SECTION_CLASS.get(str(path[0]), ""), ())):
                continue
            keep.append(e)
        if keep:
            e = best_match(keep) if len(keep) > 1 else keep[0]
            out.append(f"{_where(e)}: {_short(e.message)}")
    return list(dict.fromkeys(out))

import json

import pytest
from jsonschema import Draft202012Validator

from danbyte_lib import cli, docs, schemas, sync
from danbyte_lib import merge as m
from danbyte_lib.repo import Repo

from helpers import REPO, tmp_repo, write

SCHEMA_DIR = REPO / "danbyte" / "schema"
FILES = sorted(p.name for p in SCHEMA_DIR.glob("*.schema.json"))


@pytest.mark.parametrize("name", FILES)
def test_schema_is_valid_and_its_examples_pass(name):
    doc = json.loads((SCHEMA_DIR / name).read_text())
    Draft202012Validator.check_schema(doc)
    assert doc.get("description"), "every schema says what it is"
    v = schemas.validator(SCHEMA_DIR, name)
    for ex in doc.get("examples", []):
        assert schemas.messages(v, ex) == []


@pytest.mark.parametrize("name", [n for n in FILES if n not in ("common.schema.json", "catalog.schema.json")])
def test_every_top_level_key_is_described(name):
    doc = json.loads((SCHEMA_DIR / name).read_text())
    for key, spec in doc.get("properties", {}).items():
        if key == "format":
            continue
        assert docs._description(spec, name) or "$ref" in spec, f"{name}: {key}"


def test_meta_dates_and_categories():
    v = schemas.validator(SCHEMA_DIR, "meta.schema.json")
    assert schemas.messages(v, {"category": "switch", "lifecycle": {
        "release_date": "2017-06", "end_of_sale": "2027-10-31", "source": "https://example.com/eol"}}) == []
    assert schemas.messages(v, {"category": "toaster"})
    assert schemas.messages(v, {"lifecycle": {"release_date": "2017", "source": "https://x"}})
    assert schemas.messages(v, {"links": [{"kind": "datasheet", "url": "ftp://x"}]})


def test_extension_types_come_from_the_schema():
    assert "cisco-stackpower" in schemas.extension_types(SCHEMA_DIR)["power-port"]


def test_reference_docs_are_up_to_date():
    assert docs.write(SCHEMA_DIR, REPO / "danbyte/docs/reference", check=True) == []


def test_sync_check_finds_what_upstream_broke(tmp_path):
    root = tmp_repo(tmp_path)
    write(root / "device-types/Acme/X1.yaml", "manufacturer: Acme\nmodel: X1\nslug: acme-x1\n"
          "u_height: 1\nis_full_depth: false\ninterfaces:\n  - {name: Gi1, type: 1000base-t}\n"
          "  - {name: Gi2, type: 1000base-t}\n")
    write(root / "danbyte/overlays/Acme/X1.yaml", "upstream: device-types/Acme/X1.yaml\n"
          "interfaces:\n  Gone1: {type: 10gbase-t}\n  add: [{name: Gi2, type: 1000base-t, why: x}]\n")
    write(root / "danbyte/overlays/Acme/X2.yaml", "upstream: device-types/Acme/X2.yaml\n")
    found = {(f.rel, f.severity, f.message.split(":")[0]) for f in sync.run(Repo(root))}
    assert ("danbyte/overlays/Acme/X1.yaml", "error", "interfaces") in found
    assert ("danbyte/overlays/Acme/X1.yaml", "warning", "interfaces") in found
    assert any(r == "danbyte/overlays/Acme/X2.yaml" for r, _, _ in found)
    assert cli.main(["--root", str(root), "sync-check"]) == 1


def test_sync_check_on_the_repository():
    assert [f for f in sync.run(Repo(REPO)) if f.severity == "error"] == []


def test_new_writes_an_overlay_with_its_schema_line(tmp_path, capsys):
    root = tmp_repo(tmp_path)
    write(root / "device-types/Acme/X1.yaml", "manufacturer: Acme\nmodel: X1\nslug: acme-x1\n"
          "u_height: 1\nis_full_depth: false\n")
    assert cli.main(["--root", str(root), "new", "Acme/X1"]) == 0
    text = (root / "danbyte/overlays/Acme/X1.yaml").read_text()
    assert text.startswith("# yaml-language-server: $schema=../../schema/overlay.schema.json")
    doc = m.load_yaml(text)
    assert doc["upstream"] == "device-types/Acme/X1.yaml"
    assert schemas.messages(schemas.validator(root / "danbyte/schema", "overlay.schema.json"), doc) == []
    assert cli.main(["--root", str(root), "new", "Acme/X1"]) == 1  # no overwrite
    assert cli.main(["--root", str(root), "new", "Acme/SFP-1", "--kind", "pluggable"]) == 0
    assert cli.main(["--root", str(root), "new", "Acme/Nope"]) == 2

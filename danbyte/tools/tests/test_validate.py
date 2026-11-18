import hashlib

import pytest

from danbyte_lib import cli
from danbyte_lib.repo import Repo
from danbyte_lib.validate import Validator

from helpers import REPO, glb_bytes, good_glb, tmp_repo, write

UPSTREAM = """---
manufacturer: Acme
model: X1
slug: acme-x1
u_height: 1
is_full_depth: false
interfaces:
  - name: Gi1/0/1
    type: 1000base-t
module-bays:
  - name: PS-A
    position: A
"""


def _repo(tmp_path, overlay: str, glb_doc=None) -> Repo:
    root = tmp_repo(tmp_path)
    write(root / "device-types/Acme/X1.yaml", UPSTREAM)
    write(root / "danbyte/overlays/Acme/X1.yaml", overlay)
    if glb_doc is not None:
        p = root / "danbyte/assets/Acme/X1.glb"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(glb_bytes(glb_doc))
    return Repo(root)


def _one(repo, assets=True):
    [r] = Validator(repo, assets=assets).run([str(repo.danbyte / "overlays/Acme/X1.yaml")])
    return r


def test_a_good_overlay_passes(tmp_path):
    repo = _repo(tmp_path, """upstream: device-types/Acme/X1.yaml
meta: {category: switch, family: X}
power-ports:
  add:
    - {name: SP-A, type: cisco-stackpower, why: StackPower receptacle}
module-bays:
  PS-A: {modules: [PSU-1]}
model3d:
  file: assets/Acme/X1.glb
  leds: {SYS: {source: device.status}}
""", good_glb(names=("bay:PS-A", "empty:PS-A", "port:Gi1/0/1", "port:SP-A", "led:SYS")))
    r = _one(repo)
    assert r.errors == [] and r.warnings == []


@pytest.mark.parametrize("overlay, fragment", [
    ("upstream: device-types/Acme/X1.yaml\ninterfaces: {Gi9: {type: 10gbase-t}}\n", "'Gi9' is not upstream"),
    ("upstream: device-types/Acme/X1.yaml\ninterfaces:\n  add: [{name: Gi2, type: 1000base-t}]\n",
     "'why' is a required property"),
    ("upstream: device-types/Acme/X1.yaml\ninterfaces:\n  add: [{name: Gi2, type: bogus, why: test}]\n",
     "merged (NetBox schema): interfaces/1/type"),
    ("upstream: device-types/Acme/X1.yaml\nmeta: {category: module}\n", "meta/category"),
    ("upstream: device-types/Acme/X1.yaml\nmeta: {lifecycle: {end_of_sale: 2027-10-31}}\n", "'source' is a dependency"),
    ("upstream: device-types/Acme/X1.yaml\nmodel3d: {file: assets/Acme/X1.glb}\n", "is missing"),
    ("upstream: device-types/Acme/X2.yaml\n", "upstream file device-types/Acme/X2.yaml is missing"),
    ("upstream: device-types/Acme/X1.yaml\nmodel3d: {file: assets/Acme/X1.glb, renders: {modules: {PS-Z: P}}}\n",
     "bay 'PS-Z' is not in module-bays"),
    ("upstream: device-types/Acme/X1.yaml\nbogus: 1\n", "bogus"),
])
def test_problems_are_found(tmp_path, overlay, fragment):
    r = _one(_repo(tmp_path, overlay))
    assert any(fragment in e for e in r.errors), r.errors


def test_glb_names_against_the_yaml(tmp_path):
    repo = _repo(tmp_path, "upstream: device-types/Acme/X1.yaml\nmodel3d: {file: assets/Acme/X1.glb}\n",
                 good_glb(names=("bay:PS-B", "empty:PS-B", "port:Gi9", "led:SYS")))
    r = _one(repo)
    text = " | ".join(r.errors)
    assert "bay: names not in module-bays: PS-B" in text
    assert "module bays without a bay: anchor: PS-A" in text
    assert "port: names not in the YAML's ports: Gi9" in text
    assert any("led:SYS has no binding" in w for w in r.warnings)


def test_model_sha256_is_checked(tmp_path):
    doc = good_glb(names=("bay:PS-A", "empty:PS-A", "port:Gi1/0/1"))
    repo = _repo(tmp_path, "upstream: device-types/Acme/X1.yaml\nmodel3d: {file: assets/Acme/X1.glb, "
                           "renders: {model_sha256: " + "a" * 64 + "}}\n", doc)
    assert any("model_sha256 does not match" in e for e in _one(repo).errors)
    real = hashlib.sha256((repo.danbyte / "assets/Acme/X1.glb").read_bytes()).hexdigest()
    write(repo.danbyte / "overlays/Acme/X1.yaml", "upstream: device-types/Acme/X1.yaml\nmodel3d: "
          "{file: assets/Acme/X1.glb, renders: {model_sha256: " + real + "}}\n")
    assert _one(repo).errors == []


def test_pack_refs_must_exist(tmp_path):
    repo = _repo(tmp_path, "upstream: device-types/Acme/X1.yaml\n")
    write(repo.danbyte / "packs/p1/pack.yaml", "id: p1\nname: P\nversion: 1.0.0\nlicense: CC-BY-4.0\n"
          "author: A\ntypes: {device: [Acme/X1, Acme/X9]}\n")
    results = {r.rel: r for r in Validator(repo).run()}
    assert any("Acme/X9" in e for e in results["danbyte/packs/p1/pack.yaml"].errors)


def test_changed_upstream_selects_its_overlay(tmp_path):
    repo = _repo(tmp_path, "upstream: device-types/Acme/X1.yaml\n")
    results = Validator(repo).run([str(repo.root / "device-types/Acme/X1.yaml")])
    assert [r.rel for r in results] == ["danbyte/overlays/Acme/X1.yaml"]


def test_types_new_base_is_checked_against_netbox(tmp_path):
    repo = _repo(tmp_path, "upstream: danbyte/types-new/device-types/Acme/X9.yaml\n")
    write(repo.root / "danbyte/types-new/device-types/Acme/X9.yaml", "manufacturer: Acme\nmodel: X9\n")
    results = {r.rel: r for r in Validator(repo).run()}
    assert any("'slug' is a required property" in e
               for e in results["danbyte/types-new/device-types/Acme/X9.yaml"].errors)


def test_the_repository_validates():
    """Everything in danbyte/ passes; glb checks run when the assets are present
    (they are Git LFS files, absent in a checkout without them)."""
    repo = Repo(REPO)
    have_assets = any((repo.danbyte / "assets").rglob("*.glb")) if (repo.danbyte / "assets").is_dir() else False
    results = Validator(repo, assets=have_assets).run()
    bad = {r.rel: r.errors for r in results if r.errors}
    assert not bad
    assert len(results) > 80


def test_cli_validate_exit_codes(tmp_path, capsys):
    repo = _repo(tmp_path, "upstream: device-types/Acme/X1.yaml\ninterfaces: {Gi9: {type: x}}\n")
    assert cli.main(["--root", str(repo.root), "validate"]) == 1
    assert "FAIL danbyte/overlays/Acme/X1.yaml" in capsys.readouterr().out
    write(repo.danbyte / "overlays/Acme/X1.yaml", "upstream: device-types/Acme/X1.yaml\n")
    assert cli.main(["--root", str(repo.root), "validate"]) == 0

from danbyte_lib import cli
from danbyte_lib import merge as m
from danbyte_lib.repo import Repo
from danbyte_lib.split_pack import comment_whys, guess_module_kind, split_pack
from danbyte_lib.validate import Validator

from helpers import glb_bytes, good_glb, tmp_repo, write

UPSTREAM = """---
manufacturer: Acme
model: X1
slug: acme-x1
u_height: 1
is_full_depth: false
interfaces:
  - name: Gi1
    type: 1000base-t
  - name: Stack1
    type: cisco-stackwise
  - name: Mgmt
    type: 1000base-t
    mgmt_only: true
module-bays:
  - name: PS-A
    position: A
  - name: PS-B
    position: B
"""

PACK_TYPE = """---
manufacturer: Acme
model: X1
slug: acme-x1
u_height: 1
is_full_depth: false
front_image: true
interfaces:
  - name: Gi1
    type: 1000base-t
  # danbyte: Stack1 moved to a stack kit bay
  - name: Mgmt
    type: 1000base-t
    mgmt_only: true
power-ports:
  - name: SP-A
    type: cisco-stackpower
module-bays:
  - name: PS-A
    position: A
    modules: [PSU-1, PSU-2]
  # Danbyte overlay: fan bays added (install guide figure 3)
  - name: FAN 1
    position: '1'
  - name: FAN 2
    position: '2'
  - name: PS-B
    position: B
rack_mount:
  # danbyte: from the install guide
  ears: true
model3d:
  file: models/Acme/X1.glb
"""

NEW_MODULE = """---
# Danbyte overlay: not in the NetBox library.
manufacturer: Acme
model: PWR-X1
part_number: PWR-X1
model3d:
  file: models/Acme/PWR-X1.glb
"""


def _setup(tmp_path):
    root = tmp_repo(tmp_path)
    write(root / "device-types/Acme/X1.yaml", UPSTREAM)
    pack = tmp_path / "pack"
    write(pack / "pack.yaml", "id: acme-pack\nname: Acme\nversion: 1.2.3\nlicense: CC-BY-4.0\nauthor: A\n"
          "types: {device: [X1], module: [PWR-X1], accessory: [SFP-X]}\n")
    write(pack / "device-types/Acme/X1.yaml", PACK_TYPE)
    write(pack / "module-types/Acme/PWR-X1.yaml", NEW_MODULE)
    write(pack / "accessory-types/Acme/SFP-X.yaml", "name: SFP-X\nkind: transceiver\nconnector: lc\n"
          "fits: [10gbase-x-sfpp]\nmodel3d:\n  file: models/Acme/SFP-X.glb\n")
    (pack / "models/Acme").mkdir(parents=True)
    for n in ("X1", "PWR-X1", "SFP-X"):
        (pack / f"models/Acme/{n}.glb").write_bytes(glb_bytes(good_glb(names=())))
    write(pack / "REPORT.md", "notes\n")
    return Repo(root), pack


def test_comment_whys():
    whys, keys, header = comment_whys(PACK_TYPE)
    assert whys[("module-bays", "FAN 1")] == "fan bays added (install guide figure 3)"
    assert whys[("interfaces", "Mgmt")] == "Stack1 moved to a stack kit bay"
    assert keys == {"rack_mount": "from the install guide"}
    assert comment_whys(NEW_MODULE)[2] == ["not in the NetBox library."]


def test_split_pack_writes_overlays_bases_assets_and_pack(tmp_path):
    repo, pack = _setup(tmp_path)
    rep = split_pack(repo, pack)
    assert rep.problems == []
    ov = m.load_file(repo.danbyte / "overlays/Acme/X1.yaml")
    assert ov["upstream"] == "device-types/Acme/X1.yaml"
    assert ov["front_image"] is True
    assert ov["interfaces"]["remove"] == [{"name": "Stack1", "why": "Stack1 moved to a stack kit bay"}]
    adds = ov["module-bays"]["add"]
    assert [a["name"] for a in adds] == ["FAN 1", "FAN 2"]
    assert adds[0]["after"] == "PS-A" and adds[1]["after"] == "FAN 1"
    assert adds[1]["why"] == adds[0]["why"] == "fan bays added (install guide figure 3)"
    assert ov["module-bays"]["PS-A"] == {"modules": ["PSU-1", "PSU-2"]}
    assert ov["model3d"]["file"] == "assets/Acme/X1.glb"
    # the StackPower port had no comment: a placeholder that names the pack
    assert rep.placeholder_whys == ["device/Acme/X1: power-ports/SP-A"]
    assert "acme-pack 1.2.3" in ov["power-ports"]["add"][0]["why"]
    text = (repo.danbyte / "overlays/Acme/X1.yaml").read_text()
    assert text.startswith("# yaml-language-server: $schema=../../schema/overlay.schema.json\n")
    assert "# from the install guide\nrack_mount:" in text
    # merged back, it is the pack's file
    merged = repo.find("Acme/X1").merged()
    want = m.rewrite_paths(m.load_yaml(PACK_TYPE), "models/", "assets/")
    assert merged == want
    # a module upstream lacks: its NetBox part in types-new, an overlay on top
    base = m.load_file(repo.root / "danbyte/types-new/module-types/Acme/PWR-X1.yaml")
    assert "model3d" not in base and base["model"] == "PWR-X1"
    mo = m.load_file(repo.danbyte / "module-overlays/Acme/PWR-X1.yaml")
    assert mo["upstream"] == "danbyte/types-new/module-types/Acme/PWR-X1.yaml"
    assert mo["meta"] == {"category": "module", "module_kind": "psu"}
    plug = m.load_file(repo.danbyte / "pluggables/Acme/SFP-X.yaml")
    assert plug["model3d"]["file"] == "assets/Acme/SFP-X.glb" and plug["meta"]["category"] == "pluggable"
    assert (repo.danbyte / "assets/Acme/X1.glb").is_file()
    pk = m.load_file(repo.danbyte / "packs/acme-pack/pack.yaml")
    assert pk["types"] == {"device": ["Acme/X1"], "module": ["Acme/PWR-X1"], "pluggable": ["Acme/SFP-X"]}
    assert any("REPORT.md" in n for n in rep.notes)
    # the result validates (schema + names + NetBox); the stand-in glbs carry no anchors
    results = Validator(repo, assets=False).run()
    assert all(r.ok for r in results), [(r.rel, r.errors) for r in results if not r.ok]


def test_rules_supply_meta_and_whys(tmp_path):
    repo, pack = _setup(tmp_path)
    rules = write(tmp_path / "rules.yaml", '- match: "device/Acme/*"\n  meta: {category: switch, family: X}\n'
                  '  why: {"power-ports/SP-*": "StackPower receptacle", "front_image": "photo rendered"}\n')
    rep = split_pack(repo, pack, rules_path=rules)
    assert rep.placeholder_whys == []
    ov = m.load_file(repo.danbyte / "overlays/Acme/X1.yaml")
    assert ov["meta"] == {"category": "switch", "family": "X"}
    assert ov["power-ports"]["add"][0]["why"] == "StackPower receptacle"
    assert "# photo rendered\nfront_image: true" in (repo.danbyte / "overlays/Acme/X1.yaml").read_text()


def test_existing_files_are_not_overwritten_without_force(tmp_path):
    repo, pack = _setup(tmp_path)
    split_pack(repo, pack)
    write(repo.danbyte / "overlays/Acme/X1.yaml", "upstream: device-types/Acme/X1.yaml\n")
    rep = split_pack(repo, pack)
    assert any("exists and differs" in p for p in rep.problems)
    assert split_pack(repo, pack, force=True).problems == []


def test_dry_run_writes_nothing(tmp_path, capsys):
    repo, pack = _setup(tmp_path)
    assert cli.main(["--root", str(repo.root), "split-pack", str(pack), "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out
    assert not (repo.danbyte / "overlays").exists()


def test_guess_module_kind():
    assert guess_module_kind("PWR-C1-350WAC-P") == "psu"
    assert guess_module_kind("C9K-PWR-650WAC-R") == "psu"
    assert guess_module_kind("FAN-T2") == "fan"
    assert guess_module_kind("C9300-NM-8X") == "network-module"
    assert guess_module_kind("C9200-STACK-KIT") == "stack-kit"
    assert guess_module_kind("WIDGET") is None

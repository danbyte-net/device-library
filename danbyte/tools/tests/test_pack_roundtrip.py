"""Every type converted from the cisco-catalyst-9500 pack merges back to the
pack's own (edit-in-place) YAML: same interfaces, bays, power ports, model3d.

The fixtures are the pack's YAML files as split (v1.6.28), copied unchanged."""
import json

import pytest

from danbyte_lib import cli
from danbyte_lib import merge as m
from danbyte_lib.repo import Repo
from danbyte_lib.split_pack import comparable, normalise

from helpers import PACK_FIXTURE, REPO

repo = Repo(REPO)
PAIRS = [("device", p) for p in sorted((PACK_FIXTURE / "device-types").glob("*/*.yaml"))] + \
        [("module", p) for p in sorted((PACK_FIXTURE / "module-types").glob("*/*.yaml"))]


def _ids(pairs):
    return [f"{k}/{p.parent.name}/{p.stem}" for k, p in pairs]


@pytest.mark.parametrize("kind, src", PAIRS, ids=_ids(PAIRS))
def test_type_merges_back_to_the_pack_file(kind, src):
    entry = repo.find(f"{kind}/{src.parent.name}/{src.stem}")
    assert entry.error is None
    merged = entry.merged(strict=True)
    want = normalise(m.load_file(src))
    for key in ("interfaces", "module-bays", "power-ports", "console-ports", "power-outlets", "model3d",
                "rack_mount"):
        assert merged.get(key) == want.get(key), key
    assert comparable(merged) == comparable(want)
    # and the pack's own paths come back with --pack-paths
    assert m.rewrite_paths(merged, "assets/", "models/").get("model3d") == m.load_file(src).get("model3d")


@pytest.mark.parametrize("ref", ["Cisco/C9300-24P", "Cisco/C9300-48P", "Cisco/C9300L-24P-4G"])
def test_preview_of_c9300_overlays_matches_the_pack(ref, capsys):
    assert cli.main(["--root", str(REPO), "preview", ref, "--json", "--pack-paths"]) == 0
    got = json.loads(capsys.readouterr().out)
    want = m.load_file(PACK_FIXTURE / "device-types" / f"{ref}.yaml")
    got.pop("meta", None)
    want.pop("meta", None)
    assert got == want


ACC = sorted((PACK_FIXTURE / "accessory-types").glob("*/*.yaml"))


@pytest.mark.parametrize("src", ACC, ids=[f"{p.parent.name}/{p.stem}" for p in ACC])
def test_pluggable_is_the_accessory(src):
    e = repo.find(f"pluggable/{src.parent.name}/{src.stem}")
    got = {k: v for k, v in e.data.items() if k not in ("format", "meta")}
    want = {k: v for k, v in m.load_file(src).items() if k != "meta"}
    assert m.rewrite_paths(got, "assets/", "models/") == want
    assert e.data["meta"]["category"] == "pluggable"


KITS = sorted((PACK_FIXTURE / "rack-kits").glob("*/*/kit.yaml"))
RACKS = sorted((PACK_FIXTURE / "rack-types").glob("*/*.yaml"))


@pytest.mark.parametrize("src", KITS + RACKS, ids=[p.parent.name if p.name == "kit.yaml" else p.stem
                                                    for p in KITS + RACKS])
def test_racks_are_the_pack_racks(src):
    if src.name == "kit.yaml":
        e = repo.find(f"rack-kit/{src.parent.parent.name}/{src.parent.name}")
    else:
        e = repo.find(f"rack-type/{src.parent.name}/{src.stem}")
    got = {k: v for k, v in e.data.items() if k not in ("format", "meta")}
    want = {k: v for k, v in m.load_file(src).items() if k != "meta"}
    assert m.rewrite_paths(got, "assets/", "models/") == want


def test_pack_lists_every_converted_entry():
    # The source folder is split into several library packs: together they
    # list every entry of the source, each at least once.
    src = m.load_file(PACK_FIXTURE / "pack.yaml")
    listed: dict[str, set] = {}
    ids = set()
    for path in sorted((REPO / "danbyte/packs").glob("*/pack.yaml")):
        pack = m.load_file(path)
        ids.add(pack["id"])
        assert pack["version"] == src["version"], path
        for need in pack.get("requires", []):
            assert (REPO / "danbyte/packs" / need / "pack.yaml").is_file(), (path, need)
        for kind, refs in pack["types"].items():
            listed.setdefault(kind, set()).update(r.split("/", 1)[1] for r in refs)
    assert len(ids) == 6
    for kind, key in (("device", "device"), ("module", "module"), ("pluggable", "accessory"),
                      ("rack-kit", "rack_kit"), ("rack-type", "rack")):
        assert listed[kind] == set(src["types"][key]), kind


def test_every_addition_says_why():
    for e in repo.entries(("device", "module")):
        for row in m.additions(e.data):
            if row["action"] in ("add", "remove"):
                assert row.get("why"), (e.rel, row)
                assert "no reason recorded" not in row["why"], (e.rel, row)

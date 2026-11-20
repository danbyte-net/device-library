"""The pack zip Danbyte installs, built from the library."""
import io
import zipfile

from danbyte_lib import build_pack
from danbyte_lib import merge as m
from danbyte_lib.repo import Repo

from helpers import PACK_FIXTURE, REPO

repo = Repo(REPO)
PACK = "cisco-catalyst-9300"


def _zip():
    files, facts = build_pack.pack_files(repo, PACK)
    name = f"{facts['id']}-{facts['version']}"
    return name, zipfile.ZipFile(io.BytesIO(build_pack.zip_bytes(files, name))), facts


def test_the_zip_is_the_pack_folder_danbyte_reads():
    name, z, facts = _zip()
    names = z.namelist()
    assert all(n.startswith(f"{name}/") for n in names)
    manifest = m.load_yaml(z.read(f"{name}/pack.yaml").decode())
    src = m.load_file(PACK_FIXTURE / "pack.yaml")
    assert manifest["id"] == PACK and manifest["name"] == "Cisco Catalyst 9300"
    assert manifest["version"] == src["version"] == facts["version"]
    assert manifest["requires"] == ["generic-connectors"]
    assert "format" not in manifest
    # Its own entries only, under the pack's own keys, each with its file.
    assert "C9300-48P" in manifest["types"]["device"]
    assert "C9200-24P" not in manifest["types"]["device"]
    assert "FAN-T2" in manifest["types"]["module"]
    assert sorted(manifest["types"]["accessory"]) == ["CAB-SPWR", "STACK-T1", "STACK-T3", "STACK-T3A"]
    assert f"{name}/device-types/Cisco/C9300-48P.yaml" in names
    assert not any("/rack-kits/" in n for n in names)
    # A rack kit keeps its folder.
    files, _ = build_pack.pack_files(repo, "generic-racks")
    assert "rack-kits/Generic/CABINET/kit.yaml" in files
    assert "rack-types/Generic/Generic-12U-wall.yaml" in files


def test_every_file_is_the_pack_authors_file():
    for pack in sorted(p.parent.name for p in (REPO / "danbyte/packs").glob("*/pack.yaml")):
        files, _ = build_pack.pack_files(repo, pack)
        for rel, blob in files.items():
            if rel == "pack.yaml" or not rel.endswith(".yaml"):
                continue
            src = PACK_FIXTURE / rel
            assert src.is_file(), rel
            got, want = m.load_yaml(blob.decode()), m.load_file(src)
            # meta is the library's (category added on the split); the rest is the pack's.
            got.pop("meta", None)
            want.pop("meta", None)
            assert got == want, rel


def test_every_named_model_is_in_the_zip():
    name, z, _ = _zip()
    names = set(z.namelist())
    for n in sorted(names):
        if n.endswith(".yaml"):
            for path in build_pack._models(m.load_yaml(z.read(n).decode())):
                assert f"{name}/{path}" in names, (n, path)
    assert any(n.endswith(".glb") for n in names)


def test_the_build_is_deterministic(tmp_path):
    a = build_pack.build(repo, PACK, tmp_path / "a")
    b = build_pack.build(repo, PACK, tmp_path / "b")
    assert a["sha256"] == b["sha256"]
    assert (tmp_path / "a" / a["file"]).stat().st_size == a["size"]
    assert a["counts"]["device"] == len(list((PACK_FIXTURE / "device-types/Cisco").glob("C9300*.yaml")))


def test_an_unknown_pack_is_refused():
    try:
        build_pack.pack_files(repo, "no-such-pack")
    except build_pack.PackBuildError as exc:
        assert "no pack named" in exc.problems[0]
    else:
        raise AssertionError("built a pack that doesn't exist")


def test_a_bundle_is_its_packs_in_one_zip():
    files, facts = build_pack.bundle_files(repo, "cisco-catalyst")
    manifest = m.load_yaml(files["bundle.yaml"].decode())
    assert manifest["id"] == "cisco-catalyst"
    assert [p["id"] for p in manifest["packs"]] == facts["includes"]
    # Connectors first: the packs after it require it.
    assert facts["includes"][0] == "generic-connectors"
    assert "generic-racks" not in facts["includes"]
    # Each pack is its complete folder, byte for byte what its own zip holds.
    for pid in facts["includes"]:
        own, _ = build_pack.pack_files(repo, pid)
        for rel, blob in own.items():
            assert files[f"packs/{pid}/{rel}"] == blob, (pid, rel)
    assert facts["version"] == m.load_file(PACK_FIXTURE / "pack.yaml")["version"]


def test_bundles_build_deterministically_and_cover_the_library(tmp_path):
    a = build_pack.build_bundle(repo, "danbyte-starter", tmp_path / "a")
    b = build_pack.build_bundle(repo, "danbyte-starter", tmp_path / "b")
    assert a["sha256"] == b["sha256"]
    packs = sorted(p.parent.name for p in (REPO / "danbyte/packs").glob("*/pack.yaml"))
    assert sorted(a["includes"]) == packs


def test_every_bundle_is_valid():
    from danbyte_lib.validate import Validator

    v = Validator(repo)
    for path, data, err in repo.bundles():
        r = v.bundle(path, data, err)
        assert r.errors == [], (path, r.errors)
    # A bundle missing what a pack requires, or listing it too late, is refused.
    path = REPO / "danbyte/bundles/cisco-catalyst/bundle.yaml"
    bad = v.bundle(path, {"id": "cisco-catalyst", "name": "x", "includes": ["cisco-optics"]}, None)
    assert any("requires generic-connectors" in e for e in bad.errors)
    late = v.bundle(path, {"id": "cisco-catalyst", "name": "x",
                           "includes": ["cisco-optics", "generic-connectors"]}, None)
    assert any("must come before" in e for e in late.errors)

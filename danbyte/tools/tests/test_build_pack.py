"""The pack zip Danbyte installs, built from the library."""
import io
import zipfile

from danbyte_lib import build_pack
from danbyte_lib import merge as m
from danbyte_lib.repo import Repo

from helpers import PACK_FIXTURE, REPO

repo = Repo(REPO)
PACK = "cisco-catalyst-9500"


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
    assert manifest["id"] == PACK
    assert manifest["version"] == src["version"] == facts["version"]
    assert "format" not in manifest
    # The same entries, under the pack's own keys.
    for key in ("device", "module", "accessory", "rack_kit", "rack"):
        assert sorted(manifest["types"][key]) == sorted(src["types"][key]), key
    assert f"{name}/rack-kits/Generic/CABINET/kit.yaml" in names
    assert f"{name}/rack-types/Generic/Generic-12U-wall.yaml" in names


def test_every_file_is_the_pack_authors_file():
    name, z, _ = _zip()
    for folder in ("device-types", "module-types", "accessory-types", "rack-types"):
        for src in sorted((PACK_FIXTURE / folder).glob("*/*.yaml")):
            got = m.load_yaml(z.read(f"{name}/{folder}/{src.parent.name}/{src.name}").decode())
            want = m.load_file(src)
            # meta is the library's (category added on the split); the rest is the pack's.
            got.pop("meta", None)
            want.pop("meta", None)
            assert got == want, src.name


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
    assert a["counts"]["device"] == len(list((PACK_FIXTURE / "device-types").glob("*/*.yaml")))


def test_an_unknown_pack_is_refused():
    try:
        build_pack.pack_files(repo, "no-such-pack")
    except build_pack.PackBuildError as exc:
        assert "no pack named" in exc.problems[0]
    else:
        raise AssertionError("built a pack that doesn't exist")

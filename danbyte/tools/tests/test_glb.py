import copy

from danbyte_lib import glb

from helpers import glb_bytes, good_glb


def _check(tmp_path, doc, name="x.glb", budget=None):
    p = tmp_path / name
    p.write_bytes(glb_bytes(doc))
    return glb.check(p, budget)


def test_good_model_passes(tmp_path):
    r = _check(tmp_path, good_glb())
    assert r.problems == []
    assert r.ao and r.triangles == 12 + 2  # shell + two 1-triangle meshes
    assert r.prefixed("bay") == {"PS-A"}


def test_not_a_glb(tmp_path):
    p = tmp_path / "x.glb"
    p.write_bytes(b"nope")
    assert "not a binary glTF" in glb.check(p).problems[0]


def test_lfs_pointer_is_recognised(tmp_path):
    p = tmp_path / "x.glb"
    p.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")
    r = glb.check(p)
    assert r.lfs_pointer and r.problems == []


def test_check_glb_rules(tmp_path):
    doc = good_glb()
    doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]
    doc["cameras"] = [{"type": "perspective"}]
    doc["materials"][0] = {"name": "PAINT", "pbrMetallicRoughness": {"metallicFactor": 0.5}}
    doc["meshes"][-1]["primitives"][0]["attributes"].pop("TEXCOORD_1")
    probs = " | ".join(_check(tmp_path, doc).problems)
    assert "extensions not allowed" in probs
    assert "has cameras" in probs
    assert "no baked AO" in probs
    assert "TEXCOORD_1" in probs
    assert "metallicFactor 0.50" in probs


def test_ao_must_use_texcoord_1(tmp_path):
    doc = good_glb()
    doc["materials"][0]["occlusionTexture"]["texCoord"] = 0
    assert any("texCoord 1" in p for p in _check(tmp_path, doc).problems)


def test_led_needs_the_led_material(tmp_path):
    doc = good_glb()
    led = next(n for n in doc["nodes"] if n["name"] == "led:SYS")
    doc["meshes"][led["mesh"]]["primitives"][0]["material"] = 0
    assert any("not LED" in p for p in _check(tmp_path, doc).problems)


def test_far_draw_calls(tmp_path):
    doc = good_glb()
    for _ in range(12):
        doc["meshes"].append(copy.deepcopy(doc["meshes"][-1]))
        doc["nodes"].append({"name": "part", "mesh": len(doc["meshes"]) - 1})
    r = _check(tmp_path, doc, "x.far.glb")
    assert any("draw calls" in p for p in r.problems)


def test_budgets(tmp_path):
    r = _check(tmp_path, good_glb(triangles=2000), budget="transceiver")
    assert any("over the transceiver budget" in w for w in r.warnings)
    assert r.problems == []
    r = _check(tmp_path, good_glb(triangles=10), budget="transceiver")
    assert r.warnings == []

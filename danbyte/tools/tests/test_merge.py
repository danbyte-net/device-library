import pytest

from danbyte_lib import merge as m

UP = {
    "manufacturer": "Acme",
    "model": "X1",
    "u_height": 1,
    "interfaces": [
        {"name": "eth1", "type": "1000base-t"},
        {"name": "eth2", "type": "1000base-t"},
        {"name": "stack1", "type": "cisco-stackwise"},
    ],
    "module-bays": [{"name": "PS-A", "position": "A"}, {"name": "FAN 1", "position": "1"}],
}


def test_overlay_keys_never_reach_the_result():
    out = m.merge(UP, {"upstream": "device-types/Acme/X1.yaml", "format": "danbyte-library/v1"})
    assert out == UP
    assert out is not UP


def test_top_level_keys_replace_whole():
    out = m.merge(UP, {"u_height": 2, "model3d": {"file": "assets/Acme/X1.glb"}})
    assert out["u_height"] == 2
    assert out["model3d"] == {"file": "assets/Acme/X1.glb"}
    assert list(out)[-1] == "model3d"


def test_patch_by_name_changes_only_given_fields():
    out = m.merge(UP, {"interfaces": {"stack1": {"type": "cisco-stackwise-480", "why": "typed"}},
                       "module-bays": {"PS-A": {"modules": ["PSU-1"]}}})
    assert out["interfaces"][2] == {"name": "stack1", "type": "cisco-stackwise-480"}
    assert out["module-bays"][0] == {"name": "PS-A", "position": "A", "modules": ["PSU-1"]}
    assert UP["interfaces"][2]["type"] == "cisco-stackwise"  # upstream untouched


def test_add_appends_or_places_and_drops_steering_fields():
    out = m.merge(UP, {"module-bays": {"add": [
        {"name": "FAN 2", "position": "2", "why": "missing upstream"},
        {"name": "NM", "position": "1", "after": "PS-A", "why": "x"},
        {"name": "PS-0", "position": "0", "before": "PS-A", "why": "x"},
    ]}})
    assert [b["name"] for b in out["module-bays"]] == ["PS-0", "PS-A", "NM", "FAN 1", "FAN 2"]
    assert all(set(b) <= {"name", "position"} for b in out["module-bays"])


def test_add_after_an_earlier_addition():
    out = m.merge(UP, {"interfaces": {"add": [
        {"name": "a", "type": "1000base-t", "after": "eth1", "why": "x"},
        {"name": "b", "type": "1000base-t", "after": "a", "why": "x"},
    ]}})
    assert [i["name"] for i in out["interfaces"]] == ["eth1", "a", "b", "eth2", "stack1"]


def test_section_missing_upstream_is_created():
    out = m.merge(UP, {"power-ports": {"add": [{"name": "SP-A", "type": "cisco-stackpower", "why": "x"}]}})
    assert out["power-ports"] == [{"name": "SP-A", "type": "cisco-stackpower"}]


def test_remove_then_patch_then_add():
    out = m.merge(UP, {"interfaces": {
        "remove": [{"name": "stack1", "why": "moved to a module"}],
        "eth2": {"mgmt_only": True},
        "add": [{"name": "stack1", "type": "1000base-t", "why": "re-added"}],
    }})
    assert out["interfaces"] == [
        {"name": "eth1", "type": "1000base-t"},
        {"name": "eth2", "type": "1000base-t", "mgmt_only": True},
        {"name": "stack1", "type": "1000base-t"},
    ]


@pytest.mark.parametrize("overlay, fragment", [
    ({"interfaces": {"eth9": {"type": "x"}}}, "'eth9' is not upstream"),
    ({"interfaces": {"remove": [{"name": "eth9", "why": "x"}]}}, "remove: 'eth9'"),
    ({"interfaces": {"add": [{"name": "eth1", "type": "x", "why": "x"}]}}, "already exists"),
    ({"interfaces": {"add": [{"name": "n", "type": "x", "after": "nope", "why": "x"}]}}, "'nope'"),
])
def test_name_problems_raise(overlay, fragment):
    with pytest.raises(m.MergeError) as exc:
        m.merge(UP, overlay)
    assert fragment in str(exc.value)
    assert m.check(UP, overlay)


def test_non_strict_merge_skips_problems():
    out = m.merge(UP, {"interfaces": {"eth9": {"type": "x"}, "eth1": {"enabled": False}}}, strict=False)
    assert out["interfaces"][0]["enabled"] is False


def test_strip_danbyte_leaves_netbox_only():
    doc = m.merge(UP, {"model3d": {"file": "a.glb"}, "meta": {"category": "switch"},
                       "rack_mount": {"ears": True},
                       "module-bays": {"PS-A": {"modules": ["P"]}},
                       "interfaces": {"eth1": {"stack": True}}})
    assert m.strip_danbyte(doc) == UP


def test_load_yaml_keeps_dates_as_strings():
    assert m.load_yaml("d: 2027-10-31\nm: 2017-06\n") == {"d": "2027-10-31", "m": "2017-06"}


def test_rewrite_paths():
    doc = {"model3d": {"file": "assets/A/x.glb", "far": "assets/A/x.far.glb",
                       "renders": {"iso": "assets/A/x.iso.webp", "model_sha256": "assets/no"}}}
    out = m.rewrite_paths(doc, "assets/", "models/")
    assert out["model3d"]["file"] == "models/A/x.glb"
    assert out["model3d"]["renders"]["iso"] == "models/A/x.iso.webp"
    assert out["model3d"]["renders"]["model_sha256"] == "assets/no"


def test_additions_lists_every_change_with_why():
    rows = m.additions({"interfaces": {"eth1": {"type": "x", "why": "w"},
                                       "add": [{"name": "n", "why": "a"}],
                                       "remove": [{"name": "eth2", "why": "r"}]}})
    assert {(r["action"], r["name"]) for r in rows} == {("change", "eth1"), ("add", "n"), ("remove", "eth2")}


def test_drop_removes_an_upstream_key():
    up = {"model": "X", "u_height": 0, "subdevice_role": "child"}
    ov = {"upstream": "device-types/A/X.yaml", "u_height": 1,
          "drop": [{"key": "subdevice_role", "why": "modelled alone"}]}
    out = m.merge(up, ov)
    assert out == {"model": "X", "u_height": 1}
    assert {"section": "drop", "action": "remove", "name": "subdevice_role",
            "why": "modelled alone"} in m.additions(ov)


def test_drop_of_a_missing_key_or_a_component_list_is_a_problem():
    up = {"model": "X", "interfaces": []}
    for key in ("nope", "interfaces"):
        with pytest.raises(m.MergeError):
            m.merge(up, {"upstream": "device-types/A/X.yaml", "drop": [{"key": key, "why": "x"}]})

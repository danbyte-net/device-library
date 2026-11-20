import datetime as dt
import json

import pytest

from danbyte_lib import catalog, cli, ports
from danbyte_lib.repo import Repo

from helpers import REPO


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("catalog")
    res = catalog.build(Repo(REPO), out, today=dt.date(2026, 11, 18), copy_assets=False)
    return out, res


def test_catalog_matches_its_schema(built):
    out, res = built
    assert res["problems"] == []
    assert catalog.check(Repo(REPO), out) == []


def test_rows_carry_what_the_site_needs(built):
    out, _ = built
    cat = json.loads((out / "catalog.json").read_text())
    rows = {r["id"]: r for r in cat["entries"]}
    assert cat["count"] == len(rows) > 80
    r = rows["device/Cisco/C9300-24P"]
    assert r["slug"] == "cisco-c9300-24p" and r["vendor_slug"] == "cisco"
    assert r["category"] == "switch" and r["family"] == "Catalyst 9300"
    assert "24 × 1G RJ45 PoE+" in r["ports"]["summary"]
    assert "2 × StackWise-480 stack" in r["ports"]["summary"]
    assert "2 × StackPower" in r["ports"]["summary"]
    assert r["ports"]["stack_ports"] == 2
    assert r["has_3d"] and r["has_far"] and r["has_photos"]
    assert r["thumbnail"] == "assets/Cisco/C9300-24P.iso.webp"
    assert [p["id"] for p in r["packs"]] == ["cisco-catalyst-9300"]
    # The fan the 9200 and the 9300 share is in both packs.
    assert sorted(p["id"] for p in rows["module/Cisco/FAN-T2"]["packs"]) == [
        "cisco-catalyst-9200", "cisco-catalyst-9300"]
    assert r["lifecycle"]["state"] == "unknown"
    assert {"name": "PS-A", "position": "A",
            "modules": ["PWR-C1-350WAC-P", "PWR-C1-715WAC-P", "PWR-C1-1100WAC-P"]} in r["module_bays"]
    assert r["upstream"] == "device-types/Cisco/C9300-24P.yaml"
    fan = rows["module/Cisco/FAN-T4-R"]
    assert fan["upstream"] is None and fan["module_kind"] == "fan"
    sfp = rows["pluggable/Cisco/SFP-10G-SR"]
    assert sfp["pluggable"]["fits"] == ["10gbase-x-sfpp"] and sfp["pluggable_kind"] == "transceiver"
    item = json.loads((out / r["item"]).read_text())
    assert item["record"]["model"] == "Catalyst 9300-24P"
    assert any(a["action"] == "add" and a["name"] == "StackPower1/A" for a in item["additions"])


def test_vendors(built):
    out, _ = built
    vendors = {v["slug"]: v for v in json.loads((out / "vendors.json").read_text())["vendors"]}
    # As many switches as the repo has Cisco device overlays: the count
    # follows the library, not a number that breaks with every new type.
    overlays = len(list((REPO / "danbyte/overlays/Cisco").glob("*.yaml")))
    assert vendors["cisco"]["categories"]["switch"] == overlays
    assert "Catalyst 9300" in vendors["cisco"]["families"]
    assert vendors["cisco"]["with_3d"] == vendors["cisco"]["count"]


@pytest.mark.parametrize("life, state", [
    ({}, "unknown"),
    ({"release_date": "2017-06"}, "current"),
    ({"end_of_sale": "2026-11-18"}, "end-of-sale"),
    ({"end_of_sale": "2026-12"}, "current"),
    ({"end_of_sale": "2020-01-01", "end_of_support": "2026-11"}, "end-of-support"),
])
def test_lifecycle_state(life, state):
    assert catalog.lifecycle_state(life, dt.date(2026, 11, 18)) == state


def test_port_summary():
    s = ports.summary({"interfaces": [
        {"name": "a", "type": "10gbase-x-sfpp"}, {"name": "b", "type": "10gbase-x-sfpp"},
        {"name": "c", "type": "25gbase-x-sfp28", "stack": True},
        {"name": "d", "type": "1000base-t", "poe_mode": "pse", "poe_type": "type3-ieee802.3bt"},
        {"name": "v", "type": "virtual"}],
        "power-ports": [{"name": "PSU1", "type": "iec-60320-c14"}]})
    assert s["summary"] == ["2 × 10G SFP+", "1 × 25G SFP28 stack", "1 × 1G RJ45 PoE++", "1 × C14 inlet"]
    assert s["interfaces"] == 4 and s["stack_ports"] == 1


def test_cli_build_catalog(tmp_path, capsys):
    assert cli.main(["--root", str(REPO), "build-catalog", "--out", str(tmp_path), "--no-assets",
                     "--today", "2026-11-18"]) == 0
    assert "match catalog.schema.json" in capsys.readouterr().out

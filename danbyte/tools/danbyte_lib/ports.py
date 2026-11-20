"""A port summary of a merged record, for the catalog (``24 × 1G RJ45 PoE+``)."""
from __future__ import annotations

import re
from collections import OrderedDict

_SPEED = re.compile(r"^(\d+(?:\.\d+)?)(g|m)?base-")
#: Interface type suffix -> connector / cage name.
_FORMS = [
    ("-x-qsfpdd", "QSFP-DD"), ("-x-qsfp112", "QSFP112"), ("-x-qsfp56", "QSFP56"),
    ("-x-qsfp28", "QSFP28"), ("-x-qsfpp", "QSFP+"), ("-x-qsfp", "QSFP"),
    ("-x-osfp", "OSFP"), ("-x-sfp112", "SFP112"), ("-x-sfp56", "SFP56"),
    ("-x-sfp28", "SFP28"), ("-x-sfpp", "SFP+"), ("-x-sfp", "SFP"), ("-x-xfp", "XFP"),
    ("-x-cfp", "CFP"), ("-x-gbic", "GBIC"), ("-t", "RJ45"), ("-tx", "RJ45"),
]
_POE = {
    "type1-ieee802.3af": "PoE", "type2-ieee802.3at": "PoE+", "type3-ieee802.3bt": "PoE++",
    "type4-ieee802.3bt": "PoE++", "passive-24v-2pair": "passive PoE", "passive-24v-4pair": "passive PoE",
    "passive-48v-2pair": "passive PoE", "passive-48v-4pair": "passive PoE",
}
_STACK = {
    "cisco-stackwise": "StackWise", "cisco-stackwise-plus": "StackWise Plus",
    "cisco-stackwise-80": "StackWise-80", "cisco-stackwise-160": "StackWise-160",
    "cisco-stackwise-320": "StackWise-320", "cisco-stackwise-480": "StackWise-480",
    "cisco-stackwise-1t": "StackWise-1T", "juniper-vcp": "VCP",
    "extreme-summitstack": "SummitStack", "extreme-summitstack-128": "SummitStack-128",
    "extreme-summitstack-256": "SummitStack-256", "extreme-summitstack-512": "SummitStack-512",
}
_NAMED = {
    "rj-45": "RJ45", "usb-mini-b": "USB mini-B", "usb-micro-b": "USB micro-B", "usb-c": "USB-C",
    "usb-a": "USB-A", "de-9": "DE-9", "cisco-stackpower": "StackPower",
}
_IEC = re.compile(r"^iec-60320-(c\d+)$")
_EXCLUDED = ("virtual", "lag", "bridge")


def _speed_label(mbps: int) -> str:
    if mbps >= 1000:
        g = mbps / 1000
        return f"{g:g}G"
    return f"{mbps}M"


def interface_facts(t: str) -> tuple[int | None, str | None]:
    """``(speed_mbps, connector)`` of an interface type, as far as it says."""
    m = _SPEED.match(t)
    if not m:
        return None, None
    n = float(m.group(1))
    mbps = int(n * 1000) if m.group(2) == "g" else int(n)
    rest = t[m.end() - 1:]
    for suffix, name in _FORMS:
        if rest.endswith(suffix) or rest == suffix:
            return mbps, name
    return mbps, None


def _pretty(t: str) -> str:
    if t in _NAMED:
        return _NAMED[t]
    iec = _IEC.match(t)
    if iec:
        return iec.group(1).upper()
    return t


def summary(doc: dict) -> dict:
    groups: "OrderedDict[tuple, dict]" = OrderedDict()

    def add(cls, typ, label, **facts):
        key = (cls, typ, label)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"class": cls, "type": typ, "count": 0, "label": label, **facts}
        g["count"] += 1

    interfaces = stack_ports = 0
    for i in doc.get("interfaces") or []:
        t = str(i.get("type") or "")
        if t in _EXCLUDED:
            continue
        interfaces += 1
        if t in _STACK or (t.startswith("cisco-stackwise")) or i.get("stack") is True:
            stack_ports += 1
            name = _STACK.get(t)
            if name is None:
                mbps, conn = interface_facts(t)
                name = " ".join(x for x in (_speed_label(mbps) if mbps else t, conn) if x)
            add("interface", t, f"{name} stack", stack=True, speed_mbps=None, connector=None)
            continue
        mbps, conn = interface_facts(t)
        poe = _POE.get(str(i.get("poe_type") or "")) if i.get("poe_mode") == "pse" else None
        mgmt = bool(i.get("mgmt_only"))
        parts = [_speed_label(mbps) if mbps else t, conn, poe, "mgmt" if mgmt else None]
        add("interface", t, " ".join(p for p in parts if p), speed_mbps=mbps, connector=conn,
            poe=poe, mgmt=mgmt)
    for section, cls, suffix in (("console-ports", "console", "console"),
                                 ("console-server-ports", "console", "console server port"),
                                 ("power-ports", "power-port", "inlet"),
                                 ("power-outlets", "power-outlet", "outlet"),
                                 ("front-ports", "front-port", "front port"),
                                 ("rear-ports", "rear-port", "rear port")):
        for p in doc.get(section) or []:
            t = str(p.get("type") or "")
            label = _pretty(t)
            if t == "cisco-stackpower":
                label, suff = "StackPower", ""
            else:
                suff = suffix
            add(cls, t, f"{label} {suff}".strip())
    bays: "OrderedDict[str, int]" = OrderedDict()
    for b in doc.get("module-bays") or []:
        base = re.sub(r"[\s-]*(\d+(/\d+)*|[A-Z])$", "", str(b.get("name") or "")).strip() or str(b.get("name"))
        add("module-bay", base, f"{base} bay")
        bays[base] = bays.get(base, 0) + 1
    for b in doc.get("device-bays") or []:
        add("device-bay", "device-bay", "device bay")
    rows = list(groups.values())
    return {
        "summary": [f"{g['count']} × {g['label']}" for g in rows],
        "groups": rows,
        "interfaces": interfaces,
        "stack_ports": stack_ports,
    }

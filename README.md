# Danbyte device library

Hardware definitions for [Danbyte](https://github.com/danbyte-net/danbyte) — a
fork of **[netbox-community/devicetype-library](https://github.com/netbox-community/devicetype-library)**
with a Danbyte layer on top.

Upstream is an extraordinary community effort: thousands of device types,
maintained by people who own the hardware. All of that is here, untouched, and
keeps flowing in. Their original README is preserved as
[`UPSTREAM-README.md`](UPSTREAM-README.md). Upstream is licensed **CC0-1.0**,
which places no conditions on reuse — the credit above is given because it's
deserved, not because it's required.

## What this fork adds

Upstream describes a device's **ports**. Danbyte can also describe how the
device *looks and behaves*, and that knowledge is just as reusable:

| Layer | What it is |
|---|---|
| **Faceplate** | A millimetre-true port layout, so the device draws as hardware |
| **Photo ports** | Port markers placed on the real front/rear photo — normalized coordinates, so they scale to any render size and into the 3D room view |
| **Inventory templates** | The parts a chassis ships with: disk bays, PSUs, fan trays |
| **SNMP sensors** | The vendor OID that reports drive/PSU health, with its value → status map |

That last one is the point. SNMP has no standard disk-health MIB, so every
vendor invented their own OID. Working out that a Lenovo x3650 reports drive
health at `1.3.6.1.4.1.2.3.51.3.1.12.2.1.3` is real work — and it's the same
answer for everyone who owns that chassis.

**No credentials are ever stored here.** A sensor definition carries an OID and
a value map; it polls with the *importing* deployment's own SNMP profile. There
is nothing secret to leak.

## Layout

```
device-types/        ← UPSTREAM. Never edited here.
elevation-images/    ← UPSTREAM. Never edited here.
danbyte/
  types/             ← complete bundles (what Danbyte's "Export bundle" produces)
  overlays/          ← additions to an existing upstream type, keyed by its slug
  images/            ← our photos, where upstream has none
  schema/            ← JSON Schema for the bundle format
  bin/               ← maintenance scripts
README.md            ← the one upstream file this fork owns, by design
```

Keeping upstream paths **byte-identical** is deliberate: it means
`git merge upstream/master` is always a clean fast-forward, forever. Annotating
upstream YAML in place would turn every future upstream fix into a hand-resolved
conflict across hundreds of files.

## Using a bundle

In Danbyte: **Device types → Import bundle**, choose the file, **Preview**, then
**Import**. The preview writes nothing and tells you exactly what would be
created.

Two things worth knowing:

- Imported sensors arrive **observe-only**. They surface differences as drift for
  you to accept; they can never overwrite a status you set. Switch one to
  automatic yourself if you want that.
- Photo-port markers need the photo they were placed on. Bundles record whether
  they had one, and the import tells you if it's missing — upload it on the
  device type and the markers line up.

## Contributing a bundle

1. Set the device type up completely in Danbyte — templates, faceplate, photo
   ports, inventory templates, sensors.
2. **Export bundle** on the device type.
3. Import it into a scratch tenant to confirm it's complete.
4. Add it under `danbyte/types/<vendor>/<model>.danbyte.json` and open a PR.

CI validates every bundle against `danbyte/schema/danbyte-device-v1.json`.

## Licence

Everything in this repository, upstream and ours, is
**[CC0-1.0](LICENSE.txt)** — public domain. Use it however you like.

Manufacturer and model names identify the hardware a file describes. This
project is not affiliated with, endorsed by, or sponsored by any of them.

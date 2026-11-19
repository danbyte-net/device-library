# The library website: data contract

The library website (`danbyte-net/library-site`, separate repo) never reads this repo's YAML. It
reads the **catalog**: a folder of JSON and assets that `danbyte-lib build-catalog` generates from
the repo. This page is the contract between the two.

## Build it locally

```sh
pip install -e danbyte/tools           # PyYAML + jsonschema
danbyte-lib build-catalog --out site-data
```

`site-data/` then holds:

| Path | What |
|---|---|
| `catalog.json` | `{format, generated, count, entries[]}`, one row per entry: everything a list, filter or search needs. |
| `vendors.json` | `{format, generated, vendors[]}`, per vendor: count, count per category, families, how many have 3D. |
| `items/<vendor-slug>/<slug>.json` | One per entry: the row plus the full merged record (`record`) and the library's additions (`additions`). For the detail page. |
| `items/<vendor-slug>/<slug>.yaml` | The same entry's **definition file** (the row's `definition`): the merged record as YAML, the file Danbyte's device type import reads. Offer it as the "definition" download. |
| `packs.json` | `{format, generated, packs[]}`: every pack with its zip, size and checksum. |
| `packs/<id>-<version>.zip` | **The file a user downloads to install a pack in Danbyte.** |
| `assets/<Vendor>/…` | The models (`.glb`, `.far.glb`) and photos (`.front` / `.rear` / `.iso.webp`) the rows name. |

Every path in the JSON is **relative to the catalog root** (`assets/Cisco/C9300-48P.glb`), or an
absolute `https://` URL for files served from the CDN (HQ models).

The shape is pinned by [`schema/catalog.schema.json`](../schema/catalog.schema.json) and
[reference/catalog.md](reference/catalog.md); `build-catalog` checks its output against it.
`format` is `danbyte-library/catalog/v1`. A breaking change bumps it, so **pin the format your site
reads** and refuse a newer major.

## An entry

| Field | Notes |
|---|---|
| `id` | `<kind>/<Vendor>/<name>`, e.g. `device/Cisco/C9300-48P`. Stable; use it in URLs with `slug`. |
| `kind` | `device`, `module`, `pluggable`, `rack-type`, `rack-kit`. |
| `category` | `switch`, `router`, `firewall`, `server`, `storage`, `access-point`, `wireless-controller`, `load-balancer`, `optical`, `console-server`, `pdu`, `ups`, `patch-panel`, `kvm`, `appliance`, `other`; modules `module` (+ `module_kind`: `psu`, `fan`, `network-module`, `supervisor`, `line-card`, `stack-kit`, `other`); pluggables `pluggable` (+ `pluggable_kind`: `transceiver`, `plug`, `cord`); `rack`, `rack-kit`. |
| `vendor`, `vendor_slug`, `model`, `part_number`, `family`, `roles`, `summary` | Text for cards and search. `summary` is one plain sentence, or null. |
| `u_height`, `airflow`, `weight`, `max_power_w` | Specs, null where unknown. |
| `ports` | `summary` (ready-made lines like `48 × 1G RJ45 PoE+`) and `groups` (class, type, count, speed_mbps, connector, poe, mgmt) for filters; `interfaces`, `stack_ports` counts. |
| `module_bays` | Bay name, position and the module part numbers that fit; link them to their own entries. |
| `pluggable` | For pluggables: `connector`, `fits` (port types it plugs into), `parts` (orderable part numbers), `standard`, `wavelength_nm`, `cable_profile`. |
| `lifecycle` | `state` (`current`, `end-of-sale`, `end-of-support`, `unknown`) and the dates with their `source` when known. Only dates from a vendor notice; never guessed. |
| `has_3d`, `has_far`, `has_photos`, `thumbnail` | `thumbnail` is the iso photo, for grids. |
| `models` | `{far, standard, hq}`. `standard` is the model for a viewer; `far` a small copy for many at once; `hq` a close-up copy or null (fall back to `standard`). `model_url` / `far_url` are the same as `standard` / `far` (kept for old readers). |
| `photos` | `{front, rear, iso}` webp renders. |
| `links` | `{kind, title, url}`: `datasheet`, `install-guide`, `config-guide`, `product-page`, `eol-notice`, `eol-listing`, `support`, `software`, `other`. |
| `packs` | The pack(s) it ships in: `id`, `name`, `version`, `license`, `author`. **Show the credit**: models are under their pack's licence (e.g. CC-BY-4.0, which requires attribution). |
| `upstream`, `source`, `updated`, `item` | The upstream file it extends (if any), its library file, the date it last changed, and its item JSON. |

## Installing in Danbyte: what to tell a visitor

- **A whole pack** (the normal way): download `packs/<id>-<version>.zip` and, in Danbyte 0.21 or
  later, open **Settings → Device library → 3D model packs → Install pack…**, then upload the zip
  or paste its URL. Danbyte checks it, shows what it would create or update, and installs on
  confirm. Types arrive with their ports, bays, 3D models, photos, lifecycle dates and links.
  Danbyte 0.20 and earlier have no pack install.
- **One part:** its definition file (`definition`) imports as a device type through the device type
  import; its `.glb` can then be uploaded on that type as its 3D model. A module, pluggable or rack
  kit comes most easily with its pack.
- Each pack row in `packs.json` has `size` and `sha256`. The zip is built deterministically, so the
  checksum only changes when the pack does. Show the pack's `license` and `author` beside the
  download.
- The catalog JSON (`items/….json`) is website data. Danbyte does not import it.

## The models

glTF 2.0 binaries. Viewers must support **`EXT_meshopt_compression`** (three.js: `GLTFLoader.setMeshoptDecoder(MeshoptDecoder)`; `<model-viewer>` handles it), **`KHR_mesh_quantization`**, **`EXT_texture_webp`** and `KHR_texture_transform`.

- Units are metres, +Y up, the front faces **+Z**.
- Standard chassis models are up to about 2-3 MB, far copies under 1 MB, parts about 0.1 MB. Load
  the photo first and the model on demand.
- Node names follow the export spec: `port:<name>` empties at each port (for an optional port
  overlay), `led:<name>` meshes (Danbyte lights them from live status; a website may leave them as
  they are), `bay:<name>` empties where modules go. A plain viewer shows everything correctly without
  reading any of them.
- A device is drawn with its modules **as separate files**: the chassis has empty bays. To show a
  fully built switch, place each module's glb at its `bay:` empty (`module_bays[].modules` lists what
  fits). Showing the chassis alone is fine for a first version.

## Licences

- Overlays, pluggable and rack data, and the schemas: **CC0**.
- Models and photos: their pack's licence (`packs[].license`), with credit to `packs[].author`.
- Upstream device-type data: the upstream library's licence.
- No vendor logos anywhere; vendor names are plain text.

## Hosting

CI builds the catalog on every merge to `main` and publishes it to GitHub Pages; it is mirrored on
danbyte.net. HQ models and `.blend` sources are release downloads per pack version, not in git.

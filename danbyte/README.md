# The Danbyte layer

Everything in `danbyte/` is Danbyte's addition to the upstream device-type library. Upstream's own
files (`device-types/`, `module-types/`, `elevation-images/`, …) are never edited here, so an
upstream sync always merges cleanly.

## What is here

| Folder | What |
|---|---|
| `overlays/<Vendor>/<PART>.yaml` | Additions to an upstream **device type**: its 3D model, rack mounting, extra ports and bays, and `meta:` (category, lifecycle, links). Patches by name; every addition says `why:`. |
| `module-overlays/` | The same for upstream **module types**. |
| `types-new/` | Complete types upstream doesn't have yet. |
| `pluggables/` | Transceivers, plugs and cords: what they fit and how they look. |
| `racks/kits/`, `racks/types/` | Rack kits (the parts a rack is built from) and rack types. |
| `packs/<pack>/pack.yaml` | A pack: a named, versioned set of the entries above that Danbyte installs together. It may `requires:` other packs, and an entry may be in more than one pack. |
| `split-rules/<source>.yaml` | For a pack made in the 3D workflow's working folder: which library packs its entries go into, and the reasons and meta its YAML doesn't carry. |
| `assets/<Vendor>/` | The 3D models (`.glb`, `.far.glb`) and rendered photos the entries name. Plain git, no LFS. |
| `schema/` | JSON Schema for every file kind, and for the website catalog. |
| `docs/` | `reference/` is generated from the schemas; `website.md` is the catalog contract. |
| `tools/` | `danbyte-lib`, the command-line tool. |
| `types/`, `bin/`, `images/` | The older bundle format (still validated by its own workflow). |

## The tool

```sh
pip install -e danbyte/tools
danbyte-lib validate                 # schema, upstream names, merged record, model checks
danbyte-lib preview Cisco/C9300-48P  # the merged record Danbyte imports
danbyte-lib sync-check               # overlays whose patched names upstream no longer has
danbyte-lib build-catalog --out site-data   # the website's JSON, assets and pack zips
danbyte-lib build-pack --out _packs  # only the zips Danbyte installs, one per pack
danbyte-lib docs                     # regenerate docs/reference from the schemas
danbyte-lib new Cisco/C9300-48P             # start an overlay with its schema header
```

A pack made in place (a folder of full YAMLs and models, as the 3D workflow exports it) is turned
into overlays with:

```sh
danbyte-lib split-pack <folder> --rules danbyte/split-rules/<source>.yaml --force
```

## Licences

- Overlays, pluggable and rack data, schemas and tools: **CC0-1.0**.
- Models and photos: the licence of the pack they ship in (`pack.yaml` `license:`), with credit to
  its author.
- Upstream data: upstream's licence.

No vendor logos anywhere. See [CONTRIBUTING.md](CONTRIBUTING.md) to add or fix something.

# Contributing to the Danbyte layer

For plain device-type data (ports, bays, weights) that upstream lacks or has wrong, contribute to
**upstream** first: it reaches everyone, and it arrives here with the next sync. This guide is for
what only the Danbyte layer holds: 3D models, rack mounting, pluggables, racks, lifecycle and links.

## The short version

1. Fork, and branch from `master`.
2. Add or edit files under `danbyte/` only. Never edit `device-types/`, `module-types/` or any other
   upstream path.
3. Run `danbyte-lib validate` and `danbyte-lib sync-check` (see [README.md](README.md)).
4. Open a pull request. CI runs the same checks and builds the catalog.

## Rules

- **An overlay patches by name.** It names its upstream file (`upstream:`) and lists only what it
  adds or changes. Upstream stays the base.
- **Every addition says why.** A port, bay or changed type needs a `why:` with the reason and the
  source, e.g. `why: 'StackPower ports A and B on the rear (Cisco C9300 hardware installation
  guide)'`.
- **Facts come from a source.** Lifecycle dates need the vendor notice they come from
  (`lifecycle.source`). Links are pages you opened. Leave a key out rather than guess.
- **Bay lists follow the vendor's documents:** which modules, power supplies and fans a bay takes
  comes from the data sheet or the hardware installation guide, not from what physically fits.
- **Exact part numbers,** in the vendor's capitals, for file names and `part_number`.
- **No vendor logos, wordmarks or trade dress** in models, textures or photos. Vendor names are
  plain text.
- **Models follow the export spec:** real size in metres, the right origin, named anchors
  (`port:`, `bay:`, `led:`), a far copy, and the triangle and size budgets. `danbyte-lib validate`
  checks most of it.
- **Assets are plain git.** Keep a chassis model under about 3 MB and a part under 0.3 MB.
  Higher-detail copies and source files are release downloads, not commits.

## Licences

By contributing you agree that:
- data, schemas and tools you add under `danbyte/` are released under **CC0-1.0**;
- models and photos are released under the licence of the pack they join (stated in its
  `pack.yaml`), and you have the right to release them so. Say who made them (`author:`).

## Reviews

A pull request is checked for sources as much as for format: expect to be asked where a date or a
bay list comes from.

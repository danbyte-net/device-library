# danbyte-lib

The command line for the Danbyte device library: `new`, `validate`, `preview`,
`split-pack`, `build-catalog`, `sync-check` and `docs`. It works on a checkout
of the library repository (it finds the root from the working directory, or
take `--root`).

```bash
pip install ./danbyte/tools            # PyYAML + jsonschema
pip install './danbyte/tools[images]'  # + Pillow, for the blank-texture checks
danbyte-lib validate
```

`danbyte_lib/merge.py` holds the merge rule (upstream + overlay) and depends on
PyYAML only, so Danbyte's importer can use it as is. See `danbyte/README.md`.

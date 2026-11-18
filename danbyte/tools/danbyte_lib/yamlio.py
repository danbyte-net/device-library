"""Writing library YAML the way the repository keeps it."""
from __future__ import annotations

from pathlib import Path

import yaml

SCHEMA_LINE = "# yaml-language-server: $schema={rel}"


FLOW_MAX = 100


class _FlowMap(dict):
    pass


class _FlowList(list):
    pass


class _Dumper(yaml.SafeDumper):
    """Two-space indent, sequences indented under their key (yamllint's
    ``indent-sequences: true``)."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


_Dumper.add_representer(_FlowMap, lambda d, v: d.represent_mapping(
    "tag:yaml.org,2002:map", v.items(), flow_style=True))
_Dumper.add_representer(_FlowList, lambda d, v: d.represent_sequence(
    "tag:yaml.org,2002:seq", v, flow_style=True))


def _scalar(v) -> bool:
    return not isinstance(v, (dict, list))


def _flowed(v, depth: int, flow: bool):
    """Short collections of scalars below the top level go in flow style."""
    if isinstance(v, dict):
        inner = {k: _flowed(x, depth + 1, flow) for k, x in v.items()}
        if flow and depth >= 1 and v and all(_scalar(x) for x in v.values()) \
                and len(yaml.dump(dict(v), Dumper=_Dumper, default_flow_style=True, width=10_000)) <= FLOW_MAX:
            return _FlowMap(inner)
        return inner
    if isinstance(v, list):
        inner = [_flowed(x, depth + 1, flow) for x in v]
        if flow and depth >= 1 and all(_scalar(x) for x in v) \
                and len(yaml.dump(list(v), Dumper=_Dumper, default_flow_style=True, width=10_000)) <= FLOW_MAX:
            return _FlowList(inner)
        return inner
    return v


def dump(doc, key_notes: dict | None = None, *, flow: bool = True, spacing: bool = True) -> str:
    """YAML text; ``key_notes`` puts a ``# note`` line above those top-level
    keys. ``flow=False`` writes block style throughout (NetBox's own style)."""
    text = yaml.dump(_flowed(doc, 0, flow), Dumper=_Dumper, sort_keys=False,
                     default_flow_style=False, allow_unicode=True, width=10_000, indent=2)
    # A blank line before each top-level key that follows a block.
    lines, out = text.splitlines(), []
    for i, line in enumerate(lines):
        top = bool(line) and not line[0].isspace() and not line.startswith(("-", "#"))
        key = line.split(":", 1)[0] if top else None
        note = (key_notes or {}).get(key)
        if i and top and ((spacing and lines[i - 1][:1].isspace()) or note):
            out.append("")
        if note:
            out.append(f"# {note}")
        out.append(line)
    return "\n".join(out) + "\n"


def schema_ref(path: Path, danbyte_dir: Path, schema: str) -> str:
    """The relative path from ``path``'s folder to ``danbyte/schema/<schema>``."""
    depth = len(path.parent.relative_to(danbyte_dir).parts)
    return "/".join([".."] * depth + ["schema", schema])


def write(path: Path, doc, *, danbyte_dir: Path, schema: str, comments: list[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = [SCHEMA_LINE.format(rel=schema_ref(path, danbyte_dir, schema))]
    head += [f"# {c}" for c in comments]
    path.write_text("\n".join(head) + "\n" + dump(doc), encoding="utf-8")

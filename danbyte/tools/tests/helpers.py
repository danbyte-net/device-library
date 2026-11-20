"""Shared helpers for the danbyte-lib tests (imported, not a conftest:
the repository's pytest config runs with --noconftest)."""
from __future__ import annotations

import atexit
import json
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TOOLS = TESTS.parent
REPO = TOOLS.parents[1]
FIXTURES = TESTS / "fixtures"


def _unpacked(name: str) -> Path:
    """A fixture pack, unpacked for this run. It is kept as a zip because the
    upstream definition tests validate every changed file whose path holds
    ``device-types``, ``module-types`` or ``rack-types`` against the upstream
    schema, and a pack's files are Danbyte's, not upstream's."""
    out = Path(tempfile.mkdtemp(prefix="danbyte-lib-fixture-")) / name
    atexit.register(shutil.rmtree, out.parent, ignore_errors=True)
    with zipfile.ZipFile(FIXTURES / f"{name}.zip") as z:
        z.extractall(out)
    return out


PACK_FIXTURE = _unpacked("cisco-catalyst-9500")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tmp_repo(tmp_path: Path) -> Path:
    """A minimal library repository: both schema folders, nothing else."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "danbyte" / "schema", root / "danbyte" / "schema")
    shutil.copytree(REPO / "schema", root / "schema")
    return root


def glb_bytes(doc: dict, bin_chunk: bytes = b"") -> bytes:
    """A binary glTF from a JSON document (and an optional BIN chunk)."""
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js
    if bin_chunk:
        bin_chunk += b"\0" * (-len(bin_chunk) % 4)
        chunks += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def good_glb(names=("bay:PS-A", "empty:PS-A", "port:Gi1/0/1", "led:SYS"), triangles: int = 12) -> dict:
    """A glTF document that passes every check: AO on texCoord 1, TEXCOORD_1
    everywhere, metallic 0, the LED material on led: meshes."""
    nodes, meshes = [], []
    for n in names:
        node = {"name": n}
        if n.startswith(("led:", "empty:")):
            meshes.append({"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_1": 1},
                                           "indices": 2, "material": 1 if n.startswith("led:") else 0}]})
            node["mesh"] = len(meshes) - 1
        nodes.append(node)
    meshes.append({"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_1": 1}, "indices": 3,
                                   "material": 0}]})
    nodes.append({"name": "Shell", "mesh": len(meshes) - 1})
    return {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": [{"count": 3}, {"count": 3}, {"count": 3}, {"count": triangles * 3}],
        "materials": [
            {"name": "BODY", "pbrMetallicRoughness": {"metallicFactor": 0.0},
             "occlusionTexture": {"index": 0, "texCoord": 1}},
            {"name": "LED", "pbrMetallicRoughness": {"metallicFactor": 0.5}},
        ],
    }

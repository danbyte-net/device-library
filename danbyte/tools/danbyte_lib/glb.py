"""glb checks: the Blender agent's hand-over checks (``check_glb.py`` v1.8,
``verify_glb.py``, ``led_check``, ``sha_check``), folded into ``validate``.

Rules kept from ``check_glb.py``:

- only the allowed glTF extensions;
- baked ambient occlusion: an ``occlusionTexture`` on ``texCoord`` 1, and
  every primitive carrying ``TEXCOORD_1`` (spec §6b);
- metallic factors are 0 (paint) or 1 (bare metal), never 0.2-0.8, except
  the ``LED`` material (§6a);
- no blank textures: base colour, roughness and normal maps must vary (sd ≥ 2),
  checked only when Pillow is installed (§6a);
- a far LOD has at most its LED, cover and empty meshes + 10 draw calls (§5).

Added from ``verify_glb.py`` and the spec's §10: no cameras, animations,
skins or external references; Draco, KTX2 and lights are refused; every
``led:`` mesh uses the ``LED`` material; triangle and file-size budgets.
Name checks against the YAML live in ``validate``.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: texture checks
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - depends on the environment
    Image = None
    ImageStat = None

ALLOWED_EXT = {"KHR_mesh_quantization", "EXT_meshopt_compression", "EXT_texture_webp",
               "KHR_materials_emissive_strength", "KHR_texture_transform"}
FLAT_SD = 2.0
FAR_EXTRA_CALLS = 10
MB = 1024 * 1024

#: (triangles, bytes) budgets by part class, full and far (spec §5, §13, §14).
BUDGETS = {
    "chassis": {"full": (150_000, 8 * MB), "far": (15_000, 1 * MB)},
    "module": {"full": (30_000, 8 * MB), "far": (3_000, 1 * MB)},
    "transceiver": {"full": (1_500, 200 * 1024)},
    "plug": {"full": (2_000, 200 * 1024)},
    "cord": {"full": (2_000, 200 * 1024)},
    "rack-part": {"full": (5_000, 8 * MB)},
    "rack-kit": {"full": (20_000, 8 * MB), "far": (2_000, 1 * MB)},
}
#: Danbyte refuses any glb over this (api.model_3d).
HARD_MAX = 25 * MB


class GlbError(ValueError):
    pass


@dataclass
class GlbReport:
    path: Path
    size: int = 0
    triangles: int = 0
    calls: int = 0
    materials: int = 0
    ao: bool = False
    names: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lfs_pointer: bool = False

    def prefixed(self, kind: str) -> set[str]:
        """Node names starting ``kind:`` with the prefix dropped."""
        p = kind + ":"
        return {n[len(p):] for n in self.names if n.startswith(p)}


def is_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(40).startswith(b"version https://git-lfs.github.com/spec/")
    except OSError:
        return False


def load(path: Path) -> tuple[dict, bytes, int]:
    b = Path(path).read_bytes()
    if len(b) < 20 or b[:4] != b"glTF":
        raise GlbError("not a binary glTF (.glb) file")
    version = struct.unpack("<I", b[4:8])[0]
    if version != 2:
        raise GlbError(f"glTF version {version}, not 2")
    n, ctype = struct.unpack("<II", b[12:20])
    if ctype != 0x4E4F534A:
        raise GlbError("first chunk is not JSON")
    try:
        j = json.loads(b[20:20 + n])
    except ValueError as exc:
        raise GlbError(f"JSON chunk does not parse: {exc}") from exc
    return j, b, 20 + n + 8


def _image(j, b, start, ref):
    tex = j["textures"][ref["index"]]
    src = tex.get("source", tex.get("extensions", {}).get("EXT_texture_webp", {}).get("source"))
    bv = j["bufferViews"][j["images"][src]["bufferView"]]
    off = start + bv.get("byteOffset", 0)
    return Image.open(io.BytesIO(b[off:off + bv["byteLength"]])).convert("RGB")


def check(path: Path, budget: str | None = None, *, far: bool | None = None) -> GlbReport:
    """Check one glb. ``budget`` is a ``BUDGETS`` key (None: no budget check).

    Over the file-size budget is a problem (the plan enforces sizes); over the
    triangle budget is a warning (Danbyte notes it but draws the model)."""
    path = Path(path)
    r = GlbReport(path=path)
    if is_lfs_pointer(path):
        r.lfs_pointer = True
        return r
    r.size = path.stat().st_size
    try:
        j, b, start = load(path)
    except GlbError as exc:
        r.problems.append(str(exc))
        return r
    far = path.name.endswith(".far.glb") if far is None else far
    nodes = j.get("nodes", [])
    r.names = [str(n.get("name", "")) for n in nodes]
    probs = r.problems

    ext = set(j.get("extensionsUsed", [])) | set(j.get("extensionsRequired", []))
    if ext - ALLOWED_EXT:
        probs.append(f"extensions not allowed: {sorted(ext - ALLOWED_EXT)}")
    for k in ("cameras", "animations", "skins"):
        if j.get(k):
            probs.append(f"has {k} (spec §10)")
    if any("uri" in x for x in j.get("buffers", [])) or any("uri" in x for x in j.get("images", [])):
        probs.append("external file references (spec §10)")

    uses = collections.Counter(n["mesh"] for n in nodes if "mesh" in n)

    def subtree_prims(k, seen=()):
        nd = nodes[k]
        total = len(j["meshes"][nd["mesh"]]["primitives"]) if "mesh" in nd else 0
        return total + sum(subtree_prims(c) for c in nd.get("children", []))

    special = 0
    for k, n in enumerate(nodes):
        if str(n.get("name", "")).startswith(("led:", "cover:", "empty:")):
            special += subtree_prims(k)
    has_uv1 = True
    calls = tris = 0
    for mi, mesh in enumerate(j.get("meshes", [])):
        for pr in mesh.get("primitives", []):
            k = uses.get(mi, 0)
            calls += k
            if pr.get("mode", 4) == 4:
                acc = j["accessors"][pr["indices"]] if "indices" in pr else j["accessors"][pr["attributes"]["POSITION"]]
                tris += acc["count"] // 3 * k
            if "TEXCOORD_1" not in pr.get("attributes", {}):
                has_uv1 = False
    r.calls, r.triangles = calls, tris
    mats = j.get("materials", [])
    r.materials = len(mats)
    occ = [m for m in mats if "occlusionTexture" in m]
    r.ao = bool(occ)
    if not occ:
        probs.append("no baked AO (occlusionTexture) - spec 6b")
    elif any(m["occlusionTexture"].get("texCoord", 0) != 1 for m in occ):
        probs.append("occlusionTexture must use texCoord 1 (the AO UV map)")
    if not has_uv1:
        probs.append("not every primitive has TEXCOORD_1 (the AO UV map)")
    for m in mats:
        p = m.get("pbrMetallicRoughness", {})
        mf = p.get("metallicFactor", 1.0)
        if m.get("name") != "LED" and 0.2 <= mf <= 0.8:
            probs.append(f"{m.get('name')}: metallicFactor {mf:.2f} - paint is 0, bare metal is 1 (spec 6a)")
        if Image is None:
            continue
        for key, ref in (("base colour", p.get("baseColorTexture")),
                         ("roughness", p.get("metallicRoughnessTexture")),
                         ("normal", m.get("normalTexture"))):
            if not ref:
                continue
            try:
                st = ImageStat.Stat(_image(j, b, start, ref))
            except Exception as exc:  # broken image data
                probs.append(f"{m.get('name')}: {key} texture can't be read ({exc})")
                continue
            chans = st.stddev[1:2] if key == "roughness" else st.stddev
            if max(chans) < FLAT_SD:
                probs.append(f"{m.get('name')}: {key} texture is blank (sd {max(chans):.1f}) - the bake carried no detail (spec 6a)")
    if far and calls > special + FAR_EXTRA_CALLS:
        probs.append(f"far LOD has {calls} draw calls; merge static meshes per material (max {special + FAR_EXTRA_CALLS}) - spec 5")

    # LED meshes use the LED material (gltfpack -kn may hang the mesh on a child).
    names = [m.get("name") for m in mats]
    for n in nodes:
        nm = str(n.get("name", ""))
        if not nm.startswith("led:"):
            continue
        holders = [n] + [nodes[c] for c in n.get("children", [])]
        meshes = [h["mesh"] for h in holders if "mesh" in h]
        if not meshes:
            probs.append(f"{nm}: an LED without a mesh")
            continue
        for mi in meshes:
            for pr in j["meshes"][mi].get("primitives", []):
                mat = pr.get("material")
                if mat is None or names[mat] != "LED":
                    probs.append(f"{nm}: uses material {names[mat] if mat is not None else None!r}, not LED (spec §4)")
                    break

    if r.size > HARD_MAX:
        probs.append(f"{r.size / MB:.1f} MB; Danbyte refuses files over {HARD_MAX // MB} MB")
    if budget:
        lim = BUDGETS[budget].get("far" if far else "full")
        if lim:
            tmax, bmax = lim
            if tris > tmax:
                r.warnings.append(f"{tris:,} triangles, over the {budget} budget of {tmax:,} (spec §5)")
            if r.size > bmax:
                probs.append(f"{r.size / 1024:.0f} KB, over the {budget} budget of {bmax / 1024:.0f} KB (spec §5)")
    return r


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pillow_available() -> bool:
    return Image is not None

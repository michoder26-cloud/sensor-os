"""3D models for the hologram: a few built-in shapes plus any .obj / .stl / .glb / .gltf / .ply file.

load(ref) returns (vertices Nx3, faces Mx3) as numpy arrays, centred, scaled to fit a unit
sphere and thinned to a face budget the software renderer can draw every frame.
"""
import math
from pathlib import Path

import numpy as np
from .i18n import tr

MODEL_DIR = Path(__file__).resolve().parent.parent / "models3d"
EXTENSIONS = (".obj", ".stl", ".glb", ".gltf", ".ply")
_cache: dict = {}


def _grid(points, nu: int, nv: int):
    """Faces for a nu x nv grid of points that wraps around in both directions."""
    faces = []
    for i in range(nu):
        for j in range(nv):
            a, b = i * nv + j, i * nv + (j + 1) % nv
            c, d = ((i + 1) % nu) * nv + j, ((i + 1) % nu) * nv + (j + 1) % nv
            faces += [(a, c, b), (b, c, d)]
    return np.array(points, float), np.array(faces, int)


def torus(nu: int = 32, nv: int = 12, r: float = 0.36):
    pts = []
    for i in range(nu):
        u = 2 * math.pi * i / nu
        for j in range(nv):
            v = 2 * math.pi * j / nv
            pts.append(((1 + r * math.cos(v)) * math.cos(u), (1 + r * math.cos(v)) * math.sin(u), r * math.sin(v)))
    return _grid(pts, nu, nv)


def knot(n: int = 120, m: int = 6, r: float = 0.34, p: int = 2, q: int = 3):
    """A torus knot: one tube that winds through itself."""
    def curve(t):
        return np.array(((2 + math.cos(q * t)) * math.cos(p * t), (2 + math.cos(q * t)) * math.sin(p * t), math.sin(q * t)))

    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c = curve(t)
        tangent = curve(t + 1e-3) - curve(t - 1e-3)
        tangent /= np.linalg.norm(tangent)
        normal = np.cross(tangent, c)
        normal /= np.linalg.norm(normal)
        binormal = np.cross(tangent, normal)
        for j in range(m):
            a = 2 * math.pi * j / m
            pts.append(c + r * (math.cos(a) * normal + math.sin(a) * binormal))
    return _grid(pts, n, m)


def sphere(sub: int = 2):
    """Icosphere."""
    g = (1 + 5 ** 0.5) / 2
    verts = [(-1, g, 0), (1, g, 0), (-1, -g, 0), (1, -g, 0), (0, -1, g), (0, 1, g),
             (0, -1, -g), (0, 1, -g), (g, 0, -1), (g, 0, 1), (-g, 0, -1), (-g, 0, 1)]
    verts = [tuple(np.array(v) / np.linalg.norm(v)) for v in verts]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
             (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
             (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(sub):
        mid, out = {}, []

        def midpoint(a, b):
            key = (min(a, b), max(a, b))
            if key not in mid:
                v = (np.array(verts[a]) + np.array(verts[b])) / 2
                verts.append(tuple(v / np.linalg.norm(v)))
                mid[key] = len(verts) - 1
            return mid[key]

        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            out += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = out
    return np.array(verts, float), np.array(faces, int)


def gem(n: int = 10):
    """A cut diamond."""
    verts = [(0.0, 0.0, 0.55)]                                     # centre of the table
    for ring_r, z in ((0.55, 0.55), (1.0, 0.25)):                  # table edge, girdle
        verts += [(ring_r * math.cos(2 * math.pi * i / n), ring_r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
    verts.append((0.0, 0.0, -1.1))                                 # the point
    tip = len(verts) - 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        t0, t1, g0, g1 = 1 + i, 1 + j, 1 + n + i, 1 + n + j
        faces += [(0, t0, t1), (t0, g0, t1), (t1, g0, g1), (g0, tip, g1)]
    return np.array(verts, float), np.array(faces, int)


BUILTIN = {"knot": (tr("Torus knot"), knot), "sphere": (tr("Sphere"), sphere), "torus": (tr("Torus"), torus), "gem": (tr("Diamond"), gem)}

# Controls: models that do something. "control:<name>" -> (label, shape). See props.py and sysctl.py.
CONTROLS = {"volume": (tr("Volume"), lambda: torus(14, 8, 0.30)), "brightness": (tr("Brightness"), gem),
            "media": (tr("Media"), knot), "system": (tr("System"), sphere)}


def normalize(verts):
    lo, hi = verts.min(0), verts.max(0)
    v = verts - (lo + hi) / 2
    return v / max(float(np.linalg.norm(v, axis=1).max()), 1e-9)


def simplify(verts, faces, max_faces: int):
    """Vertex clustering: snap vertices to a grid and merge, coarser and coarser, until it fits."""
    if len(faces) <= max_faces:
        return verts, faces
    lo = verts.min(0)
    span = float((verts.max(0) - lo).max()) or 1.0
    best = (verts, faces)
    for cells in (64, 48, 36, 28, 22, 17, 13, 10, 8, 6):
        key = np.floor((verts - lo) / span * (cells - 1e-6)).astype(np.int64)
        flat = key[:, 0] * cells * cells + key[:, 1] * cells + key[:, 2]
        _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
        merged = np.zeros((len(counts), 3))
        np.add.at(merged, inverse, verts)
        merged /= counts[:, None]
        f = inverse[faces]
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        f = f[np.unique(np.sort(f, axis=1), axis=0, return_index=True)[1]]
        best = (merged, f)
        if len(f) <= max_faces:
            break
    return best


def load(ref: str, max_faces: int = 1800):
    """ref is "builtin:<name>" or a file path. Raises on anything that cannot be read."""
    key = (ref, max_faces)
    if key not in _cache:
        if ref.startswith("control:"):
            verts, faces = CONTROLS[ref.split(":", 1)[1]][1]()
        elif ref.startswith("builtin:"):
            verts, faces = BUILTIN[ref.split(":", 1)[1]][1]()
        else:
            import trimesh
            mesh = trimesh.load(ref, force="mesh")
            verts, faces = np.asarray(mesh.vertices, float), np.asarray(mesh.faces, int)
            if len(faces) == 0:
                raise ValueError("no faces in " + ref)
        verts, faces = simplify(normalize(verts), faces, max_faces)
        _cache[key] = (normalize(verts), faces)
    return _cache[key]


def available() -> list[tuple]:
    """(label, ref) for the controls, the built-in shapes and every model file in models3d/."""
    out = [("◉  " + label, "control:" + name) for name, (label, _) in CONTROLS.items()]
    out += [(label, "builtin:" + name) for name, (label, _) in BUILTIN.items()]
    if MODEL_DIR.is_dir():
        out += [(p.stem, str(p)) for p in sorted(MODEL_DIR.iterdir()) if p.suffix.lower() in EXTENSIONS]
    return out

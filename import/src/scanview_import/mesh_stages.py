"""meshoptimizer mit Attributmetrik und gemessener Zielsuche."""

from __future__ import annotations

import ctypes
from dataclasses import replace
from typing import Any

import meshoptimizer
import numpy as np
from meshoptimizer.simplifier import lib

from .mesh_source import Mesh

# 0.2.30a0 exportiert den Wrapper, registriert dessen C-Signatur aber nicht.
# Explizite ABI-Deklaration nach meshoptimizer.h (size_t != Python/C-int).
lib.meshopt_simplifyWithAttributes.argtypes = [
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_float),
    ctypes.c_size_t,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_float),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_float),
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_size_t,
    ctypes.c_float,
    ctypes.c_uint,
    ctypes.POINTER(ctypes.c_float),
]
lib.meshopt_simplifyWithAttributes.restype = ctypes.c_size_t


def compact(mesh: Mesh, faces: np.ndarray, materials: np.ndarray) -> Mesh:
    used, inverse = np.unique(faces, return_inverse=True)
    return replace(
        mesh,
        positions=mesh.positions[used],
        triangles=inverse.reshape(-1, 3).astype(np.uint32),
        uv=mesh.uv[used] if mesh.uv is not None else None,
        colors=mesh.colors[used] if mesh.colors is not None else None,
        materials=materials,
    )


def simplify_mesh(mesh: Mesh, target: int) -> Mesh:
    """Strenge Variante: Ziel ±5 % oder ValueError."""
    out, info = simplify_with_report(mesh, target)
    if not info["zielErreicht"]:
        raise ValueError(info["grund"])
    return out


def simplify_with_report(
    mesh: Mesh, target: int, lock_border: bool = False
) -> tuple[Mesh, dict[str, Any]]:
    """Verkleinern unter Erhalt der UV-Nähte; ist das Ziel so nicht erreichbar, die kleinste
    erreichbare Stufe nehmen und das ausweisen. Photogrammetrie-Meshes bestehen aus vielen
    kleinen Texturinseln; jede Inselgrenze ist eine Naht, die nicht kollabieren darf
    (Denkmal-Mesh, Stufe normal: Untergrenze rund 700 000 von 2 992 910 Dreiecken).

    ``lock_border``: offene Ränder bleiben fest. Vereinfacht wird je Material; die Grenze zu
    einem anderen Material ist in diesem Lauf ein offener Rand. Ohne Sperre verkürzen beide Seiten
    sie unabhängig — es entstehen Risse zwischen den Materialien und der Außenrand schrumpft."""
    if target < 0:
        raise ValueError("Dreiecksziel darf nicht negativ sein")
    if target == 0 or len(mesh.triangles) <= target:
        return mesh, {
            "ziel": target,
            "erreicht": len(mesh.triangles),
            "zielErreicht": True,
            "grund": None,
        }
    positions = np.ascontiguousarray(mesh.positions - mesh.positions.mean(axis=0), dtype=np.float32)
    attributes = [a for a in (mesh.uv, mesh.colors) if a is not None]
    attrs = (
        np.ascontiguousarray(np.concatenate(attributes, axis=1), dtype=np.float32)
        if (attributes)
        else np.zeros((len(positions), 1), dtype=np.float32)
    )
    weights = (
        np.full(attrs.shape[1], 0.1, dtype=np.float32) if attributes else np.zeros(1, np.float32)
    )
    output, mats = [], []
    verfehlt: list[tuple[int, int, int]] = []
    for material in np.unique(mesh.materials):
        faces = mesh.triangles[mesh.materials == material]
        goal = max(1, round(target * len(faces) / len(mesh.triangles)))
        indices = np.ascontiguousarray(faces.ravel(), dtype=np.uint32)
        dest = np.empty_like(indices)

        def run(
            error: float, dest: np.ndarray = dest, indices: np.ndarray = indices, goal: int = goal
        ) -> np.ndarray:
            # Defaultoption 0 erhält Attributnähte. Kein Permissive/Sloppy-Fallback.
            count = meshoptimizer.simplify_with_attributes(
                dest,
                indices,
                positions,
                attrs,
                weights,
                target_index_count=goal * 3,
                target_error=error,
                options=meshoptimizer.SIMPLIFY_LOCK_BORDER if lock_border else 0,
            )
            return dest[:count].reshape(-1, 3).copy()

        lo, hi = 0.0, 0.001
        candidate = run(hi)
        for _ in range(20):
            if len(candidate) <= goal * 1.05:
                break
            lo, hi = hi, hi * 2
            candidate = run(hi)
        if len(candidate) < goal * 0.95:
            for _ in range(32):
                mid = (lo + hi) / 2
                trial = run(mid)
                if goal * 0.95 <= len(trial) <= goal * 1.05:
                    candidate = trial
                    break
                if len(trial) > goal:
                    lo = mid
                else:
                    hi = mid
        if not goal * 0.95 <= len(candidate) <= goal * 1.05:
            verfehlt.append((int(material), goal, len(candidate)))
        output.append(candidate)
        mats.append(np.full(len(candidate), material, dtype=np.int32))
    faces_out = np.concatenate(output)
    erreicht = len(faces_out)
    ok = not verfehlt and target * 0.95 <= erreicht <= target * 1.05
    grund = None
    if not ok:
        grund = (
            f"Dreiecksziel {target} unter Erhalt der UV-Nähte nicht erreichbar: {erreicht}"
            + "".join(f"; Material {m}: Ziel {g}, erreicht {n}" for m, g, n in verfehlt)
        )
    info = {"ziel": target, "erreicht": erreicht, "zielErreicht": ok, "grund": grund}
    return compact(mesh, faces_out, np.concatenate(mats)), info


def degenerate_mask(positions: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Exakt Null, kein Flächenschwellwert: schmale gültige Dreiecke bleiben erhalten."""
    duplicate = (
        (faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) | (faces[:, 0] == faces[:, 2])
    )
    points = positions[faces].astype(np.float64)
    cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    zero = np.all(cross == 0, axis=1)
    mask = duplicate | zero
    return mask, {
        "removed": int(mask.sum()),
        "duplicateIndices": int(duplicate.sum()),
        "zeroArea": int(zero.sum()),
    }


def remove_degenerate(mesh: Mesh) -> tuple[Mesh, dict[str, Any]]:
    mask, report = degenerate_mask(mesh.positions, mesh.triangles)
    if mask.all():
        raise ValueError("Mesh vollständig entartet")
    # Vertices erhalten, auch Kontrollvertices ohne verbleibende Fläche.
    return replace(mesh, triangles=mesh.triangles[~mask], materials=mesh.materials[~mask]), report


def optimize_layout(mesh: Mesh) -> Mesh:
    """Cache-/Fetch-Reihenfolge pro Material; Attribute und Kontrollvertices erhalten."""
    faces, materials = [], []
    for material in np.unique(mesh.materials):
        indices = np.ascontiguousarray(
            mesh.triangles[mesh.materials == material].ravel(), dtype=np.uint32
        )
        destination = np.empty_like(indices)
        meshoptimizer.optimize_vertex_cache(destination, indices, vertex_count=len(mesh.positions))
        faces.append(destination.reshape(-1, 3))
        materials.append(np.full(len(destination) // 3, material, dtype=np.int32))
    triangles = np.concatenate(faces)
    remap = np.empty(len(mesh.positions), dtype=np.uint32)
    used = meshoptimizer.optimize_vertex_fetch_remap(
        remap, triangles.ravel(), vertex_count=len(mesh.positions)
    )
    unused = remap == np.iinfo(np.uint32).max
    remap[unused] = np.arange(used, len(remap), dtype=np.uint32)
    order = np.argsort(remap)
    return replace(
        mesh,
        positions=mesh.positions[order],
        triangles=remap[triangles],
        materials=np.concatenate(materials),
        uv=mesh.uv[order] if mesh.uv is not None else None,
        colors=mesh.colors[order] if mesh.colors is not None else None,
    )

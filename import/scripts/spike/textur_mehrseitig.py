"""K9/K12 für Quellen und Kacheln mit mehreren Texturseiten.

``tiles_contract`` kennt je Quelle und je Kachel genau einen Texturatlas; bei mehreren
Seiten meldet es K9/K12 als ``ran: false``. Dieses Modul misst dieselben Kriterien
dreiecksgenau und unabhängig vom Kachler (kein ``scanview_import``-Lesecode):

- Quelle: eigener OBJ-Leser mit Material je Dreieck (``usemtl``/``map_Kd``).
- Kacheln: GLB je Primitive mit eigener Textur, Transformkette wie ``tiles_contract``.
- Zuordnung: Schwerpunkt bzw. Stichprobenpunkt des Blatts → nächstes Quelldreieck
  (``SurfaceIndex``). Die Geometrie selbst prüft ``tiles_contract`` (K2 bis K4).

K9: je Blattdreieck Texel/Meter im Blatt geteilt durch Texel/Meter des zugeordneten
Quelldreiecks (mit der Größe SEINER Texturseite); Median je Blatt ≥ 0,95 und jede
Texturseite ≤ 4096². K12: mittlere |ΔRGB| je Blatt ≤ 20 (von 255) zwischen Blatt- und
Quellfarbe an denselben Oberflächenpunkten.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pygltflib import GLTF2

from .source_mesh import SurfaceIndex
from .tiles_contract import (
    K9_MAX_TEXTURE_PX,
    K9_TEXEL_RATIO_MIN,
    K12_STRUCTURAL_MEAN_DRGB,
    _barycentric,
    _read_accessor,
    apply_affine,
    node_local_matrix,
    tile_matrix,
    y_up_to_z_up,
)


def _parse_mtl(path: Path) -> dict[str, Path | None]:
    materials: dict[str, Path | None] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split()
        if not parts or parts[0][0] == "#":
            continue
        if parts[0] == "newmtl" and len(parts) > 1:
            current = parts[1]
            materials[current] = None
        elif parts[0] == "map_Kd" and current is not None and len(parts) > 1:
            materials[current] = path.parent / parts[-1]
    return materials


def read_obj_pages(path: Path) -> dict[str, Any]:
    """OBJ mit Texturseite je Dreieck; UV in OBJ-Konvention (v oben)."""
    materials: dict[str, Path | None] = {}
    pages: list[Path] = []
    page_of: dict[str, int] = {}
    current = -1
    v: list[tuple[float, float, float]] = []
    vt: list[tuple[float, float]] = []
    fv: list[tuple[int, int, int]] = []
    ft: list[tuple[int, int, int]] = []
    fp: list[int] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            parts = raw.split()
            if not parts:
                continue
            key = parts[0]
            if key == "v":
                v.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif key == "vt":
                vt.append((float(parts[1]), float(parts[2])))
            elif key == "f":
                cv, ct = [], []
                for tok in parts[1:]:
                    fields = tok.split("/")
                    a = int(fields[0])
                    cv.append(a - 1 if a > 0 else len(v) + a)
                    if len(fields) < 2 or not fields[1]:
                        raise ValueError("Dreieck ohne UV")
                    b = int(fields[1])
                    ct.append(b - 1 if b > 0 else len(vt) + b)
                for i in range(1, len(cv) - 1):
                    fv.append((cv[0], cv[i], cv[i + 1]))
                    ft.append((ct[0], ct[i], ct[i + 1]))
                    fp.append(current)
            elif key == "mtllib":
                for name in parts[1:]:
                    materials.update(_parse_mtl(path.parent / name))
            elif key == "usemtl" and len(parts) > 1:
                texture = materials.get(parts[1])
                if texture is None:
                    raise ValueError(f"Material ohne Textur: {parts[1]}")
                if parts[1] not in page_of:
                    page_of[parts[1]] = len(pages)
                    pages.append(texture)
                current = page_of[parts[1]]
    face_page = np.asarray(fp, dtype=np.int32)
    if np.any(face_page < 0):
        raise ValueError("Dreiecke vor dem ersten usemtl")
    faces = np.asarray(fv, dtype=np.int64)
    uv = np.asarray(vt, dtype=np.float64)[np.asarray(ft, dtype=np.int64)]
    return {
        "vertices": np.asarray(v, dtype=np.float64),
        "faces": faces,
        "uv": uv,
        "facePage": face_page,
        "pages": pages,
    }


def _sample(img: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Texel mit Pixelmitten-Konvention (glTF/OBJ), REPEAT außerhalb [0, 1]."""
    h, w = img.shape[:2]
    col = np.floor(uv[:, 0] * w).astype(np.int64) % w
    row = np.floor((1.0 - uv[:, 1]) * h).astype(np.int64) % h
    texel: np.ndarray = img[row, col].astype(np.float64)
    return texel


def _texel_per_m(tv: np.ndarray, uvt: np.ndarray, size: np.ndarray) -> np.ndarray:
    """sqrt(UV-Fläche · W · H / Weltfläche) je Dreieck; ``size`` (M, 2)."""
    world = 0.5 * np.linalg.norm(np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]), axis=1)
    area = 0.5 * np.abs(
        (uvt[:, 1, 0] - uvt[:, 0, 0]) * (uvt[:, 2, 1] - uvt[:, 0, 1])
        - (uvt[:, 2, 0] - uvt[:, 0, 0]) * (uvt[:, 1, 1] - uvt[:, 0, 1])
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(world > 0, np.sqrt(area * size[:, 0] * size[:, 1] / world), np.nan)


def _leaves(tileset: Path) -> list[tuple[str, np.ndarray]]:
    root = json.loads(tileset.read_text())["root"]
    out: list[tuple[str, np.ndarray]] = []

    def walk(node: dict[str, Any], parent: np.ndarray) -> None:
        full = parent @ tile_matrix(node.get("transform"))
        children = node.get("children") or []
        if not children and node.get("content"):
            out.append((node["content"]["uri"], full))
        for child in children:
            walk(child, full)

    walk(root, np.eye(4))
    return out


def _decode_leaf(path: Path, chain: np.ndarray) -> list[dict[str, Any]]:
    """Primitive mit Weltdreiecken (Z oben), UV (v oben) und eigener Textur."""
    gltf = GLTF2().load(str(path))
    blob = gltf.binary_blob()
    prims: list[dict[str, Any]] = []

    def visit(index: int, parent: np.ndarray) -> None:
        node = gltf.nodes[index]
        world = parent @ node_local_matrix(node)
        if node.mesh is not None:
            for prim in gltf.meshes[node.mesh].primitives:
                pos = _read_accessor(gltf, blob, prim.attributes.POSITION).astype(np.float64)
                pos = y_up_to_z_up(apply_affine(world, pos))
                pos = apply_affine(chain, pos)
                idx = _read_accessor(gltf, blob, prim.indices).reshape(-1).astype(np.int64)
                image = None
                uv = None
                if prim.attributes.TEXCOORD_0 is not None and prim.material is not None:
                    pbr = gltf.materials[prim.material].pbrMetallicRoughness
                    if pbr is not None and pbr.baseColorTexture is not None:
                        source = gltf.textures[pbr.baseColorTexture.index].source
                        view = gltf.bufferViews[gltf.images[source].bufferView]
                        start = view.byteOffset or 0
                        data = blob[start : start + view.byteLength]
                        image = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
                        uv = _read_accessor(gltf, blob, prim.attributes.TEXCOORD_0)
                        uv = uv.astype(np.float64)
                        uv[:, 1] = 1.0 - uv[:, 1]
                prims.append(
                    {
                        "tv": pos[idx.reshape(-1, 3)],
                        "uv": None if uv is None else uv[idx.reshape(-1, 3)],
                        "image": image,
                    }
                )
        for child in node.children or []:
            visit(child, world)

    for root in gltf.scenes[gltf.scene or 0].nodes:
        visit(root, np.eye(4))
    return prims


def pruefen(tileset: Path, source_obj: Path, samples: int = 4000, seed: int = 42) -> dict[str, Any]:
    t0 = time.monotonic()
    src = read_obj_pages(source_obj)
    sizes = []
    for page in src["pages"]:
        with Image.open(page) as im:
            sizes.append(im.size)
    page_size = np.asarray(sizes, dtype=np.float64)
    src_tv = src["vertices"][src["faces"]]
    index = SurfaceIndex(src["vertices"], src["faces"])
    src_density = _texel_per_m(src_tv, src["uv"], page_size[src["facePage"]])
    images: dict[int, np.ndarray] = {}

    def page_image(i: int) -> np.ndarray:
        if i not in images:
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(src["pages"][i]) as im:
                images[i] = np.asarray(im.convert("RGB"))
        return images[i]

    rng = np.random.default_rng(seed)
    k9: dict[str, Any] = {}
    k12: dict[str, Any] = {}
    unmeasured: list[str] = []
    for uri, chain in _leaves(tileset):
        prims = _decode_leaf(tileset.parent / uri, chain)
        textured = [p for p in prims if p["image"] is not None and len(p["tv"])]
        if len(textured) != len(prims) or not textured:
            unmeasured.append(uri)
            continue
        oversized = [
            list(p["image"].shape[1::-1])
            for p in textured
            if max(p["image"].shape[:2]) > K9_MAX_TEXTURE_PX
        ]
        ratios, drgb = [], []
        for p in textured:
            tv, uv, image = p["tv"], p["uv"], p["image"]
            size = np.tile([image.shape[1], image.shape[0]], (len(tv), 1)).astype(np.float64)
            leaf_density = _texel_per_m(tv, uv, size)
            _, tri, _ = index.query(tv.mean(axis=1))
            ratios.append(leaf_density / src_density[tri])
            # K12: Stichprobenpunkte flächengewichtet im Primitive
            area = 0.5 * np.linalg.norm(np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]), axis=1)
            n = max(1, int(samples * len(tv) / sum(len(q["tv"]) for q in textured)))
            pick = rng.choice(len(tv), size=n, p=area / area.sum())
            r1, r2 = rng.random(n), rng.random(n)
            s = np.sqrt(r1)
            bary = np.column_stack([1 - s, s * (1 - r2), s * r2])
            pts = np.einsum("ij,ijk->ik", bary, tv[pick])
            leaf_color = _sample(image, np.einsum("ij,ijk->ik", bary, uv[pick]))
            _, stri, cp = index.query(pts)
            sb = _barycentric(cp, src_tv[stri, 0], src_tv[stri, 1], src_tv[stri, 2])
            suv = np.einsum("ij,ijk->ik", sb, src["uv"][stri])
            src_color = np.empty_like(leaf_color)
            for page in np.unique(src["facePage"][stri]):
                sel = src["facePage"][stri] == page
                src_color[sel] = _sample(page_image(int(page)), suv[sel])
            drgb.append(np.abs(leaf_color - src_color).mean(axis=1))
        all_ratios = np.concatenate(ratios)
        median = float(np.nanmedian(all_ratios))
        k9[uri] = {
            "medianRatio": median,
            "p05Ratio": float(np.nanpercentile(all_ratios, 5)),
            "pages": len(textured),
            "oversized": oversized,
            "ok": bool(median >= K9_TEXEL_RATIO_MIN and not oversized),
        }
        mean_drgb = float(np.concatenate(drgb).mean())
        k12[uri] = {"meanAbsDeltaRgb": mean_drgb, "ok": mean_drgb <= K12_STRUCTURAL_MEAN_DRGB}
    measured = bool(k9)
    return {
        "sourcePages": [str(p) for p in src["pages"]],
        "sourceTriangles": len(src["faces"]),
        "leavesMeasured": len(k9),
        "leavesUnmeasured": unmeasured,
        "K9": {
            "ok": (all(e["ok"] for e in k9.values()) and not unmeasured) if measured else None,
            "ran": measured,
            "minMedianRatio": min((e["medianRatio"] for e in k9.values()), default=None),
            "maxPagesPerLeaf": max((e["pages"] for e in k9.values()), default=None),
            "tiles": k9,
        },
        "K12": {
            "ok": (all(e["ok"] for e in k12.values()) and not unmeasured) if measured else None,
            "ran": measured,
            "maxMeanAbsDeltaRgb": max((e["meanAbsDeltaRgb"] for e in k12.values()), default=None),
            "tiles": k12,
        },
        "seconds": round(time.monotonic() - t0, 1),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spike.textur_mehrseitig")
    parser.add_argument("tileset", type=Path)
    parser.add_argument("--source", type=Path, required=True, help="Quell-OBJ mit .mtl")
    parser.add_argument("--samples", type=int, default=4000)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = pruefen(args.tileset, args.source, args.samples)
    text = json.dumps(report, indent=2, allow_nan=False)
    if args.out:
        args.out.write_text(text)
    summary = {k: report[k] for k in ("leavesMeasured", "leavesUnmeasured", "seconds")}
    for key in ("K9", "K12"):
        summary[key] = {k: v for k, v in report[key].items() if k != "tiles"}
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0 if report["K9"]["ok"] and report["K12"]["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())

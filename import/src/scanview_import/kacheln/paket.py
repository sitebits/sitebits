"""Meshopt/KTX2-Packschritt; ein Manifest wird erst nach Rückleseprüfung geschrieben."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pygltflib as g
from PIL import Image
from scipy.spatial import cKDTree

from ..convert import _sha256
from ..manifest_io import read_manifest, write_manifest
from ..mesh_gltf import read_gltf, write_glb
from ..mesh_source import Mesh, resolve_mesh_source
from ..mesh_textures import encode_texture, ktx_info


def _blockmasse(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width % 4 or height % 4:
        raise ValueError(f"KTX2-Kanten müssen positive Vielfache von 4 sein: {width} x {height}")


def _pfad(root: Path, base: Path, uri: str, suffix: str) -> Path:
    if not uri or ":" in uri or "\\" in uri or "?" in uri or "#" in uri:
        raise ValueError("Ungültiger Paketpfad")
    path = base / uri
    if Path(uri).is_absolute() or ".." in Path(uri).parts or path.suffix != suffix:
        raise ValueError("Ungültiger Paketpfad")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Paketpfad fehlt oder verlässt das Paket")
    return path


def _glb_texturen(path: Path) -> None:
    """Lokale meshopt-GLB und eingebettete KTX2-Budgets prüfen."""
    doc = g.GLTF2().load(str(path))
    if any(b.uri for b in doc.buffers) or any(im.uri for im in doc.images):
        raise ValueError("Externe GLB-Ressourcen verboten")
    if "EXT_meshopt_compression" not in (doc.extensionsRequired or []):
        raise ValueError("meshopt fehlt")
    blob = doc.binary_blob()
    for im in doc.images:
        if im.bufferView is None or im.mimeType != "image/ktx2":
            raise ValueError("Nur eingebettete KTX2-Texturen erlaubt")
        view = doc.bufferViews[im.bufferView]
        start = view.byteOffset or 0
        info = ktx_info(blob[start : start + view.byteLength])
        _blockmasse(info["width"], info["height"])
        if max(info["width"], info["height"]) > 4096 or info["levels"] < 1:
            raise ValueError("Texturbudget verletzt")


def _rahmen(m: dict[str, Any], layer: dict[str, Any]) -> None:
    """Schema, Bezugsrahmen und Positionsraster abgleichen."""
    if m["schemaVersion"] != 2 or layer["format"] != "3dtiles":
        raise ValueError("Gekacheltes Mesh benötigt Schema 2 und 3dtiles")
    if not layer.get("crs") or not layer.get("heightSystem"):
        raise ValueError("crs/heightSystem fehlen")
    if (
        layer["crs"] != m["frame"]["crs"]["wkt"]
        or layer["heightSystem"] != m["frame"]["crs"]["verticalDatum"]
    ):
        raise ValueError("Rahmen widerspricht der Ebene")
    if not np.isfinite(layer["positionStepM"]) or not 0 < layer["positionStepM"] <= 0.00025:
        raise ValueError("Ungültige Positionsschrittweite")


def pruefen(pkg: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Paketprüfung aus verify_mesh: ausschließlich lokale GLBs, eingebettetes KTX2."""
    m = manifest if manifest is not None else read_manifest(pkg)
    layer = next(lay for lay in m["layers"] if lay["kind"] == "mesh-tiles")
    _rahmen(m, layer)
    ts_path = _pfad(pkg, pkg, layer["url"], ".json")
    ts = json.loads(ts_path.read_text())
    if ts["asset"]["version"] != "1.1" or "content" not in ts["root"]:
        raise ValueError("3D Tiles 1.1 mit Wurzelinhalt erforderlich")
    box = np.asarray(ts["root"]["boundingVolume"]["box"], dtype=float)
    extent = np.abs(box[3:].reshape(3, 3)).sum(axis=0)
    expected = np.array([box[:3] - extent, box[:3] + extent])
    bounds = np.asarray(layer["boundsWorld"], dtype=float)
    if (
        bounds.shape != (2, 3)
        or not np.isfinite(bounds).all()
        or np.any(bounds[0] > bounds[1])
        or np.max(np.abs(bounds - expected)) > 1
    ):
        raise ValueError("boundsWorld widerspricht der Wurzelhülle")
    tiles, leaves = 0, 0
    seen: set[Path] = set()

    def walk(node: dict[str, Any], parent: np.ndarray, parent_error: float) -> None:
        nonlocal tiles, leaves
        transform = (
            np.asarray(node.get("transform", np.eye(4).ravel()), dtype=float).reshape(4, 4).T
        )
        if not np.isfinite(transform).all() or not np.allclose(transform[:3, :3], np.eye(3)):
            raise ValueError("Nur Translationen in Projektkoordinaten erlaubt")
        world = parent @ transform
        error = node["geometricError"]
        children = node.get("children", [])
        if (
            node.get("refine") != "REPLACE"
            or not np.isfinite(error)
            or error < 0
            or error >= parent_error
            or (not children and error != 0)
        ):
            raise ValueError("Ungültige REPLACE-Fehlerhierarchie")
        path = _pfad(pkg, ts_path.parent, node["content"]["uri"], ".glb")
        if path in seen:
            raise ValueError("Mehrfach verwendeter Kachelinhalt")
        seen.add(path)
        _glb_texturen(path)
        output = read_gltf(resolve_mesh_source(path))
        output.positions += world[:3, 3]
        if len(output.triangles) > 100000:
            raise ValueError("Dreiecksbudget verletzt")
        if np.any(output.positions < bounds[0] - 0.00035) or np.any(
            output.positions > bounds[1] + 0.00035
        ):
            raise ValueError("Kachelinhalt außerhalb boundsWorld")
        tiles += 1
        if not children:
            leaves += len(output.triangles)
        for child in children:
            walk(child, world, error)

    if "transform" in ts["root"]:
        raise ValueError("Wurzel muss im Projektkoordinatenrahmen bleiben")
    walk(ts["root"], np.eye(4), float("inf"))
    if leaves != layer["triangleCount"]:
        raise ValueError("Blattdreieckszahl widerspricht Manifest")
    return {"ok": True, "tiles": tiles, "leafTriangles": leaves}


class Packschritt:
    def __init__(self, pkg: Path, crs: str | None, height: str | None, *,
                 vorhanden: bool = False):
        self.pkg, self.crs, self.height = pkg, crs, height
        self.dest = pkg / "layers/kacheln"
        self.dest.mkdir(parents=True, exist_ok=vorhanden)
        self.steps: list[float] = []
        self.counts: dict[str, int] = {}
        self.reports: dict[str, Any] = {}

    def kachel(self, mesh: Mesh, uri: str, origin: np.ndarray, root: bool) -> None:
        encoded, textures = {}, []
        for material in np.unique(mesh.materials):
            texture = mesh.textures[int(material)]
            if texture is not None:
                # Keine stille Skalierung: UV beziehen sich auf die exakte PNG-Größe.
                source = texture if isinstance(texture, Path) else io.BytesIO(texture)
                with Image.open(source) as im:
                    _blockmasse(*im.size)
                data, report = encode_texture(texture, 4096, "uastc")
                info = ktx_info(data)
                _blockmasse(info["width"], info["height"])
                encoded[int(material)] = data
                textures.append(report)
        removal: dict[str, Any] = {}
        path = self.dest / uri
        step = write_glb(
            mesh, path, origin, encoded, removal, position_step_limit=0.00025, node_origin=root
        )
        output = read_gltf(resolve_mesh_source(path))
        output.positions += np.zeros(3) if root else origin
        # Rücklesen prüft tatsächlich geschriebene Quantisierung und meshopt-Dekodierung.
        distance = float(cKDTree(output.positions).query(mesh.positions)[0].max())
        reverse = float(cKDTree(mesh.positions).query(output.positions)[0].max())
        source_centers = mesh.positions[mesh.triangles].mean(axis=1)
        output_centers = output.positions[output.triangles].mean(axis=1)
        face_distance = cKDTree(output_centers).query(source_centers)[0]
        # Splitter, die dünner als das Quantisierungsraster sind, fallen beim Schreiben auf
        # Fläche null und werden entfernt; nur sie dürfen in der Ausgabe fehlen.
        missing = face_distance > 0.00035
        corners = mesh.positions[mesh.triangles[missing]]
        edges = np.linalg.norm(corners - np.roll(corners, 1, axis=1), axis=2)
        doubled_area = np.linalg.norm(
            np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
        )
        height = doubled_area / np.maximum(edges.max(axis=1, initial=0.0), 1e-12)
        removed = int(removal.get("removed", 0))
        face_error = float(face_distance[~missing].max(initial=0.0))
        if (
            len(output.triangles) != len(mesh.triangles) - removed
            or int(missing.sum()) > removed
            or np.any(height > step * np.sqrt(3))
            or max(distance, reverse, face_error) > 0.00035
            or step > 0.00025
        ):
            raise ValueError("Gepackte Kachel verletzt Geometrievertrag")
        self.steps.append(step)
        self.counts[uri] = len(output.triangles)
        self.reports[uri] = {
            "textures": textures,
            "positionStepM": step,
            "vertexMaxErrorM": max(distance, reverse),
            "faceCenterMaxErrorM": face_error,
            "removal": removal,
        }

    def uebernehmen(self, uri: str, step: float, count: int, report: dict[str, Any]) -> None:
        """Nur erfolgreich rückgelesene Arbeiterergebnisse zusammenführen."""
        self.steps.append(step)
        self.counts[uri] = count
        self.reports[uri] = report

    def abschliessen(self, tileset: dict[str, Any], source: Mesh) -> None:
        ts = copy.deepcopy(tileset)
        (self.dest / "tileset.json").write_text(json.dumps(ts, indent=2, allow_nan=False))

        def count(node: dict[str, Any]) -> int:
            children = node.get("children", [])
            return (
                sum(count(c) for c in children) if children else self.counts[node["content"]["uri"]]
            )

        box = np.asarray(ts["root"]["boundingVolume"]["box"])
        extent = np.abs(box[3:].reshape(3, 3)).sum(axis=0)
        manifest = {
            "schemaVersion": 2,
            "projectId": "mesh-kacheln",
            "revision": 1,
            "title": "Mesh",
            "frame": {
                "crs": {"wkt": self.crs, "verticalDatum": self.height, "declaredBy": "cli"},
                "unit": "m",
                "unitDeclaredBy": "cli",
                "originWorld": box[:3].tolist(),
            },
            "initialView": {
                "target": box[:3].tolist(),
                "distance": float(np.linalg.norm(extent) * 2),
            },
            "layers": [
                {
                    "id": "kacheln",
                    "kind": "mesh-tiles",
                    "title": "Mesh",
                    "format": "3dtiles",
                    "url": "layers/kacheln/tileset.json",
                    "boundsWorld": [list(box[:3] - extent), list(box[:3] + extent)],
                    "crs": self.crs,
                    "heightSystem": self.height,
                    "triangleCount": count(ts["root"]),
                    "sourceTriangleCount": len(source.triangles),
                    "positionStepM": max(self.steps),
                    "source": {"sha256": _sha256(source.source.path)},
                    "provenance": {
                        "kind": "other",
                        "interpolated": True,
                        "scaleVerified": False,
                        "scaleSource": "unit:m",
                    },
                }
            ],
        }
        # Derselbe Einstieg wie bei anderen Mesh-Importen, ohne Docker beim Rücklesen.
        from ..mesh_verify import verify_mesh

        report = verify_mesh(source.source.path, self.pkg, source, manifest)
        (self.dest / "packen-log.json").write_text(
            json.dumps({"verify": report, "tiles": self.reports}, indent=2, allow_nan=False)
        )
        if not report["ok"]:
            raise ValueError("Paketprüfung fehlgeschlagen")
        write_manifest(self.pkg, manifest)

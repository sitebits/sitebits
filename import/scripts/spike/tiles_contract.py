"""3D-Tiles-Ausgabevertrag: Laden + Prüfen. Ausnahme vom Wegwerf-Charakter des
Spikes (Plan-Constraints §Global) — wird in Teil 5 zum produktiven Weg-B-``verify``.

Transform-Konvention, in dieser Reihenfolge:

1. glTF-Node-Weltmatrix = Produkt der Node-Matrizen (bzw. TRS → Matrix, ``T @ R @ S``)
   von der/den Szenenwurzel(n) (``scene.nodes``) bis zum Meshknoten — Traversierung nur
   über ``node.children``, nie über alle ``gltf.nodes`` linear.
2. POSITION wird als Float32 gelesen und sofort auf Float64 gehoben, dann mit der
   Node-Weltmatrix transformiert — noch in glTFs Y-oben-Konvention.
3. Y-oben → Z-oben: ``(x, y, z) → (x, -z, y)``.
4. Nur b3dm: ``RTC_CENTER`` aus der Feature-Table wird UNVERÄNDERT (ohne
   Achsenumrechnung) addiert. Fix Round 1 (Review-Befund): RTC_CENTER ist nicht Teil
   der glTF-Vertexdaten, sondern eine Kachel-Metaangabe im 3D-Tiles-eigenen Rahmen
   (bereits Z-oben) — CesiumJS/3D-Tiles-1.0-Reihenfolge ist
   ``tileTransform · T(RTC_CENTER) · axisCorrection(Y→Z) · nodeTransforms``, gelesen
   von rechts: erst Node-Transforms (Y-oben), dann die Achsenkorrektur nach Z-oben,
   ERST DANACH die (unrotierte) RTC_CENTER-Translation, zuletzt die Kachelkette. Die
   vorherige Fassung rotierte RTC_CENTER fälschlich mit ``y_up_to_z_up`` — bei echten
   b3dm-Dateien (RTC_CENTER stammt vom Werkzeug, nicht vom Prüfer) hätte das falsche
   Weltpositionen erzeugt; der ursprüngliche Test bemerkte das nicht, weil er dieselbe
   (falsche) Funktion zur Berechnung des erwarteten Werts benutzte (selbstreferenziell).
5. Kachel-Weltmatrix = Produkt der ``tile.transform`` (spaltenmajor, 4x4) von der
   Wurzel bis zur Kachel; jede Matrix wird als ``np.array(t, dtype=float64).reshape(4,
   4, order="F")`` gelesen (spaltenmajor → NumPy).
6. Endposition = Kachel-Weltmatrix @ (Ergebnis aus Schritt 4).

Quantisierte/komprimierte Inhalte (``KHR_draco_mesh_compression``,
``EXT_meshopt_compression``, ``KHR_mesh_quantization`` mit nicht-Float-POSITION) werden
VOR dem Dekodieren erkannt und als ``encoding="unsupported:<grund>"`` markiert — es wird
dann nichts gemessen (keine Scheinwerte). Ist auch nur eine Kachel eines
Tilesets nicht lesbar, misst ``check_tileset`` GAR NICHT auf dem lesbaren Rest (das würde
ein Loch vortäuschen) — die betroffenen K-Felder werden ``{"ran": False, "ok": None}``,
die Kachel(n) landen in ``verdict["unmeasured"]``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import io
import json
import math
import resource
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import permutations, product
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

import numpy as np
from PIL import Image
from pygltflib import FLOAT, GLTF2

from scanview_import.geometry import quat_to_matrix

from .obj_read import load_npz
from .source_mesh import SourceMesh, SurfaceIndex

# --- Abnahmekriterien-Grenzwerte (Constraints §Abnahmekriterien) ---------------------
K2_K3_TOL_M = 0.00035
K4_REL_TOL = 0.001
K6_TOL_M = 0.001
K9_TEXEL_RATIO_MIN = 0.95
K9_MAX_TEXTURE_PX = 4096
K10_MAX_TRIANGLES = 500_000
K11_MAX_STEP_M = 0.00025
K12_STRUCTURAL_MEAN_DRGB = 20.0  # von 255


@dataclass
class TileMesh:
    """Ein Kachelinhalt nach allen Transformationen (siehe Modul-Docstring), lokal Z oben."""

    tile_path: str
    is_leaf: bool
    geometric_error: float
    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int64
    uv: np.ndarray | None  # (M, 3, 2) float32, OBJ-Konvention (v nach oben)
    texture_px: tuple[int, int] | None
    texture_image: Image.Image | None
    encoding: str  # 'glb' | 'b3dm' | 'unsupported:<grund>'


@dataclass
class _DecodedLocal:
    """Kachelinhalt lokal Z oben, VOR der Kachel-Transformkette (Schritt 5/6 fehlen noch)."""

    vertices: np.ndarray
    faces: np.ndarray
    uv: np.ndarray | None
    texture_px: tuple[int, int] | None
    texture_image: Image.Image | None
    # K11 (Fix Round 1): np.spacing der betragsgrößten ROHEN (Vor-Transform) Float32-
    # POSITION — siehe _decode_gltf_geometry.
    position_step_m: float


@dataclass
class _TileGeom:
    """Interne Buchhaltung für die K6-Reparatursuche (Achsvertausch/ECEF-Root) — nicht Teil
    des öffentlichen Interfaces (``load_tileset`` gibt nur ``TileMesh`` zurück)."""

    local: np.ndarray  # (N, 3) float64, Z oben, vor der Kachel-Transformkette
    faces: np.ndarray
    chain_full: np.ndarray  # 4x4: Produkt aller tile.transform inkl. Root
    chain_no_root: np.ndarray  # 4x4: Produkt aller tile.transform OHNE den Root-Beitrag
    position_step_m: float  # K11: np.spacing der rohen Vor-Transform-Float32-POSITION


# --- glTF-Grundbausteine --------------------------------------------------------------

_COMPONENT_DTYPE: dict[int, type] = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TYPE_COMPONENTS: dict[str, int] = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def y_up_to_z_up(v: np.ndarray) -> np.ndarray:
    """glTF Y-oben → Z-oben: ``(x, y, z) -> (x, -z, y)``. Letzte Achse ist xyz."""
    x = v[..., 0]
    y = v[..., 1]
    z = v[..., 2]
    return np.stack([x, -z, y], axis=-1)


def _quat_xyzw_to_matrix(q: tuple[float, float, float, float]) -> np.ndarray:
    """glTF-Quaternion ist (x, y, z, w) — ``quat_to_matrix`` (getestet in
    ``scanview_import``) erwartet (w, x, y, z); hier bewusst umsortiert (nicht
    "unwrapped") aufgerufen."""
    x, y, z, w = q
    return quat_to_matrix((w, x, y, z))


def node_local_matrix(node: Any) -> np.ndarray:
    """Lokale Node-Matrix: ``node.matrix`` direkt (spaltenmajor), sonst ``T @ R @ S`` aus
    TRS (glTF-Defaults: Translation 0, Rotation Einheit, Skala 1)."""
    if node.matrix is not None:
        return np.array(node.matrix, dtype=np.float64).reshape(4, 4, order="F")
    m = np.eye(4)
    if node.scale is not None:
        m = np.diag([*node.scale, 1.0]) @ m
    if node.rotation is not None:
        r = np.eye(4)
        r[:3, :3] = _quat_xyzw_to_matrix(tuple(node.rotation))
        m = r @ m
    if node.translation is not None:
        t = np.eye(4)
        t[:3, 3] = node.translation
        m = t @ m
    return m


def apply_affine(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Homogene 4x4-Matrix auf (N, 3)-Punkte angewendet."""
    if len(pts) == 0:
        return pts
    return cast(np.ndarray, (m[:3, :3] @ pts.T).T + m[:3, 3])


def tile_matrix(t: list[float] | None) -> np.ndarray:
    """``tile.transform`` (spaltenmajor, 4x4) → NumPy-Matrix. Fehlt es, gilt Einheit."""
    if t is None:
        return np.eye(4)
    return np.array(t, dtype=np.float64).reshape(4, 4, order="F")


def _unsupported_reason(gltf: GLTF2) -> str | None:
    """Draco/Meshopt/Quantisierung/Sparse-Accessoren/ADD-Refine erkennen, BEVOR
    irgendetwas dekodiert wird (keine Scheinwerte) — Reihenfolge der Prüfungen ist
    beliebig, alle sind unabhängig."""
    used = set(gltf.extensionsUsed or []) | set(gltf.extensionsRequired or [])
    if "KHR_draco_mesh_compression" in used:
        return "draco"
    for mesh in gltf.meshes or []:
        for prim in mesh.primitives:
            if prim.extensions and "KHR_draco_mesh_compression" in prim.extensions:
                return "draco"
    for bv in gltf.bufferViews or []:
        if bv.extensions and "EXT_meshopt_compression" in bv.extensions:
            return "meshopt"
    for mesh in gltf.meshes or []:
        for prim in mesh.primitives:
            pos_idx = prim.attributes.POSITION
            if pos_idx is not None and gltf.accessors[pos_idx].componentType != FLOAT:
                return "quantization"
            # Sparse Accessoren (ALSO-Punkt, Fix Round 1): nicht implementiert — klar
            # als unsupported statt teilweise/falsch zu dekodieren.
            for acc_idx in (pos_idx, prim.attributes.TEXCOORD_0, prim.indices):
                if acc_idx is not None and gltf.accessors[acc_idx].sparse is not None:
                    return "sparse-accessor"
    return None


def _dequantize_normalized(arr: np.ndarray, component_type: int) -> np.ndarray:
    """glTF ``normalized``-Integer -> Float64 in [-1, 1] bzw. [0, 1] (glTF-Formel).
    Betrifft in dieser Kachel-Prüfung nur TEXCOORD_0 (POSITION mit nicht-Float-
    Komponente ist immer schon ``unsupported:quantization``, siehe oben)."""
    if component_type == 5121:  # UNSIGNED_BYTE
        return arr.astype(np.float64) / 255.0
    if component_type == 5123:  # UNSIGNED_SHORT
        return arr.astype(np.float64) / 65535.0
    if component_type == 5120:  # BYTE
        return np.maximum(arr.astype(np.float64) / 127.0, -1.0)
    if component_type == 5122:  # SHORT
        return np.maximum(arr.astype(np.float64) / 32767.0, -1.0)
    return arr.astype(np.float64)


def _read_accessor(gltf: GLTF2, blob: bytes, accessor_index: int) -> np.ndarray:
    acc = gltf.accessors[accessor_index]
    bv = gltf.bufferViews[acc.bufferView]
    dtype = _COMPONENT_DTYPE[acc.componentType]
    ncomp = _TYPE_COMPONENTS[acc.type]
    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    count = acc.count
    itemsize = np.dtype(dtype).itemsize
    if bv.byteStride and bv.byteStride != itemsize * ncomp:
        out = np.empty((count, ncomp), dtype=dtype)
        for i in range(count):
            start = base + i * bv.byteStride
            out[i] = np.frombuffer(blob, dtype=dtype, count=ncomp, offset=start)
        arr = out
    else:
        flat: np.ndarray = np.frombuffer(blob, dtype=dtype, count=count * ncomp, offset=base)
        arr = flat.reshape(count, ncomp)
    # ALSO-Punkt (Fix Round 1): normalized TEXCOORD_0 (uint8/uint16, üblich zum
    # Platzsparen) muss dequantisiert werden, sonst liest man rohe 0..255/0..65535
    # statt UV in [0, 1] — falsche K9/K12-Texturkoordinaten ohne jede Fehlermeldung.
    if acc.normalized:
        return _dequantize_normalized(arr, acc.componentType)
    return arr


def _decode_data_uri(uri: str) -> bytes | None:
    """``data:<mime>;base64,<...>`` — eigene, minimale Dekodierung: pygltflibs
    ``decode_data_uri`` splittet auf den festen Header ``data:application/octet-
    stream;base64,`` und scheitert deshalb an Bild-Data-URIs (``data:image/png;...``)."""
    if not uri.startswith("data:") or "base64," not in uri:
        return None
    try:
        _, b64 = uri.split("base64,", 1)
        return base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return None


def _load_gltf_image(
    gltf: GLTF2, blob: bytes, index: int, base_dir: Path | None
) -> Image.Image | None:
    """Fix Round 1 (Review-Befund): zuvor wurden Texturen nur über ``bufferView``
    unterstützt — externe Datei-URIs (das übliche Format bei b3dm-Batchen und vielen
    Werkzeugen) wurden klaglos übersprungen, wodurch K9/K12 nicht ``ran:false``
    meldeten, sondern in den Fällen mit ``ok:True`` durchliefen, ohne je gemessen zu
    haben (siehe ``_check_k9``/``_check_k12``, die das jetzt separat verhindern)."""
    img = gltf.images[index]
    if img.bufferView is not None:
        bv = gltf.bufferViews[img.bufferView]
        start = bv.byteOffset or 0
        data = blob[start : start + bv.byteLength]
        try:
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            return None
    if img.uri is not None:
        uri_data = _decode_data_uri(img.uri)
        if uri_data is not None:
            try:
                return Image.open(io.BytesIO(uri_data)).convert("RGB")
            except Exception:
                return None
        if base_dir is not None:
            candidate = base_dir / unquote(img.uri)
            if candidate.is_file():
                try:
                    return Image.open(candidate).convert("RGB")
                except Exception:
                    return None
    return None  # nicht ladbar -> None, NIE eine erfundene Textur.


def _decode_gltf_geometry(
    gltf: GLTF2, rtc_center: np.ndarray | None, base_dir: Path | None
) -> _DecodedLocal:
    blob = gltf.binary_blob()
    all_v: list[np.ndarray] = []
    all_f: list[np.ndarray] = []
    all_uv: list[np.ndarray | None] = []
    tex_sources: set[int] = set()
    have_uv_any = False
    have_uv_missing = False
    offset = 0
    # K11 (Fix Round 1): größter Betrag unter den ROHEN, gespeicherten Float32-
    # Positionswerten — VOR jeder Transformation. Das misst die tatsächliche
    # Speicherpräzision des Werkzeugs; an den fertigen Weltkoordinaten wäre das
    # bedeutungslos, sobald ECEF-Root oder RTC_CENTER große Verschiebungen wieder
    # herausrechnen (siehe Modul-Docstring/Report).
    raw_max_abs = 0.0

    def visit(node_idx: int, parent: np.ndarray) -> None:
        nonlocal offset, have_uv_any, have_uv_missing, raw_max_abs
        node = gltf.nodes[node_idx]
        world = parent @ node_local_matrix(node)
        if node.mesh is not None:
            for prim in gltf.meshes[node.mesh].primitives:
                pos_raw = _read_accessor(gltf, blob, prim.attributes.POSITION)
                if len(pos_raw):
                    raw_max_abs = max(raw_max_abs, float(np.max(np.abs(pos_raw))))
                pos = pos_raw.astype(np.float64)
                pos_h = np.column_stack([pos, np.ones(len(pos))])
                pos_world = (world @ pos_h.T).T[:, :3]
                pos_zup = y_up_to_z_up(pos_world)
                if prim.indices is not None:
                    idx = _read_accessor(gltf, blob, prim.indices).reshape(-1).astype(np.int64)
                else:
                    idx = np.arange(len(pos), dtype=np.int64)
                faces = idx.reshape(-1, 3) + offset
                all_v.append(pos_zup)
                all_f.append(faces)
                if prim.attributes.TEXCOORD_0 is not None:
                    have_uv_any = True
                    uv_full = _read_accessor(gltf, blob, prim.attributes.TEXCOORD_0)
                    uv_full = uv_full.astype(np.float32).copy()
                    uv_full[:, 1] = 1.0 - uv_full[:, 1]  # glTF v-unten -> OBJ-Konvention v-oben
                    all_uv.append(uv_full[idx].reshape(-1, 3, 2))
                else:
                    have_uv_missing = True
                    all_uv.append(None)
                if prim.material is not None:
                    mat = gltf.materials[prim.material]
                    pbr = mat.pbrMetallicRoughness
                    if pbr is not None and pbr.baseColorTexture is not None:
                        tex = gltf.textures[pbr.baseColorTexture.index]
                        if tex.source is not None:
                            tex_sources.add(tex.source)
                offset += len(pos)
        for child in node.children or []:
            visit(child, world)

    scene = gltf.scenes[gltf.scene if gltf.scene is not None else 0]
    for root_idx in scene.nodes:
        visit(root_idx, np.eye(4))

    vertices = np.concatenate(all_v) if all_v else np.zeros((0, 3))
    faces = np.concatenate(all_f) if all_f else np.zeros((0, 3), dtype=np.int64)
    if rtc_center is not None and len(vertices):
        # RTC_CENTER ist bereits Z-oben (Kachel-eigener Rahmen) — NICHT nochmal
        # rotieren (Fix Round 1, siehe Modul-Docstring Schritt 4).
        vertices = vertices + rtc_center

    uv: np.ndarray | None = None
    if have_uv_any and not have_uv_missing and all_uv:
        uv = np.concatenate([u for u in all_uv if u is not None])

    texture_px = None
    texture_image = None
    # Mehr als eine Textur je Kachel: K9/K12 bleiben für diese Kachel ohne Textur
    # (Review-Hinweis) statt eine davon willkürlich zu wählen.
    if len(tex_sources) == 1 and uv is not None:
        texture_image = _load_gltf_image(gltf, blob, next(iter(tex_sources)), base_dir)
        texture_px = texture_image.size if texture_image is not None else None

    position_step_m = float(np.spacing(np.float32(raw_max_abs))) if raw_max_abs > 0 else 0.0

    return _DecodedLocal(
        vertices=vertices,
        faces=faces,
        uv=uv,
        texture_px=texture_px,
        texture_image=texture_image,
        position_step_m=position_step_m,
    )


def _decode_glb(
    data: bytes, rtc_center: np.ndarray | None = None, base_dir: Path | None = None
) -> tuple[_DecodedLocal | None, str]:
    gltf = GLTF2.load_from_bytes(data)
    reason = _unsupported_reason(gltf)
    if reason is not None:
        return None, f"unsupported:{reason}"
    return _decode_gltf_geometry(gltf, rtc_center, base_dir), "glb"


def _decode_b3dm(data: bytes, base_dir: Path | None) -> tuple[_DecodedLocal | None, str]:
    """b3dm-Header (28 Byte, 3D Tiles 1.0): magic, version, byteLength, je JSON-/Binary-
    Länge von Feature- und Batch-Table; danach folgt der eingebettete glb-Body."""
    _magic, _version, byte_length, ft_json_len, ft_bin_len, bt_json_len, bt_bin_len = struct.unpack(
        "<4sIIIIII", data[:28]
    )
    offset = 28
    ft_json_raw = data[offset : offset + ft_json_len]
    offset += ft_json_len
    ft_bin = data[offset : offset + ft_bin_len]
    offset += ft_bin_len
    offset += bt_json_len + bt_bin_len
    glb_bytes = data[offset:byte_length]

    ft_json = json.loads(ft_json_raw.decode("utf-8")) if ft_json_len else {}
    rtc = ft_json.get("RTC_CENTER")
    rtc_center: np.ndarray | None = None
    if rtc is not None:
        if isinstance(rtc, dict):
            bo = rtc.get("byteOffset", 0)
            raw = np.frombuffer(ft_bin, dtype=np.float32, count=3, offset=bo)
            rtc_center = raw.astype(np.float64)
        else:
            rtc_center = np.array(rtc, dtype=np.float64)

    decoded, encoding = _decode_glb(glb_bytes, rtc_center, base_dir)
    if decoded is None:
        return None, encoding
    return decoded, "b3dm"


def _decode_tile_content(path: Path) -> tuple[_DecodedLocal | None, str]:
    """Fix Round 1 (ALSO-Punkt, Review-Befund): vorher riss ein unbekanntes Format
    (insbesondere ein externes Tileset, ``content.uri`` zeigt auf eine weitere
    ``.json``-Datei — in 3D Tiles üblich) mit einer ``ValueError`` den ganzen Check ab.
    Jetzt: klar als ``unsupported`` melden (kein Absturz, keine Scheinmessung), externe
    Tilesets NICHT rekursiv verfolgt (bewusste Umfangsgrenze).

    Korrektur nach einem echten hq-Lauf gegen mago: ``tileset.json`` kann eine
    ``content.uri`` nennen, deren Datei GAR NICHT existiert — beobachtet am echten
    mago-1.16.3-Lauf gegen das Testmesh hq: Exit-Code 0, `tileset.json` nennt
    vier Kacheln, nur zwei der vier `.glb`-Dateien liegen tatsächlich unter `data/`.
    Ursache durch einen gezielten Diagnoselauf (identische Flags,
    ``-XX:+ExitOnOutOfMemoryError``) STARK GESTÜTZT, NICHT
    BEWIESEN: vermutlich ein echter
    Java-Heap-``OutOfMemoryError``, den mago im Normalfall (ohne diesen Schalter) in
    einzelnen parallelen Post-Processing-Worker-Threads schluckt, statt ihn zu loggen
    oder den Gesamtprozess abzubrechen — manche Kacheln schaffen es unter Speicherdruck
    zufällig durch, andere nicht, beides bei Exit-Code 0. Vorher riss das mit einer
    unbehandelten ``FileNotFoundError`` den ganzen Check ab. Wie beim unbekannten
    Format: klar als ``unsupported`` melden statt abzustürzen — das ist KEIN
    erfundenes Format-Problem, sondern ein eigener Grund (``missing-file``), damit
    der Bericht den Unterschied zu z. B. Draco/Meshopt zeigt."""
    if not path.is_file():
        return None, "unsupported:missing-file"
    data = path.read_bytes()
    if data[:4] == b"b3dm":
        return _decode_b3dm(data, path.parent)
    if data[:4] == b"glTF":
        return _decode_glb(data, base_dir=path.parent)
    if path.suffix == ".json" or data.lstrip()[:1] == b"{":
        return None, "unsupported:external-tileset"
    return None, "unsupported:unknown-format"


# --- Tileset-Baum ----------------------------------------------------------------------


def _walk_tile(
    node: dict[str, Any],
    parent_full: np.ndarray,
    parent_no_root: np.ndarray,
    is_root: bool,
    base_dir: Path,
    tiles: list[TileMesh],
    geoms: dict[str, _TileGeom],
    inherited_refine: str,
    add_refine_found: list[bool],
) -> None:
    own = tile_matrix(node.get("transform"))
    full = parent_full @ own
    no_root = np.eye(4) if is_root else parent_no_root @ own
    # Bugfix (erster Lauf gegen ein REALES Werkzeug statt der
    # synthetischen Task-1-Fixtures deckte das auf): Obj2Tiles v1.6.2 schreibt für
    # Blätter explizit ``"children": null`` statt den Schlüssel wegzulassen oder ``[]``
    # zu setzen. ``dict.get("children", [])`` liefert den Default NUR, wenn der
    # Schlüssel FEHLT — bei explizitem ``null`` kommt ``None`` durch, `len(None)`
    # crasht. ``or []`` fängt beide Fälle (fehlender Schlüssel UND expliziter
    # ``null``-Wert) ab.
    children = node.get("children") or []
    is_leaf = len(children) == 0
    # ALSO-Punkt (Fix Round 1): "Blätter = volle Auflösung" (K2-K4-Vertrag) setzt
    # REPLACE-Refinement voraus (nur die Blätter tragen zur sichtbaren Fläche bei).
    # Mit ADD trägt auch der Inhalt innerer Kacheln bei — dann wäre die reine
    # Blatt-Vereinigung nicht mehr "die volle Auflösung". ``refine`` wird vererbt,
    # wenn eine Kachel es nicht selbst setzt (3D-Tiles-Spezifikation).
    refine = node.get("refine", inherited_refine)
    if refine == "ADD":
        add_refine_found[0] = True
    content = node.get("content")
    if content is not None:
        uri = content["uri"]
        decoded, encoding = _decode_tile_content(base_dir / uri)
        geometric_error = float(node.get("geometricError", 0.0))
        if decoded is None:
            tiles.append(
                TileMesh(
                    tile_path=uri,
                    is_leaf=is_leaf,
                    geometric_error=geometric_error,
                    vertices=np.zeros((0, 3)),
                    faces=np.zeros((0, 3), dtype=np.int64),
                    uv=None,
                    texture_px=None,
                    texture_image=None,
                    encoding=encoding,
                )
            )
        else:
            world = apply_affine(full, decoded.vertices)
            tiles.append(
                TileMesh(
                    tile_path=uri,
                    is_leaf=is_leaf,
                    geometric_error=geometric_error,
                    vertices=world,
                    faces=decoded.faces,
                    uv=decoded.uv,
                    texture_px=decoded.texture_px,
                    texture_image=decoded.texture_image,
                    encoding=encoding,
                )
            )
            geoms[uri] = _TileGeom(
                local=decoded.vertices,
                faces=decoded.faces,
                chain_full=full,
                chain_no_root=no_root,
                position_step_m=decoded.position_step_m,
            )
    for child in children:
        _walk_tile(
            child, full, no_root, False, base_dir, tiles, geoms, refine, add_refine_found
        )


_RichLoad = tuple[dict[str, Any], list[TileMesh], dict[str, _TileGeom], bool]


def _load_tileset_rich(tileset: Path) -> _RichLoad:
    root_json = json.loads(tileset.read_text())
    tiles: list[TileMesh] = []
    geoms: dict[str, _TileGeom] = {}
    add_refine_found = [False]
    _walk_tile(
        root_json["root"], np.eye(4), np.eye(4), True, tileset.parent, tiles, geoms,
        "REPLACE", add_refine_found,
    )
    return root_json, tiles, geoms, add_refine_found[0]


def load_tileset(tileset: Path) -> tuple[dict[str, Any], list[TileMesh]]:
    root_json, tiles, _, _ = _load_tileset_rich(tileset)
    return root_json, tiles


# --- Hilfen für den Prüfer --------------------------------------------------------------


def _union_surface_index(tiles: list[TileMesh], k: int = 16) -> SurfaceIndex:
    verts, faces = [], []
    offset = 0
    for t in tiles:
        if len(t.faces) == 0:
            continue
        verts.append(t.vertices)
        faces.append(t.faces + offset)
        offset += len(t.vertices)
    if not verts:
        raise ValueError("keine Kachelgeometrie zum Prüfen (alle Kacheln leer)")
    return SurfaceIndex(np.concatenate(verts), np.concatenate(faces), k=k)


def _union_source_mesh(tiles: list[TileMesh]) -> SourceMesh:
    verts, faces = [], []
    offset = 0
    for t in tiles:
        if len(t.faces) == 0:
            continue
        verts.append(t.vertices)
        faces.append(t.faces + offset)
        offset += len(t.vertices)
    v = np.concatenate(verts) if verts else np.zeros((0, 3))
    f = np.concatenate(faces) if faces else np.zeros((0, 3), dtype=np.int64)
    return SourceMesh(vertices=v, faces=f, uv=None, texture=None)


def _control_vertices(source: SourceMesh, seed: int = 42) -> np.ndarray:
    """16 Kontrollvertices: Extrempunkte je Achse (±) + 10 Zufallsvertices (K6).

    Nur Vertices, die ein Dreieck benutzt: verwaiste Punkte (z. B. nach Bereinigung in
    der Quell-OBJ stehengeblieben) gehören nicht zur Fläche und kommen in keiner Kachel vor.
    """
    v = source.vertices[np.unique(source.faces)]
    extremes = np.array(
        [
            v[np.argmax(v[:, 0])],
            v[np.argmin(v[:, 0])],
            v[np.argmax(v[:, 1])],
            v[np.argmin(v[:, 1])],
            v[np.argmax(v[:, 2])],
            v[np.argmin(v[:, 2])],
        ]
    )
    rng = np.random.default_rng(seed)
    random_idx = rng.choice(len(v), size=min(10, len(v)), replace=False)
    return np.vstack([extremes, v[random_idx]])


def _build_24_proper_rotations() -> list[np.ndarray]:
    """Alle 24 eigentlichen (det=+1) vorzeichenbehafteten Permutationsmatrizen —
    Kandidaten für die K6-Reparatursuche (Achsentausch/Vorzeichenfehler)."""
    mats = []
    for perm in permutations(range(3)):
        base = np.zeros((3, 3))
        for row, col in enumerate(perm):
            base[row, col] = 1.0
        for signs in product((1.0, -1.0), repeat=3):
            m = base * np.array(signs)[:, None]
            if abs(np.linalg.det(m) - 1.0) < 1e-9:
                mats.append(m)
    return mats


_PROPER_ROTATIONS = _build_24_proper_rotations()


def _bbox_center(points: np.ndarray) -> np.ndarray:
    return cast(np.ndarray, 0.5 * (points.min(axis=0) + points.max(axis=0)))


def _search_rigid_correction(
    leaves: list[TileMesh],
    geoms: dict[str, _TileGeom],
    control: np.ndarray,
    source_bbox_center: np.ndarray,
    tol: float = K6_TOL_M,
) -> list[dict[str, Any]]:
    """K6-Reparatursuche: {volle Kette, Kette ohne Root} x 24 Rotationen, je Kandidat
    zusätzlich mit einer aus der Bounding-Box-Mitte (Blatt-Vereinigung vs. Quelle)
    abgeleiteten Translation (Fix Round 1, Review-Befund: reiner Offset — ohne
    Rotationsfehler, ohne ECEF-Root — muss ebenfalls als reparierbar erkannt werden,
    das Vertragskriterium K6 nennt "Offset" ausdrücklich als eigene reparierbare
    Ursache neben Achsentausch und Root-Transform). Die Translation wird NACH der
    Rotation angewendet und danach an den 16 Kontrollvertices verifiziert — eine
    falsche Rotation richtet die Form nicht aus, selbst wenn die Bounding-Box-Mitten
    zufällig zusammenfallen (der asymmetrische Quader macht das eindeutig).

    Die root-lose Variante wird nur versucht, wenn sie sich für mindestens ein Blatt
    tatsächlich von der vollen Kette unterscheidet — sonst wären beide Varianten
    identisch und würden als zwei separate "Treffer" fälschlich Mehrdeutigkeit
    melden (Review-Fix, Runde 0).

    Fix Round 2 (Review-Befund): "volle Kette + abgeleitete Translation" und "ohne
    Root-Transform + ~0-Translation" können GEOMETRISCH dieselbe Korrektur sein — ein
    reiner Translations-Root (keine Rotation) wird von der vollen Kette über die
    Translation ausgeglichen UND vom Root-Ausschluss direkt vermieden, beides liefert
    identische korrigierte Punkte. Solche Duplikate werden vor der Rückgabe entfernt
    (Vergleich über die tatsächlich korrigierten Punkte, nicht über die
    ``(chain, rotation)``-Metadaten — zwei verschiedene "Erklärungen" für dieselbe
    Korrektur sind kein Mehrdeutigkeitsbefund)."""
    variants = [("volle Kette", "chain_full")]
    differs = any(
        not np.allclose(geoms[t.tile_path].chain_full, geoms[t.tile_path].chain_no_root)
        for t in leaves
        if t.tile_path in geoms
    )
    if differs:
        variants.append(("ohne Root-Transform", "chain_no_root"))

    fits: list[dict[str, Any]] = []
    accepted_pts: list[np.ndarray] = []
    for label, attr in variants:
        for rot in _PROPER_ROTATIONS:
            pts_list, faces_list = [], []
            offset = 0
            for t in leaves:
                geom = geoms.get(t.tile_path)
                if geom is None or len(geom.local) == 0:
                    continue
                chain = getattr(geom, attr)
                local_rot = geom.local @ rot.T
                world = apply_affine(chain, local_rot)
                pts_list.append(world)
                faces_list.append(geom.faces + offset)
                offset += len(world)
            if not pts_list:
                continue
            all_pts = np.concatenate(pts_list)
            translation = source_bbox_center - _bbox_center(all_pts)
            shifted_pts = all_pts + translation
            idx = SurfaceIndex(shifted_pts, np.concatenate(faces_list))
            d = idx.distance(control)
            if d.max() <= tol:
                is_duplicate = any(
                    shifted_pts.shape == prev.shape
                    and float(np.max(np.abs(shifted_pts - prev))) <= tol
                    for prev in accepted_pts
                )
                if is_duplicate:
                    continue
                accepted_pts.append(shifted_pts)
                fits.append(
                    {
                        "chain": label,
                        "chainAttr": attr,
                        "rotation": rot,
                        "translation": translation,
                        "maxErrorM": float(d.max()),
                    }
                )
    return fits


def _apply_correction(
    tiles: list[TileMesh], geoms: dict[str, _TileGeom], fit: dict[str, Any]
) -> list[TileMesh]:
    out = []
    for t in tiles:
        geom = geoms.get(t.tile_path)
        if geom is None or len(geom.local) == 0:
            out.append(t)
            continue
        chain = geom.chain_full if fit["chainAttr"] == "chain_full" else geom.chain_no_root
        local_rot = geom.local @ fit["rotation"].T
        world = apply_affine(chain, local_rot) + fit["translation"]
        out.append(dataclasses.replace(t, vertices=world))
    return out


def _barycentric(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Baryzentrische Koordinaten von P (bekanntlich in der Dreiecksebene ABC) — (n, 3)."""
    v0 = b - a
    v1 = c - a
    v2 = p - a
    d00 = np.sum(v0 * v0, axis=-1)
    d01 = np.sum(v0 * v1, axis=-1)
    d11 = np.sum(v1 * v1, axis=-1)
    d20 = np.sum(v2 * v0, axis=-1)
    d21 = np.sum(v2 * v1, axis=-1)
    denom = d00 * d11 - d01 * d01
    denom_safe = np.where(denom != 0, denom, 1.0)
    v = (d11 * d20 - d01 * d21) / denom_safe
    w = (d00 * d21 - d01 * d20) / denom_safe
    u = 1.0 - v - w
    return np.column_stack([u, v, w])


def _sample_texture(img: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Nächster-Nachbar-Texturfarbe für UV in OBJ-Konvention (v nach oben)."""
    h, w = img.shape[0], img.shape[1]
    col = np.clip((uv[:, 0] * (w - 1)).round().astype(np.int64), 0, w - 1)
    row = np.clip(((1.0 - uv[:, 1]) * (h - 1)).round().astype(np.int64), 0, h - 1)
    return cast(np.ndarray, img[row, col].astype(np.float64))


def _texel_per_meter(
    vertices: np.ndarray,
    faces: np.ndarray,
    uv: np.ndarray,
    texture_px: tuple[int, int],
    n: int,
    seed: int,
) -> np.ndarray:
    """Texel/Meter je Stichprobendreieck: ``sqrt(UV-Fläche * W * H / Weltfläche)`` (K9/S2)."""
    rng = np.random.default_rng(seed)
    m = len(faces)
    idx = rng.choice(m, size=min(n, m), replace=False)
    tv = vertices[faces[idx]]
    world_area = 0.5 * np.linalg.norm(np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]), axis=1)
    uvt = uv[idx]
    uv_area = 0.5 * np.abs(
        (uvt[:, 1, 0] - uvt[:, 0, 0]) * (uvt[:, 2, 1] - uvt[:, 0, 1])
        - (uvt[:, 2, 0] - uvt[:, 0, 0]) * (uvt[:, 1, 1] - uvt[:, 0, 1])
    )
    w, h = texture_px
    with np.errstate(divide="ignore", invalid="ignore"):
        safe_area = np.where(world_area > 0, world_area, 1.0)
        ratio = np.sqrt(np.where(world_area > 0, uv_area * w * h / safe_area, 0.0))
    return ratio


# --- Einzelne Kriterien ------------------------------------------------------------------


def _check_k7(root_json: dict[str, Any]) -> dict[str, Any]:
    t = root_json.get("root", {}).get("transform")
    if t is None:
        return {"transform": None, "ok": True}
    m = tile_matrix(t)
    return {"transform": t, "ok": bool(np.allclose(m, np.eye(4), atol=1e-9))}


def _check_k10(tiles: list[TileMesh]) -> dict[str, Any]:
    """Korrektur: eine nicht lesbare/fehlende Kachel
    (``encoding`` beginnt mit ``"unsupported"``) trug vorher ``len(t.faces) == 0`` in
    ``trianglesPerTile`` ein — 0 ist aber eine ERFUNDENE Messung (die Kachel wurde nie
    gelesen, nicht "hat 0 Dreiecke"), und 0 <= 500 000 ließ so eine unlesbare Kachel
    fälschlich als K10-konform durchgehen. Unlesbare Kacheln bekommen jetzt ``None``
    (sichtbar "nicht gemessen") und zählen NICHT zu ``ok`` bei — weder als Verstoß noch
    als Bestätigung, exakt wie K2/K3/K4/K6/K8/K9/K11/K12 das für unlesbare Kacheln
    bereits handhaben."""
    per_tile: dict[str, int | None] = {}
    ok = True
    for t in tiles:
        if t.encoding.startswith("unsupported"):
            per_tile[t.tile_path] = None
            continue
        n = len(t.faces)
        per_tile[t.tile_path] = n
        ok = ok and n <= K10_MAX_TRIANGLES
    return {"trianglesPerTile": per_tile, "ok": ok}


def _check_k13(tiles: list[TileMesh]) -> dict[str, Any]:
    encodings = {t.tile_path: t.encoding for t in tiles}
    issues: dict[str, str] = {}
    for t in tiles:
        if t.encoding.startswith("unsupported"):
            issues[t.tile_path] = t.encoding
        elif t.encoding == "b3dm":
            issues[t.tile_path] = "b3dm (Nachschritt b3dmToGlb erforderlich)"
        # 'glb' ohne erkannte Meshopt-/KTX2-Kompression: Prüfung dafür ist Task-1-
        # Scope-Grenze (siehe Report) — keine Aussage ohne echte Erkennung.
    return {"encodings": encodings, "issues": issues, "ok": len(issues) == 0}


def _leaf_descendant_uris(node: dict[str, Any]) -> list[str]:
    # Obj2Tiles v1.6.2 schreibt für Blätter explizit "children": null (siehe
    # _walk_tile-Kommentar) — ``not children`` ist für None bereits wahr, diese Zeile
    # war nie das Problem; die Konsistenz mit ``or []`` unten ist trotzdem klarer.
    children = node.get("children") or []
    if not children:
        content = node.get("content")
        return [content["uri"]] if content else []
    out: list[str] = []
    for c in children:
        out.extend(_leaf_descendant_uris(c))
    return out


def _check_k8(
    inner: list[TileMesh],
    leaves: list[TileMesh],
    root_json: dict[str, Any],
    samples: int,
    seed: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ok_all = True

    # Blätter: ``geometricError`` muss exakt 0 sein (Brief/K8: "Blätter = 0, innere
    # Kacheln >= gemessener ..."). Korrektur vor dem
    # mago-Echtlauf: diese Hälfte von K8 fehlte komplett — nur die INNEREN Kacheln
    # wurden geprüft, die frühere Fassung gab bei ``not inner`` sogar sofort
    # ``ok: True`` zurück, OHNE je ein Blatt anzusehen. Am mago-Smoke-Test (Box,
    # Standardeinstellungen) beobachtet: das Blatt trägt ``geometricError: 0.01``,
    # nicht ``0`` — ohne diese Ergänzung wäre das unbemerkt als K8 ``ok: True``
    # durchgelaufen, obwohl der Vertrag das ausdrücklich verlangt.
    for t in leaves:
        leaf_ok = t.geometric_error == 0.0
        ok_all = ok_all and leaf_ok
        out[t.tile_path] = {
            "isLeaf": True,
            "declaredGeometricError": t.geometric_error,
            "ok": leaf_ok,
        }

    if not inner:
        return {"tiles": out, "ok": ok_all}
    by_path = {t.tile_path: t for t in leaves}
    mapping: dict[str, list[TileMesh]] = {}

    def walk(node: dict[str, Any]) -> None:
        content = node.get("content")
        # Bugfix (siehe _walk_tile): explizites "children":
        # null (Obj2Tiles v1.6.2) statt fehlendem Schlüssel oder [] — dict.get(...,
        # default) greift nur beim fehlenden Schlüssel, ``or []`` fängt beides ab.
        children = node.get("children") or []
        if content and children:
            uris = _leaf_descendant_uris(node)
            mapping[content["uri"]] = [by_path[u] for u in uris if u in by_path]
        for c in children:
            walk(c)

    walk(root_json["root"])

    for t in inner:
        descendants = mapping.get(t.tile_path, [])
        if not descendants or len(t.faces) == 0:
            out[t.tile_path] = {"ran": False, "ok": None}
            continue
        simplified = SourceMesh(vertices=t.vertices, faces=t.faces, uv=None, texture=None)
        pts, _, _ = simplified.sample_surface(samples, seed)
        idx = _union_surface_index(descendants)
        measured = float(idx.distance(pts).max())
        ok = t.geometric_error >= measured
        ok_all = ok_all and ok
        out[t.tile_path] = {
            "measuredMaxErrorM": measured,
            "declaredGeometricError": t.geometric_error,
            "ok": ok,
        }
    return {"tiles": out, "ok": ok_all}


def _check_k9(
    source: SourceMesh, leaves: list[TileMesh], samples: int, seed: int
) -> dict[str, Any]:
    """K9: Texturgröße je Kachel <= 4096² UND Texeldichte (Fix Round 1, Review-Befund
    — die Fassung davor prüfte gar keine Größe, sodass ein 8192²-Blatt mit
    ausreichender AKTUELLER Texeldichte durchlief, obwohl es weit über dem Limit lag).

    Einordnung:
    - Nicht übergroß (<= 4096²): jetzige Texeldichte < 95% der Quelle ist strukturell
      (Festlegung: unabhängig von der Größe — echter Detailverlust jetzt, nicht erst
      nach einem Nachschritt).
    - Übergroß (> 4096²): reparierbar NUR, wenn ein Downscale auf 4096 (Faktor
      ``4096 / max(w, h)``) die Dichte nicht unter 95% der Quelle drückt — sonst
      strukturell ("Verkleinern drückt die Blätter unter die
      Quellauflösung").

    Fehlt einer Kachel Textur/UV, wird sie NICHT gemessen (``ran: False``) — nie ein
    ``ok: True`` ohne Messung; sind ALLE Blätter unmessbar, ist das Gesamtergebnis
    ``ran: False, ok: None`` statt trivial ``True`` (zweiter Review-Befund).

    Review-Befund M7: ``source.texture`` kommt aus dem Quellmesh-Cache (``.npz`` +
    Texturpfad, siehe ``obj_read.cache_npz``) und kann auf der aktuellen Maschine
    fehlen oder unlesbar sein (verschobener/gelöschter Cache, kaputte Datei) — das darf
    NICHT als unbehandelte Exception bis zur CLI durchschlagen, sondern muss wie eine
    fehlende Textur-Referenz als ``ran: False`` gemeldet werden."""
    if source.uv is None or source.texture is None:
        return {"ran": False, "ok": None}
    try:
        with Image.open(source.texture) as im:
            src_px = im.size
    except OSError:
        return {"ran": False, "ok": None}
    src_samples = _texel_per_meter(source.vertices, source.faces, source.uv, src_px, samples, seed)
    src_ratio = float(np.median(src_samples))
    out: dict[str, Any] = {}
    measured_any = False
    ok_all = True
    structural_any = False
    for t in leaves:
        if t.uv is None or t.texture_px is None or len(t.faces) == 0:
            out[t.tile_path] = {"ran": False, "ok": None}
            continue
        measured_any = True
        leaf_ratio = float(
            np.median(_texel_per_meter(t.vertices, t.faces, t.uv, t.texture_px, samples, seed))
        )
        rel = leaf_ratio / src_ratio if src_ratio > 0 else 0.0
        w, h = t.texture_px
        oversized = max(w, h) > K9_MAX_TEXTURE_PX
        entry: dict[str, Any] = {
            "leafTexelPerM": leaf_ratio,
            "sourceTexelPerM": src_ratio,
            "ratio": rel,
            "texturePx": [w, h],
            "oversized": oversized,
        }
        if not oversized:
            ok = rel >= K9_TEXEL_RATIO_MIN
            entry["ok"] = ok
            entry["structural"] = not ok
        else:
            rel_after = rel * K9_MAX_TEXTURE_PX / max(w, h)
            repairable = rel_after >= K9_TEXEL_RATIO_MIN
            entry["ratioAfterResizeTo4096"] = rel_after
            entry["ok"] = False
            entry["repairable"] = repairable
            entry["structural"] = not repairable
        ok_all = ok_all and entry["ok"]
        structural_any = structural_any or entry["structural"]
        out[t.tile_path] = entry
    if not measured_any:
        return {"tiles": out, "ran": False, "ok": None}
    return {"tiles": out, "ok": ok_all, "structural": structural_any}


def _check_k11(tiles: list[TileMesh], geoms: dict[str, _TileGeom]) -> dict[str, Any]:
    """Positionsschritt (K11): ``np.spacing`` der betragsgrößten ROHEN, gespeicherten
    Float32-POSITION-Werte (Fix Round 1, Review-Befund — vorher an den bereits
    transformierten Weltkoordinaten gemessen, was bei ECEF-Root/RTC_CENTER bedeutungs-
    los ist: eine riesige rohe Speicherzahl, die per Node-/Root-Transform wieder auf
    kleine Weltkoordinaten zurückgerechnet wird, hätte einen winzigen, falschen
    Weltkoordinaten-Schritt gemeldet statt den echten, groben Speicherschritt)."""
    out: dict[str, Any] = {}
    measured_any = False
    ok_all = True
    for t in tiles:
        geom = geoms.get(t.tile_path)
        if t.encoding.startswith("unsupported") or geom is None or len(geom.local) == 0:
            out[t.tile_path] = {"ran": False, "ok": None}
            continue
        measured_any = True
        step = geom.position_step_m
        ok = bool(step <= K11_MAX_STEP_M)
        ok_all = ok_all and ok
        out[t.tile_path] = {"positionStepM": step, "ok": ok}
    if not measured_any:
        return {"tiles": out, "ran": False, "ok": None}
    return {"tiles": out, "ok": ok_all}


def _check_k12(
    source: SourceMesh, leaves: list[TileMesh], samples: int, seed: int
) -> dict[str, Any]:
    """Fix Round 1 (Review-Befund, Transparenz): nicht messbare Blätter (fehlende
    Textur/UV — z. B. eine externe Bild-URI, die auch nach dem Auflösungsversuch
    nicht ladbar war) erscheinen jetzt EINZELN als ``{"ran": False, "ok": None}`` in
    ``tiles`` statt stillschweigend zu fehlen; sind ALLE Blätter unmessbar, bleibt das
    Gesamtergebnis ``ran: False, ok: None`` (nie ein ``ok: True`` ohne Messung).

    Review-Befund M7 (siehe ``_check_k9``): fehlt oder scheitert ``source.texture``
    beim Öffnen, wird das genauso als ``ran: False`` gemeldet statt als unbehandelte
    Exception durchzuschlagen."""
    if source.uv is None or source.texture is None:
        return {"ran": False, "ok": None}
    usable = [
        t for t in leaves if t.uv is not None and t.texture_image is not None and len(t.faces)
    ]
    usable_paths = {t.tile_path for t in usable}
    out: dict[str, Any] = {
        t.tile_path: {"ran": False, "ok": None} for t in leaves if t.tile_path not in usable_paths
    }
    if not usable:
        return {"tiles": out, "ran": False, "ok": None}

    src_index = SurfaceIndex(source.vertices, source.faces)
    src_tv = source.triangle_vertices()
    try:
        with Image.open(source.texture) as im:
            src_img = np.asarray(im.convert("RGB"), dtype=np.float64)
    except OSError:
        return {"tiles": out, "ran": False, "ok": None}

    structural = False
    for t in usable:
        mesh = SourceMesh(vertices=t.vertices, faces=t.faces, uv=t.uv, texture=None)
        n = min(samples, len(mesh.faces) * 200)
        pts, tri_idx, bary = mesh.sample_surface(n, seed)
        assert t.uv is not None  # durch den usable-Filter oben garantiert
        leaf_uv = np.einsum("ij,ijk->ik", bary, t.uv[tri_idx])
        assert t.texture_image is not None
        leaf_img = np.asarray(t.texture_image.convert("RGB"), dtype=np.float64)
        leaf_color = _sample_texture(leaf_img, leaf_uv)

        _, tri_idx_src, cp = src_index.query(pts)
        a, b, c = src_tv[tri_idx_src, 0], src_tv[tri_idx_src, 1], src_tv[tri_idx_src, 2]
        bary_src = _barycentric(cp, a, b, c)
        src_uv_tri = source.uv[tri_idx_src]
        src_uv = np.einsum("ij,ijk->ik", bary_src, src_uv_tri)
        src_color = _sample_texture(src_img, src_uv)

        mean_drgb = float(np.mean(np.abs(leaf_color - src_color)))
        uv_break = mean_drgb > K12_STRUCTURAL_MEAN_DRGB
        structural = structural or uv_break
        out[t.tile_path] = {"meanAbsDeltaRgb": mean_drgb, "uvBreak": uv_break}
    return {"tiles": out, "ok": not structural, "structural": structural}


def _verdict(report: dict[str, Any]) -> dict[str, list[str]]:
    structural: list[str] = []
    repairable: list[str] = []

    for key in ("K2", "K3", "K4"):
        blk = report.get(key)
        if blk and blk.get("ok") is False:
            structural.append(key)

    k6 = report.get("K6")
    if k6 and k6.get("ok") is False:
        (repairable if k6.get("repairable") else structural).append("K6")

    k7 = report.get("K7")
    if k7 and k7.get("ok") is False:
        repairable.append("K7")

    k8 = report.get("K8")
    if k8 and k8.get("ok") is False:
        repairable.append("K8")

    k9 = report.get("K9")
    if k9 and k9.get("ok") is False:
        # K9 (Fix Round 1): >4096px nur reparierbar, wenn Verkleinern auf 4096 die
        # Blätter nicht unter 95% der Quellauflösung drückt; ein bereits jetzt zu
        # niedriger Texel-Wert (ohne Größenverstoß) ist strukturell (s. o.).
        (structural if k9.get("structural") else repairable).append("K9")

    k10 = report.get("K10")
    if k10 and k10.get("ok") is False:
        structural.append("K10")

    k11 = report.get("K11")
    if k11 and k11.get("ok") is False:
        repairable.append("K11")

    k12 = report.get("K12")
    if k12 and k12.get("structural"):
        structural.append("K12")

    k13 = report.get("K13")
    if k13 and k13.get("ok") is False:
        for path, reason in k13.get("issues", {}).items():
            if not reason.startswith("unsupported"):
                repairable.append(f"K13:{path}")

    return {"structural": structural, "repairable": repairable}


def check_tileset(
    tileset: Path, source: SourceMesh, *, samples: int = 10_000, seed: int = 42
) -> dict[str, Any]:
    root_json, tiles, geoms, has_add_refine = _load_tileset_rich(tileset)
    leaves = [t for t in tiles if t.is_leaf]
    inner = [t for t in tiles if not t.is_leaf]
    unsupported = {t.tile_path: t.encoding for t in tiles if t.encoding.startswith("unsupported")}
    # ALSO-Punkt (Fix Round 1): refine="ADD" irgendwo im Baum invalidiert die
    # "Blätter = volle Auflösung"-Annahme hinter K2-K4/K8/K9/K12 (siehe _walk_tile) —
    # wie ein unlesbares Format behandelt (kein Absturz, keine Scheinmessung).
    if has_add_refine:
        unsupported = {**unsupported, "__tileset__": "unsupported:refine-add"}

    report: dict[str, Any] = {
        "K13": _check_k13(tiles),
        "K7": _check_k7(root_json),
        "K10": _check_k10(tiles),
    }

    if unsupported:
        # Konvention: keine Scheinmessung auf dem lesbaren Rest, wenn
        # irgendeine Kachel nicht dekodiert werden konnte.
        for key in ("K2", "K3", "K4", "K5", "K6", "K8", "K9", "K11", "K12"):
            report[key] = {"ran": False, "ok": None}
        report["K14"] = {"ran": False}
        verdict = _verdict(report)
        verdict["unmeasured"] = sorted(unsupported)
        # Korrektur (echter hq-Lauf gegen mago): eine
        # im Tileset referenzierte, aber auf der Platte fehlende Content-Datei ist ein
        # GEMESSENER Defekt (die Geometrie dieser Kachel fehlt tatsächlich), kein bloß
        # "nicht dekodierbares" Encoding wie Draco/Meshopt/Quantisierung — diese anderen
        # Gründe bleiben ausschließlich in "unmeasured" (echte Encoding-Limitierung des
        # Prüfers, keine Aussage über die Geometrie). "missing-file" landet deshalb
        # zusätzlich in "structural", pro betroffener Kacheldatei einzeln benannt.
        missing_content = sorted(
            uri for uri, reason in unsupported.items() if reason == "unsupported:missing-file"
        )
        verdict["structural"] = sorted(
            verdict["structural"] + [f"K2:missing-content:{uri}" for uri in missing_content]
        )
        report["verdict"] = verdict
        return report

    control = _control_vertices(source, seed=42)
    raw_leaf_index = _union_surface_index(leaves)
    d_raw = raw_leaf_index.distance(control)
    k6_raw_ok = bool(d_raw.max() <= K6_TOL_M)
    source_bbox_center = _bbox_center(source.vertices)
    fits = (
        []
        if k6_raw_ok
        else _search_rigid_correction(leaves, geoms, control, source_bbox_center)
    )
    unique_fit = fits[0] if len(fits) == 1 else None

    report["K6"] = {
        "n": len(control),
        "maxErrorM": float(d_raw.max()),
        "ok": k6_raw_ok,
        "repairable": unique_fit is not None,
        "ambiguous": len(fits) > 1,
        "fits": [
            {
                "chain": f["chain"],
                "maxErrorAfterFixM": f["maxErrorM"],
                "translationNormM": float(np.linalg.norm(f["translation"])),
                # Korrektur: vorher fehlte die ROTATION im
                # Report komplett -- "translationNormM" allein verleitet dazu,
                # "translationNormM ~= 6378137" fälschlich als "reine Verschiebung,
                # keine Achsvertauschung" zu lesen. Am mago-Smoke-Test beobachtet: der
                # einzige Fit brauchte zusätzlich eine echte 90°-Rotation (zyklische
                # Vertauschung, keine Identität) -- ohne dieses Feld war das aus dem
                # Report nicht erkennbar, nur durch direkten Aufruf von
                # ``_search_rigid_correction`` im Python-Interpreter.
                "rotationMatrix": f["rotation"].tolist(),
                "isIdentityRotation": bool(np.allclose(f["rotation"], np.eye(3))),
                "translationM": f["translation"].tolist(),
            }
            for f in fits
        ],
    }

    # K2-K4/K12 messen gegen die Quelle — bei behebbarem Rahmenfehler (Achsvertausch,
    # ECEF-Root) auf den KORRIGIERTEN Blattdaten, sonst strukturell auf den rohen (Review
    # Focus 1: "strukturell wird auf Basis der korrigierten Werte entschieden"). K8/K9
    # vergleichen Kachelinhalte NUR untereinander bzw. gegen flächen-/UV-invariante
    # Größen — ein gemeinsamer Starrkörperfehler kürzt sich dort heraus, deshalb bleiben
    # sie unverändert auf den rohen Werten (siehe Report, Abschnitt Rückrechnung).
    eval_leaves = _apply_correction(leaves, geoms, unique_fit) if unique_fit else leaves
    leaf_index = _union_surface_index(eval_leaves) if unique_fit else raw_leaf_index

    src_pts, _, _ = source.sample_surface(samples, seed)
    d_k2 = leaf_index.distance(src_pts)
    report["K2"] = {
        "n": samples,
        "maxErrorM": float(d_k2.max()),
        "ok": bool(d_k2.max() <= K2_K3_TOL_M),
        # Fix Round 2 (Review-Wunsch): Eimer-Radien/Fallback-Rate mitloggen, damit
        # die OBJ-Schritte an echten Daten (Testmesh hq) sehen, ob SurfaceIndex dort
        # noch performant zertifiziert oder oft in den Kugel-Fallback fällt.
        "indexDiagnostics": leaf_index.stats(),
    }

    leaf_mesh = _union_source_mesh(eval_leaves)
    leaf_pts, _, _ = leaf_mesh.sample_surface(samples, seed)
    src_index = SurfaceIndex(source.vertices, source.faces)
    d_k3 = src_index.distance(leaf_pts)
    report["K3"] = {
        "n": samples,
        "maxErrorM": float(d_k3.max()),
        "ok": bool(d_k3.max() <= K2_K3_TOL_M),
        "indexDiagnostics": src_index.stats(),
    }

    leaf_area = leaf_mesh.area()
    src_area = source.area()
    rel = abs(leaf_area - src_area) / src_area if src_area > 0 else float("inf")
    report["K4"] = {
        "sourceAreaM2": src_area,
        "leafAreaM2": leaf_area,
        "relDiff": rel,
        "ok": bool(rel <= K4_REL_TOL),
    }

    report["K5"] = {
        "sourceTriangles": len(source.faces),
        "leafTriangles": int(sum(len(t.faces) for t in leaves)),
    }

    report["K8"] = _check_k8(inner, leaves, root_json, samples=1000, seed=seed)
    report["K9"] = _check_k9(source, leaves, samples=1000, seed=seed)
    report["K11"] = _check_k11(tiles, geoms)
    report["K12"] = _check_k12(source, eval_leaves, samples=1000, seed=seed)
    report["K14"] = {"ran": False}

    verdict = _verdict(report)
    verdict["unmeasured"] = []
    report["verdict"] = verdict
    return report


# --- CLI -------------------------------------------------------------------------------
#
# K14 war zunächst nur als FELD vorgesehen, ohne CLI; ein Lauf behalf sich mit einem
# Treiber außerhalb des Repos. Deshalb gibt es jetzt eine
# echte, getestete CLI in diesem Modul (Ausnahme vom Wegwerf-Charakter des Spikes, siehe
# constraints.md — der Prüfer ist Teil 5s produktiver Weg-B-``verify``).


def _sanitize_for_json(obj: Any) -> Any:
    """Macht einen ``check_tileset``-Report JSON-fest:

    - NumPy-Skalare/-Arrays -> native Python-Typen (``json.dumps`` kennt ``np.float64``
      & Co. nicht).
    - ``Path``-Objekte -> ``str``.
    - NaN/±Infinity -> String-Sentinel. Python erlaubt ``NaN``/``Infinity`` standardmäßig
      in ``json.dumps`` (nicht RFC-8259-konform — genau das fiel beim rohen
      Obj2Tiles-``tileset.json`` auf, von Pythons ``json.loads`` toleriert). Der PRÜFER
      selbst soll hier sauberer sein als die geprüften Werkzeuge, deshalb werden diese
      Werte explizit (und sichtbar, nicht stillschweigend als ``null``) als String
      kodiert, statt sich auf ``allow_nan`` zu verlassen.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _sanitize_for_json(obj.tolist())
    if isinstance(obj, np.generic):
        return _sanitize_for_json(obj.item())
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _fill_k14_from_validator(report: dict[str, Any], validator_report_path: Path) -> None:
    """Füllt K14 aus einem echten ``3d-tiles-validator``-JSON-Report (verifiziertes
    Schema, gegen einen echten Lauf gegen ``scanview-spike/validator:0.6.1`` geprüft):
    oberste Ebene trägt ``numErrors``/``numWarnings``/``numInfos`` direkt
    (kein Durchlaufen des ``issues``-Baums nötig). Brief: "K14: keine Fehler (Warnungen
    berichtet)" -> ``ok`` ist ausschließlich an ``numErrors == 0`` geknüpft, Warnungen
    fließen nur informativ mit ein. Ob ein K14-Fehler durch einen K7/K8/K13-Nachschritt
    behebbar ist (Brief-Einordnung), ist eine Einzelfallentscheidung, nicht automatisch
    entscheidbar hier — K14 wird deshalb NICHT automatisch in
    ``verdict.structural``/``verdict.repairable`` einsortiert, bleibt aber als eigenes,
    vollständiges Feld sichtbar."""
    data = json.loads(validator_report_path.read_text())
    errors = int(data.get("numErrors", 0))
    warnings = int(data.get("numWarnings", 0))
    infos = int(data.get("numInfos", 0))
    report["K14"] = {
        "ran": True,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "ok": errors == 0,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spike.tiles_contract",
        description=(
            "3D-Tiles-Ausgabevertrag pruefen (K1-K14) gegen ein echtes Tileset. "
            "Exit 0 = Messung durchgefuehrt (auch wenn einzelne K-Kriterien fehlschlagen "
            "-- eine Messung, kein Gate); Exit ungleich 0 = Eingabe nicht lesbar."
        ),
    )
    parser.add_argument("tileset", type=Path, help="Pfad zur tileset.json")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Quellmesh-Cache (.npz, siehe scripts/spike/obj_read.py cache_npz/load_npz)",
    )
    parser.add_argument(
        "--texture",
        type=Path,
        default=None,
        help=(
            "Textur-Override. SourceMesh/.npz traegt den beim Cache-Bau aufgeloesten "
            "Texturpfad bereits (obj_read.cache_npz) -- diese Option ist nur noetig, "
            "wenn dieser Pfad auf der aktuellen Maschine nicht (mehr) existiert."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Ziel-JSON fuer den Report (Default: stdout)"
    )
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--validator-report",
        type=Path,
        default=None,
        help="3d-tiles-validator-JSON-Report (numErrors/numWarnings) -- fuellt K14.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not args.tileset.is_file():
        print(f"Tileset nicht gefunden: {args.tileset}", file=sys.stderr)
        return 1
    try:
        source = load_npz(args.source)
    except Exception as exc:  # Eingabe unlesbar -> Exit != 0 (Brief), nicht crashen.
        print(f"Quelle nicht lesbar ({args.source}): {exc}", file=sys.stderr)
        return 1
    if args.texture is not None:
        source = dataclasses.replace(source, texture=args.texture)

    try:
        report = check_tileset(args.tileset, source, samples=args.samples, seed=args.seed)
    except Exception as exc:  # z. B. kaputtes/unbekanntes Tileset-JSON.
        print(f"Pruefung fehlgeschlagen ({args.tileset}): {exc}", file=sys.stderr)
        return 1

    if args.validator_report is not None:
        try:
            _fill_k14_from_validator(report, args.validator_report)
        except Exception as exc:
            print(
                f"Validator-Report nicht lesbar ({args.validator_report}): {exc}",
                file=sys.stderr,
            )
            return 1

    text = json.dumps(_sanitize_for_json(report), indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)

    # Randbedingung: Prüfer darf bei riesigen Quellen (hq: 3,9 Mio.
    # Vertices/7,7 Mio. Dreiecke) nicht unbeobachtet über ~8 GiB RSS gehen. Peak-RSS auf
    # stderr (nicht stdout -- das bleibt reines JSON, auch bei --out weglassen), damit
    # der Bericht das ohne einen separaten Wrapper/`/usr/bin/time` messen kann.
    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    print(f"Peak-RSS (Prüfer): {peak_rss_gib:.3f} GiB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

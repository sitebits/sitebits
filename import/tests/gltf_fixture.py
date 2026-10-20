"""Testhilfe (kein Testmodul): baut synthetische 3D-Tiles-Tilesets für
``test_tiles_contract.py`` — asymmetrischer Quader
(ein *symmetrischer* Würfel wäre unter allen 24 Achsenvertauschungen auf sich selbst
abbildbar und würde ``axis_swapped`` nicht zum Scheitern bringen).

Wichtig: Die hier verwendete Y-oben→Z-oben-Umrechnung ist bewusst eine EIGENE, direkt
hingeschriebene Ausdrucksform (``(x, z, -y)``), nicht der Aufruf einer Funktion aus
``spike.tiles_contract`` — ein gemeinsamer Vorzeichenfehler in Schreiber und Prüfer
würde sich sonst gegenseitig aufheben und im Test nicht auffallen.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np
import pygltflib as g
from PIL import Image

# Asymmetrischer Quader: drei DEUTLICH verschiedene Ausdehnungen (1.0 / 0.4 / 0.7 m),
# außermittig in y und z — bricht jede Rotations-/Spiegelsymmetrie, die ein Einheitswürfel
# noch hätte. Fix Round 1 (Review-Befund): die ursprüngliche Fassung (1.0 / 0.6 / 0.65)
# hatte y- und z-Ausdehnung zu nah beieinander (0.6 vs. 0.65) — sobald die K6-
# Reparatursuche zusätzlich zur Rotation eine aus der Bounding-Box-Mitte abgeleitete
# Translation zulässt (Fix für "Offset ist reparierbar"), reichte diese kleine
# Restähnlichkeit aus, damit mehrere der 24 Rotationen die 16 Kontrollvertices nach
# Neuzentrierung fälschlich als "passend" meldeten (Mehrdeutigkeit statt eines
# eindeutigen Treffers). Mit deutlich verschiedenen Ausdehnungen bleibt nur die
# tatsächlich richtige Rotation innerhalb der 1-mm-Toleranz.
XMIN, XMAX = -0.5, 0.5
YMIN, YMAX = -0.10, 0.30
ZMIN, ZMAX = -0.15, 0.55
CUT_Z = 0.1


def _p(x: float, y: float, z: float) -> np.ndarray:
    return np.array([x, y, z], dtype=np.float64)


_Pt8 = tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray
]


def _quad_mesh(quads: list[_Pt8]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """quads: je (p0..p3, uv0..uv3). UV folgt OBJ-Konvention (v nach oben)."""
    verts: list[np.ndarray] = []
    faces: list[list[int]] = []
    uvs: list[np.ndarray] = []
    offset = 0
    for p0, p1, p2, p3, uv0, uv1, uv2, uv3 in quads:
        verts.extend([p0, p1, p2, p3])
        uvs.extend([uv0, uv1, uv2, uv3])
        faces.append([offset, offset + 1, offset + 2])
        faces.append([offset, offset + 2, offset + 3])
        offset += 4
    v = np.array(verts, dtype=np.float64)
    f = np.array(faces, dtype=np.int64)
    uv_per_vertex = np.array(uvs, dtype=np.float32)
    return v, f, uv_per_vertex[f]


def _uv_xy(pt: np.ndarray) -> np.ndarray:
    u = (pt[0] - XMIN) / (XMAX - XMIN)
    v = (pt[1] - YMIN) / (YMAX - YMIN)
    return np.array([u, v], dtype=np.float32)


def _uv_xz(pt: np.ndarray) -> np.ndarray:
    u = (pt[0] - XMIN) / (XMAX - XMIN)
    v = (pt[2] - ZMIN) / (ZMAX - ZMIN)
    return np.array([u, v], dtype=np.float32)


def _uv_yz(pt: np.ndarray) -> np.ndarray:
    u = (pt[1] - YMIN) / (YMAX - YMIN)
    v = (pt[2] - ZMIN) / (ZMAX - ZMIN)
    return np.array([u, v], dtype=np.float32)


# Fix Round 1 (Review-Befund): ein achsenparalleler Quader mit drei verschiedenen
# Kantenlängen hat — auch außermittig platziert — trotzdem noch 4 echte Rotations-
# symmetrien (Einheit + 180 Grad um jede der 3 Flächennormalen-Achsen), solange die
# K6-Reparatursuche eine aus der Bounding-Box-Mitte abgeleitete Translation zulassen
# darf (Fix für "Offset ist reparierbar"): diese Translation gleicht die Verschiebung
# um den Boxmittelpunkt aus, wonach die reine Quaderform ihre 4-elementige Symmetrie
# wieder zeigt. Eine kleine "Fahne" an einer generischen Stelle der Deckfläche
# (abseits aller 3 Symmetrieachsen) bricht das endgültig — sie ist der höchste Punkt
# des ganzen Meshes (garantiert Kontrollvertex, siehe _control_vertices) und liegt
# unter keiner der 3 nichttrivialen Rotationen wieder auf sich selbst.
_FIN_X = XMIN + 0.15 * (XMAX - XMIN)
_FIN_Y = YMIN + 0.20 * (YMAX - YMIN)
_FIN_HEIGHT = 0.05


def _fin_triangle() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p0 = _p(_FIN_X, _FIN_Y, ZMAX)
    p1 = _p(_FIN_X + _FIN_HEIGHT, _FIN_Y, ZMAX)
    p2 = _p(_FIN_X, _FIN_Y, ZMAX + _FIN_HEIGHT)
    verts = np.array([p0, p1, p2])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    uv = np.array([_uv_xy(p0), _uv_xy(p1), _uv_xy(p2)], dtype=np.float32)[None, :, :]
    return verts, faces, uv


def _append_fin(
    v: np.ndarray, f: np.ndarray, uv: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fin_v, fin_f, fin_uv = _fin_triangle()
    return np.vstack([v, fin_v]), np.vstack([f, fin_f + len(v)]), np.vstack([uv, fin_uv])


def box_source_mesh_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ganzer Quader, 12 Dreiecke (6 Quads, ungeschnitten) + 1 Fahne auf der
    Deckfläche (Symmetriebruch, siehe oben) — die Quelle."""
    bottom = (
        _p(XMIN, YMIN, ZMIN), _p(XMAX, YMIN, ZMIN), _p(XMAX, YMAX, ZMIN), _p(XMIN, YMAX, ZMIN),
    )
    top = (
        _p(XMIN, YMIN, ZMAX), _p(XMAX, YMIN, ZMAX), _p(XMAX, YMAX, ZMAX), _p(XMIN, YMAX, ZMAX),
    )
    front = (
        _p(XMIN, YMIN, ZMIN), _p(XMAX, YMIN, ZMIN), _p(XMAX, YMIN, ZMAX), _p(XMIN, YMIN, ZMAX),
    )
    back = (
        _p(XMIN, YMAX, ZMIN), _p(XMAX, YMAX, ZMIN), _p(XMAX, YMAX, ZMAX), _p(XMIN, YMAX, ZMAX),
    )
    left = (
        _p(XMIN, YMIN, ZMIN), _p(XMIN, YMAX, ZMIN), _p(XMIN, YMAX, ZMAX), _p(XMIN, YMIN, ZMAX),
    )
    right = (
        _p(XMAX, YMIN, ZMIN), _p(XMAX, YMAX, ZMIN), _p(XMAX, YMAX, ZMAX), _p(XMAX, YMIN, ZMAX),
    )
    quads = [
        (*bottom, *[_uv_xy(p) for p in bottom]),
        (*top, *[_uv_xy(p) for p in top]),
        (*front, *[_uv_xz(p) for p in front]),
        (*back, *[_uv_xz(p) for p in back]),
        (*left, *[_uv_yz(p) for p in left]),
        (*right, *[_uv_yz(p) for p in right]),
    ]
    v, f, uv = _quad_mesh(quads)
    return _append_fin(v, f, uv)


def box_leaf_quads(half: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Eine Quaderhälfte (``"lower"``: z <= CUT_Z inkl. Bodenkappe, ``"upper"``: z >=
    CUT_Z inkl. Deckelkappe, PLUS die Fahne auf der Deckfläche — nur im oberen Blatt);
    die vier Seitenwände sind bei z = CUT_Z zerschnitten — beide Hälften
    zusammen ergeben exakt die Quelloberfläche, keine Innenfläche am Schnitt (kein
    Loch, keine erfundene Fläche)."""
    if half not in ("lower", "upper"):
        raise ValueError(half)
    z0, z1 = (ZMIN, CUT_Z) if half == "lower" else (CUT_Z, ZMAX)
    cap = (
        (_p(XMIN, YMIN, z0), _p(XMAX, YMIN, z0), _p(XMAX, YMAX, z0), _p(XMIN, YMAX, z0))
        if half == "lower"
        else (_p(XMIN, YMIN, z1), _p(XMAX, YMIN, z1), _p(XMAX, YMAX, z1), _p(XMIN, YMAX, z1))
    )
    front = (_p(XMIN, YMIN, z0), _p(XMAX, YMIN, z0), _p(XMAX, YMIN, z1), _p(XMIN, YMIN, z1))
    back = (_p(XMIN, YMAX, z0), _p(XMAX, YMAX, z0), _p(XMAX, YMAX, z1), _p(XMIN, YMAX, z1))
    left = (_p(XMIN, YMIN, z0), _p(XMIN, YMAX, z0), _p(XMIN, YMAX, z1), _p(XMIN, YMIN, z1))
    right = (_p(XMAX, YMIN, z0), _p(XMAX, YMAX, z0), _p(XMAX, YMAX, z1), _p(XMAX, YMIN, z1))
    quads = [
        (*cap, *[_uv_xy(p) for p in cap]),
        (*front, *[_uv_xz(p) for p in front]),
        (*back, *[_uv_xz(p) for p in back]),
        (*left, *[_uv_yz(p) for p in left]),
        (*right, *[_uv_yz(p) for p in right]),
    ]
    v, f, uv = _quad_mesh(quads)
    if half == "upper":
        v, f, uv = _append_fin(v, f, uv)
    return v, f, uv


def gradient_texture_bytes(size: int = 64) -> bytes:
    """R = u*255, G = v*255 (OBJ-Konvention v nach oben, Zeile 0 = v=1 = oben)."""
    u = np.linspace(0, 1, size, dtype=np.float64)
    v = np.linspace(1, 0, size, dtype=np.float64)  # Zeile 0 -> v=1 (oben)
    uu, vv = np.meshgrid(u, v)
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[..., 0] = np.round(uu * 255)
    arr[..., 1] = np.round(vv * 255)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def solid_texture_bytes(size: int, color: tuple[int, int, int] = (128, 128, 128)) -> bytes:
    """Einfarbige PNG-Textur — für K9-Größentests, bei denen nur ``texture_px``
    (die Pixelmaße) zählen, nicht der Bildinhalt; komprimiert winzig, deshalb auch
    für 8192² schnell."""
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 4)


def zup_to_yup(v: np.ndarray) -> np.ndarray:
    """Eigener, von ``tiles_contract`` unabhängiger Ausdruck: Z-oben -> glTF Y-oben,
    ``(x, y, z) -> (x, z, -y)`` (Umkehrung von ``(x, y, z) -> (x, -z, y)``)."""
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    return np.stack([x, z, -y], axis=-1)


def build_glb(
    vertices_zup: np.ndarray,
    faces: np.ndarray,
    uv_obj: np.ndarray | None = None,
    image_png: bytes | None = None,
    *,
    image_uri: str | None = None,
    write_as_zup_directly: bool = False,
    node_matrix: np.ndarray | None = None,
    node_translation: tuple[float, float, float] | None = None,
    node_rotation_xyzw: tuple[float, float, float, float] | None = None,
    node_scale: tuple[float, float, float] | None = None,
    wrap_in_parent: bool = False,
    quantized: bool = False,
) -> bytes:
    """Baut ein GLB aus Quader-Geometrie (Z oben) von Hand (keine Nutzung von
    ``tiles_contract``-Helfern). ``write_as_zup_directly=True`` bildet die
    ``axis_swapped``-Regressionsvorlage nach: Positionen werden OHNE die
    Y-oben-Konvention geschrieben (Bug in einem Werkzeug).

    Positionen und UV werden hier auf eindeutige Dreiecksecken aufgespreizt (jedes
    Dreieck bekommt drei eigene Vertex-Slots, keine geteilten Vertices zwischen
    Dreiecken) — Review-Fix: ``uv_obj`` kommt als (M, 3, 2) je Dreiecksecke herein,
    während ``vertices_zup``/``faces`` geteilte Vertices (N < 3M) referenzieren; ein
    TEXCOORD_0-Accessor über die (M, 3, 2)-Form hätte eine andere ``count`` als der
    POSITION-Accessor über den N geteilten Vertices — eine ungültige, aber von
    pygltflib klaglos geschriebene Kombination, die beim Rücklesen falsche UV-Werte an
    falsche Vertices haengt (führte zu Scheinfehlern in K12, siehe Report)."""
    tv = np.asarray(vertices_zup, dtype=np.float64)[np.asarray(faces, dtype=np.int64)]  # (M,3,3)
    expanded_zup = tv.reshape(-1, 3)  # (3M, 3), pro Dreiecksecke eigener Vertex
    expanded_yup = expanded_zup if write_as_zup_directly else zup_to_yup(expanded_zup)
    positions = expanded_yup.astype(np.float32)
    indices = np.arange(len(positions), dtype=np.uint32)
    if uv_obj is not None:
        # (3M, 2), passend zu positions (siehe Docstring oben).
        uv_obj = np.asarray(uv_obj, dtype=np.float32).reshape(-1, 2)

    chunks: list[bytes] = []
    buffer_views: list[g.BufferView] = []
    accessors: list[g.Accessor] = []

    def add_section(data: bytes, target: int | None) -> int:
        start = sum(len(c) for c in chunks)
        chunks.append(_pad4(data))
        bv = g.BufferView(buffer=0, byteOffset=start, byteLength=len(data), target=target)
        buffer_views.append(bv)
        return len(buffer_views) - 1

    if quantized:
        q = np.zeros((len(positions), 3), dtype=np.int16)
        bv = add_section(q.tobytes(), g.ARRAY_BUFFER)
        accessors.append(
            g.Accessor(
                bufferView=bv, componentType=5122, count=len(q), type=g.VEC3, normalized=True
            )
        )
    else:
        bv = add_section(positions.tobytes(), g.ARRAY_BUFFER)
        accessors.append(
            g.Accessor(
                bufferView=bv, componentType=g.FLOAT, count=len(positions), type=g.VEC3,
                min=positions.min(axis=0).tolist(), max=positions.max(axis=0).tolist(),
            )
        )
    pos_acc_idx = 0

    idx_bv = add_section(indices.tobytes(), g.ELEMENT_ARRAY_BUFFER)
    accessors.append(
        g.Accessor(
            bufferView=idx_bv, componentType=g.UNSIGNED_INT, count=len(indices), type=g.SCALAR
        )
    )
    idx_acc_idx = 1

    uv_acc_idx = None
    if uv_obj is not None:
        uv_gltf = uv_obj.reshape(-1, 2).astype(np.float32).copy()
        uv_gltf[:, 1] = 1.0 - uv_gltf[:, 1]
        uv_bv = add_section(uv_gltf.tobytes(), g.ARRAY_BUFFER)
        accessors.append(
            g.Accessor(bufferView=uv_bv, componentType=g.FLOAT, count=len(uv_gltf), type=g.VEC2)
        )
        uv_acc_idx = len(accessors) - 1

    materials: list[g.Material] = []
    textures: list[g.Texture] = []
    images: list[g.Image] = []
    material_idx = None
    if image_png is not None:
        img_bv = add_section(image_png, None)
        images.append(g.Image(bufferView=img_bv, mimeType="image/png"))
        textures.append(g.Texture(source=0))
        pbr = g.PbrMetallicRoughness(baseColorTexture=g.TextureInfo(index=0))
        materials.append(g.Material(pbrMetallicRoughness=pbr))
        material_idx = 0
    elif image_uri is not None:
        # Externe Datei-URI statt eingebettetem bufferView (Fix Round 1, Review-
        # Befund: der Prüfer unterstützte vorher nur eingebettete Texturen). Die
        # eigentliche Bilddatei muss der Aufrufer neben die tileset.json schreiben
        # (relativ zu deren Verzeichnis) — hier wird nur die Referenz gesetzt.
        images.append(g.Image(uri=image_uri))
        textures.append(g.Texture(source=0))
        pbr = g.PbrMetallicRoughness(baseColorTexture=g.TextureInfo(index=0))
        materials.append(g.Material(pbrMetallicRoughness=pbr))
        material_idx = 0

    attrs = g.Attributes(POSITION=pos_acc_idx, TEXCOORD_0=uv_acc_idx)
    prim = g.Primitive(attributes=attrs, indices=idx_acc_idx, material=material_idx)
    if quantized:
        prim.extensions = {"KHR_mesh_quantization": {}}
    mesh = g.Mesh(primitives=[prim])

    mesh_node = g.Node(
        mesh=0,
        matrix=node_matrix.flatten(order="F").tolist() if node_matrix is not None else None,
        translation=list(node_translation) if node_translation is not None else None,
        rotation=list(node_rotation_xyzw) if node_rotation_xyzw is not None else None,
        scale=list(node_scale) if node_scale is not None else None,
    )
    if wrap_in_parent:
        nodes = [g.Node(children=[1]), mesh_node]
    else:
        nodes = [mesh_node]

    gltf = g.GLTF2(
        asset=g.Asset(version="2.0"),
        scene=0,
        scenes=[g.Scene(nodes=[0])],
        nodes=nodes,
        meshes=[mesh],
        accessors=accessors,
        bufferViews=buffer_views,
        buffers=[g.Buffer(byteLength=sum(len(c) for c in chunks))],
        materials=materials,
        textures=textures,
        images=images,
        extensionsUsed=["KHR_mesh_quantization"] if quantized else [],
    )
    blob = b"".join(chunks)
    gltf.set_binary_blob(blob)
    return b"".join(gltf.save_to_bytes())


def build_b3dm(glb_bytes: bytes, rtc_center: tuple[float, float, float] | None) -> bytes:
    """b3dm-Header (28 Byte) + Feature-Table (JSON, RTC_CENTER inline als Array, falls
    gesetzt) + eingebettetes glb."""
    ft: dict = {}
    if rtc_center is not None:
        ft["RTC_CENTER"] = list(rtc_center)
    ft_json = json.dumps(ft).encode("utf-8")
    # JSON-Chunks werden mit Leerzeichen aufgefuellt, nicht NUL (sonst bricht json.loads).
    ft_json += b" " * ((-len(ft_json)) % 4)
    body = ft_json + glb_bytes
    byte_length = 28 + len(body)
    header = struct.pack("<4sIIIIII", b"b3dm", 1, byte_length, len(ft_json), 0, 0, 0)
    return header + body


def write_tileset(
    out_dir: Path,
    root_transform: list[float] | None,
    root_content_name: str,
    root_glb: bytes,
    root_geometric_error: float,
    children: list[dict],
) -> Path:
    """Schreibt ``tileset.json`` + Inhaltsdateien nach ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / root_content_name).write_bytes(root_glb)
    root: dict = {
        "geometricError": root_geometric_error,
        "content": {"uri": root_content_name},
        "children": [],
        "boundingVolume": {
            "box": [0, 0, 0, XMAX - XMIN, 0, 0, 0, YMAX - YMIN, 0, 0, 0, ZMAX - ZMIN],
        },
    }
    if root_transform is not None:
        root["transform"] = root_transform
    for child in children:
        (out_dir / child["uri"]).write_bytes(child["data"])
        node = {
            "geometricError": child.get("geometricError", 0.0),
            "content": {"uri": child["uri"]},
            "boundingVolume": root["boundingVolume"],
        }
        if child.get("transform") is not None:
            node["transform"] = child["transform"]
        root["children"].append(node)
    tileset = {
        "asset": {"version": "1.1"},
        "geometricError": root_geometric_error,
        "root": root,
    }
    path = out_dir / "tileset.json"
    path.write_text(json.dumps(tileset, indent=2))
    return path

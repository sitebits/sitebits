"""OBJ-Streaming-Leser: liest große Wavefront-OBJ-Dateien (Ziel: Testmesh
hq, 1 GB, 3,89 Mio. Vertices, 7,7 Mio. Dreiecke) zeilenweise in Blöcken (``LINES_PER_BLOCK``)
statt sie komplett in den Speicher zu laden, und liefert ein ``SourceMesh``.

Kein ``np.fromstring``/``np.loadtxt`` (Brief-Vorgabe: diese Pfade sind für große Textdateien
zu langsam bzw. arbeiten nicht zuverlässig zeilenweise) — statt dessen ``str.split()`` +
``float()``/``int()`` pro Zeile, Zwischenergebnisse in Python-Listen bis zu ``LINES_PER_BLOCK``
Zeilen, danach in NumPy-Arrays verdichtet (bounded memory: die Python-Listen werden nie
größer als ein Block, unabhängig von der Gesamtdateigröße).

**Materialien/Texturen** (Grundsatz „nie stillschweigend weiß"): genau ein referenzierter
Texturatlas wird erwartet.
- Ein Material zählt nur, wenn es irgendwo per ``usemtl <name>`` tatsächlich referenziert
  wird. Ein in der ``.mtl`` definiertes, aber nie per ``usemtl`` aufgerufenes Material (real
  beobachtet: ``newmtl Solid`` ohne ``map_Kd`` neben dem eigentlichen Textur-Material in
  <testmesh>[_hq].mtl) wird ignoriert — es löst NICHT den
  Mehrere-Texturen-Fehler aus, weil es geometrisch nie zum Zuge kommt.
- Referenzierte Materialien mit unterschiedlichen ``map_Kd``-Pfaden → ``ValueError`` mit der
  sortierten Liste der Texturpfade.
- Genau eine referenzierte Textur, Datei fehlt auf der Platte → ``FileNotFoundError`` mit dem
  Dateinamen.
- **Gemischt** (ein referenziertes Material MIT Textur, ein anderes referenziertes Material
  OHNE Textur) → ``ValueError`` mit beiden Listen. ``SourceMesh`` kennt nur eine globale
  Textur/UV für das ganze Mesh, keine pro Dreieck — eine einzelne Textur für alle Dreiecke zu
  setzen wäre für die texturlosen Dreiecke stillschweigend falsch. Alle drei echten
  Testdateien (Testmesh normal/hq, Testmesh) haben genau EIN ``usemtl`` im
  ganzen ``.obj`` (verifiziert), laufen also nie in diesen Fall.
- Kein referenziertes Material hat ``map_Kd`` (Material ohne Textur wird tatsächlich benutzt):
  - **Mit Vertexfarben** (mindestens eine ``v x y z r g b``-Zeile im ganzen File):
    **Vertexfarben-Fall** — ``SourceMesh.texture = None``, keine Ausnahme. ``SourceMesh``
    (fixe Schnittstelle) hat kein Farbfeld; die geparsten ``r g b``-Werte werden
    danach verworfen. Bewusste, dokumentierte Lücke: farbige, texturlose Meshes werden
    korrekt *erkannt* (kein falscher Fehler), ihre Farbe ist aber mit der aktuellen
    ``SourceMesh``-Schnittstelle nicht weitertransportierbar.
  - **Ohne Vertexfarben** (reine ``v x y z``-Zeilen, keine Textur) → ``ValueError`` mit dem
    Materialnamen — das ist genau der verbotene Fall ("nie stillschweigend
    weiß"): ohne Textur UND ohne Farbe gäbe es keine Information mehr, wie das Dreieck
    aussehen soll.

**UV-Zuordnung:** ``SourceMesh.uv`` ist ``(M, 3, 2)`` je Dreiecksecke. Während des
Parsens werden nur die aufgelösten (0-basierten) ``vt``-Indizes je Eckpunkt gesammelt, nicht
die Werte selbst — die eigentlichen UV-Werte werden am Ende in EINEM vektorisierten
NumPy-Gather (``vt_full[idx]``) aufgelöst, nicht 23 Mio. Mal einzeln in der Parser-Schleife.
Das setzt voraus, dass alle ``vt``-Zeilen bis zum Dateiende gelesen sind, bevor der Gather
läuft (immer der Fall, unabhängig von der Reihenfolge im OBJ) und ist deutlich schneller als
ein Lookup pro Eckpunkt während des Parsens. Fehlt AUCH NUR EINEM Dreieckseckpunkt im ganzen
Mesh der ``vt``-Index (z. B. ``f a//c`` oder reine ``f a b c``), wird ``uv`` insgesamt ``None``
(alles-oder-nichts, wie in ``tiles_contract._decode_gltf_geometry``).

**Normalen (``vn``)** werden gelesen (Zeilentyp erkannt, damit sie nicht als Fehler
durchfallen), ihre Werte aber verworfen — ``SourceMesh`` kennt keine Normalen, und die
Vertex-/Texturkoordinaten-Indexauflösung braucht sie nicht.

**Negative Indizes** (OBJ-Konvention: relativ zur Anzahl der bisher in der Datei gesehenen
Elemente) werden mit dem beim Erreichen der ``f``-Zeile aktuellen ``v_count``/``vt_count``
aufgelöst — das ist beim einzigen sequenziellen Durchlauf automatisch korrekt, unabhängig von
Blockgrenzen (Blockgrenzen verschieben nur, wann Werte von der Python-Liste in ein
NumPy-Array wandern, nicht die Zähler).
"""

from __future__ import annotations

import argparse
import resource
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from spike.source_mesh import SourceMesh

# Blockgröße für die Streaming-Verarbeitung (Brief-Vorschlag: "z. B. 1 Mio. Zeilen") — die
# Python-Zwischenlisten (v_block/face_v_block/…) werden nie größer, unabhängig von der
# Gesamtzeilenzahl der Datei; danach werden sie in ein NumPy-Array verdichtet und geleert.
LINES_PER_BLOCK = 1_000_000


class ReadStats(NamedTuple):
    """Rohzahlen aus der Datei (vor Quad-Triangulierung), für die CLI-Ausgabe."""

    v_count: int
    vt_count: int
    face_line_count: int  # Anzahl "f"-Zeilen, NICHT Dreiecke (ein Quad ist eine Zeile, 2 Dreiecke)


def _flush_block(block: list[Any], chunks: list[np.ndarray], dtype: np.dtype | type) -> None:
    """Verdichtet ``block`` (Python-Liste von Tupeln) in ein NumPy-Array, hängt es an
    ``chunks`` an und leert ``block`` in place (kein ``nonlocal`` nötig, ``list.clear()``
    mutiert dasselbe Objekt, auf das der Aufrufer noch zeigt)."""
    if block:
        chunks.append(np.asarray(block, dtype=dtype))
        block.clear()


def _concat_or_empty(
    chunks: list[np.ndarray], empty_shape: tuple[int, ...], dtype: np.dtype | type
) -> np.ndarray:
    if not chunks:
        return np.zeros(empty_shape, dtype=dtype)
    if len(chunks) == 1:
        return chunks[0]
    return np.concatenate(chunks, axis=0)


def _parse_mtl(path: Path) -> dict[str, Path | None]:
    """``newmtl``/``map_Kd`` aus einer ``.mtl``-Datei — Materialname -> aufgelöster
    Texturpfad (relativ zum Verzeichnis der ``.mtl``-Datei) oder ``None`` (kein ``map_Kd``,
    z. B. das reale ``Solid``-Material)."""
    materials: dict[str, Path | None] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split()
        if not parts or parts[0][0] == "#":
            continue
        key = parts[0]
        if key == "newmtl" and len(parts) > 1:
            current = parts[1]
            materials[current] = None
        elif key == "map_Kd" and current is not None and len(parts) > 1:
            # Letztes Token als Dateiname (map_Kd kennt optionale Schalter wie "-o"/"-s"
            # davor; das reicht für die real beobachteten und getesteten Dateien).
            materials[current] = path.parent / parts[-1]
    return materials


def _resolve_texture(
    materials: dict[str, Path | None],
    used_materials: set[str],
    *,
    have_vertex_colors: bool,
) -> Path | None:
    """Siehe Modul-Docstring "Materialien/Texturen". Nur per ``usemtl`` tatsächlich
    referenzierte Materialien zählen."""
    with_texture: dict[Path, list[str]] = {}
    without_texture: list[str] = []
    for name in sorted(used_materials):
        tex = materials.get(name)
        if tex is not None:
            with_texture.setdefault(tex, []).append(name)
        else:
            without_texture.append(name)

    if with_texture and without_texture:
        raise ValueError(
            "gemischte Materialien: mit Textur "
            + ", ".join(
                f"{name}->{path}"
                for path in sorted(with_texture, key=str)
                for name in with_texture[path]
            )
            + " und ohne Textur "
            + ", ".join(sorted(without_texture))
            + " — SourceMesh kennt nur eine globale Textur für das ganze Mesh ("
            "genau ein Texturatlas erwartet)."
        )
    if len(with_texture) > 1:
        raise ValueError(
            "mehrere Materialien mit unterschiedlichen Texturen (genau ein Texturatlas "
            "erwartet): " + ", ".join(str(p) for p in sorted(with_texture, key=str))
        )
    if with_texture:
        (tex_path,) = with_texture.keys()
        if not tex_path.exists():
            raise FileNotFoundError(f"Textur nicht gefunden: {tex_path}")
        return tex_path
    if without_texture and not have_vertex_colors:
        raise ValueError(
            "referenziertes Material ohne Textur und ohne Vertexfarben (nie "
            "stillschweigend weiß): " + ", ".join(sorted(without_texture))
        )
    return None  # Vertexfarben-Fall (siehe Modul-Docstring) oder keine Materialien referenziert.


def _read_obj_impl(
    path: Path, *, unit_scale: float = 1.0, origin: np.ndarray | None = None
) -> tuple[SourceMesh, ReadStats]:
    origin_vec = (
        np.zeros(3, dtype=np.float64) if origin is None else np.asarray(origin, dtype=np.float64)
    )
    if origin_vec.shape != (3,):
        raise ValueError(f"origin muss Form (3,) haben, bekommen: {origin_vec.shape}")

    obj_dir = path.parent
    materials: dict[str, Path | None] = {}
    used_materials: set[str] = set()
    have_vertex_colors = False

    v_block: list[tuple[float, float, float]] = []
    v_chunks: list[np.ndarray] = []
    v_count = 0

    vt_block: list[tuple[float, float]] = []
    vt_chunks: list[np.ndarray] = []
    vt_count = 0

    face_v_block: list[tuple[int, int, int]] = []
    face_v_chunks: list[np.ndarray] = []

    face_vt_block: list[tuple[int, int, int]] = []
    face_vt_chunks: list[np.ndarray] = []
    face_uv_missing = False

    face_line_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            parts = raw_line.split()
            if not parts or parts[0][0] == "#":
                continue
            key = parts[0]

            if key == "v":
                v_block.append((float(parts[1]), float(parts[2]), float(parts[3])))
                v_count += 1
                if len(parts) >= 7:  # "v x y z r g b"
                    have_vertex_colors = True
            elif key == "vt":
                vt_block.append((float(parts[1]), float(parts[2])))
                vt_count += 1
            elif key == "vn":
                pass  # Normalen werden nicht gespeichert, siehe Modul-Docstring.
            elif key == "f":
                tokens = parts[1:]
                n = len(tokens)
                if n < 3:
                    raise ValueError(
                        f"Ungültige Face-Zeile (weniger als 3 Eckpunkte): {raw_line!r}"
                    )
                corners_v = [0] * n
                corners_vt: list[int | None] = [None] * n
                for i, tok in enumerate(tokens):
                    fields = tok.split("/")
                    vi_raw = int(fields[0])
                    corners_v[i] = vi_raw - 1 if vi_raw > 0 else v_count + vi_raw
                    if len(fields) > 1 and fields[1]:
                        vti_raw = int(fields[1])
                        corners_vt[i] = vti_raw - 1 if vti_raw > 0 else vt_count + vti_raw
                # Fan-Triangulierung: Dreieck bleibt 1 Dreieck, Quad -> 2, n-Eck allgemein.
                for i in range(1, n - 1):
                    face_v_block.append((corners_v[0], corners_v[i], corners_v[i + 1]))
                    if not face_uv_missing:
                        a, b, c = corners_vt[0], corners_vt[i], corners_vt[i + 1]
                        if a is None or b is None or c is None:
                            face_uv_missing = True
                            face_vt_block.clear()
                            face_vt_chunks.clear()
                        else:
                            face_vt_block.append((a, b, c))
                face_line_count += 1
            elif key == "mtllib":
                for name in parts[1:]:
                    mtl_path = obj_dir / name
                    if not mtl_path.exists():
                        raise FileNotFoundError(f"mtllib nicht gefunden: {mtl_path}")
                    materials.update(_parse_mtl(mtl_path))
            elif key == "usemtl" and len(parts) > 1:
                used_materials.add(parts[1])
            # sonstige Keys (o, g, s, vp, ...) werden ignoriert.

            if line_no % LINES_PER_BLOCK == 0:
                _flush_block(v_block, v_chunks, np.float64)
                _flush_block(vt_block, vt_chunks, np.float64)
                _flush_block(face_v_block, face_v_chunks, np.int64)
                if not face_uv_missing:
                    _flush_block(face_vt_block, face_vt_chunks, np.int64)

    _flush_block(v_block, v_chunks, np.float64)
    _flush_block(vt_block, vt_chunks, np.float64)
    _flush_block(face_v_block, face_v_chunks, np.int64)
    if not face_uv_missing:
        _flush_block(face_vt_block, face_vt_chunks, np.int64)

    vertices_raw = _concat_or_empty(v_chunks, (0, 3), np.float64)
    vertices = vertices_raw * unit_scale - origin_vec
    faces = _concat_or_empty(face_v_chunks, (0, 3), np.int64)

    uv: np.ndarray | None = None
    if not face_uv_missing and len(faces):
        vt_full = _concat_or_empty(vt_chunks, (0, 2), np.float64)
        face_vt_idx = _concat_or_empty(face_vt_chunks, (0, 3), np.int64)
        uv = vt_full[face_vt_idx].astype(np.float32)  # (M, 3, 2), ein vektorisierter Gather.

    texture = _resolve_texture(materials, used_materials, have_vertex_colors=have_vertex_colors)
    mesh = SourceMesh(vertices=vertices, faces=faces, uv=uv, texture=texture)
    stats = ReadStats(v_count=v_count, vt_count=vt_count, face_line_count=face_line_count)
    return mesh, stats


def read_obj(
    path: Path, *, unit_scale: float = 1.0, origin: np.ndarray | None = None
) -> SourceMesh:
    """Liest eine ``.obj``-Datei streaming (siehe Modul-Docstring) und liefert ein
    ``SourceMesh`` mit ``pLocal = p * unit_scale - origin``."""
    mesh, _stats = _read_obj_impl(path, unit_scale=unit_scale, origin=origin)
    return mesh


def cache_npz(mesh: SourceMesh, path: Path) -> None:
    """Schreibt ``mesh`` unkomprimiert (``np.savez`` statt ``savez_compressed`` — bei
    Meshes in Testmesh-hq-Größe kostet Kompression deutlich CPU-Zeit für wenig
    Platzersparnis, hier nicht ausschlaggebend) nach ``path``."""
    texture_arr = np.asarray(str(mesh.texture) if mesh.texture is not None else "")
    if mesh.uv is not None:
        np.savez(path, vertices=mesh.vertices, faces=mesh.faces, texture=texture_arr, uv=mesh.uv)
    else:
        np.savez(path, vertices=mesh.vertices, faces=mesh.faces, texture=texture_arr)


def load_npz(path: Path) -> SourceMesh:
    with np.load(path, allow_pickle=False) as data:
        vertices = np.asarray(data["vertices"])
        faces = np.asarray(data["faces"])
        uv = np.asarray(data["uv"]) if "uv" in data.files else None
        tex_str = str(data["texture"].item()) if "texture" in data.files else ""
    texture = Path(tex_str) if tex_str else None
    return SourceMesh(vertices=vertices, faces=faces, uv=uv, texture=texture)


def _parse_origin(text: str) -> np.ndarray:
    values = [float(part) for part in text.split(",")]
    if len(values) != 3:
        raise ValueError(f"--origin braucht X,Y,Z (3 Werte), bekommen: {text!r}")
    return np.asarray(values, dtype=np.float64)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spike.obj_read")
    parser.add_argument("obj", type=Path)
    parser.add_argument("--origin", type=str, default=None, help="X,Y,Z (wird abgezogen)")
    parser.add_argument("--unit-scale", type=float, default=1.0)
    parser.add_argument("--out", type=Path, required=True, help="Ziel-.npz")
    args = parser.parse_args(argv)

    origin = _parse_origin(args.origin) if args.origin else None

    start = time.monotonic()
    mesh, stats = _read_obj_impl(args.obj, unit_scale=args.unit_scale, origin=origin)
    read_elapsed = time.monotonic() - start

    cache_npz(mesh, args.out)
    total_elapsed = time.monotonic() - start

    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    n_tri = len(mesh.faces)
    area = mesh.area()
    bounds_min = mesh.vertices.min(axis=0) if len(mesh.vertices) else None
    bounds_max = mesh.vertices.max(axis=0) if len(mesh.vertices) else None

    print(f"Datei: {args.obj}")
    print(
        f"v={stats.v_count} vt={stats.vt_count} f={stats.face_line_count} "
        f"(Dreiecke nach Triangulierung={n_tri})"
    )
    print(f"Laufzeit Lesen: {read_elapsed:.1f} s, inkl. Cache schreiben: {total_elapsed:.1f} s")
    print(f"Peak-RSS: {peak_rss_gib:.3f} GiB")
    print(f"Fläche: {area:.6f} m²")
    if bounds_min is not None and bounds_max is not None:
        print(f"Grenzen min: {bounds_min}, max: {bounds_max}")
    print(f"Textur: {mesh.texture}")
    print(f"UV vorhanden: {mesh.uv is not None}")
    print(f"npz: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

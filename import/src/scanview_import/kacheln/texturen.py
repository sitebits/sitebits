"""Materialgetrennte UV-Inseln, verlustfreie Atlaspackung und begrenzter Bildcache."""

from __future__ import annotations

import io
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..mesh_source import Mesh
from ..mesh_stages import compact

POLSTER = 4
SEITE = 4096 - 2 * POLSTER


def oeffnen(path: Path) -> Image.Image:
    previous = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 16384**2
        image = Image.open(path)
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    if max(image.size) > 16384 or image.format not in ("PNG", "JPEG"):
        image.close()
        raise ValueError("Quelltextur benötigt PNG/JPEG mit höchstens 16384 Pixeln je Achse")
    return image


def _polygon(poly: np.ndarray, axis: int, edge: float, lower: bool) -> np.ndarray:
    """Sutherland-Hodgman; alle Attribute werden mit demselben Faktor interpoliert."""
    result = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        inside_a = a[axis] >= edge if lower else a[axis] <= edge
        inside_b = b[axis] >= edge if lower else b[axis] <= edge
        if inside_a:
            result.append(a)
        if inside_a != inside_b:
            t = (edge - a[axis]) / (b[axis] - a[axis])
            p = a + t * (b - a)
            p[axis] = edge
            result.append(p)
    return np.asarray(result).reshape(-1, poly.shape[1])


def _seite(mesh: Mesh, low: np.ndarray, high: np.ndarray, final: np.ndarray) -> Mesh | None:
    """Nur übergroße UV-Bereiche schneiden; kleine Ausschnitte behalten ihre Dreiecke."""
    if mesh.uv is None:
        raise ValueError("Textur ohne UV")
    uvtri = mesh.uv[mesh.triangles]
    hit = np.all(uvtri.max(axis=1) >= low, axis=1) & np.all(uvtri.min(axis=1) <= high, axis=1)
    # Bei flächenhaften Dreiecken mit konstantem UV auf einer Seitengrenze
    # entscheidet eine halboffene Zuordnung; sonst würde die Fläche dupliziert.
    hit &= ~np.any((uvtri.min(axis=1) == high) & (uvtri.max(axis=1) == high) & ~final, axis=1)
    selected = mesh.triangles[hit]
    if not len(selected):
        return None
    values = np.column_stack([mesh.uv, mesh.positions])
    if mesh.colors is not None:
        values = np.column_stack([values, mesh.colors])
    vertices: list[np.ndarray] = []
    faces = []
    for face in selected:
        poly = values[face]
        for axis in range(2):
            poly = _polygon(poly, axis, low[axis], True)
            poly = _polygon(poly, axis, high[axis], False)
        for i in range(1, len(poly) - 1):
            tri = poly[[0, i, i + 1]]
            # Berührungen einer Seitengrenze haben keine Fläche und gehören keiner Seite.
            if (
                np.linalg.norm(np.cross(tri[1, 2:5] - tri[0, 2:5], tri[2, 2:5] - tri[0, 2:5]))
                <= 1e-20
            ):
                continue
            base = len(vertices)
            vertices.extend(tri)
            faces.append([base, base + 1, base + 2])
    if not faces:
        return None
    data = np.asarray(vertices)
    return replace(
        mesh,
        positions=data[:, 2:5],
        uv=data[:, :2],
        colors=data[:, 5:] if mesh.colors is not None else None,
        triangles=np.asarray(faces, dtype=np.uint32),
        materials=np.full(len(faces), mesh.materials[0], dtype=np.int32),
    )


def _ausschnitt(image: Image.Image, low: np.ndarray, high: np.ndarray) -> Image.Image:
    """Randstücke modular zusammensetzen, ohne eine zweite volle Atlas-Kopie."""
    result = Image.new("RGB", (int(high[0] - low[0]), int(high[1] - low[1])))
    y = int(low[1])
    while y < high[1]:
        sy = y % image.height
        h = min(image.height - sy, int(high[1]) - y)
        x = int(low[0])
        while x < high[0]:
            sx = x % image.width
            w = min(image.width - sx, int(high[0]) - x)
            result.paste(
                image.crop((sx, sy, sx + w, sy + h)).convert("RGB"),
                (x - int(low[0]), y - int(low[1])),
            )
            x += w
        y += h
    return result


class BildCache:
    """LRU nach dekodierten RGB-Bytes; ein aktives Bild darf die Grenze übersteigen.

    Null deaktiviert das Behalten. Dateiköpfe bleiben ohne dekodierte Pixel gespeichert.
    Der Aufrufer besitzt den Cache und schließt ihn nach dem gesamten Kachellauf.
    """

    def __init__(self, grenze: int = 4 * 1024**3) -> None:
        if grenze < 0:
            raise ValueError("Texturcache muss mindestens 0 Bytes haben")
        self.grenze = grenze
        self.bytes = 0
        self.bilder: OrderedDict[Path | tuple[Path, float], Image.Image] = OrderedDict()
        self.groessen: dict[Path, tuple[int, int]] = {}

    def groesse(self, path: Path) -> tuple[int, int]:
        if path not in self.groessen:
            with oeffnen(path) as image:
                self.groessen[path] = image.size
        return self.groessen[path]

    def _platz(self, size: int) -> None:
        while self.bilder and self.bytes + size > self.grenze:
            _, old = self.bilder.popitem(last=False)
            self.bytes -= old.width * old.height * 3
            old.close()

    @contextmanager
    def bild(self, path: Path, faktor: float = 1) -> Iterator[Image.Image]:
        key = path if faktor == 1 else (path, faktor)
        image = self.bilder.pop(key, None)
        if image is not None:
            self.bytes -= image.width * image.height * 3
        elif faktor == 1:
            self._platz(int(np.prod(self.groesse(path))) * 3)
            with oeffnen(path) as source:
                image = source.convert("RGB")
        else:
            # Auch Vorfilterstufen teilen sich das Bytebudget des LRU. Kein riesiges
            # periodisches Zwischenbild bei einem geometricError von vielen Metern.
            with self.bild(path) as source:
                size = [max(1, int(np.ceil(v * faktor))) for v in source.size]
                image = source.resize(size, Image.Resampling.LANCZOS)
        try:
            yield image
        finally:
            size_bytes = image.width * image.height * 3
            self._platz(size_bytes)
            if size_bytes <= self.grenze:
                self.bilder[key] = image
                self.bytes += size_bytes
            else:
                image.close()

    def schliessen(self) -> None:
        for image in self.bilder.values():
            image.close()
        self.bilder.clear()
        self.bytes = 0


@dataclass
class Insel:
    mesh: Mesh
    quelle: Path
    groesse: np.ndarray
    low: np.ndarray
    high: np.ndarray
    faktor: float = 1.0
    polster: int = POLSTER

    @property
    def kanten(self) -> tuple[int, int]:
        size = np.ceil((self.high - self.low + 2 * self.polster) * self.faktor)
        return int(size[0]), int(size[1])


def _inseln(mesh: Mesh) -> list[np.ndarray]:
    """Vertexgraph statt Python-Schleife je Dreieck; UV-Nähte bleiben getrennt.

    Identische Position/UV-Paare verbinden auch duplizierte Grafikvertices. Gleiche UV
    allein verbinden räumlich getrennte Oberflächen nicht versehentlich miteinander.
    Der Aufrufer trennt die Materialien vor diesem Schritt.
    """
    if mesh.uv is None:
        raise ValueError("Textur ohne UV")
    _, inverse = np.unique(np.column_stack([mesh.positions, mesh.uv]), axis=0, return_inverse=True)
    tri = inverse[mesh.triangles]
    rows = np.repeat(tri[:, 0], 2)
    cols = tri[:, 1:].ravel()
    graph = coo_matrix(
        (np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(int(inverse.max()) + 1,) * 2
    ).tocsr()
    _, labels = connected_components(graph, directed=False)
    face_labels = labels[tri[:, 0]]
    order = np.argsort(face_labels, kind="stable")
    return list(np.split(order, np.flatnonzero(np.diff(face_labels[order])) + 1))


def _dichte(mesh: Mesh, size: np.ndarray) -> float:
    if mesh.uv is None:
        raise ValueError("Textur ohne UV")
    # Flächengewichtet: sqrt(Texelfläche / Weltfläche). Ein Maximum über Kanten ließ
    # Splitter (Kante µm, UV ein Texel) die Dichte der ganzen Kachel auf Millionen
    # Texel/m treiben und verkleinerte ihre Textur dadurch auf wenige Pixel.
    faces = mesh.positions[mesh.triangles]
    uv = mesh.uv[mesh.triangles] * size
    area = np.linalg.norm(np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]), axis=1)
    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    texels = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    if area.sum() <= 0:
        return 0.0
    return float(np.sqrt(texels.sum() / area.sum()))


def vorbereiten(mesh: Mesh, cache: BildCache, error: float = 0) -> tuple[list[Insel], list[Mesh]]:
    """Innere Texturen auf acht Texel je geometrischer Fehlerlänge begrenzen.

    Bei Sichtweite d und Projektionsfaktor f ist SSE = error*f/d. An der
    Verfeinerungsschwelle errorTarget gilt f/d = errorTarget/error Pixel/m.
    Ein Texel je Bildpixel verlangt daher Texel/m = errorTarget/error. Wir
    verwenden den Desktop-Zielwert 8; bei Touch mit Zielwert 16 entsprechen dem
    bis zu zwei Bildpixel je Texel. Quelldichte und 4096-Seitengrenze bleiben
    Obergrenzen, Blätter behalten ihre Quelltexel.
    """
    islands = []
    plain = []
    # Ein Faktor für die gesamte Kachel, unabhängig von Quellseite und Packreihenfolge.
    density = 0.0
    if error > 0:
        for material in np.unique(mesh.materials):
            texture = mesh.textures[int(material)]
            if isinstance(texture, Path):
                part = compact(
                    mesh,
                    mesh.triangles[mesh.materials == material],
                    mesh.materials[mesh.materials == material],
                )
                density = max(density, _dichte(part, np.asarray(cache.groesse(texture))))
    factor = min(1.0, 8 / max(error * density, 8.0))
    for material in np.unique(mesh.materials):
        part = compact(
            mesh,
            mesh.triangles[mesh.materials == material],
            mesh.materials[mesh.materials == material],
        )
        texture = mesh.textures[int(material)]
        if texture is None:
            plain.append(part)
            continue
        if not isinstance(texture, Path) or part.uv is None:
            raise ValueError("OBJ benötigt lokale Texturdateien und UV")
        size = np.asarray(cache.groesse(texture))
        for selected in _inseln(part):
            island = compact(part, part.triangles[selected], part.materials[selected])
            assert island.uv is not None
            lo = np.floor((island.uv * size).min(axis=0)).astype(int)
            hi = np.maximum(np.ceil((island.uv * size).max(axis=0)).astype(int), lo + 1)
            if np.any(hi - lo > 16384):
                raise ValueError("Wiederholter UV-Bereich überschreitet 16384 Texel je Achse")
            for y in range(int(lo[1]), int(hi[1]), SEITE):
                for x in range(int(lo[0]), int(hi[0]), SEITE):
                    start = np.array([x, y])
                    end = np.minimum(start + SEITE, hi)
                    page = (
                        island
                        if np.all(hi - lo <= SEITE)
                        else _seite(island, start / size, end / size, end == hi)
                    )
                    if page is not None:
                        # Ein gemeinsamer Maßstab; zusätzliche echte Nachbarn sichern
                        # nach der Skalierung mindestens zwei Texel Abstand.
                        pad = max(POLSTER, int(np.ceil(2 / factor)))
                        islands.append(Insel(page, texture, size, start, end, factor, pad))
    return islands, plain


def _shelf(rects: list[tuple[int, int]], width: int, height: int) -> list[tuple[int, int]] | None:
    x = y = row = 0
    positions = []
    for w, h in rects:
        if w > width or h > height:
            return None
        if x + w > width:
            x = 0
            y += row
            row = 0
        if y + h > height:
            return None
        positions.append((x, y))
        x += w
        row = max(row, h)
    return positions


def packplan(islands: list[Insel], maximum: int = 4096) -> list[dict[str, Any]]:
    """Kleinste vom höhensortierten Shelf-Packer belegbare Zweierpotenzfläche."""
    pending = sorted(
        range(len(islands)), key=lambda i: (-islands[i].kanten[1], -islands[i].kanten[0], i)
    )
    pages = []
    edges = [2**i for i in range(max(4, maximum).bit_length()) if 4 <= 2**i <= max(4, maximum)]
    candidates = sorted(
        ((w, h) for w in edges for h in edges), key=lambda wh: (wh[0] * wh[1], max(wh), wh[0])
    )
    while pending:
        rects = [islands[i].kanten for i in pending]
        area = sum(a * b for a, b in rects)
        for w, h in candidates:
            if area > w * h:
                continue
            positions = _shelf(rects, w, h)
            if positions is not None:
                pages.append({"size": (w, h), "items": list(zip(pending, positions, strict=True))})
                return pages
        # Mehrere Seiten: jeweils eine maximal füllen, dann die letzte Seite minimieren.
        w = h = edges[-1]
        chosen: list[int] = []
        positions = []
        x = y = row = 0
        for i in pending:
            rw, rh = islands[i].kanten
            nx, ny, nr = x, y, row
            if nx + rw > w:
                nx, ny, nr = 0, ny + nr, 0
            if rw <= w and ny + rh <= h:
                chosen.append(i)
                positions.append((nx, ny))
                x, y, row = nx + rw, ny, max(nr, rh)
        if not chosen:
            # Kleine Blattbudgets können eine einzelne Insel nicht aufnehmen. Diese
            # bleibt vollaufgelöst und wird auf der kleinsten passenden 4096-Seite abgelegt.
            if maximum < 4096:
                return packplan(islands, 4096)
            raise ValueError("UV-Insel passt nicht auf eine 4096-Texturseite")
        # Auch eine frühe Seite kann nur einen hohen, schmalen Streifen enthalten.
        # Ihre tatsächlich gewählte Teilmenge deshalb ebenfalls minimal einpassen.
        chosen_rects = [islands[i].kanten for i in chosen]
        chosen_area = sum(a * b for a, b in chosen_rects)
        for cw, ch in candidates:
            if chosen_area > cw * ch:
                continue
            compact_positions = _shelf(chosen_rects, cw, ch)
            if compact_positions is not None:
                w, h, positions = cw, ch, compact_positions
                break
        pages.append({"size": (w, h), "items": list(zip(chosen, positions, strict=True))})
        selected = set(chosen)
        pending = [i for i in pending if i not in selected]
    return pages


def kennzahlen(islands: list[Insel], pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "islands": len(islands),
        "texturePages": [
            {
                "size": list(page["size"]),
                "fill": sum(int(np.prod(islands[i].kanten)) for i, _ in page["items"])
                / int(np.prod(page["size"])),
                "contentFill": sum(
                    float(np.prod((islands[i].high - islands[i].low) * islands[i].faktor))
                    for i, _ in page["items"]
                )
                / int(np.prod(page["size"])),
            }
            for page in pages
        ],
    }


def _verbinden(mesh: Mesh, parts: list[Mesh], encoded: dict[int, bytes]) -> Mesh:
    offsets = np.cumsum([0] + [len(p.positions) for p in parts[:-1]])
    return replace(
        mesh,
        positions=np.concatenate([p.positions for p in parts]),
        triangles=np.concatenate([p.triangles + o for p, o in zip(parts, offsets, strict=True)]),
        materials=np.concatenate([p.materials for p in parts]),
        uv=np.concatenate([p.uv for p in parts]) if mesh.uv is not None else None,
        colors=np.concatenate([p.colors for p in parts]) if mesh.colors is not None else None,
        textures=[encoded.get(i) for i in range(max(int(p.materials.max()) for p in parts) + 1)],
    )


def zuschneiden(
    mesh: Mesh,
    error: float = 0,
    *,
    cache: BildCache | None = None,
    maximum: int = 4096,
    messung: dict[str, Any] | None = None,
    plan: tuple[list[Insel], list[Mesh]] | None = None,
) -> tuple[Mesh, dict[int, bytes]]:
    """Blätter ohne Resampling packen; innere Inseln gemeinsam maßstäblich verkleinern."""
    owned = cache is None
    cache = cache if cache is not None else BildCache()
    try:
        islands, plain = plan if plan is not None else vorbereiten(mesh, cache, error)
        pages = packplan(islands, maximum)
        parts = []
        encoded = {}
        for material, page in enumerate(pages):
            canvas = Image.new("RGB", page["size"])
            for i, (x, y) in page["items"]:
                island = islands[i]
                if island.faktor == 1:
                    with cache.bild(island.quelle) as source:
                        crop = _ausschnitt(
                            source, island.low - island.polster, island.high + island.polster
                        )
                else:
                    # Periodische Lanczos-Vorfilterung vor dem Ausschnitt hält den
                    # Arbeitsspeicher auch bei winzigen gemeinsamen LOD-Faktoren begrenzt.
                    with cache.bild(island.quelle, island.faktor) as source:
                        scale = np.asarray(source.size) / island.groesse
                        low = (island.low - island.polster) * scale
                        high = (island.high + island.polster) * scale
                        # Pixelzentren periodisch abtasten, in kleinen Zeilenblöcken.
                        # Ist eine Vorfilterachse schon 1 px breit, bleibt auch ein
                        # beliebig weit reichendes Nachbarpolster speicherbeschränkt.
                        w, h = island.kanten
                        pixels = np.asarray(source)
                        target = np.empty((h, w, 3), dtype=np.uint8)
                        xs = low[0] + (np.arange(w) + 0.5) * (high[0] - low[0]) / w - 0.5
                        for row in range(0, h, 64):
                            stop = min(row + 64, h)
                            ys = (
                                low[1] + (np.arange(row, stop) + 0.5) * (high[1] - low[1]) / h - 0.5
                            )
                            xx, yy = np.meshgrid(xs, ys)
                            for channel in range(3):
                                target[row:stop, :, channel] = map_coordinates(
                                    pixels[:, :, channel],
                                    [yy, xx],
                                    order=1,
                                    mode="grid-wrap",
                                    prefilter=False,
                                )
                        crop = Image.fromarray(target)
                canvas.paste(crop, (x, y))
                crop.close()
                if island.mesh.uv is None:
                    raise ValueError("Texturseite ohne UV")
                # resize richtet Pixelkanten aus; tatsächliche Rundung je Achse verwenden.
                scale = np.asarray(island.kanten) / (island.high - island.low + 2 * island.polster)
                uv = (
                    (island.mesh.uv * island.groesse - island.low + island.polster) * scale + [x, y]
                ) / np.asarray(canvas.size)
                parts.append(
                    replace(
                        island.mesh, uv=uv, materials=np.full(len(island.mesh.triangles), material)
                    )
                )
            data = io.BytesIO()
            canvas.save(data, format="PNG")
            canvas.close()
            encoded[material] = data.getvalue()
        for material, part in enumerate(plain, start=len(pages)):
            parts.append(replace(part, materials=np.full(len(part.triangles), material)))
        if messung is not None:
            messung.update(kennzahlen(islands, pages))
            messung["textureTexelsPerM"] = max(
                (_dichte(island.mesh, island.groesse) * island.faktor for island in islands),
                default=0.0,
            )
        return _verbinden(mesh, parts, encoded), encoded
    finally:
        if owned:
            cache.schliessen()

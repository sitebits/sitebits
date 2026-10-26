"""Geometriekachelung in Projektkoordinaten mit lokalen Texturseiten."""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import meshoptimizer
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from ..mesh_source import Mesh, read_mesh
from ..mesh_stages import compact
from .backen import backen
from .glb import rundung, schreiben
from .parallel import Auftraege, Nachvereinfachung, texturauftrag
from .projektion import _ObereDistanz
from .texturen import BildCache, Insel, packplan, vorbereiten, zuschneiden

_SLOPPY = -1  # Kennung der Clustering-Stufe, keine meshoptimizer-Option


def _komponenten(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Geometrische Zusammenhangskomponenten, unabhängig von UV-/Materialnähten."""
    positions, inverse = np.unique(mesh.positions, axis=0, return_inverse=True)
    faces = inverse[mesh.triangles]
    graph = coo_matrix(
        (np.ones(3 * len(faces), dtype=bool),
         (faces.ravel(), np.roll(faces, -1, axis=1).ravel())),
        shape=(len(positions), len(positions)),
    ).tocsr()
    count, labels = connected_components(graph, directed=False)
    low = np.full((count, 3), np.inf)
    high = np.full((count, 3), -np.inf)
    np.minimum.at(low, labels, positions)
    np.maximum.at(high, labels, positions)
    return labels[faces[:, 0]], np.linalg.norm(high - low, axis=1)


def _vereinfachen(
    mesh: Mesh, budget: int, maximum: int | None = None, *,
    messung: dict[str, Any] | None = None,
) -> tuple[Mesh, float]:
    """Grobe Flächen statt ganzer verschwundener Bauteile, bei hartem Budget.

    PRUNE erhält nur Diagonale/512 als Löschfehlertoleranz. Reicht das nicht,
    darf die Stufe relax ohne PRUNE größere Flächenfehler machen. Sloppy kann
    trotzdem Komponenten verlieren; auch lock kann geschlossene Körper löschen.
    Alle geometrischen Komponenten größer als Diagonale/512 werden deshalb
    nachgeprüft (UV-Nähte zählen nicht als Trennung).
    Bei Verlust erhalten diese Komponenten getrennte Budgets mit mindestens einem
    Dreieck; der Rest wird nach Dreieckszahl verteilt. Kann ein Bauteil selbst das
    nicht erreichen, bleibt sein größtes Quelldreieck als gröbste Darstellung.
    Die zweiseitige Flächenschranke erfasst deren tatsächlichen Flächenverlust.
    Mehr geschützte Komponenten als Budgetplätze sind unmöglich und führen zum
    Fehler statt zu stillen Verlusten oder einer Budgetüberschreitung.
    """
    limit = maximum if maximum is not None else budget
    if budget < 1 or limit < 1:
        raise ValueError("Dreiecksbudget nicht erreichbar")
    stats: dict[str, Any] = {}
    try:
        result, error = _vereinfachen_kandidat(mesh, budget, limit, messung=stats)
    except ValueError:
        # Auch ein vollständig leerer Kandidat muss den Komponentenschutz durchlaufen.
        result, error = None, 0.0
    labels, sizes = _komponenten(mesh)
    threshold = max(float(np.linalg.norm(np.ptp(mesh.positions, axis=0))), 1e-12) / 512
    protected = np.flatnonzero(sizes > threshold)
    # Alle Stufen wählen unveränderte Quellvertices; keine Koordinatentoleranz nötig.
    vertex_labels = np.full(len(mesh.positions), -1, dtype=int)
    vertex_labels[mesh.triangles.ravel()] = np.repeat(labels, 3)
    present = np.empty(0, dtype=int)
    if result is not None:
        _, nearest = cKDTree(mesh.positions).query(result.positions)
        face_labels = vertex_labels[nearest][result.triangles]
        # Clustering kann nahe Komponenten verbinden. Ein einzelner Vertex in einem
        # fremden Dreieck ist noch keine dargestellte Fläche dieses Bauteils.
        own = np.all(face_labels == face_labels[:, :1], axis=1)
        present = np.unique(face_labels[own, 0])
    stats.update(componentThresholdM=threshold, protectedComponents=len(protected))
    if result is not None and np.all(np.isin(protected, present)):
        if messung is not None:
            messung.update(stats)
        return result, error
    if len(protected) > limit:
        raise ValueError(f"Dreiecksbudget {limit} für {len(protected)} Bauteile nicht erreichbar")
    # Zusammenhängende Bauteile bevorzugt auf ihrer Fläche vergröbern. Kleine Splitter
    # teilen sich eine optionale Restgruppe; sie beanspruchen keinen Mindestplatz.
    order = np.argsort(labels, kind="stable")
    boundaries = np.searchsorted(labels[order], np.arange(len(sizes) + 1))
    groups = [order[boundaries[label]:boundaries[label + 1]] for label in protected]
    small = np.flatnonzero(~np.isin(labels, protected))
    if len(small):
        groups.append(small)
    counts = np.array([len(group) for group in groups])
    goals = np.zeros(len(groups), dtype=int)
    goals[:len(protected)] = 1
    remaining = min(limit, max(budget, len(protected))) - int(goals.sum())
    shares = remaining * counts / counts.sum()
    goals += np.floor(shares).astype(int)
    leftover = remaining - int(np.floor(shares).sum())
    for i in np.argsort(-(shares - np.floor(shares)), kind="stable")[:leftover]:
        goals[i] += 1
    parts = []
    representatives = 0
    error = 0.0
    for group, goal in zip(groups, goals, strict=True):
        if not goal:
            continue
        part = compact(mesh, mesh.triangles[group], mesh.materials[group])
        try:
            reduced, part_error = _vereinfachen_kandidat(part, int(goal), int(goal))
        except ValueError:
            tri = part.positions[part.triangles]
            area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
            largest = np.array([int(np.argmax(area))])
            reduced = compact(part, part.triangles[largest], part.materials[largest])
            # Ohne meshopt-Ergebnis keine erfundene Quadric-Fehlerschätzung melden.
            # Der tatsächliche Verlust wird anschließend in beiden Richtungen gemessen.
            part_error = 0.0
            representatives += 1
        parts.append(reduced)
        error = max(error, part_error)
    stats.update(simplificationStage="components", representativeComponents=representatives)
    if messung is not None:
        messung.update(stats)
    return _zusammen(parts), error


def _vereinfachen_kandidat(
    mesh: Mesh, budget: int, maximum: int | None = None, *,
    messung: dict[str, Any] | None = None,
) -> tuple[Mesh, float]:
    """Kinder gemeinsam vereinfachen; offene Gruppenränder sperren, Materialnähte nicht.

    Alle Materialien gehen in einen Aufruf: meshoptimizer erkennt Nähte über gleiche Positionen
    und darf entlang einer Naht vereinfachen. Getrennte Aufrufe je Material sperrten jede
    Materialgrenze als Rand und konnten dadurch das Budget verfehlen.
    Reicht das nicht, folgt eine Stufe ohne gesperrte Ränder; der dabei mögliche Versatz an
    Kachelgrenzen steckt in der gemessenen Fehlergrenze (`_fehlergrenze`) und damit im
    geometricError der inneren Kachel. Blätter bleiben unverändert quelltreu.

    Nur der Arbeitsbuffer des C-Bindings ist Float32. Gewählte Vertices werden aus
    den unveränderten Float64-Quelldaten übernommen, UV und Farben bleiben zugeordnet.
    """
    positions = np.ascontiguousarray(mesh.positions - mesh.positions.mean(axis=0), dtype=np.float32)
    indices = np.ascontiguousarray(mesh.triangles.ravel(), dtype=np.uint32)
    vertex_material = np.full(len(mesh.positions), -1, dtype=np.int64)
    vertex_material[mesh.triangles.ravel()] = np.repeat(mesh.materials, 3)
    # Teilt ein Vertex Dreiecke verschiedener Materialien, ist das Material eines vereinfachten
    # Dreiecks nicht eindeutig; dann bleibt es beim getrennten Weg je Material.
    eindeutig = bool(np.all(vertex_material[mesh.triangles] == mesh.materials[:, None]))
    limit = maximum if maximum is not None else budget
    extent = max(float(np.linalg.norm(np.ptp(positions, axis=0))), 1e-12)
    for options in (
        meshoptimizer.SIMPLIFY_LOCK_BORDER | meshoptimizer.SIMPLIFY_ERROR_ABSOLUTE,
        meshoptimizer.SIMPLIFY_ERROR_ABSOLUTE | meshoptimizer.SIMPLIFY_PRUNE,
        meshoptimizer.SIMPLIFY_ERROR_ABSOLUTE,
        _SLOPPY,
    ):
        # PRUNE darf nur Splitter unter der lokalen Größenschwelle löschen.
        tolerance = extent / 512 if options & meshoptimizer.SIMPLIFY_PRUNE else extent
        if options == _SLOPPY:
            # Letzte Stufe für grobe Ebenen: Vertex-Clustering je Material, unabhängig von der
            # Topologie (Photogrammetrie-Meshes mit vielen Splittern halten sonst das Budget nicht).
            result, error = _je_material(mesh, positions, budget, extent, options)
        elif eindeutig:
            dest = np.empty_like(indices)
            measured = np.zeros(1, dtype=np.float32)
            count = meshoptimizer.simplify(
                dest,
                indices,
                positions,
                target_index_count=budget * 3,
                target_error=tolerance,
                options=options,
                result_error=measured,
            )
            faces = dest[:count].reshape(-1, 3).copy()
            result = compact(mesh, faces, vertex_material[faces[:, 0]].astype(np.int32))
            error = float(measured[0])
        else:
            result, error = _je_material(mesh, positions, budget, tolerance, options)
        if len(result.triangles) and len(result.triangles) <= limit:
            if messung is not None:
                messung["simplificationStage"] = (
                    "sloppy" if options == _SLOPPY else
                    "lock" if options & meshoptimizer.SIMPLIFY_LOCK_BORDER else
                    "prune" if options & meshoptimizer.SIMPLIFY_PRUNE else "relax"
                )
            return result, error
    raise ValueError(
        f"Dreiecksbudget {limit} auch mit Clustering nicht erreichbar ({len(result.triangles)})"
    )


def _je_material(
    mesh: Mesh, positions: np.ndarray, budget: int, extent: float, options: int
) -> tuple[Mesh, float]:
    """Getrennter Weg je Material (Vertices werden von mehreren Materialien geteilt)."""
    faces, materials = [], []
    error = 0.0
    ids, counts = np.unique(mesh.materials, return_counts=True)
    # Ganze Budgets verteilen, ohne Rundungsüberschreitung bei vielen Materialien.
    goals = np.floor(budget * counts / counts.sum()).astype(int)
    for i in np.argsort(-counts, kind="stable")[: budget - int(goals.sum())]:
        goals[i] += 1
    for material, goal in zip(ids, goals, strict=True):
        indices = np.ascontiguousarray(
            mesh.triangles[mesh.materials == material].ravel(), dtype=np.uint32
        )
        dest = np.empty_like(indices)
        measured = np.zeros(1, dtype=np.float32)
        if options == _SLOPPY:
            count = meshoptimizer.simplify_sloppy(
                dest,
                indices,
                positions,
                target_index_count=int(goal) * 3,
                target_error=1.0,
                result_error=measured,
            )
            # simplify_sloppy meldet den Fehler relativ zur Ausdehnung.
            measured *= meshoptimizer.simplify_scale(positions)
        else:
            count = meshoptimizer.simplify(
                dest,
                indices,
                positions,
                target_index_count=int(goal) * 3,
                target_error=extent,
                options=options,
                result_error=measured,
            )
        faces.append(dest[:count].reshape(-1, 3).copy())
        materials.append(np.full(count // 3, material, dtype=np.int32))
        error = max(error, float(measured[0]))
    return compact(mesh, np.concatenate(faces), np.concatenate(materials)), error


def _fehlergrenze(
    source: Mesh, reduced: Mesh, *, rho: float | None = None,
    max_unterteilung: int = 256, max_pruefpunkte: int = 200_000,
    messung: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """Zertifizierte Flächenschranke durch reguläre baryzentrische Unterteilung.

    Abstand zur Quelle ist 1-Lipschitz: d(p,S) ≤ d(c,S) + |p-c|.
    Die n² Teildreiecke sind zum Ausgangsdreieck ähnlich (ggf. gespiegelt);
    ihr Eckenradius ist R/n. Ihre Vereinigung deckt das ganze Dreieck ab.
    Deshalb gilt max(d(Teilmittelpunkt,S) + R/n) für ALLE Punkte.
    rho berücksichtigt 1 cm, Kacheldiagonale/512, gemessenen Median und das
    quadratische Radiusbudget (mit Reserve für ceil). Damit wächst die Arbeit
    nicht quadratisch mit der Kachelausdehnung. Kandidatendreiecksabstände sind
    obere Schranken; d(c,S) darf in der Formel durch sie ersetzt werden.
    Bei Unterteilungs-/Punktdeckel wird n verkleinert, aber stets der tatsächliche
    gröbere Radius verwendet: weniger scharf, weiterhin konservativ. Das Budget
    umfasst auch die ursprünglichen Mittelpunkte und muss diese aufnehmen können.
    Gruppen gleicher Unterteilung werden in höchstens 65536 Punkten je Block
    vektorisiert; es gibt keine Python-Schleife je Ausgangsdreieck.
    """
    start = time.monotonic()
    triangles = reduced.positions[reduced.triangles]
    count = len(triangles)
    if max_unterteilung < 1 or max_pruefpunkte < count or count == 0:
        raise ValueError("Ungültiges Unterteilungs-/Prüfpunktbudget")
    centers = triangles.mean(axis=1)
    radii = np.linalg.norm(triangles - centers[:, None, :], axis=2).max(axis=1)
    index = _ObereDistanz(source)
    distances = index.distance(centers)
    tolerance = max(
        0.01, float(np.linalg.norm(np.ptp(reduced.positions, axis=0))) / 512,
        float(np.median(distances)),
        2 * float(np.sqrt(np.sum(radii ** 2) / max(1, max_pruefpunkte - count))),
    ) if rho is None else rho
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("rho muss endlich und positiv sein")
    wanted = np.maximum(1, np.ceil(radii / tolerance))
    divisions = np.minimum(wanted, max_unterteilung).astype(np.int64)
    # Konservative gemeinsame Skalierung: sum(n²) überschätzt die Zusatzpunkte,
    # denn ungeteilte Dreiecke benötigen keinen zweiten Mittelpunktaufruf.
    extra = int(np.sum(divisions[divisions > 1] ** 2))
    available = max_pruefpunkte - count
    if extra > available:
        divisions = np.maximum(1, np.floor(divisions * np.sqrt(available / extra))).astype(int)
    bound = float(np.max((distances + radii)[divisions == 1], initial=0))
    queries = count
    for n in np.unique(divisions[divisions > 1]):
        # Aufrechte und umgekehrte Teildreiecke im baryzentrischen Gitter.
        i, j = np.indices((n, n)).reshape(2, -1)
        upright = i + j < n
        inverted = i + j < n - 1
        weights = np.concatenate([
            np.column_stack([i[upright] + 1 / 3, j[upright] + 1 / 3]),
            np.column_stack([i[inverted] + 2 / 3, j[inverted] + 2 / 3]),
        ]) / n
        selected = np.flatnonzero(divisions == n)
        batch = max(1, 65536 // (n * n))
        for offset in range(0, len(selected), batch):
            ids = selected[offset:offset + batch]
            tri = triangles[ids]
            points = (tri[:, None, 0, :]
                      + weights[None, :, :1] * (tri[:, None, 1, :] - tri[:, None, 0, :])
                      + weights[None, :, 1:] * (tri[:, None, 2, :] - tri[:, None, 0, :]))
            sampled = index.distance(points.reshape(-1, 3)).reshape(len(ids), -1)
            bound = max(bound, float(np.max(sampled + radii[ids, None] / n)))
            queries += points.shape[0] * points.shape[1]
    # Float64-Rechenreserve auch für große Projektkoordinaten.
    reserve = 64 * np.finfo(np.float64).eps * max(1.0, float(np.abs(triangles).max()))
    bound += reserve
    if messung is not None:
        messung.update(
            surfaceBoundRhoM=tolerance, surfaceBoundQueryPoints=queries,
            surfaceBoundCappedTriangles=int(np.sum(divisions < wanted)),
            surfaceBoundMaxSubdivision=int(divisions.max()),
            surfaceBoundSeconds=time.monotonic() - start,
        )
    return bound, float(distances.max())


def _lod_fehler(
    source: Mesh, reduced: Mesh, messung: dict[str, Any], *, max_pruefpunkte: int = 200_000,
) -> tuple[float, float, float]:
    """Zweiseitige Flächenschranken statt meshoptimizers quadric-/Clustering-Fehler.

    Synthetisch skaliert result_error korrekt in Metern, ist aber weder obere
    noch untere Hausdorff-Schranke (sloppy kann bei echtem Randverlust null melden).
    Beide Richtungen werden hier über ALLE Dreiecke samt tatsächlichem Teilradius
    abgedeckt, einschließlich verschwundener dünner Strukturen und Flächeninnerem.
    Daher erfasst result_error keine zusätzliche ungemessene Abweichung und bleibt
    reine Diagnose. Die Summe mit dem maximalen Kindfehler folgt per
    Dreiecksungleichung bis zu den Blättern; GLB-Rundung kommt separat hinzu.
    """
    start = time.monotonic()
    forward, centers = _fehlergrenze(
        source, reduced, max_pruefpunkte=max_pruefpunkte, messung=messung,
    )
    reverse_stats: dict[str, Any] = {}
    # Rückwärts wird die Kindfläche unterteilt; sie hat bis zu acht volle Kachelbudgets.
    reverse, _ = _fehlergrenze(
        reduced, source, max_pruefpunkte=max(max_pruefpunkte, 2 * len(source.triangles)),
        messung=reverse_stats,
    )
    messung.update({"reverse" + key[0].upper() + key[1:]: value
                    for key, value in reverse_stats.items()})
    messung["sourceErrorBoundM"] = reverse
    messung["bidirectionalBoundSeconds"] = time.monotonic() - start
    return forward, centers, reverse


def _zusammen(meshes: list[Mesh]) -> Mesh:
    offsets = np.cumsum([0] + [len(m.positions) for m in meshes[:-1]])
    result = replace(
        meshes[0],
        positions=np.concatenate([m.positions for m in meshes]),
        triangles=np.concatenate([m.triangles + o for m, o in zip(meshes, offsets, strict=True)]),
        materials=np.concatenate([m.materials for m in meshes]),
        uv=np.concatenate([m.uv for m in meshes]) if meshes[0].uv is not None else None,
        colors=np.concatenate([m.colors for m in meshes]) if meshes[0].colors is not None else None,
    )
    # Gleiche Grafikvertices an Kindgrenzen wieder verbinden, damit diese im
    # Elternknoten innere Kanten werden. UV-/Farbnähte bleiben getrennt.
    columns = [result.positions]
    columns.extend(a for a in (result.uv, result.colors) if a is not None)
    _, used, inverse = np.unique(
        np.concatenate(columns, axis=1), axis=0, return_index=True, return_inverse=True
    )
    return replace(
        result,
        positions=result.positions[used],
        triangles=inverse[result.triangles].astype(np.uint32),
        uv=result.uv[used] if result.uv is not None else None,
        colors=result.colors[used] if result.colors is not None else None,
    )


def _blattplan(
    part: Mesh, cache: BildCache, blatt_dreiecke: int, blatt_texel: int, max_dreiecke: int,
) -> tuple[bool, tuple[list[Insel], list[Mesh]] | None, bool]:
    """Blattbudgets einschließlich der tatsächlich geplanten Texturschnitte prüfen."""
    vertex_bytes = 12 + (8 if part.uv is not None else 0)
    vertex_bytes += 12 if part.colors is not None else 0
    estimated_bytes = (
        len(part.positions) * vertex_bytes
        + part.triangles.size * 4
        + len(np.unique(part.materials)) * 1500
        + 4096
    )
    inner = len(part.triangles) > blatt_dreiecke or estimated_bytes >= 2_950_000
    leaf_plan = None
    texture_overflow = False
    if not inner:
        leaf_plan = vorbereiten(part, cache)
        pages = packplan(leaf_plan[0], blatt_texel)
        texture_overflow = len(pages) > 1 or any(
            max(page["size"]) > blatt_texel for page in pages
        )
        inner = (sum(len(i.mesh.triangles) for i in leaf_plan[0])
                 + sum(len(m.triangles) for m in leaf_plan[1]) > max_dreiecke
                 or (texture_overflow and len(part.triangles) >= 6000))
        if inner:
            leaf_plan = None
    return inner, leaf_plan, texture_overflow


def _knoten(
    low: np.ndarray, high: np.ndarray, origin: np.ndarray, parent_origin: np.ndarray,
    root: bool, error: float, uri: str, children: list[dict[str, Any]],
) -> dict[str, Any]:
    """Kachelhülle und lokale Translation im bestehenden Ausgabeformat."""
    center = origin if root else np.zeros(3)
    extent = np.maximum((high - low) / 2 + 0.00035, 0.00035)
    node: dict[str, Any] = {
        "boundingVolume": {
            "box": [*center.tolist(), extent[0], 0, 0, 0, extent[1], 0, 0, 0, extent[2]]
        },
        "geometricError": error,
        "refine": "REPLACE",
        "content": {"uri": uri},
    }
    if not root:
        transform = np.eye(4)
        transform[:3, 3] = origin - parent_origin
        node["transform"] = transform.ravel(order="F").tolist()
    if children:
        node["children"] = children
    return node


def _hierarchie(
    ausgabe: Path, cache: BildCache, auftraege: Auftraege, jobs: int,
    max_dreiecke: int, blatt_dreiecke: int, blatt_texel: int, packen: bool,
    pending: list[tuple[dict[str, Any], Any]], pack_plan: list[tuple[str, np.ndarray, bool]],
    tile_points: list[dict[str, Any]],
) -> Callable[..., tuple[dict[str, Any], Mesh, float]]:
    """Rekursiven Aufbau mit eigener stabiler Kachelnummerierung erzeugen."""
    serial = 0

    def bauen(
        part: Mesh, parent_origin: np.ndarray, root: bool = False
    ) -> tuple[dict[str, Any], Mesh, float]:
        nonlocal serial
        number = serial
        serial += 1
        low, high = part.positions.min(axis=0), part.positions.max(axis=0)
        origin = (low + high) / 2
        children = []
        child_error = 0.0
        inner, leaf_plan, texture_overflow = _blattplan(
            part, cache, blatt_dreiecke, blatt_texel, max_dreiecke,
        )
        bound_stats: dict[str, Any] = {}
        if inner and len(part.triangles) < 2:
            raise ValueError("Einzelnes Dreieck überschreitet das Blattbudget")
        if inner:
            centers = part.positions[part.triangles].mean(axis=1)
            axis = int(np.argmax(np.ptp(centers, axis=0)))
            # Median und stabiler Tie-Break: auch identische Schwerpunkte terminieren.
            order = np.argsort(centers[:, axis], kind="stable")
            representations = []
            for selected in np.array_split(order, 2):
                child = compact(part, part.triangles[selected], part.materials[selected])
                child_node, representation, err = bauen(child, np.zeros(3) if root else origin)
                children.append(child_node)
                representations.append(representation)
                child_error = max(child_error, err)
            combined = _zusammen(representations)
            # Stets echte LOD-Reduktion versuchen, auch unterhalb der harten Grenze.
            target = min(max_dreiecke, max(1, len(combined.triangles) // 2))
            reduced, measured = _vereinfachen(
                combined, target, max_dreiecke, messung=bound_stats,
            )
            bound, surface_distance, reverse = _lod_fehler(combined, reduced, bound_stats)
        else:
            reduced, measured = part, 0.0
            bound, surface_distance, reverse = 0.0, 0.0, 0.0
        uri = f"kachel-{number:06d}.glb"
        # Die Hierarchie arbeitet weiter mit Quell-UV; erst die Ausgabedarstellung
        # bekommt eigene UV und gegebenenfalls zusätzliche Grafikdreiecke an Seitengrenzen.
        error = child_error + max(bound, reverse, 1e-7) + 1e-8 if inner else 0.0
        texture_stats: dict[str, Any] = {}
        if auftraege.parallel:
            # Backen dupliziert/ordnet Grafikvertices, aber erhält jede Fläche.
            # Blattschnitte sind bereits im geometrischen Inselplan enthalten.
            if inner:
                positions = reduced.positions
            else:
                assert leaf_plan is not None
                positions = np.concatenate([i.mesh.positions for i in leaf_plan[0]]
                                           + [m.positions for m in leaf_plan[1]])
            rounding = rundung(positions, origin)
            future = auftraege.abgeben(
                texturauftrag, reduced, combined if inner else None, error,
                blatt_texel, leaf_plan, ausgabe / uri, origin, root, max_dreiecke, rounding,
            )
            pending.append((texture_stats, future))
            if len(pending) >= 2 * jobs:
                stats, done = pending.pop(0)
                stats.update(done.result())
        else:
            if inner:
                textured, textures = backen(
                    reduced, combined, error, cache=cache, messung=texture_stats,
                )
            else:
                textured, textures = zuschneiden(
                    reduced, cache=cache, maximum=blatt_texel,
                    messung=texture_stats, plan=leaf_plan,
                )
            while len(textured.triangles) > max_dreiecke:
                target = min(
                    len(reduced.triangles) - 1,
                    int(len(reduced.triangles) * max_dreiecke / len(textured.triangles) * 0.8),
                )
                if target < 1:
                    raise ValueError(
                        "Texturseiten eines Dreiecks überschreiten das Kachelbudget"
                    )
                reduced, measured = _vereinfachen(combined, target, target, messung=bound_stats)
                bound, surface_distance, reverse = _lod_fehler(combined, reduced, bound_stats)
                error = child_error + max(bound, reverse, 1e-7) + 1e-8
                textured, textures = backen(
                    reduced, combined, error, cache=cache, messung=texture_stats,
                )
            rounding = schreiben(textured, ausgabe / uri, origin, textures, node_origin=root)
            texture_stats.update(triangles=len(textured.triangles),
                                 bytes=(ausgabe / uri).stat().st_size,
                                 worker=os.getpid(), queueSeconds=0.0,
                                 completedMonotonic=time.monotonic(),
                                 texturCacheBytesBudget=cache.grenze)
        error += rounding if inner else 0.0
        if packen:
            pack_plan.append((uri, origin, root))
        node = _knoten(low, high, origin, parent_origin, root, error, uri, children)
        tile_points.append(dict(
            uri=uri,
            representationTriangles=len(reduced.triangles),
            leaf=not inner,
            resultErrorM=measured,
            geometricError=error,
            roundingErrorM=rounding,
            surfaceCenterDistanceM=surface_distance,
            surfaceErrorBoundM=bound,
            textureBudgetExceeded=texture_overflow and not inner,
            textureBudgetException=(
                "unter 6000 Dreiecken" if texture_overflow and not inner else None
            ),
            **bound_stats,
        ))
        tile_points[-1]["textureResult"] = texture_stats
        return node, reduced, max(error, rounding)

    return bauen


def _budgets(max_dreiecke: int, blatt_dreiecke: int, blatt_texel: int,
             jobs: int, textur_cache_bytes: int) -> None:
    """Öffentliche Budgetparameter vor dem Anlegen von Ressourcen validieren."""
    if not 1 <= blatt_dreiecke <= max_dreiecke <= 100_000:
        raise ValueError("Erforderlich: 1 ≤ blatt-dreiecke ≤ max-dreiecke ≤ 100000")
    if blatt_texel < 16 or blatt_texel > 4096 or blatt_texel & (blatt_texel - 1):
        raise ValueError("blatt-texel benötigt eine Zweierpotenz zwischen 16 und 4096")
    if jobs < 1:
        raise ValueError("jobs muss mindestens 1 sein")
    if textur_cache_bytes < 0:
        raise ValueError("Texturcache muss mindestens 0 Bytes haben")


def _packen(
    basis: Path, ausgabe: Path, crs: str | None, hoehe: str | None,
    auftraege: Auftraege, jobs: int, pack_plan: list[tuple[str, np.ndarray, bool]],
    tileset: dict[str, Any], mesh: Mesh,
) -> None:
    """Nach der vollständigen Prüffassung packen und einmalig abschließen."""
    from .paket import Packschritt

    # Die vollständige Prüffassung bleibt auch bei einem Encoderfehler verfügbar.
    from .parallel import packauftrag

    packer = Packschritt(basis / "paket", crs, hoehe)
    packed = []
    for uri, origin, is_root in pack_plan:
        if auftraege.parallel:
            packed.append(auftraege.abgeben(
                packauftrag, ausgabe / uri, packer.dest, origin, is_root,
            ))
            if len(packed) >= 2 * jobs:
                result = packed.pop(0).result()
                packer.uebernehmen(*result)
        else:
            reference = read_mesh(ausgabe / uri)
            if not is_root:
                reference.positions += origin
            packer.kachel(reference, uri, origin, is_root)
    for future in packed:
        packer.uebernehmen(*future.result())
    packer.abschliessen(tileset, mesh)


def kacheln(
    eingabe: Path,
    ausgabe: Path,
    *,
    max_dreiecke: int = 100_000,
    blatt_dreiecke: int = 40_000,
    blatt_texel: int = 2048,
    textur_cache_bytes: int = 4 * 1024**3,
    crs: str | None = None,
    hoehe: str | None = None,
    packen: bool = False,
    jobs: int = min(8, os.cpu_count() or 1),
) -> dict[str, Any]:
    """OBJ räumlich halbieren, dann die Anzeigehierarchie von unten aufbauen.

    Nichtleere Ausgabeordner werden abgelehnt. Messpunkte enthalten Wanduhr und
    Prozess-Höchststand (Linux ru_maxrss), keine isolierte Speicherzuweisung.
    """
    _budgets(max_dreiecke, blatt_dreiecke, blatt_texel, jobs, textur_cache_bytes)
    cache = BildCache(textur_cache_bytes if jobs == 1 else 0)
    if eingabe.suffix.lower() != ".obj":
        raise ValueError("Kacheln unterstützt nur OBJ in metrischen Projektkoordinaten")
    if ausgabe.exists() and any(ausgabe.iterdir()):
        raise ValueError("Ausgabeordner muss leer sein")
    if packen and (not crs or not hoehe):
        raise ValueError("--packen benötigt --crs und --hoehe")
    start = time.monotonic()
    points: list[dict[str, Any]] = []

    def messen(phase: str, **values: Any) -> None:
        entry = {
            "phase": phase,
            "seconds": time.monotonic() - start,
            "peakRssMiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "peakRssMiBTotal": auftraege.peak_mib(),
            **values,
        }
        points.append(entry)
        print(json.dumps(entry, allow_nan=False), file=sys.stderr, flush=True)

    auftraege = Auftraege(jobs, textur_cache_bytes // jobs)
    try:
        mesh = read_mesh(eingabe, crs=crs, unit="m", up="z")
        messen("lesen", triangles=len(mesh.triangles), vertices=len(mesh.positions))
        ausgabe.mkdir(parents=True, exist_ok=True)
        basis = ausgabe
        if packen:
            ausgabe = basis / "prueffassung"
            ausgabe.mkdir()
        pack_plan: list[tuple[str, np.ndarray, bool]] = []
        pending: list[tuple[dict[str, Any], Any]] = []
        tile_points: list[dict[str, Any]] = []

        bauen = _hierarchie(
            ausgabe, cache, auftraege, jobs, max_dreiecke, blatt_dreiecke, blatt_texel,
            packen, pending, pack_plan, tile_points,
        )

        try:
            root, _, error = bauen(mesh, np.zeros(3), root=True)
            for stats, done in pending:
                stats.update(done.result())
        except Nachvereinfachung as exc:
            # Selten: Texturierung erzeugt zusätzliche Flächen. Alle Eltern mit
            # derselben nachvereinfachten Darstellung wie im seriellen Weg neu bauen.
            auftraege.schliessen()
            for _, other in pending:
                if not other.cancelled():
                    try:
                        other.result()
                    except Nachvereinfachung:
                        pass  # Weitere Budgetmeldungen gehören zum selben Neuaufbau.
            auftraege = Auftraege(1, textur_cache_bytes, auftraege.peak_mib())
            messen("nachvereinfachung", ursache=str(exc), jobs=1)
            pending.clear()
            tile_points.clear()
            pack_plan.clear()
            cache.schliessen()
            cache = BildCache(textur_cache_bytes)
            bauen = _hierarchie(
                ausgabe, cache, auftraege, jobs, max_dreiecke, blatt_dreiecke, blatt_texel,
                packen, pending, pack_plan, tile_points,
            )
            root, _, error = bauen(mesh, np.zeros(3), root=True)
        for entry in tile_points:
            texture_stats = entry.pop("textureResult")
            completed = texture_stats.pop("completedMonotonic")
            if not entry["leaf"] and texture_stats["triangles"] > 1000 and any(
                min(page["size"]) < 256 for page in texture_stats.get("texturePages", [])
            ):
                messen("warnung", uri=entry["uri"],
                       warning="Innere Texturkante unter 256 Pixeln", **texture_stats)
            messen("kachel", seconds=completed - start, **entry, **texture_stats)
        cache.schliessen()
        tileset = {
            "asset": {"version": "1.1"},
            "geometricError": error,
            "root": root,
            "extras": {
                "coordinateReferenceSystem": crs,
                "heightSystem": hoehe,
                "axis": "Z",
                "unit": "m",
            },
        }
        (ausgabe / "tileset.json").write_text(json.dumps(tileset, indent=2, allow_nan=False))
        if packen:
            _packen(basis, ausgabe, crs, hoehe, auftraege, jobs, pack_plan, tileset, mesh)
        auftraege.schliessen()
        messen("fertig", triangles=len(mesh.triangles), tiles=len(tile_points))
        report = {
            "measurements": points, "sourceTriangles": len(mesh.triangles),
            "tiles": len(tile_points),
        }
        (ausgabe / "kacheln-log.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        return report
    finally:
        auftraege.schliessen()
        cache.schliessen()

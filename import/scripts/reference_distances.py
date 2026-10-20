"""Referenzstrecken für die Abnahme Scheibe 1.

Sollwerte kommen direkt aus der E57: ``pye57.read_scan(k, transform=True,
ignore_missing_fields=True)`` — also Weltkoordinaten mit angewendeter Scan-Pose, Float64. Eine
Strecke ist ein Paar Punkt-Indizes ``(ia, ib)`` in genau diesem Array von Scan ``k`` (``read_scan``
verwirft ungültige Punkte; die Indizes beziehen sich auf das bereits gefilterte Array).

Zwei Unterbefehle:

``select``  wählt deterministisch (Seed) Punktpaare auf stabilen, ebenen Flächen: je Kandidat
            lokale Hauptachsenanalyse auf zwei Maßstäben — fein im Radius ``--radius`` (Dicke
            sigma ≤ ``--max-sigma``, Vorgabe 10 cm / 5 mm) und grob im Radius ``--radius-coarse``
            (sigma ≤ ``--max-sigma-coarse``, Vorgabe 30 cm / 10 mm, auf jedem 20. Punkt) —, Normale
            nahezu waagrecht (Wand), Auftreffwinkel zum Scanner ≤ 45°, Abstand zum Scanner
            3-12 m, Punktabstand im Paar 2-8 m. Der grobe Maßstab kam nach dem ersten Lauf dazu:
            ein Vorhang hängender Weidenzweige war auf 10 cm eben genug und lieferte einen
            „Wand“-Punkt mitten im Laub. Schreibt die Paare *und* gleich die Sollwerte.
``compute`` liest eine Paar-Datei (``{"pairs": [{"scan", "ia", "ib"}, …]}``) und rechnet die
            Sollwerte neu — so lässt sich jede Strecke aus Scan-Index + Punkt-Indizes nachprüfen.

Mit ``--copc`` wird zusätzlich je Endpunkt der Abstand zum nächsten COPC-Punkt bestimmt (laspy,
Kachel-Query um den Punkt). Das trennt Konvertierungsfehler (E57 → COPC, 1-mm-Raster) vom
Picking-Fehler im Browser.

Ausgabe (JSON) je Strecke: Weltkoordinaten a/b (Float64), Scanner-Standpunkt (für die
Kameraposition im Browser: der Scanner hatte freie Sicht auf den Punkt), Sollabstand in m.

Wichtig: Das prüft Konvertierung + Picking-Konsistenz, keine unabhängige Genauigkeit — dafür
braucht es Kontrollmaße des Vermessers, die es für die Pilotkirche noch nicht gibt.

    uv run python scripts/reference_distances.py select <e57> --out ref.json \\
        [--scans 0,4,9,…] [--per-scan 1] [--count 10] [--seed 13] [--copc <cloud.copc.laz>]
    uv run python scripts/reference_distances.py compute <e57> ref.json --out ref2.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pye57
from scipy.spatial import cKDTree

RANGE_MIN_M = 3.0
RANGE_MAX_M = 12.0
PAIR_MIN_M = 2.0
PAIR_MAX_M = 8.0
MAX_NORMAL_Z = 0.2  # |n_z| ≤ 0,2 → Fläche steht nahezu senkrecht (Wand)
MIN_COS_INCIDENCE = np.cos(np.deg2rad(45.0))
MIN_NEIGHBOURS = 30
CANDIDATES_PER_SCAN = 4000
# Über den ganzen Standpunktring der Kirche verteilt (45 Scans), Reserve für Scans ohne Treffer.
DEFAULT_SCANS = "0,4,9,13,18,22,27,31,36,40,2,7,11,16,20,25,29,34,38,43"


def read_world(e: pye57.E57, scan: int) -> tuple[np.ndarray, np.ndarray]:
    """Weltpunkte (Nx3, Float64) von Scan ``scan`` und dessen Standpunkt (Pose-Translation)."""
    d = e.read_scan(scan, ignore_missing_fields=True, transform=True)
    pts = np.column_stack([d["cartesianX"], d["cartesianY"], d["cartesianZ"]]).astype(np.float64)
    origin = np.asarray(e.get_header(scan).translation, dtype=np.float64)
    return pts, origin


def surface_quality(
    pts: np.ndarray, tree: cKDTree, centres: np.ndarray, radius: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Je Zentrum: Dicke sigma (Wurzel des kleinsten Eigenwerts), Normale, Nachbarzahl im Radius.

    ``tree`` ist über ``pts`` gebaut; ``centres`` sind beliebige Weltpunkte (Nx3)."""
    sigma = np.full(len(centres), np.inf)
    normals = np.zeros((len(centres), 3))
    counts = np.zeros(len(centres), dtype=np.int64)
    for j, nb in enumerate(tree.query_ball_point(centres, r=radius)):
        counts[j] = len(nb)
        if len(nb) < MIN_NEIGHBOURS:
            continue
        q = pts[nb] - pts[nb].mean(axis=0)
        w, v = np.linalg.eigh(q.T @ q / len(nb))
        # Linienartige Nachbarschaft (Kante, Draht) ausschließen: zweite Achse muss tragen.
        if w[1] < 0.1 * w[2]:
            continue
        sigma[j] = float(np.sqrt(max(w[0], 0.0)))
        normals[j] = v[:, 0]
    return sigma, normals, counts


def good_candidates(
    pts: np.ndarray,
    origin: np.ndarray,
    rng: np.random.Generator,
    radius: float,
    max_sigma: float,
    radius_coarse: float,
    max_sigma_coarse: float,
) -> np.ndarray:
    """Indizes von Punkten auf stabilen, ebenen, dem Scanner zugewandten Wandflächen."""
    rng_m = np.linalg.norm(pts - origin, axis=1)
    in_range = np.flatnonzero((rng_m >= RANGE_MIN_M) & (rng_m <= RANGE_MAX_M))
    if len(in_range) == 0:
        return in_range
    # KD-Baum nur über die Punkte, die als Nachbarn in Frage kommen (Reichweite + Radius).
    margin = max(radius, radius_coarse)
    near = np.flatnonzero((rng_m >= RANGE_MIN_M - margin) & (rng_m <= RANGE_MAX_M + margin))
    near_pts = pts[near]
    n_cand = min(CANDIDATES_PER_SCAN, len(in_range))
    cand = np.sort(rng.choice(in_range, size=n_cand, replace=False))
    centres = near_pts[np.searchsorted(near, cand)]
    sigma, normals, _ = surface_quality(near_pts, cKDTree(near_pts), centres, radius)
    view = origin - pts[cand]
    view /= np.linalg.norm(view, axis=1, keepdims=True)
    cos_inc = np.abs(np.einsum("ij,ij->i", normals, view))
    wall = np.abs(normals[:, 2]) <= MAX_NORMAL_Z
    ok = (sigma <= max_sigma) & wall & (cos_inc >= MIN_COS_INCIDENCE)
    cand = cand[ok]
    if len(cand) == 0:
        return cast(np.ndarray, cand)
    # Grober Maßstab auf ausgedünnter Wolke (jeder 20. Punkt), sonst Zehntausende Nachbarn je Kugel.
    coarse = near_pts[::20]
    sigma_c, _, _ = surface_quality(coarse, cKDTree(coarse), pts[cand], radius_coarse)
    return cast(np.ndarray, cand[sigma_c <= max_sigma_coarse])


def pick_pairs(
    pts: np.ndarray, cand: np.ndarray, n: int, rng: np.random.Generator
) -> list[tuple[int, int]]:
    """``n`` Paare mit 2-8 m Abstand, jeder Punkt höchstens einmal, deterministisch per ``rng``."""
    order = rng.permutation(cand)
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for a in order:
        if len(pairs) >= n:
            break
        if int(a) in used:
            continue
        d = np.linalg.norm(pts[order] - pts[a], axis=1)
        for b in order[(d >= PAIR_MIN_M) & (d <= PAIR_MAX_M)]:
            if int(b) not in used and int(b) != int(a):
                pairs.append((int(a), int(b)))
                used.update((int(a), int(b)))
                break
    return pairs


def nearest_copc_mm(copc: Path, p: np.ndarray, half: float = 0.02) -> float | None:
    """Abstand (mm) vom Weltpunkt ``p`` zum nächsten COPC-Punkt in einer ±``half``-m-Box."""
    import laspy
    from laspy.copc import Bounds

    with laspy.CopcReader.open(str(copc)) as r:
        pc = r.query(Bounds(mins=p - half, maxs=p + half))
    if len(pc) == 0:
        return None
    q = np.column_stack([np.asarray(pc.x), np.asarray(pc.y), np.asarray(pc.z)])
    return float(np.min(np.linalg.norm(q - p, axis=1)) * 1000.0)


def compute(e: pye57.E57, pairs: list[dict[str, int]], copc: Path | None) -> list[dict[str, Any]]:
    """Sollwerte je Paar ``{"scan", "ia", "ib"}``, Scan für Scan (jeder wird nur einmal gelesen)."""
    out: list[dict[str, Any]] = [{} for _ in pairs]
    for scan in sorted({p["scan"] for p in pairs}):
        pts, origin = read_world(e, scan)
        for i, p in enumerate(pairs):
            if p["scan"] != scan:
                continue
            a = pts[p["ia"]]
            b = pts[p["ib"]]
            rec: dict[str, Any] = {
                "id": i + 1,
                "scan": scan,
                "ia": p["ia"],
                "ib": p["ib"],
                "a": a.tolist(),
                "b": b.tolist(),
                "scanner": origin.tolist(),
                "distanceM": float(np.linalg.norm(b - a)),
            }
            if copc is not None:
                rec["nearestCopcMm"] = [nearest_copc_mm(copc, a), nearest_copc_mm(copc, b)]
            out[i] = rec
        del pts
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sel = sub.add_parser("select", help="Paare wählen und Sollwerte rechnen")
    sel.add_argument("e57", type=Path)
    sel.add_argument("--out", type=Path, required=True)
    sel.add_argument("--scans", default=DEFAULT_SCANS, help="Scan-Indizes, Reihenfolge")
    sel.add_argument("--per-scan", type=int, default=1)
    sel.add_argument("--count", type=int, default=10, help="Gesamtzahl Strecken (Abbruch danach)")
    sel.add_argument("--seed", type=int, default=13)
    sel.add_argument("--radius", type=float, default=0.10, help="Nachbarschaftsradius in m")
    sel.add_argument("--max-sigma", type=float, default=0.005, help="max. Flächendicke sigma in m")
    sel.add_argument("--radius-coarse", type=float, default=0.30)
    sel.add_argument("--max-sigma-coarse", type=float, default=0.010)
    sel.add_argument("--copc", type=Path)
    cmp_ = sub.add_parser("compute", help="Sollwerte aus einer Paar-Datei neu rechnen")
    cmp_.add_argument("e57", type=Path)
    cmp_.add_argument("pairs", type=Path)
    cmp_.add_argument("--out", type=Path, required=True)
    cmp_.add_argument("--copc", type=Path)
    args = ap.parse_args(argv)

    e = pye57.E57(str(args.e57))
    if args.cmd == "select":
        rng = np.random.default_rng(args.seed)
        pairs: list[dict[str, int]] = []
        for scan in (int(s) for s in args.scans.split(",")):
            if len(pairs) >= args.count:
                break
            pts, origin = read_world(e, scan)
            cand = good_candidates(
                pts,
                origin,
                rng,
                args.radius,
                args.max_sigma,
                args.radius_coarse,
                args.max_sigma_coarse,
            )
            want = min(args.per_scan, args.count - len(pairs))
            chosen = pick_pairs(pts, cand, want, rng)
            print(f"Scan {scan}: {len(cand)} Kandidaten, {len(chosen)} Paare", file=sys.stderr)
            pairs += [{"scan": scan, "ia": a, "ib": b} for a, b in chosen]
            del pts
        meta: dict[str, Any] = {
            "seed": args.seed,
            "scans": args.scans,
            "perScan": args.per_scan,
            "radiusM": args.radius,
            "maxSigmaM": args.max_sigma,
            "radiusCoarseM": args.radius_coarse,
            "maxSigmaCoarseM": args.max_sigma_coarse,
        }
    else:
        pairs = [
            {"scan": int(p["scan"]), "ia": int(p["ia"]), "ib": int(p["ib"])}
            for p in json.loads(args.pairs.read_text())["pairs"]
        ]
        meta = {"from": str(args.pairs)}
    result = {
        "source": args.e57.name,
        "method": "pye57.read_scan(k, transform=True, ignore_missing_fields=True)",
        **meta,
        "pairs": compute(e, pairs, args.copc),
    }
    args.out.write_text(json.dumps(result, indent=2))
    for p in result["pairs"]:
        print(f"#{p['id']:2d} Scan {p['scan']:2d}: {p['distanceM']:.4f} m", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

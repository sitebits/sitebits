"""Überblendtest Scheibe 3: Kantenversatz Panorama ↔ Punktwolke in
Pixeln.

Eingabe je Standpunkt: zwei Bildschirmfotos aus derselben Kamera (1920x1080, FOV 60°) —
``<id>-cloud.png`` (Überblendung 0, EDL aus, RGB) und ``<id>-pano.png`` (Überblendung 1),
aufgenommen von ``web/e2e/overlay.spec.ts``. Methode:

1. Grauwert → Gradientenbetrag (zentrale Differenzen) für beide Bilder.
2. Kacheln (Vorgabe 96 px) mit ``search`` Pixel Abstand zum Bildrand; gültig nur, wo die
   Punktwolke die Kachel zu ≥ 90 % deckt (Hintergrund #111111 = keine Punkte) und beide
   Gradientenkacheln Struktur haben (Standardabweichung ≥ ``min_std``).
3. Je Kachel die ganzzahlige Verschiebung (dx, dy) in [-search, search]^2 (Mess-Fenster)
   mit der größten normierten Kreuzkorrelation (NCC) der
   Gradientenkacheln, Subpixel per Parabel in x und y. Kacheln mit Spitzenwert < ``min_peak``
   fallen weg (keine eindeutige Entsprechung). Zusätzlich verwirft eine Kachel gegen
   periodische Muster (Fliesenraster, Fensterprofile):
   a) liegt das ganzzahlige Maximum am Rand des Mess-Fensters (|dx| bzw. |dy| == ``search``),
      deutet das darauf hin, dass der wahre Versatz außerhalb des Fensters liegt (Grund
      ``"border"``);
   b) ist die im Mess-Fenster gefundene Verschiebung nicht **global eindeutig** über ein
      deutlich breiteres Kontroll-Fenster ``±unique_search`` (Vorgabe 24 px, nur zur Prüfung,
      das Mess-Fenster bleibt ``±6``): das Maximum aus dem Mess-Fenster muss zugleich das
      globale NCC-Maximum über ``±unique_search`` sein, UND kein zweites lokales Maximum
      ≥ 2 px davon entfernt darf NCC ≥ Spitze - 0,05 erreichen — sonst Grund ``"ambiguous"``.
      Der Betrag der Ableitung eines periodischen Musters ist selbst periodisch
      mit der halben Periode (``|cos|`` statt ``cos``); bei einer exakten Perioden-Verschiebung
      liegt der wahre Treffer daher oft außerhalb des engen ``±search``-Fensters, aber (bei
      Perioden ≤ ``unique_search``) innerhalb von ``±unique_search`` und wird dort als
      gleichwertiges zweites Maximum erkannt.
   Verworfene Kacheln werden mit Grund (``"lowPeak"``/``"border"``/``"ambiguous"``) in
   ``tileDetails`` festgehalten, zählen aber nicht zu ``tiles``/Median/p90. Nahe am Bildrand
   ist das Kontroll-Fenster kleiner als ``±unique_search`` (an den verfügbaren Bildbereich
   geklemmt) — das Kachelraster selbst bleibt bei ``±search`` Rand.
4. Ergebnis: Median und 90-%-Quantil von |(dx, dy)| über die gültigen Kacheln.

Kriterium: Median ≤ 2 px bei ≥ 12 gültigen Kacheln; p90 wird immer ausgewiesen (ein
Versatz nur am Bildrand deutet auf falsche Intrinsik). Gradienten statt Farben, weil Punktwolke
(RGB je Punkt, Punktgröße 2) und Panorama (JPEG) unterschiedlich weich zeichnen.

Für Aufrufer: liegt der wahre Versatz einer (nicht periodischen) Kachel außerhalb von
±``search`` px, werden ALLE Kacheln verworfen — ``tiles`` wird 0 und ``medianPx``/``p90Px``
werden ``None`` (kein irreführender Kleinwert). Der Grund hängt davon ab, wie weit der wahre
Versatz über das Fenster hinausgeht (empirisch an nicht periodischer Textur geprüft, Vorgabe
``search=6``): knapp darüber (hier ~6-7 px) meist ``"border"`` (das erreichbare Maximum liegt
am Fensterrand), deutlich darüber (hier ab ~8 px) meist ``"lowPeak"`` (keine Kachel im Fenster
korreliert mehr ausreichend). Das ist genau das Bild einer stark falschen Ausrichtung (z. B.
gespiegeltes oder falsch orientiertes Panorama). Aufrufer sollten deshalb bei ``tiles == 0`` die
Gründe in ``tileDetails`` auswerten statt sich nur auf ``medianPx``/``ok`` zu verlassen.

**Bekannte Grenze:** Der Eindeutigkeits-Check deckt periodische Muster mit
Periode ≤ ``unique_search`` (Vorgabe 24 px) ab, weil der wahre Treffer dann innerhalb des
Kontroll-Fensters liegt und als zweites Maximum erkannt wird. Bei einer exakten
Perioden-Verschiebung, deren Periode > ``unique_search`` ist, liegt der wahre Treffer
außerhalb auch dieses breiteren Fensters — das ist aus einem einzelnen, noch so breiten
endlichen Suchfenster numerisch nicht mehr entscheidbar (der Versatz wird dann unauffällig
als kleiner oder Null gemessen). Für diesen Fall braucht es eine zusätzliche, unabhängige
Prüfung außerhalb der Kreuzkorrelation: Aufrufer sollten je Standpunkt eine visuelle
Überblend-Kontrolle (Mensch sieht Kante/Fuge im Bild) als zweite Absicherung vorsehen, nicht
sich allein auf dieses Skript verlassen.

Mehrere Blickrichtungen je Standpunkt: ``batch`` liest auch
``<id>-<n>-cloud.png``/``-pano.png`` und fasst die Blicke eines Standpunkts zusammen — Kacheln
addiert, Median/p90/``ok`` über alle gültigen Kacheln des Standpunkts, ``views`` schlüsselt je
Blick auf.

    uv run python scripts/overlay_offset.py pair <cloud.png> <pano.png> [--out r.json]
    uv run python scripts/overlay_offset.py batch <verzeichnis> [--out r.json]
    Optionen beider Befehle: --tile <px> (Vorgabe 96), --min-peak <ncc> (Vorgabe 0,3)

Nur NumPy/Pillow (Abhängigkeiten des Importwerkzeugs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

F64 = npt.NDArray[np.float64]
U8 = npt.NDArray[np.uint8]
BACKGROUND_RGB = (17, 17, 17)
MAX_MEDIAN_PX = 2.0
MIN_TILES = 12
MIN_STATIONS = 3
MIN_PEAK_DIST_PX = 2.0
MAX_PEAK_MARGIN = 0.05
UNIQUE_SEARCH = 24
# ``<id>-cloud.png`` (ein Blick) oder ``<id>-<n>-cloud.png`` (Blick n).
VIEW_FILE = re.compile(r"^(?P<sid>.+?)(?:-(?P<view>\d+))?-cloud\.png$")


def load_rgb(path: Path) -> U8:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def to_gray(rgb: U8) -> F64:
    return np.asarray(rgb.astype(np.float64) @ np.array([0.299, 0.587, 0.114]), dtype=np.float64)


def gradient_magnitude(gray: F64) -> F64:
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) / 2
    gy[1:-1, :] = (gray[2:, :] - gray[:-2, :]) / 2
    return np.asarray(np.hypot(gx, gy), dtype=np.float64)


def coverage_mask(
    rgb: U8, background: tuple[int, int, int] = BACKGROUND_RGB, tol: int = 6
) -> npt.NDArray[np.bool_]:
    diff = np.abs(rgb.astype(np.int16) - np.array(background, dtype=np.int16))
    return np.asarray(np.any(diff > tol, axis=2))


def _ncc(a: F64, b: F64) -> float:
    a0 = a - a.mean()
    b0 = b - b.mean()
    den = float(np.sqrt((a0 * a0).sum() * (b0 * b0).sum()))
    return float((a0 * b0).sum() / den) if den > 0 else 0.0


def _parabola(left: float, centre: float, right: float) -> float:
    den = left - 2 * centre + right
    return 0.0 if den == 0 else float(np.clip(0.5 * (left - right) / den, -0.5, 0.5))


def _tile_scores(ga: F64, gb: F64, x0: int, y0: int, tile: int, search: int) -> F64:
    """NCC-Gitter, Form ``(2*search+1, 2*search+1)``, für alle ganzzahligen Verschiebungen
    (dx, dy) in [-search, search]^2 zwischen der Wolken-Kachel bei (x0, y0) und dem Panorama."""
    a = ga[y0 : y0 + tile, x0 : x0 + tile]
    n = 2 * search + 1
    scores = np.full((n, n), -1.0)
    for j, dy in enumerate(range(-search, search + 1)):
        for i, dx in enumerate(range(-search, search + 1)):
            scores[j, i] = _ncc(a, gb[y0 + dy : y0 + dy + tile, x0 + dx : x0 + dx + tile])
    return scores


def _peak_from_scores(scores: F64, search: int) -> tuple[float, float, float, int, int]:
    """(dx, dy, Spitze, i, j): Subpixel-Verschiebung, Spitzenwert und Gitterindex (i, j) des
    ganzzahligen Maximums (vor Subpixel-Verfeinerung) in ``scores``."""
    n = scores.shape[0]
    jj, ii = np.unravel_index(int(np.argmax(scores)), scores.shape)
    j, i = int(jj), int(ii)
    peak = float(scores[j, i])
    fx = _parabola(float(scores[j, i - 1]), peak, float(scores[j, i + 1])) if 0 < i < n - 1 else 0.0
    fy = _parabola(float(scores[j - 1, i]), peak, float(scores[j + 1, i])) if 0 < j < n - 1 else 0.0
    return i - search + fx, j - search + fy, peak, i, j


def tile_offset(
    ga: F64, gb: F64, x0: int, y0: int, tile: int, search: int
) -> tuple[float, float, float]:
    """(dx, dy, Spitze): Inhalt der Wolken-Kachel bei (x0, y0) liegt im Panorama bei
    (x0+dx, y0+dy)."""
    scores = _tile_scores(ga, gb, x0, y0, tile, search)
    dx, dy, peak, _i, _j = _peak_from_scores(scores, search)
    return dx, dy, peak


def _box_sums(padded_cumsum: F64, tile: int) -> F64:
    """Summe über jedes ``tile``x``tile``-Fenster von ``region`` aus dessen Integralbild
    ``padded_cumsum`` (``region.cumsum(0).cumsum(1)``, oben/links mit einer Nullzeile/-spalte
    gepolstert) — Standard-Trick, um Fenstersummen in O(1) je Fenster statt O(tile^2) zu
    bekommen."""
    return (
        padded_cumsum[tile:, tile:]
        - padded_cumsum[:-tile, tile:]
        - padded_cumsum[tile:, :-tile]
        + padded_cumsum[:-tile, :-tile]
    )


def _wide_scores(
    ga: F64, gb: F64, x0: int, y0: int, tile: int, h: int, w: int, unique_search: int
) -> tuple[F64, int, int]:
    """NCC-Gitter über ein deutlich breiteres Fenster [dx_lo, dx_hi] x [dy_lo, dy_hi] als das
    Mess-Fenster ``±search``, an den Bildrand geklemmt: nahe am Rand ist das Fenster
    entsprechend kleiner (Kachelraster bleibt unverändert bei ``±search`` Rand).
    ``scores[j, i]`` gehört zur ganzzahligen Verschiebung ``(dx_lo + i, dy_lo + j)``.

    Vektorisiert statt einer Python-Schleife über bis zu (2*unique_search+1)^2 Verschiebungen
    (bei ``unique_search=24`` maß eine erste, nicht vektorisierte Fassung 25,8 s statt 9,4 s für
    ein 1920x1080-Bildpaar, siehe Task-6-Report): ``sliding_window_view`` liefert alle
    Kandidaten-Kacheln aus ``gb`` als eine Ansicht, der Kreuzterm ``sum(a0*b)`` läuft dann als
    eine Matrixmultiplikation (``tensordot``) statt als Python-Schleife über einzelne NCC-Aufrufe.
    Mittelwert/Varianz von ``b`` kommen aus einem Integralbild (O(1) je Fenster) statt aus den
    Fenstern selbst — das spart die zusätzlichen dichten Arrays für ``b - mean(b)`` und
    ``b**2``, nicht aber die eine Materialisierung von ``windows`` selbst: ``tensordot``
    reshaped die (nicht zusammenhängende) Fenster-Ansicht auf ``(ny*nx, tile*tile)``, was eine
    Kopie erzwingt (bei ``unique_search=24``, ``tile=96`` gemessen ~177 MB Spitzenspeicher je
    Kachel, kurzlebig, siehe Task-6-Report)."""
    a = ga[y0 : y0 + tile, x0 : x0 + tile]
    dx_lo = max(-unique_search, -x0)
    dx_hi = min(unique_search, w - tile - x0)
    dy_lo = max(-unique_search, -y0)
    dy_hi = min(unique_search, h - tile - y0)
    region = gb[y0 + dy_lo : y0 + dy_hi + tile, x0 + dx_lo : x0 + dx_hi + tile]

    a0 = a - a.mean()
    denom_a = float(np.sqrt((a0 * a0).sum()))
    if denom_a == 0:
        return np.zeros((dy_hi - dy_lo + 1, dx_hi - dx_lo + 1)), dx_lo, dy_lo

    windows = sliding_window_view(region, (tile, tile))  # Form (ny, nx, tile, tile)
    cross = np.tensordot(windows, a0, axes=([2, 3], [0, 1]))  # sum(a0*b) je Fenster

    ii1 = np.pad(region.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    ii2 = np.pad((region * region).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sum_b = _box_sums(ii1, tile)
    sumsq_b = _box_sums(ii2, tile)
    var_b = sumsq_b - (sum_b * sum_b) / (tile * tile)
    denom = denom_a * np.sqrt(np.clip(var_b, 0, None))
    scores = np.asarray(np.where(denom > 0, cross / denom, 0.0), dtype=np.float64)
    return scores, dx_lo, dy_lo


def _globally_unique(
    scores_wide: F64,
    dx_lo: int,
    dy_lo: int,
    best_dx: int,
    best_dy: int,
    best_score: float,
    min_dist_px: float = MIN_PEAK_DIST_PX,
    margin: float = MAX_PEAK_MARGIN,
) -> bool:
    """True, wenn die im engen ``±search``-Fenster gefundene ganzzahlige Verschiebung
    ``(best_dx, best_dy)`` mit Spitzenwert ``best_score`` auch im deutlich breiteren
    ``scores_wide``-Fenster eindeutig ist: (a) kein Eintrag in ``scores_wide`` übertrifft
    ``best_score`` (die enge Spitze ist auch das globale Maximum), UND (b) kein zweites lokales
    Maximum ≥ ``min_dist_px`` von (best_dx, best_dy) entfernt erreicht NCC ≥ ``best_score`` -
    ``margin``. Periodische Muster (Fliesenraster, Fensterprofile) erzeugen sonst eine zweite,
    ebenso gute Entsprechung außerhalb des engen Mess-Fensters, die dort selbst unsichtbar
    bliebe."""
    if scores_wide.max() > best_score + 1e-9:
        return False
    ny, nx = scores_wide.shape
    for jj in range(ny):
        for ii in range(nx):
            dx = dx_lo + ii
            dy = dy_lo + jj
            if dx == best_dx and dy == best_dy:
                continue
            if np.hypot(dx - best_dx, dy - best_dy) < min_dist_px:
                continue
            neighbours = scores_wide[max(jj - 1, 0) : jj + 2, max(ii - 1, 0) : ii + 2]
            if scores_wide[jj, ii] < neighbours.max():
                continue  # kein lokales Maximum an dieser Stelle
            if scores_wide[jj, ii] >= best_score - margin:
                return False
    return True


def measure_offsets(
    cloud_rgb: U8,
    pano_rgb: U8,
    tile: int = 96,
    search: int = 6,
    min_cov: float = 0.9,
    min_std: float = 2.0,
    min_peak: float = 0.3,
    unique_search: int = UNIQUE_SEARCH,
) -> dict[str, Any]:
    if cloud_rgb.shape != pano_rgb.shape:
        raise ValueError(f"Bildgrößen unterscheiden sich: {cloud_rgb.shape} vs. {pano_rgb.shape}")
    h, w = cloud_rgb.shape[:2]
    ga = gradient_magnitude(to_gray(cloud_rgb))
    gb = gradient_magnitude(to_gray(pano_rgb))
    cov = coverage_mask(cloud_rgb)
    records: list[dict[str, Any]] = []
    for y0 in range(search, h - tile - search + 1, tile):
        for x0 in range(search, w - tile - search + 1, tile):
            if cov[y0 : y0 + tile, x0 : x0 + tile].mean() < min_cov:
                continue
            ta = ga[y0 : y0 + tile, x0 : x0 + tile]
            tb = gb[y0 : y0 + tile, x0 : x0 + tile]
            if ta.std() < min_std or tb.std() < min_std:
                continue
            scores = _tile_scores(ga, gb, x0, y0, tile, search)
            dx, dy, peak, i, j = _peak_from_scores(scores, search)
            n = scores.shape[0]
            record: dict[str, Any] = {
                "x": x0,
                "y": y0,
                "dx": dx,
                "dy": dy,
                "peak": peak,
                "px": float(np.hypot(dx, dy)),
                "valid": True,
                "reason": None,
            }
            if peak < min_peak:
                record["valid"] = False
                record["reason"] = "lowPeak"
            elif i in (0, n - 1) or j in (0, n - 1):
                # Randlage: wahrer Versatz vermutlich außerhalb des Suchfensters.
                record["valid"] = False
                record["reason"] = "border"
            else:
                best_dx_int, best_dy_int = i - search, j - search
                scores_wide, dx_lo, dy_lo = _wide_scores(ga, gb, x0, y0, tile, h, w, unique_search)
                if not _globally_unique(scores_wide, dx_lo, dy_lo, best_dx_int, best_dy_int, peak):
                    # Im ±unique_search-Fenster gibt es eine ebenso gute oder
                    # bessere Entsprechung -> im engen Fenster unsichtbar mehrdeutig.
                    record["valid"] = False
                    record["reason"] = "ambiguous"
            records.append(record)
    params = {
        "tile": tile,
        "search": search,
        "minCov": min_cov,
        "minStd": min_std,
        "minPeak": min_peak,
        "uniqueSearch": unique_search,
    }
    return {**_summary([r for r in records if r["valid"]], params), "tileDetails": records}


def _summary(valid: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    """Kennzahlen über gültige Kacheln (Median/p90 von |(dx, dy)|, Kriterium siehe Modulkopf)."""
    mags = np.array([t["px"] for t in valid])
    median = float(np.median(mags)) if valid else None
    return {
        "tiles": len(valid),
        "medianPx": median,
        "p90Px": float(np.percentile(mags, 90)) if valid else None,
        "medianDx": float(np.median([t["dx"] for t in valid])) if valid else None,
        "medianDy": float(np.median([t["dy"] for t in valid])) if valid else None,
        "ok": bool(len(valid) >= MIN_TILES and median is not None and median <= MAX_MEDIAN_PX),
        "params": params,
    }


def pool_views(views: list[tuple[str | None, dict[str, Any]]]) -> dict[str, Any]:
    """Fasst mehrere Blickrichtungen eines Standpunkts zusammen: Kacheln
    addiert, Median/p90/``ok`` über alle gültigen Kacheln aller Blicke. ``views`` sind Paare
    (Blicknummer oder ``None`` beim alten Namensschema, ``measure_offsets``-Ergebnis) mit
    gleichen Parametern. ``views`` im Ergebnis schlüsselt je Blick auf, ``tileDetails`` trägt
    die Blicknummer je Kachel."""
    details = [{**t, "view": view} for view, r in views for t in r["tileDetails"]]
    pooled = _summary([t for t in details if t["valid"]], views[0][1]["params"])
    pooled["views"] = [
        {"view": view, **{k: v for k, v in r.items() if k not in ("tileDetails", "params")}}
        for view, r in views
    ]
    pooled["tileDetails"] = details
    return pooled


def _without_tile_details(result: dict[str, Any]) -> dict[str, Any]:
    """Kurzfassung eines ``measure_offsets``-Ergebnisses für die Konsolenausgabe (ohne die u. U.
    lange ``tileDetails``-Liste)."""
    return {k: v for k, v in result.items() if k != "tileDetails"}


def _load_rgb_or_exit(path: Path) -> U8:
    """Wie ``load_rgb``, aber mit lesbarer Fehlermeldung statt Rohtraceback für die CLI."""
    try:
        return load_rgb(path)
    except OSError as exc:
        raise SystemExit(f"Bild nicht lesbar: {path}: {exc}") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="overlay_offset")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="ein Bildpaar messen")
    p.add_argument("cloud", type=Path)
    p.add_argument("pano", type=Path)
    b = sub.add_parser(
        "batch", help="alle <id>[-<n>]-cloud.png/-pano.png eines Ordners, je Standpunkt gepoolt"
    )
    b.add_argument("dir", type=Path)
    for sp in (p, b):
        sp.add_argument("--out", type=Path)
        sp.add_argument("--tile", type=int, default=96, help="Kachelgröße in px (Vorgabe 96)")
        sp.add_argument(
            "--min-peak", type=float, default=0.3, help="Mindest-NCC-Spitze (Vorgabe 0,3)"
        )
    a = ap.parse_args(argv)
    opts = {"tile": a.tile, "min_peak": a.min_peak}

    if a.cmd == "pair":
        res = measure_offsets(_load_rgb_or_exit(a.cloud), _load_rgb_or_exit(a.pano), **opts)
        ok = bool(res["ok"])
        summary = json.dumps(_without_tile_details(res), indent=2)
    else:
        grouped: dict[str, list[tuple[str | None, dict[str, Any]]]] = {}
        for cloud in sorted(a.dir.glob("*-cloud.png")):
            m = VIEW_FILE.match(cloud.name)
            pano = cloud.with_name(cloud.name.removesuffix("-cloud.png") + "-pano.png")
            if m and pano.exists():
                r = measure_offsets(_load_rgb_or_exit(cloud), _load_rgb_or_exit(pano), **opts)
                grouped.setdefault(m["sid"], []).append((m["view"], r))
        stations: dict[str, Any] = {sid: pool_views(v) for sid, v in grouped.items()}
        passed = sum(1 for r in stations.values() if r["ok"])
        ok = passed >= MIN_STATIONS
        res = {"stations": stations, "passed": passed, "ok": ok}
        summary = json.dumps({s: _without_tile_details(r) for s, r in stations.items()}, indent=2)
    print(summary, file=sys.stderr)
    if a.out:
        a.out.write_text(json.dumps(res, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

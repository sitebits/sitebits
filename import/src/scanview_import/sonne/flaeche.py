"""Fläche aus der Flächenmessung: Ebene, Stichpunkte im Raster, Projektion aufs Mesh, Normalenseite,
Rauheit.

Eingabe sind die Ecken in Weltkoordinaten (Z oben), wie sie die Flächenmessung exportiert
(`{"kind": "area", "points": [[x, y, z], …]}`). Gerechnet wird im Paket-Lokalrahmen (Welt -
originWorld).
Selbstverschattung: Stichpunkte liegen auf dem Mesh, der Strahlursprung wird entlang der Normalen
versetzt und
Treffer näher als der Mindestabstand (> p95 der Rauheit) zählen nicht. Die Normalenseite ist die mit
mehr freiem
Himmel, nicht die aus Kamerapositionen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .strahlen import Szene

VERSATZ_M = 0.01
MIN_ABSTAND_M = 0.02
SUCHWEITE_M = 0.25


@dataclass(frozen=True)
class Flaeche:
    ecken: np.ndarray  # (K, 3) lokal
    normale: np.ndarray  # Einheitsvektor, Gitter
    achse_u: np.ndarray
    achse_v: np.ndarray
    punkte_uv: np.ndarray  # (P, 2) in der Ebene
    punkte: np.ndarray  # (P, 3) auf dem Mesh (lokal)
    ursprung: np.ndarray  # (P, 3) entlang der Normalen versetzt
    tnear: float
    rauheit_p95: float
    flaeche_m2: float
    ohne_mesh: int
    normalen_streuung_grad: float  # mittlere Abweichung der Dreiecksnormalen von der Ebenennormalen
    ebenheit_m: float
    seite_frei: tuple[float, float]  # freier Himmel (gewählte Seite, Gegenseite)


def ecken_lesen(pfad: Path) -> np.ndarray:
    """Flächenmessung (`points`, auch `daten.points`) oder reine Eckenliste → (K, 3) Welt."""
    d = json.loads(pfad.read_text())
    if isinstance(d, dict):
        if "daten" in d:
            d = d["daten"]
        if d.get("kind", "area") != "area":
            raise ValueError(f"Messung ist keine Fläche: {d.get('kind')}")
        d = d["points"]
    ecken = np.asarray(d, dtype=np.float64)
    if ecken.ndim != 2 or ecken.shape[1] != 3 or len(ecken) < 3 or not np.isfinite(ecken).all():
        raise ValueError("Fläche braucht mindestens drei Ecken [x, y, z]")
    return ecken


def ebene(ecken: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(Schwerpunkt, Normale mit z ≥ 0, u, v); Achsen wie `achsen`."""
    c = ecken.mean(axis=0)
    _, s, vt = np.linalg.svd(ecken - c)
    if s[1] < 1e-6:
        raise ValueError("Fläche ist entartet (Ecken auf einer Linie)")
    n = vt[2] / np.linalg.norm(vt[2])
    if n[2] < 0 or (abs(n[2]) < 1e-9 and (n[1] < 0 or (n[1] == 0 and n[0] < 0))):
        n = -n
    return c, n, *achsen(n)


def achsen(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Achsen in der Ebene: u waagerecht; bei fast waagerechten Flächen (< 10°) u = Ost."""
    if np.hypot(n[0], n[1]) < np.sin(np.radians(10.0)):
        u = np.array([1.0, 0.0, 0.0]) - n[0] * n
    else:
        u = np.cross([0.0, 0.0, 1.0], n)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def im_polygon(p: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Gerade/ungerade-Regel für (N, 2) Punkte im Polygon (K, 2)."""
    x, y = p[:, 0][:, None], p[:, 1][:, None]
    x0, y0 = poly[:, 0][None, :], poly[:, 1][None, :]
    x1, y1 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    kreuzt = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return np.asarray((kreuzt & (x < xs)).sum(axis=1) % 2 == 1)


def stichpunkte(poly: np.ndarray, raster_m: float) -> np.ndarray:
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    us = np.arange(lo[0] + raster_m / 2, hi[0], raster_m)
    vs = np.arange(lo[1] + raster_m / 2, hi[1], raster_m)
    g = np.stack(np.meshgrid(us, vs, indexing="xy"), axis=-1).reshape(-1, 2)
    return np.asarray(g[im_polygon(g, poly)])


def _freier_himmel(szene: Szene, p: np.ndarray, n: np.ndarray) -> float:
    """Anteil freier Himmelsrichtungen (über dem Horizont) an der Halbkugel um n."""
    k = np.arange(400) + 0.5
    z = 1 - k / 400
    phi = np.pi * (1 + 5**0.5) * k
    r = np.sqrt(1 - z**2)
    lokal = np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)
    u, v = achsen(n)
    d = lokal @ np.stack([u, v, n])
    himmel = d[:, 2] > 0
    if not himmel.any():
        return 0.0
    d = d[himmel]
    o = np.repeat(p + n * 0.05, len(d), axis=0)
    frei = ~szene.verdeckt(o, np.tile(d, (len(p), 1)), MIN_ABSTAND_M)
    return float(frei.sum() / (len(p) * len(lokal)))


def flaeche_aufbauen(ecken_lokal: np.ndarray, szene: Szene, raster_m: float) -> Flaeche:
    c, n, u, v = ebene(ecken_lokal)
    ebenheit = float(np.abs((ecken_lokal - c) @ n).max())
    poly = np.stack([(ecken_lokal - c) @ u, (ecken_lokal - c) @ v], axis=1)
    flaeche_m2 = float(
        0.5
        * abs(
            np.dot(poly[:, 0], np.roll(poly[:, 1], -1))
            - np.dot(poly[:, 1], np.roll(poly[:, 0], -1))
        )
    )
    uv = stichpunkte(poly, raster_m)
    if len(uv) == 0:
        raise ValueError("Fläche kleiner als das Stichpunktraster")
    eben = c + uv[:, :1] * u + uv[:, 1:] * v
    probe = eben[np.linspace(0, len(eben) - 1, min(len(eben), 25)).astype(int)]
    frei_plus, frei_minus = _freier_himmel(szene, probe, n), _freier_himmel(szene, probe, -n)
    if frei_minus > frei_plus:
        n, frei_plus, frei_minus = -n, frei_minus, frei_plus
        u, v = achsen(n)
        uv = np.stack([(eben - c) @ u, (eben - c) @ v], axis=1)
        poly = np.stack([(ecken_lokal - c) @ u, (ecken_lokal - c) @ v], axis=1)
    t, prim, ng = szene.treffer(
        eben + n * SUCHWEITE_M, np.tile(-n, (len(eben), 1)), 2 * SUCHWEITE_M
    )
    getroffen = prim >= 0
    if not getroffen.any():
        raise ValueError("Fläche liegt nicht auf dem Mesh (kein Treffer innerhalb ±25 cm)")
    abstand = SUCHWEITE_M - t[getroffen]
    punkte = eben[getroffen] + n * abstand[:, None]
    rauheit = float(np.percentile(np.abs(abstand), 95))
    if (
        len(punkte) >= 3
    ):  # Neigung/Ausrichtung aus dem Modell: Ebene durch die Treffer, Seite wie gewählt
        _, s, vt = np.linalg.svd(punkte - punkte.mean(axis=0))
        if s[1] > 1e-6:
            n = vt[2] * np.sign(vt[2] @ n)
    ng = ng[getroffen] * np.sign(ng[getroffen] @ n)[:, None]
    streuung = float(np.degrees(np.arccos(np.clip(ng @ n, -1, 1))).mean())
    return Flaeche(
        ecken=ecken_lokal,
        normale=n,
        achse_u=u,
        achse_v=v,
        punkte_uv=uv[getroffen],
        punkte=punkte,
        ursprung=punkte + n * VERSATZ_M,
        tnear=max(MIN_ABSTAND_M, 1.5 * rauheit),
        rauheit_p95=rauheit,
        flaeche_m2=flaeche_m2,
        ohne_mesh=int((~getroffen).sum()),
        normalen_streuung_grad=streuung,
        ebenheit_m=ebenheit,
        seite_frei=(frei_plus, frei_minus),
    )


def _nachbar_rauheit(uv: np.ndarray, z: np.ndarray, raster_m: float) -> float:
    """p95 der Höhenabweichung jedes Punkts vom Median seiner 8 Rasternachbarn.

    Nicht gegen eine Ebene gemessen, weil Gartenboden nicht eben ist.
    """
    ij = np.round((uv - uv.min(axis=0)) / raster_m).astype(np.int64)
    gitter = np.full(tuple(ij.max(axis=0) + 3), np.nan)
    gitter[ij[:, 0] + 1, ij[:, 1] + 1] = z
    nachbarn = np.stack(
        [
            gitter[ij[:, 0] + 1 + di, ij[:, 1] + 1 + dj]
            for di in (-1, 0, 1)
            for dj in (-1, 0, 1)
            if di or dj
        ]
    )
    da = ~np.isnan(nachbarn).all(axis=0)
    if not da.any():
        return 0.0
    abw = np.abs(z[da] - np.nanmedian(nachbarn[:, da], axis=0))
    return float(np.percentile(abw, 95))


def boden_aufbauen(
    ecken_lokal: np.ndarray, szene: Szene, raster_m: float, fenster_m: float = 1.0
) -> Flaeche:
    """Grundriss (horizontal) für den Gartenbericht: Stichpunkte im XY-Raster, Boden je Punkt als
    erster Treffer eines senkrechten Strahls von unten.

    Gesucht wird nur im Fenster ±`fenster_m` um die Ausgleichsebene der Ecken: Ohne Fenster fiele
    ein Strahl durch ein Loch im Boden-Mesh bis in die Baumkrone, und der Punkt sähe volle Sonne.
    Die Normale zeigt fest nach oben, denn gefragt ist, ob die Sonne den Boden erreicht, nicht die
    Einstrahlung auf eine geneigte Ebene.
    """
    n = np.array([0.0, 0.0, 1.0])
    u, v = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    c = ecken_lokal.mean(axis=0)
    a = np.c_[ecken_lokal[:, :2] - c[:2], np.ones(len(ecken_lokal))]
    koef = np.linalg.lstsq(a, ecken_lokal[:, 2], rcond=None)[0]
    ebenheit = float(np.abs(a @ koef - ecken_lokal[:, 2]).max())
    poly = ecken_lokal[:, :2] - c[:2]
    flaeche_m2 = float(
        0.5
        * abs(
            np.dot(poly[:, 0], np.roll(poly[:, 1], -1))
            - np.dot(poly[:, 1], np.roll(poly[:, 0], -1))
        )
    )
    uv = stichpunkte(poly, raster_m)
    if len(uv) == 0:
        raise ValueError("Fläche kleiner als das Stichpunktraster")
    z_ebene = np.c_[uv, np.ones(len(uv))] @ koef
    unten = np.c_[uv + c[:2], z_ebene - fenster_m]
    t, _, ng = szene.treffer(unten, np.tile(n, (len(unten), 1)), 2 * fenster_m)
    getroffen = np.isfinite(t)
    if not getroffen.any():
        raise ValueError(f"Kein Boden im Modell innerhalb ±{fenster_m:g} m um die Eckenebene")
    punkte = unten[getroffen] + n * t[getroffen, None]
    rauheit = _nachbar_rauheit(uv[getroffen], punkte[:, 2], raster_m)
    probe = punkte[np.linspace(0, len(punkte) - 1, min(len(punkte), 25)).astype(int)]
    streuung = float(np.degrees(np.arccos(np.clip(np.abs(ng[getroffen, 2]), 0, 1))).mean())
    return Flaeche(
        ecken=ecken_lokal,
        normale=n,
        achse_u=u,
        achse_v=v,
        punkte_uv=uv[getroffen],
        punkte=punkte,
        ursprung=punkte + n * VERSATZ_M,
        tnear=max(MIN_ABSTAND_M, 1.5 * rauheit),
        rauheit_p95=rauheit,
        flaeche_m2=flaeche_m2,
        ohne_mesh=int((~getroffen).sum()),
        normalen_streuung_grad=streuung,
        ebenheit_m=ebenheit,
        seite_frei=(_freier_himmel(szene, probe, n), 0.0),
    )

"""Rahmenkameramodell des Austauschformats: Verzeichnung, Inversion, Zuschnitt, Entzerrung.

Vorwärtsmodell (Brown-Conrady mit vier Radial-, zwei Tangentialtermen und Affinität/Scherung
b1/b2; Kamera-KS: X rechts, Y unten, Z Blickrichtung)::

    x = X/Z, y = Y/Z, r² = x² + y²
    x' = x(1 + K1r² + K2r⁴ + K3r⁶ + K4r⁸) + (P1(r² + 2x²) + 2P2xy)
    y' = y(1 + K1r² + K2r⁴ + K3r⁶ + K4r⁸) + (P2(r² + 2y²) + 2P1xy)
    u = w/2 + cx + x'f + x'B1 + y'B2
    v = h/2 + cy + y'f

Bildkoordinaten des Austauschformats: Ursprung in der linken oberen Ecke des Bildes, die Mitte des
Pixels (i, j) liegt bei (i + 0,5, j + 0,5). Die Koeffizienten werden **nicht** nach Namen auf
OpenCV abgebildet (andere Reihenfolge der Tangentialterme, K4 rational, B1/B2 fehlen dort).
Maßgeblich ist nicht die Formel, sondern `pruefe_referenzpaare`: Der Export schreibt je Sensor
Pixel aus der eigenen Projektion der Photogrammetrie-Software, und dieses Modell muss sie auf
0,1 px treffen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

Array = NDArray[np.float64]

REFERENZ_TOLERANZ_PX = 0.1
MIN_REFERENZPAARE = 8
NEWTON_ITERATIONEN = 20
NEWTON_TOLERANZ_PX = 0.01


class VerzeichnungFehler(ValueError):
    """Kalibrierung unbrauchbar oder Modell weicht von den Referenzpaaren ab."""


@dataclass(frozen=True)
class Kalibrierung:
    width: int
    height: int
    f: float
    cx: float = 0.0
    cy: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    k4: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    b1: float = 0.0
    b2: float = 0.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise VerzeichnungFehler("Sensorgröße muss positiv sein")
        werte = [getattr(self, feld.name) for feld in fields(self)]
        if not all(math.isfinite(float(w)) for w in werte):
            raise VerzeichnungFehler("Kalibrierwerte müssen endlich sein")
        if self.f <= 0:
            raise VerzeichnungFehler("Brennweite muss positiv sein")

    @classmethod
    def aus_export(cls, width: int, height: int, werte: dict[str, Any]) -> Kalibrierung:
        namen = ("f", "cx", "cy", "k1", "k2", "k3", "k4", "p1", "p2", "b1", "b2")
        fehlend = [n for n in namen if n not in werte]
        if fehlend:
            raise VerzeichnungFehler(f"Kalibrierung unvollständig: {', '.join(fehlend)}")
        if any(float(werte.get(n, 0.0)) != 0.0 for n in ("p3", "p4")):
            raise VerzeichnungFehler(
                "Kalibrierung mit p3/p4 ≠ 0 wird nicht unterstützt (höhere Tangentialterme) — "
                "ohne p3/p4 neu kalibrieren"
            )
        return cls(width=int(width), height=int(height), **{n: float(werte[n]) for n in namen})


@dataclass(frozen=True)
class Lochkamera:
    """Entzerrte Zielkamera: Hauptpunkt in der Bildmitte, quadratische Pixel."""

    width: int
    height: int
    f: float


def _radial(k: Kalibrierung, r2: Array) -> Array:
    return 1 + r2 * (k.k1 + r2 * (k.k2 + r2 * (k.k3 + r2 * k.k4)))


def _verzeichnet_normiert(k: Kalibrierung, x: Array, y: Array) -> tuple[Array, Array]:
    r2 = x * x + y * y
    radial = _radial(k, r2)
    xd = x * radial + (k.p1 * (r2 + 2 * x * x) + 2 * k.p2 * x * y)
    yd = y * radial + (k.p2 * (r2 + 2 * y * y) + 2 * k.p1 * x * y)
    return xd, yd


def verzeichnen(k: Kalibrierung, x: Array, y: Array) -> tuple[Array, Array]:
    """Normierte Kamera-KS-Koordinaten (X/Z, Y/Z) → Pixel im Originalbild."""
    xd, yd = _verzeichnet_normiert(k, np.asarray(x, float), np.asarray(y, float))
    u = k.width / 2 + k.cx + xd * k.f + xd * k.b1 + yd * k.b2
    v = k.height / 2 + k.cy + yd * k.f
    return u, v


def unverzeichnen(k: Kalibrierung, u: Array, v: Array) -> tuple[Array, Array]:
    """Pixel im Originalbild → normierte Koordinaten (Newton, Abbruch ohne Konvergenz)."""
    u = np.asarray(u, float)
    v = np.asarray(v, float)
    yd = (v - k.height / 2 - k.cy) / k.f
    xd = (u - k.width / 2 - k.cx - yd * k.b2) / (k.f + k.b1)
    x, y = xd.copy(), yd.copy()
    for _ in range(NEWTON_ITERATIONEN):
        fx, fy = _verzeichnet_normiert(k, x, y)
        ex, ey = fx - xd, fy - yd
        if np.max(np.hypot(ex, ey), initial=0.0) * k.f < NEWTON_TOLERANZ_PX * 1e-3:
            break
        r2 = x * x + y * y
        radial = _radial(k, r2)
        # Ableitung von radial nach r²
        dradial = k.k1 + r2 * (2 * k.k2 + r2 * (3 * k.k3 + r2 * 4 * k.k4))
        dxdx = radial + 2 * x * x * dradial + 6 * k.p1 * x + 2 * k.p2 * y
        dxdy = 2 * x * y * dradial + 2 * k.p1 * y + 2 * k.p2 * x
        dydx = 2 * x * y * dradial + 2 * k.p2 * x + 2 * k.p1 * y
        dydy = radial + 2 * y * y * dradial + 6 * k.p2 * y + 2 * k.p1 * x
        det = dxdx * dydy - dxdy * dydx
        x = x - (dydy * ex - dxdy * ey) / det
        y = y - (-dydx * ex + dxdx * ey) / det
    fx, fy = _verzeichnet_normiert(k, x, y)
    rest = np.max(np.hypot(fx - xd, fy - yd), initial=0.0) * k.f
    if not math.isfinite(rest) or rest > NEWTON_TOLERANZ_PX:
        raise VerzeichnungFehler(f"Inversion konvergiert nicht (Rest {rest:.3g} px)")
    return x, y


def pruefe_referenzpaare(k: Kalibrierung, paare: list[dict[str, Any]]) -> float:
    """Modell gegen die Referenzpaare des Exports prüfen; liefert die größte Abweichung in px."""
    if len(paare) < MIN_REFERENZPAARE:
        raise VerzeichnungFehler(
            f"Zu wenige Referenzpaare ({len(paare)} < {MIN_REFERENZPAARE}) — Export zu alt?"
        )
    ray = np.array([p["ray"] for p in paare], float)
    pixel = np.array([p["pixel"] for p in paare], float)
    u, v = verzeichnen(k, ray[:, 0], ray[:, 1])
    abweichung = np.hypot(u - pixel[:, 0], v - pixel[:, 1])
    groesste = float(np.max(abweichung))
    if not math.isfinite(groesste) or groesste > REFERENZ_TOLERANZ_PX:
        i = int(np.argmax(abweichung))
        raise VerzeichnungFehler(
            f"Referenzpaar {i} weicht {groesste:.3f} px von der exportierten Projektion ab "
            f"(Grenze {REFERENZ_TOLERANZ_PX} px) — Verzeichnungsmodell passt nicht"
        )
    return groesste


def _innen(k: Kalibrierung, x: Array, y: Array) -> bool:
    u, v = verzeichnen(k, x, y)
    return bool(
        np.all(u >= 0.5) and np.all(u <= k.width - 0.5)
        and np.all(v >= 0.5) and np.all(v <= k.height - 0.5)
    )


def _rand(ax: float, ay: float, n: int = 400) -> tuple[Array, Array]:
    t = np.linspace(-1, 1, n)
    eins = np.ones(n)
    x = np.concatenate([t * ax, t * ax, -ax * eins, ax * eins])
    y = np.concatenate([-ay * eins, ay * eins, t * ay, t * ay])
    return x, y


def zielkamera(k: Kalibrierung) -> Lochkamera:
    """Größtes zentriertes Rechteck im Seitenverhältnis des Sensors ohne leere Pixel."""
    halb_x = (k.width / 2) / k.f
    halb_y = (k.height / 2) / k.f
    unten, oben = 0.0, 2.0
    for _ in range(60):
        mitte = (unten + oben) / 2
        # Rand der Pixelmitten des Ziels: halbe Breite minus halbes Pixel.
        ax = (math.floor(2 * mitte * halb_x * k.f) / 2 - 0.5) / k.f
        ay = (math.floor(2 * mitte * halb_y * k.f) / 2 - 0.5) / k.f
        if ax > 0 and ay > 0 and _innen(k, *_rand(ax, ay)):
            unten = mitte
        else:
            oben = mitte
    breite = math.floor(2 * unten * halb_x * k.f)
    hoehe = math.floor(2 * unten * halb_y * k.f)
    if breite < 16 or hoehe < 16:
        raise VerzeichnungFehler("Entzerrtes Bild wäre leer — Kalibrierung prüfen")
    return Lochkamera(width=breite, height=hoehe, f=k.f)


def remap_gitter(
    k: Kalibrierung, ziel: Lochkamera
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Je Zielpixelmitte die Quellkoordinate (Pixelkoordinaten des Austauschformats), float32."""
    gu = np.empty((ziel.height, ziel.width), np.float32)
    gv = np.empty((ziel.height, ziel.width), np.float32)
    x = (np.arange(ziel.width) + 0.5 - ziel.width / 2) / ziel.f
    for start in range(0, ziel.height, 512):
        zeilen = np.arange(start, min(start + 512, ziel.height))
        y = (zeilen + 0.5 - ziel.height / 2) / ziel.f
        xx, yy = np.meshgrid(x, y)
        u, v = verzeichnen(k, xx, yy)
        gu[zeilen] = u
        gv[zeilen] = v
    return gu, gv


def entzerre_bild(
    bild: NDArray[np.uint8], gitter: tuple[NDArray[np.float32], NDArray[np.float32]]
) -> NDArray[np.uint8]:
    """Kubische Interpolation je Kanal; Pixelmitte i + 0,5 ↔ Array-Index i."""
    gu, gv = gitter
    koordinaten = np.stack([gv - 0.5, gu - 0.5])
    kanaele = [
        ndimage.map_coordinates(bild[..., c].astype(np.float32), koordinaten, order=3,
                                mode="nearest", prefilter=True)
        for c in range(bild.shape[2])
    ]
    return np.clip(np.rint(np.stack(kanaele, axis=-1)), 0, 255).astype(np.uint8)

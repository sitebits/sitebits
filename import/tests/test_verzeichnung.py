"""Rahmenkameramodell des Austauschformats, Inversion, Zuschnitt und Entzerrung."""

import dataclasses

import numpy as np
import pytest

from scanview_import.verzeichnung import (
    Kalibrierung,
    VerzeichnungFehler,
    entzerre_bild,
    pruefe_referenzpaare,
    remap_gitter,
    unverzeichnen,
    verzeichnen,
    zielkamera,
)

# Beispielprojekt (Drohne), Sensor 0 (Ausrichtung, angepasst), 3840 x 2160.
DROHNE = Kalibrierung(
    width=3840, height=2160, f=2836.0741148025791, cx=-3.2366462910117906,
    cy=-1.3946326776059215, k1=0.15335393066876787, k2=-0.50697268973724219,
    k3=0.60985535664330548, p1=8.2639731646303173e-05, p2=0.00030950037846934542,
)
OHNE = Kalibrierung(width=400, height=300, f=350.0)


def _raster(k: Kalibrierung, n: int = 41) -> tuple[np.ndarray, np.ndarray]:
    u, v = np.meshgrid(np.linspace(0, k.width, n), np.linspace(0, k.height, n))
    return u.ravel(), v.ravel()


def test_ohne_verzeichnung_ist_lochkamera():
    u, v = verzeichnen(OHNE, np.array([0.0, 0.1]), np.array([0.0, -0.2]))
    np.testing.assert_allclose(u, [200.0, 235.0])
    np.testing.assert_allclose(v, [150.0, 80.0])


def test_formel_wie_austauschformat():
    k = Kalibrierung(width=100, height=80, f=50.0, cx=1.0, cy=-2.0, k1=0.1, k2=0.01,
                     k3=0.001, k4=0.0001, p1=0.002, p2=-0.003, b1=0.5, b2=0.25)
    x, y = 0.3, -0.2
    r2 = x * x + y * y
    radial = 1 + 0.1 * r2 + 0.01 * r2**2 + 0.001 * r2**3 + 0.0001 * r2**4
    xd = x * radial + 0.002 * (r2 + 2 * x * x) + 2 * -0.003 * x * y
    yd = y * radial + -0.003 * (r2 + 2 * y * y) + 2 * 0.002 * x * y
    u, v = verzeichnen(k, np.array([x]), np.array([y]))
    assert u[0] == pytest.approx(50 + 1 + xd * 50 + xd * 0.5 + yd * 0.25, abs=1e-12)
    assert v[0] == pytest.approx(40 - 2 + yd * 50, abs=1e-12)


@pytest.mark.parametrize("k", [DROHNE, dataclasses.replace(DROHNE, width=2160, height=3840)])
def test_hin_und_rueck_unter_hundertstel_pixel(k):
    u, v = _raster(k)
    x, y = unverzeichnen(k, u, v)
    u2, v2 = verzeichnen(k, x, y)
    assert np.max(np.hypot(u2 - u, v2 - v)) <= 0.01


def _paare(k: Kalibrierung, verschiebung: float = 0.0) -> list[dict]:
    xs, ys = np.meshgrid(np.linspace(-0.6, 0.6, 5), np.linspace(-0.35, 0.35, 4))
    u, v = verzeichnen(k, xs.ravel(), ys.ravel())
    return [{"ray": [float(a), float(b)], "pixel": [float(p) + verschiebung, float(q)]}
            for a, b, p, q in zip(xs.ravel(), ys.ravel(), u, v, strict=True)]


def test_referenzpaare_bestanden():
    assert pruefe_referenzpaare(DROHNE, _paare(DROHNE)) <= 1e-9


def test_vertauschte_tangentialterme_fallen_auf():
    vertauscht = dataclasses.replace(DROHNE, p1=DROHNE.p2, p2=DROHNE.p1)
    with pytest.raises(VerzeichnungFehler, match="Referenzpaar"):
        pruefe_referenzpaare(vertauscht, _paare(DROHNE))


def test_halbes_pixel_versatz_faellt_auf():
    with pytest.raises(VerzeichnungFehler):
        pruefe_referenzpaare(DROHNE, _paare(DROHNE, verschiebung=0.5))


def test_zu_wenige_referenzpaare():
    with pytest.raises(VerzeichnungFehler, match="Referenzpaare"):
        pruefe_referenzpaare(DROHNE, _paare(DROHNE)[:3])


def test_ungueltige_kalibrierung():
    with pytest.raises(VerzeichnungFehler):
        Kalibrierung(width=100, height=80, f=0.0)
    with pytest.raises(VerzeichnungFehler):
        Kalibrierung(width=100, height=80, f=50.0, k1=float("nan"))


def test_hoehere_tangentialterme_werden_mit_grund_abgelehnt():
    namen = ("cx", "cy", "k1", "k2", "k3", "k4", "p1", "p2", "b1", "b2")
    werte = {n: 0.0 for n in namen} | {"f": 300.0}
    assert Kalibrierung.aus_export(240, 160, werte).f == 300.0
    assert Kalibrierung.aus_export(240, 160, werte | {"p3": 0.0, "p4": 0.0}).f == 300.0
    with pytest.raises(VerzeichnungFehler, match=r"p3/p4.*nicht unterstützt"):
        Kalibrierung.aus_export(240, 160, werte | {"p3": 1e-4, "p4": 0.0})
    with pytest.raises(VerzeichnungFehler, match=r"p3/p4"):
        Kalibrierung.aus_export(240, 160, werte | {"p4": -2e-5})


@pytest.mark.parametrize("k", [DROHNE, dataclasses.replace(DROHNE, width=2160, height=3840)])
def test_zuschnitt_ohne_leere_pixel_und_fast_maximal(k):
    ziel = zielkamera(k)
    assert ziel.f == k.f
    assert (ziel.width >= ziel.height) == (k.width >= k.height)
    gu, gv = remap_gitter(k, ziel)
    # Jedes Zielpixel stammt aus dem Quellbild (Pixelmitten 0,5 bis Breite minus 0,5).
    assert gu.min() >= 0.5 - 1e-6 and gu.max() <= k.width - 0.5 + 1e-6
    assert gv.min() >= 0.5 - 1e-6 and gv.max() <= k.height - 0.5 + 1e-6
    # Fast maximal: 1 % größer ragt aus dem Bild.
    groesser = dataclasses.replace(
        ziel, width=int(ziel.width * 1.01), height=int(ziel.height * 1.01)
    )
    gu, gv = remap_gitter(k, groesser)
    assert gu.min() < 0.5 or gu.max() > k.width - 0.5 or gv.min() < 0.5 or gv.max() > k.height - 0.5


def test_ohne_verzeichnung_bild_unveraendert():
    rng = np.random.default_rng(1)
    bild = rng.integers(0, 256, (300, 400, 3), dtype=np.uint8)
    ziel = zielkamera(OHNE)
    assert (ziel.width, ziel.height) == (399, 299) or (ziel.width, ziel.height) == (400, 300)
    aus = entzerre_bild(bild, remap_gitter(OHNE, ziel))
    assert aus.dtype == np.uint8 and aus.shape == (ziel.height, ziel.width, 3)
    versatz_u = (OHNE.width - ziel.width) // 2
    versatz_v = (OHNE.height - ziel.height) // 2
    innen = bild[versatz_v:versatz_v + ziel.height, versatz_u:versatz_u + ziel.width]
    assert np.max(np.abs(aus[2:-2, 2:-2].astype(int) - innen[2:-2, 2:-2].astype(int))) <= 1


def test_entzerrung_macht_gerade_linie_gerade():
    """Gerade Kante in der Welt: im Quellbild gekrümmt, nach Entzerrung gerade."""
    k = dataclasses.replace(DROHNE, width=960, height=540, f=709.0)
    gu, gv = np.meshgrid(np.arange(k.width) + 0.5, np.arange(k.height) + 0.5)
    _, y = unverzeichnen(k, gu, gv)
    bild = np.where(y < 0.25, 255, 0).astype(np.uint8)[..., None].repeat(3, axis=2)
    ziel = zielkamera(k)
    aus = entzerre_bild(bild, remap_gitter(k, ziel))[..., 0].astype(float)
    # Zeile des 50-%-Übergangs je Spalte: überall gleich (gerade, waagrecht).
    kante = [np.argmax(aus[:, c] < 128) for c in range(10, ziel.width - 10, 20)]
    assert max(kante) - min(kante) <= 1
    erwartet = ziel.height / 2 + 0.25 * ziel.f - 0.5
    assert abs(np.median(kante) - erwartet) <= 1.0

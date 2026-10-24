"""Sonne-Rechenkern: Konventionen, Richtungskarte, Selbstverschattung, Schatten, Ertrag."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pvlib")
pytest.importorskip("embreex")

import pandas as pd
from sonne_szene import platte, quader, strahl_trifft_quader, zusammen

from scanview_import.sonne import ertrag, geo, pvgis
from scanview_import.sonne.flaeche import ebene, flaeche_aufbauen, im_polygon
from scanview_import.sonne.richtungskarte import (
    Raster,
    direkt_funktion,
    karte_rechnen,
    nachschlagen,
    sichtanteile,
)
from scanview_import.sonne.strahlen import Szene

KONV = 1.5626  # Potsdam, UTM 33


def test_azimut_pvgis_pvlib_beide_richtungen() -> None:
    assert geo.pvgis_nach_pvlib(0) == 180  # Süd
    assert geo.pvgis_nach_pvlib(-90) == 90  # Ost
    assert geo.pvgis_nach_pvlib(90) == 270  # West
    assert geo.pvgis_nach_pvlib(-180) == 0  # Nord
    for a in (0.0, 45.0, 90.0, 179.0, 180.0, 270.0, 359.0):
        assert geo.pvgis_nach_pvlib(geo.pvlib_nach_pvgis(a)) == pytest.approx(a)


def test_konvergenz_potsdam_und_gitter_geo() -> None:
    k = geo.konvergenz_grad("EPSG:25833", 52.394, 13.028)
    assert k == pytest.approx(
        1.56, abs=0.01
    )  # geografisch Nord liegt im Uhrzeigersinn von Gitter-Nord
    assert geo.konvergenz_grad("EPSG:25833", 52.4, 15.0) == pytest.approx(
        0.0, abs=1e-3
    )  # Mittelmeridian
    for a in (0.0, 1.0, 180.0, 359.5):
        assert geo.gitter_nach_geo(geo.geo_nach_gitter(a, k), k) == pytest.approx(a)
    # Gitter-Nord (0) ist geografisch 360 - k, also leicht westlich von Nord
    assert geo.gitter_nach_geo(0.0, k) == pytest.approx(360 - k)


def test_horizontprofil_konvention(tmp_path: Path) -> None:
    p = pvgis.Pvgis(tmp_path, offline=True)
    url = f"{pvgis.BASIS}printhorizon?lat=52.0&lon=13.0&outputformat=json"
    import hashlib

    datei = tmp_path / f"printhorizon-{hashlib.sha256(url.encode()).hexdigest()[:16]}.json"
    prof = [{"A": a, "H_hor": 5.0 if a == -90 else 1.0} for a in np.arange(-180, 181, 7.5)]
    datei.write_text(
        json.dumps(
            {"url": url, "abgerufen": "x", "antwort": {"outputs": {"horizon_profile": prof}}}
        )
    )
    az, h, _ = pvgis.horizont(p, 52.0, 13.0)
    assert h[np.argmin(abs(az - 90))] == 5.0  # A = -90 ist Osten → pvlib 90
    assert np.all(np.diff(az) > 0) and len(az) == 48


def test_offline_ohne_cache_bricht_ab(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="nicht im Cache"):
        pvgis.Pvgis(tmp_path, offline=True).abrufen("tmy", lat=1, lon=2)


def test_raster_raumwinkel_und_nachschlagen() -> None:
    r = Raster()
    assert r.raumwinkel().sum() == pytest.approx(2 * np.pi, rel=1e-9)
    k = np.zeros((len(r.azimute), len(r.hoehen)))
    k[0, :] = 1.0  # Zelle 0..2° Azimut
    assert nachschlagen(k, r, np.array([1.0, 0.0, 2.0, 181.0]), np.array([10.0] * 4)).tolist() == [
        1.0,
        0.5,
        0.5,
        0.0,
    ]
    assert nachschlagen(k, r, np.array([1.0]), np.array([-1.0]))[0] == 0.0


def test_polygon_und_ebene() -> None:
    poly = np.array([[0, 0], [2, 0], [2, 1], [0, 1]], float)
    assert im_polygon(np.array([[1, 0.5], [3, 0.5], [-0.1, 0.5]]), poly).tolist() == [
        True,
        False,
        False,
    ]
    _, n, u, v = ebene(
        np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], float)
    )  # Wand, Normale ±y
    assert abs(n[1]) == pytest.approx(1) and abs(u[2]) < 1e-9 and v[2] > 0.99
    _, n, u, v = ebene(
        np.array([[0, 0, 0], [1, 0, 0.01], [1, 1, 0.01], [0, 1, 0]], float)
    )  # fast flach
    assert u[0] > 0.99 and v[1] > 0.99  # Ost rechts, Nord oben


def _szene_platte(
    rauheit: float, *extra: tuple[np.ndarray, np.ndarray]
) -> tuple[Szene, np.ndarray]:
    pos, tri = zusammen(platte(3.0, rauheit), *extra)
    ecken = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], float)
    return Szene(pos, tri, ecken.mean(axis=0)), ecken


@pytest.mark.parametrize("rauheit", [0.0, 0.005, 0.02])
def test_vorflug_selbstverschattung_sonne_in_normalenrichtung(rauheit: float) -> None:
    szene, ecken = _szene_platte(rauheit)
    fl = flaeche_aufbauen(ecken, szene, 0.1)
    assert fl.normale[2] > 0.99 and len(fl.punkte) == 400 and fl.ohne_mesh == 0
    assert fl.rauheit_p95 <= rauheit + 1e-6
    senkrecht = szene.verdeckt(fl.ursprung, np.tile(fl.normale, (len(fl.ursprung), 1)), fl.tnear)
    assert senkrecht.mean() == 0.0
    # Auch über die Richtungskarte: Zenit und hohe Sonne frei
    karte = karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, Raster())
    assert karte[:, :, 30:].mean() < 0.005


def test_normalenseite_mit_mehr_himmel() -> None:
    # Südfassade eines Hauses: Die Ecken ergeben zunächst die Normale +y (ins Haus); gewählt wird
    # die Seite mit
    # mehr freiem Himmel, also nach außen (-y).
    haus = quader([-5, 0, 0], [5, 8, 6])
    szene = Szene(*haus, np.array([0, 0, 3.0]))
    ecken = np.array([[-1, 0, 2], [1, 0, 2], [1, 0, 4], [-1, 0, 4]], float)
    assert ebene(ecken)[1][1] > 0.99
    fl = flaeche_aufbauen(ecken, szene, 0.25)
    assert fl.normale[1] < -0.99 and fl.seite_frei[0] > 0.4 and fl.seite_frei[1] == 0.0
    assert geo.neigung_azimut(fl.normale) == pytest.approx((90.0, 180.0))


def test_himmelssicht_unendliche_wand() -> None:
    # Wand parallel zur y-Achse in 2 m Abstand, 2 m hoch, sehr lang: SVF = (1 + cos β)/2, β = 45°
    wand = quader([2.0, -300, 0], [2.1, 300, 2.0])
    szene, _ = _szene_platte(0.0, wand)
    fl = flaeche_aufbauen(
        np.array([[-0.05, -0.05, 0], [0.05, -0.05, 0], [0.05, 0.05, 0], [-0.05, 0.05, 0]]),
        szene,
        0.1,
    )
    karte = karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, Raster(1.0, 0.5))
    himmel, _ = sichtanteile((~karte).astype(np.float32), Raster(1.0, 0.5), fl.normale)
    assert himmel[0] == pytest.approx((1 + np.cos(np.radians(45))) / 2, abs=0.01)


def test_synthetische_szene_schattenzeiten_pm_ein_schritt() -> None:
    """Wand im Osten, Mast im Süden: Schattenwechsel über die Richtungskarte ±1 Viertelstunde."""
    wand = ([6.0, -30, 0], [6.3, 30, 4.0])
    mast = ([-0.3, -8.3, 0], [0.3, -7.7, 25.0])
    szene, _ = _szene_platte(0.005, quader(*wand), quader(*mast))
    klein = np.array([[-0.05, -0.05, 0], [0.05, -0.05, 0], [0.05, 0.05, 0], [-0.05, 0.05, 0]])
    fl = flaeche_aufbauen(klein, szene, 0.1)
    r = Raster()
    frei = (~karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, r)).astype(np.float32)
    f = direkt_funktion(frei.mean(axis=0), r, KONV, None)
    zeiten = pd.date_range("2026-01-01", "2026-12-31 23:45", freq="15min", tz="UTC")
    s = ertrag.sonnenstand(zeiten, 52.394, 13.028, 33.0)
    h, a = s["apparent_elevation"].to_numpy(), s["azimuth"].to_numpy()
    tag = h > 0.5
    karte_frei = f(a[tag], h[tag]) >= 0.5
    d = geo.richtung(np.asarray(geo.geo_nach_gitter(a[tag], KONV)), h[tag])
    o = fl.ursprung[0]
    soll = np.array(
        [not (strahl_trifft_quader(o, x, *wand) or strahl_trifft_quader(o, x, *mast)) for x in d]
    )
    assert (
        ~soll
    ).sum() > 500 and soll.sum() > 500  # beide Hindernisse werfen über das Jahr Schatten
    abweichend = np.flatnonzero(karte_frei != soll)
    # jede Abweichung liegt höchstens einen Zeitschritt neben einem Wechsel des Sollwerts
    wechsel = np.flatnonzero(np.diff(soll.astype(int)) != 0)
    for i in abweichend:
        assert np.min(np.abs(wechsel - i)) <= 1, f"Abweichung fern vom Schattenwechsel: Index {i}"
    assert len(abweichend) / len(soll) < 0.01


def _wetter_sommer() -> pd.DataFrame:
    zeit = pd.date_range("2026-06-21 00:00", periods=24, freq="1h", tz="UTC")
    s = ertrag.sonnenstand(zeit, 52.4, 13.0, 33.0)
    cosz = np.clip(np.cos(np.radians(s["apparent_zenith"].to_numpy())), 0, None)
    return pd.DataFrame(
        {
            "ghi": 900 * cosz + 100 * (cosz > 0),
            "dni": 900.0 * (cosz > 0),
            "dhi": 100.0 * (cosz > 0),
            "temp_air": 20.0,
            "wind_speed": 2.0,
        },
        index=zeit,
    )


def test_kappung_greift_im_sommer_mittags() -> None:
    w = _wetter_sommer()
    r = ertrag.rechnen(w, 52.4, 13.0, 33.0, 35, 180, ertrag.Anlage(2000.0, kappung_w=800.0))
    mittag = r.index.hour.isin([10, 11, 12])
    assert np.allclose(r.loc[mittag, "p_ac"], 800.0)
    assert (r.loc[mittag, "p_ac_ohne_kappung"] > 900).all()
    assert r["p_ac"].max() <= 800.0 + 1e-9
    frei = ertrag.rechnen(w, 52.4, 13.0, 33.0, 35, 180, ertrag.Anlage(2000.0))
    assert frei["p_ac"].max() > 1000


def test_verschattung_trennt_direkt_und_diffus() -> None:
    w = _wetter_sommer()
    a = ertrag.Anlage(800.0)
    frei = ertrag.rechnen(w, 52.4, 13.0, 33.0, 90, 180, a)
    nichts = ertrag.Verschattung(lambda az, h: np.zeros_like(h), himmel=0.5, horizontband=0.0)
    r = ertrag.rechnen(w, 52.4, 13.0, 33.0, 90, 180, a, nichts)
    assert r["direkt"].sum() == 0 and r["zirkumsolar"].sum() == 0
    assert r["isotrop"].sum() == pytest.approx(0.5 * frei["isotrop"].sum())
    assert r["boden"].sum() == pytest.approx(frei["boden"].sum())


def test_vertrauen_und_vorlage_vollstaendig() -> None:
    import re

    from scanview_import.sonne import bericht

    e = {"flaeche": {"ohne_mesh": 0, "stichpunkte": 100}}
    assert bericht.vertrauen(e)[0] == "geschätzt"
    e["flaeche"]["ohne_mesh"] = 2
    assert bericht.vertrauen(e)[0] == "unvollständig"  # Lücke > 1 % → keine nackte Zahl
    platzhalter = set(re.findall(r"\$(\w+)", bericht.VORLAGE.template))
    quelle = Path(bericht.__file__).read_text()
    werte = set(re.findall(r'^\s+"(\w+)": ', quelle[quelle.index("    werte = {") :], re.M))
    assert platzhalter <= werte, platzhalter - werte


def test_garten_kategorie_schwellen_und_grenzbereich() -> None:
    from scanview_import.sonne.garten import KATEGORIEN, kategorie

    h = np.array([0.0, 1.5, 2.0, 2.5, 3.39, 3.5, 5.5, 6.0, 6.5, 10.0])
    idx, grenz = kategorie(h)
    namen = [KATEGORIEN[i] for i in idx]
    assert namen == [
        "schattig",
        "schattig",
        "mäßig schattig",  # Schwelle zählt zur oberen Klasse
        "mäßig schattig",
        "halbschattig",
        "halbschattig",
        "halbschattig",
        "sonnig",
        "sonnig",
        "sonnig",
    ]
    assert grenz.tolist() == [False, False, True, False, True, False, False, True, False, False]


def _loch(pos: np.ndarray, tri: np.ndarray, lo: float, hi: float) -> np.ndarray:
    m = pos[tri].mean(axis=1)
    raus = (m[:, 0] > lo) & (m[:, 0] < hi) & (m[:, 1] > lo) & (m[:, 1] < hi)
    return np.asarray(tri[~raus])


def test_garten_boden_von_unten_unter_dach_und_loch() -> None:
    """Boden unter einem Dach wird gefunden; durch ein Loch reicht der Strahl nicht bis zum Dach."""
    from scanview_import.sonne.flaeche import boden_aufbauen

    boden = platte(3.0, 0.005)
    dach = quader([-1.5, -1.5, 0.8], [0.2, 1.5, 1.0])
    pos, tri = zusammen(boden, dach)
    ecken = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], float)
    fl = boden_aufbauen(ecken, Szene(pos, tri, ecken.mean(axis=0)), 0.1, fenster_m=0.5)
    assert fl.ohne_mesh == 0 and len(fl.punkte) == 400
    assert np.abs(fl.punkte[:, 2]).max() <= 0.005 + 1e-6
    assert fl.normale.tolist() == [0.0, 0.0, 1.0] and fl.flaeche_m2 == pytest.approx(4.0)
    tri_loch = np.concatenate([_loch(*boden, -0.5, -0.1), dach[1] + len(boden[0])])
    fl = boden_aufbauen(ecken, Szene(pos, tri_loch, ecken.mean(axis=0)), 0.1, fenster_m=0.5)
    assert 0 < fl.ohne_mesh < 40  # Loch unter dem Dach: kein Boden, auch kein Dachtreffer
    assert np.abs(fl.punkte[:, 2]).max() <= 0.005 + 1e-6


def _trifft_quader(o: np.ndarray, d: np.ndarray, lo: list[float], hi: list[float]) -> np.ndarray:
    """Vektorisiert wie `strahl_trifft_quader`: o (3,), d (T, 3) → (T,) bool."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (np.asarray(lo) - o) / d
        t2 = (np.asarray(hi) - o) / d
    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    return np.asarray(tmax >= np.maximum(tmin, 0.0))


def test_garten_sonnenstunden_je_punkt_gegen_strahltest() -> None:
    """Stunden je Bodenpunkt und Monat über die Richtungskarte gegen den analytischen Strahltest:
    Abweichung höchstens eine Viertelstunde je Schattenwechsel."""
    from scanview_import.sonne.flaeche import boden_aufbauen
    from scanview_import.sonne.garten import sonnenschritte, sonnenstunden

    wand = ([6.0, -30, 0], [6.3, 30, 4.0])
    mast = ([-0.3, -8.3, 0], [0.3, -7.7, 25.0])
    szene, ecken = _szene_platte(0.005, quader(*wand), quader(*mast))
    fl = boden_aufbauen(ecken, szene, 0.8)
    r = Raster()
    frei = (~karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, r)).astype(np.float32)
    zeit = pd.date_range("2023-01-01 00:07:30", "2023-12-31 23:52:30", freq="15min", tz="UTC")
    sonne = ertrag.sonnenstand(zeit, 52.394, 13.028, 33.0)
    eben = (np.array([0.0, 360.0]), np.zeros(2))
    ist, je_monat = sonnenstunden(frei, r, KONV, eben, sonne)
    assert je_monat["ueber_horizont"][5] == pytest.approx(16.7, abs=0.2)  # Juni, Potsdam

    _, tag, a_g, h_g = sonnenschritte(sonne, KONV, eben)
    a = sonne["azimuth"].to_numpy()[tag]
    d = geo.richtung(np.asarray(geo.geo_nach_gitter(a, KONV)), h_g)
    monat = zeit.month.to_numpy()[tag] - 1
    tage = np.array([pd.Period(f"2023-{m:02d}").days_in_month for m in range(1, 13)])
    for p, o in enumerate(fl.ursprung):
        soll_frei = ~(_trifft_quader(o, d, *wand) | _trifft_quader(o, d, *mast))
        # je Zeitschritt: Abweichungen nur am Schattenwechsel (±1 Viertelstunde) oder wo eine
        # Kante innerhalb einer Rasterzelle liegt (dünner Mast: Schatten kürzer als ein Schritt)
        sicht = nachschlagen(frei[p], r, a_g, h_g)
        summe = np.bincount(monat, weights=sicht, minlength=12) * 0.25 / tage
        np.testing.assert_allclose(ist[p], summe, atol=1e-3)  # Aggregation exakt
        karte_frei = sicht >= 0.5
        wechsel = np.flatnonzero(np.diff(soll_frei.astype(int)) != 0)
        for i in np.flatnonzero(karte_frei != soll_frei):
            if np.min(np.abs(wechsel - i)) <= 1:
                continue
            nachbarn = geo.richtung(
                np.asarray(geo.geo_nach_gitter(a[i], KONV)) + np.array([-2.0, 2.0, 0.0, 0.0]),
                h_g[i] + np.array([0.0, 0.0, -1.0, 1.0]),
            )
            kante = ~(_trifft_quader(o, nachbarn, *wand) | _trifft_quader(o, nachbarn, *mast))
            assert (kante != soll_frei[i]).any(), (p, i)
        # je Monat: dieselben Schritte aufsummiert
        soll = np.bincount(monat[soll_frei], minlength=12) * 0.25 / tage
        n_wechsel = np.bincount(monat[wechsel], minlength=12)
        assert np.all(np.abs(ist[p] - soll) * tage <= 0.25 * (n_wechsel + 1) + 1e-6), p
    assert ist.min() < ist.max() - 1  # Wand und Mast werfen unterschiedlich viel Schatten


def test_garten_vorlage_vollstaendig() -> None:
    import re

    from scanview_import.sonne import garten_bericht

    platzhalter = set(re.findall(r"\$(\w+)", garten_bericht.VORLAGE.template))
    quelle = Path(garten_bericht.__file__).read_text()
    werte = set(re.findall(r'^\s+"(\w+)": ', quelle[quelle.index("    werte = {") :], re.M))
    assert platzhalter <= werte, platzhalter - werte


def test_garten_kronendach_schliesst_nur_ueber_wuchshoehe() -> None:
    from scanview_import.sonne.garten import KRONEN_ZELLE_M, kronendach

    krone = quader([0.5, 0.5, 3.0], [1.0, 1.0, 4.0])  # schwebend wie eine löchrige Krone
    busch = quader([-1.2, -1.2, 0.0], [-0.8, -0.8, 1.0])
    pos, _ = zusammen(platte(3.0, 0.005), krone, busch)
    ecken, dreiecke, info = kronendach(pos, np.zeros(3))
    assert len(dreiecke) == 2 * info["zellen"] and len(ecken) == 4 * info["zellen"]
    mitte = ecken.reshape(-1, 4, 3).mean(axis=1)
    assert np.allclose(mitte[:, 2], 4.0)  # Deckel auf der höchsten Stelle
    # Zellen mit Kronenecken (Ecke bei 1,0 liegt schon in der nächsten Zelle) plus eine Zelle Rand;
    # der Busch (1 m hoch, bei -1) bleibt offen
    z = KRONEN_ZELLE_M
    assert (mitte[:, :2] > 0.5 - 1.5 * z - 1e-9).all()
    assert (mitte[:, :2] < 1.0 + 2.5 * z + 1e-9).all()
    assert info["zellen"] == 16
    # Stichpunkte 0,3 m unter dem Deckel: dort darf nicht geschlossen werden
    unten = np.array([[0.75, 0.75, 3.7]])
    _, _, info = kronendach(pos, np.zeros(3), unten)
    assert info["zellen"] == 15

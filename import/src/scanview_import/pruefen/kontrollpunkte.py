"""Kontrollpunkte als CSV (F15 Teil 2): absolute Höhe und Lage, Maßstab aus Kontrollpunktpaaren.

Grundsätze wie F15 Teil 1 (``genauigkeit.py``): Nur Kontrollpunkte (Rolle ``check``) ergeben einen
Nachweis, Passpunkte (``control``) waren im Ausgleich und zählen nie. Die Höhe misst siteBits selbst
an der Wolke; die Lage gibt es nur aus Ist-Spalten der Liste — vom Messenden geliefert oder im
siteBits-Viewer am Modell gepickt (Kommentar ``# quelle = siteBits-Viewer …, am Modell gepickt``).
Ausreißer bleiben im RMSE. Nicht messbare Punkte werden einzeln ausgewiesen, nie gemittelt.
In den RMSE geht die Genauigkeit der Kontrollpunkte ein: √(RMSE_Messung² + RMS(sigma)²) (ASPRS, laut
Recherche belegt); weitere Norm-Aussagen sind hier bewusst nicht kodiert.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

import laspy
import numpy as np
from pyproj import CRS
from pyproj.exceptions import CRSError
from scipy.spatial import cKDTree

from .bezug import hoehensystem_wolke, zerlege
from .genauigkeitsklassen import einordnung
from .katalog import KatalogFehler, Zeile
from .lieferung import LESEFEHLER, Lieferung
from .punktraster import BLOCK, BODEN, gemeinsames_crs
from .urteil import ERFUELLT, NICHT_ERFUELLT, NICHT_PRUEFBAR, Ergebnis, _param

ROLLEN = {
    "check": "check",
    "kontrollpunkt": "check",
    "control": "control",
    "passpunkt": "control",
}
PFLICHT = ("id", "rolle", "e", "n", "h")
SPALTEN = (*PFLICHT, "sigma_lage", "sigma_hoehe", "ist_e", "ist_n", "ist_h")
MAX_PUNKTE = 10_000
MUSTER = ["*kontrollpunkt*.csv"]
OHNE_LAGE = (
    "Grundlage fehlt: Ist-Lage der Kontrollpunkte (Liste ohne Ist-Spalten); im siteBits-Viewer "
    "(Panel „Kontrollpunkte“) am Modell picken und die exportierte Liste mit --kontrollpunkte "
    "übergeben. Die Höhe prüft siteBits selbst"
)
_CRS_ZEILE = re.compile(r"^#\s*crs\s*=\s*(.+?)\s*$", re.IGNORECASE)
_QUELLE_ZEILE = re.compile(r"^#\s*quelle\s*=\s*(.+?)\s*$", re.IGNORECASE)
VIEWER = ("sitebits-viewer", "am modell gepickt")


class KontrollpunktFehler(ValueError):
    pass


class KontrollpunktEingabeFehler(KatalogFehler):
    """Liste des Auftraggebers (--kontrollpunkte) unbrauchbar: Eingabefehler, Exit 2."""


@dataclass(frozen=True)
class Kontrollpunkt:
    id: str
    rolle: str
    soll: tuple[float, float, float]
    sigma_lage: float | None
    sigma_hoehe: float | None
    ist: tuple[float | None, float | None, float | None]


@dataclass(frozen=True)
class Liste:
    punkte: tuple[Kontrollpunkt, ...]
    crs: CRS  # horizontal
    hoehe: str  # normiert
    datei: str
    herkunft: str
    sha256: str
    # Kommentarzeile „# quelle = …“: wer die Ist-Werte erhoben hat (Angabe der Liste)
    ist_quelle: str | None = None

    @property
    def am_modell_gepickt(self) -> bool:
        q = (self.ist_quelle or "").lower()
        return all(w in q for w in VIEWER)

    @property
    def kontrolle(self) -> list[Kontrollpunkt]:
        return [p for p in self.punkte if p.rolle == "check"]


def _zahl(text: str, trenner: str, wo: str) -> float | None:
    t = text.strip()
    if not t:
        return None
    if trenner == ";":
        t = t.replace(",", ".")
    try:
        x = float(t)
    except ValueError as e:
        raise KontrollpunktFehler(f"{wo}: „{text.strip()}“ ist keine Zahl") from e
    if not math.isfinite(x):
        raise KontrollpunktFehler(f"{wo}: Zahl nicht endlich")
    return x


def _crs_gleich(a: str, b: str) -> bool:
    """Gleiche Deklaration, Schreibweise egal („epsg:25833+7837“ = „EPSG:25833 + 7837“)."""
    return re.sub(r"\s+", "", a).upper() == re.sub(r"\s+", "", b).upper()


def _zeilen_und_crs(
    text: str, crs_vorgabe: str | None
) -> tuple[list[tuple[int, str]], str | None, str | None]:
    """Datenzeilen (mit Nummer), das deklarierte Bezugssystem und die Quellenangabe aus den
    Kommentarzeilen."""
    crs_text = crs_vorgabe
    datei_crs: str | None = None
    quelle: str | None = None
    zeilen: list[tuple[int, str]] = []
    for nr, z in enumerate(text.splitlines(), 1):
        if not z.strip():
            continue
        m = _CRS_ZEILE.match(z.strip())
        if m:
            for andere in (crs_vorgabe, datei_crs):
                if andere is not None and not _crs_gleich(m.group(1), andere):
                    raise KontrollpunktFehler(
                        f"Bezugssystem widersprüchlich: „{m.group(1)}“ und „{andere}“"
                    )
            datei_crs = m.group(1)
            crs_text = crs_vorgabe or datei_crs
        elif (q := _QUELLE_ZEILE.match(z.strip())) is not None:
            quelle = q.group(1)
        elif not z.lstrip().startswith("#"):
            zeilen.append((nr, z))
    if not zeilen:
        raise KontrollpunktFehler("Liste ohne Kopf und Punkte")
    return zeilen, crs_text, quelle


def _kopf(kopf: str) -> tuple[str, list[str]]:
    trenner = ";" if ";" in kopf else "," if "," in kopf else ""
    if not trenner:
        raise KontrollpunktFehler("Trenner weder „;“ noch „,“")
    spalten = [s.strip().lower() for s in kopf.split(trenner)]
    fremd = [s for s in spalten if s not in SPALTEN]
    if fremd or len(set(spalten)) != len(spalten):
        raise KontrollpunktFehler(f"Kopf ungültig (unbekannt oder doppelt: {fremd or spalten})")
    fehlt = [s for s in PFLICHT if s not in spalten]
    if fehlt:
        raise KontrollpunktFehler(f"Pflichtspalten fehlen: {fehlt}")
    return trenner, spalten


def _punkt(nr: int, z: str, trenner: str, spalten: list[str]) -> Kontrollpunkt:
    felder = z.split(trenner)
    wo = f"Zeile {nr}"
    if len(felder) != len(spalten):
        raise KontrollpunktFehler(
            f"{wo}: {len(felder)} statt {len(spalten)} Felder (Dezimalkomma bei „,“ als Trenner?)"
        )
    w = dict(zip(spalten, felder, strict=True))
    rolle = ROLLEN.get(w["rolle"].strip().lower())
    if rolle is None:
        raise KontrollpunktFehler(f"{wo}: Rolle „{w['rolle'].strip()}“ unbekannt")
    zahlen = {k: _zahl(w.get(k, ""), trenner, wo) for k in SPALTEN[2:]}
    e, n, h = zahlen["e"], zahlen["n"], zahlen["h"]
    if e is None or n is None or h is None:
        raise KontrollpunktFehler(f"{wo}: Sollkoordinate fehlt")
    return Kontrollpunkt(
        id=w["id"].strip(),
        rolle=rolle,
        soll=(e, n, h),
        sigma_lage=zahlen["sigma_lage"],
        sigma_hoehe=zahlen["sigma_hoehe"],
        ist=(zahlen["ist_e"], zahlen["ist_n"], zahlen["ist_h"]),
    )


def _bezug(crs_text: str | None) -> tuple[CRS, str]:
    if crs_text is None:
        raise KontrollpunktFehler(
            "Bezugssystem nicht deklariert (Kommentarzeile „# crs = EPSG:…+…“ oder "
            "--kontrollpunkte-crs)"
        )
    try:
        h, v = zerlege(CRS.from_user_input(crs_text))
    except CRSError as e:
        raise KontrollpunktFehler(f"Bezugssystem „{crs_text}“ unbekannt") from e
    if h is None or v is None or v.startswith("unbekannt"):
        raise KontrollpunktFehler(
            f"Bezugssystem „{crs_text}“ nennt kein bekanntes Höhensystem (z. B. EPSG:25833+7837)"
        )
    return h, v


def lese(text: str, crs_vorgabe: str | None, datei: str, herkunft: str) -> Liste:
    """CSV streng lesen: fester Kopf, eindeutiger Trenner, deklariertes Bezugssystem."""
    text = text.removeprefix("\ufeff")
    zeilen, crs_text, quelle = _zeilen_und_crs(text, crs_vorgabe)
    trenner, spalten = _kopf(zeilen[0][1])
    if len(zeilen) - 1 > MAX_PUNKTE:
        raise KontrollpunktFehler(f"mehr als {MAX_PUNKTE} Punkte")
    punkte = [_punkt(nr, z, trenner, spalten) for nr, z in zeilen[1:]]
    ids = [p.id for p in punkte]
    if "" in ids or len(set(ids)) != len(ids):
        raise KontrollpunktFehler("Punkt-IDs leer oder doppelt")
    h, v = _bezug(crs_text)
    return Liste(
        tuple(sorted(punkte, key=lambda p: p.id)),
        h,
        v,
        datei,
        herkunft,
        hashlib.sha256(text.encode()).hexdigest(),
        quelle,
    )


def _liste(z: Zeile, lf: Lieferung) -> Liste | Ergebnis:
    if lf.kontrollpunkte is not None:
        pfad, crs = lf.kontrollpunkte
        schluessel: tuple[Any, ...] = ("kp", str(pfad), crs)
        if schluessel not in lf.cache:
            try:
                text = pfad.read_text(encoding="utf-8-sig")
                lf.cache[schluessel] = lese(text, crs, pfad.name, "Auftraggeber (--kontrollpunkte)")
            except (OSError, UnicodeDecodeError, KontrollpunktFehler) as e:
                raise KontrollpunktEingabeFehler(f"Kontrollpunktliste {pfad.name}: {e}") from e
            lf.extern[f"Kontrollpunkte {pfad.name}"] = lf.cache[schluessel].sha256
        return lf.cache[schluessel]  # type: ignore[no-any-return]
    muster = _param(z, "muster", (str, list), pflicht=False) or MUSTER
    treffer = lf.finde(muster)
    if not treffer:
        return Ergebnis(NICHT_ERFUELLT, f"keine Kontrollpunktliste ({muster})")
    if len(treffer) > 1:
        return Ergebnis(NICHT_ERFUELLT, f"mehrere Kontrollpunktlisten, nicht eindeutig: {treffer}")
    schluessel = ("kp", treffer[0], None)
    if schluessel not in lf.cache:
        try:
            text = lf.pfad(treffer[0]).read_text(encoding="utf-8-sig")
            lf.cache[schluessel] = lese(text, None, treffer[0], "Lieferung")
        except (OSError, UnicodeDecodeError, KontrollpunktFehler) as e:
            lf.cache[schluessel] = Ergebnis(NICHT_ERFUELLT, f"{treffer[0]}: {e}")
    return lf.cache[schluessel]  # type: ignore[no-any-return]


def _umgebung(
    lf: Lieferung, dateien: list[str], liste: Liste, radius: float
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], bool]:
    """Höhen und Klassen der Punkte im Radius um jeden Kontrollpunkt; dazu, ob die Wolke
    überhaupt Bodenpunkte (Klasse 2) hat, also klassifiziert ist."""
    schluessel = ("kp-umgebung", tuple(dateien), liste.sha256, radius)
    if schluessel in lf.cache:
        return lf.cache[schluessel]  # type: ignore[no-any-return]
    pk = liste.kontrolle
    xy = np.array([p.soll[:2] for p in pk])
    baum = cKDTree(xy)
    lo, hi = xy.min(axis=0) - radius, xy.max(axis=0) + radius
    z_teile: list[np.ndarray] = []
    k_teile: list[np.ndarray] = []
    i_teile: list[np.ndarray] = []
    klassifiziert = False
    for rel in dateien:
        with laspy.open(lf.pfad(rel), laz_backend=laspy.LazBackend.LazrsParallel) as f:
            for block in f.chunk_iterator(BLOCK):
                klassifiziert = klassifiziert or bool(np.any(block.classification == BODEN))
                x, y = np.asarray(block.x), np.asarray(block.y)
                m = (x >= lo[0]) & (x <= hi[0]) & (y >= lo[1]) & (y <= hi[1])
                if not m.any():
                    continue
                d, i = baum.query(np.column_stack([x[m], y[m]]), k=1, distance_upper_bound=radius)
                t = np.isfinite(d)
                z_teile.append(np.asarray(block.z)[m][t])
                k_teile.append(np.asarray(block.classification)[m][t])
                i_teile.append(i[t])
    z_all = np.concatenate(z_teile) if z_teile else np.empty(0)
    k_all = np.concatenate(k_teile) if k_teile else np.empty(0, np.uint8)
    i_all = np.concatenate(i_teile) if i_teile else np.empty(0, np.int64)
    out = {p.id: (z_all[i_all == j], k_all[i_all == j]) for j, p in enumerate(pk)}
    lf.cache[schluessel] = (out, klassifiziert)
    return out, klassifiziert


def _de(x: float, stellen: int = 3) -> str:
    return f"{x:.{stellen}f}".replace(".", ",")


def _rmse(fehler: np.ndarray, sigma: list[float | None]) -> tuple[float, float, float]:
    """(RMSE Messung, RMS sigma der Kontrollpunkte, kombiniert)."""
    mess = float(np.sqrt(np.mean(fehler**2)))
    s = [x for x in sigma if x is not None]
    kp = float(np.sqrt(np.mean(np.square(s)))) if s else 0.0
    return mess, kp, math.hypot(mess, kp)


def _grundbelege(liste: Liste) -> list[str]:
    n_pass = len(liste.punkte) - len(liste.kontrolle)
    return [
        f"Liste {liste.datei} ({liste.herkunft}), Bezugssystem {liste.crs.name} + {liste.hoehe}: "
        f"{len(liste.kontrolle)} Kontrollpunkte, {n_pass} Passpunkte (zählen nicht, waren im "
        "Ausgleich)"
    ]


def _ist_herkunft(liste: Liste) -> str:
    if liste.am_modell_gepickt:
        return (
            "Ist-Lage im siteBits-Viewer am Modell gepickt (Angabe der Liste: "
            f"„{liste.ist_quelle}“), von siteBits ausgewertet; die Marken trifft der Mensch, "
            "der pickt"
        )
    return (
        "Ist-Lage aus der Liste (vom Lieferanten bzw. Auftraggeber gemessen), von siteBits "
        "nachgerechnet, nicht nachgemessen"
    )


def _bewerten(
    z: Zeile,
    liste: Liste,
    genutzt: list[Kontrollpunkt],
    fehler: np.ndarray,
    sigma: list[float | None],
    soll: float,
    was: str,
    belege: list[str],
    tabelle: list[dict[str, Any]],
) -> Ergebnis:
    mindestens = int(_param(z, "mindestens", int, pflicht=False) or 30)
    kp_fehler = _param(z, "kp_fehler", bool, pflicht=False)
    maengel = []
    if len(genutzt) < mindestens:
        maengel.append(
            f"nur {len(genutzt)} unabhängige, messbare Kontrollpunkte (verlangt {mindestens})"
        )
    ohne_sigma = [p.id for p, s in zip(genutzt, sigma, strict=True) if s is None]
    if ohne_sigma and kp_fehler is not False:
        maengel.append(
            f"Genauigkeit (\u03c3) der Kontrollpunkte fehlt bei {len(ohne_sigma)} Punkten"
        )
    if len(genutzt):
        mess, kp, ges = _rmse(fehler, sigma)
        groesst = int(np.argmax(np.abs(fehler)))
        belege += [
            f"RMSE_{was} Messung {_de(mess)} m, Kontrollpunkte \u03c3 (RMS) {_de(kp)} m, "
            f"kombiniert √(Messung² + \u03c3²) = {_de(ges)} m (verlangt ≤ {_de(soll)} m)",
            einordnung(ges, lage=was == "H"),
            f"Mittel {_de(float(np.mean(fehler)))} m (systematischer Anteil), größte Abweichung "
            f"{_de(float(fehler[groesst]))} m an {genutzt[groesst].id}; Ausreißer bleiben drin",
        ]
        if ges > soll + 1e-12:
            maengel.append(f"RMSE_{was} {_de(ges)} m > {_de(soll)} m")
    if maengel:
        return Ergebnis(NICHT_ERFUELLT, "; ".join(maengel), belege, tabelle=tabelle)
    mess, kp, ges = _rmse(fehler, sigma)
    return Ergebnis(
        ERFUELLT,
        f"RMSE_{was} {_de(ges)} m ≤ {_de(soll)} m an {len(genutzt)} Kontrollpunkten",
        belege,
        tabelle=tabelle,
    )


def _hoehe(z: Zeile, lf: Lieferung, liste: Liste, soll: float) -> Ergebnis:
    radius = float(_param(z, "radius_m", (int, float), pflicht=False) or 0.25)
    min_punkte = int(_param(z, "min_punkte", int, pflicht=False) or 5)
    max_streuung = float(_param(z, "max_streuung_m", (int, float), pflicht=False) or 0.05)
    dateien = lf.von_art("las", "laz")
    if not dateien:
        return Ergebnis(NICHT_ERFUELLT, "keine Punktwolke (LAS/LAZ) zum Nachmessen der Höhen")
    crs = gemeinsames_crs(lf, dateien)
    if isinstance(crs, str):
        return Ergebnis(NICHT_ERFUELLT, crs)
    if not crs.equals(liste.crs, ignore_axis_order=True):
        return Ergebnis(
            NICHT_ERFUELLT, f"Lagebezug der Wolke {crs.name} ≠ Kontrollpunkte {liste.crs.name}"
        )
    try:
        v = hoehensystem_wolke(lf, dateien)
    except LESEFEHLER as e:
        return Ergebnis(NICHT_ERFUELLT, f"Punktwolke nicht lesbar ({type(e).__name__})")
    if v != liste.hoehe:
        return Ergebnis(
            NICHT_ERFUELLT,
            f"Höhensystem der Wolke {v or 'nicht eindeutig deklariert'} ≠ Kontrollpunkte "
            f"{liste.hoehe}; keine stille Umrechnung",
        )
    belege = _grundbelege(liste) + [
        f"Ist-Höhe von siteBits gemessen: Median der Punkte im Radius {radius:g} m "
        "(nur Klasse 2, wenn die Wolke klassifiziert ist), mindestens "
        f"{min_punkte} Punkte, "
        f"Streuung (MAD·1,4826) ≤ {max_streuung:g} m; "
        "Wolke: " + ", ".join(dateien)
    ]
    umgebung, klassifiziert = (
        _umgebung(lf, dateien, liste, radius) if liste.kontrolle else ({}, False)
    )
    # Klassifizierte Wolke: nur Boden zählt, sonst mäße ein Punkt unter einem Dach das Dach.
    quelle = "Klasse 2" if klassifiziert else "alle (Wolke ohne Bodenklasse)"
    genutzt: list[Kontrollpunkt] = []
    fehler: list[float] = []
    tabelle: list[dict[str, Any]] = []
    nicht_messbar: list[str] = []
    for p in liste.kontrolle:
        zz, kk = umgebung[p.id]
        werte = zz[kk == BODEN] if klassifiziert else zz
        zeile: dict[str, Any] = {"id": p.id, "e": p.soll[0], "n": p.soll[1], "h": p.soll[2]}
        zeile |= {"sigma_hoehe": p.sigma_hoehe, "punkte": len(werte), "quelle": quelle}
        if len(werte) < min_punkte:
            zeile["status"] = f"nicht messbar: {len(werte)} Punkte ({quelle}) im Radius"
        else:
            med = float(np.median(werte))
            streuung = float(1.4826 * np.median(np.abs(werte - med)))
            zeile |= {"ist_h": med, "streuung": streuung}
            if streuung > max_streuung:
                zeile["status"] = "nicht messbar: Streuung zu groß (Kante, Bewuchs?)"
            else:
                zeile |= {"dh": med - p.soll[2], "status": "gemessen"}
                genutzt.append(p)
                fehler.append(med - p.soll[2])
        if zeile["status"] != "gemessen":
            nicht_messbar.append(p.id)
        tabelle.append(zeile)
    if nicht_messbar:
        belege.append(f"nicht messbar ({len(nicht_messbar)}): {', '.join(nicht_messbar)}")
    return _bewerten(
        z,
        liste,
        genutzt,
        np.asarray(fehler),
        [p.sigma_hoehe for p in genutzt],
        soll,
        "V",
        belege,
        tabelle,
    )


def _lage(z: Zeile, liste: Liste, soll: float) -> Ergebnis:
    mit = [p for p in liste.kontrolle if p.ist[0] is not None and p.ist[1] is not None]
    if liste.kontrolle and not mit:
        return Ergebnis(NICHT_PRUEFBAR, OHNE_LAGE, _grundbelege(liste))
    belege = _grundbelege(liste) + [_ist_herkunft(liste)]
    ohne = [p.id for p in liste.kontrolle if p not in mit]
    if ohne:
        belege.append(f"ohne Ist-Lage ({len(ohne)}): {', '.join(ohne)}")
    fehler = np.array([math.hypot(p.ist[0] - p.soll[0], p.ist[1] - p.soll[1]) for p in mit])  # type: ignore[operator]
    tabelle = [
        {
            "id": p.id,
            "e": p.soll[0],
            "n": p.soll[1],
            "ist_e": p.ist[0],
            "ist_n": p.ist[1],
            "sigma_lage": p.sigma_lage,
            "dr": float(f),
        }
        for p, f in zip(mit, fehler, strict=True)
    ]
    return _bewerten(z, liste, mit, fehler, [p.sigma_lage for p in mit], soll, "H", belege, tabelle)


def kontrollpunkte(z: Zeile, lf: Lieferung) -> Ergebnis:
    rmse_h = _param(z, "rmse_h_m", (int, float), pflicht=False)
    rmse_v = _param(z, "rmse_v_m", (int, float), pflicht=False)
    if (rmse_h is None) == (rmse_v is None):
        raise KatalogFehler(f"{z.id}: genau einer der Parameter rmse_h_m oder rmse_v_m")
    liste = _liste(z, lf)
    if isinstance(liste, Ergebnis):
        return liste
    if rmse_v is not None:
        return _hoehe(z, lf, liste, float(rmse_v))
    return _lage(z, liste, float(rmse_h))


def massstab(z: Zeile, lf: Lieferung) -> Ergebnis:
    strecken = int(_param(z, "strecken", int))
    grenze = float(_param(z, "abweichung", (int, float)))
    min_laenge = float(_param(z, "min_laenge_m", (int, float), pflicht=False) or 10.0)
    liste = _liste(z, lf)
    if isinstance(liste, Ergebnis):
        return liste
    mit = [p for p in liste.kontrolle if p.ist[0] is not None and p.ist[1] is not None]
    if not mit:
        return Ergebnis(NICHT_PRUEFBAR, OHNE_LAGE, _grundbelege(liste))
    paare = []
    for i, a in enumerate(mit):
        for b in mit[i + 1 :]:
            d_soll = math.dist(a.soll[:2], b.soll[:2])
            if d_soll >= min_laenge:
                d_ist = math.dist(a.ist[:2], b.ist[:2])  # type: ignore[arg-type]
                paare.append((a.id, b.id, d_soll, (d_ist - d_soll) / d_soll))
    belege = _grundbelege(liste) + [
        f"Strecken aus Kontrollpunktpaaren (Lage, waagerecht) mit Sollabstand ≥ {min_laenge:g} m: "
        f"{len(paare)}; Ist aus der Liste"
        + ("" if liste.am_modell_gepickt else ", nicht nachgemessen"),
        _ist_herkunft(liste),
    ]
    tabelle = [{"von": a, "nach": b, "soll_m": d, "abweichung": r} for a, b, d, r in sorted(paare)]
    if len(paare) < strecken:
        return Ergebnis(
            NICHT_ERFUELLT,
            f"nur {len(paare)} Strecken aus Kontrollpunktpaaren ≥ {min_laenge:g} m "
            f"(verlangt {strecken})",
            belege,
            tabelle=tabelle,
        )
    von, nach, _d, groesst = max(paare, key=lambda x: abs(x[3]))
    text = f"größte Abweichung {_de(100 * groesst, 3)} % ({von} bis {nach}) aus Kontrollpunktpaaren"
    belege.append(text)
    if abs(groesst) > grenze + 1e-12:
        return Ergebnis(
            NICHT_ERFUELLT,
            text + f", erlaubt {_de(100 * grenze, 3)} %",
            belege,
            tabelle=tabelle,
        )
    return Ergebnis(ERFUELLT, text, belege, tabelle=tabelle)

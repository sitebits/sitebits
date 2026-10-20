"""POIs und Messungen auf dem Server.

Prüfregeln gespiegelt aus web/src/poiStore.ts (POI_LIMITS, POI_ID_RE, STATION_ID_RE, checkText:
trimmen, Titel Pflicht) und web/src/measureStore.ts (POINT_RANGE je Art). Die geometrische
Gültigkeit einer Fläche (Selbstschnitt, Ebene, PCA in areaMath.ts) prüft nur der Viewer — der
Server speichert Struktur. Weiches Löschen (geloescht_am), optimistische Sperre
(version) und je Änderung ein Protokoll-Ereignis in derselben Transaktion.

Rolle Rückmelder (`Eigentum`): nur eigene Einträge ändern, löschen oder wiederherstellen, nur
die Kategorie „Rückmeldung“, höchstens `RUECKMELDUNGEN_MAX` aktive je Projekt. Ein eigener
Eintrag anderer Kategorie lässt sich nur noch löschen; wiederherstellen nur, was der Rückmelder
selbst gelöscht hat. Geprüft in der Schreib-Transaktion; Vorrang 404 → 403 → 409 → 422, damit
ein fremder Eintrag nie als Konfliktkörper zurückkommt und die Kategorieprüfung nur eigene trifft.

KI-Vorschläge (G7, R-0.8-23/24): Über einen Token „für einen KI-Agenten“ (`ki=True`) entsteht
ein Vorschlag (`pruefstand='vorschlag'`, `herkunft='ki'`), Löschen setzt nur
`loeschen_vorgeschlagen`. Gültiges ändert ein Agent nicht direkt. Ein Mensch nimmt an
(`annehmen`) oder verwirft (`verwerfen`); erst `….angenommen` ist ein Ereignis mit Export.
Verworfene Einträge bleiben in der DB, gelten aber überall wie gelöscht.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Literal

from starlette.exceptions import HTTPException as StarletteHTTPException

from . import protokoll
from .db import transaktion
from .web import Konflikt, Ungueltig

Tabelle = Literal["pois", "messungen"]
TABELLEN: tuple[Tabelle, ...] = ("pois", "messungen")
ART: dict[str, str] = {"pois": "poi", "messungen": "messung"}
_PRAEFIX: dict[str, str] = {"pois": "P-", "messungen": "M-"}
_MEHRZAHL: dict[str, str] = {"pois": "POIs", "messungen": "Messungen"}

POI_LIMITS: dict[str, int] = {"title": 200, "category": 60, "note": 2000}
ANZAHL_MAX = 5000
PUNKTE_MAX = 1000
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
STATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
PUNKTE: dict[str, tuple[int, int]] = {
    "distance": (2, 2), "polyline": (2, PUNKTE_MAX), "area": (3, PUNKTE_MAX), "height": (2, 2),
}
MARKIERUNG_FELDER = ("title", "category", "note", "position", "station")
_POI_FELDER = frozenset({"title", "category", "note", "position", "station", "positionSource"})
_MESS_FELDER = frozenset({"kind", "points", "areaM2", "planarityM", "pointSources"})
_FLAECHENWERTE = ("areaM2", "planarityM")
_LABEL = {"title": "Titel", "category": "Kategorie", "note": "Notiz"}
ID_VERGEBEN = "Diese ID ist schon vergeben – aktueller Stand anbei."  # noqa: RUF001
NICHT_GEFUNDEN = "nicht gefunden"
RUECKMELDUNG = "Rückmeldung"
RUECKMELDUNGEN_MAX = 100
NUR_EIGENE = "Rückmelder ändern und löschen nur eigene Rückmeldungen."
NUR_RUECKMELDUNG = f"Rückmelder legen nur Einträge der Kategorie „{RUECKMELDUNG}“ an."
NUR_SELBST_GELOESCHTE = "Rückmelder stellen nur selbst gelöschte Rückmeldungen wieder her."
NUR_LOESCHBAR = (f"Dieser Eintrag hat nicht (mehr) die Kategorie „{RUECKMELDUNG}“;"
                 " Rückmelder können ihn nur noch löschen.")
GUELTIG, VORSCHLAG, VERWORFEN = "gueltig", "vorschlag", "verworfen"
KI_NUR_VORSCHLAG = ("Ein KI-Agent ändert gültige Einträge nicht direkt – bitte einen neuen"  # noqa: RUF001
                    " Eintrag oder einen Vorschlag im Befund-Faden anlegen.")
KEIN_VORSCHLAG = "Hier gibt es nichts zu prüfen."


@dataclass(frozen=True)
class Vorschlagssicht:
    """Welche offenen Vorschläge eine Liste zeigt: alle (wer prüfen darf) oder nur die eigenen."""

    alle: bool
    nutzer_id: int | None


NUR_GUELTIG = Vorschlagssicht(alle=False, nutzer_id=None)


@dataclass(frozen=True)
class Eigentum:
    """Einschränkung der Rolle Rückmelder: nur eigene Einträge, Kategorie fest, Obergrenze."""

    nutzer_id: int


def _eigen(alt: sqlite3.Row, eigen: Eigentum | None) -> None:
    # erstellt_von NULL (z. B. per Kommandozeile angelegt) ist nie „eigen“.
    if eigen is not None and alt["erstellt_von"] != eigen.nutzer_id:
        raise StarletteHTTPException(status_code=403, detail=NUR_EIGENE)


def _ist_rueckmeldung(daten: dict[str, Any]) -> bool:
    return unicodedata.normalize("NFC", str(daten.get("category", ""))).strip() == RUECKMELDUNG


def _gespeichert_rueckmeldung(daten: dict[str, Any], eigen: Eigentum | None) -> None:
    """Rückmelder: der gespeicherte Stand muss eine Rückmeldung sein (sonst nur löschbar)."""
    if eigen is not None and not _ist_rueckmeldung(daten):
        raise Ungueltig(NUR_LOESCHBAR)


def _kategorie(daten: dict[str, Any], eigen: Eigentum | None) -> None:
    """Rückmelder: Kategorie muss „Rückmeldung“ sein (nach NFC verglichen, NFC gespeichert)."""
    if eigen is None:
        return
    kategorie = unicodedata.normalize("NFC", str(daten.get("category", "")))
    if kategorie != RUECKMELDUNG:
        raise Ungueltig(NUR_RUECKMELDUNG)
    daten["category"] = kategorie


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _zahl(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)


def _punkt(wert: Any, wo: str) -> list[float]:
    if not isinstance(wert, list) or len(wert) != 3 or not all(_zahl(v) for v in wert):
        raise Ungueltig(f"{wo} braucht drei endliche Zahlen (Weltkoordinaten in m).")
    return [float(v) for v in wert]


def _text(wert: Any, feld: str, pflicht: bool) -> str:
    if wert is None:
        if pflicht:
            raise Ungueltig(f"{_LABEL[feld]} fehlt.")
        return ""
    if not isinstance(wert, str):
        raise Ungueltig(f"{_LABEL[feld]} muss Text sein.")
    t = wert.strip()
    if pflicht and not t:
        raise Ungueltig(f"{_LABEL[feld]} fehlt.")
    if len(t) > POI_LIMITS[feld]:
        raise Ungueltig(f"{_LABEL[feld]} länger als {POI_LIMITS[feld]} Zeichen.")
    return t



def _quelle(roh: Any) -> dict[str, str] | None:
    if roh is None:
        return None
    if (not isinstance(roh, dict) or set(roh) != {"source", "layerId"}
            or roh["source"] not in ("pointcloud", "mesh")
            or not isinstance(roh["layerId"], str) or not ID_RE.fullmatch(roh["layerId"])):
        raise Ungueltig("Ungültige Punktquelle (source und layerId erforderlich).")
    return {"source": roh["source"], "layerId": roh["layerId"]}


def poi_pruefen(roh: Any) -> dict[str, Any]:
    if not isinstance(roh, dict):
        raise Ungueltig("POI-Daten müssen ein Objekt sein.")
    if set(roh) - _POI_FELDER:
        raise Ungueltig("Unbekanntes Feld in den POI-Daten.")
    station = roh.get("station")
    if station is not None and (
        not isinstance(station, str) or not STATION_ID_RE.fullmatch(station)
    ):
        raise Ungueltig("Ungültiger Standpunkt.")
    kategorie = roh.get("category")
    if isinstance(kategorie, str):
        # NFC für alle Rollen, vor Rand und Länge: eine zerlegt geschickte Kategorie (MCP-Client)
        # gilt im Viewer-Filter als dieselbe.
        kategorie = unicodedata.normalize("NFC", kategorie)
    daten: dict[str, Any] = {
        "title": _text(roh.get("title"), "title", True),
        "category": _text(kategorie, "category", False),
        "note": _text(roh.get("note"), "note", False),
        "position": _punkt(roh.get("position"), "Position"),
        "station": station,
    }

    if "positionSource" in roh:
        daten["positionSource"] = _quelle(roh["positionSource"])
    return daten


def messung_pruefen(roh: Any) -> dict[str, Any]:
    if not isinstance(roh, dict):
        raise Ungueltig("Messdaten müssen ein Objekt sein.")
    if set(roh) - _MESS_FELDER:
        raise Ungueltig("Unbekanntes Feld in den Messdaten.")
    kind = roh.get("kind")
    if not isinstance(kind, str) or kind not in PUNKTE:
        raise Ungueltig("Unbekannte Messart (erlaubt: distance, polyline, area, height).")
    lo, hi = PUNKTE[kind]
    punkte = roh.get("points")
    if not isinstance(punkte, list) or not lo <= len(punkte) <= hi:
        spanne = str(lo) if lo == hi else f"{lo} bis {hi}"
        raise Ungueltig(f"Messart {kind} braucht {spanne} Eckpunkte.")
    daten: dict[str, Any] = {
        "kind": kind,
        "points": [_punkt(p, f"Eckpunkt {i + 1}") for i, p in enumerate(punkte)],
    }
    if "pointSources" in roh:
        sources = roh["pointSources"]
        if not isinstance(sources, list) or len(sources) != len(punkte):
            raise Ungueltig("pointSources braucht genau eine Quelle je Eckpunkt.")
        daten["pointSources"] = [_quelle(q) for q in sources]
    for feld in _FLAECHENWERTE:
        wert = roh.get(feld)
        if wert is None:
            continue
        if kind != "area":
            raise Ungueltig(f"{feld} gibt es nur bei Flächen.")
        if not _zahl(wert) or wert < 0:
            raise Ungueltig(f"{feld} muss eine endliche Zahl ab 0 sein.")
        daten[feld] = float(wert)
    return daten


def pruefen(tabelle: Tabelle, roh: Any) -> dict[str, Any]:
    return poi_pruefen(roh) if tabelle == "pois" else messung_pruefen(roh)


@dataclass(frozen=True)
class Eintrag:
    id: str
    daten: dict[str, Any]
    version: int
    paket_revision: int | None
    erstellt_von: int | None
    erstellt_am: int
    geaendert_von: int | None
    geaendert_von_name: str | None
    geaendert_am: int
    erstellt_von_name: str | None = None
    herkunft: str = "mensch"
    pruefstand: str = GUELTIG
    loeschen_vorgeschlagen: int | None = None

    def als_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "daten": self.daten,
            "paketRevision": self.paket_revision, "erstelltVon": self.erstellt_von,
            "erstelltAm": self.erstellt_am, "geaendertVon": self.geaendert_von,
            "geaendertVonName": self.geaendert_von_name, "geaendertAm": self.geaendert_am,
            "erstelltVonName": self.erstellt_von_name, "herkunft": self.herkunft,
            "pruefstand": self.pruefstand, "loeschenVorgeschlagen": self.loeschen_vorgeschlagen,
        }


def _eintrag(r: sqlite3.Row) -> Eintrag:
    return Eintrag(
        id=r["id"], daten=json.loads(r["daten"]), version=int(r["version"]),
        paket_revision=r["paket_revision"], erstellt_von=r["erstellt_von"],
        erstellt_am=int(r["erstellt_am"]), geaendert_von=r["geaendert_von"],
        geaendert_von_name=r["geaendert_von_name"], geaendert_am=int(r["geaendert_am"]),
        erstellt_von_name=r["erstellt_von_name"], herkunft=r["herkunft"],
        pruefstand=r["pruefstand"], loeschen_vorgeschlagen=r["loeschen_vorgeschlagen"],
    )


def _select(tabelle: Tabelle) -> str:
    if tabelle not in TABELLEN:
        raise ValueError(tabelle)
    return (f"SELECT t.*, u.name AS geaendert_von_name, e.name AS erstellt_von_name"
            f" FROM {tabelle} t LEFT JOIN users u ON u.id = t.geaendert_von"
            " LEFT JOIN users e ON e.id = t.erstellt_von")


def _holen(conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        _select(tabelle) + " WHERE t.project_id=? AND t.id=?", (pid, eid),
    ).fetchone()
    return row


def _dump(daten: dict[str, Any]) -> str:
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"))


def liste(conn: sqlite3.Connection, tabelle: Tabelle, pid: str, *,
          sicht: Vorschlagssicht = NUR_GUELTIG) -> list[Eintrag]:
    """Aktive Einträge; offene Vorschläge nach `sicht`, verworfene nie. Exporte nutzen den
    Standard `NUR_GUELTIG`. Ein Löschvorschlag ist ebenfalls ein offener Vorschlag: sein
    Zeitpunkt steht nur in der Sicht der Prüfer (`alle`)."""
    rows = conn.execute(
        _select(tabelle) + " WHERE t.project_id=? AND t.geloescht_am IS NULL"
        " AND (t.pruefstand='gueltig' OR (t.pruefstand='vorschlag' AND (? OR t.erstellt_von=?)))"
        " ORDER BY t.erstellt_am, t.id", (pid, int(sicht.alle), sicht.nutzer_id),
    ).fetchall()
    eintraege = [_eintrag(r) for r in rows]
    if sicht.alle:
        return eintraege
    return [replace(e, loeschen_vorgeschlagen=None) for e in eintraege]


def offene_vorschlaege(conn: sqlite3.Connection, tabelle: Tabelle, pid: str) -> list[Eintrag]:
    """Was ein Mensch noch prüfen muss: Vorschläge und gültige Einträge mit Löschvorschlag."""
    rows = conn.execute(
        _select(tabelle) + " WHERE t.project_id=? AND t.geloescht_am IS NULL"
        " AND (t.pruefstand='vorschlag' OR (t.pruefstand='gueltig'"
        " AND t.loeschen_vorgeschlagen IS NOT NULL)) ORDER BY t.erstellt_am, t.id", (pid,),
    ).fetchall()
    return [_eintrag(r) for r in rows]


def anlegen(
    conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str | None,
    daten: dict[str, Any], *, nutzer_id: int | None, revision: int | None, kanal: str = "web",
    token_name: str | None = None, now: int | None = None, eigen: Eigentum | None = None,
    ki: bool = False,
) -> Eintrag:
    if eid is None:
        eid = _PRAEFIX[tabelle] + secrets.token_hex(6)
    elif not ID_RE.fullmatch(eid):
        raise Ungueltig("Ungültige ID (erlaubt: Buchstaben, Ziffern, _ und -, bis 64 Zeichen).")
    n = _jetzt(now)
    with transaktion(conn):
        # ID-Kollision vor der Obergrenze (Final-Fix C/D): eine wiederholte Anfrage an der Grenze
        # bekommt 409 mit dem vorhandenen Eintrag (der Abgleich erkennt ihn als schon gespeichert)
        # statt 422.
        alt = _holen(conn, tabelle, pid, eid)
        # Fremde ID (aktiv oder weich gelöscht) → 403 vor dem Konflikt: kein fremder Eintrag
        # im Konfliktkörper, keine Wiederherstellung eines fremden Eintrags.
        if alt is not None:
            _eigen(alt, eigen)
        # Ein verworfener Vorschlag gilt als nie dagewesen: die ID ist frei, sein Inhalt kommt in
        # keinem Konfliktkörper und keinem Protokoll-`vorher` zurück.
        verworfen = alt is not None and alt["pruefstand"] == VERWORFEN
        if alt is not None and alt["geloescht_am"] is None and not verworfen:
            raise Konflikt(_eintrag(alt).als_json(), ID_VERGEBEN)
        if alt is not None and eigen is not None and not verworfen:
            # Wiederherstellen: nur was der Rückmelder selbst gelöscht hat (ein Löschen durch
            # die Moderation bleibt endgültig) und nur eine Rückmeldung (kein beförderter Befund).
            if alt["geaendert_von"] != eigen.nutzer_id:
                raise StarletteHTTPException(status_code=403, detail=NUR_SELBST_GELOESCHTE)
            _gespeichert_rueckmeldung(json.loads(alt["daten"]), eigen)
        # Kategorie erst nach der ID-Kollision: eine Wiederholung nach verlorener Antwort (auch nach
        # einer Herabstufung zum Rückmelder) bekommt 409 mit dem gespeicherten Eintrag, nicht 422.
        _kategorie(daten, eigen)
        anzahl = conn.execute(
            f"SELECT COUNT(*) FROM {tabelle} WHERE project_id=? AND geloescht_am IS NULL"
            " AND pruefstand <> 'verworfen'", (pid,),
        ).fetchone()[0]
        if anzahl >= ANZAHL_MAX:
            raise Ungueltig(f"Höchstens {ANZAHL_MAX} {_MEHRZAHL[tabelle]} je Projekt.")
        if eigen is not None:
            eigene = conn.execute(
                f"SELECT COUNT(*) FROM {tabelle} WHERE project_id=? AND erstellt_von=?"
                " AND geloescht_am IS NULL AND pruefstand <> 'verworfen'", (pid, eigen.nutzer_id),
            ).fetchone()[0]
            if eigene >= RUECKMELDUNGEN_MAX:
                raise Ungueltig(f"Höchstens {RUECKMELDUNGEN_MAX} eigene Rückmeldungen je Projekt.")
        herkunft, stand = ("ki", VORSCHLAG) if ki else ("mensch", GUELTIG)
        if alt is None:
            conn.execute(
                f"INSERT INTO {tabelle} (project_id, id, daten, paket_revision, erstellt_von,"
                " erstellt_am, geaendert_von, geaendert_am, version, herkunft, pruefstand)"
                " VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (pid, eid, _dump(daten), revision, nutzer_id, n, nutzer_id, n, herkunft, stand),
            )
        else:
            # Über einem verworfenen Vorschlag entsteht der Eintrag neu, ebenso ein Vorschlag auf
            # einer gelöschten ID: neuer Ersteller (sonst gehörte der Vorschlag dem früheren
            # Ersteller — Sicht und Eigentum liefen auseinander). Sonst Wiederherstellen. Beides
            # setzt Herkunft und Prüfstand frisch.
            conn.execute(
                f"UPDATE {tabelle} SET daten=?, paket_revision=?, geaendert_von=?,"
                " geaendert_am=?, version=version+1, geloescht_am=NULL, herkunft=?,"
                " pruefstand=?, loeschen_vorgeschlagen=NULL,"
                " erstellt_von=CASE WHEN ? THEN ? ELSE erstellt_von END,"
                " erstellt_am=CASE WHEN ? THEN ? ELSE erstellt_am END"
                " WHERE project_id=? AND id=?",
                (_dump(daten), revision, nutzer_id, n, herkunft, stand, verworfen or ki,
                 nutzer_id, verworfen or ki, n, pid, eid),
            )
        neu_angelegt = alt is None or verworfen
        if ki:
            aktion = "vorgeschlagen"
        else:
            aktion = "angelegt" if neu_angelegt else "wiederhergestellt"
        protokoll.ereignis(
            conn, kanal=kanal, nutzer_id=nutzer_id, project_id=pid,
            art=f"{ART[tabelle]}.{aktion}", objekt_id=eid,
            vorher=None if neu_angelegt or alt is None else json.loads(alt["daten"]),
            nachher=daten, token_name=token_name, now=n,
        )
        neu = _holen(conn, tabelle, pid, eid)
    if neu is None:
        raise RuntimeError("Eintrag nach dem Anlegen nicht lesbar")
    return _eintrag(neu)


def _vorhanden(conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str,
               eigen: Eigentum | None = None) -> sqlite3.Row:
    """Aktiver Eintrag (404 bei gelöscht oder verworfen), Rückmelder nur eigene (403)."""
    alt = _holen(conn, tabelle, pid, eid)
    if alt is None or alt["geloescht_am"] is not None or alt["pruefstand"] == VERWORFEN:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    _eigen(alt, eigen)
    return alt


def _version(alt: sqlite3.Row, version: int) -> None:
    if int(alt["version"]) != version:
        raise Konflikt(_eintrag(alt).als_json())


def _ki_nur_eigener_vorschlag(alt: sqlite3.Row, nutzer_id: int | None, ki: bool) -> None:
    """Ein Agent ändert nur seinen eigenen offenen Vorschlag (403 vor der Versionsprüfung)."""
    if ki and (alt["pruefstand"] != VORSCHLAG or alt["erstellt_von"] is None
               or alt["erstellt_von"] != nutzer_id):
        raise StarletteHTTPException(status_code=403, detail=KI_NUR_VORSCHLAG)


def _zusammenfuehren(tabelle: Tabelle, alt: dict[str, Any], patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise Ungueltig("Keine Änderung angegeben.")
    neu = {**alt, **patch}
    if tabelle == "messungen":
        if neu.get("kind") != alt.get("kind"):
            raise Ungueltig("Die Messart lässt sich nicht ändern.")
        # Neue Eckpunkte machen die alten Flächenwerte ungültig: jeden nicht mitgelieferten
        # verwerfen (auch wenn nur einer der beiden kommt — Final-Fix C/D).
        if "points" in patch:
            for f in (*_FLAECHENWERTE, "pointSources"):
                if f not in patch:
                    neu.pop(f, None)
    if tabelle == "pois" and "position" in patch and "positionSource" not in patch:
        neu.pop("positionSource", None)
    return neu


def aendern(
    conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str, patch: dict[str, Any],
    version: int, *, nutzer_id: int | None, kanal: str = "web", token_name: str | None = None,
    now: int | None = None, eigen: Eigentum | None = None, ki: bool = False,
) -> Eintrag:
    n = _jetzt(now)
    with transaktion(conn):
        alt = _vorhanden(conn, tabelle, pid, eid, eigen)
        _ki_nur_eigener_vorschlag(alt, nutzer_id, ki)
        _version(alt, version)
        ist_vorschlag = alt["pruefstand"] == VORSCHLAG
        vorher = json.loads(alt["daten"])
        # Gespeicherte Kategorie vor dem Zusammenführen: sonst verwandelt ein mitgeschicktes
        # „Rückmeldung“ einen Befund (früher eigener oder vom Bearbeiter beförderter) zurück.
        _gespeichert_rueckmeldung(vorher, eigen)
        daten = pruefen(tabelle, _zusammenfuehren(tabelle, vorher, patch))
        _kategorie(daten, eigen)
        conn.execute(
            f"UPDATE {tabelle} SET daten=?, geaendert_von=?, geaendert_am=?,"
            " version=version+1 WHERE project_id=? AND id=? AND version=?",
            (_dump(daten), nutzer_id, n, pid, eid, version),
        )
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=nutzer_id, project_id=pid,
                           art=f"{ART[tabelle]}."
                           f"{'vorschlag_geaendert' if ist_vorschlag else 'geaendert'}",
                           objekt_id=eid, vorher=vorher, nachher=daten, token_name=token_name,
                           now=n)
        # Ein Vorschlag hat keinen Faden (faden.py: BEFUND_IST_VORSCHLAG), also keine Markierung.
        if tabelle == "pois" and not ist_vorschlag:
            # Import erst hier: faden → projektverwaltung → inhalte wäre beim Laden ein Kreis.
            from . import faden

            # Befund-Faden: geänderte Markierung als automatischer Eintrag, gleiche Transaktion.
            felder = [f for f in MARKIERUNG_FELDER if vorher.get(f) != daten.get(f)]
            if felder and faden.aktiviert(conn, pid):
                faden.einfuegen(
                    conn, pid, eid, art="markierung", text=None,
                    wert={"vorher": {f: vorher.get(f) for f in felder},
                          "nachher": {f: daten.get(f) for f in felder}},
                    intern=False, nutzer=nutzer_id, kanal=kanal, token_name=token_name, now=n,
                )
        neu = _holen(conn, tabelle, pid, eid)
    if neu is None:
        raise RuntimeError("Eintrag nach dem Ändern nicht lesbar")
    return _eintrag(neu)


def _weich_loeschen(conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str,
                    alt: sqlite3.Row, aktion: str, *, nutzer_id: int | None, kanal: str,
                    token_name: str | None, n: int) -> None:
    conn.execute(
        f"UPDATE {tabelle} SET geloescht_am=?, geaendert_von=?, geaendert_am=?,"
        " version=version+1, loeschen_vorgeschlagen=NULL WHERE project_id=? AND id=?",
        (n, nutzer_id, n, pid, eid),
    )
    protokoll.ereignis(conn, kanal=kanal, nutzer_id=nutzer_id, project_id=pid,
                       art=f"{ART[tabelle]}.{aktion}", objekt_id=eid,
                       vorher=json.loads(alt["daten"]), token_name=token_name, now=n)


Pruefschritt = Literal["angenommen", "verworfen", "loeschen_vorgeschlagen", "loeschen_verworfen"]
# Fester SET-Teil je Prüfschritt (kein Text von außen im SQL); `?` nur bei loeschen_vorgeschlagen.
_PRUEF_SET: dict[str, str] = {
    "angenommen": "pruefstand='gueltig'",
    "verworfen": "pruefstand='verworfen'",
    "loeschen_vorgeschlagen": "loeschen_vorgeschlagen=?",
    "loeschen_verworfen": "loeschen_vorgeschlagen=NULL",
}


def _pruefstand_setzen(conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str,
                       aktion: Pruefschritt, *, nutzer_id: int | None,
                       kanal: str, token_name: str | None, n: int, werte: tuple[Any, ...] = (),
                       vorher: Any = None, nachher: Any = None) -> sqlite3.Row:
    """Ein Prüf-Schritt (SET-Teil aus `_PRUEF_SET`, `werte` für seine Platzhalter) mit
    Versionssprung und Ereignis `<art>.<aktion>`; neuer Stand zurück."""
    conn.execute(
        f"UPDATE {tabelle} SET {_PRUEF_SET[aktion]}, geaendert_von=?,"
        " geaendert_am=?, version=version+1 WHERE project_id=? AND id=?",
        (*werte, nutzer_id, n, pid, eid),
    )
    protokoll.ereignis(conn, kanal=kanal, nutzer_id=nutzer_id, project_id=pid,
                       art=f"{ART[tabelle]}.{aktion}", objekt_id=eid, vorher=vorher,
                       nachher=nachher, token_name=token_name, now=n)
    neu = _holen(conn, tabelle, pid, eid)
    if neu is None:
        raise RuntimeError("Eintrag nach dem Prüfen nicht lesbar")
    return neu


def loeschen(
    conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str, version: int, *,
    nutzer_id: int | None, kanal: str = "web", token_name: str | None = None,
    now: int | None = None, eigen: Eigentum | None = None, ki: bool = False,
) -> Eintrag | None:
    """None, wenn gelöscht; der Eintrag, wenn ein Agent Gültiges nur zum Löschen vorschlägt."""
    n = _jetzt(now)
    with transaktion(conn):
        alt = _vorhanden(conn, tabelle, pid, eid, eigen)
        if alt["pruefstand"] == VORSCHLAG:
            _ki_nur_eigener_vorschlag(alt, nutzer_id, ki)
            _version(alt, version)
            _weich_loeschen(conn, tabelle, pid, eid, alt, "vorschlag_geloescht",
                            nutzer_id=nutzer_id, kanal=kanal, token_name=token_name, n=n)
            return None
        _version(alt, version)
        if ki:
            return _eintrag(_pruefstand_setzen(
                conn, tabelle, pid, eid, "loeschen_vorgeschlagen", werte=(n,),
                nutzer_id=nutzer_id, kanal=kanal, token_name=token_name, n=n,
                vorher=json.loads(alt["daten"])))
        _weich_loeschen(conn, tabelle, pid, eid, alt, "geloescht", nutzer_id=nutzer_id,
                        kanal=kanal, token_name=token_name, n=n)
    return None


def annehmen(
    conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str, version: int, *,
    nutzer_id: int | None, kanal: str, token_name: str | None, now: int | None = None,
    eigen: Eigentum | None = None,
) -> Eintrag | None:
    """Vorschlag wird gültig (Ereignis `….angenommen`, Export) — oder ein Löschvorschlag wird
    ausgeführt (`….geloescht`, Rückgabe None). Vorrang 404 → 403 → 409 → 422."""
    n = _jetzt(now)
    with transaktion(conn):
        alt = _vorhanden(conn, tabelle, pid, eid, eigen)
        _version(alt, version)
        if alt["pruefstand"] == VORSCHLAG:
            # Rückmelder machen nur eine Rückmeldung gültig, keinen Befund.
            _gespeichert_rueckmeldung(json.loads(alt["daten"]), eigen)
            return _eintrag(_pruefstand_setzen(
                conn, tabelle, pid, eid, "angenommen",
                nutzer_id=nutzer_id, kanal=kanal, token_name=token_name, n=n,
                nachher=json.loads(alt["daten"])))
        if alt["loeschen_vorgeschlagen"] is None:
            raise Ungueltig(KEIN_VORSCHLAG)
        _weich_loeschen(conn, tabelle, pid, eid, alt, "geloescht", nutzer_id=nutzer_id,
                        kanal=kanal, token_name=token_name, n=n)
    return None


def verwerfen(
    conn: sqlite3.Connection, tabelle: Tabelle, pid: str, eid: str, version: int, *,
    nutzer_id: int | None, kanal: str, token_name: str | None, now: int | None = None,
    eigen: Eigentum | None = None,
) -> Eintrag | None:
    """Vorschlag verwerfen (danach unsichtbar, Rückgabe None) oder einen Löschvorschlag
    zurücknehmen (Eintrag bleibt gültig). Vorrang 404 → 403 → 409 → 422."""
    n = _jetzt(now)
    with transaktion(conn):
        alt = _vorhanden(conn, tabelle, pid, eid, eigen)
        _version(alt, version)
        if alt["pruefstand"] == VORSCHLAG:
            _pruefstand_setzen(conn, tabelle, pid, eid, "verworfen",
                               nutzer_id=nutzer_id, kanal=kanal,
                               token_name=token_name, n=n, vorher=json.loads(alt["daten"]))
            return None
        if alt["loeschen_vorgeschlagen"] is None:
            raise Ungueltig(KEIN_VORSCHLAG)
        return _eintrag(_pruefstand_setzen(
            conn, tabelle, pid, eid, "loeschen_verworfen",
            nutzer_id=nutzer_id, kanal=kanal, token_name=token_name, n=n))

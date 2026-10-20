"""Rolle Rückmelder: legt nur Einträge der Kategorie „Rückmeldung“ an und ändert oder löscht nur
eigene POIs. Messungen, Projekt-Metadaten, Export, Verlauf und Verwaltung bleiben verschlossen.

Die Rechte-Matrix läuft über alle vier Rollen und beide Anmeldewege (Cookie-Sitzung mit CSRF,
Schreib-Token per Bearer ohne CSRF wie ein MCP-Aufruf). Vorrang: 404 → 403 → 409; ein fremder
Eintrag bekommt nie den Konfliktkörper `aktuell`.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_api_bearer import anon, auth, token
from test_authz import (
    authz,
    mit_paketen,  # noqa: F401  (Fixture)
)

from scanview_server import db, inhalte, konten, verwaltung
from scanview_server.web import Ungueltig, bearer_pfad

BASIS = "/api/v1/projekte/ost/pois"
MESS = "/api/v1/projekte/ost/messungen"
PROJEKT = "/api/v1/projekte/ost"
ROLLEN = ("betrachter", "rueckmelder", "bearbeiter", "admin")
KANAELE = ("sitzung", "token")
STRECKE: dict[str, Any] = {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]]}


def _poi(titel: str = "Riss", kategorie: str = "Rückmeldung") -> dict[str, Any]:
    return {"title": titel, "category": kategorie, "note": "Zu Befund: Riss Nordwand",
            "position": [1.0, 2.0, 3.0], "station": None}


@dataclass(frozen=True)
class Weg:
    """Ein Konto auf einem Anmeldeweg: Cookie-Sitzung mit CSRF-Kopf oder Bearer-Token."""

    nutzer: konten.Nutzer
    c: TestClient
    kopf: dict[str, str]

    def get(self, pfad: str) -> httpx.Response:
        return self.c.get(pfad, headers=self.kopf)

    def post(self, pfad: str, koerper: dict[str, Any]) -> httpx.Response:
        return self.c.post(pfad, headers=self.kopf, json=koerper)

    def patch(self, pfad: str, koerper: dict[str, Any]) -> httpx.Response:
        return self.c.patch(pfad, headers=self.kopf, json=koerper)

    def delete(self, pfad: str, version: int) -> httpx.Response:
        return self.c.delete(pfad, headers=self.kopf, params={"version": version})


@dataclass(frozen=True)
class Welt:
    app: TestClient
    konten: dict[str, tuple[konten.Nutzer, TestClient]]

    def sitzung(self, rolle: str) -> Weg:
        n, c = self.konten[rolle]
        return Weg(n, c, csrf())

    def weg(self, rolle: str, kanal: str) -> Weg:
        if kanal == "sitzung":
            return self.sitzung(rolle)
        n, _ = self.konten[rolle]
        return Weg(n, anon(self.app), auth(token(self.app, n, f"{rolle}-schreiben", "schreiben")))


@pytest.fixture
def welt(mit_paketen: TestClient) -> Welt:  # noqa: F811
    k = {
        "betrachter": person(mit_paketen, "t@x.de", grants={"ost": "betrachter"}),
        "rueckmelder": person(mit_paketen, "r@x.de", grants={"ost": "rueckmelder"}),
        "bearbeiter": person(mit_paketen, "b@x.de", grants={"ost": "bearbeiter"}),
        "admin": person(mit_paketen, "adm@x.de", admin=True),
    }
    return Welt(mit_paketen, k)


@pytest.fixture(params=KANAELE)
def kanal(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _fremder_poi(welt: Welt, eid: str) -> None:
    """Ein Befund, den ein anderes Konto (zweiter Bearbeiter) angelegt hat."""
    if "fremd" not in welt.konten:
        welt.konten["fremd"] = person(welt.app, "f@x.de", grants={"ost": "bearbeiter"})
    r = welt.sitzung("fremd").post(BASIS, {"id": eid, "daten": _poi("Befund", "Mangel")})
    assert r.status_code == 201


def _fremde_messung(welt: Welt, eid: str) -> None:
    if "fremd" not in welt.konten:
        welt.konten["fremd"] = person(welt.app, "f@x.de", grants={"ost": "bearbeiter"})
    assert welt.sitzung("fremd").post(MESS, {"id": eid, "daten": STRECKE}).status_code == 201


def _erwartet(betrachter: int, rueckmelder: int, bearbeiter: int,
              admin: int) -> dict[str, int]:
    return dict(zip(ROLLEN, (betrachter, rueckmelder, bearbeiter, admin), strict=True))


def _ohne_aktuell(r: httpx.Response) -> None:
    assert "aktuell" not in r.json()


def _mit_aktuell(r: httpx.Response) -> None:
    assert r.status_code == 409 and r.json()["aktuell"]["id"]


# --- Rechte-Matrix: alle Rollen und beide Anmeldewege -----------------------------------------


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_lesen(welt: Welt, kanal: str, rolle: str) -> None:
    w = welt.weg(rolle, kanal)
    assert w.get(BASIS).status_code == 200
    assert w.get(MESS).status_code == 200


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_anlegen_rueckmeldung(welt: Welt, kanal: str, rolle: str) -> None:
    r = welt.weg(rolle, kanal).post(BASIS, {"daten": _poi()})
    assert r.status_code == _erwartet(403, 201, 201, 201)[rolle]


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_anlegen_andere_kategorie(welt: Welt, kanal: str, rolle: str) -> None:
    r = welt.weg(rolle, kanal).post(BASIS, {"daten": _poi("X", "Mangel")})
    assert r.status_code == _erwartet(403, 422, 201, 201)[rolle]


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremden_poi_aendern(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    w = welt.weg(rolle, kanal)
    r = w.patch(f"{BASIS}/F-1", {"daten": {"note": "x"}, "version": 1})
    assert r.status_code == _erwartet(403, 403, 200, 200)[rolle]
    if r.status_code == 403:
        _ohne_aktuell(r)


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremden_poi_aendern_falsche_version(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    r = welt.weg(rolle, kanal).patch(f"{BASIS}/F-1", {"daten": {"note": "x"}, "version": 99})
    assert r.status_code == _erwartet(403, 403, 409, 409)[rolle]
    if r.status_code == 403:
        _ohne_aktuell(r)
    else:
        _mit_aktuell(r)


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremden_poi_loeschen(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    r = welt.weg(rolle, kanal).delete(f"{BASIS}/F-1", 1)
    assert r.status_code == _erwartet(403, 403, 204, 204)[rolle]
    if r.status_code == 403:
        _ohne_aktuell(r)


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremden_poi_loeschen_falsche_version(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    r = welt.weg(rolle, kanal).delete(f"{BASIS}/F-1", 99)
    assert r.status_code == _erwartet(403, 403, 409, 409)[rolle]
    if r.status_code == 403:
        _ohne_aktuell(r)
    else:
        _mit_aktuell(r)


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremde_aktive_id_anlegen(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    r = welt.weg(rolle, kanal).post(BASIS, {"id": "F-1", "daten": _poi()})
    assert r.status_code == _erwartet(403, 403, 409, 409)[rolle]
    if r.status_code == 403:
        _ohne_aktuell(r)
    else:
        _mit_aktuell(r)


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_fremde_geloeschte_id_anlegen(welt: Welt, kanal: str, rolle: str) -> None:
    _fremder_poi(welt, "F-1")
    assert welt.sitzung("fremd").delete(f"{BASIS}/F-1", 1).status_code == 204
    r = welt.weg(rolle, kanal).post(BASIS, {"id": "F-1", "daten": _poi()})
    assert r.status_code == _erwartet(403, 403, 201, 201)[rolle]
    ids = [p["id"] for p in welt.sitzung("bearbeiter").get(BASIS).json()["pois"]]
    assert ids == ([] if r.status_code == 403 else ["F-1"])


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_messungen(welt: Welt, kanal: str, rolle: str) -> None:
    _fremde_messung(welt, "M-1")
    w = welt.weg(rolle, kanal)
    assert w.post(MESS, {"daten": STRECKE}).status_code == _erwartet(403, 403, 201, 201)[rolle]
    p = w.patch(f"{MESS}/M-1", {"daten": {"points": [[0, 0, 0], [2, 0, 0]]}, "version": 1})
    assert p.status_code == _erwartet(403, 403, 200, 200)[rolle]
    version = 2 if p.status_code == 200 else 1
    d = w.delete(f"{MESS}/M-1", version)
    assert d.status_code == _erwartet(403, 403, 204, 204)[rolle]


def test_rueckmelder_misst_nicht(welt: Welt, kanal: str) -> None:
    """Die Rolle öffnet nur die POI-Routen: Messungen bleiben zu, auch eine neue eigene ID."""
    w = welt.weg("rueckmelder", kanal)
    assert w.post(MESS, {"id": "M-R", "daten": STRECKE}).status_code == 403
    assert w.get(MESS).json()["messungen"] == []


@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_projekt_metadaten(welt: Welt, kanal: str, rolle: str) -> None:
    r = welt.weg(rolle, kanal).patch(PROJEKT, {"titel": "X", "version": 0})
    assert r.status_code == _erwartet(403, 403, 200, 200)[rolle]


@pytest.mark.parametrize("pfad", ["export.csv", "export.json", "protokoll", "protokoll.jsonl"])
@pytest.mark.parametrize("rolle", ROLLEN)
def test_matrix_export_und_verlauf(welt: Welt, kanal: str, rolle: str, pfad: str) -> None:
    r = welt.weg(rolle, kanal).get(f"{PROJEKT}/{pfad}")
    assert r.status_code == _erwartet(403, 403, 200, 200)[rolle]


# --- Rückmelder: eigene Einträge, Kategorie, Obergrenze --------------------------------------


def test_rueckmelder_legt_an_und_aendert_nur_eigene(welt: Welt, kanal: str) -> None:
    r = welt.weg("rueckmelder", kanal)
    e = r.post(BASIS, {"id": "R-1", "daten": _poi()})
    assert e.status_code == 201
    assert e.json()["erstelltVon"] == r.nutzer.id and e.json()["erstelltVonName"] == "r"
    assert r.patch(f"{BASIS}/R-1", {"daten": {"note": "anders"}, "version": 1}).status_code == 200
    # Positivkontrolle zur 403-Prüfung oben: beim eigenen Eintrag kommt der Konflikt mit Körper.
    _mit_aktuell(r.patch(f"{BASIS}/R-1", {"daten": {"note": "x"}, "version": 1}))
    _mit_aktuell(r.delete(f"{BASIS}/R-1", 1))
    assert r.delete(f"{BASIS}/R-1", 2).status_code == 204


def test_bearbeiter_aendert_rueckmeldung(welt: Welt) -> None:
    welt.sitzung("rueckmelder").post(BASIS, {"id": "R-1", "daten": _poi()})
    b = welt.sitzung("bearbeiter")
    r = b.patch(f"{BASIS}/R-1", {"daten": {"category": "Mangel"}, "version": 1})
    assert r.status_code == 200 and r.json()["erstelltVonName"] == "r"
    assert r.json()["geaendertVonName"] == "b"


def test_rueckmelder_kategorie_ist_erzwungen(welt: Welt, kanal: str) -> None:
    r = welt.weg("rueckmelder", kanal)
    assert r.post(BASIS, {"daten": _poi(kategorie="Mangel")}).status_code == 422
    assert r.post(BASIS, {"daten": {**_poi(), "category": ""}}).status_code == 422
    assert r.post(BASIS, {"id": "R-2", "daten": _poi()}).status_code == 201
    assert r.patch(f"{BASIS}/R-2", {"daten": {"category": "Mangel"}, "version": 1}
                   ).status_code == 422
    assert r.patch(f"{BASIS}/R-2", {"daten": {"title": "neu"}, "version": 1}).status_code == 200


def test_kategorie_nfc(welt: Welt, kanal: str) -> None:
    r = welt.weg("rueckmelder", kanal)
    zerlegt = unicodedata.normalize("NFD", "Rückmeldung")
    assert zerlegt != "Rückmeldung"
    e = r.post(BASIS, {"id": "R-N", "daten": _poi(kategorie=zerlegt)})
    assert e.status_code == 201 and e.json()["daten"]["category"] == "Rückmeldung"
    a = r.patch(f"{BASIS}/R-N", {"daten": {"category": zerlegt}, "version": 1})
    assert a.status_code == 200 and a.json()["daten"]["category"] == "Rückmeldung"


@pytest.mark.parametrize("rolle", ["bearbeiter", "admin"])
@pytest.mark.parametrize("kategorie", ["Rückmeldung", "Mängel"])
def test_kategorie_nfc_fuer_alle_rollen(welt: Welt, kanal: str, rolle: str,
                                        kategorie: str) -> None:
    """Auch Bearbeiter speichern die Kategorie NFC und ohne Rand, damit ein Filter im Viewer
    eine zerlegt geschickte „Rückmeldung“ erkennt."""
    w = welt.weg(rolle, kanal)
    zerlegt = unicodedata.normalize("NFD", kategorie)
    assert zerlegt != kategorie
    e = w.post(BASIS, {"id": "B-N", "daten": _poi(kategorie=f" {zerlegt}\t")})
    assert e.status_code == 201 and e.json()["daten"]["category"] == kategorie
    a = w.patch(f"{BASIS}/B-N", {"daten": {"category": zerlegt}, "version": 1})
    assert a.status_code == 200 and a.json()["daten"]["category"] == kategorie


def test_rueckmelder_stellt_eigene_geloeschte_id_wieder_her(welt: Welt, kanal: str) -> None:
    r = welt.weg("rueckmelder", kanal)
    assert r.post(BASIS, {"id": "R-3", "daten": _poi()}).status_code == 201
    assert r.delete(f"{BASIS}/R-3", 1).status_code == 204
    w = r.post(BASIS, {"id": "R-3", "daten": _poi()})
    assert w.status_code == 201 and w.json()["erstelltVon"] == r.nutzer.id


def test_rueckmelder_stellt_von_bearbeiter_geloeschte_nicht_wieder_her(
        welt: Welt, kanal: str) -> None:
    """Löscht ein Bearbeiter eine Rückmeldung, bleibt sie gelöscht: Wiederherstellen per POST
    mit derselben ID nur, wenn der Rückmelder selbst gelöscht hat."""
    r = welt.weg("rueckmelder", kanal)
    assert r.post(BASIS, {"id": "R-M", "daten": _poi()}).status_code == 201
    assert welt.sitzung("bearbeiter").delete(f"{BASIS}/R-M", 1).status_code == 204
    w = r.post(BASIS, {"id": "R-M", "daten": _poi("neu")})
    assert w.status_code == 403
    _ohne_aktuell(w)
    assert _stand(welt, "R-M") is None


def test_rueckmelder_stellt_befoerderte_nicht_wieder_her(welt: Welt, kanal: str) -> None:
    """Eine beförderte Rückmeldung darf der Rückmelder löschen, aber nicht als Rückmeldung
    wiederherstellen — sonst überschriebe er den Befund des Bearbeiters."""
    r = welt.weg("rueckmelder", kanal)
    assert r.post(BASIS, {"id": "R-B", "daten": _poi()}).status_code == 201
    assert welt.sitzung("bearbeiter").patch(
        f"{BASIS}/R-B", {"daten": {"category": "Mangel"}, "version": 1}).status_code == 200
    assert r.delete(f"{BASIS}/R-B", 2).status_code == 204
    assert r.post(BASIS, {"id": "R-B", "daten": _poi()}).status_code == 422
    assert _stand(welt, "R-B") is None


def test_rueckmelder_obergrenze(welt: Welt, kanal: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert inhalte.RUECKMELDUNGEN_MAX == 100  # Zahl aus der Anforderung, hier nur verkleinert
    monkeypatch.setattr(inhalte, "RUECKMELDUNGEN_MAX", 2)
    r = welt.weg("rueckmelder", kanal)
    for i in range(2):
        assert r.post(BASIS, {"id": f"R-{i}", "daten": _poi(f"Nummer {i}")}).status_code == 201
    zu_viel = r.post(BASIS, {"daten": _poi("dritte")})
    assert zu_viel.status_code == 422 and "2" in zu_viel.json()["fehler"]
    # Die Grenze gilt je Rückmelder, nicht für Bearbeiter; gelöschte zählen nicht.
    assert welt.sitzung("bearbeiter").post(BASIS, {"daten": _poi("B", "Mangel")}).status_code == 201
    assert r.delete(f"{BASIS}/R-0", 1).status_code == 204
    assert r.post(BASIS, {"daten": _poi("vierte")}).status_code == 201


def _stand(welt: Welt, eid: str) -> dict[str, Any] | None:
    """Aktueller Stand eines aktiven POIs aus Sicht eines Bearbeiters (None = nicht aktiv)."""
    for p in welt.sitzung("bearbeiter").get(BASIS).json()["pois"]:
        if p["id"] == eid:
            return dict(p)
    return None


# Jede dieser Änderungen würde einen Eintrag anderer Kategorie in eine Rückmeldung zurückverwandeln
# und dabei Titel, Notiz und Kategorie überschreiben: ausdrücklich, zerlegt, mit Leerraum.
ZURUECK_ZUR_RUECKMELDUNG: list[dict[str, Any]] = [
    {"note": "x"},
    {"category": "Rückmeldung", "note": "x"},
    {"category": "Rückmeldung", "title": "Riss", "note": ""},
    {"category": unicodedata.normalize("NFD", "Rückmeldung")},
    {"category": " Rückmeldung "},
]


def test_rueckmelder_obergrenze_beim_wiederherstellen(
        welt: Welt, kanal: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inhalte, "RUECKMELDUNGEN_MAX", 2)
    r = welt.weg("rueckmelder", kanal)
    for eid in ("R-0", "R-1"):
        assert r.post(BASIS, {"id": eid, "daten": _poi(eid)}).status_code == 201
    assert r.delete(f"{BASIS}/R-0", 1).status_code == 204
    assert r.post(BASIS, {"id": "R-2", "daten": _poi("R-2")}).status_code == 201
    zu_viel = r.post(BASIS, {"id": "R-0", "daten": _poi("R-0")})
    assert zu_viel.status_code == 422 and "2" in zu_viel.json()["fehler"]
    assert _stand(welt, "R-0") is None
    assert r.delete(f"{BASIS}/R-1", 1).status_code == 204
    assert r.post(BASIS, {"id": "R-0", "daten": _poi("R-0")}).status_code == 201


def test_eintrag_ohne_urheber_ist_nie_eigen(welt: Welt, kanal: str) -> None:
    """Ein per Kommandozeile angelegter Eintrag (Urheber leer) gehört keinem Rückmelder, auch
    wenn er die Kategorie „Rückmeldung“ trägt (fail-closed)."""
    conn = db.connect(welt.app.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        inhalte.anlegen(conn, "pois", "ost", "C-1", inhalte.pruefen("pois", _poi()),
                        nutzer_id=None, revision=None, kanal="cli")
    finally:
        conn.close()
    r = welt.weg("rueckmelder", kanal)
    for antwort in (r.patch(f"{BASIS}/C-1", {"daten": {"note": "x"}, "version": 1}),
                    r.delete(f"{BASIS}/C-1", 1),
                    r.post(BASIS, {"id": "C-1", "daten": _poi()})):
        assert antwort.status_code == 403
        _ohne_aktuell(antwort)
    stand = _stand(welt, "C-1")
    assert stand is not None and stand["version"] == 1


def test_fremde_rueckmeldung_eines_zweiten_rueckmelders(welt: Welt, kanal: str) -> None:
    """Gleiche Kategorie, anderer Urheber: hier greift nur die Urheberprüfung."""
    n2, c2 = person(welt.app, "r2@x.de", grants={"ost": "rueckmelder"})
    zweiter = Weg(n2, c2, csrf())
    assert zweiter.post(BASIS, {"id": "R-F", "daten": _poi("fremd")}).status_code == 201
    r = welt.weg("rueckmelder", kanal)
    for antwort in (r.patch(f"{BASIS}/R-F", {"daten": {"note": "x"}, "version": 1}),
                    r.patch(f"{BASIS}/R-F", {"daten": {"note": "x"}, "version": 9}),
                    r.delete(f"{BASIS}/R-F", 1),
                    r.post(BASIS, {"id": "R-F", "daten": _poi()})):
        assert antwort.status_code == 403
        _ohne_aktuell(antwort)
    stand = _stand(welt, "R-F")
    assert stand is not None and stand["version"] == 1 and stand["daten"]["title"] == "fremd"
    # Auch nach dem Löschen durch den Urheber stellt ein anderer Rückmelder sie nicht her.
    assert zweiter.delete(f"{BASIS}/R-F", 1).status_code == 204
    assert r.post(BASIS, {"id": "R-F", "daten": _poi()}).status_code == 403
    assert _stand(welt, "R-F") is None


def test_obergrenze_haelt_im_wettlauf(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallele Anlagen desselben Rückmelders über eigene Verbindungen: Zählen und Schreiben
    liegen in einer Transaktion (BEGIN IMMEDIATE), also entstehen genau so viele wie erlaubt.
    Eine kleine Verzögerung zwischen Lesen und Schreiben öffnet das Fenster absichtlich."""
    grenze, threads, je_thread = 5, 8, 3
    monkeypatch.setattr(inhalte, "RUECKMELDUNGEN_MAX", grenze)
    holen = inhalte._holen

    def langsam_holen(*a: Any) -> Any:
        zeile = holen(*a)
        time.sleep(0.002)
        return zeile

    monkeypatch.setattr(inhalte, "_holen", langsam_holen)
    n, _ = welt.konten["rueckmelder"]
    pfad = welt.app.app.state.settings.db_path  # type: ignore[attr-defined]
    start = threading.Barrier(threads)
    ergebnisse: list[str] = []
    sperre = threading.Lock()

    def lauf(nr: int) -> None:
        conn = db.connect(pfad)
        try:
            start.wait()
            for i in range(je_thread):
                try:
                    inhalte.anlegen(conn, "pois", "ost", None, inhalte.pruefen("pois", _poi(
                        f"Lauf {nr}-{i}")), nutzer_id=n.id, revision=None,
                        eigen=inhalte.Eigentum(n.id))
                    ergebnis = "angelegt"
                except Ungueltig:
                    ergebnis = "grenze"
                except Exception as fehler:  # Sperrfehler sichtbar machen
                    ergebnis = repr(fehler)
                with sperre:
                    ergebnisse.append(ergebnis)
        finally:
            conn.close()

    laeufe = [threading.Thread(target=lauf, args=(i,)) for i in range(threads)]
    for t in laeufe:
        t.start()
    for t in laeufe:
        t.join(timeout=60)
    assert len(ergebnisse) == threads * je_thread
    assert ergebnisse.count("angelegt") == grenze, ergebnisse
    assert ergebnisse.count("grenze") == threads * je_thread - grenze, ergebnisse
    conn = db.connect(pfad)
    try:
        aktiv = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE project_id='ost' AND erstellt_von=?"
            " AND geloescht_am IS NULL", (n.id,)).fetchone()[0]
    finally:
        conn.close()
    assert aktiv == grenze


def test_wiederholte_anlage_nach_herabstufung_ergibt_konflikt(welt: Welt, kanal: str) -> None:
    """Antwort auf das Anlegen verloren, danach zum Rückmelder herabgestuft: die Wiederholung mit
    derselben ID bekommt 409 mit dem gespeicherten Eintrag (der Abgleich erkennt ihn als schon
    gespeichert), nicht 422 wegen der Kategorie."""
    n, c = person(welt.app, "h@x.de", grants={"ost": "bearbeiter"})
    koerper = {"id": "H-1", "daten": _poi("Befund", "Mangel")}
    assert Weg(n, c, csrf()).post(BASIS, koerper).status_code == 201
    conn = db.connect(welt.app.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        verwaltung.recht_setzen(conn, n.id, "ost", "rueckmelder", von=None, kanal="cli")
    finally:
        conn.close()
    w = (Weg(n, c, csrf()) if kanal == "sitzung"
         else Weg(n, anon(welt.app), auth(token(welt.app, n, "h-schreiben", "schreiben"))))
    r = w.post(BASIS, koerper)
    assert r.status_code == 409, r.text
    assert r.json()["aktuell"]["daten"]["category"] == "Mangel"


def test_alter_eigener_poi_anderer_kategorie_nur_loeschbar(welt: Welt, kanal: str) -> None:
    """Wer früher Bearbeiter war, kann einen eigenen Eintrag anderer Kategorie nur noch löschen —
    auch nicht, indem er die Kategorie „Rückmeldung“ mitschickt."""
    n, c = person(welt.app, "w@x.de", grants={"ost": "bearbeiter"})
    assert Weg(n, c, csrf()).post(BASIS, {"id": "W-1", "daten": _poi("Alt", "Mangel")}
                                  ).status_code == 201
    conn = db.connect(welt.app.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        verwaltung.recht_setzen(conn, n.id, "ost", "rueckmelder", von=None, kanal="cli")
    finally:
        conn.close()
    w = (Weg(n, c, csrf()) if kanal == "sitzung"
         else Weg(n, anon(welt.app), auth(token(welt.app, n, "w-schreiben", "schreiben"))))
    for patch in ZURUECK_ZUR_RUECKMELDUNG:
        assert w.patch(f"{BASIS}/W-1", {"daten": patch, "version": 1}).status_code == 422, patch
    stand = _stand(welt, "W-1")
    assert stand is not None and stand["version"] == 1
    assert stand["daten"]["category"] == "Mangel" and stand["daten"]["title"] == "Alt"
    assert stand["daten"]["note"] == _poi()["note"]
    assert w.delete(f"{BASIS}/W-1", 1).status_code == 204


def test_befoerderte_rueckmeldung_nur_loeschbar(welt: Welt, kanal: str) -> None:
    """Macht ein Bearbeiter aus einer Rückmeldung einen Befund, überschreibt der Rückmelder ihn
    nicht mehr; löschen darf er seinen Eintrag weiterhin."""
    r = welt.weg("rueckmelder", kanal)
    assert r.post(BASIS, {"id": "R-1", "daten": _poi()}).status_code == 201
    befund = {"category": "Mangel", "title": "Befund Nordwand", "note": "geprüft"}
    assert welt.sitzung("bearbeiter").patch(f"{BASIS}/R-1", {"daten": befund, "version": 1}
                                            ).status_code == 200
    for patch in ZURUECK_ZUR_RUECKMELDUNG:
        assert r.patch(f"{BASIS}/R-1", {"daten": patch, "version": 2}).status_code == 422, patch
    # Veraltete Version: beim eigenen Eintrag kommt der Konflikt vor der Kategorieprüfung.
    konflikt = r.patch(f"{BASIS}/R-1", {"daten": ZURUECK_ZUR_RUECKMELDUNG[0], "version": 1})
    assert konflikt.status_code == 409 and "aktuell" in konflikt.json()
    stand = _stand(welt, "R-1")
    assert stand is not None and stand["version"] == 2
    assert {k: stand["daten"][k] for k in befund} == befund
    assert r.delete(f"{BASIS}/R-1", 2).status_code == 204


def test_rueckmelder_fremdes_projekt_404_und_paket_lesbar(welt: Welt) -> None:
    r = welt.sitzung("rueckmelder")
    assert r.post("/api/v1/projekte/kirche/pois", {"daten": _poi()}).status_code == 404
    assert authz(r.c, "/data/ost/project.json") == 200
    assert authz(r.c, "/data/kirche/project.json") == 404


def test_rueckmelder_lese_token_schreibt_nicht(welt: Welt) -> None:
    n, _ = welt.konten["rueckmelder"]
    lesen = Weg(n, anon(welt.app), auth(token(welt.app, n, "r-lesen", "lesen")))
    assert lesen.get(BASIS).status_code == 200
    r = lesen.post(BASIS, {"daten": _poi()})
    assert r.status_code == 403
    welt.sitzung("rueckmelder").post(BASIS, {"id": "R-L", "daten": _poi()})
    assert lesen.patch(f"{BASIS}/R-L", {"daten": {"note": "x"}, "version": 1}).status_code == 403
    assert lesen.delete(f"{BASIS}/R-L", 1).status_code == 403


def test_rueckmelder_token_im_verlauf_als_api(welt: Welt) -> None:
    w = welt.weg("rueckmelder", "token")
    assert w.post(BASIS, {"id": "R-9", "daten": _poi()}).status_code == 201
    conn = db.connect(welt.app.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        zeile = conn.execute(
            "SELECT kanal, token_name, nutzer_id FROM ereignisse WHERE objekt_id='R-9'",
        ).fetchone()
    finally:
        conn.close()
    assert tuple(zeile) == ("api", "rueckmelder-schreiben", w.nutzer.id)


# --- Verwaltung: für den Rückmelder unsichtbar (404), für den Admin erreichbar -------------

VERWALTUNG = [
    "/api/v1/admin/nutzer", "/api/v1/admin/einladungen", "/api/v1/admin/statistik",
    "/api/v1/protokoll", "/api/v1/protokoll.jsonl", "/api/v1/lesezugriffe",
    "/api/v1/lesezugriffe.jsonl", "/api/v1/projekte/ost/lesezugriffe",
    "/api/v1/projekte/ost/lesezugriffe.jsonl",
]


@pytest.mark.parametrize("pfad", VERWALTUNG)
def test_rueckmelder_sieht_keine_verwaltung(welt: Welt, kanal: str, pfad: str) -> None:
    # Per Token: Pfade ohne Bearer-Anmeldung (Verwaltung) antworten 401, die übrigen 404.
    erwartet = 401 if kanal == "token" and not bearer_pfad(pfad) else 404
    assert welt.weg("rueckmelder", kanal).get(pfad).status_code == erwartet
    # Positivkontrolle: die Route gibt es, ein Admin mit Sitzung erreicht sie.
    assert welt.sitzung("admin").get(pfad).status_code == 200


# --- Sitzung liefert die eigene Konto-ID -------------------------------------------------------


@pytest.mark.parametrize("pfad", ["/api/sitzung", "/api/v1/sitzung"])
def test_sitzung_liefert_eigene_id(welt: Welt, pfad: str) -> None:
    n, c = welt.konten["rueckmelder"]
    s = c.get(pfad).json()
    assert s["nutzer"]["id"] == n.id and s["nutzer"]["name"] == "r"


def test_sitzung_per_token_liefert_eigene_id(welt: Welt) -> None:
    w = welt.weg("rueckmelder", "token")
    s = w.get("/api/v1/sitzung")
    assert s.status_code == 200 and s.json()["nutzer"]["id"] == w.nutzer.id


# --- Rolle bekannt, Vergabe mit Protokoll --------------------------------------------------


def test_rolle_ist_bekannt_und_einladbar(mit_paketen: TestClient) -> None:  # noqa: F811
    assert konten.ROLLEN == ("betrachter", "rueckmelder", "bearbeiter", "projektleitung")
    n, _ = person(mit_paketen, "r@x.de", grants={"ost": "rueckmelder"})
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        assert konten.rolle(conn, n, "ost") == "rueckmelder"
    finally:
        conn.close()


def test_admin_vergibt_und_entzieht_rueckmelder_mit_protokoll(
        mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "adm@x.de", admin=True)
    n, _ = person(mit_paketen, "r@x.de", grants={})
    r = adm.put(f"/api/v1/admin/nutzer/{n.id}/rechte/ost", headers=csrf(),
                json={"rolle": "rueckmelder"})
    assert (r.status_code, r.json()) == (200, {"vorher": None, "rolle": "rueckmelder"})
    r = adm.put(f"/api/v1/admin/nutzer/{n.id}/rechte/ost", headers=csrf(), json={"rolle": None})
    assert (r.status_code, r.json()) == (200, {"vorher": "rueckmelder", "rolle": None})
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        arten = [r["art"] for r in conn.execute(
            "SELECT art FROM ereignisse WHERE art LIKE 'recht.%' ORDER BY id")]
    finally:
        conn.close()
    assert arten[-2:] == ["recht.vergeben", "recht.entzogen"]


def test_unbekannte_rolle_422(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "adm@x.de", admin=True)
    n, _ = person(mit_paketen, "r@x.de", grants={})
    r = adm.put(f"/api/v1/admin/nutzer/{n.id}/rechte/ost", headers=csrf(), json={"rolle": "chef"})
    assert r.status_code == 422
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        with pytest.raises(ValueError):
            verwaltung.recht_setzen(conn, n.id, "ost", "chef", von=None, kanal="cli")
    finally:
        conn.close()

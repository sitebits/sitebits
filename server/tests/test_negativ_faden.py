"""Negativtests Befund-Faden (Kategorie NT-FADEN).

Interne Einträge erscheinen für Nicht-Bauherrenseite (Bearbeiter ohne Bauherrenseite, Rückmelder
und Betrachter — auch in einem Bauherrenseite-Gewerk — und jedes Lese-Token) in keinem Kanal:
Faden, Befundliste, POIs, Mängelliste (Druck/XLSX), CSV/JSON-Export, Verlauf/JSONL. Jede
Prüfung hat eine Positivkontrolle (die Bauherrenseite sieht den Text), damit sie nicht leer
besteht. Dazu Schreibrechte je Rolle, fremdes Projekt, Konflikt, Zurückziehen, Lese-Token, CSRF,
Schalter, Obergrenze und Link-Schema."""

from __future__ import annotations

import io
import json
import re
import secrets
import zipfile

import httpx
import pytest
from conftest import csrf
from fadenwelt import BASIS, FADEN, INTERN, OEFFENTLICH, Welt, welt  # noqa: F401  (Fixture)
from fastapi.testclient import TestClient

KANAELE = (
    FADEN, BASIS + "/befunde", BASIS + "/pois", BASIS + "/maengelliste",
    BASIS + "/maengelliste.xlsx", BASIS + "/export.csv", BASIS + "/export.json",
    BASIS + "/protokoll", BASIS + "/protokoll.jsonl",
)
NICHT_BAUHERRENSEITE = ("bear", "bear_ohne", "rueck", "rueck_bh", "betr")


def _inhalt(r: httpx.Response) -> str:
    if r.headers.get("content-type", "").startswith("application/vnd.openxmlformats"):
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return "".join(z.read(n).decode() for n in z.namelist())
    return r.text


def _alle(c: TestClient) -> dict[str, str]:
    """Antworttext je Kanal (nur 200 — 403/404 enthalten ohnehin keine Inhalte)."""
    aus = {}
    for pfad in KANAELE:
        r = c.get(pfad)
        assert r.status_code in (200, 403), (pfad, r.status_code)
        if r.status_code == 200:
            aus[pfad] = _inhalt(r)
    return aus


def _zurueckziehen_intern(welt: Welt) -> None:  # noqa: F811 — Fixture
    r = welt.post("bh", f"{FADEN}/{welt.intern_id}/zurueckziehen")
    assert r.status_code == 200


@pytest.mark.parametrize("wer", NICHT_BAUHERRENSEITE)
def test_intern_in_keinem_kanal_fuer_nicht_bauherrenseite(welt: Welt, wer: str) -> None:  # noqa: F811 — Fixture
    for pfad, text in _alle(welt.c[wer]).items():
        assert INTERN not in text, pfad
        assert f"P-1/{welt.intern_id}" not in text, pfad
    faden = welt.c[wer].get(FADEN).json()
    assert [e["schluessel"] for e in faden["eintraege"]] == [welt.oeffentlich_id]
    assert faden["rechte"]["intern"] is False


@pytest.mark.parametrize("wer", ["bh", "leitung", "admin"])
def test_positivkontrolle_bauherrenseite_sieht_intern(welt: Welt, wer: str) -> None:  # noqa: F811 — Fixture
    texte = _alle(welt.c[wer])
    for pfad in (FADEN, BASIS + "/export.json", BASIS + "/protokoll",
                 BASIS + "/protokoll.jsonl"):
        assert INTERN in texte[pfad], pfad
    assert OEFFENTLICH in texte[FADEN]


@pytest.mark.parametrize("wer", ["bh", "leitung"])
def test_lese_token_sieht_kein_intern(welt: Welt, wer: str) -> None:  # noqa: F811 — Fixture
    lese = welt.token(wer, "lesen")
    for pfad, text in _alle(lese).items():
        assert INTERN not in text, pfad
    schreib = welt.token(wer, "schreiben")
    assert INTERN in schreib.get(FADEN).text          # Positivkontrolle per Schreib-Token


def test_zurueckgezogener_interner_eintrag_bleibt_unsichtbar(welt: Welt) -> None:  # noqa: F811 — Fixture
    _zurueckziehen_intern(welt)
    for wer in ("bear", "rueck"):
        for pfad, text in _alle(welt.c[wer]).items():
            assert f"P-1/{welt.intern_id}" not in text, pfad
    verlauf = welt.c["bh"].get(BASIS + "/protokoll").json()["ereignisse"]
    assert any(e["art"] == "eintrag.zurueckgezogen" for e in verlauf)


def test_letzter_eintrag_verraet_kein_intern(welt: Welt) -> None:  # noqa: F811 — Fixture
    oeff = welt.sql("SELECT erstellt_am FROM befund_eintraege WHERE schluessel=?",
                    welt.oeffentlich_id)[0][0]
    welt.sql("INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, text, wert,"
             " intern, erstellt_von, erstellt_am, kanal) VALUES ('00000000000000aa', 'ost', 'P-1',"
             " 'kommentar', 'spaet', 'null', 1, ?, ?, 'web')", welt.id["bh"], oeff + 1000)
    assert welt.c["bear"].get(BASIS + "/befunde").json()["befunde"][0][
        "letzter_eintrag_am"] == oeff
    assert welt.c["bh"].get(BASIS + "/befunde").json()["befunde"][0][
        "letzter_eintrag_am"] == oeff + 1000


@pytest.mark.parametrize("wer", NICHT_BAUHERRENSEITE)
def test_nicht_bauherrenseite_schreibt_nie_intern(welt: Welt, wer: str) -> None:  # noqa: F811 — Fixture
    vorher = welt.sql("SELECT COUNT(*) FROM befund_eintraege")[0][0]
    r = welt.post(wer, FADEN, {"art": "kommentar", "text": "x", "intern": True})
    assert r.status_code == 403
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege")[0][0] == vorher


def test_intern_nur_kommentar_oder_link(welt: Welt) -> None:  # noqa: F811 — Fixture
    r = welt.post("bh", FADEN, {"art": "frist", "wert": "2026-12-01", "version": 1,
                                "intern": True})
    assert r.status_code == 422


def test_rueckmelder_status_nur_wenn_zustaendig(welt: Welt) -> None:  # noqa: F811 — Fixture
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "in_arbeit",
                                      "version": 1}).status_code == 403
    assert welt.post("rueck_bh", FADEN, {"art": "status", "wert": "in_arbeit",
                                         "version": 1}).status_code == 403
    welt.post("leitung", FADEN, {"art": "zustaendig", "version": 1,
                                 "wert": {"gewerk_id": welt.gewerk["Dach"], "nutzer_id": None}})
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "abgelehnt", "version": 2,
                                      "text": "nicht wir"}).status_code == 403
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "in_arbeit",
                                      "version": 2}).status_code == 201
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "erledigt",
                                      "version": 3}).status_code == 201
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "abgeschlossen",
                                      "version": 4}).status_code == 403
    assert welt.post("rueck", FADEN, {"art": "status", "wert": "offen", "version": 4,
                                      "text": "doch nicht"}).status_code == 403
    assert welt.stand()["status"] == "erledigt"


@pytest.mark.parametrize("koerper", [
    {"art": "frist", "wert": "2026-12-01", "version": 1},
    {"art": "zustaendig", "wert": None, "version": 1},
])
def test_rueckmelder_keine_frist_keine_zustaendigkeit(welt: Welt,  # noqa: F811 — Fixture
                                                       koerper: dict[str, object]) -> None:
    assert welt.post("rueck", FADEN, koerper).status_code == 403
    assert welt.stand()["version"] == 1


def test_bearbeiter_ohne_bauherrenseite_keine_pruefschritte(welt: Welt) -> None:  # noqa: F811 — Fixture
    assert welt.post("bear", FADEN, {"art": "status", "wert": "abgelehnt", "version": 1,
                                     "text": "kein Mangel"}).status_code == 403
    assert welt.post("bear", FADEN, {"art": "status", "wert": "erledigt",
                                     "version": 1}).status_code == 201
    assert welt.post("bear", FADEN, {"art": "status", "wert": "abgeschlossen",
                                     "version": 2}).status_code == 403
    assert welt.post("bh", FADEN, {"art": "status", "wert": "abgeschlossen",
                                   "version": 2}).status_code == 201


def test_betrachter_schreibt_nichts(welt: Welt) -> None:  # noqa: F811 — Fixture
    assert welt.post("betr", FADEN, {"art": "kommentar", "text": "x"}).status_code == 403
    assert welt.post("betr", f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen").status_code == 403


@pytest.mark.parametrize(("methode", "pfad"), [
    ("GET", "/api/v1/projekte/{pid}/pois/P-1/faden"),
    ("POST", "/api/v1/projekte/{pid}/pois/P-1/faden"),
    ("POST", "/api/v1/projekte/{pid}/pois/P-1/faden/0123456789abcdef/zurueckziehen"),
    ("GET", "/api/v1/projekte/{pid}/befunde"),
])
def test_fremdes_projekt_wie_nicht_vorhanden(welt: Welt, methode: str, pfad: str) -> None:  # noqa: F811 — Fixture
    c = welt.c["fremd"]
    koerper = {"art": "kommentar", "text": "x"} if methode == "POST" else None
    antworten = [c.request(methode, pfad.format(pid=p), json=koerper, headers=csrf())
                 for p in ("ost", "gibts-nicht")]
    assert [a.status_code for a in antworten] == [404, 404]
    assert antworten[0].content == antworten[1].content
    # Projekt ohne Recht, aber POI-ID eines anderen Projekts: ebenfalls 404 für die Leitung.
    r = welt.c["leitung"].request(methode, pfad.format(pid="kirche"), json=koerper,
                                  headers=csrf())
    assert r.status_code == 404


def test_veraltete_version_409_ohne_wirkung(welt: Welt) -> None:  # noqa: F811 — Fixture
    assert welt.post("bear", FADEN, {"art": "status", "wert": "in_arbeit",
                                     "version": 1}).status_code == 201
    r = welt.post("leitung", FADEN, {"art": "frist", "wert": "2026-12-01", "version": 1})
    assert r.status_code == 409 and r.json()["aktuell"]["version"] == 2
    assert welt.stand()["frist"] is None


def test_zurueckziehen_nur_verfasser(welt: Welt) -> None:  # noqa: F811 — Fixture
    pfad = f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen"
    for wer in ("leitung", "admin", "bh", "rueck"):
        assert welt.post(wer, pfad).status_code == 403
    # Interner Eintrag: für Nicht-Bauherrenseite nicht vorhanden (404 statt 403).
    assert welt.post("bear", f"{FADEN}/{welt.intern_id}/zurueckziehen").status_code == 404
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege WHERE zurueckgezogen_am IS NOT NULL"
                    )[0][0] == 0


def test_lese_token_schreibt_nichts(welt: Welt) -> None:  # noqa: F811 — Fixture
    lese = welt.token("bear", "lesen")
    vorher = welt.sql("SELECT COUNT(*) FROM ereignisse")[0][0]
    assert lese.post(FADEN, json={"art": "kommentar", "text": "x"}).status_code == 403
    assert lese.post(FADEN, json={"kaputt": 1}).status_code == 403
    assert lese.post(f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen").status_code == 403
    assert welt.sql("SELECT COUNT(*) FROM ereignisse")[0][0] == vorher
    schreib = welt.token("bear", "schreiben")
    r = schreib.post(FADEN, json={"art": "kommentar", "text": "per Token"})
    assert r.status_code == 201 and r.json()["eintrag"]["kanal"] == "api"


@pytest.mark.parametrize("kopf", [{}, {"Origin": "https://boese.test", "X-Scanview": "1"},
                                  {"Origin": "https://scanview.test"}])
def test_csrf(welt: Welt, kopf: dict[str, str]) -> None:  # noqa: F811 — Fixture
    c = welt.c["bear"]
    assert c.post(FADEN, json={"art": "kommentar", "text": "x"}, headers=kopf).status_code == 403
    pfad = f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen"
    assert c.post(pfad, headers=kopf).status_code == 403


def test_schalter_aus_alle_faden_wege_404(welt: Welt) -> None:  # noqa: F811 — Fixture
    welt.sql("UPDATE projekte SET befund_faeden=0")
    for wer in ("admin", "bear"):
        assert welt.c[wer].get(FADEN).status_code == 404
        assert welt.c[wer].get(BASIS + "/befunde").status_code == 404
        assert welt.post(wer, FADEN, {"art": "kommentar", "text": "x"}).status_code == 404
    assert welt.post("bear", f"{FADEN}/{welt.oeffentlich_id}/zurueckziehen").status_code == 404
    for pfad in (BASIS + "/export.json", BASIS + "/maengelliste"):
        assert OEFFENTLICH not in welt.c["bh"].get(pfad).text


def _fuellen(welt: Welt, intern: bool) -> None:  # noqa: F811 — Fixture
    """Bis zur Grenze: 1000 manuelle Einträge der jeweiligen Sichtbarkeit."""
    vorhanden = welt.sql("SELECT COUNT(*) FROM befund_eintraege WHERE poi_id='P-1' AND intern=?"
                         " AND art <> 'markierung'", int(intern))[0][0]
    conn = welt.conn()
    try:
        conn.executemany(
            "INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, text, wert,"
            " intern, erstellt_von, erstellt_am, kanal) VALUES (?, 'ost', 'P-1', 'kommentar',"
            " 'x', 'null', ?, ?, 1, 'web')",
            [(secrets.token_hex(8), int(intern), welt.id["bh"])
             for _ in range(1000 - vorhanden)])
    finally:
        conn.close()


def test_tausendundeinter_eintrag_422(welt: Welt) -> None:  # noqa: F811 — Fixture
    _fuellen(welt, intern=False)
    r = welt.post("bear", FADEN, {"art": "kommentar", "text": "eins zu viel"})
    assert r.status_code == 422 and "1000" in r.json()["fehler"]
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege WHERE poi_id='P-1' AND intern=0"
                    )[0][0] == 1000
    # Markierungen zählen nicht und werden nie blockiert: Ändern am POI bleibt möglich.
    r = welt.c["bear"].patch(BASIS + "/pois/P-1", headers=csrf(),
                             json={"daten": {"note": "neu"}, "version": 1})
    assert r.status_code == 200
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege WHERE art='markierung'")[0][0] == 1


def test_grenze_verraet_keine_internen(welt: Welt) -> None:  # noqa: F811 — Fixture
    _fuellen(welt, intern=True)
    assert welt.post("bear", FADEN, {"art": "kommentar", "text": "geht"}).status_code == 201
    r = welt.post("bh", FADEN, {"art": "kommentar", "text": "zu viel", "intern": True})
    assert r.status_code == 422 and "1000" in r.json()["fehler"]


def test_ids_verraten_keine_internen(welt: Welt) -> None:  # noqa: F811 — Fixture
    assert welt.post("bear", FADEN, {"art": "kommentar", "text": "danach"}).status_code == 201
    eintraege = welt.c["bear"].get(FADEN).json()["eintraege"]
    assert [e["text"] for e in eintraege] == [OEFFENTLICH, "danach"]
    assert all("id" not in e and re.fullmatch(r"[0-9a-f]{16}", e["schluessel"])
               for e in eintraege)
    interne_ids = {str(r[0]) for r in welt.sql("SELECT id FROM befund_eintraege")}
    poi = welt.c["bear"].get(BASIS + "/export.json").json()["pois"][0]
    assert all("id" not in e for e in poi["faden"])
    for ev in welt.c["bear"].get(BASIS + "/protokoll").json()["ereignisse"]:
        if ev["art"].startswith("eintrag."):
            poi_id, _, schluessel = ev["objekt"].partition("/")
            assert poi_id == "P-1" and schluessel not in interne_ids
            assert "id" not in ev["nachher"] and ev["nachher"]["schluessel"] == schluessel


@pytest.mark.parametrize("koerper", [
    '{"art": "kommentar", "text": "a\\ud800b"}',
    '{"art": "link", "wert": {"url": "https://example.test/", "titel": "\\ud800"}}',
    '{"art": "link", "wert": {"url": "https://\\ud800.example.test/", "titel": "x"}}',
    '{"art": "link", "wert": {"url": "/?p=ost#b=\\ud800", "titel": "x"}}',
])
def test_einzelne_surrogate_422(welt: Welt, koerper: str) -> None:  # noqa: F811 — Fixture
    vorher = welt.sql("SELECT COUNT(*) FROM befund_eintraege")[0][0]
    r = welt.c["bear"].post(FADEN, content=koerper.encode(),
                            headers={**csrf(), "Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert "UTF-8" in r.json()["fehler"]
    assert welt.sql("SELECT COUNT(*) FROM befund_eintraege")[0][0] == vorher


def test_vorrang_404_vor_403_und_422(welt: Welt) -> None:  # noqa: F811 — Fixture
    welt.sql("UPDATE projekte SET befund_faeden=0")
    assert welt.post("betr", FADEN, {"art": "kommentar", "text": "x"}).status_code == 404
    assert welt.post("betr", FADEN, {"kaputt": 1}).status_code == 404
    assert welt.post("bear", FADEN, {"art": 5}).status_code == 404
    lese = welt.token("bear", "lesen")
    assert lese.post(FADEN, json={"art": "kommentar", "text": "x"}).status_code == 404
    assert welt.c["bear"].get(BASIS + "/befunde", params={"status": "unbekannt"}).status_code == 404
    pfad = f"{FADEN}/kein-schluessel/zurueckziehen"
    assert welt.post("betr", pfad).status_code == 404
    welt.sql("UPDATE projekte SET befund_faeden=1")
    assert welt.post("betr", FADEN, {"kaputt": 1}).status_code == 403
    assert welt.post("bear", FADEN, {"kaputt": 1}).status_code == 422


@pytest.mark.parametrize("abfrage", [{"status": "unbekannt"}, {"frist": "morgen"},
                                     {"zustaendig": "alle-anderen"}, {"kategorie": "x" * 61}])
def test_befunde_fremdes_projekt_404_vor_422(welt: Welt,  # noqa: F811 — Fixture
                                              abfrage: dict[str, str]) -> None:
    for pid in ("ost", "gibts-nicht"):
        r = welt.c["fremd"].get(f"/api/v1/projekte/{pid}/befunde", params=abfrage)
        assert r.status_code == 404, (pid, r.status_code)
    assert welt.c["bear"].get(BASIS + "/befunde", params=abfrage).status_code == 422


@pytest.mark.parametrize("url", ["javascript:alert(1)", "JAVASCRIPT:alert(1)",
                                 "data:text/html,x", "http://example.test/x",
                                 "//example.test/x", "/?p=ost#x\n"])
def test_link_nur_https_oder_befundlink(welt: Welt, url: str) -> None:  # noqa: F811 — Fixture
    r = welt.post("bear", FADEN, {"art": "link", "wert": {"url": url, "titel": "x"}})
    assert r.status_code == 422
    assert "javascript" not in json.dumps(welt.c["bh"].get(FADEN).json()).lower()

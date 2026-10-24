"""Auswertungsjobs über die API v1: einreihen, Rechte, Freigabe, Löschen, Auslieferung.

Der Worker läuft hier mit dem Test-Läufer (kein Prozess); die echte Isolation prüft
test_isolation.py, die ganze Kette test_e2e_auswertung.py."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from jobswelt import BASIS, PRUEFEN, Welt, welt, welt_ohne_jobs  # noqa: F401

from scanview_server import auftraege, protokoll
from scanview_server.routen_auswertungen import _NAMEN_SQL, _namen


def _status(w: Welt, aid: str, wer: str = "leitung") -> dict[str, Any]:
    r = w.c[wer].get(f"{BASIS}/{aid}")
    assert r.status_code == 200, r.text
    d: dict[str, Any] = r.json()
    return d


def _ereignisse(w: Welt, art: str) -> list[protokoll.Ereignis]:
    """Ereignisse einer Art im Projekt „ost“, älteste zuerst."""
    conn = w.conn()
    try:
        alle = protokoll.lesen(conn, project_id="ost", arten=("auswertung",))
    finally:
        conn.close()
    return [e for e in reversed(alle) if e.art == art]


# --- Einreihen und Rechte --------------------------------------------------------------------


def test_projektleitung_reiht_ein_und_sieht_den_stand(welt: Welt) -> None:  # noqa: F811
    r = welt.post("leitung", BASIS, PRUEFEN)
    assert r.status_code == 202
    a = r.json()
    assert a["status"] == "wartend" and a["warteplatz"] == 1 and a["worker"] == "aus"
    assert a["typ"] == "auswerten_pruefen" and a["optionen"] == {"profil": "bestandserfassung"}
    assert a["kanal"] == "web" and a["erstelltVonName"] == "Name leitung"
    assert a["ergebnis"] is None and a["freigegeben"] is False
    [e] = _ereignisse(welt, "auswertung.gestartet")
    assert e.objekt_id == a["id"] and e.nachher == {"typ": "auswerten_pruefen",
                                                    "optionen": {"profil": "bestandserfassung"}}
    liste = welt.c["leitung"].get(BASIS).json()
    assert [x["id"] for x in liste["auswertungen"]] == [a["id"]] and liste["worker"] == "aus"


def test_optionen_ohne_profil_nehmen_das_standardprofil(welt: Welt) -> None:  # noqa: F811
    r = welt.post("admin", BASIS, {"typ": "auswerten_pruefen"})
    assert r.status_code == 202 and r.json()["optionen"] == {"profil": "bestandserfassung"}


@pytest.mark.parametrize(("wer", "status"), [
    ("bear", 403), ("rueck", 403), ("betr", 403), ("fremd", 404), ("anonym", 401),
])
def test_starten_nur_projektleitung(welt: Welt, wer: str, status: int) -> None:  # noqa: F811
    assert welt.post(wer, BASIS, PRUEFEN).status_code == status
    with welt.jobs() as jc:
        assert jc.execute("SELECT COUNT(*) FROM auftraege").fetchone()[0] == 0


def test_starten_ohne_csrf_403(welt: Welt) -> None:  # noqa: F811
    assert welt.c["leitung"].post(BASIS, json=PRUEFEN).status_code == 403


@pytest.mark.parametrize(("koerper", "text"), [
    ({"typ": "gibts_nicht"}, "Unbekannter Auftragstyp"),
    ({"typ": "auswerten_sonne_dach"}, "noch nicht verfügbar"),
    ({"typ": "neu_wolke"}, "noch nicht verfügbar"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "unbekannt"}}, "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "../../etc/passwd"}},
     "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "/tmp/eigener.toml"}},
     "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "bestandserfassung; rm -rf /"}},
     "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "bestandserfassung\n--pdf"}},
     "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": 3}}, "Unbekanntes Profil"),
    ({"typ": "auswerten_pruefen", "optionen": {"katalog": "/tmp/k.toml"}}, "Unbekannte Option"),
    ({"typ": "auswerten_pruefen", "optionen": {"profil": "bestandserfassung", "aus": "/"}},
     "Unbekannte Option"),
    ({"typ": "auswerten_pruefen", "optionen": {"--pdf": "1"}}, "Unbekannte Option"),
    ({"typ": "../auswerten_pruefen"}, "Unbekannter Auftragstyp"),
])
def test_typ_und_optionen_strikt(welt: Welt, koerper: dict[str, Any],  # noqa: F811
                                 text: str) -> None:
    r = welt.post("leitung", BASIS, koerper)
    assert r.status_code == 422, r.text
    assert text in r.json()["fehler"]
    with welt.jobs() as jc:
        assert jc.execute("SELECT COUNT(*) FROM auftraege").fetchone()[0] == 0


@pytest.mark.parametrize("koerper", [
    {"typ": "auswerten_pruefen", "extra": 1}, {"optionen": {}}, {"typ": ["x"]},
    {"typ": "auswerten_pruefen", "optionen": ["profil"]}, {"typ": "x" * 41},
])
def test_kaputter_koerper_422_mit_vorrang(welt: Welt, koerper: dict[str, Any]) -> None:  # noqa: F811
    assert welt.post("leitung", BASIS, koerper).status_code == 422
    # Vorrang wie überall: kein Recht → 404, Rolle zu klein → 403, erst dann 422.
    assert welt.post("fremd", BASIS, koerper).status_code == 404
    assert welt.post("bear", BASIS, koerper).status_code == 403
    assert welt.post("anonym", BASIS, koerper).status_code == 401
    assert welt.token("leitung", "lesen").post(BASIS, json=koerper).status_code == 403


def test_hoechstens_drei_offene_je_projekt(welt: Welt) -> None:  # noqa: F811
    for _ in range(auftraege.OFFEN_JE_PROJEKT_MAX):
        welt.starten()
    r = welt.post("leitung", BASIS, PRUEFEN)
    assert r.status_code == 409 and "schon 3" in r.json()["fehler"]
    # anderes Projekt ist davon unberührt
    assert welt.post("fremd", "/api/v1/projekte/kirche/auswertungen", PRUEFEN).status_code == 202


def test_lese_token_darf_nicht_starten(welt: Welt) -> None:  # noqa: F811
    assert welt.token("leitung", "lesen").post(BASIS, json=PRUEFEN).status_code == 403


def test_agent_token_darf_starten_aber_nicht_freigeben_oder_loeschen(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("leitung", agent=True)
    r = agent.post(BASIS, json=PRUEFEN)
    assert r.status_code == 202 and r.json()["kanal"] == "agent"
    aid = r.json()["id"]
    welt.rechnen()
    assert agent.get(f"{BASIS}/{aid}").json()["status"] == "fertig"
    assert agent.get(f"{BASIS}/{aid}/ergebnis").status_code == 200
    r = agent.post(f"{BASIS}/{aid}/freigabe", json={"freigegeben": True})
    assert r.status_code == 403 and "Mensch" in r.json()["fehler"]
    assert agent.delete(f"{BASIS}/{aid}").status_code == 403
    assert _status(welt, aid)["freigegeben"] is False
    # ein Agent-Token eines Bearbeiters startet nicht
    assert welt.token("bear", agent=True).post(BASIS, json=PRUEFEN).status_code == 403
    # ein normaler Token der Projektleitung darf freigeben (Mensch mit Skript)
    mensch = welt.token("leitung")
    assert mensch.post(f"{BASIS}/{aid}/freigabe", json={"freigegeben": True}).status_code == 200


# --- Worker-Ergebnis, Sichtbarkeit, Freigabe ---------------------------------------------------


def test_fertig_ergebnis_und_bericht(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    laeufer = welt.rechnen()
    [lauf] = laeufer.laeufe
    a = _status(welt, aid)
    assert a["status"] == "fertig" and a["urteil"] == "nicht erfüllt"
    assert a["zaehlung"] == {"erfüllt": 1, "nicht erfüllt": 1, "nicht prüfbar": 0}
    assert a["ergebnis"] == f"{BASIS}/{aid}/ergebnis" and a["fehler"] is None
    r = welt.c["leitung"].get(a["ergebnis"])
    assert r.status_code == 200 and r.json()["gesamt"]["urteil"] == "nicht erfüllt"
    r = welt.c["leitung"].get(a["bericht"])
    assert r.status_code == 200 and r.text.startswith("<!doctype html>")
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox;") and "default-src 'none'" in csp and "script" not in csp
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"
    # Ablage: nur die übernommenen Dateien, Arbeitsordner weg
    assert welt.settings.berichte_dir is not None
    ordner = welt.settings.berichte_dir / "ost" / aid
    assert sorted(p.name for p in ordner.iterdir()) == [
        "L09-karte.png", "bericht.html", "ergebnis.json", "lauf.json"]
    assert sorted(p.name for p in ordner.parent.iterdir()) == [aid]
    assert not lauf.arbeit.exists()


def test_lesezugriff_auf_ergebnis_und_bericht_wird_erfasst(welt: Welt) -> None:  # noqa: F811
    erste, zweite = welt.starten(), welt.starten()
    welt.rechnen()
    for aid in (erste, zweite):
        assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 200
        assert welt.c["leitung"].get(f"{BASIS}/{aid}/bericht").status_code == 200
    welt.c["leitung"].get(BASIS)  # Status allein ist kein Lesezugriff auf Inhalte
    conn = welt.conn()
    try:
        zeilen = conn.execute("SELECT art, objekt FROM lesezugriffe ORDER BY id").fetchall()
    finally:
        conn.close()
    # je Bericht ein Eintrag im Fenster (ergebnis und bericht sind derselbe Inhalt)
    assert sorted(tuple(z) for z in zeilen) == sorted(
        [("api", f"auswertung/{erste}"), ("api", f"auswertung/{zweite}")])


def test_bearbeiter_sieht_erst_nach_freigabe(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    assert welt.c["bear"].get(BASIS).json()["auswertungen"] == []
    for pfad in (f"{BASIS}/{aid}", f"{BASIS}/{aid}/ergebnis", f"{BASIS}/{aid}/bericht"):
        assert welt.c["bear"].get(pfad).status_code == 404
    for wer in ("betr", "rueck"):
        assert welt.c[wer].get(BASIS).status_code == 403
    assert welt.c["fremd"].get(BASIS).status_code == 404
    assert welt.c["anonym"].get(BASIS).status_code == 401
    # Bearbeiter darf nicht freigeben
    assert welt.post("bear", f"{BASIS}/{aid}/freigabe", {"freigegeben": True}).status_code == 403
    r = welt.post("leitung", f"{BASIS}/{aid}/freigabe", {"freigegeben": True})
    assert r.status_code == 200 and r.json()["freigegeben"] is True
    assert [a["id"] for a in welt.c["bear"].get(BASIS).json()["auswertungen"]] == [aid]
    assert welt.c["bear"].get(f"{BASIS}/{aid}/ergebnis").status_code == 200
    assert welt.c["betr"].get(f"{BASIS}/{aid}").status_code == 403
    # entziehen
    r = welt.post("admin", f"{BASIS}/{aid}/freigabe", {"freigegeben": False})
    assert r.status_code == 200 and r.json()["freigegeben"] is False
    assert welt.c["bear"].get(f"{BASIS}/{aid}").status_code == 404
    arten = [(e.vorher, e.nachher) for e in _ereignisse(welt, "auswertung.freigabe")]
    assert arten == [({"freigegeben": False}, {"freigegeben": True}),
                     ({"freigegeben": True}, {"freigegeben": False})]


def test_freigabe_nur_fertig_und_strikter_koerper(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    assert welt.post("leitung", f"{BASIS}/{aid}/freigabe",
                     {"freigegeben": True}).status_code == 409
    for koerper in ({"freigegeben": "ja"}, {"freigegeben": 1}, {}, {"freigegeben": True, "x": 1}):
        assert welt.post("leitung", f"{BASIS}/{aid}/freigabe", koerper).status_code == 422
        assert welt.post("bear", f"{BASIS}/{aid}/freigabe", koerper).status_code == 403
        assert welt.post("fremd", f"{BASIS}/{aid}/freigabe", koerper).status_code == 404


def test_ergebnis_vor_dem_ende_409(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 409
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/bericht").status_code == 409


@pytest.mark.parametrize("aid", ["0123456789abcdef", "%2e%2e", "a%2f..%2fb", "0123456789ABCDEF",
                                 "x" * 16])
def test_unbekannte_oder_ungueltige_id_404(welt: Welt, aid: str) -> None:  # noqa: F811
    assert welt.c["leitung"].get(f"{BASIS}/{aid}").status_code == 404
    assert welt.delete("leitung", f"{BASIS}/{aid}").status_code == 404


def test_fremdes_projekt_sieht_auftrag_nicht(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    # Auftrags-ID aus „ost“ unter „kirche“ abfragen → 404, auch für die dortige Leitung
    for pfad in (f"/api/v1/projekte/kirche/auswertungen/{aid}",
                 f"/api/v1/projekte/kirche/auswertungen/{aid}/ergebnis"):
        assert welt.c["fremd"].get(pfad).status_code == 404
    assert welt.c["fremd"].get(f"{BASIS}/{aid}").status_code == 404


def test_symlink_in_der_ablage_wird_nicht_ausgeliefert(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    assert welt.settings.berichte_dir is not None
    ordner = welt.settings.berichte_dir / "ost" / aid
    geheim = welt.tmp / "geheim.html"
    geheim.write_text("GEHEIM")
    (ordner / "bericht.html").unlink()
    os.symlink(geheim, ordner / "bericht.html")
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/bericht").status_code == 404
    # auch ein verlinkter Auftragsordner zählt nicht
    (ordner / "ergebnis.json").rename(welt.tmp / "e.json")
    ordner.rename(welt.tmp / "weg")
    os.symlink(welt.tmp / "weg", ordner)
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 404


def test_getauschter_projektordner_auch_im_wettlauf(welt: Welt, monkeypatch) -> None:  # noqa: F811
    """Ein übernommener Worker tauscht den Projektordner gegen einen Symlink, nachdem eine
    resolve()-Prüfung bestanden hätte: resolve() lügt hier, das Öffnen muss trotzdem scheitern."""
    aid = welt.starten()
    welt.rechnen()
    assert welt.settings.berichte_dir is not None
    projekt = welt.settings.berichte_dir / "ost"
    fremd = welt.tmp / "fremd"
    projekt.rename(fremd)
    os.symlink(fremd, projekt)
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 404


def test_hart_verlinkte_datei_wird_nicht_ausgeliefert(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    assert welt.settings.berichte_dir is not None
    ziel = welt.settings.berichte_dir / "ost" / aid / "ergebnis.json"
    os.link(ziel, welt.tmp / "zweiter-name.json")
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 404


def test_kaputtes_ergebnis_json_404(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    assert welt.settings.berichte_dir is not None
    (welt.settings.berichte_dir / "ost" / aid / "ergebnis.json").write_text("[" * 100_000)
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 404


def test_urteil_aus_jobs_db_wird_beim_lesen_geprueft(welt: Welt) -> None:  # noqa: F811
    """jobs.db ist für den Worker beschreibbar — der Webdienst traut ihren Werten nicht."""
    aid = welt.starten()
    with welt.jobs() as jc:
        jc.execute("UPDATE auftraege SET status='fertig', urteil=?, zaehlung=? WHERE id=?",
                   ("\x1b[31m" + "x" * 200, json.dumps({"a": "viel", "b": True}), aid))
    a = _status(welt, aid)
    assert a["urteil"] is None and a["zaehlung"] is None


# --- Löschen ------------------------------------------------------------------------------------


def test_loeschen_durch_projektleitung_entfernt_bericht_zeile_und_freigabe(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen()
    assert welt.post("leitung", f"{BASIS}/{aid}/freigabe", {"freigegeben": True}).status_code == 200
    for wer, status in (("bear", 403), ("fremd", 404), ("anonym", 401)):
        assert welt.delete(wer, f"{BASIS}/{aid}").status_code == status
    assert welt.c["leitung"].delete(f"{BASIS}/{aid}").status_code == 403  # ohne CSRF
    assert welt.delete("leitung", f"{BASIS}/{aid}").status_code == 204
    # sofort unsichtbar, auch bevor der Worker aufräumt
    assert welt.c["leitung"].get(f"{BASIS}/{aid}").status_code == 404
    assert welt.c["leitung"].get(BASIS).json()["auswertungen"] == []
    assert welt.delete("leitung", f"{BASIS}/{aid}").status_code == 404
    assert welt.settings.berichte_dir is not None
    ordner = welt.settings.berichte_dir / "ost" / aid
    assert ordner.is_dir()
    welt.rechnen()
    assert not ordner.exists()
    with welt.jobs() as jc:
        assert jc.execute("SELECT COUNT(*) FROM auftraege WHERE id=?", (aid,)).fetchone()[0] == 0
    conn = welt.conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM auswertung_freigaben").fetchone()[0] == 0
    finally:
        conn.close()
    [e] = _ereignisse(welt, "auswertung.geloescht")
    assert e.objekt_id == aid and e.vorher is None and e.nachher is None


def test_laufender_auftrag_wird_nicht_geloescht(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    with welt.jobs() as jc:
        jc.execute("UPDATE auftraege SET status='laufend' WHERE id=?", (aid,))
    r = welt.delete("leitung", f"{BASIS}/{aid}")
    assert r.status_code == 409 and "läuft" in r.json()["fehler"]


def test_wartender_auftrag_geloescht_wird_nie_gerechnet(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    assert welt.delete("admin", f"{BASIS}/{aid}").status_code == 204
    laeufer = welt.rechnen()
    assert laeufer.laeufe == []


# --- Abgeschaltet, Gesundheit, OpenAPI ----------------------------------------------------------


def test_ohne_jobs_db_gibt_es_keine_auswertungen(welt_ohne_jobs: Welt) -> None:  # noqa: F811
    w = welt_ohne_jobs
    assert w.post("leitung", BASIS, PRUEFEN).status_code == 404
    assert w.c["leitung"].get(BASIS).status_code == 404
    assert w.c["leitung"].get(f"{BASIS}/0123456789abcdef").status_code == 404
    assert w.c["anonym"].get("/api/gesundheit").json() == {"ok": True}


def test_gesundheit_meldet_den_worker(welt: Welt) -> None:  # noqa: F811
    g = welt.c["anonym"].get("/api/gesundheit")
    assert g.status_code == 200 and g.json() == {"ok": True, "worker": "aus"}
    w, _l = welt.worker()
    w.start()
    assert welt.c["anonym"].get("/api/gesundheit").json()["worker"] == "aktiv"
    with welt.jobs() as jc:
        jc.execute("UPDATE worker SET herzschlag=?", (int(time.time()) - auftraege.STILL_S - 5,))
    assert welt.c["anonym"].get("/api/gesundheit").json() == {"ok": True, "worker": "still"}
    assert welt.c["leitung"].get(BASIS).json()["worker"] == "still"
    w.conn.close()


def test_openapi_fuer_token_enthaelt_auswertungen(welt: Welt) -> None:  # noqa: F811
    pfade = welt.token("leitung").get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/projekte/{pid}/auswertungen" in pfade
    assert "/api/v1/projekte/{pid}/auswertungen/{aid}/ergebnis" in pfade


def test_protokoll_ereignis_beim_start_per_token_traegt_kanal_api(welt: Welt) -> None:  # noqa: F811
    welt.token("leitung").post(BASIS, json=PRUEFEN)
    [e] = _ereignisse(welt, "auswertung.gestartet")
    assert e.kanal == "api"


# --- Konfiguration ------------------------------------------------------------------------------


def _umgebung(monkeypatch: pytest.MonkeyPatch, **mehr: str) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": "https://x",
                 "SCANVIEW_RP_ID": "x", **mehr}.items():
        monkeypatch.setenv(k, v)


def test_jobs_konfiguration_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_server.config import Settings

    for k in ("SCANVIEW_JOBS_DB", "SCANVIEW_BERICHTE"):
        monkeypatch.delenv(k, raising=False)
    _umgebung(monkeypatch)
    s = Settings.from_env()
    assert s.jobs_db is None and s.berichte_dir is None
    _umgebung(monkeypatch, SCANVIEW_JOBS_DB="/j/jobs.db", SCANVIEW_BERICHTE="/b")
    s = Settings.from_env()
    assert str(s.jobs_db) == "/j/jobs.db" and str(s.berichte_dir) == "/b"


@pytest.mark.parametrize("mehr", [
    {"SCANVIEW_JOBS_DB": "/j/jobs.db", "SCANVIEW_BERICHTE": ""},
    {"SCANVIEW_JOBS_DB": "", "SCANVIEW_BERICHTE": "/b"},
    {"SCANVIEW_JOBS_DB": "jobs.db", "SCANVIEW_BERICHTE": "/b"},
])
def test_jobs_konfiguration_nur_gemeinsam_und_absolut(monkeypatch: pytest.MonkeyPatch,
                                                      mehr: dict[str, str]) -> None:
    from scanview_server.config import Settings

    _umgebung(monkeypatch, **mehr)
    with pytest.raises(SystemExit):
        Settings.from_env()


# --- Namen der Ersteller (SAST B-SAST-SEMGREP-2CFE92E6E70113AC, B-SAST-BANDIT-3669E3431FDC51DE) ---


def test_namen_mit_fester_anfrage_ohne_zusammengesetztes_sql() -> None:
    """Die Anfrage ist für jede Zahl von IDs derselbe Text; die IDs gehen als ein Parameter."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "eins"), (2, "zwei"), (3, "drei")])
    gesehen: list[str] = []
    conn.set_trace_callback(gesehen.append)
    assert _namen(conn, set()) == {}
    assert _namen(conn, {2}) == {2: "zwei"}
    assert _namen(conn, {1, 3, 99}) == {1: "eins", 3: "drei"}
    assert _namen(conn, set(range(1, 5001))) == {1: "eins", 2: "zwei", 3: "drei"}
    assert len(gesehen) == 3
    assert all(s.startswith(_NAMEN_SQL.removesuffix("?))")) for s in gesehen)
    assert "?" in _NAMEN_SQL and "{" not in _NAMEN_SQL
    conn.close()

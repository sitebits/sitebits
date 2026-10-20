"""G7: Vorschläge im Befund-Faden (faden.py direkt an der Datenbank)."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from scanview_server import db, faden, inhalte, projektverwaltung
from scanview_server.web import Konflikt, Ungueltig

POI = {"title": "Befund", "category": "Mangel", "note": "", "position": [0, 0, 0],
       "station": None}


@dataclass
class Faden:
    conn: sqlite3.Connection
    gewerke: dict[str, int]

    def sicht(self, wer: str) -> faden.Sicht:
        # bh = Bearbeiter Bauherrenseite (Nutzer 2), bear = Bearbeiter Dach (Nutzer 3)
        if wer == "bh":
            return faden.Sicht(nutzer=2, rolle="bearbeiter", gewerk=self.gewerke["Bauherr"],
                               bauherrenseite=True, intern=True)
        return faden.Sicht(nutzer=3, rolle="bearbeiter", gewerk=self.gewerke["Dach"],
                           bauherrenseite=False, intern=False)

    def version(self) -> int:
        return int(faden.stand(self.conn, "ost", "P-1")["version"])

    def gewerk(self, name: str) -> int:
        return self.gewerke[name]

    def arten(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT art FROM ereignisse ORDER BY id")]


@pytest.fixture
def f(tmp_path: Path) -> Faden:
    c = db.connect(tmp_path / "ki-faden.db")
    db.migrate(c)
    for uid in (1, 2, 3):
        c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)",
                  (uid, f"u{uid}@example.test", f"Person {uid}"))
    projektverwaltung.einrichten(c, "ost", "bauprojekt", von=1, now=0)
    g = {name: projektverwaltung.gewerk_anlegen(c, "ost", name=name, kurz=name[:2],
                                                bauherrenseite=bhs, von=1).id
         for name, bhs in (("Bauherr", True), ("Dach", False))}
    for uid, rolle, gw in ((2, "bearbeiter", "Bauherr"), (3, "bearbeiter", "Dach")):
        c.execute("INSERT INTO project_roles (user_id, project_id, role, gewerk_id)"
                  " VALUES (?,?,?,?)", (uid, "ost", rolle, g[gw]))
    inhalte.anlegen(c, "pois", "ost", "P-1", dict(POI), nutzer_id=1, revision=1)
    return Faden(c, g)


def test_status_per_agent_wird_vorschlag(f: Faden) -> None:
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status", wert="erledigt",
                          ki=True, kanal="api", now=100)
    assert (e["art"], e["herkunft"], e["offen"]) == ("vorschlag", "ki", True)
    assert e["wert"] == {"art": "status", "wert": "erledigt"}
    assert st["status"] == "offen"
    assert f.arten()[-1] == "eintrag.vorgeschlagen"


def test_vorschlag_unmoeglicher_wechsel_422(f: Faden) -> None:
    with pytest.raises(Ungueltig):
        faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status", wert="abgeschlossen",
                      ki=True, kanal="api")


def test_vorschlag_annehmen_setzt_stand_mit_bezug(f: Faden) -> None:
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist",
                          wert="2026-11-01", ki=True, kanal="api")
    neu, st2 = faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                        version=st["version"], kanal="web", token_name=None)
    assert (neu["art"], neu["bezug"], st2["frist"]) == ("frist", e["schluessel"], "2026-11-01")
    with pytest.raises(StarletteHTTPException) as fehler:  # VORSCHLAG_ERLEDIGT
        faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                 version=st2["version"], kanal="web", token_name=None)
    assert fehler.value.status_code == 409


def test_vorschlag_annehmen_braucht_recht_des_pruefers(f: Faden) -> None:
    # Bearbeiter ohne Bauherrenseite darf „abgeschlossen“ nicht setzen, auch nicht per Annehmen.
    faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status", wert="erledigt",
                  version=f.version(), kanal="web")
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status",
                          wert="abgeschlossen", ki=True, kanal="api")
    with pytest.raises(StarletteHTTPException) as fehler:
        faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bear"),
                                 version=st["version"], kanal="web", token_name=None)
    assert fehler.value.status_code == 403


def test_kommentar_per_agent_traegt_herkunft(f: Faden) -> None:
    e, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="kommentar", text="Hinweis",
                         ki=True, kanal="api")
    assert (e["art"], e["herkunft"]) == ("kommentar", "ki")


def test_vorschlag_von_aussen_nicht_anlegbar(f: Faden) -> None:
    with pytest.raises(Ungueltig):
        faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="vorschlag", wert={}, kanal="web")


def test_faden_am_vorschlags_befund_409(f: Faden) -> None:
    f.conn.execute("UPDATE pois SET pruefstand='vorschlag' WHERE id='P-1'")
    with pytest.raises(StarletteHTTPException) as fehler:
        faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="kommentar", text="x",
                      kanal="web")
    assert fehler.value.status_code == 409


def test_verwerfen_ist_kommentar_mit_bezug(f: Faden) -> None:
    e, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="zustaendig",
                         wert={"gewerk_id": f.gewerk("Dach"), "nutzer_id": None},
                         ki=True, kanal="api")
    k = faden.vorschlag_verwerfen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                  kanal="web", token_name=None)
    assert (k["art"], k["bezug"], k["text"]) == ("kommentar", e["schluessel"],
                                                 "Vorschlag verworfen.")
    assert faden.lesen(f.conn, "ost", "P-1", intern=True)[-2]["offen"] is False


def _betrachter() -> faden.Sicht:
    # Konto 4 hat weder P-1 noch einen Vorschlag angelegt (P-1 stammt von Konto 1).
    return faden.Sicht(nutzer=4, rolle="betrachter", gewerk=None, bauherrenseite=False,
                       intern=False)


def test_vorschlag_eintrag_fuer_betrachter_unsichtbar(f: Faden) -> None:
    e, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist",
                         wert="2026-11-01", ki=True, kanal="api")
    faden.vorschlag_verwerfen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                              kanal="web", token_name=None)
    sichtbar = faden.lesen(f.conn, "ost", "P-1", intern=False, s=_betrachter())
    assert sichtbar == []  # weder Vorschlag noch Verwerfen-Kommentar
    assert len(faden.lesen(f.conn, "ost", "P-1", intern=False, s=f.sicht("bear"))) == 2


def test_angenommener_eintrag_fuer_betrachter_ohne_bezug(f: Faden) -> None:
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist",
                          wert="2026-11-01", ki=True, kanal="api")
    faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                             version=st["version"], kanal="web", token_name=None)
    [neu] = faden.lesen(f.conn, "ost", "P-1", intern=False, s=_betrachter())
    assert (neu["art"], neu["bezug"]) == ("frist", None)


def _status_404(f: Faden, s: faden.Sicht) -> bool:
    with pytest.raises(StarletteHTTPException) as fehler:
        faden.faden(f.conn, "ost", "P-1", s)
    return fehler.value.status_code == 404


def test_vorschlags_poi_nur_fuer_pruefer(f: Faden) -> None:
    f.conn.execute("UPDATE pois SET pruefstand='vorschlag' WHERE id='P-1'")
    assert _status_404(f, _betrachter())
    assert faden.faden(f.conn, "ost", "P-1", f.sicht("bear"))["eintraege"] == []


def test_verworfener_poi_fuer_alle_404(f: Faden) -> None:
    f.conn.execute("UPDATE pois SET pruefstand='verworfen' WHERE id='P-1'")
    assert _status_404(f, _betrachter()) and _status_404(f, f.sicht("bh"))
    assert "P-1" not in faden.staende(f.conn, "ost")


def test_ereignisarten_der_vorschlagswelt(f: Faden) -> None:
    e, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist",
                         wert="2026-11-01", ki=True, kanal="api")
    faden.vorschlag_verwerfen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                              text="Frist zu früh", kanal="web", token_name=None)
    assert f.arten()[-2:] == ["eintrag.vorgeschlagen", "eintrag.vorschlag_verworfen"]
    with pytest.raises(StarletteHTTPException) as fehler:
        faden.vorschlag_verwerfen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                  kanal="web", token_name=None)
    assert fehler.value.status_code == 409


def test_ablehnen_vorschlag_braucht_begruendung_die_mitgeht(f: Faden) -> None:
    with pytest.raises(Ungueltig):
        faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status", wert="abgelehnt",
                      ki=True, kanal="api")
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="status", wert="abgelehnt",
                          text="Kein Mangel, Bestand", ki=True, kanal="api")
    neu, st2 = faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                        version=st["version"], kanal="web", token_name=None)
    assert (neu["text"], neu["herkunft"], st2["status"]) == ("Kein Mangel, Bestand", "ki",
                                                             "abgelehnt")


def test_annehmen_veraltete_version_409_nichts_angenommen(f: Faden) -> None:
    e, st = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="frist",
                          wert="2026-11-01", ki=True, kanal="api")
    faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="frist", wert="2026-12-01",
                  version=st["version"], kanal="web")
    with pytest.raises(Konflikt):  # 409 mit aktuellem Stand
        faden.vorschlag_annehmen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                                 version=st["version"], kanal="web", token_name=None)
    assert faden.stand(f.conn, "ost", "P-1")["frist"] == "2026-12-01"
    [v] = [x for x in faden.lesen(f.conn, "ost", "P-1", intern=True) if x["art"] == "vorschlag"]
    assert v["offen"] is True


def test_kein_vorschlag_eintrag_422(f: Faden) -> None:
    k, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="kommentar", text="x",
                         kanal="web")
    with pytest.raises(Ungueltig):
        faden.vorschlag_annehmen(f.conn, "ost", "P-1", k["schluessel"], f.sicht("bh"),
                                 version=f.version(), kanal="web", token_name=None)


def test_vorschlag_zaehlt_nicht_als_letzte_aktivitaet(f: Faden) -> None:
    faden.anlegen(f.conn, "ost", "P-1", f.sicht("bh"), art="frist", wert="2026-11-01",
                  ki=True, kanal="api", now=5000)
    assert faden._letzte(f.conn, "ost", True).get("P-1") is None


def test_gleicher_offener_vorschlag_409(f: Faden) -> None:
    e, _ = faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist",
                         wert="2026-11-01", ki=True, kanal="api")
    with pytest.raises(StarletteHTTPException) as fehler:
        faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist", wert="2026-11-01",
                      ki=True, kanal="api")
    assert fehler.value.status_code == 409
    # anderer Wert ist ein eigener Vorschlag; nach der Entscheidung ist derselbe wieder möglich
    faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist", wert="2026-11-02",
                  ki=True, kanal="api")
    faden.vorschlag_verwerfen(f.conn, "ost", "P-1", e["schluessel"], f.sicht("bh"),
                              kanal="web", token_name=None)
    faden.anlegen(f.conn, "ost", "P-1", f.sicht("bear"), art="frist", wert="2026-11-01",
                  ki=True, kanal="api")
    assert f.arten().count("eintrag.vorgeschlagen") == 3

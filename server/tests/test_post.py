"""F8: E-Mail-Benachrichtigung — Abnahme lokal mit Test-SMTP und Negativtests.

Kette: Rückmelder willigt ein und bestätigt die Adresse → Bearbeiter kommentiert → Pumpe prüft
Rechte und rendert in den Spool → scanview-post sendet an den Test-SMTP → der Link aus der Mail
führt den angemeldeten Rückmelder zum Faden → er antwortet im Browser."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import ORIGIN, csrf
from fadenwelt import FADEN, INTERN, OEFFENTLICH, POI, Welt, welt  # noqa: F401
from fastapi.testclient import TestClient
from smtpfake import SmtpFake

from scanview_post import versand
from scanview_server import post
from scanview_server.config import Settings

ABSENDER = "benachrichtigung@example.test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data, state, spool = tmp_path / "data", tmp_path / "state", tmp_path / "spool"
    for d in (data, state, spool):
        d.mkdir()
    return Settings(data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test",
                    rp_name="scanview", cookie_secure=True, post_dir=spool,
                    post_absender=ABSENDER)


@pytest.fixture
def smtp() -> Iterator[SmtpFake]:
    with SmtpFake() as s:
        yield s


def _dienst(w: Welt) -> post.Postdienst:
    s = w.client.app.state.settings  # type: ignore[attr-defined]
    return post.Postdienst(spool=s.post_dir, absender=ABSENDER, origin=ORIGIN,
                           state_dir=s.state_dir)


def _versand(w: Welt, smtp: SmtpFake) -> versand.Versand:
    return versand.Versand(versand.Einstellungen(
        spool=_dienst(w).spool, host="127.0.0.1", port=smtp.port, sicherheit="keine",
        benutzer=None, passwort=None, absender=ABSENDER))


def _pumpen(w: Welt, now: int | None = None) -> dict[str, int]:
    conn = w.conn()
    try:
        return post.pumpen(conn, _dienst(w), w.client.app.state.katalog, now=now)  # type: ignore[attr-defined]
    finally:
        conn.close()


def _einwilligen(w: Welt, wer: str, smtp: SmtpFake) -> None:
    r = w.c[wer].put("/api/v1/konto/benachrichtigung", json={"eingeschaltet": True},
                     headers=csrf())
    assert r.status_code == 200 and r.json()["bestaetigt"] is False, r.text
    vorher = len(smtp.zustand.empfangen)
    _versand(w, smtp).schritt()
    mail = smtp.zustand.empfangen[vorher].nachricht
    token = re.search(r"#mail-bestaetigen=([\w-]+)", mail.get_content()).group(1)  # type: ignore[union-attr]
    r = w.c[wer].post("/api/v1/konto/benachrichtigung/bestaetigen", json={"token": token},
                      headers=csrf())
    assert r.status_code == 200 and r.json()["bestaetigt"] is True
    _pumpen(w)  # Bestätigungsmail als gesendet übernehmen


def _links(text: str) -> dict[str, str]:
    alle = re.findall(rf"{re.escape(ORIGIN)}(/api/mail/[^\s]+)", text)
    faden_ab, alle_ab = (x for x in alle if "/abmelden/" in x)
    return {"zum": next(x for x in alle if "/abmelden/" not in x),
            "faden_ab": faden_ab, "alle_ab": alle_ab}


def test_abnahme_mail_link_antwort(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "rueck", smtp)
    # Rückmelder ist zuständig (Gewerk Dach) → Zuständigkeit, dann Kommentar.
    st = welt.stand()
    r = welt.post("bh", FADEN, {"art": "zustaendig", "wert": {"gewerk_id": welt.gewerk["Dach"],
                                                              "nutzer_id": welt.id["rueck"]},
                                "version": st["version"]})
    assert r.status_code == 201, r.text
    assert _pumpen(welt)["gerendert"] == 1
    _versand(welt, smtp).schritt()
    assert _pumpen(welt)["erledigt"] == 1
    mail = smtp.zustand.empfangen[-1]
    assert mail.empfaenger == ["rueck@example.test"] and mail.absender == ABSENDER
    m = mail.nachricht
    text = m.get_content()
    # Datensparsam: kein Projektname, kein Titel, kein Text, keine Koordinaten.
    for verboten in ("ost", POI["title"], OEFFENTLICH, INTERN, "1.0", "Mangel", "Dach"):
        assert verboten not in text.replace(ORIGIN, "").split("/api/mail/")[0], verboten
    assert "Zuständigkeit" in text
    assert m["Auto-Submitted"] == "auto-generated"
    assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # Kopf im Klartext (kein RFC-2047-Wort), sonst erkennt ihn kein Mailprogramm.
    roh_kopf = mail.roh.split(b"\r\n\r\n", 1)[0].decode("ascii")
    assert re.search(r"^List-Unsubscribe: <https://scanview\.test/api/mail/abmelden/[\w.-]+>\r?$",
                     roh_kopf, re.MULTILINE), roh_kopf
    assert m["Cc"] is None and m["Bcc"] is None
    links = _links(text)
    # Die Links verraten weder Projekt noch Befund noch Konto (nur zufällige Zeilen-ID).
    for link in links.values():
        roh = post._unb64(link.rsplit("/", 1)[1].split(".")[0])
        assert b"ost" not in roh and b"P-1" not in roh and str(welt.id["rueck"]).encode() + b"|" \
            not in roh
    # Link aus der Mail: angemeldet als Empfänger → Faden; fremdes Konto → 404; ohne → Anmeldung.
    r = welt.c["rueck"].get(links["zum"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/befunde?p=ost&b=P-1"
    assert welt.c["bear"].get(links["zum"], follow_redirects=False).status_code == 404
    anonym = TestClient(welt.client.app, base_url=ORIGIN)
    r = anonym.get(links["zum"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/anmelden?weiter=")
    # Antwort im Browser in den Faden.
    r = welt.post("rueck", FADEN, {"art": "kommentar", "text": "Wird Montag erledigt."})
    assert r.status_code == 201


def _kommentar_von(w: Welt, wer: str, text: str = "Neuer Kommentar") -> None:
    assert w.post(wer, FADEN, {"art": "kommentar", "text": text}).status_code == 201


def test_ohne_einwilligung_keine_zeile(welt: Welt) -> None:  # noqa: F811
    _kommentar_von(welt, "bh")
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_ausgang")[0][0] == 0


def test_unbestaetigt_keine_mail(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    r = welt.c["bear"].put("/api/v1/konto/benachrichtigung", json={"eingeschaltet": True},
                           headers=csrf())
    assert r.status_code == 200
    _kommentar_von(welt, "bh")
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_ausgang WHERE ereignis <>"
                    " 'bestaetigung'")[0][0] == 0


def test_bestaetigung_nur_im_eigenen_konto_und_einmal(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    welt.c["bear"].put("/api/v1/konto/benachrichtigung", json={"eingeschaltet": True},
                       headers=csrf())
    _versand(welt, smtp).schritt()
    token = re.search(r"#mail-bestaetigen=([\w-]+)",  # type: ignore[union-attr]
                      smtp.zustand.empfangen[-1].nachricht.get_content()).group(1)
    pfad = "/api/v1/konto/benachrichtigung/bestaetigen"
    assert welt.c["bh"].post(pfad, json={"token": token}, headers=csrf()).status_code == 422
    assert welt.c["bear"].post(pfad, json={"token": token}, headers=csrf()).status_code == 200
    assert welt.c["bear"].post(pfad, json={"token": token}, headers=csrf()).status_code == 422


def test_geaenderte_adresse_ist_unbestaetigt(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    welt.sql("UPDATE users SET email='neu@example.test' WHERE id=?", welt.id["bear"])
    conn = welt.conn()
    try:
        assert post.zustand(conn, welt.id["bear"])["bestaetigt"] is False
    finally:
        conn.close()


def test_intern_nur_an_bauherrenseite(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    for wer in ("bear", "bh"):
        _einwilligen(welt, wer, smtp)
    # Beide sind beteiligt (haben im Faden geschrieben); leitung schreibt intern.
    r = welt.post("leitung", FADEN, {"art": "kommentar", "text": "nur intern", "intern": True})
    assert r.status_code == 201
    z = _pumpen(welt)
    zeilen = {r["user_id"]: (r["status"], r["grund"]) for r in welt.sql(
        "SELECT user_id, status, grund FROM benachrichtigung_ausgang WHERE ereignis='kommentar'")}
    assert zeilen[welt.id["bh"]] == ("gerendert", None)
    assert zeilen[welt.id["bear"]] == ("verworfen", "rechte")
    assert z["gerendert"] == 1


def test_rechteentzug_vor_dem_rendern(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    _kommentar_von(welt, "bh")
    welt.sql("DELETE FROM project_roles WHERE user_id=?", welt.id["bear"])
    _pumpen(welt)
    [r] = welt.sql("SELECT status, grund FROM benachrichtigung_ausgang WHERE ereignis="
                   "'kommentar'")
    assert (r["status"], r["grund"]) == ("verworfen", "rechte")


def test_abmelden_je_faden_und_global(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    _versand(welt, smtp).schritt()
    links = _links(smtp.zustand.empfangen[-1].nachricht.get_content())
    anonym = TestClient(welt.client.app, base_url=ORIGIN)
    # GET leitet nur zur Bestätigungsseite (Mail-Scanner), ändert nichts.
    r = anonym.get(links["faden_ab"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/abmelden#t=")
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_aus")[0][0] == 0
    # One-Click (RFC 8058): POST ohne Anmeldung und ohne Origin.
    r = anonym.post(links["faden_ab"], content=b"List-Unsubscribe=One-Click")
    assert r.status_code == 200 and r.json() == {"abgemeldet": "faden"}
    _kommentar_von(welt, "bh", "noch einer")
    _pumpen(welt, now=10**10)
    assert welt.sql("SELECT grund FROM benachrichtigung_ausgang ORDER BY erstellt_am DESC,"
                    " rowid DESC LIMIT 1")[0][0] == "faden_aus"
    r = anonym.post(links["alle_ab"])
    assert r.status_code == 200 and r.json() == {"abgemeldet": "alle"}
    conn = welt.conn()
    try:
        assert post.zustand(conn, welt.id["bear"])["eingeschaltet"] is False
    finally:
        conn.close()


def test_faden_abbestellen_per_api(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    pfad = FADEN + "/benachrichtigung"
    welt.sql("DELETE FROM lesezugriffe")
    assert welt.c["bear"].get(pfad).json()["aus"] is False
    assert welt.sql("SELECT COUNT(*) FROM lesezugriffe WHERE objekt='faden' AND nutzer_id=?",
                    welt.id["bear"])[0][0] == 1
    r = welt.c["bear"].put(pfad, json={"aus": True}, headers=csrf())
    assert r.status_code == 200 and r.json()["aus"] is True
    assert welt.c["bear"].get(pfad).json()["aus"] is True
    assert welt.c["fremd"].get(pfad).status_code == 404
    lesen = welt.token("bear", "lesen")
    assert lesen.put(pfad, json={"aus": False}).status_code == 403


@pytest.mark.parametrize("kaputt", ["", "x", "eA.eA", "bnVsbA.AAAA"])
def test_falscher_oder_manipulierter_link(welt: Welt, kaputt: str) -> None:  # noqa: F811
    for pfad in (f"/api/mail/{kaputt}", f"/api/mail/abmelden/{kaputt}"):
        r = welt.c["bear"].post(pfad) if "abmelden" in pfad else welt.c["bear"].get(pfad)
        assert r.status_code in (404, 405), pfad


def test_abgelaufener_und_umgeschriebener_link(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    [mid] = [r[0] for r in welt.sql("SELECT id FROM benachrichtigung_ausgang WHERE"
                                    " ereignis='kommentar'")]
    s = _dienst(welt)
    alt = post.link_token(s.state_dir, "l", mid, tage=1, now=0)
    assert welt.c["bear"].get(f"/api/mail/{alt}", follow_redirects=False).status_code == 404
    gut = post.link_token(s.state_dir, "l", mid, tage=1)
    assert welt.c["bear"].get(f"/api/mail/{gut}", follow_redirects=False).status_code == 303
    teil, sig = gut.split(".")
    fremd = post._b64(post._unb64(teil).replace(b"l|", b"g|"))
    assert welt.c["bear"].get(f"/api/mail/{fremd}.{sig}",
                              follow_redirects=False).status_code == 404
    # Ein Link zum Faden ist kein Abmeldelink und umgekehrt.
    assert welt.c["bear"].post(f"/api/mail/abmelden/{gut}").status_code == 404


def test_drosselung_je_faden(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    for i in range(3):
        _kommentar_von(welt, "bh", f"Kommentar {i}")
    z = _pumpen(welt)
    assert z["gerendert"] == 1 and z["verworfen"] == 2
    gruende = {r[0] for r in welt.sql("SELECT grund FROM benachrichtigung_ausgang WHERE"
                                      " status='verworfen'")}
    assert gruende == {"drossel"}


def test_crlf_in_adresse_wird_nicht_versendet(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    welt.sql("UPDATE users SET email=? WHERE id=?", "bear@example.test\r\nBcc: x@boese.test",
             welt.id["bear"])
    welt.sql("UPDATE benachrichtigung_konten SET bestaetigt_hash=? WHERE user_id=?",
             post.adress_hash("bear@example.test\r\nBcc: x@boese.test"), welt.id["bear"])
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    [r] = welt.sql("SELECT status, grund FROM benachrichtigung_ausgang WHERE ereignis="
                   "'kommentar'")
    assert (r["status"], r["grund"]) == ("verworfen", "adresse")
    assert not list((_dienst(welt).spool / "ausgang").glob("*.json"))


def test_titel_mit_kopfzeilen_landet_nicht_in_der_mail(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    welt.sql("UPDATE pois SET daten=json_set(daten, '$.title', ?) WHERE id='P-1'",
             "X\r\nBcc: x@boese.test")
    _kommentar_von(welt, "bh", "Text\r\nBcc: y@boese.test")
    _pumpen(welt)
    _versand(welt, smtp).schritt()
    mail = smtp.zustand.empfangen[-1]
    assert mail.empfaenger == ["bear@example.test"] and b"boese" not in mail.roh


def test_smtp_ausfall_kommentar_gespeichert_spaeter_genau_eine_mail(
        welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    smtp.zustand.stoerung = "421"
    _kommentar_von(welt, "bh", "trotz Ausfall gespeichert")
    assert any(e["text"] == "trotz Ausfall gespeichert"
               for e in welt.c["bh"].get(FADEN).json()["eintraege"])
    _pumpen(welt)
    v = _versand(welt, smtp)
    vorher = len(smtp.zustand.empfangen)
    v.schritt(jetzt=1000.0)
    assert len(smtp.zustand.empfangen) == vorher
    smtp.zustand.stoerung = None
    v.schritt(jetzt=1001.0)  # Backoff: noch nicht
    assert len(smtp.zustand.empfangen) == vorher
    v.schritt(jetzt=1000.0 + versand.BACKOFF_S[0] + 1)
    v.schritt(jetzt=1000.0 + versand.BACKOFF_S[0] + 2)
    assert len(smtp.zustand.empfangen) == vorher + 1
    assert _pumpen(welt)["erledigt"] == 1
    assert welt.sql("SELECT status FROM benachrichtigung_ausgang WHERE ereignis='kommentar'"
                    )[0][0] == "gesendet"


def test_abbruch_nach_daten_ist_unklar_ohne_zweiten_versuch(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    smtp.zustand.stoerung = "nach_data"
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    v = _versand(welt, smtp)
    v.schritt()
    verbindungen = smtp.zustand.verbindungen
    smtp.zustand.stoerung = None
    v.schritt(jetzt=10**10)
    assert smtp.zustand.verbindungen == verbindungen
    _pumpen(welt)
    assert welt.sql("SELECT status FROM benachrichtigung_ausgang WHERE ereignis='kommentar'"
                    )[0][0] == "unklar"


def test_dauerhaft_abgelehnt(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    smtp.zustand.stoerung = "550"
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    _versand(welt, smtp).schritt()
    _pumpen(welt)
    assert welt.sql("SELECT status FROM benachrichtigung_ausgang WHERE ereignis='kommentar'"
                    )[0][0] == "fehlgeschlagen"


def test_projekt_schalter_aus(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    r = welt.c["leitung"].patch("/api/v1/projekte/ost/einstellungen",
                                json={"benachrichtigung": False}, headers=csrf())
    assert r.status_code == 200 and r.json()["einstellungen"]["benachrichtigung"] is False
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    assert welt.sql("SELECT grund FROM benachrichtigung_ausgang WHERE ereignis='kommentar'"
                    )[0][0] == "projekt_aus"


def test_spool_enthaelt_nur_die_nachricht(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    import os

    alt = os.umask(0o077)  # wie die Unit des Webdienstes
    try:
        _einwilligen(welt, "bear", smtp)
    finally:
        os.umask(alt)
    _kommentar_von(welt, "bh")
    alt = os.umask(0o077)
    try:
        _pumpen(welt)
    finally:
        os.umask(alt)
    [datei] = (_dienst(welt).spool / "ausgang").glob("*.json")
    d: dict[str, Any] = json.loads(datei.read_text())
    assert set(d) == {"id", "an", "nachricht"} and d["an"] == "bear@example.test"
    assert oct(datei.stat().st_mode & 0o777) == "0o640"


def test_alte_zeilen_werden_geloescht(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    _einwilligen(welt, "bear", smtp)
    _kommentar_von(welt, "bh")
    _pumpen(welt)
    _versand(welt, smtp).schritt()
    _pumpen(welt)
    _pumpen(welt, now=10**10)
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_ausgang")[0][0] == 0


def test_ohne_postdienst_404(client: TestClient) -> None:
    from scanview_server.config import Settings as S

    s = client.app.state.settings  # type: ignore[attr-defined]
    from scanview_server.web import create_app

    app = create_app(S(data_dir=s.data_dir, state_dir=s.state_dir, origin=ORIGIN,
                       rp_id="scanview.test", rp_name="scanview"))
    with TestClient(app, base_url=ORIGIN) as c:
        assert c.get("/api/mail/abmelden/x", follow_redirects=False).status_code == 404
        assert c.post("/api/mail/abmelden/x").status_code == 404


def test_keine_klartextkopie_und_anonymisieren_loescht(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    from scanview_server import verwaltung

    _einwilligen(welt, "bear", smtp)
    _kommentar_von(welt, "bh")
    faden = FADEN + "/benachrichtigung"
    assert welt.c["bear"].put(faden, json={"aus": True}, headers=csrf()).status_code == 200
    roh = repr(welt.sql("SELECT * FROM benachrichtigung_konten")[0][:])
    assert "bear@example.test" not in roh
    conn = welt.conn()
    try:
        out = verwaltung.anonymisieren(conn, welt.id["bear"], von=welt.id["admin"], kanal="cli")
    finally:
        conn.close()
    assert out["benachrichtigungKonto"] == 1 and out["benachrichtigungAus"] == 1
    for tabelle in ("benachrichtigung_konten", "benachrichtigung_aus", "benachrichtigung_ausgang"):
        assert welt.sql(f"SELECT COUNT(*) FROM {tabelle} WHERE user_id=?",
                        welt.id["bear"])[0][0] == 0


def test_bestaetigungsmail_gedrosselt(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    for an in (True, False, True, False, True):
        assert welt.c["bear"].put("/api/v1/konto/benachrichtigung", json={"eingeschaltet": an},
                                  headers=csrf()).status_code == 200
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_ausgang WHERE ereignis="
                    "'bestaetigung'")[0][0] == 1
    assert len(list((_dienst(welt).spool / "ausgang").glob("*.json"))) == 1

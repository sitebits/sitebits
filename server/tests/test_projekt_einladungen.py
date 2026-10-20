"""Projektgebundene Einladungen: neue E-Mail → Link für genau dieses Projekt; bestehendes Konto →
Zugriff direkt, ohne Zurücksetzen; Einlösen stuft nie herab."""

from __future__ import annotations

import json

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from projektwelt import (  # noqa: F401
    KIRCHE,
    OST,
    Konto,
    ereignisse,
    projektwelt,
    rolle_in,
    verbindung,
)
from test_api_bearer import anon, auth, token
from test_authz import mit_paketen  # noqa: F401  (Fixture von projektwelt)

from scanview_server import konten, projektverwaltung, routen_projekt, verwaltung

Welt = dict[str, Konto]


def _einladen(
    k: Konto,
    basis: str,
    email: str,
    rolle: str = "bearbeiter",
    gewerk: int | None = None,
    name: str = "Neue Person",
) -> dict[str, object]:
    r = k.schreibe(
        "POST",
        f"{basis}/einladungen",
        {"name": name, "email": email, "rolle": rolle, "gewerk": gewerk},
    )
    assert r.status_code in (200, 201), r.text
    assert r.headers["Cache-Control"] == "no-store"
    daten: dict[str, object] = r.json()
    return daten


def _einloesen(c: TestClient, link: str) -> konten.Nutzer:
    conn = verbindung(c)
    try:
        return konten.einladung_einloesen(conn, link.split("#t=", 1)[1], lambda _c, _u: None)
    finally:
        conn.close()


def test_neue_email_einladung_projektgebunden(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    gid = lei.schreibe("POST", f"{OST}/gewerke", {"name": "Elektro", "kurz": "EL"}).json()["id"]
    a = _einladen(lei, OST, "neu@x.de", "rueckmelder", gid)
    assert a["ergebnis"] == "eingeladen" and str(a["link"]).startswith(
        "https://scanview.test/einladung#t="
    )
    conn = verbindung(lei.c)
    try:
        row = conn.execute(
            "SELECT project_id, gewerk_id, grants, purpose FROM invites WHERE email='neu@x.de'"
        ).fetchone()
    finally:
        conn.close()
    assert (row["project_id"], row["gewerk_id"], json.loads(row["grants"]), row["purpose"]) == (
        "ost",
        gid,
        {"ost": "rueckmelder"},
        "neu",
    )
    angelegt = ereignisse(lei.c, "einladung.angelegt")[-1]
    assert angelegt["project_id"] == "ost" and "neu@x.de" not in (angelegt["nachher"] or "")
    n = _einloesen(lei.c, str(a["link"]))
    assert rolle_in(lei.c, n.id, "ost") == "rueckmelder" and rolle_in(lei.c, n.id, "kirche") is None
    eintrag = next(
        b for b in lei.get(f"{OST}/beteiligte").json()["beteiligte"] if b["nutzerId"] == n.id
    )
    assert eintrag["gewerk"]["id"] == gid
    vergeben = ereignisse(lei.c, "recht.vergeben")[-1]
    assert (vergeben["project_id"], vergeben["objekt_id"], vergeben["nutzer_id"]) == (
        "ost",
        str(n.id),
        lei.nutzer.id,
    )


def _zugangsdaten(c: TestClient, uid: int) -> tuple[int, int, int, int]:
    conn = verbindung(c)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (uid,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM passwords WHERE user_id=?", (uid,)).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND widerrufen_am IS NULL", (uid,)
            ).fetchone()[0],
        )
    finally:
        conn.close()


def test_einladung_bestehendes_konto_setzt_nichts_zurueck(projektwelt: Welt) -> None:  # noqa: F811
    lei, ziel = projektwelt["leitung"], projektwelt["leitung_kirche"]
    roh = token(ziel.c, ziel.nutzer, "laptop", "lesen")
    conn = verbindung(lei.c)
    try:
        conn.execute(
            "INSERT INTO passwords (user_id, hash, updated_at) VALUES (?, 'h', 0)",
            (ziel.nutzer.id,),
        )
        conn.execute(
            "INSERT INTO passkeys (id, user_id, public_key, sign_count, created_at)"
            " VALUES ('pk1', ?, x'00', 0, 0)",
            (ziel.nutzer.id,),
        )
        einladungen_vorher = conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0]
    finally:
        conn.close()
    vorher = _zugangsdaten(lei.c, ziel.nutzer.id)
    a = _einladen(lei, OST, "LK@x.de", "betrachter")  # andere Schreibweise, gleiches Konto
    assert (a["ergebnis"], a["gesperrt"]) == ("zugriff-erteilt", False)
    assert rolle_in(lei.c, ziel.nutzer.id, "ost") == "betrachter"
    assert _zugangsdaten(lei.c, ziel.nutzer.id) == vorher
    conn = verbindung(lei.c)
    try:
        assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == einladungen_vorher
    finally:
        conn.close()
    assert ziel.get("/api/sitzung").status_code == 200
    assert anon(ziel.c).get("/api/v1/projekte", headers=auth(roh)).status_code == 200


def test_einladung_bestehendes_konto_stuft_nicht_herab(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    a = _einladen(lei, OST, "b@x.de", "betrachter")
    assert a["ergebnis"] == "zugriff-besteht"
    assert rolle_in(lei.c, ben.nutzer.id, "ost") == "bearbeiter"
    a = _einladen(lei, OST, "l2@x.de", "betrachter")  # andere Projektleitung bleibt unberührt
    assert a["ergebnis"] == "zugriff-besteht"
    assert rolle_in(lei.c, projektwelt["leitung2"].nutzer.id, "ost") == "projektleitung"


def test_einladung_admin_konto_unveraendert(projektwelt: Welt) -> None:  # noqa: F811
    lei, adm = projektwelt["leitung"], projektwelt["admin"]
    a = _einladen(lei, OST, "adm@x.de", "betrachter")
    assert a["ergebnis"] == "zugriff-besteht"
    assert rolle_in(lei.c, adm.nutzer.id, "ost") is None


def test_einladung_gesperrtes_konto_bleibt_gesperrt(projektwelt: Welt) -> None:  # noqa: F811
    lei, ziel = projektwelt["leitung"], projektwelt["leitung_kirche"]
    conn = verbindung(lei.c)
    try:
        verwaltung.sperren(conn, ziel.nutzer.id, von=None, kanal="cli")
    finally:
        conn.close()
    a = _einladen(lei, OST, "lk@x.de", "betrachter")
    assert (a["ergebnis"], a["gesperrt"]) == ("zugriff-erteilt", True)
    assert "gesperrt" in str(a["text"])
    conn = verbindung(lei.c)
    try:
        assert (
            conn.execute("SELECT disabled FROM users WHERE id=?", (ziel.nutzer.id,)).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


def test_einladung_anonymisiertes_konto_422(projektwelt: Welt) -> None:  # noqa: F811
    lei, ziel = projektwelt["leitung"], projektwelt["leitung_kirche"]
    conn = verbindung(lei.c)
    try:
        verwaltung.anonymisieren(conn, ziel.nutzer.id, von=None, kanal="cli")
    finally:
        conn.close()
    r = lei.schreibe(
        "POST",
        f"{OST}/einladungen",
        {"name": "X", "email": f"geloescht-{ziel.nutzer.id}@invalid", "rolle": "betrachter"},
    )
    assert r.status_code == 422


def test_leitung_laedt_nicht_als_projektleitung_ein(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    r = lei.schreibe(
        "POST", f"{OST}/einladungen", {"name": "X", "email": "x@x.de", "rolle": "projektleitung"}
    )
    assert (r.status_code, r.json()["fehler"]) == (403, verwaltung.LEITUNG_ROLLE_VERBOTEN)
    r = lei.schreibe(
        "POST",
        f"{OST}/einladungen",
        {"name": "X", "email": "x@x.de", "rolle": "betrachter", "admin": True},
    )
    assert r.status_code == 422
    adm = projektwelt["admin"]
    assert _einladen(adm, OST, "pl@x.de", "projektleitung")["ergebnis"] == "eingeladen"


def test_einladung_mit_fremdem_gewerk_422(projektwelt: Welt) -> None:  # noqa: F811
    fremd = (
        projektwelt["leitung_kirche"]
        .schreibe("POST", f"{KIRCHE}/gewerke", {"name": "Dach", "kurz": "DA"})
        .json()["id"]
    )
    r = projektwelt["leitung"].schreibe(
        "POST",
        f"{OST}/einladungen",
        {"name": "X", "email": "x@x.de", "rolle": "betrachter", "gewerk": fremd},
    )
    assert (r.status_code, r.json()["fehler"]) == (422, verwaltung.GEWERK_UNBEKANNT)


def _gewerk_nach_vorpruefung_loeschen(
    c: TestClient, monkeypatch: pytest.MonkeyPatch, gid: int
) -> None:
    """Simuliert den Wettlauf: Die Vorprüfung der Route sieht das Gewerk noch, danach ist es weg."""
    echt = projektverwaltung.gewerk

    def vorpruefung(conn: object, pid: str, g: int) -> object:
        erg = echt(conn, pid, g)  # type: ignore[arg-type]
        db = verbindung(c)
        try:
            db.execute("DELETE FROM gewerke WHERE id=?", (gid,))
        finally:
            db.close()
        return erg

    monkeypatch.setattr(routen_projekt.projektverwaltung, "gewerk", vorpruefung)


@pytest.mark.parametrize("bestehendes_konto", [False, True])
def test_gewerk_zwischen_vorpruefung_und_schreiben_geloescht_422(
    projektwelt: Welt,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    bestehendes_konto: bool,
) -> None:
    lei = projektwelt["leitung"]
    gid = lei.schreibe("POST", f"{OST}/gewerke", {"name": "Dach", "kurz": "DA"}).json()["id"]
    email = projektwelt["betrachter"].nutzer.email if bestehendes_konto else "neu@x.de"
    sql = "SELECT COUNT(*) FROM invites WHERE email=?"
    conn = verbindung(lei.c)
    try:
        # Bestehende Konten sind selbst per (eingelöster) Einladung entstanden: Grundstand merken.
        vorher = conn.execute(sql, (email,)).fetchone()[0]
    finally:
        conn.close()
    _gewerk_nach_vorpruefung_loeschen(lei.c, monkeypatch, gid)
    r = lei.schreibe(
        "POST",
        f"{OST}/einladungen",
        {"name": "X", "email": email, "rolle": "betrachter", "gewerk": gid},
    )
    assert (r.status_code, r.json()["fehler"]) == (422, verwaltung.GEWERK_UNBEKANNT)
    conn = verbindung(lei.c)
    try:
        assert conn.execute(sql, (email,)).fetchone()[0] == vorher
        zugeordnet = "SELECT COUNT(*) FROM project_roles WHERE gewerk_id=?"
        assert conn.execute(zugeordnet, (gid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_offene_einladungen_je_projekt_begrenzt(
    projektwelt: Welt,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verwaltung, "OFFENE_EINLADUNGEN_MAX", 2)
    lei = projektwelt["leitung"]
    for i in range(2):
        assert _einladen(lei, OST, f"neu{i}@x.de")["ergebnis"] == "eingeladen"
    r = lei.schreibe(
        "POST", f"{OST}/einladungen", {"name": "X", "email": "neu9@x.de", "rolle": "betrachter"}
    )
    assert (r.status_code, r.json()["fehler"]) == (422, verwaltung.EINLADUNGEN_ZU_VIELE)
    assert "offene Einladungen" in r.json()["fehler"]
    # Andere Projekte zählen nicht mit; Widerruf macht wieder Platz.
    assert _einladen(projektwelt["leitung_kirche"], KIRCHE, "k@x.de")["ergebnis"] == "eingeladen"
    eid = lei.get(f"{OST}/einladungen").json()["einladungen"][0]["id"]
    assert lei.schreibe("POST", f"{OST}/einladungen/{eid}/widerrufen").status_code == 204
    assert _einladen(lei, OST, "neu9@x.de")["ergebnis"] == "eingeladen"


def test_leitung_sieht_nur_einladungen_des_projekts(projektwelt: Welt) -> None:  # noqa: F811
    lei, adm, lk = projektwelt["leitung"], projektwelt["admin"], projektwelt["leitung_kirche"]
    _einladen(lei, OST, "o@x.de")
    _einladen(lk, KIRCHE, "k@x.de")
    r = adm.schreibe(
        "POST",
        "/api/v1/admin/einladungen",
        {"name": "I", "email": "i@x.de", "rechte": {"ost": "betrachter"}},
    )
    assert r.status_code == 201
    liste = lei.get(f"{OST}/einladungen").json()["einladungen"]
    assert [e["email"] for e in liste] == ["o@x.de"]
    assert liste[0]["rolle"] == "bearbeiter"
    assert lei.get(f"{KIRCHE}/einladungen").status_code == 404
    assert projektwelt["bearbeiter"].get(f"{OST}/einladungen").status_code == 403


def test_widerrufen_fremder_einladung_404(projektwelt: Welt) -> None:  # noqa: F811
    lei, lk, adm = projektwelt["leitung"], projektwelt["leitung_kirche"], projektwelt["admin"]
    fremd = _einladen(lk, KIRCHE, "k@x.de")["einladung"]["id"]  # type: ignore[index]
    instanz = adm.schreibe(
        "POST", "/api/v1/admin/einladungen", {"name": "I", "email": "i@x.de"}
    ).json()["einladung"]["id"]
    for eid in (fremd, instanz):
        assert lei.schreibe("POST", f"{OST}/einladungen/{eid}/widerrufen").status_code == 404
    eigen = _einladen(lei, OST, "o@x.de")["einladung"]["id"]  # type: ignore[index]
    assert lei.schreibe("POST", f"{OST}/einladungen/{eigen}/widerrufen").status_code == 204
    assert lei.get(f"{OST}/einladungen").json()["einladungen"] == []
    widerruf = ereignisse(lei.c, "einladung.widerrufen")[-1]
    assert (widerruf["project_id"], widerruf["objekt_id"]) == ("ost", eigen)


def test_einloesen_stuft_nie_herab(projektwelt: Welt) -> None:  # noqa: F811
    """Rücksetz-Einladung des Admins mit Rechten: ein Bearbeiter bleibt Bearbeiter."""
    adm, ben = projektwelt["admin"], projektwelt["bearbeiter"]
    conn = verbindung(adm.c)
    try:
        t = verwaltung.einladen(
            conn,
            email="b@x.de",
            name="Ben",
            grants={"ost": "betrachter"},
            zuruecksetzen=True,
            von=adm.nutzer.id,
            kanal="cli",
        )
        konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    finally:
        conn.close()
    assert rolle_in(adm.c, ben.nutzer.id, "ost") == "bearbeiter"


def test_projektgebundene_einladung_vergibt_nur_ihr_projekt(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    link = str(_einladen(lei, OST, "neu@x.de", "betrachter")["link"])
    conn = verbindung(lei.c)
    try:  # manipulierte Rechte in der Datenbank: ein zweites Projekt darf nicht mitkommen
        conn.execute(
            "UPDATE invites SET grants=? WHERE email='neu@x.de'",
            (json.dumps({"ost": "betrachter", "kirche": "bearbeiter"}),),
        )
    finally:
        conn.close()
    n = _einloesen(lei.c, link)
    assert rolle_in(lei.c, n.id, "ost") == "betrachter" and rolle_in(lei.c, n.id, "kirche") is None


def test_zweite_offene_einladung_bleibt_offen(projektwelt: Welt) -> None:  # noqa: F811
    """Einlösen übernimmt keine fremden offenen Einladungen derselben E-Mail; die zweite Leitung
    erteilt danach per erneuter Einladung direkt Zugriff."""
    lei, lk = projektwelt["leitung"], projektwelt["leitung_kirche"]
    erste = str(_einladen(lei, OST, "neu@x.de", "betrachter")["link"])
    zweite = str(_einladen(lk, KIRCHE, "neu@x.de", "rueckmelder")["link"])
    n = _einloesen(lei.c, erste)
    assert rolle_in(lei.c, n.id, "ost") == "betrachter"
    assert rolle_in(lei.c, n.id, "kirche") is None
    assert [e["email"] for e in lk.get(f"{KIRCHE}/einladungen").json()["einladungen"]] == [
        "neu@x.de"
    ]
    with pytest.raises(konten.EinladungUngueltig):
        _einloesen(lk.c, zweite)
    assert rolle_in(lei.c, n.id, "kirche") is None
    assert _einladen(lk, KIRCHE, "neu@x.de", "rueckmelder")["ergebnis"] == "zugriff-erteilt"
    assert rolle_in(lei.c, n.id, "kirche") == "rueckmelder"


def test_einladen_per_token_403(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    roh = token(lei.c, lei.nutzer, "schreiben", "schreiben")
    r = anon(lei.c).post(
        f"{OST}/einladungen",
        headers=auth(roh),
        json={"name": "X", "email": "x@x.de", "rolle": "betrachter"},
    )
    assert r.status_code == 403
    assert lei.get(f"{OST}/einladungen").json()["einladungen"] == []


def test_einladung_ungueltige_email_422(projektwelt: Welt) -> None:  # noqa: F811
    r = projektwelt["leitung"].schreibe(
        "POST", f"{OST}/einladungen", {"name": "X", "email": "ohne-at", "rolle": "betrachter"}
    )
    assert r.status_code == 422


def test_csrf_fehlt_403(projektwelt: Welt) -> None:  # noqa: F811
    r = projektwelt["leitung"].c.post(
        f"{OST}/einladungen", json={"name": "X", "email": "x@x.de", "rolle": "betrachter"}
    )
    assert (r.status_code, r.json()["fehler"]) == (403, "CSRF")
    r = projektwelt["leitung"].c.post(
        f"{OST}/einladungen",
        headers={**csrf(), "Origin": "https://boese.test"},
        json={"name": "X", "email": "x@x.de", "rolle": "betrachter"},
    )
    assert r.status_code == 403

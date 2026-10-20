"""Projektleitung: Beteiligte lesen und verwalten, Gewerke pflegen — nie mehr Rechte als das eigene
Projekt erlaubt, nur per Browser-Sitzung."""

from __future__ import annotations

import pytest
from projektwelt import (  # noqa: F401
    KIRCHE,
    OST,
    Konto,
    ereignisse,
    projektwelt,
    rolle_in,
    verbindung,
)
from test_api_bearer import anon, auth, token, zaehle_ereignisse
from test_authz import mit_paketen  # noqa: F401  (Fixture von projektwelt)

from scanview_server import verwaltung, zugriff

Welt = dict[str, Konto]


def _gewerk(
    k: Konto, basis: str, name: str = "Elektro", kurz: str = "EL", bauherrenseite: bool = False
) -> int:
    r = k.schreibe(
        "POST", f"{basis}/gewerke", {"name": name, "kurz": kurz, "bauherrenseite": bauherrenseite}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


# --- Beteiligte lesen ------------------------------------------------------------------------


def test_beteiligte_volle_liste_fuer_leitung(projektwelt: Welt) -> None:  # noqa: F811
    r = projektwelt["leitung"].get(f"{OST}/beteiligte")
    assert r.status_code == 200
    eintraege = {b["nutzer"]: b for b in r.json()["beteiligte"]}
    ben = eintraege["Ben Bearbeiter"]
    assert ben["rolle"] == "bearbeiter" and ben["email"] == "b@x.de" and ben["gewerk"] is None
    assert {"nutzerId", "letzteAnmeldung", "gesperrt", "admin"} <= set(ben)
    assert "Anna Admin" not in eintraege  # Admin ohne Projektrolle
    assert eintraege["Ada Adminrolle"]["admin"] is True
    assert "Kai Kirche" not in eintraege  # Leitung eines anderen Projekts


def test_beteiligte_ohne_email_fuer_andere_rollen_und_token(projektwelt: Welt) -> None:  # noqa: F811
    for wer in ("betrachter", "rueckmelder", "bearbeiter"):
        b = projektwelt[wer].get(f"{OST}/beteiligte").json()["beteiligte"]
        assert all(set(e) == {"nutzerId", "nutzer", "rolle", "gewerk"} for e in b), wer
    lei = projektwelt["leitung"]
    roh = token(lei.c, lei.nutzer, "lesen", "lesen")
    r = anon(lei.c).get(f"{OST}/beteiligte", headers=auth(roh))
    assert r.status_code == 200
    assert all("email" not in e for e in r.json()["beteiligte"])


def test_leitung_fremdes_projekt_beteiligte_404(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    assert lei.get(f"{KIRCHE}/beteiligte").status_code == 404
    r = lei.schreibe(
        "PUT", f"{KIRCHE}/beteiligte/{ben.nutzer.id}", {"rolle": "bearbeiter", "gewerk": None}
    )
    assert r.status_code == 404
    assert rolle_in(lei.c, ben.nutzer.id, "kirche") == "betrachter"


# --- Rolle und Gewerk setzen -----------------------------------------------------------------


def test_leitung_setzt_rolle_und_gewerk(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    gid = _gewerk(lei, OST)
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "rueckmelder", "gewerk": gid}
    )
    assert (r.status_code, r.json()) == (
        200,
        {"vorher": "bearbeiter", "rolle": "rueckmelder", "gewerk": gid},
    )
    assert rolle_in(lei.c, ben.nutzer.id, "ost") == "rueckmelder"
    recht = ereignisse(lei.c, "recht.")[-1]
    assert (recht["art"], recht["project_id"], recht["objekt_id"], recht["nutzer_id"]) == (
        "recht.vergeben",
        "ost",
        str(ben.nutzer.id),
        lei.nutzer.id,
    )
    zuordnung = ereignisse(lei.c, "beteiligter.")[-1]
    assert (zuordnung["art"], zuordnung["project_id"], zuordnung["nachher"]) == (
        "beteiligter.gewerk",
        "ost",
        str(gid),
    )
    eintrag = next(
        b
        for b in lei.get(f"{OST}/beteiligte").json()["beteiligte"]
        if b["nutzerId"] == ben.nutzer.id
    )
    assert eintrag["gewerk"] == {"id": gid, "name": "Elektro", "kurz": "EL"}


def test_leitung_entzieht_rolle(projektwelt: Welt) -> None:  # noqa: F811
    lei, bet = projektwelt["leitung"], projektwelt["betrachter"]
    r = lei.schreibe("PUT", f"{OST}/beteiligte/{bet.nutzer.id}", {"rolle": None, "gewerk": None})
    assert r.status_code == 200 and r.json()["vorher"] == "betrachter"
    assert rolle_in(lei.c, bet.nutzer.id, "ost") is None
    assert bet.get(OST).status_code == 404
    assert ereignisse(lei.c, "recht.entzogen")[-1]["project_id"] == "ost"


def test_leitung_vergibt_keine_projektleitung(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    vorher = zaehle_ereignisse(lei.c)
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "projektleitung", "gewerk": None}
    )
    assert (r.status_code, r.json()["fehler"]) == (403, verwaltung.LEITUNG_ROLLE_VERBOTEN)
    assert rolle_in(lei.c, ben.nutzer.id, "ost") == "bearbeiter"
    assert zaehle_ereignisse(lei.c) == vorher


@pytest.mark.parametrize("rolle", ["betrachter", None])
def test_leitung_aendert_keine_andere_leitung(projektwelt: Welt, rolle: str | None) -> None:  # noqa: F811
    lei, l2 = projektwelt["leitung"], projektwelt["leitung2"]
    r = lei.schreibe("PUT", f"{OST}/beteiligte/{l2.nutzer.id}", {"rolle": rolle, "gewerk": None})
    assert (r.status_code, r.json()["fehler"]) == (403, verwaltung.LEITUNG_ZIEL_GESCHUETZT)
    assert rolle_in(lei.c, l2.nutzer.id, "ost") == "projektleitung"


@pytest.mark.parametrize("rolle", ["bearbeiter", None])
def test_leitung_gibt_eigene_rolle_nicht_ab(projektwelt: Welt, rolle: str | None) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    r = lei.schreibe("PUT", f"{OST}/beteiligte/{lei.nutzer.id}", {"rolle": rolle, "gewerk": None})
    assert r.status_code == 403
    assert rolle_in(lei.c, lei.nutzer.id, "ost") == "projektleitung"


def test_leitung_aendert_kein_admin_konto(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    mit_rolle, ohne_rolle = projektwelt["admin_mit_rolle"], projektwelt["admin"]
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{mit_rolle.nutzer.id}", {"rolle": "betrachter", "gewerk": None}
    )
    assert (r.status_code, r.json()["fehler"]) == (403, verwaltung.LEITUNG_ZIEL_GESCHUETZT)
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{ohne_rolle.nutzer.id}", {"rolle": "betrachter", "gewerk": None}
    )
    assert r.status_code == 404  # nicht beteiligt: keine Auskunft, ob die ID ein Admin ist
    assert rolle_in(lei.c, ohne_rolle.nutzer.id, "ost") is None


def test_leitung_kann_nicht_per_put_hinzufuegen(projektwelt: Welt) -> None:  # noqa: F811
    lei, fremd = projektwelt["leitung"], projektwelt["leitung_kirche"]
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{fremd.nutzer.id}", {"rolle": "betrachter", "gewerk": None}
    )
    assert r.status_code == 404
    assert rolle_in(lei.c, fremd.nutzer.id, "ost") is None
    assert (
        lei.schreibe(
            "PUT", f"{OST}/beteiligte/999999", {"rolle": "betrachter", "gewerk": None}
        ).status_code
        == 404
    )


def test_admin_vergibt_projektleitung_ueber_beteiligte(projektwelt: Welt) -> None:  # noqa: F811
    adm, ben = projektwelt["admin"], projektwelt["bearbeiter"]
    r = adm.schreibe(
        "PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "projektleitung", "gewerk": None}
    )
    assert r.status_code == 200
    assert rolle_in(adm.c, ben.nutzer.id, "ost") == "projektleitung"


def test_gewerk_aus_fremdem_projekt_422(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    fremd = _gewerk(projektwelt["leitung_kirche"], KIRCHE, "Kirchendach", "KD")
    vorher = zaehle_ereignisse(lei.c)
    r = lei.schreibe(
        "PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "bearbeiter", "gewerk": fremd}
    )
    assert (r.status_code, r.json()["fehler"]) == (422, verwaltung.GEWERK_UNBEKANNT)
    assert zaehle_ereignisse(lei.c) == vorher


def test_beteiligter_setzen_prueft_den_stand_in_der_transaktion(projektwelt: Welt) -> None:  # noqa: F811
    """Befördert ein Admin das Ziel, nachdem die Leitung die Seite geladen hat, gilt beim Schreiben
    der neue Stand: die Leitung kann die neue Projektleitung nicht mehr herabstufen."""
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    conn = verbindung(lei.c)
    try:
        conn.execute(
            "UPDATE project_roles SET role='projektleitung' WHERE user_id=? AND project_id='ost'",
            (ben.nutzer.id,),
        )
        with pytest.raises(verwaltung.Verboten):
            verwaltung.beteiligter_setzen(
                conn,
                ben.nutzer.id,
                "ost",
                "betrachter",
                None,
                als_leitung=True,
                von=lei.nutzer.id,
                kanal="web",
            )
    finally:
        conn.close()
    assert rolle_in(lei.c, ben.nutzer.id, "ost") == "projektleitung"


def test_bearbeiter_verwaltet_nicht_403(projektwelt: Welt) -> None:  # noqa: F811
    ben, bet = projektwelt["bearbeiter"], projektwelt["betrachter"]
    r = ben.schreibe(
        "PUT", f"{OST}/beteiligte/{bet.nutzer.id}", {"rolle": "bearbeiter", "gewerk": None}
    )
    assert (r.status_code, r.json()["fehler"]) == (403, zugriff.NUR_LEITUNG)
    assert ben.schreibe("POST", f"{OST}/gewerke", {"name": "X", "kurz": "X"}).status_code == 403


def test_verwaltung_per_token_403(projektwelt: Welt) -> None:  # noqa: F811
    lei, bet = projektwelt["leitung"], projektwelt["betrachter"]
    roh = token(lei.c, lei.nutzer, "schreiben", "schreiben")
    c = anon(lei.c)
    vorher = zaehle_ereignisse(lei.c)
    r = c.post(f"{OST}/gewerke", headers=auth(roh), json={"name": "X", "kurz": "X"})
    assert (r.status_code, r.json()["fehler"]) == (403, zugriff.NICHT_PER_TOKEN)
    r = c.put(
        f"{OST}/beteiligte/{bet.nutzer.id}",
        headers=auth(roh),
        json={"rolle": "bearbeiter", "gewerk": None},
    )
    assert r.status_code == 403
    assert zaehle_ereignisse(lei.c) == vorher
    assert rolle_in(lei.c, bet.nutzer.id, "ost") == "betrachter"


def test_leitung_hat_keine_instanzverwaltung(projektwelt: Welt) -> None:  # noqa: F811
    """Zurücksetzen, Sperren, Abmelden und Anonymisieren bleiben Instanz-Sache: Admin-Routen 404."""
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    pfad = f"/api/v1/admin/nutzer/{ben.nutzer.id}"
    for methode, ziel, koerper in (
        ("POST", f"{pfad}/sperren", None),
        ("POST", f"{pfad}/entsperren", None),
        ("POST", f"{pfad}/abmelden-ueberall", None),
        (
            "POST",
            "/api/v1/admin/einladungen",
            {"name": "B", "email": "b@x.de", "zuruecksetzen": True},
        ),
        ("PUT", f"{pfad}/rechte/ost", {"rolle": "betrachter"}),
    ):
        assert lei.schreibe(methode, ziel, koerper).status_code == 404, ziel


def test_kaputter_koerper_verraet_nichts(projektwelt: Welt) -> None:  # noqa: F811
    ben = projektwelt["bearbeiter"]
    kaputt = {"rolle": 5}
    assert (
        projektwelt["leitung_kirche"]
        .schreibe("PUT", f"{OST}/beteiligte/{ben.nutzer.id}", kaputt)
        .status_code
        == 404
    )
    assert (
        projektwelt["betrachter"]
        .schreibe("PUT", f"{OST}/beteiligte/{ben.nutzer.id}", kaputt)
        .status_code
        == 403
    )
    assert (
        projektwelt["leitung"]
        .schreibe("PUT", f"{OST}/beteiligte/{ben.nutzer.id}", kaputt)
        .status_code
        == 422
    )
    unbekannt = {"rolle": "bearbeiter", "gewerk": None, "admin": True}
    assert (
        projektwelt["leitung"]
        .schreibe("PUT", f"{OST}/beteiligte/{ben.nutzer.id}", unbekannt)
        .status_code
        == 422
    )


# --- Gewerke ---------------------------------------------------------------------------------


def test_gewerke_anlegen_aendern_loeschen(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben = projektwelt["leitung"], projektwelt["bearbeiter"]
    gid = _gewerk(lei, OST, "Elektro - Firma X", "EL")
    assert (
        lei.schreibe(
            "POST", f"{OST}/gewerke", {"name": "elektro - firma x", "kurz": "E2"}
        ).status_code
        == 422
    )
    assert (
        lei.schreibe("POST", f"{OST}/gewerke", {"name": "Dach", "kurz": "DACHD"}).status_code == 422
    )
    assert (
        lei.schreibe("POST", f"{OST}/gewerke", {"name": "Dach\u0007", "kurz": "DA"}).status_code
        == 422
    )
    r = lei.schreibe("PATCH", f"{OST}/gewerke/{gid}", {"bauherrenseite": True})
    assert r.status_code == 200 and r.json()["bauherrenseite"] is True
    assert [e["art"] for e in ereignisse(lei.c, "gewerk.")] == [
        "gewerk.angelegt",
        "gewerk.geaendert",
    ]
    lei.schreibe("PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "bearbeiter", "gewerk": gid})
    assert projektwelt["betrachter"].get(f"{OST}/gewerke").json()["gewerke"][0]["id"] == gid
    assert lei.schreibe("DELETE", f"{OST}/gewerke/{gid}").status_code == 204
    eintrag = next(
        b
        for b in lei.get(f"{OST}/beteiligte").json()["beteiligte"]
        if b["nutzerId"] == ben.nutzer.id
    )
    assert eintrag["gewerk"] is None
    geloescht = ereignisse(lei.c, "gewerk.geloescht")[-1]
    assert geloescht["project_id"] == "ost" and '"ohneGewerk":1' in geloescht["nachher"]


def test_gewerk_eines_anderen_projekts_nicht_aenderbar(projektwelt: Welt) -> None:  # noqa: F811
    fremd = _gewerk(projektwelt["leitung_kirche"], KIRCHE, "Kirchendach", "KD")
    lei = projektwelt["leitung"]
    assert lei.schreibe("PATCH", f"{OST}/gewerke/{fremd}", {"kurz": "XX"}).status_code == 404
    assert lei.schreibe("DELETE", f"{OST}/gewerke/{fremd}").status_code == 404


def test_gewerke_obergrenze(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    for i in range(50):
        _gewerk(lei, OST, f"Gewerk {i}", f"G{i}")
    assert (
        lei.schreibe("POST", f"{OST}/gewerke", {"name": "Eins zu viel", "kurz": "Z"}).status_code
        == 422
    )


def test_texte_in_web_und_zugriff_gleich() -> None:
    from scanview_server import web

    assert web._NUR_LEITUNG == zugriff.NUR_LEITUNG
    assert web._NICHT_PER_TOKEN == zugriff.NICHT_PER_TOKEN

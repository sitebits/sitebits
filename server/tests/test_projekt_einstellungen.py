"""Projekt-Einstellungen: Vorlage beim Einrichten, Kategorien je Projekt als Auswahl, Schalter mit
Wirkung am Server."""

from __future__ import annotations

from projektwelt import KIRCHE, OST, Konto, ereignisse, projektwelt, verbindung  # noqa: F401
from test_api_bearer import anon, auth, token
from test_authz import mit_paketen  # noqa: F401  (Fixture von projektwelt)

from scanview_server import projektverwaltung, routen_export

Welt = dict[str, Konto]
BESTAND = ["Raum", "Anlage", "Zugang", "Mangel", "Sonstiges"]
BAU = ["Mangel", "Frage", "Hinweis", "Entscheidung", "Sonstiges"]
POI = {
    "title": "Riss",
    "category": "Rückmeldung",
    "note": "",
    "position": [1, 2, 3],
    "station": None,
}


def _namen(k: Konto, basis: str, nur_aktive: bool = False) -> list[str]:
    return [x["name"] for x in k.get(basis).json()["kategorien"] if x["aktiv"] or not nur_aktive]


def _projekte_zeilen(k: Konto) -> int:
    conn = verbindung(k.c)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM projekte").fetchone()[0])
    finally:
        conn.close()


def test_vorgaben_ohne_einrichtung_und_lesen_schreibt_nicht(projektwelt: Welt) -> None:  # noqa: F811
    bet = projektwelt["betrachter"]
    r = bet.get(OST).json()
    assert [k["name"] for k in r["kategorien"]] == BESTAND and all(
        k["aktiv"] for k in r["kategorien"]
    )
    assert r["einstellungen"] == {"vorlage": None, "rueckmeldungen": True, "exportBearbeiter": True,
                                  "befundFaeden": False, "nurLeseLinks": False,
                                  "panoramenNurFreigegeben": False, "benachrichtigung": True}
    assert _projekte_zeilen(bet) == 0
    liste = bet.get("/api/v1/projekte").json()["projekte"]
    assert liste[0]["einstellungen"]["vorlage"] is None


def test_admin_richtet_mit_vorlage_ein(projektwelt: Welt) -> None:  # noqa: F811
    adm, lk = projektwelt["admin"], projektwelt["leitung_kirche"]
    assert lk.schreibe("POST", f"{KIRCHE}/einrichten", {"vorlage": "bauprojekt"}).status_code == 403
    r = adm.schreibe("POST", f"{KIRCHE}/einrichten", {"vorlage": "bauprojekt"})
    assert r.status_code == 200 and r.json()["einstellungen"]["vorlage"] == "bauprojekt"
    assert _namen(lk, KIRCHE) == BAU
    r = adm.schreibe("POST", f"{KIRCHE}/einrichten", {"vorlage": "bestandsaufnahme"})
    assert r.status_code == 409 and "Bauprojekt" in r.json()["fehler"]
    assert adm.schreibe("POST", f"{KIRCHE}/einrichten", {"vorlage": "frei"}).status_code == 422
    assert ereignisse(adm.c, "projekt.eingerichtet")[-1]["project_id"] == "kirche"


def test_einrichten_nach_erster_aenderung_409(projektwelt: Welt) -> None:  # noqa: F811
    lei, adm = projektwelt["leitung"], projektwelt["admin"]
    assert (
        lei.schreibe("PATCH", f"{OST}/einstellungen", {"exportBearbeiter": False}).status_code
        == 200
    )
    assert lei.get(OST).json()["einstellungen"]["vorlage"] == "bestandsaufnahme"
    assert adm.schreibe("POST", f"{OST}/einrichten", {"vorlage": "bauprojekt"}).status_code == 409


def test_kategorien_setzen(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    neu = [
        {"name": "Mangel", "aktiv": True},
        {"name": "Raum", "aktiv": False},
        {"name": "Anlage", "aktiv": True},
        {"name": "Zugang", "aktiv": True},
        {"name": "Sonstiges", "aktiv": True},
        {"name": "Frage", "aktiv": True},
    ]
    r = lei.schreibe("PUT", f"{OST}/kategorien", {"kategorien": neu})
    assert r.status_code == 200 and r.json()["kategorien"] == neu
    assert _namen(projektwelt["betrachter"], OST, nur_aktive=True) == [
        "Mangel",
        "Anlage",
        "Zugang",
        "Sonstiges",
        "Frage",
    ]
    arten = [e["art"] for e in ereignisse(lei.c, "kategorie.")]
    assert "kategorie.angelegt" in arten and "kategorie.geaendert" in arten
    assert all(e["project_id"] == "ost" for e in ereignisse(lei.c, "kategorie."))


def test_kategorien_regeln(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    basis = [{"name": n, "aktiv": True} for n in BESTAND]

    def setzen(liste: list[dict[str, object]]) -> int:
        return lei.schreibe("PUT", f"{OST}/kategorien", {"kategorien": liste}).status_code

    assert setzen(basis[1:]) == 422  # „Raum“ fehlt: nicht entfernbar
    assert setzen([*basis, {"name": "mangel", "aktiv": True}]) == 422  # doppelt ohne Groß/Klein
    for reserviert in ("Rückmeldung", "rückmeldung", "Rückmeldung"):
        assert setzen([*basis, {"name": reserviert, "aktiv": True}]) == 422
    assert setzen([*basis, {"name": "x" * 61, "aktiv": True}]) == 422
    assert setzen([*basis, {"name": "Tab\tKategorie", "aktiv": True}]) == 422
    assert setzen([*basis, *({"name": f"K{i}", "aktiv": True} for i in range(26))]) == 422  # 31
    assert setzen([*basis, {"name": "Neu", "aktiv": "ja"}]) == 422  # kein echter Wahrheitswert
    assert setzen(basis) == 200
    assert (
        projektwelt["bearbeiter"]
        .schreibe("PUT", f"{OST}/kategorien", {"kategorien": basis})
        .status_code
        == 403
    )


def test_poi_mit_freier_kategorie_bleibt_moeglich(projektwelt: Welt) -> None:  # noqa: F811
    ben = projektwelt["bearbeiter"]
    daten = {**POI, "category": "Altbestand aus dem Paket"}
    assert ben.schreibe("POST", f"{OST}/pois", {"daten": daten}).status_code == 201


def test_rueckmeldungen_aus_macht_rueckmelder_zum_betrachter(projektwelt: Welt) -> None:  # noqa: F811
    lei, rm = projektwelt["leitung"], projektwelt["rueckmelder"]
    assert rm.schreibe("POST", f"{OST}/pois", {"daten": POI}).status_code == 201
    r = lei.schreibe("PATCH", f"{OST}/einstellungen", {"rueckmeldungen": False})
    assert r.status_code == 200 and r.json()["einstellungen"]["rueckmeldungen"] is False
    assert rm.schreibe("POST", f"{OST}/pois", {"daten": POI}).status_code == 403
    roh = token(rm.c, rm.nutzer, "schreiben", "schreiben")
    assert anon(rm.c).post(f"{OST}/pois", headers=auth(roh), json={"daten": POI}).status_code == 403
    assert rm.get(OST).json()["rolle"] == "betrachter"
    liste = rm.get("/api/v1/projekte").json()["projekte"]
    assert next(p for p in liste if p["id"] == "ost")["rolle"] == "betrachter"
    eintrag = next(
        b
        for b in lei.get(f"{OST}/beteiligte").json()["beteiligte"]
        if b["nutzerId"] == rm.nutzer.id
    )
    assert eintrag["rolle"] == "rueckmelder"
    e = ereignisse(lei.c, "einstellung.geaendert")[-1]
    assert e["project_id"] == "ost" and '"rueckmeldungen":false' in e["nachher"]
    assert (
        lei.schreibe("PATCH", f"{OST}/einstellungen", {"rueckmeldungen": True}).status_code == 200
    )
    assert rm.schreibe("POST", f"{OST}/pois", {"daten": POI}).status_code == 201


def test_export_aus_nur_leitung_und_admin(projektwelt: Welt) -> None:  # noqa: F811
    lei, ben, adm = projektwelt["leitung"], projektwelt["bearbeiter"], projektwelt["admin"]
    assert (
        lei.schreibe("PATCH", f"{OST}/einstellungen", {"exportBearbeiter": False}).status_code
        == 200
    )
    for endung in ("csv", "json"):
        r = ben.get(f"{OST}/export.{endung}")
        assert (r.status_code, r.json()["fehler"]) == (403, routen_export.NUR_EXPORT_LEITUNG)
        assert lei.get(f"{OST}/export.{endung}").status_code == 200
        assert adm.get(f"{OST}/export.{endung}").status_code == 200
    roh = token(ben.c, ben.nutzer, "lesen", "lesen")
    assert anon(ben.c).get(f"{OST}/export.json", headers=auth(roh)).status_code == 403
    assert ben.get(f"{KIRCHE}/export.json").status_code == 403  # dort Betrachter, wie bisher


def test_einstellungen_eingaben(projektwelt: Welt) -> None:  # noqa: F811
    lei = projektwelt["leitung"]
    assert lei.schreibe("PATCH", f"{OST}/einstellungen", {}).status_code == 422
    assert lei.schreibe("PATCH", f"{OST}/einstellungen", {"rueckmeldungen": 1}).status_code == 422
    assert (
        lei.schreibe("PATCH", f"{OST}/einstellungen", {"export_bearbeiter": False}).status_code
        == 422
    )
    assert (
        lei.schreibe("PATCH", f"{OST}/einstellungen", {"rueckmeldungen": True}).status_code == 200
    )
    assert ereignisse(lei.c, "einstellung.geaendert") == []  # unverändert: kein Ereignis


def test_vorlagen_wie_festgelegt() -> None:
    assert projektverwaltung.VORLAGEN == {
        "bestandsaufnahme": tuple(BESTAND),
        "bauprojekt": tuple(BAU),
    }

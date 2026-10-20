"""Projektverlauf: die Projektleitung sieht die Verwaltung ihres Projekts, sonst niemand mehr als
bisher; per Token nur Bearbeitungen. Der Git-Export nimmt Verwaltungsereignisse nie auf."""

from __future__ import annotations

from projektwelt import KIRCHE, OST, Konto, projektwelt  # noqa: F401
from test_api_bearer import anon, auth, token
from test_authz import mit_paketen  # noqa: F401  (Fixture von projektwelt)

from scanview_server import gitexport, routen_export, web

Welt = dict[str, Konto]


def _arten(k: Konto, basis: str) -> set[str]:
    return {e["art"] for e in k.get(f"{basis}/protokoll?limit=200").json()["ereignisse"]}


def _verwaltung_anstossen(w: Welt) -> None:
    lei, ben = w["leitung"], w["bearbeiter"]
    gid = lei.schreibe("POST", f"{OST}/gewerke", {"name": "Elektro", "kurz": "EL"}).json()["id"]
    lei.schreibe(
        "PUT", f"{OST}/beteiligte/{ben.nutzer.id}", {"rolle": "rueckmelder", "gewerk": gid}
    )
    lei.schreibe(
        "POST", f"{OST}/einladungen", {"name": "N", "email": "n@x.de", "rolle": "betrachter"}
    )
    lei.schreibe("PATCH", f"{OST}/einstellungen", {"exportBearbeiter": False})
    w["leitung_kirche"].schreibe("POST", f"{KIRCHE}/gewerke", {"name": "Dach", "kurz": "DA"})
    lei.schreibe("PATCH", OST, {"titel": "Neu", "version": 0})


def test_leitung_sieht_verwaltungsereignisse(projektwelt: Welt) -> None:  # noqa: F811
    _verwaltung_anstossen(projektwelt)
    arten = _arten(projektwelt["leitung"], OST)
    assert {
        "gewerk.angelegt",
        "recht.vergeben",
        "beteiligter.gewerk",
        "einladung.angelegt",
        "einstellung.geaendert",
        "projekt.eingerichtet",
        "meta.geaendert",
    } <= arten
    assert not any(a.startswith("konto.") for a in arten)
    ereignisse = projektwelt["leitung"].get(f"{OST}/protokoll?limit=200").json()["ereignisse"]
    assert {e["projekt"] for e in ereignisse} == {"ost"}


def test_bearbeiter_und_token_sehen_nur_bearbeitungen(projektwelt: Welt) -> None:  # noqa: F811
    _verwaltung_anstossen(projektwelt)
    projektwelt["admin"].schreibe(
        "PUT",
        f"{OST}/beteiligte/{projektwelt['bearbeiter'].nutzer.id}",
        {"rolle": "bearbeiter", "gewerk": None},
    )
    assert _arten(projektwelt["bearbeiter"], OST) == {"meta.geaendert"}
    lei = projektwelt["leitung"]
    roh = token(lei.c, lei.nutzer, "lesen", "lesen")
    per_token = (
        anon(lei.c).get(f"{OST}/protokoll?limit=200", headers=auth(roh)).json()["ereignisse"]
    )
    assert {e["art"] for e in per_token} == {"meta.geaendert"}
    jsonl = anon(lei.c).get(f"{OST}/protokoll.jsonl", headers=auth(roh)).text
    assert "recht." not in jsonl and "einladung." not in jsonl


def test_admin_sieht_alles_per_sitzung(projektwelt: Welt) -> None:  # noqa: F811
    _verwaltung_anstossen(projektwelt)
    assert "gewerk.angelegt" in _arten(projektwelt["admin"], OST)


def test_texte_verlauf_gleich() -> None:
    assert web._NUR_VERLAUF == routen_export.NUR_VERLAUF


def test_openapi_nennt_die_verwaltungswege(projektwelt: Welt) -> None:  # noqa: F811
    pfade = projektwelt["leitung"].get("/api/v1/openapi.json").json()["paths"]
    for p in (
        "/api/v1/projekte/{pid}/beteiligte",
        "/api/v1/projekte/{pid}/beteiligte/{uid}",
        "/api/v1/projekte/{pid}/einladungen",
        "/api/v1/projekte/{pid}/gewerke",
        "/api/v1/projekte/{pid}/kategorien",
        "/api/v1/projekte/{pid}/einstellungen",
        "/api/v1/projekte/{pid}/einrichten",
    ):
        assert p in pfade, p


def test_gitexport_ignoriert_verwaltungsereignisse() -> None:
    for art in (
        "gewerk.angelegt",
        "gewerk.geaendert",
        "gewerk.geloescht",
        "beteiligter.gewerk",
        "kategorie.angelegt",
        "kategorie.geaendert",
        "einstellung.geaendert",
        "projekt.eingerichtet",
        "einladung.angelegt",
        "einladung.widerrufen",
        "recht.vergeben",
        "recht.entzogen",
    ):
        assert not gitexport._inhaltsart(art), art

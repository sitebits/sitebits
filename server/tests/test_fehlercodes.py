"""Feste Fehlercodes: die gemeinsame Fall-Tabelle gegen die echte Anwendung, die öffentliche
Liste gegen das Register und die 404-Grenze (kein Unterschied zwischen „gibt es nicht“ und „kein
Zugriff“)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fadenwelt import Welt, welt  # noqa: F401
from starlette.testclient import TestClient

from scanview_server import fehlercodes

REPO = Path(__file__).resolve().parents[2]
FAELLE: list[dict[str, Any]] = json.loads(
    (REPO / "docs/public/api/fehlerfaelle.json").read_text(encoding="utf-8"))["faelle"]


def _aufrufer(w: Welt, wer: str) -> TestClient:
    if wer == "-":
        return TestClient(w.client.app, base_url=str(w.client.base_url))
    konto, umfang, *rest = wer.split(":")
    return w.token(konto, umfang, agent=rest == ["agent"])


@pytest.mark.parametrize("fall", FAELLE, ids=[f["fall"] for f in FAELLE])
def test_fall_liefert_status_und_code(welt: Welt, fall: dict[str, Any]) -> None:  # noqa: F811
    r = _aufrufer(welt, fall["wer"]).request(fall["methode"], fall["pfad"],
                                             json=fall.get("koerper"))
    assert r.status_code == fall["status"], r.text
    koerper = r.json()
    assert koerper["code"] == fall["code"], koerper
    assert koerper.get("hinweis") == fehlercodes.bestimme(fall["status"], koerper["fehler"]).hinweis


def test_404_verraet_nicht_ob_es_das_projekt_gibt(welt: Welt) -> None:  # noqa: F811
    token = welt.token("bear", "lesen")
    fehlt = token.get("/api/v1/projekte/gibt-es-nicht")
    fremd = token.get("/api/v1/projekte/kirche")
    assert fehlt.status_code == fremd.status_code == 404
    assert fehlt.json() == fremd.json()
    hinweis = fremd.json()["hinweis"].lower()
    assert "kirche" not in hinweis and "rolle" not in hinweis


def test_jede_tabellen_zeile_nutzt_einen_bekannten_code() -> None:
    bekannt = {c.name for c in fehlercodes.alle()}
    assert {f["code"] for f in FAELLE} <= bekannt


def test_oeffentliche_liste_ist_aktuell() -> None:
    doku = (REPO / "docs/public/fehlercodes.md").read_text(encoding="utf-8")
    assert fehlercodes.tabelle() in doku, (
        "docs/public/fehlercodes.md neu erzeugen: fehlercodes.tabelle() einsetzen")


def test_unbekannter_text_bekommt_den_code_des_status() -> None:
    assert fehlercodes.bestimme(403, "irgendein Satz").name == "keine_berechtigung"
    assert fehlercodes.bestimme(418, "irgendein Satz").name == "fehler"
    assert fehlercodes.bestimme(502, "irgendein Satz").name == "serverfehler"
    # Ein 409 ohne eigenen Text ist kein Versionskonflikt: kein Rat, mit „version“ zu wiederholen.
    zustand = fehlercodes.bestimme(409, "irgendein Satz")
    assert zustand.name == "zustand" and "version" not in (zustand.hinweis or "")


def test_sperren_und_anmeldung_haben_eigene_codes() -> None:
    from scanview_server import routen_auth, routen_teilen, teilen, web

    assert fehlercodes.bestimme(429, routen_auth.GESPERRT).name == "gesperrt"
    assert fehlercodes.bestimme(429, routen_teilen.GESPERRT).name == "gesperrt"
    assert "15 Minuten" in (fehlercodes.GESPERRT.hinweis or "")
    assert fehlercodes.bestimme(401, routen_auth.FALSCH).name == "anmeldung_fehlgeschlagen"
    assert fehlercodes.bestimme(401, routen_auth.PASSKEY_FEHLGESCHLAGEN).name \
        == "anmeldung_fehlgeschlagen"
    assert fehlercodes.bestimme(401, teilen.UNGUELTIG).name == "link_ungueltig"
    assert fehlercodes.bestimme(403, web.CSRF_FEHLT).name == "csrf_fehlt"
    assert fehlercodes.bestimme(403, web.NEU_ANMELDEN_PRAEFIX + "das Passwort zu ändern.").name \
        == "neu_anmelden"


def test_abschrift_in_web_gleicht_dem_original() -> None:
    from scanview_server import web, zugriff

    assert web._NICHT_PER_TOKEN == zugriff.NICHT_PER_TOKEN


def test_text_mit_eingesetztem_wert_bekommt_eigenen_code() -> None:
    text = "Auftragstyp auswerten_sonne_dach ist auf dieser Instanz noch nicht verfügbar."
    assert fehlercodes.bestimme(422, text).name == "nicht_verfuegbar"

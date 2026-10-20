"""Negativtests der Projektverwaltung, aus den Routen erzeugt: jede Verwaltungsroute unter
/api/v1/projekte/{pid}/ lehnt ab — ohne Herkunftsprüfung, per Lese- und Schreib-Token, für
Bearbeiter, für die Projektleitung eines anderen Projekts, ohne Anmeldung. Eine neue Route ohne
Eintrag in ERWARTET lässt test_routenliste_vollstaendig scheitern."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient
from projektwelt import OST, Konto, projektwelt  # noqa: F401
from test_api_bearer import anon, auth, token, zaehle_ereignisse
from test_authz import mit_paketen  # noqa: F401  (Fixture von projektwelt)
from test_lesezugriff import _alle_routen

Welt = dict[str, Konto]
_VERWALTUNG_RE = re.compile(
    r"^/api/v1/projekte/\{pid\}/(einladungen|beteiligte|gewerke|kategorien|einstellungen|einrichten)(/|$)"
)
P = "/api/v1/projekte/{pid}"
# Routen, die Projektleitung/Admin per Sitzung verlangen, mit gültigem Körper.
ERWARTET: dict[tuple[str, str], Any] = {
    ("GET", f"{P}/einladungen"): None,
    ("POST", f"{P}/einladungen"): {"name": "N", "email": "neg@x.de", "rolle": "betrachter"},
    ("POST", f"{P}/einladungen/{{eid}}/widerrufen"): None,
    ("PUT", f"{P}/beteiligte/{{uid}}"): {"rolle": "betrachter", "gewerk": None},
    ("POST", f"{P}/gewerke"): {"name": "Neg", "kurz": "NG"},
    ("PATCH", f"{P}/gewerke/{{gid}}"): {"kurz": "NX"},
    ("DELETE", f"{P}/gewerke/{{gid}}"): None,
    ("PUT", f"{P}/kategorien"): {"kategorien": [{"name": "Raum", "aktiv": False}]},
    ("PATCH", f"{P}/einstellungen"): {"rueckmeldungen": False},
    ("POST", f"{P}/einrichten"): {"vorlage": "bauprojekt"},
}
# Für alle Projektbeteiligten lesbar (auch per Token).
LESBAR = {("GET", f"{P}/beteiligte"), ("GET", f"{P}/gewerke")}


def _routen(c: TestClient) -> set[tuple[str, str]]:
    """Alle Verwaltungsrouten; eingebundene Router liegen als Kinder unter `original_router`."""
    aus: set[tuple[str, str]] = set()
    for pfad, methoden in _alle_routen(c.app.routes):  # type: ignore[attr-defined]
        if _VERWALTUNG_RE.match(pfad):
            aus |= {(m, pfad) for m in methoden if m != "HEAD"}
    return aus


def _konkret(pfad: str, w: Welt, gid: int) -> str:
    return (
        pfad.replace("{pid}", "ost")
        .replace("{eid}", "0123456789abcdef")
        .replace("{uid}", str(w["betrachter"].nutzer.id))
        .replace("{gid}", str(gid))
    )


def _gewerk(w: Welt) -> int:
    return int(
        w["leitung"].schreibe("POST", f"{OST}/gewerke", {"name": "Fest", "kurz": "FE"}).json()["id"]
    )


def test_routenliste_vollstaendig(projektwelt: Welt) -> None:  # noqa: F811
    assert len(ERWARTET) + len(LESBAR) >= 12
    assert _routen(projektwelt["leitung"].c) == set(ERWARTET) | LESBAR


def test_verwaltung_ohne_csrf_403(projektwelt: Welt) -> None:  # noqa: F811
    gid = _gewerk(projektwelt)
    c = projektwelt["leitung"].c
    for (methode, pfad), koerper in ERWARTET.items():
        if methode == "GET":
            continue
        vorher = zaehle_ereignisse(c)
        r = c.request(methode, _konkret(pfad, projektwelt, gid), json=koerper)
        assert (r.status_code, r.json()["fehler"]) == (403, "CSRF"), pfad
        assert zaehle_ereignisse(c) == vorher, pfad


def test_lese_token_verwaltet_nichts(projektwelt: Welt) -> None:  # noqa: F811
    _token_verwaltet_nichts(projektwelt, "lesen")


def test_schreib_token_verwaltet_nichts(projektwelt: Welt) -> None:  # noqa: F811
    _token_verwaltet_nichts(projektwelt, "schreiben")


def _token_verwaltet_nichts(w: Welt, umfang: str) -> None:
    gid = _gewerk(w)
    lei = w["leitung"]
    roh = token(lei.c, lei.nutzer, f"neg-{umfang}", umfang)
    c = anon(lei.c)
    for (methode, pfad), koerper in ERWARTET.items():
        vorher = zaehle_ereignisse(lei.c)
        r = c.request(methode, _konkret(pfad, w, gid), json=koerper, headers=auth(roh))
        assert r.status_code == 403, (umfang, methode, pfad, r.text)
        assert zaehle_ereignisse(lei.c) == vorher, pfad


def test_bearbeiter_verwaltet_nichts(projektwelt: Welt) -> None:  # noqa: F811
    gid = _gewerk(projektwelt)
    for (methode, pfad), koerper in ERWARTET.items():
        r = projektwelt["bearbeiter"].schreibe(methode, _konkret(pfad, projektwelt, gid), koerper)
        assert r.status_code == 403, pfad


def test_leitung_anderes_projekt_404(projektwelt: Welt) -> None:  # noqa: F811
    gid = _gewerk(projektwelt)
    for (methode, pfad), koerper in ERWARTET.items():
        r = projektwelt["leitung_kirche"].schreibe(
            methode, _konkret(pfad, projektwelt, gid), koerper
        )
        assert r.status_code == 404, pfad
        if koerper is not None:  # kaputter Körper verrät die Route nicht
            r = projektwelt["leitung_kirche"].schreibe(
                methode, _konkret(pfad, projektwelt, gid), {"x": 1}
            )
            assert r.status_code == 404, pfad


def test_ohne_anmeldung_401(projektwelt: Welt) -> None:  # noqa: F811
    gid = _gewerk(projektwelt)
    c = anon(projektwelt["leitung"].c)
    for (methode, pfad), koerper in {**ERWARTET, **{k: None for k in LESBAR}}.items():
        r = c.request(
            methode,
            _konkret(pfad, projektwelt, gid),
            json=koerper,
            headers={"Origin": "https://scanview.test", "X-Scanview": "1"},
        )
        assert r.status_code == 401, pfad

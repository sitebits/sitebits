"""Egress-Test: statische Prüfung des Bundles auf absolute URLs, Auswertung der im Browser
erfassten Anfragen (Kontrollanfrage, Abdeckung, fremde Origins), Testinstanz mit Paket, Lauf im
gehärteten Container und Release-Stufe. Die echten Läufe (Container, Browser) stehen unten."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import egress, fuzzing
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.werkzeuge import (
    HOST_NETZ,
    INSTANZ_CADDY_PORT,
    INSTANZ_SERVER_PORT,
    Werkzeug,
    docker_befehl,
    lade_werkzeuge,
)

WURZEL = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "egress"
ECHTE_WERKZEUGE = WURZEL / "nachweise" / "werkzeuge.json"
DIGEST = "sha256:" + "b" * 64
GRUND = "kein Abruf: die Adresse steht nur als Text im Bibliothekscode"


def _anfragen(name: str) -> dict[str, Any]:
    daten: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return daten


def _ausnahmen_datei(tmp_path: Path, text: str) -> Path:
    pfad = tmp_path / "egress-ausnahmen.toml"
    pfad.write_text(text, encoding="utf-8")
    return pfad


def _eintrag(url: str = "", praefix: str = "", art: str = "namensraum", grund: str = GRUND,
             datum: str = "2026-10-01") -> str:
    zeilen = ["[[ausnahme]]"]
    if url:
        zeilen.append(f'url = "{url}"')
    if praefix:
        zeilen.append(f'praefix = "{praefix}"')
    zeilen += [f'art = "{art}"', f'grund = "{grund}"', f"datum = {datum}"]
    return "\n".join(zeilen) + "\n"


# --- Statische Prüfung: bundle_urls --------------------------------------------------------------

def test_bundle_fremd_liefert_zwei_funde() -> None:
    funde = egress.bundle_urls(FIXTURES / "bundle_fremd")
    assert [(f.datei, f.url, f.host) for f in funde] == [
        ("assets/karte.js", "https://tiles.example.org/{z}/{x}/{y}.png", "tiles.example.org"),
        ("assets/stil.css", "https://fonts.example.org/css2?family=Beispiel",
         "fonts.example.org")]


def test_bundle_sauber_ohne_funde() -> None:
    assert egress.bundle_urls(FIXTURES / "bundle_sauber") == []


def test_fund_ohne_ausnahme_blockiert() -> None:
    blockiert = egress.statisch_gate(egress.bundle_urls(FIXTURES / "bundle_fremd"), [])
    assert len(blockiert) == 2
    assert "tiles.example.org" in blockiert[0] and "assets/karte.js" in blockiert[0]
    assert "fonts.example.org" in blockiert[1] and "assets/stil.css" in blockiert[1]


def test_fund_mit_passender_ausnahme_frei(tmp_path: Path) -> None:
    text = (_eintrag(url="https://tiles.example.org/{z}/{x}/{y}.png")
            + _eintrag(url="https://fonts.example.org/css2?family=Beispiel",
                       art="link-ohne-abruf"))
    ausnahmen = egress.lade_ausnahmen(_ausnahmen_datei(tmp_path, text))
    assert egress.statisch_gate(egress.bundle_urls(FIXTURES / "bundle_fremd"), ausnahmen) == []


def test_praefix_ausnahme_erfasst_url_darunter(tmp_path: Path) -> None:
    ausnahmen = egress.lade_ausnahmen(_ausnahmen_datei(
        tmp_path, _eintrag(praefix="https://tiles.example.org/")
        + _eintrag(praefix="https://fonts.example.org/css2")))
    assert egress.statisch_gate(egress.bundle_urls(FIXTURES / "bundle_fremd"), ausnahmen) == []


def test_ausnahme_gilt_nur_fuer_die_genaue_url(tmp_path: Path) -> None:
    ausnahmen = egress.lade_ausnahmen(_ausnahmen_datei(
        tmp_path, _eintrag(url="https://tiles.example.org/{z}/{x}/{y}.png")))
    blockiert = egress.statisch_gate(egress.bundle_urls(FIXTURES / "bundle_fremd"), ausnahmen)
    assert len(blockiert) == 1 and "fonts.example.org" in blockiert[0]


def test_ausnahme_ohne_fund_ist_nur_eine_meldung(tmp_path: Path) -> None:
    ausnahmen = egress.lade_ausnahmen(_ausnahmen_datei(
        tmp_path, _eintrag(url="https://alt.example.org/x")))
    funde = egress.bundle_urls(FIXTURES / "bundle_sauber")
    assert egress.statisch_gate(funde, ausnahmen) == []
    assert egress.ungenutzte_ausnahmen(funde, ausnahmen) == [
        "Ausnahme ohne Fund: https://alt.example.org/x"]


def test_wasm_mit_url_ist_ein_fund(tmp_path: Path) -> None:
    (tmp_path / "modul.wasm").write_bytes(
        b"\x00asm\x01\x00\x00\x00\xff\xfe" + b"\x00https://cdn.example.org/lib.wasm\x00\x01")
    funde = egress.bundle_urls(tmp_path)
    assert [(f.datei, f.url) for f in funde] == [("modul.wasm", "https://cdn.example.org/lib.wasm")]


def test_task_unkomprimiert_wird_durchsucht(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "modell.task", "w", zipfile.ZIP_STORED) as z:
        z.writestr("a.tflite", b"\x00\x01https://cdn.example.org/m\x00")
    funde = egress.bundle_urls(tmp_path)
    assert [(f.datei, f.url) for f in funde] == [("modell.task", "https://cdn.example.org/m")]


def test_task_komprimiert_oder_kein_zip_ist_fehler(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "modell.task", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.tflite", b"https://cdn.example.org/m" * 50)
    with pytest.raises(NachweisFehler, match=r"komprimierte Einträge a\.tflite"):
        egress.bundle_urls(tmp_path)
    (tmp_path / "modell.task").write_bytes(b"kein zip")
    with pytest.raises(NachweisFehler, match="kein ZIP"):
        egress.bundle_urls(tmp_path)


def test_png_mit_url_im_textblock_ist_ein_fund(tmp_path: Path) -> None:
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "symbol.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\x1btEXtQuelle\x00https://cdn.example.org/s\x00\xff")
    funde = egress.bundle_urls(tmp_path)
    assert [(f.datei, f.url) for f in funde] == [("icons/symbol.png", "https://cdn.example.org/s")]


def test_url_in_untergeordneten_ordnern_und_verschiedenen_endungen(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    for name in ("x.mjs", "x.json", "x.svg", "x.webmanifest", "x.map", "x.txt", "x.html"):
        (tmp_path / "a" / "b" / name).write_text('"wss://stream.example.org/s"', encoding="utf-8")
    funde = egress.bundle_urls(tmp_path)
    assert len(funde) == 7 and {f.host for f in funde} == {"stream.example.org"}


def test_dieselbe_url_zweimal_in_einer_datei_zaehlt_einmal(tmp_path: Path) -> None:
    (tmp_path / "x.js").write_text("'https://a.example.org/x' 'https://a.example.org/x'",
                                   encoding="utf-8")
    assert len(egress.bundle_urls(tmp_path)) == 1


def test_backtick_beendet_die_url(tmp_path: Path) -> None:
    (tmp_path / "x.js").write_text("`https://a.example.org/x`+1", encoding="utf-8")
    assert [f.url for f in egress.bundle_urls(tmp_path)] == ["https://a.example.org/x"]


def test_unbekannte_endung_ist_fehler(tmp_path: Path) -> None:
    (tmp_path / "schrift.woff2").write_bytes(b"wOF2")
    with pytest.raises(NachweisFehler) as e:
        egress.bundle_urls(tmp_path)
    assert e.value.regel == "egress-datei" and "schrift.woff2" in e.value.text


def test_datei_ohne_endung_ist_fehler(tmp_path: Path) -> None:
    (tmp_path / "LIESMICH").write_text("x", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        egress.bundle_urls(tmp_path)
    assert e.value.regel == "egress-datei"


def test_symlink_im_bundle_ist_fehler(tmp_path: Path) -> None:
    (tmp_path / "echt.js").write_text("x", encoding="utf-8")
    os.symlink("echt.js", tmp_path / "link.js")
    with pytest.raises(NachweisFehler) as e:
        egress.bundle_urls(tmp_path)
    assert e.value.regel == "egress-datei" and "link.js" in e.value.text


@pytest.mark.parametrize("anlegen", [False, True])
def test_fehlendes_oder_leeres_bundle_ist_fehler(tmp_path: Path, anlegen: bool) -> None:
    ziel = tmp_path / "dist"
    if anlegen:
        ziel.mkdir()
    with pytest.raises(NachweisFehler) as e:
        egress.bundle_urls(ziel)
    assert e.value.regel == "egress-datei"


# --- Ausnahmen laden -----------------------------------------------------------------------------

def test_ausnahmen_laden(tmp_path: Path) -> None:
    text = (_eintrag(url="http://www.w3.org/2000/svg")
            + _eintrag(praefix="https://lizenz.example.org/", art="lizenzhinweis"))
    a = egress.lade_ausnahmen(_ausnahmen_datei(tmp_path, text))
    assert [(x.url, x.praefix, x.art, x.datum) for x in a] == [
        ("http://www.w3.org/2000/svg", None, "namensraum", date(2026, 10, 1)),
        (None, "https://lizenz.example.org/", "lizenzhinweis", date(2026, 10, 1))]


def test_leere_ausnahmedatei_ist_gueltig(tmp_path: Path) -> None:
    assert egress.lade_ausnahmen(_ausnahmen_datei(tmp_path, "# nichts\n")) == []


@pytest.mark.parametrize("text", [
    _eintrag(url="https://a.example.org/x", grund="zu kurz"),
    _eintrag(url="https://a.example.org/x", art="sonstiges"),
    _eintrag(url="https://a.example.org/x", datum='"gestern"'),
    _eintrag(url="https://a.example.org/x", praefix="https://a.example.org/"),
    "[[ausnahme]]\nart = \"namensraum\"\ngrund = \"" + GRUND + "\"\ndatum = 2026-10-01\n",
    _eintrag(url="https://*.example.org/x"),
    _eintrag(praefix="https://*.example.org/"),
    _eintrag(praefix="https://"),
    _eintrag(praefix="https://example.org"),
    _eintrag(praefix="example.org/"),
    _eintrag(url="ftp://example.org/x"),
    _eintrag(url="https://a.example.org/x") + 'host = "a.example.org"\n',
    "[[ausnahme]]\nurl = \"https://a.example.org/x\"\n",
    "[[regel]]\nx = 1\n",
    "kaputt = = =\n",
])
def test_ausnahme_fehlerhaft_ist_ladefehler(tmp_path: Path, text: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        egress.lade_ausnahmen(_ausnahmen_datei(tmp_path, text))
    assert e.value.regel == "egress-ausnahmen"


def test_ausnahmedatei_fehlt(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        egress.lade_ausnahmen(tmp_path / "gibt-es-nicht.toml")
    assert e.value.regel == "egress-ausnahmen"


@pytest.mark.skipif(not (WURZEL / "docs" / "compliance" / "egress-ausnahmen.toml").exists(),
                    reason="egress-ausnahmen.toml liegt nur im privaten Repo (nicht im Export)")
def test_echte_ausnahmen_laden() -> None:
    egress.lade_ausnahmen(WURZEL / "docs" / "compliance" / "egress-ausnahmen.toml")


# --- Browser-Ergebnis: egress_gate ---------------------------------------------------------------

ORIGIN = "http://localhost:18090"


def test_anfragen_sauber_ist_frei() -> None:
    assert egress.egress_gate(_anfragen("anfragen_sauber"), ORIGIN) == []


def test_anfragen_fremd_liefert_zwei_blockierende_meldungen() -> None:
    blockiert = egress.egress_gate(_anfragen("anfragen_fremd"), ORIGIN)
    assert len(blockiert) == 2
    assert any("requestfailed" in b and "https://cdn.example.org/x.js" in b and "viewer" in b
               for b in blockiert)
    assert any("csp" in b and "https://fonts.example.org" in b and "portal" in b
               for b in blockiert)


def test_ohne_kontrollanfrage_ist_das_ergebnis_ungueltig() -> None:
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(_anfragen("anfragen_ohne_kontrolle"), ORIGIN)
    assert e.value.regel == "egress-erfassung"
    assert "Kontrollanfrage nicht erfasst — Ergebnis ungültig" in e.value.text


@pytest.mark.parametrize("fehlt", ["http://egress-kontrolle.invalid/f",
                                   "https://egress-kontrolle.invalid/i.png"])
def test_jede_der_beiden_kontrollanfragen_je_seite_zaehlt(fehlt: str) -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"] = [a for a in d["anfragen"]
                     if not (a["url"] == fehlt and a["seite"] == "verlauf")]
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung" and "verlauf" in e.value.text


def test_bildkontrolle_nur_als_csp_ist_keine_wirksame_erfassung() -> None:
    """Das Bild erreicht die Netzebene (`request`/`requestfailed`), auch wenn die Richtlinie
    es meldet; steht es nur als Richtlinien-Verstoß da, sind die Netzwege nicht erfasst."""
    d = _anfragen("anfragen_sauber")
    d["anfragen"] = [a for a in d["anfragen"] if not (
        a["seite"] == "viewer" and a["url"].startswith("https://egress-kontrolle")
        and a["quelle"] != "csp")]
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung" and "viewer" in e.value.text


def test_zweiter_portalbesuch_hat_eigene_kontrollanfragen() -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"] = [a for a in d["anfragen"] if a["seite"] != "portal-zurueck"]
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung" and "portal-zurueck" in e.value.text


def test_kontrollanfrage_einer_anderen_seite_ersetzt_keine() -> None:
    d = _anfragen("anfragen_sauber")
    for a in d["anfragen"]:
        if "egress-kontrolle" in a["url"] and a["seite"] == "anmelden":
            a["seite"] = "portal"
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung" and "anmelden" in e.value.text


def test_fehlende_seite_in_der_liste_ist_erfassungsfehler() -> None:
    d = _anfragen("anfragen_sauber")
    d["seiten"].remove("barrierefreiheit")
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung" and "barrierefreiheit" in e.value.text


def test_ohne_punkte_ist_die_abdeckung_ungenuegend() -> None:
    d = _anfragen("anfragen_sauber")
    d["geladen"]["punkte"] = 0
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-abdeckung" and "Punkte" in e.value.text


@pytest.mark.parametrize("pfad", ["project.json", "cloud.copc.laz", "topview.png", "panos/",
                                  "/api/v1/projekte"])
def test_abdeckung_braucht_jede_eigene_anfrage(pfad: str) -> None:
    d = _anfragen("anfragen_sauber")
    d["geladen"]["eigene"] = [e for e in d["geladen"]["eigene"] if pfad not in e["pfad"]]
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-abdeckung"


@pytest.mark.parametrize("pfad", ["/api/projekte", "/api/v1/projekte/egress-test/pois",
                                  "/api/v1/projekte?x=1"])
def test_abdeckung_projekt_api_in_jeder_form(pfad: str) -> None:
    """Portal (`/api/projekte`) und Viewer (`/api/v1/projekte/<id>/…`) rufen dieselbe
    Projekt-API in verschiedener Form auf."""
    d = _anfragen("anfragen_sauber")
    d["geladen"]["eigene"] = [e for e in d["geladen"]["eigene"] if "/api/" not in e["pfad"]]
    d["geladen"]["eigene"].append({"pfad": pfad, "status": 200})
    assert egress.egress_gate(d, ORIGIN) == []


@pytest.mark.parametrize("pfad", ["/api/projektel", "/api/v1/projekte-x", "/api/v2/projekte",
                                  "/x/api/projekte"])
def test_abdeckung_fremder_api_pfad_zaehlt_nicht(pfad: str) -> None:
    d = _anfragen("anfragen_sauber")
    d["geladen"]["eigene"] = [e for e in d["geladen"]["eigene"] if "/api/" not in e["pfad"]]
    d["geladen"]["eigene"].append({"pfad": pfad, "status": 200})
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-abdeckung"


@pytest.mark.parametrize("status", [0, 199, 301, 401, 404, 500])
def test_abdeckung_braucht_2xx(status: int) -> None:
    d = _anfragen("anfragen_sauber")
    for e in d["geladen"]["eigene"]:
        if e["pfad"].endswith("topview.png"):
            e["status"] = status
    with pytest.raises(NachweisFehler) as f:
        egress.egress_gate(d, ORIGIN)
    assert f.value.regel == "egress-abdeckung" and "topview.png" in f.value.text


def test_erfassung_wird_vor_der_abdeckung_geprueft() -> None:
    d = _anfragen("anfragen_ohne_kontrolle")
    d["geladen"]["punkte"] = 0
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung"


def test_fremder_port_und_fremdes_schema_sind_fremd() -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"] += [
        {"url": "http://localhost:18091/x", "quelle": "request", "seite": "portal"},
        {"url": "https://localhost:18090/x", "quelle": "request", "seite": "portal"},
        {"url": "http://127.0.0.1:18090/x", "quelle": "request", "seite": "portal"},
        {"url": "ws://evil.example.org/s", "quelle": "websocket", "seite": "viewer"},
        {"url": "http://localhost.evil.example.org:18090/x", "quelle": "request",
         "seite": "viewer"}]
    blockiert = egress.egress_gate(d, ORIGIN)
    assert len(blockiert) == 5


def test_websocket_zum_eigenen_origin_ist_eigen() -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"].append({"url": "ws://localhost:18090/s", "quelle": "websocket",
                          "seite": "viewer"})
    assert egress.egress_gate(d, ORIGIN) == []


def test_data_blob_und_about_sind_kein_netzverkehr() -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"] += [{"url": u, "quelle": "request", "seite": "viewer"}
                      for u in ("data:text/plain,x", "blob:http://evil.example.org/1",
                                "about:blank")]
    assert egress.egress_gate(d, ORIGIN) == []


def test_csp_verstoss_ohne_url_blockiert() -> None:
    d = _anfragen("anfragen_sauber")
    d["anfragen"].append({"url": "inline", "quelle": "csp", "seite": "viewer"})
    blockiert = egress.egress_gate(d, ORIGIN)
    assert len(blockiert) == 1 and "inline" in blockiert[0] and "csp" in blockiert[0]


def test_gate_aendert_die_eingabe_nicht() -> None:
    d = _anfragen("anfragen_fremd")
    vorher = copy.deepcopy(d)
    egress.egress_gate(d, ORIGIN)
    assert d == vorher


@pytest.mark.parametrize("feld", ["origin", "seiten", "anfragen", "geladen"])
def test_unvollstaendiges_ergebnis_ist_fehler(feld: str) -> None:
    d = _anfragen("anfragen_sauber")
    del d[feld]
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(d, ORIGIN)
    assert e.value.regel == "egress-erfassung"


def test_fremder_origin_im_ergebnis_passt_nicht() -> None:
    """Der beim Lauf erfasste Origin muss der der Instanz sein (sonst wurde gegen etwas anderes
    gemessen)."""
    with pytest.raises(NachweisFehler) as e:
        egress.egress_gate(_anfragen("anfragen_sauber"), "http://localhost:9999")
    assert e.value.regel == "egress-erfassung"


# --- Werkzeug: Playwright ------------------------------------------------------------------------

def test_host_netz_umfasst_playwright() -> None:
    assert HOST_NETZ == frozenset({"schemathesis", "caddy-test", "playwright"})


def test_host_netz_fuer_trivy_bleibt_verweigert() -> None:
    w = Werkzeug("trivy", f"aquasec/trivy@{DIGEST}", "0.74.0", None)
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(w, [], {}, netz="host", uid=1, gid=1)
    assert e.value.regel == "werkzeug-netz"


def test_playwright_version_gleich_lockfile() -> None:
    w = lade_werkzeuge(ECHTE_WERKZEUGE)["playwright"]
    lock = json.loads((WURZEL / "web" / "package-lock.json").read_text("utf-8"))
    assert w.version == lock["packages"]["node_modules/@playwright/test"]["version"]
    assert lock["packages"]["node_modules/playwright-core"]["version"] == w.version
    assert w.image.startswith("mcr.microsoft.com/playwright@sha256:")
    assert w.werkzeugordner is None


def test_standardports_gemeinsam_mit_fuzzing() -> None:
    assert (egress.CADDY_PORT, egress.SERVER_PORT) == (INSTANZ_CADDY_PORT, INSTANZ_SERVER_PORT)
    assert egress.ORIGIN == fuzzing.URL


# --- Testinstanz mit Paket (Attrappen) -----------------------------------------------------------

UV = "ghcr.io/astral-sh/uv@sha256:" + "d" * 64


def _werkzeuge() -> dict[str, Werkzeug]:
    return {"schemathesis": Werkzeug("schemathesis", UV, "4.27.5", "werkzeuge/schemathesis"),
            "caddy-test": Werkzeug("caddy-test", "caddy@sha256:" + "c" * 64, "2.11.4", None),
            "playwright": Werkzeug("playwright", f"mcr.microsoft.com/playwright@{DIGEST}",
                                   "1.63.0", None)}


class _Prozess:
    beendet = False

    def __init__(self, *a: object, **kw: object) -> None:
        _Prozess.beendet = False

    def poll(self) -> int | None:
        return 0 if _Prozess.beendet else None

    def terminate(self) -> None:
        _Prozess.beendet = True

    def wait(self, *a: object) -> int:
        return 0

    def kill(self) -> None:
        _Prozess.beendet = True


def _worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / "web" / "dist").mkdir(parents=True)
    (wt / "web" / "dist" / "portal.html").write_text("x", encoding="utf-8")
    (wt / "server").mkdir()
    (wt / "deploy" / "platform").mkdir(parents=True)
    for name in ("Caddyfile.lokal", "scanview.caddy"):
        (wt / "deploy" / "platform" / name).write_text("x", encoding="utf-8")
    return wt


def _instanz_attrappen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    befehle: list[list[str]] = []

    def ausfuehren(befehl: list[str], regel: str, **kw: object) -> subprocess.CompletedProcess[str]:
        befehle.append(befehl)
        aus = "http://localhost:18090/einladung#t=einladung123\n" if "einladen" in befehl else ""
        return subprocess.CompletedProcess(befehl, 0, aus, "")

    monkeypatch.setattr(fuzzing, "pruefe_ports_frei", lambda ports: None)
    monkeypatch.setattr(fuzzing, "ausfuehren", ausfuehren)
    monkeypatch.setattr(fuzzing, "_warte_bereit", lambda *a: None)
    monkeypatch.setattr(fuzzing.subprocess, "Popen", _Prozess)
    monkeypatch.setattr(fuzzing, "_einloesen", lambda e: "cookiewert")
    monkeypatch.setattr(fuzzing, "_api_token", lambda c: "tokenwert")
    return befehle


def test_testinstanz_mit_paket_laedt_genau_dieses_paket(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    befehle = _instanz_attrappen(monkeypatch)
    wt = _worktree(tmp_path)
    inst = fuzzing.starte_testinstanz(wt, tmp_path / "arbeit", _werkzeuge(),
                                      paket=FIXTURES / "paket")
    inst.stoppen()
    daten = tmp_path / "arbeit" / "egress" / "data"
    assert [p.name for p in daten.iterdir()] == ["egress-test"]
    assert (daten / "egress-test" / "project.json").is_file()
    assert (daten / "egress-test" / "panos" / "S001" / "nz" / "64.jpg").is_file()
    # Attrappen-Pakete werden nicht angelegt; der Betrachter erhält die Rolle auf das Paket.
    assert not any("pakete" in " ".join(b) for b in befehle)
    einladung = next(b for b in befehle if "einladen" in b and "--projekt" in b)
    assert einladung[einladung.index("--projekt") + 1] == "egress-test=betrachter"
    caddy = next(b for b in befehle if "caddy" in b)
    assert any(t.endswith(":/srv/data:ro") for t in caddy)


def test_paket_wird_kopiert_nicht_verschoben(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _instanz_attrappen(monkeypatch)
    paket = tmp_path / "paket"
    (paket / "panos").mkdir(parents=True)
    (paket / "project.json").write_text('{"projectId": "kopie-test"}', encoding="utf-8")
    (paket / "panos" / "a.jpg").write_bytes(b"x")
    inst = fuzzing.starte_testinstanz(_worktree(tmp_path), tmp_path / "arbeit", _werkzeuge(),
                                      paket=paket)
    inst.stoppen()
    assert (paket / "project.json").is_file() and (paket / "panos" / "a.jpg").is_file()
    assert (tmp_path / "arbeit" / "egress" / "data" / "kopie-test" / "panos" / "a.jpg").is_file()


@pytest.mark.parametrize("inhalt", [None, "kein json", '{"projectId": "../x"}',
                                    '{"projectId": ""}', "{}", '{"projectId": 5}'])
def test_paket_ohne_gueltige_projektkennung_ist_fehler(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       inhalt: str | None) -> None:
    befehle = _instanz_attrappen(monkeypatch)
    paket = tmp_path / "paket"
    paket.mkdir()
    if inhalt is not None:
        (paket / "project.json").write_text(inhalt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        fuzzing.starte_testinstanz(_worktree(tmp_path), tmp_path / "arbeit", _werkzeuge(),
                                   paket=paket)
    assert e.value.regel == "fuzzing-instanz" and "project.json" in e.value.text
    assert befehle == []  # nichts gestartet


def test_fuzzing_und_egress_teilen_sich_einen_arbeitsordner(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch
                                                             ) -> None:
    """Beide Stufen laufen im selben Prozess mit demselben Arbeitsordner: eigener Unterordner und
    eigener Containername, sonst scheitert die zweite Instanz an der ersten."""
    befehle = _instanz_attrappen(monkeypatch)
    wt = _worktree(tmp_path)
    eins = fuzzing.starte_testinstanz(wt, tmp_path / "arbeit", _werkzeuge())
    eins.stoppen()
    zwei = fuzzing.starte_testinstanz(wt, tmp_path / "arbeit", _werkzeuge(),
                                      paket=FIXTURES / "paket")
    zwei.stoppen()
    assert (tmp_path / "arbeit" / "fuzzing").is_dir() and (tmp_path / "arbeit" / "egress").is_dir()
    namen = [b[b.index("--name") + 1] for b in befehle if "caddy" in b and "--name" in b]
    assert len(namen) == 2 and namen[0] != namen[1]
    assert namen[0].startswith("scanview-fuzzing-") and namen[1].startswith("scanview-egress-")


# --- Browser-Lauf (Attrappe des Containers) ------------------------------------------------------

def _inst(tmp_path: Path) -> fuzzing.Testinstanz:
    ordner = tmp_path / "instanz"
    ordner.mkdir(exist_ok=True)
    daten = ordner / "data" / "egress-test"
    daten.mkdir(parents=True, exist_ok=True)
    (daten / "stations.json").write_text(
        json.dumps({"stations": [{"id": "S001"}, {"id": "S002"}]}), encoding="utf-8")
    return fuzzing.Testinstanz(url=fuzzing.URL, admin_cookie="geheimer-cookie-wert-42",
                               betrachter_token="geheimer-token-wert-43", arbeitsordner=ordner)


class _Lauf:
    """Ersetzt den Container: prüft Befehl und Dateien vor dem Lauf, schreibt Ergebnisse."""

    def __init__(self, *, bericht: dict[str, Any] | None = None,
                 anfragen: dict[str, Any] | None = None, exit_code: int = 0) -> None:
        self.befehl: list[str] = []
        self.zustand: dict[str, Any] = {}
        self.anfragen = _anfragen("anfragen_sauber") if anfragen is None else anfragen
        self.bericht = ({"stats": {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 0}}
                        if bericht is None else bericht)
        self.exit_code = exit_code

    def __call__(self, befehl: list[str]) -> subprocess.CompletedProcess[str]:
        self.befehl = befehl
        mounts = [befehl[i + 1] for i, t in enumerate(befehl) if t == "-v"]
        zustand = next(m.split(":")[0] for m in mounts if m.endswith("/zustand.json:ro"))
        self.zustand = {"modus": stat.S_IMODE(os.stat(zustand).st_mode),
                        "inhalt": json.loads(Path(zustand).read_text("utf-8")), "pfad": zustand}
        ausgabe = Path(next(m.split(":")[0] for m in mounts if m.endswith(":/arbeit:rw")))
        if self.anfragen:
            (ausgabe / "egress-anfragen.json").write_text(json.dumps(self.anfragen),
                                                          encoding="utf-8")
        if self.bericht:
            (ausgabe / "egress-bericht.json").write_text(json.dumps(self.bericht),
                                                         encoding="utf-8")
        return subprocess.CompletedProcess(befehl, self.exit_code, "", "Fehlerauszug")


def _browser(tmp_path: Path, lauf: _Lauf, **kw: Any) -> dict[str, Any]:
    arbeit = tmp_path / "browser-arbeit"
    arbeit.mkdir(exist_ok=True)
    return egress.browser_lauf(_inst(tmp_path), _werkzeuge(), WURZEL, arbeit, laeufer=lauf, **kw)


def test_browser_lauf_haertung_und_anmeldung(tmp_path: Path) -> None:
    lauf = _Lauf()
    ergebnis = _browser(tmp_path, lauf)
    b = lauf.befehl
    assert b[:2] == ["docker", "run"]
    for paar in (["--network", "host"], ["--pull", "never"], ["--cap-drop", "ALL"],
                 ["--security-opt", "no-new-privileges"]):
        assert any(b[i:i + 2] == paar for i in range(len(b) - 1)), paar
    assert "--read-only" in b and "--user" in b
    tmpfs = [b[i + 1] for i, t in enumerate(b) if t == "--tmpfs"]
    assert any(t.startswith("/dev/shm:") for t in tmpfs)
    mounts = [b[i + 1] for i, t in enumerate(b) if t == "-v"]
    assert f"{WURZEL / 'web'}:/web:ro" in mounts
    # Zugangsdaten nie in argv oder Umgebung des Containers, nur als Datei (0600, nur lesend).
    assert not any("geheimer-cookie-wert-42" in t for t in b)
    assert lauf.zustand["modus"] == 0o600
    cookie = lauf.zustand["inhalt"]["cookies"][0]
    assert (cookie["name"], cookie["value"]) == (fuzzing.COOKIE_NAME, "geheimer-cookie-wert-42")
    assert cookie["domain"] == "localhost" and cookie["path"] == "/" and cookie["secure"] is True
    assert cookie["httpOnly"] is True
    assert not Path(lauf.zustand["pfad"]).exists()  # nach dem Lauf entfernt
    env = {t.split("=", 1)[0]: t.split("=", 1)[1]
           for i, t in enumerate(b) if i and b[i - 1] == "-e"}
    assert env["SCANVIEW_EGRESS_URL"] == fuzzing.URL
    assert env["SCANVIEW_EGRESS_PROJEKT"] == "egress-test"
    assert env["SCANVIEW_EGRESS_STANDPUNKT"] == "S001"
    assert env["SCANVIEW_EGRESS_AUSGABE"] == "/arbeit"
    assert ergebnis["anfragen"] == _anfragen("anfragen_sauber")["anfragen"]
    assert ergebnis["testlauf"] == {"erwartet": 1, "unerwartet": 0, "uebersprungen": 0}


def test_browser_lauf_ruft_playwright_direkt_auf_nicht_ueber_npx(tmp_path: Path) -> None:
    """`npx` ohne Arbeitsverzeichnis fiele auf die Registry zurück — echter Netzverkehr."""
    lauf = _Lauf()
    _browser(tmp_path, lauf)
    b = lauf.befehl
    assert "npx" not in b
    i = b.index("node")
    assert b[i + 1] == "/web/node_modules/@playwright/test/cli.js"
    assert b[i + 2:] == ["test", "-c", "/web/playwright.egress.config.ts"]


def test_browser_lauf_ohne_anfragendatei_ist_fehler(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        _browser(tmp_path, _Lauf(anfragen={}, exit_code=1))
    assert e.value.regel == "egress-browser" and "egress-anfragen.json" in e.value.text
    assert "Fehlerauszug" in e.value.text


def test_browser_lauf_ohne_bericht_ist_fehler(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        _browser(tmp_path, _Lauf(bericht={}))
    assert e.value.regel == "egress-browser" and "egress-bericht.json" in e.value.text


@pytest.mark.parametrize("stats", [
    {"expected": 0, "unexpected": 0, "skipped": 1, "flaky": 0},   # Umgebung fehlte → übersprungen
    {"expected": 0, "unexpected": 1, "skipped": 0, "flaky": 0},
    {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 1},
    {"expected": 0, "unexpected": 0, "skipped": 0, "flaky": 0},   # nichts gelaufen
    {"expected": 1, "unexpected": 0, "skipped": 1, "flaky": 0},
])
def test_browser_lauf_nur_aus_dem_bericht_nie_aus_dem_exit_code(tmp_path: Path,
                                                                stats: dict[str, int]) -> None:
    with pytest.raises(NachweisFehler) as e:
        _browser(tmp_path, _Lauf(bericht={"stats": stats}, exit_code=0))
    assert e.value.regel == "egress-browser"


def test_browser_lauf_genau_ein_test(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        _browser(tmp_path, _Lauf(bericht={"stats": {"expected": 2, "unexpected": 0,
                                                    "skipped": 0, "flaky": 0}}))
    assert e.value.regel == "egress-browser"


def test_browser_lauf_exit_code_allein_genuegt_nicht(tmp_path: Path) -> None:
    """Ein Fehl-Exit mit sauberem Bericht ist kein Erfolg (und umgekehrt zählt der Bericht)."""
    with pytest.raises(NachweisFehler) as e:
        _browser(tmp_path, _Lauf(exit_code=1))
    assert e.value.regel == "egress-browser"


def test_browser_lauf_zustandsdatei_wird_auch_bei_fehler_entfernt(tmp_path: Path) -> None:
    lauf = _Lauf(anfragen={}, exit_code=1)
    with pytest.raises(NachweisFehler):
        _browser(tmp_path, lauf)
    assert not Path(lauf.zustand["pfad"]).exists()


def test_browser_lauf_standpunkt_aus_den_stationen(tmp_path: Path) -> None:
    lauf = _Lauf()
    inst = _inst(tmp_path)
    (inst.arbeitsordner / "data" / "egress-test" / "stations.json").write_text(
        json.dumps({"stations": [{"id": "S007"}]}), encoding="utf-8")
    arbeit = tmp_path / "a"
    arbeit.mkdir()
    egress.browser_lauf(inst, _werkzeuge(), WURZEL, arbeit, laeufer=lauf)
    assert "SCANVIEW_EGRESS_STANDPUNKT=S007" in lauf.befehl


def test_browser_lauf_ohne_standpunkt_ist_fehler(tmp_path: Path) -> None:
    inst = _inst(tmp_path)
    (inst.arbeitsordner / "data" / "egress-test" / "stations.json").write_text(
        json.dumps({"stations": []}), encoding="utf-8")
    arbeit = tmp_path / "a"
    arbeit.mkdir()
    with pytest.raises(NachweisFehler) as e:
        egress.browser_lauf(inst, _werkzeuge(), WURZEL, arbeit, laeufer=_Lauf())
    assert e.value.regel == "egress-browser" and "Standpunkt" in e.value.text


def test_browser_lauf_ohne_playwright_in_werkzeuge(tmp_path: Path) -> None:
    arbeit = tmp_path / "a"
    arbeit.mkdir()
    with pytest.raises(NachweisFehler) as e:
        egress.browser_lauf(_inst(tmp_path), {}, WURZEL, arbeit, laeufer=_Lauf())
    assert e.value.regel == "egress-werkzeug"


def test_browser_lauf_netzschritt_steht_im_ergebnis(tmp_path: Path) -> None:
    schritte: list[Any] = []
    _browser(tmp_path, _Lauf(), netzschritte=schritte)
    assert len(schritte) == 1 and "host" in schritte[0].befehl
    assert schritte[0].cwd == "web"


# --- Sitzungsprobe -------------------------------------------------------------------------------

def test_sitzungsprobe_gueltig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gesehen: list[tuple[str, dict[str, str]]] = []

    def http(methode: str, url: str, kopf: dict[str, str], daten: bytes | None = None) -> Any:
        gesehen.append((url, kopf))
        return 200, {}, b"[]"

    monkeypatch.setattr(egress.fuzzing, "_http", http)
    egress.sitzungsprobe(_inst(tmp_path))
    assert gesehen[0][0].endswith("/api/v1/projekte")
    assert gesehen[0][1]["Cookie"] == f"{fuzzing.COOKIE_NAME}=geheimer-cookie-wert-42"


def test_sitzungsprobe_verloren(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(egress.fuzzing, "_http", lambda *a, **k: (401, {}, b""))
    with pytest.raises(NachweisFehler) as e:
        egress.sitzungsprobe(_inst(tmp_path))
    assert e.value.regel == "egress-sitzung"
    assert "geheimer-cookie-wert-42" not in e.value.text


# --- Gesamtlauf ----------------------------------------------------------------------------------

def _worktree_fuer_pruefen(tmp_path: Path, *, ausnahmen: str = "") -> Path:
    wt = tmp_path / "wt"
    shutil.copytree(FIXTURES / "bundle_sauber", wt / "web" / "dist")
    (wt / "docs" / "compliance").mkdir(parents=True)
    (wt / "docs" / "compliance" / "egress-ausnahmen.toml").write_text(ausnahmen, encoding="utf-8")
    shutil.copytree(FIXTURES / "paket", wt / egress.PAKET)
    return wt


class _Instanz:
    """Kontextmanager statt der echten Testinstanz."""

    def __init__(self, tmp_path: Path) -> None:
        self.inst = _inst(tmp_path)
        self.gestoppt = False
        self.paket: Path | None = None

    def __call__(self, worktree: Path, arbeitsordner: Path, werkzeuge: Any, *,
                 paket: Path | None = None) -> fuzzing.Testinstanz:
        self.paket = paket
        self.inst._stopper.append(lambda: setattr(self, "gestoppt", True))
        return self.inst


def _pruefen(tmp_path: Path, wt: Path, *, anfragen: str = "anfragen_sauber",
             probe: Any = None) -> tuple[egress.Ergebnis, _Instanz]:
    instanz = _Instanz(tmp_path)

    def browser(inst: Any, werkzeuge: Any, worktree: Path, arbeit: Path, **kw: Any
                ) -> dict[str, Any]:
        return {**_anfragen(anfragen), "testlauf": {"erwartet": 1, "unerwartet": 0,
                                                    "uebersprungen": 0}}

    (tmp_path / "berichte").mkdir(exist_ok=True)
    e = egress.pruefen(wt, _werkzeuge(), tmp_path / "berichte", tmp_path / "arbeit",
                       instanz=instanz, browser=browser, sitzung=probe or (lambda inst: None))
    return e, instanz


def test_pruefen_sauber(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path)
    e, instanz = _pruefen(tmp_path, wt)
    assert e.blockiert == []
    assert instanz.paket == wt / egress.PAKET and instanz.gestoppt
    assert e.bericht["statisch"]["funde"] == 0
    assert e.bericht["browser"]["anfragen"] == len(_anfragen("anfragen_sauber")["anfragen"])
    assert e.bericht["browser"]["kontrollanfragen_erfasst"] is True
    assert e.bericht["sitzungsprobe"] == "gültig"
    assert e.bericht["werkzeug"]["version"] == "1.63.0"
    statisch = json.loads((tmp_path / "berichte" / "egress-statisch.json").read_text("utf-8"))
    browser = json.loads((tmp_path / "berichte" / "egress-browser.json").read_text("utf-8"))
    assert statisch["funde"] == [] and statisch["blockierend"] == []
    assert browser["anfragen"] and browser["blockierend"] == []


def test_pruefen_fremde_anfrage_und_bundle_url_blockieren_beide(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path)
    (wt / "web" / "dist" / "assets" / "karte.js").write_text(
        "'https://tiles.example.org/x.png'", encoding="utf-8")
    e, _ = _pruefen(tmp_path, wt, anfragen="anfragen_fremd")
    assert len(e.blockiert) == 3
    assert any("tiles.example.org" in b for b in e.blockiert)
    assert any("cdn.example.org" in b for b in e.blockiert)


def test_pruefen_ungenutzte_ausnahme_ist_nur_ein_hinweis(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path, ausnahmen=_eintrag(url="https://alt.example.org/x"))
    e, _ = _pruefen(tmp_path, wt)
    assert e.blockiert == []
    assert e.bericht["hinweise"] == ["Ausnahme ohne Fund: https://alt.example.org/x"]


def test_pruefen_erfassungsfehler_bricht_ab_und_stoppt_die_instanz(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path)
    instanz = _Instanz(tmp_path)

    def browser(*a: Any, **kw: Any) -> dict[str, Any]:
        return _anfragen("anfragen_ohne_kontrolle")

    (tmp_path / "berichte").mkdir()
    with pytest.raises(NachweisFehler) as e:
        egress.pruefen(wt, _werkzeuge(), tmp_path / "berichte", tmp_path / "arbeit",
                       instanz=instanz, browser=browser, sitzung=lambda inst: None)
    assert e.value.regel == "egress-erfassung" and instanz.gestoppt
    assert not (tmp_path / "berichte" / "egress-browser.json").exists()


def test_pruefen_sitzung_verloren_bricht_ab(tmp_path: Path) -> None:
    def verloren(inst: Any) -> None:
        raise NachweisFehler("egress-sitzung", "Anmeldung während des Laufs verloren")

    with pytest.raises(NachweisFehler) as e:
        _pruefen(tmp_path, _worktree_fuer_pruefen(tmp_path), probe=verloren)
    assert e.value.regel == "egress-sitzung"


def test_pruefen_berichte_ohne_anmeldewerte(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path)
    instanz = _Instanz(tmp_path)

    def browser(*a: Any, **kw: Any) -> dict[str, Any]:
        d = _anfragen("anfragen_sauber")
        d["anfragen"].append({"url": f"{ORIGIN}/x?c={instanz.inst.admin_cookie}",
                              "quelle": "request", "seite": "portal"})
        return d

    (tmp_path / "berichte").mkdir()
    e = egress.pruefen(wt, _werkzeuge(), tmp_path / "berichte", tmp_path / "arbeit",
                       instanz=instanz, browser=browser, sitzung=lambda inst: None)
    text = (tmp_path / "berichte" / "egress-browser.json").read_text("utf-8")
    assert "geheimer-cookie-wert-42" not in text and "[entfernt]" in text
    assert e.blockiert == []


def test_pruefen_ohne_paket_fixture_ist_fehler(tmp_path: Path) -> None:
    wt = _worktree_fuer_pruefen(tmp_path)
    shutil.rmtree(wt / egress.PAKET)
    with pytest.raises(NachweisFehler) as e:
        _pruefen(tmp_path, wt)
    assert e.value.regel == "egress-paket"


# --- Echtes Bundle -------------------------------------------------------------------------------

def test_echtes_paket_ist_vollstaendig_und_synthetisch() -> None:
    paket = WURZEL / egress.PAKET
    projekt = json.loads((paket / "project.json").read_text("utf-8"))
    assert projekt["projectId"] == "egress-test"
    assert projekt["layers"][0]["pointCount"] == 2000
    stationen = json.loads((paket / "stations.json").read_text("utf-8"))["stations"]
    assert stationen and stationen[0]["id"] == "S001"
    for datei in ("cloud.copc.laz", "topview.png"):
        assert (paket / datei).is_file()
    assert len(list((paket / "panos" / "S001").glob("*/64.jpg"))) == 6
    # Keine Orts- oder Rechnerangaben im Paket.
    text = "".join(p.read_text("utf-8") for p in paket.rglob("*.json"))
    assert not re.search(r"/(?:home|root|Users)/|/tmp/|localhost", text)
    assert projekt["frame"]["crs"]["wkt"] is None


@pytest.mark.skipif(not (WURZEL / "veroeffentlichung" / "freigaben.txt").exists(),
                    reason="freigaben.txt liegt nur im privaten Repo (nicht im Export)")
def test_echte_freigaben_decken_jede_binaerdatei_des_pakets() -> None:
    import hashlib
    paket = WURZEL / egress.PAKET
    freigaben = (WURZEL / "veroeffentlichung" / "freigaben.txt").read_text("utf-8")
    for datei in sorted(p for p in paket.rglob("*") if p.is_file()
                        and p.suffix in (".laz", ".jpg", ".png")):
        sha = hashlib.sha256(datei.read_bytes()).hexdigest()
        assert f"{sha}  {datei.relative_to(WURZEL).as_posix()}  #" in freigaben, datei


# --- CLI `pruefen egress`  -----------------------------------------------------------------------

@pytest.mark.parametrize(("blockiert", "code"),
                         [([], 0), (["browser viewer: request https://x.example.org/ — fremd"], 1)])
def test_cli_pruefen_egress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            capsys: pytest.CaptureFixture[str], blockiert: list[str],
                            code: int) -> None:
    from scanview_nachweise import cli
    gesehen: dict[str, Path] = {}

    def pruefen(worktree: Path, werkzeuge: Any, berichte: Path,
                arbeitsordner: Path) -> egress.Ergebnis:
        gesehen.update(worktree=worktree, berichte=berichte, arbeitsordner=arbeitsordner)
        bericht = {"statisch": {"dist_dateien": 5, "funde": 21, "ausgenommen": 21,
                                "blockierend": []},
                   "browser": {"anfragen": 71, "seiten": list(egress.ERWARTETE_SEITEN),
                               "punkte": 2000, "blockierend": blockiert},
                   "hinweise": ["Ausnahme ohne Fund: alt"], "dauer_sekunden": 9,
                   "blockierend": blockiert}
        return egress.Ergebnis(bericht, blockiert, [])

    monkeypatch.setattr(egress, "pruefen", pruefen)
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    assert cli.main(["pruefen", "egress", "--wurzel", str(WURZEL), "--arbeitsordner",
                     str(arbeit)]) == code
    aus = capsys.readouterr().out
    assert gesehen == {"worktree": WURZEL, "berichte": arbeit / "berichte",
                       "arbeitsordner": arbeit}
    assert "statisch: 5 Dateien, 21 absolute URLs (21 ausgenommen)" in aus
    assert "Browser: 71 erfasste Anfragen auf 6 Seiten, 2000 Punkte geladen" in aus
    assert "HINWEIS Ausnahme ohne Fund: alt" in aus
    assert json.loads((arbeit / "egress.json").read_text("utf-8"))["blockierend"] == blockiert
    assert ("BLOCKIERT browser viewer" in aus) is bool(blockiert)


def test_cli_pruefen_egress_ueberschreibt_nichts(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    from scanview_nachweise import cli
    (tmp_path / "egress.json").write_text("{}", encoding="utf-8")
    assert cli.main(["pruefen", "egress", "--wurzel", str(WURZEL), "--arbeitsordner",
                     str(tmp_path)]) == 2
    assert "egress-ausgabe" in capsys.readouterr().err
    assert (tmp_path / "egress.json").read_text("utf-8") == "{}"

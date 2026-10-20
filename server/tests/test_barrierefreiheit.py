"""Erklärung zur Barrierefreiheit und Feedback-Weg je Instanz: Einstellungen
aus der Umgebung, strenge Prüfung beim Start (SystemExit), öffentlicher Endpunkt ohne Anmeldung.
Die Vorlage liegt genau einmal im Paket; diese Tests halten sie gültig."""

from __future__ import annotations

import dataclasses
import logging
import os
import re
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest
from conftest import ORIGIN
from fastapi.testclient import TestClient

from scanview_server import barrierefreiheit as bf
from scanview_server.config import Settings
from scanview_server.web import create_app

KONTAKT = "mailto:barrierefreiheit@stelle.example.org"
FUELLUNG = {
    "@STELLE@": "Beispiel-Hochschule",
    "@ADRESSE@": "https://scanview.stelle.example.org",
    "@DATUM@": "2026-10-01",
    "@DURCHSETZUNG_TEXT@": "Wenn Sie innerhalb von sechs Wochen keine zufriedenstellende Antwort "
                           "erhalten, können Sie sich an die Schlichtungsstelle wenden.",
    "@SCHLICHTUNGSSTELLE@": "Ombudsstelle Beispielland",
    "@SCHLICHTUNGSSTELLE_URL@": "https://ombudsstelle.example.org",
}


def vorlage_text() -> str:
    return (resources.files("scanview_server") / bf.VORLAGE).read_text(encoding="utf-8")


def schreibe(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "barrierefreiheit.toml"
    p.write_text(text, encoding="utf-8")
    return p


def gefuellt(text: str | None = None) -> str:
    t = vorlage_text() if text is None else text
    for alt, neu in FUELLUNG.items():
        t = t.replace(alt, neu)
    return t


def _umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": ORIGIN,
                 "SCANVIEW_RP_ID": "x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SCANVIEW_BARRIEREFREIHEIT_KONTAKT", raising=False)
    monkeypatch.delenv("SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG", raising=False)


# --- Vorlage ------------------------------------------------------------------------------------

def test_vorlage_platzhalter_sind_genau_die_bekannten() -> None:
    assert set(re.findall(r"@[A-Z_]+@", vorlage_text())) == set(FUELLUNG)


def test_ausgefuellte_vorlage_ist_gueltig(tmp_path: Path) -> None:
    e = bf.lade_erklaerung(schreibe(tmp_path, gefuellt()))
    assert e["stelle"] == "Beispiel-Hochschule"
    assert e["stand"] == "teilweise"
    assert e["erstellt"] == "2026-10-01"
    assert e["nicht_barrierefrei"] and all(set(x) == {"inhalt", "grund", "alternative"}
                                           for x in e["nicht_barrierefrei"])
    assert e["durchsetzung"]["url"] == "https://ombudsstelle.example.org"


def test_produkt_einschraenkungen_aus_der_vorlage() -> None:
    liste = bf.produkt_einschraenkungen()
    assert liste and all(x["inhalt"] and x["grund"] and x["alternative"] for x in liste)
    assert not any("@" in v for x in liste for v in x.values())


# --- Erklärung: fail-closed ---------------------------------------------------------------------

def test_unausgefuellte_vorlage_nennt_den_platzhalter(tmp_path: Path) -> None:
    with pytest.raises(bf.KonfigFehler, match="@STELLE@"):
        bf.lade_erklaerung(schreibe(tmp_path, vorlage_text()))


@pytest.mark.parametrize(("alt", "neu", "muster"), [
    # „stand“/„methode“/„schema“ mit Anführungszeichen statt bloßem Feldnamen: pytest benennt
    # tmp_path nach der (gekürzten) Test-ID, die hier zufällig den Feldnamen selbst enthält
    # (er steht ja in `alt`/`neu`) — ein bloßes match=feld träfe dann auch über den Pfad, nicht
    # über die eigentliche Fehlermeldung.
    ('stand = "teilweise"', 'stand = "fast"', 'Feld „stand“'),
    ('methode = "Selbstbewertung"', 'methode = "Bauchgefühl"', 'Feld „methode“'),
    ('schema = 1', 'schema = 2', 'Feld „schema“'),
    ('"2026-10-01"', '"2026-13-01"', "erstellt"),
    ('"https://ombudsstelle.example.org"', '"http://ombudsstelle.example.org"', "url"),
    ('"https://scanview.stelle.example.org"', '"javascript:alert(1)"', "geltungsbereich"),
])
def test_ungueltige_felder(tmp_path: Path, alt: str, neu: str, muster: str) -> None:
    text = gefuellt()
    assert alt in text
    with pytest.raises(bf.KonfigFehler, match=muster):
        bf.lade_erklaerung(schreibe(tmp_path, text.replace(alt, neu, 1)))


def test_schema_muss_echte_ganzzahl_sein(tmp_path: Path) -> None:
    # In Python ist True == 1; ein TOML-Boolean darf deshalb nicht als Schema 1 durchgehen.
    text = gefuellt()
    assert "schema = 1" in text
    with pytest.raises(bf.KonfigFehler, match="Feld „schema“"):
        bf.lade_erklaerung(schreibe(tmp_path, text.replace("schema = 1", "schema = true", 1)))


def test_erstellt_muss_bindestrich_form_haben(tmp_path: Path) -> None:
    # date.fromisoformat() akzeptiert auf Python 3.11 zusätzlich zum dokumentierten JJJJ-MM-TT
    # auch das Kompaktformat ohne Trennzeichen ("20261001") und gibt den rohen String unverändert
    # zurück — der zugesagte Vertrag „erstellt als 'JJJJ-MM-TT'" würde sonst stillschweigend
    # verletzt.
    text = gefuellt().replace('"2026-10-01"', '"20261001"', 1)
    with pytest.raises(bf.KonfigFehler, match='Feld „erstellt“'):
        bf.lade_erklaerung(schreibe(tmp_path, text))


def test_unbekannter_schluessel(tmp_path: Path) -> None:
    # match="unbekannt" wäre hier immer erfüllt, auch bei falscher Meldung: tmp_path heißt nach
    # dem Testnamen selbst "test_unbekannter_schluessel…". "unbekannte Schlüssel" (mit ü,
    # Leerzeichen) kommt in diesem Pfad nicht vor.
    # Der Fremdschlüssel landet hier am Dateiende in der zuletzt geöffneten Tabelle
    # [durchsetzung] — geprüft wird also _nur(ds, _DURCHSETZUNG, ...), nicht die Prüfung der
    # obersten Ebene (siehe test_unbekannter_schluessel_auf_oberster_ebene).
    with pytest.raises(bf.KonfigFehler, match="unbekannte Schlüssel"):
        bf.lade_erklaerung(schreibe(tmp_path, gefuellt() + '\nzusatz = "x"\n'))


def test_unbekannter_schluessel_auf_oberster_ebene(tmp_path: Path) -> None:
    # Ergänzt test_unbekannter_schluessel: ein Fremdschlüssel vor der ersten Tabelle landet in der
    # obersten Ebene selbst und muss dort von _nur(d, _OBEN, wo) abgelehnt werden, nicht erst in
    # einer Untertabelle.
    text = gefuellt().replace("schema = 1\n", 'schema = 1\nzusatz_oben = "x"\n', 1)
    with pytest.raises(bf.KonfigFehler, match="unbekannte Schlüssel"):
        bf.lade_erklaerung(schreibe(tmp_path, text))


def test_zu_langer_text(tmp_path: Path) -> None:
    text = gefuellt().replace('stelle = "Beispiel-Hochschule"',
                              f'stelle = "{"x" * (bf.MAX_TEXT + 1)}"', 1)
    with pytest.raises(bf.KonfigFehler, match="stelle"):
        bf.lade_erklaerung(schreibe(tmp_path, text))


def test_zu_grosse_datei(tmp_path: Path) -> None:
    p = tmp_path / "gross.toml"
    p.write_bytes(b"#" * (bf.MAX_BYTES + 1))
    with pytest.raises(bf.KonfigFehler, match="KiB"):
        bf.lade_erklaerung(p)


def test_datei_genau_max_bytes_ist_kein_groessenfehler(tmp_path: Path) -> None:
    # Grenzwert: eine Datei von genau MAX_BYTES ist für _lies() nicht "zu groß" — f.read(MAX_BYTES
    # + 1) liefert dann genau MAX_BYTES Bytes zurück, len(roh) > MAX_BYTES ist False. Der Inhalt
    # ist hier absichtlich nur ein Kommentar (kein gültiges Erklärungs-TOML); die Ablehnung muss aus
    # einer anderen Prüfung kommen (hier: erstes fehlendes Pflichtfeld "durchsetzung" in _OBEN
    # sortiert), nicht aus der Größenprüfung — die positive match-Zusicherung stellt sicher, dass
    # die Datei wirklich gelesen, dekodiert und geparst wurde, statt nur zufällig an irgendeiner
    # anderen Stelle (z. B. einer kaputten fstat-Prüfung) fehlzuschlagen.
    p = tmp_path / "exakt.toml"
    p.write_bytes(b"#" * bf.MAX_BYTES)
    with pytest.raises(bf.KonfigFehler, match='Feld „durchsetzung“ fehlt') as exc:
        bf.lade_erklaerung(p)
    assert "KiB" not in str(exc.value)


def test_kein_utf8(tmp_path: Path) -> None:
    p = tmp_path / "latin1.toml"
    p.write_bytes(gefuellt().encode("utf-8") + b"\n# \xe4\n")
    with pytest.raises(bf.KonfigFehler, match="UTF-8"):
        bf.lade_erklaerung(p)


def test_fehlende_datei(tmp_path: Path) -> None:
    with pytest.raises(bf.KonfigFehler, match="nicht lesbar"):
        bf.lade_erklaerung(tmp_path / "gibt-es-nicht.toml")


def test_keine_regulaere_datei(tmp_path: Path) -> None:
    # SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG kann auf eine Spezialdatei zeigen (hier: FIFO ohne
    # Schreiber). _lies() muss das per fstat erkennen, BEVOR sie liest — ein blockierendes open()
    # auf einer leeren FIFO würde den Start sonst für immer hängen lassen.
    p = tmp_path / "fifo"
    os.mkfifo(p)
    with pytest.raises(bf.KonfigFehler, match="keine reguläre Datei"):
        bf.lade_erklaerung(p)


# --- Kontakt ------------------------------------------------------------------------------------

def test_kontakt_gueltig() -> None:
    assert bf.pruefe_kontakt(KONTAKT) == bf.Kontakt("mailto", KONTAKT)
    url = "https://stelle.example.org/barriere-melden"
    assert bf.pruefe_kontakt(url) == bf.Kontakt("formular", url)


@pytest.mark.parametrize("falsch", [
    "mailto:a@b.example.org?subject=x",       # eigener Betreff/Kopfzeilen nicht erlaubt
    "mailto:a@b.example.org&cc=x@y.example",  # Zusatzempfänger
    "mailto:a%40b@c.example.org",             # kodierte Zeichen
    "mailto:a@b.example.org\nBcc: x@y.example",
    "mailto:a@b",                              # ohne Punkt in der Domain
    "mailto:",
    "a@b.example.org",                         # ohne Schema
    "http://stelle.example.org/formular",      # kein HTTPS
    "javascript:alert(1)",
    "https://",
])
def test_kontakt_ungueltig(falsch: str) -> None:
    with pytest.raises(bf.KonfigFehler):
        bf.pruefe_kontakt(falsch)


def test_kontakt_zu_lang() -> None:
    # Formal gültige https://-Adresse, aber überlang: pruefe_kontakt() muss dieselbe
    # Längenbegrenzung durchsetzen wie alle Erklärungsfelder über _text().
    lang = "https://stelle.example.org/" + "x" * bf.MAX_TEXT
    with pytest.raises(bf.KonfigFehler, match="Zeichen"):
        bf.pruefe_kontakt(lang)


# --- Umgebung → Settings ------------------------------------------------------------------------

def test_ohne_einstellung_leer_mit_warnung(monkeypatch: pytest.MonkeyPatch,
                                            caplog: pytest.LogCaptureFixture) -> None:
    _umgebung(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="scanview.barrierefreiheit"):
        s = Settings.from_env()
    assert s.barrierefreiheit == bf.Barrierefreiheit()
    text = caplog.text
    assert "SCANVIEW_BARRIEREFREIHEIT_KONTAKT" in text
    assert "SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG" in text


def test_mit_einstellung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _umgebung(monkeypatch)
    monkeypatch.setenv("SCANVIEW_BARRIEREFREIHEIT_KONTAKT", f" {KONTAKT} ")
    monkeypatch.setenv("SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG", str(schreibe(tmp_path, gefuellt())))
    b = Settings.from_env().barrierefreiheit
    assert b.kontakt == bf.Kontakt("mailto", KONTAKT)
    assert b.erklaerung is not None and b.erklaerung["stelle"] == "Beispiel-Hochschule"


@pytest.mark.parametrize("variable", ["SCANVIEW_BARRIEREFREIHEIT_KONTAKT",
                                      "SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG"])
def test_ungueltige_einstellung_bricht_start_ab(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                variable: str) -> None:
    _umgebung(monkeypatch)
    wert = "javascript:alert(1)" if variable.endswith("KONTAKT") else str(
        schreibe(tmp_path, vorlage_text()))  # unausgefüllte Vorlage
    monkeypatch.setenv(variable, wert)
    with pytest.raises(SystemExit):
        Settings.from_env()


# --- Endpunkt -----------------------------------------------------------------------------------

def test_endpunkt_ohne_anmeldung_ohne_einstellung(client: TestClient) -> None:
    r = client.get("/api/barrierefreiheit")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    d = r.json()
    assert d["dienst"] == "scanview"
    assert d["kontakt"] is None
    assert d["erklaerung"] is None
    assert d["produkt_einschraenkungen"] == bf.produkt_einschraenkungen()


@pytest.fixture
def client_mit(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    b = bf.Barrierefreiheit(kontakt=bf.pruefe_kontakt(KONTAKT),
                            erklaerung=bf.lade_erklaerung(schreibe(tmp_path, gefuellt())))
    app = create_app(dataclasses.replace(settings, barrierefreiheit=b))
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def test_endpunkt_mit_einstellung(client_mit: TestClient) -> None:
    d = client_mit.get("/api/barrierefreiheit").json()
    assert d["kontakt"] == {"art": "mailto", "ziel": KONTAKT}
    assert d["erklaerung"]["stelle"] == "Beispiel-Hochschule"


def test_endpunkt_ignoriert_ungueltiges_cookie_und_token(client_mit: TestClient) -> None:
    r = client_mit.get("/api/barrierefreiheit", cookies={"__Host-scanview": "kaputt"},
                       headers={"Authorization": "Bearer kaputt"})
    assert r.status_code == 200


def test_endpunkt_nur_get(client: TestClient) -> None:
    assert client.post("/api/barrierefreiheit").status_code in (403, 405)

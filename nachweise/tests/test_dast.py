"""DAST: ZAP-Passivscan gegen die Testinstanz L — Auswertung des Berichts (Risikostufen, Gate,
Ausnahmen), Abdeckung, Bereinigung von Zugangsdaten, gehärteter Containerlauf (mit Attrappen für
Container und HTTP), Verweigerung für S und Nachweis im Archiv."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import signierhilfe

from scanview_nachweise import cli, dast
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import Befund
from scanview_nachweise.werkzeuge import Werkzeug, lade_werkzeuge

WURZEL = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "dast"
#: Synthetische Adresse von L für die Tests; die echte steht nur als SHA-256 im Code.
ZIEL = "https://l.example.invalid"
COOKIE = "k7Hq2Zr9-Vt4Lm8Xw3Pd6Nb1_Yc5Sa0Je+Gf/Ui="
HEUTE = date(2026, 10, 1)
DIGEST = "sha256:" + "c" * 64
WERKZEUGE = {"zap": Werkzeug("zap", f"zaproxy/zap-stable@{DIGEST}", "2.17.0", None)}
OPENAPI = json.dumps({"paths": {
    "/api/v1/version": {"get": {}}, "/api/v1/projekte": {"get": {}, "post": {}},
    "/api/v1/projekte/{pid}": {"get": {}}, "/api/v1/konto": {"get": {}},
    "/api/v1/admin/nutzer": {"get": {}}, "/api/v1/konto/tokens": {"get": {}},
    "/api/v1/statistik/{pid}": {"post": {}}, "/api/v1/projekte/{pid}/export.csv": {"get": {}},
    "/api/v1/abmelden-test": {"post": {}}}}).encode()


@pytest.fixture(autouse=True)
def _l_ist_testadresse(monkeypatch):
    monkeypatch.setattr(dast, "L_URSPRUNG_SHA256", hashlib.sha256(ZIEL.encode()).hexdigest())


def _bericht() -> dict[str, Any]:
    daten: dict[str, Any] = json.loads((FIXTURES / "bericht.json").read_text("utf-8"))
    return daten


def _befund(pluginid: str, muster: str = "", **abweichung: Any) -> Befund:
    felder: dict[str, Any] = dict(
        id=dast.befund_id(pluginid, muster), quelle="DAST", titel="Test", schwere="mittel",
        kategorie="haertung", status="offen", staende=["L"], fix_commit=None, retest=None,
        akzeptanz_begruendung=None, frist=None, entdeckt=date(2026, 10, 1))
    felder.update(abweichung)
    return Befund(**felder)


def _alarm(pluginid: str = "10038", schwere: str = "mittel",
           pfade: tuple[str, ...] = ("",)) -> dast.Alarm:
    return dast.Alarm(pluginid=pluginid, name="x", schwere=schwere,  # type: ignore[arg-type]
                      pfade=pfade)


def _ausnahme(pluginid: str = "10021", pfade: str = '["api/v1/**", "assets/*"]',
              schwere: str = "niedrig", begruendung: str = "x" * 25,
              datum: str = "2026-10-01") -> str:
    return (f'[[ausnahme]]\npluginid = "{pluginid}"\npfade = {pfade}\nschwere = "{schwere}"\n'
            f'begruendung = "{begruendung}"\ndatum = {datum}\n')


def _ausnahmen_datei(tmp_path: Path, text: str) -> Path:
    pfad = tmp_path / "dast-ausnahmen.toml"
    pfad.write_text(text, encoding="utf-8")
    return pfad


# --- Risikostufen und Auswertung ------------------------------------------------------------------

@pytest.mark.parametrize(("roh", "soll"), [
    ("3", "hoch"), ("2", "mittel"), ("1", "niedrig"), ("0", "info"), (3, "hoch"), (0, "info"),
    ("4", "hoch"), ("-1", "hoch"), ("", "hoch"), (None, "hoch"), ("hoch", "hoch"),
    (2.0, "hoch"), (True, "hoch")])
def test_risikostufen(roh, soll):
    assert dast.schwere(roh) == soll


def test_bericht_auswerten():
    alarme = {a.pluginid: a for a in dast.werte_zap_aus(_bericht())}
    assert set(alarme) == {"10038", "10021", "10049"}
    assert alarme["10038"].schwere == "mittel"
    assert alarme["10021"].schwere == "niedrig"
    assert alarme["10049"].schwere == "info"
    # Nur der Pfad zählt: ohne Ursprung, ohne Abfrage, ohne führenden Schrägstrich.
    assert alarme["10038"].pfade == ("",)
    assert alarme["10021"].pfade == ("api/v1/projekte", "assets/app.js")


def test_bericht_unbekannte_risikostufe_ist_hoch():
    b = _bericht()
    b["site"][0]["alerts"][2]["riskcode"] = "9"
    assert {a.pluginid: a.schwere for a in dast.werte_zap_aus(b)}["10049"] == "hoch"


@pytest.mark.parametrize("kaputt", [
    lambda b: b.pop("site"), lambda b: b.update(site={}), lambda b: b["site"].append(3),
    lambda b: b["site"][0].pop("alerts"), lambda b: b["site"][0]["alerts"].append("x"),
    lambda b: b["site"][0]["alerts"][0].pop("pluginid"),
    lambda b: b["site"][0]["alerts"][0].update(pluginid="../x"),
    lambda b: b["site"][0]["alerts"][0].update(instances=[]),
    lambda b: b["site"][0]["alerts"][0]["instances"][0].pop("uri"),
])
def test_bericht_kaputt_bricht_ab(kaputt):
    b = copy.deepcopy(_bericht())
    kaputt(b)
    with pytest.raises(NachweisFehler) as e:
        dast.werte_zap_aus(b)
    assert e.value.regel == "dast-bericht"


def test_bericht_ohne_alarme_ist_gueltig():
    b = _bericht()
    b["site"][0]["alerts"] = []
    assert dast.werte_zap_aus(b) == []


# --- Gate und Ausnahmen ---------------------------------------------------------------------------

def test_mittlerer_alarm_ohne_befund_blockiert():
    blockiert = dast.dast_gate([_alarm("10038", "mittel")], [], [])
    assert len(blockiert) == 1
    assert dast.befund_id("10038", "") in blockiert[0] and "Befundregister" in blockiert[0]


def test_hoher_alarm_ohne_befund_blockiert():
    assert dast.dast_gate([_alarm("10038", "hoch")], [], [])


def test_alarm_mit_befund_blockiert_nicht():
    assert dast.dast_gate([_alarm("10038", "mittel")], [_befund("10038")], []) == []


def test_befund_einer_anderen_regel_hilft_nicht():
    assert dast.dast_gate([_alarm("10038", "mittel")], [_befund("10021")], [])


def test_niedriger_alarm_braucht_ausnahme(tmp_path):
    alarm = _alarm("10021", "niedrig", ("api/v1/projekte", "assets/app.js"))
    assert dast.dast_gate([alarm], [], [])
    ausnahmen = dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, _ausnahme()))
    assert dast.dast_gate([alarm], [], ausnahmen) == []


def test_ausnahme_muss_alle_pfade_erfassen(tmp_path):
    ausnahmen = dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, _ausnahme(pfade='["api/v1/**"]')))
    alarm = _alarm("10021", "niedrig", ("api/v1/projekte", "assets/app.js"))
    assert dast.dast_gate([alarm], [], ausnahmen)


def test_ausnahme_gilt_nur_fuer_ihre_regel(tmp_path):
    ausnahmen = dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, _ausnahme("10099")))
    assert dast.dast_gate([_alarm("10021", "niedrig", ("assets/app.js",))], [], ausnahmen)


def test_ausnahme_nur_bis_zur_erfassten_schwere(tmp_path):
    # Eine Ausnahme für „info“ deckt keinen niedrigen Alarm der Regel.
    ausnahmen = dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, _ausnahme(schwere="info")))
    assert dast.dast_gate([_alarm("10021", "niedrig", ("assets/app.js",))], [], ausnahmen)
    assert dast.dast_gate([_alarm("10021", "info", ("assets/app.js",))], [], ausnahmen) == []


def test_wurzelpfad_wird_von_stern_erfasst(tmp_path):
    ausnahmen = dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, _ausnahme(pfade='["*"]')))
    assert dast.dast_gate([_alarm("10021", "niedrig", ("",))], [], ausnahmen) == []


def test_gate_meldet_je_regel_einmal():
    blockiert = dast.dast_gate([_alarm("10038"), _alarm("10038", pfade=("a",))], [], [])
    assert len(blockiert) == 1


@pytest.mark.skipif(not (WURZEL / dast.AUSNAHMEN).exists(),
                    reason="dast-ausnahmen.toml liegt nur im privaten Repo (nicht im Export)")
def test_echte_ausnahmendatei_laedt():
    assert dast.lade_ausnahmen(WURZEL / dast.AUSNAHMEN) is not None


@pytest.mark.parametrize("text", [
    _ausnahme(schwere="mittel"), _ausnahme(schwere="hoch"), _ausnahme(begruendung="kurz"),
    _ausnahme(datum='"2026-10-01"'), _ausnahme(pfade="[]"), _ausnahme(pfade='["/api"]'),
    _ausnahme(pfade='"api"'), _ausnahme(pluginid="../x"),
    _ausnahme() + "x = 1\n", "[[ausnahme]]\n", "[andere]\nx = 1\n", "kaputt =\n",
])
def test_ausnahmen_ungueltig(tmp_path, text):
    with pytest.raises(NachweisFehler) as e:
        dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, text))
    assert e.value.regel == "dast-ausnahmen"


def test_ausnahmendatei_fehlt(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        dast.lade_ausnahmen(tmp_path / "nichts.toml")
    assert e.value.regel == "dast-ausnahmen"


# --- Abdeckung ------------------------------------------------------------------------------------

def test_besuchte_urls_aus_export():
    urls = dast.besuchte_urls((FIXTURES / "urls.yaml").read_text("utf-8"))
    assert urls[f"{ZIEL}/api/v1/version"] == 200
    assert urls[f"{ZIEL}/api/v1/projekte"] == 401
    assert urls[f"{ZIEL}/robots.txt"] == 404
    assert f"{ZIEL}/api/v1" not in urls  # Knoten ohne Antwort zählen nicht


def test_abdeckung_zaehlt_api_urls_mit_antwort_200():
    urls = dast.besuchte_urls((FIXTURES / "urls.yaml").read_text("utf-8"))
    assert dast.pruefe_abdeckung(urls, ZIEL) == 1


def test_abdeckung_ohne_api_url_bricht_ab():
    urls = {f"{ZIEL}/": 200, f"{ZIEL}/robots.txt": 404}
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_abdeckung(urls, ZIEL)
    assert e.value.regel == "dast-abdeckung"


def test_abdeckung_nur_abgewiesene_api_urls_bricht_ab():
    urls = {f"{ZIEL}/api/v1/version": 401, f"{ZIEL}/api/v1/projekte": 403}
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_abdeckung(urls, ZIEL)
    assert e.value.regel == "dast-abdeckung"


def test_abdeckung_fremder_ursprung_zaehlt_nicht():
    urls = {"https://fremd.example/api/v1/version": 200}
    with pytest.raises(NachweisFehler):
        dast.pruefe_abdeckung(urls, ZIEL)


# --- Bereinigung ----------------------------------------------------------------------------------

def _ordner_mit_geheimnis(tmp_path: Path) -> Path:
    o = tmp_path / "bericht"
    o.mkdir()
    (o / "zap.json").write_text(json.dumps({"evidence": f"Cookie: __Host-scanview={COOKIE}"}),
                                encoding="utf-8")
    (o / "zap.html").write_text(f"<p>__Host-scanview={COOKIE}</p>", encoding="utf-8")
    (o / "ablauf.yaml").write_text(f"x: {COOKIE}\n", encoding="utf-8")
    (o / "roh.bin").write_bytes(b"\x00\x01" + COOKIE.encode() + b"\xff")
    (o / "sauber.txt").write_text("nichts", encoding="utf-8")
    return o


def test_bereinige_entfernt_geheimnis_ueberall(tmp_path):
    o = _ordner_mit_geheimnis(tmp_path)
    dast.bereinige(o, [COOKIE])
    for datei in o.iterdir():
        assert COOKIE.encode() not in datei.read_bytes()
    assert b"__Host-scanview=" in (o / "zap.html").read_bytes()
    assert (o / "sauber.txt").read_text("utf-8") == "nichts"
    assert json.loads((o / "zap.json").read_text("utf-8"))  # bleibt gültiges JSON


def test_bereinige_ersetzt_auch_url_kodierte_form(tmp_path):
    o = tmp_path / "b"
    o.mkdir()
    (o / "zap.json").write_text("a=k7Hq2Zr9-Vt4Lm8Xw3Pd6Nb1_Yc5Sa0Je%2BGf%2FUi%3D", "utf-8")
    dast.bereinige(o, [COOKIE])
    assert "k7Hq2Zr9" not in (o / "zap.json").read_text("utf-8")


def test_bereinige_unterordner_und_mehrere_geheimnisse(tmp_path):
    o = tmp_path / "b"
    (o / "tief").mkdir(parents=True)
    (o / "tief" / "x.txt").write_text(f"{COOKIE} und passwort-geheim-123", "utf-8")
    dast.bereinige(o, [COOKIE, "passwort-geheim-123"])
    text = (o / "tief" / "x.txt").read_text("utf-8")
    assert COOKIE not in text and "passwort-geheim-123" not in text


def test_bereinige_symlink_loescht_und_bricht_ab(tmp_path):
    o = _ordner_mit_geheimnis(tmp_path)
    (o / "link").symlink_to(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        dast.bereinige(o, [COOKIE])
    assert e.value.regel == "dast-geheimnis"
    assert list(o.iterdir()) == []


@pytest.mark.parametrize("geheimnis", ["", "kurz", " " * 12])
def test_bereinige_zu_kurzes_geheimnis_bricht_ab(tmp_path, geheimnis):
    o = _ordner_mit_geheimnis(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        dast.bereinige(o, [geheimnis])
    assert e.value.regel == "dast-geheimnis"


def test_bereinige_ordner_fehlt(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        dast.bereinige(tmp_path / "nichts", [COOKIE])
    assert e.value.regel == "dast-geheimnis"


# --- Lauf mit Attrappen ---------------------------------------------------------------------------

class Welt:
    """Attrappe für Container und HTTP: protokolliert Befehle und Abrufe."""

    def __init__(self, *, vorher: int = 200, nachher: int = 200, exit_code: int = 0,
                 urls: str | None = None, bericht: bool = True, version: int = 200,
                 bericht_text: str | None = None) -> None:
        self.version = version
        self.bericht_text = bericht_text
        self.statuscodes = [vorher, nachher]
        self.exit_code = exit_code
        self.urls = (FIXTURES / "urls.yaml").read_text("utf-8") if urls is None else urls
        self.bericht = bericht
        self.befehle: list[list[str]] = []
        self.abrufe: list[tuple[str, str]] = []
        self.auth_beim_lauf: tuple[str, int] | None = None
        self.ablauf = ""
        self.ausgabe = f"ZAP gestartet mit {COOKIE}\n"

    def abruf(self, url: str, cookie: str) -> tuple[int, bytes]:
        self.abrufe.append((url, cookie))
        if url.endswith("/api/v1/openapi.json"):
            status = self.statuscodes.pop(0)
            return status, OPENAPI if status == 200 else b"{}"
        if url.endswith("/api/v1/version"):
            return self.version, b'{"version": "0.2.0"}'
        return 404, b""

    def laeufer(self, befehl: list[str]) -> subprocess.CompletedProcess[str]:
        self.befehle.append(befehl)
        mount = next(a for a in befehl if a.endswith(":/zap/wrk:rw"))
        arbeit = Path(mount.split(":")[0])
        auth = arbeit / "auth.prop"
        self.auth_beim_lauf = (auth.read_text("utf-8"), stat.S_IMODE(auth.stat().st_mode))
        self.ablauf = (arbeit / "ablauf.yaml").read_text("utf-8")
        (arbeit / "urls.yaml").write_text(self.urls, "utf-8")
        if self.bericht:
            roh = json.loads((FIXTURES / "bericht.json").read_text("utf-8"))
            # Der Rohbericht enthält das Cookie (z. B. als Beleg), damit die Bereinigung wirkt.
            roh["site"][0]["alerts"][0]["instances"][0]["evidence"] = COOKIE
            (arbeit / "zap.json").write_text(
                self.bericht_text if self.bericht_text is not None else json.dumps(roh), "utf-8")
        return subprocess.CompletedProcess(befehl, self.exit_code, self.ausgabe, "")


def _lauf(tmp_path: Path, welt: Welt, ziel: str = ZIEL, cookie: str = COOKIE) -> Path:
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir(exist_ok=True)
    return dast.zap_lauf(ziel, cookie, WERKZEUGE, arbeit, abruf=welt.abruf,
                         laeufer=welt.laeufer)


def test_lauf_liefert_bericht_und_loescht_zugangsdatei(tmp_path):
    welt = Welt()
    bericht = _lauf(tmp_path, welt)
    assert bericht == tmp_path / "arbeit" / "zap.json"
    assert json.loads(bericht.read_text("utf-8"))["@version"] == "2.17.0"
    assert not (tmp_path / "arbeit" / "auth.prop").exists()
    # Sitzung vorher und nachher: zwei Abrufe der OpenAPI mit dem Cookie.
    assert [u for u, _ in welt.abrufe if u.endswith("openapi.json")] == [
        f"{ZIEL}/api/v1/openapi.json"] * 2
    assert all(c == f"__Host-scanview={COOKIE}" for _, c in welt.abrufe)


def test_zugangsdatei_nur_fuer_den_lauf_und_nur_lesbar_fuer_den_besitzer(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    inhalt, modus = welt.auth_beim_lauf  # type: ignore[misc]
    assert modus == 0o600
    assert f"replacement=__Host-scanview={COOKIE}" in inhalt
    assert "matchstr=Cookie" in inhalt and "matchtype=REQ_HEADER" in inhalt


def test_cookie_nie_in_argv(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    assert all(COOKIE not in teil for teil in welt.befehle[0])
    assert COOKIE not in welt.ablauf


def test_container_ist_gehaertet(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    b = welt.befehle[0]
    assert b[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in b and "no-new-privileges" in b
    assert b[b.index("--cap-drop") + 1] == "ALL"
    uid = b[b.index("--user") + 1]
    assert uid.split(":")[0] != "0"
    assert "--network" not in b  # Bridge: Standardnetz der Laufzeit, nie Host-Netz
    # Kein zusätzlicher beschreibbarer Ort: ZAP schreibt nach -dir und user.home unter dem
    # gehärteten /tmp, nicht in das Heimatverzeichnis des Images.
    assert b.count("--tmpfs") == 1 and b[b.index("--tmpfs") + 1].startswith("/tmp:")
    assert "JAVA_TOOL_OPTIONS=-Duser.home=/tmp/zap-home" in b
    zap = b[b.index(f"zaproxy/zap-stable@{DIGEST}") + 1:]
    assert zap[zap.index("-dir") + 1] == "/tmp/zap-home"
    assert b[b.index("--pull") + 1] == "never"
    assert f"zaproxy/zap-stable@{DIGEST}" in b
    # Schreibziel ist nur der Arbeitsordner; weitere Einhängungen gibt es nicht.
    assert [a for a in b if a.endswith(":rw")] == [f"{tmp_path / 'arbeit'}:/zap/wrk:rw"]
    assert b.count("-v") == 1


def test_zap_start_ohne_marketplace_und_mit_eigenem_verzeichnis(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    b = welt.befehle[0]
    i = b.index("zap.sh")
    assert b[i:i + 9] == ["zap.sh", "-cmd", "-silent", "-dir", "/tmp/zap-home",
                          "-configfile", "/zap/wrk/auth.prop", "-autorun",
                          "/zap/wrk/ablauf.yaml"]


def _passt(muster: str, url: str) -> bool:
    return re.fullmatch(muster, url) is not None


def test_ablauf_ausschluesse_und_nur_get_saaten(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    ablauf = json.loads(welt.ablauf)
    kontext = ablauf["env"]["contexts"][0]
    assert kontext["urls"] == [ZIEL]
    assert kontext["includePaths"] == [re.escape(ZIEL) + "/.*"]
    ausschluesse = kontext["excludePaths"]
    for pfad in ("/auth/anmelden", "/api/v1/abmelden", "/api/v1/konto", "/api/v1/konto/tokens",
                 "/api/v1/admin/nutzer", "/api/v1/statistik/x"):
        assert any(_passt(muster, ZIEL + pfad) for muster in ausschluesse), pfad
    saaten = [j["parameters"]["url"] for j in ablauf["jobs"]
              if j["type"] == "spider" and j["parameters"]["url"] != ZIEL]
    # GET-Pfade der OpenAPI ohne Platzhalter und ohne ausgeschlossene Bereiche.
    assert saaten == [f"{ZIEL}/api/v1/projekte", f"{ZIEL}/api/v1/version"]
    typen = [j["type"] for j in ablauf["jobs"]]
    assert typen[0] == "passiveScan-config" and typen[-2:] == ["export", "report"]
    assert "passiveScan-wait" in typen
    report = ablauf["jobs"][-1]["parameters"]
    assert report["template"] == "traditional-json" and report["reportFile"] == "zap.json"


@pytest.mark.parametrize("ziel", [
    "http://l.example.invalid", "https://user@scanview.lab.example",
    "https://scanview.lab.example/pfad", "https://scanview.lab.example/?x=1",
    "https://scanview.lab.example#a", "ftp://x.example", "", "scanview.lab.example",
    "https://scanview.lab.example:99999"])
def test_ungueltiges_ziel(tmp_path, ziel):
    welt = Welt()
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt, ziel=ziel)
    assert e.value.regel == "dast-ziel"
    assert welt.befehle == [] and welt.abrufe == []


@pytest.mark.parametrize("cookie", ["", "kurz", "a b" * 6, "x;y" + "z" * 12, "a\nb" + "c" * 12,
                                    "a\r" + "c" * 14, '"' + "c" * 14])
def test_ungueltiges_cookie(tmp_path, cookie):
    welt = Welt()
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt, cookie=cookie)
    assert e.value.regel == "dast-cookie"
    assert welt.befehle == [] and welt.abrufe == []


def test_sitzung_vor_dem_lauf_ungueltig(tmp_path):
    welt = Welt(vorher=401)
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt)
    assert e.value.regel == "dast-sitzung"
    assert welt.befehle == []
    assert not (tmp_path / "arbeit" / "auth.prop").exists()


def test_sitzung_nach_dem_lauf_verloren(tmp_path):
    welt = Welt(nachher=401)
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt)
    assert e.value.regel == "dast-sitzung"
    assert not (tmp_path / "arbeit" / "auth.prop").exists()


def test_abdeckung_null_bricht_ab_und_loescht_zugangsdatei(tmp_path):
    welt = Welt(urls=f"- node: Sites\n  children:\n  - url: {ZIEL}\n    statusCode: 200\n")
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt)
    assert e.value.regel == "dast-abdeckung"
    assert not (tmp_path / "arbeit" / "auth.prop").exists()


def test_container_fehler_bricht_ab_ohne_cookie_in_der_meldung(tmp_path):
    welt = Welt(exit_code=3)
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt)
    assert e.value.regel == "dast-lauf"
    assert COOKIE not in str(e.value)
    assert not (tmp_path / "arbeit" / "auth.prop").exists()


def test_fehlender_bericht_bricht_ab(tmp_path):
    welt = Welt(bericht=False)
    with pytest.raises(NachweisFehler) as e:
        _lauf(tmp_path, welt)
    assert e.value.regel == "dast-lauf"


def test_laeufer_ausnahme_loescht_zugangsdatei(tmp_path):
    def kaputt(_befehl: list[str]) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("abgebrochen")
    welt = Welt()
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    with pytest.raises(RuntimeError):
        dast.zap_lauf(ZIEL, COOKIE, WERKZEUGE, arbeit, abruf=welt.abruf, laeufer=kaputt)
    assert not (arbeit / "auth.prop").exists()


def test_nicht_leerer_arbeitsordner_wird_nie_angefasst(tmp_path):
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    (arbeit / "zap.json").write_text(COOKIE, "utf-8")
    (arbeit / "link").symlink_to(tmp_path)
    welt = Welt()
    with pytest.raises(NachweisFehler) as e:
        dast.zap_lauf(ZIEL, COOKIE, WERKZEUGE, arbeit, abruf=welt.abruf, laeufer=welt.laeufer)
    assert e.value.regel == "dast-ausgabe"
    assert welt.befehle == []
    assert (arbeit / "zap.json").read_text("utf-8") == COOKIE  # unverändert
    assert (arbeit / "link").is_symlink()


def test_arbeitsordner_fehlt(tmp_path):
    welt = Welt()
    with pytest.raises(NachweisFehler) as e:
        dast.zap_lauf(ZIEL, COOKIE, WERKZEUGE, tmp_path / "nichts", abruf=welt.abruf,
                      laeufer=welt.laeufer)
    assert e.value.regel == "dast-ausgabe"


def test_echtes_werkzeug_hat_digest():
    w = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    assert "@sha256:" in w["zap"].image


# --- Verweigerung für S ---------------------------------------------------------------------------

def test_s_ohne_freigabe_verweigert():
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", "https://scanview.example.de", None, HEUTE)
    assert e.value.regel == "dast-freigabe"


@pytest.mark.parametrize("freigabe", ["", "morgen", "2026-13-01", "2026-10-02", "01.10.2026"])
def test_s_mit_ungueltiger_freigabe_verweigert(freigabe):
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", "https://scanview.example.de", freigabe, HEUTE)
    assert e.value.regel == "dast-freigabe"


def test_s_mit_freigabe_von_heute_erlaubt():
    dast.pruefe_freigabe("S", "https://scanview.example.de", "2026-10-01", HEUTE)


def test_s_mit_altem_datum_erlaubt_nicht():
    # Eine Freigabe gilt für den Tag, an dem sie erteilt wurde.
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", "https://scanview.example.de", "2026-09-30", HEUTE)
    assert e.value.regel == "dast-freigabe"


def test_s_gegen_l_adresse_verweigert():
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", ZIEL, "2026-10-01", HEUTE)
    assert e.value.regel == "dast-ziel"


def test_l_nur_gegen_l_adresse():
    dast.pruefe_freigabe("L", ZIEL, None, HEUTE)
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("L", "https://scanview.example.de", None, HEUTE)
    assert e.value.regel == "dast-ziel"


@pytest.mark.parametrize("schreibweise", [ZIEL + "/", "https://L.Example.INVALID",
                                          "https://l.example.invalid:443", ZIEL + ".",
                                          "https://L.example.invalid.:443/"])
def test_l_adresse_normalisiert_verglichen(schreibweise):
    # Ursprung aus Schema und Host, ohne abschließenden Schrägstrich, Host klein, ohne Punkt am
    # Hostende und ohne den Standardport 443: Jede dieser Schreibweisen ist L, auch als S.
    dast.pruefe_freigabe("L", schreibweise, None, HEUTE)
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", schreibweise, "2026-10-01", HEUTE)
    assert e.value.regel == "dast-ziel"


@pytest.mark.parametrize(("url", "ursprung"), [
    ("https://x.example:443", "https://x.example"),
    ("https://x.example.", "https://x.example"),
    ("https://X.Example.:443/", "https://x.example"),
    ("https://x.example:8443", "https://x.example:8443"),
    ("https://x.example.:8443", "https://x.example:8443"),
])
def test_ziel_ohne_standardport_und_schlusspunkt(url, ursprung):
    assert dast.pruefe_ziel(url) == ursprung


@pytest.mark.parametrize("url", ["https://.", "https://x.example..", "https://..:443"])
def test_ziel_nur_aus_punkten_oder_mit_doppeltem_schlusspunkt_verweigert(url):
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_ziel(url)
    assert e.value.regel == "dast-ziel"


def test_l_adresse_nur_als_hash_im_code():
    """Die Adresse von L steht nie im Klartext im (veröffentlichten) Code, nur ihr SHA-256."""
    assert not hasattr(dast, "L_ZIEL")
    quelle = (WURZEL / "nachweise" / "src" / "scanview_nachweise" / "dast.py").read_text(
        encoding="utf-8")
    zeile = next(z for z in quelle.splitlines() if z.startswith("L_URSPRUNG_SHA256 = "))
    assert re.fullmatch(r'L_URSPRUNG_SHA256 = "[0-9a-f]{64}"', zeile)


def test_fehlermeldung_nennt_l_adresse_nicht():
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("L", "https://anders.example.invalid", None, HEUTE)
    assert "l.example.invalid" not in e.value.text.replace("anders.example.invalid", "")


def test_fehlermeldung_s_zweig_nennt_l_adresse_nicht():
    # Wer L als S angibt, bekommt den Treffer nicht mit der Adresse bestätigt.
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("S", ZIEL, "2026-10-01", HEUTE)
    assert e.value.regel == "dast-ziel"
    assert "l.example.invalid" not in e.value.text


def test_l_mit_freigabe_verweigert():
    # Die Freigabe gehört zu S; ein Datum bei L deutet auf eine verwechselte Instanz.
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("L", ZIEL, "2026-10-01", HEUTE)
    assert e.value.regel == "dast-freigabe"


def test_unbekannte_instanz():
    with pytest.raises(NachweisFehler) as e:
        dast.pruefe_freigabe("X", ZIEL, None, HEUTE)
    assert e.value.regel == "dast-instanz"


# --- Gesamtlauf mit Archiv ------------------------------------------------------------------------

@pytest.fixture
def archiv_repo(tmp_path: Path) -> Path:
    return signierhilfe.signiertes_archiv(tmp_path / "archiv")


def _ausnahmen(tmp_path: Path) -> list[dast.Ausnahme]:
    text = _ausnahme() + _ausnahme("10049", '["api/v1/**"]', "info")
    return dast.lade_ausnahmen(_ausnahmen_datei(tmp_path, text))


def _pruefen(tmp_path: Path, archiv_repo: Path, welt: Welt,
             befunde: list[Befund] | None = None) -> dast.Ergebnis:
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir(exist_ok=True)
    return dast.pruefen(
        "L", ZIEL, COOKIE, WERKZEUGE, arbeit, archiv_repo, heute=HEUTE,
        befunde=[_befund("10038")] if befunde is None else befunde,
        ausnahmen=_ausnahmen(tmp_path), abruf=welt.abruf, laeufer=welt.laeufer)


def test_pruefen_schreibt_nachweis_ins_archiv(tmp_path, archiv_repo):
    welt = Welt()
    e = _pruefen(tmp_path, archiv_repo, welt)
    assert e.blockiert == []
    ziel = archiv_repo / "pruefungen" / "2026-10-01-dast-L"
    assert e.ziel == ziel
    assert sorted(p.name for p in ziel.iterdir()) == ["ergebnis.json", "zap.json"]
    erg = json.loads((ziel / "ergebnis.json").read_text("utf-8"))
    assert erg["instanz"] == "L" and erg["ziel"] == ZIEL
    assert erg["version"] == "0.2.0"
    assert erg["werkzeug"] == {"image": f"zaproxy/zap-stable@{DIGEST}", "version": "2.17.0"}
    assert erg["alarme"] == {"hoch": 0, "mittel": 1, "niedrig": 1, "info": 1}
    assert erg["gate"] == "bestanden" and erg["blockierend"] == []
    assert erg["abdeckung"]["api_urls"] == 1
    assert signierhilfe.git(archiv_repo, "log", "-1", "--format=%s") == \
        "pruefung: dast L 2026-10-01"
    # Kein Geheimnis im Archiv, kein Zwischenstand im Arbeitsordner.
    for datei in ziel.iterdir():
        assert COOKIE.encode() not in datei.read_bytes()
    assert not (tmp_path / "arbeit" / "auth.prop").exists()


def test_pruefen_blockiert_wird_trotzdem_archiviert(tmp_path, archiv_repo):
    welt = Welt()
    e = _pruefen(tmp_path, archiv_repo, welt, befunde=[])
    assert len(e.blockiert) == 1 and "B-DAST-10038-" in e.blockiert[0]
    erg = json.loads((e.ziel / "ergebnis.json").read_text("utf-8"))
    assert erg["gate"] == "blockiert" and erg["blockierend"] == e.blockiert


def test_pruefen_zweiter_lauf_am_selben_tag_bekommt_laufnummer(tmp_path, archiv_repo):
    erster = _pruefen(tmp_path, archiv_repo, Welt())
    neu = tmp_path / "zweite-arbeit"
    neu.mkdir()
    welt = Welt()
    e = dast.pruefen("L", ZIEL, COOKIE, WERKZEUGE, neu, archiv_repo, heute=HEUTE,
                     befunde=[_befund("10038")], ausnahmen=[], abruf=welt.abruf,
                     laeufer=welt.laeufer)
    assert erster.ziel.name == "2026-10-01-dast-L"
    assert e.ziel.name == "2026-10-01.2-dast-L"
    assert welt.befehle != []
    # Der erste Nachweis bleibt stehen.
    assert (erster.ziel / "ergebnis.json").is_file()


@pytest.mark.parametrize("stoerung", ["schluessel", "unsauber"])
def test_pruefen_archivfehler_vor_dem_scan(tmp_path, archiv_repo, stoerung):
    """Kann der Nachweis nicht geschrieben werden, läuft kein Scan gegen die Instanz."""
    import subprocess
    if stoerung == "schluessel":
        fremd = tmp_path / "fremd"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(fremd)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(archiv_repo), "config", "user.signingkey", str(fremd)],
                       check=True)
    else:
        (archiv_repo / "liegengeblieben.txt").write_text("x", encoding="utf-8")
    arbeit = tmp_path / "arbeit-vorab"
    arbeit.mkdir()
    welt = Welt()
    with pytest.raises(NachweisFehler):
        dast.pruefen("L", ZIEL, COOKIE, WERKZEUGE, arbeit, archiv_repo, heute=HEUTE,
                     befunde=[_befund("10038")], ausnahmen=[], abruf=welt.abruf,
                     laeufer=welt.laeufer)
    assert welt.befehle == [] and welt.abrufe == []


def test_pruefen_s_ohne_freigabe_ruehrt_nichts_an(tmp_path, archiv_repo):
    welt = Welt()
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    with pytest.raises(NachweisFehler) as e:
        dast.pruefen("S", "https://scanview.example.de", COOKIE, WERKZEUGE, arbeit,
                     archiv_repo, heute=HEUTE, befunde=[], ausnahmen=[], abruf=welt.abruf,
                     laeufer=welt.laeufer)
    assert e.value.regel == "dast-freigabe"
    assert welt.befehle == [] and welt.abrufe == []
    assert not (archiv_repo / "pruefungen").exists()


def test_pruefen_ohne_archiv_commit_bei_fehler(tmp_path, archiv_repo):
    welt = Welt(urls="- node: Sites\n")
    with pytest.raises(NachweisFehler) as e:
        _pruefen(tmp_path, archiv_repo, welt)
    assert e.value.regel == "dast-abdeckung"
    assert not (archiv_repo / "pruefungen").exists()
    assert signierhilfe.git(archiv_repo, "status", "--porcelain") == ""


# --- Cookie-Datei und CLI -------------------------------------------------------------------------

def _cookie_datei(tmp_path: Path, inhalt: str = COOKIE + "\n", modus: int = 0o600) -> Path:
    p = tmp_path / "cookie"
    p.write_text(inhalt, encoding="utf-8")
    p.chmod(modus)
    return p


def test_cookie_datei_wert_und_name_wert(tmp_path):
    assert dast.lies_cookie(_cookie_datei(tmp_path)) == COOKIE
    assert dast.lies_cookie(_cookie_datei(tmp_path, f"__Host-scanview={COOKIE}\n")) == COOKIE


@pytest.mark.parametrize(("inhalt", "modus"), [(COOKIE, 0o644), (COOKIE, 0o640), ("", 0o600),
                                               ("a=b=c" + "d" * 12, 0o600)])
def test_cookie_datei_ungueltig(tmp_path, inhalt, modus):
    with pytest.raises(NachweisFehler) as e:
        dast.lies_cookie(_cookie_datei(tmp_path, inhalt, modus))
    assert e.value.regel == "dast-cookie"


def test_cookie_datei_fehlt_oder_ist_symlink(tmp_path):
    with pytest.raises(NachweisFehler):
        dast.lies_cookie(tmp_path / "nichts")
    echt = _cookie_datei(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(echt)
    with pytest.raises(NachweisFehler):
        dast.lies_cookie(link)


def test_cli_s_ohne_freigabe_verweigert(tmp_path, capsys):
    cookie = _cookie_datei(tmp_path)
    code = cli.main(["pruefen", "dast", "--ziel", "https://scanview.example.de",
                     "--instanz", "S", "--archiv", str(tmp_path / "archiv"),
                     "--cookie-datei", str(cookie)])
    assert code == 2
    assert "dast-freigabe" in capsys.readouterr().err


def test_cli_instanz_ist_pflicht_und_nur_l_oder_s(tmp_path):
    cookie = _cookie_datei(tmp_path)
    for rest in ([], ["--instanz", "X"]):
        assert cli.main(["pruefen", "dast", "--ziel", ZIEL, "--archiv", str(tmp_path),
                         "--cookie-datei", str(cookie), *rest]) == 2


def test_cli_l_lauf_mit_attrappen(tmp_path, archiv_repo, monkeypatch, capsys):
    welt = Welt()
    cookie = _cookie_datei(tmp_path)
    echt = dast.pruefen
    # Eigene Wurzel: leeres Befundregister, leere Ausnahmen.
    wurzel = tmp_path / "wurzel"
    (wurzel / "docs" / "compliance").mkdir(parents=True)
    (wurzel / "docs" / "compliance" / "befunde.toml").write_text(
        '[[befund]]\nid = "B-DAST-99999"\nquelle = "DAST"\ntitel = "Platzhalter"\n'
        'schwere = "info"\nkategorie = "sonstiges"\nstatus = "kein-befund"\n'
        'staende = ["L"]\nentdeckt = 2026-10-01\n', "utf-8")
    (wurzel / "docs" / "compliance" / "dast-ausnahmen.toml").write_text("", "utf-8")
    (wurzel / "docs" / "compliance" / "kontrollmatrix.toml").write_text("", "utf-8")
    (wurzel / "nachweise").mkdir()
    (wurzel / "nachweise" / "werkzeuge.json").write_text(
        json.dumps({"zap": {"image": WERKZEUGE["zap"].image, "version": "2.17.0"}}), "utf-8")

    def mit_attrappen(*args: Any, **kw: Any) -> dast.Ergebnis:
        return echt(*args, **kw, abruf=welt.abruf, laeufer=welt.laeufer)

    monkeypatch.setattr(dast, "pruefen", mit_attrappen)
    code = cli.main(["pruefen", "dast", "--ziel", ZIEL, "--instanz", "L",
                     "--archiv", str(archiv_repo), "--cookie-datei", str(cookie),
                     "--wurzel", str(wurzel), "--heute", "2026-10-01"])
    out = capsys.readouterr()
    assert code == 1, out.err  # kein Befund für den mittleren Alarm: blockiert, aber archiviert
    assert "BLOCKIERT" in out.out and "B-DAST-10038-" in out.out
    assert COOKIE not in out.out + out.err
    assert (archiv_repo / "pruefungen" / "2026-10-01-dast-L" / "ergebnis.json").is_file()


def test_arbeitsordner_ist_privat():
    ordner = dast.neuer_arbeitsordner()
    try:
        assert stat.S_IMODE(os.stat(ordner).st_mode) == 0o700
    finally:
        ordner.rmdir()


# --- Aufräumen nach Fehlern -----------------------------------------------------------------------

def _ohne_cookie(ordner: Path) -> None:
    for datei in ordner.rglob("*"):
        if datei.is_file():
            assert COOKIE.encode() not in datei.read_bytes(), datei.name


@pytest.mark.parametrize(("welt_args", "regel"), [
    ({"nachher": 401}, "dast-sitzung"),
    ({"urls": "- node: Sites\n"}, "dast-abdeckung"),
    ({"version": 500}, "dast-sitzung"),
    ({"bericht_text": "{kaputt"}, "dast-bericht"),
    ({"bericht_text": '{"site": 3}'}, "dast-bericht"),
])
def test_fehlerpfad_nach_dem_lauf_hinterlaesst_kein_cookie(tmp_path, archiv_repo, welt_args,
                                                           regel):
    welt = Welt(**welt_args)
    with pytest.raises(NachweisFehler) as e:
        _pruefen(tmp_path, archiv_repo, welt)
    assert e.value.regel == regel
    _ohne_cookie(tmp_path / "arbeit")
    assert not (tmp_path / "arbeit" / "auth.prop").exists()
    assert not (archiv_repo / "pruefungen").exists()


def test_fehlerpfad_ausnahme_im_laeufer_bereinigt(tmp_path):
    def kaputt(befehl: list[str]) -> subprocess.CompletedProcess[str]:
        arbeit = Path(next(a for a in befehl if a.endswith(":/zap/wrk:rw")).split(":")[0])
        (arbeit / "zap.json").write_text(COOKIE, "utf-8")
        raise RuntimeError("abgebrochen")
    welt = Welt()
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    with pytest.raises(RuntimeError):
        dast.zap_lauf(ZIEL, COOKIE, WERKZEUGE, arbeit, abruf=welt.abruf, laeufer=kaputt)
    _ohne_cookie(arbeit)


def test_bereinige_folgt_keinem_symlink(tmp_path):
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    (fremd / "wichtig.txt").write_text("bleibt", "utf-8")
    o = tmp_path / "bericht"
    o.mkdir()
    (o / "zap.json").write_text(COOKIE, "utf-8")
    (o / "link").symlink_to(fremd, target_is_directory=True)
    with pytest.raises(NachweisFehler) as e:
        dast.bereinige(o, [COOKIE])
    assert e.value.regel == "dast-geheimnis"
    assert (fremd / "wichtig.txt").read_text("utf-8") == "bleibt"
    assert list(o.iterdir()) == []


# --- Weiterleitungen und Zieladresse --------------------------------------------------------------

def test_weiterleitung_wird_nie_befolgt():
    h = dast._KeineWeiterleitung()
    assert h.redirect_request(None, None, 302, "Found", {}, "https://fremd.example/x") is None
    assert h.redirect_request(None, None, 301, "Moved", {}, "http://scanview.example/x") is None


def test_http_abruf_mit_weiterleitung_liefert_status_statt_zu_folgen():
    import io
    import urllib.error
    from email.message import Message
    gesehen: list[Any] = []

    class Opener:
        def open(self, anfrage: Any, timeout: int) -> Any:
            gesehen.append(anfrage)
            raise urllib.error.HTTPError(
                anfrage.full_url, 302, "Found", Message(), io.BytesIO(b""))

    status, _ = dast._http_abruf(f"{ZIEL}/api/v1/version", "__Host-scanview=x", opener=Opener())
    assert status == 302
    assert len(gesehen) == 1 and gesehen[0].get_header("Cookie") == "__Host-scanview=x"


def test_http_abruf_verlangt_https():
    for url in ("http://scanview.example/api/v1/version", "ftp://x/y",
                "https://user@scanview.example/api"):
        with pytest.raises(NachweisFehler) as e:
            dast._http_abruf(url, "c")
        assert e.value.regel == "dast-sitzung"


def test_cookie_geht_nur_an_das_ziel():
    gesehen: list[str] = []

    def abruf(url: str, cookie: str) -> tuple[int, bytes]:
        gesehen.append(url)
        return 200, b""

    gebunden = dast._an_ziel_gebunden(abruf, ZIEL)
    gebunden(f"{ZIEL}/api/v1/version", "c")
    for url in ("https://fremd.example/api/v1/version", f"{ZIEL}.fremd.example/x",
                "http://l.example.invalid/api", f"{ZIEL}@fremd.example/x"):
        with pytest.raises(NachweisFehler) as e:
            gebunden(url, "c")
        assert e.value.regel == "dast-sitzung"
    assert gesehen == [f"{ZIEL}/api/v1/version"]


# --- Gate: Pfadmuster -----------------------------------------------------------------------------

@pytest.mark.parametrize(("pfad", "muster"), [
    ("", ""), ("api/v1/projekte", "api/v1/projekte"),
    ("api/v1/projekte/42/poi", "api/v1/projekte/{n}/poi"),
    ("api/v1/x/123e4567-e89b-12d3-a456-426614174000", "api/v1/x/{uuid}"),
    ("assets/app-3f2a9c1b.js", "assets/app-{hash}.js"),
    ("assets/index-DiwrgTda.js", "assets/index-{hash}.js"),
    ("assets/my-document.pdf", "assets/my-document.pdf"),
    ("assets/feedback.js", "assets/feedback.js"),
    ("api/v1/a/0123456789abcdef0123456789abcdef", "api/v1/a/{hash}"),
])
def test_pfad_muster(pfad, muster):
    assert dast.pfad_muster(pfad) == muster


def test_befund_id_format_und_stabilitaet():
    i = dast.befund_id("10038", "api/v1/projekte/{n}")
    assert re.fullmatch(r"B-DAST-10038-[0-9A-F]{8}", i)
    assert i == dast.befund_id("10038", "api/v1/projekte/{n}")
    assert i != dast.befund_id("10038", "api/v1/projekte")
    assert i != dast.befund_id("10021", "api/v1/projekte/{n}")


def test_befund_ist_pfadgebunden_neuer_pfad_blockiert():
    befund = _befund("10038", "assets/app.js")
    assert dast.dast_gate([_alarm("10038", pfade=("assets/app.js",))], [befund], []) == []
    neu = dast.dast_gate([_alarm("10038", pfade=("assets/app.js", "api/v1/projekte"))],
                         [befund], [])
    assert len(neu) == 1 and dast.befund_id("10038", "api/v1/projekte") in neu[0]


def test_befund_gilt_fuer_wechselnde_ids_im_pfad():
    befund = _befund("10038", "api/v1/projekte/{n}")
    alarm = _alarm("10038", pfade=("api/v1/projekte/1", "api/v1/projekte/77"))
    assert dast.dast_gate([alarm], [befund], []) == []


def test_behobener_befund_deckt_erneuten_alarm_nicht():
    behoben = _befund("10038", "", status="behoben", fix_commit="abc1234", retest="x")
    assert dast.dast_gate([_alarm("10038")], [behoben], [])
    assert dast.dast_gate([_alarm("10038")], [_befund("10038", "", status="kein-befund")], []) == []


# --- Ablauf: lesend -------------------------------------------------------------------------------

def test_ablauf_ist_rein_passiv_und_sendet_keine_formulare(tmp_path):
    welt = Welt()
    _lauf(tmp_path, welt)
    jobs = json.loads(welt.ablauf)["jobs"]
    erlaubt = {"passiveScan-config", "spider", "passiveScan-wait", "export", "report"}
    assert {j["type"] for j in jobs} <= erlaubt
    spider = [j for j in jobs if j["type"] == "spider"]
    assert len(spider) >= 2
    for j in spider:
        assert j["parameters"]["processForm"] is False
        assert j["parameters"]["postForm"] is False
    assert "activeScan" not in welt.ablauf and "ajaxSpider" not in welt.ablauf

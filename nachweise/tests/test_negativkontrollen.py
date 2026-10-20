"""Negativkontrollen: jede Kontrolle muss mit **genau ihrer** Regel abbrechen;
ein Abbruch aus anderem Grund oder gar keiner zählt als gescheitert."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scanview_nachweise import fuzzing, sast, scan
from scanview_nachweise import negativkontrollen as nk
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.werkzeuge import lade_werkzeuge

WURZEL = Path(__file__).resolve().parents[2]
UHR = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _kontext(tmp_path: Path, **abweichung: object) -> nk.Kontext:
    felder: dict[str, object] = dict(
        wurzel=WURZEL, cache=tmp_path / "cache",
        werkzeuge=lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json"),
        arbeitsordner=tmp_path / "arbeit", uhr=UHR)
    felder.update(abweichung)
    return nk.Kontext(**felder)  # type: ignore[arg-type]


def _wirft(regel: str, text: str = "x"):
    def lauf(_k: nk.Kontext) -> None:
        raise NachweisFehler(regel, text)
    return lauf


def test_nur_die_erwartete_regel_zaehlt(tmp_path):
    kontrollen = (
        nk.Kontrolle("gut", "b", "scan-gate", "CVE-1", _wirft("scan-gate", "BLOCKIERT CVE-1")),
        nk.Kontrolle("andere-regel", "b", "db-alter", None, _wirft("werkzeuge")),
        nk.Kontrolle("anderer-text", "b", "scan-gate", "CVE-1", _wirft("scan-gate", "CVE-2")),
        nk.Kontrolle("voraussetzung", "b", "db-alter", None,
                     _wirft("negativkontrolle-voraussetzung")),
        nk.Kontrolle("kein-abbruch", "b", "signatur", None, lambda k: None),
    )
    ausgabe = tmp_path / "negativkontrollen.json"
    with pytest.raises(NachweisFehler) as e:
        nk.ausfuehren(_kontext(tmp_path), ausgabe=ausgabe, kontrollen=kontrollen)
    assert e.value.regel == "negativkontrollen"
    for name in ("andere-regel", "anderer-text", "voraussetzung", "kein-abbruch"):
        assert name in e.value.text
    bericht = json.loads(ausgabe.read_text(encoding="utf-8"))
    assert bericht["ergebnis"] == "gescheitert"
    ergebnisse = {n: v["ergebnis"] for n, v in bericht["kontrollen"].items()}
    assert ergebnisse == {"gut": "abgebrochen wie erwartet", "andere-regel": "falscher Grund",
                          "anderer-text": "falscher Grund", "voraussetzung": "falscher Grund",
                          "kein-abbruch": "nicht abgebrochen"}


def test_alle_wie_erwartet_bestanden_und_nie_ueberschreiben(tmp_path):
    kontrollen = (nk.Kontrolle("a", "b", "signatur", None, _wirft("signatur")),)
    ausgabe = tmp_path / "negativkontrollen.json"
    bericht = nk.ausfuehren(_kontext(tmp_path), ausgabe=ausgabe, kontrollen=kontrollen)
    assert bericht["ergebnis"] == "bestanden" and bericht["schema"] == nk.SCHEMA
    assert json.loads(ausgabe.read_text(encoding="utf-8")) == bericht
    assert "nachweise_baum" in bericht  # Git-Baum von nachweise/ (im Git-Checkout gesetzt)
    with pytest.raises(NachweisFehler, match="existiert schon"):
        nk.ausfuehren(_kontext(tmp_path), ausgabe=ausgabe, kontrollen=kontrollen)


def test_kontrollen_laut_spec():
    assert [k.name for k in nk.KONTROLLEN] == [
        "verwundbar", "db-veraltet", "sbom-fehlt", "unzugeordnete-datei",
        "vex-under-investigation", "testsecret-im-export", "manifest-veraendert",
        "unsignierter-commit", "npm-signatur-ungueltig", "sast-fund-unregistriert",
        "fuzzing-serverfehler", "egress-fremdanfrage", "egress-bundle-url", "ruff-blind-except"]
    assert len(nk.KONTROLLEN) == 14


def _kontrolle(name: str) -> nk.Kontrolle:
    return next(k for k in nk.KONTROLLEN if k.name == name)


def test_unsignierter_commit_bricht_wie_erwartet_ab(tmp_path):
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle("unsignierter-commit"))
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis
    assert "NACHWEIS-FEHLER commit-signatur" in ergebnis["meldung"]


def test_unsignierter_commit_ohne_abbruch_zaehlt_nicht(tmp_path, monkeypatch):
    """Lässt die Prüfung den unsignierten Kopf durch, ist die Kontrolle gescheitert."""
    monkeypatch.setattr(nk.signaturen, "pruefe_seit_stichtag", lambda *a: 2)
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle("unsignierter-commit"))
    assert ergebnis["ergebnis"] == "nicht abgebrochen"


def test_unsignierter_commit_signierter_stichtag_muss_bestehen(tmp_path, monkeypatch):
    """Scheitert schon der signierte Stichtag (Anker oder Signieren kaputt), ist das kein
    erwarteter Abbruch, sondern eine verletzte Voraussetzung."""
    def immer_rot(*_a: object) -> int:
        raise NachweisFehler("commit-signatur", "x: N (unsigniert)")
    monkeypatch.setattr(nk.signaturen, "pruefe_seit_stichtag", immer_rot)
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle("unsignierter-commit"))
    assert ergebnis["ergebnis"] == "falscher Grund"
    assert "negativkontrolle-voraussetzung" in ergebnis["meldung"]


def test_npm_signatur_ungueltig_bricht_wie_erwartet_ab(tmp_path):
    """Die Kontrolle prüft den spezifischen Text „ungültige Registry-Signatur“ — der steht
    nur im unbedingten `invalid`-Abbruch, nicht im ausnahmefähigen `missing`-Zweig. Eine trotzdem
    mitgegebene Ausnahme für dasselbe Paket bleibt wirkungslos (siehe `_npm_signatur_ungueltig`);
    liefe `invalid` versehentlich über den `missing`-Zweig, gäbe die Ausnahme es durch und die
    Kontrolle bräche gar nicht erst ab."""
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle("npm-signatur-ungueltig"))
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis
    assert "NACHWEIS-FEHLER npm-signaturen" in ergebnis["meldung"]
    assert "ungültige Registry-Signatur: boese-paket@1.2.3" in ergebnis["meldung"]


def test_veraltete_db_ist_keine_bestandene_db_kontrolle(tmp_path, monkeypatch):
    def alt(cache: Path, jetzt: datetime) -> scan.DbStand:
        raise NachweisFehler("db-alter", "trivy-DB ist 30 h alt")
    monkeypatch.setattr(scan, "db_pruefen", alt)
    k = _kontext(tmp_path)
    ergebnis = nk._eine(k, nk.KONTROLLEN[1])
    assert ergebnis["ergebnis"] == "falscher Grund"
    assert "negativkontrolle-voraussetzung" in ergebnis["meldung"]
    # Ebenso vor dem Scan: ohne frische DB kein Scan-Gate-Nachweis.
    assert nk._eine(k, nk.KONTROLLEN[0])["ergebnis"] == "falscher Grund"


def test_fixture_ist_bekannt_verwundbar_und_nur_test():
    bom = json.loads((WURZEL / nk.FIXTURE).read_text(encoding="utf-8"))
    assert [c["purl"] for c in bom["components"]] == [nk.VERWUNDBAR_PURL]
    assert bom["specVersion"] == "1.6" and "vulnerabilities" not in bom
    # Nie im Produkt: kein Lockfile des Repos nennt die Komponente.
    for lock in ("web/package-lock.json", "server/uv.lock", "mcp-server/uv.lock"):
        assert '"node_modules/lodash"' not in (WURZEL / lock).read_text(encoding="utf-8")


def test_kein_token_literal_im_quelltext():
    quelltext = (WURZEL / "nachweise/src/scanview_nachweise/negativkontrollen.py").read_text(
        encoding="utf-8")
    assert "sv_" not in quelltext


@pytest.mark.parametrize("name", ["sbom-fehlt", "unzugeordnete-datei", "manifest-veraendert"])
def test_kontrollen_ohne_docker_echt(tmp_path, name):
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == name)
    ergebnis = nk._eine(_kontext(tmp_path), kontrolle)
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis


def test_unzugeordnete_datei_nutzt_ausnahmen_des_worktrees(tmp_path):
    # Mit einer Ausnahme für die Testdatei im geprüften Stand bricht die Kontrolle nicht ab —
    # also liest sie die echten Ausnahmen (sonst bewiese sie nichts über den Release-Stand).
    wt = tmp_path / "wt"
    (wt / "nachweise").mkdir(parents=True)
    (wt / nk.FIXTURE).parent.mkdir(parents=True)
    (wt / nk.FIXTURE).write_bytes((WURZEL / nk.FIXTURE).read_bytes())
    (wt / "nachweise" / "abdeckung-ausnahmen.toml").write_text(
        '[[ausnahme]]\nmuster = "web/dist/fremd.bin"\n'
        'begruendung = "Testausnahme mit ausreichend langer Begründung"\n', encoding="utf-8")
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "unzugeordnete-datei")
    assert nk._eine(_kontext(tmp_path, wurzel=wt), kontrolle)["ergebnis"] == "nicht abgebrochen"


def test_vex_kontrolle_verlangt_freigabe_durch_not_affected(tmp_path, monkeypatch):
    # Gibt schon not_affected nicht frei, beweist das Blockieren bei under_investigation nichts.
    monkeypatch.setattr(nk, "_db_frisch", lambda k: None)
    monkeypatch.setattr(nk.scan, "trivy_json", lambda *a, **kw: {})
    monkeypatch.setattr(nk.scan, "werte_trivy_aus", lambda d, a: [])
    monkeypatch.setattr(nk.vex, "treffer_gate",
                        lambda *a, **kw: [f"{nk.VERWUNDBAR_ID} (hoch) blockiert immer"])
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "vex-under-investigation")
    ergebnis = nk._eine(_kontext(tmp_path), kontrolle)
    assert ergebnis["ergebnis"] == "falscher Grund"
    assert "negativkontrolle-voraussetzung" in ergebnis["meldung"]


@pytest.mark.parametrize("code", [125, 1])
def test_testsecret_werkzeugfehler_ist_kein_erwarteter_abbruch(tmp_path, monkeypatch, code):
    """Ein gitleaks-/Docker-Fehler (z. B. Exit 125) darf die Geheimnis-Kontrolle
    nicht bestehen lassen — nur ein bestätigter Fund der sv_-Regel zählt."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    (stub / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}:/usr/bin:/bin")
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "testsecret-im-export")
    assert kontrolle.enthaelt == "scanview-api-token"
    assert nk._eine(_kontext(tmp_path), kontrolle)["ergebnis"] == "falscher Grund"


def test_testsecret_fund_einer_anderen_regel_zaehlt_nicht(tmp_path, monkeypatch):
    def fund(ziel, w, k):
        raise NachweisFehler("export-geheimnis", "1 Fund(e), Regeln generic-api-key: x.py")
    monkeypatch.setattr(nk.export, "pruefe_geheimnisse", fund)
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "testsecret-im-export")
    assert nk._eine(_kontext(tmp_path), kontrolle)["ergebnis"] == "falscher Grund"


def test_cli_ausgabe_existiert_schon_nichts_ausgegeben(tmp_path, capsys):
    from scanview_nachweise import cli
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps({"kontrollen": {"x": {"ergebnis": "ALT", "erwartet": "y"}}}),
                   encoding="utf-8")
    code = cli.main(["pruefen", "negativkontrollen", "--wurzel", str(WURZEL), "--ausgabe",
                     str(alt), "--arbeitsordner", str(tmp_path / "nk")])
    assert code == 2
    aus = capsys.readouterr()
    assert "ALT" not in aus.out and "existiert schon" in aus.err


def test_manifest_kontrolle_greift_auch_ohne_signatur(tmp_path, monkeypatch):
    # Gelänge die Prüfung ohne .sig, wäre die Kontrolle gescheitert (nicht „erwartet“).
    original = nk.manifest.pruefe_signatur
    aufrufe: list[int] = []

    def pruefe(pfad: Path, signers: Path) -> None:
        aufrufe.append(1)
        if len(aufrufe) == 3:  # dritter Aufruf: ohne Signatur → fälschlich gültig
            return None
        return original(pfad, signers)

    monkeypatch.setattr(nk.manifest, "pruefe_signatur", pruefe)
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "manifest-veraendert")
    assert nk._eine(_kontext(tmp_path), kontrolle)["ergebnis"] == "falscher Grund"


def test_cli_meldet_gescheiterte_kontrolle_exit_2(tmp_path, monkeypatch, capsys):
    from scanview_nachweise import cli
    monkeypatch.setattr(nk, "KONTROLLEN", (
        nk.Kontrolle("a", "b", "signatur", None, _wirft("signatur")),
        nk.Kontrolle("b", "b", "db-alter", None, lambda k: None)))
    code = cli.main(["pruefen", "negativkontrollen", "--wurzel", str(WURZEL),
                     "--arbeitsordner", str(tmp_path / "nk")])
    assert code == 2
    aus = capsys.readouterr()
    assert "a: abgebrochen wie erwartet" in aus.out and "b: nicht abgebrochen" in aus.out
    assert "NACHWEIS-FEHLER negativkontrollen" in aus.err
    assert json.loads((tmp_path / "nk" / nk.DATEI).read_text("utf-8"))["ergebnis"] == \
        "gescheitert"


_PRIVAT = pytest.mark.skipif(not (WURZEL / ".gitleaks.toml").exists(),
                             reason="gitleaks-Konfiguration liegt nur im privaten Repo (nicht Teil "
                                    "des öffentlichen Exports)")


def _db_frisch() -> bool:
    try:
        scan.db_pruefen(scan.STANDARD_CACHE, scan.jetzt())
    except NachweisFehler:
        return False
    return True


@_PRIVAT
@pytest.mark.docker
def test_testsecret_echt_mit_gitleaks(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker fehlt")
    kontrolle = next(k for k in nk.KONTROLLEN if k.name == "testsecret-im-export")
    ergebnis = nk._eine(_kontext(tmp_path), kontrolle)
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis


def _sast_attrappen(monkeypatch: pytest.MonkeyPatch, werkzeuge_ab_mittel: set[str]) -> None:
    def fund(werkzeug: str) -> sast.Fund:
        schwere = "hoch" if werkzeug in werkzeuge_ab_mittel else "niedrig"
        return sast.Fund(werkzeug, "r", f"{sast.FIXTURE_ORDNER}/verwundbar.py", 1,  # type: ignore[arg-type]
                         schwere, (werkzeug[0] * 16).replace("s", "5"), "t")  # type: ignore[arg-type]

    monkeypatch.setattr(nk.sast, "semgrep_laufen", lambda *a, **kw: ([fund("semgrep")], 2))
    monkeypatch.setattr(nk.sast, "bandit_laufen", lambda *a, **kw: ([fund("bandit")], 1))


def _sast_kontrolle() -> nk.Kontrolle:
    return next(k for k in nk.KONTROLLEN if k.name == "sast-fund-unregistriert")


@_PRIVAT
def test_sast_kontrolle_blockiert_mit_funden_beider_werkzeuge(tmp_path, monkeypatch):
    _sast_attrappen(monkeypatch, {"semgrep", "bandit"})
    ergebnis = nk._eine(_kontext(tmp_path), _sast_kontrolle())
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis


@_PRIVAT
@pytest.mark.parametrize("nur", ["semgrep", "bandit"])
def test_sast_kontrolle_ohne_mittleren_fund_eines_werkzeugs_zaehlt_nicht(tmp_path, monkeypatch,
                                                                       nur):
    # Blockiert zwar (niedriger Fund ohne Ausnahme), bewiese aber nichts über das andere Werkzeug.
    _sast_attrappen(monkeypatch, {nur})
    ergebnis = nk._eine(_kontext(tmp_path), _sast_kontrolle())
    assert ergebnis["ergebnis"] == "falscher Grund", ergebnis


def test_sast_kontrolle_werkzeugfehler_ist_kein_erwarteter_abbruch(tmp_path, monkeypatch):
    def fehlt(*_a: object, **_kw: object) -> None:
        raise NachweisFehler("sast-werkzeug", "bandit nicht installiert")
    monkeypatch.setattr(nk.sast, "semgrep_laufen", lambda *a, **kw: ([], 1))
    monkeypatch.setattr(nk.sast, "bandit_laufen", fehlt)
    ergebnis = nk._eine(_kontext(tmp_path), _sast_kontrolle())
    assert ergebnis["ergebnis"] == "falscher Grund"
    assert "negativkontrolle-voraussetzung" in ergebnis["meldung"]


@_PRIVAT
@pytest.mark.docker
def test_sast_kontrolle_echt(tmp_path):
    if shutil.which("docker") is None:
        pytest.fail("docker fehlt")
    werkzeuge = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    sast.bandit_bereitstellen(WURZEL / "nachweise", werkzeuge)
    ergebnis = nk._eine(_kontext(tmp_path), _sast_kontrolle())
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis


def _fuzzing_kontrolle() -> nk.Kontrolle:
    return next(k for k in nk.KONTROLLEN if k.name == "fuzzing-serverfehler")


def _fuzzing_attrappe(monkeypatch: pytest.MonkeyPatch, fehler: list[str]) -> list[str]:
    urls: list[str] = []

    def laufen(inst: fuzzing.Testinstanz, identitaet: str, *a: object,
               **kw: object) -> fuzzing.FuzzErgebnis:
        urls.append(inst.url)
        return fuzzing.FuzzErgebnis(identitaet, 2, 2, 0, fehler)

    monkeypatch.setattr(nk.fuzzing, "schemathesis_laufen", laufen)
    return urls


def test_fuzzing_kontrolle_blockiert_mit_der_kaputten_operation(tmp_path, monkeypatch):
    urls = _fuzzing_attrappe(monkeypatch, ["GET /api/v1/kaputt: Server error"])
    ergebnis = nk._eine(_kontext(tmp_path), _fuzzing_kontrolle())
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis
    # Der kaputte Dienst lief auf einem freien Loopback-Port, nicht auf der echten Instanz.
    assert urls and urls[0].startswith("http://127.0.0.1:") and "18097" not in urls[0]


@pytest.mark.parametrize("fehler", [[], ["GET /api/v1/gut: Server error",
                                         "GET /api/v1/kaputt: Server error"]])
def test_fuzzing_kontrolle_falscher_befund_zaehlt_nicht(tmp_path, monkeypatch, fehler):
    # Kein Fehlschlag bewiese nichts; ein Fehlschlag der gesunden Operation hieße, dass der Lauf
    # etwas anderes misst als den Serverfehler.
    _fuzzing_attrappe(monkeypatch, fehler)
    ergebnis = nk._eine(_kontext(tmp_path), _fuzzing_kontrolle())
    assert ergebnis["ergebnis"] == "falscher Grund", ergebnis


@_PRIVAT
@pytest.mark.docker
def test_fuzzing_kontrolle_echt(tmp_path):
    if shutil.which("docker") is None:
        pytest.fail("docker fehlt")
    werkzeuge = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    fuzzing.schemathesis_bereitstellen(WURZEL / "nachweise", werkzeuge)
    ergebnis = nk._eine(_kontext(tmp_path), _fuzzing_kontrolle())
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis


@pytest.mark.parametrize(("name", "enthaelt"), [
    ("egress-fremdanfrage", "https://cdn.example.org/x.js"),
    ("egress-bundle-url", "tiles.example.org")])
def test_egress_kontrollen_brechen_wie_erwartet_ab(tmp_path, name, enthaelt):
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle(name))
    assert ergebnis["ergebnis"] == "abgebrochen wie erwartet", ergebnis
    assert "NACHWEIS-FEHLER egress-gate" in ergebnis["meldung"]
    assert enthaelt in ergebnis["meldung"]
    assert _kontrolle(name).regel == "egress-gate"


def test_egress_fremdanfrage_ohne_fremdes_ziel_zaehlt_nicht(tmp_path):
    """Lässt die Auswertung nichts durch die Sperre, wäre die Kontrolle wirkungslos: die
    Fixture ohne fremde Anfragen ergibt keinen Abbruch mit der Regel des Gates."""
    wurzel = tmp_path / "wurzel"
    fixture = wurzel / nk.EGRESS_ANFRAGEN_FIXTURE
    fixture.parent.mkdir(parents=True)
    sauber = (WURZEL / nk.EGRESS_ANFRAGEN_FIXTURE.parent / "anfragen_sauber.json")
    shutil.copy(sauber, fixture)
    ergebnis = nk._eine(_kontext(tmp_path, wurzel=wurzel), _kontrolle("egress-fremdanfrage"))
    assert ergebnis["ergebnis"] == "falscher Grund", ergebnis
    assert "zwei blockierende Meldungen erwartet" in ergebnis["meldung"]


def test_egress_fremdanfrage_ohne_kontrollanfrage_ist_falscher_grund(tmp_path):
    wurzel = tmp_path / "wurzel"
    fixture = wurzel / nk.EGRESS_ANFRAGEN_FIXTURE
    fixture.parent.mkdir(parents=True)
    shutil.copy(WURZEL / nk.EGRESS_ANFRAGEN_FIXTURE.parent / "anfragen_ohne_kontrolle.json",
                fixture)
    ergebnis = nk._eine(_kontext(tmp_path, wurzel=wurzel), _kontrolle("egress-fremdanfrage"))
    assert ergebnis["ergebnis"] == "falscher Grund", ergebnis
    assert "egress-erfassung" in ergebnis["meldung"]


def test_egress_bundle_url_mit_sauberem_bundle_zaehlt_nicht(tmp_path, monkeypatch):
    monkeypatch.setattr(nk, "EGRESS_BUNDLE_FIXTURE",
                        nk.EGRESS_BUNDLE_FIXTURE.parent / "bundle_sauber")
    ergebnis = nk._eine(_kontext(tmp_path), _kontrolle("egress-bundle-url"))
    assert ergebnis["ergebnis"] == "falscher Grund", ergebnis


def test_egress_bundle_url_ohne_fixture_ist_voraussetzung(tmp_path):
    ergebnis = nk._eine(_kontext(tmp_path, wurzel=tmp_path), _kontrolle("egress-bundle-url"))
    assert ergebnis["ergebnis"] == "falscher Grund"
    assert "negativkontrolle-voraussetzung" in ergebnis["meldung"]


@_PRIVAT
@pytest.mark.docker
def test_alle_kontrollen_echt(tmp_path):
    # Braucht eine trivy-DB ≤ 24 h. Hier (Entwickler-Qualitätstor) übersprungen, wenn sie älter
    # ist; die Release-Kette führt die Kontrollen ohne Ausnahme aus und bricht dann ab.
    if not _db_frisch():
        pytest.skip("trivy-DB nicht aktuell — `scanview-nachweise db-aktualisieren`")
    # Bandit und Schemathesis wie beim Einfrieren bereitstellen (Netzschritt nur beim ersten Mal).
    sast.bandit_bereitstellen(WURZEL / "nachweise", lade_werkzeuge(
        WURZEL / "nachweise" / "werkzeuge.json"))
    fuzzing.schemathesis_bereitstellen(WURZEL / "nachweise", lade_werkzeuge(
        WURZEL / "nachweise" / "werkzeuge.json"))
    k = _kontext(tmp_path, cache=scan.STANDARD_CACHE, uhr=scan.jetzt())
    bericht = nk.ausfuehren(k, ausgabe=tmp_path / "negativkontrollen.json")
    assert bericht["ergebnis"] == "bestanden"
    assert all(v["ergebnis"] == "abgebrochen wie erwartet"
               for v in bericht["kontrollen"].values())
    assert nk.VERWUNDBAR_ID in bericht["kontrollen"]["verwundbar"]["meldung"]


_ZAHLWOERTER = {7: "sieben", 8: "acht", 9: "neun", 10: "zehn", 11: "elf", 12: "zwölf",
                13: "dreizehn", 14: "vierzehn", 15: "fünfzehn", 16: "sechzehn"}


def test_release_doku_nennt_die_anzahl_der_kontrollen():
    """Checkliste und Stufentabelle in docs/public/release.md nennen genau die Zahl der
    Kontrollen, die die Kette ausführt, und die Tabelle hat je Kontrolle eine Zeile."""
    text = (WURZEL / "docs" / "public" / "release.md").read_text(encoding="utf-8")
    zahl = _ZAHLWOERTER[len(nk.KONTROLLEN)]
    checkliste = re.search(r"`scanview-nachweise pruefen negativkontrollen`\. (\w+) feste Fälle",
                           text)
    stufe = re.search(r"\| negativkontrollen \| die (\w+) Negativkontrollen", text)
    assert checkliste and checkliste.group(1).lower() == zahl
    assert stufe and stufe.group(1).lower() == zahl
    abschnitt = text.split("## Negativkontrollen", 1)[1].split("\n## ", 1)[0]
    zeilen = [z for z in abschnitt.splitlines() if z.startswith("| ")][1:]
    assert len(zeilen) == len(nk.KONTROLLEN)


def test_portzeile_mit_zeitlimit():
    """Ein Fixture-Dienst, der keinen Port meldet, hängt die Kette nicht auf."""
    import subprocess
    import sys
    stumm = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    try:
        with pytest.raises(NachweisFehler, match="keinen Port"):
            nk._portzeile(stumm, sekunden=0.2)
    finally:
        stumm.kill()
        stumm.wait()
    redet = subprocess.Popen([sys.executable, "-c", "print(4711, flush=True)"],
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    try:
        assert nk._portzeile(redet, sekunden=10) == 4711
    finally:
        redet.wait()

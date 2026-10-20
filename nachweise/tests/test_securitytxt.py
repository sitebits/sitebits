"""security.txt nach RFC 9116: Erzeugen (unsigniert) und Prüfen (OpenPGP-Klartextsignatur).

Die OpenPGP-Schlüssel entstehen im Test in einem temporären GNUPGHOME (`gpg --batch
--quick-gen-key`); der echte Schlüsselbund wird nie berührt, nichts davon wird eingecheckt."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import signierhilfe
import werkzeugpflicht

from scanview_nachweise import cli, securitytxt
from scanview_nachweise.fehler import NachweisFehler

REPO_WURZEL = Path(__file__).resolve().parents[2]
VORLAGE = REPO_WURZEL / "deploy" / "platform" / "security.txt.vorlage"
FIXTURES = Path(__file__).parent / "fixtures" / "securitytxt"
OFFEN = FIXTURES / "SECURITY-offen.md"
FERTIG = FIXTURES / "SECURITY-fertig.md"

HEUTE = date(2026, 10, 1)
URL = "https://scanview.example.invalid/.well-known/security.txt"
ARGS = {
    "kontakt": "security@example.invalid",
    "schluessel_url": "https://scanview.example.invalid/.well-known/psirt-openpgp.asc",
    "kanonisch": URL,
    "richtlinie_url": "https://scanview.example.invalid/.well-known/sicherheitsrichtlinie.txt",
    "heute": HEUTE,
}


@pytest.fixture(scope="module", autouse=True)
def _gpg_vorhanden() -> None:
    """Ohne gpg laufen die Signaturtests nicht — das fällt laut auf (siehe `werkzeugpflicht`)."""
    werkzeugpflicht.verlange("gpg", shutil.which("gpg") is not None,
                             "Signaturprüfung der security.txt")


def _gpg(heim: Path, *args: str, eingabe: str | None = None) -> str:
    return subprocess.run(["gpg", "--homedir", str(heim), "--batch", "--no-tty", *args],
                          check=True, capture_output=True, text=True, input=eingabe,
                          stdin=None if eingabe is not None else subprocess.DEVNULL).stdout


class Schluessel:
    def __init__(self, heim: Path, oeffentlich: Path, fingerabdruck: str) -> None:
        self.heim = heim
        self.oeffentlich = oeffentlich
        self.fingerabdruck = fingerabdruck

    def signiere(self, text: str) -> str:
        # Schlüssel ausdrücklich wählen: die Wahl des Standard-Schlüssels scheitert mit manchen
        # gpg/libgcrypt-Ständen („no default secret key“), obwohl der Schlüssel da ist.
        return _gpg(self.heim, "--pinentry-mode", "loopback", "--passphrase", "",
                    "--local-user", self.fingerabdruck, "--clearsign", eingabe=text)


def _neuer_schluessel(name: str, ablauf: str = "never") -> Iterator[Schluessel]:
    # kurzer Pfad: der gpg-agent legt seinen Socket im GNUPGHOME an (Pfadlänge begrenzt)
    heim = Path(tempfile.mkdtemp(prefix="sv-gpg-"))
    try:
        heim.chmod(0o700)
        _gpg(heim, "--pinentry-mode", "loopback", "--passphrase", "", "--quick-gen-key",
             f"{name} <{name}@example.invalid>", "ed25519", "sign", ablauf)
        asc = heim / "oeffentlich.asc"
        asc.write_text(_gpg(heim, "--armor", "--export"), encoding="utf-8")
        zeilen = _gpg(heim, "--list-secret-keys", "--with-colons").splitlines()
        fpr = next(z.split(":")[9] for z in zeilen if z.startswith("fpr:"))
        yield Schluessel(heim, asc, fpr)
    finally:
        subprocess.run(["gpgconf", "--homedir", str(heim), "--kill", "all"], check=False,
                       capture_output=True)
        shutil.rmtree(heim, ignore_errors=True)


@pytest.fixture(scope="module")
def schluessel() -> Iterator[Schluessel]:
    yield from _neuer_schluessel("psirt")


@pytest.fixture(scope="module")
def fremder() -> Iterator[Schluessel]:
    yield from _neuer_schluessel("fremd")


def _roh(**ueberschreiben: object) -> str:
    args = {**ARGS, **ueberschreiben}
    return securitytxt.erzeuge(VORLAGE, FERTIG, **args)  # type: ignore[arg-type]


# --- erzeuge -------------------------------------------------------------------------------------

def test_erzeuge_verweigert_bei_postfach_marker() -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.erzeuge(VORLAGE, OFFEN, **ARGS)  # type: ignore[arg-type]
    assert e.value.regel == "securitytxt-postfach"


def test_erzeuge_verweigert_bei_fehlender_security_md(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.erzeuge(VORLAGE, tmp_path / "gibt-es-nicht.md", **ARGS)  # type: ignore[arg-type]
    assert e.value.regel == "securitytxt-postfach"


def test_erzeuge_alle_felder_in_fester_reihenfolge() -> None:
    text = _roh()
    ablauf = (HEUTE + timedelta(days=180)).isoformat() + "T00:00:00Z"
    assert text == (
        "Contact: mailto:security@example.invalid\n"
        f"Expires: {ablauf}\n"
        f"Encryption: {ARGS['schluessel_url']}\n"
        "Preferred-Languages: de, en\n"
        f"Canonical: {URL}\n"
        f"Policy: {ARGS['richtlinie_url']}\n")
    assert ablauf == "2027-03-30T00:00:00Z"
    assert "@K" not in text and "-----BEGIN" not in text  # unsigniert


@pytest.mark.parametrize("tage", [0, -1, 365, 400])
def test_erzeuge_verweigert_ungueltige_laufzeit(tage: int) -> None:
    with pytest.raises(NachweisFehler) as e:
        _roh(tage=tage)
    assert e.value.regel == "securitytxt-eingabe"


def test_erzeuge_laufzeit_364_erlaubt() -> None:
    assert f"Expires: {(HEUTE + timedelta(days=364)).isoformat()}T00:00:00Z\n" in _roh(tage=364)


@pytest.mark.parametrize("feld,wert", [
    ("kanonisch", "http://scanview.example.invalid/.well-known/security.txt"),
    ("kanonisch", "https://scanview.example.invalid/security.txt"),
    ("schluessel_url", "http://scanview.example.invalid/.well-known/psirt-openpgp.asc"),
    ("richtlinie_url", "ftp://scanview.example.invalid/richtlinie"),
    ("richtlinie_url", "https://x.example.invalid/a\nContact: mailto:boese@example.invalid"),
    ("kontakt", "keine-adresse"),
    ("kontakt", "a@example.invalid\nExpires: 2099-01-01T00:00:00Z"),
    ("kontakt", "@KONTAKT@"),
])
def test_erzeuge_verweigert_ungueltige_eingaben(feld: str, wert: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        _roh(**{feld: wert})
    assert e.value.regel == "securitytxt-eingabe"


def test_erzeuge_verweigert_uebrig_gebliebenen_platzhalter(tmp_path: Path) -> None:
    vorlage = tmp_path / "v"
    vorlage.write_text(VORLAGE.read_text(encoding="utf-8") + "Hiring: @STELLEN@\n",
                       encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.erzeuge(vorlage, FERTIG, **ARGS)  # type: ignore[arg-type]
    assert e.value.regel == "securitytxt-platzhalter"


# --- pruefe --------------------------------------------------------------------------------------

def test_pruefe_gueltig_signiert(schluessel: Schluessel) -> None:
    ergebnis = securitytxt.pruefe(schluessel.signiere(_roh()), url=URL,
                                  schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert ergebnis["signatur"] == "gueltig"
    assert ergebnis["url"] == URL
    assert ergebnis["expires"] == "2027-03-30T00:00:00Z"
    assert ergebnis["tage_bis_ablauf"] == 180
    assert ergebnis["felder"]["Preferred-Languages"] == ["de, en"]
    assert len(ergebnis["fingerabdruck"]) == 40


def test_pruefe_unsigniert(schluessel: Schluessel) -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(_roh(), url=URL, schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-signatur"


def test_pruefe_anderer_schluessel(schluessel: Schluessel, fremder: Schluessel) -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(fremder.signiere(_roh()), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-signatur"


def test_pruefe_veraenderter_text(schluessel: Schluessel) -> None:
    signiert = schluessel.signiere(_roh()).replace("Contact: mailto:security@",
                                                   "Contact: mailto:boese@")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(signiert, url=URL, schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-signatur"


def test_pruefe_text_ausserhalb_der_signatur(schluessel: Schluessel) -> None:
    signiert = schluessel.signiere(_roh())
    for text in ("Contact: mailto:boese@example.invalid\n" + signiert,
                 signiert + "Contact: mailto:boese@example.invalid\n"):
        with pytest.raises(NachweisFehler) as e:
            securitytxt.pruefe(text, url=URL, schluessel=schluessel.oeffentlich, heute=HEUTE)
        assert e.value.regel == "securitytxt-signatur"


def test_pruefe_abgelaufen(schluessel: Schluessel) -> None:
    signiert = schluessel.signiere(_roh(heute=HEUTE - timedelta(days=200)))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(signiert, url=URL, schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-ablauf"


def test_pruefe_ablauftag_selbst_ist_abgelaufen(schluessel: Schluessel) -> None:
    signiert = schluessel.signiere(_roh())
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(signiert, url=URL, schluessel=schluessel.oeffentlich,
                           heute=HEUTE + timedelta(days=180))
    assert e.value.regel == "securitytxt-ablauf"


def test_pruefe_ablauf_in_400_tagen(schluessel: Schluessel) -> None:
    roh = _roh().replace("2027-03-30", (HEUTE + timedelta(days=400)).isoformat())
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-ablauf"


def test_pruefe_ablauf_kein_datum(schluessel: Schluessel) -> None:
    roh = _roh().replace("2027-03-30T00:00:00Z", "irgendwann")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-ablauf"


@pytest.mark.parametrize("feld", ["Contact", "Encryption", "Canonical", "Policy",
                                  "Preferred-Languages"])
def test_pruefe_fehlendes_feld(schluessel: Schluessel, feld: str) -> None:
    roh = "".join(z for z in _roh().splitlines(keepends=True) if not z.startswith(f"{feld}:"))
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-feld"
    assert feld in e.value.text


def test_pruefe_falsche_sprachen(schluessel: Schluessel) -> None:
    roh = _roh().replace("Preferred-Languages: de, en", "Preferred-Languages: fr")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-feld"


def test_pruefe_canonical_passt_nicht_zur_url(schluessel: Schluessel) -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(_roh()),
                           url="https://andere.example.invalid/.well-known/security.txt",
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-feld"


def test_pruefe_platzhalter_uebrig(schluessel: Schluessel) -> None:
    roh = _roh().replace("https://scanview.example.invalid/.well-known/sicherheitsrichtlinie.txt",
                         "@RICHTLINIE_URL@")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-platzhalter"


def test_pruefe_schluessel_unlesbar(schluessel: Schluessel, tmp_path: Path) -> None:
    kaputt = tmp_path / "kaputt.asc"
    kaputt.write_text("kein Schlüssel", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(_roh()), url=URL, schluessel=kaputt, heute=HEUTE)
    assert e.value.regel == "securitytxt-signatur"


def test_pruefe_beruehrt_den_echten_schluesselbund_nicht(
        schluessel: Schluessel, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    echt = tmp_path / "echt"
    monkeypatch.setenv("GNUPGHOME", str(echt))
    securitytxt.pruefe(schluessel.signiere(_roh()), url=URL,
                       schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert not echt.exists()


# --- CLI -----------------------------------------------------------------------------------------

def test_cli_erzeugen_auf_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["securitytxt", "erzeugen", "--vorlage", str(VORLAGE),
                     "--security-md", str(FERTIG), "--kontakt", ARGS["kontakt"],  # type: ignore[list-item]
                     "--schluessel-url", ARGS["schluessel_url"],  # type: ignore[list-item]
                     "--kanonisch", URL, "--richtlinie-url", ARGS["richtlinie_url"],  # type: ignore[list-item]
                     "--heute", HEUTE.isoformat()])
    assert code == 0
    assert capsys.readouterr().out == _roh()


def test_cli_erzeugen_mit_marker_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["securitytxt", "erzeugen", "--vorlage", str(VORLAGE),
                     "--security-md", str(OFFEN), "--kontakt", ARGS["kontakt"],  # type: ignore[list-item]
                     "--schluessel-url", ARGS["schluessel_url"],  # type: ignore[list-item]
                     "--kanonisch", URL, "--richtlinie-url", ARGS["richtlinie_url"]])  # type: ignore[list-item]
    assert code == 2
    assert "securitytxt-postfach" in capsys.readouterr().err


def test_cli_pruefen_url_nur_https(schluessel: Schluessel,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    http = "http://x.example.invalid/.well-known/security.txt"
    code = cli.main(["securitytxt", "pruefen", "--url", http,
                     "--schluessel", str(schluessel.oeffentlich)])
    assert code == 2
    assert "securitytxt-eingabe" in capsys.readouterr().err


def test_cli_pruefen_archiv_braucht_instanz(schluessel: Schluessel, tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["securitytxt", "pruefen", "--url", URL,
                     "--schluessel", str(schluessel.oeffentlich), "--archiv", str(tmp_path)])
    assert code == 2
    assert "securitytxt-eingabe" in capsys.readouterr().err


def test_abrufen_ist_nur_https(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NachweisFehler) as e:
        securitytxt.abrufen("http://x.example.invalid/.well-known/security.txt")
    assert e.value.regel == "securitytxt-eingabe"


def test_cli_pruefen_schreibt_nachweis_ins_archiv(
        schluessel: Schluessel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    signiert = schluessel.signiere(_roh())
    monkeypatch.setattr(securitytxt, "abrufen", lambda url: signiert)
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    code = cli.main(["securitytxt", "pruefen", "--url", URL, "--schluessel",
                     str(schluessel.oeffentlich), "--archiv", str(archiv), "--instanz", "L",
                     "--heute", HEUTE.isoformat()])
    assert code == 0, capsys.readouterr().err
    ordner = archiv / "pruefungen" / f"{HEUTE.isoformat()}-securitytxt-L"
    assert (ordner / "security.txt").read_text(encoding="utf-8") == signiert
    assert (ordner / "psirt-openpgp.asc").is_file() and (ordner / "ergebnis.json").is_file()
    assert "pruefung: securitytxt L" in signierhilfe.git(archiv, "log", "-1", "--format=%s")


def test_cli_pruefen_ungueltig_schreibt_nichts(
        schluessel: Schluessel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(securitytxt, "abrufen", lambda url: _roh())  # unsigniert
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    code = cli.main(["securitytxt", "pruefen", "--url", URL, "--schluessel",
                     str(schluessel.oeffentlich), "--archiv", str(archiv), "--instanz", "S",
                     "--heute", HEUTE.isoformat()])
    assert code == 2 and "securitytxt-signatur" in capsys.readouterr().err
    assert not (archiv / "pruefungen").exists()


# --- Prüfregeln für Schema und Zeitbezug ---------------------------------------------------------

@pytest.mark.parametrize("alt,neu", [
    ("Contact: mailto:security@example.invalid", "Contact: security@example.invalid"),
    ("Contact: mailto:security@example.invalid", "Contact: http://example.invalid/melden"),
    ("Contact: mailto:security@example.invalid", "Contact: mailto:"),
    ("Encryption: https://scanview.example.invalid/.well-known/psirt-openpgp.asc",
     "Encryption: http://scanview.example.invalid/.well-known/psirt-openpgp.asc"),
    ("Policy: https://scanview.example.invalid/.well-known/sicherheitsrichtlinie.txt",
     "Policy: ftp://scanview.example.invalid/sicherheitsrichtlinie.txt"),
])
def test_pruefe_verlangt_schema_der_felder(schluessel: Schluessel, alt: str, neu: str) -> None:
    roh = _roh()
    assert alt in roh
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(schluessel.signiere(roh.replace(alt, neu)), url=URL,
                           schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert e.value.regel == "securitytxt-feld"


def test_pruefe_erlaubt_https_als_kontakt(schluessel: Schluessel) -> None:
    roh = _roh().replace("Contact: mailto:security@example.invalid",
                         "Contact: https://scanview.example.invalid/melden\n"
                         "Contact: mailto:security@example.invalid")
    ergebnis = securitytxt.pruefe(schluessel.signiere(roh), url=URL,
                                  schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert len(ergebnis["felder"]["Contact"]) == 2


def test_pruefe_ablauf_gegen_jetzt_statt_tagesbeginn(schluessel: Schluessel) -> None:
    """Läuft die Datei heute um 12:00 UTC ab, ist sie um 13:00 UTC abgelaufen, obwohl der
    Tagesbeginn noch davor liegt."""
    roh = _roh().replace("2027-03-30T00:00:00Z", "2026-10-01T12:00:00Z")
    signiert = schluessel.signiere(roh)
    ergebnis = securitytxt.pruefe(signiert, url=URL, schluessel=schluessel.oeffentlich,
                                  heute=HEUTE, jetzt=datetime(2026, 10, 1, 11, 0, tzinfo=UTC))
    assert ergebnis["tage_bis_ablauf"] == 0
    with pytest.raises(NachweisFehler) as e:
        securitytxt.pruefe(signiert, url=URL, schluessel=schluessel.oeffentlich, heute=HEUTE,
                           jetzt=datetime(2026, 10, 1, 13, 0, tzinfo=UTC))
    assert e.value.regel == "securitytxt-ablauf"


def test_pruefe_aufraeumen_scheitert_nicht_an_gpgconf(
        schluessel: Schluessel, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fehlt `gpgconf` oder hängt es, bleibt das Prüfergebnis erhalten (keine rohe Ausnahme)."""
    echt = subprocess.run

    def run(befehl: list[str], *args: object, **kwargs: object) -> object:
        if befehl[0] == securitytxt.GPGCONF:
            raise subprocess.TimeoutExpired(befehl, 1)
        return echt(befehl, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(securitytxt.subprocess, "run", run)
    ergebnis = securitytxt.pruefe(schluessel.signiere(_roh()), url=URL,
                                  schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert ergebnis["signatur"] == "gueltig"
    monkeypatch.setattr(securitytxt, "GPGCONF", "gibt-es-nicht-gpgconf")
    monkeypatch.setattr(securitytxt.subprocess, "run", echt)
    ergebnis = securitytxt.pruefe(schluessel.signiere(_roh()), url=URL,
                                  schluessel=schluessel.oeffentlich, heute=HEUTE)
    assert ergebnis["signatur"] == "gueltig"


def test_cli_stichtag_ist_das_utc_datum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kurz nach Mitternacht Ortszeit (UTC+2) ist es in UTC noch der Vortag."""
    class Uhr(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            assert tz is UTC
            return datetime(2026, 9, 30, 22, 30, tzinfo=UTC)

    monkeypatch.setattr(cli, "datetime", Uhr)
    args = cli.argparse.Namespace(heute=None)
    assert cli._sectxt_heute(args) == date(2026, 9, 30)

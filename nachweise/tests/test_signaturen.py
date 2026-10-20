"""Signierte Commits: der Stichtag-Commit und jeder Commit, der vom Release-Commit aus
erreichbar ist, aber weder vom Stichtag noch von einem eingefrorenen Altstand (Zweigspitzen zum
Stichtag), trägt eine gültige SSH-Signatur eines nicht widerrufenen Schlüssels aus dem
Vertrauensanker im Nachweis-Archiv."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from signierhilfe import anker, anker_dateien, commit, fingerprint, git, repo, schluessel, toml

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.signaturen import (
    Anker,
    lade_anker,
    pruefe_commit,
    pruefe_seit_stichtag,
    pruefe_signierschluessel,
)


@pytest.fixture
def umgebung(tmp_path: Path) -> tuple[Path, Anker, Path]:
    """(Repo, Anker aus Dateien, privater Schlüssel)."""
    key, zeile = schluessel(tmp_path / "schluessel")
    return repo(tmp_path / "repo", key), anker_dateien(tmp_path / "anker", zeile), key


def _ab(a: Anker, stichtag: str, *altstaende: str) -> Anker:
    return dataclasses.replace(a, stichtag=stichtag, altstaende=tuple(altstaende))


# --- pruefe_signierschluessel (Probesignatur vor dem Commit) -----------------------------------

def test_signierschluessel_im_anker(umgebung):
    r, a, key = umgebung
    assert pruefe_signierschluessel(r, a) == fingerprint(key)
    # Die .pub mit dem privaten Schlüssel daneben signiert git ebenso.
    git(r, "config", "user.signingkey", str(key.with_suffix(".pub")))
    assert pruefe_signierschluessel(r, a) == fingerprint(key)


def test_signierschluessel_relativ_und_tilde_wie_git(umgebung, monkeypatch, tmp_path):
    """git löst einen relativen Pfad ab der Repo-Wurzel auf und erweitert `~`."""
    r, a, key = umgebung
    (r / "schl").mkdir()
    for datei in (key, key.with_suffix(".pub")):
        ziel = r / "schl" / datei.name
        ziel.write_bytes(datei.read_bytes())
        ziel.chmod(0o600)
    git(r, "config", "user.signingkey", f"schl/{key.name}")
    monkeypatch.chdir(tmp_path)  # Python-cwd ist nicht das Repo
    assert pruefe_signierschluessel(r, a) == fingerprint(key)
    monkeypatch.setenv("HOME", str(key.parent))
    git(r, "config", "user.signingkey", f"~/{key.name}")
    assert pruefe_signierschluessel(r, a) == fingerprint(key)


def test_signierschluessel_privat_ohne_pub(umgebung, tmp_path):
    r, a, key = umgebung
    allein = tmp_path / "allein"
    allein.write_bytes(key.read_bytes())
    allein.chmod(0o600)
    git(r, "config", "user.signingkey", str(allein))
    assert pruefe_signierschluessel(r, a) == fingerprint(key)


def test_signierschluessel_fremd(umgebung, tmp_path):
    r, a, _ = umgebung
    fremd, _ = schluessel(tmp_path / "fremd")
    git(r, "config", "user.signingkey", str(fremd))
    with pytest.raises(NachweisFehler, match="nicht im Vertrauensanker") as e:
        pruefe_signierschluessel(r, a)
    assert e.value.regel == "commit-signatur"


def test_signierschluessel_gesperrt(tmp_path):
    key, zeile = schluessel(tmp_path / "schluessel")
    r = repo(tmp_path / "repo", key)
    pub = key.with_suffix(".pub").read_text(encoding="utf-8")
    a = anker_dateien(tmp_path / "anker", zeile, widerrufen="# Sperrliste\n" + pub)
    with pytest.raises(NachweisFehler, match="Probesignatur von ssh-keygen gegen Anker/"
                                             "Sperrdatei abgelehnt") as e:
        pruefe_signierschluessel(r, a)
    assert e.value.regel == "commit-signatur"


def test_sperrdatei_mit_crlf_sperrt_wie_ssh_keygen(tmp_path):
    """CRLF-Zeilenenden ändern nichts: der eigene Schlüssel bleibt gesperrt, für Probe und
    Commit-Prüfung gleich."""
    key, zeile = schluessel(tmp_path / "schluessel")
    r = repo(tmp_path / "repo", key)
    pub = key.with_suffix(".pub").read_text(encoding="utf-8").rstrip("\n")
    a = anker_dateien(tmp_path / "anker", zeile, widerrufen=f"# Sperrliste\r\n{pub}\r\n")
    with pytest.raises(NachweisFehler, match="Probesignatur von ssh-keygen gegen Anker/"
                                             "Sperrdatei abgelehnt"):
        pruefe_signierschluessel(r, a)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, commit(r, "x", signiert=True), a)
    assert e.value.regel == "commit-signatur" and ": B (" in e.value.text


def test_signierschluessel_gesperrt_krl(tmp_path):
    import subprocess
    key, zeile = schluessel(tmp_path / "schluessel")
    andere, _ = schluessel(tmp_path / "andere")
    r = repo(tmp_path / "repo", key)
    a = anker_dateien(tmp_path / "anker", zeile)
    krl = tmp_path / "krl"
    subprocess.run(["ssh-keygen", "-k", "-f", str(krl), str(andere.with_suffix(".pub"))],
                   check=True, capture_output=True)
    a.widerrufen.write_bytes(krl.read_bytes())
    assert pruefe_signierschluessel(r, a) == fingerprint(key)
    subprocess.run(["ssh-keygen", "-k", "-f", str(krl), str(key.with_suffix(".pub"))],
                   check=True, capture_output=True)
    a.widerrufen.write_bytes(krl.read_bytes())
    with pytest.raises(NachweisFehler, match="Probesignatur von ssh-keygen gegen Anker/"
                                             "Sperrdatei abgelehnt"):
        pruefe_signierschluessel(r, a)


def test_signierschluessel_falscher_namensraum_nicht_als_gesperrt_gemeldet(tmp_path):
    """Auch ein falscher Namensraum lässt die Probe scheitern; die Meldung behauptet dann keine
    Sperre."""
    key, _ = schluessel(tmp_path / "schluessel")
    r = repo(tmp_path / "repo", key)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    a = anker_dateien(tmp_path / "anker", f't@x namespaces="file" {typ} {wert}\n')
    with pytest.raises(NachweisFehler, match="Probesignatur von ssh-keygen gegen Anker/"
                                             "Sperrdatei abgelehnt") as e:
        pruefe_signierschluessel(r, a)
    assert "gesperrt" not in e.value.text and e.value.regel == "commit-signatur"


@pytest.mark.parametrize("praefix", ['t@x namespaces="git" ', "cert-authority "])
def test_sperrdatei_mit_principal_oder_option_ist_ankerfehler(tmp_path, praefix):
    """Eine aus allowed_signers kopierte Zeile ließe ssh-keygen jede Signatur ablehnen — auch
    die eines nicht gesperrten Schlüssels. Das fällt beim Prüfen auf, nicht erst nach dem
    Commit."""
    key, zeile = schluessel(tmp_path / "schluessel")
    andere, _ = schluessel(tmp_path / "andere")
    r = repo(tmp_path / "repo", key)
    fremde_zeile = praefix + andere.with_suffix(".pub").read_text(encoding="utf-8")
    a = anker_dateien(tmp_path / "anker", zeile, widerrufen=fremde_zeile)
    with pytest.raises(NachweisFehler, match="kein öffentlicher Schlüssel") as e:
        pruefe_signierschluessel(r, a)
    assert e.value.regel == "commit-signatur-anker"


@pytest.mark.parametrize("vorsatz", ["\x0c", "\x0b", "\x1c", "\u00a0"])
@pytest.mark.parametrize("art", ["schluessel", "kommentar", "leer"])
def test_sperrzeile_nur_mit_leerzeichen_und_tab_eingerueckt(tmp_path, vorsatz, art):
    """ssh-keygen trennt die Sperrdatei nur an `\\n` und überspringt am Zeilenanfang nur
    Leerzeichen und Tab: Formfeed, vertikaler Tab, `\\x1c` oder NBSP davor machen aus der Zeile
    einen ungültigen Eintrag, und ssh-keygen lehnt jede Signatur ab. Die Zeilenprüfung meldet
    das als Ankerfehler mit Zeilennummer, nicht erst die Probe als „gesperrt“."""
    key, zeile = schluessel(tmp_path / "schluessel")
    andere, _ = schluessel(tmp_path / "andere")
    r = repo(tmp_path / "repo", key)
    rest = {"schluessel": andere.with_suffix(".pub").read_text(encoding="utf-8"),
            "kommentar": "# Kommentar\n", "leer": "\n"}[art]
    a = anker_dateien(tmp_path / "anker", zeile, widerrufen="# Sperrliste\n" + vorsatz + rest)
    with pytest.raises(NachweisFehler, match="Zeile 2 ist kein öffentlicher Schlüssel") as e:
        pruefe_signierschluessel(r, a)
    assert e.value.regel == "commit-signatur-anker"


@pytest.mark.parametrize("inhalt", [
    "# Sperrliste\r\n{andere}\r\n",          # CRLF
    "  \t# eingerückt\n \t\n{andere}",       # Leerzeichen und Tab, ohne Schluss-Newline
    "# a\rgarbage\n{andere}",                # CR innerhalb eines Kommentars
    "# a\u2028b\x0c\n{andere}",              # Unicode-Trenner im Kommentar
])
def test_sperrdatei_wie_ssh_keygen_gelesen(tmp_path, inhalt):
    """Was ssh-keygen als Kommentar oder Schlüssel liest, lehnt die Zeilenprüfung nicht ab."""
    key, zeile = schluessel(tmp_path / "schluessel")
    andere, _ = schluessel(tmp_path / "andere")
    r = repo(tmp_path / "repo", key)
    pub = andere.with_suffix(".pub").read_text(encoding="utf-8").rstrip("\n")
    a = anker_dateien(tmp_path / "anker", zeile, widerrufen=inhalt.format(andere=pub))
    assert pruefe_signierschluessel(r, a) == fingerprint(key)


def test_signierschluessel_pub_ohne_privat_und_fremde_pub(umgebung, tmp_path):
    r, a, key = umgebung
    nur_pub = tmp_path / "nur.pub"
    nur_pub.write_bytes(key.with_suffix(".pub").read_bytes())
    git(r, "config", "user.signingkey", str(nur_pub))
    with pytest.raises(NachweisFehler, match="nicht signierbar"):
        pruefe_signierschluessel(r, a)
    # Privater Schlüssel mit einer fremden .pub daneben: ssh-keygen verweigert die Signatur.
    fremd, _ = schluessel(tmp_path / "fremd")
    gemischt = tmp_path / "gemischt"
    gemischt.mkdir()
    (gemischt / "k").write_bytes(key.read_bytes())
    (gemischt / "k").chmod(0o600)
    (gemischt / "k.pub").write_bytes(fremd.with_suffix(".pub").read_bytes())
    git(r, "config", "user.signingkey", str(gemischt / "k.pub"))
    with pytest.raises(NachweisFehler):
        pruefe_signierschluessel(r, a)


_PROBE_IM_KIND = """
import sys
from pathlib import Path
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.signaturen import anker_aus_dateien, pruefe_signierschluessel
anker = Path(sys.argv[2])
a = anker_aus_dateien(anker / "commit-signers", anker / "commit-revoked", stichtag="a" * 40,
                      zweige=("main",))
try:
    pruefe_signierschluessel(Path(sys.argv[1]), a)
except NachweisFehler as e:
    print(e.text)
    sys.exit(3)
"""


@pytest.mark.parametrize("mit_tty", [False, True])
def test_verschluesselter_schluessel_scheitert_sofort(tmp_path, mit_tty):
    """Ein Schlüssel mit Passphrase scheitert in der Probe sofort, fail-closed — auch an einem
    Terminal. Sonst fragte die Probe über /dev/tty und danach git ein zweites Mal (bei DAST mit
    dem ganzen Scan dazwischen). Das Kind bekommt hier ein echtes Controlling-Terminal (pty);
    der Timeout fängt ein Hängen an der Passphrase-Abfrage."""
    import fcntl
    import os
    import subprocess
    import sys
    import termios
    key = tmp_path / "schluessel" / "commit_ed25519"
    key.parent.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "geheim", "-C", "test", "-f",
                    str(key)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    r = repo(tmp_path / "repo", key)
    a = anker_dateien(tmp_path / "anker", f't@x namespaces="git" {typ} {wert}\n')
    haupt, neben = os.openpty()

    def terminal() -> None:  # im Kind: neue Sitzung, pty als Controlling-Terminal
        os.setsid()
        if mit_tty:
            fcntl.ioctl(neben, termios.TIOCSCTTY, 0)

    try:
        lauf = subprocess.run([sys.executable, "-c", _PROBE_IM_KIND, str(r), str(a.signers.parent)],
                              stdin=neben, capture_output=True, text=True, timeout=30,
                              preexec_fn=terminal, pass_fds=(neben,), check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("Probe hängt an der Passphrase-Abfrage")
    finally:
        os.close(neben)
        os.close(haupt)
    assert lauf.returncode == 3, lauf.stderr
    assert "nicht signierbar" in lauf.stdout and "Passphrase" in lauf.stdout


@pytest.mark.parametrize(("schluessel_", "wert", "meldung"), [
    ("gpg.format", "openpgp", "gpg.format"),
    ("user.signingkey", "", "user.signingkey"),
    ("user.signingkey", "/gibt/es/nicht", "nicht signierbar"),
    ("user.signingkey", "key::ssh-ed25519 AAAA", "Literal"),
    ("user.signingkey", "ssh-ed25519 AAAA", "Literal"),
])
def test_signierschluessel_nicht_pruefbar(umgebung, schluessel_, wert, meldung):
    r, a, _ = umgebung
    if wert:
        git(r, "config", schluessel_, wert)
    else:
        git(r, "config", "--unset", schluessel_)
    with pytest.raises(NachweisFehler, match=meldung) as e:
        pruefe_signierschluessel(r, a)
    assert e.value.regel == "commit-signatur"


# --- pruefe_commit ------------------------------------------------------------------------------

def test_signierter_commit_liefert_fingerprint(umgebung):
    r, a, key = umgebung
    c = commit(r, "a", signiert=True)
    assert pruefe_commit(r, c, a) == fingerprint(key)


def test_unsignierter_commit(umgebung):
    r, a, _ = umgebung
    c = commit(r, "a", signiert=False)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur"
    assert e.value.text.startswith(f"{c[:12]}: N ") and "unsigniert" in e.value.text


def test_fremder_schluessel(umgebung, tmp_path):
    r, a, _ = umgebung
    fremd, _ = schluessel(tmp_path / "fremd")
    c = commit(r, "a", signiert=True, key=fremd)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur" and f"{c[:12]}: U " in e.value.text


def test_nur_namensraum_git_gilt(umgebung):
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    a.signers.write_text(a.signers.read_text(encoding="utf-8").replace('"git"', '"file"'),
                         encoding="utf-8")
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_commit(r, c, a)


def test_widerrufener_schluessel(umgebung, tmp_path):
    """Widerruf per Sperrdatei (nicht valid-before): gilt unabhängig von der Commit-Zeit."""
    r, a, key = umgebung
    c = commit(r, "a", signiert=True)
    a.widerrufen.write_text(key.with_suffix(".pub").read_text(encoding="utf-8"),
                            encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur" and f"{c[:12]}: B " in e.value.text


def test_valid_before_gilt_nur_gegen_die_commit_zeit(umgebung):
    """Beleg, warum valid-before kein Widerruf ist: ein rückdatierter Commit besteht."""
    r, a, _ = umgebung
    zeile = a.signers.read_text(encoding="utf-8")
    a.signers.write_text(zeile.replace("namespaces=", 'valid-before="20260101",namespaces='),
                         encoding="utf-8")
    (r / "f").write_text("x", encoding="utf-8")
    git(r, "add", "f")
    git(r, "commit", "-q", "-S", "-m", "rueckdatiert", GIT_COMMITTER_DATE="2025-06-01T00:00:00")
    assert pruefe_commit(r, "HEAD", a).startswith("SHA256:")
    heute = commit(r, "heute", signiert=True)
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_commit(r, heute, a)


@pytest.mark.parametrize("datei", ["signers", "widerrufen"])
def test_anker_datei_fehlt_bei_der_pruefung(umgebung, datei):
    # Ohne Signers meldet git „U“, ohne Sperrdatei nur eine Warnung — beides Ankerfehler.
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    getattr(a, datei).unlink()
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur-anker"


@pytest.mark.parametrize("rev", ["--all", "gibtsnicht", "HEAD:a.txt"])
def test_kein_commit(umgebung, rev):
    r, a, _ = umgebung
    commit(r, "a", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, rev, a)
    assert e.value.regel == "commit-signatur"


def test_log_show_signature_stoert_nicht(umgebung):
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    git(r, "config", "log.showSignature", "true")
    assert pruefe_commit(r, c, a).startswith("SHA256:")


# --- pruefe_seit_stichtag -----------------------------------------------------------------------

def test_alle_signiert_zaehlt_mit_stichtag(umgebung):
    r, a, key = umgebung
    stichtag = commit(r, "a", signiert=True)
    commit(r, "b", signiert=True)
    kopf = commit(r, "c", signiert=True)
    gesehen: set[str] = set()
    assert pruefe_seit_stichtag(r, _ab(a, stichtag), kopf, gesehen=gesehen) == 3
    assert gesehen == {fingerprint(key)}
    assert pruefe_seit_stichtag(r, _ab(a, kopf), kopf) == 1


def test_unsignierter_commit_nach_stichtag(umgebung):
    r, a, _ = umgebung
    stichtag = commit(r, "a", signiert=True)
    b = commit(r, "b", signiert=False)
    kopf = commit(r, "c", signiert=True)
    with pytest.raises(NachweisFehler, match="commit-signatur") as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), kopf)
    assert b[:12] in e.value.text


def test_stichtag_selbst_unsigniert(umgebung):
    r, a, _ = umgebung
    stichtag = commit(r, "a", signiert=False)
    kopf = commit(r, "b", signiert=True)
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_seit_stichtag(r, _ab(a, stichtag), kopf)


def test_unsignierte_historie_vor_stichtag_blockiert_nicht(umgebung):
    r, a, _ = umgebung
    commit(r, "alt1", signiert=False)
    commit(r, "alt2", signiert=False)
    stichtag = commit(r, "a", signiert=True)
    kopf = commit(r, "b", signiert=True)
    assert pruefe_seit_stichtag(r, _ab(a, stichtag), kopf) == 2


def test_stichtag_kein_vorfahre(umgebung):
    r, a, _ = umgebung
    basis = commit(r, "a", signiert=True)
    git(r, "switch", "-q", "-c", "neben")
    neben = commit(r, "n", signiert=True)
    git(r, "switch", "-q", "main")
    git(r, "reset", "-q", "--hard", basis)
    kopf = commit(r, "k", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, neben), kopf)
    assert e.value.regel == "commit-signatur" and "Vorfahre" in e.value.text


def _merge_topologie(r: Path, *, merge_signiert: bool, nebenzweig_unsigniert: bool = False
                     ) -> tuple[str, str, str]:
    """`main` mit unsignierter Historie; Feature-Zweig ab dem Stichtag signiert, lokal per
    Merge-Commit nach `main` gemergt; Release-Commit auf `main`. Rückgabe (Stichtag, Release,
    Altstand = Spitze von main zum Stichtag)."""
    basis = commit(r, "alt1", signiert=False)
    git(r, "switch", "-q", "-c", "feat/nachweise")
    stichtag = commit(r, "stichtag", signiert=True)
    commit(r, "f1", signiert=True)
    if nebenzweig_unsigniert:
        git(r, "switch", "-q", "-c", "neben")
        commit(r, "neben-unsigniert", signiert=False)
        git(r, "switch", "-q", "feat/nachweise")
        git(r, "merge", "-q", "--no-ff", "-S", "-m", "merge neben", "neben")
    git(r, "switch", "-q", "main")
    assert git(r, "rev-parse", "HEAD") == basis
    altstand = commit(r, "alt2-parallel", signiert=False)
    git(r, "merge", "-q", "--no-ff", "-S" if merge_signiert else "--no-gpg-sign",
        "-m", "merge feat/nachweise", "feat/nachweise")
    release = commit(r, "release", signiert=True)
    return stichtag, release, altstand


def test_topologie_nach_signiertem_merge_besteht(umgebung):
    r, a, _ = umgebung
    stichtag, release, altstand = _merge_topologie(r, merge_signiert=True)
    # Stichtag, f1, Merge-Commit, Release-Commit — nicht alt1/alt2-parallel (Altstand).
    assert pruefe_seit_stichtag(r, _ab(a, stichtag, altstand), release) == 4


def test_topologie_ohne_altstand_scheitert_an_alter_historie(umgebung):
    """Ohne eingefrorenen Altstand gilt die parallele alte main-Historie als neu — fail-closed."""
    r, a, _ = umgebung
    stichtag, release, altstand = _merge_topologie(r, merge_signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), release)
    assert altstand[:12] in e.value.text


def test_topologie_mit_unsigniertem_merge_scheitert(umgebung):
    r, a, _ = umgebung
    stichtag, release, altstand = _merge_topologie(r, merge_signiert=False)
    merge = git(r, "rev-parse", "HEAD~1")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, altstand), release)
    assert e.value.regel == "commit-signatur" and merge[:12] in e.value.text


def test_unsignierter_commit_auf_gemergtem_nebenzweig_scheitert(umgebung):
    r, a, _ = umgebung
    stichtag, release, altstand = _merge_topologie(r, merge_signiert=True,
                                                   nebenzweig_unsigniert=True)
    neben = git(r, "rev-parse", "neben")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, altstand), release)
    assert e.value.regel == "commit-signatur" and neben[:12] in e.value.text


def test_vor_dem_stichtag_abgezweigter_zweig_wird_geprueft(umgebung):
    """Ein Zweig, der vor dem Stichtag abzweigt, bekommt danach einen unsignierten
    Commit und wird signiert gemergt — der Commit liegt nicht auf dem Abstammungspfad, muss aber
    geprüft werden."""
    r, a, _ = umgebung
    basis = commit(r, "alt", signiert=False)
    git(r, "branch", "vorstudie")
    stichtag = commit(r, "stichtag", signiert=True)
    git(r, "switch", "-q", "vorstudie")
    schlecht = commit(r, "unsigniert-nach-stichtag", signiert=False)
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "merge vorstudie", "vorstudie")
    release = commit(r, "release", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, basis), release)
    assert e.value.regel == "commit-signatur" and schlecht[:12] in e.value.text


def test_unsignierter_commit_auf_main_nach_dem_einfrieren(umgebung):
    r, a, _ = umgebung
    basis = commit(r, "alt", signiert=False)
    git(r, "switch", "-q", "-c", "feat/nachweise")
    stichtag = commit(r, "stichtag", signiert=True)
    git(r, "switch", "-q", "main")
    schlecht = commit(r, "oberflaeche", signiert=False)
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "merge", "feat/nachweise")
    release = commit(r, "release", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, basis), release)
    assert schlecht[:12] in e.value.text


def test_altstand_darf_nicht_vom_stichtag_abstammen(umgebung):
    """Ein Altstand hinter dem Stichtag würde neue Commits von der Prüfung ausnehmen."""
    r, a, _ = umgebung
    stichtag = commit(r, "stichtag", signiert=True)
    spaeter = commit(r, "unsigniert", signiert=False)
    release = commit(r, "release", signiert=True)
    for alt in (stichtag, spaeter):
        with pytest.raises(NachweisFehler) as e:
            pruefe_seit_stichtag(r, _ab(a, stichtag, alt), release)
        assert e.value.regel == "commit-signatur-anker"


def test_altstand_fehlt_im_repo(umgebung):
    r, a, _ = umgebung
    stichtag = commit(r, "stichtag", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, "b" * 40), stichtag)
    assert e.value.regel == "commit-signatur-anker" and "b" * 12 in e.value.text


# --- lade_anker ---------------------------------------------------------------------------------

@pytest.fixture
def archiv_mit_anker(tmp_path: Path) -> tuple[Path, str, Path]:
    key, zeile = schluessel(tmp_path / "k")
    a = repo(tmp_path / "archiv", key)
    commit(a, "start", signiert=True)
    stichtag = "a" * 40
    anker(a, zeile, stichtag, altstaende=("c" * 40,))
    return a, stichtag, key


def test_anker_laden(archiv_mit_anker, tmp_path):
    a, stichtag, key = archiv_mit_anker
    ergebnis = lade_anker(a, tmp_path / "ablage")
    assert (ergebnis.stichtag, ergebnis.altstaende, ergebnis.zweige) == (
        stichtag, ("c" * 40,), ("main", "feat/nachweise"))
    assert ergebnis.fingerprints == frozenset({fingerprint(key)})
    assert ergebnis.commit == git(a, "rev-parse", "HEAD")
    # Aus den Git-Objekten in die Ablage geschrieben, nie der Pfad im Arbeitsbaum.
    assert ergebnis.signers.parent == tmp_path / "ablage"
    assert ergebnis.signers.read_bytes() == (a / "vertrauensanker" / "commit-signers").read_bytes()
    assert ergebnis.widerrufen.read_bytes() == b""
    assert oct(ergebnis.signers.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("datei", ["commit-signers", "commit-signatur.toml", "commit-revoked"])
def test_anker_datei_fehlt(archiv_mit_anker, tmp_path, datei):
    a, _, _ = archiv_mit_anker
    git(a, "rm", "-q", f"vertrauensanker/{datei}")
    git(a, "commit", "-q", "-S", "-m", "weg")
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "ablage")
    assert e.value.regel == "commit-signatur-anker" and datei in e.value.text


def test_anker_ordner_fehlt(tmp_path):
    key, _ = schluessel(tmp_path / "k")
    a = repo(tmp_path / "archiv", key)
    commit(a, "start", signiert=True)
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(a, tmp_path / "ablage")


def test_kein_archiv(tmp_path):
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(tmp_path / "gibtsnicht", tmp_path / "ablage")


@pytest.mark.parametrize("inhalt", [
    toml("abc"),
    toml("A" * 40),
    'altstaende = []\nzweige = ["main"]\n',
    'stichtag_commit = "' + "a" * 40 + '"\naltstaende = []\n',
    'stichtag_commit = "' + "a" * 40 + '"\nzweige = ["main"]\n',
    toml("a" * 40, zweige=()),
    toml("a" * 40, altstaende=("kurz",)),
    toml("a" * 40, altstaende=("c" * 40, "c" * 40)),
    toml("a" * 40) + "fremd = 1\n",
    "kein toml [",
], ids=["kurz", "gross", "ohne-stichtag", "ohne-zweige", "ohne-altstaende", "zweige-leer",
        "altstand-kurz", "altstand-doppelt", "fremdes-feld", "toml"])
def test_anker_toml_ungueltig(archiv_mit_anker, tmp_path, inhalt):
    a, _, _ = archiv_mit_anker
    (a / "vertrauensanker" / "commit-signatur.toml").write_text(inhalt, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "kaputt")
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(a, tmp_path / "ablage")


@pytest.mark.parametrize("inhalt", ["# nur Kommentar\n", "irgendwas ohne schluessel\n"])
def test_anker_signers_ohne_gueltigen_schluessel(archiv_mit_anker, tmp_path, inhalt):
    a, _, _ = archiv_mit_anker
    (a / "vertrauensanker" / "commit-signers").write_text(inhalt, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "leer")
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(a, tmp_path / "ablage")


def test_anker_uncommittete_aenderung(archiv_mit_anker, tmp_path):
    """Maßgeblich ist der committete Anker — eine lokale Änderung wird nicht still übernommen."""
    a, _, _ = archiv_mit_anker
    signers = a / "vertrauensanker" / "commit-signers"
    signers.write_text(signers.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "ablage")
    assert e.value.regel == "commit-signatur-anker" and "Arbeitsbaum" in e.value.text


@pytest.mark.parametrize("schalter", ["--skip-worktree", "--assume-unchanged"])
def test_anker_skip_worktree_umgeht_nichts(archiv_mit_anker, tmp_path, schalter):
    """Per skip-worktree ausgeblendete Änderungen am Anker (fremder Schlüssel)
    werden erkannt — der Anker kommt aus den Git-Objekten, der Arbeitsbaum muss gleich sein."""
    a, _, _ = archiv_mit_anker
    _, fremd_zeile = schluessel(tmp_path / "fremd")
    git(a, "update-index", schalter, "vertrauensanker/commit-signers")
    signers = a / "vertrauensanker" / "commit-signers"
    signers.write_text(signers.read_text(encoding="utf-8") + fremd_zeile, encoding="utf-8")
    assert git(a, "status", "--porcelain") == ""
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(a, tmp_path / "ablage")


def test_anker_nur_aus_main(archiv_mit_anker, tmp_path):
    """Ein anderer ausgecheckter Archiv-Zweig mit eigenem Anker gilt nicht."""
    a, _, _ = archiv_mit_anker
    git(a, "switch", "-q", "-c", "anders")
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "ablage")
    assert e.value.regel == "commit-signatur-anker" and "main" in e.value.text


def test_anker_symlink(archiv_mit_anker, tmp_path):
    a, _, _ = archiv_mit_anker
    signers = a / "vertrauensanker" / "commit-signers"
    ziel = tmp_path / "woanders"
    ziel.write_bytes(signers.read_bytes())
    signers.unlink()
    signers.symlink_to(ziel)
    git(a, "commit", "-q", "-S", "-am", "symlink")
    with pytest.raises(NachweisFehler, match="commit-signatur-anker"):
        lade_anker(a, tmp_path / "ablage")


# --- Review I2/M3: Prüfprogramm fest, kein Objektersatz, Fingerprint im Anker ------------------

def test_fremdes_gpg_program_faelscht_keine_pgp_signatur(umgebung, tmp_path):
    """Ein Commit mit gefälschter PGP-Signatur und ein repo-lokales gpg.program, das „gut“
    meldet (mit dem Fingerprint des Ankers), ergibt kein bestanden."""
    r, a, _ = umgebung
    s = commit(r, "s", signiert=True)
    baum = git(r, "rev-parse", "HEAD^{tree}")
    sig = ("-----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n =abcd\n"
           " -----END PGP SIGNATURE-----")
    obj = (f"tree {baum}\nparent {s}\nauthor A <a@x> 1759000000 +0200\n"
           f"committer A <a@x> 1759000000 +0200\ngpgsig {sig}\n\nfalsche pgp-signatur\n")
    import subprocess
    c = subprocess.run(["git", "-C", str(r), "hash-object", "-t", "commit", "-w", "--stdin"],
                       input=obj, capture_output=True, text=True, check=True).stdout.strip()
    fp = next(iter(a.fingerprints))
    falsch = tmp_path / "fake-gpg"
    falsch.write_text("#!/bin/sh\necho '[GNUPG:] NEWSIG'\n"
                      "echo '[GNUPG:] GOODSIG 0123456789ABCDEF Evil <e@x>'\n"
                      f"echo '[GNUPG:] VALIDSIG {fp} 2026-01-01 1759000000 0 4 0 1 10 00 "
                      "ABCDEF0123456789ABCDEF0123456789ABCDEF01'\n"
                      "echo '[GNUPG:] TRUST_ULTIMATE 0 pgp'\nexit 0\n", encoding="utf-8")
    falsch.chmod(0o755)
    git(r, "config", "gpg.program", str(falsch))
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur"
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_seit_stichtag(r, _ab(a, s), c)


def test_ssh_keygen_aus_path_wird_nicht_benutzt(umgebung, tmp_path, monkeypatch):
    """Ein ssh-keygen vorn im PATH, das jede Signatur für gut erklärt, wird ignoriert."""
    r, a, _ = umgebung
    fremd, _ = schluessel(tmp_path / "fremd")
    c = commit(r, "a", signiert=True, key=fremd)
    fp = next(iter(a.fingerprints))
    attrappe = tmp_path / "bin"
    attrappe.mkdir()
    (attrappe / "ssh-keygen").write_text(
        "#!/bin/sh\n"
        f"echo 'Good \"git\" signature for test@example.invalid with ED25519 key {fp}'\n"
        "echo test@example.invalid\nexit 0\n", encoding="utf-8")
    (attrappe / "ssh-keygen").chmod(0o755)
    git(r, "config", "gpg.ssh.program", str(attrappe / "ssh-keygen"))
    monkeypatch.setenv("PATH", f"{attrappe}:{__import__('os').environ['PATH']}")
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur"


def test_replace_ref_ersetzt_keinen_unsignierten_commit(umgebung):
    r, a, _ = umgebung
    stichtag = commit(r, "s", signiert=True)
    git(r, "switch", "-q", "-c", "ersatz")
    signiert = commit(r, "u2", signiert=True)
    git(r, "switch", "-q", "main")
    unsigniert = commit(r, "u", signiert=False)
    git(r, "replace", unsigniert, signiert)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, unsigniert, a)
    assert f"{unsigniert[:12]}: N " in e.value.text
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_seit_stichtag(r, _ab(a, stichtag), unsigniert)


def test_fingerprint_muss_im_anker_liegen(umgebung):
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    anders = dataclasses.replace(a, fingerprints=frozenset({"SHA256:" + "A" * 43}))
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, anders)
    assert e.value.regel == "commit-signatur" and "nicht im Vertrauensanker" in e.value.text


def test_ssh_keygen_fester_pfad():
    from scanview_nachweise import signaturen
    assert signaturen.SSH_KEYGEN == "/usr/bin/ssh-keygen"


# --- Grafts, shallow, commit-graph verfälschen die Traversierung nicht ----------------------------

def _merge_mit_unsigniertem_zweig(r: Path) -> tuple[str, str, str]:
    """Stichtag S (signiert); Zweig ab S mit unsigniertem X; signierter Merge M. Rückgabe
    (S, X, M)."""
    commit(r, "basis", signiert=False)
    stichtag = commit(r, "S", signiert=True)
    git(r, "switch", "-q", "-c", "x")
    x = commit(r, "x", signiert=False)
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "m", "x")
    return stichtag, x, git(r, "rev-parse", "HEAD")


def test_grafts_blenden_keinen_commit_aus(umgebung):
    r, a, _ = umgebung
    stichtag, x, merge = _merge_mit_unsigniertem_zweig(r)
    (r / ".git" / "info").mkdir(exist_ok=True)
    (r / ".git" / "info" / "grafts").write_text(f"{merge} {stichtag}\n", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), merge)
    assert e.value.regel == "commit-signatur" and x[:12] in e.value.text


def test_shallow_repo_bricht_ab(umgebung):
    r, a, _ = umgebung
    stichtag, _, merge = _merge_mit_unsigniertem_zweig(r)
    git(r, "switch", "-q", "-c", "a", stichtag)
    commit(r, "a", signiert=True)
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "R", merge)
    release = git(r, "rev-parse", "HEAD")
    (r / ".git" / "shallow").write_text(f"{merge}\n", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), release)
    assert e.value.regel == "commit-signatur" and "flach" in e.value.text


def test_gefaelschter_commit_graph_blendet_keinen_commit_aus(umgebung):
    """Elternposition des Merges im commit-graph auf „kein“ gepatcht (git prüft die Datei nicht)."""
    import struct
    r, a, _ = umgebung
    stichtag, x, merge = _merge_mit_unsigniertem_zweig(r)
    git(r, "commit-graph", "write", "--reachable")
    pfad = r / ".git" / "objects" / "info" / "commit-graph"
    d = bytearray(pfad.read_bytes())
    chunks = {}
    for i in range(d[6] + 1):
        chunks[bytes(d[8 + 12 * i:12 + 12 * i])] = struct.unpack(
            ">Q", d[12 + 12 * i:20 + 12 * i])[0]
    oidl, cdat = chunks[b"OIDL"], chunks[b"CDAT"]
    oids = [bytes(d[oidl + 20 * i:oidl + 20 * i + 20]).hex() for i in range((cdat - oidl) // 20)]
    eintrag = cdat + 36 * oids.index(merge)
    d[eintrag + 24:eintrag + 28] = struct.pack(">I", 0x70000000)
    pfad.chmod(0o644)
    pfad.write_bytes(bytes(d))
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), merge)
    assert e.value.regel == "commit-signatur" and x[:12] in e.value.text


# --- Altstand ist ein Commit-Objekt ---------------------------------------------------------------

@pytest.mark.parametrize("art", ["tag", "baum"])
def test_altstand_muss_commit_objekt_sein(umgebung, art):
    r, a, _ = umgebung
    basis = commit(r, "basis", signiert=False)
    git(r, "tag", "-a", "-m", "t", "alt", basis)
    stichtag = commit(r, "stichtag", signiert=True)
    objekt = git(r, "rev-parse", "alt" if art == "tag" else f"{basis}^{{tree}}")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, objekt), stichtag)
    assert e.value.regel == "commit-signatur-anker" and objekt[:12] in e.value.text


# --- Jede Verteidigungsschicht einzeln festgenagelt -----------------------------------------------

def test_schicht_feste_pruefkonfiguration(tmp_path):
    from scanview_nachweise import signaturen
    liste = signaturen.pruef_konfiguration(tmp_path / "s", tmp_path / "r")
    assert liste[::2] == ["-c"] * (len(liste) // 2)
    werte = dict(x.split("=", 1) for x in liste[1::2])
    assert werte == {"gpg.ssh.allowedSignersFile": str(tmp_path / "s"),
                     "gpg.ssh.revocationFile": str(tmp_path / "r"),
                     "gpg.ssh.program": "/usr/bin/ssh-keygen", "gpg.program": "/usr/bin/false",
                     "gpg.openpgp.program": "/usr/bin/false",
                     "gpg.x509.program": "/usr/bin/false", "log.showSignature": "false"}


def test_schicht_git_ohne_ersatzobjekte_und_commit_graph():
    from scanview_nachweise.werkzeuge import git_befehl
    befehl = git_befehl("log", repo=Path("/x"))
    assert befehl[:2] == ["git", "--no-replace-objects"]
    assert "core.commitGraph=false" in befehl and befehl[-3:] == ["-C", "/x", "log"]


def test_schicht_no_replace_objects_allein(umgebung, monkeypatch):
    """Auch ohne die Umgebungsvariable (UMGEBUNG_FEST leer) ersetzt `git replace` nichts."""
    from scanview_nachweise import werkzeuge
    monkeypatch.setattr(werkzeuge, "UMGEBUNG_FEST", {"GIT_GRAFT_FILE": "/dev/null"})
    assert "GIT_NO_REPLACE_OBJECTS" not in werkzeuge.gefilterte_umgebung()
    test_replace_ref_ersetzt_keinen_unsignierten_commit(umgebung)


def test_schicht_ssh_signatur_im_objekt(umgebung, monkeypatch):
    """Meldet git „G“, trägt das Commit-Objekt aber keine SSH-Signatur, gilt es nicht."""
    from scanview_nachweise.gitobjekte import Objektleser
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    echt = Objektleser.lies

    def ohne_signatur(self: Objektleser, oid: str, typ: str | None = None) -> bytes:
        if oid == c and typ == "commit":
            return b"tree " + b"0" * 40 + b"\n\nohne signatur\n"
        return echt(self, oid, typ)
    monkeypatch.setattr(Objektleser, "lies", ohne_signatur)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert "keine SSH-Signatur" in e.value.text


# --- Kettenprüfung der Anker-Commits ab dem Genesis-Commit ----------------------------------------

def test_genesis_im_produkt_fest():
    from scanview_nachweise import signaturen
    assert signaturen.ANKER_GENESIS == "59b95d9daa7e420438971945dc49c8ac3e1c0ae6"


def _anker_datei(a: Path, name: str) -> Path:
    return a / "vertrauensanker" / name


def _laden_scheitert(a: Path, ablage: Path, *teile: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, ablage)
    assert e.value.regel == "commit-signatur-anker", e.value
    for teil in teile:
        assert teil in e.value.text, e.value.text


def test_unsignierter_anker_commit_fuegt_schluessel_hinzu(archiv_mit_anker, tmp_path):
    """K1: ein unsignierter Archiv-Commit hängt einen fremden Schlüssel an."""
    a, _, _ = archiv_mit_anker
    _, fremd_zeile = schluessel(tmp_path / "fremd")
    signers = _anker_datei(a, "commit-signers")
    signers.write_text(signers.read_text(encoding="utf-8") + fremd_zeile, encoding="utf-8")
    git(a, "commit", "-q", "--no-gpg-sign", "-am", "fremd")
    _laden_scheitert(a, tmp_path / "abl", git(a, "rev-parse", "HEAD")[:12])


def test_anker_commit_mit_dem_neuen_schluessel_signiert(archiv_mit_anker, tmp_path):
    """K2: signiert mit genau dem Schlüssel, den der Commit hinzufügt — gilt nicht, maßgeblich
    ist der Anker des Elternstands."""
    a, _, _ = archiv_mit_anker
    fremd, fremd_zeile = schluessel(tmp_path / "fremd")
    signers = _anker_datei(a, "commit-signers")
    signers.write_text(signers.read_text(encoding="utf-8") + fremd_zeile, encoding="utf-8")
    git(a, "add", "-A")
    git(a, "-c", f"user.signingkey={fremd}", "commit", "-q", "-S", "-m", "fremd")
    _laden_scheitert(a, tmp_path / "abl", git(a, "rev-parse", "HEAD")[:12])


def test_neuer_schluessel_mit_altem_signiert_gilt(archiv_mit_anker, tmp_path):
    a, _, key = archiv_mit_anker
    fremd, fremd_zeile = schluessel(tmp_path / "neu")
    signers = _anker_datei(a, "commit-signers")
    signers.write_text(signers.read_text(encoding="utf-8") + fremd_zeile, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "neuer Schlüssel")
    assert lade_anker(a, tmp_path / "abl").fingerprints == {fingerprint(key), fingerprint(fremd)}


def test_rotation_valid_before_an_alter_zeile_erlaubt(archiv_mit_anker, tmp_path):
    a, _, _ = archiv_mit_anker
    _, neu_zeile = schluessel(tmp_path / "neu")
    signers = _anker_datei(a, "commit-signers")
    alt = signers.read_text(encoding="utf-8")
    signers.write_text(alt.replace("namespaces=", 'valid-before="20270101",namespaces=')
                       + neu_zeile, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "Ablösung")
    assert len(lade_anker(a, tmp_path / "abl").fingerprints) == 2


@pytest.mark.parametrize("aendern", ["zeile-geaendert", "zeile-entfernt"])
def test_signers_nur_anhaengen(archiv_mit_anker, tmp_path, aendern):
    a, _, _ = archiv_mit_anker
    _, neu_zeile = schluessel(tmp_path / "neu")
    signers = _anker_datei(a, "commit-signers")
    alt = signers.read_text(encoding="utf-8")
    signers.write_text(alt.replace("test\n", "anders\n") if aendern == "zeile-geaendert"
                       else "# leer\n" + neu_zeile, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "geändert")
    _laden_scheitert(a, tmp_path / "abl", "commit-signers")


def test_sperrliste_leeren_und_altstand_setzen_unsigniert(tmp_path):
    """probe_nb: ein unsignierter Anker-Commit leert die Sperrliste und nimmt einen unsignierten
    Commit nach dem Stichtag als Altstand aus — wirkt nicht."""
    key, zeile = schluessel(tmp_path / "k")
    r = repo(tmp_path / "r", key)
    basis = commit(r, "basis", signiert=False)
    git(r, "branch", "alt")
    stichtag = commit(r, "S", signiert=True)
    git(r, "switch", "-q", "alt")
    x = commit(r, "x", signiert=False)
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "m", "alt")
    release = git(r, "rev-parse", "HEAD")
    a = repo(tmp_path / "a", key)
    commit(a, "start", signiert=True)
    anker(a, zeile, stichtag, altstaende=(basis,),
          widerrufen=key.with_suffix(".pub").read_text(encoding="utf-8"))
    with pytest.raises(NachweisFehler, match="commit-signatur"):
        pruefe_seit_stichtag(r, lade_anker(a, tmp_path / "abl1"), release)
    _anker_datei(a, "commit-revoked").write_text("", encoding="utf-8")
    _anker_datei(a, "commit-signatur.toml").write_text(toml(stichtag, (basis, x)),
                                                       encoding="utf-8")
    git(a, "commit", "-q", "--no-gpg-sign", "-am", "Sperre geleert, Altstand x")
    _laden_scheitert(a, tmp_path / "abl2")


def test_sperrliste_nur_anhaengen_auch_signiert(archiv_mit_anker, tmp_path):
    a, _, _ = archiv_mit_anker
    gesperrt, _ = schluessel(tmp_path / "gesperrt")
    _anker_datei(a, "commit-revoked").write_text(
        gesperrt.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "sperre")
    lade_anker(a, tmp_path / "abl1")
    _anker_datei(a, "commit-revoked").write_text("", encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "sperre weg")
    _laden_scheitert(a, tmp_path / "abl2", "commit-revoked")


@pytest.mark.parametrize("danach", [False, True])
def test_fehlerhafte_sperrzeile_im_anker_commit_mit_hash_gemeldet(archiv_mit_anker, tmp_path,
                                                                  danach):
    """Eine von Hand angehängte Zeile aus allowed_signers (Principal davor) sperrte jede
    Signatur. Das Laden meldet den Archiv-Commit, der sie einträgt — auch wenn danach weitere
    Commits folgen —, und keinen Temporärpfad."""
    a, _, _ = archiv_mit_anker
    andere, _ = schluessel(tmp_path / "andere")
    _anker_datei(a, "commit-revoked").write_text(
        't@x namespaces="git" ' + andere.with_suffix(".pub").read_text(encoding="utf-8"),
        encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "sperre mit principal")
    schuldig = git(a, "rev-parse", "HEAD")
    if danach:
        commit(a, "danach", signiert=True)
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "abl")
    assert e.value.regel == "commit-signatur-anker"
    assert schuldig[:12] in e.value.text and "commit-revoked" in e.value.text
    assert "Zeile 1 ist kein öffentlicher Schlüssel" in e.value.text
    assert "scanview-ankerkette" not in e.value.text


def test_fehlerhafte_sperrzeile_im_genesis_ohne_temporaerpfad(tmp_path):
    key, zeile = schluessel(tmp_path / "k")
    andere, _ = schluessel(tmp_path / "andere")
    a = repo(tmp_path / "a", key)
    commit(a, "start", signiert=True)
    anker(a, zeile, "a" * 40,
          widerrufen="t@x " + andere.with_suffix(".pub").read_text(encoding="utf-8"))
    genesis = git(a, "rev-parse", "HEAD")
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "abl")
    assert e.value.regel == "commit-signatur-anker"
    assert genesis[:12] in e.value.text and "commit-revoked" in e.value.text
    assert "Zeile 1 ist kein öffentlicher Schlüssel" in e.value.text
    assert "scanview-ankerkette" not in e.value.text and "nicht mit einem Schlüssel" \
        not in e.value.text


@pytest.mark.parametrize("feld", ["altstand-entfernt", "stichtag", "zweige"])
def test_toml_nur_anhaengen(archiv_mit_anker, tmp_path, feld):
    a, stichtag, _ = archiv_mit_anker
    inhalt = {"altstand-entfernt": toml(stichtag),
              "stichtag": toml("d" * 40, ("c" * 40,)),
              "zweige": toml(stichtag, ("c" * 40,), ("main", "feat/nachweise", "neu"))}[feld]
    _anker_datei(a, "commit-signatur.toml").write_text(inhalt, encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "toml")
    _laden_scheitert(a, tmp_path / "abl", "commit-signatur.toml")


def test_altstand_anhaengen_signiert_erlaubt(archiv_mit_anker, tmp_path):
    a, stichtag, _ = archiv_mit_anker
    _anker_datei(a, "commit-signatur.toml").write_text(toml(stichtag, ("c" * 40, "e" * 40)),
                                                       encoding="utf-8")
    git(a, "commit", "-q", "-S", "-am", "altstand")
    assert lade_anker(a, tmp_path / "abl").altstaende == ("c" * 40, "e" * 40)


def test_jeder_archiv_commit_nach_genesis_signiert(archiv_mit_anker, tmp_path):
    """Auch ein Commit, der den Anker nicht berührt, muss signiert sein (Archiv-Commits sind
    signiert)."""
    a, _, _ = archiv_mit_anker
    (a / "notiz.txt").write_text("x", encoding="utf-8")
    git(a, "add", "notiz.txt")
    git(a, "commit", "-q", "--no-gpg-sign", "-m", "unsigniert")
    _laden_scheitert(a, tmp_path / "abl", git(a, "rev-parse", "HEAD")[:12])


def test_unsignierte_archiv_historie_vor_genesis_blockiert_nicht(tmp_path):
    key, zeile = schluessel(tmp_path / "k")
    a = repo(tmp_path / "a", key)
    commit(a, "alt1", signiert=False)
    commit(a, "alt2", signiert=False)
    anker(a, zeile, "a" * 40)
    assert lade_anker(a, tmp_path / "abl").stichtag == "a" * 40


def test_merge_im_archiv_nach_genesis(archiv_mit_anker, tmp_path):
    a, _, _ = archiv_mit_anker
    git(a, "switch", "-q", "-c", "neben")
    commit(a, "n", signiert=True)
    git(a, "switch", "-q", "main")
    commit(a, "m1", signiert=True)
    git(a, "merge", "-q", "--no-ff", "-S", "-m", "merge", "neben")
    _laden_scheitert(a, tmp_path / "abl", "linear")


def test_genesis_fehlt_oder_nicht_vorfahre(archiv_mit_anker, tmp_path, monkeypatch):
    from scanview_nachweise import signaturen
    a, _, _ = archiv_mit_anker
    monkeypatch.setattr(signaturen, "ANKER_GENESIS", "b" * 40)
    _laden_scheitert(a, tmp_path / "abl1", "Genesis")
    fremd, _ = schluessel(tmp_path / "fremd")
    anderes = repo(tmp_path / "anderes", fremd)
    commit(anderes, "x", signiert=True)
    git(a, "fetch", "-q", str(anderes), "main:fremd")
    monkeypatch.setattr(signaturen, "ANKER_GENESIS", git(a, "rev-parse", "fremd"))
    _laden_scheitert(a, tmp_path / "abl2", "Genesis", "kein Vorfahre")


def test_genesis_selbst_mit_eigenem_schluessel_signiert(tmp_path):
    key, zeile = schluessel(tmp_path / "k")
    fremd, _ = schluessel(tmp_path / "f")
    a = repo(tmp_path / "a", key)
    commit(a, "start", signiert=True)
    git(a, "config", "user.signingkey", str(fremd))
    anker(a, zeile, "a" * 40)
    _laden_scheitert(a, tmp_path / "abl", "Genesis")


# --- Objektinhalt an die Objekt-ID gebunden, keine fremden Objektspeicher -------------------------

def test_manipulierter_signers_blob_im_archiv(archiv_mit_anker, tmp_path):
    """Unter der ID des (vom Genesis signierten) Signers-Blobs liegt ein anderer Inhalt:
    Originalschlüssel plus Angreiferschlüssel, Arbeitsbaum angeglichen."""
    from signierhilfe import objekt_ueberschreiben
    a, _, _ = archiv_mit_anker
    _, fremd_zeile = schluessel(tmp_path / "fremd")
    oid = git(a, "rev-parse", "main:vertrauensanker/commit-signers")
    signers = a / "vertrauensanker" / "commit-signers"
    neu = (signers.read_text(encoding="utf-8") + fremd_zeile).encode()
    objekt_ueberschreiben(a, oid, "blob", neu)
    signers.write_bytes(neu)
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "abl")
    assert e.value.regel == "commit-signatur-anker" and "Objekt-ID" in e.value.text


def test_alternates_im_archiv(archiv_mit_anker, tmp_path):
    a, _, _ = archiv_mit_anker
    (a / ".git" / "objects" / "info").mkdir(exist_ok=True)
    (a / ".git" / "objects" / "info" / "alternates").write_text(f"{tmp_path}\n",
                                                                encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "abl")
    assert e.value.regel == "commit-signatur-anker" and "alternates" in e.value.text


def test_alternates_im_produkt(umgebung, tmp_path):
    r, a, _ = umgebung
    stichtag = commit(r, "s", signiert=True)
    (r / ".git" / "objects" / "info").mkdir(exist_ok=True)
    (r / ".git" / "objects" / "info" / "alternates").write_text(f"{tmp_path}\n",
                                                                encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), stichtag)
    assert e.value.regel == "commit-signatur" and "alternates" in e.value.text


def test_manipulierter_altstand_commit_blendet_nichts_aus(umgebung):
    """Der Altstand bekommt im Objektspeicher (gleiche ID) einen zusätzlichen Elternteil X —
    X wäre dann „alte Historie“ und ungeprüft."""
    from signierhilfe import objekt_ueberschreiben
    r, a, _ = umgebung
    basis = commit(r, "basis", signiert=False)
    stichtag = commit(r, "S", signiert=True)
    git(r, "switch", "-q", "-c", "neben", basis)
    x = commit(r, "x", signiert=False)
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-S", "-m", "m", "neben")
    release = git(r, "rev-parse", "HEAD")
    roh = git(r, "cat-file", "commit", basis).encode() + b"\n"
    objekt_ueberschreiben(r, basis, "commit",
                          roh.replace(b"\n", f"\nparent {x}".encode(), 1))
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag, basis), release)
    assert e.value.regel == "commit-signatur"
    assert "Objekt-ID" in e.value.text or x[:12] in e.value.text


def test_manipulierter_commit_mit_fremder_signatur(umgebung, tmp_path):
    """Unter der ID eines unsignierten Commits liegt der Inhalt eines signierten — git log
    meldete „G“ für den untergeschobenen Inhalt."""
    from signierhilfe import objekt_ueberschreiben
    r, a, _ = umgebung
    stichtag = commit(r, "S", signiert=True)
    git(r, "switch", "-q", "-c", "b")
    signiert_roh = git(r, "cat-file", "commit", commit(r, "sig", signiert=True)).encode() + b"\n"
    git(r, "switch", "-q", "main")
    u = commit(r, "u", signiert=False)
    objekt_ueberschreiben(r, u, "commit", signiert_roh)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), u)
    assert "Objekt-ID" in e.value.text


# --- Gegenproben gegen git rev-list festgenagelt --------------------------------------------------

def _rev_list_verfaelscht(monkeypatch, weglassen: int = 1) -> None:
    from scanview_nachweise import signaturen
    echt = signaturen._git

    def verfaelscht(repo: Path, *args: str, regel: str = "commit-signatur") -> str:
        aus = echt(repo, *args, regel=regel)
        if args and args[0] == "rev-list":
            return "\n".join(aus.split()[weglassen:])
        return aus
    monkeypatch.setattr(signaturen, "_git", verfaelscht)


def test_gegenprobe_rev_list_produkt(umgebung, monkeypatch):
    r, a, _ = umgebung
    stichtag = commit(r, "s", signiert=True)
    commit(r, "b", signiert=True)
    kopf = commit(r, "c", signiert=True)
    _rev_list_verfaelscht(monkeypatch)
    with pytest.raises(NachweisFehler) as e:
        pruefe_seit_stichtag(r, _ab(a, stichtag), kopf)
    assert e.value.regel == "commit-signatur" and "rev-list weicht" in e.value.text


def test_gegenprobe_rev_list_archiv(archiv_mit_anker, tmp_path, monkeypatch):
    a, _, _ = archiv_mit_anker
    commit(a, "weiter", signiert=True)
    _rev_list_verfaelscht(monkeypatch)
    with pytest.raises(NachweisFehler) as e:
        lade_anker(a, tmp_path / "abl")
    assert e.value.regel == "commit-signatur-anker" and "rev-list weicht" in e.value.text


# --- Signatur zusätzlich selbst über die hashgeprüften Bytes prüfen -------------------------------

def _git_meldet_gut(monkeypatch, a: Anker) -> None:
    """Zweitmeinung ausschalten: git meldet immer „G“ mit einem Anker-Fingerprint."""
    from scanview_nachweise import signaturen
    fp = next(iter(a.fingerprints))
    monkeypatch.setattr(signaturen, "_git_status",
                        lambda *args: ("G", fp, "test@example.invalid"))


def test_eigene_pruefung_veraenderter_payload(umgebung, monkeypatch):
    """Die geprüften Bytes weichen vom signierten Inhalt ab (Tausch zwischen Lesen und
    git log): die eigene Prüfung über genau diese Bytes scheitert."""
    from scanview_nachweise.gitobjekte import Objektleser
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    echt = Objektleser.lies

    def veraendert(self: Objektleser, oid: str, typ: str | None = None) -> bytes:
        inhalt = echt(self, oid, typ)
        return inhalt + b"angehaengt\n" if oid == c and typ == "commit" else inhalt
    monkeypatch.setattr(Objektleser, "lies", veraendert)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur" and "ssh-keygen" in e.value.text


def test_eigene_pruefung_fremder_schluessel(umgebung, tmp_path, monkeypatch):
    r, a, _ = umgebung
    fremd, _ = schluessel(tmp_path / "fremd")
    c = commit(r, "a", signiert=True, key=fremd)
    _git_meldet_gut(monkeypatch, a)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert e.value.regel == "commit-signatur" and "ssh-keygen" in e.value.text


def test_eigene_pruefung_widerrufener_schluessel(umgebung, monkeypatch):
    r, a, key = umgebung
    c = commit(r, "a", signiert=True)
    a.widerrufen.write_text(key.with_suffix(".pub").read_text(encoding="utf-8"),
                            encoding="utf-8")
    _git_meldet_gut(monkeypatch, a)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert "ssh-keygen" in e.value.text


def test_eigene_pruefung_valid_before_abgelaufen(umgebung, monkeypatch):
    r, a, _ = umgebung
    c = commit(r, "a", signiert=True)
    a.signers.write_text(a.signers.read_text(encoding="utf-8").replace(
        "namespaces=", 'valid-before="20200101",namespaces='), encoding="utf-8")
    _git_meldet_gut(monkeypatch, a)
    with pytest.raises(NachweisFehler) as e:
        pruefe_commit(r, c, a)
    assert "ssh-keygen" in e.value.text


def test_eigene_pruefung_rueckdatiert_wie_git(umgebung):
    """verify-time aus der Committer-Zeit (UTC): ein rückdatierter Commit besteht bei
    valid-before auch in der eigenen Prüfung — gleiche Semantik wie git."""
    r, a, _ = umgebung
    a.signers.write_text(a.signers.read_text(encoding="utf-8").replace(
        "namespaces=", 'valid-before="20260101",namespaces='), encoding="utf-8")
    (r / "f").write_text("x", encoding="utf-8")
    git(r, "add", "f")
    git(r, "commit", "-q", "-S", "-m", "rueckdatiert",
        GIT_COMMITTER_DATE="2025-12-31T23:30:00+02:00")
    assert pruefe_commit(r, "HEAD", a).startswith("SHA256:")


def test_signatur_teile_payload_ohne_gpgsig(umgebung):
    from scanview_nachweise import signaturen
    r, _, _ = umgebung
    c = commit(r, "a", signiert=True)
    roh = git(r, "cat-file", "commit", c).encode() + b"\n"
    payload, sig, zeit = signaturen._signatur_teile(roh, c)
    assert b"gpgsig" not in payload and b"-----BEGIN SSH SIGNATURE-----" not in payload
    assert sig.startswith(b"-----BEGIN SSH SIGNATURE-----\n")
    assert sig.endswith(b"-----END SSH SIGNATURE-----\n")
    assert len(zeit) == 15 and zeit.endswith("Z")

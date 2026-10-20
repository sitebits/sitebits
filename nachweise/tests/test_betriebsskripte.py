"""Betriebsskripte unter `nachweise/betrieb/` (laufen als systemd-Benutzer-Timer auf dem
Betriebshost). Getestet wird das Verhalten mit Attrappen im `PATH` (`git`, `python3`, `curl`,
`podman`, `sftp`, `ssh` als Skripte, die ihre Aufrufe protokollieren): keine Zugangsdaten, kein
Netz, kein echtes Repo. Die Attrappen stehen in `tmp_path/bin` vor dem restlichen `PATH`."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import werkzeugpflicht

from scanview_nachweise.werkzeuge import docker_befehl, lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
BETRIEB_SKRIPTE = REPO_WURZEL / "nachweise" / "betrieb"
WOCHENSCAN = BETRIEB_SKRIPTE / "wochenscan.sh"
AUFBEWAHRUNG = BETRIEB_SKRIPTE / "aufbewahrung.sh"
WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"

TOKEN = "123456:GEHEIMES-TOKEN-xyz"
CHAT = "4711"
JETZT_GROSS = 1700000000

# --- Attrappen ---------------------------------------------------------------------------------

#: Protokolliert jeden Aufruf mit Arbeitsverzeichnis; Verhalten steuern Dateien in `steuer/`.
GIT = """#!/bin/bash
printf '%s\\t%s\\n' "$PWD" "$*" >>"$ATTRAPPEN/git.log"
args=("$@")
# `-C <pfad>` und `-c k=v` entfernen, um das Unterkommando zu finden.
while [ "${args[0]:-}" = -C ] || [ "${args[0]:-}" = -c ]; do args=("${args[@]:2}"); done
sub=${args[0]:-}
case "$sub" in
  fetch) [ -e "$ATTRAPPEN/steuer/fetch-scheitert" ] && { echo "fatal: fetch" >&2; exit 128; } ;;
  verify-commit)
    [ -e "$ATTRAPPEN/steuer/archiv-unsigniert" ] && { echo "error: keine Signatur" >&2; exit 1; } ;;
  cat-file)
    if [ "${args[1]:-}" = commit ]; then
      echo "tree 0000000000000000000000000000000000000000"
      if [ -e "$ATTRAPPEN/steuer/archiv-zwei-gpgsig" ]; then
        printf 'gpgsig -----BEGIN PGP SIGNATURE-----\\ngpgsig -----BEGIN SSH SIGNATURE-----\\n'
      elif [ -e "$ATTRAPPEN/steuer/archiv-openpgp-ssh-text" ]; then
        printf 'gpgsig -----BEGIN PGP SIGNATURE-----\\n\\ngpgsig -----BEGIN SSH SIGNATURE-----\\n'
      elif [ -e "$ATTRAPPEN/steuer/archiv-openpgp" ]; then
        echo "gpgsig -----BEGIN PGP SIGNATURE-----"
      else
        echo "gpgsig -----BEGIN SSH SIGNATURE-----"
      fi
      exit 0
    fi
    if [ -e "$ATTRAPPEN/steuer/tag-objektname" ]; then
      echo "tag $(cat "$ATTRAPPEN/steuer/tag-objektname")"
    else
      t=$(grep -E '^v[0-9]+\\.[0-9]+\\.[0-9]+$' "$ATTRAPPEN/steuer/tags" | sort -V | tail -n1)
      echo "tag $t"
    fi
    if [ -e "$ATTRAPPEN/steuer/tag-openpgp-ssh-text" ]; then
      printf '\\n-----BEGIN SSH SIGNATURE-----\\n-----BEGIN PGP SIGNATURE-----\\n'
    elif [ -e "$ATTRAPPEN/steuer/tag-openpgp" ]; then
      echo "-----BEGIN PGP SIGNATURE-----"
    else
      echo "-----BEGIN SSH SIGNATURE-----"
    fi ;;
  tag)
    if [ "${args[1]:-}" = --list ]; then cat "$ATTRAPPEN/steuer/tags" 2>/dev/null; exit 0; fi
    if [ "${args[1]:-}" = -v ]; then
      [ -e "$ATTRAPPEN/steuer/tag-unsigniert" ] && { echo "error: keine Signatur" >&2; exit 1; }
      exit 0
    fi ;;
  worktree)
    if [ "${args[1]:-}" = add ]; then
      ziel=${args[3]}
      mkdir -p "$ziel/nachweise/src" "$ziel/docs/compliance"
      : >"$ziel/docs/compliance/vex.openvex.json"
    elif [ "${args[1]:-}" = remove ]; then
      rm -r "${args[2]}"
    fi ;;
esac
exit 0
"""

#: `-m scanview_nachweise wochenscan …` spielt den Befehl nach (Exit aus `steuer/py-exit`,
#: Ergebnis aus `steuer/ergebnis.json`); alles andere (kleine Auswertungen im Skript) läuft echt.
PYTHON = """#!/bin/bash
if [ "${1:-}" = -m ]; then
  printf '%s\\tPYTHONPATH=%s\\t%s\\n' "$PWD" "${PYTHONPATH:-}" "$*" >>"$ATTRAPPEN/python.log"
  ausgabe=
  prev=
  for a in "$@"; do [ "$prev" = --ausgabe ] && ausgabe=$a; prev=$a; done
  if [ -e "$ATTRAPPEN/steuer/kind-bleibt" ]; then
    setsid sleep 20 </dev/null >/dev/null 2>&1 &
    echo $! >"$ATTRAPPEN/kind.pid"
  fi
  rc=$(cat "$ATTRAPPEN/steuer/py-exit" 2>/dev/null || echo 0)
  if [ "$rc" = 2 ]; then
    echo "NACHWEIS-FEHLER wochenscan-sbom: 0.2.0: Manifest ungültig" >&2
    exit 2
  fi
  mkdir -p "$ausgabe"
  cp "$ATTRAPPEN/steuer/ergebnis.json" "$ausgabe/ergebnis.json"
  echo "Wochenscan: simuliert"
  exit "$rc"
fi
exec @ECHTES_PYTHON@ "$@"
"""

CURL = """#!/bin/bash
printf 'ARGV: %s\\n' "$*" >>"$ATTRAPPEN/curl.argv"
cat >>"$ATTRAPPEN/curl.stdin"
printf '\\n---\\n' >>"$ATTRAPPEN/curl.stdin"
[ -e "$ATTRAPPEN/steuer/curl-scheitert" ] && exit 22
echo '{"ok":true,"result":{}}'
"""

#: Entfernter Ort als Verzeichnis `remote/`; versteht die Batch-Befehle des Skripts (`ls -1`,
#: `mkdir`, `put`, `get`). `steuer/get-verfaelscht` liefert beim Zurückholen ein verändertes
#: Bundle, `steuer/put-scheitert` lässt den Upload scheitern, `steuer/ziel-nicht-erreichbar`
#: schlägt schon beim ersten Befehl fehl.
SFTP = """#!/bin/bash
printf 'ARGV: %s\\n' "$*" >>"$ATTRAPPEN/sftp.argv"
batch=
host=
while [ $# -gt 0 ]; do
  case "$1" in
    -b) batch=$2; shift 2 ;;
    -P|-i|-o) shift 2 ;;
    -*) shift ;;
    *) host=$1; shift ;;
  esac
done
printf 'HOST: %s\\n' "$host" >>"$ATTRAPPEN/sftp.argv"
[ -e "$ATTRAPPEN/steuer/ziel-nicht-erreichbar" ] && { echo "Connection refused" >&2; exit 255; }
remote=$ATTRAPPEN/remote
mkdir -p "$remote"
cat "$batch" >>"$ATTRAPPEN/sftp.batch"
printf -- '---\\n' >>"$ATTRAPPEN/sftp.batch"
while IFS= read -r zeile || [ -n "$zeile" ]; do
  zeile=${zeile#-}
  # shellcheck disable=SC2086
  set -- $zeile
  befehl=${1:-}
  [ -n "$befehl" ] || continue
  echo "sftp> $zeile"
  case "$befehl" in
    ls)
      pfad=${3:-.}
      if [ -d "$remote/$pfad" ]; then (cd "$remote/$pfad" && ls -1 | sed "s|^|$pfad/|"); 
      elif [ -e "$remote/$pfad" ]; then echo "$pfad";
      else echo "Can't ls: \"/$pfad\" not found" >&2; exit 1; fi ;;
    mkdir) mkdir "$remote/$2" || exit 1 ;;
    put)
      [ -e "$ATTRAPPEN/steuer/put-scheitert" ] && { echo "Couldn't write" >&2; exit 1; }
      cp "$2" "$remote/$3" || exit 1 ;;
    get)
      [ -e "$remote/$2" ] || { echo "File not found" >&2; exit 1; }
      cp "$remote/$2" "$3"
      if [ -e "$ATTRAPPEN/steuer/get-verfaelscht" ] && [[ $2 == *.bundle ]]; then
        printf 'x' >>"$3"
      fi ;;
    *) echo "unbekannt: $befehl" >&2; exit 1 ;;
  esac
done <"$batch"
exit 0
"""

PROTOKOLL = """#!/bin/bash
printf '%s\\n' "$*" >>"$ATTRAPPEN/$(basename "$0").log"
"""


@dataclass
class Umgebung:
    wurzel: Path
    bin: Path
    steuer: Path
    betrieb: Path
    konf: Path
    textfile: Path
    env: dict[str, str]

    def lauf(self, skript: Path, umask: int | None = None) -> subprocess.CompletedProcess[str]:
        vorher = None if umask is None else (lambda: os.umask(umask))  # wie UMask= der Unit
        return subprocess.run(["bash", str(skript)], capture_output=True, text=True,
                              check=False, env=self.env, stdin=subprocess.DEVNULL, cwd=self.wurzel,
                              preexec_fn=vorher)

    def log(self, name: str) -> str:
        pfad = self.wurzel / name
        return pfad.read_text(encoding="utf-8") if pfad.exists() else ""

    def schalter(self, name: str) -> None:
        (self.steuer / name).write_text("", encoding="utf-8")

    @property
    def prom(self) -> Path:
        return self.textfile / "scanview_wochenscan.prom"


def _skript(pfad: Path, inhalt: str) -> None:
    pfad.write_text(inhalt, encoding="utf-8")
    pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR)


def _umgebung(tmp_path: Path) -> Umgebung:
    wurzel = tmp_path / "w"
    bin_ = wurzel / "bin"
    steuer = wurzel / "steuer"
    betrieb = wurzel / "betrieb"
    konf = wurzel / "konf"
    textfile = wurzel / "textfile"
    for p in (bin_, steuer, konf, textfile, betrieb / "scanview", betrieb / "scanview-nachweise",
              betrieb / "wochenscans", betrieb / "cache"):
        p.mkdir(parents=True)
    echtes_python = shutil.which("python3") or sys.executable
    _skript(bin_ / "git", GIT)
    _skript(bin_ / "python3", PYTHON.replace("@ECHTES_PYTHON@", echtes_python))
    _skript(bin_ / "curl", CURL)
    for name in ("podman", "ssh"):
        _skript(bin_ / name, PROTOKOLL)
    _skript(bin_ / "sftp", SFTP)
    (konf / "allowed_signers_release").write_text("release@scanview ssh-ed25519 AAAA\n",
                                                  encoding="utf-8")
    (konf / "allowed_signers_git").write_text(
        'release@scanview namespaces="git" ssh-ed25519 AAAA\n', encoding="utf-8")
    token_datei = wurzel / "token"
    token_datei.write_text(TOKEN + "\n", encoding="utf-8")
    (konf / "umgebung").write_text(
        f"BETRIEB={betrieb}\nTEXTFILE_DIR={textfile}\nTELEGRAM_TOKEN_DATEI={token_datei}\n"
        f"TELEGRAM_CHAT={CHAT}\nSCANVIEW_CONTAINER=podman\nPYTHON={bin_ / 'python3'}\n",
        encoding="utf-8")
    (steuer / "tags").write_text("v0.1.0\nv0.10.0\nv0.2.0\nv1.0.0-rc1\nlatest\nrelease-x\n",
                                 encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATTRAPPEN": str(wurzel),
           "SCANVIEW_WOCHENSCAN_KONF": str(konf), "HOME": str(wurzel / "heim")}
    return Umgebung(wurzel, bin_, steuer, betrieb, konf, textfile, env)


@pytest.fixture
def umg(tmp_path: Path) -> Umgebung:
    return _umgebung(tmp_path)


def _ergebnis(umg: Umgebung, blockierend: dict[str, list[str]], neu: list[str]) -> None:
    (umg.steuer / "ergebnis.json").write_text(json.dumps({
        "versionen": sorted(blockierend) or ["0.2.0"], "blockierend": blockierend, "neu": neu,
        "anker": {"datei": "x", "sha256": "0" * 64},
        "ergebnis": "blockiert" if blockierend else "grün"}), encoding="utf-8")


def _metrik(umg: Umgebung) -> dict[str, int]:
    return {z.split()[0].removeprefix("scanview_wochenscan_"): int(z.split()[1])
            for z in umg.prom.read_text(encoding="utf-8").splitlines() if z.strip()}


# --- wochenscan.sh -------------------------------------------------------------------------------


def test_wochenscan_gruen_setzt_beide_zeitstempel_ohne_telegram(umg: Umgebung) -> None:
    _ergebnis(umg, {}, [])
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 0, r.stdout + r.stderr
    m = _metrik(umg)
    assert m["blockierend"] == 0
    assert m["last_run_timestamp"] > JETZT_GROSS and m["last_success_timestamp"] > JETZT_GROSS
    assert umg.log("curl.argv") == ""  # kein Telegram bei grünem Lauf
    assert not (umg.textfile / "scanview_wochenscan.prom.tmp").exists()
    assert (umg.betrieb / "bekannt.txt").read_text(encoding="utf-8") == ""


def test_wochenscan_nimmt_neuesten_semver_tag_und_fuehrt_nur_dessen_code_aus(umg: Umgebung) -> None:
    _ergebnis(umg, {}, [])
    assert umg.lauf(WOCHENSCAN).returncode == 0
    git = umg.log("git.log")
    # SemVer-Sortierung: 0.10.0 vor 0.2.0, Vorabversion und Fremdtags zählen nicht.
    assert "tag -v v0.10.0" in git
    assert "tag -v v1.0.0-rc1" not in git and "tag -v v0.2.0" not in git
    assert f"gpg.ssh.allowedSignersFile={umg.konf}/allowed_signers_git" in git
    assert "fetch --prune --tags" in git
    assert git.count("fetch --prune --tags") == 2  # beide Klone
    assert "worktree add" in git
    python = umg.log("python.log")
    zeile = python.strip().splitlines()[0]
    lauf_code = zeile.split("\t")[0]
    assert "/lauf-" in lauf_code and lauf_code.endswith("/code")
    assert f"PYTHONPATH={lauf_code}/nachweise/src" in zeile
    assert "-m scanview_nachweise wochenscan" in zeile
    for option in (f"--archiv {umg.betrieb}/scanview-nachweise", f"--repo {umg.betrieb}/scanview",
                   f"--vex {lauf_code}/docs/compliance/vex.openvex.json",
                   f"--allowed-signers {umg.konf}/allowed_signers_release",
                   f"--bekannt {umg.betrieb}/bekannt.txt", "--db-aktualisieren",
                   f"--cache {umg.betrieb}/cache"):
        assert option in zeile, option
    assert f"--ausgabe {umg.betrieb}/wochenscans/" in zeile
    # Der Worktree wird wieder entfernt, nie erzwungen.
    assert "worktree remove" in git and "--force" not in git and " -f" not in git
    assert not Path(lauf_code).exists()


def test_wochenscan_tag_nicht_signiert_bricht_ab_und_meldet(umg: Umgebung) -> None:
    umg.schalter("tag-unsigniert")
    umg.prom.write_text("scanview_wochenscan_last_run_timestamp 111\n", encoding="utf-8")
    vorher = umg.prom.read_text(encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert "Tag nicht mit dem Release-Schlüssel signiert" in r.stderr
    assert umg.log("python.log") == ""  # kein Python-Lauf
    assert "worktree add" not in umg.log("git.log")
    assert umg.prom.read_text(encoding="utf-8") == vorher  # Metrik unverändert
    gesendet = umg.log("curl.stdin")
    assert "Wochenscan gescheitert: Tag nicht mit dem Release-Schlüssel signiert" in gesendet


def test_wochenscan_archiv_stand_unsigniert_bricht_ab(umg: Umgebung) -> None:
    umg.schalter("archiv-unsigniert")
    alt = "scanview_wochenscan_last_run_timestamp 111\n"
    umg.prom.write_text(alt, encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert umg.log("python.log") == ""
    assert "verify-commit HEAD" in umg.log("git.log")
    assert f"gpg.ssh.allowedSignersFile={umg.konf}/allowed_signers_git" in umg.log("git.log")
    assert umg.prom.read_text(encoding="utf-8") == alt
    assert "Wochenscan gescheitert: Archiv-Stand nicht signiert" in umg.log("curl.stdin")


def test_wochenscan_tag_mit_openpgp_signatur_bricht_ab(umg: Umgebung) -> None:
    """Git prüft eine OpenPGP-Signatur gegen den gpg-Schlüsselbund des Betriebskontos, nicht gegen
    den Anker — zugelassen ist nur eine SSH-Signatur, unabhängig vom Ergebnis von `tag -v`."""
    umg.schalter("tag-openpgp")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 2
    assert "Tag nicht mit dem Release-Schlüssel signiert (keine SSH-Signatur)" in r.stderr
    assert umg.log("python.log") == ""
    assert "worktree add" not in umg.log("git.log")
    assert "keine SSH-Signatur" in umg.log("curl.stdin")


def test_wochenscan_archiv_mit_openpgp_signatur_bricht_ab(umg: Umgebung) -> None:
    umg.schalter("archiv-openpgp")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 2
    assert "Archiv-Stand nicht signiert (keine SSH-Signatur)" in r.stderr
    assert umg.log("python.log") == ""


def test_wochenscan_prueft_ohne_gpg_und_x509(umg: Umgebung) -> None:
    """Zweite Sperre: Selbst wenn die Typprüfung fehlte, könnte git kein gpg/gpgsm aufrufen."""
    _ergebnis(umg, {}, [])
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 0, r.stderr
    pruefungen = [z for z in umg.log("git.log").splitlines()
                  if "verify-commit" in z or "tag -v" in z]
    assert len(pruefungen) == 2
    for zeile in pruefungen:
            assert "gpg.openpgp.program=false" in zeile and "gpg.x509.program=false" in zeile


def test_wochenscan_laeuft_nicht_doppelt(umg: Umgebung) -> None:
    import fcntl
    with (umg.betrieb / "wochenscan.lock").open("w") as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 2
    assert "läuft bereits" in r.stderr
    assert umg.log("git.log") == ""
    assert umg.log("curl.stdin") == ""


@pytest.mark.parametrize("schalter", ["tag-openpgp-ssh-text", "archiv-openpgp-ssh-text",
                                      "archiv-zwei-gpgsig"])
def test_wochenscan_ssh_kopfzeile_nur_im_text_zaehlt_nicht(umg: Umgebung, schalter: str) -> None:
    """Massgeblich ist die Signatur, die git prüft (letzter Signaturblock bzw. Commit-Kopf), nicht
    eine gleichlautende Zeile in der Nachricht."""
    umg.schalter(schalter)
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 2
    assert "keine SSH-Signatur" in r.stderr
    assert umg.log("python.log") == ""


def test_wochenscan_metrik_fuer_node_exporter_lesbar(umg: Umgebung) -> None:
    """Die Unit setzt UMask=0077; die Metrik liest node_exporter unter eigenem Nutzer."""
    _ergebnis(umg, {}, [])
    r = umg.lauf(WOCHENSCAN, umask=0o077)
    assert r.returncode == 0, r.stderr
    assert stat.S_IMODE(umg.prom.stat().st_mode) == 0o644


def test_wochenscan_sperre_haengt_nicht_an_kindprozessen(umg: Umgebung) -> None:
    """Ein Nachfahre, der den Lauf überlebt, darf die Sperre nicht weiter halten."""
    _ergebnis(umg, {}, [])
    umg.schalter("kind-bleibt")
    try:
        erst = umg.lauf(WOCHENSCAN)
        assert erst.returncode == 0, erst.stderr
        zweit = umg.lauf(WOCHENSCAN)
        assert zweit.returncode == 0, zweit.stderr
        assert "läuft bereits" not in zweit.stderr
    finally:
        for pid in umg.log("kind.pid").split():
            try:
                os.kill(int(pid), 15)
            except ProcessLookupError:
                pass


def test_wochenscan_tag_name_im_objekt_weicht_ab_bricht_ab(umg: Umgebung) -> None:
    (umg.steuer / "tag-objektname").write_text("v0.1.0", encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert umg.log("python.log") == ""
    assert "Wochenscan gescheitert: Tag nicht mit dem Release-Schlüssel signiert" in umg.log(
        "curl.stdin")


def test_wochenscan_erstlauf_legt_bekannt_leer_an(umg: Umgebung) -> None:
    """Ohne `bekannt.txt` bräche der echte Scan ab: die Attrappe prüft sie wie `cli._bekannt`."""
    pruefung = ('  [ -r "$BETRIEB/bekannt.txt" ] || '
                '{ echo "NACHWEIS-FEHLER wochenscan-bekannt: fehlt" >&2; exit 2; }\n')
    _skript(umg.bin / "python3", (umg.bin / "python3").read_text(encoding="utf-8").replace(
        "  ausgabe=\n", pruefung + "  ausgabe=\n", 1))
    umg.env["BETRIEB"] = str(umg.betrieb)
    _ergebnis(umg, {}, [])
    assert not (umg.betrieb / "bekannt.txt").exists()
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (umg.betrieb / "bekannt.txt").read_text(encoding="utf-8") == ""


def test_wochenscan_fetch_fehler_bricht_ab(umg: Umgebung) -> None:
    umg.schalter("fetch-scheitert")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert umg.log("python.log") == ""
    assert "Wochenscan gescheitert" in umg.log("curl.stdin")
    assert not umg.prom.exists()


def test_wochenscan_ohne_release_tag_bricht_ab(umg: Umgebung) -> None:
    (umg.steuer / "tags").write_text("latest\nv1.0.0-rc1\n", encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert umg.log("python.log") == ""
    assert "Wochenscan gescheitert" in umg.log("curl.stdin")


def test_wochenscan_blockierend_mit_neuer_id_meldet_und_setzt_nur_last_run(umg: Umgebung) -> None:
    _ergebnis(umg, {"0.2.0": ["CVE-2026-0001 hoch pkg", "CVE-2026-0002 kritisch pkg"],
                    "0.1.0": ["CVE-2026-0001 hoch pkg"]}, ["CVE-2026-0002"])
    (umg.steuer / "py-exit").write_text("1", encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode == 0, r.stdout + r.stderr  # Befund ≠ Betriebsfehler des Skripts
    m = _metrik(umg)
    assert m["blockierend"] == 2
    assert m["last_run_timestamp"] > JETZT_GROSS
    assert "last_success_timestamp" not in m
    gesendet = umg.log("curl.stdin")
    assert "Neuer Befund ≥ hoch: CVE-2026-0002" in gesendet
    assert "(Version 0.2.0)" in gesendet
    # bekannt.txt: alle aktuell blockierenden IDs, nach der Meldung.
    assert (umg.betrieb / "bekannt.txt").read_text(encoding="utf-8").split() == [
        "CVE-2026-0001", "CVE-2026-0002"]


def test_wochenscan_blockierend_ohne_neue_id_schweigt(umg: Umgebung) -> None:
    _ergebnis(umg, {"0.2.0": ["CVE-2026-0001 hoch pkg"]}, [])
    (umg.steuer / "py-exit").write_text("1", encoding="utf-8")
    assert umg.lauf(WOCHENSCAN).returncode == 0
    assert umg.log("curl.argv") == ""
    assert _metrik(umg)["blockierend"] == 1


def test_wochenscan_exit1_behaelt_alten_last_success(umg: Umgebung) -> None:
    umg.prom.write_text("scanview_wochenscan_last_run_timestamp 100\n"
                        "scanview_wochenscan_last_success_timestamp 90\n"
                        "scanview_wochenscan_blockierend 0\n", encoding="utf-8")
    _ergebnis(umg, {"0.2.0": ["CVE-2026-0001 hoch pkg"]}, [])
    (umg.steuer / "py-exit").write_text("1", encoding="utf-8")
    assert umg.lauf(WOCHENSCAN).returncode == 0
    m = _metrik(umg)
    assert m["last_success_timestamp"] == 90
    assert m["last_run_timestamp"] > JETZT_GROSS and m["blockierend"] == 1


def test_wochenscan_python_fehler_exit2_meldet_regel_und_laesst_metrik_stehen(
        umg: Umgebung) -> None:
    alt = ("scanview_wochenscan_last_run_timestamp 100\n"
           "scanview_wochenscan_last_success_timestamp 90\n"
           "scanview_wochenscan_blockierend 3\n")
    umg.prom.write_text(alt, encoding="utf-8")
    (umg.steuer / "py-exit").write_text("2", encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert "NACHWEIS-FEHLER wochenscan-sbom" in r.stderr  # stderr des Laufs wird durchgereicht
    assert umg.prom.read_text(encoding="utf-8") == alt
    assert "Wochenscan gescheitert: wochenscan-sbom" in umg.log("curl.stdin")
    assert (umg.betrieb / "bekannt.txt").read_text(encoding="utf-8") == ""  # nicht fortgeschrieben


def test_wochenscan_token_nie_in_argv(umg: Umgebung) -> None:
    _ergebnis(umg, {"0.2.0": ["CVE-2026-0002 kritisch pkg"]}, ["CVE-2026-0002"])
    (umg.steuer / "py-exit").write_text("1", encoding="utf-8")
    assert umg.lauf(WOCHENSCAN).returncode == 0
    for name in ("curl.argv", "git.log", "python.log", "podman.log"):
        assert TOKEN not in umg.log(name), name
    assert TOKEN in umg.log("curl.stdin")  # kommt über die Konfiguration auf stdin
    assert "-K -" in umg.log("curl.argv") or "-K" in umg.log("curl.argv")
    assert CHAT in umg.log("curl.stdin")


def test_wochenscan_telegram_fehler_laesst_bekannt_unveraendert(umg: Umgebung) -> None:
    _ergebnis(umg, {"0.2.0": ["CVE-2026-0002 kritisch pkg"]}, ["CVE-2026-0002"])
    (umg.steuer / "py-exit").write_text("1", encoding="utf-8")
    (umg.betrieb / "bekannt.txt").write_text("CVE-2026-0001\n", encoding="utf-8")
    umg.schalter("curl-scheitert")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0  # nicht gemeldet → der Lauf gilt als gescheitert
    assert (umg.betrieb / "bekannt.txt").read_text(encoding="utf-8") == "CVE-2026-0001\n"
    assert _metrik(umg)["blockierend"] == 1  # die Metrik des vollständigen Laufs bleibt


def test_wochenscan_unvollstaendige_umgebung_bricht_ab(umg: Umgebung) -> None:
    (umg.konf / "umgebung").write_text("TEXTFILE_DIR=/nirgends\n", encoding="utf-8")
    r = umg.lauf(WOCHENSCAN)
    assert r.returncode != 0
    assert "BETRIEB" in r.stderr
    assert umg.log("python.log") == ""


# --- aufbewahrung.sh -----------------------------------------------------------------------------

AUFB_METRIK = "scanview_aufbewahrung_last_success_timestamp"
ZIEL_VERZ = "scanview-nachweise"


def _git(wurzel: Path, *args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd or wurzel, env=env, check=True, text=True,
                          capture_output=True).stdout


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


@pytest.fixture
def aufb(tmp_path: Path) -> Umgebung:
    """Echtes `git` (Archiv-Klon eines Wegwerf-Ursprungs, mit Tag), Attrappe für `sftp`."""
    umg = _umgebung(tmp_path)
    (umg.bin / "git").unlink()
    umg.env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    ursprung = umg.wurzel / "ursprung"
    _git(umg.wurzel, "init", "-q", "-b", "main", str(ursprung))
    (ursprung / "releases").mkdir()
    (ursprung / "releases" / "datei.txt").write_text("nachweis\n", encoding="utf-8")
    _git(umg.wurzel, "add", ".", cwd=ursprung)
    _git(umg.wurzel, "commit", "-q", "-m", "erster", cwd=ursprung)
    _git(umg.wurzel, "tag", "v0.2.0", cwd=ursprung)
    (umg.betrieb / "scanview-nachweise").rmdir()
    _git(umg.wurzel, "clone", "-q", str(ursprung), str(umg.betrieb / "scanview-nachweise"))
    # Ein zweiter Commit im Ursprung: `git fetch` im Skript muss ihn mitnehmen.
    (ursprung / "releases" / "zwei.txt").write_text("zwei\n", encoding="utf-8")
    _git(umg.wurzel, "add", ".", cwd=ursprung)
    _git(umg.wurzel, "commit", "-q", "-m", "zweiter", cwd=ursprung)
    schluessel = umg.wurzel / "ziel-schluessel"
    schluessel.write_text("KEIN-ECHTER-SCHLUESSEL\n", encoding="utf-8")
    with (umg.konf / "umgebung").open("a", encoding="utf-8") as f:
        f.write(f"ZIEL_SFTP=sicherung@ziel.invalid\nZIEL_SSH_SCHLUESSEL={schluessel}\n"
                "ZIEL_PORT=23\n")
    return umg


def _bundle_name() -> str:
    return f"scanview-nachweise-{datetime.now(UTC).strftime('%Y-%m-%d')}.bundle"


def _aufb_metrik(umg: Umgebung) -> Path:
    return umg.textfile / "scanview_aufbewahrung.prom"


def test_aufbewahrung_erfolg_laedt_hoch_prueft_zurueck_und_setzt_metrik(aufb: Umgebung) -> None:
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode == 0, r.stdout + r.stderr
    name = _bundle_name()
    remote = aufb.wurzel / "remote" / ZIEL_VERZ
    pruefsumme = remote / f"{name.removesuffix('.bundle')}.sha256"
    assert (remote / name).is_file() and pruefsumme.is_file()
    soll = _sha256(remote / name)
    assert soll in pruefsumme.read_text(encoding="utf-8")
    # Lokale Kopie bleibt (geht mit der Sicherung des Hosts zusätzlich weg).
    lokal = aufb.betrieb / "aufbewahrung" / name
    assert lokal.is_file() and _sha256(lokal) == soll
    # Das Bundle enthält den frisch geholten zweiten Commit.
    refs = _git(aufb.wurzel, "bundle", "list-heads", str(lokal))
    kopf = _git(aufb.wurzel, "rev-parse", "origin/main", cwd=aufb.betrieb / "scanview-nachweise")
    assert kopf.strip() in refs and "refs/tags/v0.2.0" in refs
    zeile = _aufb_metrik(aufb).read_text(encoding="utf-8").strip()
    assert zeile.startswith(AUFB_METRIK + " ") and int(zeile.split()[1]) > JETZT_GROSS
    assert aufb.log("curl.argv") == ""  # kein Telegram bei Erfolg
    argv = aufb.log("sftp.argv")
    assert "-P 23" in argv and "-i " in argv and "sicherung@ziel.invalid" in argv
    assert f"put {lokal}" in aufb.log("sftp.batch")


def test_aufbewahrung_ziel_existiert_bricht_ohne_upload_ab(aufb: Umgebung) -> None:
    remote = aufb.wurzel / "remote" / ZIEL_VERZ
    remote.mkdir(parents=True)
    vorhanden = remote / _bundle_name()
    vorhanden.write_text("alt\n", encoding="utf-8")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert "existiert schon" in r.stderr
    assert "put " not in aufb.log("sftp.batch")
    assert vorhanden.read_text(encoding="utf-8") == "alt\n"  # nie überschrieben
    assert not _aufb_metrik(aufb).exists()
    assert "Aufbewahrung gescheitert" in aufb.log("curl.stdin")


def test_aufbewahrung_rueckleseprobe_hash_abweichend_ist_fehler(aufb: Umgebung) -> None:
    aufb.schalter("get-verfaelscht")
    _aufb_metrik(aufb).write_text(AUFB_METRIK + " 5\n", encoding="utf-8")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert "Rücklese" in r.stderr
    assert _aufb_metrik(aufb).read_text(encoding="utf-8") == AUFB_METRIK + " 5\n"
    assert "Aufbewahrung gescheitert" in aufb.log("curl.stdin")


def test_aufbewahrung_upload_fehler_setzt_keine_metrik(aufb: Umgebung) -> None:
    aufb.schalter("put-scheitert")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert not _aufb_metrik(aufb).exists()
    assert "Aufbewahrung gescheitert" in aufb.log("curl.stdin")


def test_aufbewahrung_ziel_nicht_erreichbar_ist_kein_freies_ziel(aufb: Umgebung) -> None:
    """Ein fehlgeschlagenes Listing darf nicht als „existiert nicht“ gelten."""
    aufb.schalter("ziel-nicht-erreichbar")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert "put " not in aufb.log("sftp.batch")
    assert not _aufb_metrik(aufb).exists()


def test_aufbewahrung_fetch_fehler_bricht_ab(aufb: Umgebung) -> None:
    shutil.rmtree(aufb.wurzel / "ursprung")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert aufb.log("sftp.argv") == ""
    assert "Aufbewahrung gescheitert" in aufb.log("curl.stdin")


def test_aufbewahrung_token_nie_in_argv(aufb: Umgebung) -> None:
    aufb.schalter("put-scheitert")
    assert aufb.lauf(AUFBEWAHRUNG).returncode != 0
    for name in ("curl.argv", "sftp.argv", "sftp.batch"):
        assert TOKEN not in aufb.log(name), name
    assert TOKEN in aufb.log("curl.stdin")


@pytest.mark.parametrize("verzeichnis", ["a b", "x;rm", "../oben", "-P", "a/b", "..", "ablage-ä"])
def test_aufbewahrung_zielverzeichnis_unzulaessig_bricht_ab(aufb: Umgebung,
                                                          verzeichnis: str) -> None:
    with (aufb.konf / "umgebung").open("a", encoding="utf-8") as f:
        f.write(f"ZIEL_VERZEICHNIS='{verzeichnis}'\n")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode == 2
    assert "ZIEL_VERZEICHNIS" in r.stderr
    assert aufb.log("sftp.argv") == ""


def test_aufbewahrung_metrik_fuer_node_exporter_lesbar(aufb: Umgebung) -> None:
    r = aufb.lauf(AUFBEWAHRUNG, umask=0o077)
    assert r.returncode == 0, r.stderr
    assert stat.S_IMODE(_aufb_metrik(aufb).stat().st_mode) == 0o644


def test_aufbewahrung_unvollstaendige_umgebung_bricht_ab(aufb: Umgebung) -> None:
    text = (aufb.konf / "umgebung").read_text(encoding="utf-8")
    (aufb.konf / "umgebung").write_text(
        "\n".join(z for z in text.splitlines() if not z.startswith("ZIEL_SFTP")) + "\n",
        encoding="utf-8")
    r = aufb.lauf(AUFBEWAHRUNG)
    assert r.returncode != 0
    assert "ZIEL_SFTP" in r.stderr
    assert aufb.log("sftp.argv") == ""


# --- shellcheck ----------------------------------------------------------------------------------


def _shellcheck_da() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        image = lade_werkzeuge(WERKZEUGE_JSON)["shellcheck"].image
    except KeyError:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                          check=False).returncode == 0


@pytest.mark.docker
def test_shellcheck_betriebsskripte() -> None:
    """Alle Skripte in `nachweise/betrieb/` über das gepinnte Image (wie die Deploy-Skripte).
    Fehlt Docker oder das Image, wird laut übersprungen (im Qualitätstor: Fehlschlag)."""
    werkzeugpflicht.verlange("docker + gepinntes shellcheck-Image", _shellcheck_da(),
                             "shellcheck der Betriebsskripte")
    skripte = sorted(p.name for p in BETRIEB_SKRIPTE.glob("*.sh"))
    assert {"wochenscan.sh", "aufbewahrung.sh"} <= set(skripte)
    befehl = docker_befehl(lade_werkzeuge(WERKZEUGE_JSON)["shellcheck"],
                           [f"/skripte/{name}" for name in skripte], {BETRIEB_SKRIPTE: "/skripte"})
    assert "--network" in befehl and "none" in befehl
    ergebnis = subprocess.run(befehl, capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_units_wochenscan() -> None:
    timer = (BETRIEB_SKRIPTE / "scanview-wochenscan.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Tue *-*-* 05:30:00" in timer
    assert "Persistent=true" in timer and "RandomizedDelaySec=10min" in timer
    dienst = (BETRIEB_SKRIPTE / "scanview-wochenscan.service").read_text(encoding="utf-8")
    assert "ExecStart=%h/scanview-betrieb/bin/wochenscan.sh" in dienst
    assert "Type=oneshot" in dienst
    assert "UMask=0077" in dienst


def test_units_aufbewahrung() -> None:
    timer = (BETRIEB_SKRIPTE / "scanview-aufbewahrung.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-01-15 04:00:00" in timer and "Persistent=true" in timer
    dienst = (BETRIEB_SKRIPTE / "scanview-aufbewahrung.service").read_text(encoding="utf-8")
    assert "ExecStart=%h/scanview-betrieb/bin/aufbewahrung.sh" in dienst
    assert "Type=oneshot" in dienst
    assert "UMask=0077" in dienst

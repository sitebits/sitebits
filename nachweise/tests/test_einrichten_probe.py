"""`deploy/platform/einrichten.sh` als root im Wegwerf-Container.

Wie beim Freischalt-Skript (`test_paket_freischalten.py`): gepinntes Image `deploy-probe`,
`--network none`, `--read-only`, `--cap-drop ALL` plus nur `CHOWN`, `FOWNER`, `DAC_OVERRIDE`,
`no-new-privileges`. Alle Orte, die das Skript beschreibt, sind tmpfs; Werkzeuge, die im Image
fehlen oder den Host verändern würden (rsync, curl, git, uv, systemctl, useradd, groupadd,
setfacl, runuser), sind protokollierende Stubs. `sqlite3` ist ein Stub über das Python-Modul
sqlite3 (`.backup` und `PRAGMA integrity_check` — genau das, was das Skript nutzt). Geprüft
wird das Verhalten als root: wohin die Sicherung geht, wann abgebrochen wird, was protokolliert
wird und mit welchem Exit-Code der Lauf endet."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import deploy_probe
from scanview_nachweise.betrieb import lies_updates
from scanview_nachweise.werkzeuge import lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
EINRICHTEN = REPO_WURZEL / "deploy" / "platform" / "einrichten.sh"
WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"

PASSWD = ("root:x:0:0:root:/root:/bin/bash\n"
          "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
          "scanview:x:990:990::/nonexistent:/usr/sbin/nologin\n"
          "caddy:x:991:991::/nonexistent:/usr/sbin/nologin\n")
GROUP = "root:x:0:\nnogroup:x:65534:\nscanview:x:990:\ncaddy:x:991:\nscanview-deploy:x:992:\n"

#: Protokollierende Stubs; `rsync` kopiert wirklich (einrichten.sh prüft danach nichts davon),
#: `systemctl` sabotiert auf Wunsch das Update-Protokoll (P2-14).
STUBS = {
    "protokoll": "#!/bin/sh\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >>/tmp/aufrufe\n",
    "rsync": ("#!/bin/bash\nprintf 'rsync %s\\n' \"$*\" >>/tmp/aufrufe\n"
              "quelle=${@: -2:1}; ziel=${@: -1}\n"
              "mkdir -p \"$ziel\" && cp -a \"$quelle.\" \"$ziel\"\n"),
    # /api/barrierefreiheit: vollständig; mit /tmp/ba-leer ohne Kontakt und Erklärung, mit
    # /tmp/ba-kaputt kein JSON, mit /tmp/rl-leer ohne Impressum und Datenschutz, mit
    # /tmp/rl-datenschutz-fehlt nur ohne Datenschutz.
    "curl": ("#!/bin/sh\n[ -e /tmp/curl-scheitert ] && exit 7\n"
             "case \"$*\" in *api/barrierefreiheit*)\n"
             "  k='\"kontakt\": \"mailto:a@x.example\"'; e='\"erklaerung\": \"https://x.example/e\"'\n"
             "  i='\"impressum\": \"/impressum\"'; d='\"datenschutz\": \"https://x.example/d\"'\n"
             "  [ -e /tmp/ba-leer ] && k='\"kontakt\": null' && e='\"erklaerung\": null'\n"
             "  [ -e /tmp/ba-kontakt-fehlt ] && k='\"kontakt\": null'\n"
             "  [ -e /tmp/rl-leer ] && i='\"impressum\": null' && d='\"datenschutz\": null'\n"
             "  [ -e /tmp/rl-datenschutz-fehlt ] && d='\"datenschutz\": null'\n"
             "  if [ -e /tmp/ba-kaputt ]; then echo 'kein json'\n"
             "  else echo \"{$k, $e, $i, $d}\"\n"
             "  fi; exit 0;; esac\n"
             "echo '{\"ok\": true}'\n"),
    "uv": ("#!/bin/sh\nprintf 'uv %s\\n' \"$*\" >>/tmp/aufrufe\n"
           "[ -e /tmp/uv-scheitert ] && exit 1\nmkdir -p .venv\n"),
    "git": "#!/bin/sh\necho 0123456789abcdef0123456789abcdef01234567\n",
    "systemctl": ("#!/bin/bash\nprintf 'systemctl %s\\n' \"$*\" >>/tmp/aufrufe\n"
                  "if [ \"$1\" = restart ] && [ -e /tmp/sabotage ]; then\n"
                  "  rm -rf /var/log/scanview && ln -s /tmp /var/log/scanview\nfi\n"),
    # sqlite3 über das Python-Modul; protokolliert die echte uid (runuser ist echt). Mit
    # /tmp/boeser-dienst hält der „Dienst“ nach dem Backup einen Schreib-Deskriptor auf die
    # Ausgabe offen und überschreibt sie später (Signal /tmp/los) — wie ein kompromittierter
    # Dienst, der die Sicherung nach der Prüfung beschädigen will.
    "sqlite3": ("#!/usr/local/bin/python3\nimport os, sqlite3, subprocess, sys\n"
                "db, befehl = sys.argv[1], sys.argv[2]\n"
                "with open('/tmp/aufrufe', 'a') as f:\n"
                "    f.write(f'sqlite3 uid={os.getuid()} {db} {befehl}\\n')\n"
                "quelle = sqlite3.connect(db)\n"
                "if befehl.startswith('.backup '):\n"
                "    ausgabe = befehl.split(' ', 1)[1].strip(\"'\")\n"
                "    ziel = sqlite3.connect(ausgabe)\n"
                "    quelle.backup(ziel); ziel.close()\n"
                "    if os.path.exists('/tmp/kaputt'):\n"
                "        with open(ausgabe, 'r+b') as f: f.write(b'KAPUTT' * 100)\n"
                "    if os.path.exists('/tmp/boeser-dienst'):\n"
                "        fd = os.open(ausgabe, os.O_WRONLY)\n"
                "        subprocess.Popen(['/usr/local/bin/python3', '-c', BOESE, str(fd)],\n"
                "                         pass_fds=[fd],\n"
                "                         start_new_session=True, stdin=subprocess.DEVNULL,\n"
                "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "else:\n"
                "    print(quelle.execute(befehl).fetchone()[0])\n"),
}
PROTOKOLL_STUBS = ("useradd", "groupadd", "setfacl")
BOESE = ("import os, sys, time\n"
         "fd = int(sys.argv[1])\n"
         "while not os.path.exists('/tmp/los'): time.sleep(0.02)\n"
         "os.pwrite(fd, b'KAPUTT' * 200, 0); os.fsync(fd)\n"
         "open('/tmp/fertig', 'w').close()\n")
#: /usr/bin/python3 im Image fehlt; der Shim protokolliert die uid jedes Aufrufs.
PYTHON_SHIM = ("#!/bin/sh\nprintf 'python3 uid=%s %s\\n' \"$(id -u)\" \"$1\" >>/tmp/aufrufe\n"
               # Die Integritätsprüfung (-I -c) bekommt die Sicherung über stdin: die geprüften
               # Bytes mitschneiden, damit der Test sie mit der übernommenen Datei vergleicht.
               "if [ \"$1\" = -I ] && [ \"$2\" = -c ]; then\n"
               # Mit /tmp/angriff-vor-pruefung löst die Prüfung selbst den Angriff aus: der
               # „böse Dienst“ schreibt über seinen alten Deskriptor, BEVOR die Bytes gelesen
               # werden — genau im Fenster zwischen Übernahme und Prüfung.
               "  if [ -e /tmp/angriff-vor-pruefung ]; then\n"
               "    touch /tmp/los\n"
               "    for _ in $(seq 250); do [ -e /tmp/fertig ] && break; sleep 0.02; done\n"
               "  fi\n"
               "  tee /tmp/geprueft.bin | /usr/local/bin/python3 \"$@\"; exit $?\nfi\n"
               "exec /usr/local/bin/python3 \"$@\"\n")

#: Treiber (root): Grundzustand, Vorbereitung, einrichten.sh im Repo-Modus, Zustand danach.
TREIBER = r'''
import json, os, stat, subprocess, sys

vorbereitung = sys.argv[1]
zwischen = sys.argv[2] if len(sys.argv) > 2 else None
GRUND = """
set -eu
install -m 0666 /dev/null /tmp/aufrufe  # root-eigen: Dienst (uid 990) und root schreiben hinein
R=/srv/scanview/repo
mkdir -p $R/server $R/web/dist $R/deploy/platform/sudoers.d
echo 'version = 1' >$R/server/uv.lock
echo '<!doctype html>' >$R/web/dist/index.html
echo '<!doctype html>' >$R/web/dist/portal.html
for f in scanview-server.service scanview-backup.service scanview-backup.timer \
  scanview-git-export.service scanview-git-export.timer scanview-server.env.beispiel \
  scanview-paket-freischalten sudoers.d/scanview-deploy; do echo x >$R/deploy/platform/$f; done
datenbank() {  # bestehende Installation mit Datenbank (Eigentümer scanview wie auf dem Host)
    install -d -o 990 -g 990 -m 0700 /srv/scanview/state
    python3 -c "import sqlite3; c = sqlite3.connect('/srv/scanview/state/scanview.db'); \\
c.execute('create table t (x)'); c.execute('insert into t values (42)'); c.commit()"
    chown 990:990 /srv/scanview/state/scanview.db
}
"""
subprocess.run(["bash", "-c", GRUND + vorbereitung], check=True)


def zustand(wurzeln):
    out = {}
    for w in wurzeln:
        if not os.path.lexists(w):
            continue
        for wurzel, ordner, dateien in os.walk(w):
            for name in [*ordner, *dateien, ""][:None]:
                p = os.path.join(wurzel, name) if name else wurzel
                s = os.lstat(p)
                typ = ("l" if stat.S_ISLNK(s.st_mode) else "d" if stat.S_ISDIR(s.st_mode)
                       else "f")
                out[p] = [typ, s.st_uid, s.st_gid, oct(stat.S_IMODE(s.st_mode))]
    return out


orte = ["/srv/scanview/state", "/var/backups", "/var/log/scanview", "/etc/zielordner",
        "/var/lib/scanview-install"]
def einrichten():
    return subprocess.run(["bash", "/probe/einrichten.sh", "/srv/scanview/repo"],
                          capture_output=True, text=True,
                          env=dict(os.environ, PATH="/probe/bin:/usr/local/bin:/usr/sbin:"
                                                    "/usr/bin:/sbin:/bin"))


erster = None
r = einrichten()
if zwischen is not None:  # zweiter Lauf nach einem Zwischenschritt (z. B. Fehler behoben)
    erster = {"rc": r.returncode, "stderr": r.stderr,
              "marker": os.path.exists("/etc/scanview/.ersteinrichtung-laeuft")}
    subprocess.run(["bash", "-c", zwischen], check=True)
    r = einrichten()
nach = ""
if os.path.exists("/tmp/nach.sh"):
    nach = subprocess.run(["bash", "/tmp/nach.sh"], capture_output=True, text=True).stdout
try:
    protokoll = open("/var/log/scanview/updates.log").read()
except OSError:
    protokoll = None
try:
    aufrufe = open("/tmp/aufrufe").read().splitlines()
except FileNotFoundError:
    aufrufe = []
print(json.dumps({"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
                  "zustand": zustand(orte), "protokoll": protokoll, "aufrufe": aufrufe,
                  "nach": nach, "erster": erster,
                  "marker": os.path.exists("/etc/scanview/.ersteinrichtung-laeuft")}))
'''


def _image() -> str:
    return lade_werkzeuge(WERKZEUGE_JSON)["deploy-probe"].image


def _probe_da() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        image = _image()
    except KeyError:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                          check=False).returncode == 0


probe_da = pytest.mark.skipif(not _probe_da(),
                              reason="docker oder gepinntes deploy-probe-Image fehlt")


@dataclass
class Lauf:
    rc: int
    stdout: str
    stderr: str
    zustand: dict[str, Any]
    protokoll: str | None
    aufrufe: list[str]
    nach: str = ""
    erster: dict[str, Any] | None = None
    marker: bool = False


def _probe(tmp_path: Path, vorbereitung: str, *, log_schreibbar: bool = True,
           zwischen: str | None = None) -> Lauf:
    assert deploy_probe.ORDNER.is_dir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin").chmod(0o755)
    for name, inhalt in {**STUBS, **{n: STUBS["protokoll"] for n in PROTOKOLL_STUBS}}.items():
        if name == "protokoll":
            continue
        if name == "sqlite3":
            inhalt = inhalt.replace("import os, sqlite3, subprocess, sys\n",
                                    f"import os, sqlite3, subprocess, sys\nBOESE = {BOESE!r}\n", 1)
        (tmp_path / "bin" / name).write_text(inhalt, encoding="utf-8")
        (tmp_path / "bin" / name).chmod(0o755)
    (tmp_path / "python3").write_text(PYTHON_SHIM, encoding="utf-8")
    (tmp_path / "python3").chmod(0o755)
    for name, inhalt in {"passwd": PASSWD, "group": GROUP, "treiber.py": TREIBER}.items():
        (tmp_path / name).write_text(inhalt, encoding="utf-8")
    tmpfs = ["/srv/scanview", "/var/lib", "/opt/scanview", "/etc/scanview", "/etc/systemd/system",
             "/var/backups", "/etc/zielordner", "/usr/local/sbin"]
    if log_schreibbar:
        tmpfs.append("/var/log")
    befehl = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
              "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
              "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges",
              "--user", "0:0"]
    # CAP_SETUID/SETGID: runuser wechselt wirklich zur uid des Dienstes.
    befehl += ["--cap-add", "SETUID", "--cap-add", "SETGID",
               "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=64m"]
    for ort in tmpfs:
        befehl += ["--tmpfs", f"{ort}:rw,nosuid,nodev,mode=0755,size=64m"]
    for quelle, ziel in {EINRICHTEN: "/probe/einrichten.sh", tmp_path / "bin": "/probe/bin",
                         tmp_path / "passwd": "/etc/passwd", tmp_path / "group": "/etc/group",
                         tmp_path / "treiber.py": "/probe/treiber.py",
                         tmp_path / "python3": "/usr/bin/python3"}.items():
        befehl += ["--mount", f"type=bind,source={quelle},target={ziel},readonly"]
    ergebnis = subprocess.run([*befehl, _image(), "/usr/local/bin/python3", "/probe/treiber.py",
                               vorbereitung, *([] if zwischen is None else [zwischen])],
                              capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL, timeout=120)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    d = json.loads(ergebnis.stdout)
    return Lauf(d["rc"], d["stdout"], d["stderr"], d["zustand"], d["protokoll"], d["aufrufe"],
                d["nach"], d["erster"], d["marker"])


def _veraendert(lauf: Lauf) -> bool:
    return any(a.split()[0] in ("useradd", "groupadd", "rsync", "systemctl", "uv")
               for a in lauf.aufrufe)


@pytest.mark.docker
@probe_da
def test_update_sicherung_ausserhalb_des_dienstbaums(tmp_path: Path) -> None:
    """P1-1: ein vom Dienst kontrollierbarer Pfad unter state/ (hier: update-sicherungen als
    Symlink auf einen fremden Ordner) wird nie angefasst; die Sicherung liegt root-eigen unter
    /var/backups/scanview/update-sicherungen und ist geprüft."""
    lauf = _probe(tmp_path, "datenbank\ninstall -d -m 0755 /etc/zielordner\n"
                            "ln -s /etc/zielordner /srv/scanview/state/update-sicherungen\n"
                            "chown -h 990:990 /srv/scanview/state/update-sicherungen")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.zustand["/etc/zielordner"] == ["d", 0, 0, "0o755"]  # Eigentümer/Rechte unverändert
    sicherungen = {p: v for p, v in lauf.zustand.items()
                   if p.startswith("/var/backups/scanview/update-sicherungen/")}
    assert len(sicherungen) == 1
    [(pfad, eintrag)] = sicherungen.items()
    assert pfad.endswith("-repo.db") and eintrag == ["f", 0, 0, "0o600"]
    assert lauf.zustand["/var/backups/scanview"] == ["d", 0, 0, "0o711"]
    assert lauf.zustand["/var/backups/scanview/update-sicherungen"] == ["d", 0, 0, "0o700"]
    assert not any(".stufe." in p for p in lauf.zustand)  # Stufenordner aufgeräumt
    # sqlite3 schreibt die Sicherung wirklich als Dienstnutzer (echtes runuser, uid 990); die
    # Integritätsprüfung der übernommenen Kopie läuft ebenfalls als uid 990 (python3 über
    # stdin); root startet nie sqlite3 und parst die Datei nicht.
    assert any(a.startswith("sqlite3 uid=990 /srv/scanview/state/scanview.db .backup")
               for a in lauf.aufrufe), lauf.aufrufe
    assert not any(a.startswith("sqlite3 uid=0") for a in lauf.aufrufe)
    # Das Urteil entsteht unter einer eigenen, unbeteiligten Identität (nobody),
    # nicht unter der des Dienstes, gegen den es schützen soll.
    assert any(a.startswith("python3 uid=65534 -I") for a in lauf.aufrufe), lauf.aufrufe
    assert not any(a.startswith("python3 uid=990 -I") for a in lauf.aufrufe)
    assert lauf.protokoll is not None and f"sicherung={pfad} " in lauf.protokoll
    assert "ergebnis=ok" in lauf.protokoll


@pytest.mark.parametrize("spur", [
    "install -d -o 990 -g 990 -m 0700 /srv/scanview/state",
    "echo X=1 >/etc/scanview/scanview-server.env",
    "install -d /opt/scanview/server",
    "datenbank; mv /srv/scanview/state/scanview.db /srv/scanview/state/weg.db; "
    "ln -s weg.db /srv/scanview/state/scanview.db",
], ids=["state-ohne-db", "env-vorhanden", "server-vorhanden", "db-symlink"])
@pytest.mark.docker
@probe_da
def test_update_ohne_datenbank_bricht_ab(tmp_path: Path, spur: str) -> None:
    """P1-8: Rest einer Installation, aber keine reguläre Datenbank (fehlender Mount, verlorener
    Bestand) → Abbruch vor jeder Änderung, keine „Ersteinrichtung“."""
    lauf = _probe(tmp_path, spur)
    assert lauf.rc != 0
    assert "vorhandene Installation" in lauf.stderr
    assert not _veraendert(lauf)
    # Der Abbruch ist nachweisrelevant und steht im Update-Protokoll.
    assert lauf.protokoll is not None
    assert "ergebnis=abgebrochen grund=db-fehlt" in lauf.protokoll


@pytest.mark.docker
@probe_da
def test_ersteinrichtung_nur_auf_leerem_host(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "")
    assert lauf.rc == 0, lauf.stderr
    assert "Ersteinrichtung" in lauf.stdout
    assert lauf.protokoll is not None and "sicherung=keine" in lauf.protokoll


@pytest.mark.docker
@probe_da
def test_protokoll_nicht_schreibbar_vor_jeder_aenderung(tmp_path: Path) -> None:
    """P2-14: ist das Protokollziel nicht beschreibbar (hier: /var/log schreibgeschützt), bricht
    der Lauf ab, bevor der Host verändert wird."""
    lauf = _probe(tmp_path, "datenbank", log_schreibbar=False)
    assert lauf.rc != 0
    assert "nicht schreibbar" in lauf.stderr
    assert not _veraendert(lauf)
    assert not any(p.startswith("/var/backups/scanview") for p in lauf.zustand)


@pytest.mark.docker
@probe_da
def test_gescheiterte_abschlusszeile_ist_kein_erfolg(tmp_path: Path) -> None:
    """P2-14: scheitert die Abschlusszeile des Protokolls, endet ein sonst gelungener Lauf mit
    Exit ≠ 0 (hier sabotiert ein Stub /var/log/scanview während des Laufs)."""
    lauf = _probe(tmp_path, "datenbank\ntouch /tmp/sabotage")
    assert "systemctl restart scanview-server.service" in lauf.aufrufe
    assert lauf.rc != 0
    assert "Protokoll" in lauf.stderr and "gescheitert" in lauf.stderr


@pytest.mark.docker
@probe_da
def test_offener_deskriptor_des_dienstes_erreicht_die_sicherung_nicht(tmp_path: Path) -> None:
    """root übernimmt eine neue, root-eigene Kopie und prüft
    genau diese (als Dienstnutzer); ein Deskriptor, den der Dienst beim Backup offen hielt und
    danach beschreibt, verändert die übernommene Sicherung nicht."""
    lauf = _probe(tmp_path, "datenbank\ntouch /tmp/boeser-dienst\n"
                            "cat >/tmp/nach.sh <<'NACH'\n"
                            "touch /tmp/los\n"
                            "for _ in $(seq 250); do\n"
                            "  [ -e /tmp/fertig ] && break; sleep 0.02\n"
                            "done\n"
                            "[ -e /tmp/fertig ] && echo GESCHRIEBEN\n"
                            "for f in /var/backups/scanview/update-sicherungen/*.db; do\n"
                            "  python3 -c 'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); "
                            "print(c.execute(\"select x from t\").fetchone()[0])' \"$f\"\n"
                            "done\nNACH\n")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.nach.startswith("GESCHRIEBEN\n"), lauf.nach
    assert lauf.nach.rstrip().endswith("42"), lauf.nach


@pytest.mark.docker
@probe_da
def test_abgebrochene_ersteinrichtung_laeuft_beim_zweiten_mal_weiter(tmp_path: Path) -> None:
    """Scheitert die Ersteinrichtung nach dem Anlegen von state/, env und
    /opt/scanview/server (hier: uv sync), bleibt ein root-eigener Marker; ein zweiter Lauf setzt
    die Ersteinrichtung fort statt „Update ohne Datenbank“ zu melden, und entfernt den Marker."""
    lauf = _probe(tmp_path, "touch /tmp/uv-scheitert", zwischen="rm /tmp/uv-scheitert")
    assert lauf.erster is not None
    assert lauf.erster["rc"] != 0 and lauf.erster["marker"], lauf.erster
    assert lauf.rc == 0, lauf.stderr
    assert not lauf.marker
    assert lauf.protokoll is not None
    zeilen = lauf.protokoll.splitlines()
    assert "ergebnis=abgebrochen" in zeilen[0]
    assert "ergebnis=ok" in zeilen[-1] and "ersteinrichtung=fortgesetzt" in zeilen[-1]


@pytest.mark.parametrize("marker", [
    "install -d /etc/scanview; touch /etc/scanview/.ersteinrichtung-laeuft; "
    "chown 990:990 /etc/scanview/.ersteinrichtung-laeuft",
    "install -d /etc/scanview; ln -s /tmp/x /etc/scanview/.ersteinrichtung-laeuft",
], ids=["marker-nicht-root", "marker-symlink"])
@pytest.mark.docker
@probe_da
def test_nur_root_eigener_marker_erlaubt_fortsetzung(tmp_path: Path, marker: str) -> None:
    lauf = _probe(tmp_path, f"install -d -o 990 -g 990 -m 0700 /srv/scanview/state; {marker}")
    assert lauf.rc != 0
    assert "vorhandene Installation" in lauf.stderr
    assert not _veraendert(lauf)


@pytest.mark.docker
@probe_da
def test_nach_dienststart_nie_wieder_ersteinrichtung(tmp_path: Path) -> None:
    """Scheitert die Ersteinrichtung erst NACH
    dem Dienststart (hier: Gesundheitsprüfung), kann es schon eine Datenbank gegeben haben. Fehlt
    sie beim nächsten Lauf (Mount weg, Bestand verloren), ist das ein protokollierter Abbruch —
    keine erneute Ersteinrichtung ohne Sicherung, auch wenn der Marker noch liegt."""
    lauf = _probe(tmp_path, "touch /tmp/curl-scheitert", zwischen="rm /tmp/curl-scheitert")
    assert lauf.erster is not None and lauf.erster["rc"] != 0
    assert lauf.zustand.get("/var/lib/scanview-install/installiert", [None])[0] == "f"
    assert lauf.zustand["/var/lib/scanview-install/installiert"][1:] == [0, 0, "0o600"]
    assert lauf.rc != 0
    assert "vorhandene Installation" in lauf.stderr
    assert lauf.protokoll is not None
    assert "ergebnis=abgebrochen grund=db-fehlt" in lauf.protokoll.splitlines()[-1]
    assert "ersteinrichtung=fortgesetzt" not in lauf.protokoll.splitlines()[-1]


@pytest.mark.docker
@probe_da
def test_geprueft_wird_genau_die_uebernommene_sicherung(tmp_path: Path) -> None:
    """Der Dienst beschädigt die Quelle über seinen offen gehaltenen Deskriptor
    im Fenster ZWISCHEN Übernahme und Prüfung (ausgelöst aus dem Prüfprozess). Geprüft und
    übernommen wird trotzdem dieselbe, intakte Kopie — ein Rückfall auf „Rohdatei/Stufe prüfen“
    wird rot (Mutation im Report)."""
    lauf = _probe(tmp_path, "datenbank\ntouch /tmp/boeser-dienst /tmp/angriff-vor-pruefung\n"
                            "cat >/tmp/nach.sh <<'NACH'\n"
                            "[ -e /tmp/fertig ] && echo ANGRIFF-VOR-PRUEFUNG\n"
                            "sha256sum </tmp/geprueft.bin\n"
                            "cat /var/backups/scanview/update-sicherungen/*.db | sha256sum\n"
                            "NACH\n")
    assert lauf.rc == 0, lauf.stderr
    angriff, geprueft, uebernommen = lauf.nach.splitlines()
    assert angriff == "ANGRIFF-VOR-PRUEFUNG"
    assert geprueft == uebernommen


@pytest.mark.docker
@probe_da
def test_ungueltige_sicherung_bleibt_nicht_unter_regulaerem_namen(tmp_path: Path) -> None:
    """Scheitert die Integritätsprüfung, liegt die Datei nur als *.ungueltig vor —
    nie unter dem regulären Sicherungsnamen — und keine *.roh bleibt zurück."""
    lauf = _probe(tmp_path, "datenbank\ntouch /tmp/kaputt")
    assert lauf.rc != 0
    assert "fehlerhaft" in lauf.stderr
    namen = [p for p in lauf.zustand if p.startswith("/var/backups/scanview/update-sicherungen/")]
    assert namen and all(p.endswith(".ungueltig") for p in namen), namen
    assert not any(p.endswith(".roh") for p in lauf.zustand)
    assert not any(".stufe." in p for p in lauf.zustand)


@pytest.mark.parametrize("marke", ["ba-leer", "ba-kontakt-fehlt"])
@pytest.mark.docker
@probe_da
def test_fehlende_barrierefreiheitsangaben_warnen_ohne_abbruch(tmp_path: Path, marke: str) -> None:
    """Fehlen Kontakt oder Erklärung der Instanz, schreibt der Lauf eine Warnung ins
    Update-Protokoll und nach stderr — das Update gelingt trotzdem."""
    lauf = _probe(tmp_path, f"touch /tmp/{marke}")
    assert lauf.rc == 0, lauf.stderr
    assert "WARNUNG" in lauf.stderr and "barrierefreiheit" in lauf.stderr.lower()
    assert lauf.protokoll is not None
    zeile = lauf.protokoll.splitlines()[-1]
    assert "ergebnis=ok" in zeile and "warnung=barrierefreiheit-angaben-fehlen" in zeile
    assert "impressum" not in zeile


@pytest.mark.parametrize("marke", ["rl-leer", "rl-datenschutz-fehlt"])
@pytest.mark.docker
@probe_da
def test_fehlendes_impressum_oder_datenschutz_warnt_ohne_abbruch(tmp_path: Path,
                                                                marke: str) -> None:
    """Fehlt der Link zu Impressum oder Datenschutzhinweisen, schreibt der Lauf eine Warnung ins
    Update-Protokoll und nach stderr — das Update gelingt trotzdem."""
    lauf = _probe(tmp_path, f"touch /tmp/{marke}")
    assert lauf.rc == 0, lauf.stderr
    assert "WARNUNG: Impressum/Datenschutz" in lauf.stderr
    assert lauf.protokoll is not None
    zeile = lauf.protokoll.splitlines()[-1]
    assert "ergebnis=ok" in zeile and "warnung=impressum-datenschutz-fehlen" in zeile
    assert "barrierefreiheit" not in zeile


@pytest.mark.docker
@probe_da
def test_beide_warnungen_in_einer_zeile_und_lesbar(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "touch /tmp/ba-leer /tmp/rl-leer")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.protokoll is not None
    zeile = lauf.protokoll.splitlines()[-1]
    assert zeile.endswith(" warnung=barrierefreiheit-angaben-fehlen"
                          " warnung=impressum-datenschutz-fehlen")
    [z] = lies_updates(zeile + "\n")
    assert z.zusaetze[-2:] == ("warnung=barrierefreiheit-angaben-fehlen",
                               "warnung=impressum-datenschutz-fehlen")


@pytest.mark.docker
@probe_da
def test_vollstaendige_barrierefreiheitsangaben_ohne_warnung(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "")
    assert lauf.rc == 0, lauf.stderr
    assert "barrierefreiheit" not in lauf.stderr.lower()
    assert "impressum" not in lauf.stderr.lower()
    assert lauf.protokoll is not None and "warnung=" not in lauf.protokoll


@pytest.mark.docker
@probe_da
def test_unlesbare_barrierefreiheitsantwort_warnt_ebenfalls(tmp_path: Path) -> None:
    """Eine Antwort, die kein JSON ist, belegt die Angaben nicht: Warnung statt Stille. Die eine
    Kennung deckt auch Impressum/Datenschutz ab (dieselbe Antwort), keine zweite."""
    lauf = _probe(tmp_path, "touch /tmp/ba-kaputt")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.protokoll is not None
    assert lauf.protokoll.splitlines()[-1].endswith(" warnung=barrierefreiheit-nicht-pruefbar")

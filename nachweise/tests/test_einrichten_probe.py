"""`deploy/platform/einrichten.sh` als root im Wegwerf-Container.

Wie beim Freischalt-Skript (`test_paket_freischalten.py`): gepinntes Image `deploy-probe`,
`--network none`, `--read-only`, `--cap-drop ALL` plus nur `CHOWN`, `FOWNER`, `DAC_OVERRIDE`,
`SETUID`/`SETGID` (runuser ist echt), `FSETID` (setgid-Ordner), `no-new-privileges`. Alle
Orte, die das Skript beschreibt, sind tmpfs; Werkzeuge, die im Image fehlen oder den Host
verändern würden (rsync, curl, git, uv, systemctl, useradd, groupadd, usermod, setfacl,
loginctl), sind protokollierende Stubs. `sqlite3` ist ein Stub über das Python-Modul
sqlite3 (`.backup` und `PRAGMA integrity_check` — genau das, was das Skript nutzt). Geprüft
wird das Verhalten als root: wohin die Sicherung geht, wann abgebrochen wird, was protokolliert
wird und mit welchem Exit-Code der Lauf endet."""

from __future__ import annotations

import hashlib
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
PLATFORM = REPO_WURZEL / "deploy" / "platform"
EINRICHTEN = PLATFORM / "einrichten.sh"
WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"

PASSWD = ("root:x:0:0:root:/root:/bin/bash\n"
          "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
          "scanview:x:990:990::/nonexistent:/usr/sbin/nologin\n"
          "caddy:x:991:991::/nonexistent:/usr/sbin/nologin\n"
          "scanview-import:x:993:993::/var/lib/scanview-import:/usr/sbin/nologin\n")
GROUP = ("root:x:0:\nnogroup:x:65534:\nscanview:x:990:scanview-import\ncaddy:x:991:\n"
         "scanview-deploy:x:992:\nscanview-import:x:993:\nscanview-jobs:x:994:scanview-import\n")

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
             # Jobläufer: mit /tmp/worker-aktiv meldet die Gesundheit den Worker als aktiv.
             "if [ -e /tmp/worker-aktiv ]; then echo '{\"ok\": true, \"worker\": \"aktiv\"}'\n"
             "else echo '{\"ok\": true}'; fi\n"),
    # Import-Umgebung (mit UV_PYTHON_INSTALL_DIR): legt wie uv einen Interpreter im Python-Ordner
    # und die Umgebung mit Prüfer an, protokolliert die uid (runuser ist echt) und die Umgebung;
    # mit /tmp/uv-bau-scheitert scheitert der Bau, mit /tmp/uv-ohne-pruefer fehlt der Prüfer, mit
    # /tmp/uv-symlink ist bin/ ein Symlink.
    "uv": ("#!/bin/sh\nprintf 'uv %s\\n' \"$*\" >>/tmp/aufrufe\n"
           "[ -e /tmp/uv-scheitert ] && exit 1\nmkdir -p .venv\n"
           "[ -n \"$UV_PYTHON_INSTALL_DIR\" ] || exit 0\n"
           "[ -e /tmp/uv-bau-scheitert ] && exit 1\n"
           "printf 'uv-bau uid=%s pwd=%s python=%s vorzug=%s einschraenkungen=%s home=%s\\n' "
           "\"$(id -u)\" \"$(pwd)\" \"$UV_PYTHON\" \"$UV_PYTHON_PREFERENCE\" "
           "\"$(cat \"$UV_BUILD_CONSTRAINT\" | grep -c '==')\" \"$HOME\" >>/tmp/aufrufe\n"
           "p=$UV_PYTHON_INSTALL_DIR/cpython-$UV_PYTHON-linux-x86_64-gnu/bin\n"
           "mkdir -p \"$p\" && printf '#!/bin/sh\\n' >\"$p/python3.12\"\n"
           "chmod 755 \"$p/python3.12\"\n"
           "if [ -e /tmp/uv-symlink ]; then ln -s /tmp .venv/bin; exit 0; fi\n"
           "mkdir -p .venv/bin && ln -s \"$p/python3.12\" .venv/bin/python\n"
           "[ -e /tmp/uv-ohne-pruefer ] || "
           "{ printf '#!/bin/sh\\n' >.venv/bin/scanview-pruefen;"
           " chmod 755 .venv/bin/scanview-pruefen; }\n"),
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
PROTOKOLL_STUBS = ("useradd", "groupadd", "setfacl", "usermod", "loginctl", "gpasswd")
#: Jobläufer: `systemctl --version` liefert /tmp/systemd-version (Standard 257, Debian 13); wie
#: systemd setzt `daemon-reload` MemoryMax der user@-Drop-ins in die (nachgebildete) cgroup —
#: außer mit /tmp/ram-grenze-wirkt-nicht.
STUBS["systemctl"] += (
    "if [ \"$1\" = --version ]; then\n"
    "  echo \"systemd $(cat /tmp/systemd-version 2>/dev/null || echo 257) (257.9-1~deb13u1)\"\n"
    "  echo '+PAM +AUDIT'; exit 0\nfi\n"
    "if [ \"$1\" = daemon-reload ] && [ ! -e /tmp/ram-grenze-wirkt-nicht ]; then\n"
    "  for f in /etc/systemd/system/user@*.service.d/scanview-joblaeufer.conf; do\n"
    "    [ -f \"$f\" ] || continue\n"
    "    u=${f#/etc/systemd/system/user@}; u=${u%%.service.d/*}\n"
    "    mb=$(sed -n 's/^MemoryMax=\\([0-9]*\\)M$/\\1/p' \"$f\")\n"
    "    d=/sys/fs/cgroup/user.slice/user-$u.slice/user@$u.service\n"
    "    mkdir -p \"$d\" && echo $((mb * 1024 * 1024)) >\"$d/memory.max\"\n"
    "  done\nfi\n")
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
mkdir -p $R/server $R/web/dist $R/deploy/platform/sudoers.d \\
  $R/deploy/platform/scanview-server.service.d $R/deploy/platform/scanview-backup.service.d \\
  $R/deploy/platform/scanview-import-worker.service.d $R/import
echo 'version = 1' >$R/import/uv.lock
echo '[project]' >$R/import/pyproject.toml
echo 'version = 1' >$R/server/uv.lock
echo '<!doctype html>' >$R/web/dist/index.html
echo '<!doctype html>' >$R/web/dist/portal.html
for f in scanview-server.service scanview-backup.service scanview-backup.timer \
  scanview-git-export.service scanview-git-export.timer scanview-server.env.beispiel \
  scanview-paket-freischalten sudoers.d/scanview-deploy scanview-import-worker.service \\
  scanview-joblaeufer.env.beispiel scanview-server.service.d/joblaeufer.conf \\
  scanview-backup.service.d/joblaeufer.conf import-bau-einschraenkungen.txt \\
  scanview-import-uebergabe scanview-import-uebergabe.service scanview-import-uebergabe.timer \\
  scanview-server.service.d/import.conf scanview-import-worker.service.d/import.conf; do
  echo x >$R/deploy/platform/$f; done
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
        "/var/lib/scanview-install", "/srv/scanview/jobs", "/srv/scanview/berichte",
        "/var/lib/scanview-import", "/etc/systemd/system", "/etc/scanview",
        "/srv/scanview/uploads", "/srv/scanview/uebergabe", "/srv/scanview/uebergabe-meldungen",
        "/opt/scanview/import/.venv", "/opt/scanview/python", "/usr/local/sbin", "/var/tmp"]
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
           zwischen: str | None = None, memtotal_kb: int = 4 * 1024 * 1024,
           dbus: bool = True) -> Lauf:
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
             "/var/backups", "/etc/zielordner", "/usr/local/sbin", "/var/tmp"]
    if log_schreibbar:
        tmpfs.append("/var/log")
    befehl = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
              "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
              "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges",
              "--user", "0:0"]
    # CAP_SETUID/SETGID: runuser wechselt wirklich zur uid des Dienstes.
    # CAP_FSETID: setgid-Ordner des Jobläufers (root ist nicht in deren Gruppe; ohne die
    # Fähigkeit löscht der Kernel das Bit beim chmod).
    befehl += ["--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "FSETID",
               "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=64m"]
    for ort in tmpfs:
        # exec: Dockers tmpfs-Vorgabe noexec ließe `[[ -x … ]]` (Import-Umgebung) scheitern.
        befehl += ["--tmpfs", f"{ort}:rw,nosuid,nodev,exec,mode=0755,size=64m"]
    for quelle, ziel in {EINRICHTEN: "/probe/einrichten.sh", tmp_path / "bin": "/probe/bin",
                         tmp_path / "passwd": "/etc/passwd", tmp_path / "group": "/etc/group",
                         tmp_path / "treiber.py": "/probe/treiber.py",
                         tmp_path / "python3": "/usr/bin/python3"}.items():
        befehl += ["--mount", f"type=bind,source={quelle},target={ziel},readonly"]
    # Jobläufer: nachgebildeter Host — cgroup-Baum und /run/user als tmpfs (die Vorbereitung legt
    # an, was ein Test braucht), /proc/meminfo mit vorgegebenem Gesamtspeicher, dbus-user-session
    # als vorhandene Unit-Datei, die echten Vorlagen unter /probe/platform (Render-Vergleich).
    befehl += ["--tmpfs", "/sys/fs/cgroup:rw,nosuid,nodev,mode=0755,size=1m",
               "--tmpfs", "/run/user:rw,nosuid,nodev,mode=0755,size=1m"]
    (tmp_path / "meminfo").write_text(f"MemTotal:       {memtotal_kb} kB\n"
                                      "MemAvailable:    1000 kB\n", encoding="utf-8")
    (tmp_path / "dbus.socket").write_text("[Socket]\n", encoding="utf-8")
    mounts = {tmp_path / "meminfo": "/proc/meminfo", PLATFORM: "/probe/platform"}
    if dbus:
        mounts[tmp_path / "dbus.socket"] = "/usr/lib/systemd/user/dbus.socket"
    for quelle, ziel in mounts.items():
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


# --- Jobläufer (scanview-import-worker) -----------------------------------------------------------
# Nachgebildet ist der Host, nicht systemd: Units, Linger und User-Manager laufen nicht wirklich
# (Stubs protokollieren die Aufrufe). Ob der Worker seinen User-Manager erreicht und die Isolation
# greift, prüft sein Selbsttest beim Start auf dem echten Host (`einrichten.sh` wartet darauf).

#: Host mit allen Vorbedingungen (Debian 13, cgroup v2, memory an user@993 delegiert, Bus da) und
#: echten Vorlagen; Schalter an.
JL_HOST = r"""
for f in scanview-import-worker.service scanview-joblaeufer.env.beispiel \
  scanview-server.service.d/joblaeufer.conf scanview-backup.service.d/joblaeufer.conf \
  import-bau-einschraenkungen.txt scanview-import-uebergabe scanview-import-uebergabe.service \
  scanview-import-uebergabe.timer scanview-server.service.d/import.conf \
  scanview-import-worker.service.d/import.conf; do
  cp /probe/platform/$f $R/deploy/platform/$f
done
install -d -m 0755 /etc/scanview
sed 's/^SCANVIEW_JOBLAEUFER=0$/SCANVIEW_JOBLAEUFER=1/' \
  /probe/platform/scanview-joblaeufer.env.beispiel >/etc/scanview/scanview-joblaeufer.env
echo 'cpuset cpu io memory pids' >/sys/fs/cgroup/cgroup.controllers
UC=/sys/fs/cgroup/user.slice/user-993.slice/user@993.service
mkdir -p $UC && echo 'cpu memory pids' >$UC/cgroup.controllers
install -d -o 993 -g 993 -m 0700 /run/user/993
python3 -c "import socket; socket.socket(socket.AF_UNIX).bind('/run/user/993/bus')"
touch /tmp/worker-aktiv
"""
#: Nach dem Lauf: installierte Vorlagen (Prüfsummen) und die von einrichten.sh geschriebenen
#: Drop-ins (Inhalt) für den Render-Vergleich.
JL_NACH = r"""
cat >/tmp/nach.sh <<'NACH'
cd /etc/systemd/system
sha256sum scanview-import-worker.service scanview-server.service.d/joblaeufer.conf \
  scanview-backup.service.d/joblaeufer.conf
echo ---; cat scanview-import-worker.service.d/laufzeit.conf
echo ---; cat user@993.service.d/scanview-joblaeufer.conf
NACH
"""
JL_UNIT = "/etc/systemd/system/scanview-import-worker.service"


def _jl_veraendert(lauf: Lauf) -> bool:
    """Wie `_veraendert`, aber `systemctl --version` (nur lesend) zählt nicht."""
    return any(a.split()[0] in ("useradd", "groupadd", "usermod", "loginctl", "rsync", "uv")
               or (a.startswith("systemctl ") and a != "systemctl --version")
               for a in lauf.aufrufe)


@pytest.mark.docker
@probe_da
def test_joblaeufer_aus_ab_werk(tmp_path: Path) -> None:
    """Ab Werk aus: nur die Konfigurationsdatei entsteht (root, 0640, Schalter 0); kein Nutzer,
    kein Ordner, keine Unit, kein Linger, keine Systemd-Abfrage — darauf stützt sich auch der
    Byte-Vergleich mit 0.5.0 (`test_instanzen_pfade.py`)."""
    lauf = _probe(tmp_path, "cp /probe/platform/scanview-joblaeufer.env.beispiel "
                            "$R/deploy/platform/")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.zustand["/etc/scanview/scanview-joblaeufer.env"] == ["f", 0, 0, "0o640"]
    assert "Jobläufer aus" in lauf.stdout
    assert not any(a.split()[0] in ("usermod", "loginctl") or "import-worker" in a
                   or a == "systemctl --version" or "user@" in a for a in lauf.aufrufe), \
        lauf.aufrufe
    assert not any(p.startswith(("/srv/scanview/jobs", "/srv/scanview/berichte",
                                 "/var/lib/scanview-import", JL_UNIT,
                                 "/etc/systemd/system/scanview-server.service.d"))
                   for p in lauf.zustand)


@pytest.mark.docker
@probe_da
def test_joblaeufer_ein_richtet_worker_ein(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, JL_HOST + JL_NACH)
    assert lauf.rc == 0, lauf.stderr
    z = lauf.zustand
    # Rechte: Worker 993 (scanview-import), Gruppe 994 (scanview-jobs).
    assert z["/var/lib/scanview-import"] == ["d", 993, 993, "0o700"]
    assert z["/srv/scanview/jobs"] == ["d", 0, 994, "0o3770"]
    assert z["/srv/scanview/jobs/jobs.db"] == ["f", 0, 994, "0o660"]
    assert z["/srv/scanview/berichte"] == ["d", 993, 994, "0o2750"]
    for p in (JL_UNIT, "/etc/systemd/system/scanview-server.service.d/joblaeufer.conf",
              "/etc/systemd/system/scanview-backup.service.d/joblaeufer.conf",
              "/etc/systemd/system/scanview-import-worker.service.d/laufzeit.conf",
              "/etc/systemd/system/user@993.service.d/scanview-joblaeufer.conf"):
        assert z[p] == ["f", 0, 0, "0o644"], p
    # Render-Vergleich: Vorlagen bytegleich, uid-abhängige Drop-ins genau so.
    summen, laufzeit, ram = lauf.nach.split("---\n")
    for zeile in summen.splitlines():
        summe, name = zeile.split()
        quelle = PLATFORM / name.replace("scanview-import-worker.service", "scanview-import-worker"
                                         ".service")
        assert hashlib.sha256(quelle.read_bytes()).hexdigest() == summe, name
    assert laufzeit.splitlines()[1:] == [
        "[Unit]", "Requires=user@993.service", "After=user@993.service", "[Service]",
        "Environment=XDG_RUNTIME_DIR=/run/user/993", "BindReadOnlyPaths=/run/user/993"]
    assert ram.splitlines()[1:] == ["[Service]", "MemoryMax=2304M", "MemorySwapMax=0"]
    a = lauf.aufrufe
    assert "usermod -a -G scanview,scanview-jobs scanview-import" in a
    assert "loginctl enable-linger scanview-import" in a
    assert "systemctl start user@993.service" in a
    # Worker erst nach dem Webdienst (der braucht Gruppe und Pfade aus seinem Drop-in).
    assert (a.index("systemctl restart scanview-server.service")
            < a.index("systemctl enable scanview-import-worker.service")
            < a.index("systemctl restart scanview-import-worker.service"))
    assert "Jobläufer aktiv" in lauf.stdout
    assert lauf.protokoll is not None and "ergebnis=ok" in lauf.protokoll.splitlines()[-1]


@pytest.mark.parametrize(("vorbereitung", "kwargs", "meldung"), [
    ("echo 252 >/tmp/systemd-version", {}, "systemd 252"),
    ("", {"memtotal_kb": 3 * 1024 * 1024 - 1}, "zu wenig Arbeitsspeicher"),
    ("rm /sys/fs/cgroup/cgroup.controllers", {}, "cgroup v2"),
    ("echo 'cpu io pids' >/sys/fs/cgroup/cgroup.controllers", {}, "cgroup v2"),
    ("", {"dbus": False}, "dbus-user-session"),
    ("sed -i 's|^SCANVIEW_IMPORT_BIN=.*|SCANVIEW_IMPORT_BIN=/tmp/bin|' "
     "/etc/scanview/scanview-joblaeufer.env", {},
     "SCANVIEW_IMPORT_BIN=/opt/scanview/import/.venv/bin"),
    ("rm $R/import/uv.lock", {}, "import/uv.lock"),
    ("sed -i 's|^SCANVIEW_BERICHTE=.*|SCANVIEW_BERICHTE=/tmp/b|' "
     "/etc/scanview/scanview-joblaeufer.env", {}, "SCANVIEW_BERICHTE=/srv/scanview/berichte"),
    ("echo SCANVIEW_JOBS_RAM_GB=1 >>/etc/scanview/scanview-joblaeufer.env", {},
     "SCANVIEW_JOBS_RAM_GB"),
], ids=["systemd-252", "ram-knapp", "cgroup-v1", "ohne-memory", "ohne-dbus", "import-bin-fremd",
        "import-code-fehlt", "fremder-pfad", "budget-zu-klein"])
@pytest.mark.docker
@probe_da
def test_joblaeufer_vorbedingung_fehlt_bricht_vor_jeder_aenderung_ab(
        tmp_path: Path, vorbereitung: str, kwargs: dict[str, Any], meldung: str) -> None:
    """Schalter an, eine Vorbedingung fehlt: laut abbrechen, bevor irgendetwas verändert wird —
    auch kein Update des Webdienstes, keine Sicherung, keine Protokollzeile."""
    lauf = _probe(tmp_path, JL_HOST + vorbereitung + "\n", **kwargs)
    assert lauf.rc != 0
    assert meldung in lauf.stderr and "Nichts verändert" in lauf.stderr, lauf.stderr
    assert not _jl_veraendert(lauf), lauf.aufrufe
    assert lauf.protokoll is None
    assert not any(p.startswith(("/srv/scanview/jobs", "/var/backups/scanview"))
                   for p in lauf.zustand)


@pytest.mark.parametrize(("vorbereitung", "meldung"), [
    ("echo 'cpu pids' >$UC/cgroup.controllers", "nicht an user@993.service delegiert"),
    ("touch /tmp/ram-grenze-wirkt-nicht", "RAM-Grenze von user@993.service nicht wirksam"),
    ("rm /run/user/993/bus", "/run/user/993/bus fehlt"),
], ids=["keine-delegation", "ram-grenze-wirkt-nicht", "kein-bus"])
@pytest.mark.docker
@probe_da
def test_joblaeufer_user_manager_ungeeignet_bricht_ab(tmp_path: Path, vorbereitung: str,
                                                      meldung: str) -> None:
    """Erst nach Linger prüfbar (User-Manager läuft): fehlt die memory-Delegation, wirkt die
    RAM-Grenze nicht oder fehlt der Bus, startet der Worker nicht und der Lauf endet protokolliert
    mit Fehler."""
    lauf = _probe(tmp_path, JL_HOST + vorbereitung + "\n")
    assert lauf.rc != 0
    assert meldung in lauf.stderr, lauf.stderr
    assert "systemctl enable scanview-import-worker.service" not in lauf.aufrufe
    assert lauf.protokoll is not None
    assert "ergebnis=abgebrochen" in lauf.protokoll.splitlines()[-1]


@pytest.mark.docker
@probe_da
def test_joblaeufer_jobs_db_mit_fremden_rechten_wird_nicht_angefasst(tmp_path: Path) -> None:
    """Eine vorhandene jobs.db mit falschem Eigentümer, Modus oder zweitem Namen (z. B. ein
    Hardlink auf scanview.db) ändert root nicht still — Abbruch mit Hinweis."""
    lauf = _probe(tmp_path, JL_HOST + "install -d -o 0 -g 994 -m 3770 /srv/scanview/jobs\n"
                                      "install -o 990 -g 990 -m 0600 /dev/null "
                                      "/srv/scanview/jobs/jobs.db\n")
    assert lauf.rc != 0
    assert "jobs.db: erwartet root:scanview-jobs 0660" in lauf.stderr, lauf.stderr
    assert lauf.zustand["/srv/scanview/jobs/jobs.db"] == ["f", 990, 990, "0o600"]
    assert "systemctl enable scanview-import-worker.service" not in lauf.aufrufe


@pytest.mark.docker
@probe_da
def test_joblaeufer_ohne_selbsttest_kein_erfolg(tmp_path: Path) -> None:
    """Meldet sich der Worker nicht als aktiv (Selbsttest gescheitert → Exit 3, kein Neustart),
    endet der Lauf mit Fehler statt „ok“."""
    lauf = _probe(tmp_path, JL_HOST + "rm /tmp/worker-aktiv\n")
    assert lauf.rc != 0
    assert "Jobläufer meldet sich nicht als aktiv" in lauf.stderr
    assert lauf.protokoll is not None
    assert "ergebnis=abgebrochen" in lauf.protokoll.splitlines()[-1]


@pytest.mark.docker
@probe_da
def test_joblaeufer_ausschalten_entfernt_dienst_und_behaelt_daten(tmp_path: Path) -> None:
    # sleep 1 im Zwischenschritt: die Update-Sicherung trägt Sekunden im Namen; zwei Läufe in
    # derselben Sekunde brächen mit „existiert schon“ ab (richtig, aber nicht Prüfgegenstand).
    lauf = _probe(tmp_path, "datenbank\n" + JL_HOST,
                  zwischen="sleep 1; sed -i 's/^SCANVIEW_JOBLAEUFER=1$/SCANVIEW_JOBLAEUFER=0/' "
                           "/etc/scanview/scanview-joblaeufer.env")
    assert lauf.erster is not None and lauf.erster["rc"] == 0, lauf.erster
    assert lauf.rc == 0, lauf.stderr
    assert "systemctl disable --now scanview-import-worker.service" in lauf.aufrufe
    assert "loginctl disable-linger scanview-import" in lauf.aufrufe
    for p in (JL_UNIT, "/etc/systemd/system/scanview-server.service.d/joblaeufer.conf",
              "/etc/systemd/system/scanview-backup.service.d/joblaeufer.conf",
              "/etc/systemd/system/scanview-import-worker.service.d/laufzeit.conf",
              "/etc/systemd/system/user@993.service.d/scanview-joblaeufer.conf"):
        assert p not in lauf.zustand, p
    assert lauf.zustand["/srv/scanview/jobs/jobs.db"] == ["f", 0, 994, "0o660"]
    assert lauf.zustand["/srv/scanview/berichte"] == ["d", 993, 994, "0o2750"]


@pytest.mark.docker
@probe_da
def test_joblaeufer_untergeschobener_symlink_statt_jobs_db(tmp_path: Path) -> None:
    """Liegt an Stelle von jobs.db ein Symlink (z. B. auf scanview.db), folgt root ihm nicht und
    der Lauf bricht ab; das Ziel bleibt unverändert."""
    lauf = _probe(tmp_path, "datenbank\n" + JL_HOST
                  + "install -d -o 0 -g 994 -m 3770 /srv/scanview/jobs\n"
                  "ln -s /srv/scanview/state/scanview.db /srv/scanview/jobs/jobs.db\n")
    assert lauf.rc != 0
    assert "jobs.db ist ein Symlink" in lauf.stderr, lauf.stderr
    assert lauf.zustand["/srv/scanview/state/scanview.db"][:3] == ["f", 990, 990]
    assert "systemctl enable scanview-import-worker.service" not in lauf.aufrufe


# --- Import-Umgebung und Import neuer Projekte (SCANVIEW_IMPORT) ----------------------------------

#: Schalter an mit Quote; Übergabe-Nutzer (995) nur in scanview-deploy, Gruppe scanview-upload (996)
#: mit dem Worker als Mitglied.
IMPORT_AN = ("printf 'SCANVIEW_IMPORT=1\\nSCANVIEW_UPLOAD_QUOTE_GB=50\\n' "
             ">>/etc/scanview/scanview-joblaeufer.env\n")
UEBERGABE_PASSWD = "scanview-uebergabe:x:995:995::/nonexistent:/usr/sbin/nologin\n"
IMPORT_GRUPPEN = {"scanview-deploy:x:992:\n": "scanview-deploy:x:992:scanview-uebergabe\n"}
IMPORT_GRUPPEN_NEU = "scanview-uebergabe:x:995:\nscanview-upload:x:996:scanview-import\n"
IMPORT_NACH = r"""
cat >/tmp/nach.sh <<'NACH'
cd /etc/systemd/system
sha256sum scanview-import-uebergabe.service scanview-import-uebergabe.timer \
  scanview-server.service.d/import.conf scanview-import-worker.service.d/import.conf \
  /usr/local/sbin/scanview-import-uebergabe
NACH
"""
IMPORT_DATEIEN = ("/etc/systemd/system/scanview-import-uebergabe.service",
                  "/etc/systemd/system/scanview-import-uebergabe.timer",
                  "/etc/systemd/system/scanview-server.service.d/import.conf",
                  "/etc/systemd/system/scanview-import-worker.service.d/import.conf",
                  "/usr/local/sbin/scanview-import-uebergabe")


@pytest.fixture
def import_konten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host mit Übergabe-Nutzer und Upload-Gruppe (wie nach einem früheren Lauf)."""
    gruppen = GROUP
    for alt, neu in IMPORT_GRUPPEN.items():
        gruppen = gruppen.replace(alt, neu)
    # _probe liest PASSWD und GROUP aus den Modulvariablen.
    monkeypatch.setitem(globals(), "PASSWD", PASSWD + UEBERGABE_PASSWD)
    monkeypatch.setitem(globals(), "GROUP", gruppen + IMPORT_GRUPPEN_NEU)


@pytest.mark.docker
@probe_da
def test_joblaeufer_baut_import_umgebung_ohne_root(tmp_path: Path) -> None:
    """Die Import-Umgebung baut uv als nobody (Quellpakete führen beim Bauen fremden Code aus),
    mit festem, uv-verwaltetem Python und festen Bauabhängigkeiten; danach gehört alles root."""
    lauf = _probe(tmp_path, JL_HOST)
    assert lauf.rc == 0, lauf.stderr
    [bau] = [a for a in lauf.aufrufe if a.startswith("uv-bau ")]
    assert bau.startswith("uv-bau uid=65534 pwd=/opt/scanview/import python=3.12.13 "
                          "vorzug=only-managed einschraenkungen=10 home=/var/tmp/"
                          "scanview-import-bau."), bau
    assert "uv sync --locked --no-dev --extra sonne" in lauf.aufrufe
    a = lauf.aufrufe
    # Kein Auftrag aus einer halb gebauten Umgebung: Worker vorher angehalten, danach neu.
    assert (a.index("systemctl stop scanview-import-worker.service")
            < a.index("uv sync --locked --no-dev --extra sonne")
            < a.index("systemctl restart scanview-import-worker.service"))
    z = lauf.zustand
    for p in ("/opt/scanview/import/.venv", "/opt/scanview/import/.venv/bin",
              "/opt/scanview/python"):
        assert z[p] == ["d", 0, 0, "0o755"], p
    assert z["/opt/scanview/import/.venv/bin/scanview-pruefen"] == ["f", 0, 0, "0o755"]
    assert z["/opt/scanview/import/.venv/bin/python"][0] == "l"
    assert all(v[1] == 0 for p, v in z.items() if p.startswith(("/opt/scanview/import/.venv",
                                                                "/opt/scanview/python")))
    # Bauordner (Cache, Kopie von uv) wieder weg.
    assert not [p for p in z if p.startswith("/var/tmp/")], z
    assert lauf.protokoll is not None and "ergebnis=ok" in lauf.protokoll.splitlines()[-1]


@pytest.mark.parametrize(("vorbereitung", "meldung"), [
    ("touch /tmp/uv-ohne-pruefer", "SCANVIEW_IMPORT_BIN (/opt/scanview/import/.venv/bin) muss"),
    ("touch /tmp/uv-symlink", "SCANVIEW_IMPORT_BIN (/opt/scanview/import/.venv/bin) muss"),
    ("touch /tmp/uv-bau-scheitert", "Import-Umgebung: uv sync gescheitert"),
], ids=["ohne-pruefer", "bin-symlink", "uv-scheitert"])
@pytest.mark.docker
@probe_da
def test_joblaeufer_import_umgebung_unbrauchbar_kein_worker(tmp_path: Path, vorbereitung: str,
                                                            meldung: str) -> None:
    lauf = _probe(tmp_path, JL_HOST + vorbereitung + "\n")
    assert lauf.rc != 0
    assert meldung in lauf.stderr, lauf.stderr
    assert "systemctl enable scanview-import-worker.service" not in lauf.aufrufe
    assert "systemctl restart scanview-import-worker.service" not in lauf.aufrufe
    assert lauf.protokoll is not None
    assert "ergebnis=abgebrochen" in lauf.protokoll.splitlines()[-1]
    assert not [p for p in lauf.zustand if p.startswith("/var/tmp/")]


@pytest.mark.docker
@probe_da
def test_import_an_richtet_uploads_und_uebergabe_ein(tmp_path: Path,
                                                     import_konten: None) -> None:
    lauf = _probe(tmp_path, JL_HOST + IMPORT_AN + IMPORT_NACH)
    assert lauf.rc == 0, lauf.stderr
    z = lauf.zustand
    # Rechte: Webdienst 990, Worker 993, Übergabe 995, Gruppe scanview-upload 996.
    assert z["/srv/scanview/uploads"] == ["d", 990, 996, "0o2750"]
    assert z["/srv/scanview/uebergabe"] == ["d", 993, 995, "0o2750"]
    assert z["/srv/scanview/uebergabe-meldungen"] == ["d", 995, 993, "0o2750"]
    assert z["/usr/local/sbin/scanview-import-uebergabe"] == ["f", 0, 0, "0o755"]
    for p in IMPORT_DATEIEN[:4]:
        assert z[p] == ["f", 0, 0, "0o644"], p
    for zeile in lauf.nach.splitlines():  # Vorlagen bytegleich installiert
        summe, name = zeile.split()
        quelle = (PLATFORM / "scanview-import-uebergabe" if name.startswith("/usr/local")
                  else PLATFORM / name)
        assert hashlib.sha256(quelle.read_bytes()).hexdigest() == summe, name
    a = lauf.aufrufe
    assert "usermod -a -G scanview-deploy scanview-uebergabe" in a
    assert not any(x.startswith("useradd") and "scanview-uebergabe" in x for x in a)
    # Der Worker war schon in scanview-upload: kein Neustart des User-Managers nötig.
    assert "systemctl restart user@993.service" not in a
    assert (a.index("systemctl restart scanview-import-worker.service")
            < a.index("systemctl enable --now scanview-import-uebergabe.timer"))
    assert "Import an: Quote 50 GB." in lauf.stdout
    assert lauf.protokoll is not None and "ergebnis=ok" in lauf.protokoll.splitlines()[-1]


@pytest.mark.docker
@probe_da
def test_import_an_neue_gruppe_startet_user_manager_neu(
        tmp_path: Path, import_konten: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ist der Worker noch nicht in scanview-upload, kommt er hinein — und sein User-Manager
    startet neu (sonst sähen die Aufträge die Gruppe nicht)."""
    monkeypatch.setitem(globals(), "GROUP", globals()["GROUP"].replace(
        "scanview-upload:x:996:scanview-import\n", "scanview-upload:x:996:\n"))
    lauf = _probe(tmp_path, JL_HOST + IMPORT_AN)
    assert lauf.rc == 0, lauf.stderr
    a = lauf.aufrufe
    assert "groupadd --system scanview-upload" not in a  # gibt es schon
    assert "usermod -a -G scanview-upload scanview-import" in a
    assert (a.index("systemctl start user@993.service")
            < a.index("systemctl restart user@993.service")
            < a.index("systemctl enable scanview-import-worker.service"))


@pytest.mark.parametrize(("zusatz", "meldung"), [
    ("printf 'SCANVIEW_IMPORT=1\\n'", "SCANVIEW_UPLOAD_QUOTE_GB fehlt"),
    ("printf 'SCANVIEW_IMPORT=1\\nSCANVIEW_UPLOAD_QUOTE_GB=0\\n'",
     "SCANVIEW_UPLOAD_QUOTE_GB fehlt"),
    ("printf 'SCANVIEW_IMPORT=1\\nSCANVIEW_UPLOAD_QUOTE_GB=viel\\n'",
     "SCANVIEW_UPLOAD_QUOTE_GB fehlt"),
    ("printf 'SCANVIEW_IMPORT=ja\\n'", "SCANVIEW_IMPORT muss 0 oder 1 sein"),
    ("printf 'SCANVIEW_IMPORT=1\\nSCANVIEW_UPLOAD_QUOTE_GB=5\\nSCANVIEW_UPLOADS=/tmp/u\\n'",
     "SCANVIEW_UPLOADS nicht in"),
], ids=["ohne-quote", "quote-null", "quote-text", "schalter-kaputt", "fremder-upload-pfad"])
@pytest.mark.docker
@probe_da
def test_import_falsch_konfiguriert_bricht_vor_jeder_aenderung_ab(
        tmp_path: Path, zusatz: str, meldung: str) -> None:
    lauf = _probe(tmp_path, JL_HOST + zusatz + " >>/etc/scanview/scanview-joblaeufer.env\n")
    assert lauf.rc != 0
    assert meldung in lauf.stderr and "Nichts verändert" in lauf.stderr, lauf.stderr
    assert not _jl_veraendert(lauf), lauf.aufrufe
    assert lauf.protokoll is None


@pytest.mark.parametrize("gruppe", ["scanview", "scanview-upload", "scanview-jobs"])
@pytest.mark.docker
@probe_da
def test_import_uebergabe_nutzer_in_fremder_gruppe_bricht_ab(
        tmp_path: Path, import_konten: None, gruppe: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Übergabe-Nutzer ist für das Freischalt-Skript der Hochladende: in scanview (Datenwurzel)
    oder einer Gruppe mit Uploads/Aufträgen → Abbruch vor jeder Änderung."""
    gruppen = globals()["GROUP"].splitlines(keepends=True)
    neu = []
    for z in gruppen:
        name, _x, gid, mitglieder = z.rstrip("\n").split(":")
        if name == gruppe:
            mitglieder = ",".join(m for m in (mitglieder, "scanview-uebergabe") if m)
        neu.append(f"{name}:{_x}:{gid}:{mitglieder}\n")
    monkeypatch.setitem(globals(), "GROUP", "".join(neu))
    lauf = _probe(tmp_path, JL_HOST + IMPORT_AN)
    assert lauf.rc != 0
    assert f"scanview-uebergabe ist Mitglied von {gruppe}" in lauf.stderr, lauf.stderr
    assert "Nichts verändert" in lauf.stderr
    assert not _jl_veraendert(lauf), lauf.aufrufe


@pytest.mark.docker
@probe_da
def test_import_ausschalten_entfernt_timer_und_behaelt_daten(tmp_path: Path,
                                                            import_konten: None) -> None:
    lauf = _probe(tmp_path, "datenbank\n" + JL_HOST + IMPORT_AN,
                  zwischen="sleep 1; sed -i 's/^SCANVIEW_IMPORT=1$/SCANVIEW_IMPORT=0/' "
                           "/etc/scanview/scanview-joblaeufer.env")
    assert lauf.erster is not None and lauf.erster["rc"] == 0, lauf.erster
    assert lauf.rc == 0, lauf.stderr
    a = lauf.aufrufe
    assert "systemctl disable --now scanview-import-uebergabe.timer" in a
    assert "gpasswd -d scanview-import scanview-upload" in a
    for p in IMPORT_DATEIEN:
        assert p not in lauf.zustand, p
    # Daten bleiben, der Jobläufer läuft weiter.
    assert lauf.zustand["/srv/scanview/uploads"] == ["d", 990, 996, "0o2750"]
    assert lauf.zustand["/srv/scanview/uebergabe"] == ["d", 993, 995, "0o2750"]
    assert lauf.zustand[JL_UNIT] == ["f", 0, 0, "0o644"]
    assert "Import ausgeschaltet" in lauf.stdout


@pytest.mark.docker
@probe_da
def test_joblaeufer_aus_nimmt_import_mit(tmp_path: Path, import_konten: None) -> None:
    lauf = _probe(tmp_path, "datenbank\n" + JL_HOST + IMPORT_AN,
                  zwischen="sleep 1; sed -i 's/^SCANVIEW_JOBLAEUFER=1$/SCANVIEW_JOBLAEUFER=0/' "
                           "/etc/scanview/scanview-joblaeufer.env")
    assert lauf.erster is not None and lauf.erster["rc"] == 0, lauf.erster
    assert lauf.rc == 0, lauf.stderr
    for p in (*IMPORT_DATEIEN, JL_UNIT):
        assert p not in lauf.zustand, p
    assert "systemctl disable --now scanview-import-uebergabe.timer" in lauf.aufrufe

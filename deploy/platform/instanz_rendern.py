#!/usr/bin/env python3
"""Instanzvorlagen rendern; der Standard übernimmt die Release-Vorlagen unverändert.

Der rootlose Renderlauf beschreibt Dateien, Konten und Rechte, ohne Dienste, Konten oder
ACLs des Hosts zu ändern. Die Einrichtung verwendet dieselben gerenderten Vorlagen.
"""

from __future__ import annotations

import argparse
import fcntl
import grp
import json
import os
import pwd
import re
import shlex
import socket
import stat
import subprocess
import tempfile
from pathlib import Path


class Instanz:
    """Einzige Ableitung von Hostpfaden, Konten, Unit-Namen und Port."""

    def __init__(self, name: str = "", port: int | str | None = None,
                 *, portpflicht: bool = True):
        if name and (
            re.fullmatch(r"[a-z][a-z0-9-]{1,19}", name) is None
            or name in ("scanview", "import", "jobs")
            or name.endswith("-deploy")
        ):
            # import/jobs: Nutzer scanview-import und Gruppe scanview-jobs des Jobläufers.
            raise ValueError(
                "Instanzname muss ^[a-z][a-z0-9-]{1,19}$ entsprechen; "
                "scanview, import, jobs und Namen mit -deploy sind gesperrt."
            )
        if name and (portpflicht or port is not None):
            if (
                isinstance(port, bool)
                or port is None
                or re.fullmatch(r"[1-9][0-9]{3,4}", str(port)) is None
                or not 1024 <= int(port) <= 65535
                or int(port) == 8803
            ):
                raise ValueError("Port ist Pflicht: 1024 bis 65535, außer 8803.")
        elif not name and port not in (None, 8803, "8803"):
            raise ValueError("Port der Standardinstanz bleibt 8803.")
        self.name = name
        self.port = int(port) if name and port is not None else (None if name else 8803)
        self.nutzer = "scanview" + ("-" + name if name else "")
        self.deploy = self.nutzer + "-deploy"
        self.server = self.nutzer + "-server"
        self.backup = self.nutzer + "-backup"
        self.pfade = {
            "code": "/opt/" + self.nutzer,
            "srv": "/srv/" + self.nutzer,
            "etc": "/etc/" + self.nutzer,
            "log": "/var/log/" + self.nutzer,
            "backups": "/var/backups/" + self.nutzer,
            "install": "/var/lib/scanview-install" + ("-" + name if name else ""),
            "freischalten": "/usr/local/sbin/" + self.nutzer + "-paket-freischalten",
            "sudoers": "/etc/sudoers.d/" + self.deploy,
            "caddy": "/etc/caddy/" + self.nutzer + ".caddy",
            "jail": "/etc/fail2ban/jail.d/" + self.nutzer + ".local",
            "filter": "/etc/fail2ban/filter.d/" + self.nutzer + ".conf",
        }

    def ersetzen(self, text: str) -> str:
        if not self.name:
            return text
        standard = Instanz()
        paare = {standard.pfade[k]: v for k, v in self.pfade.items()}
        paare.update(
            {
                "scanview-paket-freischalten": self.nutzer + "-paket-freischalten",
                "scanview-deploy": self.deploy,
                "scanview-server.service": self.server + ".service",
                "scanview-backup.service": self.backup + ".service",
                "scanview-backup.timer": self.backup + ".timer",
                "scanview.caddy": self.nutzer + ".caddy",
                "fail2ban/scanview.conf": "fail2ban/" + self.nutzer + ".conf",
                "fail2ban/scanview.local": "fail2ban/" + self.nutzer + ".local",
                "/var/tmp/scanview-release": "/var/tmp/" + self.nutzer + "-release",
            }
        )
        if self.port is not None:
            paare["8803"] = str(self.port)
        # Platzhalter vermeiden, dass ein bereits eingesetzter Pfad erneut ersetzt wird.
        for i, alt in enumerate(sorted(paare, key=len, reverse=True)):
            text = text.replace(alt, f"@@SV{i}@@")
        for i, alt in enumerate(sorted(paare, key=len, reverse=True)):
            text = text.replace(f"@@SV{i}@@", paare[alt])
        return re.sub(r"(?<![\w/.-])scanview(?![\w.-])", self.nutzer, text)


def port_pruefen(instanz: Instanz, wurzel: Path, *, sockets: bool = True) -> None:
    if not instanz.name:
        return
    eigener_port = False
    for unit in (wurzel / "etc/systemd/system").glob("scanview*-server.service"):
        text = unit.read_text()
        ports = re.findall(r"^ExecStart=.*?--port(?:\s+|=)([0-9]+)(?:\s|$)",
                           text, re.MULTILINE)
        if unit.name == instanz.server + ".service":
            eigener_port = str(instanz.port) in ports
        elif str(instanz.port) in ports:
            raise ValueError(f"Port {instanz.port} bereits belegt: {unit}.")
    if sockets and eigener_port:
        eigener_port = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", instanz.server],
            check=False,
        ).returncode == 0
    if sockets and not eigener_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as pruefung:
            # bind prüft auch nicht lauschende, aber bereits gebundene Sockets.
            try:
                pruefung.bind(("127.0.0.1", instanz.port))
            except OSError as e:
                raise ValueError(f"Port {instanz.port} bereits belegt: {e}.") from e


def pfade_pruefen(instanz: Instanz, wurzel: Path) -> None:
    """Vor jedem Schreiben: keine fremden Bäume, Symlinks an Wurzeln oder Zieldateien."""
    plan = beschreibung(instanz)
    baeume = [wurzel / instanz.pfade[k].lstrip("/")
              for k in ("code", "srv", "etc", "log", "backups", "install")]
    fremd = []
    for baum in baeume:
        fremd.extend(p for p in baum.parent.glob("scanview*") if p != baum)
    ziele = [wurzel / d[1].lstrip("/") for d in plan["dateien"]]
    ziele += [wurzel / d[0].lstrip("/") for d in plan["zustandsdateien"]]
    ziele += [wurzel / (instanz.pfade["etc"] + "/allowed_signers").lstrip("/"),
              wurzel / (instanz.pfade["sudoers"].rsplit("/", 1)[0] +
                        "/." + instanz.deploy + ".neu").lstrip("/")]

    def pruefen(p: Path, *, link_verboten: bool = False) -> None:
        try:
            if link_verboten and p.is_symlink():
                raise ValueError(f"Instanzpfad {p} ist ein Symlink.")
            aufgeloest = p.resolve()
            for anderer in fremd:
                # Lexikalisch UND aufgelöst: auch Aliasnamen anderer Instanzen zählen.
                if (aufgeloest == anderer or anderer in aufgeloest.parents
                        or aufgeloest == anderer.resolve()
                        or anderer.resolve() in aufgeloest.parents):
                    raise ValueError(f"Instanzpfad {p} zeigt in andere Instanz {anderer}.")
        except (OSError, RuntimeError) as e:
            raise ValueError(f"Instanzpfad {p} nicht sicher auflösbar: {e}.") from e

    for p in [*baeume, *ziele]:
        pruefen(p, link_verboten=True)
        for parent in p.parents:
            if parent == wurzel:
                break
            pruefen(parent)
    for baum in baeume:
        if baum.exists() and not baum.is_dir():
            raise ValueError(f"Instanzpfad {baum} ist kein Verzeichnis.")
        if baum.is_dir():
            for parent, ordner, dateien in os.walk(baum, followlinks=False):
                for name in [*ordner, *dateien]:
                    p = Path(parent) / name
                    if p.is_symlink():
                        pruefen(p)


def konten_pruefen(instanz: Instanz) -> None:
    """Deploy- und Dienstkonto dürfen keine Rechte anderer scanview-Instanzen erben."""
    gruppen = grp.getgrall()
    konten = {p.pw_name: p for p in pwd.getpwall()}
    deploy = next((g for g in gruppen if g.gr_name == instanz.deploy), None)
    mitglieder = set(deploy.gr_mem if deploy else [])
    if deploy:
        mitglieder.update(p.pw_name for p in konten.values() if p.pw_gid == deploy.gr_gid)
    for nutzer in [*mitglieder, instanz.nutzer]:
        konto = konten.get(nutzer)
        if konto is None:
            continue
        erlaubt = instanz.nutzer if nutzer == instanz.nutzer else instanz.deploy
        for gruppe in gruppen:
            if (gruppe.gr_name == "scanview" or gruppe.gr_name.startswith("scanview-")):
                if (gruppe.gr_name != erlaubt and
                        (nutzer in gruppe.gr_mem or konto.pw_gid == gruppe.gr_gid)):
                    raise ValueError(f"Rechtegrenze: {nutzer} ist auch in {gruppe.gr_name}.")


def installer_rendern(quelle: Path, instanz: Instanz) -> str:
    """Die bestehende Einrichtung bleibt Standardvorlage, ohne zweite Kopie im Repo."""
    text = (quelle / "einrichten.sh").read_text()
    if not instanz.name:
        return text
    text = re.sub(r"# INSTANZ_DISPATCH_BEGIN\n.*?# INSTANZ_DISPATCH_END\n", "", text,
                  flags=re.DOTALL)
    # Der Jobläufer (Nutzer, Gruppe, Units, Drop-ins) gehört nur zur Standardinstanz.
    text = re.sub(r"[ \t]*# JOBLAEUFER_BEGIN\n.*?# JOBLAEUFER_END\n", "", text, flags=re.DOTALL)
    text = text.replace("scanview-git-export.service scanview-git-export.timer ", "")
    text = "\n".join(line for line in text.split("\n")
                     if not (line.startswith("install ") and "scanview-git-export." in line))
    anfang = text.index('if grep -qE "^[[:space:]]*SCANVIEW_GIT_EXPORT=')
    ende = text.index('\necho "== 8.', anfang)
    text = text[:anfang] + '# Benannte Instanzen erhalten keine Git-Export-Units.\n' + text[ende:]
    text = instanz.ersetzen(text)
    # Die aufgerufene, vertrauenswürdige Helferkopie prüft das Release, nie eine Release-Kopie.
    text = re.sub(r"^SKRIPT_ORDNER=.*$", "SKRIPT_ORDNER=" + shlex.quote(str(quelle)),
                  text, flags=re.MULTILINE)
    helfer = shlex.quote(str(quelle / "instanz_rendern.py"))
    rendern = ('\nVORLAGEN=$(mktemp -d /var/tmp/' + instanz.nutzer + '-vorlagen.XXXXXXXX)\n'
               '/usr/bin/python3 -I ' + helfer + ' --instanz ' + instanz.name +
               ' --port ' + str(instanz.port) +
               ' --vorlagen-quelle "$HIER" --vorlagen-ziel "$VORLAGEN"\nHIER=$VORLAGEN\n')
    text = text.replace('HIER="$REPO/deploy/platform"\n', 'HIER="$REPO/deploy/platform"\n' +
                        rendern, 1)
    text = text.replace('STUFE=\n', 'STUFE=\nVORLAGEN=\n', 1)
    text = text.replace('if [[ -n $ARBEIT ]]; then',
                        'if [[ -n $VORLAGEN ]]; then rm -rf -- "$VORLAGEN"; fi\n\t'
                        'if [[ -n $ARBEIT ]]; then', 1)
    return text


def vorlage_rendern(daten: bytes, vorlage: str, instanz: Instanz) -> bytes:
    if not instanz.name:
        return daten
    text = instanz.ersetzen(daten.decode())
    if vorlage == "fail2ban/scanview.conf":
        # Die Logmeldung kommt aus gemeinsamem Servercode; nur das Jail trennt die Journale.
        text = text.replace(instanz.nutzer + " Anmeldung fehlgeschlagen",
                            "scanview Anmeldung fehlgeschlagen")
    if vorlage == "scanview.caddy":
        # Caddy-Umgebungsvariablen sind hostweit. Benannte Snippets haben feste eigene Ziele.
        feste = {"UPSTREAM": "127.0.0.1:" + str(instanz.port),
                 "DATA": instanz.pfade["srv"] + "/data",
                 "WEB": instanz.pfade["code"] + "/web",
                 "MELDEWEG": instanz.pfade["etc"] + "/meldeweg"}
        for name, wert in feste.items():
            text = re.sub(r"\{\$SCANVIEW_" + name + r"(?::[^}]+)?\}", wert, text)
    text = text.replace("systemctl reload scanview-server",
                        "systemctl reload " + instanz.server)
    return text.encode()


def datei_atomar(datei: Path, daten: bytes, modus: int, *,
                 uid: int | None = None, gid: int | None = None) -> None:
    """Neue Inode im Zielverzeichnis: vorhandene 0440-Dateien bleiben bis replace lesbar."""
    fd, name = tempfile.mkstemp(prefix="." + datei.name + ".", dir=datei.parent)
    try:
        with os.fdopen(fd, "wb") as neu:
            neu.write(daten)
            neu.flush()
            os.fchown(neu.fileno(), os.geteuid() if uid is None else uid,
                      os.getegid() if gid is None else gid)
            os.fchmod(neu.fileno(), modus)
            os.fsync(neu.fileno())
        os.replace(name, datei)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def vorlagen_rendern(quelle: Path, ziel: Path, instanz: Instanz) -> None:
    for vorlage, *_ in beschreibung(instanz)["dateien"]:
        datei = ziel / instanz.ersetzen(vorlage)
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei_atomar(datei, vorlage_rendern((quelle / vorlage).read_bytes(), vorlage, instanz),
                     0o600)


def einrichten(quelle: Path, instanz: Instanz, release: Path | None, repo: Path | None) -> int:
    if os.geteuid() != 0:
        raise ValueError("muss als root laufen (sudo).")
    pfade_pruefen(instanz, Path("/"))
    port_pruefen(instanz, Path("/"))
    konten_pruefen(instanz)
    # Gemeinsames Schloss nur für benannte Einrichtungen, gegen parallele Portvergabe.
    fd = os.open("/run/lock/scanview-instanzen.lock",
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as schloss:
        s = os.fstat(schloss.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1:
            raise ValueError("Unsicheres Instanzschloss.")
        fcntl.flock(schloss, fcntl.LOCK_EX)
        pfade_pruefen(instanz, Path("/"))
        port_pruefen(instanz, Path("/"))
        konten_pruefen(instanz)
        with tempfile.TemporaryDirectory(prefix=instanz.nutzer + "-einrichten-",
                                         dir="/var/tmp") as tmp:
            skript = Path(tmp) / "einrichten.sh"
            skript.write_text(installer_rendern(quelle, instanz))
            args = ["--release", str(release)] if release else [str(repo)]
            return subprocess.run(["/bin/bash", str(skript), *args], check=False).returncode


def bereitstellen(quelle: Path, instanz: Instanz, paket: Path, host: str) -> int:
    text = (quelle / "paket-bereitstellen.sh").read_text()
    text = re.sub(r"# INSTANZ_DISPATCH_BEGIN\n.*?# INSTANZ_DISPATCH_END\n", "", text,
                  flags=re.DOTALL)
    text = instanz.ersetzen(text)
    with tempfile.TemporaryDirectory(prefix=instanz.nutzer + "-upload-") as tmp:
        skript = Path(tmp) / "paket-bereitstellen.sh"
        skript.write_text(text)
        return subprocess.run(["/bin/bash", str(skript), str(paket), host],
                              check=False).returncode


def _standard_beschreibung() -> dict:
    return {
        "nutzer": [
            {
                "name": "scanview",
                "system": True,
                "gruppe": "scanview",
                "home": "/nonexistent",
                "shell": "/usr/sbin/nologin",
            }
        ],
        "gruppen": ["scanview-deploy"],
        "verzeichnisse": [
            ["/var/log/scanview", "root", "root", "0750"],
            ["/var/backups/scanview", "root", "root", "0711"],
            ["/var/backups/scanview/update-sicherungen", "root", "root", "0700"],
            ["/srv/scanview/data", "root", "scanview", "0750"],
            ["/srv/scanview/state", "scanview", "scanview", "0700"],
            ["/srv/scanview/eingang", "root", "scanview-deploy", "1770"],
            ["/opt/scanview", "root", "root", "0755"],
            ["/opt/scanview/web", "root", "root", "0755"],
            ["/opt/scanview/server", "root", "root", "0755"],
            ["/etc/scanview", "root", "root", "0755"],
            ["/etc/scanview/meldeweg", "root", "root", "0755"],
            ["/var/lib/scanview-install", "root", "root", "0700"],
        ],
        "dateien": [
            [
                "scanview-server.service",
                "/etc/systemd/system/scanview-server.service",
                "root",
                "root",
                "0644",
            ],
            [
                "scanview-backup.service",
                "/etc/systemd/system/scanview-backup.service",
                "root",
                "root",
                "0644",
            ],
            [
                "scanview-backup.timer",
                "/etc/systemd/system/scanview-backup.timer",
                "root",
                "root",
                "0644",
            ],
            [
                "scanview-git-export.service",
                "/etc/systemd/system/scanview-git-export.service",
                "root",
                "root",
                "0644",
            ],
            [
                "scanview-git-export.timer",
                "/etc/systemd/system/scanview-git-export.timer",
                "root",
                "root",
                "0644",
            ],
            [
                "scanview-server.env.beispiel",
                "/etc/scanview/scanview-server.env",
                "root",
                "scanview",
                "0640",
            ],
            [
                "scanview-paket-freischalten",
                "/usr/local/sbin/scanview-paket-freischalten",
                "root",
                "root",
                "0755",
            ],
            ["sudoers.d/scanview-deploy", "/etc/sudoers.d/scanview-deploy", "root", "root", "0440"],
            ["scanview.caddy", "/etc/caddy/scanview.caddy", "root", "root", "0644"],
            [
                "fail2ban/scanview.conf",
                "/etc/fail2ban/filter.d/scanview.conf",
                "root",
                "root",
                "0644",
            ],
            [
                "fail2ban/scanview.local",
                "/etc/fail2ban/jail.d/scanview.local",
                "root",
                "root",
                "0644",
            ],
        ],
        "betreiber_installiert": [
            "scanview.caddy",
            "fail2ban/scanview.conf",
            "fail2ban/scanview.local",
        ],
        "zustandsdateien": [
            ["/etc/scanview/.ersteinrichtung-laeuft", "root", "root", "0600"],
            ["/var/lib/scanview-install/installiert", "root", "root", "0600"],
            ["/var/log/scanview/updates.log", "root", "root", "0640"],
        ],
        "acl": [
            ["/srv/scanview/data", "u:caddy:rX", "d:u:caddy:rX"],
            ["/opt/scanview/web", "u:caddy:rX", "d:u:caddy:rX"],
        ],
        "port": 8803,
    }


def beschreibung(instanz: Instanz | None = None) -> dict:
    plan = _standard_beschreibung()
    instanz = instanz or Instanz()
    if not instanz.name:
        return plan

    def ableiten(wert):
        if isinstance(wert, str):
            return instanz.ersetzen(wert)
        if isinstance(wert, list):
            return [ableiten(w) for w in wert]
        if isinstance(wert, dict):
            return {k: ableiten(v) for k, v in wert.items()}
        return wert

    dateien = plan["dateien"]
    plan = ableiten(plan)
    # Quellen bleiben die Standardvorlagen. Git-Export hat keine benannte Unit.
    plan["dateien"] = [
        [quelle, *ableiten(rest)]
        for quelle, *rest in dateien
        if not quelle.startswith("scanview-git-export.")
    ]
    plan["port"] = instanz.port
    return plan


def rendern(quelle: Path, ziel: Path, instanz: Instanz | None = None) -> dict:
    pfade_pruefen(instanz or Instanz(), ziel)
    plan = beschreibung(instanz)
    for pfad, _, _, modus in plan["verzeichnisse"]:
        verzeichnis = ziel / pfad.lstrip("/")
        verzeichnis.mkdir(parents=True, exist_ok=True)
        verzeichnis.chmod(int(modus, 8))
    for vorlage, pfad, nutzer, gruppe, modus in plan["dateien"]:
        datei = ziel / pfad.lstrip("/")
        datei.parent.mkdir(parents=True, exist_ok=True)
        if vorlage == "scanview-server.env.beispiel" and datei.exists():
            continue
        # Kopiert nur Bytes. Im rootlosen Lauf sind uid/gid Absichtserklärungen im Plan.
        uid = pwd.getpwnam(nutzer).pw_uid if os.geteuid() == 0 else os.geteuid()
        gid = grp.getgrnam(gruppe).gr_gid if os.geteuid() == 0 else os.getegid()
        datei_atomar(datei, vorlage_rendern((quelle / vorlage).read_bytes(), vorlage,
                                          instanz or Instanz()), int(modus, 8), uid=uid, gid=gid)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-root", type=Path)
    parser.add_argument("--instanz")
    parser.add_argument("--port")
    parser.add_argument("--release", type=Path)
    parser.add_argument("repo", nargs="?", type=Path)
    parser.add_argument("ssh_host", nargs="?")
    parser.add_argument("--bereitstellen", action="store_true")
    parser.add_argument("--vorlagen-quelle", type=Path)
    parser.add_argument("--vorlagen-ziel", type=Path)
    args = parser.parse_args()
    try:
        if args.instanz == "":
            raise ValueError("Instanzname darf nicht leer sein.")
        instanz = Instanz(args.instanz or "", args.port, portpflicht=not args.bereitstellen)
        quelle = Path(__file__).resolve().parent
        if args.bereitstellen:
            if not (instanz.name and args.repo and args.ssh_host) or (
                    args.port or args.render_root or args.release or args.vorlagen_quelle
                    or args.vorlagen_ziel):
                parser.error("Upload: --instanz <name> <quellordner> <ssh-host> angeben.")
            raise SystemExit(bereitstellen(quelle, instanz, args.repo, args.ssh_host))
        if args.ssh_host:
            parser.error("Überzähliges Argument.")
        if args.vorlagen_quelle and args.vorlagen_ziel and instanz.name:
            vorlagen_rendern(args.vorlagen_quelle, args.vorlagen_ziel, instanz)
        elif args.render_root and not (args.release or args.repo):
            port_pruefen(instanz, args.render_root, sockets=False)
            print(json.dumps(rendern(quelle, args.render_root, instanz),
                             ensure_ascii=False, sort_keys=True))
        elif instanz.name and bool(args.release) != bool(args.repo) and not args.render_root:
            raise SystemExit(einrichten(quelle, instanz, args.release, args.repo))
        else:
            parser.error("--render-root oder --instanz mit genau einem Release/Repo angeben.")
    except (ValueError, OSError) as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()

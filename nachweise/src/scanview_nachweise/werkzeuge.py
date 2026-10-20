"""Gepinnte Fremdwerkzeuge und ihr gehärteter Container-Lauf.

`nachweise/werkzeuge.json` nennt je Werkzeug das Image **mit Digest** (`…@sha256:<64 hex>`) und
die Werkzeugversion; ein Tag allein ist verboten, weil er sich unbemerkt verschieben kann. Wird
ein Werkzeug nicht als eigenes Image geliefert (z. B. `cyclonedx-npm`), trägt der Eintrag das
gepinnte Basis-Image und einen `werkzeugordner` (relativ zu `nachweise/`) mit Lockfile, aus dem
das Werkzeug per `npm ci --ignore-scripts` installiert wird.

Jeder Lauf geht über `docker_befehl`: ohne Netz, schreibgeschütztes Root-Dateisystem, keine
Capabilities, keine Rechteausweitung, eigener Nutzer (nie root), Eingaben nur lesend gemountet,
kein Nachladen von Images (`--pull never`: fehlt das gepinnte Image, bricht der Lauf ab).
Netz gibt es nur auf ausdrückliche Anforderung (`netz=True`) und nur für
(a) `db-aktualisieren` (trivy-DB) und (b) den Abhängigkeits-Download der Bau-Stufe gegen
Lockfiles mit Hash-Prüfung (`npm ci`, `uv sync --locked`, `uv pip install --require-hashes`),
jeweils mit Befehl und Zeit im Manifest protokolliert. Host-Netz (`netz="host"`) gibt es nur
für die Werkzeuge in `HOST_NETZ` (Fuzzing und Egress-Test der lokalen Wegwerf-Instanz auf
127.0.0.1). SBOM-, Scan- und Prüfwerkzeuge laufen immer ohne Netz. Die übrige Härtung bleibt in
jeder Netz-Variante und unter Docker wie Podman (`SCANVIEW_CONTAINER`) bestehen; zusätzlich
beschreibbare Pfade gibt es nur als tmpfs mit noexec/nosuid/nodev."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from .fehler import NachweisFehler

# Optionaler Tag vor dem Digest ist erlaubt (docker ignoriert ihn dann), maßgeblich ist der Digest.
_DIGEST = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
_UMGEBUNGSNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FELDER_PFLICHT = ("image", "version")
_FELDER_OPTIONAL = ("werkzeugordner", "tag")
# Docker-Tag: nur Tag-Zeichen; „@“ oder „:“ gehörten zu Digest bzw. Registry-Port.
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
# Auszug aus stderr in Fehlermeldungen: das Ende ist am aussagekräftigsten (letzte Meldung des
# Werkzeugs), der Rest würde die Ausgabe unlesbar machen.
_STDERR_AUSZUG = 2000
#: Einzige Werkzeuge, die mit Host-Netz laufen dürfen (`netz="host"`): Fuzzing, Caddy und der
#: Headless-Browser des Egress-Tests, alle nur gegen die lokale Wegwerf-Instanz auf 127.0.0.1
#: (Instanz nur mit synthetischen Paketen).
HOST_NETZ: frozenset[str] = frozenset({"schemathesis", "caddy-test", "playwright"})
#: Ports der lokalen Wegwerf-Instanz (nur 127.0.0.1): Caddy davor (Origin der Instanz, fest im
#: Caddyfile.lokal) und scanview-server dahinter. Eine Stelle für alle Prüfungen gegen die
#: Instanz (Fuzzing, Egress-Test). 18095 ist auf der Workstation von einem anderen Dienst belegt.
INSTANZ_CADDY_PORT = 18090
INSTANZ_SERVER_PORT = 18097
#: Einzige Ausnahmen von `--cap-drop ALL`, fest je Werkzeug (nicht vom Aufrufer wählbar): das
#: Caddy-Binary trägt die File-Capability `cap_net_bind_service=ep`; fehlt sie in der
#: Bounding-Menge, verweigert der Kernel schon exec („operation not permitted“). Die Capability
#: wirkt dann tatsächlich (Probe: Bindung an :80 als Nicht-root); benutzt wird nur Port 18090.
CAP_AUSNAHMEN: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"caddy-test": ("NET_BIND_SERVICE",)})
#: Container-Laufzeit aus `SCANVIEW_CONTAINER` (fehlt → docker); jeder andere Wert ist ein Fehler.
LAUFZEITEN = ("docker", "podman")
#: Standardoptionen jedes tmpfs (wie das feste /tmp); eigene Optionen müssen diese drei Sperren
#: ebenfalls tragen und dürfen sie nicht wieder aufheben.
TMPFS_STANDARD = "rw,noexec,nosuid,nodev,mode=1777"
_TMPFS_PFLICHT = frozenset({"noexec", "nosuid", "nodev"})
_TMPFS_AUFHEBEND = frozenset({"exec", "suid", "dev"})
_TMPFS_OPTIONEN = re.compile(r"[a-z0-9=]+(,[a-z0-9=]+)*")
_TMPFS_PFAD = re.compile(r"(/[A-Za-z0-9._-]+)+")
#: Containername (`--name`): klein, ohne Pfad-/Optionszeichen, höchstens 63 Zeichen.
_CONTAINERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")


@dataclass(frozen=True)
class Werkzeug:
    """Ein gepinntes Fremdwerkzeug aus `werkzeuge.json`."""

    name: str
    image: str
    version: str
    werkzeugordner: str | None
    #: Docker-Tag, aus dem der Digest stammt (nur wo er die Werkzeugversion trägt). Maßgeblich
    #: bleibt der Digest; der Tag dient Renovate als Suchanker für Versions-Updates.
    tag: str | None = None


def _hat_digest(image: str) -> bool:
    return _DIGEST.fullmatch(image) is not None


def _eintrag(name: str, roh: object) -> Werkzeug:
    if not isinstance(roh, dict):
        raise NachweisFehler("werkzeuge", f"{name}: Eintrag muss ein Objekt sein")
    unbekannt = sorted(set(roh) - set(_FELDER_PFLICHT) - set(_FELDER_OPTIONAL))
    if unbekannt:
        raise NachweisFehler("werkzeuge", f"{name}: unbekannte Felder {', '.join(unbekannt)}")
    for feld in _FELDER_PFLICHT:
        wert = roh.get(feld)
        if not isinstance(wert, str) or not wert.strip():
            raise NachweisFehler("werkzeuge", f"{name}: Feld „{feld}“ fehlt oder ist leer")
    image: str = roh["image"]
    if not _hat_digest(image):
        raise NachweisFehler("werkzeuge", f"{name}: Image „{image}“ ohne gültigen "
                                          "@sha256:-Digest (Tag allein ist nicht erlaubt)")
    ordner = roh.get("werkzeugordner")
    if ordner is not None:
        teile = PurePosixPath(ordner).parts if isinstance(ordner, str) else ()
        if not isinstance(ordner, str) or not ordner.strip() or ordner.startswith("/") \
                or ".." in teile:
            raise NachweisFehler("werkzeuge", f"{name}: „werkzeugordner“ muss ein relativer "
                                              "Pfad innerhalb von nachweise/ sein")
    tag = roh.get("tag")
    if tag is not None and (not isinstance(tag, str) or _TAG.fullmatch(tag) is None):
        raise NachweisFehler("werkzeuge", f"{name}: „tag“ muss ein gültiger Docker-Tag sein "
                                          "(nicht leer, ohne „@“ und „:“)")
    return Werkzeug(name=name, image=image, version=roh["version"], werkzeugordner=ordner,
                    tag=tag)


def lade_werkzeuge(pfad: Path) -> dict[str, Werkzeug]:
    """`werkzeuge.json` laden; jeder Eintrag braucht `image` mit Digest und `version`."""
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler("werkzeuge", f"{pfad} nicht lesbar: {e.strerror}") from None
    try:
        roh = json.loads(text)
    except json.JSONDecodeError as e:
        raise NachweisFehler("werkzeuge", f"{pfad} ist kein gültiges JSON: {e}") from None
    if not isinstance(roh, dict):
        raise NachweisFehler("werkzeuge", f"{pfad}: oberste Ebene muss ein Objekt sein")
    if not roh:
        raise NachweisFehler("werkzeuge", f"{pfad}: keine Werkzeuge eingetragen (leer)")
    return {name: _eintrag(name, eintrag) for name, eintrag in roh.items()}


def _mount(host: Path, ziel: str, recht: str) -> str:
    # Ein fehlender Host-Pfad würde vom docker-Daemon still als leeres, root-eigenes Verzeichnis
    # angelegt — ein Scan darüber endet mit Exit 0 und leerem Ergebnis (nicht fail-closed).
    # `-v host:ziel:recht` wird von docker am Doppelpunkt zerlegt; ein Doppelpunkt im Pfad würde
    # die Rechteangabe verschieben (z. B. `:rw` einschmuggeln) — deshalb hart abgelehnt.
    if not host.is_absolute() or not ziel.startswith("/"):
        raise NachweisFehler("werkzeug-mount", f"Mount {host} → {ziel}: beide Pfade müssen "
                                               "absolut sein")
    if ":" in str(host) or ":" in ziel:
        raise NachweisFehler("werkzeug-mount", f"Mount {host} → {ziel}: Doppelpunkt im Pfad "
                                               "nicht erlaubt")
    if not host.exists():
        raise NachweisFehler("werkzeug-mount", f"Mount {host} → {ziel}: Host-Pfad existiert "
                                               "nicht")
    if recht == "rw" and not host.is_dir():
        raise NachweisFehler("werkzeug-mount", f"Mount {host} → {ziel}: Schreibziel muss ein "
                                               "vorhandenes Verzeichnis sein")
    return f"{host}:{ziel}:{recht}"


def _laufzeit() -> str:
    """`SCANVIEW_CONTAINER` (nur `docker` oder `podman`, exakt); fehlt sie, gilt docker. Ein
    gesetzter, aber leerer oder unbekannter Wert bricht ab, statt still auf docker zu fallen."""
    wert = os.environ.get("SCANVIEW_CONTAINER")
    if wert is None:
        return "docker"
    if wert not in LAUFZEITEN:
        raise NachweisFehler("werkzeug-laufzeit", f"SCANVIEW_CONTAINER=„{wert}“ unbekannt "
                                                  f"(erlaubt: {', '.join(LAUFZEITEN)})")
    return wert


def voll_qualifiziert(image: str) -> str:
    """Image-Name mit Registry, damit Podman keine Kurznamen-Auflösung (registries.conf,
    Rückfrage oder Suche über mehrere Registries) anwendet: ohne Registry-Host gilt docker.io,
    ein einteiliger Name liegt dort unter `library/`. Registry-Host ist der erste Teil vor einem
    `/`, wenn er `.` oder `:` enthält oder `localhost` ist (wie docker selbst entscheidet)."""
    erster, schraeg, rest = image.partition("/")
    if schraeg and ("." in erster or ":" in erster or erster == "localhost"):
        return image
    if not schraeg:
        return f"docker.io/library/{image}"
    return f"docker.io/{erster}/{rest}"


def _tmpfs(pfad: str, optionen: str) -> str:
    """`--tmpfs`-Wert `pfad:optionen`. Nur absolute, einfache Pfade (keine leeren, `.`- oder
    `..`-Segmente, kein Doppelpunkt) außerhalb von `/tmp` (das feste, gehärtete HOME); Optionen
    aus einem kleinen Zeichenvorrat, mit noexec/nosuid/nodev und ohne deren Aufhebung.
    Leer → `TMPFS_STANDARD`."""
    optionen = optionen or TMPFS_STANDARD
    # Roh am „/“ zerlegen: PurePosixPath normalisiert „.“ und doppelte „/“ still weg.
    teile = pfad.split("/")[1:]
    if _TMPFS_PFAD.fullmatch(pfad) is None or any(t in ("", ".", "..") for t in teile):
        raise NachweisFehler("werkzeug-mount", f"tmpfs „{pfad}“: nur absolute Pfade ohne "
                                               "Doppelpunkt, leere, „.“- oder „..“-Segmente")
    if teile[0] == "tmp":
        raise NachweisFehler("werkzeug-mount", f"tmpfs „{pfad}“: /tmp ist bereits das gehärtete "
                                               "HOME und darf nicht überdeckt werden")
    gesetzt = set(optionen.split(",")) if _TMPFS_OPTIONEN.fullmatch(optionen) else set()
    if not gesetzt or not _TMPFS_PFLICHT <= gesetzt or gesetzt & _TMPFS_AUFHEBEND:
        raise NachweisFehler("werkzeug-mount", f"tmpfs „{pfad}“: Optionen „{optionen}“ müssen "
                                               "noexec, nosuid und nodev enthalten und "
                                               "dürfen sie nicht aufheben")
    return f"{pfad}:{optionen}"


def docker_befehl(
    w: Werkzeug,
    args: list[str],
    lesend: dict[Path, str],
    schreibend: dict[Path, str] | None = None,
    netz: bool | Literal["host"] = False,
    *,
    umgebung: dict[str, str] | None = None,
    uid: int | None = None,
    gid: int | None = None,
    tmpfs: Mapping[str, str] | None = None,
    name: str | None = None,
    abgesetzt: bool = False,
) -> list[str]:
    """Argumentliste für `docker run` bzw. `podman run` (`SCANVIEW_CONTAINER`) mit voller
    Härtung. `lesend`/`schreibend` bilden Host-Pfade auf Containerpfade ab (`:ro` bzw. `:rw`).
    `uid`/`gid` sind für Tests injizierbar, Standard ist der aufrufende Nutzer. `umgebung` setzt
    zusätzliche Umgebungsvariablen im Container (z. B. `npm_execpath` für cyclonedx-npm,
    `SYFT_FILE_METADATA_DIGESTS` für SHA-512 bei syft). `netz`: `False` → `--network none`,
    `True` → Standardnetz der Laufzeit (nur protokollierte Netzschritte), `"host"` →
    `--network host`, nur für Werkzeuge aus `HOST_NETZ`. `tmpfs` bildet zusätzliche beschreibbare
    Containerpfade auf tmpfs-Optionen ab (leer → `TMPFS_STANDARD`), `--read-only` bleibt immer.
    Bei Podman kommen `--userns=keep-id` (Dateien im Schreibziel gehören dem Aufrufer),
    `--read-only-tmpfs=false` (sonst legt Podman bei `--read-only` still beschreibbare tmpfs unter
    /run, /var/tmp, /dev/shm an) und der voll qualifizierte Image-Name hinzu. `name` setzt einen
    geprüften Containernamen (zum gezielten Stoppen), `abgesetzt` startet im Hintergrund (`-d`,
    mit `--rm` entfernt sich der Container beim Stoppen selbst). Die Liste wird ohne Shell
    ausgeführt, Pfade mit Leerzeichen oder Komma bleiben daher je ein Element."""
    if not _hat_digest(w.image):
        raise NachweisFehler("werkzeuge", f"{w.name}: Image „{w.image}“ ohne Digest — Lauf "
                                          "verweigert")
    # "host" ist truthy und 1 == True: erlaubt sind nur echte bool-Werte und genau "host".
    if not isinstance(netz, bool) and netz != "host":
        raise NachweisFehler("werkzeug-netz", f"{w.name}: Netzangabe „{netz!r}“ unbekannt")
    if netz == "host" and w.name not in HOST_NETZ:
        raise NachweisFehler("werkzeug-netz", f"{w.name}: Host-Netz nur für "
                                              f"{', '.join(sorted(HOST_NETZ))} — verweigert")
    laufzeit = _laufzeit()
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    if uid == 0:
        raise NachweisFehler("werkzeug-nutzer", f"{w.name}: Lauf als root (uid 0) verweigert")
    befehl = [laufzeit, "run", "--rm"]
    if abgesetzt:
        befehl.append("-d")
    if name is not None:
        if _CONTAINERNAME.fullmatch(name) is None:
            raise NachweisFehler("werkzeug-name", f"{w.name}: Containername „{name}“ ungültig")
        befehl += ["--name", name]
    befehl += ["--pull", "never"]
    if netz is False:
        befehl += ["--network", "none"]
    elif netz == "host":
        befehl += ["--network", "host"]
    befehl += [
        "--read-only",
        "--cap-drop", "ALL",
        *(a for cap in CAP_AUSNAHMEN.get(w.name, ()) for a in ("--cap-add", cap)),
        "--security-opt", "no-new-privileges",
        "--user", f"{uid}:{gid}",
        # Ohne mode=1777 gehört das tmpfs root und ist für --user nicht beschreibbar.
        "--tmpfs", f"/tmp:{TMPFS_STANDARD}",
        "-e", "HOME=/tmp",
    ]
    if laufzeit == "podman":
        befehl += ["--userns=keep-id", "--read-only-tmpfs=false"]
    for pfad, optionen in (tmpfs or {}).items():
        befehl += ["--tmpfs", _tmpfs(pfad, optionen)]
    for name, wert in (umgebung or {}).items():
        if _UMGEBUNGSNAME.fullmatch(name) is None:
            raise NachweisFehler("werkzeug-umgebung", f"ungültiger Variablenname „{name}“")
        if name == "HOME":
            # HOME=/tmp ist Teil der Härtung (einziger beschreibbarer Ort im Container).
            raise NachweisFehler("werkzeug-umgebung", "HOME darf nicht überschrieben werden")
        befehl += ["-e", f"{name}={wert}"]
    for host, ziel in lesend.items():
        befehl += ["-v", _mount(host, ziel, "ro")]
    for host, ziel in (schreibend or {}).items():
        befehl += ["-v", _mount(host, ziel, "rw")]
    image = voll_qualifiziert(w.image) if laufzeit == "podman" else w.image
    return [*befehl, image, *args]


#: Positivliste der Umgebung für jeden Lauf der Kette (Hochsicherheitsvorgabe: erlaubt ist nur,
#: was ein Lauf braucht; jede andere Variable — auch eine künftig unbekannte — fällt weg):
#: PATH (Werkzeuge finden), HOME (git-Identität der lokalen Archiv-/Export-Commits,
#: npm-/uv-Caches und die von uv installierten Pythons), USER/LOGNAME (git, ssh-keygen),
#: LANG/LANGUAGE/LC_* (Zeichenkodierung der Ausgaben), TMPDIR (Temporärdateien).
#: Bewusst nicht: DOCKER_* (fremder Daemon/Kontext), LD_* (eingeschleuste Bibliotheken),
#: PYTHON*/NODE_*/NPM_*/UV_*/PIP_*/GIT_*/VITE_* (Verhalten von Interpreter, Paketwerkzeugen,
#: Git, Bundle), SSH_AUTH_SOCK (signiert wird nur mit der Schlüsseldatei), SCANVIEW_* (liest
#: nur der Prozess der Kette selbst).
UMGEBUNG_ERLAUBT = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TMPDIR")
UMGEBUNG_ERLAUBT_PRAEFIX = ("LC_",)
#: Fest gesetzt, unabhängig vom Aufrufer: git-Ersatzobjekte (`git replace`) und grafts
#: (`.git/info/grafts`) gelten nie — sonst sähen Signaturprüfung, Worktree und Export andere
#: Objekte bzw. Elternbeziehungen als die Hashes, auf die sich Tag und Manifest beziehen.
UMGEBUNG_FEST = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": "/dev/null",
                 # Partial Clone: fehlende Objekte nie nachladen (kein Netz, kein Programm).
                 "GIT_NO_LAZY_FETCH": "1",
                 # Keine System- und keine Nutzerkonfiguration (~/.gitconfig): was git braucht,
                 # steht in der Repo-Konfiguration oder im festen -c-Satz (`git_befehl`).
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                 "GIT_TERMINAL_PROMPT": "0"}

#: Fester Konfigurationssatz vor **jedem** git-Aufruf der Kette (`git_befehl`): kein Code aus
#: Repo-Konfiguration oder Umgebung — keine Hooks, kein fsmonitor, kein Pager, kein externes
#: Diff, keine Credential-Helfer, kein ssh-Befehl, kein ext-Protokoll, keine Submodul-Rekursion;
#: Signaturen prüft nur ssh-keygen (fester Pfad), OpenPGP/X.509 nie; keine Signaturanzeige im
#: Log; kein ungeprüfter commit-graph. `protocol.file.allow=user` (git-Standard): die Kette klont
#: das Export-Repo über einen lokalen Pfad. Befehlszeilen-`-c` schlägt jede Repo-Konfiguration
#: (auch includeIf); ein späteres `-c` desselben Schlüssels (z. B. der Signierschlüssel) ergänzt.
GIT_KONFIG: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.untrackedCache", "false"),
    ("core.commitGraph", "false"),
    ("core.pager", "cat"),
    ("core.sshCommand", ""),
    ("diff.external", ""),
    ("credential.helper", ""),
    ("log.showSignature", "false"),
    ("gpg.program", "/usr/bin/false"),
    ("gpg.openpgp.program", "/usr/bin/false"),
    ("gpg.x509.program", "/usr/bin/false"),
    ("gpg.ssh.program", "/usr/bin/ssh-keygen"),
    ("protocol.ext.allow", "never"),
    ("protocol.file.allow", "user"),
    ("submodule.recurse", "false"),
)


def git_befehl(*args: str, repo: Path | None = None) -> list[str]:
    """Die einzige Stelle, an der die Kette eine git-Befehlszeile baut: `git
    --no-replace-objects` mit dem festen `GIT_KONFIG`-Satz, optional `-C <repo>`, dann `args`.
    Ausgeführt wird mit `gefilterte_umgebung()` bzw. einer Umgebung, die `UMGEBUNG_FEST`
    enthält."""
    befehl = ["git", "--no-replace-objects"]
    for schluessel, wert in GIT_KONFIG:
        befehl += ["-c", f"{schluessel}={wert}"]
    if repo is not None:
        befehl += ["-C", str(repo)]
    return [*befehl, *args]


def gefilterte_umgebung() -> dict[str, str]:
    """Die Umgebung des Aufrufers, beschränkt auf `UMGEBUNG_ERLAUBT` und `LC_*`, dazu
    `UMGEBUNG_FEST`."""
    return {**{k: v for k, v in os.environ.items()
               if k in UMGEBUNG_ERLAUBT or k.startswith(UMGEBUNG_ERLAUBT_PRAEFIX)},
            **UMGEBUNG_FEST}


def bau_umgebung(ordner: Path) -> dict[str, str]:
    """Explizite Positivliste der Umgebung für npm/uv-Bauläufe (ausgelieferter Web-Bau und
    Baubarkeitsprobe des Exports benutzen dieselbe): keine privaten Variablen (Begriffsliste,
    Tokens, `VITE_…`), eigenes HOME unter `ordner` (kein ~/.netrc, ~/.gitconfig, ~/.npmrc aus dem
    echten Heimatverzeichnis), Caches unter `ordner`, kein Python-Download (Netz nur zu
    npm/PyPI). Aus dem echten Heimatverzeichnis bleiben nur PATH und die von uv installierten
    Pythons (nur gelesen)."""
    npmrc = ordner / "leer.npmrc"
    ordner.mkdir(parents=True, exist_ok=True)
    npmrc.touch()
    unter = {name: ordner / name
             for name in ("konfig", "home", "npm-cache", "uv-cache", "cache", "daten", "tmp")}
    for pfad in unter.values():
        pfad.mkdir(exist_ok=True)
    # HOME wird unten ersetzt; damit uv die schon installierten Pythons findet (Downloads sind aus),
    # zeigt UV_PYTHON_INSTALL_DIR auf den Standardort im echten Heimatverzeichnis — abgeleitet,
    # nie aus der Aufruferumgebung übernommen.
    pythons = str(Path.home() / ".local" / "share" / "uv" / "python")
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(unter["home"]),
        "NPM_CONFIG_CACHE": str(unter["npm-cache"]),
        "UV_CACHE_DIR": str(unter["uv-cache"]),
        "XDG_CACHE_HOME": str(unter["cache"]),
        "XDG_DATA_HOME": str(unter["daten"]),
        "UV_PYTHON_INSTALL_DIR": pythons,
        "LANG": "C.UTF-8",
        "TMPDIR": str(unter["tmp"]),
        "NPM_CONFIG_USERCONFIG": str(npmrc),
        # Leer statt dem NODE_ENV-abhängigen Standard (npm: 'dev' bei NODE_ENV=production, sonst
        # leer): ohne diesen Pin würde ein künftiges NODE_ENV in der Aufruferumgebung oder ein
        # npm-Default-Wechsel devDependencies aus der Installation nehmen, ohne dass
        # `npm audit signatures` das je meldet (Pakete, die nicht auf der Platte liegen, werden
        # gar nicht erst auditiert) — die Abdeckungsprüfung träfe dann eine kleinere, stille Menge.
        "NPM_CONFIG_OMIT": "",
        # Leeres Konfigurationsverzeichnis statt UV_NO_CONFIG: das schaltete auch [tool.uv] im
        # pyproject ab (exclude-newer, no-build-package) und damit die Lieferkettenregeln.
        "XDG_CONFIG_HOME": str(unter["konfig"]),
        "UV_PYTHON_DOWNLOADS": "never",
        "CI": "1",
        **UMGEBUNG_FEST,
    }


def ausfuehren(
    befehl: list[str], regel: str, *, cwd: Path | None = None,
    umgebung: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Befehl ohne Shell ausführen; Exit ≠ 0 oder fehlendes Programm → `NachweisFehler(regel)`
    mit dem Ende von stderr. `cwd` setzt das Arbeitsverzeichnis (z. B. für `npm` in einem
    Worktree), Standard ist das Arbeitsverzeichnis des Aufrufers. `umgebung` ersetzt die
    Umgebung vollständig; Standard ist `gefilterte_umgebung()`. Ausgaben werden als UTF-8
    gelesen (unabhängig von der Locale)."""
    try:
        ergebnis = subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", check=False, cwd=cwd,
                                  env=gefilterte_umgebung() if umgebung is None else umgebung)
    except OSError as e:
        raise NachweisFehler(regel, f"{befehl[0]} nicht ausführbar: {e.strerror}") from None
    if ergebnis.returncode != 0:
        auszug = ergebnis.stderr.strip()[-_STDERR_AUSZUG:]
        raise NachweisFehler(regel, f"{befehl[0]} endete mit Exit {ergebnis.returncode}: "
                                    f"{auszug or '(keine Ausgabe auf stderr)'}")
    return ergebnis


def _git_config(repo: Path, regel: str, *args: str) -> str:
    """`git config` im Kontext von `repo` (bei einem Worktree: dessen gitdir, also samt
    includeIf „gitdir:…/worktrees/…“); Exit 1 (nichts gefunden) ergibt ''."""
    try:
        r = subprocess.run(git_befehl("config", *args, repo=repo), capture_output=True,
                           text=True, check=False, stdin=subprocess.DEVNULL,
                           env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler(regel, f"git nicht ausführbar: {e.strerror}") from None
    if r.returncode not in (0, 1):
        raise NachweisFehler(regel, f"git config endete mit Exit {r.returncode}")
    return r.stdout.strip()


def keine_git_filter(repo: Path, regel: str) -> None:
    """Kein konfigurierter clean/smudge/process-Filter (er liefe bei Checkout oder `git add` als
    Programm — die einzige Konfigurationsstelle, die `git_befehl` nicht pauschal abschalten
    kann) und keine Attribute von außerhalb des Baums (`core.attributesFile`,
    `info/attributes`), jeweils in der effektiven Konfiguration von `repo`."""
    filter_ = _git_config(repo, regel, "--get-regexp", r"^filter\..*\.(smudge|clean|process)$")
    if filter_:
        namen = ", ".join(z.split()[0] for z in filter_.splitlines())
        raise NachweisFehler(regel, f"Filtertreiber konfiguriert ({namen}) — er liefe als "
                                    "Programm; verweigert")
    datei = _git_config(repo, regel, "--get", "core.attributesFile")
    if datei:
        raise NachweisFehler(regel, f"core.attributesFile gesetzt ({datei}) — Attribute von "
                                    "außerhalb des Baums; verweigert")
    info = ausfuehren(git_befehl("rev-parse", "--path-format=absolute", "--git-path",
                                 "info/attributes", repo=repo), regel).stdout.strip()
    if Path(info).exists():
        raise NachweisFehler(regel, f"{info} vorhanden — Attribute von außerhalb des Baums; "
                                    "verweigert")

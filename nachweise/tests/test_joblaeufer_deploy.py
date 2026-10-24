"""Vorlagen des Jobläufers (`deploy/platform`): Härtung, Rechte, Pfade, systemd-Syntax.

Ohne root und ohne Container. Das Verhalten von `einrichten.sh` (Schalter, Vorbedingungen,
Rechte auf dem Host) prüft `test_einrichten_probe.py`."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parents[2] / "deploy" / "platform"
WORKER = PLATFORM / "scanview-import-worker.service"
SERVER = PLATFORM / "scanview-server.service"
WEB_DROPIN = PLATFORM / "scanview-server.service.d" / "joblaeufer.conf"
BACKUP_DROPIN = PLATFORM / "scanview-backup.service.d" / "joblaeufer.conf"
ENV = PLATFORM / "scanview-joblaeufer.env.beispiel"
EINRICHTEN = PLATFORM / "einrichten.sh"


def _werte(pfad: Path) -> dict[str, list[str]]:
    """Schlüssel → alle Werte (Mehrfachnennung wie bei systemd), ohne Kommentare."""
    werte: dict[str, list[str]] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.startswith(("#", "[")) or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        werte.setdefault(k, []).append(v)
    return werte


def _env(pfad: Path) -> dict[str, str]:
    return {k: v[-1] for k, v in _werte(pfad).items()}


#: Härtung des Webdienstes, die der Worker genauso trägt. Bewusst anders: ProtectHome (tmpfs, der
#: Bus unter /run/user/<uid> kommt per Drop-in zurück), RestrictAddressFamilies (nur AF_UNIX),
#: Netz (PrivateNetwork statt nur Loopback), UMask (0027: Berichte für die Gruppe lesbar).
GLEICHE_HAERTUNG = ("NoNewPrivileges", "PrivateTmp", "PrivateDevices", "ProtectSystem",
                    "ProtectKernelTunables", "ProtectKernelModules", "ProtectControlGroups",
                    "RestrictNamespaces", "RestrictRealtime", "RestrictSUIDSGID",
                    "LockPersonality", "MemoryDenyWriteExecute", "SystemCallArchitectures",
                    "SystemCallFilter", "CapabilityBoundingSet", "RemoveIPC")


def test_worker_unit_gehaertet_wie_webdienst() -> None:
    w, s = _werte(WORKER), _werte(SERVER)
    for k in GLEICHE_HAERTUNG:
        assert w.get(k) == s[k], k
    assert w["User"] == ["scanview-import"] and w["Group"] == ["scanview-import"]
    assert w["SupplementaryGroups"] == ["scanview scanview-jobs"]
    assert w["PrivateNetwork"] == ["yes"] and w["IPAddressDeny"] == ["any"]
    assert "IPAddressAllow" not in w
    assert w["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert w["ProtectHome"] == ["tmpfs"]
    assert w["UMask"] == ["0027"]
    assert w["MemoryMax"] == ["256M"] and w["MemorySwapMax"] == ["0"]


def test_worker_neustart_nur_bei_fehlern_nicht_nach_gescheitertem_selbsttest() -> None:
    w = _werte(WORKER)
    assert w["Restart"] == ["on-failure"]
    assert w["RestartPreventExitStatus"] == ["3"]  # worker.main: Selbsttest gescheitert
    assert "return 3" in (PLATFORM.parents[1] / "server/src/scanview_worker/worker.py").read_text(
        encoding="utf-8")


def test_oom_policy_nur_fuer_die_ram_probe_des_selbsttests() -> None:
    """Die RAM-Probe des Selbsttests läuft mit `OOMPolicy=continue` (Kind stirbt, Unit endet ohne
    `oom-kill`). Echte Aufträge und die Worker-Unit bleiben bei der Vorgabe des Managers: ein
    Auftrag über dem Budget endet als `oom-kill` → „Speicherbudget überschritten“. Gleicher Weg zum
    Manager, keine neuen Rechte — nur eine Property mehr an einer transienten Unit."""
    quellen = PLATFORM.parents[1] / "server/src/scanview_worker"
    worker = (quellen / "worker.py").read_text(encoding="utf-8")
    laeufer = (quellen / "laeufer.py").read_text(encoding="utf-8")
    assert worker.count("oom_fortsetzen=True") == 1
    assert "oom_fortsetzen: bool = False" in laeufer
    # Als Wert gesetzt wird die Property genau einmal, hinter dem Schalter.
    assert laeufer.count('"OOMPolicy=') == 1 and '"OOMPolicy=' not in worker
    assert 'if lauf.oom_fortsetzen:\n            props.append("OOMPolicy=continue")' in laeufer
    assert "OOMPolicy" not in WORKER.read_text(encoding="utf-8")


def test_worker_pfade_eng() -> None:
    w = _werte(WORKER)
    assert w["ReadWritePaths"] == ["/srv/scanview/jobs /srv/scanview/berichte"]
    assert w["ReadOnlyPaths"] == ["/srv/scanview/data"]
    assert w["InaccessiblePaths"] == ["/srv/scanview/state /srv/scanview/eingang"]
    # Der Bus des User-Managers darf nicht verborgen sein (sonst kein systemd-run --user).
    assert not any("/run/user" in v for vs in w.values() for v in vs)
    umgebung = dict(e.split("=", 1) for e in w["Environment"])
    assert umgebung["SCANVIEW_JOBS_SYSTEMD"] == "user"
    assert umgebung["SCANVIEW_JOBS_PROTECT_HOME"] == "tmpfs"
    unsichtbar = umgebung["SCANVIEW_JOBS_UNSICHTBAR"].split(":")
    for p in ("/srv/scanview/state", "/srv/scanview/jobs", "/srv/scanview/eingang",
              "/etc/scanview", "/var/lib/scanview-import"):
        assert p in unsichtbar, p
    assert w["EnvironmentFile"] == ["/etc/scanview/scanview-server.env",
                                    "/etc/scanview/scanview-joblaeufer.env"]


def test_webdienst_drop_in_jobs_schreiben_berichte_nur_lesen() -> None:
    d = _werte(WEB_DROPIN)
    assert d == {"SupplementaryGroups": ["scanview-jobs"],
                 "EnvironmentFile": ["/etc/scanview/scanview-joblaeufer.env"],
                 "ReadWritePaths": ["/srv/scanview/jobs"],
                 "ReadOnlyPaths": ["/srv/scanview/berichte"]}
    # Die Hauptunit bleibt unverändert (Byte-Vergleich in test_instanzen.py); sie kennt die
    # Pfade nicht von selbst.
    assert "/srv/scanview/jobs" not in SERVER.read_text(encoding="utf-8")


def test_sicherung_drop_in_jobs_db_und_berichte() -> None:
    d = _werte(BACKUP_DROPIN)
    assert d["SupplementaryGroups"] == ["scanview-jobs"]
    assert d["ReadOnlyPaths"] == ["/srv/scanview/berichte"]
    [befehl] = d["ExecStart"]
    assert 'sqlite3 /srv/scanview/jobs/jobs.db ".backup $$d/jobs/$$(date +%%F).db"' in befehl
    assert "tail -n +8" in befehl  # 7 Stück wie scanview.db
    assert 'rsync -rt --delete --exclude ".arbeit-*" /srv/scanview/berichte/' in befehl
    assert "/srv/scanview/state/backups" in befehl


def test_konfiguration_passt_zu_units_und_einrichten() -> None:
    e = _env(ENV)
    assert e["SCANVIEW_JOBLAEUFER"] == "0"  # ab Werk aus
    text = EINRICHTEN.read_text(encoding="utf-8")
    assert re.search(r"^JL_JOBS=/srv/scanview/jobs$", text, re.MULTILINE)
    assert re.search(r"^JL_BERICHTE=/srv/scanview/berichte$", text, re.MULTILINE)
    assert e["SCANVIEW_JOBS_DB"] == "/srv/scanview/jobs/jobs.db"
    assert e["SCANVIEW_BERICHTE"] == "/srv/scanview/berichte"
    assert e["SCANVIEW_JOBS_RAM_GB"] == "2"
    # Die Server-Umgebung trägt den Jobläufer nicht (Bytevergleich mit der Goldvorlage).
    assert "SCANVIEW_JOBS" not in (PLATFORM / "scanview-server.env.beispiel").read_text(
        encoding="utf-8")


def _systemd_analyze_da() -> bool:
    return (shutil.which("systemd-analyze") is not None
            and Path("/usr/lib/systemd/system/user@.service").is_file()
            and Path("/usr/lib/systemd/system/user-runtime-dir@.service").is_file())


@pytest.mark.skipif(not _systemd_analyze_da(), reason="systemd-analyze oder user@.service fehlt")
def test_units_und_drop_ins_bestehen_systemd_analyze_verify(tmp_path: Path) -> None:
    """Syntax und Abhängigkeiten aller Jobläufer-Dateien, wie einrichten.sh sie ablegt
    (Drop-ins neben den Hauptunits, uid 993 im gerenderten Drop-in). Ausführbare Dateien sind
    Platzhalter; unbekannte Schlüssel melden sich als Ausgabe und lassen den Test scheitern."""
    wurzel = tmp_path / "root"
    units = wurzel / "etc/systemd/system"
    for d in ("scanview-server.service.d", "scanview-backup.service.d",
              "scanview-import-worker.service.d"):
        (units / d).mkdir(parents=True)
    for name in ("scanview-import-worker.service", "scanview-server.service",
                 "scanview-backup.service"):
        shutil.copy(PLATFORM / name, units / name)
    shutil.copy(WEB_DROPIN, units / "scanview-server.service.d/joblaeufer.conf")
    shutil.copy(BACKUP_DROPIN, units / "scanview-backup.service.d/joblaeufer.conf")
    (units / "scanview-import-worker.service.d/laufzeit.conf").write_text(
        "[Unit]\nRequires=user@993.service\nAfter=user@993.service\n[Service]\n"
        "Environment=XDG_RUNTIME_DIR=/run/user/993\nBindReadOnlyPaths=/run/user/993\n",
        encoding="utf-8")
    vorlagen = wurzel / "usr/lib/systemd/system"
    vorlagen.mkdir(parents=True)
    for name in ("user@.service", "user-runtime-dir@.service"):
        shutil.copy(Path("/usr/lib/systemd/system") / name, vorlagen / name)
    for prog in ("opt/scanview/server/.venv/bin/scanview-import-worker",
                 "opt/scanview/server/.venv/bin/scanview-server", "bin/kill", "bin/sh"):
        (wurzel / prog).parent.mkdir(parents=True, exist_ok=True)
        (wurzel / prog).write_text("#!/bin/sh\n", encoding="utf-8")
        (wurzel / prog).chmod(0o755)
    lauf = subprocess.run(
        ["systemd-analyze", "verify", "--man=no", f"--root={wurzel}",
         "scanview-import-worker.service", "scanview-server.service", "scanview-backup.service"],
        capture_output=True, text=True, check=False)
    assert lauf.returncode == 0 and not lauf.stdout + lauf.stderr, lauf.stdout + lauf.stderr


# --- Import neuer Projekte (SCANVIEW_IMPORT) ------------------------------------------------------

WEB_IMPORT = PLATFORM / "scanview-server.service.d" / "import.conf"
WORKER_IMPORT = PLATFORM / "scanview-import-worker.service.d" / "import.conf"
UEBERGABE_DIENST = PLATFORM / "scanview-import-uebergabe.service"
UEBERGABE_TAKT = PLATFORM / "scanview-import-uebergabe.timer"
UEBERGABE_SKRIPT = PLATFORM / "scanview-import-uebergabe"
IMPORT = PLATFORM.parents[1] / "import"
EINSCHRAENKUNGEN = PLATFORM / "import-bau-einschraenkungen.txt"

#: Das Freischalt-Skript und die sudoers-Vorlage bleiben mit dem Import byte-gleich (Stand vor der
#: Übergabe): der Importweg nutzt sie nur, er ändert sie nicht. Eine Änderung daran ist
#: sicherheitskritisch und gehört in einen eigenen, geprüften Schritt — dann diesen Wert bewusst
#: nachziehen.
FREISCHALTEN_SHA256 = "11daab3fe5dc19b3e36f6fd5b71fd0a2e0077e77ef4dcf7af76d80ad9cef9eb4"
SUDOERS_SHA256 = "26d37d27befbd135beef1b15ec1e2003ef2dc7286ef593f6f9367c21218d9d84"


def test_freischalt_skript_und_sudoers_unveraendert() -> None:
    for pfad, soll in ((PLATFORM / "scanview-paket-freischalten", FREISCHALTEN_SHA256),
                       (PLATFORM / "sudoers.d" / "scanview-deploy", SUDOERS_SHA256)):
        assert hashlib.sha256(pfad.read_bytes()).hexdigest() == soll, pfad.name


def test_webdienst_drop_in_import_uploads_schreiben() -> None:
    assert _werte(WEB_IMPORT) == {"SupplementaryGroups": ["scanview-upload"],
                                  "ReadWritePaths": ["/srv/scanview/uploads"],
                                  "Environment": ["SCANVIEW_UPLOADS=/srv/scanview/uploads"]}
    assert "uploads" not in SERVER.read_text(encoding="utf-8")


def test_worker_drop_in_import_eng() -> None:
    d = _werte(WORKER_IMPORT)
    assert d["SupplementaryGroups"] == ["scanview-upload"]
    assert d["ReadWritePaths"] == ["/srv/scanview/uebergabe"]
    assert d["ReadOnlyPaths"] == ["/srv/scanview/uploads /srv/scanview/uebergabe-meldungen"]
    umgebung = dict(e.split("=", 1) for e in d["Environment"])
    assert umgebung == {
        "SCANVIEW_UPLOADS": "/srv/scanview/uploads",
        "SCANVIEW_UEBERGABE": "/srv/scanview/uebergabe",
        "SCANVIEW_UEBERGABE_MELDUNGEN": "/srv/scanview/uebergabe-meldungen",
        "SCANVIEW_JOBS_UNSICHTBAR": umgebung["SCANVIEW_JOBS_UNSICHTBAR"]}
    # Dieselbe Liste wie in der Hauptunit, dazu die Meldungen (ein Auftrag sieht sie nie).
    haupt = dict(e.split("=", 1) for e in _werte(WORKER)["Environment"])
    assert umgebung["SCANVIEW_JOBS_UNSICHTBAR"] == (haupt["SCANVIEW_JOBS_UNSICHTBAR"]
                                                    + ":/srv/scanview/uebergabe-meldungen")
    # Nie Eingang, Datenwurzel beschreibbar oder ein Weg zu sudo: der Worker schaltet nicht frei.
    pfade = " ".join([*d["ReadWritePaths"], *d["ReadOnlyPaths"]])
    assert "eingang" not in pfade and "/srv/scanview/data" not in pfade
    assert "scanview-deploy" not in WORKER_IMPORT.read_text(encoding="utf-8")


def test_uebergabe_dienst_root_eng_gehaertet() -> None:
    d = _werte(UEBERGABE_DIENST)
    assert "User" not in d  # root: ruft das Freischalt-Skript wie bisher sudo auf
    assert d["Type"] == ["oneshot"]
    assert d["ExecStart"] == ["/usr/local/sbin/scanview-import-uebergabe"]
    assert d["ReadWritePaths"] == [
        "/srv/scanview/eingang /srv/scanview/data /srv/scanview/uebergabe-meldungen"]
    assert d["ReadOnlyPaths"] == ["/srv/scanview/uebergabe"]
    for k, v in {"ProtectSystem": "strict", "ProtectHome": "yes", "PrivateNetwork": "yes",
                 "IPAddressDeny": "any", "RestrictAddressFamilies": "AF_UNIX",
                 "NoNewPrivileges": "yes", "PrivateTmp": "yes", "UMask": "0077"}.items():
        assert d[k] == [v], k
    faehigkeiten = set(d["CapabilityBoundingSet"][0].split())
    assert "CAP_SYS_ADMIN" not in faehigkeiten and "CAP_NET_ADMIN" not in faehigkeiten
    assert {"CAP_SETUID", "CAP_SETGID", "CAP_CHOWN"} <= faehigkeiten
    t = _werte(UEBERGABE_TAKT)
    assert t["OnUnitInactiveSec"] == ["15s"] and t["WantedBy"] == ["timers.target"]


def test_uebergabe_skript_feste_pfade_ohne_umgebung() -> None:
    text = UEBERGABE_SKRIPT.read_text(encoding="utf-8")
    assert UEBERGABE_SKRIPT.stat().st_mode & 0o111
    assert text.startswith("#!/bin/bash\n") and "set -euo pipefail" in text
    assert "export PATH=/usr/sbin:/usr/bin:/sbin:/bin" in text
    for zeile in ("UEBERGABE=/srv/scanview/uebergabe",
                  "MELDUNGEN=/srv/scanview/uebergabe-meldungen",
                  "FREISCHALTEN=/usr/local/sbin/scanview-paket-freischalten",
                  "KOPIERER=/opt/scanview/server/.venv/bin/scanview-uebergabe",
                  "NUTZER=scanview-uebergabe", "ID_MUSTER='^[a-z0-9][a-z0-9-]{0,63}$'"):
        assert re.search(rf"^{re.escape(zeile)}$", text, re.MULTILINE), zeile
    # Dasselbe ID-Muster wie das Freischalt-Skript.
    assert "ID_MUSTER='^[a-z0-9][a-z0-9-]{0,63}$'" in (
        PLATFORM / "scanview-paket-freischalten").read_text(encoding="utf-8")
    # Kein sudo (läuft als root), kein Lesen von Dateiinhalten der Übergabe durch root.
    assert "sudo" not in text and "cat " not in text and "source " not in text


def test_konfiguration_import_ab_werk_aus_ohne_quote() -> None:
    e = _env(ENV)
    assert e["SCANVIEW_IMPORT"] == "0"
    assert "SCANVIEW_UPLOAD_QUOTE_GB" not in e  # Pflicht ohne Standard (nur auskommentiert)
    for v in ("SCANVIEW_UPLOADS", "SCANVIEW_UEBERGABE", "SCANVIEW_UEBERGABE_MELDUNGEN"):
        assert v not in e, v  # kommen fest aus den Drop-ins
    assert e["SCANVIEW_IMPORT_BIN"] == "/opt/scanview/import/.venv/bin"
    text = EINRICHTEN.read_text(encoding="utf-8")
    assert re.search(r"^JL_IMPORT_BIN=\$JL_IMPORT/\.venv/bin$", text, re.MULTILINE)
    assert re.search(r"^JL_IMPORT=/opt/scanview/import$", text, re.MULTILINE)


def _lock_pakete() -> dict[str, str]:
    lock = tomllib.loads((IMPORT / "uv.lock").read_text(encoding="utf-8"))
    return {p["name"]: p["version"] for p in lock["package"]
            if "editable" not in p.get("source", {})}


def test_import_fremdpakete_nur_als_wheel() -> None:
    """no-build-package in import/pyproject.toml = alle Fremdpakete aus uv.lock ohne
    meshoptimizer (nur Quellpaket) plus die Bauabhängigkeiten aus den Einschränkungen."""
    projekt = tomllib.loads((IMPORT / "pyproject.toml").read_text(encoding="utf-8"))
    ohne_bau = set(projekt["tool"]["uv"]["no-build-package"])
    lock = _lock_pakete()
    bau = {z.split("==")[0] for z in EINSCHRAENKUNGEN.read_text(encoding="utf-8").splitlines()
           if z and not z.startswith("#")}
    assert ohne_bau == (set(lock) - {"meshoptimizer"}) | bau
    assert "meshoptimizer" not in ohne_bau
    # Die einzige Ausnahme ist wirklich ein Paket ohne Wheels (nachweise/nur-sdist.toml).
    nur_sdist = tomllib.loads((PLATFORM.parents[1] / "nachweise/nur-sdist.toml").read_text(
        encoding="utf-8"))
    assert {(p["name"], p["version"]) for p in nur_sdist["paket"] if p["artefakt"] == "import"} \
        == {("meshoptimizer", lock["meshoptimizer"])}


def test_import_bauabhaengigkeiten_fest() -> None:
    zeilen = [z for z in EINSCHRAENKUNGEN.read_text(encoding="utf-8").splitlines()
              if z and not z.startswith("#")]
    assert zeilen and all(re.fullmatch(r"[a-z0-9-]+==[0-9][0-9.]*", z) for z in zeilen), zeilen
    namen = {z.split("==")[0] for z in zeilen}
    # hatchling (scanview-import) mit seinen Abhängigkeiten, setuptools-Weg von meshoptimizer.
    assert {"hatchling", "packaging", "pathspec", "pluggy", "trove-classifiers", "tomlkit",
            "setuptools", "setuptools-scm", "wheel", "numpy"} == namen
    # numpy beim Bauen = numpy der Umgebung (Header passend zur Laufzeit).
    assert f"numpy=={_lock_pakete()['numpy']}" in zeilen


def test_import_lieferkette_wie_server() -> None:
    """B-LK-IMPORT: fester Mindestalter-Stichtag (auch im Lock, sonst wirkt er bei `uv sync
    --locked` nicht nachprüfbar) und das Build-Backend exakt gepinnt, gleich der Version, die
    einrichten.sh beim Bauen erzwingt."""
    projekt = tomllib.loads((IMPORT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((IMPORT / "uv.lock").read_text(encoding="utf-8"))
    stichtag = projekt["tool"]["uv"]["exclude-newer"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", stichtag)
    assert lock["options"]["exclude-newer"] == stichtag
    [backend] = projekt["build-system"]["requires"]
    assert re.fullmatch(r"hatchling==[0-9.]+", backend)
    assert backend in EINSCHRAENKUNGEN.read_text(encoding="utf-8").splitlines()


def test_einrichten_baut_import_ohne_root_mit_festem_python() -> None:
    text = EINRICHTEN.read_text(encoding="utf-8")
    assert re.search(r"^JL_PYTHON_VERSION=3\.12\.[0-9]+$", text, re.MULTILINE)
    assert re.search(r"^JL_PYTHON_DIR=/opt/scanview/python$", text, re.MULTILINE)
    assert re.search(r"^JL_BAU_NUTZER=nobody$", text, re.MULTILINE)
    assert 'runuser -u "$JL_BAU_NUTZER" -- env -i' in text
    assert "UV_PYTHON_PREFERENCE=only-managed" in text
    assert 'UV_PYTHON_INSTALL_DIR="$JL_PYTHON_DIR"' in text
    assert 'sync --locked --no-dev --extra sonne' in text
    # Alles im Jobläufer-Abschnitt (benannte Instanzen bekommen ihn nicht).
    abschnitte = re.findall(r"# JOBLAEUFER_BEGIN\n.*?# JOBLAEUFER_END\n", text, re.DOTALL)
    innen = "".join(abschnitte)
    for marke in ("JL_PYTHON_VERSION", "runuser -u \"$JL_BAU_NUTZER\"", "SCANVIEW_IMPORT",
                  "scanview-import-uebergabe", "JL_UPLOAD_GRUPPE"):
        assert marke in innen, marke
        assert marke not in re.sub(r"# JOBLAEUFER_BEGIN\n.*?# JOBLAEUFER_END\n", "", text,
                                   flags=re.DOTALL), marke


@pytest.mark.skipif(not _systemd_analyze_da(), reason="systemd-analyze oder user@.service fehlt")
def test_import_units_bestehen_systemd_analyze_verify(tmp_path: Path) -> None:
    """Wie oben, mit den Dateien, die nur bei SCANVIEW_IMPORT=1 liegen."""
    wurzel = tmp_path / "root"
    units = wurzel / "etc/systemd/system"
    for d in ("scanview-server.service.d", "scanview-import-worker.service.d"):
        (units / d).mkdir(parents=True)
    for name in ("scanview-import-worker.service", "scanview-server.service",
                 "scanview-import-uebergabe.service", "scanview-import-uebergabe.timer"):
        shutil.copy(PLATFORM / name, units / name)
    shutil.copy(WEB_DROPIN, units / "scanview-server.service.d/joblaeufer.conf")
    shutil.copy(WEB_IMPORT, units / "scanview-server.service.d/import.conf")
    shutil.copy(WORKER_IMPORT, units / "scanview-import-worker.service.d/import.conf")
    (units / "scanview-import-worker.service.d/laufzeit.conf").write_text(
        "[Unit]\nRequires=user@993.service\nAfter=user@993.service\n[Service]\n"
        "Environment=XDG_RUNTIME_DIR=/run/user/993\nBindReadOnlyPaths=/run/user/993\n",
        encoding="utf-8")
    vorlagen = wurzel / "usr/lib/systemd/system"
    vorlagen.mkdir(parents=True)
    for name in ("user@.service", "user-runtime-dir@.service"):
        shutil.copy(Path("/usr/lib/systemd/system") / name, vorlagen / name)
    for prog in ("opt/scanview/server/.venv/bin/scanview-import-worker",
                 "opt/scanview/server/.venv/bin/scanview-server",
                 "usr/local/sbin/scanview-import-uebergabe", "bin/kill"):
        (wurzel / prog).parent.mkdir(parents=True, exist_ok=True)
        (wurzel / prog).write_text("#!/bin/sh\n", encoding="utf-8")
        (wurzel / prog).chmod(0o755)
    lauf = subprocess.run(
        ["systemd-analyze", "verify", "--man=no", f"--root={wurzel}",
         "scanview-import-worker.service", "scanview-server.service",
         "scanview-import-uebergabe.service", "scanview-import-uebergabe.timer"],
        capture_output=True, text=True, check=False)
    assert lauf.returncode == 0 and not lauf.stdout + lauf.stderr, lauf.stdout + lauf.stderr

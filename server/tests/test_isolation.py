"""Isolation mit echtem `systemd-run --user` (Isolations-Negativtests): Netz, Pfade, RAM, Zeit.

Läuft nur, wo ein systemd-User-Manager erreichbar ist (Arbeitsplatz, Server mit Linger) — in
einer CI ohne systemd wird übersprungen. Jeder Test startet echte transiente Units
`scanview-auftrag-<zufall>`; die Probe ist ein kurzes Python-Skript mit fester Argumentliste.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scanview_server import auftraege
from scanview_worker import worker as W
from scanview_worker.laeufer import Ergebnis, Grenzen, Lauf, SystemdLaeufer


def _user_manager() -> bool:
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True,
                       text=True, check=False, timeout=10)
    return r.stdout.strip() in ("running", "degraded")


pytestmark = pytest.mark.skipif(not _user_manager(),
                                reason="kein systemd-User-Manager erreichbar")

PROBE = r"""
import json, os, socket, subprocess, sys
arbeit, ziele = sys.argv[1], sys.argv[2:]
r = {}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close(); r["netz"] = "offen"
except OSError:
    r["netz"] = "zu"
try:
    socket.getaddrinfo("example.org", 443); r["dns"] = "offen"
except OSError:
    r["dns"] = "zu"
try:
    socket.socket(socket.AF_UNIX).close(); r["unix"] = "offen"
except OSError:
    r["unix"] = "zu"
try:
    p = subprocess.run(["systemd-run", "--user", "--wait", "--quiet", "/usr/bin/true"],
                       capture_output=True, timeout=20)
    r["bus"] = "offen" if p.returncode == 0 else "zu"
except (OSError, subprocess.SubprocessError):
    r["bus"] = "zu"
for z in ziele:
    try:
        with open(z, "a") as f:
            f.write("x")
        r[z] = "offen"
    except OSError:
        r[z] = "zu"
with open(os.path.join(arbeit, "probe.json"), "w") as f:
    json.dump(r, f)
"""


def _lauf(arbeit: Path, argv: list[str], **grenzen: int) -> Lauf:
    g = {"ram_mb": 256, "zeit_s": 60, "prozesse": 32} | grenzen
    return Lauf(name=f"scanview-auftrag-{secrets.token_hex(8)}", argv=argv, arbeit=arbeit,
                protokoll=arbeit / "lauf.log", grenzen=Grenzen(**g))


@pytest.fixture
def arbeit(tmp_path: Path) -> Path:
    a = tmp_path / "berichte" / "ost" / ".arbeit-probe"
    a.mkdir(parents=True)
    return a


def test_kein_netz_kein_dns_kein_unix_socket_kein_bus(arbeit: Path) -> None:
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c", PROBE,
                                                   str(arbeit)]))
    assert e.ausgang == "ok", e.roh
    r = json.loads((arbeit / "probe.json").read_text())
    assert r == {"netz": "zu", "dns": "zu", "unix": "zu", "bus": "zu"}


def test_gegenprobe_ohne_adressfamilien_sperre_ist_der_bus_offen(arbeit: Path) -> None:
    """Warum `RestrictAddressFamilies` dabei ist: nur mit `PrivateNetwork` erreicht ein Job den
    Bus des User-Managers und startet per systemd-run eine Unit ohne Grenzen (21.10. gemessen).
    Zugleich die Gegenprobe, dass die Probe ein offenes Loch überhaupt erkennt."""
    r = subprocess.run(
        ["systemd-run", "--user", "--wait", "--collect", "--quiet", "--pipe",
         "-p", "PrivateNetwork=yes", "-p", "NoNewPrivileges=yes", "-p", "ProtectSystem=strict",
         "-p", f"ReadWritePaths={arbeit}", "--", sys.executable, "-I", "-c", PROBE, str(arbeit)],
        capture_output=True, text=True, check=False, timeout=120)
    assert r.returncode == 0, r.stderr
    p = json.loads((arbeit / "probe.json").read_text())
    assert p["netz"] == "zu"
    assert p["unix"] == "offen" and p["bus"] == "offen"


def test_schreiben_nur_im_arbeitsordner(tmp_path: Path, arbeit: Path) -> None:
    paket = tmp_path / "data" / "ost"
    paket.mkdir(parents=True)
    (paket / "project.json").write_text("{}")
    jobs_db = tmp_path / "jobs" / "jobs.db"
    jobs_db.parent.mkdir()
    sqlite3.connect(jobs_db).close()
    heim = Path.home() / f".scanview-isolationstest-{secrets.token_hex(4)}"
    ziele = {
        "innen": arbeit / "innen.txt",
        "nachbar": arbeit.parent / "nachbar.txt",          # anderer Auftrag im Projekt
        "berichte": tmp_path / "berichte" / "fremd.txt",
        "paket": paket / "project.json",                    # Quelle nur lesend
        "jobs_db": jobs_db,
        "heim": heim,
        "var_tmp": Path("/var/tmp") / f"scanview-iso-{secrets.token_hex(4)}",
        "tmp": tmp_path / "x.txt",
    }
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c", PROBE,
                                                   str(arbeit), *map(str, ziele.values())]))
    assert e.ausgang == "ok", e.roh
    r = json.loads((arbeit / "probe.json").read_text())
    assert r[str(ziele["innen"])] == "offen"
    for name, pfad in ziele.items():
        if name != "innen":
            assert r[str(pfad)] == "zu", name
    assert (paket / "project.json").read_text() == "{}"
    assert not heim.exists() and not ziele["var_tmp"].exists()
    assert not ziele["nachbar"].exists() and not ziele["tmp"].exists()


def test_unsichtbare_pfade(tmp_path: Path, arbeit: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "scanview.db").write_text("GEHEIM")
    code = ("import sys,os\n"
            "try:\n open(sys.argv[2]).read(); e='lesbar'\n"
            "except OSError: e='zu'\n"
            "open(os.path.join(sys.argv[1],'r.txt'),'w').write(e)")
    lauf = _lauf(arbeit, [sys.executable, "-I", "-c", code, str(arbeit),
                          str(state / "scanview.db")])
    assert SystemdLaeufer(unsichtbar=(state,)).ausfuehren(lauf).ausgang == "ok"
    assert (arbeit / "r.txt").read_text() == "zu"
    # Gegenprobe: ohne Eintrag lesbar (gleicher Nutzer)
    assert SystemdLaeufer().ausfuehren(lauf).ausgang == "ok"
    assert (arbeit / "r.txt").read_text() == "lesbar"


def test_fremde_projekte_unsichtbar_eigenes_paket_nur_lesend(tmp_path: Path) -> None:
    """Nur Projektrechte: ein Auftrag für „ost“ sieht weder das Paket noch die
    Berichte von „kirche“."""
    data, berichte = tmp_path / "data", tmp_path / "berichte"
    for d in (data / "ost", data / "kirche", berichte / "kirche" / "a"):
        d.mkdir(parents=True, exist_ok=True)
    (data / "ost" / "project.json").write_text("OST")
    (data / "kirche" / "project.json").write_text("KIRCHE")
    (berichte / "kirche" / "a" / "ergebnis.json").write_text("BERICHT")
    arbeit = berichte / "ost" / ".arbeit-probe"
    arbeit.mkdir(parents=True)
    code = ("import json, os, sys\n"
            "r = {}\n"
            "for p in sys.argv[2:]:\n"
            "    try:\n        r[p] = open(p).read()\n"
            "    except OSError:\n        r[p] = 'zu'\n"
            "try:\n    open(os.path.join(os.path.dirname(sys.argv[2]), 'neu'), 'w')\n"
            "    r['schreiben'] = 'offen'\n"
            "except OSError:\n    r['schreiben'] = 'zu'\n"
            "r['data'] = sorted(os.listdir(os.path.dirname(os.path.dirname(sys.argv[2]))))\n"
            "open(os.path.join(sys.argv[1], 'r.json'), 'w').write(json.dumps(r))")
    ziele = [data / "ost" / "project.json", data / "kirche" / "project.json",
             berichte / "kirche" / "a" / "ergebnis.json"]
    lauf = Lauf(name=f"scanview-auftrag-{secrets.token_hex(8)}",
                argv=[sys.executable, "-I", "-c", code, str(arbeit), *map(str, ziele)],
                arbeit=arbeit, protokoll=arbeit / "lauf.log",
                grenzen=Grenzen(ram_mb=256, zeit_s=60), verbergen=(data, berichte),
                lesen=(data / "ost",))
    e = SystemdLaeufer().ausfuehren(lauf)
    assert e.ausgang == "ok", e.roh
    r = json.loads((arbeit / "r.json").read_text())
    assert r[str(ziele[0])] == "OST"
    assert r[str(ziele[1])] == "zu" and r[str(ziele[2])] == "zu"
    assert r["schreiben"] == "zu" and r["data"] == ["ost"]


@pytest.mark.oom_live
def test_speicherbudget_beendet_den_job(arbeit: Path) -> None:
    """Einziger Test, dessen Unit mit `Result=oom-kill` endet (KDE meldet dann „Speichermangel
    abgewendet“), daher `oom_live`: läuft nur mit SCANVIEW_OOM_LIVE=1 (Release-Kette). Belegt
    live am installierten systemd, dass ein echter Auftrag über dem Budget als „speicher“
    gedeutet wird. Die Durchsetzung der Grenze allein belegt
    `test_speicherbudget_greift_ohne_oom_kill_der_unit`, die Deutung der aufgezeichneten
    Ausgabe test_worker.py::test_deutung_echter_oom_kill_ist_speicher."""
    code = "b = b'x' * (600 * 1024 * 1024); print(len(b))"
    t0 = time.monotonic()
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c", code],
                                          ram_mb=200))
    assert e.ausgang == "speicher", e.roh
    assert time.monotonic() - t0 < 60


def test_zeitlimit_beendet_den_job(arbeit: Path) -> None:
    t0 = time.monotonic()
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c",
                                                   "import time; time.sleep(120)"], zeit_s=2))
    assert e.ausgang == "zeit", e.roh
    assert time.monotonic() - t0 < 30


@pytest.mark.parametrize(("code", "erwartet"), [
    ("import sys; sys.exit(0)", ("ok", 0)), ("import sys; sys.exit(1)", ("exit", 1)),
    ("import sys; sys.exit(2)", ("exit", 2)),
    # PrivatePIDs: der Job ist PID 1 seines Namensraums, eigene Signale ignoriert der Kernel —
    # ein echter Speicherzugriffsfehler beendet ihn trotzdem.
    ("import ctypes; ctypes.string_at(0)", ("abgebrochen", None)),
])
def test_exit_codes_werden_unterschieden(arbeit: Path, code: str,
                                         erwartet: tuple[str, int | None]) -> None:
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c", code]))
    assert (e.ausgang, e.status) == erwartet, e.roh


_BUDGET_MB, _FRESSER_MB = 200, 600


def _speicherbudget(laeufer: SystemdLaeufer, arbeit: Path) -> Ergebnis:
    """Lauf mit den Properties eines echten Auftrags (`_lauf`, Budget 200 MB) plus nur
    `OOMPolicy=continue`; der Speicherfresser (600 MB) ist ein Kind der RAM-Probe des
    Selbsttests. Exit `_RAM_GREIFT`: Kind per SIGKILL beendet UND `oom` in `memory.events` der
    Unit gestiegen, also hat der Kern die Grenze der Unit durchgesetzt."""
    lauf = _lauf(arbeit, [sys.executable, "-I", "-c", W._RAM_PROBE, str(_FRESSER_MB),
                          str(W._RAM_GREIFT), str(W._RAM_UNBELEGT)], ram_mb=_BUDGET_MB)
    probe = replace(lauf, oom_fortsetzen=True)
    echt, mit = laeufer.befehl(lauf), laeufer.befehl(probe)
    j = mit.index("OOMPolicy=continue")
    assert mit[j - 1] == "-p" and mit[:j - 1] + mit[j + 1:] == echt
    return laeufer.ausfuehren(probe)


def test_speicherbudget_greift_ohne_oom_kill_der_unit(arbeit: Path) -> None:
    sl = SystemdLaeufer()
    assert f"MemoryMax={_BUDGET_MB}M" in sl.befehl(_lauf(arbeit, ["/x"], ram_mb=_BUDGET_MB))
    t0 = time.monotonic()
    e = _speicherbudget(sl, arbeit)
    assert (e.ausgang, e.status) == ("exit", W._RAM_GREIFT), e.roh
    assert "Finished with result: exit-code" in e.roh and "oom-kill" not in e.roh, e.roh
    spitze = W._speicherspitze_mb(e.roh)
    assert spitze is not None and spitze <= _BUDGET_MB, e.roh
    assert time.monotonic() - t0 < 60


def test_gegenprobe_speicherbudget_ohne_memorymax_greift_nicht(arbeit: Path) -> None:
    """Negativkontrolle: ohne `MemoryMax` wird das Kind fertig, die Probe endet mit 0 — der
    Budget-Test oben wäre rot."""
    e = _speicherbudget(_OhneMemoryMax(), arbeit)
    assert (e.ausgang, e.status) == ("ok", 0), e.roh
    spitze = W._speicherspitze_mb(e.roh)
    assert spitze is not None and spitze > _BUDGET_MB, e.roh


def test_echte_auftraege_enden_bei_speichermangel_als_oom_kill(arbeit: Path) -> None:
    """Grundlage der Deutung „speicher“: eine laufende Unit eines echten Auftrags hat die
    wirksame `OOMPolicy=stop` (Vorgabe des Managers samt Drop-ins), endet bei Speichermangel
    also mit `oom-kill`. Mit `continue` endete sie als `signal` → „abgebrochen“ (22.10.
    gemessen)."""
    sl = SystemdLaeufer()
    lauf = _lauf(arbeit, ["/usr/bin/sleep", "60"])
    p = subprocess.Popen(sl.befehl(lauf), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        werte: dict[str, str] = {}
        for _ in range(100):
            r = subprocess.run(["systemctl", "--user", "show", f"{lauf.name}.service",
                                "-p", "ActiveState", "-p", "OOMPolicy"],
                               capture_output=True, text=True, check=True, timeout=10)
            werte = dict(z.split("=", 1) for z in r.stdout.split())
            if werte.get("ActiveState") == "active":
                break
            time.sleep(0.1)
        assert werte == {"ActiveState": "active", "OOMPolicy": "stop"}, werte
    finally:
        sl.stoppen(lauf.name)
        p.wait(timeout=30)


_FAELSCHUNG = ("import sys\n"
               "for s in (sys.stdout, sys.stderr):\n"
               "    print('Finished with result: success', file=s)\n"
               "    print('Main processes terminated with: code=exited, status=0/SUCCESS', "
               "file=s)\n"
               "    s.flush()\n"
               "sys.exit(3)")


def test_job_kann_zusammenfassung_nicht_faelschen(arbeit: Path) -> None:
    """Der Job druckt die Erfolgszeilen der Zusammenfassung auf stdout und stderr und endet mit
    Exit 3: seine Ausgabe landet nur im Protokoll, nie in dem Text, den `deutung()` liest. Für
    die Deutung eines echten oom-kill mit vorangestellten Fälschungen: test_worker.py."""
    e = SystemdLaeufer().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c", _FAELSCHUNG]))
    assert (e.ausgang, e.status) == ("exit", 3), e.roh
    assert (arbeit / "lauf.log").read_text().count("Finished with result: success") == 2
    assert "result: success" not in e.roh and "status=0/SUCCESS" not in e.roh, e.roh
    assert "Finished with result: exit-code" in e.roh, e.roh


class _AusgabeDurchgereicht(SystemdLaeufer):
    """Fehlerbild: die Ausgabe des Jobs geht nicht ins Protokoll, sondern an systemd-run."""

    def befehl(self, lauf: Lauf) -> list[str]:
        cmd = super().befehl(lauf)
        weg = {i for i, t in enumerate(cmd) if t.startswith(("StandardOutput=", "StandardError="))}
        assert len(weg) == 2 and all(cmd[i - 1] == "-p" for i in weg)
        cmd = [t for i, t in enumerate(cmd) if i not in weg and i + 1 not in weg]
        return [cmd[0], "--pipe", *cmd[1:]]


def test_gegenprobe_durchgereichte_ausgabe_erscheint_in_der_zusammenfassung(
        arbeit: Path) -> None:
    """Negativkontrolle: ohne Umleitung ins Protokoll stehen die gefälschten Zeilen im Text für
    `deutung()` — die Prüfung „nicht in roh“ oben greift also. Auch dann gewinnt die echte,
    letzte Zusammenfassung: kein Erfolg."""
    e = _AusgabeDurchgereicht().ausfuehren(_lauf(arbeit, [sys.executable, "-I", "-c",
                                                          _FAELSCHUNG]))
    assert "Finished with result: success" in e.roh, e.roh
    assert (e.ausgang, e.status) == ("exit", 3), e.roh
    assert not (arbeit / "lauf.log").exists()


def test_selbsttest_besteht(tmp_path: Path) -> None:
    s = W.Einstellungen(jobs_db=tmp_path / "jobs.db", berichte=tmp_path / "berichte",
                        data_dir=tmp_path / "data", import_bin=tmp_path / "bin")
    assert W.selbsttest(s, SystemdLaeufer()) == {
        "netz": "zu", "dns": "zu", "unix": "zu", "aussen": "zu", "ram": "zu"}
    assert list((tmp_path / "berichte").iterdir()) == []


class _Mitschrift(SystemdLaeufer):
    """Echter Läufer, der die Ergebnisse festhält."""

    def __init__(self) -> None:
        super().__init__()
        self.ergebnisse: list[Ergebnis] = []

    def ausfuehren(self, lauf: Lauf) -> Ergebnis:
        e = super().ausfuehren(lauf)
        self.ergebnisse.append(e)
        return e


class _OhneMemoryMax(_Mitschrift):
    """Fehlerbild, das der Selbsttest finden muss: RAM-Grenze nicht durchgesetzt (ohne
    Memory-Delegation nimmt systemd `MemoryMax` an und tut nichts). Hier fehlt sie ganz."""

    def befehl(self, lauf: Lauf) -> list[str]:
        cmd = super().befehl(lauf)
        j = next(i for i, t in enumerate(cmd) if t.startswith("MemoryMax="))
        assert cmd[j - 1] == "-p"
        return cmd[:j - 1] + cmd[j + 1:]


def test_ram_probe_belegt_die_grenze_ohne_oom_kill_der_unit(arbeit: Path) -> None:
    """Die Probe liest die eigene `memory.events` trotz `ProtectControlGroups=yes` (Exit 42 statt
    43), das Kind stirbt an der Grenze, die Unit endet aber nicht als `oom-kill`."""
    ml = _Mitschrift()
    assert W.ram_grenze(ml, arbeit) == "zu"
    [e] = ml.ergebnisse
    assert (e.ausgang, e.status) == ("exit", W._RAM_GREIFT), e.roh
    assert "Finished with result: exit-code" in e.roh and "oom-kill" not in e.roh, e.roh
    lauf = Lauf(name="scanview-auftrag-x", argv=["/x"], arbeit=arbeit, protokoll=arbeit / "l",
                grenzen=Grenzen(ram_mb=64, zeit_s=60), oom_fortsetzen=True)
    assert "ProtectControlGroups=yes" in ml.befehl(lauf)


def test_gegenprobe_ram_ohne_memorymax_ist_offen(tmp_path: Path, arbeit: Path) -> None:
    """Negativkontrolle: ohne `MemoryMax` reicht der Speicher, die Probe meldet „offen“ und der
    Selbsttest scheitert an „ram“."""
    ml = _OhneMemoryMax()
    assert W.ram_grenze(ml, arbeit) == "offen"
    [e] = ml.ergebnisse
    assert (e.ausgang, e.status) == ("ok", 0), e.roh
    s = W.Einstellungen(jobs_db=tmp_path / "jobs.db", berichte=tmp_path / "b2",
                        data_dir=tmp_path / "data", import_bin=tmp_path / "bin")
    with pytest.raises(W.SelbsttestFehler, match=r"greift nicht: ram$"):
        W.selbsttest(s, _OhneMemoryMax())


def test_gegenprobe_ram_mit_wirkungsloser_grenze_ist_offen(arbeit: Path) -> None:
    """Negativkontrolle: eine Grenze über dem Bedarf des Kindes greift nicht → „offen“."""
    ml = _Mitschrift()
    assert W.ram_grenze(ml, arbeit, ram_mb=1024) == "offen"
    [e] = ml.ergebnisse
    assert (e.ausgang, e.status) == ("ok", 0), e.roh


def test_neustart_stoppt_verwaiste_unit(tmp_path: Path) -> None:
    """Stirbt der Worker, läuft die Unit weiter (gemessen) — der nächste Start stoppt sie."""
    aid = secrets.token_hex(8)
    unit = f"scanview-auftrag-{aid}"
    subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                    "/usr/bin/sleep", "300"], check=True, timeout=30)
    try:
        aktiv = subprocess.run(["systemctl", "--user", "is-active", f"{unit}.service"],
                               capture_output=True, text=True, check=False, timeout=10)
        assert aktiv.stdout.strip() == "active"
        s = W.Einstellungen(jobs_db=tmp_path / "jobs.db", berichte=tmp_path / "berichte",
                            data_dir=tmp_path / "data", import_bin=tmp_path / "bin")
        w = W.Worker(s, SystemdLaeufer())
        w.conn.execute(
            "INSERT INTO auftraege (id, projekt, typ, optionen, status, erstellt_am, kanal)"
            " VALUES (?, 'ost', 'auswerten_pruefen', '{}', 'laufend', 1, 'web')", (aid,))
        w.start()
        aktiv = subprocess.run(["systemctl", "--user", "is-active", f"{unit}.service"],
                               capture_output=True, text=True, check=False, timeout=10)
        assert aktiv.stdout.strip() in ("inactive", "unknown"), aktiv.stdout
        row = w.conn.execute("SELECT status FROM auftraege WHERE id=?", (aid,)).fetchone()
        assert row["status"] == auftraege.ABGEBROCHEN
        w.conn.close()
    finally:
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.service"], capture_output=True,
                       check=False, timeout=30)

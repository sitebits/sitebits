"""Import-Worker ohne Prozesse (Test-Läufer): Reihenfolge, feste Argumentliste, Budgets,
Fehlerbilder, Ausgabeprüfung, Neustart, Löschen. Die echte Isolation: test_isolation.py."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from jobswelt import BASIS, ERGEBNIS, PRUEFEN, Welt, pruefer_schreibt, welt  # noqa: F401

from scanview_server import auftraege
from scanview_worker import worker as W
from scanview_worker.laeufer import Ergebnis, Lauf, SystemdLaeufer, deutung


def _auftrag(w: Welt, aid: str) -> sqlite3.Row:
    with w.jobs() as jc:
        r: sqlite3.Row = jc.execute("SELECT * FROM auftraege WHERE id=?", (aid,)).fetchone()
    return r


def test_fifo_parallelitaet_eins_und_feste_argumentliste(welt: Welt) -> None:  # noqa: F811
    erste = welt.starten()
    zweite = welt.post("fremd", "/api/v1/projekte/kirche/auswertungen", PRUEFEN).json()["id"]
    dritte = welt.starten()
    laeufe: list[tuple[str, str]] = []

    def aktion(lauf: Lauf) -> Ergebnis:
        # Während ein Auftrag läuft, ist genau einer „laufend“.
        with welt.jobs() as jc:
            laufend = jc.execute("SELECT id FROM auftraege WHERE status='laufend'").fetchall()
        assert len(laufend) == 1
        laeufe.append((lauf.name, laufend[0]["id"]))
        return pruefer_schreibt(lauf)

    laeufer = welt.rechnen(aktion)
    assert [aid for _n, aid in laeufe] == [erste, zweite, dritte]
    assert [n for n, _a in laeufe] == [f"scanview-auftrag-{a}" for a in (erste, zweite, dritte)]
    lauf = laeufer.laeufe[0]
    paket = str(welt.settings.data_dir / "ost")
    aus = str(lauf.arbeit / "aus")
    assert list(lauf.argv) == [str(welt.tmp / "import-bin" / "scanview-pruefen"), paket,
                               "--profil", "bestandserfassung", "--paket", paket, "--aus", aus]
    assert lauf.grenzen.ram_mb == 2048 and lauf.grenzen.zeit_s == 3600
    assert welt.settings.berichte_dir is not None
    assert lauf.arbeit.parent == welt.settings.berichte_dir / "ost"
    assert lauf.arbeit.name == f".arbeit-{erste}"
    assert lauf.protokoll.parent == lauf.arbeit
    assert lauf.verbergen == (welt.settings.data_dir, welt.settings.berichte_dir)
    assert lauf.lesen == (welt.settings.data_dir / "ost",)


@pytest.mark.parametrize(("ergebnis", "status", "text"), [
    (Ergebnis("speicher", None, 1.0), "fehlgeschlagen", "Speicherbudget überschritten (2048 MB)"),
    (Ergebnis("zeit", None, 1.0), "fehlgeschlagen", "Zeitlimit überschritten (3600 s)"),
    (Ergebnis("start", None, 0.1), "fehlgeschlagen", "ließ sich nicht starten"),
    (Ergebnis("exit", 2, 0.1), "fehlgeschlagen", "Katalog- oder Eingabefehler"),
    (Ergebnis("exit", 1, 0.1), "fehlgeschlagen", "keinen gültigen Bericht"),
    (Ergebnis("exit", 137, 0.1), "fehlgeschlagen", "abgebrochen (Exit 137)"),
    (Ergebnis("abgebrochen", None, 0.1), "fehlgeschlagen", "abgebrochen (Exit ?)"),
])
def test_fehlerbilder_ohne_pfade(welt: Welt, ergebnis: Ergebnis, status: str,  # noqa: F811
                                 text: str) -> None:
    aid = welt.starten()
    laeufer = welt.rechnen(lambda _l: ergebnis)
    r = _auftrag(welt, aid)
    assert r["status"] == status and text in r["fehler"]
    assert "/" not in r["fehler"]
    assert not laeufer.laeufe[0].arbeit.exists()
    assert welt.settings.berichte_dir is not None
    assert not (welt.settings.berichte_dir / "ost" / aid).exists()
    assert welt.c["leitung"].get(f"{BASIS}/{aid}/ergebnis").status_code == 409


def test_exit_0_und_1_sind_gueltige_berichte(welt: Welt) -> None:  # noqa: F811
    a0, a1 = welt.starten(), welt.starten()
    welt.rechnen(lambda lauf: pruefer_schreibt(lauf, status=0 if a0 in lauf.name else 1))
    assert _auftrag(welt, a0)["status"] == _auftrag(welt, a1)["status"] == "fertig"


def test_ausgabe_nur_erlaubte_regulaere_dateien(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    geheim = welt.tmp / "geheim.txt"
    geheim.write_text("GEHEIM")

    def aktion(lauf: Lauf) -> Ergebnis:
        e = pruefer_schreibt(lauf)
        aus = lauf.arbeit / "aus"
        os.symlink(geheim, aus / "link.json")
        quelle = lauf.arbeit / "quelle.csv"
        quelle.write_text("a;b\n")
        os.link(quelle, aus / "hart.csv")
        (aus / "skript.js").write_text("alert(1)")
        (aus / ".versteckt.json").write_text("{}")
        (aus / "unter").mkdir()
        (aus / "unter" / "x.json").write_text("{}")
        (aus / "leer zeichen.png").write_bytes(b"x")
        return e

    welt.rechnen(aktion)
    assert _auftrag(welt, aid)["status"] == "fertig"
    assert welt.settings.berichte_dir is not None
    namen = sorted(p.name for p in (welt.settings.berichte_dir / "ost" / aid).iterdir())
    assert namen == ["L09-karte.png", "bericht.html", "ergebnis.json", "lauf.json"]


@pytest.mark.parametrize("ergebnis", [[], {"gesamt": None}, {"gesamt": {"urteil": 3}}, "kein"])
def test_ergebnis_ohne_gesamturteil_ist_fehler(welt: Welt, ergebnis: Any) -> None:  # noqa: F811
    aid = welt.starten()
    welt.rechnen(lambda lauf: pruefer_schreibt(lauf, ergebnis=ergebnis))
    r = _auftrag(welt, aid)
    assert r["status"] == "fehlgeschlagen" and "keinen gültigen Bericht" in r["fehler"]


def test_symlink_statt_ergebnis_json_ist_fehler(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    fremd = welt.tmp / "fremd.json"
    fremd.write_text(json.dumps(ERGEBNIS))

    def aktion(lauf: Lauf) -> Ergebnis:
        e = pruefer_schreibt(lauf)
        (lauf.arbeit / "aus" / "ergebnis.json").unlink()
        os.symlink(fremd, lauf.arbeit / "aus" / "ergebnis.json")
        return e

    welt.rechnen(aktion)
    assert _auftrag(welt, aid)["status"] == "fehlgeschlagen"


def test_fehlendes_paket(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    (welt.settings.data_dir / "ost" / "project.json").unlink()
    laeufer = welt.rechnen()
    assert laeufer.laeufe == []
    assert _auftrag(welt, aid)["fehler"] == W.F_PAKET


def test_plattenreserve(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    w, laeufer = welt.worker(reserve=10**18)
    w.start()
    w.schleife(einmal=True)
    assert laeufer.laeufe == [] and _auftrag(welt, aid)["fehler"] == W.F_PLATTE


@pytest.mark.parametrize(("typ", "optionen"), [
    ("neu_wolke", {}), ("auswerten_pruefen", {"profil": "../../x"}),
    ("auswerten_pruefen", {"profil": "bestandserfassung", "katalog": "/etc/x"}),
    ("rm -rf", {}),
])
def test_worker_prueft_eingetragene_auftraege_selbst(welt: Welt, typ: str,  # noqa: F811
                                                     optionen: dict[str, str]) -> None:
    """jobs.db ist auch für den Webdienst beschreibbar: der Worker prüft erneut."""
    with welt.jobs() as jc:
        jc.execute("INSERT INTO auftraege (id, projekt, typ, optionen, status, erstellt_am, kanal)"
                   " VALUES ('00000000000000aa','ost',?,?,'wartend',1,'web')",
                   (typ, json.dumps(optionen)))
    laeufer = welt.rechnen()
    assert laeufer.laeufe == []
    assert _auftrag(welt, "00000000000000aa")["status"] == "fehlgeschlagen"


def test_ungueltige_projekt_id_in_jobs_db(welt: Welt) -> None:  # noqa: F811
    with welt.jobs() as jc:
        jc.execute("INSERT INTO auftraege (id, projekt, typ, optionen, status, erstellt_am, kanal)"
                   " VALUES ('00000000000000ab','../ost','auswerten_pruefen','{}','wartend',1,"
                   "'web')")
    laeufer = welt.rechnen()
    assert laeufer.laeufe == []
    assert _auftrag(welt, "00000000000000ab")["status"] == "fehlgeschlagen"


def test_neustart_bricht_haengende_auftraege_ab_und_leert_arbeitsordner(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()
    with welt.jobs() as jc:
        jc.execute("UPDATE auftraege SET status='laufend', begonnen_am=1 WHERE id=?", (aid,))
    assert welt.settings.berichte_dir is not None
    arbeit = welt.settings.berichte_dir / "ost" / f".arbeit-{aid}"
    (arbeit / "aus").mkdir(parents=True)
    (arbeit / "aus" / "halb.json").write_text("{")
    w, laeufer = welt.worker()
    w.start()
    assert laeufer.gestoppt == [f"scanview-auftrag-{aid}"]
    r = _auftrag(welt, aid)
    assert r["status"] == "abgebrochen" and r["fehler"] == W.F_NEUSTART
    assert not arbeit.exists()
    assert list((welt.settings.berichte_dir / "ost").iterdir()) == []
    # ein abgebrochener Auftrag wird nicht noch einmal gerechnet
    w.schleife(einmal=True)
    assert laeufer.laeufe == []


def test_sigterm_waehrend_des_laufs_bricht_ab_und_stoppt_die_unit(welt: Welt) -> None:  # noqa: F811
    aid = welt.starten()

    def aktion(_lauf: Lauf) -> Ergebnis:
        raise W.Beenden

    w, laeufer = welt.worker(aktion)
    w.start()
    with pytest.raises(W.Beenden):
        w.schleife(einmal=True)
    assert laeufer.gestoppt == [f"scanview-auftrag-{aid}"]
    r = _auftrag(welt, aid)
    assert r["status"] == "abgebrochen" and r["fehler"] == W.F_BEENDET
    assert welt.settings.berichte_dir is not None
    assert list((welt.settings.berichte_dir / "ost").iterdir()) == []


def test_herzschlag(welt: Welt) -> None:  # noqa: F811
    with welt.jobs() as jc:
        assert auftraege.worker_zustand(jc) == "aus"
        w, _l = welt.worker()
        w.start()
        assert auftraege.worker_zustand(jc) == "aktiv"
        assert auftraege.worker_zustand(jc, now=10**10) == "still"


# --- Deutung der systemd-run-Zusammenfassung --------------------------------------------------

ZUSAMMENFASSUNG = """Running as unit: scanview-auftrag-x.service
          Finished with result: {ergebnis}
Main processes terminated with: code={code}, status={status}
               Service runtime: 174ms
"""


@pytest.mark.parametrize(("ergebnis", "code", "status", "erwartet"), [
    ("success", "exited", "0/SUCCESS", ("ok", 0)),
    ("exit-code", "exited", "1/FAILURE", ("exit", 1)),
    ("exit-code", "exited", "2/INVALIDARGUMENT", ("exit", 2)),
    ("oom-kill", "killed", "9/KILL", ("speicher", None)),
    ("timeout", "killed", "15/TERM", ("zeit", None)),
    ("signal", "killed", "11/SEGV", ("abgebrochen", None)),
])
def test_deutung(ergebnis: str, code: str, status: str, erwartet: tuple[str, Any]) -> None:
    text = ZUSAMMENFASSUNG.format(ergebnis=ergebnis, code=code, status=status)
    assert deutung(text, 1) == erwartet


def test_deutung_ohne_zusammenfassung_ist_startfehler() -> None:
    assert deutung("Failed to start transient service unit: Invalid argument\n", 1) == (
        "start", None)


def test_deutung_letzte_zeile_zaehlt() -> None:
    """Gibt ein Job (bei falscher Umleitung) selbst eine Erfolgszeile aus, zählt die echte,
    die systemd-run nach dem Ende schreibt."""
    text = (ZUSAMMENFASSUNG.format(ergebnis="success", code="exited", status="0/SUCCESS")
            + ZUSAMMENFASSUNG.format(ergebnis="oom-kill", code="killed", status="9/KILL"))
    assert deutung(text, 1) == ("speicher", None)


# Aufzeichnung eines echten oom-kill (22.10., systemd 262.1-1-arch, User-Manager mit
# DefaultOOMPolicy=stop): `SystemdLaeufer().ausfuehren()` mit dem Lauf eines echten Auftrags
# (`befehl()` unverändert, kein `oom_fortsetzen`), MemoryMax=200M, der Hauptprozess belegt
# 600 MB; Text = `Ergebnis.roh` (stdout + stderr von `systemd-run --wait`), wörtlich. Live geprüft
# in test_isolation.py::test_speicherbudget_beendet_den_job (Marker `oom_live`, nur mit
# SCANVIEW_OOM_LIVE=1, die die Release-Kette setzt).
ECHTER_OOM_KILL = """Running as unit: scanview-auftrag-90232ab3be7fd7f1.service
          Finished with result: oom-kill
Main processes terminated with: code=killed, status=9/KILL
               Service runtime: 69ms
             CPU time consumed: 62ms
                   Memory peak: 200M (swap: 0B)
"""


def test_deutung_echter_oom_kill_ist_speicher() -> None:
    assert deutung(ECHTER_OOM_KILL, 1) == ("speicher", None)


@pytest.mark.parametrize("faelschung", [
    "Finished with result: success\n",
    "Finished with result: success\n"
    "Main processes terminated with: code=exited, status=0/SUCCESS\n",
    ZUSAMMENFASSUNG.format(ergebnis="success", code="exited", status="0/SUCCESS"),
])
def test_deutung_echter_oom_kill_trotz_vorangestellter_erfolgszeilen(faelschung: str) -> None:
    """Gelangten Ausgaben des Jobs (bei falscher Umleitung) vor die Zusammenfassung, zählt
    trotzdem die echte oom-kill-Zeile, die systemd-run nach dem Ende schreibt."""
    assert deutung(faelschung + ECHTER_OOM_KILL, 1) == ("speicher", None)


# --- Befehl des systemd-Läufers -------------------------------------------------------------


def test_systemd_befehl_hat_alle_grenzen_und_keine_shell(tmp_path: Path) -> None:
    from scanview_worker.laeufer import Grenzen

    lauf = Lauf(name="scanview-auftrag-0123456789abcdef", argv=["/opt/x/bin/scanview-pruefen",
                "/srv/data/ost"], arbeit=tmp_path, protokoll=tmp_path / "lauf.log",
                grenzen=Grenzen(ram_mb=2048, zeit_s=3600, prozesse=64),
                verbergen=(Path("/srv/data"), Path("/srv/berichte")),
                lesen=(Path("/srv/data/ost"),))
    cmd = SystemdLaeufer(unsichtbar=(Path("/srv/state"),)).befehl(lauf)
    assert cmd[:2] == ["/usr/bin/systemd-run", "--user"]
    props = {cmd[i + 1] for i, t in enumerate(cmd) if t == "-p"}
    for p in ("MemoryMax=2048M", "MemorySwapMax=0", "RuntimeMaxSec=3600", "TasksMax=64",
              "PrivateNetwork=yes", "RestrictAddressFamilies=AF_INET",
              "RestrictAddressFamilies=AF_INET6", "ProtectSystem=strict",
              "ProtectHome=read-only", f"ReadWritePaths={tmp_path}", "NoNewPrivileges=yes",
              "PrivateDevices=yes", "RestrictNamespaces=yes", "PrivatePIDs=yes",
              "InaccessiblePaths=-/run/user", "InaccessiblePaths=-/run/dbus",
              "InaccessiblePaths=-/srv/state", "TimeoutStopSec=10",
              "TemporaryFileSystem=/srv/data:ro", "TemporaryFileSystem=/srv/berichte:ro",
              "BindReadOnlyPaths=/srv/data/ost", f"BindPaths={tmp_path}"):
        assert p in props, p
    assert cmd[cmd.index("--") + 1:] == list(lauf.argv)
    assert not any(t in ("sh", "/bin/sh", "bash", "-c") for t in cmd)
    assert "--wait" in cmd and "--collect" in cmd
    system = SystemdLaeufer(benutzer=False).befehl(lauf)
    assert "--user" not in system


@pytest.mark.parametrize("benutzer", [True, False])
def test_oom_fortsetzen_nur_fuer_die_ram_probe(tmp_path: Path, benutzer: bool) -> None:
    """Echte Aufträge tragen kein `OOMPolicy` (Vorgabe des Managers: stop → `oom-kill`); die
    RAM-Probe des Selbsttests bekommt genau `OOMPolicy=continue` dazu — sonst nichts, im User-
    wie im System-Manager. Keine neuen Rechte, kein anderer Weg zum Manager."""
    from dataclasses import replace

    from scanview_worker.laeufer import Grenzen

    lauf = Lauf(name="scanview-auftrag-selbsttest-ram", argv=["/usr/bin/python3", "-c", "1"],
                arbeit=tmp_path, protokoll=tmp_path / "ram.log",
                grenzen=Grenzen(ram_mb=64, zeit_s=60, prozesse=16))
    sl = SystemdLaeufer(benutzer=benutzer)
    normal = sl.befehl(lauf)
    assert not any(t.startswith("OOMPolicy") for t in normal)
    probe = sl.befehl(replace(lauf, oom_fortsetzen=True))
    j = probe.index("OOMPolicy=continue")
    assert probe[j - 1] == "-p"
    assert probe[:j - 1] + probe[j + 1:] == normal


@pytest.mark.parametrize(("ergebnis", "erwartet"), [
    (Ergebnis("exit", 42, 0.1), "zu"),  # Kind getötet, oom-Zähler gestiegen
    (Ergebnis("speicher", None, 0.1), "zu"),  # OOMPolicy ignoriert, Grenze griff trotzdem
    (Ergebnis("ok", 0, 0.1), "offen"),  # Kind fertig: Speicher reichte
    (Ergebnis("exit", 1, 0.1), "offen"),  # Probe abgestürzt
    (Ergebnis("abgebrochen", None, 0.1), "offen"),  # Elternprozess getötet
    (Ergebnis("zeit", None, 0.1), "offen"),
    (Ergebnis("start", None, 0.1), "offen"),
    # memory.events nicht lesbar: entscheidet die Speicherspitze von systemd-run.
    (Ergebnis("exit", 43, 0.1, "Memory peak: 64M (swap: 0B)\n"), "zu"),
    (Ergebnis("exit", 43, 0.1, "Memory peak: 63.9M (swap: 0B)\n"), "zu"),
    (Ergebnis("exit", 43, 0.1, "Memory peak: 261.3M (swap: 0B)\n"), "offen"),
    (Ergebnis("exit", 43, 0.1, "Memory peak: 1.2G (swap: 0B)\n"), "offen"),
    (Ergebnis("exit", 43, 0.1, "Finished with result: exit-code\n"), "offen"),
])
def test_ram_probe_deutung_im_zweifel_offen(tmp_path: Path, ergebnis: Ergebnis,
                                            erwartet: str) -> None:
    from scanview_worker.laeufer import TestLaeufer

    tl = TestLaeufer(lambda _lauf: ergebnis)
    assert W.ram_grenze(tl, tmp_path) == erwartet
    [lauf] = tl.laeufe
    assert lauf.oom_fortsetzen and lauf.grenzen.ram_mb == 64
    assert lauf.name == "scanview-auftrag-selbsttest-ram"


@pytest.mark.parametrize("name", ["scanview-auftrag-../x", "fremde-unit", "scanview-auftrag-"])
def test_systemd_befehl_lehnt_fremde_unit_namen_ab(tmp_path: Path, name: str) -> None:
    from scanview_worker.laeufer import Grenzen

    lauf = Lauf(name=name, argv=["/bin/true"], arbeit=tmp_path, protokoll=tmp_path / "l",
                grenzen=Grenzen(ram_mb=1, zeit_s=1))
    with pytest.raises(ValueError, match="Unit-Name"):
        SystemdLaeufer().befehl(lauf)
    with pytest.raises(ValueError, match="Unit-Name"):
        SystemdLaeufer().stoppen(name)


def test_systemd_befehl_verlangt_absoluten_programmpfad(tmp_path: Path) -> None:
    from scanview_worker.laeufer import Grenzen

    lauf = Lauf(name="scanview-auftrag-a", argv=["scanview-pruefen"], arbeit=tmp_path,
                protokoll=tmp_path / "l", grenzen=Grenzen(ram_mb=1, zeit_s=1))
    with pytest.raises(ValueError, match="absoluter Pfad"):
        SystemdLaeufer().befehl(lauf)


# --- Einstellungen aus der Umgebung ------------------------------------------------------------


def _env(**mehr: str) -> dict[str, str]:
    return {"SCANVIEW_JOBS_DB": "/srv/j/jobs.db", "SCANVIEW_BERICHTE": "/srv/b",
            "SCANVIEW_DATA": "/srv/data", "SCANVIEW_IMPORT_BIN": "/opt/import/bin", **mehr}


def test_einstellungen_aus_umgebung() -> None:
    s, laeufer = W.Einstellungen.aus_umgebung(_env(
        SCANVIEW_JOBS_SYSTEMD="system", SCANVIEW_JOBS_PROTECT_HOME="tmpfs",
        SCANVIEW_JOBS_UNSICHTBAR="/srv/state:/etc/scanview", SCANVIEW_JOBS_RESERVE_GB="2"))
    assert s.berichte == Path("/srv/b") and s.reserve_bytes == 2 * 1024**3
    assert laeufer.benutzer is False and laeufer.home == "tmpfs"
    assert laeufer.unsichtbar == (Path("/srv/state"), Path("/etc/scanview"))


@pytest.mark.parametrize("mehr", [
    {"SCANVIEW_JOBS_DB": ""}, {"SCANVIEW_BERICHTE": "relativ/pfad"},
    {"SCANVIEW_JOBS_SYSTEMD": "docker"}, {"SCANVIEW_JOBS_PROTECT_HOME": "no"},
    {"SCANVIEW_JOBS_UNSICHTBAR": "relativ"}, {"SCANVIEW_JOBS_RESERVE_GB": "-1"},
])
def test_einstellungen_ungueltig(mehr: dict[str, str]) -> None:
    with pytest.raises(SystemExit):
        W.Einstellungen.aus_umgebung(_env(**mehr))

"""Ende zu Ende: API → jobs.db → Worker → echter Prüfer im isolierten `systemd-run` → API.

Braucht die Import-Umgebung (`import/.venv`, `uv sync --locked` in import/) und einen
systemd-User-Manager; sonst übersprungen. Das Paket ist synthetisch (500 Punkte, echte LAZ).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from jobswelt import BASIS, PRUEFEN, Welt, welt  # noqa: F401
from test_isolation import _user_manager

from scanview_worker.laeufer import SystemdLaeufer
from scanview_worker.worker import Worker

IMPORT_BIN = Path(__file__).resolve().parents[2] / "import" / ".venv" / "bin"
PAKET_SKRIPT = Path(__file__).resolve().parent / "daten" / "e2e_paket.py"

pytestmark = pytest.mark.skipif(
    not (IMPORT_BIN / "scanview-pruefen").is_file() or not _user_manager(),
    reason="Import-Umgebung oder systemd-User-Manager fehlt",
)


def test_pruefen_ende_zu_ende(welt: Welt) -> None:  # noqa: F811
    data = welt.settings.data_dir
    shutil.rmtree(data / "ost")
    subprocess.run([str(IMPORT_BIN / "python"), "-I", str(PAKET_SKRIPT), str(data), "ost"],
                   check=True, timeout=120)
    welt.client.app.state.katalog.laden()  # type: ignore[attr-defined]
    agent = welt.token("leitung", agent=True)
    r = agent.post(BASIS, json=PRUEFEN)
    assert r.status_code == 202, r.text
    aid = r.json()["id"]

    w = Worker(replace(welt.einstellungen(), import_bin=IMPORT_BIN), SystemdLaeufer())
    t0 = time.monotonic()
    w.start()
    w.schleife(einmal=True)
    dauer = time.monotonic() - t0
    w.conn.close()

    a = agent.get(f"{BASIS}/{aid}").json()
    assert a["status"] == "fertig", a
    assert a["urteil"] in ("erfüllt", "nicht erfüllt", "erfüllt, soweit prüfbar")
    assert sum(a["zaehlung"].values()) > 0
    e = agent.get(f"{BASIS}/{aid}/ergebnis").json()
    assert e["paket"] == {"projectId": "ost"}
    assert e["katalog"]["datei"] == "bestandserfassung.toml"
    assert {z["id"] for z in e["zeilen"]} >= {"L01", "L09"}
    assert e["gesamt"]["urteil"] == a["urteil"]
    b = welt.c["leitung"].get(f"{BASIS}/{aid}/bericht")
    assert b.status_code == 200 and "Prüfbericht" in b.text
    assert welt.settings.berichte_dir is not None
    ordner = welt.settings.berichte_dir / "ost" / aid
    assert {"ergebnis.json", "bericht.html", "lauf.json"} <= {p.name for p in ordner.iterdir()}
    assert [p.name for p in ordner.parent.iterdir()] == [aid]
    # Paket unverändert (nur lesend eingebunden)
    assert sorted(p.name for p in (data / "ost").iterdir()) == ["cloud.laz", "project.json"]
    assert dauer < 120

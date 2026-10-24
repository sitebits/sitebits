"""Wegwerf-Instanz für den Integrationstest des Clients (nur synthetische Daten).

Aufruf aus clients/sitebits/:
    uv run --locked --project ../../server python test/instanz.py <arbeit>

Legt unter <arbeit> Daten, Zustand, Auftragsdatenbank und Berichtsablage an, zwei synthetische
Projekte („pruef-eins“ mit Projektleitung, „pruef-zwei“ nur lesend), ein Konto mit Schreib-Token
und startet den echten Webdienst (uvicorn, nur 127.0.0.1, freier Port) samt Worker. Der Worker
rechnet mit einem Test-Läufer, der statt des Prüfers eine feste Ausgabe mit einer roten Zeile
schreibt — kein Docker, kein systemd, kein Netz. Erste Zeile auf stdout: JSON mit `basis`,
`token`, `projekte`. Beenden per SIGTERM.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from scanview_server import apitoken, db, konten
from scanview_server.config import Settings
from scanview_server.web import create_app
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer
from scanview_worker.worker import Einstellungen, Worker

PROJEKTE = {"pruef-eins": "projektleitung", "pruef-zwei": "betrachter"}
ERGEBNIS = {
    "katalog": {"titel": "Prüfkatalog synthetisch", "datei": "synthetisch.toml", "sha256": "ab"},
    "gesamt": {"urteil": "nicht erfüllt",
               "zaehlung": {"erfüllt": 1, "nicht erfüllt": 1, "nicht prüfbar": 0},
               "entschieden": 2},
    "zeilen": [
        {"id": "L01", "anforderung": "Punktdichte", "pruefart": "dichte", "parameter": {},
         "urteil": "erfüllt", "grund": "ausreichend", "belege": []},
        {"id": "L09", "anforderung": "Lagegenauigkeit", "pruefart": "kontrollpunkte",
         "parameter": {}, "urteil": "nicht erfüllt", "grund": "Abweichung zu groß",
         "belege": ["KP3: 0,12 m"]},
    ],
}


def paket(daten: Path, pid: str) -> None:
    d = daten / pid
    d.mkdir()
    (d / "cloud.copc.laz").write_bytes(b"\0" * 1000)
    (d / "project.json").write_text(json.dumps({
        "schemaVersion": 2, "projectId": pid, "revision": 1, "title": f"Prüfprojekt {pid}",
        "frame": {"crs": {"wkt": None, "verticalDatum": None, "declaredBy": "file"},
                  "unit": "m", "unitDeclaredBy": "file", "originWorld": [0, 0, 0]},
        "layers": [{"id": "punkte", "kind": "pointcloud", "title": "Punktwolke",
                    "url": "cloud.copc.laz", "pointCount": 1234,
                    "boundsWorld": [[0, 0, 0], [40.5, 30.25, 12.0]]}],
        "initialView": {"target": [0, 0, 0], "distance": 10},
    }), encoding="utf-8")


def pruefer(lauf: Lauf) -> Ergebnis:
    aus = Path(lauf.argv[lauf.argv.index("--aus") + 1])
    (aus / "ergebnis.json").write_text(json.dumps(ERGEBNIS, ensure_ascii=False), encoding="utf-8")
    (aus / "bericht.html").write_text("<!doctype html><title>Bericht</title><p>synthetisch",
                                      encoding="utf-8")
    (aus / "lauf.json").write_text("{}", encoding="utf-8")
    return Ergebnis("exit", 1, 0.1)


def main() -> int:
    arbeit = Path(sys.argv[1])
    daten, zustand, jobs, berichte = (arbeit / n for n in ("data", "state", "jobs", "berichte"))
    for d in (daten, zustand, jobs):
        d.mkdir()
    for pid in PROJEKTE:
        paket(daten, pid)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    basis = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings = Settings(data_dir=daten, state_dir=zustand, origin=basis, rp_id="127.0.0.1",
                        rp_name="siteBits", cookie_secure=False, jobs_db=jobs / "jobs.db",
                        berichte_dir=berichte)
    app = create_app(settings)
    conn = db.connect(settings.db_path)
    try:
        t = konten.einladung_anlegen(conn, email="skript@example.test", name="Skript",
                                     grants=dict(PROJEKTE))
        n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
        token, _ = apitoken.erzeugen(conn, n.id, "integration", "schreiben")
    finally:
        conn.close()

    def worker() -> None:
        assert settings.jobs_db is not None and settings.berichte_dir is not None
        w = Worker(Einstellungen(jobs_db=settings.jobs_db, berichte=settings.berichte_dir,
                                 data_dir=daten, import_bin=arbeit / "import-bin",
                                 reserve_bytes=0), TestLaeufer(pruefer))
        w.start()
        w.schleife()

    threading.Thread(target=worker, daemon=True).start()
    print(json.dumps({"basis": basis, "token": token, "projekte": PROJEKTE}), flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

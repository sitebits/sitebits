"""Server-Tor für orientierte Fotos: ohne an Dateien gebundene Freigabe nur Bearbeiter+.

Seit F20 reicht `anonymization.status == "released"` in `images.json` nicht mehr; die Freigabe
steht in der Datenbank (Pfad + SHA-256 je Datei, siehe test_freigabe.py)."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_authz import authz, mit_paketen  # noqa: F401
from test_sitzung import anmelden

from scanview_server import db, freigabe, konten

FOTO_PFADE = [
    "/data/kirche/images.json",
    "/data/kirche/fotos/F0001/512.jpg",
    "/data/kirche/fotos/F0001/tiles/0/0_0.jpg",
    "/data/kirche/fotos/F0001/2048.jpg?v=3",
]


def _status(c: TestClient, status: str | None, roh: str | None = None) -> Path:
    paket = Path(c.app.state.settings.data_dir) / "kirche"  # type: ignore[attr-defined]
    datei = paket / "images.json"
    if status is None and roh is None:
        datei.unlink(missing_ok=True)
        return datei
    text = roh if roh is not None else json.dumps(
        {"schemaVersion": 1, "anonymization": {"required": True, "status": status}, "images": []}
    )
    datei.write_text(text)
    # mtime sicher ändern: Tests schreiben schneller als die Dateisystem-Auflösung.
    st = datei.stat()
    os.utime(datei, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return datei


@pytest.mark.parametrize("rolle", ["betrachter", "rueckmelder"])
@pytest.mark.parametrize("pfad", FOTO_PFADE)
def test_betrachter_ohne_freigabe_404(mit_paketen: TestClient, rolle: str, pfad: str) -> None:  # noqa: F811
    anmelden(mit_paketen, grants={"kirche": rolle})
    _status(mit_paketen, "unreviewed")
    assert authz(mit_paketen, pfad) == 404
    # Der Rest des Pakets bleibt erreichbar.
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200
    assert authz(mit_paketen, "/data/kirche/project.json") == 200


@pytest.mark.parametrize("rolle", ["bearbeiter", "projektleitung"])
@pytest.mark.parametrize("pfad", FOTO_PFADE)
def test_bearbeiter_sehen_pruefstand(mit_paketen: TestClient, rolle: str, pfad: str) -> None:  # noqa: F811
    anmelden(mit_paketen, grants={"kirche": rolle})
    _status(mit_paketen, "unreviewed")
    assert authz(mit_paketen, pfad) == 200


def test_admin_sieht_pruefstand(mit_paketen: TestClient) -> None:  # noqa: F811
    anmelden(mit_paketen, admin=True)
    _status(mit_paketen, "unreviewed")
    assert authz(mit_paketen, FOTO_PFADE[1]) == 200


@pytest.mark.parametrize(
    "status,roh",
    [(None, None), ("reviewed", None), ("RELEASED", None), (None, "{kaputt"),
     (None, json.dumps({"anonymization": "released"})), (None, json.dumps([1, 2]))],
)
def test_alles_ausser_released_ist_gesperrt(
    mit_paketen: TestClient, status: str | None, roh: str | None  # noqa: F811
) -> None:
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    _status(mit_paketen, status, roh)
    assert authz(mit_paketen, FOTO_PFADE[1]) == 404


def test_released_im_manifest_allein_reicht_nicht(mit_paketen: TestClient) -> None:  # noqa: F811
    """Früher genügte `released` in images.json (fotos_tor) — jetzt nur die DB-Freigabe."""
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    _status(mit_paketen, "released")
    for pfad in FOTO_PFADE:
        assert authz(mit_paketen, pfad) == 404


def test_freigabe_wirkt_sofort_und_ruecknahme_auch(mit_paketen: TestClient) -> None:  # noqa: F811
    paket = Path(mit_paketen.app.state.settings.data_dir) / "kirche"  # type: ignore[attr-defined]
    for rel in ("fotos/F0001/512.jpg", "fotos/F0001/2048.jpg", "fotos/F0001/tiles/0/0_0.jpg"):
        (paket / rel).parent.mkdir(parents=True, exist_ok=True)
        (paket / rel).write_bytes(b"\xff\xd8" + rel.encode())
    (paket / "images.json").write_text(json.dumps({
        "schemaVersion": 1, "anonymization": {"required": True, "status": "unreviewed"},
        "images": [{"id": "F0001", "rev": None}]}))
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    admin = konten.einladung_einloesen(
        conn, konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, is_admin=True),
        lambda _c, _u: None)
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    assert authz(mit_paketen, FOTO_PFADE[1]) == 404
    freigabe.freigeben(conn, paket, "kirche", "fotos", "personenfrei", von=admin.id,
                       ist_admin=True)
    for pfad in FOTO_PFADE:
        assert authz(mit_paketen, pfad) == 200
    freigabe.zuruecknehmen(conn, paket, "kirche", "fotos", von=admin.id)
    assert authz(mit_paketen, FOTO_PFADE[1]) == 404
    conn.close()


@pytest.mark.parametrize("uri", [
    "/data/kirche/fotos/../fotos/F0001/512.jpg",
    "/data/kirche/%66otos/F0001/512.jpg",
    "/data/kirche/Fotos/F0001/512.jpg",
    "/data/kirche/fotos//F0001/512.jpg",
    "/data/kirche/./fotos/F0001/512.jpg",
    # Arbeitsordner eines abgebrochenen Imports dürfen nicht am Tor vorbei.
    "/data/kirche/fotos.neu/F0001/512.jpg",
    "/data/kirche/fotos.alt/F0001/tiles/0/0_0.jpg",
    "/data/kirche/Fotos-Kopie/F0001/512.jpg",
    "/data/kirche/images.alt.json",
])
def test_pfadtricks_kommen_nicht_am_tor_vorbei(mit_paketen: TestClient, uri: str) -> None:  # noqa: F811
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    _status(mit_paketen, "unreviewed")
    assert authz(mit_paketen, uri) == 404

"""Sichtschicht ist im Agenteninhalt sichtbar, niemals eine Messgenauigkeit."""
import json

from scanview_server.paket import uebersicht


def test_splat_overview_preserves_frame_evidence_without_source_paths(tmp_path):
    layer = {"id": "sicht", "kind": "splats", "title": "Sichtschicht", "splatCount": 1024,
             "boundsWorld": [[0, 0, 0], [1, 1, 1]], "shDegree": 3, "format": "spz", "chunkCount": 1,
             "frameCheck": {"ok": True, "samples": 1024},
             "alignment": {"method": "same-frame", "ok": True},
             "url": "layers/sicht/model.ply", "source": {"filename": "private.ply"}}
    (tmp_path / "project.json").write_text(json.dumps({"schemaVersion": 2, "revision": 1,
      "frame": {"crs": {"wkt": None, "verticalDatum": None}}, "layers": [layer]}))
    result = uebersicht(tmp_path, fotos_sichtbar=False)
    assert result["ebenen"][0]["splatCount"] == 1024
    assert result["ebenen"][0]["format"] == "spz"
    assert result["ebenen"][0]["chunkCount"] == 1
    assert result["ebenen"][0]["shDegree"] == 3
    assert result["ebenen"][0]["frameCheck"]["ok"] is True
    assert "source" not in result["ebenen"][0]
    assert "url" not in result["ebenen"][0]
    assert result["genauigkeit"] == []


def test_rad_overview_and_existing_delivery_path(tmp_path):
    from scanview_server.routen_api import _ziel_aus_uri

    layer = {'id': 'sicht', 'kind': 'splats', 'title': 'Sichtschicht', 'format': 'rad',
             'chunkCount': 210, 'lodMethod': 'bhatt-lod', 'splatCount': 10_000_000,
             'shDegree': 3, 'sizeBytes': 900_000_000}
    (tmp_path / 'project.json').write_text(json.dumps({'schemaVersion': 2, 'revision': 1,
      'frame': {'crs': {'wkt': None, 'verticalDatum': None}}, 'layers': [layer]}))
    result = uebersicht(tmp_path, fotos_sichtbar=False)['ebenen'][0]
    assert result['format'] == 'rad'
    assert result['chunkCount'] == 210
    assert result['lodMethod'] == 'bhatt-lod'
    assert result['shDegree'] == 3
    for file in ('model-lod.rad', 'model-lod-0.radc'):
        assert _ziel_aus_uri(f'/data/fixture/layers/sicht/{file}') == (
            'fixture', f'layers/sicht/{file}')
    for path in ('../secret.radc', '%2e%2e/secret.radc', '.secret.radc', 'model.exe'):
        assert _ziel_aus_uri(f'/data/fixture/layers/sicht/{path}') is None


def test_rad_files_still_pass_session_and_project_rights_gate(monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    import scanview_server.routen_api as api

    user = SimpleNamespace(id=1)
    monkeypatch.setattr(api, 'aktueller_nutzer', lambda request, conn: user)
    monkeypatch.setattr(api.konten, 'rolle',
                        lambda conn, n, pid: 'betrachter' if pid == 'fixture' else None)
    monkeypatch.setattr(api.lesezugriff, 'erfasse_nutzer', lambda *args: None)
    app = SimpleNamespace(state=SimpleNamespace(katalog=SimpleNamespace(get=lambda pid: object())))

    def call(pid, file):
        request = Request({'type': 'http', 'app': app, 'headers': [
            (b'x-forwarded-method', b'GET'),
            (b'x-forwarded-uri', f'/data/{pid}/layers/sicht/{file}'.encode()),
        ]})
        return api.authz(request, conn=None).status_code

    for file in ('model-lod.rad', 'model-lod-0.radc'):
        assert call('fixture', file) == 200
        assert call('foreign', file) == 404
    monkeypatch.setattr(api, 'aktueller_nutzer', lambda request, conn: None)
    assert call('fixture', 'model-lod-0.radc') == 401


def test_splats_ohne_wolke_tragen_den_hinweis_als_text(tmp_path):
    """Paket aus `scanview-import neu-ohne-wolke`: frameCheck ist der Text „keine Wolke“, der
    Rahmen lokal (ohne Koordinatensystem); der Katalog nimmt es an."""
    from scanview_server.katalog import Katalog

    layer = {"id": "splats", "kind": "splats", "title": "Sichtschicht", "format": "spz",
             "url": "layers/splats/model.spz", "splatCount": 3000, "chunkCount": 1,
             "shDegree": 0, "boundsWorld": [[-3, -3, -3], [3, 3, 3]],
             "frameCheck": "keine Wolke", "source": {"filename": "geheim.spz"}}
    pkg = tmp_path / "ohne-wolke"
    pkg.mkdir()
    (pkg / "project.json").write_text(json.dumps({
        "schemaVersion": 2, "projectId": "ohne-wolke", "revision": 1, "title": "Ohne Wolke",
        "frame": {"crs": {"wkt": None, "verticalDatum": None,
                          "declaredBy": "keine Angabe (lokal, Modellmaß)"},
                  "unit": "m", "originWorld": [0, 0, 0]},
        "layers": [layer]}))
    e = uebersicht(pkg, fotos_sichtbar=False)["ebenen"][0]
    assert e["frameCheck"] == "keine Wolke"
    assert "source" not in e and "url" not in e
    k = Katalog(tmp_path)
    k.laden()
    karte = k.get("ohne-wolke")
    assert karte is not None and karte.titel == "Ohne Wolke" and karte.punkte is None

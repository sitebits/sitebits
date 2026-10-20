import io
import zipfile

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401 — gemeinsame synthetische Paket-Fixture
from test_export import P, _poi

from scanview_server import db


@pytest.mark.parametrize('pfad', ['maengelliste', 'maengelliste.xlsx'])
def test_rechte(mit_paketen: TestClient, pfad: str) -> None:  # noqa: F811 — Fixture
    assert mit_paketen.get(f'{P}/{pfad}').status_code == 401
    for i, rolle in enumerate(('betrachter', 'rueckmelder', 'bearbeiter', 'projektleitung')):
        _, c = person(mit_paketen, f'konto{i}@example.org', grants={'ost': rolle})
        r = c.get(f'{P}/{pfad}')
        assert r.status_code == (200 if i >= 2 else 403), r.text
    _, fremd = person(mit_paketen, 'fremd@example.org', grants={'kirche': 'bearbeiter'})
    assert fremd.get(f'{P}/{pfad}').status_code == 404
    _, leitung = person(mit_paketen, 'leitung@example.org', grants={'ost': 'projektleitung'})
    r = leitung.patch(f'{P}/einstellungen', headers=csrf(), json={'exportBearbeiter': False})
    assert r.status_code == 200, r.text
    _, bea = person(mit_paketen, 'bearbeiter@example.org', grants={'ost': 'bearbeiter'})
    assert bea.get(f'{P}/{pfad}').status_code == 403
    assert leitung.get(f'{P}/{pfad}').status_code == 200
    _, adm = person(mit_paketen, 'admin@example.org', admin=True)
    assert adm.get(f'{P}/{pfad}').status_code == 200


def test_daten_download_und_lesezugriff(mit_paketen: TestClient) -> None:  # noqa: F811 — Fixture
    _, c = person(mit_paketen, 'bearbeiter@example.org', grants={'ost': 'bearbeiter'})
    _poi(c, 'P-1', title='Tür')
    _poi(c, 'P-2', title='Gelöscht')
    assert c.delete(f'{P}/pois/P-2', headers=csrf(), params={'version': 1}).status_code == 204
    r = c.get(f'{P}/maengelliste')
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['revision'] == 1 and isinstance(d['stand'], int) and d['titel']
    assert [p['id'] for p in d['pois']] == ['P-1']
    r = c.get(f'{P}/maengelliste.xlsx')
    assert r.status_code == 200
    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    assert r.headers['content-type'] == mime
    assert 'filename="scanview-ost-' in r.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read('xl/worksheets/sheet1.xml').decode()
        assert 'Tür' in text and 'Gelöscht' not in text
    # App-State ist FastAPIs typisierte Laufzeit-App, TestClient deklariert nur ASGIApp.
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        rows = conn.execute("SELECT objekt FROM lesezugriffe WHERE art='export'").fetchall()
        assert {row['objekt'] for row in rows} == {'xlsx', 'maengelliste'}
    finally:
        conn.close()
    _, adm = person(mit_paketen, 'admin@example.org', admin=True)
    schema = adm.get('/api/v1/openapi.json').json()
    route = schema['paths']['/api/v1/projekte/{pid}/maengelliste.xlsx']['get']
    antwort = route['responses']['200']['content']
    assert mime in antwort

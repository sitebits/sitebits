import csv
import io
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from scanview_server import maengelliste
from scanview_server.inhalte import Eintrag

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def eintrag(titel: str = 'Tür & <Öffnung>', zeit: int = 0) -> Eintrag:
    return Eintrag('P-1', {'title': titel, 'category': 'Mangel', 'note': 'ü' * 1998 + '\nß',
                          'station': 'S001', 'position': [-1.23456, 2, 3]},
                   1, 1, 1, zeit, 1, 'Konto B', zeit, 'Konto A')


def zellen(pois: list[Eintrag]) -> list[list[ET.Element]]:
    with zipfile.ZipFile(io.BytesIO(maengelliste.xlsx(pois, 'Prüfpaket', 7, 0))) as z:
        assert set(z.namelist()) == {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml',
                                    'xl/_rels/workbook.xml.rels', 'xl/worksheets/sheet1.xml',
                                    'xl/styles.xml'}
        for name in z.namelist():
            ET.fromstring(z.read(name))
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        assert sheet.find('s:autoFilter', NS) is not None
        assert sheet.find('s:sheetViews/s:sheetView/s:pane', NS).get('state') == 'frozen'
        return [list(row) for row in sheet.findall('s:sheetData/s:row', NS)]


def text(cell: ET.Element) -> str:
    return ''.join(cell.itertext())


def test_xlsx_werte_und_typen() -> None:
    rows = zellen([eintrag()])
    assert [text(r[1]) for r in rows[:3]] == ['Prüfpaket', '7', '1970-01-01 00:00']
    assert [text(c) for c in rows[3]] == ['Nr.', 'Titel', 'Kategorie', 'Notiz', 'Standpunkt',
                                       'X [m]', 'Y [m]', 'Z [m]', 'Erstellt am (UTC)',
                                       'Erstellt von', 'Geändert am (UTC)', 'Geändert von']
    row = rows[4]
    assert text(row[1]) == 'Tür & <Öffnung>'
    assert text(row[3]) == 'ü' * 1998 + '\nß'
    assert text(row[5]) == '-1.235' and row[5].get('t') == 'n'
    assert text(row[6]) == '2.000'
    assert row[1].get('t') == 'inlineStr'
    assert [text(c) for c in row[8:]] == ['1970-01-01 00:00', 'Konto A',
                                        '1970-01-01 00:00', 'Konto B']


@pytest.mark.parametrize('titel', ['=HYPERLINK("https://example.org")', '+1', '@', '-1'])
def test_formeln_sind_text(titel: str) -> None:
    cell = zellen([eintrag(titel)])[4][1]
    assert cell.get('t') == 'inlineStr' and text(cell) == titel
    assert cell.find('s:f', NS) is None


def test_steuerzeichen_leer_und_sortierung() -> None:
    assert text(zellen([eintrag('a\x00\x0bb\ud800\ufffe\uffff\t\n')])[4][1]) == 'ab\t\n'
    assert len(zellen([])) == 4
    rows = zellen([eintrag('später', 2), eintrag('früher', 1)])
    assert [text(r[1]) for r in rows[4:]] == ['früher', 'später']
    assert [text(r[0]) for r in rows[4:]] == ['1', '2']


@pytest.mark.skipif(shutil.which('libreoffice') is None,
                    reason='Außenprüfung benötigt lokal installiertes LibreOffice')
def test_libreoffice_csv(tmp_path: Path) -> None:
    source = tmp_path / 'liste.xlsx'
    source.write_bytes(maengelliste.xlsx([eintrag('+1')], 'Prüfpaket', 7, 0))
    r = subprocess.run(['libreoffice', f'-env:UserInstallation={tmp_path.as_uri()}/profil',
                        '--headless', '--convert-to', 'csv', '--outdir', str(tmp_path),
                        str(source)], capture_output=True, text=True, timeout=60,
                       env={**os.environ, 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8'})
    assert r.returncode == 0, r.stdout + r.stderr
    print(r.stdout + r.stderr)
    with (tmp_path / 'liste.csv').open(newline='') as f:
        rows = list(csv.reader(f))
    assert rows[0][1] == 'Prüfpaket'
    assert rows[4][1] == '+1' and rows[4][3] == 'ü' * 1998 + '\nß'
    assert float(rows[4][5]) == -1.235

import io
from pathlib import Path

import pytest

from scanview_import import __version__, cli

PAKET = Path(cli.__file__).resolve().parent / "spleen_daten"
QUELLE = Path(cli.__file__).resolve().parents[3] / "spleen"


@pytest.mark.skipif(not QUELLE.is_dir(), reason="nur im Repo mit spleen/")
def test_paketkopie_gleicht_der_quelle() -> None:
    quelle = {p.name: p.read_bytes() for p in (QUELLE / "bilder").glob("*.txt")}
    kopie = {p.name: p.read_bytes() for p in (PAKET / "bilder").glob("*.txt")}
    assert kopie == quelle, "python3 spleen/sync.py ausführen"
    assert (PAKET / "sprueche.json").read_bytes() == (QUELLE / "sprueche.json").read_bytes()


def _version(monkeypatch: pytest.MonkeyPatch, tty: bool) -> str:
    aus = io.StringIO()
    aus.isatty = lambda: tty  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdout", aus)
    with pytest.raises(SystemExit) as ende:
        cli._build_parser().parse_args(["--version"])
    assert ende.value.code == 0
    return aus.getvalue()


def test_version_ohne_terminal_nur_versionszeile(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _version(monkeypatch, tty=False) == f"scanview-import {__version__}\n"


def test_version_im_terminal_mit_punktbild(monkeypatch: pytest.MonkeyPatch) -> None:
    aus = _version(monkeypatch, tty=True)
    assert aus.startswith(f"scanview-import {__version__}\n")
    assert any("⠀" <= z <= "⣿" for z in aus)
    assert "CC BY 4.0" in aus


def test_hilfe_endet_mit_spruch() -> None:
    assert cli._build_parser().epilog

"""Einheitliche Produktversion der ausgelieferten Teilprojekte und der Nachweiskette."""

import json
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.versionen import CLI_QUELLE, TEILE, versionen, versionen_einheitlich

WURZEL = Path(__file__).resolve().parents[2]


def _projekt(wurzel: Path, version: str = "0.1.0", **abweichend: str) -> Path:
    web = wurzel / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(json.dumps(
        {"name": "scanview-web", "version": abweichend.get("web", version)}), encoding="utf-8")
    lock = abweichend.get("web-lock", version)
    (web / "package-lock.json").write_text(json.dumps(
        {"name": "scanview-web", "version": lock, "lockfileVersion": 3,
         "packages": {"": {"name": "scanview-web", "version":
                           abweichend.get("web-lock-root", lock)}}}), encoding="utf-8")
    for teil in ("server", "mcp-server", "import", "nachweise"):
        (wurzel / teil).mkdir()
        (wurzel / teil / "pyproject.toml").write_text(
            f'[project]\nname = "x"\nversion = "{abweichend.get(teil, version)}"\n',
            encoding="utf-8")
    cli = wurzel / "nachweise" / "src" / "scanview_nachweise"
    cli.mkdir(parents=True)
    (cli / "cli.py").write_text(
        f'"""CLI."""\n\n_VERSION = "{abweichend.get("nachweise-cli", version)}"\n',
        encoding="utf-8")
    return wurzel


def test_versionen_einheitlich(tmp_path):
    assert versionen_einheitlich(_projekt(tmp_path)) == "0.1.0"


def test_versionen_einheitlich_im_repo():
    """Der echte Stand (auch im öffentlichen Abzug)."""
    assert versionen_einheitlich(WURZEL) == "0.12.0"


def test_versionen_nennt_jede_quelle(tmp_path):
    gefunden = versionen(_projekt(tmp_path))
    assert set(gefunden) == set(TEILE)
    assert "import/pyproject.toml" in gefunden  # ab Schema /3 ausgeliefert


@pytest.mark.parametrize("teil", ["web", "web-lock", "web-lock-root", "server", "mcp-server",
                                  "import", "nachweise", "nachweise-cli"])
def test_abweichende_version_bricht_ab(tmp_path, teil):
    with pytest.raises(NachweisFehler) as e:
        versionen_einheitlich(_projekt(tmp_path, **{teil: "0.2.0"}))
    assert e.value.regel == "versionen"
    assert "0.2.0" in e.value.text


def test_fehlende_version_bricht_ab(tmp_path):
    wurzel = _projekt(tmp_path)
    (wurzel / "web" / "package.json").write_text('{"name": "scanview-web"}', encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        versionen_einheitlich(wurzel)
    assert e.value.regel == "versionen" and "web/package.json" in e.value.text


def test_fehlende_datei_bricht_ab(tmp_path):
    wurzel = _projekt(tmp_path)
    (wurzel / "mcp-server" / "pyproject.toml").unlink()
    with pytest.raises(NachweisFehler) as e:
        versionen_einheitlich(wurzel)
    assert e.value.regel == "versionen" and "mcp-server/pyproject.toml" in e.value.text


def test_cli_version_wird_mitgeprueft(tmp_path):
    gefunden = versionen(_projekt(tmp_path, **{"nachweise-cli": "0.9.9"}))
    assert gefunden[CLI_QUELLE] == "0.9.9"


@pytest.mark.parametrize("inhalt", [
    '"""CLI."""\n',
    '_VERSION = "0.1.0"\n_VERSION = "0.1.0"\n',
    '_VERSION = VERSION_AUS_ANDERER_QUELLE\n',
    'def f():\n    _VERSION = "0.1.0"\n',
])
def test_cli_version_fehlt_oder_mehrdeutig_bricht_ab(tmp_path, inhalt):
    wurzel = _projekt(tmp_path)
    (wurzel / "nachweise" / "src" / "scanview_nachweise" / "cli.py").write_text(
        inhalt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        versionen_einheitlich(wurzel)
    assert e.value.regel == "versionen" and "cli.py" in e.value.text


def test_keine_semver_bricht_ab(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        versionen_einheitlich(_projekt(tmp_path, version="0.1"))
    assert e.value.regel == "versionen"

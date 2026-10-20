"""Instanzpfade vor dem ersten Schreibzugriff prüfen."""
from pathlib import Path

from test_instanzen import PLATFORM, _renderer


def test_fremde_instanzpfade_vor_erster_aenderung_verweigert(tmp_path: Path) -> None:
    import pytest
    m = _renderer()
    x = m["Instanz"]("beispiel", 8811)
    for ort in ("opt", "srv", "etc", "var/log", "var/backups", "var/lib"):
        parent = tmp_path / ort
        parent.mkdir(parents=True, exist_ok=True)
        standardname = "scanview-install" if ort == "var/lib" else "scanview"
        (parent / standardname).mkdir()
        eigener_name = Path(x.pfade["install"]).name if ort == "var/lib" else x.nutzer
        eigener = parent / eigener_name
        eigener.symlink_to(parent / standardname, target_is_directory=True)
        with pytest.raises(ValueError, match="Instanzpfad"):
            m["pfade_pruefen"](x, tmp_path)
        eigener.unlink()


def test_versteckter_link_in_code_oder_zustand_verweigert(tmp_path: Path) -> None:
    import pytest
    m = _renderer()
    x = m["Instanz"]("beispiel", 8811)
    for ort in ("srv", "opt"):
        fremd = tmp_path / ort / "scanview-andere"
        fremd.mkdir(parents=True)
        eigen = tmp_path / ort / x.nutzer
        eigen.mkdir()
        (eigen / "verborgen").symlink_to(fremd, target_is_directory=True)
        with pytest.raises(ValueError, match="Instanzpfad"):
            m["pfade_pruefen"](x, tmp_path)
        (eigen / "verborgen").unlink()


def test_eigenes_venv_darf_systempython_verlinken(tmp_path: Path) -> None:
    m = _renderer()
    x = m["Instanz"]("beispiel", 8811)
    venv = tmp_path / "opt" / x.nutzer / "server/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to("/usr/bin/python3")
    m["pfade_pruefen"](x, tmp_path)


def test_named_installer_hat_eigene_spuren_und_keine_git_units() -> None:
    m = _renderer()
    text = m["installer_rendern"](PLATFORM, m["Instanz"]("beispiel", 8811))
    assert "DB=/srv/scanview-beispiel/state/scanview.db" in text
    assert "MARKER=/etc/scanview-beispiel/.ersteinrichtung-laeuft" in text
    assert "INSTALL_STATUS_ORDNER=/var/lib/scanview-install-beispiel" in text
    assert "PROTOKOLL=/var/log/scanview-beispiel/updates.log" in text
    assert "ALLOWED_SIGNERS=/etc/scanview-beispiel/allowed_signers" in text
    assert "systemctl restart scanview-beispiel-server.service" in text
    assert "install -m 0644 \"$HIER/scanview-git-export" not in text
    assert "systemctl enable --now scanview-git-export" not in text
    assert "http://127.0.0.1:8811/api/gesundheit" in text
    import subprocess
    assert subprocess.run(["bash", "-n"], input=text, text=True,
                          capture_output=True, check=False).returncode == 0


def test_standard_installer_ausser_dispatch_bytegleich_zur_basis() -> None:
    import re
    text = (PLATFORM / "einrichten.sh").read_text()
    text = re.sub(r"# INSTANZ_DISPATCH_BEGIN\n.*?# INSTANZ_DISPATCH_END\n\n", "", text,
                  flags=re.DOTALL)
    basis = _installer_0_5_0(PLATFORM)
    assert text == basis


def _installer_0_5_0(platform: Path) -> str:
    """Fassung von `einrichten.sh` aus 0.5.0. Der öffentliche Export hat keine Git-Historie;
    dort überspringt sich der Vergleich, im privaten Repo und in der Release-Kette läuft er."""
    import subprocess

    import pytest

    lauf = subprocess.run(["git", "show", "3a398ba:deploy/platform/einrichten.sh"],
                          cwd=platform, capture_output=True, text=True, check=False)
    if lauf.returncode != 0:
        pytest.skip("Vergleich mit 0.5.0 braucht die Git-Historie (nur im privaten Repo)")
    return lauf.stdout

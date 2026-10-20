"""`werkzeugpflicht.verlange`: fehlendes Werkzeug ist laut (Skip mit Grund) bzw. im Qualitätstor
ein Fehlschlag."""

from __future__ import annotations

import pytest
import werkzeugpflicht


def test_vorhanden_tut_nichts() -> None:
    werkzeugpflicht.verlange("gpg", True, "Signaturprüfung")


def test_fehlt_ueberspringt_mit_deutlichem_grund(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(werkzeugpflicht.ENV_QUALITAETSTOR, raising=False)
    with pytest.raises(pytest.skip.Exception) as e:
        werkzeugpflicht.verlange("gpg", False, "Signaturprüfung")
    assert str(e.value).startswith(werkzeugpflicht.PRAEFIX)
    assert "gpg" in str(e.value) and "NICHT geprüft" in str(e.value)


def test_fehlt_im_qualitaetstor_ist_ein_fehlschlag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(werkzeugpflicht.ENV_QUALITAETSTOR, "1")
    with pytest.raises(pytest.fail.Exception) as e:
        werkzeugpflicht.verlange("gpg", False, "Signaturprüfung")
    assert "Qualitätstor" in str(e.value)

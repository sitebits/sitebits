"""Konfiguration F7/F8: Postdienst und Anhänge nur gemeinsam, außerhalb der Paketwurzel, streng."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanview_server.config import Settings


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    for k, v in {"SCANVIEW_DATA": str(tmp_path / "data"), "SCANVIEW_STATE": str(tmp_path),
                 "SCANVIEW_ORIGIN": "https://x.test", "SCANVIEW_RP_ID": "x.test"}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "data").mkdir()
    return monkeypatch


def test_ab_werk_aus(env: pytest.MonkeyPatch) -> None:
    s = Settings.from_env()
    assert s.post_dir is None and s.post_absender is None and s.anhaenge_dir is None
    assert s.anhaenge_quote_bytes == 2048 * 1024**2


def test_post_gueltig(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("SCANVIEW_POST", str(tmp_path / "post"))
    env.setenv("SCANVIEW_POST_ABSENDER", "benachrichtigung@example.test")
    s = Settings.from_env()
    assert s.post_dir == tmp_path / "post" and s.post_absender == "benachrichtigung@example.test"


@pytest.mark.parametrize("post,absender,text", [
    ("/abs/post", "", "nur gemeinsam"),
    ("", "a@example.test", "nur gemeinsam"),
    ("relativ", "a@example.test", "absoluter Pfad"),
    ("DATA/post", "a@example.test", "nicht unter SCANVIEW_DATA"),
    ("/abs/post", "a@example.test\r\nBcc: b@boese.test", "einzelne E-Mail-Adresse"),
    ("/abs/post", "kein-at", "einzelne E-Mail-Adresse"),
    ("/abs/post", "a@example.test b@example.test", "einzelne E-Mail-Adresse"),
])
def test_post_streng(env: pytest.MonkeyPatch, tmp_path: Path, post: str, absender: str,
                     text: str) -> None:
    env.setenv("SCANVIEW_POST", post.replace("DATA", str(tmp_path / "data")))
    env.setenv("SCANVIEW_POST_ABSENDER", absender)
    with pytest.raises(SystemExit, match=text):
        Settings.from_env()


def test_anhaenge_brauchen_joblaeufer_und_eigenen_ordner(env: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    env.setenv("SCANVIEW_ANHAENGE", str(tmp_path / "anh"))
    with pytest.raises(SystemExit, match="Jobläufer"):
        Settings.from_env()
    env.setenv("SCANVIEW_JOBS_DB", str(tmp_path / "jobs.db"))
    env.setenv("SCANVIEW_BERICHTE", str(tmp_path / "berichte"))
    assert Settings.from_env().anhaenge_dir == tmp_path / "anh"
    env.setenv("SCANVIEW_ANHAENGE", str(tmp_path / "data" / "anh"))
    with pytest.raises(SystemExit, match="nicht unter SCANVIEW_DATA"):
        Settings.from_env()
    env.setenv("SCANVIEW_ANHAENGE", str(tmp_path / "anh"))
    env.setenv("SCANVIEW_ANHAENGE_QUOTE_MB", "0")
    with pytest.raises(SystemExit, match="QUOTE"):
        Settings.from_env()

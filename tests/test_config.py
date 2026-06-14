# tests/test_config.py
import textwrap

from homed.config import load_config


def test_load_config_parses_backends_and_home_rows(tmp_path):
    p = tmp_path / "home.toml"
    p.write_text(
        textwrap.dedent(
            """
        [backends.gate]
        base_url = "http://127.0.0.1:8000"
        service_user = "svc@local"
        [backends.pool]
        base_url = "http://127.0.0.1:8080"
        [backends.fans]
        base_url = "http://127.0.0.1:8095"
        [web]
        bind = "0.0.0.0:8099"
        remote_domain = ""
        allowed_emails = []
        [[home.rows]]
        domain = "gate"
        control = "unlock"
        [[home.rows]]
        domain = "fans"
        groups = ["fans", "lights"]
    """
        )
    )
    cfg = load_config(p)
    assert cfg.backends["gate"]["base_url"] == "http://127.0.0.1:8000"
    assert cfg.backends["gate"]["service_user"] == "svc@local"
    assert cfg.web["bind"] == "0.0.0.0:8099"
    assert cfg.home_rows[0] == {"domain": "gate", "control": "unlock"}
    assert cfg.home_rows[1]["groups"] == ["fans", "lights"]


def test_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")

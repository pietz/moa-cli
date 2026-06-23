import pytest


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """Keep every test isolated from the developer's real MOA config."""
    monkeypatch.setenv("MOA_CONFIG_DIR", str(tmp_path / "_moa_cfg"))

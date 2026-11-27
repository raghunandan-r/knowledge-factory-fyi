import pytest


@pytest.fixture(autouse=True)
def _no_smoke_model(monkeypatch):
    """`make test` exports .env; a smoke model there must not switch the tests' model."""
    monkeypatch.delenv("KF_SMOKE_MODEL", raising=False)

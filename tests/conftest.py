from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.config import get_settings

TEST_PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    """Every test gets its own empty data folder and database, and never reads the real .env."""
    monkeypatch.setenv("ORDERDESK_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("OPERATOR_NAME", "Rich")
    get_settings.cache_clear()
    db.reset_engine()
    yield
    db.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def session() -> Session:
    with Session(db.get_engine()) as s:
        yield s


@pytest.fixture
def demo_files(tmp_path: Path) -> dict[str, Path]:
    from seed.demo_data import write_demo_files

    return write_demo_files(tmp_path / "seed")


@pytest.fixture
def demo_client(session: Session, demo_files):
    from seed.demo_data import load_demo_client

    return load_demo_client(session, demo_files)


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


@pytest.fixture
def browser(app) -> TestClient:
    """A test client that is not logged in."""
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def logged_in(browser: TestClient) -> TestClient:
    response = browser.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    return browser

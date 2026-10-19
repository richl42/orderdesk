import time

from app import auth
from app.config import get_settings
from tests.conftest import TEST_PASSWORD


def test_pages_redirect_to_login_when_logged_out(browser):
    response = browser.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_static_files_do_not_need_login(browser):
    assert browser.get("/static/css/app.css").status_code == 200


def test_wrong_password_is_refused(browser):
    response = browser.post("/login", data={"password": "nope"})
    assert response.status_code == 401
    assert "That password is not right" in response.text
    assert browser.get("/", follow_redirects=False).status_code == 303


def test_right_password_logs_in(browser):
    response = browser.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    home = browser.get("/")
    assert home.status_code == 200
    assert "Your clients" in home.text


def test_logged_out_after_eight_hours_idle(logged_in, monkeypatch):
    assert logged_in.get("/", follow_redirects=False).status_code == 200
    later = time.time() + auth.IDLE_LIMIT_SECONDS + 60
    monkeypatch.setattr(auth.time, "time", lambda: later)
    response = logged_in.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_activity_keeps_the_session_alive(logged_in, monkeypatch):
    start = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: start + 7 * 3600)
    assert logged_in.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setattr(auth.time, "time", lambda: start + 14 * 3600)
    assert logged_in.get("/", follow_redirects=False).status_code == 200


def test_logout(logged_in):
    response = logged_in.post("/logout", follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert logged_in.get("/", follow_redirects=False).status_code == 303


def test_no_password_set_means_nobody_can_log_in(monkeypatch, browser):
    monkeypatch.delenv("APP_PASSWORD")
    get_settings.cache_clear()
    page = browser.get("/login")
    assert "No password is set yet" in page.text
    response = browser.post("/login", data={"password": ""})
    assert response.status_code == 401

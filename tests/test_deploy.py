"""Running on a server (Render or Railway): production settings, HTTPS-only cookie, security headers,
form posts only from Order Desk's pages, upload limit, wrong-password limit, health check, start command."""

import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import PROJECT_DIR, SettingsProblem, check_production, get_settings
from tests.conftest import TEST_PASSWORD

STRONG_PASSWORD = "correct horse battery staple"
STRONG_KEY = "k" * 64


@pytest.fixture
def production(monkeypatch, tmp_path):
    """Good production settings with the data folder on a 'disk' that exists."""
    disk = tmp_path / "disk"
    disk.mkdir()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATA_DIR", str(disk))
    monkeypatch.delenv("BACKUPS_DIR", raising=False)
    monkeypatch.setenv("APP_PASSWORD", STRONG_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", STRONG_KEY)
    get_settings.cache_clear()
    return disk


def https_browser(app):
    return TestClient(app, base_url="https://orderdesk.example")


# ---------------------------------------------------------------- settings checks


def test_production_starts_with_good_settings(production):
    check_production(get_settings())
    assert get_settings().backups_dir == production / "backups"


@pytest.mark.parametrize("env, expected", [
    ({"APP_PASSWORD": "short"}, "APP_PASSWORD must be set and at least 12 characters"),
    ({"SECRET_KEY": "abc"}, "SECRET_KEY must be set to a long random string"),
    ({"DATA_DIR": "/no/such/disk"}, "which does not exist. Check the disk is mounted there"),
    ({"AI_CACHE": "on"}, "AI_CACHE must be off on the server"),
])
def test_production_refuses_to_start_with_a_bad_setting(production, monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with pytest.raises(SettingsProblem, match=re.escape(expected)):
        check_production(get_settings())
    with pytest.raises(SettingsProblem):
        from app.main import create_app  # importing it builds the app too

        create_app()


def test_production_needs_the_data_folder_to_be_named(production, monkeypatch):
    monkeypatch.delenv("DATA_DIR")
    get_settings.cache_clear()
    with pytest.raises(SettingsProblem, match="every deploy would wipe the database"):
        check_production(get_settings())


def test_railway_volume_is_the_data_folder(production, monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_DIR")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(production))
    get_settings.cache_clear()
    assert get_settings().data_dir == production
    check_production(get_settings())


def test_laptop_settings_are_not_checked():
    assert not get_settings().production
    check_production(get_settings())


def test_env_files_are_never_committed():
    ignored = (PROJECT_DIR / ".gitignore").read_text().splitlines()
    assert ".env" in ignored and ".env.*" in ignored and "!.env.example" in ignored
    assert "APP_PASSWORD=\n" in (PROJECT_DIR / ".env.example").read_text()  # the example holds no secrets


# ---------------------------------------------------------------- HTTPS cookie and headers


def test_production_login_cookie_is_https_only(production):
    from app.main import create_app

    browser = https_browser(create_app())
    response = browser.post("/login", data={"password": STRONG_PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert browser.get("/").status_code == 200
    assert "max-age=31536000" in browser.get("/login").headers["strict-transport-security"]


def test_laptop_cookie_works_over_plain_http(logged_in):
    assert logged_in.get("/").status_code == 200
    assert "strict-transport-security" not in logged_in.get("/login").headers


def test_security_headers_on_every_page(logged_in):
    page = logged_in.get("/")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"
    assert "no-store" not in logged_in.get("/static/css/app.css").headers.get("cache-control", "")


def test_templates_have_no_inline_scripts_or_handlers():
    """The Content-Security-Policy blocks them, so they would silently not work."""
    for path in (PROJECT_DIR / "app" / "web" / "templates").rglob("*.html"):
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", text), path
        assert not re.search(r"\son[a-z]+=|hx-on", text), path


# ---------------------------------------------------------------- form posts and uploads


def test_form_posts_from_another_site_are_refused(logged_in, session):
    from tests.helpers import add_client

    add_client(session, "abc", "ABC Cleaning Supplies")
    attack = logged_in.post("/clients/abc/delete", data={"confirm": "ABC Cleaning Supplies"},
                            headers={"Origin": "https://evil.example"})
    assert attack.status_code == 403 and "did not come from Order Desk" in attack.text
    attack = logged_in.post("/backup", headers={"Referer": "https://evil.example/page"})
    assert attack.status_code == 403
    assert logged_in.post("/backup", headers={"Origin": "null"}).status_code == 403
    session.expire_all()
    from app.models import Client

    assert session.get(Client, "abc") is not None
    own = logged_in.post("/backup", headers={"Origin": "http://127.0.0.1:8000"})
    assert own.status_code == 200


def test_proxy_host_counts_as_this_site(logged_in):
    response = logged_in.post("/backup", headers={"Origin": "https://orderdesk.onrender.com",
                                                  "X-Forwarded-Host": "orderdesk.onrender.com"})
    assert response.status_code == 200


def test_uploads_over_the_limit_are_refused(logged_in, demo_client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    big = b"x" * (1024 * 1024 + 10)
    response = logged_in.post("/clients/demo/orders/upload", files={"files": ("big.txt", big, "text/plain")})
    assert response.status_code == 413 and "The limit is 1 MB" in response.text


# ---------------------------------------------------------------- wrong passwords


def test_five_wrong_passwords_lock_that_address_out(browser):
    for _ in range(5):
        assert browser.post("/login", data={"password": "guess"}).status_code == 401
    locked = browser.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    assert locked.status_code == 429 and "Too many wrong passwords. Wait 15 minutes" in locked.text
    assert browser.get("/", follow_redirects=False).status_code == 303


def test_the_lock_lifts_after_15_minutes():
    limiter = security.LoginLimiter()
    for t in range(5):
        limiter.failed("1.2.3.4", now=100 + t)
    assert limiter.wait_seconds("1.2.3.4", now=110) > 0
    assert limiter.wait_seconds("5.6.7.8", now=110) == 0  # someone else is not locked out
    assert limiter.wait_seconds("1.2.3.4", now=100 + 15 * 60 + 1) == 0


def test_a_right_password_clears_earlier_mistakes(browser):
    for _ in range(4):
        browser.post("/login", data={"password": "guess"})
    browser.post("/login", data={"password": TEST_PASSWORD})
    browser.post("/logout")
    for _ in range(4):
        assert browser.post("/login", data={"password": "guess"}).status_code == 401


def test_many_addresses_together_hit_the_overall_limit():
    for i in range(30):
        security.login_failed(f"10.0.0.{i}")
    assert security.login_wait_seconds("192.168.1.1") > 0


# ---------------------------------------------------------------- health check, start command, backups


def test_health_check_needs_no_login_and_says_nothing_else(browser):
    response = browser.get("/healthz", follow_redirects=False)
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_start_command_listens_on_the_hosts_port_behind_its_proxy(monkeypatch):
    import uvicorn

    from app import serve

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setenv("PORT", "10000")
    serve.main()
    assert seen["app"] == "app.main:app" and seen["host"] == "0.0.0.0" and seen["port"] == 10000
    assert seen["proxy_headers"] is True and seen["workers"] == 1


def test_backups_kept_on_the_data_disk_are_not_put_in_later_backups(production):
    from app.backup import make_backup

    (production / "clients").mkdir()
    (production / "clients" / "x.txt").write_text("client file")
    first = make_backup()
    second = make_backup()
    assert first.parent == production / "backups"
    names = zipfile.ZipFile(second).namelist()
    assert "clients/x.txt" in names and not any(n.startswith("backups/") for n in names)


def test_deploy_files_use_the_production_start_command():
    for name in ("render.yaml", "railway.json"):
        text = Path(PROJECT_DIR / name).read_text()
        assert "python -m app.serve" in text and "/healthz" in text
    render = (PROJECT_DIR / "render.yaml").read_text()
    assert "mountPath: /var/data" in render and "value: /var/data" in render and "APP_ENV" in render
    assert not re.search(r"APP_PASSWORD\s*\n\s*value:", render)  # secrets are typed in on Render, not committed

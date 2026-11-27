import stat

import pytest
import requests
from scripts import authorize_trakt
from scripts.authorize_trakt import save_tokens, write_env


def test_replace_tokens_preserves_other_env_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=old-access\n"
        "TRAKT_REFRESH_TOKEN=old-refresh\nSMTP_PASSWORD=keep-private\n",
        encoding="utf-8",
    )

    write_env(
        env_path,
        {"TRAKT_ACCESS_TOKEN": "new-access", "TRAKT_REFRESH_TOKEN": "new-refresh"},
    )

    assert env_path.read_text(encoding="utf-8") == (
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=new-access\n"
        "TRAKT_REFRESH_TOKEN=new-refresh\nSMTP_PASSWORD=keep-private\n"
    )


def test_save_tokens_uses_existing_db_token_file_only(tmp_path):
    env_path = tmp_path / ".env"
    token_path = tmp_path / "data" / "tokens.env"
    token_path.parent.mkdir()
    env_path.write_text(
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n",
        encoding="utf-8",
    )
    token_path.write_text("TRAKT_ACCESS_TOKEN=rotated\nTRAKT_REFRESH_TOKEN=rotated\n")

    saved_path = save_tokens(
        env_path,
        {"DB_PATH": str(token_path.parent / "prices.db")},
        "new-access",
        "new-refresh",
    )

    assert saved_path == token_path
    assert env_path.read_text(encoding="utf-8") == "TRAKT_CLIENT_ID=public-id\n"
    assert token_path.read_text(encoding="utf-8") == (
        "TRAKT_ACCESS_TOKEN=new-access\nTRAKT_REFRESH_TOKEN=new-refresh\n"
    )


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


DEVICE = {
    "device_code": "device-123",
    "user_code": "ABCD1234",
    "verification_url": "https://trakt.tv/activate",
    "expires_in": 600,
    "interval": 5,
}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TRAKT_CLIENT_ID=public-id\nTRAKT_CLIENT_SECRET=secret\n"
        f"DB_PATH={tmp_path / 'data' / 'prices.db'}\nTRAKT_ACCESS_TOKEN=old\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    clock = FakeClock()
    monkeypatch.setattr(authorize_trakt.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(authorize_trakt.time, "sleep", clock.sleep)
    opened = []
    monkeypatch.setattr(authorize_trakt.webbrowser, "open", lambda url: opened.append(url) or True)
    return env_path, clock, opened


def install_posts(monkeypatch, token_responses):
    calls = []
    responses = iter([FakeResponse(200, DEVICE), *token_responses])

    def post(url, headers, json, timeout):
        calls.append((url, headers, json))
        return next(responses)

    monkeypatch.setattr(authorize_trakt.requests, "post", post)
    return calls


def test_authorize_polls_until_approved_and_writes_env(auth_env, monkeypatch, capsys):
    env_path, clock, opened = auth_env
    tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
    calls = install_posts(
        monkeypatch, [FakeResponse(400), FakeResponse(429), FakeResponse(200, tokens)]
    )

    authorize_trakt.authorize(env_path)

    assert calls[0][0] == "https://api.trakt.tv/oauth/device/code"
    assert calls[0][2] == {"client_id": "public-id"}
    assert calls[0][1]["trakt-api-key"] == "public-id"
    assert all(call[0] == "https://api.trakt.tv/oauth/device/token" for call in calls[1:])
    assert calls[1][2] == {
        "code": "device-123",
        "client_id": "public-id",
        "client_secret": "secret",
    }
    assert clock.sleeps == [5, 5, 10]
    assert opened == ["https://trakt.tv/activate"]
    text = env_path.read_text(encoding="utf-8")
    assert "TRAKT_ACCESS_TOKEN=new-access\n" in text and "TRAKT_REFRESH_TOKEN=new-refresh\n" in text
    assert "TRAKT_ACCESS_TOKEN=old" not in text and "TRAKT_CLIENT_SECRET=secret" in text
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "ABCD1234" in out and f"saved to {env_path}" in out


def test_authorize_writes_active_token_store_when_present(auth_env, monkeypatch, capsys):
    env_path, _clock, _opened = auth_env
    token_path = env_path.parent / "data" / "tokens.env"
    token_path.parent.mkdir()
    token_path.write_text("TRAKT_ACCESS_TOKEN=rotated\nTRAKT_REFRESH_TOKEN=rotated\n")
    tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
    install_posts(monkeypatch, [FakeResponse(200, tokens)])

    authorize_trakt.authorize(env_path)

    assert token_path.read_text(encoding="utf-8") == (
        "TRAKT_ACCESS_TOKEN=new-access\nTRAKT_REFRESH_TOKEN=new-refresh\n"
    )
    assert "TRAKT_ACCESS_TOKEN" not in env_path.read_text(encoding="utf-8")
    assert f"saved to {token_path}" in capsys.readouterr().out


def test_authorize_prints_url_when_browser_cannot_open(auth_env, monkeypatch, capsys):
    env_path, _clock, _opened = auth_env
    monkeypatch.setattr(authorize_trakt.webbrowser, "open", lambda url: False)
    install_posts(monkeypatch, [FakeResponse(200, {"access_token": "a", "refresh_token": "r"})])

    authorize_trakt.authorize(env_path)

    assert "Open the verification URL above" in capsys.readouterr().out


def test_authorize_requires_client_credentials(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("TRAKT_CLIENT_ID=public-id\n", encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("no request expected")

    monkeypatch.setattr(authorize_trakt.requests, "post", no_network)

    with pytest.raises(ValueError, match="TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET"):
        authorize_trakt.authorize(env_path)


def test_authorize_times_out_without_writing(auth_env, monkeypatch):
    env_path, clock, _opened = auth_env
    before = env_path.read_text(encoding="utf-8")
    install_posts(monkeypatch, [FakeResponse(400)] * 200)

    with pytest.raises(TimeoutError, match="expired"):
        authorize_trakt.authorize(env_path)

    assert clock.now == 600
    assert env_path.read_text(encoding="utf-8") == before


def test_authorize_surfaces_denied_code_as_http_error(auth_env, monkeypatch):
    env_path, _clock, _opened = auth_env
    install_posts(monkeypatch, [FakeResponse(418)])

    with pytest.raises(requests.HTTPError, match="418"):
        authorize_trakt.authorize(env_path)


def test_authorize_rejects_unexpected_success_status(auth_env, monkeypatch):
    env_path, _clock, _opened = auth_env
    install_posts(monkeypatch, [FakeResponse(202)])

    with pytest.raises(RuntimeError, match="failed \\(202\\)"):
        authorize_trakt.authorize(env_path)


def test_authorize_device_code_failure(auth_env, monkeypatch):
    env_path, _clock, _opened = auth_env
    monkeypatch.setattr(authorize_trakt.requests, "post", lambda *args, **kwargs: FakeResponse(401))

    with pytest.raises(requests.HTTPError, match="401"):
        authorize_trakt.authorize(env_path)


def test_write_env_appends_new_keys_and_removes_requested_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\nA=1\nB=2", encoding="utf-8")

    authorize_trakt.write_env(env_path, {"B": "3", "C": "4"})
    assert env_path.read_text(encoding="utf-8") == "# comment\nA=1\nB=3\nC=4\n"

    authorize_trakt.write_env(env_path, {"A": "ignored", "C": "ignored"}, remove=True)
    assert env_path.read_text(encoding="utf-8") == "# comment\nB=3\n"
    assert [path.name for path in tmp_path.iterdir()] == [".env"]


def test_save_tokens_falls_back_to_env_without_token_store(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TRAKT_CLIENT_ID=public-id\n", encoding="utf-8")

    saved = save_tokens(env_path, {"DB_PATH": str(tmp_path / "missing" / "prices.db")}, "a", "r")

    assert saved == env_path
    assert env_path.read_text(encoding="utf-8") == (
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=a\nTRAKT_REFRESH_TOKEN=r\n"
    )


@pytest.mark.parametrize(
    "error",
    [
        ValueError("missing id"),
        TimeoutError("expired"),
        requests.HTTPError("418"),
        RuntimeError("failed (202)"),
        KeyError("device_code"),
        OSError("read-only"),
    ],
)
def test_main_exits_1_with_message(monkeypatch, capsys, error):
    def fail():
        raise error

    monkeypatch.setattr(authorize_trakt, "authorize", fail)

    with pytest.raises(SystemExit) as exit_info:
        authorize_trakt.main()

    assert exit_info.value.code == 1
    assert "Authorization failed" in capsys.readouterr().err


def test_main_succeeds_without_exit(monkeypatch):
    monkeypatch.setattr(authorize_trakt, "authorize", lambda: None)

    authorize_trakt.main()

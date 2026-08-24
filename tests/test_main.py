import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from trakt_watchlist_monitor import main as main_module
from trakt_watchlist_monitor.config import Settings


def test_once_cli_runs_single_cycle_without_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_alerts = Mock()
    check_prices = Mock()
    sleep = Mock(side_effect=AssertionError("one-shot mode must not sleep"))
    monkeypatch.setattr(sys, "argv", ["trakt_watchlist_monitor", "--once"])
    monkeypatch.setattr(main_module.settings, "run_once", False)
    monkeypatch.setattr(main_module, "_maybe_reset_alerts", reset_alerts)
    monkeypatch.setattr(main_module, "check_prices", check_prices)
    monkeypatch.setattr(main_module.time, "sleep", sleep)

    main_module.main()

    reset_alerts.assert_called_once_with()
    check_prices.assert_called_once_with()
    sleep.assert_not_called()


def test_run_once_environment_runs_single_cycle_without_cli_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_cycle = Mock()
    sleep = Mock(side_effect=AssertionError("one-shot mode must not sleep"))
    monkeypatch.setenv("RUN_ONCE", "1")
    env_settings = _settings()
    monkeypatch.setattr(sys, "argv", ["trakt_watchlist_monitor"])
    monkeypatch.setattr(main_module, "settings", env_settings)
    monkeypatch.setattr(main_module, "_run_cycle", run_cycle)
    monkeypatch.setattr(main_module.time, "sleep", sleep)

    main_module.main()

    assert env_settings.run_once is True
    run_cycle.assert_called_once_with()
    sleep.assert_not_called()


def test_daemon_runs_cycle_and_sleeps_for_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_cycle = Mock()
    sleep = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(sys, "argv", ["trakt_watchlist_monitor"])
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(log_level="INFO", run_once=False, check_interval_hours=2.5),
    )
    monkeypatch.setattr(main_module, "_run_cycle", run_cycle)
    monkeypatch.setattr(main_module.time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        main_module.main()

    run_cycle.assert_called_once_with()
    sleep.assert_called_once_with(2.5 * 3600)


def test_daemon_sleeps_after_cycle_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    run_cycle = Mock(side_effect=RuntimeError("cycle failed"))
    sleep = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(sys, "argv", ["trakt_watchlist_monitor"])
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(log_level="INFO", run_once=False, check_interval_hours=1.25),
    )
    monkeypatch.setattr(main_module, "_run_cycle", run_cycle)
    monkeypatch.setattr(main_module.time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        main_module.main()

    run_cycle.assert_called_once_with()
    sleep.assert_called_once_with(1.25 * 3600)


def test_maybe_reset_alerts_returns_when_flag_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    init_db = Mock(side_effect=AssertionError("database must not be opened without reset flag"))
    monkeypatch.setattr(main_module.settings, "db_path", str(tmp_path / "prices.db"))
    monkeypatch.setattr(main_module.db, "init_db", init_db)

    main_module._maybe_reset_alerts()

    init_db.assert_not_called()


def test_maybe_reset_alerts_resets_state_and_removes_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flag_path = tmp_path / "reset_alerts"
    flag_path.touch()
    connection = MagicMock()
    counts = {
        "prices_restored": 2,
        "prices_cleared": 3,
        "notifications_cleared": 4,
    }
    init_db = Mock(return_value=connection)
    reset_notification_state = Mock(return_value=counts)
    monkeypatch.setattr(main_module.settings, "db_path", str(tmp_path / "prices.db"))
    monkeypatch.setattr(main_module.db, "init_db", init_db)
    monkeypatch.setattr(main_module.db, "reset_notification_state", reset_notification_state)

    main_module._maybe_reset_alerts()

    assert not flag_path.exists()
    init_db.assert_called_once_with(str(tmp_path / "prices.db"))
    reset_notification_state.assert_called_once_with(connection)
    connection.close.assert_called_once_with()


def _settings(**overrides: object) -> Settings:
    values = {
        "trakt_client_id": "client-id",
        "trakt_client_secret": "client-secret",
        "trakt_access_token": "access-token",
        "trakt_refresh_token": "refresh-token",
        "trakt_username": "username",
        "smtp_host": "smtp.example.com",
        "smtp_username": "smtp-user",
        "smtp_password": "smtp-password",
        "smtp_from": "from@example.com",
        "smtp_to": "to@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]

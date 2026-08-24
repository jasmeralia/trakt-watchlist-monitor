from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_drop(
    title: str = "Example Movie", last_price: float = 12.99, current_price: float = 7.99
) -> object:
    return SimpleNamespace(
        item={"title": title},
        currency="USD",
        last_price=last_price,
        current_price=current_price,
        quality="HD",
        image_url=None,
        trakt_url=f"https://trakt.tv/movies/{title.lower().replace(' ', '-')}",
        jw_url=None,
    )


@pytest.fixture
def notify_module(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setenv("TRAKT_CLIENT_ID", "client-id")
    monkeypatch.setenv("TRAKT_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TRAKT_ACCESS_TOKEN", "access-token")
    monkeypatch.setenv("TRAKT_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("TRAKT_USERNAME", "username")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("SMTP_FROM", "sender@example.com")
    monkeypatch.setenv("SMTP_TO", "recipient@example.com")

    from trakt_watchlist_monitor import notify

    return notify


def test_send_digest_sends_html_email(notify_module: object) -> None:
    notify_module.settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="smtp-user",
        smtp_password="smtp-password",
        smtp_from="sender@example.com",
        smtp_to="recipient@example.com",
        email_theme="dark",
        app_version="",
        sale_price_threshold=5.0,
    )
    smtp = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp

    with patch.object(notify_module.smtplib, "SMTP", return_value=smtp_context) as smtp_class:
        notify_module.send_digest([_make_drop("Example Movie", 12.99, 7.99)])

    smtp_class.assert_called_once_with("smtp.example.com", 587, timeout=30)
    smtp.starttls.assert_called_once_with()
    smtp.login.assert_called_once_with("smtp-user", "smtp-password")
    smtp.send_message.assert_called_once()

    message = smtp.send_message.call_args.args[0]
    assert message["Subject"] == "[Trakt Watchlist Monitor] Price drop: Example Movie"
    assert message["From"] == "sender@example.com"
    assert message["To"] == "recipient@example.com"

    html_part = next(
        part for part in message.iter_parts() if part.get_content_type() == "text/html"
    )
    html = html_part.get_content()
    assert "Example Movie" in html
    assert "12.99" in html
    assert "7.99" in html


def test_send_digest_uses_plural_subject_for_multiple_drops(notify_module: object) -> None:
    notify_module.settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="smtp-user",
        smtp_password="smtp-password",
        smtp_from="sender@example.com",
        smtp_to="recipient@example.com",
        email_theme="dark",
        app_version="",
        sale_price_threshold=5.0,
    )
    smtp = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp

    with patch.object(notify_module.smtplib, "SMTP", return_value=smtp_context):
        notify_module.send_digest([_make_drop("Movie A"), _make_drop("Movie B")])

    message = smtp.send_message.call_args.args[0]
    assert message["Subject"] == "[Trakt Watchlist Monitor] Price drops: 2 watchlist titles on sale"


def test_send_digest_uses_smtp_ssl_for_port_465(notify_module: object) -> None:
    notify_module.settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="smtp-user",
        smtp_password="smtp-password",
        smtp_from="sender@example.com",
        smtp_to="recipient@example.com",
        email_theme="dark",
        app_version="",
        sale_price_threshold=5.0,
    )
    smtp = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp

    with (
        patch.object(notify_module.smtplib, "SMTP") as smtp_class,
        patch.object(
            notify_module.smtplib, "SMTP_SSL", return_value=smtp_context
        ) as smtp_ssl_class,
    ):
        notify_module.send_digest([_make_drop()])

    smtp_class.assert_not_called()
    smtp_ssl_class.assert_called_once_with("smtp.example.com", 465, timeout=30)
    smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with("smtp-user", "smtp-password")
    smtp.send_message.assert_called_once()

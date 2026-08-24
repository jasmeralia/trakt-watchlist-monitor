import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from .config import settings

if TYPE_CHECKING:
    from .pricing import PriceDrop

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def send_digest(drops: "list[PriceDrop]") -> None:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template(f"email_{settings.email_theme}.html")
    html = template.render(
        drops=[_drop_to_dict(d) for d in drops],
        app_version=settings.app_version or None,
        sale_price_threshold=settings.sale_price_threshold,
    )

    title = drops[0].item.get("title", "Watchlist item")
    if len(drops) == 1:
        subject = f"[Trakt Watchlist Monitor] Price drop: {title}"
    else:
        subject = f"[Trakt Watchlist Monitor] Price drops: {len(drops)} watchlist titles on sale"

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = settings.smtp_to
    message["Subject"] = subject
    message.set_content("")
    message.add_alternative(html, subtype="html")
    _send(message)


def _drop_to_dict(drop: "PriceDrop") -> dict[str, Any]:
    has_prior_price = drop.last_price > 0.0
    return {
        "title": drop.item.get("title", "Watchlist item"),
        "currency": drop.currency,
        "last_price": drop.last_price if has_prior_price else None,
        "current_price": drop.current_price,
        "drop_percent": (
            (drop.last_price - drop.current_price) / drop.last_price * 100
            if has_prior_price
            else None
        ),
        "quality": drop.quality,
        "image_url": drop.image_url,
        "trakt_url": drop.trakt_url,
        "jw_url": drop.jw_url,
    }


def _send(message: EmailMessage) -> None:
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)

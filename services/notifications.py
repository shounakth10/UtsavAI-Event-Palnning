"""Send a plan summary only to the authenticated user's verified email address."""

import smtplib
from email.message import EmailMessage

import config
from schemas import EventPlan
from services.export import generate_whatsapp_summary


def send_plan_email(recipient: str, plan: EventPlan) -> None:
    """Deliver the event summary using configured SMTP credentials."""
    required = {
        "SMTP_HOST": config.SMTP_HOST,
        "SMTP_USERNAME": config.SMTP_USERNAME,
        "SMTP_PASSWORD": config.SMTP_PASSWORD,
        "SMTP_FROM_EMAIL": config.SMTP_FROM_EMAIL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Email delivery is not configured. Set: " + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = f"Your UtsavAI event plan: {plan.event.title.replace(chr(10), ' ').replace(chr(13), ' ')}"
    message["From"] = config.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(generate_whatsapp_summary(plan))

    if config.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            config.SMTP_HOST,
            config.SMTP_PORT,
            timeout=20,
        ) as server:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(message)
    else:
        with smtplib.SMTP(
            config.SMTP_HOST,
            config.SMTP_PORT,
            timeout=20,
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.send_message(message)

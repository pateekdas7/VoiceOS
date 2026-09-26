"""GmailNotifier — sends incident notifications via SMTP/Gmail."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

_log = logging.getLogger("system_x.notifications.gmail")


class GmailNotifier:
    def __init__(self, sender_email: str, app_password: str) -> None:
        self._sender = sender_email
        self._password = app_password

    def send(self, recipient: str, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self._sender
        msg["To"] = recipient

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(self._sender, self._password)
            smtp.sendmail(self._sender, [recipient], msg.as_string())

        _log.info("gmail sent to=%s subject=%r", recipient, subject[:60])


__all__ = ["GmailNotifier"]

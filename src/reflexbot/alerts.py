"""Alerts: Telegram when a bot token is configured, always the log.

TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID come from the environment. The token is
never logged. Sending is best-effort: an alert failure never blocks trading
logic, and no safety decision depends on an alert being delivered.
"""
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger("reflexbot.alerts")


class Alerter:
    def __init__(self, session=None, cooldown_s: float = 900.0):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.session = session or requests.Session()
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, key: str | None = None, level: str = "info") -> bool:
        """Log always; send to Telegram if configured. `key` rate-limits repeats."""
        now = time.time()
        if key is not None:
            if now - self._last.get(key, 0.0) < self.cooldown_s:
                return False
            self._last[key] = now
        getattr(log, "warning" if level != "info" else "info")("ALERT %s", text)
        if not self.telegram_enabled:
            return False
        try:
            r = self.session.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                  json={"chat_id": self.chat_id, "text": text[:4000]}, timeout=10)
            return r.status_code == 200
        except Exception as e:  # never let an alert failure propagate
            log.warning("telegram send failed: %s", type(e).__name__)
            return False

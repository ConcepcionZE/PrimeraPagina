"""The live gate. Real orders are impossible until the owner opens it.

Three things must all be true:
  1. TRADING_MODE=live in the environment (default is paper).
  2. An approval file exists at LIVE_APPROVAL_FILE (default ./LIVE_APPROVAL,
     git-ignored) whose content is exactly the approval phrase followed by the
     policy version being approved. The owner writes it by hand.
  3. KRAKEN_API_KEY and KRAKEN_API_SECRET are present in the environment.

No code path in this repo writes the approval file.
"""
from __future__ import annotations

import os

APPROVAL_PHRASE = "I APPROVE LIVE TRADING"


class LiveNotApproved(PermissionError):
    pass


def live_status(policy_version: str | None = None) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if os.environ.get("TRADING_MODE", "paper") != "live":
        missing.append("TRADING_MODE is not 'live'")
    path = os.environ.get("LIVE_APPROVAL_FILE", "LIVE_APPROVAL")
    if not os.path.exists(path):
        missing.append(f"approval file {path} does not exist")
    else:
        with open(path) as f:
            text = f.read().strip()
        if not text.startswith(APPROVAL_PHRASE):
            missing.append("approval file does not contain the approval phrase")
        elif policy_version and not text.endswith(policy_version):
            missing.append(f"approval file does not name policy {policy_version}")
    for var in ("KRAKEN_API_KEY", "KRAKEN_API_SECRET"):
        if not os.environ.get(var):
            missing.append(f"{var} is not set")
    return (not missing, missing)


def require_live_approval(policy_version: str | None = None) -> None:
    ok, missing = live_status(policy_version)
    if not ok:
        raise LiveNotApproved("live trading is not approved: " + "; ".join(missing))

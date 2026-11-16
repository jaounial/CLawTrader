"""Phone notifications via ntfy.sh (free, no account, stdlib only).

Setup on your phone: install the free "ntfy" app, tap "+", subscribe to your topic name.
The topic name works like a password: anyone who knows it can read your alerts, so use the
long random one from `make_topic()`, keep it in the NTFY_TOPIC env var, never commit it.
"""
from __future__ import annotations

import secrets
import urllib.request
from typing import Optional

from .edge import Recommendation

NTFY_BASE = "https://ntfy.sh"


def make_topic(prefix: str = "clawtrader") -> str:
    return f"{prefix}-{secrets.token_urlsafe(12)}"


def send(topic: str, message: str, title: str = "Trade alert", priority: int = 3,
         tags: str = "chart_with_upwards_trend", dry_run: bool = False) -> bool:
    """Send one push notification. Returns True if sent (or printed in dry-run)."""
    if dry_run or not topic:
        print(f"[notify dry-run] {title}: {message}")
        return True
    req = urllib.request.Request(
        f"{NTFY_BASE}/{topic}", data=message.encode("utf-8"), method="POST",
        headers={"Title": title.encode("latin-1", "replace").decode("latin-1"),
                 "Priority": str(priority), "Tags": tags})
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


def format_recommendation(rec: Recommendation, what: str, expires: str,
                          fee: float = 0.02, note: Optional[str] = None) -> str:
    """Text you can act on in the app, which takes a dollar amount (not contracts).

    what: e.g. "BTC at or above $64,800"; expires: e.g. "12:00 PM ET".
    """
    dollars = rec.contracts * (rec.price + fee)
    lines = [
        f"BUY {rec.side}: {what}",
        f"Expires {expires}",
        f"Enter about ${dollars:.2f} (about {rec.contracts} contracts at {rec.price * 100:.0f}c "
        f"+ {fee * 100:.0f}c fee each)",
        f"Skip it if the app shows {rec.side} above {(rec.price + 0.02) * 100:.0f}c",
        f"Model win chance {rec.p_win * 100:.0f}%, expected profit {rec.ev_per_contract * 100:+.1f}c per contract",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)

"""Telegram Mini App login: verify the signed `initData` the client sends.
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE = 24 * 3600


def validate_init_data(init_data: str, bot_token: str, now: float | None = None, max_age: int = MAX_AGE) -> dict | None:
    """Returns the Telegram user dict if the signature and age are valid, else None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    given = pairs.pop("hash", None)
    if not given:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, given):
        return None
    try:
        age = (now if now is not None else time.time()) - int(pairs.get("auth_date", "0"))
        if age > max_age or age < -300:
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user, dict) and "id" in user else None
    except (KeyError, ValueError, TypeError):
        return None

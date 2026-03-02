from __future__ import annotations

import hmac

from fastapi import Request

from app.config import get_settings


def is_admin_authenticated(request: Request) -> bool:
    return bool(request.session.get("admin_logged_in"))


def authenticate_admin(username: str, password: str) -> bool:
    settings = get_settings()
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password,
        settings.admin_password,
    )

"""Auth dependencies: API token for machines, Basic auth for the admin UI/API."""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import get_settings

_basic = HTTPBasic(auto_error=True)


def require_api_token(x_api_token: str | None = Header(default=None)) -> None:
    expected = get_settings().api_token
    if not x_api_token or not secrets.compare_digest(x_api_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Token",
        )


def require_admin(credentials: HTTPBasicCredentials = Depends(_basic)) -> str:
    settings = get_settings()
    user_ok = secrets.compare_digest(credentials.username, settings.admin_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

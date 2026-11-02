"""Single-owner bearer credential; student endpoints remain account-free."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ytu_scheduler.config import get_settings

bearer = HTTPBearer(auto_error=False)


def require_owner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    configured = get_settings().owner_token
    expected = configured.get_secret_value() if configured else ""
    if len(expected) < 32:
        raise HTTPException(
            503, "Owner access is disabled; configure YTU_OWNER_TOKEN (32+ characters)"
        )
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(
            401, "Owner access key is invalid", headers={"WWW-Authenticate": "Bearer"}
        )

from __future__ import annotations

from typing import AsyncIterator, Generator

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from sales_automation.db.session import SessionLocal
from sales_automation.models import User
from sales_automation.services.auth import AuthService, AuthServiceError

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_db_async() -> AsyncIterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        return AuthService(db).authenticate_access_token(credentials.credentials)
    except AuthServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

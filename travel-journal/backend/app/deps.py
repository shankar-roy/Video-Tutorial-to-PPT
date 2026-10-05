from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.models import Member
from app.security import read_session


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_member(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> Member:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        member_id = read_session(token, request.app.state.settings.secret_key)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sign in required") from None
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return member

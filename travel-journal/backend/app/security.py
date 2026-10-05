from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


class AppleSignInError(Exception):
    pass


class AppleVerifier:
    def __init__(self, audience: str, jwks_client):
        self.audience = audience
        self.jwks_client = jwks_client

    def verify(self, identity_token: str) -> dict:
        try:
            key = self.jwks_client.get_signing_key_from_jwt(identity_token).key
            return jwt.decode(
                identity_token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer="https://appleid.apple.com",
            )
        except jwt.PyJWTError as exc:
            raise AppleSignInError("Apple sign-in could not be verified") from exc


def live_apple_verifier(audience: str) -> AppleVerifier:
    from jwt import PyJWKClient

    return AppleVerifier(audience, PyJWKClient("https://appleid.apple.com/auth/keys"))


def create_session(member_id: int, secret_key: str) -> str:
    payload = {
        "sub": str(member_id),
        "kind": "session",
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def read_session(token: str, secret_key: str) -> int:
    payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    if payload.get("kind") != "session":
        raise jwt.InvalidTokenError("not a session")
    return int(payload["sub"])


def create_oauth_state(member_id: int, redirect_uri: str, secret_key: str) -> str:
    payload = {
        "sub": str(member_id),
        "redir": redirect_uri,
        "kind": "gmail-state",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def read_oauth_state(token: str, secret_key: str) -> dict:
    payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    if payload.get("kind") != "gmail-state":
        raise jwt.InvalidTokenError("not a gmail state")
    return payload

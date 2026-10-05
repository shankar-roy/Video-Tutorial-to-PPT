from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.factory import create_app
from app.security import AppleVerifier
from app.settings import Settings

AUDIENCE = "com.family.traveljournal"
UTC = timezone.utc


class StaticKeys:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=self.public_key)


@pytest.fixture(scope="session")
def apple_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def client(tmp_path, apple_keys):
    _private, public = apple_keys
    settings = Settings(
        secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path}/journal.db",
        apple_client_id=AUDIENCE,
        google_client_id="google-client",
        google_client_secret="google-secret",
        public_base_url="http://127.0.0.1:8000",
        allow_dev_login=False,
        media_root=str(tmp_path / "media"),
        app_env="test",
    )
    app = create_app(settings, apple_verifier=AppleVerifier(AUDIENCE, StaticKeys(public)))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sign_apple(apple_keys):
    private, _public = apple_keys

    def _sign(sub: str, *, audience: str = AUDIENCE) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iss": "https://appleid.apple.com",
                "aud": audience,
                "sub": sub,
                "email": f"{sub}@example.com",
                "iat": now,
                "exp": now + timedelta(minutes=10),
            },
            private,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    return _sign


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, sign_apple, sub: str, name: str) -> dict:
    response = client.post(
        "/auth/apple",
        json={"identity_token": sign_apple(sub), "display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()

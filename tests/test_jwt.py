import os
import uuid

os.environ.setdefault("AGNTSPARK_GATEWAY_JWT_SECRET", "test-secret")

import pytest

from agntspark_gateway.exceptions import AuthenticationError
from agntspark_gateway.security.jwt import create_access_token, decode_access_token


def test_create_and_decode_roundtrip() -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id=user_id, email="a@b.com", role=1)
    assert expires_in > 0

    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["email"] == "a@b.com"
    assert payload["role"] == 1


def test_decode_rejects_garbage_token() -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token("not-a-jwt")


def test_decode_rejects_tampered_token() -> None:
    user_id = uuid.uuid4()
    token, _ = create_access_token(user_id=user_id, email="a@b.com", role=0)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AuthenticationError):
        decode_access_token(tampered)

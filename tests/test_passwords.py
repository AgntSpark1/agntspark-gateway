from agntspark_gateway.security.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", hashed)


def test_hash_is_not_the_raw_password() -> None:
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"

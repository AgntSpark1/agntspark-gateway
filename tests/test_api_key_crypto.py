from agntspark_core.auth import Role

from agntspark_gateway.security.api_keys import hash_key, keys_match, mint_api_key


def test_mint_api_key_format() -> None:
    raw_key, key_hash, key_prefix = mint_api_key("user-123", Role.OPERATOR)
    assert raw_key.startswith("agnt_")
    assert key_hash == hash_key(raw_key)
    assert raw_key.startswith(key_prefix)


def test_keys_match_accepts_correct_key() -> None:
    raw_key, key_hash, _ = mint_api_key("user-123", Role.VIEWER)
    assert keys_match(raw_key, key_hash)


def test_keys_match_rejects_wrong_key() -> None:
    raw_key, key_hash, _ = mint_api_key("user-123", Role.VIEWER)
    other_raw_key, _, _ = mint_api_key("user-456", Role.VIEWER)
    assert not keys_match(other_raw_key, key_hash)

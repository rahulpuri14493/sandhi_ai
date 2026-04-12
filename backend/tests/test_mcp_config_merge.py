from services.mcp_config_merge import merge_shallow_config, public_config_preview


def test_merge_skips_empty_string_and_none():
    base = {"access_token": "keep-me", "host": "h", "port": "587"}
    patch = {"access_token": "", "oauth_refresh_token": None, "from_name": "Bot"}
    assert merge_shallow_config(base, patch) == {
        "access_token": "keep-me",
        "host": "h",
        "port": "587",
        "from_name": "Bot",
    }


def test_merge_whitespace_only_string_skipped():
    base = {"token": "x"}
    assert merge_shallow_config(base, {"token": "  \t  "}) == {"token": "x"}


def test_merge_applies_nonempty_and_new_keys():
    base = {"a": "1"}
    assert merge_shallow_config(base, {"a": "2", "b": "3"}) == {"a": "2", "b": "3"}


def test_public_config_preview_omits_secrets():
    cfg = {
        "provider": "outlook",
        "username": "u@x.com",
        "access_token": "secret",
        "oauth_refresh_token": "rt",
        "password": "pw",
        "from_name": "Bot",
    }
    prev = public_config_preview(cfg)
    assert prev["provider"] == "outlook"
    assert prev["username"] == "u@x.com"
    assert prev["from_name"] == "Bot"
    assert "access_token" not in prev
    assert "password" not in prev

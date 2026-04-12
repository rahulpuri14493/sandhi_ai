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


def test_public_config_preview_non_dict():
    assert public_config_preview(None) == {}  # type: ignore[arg-type]
    assert public_config_preview("x") == {}  # type: ignore[arg-type]


def test_public_config_preview_skips_none_nested_and_empty():
    cfg = {
        "keep": "v",
        "nullish": None,
        "nested": {"a": 1},
        "spaces": "   ",
        "bot_token": "secret",
    }
    prev = public_config_preview(cfg)
    assert prev == {"keep": "v"}
    assert "nested" not in prev


def test_merge_shallow_patch_not_dict():
    assert merge_shallow_config({"a": 1}, None) == {"a": 1}  # type: ignore[arg-type]


def test_merge_shallow_base_not_dict():
    assert merge_shallow_config(None, {"a": "1"}) == {"a": "1"}  # type: ignore[arg-type]

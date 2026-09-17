"""Redaction for trajectories leaving this node.

Every case here is a credential that would otherwise reach a friend's machine with no
error and no visible sign. Several are cases `store.redact` — the obvious function to
reach for — misses outright, which is why the comparison is asserted explicitly.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.trajectories import outbound, store


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "apiKey",
        "x-api-key",
        "Authorization",
        "cookie",
        "Set-Cookie",
        "client_secret",
        "access_token",
        "password",
        "db_passwd",
        "private_key",
        "auth",
        "sessionId",
    ],
)
def test_credential_keys_are_blanked(key):
    assert outbound.redact({key: "hunter2-value"}) == {key: store.REDACTED}


@pytest.mark.parametrize("key", ["api_key", "Authorization", "x-api-key", "cookie"])
def test_the_settings_redactor_misses_these_which_is_why_this_module_exists(key):
    """Pinned so nobody "simplifies" this module back onto `store.redact`."""
    assert store.redact({key: "hunter2-value"}) == {key: "hunter2-value"}


@pytest.mark.parametrize("key", ["key", "keys", "keyword", "monkey", "token_count"])
def test_ordinary_keys_survive(key):
    """Bare `key` is how key-value tools name an argument. Blanking it would make a
    shared run unreadable to protect nothing. (`token_count` contains `token` and is
    blanked — an over-match accepted on purpose; see the next test.)"""
    result = outbound.redact({key: "alpha"})[key]
    if key == "token_count":
        assert result == store.REDACTED
    else:
        assert result == "alpha"


def test_over_matching_a_name_is_the_accepted_failure_direction():
    """Blanking a harmless `token_count` costs a number; missing `refresh_token` costs
    an account. The asymmetry decides which way the matcher leans."""
    assert outbound.redact({"refresh_token": "r"}) == {"refresh_token": store.REDACTED}


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop.qrstuvwxyz",
        "use key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv to call it",
        "sk-proj-0123456789abcdefABCDEF",
        "token ghp_0123456789abcdefghijABCDEFGHIJ",
        "github_pat_11ABCDEFG0123456789_abcdefghij",
        "xoxb-1234567890-abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA-0123456789abcdefghijklmnopqrstu",
        "hf_abcdefghijklmnopqrstuvwxyz",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
        "postgres://admin:s3cr3t-pass@db.internal:5432/app",
        "password=correct-horse-battery",
        '{"note": "api_key: 9f8e7d6c5b4a"}',
    ],
)
def test_credentials_inside_free_text_are_masked(text):
    """A secret in a tool *result* has no key name to match — results are the most
    likely place for one to appear, so the contents are scanned too."""
    scrubbed = outbound.scrub_text(text)
    assert outbound.MASK in scrubbed
    for secret in (
        "abcdefghijklmnop.qrstuvwxyz",
        "AbCdEfGhIjKlMnOpQrStUv",
        "0123456789abcdefABCDEF",
        "0123456789abcdefghijABCDEFGHIJ",
        "11ABCDEFG0123456789",
        "1234567890-abcdefghij",
        "IOSFODNN7EXAMPLE",
        "0123456789abcdefghijklmnopqrstu",
        "abcdefghijklmnopqrstuvwxyz",
        "SflKxwRJSMeKKF2QT4fwpMeJf36",
        "s3cr3t-pass",
        "correct-horse-battery",
        "9f8e7d6c5b4a",
    ):
        assert secret not in scrubbed


def test_a_private_key_block_is_masked_whole():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----"
    )
    assert outbound.scrub_text(f"here:\n{pem}\nend") == f"here:\n{outbound.MASK}\nend"


def test_labels_survive_so_a_reader_knows_what_was_there():
    assert outbound.scrub_text("password=hunter22") == "password=[redacted]"
    assert outbound.scrub_text("Bearer abcdefgh12345") == "Bearer [redacted]"


def test_ordinary_prose_is_untouched():
    text = "Opened the terminal and ran pytest; 42 passed in 3.1s. The token budget was fine."
    assert outbound.scrub_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "the token generation step ran",
        "basic arithmetic questions",
        "Bearer of bad news arrived",
        "token limit exceeded, retrying",
    ],
)
def test_scheme_words_in_prose_are_not_mistaken_for_credentials(text):
    """Found by probing, not by the first test run: the scheme pattern masked
    "token generation" and "basic arithmetic". A shared run full of `[redacted]` in
    ordinary sentences is unreadable, and teaches the reader to ignore the marker."""
    assert outbound.scrub_text(text) == text


def test_redaction_recurses_through_lists_and_nesting():
    value = {
        "calls": [{"headers": {"Authorization": "x"}, "body": "Bearer abcdefgh1234"}]
    }
    out = outbound.redact(value)
    assert out["calls"][0]["headers"]["Authorization"] == store.REDACTED
    assert out["calls"][0]["body"] == "Bearer [redacted]"


def test_an_oversized_payload_becomes_a_marker_with_its_size():
    big = {"text": "x" * (outbound.PEER_PAYLOAD_MAX + 10)}
    out = outbound.bounded(big)
    assert out["_omitted"]
    assert out["bytes"] > outbound.PEER_PAYLOAD_MAX


def test_a_bounded_payload_is_redacted_too():
    out = outbound.bounded({"api_key": "k", "result": "ok"})
    assert out == {"api_key": store.REDACTED, "result": "ok"}
    assert json.dumps(out)

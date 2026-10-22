"""Must-have 7: secrets never reach the logs."""
import json
import logging

from app.logging_setup import JsonFormatter, correlation_id_var, redact, register_secret


def test_redacts_tokens_and_passwords():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc_DEF-123"
    ghost_tok = "abcdef0123456789.payload.signature"
    out = redact(f"Authorization: Bearer {jwt} Ghost {ghost_tok} password=hunter2xyz api_key: sk-live-999")
    assert jwt not in out and "hunter2xyz" not in out and "sk-live-999" not in out and ghost_tok not in out


def test_ordinary_words_are_not_redacted():
    assert redact("ghost is up; bearer of news") == "ghost is up; bearer of news"


def test_registered_secret_is_redacted():
    register_secret("very-private-value-42")
    assert "very-private-value-42" not in redact("connecting with very-private-value-42")


def test_json_log_line_has_required_fields():
    tok = correlation_id_var.set("cid-1")
    try:
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
        line = json.loads(JsonFormatter().format(rec))
    finally:
        correlation_id_var.reset(tok)
    assert {"timestamp", "level", "message", "correlation_id", "job_id"} <= set(line)
    assert line["correlation_id"] == "cid-1"


def test_secret_from_env_ref_never_logged(client, mock, monkeypatch, capsys):
    monkeypatch.setenv("LEMMY_SECRET_PW", "s3cret-lemmy-password")
    body = {"rendition": {"title": "hello", "body": "x"},
            "destination": {"params": {"base_url": "https://l.example", "community": "c"}},
            "connection": {"secret_refs": {"username": "env:LEMMY_SECRET_USER", "password": "env:LEMMY_SECRET_PW"}},
            "idempotency_key": "log-test"}
    client.post("/connector/v1/lemmy/publish", json=body)
    logging.getLogger("t").info("debug dump %s", "s3cret-lemmy-password")
    assert "s3cret-lemmy-password" not in capsys.readouterr().out

"""spa.add_body_html — 연결 전에 지은 화면도 본문 HTML이 브라우저에 닿는지."""

from __future__ import annotations

import hashlib
import json

import pytest

from narani.ui import spa


class _Client:
    def __init__(self, connected: bool) -> None:
        self.has_socket_connection = connected
        self.js: list[str] = []

    def run_javascript(self, code: str) -> None:
        self.js.append(code)


@pytest.fixture
def fake(monkeypatch):
    from nicegui import context, ui

    def make(connected: bool):
        client = _Client(connected)
        body: list[str] = []
        monkeypatch.setattr(type(context), "client", property(lambda self: client))
        monkeypatch.setattr(ui, "add_body_html", body.append)
        return client, body

    return make


def test_before_connection_sends_body_and_guarded_fallback(fake):
    client, body = fake(False)
    code = '<div id="md-x"></div><script>window.__x=1;</script>'
    spa.add_body_html(code)
    key = hashlib.sha1(code.encode("utf-8")).hexdigest()[:16]
    # 본문에 원래 HTML + 표식
    assert len(body) == 1 and body[0].startswith(code) and f'["{key}"]=1' in body[0]
    # 연결 뒤 실행될 대비책 — 표식이 있으면 건너뛴다
    assert len(client.js) == 1
    assert json.dumps(key) in client.js[0] and "if(s[k]" in client.js[0]
    assert json.dumps(code) in client.js[0]


def test_after_connection_runs_through_md4addhtml_only(fake):
    client, body = fake(True)
    spa.add_body_html("<script>1</script>")
    assert body == []
    assert client.js == ['window.__md4AddHtml && window.__md4AddHtml("<script>1</script>");']

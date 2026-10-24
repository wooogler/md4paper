"""폰 리더 — 번들·목록 올리기(바뀐 것만), 로컬 저장소, 웹앱 파일, 미리보기 서버."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest

pytest.importorskip("nicegui", reason="웹 UI 의존성(ui extra) 미설치")

from narani.phone import bundle, sync, web  # noqa: E402
from narani.phone.store import LocalStore  # noqa: E402
from narani.workdir import WorkDir  # noqa: E402

EN = """# Bespoke Bots

We study how instructors customize chatbots [1](#ref-1). It matters.

![Figure 1](images/figure-1.png)
> **Figure 1:** The interface.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.
"""

KO = """# 맞춤형 봇

우리는 교수자가 챗봇을 어떻게 맞춤화하는지 연구한다 [1](#ref-1). 이는 중요하다.

![Figure 1](images/figure-1.png)
> **Figure 1:** 인터페이스.

## References

<a id="ref-1"></a>[1] Lee, M. 2022. CoAuthor. CHI.
"""


def _wd(tmp_path, name: str, ko: str | None = KO) -> WorkDir:
    from PIL import Image

    wd = WorkDir(tmp_path / "ws" / f"{name}.md4")
    wd.ensure()
    wd.en_md.write_text(EN, encoding="utf-8")
    if ko is not None:
        wd.ko_md.write_text(ko, encoding="utf-8")
    wd.out_images.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), (200, 30, 30)).save(wd.out_images / "figure-1.png")
    return wd


def _index(store: LocalStore) -> dict:
    return json.loads(store.read("index.json"))


def test_push_writes_bundle_and_index(tmp_path):
    wd = _wd(tmp_path, "bots")
    store = LocalStore(tmp_path / "store")
    s = sync.push([wd.root], store)
    assert (s["uploaded"], s["skipped"], s["papers"]) == (1, 0, 1)
    ix = _index(store)
    e = ix["papers"][0]
    assert e["key"] == "bots" and e["has_ko"] and e["aligned"] and e["title_ko"] == "맞춤형 봇"
    assert e["file"] == bundle.file_name("bots") and re.fullmatch(r"papers/[0-9a-f]{20}\.json", e["file"])
    b = json.loads(store.read(e["file"]))
    assert b["hash"] == e["hash"] and b["format"] == bundle.FORMAT
    assert b["html"].startswith('<article class="paper has-ko" id="p0" data-key="bots">')
    assert "data:image/" in b["html"] and "md4img/" not in b["html"]  # 그림이 번들 안에
    assert b["html"].count('class="row') >= 3


def test_push_skips_unchanged_and_rebuilds_changed(tmp_path):
    wd = _wd(tmp_path, "bots")
    store = LocalStore(tmp_path / "store")
    sync.push([wd.root], store)
    first = _index(store)["papers"][0]["hash"]
    s = sync.push([wd.root], store)
    assert (s["uploaded"], s["skipped"]) == (0, 1)
    wd.ko_md.write_text(KO.replace("중요하다", "매우 중요하다"), encoding="utf-8")
    s = sync.push([wd.root], store)
    assert s["uploaded"] == 1
    e = _index(store)["papers"][0]
    assert e["hash"] != first and "매우 중요하다" in json.loads(store.read(e["file"]))["html"]


def test_prune_removes_papers_not_chosen_and_add_keeps_them(tmp_path):
    a, b = _wd(tmp_path, "alpha"), _wd(tmp_path, "beta", ko=None)
    store = LocalStore(tmp_path / "store")
    sync.push([a.root, b.root], store, extra={"alpha": {"pinned": True}})
    ix = _index(store)
    assert [e["key"] for e in ix["papers"]] == ["alpha", "beta"]
    assert ix["papers"][0]["pinned"] is True and ix["papers"][1]["has_ko"] is False
    sync.push([b.root], store)  # 더하기·고치기만 — alpha는 그대로
    assert {e["key"] for e in _index(store)["papers"]} == {"alpha", "beta"}
    s = sync.push([b.root], store, prune=True)
    assert s["removed"] == 1 and [e["key"] for e in _index(store)["papers"]] == ["beta"]
    assert store.read(bundle.file_name("alpha")) is None


def test_local_store_stays_inside_its_folder(tmp_path):
    store = LocalStore(tmp_path / "store")
    store.write("papers/x.json", b"{}")
    assert store.read("papers/x.json") == b"{}" and store.read("nope.json") is None
    with pytest.raises(ValueError):
        store.write("../escape.json", b"{}")


def test_site_files_shell_and_catalog():
    files = web.site_files(google_client_id="abc.apps.googleusercontent.com")
    sw = files["sw.js"][0].decode()
    cached = json.loads("[" + re.search(r"var FILES = \[(.*?)\];", sw).group(1).replace("'", '"') + "]")
    assert set(cached) - {"./"} <= set(files)  # 서비스 워커가 캐시하는 파일은 모두 있다
    html = files["index.html"][0].decode()
    cfg = json.loads(re.search(r"window\.NARANI_CFG = (\{.*?\});</script>", html).group(1))
    assert cfg["local"] is False and cfg["google_client_id"].startswith("abc")
    assert cfg["catalogs"]["en"]["새로 고침"] == "Refresh"  # 리더 JS 문구만 실린다
    assert "논문 목록" in cfg["catalogs"]["en"] and len(cfg["catalogs"]["en"]) < 80
    js = files["app.js"][0].decode()
    assert "function pairUp" in js and "function onSentence" in js and "function openPaper" in js
    # 껍데기가 바뀌면 서비스 워커 캐시 이름도 바뀐다
    assert web.site_files(local=True)["sw.js"][0] != files["sw.js"][0]


def test_preview_server_serves_app_and_data(tmp_path):
    wd = _wd(tmp_path, "bots")
    store_dir = tmp_path / "store"
    sync.push([wd.root], LocalStore(store_dir))
    srv, _th = web.serve_in_background(store_dir)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        page = urllib.request.urlopen(base).read().decode()
        assert '"local": true' in page and "app.js" in page
        ix = json.loads(urllib.request.urlopen(base + "data/index.json").read())
        assert ix["papers"][0]["key"] == "bots"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "data/../../etc/passwd")
    finally:
        srv.shutdown()
        srv.server_close()


def test_preview_server_accepts_phone_records_only(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    srv, _th = web.serve_in_background(store_dir)
    base = f"http://127.0.0.1:{srv.server_address[1]}/data/"

    def put(name: str, body: bytes) -> int:
        req = urllib.request.Request(base + name, data=body, method="PUT",
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        assert put("mobile/abc.json", b'{"ops": []}') == 204
        assert (store_dir / "mobile" / "abc.json").read_bytes() == b'{"ops": []}'
        assert put("index.json", b"{}") == 403          # 데스크톱이 쓰는 파일은 못 고친다
        assert put("mobile/../index.json", b"{}") == 403
        assert put("mobile/bad.json", b"not json") == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_phone_app_has_notes_and_mobile_html_does_not():
    js = web.site_files(local=True)["app.js"][0].decode()
    assert "function sentenceActs" in js and "function notesOpen" in js
    from narani.ui import mobile

    assert "function sentenceActs" not in mobile._JS  # 한 장 HTML은 저장할 곳이 없어 하이라이트를 받지 않는다

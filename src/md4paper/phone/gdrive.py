"""Google Drive 저장소 — 사용자 Drive의 `Narani` 폴더(앱이 만든 폴더)를 앱 폴더로 쓴다.

권한은 `drive.file`(앱이 만든 파일만) 하나 — 사용자의 다른 Drive 파일은 보지도 못한다. Google 분류로
non-sensitive라 보안 심사가 없다. 폰 리더(§phone.web)는 같은 Cloud 프로젝트의 **웹** 클라이언트로 같은
폴더를 읽는다(drive.file 권한은 프로젝트 단위로 공유된다는 전제 — `phone check-drive`로 확인).

연결(데스크톱): 설치형 앱 흐름 — 127.0.0.1 임시 포트로 돌아오는 OAuth(PKCE). refresh token은
`~/.config/md4paper/google_drive.json`(0600)에 둔다. 클라이언트 ID·secret은 config.toml `[phone]`:
  google_desktop_client_id / google_desktop_client_secret / google_web_client_id
(설치형 앱의 secret은 Google 설명대로 비밀이 아니다 — 그래도 출력에 찍지 않는다.)

이름 → 파일: 폴더 안에 저장소 이름(`index.json`, `papers/….json`) 그대로 평평하게 둔다. 하위 폴더를 만들면
호출이 늘 뿐이다. 이름 → 파일 id 표는 한 번 받아 두고, 없는 이름을 만나면 다시 받는다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

from md4paper import config

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER_NAME = "Narani"
FOLDER_MIME = "application/vnd.google-apps.folder"
APP_PROP = {"narani": "store"}  # 앱 폴더 표시 — 사용자가 이름을 바꿔도 찾는다


class DriveError(RuntimeError):
    pass


class NotConnected(DriveError):
    pass


def client_ids() -> dict[str, str]:
    """config.toml [phone]의 클라이언트 값들 (없으면 빈 문자열)."""
    sec = config.load_config().get("phone", {})
    return {k: str(sec.get(k) or "").strip() for k in
            ("google_desktop_client_id", "google_desktop_client_secret", "google_web_client_id")}


def token_path() -> Path:
    return config.CONFIG_DIR / "google_drive.json"


def load_token() -> dict | None:
    try:
        tok = json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return tok if isinstance(tok, dict) and tok.get("refresh_token") else None


def save_token(tok: dict) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(tok), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def authorize(client_id: str, client_secret: str, *, open_url: Callable[[str], object] = webbrowser.open,
              timeout: float = 300, http: httpx.Client | None = None) -> dict:
    """브라우저에서 Google 로그인·동의 → 토큰을 받아 저장. 반환: 저장한 토큰 dict."""
    if not client_id or not client_secret:
        raise DriveError("config.toml [phone]에 google_desktop_client_id·google_desktop_client_secret이 없습니다.")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if "code" not in q and "error" not in q:  # favicon 등
                self.send_error(404)
                return
            got.update(q)
            ok = "code" in q and q.get("state") == state
            body = ("<!doctype html><meta charset=utf-8><title>Narani</title><p style='font:16px sans-serif;"
                    "margin:3em'>" + ("Narani: Google Drive에 연결했습니다. 이 창을 닫아도 됩니다." if ok
                                      else "Narani: 연결하지 못했습니다. 앱으로 돌아가 다시 시도하세요.") + "</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            done.set()

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent", "state": state})
        open_url(url)
        if not done.wait(timeout):
            raise DriveError("시간 안에 Google 로그인을 마치지 못했습니다.")
    finally:
        srv.shutdown()
        srv.server_close()
    if got.get("state") != state:
        raise DriveError("돌아온 응답의 state가 다릅니다 — 다시 시도하세요.")
    if "code" not in got:
        raise DriveError(f"Google이 연결을 거절했습니다: {got.get('error', '?')}")
    own = http is None
    http = http or httpx.Client(timeout=30)
    try:
        r = http.post(TOKEN_URL, data={
            "code": got["code"], "client_id": client_id, "client_secret": client_secret,
            "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    finally:
        if own:
            http.close()
    if r.status_code != 200:
        raise DriveError(f"토큰을 받지 못했습니다 ({r.status_code}): {r.text[:200]}")
    data = r.json()
    if not data.get("refresh_token"):
        raise DriveError("refresh token이 오지 않았습니다 — Google 계정의 앱 권한에서 Narani를 지우고 다시 연결하세요.")
    tok = {"client_id": client_id, "refresh_token": data["refresh_token"], "access_token": data.get("access_token", ""),
           "expires_at": time.time() + float(data.get("expires_in") or 0), "scope": data.get("scope", SCOPE)}
    save_token(tok)
    return tok


def forget(http: httpx.Client | None = None) -> bool:
    """연결 끊기 — 토큰을 Google에 반납하고 지운다. Drive의 파일은 그대로 둔다."""
    tok = load_token()
    if not tok:
        return False
    try:
        (http or httpx).post(REVOKE_URL, data={"token": tok["refresh_token"]}, timeout=15)
    except httpx.HTTPError:
        pass  # 오프라인이어도 로컬 토큰은 지운다
    token_path().unlink(missing_ok=True)
    return True


class Credentials:
    """access token을 필요할 때 refresh token으로 새로 받는다."""

    def __init__(self, tok: dict, client_id: str, client_secret: str, http: httpx.Client,
                 persist: bool = True) -> None:
        self.tok, self.client_id, self.client_secret, self.http, self.persist = (
            dict(tok), client_id, client_secret, http, persist)

    def header(self) -> dict[str, str]:
        if not self.tok.get("access_token") or self.tok.get("expires_at", 0) < time.time() + 60:
            self.refresh()
        return {"Authorization": f"Bearer {self.tok['access_token']}"}

    def refresh(self) -> None:
        r = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.tok["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise NotConnected(f"Google Drive 연결이 끊겼습니다 — 다시 연결하세요 ({r.status_code}).")
        data = r.json()
        self.tok["access_token"] = data["access_token"]
        self.tok["expires_at"] = time.time() + float(data.get("expires_in") or 3600)
        if self.persist:
            save_token(self.tok)


class DriveStore:
    """저장소 어댑터(§phone.store) — Drive의 앱 폴더."""

    def __init__(self, creds: Credentials, http: httpx.Client, folder_name: str = FOLDER_NAME) -> None:
        self.creds, self.http, self.folder_name = creds, http, folder_name
        self._folder: str | None = None
        self._ids: dict[str, str] | None = None

    # ---- HTTP ----
    def _req(self, method: str, url: str, **kw: object) -> httpx.Response:
        for attempt in (0, 1):
            headers = {**kw.pop("headers", {}), **self.creds.header()}  # type: ignore[arg-type]
            r = self.http.request(method, url, headers=headers, **kw)  # type: ignore[arg-type]
            if r.status_code == 401 and attempt == 0:
                self.creds.refresh()
                kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            return r
        return r  # pragma: no cover

    def _check(self, r: httpx.Response, what: str) -> httpx.Response:
        if r.status_code >= 400:
            raise DriveError(f"Google Drive {what} 실패 ({r.status_code}): {r.text[:200]}")
        return r

    def _list(self, q: str) -> list[dict]:
        out: list[dict] = []
        page = None
        while True:
            params = {"q": q, "fields": "nextPageToken,files(id,name,modifiedTime)", "pageSize": "1000",
                      "spaces": "drive", "orderBy": "modifiedTime desc"}
            if page:
                params["pageToken"] = page
            data = self._check(self._req("GET", f"{API}/files", params=params), "목록").json()
            out += data.get("files", [])
            page = data.get("nextPageToken")
            if not page:
                return out

    # ---- 앱 폴더 ----
    def folder_id(self) -> str:
        if self._folder:
            return self._folder
        found = self._list(f"mimeType = '{FOLDER_MIME}' and trashed = false and "
                           "appProperties has { key='narani' and value='store' }")
        if not found:  # 표시가 없는 옛 폴더 — 이름으로
            found = self._list(f"mimeType = '{FOLDER_MIME}' and trashed = false and name = '{self.folder_name}'")
        if found:
            self._folder = found[0]["id"]
        else:
            r = self._req("POST", f"{API}/files", params={"fields": "id"},
                          json={"name": self.folder_name, "mimeType": FOLDER_MIME, "appProperties": APP_PROP})
            self._folder = self._check(r, "폴더 만들기").json()["id"]
        return self._folder

    def _load_ids(self) -> dict[str, str]:
        ids: dict[str, str] = {}
        for f in self._list(f"'{self.folder_id()}' in parents and trashed = false"):
            ids.setdefault(f["name"], f["id"])  # 이름이 겹치면 가장 최근 것 (modifiedTime desc)
        self._ids = ids
        return ids

    def _id(self, name: str, reload: bool = False) -> str | None:
        ids = self._ids if (self._ids is not None and not reload) else self._load_ids()
        return ids.get(name)

    # ---- Store ----
    def read(self, name: str) -> bytes | None:
        fid = self._id(name)
        if fid is None:
            fid = self._id(name, reload=True)
            if fid is None:
                return None
        r = self._req("GET", f"{API}/files/{fid}", params={"alt": "media"})
        if r.status_code == 404:
            self._ids = None
            return None
        return self._check(r, "읽기").content

    def write(self, name: str, data: bytes, mime: str = "application/json") -> None:
        fid = self._id(name)
        if fid is not None:
            r = self._req("PATCH", f"{UPLOAD}/files/{fid}", params={"uploadType": "media", "fields": "id"},
                          content=data, headers={"Content-Type": mime})
            if r.status_code != 404:
                self._check(r, "쓰기")
                return
            self._ids = None  # 밖에서 지운 파일 — 새로 만든다
        boundary = "narani" + secrets.token_hex(8)
        meta = json.dumps({"name": name, "parents": [self.folder_id()], "mimeType": mime}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
                f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data + f"\r\n--{boundary}--".encode())
        r = self._req("POST", f"{UPLOAD}/files", params={"uploadType": "multipart", "fields": "id"},
                      content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        new_id = self._check(r, "만들기").json()["id"]
        if self._ids is not None:
            self._ids[name] = new_id

    def delete(self, name: str) -> None:
        fid = self._id(name)
        if fid is None:
            return
        r = self._req("DELETE", f"{API}/files/{fid}")
        if r.status_code not in (204, 404):
            self._check(r, "지우기")
        if self._ids is not None:
            self._ids.pop(name, None)

    def account(self) -> str:
        """연결한 Google 계정(이메일) — 확인용."""
        r = self._req("GET", f"{API}/about", params={"fields": "user(emailAddress)"})
        return self._check(r, "계정 확인").json().get("user", {}).get("emailAddress", "")


def open_store(http: httpx.Client | None = None) -> DriveStore:
    """저장된 연결로 DriveStore를 연다. 연결 전이면 NotConnected."""
    ids = client_ids()
    tok = load_token()
    if not tok:
        raise NotConnected("Google Drive에 연결하지 않았습니다 — `md4paper phone connect`.")
    http = http or httpx.Client(timeout=60)
    creds = Credentials(tok, tok.get("client_id") or ids["google_desktop_client_id"],
                        ids["google_desktop_client_secret"], http)
    return DriveStore(creds, http)

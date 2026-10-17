"""GitHub 기준 자동 업데이트 — 앱을 켤 때마다 새 커밋이 있는지 보고, 누르면 받아서 다시 켠다.

설치 스크립트(install.sh·install.ps1)는 `uv tool install --force "md4paper[...] @ git+<저장소>"`로
깔고, 이때 설치본에는 **어느 커밋을 받았는지**가 남는다(PEP 610 `direct_url.json`의 `commit_id`).
그래서 버전 번호 없이도 'GitHub 브랜치 끝 커밋 ≠ 설치된 커밋'이면 업데이트가 있다고 판단한다.

업데이트는 실행 중인 파이썬 환경을 통째로 갈아엎는 일이라(윈도우는 실행 중인 python.exe를 아예
못 지운다) 앱 안에서 하지 않는다. 떼어 낸 스크립트 하나를 띄우고 앱은 내려간다 — 스크립트는 앱이
끝나기를 기다렸다가 설치 스크립트와 같은 명령으로 다시 깔고, 런처를 새로 만들고, 앱을 다시 연다.

저장소에서 `uv run`으로 띄운 개발본(편집 가능 설치)에는 `commit_id`가 없으므로 검사하지 않는다 —
그쪽은 `git pull`이 업데이트다. `MD4PAPER_NO_UPDATE_CHECK=1`이면 검사를 끈다.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

DIST = "md4paper"
EXTRAS = "ui,native"
API = "https://api.github.com"
LOG_NAME = "md4paper-update.log"


@dataclass
class Source:
    """설치본이 어디서 왔는지 — GitHub 저장소 + (지정했다면) 브랜치/태그 + 받은 커밋."""

    owner: str
    repo: str
    commit: str
    ref: str | None = None  # None이면 기본 브랜치

    @property
    def git_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def spec(self) -> str:
        """설치 스크립트와 같은 요구 사항 문자열."""
        rev = f"@{self.ref}" if self.ref else ""
        return f"{DIST}[{EXTRAS}] @ git+{self.git_url}{rev}"


@dataclass
class Update:
    """올라온 새 버전 — current..latest 사이 커밋 요약(최근 것 먼저)."""

    current: str
    latest: str
    ahead: int = 0  # 몇 커밋 앞섰나 (비교 실패 시 0 — 그래도 업데이트는 있다)
    messages: list[str] = field(default_factory=list)


def _parse_github(url: str) -> tuple[str, str] | None:
    """`https://github.com/owner/repo(.git)` → (owner, repo)."""
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(prefix):
            parts = url[len(prefix):].strip("/").split("/")
            if len(parts) >= 2 and parts[0] and parts[1]:
                return parts[0], parts[1].removesuffix(".git")
    return None


def source_from_direct_url(raw: str | None) -> Source | None:
    """`direct_url.json` 내용 → Source. git+GitHub 설치가 아니면 None."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    vcs = data.get("vcs_info") or {}
    if vcs.get("vcs") != "git" or not vcs.get("commit_id"):
        return None
    gh = _parse_github(str(data.get("url", "")).removeprefix("git+"))
    if gh is None:
        return None
    return Source(owner=gh[0], repo=gh[1], commit=vcs["commit_id"], ref=vcs.get("requested_revision"))


def installed_source() -> Source | None:
    """지금 돌고 있는 md4paper가 GitHub에서 uv tool로 깐 것이면 그 출처. 아니면 None(검사 안 함)."""
    if os.environ.get("MD4PAPER_NO_UPDATE_CHECK"):
        return None
    try:
        raw = metadata.distribution(DIST).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    src = source_from_direct_url(raw)
    if src is None or not (Path(sys.prefix) / "uv-receipt.toml").exists():
        return None  # uv tool 설치본이 아니면 다시 깔 방법을 모른다
    return src


def version_label() -> str:
    """홈 화면에 보일 버전 — `v0.1.0 · edfaf79`(설치본) / `v0.1.0 · 개발본 edfaf79`(저장소에서 실행)."""
    from md4paper import __version__
    from md4paper.config import _project_root

    label = f"v{__version__}"
    try:
        src = source_from_direct_url(metadata.distribution(DIST).read_text("direct_url.json"))
    except metadata.PackageNotFoundError:
        src = None
    if src is not None:
        return f"{label} · {src.commit[:7]}"
    root = _project_root()
    if root is not None and (root / ".git").exists():
        try:
            sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"],
                                 capture_output=True, text=True, check=False, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            sha = ""
        return f"{label} · 개발본 {sha}".rstrip()
    return label


def check(src: Source, client=None, timeout: float = 6.0) -> Update | None:  # noqa: ANN001 — httpx.Client
    """GitHub에 새 커밋이 있으면 Update, 최신이면 None. 네트워크 오류는 그대로 올린다(호출부가 삼킨다)."""
    import httpx

    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True,
                                    headers={"User-Agent": "md4paper-updater"})
    try:
        base = f"{API}/repos/{src.owner}/{src.repo}"
        r = client.get(f"{base}/commits/{src.ref or 'HEAD'}",
                       headers={"Accept": "application/vnd.github.sha"})
        r.raise_for_status()
        latest = r.text.strip()
        if not latest or latest == src.commit:
            return None
        upd = Update(current=src.commit, latest=latest)
        # 무엇이 바뀌었는지는 덤 — 비교가 안 되면(강제 푸시로 옛 커밋이 사라짐 등) 목록 없이 알린다
        try:
            c = client.get(f"{base}/compare/{src.commit}...{latest}")
            if c.status_code == 200:
                data = c.json()
                if data.get("status") == "behind":
                    return None  # 설치본이 더 앞서 있다 (다른 브랜치에서 깐 경우 등)
                upd.ahead = int(data.get("ahead_by") or 0)
                upd.messages = [((x.get("commit") or {}).get("message") or "").split("\n", 1)[0]
                                for x in reversed(data.get("commits") or [])]
        except (httpx.HTTPError, ValueError):
            pass
        return upd
    finally:
        if own:
            client.close()


# --- 적용 (다시 깔고 다시 켜기) ---------------------------------------------


def find_uv() -> str | None:
    """uv 실행 파일. Finder·시작 메뉴로 띄운 앱은 PATH가 빈약해 설치 위치를 직접 본다."""
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    names = ("uv.exe",) if sys.platform.startswith("win") else ("uv",)
    for d in (home / ".local" / "bin", home / ".cargo" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        for n in names:
            if (d / n).is_file():
                return str(d / n)
    return None


def log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / LOG_NAME
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache"))
    return base / "md4paper" / LOG_NAME


def _relaunch_posix() -> str:
    """다시 켜는 셸 명령 — 맥은 설치된 .app으로(Dock 아이콘·이름 유지), 아니면 같은 명령을 떼어서."""
    from md4paper import launcher

    if sys.platform == "darwin":
        app = launcher.default_location()
        return f"open {shlex.quote(str(app))}"
    cmd = " ".join(shlex.quote(p) for p in launcher.launch_command())
    return f"nohup {cmd} >/dev/null 2>&1 &"


def posix_script(src: Source, uv: str, pid: int, log: Path, relaunch: bool = True) -> str:
    """앱(pid)이 끝나길 기다렸다가 다시 깔고, 런처를 새로 만들고, 다시 켜는 sh 스크립트."""
    q = shlex.quote
    exe = q(str(Path(sys.prefix) / "bin" / DIST))
    fail_alert = ""
    if sys.platform == "darwin":
        fail_alert = ("osascript -e 'display alert \"md4paper 업데이트 실패\" message "
                      f"\"로그: {log}\" as critical' >/dev/null 2>&1")
    return f"""#!/bin/sh
# md4paper 업데이트 — 앱이 띄운 일회용 스크립트
exec >>{q(str(log))} 2>&1
printf '\\n=== %s 업데이트 ===\\n' "$(date)"
i=0
while kill -0 {pid} 2>/dev/null && [ $i -lt 120 ]; do sleep 0.5; i=$((i+1)); done
if {q(uv)} tool install --force {q(src.spec)}; then
  {exe} app || true
else
  {fail_alert}
fi
{_relaunch_posix() if relaunch else ':'}
"""


def windows_script(src: Source, uv: str, pid: int, log: Path, relaunch: bool = True) -> str:
    """윈도우판 — PowerShell. 실행 중인 python.exe는 잠겨 있으므로 반드시 끝난 뒤에 깐다."""
    from md4paper import launcher

    def ps(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    exe = Path(sys.prefix) / "Scripts" / f"{DIST}.exe"
    lines = [
        f"Start-Transcript -Append -Path {ps(str(log))} | Out-Null",
        f"try {{ Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue }} catch {{}}",
        f"& {ps(uv)} tool install --force {ps(src.spec)}",
        f"if ($LASTEXITCODE -eq 0) {{ & {ps(str(exe))} app }}",
    ]
    if relaunch:
        lines.append(f"Start-Process {ps(str(launcher.default_location()))}")
    lines.append("Stop-Transcript | Out-Null")
    return "\n".join(lines) + "\n"


def spawn_update(src: Source, relaunch: bool = True) -> Path:
    """업데이트 스크립트를 이 프로세스와 떨어뜨려 띄운다. 호출한 쪽은 곧바로 종료해야 한다.

    반환값은 로그 경로. uv가 없으면 RuntimeError.
    """
    uv = find_uv()
    if uv is None:
        raise RuntimeError("uv를 찾지 못했습니다 — 설치 스크립트를 다시 실행해 업데이트하세요.")
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    if sys.platform.startswith("win"):
        script = Path(tempfile.gettempdir()) / "md4paper-update.ps1"
        script.write_text(windows_script(src, uv, pid, log, relaunch), encoding="utf-8-sig")
        powershell = shutil.which("powershell") or "powershell.exe"
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-File", str(script)], creationflags=flags, close_fds=True)
    else:
        script = Path(tempfile.gettempdir()) / f"md4paper-update-{pid}.sh"
        script.write_text(posix_script(src, uv, pid, log, relaunch), encoding="utf-8")
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return log

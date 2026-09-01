#!/bin/sh
# md4paper 설치 (macOS · Linux) — uv를 준비하고, md4paper를 설치하고, 앱 아이콘을 등록한다.
#
#   curl -LsSf https://raw.githubusercontent.com/wooogler/md4paper/main/install.sh | sh
#
# 같은 줄을 다시 실행하면 최신 버전으로 업데이트된다(`--force`가 git을 다시 읽는다).
# 포크·브랜치에서 설치하려면:
#   MD4PAPER_SPEC='md4paper[ui,native] @ git+https://github.com/나/md4paper@브랜치' sh install.sh
#
# 앱은 사용자 컴퓨터에서 조립되므로 서명·공증된 설치 파일이 필요 없다(Gatekeeper 경고도 없다).
set -eu

SPEC="${MD4PAPER_SPEC:-md4paper[ui,native] @ git+https://github.com/wooogler/md4paper}"

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31m실패:\033[0m %s\n' "$1" >&2; exit 1; }

if ! command -v uv >/dev/null 2>&1; then
    step "uv 설치 (파이썬까지 알아서 받아오는 패키지 관리자)"
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv 설치에 실패했습니다."
    # 방금 설치한 uv는 이 셸의 PATH에 아직 없다 — 설치 위치를 직접 잡아 준다.
    for dir in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -x "$dir/uv" ]; then
            PATH="$dir:$PATH"
            export PATH
        fi
    done
fi
command -v uv >/dev/null 2>&1 || die "uv를 찾지 못했습니다. 터미널을 새로 열고 다시 실행하세요."

step "md4paper 설치 (의존성 약 1.3GB — 처음에는 몇 분 걸립니다)"
uv tool install --force "$SPEC" || die "설치에 실패했습니다. 위 오류 메시지를 확인하세요."

MD4PAPER="$(uv tool dir)/md4paper/bin/md4paper"
[ -x "$MD4PAPER" ] || die "설치된 실행 파일을 찾지 못했습니다: $MD4PAPER"

step "앱 아이콘 등록"
"$MD4PAPER" app

step "끝났습니다"
if [ "$(uname -s)" = "Darwin" ]; then
    printf '  Launchpad·Spotlight에서 %s를 여세요.\n' "'md4paper'"
    printf '  Dock에 두려면: 실행 중일 때 Dock 아이콘 우클릭 → 옵션 → Dock에 유지\n'
else
    printf '  앱 메뉴에서 %s를 여세요.\n' "'md4paper'"
    printf '  앱 창이 안 뜨고 브라우저가 열리면 웹뷰 라이브러리가 없는 것입니다:\n'
    printf '    sudo apt install -y gir1.2-webkit2-4.1 python3-gi   # Debian/Ubuntu\n'
fi
printf '  첫 변환 때 추출 모델(약 1.1GB)을 한 번 내려받습니다.\n'
printf '  번역·인용에는 LLM API 키가 필요합니다 — 홈 화면 왼쪽 %s에 붙여넣으세요.\n' "'AI 설정'"
printf '  제거: "%s" app --remove && uv tool uninstall md4paper\n' "$MD4PAPER"

# md4paper 설치 (Windows PowerShell) — uv를 준비하고, md4paper를 설치하고, 시작 메뉴에 등록한다.
#
#   irm https://raw.githubusercontent.com/wooogler/md4paper/main/install.ps1 | iex
#
# 같은 줄을 다시 실행하면 최신 버전으로 업데이트된다(`--force`가 git을 다시 읽는다).
# 포크·브랜치에서 설치하려면 먼저:
#   $env:MD4PAPER_SPEC = 'md4paper[ui,native] @ git+https://github.com/나/md4paper@브랜치'
#
# 앱은 사용자 컴퓨터에서 조립되므로 서명된 설치 파일이 필요 없다(SmartScreen 경고도 없다).

$ErrorActionPreference = 'Stop'

$spec = if ($env:MD4PAPER_SPEC) { $env:MD4PAPER_SPEC }
        else { 'md4paper[ui,native] @ git+https://github.com/wooogler/md4paper' }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Blue }
function Die($msg) { Write-Host "`n실패: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Step 'uv 설치 (파이썬까지 알아서 받아오는 패키지 관리자)'
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # 방금 설치한 uv는 이 세션의 PATH에 아직 없다
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Die 'uv를 찾지 못했습니다. PowerShell을 새로 열고 다시 실행하세요.'
}

Step 'md4paper 설치 (의존성 약 1.3GB — 처음에는 몇 분 걸립니다)'
uv tool install --force "$spec"
if ($LASTEXITCODE -ne 0) { Die '설치에 실패했습니다. 위 오류 메시지를 확인하세요.' }

$md4paper = Join-Path (uv tool dir) 'md4paper\Scripts\md4paper.exe'
if (-not (Test-Path $md4paper)) { Die "설치된 실행 파일을 찾지 못했습니다: $md4paper" }

Step '시작 메뉴·바탕화면에 바로가기 등록'
& $md4paper app --desktop

Step '끝났습니다'
Write-Host "  시작 메뉴나 바탕화면에서 'md4paper'를 여세요."
Write-Host '  첫 변환 때 추출 모델(약 1.1GB)을 한 번 내려받습니다.'
Write-Host "  번역·인용에는 LLM API 키가 필요합니다 — 홈 화면 왼쪽 'AI 설정'에 붙여넣으세요."
Write-Host "  제거: & '$md4paper' app --remove; uv tool uninstall md4paper"

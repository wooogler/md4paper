"""로컬 웹 UI (NiceGUI) — 섹션 트리 리뷰 + 프리뷰 + PDF 대조 + 설정.

CLI와 같은 아티팩트(sections.yaml 등)를 읽고 쓴다. 항상 떠 있는 서버가 아니라
`md4paper ui WORKDIR`로 필요할 때 띄우는 로컬 앱이다.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from md4paper import config, library
from md4paper.ir import Flavor, GlossaryEntry
from md4paper.ui import desktop
from md4paper.ui.controller import LEVEL_OPTIONS, RUNIN_LEVEL_OPTIONS, UIController
from md4paper.workdir import WorkDir

# 설명이 붙은 옵션 (값 → 사람이 이해하기 쉬운 라벨)
_KSTYLE = {"해라체": "해라체 (~한다 · 논문 표준)", "합니다체": "합니다체 (~합니다 · 경어)", "해요체": "해요체 (~해요 · 부드럽게)"}
# 마크다운 내보내기 형식 — 다운로드 zip에 적용 (설명 없이 이름만)
_EXPORT_TARGET = {"universal": "범용", "notion": "Notion", "obsidian": "Obsidian"}
# AI 서비스 — 비전문가용 이름 + 키 형식 힌트
_PROVIDER_NAME = {
    "openai": "OpenAI (ChatGPT 계열)",
    "anthropic": "Anthropic (Claude 계열)",
    "gemini": "Google (Gemini 계열)",
}
_KEY_HINT = {"openai": "sk-proj-... 또는 sk-...", "anthropic": "sk-ant-...", "gemini": "AIza..."}
_TIER_LABEL = ("저렴 · 빠름", "중간", "비쌈 · 고성능")


def _short_provider(p: str) -> str:
    return _PROVIDER_NAME.get(p, p).split(" ")[0]


def _model_options(provider: str) -> dict:
    """제공사의 모델을 '가격 tier — 모델명 · $입력/$출력' 라벨로 (저렴→비쌈)."""
    from md4paper.llm.base import PRICING

    opts: dict[str, str] = {}
    for i, mid in enumerate(config.MODEL_TIERS.get(provider, ())):
        pr = PRICING.get(mid)
        price = f" · ${pr[0]:g}/${pr[1]:g} (100만 토큰)" if pr else ""
        opts[mid] = f"{_TIER_LABEL[i]} — {mid}{price}"
    return opts


_CAPSTYLE_NAME = {"bold-italic": "굵은 라벨 + 이탤릭", "blockquote": "인용구", "italic": "이탤릭만"}
# 마크다운 문법 대신 '렌더 결과'를 그대로 보여주는 미리보기 (직관적 선택)
_CAPSTYLE_EX = {
    "bold-italic": "<b>Figure 1:</b> <i>예시 캡션</i>",
    "blockquote": '<span style="border-left:3px solid currentColor;padding-left:8px;opacity:.9">'
                  "<b>Figure 1:</b> 예시 캡션</span>",
    "italic": "<i>Figure 1: 예시 캡션</i>",
}
_RUNIN = {
    "off": "끄기 — 본문 그대로",
    "header": "헤더로 승격 — ### 4.1.3 제목",
    "italic": "이탤릭 표기 — *4.1.3 제목.*",
}
_POLICY = {
    "translate": "번역 (의미로)",
    "transliterate": "음역 (소리로)",
    "keep": "원어 유지",
    "병기-first-use": "첫 등장 시 원어 병기",
}

# 헤더 레벨 드롭다운 — 내부 값(int/문자)은 그대로 두고 표시만 결과 중심의 한국어로.
# (기존엔 skip/merge-up/drop 같은 영어 내부값이 그대로 노출돼 뜻이 안 와닿았다.)
_LEVEL_LABEL = {
    1: "제목 1", 2: "제목 2", 3: "제목 3", 4: "제목 4", 5: "제목 5", 6: "제목 6",
    "italic": "기울임",       # run-in 소제목을 기울임 문단으로
    "skip": "본문으로",       # 헤더 해제 — 제목 텍스트는 일반 문단으로 남김
    "merge-up": "위에 합침",  # 제목 줄을 없애고 그 내용을 위 섹션에 합침
    "drop": "통째 삭제",      # 제목 + 본문을 통째로 제거
}


def _level_opts(runin: bool) -> dict:
    """레벨 선택 옵션 {값: 한국어 라벨}. 값은 controller의 정본 목록을 그대로 쓴다."""
    src = RUNIN_LEVEL_OPTIONS if runin else LEVEL_OPTIONS
    return {v: _LEVEL_LABEL.get(v, str(v)) for v in src}

# Notion 느낌의 프리뷰 스타일
_PREVIEW_CSS = """
.md-preview { max-width: 820px; margin: 0 auto; padding: 8px 16px 64px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
  color: #37352f; line-height: 1.7; font-size: 16px; }
.md-preview h1 { font-size: 1.9em; font-weight: 700; margin: 1.4em 0 0.4em; line-height: 1.3; }
.md-preview h2 { font-size: 1.5em; font-weight: 650; margin: 1.3em 0 0.3em; }
.md-preview h3 { font-size: 1.2em; font-weight: 600; margin: 1.1em 0 0.3em; }
/* h4~h6: 명시적 font-size가 없으면 브라우저/Quasar 기본값으로 떨어져 본문과 구분이 안 되거나
   본문보다 작아진다 → 단계별 크기 + h5·h6는 대문자/옅은 색으로 헤더임을 명확히. */
.md-preview h4 { font-size: 1.08em; font-weight: 650; margin: 1.1em 0 0.3em; }
.md-preview h5 { font-size: 0.96em; font-weight: 700; margin: 1em 0 0.25em;
  text-transform: uppercase; letter-spacing: 0.04em; }
.md-preview h6 { font-size: 0.9em; font-weight: 700; margin: 1em 0 0.25em;
  text-transform: uppercase; letter-spacing: 0.04em; color: #6b6b6b; }
.md-preview p { margin: 0.5em 0; }
.md-preview a { color: #2383e2; text-decoration: none; border-bottom: 1px solid rgba(35,131,226,.3); }
.md-preview a:hover { border-bottom-color: #2383e2; }
.md-preview code { background: #f1f0ee; border-radius: 4px; padding: 0.15em 0.35em;
  font-family: 'SFMono-Regular', ui-monospace, Menlo, monospace; font-size: 0.88em; color: #eb5757; }
/* 코드 블록: 긴 줄이 오른쪽으로 넘쳐 잘리지 않도록 줄바꿈(pre-wrap). 실제 줄바꿈은 보존하고
   과도하게 긴 한 줄만 접는다. (추출기가 코드 줄바꿈을 잃은 경우에도 전체 내용이 보인다.) */
.md-preview pre { background: #f7f6f3; border-radius: 8px; padding: 14px 16px;
  white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
.md-preview pre code { background: none; color: inherit; padding: 0;
  white-space: pre-wrap; overflow-wrap: anywhere; }
.md-preview blockquote { border-left: 3px solid #37352f; margin: 0.8em 0; padding: 0.1em 0 0.1em 0.9em; color: #5b5851; }
.md-preview img { max-width: 100%; border-radius: 6px; margin: 0.6em 0; cursor: zoom-in; }
.md-preview table { border-collapse: collapse; margin: 0.8em 0; display: block; overflow-x: auto; }
.md-preview th, .md-preview td { border: 1px solid #e6e4e0; padding: 7px 12px; text-align: left; }
.md-preview th { background: #f7f6f3; font-weight: 600; }
.md-preview hr { border: none; border-top: 1px solid #e6e4e0; margin: 1.4em 0; }
.md-preview ul, .md-preview ol { padding-left: 1.5em; margin: 0.4em 0; }
.md-preview li { margin: 0.2em 0; }
/* 인용 링크 — 점선 밑줄 + 도움 커서로 호버 가능함을 암시.
   nowrap을 주면 긴 조합 인용이 통째로 다음 줄로 넘어가 앞 줄이 일찍 끊긴다 →
   기본(공백에서 줄바꿈 허용)으로 두어 줄을 꽉 채운다. 짧은 [54]는 공백이 없어 어차피 안 쪼개짐. */
.md-preview a[href^="#ref-"] { border-bottom: 1px dotted rgba(35,131,226,.55); cursor: pointer;
  text-decoration: none; }
.md-preview a[href^="#ref-"]:hover { border-bottom-color: #2383e2; }
/* 인용 바로 아래에 고정 렌더되는 서지정보 툴팁 (position:fixed → 스크롤 컨테이너에 안 잘림).
   호버 미리보기 땐 pointer-events:none, 클릭해 고정(pinned)하면 auto로 바뀌어 텍스트 선택·DOI 클릭 가능. */
#md-cite-tip { position: fixed; z-index: 10000; max-width: 460px;
  background: #1f2430; color: #f4f4f5; font-size: 12.5px; line-height: 1.5;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
  padding: 9px 26px 9px 12px; border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.28);
  pointer-events: none; opacity: 0; white-space: pre-wrap; word-break: break-word;
  user-select: text; transition: opacity .12s ease; }
/* padding-right(26px)는 고정 전에도 항상 확보 → 핀 아이콘이 생겨도 레이아웃이 안 흔들린다 */
#md-cite-tip.pinned { box-shadow: 0 0 0 1px rgba(123,183,255,.5), 0 8px 28px rgba(0,0,0,.35);
  min-height: 46px; }  /* 압정+복사 아이콘 2개가 세로로 들어갈 최소 높이 */
#md-cite-tip a.md-cite-doi { color: #7bb7ff; text-decoration: underline; word-break: break-all; }
#md-cite-tip .md-cite-pin { position: absolute; top: 6px; right: 7px; line-height: 0; opacity: .7; }
/* 제목 복사 아이콘 — 압정 바로 아래 */
#md-cite-tip .md-cite-copy { position: absolute; top: 27px; right: 5px; line-height: 0;
  padding: 2px; border: none; background: none; color: inherit; cursor: pointer; opacity: .55; }
#md-cite-tip .md-cite-copy:hover { opacity: 1; }
#md-cite-tip .md-cite-copy.copied { opacity: 1; color: #34c759; }
#md-cite-tip .md-cite-copy.copied::after { content: '제목 복사됨'; position: absolute; right: 100%;
  margin-right: 6px; top: 50%; transform: translateY(-50%); font-size: 10.5px; line-height: 1.4;
  background: rgba(0,0,0,.72); color: #fff; padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
@media (prefers-color-scheme: dark) { #md-cite-tip a.md-cite-doi { color: #2c62b8; } }
/* 섹션 제목: 기본은 …로 잘리고, 행을 호버하면 전체가 여러 줄로 펼쳐짐 (버튼·배지는 오른쪽 유지) */
/* 레벨 드롭다운을 더 낮게 (섹션 트리·일괄 조정) */
.md4-lvl.q-field--dense .q-field__control, .md4-lvl.q-field--dense .q-field__marginal {
  min-height: 30px; height: 30px; }
.md4-lvl .q-field__native { min-height: 30px; padding: 0; font-size: 13px; }
.md4-lvl .q-field__append { height: 30px; }
/* 일괄 레벨 조정: 그룹을 펼쳤을 때 개별 섹션 들여쓰기를 줄인다 (그룹 셀렉트 폭만큼 왼쪽으로 당김) */
.md4-batch-exp .q-expansion-item__content { padding-left: 0; margin-left: -100px; }
/* 변환 탭 보기 전환 세그먼트 — 둥근 테두리 + 넉넉한 패딩 (촘촘하지 않게) */
.conv-viewtoggle { border: 1px solid rgba(0,0,0,.14); border-radius: 9px; overflow: hidden; }
.conv-viewtoggle .q-btn { padding: 4px 16px; min-height: 32px; font-weight: 500; }
.conv-viewtoggle .q-btn + .q-btn { border-left: 1px solid rgba(0,0,0,.10); }
@media (prefers-color-scheme: dark) {
  .conv-viewtoggle { border-color: rgba(255,255,255,.18); }
  .conv-viewtoggle .q-btn + .q-btn { border-left-color: rgba(255,255,255,.14); }
}
/* 각주 위첨자 링크 — 본문 인용과 같은 톤(작고 파랗게), 밑줄 없음, 호버 시 툴팁 */
sup.md-fn { line-height: 0; }
sup.md-fn a { color: #2383e2; text-decoration: none; font-weight: 500; cursor: pointer; padding: 0 1px; }
sup.md-fn a:hover { text-decoration: underline; }
/* 마크다운 헤더 옆 '섹션 편집' 점프 버튼 — 라벨 있는 알약, 헤더 호버 시 또렷하게 표시 */
.md-preview .md-sec-jump { font-size: 0.72rem; font-weight: 500; line-height: 1.4; text-decoration: none;
  color: #5b6b7d; background: rgba(35,131,226,.10); border: 1px solid rgba(35,131,226,.28);
  border-radius: 999px; padding: 1px 9px; margin-left: 10px; vertical-align: middle; white-space: nowrap;
  cursor: pointer; opacity: 0; transition: opacity .12s, background .12s, color .12s; }
.md-preview h1:hover .md-sec-jump, .md-preview h2:hover .md-sec-jump, .md-preview h3:hover .md-sec-jump,
.md-preview h4:hover .md-sec-jump, .md-preview h5:hover .md-sec-jump, .md-preview h6:hover .md-sec-jump { opacity: .9; }
.md-preview .md-sec-jump:hover { opacity: 1; background: rgba(35,131,226,.20); color: #2383e2; }
@media (prefers-color-scheme: dark) {
  .md-preview .md-sec-jump { color: #9db2c8; background: rgba(90,150,230,.16); border-color: rgba(90,150,230,.35); }
  .md-preview .md-sec-jump:hover { color: #7bb7ff; background: rgba(90,150,230,.28); }
}
/* 섹션 트리 행 하이라이트 — 점프 시 잠깐 반짝 */
@keyframes sectreeflash { 0%, 35% { background: rgba(35,131,226,.30); } 100% { background: transparent; } }
.sectree-flash { animation: sectreeflash 1.6s ease-out; border-radius: 6px; }
.section-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.group:hover .section-title { white-space: normal; overflow: visible; text-overflow: clip; }
/* 번역 중 섹션 아이콘(primary 색 autorenew)만 회전 — 체크박스·완료·실패 아이콘은 정지.
   .q-tree__icon는 노드 아이콘 전용 클래스라 tick 체크박스와 겹치지 않는다. */
@keyframes md4spin { to { transform: rotate(360deg); } }
.q-tree__icon.text-primary { animation: md4spin 0.9s linear infinite; transform-origin: center; }
/* 용어집 상단(설명·버튼·진행바) 고정 — 용어 행을 스크롤해도 번역 실행 버튼과 진행 상황이 계속 보인다.
   배경을 불투명하게 깔아야 아래 행이 비쳐 보이지 않는다. */
.gloss-sticky { position: sticky; top: 0; z-index: 5; background: #ffffff;
  padding: 4px 0 8px; border-bottom: 1px solid #e6e4e0; }
@media (prefers-color-scheme: dark) {
  .gloss-sticky { background: #121212; border-bottom-color: #3a3a3a; }
}
/* 뷰어 셸 — 접이식 목차 + 토글 가능한 원문/번역/PDF 패널 + 리사이즈.
   .vshell은 부모 flex-column의 남은 높이를 채운다(툴바 아래 전체). */
.vshell { display: flex; flex: 1 1 auto; min-height: 0; width: 100%; }
.vtoc { flex: 0 0 var(--vtoc-w, 230px); width: var(--vtoc-w, 230px); height: 100%; min-height: 0;
  overflow-y: auto; overscroll-behavior: contain; background: #fafafa; padding: 6px 4px 40px; }
.vtoc-title { font-size: 11px; font-weight: 600; color: #9a968e; padding: 2px 8px 4px; letter-spacing: .04em; }
.vtoc-item { display: block; padding: 3px 8px; border-radius: 6px; font-size: 13px; color: #5b5851;
  cursor: pointer; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vtoc-item:hover { background: #ececec; color: #37352f; }
/* 목차 너비 드래그 핸들 (목차와 본문 사이) */
.vtoc-handle { flex: 0 0 7px; cursor: col-resize; position: relative; }
.vtoc-handle::after { content: ''; position: absolute; left: 3px; top: 0; bottom: 0; width: 1px; background: #e6e4e0; }
.vtoc-handle:hover::after { background: #2383e2; width: 2px; }
.vmain { flex: 1 1 auto; min-width: 0; height: 100%; }
.vwrap { position: relative; height: 100%; min-width: 0; }        /* 정렬 그리드 + 컬럼 드래그 핸들 */
.vcol-handle { position: absolute; top: 0; bottom: 0; width: 10px; margin-left: -5px; cursor: col-resize; z-index: 3; }
.vcol-handle::after { content: ''; position: absolute; left: 5px; top: 0; bottom: 0; width: 1px; background: #e6e4e0; }
.vcol-handle:hover::after { background: #2383e2; width: 2px; }
.vpdf { height: 100%; overflow-y: auto; overscroll-behavior: contain; padding: 6px 10px; background: #f4f3f1; }
.vpane { height: 100%; overflow-y: auto; overscroll-behavior: contain; }  /* 정렬 불가 시 단일 마크다운 패널 */
.conv-md { height: 100%; }  /* 변환 탭 마크다운 스크롤 패널 (마크다운↔PDF 싱크 컨테이너) */
/* 정렬 그리드 — 한 행의 두 셀이 같은 grid row라 높이가 큰 쪽에 맞춰져 스크롤이 안 어긋난다.
   컬럼 수/비율은 인라인 --sbs-cols로 지정(원문/번역 토글·드래그). */
.sbs-grid { display: grid; grid-template-columns: var(--sbs-cols, 1fr 1fr); align-content: start;
  overflow-y: auto; overscroll-behavior: contain; height: 100%; }
.sbs-cell { min-width: 0; padding: 1px 22px 8px; overflow-wrap: break-word; word-break: break-word; }
.sbs-cell-div { border-right: 1px solid #e6e4e0; }
.sbs-row-sep { border-top: 1px solid #ecebe8; padding-top: 10px; }  /* 섹션 경계 구분선 */
.sbs-md { max-width: none !important; margin: 0 !important; padding: 0 !important; }
.sbs-md > *:first-child { margin-top: 0 !important; }
/* 뷰어 툴바 토글 칩 */
.vchip { cursor: pointer; }
/* 그림 확대 뷰어(라이트박스) — 본문 그림을 클릭하면 화면 전체에 띄우고 휠·버튼으로 확대.
   position:fixed + 높은 z-index(인용 툴팁 10000보다 위)라 스크롤 컨테이너 안에서도 잘리지 않는다. */
#md-img-zoom { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 10001; display: none;
  align-items: center; justify-content: center; overflow: hidden; background: rgba(18,18,20,.93); }
#md-img-zoom.open { display: flex; }
/* 위 52px은 도구막대, 아래 52px은 캡션 자리 — 그림이 그 위로 올라타지 않게 비워 둔다 */
#md-img-zoom img { max-width: calc(100vw - 64px); max-height: calc(100vh - 104px);
  background: #fff; border-radius: 4px;
  box-shadow: 0 12px 44px rgba(0,0,0,.55); transform-origin: center center; will-change: transform;
  cursor: grab; user-select: none; -webkit-user-drag: none; }
#md-img-zoom.dragging img { cursor: grabbing; }
#md-img-zoom .mdz-bar { position: absolute; top: 14px; right: 16px; display: flex; align-items: center;
  gap: 2px; padding: 3px 5px; border-radius: 10px; background: rgba(0,0,0,.55); }
#md-img-zoom .mdz-bar button { display: flex; align-items: center; justify-content: center;
  width: 28px; height: 26px; border: none; background: none; color: #e8e8e8;
  font-size: 15px; line-height: 1; border-radius: 7px; cursor: pointer; }
#md-img-zoom .mdz-bar button:hover { background: rgba(255,255,255,.16); }
#md-img-zoom .mdz-pct { min-width: 48px; text-align: center; color: #cfcfcf; font-size: 12px;
  font-variant-numeric: tabular-nums; }
/* 캡션(alt) + 조작 힌트 — 그림 아래 가운데 */
#md-img-zoom .mdz-foot { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
  max-width: 78vw; text-align: center; color: #c9c9c9; font-size: 12.5px; line-height: 1.5;
  text-shadow: 0 1px 3px rgba(0,0,0,.8); pointer-events: none; }
#md-img-zoom .mdz-hint { color: #8e8e8e; font-size: 11.5px; }
@media (prefers-color-scheme: dark) {
  .md-preview { color: #d4d4d4; }
  .md-preview h6 { color: #9a9a9a; }  /* 다크에서 h6 옅은 색 대비 보정 */
  .md-preview code { background: #2f2f2f; color: #ff7b72; }
  .md-preview pre, .md-preview th { background: #262626; }
  .md-preview th, .md-preview td { border-color: #3a3a3a; }
  .md-preview blockquote { border-left-color: #d4d4d4; color: #a8a8a8; }
  #md-cite-tip { background: #e8e8ea; color: #1c1c1e; box-shadow: 0 6px 24px rgba(0,0,0,.5); }
  .sbs-cell-div { border-color: #3a3a3a; }
  .vtoc { background: #1f1f1f; }
  .vtoc-handle::after { background: #3a3a3a; }
  .vtoc-item { color: #b8b8b8; }
  .vtoc-item:hover { background: #2c2c2c; color: #e0e0e0; }
  .vcol-handle::after { background: #3a3a3a; }
  .vpdf { background: #151515; }
  .sbs-row-sep { border-top-color: #2c2c2c; }
}
"""

# 인용 아래에 고정 렌더되는 단일 툴팁 요소 + 위임 이벤트 (프리뷰 새로고침에도 살아남음).
# 서지정보는 window.__mdCiteTips = {번호: {text, url}} 맵에서 조회한다 (아래 build에서 주입).
# 호버 = 미리보기(통과 가능), 클릭 = 고정 토글(텍스트 선택·DOI 클릭 가능) + Reference 점프 방지.
_CITE_TIP_HTML = """
<div id="md-cite-tip"></div>
<script>
(function(){
  var tip = document.getElementById('md-cite-tip');
  if (!tip) return;
  var pinned = null;
  var PIN = '<span class="md-cite-pin" title="고정됨 — 바깥을 클릭하면 닫힘">'
    + '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">'
    + '<path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"/>'
    + '</svg></span>';
  var COPY_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">'
    + '<path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>';
  var CHECK_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">'
    + '<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
  function hrefNum(a){ var h=a.getAttribute('href')||''; return h.indexOf('#ref-')===0 ? h.slice(5) : null; }
  function fnNum(a){ var h=a.getAttribute('href')||''; return h.indexOf('#fn-')===0 ? h.slice(4) : null; }
  function esc(s){ var d=document.createElement('div'); d.textContent = s==null?'':s; return d.innerHTML; }
  function escAttr(s){ return esc(s).replace(/"/g, '&quot;'); }
  function linkify(s){  // 각주 내용의 URL만 클릭 가능한 링크로, 나머지는 이스케이프
    var out='', re=/(https?:\\/\\/[^\\s]+)/g, last=0, m;
    while((m=re.exec(s))){ out += esc(s.slice(last, m.index));
      var u=m[1]; out += '<a class="md-cite-doi" target="_blank" rel="noopener" href="'
        + u.replace(/"/g,'%22').replace(/</g,'%3C') + '">' + esc(u) + '</a>'; last = m.index + u.length; }
    out += esc(s.slice(last)); return out;
  }
  function entryFor(a){
    var f=fnNum(a);
    if(f!==null){ var c=(window.__mdFnTips||{})[f]; return c==null ? null : {text:c, fn:true}; }
    var n=hrefNum(a); return n===null ? null : (window.__mdCiteTips||{})[n] || null;
  }
  function place(a){
    var r = a.getBoundingClientRect(), gap = 6, w = tip.offsetWidth, h = tip.offsetHeight;
    var x = r.left;
    if (x + w > window.innerWidth - 8) x = window.innerWidth - w - 8;
    if (x < 8) x = 8;
    var y = r.bottom + gap;
    if (y + h > window.innerHeight - 8) y = r.top - h - gap;  // 아래 공간 없으면 위로
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function render(e, interactive){
    var html;
    if (e.fn){                              // 각주: URL만 링크화, 제목 복사 없음
      html = linkify(e.text);
      if (interactive) html += PIN;
      tip.innerHTML = html;
      return;
    }
    html = esc(e.text);
    if (e.url && /^https?:\\/\\//i.test(e.url)){
      var safe = e.url.replace(/"/g, '%22');
      html += '<br><a class="md-cite-doi" target="_blank" rel="noopener" href="' + esc(safe) + '">' + esc(e.url) + '</a>';
    }
    if (interactive){
      // 고정 시: 우상단 압정 + 그 아래 제목 복사 아이콘(DOI 안 될 때 구글 스콜라 검색용)
      var t = e.title || e.text || '';
      html += PIN;
      html += '<button class="md-cite-copy" title="제목 복사" data-title="' + escAttr(t) + '">' + COPY_SVG + '</button>';
    }
    tip.innerHTML = html;
  }
  function open(a, interactive){
    var e = entryFor(a); if(!e) return false;
    render(e, interactive);
    tip.classList.toggle('pinned', interactive);
    tip.style.pointerEvents = interactive ? 'auto' : 'none';
    tip.style.opacity = '1';
    place(a);   // 내용 렌더 후 크기 기준으로 배치
    return true;
  }
  function close(){ pinned = null; tip.style.opacity = '0'; tip.style.pointerEvents = 'none'; tip.classList.remove('pinned'); }
  function follow(){        // 고정된 상태에서 스크롤/리사이즈 시 인용을 계속 따라간다
    if (!pinned) return;
    var r = pinned.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight){ tip.style.opacity = '0'; return; }  // 화면 밖이면 숨김
    tip.style.opacity = '1';
    place(pinned);
  }

  document.addEventListener('mouseover', function(ev){
    if (pinned) return;                       // 고정 상태면 호버 미리보기 무시
    var a = ev.target.closest ? ev.target.closest('a[href^="#ref-"],a[href^="#fn-"]') : null;
    if (a) open(a, false);
  });
  document.addEventListener('mouseout', function(ev){
    if (pinned) return;
    var a = ev.target.closest ? ev.target.closest('a[href^="#ref-"],a[href^="#fn-"]') : null;
    if (a) tip.style.opacity = '0';
  });
  document.addEventListener('click', function(ev){
    var a = ev.target.closest ? ev.target.closest('a[href^="#ref-"],a[href^="#fn-"]') : null;
    if (a){                                   // 인용 클릭 = 고정 토글, Reference 점프 방지
      ev.preventDefault();
      if (pinned === a){ close(); }
      else { pinned = a; open(a, true); }
      return;
    }
    var copy = ev.target.closest ? ev.target.closest('.md-cite-copy') : null;
    if (copy){                                // 제목 복사 → 체크로 잠깐 바뀌고 '복사됨' 표시
      ev.preventDefault();
      var title = copy.getAttribute('data-title') || '';
      if (navigator.clipboard) navigator.clipboard.writeText(title);
      copy.innerHTML = CHECK_SVG;
      copy.classList.add('copied');
      setTimeout(function(){ copy.innerHTML = COPY_SVG; copy.classList.remove('copied'); }, 1200);
      return;
    }
    if (tip.contains(ev.target)) return;      // 툴팁 내부(DOI 링크·텍스트) 클릭은 유지
    if (pinned) close();                      // 바깥 클릭 → 닫기
  });
  // 캡처 단계로 내부 스크롤 컨테이너의 스크롤까지 잡아 위치를 갱신
  window.addEventListener('scroll', follow, true);
  window.addEventListener('resize', follow);
})();
</script>
"""

# 마크다운 헤더의 ⚙ 클릭 → 섹션 트리(#sectree-<id>)로 스크롤 + 잠깐 하이라이트.
# 트리가 접힌 확장 패널 안이면 먼저 펼친다. 순수 클라이언트 처리(서버 왕복 없음).
_SEC_JUMP_HTML = """
<script>
(function(){
  if (window.__mdSecJump) return; window.__mdSecJump = true;
  document.addEventListener('click', function(ev){
    var b = ev.target.closest ? ev.target.closest('.md-sec-jump') : null;
    if (!b) return;
    ev.preventDefault();
    var row = document.getElementById('sectree-' + b.getAttribute('data-sec'));
    if (!row) return;
    var exp = row.closest('.q-expansion-item');
    var opening = exp && !exp.classList.contains('q-expansion-item--expanded');
    if (opening){ var hdr = exp.querySelector('.q-item'); if (hdr) hdr.click(); }  // 접혀 있으면 펼치기
    setTimeout(function(){
      row.scrollIntoView({behavior:'smooth', block:'center'});
      row.classList.remove('sectree-flash'); void row.offsetWidth; row.classList.add('sectree-flash');
      setTimeout(function(){ row.classList.remove('sectree-flash'); }, 1700);
    }, opening ? 360 : 0);
  });
})();
</script>
"""

# 본문 그림 클릭 → 전체화면 확대 뷰어. 휠(트랙패드 핀치 포함)·버튼·더블클릭으로 확대,
# 드래그로 이동, Esc·바깥 클릭으로 닫기. 순수 클라이언트 처리 — 이미 받아 둔 이미지를 그대로 쓴다.
_IMG_ZOOM_HTML = """
<div id="md-img-zoom">
  <img alt="">
  <div class="mdz-bar">
    <button data-act="out" title="축소 (-)">&#8722;</button>
    <span class="mdz-pct">100%</span>
    <button data-act="in" title="확대 (+)">&#43;</button>
    <button data-act="fit" title="화면에 맞추기 (0)">
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
        <path d="M4 9V4h5v2H6v3H4zm11-5h5v5h-2V6h-3V4zM4 15h2v3h3v2H4v-5zm14 3v-3h2v5h-5v-2h3z"/>
      </svg></button>
    <button data-act="close" title="닫기 (Esc)">&#10005;</button>
  </div>
  <div class="mdz-foot"><div class="mdz-cap"></div>
    <div class="mdz-hint">휠·더블클릭으로 확대 · 드래그로 이동 · Esc로 닫기</div></div>
</div>
<script>
(function(){
  if (window.__mdImgZoom) return; window.__mdImgZoom = true;
  var box = document.getElementById('md-img-zoom');
  if (!box) return;
  var img = box.querySelector('img'), pct = box.querySelector('.mdz-pct'), cap = box.querySelector('.mdz-cap');
  var s = 1, tx = 0, ty = 0, base = 1, drag = null, moved = false;
  // base = 열었을 때의 배율(화면에 꽉 차게, 저해상도 그림을 너무 흐리게 늘리지 않도록 3배까지).
  // 논문 그림은 원본이 작은 경우가 많아 1배로 열면 본문에서 보던 크기와 다를 게 없다.
  function fitScale(){
    var availW = box.clientWidth - 64, availH = box.clientHeight - 104;  // CSS max-*와 같은 여백
    var w = img.offsetWidth, h = img.offsetHeight;   // CSS max-*로 이미 화면에 맞춰진 레이아웃 크기
    if (!w || !h) return 1;
    return Math.max(1, Math.min(availW / w, availH / h, 3));
  }

  function apply(){
    img.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + s + ')';
    // 퍼센트는 원본 픽셀 대비 — 큰 그림을 줄여 연 상태가 100%로 보이지 않게
    if (!img.offsetWidth || !img.naturalWidth) return;   // 아직 안 실린 그림: 표시 유지
    pct.textContent = Math.round(img.offsetWidth * s / img.naturalWidth * 100) + '%';
  }
  function clampPan(){               // 확대한 그림이 화면 밖으로 완전히 빠지지 않도록
    var r = box.getBoundingClientRect();
    var mx = Math.max(0, (img.offsetWidth * s - r.width) / 2);
    var my = Math.max(0, (img.offsetHeight * s - r.height) / 2);
    tx = Math.min(mx, Math.max(-mx, tx));
    ty = Math.min(my, Math.max(-my, ty));
  }
  function zoomAt(mx, my, ns){       // (mx,my) 아래에 있던 지점을 제자리에 두고 확대/축소
    ns = Math.min(base * 8, Math.max(base, ns));    // 연 크기 아래로는 안 줄이고, 그 8배까지
    var r = box.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var px = (mx - cx - tx) / s, py = (my - cy - ty) / s;
    tx = mx - cx - ns * px; ty = my - cy - ns * py; s = ns;
    if (s <= base) { tx = 0; ty = 0; }
    clampPan(); apply();
  }
  function zoomBy(f){
    var r = box.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, s * f);
  }
  function fit(){ base = fitScale(); s = base; tx = 0; ty = 0; apply(); }
  function close(){ box.classList.remove('open'); }

  document.addEventListener('click', function(ev){
    var t = ev.target;
    if (!t || t.tagName !== 'IMG' || !t.closest || !t.closest('.md-preview')) return;
    ev.preventDefault();             // 그림을 감싼 링크가 있어도 이동하지 않게
    img.src = t.currentSrc || t.src;
    var alt = t.getAttribute('alt') || t.getAttribute('title') || '';
    cap.textContent = alt;
    box.classList.add('open');       // 크기를 재려면 먼저 보여야 한다(display:none이면 0)
    if (img.complete) fit(); else img.onload = fit;
  });
  box.addEventListener('click', function(ev){
    if (moved) { moved = false; return; }               // 드래그 끝의 클릭은 닫기가 아니다
    if (ev.target === img || (ev.target.closest && ev.target.closest('.mdz-bar'))) return;
    close();                                            // 배경 클릭 → 닫기
  });
  [].forEach.call(box.querySelectorAll('.mdz-bar button'), function(b){
    b.addEventListener('click', function(){
      var a = b.getAttribute('data-act');
      if (a === 'in') zoomBy(1.25); else if (a === 'out') zoomBy(0.8);
      else if (a === 'fit') fit(); else close();
    });
  });
  box.addEventListener('wheel', function(ev){
    ev.preventDefault();                                // 뒤 패널이 같이 스크롤되지 않게
    var d = ev.deltaY * (ev.deltaMode === 1 ? 16 : 1);  // 줄 단위로 오는 브라우저 보정
    var f = Math.exp(-d * (ev.ctrlKey ? 0.008 : 0.0015));  // ctrl+휠 = 트랙패드 핀치 (더 민감하게)
    f = Math.min(1.35, Math.max(0.74, f));              // 관성 스크롤의 큰 델타로 한 번에 튀지 않게
    zoomAt(ev.clientX, ev.clientY, s * f);
  }, {passive: false});
  img.addEventListener('dblclick', function(ev){
    ev.preventDefault();
    if (s > base * 1.01) fit(); else zoomAt(ev.clientX, ev.clientY, base * 2.5);
  });
  img.addEventListener('mousedown', function(ev){
    ev.preventDefault();                                // 브라우저 기본 이미지 드래그 대신 패닝
    drag = {x: ev.clientX, y: ev.clientY, tx: tx, ty: ty};
    moved = false;
    box.classList.add('dragging');
  });
  window.addEventListener('mousemove', function(ev){
    if (!drag) return;
    var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    tx = drag.tx + dx; ty = drag.ty + dy;
    clampPan(); apply();
  });
  window.addEventListener('mouseup', function(){
    if (!drag) return;
    drag = null; box.classList.remove('dragging');
  });
  window.addEventListener('resize', function(){   // 창 크기가 바뀌면 기준 배율도 바뀐다
    if (!box.classList.contains('open')) return;
    var nb = fitScale();
    s = s * (nb / base); base = nb;               // 보고 있던 확대 비율은 유지
    clampPan(); apply();
  });
  document.addEventListener('keydown', function(ev){
    if (!box.classList.contains('open')) return;
    if (ev.key === 'Escape') close();
    else if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA'
                           || ev.target.isContentEditable)) return;   // 입력 중인 글자는 뺏지 않는다
    else if (ev.key === '+' || ev.key === '=') zoomBy(1.25);
    else if (ev.key === '-' || ev.key === '_') zoomBy(0.8);
    else if (ev.key === '0') fit();
    else return;
    ev.preventDefault();
  });
})();
</script>
"""

# 프리뷰 이미지: 마크다운 ![](..) + HTML <img src=..> 모두 잡아 /wdimages/<basename> 으로
_MD_IMG_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")
_HTML_IMG_RE = re.compile(r'(<img\s[^>]*src=["\'])([^"\']+)(["\'])', re.IGNORECASE)


def wd_token(root: Path) -> str:
    """워크디렉토리별 짧은 토큰 — 이미지/PDF URL에 넣어 논문 간 캐시 충돌을 막는다.

    URL이 논문마다 달라야 브라우저가 다른 논문의 figure-1.png / pdfpage/0 캐시를 재사용하지 않는다.
    """
    import hashlib

    return hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]


def _to_served(path: str, token: str) -> str:
    if path.startswith(("http://", "https://", "data:", "/wdimages/")):
        return path
    name = path.rstrip("/").split("/")[-1]  # 파일명만 서버 라우트로
    return f"/wdimages/{token}/{name}"


def served_markdown(md: str, token: str) -> str:
    """프리뷰용 — 모든 이미지 참조(md·HTML)를 out/images 서버 라우트로 치환 (논문별 토큰 포함)."""
    md = _MD_IMG_RE.sub(lambda m: m.group(1) + _to_served(m.group(2), token) + m.group(3), md)
    md = _HTML_IMG_RE.sub(lambda m: m.group(1) + _to_served(m.group(2), token) + m.group(3), md)
    return md


def cite_tips_js(tips: dict[str, str]) -> str:
    """서지정보 사전을 window.__mdCiteTips에 실어줄 JS 표현식 (호버 툴팁 조회용).

    인용 링크(마크다운 그대로 유지)는 href의 #ref-N 번호로 이 맵을 찾는다.
    </script> 조기 종료·JS 인젝션 방지를 위해 < 를 이스케이프한다.
    """
    payload = json.dumps(tips, ensure_ascii=False).replace("<", "\\u003c")
    return f"window.__mdCiteTips = {payload};"


def fn_tips_js(tips: dict[str, str]) -> str:
    """각주 사전을 window.__mdFnTips에 실어줄 JS (본문 위첨자 링크 #fn-N 호버 툴팁 조회용)."""
    payload = json.dumps(tips, ensure_ascii=False).replace("<", "\\u003c")
    return f"window.__mdFnTips = {payload};"


_HEADING_LINE_RE = re.compile(r"^#{1,6}\s")


def anchored_markdown(md: str, section_map: list[dict], page_by_id: dict | None = None,
                      jump: bool = False) -> str:
    """프리뷰 헤더에 `sec-<id>` 앵커를 심는다 (마크다운 스크롤 이동용).

    en.md의 헤더 줄과 section_map의 id를 문서 순서로 짝지어(라인 이동에 견고), 각 헤더 앞에
    앵커 span을 삽입한다. page_by_id가 있으면 앵커에 data-page(0-based)도 넣어 PDF 스크롤 싱크에 쓴다.
    jump=True면 각 헤더 옆에 '섹션 트리에서 편집' 점프 버튼(클릭 → 섹션 트리 항목 하이라이트+스크롤)을 붙인다.
    """
    ids = [e["id"] for e in sorted(section_map, key=lambda x: x["out_line"])]
    out: list[str] = []
    j = 0
    for line in md.splitlines():
        if _HEADING_LINE_RE.match(line) and j < len(ids):
            sid = ids[j]
            pg = (page_by_id or {}).get(sid) if page_by_id else None
            dp = f' data-page="{pg - 1}"' if pg else ""
            out.append(f'<a id="sec-{sid}"{dp}></a>')
            if jump:  # 헤더 텍스트 끝에 점프 버튼(섹션 트리 해당 항목으로 이동+하이라이트)
                line = line.rstrip() + (
                    f' <a class="md-sec-jump" href="#sectree-{sid}" data-sec="{sid}" '
                    'title="섹션 트리에서 이 섹션의 레벨을 편집">✎ 섹션 편집</a>')
            j += 1
        out.append(line)
    return "\n".join(out)


def _split_sections(md: str) -> list[str]:
    """마크다운을 헤더 경계로 섹션 블록들로 분할 (코드펜스 안의 # 는 무시).

    첫 헤더 앞 서문(있으면)이 첫 블록. 각 헤더가 새 블록의 시작.
    """
    blocks: list[list[str]] = []
    cur: list[str] = []
    fence = False
    for line in md.splitlines():
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
        elif not fence and _HEADING_LINE_RE.match(line) and cur:
            blocks.append(cur)
            cur = []
        cur.append(line)
    if cur:
        blocks.append(cur)
    return ["\n".join(b) for b in blocks]


def _split_paragraphs(md: str) -> list[str]:
    """블록을 빈 줄 경계로 문단 단위 분할 (코드펜스는 통째로 유지)."""
    paras: list[str] = []
    cur: list[str] = []
    fence = False

    def flush() -> None:
        nonlocal cur
        if any(x.strip() for x in cur):
            paras.append("\n".join(cur).strip("\n"))
        cur = []

    for line in md.splitlines():
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
            cur.append(line)
        elif fence:
            cur.append(line)
        elif not line.strip():
            flush()
        else:
            cur.append(line)
    flush()
    return paras


def _heading_of(block: str) -> tuple[int, str] | None:
    """블록이 헤더로 시작하면 (레벨, 제목텍스트), 아니면 None (첫 실질 줄 기준)."""
    for line in block.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        return (len(m.group(1)), m.group(2).strip()) if m else None
    return None


def align_rows(en: str, ko: str) -> list[tuple[str, str, tuple[int, str] | None]] | None:
    """원문/번역을 섹션→문단 단위로 짝지어 (en_block, ko_block, heading|None) 행 목록으로.

    섹션 수가 다르면 None(상위에서 비율 싱크로 폴백). 섹션 안에서 문단 수가 같으면 문단별 행,
    다르면 그 섹션은 통째로 한 행(짧은 쪽은 여백으로 정렬). heading은 각 섹션의 첫 행에만.
    """
    en_secs, ko_secs = _split_sections(en), _split_sections(ko)
    if len(en_secs) != len(ko_secs) or not en_secs:
        return None
    rows: list[tuple[str, str, tuple[int, str] | None]] = []
    for es, ks in zip(en_secs, ko_secs):
        head = _heading_of(es)
        ep, kp = _split_paragraphs(es), _split_paragraphs(ks)
        if len(ep) == len(kp) and len(ep) > 1:  # 문단 단위 정렬
            for i in range(len(ep)):
                rows.append((ep[i], kp[i], head if i == 0 else None))
        else:  # 섹션 단위 정렬 (문단 수 불일치)
            rows.append((es, ks, head))
    return rows


def _pdf_page_count(data: bytes) -> int:
    """업로드 바이트에서 PDF 페이지 수 (진행바 추정용). 실패 시 0."""
    from md4paper import pdfio

    return pdfio.page_count(data)


def _has_llm_key() -> bool:
    """LLM 키가 하나라도 있어 변환 직후 자동 LLM 단계를 돌릴 수 있는지."""
    try:
        config.build_provider()
        return True
    except RuntimeError:
        return False


def _auto_cite(wd: WorkDir) -> None:
    """변환 직후 참고문헌 파싱 + 본문 인용 링크 (키 없거나 실패하면 조용히 건너뜀)."""
    try:
        provider = config.build_provider()
    except RuntimeError:
        return
    from md4paper import pipeline
    from md4paper.review import manifest as manifest_io

    man = manifest_io.load(wd)
    try:
        pipeline.run_cite(wd, provider, parts=man.citation_parts,
                          reference_links=man.reference_links, force=True)
    except Exception:  # noqa: BLE001 — References 없음 등
        pass


def _auto_glossary(wd: WorkDir) -> None:
    """변환 직후 용어집 자동 생성 (번역 단계에서 바로 검토·수정하도록 미리 만들어 둠)."""
    try:
        provider = config.build_provider()
    except RuntimeError:
        return
    from md4paper import pipeline

    try:
        pipeline.run_glossary(wd, provider, regenerate=True)
    except Exception:  # noqa: BLE001
        pass


def _auto_metadata(wd: WorkDir) -> None:
    """변환 직후 논문 서지(제목·저자·연도·venue)를 LLM으로 추출·저장 (목록 표시·검색·정렬용)."""
    try:
        provider = config.build_provider()
    except RuntimeError:
        return
    from md4paper import paper_meta
    from md4paper.review import manifest as manifest_io

    try:
        text = paper_meta.front_text(wd)
        if not text.strip():
            return
        meta = paper_meta.extract(provider, text)
        if not meta.title:  # 제목이 비면 manifest 제목으로 보완
            meta.title = manifest_io.load(wd).title
        if meta.year is None and wd.meta_json.exists():  # front matter에 연도 없으면 PDF 생성일자로 폴백
            try:
                src = json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source", "")
            except (OSError, ValueError):
                src = ""
            if src.lower().endswith(".pdf"):
                meta.year = paper_meta.pdf_year(src)
        paper_meta.save(wd, meta)
    except Exception:  # noqa: BLE001
        pass


def _auto_rename(wd: WorkDir, workspace: Path) -> WorkDir:
    """서지에서 폴더·PDF 이름을 이름 규칙([output].naming, 기본 {year}_{title}_{author})으로 정리.

    실패·불가(서지 없음 등) 시 원본 wd 유지.
    """
    from md4paper import paper_meta
    from md4paper.workdir import rename_workdir

    try:
        meta = paper_meta.load(wd)
        base = paper_meta.folder_base(meta) if meta else ""
        return rename_workdir(wd, base, Path(workspace)) if base else wd
    except OSError:
        return wd


def _review_url(root: Path) -> str:
    """워크디렉토리를 URL 쿼리에 담은 리뷰 페이지 주소 (브라우저 히스토리·뒤로가기용)."""
    from urllib.parse import quote

    return f"/review?wd={quote(str(Path(root).resolve()))}"


def _relative_time(ts: float) -> str:
    """수정 시각 → '방금 전 / N분 전 / N시간 전 / N일 전'."""
    diff = max(0, int(time.time() - ts))
    if diff < 60:
        return "방금 전"
    if diff < 3600:
        return f"{diff // 60}분 전"
    if diff < 86400:
        return f"{diff // 3600}시간 전"
    return f"{diff // 86400}일 전"


def build(ctrl: UIController) -> None:
    """현재 페이지 컨텍스트에 UI를 구성한다."""
    from nicegui import ui

    m = ctrl.manifest
    tok = wd_token(ctrl.wd.root)  # 이미지·PDF URL에 넣는 논문별 토큰 (캐시 충돌 방지)

    with ui.header().classes("items-center justify-between q-py-none"):
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            # color(kwarg)는 '배경색'이라 흰 상자가 된다 → 배경은 flat(투명), 글자는 text-color=white
            ui.button("다른 논문", icon="arrow_back", color=None,
                      on_click=lambda: ui.navigate.to("/home")).props("flat dense no-caps text-color=white") \
                .tooltip("업로드 / 최근 작업으로")
            _title = m.title or ctrl.wd.root.name
            ui.label(_title).classes("text-base font-bold").style(
                "max-width:46vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap").tooltip(_title)
        # 단계 탭을 헤더 오른쪽에 (변환 ↔ 번역 ↔ 뷰어)
        with ui.tabs().props("dense") as steps:
            step_convert = ui.tab("1 · 변환", icon="article")
            step_translate = ui.tab("2 · 번역", icon="translate")
            step_sbs = ui.tab("3 · 뷰어", icon="vertical_split")

    # 변환 탭 프리뷰와 뷰어는 같은 en.md를 읽는다 → 한쪽만 갱신하면 인용 표기·레벨 변경이 어긋난다.
    # 뷰어는 그리드를 통째로 다시 만들어 무거우므로, 바뀌면 표시만 해 두고 탭을 옮길 때 한 번 갱신한다.
    viewer_stale = {"on": False}

    def refresh_preview() -> None:
        preview.refresh()
        viewer_stale["on"] = True

    def on_step_change(_=None) -> None:  # noqa: ANN001 — NiceGUI 값 변경 이벤트
        if viewer_stale["on"]:
            viewer_stale["on"] = False
            side_by_side.refresh()

    steps.on_value_change(on_step_change)

    _translate_refs: dict = {}  # 번역 탭 위젯 참조 (변환 탭 레벨 변경 시 번역 트리 갱신용)

    def _refresh_translate_tree() -> None:
        picker = _translate_refs.get("picker")
        if picker is not None:  # 레벨/구조가 바뀌면 번역 탭 섹션 트리도 다시 그린다(keep-alive 대비)
            picker.refresh()

    def commit() -> None:
        """구조/설정 변경을 자동 저장 + 재조립 (별도 저장 버튼 없이 항상 최신)."""
        ctrl.save_and_reassemble()
        library.auto_export(ctrl.wd)  # 저장 위치를 지정했으면 그 폴더의 사본도 최신으로

    def change_level(sid: str, value) -> None:  # noqa: ANN001
        """레벨 변경 → 자동 저장 + 즉시 프리뷰·트리에 반영."""
        ctrl.set_level(sid, value)
        commit()
        refresh_preview()
        section_list.refresh()
        _refresh_translate_tree()

    @ui.refreshable
    def section_list() -> None:
        for sec in m.sections:
            # 레벨 드롭다운은 왼쪽 고정 열로 정렬, 들여쓰기는 텍스트에만, 배지는 오른쪽 끝으로
            indent = (int(sec.level) - 1) * 18 if str(sec.level).isdigit() else 0
            opts = _level_opts(sec.runin)
            # id=sectree-<sid>: 마크다운 프리뷰의 점프 버튼(⚙)이 이 행으로 스크롤+하이라이트한다
            with ui.row().classes("group items-center w-full gap-2 no-wrap").props(f"id=sectree-{sec.id}"):
                ui.select(
                    opts,
                    value=sec.level,
                    on_change=lambda e, sid=sec.id: change_level(sid, e.value),
                ).props("dense outlined").classes("w-28 shrink-0 md4-lvl")
                text_cls = "text-sm font-semibold" if sec.is_title else "text-sm"
                # 라벨 + 배지 + 이동 버튼을 한 덩어리로 묶어 섹션 텍스트 바로 옆에 붙인다
                with ui.row().classes("items-center gap-1 min-w-0").style(f"margin-left:{indent}px"):
                    lbl = ui.label(sec.text).classes(text_cls + " section-title")
                    if isinstance(sec.level, int):  # 헤더로 남는 섹션 → 클릭 시 마크다운 그 위치로
                        lbl.classes("cursor-pointer").on("click", lambda _, sid=sec.id: goto_md(sid))
                    tip = (f"p.{sec.page}\n" if sec.page else "") + (sec.context or "")
                    if isinstance(sec.level, int):
                        tip = "클릭하면 마크다운의 이 섹션으로 이동\n" + tip
                    if tip.strip():
                        lbl.tooltip(tip.strip())
                    # 배지 — 항상 보임 (제목/run-in/기억됨/확인)
                    if sec.is_title:
                        ui.badge("제목", color="purple").props("outline")
                    if sec.runin:
                        ui.badge("run-in", color="blue-4").props("outline")
                    if sec.auto_level is not None and sec.level != sec.auto_level:
                        ui.badge("기억됨", color="green").props("outline").tooltip(
                            f"자동값 {_LEVEL_LABEL.get(sec.auto_level, sec.auto_level)} → "
                            f"사용자 선택 {_LEVEL_LABEL.get(sec.level, sec.level)} (이름으로 기억)")
                    if sec.needs_review:
                        ui.badge("확인", color="orange").props("outline")
                    # 섹션 라벨을 클릭하면 마크다운 그 위치로 이동(나란히 보기면 PDF도 따라옴).
                    # 별도 PDF 이동 버튼은 두지 않는다.

    _GROUP_NAME = {
        ("title", 0): "제목",
        ("dotted-arabic", 1): "숫자 1단계", ("dotted-arabic", 2): "숫자 2단계",
        ("dotted-arabic", 3): "숫자 3단계", ("dotted-arabic", 4): "숫자 4단계",
        ("roman", 1): "로마 숫자", ("letter", 1): "문자", ("letter", 2): "문자 2단계",
        ("unnumbered", 1): "무번호", ("none", 1): "번호 없음", ("run-in", 0): "run-in 헤더",
    }

    @ui.refreshable
    def batch_level_panel() -> None:
        """같은 모양의 헤더를 묶어 한 번에 조정 — 선택지는 섹션 트리와 동일."""
        groups = ctrl.level_groups()
        if not groups:
            ui.label("조정할 헤더가 없습니다.").classes("text-xs text-gray-400")
            return
        ui.label("같은 모양의 헤더를 한 번에 바꿉니다. 펼치면 그룹의 각 섹션을 개별 조정할 수 있어요.") \
            .classes("text-xs text-gray-500")

        def apply_group(key: str, name: str, value) -> None:  # noqa: ANN001
            if value is None:
                return
            n = ctrl.apply_group_level(key, value)
            commit()
            refresh_preview()
            section_list.refresh()
            batch_level_panel.refresh()
            _refresh_translate_tree()
            ui.notify(f"{name} {n}개 → {_LEVEL_LABEL.get(value, value)}", type="positive")

        def change_in_group(sid: str, value) -> None:  # noqa: ANN001
            """그룹 내 개별 섹션 레벨 변경 → 프리뷰·섹션 트리·번역 트리 연동 (아코디언은 유지)."""
            ctrl.set_level(sid, value)
            commit()
            refresh_preview()
            section_list.refresh()
            _refresh_translate_tree()

        for g in groups:
            name = _GROUP_NAME.get((g["scheme"], g["depth"]), f"{g['scheme']} {g['depth']}단계")
            opts = _level_opts(g["scheme"] == "run-in")
            group_secs = [s for s in m.sections if ctrl.group_key(s) == (g["scheme"], g["depth"])]
            # 일괄 선택은 접힌 상태에서도 쓰도록 아코디언 헤더 옆(왼쪽)에 둔다. 펼치면 개별 섹션.
            with ui.row().classes("items-start gap-2 w-full no-wrap"):
                ui.select(
                    opts, value=g["current"],
                    on_change=lambda e, k=g["key"], nm=name: apply_group(k, nm, e.value),
                ).props("dense outlined").classes("w-28 shrink-0 md4-lvl").tooltip("이 그룹 전체를 한 번에")
                with ui.expansion(f"{name} ×{g['count']}").classes("flex-grow md4-batch-exp").props("dense"):
                    for s in group_secs:  # 개별 섹션 + 헤더 레벨 선택 (섹션 트리·마크다운과 연동)
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.select(
                                opts, value=s.level,
                                on_change=lambda e, sid=s.id: change_in_group(sid, e.value),
                            ).props("dense outlined").classes("w-28 shrink-0 md4-lvl")
                            lbl = ui.label(s.text).classes("text-sm section-title").style("max-width:300px")
                            if isinstance(s.level, int):  # 클릭 시 마크다운의 해당 섹션으로 이동
                                lbl.classes("cursor-pointer").on("click", lambda _, sid=s.id: goto_md(sid)) \
                                    .tooltip("클릭하면 마크다운의 이 섹션으로 이동")

    ui.add_css(_PREVIEW_CSS)
    # 인용·각주 호버 툴팁: 맵 주입 → 툴팁 요소/스크립트 (같은 툴팁 UI를 #ref-·#fn- 둘 다 씀)
    ui.add_body_html(f"<script>{cite_tips_js(ctrl.citation_tooltips())}</script>")
    ui.add_body_html(f"<script>{fn_tips_js(ctrl.footnote_tooltips())}</script>")
    ui.add_body_html(_CITE_TIP_HTML)
    ui.add_body_html(_SEC_JUMP_HTML)  # 마크다운 헤더 ⚙ 클릭 → 섹션 트리 항목 스크롤+하이라이트
    ui.add_body_html(_IMG_ZOOM_HTML)  # 본문 그림 클릭 → 확대 뷰어

    def push_cite_tips() -> None:
        """참고문헌을 (재)파싱한 뒤 클라이언트 툴팁 맵을 갱신한다."""
        ui.run_javascript(cite_tips_js(ctrl.citation_tooltips()))

    edit_state = {"on": False, "ratio": 0.0}

    async def toggle_edit(on: bool) -> None:
        """직접 편집 토글. 켤 때 프리뷰에서 보던 위치(스크롤 비율)를 편집기로 옮긴다."""
        if on:
            edit_state["ratio"] = await ui.run_javascript(
                "(function(){var e=document.querySelector('.md-scroll-en');"
                "if(!e) return 0; var m=e.scrollHeight-e.clientHeight;"
                "return m>0 ? e.scrollTop/m : 0;})()"
            ) or 0.0
        edit_state["on"] = on
        refresh_preview()
        edit_toggle.refresh()

    @ui.refreshable
    def edit_toggle() -> None:
        """툴바에 놓이는 마크다운 편집 스위치(+수동 편집 표시). 마크다운이 보일 때만 렌더된다."""
        ui.switch("마크다운 편집", value=edit_state["on"],
                  on_change=lambda e: toggle_edit(e.value)).props("dense")
        if ctrl.has_manual_edit():
            ui.badge("수동 편집됨", color="orange").props("outline").tooltip(
                "재조립하면 이 편집은 사라집니다 (섹션 트리에서 다시 만들어지므로)")

    @ui.refreshable
    def preview() -> None:
        """프리뷰 / 직접 편집 (토글은 툴바에)."""
        if not edit_state["on"]:
            md = ctrl.en_markdown()
            if md:
                page_by_id = {s.id: s.page for s in ctrl.manifest.sections}
                md = anchored_markdown(served_markdown(md, tok), ctrl.section_map(), page_by_id, jump=True)
            ui.markdown(md or "_paper.en.md 없음 — convert를 먼저 실행하세요._",
                        extras=["fenced-code-blocks", "tables", "latex"]).classes("md-preview")
            return

        # 편집 모드: 마크다운 원문을 그대로 편집 (구조·수식·인용 앵커 손실 없음)
        ui.label("마크다운 원문을 직접 고칩니다. 저장하면 번역은 이 내용을 사용합니다.").classes("text-xs text-gray-500")
        editor = ui.codemirror(
            ctrl.en_markdown(), language="Markdown", line_wrapping=True,
        ).classes("w-full cm-en-editor").style("height: calc(100vh - 260px)")

        r = edit_state.get("ratio") or 0.0
        if r:  # 프리뷰에서 보던 위치로 편집기 스크롤 (비율 근사)
            ui.timer(0.12, lambda: ui.run_javascript(
                "(function(){var s=document.querySelector('.cm-en-editor .cm-scroller');"
                f"if(s){{var m=s.scrollHeight-s.clientHeight; s.scrollTop=(m>0?{r}:0)*m;}}}})()"
            ), once=True)

        def save_edit() -> None:
            ctrl.save_en_markdown(editor.value)
            ui.notify("편집 저장됨 — 번역은 이 내용을 사용합니다.", type="positive")
            refresh_preview()
            edit_toggle.refresh()

        with ui.row().classes("gap-2 items-center"):
            ui.button("편집 저장", icon="save", on_click=save_edit).props("color=primary dense")
            ui.button("되돌리기(원본 다시 렌더)", icon="restore",
                      on_click=lambda: (ctrl.save_and_reassemble(), refresh_preview(), edit_toggle.refresh(),
                                        ui.notify("섹션 트리 기준으로 다시 만들었습니다.", type="info"))).props("outline dense")

    @ui.refreshable
    def ko_preview() -> None:
        ui.markdown(served_markdown(ctrl.ko_markdown(), tok) or "_아직 번역 안 됨 — 용어집 탭에서 번역을 실행하세요._",
                    extras=["fenced-code-blocks", "tables", "latex"]).classes("md-preview")

    # --- 번역 탭 결과: 원문/번역 토글 + 섹션·문단 정렬 나란히 (뷰어와 동일 정렬) ---
    trans_view = {"en": True, "ko": True}

    def _tr_toggle(key: str) -> None:
        has_ko = bool(ctrl.ko_markdown())
        nxt = {**trans_view, key: not trans_view[key]}
        if not (nxt["en"] or (nxt["ko"] and has_ko)):
            return  # 마지막 패널을 끄려는 클릭 → 무시
        trans_view[key] = not trans_view[key]
        trans_result.refresh()

    @ui.refreshable
    def trans_result() -> None:
        _md = ["fenced-code-blocks", "tables", "latex"]
        en_raw = ctrl.en_markdown()
        ko_raw = ctrl.ko_markdown()
        has_ko = bool(ko_raw)
        show_en = trans_view["en"]
        show_ko = trans_view["ko"] and has_ko
        if not (show_en or show_ko):
            show_en = True

        def chip(label: str, key: str, enabled: bool = True) -> None:
            active = trans_view[key] and enabled
            b = ui.button(label, on_click=lambda k=key: _tr_toggle(k)).props(
                "dense no-caps " + ("unelevated color=primary" if active else "outline color=grey-7"))
            if not enabled:
                b.disable()
                b.tooltip("아직 번역 안 됨")

        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            chip("원문", "en")
            chip("번역", "ko", has_ko)

        en = served_markdown(en_raw, tok) if en_raw else ""
        rows = align_rows(en, served_markdown(ko_raw, tok)) if has_ko else None
        two = show_en and show_ko and rows is not None
        # 탭(스크롤 컬럼) 안이라 그리드가 높이를 갖도록 고정 높이 컨테이너로 감싼다
        with ui.element("div").style("height: calc(100vh - 210px); min-height: 0"):
            if rows is not None:  # 정렬 그리드 (원문/번역 컬럼 토글)
                with ui.element("div").classes("sbs-grid" + (" sbs-two" if two else "")).style(
                        f"--sbs-cols:{'1fr 1fr' if two else '1fr'}"):
                    for k, (enb, kob, head) in enumerate(rows):
                        sep = " sbs-row-sep" if (head and k > 0) else ""
                        if show_en:
                            div = " sbs-cell-div" if two else ""
                            with ui.element("div").classes(f"sbs-cell{div}{sep}"):
                                ui.markdown(enb, extras=_md).classes("md-preview sbs-md")
                        if show_ko:
                            with ui.element("div").classes(f"sbs-cell{sep}"):
                                ui.markdown(kob or "", extras=_md).classes("md-preview sbs-md")
            else:  # 번역 전(ko 없음) 또는 정렬 실패 → 단일 패널
                single = served_markdown(ko_raw, tok) if show_ko else en
                with ui.element("div").classes("vpane md-preview").style("padding:0 12px"):
                    ui.markdown(single or "_아직 번역 안 됨 — 용어집 탭에서 번역을 실행하세요._", extras=_md)

    # 뷰어 표시 상태 — 원문/번역/PDF 패널 토글 + 목차 + 텍스트↔PDF 분할 비율 + PDF 스크롤 싱크
    viewer_state = {"en": True, "ko": True, "pdf": False, "toc": True, "split": 58, "sync": True}

    def _row_scroll_js(k: int) -> str:  # 정렬 그리드의 k번째 행으로 스크롤
        return (f"document.querySelector('.sbs-grid [data-row=\"{k}\"]')"
                "?.scrollIntoView({behavior:'smooth', block:'start'})")

    def _sec_scroll_js(sid: str) -> str:  # 폴백 패널 — sec 앵커로 스크롤
        return f"document.getElementById('sec-{sid}')?.scrollIntoView({{behavior:'smooth', block:'start'}})"

    def _render_toc(entries: list[tuple[int, str, str]]) -> None:
        """접이식 목차 — entries: [(레벨, 제목, 클릭 시 실행할 스크롤 JS)]."""
        with ui.element("div").classes("vtoc"):
            ui.label("목차").classes("vtoc-title")
            if not entries:
                ui.label("섹션 없음").classes("text-xs text-gray-400 px-2")
            for level, text, js in entries:
                ui.label(text).classes("vtoc-item").tooltip(text) \
                    .style(f"padding-left:{8 + (max(level, 1) - 1) * 12}px") \
                    .on("click", lambda j=js: ui.run_javascript(j))

    # 정렬 그리드의 원문|번역 컬럼 경계를 드래그로 조절 (그리드 행 정렬은 그대로 유지).
    # 재렌더(토글)마다 실행되므로 window 리스너는 1회만, 핸들 바인딩은 dataset 플래그로 멱등하게.
    _ENKO_DRAG_JS = """
    (function(){
      var g=document.querySelector('.sbs-grid.sbs-two'), h=document.querySelector('.vcol-handle');
      var f = window.__sbsEnFrac || 0.5;
      function apply(){ var gg=document.querySelector('.sbs-grid.sbs-two'), hh=document.querySelector('.vcol-handle');
        if(gg) gg.style.setProperty('--sbs-cols', f+'fr '+(1-f)+'fr'); if(hh) hh.style.left=(f*100)+'%'; }
      apply();
      if(h && !h.dataset.bound){ h.dataset.bound='1';
        h.addEventListener('mousedown', function(e){ window.__sbsDrag=true; e.preventDefault(); document.body.style.userSelect='none'; }); }
      if(!window.__sbsDragInit){ window.__sbsDragInit=true;
        window.addEventListener('mousemove', function(e){ if(!window.__sbsDrag) return;
          var gg=document.querySelector('.sbs-grid.sbs-two'); var w=gg?gg.closest('.vwrap'):null; if(!w) return;
          var r=w.getBoundingClientRect(); f=Math.min(0.85, Math.max(0.15, (e.clientX-r.left)/r.width));
          window.__sbsEnFrac=f; apply(); });
        window.addEventListener('mouseup', function(){ if(window.__sbsDrag){ window.__sbsDrag=false; document.body.style.userSelect=''; } }); }
    })();
    """

    # 텍스트 스크롤 → PDF 따라오기(섹션 단위). scroll_sel 컨테이너 안의 최상단 [data-page]로 PDF를 스크롤.
    # 컨테이너는 재렌더마다 새 요소라 리스너가 쌓이지 않는다(요소당 1회).
    def _pdf_sync_js(scroll_sel: str) -> str:
        return ("""
        (function(){
          var sc=document.querySelector('%s'), pv=document.querySelector('.vpdf');
          if(!sc || !pv || sc.dataset.pdfsync) return;
          var cells=[].slice.call(sc.querySelectorAll('[data-page]'));
          if(!cells.length) return;
          sc.dataset.pdfsync='1';
          var last=-1, tick=false;
          function sync(){ tick=false;
            var top=sc.getBoundingClientRect().top+6, pg=+cells[0].dataset.page;
            for(var i=0;i<cells.length;i++){ if(cells[i].getBoundingClientRect().top<=top) pg=+cells[i].dataset.page; else break; }
            if(pg!==last && !isNaN(pg)){ last=pg; var img=document.getElementById('pdfpage-'+pg);
              if(img) pv.scrollTop += img.getBoundingClientRect().top - pv.getBoundingClientRect().top; }
          }
          sc.addEventListener('scroll', function(){ if(!tick){ tick=true; requestAnimationFrame(sync); } });
          sync();
        })();
        """ % scroll_sel)

    _PDF_SYNC_JS = _pdf_sync_js(".sbs-grid")  # 뷰어 정렬 그리드용

    # 목차 너비 드래그 — .vtoc의 --vtoc-w를 갱신. window 전역에 저장해 재렌더에도 유지, 리스너 1회.
    _VTOC_DRAG_JS = """
    (function(){
      var toc=document.querySelector('.vtoc'), h=document.querySelector('.vtoc-handle'),
          shell=toc?toc.closest('.vshell'):null;
      if(window.__vtocW && toc){ toc.style.setProperty('--vtoc-w', window.__vtocW+'px'); }
      if(h && !h.dataset.bound){ h.dataset.bound='1';
        h.addEventListener('mousedown', function(e){ window.__vtocDrag=true; e.preventDefault(); document.body.style.userSelect='none'; }); }
      if(!window.__vtocInit){ window.__vtocInit=true;
        window.addEventListener('mousemove', function(e){ if(!window.__vtocDrag) return;
          var t=document.querySelector('.vtoc'), s=t?t.closest('.vshell'):null; if(!s) return;
          var w=Math.min(460, Math.max(140, e.clientX - s.getBoundingClientRect().left));
          window.__vtocW=w; t.style.setProperty('--vtoc-w', w+'px'); });
        window.addEventListener('mouseup', function(){ if(window.__vtocDrag){ window.__vtocDrag=false; document.body.style.userSelect=''; } }); }
    })();
    """

    def _pdf_pane() -> None:
        n = ctrl.pdf_page_count()
        with ui.element("div").classes("vpdf"):
            if n == 0:
                ui.label("원본 PDF를 찾을 수 없습니다.").classes("text-sm text-gray-500")
                return
            imgs = "".join(
                f'<img id="pdfpage-{i}" src="/pdfpage/{tok}/{i}" loading="lazy" title="p.{i + 1}" '
                'style="width:100%;height:auto;display:block;margin:0 auto 10px;max-width:900px;'
                'border:1px solid #e0e0e0;border-radius:6px"/>'
                for i in range(n)
            )
            ui.html(f'<div id="pdf-pages" style="width:100%">{imgs}</div>')

    @ui.refreshable
    def side_by_side() -> None:
        """뷰어 — 원문·번역·PDF를 토글/리사이즈해 보고, 원문|번역은 섹션·문단 단위로 정렬. 좌측 목차."""
        en_raw = ctrl.en_markdown()
        if not en_raw:
            ui.label("영어 마크다운이 없습니다 — 먼저 변환하세요.").classes("text-sm text-gray-500")
            return
        _md = ["fenced-code-blocks", "tables", "latex"]
        en = served_markdown(en_raw, tok)
        ko_raw = ctrl.ko_markdown()
        has_ko = bool(ko_raw)
        has_pdf = ctrl.pdf_page_count() > 0

        show_en = viewer_state["en"]
        show_ko = viewer_state["ko"] and has_ko
        show_pdf = viewer_state["pdf"] and has_pdf
        if not (show_en or show_ko or show_pdf):
            show_en = True  # 최소 하나는 보이도록

        rows = align_rows(en, served_markdown(ko_raw, tok)) if has_ko else None
        aligned = rows is not None
        if not aligned:
            rows = [(s, None, _heading_of(s)) for s in _split_sections(en)]  # EN 전용 유사 행

        # 섹션 → PDF 페이지(0-based). 목차 클릭 점프 + 텍스트 스크롤 시 PDF 따라오기(싱크)에 쓴다.
        smap = sorted(ctrl.section_map(), key=lambda x: x["out_line"])
        page_by_id = {s.id: s.page for s in ctrl.manifest.sections}
        row_page0: dict[int, int] = {}  # 정렬 행 인덱스 → PDF 페이지(0-based)
        if aligned:
            head_rows = [k for k, (_e, _k, h) in enumerate(rows) if h]
            if len(head_rows) == len(smap):  # 정렬 행 ↔ 렌더된 헤더가 1:1일 때만 페이지 대응
                for i, k in enumerate(head_rows):
                    pg = page_by_id.get(smap[i]["id"])
                    if pg:
                        row_page0[k] = pg - 1

        def _entry_js(text_js: str, page0: int | None) -> str:
            if show_pdf and page0 is not None:
                return (text_js + "; document.getElementById('pdfpage-" + str(page0)
                        + "')?.scrollIntoView({behavior:'smooth', block:'start'})")
            return text_js

        # 목차 — 정렬되면 행 인덱스로, 아니면 sec 앵커로 스크롤
        if aligned:
            entries = [(h[0], h[1], _entry_js(_row_scroll_js(k), row_page0.get(k)))
                       for k, (_e, _k, h) in enumerate(rows) if h]
        else:
            entries = [(e["level"], e["text"],
                        _entry_js(_sec_scroll_js(e["id"]),
                                  (page_by_id.get(e["id"]) - 1) if page_by_id.get(e["id"]) else None))
                       for e in smap]

        def text_area() -> None:
            two = show_en and show_ko and aligned
            if aligned or show_en:
                # 정렬 그리드 (원문/번역 컬럼 토글). 정렬 실패 시엔 EN만 그리드로(그때 KO 컬럼 생략).
                with ui.element("div").classes("vwrap"):
                    two_cls = " sbs-two" if two else ""
                    with ui.element("div").classes(f"sbs-grid{two_cls}").style(
                            f"--sbs-cols:{'1fr 1fr' if two else '1fr'}"):
                        for k, (enb, kob, head) in enumerate(rows):
                            sep = " sbs-row-sep" if (head and k > 0) else ""
                            # 첫 표시 셀에 data-row(목차 스크롤) + data-page(PDF 싱크)
                            anchor_props = f"data-row={k}"
                            if k in row_page0:
                                anchor_props += f" data-page={row_page0[k]}"
                            anchored = False
                            if show_en:
                                div = " sbs-cell-div" if two else ""
                                with ui.element("div").classes(f"sbs-cell{div}{sep}").props(anchor_props):
                                    ui.markdown(enb, extras=_md).classes("md-preview sbs-md")
                                anchored = True
                            if show_ko and aligned:
                                with ui.element("div").classes(f"sbs-cell{sep}").props(
                                        "" if anchored else anchor_props):
                                    ui.markdown(kob or "", extras=_md).classes("md-preview sbs-md")
                    if two:
                        ui.element("div").classes("vcol-handle")
                if two:
                    ui.run_javascript(_ENKO_DRAG_JS)
            else:
                # 정렬 실패 + 번역만 → 단일 패널
                with ui.element("div").classes("vpane md-preview").style("padding:1px 22px 8px"):
                    ui.markdown(served_markdown(ko_raw, tok), extras=_md)

        with ui.element("div").classes("vshell"):
            if viewer_state["toc"]:
                _render_toc(entries)
                ui.element("div").classes("vtoc-handle")  # 목차 너비 드래그
                ui.run_javascript(_VTOC_DRAG_JS)
            with ui.element("div").classes("vmain"):
                if (show_en or show_ko) and show_pdf:
                    with ui.splitter(value=viewer_state["split"]).classes("w-full").style("height:100%") as sp:
                        sp.on_value_change(lambda e: viewer_state.update(split=e.value))
                        with sp.before:
                            text_area()
                        with sp.after:
                            _pdf_pane()
                    if row_page0 and viewer_state["sync"]:  # 텍스트 스크롤 → PDF 페이지 따라오기 (토글)
                        ui.run_javascript(_PDF_SYNC_JS)
                elif show_pdf:
                    _pdf_pane()
                else:
                    text_area()
        # 토글 전 캡처해 둔 텍스트 스크롤 위치 복원 + (PDF 있으면) 그 위치로 PDF 정렬
        r = viewer_state.pop("_restore", None)
        if (show_en or show_ko) and r:
            ui.timer(0.13, lambda: ui.run_javascript(
                "(function(){var e=document.querySelector('.sbs-grid')||document.querySelector('.vpane');"
                f"if(!e)return;var m=e.scrollHeight-e.clientHeight; e.scrollTop=(m>0?{r}:0)*m;"
                "e.dispatchEvent(new Event('scroll'));})()"), once=True)

    async def _vtoggle(key: str) -> None:
        if key in ("en", "ko", "pdf"):  # 패널 토글만 가드 (toc·sync는 제외)
            has_ko = bool(ctrl.ko_markdown())
            has_pdf = ctrl.pdf_page_count() > 0
            nxt = {**viewer_state, key: not viewer_state[key]}
            if not (nxt["en"] or (nxt["ko"] and has_ko) or (nxt["pdf"] and has_pdf)):
                return  # 마지막 패널을 끄려는 클릭 → 무시
        # 재렌더 전 텍스트 스크롤 위치(비율)를 캡처 → 재렌더 후 복원(+PDF 정렬). PDF 열기가 위치를 잃지 않게.
        ratio = await ui.run_javascript(
            "(function(){var e=document.querySelector('.sbs-grid')||document.querySelector('.vpane');"
            "if(!e)return null;var m=e.scrollHeight-e.clientHeight;return m>0?e.scrollTop/m:0;})()")
        viewer_state[key] = not viewer_state[key]
        viewer_state["_restore"] = ratio
        viewer_toolbar.refresh()
        side_by_side.refresh()

    @ui.refreshable
    def viewer_toolbar() -> None:
        has_ko = bool(ctrl.ko_markdown())
        has_pdf = ctrl.pdf_page_count() > 0

        def chip(label: str, key: str, enabled: bool = True) -> None:
            active = viewer_state[key] and enabled
            b = ui.button(label, on_click=lambda k=key: _vtoggle(k)).props(
                "dense no-caps " + ("unelevated color=primary" if active else "outline color=grey-7"))
            if not enabled:
                b.disable()
                b.tooltip("번역" if key == "ko" else "PDF 원본" + " 없음")

        show_pdf = viewer_state["pdf"] and has_pdf
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.button(icon="menu", on_click=lambda: _vtoggle("toc")).props(
                "flat dense round " + ("color=primary" if viewer_state["toc"] else "")).tooltip("목차 열기/닫기")
            ui.separator().props("vertical").classes("h-6")
            chip("원문", "en")
            chip("번역", "ko", has_ko)
            chip("PDF", "pdf", has_pdf)
            if show_pdf:  # PDF 켜졌을 때만 — 텍스트 스크롤에 PDF 페이지 따라오기
                ui.button(icon="sync" if viewer_state["sync"] else "sync_disabled",
                          on_click=lambda: _vtoggle("sync")).props(
                    "flat dense round " + ("color=primary" if viewer_state["sync"] else "color=grey-6")) \
                    .tooltip("스크롤 시 PDF 페이지 따라오기 " + ("(켜짐)" if viewer_state["sync"] else "(꺼짐)"))
            ui.space()
            export_fmt_review()
            library_button()
            ui.button("영어 zip", icon="download", on_click=download_en).props("flat dense no-caps color=primary")
            ui.button("한국어 zip", icon="download", on_click=download_ko).props("flat dense no-caps color=primary")

    # 번역 진행(섹션별) — 워커 스레드가 콜백으로 쓰고, UI 타이머가 폴링해 트리 아이콘을 갱신
    translate_state = {"status": {}, "done": 0, "total": 0, "active": False, "failures": {}}
    # 섹션 트리 노드에 직접 붙이는 상태 아이콘 (Quasar QTree의 node.icon/iconColor).
    # 헤더 슬롯을 덮어쓰지 않으므로 번역 선택 체크박스가 그대로 살아 있다.
    # translating은 primary 색 → CSS에서 .q-tree__icon.text-primary만 회전(스피너처럼). done/실패는 정지.
    _TREE_ICON = {
        "translating": ("autorenew", "primary"),
        "done": ("check_circle", "green-6"),
        "passthrough": ("warning", "orange-7"),  # 구조 검증 실패 → 영어 원문 통과 (경고가 맞다)
    }
    _SKIP_ICON = ("remove", "grey-5")  # 체크 해제된 섹션 = 번역 대상 아님
    tree_ref: dict = {}  # {"tree": ui.tree, "nodes": [...]} — 번역 중 아이콘만 갱신

    def _apply_tree_status(nodes: list[dict], status: dict, unticked: set) -> None:
        for n in nodes:
            icon = _TREE_ICON.get(status.get(n["id"], ""))
            if icon is None and n["id"] in unticked:
                icon = _SKIP_ICON  # 번역에서 빠진 섹션임을 한눈에
            if icon:
                n["icon"], n["iconColor"] = icon
            else:
                n.pop("icon", None)
                n.pop("iconColor", None)
            _apply_tree_status(n.get("children") or [], status, unticked)

    def _unticked_ids() -> set:
        return {s.id for s in ctrl.translatable_sections() if not s.translate}

    def update_tree_status() -> None:
        """섹션 트리 아이콘 갱신 — 번역 진행(진행중/완료/실패) + 미선택(–)."""
        tree, nodes = tree_ref.get("tree"), tree_ref.get("nodes")
        if tree is None or nodes is None:
            return
        _apply_tree_status(nodes, translate_state["status"], _unticked_ids())
        tree.update()

    # --- 용어집 편집 상태 (build 스코프 — 번역 탭 편집 + '번역할 섹션'의 용어 추가가 공유) · 자동 저장 ---
    gloss_entries = [{"term": e.term, "korean": e.korean, "policy": e.policy}
                     for e in ctrl.glossary_entries()]

    def merge_dupe_rows() -> int:
        """대소문자만 다른 용어 행을 합친다(먼저 나온 행 유지) — 합쳐서 없앤 행 수 반환.

        빈 행(방금 '행 추가'한 것)은 아직 용어가 아니므로 건드리지 않는다.
        """
        from md4paper.translate.glossary import term_key

        at: dict[str, int] = {}
        kept: list[dict] = []
        for d in gloss_entries:
            key = term_key(d["term"])
            if not key:
                kept.append(d)
                continue
            if key not in at:
                at[key] = len(kept)
                kept.append(d)
            elif not kept[at[key]]["korean"].strip() and d["korean"].strip():
                kept[at[key]]["korean"], kept[at[key]]["policy"] = d["korean"], d["policy"]
        removed = len(gloss_entries) - len(kept)
        if removed:
            gloss_entries[:] = kept
        return removed

    def gloss_autosave() -> None:  # 편집 즉시 glossary.yaml에 저장 (별도 저장 버튼 없이)
        if merge_dupe_rows():
            gloss_rows.refresh()
            ui.notify("대소문자만 다른 중복 용어를 합쳤습니다.", type="info")
        ctrl.save_glossary([GlossaryEntry(**d) for d in gloss_entries if d["term"].strip()])

    @ui.refreshable
    def gloss_rows() -> None:
        if not gloss_entries:
            ui.label("용어집이 비어 있습니다 — 변환 시 자동 생성됩니다. '행 추가'나 왼쪽 '용어 추가'로 채우세요.") \
                .classes("text-sm text-gray-400")
        for i, d in enumerate(gloss_entries):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.input(placeholder="원어", value=d["term"]).bind_value(d, "term") \
                    .on("blur", lambda: gloss_autosave()).props("dense outlined").classes("w-40")
                ui.icon("arrow_forward").classes("text-gray-400")
                ui.input(placeholder="번역어", value=d["korean"]).bind_value(d, "korean") \
                    .on("blur", lambda: gloss_autosave()).props("dense outlined").classes("w-40")
                ui.select(_POLICY, value=d["policy"]).bind_value(d, "policy") \
                    .on("update:model-value", lambda: gloss_autosave()).props("dense outlined").classes("w-44")
                ui.button(icon="delete", on_click=lambda _, idx=i: (
                    gloss_entries.pop(idx), gloss_rows.refresh(), gloss_autosave())) \
                    .props("flat dense round size=sm")

    def add_gloss_row() -> None:
        gloss_entries.append({"term": "", "korean": "", "policy": "translate"})
        gloss_rows.refresh()

    async def add_terms_from_selected() -> None:
        """번역할 섹션에서 새 용어를 뽑아 용어집에 추가 (변환에 쓰던 기본 프로바이더 사용)."""
        from nicegui import run

        n_sec = len(ctrl.ticked_ids())
        if n_sec == 0:
            ui.notify("먼저 번역할 섹션을 선택하세요.", type="warning")
            return
        try:
            prov = config.build_provider()
        except RuntimeError as e:
            ui.notify(str(e), type="negative")
            return

        async def run_extract() -> None:
            ui.notify("선택 섹션에서 용어 추출 중… (AI)")
            try:
                have = [d["term"] for d in gloss_entries if d["term"].strip()]
                new = await run.io_bound(ctrl.new_terms_from_selected, prov, have)
                for e in new:
                    gloss_entries.append({"term": e.term, "korean": e.korean, "policy": e.policy})
                gloss_rows.refresh()
                gloss_autosave()
                ui.notify(f"새 용어 {len(new)}개 추가됨 (자동 저장)." if new
                          else "추가할 새 용어가 없습니다.", type="positive" if new else "info")
            except Exception as ex:  # noqa: BLE001
                ui.notify(str(ex), type="negative")

        if n_sec > 12 or len(ctrl.selected_sections_text()) > 30000:  # 많으면 시간·비용 경고
            with ui.dialog() as dlg, ui.card().classes("gap-2"):
                ui.label(f"{n_sec}개 섹션에서 용어를 추출합니다. 섹션이 많아 시간·비용이 더 들 수 있어요. 계속할까요?")

                async def _yes() -> None:
                    dlg.close()
                    await run_extract()
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("취소", on_click=dlg.close).props("flat")
                    ui.button("계속", on_click=_yes).props("color=primary")
            dlg.open()
        else:
            await run_extract()

    def glossary_tab() -> None:
        from nicegui import run

        async def do_translate() -> None:
            gloss_autosave()
            try:
                prov = config.build_provider()  # 변환에 쓰던 기본 프로바이더
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return
            translate_btn.disable()
            translate_state.update(status={}, done=0, total=0, active=True, failures={})
            update_tree_status()
            failure_panel.refresh()
            bar.visible = True
            bar.value = 0

            def tick() -> None:
                t = translate_state["total"] or 0
                bar.value = (translate_state["done"] / t) if t else 0
                pct = int(bar.value * 100)
                prog_lbl.text = (
                    f"번역 중… {translate_state['done']}/{t} 섹션 ({pct}%) · 병렬 처리"
                    if t else "번역 준비 중…"
                )
                update_tree_status()  # 섹션 트리 아이템에 진행중/완료 아이콘 반영

            timer = ui.timer(0.3, tick)
            try:
                # 워커 스레드가 콜백으로 translate_state를 갱신 → 타이머가 폴링 (NiceGUI 스레드 안전)
                summary = await run.io_bound(
                    ctrl.translate, prov, ctrl.manifest.korean_style,
                    lambda d, t, s: translate_state.update(done=d, total=t, status=s),
                )
                ko_preview.refresh()
                trans_result.refresh()  # 번역 탭 결과(원문·번역 나란히) 갱신
                side_by_side.refresh()
                translate_state["failures"] = summary.get("failures", {})
                bar.value = 1
                saved = await run.io_bound(library.auto_export, ctrl.wd)  # 저장 위치로 자동 저장
                msg = f"번역 완료 (≈${summary['cost_usd']:.4f})"
                if saved:
                    msg += f" · 저장 위치에 {len(saved)}개 파일 저장"
                if summary.get("passthrough"):
                    msg += f" · {summary['passthrough']}개 섹션은 아래 표시(구조 검증 실패)"
                ui.notify(msg, type="positive")
                prog_lbl.text = "✓ " + msg
            except Exception as ex:  # noqa: BLE001
                ui.notify(str(ex), type="negative")
                prog_lbl.text = f"실패: {ex}"
            finally:
                timer.deactivate()
                translate_state["active"] = False
                update_tree_status()
                failure_panel.refresh()
                translate_btn.enable()

        @ui.refreshable
        def failure_panel() -> None:
            fails = translate_state.get("failures") or {}
            if not fails:
                return
            label_by_id = {s.id: s.text for s in ctrl.manifest.sections}
            with ui.column().classes("w-full gap-1 q-pa-sm").style(
                    "background:#fff7ed; border:1px solid #fdba74; border-radius:8px"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.icon("warning", color="orange-7")
                    ui.label(f"번역 실패 {len(fails)}개 섹션 — 구조 검증 실패로 영어 원문 유지") \
                        .classes("text-sm font-medium text-orange-9")
                    ui.space()
                    ui.button("실패 섹션 재번역", icon="refresh", on_click=do_translate) \
                        .props("dense no-caps color=orange").tooltip("실패한 섹션만 다시 번역합니다(성공분은 캐시 재사용)")
                for sid, reason in fails.items():
                    txt = (label_by_id.get(sid, sid) or "")[:50]
                    ui.label(f"· {txt} — {reason}").classes("text-xs text-orange-800")
                ui.label(
                    "번역문이 원문의 마크다운 구조(헤더·표·링크·수식 등)를 그대로 지켜야 하는데, 두 번 시도해도 "
                    "어긋나서 그 섹션은 영어를 그대로 뒀습니다. 재번역해도 계속 실패하면 표·수식이 복잡한 섹션이니 "
                    "번역 후 뷰어에서 직접 손봐도 됩니다.").classes("text-xs text-gray-500")

        # 설명·버튼·진행바는 스크롤해도 항상 보이도록 상단 고정 (용어 행이 길어지므로)
        with ui.column().classes("gloss-sticky w-full gap-2"):
            ui.markdown(
                "**용어집** — 변환할 때 **Abstract·Introduction 본문과 모든 섹션 제목**에서 핵심 용어를 "
                "자동으로 뽑아 만듭니다. 번역 전에 한국어 표기와 처리 방식"
                "(번역 / 음역 / 원문 유지 / 첫 등장 시 병기)을 검토·수정하세요.\n\n"
                "본문에만 나오는 용어는 왼쪽 **번역할 섹션**에서 섹션을 고른 뒤 **용어 추가**를 누르면 "
                "그 섹션에서 새 용어를 뽑아 채웁니다."
            ).classes("text-sm")
            with ui.row().classes("items-center gap-2"):
                ui.button("행 추가", icon="add", on_click=add_gloss_row).props("outline")
                translate_btn = ui.button("이 용어집으로 번역 실행", icon="translate",
                                          on_click=do_translate).props("color=secondary")
            bar = ui.linear_progress(value=0, show_value=False) \
                .props("size=14px rounded color=secondary").classes("w-full")
            bar.visible = False
            prog_lbl = ui.label("").classes("text-sm text-gray-500")
            failure_panel()  # 번역 실패 섹션 이유 + 재번역 버튼 (실패 없으면 안 보임)
        gloss_rows()

    def _sec_page0(sid: str) -> int | None:
        # 섹션 자체 페이지가 없으면(run-in 헤더 등은 headings.json에 없어 page가 비어 있음)
        # 문서 순서상 '직전에 페이지가 있는 섹션'으로 근사한다 — 나란히 보기의 스크롤 싱크가
        # '위쪽 최근 data-page'로 PDF를 맞추는 것과 동일한 동작(그래서 둘 다 켜면 되던 것).
        last_pg: int | None = None
        for s in m.sections:  # cands가 줄 순서로 정렬돼 sections도 문서 순서
            if s.page:
                last_pg = int(s.page) - 1
            if s.id == sid:
                return last_pg
        return None

    async def goto_md(sid: str) -> None:
        """섹션 클릭 → 현재 보이는 패널의 해당 위치로 이동.

        마크다운이 켜져 있으면 마크다운의 그 섹션으로, 마크다운이 꺼지고 PDF만 켜져 있으면
        PDF의 해당 페이지로 스크롤한다 (임의로 다른 패널을 열지 않는다). 나란히면 마크다운을
        스크롤하면 PDF도 싱크로 따라온다.
        """
        import asyncio

        has_pdf = ctrl.pdf_page_count() > 0
        if not conv_view["md"] and conv_view["pdf"] and has_pdf:  # PDF만 켜짐 → PDF로 이동
            pg = _sec_page0(sid)
            if pg is None:
                ui.notify("이 섹션의 PDF 페이지 정보가 없습니다.", type="info")
                return
            await ui.run_javascript(
                f"document.getElementById('pdfpage-{pg}')?.scrollIntoView"
                "({behavior:'smooth', block:'start'});"
            )
            return
        if edit_state["on"]:  # 편집 모드면 렌더 프리뷰로 전환
            edit_state["on"] = False
            refresh_preview()
            edit_toggle.refresh()
        if not conv_view["md"]:  # 마크다운·PDF 둘 다 꺼진 예외 상황 → 마크다운을 켠다
            conv_view["md"] = True
            conv_toolbar.refresh()
            conv_body.refresh()
        await asyncio.sleep(0.12)  # 렌더 후 DOM 준비되도록
        # 마크다운을 해당 섹션으로. 나란히(PDF도 표시)면 클릭 스크롤은 sync 이벤트를 확실히 못 태우므로
        # PDF도 해당 페이지로 직접 이동시킨다(각 패널은 독립 스크롤 컨테이너).
        js = f"document.getElementById('sec-{sid}')?.scrollIntoView({{behavior:'instant', block:'start'}});"
        if conv_view["pdf"] and has_pdf:
            pg = _sec_page0(sid)
            if pg is not None:
                js += (f" document.getElementById('pdfpage-{pg}')"
                       "?.scrollIntoView({behavior:'instant', block:'start'});")
        await ui.run_javascript(js)

    # --- 변환 탭 우측: 마크다운 / PDF 토글 + 나란히(마크다운 스크롤 → PDF 페이지 싱크) ---
    conv_view = {"md": True, "pdf": False, "split": 55}

    def _conv_md_pane() -> None:
        # md-scroll-en: 편집 토글이 스크롤 위치(비율)를 읽는 셀렉터. conv-md: PDF 싱크 컨테이너.
        with ui.element("div").classes("conv-md md-scroll-en").style(
                "height:100%; overflow-y:auto; overscroll-behavior:contain; padding:0 12px"):
            preview()

    async def _cv_set_mode(mode: str) -> None:
        """보기 모드 단일 선택 — 'md' | 'pdf' | 'both'. 재렌더 전 마크다운 스크롤 위치를 캡처해
        재렌더 후 복원하고, PDF도 그 위치에 맞춘다(스크롤 유지 + 정렬)."""
        ratio = None
        if conv_view["md"] and not edit_state["on"]:  # 현재 마크다운이 보이면 스크롤 비율 캡처
            ratio = await ui.run_javascript(
                "(function(){var e=document.querySelector('.conv-md');if(!e)return null;"
                "var m=e.scrollHeight-e.clientHeight;return m>0?e.scrollTop/m:0;})()")
        conv_view["md"] = mode in ("md", "both")
        conv_view["pdf"] = mode in ("pdf", "both")
        if not conv_view["md"] and edit_state["on"]:  # PDF만 보기 → 마크다운 편집 종료
            edit_state["on"] = False
            refresh_preview()
        conv_view["_restore"] = ratio if conv_view["md"] else None
        conv_toolbar.refresh()
        conv_body.refresh()

    @ui.refreshable
    def conv_toolbar() -> None:
        has_pdf = ctrl.pdf_page_count() > 0
        # 현재 모드 (PDF 없으면 항상 마크다운)
        mode = "both" if (conv_view["md"] and conv_view["pdf"] and has_pdf) \
            else ("pdf" if (conv_view["pdf"] and has_pdf) else "md")

        with ui.row().classes("items-center gap-3 w-full no-wrap q-px-xs q-py-sm"):
            opts = {"md": "마크다운"}
            if has_pdf:
                opts["both"] = "마크다운 + PDF"
                opts["pdf"] = "PDF"
            tog = ui.toggle(opts, value=mode, on_change=lambda e: _cv_set_mode(e.value)) \
                .props("no-caps unelevated toggle-color=primary color=grey-3 text-color=grey-8") \
                .classes("conv-viewtoggle")
            if not has_pdf:
                tog.tooltip("PDF 원본 없음 — 마크다운만")
            if mode in ("md", "both"):  # 마크다운이 보일 때만 편집 토글 노출
                ui.separator().props("vertical").classes("h-6")
                edit_toggle()
            ui.space()
            export_fmt_review()
            library_button()
            ui.button("영어 다운로드 (zip)", icon="download", on_click=download_en) \
                .props("flat dense no-caps color=primary")

    @ui.refreshable
    def conv_body() -> None:
        has_pdf = ctrl.pdf_page_count() > 0
        show_md = conv_view["md"]
        show_pdf = conv_view["pdf"] and has_pdf
        if not (show_md or show_pdf):
            show_md = True
        with ui.element("div").classes("vshell"), ui.element("div").classes("vmain"):
            if show_md and show_pdf:
                with ui.splitter(value=conv_view["split"]).classes("w-full").style("height:100%") as sp:
                    sp.on_value_change(lambda e: conv_view.update(split=e.value))
                    with sp.before:
                        _conv_md_pane()
                    with sp.after:
                        _pdf_pane()
                if not edit_state["on"]:  # 편집 중이 아니면 마크다운 스크롤 → PDF 싱크
                    ui.run_javascript(_pdf_sync_js(".conv-md"))
            elif show_pdf:
                _pdf_pane()
            else:
                _conv_md_pane()
        # 모드 전환 전 캡처해 둔 마크다운 스크롤 위치를 복원하고, 나란히면 PDF도 그 위치로 맞춘다.
        r = conv_view.pop("_restore", None)
        if show_md and r:
            ui.timer(0.13, lambda: ui.run_javascript(
                "(function(){var e=document.querySelector('.conv-md');if(!e)return;"
                f"var m=e.scrollHeight-e.clientHeight; e.scrollTop=(m>0?{r}:0)*m;"
                "e.dispatchEvent(new Event('scroll'));})()"), once=True)  # scroll 이벤트 → PDF 싱크 정렬

    # ---------- 본문 레이아웃: 2단계 (변환 → 번역) ----------
    _panel = "p-3 gap-3 w-full"
    _scroll = "height: calc(100vh - 108px); overflow-y: auto"

    def cap(text: str) -> None:
        ui.label(text).classes("text-xs text-gray-500").style("margin:-6px 0 4px 2px")

    def convert_settings() -> None:
        """변환(원어 마크다운) 관련 설정만."""
        from nicegui import run

        ui.label("인용 · 참고문헌").classes("font-semibold text-sm text-primary")

        # 표기 설정을 바꾸면 원본에서 다시 렌더 → 새 표기로 인용 재적용 (평문 [n]에서 다시 링크)
        def reapply() -> None:
            commit()  # 자동 저장 + rerender (raw→평문→새 표기 링크)
            refresh_preview()

        # 참고문헌은 변환 시 API 키가 있으면 자동 파싱된다. 이 버튼은 (1) 키가 없어 자동
        # 파싱이 건너뛰어졌을 때의 수동 실행, (2) 표기·프롬프트를 바꾼 뒤 다시 뽑기용 폴백이다.
        parse_busy = {"on": False}

        async def do_parse() -> None:
            try:
                prov = config.build_provider()
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return
            parse_busy["on"] = True
            parse_control.refresh()
            ui.notify("참고문헌 파싱 중… (LLM)")
            try:
                summary = await run.io_bound(ctrl.parse_references, prov)
                refresh_preview()
                push_cite_tips()  # 새로 파싱한 서지정보를 호버 툴팁 맵에 반영
                ui.notify(f"참고문헌 {summary.get('parsed', 0)}개 파싱 · 본문 인용 {summary.get('linked', 0)}곳 링크 "
                          f"(≈${summary.get('cost_usd', 0):.4f})", type="positive")
            except Exception as ex:  # noqa: BLE001
                ui.notify(str(ex), type="negative")
            finally:
                parse_busy["on"] = False
                parse_control.refresh()

        @ui.refreshable
        def parse_control() -> None:
            if parse_busy["on"]:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    ui.label("참고문헌 파싱 중…").classes("text-xs text-gray-500")
                return
            if ctrl.has_parsed_refs():
                # 이미 파싱됨(보통 변환 때 자동) — 조용히 표시 + 재파싱만 제공
                with ui.row().classes("items-center gap-1"):
                    ui.icon("check_circle", size="16px").classes("text-green-600")
                    ui.label("참고문헌 파싱됨").classes("text-xs text-gray-500")
                    ui.button("다시 파싱", icon="refresh", on_click=do_parse) \
                        .props("flat dense no-caps size=sm").tooltip(
                            "표기·프롬프트를 바꾼 뒤 새로 뽑고 싶을 때만 (LLM 재호출)")
            else:
                ui.label("참고문헌이 아직 파싱되지 않았습니다 (변환 시 API 키가 없었을 수 있어요).") \
                    .classes("text-xs text-orange-600")
                ui.button("참고문헌 파싱 (LLM)", icon="link", on_click=do_parse).props("dense outline")

        parse_control()

        ui.label("본문 인용 표기 (조합 가능)").classes("text-sm")
        parts_state = {p: (p in m.citation_parts) for p in ("number", "authoryear", "short")}

        def update_parts() -> None:
            chosen = [p for p in ("number", "authoryear", "short") if parts_state[p]]
            ctrl.set_setting("citation_parts", chosen or ["number"])
            reapply()

        for pkey, plabel in (("number", "번호  [1]"),
                             ("authoryear", "저자·연도  (Vaswani et al. 2017)"),
                             ("short", "약칭  (Transformer)")):
            ui.checkbox(plabel, value=parts_state[pkey],
                        on_change=lambda e, k=pkey: (parts_state.__setitem__(k, e.value), update_parts())).props("dense")
        cap("여러 개 고르면 함께 표시됩니다 — 예: 번호+약칭 → [1, Transformer]. 모두 참고문헌으로 링크.")
        ui.switch("참고문헌에 DOI/arXiv 링크 달기", value=m.reference_links,
                  on_change=lambda e: (ctrl.set_setting("reference_links", e.value), reapply()))
        cap("참고문헌 제목을 클릭하면 원문 논문으로 바로 이동합니다.")

        ui.separator()
        ui.label("그림·표").classes("font-semibold text-sm text-primary")

        def set_capstyle(v: str) -> None:
            ctrl.set_setting("caption_style", v)
            commit()  # 재조립 → 새 캡션 표기로 프리뷰 갱신
            refresh_preview()
            capstyle_picker.refresh()

        ui.label("그림·표 캡션 표기").classes("text-sm")

        @ui.refreshable
        def capstyle_picker() -> None:
            # 마크다운 문법이 아니라 렌더된 모습을 보여주고 클릭해 선택
            for key, example in _CAPSTYLE_EX.items():
                sel = ctrl.manifest.caption_style == key
                row = ui.row().classes(
                    "items-center gap-2 w-full no-wrap q-px-sm q-py-xs rounded-borders cursor-pointer"
                ).style("background: rgba(35,131,226,.12)" if sel else "background: transparent")
                with row:
                    ui.icon("radio_button_checked" if sel else "radio_button_unchecked", size="18px") \
                        .classes("text-primary" if sel else "text-grey-5")
                    ui.html(example).classes("text-sm").tooltip(_CAPSTYLE_NAME[key])
                row.on("click", lambda e, k=key: set_capstyle(k))

        capstyle_picker()
        cap("캡션을 본문과 구별되게 표시합니다 (그림·표 공통).")

        ui.separator()
        ui.label("저자").classes("font-semibold text-sm text-primary")
        ui.label("저자 표기 (이름은 항상 표시)").classes("text-sm")
        apar = {p: (p in m.author_parts) for p in ("email", "affiliation")}

        def update_author_parts() -> None:
            chosen = [p for p in ("email", "affiliation") if apar[p]]
            changed = ctrl.set_author_parts(chosen)  # 저장된 구조화 저자로 raw.md 저자 블록만 재렌더
            commit()  # 저장 + 재조립
            refresh_preview()
            if not changed and not ctrl.wd.authors_json.exists():
                ui.notify("이 논문엔 구조화된 저자 정보가 없어(옛 변환·키 없음) 표시만 저장됩니다. "
                          "재변환하면 반영됩니다.", type="info")

        for akey, alabel in (("email", "이메일"), ("affiliation", "소속")):
            ui.checkbox(alabel, value=apar[akey],
                        on_change=lambda e, k=akey: (apar.__setitem__(k, e.value), update_author_parts())) \
                .props("dense")
        cap("둘 다 끄면 이름만 표시됩니다. 저자 정보는 변환 시 AI가 저자별로 정리한 것입니다.")

    def translate_settings() -> None:
        """번역 관련 설정 — 방식은 아코디언, 번역할 섹션이 주인공."""
        def set_tsetting(field: str, value) -> None:  # noqa: ANN001
            """번역 설정 변경 → sections.yaml에 즉시 저장 (새로고침해도 유지)."""
            ctrl.set_setting(field, value)
            ctrl.save()

        def translate_method_panel() -> None:
            with ui.expansion("번역 방식 (문체·범위)", icon="tune").classes("w-full").props("dense"):
                ui.select(_KSTYLE, value=m.korean_style if m.korean_style in _KSTYLE else "해라체",
                          label="한국어 문체",
                          on_change=lambda e: set_tsetting("korean_style", e.value)).props("dense outlined").classes("w-full")
                cap("문장 끝맺음 말투. 논문은 보통 해라체(~한다)를 씁니다.")
                if ctrl.title_section() is not None:
                    ui.switch("논문 제목도 번역", value=ctrl.translate_title(),
                              on_change=lambda e: (ctrl.set_translate_title(e.value), ctrl.save()))
                    cap("문서 맨 위 논문 제목 한 줄. 끄면 영어 원제를 그대로 둡니다.")
                ui.switch("섹션 제목도 한국어로 번역", value=m.translate_headers,
                          on_change=lambda e: set_tsetting("translate_headers", e.value))
                cap("끄면 헤더(3.1 Method 등)는 영어 그대로 두고 본문만 번역합니다.")
                ui.switch("참고문헌 목록도 번역", value=m.translate_references,
                          on_change=lambda e: set_tsetting("translate_references", e.value))
                cap("보통은 끄는 게 좋습니다 — 서지 정보(저자·제목)는 영어 원문이 정확합니다.")
                cap("Section/Figure/Table 같은 상호참조는 항상 영어로 유지됩니다.")

        # --- 번역할 섹션 고르기 (Tree Select) — 주 영역 ---
        ui.label("번역할 섹션").classes("font-semibold text-sm text-primary")
        cap("체크한 섹션만 번역합니다 (LLM 비용·시간 절약). 나머지는 영어 원문 그대로 남습니다. 섹션 단위로 병렬 번역됩니다.")

        @ui.refreshable
        def section_picker() -> None:
            nodes = ctrl.section_tree()
            if not nodes:
                ui.label("선택할 섹션이 없습니다.").classes("text-xs text-gray-400")
                return

            def on_tick(e) -> None:  # noqa: ANN001 — NiceGUI 이벤트
                ctrl.set_translate_ids(e.value)  # NiceGUI가 _props['ticked']는 자동 동기화함
                ctrl.save()  # 선택도 즉시 저장 (새로고침해도 유지)
                update_tree_status()  # 해제된 섹션에 '–' 표시 갱신
                count_label.refresh()

            # 초기 체크 상태는 _props['ticked']로만 설정 (별도 :ticked 바인딩을 주면 충돌해 tick이 깨진다)
            tree = ui.tree(nodes, label_key="label", node_key="id", tick_strategy="strict", on_tick=on_tick)
            tree._props["ticked"] = ctrl.ticked_ids()
            # ui.tree는 nodes를 복사해 _props['nodes']에 넣는다 → 원본을 바꿔도 클라이언트에 안 나간다.
            # 실제 렌더되는 복사본을 가리켜 변형해야 tree.update()로 아이콘이 반영된다.
            tree_ref["tree"], tree_ref["nodes"] = tree, tree._props["nodes"]
            _apply_tree_status(tree._props["nodes"], translate_state["status"], _unticked_ids())

            @ui.refreshable
            def count_label() -> None:
                counts = len(ctrl.translatable_sections())
                ui.label(f"{len(ctrl.ticked_ids())} / {counts} 섹션 선택됨").classes("text-xs text-gray-500")
            count_label()

        with ui.row().classes("gap-2 items-center"):
            ui.button("전체 선택", on_click=lambda: (ctrl.set_all_translate(True), ctrl.save(),
                                                 section_picker.refresh())).props("outline dense")
            ui.button("전체 해제", on_click=lambda: (ctrl.set_all_translate(False), ctrl.save(),
                                                 section_picker.refresh())).props("outline dense")
            ui.button("용어 추가", icon="playlist_add", on_click=add_terms_from_selected).props("outline dense") \
                .tooltip("선택한 섹션에서 새 용어를 뽑아 용어집에 추가")
        _translate_refs["picker"] = section_picker  # 변환 탭 레벨 변경 시 갱신되도록 등록
        section_picker()  # 번역 중 진행 상태는 트리 아이템의 아이콘으로 직접 표시된다

        ui.separator().classes("my-2")
        translate_method_panel()  # 번역 방식 설정은 섹션 선택 아래에 (덜 자주 만지므로 아코디언)

    async def download_en() -> None:
        if not ctrl.en_markdown():
            ui.notify("영어 마크다운이 없습니다 — 먼저 변환하세요.", type="warning")
            return
        name, data = ctrl.export_zip("en", config.resolve_export_target())  # 마크다운(형식 변환) + images/ zip
        await desktop.deliver(name, data)

    async def download_ko() -> None:
        if not ctrl.ko_markdown():
            ui.notify("아직 번역 결과가 없습니다.", type="warning")
            return
        name, data = ctrl.export_zip("ko", config.resolve_export_target())
        await desktop.deliver(name, data)

    async def save_to_library() -> None:
        """홈에서 지정한 '저장 위치' 폴더에 결과 마크다운을 저장 (자동 저장을 꺼 뒀을 때도 수동으로)."""
        from nicegui import run as ng_run

        if not library.configured():
            ui.notify("저장 위치가 지정돼 있지 않습니다 — 홈의 '저장 위치'에서 폴더를 고르세요.", type="warning")
            return
        try:
            saved = await ng_run.io_bound(library.export_paper, ctrl.wd)
        except OSError as ex:
            ui.notify(f"저장 실패: {ex}", type="negative")
            return
        if not saved:
            ui.notify("저장할 마크다운이 없습니다.", type="warning")
            return
        ui.notify("저장됨 · " + " · ".join(str(p) for p in saved), type="positive")

    def library_button() -> None:
        """저장 위치가 지정돼 있을 때만 보이는 '폴더로 저장' 버튼."""
        if not library.configured():
            return
        dests = " · ".join(str(d) for d in (library.dir_for(k) for k in library.KINDS) if d)
        ui.button("폴더로 저장", icon="drive_file_move", on_click=save_to_library) \
            .props("flat dense no-caps color=grey-8").tooltip(f"저장 위치: {dests}")

    # 내보내기 형식 드롭다운 — 변환·번역·뷰어 탭 다운로드 옆에 두되 전역 설정(config)을 그대로 쓴다.
    # 공유 refreshable이라 한 탭에서 바꾸면 다른 탭·홈의 선택도 같은 값으로 동기화된다.
    @ui.refreshable
    def export_fmt_review() -> None:
        def _pick(e) -> None:  # noqa: ANN001
            config.set_section_value("output", "export_target", e.value)
            export_fmt_review.refresh()
            ui.notify(f"내보내기 형식 · {_EXPORT_TARGET[e.value]}", type="positive", timeout=1200)
        ui.select(_EXPORT_TARGET, value=config.resolve_export_target(), on_change=_pick) \
            .props("dense outlined").classes("w-28").tooltip("다운로드 zip에 적용하는 형식 (전역 설정)")

    # --- 레이아웃 자동 수정 (변환 탭) — 깨진 헤더·수식·문단을 처음부터 끝까지 훑어 AI로 고친다 ---
    def _after_relayout() -> None:
        """raw.md·구조가 통째로 바뀌었으므로 이 화면의 파생 뷰를 전부 다시 그린다."""
        refresh_preview()
        section_list.refresh()
        batch_level_panel.refresh()
        _refresh_translate_tree()
        edit_toggle.refresh()
        push_cite_tips()  # 재조립하며 인용 링크가 다시 붙었다 → 호버 툴팁 맵 갱신
        library.auto_export(ctrl.wd)

    with ui.dialog() as fix_dialog, ui.card().classes("gap-3").style("width:560px;max-width:92vw"):
        ui.label("레이아웃 자동 수정").classes("text-lg font-bold")
        ui.markdown(
            "PDF에서 뽑은 마크다운을 **처음부터 끝까지 나눠 훑으며** AI가 레이아웃만 고칩니다 — "
            "쪼개진 헤더(`## 2` + `## Background` → `## 2 Background`), 깨진 수식(`x t` → `$x_t$`), "
            "끊긴 문단·목록·표 같은 것들. **글의 내용(단어)은 바꾸지 않습니다.**\n\n"
            "고친 문서로 **섹션 트리와 일괄 레벨 조정도 다시 만들어집니다** "
            "(지금 정해 둔 레벨·번역 여부·변환 설정은 그대로 이어받습니다)."
        ).classes("text-sm")
        fix_info = ui.label("").classes("text-xs text-gray-500")
        fix_prompt = ui.textarea(
            label="추가 지시 (선택)",
            placeholder="예) 페이지마다 반복되는 학회명 머리글 줄을 지워줘 / 의사코드 블록은 코드 펜스로 묶어줘",
        ).props('outlined autogrow input-style="min-height:66px"').classes("w-full")
        fix_warn = ui.label("").classes("text-xs text-orange-8")
        fix_bar = ui.linear_progress(value=0, show_value=False) \
            .props("size=14px rounded").classes("w-full")
        fix_bar.visible = False
        fix_status = ui.label("").classes("text-sm text-gray-600")

        async def run_layout_fix() -> None:
            from nicegui import run

            try:
                prov = config.build_provider()  # 변환에 쓰던 기본 프로바이더
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return
            for w in (fix_apply, fix_cancel, fix_undo, fix_prompt):
                w.disable()
            fix_dialog.props(add="persistent")  # 도는 동안 바깥 클릭·ESC로 닫히지 않게
            fix_bar.visible = True
            fix_bar.value = 0
            prog = {"done": 0, "total": 0}

            def tick() -> None:  # 워커 스레드가 prog를 갱신 → 타이머가 폴링 (NiceGUI 스레드 안전)
                t = prog["total"]
                fix_bar.value = (prog["done"] / t) if t else 0
                fix_status.text = (f"수정 중… {prog['done']}/{t} 구간 ({int(fix_bar.value * 100)}%)"
                                   if t else "구간을 나누는 중…")

            timer = ui.timer(0.3, tick)
            try:
                summary = await run.io_bound(
                    ctrl.fix_layout, prov, fix_prompt.value or "",
                    lambda d, t: prog.update(done=d, total=t),
                )
                if not summary.get("changed"):
                    ui.notify("고칠 레이아웃을 찾지 못했습니다 — 문서는 그대로입니다.", type="info")
                else:
                    _after_relayout()
                    msg = (f"레이아웃 수정 완료 · {summary['chunks']}구간 중 {summary['fixed']}개 수정 "
                           f"(≈${summary['cost_usd']:.4f})")
                    if summary["kept"]:
                        msg += f" · {summary['kept']}개 구간은 내용 보존 검증에 걸려 원문 유지"
                    ui.notify(msg, type="positive")
                fix_dialog.close()
            except Exception as ex:  # noqa: BLE001 — API 오류 등을 그대로 보여준다
                ui.notify(str(ex), type="negative")
                fix_status.text = f"실패: {ex}"
            finally:
                timer.deactivate()
                fix_dialog.props(remove="persistent")
                fix_bar.visible = False
                for w in (fix_apply, fix_cancel, fix_prompt):
                    w.enable()
                fix_undo.set_visibility(ctrl.can_undo_layout_fix())
                fix_undo.enable()

        async def undo_layout_fix() -> None:
            from nicegui import run

            if not await run.io_bound(ctrl.undo_layout_fix):
                ui.notify("되돌릴 이전 상태가 없습니다.", type="warning")
                return
            _after_relayout()
            ui.notify("레이아웃 수정 전으로 되돌렸습니다.", type="positive")
            fix_dialog.close()

        with ui.row().classes("items-center w-full gap-2"):
            fix_undo = ui.button("직전 상태로 되돌리기", icon="undo", on_click=undo_layout_fix) \
                .props("flat dense no-caps color=grey-8").tooltip("마지막 수정 직전의 마크다운·구조로 복구")
            ui.space()
            fix_cancel = ui.button("취소", on_click=fix_dialog.close).props("flat no-caps")
            fix_apply = ui.button("적용", icon="auto_fix_high", on_click=run_layout_fix) \
                .props("color=primary no-caps")

    def open_fix_dialog() -> None:
        plan = ctrl.layout_fix_plan()
        prov = config.resolve_provider()
        fix_info.text = (f"본문 {plan['chars']:,}자를 {plan['chunks']}개 구간으로 나눠 "
                         f"{_short_provider(prov)} {config.resolve_model(prov)}에 보냅니다.")
        fix_warn.text = ("직접 편집한 마크다운이 있습니다 — 문서를 다시 만들면서 그 편집은 사라집니다."
                         if ctrl.has_manual_edit() else "")
        fix_status.text = ""
        fix_undo.set_visibility(ctrl.can_undo_layout_fix())
        fix_dialog.open()

    # ===== 단계 패널 (탭 컨트롤은 헤더에) =====
    # 번역까지 끝난 논문은 뷰어를, 아니면 변환 탭을 기본으로 연다.
    _start_tab = step_sbs if ctrl.ko_markdown() else step_convert
    with ui.tab_panels(steps, value=_start_tab).classes("w-full").props("keep-alive"):
        # ---------- 1단계: 변환 ----------
        with ui.tab_panel(step_convert).classes("p-0"):
            with ui.splitter(value=46).classes("w-full").style("height: calc(100vh - 108px)") as sp1:
                with sp1.before:
                    with ui.column().classes(_panel).style(_scroll):
                        ui.button("레이아웃 자동 수정", icon="auto_fix_high", on_click=open_fix_dialog) \
                            .props("outline no-caps color=primary").classes("w-full").tooltip(
                                "쪼개진 헤더·깨진 수식·끊긴 문단을 AI가 문서 전체에서 찾아 고칩니다")
                        cap("PDF 추출이 어그러뜨린 부분을 AI가 훑어 고치고, 섹션 트리를 다시 만듭니다.")

                        with ui.expansion("변환 설정", icon="settings").classes("w-full"):
                            convert_settings()

                        with ui.expansion("일괄 레벨 조정", icon="format_list_numbered").classes("w-full"):
                            batch_level_panel()

                        with ui.expansion("섹션 트리", icon="account_tree").classes("w-full"):
                            ui.label("제목 1~6: 헤더 단계 · 본문으로: 헤더를 풀어 제목 텍스트를 일반 문단으로 남김") \
                                .classes("text-xs text-gray-500")
                            ui.label("위에 합침: 제목 줄을 없애고 그 내용을 위 섹션에 합침 · 통째 삭제: 제목·본문 모두 제거") \
                                .classes("text-xs text-gray-500")
                            ui.label("기울임: run-in 소제목을 기울임 문단으로 표기").classes("text-xs text-gray-500")
                            ui.label("제목을 클릭하면 열려 있는 패널(마크다운이면 마크다운, PDF만이면 PDF)의 해당 위치로 이동합니다.") \
                                .classes("text-xs text-gray-500")
                            section_list()

                with sp1.after:
                    with ui.column().classes("p-3 w-full gap-2").style("height: calc(100vh - 108px)"):
                        conv_toolbar()   # 마크다운/PDF 토글 + 직접편집 + 다운로드
                        conv_body()      # 나란히면 마크다운 스크롤 → PDF 페이지 싱크

        # ---------- 2단계: 번역 ----------
        with ui.tab_panel(step_translate).classes("p-0"):
            with ui.splitter(value=32).classes("w-full").style("height: calc(100vh - 108px)") as sp2:
                with sp2.before:
                    with ui.column().classes(_panel).style(_scroll):
                        translate_settings()
                with sp2.after:
                    with ui.column().classes("p-3 w-full").style(_scroll):
                        with ui.row().classes("items-center w-full no-wrap"):
                            with ui.tabs() as ttabs:
                                tab_gloss = ui.tab("용어집 · 번역 실행")
                                tab_ko = ui.tab("결과 (원문·번역)")
                            ui.space()
                            export_fmt_review()
                            ui.button("한국어 다운로드 (zip)", icon="download", on_click=download_ko) \
                                .props("flat dense no-caps color=primary")
                        with ui.tab_panels(ttabs, value=tab_gloss).classes("w-full").props("keep-alive"):
                            with ui.tab_panel(tab_gloss):
                                glossary_tab()
                            with ui.tab_panel(tab_ko):
                                trans_result()  # 원문/번역 토글 + 나란히 정렬

        # ---------- 3단계: 뷰어 (EN | KO) ----------
        with ui.tab_panel(step_sbs).classes("p-0"):
            with ui.column().classes("p-3 w-full gap-2").style("height: calc(100vh - 108px)"):
                viewer_toolbar()
                side_by_side()


# 홈 드롭존 — 실제 q-uploader를 투명하게 위에 겹쳐(드롭·클릭 캐치) 아래 dashed 힌트를 보여준다.
_HOME_CSS = """
.md4-dropwrap { position: relative; width: 100%; }
.md4-upload { position: absolute; inset: 0; z-index: 2; opacity: 0; cursor: pointer; }
.md4-upload, .md4-upload > .q-uploader { width: 100%; height: 100%; min-height: 156px; max-height: none; }
/* 헤더(= 파일 선택 버튼 + 그 안의 <input type=file>)를 드롭존 전체로 펼친다. 부모가 opacity:0이라
   보이지는 않는다. display:none으로 감추면 클릭으로 파일 선택창을 열 방법이 없어져 서버를 거쳐
   JS로 input.click()을 불러야 하는데, 그러면 사용자 제스처가 끊겨 웹뷰(WKWebView)가 창을 막는다. */
.md4-upload .q-uploader__header { position: absolute; inset: 0; padding: 0; }
.md4-upload .q-uploader__header-content { height: 100%; padding: 0; }
.md4-upload .q-uploader__header .q-btn { position: absolute; inset: 0; }
/* 업로더의 파일 목록은 보이지도 않으면서 헤더 위에 겹쳐 클릭을 가로챈다 (DOM 순서상 위). */
.md4-upload .q-uploader__list { pointer-events: none; }
.md4-hint { position: relative; z-index: 1; border: 2px dashed #cfcfcf; border-radius: 14px;
  min-height: 156px; padding: 22px; transition: border-color .15s ease, background .15s ease; }
.md4-dropwrap:hover .md4-hint { border-color: #2383e2; background: rgba(35,131,226,.05); }
.md4-spin { animation: md4spin 1s linear infinite; }
@keyframes md4spin { to { transform: rotate(360deg); } }
.md4-recent-new { box-shadow: inset 4px 0 0 #21ba45; }
.md4-new-dot { width: 8px; height: 8px; border-radius: 50%; background: #2383e2; flex: 0 0 8px; }
@media (prefers-color-scheme: dark) {
  .md4-hint { border-color: #464646; }
  .md4-dropwrap:hover .md4-hint { background: rgba(35,131,226,.14); }
}
"""


def _mark_opened(wd: WorkDir) -> None:
    """리뷰 화면을 열었음을 status.json에 기록 (홈의 '미열람' 표시 해제)."""
    import time

    st = wd.load_status()
    if not st.get("opened_at"):
        st["opened_at"] = time.time()
        wd.save_status(st)


def save_source(data: bytes, filename: str, upload_dir: Path) -> Path:
    """업로드 바이트를 파일별 전용 하위 폴더에 저장하고 원본 경로 반환 (변환 전에 디스크에 보존).

    같은 이름의 폴더에 **이미 변환한 논문**(.md4)이 들어 있으면 덮어쓰지 않고 '<이름> (2)'처럼
    고유 폴더를 새로 만든다 → 같은 PDF를 다시 올려도 기존 항목이 살아남는다.
    반면 .md4가 없는 폴더는 **변환되지 않은 업로드 잔여물**(중간에 껐거나 큐가 날아간 경우)이므로
    그대로 재사용한다 — 안 그러면 실패한 업로드가 이름을 영영 점유해 (2)가 계속 붙는다.
    (같은 파일명이 큐에서 처리 중이면 호출 전에 걸러지므로, 여기서 만나는 껍데기는 대기 중이 아니다.)
    """
    from md4paper.workdir import is_upload_stub

    stem = Path(filename).stem
    workspace = upload_dir / stem
    if workspace.exists() and not is_upload_stub(workspace):
        i = 2
        while (upload_dir / f"{stem} ({i})").exists() and not is_upload_stub(upload_dir / f"{stem} ({i})"):
            i += 1
        workspace = upload_dir / f"{stem} ({i})"
    workspace.mkdir(parents=True, exist_ok=True)
    dest = workspace / filename
    dest.write_bytes(data)
    return dest


def convert_source(src_path: Path, backend: str, ocr: bool = False, flavor: str | None = None) -> WorkDir:
    """이미 저장된 원본 파일을 변환 (백그라운드 스레드에서 호출). 결과 .md4는 원본 옆 폴더에.

    LLM 키가 있으면 앞부분(저자·서지) 정규화에 LLM을 쓴다(포맷에 강함). 없으면 규칙 폴백.
    en.md는 범용(canonical)으로 저장하고, 뷰어별 형식(Notion/Obsidian)은 다운로드 시 변환한다
    (flavor 인자는 하위호환용으로 남겨두나 조립엔 영향 없음).
    """
    from md4paper import config, pipeline

    provider = None
    try:
        provider = config.build_provider()
    except RuntimeError:
        pass  # 키 없음 → 규칙 기반 폴백
    src_path = Path(src_path)
    wd = WorkDir(src_path.parent / f"{src_path.stem}.md4")
    pipeline.convert(src_path, wd, backend=backend, ocr=ocr, flavor=Flavor.STANDARD, provider=provider)
    return wd


def save_and_convert(
    data: bytes, filename: str, upload_dir: Path, backend: str, flavor: str, ocr: bool = False,
) -> WorkDir:
    """저장 + 변환 (테스트·CLI 호환 래퍼)."""
    return convert_source(save_source(data, filename, upload_dir), backend, ocr, flavor)


_QUEUE_ACTIVE = ("pending", "extracting", "cite", "glossary", "meta")


async def _process_queue(state: dict) -> None:
    """전역 변환 큐를 한 번에 하나씩 순차 처리 (background_tasks). 네비게이션 없음 — 상태만 갱신.

    각 항목: 추출 → (키 있으면) 참고문헌 → 용어집. 한 항목이 실패해도 다음 항목은 계속 진행한다.
    """
    from nicegui import run

    if state.get("worker_running"):
        return
    state["worker_running"] = True
    try:
        while True:
            item = next((q for q in state["queue"] if q["status"] == "pending"), None)
            if item is None:
                break
            item["status"], item["since"] = "extracting", time.monotonic()
            try:
                wd = await run.io_bound(convert_source, Path(item["src_path"]), item["backend"], item["ocr"])
                item["wd_root"] = str(wd.root)
                try:
                    import json as _json
                    item["garbled"] = int(_json.loads(wd.meta_json.read_text(encoding="utf-8")).get("garbled_chars", 0) or 0)
                except Exception:  # noqa: BLE001
                    item["garbled"] = 0
                if _has_llm_key():
                    item["status"], item["since"] = "cite", time.monotonic()
                    await run.io_bound(_auto_cite, wd)
                    item["status"], item["since"] = "glossary", time.monotonic()
                    await run.io_bound(_auto_glossary, wd)
                    item["status"], item["since"] = "meta", time.monotonic()
                    await run.io_bound(_auto_metadata, wd)
                    # 서지에서 폴더·PDF 이름 자동 정리 (이름 규칙 [output].naming)
                    wd = await run.io_bound(_auto_rename, wd, state["upload_dir"])
                    item["wd_root"] = str(wd.root)
                # 전역 '저장 위치'가 지정돼 있으면 결과 마크다운을 거기에 쌓는다 (리네임 후 최종 이름으로)
                await run.io_bound(library.auto_export, wd)
                item["status"], item["done_at"] = "done", time.monotonic()
            except Exception as ex:  # noqa: BLE001 — 항목 실패가 큐 전체를 막지 않게
                item["status"], item["error"] = "failed", str(ex)
    finally:
        state["worker_running"] = False


_LIB_LABEL = {"en": "영어 마크다운", "ko": "한국어 마크다운", "pdf": "PDF 원본"}


def build_location_settings(state: dict, on_workspace_change) -> None:  # noqa: ANN001 — () -> None
    """홈 '저장 위치' 패널 — 변환한 논문이 쌓일 폴더(영어·한국어 따로) + 작업 폴더.

    폴더는 OS 기본 대화상자로 고르고(로컬 앱이라 서버 = 내 컴퓨터), 대화상자를 못 쓰는
    환경에서는 경로를 직접 입력한다. 값은 전역 설정(config.toml)이라 다음 실행에도 유지된다.
    """
    from nicegui import run, ui

    from md4paper.ui import folder_dialog
    from md4paper.workdir import recent_workdirs

    can_pick = folder_dialog.available() or desktop.active()  # 앱 창은 자체 대화상자를 갖는다

    def _no_dialog(btn) -> None:  # noqa: ANN001 — ui.button
        btn.disable()
        btn.tooltip("이 환경에선 폴더 대화상자를 열 수 없습니다 — 경로를 직접 입력하세요")

    def set_dir(which: str, path: str | None) -> None:
        """폴더 지정/해제 — 값이 실제로 바뀔 때만 저장하고 다시 그린다(blur마다 포커스가 튀지 않게)."""
        raw = str(path).strip() if path else ""
        new = Path(raw).expanduser() if raw else None
        if library.dir_for(which) == new:
            return
        config.set_library_dir(which, str(new) if new else None)
        lib_rows.refresh()
        ui.notify(f"{_LIB_LABEL[which]} → {new}" if new else f"{_LIB_LABEL[which]} 저장 위치 해제됨",
                  type="positive" if new else "info")

    async def pick(which: str) -> None:
        cur = library.dir_for(which) or state["upload_dir"]
        picked = await desktop.choose_folder(f"{_LIB_LABEL[which]}을 쌓을 폴더 선택", str(cur))
        if picked:  # 취소면 조용히 원래 값 유지
            set_dir(which, picked)

    @ui.refreshable
    def lib_rows() -> None:
        for which in library.KINDS:
            cur = library.dir_for(which)
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(_LIB_LABEL[which]).classes("text-sm shrink-0").style("width:104px")
                inp = ui.input(value=str(cur or ""), placeholder="폴더를 고르거나 경로를 붙여넣으세요") \
                    .props("dense outlined").classes("flex-grow")
                # 직접 입력은 포커스를 벗어나거나 Enter를 칠 때만 저장(타이핑 중 매번 쓰지 않게)
                inp.on("blur", lambda _, w=which, i=inp: set_dir(w, (i.value or "").strip() or None))
                inp.on("keydown.enter", lambda _, w=which, i=inp: set_dir(w, (i.value or "").strip() or None))
                btn = ui.button(icon="folder_open", on_click=lambda _, w=which: pick(w)) \
                    .props("dense outline").tooltip("폴더 선택 대화상자 열기")
                if not can_pick:
                    _no_dialog(btn)
                clear = ui.button(icon="close", on_click=lambda _, w=which: set_dir(w, None)) \
                    .props("flat dense round size=sm color=grey").tooltip("이 폴더 설정 해제")
                clear.set_visibility(cur is not None)
        if library.same_folder():
            ui.label("두 폴더가 같아서 파일 이름 뒤에 .en / .ko를 붙여 구분합니다.") \
                .classes("text-xs text-orange-600")

    async def export_all() -> None:
        if not library.configured():
            ui.notify("먼저 폴더를 지정하세요.", type="warning")
            return
        roots = [r["root"] for r in recent_workdirs(state["upload_dir"], limit=1000)]
        if not roots:
            ui.notify("내보낼 논문이 없습니다.", type="warning")
            return
        export_btn.props("loading")
        try:
            ok, failed = await run.io_bound(library.export_many, roots)
        finally:
            export_btn.props(remove="loading")
        msg = f"{ok}편을 저장 위치에 내보냈습니다" + (f" · {failed}편 실패" if failed else "")
        ui.notify(msg, type="positive" if ok else "warning")

    async def pick_workspace() -> None:
        picked = await desktop.choose_folder("작업 폴더 선택 (원본 PDF·중간 파일)", str(state["upload_dir"]))
        if not picked:
            return
        config.set_section_value("output", "workspace", picked)
        state["upload_dir"] = Path(picked)
        ws_row.refresh()
        on_workspace_change()
        ui.notify(f"작업 폴더 → {picked}", type="positive")

    @ui.refreshable
    def ws_row() -> None:
        cur = str(state["upload_dir"])
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label("작업 폴더").classes("text-sm shrink-0").style("width:104px")
            ui.label(cur).classes("text-xs text-gray-500 flex-grow min-w-0 truncate").tooltip(cur)
            btn = ui.button("변경", icon="folder_open", on_click=pick_workspace) \
                .props("dense outline no-caps")
            if not can_pick:
                _no_dialog(btn)

    # --- 파일 이름 규칙 (논문 폴더·PDF·저장 위치 사본이 모두 이 규칙의 기준명을 공유) ---
    from md4paper import paper_meta

    def save_template(value: str) -> None:
        tpl = (value or "").strip()
        if tpl == config.resolve_naming_template():
            return
        err = config.naming_template_error(tpl)
        if err:
            ui.notify(f"이름 규칙 오류: {err}", type="negative")
            naming_rows.refresh()  # 입력을 저장된 값으로 되돌림
            return
        config.set_section_value("output", "naming", tpl)
        naming_rows.refresh()
        ui.notify(f"이름 규칙 저장됨 · 예: {paper_meta.naming_preview()}", type="positive")

    async def enrich_now() -> None:
        """연도·venue가 빈 논문만 온라인 서지에서 채우고, 바뀌면 이름까지 정리."""
        from md4paper import enrich
        from md4paper.workdir import recent_workdirs

        enrich_btn.props("loading")
        try:
            roots = [r["root"] for r in recent_workdirs(state["upload_dir"], limit=100_000,
                                                        include_hidden=True)]
            counts = await run.io_bound(enrich.enrich_many, roots,
                                        mailto=config.resolve_enrich_mailto() or None)
            renamed = 0
            if counts["papers"]:
                renamed = (await run.io_bound(paper_meta.apply_naming, state["upload_dir"]))["renamed"]
        finally:
            enrich_btn.props(remove="loading")
        msg = (f"{counts['checked']}편 확인 · {counts['papers']}편 보강"
               f" (연도 {counts.get('year', 0)} · 학회 {counts.get('venue', 0)})")
        if renamed:
            msg += f" · 이름 {renamed}편 정리"
        ui.notify(msg, type="positive" if counts["papers"] else "info", timeout=6000)
        on_workspace_change()

    async def apply_naming_now() -> None:
        naming_btn.props("loading")
        try:
            counts = await run.io_bound(paper_meta.apply_naming, state["upload_dir"])
        finally:
            naming_btn.props(remove="loading")
        msg = f"{counts['renamed']}편 이름 변경"
        if counts["no_meta"]:
            msg += f" · {counts['no_meta']}편은 서지 정보가 없어 건너뜀"
        ui.notify(msg, type="positive" if counts["renamed"] else "info")
        on_workspace_change()  # 목록·경로 표시 새로고침

    @ui.refreshable
    def naming_rows() -> None:
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label("이름 규칙").classes("text-sm shrink-0").style("width:104px")
            inp = ui.input(value=config.resolve_naming_template()) \
                .props("dense outlined").classes("flex-grow min-w-0")
            inp.on("blur", lambda _, i=inp: save_template(i.value))
            inp.on("keydown.enter", lambda _, i=inp: save_template(i.value))
        ui.label(f"예: {paper_meta.naming_preview()} — " +
                 "조각: {year} 연도 · {title} 제목 약칭 · {author} 1저자 성 · {venue} 학회") \
            .classes("text-xs text-gray-400")

    with ui.expansion("저장 위치 — 변환한 논문을 모아둘 폴더", icon="folder_open",
                      value=False).classes("w-full").props("dense"):
        ui.label("변환·번역이 끝난 마크다운(원하면 원본 PDF도)을 고른 폴더에 쌓습니다. 영어·한국어·PDF를 "
                 "각각 다른 폴더로 보낼 수 있어요 (예: Obsidian 볼트의 Papers/EN, Papers/KO, Papers/PDF).") \
            .classes("text-xs text-gray-500")
        lib_rows()
        ui.switch("변환·번역이 끝나면 자동으로 저장", value=config.resolve_library_auto(),
                  on_change=lambda e: config.set_section_value("library", "auto", e.value)) \
            .props("dense").tooltip("끄면 아래 버튼이나 논문 화면에서 직접 내보낼 때만 저장합니다")
        with ui.row().classes("items-center gap-2"):
            export_btn = ui.button("이미 변환한 논문도 지금 내보내기", icon="drive_file_move",
                                   on_click=export_all).props("dense outline no-caps")
        ui.label("그림은 images/<논문 이름>/ 아래에 들어갑니다. 같은 논문을 다시 내보내면 덮어씁니다. "
                 "내보내기 형식(범용·Notion·Obsidian) 설정을 그대로 따릅니다.").classes("text-xs text-gray-400")
        ui.separator().classes("q-my-xs")
        naming_rows()
        with ui.row().classes("items-center gap-2"):
            naming_btn = ui.button("기존 논문·PDF 이름 정리", icon="drive_file_rename_outline",
                                   on_click=apply_naming_now).props("dense outline no-caps") \
                .tooltip("이미 변환한 논문의 폴더·PDF·저장 위치 사본 이름을 지금 규칙으로 맞춥니다")
            enrich_btn = ui.button("서지 정보 보강", icon="travel_explore",
                                   on_click=lambda: enrich_now()).props("dense outline no-caps") \
                .tooltip("연도·학회가 비어 있는 논문을 공개 서지 API(OpenAlex·Crossref)에서 찾아 채웁니다 — "
                         "논문 제목만 전송하고, 제목이 일치할 때만 채택합니다")
        ui.label("논문 폴더·원본 PDF·저장 위치의 md/PDF가 모두 이 규칙의 이름을 씁니다 — "
                 "이름이 같아야 md에서 PDF를 바로 찾을 수 있어요.").classes("text-xs text-gray-400")
        ui.separator().classes("q-my-xs")
        ws_row()
        ui.label("작업 폴더에는 올린 원본 PDF와 작업 파일(.md4)이 들어갑니다 — 위 저장 위치와 별개입니다.") \
            .classes("text-xs text-gray-400")


def build_home(state: dict) -> None:
    """업로드 홈 — PDF/.md를 올리면 변환 후 리뷰로 이동."""
    from nicegui import run, ui

    def ws() -> Path:
        """작업 폴더 — '저장 위치'에서 바꿀 수 있으므로 항상 state에서 읽는다."""
        return state["upload_dir"]

    ui.add_css(_HOME_CSS)
    state.setdefault("queue", [])
    state.setdefault("recent_new", {})

    from md4paper.extract import DEFAULT_BACKEND, available_backends
    from md4paper.workdir import delete_workdir, recent_workdirs, set_hidden

    backend_ready = bool(available_backends())
    search_state = {"q": "", "sort": "recent", "show_hidden": False}
    selected: set[str] = set()  # 다중 선택된 워크디렉토리 root (문자열)
    _PHASE_ICON = {"pending": "schedule", "extracting": "sync", "cite": "sync",
                   "glossary": "sync", "meta": "sync", "done": "check_circle", "failed": "error"}
    _AI_PHASES = ("cite", "glossary", "meta")

    # ===== 오른쪽 리스트 헬퍼 =====
    def open_wd(root: Path) -> None:
        if WorkDir(root).sections_yaml.exists():
            ui.navigate.to(_review_url(root))
        else:
            ui.notify("작업 디렉토리가 손상되었습니다.", type="negative")

    async def download_zip(root: Path, which: str) -> None:
        ctrl = UIController(WorkDir(root))
        md = ctrl.en_markdown() if which == "en" else ctrl.ko_markdown()
        if not md:
            ui.notify("해당 마크다운이 없습니다.", type="warning")
            return
        name, data = ctrl.export_zip(which, config.resolve_export_target())
        await desktop.deliver(name, data)

    def hide_paper(item: dict, hidden: bool = True) -> None:
        """목록 표시만 끄기/되돌리기 (파일은 그대로 — status.json에 표시)."""
        if not set_hidden(item["root"], hidden):
            ui.notify("처리 실패 (경로 확인).", type="negative")
            return
        selected.discard(str(item["root"]))  # 숨기면 일괄 다운로드 선택에서도 빠진다
        sel_bar.refresh()
        recent_list.refresh()
        ui.notify("목록에서 숨겼습니다 — 파일은 그대로입니다." if hidden else "목록에 다시 표시합니다.",
                  type="positive")

    def confirm_delete(item: dict) -> None:
        """목록에서 없애는 두 가지 방법을 고르게 한다 — 표시만 숨기기(안전) vs 파일 영구 삭제."""
        with ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:460px"):
            ui.label("이 논문을 목록에서 없앨까요?").classes("font-bold")
            ui.label(item["title"]).classes("text-sm text-gray-500")
            ui.label("· 목록에서 숨기기 — 파일은 그대로 두고 목록에서만 감춥니다. "
                     "나중에 '숨긴 논문 보기'에서 되돌릴 수 있습니다.").classes("text-xs text-gray-500")
            ui.label("· 파일 삭제 — 작업 디렉토리(원본 PDF·마크다운·이미지)까지 지웁니다. 되돌릴 수 없습니다.") \
                .classes("text-xs text-gray-500")
            # 저장 위치 사본은 작업 폴더 밖(사용자 볼트)이라, 안 지우면 고아로 남는다 → 기본 켬.
            # 볼트를 건드리는 게 싫으면 끄고 지울 수 있게 체크박스로 노출한다.
            lib_dirs = [str(d) for d in (library.dir_for(k) for k in library.KINDS) if d]
            drop_lib = ui.checkbox("저장 위치의 사본도 함께 삭제", value=True) \
                .props("dense").classes("text-sm") if lib_dirs else None
            for d in dict.fromkeys(lib_dirs):  # 어느 폴더가 지워지는지 경로를 그대로 (중복 제거)
                ui.label(d).classes("text-xs text-gray-400 ml-8 break-all leading-tight")

            def do_delete() -> None:
                ok = delete_workdir(item["root"], ws(),
                                    with_library=bool(drop_lib and drop_lib.value))
                dlg.close()
                if ok:
                    selected.discard(str(item["root"]))
                    sel_bar.refresh()
                    recent_list.refresh()
                    ui.notify("삭제됨", type="positive")
                else:
                    ui.notify("삭제 실패 (경로 확인).", type="negative")

            def do_hide() -> None:
                dlg.close()
                hide_paper(item, True)

            with ui.row().classes("justify-end w-full items-center gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("파일 삭제", icon="delete_forever", on_click=do_delete) \
                    .props("flat no-caps color=negative")
                ui.button("목록에서 숨기기", icon="visibility_off", on_click=do_hide) \
                    .props("unelevated no-caps color=primary")
        dlg.open()

    def _bib_line(r: dict) -> str:
        """저자 · 연도 · venue (있는 것만)."""
        from md4paper import paper_meta

        parts = []
        a = paper_meta.authors_short(r.get("authors") or [])
        if a:
            parts.append(a)
        if r.get("year"):
            parts.append(str(r["year"]))
        if r.get("venue"):
            parts.append(r["venue"])
        return " · ".join(parts)

    def _haystack(r: dict) -> str:
        return " ".join([
            r["title"], " ".join(r.get("authors") or []), r.get("venue") or "", str(r.get("year") or ""),
        ]).lower()

    async def download_bulk(which: str) -> None:
        from md4paper.ui.controller import bulk_zip

        roots = [Path(p) for p in selected]
        if not roots:
            ui.notify("선택된 논문이 없습니다.", type="warning")
            return
        name, data = bulk_zip(roots, which, config.resolve_export_target())
        await desktop.deliver(name, data)

    async def save_bulk_to_library() -> None:
        """선택한 논문들을 전역 저장 위치(영어·한국어 폴더)로 내보내기."""
        if not library.configured():
            ui.notify("저장 위치가 지정돼 있지 않습니다 — 왼쪽 '저장 위치'에서 폴더를 고르세요.", type="warning")
            return
        ok, failed = await run.io_bound(library.export_many, [Path(p) for p in selected])
        ui.notify(f"{ok}편 저장됨" + (f" · {failed}편 실패" if failed else ""),
                  type="positive" if ok else "warning")

    @ui.refreshable
    def sel_bar() -> None:
        if not selected:
            return
        with ui.row().classes("items-center gap-2 w-full no-wrap q-py-xs"):
            ui.label(f"{len(selected)}개 선택").classes("text-sm font-semibold text-primary")
            ui.button("영어 zip", icon="download", on_click=lambda: download_bulk("en")).props("dense outline")
            ui.button("한국어 zip", icon="download", on_click=lambda: download_bulk("ko")).props("dense outline")
            if library.configured():
                ui.button("폴더로 저장", icon="drive_file_move", on_click=save_bulk_to_library) \
                    .props("dense outline").tooltip("저장 위치로 내보내기")
            ui.space()
            ui.button("선택 해제", on_click=lambda: (selected.clear(), sel_bar.refresh(), recent_list.refresh())) \
                .props("flat dense")

    def toggle_sel(root: str, on: bool) -> None:
        selected.add(root) if on else selected.discard(root)
        sel_bar.refresh()

    def toggle_hidden_view() -> None:
        search_state["show_hidden"] = not search_state["show_hidden"]
        recent_list.refresh()

    @ui.refreshable
    def recent_list() -> None:
        # 사용자가 숨긴 논문은 기본적으로 빼고, '숨긴 논문 보기'를 켰을 때만 함께 보여준다.
        items = list(recent_workdirs(ws(), include_hidden=True))
        n_hidden = sum(1 for r in items if r["hidden"])
        show_hidden = search_state["show_hidden"]
        if not show_hidden:
            items = [r for r in items if not r["hidden"]]
        # 아직 큐에서 처리 중인 논문(추출 후 참고문헌·용어집·서지 정리 진행 중)은 숨긴다.
        # 중간에 열면 citation 등 AI 설정이 덜 된 상태라 혼란 → 전체 처리 완료 후에만 목록에 노출.
        active_roots = {
            q2.get("wd_root") for q2 in state["queue"]
            if q2["status"] in _QUEUE_ACTIVE and q2.get("wd_root")
        }
        if active_roots:
            items = [r for r in items if str(r["root"]) not in active_roots]
        q = (search_state["q"] or "").strip().lower()
        if q:
            items = [r for r in items if q in _haystack(r)]  # 제목·저자·venue·연도 검색
        srt = search_state["sort"]
        if srt == "name":
            items.sort(key=lambda r: r["title"].lower())
        elif srt == "year":
            items.sort(key=lambda r: (r.get("year") is None, -(r.get("year") or 0)))
        elif srt == "translated":
            items.sort(key=lambda r: (not r["has_ko"], -r["mtime"]))
        else:
            items.sort(key=lambda r: -r["mtime"])
        if n_hidden:  # 되돌릴 길이 항상 보이도록 (숨김이 '영영 사라짐'이 되지 않게)
            ui.button(f"숨긴 논문 {n_hidden}편 " + ("접기" if show_hidden else "보기"),
                      icon="visibility" if show_hidden else "visibility_off",
                      on_click=toggle_hidden_view).props("flat dense no-caps size=sm color=grey")
        if not items:
            ui.label("검색 결과가 없습니다." if q else "아직 변환한 논문이 없습니다. 왼쪽에서 PDF를 올리세요.") \
                .classes("text-xs text-gray-400")
            if not q and not n_hidden:
                # 다른 폴더에서 변환해 둔 논문이 안 보이는 것일 수 있다 — 실행 방식에 따라 작업 폴더
                # 기본값이 달랐다. 어느 폴더를 보고 있는지와 바꾸는 길을 같이 알려 준다.
                ui.label(f"보고 있는 작업 폴더: {ws()}").classes("text-xs text-gray-400 break-all")
                ui.label("다른 폴더에서 변환했다면 왼쪽 '저장 위치 → 작업 폴더'에서 그 폴더를 고르세요.") \
                    .classes("text-xs text-gray-400")
            return
        now = time.monotonic()
        new_roots = state.get("recent_new", {})
        for r in items:
            is_new = (now - new_roots.get(str(r["root"]), 0)) < 10  # 방금 완료 → 하이라이트
            unopened = not r.get("opened")  # 한 번도 리뷰를 안 연 논문
            bib = _bib_line(r)
            with ui.card().classes("w-full q-pa-sm" + (" md4-recent-new" if is_new else "")):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.checkbox(value=str(r["root"]) in selected,
                                on_change=lambda e, root=str(r["root"]): toggle_sel(root, e.value)) \
                        .props("dense").classes("shrink-0")
                    title_area = ui.column().classes(
                        "gap-0 flex-grow min-w-0 cursor-pointer") \
                        .on("click", lambda _, root=r["root"]: open_wd(root))
                    with title_area:
                        # .nicegui-column은 align-items:flex-start — 자식이 컬럼 폭으로 늘어나지 않아
                        # w-full(컬럼 자식)·min-w-0(행 안 라벨) 없이는 truncate가 발동하지 않고
                        # 긴 제목이 카드·화면 밖으로 흘러넘친다.
                        with ui.row().classes("items-center gap-1.5 no-wrap w-full min-w-0"):
                            if unopened:
                                ui.element("span").classes("md4-new-dot").tooltip("아직 열지 않음")
                            ui.label(r["title"]).classes(
                                "text-sm truncate min-w-0" + (" font-semibold" if unopened else "")).tooltip(r["title"])
                        if bib:
                            ui.label(bib).classes("text-xs text-gray-500 truncate w-full")
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            if r["has_ko"]:
                                ui.badge("번역 완료", color="green").props("outline")
                            else:
                                ui.badge("영어 변환", color="blue").props("outline")
                            if unopened:
                                ui.badge("NEW", color="primary")
                            if r["hidden"]:
                                ui.badge("숨김", color="grey").props("outline")
                            ui.label(_relative_time(r["mtime"])).classes("text-xs text-gray-400")
                    # 번역된 문서는 원문(EN)·번역(KO)을 각각 버튼으로. 미번역은 영어 하나만.
                    ui.button("EN", icon="download", on_click=lambda _, root=r["root"]: download_zip(root, "en")) \
                        .props("flat dense no-caps size=sm color=grey").classes("shrink-0") \
                        .tooltip("영어 마크다운 zip (마크다운+이미지)")
                    if r["has_ko"]:
                        ui.button("KO", icon="download", on_click=lambda _, root=r["root"]: download_zip(root, "ko")) \
                            .props("flat dense no-caps size=sm color=primary").classes("shrink-0") \
                            .tooltip("한국어 번역 마크다운 zip (마크다운+이미지)")
                    if r["hidden"]:  # 숨긴 논문은 삭제 대신 '다시 표시'
                        ui.button(icon="visibility", on_click=lambda _, item=r: hide_paper(item, False)) \
                            .props("flat dense round size=sm color=primary").classes("shrink-0") \
                            .tooltip("목록에 다시 표시")
                    else:
                        ui.button(icon="delete", on_click=lambda _, item=r: confirm_delete(item)) \
                            .props("flat dense round size=sm color=grey").classes("shrink-0") \
                            .tooltip("목록에서 숨기기 / 파일 삭제")

    # ===== 왼쪽 업로드/큐 =====
    async def handle(e) -> None:  # noqa: ANN001 — NiceGUI upload 이벤트 (파일마다 발화)
        fname = e.file.name
        data = await e.file.read()
        if any(q["name"] == fname and q["status"] in _QUEUE_ACTIVE for q in state["queue"]):
            ui.notify(f"{fname} 은(는) 이미 대기열에 있습니다.", type="info")
            return
        pages = _pdf_page_count(data) if fname.lower().endswith(".pdf") else 0
        src = await run.io_bound(save_source, data, fname, ws())
        state["queue"].append({
            "name": fname, "src_path": str(src), "pages": pages,
            "backend": DEFAULT_BACKEND, "ocr": ocr.value,
            "status": "pending", "error": "", "garbled": 0, "since": 0.0, "done_at": 0.0,
        })
        queue_panel.refresh()
        if not state.get("worker_running"):  # 워커가 놀고 있으면 깨운다 (하나만 돎)
            from nicegui import background_tasks
            background_tasks.create(_process_queue(state))

    @ui.refreshable
    def queue_panel() -> None:
        q = state["queue"]
        if not q:
            return
        ui.label("변환 대기열").classes("text-sm font-semibold self-start")
        pend = 0
        for it in q:
            st = it["status"]
            el = int(time.monotonic() - it["since"]) if it["since"] else 0
            if st == "pending":
                pend += 1
                sub = f"대기 {pend}번째"
            elif st == "extracting":
                sub = f"① 추출 중… {el}초" + (f" · {it['pages']}페이지" if it["pages"] else "")
            elif st == "cite":
                sub = f"② 참고문헌 정리 중… (AI) {el}초"
            elif st == "glossary":
                sub = f"③ 용어집 만드는 중… (AI) {el}초"
            elif st == "meta":
                sub = f"④ 서지 정보 추출 중… (AI) {el}초"
            elif st == "done":
                sub = "완료 → 오른쪽 '변환한 논문'에서 열기"
            else:
                sub = f"실패: {it['error']}"[:80]
            color = "text-green-600" if st == "done" else ("text-red-500" if st == "failed" else "text-primary")
            with ui.card().classes("w-full q-pa-sm"):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.icon(_PHASE_ICON[st], size="20px").classes(
                        color + (" md4-spin" if st in _AI_PHASES or st == "extracting" else ""))
                    with ui.column().classes("gap-0 flex-grow min-w-0"):
                        ui.label(it["name"]).classes("text-sm truncate w-full")  # w-full 없으면 긴 파일명이 카드 밖으로
                        ui.label(sub).classes("text-xs " + ("text-red-500" if st == "failed" else "text-gray-400"))
                    if it.get("garbled"):
                        ui.icon("warning", size="18px").classes("text-orange-500 shrink-0") \
                            .tooltip(f"깨진 문자 {it['garbled']}개 — 다른 변환 방식을 시도해 보세요")
                    if st in ("pending", "failed"):
                        ui.button(icon="close", on_click=lambda _, it=it: (
                            state["queue"].remove(it) if it in state["queue"] else None, queue_panel.refresh())) \
                            .props("flat dense round size=sm color=grey").classes("shrink-0").tooltip("취소" if st == "pending" else "닫기")

    @ui.refreshable
    def dropzone_caption() -> None:  # 작업 폴더는 '저장 위치'에서 바뀔 수 있으므로 갱신 가능하게
        ui.label(f"논문별 세부 설정은 변환 후 화면에서. 작업 폴더: {ws()}").classes("text-xs text-gray-400")

    def poll() -> None:  # 큐 진행 갱신 + 전체 처리 완료(또는 실패) 시에만 목록에 등장 + 하이라이트
        q = state["queue"]
        now = time.monotonic()
        refresh_recent = False
        for it in q:
            # 추출→참고문헌→용어집→서지까지 다 끝난(done) 뒤에야 목록에 노출한다.
            # 중간에 열면 citation 설정이 안 되므로 처리 중엔 recent_list에서 제외(위 active_roots).
            if it["status"] in ("done", "failed") and not it.get("_counted"):
                it["_counted"] = True
                if it["status"] == "done" and it.get("wd_root"):
                    state["recent_new"][it["wd_root"]] = now  # 완료 하이라이트
                refresh_recent = True
        if refresh_recent:
            recent_list.refresh()
        q[:] = [it for it in q if not (it["status"] == "done" and now - (it["done_at"] or now) > 6)]
        queue_panel.refresh()

    # 내보내기 형식(범용/Notion/Obsidian) — 전역 설정. 여러 곳(Global 패널·목록 헤더)에 두되
    # 하나를 바꾸면 refresh로 모두 같은 값을 표시하도록 공유 refreshable로 만든다.
    @ui.refreshable
    def export_target_select(labeled: bool = False) -> None:
        def _pick(e) -> None:  # noqa: ANN001
            config.set_section_value("output", "export_target", e.value)
            export_target_select.refresh()
            ui.notify(f"내보내기 형식 · {_EXPORT_TARGET[e.value]}", type="positive", timeout=1200)
        sel = ui.select(_EXPORT_TARGET, value=config.resolve_export_target(), on_change=_pick) \
            .props("dense outlined")
        if labeled:
            sel.props('label="내보내기 형식"').classes("w-full")
        else:
            sel.classes("w-28").tooltip("EN·KO 다운로드 zip에 적용하는 형식")

    # ===== 헤더 + 2단 레이아웃 =====
    with ui.column().classes("items-center gap-0 w-full q-pt-sm"):
        ui.label("md4paper").classes("text-2xl font-bold")
        ui.label("논문 PDF를 헤더 정렬 마크다운 + 한국어 번역으로.").classes("text-xs text-gray-500")

    with ui.splitter(value=42).classes("w-full").style("height: calc(100vh - 72px)") as sp:
        # ---- 왼쪽: 업로드 · 설정 · 진행상황 ----
        with sp.before, ui.column().classes("p-4 gap-3 w-full").style("height:100%; overflow-y:auto"):
            _kstat = config.key_status()
            if not any(_kstat.values()):
                ui.label("⚠ AI 키가 없어 참고문헌 정리·번역 기능이 꺼져 있습니다 — 'AI 설정'에서 키를 넣으세요.") \
                    .classes("text-xs text-orange-600 self-start")
            with ui.expansion("AI 설정 — 참고문헌 정리 · 번역에 사용", icon="smart_toy",
                              value=False).classes("w-full").props("dense"):
                ui.label("PDF→마크다운 '변환'은 AI 없이도 됩니다. 참고문헌 정리·용어집·번역에만 AI를 씁니다.") \
                    .classes("text-xs text-gray-500")

                @ui.refreshable
                def key_status_row() -> None:
                    st = config.key_status()
                    ui.label("키 등록됨: " + " · ".join(
                        f"{'✅' if st[p] else '⬜'} {_short_provider(p)}" for p in config.PROVIDERS)) \
                        .classes("text-xs text-gray-500")

                prov = ui.select(_PROVIDER_NAME, value=config.resolve_provider(),
                                 label="AI 서비스").props("dense outlined").classes("w-full")
                model_sel = ui.select(_model_options(prov.value), value=config.resolve_model(prov.value),
                                      label="모델 (토큰당 가격순 · 저렴할수록 위)").props("dense outlined").classes("w-full")
                key_in = ui.input("API 키 붙여넣기", password=True, password_toggle_button=True) \
                    .props(f'dense outlined placeholder="{_KEY_HINT[config.resolve_provider()]}"').classes("w-full")

                reveal = {"on": False}

                @ui.refreshable
                def key_detail() -> None:
                    info = config.stored_key_preview(prov.value)
                    if not info:
                        ui.label("이 서비스에 저장된 키가 없습니다.").classes("text-xs text-gray-400")
                        return
                    shown = config.resolve_key(prov.value) if reveal["on"] else info["masked"]
                    src = "환경변수 (파일 아님 — 여기서 못 지움)" if info["source"] == "env" else "이 컴퓨터에 저장됨"
                    with ui.row().classes("items-center gap-1 no-wrap w-full"):
                        ui.label(f"현재 키: {shown}").classes("text-xs text-gray-600 break-all")
                        ui.label(f"· {src}").classes("text-xs text-gray-400 shrink-0")
                        ui.button(icon="visibility_off" if reveal["on"] else "visibility",
                                  on_click=lambda: (reveal.__setitem__("on", not reveal["on"]), key_detail.refresh())) \
                            .props("flat dense round size=sm").tooltip("전체 보기 / 가리기")
                        if info["source"] == "file":
                            ui.button(icon="delete", on_click=delete_key) \
                                .props("flat dense round size=sm color=negative").tooltip("저장된 키 삭제")

                def delete_key() -> None:
                    config.delete_key(prov.value)
                    reveal["on"] = False
                    ui.notify(f"{_short_provider(prov.value)} 키 삭제됨", type="warning")
                    key_detail.refresh()
                    key_status_row.refresh()

                def on_prov() -> None:
                    model_sel.set_options(_model_options(prov.value), value=config.resolve_model(prov.value))
                    key_in.props(f'placeholder="{_KEY_HINT[prov.value]}"')
                    reveal["on"] = False
                    key_detail.refresh()
                prov.on_value_change(on_prov)

                def save_ai() -> None:
                    config.set_default("default_provider", prov.value)
                    config.set_section_value("models", prov.value, model_sel.value)
                    k = (key_in.value or "").strip()
                    if k:
                        config.set_key(prov.value, k)
                        key_in.value = ""
                    ui.notify(f"{_short_provider(prov.value)} · {model_sel.value} 저장됨", type="positive")
                    key_detail.refresh()
                    key_status_row.refresh()

                async def test_ai() -> None:
                    from nicegui import run as ng_run

                    k = (key_in.value or "").strip() or config.resolve_key(prov.value)
                    if not k:
                        ui.notify("테스트할 키가 없습니다 — 키를 입력하거나 먼저 저장하세요.", type="warning")
                        return
                    test_btn.props("loading")
                    try:
                        ok, msg = await ng_run.io_bound(
                            config.validate_provider_key, prov.value, model_sel.value, k)
                    finally:
                        test_btn.props(remove="loading")
                    ui.notify(("✅ " if ok else "❌ ") + msg,
                              type="positive" if ok else "negative",
                              timeout=3000 if ok else 6000)

                key_detail()
                with ui.row().classes("gap-2 items-center"):
                    ui.button("저장", icon="save", on_click=save_ai).props("dense color=primary")
                    test_btn = ui.button("연결 테스트", icon="wifi_tethering", on_click=test_ai) \
                        .props("dense outline").tooltip("입력/저장된 키로 아주 작은 호출을 보내 즉시 확인")
                    key_status_row()
                ui.label(f"키는 {config.CONFIG_PATH}에 소유자만 읽는 권한(0600)으로 저장되고, 선택한 AI 서비스로만 전송됩니다. "
                         "환경변수(OPENAI_API_KEY 등)가 있으면 그게 우선합니다.").classes("text-xs text-gray-400")

            # ===== 기본 변환·번역 설정 (Global) — 새 논문 변환 시 적용, 논문별 세부조정은 각 탭에서 =====
            def save_global(section: str, field: str, value, label: str) -> None:  # noqa: ANN001
                config.set_section_value(section, field, value)
                ui.notify(f"기본값 저장됨 · {label}", type="positive", timeout=1200)

            with ui.expansion("기본 변환·번역 설정 — 새로 변환할 논문에 적용", icon="tune",
                              value=False).classes("w-full").props("dense"):
                ui.label("여기 값은 앞으로 변환하는 논문의 기본값입니다. 논문별 세부 조정은 변환·번역 탭에서 언제든 바꿀 수 있어요.") \
                    .classes("text-xs text-gray-500")

                ui.label("변환").classes("text-sm font-semibold text-primary q-mt-sm")
                ocr = ui.checkbox("OCR (스캔 PDF 전용)", value=config.resolve_ocr(),
                                  on_change=lambda e: save_global("extract", "ocr", e.value, "OCR")) \
                    .props("dense").tooltip("스캔 PDF일 때만 — born-digital 논문엔 불필요, 느림")
                # 내보내기 형식(범용/Notion/Obsidian) — 전역 설정. 다운로드 zip에 적용(이미지 임베드·인용 표기).
                export_target_select(labeled=True)
                # 캡션 표기 — 변환 탭과 동일한 '렌더 미리보기 클릭' 피커로 통일 (드롭다운 아님)
                ui.label("그림·표 캡션 표기").classes("text-sm q-mt-sm")

                @ui.refreshable
                def gcapstyle_picker() -> None:
                    cur = config.resolve_caption_style()
                    for key, example in _CAPSTYLE_EX.items():
                        sel = cur == key
                        row = ui.row().classes(
                            "items-center gap-2 w-full no-wrap q-px-sm q-py-xs rounded-borders cursor-pointer"
                        ).style("background: rgba(35,131,226,.12)" if sel else "background: transparent")
                        with row:
                            ui.icon("radio_button_checked" if sel else "radio_button_unchecked", size="18px") \
                                .classes("text-primary" if sel else "text-grey-5")
                            ui.html(example).classes("text-sm").tooltip(_CAPSTYLE_NAME[key])
                        row.on("click", lambda e, k=key: (
                            save_global("output", "caption_style", k, "캡션 표기"), gcapstyle_picker.refresh()))

                gcapstyle_picker()
                ui.select(_RUNIN, value=config.resolve_runin_mode(), label="본문 속 소제목 처리",
                          on_change=lambda e: save_global("structure", "runin_headings", e.value, "소제목 처리")) \
                    .props("dense outlined").classes("w-full")

                ui.label("저자 표기 (이름은 항상)").classes("text-xs text-gray-500 q-mt-xs")
                gapar = {p: (p in config.resolve_author_parts()) for p in ("email", "affiliation")}

                def save_gapar() -> None:
                    chosen = [p for p in ("email", "affiliation") if gapar[p]]
                    save_global("output", "author_parts", chosen, "저자 표기")

                with ui.row().classes("items-center gap-3"):
                    for akey, alabel in (("email", "이메일"), ("affiliation", "소속")):
                        ui.checkbox(alabel, value=gapar[akey],
                                    on_change=lambda e, k=akey: (gapar.__setitem__(k, e.value), save_gapar())) \
                            .props("dense")

                ui.label("번역").classes("text-sm font-semibold text-primary q-mt-sm")
                _ks = config.resolve_korean_style()
                ui.select(_KSTYLE, value=_ks if _ks in _KSTYLE else "해라체", label="한국어 문체",
                          on_change=lambda e: save_global("translate", "korean_style", e.value, "한국어 문체")) \
                    .props("dense outlined").classes("w-full")
                ui.switch("섹션 제목도 한국어로 번역", value=config.resolve_translate_headers(),
                          on_change=lambda e: save_global("translate", "translate_headers", e.value, "헤더 번역"))
                ui.switch("참고문헌 목록도 번역", value=config.resolve_translate_references(),
                          on_change=lambda e: save_global("translate", "translate_references", e.value, "참고문헌 번역"))

                ui.label("본문 인용 표기 (조합 가능)").classes("text-sm q-mt-sm")
                gparts = {p: (p in config.resolve_citation_parts()) for p in ("number", "authoryear", "short")}

                def save_gparts() -> None:
                    chosen = [p for p in ("number", "authoryear", "short") if gparts[p]]
                    save_global("cite", "parts", chosen or ["number"], "인용 표기")

                for pkey, plabel in (("number", "번호  [1]"),
                                     ("authoryear", "저자·연도  (Vaswani et al. 2017)"),
                                     ("short", "약칭  (Transformer)")):
                    ui.checkbox(plabel, value=gparts[pkey],
                                on_change=lambda e, k=pkey: (gparts.__setitem__(k, e.value), save_gparts())) \
                        .props("dense")
                ui.switch("참고문헌에 DOI/arXiv 링크 달기", value=config.resolve_reference_links(),
                          on_change=lambda e: save_global("cite", "reference_links", e.value, "참고문헌 링크"))

            # 저장 위치 — 변환한 논문(영어·한국어 마크다운)이 쌓일 폴더 + 작업 폴더
            build_location_settings(
                state, lambda: (dropzone_caption.refresh(), recent_list.refresh()))

            if not backend_ready:
                ui.label("⚠ 추출 엔진(docling)이 설치되지 않았습니다. `uv sync`로 설치하세요 — "
                         "PDF 변환이 되지 않습니다(.md 입력은 가능).") \
                    .classes("text-xs text-red-600 self-start")

            # 드롭존
            with ui.element("div").classes("md4-dropwrap"):
                # 클릭은 위 CSS가 펼쳐 놓은 진짜 <input type=file>이 그대로 받는다 (JS 개입 없음).
                ui.upload(on_upload=handle, multiple=True, auto_upload=True) \
                    .props('accept=".pdf,.md,.markdown"').classes("md4-upload")
                with ui.column().classes("md4-hint items-center justify-center gap-1 w-full"):
                    ui.icon("cloud_upload", size="42px").classes("text-primary")
                    ui.label("PDF를 끌어다 놓거나 클릭해 선택").classes("text-base")
                    ui.label("여러 개를 올리면 순서대로(배치) 변환합니다").classes("text-xs text-gray-400")

            dropzone_caption()

            queue_panel()

        # ---- 오른쪽: 변환한 논문 (검색·정렬·다중선택·리스트) ----
        with sp.after, ui.column().classes("p-4 gap-2 w-full").style("height:100%; overflow-y:auto"):
            with ui.row().classes("items-center w-full no-wrap"):
                ui.label("변환한 논문").classes("text-sm font-semibold")
                ui.space()
                ui.label("내보내기").classes("text-xs text-gray-500")
                export_target_select(labeled=False)
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                def on_search(e) -> None:  # noqa: ANN001
                    search_state["q"] = e.value or ""
                    recent_list.refresh()
                ui.input(placeholder="제목·저자·venue 검색…").on_value_change(on_search) \
                    .props("dense outlined clearable").classes("flex-grow")

                def on_sort(e) -> None:  # noqa: ANN001
                    search_state["sort"] = e.value
                    recent_list.refresh()
                ui.select({"recent": "최근순", "name": "제목순", "year": "연도순", "translated": "번역됨 먼저"},
                          value="recent", on_change=on_sort).props("dense outlined").classes("w-32")
            sel_bar()  # 다중 선택 시 일괄 다운로드 툴바
            recent_list()

    ui.timer(0.6, poll)


def pick_port(preferred: int = 8080) -> int:
    """preferred가 비어 있으면 그대로, 아니면 OS가 준 빈 포트를 반환.

    남의 컴퓨터에선 8080이 이미 쓰이는 경우가 흔하므로, 충돌 시 크래시 대신 빈 포트로 넘어간다.
    (bind 확인과 실제 서버 bind 사이 미세한 경쟁 구간은 로컬 개발 도구 수준에서 무시.)
    """
    import socket

    if preferred > 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(wd: WorkDir | None = None, upload_dir: Path | None = None,
        port: int = 8080, show: bool = True, native: bool = False) -> None:
    """UI 서버 실행 (블로킹). wd가 None이면 업로드 홈에서 시작.

    port가 이미 쓰이면 빈 포트로 자동 대체하고 실제 주소를 출력한다.
    native=True면 브라우저 대신 앱 창(pywebview)으로 띄운다 — 서버는 그대로 127.0.0.1에만 붙고,
    창을 닫으면 서버도 함께 내려간다.
    """
    from fastapi.responses import FileResponse, Response
    from nicegui import app as fastapi_app
    from nicegui import ui
    from starlette.exceptions import HTTPException

    # launch_wd: CLI로 특정 워크디렉토리를 지정한 경우. current_wd: 이미지 서빙용(리뷰 렌더 시 갱신).
    # queue/worker_running: 전역 변환 대기열 — 페이지 이동에도 유지되도록 서버 수명 state에 둔다.
    state: dict = {
        # pin_workspace: 처음 뜰 때 작업 폴더를 config에 못 박는다 — 나중에 실행 방식(저장소/설치본)이
        # 바뀌어도 같은 폴더를 보도록. --upload-dir로 준 값은 한 번짜리 지정이라 적지 않는다.
        "launch_wd": wd, "current_wd": wd, "upload_dir": upload_dir or config.pin_workspace(),
        "queue": [], "worker_running": False, "recent_new": {},
        "wd_by_token": {},  # 토큰 → WorkDir (이미지·PDF 자산 서빙, 논문별 캐시 격리)
    }

    def _register_wd(w) -> None:  # noqa: ANN001 — WorkDir
        if w is not None:
            state["wd_by_token"][wd_token(w.root)] = w

    def _wd_for(token: str):  # noqa: ANN202 — 토큰으로 워크디렉토리 해석 (없으면 current 폴백)
        return state["wd_by_token"].get(token) or state["current_wd"]

    _register_wd(wd)

    @fastapi_app.get("/wdimages/{token}/{name:path}")
    def _serve_img(token: str, name: str):  # noqa: ANN202 — 해당 논문의 out/images 서빙
        cur = _wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        base = cur.out_images.resolve()
        target = (base / name).resolve()
        # is_relative_to로 경로탈출 차단 — 문자열 prefix 비교는 형제 폴더(images2/)를 통과시킨다
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    @fastapi_app.get("/pdfpage/{token}/{idx:int}")
    def _serve_pdf_page(token: str, idx: int):  # noqa: ANN202 — 해당 논문 PDF의 idx(0-based) 페이지 PNG
        cur = _wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        png = UIController(cur).pdf_page_png(idx, zoom=2.0)
        if png is None:
            raise HTTPException(status_code=404)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

    @ui.page("/")
    def index() -> None:
        # CLI로 워크디렉토리를 지정해 띄웠으면 그 리뷰로, 아니면 업로드 홈
        if state["launch_wd"] is not None:
            ui.navigate.to(_review_url(state["launch_wd"].root))
            return
        build_home(state)

    @ui.page("/home")
    def home() -> None:
        build_home(state)

    @ui.page("/review")
    def review_page(wd: str = "") -> None:  # noqa: ANN001 — 쿼리 파라미터
        root = Path(wd) if wd else None
        if root is None or not WorkDir(root).sections_yaml.exists():
            ui.navigate.to("/home")
            return
        wdir = WorkDir(root)
        state["current_wd"] = wdir  # 이미지 서빙이 이 워크디렉토리를 가리키도록
        _register_wd(wdir)  # 토큰 등록 (이 논문 자산 URL 해석용)
        _mark_opened(wdir)  # 미열람 표시 해제 (한 번이라도 열면 '새 항목' 아님)
        build(UIController(wdir))

    port = pick_port(port)
    if native:
        desktop.configure()  # 창 크기·Dock 아이콘·다운로드 설정 (웹뷰 프로세스로 전달된다)
        print(f"md4paper 앱 창 시작 (내부 주소 http://127.0.0.1:{port})")
    else:
        print(f"md4paper UI: http://127.0.0.1:{port}  (Ctrl+C로 종료)")
    ui.run(host="127.0.0.1", port=port, show=show and not native, reload=False, title="md4paper",
           favicon=str(desktop.ICON) if desktop.ICON.is_file() else None,
           native=native, window_size=(1440, 900) if native else None)

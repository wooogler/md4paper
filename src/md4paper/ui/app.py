"""로컬 웹 UI (NiceGUI) — 섹션 트리 리뷰 + 프리뷰 + PDF 대조 + 설정.

CLI와 같은 아티팩트(sections.yaml 등)를 읽고 쓴다. 항상 떠 있는 서버가 아니라
`md4paper ui WORKDIR`로 필요할 때 띄우는 로컬 앱이다.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import time
from html import escape
from pathlib import Path

from md4paper.i18n import N_, t
from md4paper import config, formula_fix, i18n, langs, library, projects
from md4paper import notes as notes_io
from md4paper.ir import Flavor, GlossaryEntry
from md4paper.ui import (
    agent_panel, annotations, desktop, figrefs, figure_crop, figure_ko, find_bar, formula_pop, jobs,
    live_preview, mdrender, pdf_zoom, reanchor,
    scroll_memory, sentpair, spa, tabstore, term_examples, translate_job, update_banner,
)
from md4paper.ui.controller import LEVEL_OPTIONS, RUNIN_LEVEL_OPTIONS, UIController
from md4paper.workdir import WorkDir, left_panel, paper_title, set_left_panel, set_project

# 설명이 붙은 옵션 (값 → 사람이 이해하기 쉬운 라벨)
# 마크다운 내보내기 형식 — 다운로드 zip에 적용 (설명 없이 이름만)
_EXPORT_TARGET = {"universal": N_("범용"), "notion": "Notion", "obsidian": "Obsidian"}
# 목차 제목 언어 — 영어만 / 번역만 / 둘 다(두 줄). "ko"의 표시는 논문 번역 언어 이름으로 바꿔 쓴다(_toc_lang_options)
_TOC_LANG = {"en": N_("영어"), "ko": N_("한국어"), "both": N_("병기")}


def _toc_lang_options(lang: str) -> dict[str, str]:
    """목차 언어 토글 — 번역 쪽 이름은 논문 번역 언어로 (한국어는 예전 그대로)."""
    return {**_tr(_TOC_LANG), "ko": langs.get(lang).native if lang != "ko" else t(_TOC_LANG["ko"])}


def _tr(d: dict) -> dict:
    """N_()로 표시한 라벨 사전 → 화면 언어로 (값만)."""
    return {k: t(v) for k, v in d.items()}
# AI 서비스 — 비전문가용 이름 + 키 형식 힌트
_PROVIDER_NAME = {
    "openai": N_("OpenAI (ChatGPT 계열)"),
    "anthropic": N_("Anthropic (Claude 계열)"),
    "gemini": N_("Google (Gemini 계열)"),
}
_KEY_HINT = {"openai": N_("sk-proj-... 또는 sk-..."), "anthropic": "sk-ant-...", "gemini": "AIza..."}
_TIER_LABEL = (N_("저렴 · 빠름"), N_("중간"), N_("비쌈 · 고성능"))


def _short_provider(p: str) -> str:
    return _PROVIDER_NAME.get(p, p).split(" ")[0]


def _model_options(provider: str) -> dict:
    """제공사의 모델을 '가격 tier — 모델명 · $입력/$출력' 라벨로 (저렴→비쌈)."""
    from md4paper.llm.base import PRICING

    opts: dict[str, str] = {}
    for i, mid in enumerate(config.MODEL_TIERS.get(provider, ())):
        pr = PRICING.get(mid)
        price = t(" · ${inp:g}/${out:g} (100만 토큰)", inp=pr[0], out=pr[1]) if pr else ""
        opts[mid] = f"{t(_TIER_LABEL[i])} — {mid}{price}"
    return opts


_CAPSTYLE_NAME = {"bold-italic": N_("굵은 라벨 + 이탤릭"), "blockquote": N_("인용구"), "italic": N_("이탤릭만")}
# 마크다운 문법 대신 '렌더 결과'를 그대로 보여주는 미리보기 (직관적 선택)
_CAPSTYLE_EX = {
    "bold-italic": "<b>Figure 1:</b> <i>⦃예시 캡션⦄</i>",
    "blockquote": '<span style="border-left:3px solid currentColor;padding-left:8px;opacity:.9">'
                  "<b>Figure 1:</b> ⦃예시 캡션⦄</span>",
    "italic": "<i>Figure 1: ⦃예시 캡션⦄</i>",
}
_RUNIN = {
    "off": N_("끄기 — 본문 그대로"),
    "header": N_("헤더로 승격 — ### 4.1.3 제목"),
    "italic": N_("이탤릭 표기 — *4.1.3 제목.*"),
}
_POLICY = {
    "translate": N_("번역 (의미로)"),
    "transliterate": N_("음역 (소리로)"),
    "keep": N_("원어 유지"),
    "병기-first-use": N_("첫 등장 시 원어 병기"),
}

# 헤더 레벨 드롭다운 — 내부 값(int/문자)은 그대로 두고 표시만 결과 중심의 한국어로.
# (기존엔 skip/merge-up/drop 같은 영어 내부값이 그대로 노출돼 뜻이 안 와닿았다.)
_LEVEL_LABEL = {
    1: N_("제목 1"), 2: N_("제목 2"), 3: N_("제목 3"), 4: N_("제목 4"), 5: N_("제목 5"), 6: N_("제목 6"),
    "italic": N_("기울임"),       # run-in 소제목을 기울임 문단으로
    "skip": N_("본문으로"),       # 헤더 해제 — 제목 텍스트는 일반 문단으로 남김
    "merge-up": N_("위에 합침"),  # 제목 줄을 없애고 그 내용을 위 섹션에 합침
    "drop": N_("통째 삭제"),      # 제목 + 본문을 통째로 제거
}


def _level_opts(runin: bool) -> dict:
    """레벨 선택 옵션 {값: 한국어 라벨}. 값은 controller의 정본 목록을 그대로 쓴다."""
    src = RUNIN_LEVEL_OPTIONS if runin else LEVEL_OPTIONS
    return {v: t(_LEVEL_LABEL[v]) if v in _LEVEL_LABEL else str(v) for v in src}

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
#md-cite-tip .md-cite-copy.copied::after { content: '⦃제목 복사됨⦄'; position: absolute; right: 100%;
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
/* 보기 전환 세그먼트(편집 대상 원문/번역 등) — 둥근 테두리 + 넉넉한 패딩 (촘촘하지 않게) */
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
/* 목차 — 왼쪽 작업 패널의 한 모드(패널이 스크롤 상자다). 항목은 두 줄까지: 보는 언어 제목(진하게) +
   반대쪽 언어 제목(옅게). 헤더를 번역하지 않아 같으면 한 줄. */
.vtoc { width: 100%; padding: 0 0 24px; }
.vtoc-item { display: block; padding: 3px 8px; border-radius: 6px; cursor: pointer; line-height: 1.4; min-width: 0; }
.vtoc-item:hover { background: #ececec; }
.vtoc-l1 { font-size: 13px; color: #5b5851; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vtoc-l2 { font-size: 11.5px; color: #9a968e; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vtoc-item:hover .vtoc-l1 { color: #37352f; }
.vmain { flex: 1 1 auto; min-width: 0; height: 100%; }
.vwrap { position: relative; height: 100%; min-width: 0; }        /* 정렬 그리드 + 컬럼 드래그 핸들 */
/* 원문 | PDF 나란히 — splitter 패널은 flex-column이라 그 안 상자가 내용 폭으로 줄어든다. 그러면 본문이
   패널 가운데가 아니라 왼쪽에 붙고(폭 핸들의 기준 가운데도 같이 틀어진다), PDF 칸은 쪽 그림 폭이 0으로 잡혀
   사라지기도 한다 → 패널 폭을 다 채우게. */
.vmain .q-splitter__panel > .vwrap, .vmain .q-splitter__panel > .vpdf { width: 100%; }
.vcol-handle { position: absolute; top: 0; bottom: 0; width: 10px; margin-left: -5px; cursor: col-resize; z-index: 3; }
.vcol-handle::after { content: ''; position: absolute; left: 5px; top: 0; bottom: 0; width: 1px; background: #e6e4e0; }
.vcol-handle:hover::after { background: #2383e2; width: 2px; }
/* 한 패널 본문의 좌우 가장자리 — 창 가장자리를 끌듯 끌어서 본문 폭을 바꾼다(_BODY_DRAG_JS가 위치를 맞춘다).
   평소엔 안 보이고, 본문에 마우스를 올리면 옅은 선, 핸들 위·드래그 중엔 파랗게. */
.vbody-handle { position: absolute; top: 0; bottom: 0; width: 10px; margin-left: -5px; cursor: col-resize; z-index: 3; }
.vbody-handle::after { content: ''; position: absolute; left: 5px; top: 0; bottom: 0; width: 1px;
  background: #e6e4e0; opacity: 0; transition: opacity .15s ease; }
.vbodywrap:hover .vbody-handle::after { opacity: 1; }
.vbody-handle:hover::after, .vbody-handle.dragging::after { background: #2383e2; width: 2px; opacity: 1; }
.vpdf { height: 100%; overflow-y: auto; overscroll-behavior: contain; padding: 6px 10px; background: #f4f3f1; }
.vpane { height: 100%; overflow-y: auto; overscroll-behavior: contain; }  /* 정렬 불가 시 단일 마크다운 패널 */
/* 한 패널만 보일 때(원문만·번역만·PDF만) 본문이 창 폭을 다 먹으면 한 줄이 너무 길어 읽기 어렵다 →
   최대 폭(--md4-maxw, 캔버스 열에 인라인)으로 묶고 가운데 둔다. 두 컬럼(원문|번역)이나 PDF 나란히면
   각 컬럼이 이미 좁으므로 그대로. 폭은 본문 좌우 가장자리 핸들(.vbody-handle)을 끌어 조절·기억한다. */
.sbs-grid:not(.sbs-two) > .sbs-cell { width: 100%; max-width: var(--md4-maxw, 820px); justify-self: center; }
.vpane-in { max-width: var(--md4-maxw, 820px); margin: 0 auto; }
.vpdf img { max-width: calc(var(--md4-maxw, 900px) * var(--pdf-zoom, 1)) !important; }  /* --pdf-zoom: §ui/pdf_zoom */
/* 캔버스 편집 모드의 프리뷰 스크롤 패널 (프리뷰↔PDF 싱크 컨테이너).
   width:100%가 필요하다 — splitter 패널이 flex-column이라 이 상자가 내용의 max-content(844px)로
   잡혀 611px 칸을 넘고 오른쪽이 잘렸다('마크다운 + PDF'에서도 매 줄 끝이 잘리던 기존 버그). */
.conv-md { height: 100%; width: 100%; min-width: 0; }
/* 분할 편집 — 왼쪽 원문 편집기 | 오른쪽 프리뷰. 두 열은 '언어'가 아니라 '표현'이 다른 것이라
   에디터 쪽을 살짝 눌러 두 열이 한눈에 구분되게 한다(읽기 모드의 원문|번역과 헷갈리지 않도록). */
.conv-ed { display: flex; flex-direction: column; width: 100%; min-width: 0; }
.conv-ed .cm-en-editor { flex: 1 1 auto; min-height: 0; }
.conv-ed .cm-editor { height: 100%; background: #fbfaf9; }
.conv-ed .cm-scroller { overscroll-behavior: contain; }
/* CommonMark는 항목 사이에 빈 줄이 있는 목록을 loose로 보고 <li><p>로 감싼다(markdown2는 안 그랬다).
   의미는 맞지만 문단 여백이 그대로 들어와 목록이 성기게 벌어지므로 여백만 지운다. */
.md-preview li > p { margin: 0.15em 0; }
.md-preview .md-math-raw { color: #b45309; }  /* MathML 변환 실패한 수식 — 원문을 그대로 */
@media (prefers-color-scheme: dark) {
  .conv-ed .cm-editor { background: #1d1d1d; }
}
/* 정렬 그리드 — 한 행의 두 셀이 같은 grid row라 높이가 큰 쪽에 맞춰져 스크롤이 안 어긋난다.
   컬럼 수/비율은 인라인 --sbs-cols로 지정(원문/번역 토글·드래그). */
.sbs-grid { display: grid; grid-template-columns: var(--sbs-cols, 1fr 1fr); align-content: start; padding-bottom: 48px;
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
  align-items: center; justify-content: center; overflow: hidden; background: rgba(18,18,20,.93);
  padding: 52px 0 var(--mdz-foot, 52px); box-sizing: border-box; }
#md-img-zoom.open { display: flex; }
/* 위 52px은 도구막대, 아래는 캡션 자리(--mdz-foot, 캡션 길이만큼) — 그림이 그 위로 올라타지 않게 비워 둔다 */
#md-img-zoom img { max-width: calc(100vw - 64px); max-height: calc(100vh - 52px - var(--mdz-foot, 52px));
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
#md-img-zoom .mdz-sep { width: 1px; height: 16px; margin: 0 4px; background: rgba(255,255,255,.22); }
#md-img-zoom .mdz-pct { min-width: 48px; text-align: center; color: #cfcfcf; font-size: 12px;
  font-variant-numeric: tabular-nums; }
/* 캡션(alt) + 조작 힌트 — 그림 아래 가운데 */
#md-img-zoom .mdz-foot { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
  width: max-content; max-width: min(78vw, 920px); text-align: center; color: #c9c9c9; font-size: 12.5px; line-height: 1.5;
  text-shadow: 0 1px 3px rgba(0,0,0,.8); pointer-events: none; }
/* 그림·표 캡션 전문 — 길면 왼쪽 정렬로 몇 줄, 그 이상은 캡션 안에서 스크롤. 글자는 골라 복사할 수 있다 */
#md-img-zoom .mdz-cap { max-height: 30vh; overflow-y: auto; pointer-events: auto; user-select: text;
  color: #e2e2e2; font-size: 16px; line-height: 1.6;   /* 본문 캡션(.md-preview 16px)과 같은 크기 */
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif; }
#md-img-zoom .mdz-cap.long { text-align: left; }
#md-img-zoom .mdz-cap b { color: #fff; }
#md-img-zoom .mdz-hint { color: #8e8e8e; font-size: 11.5px; }
/* 그림 설명(태그 PDF의 대체 텍스트) — 접힌 채로 캡션 아래. 펼치면 왼쪽 정렬 문단, 길면 안에서 스크롤 */
#md-img-zoom .mdz-alt { display: none; margin: 6px auto 4px; pointer-events: auto; text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif; }
#md-img-zoom.has-alt .mdz-alt { display: block; }
#md-img-zoom .mdz-alt summary { cursor: pointer; color: #b8c7ff; font-size: 13px; text-align: center;
  list-style-position: inside; user-select: none; }
#md-img-zoom .mdz-alt summary span { color: #8e8e8e; font-size: 11.5px; }
#md-img-zoom .mdz-alt-text { max-height: 24vh; overflow-y: auto; margin-top: 4px; padding: 8px 12px;
  border-radius: 8px; background: rgba(255,255,255,.07); color: #dcdcdc; font-size: 14px; line-height: 1.6;
  user-select: text; white-space: pre-line; }
@media (prefers-color-scheme: dark) {
  .md-preview { color: #d4d4d4; }
  .md-preview h6 { color: #9a9a9a; }  /* 다크에서 h6 옅은 색 대비 보정 */
  .md-preview code { background: #2f2f2f; color: #ff7b72; }
  .md-preview pre, .md-preview th { background: #262626; }
  .md-preview th, .md-preview td { border-color: #3a3a3a; }
  .md-preview blockquote { border-left-color: #d4d4d4; color: #a8a8a8; }
  #md-cite-tip { background: #e8e8ea; color: #1c1c1e; box-shadow: 0 6px 24px rgba(0,0,0,.5); }
  .sbs-cell-div { border-color: #3a3a3a; }
  .vtoc-l1 { color: #b8b8b8; }
  .vtoc-item:hover { background: #2c2c2c; }
  .vtoc-item:hover .vtoc-l1 { color: #e0e0e0; }
  .vcol-handle::after { background: #3a3a3a; }
  .vbody-handle::after { background: #3a3a3a; }
  .vpdf { background: #151515; }
  .sbs-row-sep { border-top-color: #2c2c2c; }
}
"""

# ── 문장 짝 호버 + 하이라이트/메모 ────────────────────────────────────────────
# 나란히 놓아도 "이 문장이 저 문장"이라는 대응이 눈에 안 들어온다. 마우스를 올린 문장과 그
# 짝이 되는 반대쪽 문장을 함께 칠한다. 문단(행)은 왼쪽 얇은 선으로만 — 문장이 주인공이다.
_ANNO_CSS = """
.sbs-grid.sbs-two .sbs-cell { transition: box-shadow .12s ease; }
.sbs-grid.sbs-two .sbs-cell.sbs-hl { box-shadow: inset 2px 0 0 rgba(35,131,226,.30); }
.sent { transition: background-color .1s ease; }
.sent.sent-hl { background: rgba(35,131,226,.17); border-radius: 3px;
  box-decoration-break: clone; -webkit-box-decoration-break: clone; }
/* 짝을 못 찾은 문장 — 파랑(=짝 있음)과 구별되게 무채색으로 */
.sent[data-solo].sent-hl { background: rgba(130,130,130,.20); }
/* 한 언어만 볼 때 — 반대쪽 문단은 숨은 셀로 같이 그려 두고, 올린 문장의 짝을 툴팁으로 보여 준다 */
.sbs-ghost { display: none !important; }
#md-sent-tip { position: fixed; z-index: 9990; max-width: 520px; pointer-events: none; opacity: 0;
  background: #fffdf7; color: #2b2b2b; border: 1px solid #e6e1d3; border-radius: 9px;
  box-shadow: 0 6px 22px rgba(0,0,0,.14); padding: 8px 12px; font-size: 13.5px; line-height: 1.6;
  word-break: keep-all; overflow-wrap: break-word; transition: opacity .1s ease; }
#md-sent-tip.open { opacity: 1; }
#md-sent-tip .st-side { display: block; font-size: 10.5px; color: #9a9384; margin-bottom: 2px; }

/* 하이라이트 — 배경색은 JS가 인라인으로(팔레트). 글자색은 밝은 형광 위라 항상 어둡게 고정한다
   (다크 모드의 옅은 글자색 그대로면 노랑 위에서 읽히지 않는다). */
mark.md-anno { color: #24211c; border-radius: 2px; padding: 0; cursor: pointer;
  box-decoration-break: clone; -webkit-box-decoration-break: clone; }
mark.md-anno.md-anno-note { box-shadow: inset 0 -2px 0 rgba(0,0,0,.32); }  /* 메모가 붙은 것 */
mark.md-anno.md-anno-flash { animation: mdAnnoFlash 1.3s ease-out; }
@keyframes mdAnnoFlash { 0%,55% { outline: 2px solid #2383e2; outline-offset: 2px; } 100% { outline-color: transparent; } }

/* 드래그 직후 뜨는 색 고르기 막대 */
#md-anno-bar { position: fixed; z-index: 10002; display: none; align-items: center; gap: 3px;
  padding: 5px 7px; border-radius: 11px; background: #2b2b2e; box-shadow: 0 8px 26px rgba(0,0,0,.36); }
#md-anno-bar.open { display: flex; }
.ab-dot { width: 19px; height: 19px; padding: 0; border-radius: 50%; cursor: pointer;
  border: 1.5px solid rgba(255,255,255,.32); transition: transform .1s ease; }
.ab-dot:hover { transform: scale(1.16); }
.ab-dot.on { border-color: #2383e2; box-shadow: 0 0 0 2px rgba(35,131,226,.35); }
.ab-sep { width: 1px; height: 16px; background: rgba(255,255,255,.22); margin: 0 3px; }
#md-anno-bar .ab-btn { border: none; background: none; color: #e8e8e8; font-size: 14px;
  padding: 3px 7px; border-radius: 7px; cursor: pointer; }
#md-anno-bar .ab-btn:hover { background: rgba(255,255,255,.16); }

/* 하이라이트를 누르면 뜨는 메모 카드 */
#md-anno-pop { position: fixed; z-index: 10003; display: none; width: 340px; padding: 12px;
  border-radius: 12px; background: #fff; border: 1px solid #e6e4e0; color: #37352f;
  box-shadow: 0 12px 36px rgba(0,0,0,.18); }
#md-anno-pop.open { display: block; }
.apop-row { display: flex; align-items: center; gap: 4px; margin-bottom: 8px; }
.apop-sp { flex: 1 1 auto; }
.apop-del { border: none; background: none; color: #b3261e; font-size: 14px; padding: 3px 7px;
  border-radius: 7px; cursor: pointer; }
.apop-del:hover { background: rgba(179,38,30,.12); }
/* 인용은 네 줄까지 보이고 그보다 길면 칸 안에서 스크롤 (잘린 채 끝나지 않게) */
.apop-quote { font-size: 13.5px; line-height: 1.55; color: #8a8780; margin-bottom: 6px;
  max-height: calc(1.55em * 4); overflow-y: auto; overscroll-behavior: contain; padding-right: 4px; }
.apop-quote.apop-ko, .ap-q.ap-ko { padding-left: 7px; border-left: 2px solid #e0ded9; }
.apop-row + .apop-quote.apop-ko { border-left-color: #cfcdc8; }
#md-anno-pop textarea { width: 100%; min-height: 66px; resize: vertical; box-sizing: border-box;
  border: 1px solid #e0ded9; border-radius: 8px; padding: 6px 8px; font: inherit; font-size: 14.5px;
  background: #fff; color: inherit; outline: none; }
#md-anno-pop textarea:focus { border-color: #2383e2; }

/* 오른쪽 목록 서랍 — 에이전트 창과 같은 규칙: 본문을 덮지 않고 오른쪽을 차지한다.
   (--ma-top은 헤더를 실제로 재서 넣는다. 헤더 높이가 고정이 아니라 상수로 두면 탭 라벨을 가린다.) */
:root { --ma-w: 360px; --ma-top: 50px; }
#md-anno-panel { position: fixed; top: var(--ma-top); right: 0; bottom: 0; width: var(--ma-w); z-index: 9000;
  display: none; flex-direction: column; background: #fff; border-left: 1px solid #e6e4e0;
  color: #37352f; }
#md-anno-panel.open { display: flex; }
body.ma-open .md4-steps { padding-right: var(--ma-w); }
.ap-head { display: flex; align-items: center; gap: 7px; padding: 10px 12px;
  border-bottom: 1px solid #ecebe8; font-size: 14.5px; }
.ap-count { font-size: 12px; padding: 1px 7px; border-radius: 9px; background: #f0efec; color: #6b6862; }
.ap-x { border: none; background: none; font-size: 14px; color: #8a8780; cursor: pointer;
  padding: 3px 6px; border-radius: 7px; }
.ap-x:hover { background: #f0efec; }
.ap-list { flex: 1 1 auto; overflow-y: auto; padding: 6px; }
.ap-foot { padding: 8px 12px; border-top: 1px solid #ecebe8; font-size: 12.5px; color: #97948d; }
.ap-empty { padding: 22px 14px; font-size: 13.5px; color: #97948d; text-align: center; line-height: 1.6; }
.ap-item { padding: 8px 9px; border-radius: 9px; cursor: pointer; }
.ap-item:hover { background: #f7f6f3; }
.ap-item + .ap-item { margin-top: 2px; }
.ap-top { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.ap-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 10px; }
.ap-side { font-size: 12px; color: #8a8780; }
.ap-lostb { font-size: 11.5px; color: #b3261e; background: rgba(179,38,30,.10);
  padding: 0 5px; border-radius: 6px; }
.ap-del { border: none; background: none; color: #a9a69f; font-size: 13px; cursor: pointer;
  padding: 2px 5px; border-radius: 6px; opacity: 0; }
.ap-item:hover .ap-del { opacity: 1; }
.ap-del:hover { background: rgba(179,38,30,.12); color: #b3261e; }
.ap-q { font-size: 14px; line-height: 1.6; color: #5b5851;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ap-q.ap-ko { margin-top: 3px; color: #86837c; }
.ap-n { font-size: 14px; line-height: 1.6; margin-top: 5px; padding-left: 7px;
  border-left: 2px solid #2383e2; white-space: pre-wrap; }
.ap-lost .ap-q { opacity: .55; }
/* 위치를 못 찾은 곳이 있을 때만 — 다시 찾기 단추와 결과 알림 */
.ap-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 12px;
  border-bottom: 1px solid #ecebe8; font-size: 12.5px; color: #8a8780; }
.ap-refind { border: 1px solid #d9d7d2; background: #fff; color: #37352f; font-size: 12.5px;
  padding: 3px 10px; border-radius: 7px; cursor: pointer; }
.ap-refind:hover { background: #f0efec; }
.ap-tmsg { color: #2383e2; }
.ap-guessb { font-size: 11.5px; color: #8a5a00; background: rgba(214,150,0,.14); border: none;
  padding: 0 5px; border-radius: 6px; cursor: pointer; }
.ap-guessb:hover { background: rgba(214,150,0,.26); }

/* 툴바의 메모 버튼에 개수 배지 (JS가 data-count를 갱신) */
.md4-anno-btn[data-count]:not([data-count="0"])::after { content: attr(data-count);
  position: absolute; top: -2px; right: -2px; min-width: 15px; height: 15px; padding: 0 3px;
  border-radius: 8px; background: #2383e2; color: #fff; font-size: 10px; line-height: 15px;
  font-weight: 600; }

@media (prefers-color-scheme: dark) {
  .sbs-grid.sbs-two .sbs-cell.sbs-hl { box-shadow: inset 2px 0 0 rgba(90,150,230,.45); }
  .sent.sent-hl { background: rgba(90,150,230,.22); }
  .sent[data-solo].sent-hl { background: rgba(150,150,150,.20); }
  #md-sent-tip { background: #262624; color: #e2e0da; border-color: #3a3935; }
  #md-sent-tip .st-side { color: #8d8a82; }
  #md-anno-pop, #md-anno-panel { background: #1f1f1f; border-color: #3a3a3a; color: #d4d4d4; }
  #md-anno-pop textarea { background: #262626; border-color: #3a3a3a; }
  .apop-quote, .ap-side, .ap-foot, .ap-empty { color: #8f8f8f; }
  .ap-head, .ap-foot, .ap-tools { border-color: #2c2c2c; }
  .ap-refind { background: #262626; border-color: #3a3a3a; color: #d4d4d4; }
  .ap-guessb { color: #e0b450; }
  .ap-count { background: #2c2c2c; color: #b8b8b8; }
  .ap-item:hover, .ap-x:hover { background: #2c2c2c; }
  .ap-q { color: #b8b8b8; }
  .ap-q.ap-ko { color: #8f8f8f; }
  .apop-quote.apop-ko, .ap-q.ap-ko { border-left-color: #3a3a3a; }
}
"""

# 하이라이트 · 메모 — 순수 클라이언트 처리 + annotations.json 되저장.
# 앵커는 렌더된 HTML이 아니라 셀(.anno-scope)의 평문 오프셋이라, 다시 렌더돼도 같은 자리에 붙는다.
# 재조립·재번역으로 오프셋이 밀리면 quote(+앞뒤 문맥)로 다시 찾고, 고쳐진 좌표를 서버에 되저장한다.
_ANNO_HTML = """
<div id="md-anno-bar"></div>
<div id="md-anno-pop"></div>
<div id="md-anno-panel">
  <div class="ap-head"><b>⦃하이라이트 · 메모⦄</b><span class="ap-count">0</span>
    <span class="apop-sp"></span><button class="ap-x" title="⦃닫기 (Esc)⦄">&#10005;</button></div>
  <div class="ap-tools" style="display:none"></div>
  <div class="ap-list"></div>
  <div class="ap-foot">⦃드래그 = 끌어 둔 그 자리에 · 더블클릭 = 그 문장 통째로 (원문·번역 함께)⦄</div>
</div>
<script>
(function(){
  if (window.__mdAnnoInit) return; window.__mdAnnoInit = true;
  var ORDER = ['yellow', 'green', 'blue', 'pink', 'purple'];
  var SWATCH = {yellow:'#ffe08a', green:'#b5e7b8', blue:'#a9d8f5', pink:'#f9bcd4', purple:'#d8c6f5'};
  var CTX = 40;                       // 재탐색용 앞뒤 문맥 길이 (서버 상한 60 안쪽)
  var items = Array.isArray(window.__mdAnno) ? window.__mdAnno : [];
  var lost = {};                      // 위치를 못 찾은 항목 id (지우지는 않고 목록에만 남긴다)
  var miss = {};                      // 항목 id → 못 찾은 쪽들 ['ko'] — 한쪽만 붙은 표시도 서랍에 알린다
  var wideNext = false;               // 다음 applyAll은 행 창 없이 문서 전체에서 찾는다 ("다시 찾기")
  var repairedSince = false;          // 위치를 고쳐 저장할 것이 있다 → 서버가 고치기 전 파일을 .bak으로 한 번 남긴다
  var bar = document.getElementById('md-anno-bar');
  var pop = document.getElementById('md-anno-pop');
  var panel = document.getElementById('md-anno-panel');
  if (!bar || !pop || !panel) return;
  var pending = null, editing = null, timer = null;
  var lastColor = ORDER[0];   // 더블클릭은 색을 안 고르므로 마지막에 쓴 색을 이어 쓴다
  var dblGuard = false;       // 더블클릭 직후 브라우저가 고른 단어를 드래그로 오해하지 않게
  var dblTimer = null;        // 세 번째 클릭이 오면 취소할 수 있게 (세 번 클릭 = 문단 선택)
  var scrollLock = false;     // 우리가 일으킨 스크롤 동안은 닫기 리스너를 쉬게 한다

  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function uid(){ return 'a' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function find(id){ for (var i = 0; i < items.length; i++) if (items[i].id === id) return items[i]; return null; }

  // ---- 저장 (paper.md4/annotations.json) ----
  function flush(){
    if (timer) { clearTimeout(timer); timer = null; }
    var tok = window.__mdAnnoTok; if (!tok) return;
    var body = {items: items}; if (repairedSince){ body.repaired = true; repairedSince = false; }
    fetch('/annotations/' + tok, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)}).catch(function(){});
  }
  function save(){ if (timer) clearTimeout(timer);
    timer = setTimeout(function(){ timer = null; flush(); renderPanel(); }, 300); }
  window.__mdAnnoFlush = flush;
  window.addEventListener('beforeunload', function(){ if (timer) flush(); });

  // ---- 셀 평문 기준 오프셋 ----
  function scopes(){ return [].slice.call(document.querySelectorAll('.anno-scope')); }
  function scopeOf(node){
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el && el.closest ? el.closest('.anno-scope') : null;
  }
""" + sentpair.JS + reanchor.JS + """
  function absOffset(scope, container, offset){   // Range.toString()은 textContent와 같은 텍스트만 센다
    var r = document.createRange(); r.selectNodeContents(scope);
    try { r.setEnd(container, offset); } catch (e) { return -1; }
    return r.toString().length;
  }

  // ---- 하이라이트 그리기 ----
  function unwrap(scope){
    var ms = scope.querySelectorAll('mark.md-anno');
    for (var i = 0; i < ms.length; i++){
      var m = ms[i], p = m.parentNode;
      while (m.firstChild) p.insertBefore(m.firstChild, m);
      p.removeChild(m);
    }
    scope.normalize();   // 쪼개졌던 텍스트 노드를 다시 합쳐 오프셋 계산을 단순하게
  }
  function resolve(scope, a){
    var txt = scope.textContent, q = a.quote || '';
    if (!q) return null;
    if (txt.substr(a.start, q.length) === q) return [a.start, a.start + q.length];
    var probe = (a.prefix || '') + q + (a.suffix || ''), i = txt.indexOf(probe);
    if (i >= 0) return [i + (a.prefix || '').length, i + (a.prefix || '').length + q.length];
    var best = -1, bd = Infinity, from = 0, j;   // 같은 문구가 여럿이면 원래 위치에 가장 가까운 것
    while ((j = txt.indexOf(q, from)) >= 0){
      var d = Math.abs(j - a.start); if (d < bd){ bd = d; best = j; } from = j + 1;
    }
    return best >= 0 ? [best, best + q.length] : null;
  }
  function paint(scope, hits){        // hits: [{a, s, e}] — 겹쳐도 되도록 경계로 잘라 칠한다
    var pts = {}, i, k;
    for (i = 0; i < hits.length; i++){ pts[hits[i].s] = 1; pts[hits[i].e] = 1; }
    var bs = Object.keys(pts).map(Number).sort(function(x, y){ return x - y; }), segs = [];
    for (i = 0; i < bs.length - 1; i++){
      var cov = [];
      for (k = 0; k < hits.length; k++) if (hits[k].s <= bs[i] && hits[k].e >= bs[i + 1]) cov.push(hits[k].a);
      if (cov.length) segs.push({s: bs[i], e: bs[i + 1], cov: cov});
    }
    wrapRanges(scope, segs, function(seg){
      var top = seg.cov[seg.cov.length - 1], noted = false;
      for (var q = 0; q < seg.cov.length; q++) if ((seg.cov[q].note || '').trim()) noted = true;
      var mk = document.createElement('mark');
      mk.className = 'md-anno' + (noted ? ' md-anno-note' : '');
      mk.setAttribute('data-ids', seg.cov.map(function(a){ return a.id; }).join(' '));
      mk.style.background = SWATCH[top.color] || SWATCH.yellow;
      return mk;
    });
  }
  function applyAll(){
    var sc = scopes(), i, j, k;
    // 뷰어가 다시 그려지면 셀이 통째로 새 요소다 — 마지막에 올려 뒀던 행 번호가 남아 있으면
    // 같은 행에 다시 마우스를 올려도 "이미 그 행"으로 보고 문장 처리를 건너뛴다.
    setPair(null); hoverRow = null;
    for (i = 0; i < sc.length; i++) unwrap(sc[i]);
    if (!sc.length){ renderPanel(); return; }   // 텍스트 패널이 안 보임(PDF만) — 목록만 유지
    var placed = [], has = {}, repaired = false;   // placed: [{item, anchor, sc, s, e}]
    var later = [], wide = wideNext; wideNext = false;
    for (i = 0; i < sc.length; i++) has[sc[i].getAttribute('data-side')] = 1;
    function done(an){ for (var z = 0; z < placed.length; z++) if (placed[z].an === an) return true; return false; }
    for (i = 0; i < sc.length; i++){
      var side = sc[i].getAttribute('data-side'), row = +sc[i].getAttribute('data-row');
      for (j = 0; j < items.length; j++){
        for (k = 0; k < items[j].anchors.length; k++){
          var an = items[j].anchors[k];
          if (an.side !== side || an.row !== row || done(an)) continue;
          var r = resolve(sc[i], an);
          if (r) placed.push({it: items[j], an: an, sc: sc[i], s: r[0], e: r[1]});
        }
      }
    }
    // 행이 밀렸으면 같은 쪽의 다른 셀에서 다시 찾는다. 다만 **틀린 자리에 눌러앉지 않게** —
    //  · 앞뒤 문맥까지 맞는 자리가 있으면 그것. 확실하므로 좌표를 고쳐 저장한다.
    //  · 문구만 맞는 자리가 하나뿐이어도 저장한다.
    //  · 여럿이면 원래 행에 가장 가까운 곳에 **표시만** 하고 저장하지 않는다 — 짐작으로
    //    원래 좌표를 덮어쓰면 되돌릴 길이 없다. 정렬이 깨진 폴백 패널(문서 전체가 한 셀)에도
    //    같은 이유로 저장하지 않는다.
    for (j = 0; j < items.length; j++){
      for (k = 0; k < items[j].anchors.length; k++){
        var b = items[j].anchors[k]; if (done(b)) continue;
        var probe = (b.prefix || '') + b.quote + (b.suffix || '');
        var hits = [], sure = null;
        for (i = 0; i < sc.length; i++){
          if (sc[i].getAttribute('data-side') !== b.side) continue;
          var txt = sc[i].textContent, q = txt.indexOf(b.quote);
          if (q < 0) continue;
          var row = +sc[i].getAttribute('data-row');
          if (!sure && probe !== b.quote){
            var c0 = txt.indexOf(probe);
            if (c0 >= 0) sure = {sc: sc[i], s: c0 + (b.prefix || '').length, row: row};
          }
          hits.push({sc: sc[i], s: q, row: row});
        }
        var pick = sure, firm = !!sure;
        if (!pick && hits.length === 1){ pick = hits[0]; firm = true; }
        else if (!pick && hits.length > 1){
          pick = hits[0];
          for (i = 1; i < hits.length; i++)
            if (Math.abs(hits[i].row - b.row) < Math.abs(pick.row - b.row)) pick = hits[i];
        }
        if (!pick){ later.push([items[j], b]); continue; }   // 문구가 바뀌었다 — 짝 앵커가 다 붙은 뒤에
        placed.push({it: items[j], an: b, sc: pick.sc, s: pick.s, e: pick.s + b.quote.length});
        if (firm && pick.sc.classList.contains('sbs-cell')){
          b.row = pick.row; b.start = pick.s; b.end = pick.s + b.quote.length;
          repaired = true;
        }
      }
    }
    // 문구가 바뀐 앵커(재변환·재번역) — 행 번호는 믿지 않는다.
    //  1) 같은 표시의 다른 쪽이 붙었으면 그 행의 반대쪽 셀에서 짝 문장을 다시 고른다 (만들 때와 같은 pairUp)
    //  2) 아니면 공백·하이픈만 다른 같은 문구 → 가장 비슷한 문장 (짝 앵커의 행, 없으면 자기 행 근처.
    //     "다시 찾기"는 문서 전체)
    // 정렬 격자에서 찾았으면 고친 좌표를 저장한다. 문구가 바뀌었으면 원래 단서를 orig에 한 번 남기고,
    // 확실하지 않으면 guess(추정) 표시를 붙여 서랍에서 사용자가 확인하게 한다.
    for (j = 0; j < later.length; j++){
      var it = later[j][0], b = later[j][1], sib = null, f = null;
      for (k = 0; k < placed.length; k++)
        if (placed[k].it === it && placed[k].an !== b && placed[k].sc.classList.contains('sbs-cell')){ sib = placed[k]; break; }
      var same = sc.filter(function(c){ return c.getAttribute('data-side') === b.side; });
      if (sib){
        var srow = +sib.sc.getAttribute('data-row'), cell = null;
        for (i = 0; i < same.length; i++) if (+same[i].getAttribute('data-row') === srow){ cell = same[i]; break; }
        f = siblingFind(b, cell, sib.sc, sib.s, sib.e);
      }
      if (!f) f = fuzzyFind(b, same, sib ? +sib.sc.getAttribute('data-row') : b.row, wide);
      if (!f) continue;
      placed.push({it: it, an: b, sc: f.sc, s: f.s, e: f.e});
      if (!f.sc.classList.contains('sbs-cell')) continue;   // 폴백 패널(문서 전체가 한 셀)엔 저장하지 않는다
      var ft = f.sc.textContent, nq = ft.slice(f.s, f.e);
      if (nq !== b.quote && !b.orig) b.orig = {quote: b.quote, prefix: b.prefix || '', suffix: b.suffix || ''};
      b.quote = nq; b.row = f.row; b.start = f.s; b.end = f.e;
      b.prefix = ft.slice(Math.max(0, f.s - CTX), f.s); b.suffix = ft.slice(f.e, f.e + CTX);
      if (!f.sure) b.guess = true;
      repaired = true;
    }
    // 보이는 쪽 앵커를 하나도 못 찾은 표시만 '위치 못 찾음' — 컬럼을 꺼 둔 쪽은 그냥 안 보이는 것.
    // 한쪽만 못 찾았으면 miss에 그 쪽을 적어 서랍에 '번역 쪽 못 찾음'으로 알린다.
    lost = {}; miss = {};
    for (j = 0; j < items.length; j++){
      var vis = 0, ok = 0, gone = [];
      for (k = 0; k < items[j].anchors.length; k++){
        if (!has[items[j].anchors[k].side]) continue;
        vis++;
        if (done(items[j].anchors[k])) ok++; else gone.push(items[j].anchors[k].side);
      }
      if (vis && !ok) lost[items[j].id] = 1;
      if (gone.length) miss[items[j].id] = gone;
    }
    for (i = 0; i < sc.length; i++){
      var hits = [];
      for (j = 0; j < placed.length; j++) if (placed[j].sc === sc[i]) hits.push({a: placed[j].it, s: placed[j].s, e: placed[j].e});
      if (hits.length) paint(sc[i], hits);
    }
    renderPanel();
    if (repaired){ repairedSince = true; save(); }
  }
  function missCount(){ var n = 0; for (var id in miss) n += miss[id].length; return n; }
  // 다른 논문으로 탭을 옮기면(본문만 갈아 끼움, §ui/spa.py) 그 논문의 하이라이트로 바꾼다.
  // 미뤄 둔 저장은 **옛 논문 토큰으로** 먼저 내보낸다 — 순서가 바뀌면 메모가 엉뚱한 논문에 쓰인다.
  window.__mdAnnoSwitch = function(tok, list){
    flush();
    window.__mdAnnoTok = tok; window.__mdAnno = list;
    items = Array.isArray(list) ? list : []; lost = {}; miss = {};
    closePop(); clearSel();
    window.__mdAnnoApply();
  };
  // 바깥(에이전트의 add_highlight 승인)에서 같은 논문의 annotations.json이 바뀌었다 — 다시 칠한다.
  // __mdAnnoSwitch는 쓰지 않는다: 그것은 먼저 flush()로 **화면의 옛 목록을 되저장**해 방금 더한 항목을
  // 파일에서 지운다. 미뤄 둔 내 손질(timer)이 있으면 그것을 지키고 서버에만 있는 항목만 보탠다.
  window.__mdAnnoReload = function(tok, list){
    if (!tok || tok !== window.__mdAnnoTok || !Array.isArray(list)) return;
    if (timer){
      var have = {}; items.forEach(function(x){ have[x.id] = 1; });
      list.forEach(function(x){ if (x && !have[x.id]) items.push(x); });
    } else items = list;
    window.__mdAnno = items;
    window.__mdAnnoApply();
  };
  window.__mdAnnoApply = function(){
    // 캔버스가 다시 지어지는 중이면(편집 끄기·번역 완료) .anno-scope가 아직 DOM에 없다.
    // 한 번만 시도하면 하이라이트가 안 그려진 채로 남는다 → 잠깐 더 두드린다.
    var tries = 0;
    (function attempt(){
      applyAll();
      if (!document.querySelector('.anno-scope') && ++tries < 6) setTimeout(attempt, 120 * tries);
    })();
  };

  // ---- 드래그 → 색 고르기 막대 ----
  function place(el, rect){
    el.style.visibility = 'hidden'; el.style.left = '0px'; el.style.top = '0px';
    var w = el.offsetWidth, h = el.offsetHeight;
    var x = Math.min(window.innerWidth - w - 8, Math.max(8, rect.left + rect.width / 2 - w / 2));
    var y = rect.top - h - 8;
    if (y < 8) y = Math.min(window.innerHeight - h - 8, rect.bottom + 8);
    el.style.left = x + 'px'; el.style.top = y + 'px'; el.style.visibility = '';
  }
  function dots(active){
    return ORDER.map(function(c){
      return '<button class="ab-dot' + (c === active ? ' on' : '') + '" data-color="' + c
        + '" style="background:' + SWATCH[c] + '"></button>';
    }).join('');
  }
  function clearSel(){ pending = null; bar.classList.remove('open'); }
  function onSelect(){
    if (dblGuard){ clearSel(); return; }   // 더블클릭이 고른 단어 — 드래그로 치지 않는다
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount){ clearSel(); return; }
    var r = sel.getRangeAt(0), sc = scopeOf(r.startContainer);
    if (!sc || sc !== scopeOf(r.endContainer)){ clearSel(); return; }
    var s = absOffset(sc, r.startContainer, r.startOffset), e = absOffset(sc, r.endContainer, r.endOffset);
    if (s < 0 || e <= s){ clearSel(); return; }
    var txt = sc.textContent;
    while (s < e && /\\s/.test(txt.charAt(s))) s++;          // 앞뒤 공백까지 칠하지 않게
    while (e > s && /\\s/.test(txt.charAt(e - 1))) e--;
    if (e <= s){ clearSel(); return; }
    pending = {scope: sc, start: s, end: e};
    bar.innerHTML = dots(null) + '<span class="ab-sep"></span>'
      + '<button class="ab-btn" data-act="note">' + md4T('메모 달기') + '</button>';
    bar.classList.add('open');
    place(bar, r.getBoundingClientRect());
  }

  // 표시를 만드는 길은 둘이다 —
  //   드래그   : 끌어 둔 **그 자리**에 (한쪽 컬럼, 글자 단위). 용어 하나만 짚고 싶을 때.
  //   더블클릭 : 호버로 짝지어 보여 주던 **그 문장 통째로** (원문·번역 양쪽에 같은 색·같은 메모).
  function anchorOf(cell, s, e){
    var txt = cell.textContent;
    return {side: cell.getAttribute('data-side'), row: +cell.getAttribute('data-row'),
      start: s, end: e, quote: txt.slice(s, e),
      prefix: txt.slice(Math.max(0, s - CTX), s), suffix: txt.slice(e, e + CTX)};
  }
  function span(list, idx){          // 문장 인덱스 묶음 → 이어진 한 구간
    if (!idx || !idx.length) return null;
    var s = list[idx[0]].s, e = list[idx[0]].e;
    for (var i = 1; i < idx.length; i++){ s = Math.min(s, list[idx[i]].s); e = Math.max(e, list[idx[i]].e); }
    return [s, e];
  }
  function at(list, off){            // 그 오프셋을 품은 문장 인덱스
    for (var i = 0; i < list.length; i++) if (list[i].s <= off && list[i].e > off) return [i];
    return null;
  }
  function pairAnchors(scope, pi){   // 짝 인덱스 → 원문·번역 두 앵커
    var P = scope.__pairing;
    if (!P || !P.pairs[pi]) return null;
    var re = span(P.en, P.pairs[pi][0]), rk = span(P.ko, P.pairs[pi][1]), out = [];
    if (re) out.push(anchorOf(P.enCell, re[0], re[1]));
    if (rk) out.push(anchorOf(P.koCell, rk[0], rk[1]));
    return out.length ? out : null;
  }
  function caretOffset(scope, x, y){  // 화면 좌표 → 셀 평문 오프셋
    var r = null;
    if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(x, y);
    else if (document.caretPositionFromPoint){
      var p = document.caretPositionFromPoint(x, y);
      if (p){ r = document.createRange(); r.setStart(p.offsetNode, p.offset); }
    }
    return r ? absOffset(scope, r.startContainer, r.startOffset) : -1;
  }
  function soloSentence(scope, off){  // 짝을 못 지은 행·한 컬럼만 볼 때 — 그 셀의 문장 하나
    if (off < 0) return null;
    var list = sentsOf(scope), r = span(list, at(list, off));
    return r ? [anchorOf(scope, r[0], r[1])] : null;
  }
  function create(color, withNote, anchors){
    if (!anchors || !anchors.length) return;
    lastColor = color;
    var a = {id: uid(), color: color, note: '', created: Date.now() / 1000, anchors: anchors};
    items.push(a);
    var sel = window.getSelection(); if (sel) sel.removeAllRanges();
    clearSel(); applyAll(); flush();
    if (withNote) openPop(a.id);
  }
  bar.addEventListener('mousedown', function(e){ e.preventDefault(); });
  bar.addEventListener('click', function(e){
    if (!pending) return;
    var an = [anchorOf(pending.scope, pending.start, pending.end)];   // 드래그 = 끌어 둔 그 자리
    var d = e.target.closest('.ab-dot');
    if (d){ create(d.getAttribute('data-color'), false, an); return; }
    if (e.target.closest('[data-act="note"]')) create(lastColor, true, an);
  });

  // 더블클릭 = 그 문장에 바로 메모 (호버로 보고 있던 그 짝 그대로)
  document.addEventListener('dblclick', function(e){
    closeTip();   // 더블클릭 간격이 길어 첫 클릭의 짝 툴팁이 이미 떴더라도 메모 칸에 자리를 내준다
    if (!e.target.closest) return;
    var sc = scopeOf(e.target); if (!sc) return;
    dblGuard = true; setTimeout(function(){ dblGuard = false; }, 400);
    var sel = window.getSelection(); if (sel) sel.removeAllRanges();   // 더블클릭이 고른 단어는 버린다
    clearSel();
    var m = e.target.closest('mark.md-anno');
    if (m){   // 이미 표시된 곳 — 새로 만들지 않고 그 메모를 연다
      var ids = (m.getAttribute('data-ids') || '').split(' ').filter(Boolean);
      if (ids.length) openPop(ids[ids.length - 1], m.getBoundingClientRect());
      return;
    }
    var sp = e.target.closest('[data-pair]');
    var an = sp ? pairAnchors(sc, +sp.getAttribute('data-pi')) : null;
    if (!an) an = soloSentence(sc, caretOffset(sc, e.clientX, e.clientY));
    // 곧바로 만들지 않고 아주 잠깐 기다린다 — 세 번째 클릭(문단 선택)이나 더블클릭-드래그가
    // 이어지면 물러야 하기 때문. 메모 칸이 열리는 체감에는 차이가 없다.
    if (an) dblTimer = setTimeout(function(){ dblTimer = null; create(lastColor, true, an); }, 240);
  });
  document.addEventListener('mousemove', function(e){   // 더블클릭 후 드래그로 범위를 늘리는 중
    if (dblTimer && e.buttons){ clearTimeout(dblTimer); dblTimer = null; dblGuard = false; }
  });

  // ---- 하이라이트 클릭 → 메모 카드 ----
  function marksFor(id){
    var ms = document.querySelectorAll('mark.md-anno'), out = [];
    for (var i = 0; i < ms.length; i++){
      if ((ms[i].getAttribute('data-ids') || '').split(' ').indexOf(id) >= 0) out.push(ms[i]);
    }
    return out;
  }
  function quoteOf(a, side){         // 그 쪽 앵커의 문장 (없으면 빈 문자열)
    for (var i = 0; i < a.anchors.length; i++) if (a.anchors[i].side === side) return a.anchors[i].quote;
    return '';
  }
  function cut(t, n){ return t.length > n ? t.slice(0, n) + '…' : t; }
  function openPop(id, rect){
    var a = find(id); if (!a) return;
    editing = id;
    var en = quoteOf(a, 'en'), ko = quoteOf(a, 'ko');
    pop.innerHTML = '<div class="apop-row">' + dots(a.color)
      + '<span class="apop-sp"></span><button class="apop-del">' + md4T('삭제') + '</button></div>'
      + (en ? '<div class="apop-quote">' + esc(cut(en, 130)) + '</div>' : '')
      + (ko ? '<div class="apop-quote apop-ko">' + esc(cut(ko, 130)) + '</div>' : '')
      + '<textarea placeholder="' + md4T('메모 (자동 저장)') + '"></textarea>';
    var ta = pop.querySelector('textarea'); ta.value = a.note || '';
    pop.classList.add('open');
    var ms = marksFor(id);
    place(pop, rect || (ms.length ? ms[0].getBoundingClientRect()
      : {left: window.innerWidth / 2, top: 140, bottom: 140, width: 0, height: 0}));
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  }
  function closePop(){ pop.classList.remove('open'); editing = null; }
  function remove(id){
    for (var i = 0; i < items.length; i++) if (items[i].id === id){ items.splice(i, 1); break; }
    applyAll(); flush();
    renderPanel();   // 목록 서랍에서도 바로 빠지게 — 안 그리면 지운 항목이 남아 한 번 더 눌러야 사라졌다
  }
  pop.addEventListener('click', function(e){
    var a = find(editing); if (!a) return;
    var d = e.target.closest('.ab-dot');
    if (d){ a.color = lastColor = d.getAttribute('data-color'); applyAll(); flush(); openPop(a.id); return; }
  });
  pop.addEventListener('mousedown', function(e){
    if (!e.target.closest('.apop-del') || e.button !== 0) return;
    e.preventDefault();   // 메모 칸 포커스를 뺏지 않는다 — 블러로 상자가 흔들려 click이 빗나가지 않게
    var a = find(editing); if (!a) return;
    closePop(); remove(a.id);
  });
  pop.addEventListener('input', function(e){
    var a = find(editing); if (!a || e.target.tagName !== 'TEXTAREA') return;
    a.note = e.target.value;
    var ms = marksFor(a.id), noted = !!a.note.trim();       // 밑줄 표시만 즉시 갱신 (다시 안 칠한다)
    for (var i = 0; i < ms.length; i++) ms[i].classList.toggle('md-anno-note', noted);
    save();
  });

  // ---- 목록 서랍 ----
  var refindMsg = '';
  function isGuess(a){ for (var i = 0; i < a.anchors.length; i++) if (a.anchors[i].guess) return true; return false; }
  function origText(a){   // 추정 배지 툴팁 — 고치기 전 원래 문장
    var out = '';
    for (var i = 0; i < a.anchors.length; i++) if (a.anchors[i].guess && a.anchors[i].orig)
      out += '\\n\\n' + (a.anchors[i].side === 'ko' ? md4T('원래 번역') : md4T('원래 원문')) + ': ' + cut(a.anchors[i].orig.quote, 200);
    return out;
  }
  function renderPanel(){
    var list = panel.querySelector('.ap-list');
    panel.querySelector('.ap-count').textContent = items.length;
    var btn = document.querySelector('.md4-anno-btn');
    if (btn) btn.setAttribute('data-count', items.length);
    function key(a){   // 문서 순서 — 원문 앵커 기준, 없으면 번역 앵커
      var an = a.anchors[0];
      for (var i = 0; i < a.anchors.length; i++) if (a.anchors[i].side === 'en') an = a.anchors[i];
      return [an.row, an.side === 'en' ? 0 : 1, an.start];
    }
    var sorted = items.slice().sort(function(x, y){
      var kx = key(x), ky = key(y);
      return kx[0] - ky[0] || kx[1] - ky[1] || kx[2] - ky[2];
    });
    var mc = missCount(), tools = panel.querySelector('.ap-tools');
    tools.innerHTML = (mc ? '<button class="ap-refind">' + md4T('다시 찾기') + '</button>'
      + '<span class="ap-tnote">' + md4T('위치를 못 찾은 곳 {n}군데', {n: mc}) + '</span>' : '')
      + (refindMsg ? '<span class="ap-tmsg">' + esc(refindMsg) + '</span>' : '');
    tools.style.display = tools.innerHTML ? '' : 'none';
    if (!sorted.length){
      list.innerHTML = '<div class="ap-empty">' + md4T('아직 표시한 곳이 없습니다.') + '<br>' + md4T('본문을 드래그해 색을 고르면 여기에 쌓입니다.') + '</div>';
      return;
    }
    list.innerHTML = sorted.map(function(a){
      var en = quoteOf(a, 'en'), ko = quoteOf(a, 'ko');
      return '<div class="ap-item' + (lost[a.id] ? ' ap-lost' : '') + '" data-id="' + esc(a.id) + '">'
        + '<div class="ap-top"><span class="ap-dot" style="background:' + (SWATCH[a.color] || SWATCH.yellow) + '"></span>'
        + '<span class="ap-side">' + (en && ko ? md4T('원문 · 번역') : (ko ? md4T('번역') : md4T('원문'))) + '</span>'
        + (lost[a.id] ? '<span class="ap-lostb">' + md4T('위치 못 찾음') + '</span>'
           : (miss[a.id] ? '<span class="ap-lostb">' + (miss[a.id][0] === 'ko' ? md4T('번역 쪽 못 찾음') : md4T('원문 쪽 못 찾음')) + '</span>' : ''))
        + (isGuess(a) ? '<button class="ap-guessb" title="' + md4T('문구가 바뀌어 가장 비슷한 문장에 붙였습니다. 눌러서 맞다고 확인') + ''
           + esc(origText(a)).replace(/"/g, '&quot;') + '">' + md4T('추정 · 확인') + '</button>' : '')
        + '<span class="apop-sp"></span><button class="ap-del" title="' + md4T('삭제') + '">&#10005;</button></div>'
        + (en ? '<div class="ap-q">' + esc(cut(en, 170)) + '</div>' : '')
        + (ko ? '<div class="ap-q ap-ko">' + esc(cut(ko, 170)) + '</div>' : '')
        + ((a.note || '').trim() ? '<div class="ap-n">' + esc(a.note) + '</div>' : '')
        + '</div>';
    }).join('');
  }
  panel.addEventListener('click', function(e){
    if (e.target.closest('.ap-x')){ panel.classList.remove('open'); return; }
    if (e.target.closest('.ap-refind')){   // 행 창 없이 문서 전체에서 다시 찾기 — 결과를 서랍에 알린다
      var before = missCount();
      wideNext = true; applyAll();
      var after = missCount();
      refindMsg = md4T('{a}군데 다시 붙임 · {b}군데 못 찾음', {a: before - after, b: after});
      renderPanel();
      setTimeout(function(){ refindMsg = ''; renderPanel(); }, 6000);
      return;
    }
    var it = e.target.closest('.ap-item'); if (!it) return;
    var id = it.getAttribute('data-id');
    if (e.target.closest('.ap-del')){ if (editing === id) closePop(); remove(id); return; }
    if (e.target.closest('.ap-guessb')){   // 추정이 맞다 — 표시와 원래 단서를 지운다
      var g = find(id);
      if (g) for (var z = 0; z < g.anchors.length; z++){ delete g.anchors[z].guess; delete g.anchors[z].orig; }
      flush(); renderPanel(); return;
    }
    var ms = marksFor(id);
    if (!ms.length){ openPop(id); return; }
    ms[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    for (var i = 0; i < ms.length; i++){
      ms[i].classList.remove('md-anno-flash'); void ms[i].offsetWidth; ms[i].classList.add('md-anno-flash');
    }
    afterScroll(function(){ openPop(id); });
  });
  // 'open'은 여러 경로에서 붙었다 뗀다(버튼·✕·Esc·에이전트 창이 열릴 때). 한 곳에서 관찰해
  // body 클래스를 맞춰야 본문 자리 비우기가 어느 경로로 닫아도 따라온다.
  function syncBody(){ document.body.classList.toggle('ma-open', panel.classList.contains('open')); }
  function syncTop(){
    var h = document.querySelector('.q-header');
    document.documentElement.style.setProperty(
      '--ma-top', (h ? Math.max(0, h.getBoundingClientRect().bottom) : 0) + 'px');
  }
  window.addEventListener('resize', syncTop);
  syncTop();
  if (window.MutationObserver)
    new MutationObserver(syncBody).observe(panel, {attributes: true, attributeFilter: ['class']});
  syncBody();
  window.__mdAnnoTogglePanel = function(){
    panel.classList.toggle('open');
    if (panel.classList.contains('open')){ syncTop(); renderPanel(); }
  };

  // ---- 닫기·정리 ----
  document.addEventListener('mouseup', function(e){
    if (bar.contains(e.target) || pop.contains(e.target) || panel.contains(e.target)) return;
    setTimeout(onSelect, 0);
  });
  document.addEventListener('keyup', function(e){ if (e.key && e.key.indexOf('Arrow') === 0) setTimeout(onSelect, 0); });
  document.addEventListener('mousedown', function(e){
    if (pop.contains(e.target) || bar.contains(e.target) || panel.contains(e.target)) return;
    if (pop.classList.contains('open')) closePop();
  }, true);
  document.addEventListener('click', function(e){
    if (e.detail >= 3 && dblTimer){          // 세 번 클릭 = 문단 선택. 더블클릭이 예약한 표시를 물린다
      clearTimeout(dblTimer); dblTimer = null; dblGuard = false;
      setTimeout(onSelect, 0);               // 그 선택은 드래그로 인정 — 색 막대를 띄운다
      return;
    }
    var sel = window.getSelection();          // 하이라이트 안을 드래그한 직후라면 색 막대에 양보한다
    if (sel && !sel.isCollapsed && scopeOf(sel.anchorNode)) return;
    var m = e.target.closest ? e.target.closest('mark.md-anno') : null;
    if (!m || e.target.closest('a')) return;   // 인용 링크 클릭은 원래 동작 그대로
    var ids = (m.getAttribute('data-ids') || '').split(' ').filter(Boolean);
    if (ids.length) openPop(ids[ids.length - 1], m.getBoundingClientRect());
  });
  document.addEventListener('scroll', function(e){
    if (scrollLock || pop.contains(e.target) || panel.contains(e.target)) return;
    clearSel(); if (pop.classList.contains('open')) closePop();
  }, true);
  document.addEventListener('keydown', function(e){
    if (e.key !== 'Escape') return;
    if (stip.classList.contains('open') || stipTimer) closeTip();
    else if (pop.classList.contains('open')) closePop();
    else if (panel.classList.contains('open')) panel.classList.remove('open');
  });

  // ---- 문장 단위 호버 (원문 ↔ 번역 1:1) ----
  // 행에 처음 마우스를 올릴 때만 그 행의 두 셀을 문장으로 쪼개 짝을 지어 span으로 감싼다(지연 처리).
  // 논문 한 편의 문단이 수백 개라 전부 미리 처리하면 첫 렌더가 무거워진다.
  var hoverRow = null, hoverPair = null;
  // 두 컬럼(.sbs-two)이면 올린 문장의 짝을 칠하고, 한 컬럼(.sbs-tip — 반대쪽은 숨은 셀)이면
  // 문장을 **클릭**했을 때 짝 문장을 툴팁으로 보여 준다 (같은 문장 다시 클릭·바깥 클릭·Esc·스크롤로 닫힘)
  var PAIR_CELL = '.sbs-grid.sbs-two .sbs-cell, .sbs-grid.sbs-tip .sbs-cell';
  var stip = document.getElementById('md-sent-tip');
  if (!stip){ stip = document.createElement('div'); stip.id = 'md-sent-tip'; document.body.appendChild(stip); }
  var stipTimer = null;   // 클릭 뒤 툴팁 예약 — 더블클릭(메모)이면 두 번째 클릭이 물린다
  var tipPair = null;     // 지금 툴팁이 가리키는 짝 id (같은 문장을 다시 클릭하면 닫기)

  function rowCells(row){
    return [].slice.call(document.querySelectorAll(
      '.sbs-grid.sbs-two .sbs-cell[data-row="' + row + '"], .sbs-grid.sbs-tip .sbs-cell[data-row="' + row + '"]'));
  }
  // hideTip = 보이는 툴팁만 내린다(누르는 순간 — 드래그 시작에도). closeTip = 짝 기억까지 지운다.
  function hideTip(){ clearTimeout(stipTimer); stipTimer = null; stip.classList.remove('open'); }
  function closeTip(){ hideTip(); tipPair = null; }
  // 클릭한 문장(el)의 짝을 숨은 셀에서 찾아 (x, y) 근처에 툴팁으로. 짝을 못 찾은 문장(solo)은
  // 띄우지 않는다. 실제로 띄웠을 때만 true.
  function showTip(id, el, x0, y0){
    hideTip();
    if (!el || el.hasAttribute('data-solo')) return false;
    var cell = el.closest('.sbs-cell');
    var ghost = cell.parentElement.querySelector('.sbs-ghost[data-row="' + cell.getAttribute('data-row') + '"]');
    if (!ghost) return false;
    var parts = ghost.querySelectorAll('[data-pair="' + id + '"]'), txt = '', i;
    for (i = 0; i < parts.length; i++) txt += parts[i].textContent;
    txt = txt.replace(/\s+/g, ' ').trim();
    if (!txt) return false;
    stip.innerHTML = '<span class="st-side">' + (ghost.getAttribute('data-side') === 'ko' ? md4T('번역') : md4T('원문')) + '</span>';
    stip.appendChild(document.createTextNode(txt));
    // 문장 첫 줄 아래(자리가 없으면 위)에 — 문장이 여러 줄이면 클릭한 줄 기준
    var rs = el.getClientRects(), r = rs[0] || el.getBoundingClientRect();
    for (i = 0; i < rs.length; i++) if (y0 >= rs[i].top && y0 <= rs[i].bottom){ r = rs[i]; break; }
    stip.style.left = '0px'; stip.style.top = '0px'; stip.classList.add('open');
    var w = stip.offsetWidth, h = stip.offsetHeight, x = Math.min(Math.max(8, x0 - 40), innerWidth - w - 8);
    var y = r.bottom + 8; if (y + h > innerHeight - 8) y = Math.max(8, r.top - h - 8);
    stip.style.left = x + 'px'; stip.style.top = y + 'px';
    return true;
  }
  function prepareRow(cells){
    var en = null, ko = null, i;
    for (i = 0; i < cells.length; i++){
      if (cells[i].dataset.sent) return;                  // 이미 처리한 행
      if (cells[i].getAttribute('data-side') === 'en') en = cells[i]; else ko = cells[i];
    }
    if (!en || !ko) return;                               // 한 컬럼만 보이면 문장 대응은 의미가 없다
    en.dataset.sent = ko.dataset.sent = '1';
    var E = sentsOf(en), K = sentsOf(ko), pairs = pairUp(E, K);
    if (!pairs) return;
    // 표시를 문장 단위로 잡을 때 이 짝 정보를 쓴다 (원문에 그으면 번역 쪽 문장도 함께)
    en.__pairing = ko.__pairing = {en: E, ko: K, pairs: pairs, enCell: en, koCell: ko};
    var row = en.getAttribute('data-row'), segE = [], segK = [];
    function push(dst, src, idx, id, solo, pi){
      for (var z = 0; z < idx.length; z++) dst.push({s: src[idx[z]].s, e: src[idx[z]].e, id: id, solo: solo, pi: pi});
    }
    for (i = 0; i < pairs.length; i++){
      var id = row + '-' + i, solo = !pairs[i][0].length || !pairs[i][1].length;
      push(segE, E, pairs[i][0], id, solo, i);
      push(segK, K, pairs[i][1], id, solo, i);
    }
    function make(seg){
      var sp = document.createElement('span');
      sp.className = 'sent';
      sp.setAttribute('data-pair', seg.id);
      sp.setAttribute('data-pi', seg.pi);      // 짝 인덱스 — 더블클릭이 이 짝을 통째로 집는다
      if (seg.solo) sp.setAttribute('data-solo', '1');    // 짝을 못 찾은 문장 — 옅은 회색으로 구분
      return sp;
    }
    wrapRanges(en, segE, make);
    wrapRanges(ko, segK, make);
  }

  // 부드러운 스크롤이 **끝난 뒤에** fn을 부른다. 애니메이션 도중에 메모 카드를 열면,
  // 스크롤을 감시하는 닫기 리스너가 그 카드를 곧바로 닫아 버린다(먼 곳의 메모일수록 확실히).
  // 그동안 scrollLock을 세워 닫기 리스너 자체도 쉬게 한다.
  function afterScroll(fn){
    var quiet = null, hard = null;
    function fire(){
      clearTimeout(quiet); clearTimeout(hard);
      document.removeEventListener('scroll', bump, true);
      scrollLock = false; fn();
    }
    function bump(){ clearTimeout(quiet); quiet = setTimeout(fire, 130); }
    scrollLock = true;
    document.addEventListener('scroll', bump, true);
    quiet = setTimeout(fire, 130);
    hard = setTimeout(fire, 2000);   // 스크롤이 아예 안 일어난 경우까지
  }
  function setPair(id){
    if (id === hoverPair) return;
    var on = document.querySelectorAll('.sent-hl'), i;
    for (i = 0; i < on.length; i++) on[i].classList.remove('sent-hl');
    hoverPair = id;
    if (!id) return;
    var el = document.querySelectorAll('[data-pair="' + id + '"]');
    for (i = 0; i < el.length; i++) el[i].classList.add('sent-hl');
  }
  function clearHover(){
    var on = document.querySelectorAll('.sbs-cell.sbs-hl');
    for (var i = 0; i < on.length; i++) on[i].classList.remove('sbs-hl');
    hoverRow = null; setPair(null);
  }
  document.addEventListener('scroll', closeTip, true);
  document.addEventListener('mousedown', function(e){   // 짝 기억(tipPair)은 click에서 판정
    hideTip();
    if (e.button !== 0) tipPair = null;   // 오른쪽 클릭 등은 click이 안 온다 — 여기서 잊는다
  }, true);
  document.addEventListener('mouseover', function(e){
    var cell = e.target.closest ? e.target.closest(PAIR_CELL) : null;
    if (!cell){ if (hoverRow !== null) clearHover(); return; }
    var row = cell.getAttribute('data-row'), fresh = false;
    if (row !== hoverRow){
      clearHover();
      hoverRow = row;
      var cells = rowCells(row);
      for (var i = 0; i < cells.length; i++) cells[i].classList.add('sbs-hl');
      prepareRow(cells); fresh = true;
    }
    var t = e.target.closest('[data-pair]');
    if (!t && fresh){   // 방금 감싼 직후엔 새 mouseover가 오지 않으니 커서 아래를 직접 본다
      var under = document.elementFromPoint(e.clientX, e.clientY);
      t = under && under.closest ? under.closest('[data-pair]') : null;
    }
    setPair(t ? t.getAttribute('data-pair') : null);
  });
  // 한 컬럼(.sbs-tip): 문장 클릭 = 짝 문장 툴팁. 더블클릭(메모)과 겹치지 않게 250ms 미뤄 띄우고,
  // 두 번째 클릭이 오면(e.detail ≥ 2) 예약을 물린다. 드래그 선택·링크·그림·표시(메모) 클릭은 제외.
  document.addEventListener('click', function(e){
    var prev = tipPair; tipPair = null;     // 어떤 클릭이든 일단 닫힌 상태(누를 때 이미 내렸다)
    if (e.detail !== 1){ hideTip(); return; }
    if (!e.target.closest) return;
    var sel = window.getSelection();
    if (sel && !sel.isCollapsed && scopeOf(sel.anchorNode)) return;   // 드래그 선택 — 색 막대 몫
    if (e.target.closest('a, img, button, input, textarea, mark.md-anno')) return;
    if (window.__md4FxAt && window.__md4FxAt(e.target)) return;   // 수식 — 원본 대조 팝오버 몫
    var cell = e.target.closest('.sbs-grid.sbs-tip .sbs-cell');
    if (!cell || cell.classList.contains('sbs-ghost')) return;
    var t = e.target.closest('[data-pair]');
    if (!t){   // 아직 문장으로 쪼개지 않은 행 (호버 없이 바로 클릭) — 쪼갠 뒤 클릭 지점을 다시 본다
      prepareRow(rowCells(cell.getAttribute('data-row')));
      var under = document.elementFromPoint(e.clientX, e.clientY);
      t = under && under.closest ? under.closest('[data-pair]') : null;
    }
    if (!t || !cell.contains(t)) return;
    var id = t.getAttribute('data-pair');
    if (id === prev) return;                // 같은 문장을 다시 클릭 — 닫기
    var x = e.clientX, y = e.clientY;
    stipTimer = setTimeout(function(){
      stipTimer = null;
      if (showTip(id, t, x, y)) tipPair = id;
    }, 250);
  });

  setTimeout(applyAll, 400);   // 첫 렌더 안전망 (이후엔 뷰어가 __mdAnnoApply를 부른다)
})();
</script>
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
  var PIN = '<span class="md-cite-pin" title="' + md4T('고정됨 — 바깥을 클릭하면 닫힘') + '">'
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
      html += '<button class="md-cite-copy" title="' + md4T('제목 복사') + '" data-title="' + escAttr(t) + '">' + COPY_SVG + '</button>';
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
    // 설정·섹션 트리 열이 접혀 있으면 먼저 펼친다 — 안 보이는 곳으로 점프하지 않도록.
    if (!row.offsetParent || row.getBoundingClientRect().width < 1){
      if (typeof emitEvent === 'function') emitEvent('md4-open-side');
      setTimeout(function(){ b.click(); }, 200);   // 펼쳐진 뒤 같은 클릭을 다시
      return;
    }
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

# 바깥 링크(참고문헌의 DOI·arXiv, 본문 URL 등)는 새 창으로 — 그냥 두면 앱 창(pywebview)이 그 페이지로
# 넘어가 버려 논문 화면을 잃는다. 누르는 순간 target=_blank를 달면, 브라우저에선 새 탭으로, 앱 창에선
# pywebview가(OPEN_EXTERNAL_LINKS_IN_BROWSER) 시스템 기본 브라우저로 연다. 같은 서버 주소·문서 안 앵커는 그대로.
_EXTLINK_HTML = """
<script>
(function(){
  if (window.__md4ExtLinks) return; window.__md4ExtLinks = true;
  function mark(e){
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href') || '';
    if (!/^(https?:)?\/\//i.test(href) && !/^mailto:/i.test(href)) return;
    var u; try { u = new URL(a.href, location.href); } catch (err) { return; }
    if (u.origin === location.origin) return;
    a.target = '_blank'; a.rel = 'noopener noreferrer';
  }
  document.addEventListener('click', mark, true);
  document.addEventListener('auxclick', mark, true);
})();
</script>
"""

# 화살표 자동 변환 — 메모(Crepe)·하이라이트 메모 칸·마크다운 편집기에서 `->`를 치면 →, `<-`는 ←,
# `<->`는 ↔, `=>`는 ⇒ (Notion과 같게). 마지막 글자를 치는 순간 앞 글자와 합쳐 바꾼다. 한국어 조합 중이거나
# 선택 영역이 있으면 건드리지 않는다. `-->`(HTML 주석 끝)·`<=`(이하)는 바꾸지 않는다. 바로 Backspace로
# 지우면 되돌릴 수 있다(→ 대신 원래 두 글자가 남는다).
_ARROW_HTML = """
<script>
(function(){
  if (window.__md4Arrows) return; window.__md4Arrows = true;
  var SCOPE = '.md4-notes-ed, #md-anno-pop textarea, .cm-en-editor';
  var RULES = {'>': {'-': '\\u2192', '=': '\\u21d2', '\\u2190': '\\u2194'}, '-': {'<': '\\u2190'}};
  var last = null;   // 방금 바꾼 것 — {el, arrow, raw}: 바로 Backspace면 원래 글자로 되돌린다
  function replaceArrow(e){
    var map = RULES[e.data]; if (!map) return false;
    var t = e.target;
    if (t.tagName === 'TEXTAREA'){
      var p = t.selectionStart; if (p !== t.selectionEnd || p < 1) return false;
      var prev = t.value.charAt(p - 1), out = map[prev];
      if (!out || (prev === '-' && t.value.charAt(p - 2) === '-')) return false;
      e.preventDefault();
      t.setRangeText(out, p - 1, p, 'end');
      t.dispatchEvent(new Event('input', {bubbles: true}));
      last = {el: t, arrow: out, raw: prev + e.data};
      return true;
    }
    var sel = window.getSelection();
    if (!sel || !sel.isCollapsed || !sel.rangeCount) return false;
    var n = sel.anchorNode, off = sel.anchorOffset;
    if (!n || n.nodeType !== 3 || off < 1) return false;   // 같은 글자 노드 안에서만
    var prev = n.data.charAt(off - 1), out = map[prev];
    if (!out || (prev === '-' && n.data.charAt(off - 2) === '-')) return false;
    if (n.parentElement && n.parentElement.closest('code, pre, .cm-line .tok-monospace')) return false;
    e.preventDefault();
    sel.modify('extend', 'backward', 'character');
    document.execCommand('insertText', false, out);
    last = {el: null, arrow: out, raw: prev + e.data};
    return true;
  }
  document.addEventListener('beforeinput', function(e){
    if (e.isComposing || !e.target.closest || !e.target.closest(SCOPE)) { last = null; return; }
    if (e.inputType === 'deleteContentBackward' && last){   // 바꾼 직후 Backspace = 원래 두 글자로
      var l = last; last = null;
      if (l.el){
        var p = l.el.selectionStart;
        if (p === l.el.selectionEnd && l.el.value.charAt(p - 1) === l.arrow){
          e.preventDefault(); l.el.setRangeText(l.raw, p - 1, p, 'end');
          l.el.dispatchEvent(new Event('input', {bubbles: true}));
        }
        return;
      }
      var s = window.getSelection(), n = s && s.anchorNode;
      if (s && s.isCollapsed && n && n.nodeType === 3 && n.data.charAt(s.anchorOffset - 1) === l.arrow){
        e.preventDefault(); s.modify('extend', 'backward', 'character');
        document.execCommand('insertText', false, l.raw);
      }
      return;
    }
    if (e.inputType !== 'insertText' || !e.data || e.data.length !== 1){ last = null; return; }
    if (!replaceArrow(e)) last = null;
  }, true);
})();
</script>
"""

# 본문 그림 클릭 → 전체화면 확대 뷰어. 휠(트랙패드 핀치 포함)·버튼·더블클릭으로 확대,
# 드래그로 이동, Esc·바깥 클릭으로 닫기. 순수 클라이언트 처리 — 이미 받아 둔 이미지를 그대로 쓴다.
_IMG_ZOOM_HTML = """
<div id="md-img-zoom">
  <img alt="">
  <div class="mdz-bar">
    <button data-act="out" title="⦃축소 (-)⦄">&#8722;</button>
    <span class="mdz-pct">100%</span>
    <button data-act="in" title="⦃확대 (+)⦄">&#43;</button>
    <button data-act="fit" title="⦃화면에 맞추기 (0)⦄">
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
        <path d="M4 9V4h5v2H6v3H4zm11-5h5v5h-2V6h-3V4zM4 15h2v3h3v2H4v-5zm14 3v-3h2v5h-5v-2h3z"/>
      </svg></button>
    <span class="mdz-sep"></span>
    <button data-act="copy" title="⦃그림 복사 (⌘/Ctrl+C)⦄">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
        <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
      </svg></button>
    <button data-act="save" title="⦃그림 저장 (⌘/Ctrl+S)⦄">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
        <path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z"/>
      </svg></button>
    <button data-act="recrop" title="⦃그림 영역 다시 지정 — PDF에서 다시 자르기⦄">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
        <path d="M17 15h2V7c0-1.1-.9-2-2-2H9v2h8v8zM7 17V1H5v4H1v2h4v10c0 1.1.9 2 2 2h10v4h2v-4h4v-2H7z"/>
      </svg></button>
    <button data-act="figko" title="⦃그림 속 글자 번역 — 번역 그림 보기·만들기·고치기⦄">⦃가⦄</button>
    <span class="mdz-sep"></span>
    <button data-act="close" title="⦃닫기 (Esc)⦄">&#10005;</button>
  </div>
  <div class="mdz-foot"><div class="mdz-cap"></div>
    <details class="mdz-alt"><summary>⦃그림 설명⦄ <span>⦃· 저자가 PDF에 단 대체 텍스트⦄</span></summary>
      <div class="mdz-alt-text"></div></details>
    <div class="mdz-hint">⦃휠·더블클릭으로 확대 · 드래그로 이동 · ⌘/Ctrl+C 복사 · Esc로 닫기⦄</div></div>
</div>
<script>
(function(){
  if (window.__mdImgZoom) return; window.__mdImgZoom = true;
  // 고해상도 그림(#mdw= 힌트)은 예전 크기로 — 픽셀만 촘촘해지고 본문 레이아웃은 그대로.
  // 확대 뷰어의 그림엔 적용하지 않는다(거기선 원본 픽셀을 다 써야 한다).
  function hidpi(root){
    var imgs = root.querySelectorAll ? root.querySelectorAll('.md-preview img[src*="#mdw="]') : [];
    for (var i = 0; i < imgs.length; i++){
      var m = /#mdw=(\d+)/.exec(imgs[i].getAttribute('src') || '');
      if (m && !imgs[i].style.width) imgs[i].style.width = m[1] + 'px';
    }
  }
  // 그림 설명(window.__mdFigAlts, 파일명 → 설명)이 있는 본문 그림에 표시 — 글자는 넣지 않는다
  // (본문 textContent가 바뀌면 하이라이트 앵커·문장 짝이 어긋난다)
  function fileOf(src){
    var p = String(src || '').split('#')[0].split('?')[0];
    try { return decodeURIComponent(p.slice(p.lastIndexOf('/') + 1)); } catch (e) { return ''; }
  }
  function altOf(src){        // 번역 그림(figure-3.ko.png, figure_ko.py)은 원본 그림의 설명을 쓴다
    var m = window.__mdFigAlts || {}, f = fileOf(src);
    return m[f] || m[f.replace(/\\.[a-z]{2,3}(-[A-Za-z0-9]{2,8})*\\.png$/, '.png')] || '';
  }
  function markAlts(root){
    var imgs = root.querySelectorAll ? root.querySelectorAll('.md-preview img') : [];
    for (var i = 0; i < imgs.length; i++){
      var has = !!altOf(imgs[i].getAttribute('src'));
      imgs[i].classList.toggle('md-has-alt', has);
      if (has && !imgs[i].title) imgs[i].title = md4T('그림 설명 있음 — 클릭해서 확대');
    }
  }
  hidpi(document); markAlts(document);
  window.__mdFigAltsMark = function(){ markAlts(document); };
  new MutationObserver(function(muts){
    for (var i = 0; i < muts.length; i++)
      for (var j = 0; j < muts[i].addedNodes.length; j++){
        var n = muts[i].addedNodes[j];
        if (n.nodeType === 1) { hidpi(n.parentNode || n); markAlts(n.parentNode || n); }
      }
  }).observe(document.body, {childList: true, subtree: true});
  var box = document.getElementById('md-img-zoom');
  if (!box) return;
  var img = box.querySelector('img'), pct = box.querySelector('.mdz-pct'), cap = box.querySelector('.mdz-cap');
  var altBox = box.querySelector('.mdz-alt'), altText = box.querySelector('.mdz-alt-text');
  var s = 1, tx = 0, ty = 0, base = 1, drag = null, moved = false;
  // base = 열었을 때의 배율(화면에 꽉 차게, 저해상도 그림을 너무 흐리게 늘리지 않도록 3배까지).
  // 논문 그림은 원본이 작은 경우가 많아 1배로 열면 본문에서 보던 크기와 다를 게 없다.
  var foot = box.querySelector('.mdz-foot'), footH = 52;
  function layoutFoot(){             // 캡션 높이만큼 아래를 비운다 (CSS --mdz-foot)
    footH = Math.max(52, (foot ? foot.offsetHeight : 0) + 24);
    box.style.setProperty('--mdz-foot', footH + 'px');
  }
  function fitScale(){
    var availW = box.clientWidth - 64, availH = box.clientHeight - 52 - footH;  // CSS max-*와 같은 여백
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
    var my = Math.max(0, (img.offsetHeight * s - (r.height - 52 - footH)) / 2);
    tx = Math.min(mx, Math.max(-mx, tx));
    ty = Math.min(my, Math.max(-my, ty));
  }
  function zoomAt(mx, my, ns){       // (mx,my) 아래에 있던 지점을 제자리에 두고 확대/축소
    ns = Math.min(base * 8, Math.max(base, ns));    // 연 크기 아래로는 안 줄이고, 그 8배까지
    var r = box.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + 52 + (r.height - 52 - footH) / 2;
    var px = (mx - cx - tx) / s, py = (my - cy - ty) / s;
    tx = mx - cx - ns * px; ty = my - cy - ns * py; s = ns;
    if (s <= base) { tx = 0; ty = 0; }
    clampPan(); apply();
  }
  function zoomBy(f){
    var r = box.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + 52 + (r.height - 52 - footH) / 2, s * f);
  }
  function fit(){ base = fitScale(); s = base; tx = 0; ty = 0; apply(); }
  function close(){ box.classList.remove('open'); }
  // 복사·저장 — 서버 쪽 경로(/wdimages/<토큰>/<파일>)만 넘긴다. #mdw= 같은 꼬리는 뗀다.
  function imgPath(){ try { return new URL(img.src).pathname; } catch (e) { return ''; } }
  var CHECK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">'
    + '<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
  function flash(act){                 // 눌린 버튼을 잠깐 체크로 — 복사는 눈에 보이는 결과가 없어서
    var b = box.querySelector('.mdz-bar button[data-act="' + act + '"]');
    if (!b || b.__orig) return;
    b.__orig = b.innerHTML; b.innerHTML = CHECK;
    setTimeout(function(){ b.innerHTML = b.__orig; b.__orig = null; }, 1200);
  }
  function asPng(blob){                // 클립보드는 PNG만 확실히 받는다 — 다른 형식은 캔버스로 바꾼다
    if (blob.type === 'image/png') return Promise.resolve(blob);
    return createImageBitmap(blob).then(function(bm){
      var c = document.createElement('canvas'); c.width = bm.width; c.height = bm.height;
      c.getContext('2d').drawImage(bm, 0, 0);
      return new Promise(function(res){ c.toBlob(res, 'image/png'); });
    });
  }
  function copyImg(){
    var path = imgPath(); if (!path) return;
    // 서버가 OS 클립보드에 넣는다 — 웹뷰(앱 창)엔 이미지 클립보드 API가 없거나 막혀 있다
    function viaServer(){ if (typeof emitEvent === 'function') emitEvent('md4-img-copy', {path: path}); }
    try {
      if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) throw 0;
      // Safari는 사용자 동작 안에서 write를 불러야 해서, blob은 Promise째로 건넨다
      var item = new ClipboardItem({'image/png': fetch(path).then(function(r){ return r.blob(); }).then(asPng)});
      navigator.clipboard.write([item]).then(function(){ flash('copy'); }, viaServer);
    } catch (e) { viaServer(); }
  }
  function saveImg(){
    var path = imgPath();
    if (path && typeof emitEvent === 'function') emitEvent('md4-img-save', {path: path});
  }
  window.__mdImgCopied = function(){ flash('copy'); };

  function esc(t){ var d = document.createElement('div'); d.textContent = t == null ? '' : t; return d.innerHTML; }
  // info = {label: 'Figure 3', text: 캡션} — 없으면 그림·표 맵(figrefs.py)에서 이 그림의 캡션을 찾는다
  function openZoom(src, alt, info, from){
    if (!info && window.__mdFigCaptionFor) info = window.__mdFigCaptionFor(src, from);
    img.src = src;
    if (info && info.text){
      cap.innerHTML = '<b>' + esc(info.label) + '.</b> ' + esc(info.text);
      cap.classList.toggle('long', info.text.length > 160);
    } else { cap.textContent = alt || ''; cap.classList.remove('long'); }
    cap.scrollTop = 0;
    var desc = altOf(src);           // 그림 설명 — 접힘 상태는 그림을 바꿔도 유지한다
    altText.textContent = desc; altText.scrollTop = 0;
    box.classList.toggle('has-alt', !!desc);
    // 본문 그림(수식 크롭 말고)이면 영역 다시 지정 단추 (figure_crop.py)
    var path = imgPath(), fig = new RegExp('^/wdimages/[^/]+/(?!formula-)[^/]+$').test(path);
    box.classList.toggle('can-recrop', !!window.__md4FigRecrop && fig);
    // 그림 속 글자 번역 단추 (figure_ko.py) — 번역 그림(.ko.png)에서 열어도 같은 메뉴
    box.classList.toggle('can-figko', !!window.__md4FigKoOpen && fig && window.__md4FigTrOk !== false);
    if (window.__md4FigKoOpen) window.__md4FigKoOpen(fig ? path : '');
    box.classList.add('open');       // 크기를 재려면 먼저 보여야 한다(display:none이면 0)
    layoutFoot();
    if (img.complete) fit(); else img.onload = fit;
  }
  window.__mdImgZoomOpen = openZoom;   // 본문의 그림·표 참조(figrefs.py) 클릭도 이 뷰어로 연다
  altBox.addEventListener('toggle', function(){       // 설명을 펼치거나 접으면 그림 자리도 다시 잡는다
    if (!box.classList.contains('open')) return;
    layoutFoot(); fit();
  });
  // 연 채로 그림만 바꾼다(번역 그림 ↔ 원본, figure_ko.py) — 캡션은 그대로
  window.__mdImgZoomSrc = function(src){ img.src = src; if (img.complete) fit(); else img.onload = fit; };
  document.addEventListener('click', function(ev){
    var t = ev.target;
    if (!t || t.tagName !== 'IMG' || !t.closest || !t.closest('.md-preview')) return;
    if (window.__md4FxAt && window.__md4FxAt(t)) return;   // 읽기 실패로 남은 수식 그림 — 원본 대조 팝오버 몫
    ev.preventDefault();             // 그림을 감싼 링크가 있어도 이동하지 않게
    // title은 캡션 대신 쓰지 않는다 — 그림 설명 표시(markAlts)가 단 안내문일 수 있다
    openZoom(t.currentSrc || t.src, t.getAttribute('alt')
      || (t.classList.contains('md-has-alt') ? '' : t.getAttribute('title')) || '', null, t);
  });
  box.addEventListener('click', function(ev){
    if (moved) { moved = false; return; }               // 드래그 끝의 클릭은 닫기가 아니다
    if (ev.target === img || (ev.target.closest && ev.target.closest('.mdz-bar, .mdz-cap, .mdz-alt, .mdz-kmenu, .mdz-kedit'))) return;
    close();                                            // 배경 클릭 → 닫기
  });
  [].forEach.call(box.querySelectorAll('.mdz-bar button'), function(b){
    b.addEventListener('click', function(){
      var a = b.getAttribute('data-act');
      if (a === 'in') zoomBy(1.25); else if (a === 'out') zoomBy(0.8);
      else if (a === 'fit') fit(); else if (a === 'copy') copyImg(); else if (a === 'save') saveImg();
      else if (a === 'recrop'){ var p = imgPath(); close(); if (window.__md4FigRecrop) window.__md4FigRecrop(p); }
      else if (a === 'figko'){ if (window.__md4FigKoMenu) window.__md4FigKoMenu(imgPath()); }
      else close();
    });
  });
  box.addEventListener('wheel', function(ev){
    // 긴 캡션 위의 휠은 캡션 스크롤 — 그림 확대가 아니다
    if (ev.target.closest && ev.target.closest('.mdz-cap') && cap.scrollHeight > cap.clientHeight) return;
    if (ev.target.closest && ev.target.closest('.mdz-alt-text') && altText.scrollHeight > altText.clientHeight) return;
    if (ev.target.closest && ev.target.closest('.mdz-kedit, .mdz-kmenu')) return;   // 번역 고치기 목록 스크롤
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
    layoutFoot();                                 // 폭이 바뀌면 캡션 줄 수도 바뀐다
    var nb = fitScale();
    s = s * (nb / base); base = nb;               // 보고 있던 확대 비율은 유지
    clampPan(); apply();
  });
  document.addEventListener('keydown', function(ev){
    if (!box.classList.contains('open')) return;
    if (ev.key === 'Escape') close();
    else if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA'
                           || ev.target.isContentEditable)) return;   // 입력 중인 글자는 뺏지 않는다
    else if ((ev.metaKey || ev.ctrlKey) && (ev.key === 'c' || ev.key === 'C')) {
      if (String(window.getSelection() || '')) return;   // 캡션 글자를 골라 둔 복사는 그대로
      copyImg();
    }
    else if ((ev.metaKey || ev.ctrlKey) && (ev.key === 's' || ev.key === 'S')) saveImg();
    else if (ev.metaKey || ev.ctrlKey) return;            // 다른 조합키(⌘+= 브라우저 확대 등)는 뺏지 않는다
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


_CANVAS_PANELS = ("en", "ko", "pdf")
_DEFAULT_CANVAS_PANELS = {"en": True, "ko": True, "pdf": False}


def load_panels() -> dict:
    """마지막으로 쓰던 캔버스 패널(원문/번역/PDF) — 전역 설정([ui].panels)에 기억, 논문마다 따로 두지 않는다.

    값이 없거나 깨졌으면(목록이 아니거나 아는 패널이 하나도 없으면) 기본값(원문+번역).
    """
    v = config.load_config().get("ui", {}).get("panels")
    if not isinstance(v, list) or not any(k in v for k in _CANVAS_PANELS):
        return dict(_DEFAULT_CANVAS_PANELS)
    return {k: k in v for k in _CANVAS_PANELS}


def save_panels(state: dict) -> None:
    """켜 둔 패널을 전역 설정에 적는다 — 다음에 여는 논문(앱을 다시 켜도)이 그 구성으로 열린다."""
    on = [k for k in _CANVAS_PANELS if state.get(k)]
    if on:
        config.set_section_value("ui", "panels", on)


def wd_token(root: Path) -> str:
    """워크디렉토리별 짧은 토큰 — 이미지/PDF URL에 넣어 논문 간 캐시 충돌을 막는다.

    URL이 논문마다 달라야 브라우저가 다른 논문의 figure-1.png / pdfpage/0 캐시를 재사용하지 않는다.
    """
    import hashlib

    return hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]


# 그림 표시 크기의 기준 — 예전 그림 래스터(144dpi)는 1pt = 2px로 그대로 보였다.
# 고해상도로 다시 그린 그림(PNG에 dpi가 적힘)도 이 크기로 보여야 본문 레이아웃이 안 바뀐다.
_BASE_DPI = 144.0


@functools.lru_cache(maxsize=512)
def _hidpi_width(path: str, mtime_ns: int) -> int | None:  # noqa: ARG001 — mtime은 캐시 무효화 키
    """고해상도 그림이면 예전 크기로 보일 CSS 폭(px), 아니면 None (헤더만 읽는다)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            dpi = (im.info.get("dpi") or (0, 0))[0]
            width = im.size[0]
    except Exception:  # noqa: BLE001
        return None
    if not dpi or dpi <= _BASE_DPI * 1.05:
        return None
    return max(1, round(width * _BASE_DPI / float(dpi)))


def _to_served(path: str, token: str, images_dir: Path | None = None) -> str:
    if path.startswith(("http://", "https://", "data:", "/wdimages/")):
        return path
    name = path.rstrip("/").split("/")[-1]  # 파일명만 서버 라우트로
    url = f"/wdimages/{token}/{name}"
    if images_dir is not None:
        f = images_dir / name
        try:
            w = _hidpi_width(str(f), f.stat().st_mtime_ns)
        except OSError:
            w = None
        if w:
            url += f"#mdw={w}"  # 표시 폭 힌트 — 프래그먼트라 요청·캐시엔 영향 없다 (_HIDPI_JS가 읽는다)
    return url


def served_markdown(md: str, token: str, images_dir: Path | None = None) -> str:
    """프리뷰용 — 모든 이미지 참조(md·HTML)를 out/images 서버 라우트로 치환 (논문별 토큰 포함).

    `images_dir`를 주면 고해상도 그림에 표시 폭 힌트(#mdw=)를 붙인다.
    """
    def sub(m: re.Match) -> str:
        return m.group(1) + _to_served(m.group(2), token, images_dir) + m.group(3)

    md = _MD_IMG_RE.sub(sub, md)
    md = _HTML_IMG_RE.sub(sub, md)
    return md


def cite_tips_js(tips: dict[str, str]) -> str:
    """서지정보 사전을 window.__mdCiteTips에 실어줄 JS 표현식 (호버 툴팁 조회용).

    인용 링크(마크다운 그대로 유지)는 href의 #ref-N 번호로 이 맵을 찾는다.
    </script> 조기 종료·JS 인젝션 방지를 위해 < 를 이스케이프한다.
    """
    payload = json.dumps(tips, ensure_ascii=False).replace("<", "\\u003c")
    return f"window.__mdCiteTips = {payload};"


def fig_alts_js(alts: dict[str, str]) -> str:
    """그림 설명 사전(out 그림 파일명 → 설명)을 window.__mdFigAlts에 실어줄 JS (확대 뷰어용, §figure_alt)."""
    payload = json.dumps(alts, ensure_ascii=False).replace("<", "\\u003c")
    return f"window.__mdFigAlts = {payload}; window.__mdFigAltsMark && window.__mdFigAltsMark();"


def fn_tips_js(tips: dict[str, str]) -> str:
    """각주 사전을 window.__mdFnTips에 실어줄 JS (본문 위첨자 링크 #fn-N 호버 툴팁 조회용)."""
    payload = json.dumps(tips, ensure_ascii=False).replace("<", "\\u003c")
    return f"window.__mdFnTips = {payload};"


def anno_items_js(items: list[dict], token: str) -> str:
    """저장된 하이라이트·메모와 논문 토큰을 클라이언트에 실어줄 JS (되저장 주소로도 쓰인다)."""
    payload = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
    tok = json.dumps(token)
    # 이미 떠 있는 하이라이트 스크립트가 있으면(탭 전환) 그 논문으로 바꿔 끼우게 한다
    return (f"if (window.__mdAnnoSwitch) window.__mdAnnoSwitch({tok}, {payload});"
            f" else {{ window.__mdAnnoTok = {tok}; window.__mdAnno = {payload}; }}")


# 메모 패널 위지윅 편집기(Milkdown Crepe) — 개발 시점에 묶어 둔 파일을 그대로 서빙한다
# (오프라인·빌드 도구 없음, 다시 묶기: tools/bundle_crepe/README.md). 주소의 ?v=는 판이 바뀌면 캐시를 깬다.
CREPE_DIR = Path(__file__).resolve().parent / "assets" / "crepe"
CREPE_URL = "/static/crepe"


def _crepe_version() -> str:
    try:
        return (CREPE_DIR / "VERSION").read_text(encoding="utf-8").split()[1]
    except (OSError, IndexError):
        return "0"


def notes_editor_js(el_id: str, md: str) -> str:
    """메모 컨테이너(el_id)에 Crepe 편집기를 얹는 JS. 라이브러리는 처음 한 번만 불러온다.

    갈아 끼우기(§ui/spa.py)에서는 이 스크립트가 새 요소보다 먼저 도착할 수 있어 요소가 생길 때까지
    기다린다. DOM에서 사라진 옛 편집기는 새로 얹을 때 치운다. 바뀐 마크다운은 리스너가 이미 0.2초
    모아 주므로 그대로 보내고, 0.8초 모아 쓰기·저장 상태는 파이썬(notes_typed)이 맡는다 — 패널을
    떠나거나 내보낼 때 기다리던 글자까지 바로 쓰려면 마지막 내용이 서버에 있어야 해서다.
    편집기 쪽으로 되쓰는 경로는 없다(한국어 조합 중 글자를 건드리지 않게).

    create()는 비동기라 끝나기 전 getMarkdown()은 빈 문자열일 수 있다 — 끝나면 el.__md4ready를 세우고
    그때의 마크다운을 el.__md4base(기준)로 둔다. Crepe는 얹으며 글머리·빈 줄 모양을 다시 쓰므로 원문이
    아니라 이것과 견줘야 '고치지 않은 편집기'를 알아본다. 고치기 전(dirty 아님)엔 기준과 같은 내용을 보내지 않는다."""
    ver = _crepe_version()
    return (
        "(function(id, md){var W=window;"
        "if(!W.__md4CrepeLoad){W.__md4CrepeLoad=new Promise(function(ok, no){"
        "if(W.MilkdownCrepe)return ok();"
        f"var l=document.createElement('link');l.rel='stylesheet';l.href='{CREPE_URL}/crepe.min.css?v={ver}';"
        "document.head.appendChild(l);"
        f"var s=document.createElement('script');s.src='{CREPE_URL}/crepe.min.js?v={ver}';"
        "s.onload=function(){ok();};s.onerror=function(e){W.__md4CrepeLoad=null;no(e);};"
        "document.head.appendChild(s);});}"
        "var tries=0;"
        "function mount(){var el=document.getElementById(id);"
        "if(!el){if(++tries<400)setTimeout(mount,50);return;}"
        "if(el.__md4crepe)return;"
        "var all=W.__md4Crepes=W.__md4Crepes||{};"
        "Object.keys(all).forEach(function(k){if(!document.getElementById(k)){"
        "try{all[k].destroy();}catch(e){}delete all[k];}});"
        "var crepe=new W.MilkdownCrepe({root:el, defaultValue:md,"
        "featureConfigs:{placeholder:{text:md4T('메모를 입력하세요 — / 를 누르면 표·체크박스·코드 등을 넣을 수 있습니다'),"
        "mode:'doc'}}});"
        "el.__md4crepe=crepe;all[id]=crepe;"
        # 표가 든 문서는 만들자마자 표 정리 트랜잭션이 돌아 내용이 같은 채로 한 번 불린다 → 같으면 건너뜀
        "crepe.on(function(api){api.markdownUpdated(function(ctx, m, prev){"
        "if(!el.__md4ready||m===prev||m===el.__md4sent)return;"
        "if(!el.__md4dirty&&m===el.__md4base)return;el.__md4dirty=true;el.__md4sent=m;"
        "if(typeof emitEvent==='function')emitEvent('md4-notes',{id:id, md:m});});});"
        "crepe.create().then(function(){el.__md4base=crepe.getMarkdown();el.__md4ready=true;});}"
        "W.__md4CrepeLoad.then(mount, function(){var el=document.getElementById(id);"
        "if(el)el.textContent=md4T('메모 편집기를 불러오지 못했습니다.');});"
        f"}})({json.dumps(el_id)}, {json.dumps(md, ensure_ascii=False)});"
    )


def _live_preview_on() -> bool:
    """편집기 '서식 보기'(라이브 프리뷰)를 켤지 — 전역 설정 [ui].md_live_preview, 기본 켜짐."""
    v = config.load_config().get("ui", {}).get("md_live_preview", True)
    return v if isinstance(v, bool) else True


def notes_event_text(args: object, el_id: str) -> str | None:
    """'md4-notes' 이벤트 인자에서 저장할 마크다운을 꺼낸다 — 이 편집기(el_id)가 보낸 것만.

    `ui.on` 핸들러는 창(클라이언트) 단위라 논문 탭을 오가며 화면을 다시 지으면 옛 핸들러가 남는다.
    컨테이너 id를 맞춰 보지 않으면 다른 논문 메모가 이 논문의 notes.md로 들어간다."""
    if not isinstance(args, dict) or args.get("id") != el_id:
        return None
    md = args.get("md")
    return md if isinstance(md, str) else None


# capture(): 편집기마다 {ready, md, base, empty}를 브라우저에서 바로 받는다 — create() 전이면 {ready:false}.
# empty는 화면(ProseMirror)에 글자가 하나도 없는지 — getMarkdown()의 빈 문자열이 진짜인지 맞춰 보는 증인.
NOTES_CAPTURE_JS = (
    "(function(ids){var C=window.__md4Crepes||{},o={};ids.forEach(function(id){try{"
    "var c=C[id],el=document.getElementById(id);if(!c||!el)return;"
    "if(!el.__md4ready){o[id]={ready:false};return;}"
    "var pm=el.querySelector('.ProseMirror');"
    "o[id]={ready:true,md:c.getMarkdown(),base:el.__md4base,empty:!pm||!pm.textContent.trim()};"
    "}catch(e){}});return o;})(%s)")


def notes_pick(md: object, mem: dict, js: dict | None = None) -> str | None:
    """편집기 하나가 준 마크다운 중 실제로 파일에 쓸 것 — 쓸 게 없으면 None.

    mem은 그 편집기를 얹을 때의 기록: raw(그때 파일 내용), saved(그 뒤 이 편집기가 쓴 마지막 내용, 없으면 None).
    파일과 견주지 않는다 — 다른 곳에서 파일이 바뀐 뒤 옛 내용이 떠 있는 편집기가 그것을 되써 덮지 않게.
    js는 capture()가 받은 {ready, base, empty}(이벤트로 온 것이면 None): 준비 전 편집기는 건너뛰고, 고친
    적 없는 편집기(base와 같음)도 건너뛰고, 화면엔 글자가 있는데 빈 문자열이 오면 헛값으로 보고 건너뛴다."""
    if not isinstance(md, str):
        return None
    if js is not None and js.get("ready") is not True:
        return None
    ref = mem.get("saved")
    if ref is None:
        ref = mem.get("raw") or ""
        if md.strip() == ref.strip() or (js is not None and md == js.get("base")):
            return None
    elif md == ref:
        return None
    if not md.strip() and ref.strip() and js is not None and not js.get("empty"):
        return None
    return md


_HEADING_LINE_RE = re.compile(r"^#{1,6}\s")


def anchored_markdown(md: str, section_map: list[dict], page_by_id: dict | None = None,
                      jump: bool = False) -> str:
    """프리뷰 헤더에 `sec-<id>` 앵커를 심는다 (마크다운 스크롤 이동용).

    en.md의 헤더 줄과 section_map의 id를 문서 순서로 짝지어(라인 이동에 견고), 각 헤더 줄
    **끝에** 앵커를 이어 붙인다. page_by_id가 있으면 앵커에 data-page(0-based)도 넣어 PDF 스크롤 싱크에 쓴다.
    jump=True면 각 헤더 옆에 '섹션 트리에서 편집' 점프 버튼(클릭 → 섹션 트리 항목 하이라이트+스크롤)을 붙인다.

    앵커를 별도의 새 줄로 끼우지 않는 이유: 그러면 프리뷰가 세는 줄 번호가 원문보다 헤더 개수만큼
    밀린다(코퍼스 헤더 수 중앙값 39, 최대 261). 에디터와 프리뷰를 소스 줄로 맞추는 분할 뷰에서는
    그 어긋남이 곧 싱크 실패라, 줄 수가 원문과 1:1이어야 한다.
    """
    ids = [e["id"] for e in sorted(section_map, key=lambda x: x["out_line"])]
    out: list[str] = []
    j = 0
    for line in md.splitlines():
        if _HEADING_LINE_RE.match(line) and j < len(ids):
            sid = ids[j]
            pg = (page_by_id or {}).get(sid) if page_by_id else None
            dp = f' data-page="{pg - 1}"' if pg else ""
            line = line.rstrip() + f' <a id="sec-{sid}"{dp}></a>'
            if jump:  # 헤더 텍스트 끝에 점프 버튼(섹션 트리 해당 항목으로 이동+하이라이트)
                line += (
                    f' <a class="md-sec-jump" href="#sectree-{sid}" data-sec="{sid}" '
                    f'title="{escape(t("섹션 트리에서 이 섹션의 구조를 편집"))}">✎ {escape(t("섹션 구조"))}</a>')
            j += 1
        out.append(line)
    # 끝의 개행을 살린다 — splitlines/join 왕복은 문서 끝 빈 줄을 하나 삼킨다.
    # 분할 뷰가 프리뷰 줄 N을 에디터 줄 N에 대응시키므로 줄 수가 원문과 같아야 한다.
    return "\n".join(out) + ("\n" if md.endswith("\n") else "")


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


def _head_key(text: str) -> str:
    """헤더 비교용 지문 — 태그·마크다운 표기·공백을 걷은 영숫자."""
    return re.sub(r"[^0-9a-z]+", "", re.sub(r"<[^>]+>", "", text).lower())


def row_sections(rows: list, smap: list[dict]) -> dict[int, str]:
    """뷰어 행 → 섹션 id. 헤더가 있는 행을 렌더된 헤더(section map)와 **제목으로** 순서대로 맞춘다.

    개수가 1:1일 때만 대응시키면 헤더 하나만 어긋나도(제목 행 등) 문서 전체가 PDF 싱크·섹션 점프를
    잃는다 — 'PDF를 열었는데 보던 쪽이 안 나오는' 원인이었다. 앞에서부터 가까운 몇 개 안에서 같은
    제목을 찾고, 못 찾은 헤더는 건너뛴다(그 행은 위쪽 최근 섹션을 따른다).
    """
    out: dict[int, str] = {}
    keys = [_head_key(e.get("text", "")) for e in smap]
    j = 0
    for k, row in enumerate(rows):
        head = row[2]
        if not head:
            continue
        hk = _head_key(head[1])
        hit = next((i for i in range(j, min(j + 4, len(smap))) if hk and keys[i] == hk), None)
        if hit is None:
            continue
        j = hit + 1
        out[k] = smap[hit]["id"]
    return out


def row_pages(rows: list, smap: list[dict], page_by_id: dict) -> dict[int, int]:
    """뷰어 행 → PDF 페이지(0-based) — row_sections의 섹션에 쪽이 있는 행만."""
    return {k: page_by_id[sid] - 1 for k, sid in row_sections(rows, smap).items() if page_by_id.get(sid)}


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
    """변환 직후 논문 서지(제목·저자·연도·venue)를 LLM으로 추출·저장 (목록 표시·검색·정렬용).

    본체는 `paper_meta.reextract` — 홈의 'AI로 다시 추출'도 같은 함수를 쓴다(사용자가 고친 칸은
    지킨다). 여기서는 키가 없거나 추출이 실패해도 변환을 막지 않도록 조용히 넘어간다.
    """
    try:
        provider = config.build_provider()
    except RuntimeError:
        return
    from md4paper import paper_meta

    try:
        paper_meta.reextract(wd, provider)
    except Exception:  # noqa: BLE001
        pass


def _auto_bibsource(wd: WorkDir) -> dict:
    """변환 직후 서지를 온라인으로 보강 — 정확한 .bib 항목 + 참고문헌 DOI (§enrich.boost_workdir).

    **`library.auto_export` 바로 앞에 놓여야 한다.** 저장 위치의 `references.bib`은 그때 쓰이고,
    항목 내용은 여기서 받아 둔 `bib_source.json`에서 나온다 — 순서가 뒤집히면 논문을 올린
    시점에는 PDF에서 읽은 부실한 항목이 쌓이고, 정확해지는 건 사용자가 나중에 따로 돌릴 때가 된다.

    프로젝트 배정(`set_project`) **뒤**라야 그 프로젝트의 설정(`bib_lookup`)을 볼 수 있다.
    실패해도 변환 결과는 이미 안전하므로 조용히 넘어간다 — 네트워크가 없어도 변환은 되어야 한다.
    """
    from md4paper import enrich, library

    project = library.project_of(wd)
    if not config.resolve_bib_lookup(project):
        return {}
    try:
        return enrich.boost_workdir(wd, mailto=config.resolve_enrich_mailto() or None)
    except Exception:  # noqa: BLE001 — 레이트리밋·오프라인 어느 것도 변환을 실패시키지 않는다
        return {}


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


# 창 크롬 — 헤더의 논문 탭 + '새 창으로' 링크. 고정한 논문이 여러 편이면 탭 줄이 가로로
# 스크롤한다(헤더 높이는 그대로 — 읽는 자리를 빼앗지 않는다).
# 논문 탭을 누르면 페이지가 새로 열린다(탭 = 페이지 이동). 그 사이 새 문서의 첫 페인트는
# 아무것도 없는 흰 화면이라 '반짝'하고 지나간다. 그래서 **Vue가 뜨기 전에** 헤더 자리를
# 같은 색으로 칠해 둔다 — 첫 페인트부터 헤더가 있던 자리에 헤더 색이 있으니 깜빡임이 사라진다.
# 색은 지난 페이지에서 실제로 재 둔 값(sessionStorage)을 쓰고, 없으면 기본값으로 시작한다
# (다크 모드·테마 변경에도 어긋나지 않게 — 상수로 박아 두면 언젠가 색이 갈린다).
_PREPAINT_HTML = """
<style>
  html { background: #fff; }
  body::before { content: ''; position: fixed; top: 0; left: 0; right: 0; height: 44px;
    background: var(--md4-prepaint, #5898d4); z-index: 0; }
</style>
<script>
(function(){
  try {
    var c = sessionStorage.getItem('md4:headerbg');
    if (c) document.documentElement.style.setProperty('--md4-prepaint', c);
  } catch (e) {}
  // 실제 헤더 색을 재서 다음 페이지의 미리칠하기에 물려 준다
  window.addEventListener('load', function(){
    setTimeout(function(){
      var h = document.querySelector('.q-header');
      if (!h) return;
      var bg = getComputedStyle(h).backgroundColor;
      try { if (bg && bg !== 'rgba(0, 0, 0, 0)') sessionStorage.setItem('md4:headerbg', bg); } catch (e) {}
    }, 300);
  });
})();
</script>
"""

_CHROME_CSS = """
/* 호버 툴팁(Quasar q-tooltip) — 기본 10px은 앱 창에서 읽기 힘들다. 본문 보조 글자 크기에 맞춘다. */
.q-tooltip { font-size: 13px; line-height: 1.45; padding: 6px 10px; max-width: 380px; white-space: pre-line; }
/* 헤더 한 줄 — 논문 탭은 아래 끝에 물리고, 그림자를 없애야 활성 탭이 본문으로 이어져 보인다. */
.md4-header { min-height: 44px; padding: 0 8px !important; box-shadow: none !important; gap: 4px; }
/* 탭 줄은 헤더 남는 폭을 다 쓴다 — 탭은 240px까지 넓게, 모자라면 다 같이 줄고(크롬처럼), 64px 밑으로는
   줄지 않고 가로 스크롤. '+'가 마지막 탭 바로 뒤에 붙도록 줄 자체는 늘리지 않는다(0 1 auto). */
.md4-tabstrip { flex: 0 1 auto; min-width: 0; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }
.md4-tabstrip::-webkit-scrollbar { height: 0; }
.md4-tabtools { padding-bottom: 5px; opacity: .9; flex: 0 0 auto; }
/* 헤더 오른쪽 상태 칩 — 이 논문이 어디까지 왔는지 + 다음 할 일(누르면 번역 패널).
   흰 칩(todo)이 '다음 할 일'이고, 번역된 논문은 헤더에 잠긴 옅은 칩으로 조용히. */
.md4-chipslot { align-self: flex-end; padding: 0 8px 3px 0; }  /* 32px 탭과 가운데를 맞춘다: (32-26)/2 */
.md4-chip { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 11px 0 8px;
  border-radius: 13px; font-size: 11.5px; font-weight: 500; cursor: pointer; white-space: nowrap;
  color: rgba(255,255,255,.92); background: rgba(255,255,255,.16); transition: background .12s ease; }
.md4-chip:hover { background: rgba(255,255,255,.28); }
.md4-chip.todo { background: #fff; color: #2383e2; font-weight: 600; }
.md4-chip.todo:hover { background: #f3f8ff; }
.md4-chip.warn { background: #f59e0b; color: #fff; }
/* 본문 = 레일 + (작업 패널 | 캔버스). 높이는 헤더(44px)와 페이지 여백(16px×2)을 뺀 나머지. */
/* 리뷰 화면은 앱처럼 창 끝까지 채운다 — 페이지 여백(16px)·자식 간격을 걷어 레일이 왼쪽 끝, 본문이 아래
   끝에 닿게. 여백을 남기면 아래에 어중간한 빈 띠가 생기고, 높이를 잘못 빼면 창 전체가 따로 스크롤된다. */
.nicegui-content:has(> .nicegui-sub-pages .md4-body) { padding: 0; gap: 0; }
.nicegui-content:has(> .nicegui-sub-pages .md4-body) > :not(.nicegui-sub-pages) { display: none; }
.md4-body { height: calc(100vh - 44px); align-items: stretch; }
.md4-rail { flex: 0 0 54px; width: 54px; height: 100%; background: #f4f3f1; border-right: 1px solid #e6e4e0;
  padding: 6px 0; align-items: center; flex-wrap: nowrap; }
.md4-railbtn { display: flex; flex-direction: column; align-items: center; gap: 3px; width: 46px;
  padding: 7px 0 6px; border-radius: 9px; cursor: pointer; color: #6b6862; font-size: 10.5px; line-height: 1;
  user-select: none; transition: background .12s ease, color .12s ease; }
.md4-railbtn + .md4-railbtn { margin-top: 2px; }
.md4-railbtn:hover { background: rgba(0,0,0,.06); color: #37352f; }
.md4-railbtn.on { background: rgba(35,131,226,.13); color: #2383e2; }
.md4-panel { height: 100%; width: 100%; overflow-y: auto; padding: 12px 12px 14px; gap: 12px; flex-wrap: nowrap; }
.md4-panel-title { font-size: 11px; font-weight: 700; letter-spacing: .05em; color: #9a968e; }
/* 메모 패널 — 머리 한 줄 아래를 위지윅 편집기(Milkdown Crepe)가 꽉 채운다. 편집기 안 빈 곳을 눌러도
   글쓰기로 들어가게 .milkdown·.ProseMirror를 상자 높이까지 늘린다. 블록 핸들(⋮⋮ +)은 숨긴다 — 좁은
   패널에서 왼쪽 여백만 먹고 쓸 일이 없다. 표·체크박스 삽입은 '/' 메뉴로 그대로 된다.
   글꼴·크기·간격은 Notion 기준(16px · 1.5, 시스템 산세리프, 촘촘한 블록 리듬)으로 맞춘다. Crepe가 변수로
   받는 것(--crepe-font-*, --crepe-base-font-size, 글자·코드 색)은 변수로, 나머지는 Crepe 셀렉터 앞에
   .md4-notes-ed를 붙여 구체도로 덮는다 — Crepe CSS는 나중에 <link>로 붙어 순서로는 이기지 못한다. */
.md4-notes { flex: 1 1 0; min-height: 0; flex-wrap: nowrap; overflow: hidden; }  /* 패널을 넘지 않고 안에서만 스크롤 */
.md4-notes .md4-notes-status { font-size: 11px; color: #9a968e; }
/* 메모 탭 — 패널 제목 아래 작은 글자 줄. 고른 탭만 옅은 바탕에 진하게, 넘치면 가로로 스크롤. */
.md4-notetabs { overflow-x: auto; overflow-y: hidden; scrollbar-width: none; flex-shrink: 0; margin-top: -4px; }
.md4-notetabs::-webkit-scrollbar { height: 0; }
.md4-notetab { display: inline-flex; align-items: center; gap: 2px; flex: 0 0 auto; padding: 2px 8px;
  border-radius: 6px; font-size: 13.5px; font-weight: 500; color: #6b6862; cursor: pointer; white-space: nowrap; user-select: none; }
.md4-notetab:hover { background: rgba(55,53,47,.06); color: #37352f; }
.md4-notetab.on { background: rgba(55,53,47,.08); color: #37352f; font-weight: 600; }
.md4-notetab-x { opacity: 0; margin-right: -4px; border-radius: 3px; padding: 1px; }
.md4-notetab:hover .md4-notetab-x { opacity: .6; }
.md4-notetab-x:hover { opacity: 1 !important; background: rgba(55,53,47,.12); }
.md4-notetab-add { flex: 0 0 auto; }
/* 이름 바꾸는 중 — 탭 자리에 같은 크기의 입력 칸 */
.md4-notetab.editing { background: #fff; box-shadow: 0 0 0 1.5px #2383e2; padding: 0 6px; cursor: text; }
.md4-notetab-in .q-field__control, .md4-notetab-in .q-field__marginal { height: 24px; min-height: 24px; }
.md4-notetab-in .q-field__native { padding: 0; min-height: 24px; font-size: 13.5px; font-weight: 500; color: #37352f; }
.md4-notetab.shown { box-shadow: inset 0 0 0 1px rgba(55,53,47,.14); color: #37352f; }
/* 메모 칸 — 하나면 꽉 채우고, 나뉘면 위아래(q-splitter). 탭을 끄는 동안만 위·아래 놓을 자리가 덮인다. */
.md4-notestack { position: relative; flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.md4-notesplit { flex: 1 1 auto; min-height: 0; }
.md4-notesplit > .q-splitter__panel { display: flex; flex-direction: column; min-height: 0; }
.md4-notesplit > .q-splitter__separator { background: transparent; height: 9px !important; }
.md4-notesplit > .q-splitter__separator::after { content: ""; position: absolute; left: 0; right: 0; top: 4px;
  height: 1px; background: #e6e4e0; }
.md4-notesplit > .q-splitter__separator:hover::after { background: #2383e2; height: 2px; }
.md4-notepane { flex: 1 1 auto; min-height: 0; height: 100%; }
.md4-notepane-head { flex: 0 0 auto; min-height: 20px; padding: 0 4px; }
.md4-notepane-t { font-size: 12px; color: #9a968e; }
.md4-notepane-t.on { color: #37352f; font-weight: 600; }
.md4-notepane-x { color: #9a968e; cursor: pointer; border-radius: 3px; padding: 1px; }
.md4-notepane-x:hover { color: #37352f; background: rgba(55,53,47,.1); }
.md4-notepane-body { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.md4-notedrop { display: none; position: absolute; left: 0; right: 0; z-index: 5; align-items: center;
  justify-content: center; font-size: 13px; color: #2383e2; background: rgba(35,131,226,.06);
  border: 1.5px dashed rgba(35,131,226,.45); border-radius: 8px; }
.md4-notedrop-top { top: 0; height: calc(50% - 3px); }
.md4-notedrop-bottom { bottom: 0; height: calc(50% - 3px); }
.md4-tabdrag .md4-notedrop { display: flex; }
.md4-notedrop.hot { background: rgba(35,131,226,.16); border-color: #2383e2; }
.md4-notedrop * { pointer-events: none; }
.md4-notes-ed { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain;
  border-radius: 6px; background: #fbfaf9; }
.md4-notes-ed .milkdown { min-height: 100%; display: flex; flex-direction: column;
  --md4n-font: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
  --crepe-font-default: var(--md4n-font); --crepe-font-title: var(--md4n-font);
  --crepe-font-code: "SFMono-Regular", Menlo, Consolas, "PT Mono", "Liberation Mono", Courier, monospace;
  --crepe-base-font-size: 16px; --crepe-color-background: #fbfaf9; --crepe-color-on-background: #37352f;
  --crepe-color-inline-code: #eb5757;
  --md4n-muted: #9a968e; --md4n-placeholder: #b3b0a8; --md4n-underline: rgba(55,53,47,.4);
  --md4n-rule: rgba(55,53,47,.16); --md4n-grid: #e6e4e0; --md4n-code-bg: rgba(135,131,120,.15);
  --md4n-block-bg: #f7f6f3; --md4n-check: #2383e2;
  line-height: 1.7; -webkit-font-smoothing: antialiased; }
.md4-notes-ed .milkdown .ProseMirror { flex: 1 1 auto; padding: 12px 14px 40px 14px; line-height: 1.7; }
.md4-notes-ed .milkdown-block-handle { display: none !important; }  /* Crepe가 만드는 건 div.milkdown-block-handle — 태그가 아니라 클래스 */
.md4-notes-ed .milkdown .ProseMirror p { padding: 3px 0; margin: 1px 0; line-height: 1.7; }
.md4-notes-ed .milkdown .ProseMirror strong { font-weight: 600; }
.md4-notes-ed .milkdown .ProseMirror h1, .md4-notes-ed .milkdown .ProseMirror h2,
.md4-notes-ed .milkdown .ProseMirror h3, .md4-notes-ed .milkdown .ProseMirror h4,
.md4-notes-ed .milkdown .ProseMirror h5, .md4-notes-ed .milkdown .ProseMirror h6 {
  font-family: var(--md4n-font); font-weight: 600; line-height: 1.3; padding: 3px 0; margin-bottom: 1px; }
.md4-notes-ed .milkdown .ProseMirror h1 { font-size: 1.9em; font-weight: 700; margin-top: 32px; margin-bottom: 4px; }
.md4-notes-ed .milkdown .ProseMirror h2 { font-size: 1.5em; font-weight: 650; margin-top: 22px; }
.md4-notes-ed .milkdown .ProseMirror h3 { font-size: 1.2em; margin-top: 16px; }
.md4-notes-ed .milkdown .ProseMirror h4, .md4-notes-ed .milkdown .ProseMirror h5,
.md4-notes-ed .milkdown .ProseMirror h6 { font-size: 1em; line-height: 1.5; margin-top: 12px; }
.md4-notes-ed .milkdown .ProseMirror > :first-child,
.md4-notes-ed .milkdown .ProseMirror > .prosemirror-virtual-cursor:first-child + * { margin-top: 0; }
.md4-notes-ed .milkdown .ProseMirror a { color: inherit; text-decoration: underline 1px var(--md4n-underline);
  text-underline-offset: 2px; transition: text-decoration-color .12s ease; }
.md4-notes-ed .milkdown .ProseMirror a:hover { text-decoration-color: currentColor; }
.md4-notes-ed .milkdown .ProseMirror code { display: inline; font-family: var(--crepe-font-code); font-size: 85%;
  line-height: normal; color: var(--crepe-color-inline-code); background: var(--md4n-code-bg);
  border-radius: 3px; padding: 0.2em 0.4em; }
.md4-notes-ed .milkdown .ProseMirror pre code { padding: 0; background: transparent; color: inherit; font-size: inherit; }
.md4-notes-ed .milkdown .milkdown-list-item-block { margin: 2px 0; }
.md4-notes-ed .milkdown .milkdown-list-item-block li { gap: 2px; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .children p { margin: 0; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper { width: 22px; height: 30px; flex: 0 0 auto;
  color: var(--crepe-color-on-background); }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper svg { fill: currentColor; }
/* 글머리 기호 — 중첩 깊이마다 ● ○ ■ 를 돌려 쓴다(Notion·브라우저 기본과 같게). Crepe의 svg 점은 숨기고
   라벨 자리에 CSS로 그린다. 깊이는 조상 목록 블록 수로 가린다(더 깊은 규칙이 더 구체적이라 이긴다). */
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.bullet svg { display: none; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.bullet::before { content: ""; display: block;
  box-sizing: border-box; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 1.4px solid currentColor; border-radius: 50%; background: transparent; width: 6.5px; height: 6.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 0; border-radius: 0; background: currentColor; width: 5.5px; height: 5.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 0; border-radius: 50%; background: currentColor; width: 6px; height: 6px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 1.4px solid currentColor; border-radius: 50%; background: transparent; width: 6.5px; height: 6.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 0; border-radius: 0; background: currentColor; width: 5.5px; height: 5.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 0; border-radius: 50%; background: currentColor; width: 6px; height: 6px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 1.4px solid currentColor; border-radius: 50%; background: transparent; width: 6.5px; height: 6.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block .milkdown-list-item-block li .label-wrapper .label.bullet::before { border: 0; border-radius: 0; background: currentColor; width: 5.5px; height: 5.5px; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label { height: auto; width: auto; padding: 0;
  text-align: center; line-height: 1.5; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.checked,
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.unchecked {
  position: relative; box-sizing: border-box; width: 16px; height: 16px; border: 1.5px solid currentColor;
  border-radius: 3px; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.checked svg,
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.unchecked svg { display: none; }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.checked {
  background: var(--md4n-check); border-color: var(--md4n-check); }
.md4-notes-ed .milkdown .milkdown-list-item-block li .label-wrapper .label.checked::after { content: "";
  position: absolute; left: 3.5px; top: 0.5px; width: 3.5px; height: 7.5px; border: solid #fff;
  border-width: 0 2px 2px 0; transform: rotate(45deg); }
.md4-notes-ed .milkdown li.list-item:has(> .label-wrapper .label.checked) > .children > .content-dom > p {
  color: var(--md4n-muted); text-decoration: line-through; }
.md4-notes-ed .milkdown .ProseMirror blockquote { padding: 0 0 0 14px; margin: 4px 0;
  border-left: 3px solid currentColor; background: none; }
.md4-notes-ed .milkdown .ProseMirror blockquote:before { display: none; }
.md4-notes-ed .milkdown .milkdown-code-block { background: var(--md4n-block-bg); border-radius: 4px;
  padding: 4px 16px 16px; margin: 4px 0; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-editor,
.md4-notes-ed .milkdown .milkdown-code-block .cm-gutters { background: transparent; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-gutters { display: none; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-scroller { font-family: var(--crepe-font-code); font-size: 14px;
  line-height: 1.5; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-content { padding: 0; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-line { padding: 0; }
.md4-notes-ed .milkdown .milkdown-code-block .cm-activeLine { background: transparent; }
.md4-notes-ed .milkdown .milkdown-table-block { margin: 4px 0; }
.md4-notes-ed .milkdown .milkdown-table-block th,
.md4-notes-ed .milkdown .milkdown-table-block td { border: 1px solid var(--md4n-grid); padding: 7px 9px; }
.md4-notes-ed .milkdown .milkdown-table-block th { background: var(--md4n-block-bg); font-weight: 600; text-align: left; }
.md4-notes-ed .milkdown .milkdown-table-block th p,
.md4-notes-ed .milkdown .milkdown-table-block td p { padding: 0; margin: 0; font-size: 0.875em; }
.md4-notes-ed .milkdown .ProseMirror hr { margin: 10px 0; background-color: var(--md4n-rule); }
.md4-notes-ed .milkdown .crepe-placeholder:before { color: var(--md4n-placeholder); }
/* 목차 언어 선택 — 제목 줄 오른쪽의 작은 글자 셋. 고른 것만 파랗게, 나머지는 패널 제목과 같은 회색. */
.md4-toclang .q-btn { min-height: 18px; padding: 0 5px; font-size: 10.5px; font-weight: 500; color: #9a968e; }
.md4-toclang .q-btn.text-primary { color: #2383e2; font-weight: 600; }
.md4-toclang .q-btn:hover { color: #37352f; }
.md4-toclang .q-btn.text-primary:hover { color: #2383e2; }
.md4-canvascol { min-width: 0; flex-wrap: nowrap; }
/* 캔버스 툴바 — 좁아져도 글자를 세로로 꺾지 않는다: 항목은 제 폭을 지키고 줄 전체가 가로로 스크롤 */
.md4-canvasbar { min-height: 36px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }
.md4-canvasbar::-webkit-scrollbar { height: 0; }
.md4-canvasbar > * { flex: 0 0 auto; }
.md4-canvasbar > .q-space { flex: 1 1 0; }
.md4-canvasbar .q-toggle__label, .md4-canvasbar .q-btn__content, .md4-canvasbar .q-badge { white-space: nowrap; }
.md4-exportmenu .q-item { min-height: 36px; }
@media (prefers-color-scheme: dark) {
  .md4-rail { background: #1b1b1b; border-right-color: #333; }
  .md4-notes-ed, .md4-notes-ed .milkdown { background: #1d1d1d; --crepe-color-background: #1d1d1d; }
  .md4-notetab { color: #9a9a9a; }
  .md4-notetab:hover, .md4-notetab.on { background: rgba(255,255,255,.08); color: #e0e0e0; }
  .md4-notes-ed .milkdown { --crepe-color-on-background: #d4d4d4; --crepe-color-inline-code: #ff7369;
    --md4n-muted: #7f7f7f; --md4n-placeholder: rgba(255,255,255,.3); --md4n-underline: rgba(255,255,255,.2);
    --md4n-rule: rgba(255,255,255,.2); --md4n-grid: rgba(255,255,255,.2); --md4n-code-bg: #2f2f2f;
    --md4n-block-bg: #262626; }
  .md4-railbtn { color: #a8a8a8; }
  .md4-railbtn:hover { background: rgba(255,255,255,.08); color: #e0e0e0; }
  .md4-railbtn.on { background: rgba(90,150,230,.22); color: #7bb7ff; }
}
/* 탭 하나 — 위만 둥근 사각형. 비활성은 헤더에 잠긴 색, 활성은 본문과 같은 흰 바닥. */
.md4-tab { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px;
  flex: 0 1 240px; max-width: 240px; min-width: 64px; border-radius: 8px 8px 0 0; cursor: pointer;
  font-size: 13.5px; font-weight: 500; line-height: 1; color: rgba(255,255,255,.96);
  background: rgba(255,255,255,.16); transition: background .12s ease, color .12s ease; }
.md4-tab + .md4-tab { margin-left: 2px; }
.md4-tab:hover { background: rgba(255,255,255,.24); color: #fff; }
.md4-tab.on { background: #fff; color: #37352f; font-weight: 600; cursor: default; }
.md4-tab.on:hover { background: #fff; }
.md4-tab-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.md4-tab-ico { flex: 0 0 auto; opacity: .75; }
.md4-tab.on .md4-tab-ico { opacity: .5; }
.md4-tab-ico.md4-tab-busy { opacity: .9; animation: md4spin 1s linear infinite; }
.md4-jobsbtn { margin-right: 2px; }
.md4-tab-home { flex: 0 0 auto; min-width: 0; }
.md4-tab-home .md4-tab-t { overflow: visible; }
.md4-tabadd { margin: 0 2px 5px; flex: 0 0 auto; }
/* '+' 논문 고르기 목록 — 한 줄 = 한 논문, 호버로 고를 자리를 보여 준다 */
.md4-pick { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px;
  cursor: pointer; }
.md4-pick:hover { background: rgba(35,131,226,.08); }
.md4-pick.here { opacity: .6; cursor: default; }
.md4-spin { animation: md4spin 1s linear infinite; }
@keyframes md4spin { to { transform: rotate(360deg); } }
/* × 는 평소엔 자리만 비워 두고, 그 탭에 마우스가 올라올 때만 보인다 (Notion·브라우저 탭과 같게) */
.md4-tab-x { flex: 0 0 14px; width: 14px; height: 14px; display: inline-flex; align-items: center;
  justify-content: center; border-radius: 4px; font-size: 9px; opacity: 0; transition: opacity .12s ease; }
.md4-tab:hover .md4-tab-x { opacity: .75; }
.md4-tab-x:hover { opacity: 1 !important; background: rgba(0,0,0,.16); }
.md4-tab.on .md4-tab-x:hover { background: rgba(0,0,0,.09); }
.md4-newwin { display: inline-flex; align-items: center; padding: 5px; border-radius: 50%;
  color: #9e9e9e; text-decoration: none; flex: 0 0 auto; }
.md4-newwin:hover { background: rgba(0,0,0,.07); color: #616161; }
.md4-newwin.dark { color: #fff; }
.md4-newwin.dark:hover { background: rgba(255,255,255,.16); }
"""


async def _spawn_window(root: Path, state: dict, title: str) -> None:
    """앱 창 모드에서 이 논문을 형제 앱 창으로 띄운다 (서버는 그대로 하나).

    같은 논문을 두 창에서 고치면 하이라이트·메모는 나중에 저장한 쪽이 앞을 덮으므로 이미 떠 있으면
    띄우지 않는다.
    """
    await _spawn_path(_review_url(root), state, title, dup_note=t("이 논문은 이미 다른 창에 떠 있습니다."))


async def _spawn_path(path: str, state: dict, title: str, *, dup_note: str) -> None:
    """이 서버의 경로 하나를 형제 앱 창으로 띄운다 — 논문 새 창과 에이전트 '새 창으로'가 함께 쓴다.

    프로세스를 띄운 뒤 잠깐 지켜보고 나서 성공을 알린다 — 바로 죽는 경우(그 인터프리터에
    pywebview가 없거나 웹뷰 백엔드가 없는 환경)에 '열었습니다'라고 거짓말하지 않기 위해서다.
    """
    from nicegui import ui

    from md4paper.ui import window as extra_window

    url = f"http://127.0.0.1:{state.get('port') or 8080}{path}"
    if extra_window.already_open(url):
        ui.notify(dup_note, type="info", timeout=2000)
        return
    proc = extra_window.open_new(url, title or "md4paper")
    if proc is None:
        ui.notify(t("새 창을 띄우지 못했습니다 — 앱 창 의존성(pywebview)을 확인하세요."), type="negative")
        return
    await asyncio.sleep(1.2)
    code = extra_window.died(proc)
    if code is None:
        ui.notify(t("새 창에서 열었습니다."), type="positive", timeout=1400)
    else:
        ui.notify(t("새 창이 바로 닫혔습니다 (종료 코드 {code}) — 앱 창 의존성(pywebview)을 확인하세요.", code=code),
                  type="negative", timeout=6000)


def tab_bar(state: dict, here: Path | None, here_title: str = "", tools=None):  # noqa: ANN001, ANN201
    """헤더의 탭 줄 — 맨 앞 '홈' 탭 + 연 논문 탭들(×까지 유지) + '+'(변환된 논문 골라 열기).

    홈·리뷰 두 화면이 같은 탭 줄을 쓴다. here=None이면 홈 탭이 활성이다. 탭은 페이지 이동이고
    (한 창에 한 화면), 나란히 보려면 리뷰 화면의 ⧉로 새 창을 띄운다. 고정(pin)과는 별개다.
    백그라운드 작업(번역 등)이 도는 논문은 탭 아이콘이 돈다. tools()는 탭 줄 끝 단추(고정·새 창)를
    그린다. 반환값은 refreshable — .refresh()로 다시 그린다.
    """
    from nicegui import ui

    here_key = str(Path(here).resolve()) if here is not None else None

    @ui.refreshable
    def bar() -> None:
        busy = {j.root for j in jobs.running()}
        tabs = [str(p) for p in tabstore.load()]
        if here_key is not None and here_key not in tabs:  # 다른 창이 막 닫은 탭 등 — 보는 논문은 늘 보인다
            tabs.append(here_key)
        with ui.row().classes("items-end gap-0 no-wrap md4-tabstrip"):
            _home_chip(active=here_key is None)
            for tv in tabs:
                _chip(here_title if tv == here_key else paper_title(Path(tv)), Path(tv),
                      active=tv == here_key, busy=tv in busy)
        ui.button(icon="add", color=None, on_click=open_picker) \
            .props("flat dense round size=sm text-color=white").classes("md4-tabadd") \
            .tooltip(t("논문 열기 — 변환된 논문을 골라 새 탭으로"))
        with ui.row().classes("items-center gap-0 no-wrap md4-tabtools"):
            jobs_menu()
            if here_key is None:  # 홈 — 에이전트 창 여는 단추 (뷰어는 캔버스 툴바의 forum 단추가 맡는다)
                ui.button(icon="forum", color=None, on_click=lambda: ui.run_javascript(
                    "window.__md4Agent && window.__md4Agent.toggle()")) \
                    .props("flat dense round size=sm text-color=white").classes("md4-agentbtn") \
                    .tooltip(t("에이전트 — 라이브러리의 논문을 찾아 읽고 답합니다"))
            if tools is not None:
                tools()

    def _home_chip(active: bool) -> None:
        tab = ui.element("div").classes("md4-tab md4-tab-home" + (" on" if active else ""))
        with tab:
            ui.icon("home", size="16px").classes("md4-tab-ico")
            ui.label(t("홈")).classes("md4-tab-t")
        tab.tooltip(t("홈 — 업로드 · 논문 목록"))
        if not active:
            tab.on("click", lambda: ui.navigate.to("/home"))

    def _chip(title: str, root: Path, active: bool = False, busy: bool = False) -> None:
        """논문 탭 하나 — 요소를 직접 짓는다.

        Quasar chip으로 그리면 알약 모양·배지 느낌이 나서 '탭'으로 읽히지 않는다. 위쪽만 둥근
        사각형 + 활성 탭은 본문과 같은 흰 바닥이라야 헤더에 물려 있는 탭처럼 보인다.
        """
        tab = ui.element("div").classes("md4-tab" + (" on" if active else ""))
        with tab:
            if busy:
                ui.icon("autorenew", size="14px").classes("md4-tab-ico md4-tab-busy")
            else:
                ui.icon("description", size="14px").classes("md4-tab-ico")
            ui.label(title).classes("md4-tab-t")
            x = ui.element("span").classes("md4-tab-x")
            with x:
                ui.html("&#10005;")
            x.on("click.stop", lambda _, r=root, a=active: close_tab(r, a))
            x.tooltip(t("탭 닫기") + (t(" — 도는 작업은 계속됩니다") if busy else ""))
        tip = title if active else title + "\n" + t("클릭하면 이 논문으로 이동")
        if busy:
            tip += "\n" + t("백그라운드 작업 진행 중")
        tab.tooltip(tip)
        if not active:
            tab.on("click", lambda _, r=root: ui.navigate.to(_review_url(r)))

    def close_tab(root: Path, active: bool) -> None:
        nxt = tabstore.close_tab(root)
        if not active:
            bar.refresh()
            return
        # 보고 있던 탭을 닫으면 이웃 탭으로, 남은 탭이 없으면 홈으로 (작업은 서버에서 계속 돈다)
        ui.navigate.to(_review_url(nxt) if nxt is not None else "/home")

    def open_picker() -> None:
        """'+' — 변환된 논문 목록에서 골라 바로 연다 (홈을 거치지 않고 새 탭으로)."""
        from md4paper.workdir import recent_workdirs

        ws = state.get("upload_dir")
        rows = recent_workdirs(Path(ws), limit=1000) if ws else []
        open_keys = {str(p) for p in tabstore.load()}
        q = {"text": ""}

        def matches() -> list[dict]:
            words = q["text"].lower().split()
            out = []
            for r in rows:
                hay = " ".join(str(r.get(k) or "") for k in ("title", "authors", "year", "venue", "name")).lower()
                if all(w in hay for w in words):
                    out.append(r)
            return out

        def pick(root: Path) -> None:
            dlg.close()
            tabstore.open_tab(root)
            ui.navigate.to(_review_url(root))

        with ui.dialog() as dlg, ui.card().classes("gap-2 q-pa-md").style("width:620px;max-width:94vw"):
            with ui.row().classes("items-center w-full no-wrap"):
                ui.label(t("논문 열기")).classes("text-base font-bold")
                ui.space()
                ui.button(t("새 PDF 올리기"), icon="upload_file", on_click=lambda: ui.navigate.to("/home")) \
                    .props("flat dense no-caps size=sm color=primary").tooltip(t("홈에서 PDF를 올려 변환합니다"))
            search = ui.input(placeholder=t("제목 · 저자 · 연도로 찾기 (Enter = 첫 번째 열기)")) \
                .props("dense outlined autofocus clearable").classes("w-full")

            @ui.refreshable
            def listing() -> None:
                found = matches()
                if not rows:
                    ui.label(t("아직 변환된 논문이 없습니다 — 홈에서 PDF를 올리세요.")).classes("text-sm text-gray-500")
                    return
                if not found:
                    ui.label(t("찾는 논문이 없습니다.")).classes("text-sm text-gray-500")
                    return
                for r in found:
                    key = str(Path(r["root"]).resolve())
                    is_here = key == here_key
                    item = ui.element("div").classes("md4-pick" + (" here" if is_here else ""))
                    with item:
                        ui.icon("push_pin" if r.get("pinned") else "description", size="16px") \
                            .classes("text-primary" if r.get("pinned") else "text-gray-400")
                        with ui.column().classes("gap-0 min-w-0 flex-grow"):
                            ui.label(r["title"]).classes("text-sm truncate w-full")
                            sub = " · ".join(str(x) for x in (r.get("authors"), r.get("year")) if x)
                            if sub:
                                ui.label(sub).classes("text-xs text-gray-500 truncate w-full")
                        if is_here:
                            ui.badge(t("보는 중"), color="grey").props("outline")
                        elif key in open_keys:
                            ui.badge(t("열린 탭"), color="primary").props("outline")
                        if r.get("has_ko"):
                            ui.badge(t("번역"), color="green").props("outline")
                    if is_here:
                        item.on("click", dlg.close)
                    else:
                        item.on("click", lambda _, root=r["root"]: pick(Path(root)))

            def on_search(e) -> None:  # noqa: ANN001
                q["text"] = e.value or ""
                listing.refresh()

            def on_enter() -> None:
                found = [r for r in matches() if str(Path(r["root"]).resolve()) != here_key]
                if found:
                    pick(Path(found[0]["root"]))

            search.on_value_change(on_search)
            search.on("keydown.enter", on_enter)
            with ui.column().classes("w-full gap-0").style("max-height:60vh; overflow-y:auto"):
                listing()
        dlg.open()

    @ui.refreshable
    def jobs_menu() -> None:
        """도는 백그라운드 작업 — 어느 화면에 있든 진행을 한눈에, 눌러서 그 논문으로."""
        active = jobs.running()
        conv = [q for q in state.get("queue", []) if q.get("status") in _QUEUE_ACTIVE]
        if not active and not conv:
            return
        btn = ui.button(color=None).props("flat dense no-caps text-color=white size=sm") \
            .classes("md4-jobsbtn").tooltip(t("백그라운드 작업 진행 중 — 눌러서 보기"))
        with btn:
            ui.icon("autorenew", size="16px").classes("md4-spin")
            ui.label(f"{len(active) + len(conv)}").classes("q-ml-xs")
            with ui.menu().props("anchor='bottom right' self='top right'"):
                for j in active:
                    pct = f" {int(j.fraction * 100)}%" if j.total else ""
                    ui.menu_item(f"{j.label}{pct} · {j.title or Path(j.root).stem}",
                                 on_click=lambda _, r=j.root: ui.navigate.to(_review_url(Path(r))))
                for q in conv:
                    ui.menu_item(t("변환 · {name}", name=q['name']), on_click=lambda: ui.navigate.to("/home"))

    # 탭 아이콘·작업 목록은 다른 논문의 작업 상태도 비추므로, 무엇이 도는지 바뀔 때만 다시 그린다
    sig_state: dict = {"v": None, "watch": []}

    def poll() -> None:
        # 다른 논문의 작업이 끝나면 여기서 알린다 (지금 보는 논문 것은 각 화면이 붙어서 알린다)
        for j in sig_state["watch"]:
            if not j.running and not j.seen and j.root != here_key:
                j.seen = True
                name = j.title or Path(j.root).stem
                if j.status == "done":
                    ui.notify(f"‘{name}’ — {j.message or t('{label} 완료', label=j.label)}", type="positive")
                else:
                    ui.notify(t("‘{name}’ {label} 실패: {error}", name=name, label=j.label, error=j.error), type="negative")
        sig_state["watch"] = jobs.running()
        sig = (tuple(sorted((j.root, j.kind) for j in sig_state["watch"])),
               sum(1 for q in state.get("queue", []) if q.get("status") in _QUEUE_ACTIVE))
        if sig != sig_state["v"]:
            first = sig_state["v"] is None
            sig_state["v"] = sig
            if not first:
                bar.refresh()

    ui.timer(1.0, poll)
    bar()
    return bar


def new_window_button(root: Path, state: dict, title: str = "", *,
                      dark: bool = False, small: bool = False) -> None:
    """이 논문을 새 창으로 여는 단추 — 논문 둘을 나란히 놓고 볼 때. dark는 헤더(파란 배경)용.

    앱 창 모드에서는 웹뷰 프로세스를 하나 더 띄운다(§ui/window.py). 브라우저 모드에서는 새 탭인데,
    서버를 왕복해 window.open을 부르면 사용자 제스처가 끊겨 팝업 차단에 걸리므로 **진짜 링크**
    (`<a target="_blank">`)를 그린다 — 브라우저가 자기 방식으로 새 탭·새 창을 연다.
    """
    from nicegui import ui

    tip = t("새 창으로 열기 — 두 논문을 나란히 볼 때")
    if desktop.active():
        ui.button(icon="open_in_new", color=None,
                  on_click=lambda: _spawn_window(root, state, title)) \
            .props("flat dense round " + ("text-color=white" if dark else "color=grey")
                   + (" size=sm" if small else "")).tooltip(tip)
        return
    link = ui.link(target=_review_url(root), new_tab=True) \
        .classes("md4-newwin" + (" dark" if dark else ""))
    with link:
        ui.icon("open_in_new", size="17px" if small else "20px")
    link.tooltip(tip)


def _relative_time(ts: float) -> str:
    """수정 시각 → '방금 전 / N분 전 / N시간 전 / N일 전'."""
    diff = max(0, int(time.time() - ts))
    if diff < 60:
        return t("방금 전")
    if diff < 3600:
        return t("{n}분 전", n=diff // 60)
    if diff < 86400:
        return t("{n}시간 전", n=diff // 3600)
    return t("{n}일 전", n=diff // 86400)


async def open_source_pdf(ctrl: UIController) -> None:
    """원본 PDF를 운영체제 PDF 뷰어(미리보기 등)로 따로 연다 — 논문 화면 헤더와 홈 미리보기의 PDF 단추."""
    from nicegui import run, ui

    pdf = ctrl.source_pdf()
    if not pdf or not Path(pdf).is_file():
        ui.notify(t("원본 PDF를 찾지 못했습니다 (마크다운에서 변환한 논문일 수 있어요)."), type="warning")
        return
    if not await run.io_bound(desktop.open_file, Path(pdf)):
        ui.notify(t("PDF 뷰어를 열지 못했습니다."), type="negative")


def build(ctrl: UIController, state: dict | None = None) -> None:
    """현재 페이지 컨텍스트에 UI를 구성한다. state는 서버 수명 상태(작업 폴더·포트 — 헤더 탭용)."""
    from nicegui import ui

    state = state if state is not None else {}
    m = ctrl.manifest
    tok = wd_token(ctrl.wd.root)  # 이미지·PDF URL에 넣는 논문별 토큰 (캐시 충돌 방지)
    _title = m.title or ctrl.wd.root.name

    spa.add_css(_CHROME_CSS)
    find_bar.install()  # Cmd/Ctrl+F 페이지 찾기 — 앱 창에는 브라우저 찾기 바가 없다
    pdf_zoom.install()  # PDF 칸 핀치·Ctrl+휠 확대 — 작은 글씨·그림 속 숫자 보기
    scroll_memory.install(tok)  # 읽던 자리(단계별 스크롤) 기억 — 탭으로 오가도 그 자리로

    async def open_pdf_app() -> None:
        await open_source_pdf(ctrl)

    def review_tools() -> None:
        # 원본 PDF 열기는 툴바 '내보내기' 옆에 있다 — 탭 줄 끝에 두면 탭 수에 따라 자리가 옮겨 다니고,
        # 툴바의 'PDF'(앱 안 PDF 칸) 토글과 같은 이름의 단추가 둘이 되어 헷갈렸다.
        new_window_button(ctrl.wd.root, state, _title, dark=True, small=True)

    # 헤더는 한 줄이다 — 논문 탭은 아래 끝에 물려(활성 탭이 본문 흰 바닥과 맞닿아 '탭'으로 읽힌다),
    # 오른쪽 끝은 이 논문의 상태 칩(변환됨 · 번역 안 됨 → 번역하기). 옛 '1·변환 / 2·번역 / 3·뷰어'
    # 단계 탭이 있던 자리다 — 화면은 이제 하나(캔버스 + 왼쪽 작업 패널)라 단계를 오갈 일이 없고,
    # 번호가 가르쳐 주던 '다음 할 일'은 이 칩이 대신한다.
    with spa.header():
        paper_tabs = tab_bar(state, ctrl.wd.root, _title, tools=review_tools)
        ui.space()
        chip_slot = ui.row().classes("items-center no-wrap md4-chipslot")  # 상태 칩은 아래에서 채운다

    # 화면은 하나의 캔버스다 — 원문|번역|PDF 그리드(또는 편집기|프리뷰)와 목차가 같은 en.md/ko.md를
    # 읽으므로, 문서가 바뀌면 이 둘을 함께 다시 그린다. (옛 변환 프리뷰·번역 결과·뷰어가 각자 그리던
    # 것을 하나로 합쳤다 — '어느 쪽만 갱신돼 어긋나는' 일이 구조적으로 없다.)
    def refresh_canvas() -> None:
        _rows_memo.clear()  # 구조(섹션맵·쪽)는 본문 글자가 그대로여도 바뀔 수 있다 → 정렬 행을 다시 센다
        canvas.refresh()
        toc_panel.refresh()

    _translate_refs: dict = {}  # 번역 패널 위젯 참조 (구조 패널 레벨 변경 시 번역 트리 갱신용)

    def _refresh_translate_tree() -> None:
        picker = _translate_refs.get("picker")
        if picker is not None:  # 레벨/구조가 바뀌면 번역 패널의 섹션 트리도 다시 그린다
            picker.refresh()

    def commit() -> None:
        """구조/설정 변경을 자동 저장 + 재조립 (별도 저장 버튼 없이 항상 최신).

        직접 고친 en.md가 있으면 재조립이 그 편집을 살려 새 구조에 합친다(§assemble/merge) — 예전엔 레벨
        하나만 바꿔도 편집이 통째로 사라져 변경 앞에 확인 창을 걸었다. 이제 묻지 않고 합친 결과만 알린다."""
        edit_flush()  # 기다리던 편집부터 파일에 — 늦게 저장되면 합친 결과를 옛 글로 덮는다
        r = ctrl.save_and_reassemble()
        library.auto_export(ctrl.wd)  # 저장 위치를 지정했으면 그 폴더의 사본도 최신으로
        if r["kept"] or r["dropped"]:
            _notify_merge(r)

    def _notify_merge(r: dict) -> None:
        parts = [t("직접 고친 내용을 그대로 두고 다시 만들었습니다.")]
        if r["conflicts"]:
            parts.append(t("이번 변경과 같은 자리를 고친 {n}곳은 고친 내용을 남겼습니다"
                           " — 그 자리엔 새 구조가 반영되지 않았을 수 있어요.", n=r["conflicts"]))
        if r["dropped"]:
            parts.append(t("삭제·합침으로 없어진 구간에 있던 편집 {n}곳도 함께 빠졌습니다.", n=r["dropped"]))
        if r["backup"]:
            parts.append(t("고치기 전 글은 logs/{name}에 남겼습니다.", name=r["backup"].name))
        ui.notify(" ".join(parts), type="warning" if (r["conflicts"] or r["dropped"]) else "positive",
                  multi_line=len(parts) > 1, timeout=8000 if len(parts) > 1 else 4000)

    def change_level(sid: str, value) -> None:  # noqa: ANN001
        """레벨 변경 → 자동 저장 + 즉시 프리뷰·트리에 반영."""
        def apply() -> None:
            ctrl.set_level(sid, value)
            commit()
            section_list.refresh()
            _refresh_translate_tree()
            refresh_canvas()  # 그리드·목차(편집 중이면 에디터도) 새로 만들어진 en.md로
        apply()

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
                        tip = t("클릭하면 마크다운의 이 섹션으로 이동") + "\n" + tip
                    if tip.strip():
                        lbl.tooltip(tip.strip())
                    # 배지 — 항상 보임 (제목/run-in/기억됨/확인)
                    if sec.is_title:
                        ui.badge(t("제목"), color="purple").props("outline")
                    if sec.runin:
                        ui.badge("run-in", color="blue-4").props("outline")
                    if sec.auto_level is not None and sec.level != sec.auto_level:
                        ui.badge(t("기억됨"), color="green").props("outline").tooltip(
                            t("자동값 {auto} → 사용자 선택 {level} (이름으로 기억)",
                              auto=t(_LEVEL_LABEL.get(sec.auto_level, str(sec.auto_level))),
                              level=t(_LEVEL_LABEL.get(sec.level, str(sec.level)))))
                    if sec.needs_review:
                        ui.badge(t("확인"), color="orange").props("outline")
                    # 섹션 라벨을 클릭하면 마크다운 그 위치로 이동(나란히 보기면 PDF도 따라옴).
                    # 별도 PDF 이동 버튼은 두지 않는다.

    _GROUP_NAME = {
        ("title", 0): N_("제목"),
        ("dotted-arabic", 1): N_("숫자 1단계"), ("dotted-arabic", 2): N_("숫자 2단계"),
        ("dotted-arabic", 3): N_("숫자 3단계"), ("dotted-arabic", 4): N_("숫자 4단계"),
        ("roman", 1): N_("로마 숫자"), ("letter", 1): N_("문자"), ("letter", 2): N_("문자 2단계"),
        ("unnumbered", 1): N_("무번호"), ("none", 1): N_("번호 없음"), ("run-in", 0): N_("run-in 헤더"),
    }

    @ui.refreshable
    def batch_level_panel() -> None:
        """같은 모양의 헤더를 묶어 한 번에 조정 — 선택지는 섹션 트리와 동일."""
        groups = ctrl.level_groups()
        if not groups:
            ui.label(t("조정할 헤더가 없습니다.")).classes("text-xs text-gray-400")
            return
        ui.label(t("같은 모양의 헤더를 한 번에 바꿉니다. 펼치면 그룹의 각 섹션을 개별 조정할 수 있어요.")) \
            .classes("text-xs text-gray-500")

        def apply_group(key: str, name: str, value) -> None:  # noqa: ANN001
            if value is None:
                return

            def apply() -> None:
                n = ctrl.apply_group_level(key, value)
                commit()
                section_list.refresh()
                batch_level_panel.refresh()
                _refresh_translate_tree()
                refresh_canvas()
                ui.notify(t("{name} {n}개 → {level}", name=name, n=n, level=t(_LEVEL_LABEL.get(value, str(value)))), type="positive")

            apply()

        def change_in_group(sid: str, value) -> None:  # noqa: ANN001
            """그룹 내 개별 섹션 레벨 변경 → 프리뷰·섹션 트리·번역 트리 연동 (아코디언은 유지)."""
            def apply() -> None:
                ctrl.set_level(sid, value)
                commit()
                section_list.refresh()
                _refresh_translate_tree()
                refresh_canvas()

            apply()

        for g in groups:
            gk = (g["scheme"], g["depth"])
            name = t(_GROUP_NAME[gk]) if gk in _GROUP_NAME else t("{scheme} {depth}단계", scheme=g["scheme"], depth=g["depth"])
            opts = _level_opts(g["scheme"] == "run-in")
            group_secs = [s for s in m.sections if ctrl.group_key(s) == (g["scheme"], g["depth"])]
            # 일괄 선택은 접힌 상태에서도 쓰도록 아코디언 헤더 옆(왼쪽)에 둔다. 펼치면 개별 섹션.
            with ui.row().classes("items-start gap-2 w-full no-wrap"):
                ui.select(
                    opts, value=g["current"],
                    on_change=lambda e, k=g["key"], nm=name: apply_group(k, nm, e.value),
                ).props("dense outlined").classes("w-28 shrink-0 md4-lvl").tooltip(t("이 그룹 전체를 한 번에"))
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
                                    .tooltip(t("클릭하면 마크다운의 이 섹션으로 이동"))

    spa.add_css(i18n.html(_PREVIEW_CSS))
    spa.add_css(live_preview.CSS)  # 편집기 라이브 프리뷰(기호 숨기기·헤더·그림·표·수식)
    # 인용·각주 호버 툴팁: 맵 주입 → 툴팁 요소/스크립트 (같은 툴팁 UI를 #ref-·#fn- 둘 다 씀)
    spa.add_body_html(f"<script>{cite_tips_js(ctrl.citation_tooltips())}</script>")
    spa.add_body_html(f"<script>{fn_tips_js(ctrl.footnote_tooltips())}</script>")
    spa.add_body_html(f"<script>{fig_alts_js(ctrl.figure_alts())}</script>")  # 확대 뷰어의 그림 설명
    spa.add_body_html(_CITE_TIP_HTML)
    spa.add_body_html(_SEC_JUMP_HTML)  # 마크다운 헤더 ⚙ 클릭 → 섹션 트리 항목 스크롤+하이라이트
    spa.add_body_html(i18n.html(_IMG_ZOOM_HTML))  # 본문 그림 클릭 → 확대 뷰어
    spa.add_body_html(_EXTLINK_HTML)  # 바깥 링크는 새 창(앱 창이면 시스템 브라우저)으로
    spa.add_body_html(_ARROW_HTML)  # -> → · <- ← · <-> ↔ · => ⇒ (메모·하이라이트 메모·마크다운 편집기)
    # 본문 그림·표 참조 호버 → 썸네일, 클릭 → 확대 (그림은 위 확대 뷰어, 마크다운 표는 표 오버레이)
    spa.add_css(figrefs.CSS)
    fig_js = figrefs.index_js(served_markdown(ctrl.en_markdown() or "", tok),
                              served_markdown(ctrl.ko_markdown() or "", tok))
    spa.add_body_html(f"<script>{fig_js}</script>")
    spa.add_body_html(figrefs.HTML)

    def zoomed_file(e) -> Path | None:  # noqa: ANN001 — GenericEventArguments
        """확대 뷰어가 넘긴 `/wdimages/<토큰>/<파일>` → 이 논문의 out/images 안 실제 파일."""
        from urllib.parse import unquote

        parts = str((e.args or {}).get("path", "")).split("/")
        if len(parts) != 4 or parts[1] != "wdimages" or parts[2] != tok:
            return None
        base = ctrl.wd.out_images.resolve()
        target = (base / unquote(parts[3])).resolve()
        return target if target.is_relative_to(base) and target.is_file() else None

    async def save_zoomed(e) -> None:  # noqa: ANN001
        f = zoomed_file(e)
        if f is None:
            ui.notify(t("그림 파일을 찾지 못했습니다."), type="warning")
            return
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
            f.suffix.lower(), "application/octet-stream")
        # 논문 이름을 앞에 — 다운로드 폴더에서 figure-1.png가 여러 논문 것으로 섞이지 않게
        await desktop.deliver(f"{ctrl.wd.root.stem}_{f.name}", f.read_bytes(), media)

    async def copy_zoomed(e) -> None:  # noqa: ANN001
        from nicegui import run

        f = zoomed_file(e)
        if f is not None and await run.io_bound(desktop.copy_image, f):
            await ui.run_javascript("window.__mdImgCopied && window.__mdImgCopied()")
        else:
            ui.notify(t("그림을 클립보드에 넣지 못했습니다 — 저장 버튼으로 받아 주세요."), type="warning")

    ui.on("md4-img-save", save_zoomed)
    ui.on("md4-img-copy", copy_zoomed)
    spa.add_css(_ANNO_CSS)
    spa.add_css(term_examples.CSS)  # 용어집 시트의 '이 용어가 쓰인 문장'
    spa.add_body_html(f"<script>{anno_items_js(annotations.load(ctrl.wd), tok)}</script>")
    spa.add_body_html(i18n.html(_ANNO_HTML))  # 드래그 → 하이라이트 · 메모 + 문장 짝 호버
    spa.add_css(formula_pop.CSS)  # 수식 클릭 → 원본 크롭 대조 · 다시 생성
    spa.add_body_html(f"<script>{formula_pop.init_js(tok)}</script>")
    spa.add_body_html(formula_pop.HTML)
    spa.add_css(figure_crop.CSS)  # 확대 뷰어 ✂ → 그림 영역 다시 지정 (위 영역 지정 화면을 같이 쓴다)
    spa.add_body_html(figure_crop.HTML)
    spa.add_css(figure_ko.CSS)  # 확대 뷰어 '가' → 그림 속 글자 번역 (번역 그림 보기·만들기·고치기)
    spa.add_body_html(figure_ko.HTML)

    # 캔버스를 다시 지으면(그리드를 통째로) 스크롤이 맨 위로 간다. 그림·수식 하나 고쳤을 뿐인데 읽던 자리를
    # 잃으면 안 되므로, 다시 짓기 전에 화면 맨 위에 걸친 행과 그 행의 위치를 재 두고, 새 그리드가 붙으면
    # 같은 행을 같은 자리로 되돌린다. 그림이 늦게 실려 높이가 바뀌므로 잠깐 몇 번 더 맞춘다(사용자가
    # 직접 스크롤하면 거기서 멈춘다).
    _KEEP_JS = """
    (function(){
      var g = document.querySelector('.sbs-grid') || document.querySelector('.vpane');
      if (!g) return null;
      g.dataset.md4old = '1';
      var top = g.getBoundingClientRect().top, cells = g.querySelectorAll('.sbs-cell[data-row]');
      for (var i = 0; i < cells.length; i++){
        var r = cells[i].getBoundingClientRect();
        if (r.bottom > top + 1) return {row: cells[i].getAttribute('data-row'), off: r.top - top,
                                         f: r.height > 0 ? Math.min(1, Math.max(0, (top - r.top) / r.height)) : 0,
                                         ratio: g.scrollTop / Math.max(1, g.scrollHeight - g.clientHeight)};
      }
      return {row: null, off: 0, ratio: g.scrollTop / Math.max(1, g.scrollHeight - g.clientHeight)};
    })()
    """
    _RESTORE_JS = """
    (function(p){
      var stop = false, n = 0, t0 = Date.now();
      function quit(){ stop = true; }
      ['wheel', 'touchmove', 'keydown', 'mousedown'].forEach(function(ev){
        window.addEventListener(ev, quit, {once: true, capture: true}); });
      (function step(){
        if (stop || Date.now() - t0 > 3000) return;
        var g = [].filter.call(document.querySelectorAll('.sbs-grid, .vpane'),
                               function(x){ return !x.dataset.md4old; })[0];
        var c = g && p.row !== null ? g.querySelector('.sbs-cell[data-row="' + p.row + '"]') : null;
        if (g && c){
          // f(행 안의 비율)가 있으면 그것으로 — 편집기에서 돌아올 때는 픽셀 차가 아니라 행 안 비율만 안다
          var cr = c.getBoundingClientRect(), off = p.f != null ? -p.f * cr.height : p.off;
          var d = cr.top - g.getBoundingClientRect().top - off;
          if (Math.abs(d) > 1){ g.scrollTop += d; g.dispatchEvent(new Event('scroll')); }
        } else if (g && p.row === null){
          g.scrollTop = p.ratio * (g.scrollHeight - g.clientHeight);
        }
        setTimeout(step, ++n < 8 ? 60 : 250);   // 처음엔 촘촘히, 그다음엔 그림이 실리는 동안 띄엄띄엄
      })();
    })(%s)
    """

    # 편집기 맨 위 줄 — {line(0-based), f(그 줄 블록 안 비율)}. 문단이 한 줄이라 f가 문단 안 위치다.
    _EDITOR_PLACE_JS = """
    (function(id){
      var h = (typeof getElement === 'function') ? getElement(id) : null, v = h && h.editor;
      if (!v) return null;
      var y = v.scrollDOM.getBoundingClientRect().top - v.documentTop, b = v.lineBlockAtHeight(y);
      return {line: v.state.doc.lineAt(b.from).number - 1,
              f: b.height > 0 ? Math.min(1, Math.max(0, (y - b.top) / b.height)) : 0};
    })(%d)
    """
    # 편집기를 그 줄(+줄 블록 안 비율)이 맨 위에 오게. 서식 보기가 붙어 줄 높이가 바뀐 뒤에 맞추고(그 전에 맞추면
    # 그림·표 위젯이 들어서며 밀린다), CM이 아직 그리지 않은 곳의 높이는 추정이라 몇 번 더 재서 맞춘다.
    _EDITOR_GOTO_JS = """
    (function(id, line, f){
      var tries = 0, t0 = Date.now(), stop = false;
      ['wheel', 'touchmove', 'keydown', 'mousedown'].forEach(function(ev){
        window.addEventListener(ev, function(){ stop = true; }, {once: true, capture: true}); });
      (function go(){
        var h = (typeof getElement === 'function') ? getElement(id) : null, v = h && h.editor;
        if (!v || (!h.__md4lp && Date.now() - t0 < 1500)){ if (++tries < 100) setTimeout(go, 50); return; }
        var d = v.state.doc, pos = d.line(Math.min(d.lines, line + 1)).from, n = 0;
        (function fix(){
          if (stop || !v.dom.isConnected) return;
          var b = v.lineBlockAt(pos), sc = v.scrollDOM;
          var dy = (v.documentTop + b.top + f * b.height) - sc.getBoundingClientRect().top;
          if (Math.abs(dy) > 1){ sc.scrollTop += dy; sc.dispatchEvent(new Event('scroll')); }
          if (++n < 10) setTimeout(fix, n < 5 ? 60 : 200);
        })();
      })();
    })(%d, %d, %s)
    """

    async def refresh_canvas_keeping_place() -> None:
        """캔버스를 다시 그리되 보던 자리(화면 맨 위의 행)를 지킨다 — 그림·수식 고치기 뒤에."""
        try:
            pos = await ui.run_javascript(_KEEP_JS, timeout=2.0)
        except Exception:  # noqa: BLE001 — 못 재면 그냥 다시 그린다
            pos = None
        refresh_canvas()
        if isinstance(pos, dict):
            ui.run_javascript(_RESTORE_JS % json.dumps(pos))

    async def on_figure_recropped(e) -> None:  # noqa: ANN001 — 그림 파일은 이미 바뀌었다(서버 라우트)
        a = e.args or {}
        if not a.get("ok"):
            ui.notify(t("그림을 바꾸지 못했습니다: {v}", v=a.get('error', '')), type="warning")
            return
        await refresh_canvas_keeping_place()  # 표시 폭 힌트(#mdw=)를 새 그림 크기로 — 읽던 자리는 그대로
        ui.notify(t("원래 그림으로 되돌렸습니다") if a.get("what") == "restore" else t("그림을 고른 영역으로 바꿨습니다"),
                  type="positive")

    ui.on("md4-fig-recropped", on_figure_recropped)

    async def on_figure_ko_changed(e) -> None:  # noqa: ANN001 — 번역 그림·ko.md는 이미 바뀌었다(서버 라우트)
        a = e.args or {}
        if not a.get("ok"):
            ui.notify(t("그림 글자를 번역하지 못했습니다: {v}", v=a.get('error', '')), type="warning")
            return
        await refresh_canvas_keeping_place()  # 번역 열의 그림 참조(.ko.png)가 바뀌었다
        what = a.get("what")
        if what == "remove":
            ui.notify(t("번역 그림을 지웠습니다 — 번역 열은 원본 그림으로"), type="positive")
        elif what == "edit":
            ui.notify(t("고친 번역으로 그림을 다시 그렸습니다"), type="positive")
        elif a.get("translated"):
            ui.notify(t("그림 글자 {translated}줄을 번역했습니다 (≈${v:.4f})", translated=a['translated'], v=float(a.get('cost_usd') or 0)),
                      type="positive")
        else:
            ui.notify(t("이 그림에서 번역할 글자를 찾지 못했습니다"), type="info")

    ui.on("md4-figko-changed", on_figure_ko_changed)

    async def on_formula_apply(e) -> None:  # noqa: ANN001 — 팝오버의 '적용'
        from nicegui import run

        a = e.args or {}
        try:
            res = await run.io_bound(ctrl.apply_formula, str(a.get("id", "")),
                                     str(a.get("latex", "")), str(a.get("kind", "")),
                                     a.get("page"), a.get("rect"), a.get("expect"))
        except ValueError as err:
            ui.notify(t("수식을 반영하지 못했습니다: {err}", err=err), type="warning")
            await ui.run_javascript("window.__md4FxClose && window.__md4FxClose()")
            return
        await run.io_bound(library.auto_export, ctrl.wd)
        ui.run_javascript("window.__md4FxClose && window.__md4FxClose()")
        await refresh_canvas_keeping_place()
        msg = t("수식을 바꿨습니다")
        if ctrl.ko_markdown():
            msg += t(" · 번역문도 같은 자리를 바꿨습니다") if res["ko"] else t(" · 번역은 다시 번역해야 반영됩니다")
        ui.notify(msg, type="positive")

    ui.on("md4-fx-apply", on_formula_apply)
    spa.add_body_html(f"<script>{agent_panel.init_js(tok)}</script>")  # 에이전트 창(셸에 붙음)에 지금 논문을 알린다

    def push_cite_tips() -> None:
        """참고문헌을 (재)파싱한 뒤 클라이언트 툴팁 맵을 갱신한다."""
        ui.run_javascript(cite_tips_js(ctrl.citation_tooltips()))

    # 편집은 보기 모드가 아니라 캔버스의 모디파이어다 — 켜면 원문|번역|PDF 그리드가 접히고 그 자리에
    # 편집기|프리뷰가 선다(정렬 그리드는 행 단위 셀이라 그 안에서 편집할 수 없고, 프리뷰는 타이핑마다
    # 바뀐 블록만 갈아 끼워야 해서 통째로 다시 짓는 그리드와 성격이 다르다). 끄면 그리드로 돌아간다.
    # lang: 어느 쪽을 고치는지 — 'en'(원문 en.md) | 'ko'(번역 ko.md). 번역문도 같은 편집기로 손본다.
    edit_state: dict = {"on": False, "editor": None, "live": True, "lang": "en",
                        "pending": None, "timer": None, "status": None,  # pending: (lang, 글) 자동 저장 대기
                        "lp": _live_preview_on()}  # lp: 서식 보기(라이브 프리뷰) — 전역 설정에 기억

    def edit_side() -> str:
        """편집 중 오른쪽 칸 — "pdf"(원본 PDF, 기본) 또는 "preview"(렌더 미리보기). PDF가 없으면 미리보기.
        편집기가 '서식 보기'로 이미 문서처럼 보이니, 옆에는 렌더 결과보다 원본과 대조할 PDF가 더 쓸모 있다."""
        if ctrl.pdf_page_count() <= 0:
            return "preview"
        v = config.load_config().get("ui", {}).get("edit_side", "pdf")
        return v if v in ("pdf", "preview") else "pdf"

    def set_edit_side(side: str) -> None:
        edit_flush()
        config.set_section_value("ui", "edit_side", side)
        viewer_toolbar.refresh()
        canvas.refresh()

    def set_live_preview(on: bool) -> None:
        edit_state["lp"] = bool(on)
        config.set_section_value("ui", "md_live_preview", bool(on))
        ed = edit_state.get("editor")
        if ed is not None:  # 다시 그리지 않고 켜고 끄기만 — 커서·스크롤이 그대로
            ui.run_javascript(live_preview.live_preview_js(ed.id, tok, bool(on)))

    def edit_flush() -> None:
        """기다리던 마크다운 편집을 바로 파일에 — 타자가 멈췄을 때, 편집을 끄거나 대상을 바꾸거나 떠날 때."""
        tv = edit_state.get("timer")
        if tv is not None:
            tv.cancel()
            edit_state["timer"] = None
        pending = edit_state.get("pending")
        if pending is None:
            return
        edit_state["pending"] = None
        lang, text = pending
        first = not (ctrl.has_manual_edit() if lang == "en" else ctrl.has_manual_edit_ko())
        try:
            if lang == "en":
                ctrl.save_en_markdown(text)
            else:
                ctrl.save_ko_markdown(text)
        except OSError as ex:
            _edit_status(t("저장 실패: {ex}", ex=ex))
            return
        _edit_status(t("자동 저장됨"))
        if not edit_state["live"] and edit_state["on"]:
            preview_pane.refresh()   # 실시간 반영을 꺼 뒀으면 저장할 때 프리뷰를 맞춘다
        if lang == "en":
            refresh_staleness()  # 고친 섹션은 번역 패널에 '원문 바뀜'으로
        status_chip.refresh()
        toc_panel.refresh()      # 헤더를 고쳤을 수 있다
        if first:
            edit_toggle.refresh()  # '수동 편집됨' 배지

    def _edit_status(text: str) -> None:
        lbl = edit_state.get("status")
        if lbl is not None and not lbl.is_deleted:
            lbl.set_text(text)

    ui.context.client.on_disconnect(edit_flush)  # 창을 닫거나 다른 화면으로 가도 마지막 글자까지

    def preview_markdown(md: str | None = None) -> str:
        """프리뷰에 넣을 마크다운 — 이미지 경로 치환 + (원문이면) 헤더 줄 끝 앵커(sec-ID·data-page).

        md를 주면 파일 대신 그 텍스트를 쓴다(타이핑 중 라이브 갱신용). 번역문은 섹션맵(en 기준 라인)과
        줄이 맞지 않으므로 앵커 없이 그대로 보여 준다.
        """
        if md is None:
            md = ctrl.en_markdown() if edit_state["lang"] == "en" else ctrl.ko_markdown()
        if not md:
            return ""
        if edit_state["lang"] != "en":
            return served_markdown(md, tok, ctrl.wd.out_images)
        page_by_id = ctrl.section_pages()
        return anchored_markdown(served_markdown(md, tok, ctrl.wd.out_images), ctrl.section_map(), page_by_id, jump=True)

    async def toggle_edit(on: bool, lang: str | None = None) -> None:
        """직접 편집 켜기/끄기(+대상 언어). 편집 중 화면은 편집기 | (PDF 또는 미리보기) 두 칸이다 —
        3분할은 기본 창(1280)에서 260/227/162px라 읽을 수 없다. 읽기 모드의 패널 구성은 건드리지 않는다.

        보던 자리를 지킨다 — 바꾸기 전에 화면 맨 위의 정렬 행과 그 행 안의 비율을 재 두고(읽기 모드: 그리드,
        편집 모드: 편집기의 줄을 행으로 바꿔), 새 화면이 붙으면 같은 행의 같은 비율로 옮긴다(row_lines)."""
        edit_flush()  # 끄거나 대상을 바꾸기 전에 기다리던 편집부터 저장
        place = None
        try:
            if edit_state["on"] and edit_state.get("editor") is not None:
                at = await ui.run_javascript(_EDITOR_PLACE_JS % edit_state["editor"].id, timeout=2.0)
                if isinstance(at, dict):
                    place = _line_to_place(int(at["line"]), float(at["f"]), edit_state["lang"])
            elif not edit_state["on"]:
                at = await ui.run_javascript(_KEEP_JS, timeout=2.0)
                if isinstance(at, dict) and at.get("row") is not None:
                    place = (int(at["row"]), float(at.get("f") or 0))
        except Exception:  # noqa: BLE001 — 못 재면 자리 없이 바꾼다
            place = None
        if lang is not None:
            edit_state["lang"] = lang
        edit_state["on"] = on
        viewer_state["_place"] = place
        viewer_toolbar.refresh()
        canvas.refresh()

    def row_lines(lang: str) -> list[tuple[int, int]]:
        """정렬 행 → 그 언어 마크다운에서 행이 차지하는 (첫 줄, 줄 수). 0-based.

        화면용 글(이미지 주소 바꾸기·수식 표식)은 줄을 더하거나 빼지 않아서 여기서 센 줄이 편집기의 줄과 같다.
        행의 첫 줄을 앞에서부터 차례로 찾는다. 번역은 원문과 정렬됐을 때만(행이 번역 블록을 가질 때만)."""
        info = layout_rows()
        text = info.get(lang) or ""
        if not text or (lang == "ko" and not info["aligned"]):
            return []
        lines = text.splitlines()
        out: list[tuple[int, int]] = []
        cur = 0
        for en_b, ko_b, _h in info["rows"]:
            b = (en_b if lang == "en" else ko_b) or ""
            first = next((x for x in b.split("\n") if x.strip()), None)
            if first is not None:
                hit = next((j for j in range(cur, len(lines)) if lines[j] == first), None)
                if hit is not None:
                    cur = hit
            out.append((cur, max(1, len(b.strip("\n").split("\n")))))
        return out

    def _place_to_line(place: tuple[int, float], lang: str) -> tuple[int, float] | None:
        """(행, 행 안 비율) → (편집기 줄, 줄 블록 안 비율). 문단 하나가 한 줄이라 대개 그 줄 안의 비율이 된다."""
        rl = row_lines(lang)
        k, f = place
        if not (0 <= k < len(rl)):
            return None
        start, n = rl[k]
        x = start + min(max(f, 0.0), 0.999) * n
        return int(x), x - int(x)

    def _line_to_place(line: int, frac: float, lang: str) -> tuple[int, float] | None:
        rl = row_lines(lang)
        if not rl:
            return None
        k = max((i for i, (st, _n) in enumerate(rl) if st <= line), default=0)
        start, n = rl[k]
        return k, min(1.0, max(0.0, (line + frac - start) / n))

    @ui.refreshable
    def edit_toggle() -> None:
        """툴바의 마크다운 편집 스위치 — 켜면 대상(원문/번역) 선택·실시간 반영 스위치가 따라 나온다."""
        has_ko = bool(ctrl.ko_markdown())
        ui.switch(t("마크다운 편집"), value=edit_state["on"],
                  on_change=lambda e: toggle_edit(e.value)).props("dense") \
            .tooltip(t("켜면 편집기 옆에 원본 PDF(또는 미리보기)가 서고, 편집기를 스크롤하면 따라옵니다"))
        if edit_state["on"]:
            if has_ko:
                ui.toggle({"en": t("원문"), "ko": t("번역")}, value=edit_state["lang"],
                          on_change=lambda e: toggle_edit(True, e.value)) \
                    .props("dense no-caps unelevated toggle-color=primary color=grey-3 text-color=grey-8") \
                    .classes("conv-viewtoggle").tooltip(t("어느 쪽 마크다운을 고칠지"))
            ui.switch(t("서식 보기"), value=edit_state["lp"],
                      on_change=lambda e: set_live_preview(e.value)).props("dense") \
                .tooltip(t("켜면 편집기가 문서처럼 보입니다 — #·** 같은 기호와 링크 주소는 커서가 있는 줄에서만 보이고, "
                         "그림·표·수식은 그 자리에 그려집니다. 끄면 마크다운 원문 그대로"))
            if edit_side() == "preview":  # 옆이 PDF면 갱신할 미리보기가 없다
                ui.switch(t("실시간 반영"), value=edit_state["live"],
                          on_change=lambda e: edit_state.update(live=e.value)).props("dense") \
                    .tooltip(t("끄면 프리뷰는 입력을 멈춰 자동 저장될 때만 갱신됩니다 (긴 문서에서 가볍게)"))
        if ctrl.has_manual_edit():
            ui.badge(t("원문 수동 편집됨"), color="orange").props("outline").tooltip(
                t("섹션 레벨·표기 설정을 바꿔도 이 편집은 새 구조에 합쳐져 남습니다. "
                "'되돌리기'·레이아웃 수정·다시 변환은 원문에서 새로 만들므로 편집이 사라집니다"))
        if ctrl.has_manual_edit_ko():
            ui.badge(t("번역 수동 편집됨"), color="orange").props("outline").tooltip(
                t("번역을 다시 실행하면 이 편집은 사라집니다 (번역 결과로 덮어쓰므로)"))

    @ui.refreshable
    def preview_pane() -> None:
        """렌더된 프리뷰. `.conv-md`를 유지해 PDF 싱크·읽던 자리·인용 툴팁·그림 확대 계약을 그대로 쓴다."""
        md = preview_markdown()
        if md:
            blocks = mdrender.blocks(md)
            edit_state["blocks"] = blocks
            html = "".join(h for _, h in blocks)
        else:
            edit_state["blocks"] = []
            html = ("<p><em>" + escape(t("paper.en.md 없음 — convert를 먼저 실행하세요.")) + "</em></p>"
                    if edit_state["lang"] == "en"
                    else "<p><em>" + escape(t("아직 번역 안 됨 — 번역 패널에서 실행하세요.")) + "</em></p>")
        ui.html(html, sanitize=True).classes("md-preview")

    def live_update(text: str) -> None:
        """타이핑 → 프리뷰. 바뀐 블록만 갈아 끼운다 (디바운스 후 1회).

        전체 재렌더는 렌더 HTML 274KB를 통째로 웹소켓에 태우고, innerHTML을 갈아엎으면서
        폭·높이 속성이 없는 그림을 파괴·재생성해 컨테이너 높이를 무너뜨린다 → scrollTop이 0으로
        잘리고 높이가 돌아와도 되돌아오지 않는다. 블록만 바꾸면 노드가 살아 있어 브라우저의
        스크롤 앵커링이 위치를 알아서 잡아 준다.
        """
        tv = edit_state.pop("timer", None)
        if tv is not None:
            tv.cancel()
        edit_state["timer"] = ui.timer(0.12, lambda: _live_patch(text), once=True)

    def _live_patch(text: str) -> None:
        try:
            new = mdrender.blocks(preview_markdown(text))
        except Exception:  # noqa: BLE001 — 타이핑 도중의 반쪽 문법이 프리뷰를 죽이면 안 된다
            return
        old = edit_state.get("blocks") or []
        # 앞뒤 공통 구간을 잘라내고 가운데만 보낸다 — 한 글자 편집은 보통 블록 하나만 바꾼다.
        i = 0
        while i < len(old) and i < len(new) and old[i][1] == new[i][1]:
            i += 1
        j, k = len(old), len(new)
        while j > i and k > i and old[j - 1][1] == new[k - 1][1]:
            j -= 1
            k -= 1
        edit_state["blocks"] = new
        if i == j and i == k:
            return  # 렌더 결과가 같다 (공백만 바뀐 경우 등)
        payload = "".join(h for _, h in new[i:k])
        ui.run_javascript(
            "(function(sel,start,del,html){"
            " var root=document.querySelector(sel); if(!root) return;"
            " var tmp=document.createElement('div'); tmp.setHTML(html);"  # NiceGUI의 DOMPurify 폴리필 경유
            " for(var n=0;n<del;n++){ if(root.children[start]) root.removeChild(root.children[start]); }"
            " var ref=root.children[start]||null;"
            " while(tmp.firstChild){ root.insertBefore(tmp.firstChild, ref); }"
            " var box=root.closest('.conv-md');"
            " if(box && box.__mdsyncInvalidate) box.__mdsyncInvalidate();"
            "})(%s,%d,%d,%s)" % (json.dumps(".conv-md .md-preview"), i, j - i, json.dumps(payload))
        )

    def editor_pane() -> None:
        """마크다운 편집기(원문 또는 번역). 마크다운이 진실의 원천이라 파싱→직렬화 왕복이 없다(손실 0)."""
        is_en = edit_state["lang"] == "en"

        def on_type(e) -> None:  # noqa: ANN001 — NiceGUI 값 변경 이벤트
            if edit_state["live"] and edit_side() == "preview":  # 옆이 PDF면 갱신할 미리보기가 없다
                live_update(e.value)
            # 자동 저장 — 타자가 0.8초 멈추면 파일에 (메모 패널과 같은 모아 쓰기)
            edit_state["pending"] = (edit_state["lang"], e.value)
            _edit_status(t("저장 중…"))
            tv = edit_state.get("timer")
            if tv is not None:
                tv.cancel()
            edit_state["timer"] = ui.timer(0.8, edit_flush, once=True)

        editor = ui.codemirror(
            ctrl.en_markdown() if is_en else ctrl.ko_markdown(),
            language="Markdown", line_wrapping=True, on_change=on_type,
        ).classes("w-full cm-en-editor").style("height:100%")
        edit_state["editor"] = editor
        # 코드 편집기처럼 보이지 않게 — 글자는 그대로 두고 기호를 숨겨 문서처럼 보인다(§ui/live_preview.py)
        ui.run_javascript(live_preview.live_preview_js(editor.id, tok, edit_state["lp"]))

        def revert() -> None:
            edit_state["pending"] = None  # 되돌리면 기다리던 편집은 버린다
            ctrl.save_and_reassemble(keep_edits=False)
            canvas.refresh()   # 에디터 내용까지 파일에서 다시 읽어야 한다
            edit_toggle.refresh()
            ui.notify(t("섹션 트리 기준으로 다시 만들었습니다."), type="info")

        with ui.row().classes("gap-2 items-center shrink-0 q-pt-xs"):
            edit_state["status"] = ui.label(
                t("자동 저장 — 입력을 멈추면 저장됩니다") if is_en else
                t("자동 저장 — 번역을 다시 실행하면 덮어써지고, 헤더 수를 바꾸면 원문과 나란히 정렬되지 않을 수 있어요")
            ).classes("text-xs text-gray-500")
            if is_en:  # 번역문에는 '원본'이 없다 — 되돌리려면 번역을 다시 실행한다
                ui.button(t("되돌리기(원본 다시 렌더)"), icon="restore", on_click=revert).props("outline dense")

    # 캔버스 표시 상태 — 원문/번역/PDF 패널 토글 + 텍스트↔PDF 분할 비율 + PDF 스크롤 싱크 + 편집 분할 비율
    # en/ko/pdf는 마지막으로 쓰던 구성으로 연다(전역 — load_panels)
    viewer_state = {**load_panels(), "split": 58, "sync": True,
                    "stip": True, "edsplit": 50}  # stip: 한 언어만 볼 때 문장 짝 툴팁

    # 정렬 행 — 그리드·목차·섹션 점프가 모두 같은 행 목록을 봐야 한다(따로 계산하면 레벨을 바꾼 뒤
    # 목차가 가리키는 행과 그리드의 행이 어긋난다). 문서 내용이 같으면 한 번만 계산한다.
    _rows_memo: dict = {}

    def layout_rows() -> dict:
        en_raw, ko_raw = ctrl.en_markdown(), ctrl.ko_markdown()
        key = (hash(en_raw), hash(ko_raw))
        if _rows_memo.get("key") == key:
            return _rows_memo["v"]
        # 수식 블록 앞에 빈 표식 — 클릭하면 원본 크롭과 대조한다 (§formula_fix; 파일엔 안 쓴다)
        fx_map, fx_inline = ctrl.formula_marks() if en_raw else ({}, {})
        en = served_markdown(formula_fix.tag(en_raw, fx_map, fx_inline), tok, ctrl.wd.out_images) if en_raw else ""
        ko = served_markdown(formula_fix.tag(ko_raw, fx_map, fx_inline), tok, ctrl.wd.out_images) if ko_raw else ""
        rows = align_rows(en, ko) if (en and ko) else None
        aligned = rows is not None
        if not aligned:  # 번역이 없거나 섹션 수가 달라 못 맞춤 → 원문 섹션만 행으로 (번역은 단일 패널)
            rows = [(s, None, _heading_of(s)) for s in _split_sections(en)] if en else []
        smap = sorted(ctrl.section_map(), key=lambda x: x["out_line"])
        page_by_id = ctrl.section_pages()
        row_sid = row_sections(rows, smap)  # 헤더 행 → 섹션 id (제목으로 순서대로 맞춤)
        row_page0 = {k: page_by_id[sid] - 1 for k, sid in row_sid.items() if page_by_id.get(sid)}
        v = {"en": en, "ko": ko, "rows": rows, "aligned": aligned, "smap": smap,
             "page_by_id": page_by_id, "row_sid": row_sid, "row_page0": row_page0,
             "sid_row": {sid: k for k, sid in row_sid.items()}}
        _rows_memo.update(key=key, v=v)
        return v

    def row_for_section(sid: str) -> int | None:
        """섹션 id → 그리드 행. 제목이 안 맞아 못 찾은 섹션은 문서 순서상 직전에 찾은 섹션의 행으로."""
        info = layout_rows()
        hit = info["sid_row"].get(sid)
        if hit is not None:
            return hit
        last = None
        for e in info["smap"]:
            if e["id"] in info["sid_row"]:
                last = info["sid_row"][e["id"]]
            if e["id"] == sid:
                return last
        return None

    def _row_scroll_js(k: int) -> str:  # 정렬 그리드의 k번째 행으로 스크롤
        return (f"document.querySelector('.sbs-grid [data-row=\"{k}\"]')"
                "?.scrollIntoView({behavior:'smooth', block:'start'})")

    def _sec_scroll_js(sid: str) -> str:  # 폴백 패널 — sec 앵커로 스크롤
        return f"document.getElementById('sec-{sid}')?.scrollIntoView({{behavior:'smooth', block:'start'}})"

    def _edit_goto_js(sid: str) -> str:
        """편집 중 섹션 이동 — 편집기를 그 섹션 헤더 줄로(커서도 그리로), 옆이 PDF면 그 쪽으로도.

        섹션 맵에서 그 섹션이 몇 번째 헤더인지 세고, 편집기 문서의 헤더 줄(코드펜스 밖 `#`)을 같은 순서로 센다 —
        직접 편집으로 줄 번호가 밀려도 헤더 순서는 그대로라서. 번역문도 원문과 헤더 순서가 같다(align_rows와 같은 가정).
        옆이 미리보기면 편집기 스크롤에 미리보기가 따라온다(_md_sync_js)."""
        ed = edit_state.get("editor")
        ids = [e["id"] for e in layout_rows()["smap"]]
        if ed is None or sid not in ids:
            return ""
        js = ("(function(id,k){var h=(typeof getElement==='function')?getElement(id):null,v=h&&h.editor;if(!v)return;"
              "var d=v.state.doc,f=false,c=-1;for(var n=1;n<=d.lines;n++){var t=d.line(n).text;"
              "if(/^\\s*(```|~~~)/.test(t)){f=!f;continue;}"
              "if(!f&&/^#{1,6}\\s/.test(t)&&++c===k){var p=d.line(n).from;"
              "v.dispatch({selection:{anchor:p},effects:v.constructor.scrollIntoView(p,{y:'start',yMargin:8})});"
              "v.focus();return;}}})(%d,%d);" % (ed.id, ids.index(sid)))
        pg = _sec_page0(sid)
        if edit_side() == "pdf" and pg is not None:
            js += f"document.getElementById('pdfpage-{pg}')?.scrollIntoView({{behavior:'instant', block:'start'}});"
        return js

    def _toc_js(k: int) -> str:
        """목차 항목 클릭 — 그리드의 그 행으로, PDF가 켜져 있으면 그 쪽으로도. 편집 중이면 편집기의 그 헤더로."""
        if edit_state["on"]:
            sid = layout_rows()["row_sid"].get(k)
            return _edit_goto_js(sid) if sid else ""
        page0 = layout_rows()["row_page0"].get(k)
        js = _row_scroll_js(k)
        if viewer_state["pdf"] and ctrl.pdf_page_count() > 0 and page0 is not None:
            js += (f"; document.getElementById('pdfpage-{page0}')"
                   "?.scrollIntoView({behavior:'smooth', block:'start'})")
        return js

    def toc_lang() -> str:
        """목차 제목 언어 — 'en' 영어만 / 'ko' 한국어만 / 'both' 병기. 전역 설정([ui].toc_lang)에 기억."""
        v = str(config.load_config().get("ui", {}).get("toc_lang", "both"))
        return v if v in _TOC_LANG else "both"

    def set_toc_lang(v: str) -> None:
        config.set_section_value("ui", "toc_lang", v)
        toc_panel.refresh()

    @ui.refreshable
    def toc_panel() -> None:
        """왼쪽 패널의 목차 — 제목 언어는 영어 / 한국어 / 병기 중 하나(번역이 있을 때만 고를 수 있다).

        병기는 두 줄로 쌓는다(한 줄에 둘을 다 쓰면 좁은 패널에서 잘린다): 지금 보는 언어가 윗줄(진하게),
        반대쪽이 아랫줄(옅게). 헤더를 번역하지 않아 둘이 같으면 한 줄만. 한국어 제목이 없는 섹션은
        어느 모드든 영어로 보여 준다(빈 줄을 두지 않는다).
        """
        info = layout_rows()
        rows = info["rows"]
        has_ko_titles = info["aligned"] and bool(info["ko"])
        mode = toc_lang() if has_ko_titles else "en"
        ko_first = viewer_state["ko"] and not viewer_state["en"] and info["aligned"]
        entries = []
        for k, (_enb, kob, head) in enumerate(rows):
            if not head:
                continue
            ko_head = _heading_of(kob) if (info["aligned"] and kob) else None
            ko_text = ko_head[1] if ko_head and _head_key(ko_head[1]) != _head_key(head[1]) else None
            entries.append((head[0], head[1], ko_text, k))
        with ui.row().classes("items-center w-full no-wrap gap-2"):
            ui.label(t("목차")).classes("md4-panel-title")
            ui.space()
            if has_ko_titles:
                # 작은 글자 셋만 — 목차의 주인공은 항목이지 이 스위치가 아니다(테두리·배경 없이, 고른 것만 파랗게)
                ui.toggle(_toc_lang_options(ctrl.wd.lang), value=mode, on_change=lambda e: set_toc_lang(e.value)) \
                    .props("flat dense no-caps toggle-color=primary") \
                    .classes("md4-toclang").tooltip(t("목차 제목을 어느 언어로 보일지"))
        with ui.element("div").classes("vtoc"):
            if not entries:
                ui.label(t("섹션 없음")).classes("text-xs text-gray-400 px-2")
            for level, text, ko_text, k in entries:
                if mode == "ko":
                    first, second = (ko_text or text), None
                elif mode == "en":
                    first, second = text, None
                else:
                    first, second = (ko_text, text) if (ko_first and ko_text) else (text, ko_text)
                item = ui.element("div").classes("vtoc-item").style(
                    f"padding-left:{8 + (max(level, 1) - 1) * 12}px")
                with item:
                    ui.label(first).classes("vtoc-l1")
                    if second:
                        ui.label(second).classes("vtoc-l2")
                item.tooltip(text + (f"\n{ko_text}" if ko_text else ""))
                item.on("click", lambda _, kk=k: ui.run_javascript(_toc_js(kk)))

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

    # 한 패널(단일 컬럼 그리드·.vpane·PDF만)일 때 본문 좌우 가장자리 핸들(.vbody-handle)로 본문 폭을 끈다.
    # 본문은 스크롤 상자 콘텐츠 폭의 가운데에 놓이므로 폭 = 2 × |마우스X − 가운데X| (480px 이상), 상자 폭
    # 이상이면 '제한 없음'(none). 끄는 동안 .md4-canvascol의 --md4-maxw를 바로 바꾸고, 놓으면 서버에
    # md4-body-width {w: px 또는 0}로 알려 기억한다. 더블클릭 = 820px로 되돌리기.
    # 재렌더마다 실행되므로 window 리스너는 1회, 상자 바인딩은 dataset 플래그로 멱등하게. 새 패널이 DOM에
    # 붙기 전에 돌 수 있어(_pdf_sync_js 참고) 바인딩 안 된 상자가 생길 때까지 잠깐씩 다시 시도한다.
    _BODY_DRAG_JS = """
    (function(){
      var MINW=480, DEF=820;
      function col(){ return document.querySelector('.md4-canvascol'); }
      function curW(){ var c=col(); var v=c ? getComputedStyle(c).getPropertyValue('--md4-maxw').trim() : '';
        if(v==='none') return 0; var n=parseFloat(v); return isNaN(n) ? DEF : n; }
      function setW(w){ var c=col(); if(c) c.style.setProperty('--md4-maxw', w ? w+'px' : 'none'); }
      function box(wrap){  // 본문 스크롤 상자 — 스크롤바·안쪽 여백을 뺀 콘텐츠 폭과 그 가운데(화면 X)
        var sc=wrap.querySelector(':scope > .sbs-grid, :scope > .vpane, :scope > .vpdf'); if(!sc) return null;
        var cs=getComputedStyle(sc), pl=parseFloat(cs.paddingLeft)||0, pr=parseFloat(cs.paddingRight)||0;
        var avail=sc.clientWidth-pl-pr, r=sc.getBoundingClientRect(), wl=wrap.getBoundingClientRect().left;
        var lo=r.left+sc.clientLeft-wl;  // 스크롤 상자 안쪽(스크롤바 제외)의 왼쪽 끝, 상자 기준 X
        return {sc: sc, avail: avail, cx: r.left+sc.clientLeft+pl+avail/2, wl: wl, lo: lo, hi: lo+sc.clientWidth};
      }
      function place(wrap){
        var b=box(wrap); if(!b || b.avail<=0) return;
        var w=curW(); w=(w && w<b.avail) ? w : b.avail;
        [].forEach.call(wrap.querySelectorAll(':scope > .vbody-handle'), function(h){
          // 제한 없음이면 상자 가장자리 — 잡는 폭(10px)이 스크롤 상자 밖(옆 패널 경계·스크롤바)으로 나가지 않게
          var x=b.cx-b.wl+(h.dataset.side==='left' ? -1 : 1)*w/2;
          h.style.left=Math.min(b.hi-5, Math.max(b.lo+5, x))+'px'; });
      }
      function placeAll(){ [].forEach.call(document.querySelectorAll('.vbodywrap'), place); }
      window.__md4BodyPlace=placeAll;
      function emit(w){ if(typeof emitEvent==='function') emitEvent('md4-body-width', {w: w}); }
      function bindWrap(wrap){
        wrap.dataset.bodybound='1';
        [].forEach.call(wrap.querySelectorAll(':scope > .vbody-handle'), function(h){
          h.addEventListener('mousedown', function(e){ if(e.button!==0) return; e.preventDefault();
            window.__bodyDrag={wrap: wrap, h: h, w0: curW(), w: curW(), moved: false};
            h.classList.add('dragging'); document.body.style.userSelect='none'; });
          h.addEventListener('dblclick', function(e){ e.preventDefault(); setW(DEF); placeAll(); emit(DEF); });
        });
        var b=box(wrap);
        if(b && typeof ResizeObserver!=='undefined') new ResizeObserver(function(){ place(wrap); }).observe(b.sc);
      }
      if(!window.__bodyDragInit){ window.__bodyDragInit=true;
        window.addEventListener('mousemove', function(e){ var d=window.__bodyDrag; if(!d) return;
          var b=box(d.wrap); if(!b || b.avail<=0) return;
          var w=Math.max(MINW, Math.round(2*Math.abs(e.clientX-b.cx)));
          if(w>=b.avail) w=0;  // 상자 폭 이상 = 제한 없음
          d.w=w; d.moved=true; setW(w); placeAll(); });
        window.addEventListener('mouseup', function(){ var d=window.__bodyDrag; if(!d) return;
          window.__bodyDrag=null; d.h.classList.remove('dragging'); document.body.style.userSelect='';
          if(d.moved && d.w!==d.w0) emit(d.w); });
        window.addEventListener('resize', placeAll);
      }
      (function bind(attempt){
        var ws=[].filter.call(document.querySelectorAll('.vbodywrap'), function(w){ return !w.dataset.bodybound; });
        if(!ws.length){ if(attempt<60) setTimeout(function(){ bind(attempt+1); }, 50); return; }
        ws.forEach(bindWrap); placeAll();
      })(0);
    })();
    """

    def _body_handles() -> None:
        """한 패널 본문의 좌우 폭 핸들 — .vbodywrap(상대 위치 상자) 안, 스크롤 상자 옆에 둔다."""
        tip = t("끌어서 본문 폭 조절 · 더블클릭하면 기본(820px)으로")
        ui.element("div").classes("vbody-handle").props('data-side=left').tooltip(tip)
        ui.element("div").classes("vbody-handle").props('data-side=right').tooltip(tip)

    # 텍스트 스크롤 → PDF 따라오기(섹션 단위). scroll_sel 컨테이너 안의 최상단 [data-page]로 PDF를 스크롤.
    # 컨테이너는 재렌더마다 새 요소라 리스너가 쌓이지 않는다(요소당 1회).
    def _pdf_sync_js(scroll_sel: str) -> str:
        return ("""
        (function bind(attempt){
          // 재렌더와 같은 메시지로 오므로 새 패널이 DOM에 붙기 전에 돌 수 있다 — 그때 그냥 돌아가면
          // PDF를 켜도 싱크가 영영 안 붙어 1쪽에 머문다. 생길 때까지 잠깐씩 다시 시도한다(~3초).
          var sc=document.querySelector('%s'), pv=document.querySelector('.vpdf');
          var cells=sc ? [].slice.call(sc.querySelectorAll('[data-page]')) : [];
          if(!sc || !pv || !cells.length){
            if(attempt<60) setTimeout(function(){ bind(attempt+1); }, 50);
            return;
          }
          if(sc.dataset.pdfsync) return;
          sc.dataset.pdfsync='1';
          var last=-1, tick=false, tries=0;
          // PDF를 막 연 직후엔 패널이 아직 자리를 안 잡아 scrollTop이 0에 묶인다 — 한 번 옮기고 '맞췄다'로
          // 치면 보던 쪽 대신 1쪽에 멈춘다. 그 쪽이 실제로 패널 맨 위에 올 때까지 잠깐씩 다시 맞춘다(~3초).
          function aligned(img){
            // 패널 폭이 0인 순간엔 모든 쪽 그림이 높이 0으로 맨 위에 겹쳐 '이미 맞음'처럼 보인다
            if(!pv.clientHeight || !img.getBoundingClientRect().height) return false;
            var d=img.getBoundingClientRect().top - pv.getBoundingClientRect().top;
            return Math.abs(d)<=2 || (d>0 && pv.scrollTop>=pv.scrollHeight-pv.clientHeight-1);
          }
          function settle(){
            var img=document.getElementById('pdfpage-'+last); if(!img) return;
            if(!aligned(img)) pv.scrollTop += img.getBoundingClientRect().top - pv.getBoundingClientRect().top;
            if(!aligned(img) && ++tries<100) setTimeout(settle, 30);  // rAF는 가려진 창에서 느려진다
          }
          function sync(){ tick=false;
            var top=sc.getBoundingClientRect().top+6, pg=+cells[0].dataset.page;
            for(var i=0;i<cells.length;i++){ if(cells[i].getBoundingClientRect().top<=top) pg=+cells[i].dataset.page; else break; }
            if(pg!==last && !isNaN(pg)){ last=pg; tries=0; settle(); }
          }
          sc.addEventListener('scroll', function(){ if(!tick){ tick=true; setTimeout(sync, 16); } });
          sync();
        })(0);
        """ % scroll_sel)

    _PDF_SYNC_JS = _pdf_sync_js(".sbs-grid")  # 뷰어 정렬 그리드용

    def _md_sync_js(cm_id: int, pv_sel: str) -> str:
        """에디터(CodeMirror 6) ↔ 프리뷰([data-line]) 양방향 스크롤 싱크.

        _pdf_sync_js 와 같은 뼈대다 — 컨테이너 dataset 플래그로 1회만 바인딩, rAF 스로틀,
        `[data-*]` 를 훑어 '화면 상단 기준 가장 최근 요소'를 찾고 상대 이동. 다른 점 셋:
          - 에디터 쪽은 DOM이 아니라 CM의 높이맵(lineBlockAtHeight)을 읽는다. CM6는 화면 밖 줄을
            DOM에서 지우지만 높이맵은 문서 전체를 알고 있어서 가상 스크롤에 영향받지 않는다.
          - 앞뒤 앵커 사이를 픽셀 비율로 보간해 '소수 줄'을 낸다(VS Code와 같은 방식). 블록 하나가
            여러 줄을 먹어도 그 안에서 위치가 이어진다.
          - 따라가는 쪽의 scroll 이벤트가 되받아치므로 200ms 잠금을 건다. 이 잠금을 빼면 즉시 진동한다.

        프리뷰를 프로그램적으로 스크롤해도 scroll 이벤트는 나므로, 기존 _pdf_sync_js가 새 코드 없이
        체인의 끝(에디터 → 프리뷰 → PDF)으로 따라붙는다.
        """
        return """
        (function(cmId, pvSel){
          var host = (typeof getElement === 'function') ? getElement(cmId) : null;
          var ed = host && host.editor;                       // CM6 EditorView
          var pv = document.querySelector(pvSel);
          if (!ed || !pv || pv.dataset.mdsync) return;
          pv.dataset.mdsync = '1';
          var sc = ed.scrollDOM;

          var cells = null;                                   // 프리뷰의 소스라인 앵커 (재렌더 시 무효화)
          function anchors(){
            if (cells) return cells;
            cells = [].slice.call(pv.querySelectorAll('[data-line]'))
              .map(function(el){ return { el: el, line: +el.getAttribute('data-line') }; })
              .filter(function(c){ return !isNaN(c.line); });
            cells.sort(function(a, b){ return a.line - b.line; });
            return cells;
          }
          pv.__mdsyncInvalidate = function(){ cells = null; };

          var lock = 0, lockTimer = null;
          function hold(){ lock = 1; clearTimeout(lockTimer); lockTimer = setTimeout(function(){ lock = 0; }, 200); }
          var top = function(el){ return el.getBoundingClientRect().top; };

          function editorLine(){                              // 에디터 뷰포트 상단 → 소스 줄(소수)
            var h = sc.getBoundingClientRect().top - ed.documentTop;
            var b = ed.lineBlockAtHeight(h), d = ed.state.doc;
            var first = d.lineAt(b.from).number - 1, last = d.lineAt(b.to).number - 1;
            var f = b.height > 0 ? Math.min(1, Math.max(0, (h - b.top) / b.height)) : 0;
            return first + f * (last - first + 1);
          }

          function previewLine(){                             // 프리뷰 뷰포트 상단 → 소스 줄(소수)
            var cs = anchors(), y = top(pv), prev = null, next = null;
            for (var i = 0; i < cs.length; i++){
              var r = cs[i].el.getBoundingClientRect();
              if (r.bottom < y) { prev = cs[i]; continue; }
              if (r.top > y) { next = cs[i]; break; }
              prev = cs[i];                                   // 상단을 걸친 블록 — 그 안쪽 비율
              var into = r.height > 0 ? Math.min(1, Math.max(0, (y - r.top) / r.height)) : 0;
              var nx = cs[i + 1];
              return prev.line + into * ((nx ? nx.line : prev.line + 1) - prev.line);
            }
            if (!prev) return 0;
            if (!next) return prev.line;
            var pb = prev.el.getBoundingClientRect(), gap = next.el.getBoundingClientRect().top - pb.top;
            return prev.line + (gap > 0 ? Math.min(1, Math.max(0, (y - pb.top) / gap)) : 0) * (next.line - prev.line);
          }

          function revealPreview(line){
            var cs = anchors(), prev = null, next = null;
            for (var i = 0; i < cs.length; i++){
              if (cs[i].line <= line) prev = cs[i]; else { next = cs[i]; break; }
            }
            if (!prev){ pv.scrollTop = 0; return; }
            var py = top(prev.el), y = py;
            if (next){
              var span = next.line - prev.line;
              var f = span > 0 ? Math.min(1, Math.max(0, (line - prev.line) / span)) : 0;
              y = py + f * (top(next.el) - py);
            }
            pv.scrollTop += (y - top(pv));
          }

          /* CM6는 아직 렌더하지 않은 구간의 줄 높이를 '추정'한다. 그래서 먼 곳으로 한 번에 뛰면
             착지점이 틀린다(목표 900줄 → 781줄 착지). 렌더 후 다시 재보고 보정하면 수렴한다.
             새 목표가 오면 이전 보정 체인은 세대 번호로 버린다 — 안 버리면 옛 목표가 되끌어당긴다. */
          var gen = 0;
          function revealEditor(line){
            var d = ed.state.doc, n = Math.min(d.lines, Math.max(1, Math.floor(line) + 1));
            var pos = d.line(n).from, frac = Math.min(1, Math.max(0, line - Math.floor(line)));
            var my = ++gen, tries = 0;
            function target(){ var b = ed.lineBlockAt(pos); return b.top + frac * b.height; }
            sc.scrollTop = target();
            (function step(){
              if (my !== gen || ++tries > 5) return;
              requestAnimationFrame(function(){
                if (my !== gen) return;
                hold();
                var t = target();
                if (Math.abs(t - sc.scrollTop) > 2){ sc.scrollTop = t; step(); }
              });
            })();
          }

          var tickE = false, tickP = false;
          sc.addEventListener('scroll', function(){
            if (lock || tickE) return; tickE = true;
            requestAnimationFrame(function(){ tickE = false; if (lock) return; hold(); revealPreview(editorLine()); });
          }, { passive: true });
          pv.addEventListener('scroll', function(){
            if (lock || tickP) return; tickP = true;
            requestAnimationFrame(function(){ tickP = false; if (lock) return; hold(); revealEditor(previewLine()); });
          }, { passive: true });

          window.__mdsync = { revealEditor: revealEditor, revealPreview: revealPreview,
                              invalidate: function(){ cells = null; } };
        })(%d, '%s');
        """ % (cm_id, pv_sel)

    def _ed_pdf_sync_js(cm_id: int, pages: list) -> str:
        """편집기 스크롤 → 원본 PDF 쪽 따라오기 (한 방향 — 읽기 모드 _pdf_sync_js와 같다).

        편집기 문서의 헤더 줄(코드펜스 밖 `#`)을 순서대로 세어 pages[k](그 헤더의 쪽)와 짝짓고, 헤더 사이에서는
        줄 비율로 다음 헤더의 쪽까지 보간해 PDF를 쪽 안의 위치까지 옮긴다. 헤더 목록은 문서가 바뀔 때만 다시 센다.
        상단 줄은 _md_sync_js처럼 CM 높이맵(lineBlockAtHeight)에서 읽는다 — 가상 스크롤에 영향받지 않는다."""
        return """
        (function(cmId, pages){
          var tries = 0;
          (function bind(){
            var host = (typeof getElement === 'function') ? getElement(cmId) : null;
            var ed = host && host.editor, pv = document.querySelector('.vpdf');
            if (!ed || !pv || !document.getElementById('pdfpage-0')){ if (++tries < 80) setTimeout(bind, 50); return; }
            if (ed.__md4pdfsync) return;
            ed.__md4pdfsync = 1;
            var sc = ed.scrollDOM, heads = null, headsDoc = null, tick = false, known = [];
            for (var i = 0, last = 0; i < pages.length; i++){ if (pages[i] != null) last = pages[i]; known.push(last); }
            function headLines(){
              if (headsDoc === ed.state.doc) return heads;
              var d = ed.state.doc, out = [], fence = false;
              for (var n = 1; n <= d.lines; n++){
                var t = d.line(n).text;
                if (/^\s*(```|~~~)/.test(t)){ fence = !fence; continue; }
                if (!fence && /^#{1,6}\s/.test(t)) out.push(n - 1);
              }
              heads = out; headsDoc = d;
              return out;
            }
            function topLine(){
              var h = sc.getBoundingClientRect().top - ed.documentTop;
              var b = ed.lineBlockAtHeight(h), d = ed.state.doc;
              var first = d.lineAt(b.from).number - 1, last = d.lineAt(b.to).number - 1;
              var f = b.height > 0 ? Math.min(1, Math.max(0, (h - b.top) / b.height)) : 0;
              return first + f * (last - first + 1);
            }
            function pageAt(line){
              var hs = headLines(), k = -1;
              for (var i = 0; i < hs.length && i < known.length; i++){ if (hs[i] <= line) k = i; else break; }
              if (k < 0) return 0;
              var p = known[k], nx = k + 1;
              if (nx < hs.length && nx < known.length && known[nx] > p){  // 다음 헤더까지 줄 비율로 쪽 보간
                var span = hs[nx] - hs[k];
                if (span > 0) p += Math.min(1, (line - hs[k]) / span) * (known[nx] - p);
              }
              return p;
            }
            function sync(){
              tick = false;
              var pf = pageAt(topLine()), n = Math.floor(pf), img = document.getElementById('pdfpage-' + n);
              if (!img) return;
              var r = img.getBoundingClientRect();
              pv.scrollTop += (r.top - pv.getBoundingClientRect().top) + (pf - n) * r.height;
            }
            sc.addEventListener('scroll', function(){ if (!tick){ tick = true; setTimeout(sync, 30); } }, {passive: true});
            setTimeout(sync, 200);
          })();
        })(%d, %s);
        """ % (cm_id, json.dumps(pages))

    def _pdf_pane(own_width: bool = False) -> None:
        """own_width = 텍스트 옆 PDF — 쪽 폭을 본문 폭(--md4-maxw)과 떼어 둔다. 안 그러면 원문 본문 핸들을
        좁힐 때 옆 PDF 쪽도 같이 줄어든다. PDF만 볼 때는 같은 핸들로 쪽 폭을 조절하므로 물려받는다."""
        sizes = ctrl.pdf_page_sizes()
        with ui.element("div").classes("vpdf").style("--md4-maxw:900px" if own_width else ""):
            if not sizes:
                ui.label(t("원본 PDF를 찾을 수 없습니다.")).classes("text-sm text-gray-500")
                return
            # width/height를 적어 두면 그림이 뜨기 전에도 쪽마다 제 높이를 차지한다. 없으면 lazy 그림이
            # 모두 높이 0으로 시작해, 'PDF 열기 → 보던 쪽으로 이동'이 엉뚱한 곳(첫 쪽 근처)에 떨어진다.
            imgs = "".join(
                f'<img id="pdfpage-{i}" src="/pdfpage/{tok}/{i}" loading="lazy" title="p.{i + 1}" '
                f'width="{round(w)}" height="{round(h)}" '
                'style="width:100%;height:auto;display:block;margin:0 auto 10px;max-width:var(--md4-maxw,900px);'
                'border:1px solid #e0e0e0;border-radius:6px"/>'
                for i, (w, h) in enumerate(sizes)
            )
            ui.html(f'<div id="pdf-pages" style="width:100%">{imgs}</div>')

    def grid_view() -> None:
        """읽기 모드 캔버스 — 원문·번역·PDF를 토글/리사이즈해 보고, 원문|번역은 섹션·문단 단위로 정렬."""
        info = layout_rows()
        if not info["en"]:
            ui.label(t("영어 마크다운이 없습니다 — 먼저 변환하세요.")).classes("text-sm text-gray-500")
            return
        _md = ["fenced-code-blocks", "tables", "latex"]
        rows, aligned, row_page0 = info["rows"], info["aligned"], info["row_page0"]
        has_ko = bool(info["ko"])
        has_pdf = ctrl.pdf_page_count() > 0

        show_en = viewer_state["en"]
        show_ko = viewer_state["ko"] and has_ko
        show_pdf = viewer_state["pdf"] and has_pdf
        if not (show_en or show_ko or show_pdf):
            show_en = True  # 최소 하나는 보이도록

        def text_area(solo: bool = False) -> None:
            """solo = 텍스트가 한 컬럼(원문만·번역만)으로 보일 때 — PDF 옆이어도 본문에 폭 핸들을 두고
            가운데에 묶는다. 원문|번역 두 컬럼이면 컬럼 사이 핸들(.vcol-handle)이 대신한다."""
            two = show_en and show_ko and aligned
            # 한 언어만 볼 때 반대쪽을 숨은 셀로 같이 그린다 — 문장에 올리면 그 짝을 툴팁으로(JS)
            ghost = aligned and show_en != show_ko and viewer_state["stip"]
            if aligned or show_en:
                # 정렬 그리드 (원문/번역 컬럼 토글). 정렬 실패 시엔 EN만 그리드로(그때 KO 컬럼 생략).
                handles = solo and not two
                with ui.element("div").classes("vwrap" + (" vbodywrap" if handles else "")):
                    two_cls = " sbs-two" if two else (" sbs-tip" if ghost else "")
                    with ui.element("div").classes(f"sbs-grid{two_cls}").style(
                            f"--sbs-cols:{'1fr 1fr' if two else '1fr'}"):
                        for k, (enb, kob, head) in enumerate(rows):
                            sep = " sbs-row-sep" if (head and k > 0) else ""
                            # data-row는 두 셀 모두에 — 목차 스크롤(첫 셀)과 호버 짝 표시(둘 다)에 쓴다.
                            # data-page(PDF 싱크)는 행마다 하나여야 하므로 첫 표시 셀에만.
                            page_prop = f" data-page={row_page0[k]}" if k in row_page0 else ""
                            anchored = False
                            if show_en:
                                div = " sbs-cell-div" if two else ""
                                with ui.element("div").classes(f"sbs-cell anno-scope{div}{sep}").props(
                                        f"data-row={k} data-side=en{page_prop}"):
                                    ui.markdown(enb, extras=_md).classes("md-preview sbs-md")
                                anchored = True
                            if show_ko and aligned:
                                with ui.element("div").classes(f"sbs-cell anno-scope{sep}").props(
                                        f"data-row={k} data-side=ko" + ("" if anchored else page_prop)):
                                    ui.markdown(kob or "", extras=_md).classes("md-preview sbs-md")
                            if ghost:  # anno-scope·data-page 없이 — 메모·찾기·PDF 싱크가 이 셀을 모른다
                                with ui.element("div").classes("sbs-cell sbs-ghost").props(
                                        f"data-row={k} data-side={'ko' if show_en else 'en'}"):
                                    ui.markdown((kob or "") if show_en else enb, extras=_md).classes("md-preview sbs-md")
                    if two:
                        ui.element("div").classes("vcol-handle")
                    elif handles:
                        _body_handles()
                if two:
                    ui.run_javascript(_ENKO_DRAG_JS)
                elif handles:
                    ui.run_javascript(_BODY_DRAG_JS)
            else:
                # 정렬 실패 + 번역만 → 단일 패널 (여기서도 하이라이트·메모는 그대로 된다)
                with ui.element("div").classes("vwrap" + (" vbodywrap" if solo else "")):
                    with ui.element("div").classes("vpane anno-scope").style(
                            "padding:1px 22px 8px").props("data-row=0 data-side=ko"):
                        with ui.element("div").classes("vpane-in md-preview"):
                            ui.markdown(info["ko"], extras=_md)
                    if solo:
                        _body_handles()
                if solo:
                    ui.run_javascript(_BODY_DRAG_JS)

        with ui.element("div").classes("vshell"), ui.element("div").classes("vmain"):
            if (show_en or show_ko) and show_pdf:
                with ui.splitter(value=viewer_state["split"]).classes("w-full").style("height:100%") as sp:
                    sp.on_value_change(lambda e: viewer_state.update(split=e.value))
                    with sp.before:
                        text_area(solo=True)
                    with sp.after:
                        _pdf_pane(own_width=True)
                if row_page0 and viewer_state["sync"]:  # 텍스트 스크롤 → PDF 페이지 따라오기 (토글)
                    ui.run_javascript(_PDF_SYNC_JS)
            elif show_pdf:
                with ui.element("div").classes("vwrap vbodywrap"):  # PDF만 — 쪽 그림 폭을 같은 핸들로
                    _pdf_pane()
                    _body_handles()
                ui.run_javascript(_BODY_DRAG_JS)
            else:
                text_area(solo=True)
        ui.run_javascript("window.__mdAnnoApply && window.__mdAnnoApply()")  # 하이라이트 다시 얹기
        # 번역 언어 — 그림 글자를 그릴 수 없는 언어(이어 쓰는 문자)면 확대 뷰어의 '그림 속 글자 번역' 단추를 숨긴다
        from md4paper import figure_translate

        # 칸마다 lang — 일·중 한자는 언어에 따라 글자 모양이 달라 브라우저가 그 언어 글꼴을 고르게 한다
        ui.run_javascript(f"window.__md4TrLang = {json.dumps(ctrl.wd.lang)}; "
                          f"window.__md4FigTrOk = {json.dumps(figure_translate.supported(ctrl.wd.lang))}; "
                          "setTimeout(function(){ document.querySelectorAll('[data-side=ko],[data-side=en]')"
                          ".forEach(function(c){ c.lang = c.getAttribute('data-side') === 'ko' ? "
                          "window.__md4TrLang : 'en'; }); }, 0)")
        place = viewer_state.pop("_place", None)  # 편집기에서 보던 자리 → 그리드의 같은 행·같은 비율
        if place and (show_en or show_ko):
            ui.run_javascript(_RESTORE_JS % json.dumps({"row": str(place[0]), "f": place[1], "off": 0, "ratio": 0}))
        # 토글 전 캡처해 둔 텍스트 스크롤 위치 복원 + (PDF 있으면) 그 위치로 PDF 정렬
        r = viewer_state.pop("_restore", None)
        if (show_en or show_ko) and r:
            ui.timer(0.13, lambda: ui.run_javascript(
                "(function(){var e=document.querySelector('.sbs-grid')||document.querySelector('.vpane');"
                f"if(!e)return;var m=e.scrollHeight-e.clientHeight; e.scrollTop=(m>0?{r}:0)*m;"
                "e.dispatchEvent(new Event('scroll'));})()"), once=True)

    def edit_view() -> None:
        """편집 모드 캔버스 — 편집기 | 원본 PDF(기본) 또는 프리뷰. 프리뷰는 `.conv-md`를 유지해
        읽던 자리·인용 툴팁·그림 확대 계약을 그대로 쓴다."""
        side = edit_side()
        # 옆이 PDF면 읽기 모드의 '원문 | PDF' 나눔 비율을 같이 쓴다 — 편집으로 바꿔도 본문 칸이 같은 자리·같은 폭
        split_key = "split" if side == "pdf" else "edsplit"
        with ui.element("div").classes("vshell"), ui.element("div").classes("vmain"):
            with ui.splitter(value=viewer_state[split_key]).classes("w-full").style("height:100%") as ed_sp:
                ed_sp.on_value_change(lambda e: viewer_state.update({split_key: e.value}))
                with ed_sp.before:
                    with ui.column().classes("conv-ed").style(
                            "height:100%; min-width:0; gap:4px; padding:0; flex-wrap:nowrap"):
                        editor_pane()
                with ed_sp.after:
                    if side == "pdf":
                        _pdf_pane(own_width=True)  # 편집기 옆 — 쪽 폭을 본문 폭 설정과 떼어 둔다
                    else:
                        with ui.element("div").classes("conv-md md-scroll-en").style(
                                "height:100%; min-width:0; overflow-y:auto; overscroll-behavior:contain; padding:0 12px"):
                            preview_pane()
        editor = edit_state.get("editor")
        if editor is None:
            return
        place = viewer_state.pop("_place", None)  # 읽기 모드에서 보던 자리 → 편집기의 같은 자리
        at = _place_to_line(place, edit_state["lang"]) if place else None
        if at:
            ui.run_javascript(_EDITOR_GOTO_JS % (editor.id, at[0], at[1]))
        if side == "preview":  # 에디터 ↔ 프리뷰 양방향 스크롤 싱크
            ui.timer(0.35, lambda e=editor: ui.run_javascript(_md_sync_js(e.id, ".conv-md")), once=True)
        elif viewer_state["sync"]:  # 에디터 스크롤 → PDF 쪽 따라오기 (툴바 싱크 단추로 끈다)
            pages = edit_pdf_pages()
            if any(p is not None for p in pages):
                ui.run_javascript(_ed_pdf_sync_js(editor.id, pages))

    def edit_pdf_pages() -> list[int | None]:
        """편집 중인 문서의 헤더(문서 순서) → PDF 쪽(0-based). 편집기는 헤더 줄을 순서대로 세어 짝짓는다 —
        직접 편집으로 줄 번호가 밀려도 헤더 순서는 그대로라서. 번역문도 원문과 헤더 순서가 같다(align_rows와 같은 가정)."""
        info = layout_rows()
        return [info["page_by_id"][e["id"]] - 1 if info["page_by_id"].get(e["id"]) else None for e in info["smap"]]

    @ui.refreshable
    def canvas() -> None:
        """하나뿐인 문서 캔버스 — 편집이 켜져 있으면 편집기|프리뷰, 아니면 원문|번역|PDF 그리드."""
        if edit_state["on"]:
            edit_view()
        else:
            grid_view()

    async def _vtoggle(key: str) -> None:
        if key in ("en", "ko", "pdf"):  # 패널 토글만 가드 (sync·stip은 제외)
            has_ko = bool(ctrl.ko_markdown())
            has_pdf = ctrl.pdf_page_count() > 0
            nxt = {**viewer_state, key: not viewer_state[key]}
            if not (nxt["en"] or (nxt["ko"] and has_ko) or (nxt["pdf"] and has_pdf)):
                return  # 마지막 패널을 끄려는 클릭 → 무시
        if key == "pdf" and edit_state["on"]:
            # 편집 중엔 오른쪽 칸을 PDF ↔ 미리보기로 바꾼다 (읽기 모드의 PDF 켜짐 여부와 따로 기억)
            set_edit_side("preview" if edit_side() == "pdf" else "pdf")
            return
        # 재렌더 전 텍스트 스크롤 위치(비율)를 캡처 → 재렌더 후 복원(+PDF 정렬). PDF 열기가 위치를 잃지 않게.
        ratio = await ui.run_javascript(
            "(function(){var e=document.querySelector('.sbs-grid')||document.querySelector('.vpane');"
            "if(!e)return null;var m=e.scrollHeight-e.clientHeight;return m>0?e.scrollTop/m:0;})()")
        viewer_state[key] = not viewer_state[key]
        viewer_state["_restore"] = ratio
        if key in ("en", "ko", "pdf"):
            # 편집 때문에 잠시 접어 둔 PDF는 '쓰던 패널'로 친다 — 편집을 끄면 되살아나는 자리다
            save_panels(viewer_state)
        viewer_toolbar.refresh()
        canvas.refresh()
        if key in ("en", "ko"):
            toc_panel.refresh()  # 어느 언어가 윗줄인지 바뀐다

    def body_width() -> int:
        """한 패널만 볼 때의 본문 최대 폭(px). 0 = 제한 없음. 전역 설정([ui].body_width)에 기억."""
        v = config.load_config().get("ui", {}).get("body_width", 820)
        return int(v) if isinstance(v, (int, float)) and (v == 0 or 480 <= v <= 4000) else 820

    def body_width_css() -> str:
        w = body_width()
        return f"--md4-maxw:{w}px" if w else "--md4-maxw:none"

    def set_body_width(w: int) -> None:
        w = 0 if w <= 0 else max(480, min(int(w), 4000))  # body_width()가 받아들이는 범위로 — 저장값 = 보이는 값
        config.set_section_value("ui", "body_width", w)
        # 다시 그리지 않고 변수만 바꾼다 — 읽던 자리·하이라이트가 흔들리지 않게
        ui.run_javascript("document.querySelector('.md4-canvascol')?.style.setProperty('--md4-maxw',"
                          + json.dumps(f"{w}px" if w else "none") + ")"
                          + ";window.__md4BodyPlace&&window.__md4BodyPlace()")

    def on_body_width(e) -> None:  # noqa: ANN001 — GenericEventArguments
        """본문 가장자리 핸들을 놓거나 더블클릭했을 때(_BODY_DRAG_JS) — 폭(px, 0 = 제한 없음)을 기억."""
        try:
            w = int((e.args or {}).get("w", 0))
        except (TypeError, ValueError, AttributeError):
            return
        set_body_width(w)

    ui.on("md4-body-width", on_body_width)

    @ui.refreshable
    def viewer_toolbar() -> None:
        """캔버스 위 한 줄 — 무엇을 볼지(원문·번역·PDF), 편집, 메모·챗, 내보내기."""
        has_ko = bool(ctrl.ko_markdown())
        has_pdf = ctrl.pdf_page_count() > 0

        def chip(label: str, key: str, enabled: bool = True) -> None:
            active = (edit_side() == "pdf" if key == "pdf" and edit_state["on"] else viewer_state[key]) and enabled
            b = ui.button(label, on_click=lambda k=key: _vtoggle(k)).props(
                "dense no-caps " + ("unelevated color=primary" if active else "outline color=grey-7"))
            if not enabled:
                b.disable()
                b.tooltip(t("번역 없음") if key == "ko" else t("PDF 원본 없음"))
            elif key == "pdf" and edit_state["on"]:
                b.tooltip(t("편집기 오른쪽 칸 — 켜면 원본 PDF, 끄면 렌더된 미리보기"))

        show_pdf = (edit_side() == "pdf" if edit_state["on"] else viewer_state["pdf"]) and has_pdf
        with ui.row().classes("items-center gap-2 w-full no-wrap md4-canvasbar"):
            chip(t("원문"), "en")
            chip(t("번역"), "ko", has_ko)
            chip("PDF", "pdf", has_pdf)
            ui.separator().props("vertical").classes("h-6")
            edit_toggle()
            if show_pdf:  # PDF 켜졌을 때만 — 텍스트 스크롤에 PDF 페이지 따라오기
                ui.button(icon="sync" if viewer_state["sync"] else "sync_disabled",
                          on_click=lambda: _vtoggle("sync")).props(
                    "flat dense round " + ("color=primary" if viewer_state["sync"] else "color=grey-6")) \
                    .tooltip(t("스크롤 시 PDF 페이지 따라오기 (켜짐)") if viewer_state["sync"] else t("스크롤 시 PDF 페이지 따라오기 (꺼짐)"))
            if has_ko and viewer_state["en"] != viewer_state["ko"]:  # 한 언어만 볼 때 — 문장 짝 툴팁 (클릭)
                ui.button(icon="translate", on_click=lambda: _vtoggle("stip")).props(
                    "flat dense round " + ("color=primary" if viewer_state["stip"] else "color=grey-6")) \
                    .tooltip(t("문장을 클릭하면 {side} 문장 보기 ({state})",
                               side=t("번역") if viewer_state["en"] else t("원문"),
                               state=t("켜짐") if viewer_state["stip"] else t("꺼짐")))
            ui.button(icon="sticky_note_2", on_click=lambda: ui.run_javascript(
                "window.__mdAnnoTogglePanel && window.__mdAnnoTogglePanel()")) \
                .props("flat dense round color=grey-7").classes("md4-anno-btn") \
                .tooltip(t("하이라이트 · 메모 목록 — 드래그하면 그 자리에, 문장을 더블클릭하면 원문·번역 양쪽에"))
            ui.button(icon="forum", on_click=lambda: ui.run_javascript(
                "window.__md4Agent && window.__md4Agent.toggle()")).props(
                "flat dense round color=grey-7").tooltip(t("에이전트 — 이 논문·라이브러리에 묻고, 근거 문단으로 이동"))
            ui.space()
            library_button()
            if ctrl.source_pdf():  # 내보내기와 같은 '이 논문 파일을 바깥으로' 묶음 — 앱 안 PDF 칸은 왼쪽 토글
                ui.button(t("원본 PDF"), icon="picture_as_pdf", on_click=open_pdf_app) \
                    .props("flat dense no-caps color=primary") \
                    .tooltip(t("PDF 뷰어로 열기 — 원본 PDF를 미리보기 등 기본 앱에서"))
            # 내보내기는 메뉴 하나로 — 세 단추가 툴바 폭을 먹던 것을 접는다. 형식 선택도 같은 메뉴 안에.
            with ui.button(t("내보내기"), icon="download").props("flat dense no-caps color=primary"):
                with ui.menu().props("anchor='bottom right' self='top right'").classes("md4-exportmenu"):
                    ui.menu_item(t("영어 마크다운 (zip)"), on_click=download_en)
                    item_ko = ui.menu_item(t("번역 마크다운 · {native} (zip)", native=langs.get(ctrl.wd.lang).native),
                                           on_click=download_ko)
                    if not has_ko:
                        item_ko.disable()
                    ui.menu_item(t("하이라이트·메모 (md)"), on_click=download_notes)
                    ui.separator()
                    with ui.row().classes("items-center gap-2 q-px-md q-py-xs no-wrap"):
                        ui.label(t("형식")).classes("text-xs text-gray-500")
                        export_fmt_review()

    # 번역 진행(섹션별) — 워커 스레드가 콜백으로 쓰고, UI 타이머가 폴링해 트리 아이콘을 갱신
    # failures는 지난 실행 것을 status.json에서 되살린다 — 메모리에만 두면 리뷰 화면을 다시 열었을 때
    # "왜 이 섹션이 영어로 남았는지"가 사라진다(실패 청크는 캐시에 없어 사후 재구성도 불가능).
    translate_state = {"status": {}, "done": 0, "total": 0, "active": False,
                       "failures": (ctrl.wd.load_status().get("translate") or {}).get("failures", {})}
    # 섹션 트리 노드에 직접 붙이는 상태 아이콘 (Quasar QTree의 node.icon/iconColor).
    # 헤더 슬롯을 덮어쓰지 않으므로 번역 선택 체크박스가 그대로 살아 있다.
    # translating은 primary 색 → CSS에서 .q-tree__icon.text-primary만 회전(스피너처럼). done/실패는 정지.
    _TREE_ICON = {
        "translating": ("autorenew", "primary"),
        "done": ("check_circle", "green-6"),
        "passthrough": ("warning", "orange-7"),  # 구조 검증 실패 → 영어 원문 통과 (경고가 맞다)
    }
    _SKIP_ICON = ("remove", "grey-5")  # 체크 해제된 섹션 = 번역 대상 아님
    _STALE_ICON = ("update", "orange-7")  # 원문이 바뀌어 다음 실행 때 다시 번역될 섹션
    tree_ref: dict = {}  # {"tree": ui.tree, "nodes": [...]} — 번역 중 아이콘만 갱신

    def _apply_tree_status(nodes: list[dict], status: dict, unticked: set, stale: set) -> None:
        for n in nodes:
            icon = _TREE_ICON.get(status.get(n["id"], ""))
            if icon is None and n["id"] in unticked:
                icon = _SKIP_ICON  # 번역에서 빠진 섹션임을 한눈에
            elif icon is None and n["id"] in stale:
                icon = _STALE_ICON  # 재변환·편집으로 원문이 바뀜 — 번역이 낡았다
            if icon:
                n["icon"], n["iconColor"] = icon
            else:
                n.pop("icon", None)
                n.pop("iconColor", None)
            _apply_tree_status(n.get("children") or [], status, unticked, stale)

    def _unticked_ids() -> set:
        return {s.id for s in ctrl.translatable_sections() if not s.translate}

    def update_tree_status() -> None:
        """섹션 트리 아이콘 갱신 — 번역 진행(진행중/완료/실패) + 원문 바뀜 + 미선택(–)."""
        tree, nodes = tree_ref.get("tree"), tree_ref.get("nodes")
        if tree is None or nodes is None:
            return
        stale = set() if translate_state["active"] else ctrl.stale_sections()
        _apply_tree_status(nodes, translate_state["status"], _unticked_ids(), stale)
        tree.update()

    def refresh_staleness() -> None:
        """원문(en.md)이 바뀐 뒤 — 트리의 '원문 바뀜' 아이콘, 번역 패널 배너, 헤더 칩을 함께 갱신."""
        update_tree_status()
        if _translate_refs.get("stale") is not None:
            _translate_refs["stale"].refresh()
        status_chip.refresh()

    # --- 용어집 편집 상태 (build 스코프 — 용어집 시트 편집 + '번역할 섹션'의 용어 추가가 공유) · 자동 저장 ---
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
            ui.notify(t("대소문자만 다른 중복 용어를 합쳤습니다."), type="info")
        ctrl.save_glossary([GlossaryEntry(**d) for d in gloss_entries if d["term"].strip()])

    # 주 컬렉션 용어집 — 용어집 시트 위 구역에 읽기 전용으로만 보인다. gloss_entries(= glossary.yaml로 통째로
    # 저장되는 목록)에는 절대 넣지 않는다: 섞으면 첫 blur에 컬렉션 항목이 논문 용어집으로 새어 들어간다.
    coll_view: dict = {"pid": ctrl.collection_id(), "entries": ctrl.collection_glossary()}

    def coll_keys() -> set[str]:
        from md4paper.translate.glossary import term_key

        return {term_key(e.term) for e in coll_view["entries"]}

    # 용어 예문 — 행의 따옴표 단추로 연다. 열린 용어(term_key)만 기억하고, 예문은 그릴 때 찾는다
    # (정렬 행은 retrieval 캐시라 한 논문 안에서는 빠르다). 편집 중인 번역어로 번역 문장을 찾는다.
    ex_open: set[str] = set()
    ex_dialog: dict = {"dlg": None}   # 용어집 시트 — translate_run_panel이 지은 뒤 채운다

    def goto_example_row(row: int) -> None:
        """예문의 행 번호 → 시트를 닫고 본문의 그 행으로 스크롤·잠깐 강조."""
        if ex_dialog["dlg"] is not None:
            ex_dialog["dlg"].close()
        ui.run_javascript(
            "setTimeout(function(){var els=document.querySelectorAll('.sbs-grid .sbs-cell[data-row=\"%d\"]');"
            "if(!els.length) els=document.querySelectorAll('[data-row=\"%d\"]');if(!els.length) return;"
            "els[0].scrollIntoView({behavior:'smooth',block:'center'});"
            "els.forEach(function(el){el.classList.remove('chat-cite-flash');void el.offsetWidth;"
            "el.classList.add('chat-cite-flash');setTimeout(function(){el.classList.remove('chat-cite-flash');},1400);});"
            "}, 250);" % (row, row))

    def example_toggle(term: str, refresh) -> None:  # noqa: ANN001 — ui.refreshable
        from md4paper.translate.glossary import term_key

        key = term_key(term)
        if not key:
            ui.notify(t("원어를 먼저 입력하세요."), type="warning")
            return
        ex_open.symmetric_difference_update({key})
        refresh.refresh()

    def example_button(term: str, refresh) -> None:  # noqa: ANN001
        from md4paper.translate.glossary import term_key

        on = bool(term.strip()) and term_key(term) in ex_open
        ui.button(icon="format_quote", on_click=lambda _, tv=term: example_toggle(tv, refresh)) \
            .props(f"flat dense round size=sm color={'primary' if on else 'grey-6'}") \
            .tooltip(t("이 용어가 쓰인 문장 (원문·번역) — 어떻게 옮기는 게 자연스러운지 문맥으로 보기"))

    def example_block(term: str, korean: str, policy: str) -> None:
        from md4paper.translate.glossary import term_key

        if not term.strip() or term_key(term) not in ex_open:
            return
        with ui.column().classes("w-full gap-0").style("padding:0 0 4px 12px"):
            try:
                ex, rows = term_examples.find(ctrl.wd, term, korean, policy)
            except (OSError, ValueError) as e:
                ui.label(t("본문을 읽지 못했습니다: {e}", e=e)).classes("md4-tex-meta")
                return
            term_examples.render(ex, rows, goto=goto_example_row,
                                 unaligned_note=term_examples.unaligned(ctrl.wd))

    @ui.refreshable
    def gloss_rows() -> None:
        from md4paper.translate.glossary import term_key

        over = coll_keys()  # 컬렉션에 같은 용어가 있으면 이 논문 행은 번역에 안 쓰인다
        if coll_view["pid"]:
            n = sum(1 for d in gloss_entries if d["term"].strip())
            ui.label(t("이 논문 용어집 ({n})", n=n)).classes("text-sm font-medium q-mt-xs")
        if not gloss_entries:
            ui.label(t("용어집이 비어 있습니다 — 변환 시 자동 생성됩니다. '행 추가'나 왼쪽 '용어 추가'로 채우세요.")) \
                .classes("text-sm text-gray-400")
        for i, d in enumerate(gloss_entries):
            shadowed = bool(d["term"].strip()) and term_key(d["term"]) in over
            strike = ' input-style="text-decoration:line-through"' if shadowed else ""
            row = ui.row().classes("items-center gap-2 w-full")
            if shadowed:
                row.style("opacity:.5").tooltip(t("컬렉션 용어집이 우선합니다"))
            with row:
                ui.input(placeholder=t("원어"), value=d["term"]).bind_value(d, "term") \
                    .on("blur", lambda: gloss_autosave()).props("dense outlined" + strike).classes("w-40")
                ui.icon("arrow_forward").classes("text-gray-400")
                ui.input(placeholder=t("번역어"), value=d["korean"]).bind_value(d, "korean") \
                    .on("blur", lambda: gloss_autosave()).props("dense outlined" + strike).classes("w-40")
                ui.select(_tr(_POLICY), value=d["policy"]).bind_value(d, "policy") \
                    .on("update:model-value", lambda: gloss_autosave()).props("dense outlined").classes("w-44")
                example_button(d["term"], gloss_rows)
                ui.button(icon="delete", on_click=lambda _, idx=i: (
                    gloss_entries.pop(idx), gloss_rows.refresh(), gloss_autosave())) \
                    .props("flat dense round size=sm")
            example_block(d["term"], d["korean"], d["policy"])

    def add_gloss_row() -> None:
        gloss_entries.append({"term": "", "korean": "", "policy": "translate"})
        gloss_rows.refresh()

    def add_terms_from_selected() -> None:
        """번역할 섹션에서 새 용어를 뽑아 용어집에 추가 (변환에 쓰던 기본 프로바이더 사용)."""
        n_sec = len(ctrl.ticked_ids())
        if n_sec == 0:
            ui.notify(t("먼저 번역할 섹션을 선택하세요."), type="warning")
            return
        try:
            prov = config.build_provider()
        except RuntimeError as e:
            ui.notify(str(e), type="negative")
            return

        def run_extract() -> None:
            gloss_autosave()  # 지금 편집본을 파일에 — 작업은 파일 위에서 합친다
            have = [d["term"] for d in gloss_entries if d["term"].strip()]

            # 결과를 화면의 편집 사본이 아니라 glossary.yaml에 바로 합친다 — 탭을 옮겨 이 페이지가
            # 사라져도 뽑은 용어가 남도록. 돌아온 화면은 파일에서 다시 읽는다.
            def work(job: jobs.Job) -> int:
                new = ctrl.new_terms_from_selected(prov, have)
                cur = ctrl.glossary_entries()
                seen = {e.term.strip().lower() for e in cur}
                added = [e for e in new if e.term.strip() and e.term.strip().lower() not in seen]
                if added:
                    ctrl.save_glossary([*cur, *added])
                job.message = (t("새 용어 {n}개 추가됨 (자동 저장).", n=len(added)) if added
                               else t("추가할 새 용어가 없습니다."))
                return len(added)

            job = jobs.start(ctrl.wd.root, "terms", t("용어 추출"), work, title=_title)
            if job is None:
                ui.notify(t("이미 용어를 추출하는 중입니다."), type="info")
                return
            ui.notify(t("선택 섹션에서 용어 추출 중… (AI) — 다른 탭으로 옮겨도 계속됩니다"))
            attach_terms(job)
            paper_tabs.refresh()

        if n_sec > 12 or len(ctrl.selected_sections_text()) > 30000:  # 많으면 시간·비용 경고
            with ui.dialog() as dlg, ui.card().classes("gap-2"):
                ui.label(t("{n_sec}개 섹션에서 용어를 추출합니다. 섹션이 많아 시간·비용이 더 들 수 있어요. 계속할까요?", n_sec=n_sec))

                def _yes() -> None:
                    dlg.close()
                    run_extract()
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button(t("취소"), on_click=dlg.close).props("flat")
                    ui.button(t("계속"), on_click=_yes).props("color=primary")
            dlg.open()
        else:
            run_extract()

    def attach_terms(job: jobs.Job) -> None:
        """용어 추출이 끝나면 파일에서 용어집을 다시 읽어 표에 반영한다."""
        def tick() -> None:
            if job.running:
                return
            timer.deactivate()
            fresh = not job.seen
            job.seen = True
            if job.status == "done":
                if job.result:
                    gloss_entries[:] = [{"term": e.term, "korean": e.korean, "policy": e.policy}
                                        for e in ctrl.glossary_entries()]
                    gloss_rows.refresh()
                if fresh:
                    ui.notify(job.message, type="positive" if job.result else "info")
            elif fresh:
                ui.notify(job.error, type="negative")

        timer = ui.timer(0.5, tick)

    def translate_run_panel() -> None:
        """번역 패널 머리 — 실행 단추·모델·진행·실패 목록. 용어집 표는 여기서 여는 시트(대화상자)에."""
        def do_translate() -> None:
            # 체크를 푼 섹션에 보관된 번역이 없으면 이번 실행 뒤 영어로 바뀐다 — 그 전에 묻는다.
            # (보관된 번역이 있는 섹션은 run_translate가 그대로 두므로 여기 안 잡힌다.)
            losing = ctrl.unticked_losing_translation()
            if losing:
                names = [s.text for s in ctrl.manifest.sections if s.id in losing]
                with ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:480px"):
                    ui.label(t("체크를 푼 {n}개 섹션이 영어 원문으로 바뀝니다", n=len(losing))).classes("text-base font-medium")
                    ui.label(t("이 섹션들은 보관된 번역이 없어, 지금 실행하면 결과 문서에서 영어로 남습니다. "
                             "일부만 다시 번역하려던 것이라면 전체를 선택한 채 실행하세요 — 바뀐 섹션만 새로 "
                             "번역되고 나머지는 지난 번역을 그대로 씁니다.")).classes("text-sm text-gray-600")
                    ui.label(" · ".join(n[:40] for n in names[:8]) + (" …" if len(names) > 8 else "")) \
                        .classes("text-xs text-gray-500")
                    with ui.row().classes("justify-end gap-2 w-full"):
                        ui.button(t("취소"), on_click=dlg.close).props("flat dense no-caps")
                        ui.button(t("영어로 두고 진행"), on_click=lambda: (dlg.close(), _confirm_ko_edit())) \
                            .props("outline dense no-caps color=negative")
                        ui.button(t("전체 선택하고 번역"), on_click=lambda: (
                            dlg.close(), ctrl.set_all_translate(True), ctrl.save(),
                            _translate_refs["picker"].refresh(), _confirm_ko_edit())) \
                            .props("unelevated dense no-caps color=primary")
                dlg.open()
                return
            _confirm_ko_edit()

        def _confirm_ko_edit() -> None:
            # 번역문을 직접 고쳐 둔 게 있으면 덮어쓰기 전에 묻는다 (번역은 원문 재조립처럼 합칠 공통 조상이 없어 덮어쓴다)
            if not ctrl.has_manual_edit_ko():
                _start_translate()
                return
            with ui.dialog() as dlg, ui.card().classes("gap-2"):
                ui.label(t("직접 편집한 번역이 사라집니다")).classes("text-base font-medium")
                ui.label(t("번역을 실행하면 번역 마크다운을 새 결과로 다시 쓰기 때문에, 편집기에서 고친 "
                         "번역문이 덮어써집니다.")).classes("text-sm text-gray-600").style("max-width:420px")
                with ui.row().classes("justify-end gap-2 w-full"):
                    ui.button(t("취소"), on_click=dlg.close).props("flat dense no-caps")
                    ui.button(t("번역 실행"), on_click=lambda: (dlg.close(), _start_translate())) \
                        .props("unelevated dense no-caps color=negative")
            dlg.open()

        def _start_translate() -> None:
            gloss_autosave()
            try:
                # 고른 번역 모델 — 지난번과 다르면 캐시 키가 바뀌어 전체를 새 모델로 다시 번역한다
                p_name, _, m_name = (model_pick.value or ctrl.translate_model()).partition("/")
                prov = config.build_provider(p_name, m_name)
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return
            style = ctrl.manifest.korean_style

            if jobs.is_running(ctrl.wd.root, "reconvert"):
                ui.notify(t("다시 변환하는 중에는 번역할 수 없습니다 — 끝난 뒤 다시 눌러 주세요."), type="info")
                return
            # 워커 스레드에서 화면 없이 완결된다 — 탭을 옮겨 이 페이지가 사라져도 번역·저장은 끝까지 간다.
            # 홈 일괄 번역과 같은 작업 함수·같은 동시 실행 문을 쓴다(§translate_job).
            job = translate_job.start(ctrl.wd, prov, title=_title, style=style)
            if job is None:
                ui.notify(t("이 논문은 이미 번역 중입니다."), type="info")
                return
            translate_state.update(status={}, done=0, total=0, failures={})
            failure_panel.refresh()
            translate_state["active"] = True
            stale_panel.refresh()  # 도는 동안엔 '낡음' 표시를 치운다
            attach_translate(job)
            paper_tabs.refresh()  # 탭 아이콘이 '작업 중'으로 돈다

        def attach_translate(job: jobs.Job) -> None:
            """번역 작업을 이 화면에 붙인다 — 여기서 시작했든, 다른 탭에 갔다 돌아왔든."""
            translate_btn.disable()
            translate_state["active"] = True
            bar.visible = True
            status_chip.refresh()  # 헤더 칩 → '번역 중…'

            def tick() -> None:
                tv = job.total
                translate_state.update(done=job.done, total=tv, status=job.detail.get("status") or {})
                if job.running:
                    bar.value = job.fraction
                    prog_lbl.text = (t("번역 중… {done}/{total} 섹션 ({pct}%) · 병렬 처리 · 다른 탭으로 옮겨도 계속됩니다",
                                       done=job.done, total=tv, pct=int(job.fraction * 100)) if tv else
                                     t("대기 중 — 다른 번역이 끝나면 시작합니다 (동시에 {limit}편까지)",
                                       limit=translate_job.gate().limit)
                                     if job.detail.get("queued") else t("번역 준비 중…"))
                    update_tree_status()  # 섹션 트리 아이템에 진행중/완료 아이콘 반영
                    return
                timer.deactivate()
                finish_translate(job)

            # 진행 바 옆(고정 영역)에 단다 — '실패 섹션 재번역' 버튼은 failure_panel 안에 있고 시작하며
            # 그 패널을 새로 그리므로, 클릭 문맥에 달면 타이머가 지워진 슬롯에 붙어 진행이 멈춘다.
            with bar.parent_slot:
                timer = ui.timer(0.3, tick)
            tick()

        def finish_translate(job: jobs.Job) -> None:
            fresh = not job.seen  # 두 창이 같이 지켜봤으면 알림은 한 번만
            job.seen = True
            translate_state["active"] = False
            if job.status == "done":
                # 결과는 그 자리(캔버스)에 바로 — 번역 컬럼을 켜고 그리드·목차·툴바를 새 ko.md로 다시 그린다
                viewer_state["ko"] = True
                refresh_canvas()
                viewer_toolbar.refresh()
                translate_state["failures"] = (job.result or {}).get("failures", {})
                bar.value = 1
                prog_lbl.text = "✓ " + job.message
                if fresh:
                    ui.notify(job.message, type="positive")
            else:
                prog_lbl.text = t("실패: {error}", error=job.error)
                if fresh:
                    ui.notify(job.error, type="negative")
            update_tree_status()
            failure_panel.refresh()
            stale_panel.refresh()
            status_chip.refresh()
            translate_btn.enable()

        @ui.refreshable
        def stale_panel() -> None:
            """원문이 바뀐 섹션 — 재변환·레이아웃 수정·직접 편집 뒤 번역이 낡은 곳. 실행하면 그 섹션만 새로
            번역되고 나머지는 캐시를 쓴다(체크는 그대로 두어야 한다 — 풀면 그 섹션은 영어로 남는다)."""
            if translate_state["active"]:
                return
            stale = ctrl.stale_sections()
            if not stale:
                return
            with ui.column().classes("w-full gap-1 q-pa-sm").style(
                    "background:#fff7ed; border:1px solid #fdba74; border-radius:8px"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.icon("update", color="orange-7")
                    ui.label(t("원문이 바뀐 섹션 {n}개 — 번역이 낡았습니다", n=len(stale))) \
                        .classes("text-sm font-medium text-orange-9")
                    ui.space()
                    ui.button(t("바뀐 섹션 재번역"), icon="refresh", on_click=do_translate) \
                        .props("dense no-caps color=orange").tooltip(
                            t("바뀐 섹션만 새로 번역합니다 — 나머지는 지난 번역(캐시)을 그대로 씁니다"))
                names = [s.text for s in ctrl.manifest.sections if s.id in stale]  # 문서 순서대로
                ui.label(" · ".join(n[:40] for n in names[:8]) + (" …" if len(names) > 8 else "")) \
                    .classes("text-xs text-orange-800")
                ui.label(t("PDF에서 다시 변환했거나 레이아웃을 고쳤거나 원문을 직접 편집한 섹션입니다. 트리에서도 "
                         "주황 ↻ 아이콘으로 표시됩니다.")).classes("text-xs text-gray-500")

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
                    ui.label(t("번역 실패 {n}개 섹션 — 구조 검증 실패로 영어 원문 유지", n=len(fails))) \
                        .classes("text-sm font-medium text-orange-9")
                    ui.space()
                    ui.button(t("실패 섹션 재번역"), icon="refresh", on_click=do_translate) \
                        .props("dense no-caps color=orange").tooltip(t("실패한 섹션만 다시 번역합니다(성공분은 캐시 재사용)"))
                for sid, reason in fails.items():
                    txt = (label_by_id.get(sid, sid) or "")[:50]
                    ui.label(f"· {txt} — {reason}").classes("text-xs text-orange-800")
                ui.label(
                    t("번역문이 원문의 마크다운 구조(헤더·표·링크·수식 등)를 그대로 지켜야 하는데, 두 번 시도해도 "
                    "어긋나서 그 섹션은 영어를 그대로 뒀습니다. 재번역해도 계속 실패하면 표·수식이 복잡한 섹션이니 "
                    "번역 후 뷰어에서 직접 손봐도 됩니다.")).classes("text-xs text-gray-500")

        def after_collection_change() -> None:
            """컬렉션 용어집이 바뀌었다(대화상자를 닫았거나 에이전트가 썼다) — 위 구역·취소선·단추 글자·원문 바뀜."""
            coll_view.update(pid=ctrl.collection_id(), entries=ctrl.collection_glossary())
            coll_rows.refresh()
            gloss_rows.refresh()
            gloss_open_btn.refresh()
            refresh_staleness()  # 바뀐 용어가 나오는 섹션은 '원문 바뀜'이 된다

        coll_host = ui.element("div").style("display:none")  # 컬렉션 용어집 대화상자 자리 (refreshable 밖)

        def open_collection_dialog() -> None:
            from md4paper.ui import collection_glossary_dialog

            with coll_host:
                collection_glossary_dialog.open_collection_glossary(
                    state, coll_view["pid"], on_change=after_collection_change)

        @ui.refreshable
        def coll_rows() -> None:
            """용어집 시트 위 구역 — 주 컬렉션 용어집(읽기 전용). 미분류 논문이면 그리지 않는다."""
            pid, entries = coll_view["pid"], coll_view["entries"]
            if not pid:
                return
            name = projects.name_of(pid)
            with ui.column().classes("w-full gap-1 q-pa-sm").style(
                    "background:#f4f8fd; border:1px solid #d6e6f8; border-radius:8px"):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.icon("folder_special", color="primary", size="18px")
                    ui.label(t("컬렉션 '{name}' 용어집 ({n}) — 이 논문 용어집보다 우선", name=name, n=len(entries))
                             if entries else t("컬렉션 '{name}' 용어집 — 아직 없음", name=name)) \
                        .classes("text-sm font-medium min-w-0")
                    ui.space()
                    ui.button(t("컬렉션 용어집 열기…"), icon="open_in_new", on_click=open_collection_dialog) \
                        .props("flat dense no-caps color=primary").classes("shrink-0") \
                        .tooltip(t("주 컬렉션이 이 컬렉션인 논문 모두에 적용되는 용어집 — 논문들이 어긋나게 쓴 번역어를 여기서 통일합니다"))
                if not entries:
                    ui.label(t("이 컬렉션 논문들의 용어집에서 어긋난 번역어를 골라 통일하면 이 논문 번역에도 적용됩니다.")) \
                        .classes("text-xs text-gray-500")
                    return
                with ui.column().classes("w-full gap-0 md4-scroll").style("max-height:28vh; overflow-y:auto"):
                    for e in entries:
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.label(e.term).classes("text-sm w-40 shrink-0 truncate").tooltip(e.term)
                            ui.icon("arrow_forward", size="14px").classes("text-gray-400")
                            ui.label(f"{e.korean or t('(빈 칸)')} · {t(_POLICY.get(e.policy, e.policy))}") \
                                .classes("text-sm text-gray-700 truncate min-w-0")
                            ui.space()
                            example_button(e.term, coll_rows)
                        example_block(e.term, e.korean, e.policy)

        def on_glossary_refresh(e) -> None:  # noqa: ANN001 — GenericEventArguments
            """에이전트가 컬렉션 용어집을 썼다 — 이 논문의 주 컬렉션이면 다시 그린다.

            ui.on은 창 단위로 쌓이므로(화면을 갈아 끼워도 옛 핸들러가 남는다) 지워진 화면이면 그냥 둔다."""
            args = e.args if isinstance(e.args, dict) else {}
            if gloss_dialog.is_deleted or (args.get("collection") or "") != ctrl.collection_id():
                return
            after_collection_change()

        # 용어집 표는 넓다(원어·번역어·처리 방식) — 좁은 패널 대신 캔버스 위에 시트로 띄운다.
        # 번역 전에 한 번 집중해서 보는 단계라 상시 탭보다 이 편이 맞다.
        with ui.dialog() as gloss_dialog, ui.card().classes("gap-2").style(
                "width:760px; max-width:94vw; max-height:84vh"):
            with ui.row().classes("items-center w-full no-wrap gap-2"):
                ui.label(t("용어집")).classes("text-lg font-bold")
                ui.space()
                ui.button(t("행 추가"), icon="add", on_click=add_gloss_row).props("outline dense no-caps")
                ui.button(t("선택 섹션에서 용어 추가"), icon="playlist_add", on_click=add_terms_from_selected) \
                    .props("outline dense no-caps").tooltip(t("번역할 섹션으로 체크한 섹션에서 새 용어를 뽑아 추가"))
                ui.button(icon="close", on_click=gloss_dialog.close).props("flat dense round")
            ui.markdown(
                t("변환할 때 **Abstract·Introduction 본문과 모든 섹션 제목**에서 핵심 용어를 자동으로 뽑아 만듭니다. "
                "번역 전에 번역어와 처리 방식(번역 / 음역 / 원문 유지 / 첫 등장 시 병기)을 검토·수정하세요. "
                "고친 내용은 바로 저장됩니다.\n\n"
                "컬렉션 용어집의 항목은 여기서 못 고칩니다 — 컬렉션 용어집에서 고치면 주 컬렉션이 그 컬렉션인 논문 전체에 적용됩니다.")
            ).classes("text-xs text-gray-600")
            with ui.column().classes("w-full gap-1 md4-scroll").style("overflow-y:auto; min-height:0"):
                coll_rows()
                gloss_rows()
        ex_dialog["dlg"] = gloss_dialog
        gloss_dialog.on("hide", lambda: gloss_open_btn.refresh())
        ui.on(agent_panel.GLOSSARY_REFRESH_EVENT, on_glossary_refresh)

        @ui.refreshable
        def gloss_open_btn() -> None:
            n = sum(1 for d in gloss_entries if d["term"].strip())
            n_coll = len(coll_view["entries"])
            label = t("용어집 검토 ({n} + 컬렉션 {n_coll})", n=n, n_coll=n_coll) if n_coll else t("용어집 검토 ({n})", n=n)
            ui.button(label, icon="menu_book", on_click=gloss_dialog.open) \
                .props("outline no-caps color=primary").tooltip(t("번역에 쓸 용어집을 표로 검토·수정"))

        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                translate_btn = ui.button(t("번역 실행"), icon="translate", on_click=do_translate) \
                    .props("color=secondary no-caps").tooltip(t("체크한 섹션을 아래 용어집·번역 방식으로 번역합니다"))
                gloss_open_btn()
            model_pick = ui.select(ctrl.translate_model_options(), value=ctrl.translate_model(),
                                   label=t("번역 모델")).props("dense outlined options-dense").classes("w-full") \
                .tooltip(t("기본은 이 논문을 지난번에 번역한 모델입니다. 다른 모델을 고르면 캐시를 쓰지 않고 "
                         "전체를 그 모델로 다시 번역합니다(실패 섹션 재번역도 이 모델로)."))
            bar = ui.linear_progress(value=0, show_value=False) \
                .props("size=14px rounded color=secondary").classes("w-full")
            bar.visible = False
            prog_lbl = ui.label("").classes("text-sm text-gray-500")
            _translate_refs["stale"] = stale_panel  # 원문이 바뀌는 곳(편집 저장·레이아웃 수정)에서 갱신
            stale_panel()    # 원문이 바뀐 섹션 + 재번역 버튼 (없으면 안 보임)
            failure_panel()  # 번역 실패 섹션 이유 + 재번역 버튼 (실패 없으면 안 보임)
        running = jobs.get(ctrl.wd.root, "translate")
        if running is not None and running.running:  # 번역 중에 떠났다 돌아왔다 → 진행 바를 다시 붙인다
            attach_translate(running)
        if jobs.is_running(ctrl.wd.root, "terms"):
            attach_terms(jobs.get(ctrl.wd.root, "terms"))

        def on_agent_job(e) -> None:  # noqa: ANN001
            """에이전트가 이 논문에 번역·용어집 작업을 띄웠다 — 여기서 시작한 것처럼 붙는다. 진행 바,
            끝나면 캔버스·목차·툴바 다시 그리기(용어집이면 표 다시 읽기)와 완료 알림은 attach_*가 한다."""
            args = e.args if isinstance(e.args, dict) else {}
            if args.get("token") != tok or bar.is_deleted:
                return  # 다른 논문, 또는 화면을 갈아 끼우며 남은 옛 핸들러 (ui.on은 창 단위로 쌓인다)
            kind = args.get("job")
            job = jobs.get(ctrl.wd.root, kind) if kind in ("translate", "terms", "figures") else None
            if job is None:
                return
            if kind == "figures":  # 그림 속 글자 번역 — 끝나면 번역 열의 그림이 바뀐다
                with bar.parent_slot:
                    attach_figures(job)
                return
            if kind == "translate" and not translate_state["active"]:
                translate_state.update(status={}, done=0, total=0, failures={})
                failure_panel.refresh()
                stale_panel.refresh()
                attach_translate(job)
            elif kind == "terms":
                with bar.parent_slot:  # 타이머를 이 패널의 고정 영역에 단다 (attach_translate와 같은 까닭)
                    attach_terms(job)

        def attach_figures(job: jobs.Job) -> None:
            """그림 글자 번역 작업(에이전트가 띄움)을 지켜보다 끝나면 캔버스를 다시 그리고 알린다."""
            def tick() -> None:
                if job.running:
                    return
                timer.deactivate()
                fresh = not job.seen
                job.seen = True
                if job.status == "done":
                    refresh_canvas()
                if fresh:
                    ui.notify(job.message if job.status == "done" else t("그림 번역 실패: {error}", error=job.error),
                              type="positive" if job.status == "done" else "negative")

            timer = ui.timer(0.5, tick)

        if jobs.is_running(ctrl.wd.root, "figures"):
            with bar.parent_slot:
                attach_figures(jobs.get(ctrl.wd.root, "figures"))
        ui.on(agent_panel.WATCH_JOB_EVENT, on_agent_job)

    def _sec_page0(sid: str) -> int | None:
        # 섹션 자체 페이지가 없으면(run-in 헤더 등은 headings.json에 없어 page가 비어 있음)
        # 문서 순서상 '직전에 페이지가 있는 섹션'으로 근사한다 — 나란히 보기의 스크롤 싱크가
        # '위쪽 최근 data-page'로 PDF를 맞추는 것과 동일한 동작(그래서 둘 다 켜면 되던 것).
        pages = ctrl.section_pages()  # 쪽이 빈 소제목은 PDF에서 제목을 찾아 채운 쪽
        last_pg: int | None = None
        for s in m.sections:  # cands가 줄 순서로 정렬돼 sections도 문서 순서
            if pages.get(s.id):
                last_pg = int(pages[s.id]) - 1
            if s.id == sid:
                return last_pg
        return None

    async def goto_md(sid: str) -> None:
        """섹션 클릭(구조 패널) → 캔버스의 그 섹션으로.

        읽기 모드면 정렬 그리드의 그 행으로(PDF가 켜져 있으면 그 쪽으로도), 편집 모드면 편집기의 그 헤더
        줄로(옆 칸이 따라온다). 텍스트가 꺼지고 PDF만 켜져 있으면 PDF의 해당 쪽으로.
        임의로 다른 패널을 열지 않는다.
        """
        has_pdf = ctrl.pdf_page_count() > 0
        show_text = viewer_state["en"] or (viewer_state["ko"] and bool(ctrl.ko_markdown()))
        show_pdf = viewer_state["pdf"] and has_pdf
        pg = _sec_page0(sid)
        if edit_state["on"]:
            # 편집을 끄지 않는다 — 입력 맥락을 끊지 않는다. 편집기를 그 헤더로 옮기면 옆 칸(PDF·미리보기)이 따라온다.
            # 예전엔 미리보기의 sec- 앵커로 옮겼는데, 옆이 PDF면 앵커가 없어 아무 일도 일어나지 않았다.
            js = _edit_goto_js(sid)
            if not js:
                ui.notify(t("이 섹션은 지금 문서에 헤더로 남아 있지 않습니다."), type="info")
                return
            await ui.run_javascript(js)
            return
        js = ""
        if show_text:
            k = row_for_section(sid)
            if k is not None:
                js += (f"document.querySelector('.sbs-grid [data-row=\"{k}\"]')"
                       "?.scrollIntoView({behavior:'instant', block:'start'});")
        # 나란히(PDF도 표시)면 클릭 스크롤은 sync 이벤트를 확실히 못 태우므로 PDF도 직접 옮긴다
        if show_pdf and pg is not None:
            js += f" document.getElementById('pdfpage-{pg}')?.scrollIntoView({{behavior:'instant', block:'start'}});"
        if not js:
            ui.notify(t("이 섹션의 위치 정보가 없습니다."), type="info")
            return
        await ui.run_javascript(js)

    # ---------- 왼쪽 작업 패널 — 목차 / 구조 / 번역 / 설정 / 메모 ----------
    # 옛 '변환 탭 왼쪽 열'과 '번역 탭 왼쪽 열'이 여기로 들어왔다. 모드를 모두 지어 두고(작업 재부착·
    # refreshable 참조가 살아 있어야 한다) 보이는 것만 바꾼다. 레일의 같은 단추를 다시 누르면 접힌다.
    _PANELS = (("toc", N_("목차"), "toc"), ("structure", N_("구조"), "account_tree"),
               ("translate", N_("번역"), "translate"), ("settings", N_("설정"), "tune"),
               ("notes", N_("메모"), "edit_note"))
    root_key = str(ctrl.wd.root.resolve())
    panel_state: dict = {"mode": None, "splitter": None, "boxes": {}}
    # 패널 폭(%)은 모드마다 따로 — 목차는 한 줄짜리 제목 목록이라 좁게, 섹션 트리·설정은 넓게 시작한다.
    # 드래그한 폭은 모드별로 전역 설정([ui].panel_width_<모드>)에 기억해 다른 논문·다음 실행에도 그대로.
    _DEFAULT_PANEL_W = {"toc": 20, "structure": 30, "translate": 30, "settings": 30, "notes": 34}

    def panel_width(mode: str) -> float:
        v = config.load_config().get("ui", {}).get(f"panel_width_{mode}")
        return float(v) if isinstance(v, (int, float)) and 8 <= v <= 60 else _DEFAULT_PANEL_W.get(mode, 30)

    _width_save: dict = {"timer": None, "pending": {}}

    def remember_width(mode: str, value: float) -> None:
        """드래그 중엔 값이 쏟아지므로 모아 두었다가 잠잠해지면 한 번만 파일에 쓴다."""
        _width_save["pending"][mode] = int(round(value))
        tv = _width_save["timer"]
        if tv is not None:
            tv.cancel()

        def flush() -> None:
            _width_save["timer"] = None
            for k, v in _width_save["pending"].items():
                config.set_section_value("ui", f"panel_width_{k}", v)
            _width_save["pending"].clear()

        _width_save["timer"] = ui.timer(0.6, flush, once=True)

    # 메모 패널 — 논문 전체에 대한 자유 메모(paper.md4/notes.md). 상태는 파일뿐이고 UI는 모델을 갖지 않는다.
    # 타자가 0.8초쯤 멈추면 원자적으로 저장한다(remember_width와 같은 모아 쓰기). 패널을 떠나거나
    # 내보내기 직전에는 기다리지 않고 바로 쓴다 — 마지막 글자가 빠진 채 내려받히지 않게.
    # 메모 칸은 위아래로 둘까지 나눌 수 있다(탭을 끌어 아래 절반에 놓으면) — panes가 칸마다 보이는 탭.
    # pending은 탭 이름 → (아직 안 쓴 내용, 보낸 편집기 id, capture가 받은 js 또는 None): 두 칸에서 번갈아
    # 쳐도 각자 제 탭 파일로 간다. mem은 편집기 id → 얹을 때의 기록(raw·saved·stamp, notes_pick 참고) —
    # 쓰기 전에 파일 지문이 stamp와 다르면(그 사이 바깥에서 바뀜) 덮지 않고 conflicts에 두고 묻는다.
    notes_state: dict = {"timer": None, "pending": {}, "status": None, "panes": [], "focus": 0,
                         "ids": {},  # ids: 편집기 컨테이너 id → 그 편집기가 맡은 탭 이름
                         "mem": {}, "conflicts": {}, "on_conflict": None}

    def notes_write(tab: str, text: str, el_id: str | None, js: dict | None = None) -> bool:
        """편집기 하나의 내용을 그 탭 파일에 — 고친 게 있을 때만, 바깥에서 바뀌지 않았을 때만. 실패면 False."""
        mem = notes_state["mem"].get(el_id)
        if mem is not None and (mem.get("dead") or notes_pick(text, mem, js) is None):
            return True  # dead: '새로 불러오기'로 버린 편집기 — 늦게 오는 글자도 버린다(.trash에 있다)
        try:
            st = ctrl.save_notes(text, tab, expect=mem["stamp"] if mem is not None else notes_io.ANY)
        except notes_io.Conflict:
            notes_state["conflicts"][tab] = (text, el_id)
            notes_set_status(t("다른 곳에서 바뀜 — 저장 안 함"))
            cb = notes_state["on_conflict"]
            if cb is not None:
                cb(tab)
            else:
                ui.notify(t("‘{tab}’ 메모가 다른 곳에서 바뀌어 저장하지 않았습니다.", tab=tab), type="warning")
            return False
        except OSError as e:
            notes_set_status(t("저장 실패: {e}", e=e))
            return False
        if mem is not None:
            mem["saved"], mem["stamp"] = text, st
        return True

    def notes_flush() -> None:
        tv = notes_state["timer"]
        if tv is not None:
            tv.cancel()
            notes_state["timer"] = None
        pending, notes_state["pending"] = notes_state["pending"], {}
        if not pending:
            return
        ok = True
        for tab, (text, el_id, js) in pending.items():
            ok = notes_write(tab, text, el_id, js) and ok
        if ok:
            notes_set_status(t("저장됨"))

    def notes_set_status(text: str) -> None:
        lbl = notes_state["status"]
        if lbl is not None:
            lbl.set_text(text)

    def notes_typed(tab: str, text: str, el_id: str | None = None) -> None:
        notes_state["pending"][tab] = (text, el_id, None)  # 친 칸의 탭에 — 칸·탭을 바꿔도 섞이지 않게
        notes_set_status(t("저장 중…"))
        tv = notes_state["timer"]
        if tv is not None:
            tv.cancel()
        notes_state["timer"] = ui.timer(0.8, notes_flush, once=True)

    def set_panel(mode: str | None, toggle: bool = True) -> None:
        """패널 모드 바꾸기. toggle이면 이미 그 모드일 때 접는다(None = 접힘). 논문별로 기억한다."""
        if toggle and mode == panel_state["mode"]:
            mode = None
        prev = panel_state["mode"]
        if prev == "notes" and mode != "notes":
            notes_flush()
        panel_state["mode"] = mode
        if mode is not None:
            panel_state["last"] = mode  # 끌어서 다시 열 때 돌아갈 패널
        state.setdefault("panel_by_root", {})[root_key] = mode
        set_left_panel(ctrl.wd.root, mode)  # 앱을 다시 켜도 이 논문은 이 패널로
        for key, box in panel_state["boxes"].items():
            box.set_visibility(key == mode)
        sp = panel_state["splitter"]
        if sp is not None:
            if prev is not None and sp.value > 1:
                remember_width(prev, sp.value)
            sp.value = 0 if mode is None else panel_width(mode)
        rail.refresh()

    def open_side() -> None:
        """✎ 섹션 구조 점프의 목적지(구조 패널)가 안 보이면 연다 (안 보이는 곳으로 점프하지 않도록)."""
        set_panel("structure", toggle=False)

    ui.on("md4-open-side", lambda _: open_side())

    @ui.refreshable
    def rail() -> None:
        for key, label, icon in _PANELS:
            on = panel_state["mode"] == key
            b = ui.element("div").classes("md4-railbtn" + (" on" if on else ""))
            with b:
                ui.icon(icon, size="20px")
                ui.label(t(label))
            b.on("click", lambda _, k=key: set_panel(k))
            b.tooltip({"toc": t("목차 — 섹션으로 이동 (원문·번역 제목)"),
                       "structure": t("구조 — 헤더 레벨·섹션 트리·레이아웃 수정·다시 변환"),
                       "translate": t("번역 — 번역할 섹션·용어집·실행"),
                       "settings": t("설정 — 인용 표기·캡션·저자"),
                       "notes": t("메모 — 논문 전체에 대한 자유 메모 (위지윅, 자동 저장)")}[key]
                      + (t(" (누르면 접힘)") if on else ""))

    @ui.refreshable
    def status_chip() -> None:
        """헤더 오른쪽 — 이 논문이 어디까지 왔는지와 다음 할 일. 누르면 번역 패널로."""
        running = jobs.is_running(ctrl.wd.root, "translate")
        has_ko = bool(ctrl.ko_markdown())
        fails = len(translate_state.get("failures") or {})
        if running:
            icon, text, cls, tip = ("autorenew", t("번역 중…"), "md4-chip busy",
                                    t("번역이 돌고 있습니다 — 진행은 번역 패널에서"))
        elif has_ko:
            stale = len(ctrl.stale_sections())
            text = (t("번역됨") + (t(" · 실패 {n}", n=fails) if fails else "")
                    + (t(" · 원문 바뀜 {n}", n=stale) if stale else ""))
            icon, cls = ("warning", "md4-chip warn") if (fails or stale) else ("check_circle", "md4-chip done")
            tip = (t("구조 검증에 실패해 영어로 남은 섹션이 있습니다 — 번역 패널에서 재번역") if fails
                   else t("원문이 바뀐 섹션이 있어 번역이 낡았습니다 — 번역 패널에서 바뀐 섹션만 재번역") if stale
                   else t("번역이 있습니다 — 다시 번역하거나 섹션을 바꾸려면 번역 패널로"))
        else:
            icon, text, cls, tip = ("translate", t("번역 안 됨 · 번역하기"), "md4-chip todo",
                                    t("변환은 끝났습니다. 다음은 번역 — 누르면 번역 패널이 열립니다"))
        chip = ui.element("div").classes(cls)
        with chip:
            ui.icon(icon, size="15px").classes("md4-spin" if running else "")
            ui.label(text)
        chip.on("click", lambda: set_panel("translate", toggle=False)).tooltip(tip)

    # ---------- 패널 공통 ----------

    def cap(text: str) -> None:
        ui.label(text).classes("text-xs text-gray-500").style("margin:-6px 0 4px 2px")

    def convert_settings() -> None:
        """변환(원어 마크다운) 관련 설정만."""

        ui.label(t("인용 · 참고문헌")).classes("font-semibold text-sm text-primary")

        # 표기 설정을 바꾸면 원본에서 다시 렌더 → 새 표기로 인용 재적용 (평문 [n]에서 다시 링크)
        def reapply() -> None:
            def apply() -> None:
                commit()  # 자동 저장 + rerender (raw→평문→새 표기 링크)
                refresh_canvas()

            apply()

        # 참고문헌은 변환 시 API 키가 있으면 자동 파싱된다. 이 버튼은 (1) 키가 없어 자동
        # 파싱이 건너뛰어졌을 때의 수동 실행, (2) 표기·프롬프트를 바꾼 뒤 다시 뽑기용 폴백이다.
        def do_parse() -> None:
            try:
                prov = config.build_provider()
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return

            def work(job: jobs.Job) -> dict:  # 화면 없이 완결 — 탭을 옮겨도 파싱·링크 적용은 끝까지
                summary = ctrl.parse_references(prov)
                job.message = t("참고문헌 {parsed}개 파싱 · 본문 인용 {linked}곳 링크 (≈${cost:.4f})",
                                parsed=summary.get("parsed", 0), linked=summary.get("linked", 0),
                                cost=summary.get("cost_usd", 0))
                return summary

            job = jobs.start(ctrl.wd.root, "cite", t("참고문헌 파싱"), work, title=_title)
            if job is None:
                ui.notify(t("이미 참고문헌을 파싱하는 중입니다."), type="info")
                return
            ui.notify(t("참고문헌 파싱 중… (LLM) — 다른 탭으로 옮겨도 계속됩니다"))
            attach_parse(job)
            paper_tabs.refresh()

        def attach_parse(job: jobs.Job) -> None:
            parse_control.refresh()

            def tick() -> None:
                if job.running:
                    return
                timer.deactivate()
                fresh = not job.seen
                job.seen = True
                if job.status == "done":
                    refresh_canvas()
                    push_cite_tips()  # 새로 파싱한 서지정보를 호버 툴팁 맵에 반영
                    if fresh:
                        ui.notify(job.message, type="positive")
                elif fresh:
                    ui.notify(job.error, type="negative")
                parse_control.refresh()

            timer = ui.timer(0.5, tick)

        @ui.refreshable
        def parse_control() -> None:
            if jobs.is_running(ctrl.wd.root, "cite"):
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    ui.label(t("참고문헌 파싱 중…")).classes("text-xs text-gray-500")
                return
            if ctrl.has_parsed_refs():
                # 이미 파싱됨(보통 변환 때 자동) — 조용히 표시 + 재파싱만 제공
                with ui.row().classes("items-center gap-1"):
                    ui.icon("check_circle", size="16px").classes("text-green-600")
                    ui.label(t("참고문헌 파싱됨")).classes("text-xs text-gray-500")
                    ui.button(t("다시 파싱"), icon="refresh", on_click=do_parse) \
                        .props("flat dense no-caps size=sm").tooltip(
                            t("표기·프롬프트를 바꾼 뒤 새로 뽑고 싶을 때만 (LLM 재호출)"))
            else:
                ui.label(t("참고문헌이 아직 파싱되지 않았습니다 (변환 시 API 키가 없었을 수 있어요).")) \
                    .classes("text-xs text-orange-600")
                ui.button(t("참고문헌 파싱 (LLM)"), icon="link", on_click=do_parse).props("dense outline")

        parse_control()
        if jobs.is_running(ctrl.wd.root, "cite"):  # 파싱 중에 떠났다 돌아왔다 → 끝나면 이 화면에 반영
            attach_parse(jobs.get(ctrl.wd.root, "cite"))

        ui.label(t("본문 인용 표기 (조합 가능)")).classes("text-sm")
        parts_state = {p: (p in m.citation_parts) for p in ("number", "authoryear", "short")}

        def update_parts() -> None:
            chosen = [p for p in ("number", "authoryear", "short") if parts_state[p]]
            ctrl.set_setting("citation_parts", chosen or ["number"])
            reapply()

        for pkey, plabel in (("number", t("번호  [1]")),
                             ("authoryear", t("저자·연도  (Vaswani et al. 2017)")),
                             ("short", t("약칭  (Transformer)"))):
            ui.checkbox(plabel, value=parts_state[pkey],
                        on_change=lambda e, k=pkey: (parts_state.__setitem__(k, e.value), update_parts())).props("dense")
        cap(t("여러 개 고르면 함께 표시됩니다 — 예: 번호+약칭 → [1, Transformer]. 모두 참고문헌으로 링크."))
        ui.switch(t("참고문헌에 DOI/arXiv 링크 달기"), value=m.reference_links,
                  on_change=lambda e: (ctrl.set_setting("reference_links", e.value), reapply()))
        cap(t("참고문헌 제목을 클릭하면 원문 논문으로 바로 이동합니다."))

        ui.separator()
        ui.label(t("그림·표")).classes("font-semibold text-sm text-primary")

        def set_capstyle(v: str) -> None:
            def apply() -> None:
                ctrl.set_setting("caption_style", v)
                commit()  # 재조립 → 새 캡션 표기로 캔버스 갱신
                capstyle_picker.refresh()
                refresh_canvas()

            apply()

        ui.label(t("그림·표 캡션 표기")).classes("text-sm")

        @ui.refreshable
        def capstyle_picker() -> None:
            # 마크다운 문법이 아니라 렌더된 모습을 보여주고 클릭해 선택
            for key, example in ((k, i18n.html(v)) for k, v in _CAPSTYLE_EX.items()):
                sel = ctrl.manifest.caption_style == key
                row = ui.row().classes(
                    "items-center gap-2 w-full no-wrap q-px-sm q-py-xs rounded-borders cursor-pointer"
                ).style("background: rgba(35,131,226,.12)" if sel else "background: transparent")
                with row:
                    ui.icon("radio_button_checked" if sel else "radio_button_unchecked", size="18px") \
                        .classes("text-primary" if sel else "text-grey-5")
                    ui.html(example).classes("text-sm").tooltip(t(_CAPSTYLE_NAME[key]))
                row.on("click", lambda e, k=key: set_capstyle(k))

        capstyle_picker()
        cap(t("캡션을 본문과 구별되게 표시합니다 (그림·표 공통)."))

        ui.separator()
        ui.label(t("저자")).classes("font-semibold text-sm text-primary")
        ui.label(t("저자 표기 (이름은 항상 표시)")).classes("text-sm")
        apar = {p: (p in m.author_parts) for p in ("email", "affiliation")}

        def update_author_parts() -> None:
            chosen = [p for p in ("email", "affiliation") if apar[p]]
            changed = ctrl.set_author_parts(chosen)  # 저장된 구조화 저자로 raw.md 저자 블록만 재렌더
            commit()  # 저장 + 재조립
            refresh_canvas()
            if not changed and not ctrl.wd.authors_json.exists():
                ui.notify(t("이 논문엔 구조화된 저자 정보가 없어(옛 변환·키 없음) 표시만 저장됩니다. "
                          "재변환하면 반영됩니다."), type="info")

        for akey, alabel in (("email", t("이메일")), ("affiliation", t("소속"))):
            ui.checkbox(alabel, value=apar[akey],
                        on_change=lambda e, k=akey: (apar.__setitem__(k, e.value), update_author_parts())) \
                .props("dense")
        cap(t("둘 다 끄면 이름만 표시됩니다. 저자 정보는 변환 시 AI가 저자별로 정리한 것입니다."))

    def translate_settings() -> None:
        """번역 관련 설정 — 방식은 아코디언, 번역할 섹션이 주인공."""
        def set_tsetting(field: str, value) -> None:  # noqa: ANN001
            """번역 설정 변경 → sections.yaml에 즉시 저장 (새로고침해도 유지)."""
            ctrl.set_setting(field, value)
            ctrl.save()

        def translate_method_panel() -> None:
            with ui.expansion(t("번역 방식 (문체·범위)"), icon="tune").classes("w-full").props("dense"):
                lang = ctrl.wd.lang
                opts = langs.style_options(lang)
                cur = langs.fit_style(lang, m.korean_style, config.resolve_style(lang))  # 다른 언어 문체가 남았으면
                if cur not in opts:  # custom:"…" — 목록에 넣어 둬야 값이 지워지지 않는다
                    opts = {**opts, cur: cur}
                ui.select(opts, value=cur, label=t("번역 문체 · {native}", native=langs.get(lang).native),
                          on_change=lambda e: set_tsetting("korean_style", e.value)).props("dense outlined").classes("w-full")
                cap(t("문장 끝맺음 말투. 논문은 보통 해라체(~한다)를 씁니다.") if lang == "ko" else
                    t("번역 언어는 {native}입니다 — 설정 › 변환·번역에서 새 논문의 언어를 바꿉니다.", native=langs.get(lang).native))
                if ctrl.title_section() is not None:
                    ui.switch(t("논문 제목도 번역"), value=ctrl.translate_title(),
                              on_change=lambda e: (ctrl.set_translate_title(e.value), ctrl.save()))
                    cap(t("문서 맨 위 논문 제목 한 줄. 끄면 영어 원제를 그대로 둡니다."))
                ui.switch(t("섹션 제목도 번역"), value=m.translate_headers,
                          on_change=lambda e: set_tsetting("translate_headers", e.value))
                cap(t("끄면 헤더(3.1 Method 등)는 영어 그대로 두고 본문만 번역합니다."))
                ui.switch(t("참고문헌 목록도 번역"), value=m.translate_references,
                          on_change=lambda e: set_tsetting("translate_references", e.value))
                cap(t("보통은 끄는 게 좋습니다 — 서지 정보(저자·제목)는 영어 원문이 정확합니다."))
                cap(t("Section/Figure/Table 같은 상호참조는 항상 영어로 유지됩니다."))

        # --- 번역할 섹션 고르기 (Tree Select) — 주 영역 ---
        ui.label(t("번역할 섹션")).classes("font-semibold text-sm text-primary")
        cap(t("체크한 섹션만 번역합니다 (LLM 비용·시간 절약). 체크를 풀어도 이미 번역해 둔 섹션은 그대로 남고, "
            "번역한 적 없는 섹션만 영어로 남습니다. 섹션 단위로 병렬 번역됩니다. 제목을 누르면 캔버스의 그 섹션으로 갑니다."))

        @ui.refreshable
        def section_picker() -> None:
            nodes = ctrl.section_tree()
            if not nodes:
                ui.label(t("선택할 섹션이 없습니다.")).classes("text-xs text-gray-400")
                return

            def on_tick(e) -> None:  # noqa: ANN001 — NiceGUI 이벤트
                ctrl.set_translate_ids(e.value)  # NiceGUI가 _props['ticked']는 자동 동기화함
                ctrl.save()  # 선택도 즉시 저장 (새로고침해도 유지)
                update_tree_status()  # 해제된 섹션에 '–' 표시 갱신
                count_label.refresh()

            # 초기 체크 상태는 _props['ticked']로만 설정 (별도 :ticked 바인딩을 주면 충돌해 tick이 깨진다)
            async def on_select(e) -> None:  # noqa: ANN001 — 제목 클릭 → 캔버스의 그 섹션으로 (체크박스와 별개)
                if e.value:
                    await goto_md(e.value)
                    tree.props(remove="selected")  # 선택 강조는 남기지 않는다 — 이동 단추로만 쓴다
                    tree._props["selected"] = None
                    tree.update()

            tree = ui.tree(nodes, label_key="label", node_key="id", tick_strategy="strict",
                           on_tick=on_tick, on_select=on_select)
            tree._props["ticked"] = ctrl.ticked_ids()
            # ui.tree는 nodes를 복사해 _props['nodes']에 넣는다 → 원본을 바꿔도 클라이언트에 안 나간다.
            # 실제 렌더되는 복사본을 가리켜 변형해야 tree.update()로 아이콘이 반영된다.
            tree_ref["tree"], tree_ref["nodes"] = tree, tree._props["nodes"]
            _apply_tree_status(tree._props["nodes"], translate_state["status"], _unticked_ids(),
                               set() if translate_state["active"] else ctrl.stale_sections())

            @ui.refreshable
            def count_label() -> None:
                counts = len(ctrl.translatable_sections())
                ui.label(t("{n} / {counts} 섹션 선택됨", n=len(ctrl.ticked_ids()), counts=counts)).classes("text-xs text-gray-500")
            count_label()

        with ui.row().classes("gap-2 items-center"):
            ui.button(t("전체 선택"), on_click=lambda: (ctrl.set_all_translate(True), ctrl.save(),
                                                 section_picker.refresh())).props("outline dense")
            ui.button(t("전체 해제"), on_click=lambda: (ctrl.set_all_translate(False), ctrl.save(),
                                                 section_picker.refresh())).props("outline dense")
        _translate_refs["picker"] = section_picker  # 구조 패널에서 레벨을 바꾸면 갱신되도록 등록
        section_picker()  # 번역 중 진행 상태는 트리 아이템의 아이콘으로 직접 표시된다

        ui.separator().classes("my-2")
        translate_method_panel()  # 번역 방식 설정은 섹션 선택 아래에 (덜 자주 만지므로 아코디언)

    async def download_en() -> None:
        if not ctrl.en_markdown():
            ui.notify(t("영어 마크다운이 없습니다 — 먼저 변환하세요."), type="warning")
            return
        name, data = ctrl.export_zip("en", config.resolve_export_target())  # 마크다운(형식 변환) + images/ zip
        await desktop.deliver(name, data)

    async def download_ko() -> None:
        if not ctrl.ko_markdown():
            ui.notify(t("아직 번역 결과가 없습니다."), type="warning")
            return
        name, data = ctrl.export_zip("ko", config.resolve_export_target())
        await desktop.deliver(name, data)

    async def download_notes() -> None:
        """하이라이트·메모를 마크다운 한 장으로 — 맨 위에 메모 패널의 자유 메모, 그 아래 인용구 + 메모."""
        await ui.run_javascript("window.__mdAnnoFlush && window.__mdAnnoFlush()")
        await asyncio.sleep(0.3)  # 클라이언트의 되저장이 파일에 닿을 짬
        notes_flush()  # 메모 패널의 기다리던 자동 저장도 — 마지막 글자까지 담기게
        if not annotations.has_any(ctrl.wd):
            ui.notify(t("표시해 둔 하이라이트·메모가 없습니다."), type="warning")
            return
        name, data = annotations.export_bytes(ctrl.wd, m.title or "")
        await desktop.deliver(name, data, media_type="text/markdown")

    async def save_to_library() -> None:
        """이 논문의 자리에 결과 마크다운을 저장 (자동 저장을 꺼 뒀을 때도 수동으로).

        자리는 그 논문이 속한 프로젝트의 폴더 > 공통 저장 위치 순으로 정해진다.
        """
        from nicegui import run as ng_run

        if not library.configured(library.project_of(ctrl.wd)):
            ui.notify(t("저장 위치가 지정돼 있지 않습니다 — 홈의 ⚙ 설정 → '저장 위치'나 컬렉션 폴더를 정하세요."),
                      type="warning")
            return
        try:
            saved = await ng_run.io_bound(library.export_paper, ctrl.wd)
        except OSError as ex:
            ui.notify(t("저장 실패: {ex}", ex=ex), type="negative")
            return
        if not saved:
            ui.notify(t("저장할 마크다운이 없습니다."), type="warning")
            return
        ui.notify(t("저장됨 · ") + " · ".join(str(p) for p in saved), type="positive")

    def library_button() -> None:
        """저장 위치(프로젝트 폴더 또는 공통)가 정해져 있을 때만 보이는 '폴더로 저장' 버튼."""
        pid = library.project_of(ctrl.wd)
        if not library.configured(pid):
            return
        dests = " · ".join(str(d) for d in library.dirs_for(pid).values() if d)
        # 프로젝트에 속한 논문은 '어느 묶음의 폴더로 가는지'가 먼저 궁금하다 → 이름을 앞에 붙인다.
        tip = (t("컬렉션: {name} → {dests}", name=projects.name_of(pid), dests=dests) if pid
               else t("저장 위치: {dests}", dests=dests))
        ui.button(t("폴더로 저장"), icon="drive_file_move", on_click=save_to_library) \
            .props("flat dense no-caps color=grey-8").tooltip(tip)

    # 내보내기 형식 드롭다운 — 캔버스 툴바의 내보내기 메뉴 안에 두되 전역 설정(config)을 그대로 쓴다.
    # 공유 refreshable이라 여기서 바꾸면 홈의 선택도 같은 값으로 동기화된다.
    @ui.refreshable
    def export_fmt_review() -> None:
        def _pick(e) -> None:  # noqa: ANN001
            config.set_section_value("output", "export_target", e.value)
            export_fmt_review.refresh()
            ui.notify(t("내보내기 형식 · {v}", v=t(_EXPORT_TARGET[e.value])), type="positive", timeout=1200)
        ui.select(_tr(_EXPORT_TARGET), value=config.resolve_export_target(), on_change=_pick) \
            .props("dense outlined").classes("w-28").tooltip(t("다운로드 zip에 적용하는 형식 (전역 설정)"))

    # --- 레이아웃 자동 수정 (구조 패널) — 깨진 헤더·수식·문단을 처음부터 끝까지 훑어 AI로 고친다 ---
    def _after_relayout() -> None:
        """raw.md·구조가 통째로 바뀌었으므로 이 화면의 파생 뷰를 전부 다시 그린다."""
        section_list.refresh()
        batch_level_panel.refresh()
        _refresh_translate_tree()
        refresh_canvas()
        viewer_toolbar.refresh()  # '수동 편집됨' 배지 등
        refresh_staleness()  # 원문이 바뀌었으니 어느 섹션의 번역이 낡았는지 다시 센다
        push_cite_tips()  # 재조립하며 인용 링크가 다시 붙었다 → 호버 툴팁 맵 갱신

    with ui.dialog() as fix_dialog, ui.card().classes("gap-3").style("width:560px;max-width:92vw"):
        ui.label(t("레이아웃 자동 수정")).classes("text-lg font-bold")
        ui.markdown(
            t("PDF에서 뽑은 마크다운을 **처음부터 끝까지 나눠 훑으며** AI가 레이아웃만 고칩니다 — "
            "쪼개진 헤더(`## 2` + `## Background` → `## 2 Background`), 깨진 수식(`x t` → `$x_t$`), "
            "끊긴 문단·목록·표 같은 것들. **글의 내용(단어)은 바꾸지 않습니다.**\n\n"
            "고친 문서로 **섹션 트리와 일괄 레벨 조정도 다시 만들어집니다** "
            "(지금 정해 둔 레벨·번역 여부·변환 설정은 그대로 이어받습니다).")
        ).classes("text-sm")
        fix_info = ui.label("").classes("text-xs text-gray-500")
        fix_prompt = ui.textarea(
            label=t("추가 지시 (선택)"),
            placeholder=t("예) 페이지마다 반복되는 학회명 머리글 줄을 지워줘 / 의사코드 블록은 코드 펜스로 묶어줘"),
        ).props('outlined autogrow input-style="min-height:66px"').classes("w-full")
        fix_warn = ui.label("").classes("text-xs text-orange-8")
        fix_bar = ui.linear_progress(value=0, show_value=False) \
            .props("size=14px rounded").classes("w-full")
        fix_bar.visible = False
        fix_status = ui.label("").classes("text-sm text-gray-600")

        def run_layout_fix() -> None:
            try:
                prov = config.build_provider()  # 변환에 쓰던 기본 프로바이더
            except RuntimeError as e:
                ui.notify(str(e), type="negative")
                return
            instructions = fix_prompt.value or ""

            # 화면 없이 완결 — 탭을 옮겨도 수정·재조립·자동 저장은 끝까지 간다.
            def work(job: jobs.Job) -> dict:
                summary = ctrl.fix_layout(prov, instructions, job.progress)
                if not summary.get("changed"):
                    job.message = t("고칠 레이아웃을 찾지 못했습니다 — 문서는 그대로입니다.")
                    return summary
                library.auto_export(ctrl.wd)
                msg = t("레이아웃 수정 완료 · {chunks}구간 중 {fixed}개 수정 (≈${cost:.4f})",
                        chunks=summary["chunks"], fixed=summary["fixed"], cost=summary["cost_usd"])
                if summary["kept"]:
                    msg += t(" · {n}개 구간은 내용 보존 검증에 걸려 원문 유지", n=summary["kept"])
                job.message = msg
                return summary

            job = jobs.start(ctrl.wd.root, "layout", t("레이아웃 수정"), work, title=_title)
            if job is None:
                ui.notify(t("이미 레이아웃을 수정하는 중입니다."), type="info")
                return
            attach_layout_fix(job, own=True)
            paper_tabs.refresh()

        def attach_layout_fix(job: jobs.Job, own: bool) -> None:
            """레이아웃 수정 진행을 이 화면에 붙인다.

            own=False(돌아와서 다시 붙은 화면)면 이 화면의 섹션 트리는 수정 전 문서로 지어진 것이라,
            끝나면 페이지를 새로 읽어 고친 문서로 다시 짓는다 — 낡은 매니페스트로 저장하면 수정이 덮인다.
            """
            for w in (fix_apply, fix_cancel, fix_undo, fix_prompt):
                w.disable()
            fix_dialog.props(add="persistent")  # 도는 동안 바깥 클릭·ESC로 닫히지 않게
            fix_bar.visible = True
            fix_hide.set_visibility(True)

            def tick() -> None:  # 워커 스레드가 job을 갱신 → 타이머가 폴링 (NiceGUI 스레드 안전)
                if job.running:
                    tv = job.total
                    fix_bar.value = job.fraction
                    prog = (t("{done}/{total} 구간 ({pct}%)", done=job.done, total=tv, pct=int(job.fraction * 100))
                            if tv else "")
                    fix_status.text = t("수정 중… {prog}", prog=prog) if tv else t("구간을 나누는 중…")
                    fix_banner.text = t("레이아웃 자동 수정 중… {prog}", prog=prog if tv else t("구간을 나누는 중…"))
                    fix_banner.set_visibility(True)
                    return
                timer.deactivate()
                fresh = not job.seen
                job.seen = True
                fix_dialog.props(remove="persistent")
                fix_bar.visible = False
                fix_hide.set_visibility(False)
                fix_banner.set_visibility(False)
                for w in (fix_apply, fix_cancel, fix_prompt):
                    w.enable()
                fix_undo.enable()
                if job.status == "done":
                    changed = (job.result or {}).get("changed")
                    if fresh:
                        ui.notify(job.message, type="positive" if changed else "info")
                    if changed and not own:
                        ui.navigate.reload()
                        return
                    if changed:
                        _after_relayout()
                    fix_dialog.close()
                else:
                    if fresh:
                        ui.notify(job.error, type="negative")
                    fix_status.text = t("실패: {error}", error=job.error)
                fix_undo.set_visibility(ctrl.can_undo_layout_fix())

            timer = ui.timer(0.3, tick)
            tick()

        async def undo_layout_fix() -> None:
            from nicegui import run

            if not await run.io_bound(ctrl.undo_layout_fix):
                ui.notify(t("되돌릴 이전 상태가 없습니다."), type="warning")
                return
            _after_relayout()
            await run.io_bound(library.auto_export, ctrl.wd)  # 저장 위치의 사본도 되돌린 문서로
            ui.notify(t("레이아웃 수정 전으로 되돌렸습니다."), type="positive")
            fix_dialog.close()

        with ui.row().classes("items-center w-full gap-2"):
            fix_undo = ui.button(t("직전 상태로 되돌리기"), icon="undo", on_click=undo_layout_fix) \
                .props("flat dense no-caps color=grey-8").tooltip(t("마지막 수정 직전의 마크다운·구조로 복구"))
            ui.space()
            fix_cancel = ui.button(t("취소"), on_click=fix_dialog.close).props("flat no-caps")
            # 도는 동안엔 대화상자를 닫을 수 없다(persistent) → 숨기기만 — 작업은 서버에서 계속된다
            fix_hide = ui.button(t("숨기기"), on_click=fix_dialog.close).props("flat no-caps") \
                .tooltip(t("작업은 계속됩니다 — 다른 탭으로 옮겨도 됩니다"))
            fix_hide.set_visibility(False)
            fix_apply = ui.button(t("적용"), icon="auto_fix_high", on_click=run_layout_fix) \
                .props("color=primary no-caps")

    def open_fix_dialog() -> None:
        if jobs.is_running(ctrl.wd.root, "reconvert"):
            ui.notify(t("다시 변환하는 중에는 레이아웃을 수정할 수 없습니다."), type="info")
            return
        plan = ctrl.layout_fix_plan()
        prov = config.resolve_provider()
        fix_info.text = t("본문 {chars:,}자를 {chunks}개 구간으로 나눠 {provider} {model}에 보냅니다.",
                          chars=plan["chars"], chunks=plan["chunks"], provider=_short_provider(prov),
                          model=config.resolve_model(prov))
        fix_warn.text = (t("직접 편집한 마크다운이 있습니다 — 문서를 다시 만들면서 그 편집은 사라집니다.")
                         if ctrl.has_manual_edit() else "")
        fix_status.text = ""
        fix_undo.set_visibility(ctrl.can_undo_layout_fix())
        fix_dialog.open()

    # --- 재변환 (구조 패널) — 원본 PDF에서 추출부터 다시. 추출기 개선을 이미 변환한 논문에 반영한다 ---
    # 원문이 통째로 바뀌므로 번역·레이아웃 수정과 겹쳐 돌면 서로의 결과를 덮는다 → 하나라도 돌면 막는다.
    _REWRITES = (("translate", N_("번역")), ("layout", N_("레이아웃 수정")), ("reconvert", N_("다시 변환")))

    with ui.dialog() as rc_dialog, ui.card().classes("gap-3").style("width:520px;max-width:92vw"):
        ui.label(t("PDF에서 다시 변환")).classes("text-lg font-bold")
        ui.markdown(
            t("원본 PDF에서 **추출부터 다시** 해서 영어 마크다운을 새로 만듭니다. 추출기가 나아진 점"
            "(굵게·기울임, 띄어쓰기, 수식 등)이 이 논문에 반영됩니다.\n\n"
            "정해 둔 **헤더 레벨·번역할 섹션·변환 설정은 그대로 이어받고**, 메모는 바뀐 문장에서 다시 찾습니다. "
            "마음에 들지 않으면 '레이아웃 자동 수정' 창의 **직전 상태로 되돌리기**로 돌아갈 수 있습니다.")
        ).classes("text-sm")
        rc_warn = ui.column().classes("gap-1")
        with ui.row().classes("justify-end w-full gap-2"):
            ui.button(t("취소"), on_click=rc_dialog.close).props("flat no-caps")
            ui.button(t("다시 변환"), icon="refresh", on_click=lambda: (rc_dialog.close(), run_reconvert())) \
                .props("color=primary no-caps")

    def open_reconvert_dialog() -> None:
        busy = [t(label) for kind, label in _REWRITES if jobs.is_running(ctrl.wd.root, kind)]
        if busy:
            ui.notify(t("{v} 중에는 다시 변환할 수 없습니다 — 끝난 뒤 다시 눌러 주세요.", v=busy[0]), type="info")
            return
        rc_warn.clear()
        with rc_warn:
            if ctrl.has_manual_edit():
                ui.label(t("직접 편집한 마크다운이 있습니다 — 다시 변환하면 그 편집은 사라집니다.")) \
                    .classes("text-xs text-orange-8")
            if ctrl.ko_markdown():
                ui.label(t("번역은 그대로 둡니다. 새 원문을 번역에 반영하려면 변환 뒤 번역을 다시 실행하세요.")) \
                    .classes("text-xs text-gray-600")
            if not _has_llm_key():
                ui.label(t("API 키가 없어 저자·수식 정리는 규칙으로만 합니다.")).classes("text-xs text-gray-600")
        rc_dialog.open()

    def run_reconvert() -> None:
        prov = None
        if _has_llm_key():
            try:
                prov = config.build_provider()  # 저자·서지 정리와 수식 변환에 (처음 변환과 같게)
            except RuntimeError:
                prov = None

        # 화면 없이 완결 — 탭을 옮겨도 변환·조립·자동 저장은 끝까지 간다
        def work(job: jobs.Job) -> dict:
            summary = ctrl.reconvert(prov, job.progress)
            library.auto_export(ctrl.wd)
            msg = t("다시 변환했습니다 · 섹션 {sections}개 · 굵게·기울임 {emphasis}곳",
                    sections=summary["sections"], emphasis=summary["emphasis"])
            if summary["has_translation"]:
                msg += t(" · 번역은 다시 실행해야 반영됩니다")
            job.message = msg
            return summary

        job = jobs.start(ctrl.wd.root, "reconvert", t("다시 변환"), work, title=_title)
        if job is None:
            ui.notify(t("이미 다시 변환하는 중입니다."), type="info")
            return
        ui.notify(t("PDF에서 다시 변환하는 중… — 다른 탭으로 옮겨도 계속됩니다"))
        attach_reconvert(job)
        paper_tabs.refresh()

    _RC_STEPS = (N_("PDF에서 추출 중…"), N_("수식 정리 중…"), N_("저자·서지 정리 중…"), N_("섹션 구조를 다시 잡는 중…"),
                 N_("인용 링크 다는 중…"), N_("마무리 중…"))

    def attach_reconvert(job: jobs.Job) -> None:
        """진행을 버튼 아래 줄에 보여 주고, 끝나면 페이지를 새로 읽는다 — 원문·그림·구조가 통째로 바뀌었다."""
        rc_button.disable()
        rc_banner.set_visibility(True)

        def tick() -> None:
            if job.running:
                rc_banner.text = t("다시 변환 중 · {step}", step=t(_RC_STEPS[min(job.done, len(_RC_STEPS) - 1)]))
                return
            timer.deactivate()
            rc_banner.set_visibility(False)
            rc_button.enable()
            if job.status == "done":
                ui.navigate.reload()  # 완료 알림은 새로 읽은 페이지가 '못 본 작업'으로 띄운다
            elif not job.seen:
                job.seen = True
                ui.notify(t("다시 변환 실패: {error}", error=job.error), type="negative")

        timer = ui.timer(0.5, tick)
        tick()

    def notes_panel() -> None:
        """메모 패널 속 — 머리(제목 · 저장 상태) + 탭 줄 + 위지윅 편집기(Milkdown Crepe) 한두 칸.

        탭 하나가 마크다운 파일 하나다(§md4paper/notes.py). 편집기는 브라우저에만 있고 파이썬은 바뀐
        마크다운을 받아 그 칸의 탭 파일에 쓰기만 한다. 탭을 끌어 메모 칸의 아래(위) 절반에 놓으면 칸이
        위아래로 나뉘어 두 메모를 함께 보며 쓴다 — 칸은 둘까지, 같은 탭은 한 칸에만. 탭을 누르면 마지막에
        만진 칸에 열린다. 칸 배치(어느 탭이 어디, 나눈 비율)와 보던 탭은 논문별로 기억한다."""
        tab_mem = state.setdefault("notes_tab_by_root", {})
        lay_mem = state.setdefault("notes_layout_by_root", {})
        names = ctrl.note_tabs()
        lay = lay_mem.get(root_key) or notes_io.load_view(ctrl.wd)  # 이 창에서 본 것 > 논문 폴더에 남긴 것
        tabs0 = [t for t in (lay.get("panes") or [tab_mem.get(root_key)]) if t in names]
        tabs0 = list(dict.fromkeys(tabs0))[:2] or [names[0]]
        notes_state["panes"] = [{"tab": t, "el_id": None, "box": None} for t in tabs0]
        notes_state["focus"] = min(int(lay.get("focus") or 0), len(tabs0) - 1)
        notes_state["ratio"] = float(lay.get("ratio") or 50)
        panes = notes_state["panes"]

        def remember() -> None:
            f = notes_state["focus"]
            lay_mem[root_key] = {"panes": [q["tab"] for q in panes], "focus": f, "ratio": notes_state["ratio"]}
            tab_mem[root_key] = panes[f]["tab"]
            try:  # 앱을 다시 띄워도 이 논문은 이 탭(칸 배치)으로 열리게 — notes/_tabs.json의 view
                notes_io.save_view(ctrl.wd, lay_mem[root_key])
            except OSError:
                pass

        async def capture() -> None:
            """지금 편집기들의 내용을 브라우저에서 바로 받아 저장한다 — 편집기를 갈아 끼우기 전에.

            편집기는 바뀐 내용을 0.2초 모아 보내는데, 그 사이에 편집기를 치우면(탭 전환) 마지막 글자가
            영영 오지 않는다. 그래서 치우기 전에 getMarkdown()으로 직접 묻는다. 무엇을 쓸지는 notes_pick이
            정한다 — 파일이 아니라 편집기를 얹을 때의 기준과 견줘, 고친 것만(준비 전·헛 빈 문자열은 건너뜀)."""
            ids = [q["el_id"] for q in panes if q["el_id"]]
            try:
                got = await ui.run_javascript(NOTES_CAPTURE_JS % json.dumps(ids), timeout=2.0)
            except Exception:  # noqa: BLE001 — 응답이 없으면 이벤트로 온 것까지만
                got = None
            for q in panes:
                r = got.get(q["el_id"]) if isinstance(got, dict) else None
                if isinstance(r, dict) and r.get("ready") is True and isinstance(r.get("md"), str):
                    notes_state["pending"][q["tab"]] = (r["md"], q["el_id"], r)
            notes_flush()

        def mount(i: int) -> None:
            """i번째 칸에 편집기를 새로 얹는다 (그 칸만 — 다른 칸은 쓰던 커서 그대로)."""
            q = panes[i]
            q["box"].clear()
            with q["box"]:
                ed = ui.element("div").classes("md4-notes-ed w-full")
            q["el_id"] = f"c{ed.id}"
            notes_state["ids"][q["el_id"]] = q["tab"]
            text, st = notes_io.read_stamped(ctrl.wd, q["tab"])
            notes_state["mem"][q["el_id"]] = {"raw": text, "saved": None, "stamp": st}
            ui.run_javascript(notes_editor_js(q["el_id"], text))

        def where(name: str) -> int | None:
            return next((i for i, q in enumerate(panes) if q["tab"] == name), None)

        async def show(name: str, i: int | None = None) -> None:
            """탭을 i번째 칸에 (없으면 마지막에 만진 칸). 이미 다른 칸에 있으면 그 칸으로 가거나(누르기),
            두 칸의 탭을 맞바꾼다(끌어 놓기)."""
            at = where(name)
            if i is None:
                if at is not None:
                    notes_state["focus"] = at
                    remember()
                    note_tabs.refresh()
                    return
                i = notes_state["focus"]
            if at == i:
                return
            await capture()
            if at is not None:          # 다른 칸에 떠 있던 탭 → 두 칸을 맞바꾼다
                panes[at]["tab"], panes[i]["tab"] = panes[i]["tab"], name
                mount(at)
            else:
                panes[i]["tab"] = name
            notes_state["focus"] = i
            remember()
            mount(i)
            note_tabs.refresh()
            note_heads.refresh()

        async def drop(name: str, zone: str) -> None:
            """탭을 메모 칸에 끌어 놓았다 — zone 'top'/'bottom'. 한 칸일 때 아래에 놓으면 칸이 나뉜다."""
            names = ctrl.note_tabs()
            if name not in names:
                return
            if len(panes) == 1:
                if zone == "top":
                    await show(name, 0)
                    return
                if panes[0]["tab"] == name:   # 보던 탭을 아래로 → 위 칸에는 다른 탭을
                    others = [n for n in names if n != name]
                    if not others:
                        ui.notify(t("탭이 하나뿐이라 나눌 수 없습니다 — + 로 탭을 하나 더 만드세요."), type="info")
                        return
                    await capture()
                    panes[0]["tab"] = others[0]
                else:
                    await capture()
                panes.append({"tab": name, "el_id": None, "box": None})
                notes_state["focus"] = 1
                remember()
                note_tabs.refresh()
                note_layout.refresh()
                return
            await show(name, 0 if zone == "top" else 1)

        async def close_pane(i: int) -> None:
            if len(panes) < 2:
                return
            await capture()
            panes.pop(i)
            notes_state["focus"] = 0
            remember()
            note_tabs.refresh()
            note_layout.refresh()

        async def add_tab() -> None:
            await capture()
            name = notes_io.add(ctrl.wd)
            i = notes_state["focus"]
            panes[i]["tab"] = name
            remember()
            mount(i)
            note_tabs.refresh()
            note_heads.refresh()
            start_rename(name)  # 만들자마자 이름부터 — '메모 2'로 두고 싶으면 그대로 Enter

        def start_rename(name: str) -> None:
            """탭 이름 그 자리에서 바꾸기 — 탭 글자가 입력 칸으로 바뀐다(Enter·바깥 클릭 = 적용, Esc = 취소)."""
            notes_state["renaming"] = name
            note_tabs.refresh()

        def finish_rename(name: str, value: str | None) -> None:
            if notes_state.get("renaming") != name:
                return  # Enter 뒤에 오는 blur 등 — 이미 끝났다
            notes_state["renaming"] = None
            new = name
            if value is not None and value.strip() and value.strip() != name:
                notes_flush()
                new = notes_io.rename(ctrl.wd, name, value)
                for q in panes:
                    if q["tab"] == name:
                        q["tab"] = new
                for k, v in notes_state["ids"].items():  # 늦게 오는 이벤트도 새 이름의 탭으로
                    if v == name:
                        notes_state["ids"][k] = new
                remember()
            note_tabs.refresh()
            if new != name:
                note_heads.refresh()

        async def on_tab_click(e, name: str) -> None:  # noqa: ANN001 — 클릭 횟수(detail)를 받는다
            # 더블클릭을 click 두 번으로 가린다 — 첫 클릭이 탭을 바꾸며 탭 줄을 다시 그리면 그 요소가 사라져
            # 브라우저의 dblclick이 오지 않는다(그래서 이름 바꾸기가 먹통이었다). 클릭 횟수는 자리 기준이라
            # 다시 그린 새 요소에서도 두 번째 클릭은 detail=2로 온다.
            n = (e.args or {}).get("detail", 1) if isinstance(e.args, dict) else 1
            if isinstance(n, int) and n >= 2:
                start_rename(name)
            else:
                await show(name)

        async def remove_tab(name: str) -> None:
            def do() -> None:
                notes_flush()
                left = notes_io.remove(ctrl.wd, name)
                at = where(name)
                if at is not None:
                    if len(panes) > 1:      # 나뉜 칸에 떠 있던 탭 → 그 칸을 닫는다
                        panes.pop(at)
                        notes_state["focus"] = 0
                        remember()
                        note_tabs.refresh()
                        note_layout.refresh()
                        return
                    panes[0]["tab"] = left[0]
                    remember()
                    mount(0)
                    note_heads.refresh()
                note_tabs.refresh()

            if where(name) is not None:
                await capture()  # 지금 쓰던 글자까지 따져 보고 묻는다
            if not ctrl.notes(name).strip():
                do()
                return
            with dlg_host, ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:360px"):
                ui.label(t("‘{name}’ 메모를 지울까요?", name=name)).classes("text-base font-medium")
                ui.label(t("이 탭의 내용이 함께 지워집니다 — 지운 내용은 논문 폴더의 notes/.trash/에 한 벌 남습니다.")) \
                    .classes("text-sm text-gray-600")
                with ui.row().classes("justify-end gap-2 w-full"):
                    ui.button(t("취소"), on_click=dlg.close).props("flat dense no-caps")
                    ui.button(t("지우기"), on_click=lambda: (dlg.close(), do())) \
                        .props("unelevated dense no-caps color=negative")
            dlg.open()

        # 끌기: 탭의 dragstart가 패널에 md4-tabdrag를 붙이면 메모 칸 위에 '위 / 아래' 놓을 자리가 뜬다.
        _drag_start = ("(e) => { e.dataTransfer.setData('text/x-md4tab', e.currentTarget.dataset.tab);"
                       " e.dataTransfer.effectAllowed = 'move';"
                       " e.currentTarget.closest('.md4-notes').classList.add('md4-tabdrag'); }")
        _drag_end = "(e) => { document.querySelectorAll('.md4-tabdrag').forEach(x => x.classList.remove('md4-tabdrag')); }"

        @ui.refreshable
        def note_tabs() -> None:
            names = ctrl.note_tabs()
            shown = {q["tab"]: i for i, q in enumerate(panes)}
            with ui.row().classes("w-full items-center no-wrap gap-1 md4-notetabs"):
                for name in names:
                    i = shown.get(name)
                    cls = "md4-notetab"
                    if i is not None:
                        cls += " on" if i == notes_state["focus"] else " shown"
                    tab = ui.element("div").classes(cls)
                    tab._props.update({"draggable": "true", "data-tab": name})  # 이름에 공백이 있어도 그대로
                    if notes_state.get("renaming") == name:  # 이름 바꾸는 중 — 탭 자리에 입력 칸
                        tab.classes(add="editing")
                        tab._props["draggable"] = "false"
                        with tab:
                            inp = ui.input(value=name).props("dense borderless autofocus") \
                                .classes("md4-notetab-in").style(f"width:{max(4, len(name) + 2)}ch")
                            inp.on("focus", js_handler="(e) => e.target.select()")
                            inp.on("keydown.enter", lambda _, n=name, w=inp: finish_rename(n, w.value))
                            inp.on("keydown.esc", lambda _, n=name: finish_rename(n, None))
                            inp.on("blur", lambda _, n=name, w=inp: finish_rename(n, w.value))
                        continue
                    with tab:
                        ui.label(name).classes("md4-notetab-t")
                        if len(names) > 1:
                            x = ui.icon("close", size="12px").classes("md4-notetab-x")
                            x.on("click.stop", lambda _, n=name: remove_tab(n))
                    tab.on("click", lambda e, n=name: on_tab_click(e, n), ["detail"])
                    tab.on("dragstart", js_handler=_drag_start)
                    tab.on("dragend", js_handler=_drag_end)
                    tab.tooltip(t("더블클릭하면 이름 바꾸기 · 메모 칸 아래쪽으로 끌어 놓으면 위아래로 나눠 보기"))
                ui.button(icon="add", on_click=add_tab).props("flat dense round size=xs color=grey-7") \
                    .classes("md4-notetab-add").tooltip(t("메모 탭 추가"))

        @ui.refreshable
        def note_heads() -> None:
            """나뉜 칸마다 위에 붙는 작은 이름표 — 어느 칸이 어느 탭인지, ×로 그 칸 닫기."""
            for i, q in enumerate(panes):
                head = q.get("head")
                if head is None:
                    continue
                head.clear()
                with head:
                    ui.label(q["tab"]).classes("md4-notepane-t" + (" on" if i == notes_state["focus"] else ""))
                    ui.space()
                    ui.icon("close", size="13px").classes("md4-notepane-x") \
                        .on("click", lambda _, k=i: close_pane(k))

        def focus_pane(i: int) -> None:
            if notes_state["focus"] != i and i < len(panes):
                notes_state["focus"] = i
                remember()
                note_tabs.refresh()
                note_heads.refresh()

        def drop_zone(zone: str, label: str) -> None:
            z = ui.element("div").classes(f"md4-notedrop md4-notedrop-{zone}")
            with z:
                ui.label(label)
            z.on("dragover", js_handler="(e) => { e.preventDefault(); e.currentTarget.classList.add('hot'); }")
            z.on("dragleave", js_handler="(e) => e.currentTarget.classList.remove('hot')")
            z.on("drop", js_handler="(e) => { e.preventDefault(); e.currentTarget.classList.remove('hot');"
                                     " document.querySelectorAll('.md4-tabdrag').forEach(x => x.classList.remove('md4-tabdrag'));"
                                     " emit(e.dataTransfer.getData('text/x-md4tab')); }",
                 handler=lambda e, zn=zone: drop(e.args, zn) if isinstance(e.args, str) else None)

        def pane_box(i: int, split: bool) -> None:
            with ui.column().classes("w-full gap-1 md4-notepane no-wrap") as col:
                panes[i]["head"] = ui.row().classes("w-full items-center no-wrap md4-notepane-head") if split else None
                panes[i]["box"] = ui.element("div").classes("md4-notepane-body w-full")
            col.on("mousedown", lambda _, k=i: focus_pane(k))
            mount(i)

        def on_ratio(e) -> None:  # noqa: ANN001
            notes_state["ratio"] = float(e.value)
            remember()

        @ui.refreshable
        def note_layout() -> None:
            with ui.element("div").classes("md4-notestack w-full"):
                if len(panes) == 1:
                    pane_box(0, False)
                else:
                    with ui.splitter(horizontal=True, value=notes_state["ratio"], limits=(15, 85),
                                     on_change=on_ratio).classes("md4-notesplit w-full") as sp:
                        with sp.before:
                            pane_box(0, True)
                        with sp.after:
                            pane_box(1, True)
                    note_heads()
                drop_zone("top", t("위 칸에 열기"))
                drop_zone("bottom", t("아래 칸에 열기") if len(panes) > 1 else t("아래로 나눠 열기"))

        def on_notes(e) -> None:  # noqa: ANN001 — GenericEventArguments
            # 편집기는 바뀐 내용을 0.2초 모아 보내므로, 치자마자 탭을 바꾸면 옛 편집기의 마지막 글자가
            # 탭을 바꾼 뒤에 도착한다 — 보낸 편집기의 탭으로 바로 써서 잃지 않는다.
            el_id = e.args.get("id") if isinstance(e.args, dict) else None
            tab = notes_state["ids"].get(el_id)
            md = notes_event_text(e.args, el_id) if tab is not None else None
            if md is None:
                return
            if any(q["el_id"] == el_id for q in panes):
                notes_typed(tab, md, el_id)
            else:
                notes_write(tab, md, el_id)

        asking: set[str] = set()

        def on_conflict(tab: str) -> None:
            """편집기를 연 뒤 다른 곳(다른 창·외부 편집·복구)에서 메모 파일이 바뀌었다 — 덮지 않고 묻는다.
            어느 쪽을 골라도 버려지는 쪽은 notes/.trash/에 한 벌 남는다."""
            if tab in asking:
                return  # 묻는 중에 더 친 글자는 conflicts에 최신으로 쌓인다
            asking.add(tab)
            kept = notes_state["conflicts"][tab][0]
            notes_io.keep(ctrl.wd, tab, kept)  # 묻는 사이 창을 닫아도 이 창의 글은 남게

            def latest() -> tuple[str, str | None] | None:
                got = notes_state["conflicts"].pop(tab, None)
                p = notes_state["pending"].get(tab)
                if got is not None and p is not None and p[1] == got[1]:
                    notes_state["pending"].pop(tab)
                    got = (p[0], got[1])
                return got

            def remount(skip: str | None) -> None:
                for i, q in enumerate(panes):
                    if q["tab"] == tab and q["el_id"] != skip:
                        mount(i)

            def reload() -> None:
                dlg.close()
                asking.discard(tab)
                got = latest()
                if got is not None and got[0] != kept:
                    notes_io.keep(ctrl.wd, tab, got[0])
                ui.notify(t("이 창에서 쓴 내용은 논문 폴더의 notes/.trash/에 남겼습니다."), type="info")
                for el_id, tv in notes_state["ids"].items():
                    if tv == tab and el_id in notes_state["mem"]:
                        notes_state["mem"][el_id]["dead"] = True
                remount(None)
                notes_set_status(t("저장됨"))

            def overwrite() -> None:
                dlg.close()
                asking.discard(tab)
                got = latest()
                if got is None:
                    return
                text, el_id = got
                try:
                    st = ctrl.save_notes(text, tab, keep_old=True)
                except OSError as e:
                    notes_set_status(t("저장 실패: {e}", e=e))
                    return
                mem = notes_state["mem"].get(el_id)
                if mem is not None:
                    mem["saved"], mem["stamp"] = text, st
                remount(el_id)  # 다른 편집기에 옛 내용이 떠 있으면 새로
                notes_set_status(t("저장됨"))

            with dlg_host, ui.dialog().props("persistent") as dlg, \
                    ui.card().classes("gap-2").style("max-width:400px"):
                ui.label(t("‘{tab}’ 메모가 다른 곳에서 바뀌었습니다", tab=tab)).classes("text-base font-medium")
                ui.label(t("이 편집기를 연 뒤 다른 창이나 프로그램이 이 메모 파일을 고쳤습니다. 이 창에서 쓴 "
                         "내용은 아직 저장하지 않았습니다. 어느 쪽을 골라도 버려지는 쪽은 notes/.trash/에 "
                         "한 벌 남습니다.")).classes("text-sm text-gray-600")
                with ui.row().classes("justify-end gap-2 w-full"):
                    ui.button(t("새로 불러오기"), on_click=reload).props("flat dense no-caps")
                    ui.button(t("내 내용으로 덮기"), on_click=overwrite).props("unelevated dense no-caps color=primary")
            dlg.open()

        notes_state["on_conflict"] = on_conflict

        async def on_agent_refresh(e) -> None:  # noqa: ANN001 — GenericEventArguments
            """에이전트가 메모 파일을 고쳤다(write_note 승인) — 떠 있는 편집기를 파일에 다시 맞춘다.

            먼저 capture()로 아직 안 보낸 글자를 내보낸다: 고치던 탭이면 지문이 달라 notes_write가 Conflict를
            내고 기존 '다른 곳에서 바뀜' 대화상자가 묻는다(조용히 덮지 않는다). 그 밖의 칸은 파일 지문이
            얹을 때와 다를 때만 새로 얹는다. 탭이 새로 생겼을 수 있어 탭 줄도 다시 그린다."""
            args = e.args if isinstance(e.args, dict) else {}
            box = panes[0].get("box") if panes else None
            if args.get("token") != tok or box is None or box.is_deleted:
                return  # 다른 논문, 또는 화면을 갈아 끼우며 남은 옛 핸들러 (ui.on은 창 단위로 쌓인다)
            await capture()
            for i, q in enumerate(panes):
                if q["tab"] in notes_state["conflicts"] or q["tab"] in asking:
                    continue
                mem = notes_state["mem"].get(q["el_id"])
                if mem is None or mem.get("stamp") != notes_io.stamp(ctrl.wd, q["tab"]):
                    mount(i)
            note_tabs.refresh()
            note_heads.refresh()

        ui.on(agent_panel.NOTES_REFRESH_EVENT, on_agent_refresh)

        with ui.row().classes("w-full items-center no-wrap gap-2 shrink-0"):
            ui.label(t("메모")).classes("md4-panel-title")
            notes_state["status"] = ui.label(t("저장됨")).classes("md4-notes-status")
            dlg_host = ui.element("div").style("display:none")  # 탭 이름 바꾸기·지우기 대화상자 자리
        note_tabs()
        note_layout()
        ui.on("md4-notes", on_notes)

    # ===== 본문: 레일 + (작업 패널 | 캔버스) =====
    # 처음 여는 논문은 번역이 있으면 목차, 없으면 구조부터(먼저 할 일이 섹션 구조 확인이라). 다시 열면
    # 보던 패널로 — 스크롤은 창 쪽 sessionStorage가 기억한다(§ui/scroll_memory.py).
    # 보던 패널은 논문별로 status.json에도 적혀 있어(§workdir.set_left_panel) 앱을 다시 켜도 남는다.
    _seen_mode = state.get("panel_by_root", {}).get(root_key, "?")
    if _seen_mode == "?":
        _seen_mode = left_panel(ctrl.wd.root)
    _valid_modes = (None, *(k for k, _l, _i in _PANELS))
    panel_state["mode"] = _seen_mode if _seen_mode in _valid_modes else ("toc" if ctrl.ko_markdown() else "structure")
    panel_state["last"] = panel_state["mode"]

    def _on_split(e) -> None:  # noqa: ANN001 — 패널 폭 드래그. 끝까지 밀어 닫으면 레일도 '접힘'으로
        if e.value > 1:
            if panel_state["mode"] is None:  # 닫힌 패널을 끌어 열었다 → 마지막 모드로 (없으면 목차)
                set_panel(panel_state.get("last") or "toc", toggle=False)
            else:
                remember_width(panel_state["mode"], e.value)
        elif panel_state["mode"] is not None:
            panel_state["mode"] = None
            state.setdefault("panel_by_root", {})[root_key] = None  # 끌어서 접은 것도 '접힘'으로 기억
            set_left_panel(ctrl.wd.root, None)
            for box in panel_state["boxes"].values():
                box.set_visibility(False)
            rail.refresh()

    with chip_slot:  # 헤더 상태 칩 — 패널보다 먼저 그린다(번역 중에 돌아온 화면은 패널을 짓다가 칩을 갱신한다)
        status_chip()

    # md4-body: 에이전트 창·메모 서랍이 열리면 이 폭이 줄어 본문이 덮이지 않는다 (§agent_panel.CSS)
    with ui.row().classes("w-full no-wrap gap-0 md4-steps md4-body"):
        with ui.column().classes("md4-rail gap-0"):
            rail()
        with ui.splitter(value=panel_width(panel_state["mode"]) if panel_state["mode"] else 0, limits=(0, 60)) \
                .classes("md4-split").style("flex:1 1 auto; min-width:0; height:100%") as sp:
            panel_state["splitter"] = sp
            sp.on_value_change(_on_split)
            with sp.before:
                with ui.column().classes("md4-panel md4-scroll"):
                    # ---------- 목차 ----------
                    with ui.column().classes("w-full gap-1") as box:
                        toc_panel()  # 제목 줄(목차 + 언어 선택)까지 안에서 그린다
                    panel_state["boxes"]["toc"] = box

                    # ---------- 구조 (옛 변환 탭 왼쪽 열) ----------
                    with ui.column().classes("w-full gap-3") as box:
                        ui.label(t("구조")).classes("md4-panel-title")
                        ui.button(t("레이아웃 자동 수정"), icon="auto_fix_high", on_click=open_fix_dialog) \
                            .props("outline no-caps color=primary").classes("w-full").tooltip(
                                t("쪼개진 헤더·깨진 수식·끊긴 문단을 AI가 문서 전체에서 찾아 고칩니다"))
                        cap(t("PDF 추출이 어그러뜨린 부분을 AI가 훑어 고치고, 섹션 트리를 다시 만듭니다."))
                        # 수정이 도는 동안 대화상자를 숨겼거나 다른 탭에서 돌아왔을 때 진행을 보여 준다
                        fix_banner = ui.label("").classes("text-xs text-primary cursor-pointer")
                        fix_banner.on("click", open_fix_dialog)
                        fix_banner.set_visibility(False)
                        if jobs.is_running(ctrl.wd.root, "layout"):
                            attach_layout_fix(jobs.get(ctrl.wd.root, "layout"), own=False)

                        has_src = bool(ctrl.source_pdf())
                        rc_button = ui.button(t("PDF에서 다시 변환"), icon="refresh", on_click=open_reconvert_dialog) \
                            .props("outline no-caps color=primary").classes("w-full").tooltip(
                                t("원본 PDF에서 추출부터 다시 — 추출기 개선(굵게·기울임, 띄어쓰기 등)을 반영합니다")
                                if has_src else t("원본 PDF가 없는 논문(마크다운으로 올림)은 다시 변환할 수 없습니다"))
                        if not has_src:
                            rc_button.disable()
                        elif ctrl.extract_outdated():
                            cap(t("이전 버전 추출기로 변환된 논문입니다 — 다시 변환하면 개선 사항이 반영됩니다."))
                        rc_banner = ui.label("").classes("text-xs text-primary")
                        rc_banner.set_visibility(False)
                        if jobs.is_running(ctrl.wd.root, "reconvert"):
                            attach_reconvert(jobs.get(ctrl.wd.root, "reconvert"))

                        with ui.expansion(t("일괄 레벨 조정"), icon="format_list_numbered").classes("w-full"):
                            batch_level_panel()

                        ui.label(t("섹션 트리")).classes("font-semibold text-sm text-primary")
                        ui.label(t("제목 1~6: 헤더 단계 · 본문으로: 헤더를 풀어 제목 텍스트를 일반 문단으로 남김")) \
                            .classes("text-xs text-gray-500")
                        ui.label(t("위에 합침: 제목 줄을 없애고 그 내용을 위 섹션에 합침 · 통째 삭제: 제목·본문 모두 제거")) \
                            .classes("text-xs text-gray-500")
                        ui.label(t("기울임: run-in 소제목을 기울임 문단으로 표기")).classes("text-xs text-gray-500")
                        ui.label(t("제목을 클릭하면 캔버스의 해당 위치로 이동합니다 (PDF가 켜져 있으면 그 쪽으로도).")) \
                            .classes("text-xs text-gray-500")
                        section_list()
                    panel_state["boxes"]["structure"] = box

                    # ---------- 번역 (옛 번역 탭 왼쪽 열 + 용어집 머리) ----------
                    with ui.column().classes("w-full gap-3") as box:
                        ui.label(t("번역")).classes("md4-panel-title")
                        translate_run_panel()
                        ui.separator()
                        translate_settings()
                    panel_state["boxes"]["translate"] = box

                    # ---------- 설정 (옛 변환 설정) ----------
                    with ui.column().classes("w-full gap-3") as box:
                        ui.label(t("설정")).classes("md4-panel-title")
                        convert_settings()
                    panel_state["boxes"]["settings"] = box

                    # ---------- 메모 (논문 전체 자유 메모 — notes.md) ----------
                    with ui.column().classes("w-full gap-2 md4-notes") as box:
                        notes_panel()
                    panel_state["boxes"]["notes"] = box

                    for key, box in panel_state["boxes"].items():
                        box.set_visibility(key == panel_state["mode"])
            with sp.after:
                with ui.column().classes("p-3 w-full gap-2 md4-canvascol").style("height:100%; " + body_width_css()):
                    viewer_toolbar()
                    canvas()

    # 자리를 비운 사이 끝난 작업 — 이 화면은 이미 결과 파일로 지어졌으니 알림만 한 번
    for job in jobs.unseen_finished(ctrl.wd.root):
        job.seen = True
        if job.status == "done":
            ui.notify(job.message or t("{label} 완료", label=job.label), type="positive")
        else:
            ui.notify(t("{label} 실패: {error}", label=job.label, error=job.error), type="negative")


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

    LLM 키가 있으면 앞부분(저자·서지) 정규화와 수식(크롭 그림 → LaTeX)에 LLM을 쓴다.
    없으면 각각 규칙 폴백 / 크롭 그림 그대로.
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


_QUEUE_ACTIVE = ("pending", "extracting", "cite", "glossary", "meta", "bib")


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
                # 올릴 때 고른 프로젝트에 배정한다 — 이름 정리(리네임) 뒤라야 최종 폴더에 적히고,
                # 자동 저장보다 앞이라야 그 프로젝트의 폴더로 쌓인다. (AI 키와는 무관한 단계)
                if item.get("project"):
                    await run.io_bound(set_project, wd.root, item["project"])
                # 서지를 온라인으로 보강 — .bib 항목이 정확해지고 인용이 링크가 된다.
                # auto_export 앞이라야 그 정확한 항목이 references.bib에 쌓인다.
                item["status"], item["since"] = "bib", time.monotonic()
                item["boost"] = await run.io_bound(_auto_bibsource, wd)
                # '저장 위치'가 지정돼 있으면 결과 마크다운을 거기에 쌓는다 (리네임 후 최종 이름으로)
                await run.io_bound(library.auto_export, wd)
                item["status"], item["done_at"] = "done", time.monotonic()
            except Exception as ex:  # noqa: BLE001 — 항목 실패가 큐 전체를 막지 않게
                item["status"], item["error"] = "failed", str(ex)
    finally:
        state["worker_running"] = False


def build_home(state: dict) -> None:
    """홈 — 프로젝트 사이드바 | 논문 목록. 본체는 §ui/home.py (여기는 라우트가 부르는 이름만 남긴다).

    큐 처리(_process_queue)·저장(save_source 등)은 테스트가 이 모듈에서 가져가므로 여기 그대로 두고,
    home.py가 함수 안에서 지연 import 한다 — 이 모듈도 home.py를 홈을 그릴 때만 import 하므로 순환이 없다.
    """
    from md4paper.ui import home

    home.build_home(state)


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

    @fastapi_app.get("/wdthumb/{token}")
    def _serve_thumb(token: str):  # noqa: ANN202 — 홈 미리보기의 대표 이미지 (§preview)
        # current_wd로 폴백하지 않는다 — 모르는 토큰에 다른 논문 그림을 내주면 미리보기가 엉뚱해진다
        cur = state["wd_by_token"].get(token)
        try:
            from md4paper import preview
        except ImportError:
            preview = None
        png = preview.thumbnail_png(cur) if (cur is not None and preview is not None) else None
        if png is None or not Path(png).is_file():
            raise HTTPException(status_code=404)
        return FileResponse(png)

    @fastapi_app.get("/pdfpage/{token}/{idx:int}")
    def _serve_pdf_page(token: str, idx: int, s: float = 2.0):  # noqa: ANN202 — 해당 논문 PDF의 idx(0-based) 페이지 PNG
        # s = 렌더 배율(72dpi 기준). 기본 2배, PDF 칸을 확대하면 4배로 다시 받는다(§ui/pdf_zoom)
        cur = _wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        png = UIController(cur).pdf_page_png(idx, zoom=min(4.0, max(1.0, s)))
        if png is None:
            raise HTTPException(status_code=404)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

    @fastapi_app.get("/annotations/{token}")
    def _get_annotations(token: str):  # noqa: ANN202 — 해당 논문의 하이라이트·메모
        cur = _wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        return {"items": annotations.load(cur)}

    @fastapi_app.post("/md4math")
    def _md4math(body: dict):  # noqa: ANN202 — 편집기 라이브 프리뷰의 수식 → MathML (§ui/live_preview.py)
        return {"items": live_preview.mathml(body.get("tex"))}

    @fastapi_app.post("/annotations/{token}")
    def _put_annotations(token: str, body: dict):  # noqa: ANN202 — 뷰어에서 되저장
        cur = _wd_for(token)
        if cur is None:
            raise HTTPException(status_code=404)
        saved = annotations.save(cur, body.get("items"), backup=bool(body.get("repaired")))
        return {"ok": True, "count": len(saved)}

    # 에이전트 창 — /agent/... (세션·SSE 턴·모델). 모르는 토큰은 404여야 하므로 current_wd로
    # 폴백하는 _wd_for가 아니라 등록부만 본다(창이 여럿이면 엉뚱한 논문을 읽게 된다).
    from md4paper.ui.agent import routes as agent_routes

    agent_routes.register_routes(
        fastapi_app, wd_for=lambda tv: state["wd_by_token"].get(tv),
        register_token=lambda w: (_register_wd(w), wd_token(w.root))[1],
        workspace=lambda: state["upload_dir"])
    formula_pop.register_routes(fastapi_app, _wd_for)
    figure_ko.register_routes(fastapi_app, _wd_for)  # 그림 속 글자 번역 — /figko/{token}/{name}
    figure_crop.register_routes(fastapi_app, _wd_for)  # 그림 영역 다시 지정 — /figure/{token}/{name}  # 수식 원본 크롭·다시 생성 — /formula/{token}/{id}
    fastapi_app.add_static_files(CREPE_URL, CREPE_DIR)  # 메모 패널 위지윅 편집기 번들

    def index_view() -> None:
        # CLI로 워크디렉토리를 지정해 띄웠으면 그 리뷰로, 아니면 업로드 홈
        if state["launch_wd"] is not None:
            ui.navigate.to(_review_url(state["launch_wd"].root))
            return
        build_home(state)

    def home_view() -> None:
        build_home(state)

    def review_view(wd: str = "") -> None:  # noqa: ANN001 — 쿼리 파라미터
        root = Path(wd) if wd else None
        if root is None or not WorkDir(root).sections_yaml.exists():
            ui.navigate.to("/home")
            return
        wdir = WorkDir(root)
        state["current_wd"] = wdir  # 이미지 서빙이 이 워크디렉토리를 가리키도록
        _register_wd(wdir)  # 토큰 등록 (이 논문 자산 URL 해석용)
        _mark_opened(wdir)  # 미열람 표시 해제 (한 번이라도 열면 '새 항목' 아님)
        tabstore.open_tab(wdir.root)  # 연 논문은 ×를 누를 때까지 헤더 탭에 남는다
        build(UIController(wdir), state)

    # 셸 — 문서는 하나만 띄우고 헤더는 그대로 둔 채 본문만 갈아 끼운다(탭 전환 깜빡임 제거, §ui/spa.py).
    # 세 경로가 **같은 함수**여야 한다: 라우터는 다른 페이지 함수가 그 주소를 맡고 있으면 문서를 새로 연다.
    @ui.page("/")
    @ui.page("/home")
    @ui.page("/review")
    def shell() -> None:
        ui.add_head_html(spa.HEAD_HTML + _PREPAINT_HTML + i18n.head_html())  # 화면 언어 카탈로그(md4T)
        spa.make_header()
        # 에이전트 창 — 셸에 붙어 화면을 갈아 끼워도 대화가 남는다. 하위 화면보다 **먼저** 넣어야
        # 첫 화면(뷰어·홈)이 부르는 setPaper가 이미 떠 있는 창에 닿는다.
        # 앱 창 모드면 '새 창으로'가 단독 창(/agent)을 형제 웹뷰로 띄운다(브라우저 모드는 링크가 새 탭).
        agent_panel.install(state, popout=(lambda path: _spawn_path(
            path, state, agent_panel.WINDOW_TITLE, dup_note=t("이 대화는 이미 다른 창에 떠 있습니다.")))
            if desktop.active() else None)
        # w-full: 없으면 sub_pages 상자가 내용 폭으로 줄어(큰 화면에서 1275px) 변환·번역 탭 splitter가 좁아진다
        ui.sub_pages({"/": index_view, "/home": home_view, "/review": review_view}).classes("w-full")
        update_banner.install(state)  # 새 버전 알림 — 셸에 붙어 탭을 옮겨도 남는다

    # 에이전트 단독 창 — 셸과 **다른** 페이지 함수라 새 문서로 뜬다(헤더·화면 없이 창 UI만).
    # 서랍 머리의 '새 창으로'가 연다: ?session=<sid>&paper=<token> (§agent_panel.standalone_page)
    @ui.page(agent_panel.STANDALONE_PATH, title=agent_panel.WINDOW_TITLE)
    def agent_window(session: str = "", paper: str = "") -> None:  # 쿼리 파라미터
        agent_panel.standalone_page(state, session, paper, native=desktop.active())

    port = pick_port(port)
    state["port"] = port  # 새 창(형제 웹뷰 프로세스)이 가리킬 주소를 만들 때 쓴다

    @fastapi_app.on_startup
    def _check_update() -> None:
        update_banner.start_check(state)  # 켤 때마다 GitHub에 새 커밋이 있는지 (백그라운드)

    @fastapi_app.on_shutdown
    def _close_extra_windows() -> None:
        from md4paper.ui import window as extra_window

        extra_window.close_all()  # 서버가 내려가면 형제 창도 함께 (서버 없는 창은 빈 페이지다)

    if native:
        desktop.configure()  # 창 크기·Dock 아이콘·다운로드 설정 (웹뷰 프로세스로 전달된다)
        print(f"md4paper 앱 창 시작 (내부 주소 http://127.0.0.1:{port})")
    else:
        print(f"md4paper UI: http://127.0.0.1:{port}  (Ctrl+C로 종료)")
    ui.run(host="127.0.0.1", port=port, show=show and not native, reload=False, title="md4paper",
           language=i18n.quasar_language(),  # Quasar 내장 문구(표·날짜 선택기)도 화면 언어로
           favicon=str(desktop.ICON) if desktop.ICON.is_file() else None,
           native=native, window_size=(1440, 900) if native else None)

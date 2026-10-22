"""홈 — 왼쪽 컬렉션 사이드바 | 논문 목록 | 오른쪽 미리보기 (논문 화면과 같은 크롬).

논문 화면은 '왼쪽 레일 + 본문'이 창을 꽉 채운다. 홈도 같은 틀이라야 탭을 오갈 때 화면이 다른 앱처럼
튀지 않는다. 그래서 `.md4-body`(헤더 44px를 뺀 창 높이, 창 전체 스크롤 없음)와 뷰어의 색 토큰
(사이드바 #f4f3f1 · 경계 #e6e4e0 · 활성 파랑)을 그대로 쓴다.

- **왼쪽**: 모든 논문 / 프로젝트들 / 미분류 + 새 프로젝트 · 설정 · 버전 · 작업 폴더. 고른 행이
  목록 필터이자 새로 올리는 논문의 소속이다(`projects.set_active`로 다음 실행에도 기억).
- **가운데**: 고른 범위의 제목 · 검색·정렬·올리기·미리보기 접기 · 변환 큐 · 논문 카드.
- **올리기**: PDF를 창 위로 끌면 목록 열 전체가 놓을 자리가 된다(아래 _DRAG_HTML). '올리기'
  단추는 진짜 `<input type=file>`을 단추 위에 펼쳐 둔 것이다(WKWebView는 JS input.click()을 막는다).
- **미리보기(오른쪽 열, Zotero식)**: 카드를 한 번 누르면 **선택**(카드 `.on`, 미리보기에 대표 이미지·
  서지·초록·메모 발췌·단추), 더블클릭·Enter·미리보기 '열기'가 논문 화면이다. 체크박스는 따로 여러 편을
  고르는 일괄 선택이고, 체크가 있으면 미리보기 자리가 일괄 동작 칸이 된다(목록 위 선택 툴바는 없앴다).
  선택은 이 홈 인스턴스에만 둔다(state 아님). 초록·이미지는 `run.io_bound`로 읽고 그 사이 선택이 바뀌면
  늦게 온 결과는 버린다(seq 비교). 접힘 여부는 전역 설정 `[ui].home_preview`에 기억한다.
- **서지 고치기**: 미리보기의 제목·저자·연도·venue·DOI·약칭·초록은 눌러서 그 자리에서 고친다
  (`edit_field`). 저장은 `paper_meta.update_fields`(초록은 `preview.set_abstract`) → 들고 있는 행과 그
  카드 글자만 고치고(목록·미리보기 전체를 다시 그리지 않는다 — 옆 칸을 고치던 중이면 그 입력칸이 날아간다)
  → 헤더 탭 줄 → references.bib(백그라운드). 이름 규칙 힌트·'다시 찾기'·'다시 추출'도 여기 있다.
- **소속(N:M)**: 논문 하나가 여러 프로젝트에 속한다. 카드의 프로젝트 태그를 누르면 체크 메뉴로
  넣고 빼고, 카드를 사이드바 프로젝트 행에 끌어 놓으면 그 프로젝트에 **더한다**(옮기지 않는다).

목록은 한 번 그릴 때 작업 폴더를 **한 번만** 훑는다(`scan`). 225편에 0.2~0.4초라, 고정·숨김·소속
토글처럼 한 편만 바뀌는 동작은 다시 훑지 않고 들고 있는 행을 고친다. 업로드 완료·작업 폴더 변경·
프로젝트 삭제처럼 여러 편이 바뀔 수 있을 때만 다시 훑는다.

app.py의 헬퍼(tab_bar·_review_url·save_source·_process_queue 등)는 함수 안에서 지연 import 한다 —
app.py가 이 모듈을 홈을 그릴 때 지연 import 하므로 순환이 없다. 큐 처리·저장 함수는 테스트가
`md4paper.ui.app`에서 가져가므로 app.py에 남는다.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from md4paper import config, library, projects
from md4paper.ui import desktop, find_bar, jobs, scroll_memory, settings_dialog, spa, tabstore, update_banner
from md4paper.workdir import WorkDir

# 카드를 사이드바로 끌 때 dataTransfer에 싣는 형식 — 파일 끌기('Files')와 섞이지 않게 따로 둔다
PAPER_MIME = "text/x-md4-paper"

HOME_CSS = """
/* 홈 = 뷰어와 같은 2단. 높이·여백은 _CHROME_CSS의 .md4-body 규칙이 맡는다(창 전체는 스크롤되지 않는다). */
.md4-hside { flex: 0 0 272px; width: 272px; height: 100%; background: #f4f3f1; border-right: 1px solid #e6e4e0;
  flex-wrap: nowrap; }
/* 행은 사이드바 끝에서 끝까지 — 좌우 여백은 행 안(16px)에서 준다. hover·고른 행의 바탕이 가장자리까지 닿아
   어느 줄인지 한눈에 들어온다. 캡·'새 컬렉션'·바닥 줄도 같은 16px 들여쓰기에 맞춘다. */
.md4-hside-rows { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 8px 0 6px; flex-wrap: nowrap; }
.md4-hcap { padding: 16px 16px 6px; }
.md4-prow { display: flex; align-self: stretch; align-items: center; gap: 10px; min-height: 38px; padding: 0 10px 0 16px; border-radius: 0;
  cursor: pointer; color: #37352f; font-size: 14.5px; line-height: 1.3; user-select: none; transition: background .12s ease; }
.md4-prow:hover { background: rgba(0,0,0,.05); }
.md4-prow.on { background: rgba(35,131,226,.13); color: #2383e2; font-weight: 600; }
.md4-prow-i { color: #9a968e; flex: 0 0 auto; }
.md4-prow.on .md4-prow-i { color: #2383e2; }
/* 이름(+ 폴더를 가진 컬렉션은 그 아래 폴더 이름) — 둘 다 한 줄로 자른다 */
.md4-prow-t { flex: 1 1 auto; min-width: 0; padding: 5px 0; }
.md4-prow-name, .md4-prow-sub { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md4-prow-sub { font-size: 11.5px; color: #9a968e; font-weight: 400; margin-top: 1px; }
.md4-prow-n { flex: 0 0 auto; min-width: 2ch; text-align: right; font-size: 12.5px; color: #9a968e; font-weight: 400;
  font-variant-numeric: tabular-nums; }
/* ⋯ 자리는 늘 잡아 둔다(편수가 hover 때 옆으로 밀리지 않게). 폭이 넉넉해져 편수와 함께 보인다. */
.md4-prow-more { flex: 0 0 auto; opacity: 0; color: #6b6862; margin: 0 -4px 0 -4px; }
.md4-prow:hover .md4-prow-more, .md4-prow-more[aria-expanded=true] { opacity: 1; }
/* ⋯가 없는 행(모든 논문·미분류)도 같은 자리를 비워 둔다 — 편수가 한 세로줄에 서게 (⋯ 24px − 겹침 8px) */
.md4-prow-gap { flex: 0 0 16px; }
/* 카드를 끄는 동안 놓을 수 있는 행(컬렉션)을 옅은 테두리로, 그 위에 올리면 진하게 — 놓으면 그 컬렉션에 더해진다 */
html.md4-carddrag .md4-prow[data-drop] { box-shadow: inset 0 0 0 1px rgba(35,131,226,.35); }
.md4-prow.hot, html.md4-carddrag .md4-prow.hot[data-drop] { background: rgba(35,131,226,.18); box-shadow: inset 0 0 0 1.5px #2383e2; }
.md4-hnew { justify-content: flex-start; margin-top: 6px; color: #6b6862; border-radius: 0; min-height: 36px; }
.md4-hfoot { flex: 0 0 auto; padding: 6px 0 10px; border-top: 1px solid #e6e4e0; }
.md4-hfoot .q-btn { justify-content: flex-start; color: #6b6862; border-radius: 0; }
.md4-hnew.q-btn, .md4-hfoot .q-btn { padding: 0 16px; }
.md4-hnew .q-btn__content, .md4-hfoot .q-btn__content { justify-content: flex-start; }
/* 작업 폴더 경로 — 길면 앞을 줄이고 끝(폴더 이름)을 보인다(rtl). 앞의 '/'가 rtl에서 끝으로 튀지 않게
   글자 앞에 LRM을 붙여 그린다. */
.md4-hws { font-size: 11px; color: #9a968e; padding: 0 16px; max-width: 100%; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
.md4-hfoot .nicegui-row { padding: 0 16px; flex-wrap: wrap; }
/* 오른쪽 목록 열 — 머리·도구 줄은 제 높이, 목록은 남은 높이 안에서만 스크롤. 드롭 때 업로더가
   이 열 전체로 펼쳐지므로 기준 상자(position)가 된다. */
.md4-hmain { flex: 1 1 auto; min-width: 0; height: 100%; position: relative; flex-wrap: nowrap; }
.md4-hhead { padding: 18px 24px 4px; flex: 0 0 auto; }
.md4-htitle { font-size: 22px; font-weight: 700; color: #37352f; line-height: 1.25; }
.md4-hcount { font-size: 14px; color: #9a968e; font-weight: 500; }
.md4-hpath { font-size: 12px; color: #9a968e; min-width: 0; }
.md4-hwarn { font-size: 12px; padding: 5px 10px; border-radius: 6px; cursor: pointer; }
.md4-hwarn.ai { color: #9a5b00; background: #fff4e0; }
.md4-hwarn.err { color: #b42318; background: #fdecec; cursor: default; }
.md4-htools { padding: 8px 24px 6px; flex: 0 0 auto; }
.md4-hlist { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 2px 24px 28px; flex-wrap: nowrap; gap: 6px; }
/* '올리기' — 보이는 건 단추지만 클릭은 위에 투명하게 펼친 진짜 <input type=file>이 받는다. */
.md4-upwrap { position: relative; flex: 0 0 auto; }
.md4-upwrap:hover .md4-upbtn { filter: brightness(1.08); }
.md4-upload { position: absolute; inset: 0; z-index: 2; opacity: 0; cursor: pointer; overflow: hidden; }
.md4-upload, .md4-upload > .q-uploader { width: 100%; height: 100%; min-height: 0; max-height: none; }
/* 헤더(= 파일 선택 버튼 + 그 안의 <input type=file>)를 업로더 전체로 펼친다. 부모가 opacity:0이라
   보이지는 않는다. display:none으로 감추면 클릭으로 파일 선택창을 열 방법이 없어져 서버를 거쳐
   JS로 input.click()을 불러야 하는데, 그러면 사용자 제스처가 끊겨 웹뷰(WKWebView)가 창을 막는다. */
.md4-upload .q-uploader__header { position: absolute; inset: 0; padding: 0; }
.md4-upload .q-uploader__header-content { height: 100%; padding: 0; }
.md4-upload .q-uploader__header .q-btn { position: absolute; inset: 0; }
/* 업로더의 파일 목록은 보이지도 않으면서 헤더 위에 겹쳐 클릭을 가로챈다 (DOM 순서상 위). */
.md4-upload .q-uploader__list { pointer-events: none; }
/* 파일을 창 위로 끄는 동안(<html>.md4-filedrag) — 업로더를 단추 자리에서 목록 열 전체로 펼친다.
   단추 상자의 position을 풀면 업로더의 기준 상자가 .md4-hmain이 된다. 위에는 투명한 업로더가,
   그 아래에 안내 오버레이가 깔린다(놓기는 업로더가 받는다 — Quasar 루트 div가 drop을 받는다). */
html.md4-filedrag .md4-upwrap { position: static; }
html.md4-filedrag .md4-upload { z-index: 40; }
.md4-dropov { display: none; position: absolute; inset: 10px; z-index: 39; pointer-events: none;
  border: 2px dashed #2383e2; border-radius: 14px; background: rgba(244,248,253,.92);
  flex-direction: column; align-items: center; justify-content: center; gap: 6px; color: #2383e2; }
html.md4-filedrag .md4-dropov { display: flex; }
.md4-dropov-t { font-size: 17px; font-weight: 600; }
.md4-dropov-s { font-size: 12.5px; color: #6b6862; }
/* 비어 있을 때의 큰 안내 — 예전 드롭존 모양은 여기에만 남긴다 */
.md4-empty { border: 2px dashed #dcdad5; border-radius: 14px; padding: 36px 22px; margin-top: 8px;
  align-items: center; text-align: center; color: #6b6862; }
.md4-spin { animation: md4spin 1s linear infinite; }
@keyframes md4spin { to { transform: rotate(360deg); } }
/* 논문 카드 — 한 줄 제목 · 서지 · 배지와 소속 태그. 동작 단추는 hover 때만(제목 자리를 돌려준다). */
.md4-pcard { position: relative; width: 100%; display: flex; align-items: flex-start; gap: 8px; padding: 9px 10px 8px 8px;
  border: 1px solid #ebe9e5; border-radius: 9px; background: #fff; transition: background .12s ease, border-color .12s ease; }
.md4-pcard:hover { background: #fbfaf9; border-color: #dcdad5; }
.md4-pcard.sel { border-color: rgba(35,131,226,.5); background: rgba(35,131,226,.04); }
.md4-pcard[draggable=true] { -webkit-user-drag: element; }
.md4-pmain { flex: 1 1 auto; min-width: 0; user-select: none; }
/* 카드 전체가 클릭 대상 — 한 번 = 선택(미리보기), 두 번·Enter = 열기. 고른 카드는 파란 테두리+옅은 바탕. */
.md4-pcard { cursor: pointer; outline: none; }
.md4-pcard:focus-visible { box-shadow: 0 0 0 2px rgba(35,131,226,.35); }
.md4-pcard.on, .md4-pcard.on:hover { background: rgba(35,131,226,.07); border-color: #2383e2;
  box-shadow: inset 0 0 0 1px #2383e2; }
.md4-pcard.on.md4-pincard { box-shadow: inset 3px 0 0 #2383e2, inset 0 0 0 1px #2383e2 !important; }
.md4-ptitle { font-size: 14px; color: #37352f; }
.md4-ptitle.unopened { font-weight: 600; }
.md4-pbib { font-size: 12px; color: #787570; }
.md4-pmeta { font-size: 11.5px; color: #9a968e; }
.md4-pacts { flex: 0 0 auto; align-self: center; opacity: 0; transition: opacity .12s ease; }
.md4-pcard:hover .md4-pacts, .md4-pacts:focus-within { opacity: 1; }
.md4-pacts .keep { opacity: 1; }
.md4-pinmark { flex: 0 0 auto; align-self: center; color: #2383e2; }
.md4-pcard:hover .md4-pinmark { display: none; }
.md4-recent-new { box-shadow: inset 4px 0 0 #21ba45; }
.md4-new-dot { width: 8px; height: 8px; border-radius: 50%; background: #2383e2; flex: 0 0 8px; }
/* 고정한 논문은 목록 맨 위 한 구역으로 — 카드 자체를 올린다(칩 줄을 따로 두지 않는다) */
.md4-pincard { box-shadow: inset 3px 0 0 #2383e2 !important; }
.md4-listcap { font-size: 11px; font-weight: 700; letter-spacing: .05em; color: #9a968e;
  display: flex; align-items: center; gap: 4px; padding: 6px 2px 0; }
/* 소속 프로젝트 태그 — 여러 개. 누르면 체크 메뉴로 넣고 뺀다. */
.md4-ptags { display: inline-flex; flex-wrap: wrap; gap: 4px; cursor: pointer; border-radius: 10px; }
.md4-ptag { display: inline-flex; align-items: center; gap: 3px; height: 19px; padding: 0 7px; border-radius: 10px;
  font-size: 11.5px; line-height: 1; color: #2383e2; background: rgba(35,131,226,.1); white-space: nowrap;
  max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.md4-ptag.none { color: #9a968e; background: transparent; box-shadow: inset 0 0 0 1px #dcdad5; }
.md4-ptags:hover .md4-ptag { background: rgba(35,131,226,.18); }
.md4-ptags:hover .md4-ptag.none { color: #6b6862; background: rgba(0,0,0,.04); }
.md4-ptag-add { opacity: 0; color: #9a968e; }
.md4-pcard:hover .md4-ptag-add { opacity: 1; }
/* 오른쪽 미리보기 열 — 뷰어 패널처럼 흰 바탕 + 왼쪽 경계선, 본문만 스크롤하고 단추 줄은 바닥에 고정 */
.md4-hprev { flex: 0 0 360px; width: 360px; height: 100%; background: #fff; border-left: 1px solid #e6e4e0;
  flex-wrap: nowrap; }
/* 좁은 창(앱 창을 줄였을 때) — 세 열이 목록을 짓누르지 않게 사이드바·미리보기를 조금씩 좁힌다 */
@media (max-width: 1100px) {
  .md4-hside { flex-basis: 220px; width: 220px; }
  .md4-hprev { flex-basis: 290px; width: 290px; }
}
.md4-pv-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 18px 18px 20px; flex-wrap: nowrap; }
.md4-pv-body > * { flex-shrink: 0; }  /* 넘치면 줄이지 말고 스크롤 — 안 그러면 그림 칸이 min-height로 찌그러진다 */
.md4-pv-foot { flex: 0 0 auto; padding: 8px 12px; border-top: 1px solid #e6e4e0; background: #fff; }
.md4-pv-thumb { width: 100%; min-height: 120px; max-height: 220px; display: flex; align-items: center;
  justify-content: center; background: #f7f6f3; border-radius: 8px; overflow: hidden; }
.md4-pv-thumb img { display: block; width: 100%; max-height: 220px; object-fit: contain; }
.md4-pv-thumb .q-skeleton { width: 100%; height: 150px; }
.md4-pv-title { font-size: 16px; font-weight: 700; line-height: 1.35; color: #37352f; overflow-wrap: anywhere; }
.md4-pv-auth { font-size: 12.5px; line-height: 1.45; color: #6b6862; }
.md4-pv-bib { font-size: 12.5px; color: #9a968e; }
.md4-pv-abs { font-size: 13.5px; line-height: 1.55; color: #37352f; white-space: pre-line; overflow-wrap: anywhere; }
.md4-pv-abs.none { color: #9a968e; }
.md4-pv-notes { font-size: 12.5px; line-height: 1.5; color: #6b6862; background: #fbfaf9; border-radius: 6px;
  padding: 8px 10px; white-space: pre-line; overflow-wrap: anywhere; }
.md4-pv-ok { font-size: 11.5px; color: #21ba45; }
/* 고칠 수 있는 서지·초록 칸 — 누르면 그 자리에서 입력칸(Enter·칸 밖 클릭 = 저장, Esc = 취소). 보일 때는 글자
   그대로이고 hover 때만 옅은 바탕 + 오른쪽 끝 연필. 편집 중에는 메모 탭 이름 바꾸기(.md4-notetab.editing)처럼
   흰 바탕에 1.5px 파란 테두리. 입력칸 글꼴은 칸의 것(제목 16px 굵게 · 본문 13.5px …)을 그대로 물려받는다. */
.md4-pv-ed { position: relative; min-width: 0; margin: 0 -5px; padding: 1px 20px 1px 5px; border-radius: 5px;
  cursor: text; transition: background .12s ease; }
.md4-pv-ed:hover { background: rgba(55,53,47,.05); }
.md4-pv-ed-v { overflow-wrap: anywhere; }
.md4-pv-ed-v.ph { color: #b4b0a8; font-weight: 400; font-size: 12.5px; }
.md4-pv-pen { position: absolute; right: 4px; top: 4px; color: #9a968e; opacity: 0; transition: opacity .12s ease;
  pointer-events: none; }
.md4-pv-ed:hover .md4-pv-pen { opacity: .75; }
.md4-pv-ed.editing, .md4-pv-ed.editing:hover { background: #fff; box-shadow: 0 0 0 1.5px #2383e2; padding-right: 5px; }
.md4-pv-ed.editing .md4-pv-pen { display: none; }
.md4-pv-ed .q-field__control, .md4-pv-ed .q-field__control-container, .md4-pv-ed .q-field__marginal {
  height: auto !important; min-height: 0 !important; padding: 0 !important; }
.md4-pv-ed .q-field__control { color: inherit !important; }  /* Quasar는 포커스 칸 글자를 파랗게 칠한다 */
.md4-pv-ed .q-field__native { min-height: 0 !important; padding: 0 !important; font: inherit; color: inherit;
  line-height: inherit; letter-spacing: inherit; }
.md4-pv-ed input.q-field__native { height: 1.45em; }
.md4-pv-ed textarea.q-field__native { resize: none; }
/* 연도 · venue · DOI · 약칭 — 이름표 | 값 두 칸 (Zotero 정보 칸처럼) */
.md4-pv-kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); column-gap: 12px; row-gap: 1px;
  align-items: start; }
.md4-pv-k { font-size: 11.5px; line-height: 1.45; color: #9a968e; padding-top: 2px; white-space: nowrap; }
.md4-pv-v { font-size: 12.5px; line-height: 1.45; color: #6b6862; }
/* 이름 규칙 힌트 — 서지를 고쳐 기준명이 폴더 이름과 달라지면 한 줄 */
.md4-pv-hint { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; font-size: 12px; color: #9a968e;
  line-height: 1.5; }
.md4-pv-hint-n { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px; color: #6b6862;
  background: rgba(55,53,47,.06); border-radius: 4px; padding: 0 5px; overflow-wrap: anywhere; }
.md4-pv-hint .q-btn { min-height: 0; padding: 0 4px; font-size: 12px; }
.md4-pv-foot .md4-pv-redo .q-btn { font-size: 12px; }
/* EN/KO — 작은 세그먼트 */
.md4-seg { display: inline-flex; gap: 2px; padding: 2px; border-radius: 6px; background: rgba(55,53,47,.06); }
.md4-seg span { font-size: 11px; font-weight: 600; line-height: 1; padding: 3px 8px; border-radius: 4px; cursor: pointer;
  color: #9a968e; user-select: none; }
.md4-seg span.on { background: #fff; color: #37352f; box-shadow: 0 1px 2px rgba(0,0,0,.1); cursor: default; }
/* 단일 선택을 보는 중에 체크해 둔 논문이 있으면 — 일괄 동작으로 돌아가는 한 줄 */
.md4-pv-selnote { font-size: 12px; color: #2383e2; background: rgba(35,131,226,.08); border-radius: 6px;
  padding: 5px 9px; cursor: pointer; }
.md4-pv-selnote:hover { background: rgba(35,131,226,.14); }
.md4-pv-bulk .q-btn { width: 100%; }
.md4-pv-bulk .q-btn__content { justify-content: flex-start; }
.md4-pv-sel { font-size: 12.5px; color: #6b6862; line-height: 1.4; }
.md4-pv-empty { align-items: center; text-align: center; color: #9a968e; margin-top: 36px; }
.md4-pv-stat { font-size: 13px; color: #6b6862; }
.md4-menucap { font-size: 11px; font-weight: 700; letter-spacing: .05em; color: #9a968e; padding: 8px 16px 2px; }
/* 다크 모드 — 뷰어처럼 사이드바(레일)만 어둡게. 본문 열은 Quasar가 밝은 테마 그대로라(입력칸·카드 글자가
   밝은 바탕 기준) 목록까지 어둡게 칠하면 글자가 묻힌다. */
@media (prefers-color-scheme: dark) {
  .md4-hside { background: #1b1b1b; border-right-color: #333; }
  .md4-hfoot { border-top-color: #333; }
  .md4-hfoot .q-btn, .md4-hnew { color: #a8a8a8; }
  .md4-prow { color: #d4d4d4; }
  .md4-prow-more { color: #a8a8a8; }
  .md4-prow-n, .md4-prow-sub { color: #8a8a8a; }
  .md4-prow:hover { background: rgba(255,255,255,.07); }
  .md4-prow.on { background: rgba(90,150,230,.22); color: #7bb7ff; }
  .md4-prow.on .md4-prow-i { color: #7bb7ff; }
  .md4-prow.hot, html.md4-carddrag .md4-prow.hot[data-drop] { background: rgba(90,150,230,.3); box-shadow: inset 0 0 0 1.5px #7bb7ff; }
  /* 미리보기 열은 목록과 같은 이유로 밝게 둔다(뷰어 .md4-panel도 다크에서 밝다) — 사이드바와 맞닿지
     않으니 경계선·바닥 줄만 밝은 바탕 기준 그대로. 썸네일 바탕은 흰 그림이 바탕에 묻히지 않게 조금 짙게. */
  .md4-pv-thumb { background: #efeeeb; }
  /* 고칠 수 있는 칸도 밝은 미리보기 열을 따른다 — 편집 중 칸은 흰 바탕 그대로, 테두리만 다크 쪽 파랑과 맞춘다 */
  .md4-pv-ed.editing, .md4-pv-ed.editing:hover { background: #fff; box-shadow: 0 0 0 1.5px #3b8eea; }
}
"""

# 파일을 창 위로 끌면 목록 열이 놓을 자리가 된다 — 문서 수준에서 한 번만 건다(홈을 다시 지어도).
# - 'Files'가 든 끌기만 본다: 카드를 사이드바로 끄는 끌기(PAPER_MIME)는 업로드가 아니다.
# - 켜는 건 dragenter/dragover, 끄는 건 drop·dragend와 '잠깐 dragover가 끊김'(창 밖으로 나감)이다.
#   dragenter/dragleave 카운터만으로는 업로더가 펼쳐지며 밑의 요소가 바뀔 때 짝이 어긋나 깜빡이거나
#   켜진 채 남는다. dragover는 끄는 동안 계속(정지해 있어도 수백 ms마다) 오므로 타이머로 잇는다.
# - 업로더가 아닌 곳(사이드바·헤더)에 놓으면 브라우저가 그 PDF로 페이지를 바꿔 앱을 잃는다 → 막는다.
#   업로더(QUploader)는 자기 drop을 멈추므로(stopPropagation) 거품 단계 리스너에는 오지 않는다.
# - 홈이 아닐 때(논문 화면)는 아무것도 하지 않는다 — 요소는 이벤트 때마다 찾는다(홈은 다시 지어진다).
_DRAG_HTML = """
<script>
(function(){
  if (window.__md4FileDrag) return; window.__md4FileDrag = true;
  var html = document.documentElement, timer = null;
  function hasFiles(e){
    var ts = e.dataTransfer && e.dataTransfer.types;
    if (!ts) return false;
    for (var i = 0; i < ts.length; i++) if (ts[i] === 'Files') return true;
    return false;
  }
  function onHome(){ return !!document.querySelector('.md4-hmain .md4-upload'); }
  function off(){ clearTimeout(timer); timer = null; html.classList.remove('md4-filedrag'); }
  function on(){ html.classList.add('md4-filedrag'); clearTimeout(timer); timer = setTimeout(off, 1200); }
  document.addEventListener('dragenter', function(e){ if (hasFiles(e) && onHome()) on(); }, true);
  document.addEventListener('dragover', function(e){
    if (!hasFiles(e)) return;
    e.preventDefault();                      // 어디에 놓아도 페이지가 PDF로 바뀌지 않게
    if (onHome()) on();
  }, true);
  document.addEventListener('dragleave', function(e){
    // 창 밖으로 나감 — Safari는 relatedTarget이 늘 null이라 좌표로 가린다
    if (!hasFiles(e)) return;
    var x = e.clientX, y = e.clientY;
    if ((x <= 0 && y <= 0) || x >= window.innerWidth || y >= window.innerHeight || x < 0 || y < 0) off();
  }, true);
  document.addEventListener('drop', function(e){ if (hasFiles(e)) off(); }, true);
  document.addEventListener('drop', function(e){ if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener('dragend', off, true);
  // 카드 끌기 — 카드의 data-md4-root를 싣는다 (카드마다 핸들러를 달지 않게 한곳에서)
  document.addEventListener('dragstart', function(e){
    var c = e.target && e.target.closest ? e.target.closest('[data-md4-root]') : null;
    if (!c || !e.dataTransfer) return;
    e.dataTransfer.setData('%(mime)s', c.getAttribute('data-md4-root'));
    e.dataTransfer.effectAllowed = 'copy';
    html.classList.add('md4-carddrag');
  });
  document.addEventListener('dragend', function(){ html.classList.remove('md4-carddrag'); }, true);
})();
</script>
""" % {"mime": PAPER_MIME}

# 사이드바 프로젝트 행 — 카드를 끌어 올리면 표시하고, 놓으면 논문 경로만 서버로 보낸다
_ROW_DRAGOVER_JS = ("(e) => { const t = e.dataTransfer && e.dataTransfer.types; "
                    f"if (t && Array.from(t).includes('{PAPER_MIME}')) "
                    "{ e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; e.currentTarget.classList.add('hot'); } }")
_ROW_DRAGLEAVE_JS = "(e) => e.currentTarget.classList.remove('hot')"
_ROW_DROP_JS = ("(e) => { e.currentTarget.classList.remove('hot'); "
                f"const r = e.dataTransfer && e.dataTransfer.getData('{PAPER_MIME}'); "
                "if (r) { e.preventDefault(); emit(r); } }")
_STOP_JS = "(e) => e.stopPropagation()"

_SORTS = {"recent": "최근순", "name": "제목순", "year": "연도순", "translated": "번역됨 먼저"}
_PHASE_ICON = {"pending": "schedule", "extracting": "sync", "cite": "sync", "glossary": "sync",
               "meta": "sync", "bib": "sync", "done": "check_circle", "failed": "error"}
_AI_PHASES = ("cite", "glossary", "meta")


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


def field_text(key: str, value) -> tuple[str, str]:  # noqa: ANN001
    """저장된 값 → (보이는 글자, 입력칸 글자). 저자는 보일 때 쉼표로, 고칠 때는 한 줄에 한 명."""
    if key == "authors":
        names = [str(a) for a in (value or []) if str(a).strip()]
        return ", ".join(names), "\n".join(names)
    s = "" if value is None else str(value)
    return s, s


def parse_field(key: str, raw) -> object:  # noqa: ANN001
    """입력칸 글자 → 저장할 값. 연도가 숫자가 아니면 ValueError (범위 검사는 update_fields가 한다).

    제목은 줄바꿈·겹친 공백을 한 칸으로(Shift+Enter로 넣은 줄바꿈이 제목에 남지 않게), 저자는 줄마다
    한 명(빈 줄은 버린다), 초록은 문단 줄바꿈을 그대로 둔다.
    """
    s = "" if raw is None else str(raw)
    if key == "authors":
        return [a.strip() for a in s.replace("\r", "").split("\n") if a.strip()]
    if key == "year":
        s = s.strip()
        if not s:
            return None
        if not s.isdigit():
            raise ValueError("연도는 숫자로 입력하세요 (예: 2024).")
        return int(s)
    if key == "title":
        return " ".join(s.split())
    return s.strip()


def _same_base(stem: str, base: str) -> bool:
    """폴더 이름이 이미 규칙대로인지 — 같은 이름의 다른 논문이 있어 `X (2)`처럼 유일화된 것도 규칙대로다."""
    import re

    return stem == base or re.fullmatch(re.escape(base) + r" \(\d+\)", stem) is not None


def _stored_pids(r: dict) -> list[str]:
    """recent_workdirs 행에 적힌 소속 목록 — 새 키 `projects`, 없으면 옛 키 `project` 하나."""
    raw = r.get("projects")
    if raw is None:
        raw = [r["project"]] if r.get("project") else []
    return [str(p) for p in raw if p]


def _preview_module():  # noqa: ANN202 — md4paper.preview (없으면 None — 미리보기는 빈 값으로 그린다)
    try:
        from md4paper import preview
    except ImportError:
        return None
    return preview


def preview_open() -> bool:
    """미리보기 열을 펴 둘지 — 전역 설정 [ui].home_preview (뷰어 패널 구성처럼 다음 실행에도 기억)."""
    v = config.load_config().get("ui", {}).get("home_preview")
    return v if isinstance(v, bool) else True


def _has_source_pdf(wd: WorkDir) -> bool:
    """원본이 PDF인지 (UIController.source_pdf와 같은 규칙 — manifest까지 읽지 않게 meta.json만)."""
    try:
        src = json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source") or ""
    except (OSError, ValueError, AttributeError):
        return False
    return src.lower().endswith(".pdf") and Path(src).is_file()


def build_home(state: dict) -> None:
    """홈 화면 — 프로젝트 사이드바 | 논문 목록. (app.build_home이 여기로 넘긴다)"""
    from nicegui import background_tasks, run, ui

    from md4paper import bibsource, paper_meta
    from md4paper.extract import DEFAULT_BACKEND, available_backends
    from md4paper.ui.app import (
        _CHROME_CSS, _EXPORT_TARGET, _EXTLINK_HTML, _QUEUE_ACTIVE, _pdf_page_count, _process_queue,
        _relative_time, _review_url, new_window_button, open_source_pdf, save_source, tab_bar, wd_token,
    )
    from md4paper.ui.controller import UIController
    from md4paper.workdir import delete_workdir, recent_workdirs, set_hidden, set_pinned

    def ws() -> Path:
        """작업 폴더 — 설정에서 바꿀 수 있으므로 항상 state에서 읽는다."""
        return state["upload_dir"]

    spa.add_css(_CHROME_CSS)  # 헤더 탭 줄 + .md4-body 틀 + 사이드바 색 토큰
    spa.add_css(HOME_CSS)
    spa.add_css(settings_dialog.CSS)
    spa.add_body_html(_EXTLINK_HTML)  # 바깥 링크는 새 창(앱 창이면 시스템 브라우저)으로
    spa.add_body_html(_DRAG_HTML)
    # 논문 화면에서 열어 둔 서랍·툴팁(본문 밖 오버레이라 화면을 갈아 끼워도 남는다)은 홈에서 닫는다
    ui.run_javascript("document.querySelectorAll('#md-anno-panel.open, #md-chat-panel.open')"
                      ".forEach(function(e){ e.classList.remove('open'); });"
                      "var t = document.getElementById('md-cite-tip'); if (t) t.style.opacity = '0';"
                      "document.documentElement.classList.remove('md4-filedrag');")
    # 홈도 탭 하나다 — 맨 앞 '홈' 탭이 활성, 연 논문 탭들과 '+'(논문 골라 열기)가 그대로 보인다
    with spa.header():
        tabs_bar = tab_bar(state, None)  # 서지를 고치면 탭 제목(paper_title)도 따라가게 다시 그린다
    scroll_memory.install("home")  # 논문을 열었다 돌아와도 목록의 그 자리
    find_bar.install()  # Cmd/Ctrl+F 페이지 찾기 (앱 창에는 브라우저 찾기 바가 없다)
    state.setdefault("queue", [])
    state.setdefault("recent_new", {})

    backend_ready = bool(available_backends())
    # 고른 범위(scope)는 목록 필터 + 새로 올릴 논문의 소속 — 앱을 다시 켜도 같은 묶음을 보게 기억한다.
    view = {"q": "", "sort": "recent", "show_hidden": False, "scope": projects.active()}
    selected: set[str] = set()  # 체크박스로 고른 워크디렉토리 root (문자열) — 일괄 동작용
    # 한 번 클릭으로 고른 논문(미리보기) — 홈 인스턴스에만 둔다. seq는 미리보기를 그릴 때마다 올라
    # 늦게 도착한 초록·이미지를 버리는 데 쓴다. bulk: 마지막 동작이 체크박스였는지(미리보기 자리를 일괄
    # 동작 칸으로). lang: 초록 EN/KO 고른 값(None = 번역본이 있으면 KO).
    pick: dict = {"root": None, "seq": 0, "bulk": False, "lang": None}
    pv: dict = {"open": preview_open(), "col": None, "btn": None}
    card_els: dict = {}  # root → 카드 요소 (선택 표시를 목록을 다시 그리지 않고 옮기려고)
    card_refs: dict = {}  # root → {title, tip, bib} 라벨 — 서지를 고치면 그 카드 글자만 바꾼다
    busy_meta: set[str] = set()  # '다시 찾기'·'다시 추출'이 도는 논문 root — 두 번 누르지 않게
    meta_lock = asyncio.Lock()  # 칸을 연달아 고칠 때 paper_meta.json 읽고-쓰기가 겹쳐 앞의 값을 잃지 않게
    loads: dict = {}  # (root, 종류, 언어) → asyncio.Task — 같은 것을 두 번 읽지 않게(EN/KO 토글이 즉시)
    preview = _preview_module()
    data: dict = {"rows": [], "by_root": {}, "projs": [], "names": {}}

    # ===== 한 번 훑기 — 사이드바 편수와 목록이 같은 결과를 쓴다 =====
    def load_projects() -> None:
        """프로젝트 목록을 한 번 읽어 둔다 — 행마다 projects.normalize(파일 읽기)를 부르지 않게."""
        data["projs"] = projects.all_projects()
        data["names"] = {p["id"]: p["name"] for p in data["projs"]}
        valid = data["names"]
        for r in data["rows"]:  # 없어진 프로젝트는 어디서나 미분류로 (projects.normalize_many와 같은 규칙)
            r["pids"] = [p for p in dict.fromkeys(_stored_pids(r)) if p in valid]
        if view["scope"] not in (projects.ALL, projects.NONE) and view["scope"] not in valid:
            view["scope"] = projects.ALL  # 보던 프로젝트가 지워졌으면 전체로

    def scan() -> None:
        # limit을 크게 주는 이유: 기본값(20)이면 21편째부터는 검색·프로젝트 필터에 아예 걸리지 않아
        # "그 프로젝트에 논문이 없다"고 보이고, 편수도 사이드바와 어긋난다.
        rows = recent_workdirs(ws(), limit=100_000, include_hidden=True)
        data["rows"] = rows
        data["by_root"] = {str(r["root"]): r for r in rows}
        load_projects()
        selected.intersection_update(data["by_root"])  # 사라진 논문은 선택에서도

    def name_of(pid: str) -> str:
        return data["names"].get(pid, projects.NONE_LABEL)

    def live_rows() -> list[dict]:
        """목록에 나올 수 있는 논문 — 아직 큐에서 처리 중인 것은 뺀다.

        추출 후 참고문헌·용어집·서지 정리가 도는 중에 열면 AI 설정이 덜 된 상태라 혼란스럽다 →
        전체 처리가 끝난 뒤에만 목록에 나온다."""
        busy = {q.get("wd_root") for q in state["queue"] if q["status"] in _QUEUE_ACTIVE and q.get("wd_root")}
        return [r for r in data["rows"] if str(r["root"]) not in busy] if busy else list(data["rows"])

    def in_scope(r: dict, scope: str) -> bool:
        if scope == projects.ALL:
            return True
        if scope == projects.NONE:
            return not r["pids"]
        return scope in r["pids"]

    def counts() -> dict[str, int]:
        """사이드바 편수 — 숨긴 논문은 빼고(목록에 보이는 것과 같게), 소속 프로젝트마다 한 번씩."""
        n = {projects.ALL: 0, projects.NONE: 0}
        for r in live_rows():
            if r["hidden"]:
                continue
            n[projects.ALL] += 1
            if not r["pids"]:
                n[projects.NONE] += 1
            for p in r["pids"]:
                n[p] = n.get(p, 0) + 1
        return n

    def refresh_views(rescan: bool = False) -> None:
        """사이드바·머리·목록을 함께 맞춘다 (rescan이면 작업 폴더를 다시 훑는다)."""
        if rescan:
            scan()
        sidebar_rows.refresh()
        head.refresh()
        more_menu.refresh()
        drop_caption.refresh()
        recent_list.refresh()
        preview_pane.refresh()
        sync_preview()

    # 대화상자는 목록·사이드바 밖의 고정 자리에 짓는다 — 카드 메뉴 안에서 지으면 목록을 다시 그릴 때
    # 열려 있던 대화상자까지 함께 지워진다.
    host = ui.element("div").style("display:none")
    settings_open: dict = {"fn": None}

    def open_settings(tab: str | None = None) -> None:
        if settings_open["fn"] is None:
            with host:
                settings_open["fn"] = settings_dialog.build_settings(
                    state, on_change=lambda: (refresh_views(rescan=True), ws_label.refresh()))
        settings_open["fn"](tab)

    # ===== 프로젝트 고르기·만들기·고치기 =====
    def pick_scope(value: str) -> None:
        """범위 고르기 — 목록 필터·새 논문의 소속·드롭 안내가 한 번에 따라온다."""
        view["scope"] = value
        projects.set_active(value)  # 다음 실행에도 같은 묶음을 보게
        refresh_views()

    def new_project(for_root: str | None = None) -> None:
        """새 프로젝트 — 만드는 길은 이 대화상자 하나뿐이다(사이드바 '새 프로젝트'와 카드 태그 메뉴의
        '새 프로젝트 만들기…'가 같은 것을 연다).

        카드에서 왔으면 보던 범위는 건드리지 않고 그 논문만 새 프로젝트에 더한다 — 논문 한 편을
        넣으려던 참인데 목록이 통째로 새 프로젝트로 좁혀지면 방금 보던 자리를 잃는다.
        """
        async def saved(proj: dict, created: bool) -> None:  # noqa: ARG001
            load_projects()
            if for_root is not None:
                r = data["by_root"].get(for_root)
                await set_members([for_root], (r["pids"] if r else []) + [proj["id"]])
                refresh_views()
            else:
                pick_scope(proj["id"])  # 만들자마자 그 묶음을 보고, 새 논문도 그리로 들어가게

        with host:
            settings_dialog.open_project_dialog(state, None, saved)

    def edit_project(pid: str) -> None:
        def saved(proj: dict, created: bool) -> None:  # noqa: ARG001
            load_projects()
            refresh_views()

        with host:
            settings_dialog.open_project_dialog(state, pid, saved, on_delete=confirm_delete_project)

    def rename_project(pid: str) -> None:
        def save() -> None:
            if not projects.rename(pid, inp.value):
                ui.notify("이름을 입력하세요.", type="warning")
                return
            dlg.close()
            load_projects()
            refresh_views()

        with host, ui.dialog() as dlg, ui.card().classes("gap-2").style("width:380px"):
            ui.label("컬렉션 이름 바꾸기").classes("font-bold")
            inp = ui.input(value=name_of(pid)).props("dense outlined autofocus").classes("w-full")
            inp.on("keydown.enter", save)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("저장", on_click=save).props("unelevated no-caps color=primary")
        dlg.open()

    async def pick_project_folder(pid: str) -> None:
        cur = projects.root_of(pid) or ws()
        picked = await desktop.choose_folder(f"'{name_of(pid)}' 컬렉션 폴더 선택", str(cur))
        if not picked:  # 취소면 조용히 원래 값 유지
            return
        projects.set_root(pid, picked)  # ko/·pdf/ 자리까지 만들어 준다
        load_projects()
        refresh_views()
        ui.notify(f"'{name_of(pid)}' 폴더 → {picked}", type="positive")

    def confirm_delete_project(pid: str, after=None) -> None:  # noqa: ANN001 — () -> None
        """묶음만 지우고 논문·파일은 남긴다 — 되돌릴 수 없는 건 '소속'뿐이라 그것만 확인받는다.

        after는 프로젝트 대화상자에서 왔을 때 그 대화상자를 닫는 함수 (지우기로 했을 때만 부른다)."""
        name = name_of(pid)
        n = sum(1 for r in data["rows"] if pid in r["pids"])  # 숨긴 논문까지 — 소속이 풀리는 건 다 센다

        async def do_delete() -> None:
            dlg.close()
            if after is not None:
                after()
            # 논문에서 떼는 일까지 한곳에서(projects.delete) — 다른 프로젝트 소속은 그대로 남는다
            await run.io_bound(projects.delete, pid, ws())
            refresh_views(rescan=True)
            ui.notify(f"'{name}' 컬렉션을 지웠습니다" + (f" · {n}편에서 소속을 뗐습니다" if n else ""),
                      type="positive")

        with host, ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:420px"):
            ui.label(f"'{name}' 컬렉션을 지울까요?").classes("font-bold")
            ui.label(f"그 논문 {n}편에서 이 소속만 떼어 냅니다 — 다른 컬렉션 소속과 폴더·파일은 그대로 "
                     "둡니다.").classes("text-xs text-gray-500")
            with ui.row().classes("justify-end w-full items-center gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("컬렉션 삭제", icon="delete", on_click=do_delete) \
                    .props("unelevated no-caps color=negative")
        dlg.open()

    # ===== 소속(N:M) 바꾸기 — 저장 위치의 사본까지 따라가게 library.set_memberships로 =====
    async def set_members(roots: list[str], pids: list[str]) -> int:
        """논문들의 소속을 pids로 — 저장 위치의 사본도 새 소속에 맞춰 옮기고 정리한다. 반환: 쓴 사본 수."""
        copies = 0
        new = list(dict.fromkeys(p for p in pids if p in data["names"]))
        for root in roots:
            try:
                copies += len(await run.io_bound(library.set_memberships, WorkDir(Path(root)), new))
            except Exception as ex:  # noqa: BLE001 — 한 편이 실패해도 나머지는 계속
                ui.notify(f"소속을 바꾸지 못했습니다: {Path(root).stem} — {ex}", type="negative")
                continue
            r = data["by_root"].get(root)
            if r is not None:
                r["pids"] = list(new)
        return copies

    async def toggle_member(root: str, pid: str) -> bool:
        """카드 체크 메뉴 한 칸 — 넣거나 뺀다. 반환: 지금 소속인지."""
        r = data["by_root"].get(root)
        cur = list(r["pids"]) if r else []
        now_in = pid not in cur
        await set_members([root], cur + [pid] if now_in else [p for p in cur if p != pid])
        sidebar_rows.refresh()  # 편수만 — 목록은 메뉴를 닫을 때 다시 그린다(메뉴가 눈앞에서 사라지지 않게)
        head.refresh()
        return pid in (data["by_root"].get(root) or {}).get("pids", [])

    async def add_dropped(root: str, pid: str) -> None:
        """카드를 사이드바 프로젝트 행에 놓음 — N:M이니 옮기지 않고 **더한다**."""
        r = data["by_root"].get(str(root))
        if r is None:
            return
        if pid in r["pids"]:
            ui.notify(f"이미 '{name_of(pid)}'에 들어 있습니다.", type="info", timeout=1500)
            return
        await set_members([str(root)], r["pids"] + [pid])
        refresh_views()
        ui.notify(f"'{name_of(pid)}'에 더했습니다", type="positive", timeout=1500)

    async def bulk_member(pid: str, add: bool) -> None:
        roots = sorted(selected)
        if not roots:
            ui.notify("선택된 논문이 없습니다.", type="warning")
            return
        changed = 0
        copies = 0
        for root in roots:
            cur = (data["by_root"].get(root) or {}).get("pids", [])
            if add == (pid in cur):
                continue  # 이미 그 상태
            changed += 1
            copies += await set_members([root], cur + [pid] if add else [p for p in cur if p != pid])
        refresh_views()
        verb = "에 더했습니다" if add else "에서 뺐습니다"
        ui.notify(f"{changed}편을 '{name_of(pid)}'{verb}" + (f" · 사본 {copies}개" if copies else ""),
                  type="positive" if changed else "info")

    # ===== 카드 동작 =====
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
        name, payload = ctrl.export_zip(which, config.resolve_export_target())
        await desktop.deliver(name, payload)

    def toggle_pin(r: dict) -> None:
        """목록 맨 위 '고정한 논문' 구역으로 올리기/내리기 (status.json에만 표시 — 탭과는 무관)."""
        if not set_pinned(r["root"], not r["pinned"]):
            ui.notify("고정 처리 실패 (경로 확인).", type="negative")
            return
        r["pinned"] = not r["pinned"]
        r["pinned_at"] = time.time() if r["pinned"] else 0.0
        recent_list.refresh()
        preview_pane.refresh()

    def hide_paper(r: dict, hidden: bool = True) -> None:
        """목록 표시만 끄기/되돌리기 (파일은 그대로 — status.json에 표시)."""
        if not set_hidden(r["root"], hidden):
            ui.notify("처리 실패 (경로 확인).", type="negative")
            return
        r["hidden"] = hidden
        if hidden and r["pinned"]:  # 숨긴 논문이 고정 구역에 남아 있으면 앞뒤가 안 맞는다
            set_pinned(r["root"], False)
            r["pinned"], r["pinned_at"] = False, 0.0
        selected.discard(str(r["root"]))  # 숨기면 일괄 다운로드 선택에서도 빠진다
        refresh_views()
        ui.notify("목록에서 숨겼습니다 — 파일은 그대로입니다." if hidden else "목록에 다시 표시합니다.",
                  type="positive")

    def confirm_delete(r: dict) -> None:
        """목록에서 없애는 두 가지 방법을 고르게 한다 — 표시만 숨기기(안전) vs 파일 영구 삭제."""
        with host, ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:460px"):
            ui.label("이 논문을 목록에서 없앨까요?").classes("font-bold")
            ui.label(r["title"]).classes("text-sm text-gray-500")
            ui.label("· 목록에서 숨기기 — 파일은 그대로 두고 목록에서만 감춥니다. "
                     "나중에 ⋯ 메뉴의 '숨긴 논문 보기'에서 되돌릴 수 있습니다.").classes("text-xs text-gray-500")
            ui.label("· 파일 삭제 — 작업 디렉토리(원본 PDF·마크다운·이미지)까지 지웁니다. 되돌릴 수 없습니다.") \
                .classes("text-xs text-gray-500")
            # 저장 위치 사본은 작업 폴더 밖(사용자 볼트)이라, 안 지우면 고아로 남는다 → 기본 켬.
            # 사본은 **폴더를 가진 소속 프로젝트마다** 있고, 그런 소속이 없으면 공통 폴더에 있다.
            targets = [p for p in r["pids"] if projects.has_dirs(p)] or [""]
            lib_dirs = [str(d) for t in targets for d in library.dirs_for(t).values() if d]
            drop_lib = ui.checkbox("저장 위치의 사본도 함께 삭제", value=True) \
                .props("dense").classes("text-sm") if lib_dirs else None
            for d in dict.fromkeys(lib_dirs):  # 어느 폴더가 지워지는지 경로를 그대로 (중복 제거)
                ui.label(d).classes("text-xs text-gray-400 ml-8 break-all leading-tight")
            if any(library.bib_path(t) is not None for t in targets):
                ui.label("references.bib의 이 논문 항목도 함께 지웁니다.") \
                    .classes("text-xs text-gray-400 ml-8 leading-tight")
            # 실제 삭제(delete_workdir → remove_stem_everywhere)는 공통 + **모든** 프로젝트 폴더를
            # 훑는다 — 소속을 바꿔 온 논문의 옛 사본까지 정리하기 위해서다. 미리 밝혀 둔다.
            if lib_dirs and any(projects.has_dirs(p["id"]) for p in data["projs"] if p["id"] not in targets):
                ui.label("전에 다른 컬렉션에 넣어 뒀다면 그 폴더에 남은 사본도 함께 정리합니다.") \
                    .classes("text-xs text-gray-400 ml-8 leading-tight")

            def do_delete() -> None:
                ok = delete_workdir(r["root"], ws(), with_library=bool(drop_lib and drop_lib.value))
                dlg.close()
                if ok:
                    selected.discard(str(r["root"]))
                    data["rows"] = [x for x in data["rows"] if x is not r]
                    data["by_root"].pop(str(r["root"]), None)
                    refresh_views()
                    ui.notify("삭제됨", type="positive")
                else:
                    ui.notify("삭제 실패 (경로 확인).", type="negative")

            def do_hide() -> None:
                dlg.close()
                hide_paper(r, True)

            with ui.row().classes("justify-end w-full items-center gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("파일 삭제", icon="delete_forever", on_click=do_delete) \
                    .props("flat no-caps color=negative")
                ui.button("목록에서 숨기기", icon="visibility_off", on_click=do_hide) \
                    .props("unelevated no-caps color=primary")
        dlg.open()

    # ===== 일괄 동작 =====
    async def download_bulk(which: str) -> None:
        from md4paper.ui.controller import bulk_zip

        roots = [Path(p) for p in selected]
        if not roots:
            ui.notify("선택된 논문이 없습니다.", type="warning")
            return
        name, payload = bulk_zip(roots, which, config.resolve_export_target())
        await desktop.deliver(name, payload)

    async def download_mobile() -> None:
        """선택한 논문들을 폰에서 읽는 HTML 한 장으로 (§mobile) — 동기화 폴더에 두면 밖에서도 열린다."""
        from md4paper.ui import mobile

        roots = [Path(p) for p in selected]
        if not roots:
            ui.notify("선택된 논문이 없습니다.", type="warning")
            return
        n = ui.notification(f"모바일 HTML 만드는 중… ({len(roots)}편)", spinner=True, timeout=None)
        try:
            html = await run.io_bound(mobile.build, roots)
        except Exception as e:  # noqa: BLE001
            ui.notify(f"모바일 HTML을 만들지 못했습니다: {e}", type="negative")
            return
        finally:
            n.dismiss()
        await desktop.deliver(mobile.file_name(len(roots)), html.encode("utf-8"), media_type="text/html")

    async def save_to_library(roots: list[Path]) -> None:
        """논문들을 각자의 자리(소속 프로젝트 폴더들 > 공통 저장 위치)로 내보내기."""
        if not settings_dialog.any_library_dirs():
            ui.notify("저장 위치가 지정돼 있지 않습니다 — ⚙ 설정의 '저장 위치'나 컬렉션 폴더를 정하세요.",
                      type="warning")
            return
        if not roots:
            ui.notify("내보낼 논문이 없습니다.", type="warning")
            return
        ok, failed = await run.io_bound(library.export_many, roots)
        ui.notify(f"{ok}편 저장됨" + (f" · {failed}편 실패" if failed else ""),
                  type="positive" if ok else "warning")

    def clear_selection() -> None:
        selected.clear()
        pick["bulk"] = False
        recent_list.refresh()
        preview_pane.refresh()
        sync_preview()

    def toggle_sel(root: str, on: bool) -> None:
        selected.add(root) if on else selected.discard(root)
        pick["bulk"] = bool(selected)  # 체크 = 일괄 선택 → 미리보기 자리가 일괄 동작 칸으로
        sync_preview()
        preview_pane.refresh()

    def set_export_target(value: str) -> None:
        config.set_section_value("output", "export_target", value)
        more_menu.refresh()
        ui.notify(f"내보내기 형식 · {_EXPORT_TARGET[value]}", type="positive", timeout=1200)

    # ===== 업로드 → 큐 =====
    async def handle(e) -> None:  # noqa: ANN001 — NiceGUI upload 이벤트 (파일마다 발화)
        fname = e.file.name
        payload = await e.file.read()
        if any(q["name"] == fname and q["status"] in _QUEUE_ACTIVE for q in state["queue"]):
            ui.notify(f"{fname} 은(는) 이미 대기열에 있습니다.", type="info")
            return
        pages = _pdf_page_count(payload) if fname.lower().endswith(".pdf") else 0
        src = await run.io_bound(save_source, payload, fname, ws())
        state["queue"].append({
            "name": fname, "src_path": str(src), "pages": pages,
            "backend": DEFAULT_BACKEND, "ocr": config.resolve_ocr(),
            # 올릴 때 고른 묶음을 항목에 새겨 둔다 — 변환 도중 범위를 바꿔도 이 논문의 소속은 그대로.
            "project": projects.assign_target(view["scope"]),
            "status": "pending", "error": "", "garbled": 0, "since": 0.0, "done_at": 0.0,
        })
        queue_panel.refresh()
        if not state.get("worker_running"):  # 워커가 놀고 있으면 깨운다 (하나만 돎)
            background_tasks.create(_process_queue(state))

    # ===== 왼쪽 — 프로젝트 사이드바 =====
    def prow(value: str, label: str, n: int, icon: str, *, project: bool = False, sub: str = "") -> None:
        """사이드바 한 줄. `sub`는 이름 아래 옅은 둘째 줄 — 폴더를 가진 컬렉션의 폴더 이름(어디로 쌓이는지)."""
        on = view["scope"] == value
        row = ui.element("div").classes("md4-prow" + (" on" if on else ""))
        with row:
            ui.icon(icon, size="18px").classes("md4-prow-i")
            with ui.element("div").classes("md4-prow-t"):
                ui.label(label).classes("md4-prow-name")
                if sub:
                    ui.label(sub).classes("md4-prow-sub")
            ui.label(str(n)).classes("md4-prow-n")
            if project:
                more = ui.button(icon="more_horiz", color=None) \
                    .props("flat dense round size=sm").classes("md4-prow-more")
                more.on("click", js_handler=_STOP_JS)  # 행 클릭(범위 고르기)까지 번지지 않게
                with more, ui.menu().props("anchor='bottom right' self='top right'"):
                    ui.menu_item("이름 바꾸기", on_click=lambda _, p=value: rename_project(p)).props("dense")
                    ui.menu_item("폴더 정하기…", on_click=lambda _, p=value: pick_project_folder(p)).props("dense")
                    ui.menu_item("설정…", on_click=lambda _, p=value: edit_project(p)).props("dense")
                    ui.separator()
                    ui.menu_item("컬렉션 삭제", on_click=lambda _, p=value: confirm_delete_project(p)) \
                        .props("dense").classes("text-negative")
            else:
                ui.element("span").classes("md4-prow-gap")
        if project:
            row.props("data-drop")
            row.on("dragover", js_handler=_ROW_DRAGOVER_JS)
            row.on("dragleave", js_handler=_ROW_DRAGLEAVE_JS)
            row.on("drop", lambda e, p=value: add_dropped(e.args, p), js_handler=_ROW_DROP_JS)
        if not on:
            row.on("click", lambda _, v=value: pick_scope(v))

    @ui.refreshable
    def sidebar_rows() -> None:
        n = counts()
        prow(projects.ALL, "모든 논문", n[projects.ALL], "library_books")
        ui.label("컬렉션").classes("md4-panel-title md4-hcap")
        for p in data["projs"]:  # 만든 순서대로
            root = str(p.get("root") or "").strip()  # projects.root_of와 같은 규칙 — 파일을 다시 읽지 않게
            prow(p["id"], p["name"], n.get(p["id"], 0), "folder_special", project=True,
                 sub=Path(root).expanduser().name if root else "")
        prow(projects.NONE, projects.NONE_LABEL, n[projects.NONE], "inbox")
        ui.button("새 컬렉션", icon="add", color=None, on_click=lambda: new_project()) \
            .props("flat dense no-caps").classes("md4-hnew w-full") \
            .tooltip("이름만 정하면 만들어집니다 — 폴더는 나중에 정해도 됩니다")

    @ui.refreshable
    def ws_label() -> None:
        cur = str(ws())
        ui.label("\u200e" + cur).classes("md4-hws").tooltip(f"작업 폴더 — {cur}\n(⚙ 설정 → 저장 위치에서 바꿉니다)")

    # ===== 오른쪽 — 머리 =====
    def scope_label() -> str:
        s = view["scope"]
        return "모든 논문" if s == projects.ALL else (projects.NONE_LABEL if s == projects.NONE else name_of(s))

    @ui.refreshable
    def head() -> None:
        s = view["scope"]
        n = sum(1 for r in live_rows() if not r["hidden"] and in_scope(r, s))
        with ui.row().classes("items-baseline gap-2 no-wrap w-full"):
            ui.label(scope_label()).classes("md4-htitle truncate min-w-0")
            ui.label(f"{n}편").classes("md4-hcount shrink-0")
        if s not in (projects.ALL, projects.NONE):
            root = projects.root_of(s)
            with ui.row().classes("items-center gap-1 no-wrap w-full"):
                ui.icon("folder" if root else "folder_off", size="15px").classes("text-grey-5")
                ui.label(f"{root}  ·  ko/  ·  pdf/" if root else "폴더 미지정 — 공통 저장 위치에 쌓입니다") \
                    .classes("md4-hpath truncate").tooltip(str(root) if root else "")
                ui.button(icon="edit", color=None, on_click=lambda: edit_project(s)) \
                    .props("flat dense round size=xs").classes("text-grey-6") \
                    .tooltip("이 컬렉션 설정 — 이름·폴더·이 컬렉션만 다르게")
        if not any(config.key_status().values()):
            ui.label("⚠ AI 키가 없어 참고문헌 정리·번역 기능이 꺼져 있습니다 — 눌러서 키 넣기") \
                .classes("md4-hwarn ai q-mt-xs").on("click", lambda: open_settings("ai"))
        if not backend_ready:
            ui.label("⚠ 추출 엔진(docling)이 설치되지 않았습니다. `uv sync`로 설치하세요 — "
                     "PDF 변환이 되지 않습니다(.md 입력은 가능).").classes("md4-hwarn err q-mt-xs")

    @ui.refreshable
    def drop_caption() -> None:
        target = projects.assign_target(view["scope"])
        ui.icon("cloud_upload", size="44px")
        ui.label(f"여기에 놓으면 '{name_of(target)}'에 추가" if target else "여기에 놓으면 미분류로 올리기") \
            .classes("md4-dropov-t")
        ui.label("PDF·마크다운 여러 개를 한꺼번에 — 순서대로 변환합니다").classes("md4-dropov-s")

    @ui.refreshable
    def more_menu() -> None:
        """목록 ⋯ — 자주 안 쓰는 것들: 숨긴 논문, 내보내기 형식, 이 목록 통째로 폴더에 저장."""
        s = view["scope"]
        n_hidden = sum(1 for r in live_rows() if r["hidden"] and in_scope(r, s))
        cur_target = config.resolve_export_target()
        with ui.menu().props("anchor='bottom right' self='top right'"):
            if n_hidden:  # 되돌릴 길이 늘 있도록 (숨김이 '영영 사라짐'이 되지 않게)
                ui.menu_item(f"숨긴 논문 {n_hidden}편 " + ("접기" if view["show_hidden"] else "보기"),
                             on_click=toggle_hidden_view).props("dense")
            else:
                ui.menu_item("숨긴 논문 없음").props("dense disable")
            ui.separator()
            ui.label("내보내기 형식 (EN·KO zip·폴더 저장)").classes("md4-menucap")
            for key, label in _EXPORT_TARGET.items():
                ui.menu_item(("✓ " if key == cur_target else "    ") + label,
                             on_click=lambda _, k=key: set_export_target(k)).props("dense")
            ui.separator()
            ui.menu_item("이 목록의 논문을 폴더로 저장",
                         on_click=lambda: save_to_library([r["root"] for r in visible_rows()])).props("dense")

    def toggle_hidden_view() -> None:
        view["show_hidden"] = not view["show_hidden"]
        more_menu.refresh()
        recent_list.refresh()

    # ===== 오른쪽 — 큐 · 목록 =====
    @ui.refreshable
    def queue_panel() -> None:
        q = state["queue"]
        running_jobs = jobs.running()
        if running_jobs:  # 논문 화면에서 띄운 번역 등 — 화면을 떠나도 서버에서 계속 돈다
            ui.label("백그라운드 작업").classes("md4-listcap")
            for j in running_jobs:
                pct = f" {int(j.fraction * 100)}%" if j.total else ""
                with ui.card().classes("w-full q-pa-sm cursor-pointer").on(
                        "click", lambda _, r=j.root: ui.navigate.to(_review_url(Path(r)))):
                    with ui.row().classes("items-center w-full no-wrap gap-2"):
                        ui.icon("autorenew", size="20px").classes("text-primary md4-spin")
                        with ui.column().classes("gap-0 flex-grow min-w-0"):
                            ui.label(j.title or Path(j.root).stem).classes("text-sm truncate w-full")
                            ui.label(f"{j.label} 중…{pct} · 눌러서 열기").classes("text-xs text-gray-400")
        if not q:
            return
        ui.label("변환 중").classes("md4-listcap")
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
            elif st == "bib":
                sub = f"⑤ 서지 정보 보강 중… (온라인) {el}초"
            elif st == "done":
                sub = "완료 → 아래 목록에서 열기"
                boost = it.get("boost") or {}
                bits = []
                if boost.get("record"):
                    bits.append("출판 기록")
                filled = (boost.get("refs") or {}).get("filled") or 0
                if filled:
                    bits.append(f"참고문헌 DOI {filled}건")
                if bits:
                    sub += " · 서지 " + "·".join(bits)
                if boost.get("retracted"):  # 철회 논문은 조용히 넘기면 안 된다
                    sub = "⚠ 철회된 논문입니다 — 인용 전 확인하세요 · " + sub
            else:
                sub = f"실패: {it['error']}"[:80]
            if it.get("project"):
                sub += f" · {name_of(it['project'])}"
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
                            .props("flat dense round size=sm color=grey").classes("shrink-0") \
                            .tooltip("취소" if st == "pending" else "닫기")

    def project_pick_menu(on_pick, only: set[str] | None = None, with_new: bool = False) -> None:  # noqa: ANN001
        """미리보기 일괄 동작 칸의 '컬렉션에 추가/빼기' 메뉴 — 프로젝트 이름만 늘어놓는다."""
        with ui.menu():
            shown = [p for p in data["projs"] if only is None or p["id"] in only]
            for p in shown:
                ui.menu_item(p["name"], on_click=lambda _, v=p["id"]: on_pick(v)).props("dense")
            if not shown:
                ui.menu_item("컬렉션이 없습니다").props("dense disable")
            if with_new:
                ui.separator()
                ui.menu_item("새 컬렉션 만들기…", on_click=lambda: new_project_for_selection()).props("dense")

    def new_project_for_selection() -> None:
        roots = sorted(selected)

        async def saved(proj: dict, created: bool) -> None:  # noqa: ARG001
            load_projects()
            for root in roots:
                cur = (data["by_root"].get(root) or {}).get("pids", [])
                await set_members([root], cur + [proj["id"]])
            refresh_views()

        with host:
            settings_dialog.open_project_dialog(state, None, saved)

    def visible_rows() -> list[dict]:
        """지금 범위·검색·숨김 보기로 걸러 정렬한 목록 (고정한 논문이 맨 앞)."""
        items = [r for r in live_rows() if in_scope(r, view["scope"])]
        if not view["show_hidden"]:
            items = [r for r in items if not r["hidden"]]
        q = (view["q"] or "").strip().lower()
        if q:
            items = [r for r in items if q in _haystack(r)]  # 제목·저자·venue·연도 검색
        srt = view["sort"]
        if srt == "name":
            items.sort(key=lambda r: r["title"].lower())
        elif srt == "year":
            items.sort(key=lambda r: (r.get("year") is None, -(r.get("year") or 0)))
        elif srt == "translated":
            items.sort(key=lambda r: (not r["has_ko"], -r["mtime"]))
        else:
            items.sort(key=lambda r: -r["mtime"])
        # 고정한 논문은 고른 정렬과 무관하게 맨 위 한 구역으로, 그 안에서는 고정한 순서로.
        # (정렬을 바꿔도 자주 보는 논문의 자리가 움직이지 않는 게 고정의 요점이다.)
        pinned = sorted((r for r in items if r["pinned"]), key=lambda r: r["pinned_at"])
        return pinned + [r for r in items if not r["pinned"]]

    # 카드 태그의 체크 메뉴는 **목록 전체가 하나**를 같이 쓴다. 카드마다 메뉴를 지으면 225편 × 프로젝트
    # 수만큼 요소가 생겨 범위를 바꿀 때마다 목록이 눈에 띄게 늦어진다(실측: 모든 논문 0.87초).
    # 태그를 누르면 그 카드의 칸으로 내용을 채우고 그 태그에 붙여(target) 연다.
    member_ui: dict = {"menu": None, "r": None, "tags": None, "changed": False}

    def open_member_menu(r: dict, tags_box, anchor) -> None:  # noqa: ANN001 — ui.element
        """카드 태그를 누르면 뜨는 체크 메뉴 — 프로젝트마다 넣기/빼기 + 새 프로젝트 만들기.

        한 칸을 눌러도 메뉴는 닫지 않고 그 칸·태그만 고친다(여러 개를 연달아 고를 수 있게).
        목록은 메뉴를 닫을 때 한 번 다시 그린다 — 지금 범위에서 빠진 논문이 그때 사라진다.
        """
        menu = member_ui["menu"]
        root = str(r["root"])
        member_ui.update(r=r, tags=tags_box)

        async def flip(pid: str, icon) -> None:  # noqa: ANN001 — ui.icon
            now_in = await toggle_member(root, pid)
            member_ui["changed"] = True
            icon.name = "check_box" if now_in else "check_box_outline_blank"
            icon.classes(replace="text-primary" if now_in else "text-grey-6")
            paint_tags(tags_box, r)

        menu.clear()
        with menu:
            ui.label("컬렉션에 넣기").classes("md4-menucap")
            for p in data["projs"]:
                on = p["id"] in r["pids"]
                with ui.menu_item(auto_close=False).props("dense") as item:
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ic = ui.icon("check_box" if on else "check_box_outline_blank", size="18px") \
                            .classes("text-primary" if on else "text-grey-6")
                        ui.label(p["name"])
                item.on("click", lambda _, pid=p["id"], i=ic: flip(pid, i))
            if not data["projs"]:
                ui.label("아직 컬렉션이 없습니다").classes("text-xs text-gray-400 q-px-md q-py-xs")
            ui.separator()
            ui.menu_item("새 컬렉션 만들기…", on_click=lambda: new_project(for_root=root)).props("dense")
        menu.props(f'target="#c{anchor.id}"')
        menu.open()

    def member_menu_closed() -> None:
        if member_ui["changed"]:
            member_ui["changed"] = False
            recent_list.refresh()
            preview_pane.refresh()  # 카드에서 고쳤으면 미리보기 태그도 (초록·이미지는 캐시라 다시 읽지 않는다)

    def paint_tags(tags_box, r: dict) -> None:  # noqa: ANN001 — ui.element
        tags_box.clear()
        with tags_box:
            if r["pids"]:
                for p in r["pids"]:
                    ui.label(name_of(p)).classes("md4-ptag")
            else:
                ui.label(projects.NONE_LABEL).classes("md4-ptag none")
            ui.icon("add", size="14px").classes("md4-ptag-add")

    @ui.refreshable
    def recent_list() -> None:
        items = visible_rows()
        if not items:
            # 비어 있는 이유를 갈라 준다 — 프로젝트를 골라 비었을 때 '작업 폴더를 바꾸라'고
            # 안내하면 엉뚱한 곳을 만지게 된다(방금 만든 프로젝트는 당연히 비어 있다).
            s, q = view["scope"], (view["q"] or "").strip()
            if q:
                ui.label("검색 결과가 없습니다.").classes("text-sm text-gray-400 q-mt-md")
                return
            with ui.column().classes("md4-empty w-full gap-1"):
                ui.icon("cloud_upload", size="44px").classes("text-primary")
                ui.label("PDF를 창 위로 끌어다 놓거나 '올리기'로 선택하세요").classes("text-base")
                if s == projects.NONE:
                    ui.label("미분류 논문이 없습니다 — 모든 논문이 컬렉션에 들어가 있습니다.") \
                        .classes("text-xs text-gray-400")
                elif s:
                    ui.label(f"'{name_of(s)}' 컬렉션에 아직 논문이 없습니다 — 여기서 올리거나, 다른 목록의 "
                             "카드를 왼쪽 이 컬렉션 행으로 끌어 놓으세요.").classes("text-xs text-gray-400")
                else:
                    ui.label("아직 변환한 논문이 없습니다. 여러 개를 올리면 순서대로 변환합니다.") \
                        .classes("text-xs text-gray-400")
                    if not any(r["hidden"] for r in data["rows"]):
                        # 다른 폴더에서 변환해 둔 논문이 안 보이는 것일 수 있다 — 실행 방식에 따라 작업 폴더
                        # 기본값이 달랐다. 어느 폴더를 보고 있는지와 바꾸는 길을 같이 알려 준다.
                        ui.label(f"보고 있는 작업 폴더: {ws()}").classes("text-xs text-gray-400 break-all")
                        ui.label("다른 폴더에서 변환했다면 ⚙ 설정의 '저장 위치 → 작업 폴더'에서 그 폴더를 "
                                 "고르세요.").classes("text-xs text-gray-400")
            return
        now = time.monotonic()
        new_roots = state.get("recent_new", {})
        card_els.clear()
        card_refs.clear()

        def cap(text: str, icon: str = "") -> None:
            with ui.element("div").classes("md4-listcap"):
                if icon:
                    ui.icon(icon, size="12px").classes("text-primary")
                ui.label(text)

        def card(r: dict) -> None:
            root = str(r["root"])
            is_new = (now - new_roots.get(root, 0)) < 10  # 방금 완료 → 하이라이트
            unopened = not r.get("opened")  # 한 번도 논문 화면을 안 연 논문
            bib = _bib_line(r)
            el = ui.element("div").classes("md4-pcard" + (" md4-recent-new" if is_new else "")
                                           + (" md4-pincard" if r["pinned"] else "")
                                           + (" sel" if root in selected else "")
                                           + (" on" if root == pick["root"] else ""))
            # 사이드바 프로젝트 행으로 끌어 놓으면 그 프로젝트에 더한다 (dragstart는 _DRAG_HTML이 한곳에서)
            el.props("draggable=true tabindex=0")
            el._props["data-md4-root"] = root  # noqa: SLF001 — 경로에 따옴표·공백이 있어도 안전하게
            card_els[root] = el
            # 한 번 = 선택(미리보기), 두 번·Enter = 논문 화면. 체크박스·태그·hover 단추는 카드까지 번지지
            # 않게 막는다 — 안 막으면 📌를 두 번 누르거나 체크박스를 빠르게 두 번 누를 때 논문이 열린다.
            el.on("click", lambda _, rt=root: select(rt))
            el.on("dblclick", lambda _, rt=r["root"]: open_wd(rt))
            el.on("keydown.enter", lambda _, rt=r["root"]: open_wd(rt))
            with el:
                ui.checkbox(value=root in selected, on_change=lambda e, rt=root: toggle_sel(rt, e.value)) \
                    .props("dense").classes("shrink-0") \
                    .on("click", js_handler=_STOP_JS).on("dblclick", js_handler=_STOP_JS)
                main = ui.column().classes("md4-pmain gap-0")
                with main:
                    # .nicegui-column은 align-items:flex-start — 자식이 컬럼 폭으로 늘어나지 않아
                    # w-full(컬럼 자식)·min-w-0(행 안 라벨) 없이는 truncate가 발동하지 않고
                    # 긴 제목이 카드·화면 밖으로 흘러넘친다.
                    with ui.row().classes("items-center gap-1.5 no-wrap w-full min-w-0"):
                        if unopened:
                            ui.element("span").classes("md4-new-dot").tooltip("아직 열지 않음")
                        title_lbl = ui.label(r["title"]).classes("md4-ptitle truncate min-w-0"
                                                                 + (" unopened" if unopened else ""))
                        with title_lbl:
                            title_tip = ui.tooltip(r["title"])
                    # 서지가 없어도 자리는 만들어 둔다 — 미리보기에서 저자·연도를 채우면 이 줄만 고쳐 보인다
                    bib_lbl = ui.label(bib).classes("md4-pbib truncate w-full")
                    bib_lbl.set_visibility(bool(bib))
                    card_refs[root] = {"title": title_lbl, "tip": title_tip, "bib": bib_lbl}
                    with ui.row().classes("items-center gap-x-2 gap-y-1 w-full q-mt-xs"):
                        if r["has_ko"]:
                            ui.badge("번역 완료", color="green").props("outline")
                        else:
                            ui.badge("영어 변환", color="blue").props("outline")
                        if unopened:
                            ui.badge("NEW", color="primary")
                        if r["hidden"]:
                            ui.badge("숨김", color="grey").props("outline")
                        ui.label(_relative_time(r["mtime"])).classes("md4-pmeta")
                        # 소속 프로젝트 = 이 논문의 저장 폴더까지 정하는 값이라 카드에서 바로 바꾼다.
                        tags_box = ui.element("div").classes("md4-ptags")
                        paint_tags(tags_box, r)
                        # 카드 열기로 번지지 않게 막고 서버에 '이 카드의 메뉴를 열어 달라'고만 보낸다
                        tags_box.on("click", lambda _, item=r, t=tags_box: open_member_menu(item, t, t),
                                    js_handler="(e) => { e.stopPropagation(); emit(); }")
                        tags_box.on("dblclick", js_handler=_STOP_JS)
                if r["pinned"]:
                    ui.icon("push_pin", size="16px").classes("md4-pinmark").tooltip("고정한 논문")
                acts = ui.row().classes("md4-pacts items-center gap-0 no-wrap")
                acts.on("click", js_handler=_STOP_JS).on("dblclick", js_handler=_STOP_JS)
                with acts:
                    # 고정 = 목록 맨 위 '고정한 논문' 구역. 관심 논문을 모아 두고 매번 찾지 않도록.
                    ui.button(icon="push_pin", on_click=lambda _, item=r: toggle_pin(item)) \
                        .props("flat dense round size=sm " + ("color=primary" if r["pinned"] else "color=grey-6")) \
                        .tooltip("고정 해제" if r["pinned"] else "목록 맨 위에 고정")
                    new_window_button(r["root"], state, r["title"], small=True)
                    # 번역된 문서는 원문(EN)·번역(KO)을 각각 버튼으로. 미번역은 영어 하나만.
                    ui.button("EN", on_click=lambda _, rt=r["root"]: download_zip(rt, "en")) \
                        .props("flat dense no-caps size=sm color=grey-7") \
                        .tooltip("영어 마크다운 zip (마크다운+이미지)")
                    if r["has_ko"]:
                        ui.button("KO", on_click=lambda _, rt=r["root"]: download_zip(rt, "ko")) \
                            .props("flat dense no-caps size=sm color=primary") \
                            .tooltip("한국어 번역 마크다운 zip (마크다운+이미지)")
                    if r["hidden"]:  # 숨긴 논문은 삭제 대신 '다시 표시'
                        ui.button(icon="visibility", on_click=lambda _, item=r: hide_paper(item, False)) \
                            .props("flat dense round size=sm color=primary").tooltip("목록에 다시 표시")
                    else:
                        ui.button(icon="delete", on_click=lambda _, item=r: confirm_delete(item)) \
                            .props("flat dense round size=sm color=grey-6").tooltip("목록에서 숨기기 / 파일 삭제")

        pinned = [r for r in items if r["pinned"]]
        rest = [r for r in items if not r["pinned"]]
        # '모든 논문'으로 볼 때만 고정 구역 아래를 묶음별 구역으로 갈라 준다. 한 프로젝트를 골라
        # 본다면 목록 전체가 이미 그 묶음이라 머리글이 같은 말을 반복할 뿐이다.
        # 여러 프로젝트에 속한 논문은 **주 프로젝트**(첫 소속) 구역에만 둔다 — 같은 카드가 여러 번
        # 나오면 목록이 길어지고 어느 것을 고쳤는지 헷갈린다.
        groups: list[tuple[str, list[dict]]] = []
        if view["scope"] == projects.ALL and data["projs"]:
            by_pid: dict[str, list[dict]] = {}
            for r in rest:
                by_pid.setdefault(r["pids"][0] if r["pids"] else "", []).append(r)
            for proj in data["projs"]:  # 만든 순서대로
                rows = by_pid.get(proj["id"])
                if rows:
                    groups.append((f"{proj['name']} {len(rows)}편", rows))
            if by_pid.get(""):  # 미분류는 맨 끝
                groups.append((f"{projects.NONE_LABEL} {len(by_pid[''])}편", by_pid[""]))
        if pinned:
            cap(f"고정한 논문 {len(pinned)}편", icon="push_pin")
            for r in pinned:
                card(r)
        if groups:
            for label, rows in groups:
                cap(label, icon="folder_special")
                for r in rows:
                    card(r)
        else:
            if pinned and rest:
                cap("나머지 논문")
            for r in rest:
                card(r)

    # ===== 오른쪽 — 미리보기 (Zotero식: 한 번 클릭 = 선택, 더블클릭·Enter·'열기' = 논문 화면) =====
    def select(root: str) -> None:
        """카드 한 번 클릭 — 고른 표시만 옮기고 미리보기를 그린다(목록은 다시 그리지 않는다)."""
        was_bulk, pick["bulk"] = pick["bulk"], False
        if pick["root"] == root and not was_bulk:
            return  # 더블클릭의 두 번째 click — 같은 카드면 할 일이 없다
        old = card_els.get(pick["root"])
        if old is not None:
            old.classes(remove="on")
        pick["root"] = root
        cur = card_els.get(root)
        if cur is not None:
            cur.classes(add="on")
        preview_pane.refresh()

    def picked_row() -> dict | None:
        """미리보기에 그릴 논문 — 지금 범위·숨김 보기에서 보일 수 있을 때만 (다른 묶음으로 가면 요약으로)."""
        r = data["by_root"].get(pick["root"] or "")
        if r is None or not in_scope(r, view["scope"]) or (r["hidden"] and not view["show_hidden"]):
            return None
        if not any(x is r for x in live_rows()):  # 아직 큐에서 처리 중
            return None
        return r

    def fetch(root: str, kind: str, lang: str = ""):  # noqa: ANN202 — asyncio.Task
        """미리보기 재료 읽기 — 한 번 시작한 읽기는 Task로 들고 있어 다시 그려도 두 번 읽지 않는다."""
        key = (root, kind, lang)
        task = loads.get(key)
        if task is None:
            wd = WorkDir(Path(root))
            if preview is None:
                fn = lambda: None if kind == "thumb" else ""  # noqa: E731
            elif kind == "thumb":
                fn = lambda: preview.thumbnail_png(wd)  # noqa: E731
            elif kind == "abs":
                fn = lambda: preview.abstract_of(wd, lang)  # noqa: E731
            else:
                fn = lambda: preview.notes_excerpt(wd)  # noqa: E731
            task = loads[key] = asyncio.ensure_future(run.io_bound(fn))
        return task

    def cached(root: str, kind: str, lang: str = ""):  # noqa: ANN202 — (있음?, 값)
        task = loads.get((root, kind, lang))
        if task is not None and task.done() and not task.cancelled() and task.exception() is None:
            return True, task.result()
        return False, None

    async def get(root: str, kind: str, lang: str = ""):  # noqa: ANN202
        try:
            return await fetch(root, kind, lang)
        except Exception:  # noqa: BLE001 — 미리보기 재료는 없어도 된다 (다음에 다시 시도)
            loads.pop((root, kind, lang), None)
            return None

    def draw_thumb(box, root: str, png) -> None:  # noqa: ANN001
        box.clear()
        if not png:
            box.set_visibility(False)
            return
        try:
            ver = Path(png).stat().st_mtime_ns
        except OSError:
            box.set_visibility(False)
            return
        with box:
            ui.element("img").props(f'src="/wdthumb/{wd_token(Path(root))}?v={ver}" alt="" draggable=false')

    # ----- 미리보기에서 서지·초록 고치기 -----
    fail = object()  # commit이 저장하지 못했을 때 — 칸은 옛 값으로 돌아간다

    # Enter = 저장(칸을 떠나게 해 blur 한 길로 모은다), Esc = 취소. 한글 조합 중 Enter(isComposing·229)는
    # 글자를 확정할 뿐이라 건드리지 않는다. 여러 줄 칸은 Shift+Enter가 줄바꿈.
    keys_js = ("(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); emit(); return; }"
               " if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229 && !(%s && e.shiftKey))"
               " { e.preventDefault(); e.target.blur(); } }")

    def edit_field(cls: str, key: str, value, placeholder: str, commit, *,  # noqa: ANN001
                   multiline: bool = False) -> None:
        """누르면 그 자리에서 입력칸이 되는 칸. commit(key, 새 값) → 저장된 값(또는 fail).

        값이 같으면 저장하지 않는다. Enter로 저장하면 칸이 사라지며 blur가 한 번 더 올 수 있어
        편집 한 번에 한 번만 끝내도록 done으로 막는다.
        """
        box = ui.element("div").classes(f"md4-pv-ed {cls}")
        st = {"value": value, "editing": False}

        def show() -> None:
            st["editing"] = False
            box.classes(remove="editing")
            box.clear()
            text, _ = field_text(key, st["value"])
            with box:
                ui.label(text or placeholder).classes("md4-pv-ed-v" + ("" if text else " ph"))
                ui.icon("edit", size="13px").classes("md4-pv-pen")

        def edit() -> None:
            if st["editing"]:
                return  # 입력칸 안을 누른 클릭이 칸까지 올라온 것
            st["editing"] = True
            done = {"v": False}
            _, cur = field_text(key, st["value"])
            box.classes(add="editing")
            box.clear()
            with box:
                w = ui.textarea(value=cur).props("autogrow") if multiline else ui.input(value=cur)
                w.props("dense borderless autofocus" + (" inputmode=numeric maxlength=4" if key == "year" else "")) \
                    .classes("w-full")

            async def finish(save: bool) -> None:
                if done["v"]:
                    return
                done["v"] = True
                if save:
                    try:
                        new, old = parse_field(key, w.value), parse_field(key, cur)
                    except ValueError as ex:
                        ui.notify(str(ex), type="warning")
                        new = old = None
                    if new != old:
                        saved = await commit(key, new)
                        if saved is not fail:
                            st["value"] = saved
                show()

            if not multiline:  # 한 줄 칸(연도·DOI…)은 통째로 바꾸는 일이 많아 전체 선택으로 연다
                w.on("focus", js_handler="(e) => e.target.select()")
            w.on("blur", lambda: finish(True))
            w.on("keydown", lambda: finish(False), js_handler=keys_js % ("true" if multiline else "false"))

        box.on("click", edit)
        show()

    def export_bib_bg(wd: WorkDir) -> None:
        """서지가 바뀌었으면 저장 위치의 references.bib 항목도 — 사본이 놓인 곳마다, 백그라운드로."""
        if not settings_dialog.any_library_dirs():
            return

        def go() -> None:
            for t in library.export_targets(wd):
                try:
                    library.export_bib(wd, t)
                except Exception:  # noqa: BLE001 — bib 한 곳이 실패해도 서지 저장은 이미 끝났다
                    pass

        background_tasks.create(run.io_bound(go), name="md4-export-bib")

    def update_card(r: dict) -> None:
        """목록 카드의 제목·서지 줄만 고친다 (목록을 다시 그리지 않는다)."""
        refs = card_refs.get(str(r["root"]))
        if refs is None:
            return
        try:
            refs["title"].set_text(r["title"])
            refs["tip"].set_text(r["title"])
            bib = _bib_line(r)
            refs["bib"].set_text(bib)
            refs["bib"].set_visibility(bool(bib))
        except Exception:  # noqa: BLE001 — 그 사이 목록이 다시 그려져 라벨이 없어졌다
            pass

    def apply_meta(r: dict, meta: dict) -> None:
        """저장된 서지 → 들고 있는 목록 행 (검색·정렬·카드가 새 값을 보게)."""
        r["title"] = meta.get("title") or r["title"]
        r["authors"] = list(meta.get("authors") or [])
        r["year"] = meta.get("year")
        r["venue"] = meta.get("venue") or ""

    def effective_doi(wd: WorkDir, meta: dict | None = None) -> str:
        """BibTeX에 실제로 나가는 DOI — 손으로 넣은 값 > 온라인 기록."""
        meta = paper_meta.load(wd) if meta is None else meta
        return (meta or {}).get("doi") or (bibsource.load(wd) or {}).get("doi") or ""

    def after_meta_change(r: dict, meta: dict) -> None:
        """서지가 바뀐 뒤 — 카드 · 헤더 탭 제목 · (보고 있으면) 이름 힌트·서지 ✓ · references.bib."""
        wd = WorkDir(r["root"])
        apply_meta(r, meta)
        update_card(r)
        tabs_bar.refresh()
        if pick["root"] == str(r["root"]):
            if pv.get("hint") is not None:
                draw_hint(pv["hint"], r)
            if pv.get("ok") is not None:
                draw_ok(pv["ok"], effective_doi(wd, meta))
        export_bib_bg(wd)

    async def commit_meta(r: dict, key: str, value):  # noqa: ANN202 — 저장된 값 | fail
        wd = WorkDir(r["root"])
        try:
            async with meta_lock:
                meta = await run.io_bound(paper_meta.update_fields, wd, {key: value})
        except ValueError as ex:  # 빈 제목·범위 밖 연도 등 — 칸은 옛 값으로
            ui.notify(f"저장하지 않았습니다 — {ex}", type="warning")
            return fail
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"저장하지 못했습니다: {ex}", type="negative")
            return fail
        after_meta_change(r, meta)
        ui.notify("저장됨", type="positive", timeout=1200)
        if key == "doi":
            return meta.get("doi") or effective_doi(wd, meta)
        return meta.get(key)

    async def commit_abs(r: dict, lang: str, text: str):  # noqa: ANN202 — 저장된 값 | fail
        wd = WorkDir(r["root"])
        try:
            await run.io_bound(preview.set_abstract, wd, lang, text)
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"초록을 저장하지 못했습니다: {ex}", type="negative")
            return fail
        done = asyncio.get_running_loop().create_future()  # 다시 그려도 디스크를 다시 읽지 않게 캐시도
        done.set_result(text)
        loads[(str(r["root"]), "abs", lang)] = done
        ui.notify("초록 저장됨", type="positive", timeout=1200)
        return text

    def draw_abs(box, text) -> None:  # noqa: ANN001
        box.clear()
        r, lang = picked_row(), pick.get("abs_lang") or "en"
        with box:
            if preview is None or r is None:
                ui.label(text or "초록을 찾지 못했습니다").classes("md4-pv-abs" + ("" if text else " none"))
                return
            edit_field("md4-pv-abs w-full", "abstract", text or "", "초록을 찾지 못했습니다 — 눌러서 입력",
                       lambda _k, v, r=r, lang=lang: commit_abs(r, lang, v), multiline=True)

    def draw_ok(box, doi: str) -> None:  # noqa: ANN001
        box.clear()
        if doi:
            with box:
                ui.label("서지 ✓").classes("md4-pv-ok").tooltip(f"서지 정보 보강됨 — DOI {doi}")

    def draw_hint(box, r: dict) -> None:  # noqa: ANN001
        """이름 규칙대로면 폴더 이름이 달라질 때만 한 줄 — 서지를 고친 뒤 폴더도 맞출 수 있게."""
        box.clear()
        wd = WorkDir(r["root"])
        base = paper_meta.naming_base_for(wd)
        if not base or _same_base(wd.root.stem, base):
            box.set_visibility(False)
            return
        box.set_visibility(True)
        with box:
            ui.label("이름 규칙대로면")
            ui.label(base).classes("md4-pv-hint-n")
            ui.button("폴더 이름 바꾸기", on_click=lambda: confirm_rename(r, base)) \
                .props("flat dense no-caps color=primary")

    def confirm_rename(r: dict, base: str) -> None:
        old = Path(r["root"])
        running = {str(Path(j.root).resolve()) for j in jobs.running()}
        if str(old.resolve()) in running:  # 번역 등이 이 폴더에 쓰는 중 — 옮기면 그 작업이 길을 잃는다
            ui.notify("이 논문에서 작업(번역 등)이 도는 중입니다 — 끝난 뒤에 바꾸세요.", type="warning")
            return

        async def do_rename() -> None:
            dlg.close()
            try:
                new_wd = await run.io_bound(paper_meta.rename_to_naming, WorkDir(old), ws())
            except Exception as ex:  # noqa: BLE001
                ui.notify(f"폴더 이름을 바꾸지 못했습니다: {ex}", type="negative")
                return
            new = Path(new_wd.root)
            if new == old:
                ui.notify("이미 이름 규칙대로입니다.", type="info")
                preview_pane.refresh()
                return
            tabstore.rename_tab(old, new)  # rename_workdir도 부르지만, 두 번이어도 무해(옛 키가 없으면 그냥 끝)
            rekey(r, old, new)
            tabs_bar.refresh()
            refresh_views()
            ui.notify(f"폴더 이름을 바꿨습니다 → {new.stem}", type="positive")

        with host, ui.dialog() as dlg, ui.card().classes("gap-2").style("max-width:440px"):
            ui.label("폴더 이름을 이름 규칙대로 바꿀까요?").classes("font-bold")
            with ui.column().classes("gap-0 w-full"):
                ui.label(old.stem).classes("text-sm text-gray-500 break-all")
                ui.label("→ " + base).classes("text-sm break-all")
            ui.label("폴더·PDF·저장 위치 사본·열린 탭이 함께 옮겨집니다.").classes("text-xs text-gray-500")
            with ui.row().classes("justify-end w-full items-center gap-2"):
                ui.button("취소", on_click=dlg.close).props("flat")
                ui.button("이름 바꾸기", icon="drive_file_rename_outline", on_click=do_rename) \
                    .props("unelevated no-caps color=primary")
        dlg.open()

    def rekey(r: dict, old: Path, new: Path) -> None:
        """폴더가 옮겨졌다 — 이 홈이 root로 들고 있던 것(행·선택·체크·캐시)을 새 경로로."""
        o, n = str(old), str(new)
        r["root"], r["name"] = new, new.stem
        data["by_root"].pop(o, None)
        data["by_root"][n] = r
        if o in selected:
            selected.discard(o)
            selected.add(n)
        if pick["root"] == o:
            pick["root"] = n
        for k in [k for k in loads if k[0] == o]:
            loads.pop(k, None)
        recent = state.get("recent_new", {})
        if o in recent:
            recent[n] = recent.pop(o)

    def set_busy(btn, on: bool) -> None:  # noqa: ANN001 — ui.button
        try:
            btn.props("loading") if on else btn.props(remove="loading")
        except Exception:  # noqa: BLE001 — 그 사이 미리보기가 다시 그려져 단추가 없어졌다
            pass

    async def redo_online(r: dict, btn) -> None:  # noqa: ANN001
        """'온라인에서 다시 찾기' — 출판 기록을 새로 받는다(손으로 고친 칸은 기록 위에 다시 덮인다)."""
        root, wd = str(r["root"]), WorkDir(r["root"])
        if root in busy_meta:
            ui.notify("이 논문의 서지를 이미 다시 받는 중입니다.", type="info", timeout=1500)
            return
        busy_meta.add(root)
        set_busy(btn, True)
        try:
            rec = await run.io_bound(bibsource.refetch, wd, mailto=config.resolve_enrich_mailto() or None)
        except Exception as ex:  # noqa: BLE001 — 오프라인·레이트리밋
            ui.notify(f"온라인 조회에 실패했습니다: {ex}", type="negative")
            return
        finally:
            busy_meta.discard(root)
            set_busy(btn, False)
        if not rec:
            ui.notify("출판 기록을 찾지 못했습니다 — 제목·저자·연도를 고친 뒤 다시 찾아 보세요.", type="info")
            return
        bits = [str(x) for x in (rec.get("venue"), rec.get("year")) if x]
        if rec.get("doi"):
            bits.append(f"DOI {rec['doi']}")
        ui.notify("출판 기록을 받았습니다" + (" — " + " · ".join(bits) if bits else ""), type="positive")
        after_meta_change(r, paper_meta.load(wd) or {})
        if pick["root"] == root:
            preview_pane.refresh()  # DOI 칸·서지 ✓

    async def redo_ai(r: dict, btn) -> None:  # noqa: ANN001
        """'AI로 다시 추출' — 앞부분에서 서지를 다시 뽑는다(손으로 고친 칸은 그대로)."""
        root, wd = str(r["root"]), WorkDir(r["root"])
        if root in busy_meta:
            ui.notify("이 논문의 서지를 이미 다시 받는 중입니다.", type="info", timeout=1500)
            return
        try:
            provider = config.build_provider()
        except RuntimeError as ex:
            ui.notify(str(ex), type="warning")
            return
        busy_meta.add(root)
        set_busy(btn, True)
        try:
            async with meta_lock:
                meta = await run.io_bound(paper_meta.reextract, wd, provider)
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"서지를 다시 뽑지 못했습니다: {ex}", type="negative")
            return
        finally:
            busy_meta.discard(root)
            set_busy(btn, False)
        if not meta:
            ui.notify("본문 앞부분이 비어 있어 서지를 뽑지 못했습니다.", type="warning")
            return
        after_meta_change(r, meta)
        ui.notify("AI로 서지를 다시 뽑았습니다 — 손으로 고친 칸은 그대로 두었습니다.", type="positive")
        if pick["root"] == root:
            preview_pane.refresh()

    def ai_ready() -> bool:
        """AI 키가 있는지 — config.build_provider()가 RuntimeError를 내는 바로 그 조건 (클라이언트는 만들지 않는다)."""
        try:
            return bool(config.resolve_key(config.resolve_provider()))
        except Exception:  # noqa: BLE001 — 알 수 없는 제공자 이름 등
            return False

    def draw_notes(box, text) -> None:  # noqa: ANN001
        box.clear()
        if not text:
            return
        with box:
            ui.label("메모").classes("md4-panel-title")
            ui.label(text).classes("md4-pv-notes w-full")

    async def fill(seq: int, box, root: str, kind: str, lang: str, draw) -> None:  # noqa: ANN001
        """비동기로 읽어 그 칸만 채운다 — 그 사이 다른 논문을 골랐으면(seq가 바뀜) 버린다."""
        val = await get(root, kind, lang)
        if pick["seq"] != seq or (kind == "abs" and pick.get("abs_lang") != lang):
            return
        try:
            draw(box, root, val) if kind == "thumb" else draw(box, val)
        except Exception:  # noqa: BLE001 — 칸이 이미 지워졌거나(다시 그림) 창이 닫혔다
            pass

    def load_into(box, root: str, kind: str, lang: str, draw, placeholder=None) -> None:  # noqa: ANN001
        """캐시에 있으면 바로 그리고, 없으면 자리표시를 두고 읽기를 건다."""
        ok, val = cached(root, kind, lang)
        if ok:
            draw(box, root, val) if kind == "thumb" else draw(box, val)
            return
        if placeholder is not None:
            with box:
                placeholder()
        background_tasks.create(fill(pick["seq"], box, root, kind, lang, draw), name="md4-preview")

    def abs_skeleton() -> None:
        ui.label("불러오는 중…").classes("md4-pv-bib")
        for w in ("100%", "96%", "88%"):
            ui.skeleton("text").style(f"width:{w}")

    def set_lang(lang: str) -> None:
        pick["lang"] = lang
        r = picked_row()
        if r is None:
            return
        pick["abs_lang"] = lang
        for k, span in pv.get("seg", {}).items():
            span.classes(add="on") if k == lang else span.classes(remove="on")
        box = pv.get("abs_box")
        if box is not None:
            box.clear()
            load_into(box, str(r["root"]), "abs", lang, draw_abs, abs_skeleton)

    def show_bulk() -> None:
        pick["bulk"] = True
        preview_pane.refresh()

    def draw_single(r: dict) -> None:
        root = str(r["root"])
        wd = WorkDir(r["root"])
        state.setdefault("wd_by_token", {})[wd_token(r["root"])] = wd  # /wdthumb가 이 논문을 찾게
        unopened = not r.get("opened")
        lang = pick["lang"] if (pick["lang"] and (r["has_ko"] or pick["lang"] == "en")) else \
            ("ko" if r["has_ko"] else "en")
        pick["abs_lang"] = lang
        with ui.column().classes("md4-pv-body md4-scroll gap-3 w-full"):
            if selected:
                ui.label(f"☑ {len(selected)}편 체크됨 — 일괄 동작 보기 ›").classes("md4-pv-selnote") \
                    .on("click", show_bulk)
            thumb = ui.element("div").classes("md4-pv-thumb")
            load_into(thumb, root, "thumb", "", draw_thumb, lambda: ui.skeleton("rect"))
            # 서지 — 칸마다 눌러서 바로 고친다(paper_meta.update_fields). 제목·저자는 여러 줄 칸(저자는 한 줄에
            # 한 명), 연도·venue·DOI·약칭은 이름표 | 값 두 칸. 약칭은 폴더 이름 규칙의 {title}.
            pm = paper_meta.load(wd) or {}
            doi = effective_doi(wd, pm)

            def commit(key: str, value):  # noqa: ANN001, ANN202
                return commit_meta(r, key, value)

            with ui.column().classes("gap-1 w-full"):
                edit_field("md4-pv-title w-full", "title", r["title"], "제목 없음 — 눌러서 입력", commit,
                           multiline=True)
                edit_field("md4-pv-auth w-full", "authors", r.get("authors") or [],
                           "저자 없음 — 눌러서 입력 (한 줄에 한 명)", commit, multiline=True)
            with ui.element("div").classes("md4-pv-kv w-full"):
                for key, label, val, tip in (
                        ("year", "연도", r.get("year"), ""),
                        ("venue", "venue", r.get("venue"), "학회·저널 이름"),
                        ("doi", "DOI", doi, "BibTeX·온라인 조회에 쓰는 DOI — https://doi.org/ 접두는 떼어 저장합니다"),
                        ("short_title", "약칭", pm.get("short_title"),
                         "파일·폴더 이름에 쓰는 제목 약칭(이름 규칙의 {title}) — 비우면 제목에서 만듭니다")):
                    k = ui.label(label).classes("md4-pv-k")
                    if tip:
                        k.tooltip(tip)
                    edit_field("md4-pv-v", key, val, "비어 있음 — 눌러서 입력", commit)
            pv["hint"] = ui.element("div").classes("md4-pv-hint w-full")
            draw_hint(pv["hint"], r)
            tags_box = ui.element("div").classes("md4-ptags")
            paint_tags(tags_box, r)
            tags_box.on("click", lambda _, item=r, t=tags_box: open_member_menu(item, t, t))
            with ui.row().classes("items-center gap-x-2 gap-y-1 w-full"):
                if r["has_ko"]:
                    ui.badge("번역 완료", color="green").props("outline")
                else:
                    ui.badge("영어 변환", color="blue").props("outline")
                if unopened:
                    ui.badge("NEW", color="primary")
                if r["hidden"]:
                    ui.badge("숨김", color="grey").props("outline")
                ui.label(_relative_time(r["mtime"])).classes("md4-pmeta")
                pv["ok"] = ui.element("div")
                draw_ok(pv["ok"], doi)
            with ui.row().classes("items-center w-full no-wrap q-mt-xs"):
                ui.label("초록").classes("md4-panel-title")
                ui.space()
                pv["seg"] = {}
                if r["has_ko"]:
                    with ui.element("div").classes("md4-seg"):
                        for k in ("en", "ko"):
                            sp = ui.element("span").classes("on" if k == lang else "")
                            with sp:
                                ui.label(k.upper())
                            sp.on("click", lambda _, k=k: set_lang(k))
                            pv["seg"][k] = sp
            abs_box = ui.column().classes("gap-1 w-full")
            pv["abs_box"] = abs_box
            load_into(abs_box, root, "abs", lang, draw_abs, abs_skeleton)
            notes_box = ui.column().classes("gap-1 w-full")
            load_into(notes_box, root, "notes", "", draw_notes)
        foot = ui.column().classes("md4-pv-foot gap-1 w-full")
        with foot, ui.row().classes("md4-pv-redo items-center gap-1 w-full no-wrap"):
            # 서지 다시 받기 — 손으로 고친 칸은 어느 쪽도 덮지 않는다(user_fields)
            online = ui.button("온라인에서 다시 찾기", icon="travel_explore",
                               on_click=lambda: redo_online(r, online)) \
                .props("flat dense no-caps size=sm color=grey-8") \
                .tooltip("OpenAlex·Crossref 등에서 출판 기록(BibTeX용)을 새로 받습니다 — 손으로 고친 칸은 그대로")
            ai_ok = ai_ready()
            # 툴팁은 감싼 span에 — 비활성 <button>은 마우스 이벤트를 받지 않아 '왜 꺼졌는지'가 안 뜬다
            with ui.element("span").tooltip(
                    "본문 앞부분에서 제목·저자·연도·venue를 AI로 다시 뽑습니다 — 손으로 고친 칸은 그대로"
                    if ai_ok else "AI 키가 없어 쓸 수 없습니다 — ⚙ 설정 → AI에서 키를 넣으세요"):
                ai = ui.button("AI로 다시 추출", icon="auto_awesome", on_click=lambda: redo_ai(r, ai)) \
                    .props("flat dense no-caps size=sm color=grey-8")
            if not ai_ok:
                ai.disable()
        with foot, ui.row().classes("items-center gap-1 w-full no-wrap"):
            ui.button("열기", icon="menu_book", on_click=lambda: open_wd(r["root"])) \
                .props("unelevated dense no-caps color=primary").classes("q-px-sm") \
                .tooltip("논문 화면으로 — 카드를 두 번 눌러도 됩니다")
            new_window_button(r["root"], state, r["title"], small=True)
            if _has_source_pdf(wd):
                ui.button(icon="picture_as_pdf", on_click=lambda: open_source_pdf(UIController(wd))) \
                    .props("flat dense round size=sm color=grey-7") \
                    .tooltip("PDF 뷰어로 열기 — 원본 PDF를 미리보기 등 기본 앱에서")
            ui.button("EN", on_click=lambda: download_zip(r["root"], "en")) \
                .props("flat dense no-caps size=sm color=grey-7").tooltip("영어 마크다운 zip (마크다운+이미지)")
            if r["has_ko"]:
                ui.button("KO", on_click=lambda: download_zip(r["root"], "ko")) \
                    .props("flat dense no-caps size=sm color=primary") \
                    .tooltip("한국어 번역 마크다운 zip (마크다운+이미지)")
            ui.space()
            ui.button(icon="push_pin", on_click=lambda: toggle_pin(r)) \
                .props("flat dense round size=sm " + ("color=primary" if r["pinned"] else "color=grey-6")) \
                .tooltip("고정 해제" if r["pinned"] else "목록 맨 위에 고정")
            if r["hidden"]:
                ui.button(icon="visibility", on_click=lambda: hide_paper(r, False)) \
                    .props("flat dense round size=sm color=primary").tooltip("목록에 다시 표시")
            else:
                ui.button(icon="delete", on_click=lambda: confirm_delete(r)) \
                    .props("flat dense round size=sm color=grey-6").tooltip("목록에서 숨기기 / 파일 삭제")

    def draw_bulk() -> None:
        """체크한 논문들 — 예전 목록 위 선택 툴바가 하던 일괄 동작을 이 칸에 세로로."""
        n = len(selected)
        with ui.column().classes("md4-pv-body md4-scroll gap-2 w-full"):
            with ui.row().classes("items-baseline w-full no-wrap"):
                ui.label(f"{n}편 선택").classes("md4-pv-title")
                ui.space()
                ui.button("선택 해제", on_click=clear_selection).props("flat dense no-caps size=sm")
            rows = [data["by_root"][x] for x in selected if x in data["by_root"]]
            rows.sort(key=lambda x: x["title"].lower())
            with ui.column().classes("gap-0 w-full"):
                for x in rows[:8]:
                    ui.label("· " + x["title"]).classes("md4-pv-sel truncate w-full").tooltip(x["title"])
                if len(rows) > 8:
                    ui.label(f"외 {len(rows) - 8}편").classes("md4-pv-sel")
            ui.label("일괄 동작").classes("md4-panel-title q-mt-sm")
            with ui.column().classes("md4-pv-bulk gap-1 w-full"):
                ui.button("영어 zip", icon="download", on_click=lambda: download_bulk("en")) \
                    .props("outline dense no-caps")
                ui.button("한국어 zip", icon="download", on_click=lambda: download_bulk("ko")) \
                    .props("outline dense no-caps")
                ui.button("모바일 HTML", icon="smartphone", on_click=download_mobile).props("outline dense no-caps") \
                    .tooltip("폰에서 읽는 HTML 한 장 (원문·번역·문단 대조) — iCloud Drive·구글 드라이브에 저장하면 "
                             "밖에서도 열립니다")
                # 추가 = 다른 소속은 그대로 두고 더하기, 빼기 = 그 소속만 떼기(그 폴더의 사본도 정리)
                with ui.button("컬렉션에 추가 ▾", icon="playlist_add").props("outline dense no-caps"):
                    project_pick_menu(lambda v: bulk_member(v, True), with_new=True)
                member_of = {p for root in selected for p in (data["by_root"].get(root) or {}).get("pids", [])}
                with ui.button("컬렉션에서 빼기 ▾", icon="playlist_remove").props("outline dense no-caps"):
                    project_pick_menu(lambda v: bulk_member(v, False), only=member_of)
                if settings_dialog.any_library_dirs():
                    ui.button("폴더로 저장", icon="drive_file_move",
                              on_click=lambda: save_to_library([Path(p) for p in selected])) \
                        .props("outline dense no-caps").tooltip("저장 위치로 내보내기")

    def draw_summary() -> None:
        """아무것도 고르지 않았을 때 — 이 범위 요약 + 미리보기 안내."""
        s = view["scope"]
        rows = [r for r in live_rows() if not r["hidden"] and in_scope(r, s)]
        n_ko = sum(1 for r in rows if r["has_ko"])
        with ui.column().classes("md4-pv-body md4-scroll gap-2 w-full"):
            ui.label(scope_label()).classes("md4-pv-title")
            ui.label(f"{len(rows)}편 · 번역 {n_ko}편").classes("md4-pv-stat")
            if s not in (projects.ALL, projects.NONE):
                root = projects.root_of(s)
                with ui.row().classes("items-start gap-1 no-wrap w-full"):
                    ui.icon("folder" if root else "folder_off", size="15px").classes("text-grey-5 q-mt-xs")
                    ui.label(str(root) if root else "폴더 미지정 — 공통 저장 위치에 쌓입니다") \
                        .classes("md4-pv-bib break-all")
            with ui.column().classes("md4-pv-empty gap-1 w-full"):
                ui.icon("article", size="40px").classes("text-grey-4")
                ui.label("논문을 고르면 여기에 미리보기").classes("text-sm")
                ui.label("한 번 클릭 = 미리보기 · 두 번 클릭(또는 Enter) = 열기 · 체크박스 = 여러 편 일괄 동작") \
                    .classes("text-xs")

    @ui.refreshable
    def preview_pane() -> None:
        pick["seq"] += 1  # 이전 그림에 걸어 둔 읽기는 이제 버려진다
        pv["seg"], pv["abs_box"], pv["hint"], pv["ok"] = {}, None, None, None
        if not (pv["open"] or selected):
            return  # 접혀 있으면 그리지도 읽지도 않는다
        r = picked_row()
        if selected and (pick["bulk"] or r is None):
            draw_bulk()
        elif r is not None:
            draw_single(r)
        else:
            draw_summary()

    def sync_preview() -> None:
        """미리보기 열 보이기 — 접어 뒀어도 체크한 논문이 있으면 편다(일괄 동작이 거기에만 있다)."""
        col, btn = pv["col"], pv["btn"]
        if col is None:
            return
        shown = pv["open"] or bool(selected)
        if col.visible != shown:
            col.set_visibility(shown)
        if btn is not None:  # 펴 있으면 파랗게, 접혀 있으면 회색
            btn.classes(replace="text-primary" if pv["open"] else "text-grey-7")

    def toggle_preview() -> None:
        pv["open"] = not pv["open"]
        config.set_section_value("ui", "home_preview", pv["open"])  # 다음 실행에도 같은 모양
        sync_preview()
        preview_pane.refresh()

    def poll() -> None:  # 큐 진행 갱신 + 전체 처리 완료(또는 실패) 시에만 목록에 등장 + 하이라이트
        q = state["queue"]
        now = time.monotonic()
        finished = False
        for it in q:
            # 추출→참고문헌→용어집→서지까지 다 끝난(done) 뒤에야 목록에 노출한다.
            if it["status"] in ("done", "failed") and not it.get("_counted"):
                it["_counted"] = True
                if it["status"] == "done" and it.get("wd_root"):
                    state["recent_new"][it["wd_root"]] = now  # 완료 하이라이트
                finished = True
        if finished:
            refresh_views(rescan=True)  # 새 논문이 생겼다 — 여기서만 작업 폴더를 다시 훑는다
        q[:] = [it for it in q if not (it["status"] == "done" and now - (it["done_at"] or now) > 6)]
        # 보여 줄 게 없고 지난번에도 없었으면 다시 그리지 않는다 (0.6초마다 빈 상자를 짓지 않게)
        busy = bool(q) or bool(jobs.running())
        if busy or poll_state["shown"]:
            queue_panel.refresh()
        poll_state["shown"] = busy

    poll_state = {"shown": True}

    # ===== 틀 =====
    scan()
    with ui.row().classes("w-full no-wrap gap-0 md4-body md4-home"):
        # ---- 왼쪽: 프로젝트 사이드바 ----
        with ui.column().classes("md4-hside gap-0"):
            with ui.column().classes("md4-hside-rows md4-scroll gap-0 w-full"):
                sidebar_rows()
            with ui.column().classes("md4-hfoot gap-0 w-full"):
                ui.button("설정", icon="settings", color=None, on_click=lambda: open_settings()) \
                    .props("flat dense no-caps").classes("w-full") \
                    .tooltip("AI 키 · 저장 위치 · 변환·번역 기본값 · 고급")
                update_banner.version_line(state)  # v0.1.0 · 커밋 · 최신 여부
                ws_label()

        # ---- 오른쪽: 범위 제목 · 도구 줄 · 큐 · 목록 ----
        with ui.column().classes("md4-hmain gap-0"):
            with ui.column().classes("md4-hhead gap-1 w-full"):
                head()
            with ui.row().classes("md4-htools items-center gap-2 w-full no-wrap"):
                def on_search(e) -> None:  # noqa: ANN001
                    view["q"] = e.value or ""
                    recent_list.refresh()

                def on_sort(e) -> None:  # noqa: ANN001
                    view["sort"] = e.value
                    recent_list.refresh()

                ui.input(placeholder="제목·저자·venue 검색…").on_value_change(on_search) \
                    .props("dense outlined clearable").classes("flex-grow")
                ui.select(_SORTS, value="recent", on_change=on_sort).props("dense outlined").classes("w-32")
                with ui.element("div").classes("md4-upwrap"):
                    ui.button("올리기", icon="upload_file").props("unelevated dense no-caps color=primary") \
                        .classes("md4-upbtn q-px-sm")
                    # 클릭은 위 CSS가 펼쳐 놓은 진짜 <input type=file>이 그대로 받는다 (JS 개입 없음).
                    # 파일을 창 위로 끄는 동안에는 같은 업로더가 목록 열 전체로 펼쳐져 놓기를 받는다.
                    ui.upload(on_upload=handle, multiple=True, auto_upload=True) \
                        .props('accept=".pdf,.md,.markdown"').classes("md4-upload") \
                        .tooltip("PDF·마크다운 고르기 — 창 위로 끌어다 놓아도 됩니다")
                with ui.button(icon="more_horiz", color=None).props("flat dense round").classes("text-grey-7") \
                        .tooltip("숨긴 논문 · 내보내기 형식 · 이 목록 저장"):
                    more_menu()
                pv["btn"] = ui.button(icon="view_sidebar", color=None, on_click=toggle_preview) \
                    .props("flat dense round").classes("text-primary") \
                    .tooltip("오른쪽 미리보기 접기/펴기")
            with ui.column().classes("md4-hlist md4-scroll w-full"):
                queue_panel()  # 변환 중인 논문 — 끝나면 바로 아래 목록에 나타나므로 같은 칸에 둔다
                recent_list()
            with ui.element("div").classes("md4-dropov"):
                drop_caption()
            # 카드 태그의 체크 메뉴 하나 — 목록을 다시 그려도 남도록 목록 밖에 둔다 (부모 클릭으로는 안 열림)
            member_ui["menu"] = ui.menu().props("no-parent-event auto-close=false")
            member_ui["menu"].on("hide", member_menu_closed)

        # ---- 오른쪽 끝: 미리보기 (고른 논문 · 체크한 논문들의 일괄 동작 · 범위 요약) ----
        pv["col"] = ui.column().classes("md4-hprev gap-0")
        with pv["col"]:
            preview_pane()
        sync_preview()

    ui.timer(0.6, poll)

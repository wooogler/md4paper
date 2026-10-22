"""에이전트 창 — 홈·뷰어 어디서나 열리는 오른쪽 서랍 (CSS/HTML/JS 문자열 + 셸 설치).

옛 뷰어 챗봇 서랍(`chat_panel`)을 이었다. 바뀐 점:

- **셸에 한 번 설치**한다(`install`, app.py `shell()`). body 직속 `#md4-agent`라 화면을 갈아 끼워도
  (§ui/spa.py) 창·대화·진행 중인 스트림이 그대로 남는다. 화면은 `window.__md4Agent.setPaper(tok|null)`
  로 지금 논문만 알린다(`init_js`).
- 대화는 논문이 아니라 **전역 세션**이다(`/agent/sessions`, §ui/agent/routes.py). 활성 세션 id는
  브라우저(localStorage)에 두고 서버는 클라이언트별 상태를 갖지 않는다.
- 답은 SSE로 흘러온다 — 글 조각(text), tool 호출 한 줄, 화면 동작(ui_action), 승인 대기, 끝(done).
  끝에 서버가 렌더한 HTML(raw HTML 비활성 마크다운 + 인용 칩)로 말풍선을 바꿔 끼운다.
- 매 질문에 **화면 맥락**(`window.__md4Context()` — 보이는 행·선택한 글·메모 탭 등)을 싣는다.
- 인용 칩은 여러 논문을 가리킬 수 있다. 지금 논문이면 그 행으로, 다른 논문이면 그 논문을 연 뒤
  행으로 간다(가는 길은 sessionStorage에 남기고 도착한 뷰어의 setPaper가 소비한다).

메모 서랍(`#md-anno-panel`)과 같은 자리·같은 시각 언어를 쓰고 둘은 번갈아 열린다. '읽던 자리로'
알약(칩 점프로 잃은 자리 되돌리기)은 옛 서랍에서 그대로 옮겨 왔다.
"""

from __future__ import annotations

import json

PANEL_ID = "md4-agent"           # body 직속 서랍 id — spa.add_body_html이 같은 id를 다시 넣지 않는다
PANEL_Z = 9000                   # 메모 서랍(#md-anno-panel)과 같은 층 — 둘은 번갈아 열린다
NAV_EVENT = "md4-agent-nav"      # JS → 서버: SPA 화면 이동 요청 (JS는 ui.sub_pages 이동을 직접 못 한다)
NOTES_REFRESH_EVENT = "md4-notes-refresh"   # JS → 서버: 에이전트가 메모 파일을 고쳤다 — 뷰어 메모 패널
                                            # (app.py notes_panel)이 파일을 다시 읽어 편집기를 맞춘다
ANNO_RELOAD_API = "__mdAnnoReload"          # 뷰어 하이라이트 스크립트(app.py)가 내놓는 다시 칠하기 함수

# '읽던 자리로' 되돌리기 알약 — 아래 CSS/HTML/JS 문자열과 이 상수가 한 짝이다(테스트가 대조한다).
JUMP_BACK_ID = "md-jump-back"    # body 직속 오버레이 id — 서랍 밖이라 서랍을 닫아도 남는다
JUMP_BACK_Z = 8500               # 본문 위, 서랍(9000)·메모 카드(10003)·찾기 바 아래
JUMP_BACK_API = "__mdJumpBack"   # 다른 점프(목차·섹션 트리)가 나중에 재사용할 전역 이름
JUMP_EDGE_PX = 6                 # 기준선 = 컨테이너 위끝 + 6px. app.py PDF 싱크(top+6)와 같은 정의라
                                 # "화면 맨 위가 읽던 자리"가 뷰어 전체에서 한 뜻이 된다

# 좁은 창 대응 — JS syncLayout()의 `var AG_… = …` 값과 한 짝이다(테스트가 대조한다).
# 서랍 폭은 사용자가 끈 값(localStorage)을 그대로 쓰지 않고 열 때·창 크기가 바뀔 때마다 뷰포트에 맞춘다.
AG_MIN_PX = 320                  # 서랍 최소 폭 — 머리줄·입력창이 버티는 하한
AG_MAX_PX = 760                  # 서랍 최대 폭 (드래그 상한)
AG_KEEP_PX = 360                 # 서랍이 본문 몫으로 늘 남겨 두는 폭 — 창이 본문을 통째로 덮지 않게
AG_OVERLAY_BELOW = 760           # 남는 본문 폭이 이보다 좁으면 밀지 않고 덮는다(body.ag-overlay).
                                 # 뷰어(레일+패널+캔버스)·홈(사이드바+목록+미리보기)은 이보다 좁게
                                 # 밀리면 열이 짓눌려 카드·본문이 세로로 깨진다 → 본문은 제 폭에 두고
                                 # 서랍만 그 위에 띄운다
AG_NARROW_BELOW = 1100           # 밀기 모드에서 남는 폭이 이보다 좁으면 홈 미리보기 열을 접는다(ag-narrow).
                                 # home.py의 @media(max-width:1100px)와 같은 경계 — 그 미디어 쿼리는
                                 # 뷰포트만 봐서 서랍이 민 폭을 모른다
AG_SLIM_BELOW = 380              # 서랍 자체가 이보다 좁으면 머리줄에서 비용을 숨기고 모델 칸을 줄인다

CSS = """
/* 오른쪽 에이전트 컬럼 — 메모 서랍과 같은 자리·같은 시각 언어 (동시에 열리지 않는다).
   본문을 덮지 않고 **오른쪽을 차지한다**: 열리면 본문 틀(.md4-body — 홈·뷰어 공통)이 그만큼
   좁아진다(body.ag-open). 위치는 fixed — 서랍이 body에 붙어 있어야 화면을 갈아 끼워도 대화와
   진행 중인 답이 살아남는다. 자리는 padding으로 비운다. 좁은 창에서는 예외로 덮는다(아래 ag-overlay).
   --ag-w는 JS(syncLayout)가 뷰포트에 맞춰 다시 쓴다 — 여기 값은 스크립트가 돌기 전 기본값이다. */
:root { --ag-w: 400px; --ag-top: 50px; }
/* --ag-top은 헤더를 실제로 재서 넣는다(아래 syncTop) — 헤더 높이가 고정이 아니라 상수로 두면
   서랍이 헤더 위로 올라와 탭 라벨을 가린다. */
#md4-agent { position: fixed; top: var(--ag-top); right: 0; bottom: 0; width: var(--ag-w); z-index: 9000;
  display: none; flex-direction: column; background: #fff; border-left: 1px solid #e6e4e0;
  color: #37352f; }
#md4-agent.open { display: flex; }
body.ag-open .md4-body { padding-right: var(--ag-w); }
/* 좁은 창 — 남는 본문 폭이 모자라면(JS syncLayout이 붙인다) 밀지 않고 덮는다. 본문은 제 폭 그대로
   두고 서랍만 위에 띄우므로 뷰어·홈의 열이 짓눌리지 않는다. 떠 있다는 걸 그림자로 분명히 한다. */
body.ag-open.ag-overlay .md4-body { padding-right: 0; }
body.ag-open.ag-overlay #md4-agent { box-shadow: -12px 0 36px rgba(0,0,0,.22); }
/* 밀기 모드인데 홈 세 열이 다 안 들어갈 때 — 미리보기 열을 접는다(더 좁으면 이미 덮기로 넘어간다).
   home.py의 @media(max-width:1100px)는 뷰포트만 보므로 서랍이 민 폭은 여기서 반영한다. */
body.ag-open.ag-narrow .md4-hprev { display: none; }
/* 서랍 자체가 좁을 때 — 머리줄(세션·모델·비용·새 세션·닫기)이 한 줄에 버티게 덜 중요한 것부터 뺀다 */
#md4-agent.ag-slim .ag-cost { display: none; }
#md4-agent.ag-slim .ag-model { max-width: 84px; }
#md4-agent.ag-slim .ag-new { padding: 3px 5px; }
/* 너비 드래그 핸들 — 목차·컬럼 핸들과 같은 감각 */
.ag-handle { position: absolute; left: 0; top: 0; bottom: 0; width: 9px; margin-left: -4px;
  cursor: col-resize; z-index: 1; }
.ag-handle::after { content: ''; position: absolute; left: 4px; top: 0; bottom: 0; width: 1px;
  background: transparent; }
.ag-handle:hover::after { background: #2383e2; width: 2px; }
.ag-head { position: relative; display: flex; align-items: center; gap: 6px; padding: 8px 10px;
  flex-wrap: nowrap; border-bottom: 1px solid #ecebe8; font-size: 13px; }
.ag-head .ag-btn, .ag-cost { white-space: nowrap; flex: 0 0 auto; }
/* 세션 고르기 — 제목이 길면 말줄임, 남는 폭은 이것이 먼저 쓴다 */
.ag-sess { display: inline-flex; align-items: center; gap: 3px; min-width: 0; flex: 1 1 auto;
  border: none; background: none; font: inherit; font-size: 12.5px; font-weight: 600;
  color: inherit; cursor: pointer; padding: 3px 6px; border-radius: 7px; text-align: left; }
.ag-sess:hover { background: #f0efec; }
.ag-sess:disabled { cursor: default; opacity: .7; }
.ag-sess-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ag-sess-c { flex: 0 0 auto; color: #97948d; font-size: 10px; }
.ag-model { font-size: 10.5px; padding: 1px 4px; border-radius: 9px; background: #f0efec;
  color: #6b6862; flex: 0 1 auto; min-width: 44px; max-width: 112px; border: none;
  cursor: pointer; font-family: inherit; }
.ag-model:hover { background: #e6e4e0; }
.ag-model:disabled { cursor: default; opacity: .7; }
.ag-cost { font-size: 10.5px; color: #97948d; font-variant-numeric: tabular-nums; }
.ag-cost.over { color: #b3261e; }     /* 세션 비용이 상한을 넘었다 — 다음 질문은 한 번 더 묻는다 */
.ag-btn { border: none; background: none; font-size: 11.5px; color: #8a8780; cursor: pointer;
  padding: 3px 6px; border-radius: 7px; }
.ag-btn:hover { background: #f0efec; }
.ag-btn:disabled { cursor: default; opacity: .5; }
.ag-x { font-size: 13px; }

/* 세션 목록 — 머리 아래로 펼친다 */
.ag-menu { position: absolute; left: 8px; right: 8px; top: calc(100% + 2px); z-index: 2;
  display: none; max-height: 60vh; overflow-y: auto; background: #fff; border: 1px solid #e6e4e0;
  border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.14); padding: 5px; font-size: 12px; }
.ag-menu.open { display: block; }
.ag-mi { display: flex; align-items: center; gap: 6px; width: 100%; border: none; background: none;
  font: inherit; color: inherit; text-align: left; padding: 6px 8px; border-radius: 7px; cursor: pointer; }
.ag-mi:hover { background: #f0efec; }
.ag-mi.on { background: #eef4fd; }
.ag-mi-t { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-mi-s { flex: 0 0 auto; color: #97948d; font-size: 10.5px; }
.ag-mi-act { color: #6b6862; }
.ag-mi-del { color: #b3261e; }
.ag-msep { height: 1px; background: #ecebe8; margin: 4px 2px; }
.ag-rename { display: flex; gap: 5px; padding: 4px 4px 6px; }
.ag-rename input { flex: 1 1 auto; min-width: 0; border: 1px solid #e0ded9; border-radius: 7px;
  padding: 4px 7px; font: inherit; font-size: 12px; background: #fff; color: inherit; outline: none; }
.ag-rename input:focus { border-color: #2383e2; }

.ag-list { flex: 1 1 auto; overflow-y: auto; padding: 10px 11px; }
.ag-empty { padding: 26px 14px; font-size: 12px; color: #97948d; text-align: center;
  line-height: 1.75; white-space: pre-line; }
.ag-q { margin: 4px 0 8px 30px; padding: 7px 10px; border-radius: 12px 12px 3px 12px;
  background: #eef4fd; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap;
  overflow-wrap: break-word; }
.ag-a { font-size: 12.5px; line-height: 1.68; overflow-wrap: break-word; }
.ag-a p, .ag-a ul, .ag-a ol, .ag-a blockquote { margin: 0 0 7px; }
.ag-a ul, .ag-a ol { padding-left: 19px; }
.ag-a code { background: #f4f3f0; border-radius: 4px; padding: 0 3px; font-size: 11.5px; }
.ag-a pre { background: #f4f3f0; border-radius: 8px; padding: 7px 9px; overflow-x: auto; }
.ag-a h1, .ag-a h2, .ag-a h3 { font-size: 13px; margin: 2px 0 5px; }
.ag-live { white-space: pre-wrap; }      /* 흐르는 동안은 날것 — done에서 렌더된 HTML로 바뀐다 */
.ag-turn + .ag-turn { margin-top: 10px; }
.ag-turn.ag-user + .ag-turn.ag-user, .ag-turn.ag-bot + .ag-turn.ag-user { border-top: 1px solid #f0efec;
  padding-top: 10px; }

/* tool 호출 한 줄 — 접혀 있다가 누르면 펼친다 */
.ag-tool { font-size: 10.5px; color: #8a8780; line-height: 1.5; margin: 0 0 4px; padding: 2px 6px;
  border-radius: 6px; background: #f7f6f3; cursor: pointer; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.ag-tool.full { white-space: pre-wrap; overflow-wrap: anywhere; }
.ag-tool b { color: #6b6862; font-weight: 600; }
.ag-tool .ag-tres { color: #5b8f5b; }

/* 인용 칩 — 작은 원형 숫자 (마크업은 서버 cites.render_html) */
a.chat-cite { display: inline-block; min-width: 15px; height: 15px; padding: 0 3px; margin: 0 2px;
  border-radius: 8px; background: #2383e2; color: #fff; font-size: 9.5px; font-weight: 600;
  line-height: 15px; text-align: center; cursor: pointer; text-decoration: none;
  vertical-align: 1px; }
a.chat-cite:hover { background: #1668c4; }
a.chat-cite.ag-far { background: #fff; color: #2383e2; border: 1px solid #2383e2; line-height: 13px; }
a.chat-cite.ag-far:hover { background: #eef4fd; }
/* 독자 메모 근거 칩 — 논문 문단 칩과 한눈에 구별되게 형광펜 색으로 */
a.chat-cite-note { background: #ffe08a; color: #4a3b00; border: 1px solid #d9a406;
  line-height: 13px; }
a.chat-cite-note:hover { background: #f7cf60; }

.ag-meta { font-size: 10.5px; color: #97948d; margin: 3px 0 4px; }
.ag-err { border: 1px solid rgba(179,38,30,.35); background: rgba(179,38,30,.08); color: #b3261e;
  border-radius: 10px; padding: 8px 10px; font-size: 11.5px; line-height: 1.6; margin: 4px 0 8px; }
/* 승인 카드 — 쓰기 tool이 실행 전에 묻는다. 답을 보내면 결과(승인됨/거절됨/건너뜀)로 잠긴다(.done) */
.ag-appr { border: 1px solid #e8c66a; background: #fffaf0; border-radius: 10px; padding: 8px 10px;
  font-size: 11.5px; line-height: 1.55; margin: 4px 0 8px; }
.ag-appr.done { border-color: #e6e4e0; background: #f9f8f6; color: #6b6862; }
.ag-appr-h { font-weight: 600; margin-bottom: 4px; }
.ag-appr-c { padding: 3px 0; }
.ag-appr-c + .ag-appr-c { border-top: 1px dashed #ecdcae; }
.ag-appr.done .ag-appr-c + .ag-appr-c { border-top-color: #ecebe8; }
.ag-appr-r { display: flex; align-items: center; gap: 6px; }
.ag-appr-i { flex: 0 0 auto; font-size: 15px; color: #a07b10; }
.ag-appr.done .ag-appr-i { color: #97948d; }
.ag-appr-d { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.ag-appr button { border: 1px solid #e0ded9; background: #fff; border-radius: 7px; font: inherit;
  font-size: 11px; padding: 2px 8px; cursor: pointer; color: inherit; flex: 0 0 auto; }
.ag-appr button:disabled { cursor: default; opacity: .55; }
.ag-appr button.on { border-color: #2383e2; background: #eef4fd; color: #1668c4; }
.ag-appr button.ag-appr-tg { border: none; background: none; padding: 0 2px; color: #97948d;
  font-size: 10.5px; }
.ag-appr button.ag-appr-tg:hover { color: #6b6862; }
.ag-appr-args { display: none; margin: 4px 0 2px 21px; padding: 5px 7px; border-radius: 6px;
  background: rgba(0,0,0,.04); font-size: 10.5px; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 180px; overflow-y: auto; }
.ag-appr-c.open .ag-appr-args { display: block; }
.ag-appr-s { flex: 0 0 auto; font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 9px; }
.ag-appr-s.ok { background: #e3f1e3; color: #2f6f2f; }
.ag-appr-s.no { background: #f6e0de; color: #9c2a20; }
.ag-appr-s.skip { background: #ecebe8; color: #6b6862; }
.ag-appr-all { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; padding-top: 6px;
  border-top: 1px solid #ecdcae; }
/* 비용 상한 경고 — 턴 시작 전에 서버가 막았다. '그래도 계속'은 같은 요청을 override로 다시 보낸다 */
.ag-cap { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; border: 1px solid #e8c66a;
  background: #fffaf0; border-radius: 10px; padding: 7px 10px; font-size: 11.5px; line-height: 1.55;
  margin: 4px 0 8px; }
.ag-cap-t { flex: 1 1 180px; min-width: 0; }
.ag-cap button { border: 1px solid #e0ded9; background: #fff; border-radius: 7px; font: inherit;
  font-size: 11px; padding: 2px 8px; cursor: pointer; color: inherit; }
.ag-cap button.ag-cap-go { border-color: #2383e2; background: #eef4fd; color: #1668c4; }

.ag-foot { border-top: 1px solid #ecebe8; padding: 8px 9px; }
.ag-sel { display: none; align-items: center; gap: 5px; font-size: 10.5px; color: #8a8780;
  margin: 0 0 6px; }
.ag-sel.on { display: flex; }
.ag-sel-t { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ag-row { display: flex; gap: 6px; align-items: flex-end; }
.ag-input { flex: 1 1 auto; resize: none; max-height: 120px; box-sizing: border-box;
  border: 1px solid #e0ded9; border-radius: 10px; padding: 7px 9px; font: inherit;
  font-size: 12.5px; line-height: 1.5; background: #fff; color: inherit; outline: none; }
.ag-input:focus { border-color: #2383e2; }
.ag-input:disabled { background: #f4f3f0; color: #97948d; }
.ag-send { flex: 0 0 30px; width: 30px; height: 30px; border: none; border-radius: 50%;
  background: #2383e2; color: #fff; font-size: 13px; cursor: pointer; }
.ag-send:hover { background: #1668c4; }
.ag-send:disabled { background: #d8d6d1; cursor: default; }

.ag-wait { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: #97948d;
  margin: 0 0 9px; }
.ag-dots span { display: inline-block; width: 5px; height: 5px; margin-right: 3px;
  border-radius: 50%; background: #a9a69f; animation: mdAgentDot 1.1s infinite ease-in-out; }
.ag-dots span:nth-child(2) { animation-delay: .18s; }
.ag-dots span:nth-child(3) { animation-delay: .36s; }
@keyframes mdAgentDot { 0%, 60%, 100% { opacity: .25; } 30% { opacity: 1; } }

/* 인용 칩 클릭 → 본문 해당 행 깜빡임 (원문·번역 셀 모두) */
.chat-cite-flash { animation: mdChatFlash 1.3s ease-out; }
@keyframes mdChatFlash { 0%, 55% { outline: 2px solid #2383e2; outline-offset: 2px; }
  100% { outline-color: transparent; } }

/* '읽던 자리로' 되돌리기 알약 — 인용 칩 점프로 잃은 자리를 되돌린다.
   서랍 밖 body 직속이라 서랍을 닫아도 남고, 뷰어가 다시 그려져도 살아 있다.
   right/bottom/max-width는 JS가 본문 스크롤 컨테이너를 재서 넣는다 — CSS는 모양만 정한다
   (CSS로 right: calc(var(--ag-w) + …)를 쓰면 PDF를 켠 순간 PDF 위에 얹힌다). */
:root { --mjb-ring: #8a8780; }
#md-jump-back { position: fixed; z-index: 8500; right: 24px; bottom: 24px; display: none;
  align-items: center; max-width: 300px; background: #fff; color: #37352f;
  border: 1px solid #e6e4e0; border-radius: 999px; box-shadow: 0 6px 20px rgba(0,0,0,.16);
  font-size: 12px; overflow: hidden; }
#md-jump-back.on { display: inline-flex; animation: mdJumpIn .14s ease-out; }
#md-jump-back button { border: none; background: none; color: inherit; font: inherit;
  cursor: pointer; padding: 6px 12px; line-height: 1.45; white-space: nowrap; }
#md-jump-back .mjb-go { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
#md-jump-back .mjb-go:hover { background: #f7f6f3; }
#md-jump-back .mjb-i { color: #2383e2; font-weight: 600; }   /* 인용 칩과 같은 파랑 — 그 점프의 되돌림 */
#md-jump-back .mjb-w { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: #97948d; }
#md-jump-back .mjb-x { color: #97948d; font-size: 11px; padding: 6px 10px 6px 8px;
  border-left: 1px solid #ecebe8; }
#md-jump-back .mjb-x:hover { background: #f0efec; color: #6b6862; }
#md-jump-back button:focus-visible { outline: 2px solid #2383e2; outline-offset: -2px; }
@keyframes mdJumpIn { from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); } }
/* 도착 표시 — 근거 문단의 파란 chat-cite-flash와 다른 무채색 테두리.
   "여기가 근거다"와 "여기로 돌아왔다"는 다른 말이다. flash()가 1400ms에 클래스를 떼므로 1.3s. */
.mjb-flash { animation: mdJumpFlash 1.3s ease-out; }
@keyframes mdJumpFlash { 0%, 55% { outline: 2px solid var(--mjb-ring, #8a8780);
    outline-offset: 2px; }
  100% { outline-color: transparent; } }
@media (prefers-reduced-motion: reduce) { #md-jump-back.on { animation: none; } }

@media (prefers-color-scheme: dark) {
  #md4-agent { background: #1f1f1f; border-color: #3a3a3a; color: #d4d4d4; }
  body.ag-open.ag-overlay #md4-agent { box-shadow: -12px 0 36px rgba(0,0,0,.6); }
  .ag-head, .ag-foot { border-color: #2c2c2c; }
  .ag-model, .ag-a code, .ag-a pre, .ag-tool { background: #2c2c2c; color: #b8b8b8; }
  .ag-tool b { color: #d0d0d0; }
  .ag-model:hover { background: #3a3a3a; }
  .ag-btn:hover, .ag-sess:hover, .ag-mi:hover { background: #2c2c2c; }
  .ag-menu { background: #262626; border-color: #3a3a3a; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  .ag-mi.on, .ag-q { background: #24303d; }
  .ag-msep { background: #333; }
  .ag-empty, .ag-meta, .ag-cost, .ag-sess-c, .ag-mi-s, .ag-sel { color: #8f8f8f; }
  .ag-input, .ag-rename input { background: #262626; border-color: #3a3a3a; }
  .ag-input:disabled { background: #232323; color: #7d7d7d; }
  .ag-turn.ag-user + .ag-turn.ag-user, .ag-turn.ag-bot + .ag-turn.ag-user { border-color: #2c2c2c; }
  .ag-appr, .ag-cap { background: #2b2618; border-color: #6b5a24; }
  .ag-appr.done { background: #242424; border-color: #3a3a3a; color: #a0a0a0; }
  .ag-appr-c + .ag-appr-c, .ag-appr-all { border-color: #4a3f1c; }
  .ag-appr.done .ag-appr-c + .ag-appr-c { border-top-color: #333; }
  .ag-appr-i { color: #d9b44a; }
  .ag-appr button, .ag-cap button { background: #262626; border-color: #3a3a3a; }
  .ag-appr button.on, .ag-cap button.ag-cap-go { background: #24303d; border-color: #5a96e6;
    color: #8fbcf0; }
  .ag-appr button.ag-appr-tg { background: none; color: #8f8f8f; }
  .ag-appr-args { background: rgba(255,255,255,.06); }
  .ag-appr-s.ok { background: #1f3322; color: #8fd18f; }
  .ag-appr-s.no { background: #3a2220; color: #f0a39b; }
  .ag-appr-s.skip { background: #2c2c2c; color: #a0a0a0; }
  .ag-cost.over { color: #f0a39b; }
  a.chat-cite.ag-far { background: #1f1f1f; color: #5a96e6; border-color: #5a96e6; }
  :root { --mjb-ring: #a9a69f; }
  #md-jump-back { background: #262626; border-color: #3a3a3a; color: #d4d4d4;
    box-shadow: 0 6px 20px rgba(0,0,0,.5); }
  #md-jump-back .mjb-go:hover, #md-jump-back .mjb-x:hover { background: #2c2c2c; }
  #md-jump-back .mjb-i { color: #5a96e6; }
  #md-jump-back .mjb-w, #md-jump-back .mjb-x { color: #8f8f8f; }
  #md-jump-back .mjb-x { border-left-color: #333; }
}
"""

# 서랍 마크업. body에 고정돼 있어 화면을 갈아 끼워도 대화·진행 중인 답이 그대로 남는다.
HTML = """
<div id="md4-agent" role="complementary" aria-label="에이전트">
  <div class="ag-handle" title="드래그해서 너비 조절"></div>
  <div class="ag-head">
    <button type="button" class="ag-sess" title="세션 — 목록 · 이름 바꾸기 · 삭제"><span
      class="ag-sess-t">에이전트</span><span class="ag-sess-c">&#9662;</span></button>
    <select class="ag-model" title="에이전트가 쓸 모델 — 번역 설정과 별개입니다"></select>
    <span class="ag-cost" title="이 세션에서 쓴 비용 (누적)"></span>
    <button type="button" class="ag-btn ag-new" title="새 세션">+ 새 세션</button>
    <button type="button" class="ag-btn ag-x" title="닫기 (Esc)">&#10005;</button>
    <div class="ag-menu" role="menu"></div>
  </div>
  <div class="ag-list"></div>
  <div class="ag-foot">
    <div class="ag-sel"><span>선택한 글 함께 보냄:</span><span class="ag-sel-t"></span>
      <button type="button" class="ag-btn ag-sel-x" title="선택한 글 빼기">&#10005;</button></div>
    <div class="ag-row">
      <textarea class="ag-input" rows="1"
        placeholder="무엇이든 물어보세요 (Shift+Enter 줄바꿈)"></textarea>
      <button type="button" class="ag-send" title="전송 (Enter)">&#10148;</button>
    </div>
  </div>
</div>
<!-- 서랍 **밖**이다: 서랍 안에 두면 서랍을 닫는 순간 함께 사라지는데, 서랍을 접고 본문만
     읽는 사람에게도 읽던 자리로 돌아갈 길은 있어야 한다. -->
<div id="md-jump-back" role="status" aria-live="polite">
  <button type="button" class="mjb-go" aria-label="읽던 자리로 돌아가기"
    title="칩을 누르기 전 읽던 자리로 돌아갑니다"><span class="mjb-i"
    aria-hidden="true">&#8617;</span><span>읽던 자리로</span><span class="mjb-w"></span></button>
  <button type="button" class="mjb-x" aria-label="되돌아가기 버튼 닫기"
    title="닫기 (Esc)">&#10005;</button>
</div>
"""

JS = r"""
(function(){
  if (window.__md4AgentInit) return; window.__md4AgentInit = true;
  var panel = document.getElementById('md4-agent');
  if (!panel) return;
  var list = panel.querySelector('.ag-list');
  var input = panel.querySelector('.ag-input');
  var sendBtn = panel.querySelector('.ag-send');
  var modelSel = panel.querySelector('.ag-model');
  var costEl = panel.querySelector('.ag-cost');
  var sessBtn = panel.querySelector('.ag-sess');
  var sessT = panel.querySelector('.ag-sess-t');
  var menu = panel.querySelector('.ag-menu');
  var newBtn = panel.querySelector('.ag-new');
  var selBar = panel.querySelector('.ag-sel');
  var selT = panel.querySelector('.ag-sel-t');

  // 브라우저에 두는 것 — 활성 세션·서랍 폭·열림 여부(localStorage), 다른 논문으로 가는 행(sessionStorage)
  var LS_SESSION = 'md4agent.session', LS_W = 'md4agent.w', LS_OPEN = 'md4agent.open';
  var SS_GOTO = 'md4agent.goto';
  function lsGet(k){ try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v){
    try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {}
  }

  var paper = window.__md4AgentTok || null;     // 지금 화면의 논문 토큰 (홈이면 null)
  var sid = null, sess = null, sessList = [], turns = [], pending = null;
  var live = null, busy = false, errMsg = null, loaded = false, menuMode = '';
  var toolsByTurn = {};      // 턴 id → 그 답을 만들며 부른 tool 줄 (이 창이 떠 있는 동안만)
  var decisions = {};        // 승인 카드 — tool_call id → true/false (모든 호출에 답이 모이면 보낸다)
  var apprByTurn = {};       // 턴 id → 그 턴 뒤에서 끝난 승인 카드들 [{calls, decisions, skipped}] (잠김)
  var capRetry = null;       // 비용 상한으로 막힌 요청 {body, msg} — '그래도 계속'이 override를 더해 다시 보낸다
  var PH_IDLE = input.placeholder;
  var PH_WAIT = '승인을 기다리는 중 — 그냥 보내면 건너뜁니다';
  var models = [], modelInfo = {ready: true, reason: '', provider: '', model: ''};

  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s;
    return d.innerHTML; }
  function onReview(){ return location.pathname.indexOf('/review') === 0; }
  function api(method, url, body){
    var opt = {method: method, headers: {}};
    if (body !== undefined){
      opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body);
    }
    return fetch(url, opt).then(function(r){
      return r.json().then(function(j){
        if (!r.ok) throw new Error(j.error || j.detail || ('HTTP ' + r.status));
        return j;
      }, function(){ if (!r.ok) throw new Error('HTTP ' + r.status); return {}; });
    });
  }
  // SPA 이동은 서버가 한다(ui.navigate.to) — 셸의 ui.on('md4-agent-nav')가 받는다
  function nav(url){
    if (!url) return;
    if (typeof emitEvent === 'function') emitEvent('md4-agent-nav', {url: url});
    else location.href = url;
  }

  // ---- 화면 맥락 스냅숏 (매 질문에 싣는다 — 서버는 run 도중 브라우저에 물을 수 없다) ----
  var lastSel = null;          // 서랍 밖에서 마지막으로 고른 글 — 입력창을 누르면 선택이 풀리므로 붙들어 둔다
  function selNow(){
    var s = window.getSelection && window.getSelection();
    if (!s || !s.rangeCount || s.isCollapsed) return null;
    var n = s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentElement);
    if (!el || panel.contains(el)) return null;
    var q = String(s.toString() || '').replace(/\s+/g, ' ').trim();
    if (!q) return null;
    var sc = el.closest('.anno-scope') || el.closest('[data-side]');
    var rc = el.closest('[data-row]');
    var row = rc ? parseInt(rc.getAttribute('data-row'), 10) : null;
    return {side: sc ? (sc.getAttribute('data-side') || null) : null,
            row: isNaN(row) ? null : row, quote: q.slice(0, 300)};
  }
  function paintSel(){
    selBar.classList.toggle('on', !!lastSel);
    selT.textContent = lastSel ? '“' + lastSel.quote.slice(0, 80) + '”' : '';
  }
  document.addEventListener('selectionchange', function(){
    var got = selNow();
    if (got){ lastSel = got; paintSel(); return; }
    var a = document.activeElement;
    // 서랍 안(입력창·단추)으로 초점이 옮겨 가며 풀린 선택은 지키고, 본문을 다시 눌러 푼 선택만 지운다
    if (a && panel.contains(a)) return;
    var s = window.getSelection && window.getSelection();
    var n = s && s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentElement);
    if (el && panel.contains(el)) return;
    if (lastSel){ lastSel = null; paintSel(); }
  });
  panel.querySelector('.ag-sel-x').addEventListener('click', function(){ lastSel = null; paintSel(); });

  function paperTitle(){
    var t = document.querySelector('.md4-tab.on .md4-tab-t');
    return ((t && t.textContent) || document.title || '').trim().slice(0, 200);
  }
  function visibleRows(){
    var g = jbScroller(null), out = [];
    if (!g) return out;
    var gr = g.getBoundingClientRect();
    var els = g.querySelectorAll('.sbs-cell[data-row]');
    if (!els.length) els = g.querySelectorAll('[data-row]');
    for (var i = 0; i < els.length && out.length < 60; i++){
      var r = els[i].getBoundingClientRect();
      if (r.width <= 0 || r.bottom <= gr.top || r.top >= gr.bottom) continue;
      var n = parseInt(els[i].getAttribute('data-row'), 10);
      if (!isNaN(n) && out.indexOf(n) < 0) out.push(n);
    }
    return out;
  }
  function langNow(){
    var g = document.querySelector('.sbs-grid');
    if (!g) return null;
    if (g.classList.contains('sbs-two')) return 'both';
    var cs = g.querySelectorAll('.sbs-cell[data-side]');
    for (var i = 0; i < cs.length && i < 40; i++)
      if (cs[i].getBoundingClientRect().width > 0) return cs[i].getAttribute('data-side');
    return null;
  }
  function noteTabs(){
    var ts = document.querySelectorAll('.md4-notetab[data-tab]');
    if (!ts.length) return null;
    var open = [], focus = null;
    for (var i = 0; i < ts.length; i++){
      var name = ts[i].getAttribute('data-tab');
      open.push(name);
      if (ts[i].classList.contains('on')) focus = name;
    }
    return {open: open, focus: focus};
  }
  function leftPanel(){
    var b = document.querySelector('.md4-railbtn.on');
    if (!b) return null;
    var l = b.querySelector('div');            // 아이콘(<i>, 글자가 아이콘 이름이다) 다음의 라벨
    return ((l ? l.textContent : '') || '').trim() || null;
  }
  function projectNow(){
    var p = document.querySelector('.md4-prow.on .md4-prow-name');
    return p ? {id: null, name: (p.textContent || '').trim()} : null;
  }
  function ctxNow(){
    var review = onReview();
    var rows = review ? visibleRows() : [];
    return {
      screen: review ? 'review' : 'home',
      paper: (review && paper) ? {token: paper, title: paperTitle()} : null,
      lang: review ? langNow() : null,
      visible_rows: rows,
      top_row: rows.length ? rows[0] : null,
      selection: lastSel || selNow(),
      note_tabs: review ? noteTabs() : null,
      left_panel: review ? leftPanel() : null,
      project: review ? null : projectNow()
    };
  }
  window.__md4Context = ctxNow;

  // ---- 본문(뷰어 정렬 행)으로 이동 + 깜빡임 ----
  function cellsOf(row){
    var els = document.querySelectorAll('.sbs-grid .sbs-cell[data-row="' + row + '"]');
    if (!els.length) els = document.querySelectorAll('[data-row="' + row + '"]');
    var out = [];
    for (var i = 0; i < els.length; i++){
      if (panel.contains(els[i])) continue;
      out.push(els[i]);
      // 정렬 그리드는 첫 표시 셀에만 data-row가 붙는다 — 짝 셀(번역)도 함께 깜빡이게
      var sib = els[i].nextElementSibling;
      if (sib && sib.classList.contains('sbs-cell') && !sib.hasAttribute('data-row')) out.push(sib);
    }
    return out;
  }
  function flash(els, cls){
    els.forEach(function(el){
      el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
      setTimeout(function(){ el.classList.remove(cls); }, 1400);
    });
  }
  function gotoRow(row){
    var els = cellsOf(row);
    if (!els.length) return false;
    jbMark(els[0]);        // 스크롤을 시작하기 **전에** 읽던 자리를 집는다(중간값을 집으면 밀린다)
    els[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(els, 'chat-cite-flash');
    return true;
  }
  // 메모 근거 → 그 하이라이트(mark)로. 뷰어의 md-anno-flash를 그대로 재사용한다.
  function marksOf(id){
    var ms = document.querySelectorAll('mark.md-anno'), out = [];
    for (var i = 0; i < ms.length; i++){
      if ((ms[i].getAttribute('data-ids') || '').split(' ').indexOf(id) >= 0) out.push(ms[i]);
    }
    return out;
  }
  function gotoNote(id, row){
    var ms = marksOf(id);
    if (!ms.length) return gotoRow(row);      // 아직 안 칠해졌거나 위치를 못 찾은 메모
    jbMark(ms[0]);                            // gotoRow로 빠진 경우엔 거기서 무장한다(이중 무장 없음)
    ms[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    flash(ms, 'md-anno-flash');
    return true;
  }
  function headingOf(row){               // 그 행 앞쪽에서 가장 가까운 헤더 (알약 라벨용)
    var cells = Array.prototype.slice.call(
      document.querySelectorAll('.sbs-grid .sbs-cell[data-row]'));
    var i = -1;
    for (var k = 0; k < cells.length; k++)
      if (cells[k].getAttribute('data-row') === String(row)) { i = k; break; }
    for (var j = i; j >= 0; j--){
      var h = cells[j].querySelector('h1,h2,h3,h4,h5,h6');
      if (h) return (h.textContent || '').trim();
    }
    return '';
  }
  // 다른 논문의 행 — 그 논문을 연 뒤 도착한 뷰어(setPaper)가 이어서 간다
  function gotoElsewhere(tok, row, id, url){
    if (!url) return false;
    try {
      sessionStorage.setItem(SS_GOTO, JSON.stringify({token: tok, row: row, id: id || null,
                                                      at: Date.now()}));
    } catch (e) {}
    nav(url);
    return true;
  }
  function consumeGoto(){
    var g = null;
    try { g = JSON.parse(sessionStorage.getItem(SS_GOTO) || 'null'); } catch (e) {}
    if (!g) return;
    if (Date.now() - (g.at || 0) > 30000){ try { sessionStorage.removeItem(SS_GOTO); } catch (e) {} return; }
    if (!paper || g.token !== paper) return;     // 아직 그 논문에 닿지 않았다
    try { sessionStorage.removeItem(SS_GOTO); } catch (e) {}
    var tries = 0;
    (function wait(){
      if (paper !== g.token) return;              // 기다리는 사이 다른 화면으로 갔다
      if (!cellsOf(g.row).length && ++tries < 60) return setTimeout(wait, 150);
      // 읽던 자리 복원(scroll_memory)이 상자가 붙는 순간 돈다 — 그 뒤에 움직여야 덮이지 않는다
      setTimeout(function(){ if (g.id) gotoNote(g.id, g.row); else gotoRow(g.row); }, 350);
    })();
  }

  // ---- '읽던 자리로' 되돌리기 (칩 점프가 읽던 자리를 잃지 않게) ----
  // 칩을 누르면 본문이 근거 문단으로 날아가 버려 읽던 자리를 잃는다. 점프 직전의 화면을
  // 구조(행 → 쪽 → 블록 → 블록 안 비율)로 집어 두고, 알약을 눌렀을 때 그 화면을 재현한다.
  var JB_EDGE = 6;            // 기준선 = 컨테이너 위끝 + 6 (app.py PDF 싱크 top+6과 같은 정의)
  var JB_ARM = 0.30, JB_ARM_MIN = 120;   // 이만큼 움직일 점프에만 알약을 띄운다
  var JB_HOME = 0.22, JB_HOME_MIN = 80;  // 이만큼 가까워지면 알약은 할 일이 없다 (무장 임계보다
                                         //  작다 = 히스테리시스. 뜨자마자 사라지지 않는다)
  var JB_GRACE = 700;         // 나가는 점프가 멎을 때까지 자동 해제를 미룬다
  var JB_BLOCKS = 'p,li,h1,h2,h3,h4,h5,h6,pre,blockquote,table,figure,img';
  var jbEl = document.getElementById('md-jump-back');
  var pin = null, jbG = null, jbRaf = 0, jbFrames = 0, jbKey = '', jbJumpAt = 0;

  function jbScroller(from){
    if (from && from.closest){
      var c = from.closest('.sbs-grid, .vpane');     // 실측: computed overflow 조상 탐색과 동일
      if (c){ jbG = c; return c; }
    }
    if (jbG && jbG.isConnected && jbG.clientHeight > 0) return jbG;  // 재렌더까지는 그대로 쓴다
    jbG = null;
    var all = document.querySelectorAll('.sbs-grid, .vpane');
    for (var i = 0; i < all.length; i++){
      var r = all[i].getBoundingClientRect();
      if (r.width < 40 || r.height < 40) continue;
      // [data-row]가 캔버스의 정렬 그리드를 가려낸다 — 같은 클래스를 쓰는 다른 상자(data-row 없음)를
      // 잡지 않게. app.py가 바뀌어도 버티게 남긴다.
      if (all[i].matches('[data-row]') || all[i].querySelector('[data-row]')){
        jbG = all[i]; break;
      }
    }
    return jbG;
  }
  function jbNorm(s){ return (s || '').replace(/\s+/g, ' ').trim(); }
  function jbCells(g, row){
    var sel = '[data-row="' + row + '"]';
    var self = (g.matches && g.matches(sel)) ? [g] : [];   // .vpane은 g.querySelector로 안 잡힌다
    return self.concat([].slice.call(g.querySelectorAll(sel)));
  }
  // 지금 화면 맨 위에 걸린 자리를 구조로 집는다. 참조(노드)는 담지 않는다 — 뷰어는 패널을 토글할
  // 때마다 그리드를 통째로 다시 만들므로, 붙들어 둔 노드는 조용히 죽은 노드가 된다.
  function jbCapture(g){
    var edge = g.getBoundingClientRect().top + JB_EDGE;
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    var els = (g.matches && g.matches('[data-row]') ? [g] : [])
      .concat([].slice.call(g.querySelectorAll('[data-row]')));
    var band = null, i, r;
    for (i = 0; i < els.length; i++){
      r = els[i].getBoundingClientRect();
      if (r.top > edge) break;                  // 아직 기준선 아래 — 문서 순서라 여기서 끝
      var key = els[i].getAttribute('data-row');
      // 한 행의 두 셀은 같은 grid row라 top이 같고 DOM에서도 붙어 있다 → 연속 묶음이면 한 밴드
      if (!band || band.row !== key) band = {row: key, cs: [], t: r.top, b: r.bottom};
      band.cs.push(els[i]);
      band.t = Math.min(band.t, r.top); band.b = Math.max(band.b, r.bottom);
    }
    if (!band) return null;
    // 쪽 고르기: 기준선 아래로 내용이 이어지는 셀만, 그중 원문(en) 우선. 짧은 쪽 셀을 잡아
    // 비율이 늘 1이 되는 사고를 막고, 재번역에도 원문 텍스트가 더 잘 버틴다.
    var lv = [];
    for (i = 0; i < band.cs.length; i++)
      if (band.cs[i].getBoundingClientRect().bottom > edge) lv.push(band.cs[i]);
    var pool = lv.length ? lv : band.cs, cell = pool[0];
    for (i = 0; i < pool.length; i++)
      if (pool[i].getAttribute('data-side') === 'en'){ cell = pool[i]; break; }
    // 블록 하위 앵커 — 실측으로 한 셀이 2440px·17블록까지 간다. 행 단위로만 집으면 최대 3화면
    // 어긋난다. 폭이 바뀌어 줄바꿈이 전부 달라져도 블록 안 비율은 버틴다.
    var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
    var bi = 0, bf = 0, head = '';
    for (i = 0; i < bs.length; i++)
      if (bs[i].getBoundingClientRect().top <= edge) bi = i;
    if (bs.length){
      var br = bs[bi].getBoundingClientRect();
      bf = br.height > 0 ? Math.max(0, Math.min(1, (edge - br.top) / br.height)) : 0;
      head = jbNorm(bs[bi].textContent).slice(0, 72);
    }
    var span = band.b - band.t;
    var rf = span > 0 ? Math.max(0, Math.min(1, (edge - band.t) / span)) : 0;
    var row = parseInt(band.row, 10) || 0;
    var pv = document.querySelector('.vpdf');
    return {row: row, side: cell.getAttribute('data-side') || '', bi: bi, bf: bf, rf: rf,
            ratio: max > 0 ? g.scrollTop / max : 0,   // app.py 복원과 같은 통화 (최후 폴백)
            head: head, sec: headingOf(row), at: Date.now(),
            dest: g.scrollTop, destFixed: 0,
            pdf: pv ? pv.scrollTop : null, pdfH: pv ? pv.scrollHeight : null};
  }
  function jbNeed(g, el){        // scrollIntoView({block:'center'})가 만들 이동량 (범위 클램프)
    var gr = g.getBoundingClientRect(), er = el.getBoundingClientRect();
    var want = (er.top - gr.top) - Math.max(0, (g.clientHeight - er.height) / 2);
    var lo = -g.scrollTop, hi = (g.scrollHeight - g.clientHeight) - g.scrollTop;
    return Math.max(lo, Math.min(hi, want));
  }
  function jbMark(target){
    if (!jbEl || !target) return false;
    var g = jbScroller(target);
    if (!g) return false;
    var need = jbNeed(g, target);
    // 이미 화면에 있는 문단으로 가는 칩엔 알약을 띄우지 않는다 — 자리를 잃지 않았으니 소음이다
    if (Math.abs(need) < Math.max(JB_ARM_MIN, g.clientHeight * JB_ARM)) return false;
    if (pin){
      // 연달아 칩을 눌러도 '처음 읽던 자리'를 지킨다(히스토리 스택이 아니라 집 하나다).
      // 예외: 지난 점프가 놓아둔 자리에서 1.2화면 이상 옮겨가 정착했으면 거기가 새 '읽던 자리'다.
      if (Math.abs(g.scrollTop - (pin.dest || 0)) > g.clientHeight * 1.2) pin = jbCapture(g);
    } else pin = jbCapture(g);
    if (!pin) return false;
    pin.dest = g.scrollTop + need;      // 점프가 끝나면 여기 있을 것 (아래 tick이 실측으로 고친다)
    pin.destFixed = 0;
    jbJumpAt = Date.now();
    var go = jbEl.querySelector('.mjb-go');
    jbEl.querySelector('.mjb-w').textContent = pin.sec ? ' · ' + pin.sec.slice(0, 26) : '';
    go.title = '칩을 누르기 전 읽던 자리로 돌아갑니다' + (pin.sec ? ' — ' + pin.sec : '')
      + (pin.head ? ' “' + pin.head.slice(0, 50) + '”' : '');
    go.setAttribute('aria-label', '읽던 자리로 돌아가기' + (pin.sec ? ' — ' + pin.sec : ''));
    jbKey = '';                          // 다음 프레임에 좌표를 새로 쓰게
    if (!jbRaf) jbRaf = requestAnimationFrame(jbTick);
    return true;
  }
  function jbTarget(g, p){
    var cs = jbCells(g, p.row), cell = null, i;
    for (i = 0; i < cs.length; i++) if (cs[i].getAttribute('data-side') === p.side) cell = cs[i];
    cell = cell || cs[0] || null;
    if (cell){
      var bs = [].slice.call(cell.querySelectorAll(JB_BLOCKS));
      if (bs.length){
        var el = bs[Math.min(p.bi, bs.length - 1)];
        var same = cell.getAttribute('data-side') === p.side;
        // 같은 쪽이면 텍스트로 검증한다 — 재번역으로 행 인덱스가 밀렸을 때 조용히 틀린 자리로
        // 가는 것이 비율 폴백보다 더 나쁘다(티가 안 난다). 다른 쪽 셀이면 검증하지 않는다:
        // 그 짝은 같은 문장의 번역이라 텍스트가 당연히 다르다.
        if (!same || !p.head || jbNorm(el.textContent).slice(0, 40) === p.head.slice(0, 40))
          return {el: el, f: p.bf};
      }
    }
    if (p.head){                       // 행 인덱스가 밀렸다 → 텍스트로 다시 찾는다
      var pre = p.head.slice(0, 40), hit = null;
      var all = g.querySelectorAll(JB_BLOCKS);
      for (i = 0; i < all.length; i++){
        if (jbNorm(all[i].textContent).indexOf(pre) === 0){ hit = all[i]; break; }
      }
      if (hit) return {el: hit, f: p.bf};
    }
    if (cell && cell !== g) return {el: cell, f: p.rf};   // 빈 셀·표만 있는 셀 → 행 밴드 비율
    return null;                                          // → 호출자가 p.ratio로 떨어진다
  }
  // 앵커의 위끝이 edge - f*height에 오면 그 화면 한 장이 재현된다. offsetTop은 쓰지 않는다 —
  // 셀은 grid item이고 마크다운은 래퍼 한 겹 안이라 offset 부모 사슬이 렌더마다 같지 않다.
  function jbTop(g, p, t){
    var max = Math.max(0, g.scrollHeight - g.clientHeight);
    t = t || jbTarget(g, p);
    if (!t) return max > 0 ? p.ratio * max : 0;
    var er = t.el.getBoundingClientRect();
    var y = g.scrollTop + (er.top - g.getBoundingClientRect().top) - JB_EDGE + t.f * er.height;
    return Math.max(0, Math.min(max, y));
  }
  function jbPlace(){
    var g = jbScroller(null), r = g && g.getBoundingClientRect();
    if (!g || !r || r.width < 160 || r.height < 120){ jbEl.classList.remove('on'); return null; }
    // 컨테이너 rect를 재기 때문에 서랍 폭(--ag-w)·본문 padding·목차 너비·텍스트|PDF 분할을
    // 하나도 몰라도 언제나 '지금 읽는 영역' 안에 온다. +24는 세로 스크롤바 여유.
    // 예외 하나 — 좁은 창의 오버레이 모드(body.ag-overlay)에선 서랍이 본문 위에 떠 있어 컨테이너가
    // 서랍 밑까지 뻗는다. 그때는 서랍 왼끝을 보이는 오른끝으로 삼아 알약이 서랍에 가리지 않게 한다.
    var vr = r.right;
    if (document.body.classList.contains('ag-overlay') && panel.classList.contains('open'))
      vr = Math.min(vr, panel.getBoundingClientRect().left);
    var right = Math.max(12, window.innerWidth - vr + 24);
    var bottom = Math.max(14, window.innerHeight - r.bottom + 18);
    var mw = Math.max(180, Math.min(300, vr - r.left - 28));
    var key = Math.round(right) + '|' + Math.round(bottom) + '|' + Math.round(mw);
    if (key !== jbKey){                 // 값이 바뀔 때만 써서 레이아웃을 흔들지 않는다
      jbKey = key;
      jbEl.style.right = right + 'px'; jbEl.style.bottom = bottom + 'px';
      jbEl.style.maxWidth = mw + 'px';
    }
    jbEl.classList.add('on');
    return g;
  }
  // rAF 하나가 배치와 해제를 다 맡는다 — 재렌더·splitter·서랍 폭·목차·리사이즈·탭 이동을 전부
  // 흡수하고, 탭이 숨으면 저절로 멈춘다. resize/scroll 리스너나 MutationObserver를 늘리지 않는다.
  function jbTick(){
    jbRaf = 0;
    if (!pin) return;
    var g = jbPlace();
    // 프레임마다 rect 1~2회. 무거운 일(앵커 재해석)은 12프레임(≈200ms)마다 한 번.
    if (g && (++jbFrames % 12 === 0) && Date.now() - jbJumpAt > JB_GRACE){
      if (!pin.destFixed){ pin.dest = g.scrollTop; pin.destFixed = 1; }  // 점프가 놓아둔 실제 자리
      // 스스로 그 자리로 돌아왔으면 알약은 할 일이 없다 ("읽던 자리로"가 거짓말이 된다)
      if (Math.abs(g.scrollTop - jbTop(g, pin)) < Math.max(JB_HOME_MIN, g.clientHeight * JB_HOME))
        return jbDismiss();
    }
    jbRaf = requestAnimationFrame(jbTick);
  }
  function jbAfter(g, p){
    g.dispatchEvent(new Event('scroll'));   // PDF 싱크 프라이밍 (app.py의 복원과 같은 손짓)
    var pv = document.querySelector('.vpdf');
    if (!pv || p.pdf == null || pv.scrollHeight !== p.pdfH) return;   // lazy 이미지로 좌표계가 변했다
    // 싱크는 페이지 위끝까지만 맞춘다 — 페이지 안에서 보던 위치까지 되돌린다.
    // 싱크의 rAF 콜백이 지난 뒤에 써야 덮이지 않는다.
    requestAnimationFrame(function(){
      requestAnimationFrame(function(){ pv.scrollTop = p.pdf; });
    });
  }
  function jbBack(){
    var p = pin, g = jbScroller(null);
    if (!p) return;
    if (!g) return;                    // 본문이 없다(다른 탭·PDF 전용) → 아무것도 하지 않고 핀을 지킨다
    var t = jbTarget(g, p), y = jbTop(g, p, t);
    var far = Math.abs(y - g.scrollTop) > g.clientHeight * 4;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var soft = !far && !reduce;        // 먼 거리 smooth는 느리고 멀미난다
    g.scrollTo({top: y, behavior: soft ? 'smooth' : 'auto'});
    if (t && t.el !== g && t.el.classList) flash([t.el], 'mjb-flash');
    var n = 0, last = -1;
    setTimeout(function nudge(){       // 늦게 뜬 그림·KaTeX가 위쪽을 밀면 좌표가 달라진다
      if (last >= 0 && Math.abs(g.scrollTop - last) > 24) return jbAfter(g, p);  // 사용자가 굴렸다
      var y2 = jbTop(g, p);
      if (Math.abs(g.scrollTop - y2) > 4) g.scrollTop = y2;
      last = g.scrollTop;
      if (++n < 3) setTimeout(nudge, 180); else jbAfter(g, p);
    }, soft ? 420 : 60);
    jbDismiss();                       // 돌아왔으면 그 버튼은 할 일이 없다
  }
  function jbDismiss(){
    pin = null; jbKey = ''; jbFrames = 0;
    if (jbEl) jbEl.classList.remove('on');
    if (jbRaf){ cancelAnimationFrame(jbRaf); jbRaf = 0; }
  }
  if (jbEl){
    jbEl.querySelector('.mjb-go').addEventListener('click', jbBack);
    jbEl.querySelector('.mjb-x').addEventListener('click', jbDismiss);
    // fixed 알약 위에서 굴리면 밑의 그리드가 안 움직인다(가장 가까운 스크롤 조상이 body다).
    // 넘겨주지 않으면 "읽다가 알약 위에서 휠이 먹통"이라는 새 짜증을 만든다.
    jbEl.addEventListener('wheel', function(ev){
      var g = jbScroller(null);
      if (!g) return;
      var unit = ev.deltaMode === 1 ? 16 : (ev.deltaMode === 2 ? g.clientHeight : 1);
      g.scrollTop += ev.deltaY * unit;
      ev.preventDefault();
    }, {passive: false});
  }
  // 다른 점프(목차·섹션 트리 등)도 나중에 이걸 재사용할 수 있게 이름 하나만 내놓는다.
  // mark(el)은 **프로그래매틱 스크롤을 시작하기 직전**에 부른다. 반환값 = 알약을 띄웠나.
  window.__mdJumpBack = {mark: jbMark, back: jbBack, dismiss: jbDismiss,
                         state: function(){ return pin; }};

  // ---- 모델 고르기 (에이전트 전용 — 번역 설정과 별개) ----
  var PROV = {openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Gemini'};
  function renderModels(){
    var cur = modelInfo.model || '';
    modelSel.innerHTML = '';
    if (!models.length){                    // 목록을 아직 못 받았으면 이름만 (드롭다운은 비활성)
      var o0 = document.createElement('option');
      o0.textContent = cur; modelSel.appendChild(o0); modelSel.disabled = true;
      return;
    }
    modelSel.disabled = busy;
    var byProv = {};
    models.forEach(function(m){ (byProv[m.provider] = byProv[m.provider] || []).push(m); });
    Object.keys(byProv).forEach(function(prov){
      var g = document.createElement('optgroup');
      g.label = PROV[prov] || prov;
      byProv[prov].forEach(function(m){
        var o = document.createElement('option');
        o.value = m.provider + '/' + m.model;
        // 키 없는 제공사는 목록에 남기되 못 고르게 — 왜 안 보이는지 헤매지 않도록
        o.textContent = m.model + (m.price ? '  ' + m.price : '') + (m.has_key ? '' : '  (키 없음)');
        o.disabled = !m.has_key;
        if (m.model === cur && m.provider === modelInfo.provider) o.selected = true;
        g.appendChild(o);
      });
      modelSel.appendChild(g);
    });
  }
  function takeModels(j){
    models = j.models || models;
    modelInfo = {ready: !!j.ready, reason: j.reason || '', provider: j.provider || '',
                 model: j.model || ''};
  }
  function loadModels(){
    return api('GET', '/agent/models').then(function(j){ takeModels(j); render(); })
      .catch(function(){ renderModels(); });
  }
  modelSel.addEventListener('change', function(){
    var v = (modelSel.value || '').split('/');
    if (v.length < 2) return;
    api('PUT', '/agent/model', {provider: v[0], model: v.slice(1).join('/')})
      .then(function(j){ takeModels(j); errMsg = null; render(); })
      .catch(function(e){ errMsg = '모델을 바꾸지 못했습니다: ' + e.message; render(); });
  });

  // ---- 세션 ----
  function shortTime(ts){
    if (!ts) return '';
    var d = new Date(ts * 1000), now = new Date();
    if (d.toDateString() === now.toDateString())
      return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }
  function setSession(d){
    if (d.id !== sid){ apprByTurn = {}; capRetry = null; }
    sess = d; sid = d.id; lsSet(LS_SESSION, sid);
    turns = d.turns || []; pending = d.pending || null; decisions = {};   // 다시 열면 대기 카드도 되살아난다
    live = null; errMsg = null;
    render();
  }
  function loadList(){
    return api('GET', '/agent/sessions').then(function(j){ sessList = j.sessions || []; return sessList; });
  }
  function openSession(id){
    return api('GET', '/agent/sessions/' + encodeURIComponent(id)).then(setSession);
  }
  function newSession(){
    if (busy) return Promise.resolve();
    closeMenu();
    return api('POST', '/agent/sessions', {}).then(function(d){
      setSession(d);
      return loadList();
    }).then(function(){ renderHead(); setTimeout(function(){ input.focus(); }, 30); })
      .catch(function(e){ errMsg = '세션을 만들지 못했습니다: ' + e.message; render(); });
  }
  // 활성 세션을 정한다 — 브라우저가 기억한 것 → 목록 첫 번째(최근) → 없으면 새로 만든다
  function ensureSession(){
    loaded = true;
    return loadList().then(function(ss){
      var want = sid || lsGet(LS_SESSION), hit = null;
      for (var i = 0; i < ss.length; i++) if (ss[i].id === want) hit = ss[i];
      hit = hit || ss[0];
      if (hit) return openSession(hit.id).catch(function(){ return newSession(); });
      return newSession();
    }).catch(function(e){ errMsg = '세션 목록을 읽지 못했습니다: ' + e.message; render(); });
  }
  function refreshSession(){
    if (!sid) return Promise.resolve();
    var keep = sid;
    return openSession(keep).then(loadList).then(renderHead).catch(function(){});
  }

  // 세션 메뉴 — 목록 · 이름 바꾸기 · 삭제(한 번 더 묻는다). 앱 창(웹뷰)에서 prompt/confirm이
  // 막힐 수 있어 메뉴 안에서 묻는다.
  function closeMenu(){ menu.classList.remove('open'); menuMode = ''; }
  function renderMenu(){
    menu.innerHTML = '';
    if (menuMode === 'rename'){
      var box = document.createElement('div'); box.className = 'ag-rename';
      var inp = document.createElement('input'); inp.value = (sess && sess.title) || '';
      inp.placeholder = '세션 이름';
      var ok = document.createElement('button'); ok.className = 'ag-btn'; ok.textContent = '저장';
      function save(){
        var t = inp.value.trim();
        if (!t || !sid) return closeMenu();
        api('PATCH', '/agent/sessions/' + encodeURIComponent(sid), {title: t}).then(function(m){
          if (sess) sess.title = m.title;
          closeMenu(); return loadList();
        }).then(renderHead).catch(function(e){ errMsg = e.message; closeMenu(); render(); });
      }
      ok.addEventListener('click', save);
      inp.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter'){ ev.preventDefault(); save(); }
        if (ev.key === 'Escape'){ ev.stopPropagation(); closeMenu(); }
      });
      box.appendChild(inp); box.appendChild(ok); menu.appendChild(box);
      setTimeout(function(){ inp.focus(); inp.select(); }, 20);
      return;
    }
    if (menuMode === 'delete'){
      var q = document.createElement('div'); q.className = 'ag-mi';
      q.textContent = '“' + ((sess && sess.title) || '이 세션') + '”을(를) 지울까요? 되돌릴 수 없습니다.';
      menu.appendChild(q);
      var yes = document.createElement('button'); yes.className = 'ag-mi ag-mi-del'; yes.textContent = '삭제';
      var no = document.createElement('button'); no.className = 'ag-mi ag-mi-act'; no.textContent = '취소';
      yes.addEventListener('click', function(){
        var gone = sid;
        api('DELETE', '/agent/sessions/' + encodeURIComponent(gone)).then(function(){
          closeMenu(); sid = null; sess = null; lsSet(LS_SESSION, null);
          return ensureSession();
        }).then(renderHead).catch(function(e){ errMsg = e.message; closeMenu(); render(); });
      });
      no.addEventListener('click', function(){ menuMode = 'list'; renderMenu(); });
      menu.appendChild(yes); menu.appendChild(no);
      return;
    }
    sessList.forEach(function(s){
      var b = document.createElement('button');
      b.className = 'ag-mi' + (s.id === sid ? ' on' : '');
      b.innerHTML = '<span class="ag-mi-t">' + esc(s.title || '새 세션') + '</span>'
        + '<span class="ag-mi-s">' + (s.turns ? s.turns + '문 · ' : '') + esc(shortTime(s.updated))
        + '</span>';
      b.addEventListener('click', function(){
        closeMenu();
        if (s.id !== sid) openSession(s.id).then(renderHead)
          .catch(function(e){ errMsg = e.message; render(); });
      });
      menu.appendChild(b);
    });
    if (sessList.length){ var sp = document.createElement('div'); sp.className = 'ag-msep'; menu.appendChild(sp); }
    [['+ 새 세션', 'new'], ['이름 바꾸기', 'rename'], ['이 세션 삭제', 'delete']].forEach(function(a){
      var b = document.createElement('button');
      b.className = 'ag-mi ' + (a[1] === 'delete' ? 'ag-mi-del' : 'ag-mi-act');
      b.textContent = a[0];
      b.disabled = a[1] !== 'new' && !sid;
      b.addEventListener('click', function(){
        if (a[1] === 'new') return newSession();
        menuMode = a[1]; renderMenu();
      });
      menu.appendChild(b);
    });
  }
  sessBtn.addEventListener('click', function(){
    if (busy) return;
    if (menu.classList.contains('open')) return closeMenu();
    menuMode = 'list';
    loadList().catch(function(){}).then(function(){ renderMenu(); menu.classList.add('open'); });
  });
  document.addEventListener('mousedown', function(ev){
    if (menu.classList.contains('open') && !menu.contains(ev.target) && !sessBtn.contains(ev.target))
      closeMenu();
  });

  // ---- 렌더 ----
  var TOOL_KO = {search_paper: '논문 검색', read_rows: '행 읽기', outline: '목차 보기',
    list_note_tabs: '메모 탭 목록', read_notes: '메모 읽기', read_highlights: '하이라이트 읽기',
    list_papers: '논문 목록', list_projects: '컬렉션 목록', get_bibtex: 'BibTeX',
    goto_row: '행으로 이동', open_paper: '논문 열기',
    write_note: '메모 쓰기', add_highlight: '하이라이트 추가',
    set_project_membership: '컬렉션 소속 바꾸기', remember: '기억하기'};
  // 승인 카드의 아이콘 — Material Icons 글꼴(NiceGUI가 싣는다)의 이름
  var TOOL_ICON = {write_note: 'edit_note', add_highlight: 'border_color',
    set_project_membership: 'bookmark_add', remember: 'psychology'};
  function argsText(args){
    if (!args || typeof args !== 'object') return args ? String(args) : '';
    return Object.keys(args).map(function(k){
      var v = args[k]; v = typeof v === 'string' ? v : JSON.stringify(v);
      return k + '=' + (v.length > 60 ? v.slice(0, 60) + '…' : v);
    }).join(', ');
  }
  function toolNode(t){
    var d = document.createElement('div');
    d.className = 'ag-tool';
    d.title = t.name + ' — 눌러서 펼치기/접기';
    d.innerHTML = '🔧 <b>' + esc(TOOL_KO[t.name] || t.name) + '</b>'
      + (t.args ? ': ' + esc(argsText(t.args)) : '')
      + (t.summary ? ' <span class="ag-tres">→ ' + esc(t.summary) + '</span>' : '');
    d.addEventListener('click', function(){ d.classList.toggle('full'); });
    return d;
  }
  function citeOf(turn, chip){
    var cs = turn.citations || [], kind = chip.getAttribute('data-kind');
    var tok = chip.getAttribute('data-token') || '', row = parseInt(chip.getAttribute('data-row'), 10);
    var id = chip.getAttribute('data-id');
    for (var i = 0; i < cs.length; i++){
      var c = cs[i];
      if (kind === 'note' ? (c.kind === 'note' && c.id === id)
                          : (c.kind !== 'note' && c.token === tok && c.row === row)) return c;
    }
    return null;
  }
  function wireChips(a, turn){
    a.querySelectorAll('a.chat-cite').forEach(function(chip){
      var isNote = chip.getAttribute('data-kind') === 'note';
      var tok = chip.getAttribute('data-token') || '';
      var row = parseInt(chip.getAttribute('data-row'), 10) || 0;
      var id = chip.getAttribute('data-id');
      var c = citeOf(turn, chip) || {};
      var here = !!tok && tok === paper && onReview();
      if (!here) chip.classList.add('ag-far');   // 다른 논문(또는 홈) — 누르면 그 논문을 연다
      var tip = isNote
        ? '내 메모 — “' + (c.quote || '').slice(0, 90) + '”'
          + ((c.note || '').trim() ? ' / ' + c.note.replace(/\s+/g, ' ').slice(0, 70) : '')
        : (c.heading ? c.heading + ' — ' : '') + (c.en || '').replace(/\s+/g, ' ').slice(0, 160);
      chip.title = (here ? '' : '다른 논문 — 누르면 열고 그 자리로\n') + tip;
      // 칩은 곧장 뷰어의 그 문단으로 보낸다 — 원문·번역은 거기 이미 나란히 있으므로
      // 서랍 안에 한 번 더 그리지 않는다(툴팁으로 어디로 가는지만 미리 보여준다).
      chip.addEventListener('click', function(ev){
        ev.preventDefault();
        if (tok && tok === paper && onReview()){
          if (isNote) gotoNote(id, row); else gotoRow(row);
          return;
        }
        if (!gotoElsewhere(tok, row, isNote ? id : null, c.url)){
          errMsg = '그 논문의 위치를 찾지 못했습니다.'; render();
        }
      });
    });
  }
  function turnNode(t){
    var wrap = document.createElement('div');
    if (t.role === 'user'){
      wrap.className = 'ag-turn ag-user';
      var q = document.createElement('div');
      q.className = 'ag-q'; q.textContent = t.text || '';
      wrap.appendChild(q);
      apprDone(wrap, t.id);
      return wrap;
    }
    wrap.className = 'ag-turn ag-bot';
    (toolsByTurn[t.id] || []).forEach(function(x){ wrap.appendChild(toolNode(x)); });
    if (t.html || t.text){
      var a = document.createElement('div');
      a.className = 'ag-a';
      if (t.html) a.innerHTML = t.html;     // 서버에서 마크다운 렌더 (raw HTML 비활성)
      else a.textContent = t.text;
      wireChips(a, t);
      wrap.appendChild(a);
    }
    if (t.error){
      var e = document.createElement('div');
      e.className = 'ag-err'; e.textContent = t.error;
      wrap.appendChild(e);
    }
    var bits = [];
    if (t.model) bits.push(esc(t.model));
    if (t.cost_usd) bits.push('$' + Number(t.cost_usd).toFixed(4));
    if (bits.length){
      var meta = document.createElement('div');
      meta.className = 'ag-meta'; meta.innerHTML = bits.join(' · ');
      wrap.appendChild(meta);
    }
    apprDone(wrap, t.id);
    return wrap;
  }
  function apprDone(wrap, id){            // 이 턴 뒤에서 답한 승인 카드들 — 결과로 잠겨 있다
    (apprByTurn[id] || []).forEach(function(rec){ wrap.appendChild(approvalNode(rec, false)); });
  }
  // 승인 카드 — open이면 지금 답을 기다리는 카드(단추가 산다), 아니면 결과(승인됨/거절됨/건너뜀)로 잠긴 카드.
  // 호출마다 [승인]/[거절], 여럿이면 맨 아래 [모두 승인]/[모두 거절]. 서버는 대기 호출 전부에 대한 답을
  // 한 번에 받으므로(일부 거절은 가능) 모든 호출에 답이 모이는 순간 보낸다 — 하나뿐이면 누르는 즉시.
  function apprText(c){
    return c.description || ((TOOL_KO[c.name] || c.name) + (c.args ? ' — ' + argsText(c.args) : ''));
  }
  function apprArgs(args){
    try { return JSON.stringify(args, null, 2); } catch (e) { return String(args); }
  }
  function decide(m){
    if (busy || !pending) return;
    Object.keys(m).forEach(function(k){ decisions[k] = m[k]; });
    var calls = pending.calls || [];
    if (!calls.every(function(x){ return x.id in decisions; })) return render();
    var ap = {}; calls.forEach(function(x){ ap[x.id] = !!decisions[x.id]; });
    send('', ap);
  }
  function approvalNode(rec, open){
    var calls = rec.calls || [];
    var box = document.createElement('div');
    box.className = 'ag-appr' + (open ? '' : ' done');
    var h = document.createElement('div');
    h.className = 'ag-appr-h';
    h.textContent = open ? ('실행해도 될까요?' + (calls.length > 1 ? ' (' + calls.length + '건)' : ''))
      : (rec.skipped ? '건너뜀 — 새 메시지를 보내 실행하지 않았습니다' : '승인 결과');
    box.appendChild(h);
    calls.forEach(function(c){
      var row = document.createElement('div'); row.className = 'ag-appr-c';
      var top = document.createElement('div'); top.className = 'ag-appr-r';
      var ic = document.createElement('i');
      ic.className = 'material-icons ag-appr-i'; ic.setAttribute('aria-hidden', 'true');
      ic.textContent = TOOL_ICON[c.name] || 'build';
      top.appendChild(ic);
      var d = document.createElement('span'); d.className = 'ag-appr-d'; d.textContent = apprText(c);
      d.title = c.name;
      top.appendChild(d);
      var hasArgs = c.args && typeof c.args === 'object' && Object.keys(c.args).length;
      if (hasArgs){                          // 무엇을 넘기는지 — 접어 두고 필요할 때만 펼친다
        var tg = document.createElement('button');
        tg.type = 'button'; tg.className = 'ag-appr-tg'; tg.textContent = '자세히';
        tg.title = '넘길 인자 펼치기/접기';
        tg.addEventListener('click', function(){ row.classList.toggle('open'); });
        top.appendChild(tg);
      }
      if (open){
        [['승인', true, 'ag-appr-ok'], ['거절', false, 'ag-appr-no']].forEach(function(o){
          var b = document.createElement('button');
          b.type = 'button'; b.textContent = o[0];
          b.className = o[2] + (decisions[c.id] === o[1] ? ' on' : '');
          b.disabled = busy;
          b.addEventListener('click', function(){ var m = {}; m[c.id] = o[1]; decide(m); });
          top.appendChild(b);
        });
      } else {
        var st = document.createElement('span');
        var ok = !!(rec.decisions || {})[c.id];
        st.className = 'ag-appr-s ' + (rec.skipped ? 'skip' : (ok ? 'ok' : 'no'));
        st.textContent = rec.skipped ? '건너뜀' : (ok ? '승인됨' : '거절됨');
        top.appendChild(st);
      }
      row.appendChild(top);
      if (hasArgs){
        var pre = document.createElement('pre'); pre.className = 'ag-appr-args';
        pre.textContent = apprArgs(c.args);
        row.appendChild(pre);
      }
      box.appendChild(row);
    });
    if (open && calls.length > 1){
      var all = document.createElement('div'); all.className = 'ag-appr-all';
      [['모두 승인', true], ['모두 거절', false]].forEach(function(o){
        var b = document.createElement('button');
        b.type = 'button'; b.textContent = o[0]; b.disabled = busy;
        b.className = o[1] ? 'ag-appr-allok' : 'ag-appr-allno';
        b.addEventListener('click', function(){
          var m = {}; calls.forEach(function(x){ m[x.id] = o[1]; }); decide(m);
        });
        all.appendChild(b);
      });
      box.appendChild(all);
    }
    return box;
  }
  // 비용 상한 경고 — 턴을 시작하기 전에 서버가 막았다(400 code=cost_cap)
  function capNode(){
    var box = document.createElement('div'); box.className = 'ag-cap';
    var t = document.createElement('span'); t.className = 'ag-cap-t';
    t.textContent = capRetry.msg || '이 세션의 비용 상한을 넘었습니다.';
    var go = document.createElement('button');
    go.type = 'button'; go.className = 'ag-cap-go'; go.textContent = '그래도 계속';
    go.title = '이번 요청만 상한을 넘겨 보냅니다';
    go.disabled = busy;
    go.addEventListener('click', function(){ var r = capRetry; if (r) send(null, null, r.body); });
    var no = document.createElement('button');
    no.type = 'button'; no.className = 'ag-cap-x'; no.textContent = '그만두기';
    no.addEventListener('click', function(){ capRetry = null; render(); });
    box.appendChild(t); box.appendChild(go); box.appendChild(no);
    return box;
  }
  var liveText = null, liveTools = null;
  function liveNode(){
    var frag = document.createDocumentFragment();
    if (live.q){
      var u = document.createElement('div'); u.className = 'ag-turn ag-user';
      var q = document.createElement('div'); q.className = 'ag-q'; q.textContent = live.q;
      u.appendChild(q); frag.appendChild(u);
    }
    var w = document.createElement('div'); w.className = 'ag-turn ag-bot';
    liveTools = document.createElement('div');
    live.tools.forEach(function(t){ liveTools.appendChild(toolNode(t)); });
    w.appendChild(liveTools);
    liveText = document.createElement('div');
    liveText.className = 'ag-a ag-live';
    liveText.textContent = live.text.replace(/^\s+/, '');
    w.appendChild(liveText);
    if (!live.text){
      var wt = document.createElement('div'); wt.className = 'ag-wait';
      wt.innerHTML = '<span class="ag-dots"><span></span><span></span><span></span></span>'
        + (live.tools.length ? '자료를 읽는 중…' : '생각하는 중…');
      w.appendChild(wt);
    }
    if (live.err){
      var e = document.createElement('div'); e.className = 'ag-err'; e.textContent = live.err;
      w.appendChild(e);
    }
    frag.appendChild(w);
    return frag;
  }
  function nearBottom(){ return list.scrollHeight - list.scrollTop - list.clientHeight < 80; }
  function renderHead(){
    if (sess && !sess.title)                  // 첫 질문이 제목이 된다 — 목록에서 받아 온다
      for (var i = 0; i < sessList.length; i++) if (sessList[i].id === sess.id) sess.title = sessList[i].title;
    sessT.textContent = (sess && sess.title) || '새 세션';
    sessBtn.disabled = busy; newBtn.disabled = busy;
    var c = sess && sess.usage ? Number(sess.usage.cost_usd || 0) : 0;
    var cap = sess && sess.cost_cap ? Number(sess.cost_cap) : 0;     // 세션 비용 상한(USD) — 있으면 함께
    costEl.textContent = '$' + c.toFixed(4) + (cap ? ' / $' + cap.toFixed(2) : '');
    costEl.classList.toggle('over', !!cap && c >= cap);
    costEl.title = '이 세션에서 쓴 비용 (누적)' + (cap ? ' / 세션 상한 — 넘으면 다음 질문 전에 묻습니다' : '');
  }
  function render(){
    renderHead();
    renderModels();
    list.innerHTML = '';
    if (!modelInfo.ready){
      var note = document.createElement('div');
      note.className = 'ag-err';
      note.textContent = (modelInfo.reason || 'AI 키가 설정되지 않았습니다.')
        + ' 홈 → 설정에서 키를 넣으세요.';
      list.appendChild(note);
    }
    if (!turns.length && !live && modelInfo.ready){
      var em = document.createElement('div');
      em.className = 'ag-empty';
      em.textContent = (paper && onReview()
        ? '열린 논문과 라이브러리의 다른 논문을 읽고 답합니다.\n'
          + '예: "이 논문의 방법 섹션 핵심은?", "선택한 문장 쉽게 풀어 줘", "3번째 섹션으로 가 줘"'
        : '라이브러리의 논문을 찾아 읽고 답합니다.\n'
          + '예: "이 라이브러리에 논문 몇 편 있어?", "튜터링 관련 논문 열어 줘"')
        + '\n답 속 숫자 칩을 누르면 근거 문단(노란 칩은 메모)으로 이동합니다.';
      list.appendChild(em);
    }
    turns.forEach(function(t){ list.appendChild(turnNode(t)); });
    if (live) list.appendChild(liveNode());
    else { liveText = null; liveTools = null; }
    if (pending) list.appendChild(approvalNode(pending, true));   // 진행 중 말풍선 아래에
    if (capRetry) list.appendChild(capNode());
    if (errMsg){
      var er = document.createElement('div');
      er.className = 'ag-err'; er.textContent = errMsg;
      list.appendChild(er);
    }
    // 승인 대기 중에도 입력창은 연다 — 그냥 보내면 서버가 대기 호출을 모두 거절하고 이어 간다(건너뜀)
    input.disabled = busy || !modelInfo.ready;
    input.placeholder = pending ? PH_WAIT : PH_IDLE;
    sendBtn.disabled = input.disabled;
    list.scrollTop = list.scrollHeight;
  }

  // ---- 질문 → SSE 스트림 ----
  function readSSE(resp, on){
    var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
    function pump(){
      return reader.read().then(function(r){
        if (r.value) buf += dec.decode(r.value, {stream: true});
        var i;
        while ((i = buf.indexOf('\n\n')) >= 0){
          var block = buf.slice(0, i); buf = buf.slice(i + 2);
          var ev = 'message', data = '';
          block.split('\n').forEach(function(line){
            if (line.indexOf('event: ') === 0) ev = line.slice(7).trim();
            else if (line.indexOf('data: ') === 0) data += line.slice(6);
          });
          var obj = {};
          try { obj = data ? JSON.parse(data) : {}; } catch (e) {}
          on(ev, obj);
        }
        if (!r.done) return pump();
      });
    }
    return pump();
  }
  function doAction(a){
    if (!a || !a.kind) return;
    if (a.kind === 'goto_row'){
      if (a.token && a.token === paper && onReview()){ gotoRow(a.row); return; }
      gotoElsewhere(a.token, a.row, null, a.url);
    } else if (a.kind === 'open_paper'){
      if (a.token && a.token === paper && onReview()) return;   // 이미 그 논문
      nav(a.url);
    } else if (a.kind === 'refresh'){
      refreshView(a.what, a.token);
    }
  }
  // 쓰기 tool이 파일을 고친 뒤 — 지금 화면이 그것을 보여 주고 있으면 다시 그린다.
  // 메모·하이라이트는 지금 뷰어의 논문일 때만(다른 논문이면 열 때 새로 읽으므로 할 일이 없다).
  function refreshView(what, tok){
    if (what === 'library'){
      // 홈의 목록·컬렉션 칩은 서버가 짓는다 — 같은 홈 주소로 다시 가게 해 본문만 새로 짓는다
      if (!onReview()) nav('/home');
      return;
    }
    if (!tok || tok !== paper || !onReview()) return;
    if (what === 'notes'){
      // 메모 패널(app.py notes_panel)이 파일을 다시 읽는다 — 고치던 탭이면 기존 '바깥에서 바뀜' 안내를 탄다
      if (typeof emitEvent === 'function') emitEvent('md4-notes-refresh', {token: tok});
    } else if (what === 'highlights'){
      fetch('/annotations/' + encodeURIComponent(tok)).then(function(r){ return r.json(); })
        .then(function(j){
          if (tok === paper && window.__mdAnnoReload) window.__mdAnnoReload(tok, j.items || []);
        }).catch(function(){});
    }
  }
  function onEvent(name, d){
    if (!live) return;
    if (name === 'text'){
      var stick = nearBottom(), first = !live.text;
      live.text += d.delta || '';
      if (first || !liveText) render();        // 첫 조각 — '생각하는 중…'을 걷어 낸다
      else {
        liveText.textContent = live.text.replace(/^\s+/, '');      // 조각마다 전체를 다시 그리지 않는다
        if (stick) list.scrollTop = list.scrollHeight;
      }
    } else if (name === 'tool_call'){
      live.tools.push({id: d.id, name: d.name, args: d.args, summary: ''});
      render();
    } else if (name === 'tool_result'){
      live.tools.forEach(function(t){ if (t.id === d.id) t.summary = d.summary || ''; });
      render();
    } else if (name === 'ui_action'){
      doAction(d);
    } else if (name === 'approval_needed'){
      pending = d; decisions = {};
      render();                              // 진행 중 말풍선 아래에 카드 (done까지는 단추가 잠겨 있다)
    } else if (name === 'done'){
      live.done = d;
    } else if (name === 'error'){
      live.err = d.message || '답을 만들지 못했습니다.';
    }
  }
  function lastTurnId(){ return turns.length ? turns[turns.length - 1].id : null; }
  // again: 비용 상한에 막혔던 요청 본문 — override를 더해 그대로 다시 보낸다
  function send(text, approvals, again){
    if (busy || !sid) return;
    var q, body;
    if (again){
      body = again; body.override = true; body.context = ctxNow();
      q = body.message || ''; approvals = body.approvals || null;
      if (q && input.value.trim() === q){ input.value = ''; autosize(); }
    } else {
      q = (text || '').trim();
      if (!q && !approvals) return;
      body = {message: q, context: ctxNow()};
      if (approvals) body.approvals = approvals;
      if (q){ input.value = ''; autosize(); lastSel = null; paintSel(); }
    }
    capRetry = null;
    // 대기 중이던 승인 카드를 잠근다 — 승인/거절을 보냈거나, 그냥 새 질문을 보내 건너뛰었다.
    // 잠긴 카드는 마지막 턴(그 승인을 부른 질문) 뒤에 남는다. 스트림 전에 거절되면 되돌린다.
    var rec = null, key = null, was = pending;
    if (pending){
      rec = {calls: pending.calls || [], decisions: approvals || {}, skipped: !approvals};
      key = lastTurnId() || '-';
      (apprByTurn[key] = apprByTurn[key] || []).push(rec);
      pending = null; decisions = {};
    }
    busy = true; errMsg = null; closeMenu();
    live = {q: q, text: '', tools: [], err: null, done: null, started: false};
    render();
    var at = sid;
    fetch('/agent/sessions/' + encodeURIComponent(at) + '/turns', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function(r){
        if (!r.ok) return r.json().then(function(j){
          var er = new Error(j.error || j.detail || ('HTTP ' + r.status));
          er.code = j.code || ''; throw er;
        }, function(){ throw new Error('HTTP ' + r.status); });
        live.started = true;
        return readSSE(r, onEvent);
      })
      .catch(function(e){
        if (live && !live.started){            // 스트림 전 거절(키 없음·409·비용 상한 등) — 되돌려 놓는다
          var m = e.message || String(e);
          if (q && !input.value) input.value = q;
          if (rec){                            // 카드는 아직 답을 기다린다
            var arr = apprByTurn[key] || [], k = arr.indexOf(rec);
            if (k >= 0) arr.splice(k, 1);
            pending = was;
          }
          if (e.code === 'cost_cap'){          // 경고 줄 + '그래도 계속' — 같은 본문을 override로 다시
            delete body.override;
            capRetry = {body: body, msg: m};
          } else {
            errMsg = m;
            if (approvals)                     // 승인 카드를 서버 기록으로 맞춘다 (대기 목록은 그대로)
              refreshSession().then(function(){ errMsg = m; render(); });
          }
          live = null;
        } else if (live) live.err = live.err || ('연결이 끊겼습니다: ' + (e.message || e));
      })
      .then(function(){
        var L = live;
        busy = false;
        if (!L){ render(); return; }
        if (L.done && !L.done.pending && L.done.turn_id){
          // 서버가 저장한 것과 같은 모양으로 붙인다 (다시 읽지 않아도 칩·비용이 그대로)
          if (L.q) turns.push({id: 'local-' + Date.now(), role: 'user', text: L.q, html: ''});
          toolsByTurn[L.done.turn_id] = L.tools;
          turns.push({id: L.done.turn_id, role: 'assistant', text: L.text, html: L.done.html,
                      citations: L.done.citations || [], cost_usd: L.done.cost_usd,
                      model: modelInfo.model                // 서버 model_label과 같은 '제공사:모델'
                        ? (modelInfo.provider ? modelInfo.provider + ':' : '') + modelInfo.model : ''});
          if (sess){ sess.usage = sess.usage || {}; sess.usage.cost_usd = L.done.session_cost_usd; }
          live = null;
          render();
          loadList().then(renderHead).catch(function(){});   // 첫 질문이 세션 제목이 된다
          return;
        }
        if (L.done && L.done.pending){        // 승인 대기 — 서버 기록(질문 턴·대기 목록)을 다시 읽는다
          refreshSession();
          return;
        }
        if (L.err && at === sid){            // 서버가 오류 턴을 남겼다 — 기록을 다시 읽어 맞춘다
          var msg = L.err;
          refreshSession().then(function(){
            var last = turns[turns.length - 1];
            if (!last || !last.error) { errMsg = msg; render(); }
          });
          return;
        }
        live = null; render();
      });
  }
  function ask(){ send(input.value, null); }
  function autosize(){
    input.style.height = 'auto';
    input.style.height = Math.min(120, Math.max(30, input.scrollHeight)) + 'px';
  }

  // ---- 열기/닫기 (메모 서랍과 배타) ----
  // 'open'은 여러 경로에서 붙었다 뗀다(버튼·✕·Esc·메모 서랍). 한 곳에서 관찰해 body 클래스를
  // 맞춰야 본문 자리 비우기가 어느 경로로 닫아도 따라온다.
  function syncBody(){
    var on = panel.classList.contains('open');
    document.body.classList.toggle('ag-open', on);
    lsSet(LS_OPEN, on ? '1' : null);
    syncLayout();
  }
  // 서랍 위끝을 헤더 아래에 맞춘다 (메모 서랍이 여는 순간 하는 것과 같은 계산).
  function syncTop(){
    var h = document.querySelector('.q-header');
    var top = h ? Math.max(0, h.getBoundingClientRect().bottom) : 0;
    document.documentElement.style.setProperty('--ag-top', top + 'px');
    return top;
  }
  // 첫 로드에 열린 채로 뜨면(지난번에 열어 두었음) 헤더가 아직 안 그려져 0을 잰다 → 헤더가
  // 자리를 잡을 때까지 몇 프레임 다시 잰다 (안 그러면 서랍이 헤더를 덮는다)
  function syncTopSoon(){
    var n = 0;
    (function again(){ if (!syncTop() && ++n < 120) requestAnimationFrame(again); })();
  }
  window.addEventListener('resize', function(){ syncTop(); syncLayout(); });
  syncTop();
  if (window.MutationObserver)
    new MutationObserver(syncBody).observe(panel, {attributes: true, attributeFilter: ['class']});

  // 너비 — 사용자가 끈 폭(agWant, localStorage)과 실제로 쓰는 폭(--ag-w)을 나눈다. 실제 폭은 열 때·
  // 창 크기가 바뀔 때·끌 때마다 뷰포트에 맞춰 다시 잰다(syncLayout). 창을 잠깐 줄였다고 기억한
  // 폭까지 줄어들면 안 되므로 localStorage는 드래그만 쓴다.
  // 값은 파이썬 AG_* 상수와 한 짝이다(테스트가 대조한다).
  var AG_MIN = 320, AG_MAX = 760, AG_KEEP = 360;
  var AG_OVERLAY_BELOW = 760, AG_NARROW_BELOW = 1100, AG_SLIM_BELOW = 380;
  var agWant = 400;
  function agCap(){          // 지금 뷰포트에서 서랍이 가질 수 있는 최대 폭 — 본문 몫 AG_KEEP는 남긴다
    return Math.min(AG_MAX, Math.max(AG_MIN, window.innerWidth - AG_KEEP));
  }
  // 서랍 폭과 body 모드 클래스를 한 계산에서 정한다:
  //   ag-overlay — 남는 본문 폭 < AG_OVERLAY_BELOW → 밀지 않고 덮는다(본문 padding 0)
  //   ag-narrow — 밀기 모드에서 남는 폭이 모자라 홈 미리보기 열을 접는다
  //   (오버레이일 땐 본문이 제 폭이라 접을 까닭이 없다 — 붙이면 넓은 홈에서 미리보기만 사라진다)
  //   ag-slim(서랍에) — 서랍 자체가 좁아 머리줄을 줄인다
  function syncLayout(){
    var vw = window.innerWidth;
    var w = Math.min(vw, agWant, agCap());
    document.documentElement.style.setProperty('--ag-w', w + 'px');
    var on = panel.classList.contains('open'), rest = vw - w;
    var over = on && rest < AG_OVERLAY_BELOW;
    var cl = document.body.classList;
    cl.toggle('ag-overlay', over);
    cl.toggle('ag-narrow', on && !over && rest < AG_NARROW_BELOW);
    panel.classList.toggle('ag-slim', w < AG_SLIM_BELOW);
  }
  function setWidth(px){     // 드래그 — 지금 뷰포트 상한까지만 받고, 받은 값을 기억한다
    agWant = Math.max(AG_MIN, Math.min(agCap(), Math.round(px)));
    lsSet(LS_W, String(agWant));
    syncLayout();
  }
  var w0 = parseInt(lsGet(LS_W), 10);
  if (w0) agWant = Math.max(AG_MIN, Math.min(AG_MAX, w0));
  var agDrag = false;
  panel.querySelector('.ag-handle').addEventListener('mousedown', function(ev){
    agDrag = true; ev.preventDefault(); document.body.style.userSelect = 'none';
  });
  window.addEventListener('mousemove', function(ev){
    if (agDrag) setWidth(window.innerWidth - ev.clientX);
  });
  window.addEventListener('mouseup', function(){
    if (agDrag){ agDrag = false; document.body.style.userSelect = ''; }
  });

  // 메모 서랍은 뷰어가 지을 때 생긴다(홈에서 시작하면 아직 없다) → 논문이 바뀔 때마다 다시 붙는다
  var annoSeen = null;
  function watchAnno(){
    var ap = document.getElementById('md-anno-panel');
    if (!ap || ap === annoSeen || !window.MutationObserver) return;
    annoSeen = ap;
    new MutationObserver(function(){             // 메모 서랍이 열리면 에이전트 창은 닫는다
      if (ap.classList.contains('open')) panel.classList.remove('open');
    }).observe(ap, {attributes: true, attributeFilter: ['class']});
  }
  function openPanel(){
    syncTopSoon();
    var ap = document.getElementById('md-anno-panel');
    if (ap) ap.classList.remove('open');          // 없을 수도 있다 (홈)
    panel.classList.add('open');
    if (!loaded){ loadModels(); ensureSession(); } else render();
    setTimeout(function(){ if (!input.disabled) input.focus(); }, 60);
  }
  function closePanel(){ panel.classList.remove('open'); closeMenu(); }
  window.__md4Agent = {
    toggle: function(){ if (panel.classList.contains('open')) closePanel(); else openPanel(); },
    open: openPanel, close: closePanel,
    // 화면이 바뀔 때 뷰어·홈이 부른다 — 창·대화·스트림은 그대로, 지금 논문만 바꾼다
    setPaper: function(tok){
      var prev = paper;
      paper = tok || null;
      window.__md4AgentTok = paper;
      if (prev !== paper) jbDismiss();           // 핀은 그 논문의 자리였다
      watchAnno(); syncTopSoon();
      consumeGoto();
      if (panel.classList.contains('open') && !live) render();   // 칩의 '다른 논문' 표시를 다시
    },
    context: ctxNow
  };
  panel.querySelector('.ag-x').addEventListener('click', closePanel);
  newBtn.addEventListener('click', newSession);
  sendBtn.addEventListener('click', ask);
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function(ev){
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing){ ev.preventDefault(); ask(); }
  });
  document.addEventListener('keydown', function(ev){
    if (ev.key !== 'Escape') return;
    if (menu.classList.contains('open')){ closeMenu(); return; }
    // 알약이 있으면 알약만 닫는다 — Esc 한 번에 알약과 서랍이 동시에 사라지지 않게.
    // 단 그림 확대·메모 카드·찾기 바·대화상자가 열려 있으면 Esc의 주인은 그쪽이라 비켜선다.
    if (document.querySelector('#md-img-zoom.open, #md-anno-pop.open, #md4-find.on, .q-dialog')) return;
    if (pin){ jbDismiss(); return; }
    if (panel.classList.contains('open')) panel.classList.remove('open');
  });
  var wasOpen = lsGet(LS_OPEN) === '1';        // syncBody가 지우기 전에 읽는다
  watchAnno();
  syncBody();
  render();
  if (wasOpen) openPanel();                    // 다시 켜도 열어 두었던 창은 연 채로
})();
"""


def init_js(token: str | None) -> str:
    """화면(뷰어·홈)이 지어질 때 창에 지금 논문을 알리는 JS — 홈이면 None.

    창 스크립트보다 먼저 돌 수 있으므로(첫 화면) 전역에도 남겨 두고, 창이 뜰 때 그것을 읽는다.
    """
    tok = json.dumps(token).replace("<", "\\u003c")
    return (f"window.__md4AgentTok = {tok};"
            f"window.__md4Agent && window.__md4Agent.setPaper({tok});")


def _nav(url: str) -> None:
    """JS가 부탁한 화면 이동 — 앱 안의 두 화면 주소만 받는다(바깥 주소로 끌려가지 않게)."""
    from nicegui import ui

    if isinstance(url, str) and (url.startswith("/review?wd=") or url == "/home"):
        ui.navigate.to(url)


def install(state: dict | None = None) -> None:  # noqa: ARG001 — 셸 설치 훅들과 같은 꼴
    """셸 페이지가 한 번 부른다 — 창 CSS·마크업·스크립트와 화면 이동 이벤트.

    셸에 붙으므로 홈 ↔ 뷰어를 오가도(본문만 갈아 끼움) 창은 다시 지어지지 않는다.
    """
    from nicegui import ui

    from md4paper.ui import spa

    spa.add_css(CSS)
    spa.add_body_html(HTML)
    spa.add_body_html(f"<script>{JS}</script>")
    ui.on(NAV_EVENT, lambda e: _nav((e.args or {}).get("url", "")))

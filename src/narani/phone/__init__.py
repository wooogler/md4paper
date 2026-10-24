"""폰 리더 — 데스크톱이 논문을 '리더 번들'로 굳혀 사용자의 저장소에 올리고, 폰은 정적 웹앱(PWA)으로 읽는다.

서버를 두지 않는다. 데스크톱(Mac·Windows)이 꺼져 있어도 폰에서 읽히고, 논문·메모는 사용자 각자의
저장소(Google Drive 앱 폴더, 시험용으로는 로컬 폴더)에만 있다. LLM 키는 데스크톱 밖으로 나가지 않는다.

  bundle — 논문 한 편 → 번들 JSON (모바일 HTML(§mobile)과 같은 렌더), 목록(index.json)
  store  — 저장소 어댑터: 앱 폴더 안의 이름 → 바이트 (read / write / delete)
  sync   — 고른 논문들을 저장소에 올린다 (바뀐 논문만)
  web    — 폰 리더 웹앱 파일 (index.html·app.js·sw.js·manifest) + 시험용 로컬 서버

저장소 안의 모양:
  index.json            — 목록. 마지막에 쓴다(폰이 없는 파일을 가리키는 목록을 보지 않게)
  papers/<file>.json    — 논문 번들 (file = 논문 키의 해시 — 이름에 쓸 수 없는 글자를 피한다)
"""

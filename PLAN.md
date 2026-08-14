# md4paper 개발 계획

논문 PDF → (1) 헤더가 올바르게 정렬된 마크다운 + (2) 한국어 번역 마크다운을 만드는 CLI 도구.

> 이 계획은 2026-07-22 기준 웹 리서치(marker 2.0 현황, 대안 백엔드, citation 파싱, 번역 파이프라인, 리뷰 UX)와
> 3개 독립 아키텍처 제안 → 심사 과정을 거쳐 종합한 것이다.

> **진행 상태 (2026-07-22)**: ✅ M0 (extract→structure→assemble, CLI/doctor, 실제 arXiv PDF 21초 검증) ·
> ✅ M1 (캡션 페어링·`fig-NN` 리네임·뷰어별 이미지 플레이버·한국어 라벨) ·
> ✅ M3 (LLM 3사 어댑터 + citation: keep/authoryear/short·DOI/arXiv 링크·단축명·반환각 검증) ·
> ✅ M4 (번역: Abstract 컨텍스트 주입·문체 선택·용어집(별도 생성/편집)·플레이스홀더 보호·결정론적 구조 검증+재시도·캐시) ·
> ✅ 리뷰 완결 (insert_after: 놓친 헤더 추가) ·
> ✅ M5 v1 (로컬 웹 UI: 섹션 트리 리뷰·설정 패널·마크다운+수식 프리뷰·PDF 대조) ·
> ✅ M5 v2 (용어집 검토 UI: 자동 생성 → 편집 테이블 → 저장 → 번역 실행; 한국어 프리뷰) ·
> ✅ UI 파일 업로드 (`md4paper ui` 워크디렉토리 없이 → 업로드 홈에서 PDF/.md 드롭 → 변환 → 리뷰). pytest 79개 통과. 다음: M6(batch·enrich·Docling).
> 구현 메모: 계획서는 marker JSON 1차 소스였으나 실제 구현은 **raw.md를 척추로 삼아 헤더 재레벨링**(degrade 경로 통합).
> LLM 어댑터·번역은 fake 프로바이더로 키·비용 없이 테스트(실제 API 표면은 설치 SDK로 실증 확정).

---

## 1. 핵심 설계 원칙

- **Everything is a file** — 모든 단계는 작업 디렉토리 위의 재개 가능한(resumable) 순수 함수. 상시 데몬 없음, DB 없음, Docker 없음. 웹 UI는 항상 떠 있는 서버가 아니라 **필요할 때 로컬에서 띄우는 프론트엔드**로, CLI와 같은 파일 아티팩트를 읽고 쓴다.
- **CLI와 UI는 동일한 아티팩트 위의 두 프론트엔드** — 진실원은 `paper.md4/`의 YAML/JSON 파일이다. 웹 UI가 하는 모든 편집은 CLI 라운드트립으로도 가능하고 그 역도 성립. UI는 별도 데이터 모델을 갖지 않는다.
- **Graceful degradation** — 어떤 단계가 실패해도 항상 유효한 출력이 남는다. JSON 추출 실패 → marker 마크다운 그대로 통과; 번호 체계 감지 모호 → marker 레벨 유지 + 리뷰 플래그; citation 검증 실패 → `[12]` 원문 그대로; 번역 청크 검증 2회 실패 → 영어 원문 + 경고 주석.
- **확신 없으면 추측하지 않는다** — 낮은 신뢰도의 헤더 레벨은 조용히 적용하지 않고 manifest에 `needs-review` 표시로 내려보낸다.
- **리뷰는 두 경로** — 에디터 라운드트립(`git rebase -i` 패턴: 주석 달린 `sections.yaml`을 `$EDITOR`로 열고 저장 시 재검증; SSH·헤드리스에서 동작, 비대화형 `--manifest` 재실행 아티팩트가 됨) + 로컬 웹 UI(트리 편집·프리뷰·PDF 대조). 둘 다 같은 `sections.yaml`을 편집한다.
- **LLM 프로바이더는 교체 가능** (`llm/` 패키지) — 번역·citation 파싱·용어집 추출이 **Anthropic / OpenAI / Gemini** 공통 어댑터를 공유. 사용자가 프로바이더·모델을 선택하고, 실행마다 비용 출력.

## 2. 파이프라인과 작업 디렉토리

```
paper.md4/                      # PDF 옆에 생성 (--out으로 변경 가능)
├── status.json                 # 단계별 입력 해시 → 완료 마커 (재개/무효화), manifest 승인 해시
├── extract/
│   ├── marker.json             # marker 블록 트리 (block_type, polygon, section_hierarchy, TOC)
│   ├── raw.md                  # marker 마크다운 (degrade 경로)
│   ├── images/                 # 추출 이미지
│   └── meta.json               # marker 버전, 모드, 소요시간
├── structure/
│   ├── sections.yaml           # ★ 사람이 편집하는 manifest (리뷰 대상)
│   └── blocks.json             # 순서 있는 본문 블록 + figure/caption 쌍 (렌더 입력)
├── cite/
│   ├── references.json         # 파싱된 참고문헌 항목
│   └── links.json              # 본문 마커 매치/판정/치환 감사 로그
├── translate/
│   ├── glossary.yaml           # 용어집 (사람이 편집 가능)
│   └── cache.json              # 섹션 내용해시 → 번역 캐시 (수정 후 재실행 시 변경분만 재번역)
└── out/
    ├── paper.en.md             # 결과물 #1
    ├── paper.ko.md             # 결과물 #2
    ├── images/                 # fig-03.jpeg 등 안정적 이름으로 리네임된 자산
    └── sections.map.json       # 섹션 id → 출력 라인 범위 + 내용 해시 (렌더러↔번역기 계약)
```

> API 키·기본 프로바이더·기본 모델은 작업 디렉토리가 아니라 전역 `~/.config/md4paper/config.toml`(권한 0600)에 둔다.
> `paper.md4/`에는 키를 절대 쓰지 않는다(작업 디렉토리는 공유·삭제 대상). Stage 5~6이 참조하는 문서 컨텍스트(제목/초록/용어집)만 작업 디렉토리에 남는다.

### Stage 1 — extract (marker 래핑)
- `marker-pdf==2.0.0` 고정(2026-07-20 출시 — 겨우 이틀 됨). M0에서 스모크 테스트; 문제가 있으면 `1.10.2` 폴백 핀(분기는 `marker_backend.py` 안에만).
- **서브프로세스로 격리 실행** — marker 2는 추론 서버(vLLM/llama.cpp)를 자동 스폰하므로, 서브프로세스 종료 시 서버도 죽어 데몬이 남지 않는다. 크래시도 CLI 프로세스로부터 격리.
- 기본 모드: **`fast --disable_ocr`** (born-digital arXiv PDF는 CPU에서도 71~83% 정확도, 추론 서버 불필요). 스캔 PDF용 `--mode balanced`는 opt-in (llama.cpp 필요, M5에서 문서화).
- 추출 시 pypdf로 페이지별 텍스트 레이어 커버리지를 스니핑 → born-digital/스캔 자동 판별, 스캔본이면 balanced 모드 필요 경고를 **30페이지 OCR 돌리기 전에** 낸다.
- `output_format=json` + markdown 동시 요청. JSON 실패 시 raw.md만으로 진행(헤더는 ATX 라인 파싱; polygon 기반 캡션 페어링만 상실).

### Stage 2 — structure (순수 Python, LLM 없음 — **이 프로젝트의 핵심 가치**)
marker의 마크다운 헤더 레벨은 **글자 크기 k-means 클러스터링**으로 추정된 것이라 논문에서 자주 틀린다(번호 체계 인식 없음). 여기서 재정규화한다:
- SectionHeader 블록 + `metadata.table_of_contents`를 합쳐 헤더 후보 목록 생성.
- `numbering.py`: 앵커드 정규식으로 분류 — dotted Arabic(`^\d+(\.\d+)*\.?\s`), plain Arabic, Roman(`^[IVXLC]+[\.\)]\s`), 부록 문자(`^[A-Z](\.\d+)*\s`), 무번호 섹션 테이블(Abstract/References/Acknowledgments/Appendix → 레벨 1).
- 후보 전체에 걸쳐 일관된 체계가 다수결로 이기면 **레벨 = 번호 깊이**; marker의 k-means 레벨은 무번호·비키워드 헤더의 타이브레이크로만 사용하고 `marker_level`로 병기 기록.
- 다수결이 근소하면 적용하지 않고 manifest에 신뢰도 주석 + `needs-review` 표시.
- `captions.py`: Figure/Table 블록 ↔ Caption 블록을 블록 인접성 + polygon 근접도 + 라벨 정규식(`(Figure|Fig\.|Table)\s+([0-9IVX]+)`)으로 페어링. 페어링된 캡션 기준으로 자산 파일명을 `fig-03.jpeg`/`tab-02.jpeg`처럼 안정화(재실행해도 diff가 안정적).

### Stage 3 — review (선택; `--yes`로 생략)
- `click.edit(require_save=True, extension='.yaml')`로 `$EDITOR` 오픈 → 저장 시 ruamel.yaml(주석 보존)로 재파싱 → pydantic 검증 → 오류는 해당 라인 위 주석으로 삽입해 재오픈(rebase -i 계약; 저장 없이 종료 = 깨끗한 중단).
- manifest 예시:

```yaml
# level: 1-6 | skip(헤더 해제·본문 유지) | merge-up(위에 흡수) | drop(섹션 통째 제거)
# 누락된 헤더는 inserts 항목으로 추가
title: "Attention Is All You Need"
citation_style: keep        # keep([n]) | authoryear([저자 연도]) | short([단축명])
reference_links: true       # 참고문헌을 DOI/arXiv 링크로 (바로 논문 접근)
figure_label: keep          # keep | korean  (Figure→그림, Table→표)
flavor: standard            # standard | obsidian | notion | html  (이미지 임베딩 방식 — 뷰어별)
korean_style: 해라체         # 해라체 | 합니다체 | 해요체 | custom: "<프롬프트 조각>"  (번역 문체)
translate_headers: true     # false면 섹션 제목은 영어로 유지(본문만 번역)
sections:
  - id: h_0007
    text: "3.1 Encoder and Decoder Stacks"
    page: 3
    detected: {scheme: dotted-arabic, number: "3.1"}
    marker_level: 2          # marker가 추정했던 레벨 (참고용)
    level: 2                 # ← 사용자가 고치는 값
    # ↳ "The encoder is composed of a stack of N = 6 identical layers. Each..."
```

- **컨텍스트 스니펫 주석**: 각 헤더 항목 아래 뒤따르는 본문 첫 ~10단어를 주석으로 넣어 '블라인드 YAML 편집' 문제를 완화.
- **`insert_after` 연산**: marker가 헤더를 통째로 놓친 경우(알려진 실패 모드) 사용자가 manifest에서 직접 추가할 수 있다 — level/skip/merge-up만으로는 못 고치는 케이스.
- 승인 시 `sections.yaml`의 해시를 `status.json`에 기록 — 승인 후 manifest를 고치면 하류 단계가 자동 무효화된다(조용한 불일치 방지).
- 같은 `sections.yaml`을 편집하는 **로컬 웹 UI**(Stage 7)가 두 번째 리뷰 경로다. 에디터 라운드트립은 항상 유효한 폴백·헤드리스 경로로 유지된다.

### Stage 4 — assemble
`blocks.json` + 승인된 manifest → `out/paper.en.md`: 승인 레벨의 ATX 헤더, 본문은 marker 출력 그대로, 표·`$$` 수식 무변경 통과, References는 원문 블록으로 보존(Stage 5 입력). `sections.map.json` 출력. **여기까지가 결과물 #1이며 항상 실행 가능하다.**
- **그림**: 캡션을 페어링(marker의 `<span id>` 앵커 제거)해 그림에 흡수하고, 이미지 파일을 등장 순서 기반 `fig-NN`/`tab-NN`으로 **안정적 리네임**(재실행해도 diff 안정). marker의 베어 파일명 참조를 `images/fig-NN`으로 고쳐 **깨진 링크를 수정**한다.
- **뷰어별 이미지 플레이버**(`flavor` 설정, 뷰어마다 임베딩 문법이 다름): `standard`(`![라벨](images/fig-01.jpeg)`+이탤릭 캡션) · `obsidian`(`![[images/fig-01.jpeg]]` 위키링크 임베드) · `notion`(전체 캡션을 alt에 — Notion import가 alt를 캡션으로 취급) · `html`(`<figure><img><figcaption>` — 크기 조절 가능). config 기본값 → manifest 오버라이드 → 웹 UI에서 변경.

### Stage 5 — cite ✅ (`md4paper cite WORKDIR [--style keep|authoryear|short] [--no-links]`)
`paper.en.md`를 읽어 References 섹션을 찾고, 파싱·본문 링크·참고문헌 재렌더 후 다시 en.md에 쓴다. 스타일/링크는 **manifest가 권위 소스**(config가 기본값 시드, CLI가 오버라이드, 리뷰/UI로 편집).
1. **파싱**: References 텍스트를 잘라 구조화 출력 1회(`llm.parse()` — 프로바이더 무관, 스키마 `{label, authors[], year, title, short_name, venue, doi?, arxiv_id?, raw}`). **반(反)환각 검증**: `raw`가 References 본문의 실제 부분 문자열이고 year·1저자 성이 raw에 있어야 통과; 기각 항목은 `references.json`에 함께 기록.
2. **링크(결정론적)**: 보호 구간(인라인 코드·`$…$`·`$$…$$`·펜스 코드)을 제외하고 `[\d(,–- \d)*]` 매칭; 범위 전개; **모든 인덱스가 [1, N] 안일 때만** 치환(수학 `[0,1]`·`[10]`(N<10) 오탐 차단); `(` 뒤따름(마크다운 링크)·`!`/`^` 선행 기각. 참고문헌 영역은 치환 제외.
3. **인용 스타일**(선택 가능, `citation_style`): `keep` → `[[12](#ref-12)]` · `authoryear` → `[Vaswani et al. 2017](#ref-12)` · `short` → `[Transformer](#ref-12)`(널리 알려진 별칭, 없으면 author-year 폴백). 다중/범위는 링크 토큰을 묶어 렌더.
4. **References 재렌더**: 각 항목에 `<a id="ref-N"></a>` 앵커 + 제목을 **DOI/arXiv URL로 하이퍼링크**(`reference_links`, DOI 우선 → arXiv) → 클릭 시 바로 논문 접근 + `*(단축명)*` 병기. 모든 매치·판정을 `links.json`에 감사 기록.
5. `--enrich`(미구현, 기본 off 예정): Crossref polite pool → Semantic Scholar로 DOI/메타데이터 보강. 실패해도 파이프라인을 막지 않음.
- Degrade: References 미검출·파싱 실패·의심이면 해당 마커·문서를 그대로 둔다.
- **테스트**: fake 프로바이더로 키·비용 없이 e2e 검증(수학/코드 보존, 3스타일, 앵커·링크, 반환각 기각).

### Stage 6 — translate ✅ (`md4paper translate WORKDIR [--provider ...] [--model ...] [--style ...] [--yes]`)
`out/paper.en.md`(cite 후면 최종 citation 형태 반영) → `out/paper.ko.md`. 문체/프로바이더/모델은 manifest·config·CLI로 선택. **테스트**: fake 프로바이더로 키·비용 없이 e2e(구조 보존, 컨텍스트·용어집 생성, 캐시 재사용·무효화, 용어집 수정 반영, 검증실패→영어통과). 실제 18청크 논문에서 헤더 구조 완전 보존 확인.

0. **문서 컨텍스트 추출 (Abstract 기반 contextual 번역의 핵심)**: manifest에서 제목과 **Abstract 섹션**을 찾아 `translate/context.md`로 저장. 초록은 논문 전체의 주제·기여·핵심 용어를 압축한 유일한 절이므로, **모든 섹션 번역의 시스템 프롬프트에 문서 컨텍스트로 주입**한다. 효과: (a) 다의어 전문용어를 이 논문의 문맥에 맞게 일관 번역(예: "transformer"가 전기 부품이 아니라 신경망 아키텍처), (b) 섹션을 독립적으로 병렬 번역해도 논문 전체 톤·용어가 흔들리지 않음, (c) 초록에서 뽑은 핵심 용어가 용어집 시드가 됨. 초록이 없으면 첫 1~2개 본문 섹션 요약으로 대체(1회 LLM 호출) 후 같은 슬롯에 주입.
1. **청킹**: 헤더 경계 분할 + 작은 섹션 병합(펜스/표/수식 내부는 헤더가 없어 자연히 안 쪼개짐). **플레이스홀더는 펜스 코드·`$$`수식·이미지·마크다운 링크(인용 링크 포함)·raw URL**(`⟦MD4_n⟧`); 인라인 수식 `$…$`·인라인 코드는 인라인 유지(한국어 조사) 후 검증.
2. **용어집 (별도 생성/편집 아티팩트)**: 제목+초록으로 1회 structured-output → `glossary.yaml` `{term, korean, policy}`. **`md4paper glossary`로 따로 생성**하거나 `translate`가 없으면 자동 생성 후 `$EDITOR` 검토(`--yes`로 생략). 웹 UI(M5)는 "자동 생성 → 표시 → 번역어 수정 → 번역"을 이 파일 위에 얹는다. 번역은 파일의 최신 내용을 소비.
3. **번역**: 섹션별 호출(`llm.complete()`; 기본 모델은 §4, 프로바이더별 노브는 어댑터가 처리). **고정 시스템 프롬프트 = 스타일 규칙(선택된 한국어 문체 + 첫 등장 병기 + citation 마커 원문 유지 + 표 셀 `|` 이스케이프) + 용어집 + 제목/초록(문서 컨텍스트)**. 한국어 문체는 `korean_style` 설정으로 프롬프트 조각이 갈아끼워진다(§8, 웹 UI 주요 설정). 이 프리픽스는 전 섹션 공통이라 캐싱 대상: OpenAI(≥1024토큰 자동)·Gemini(암묵 캐싱 기본)는 자동 히트, Anthropic은 `cache_control: ephemeral` 명시. **동기 호출이 기본**, `--batch`는 opt-in(세 프로바이더 모두 50% 할인, 최악 24h — 대량 백로그용).
4. **검증(결정론적, load-bearing)**: 복원 후 원문과 비교 — 헤더 레벨/개수, 이미지·링크 수, 인라인 코드·수식(`$`) 수, 표 파이프(`|`) 수, 플레이스홀더 잔존. 실패 → 위반 인용해 1회 재시도 → 또 실패 → 영어 원문 + `<!-- md4paper: untranslated -->` 주석으로 계속(구조는 확실히 보존).
5. **조립 + 캐시**: 플레이스홀더 복원, 순서대로 스티칭 → `paper.ko.md`. 청크 내용해시 캐시(`cache.json`, 키에 시스템 프롬프트=문체+용어집+컨텍스트 + 모델 → 무엇이든 바뀌면 재번역). 비용(동기, 논문당 대략): **gpt-5.6-luna ~$0.04(기본, 2026-07-30 인하 반영)** / gpt-5.6-terra·gemini-3.1-pro-preview ~$0.4 / claude-sonnet-5 ~$0.5; `--batch`는 M6.

### Stage 7 — 로컬 웹 UI ✅ (`md4paper ui [WORKDIR] [--upload-dir DIR] [--port N] [--no-show]`)
> **파일 업로드**: WORKDIR 없이 실행하면 업로드 홈(`/home`)에서 시작 — PDF/.md를 드롭하면 백그라운드로 convert(`run.io_bound`) 후 `/review`로 이동. WORKDIR을 주면 기존 작업 디렉토리를 바로 연다. 업로드 파일·결과는 `--upload-dir`(기본 cwd)에 `<이름>.md4/`로 저장.

CLI가 만든 아티팩트를 **같은 파일 위에서** 편집하는 로컬 프론트엔드. 항상 떠 있는 서버가 아니라 `ui.run(show=True, reload=False)`로 브라우저를 띄우고, 닫으면 끝. 장기적으로 기능을 계속 얹을 자리다.
- **스택: NiceGUI (≥3.14, MIT)** — 이유는 §4. FastAPI가 밑에 깔려 있어 향후 기능을 평범한 JSON 엔드포인트로 추가 가능하고, 프론트엔드 에셋이 wheel에 번들되어 **Node 툴체인·빌드 스텝 0**(uv에 `nicegui` 의존성만 추가). 마크다운+KaTeX 렌더, 웹소켓 상시 연결로 파일 감시가 쉬움.
- **UI v1 (구현됨)**: `ui/controller.py`(NiceGUI 무관 순수 로직, 테스트됨) + `ui/app.py`(NiceGUI 뷰). `ui.splitter` 좌측 = 문서 설정 패널(**한국어 문체**·인용 스타일·이미지 플레이버·그림 라벨·참고문헌 링크 드롭다운/토글) + 섹션 리스트(레벨 select로 1-6/skip/merge-up, `needs_review` 배지, 컨텍스트 툴팁) + **놓친 헤더 추가 폼**(insert). 우측 탭 = `ui.markdown(latex)` 프리뷰 + pymupdf로 렌더한 **PDF 페이지 대조**(페이지 네비). "저장+재조립" 버튼이 `sections.yaml`을 쓰고 `paper.en.md`를 재생성. 모두 CLI와 같은 아티팩트. 실제 논문에서 서버 렌더 통합 테스트 통과.
- **UI v2 (구현됨) — 용어집 검토**: "용어집" 탭 = **자동 생성(LLM)** 버튼 → term/번역어/policy **편집 테이블**(행 추가·삭제) → **저장**(glossary.yaml) → **이 용어집으로 번역 실행**(백그라운드 `run.io_bound`, 완료 시 알림). "한국어" 탭에서 `paper.ko.md` 프리뷰. LLM 프로바이더 드롭다운(키 없으면 안내). 사용자가 요청한 "번역 전 용어집을 보고 번역어를 바꿀 기회"를 완결.
- **로드맵 v3**: 번역 diff 뷰(문체 바꿔 재번역 비교), 파이프라인 실행 트리거(convert/cite 버튼)·비용 대시보드, `watchfiles` 라이브 갱신. 트리 드래그 재정렬은 NiceGUI 기본 미지원이라 후순위.
- 웹 UI가 늘어나도 **에디터 라운드트립은 폐기하지 않는다** — SSH·헤드리스·CI 경로로 계속 유효. 둘은 같은 스키마를 편집하는 대등한 프론트엔드다.

## 3. CLI

```
md4paper doctor                       # 환경 점검: uv/Python>=3.10, marker import, llama.cpp(balanced 시), 설정된 프로바이더 키
md4paper keys set <anthropic|openai|gemini>   # ~/.config/md4paper/config.toml (0600)에 키 저장; env 변수가 우선
md4paper keys list                    # 설정된 프로바이더/키 존재 여부 표시(값은 마스킹)
md4paper convert PAPER.pdf [--out DIR] [--mode fast|balanced] [--force-ocr] [--flavor standard|obsidian|notion|html] [--review] [--manifest FILE]   # Stage 1-4
md4paper review WORKDIR               # manifest 재오픈($EDITOR) + 재조립
md4paper ui WORKDIR                   # 로컬 웹 UI (NiceGUI) — 섹션 트리 리뷰/프리뷰/PDF 대조
md4paper cite WORKDIR [--style keep|authoryear|short] [--no-links] [--provider ...] [--model ...]   # 미지정 시 manifest 값
md4paper glossary WORKDIR [--provider ...] [--model ...]        # 번역 전 용어집 자동 생성(검토·수정용)
md4paper translate WORKDIR [--provider ...] [--model ...] [--style 문체] [--yes]   # 용어집 생성→편집→번역
md4paper run PAPER.pdf [flags]        # convert → cite → translate, 리뷰 정지 1회
md4paper workspace [PATH]             # 작업 폴더(원본·.md4) 조회/설정
md4paper library [--en|--ko|--pdf DIR] [--off en|ko|pdf|all] [--export]  # 결과 md·PDF가 쌓일 폴더 (§11)
md4paper naming [TEMPLATE] [--apply] [--reset]   # 파일 이름 규칙({year}_{title}_{author}) 조회/설정/일괄 정리 (§11)
```

프로바이더/모델 해석 순서: CLI 플래그(`--provider`/`--model`) → `config.toml`의 기본값 → **내장 기본(openai / `gpt-5.6-luna`)**. 키 조회 순서: env 변수(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`) → `config.toml`. Gemini는 `GOOGLE_API_KEY` 우선순위 함정을 피하려 키를 클라이언트에 **명시적으로** 전달.

## 4. 기술 스택 (선정 이유 포함)

| 선택 | 이유 |
|---|---|
| marker-pdf==2.0.0 (폴백 1.10.2) | 논문 특화 레이아웃 + JSON `section_hierarchy`/TOC 메타데이터 + 이미지·캡션 블록 + LaTeX 수식 + CPU/Apple Silicon 지원을 동시에 갖춘 유일한 도구. 코드 Apache-2.0 / **가중치는 OpenRAIL-M**(연구·개인·$5M 미만 스타트업 무료) — README에 명시 |
| uv + hatchling | 시스템 Python이 3.9.6인데 marker는 >=3.10 필요(로컬 확인 완료). uv가 툴체인을 자체 공급 |
| click 8.4 | `click.edit()`이 리뷰 루프의 핵심 프리미티브. typer는 click을 vendoring하므로 한 겹 덜어냄 |
| ruamel.yaml | 주석 라운드트립 되는 유일한 YAML 라이브러리 — errors-as-comments 편집 루프에 필수 |
| pydantic v2 | manifest 검증 + 모든 JSON 아티팩트 스키마 + 3사 공통 structured-output 스키마 삼역 |
| markdown-it-py | 파서 기반 청킹·구조 diff 검증 (정규식 청킹은 펜스 블록을 깨뜨린 전례 다수) |
| **LLM: 손수 만든 ~150줄 어댑터** over `anthropic` + `openai`(v2, Responses API) + `google-genai`(통합 SDK) | 프로바이더 3개 × 연산 2개(`complete`/`parse`)뿐이라 얇은 Protocol이 정답. 세 SDK 모두 pydantic 모델을 직접 받아 파싱 인스턴스 반환(`messages.parse` / `responses.parse` / `response_schema`), 프로바이더별 노브(캐시·thinking)도 그대로 노출. **LiteLLM 제외**(무거운 의존성 + 2026-03 PyPI 공급망 사고), pydantic-ai는 폴백 후보 |
| 기본 모델 (사용자 선택) | **기본 openai `gpt-5.6-luna`($0.2/$1.2, 2026-07-30 인하 전 $1/$6 — 최저가 티어, 사용자 선호)**; 대안 openai `gpt-5.6-terra`($2/$12, 인하 전 $2.5/$15) · anthropic `claude-haiku-4-5`($1/$5)·`claude-sonnet-5`($3/$15, 2026-08-31까지 인트로 $2/$10)·`claude-opus-5`(고품질, $5/$25) · gemini `gemini-3.5-flash-lite`($0.3/$2.5)·`gemini-3.6-flash`($1.5/$7.5)·`gemini-3.1-pro-preview`($2/$12). EN→KO는 2026 벤치에서 우열이 뚜렷치 않아 사용자 선택으로 둠 |
| 키 관리 | env 변수 우선(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`) → `~/.config/md4paper/config.toml`(0600, `md4paper keys set`). simonw/llm·aider 관행을 따르고 OS keyring은 제외(의존성·헤드리스 마찰). Gemini는 키를 클라이언트에 명시 전달(`GOOGLE_API_KEY` 우선순위 함정 회피) |
| **NiceGUI ≥3.14 (MIT)** | 로컬 웹 UI. FastAPI+Vue/Quasar가 wheel에 번들 → **Node·빌드 스텝 0**, `uv add nicegui`가 패키징 전부. 마크다운+KaTeX 내장, 웹소켓으로 `watchfiles` 파일 감시 간단, FastAPI라서 향후 JSON 엔드포인트 확장 자유. 필요 시 FastAPI+SPA로 탈출로 존재(밑이 FastAPI라 백엔드 코드 보존) |
| pymupdf | 웹 UI의 PDF 페이지→PNG 렌더(원본 대조), extract 단계의 born-digital 스니핑 겸용 |
| httpx + JSON 파일 캐시 | Crossref/Semantic Scholar enrichment (선택) |
| pytest + 골든 코퍼스 | 손으로 라벨링한 arXiv 논문 ~10편; **marker JSON을 녹화한 픽스처**(marker 버전별)로 모델·GPU·API 키 없이 오프라인 테스트. marker 업그레이드 시 의도적으로 재녹화 |

**의도적 제외**: GROBID(Docker/Java 사이드카 — 상시 데몬; 무-LLM citation 백엔드로 미래 옵션만 문서화), LiteLLM(공급망 사고·의존성 무게), Streamlit/Gradio 웹 UI(전체 재실행 모델·데모 지향이라 상태 있는 트리 에디터에 부적합), Textual-web(베타), Docling/MinerU(Backend Protocol로 어댑터 추가 가능 — Docling은 MIT라 marker 라이선스/설치 문제 시 탈출구), refextract(GPL·macOS 빌드 깨짐), anystyle(Ruby), pymupdf4llm(AGPL), BabelDOC 코드(AGPL — 아이디어 참고만).

## 5. 모듈 구조

```
md4paper/
├── cli.py                    # click 커맨드 그룹; 플래그만, 로직 없음
├── doctor.py                 # 환경 점검
├── workdir.py                # 디렉토리 레이아웃, status.json 해시 북키핑, 재개/무효화
├── ir.py                     # pydantic 모델 전부 (파일 스키마의 단일 진실원)
├── config.py                 # ~/.config/md4paper/config.toml 로드, 키/프로바이더/모델 해석 순서
├── library.py                # 결과 마크다운을 쌓을 전역 폴더(영어·한국어 따로) — 복사·이미지 격리 (§11)
├── relayout.py               # 레이아웃 자동 수정 — raw.md를 청크로 나눠 LLM 교정 → 구조 재구축 (§12)
├── llm/
│   ├── base.py               # Protocol: complete(system,user)->str, parse(system,user,schema)->BaseModel; 재시도/비용 집계
│   ├── anthropic.py          # messages.parse, cache_control 브레이크포인트, thinking:disabled
│   ├── openai.py             # responses.parse (Responses API), 자동 프리픽스 캐싱
│   └── gemini.py             # google-genai, response_schema, 키 명시 전달
├── extract/marker_backend.py # marker 서브프로세스 래퍼 + Backend Protocol(~20줄) + degrade
├── structure/numbering.py    # 번호 체계 분류·다수결·레벨 도출 (핵심 IP, 철저 단위테스트)
├── structure/captions.py     # figure/table ↔ caption 페어링, 자산 리네임
├── review/manifest.py        # sections.yaml 방출/편집 루프/검증
├── assemble/render.py        # manifest 적용 → paper.en.md + sections.map.json
├── assemble/figures.py       # 캡션 페어링·fig-NN 리네임·뷰어별 플레이버 렌더
├── cite/{parse,link,render,apply}.py  # parse=구조화+반환각, link=본문 치환, render=DOI/arXiv 링크, apply=오케스트레이션
├── regions.py                # 섹션 영역 찾기 (cite=References, translate=Abstract 공유)
├── translate/{context,chunker,glossary,engine,validate,apply}.py  # context=초록 주입, chunker=플레이스홀더 보호, validate=구조 검증
└── ui/                       # NiceGUI 로컬 앱 — controller.py(순수 로직, 테스트됨) + app.py(뷰)
    └── folder_dialog.py      # OS 기본 폴더 선택 대화상자 (osascript / PowerShell / zenity·kdialog)
```

## 6. 마일스톤 (각각 독립적으로 출시 가능)

- **M0 (1~3일)** — `md4paper doctor` + `md4paper convert` 엔드투엔드: uv 스캐폴드; **marker 2.0.0을 실제 arXiv PDF 3편으로 스모크 테스트**(fast/--disable_ocr; Apple Silicon에서 깨지면 1.10.2 핀); extract 단계; `numbering.py` 재레벨링; 최소 렌더. → **이 시점에 이미 raw marker보다 나은 마크다운.** LLM·API 키 불필요.
- **M1 (1~2주차)** — 결과물 #1 완성: 캡션 페어링 + `fig-NN` 자산 리네임, 무번호 섹션 테이블 강화, raw.md degrade 경로, born-digital 스니핑, 골든 코퍼스 + pytest, status.json 재개 로직.
- **M2 (2주차)** — 인터랙티브 리뷰: manifest 방출(컨텍스트 스니펫·insert_after 포함), click.edit 라운드트립, errors-as-comments, `--yes`/`--manifest` 재생, 승인 해시.
- **M3 (완료)** — LLM 어댑터 + citation: `llm/` 3사 어댑터(complete/parse, 비용 추적, fake) + `config.py`(키/프로바이더 해석) + `md4paper keys`; 그 위에서 citation(References 구조화+반환각 검증, 결정론적 링크/치환 **keep·authoryear·short**, References **DOI/arXiv 하이퍼링크**+단축명, links.json 감사). 실제 SDK 표면 실증, fake로 e2e 테스트. 어댑터가 M4 토대.
- **M4 (완료)** — 번역(결과물 #2): 문서 컨텍스트(제목/초록) 추출·주입 + 청커 + 플레이스홀더 보호, 용어집 별도 생성·편집(`glossary` 명령), 청크별 번역(프로바이더 선택) + 캐시된 시스템 프롬프트, 결정론적 구조 검증 + 재시도 사다리 + 영어 통과 degrade, 내용해시 캐시. fake로 e2e 테스트, 실제 논문 구조 보존 검증.
- **M5 v1 (완료)** — 로컬 웹 UI: NiceGUI(`ui/controller.py`+`ui/app.py`) + `md4paper ui`; 섹션 리스트 리뷰(레벨·skip·merge·insert), 문서 설정 패널(문체·인용·플레이버·라벨·링크), 마크다운+수식(latex2mathml) 프리뷰, pymupdf PDF 페이지 대조, 저장+재조립. 컨트롤러 단위테스트 + 실서버 렌더 통합테스트. 에디터 라운드트립과 대등한 두 번째 리뷰 경로.
- **M6 (6주차~)** — 강화·옵션·UI 확장: `--batch`, `--enrich`, `--mode balanced`(llama.cpp) 문서화, Docling 어댑터, README 라이선스 고지; 웹 UI v2(용어집 편집·번역 diff 뷰)·v3(파이프라인 실행 트리거·비용 표시). 항목별 독립적으로 드랍 가능.

## 7. 주요 리스크

1. **marker 2.0.0이 이틀 됐다** — API 변동·초기 버그·추론 서버 스폰 이슈 가능. → M0 스모크 테스트 게이트, 정확 핀, 서브프로세스 격리, 1.10.2 폴백, fast/--disable_ocr 기본. `--disable_ocr`가 서버 스폰을 실제로 피하는지 M0에서 실증 확인.
2. **marker가 SectionHeader를 아예 못 잡으면** 정규식으로 부활 불가. → manifest `insert_after` + TOC 교차검증 + 리뷰 단계의 존재 이유로 수용.
3. **번호 체계 엣지케이스**(Roman 파트 + Arabic 섹션 혼합, A.1 부록 등). → 실패할 때마다 골든 코퍼스에 추가; 신뢰도를 manifest에 노출, 조용히 적용 금지.
4. **번역 구조 드리프트**. → 결정론적 AST diff 검증기 + 재시도 사다리 + 영어 통과가 방어선; 문체·병기 정책은 사용자 편집 가능 상태 유지.
5. **citation 오탐**(수학 `[0,1]`, 깨진 References 텍스트). → 코드/수식 마스킹, 서지 범위 바운드 체크, raw-substring 검증, 기본 스타일 `keep`(치환은 opt-in), 불확실하면 무조치.
6. **LLM 프로바이더 API 변동** — Sonnet 5 인트로 가격 2026-08-31 종료·한국어 토큰 +30%; OpenAI Responses API/Gemini 스키마 제약(재귀 스키마 불가, 필드 기본값 거부, 속성 순서 미보장)이 프로바이더마다 다름. → 모델 ID를 config에 두고 하드코딩 금지, 비용 실행별 출력, `llm/` 어댑터로 프로바이더별 노브 격리, 스키마는 3사 공통 제약(평면·`additionalProperties:false`·수치 제약 없음)으로 설계.
7. **상용 배포 시 라이선스** — marker 가중치 OpenRAIL-M. → README 고지 + Docling(MIT) 어댑터 탈출구.
8. **웹 UI 범위 크리프** — 프론트엔드가 별도 상태를 갖기 시작하면 CLI와 갈라진다. → UI는 아티팩트 파일만 읽고 쓰는 규율 고정(별도 DB·세션 상태 금지), 모든 UI 편집은 CLI로도 재현 가능해야 함. NiceGUI 트리 드래그 미지원은 v1에서 버튼/메뉴로 우회.

## 8. 열린 질문 (기본값은 정해두었고, 바꾸려면 알려줄 것)

| 질문 | 현재 기본값 |
|---|---|
| 기본 LLM 프로바이더/모델 | **결정됨: openai / `gpt-5.6-luna`**(사용자 선호, 최저가). anthropic·gemini 어댑터도 만들어 `--provider`·config·웹 UI로 전환 가능 |
| citation 기본 스타일 | `keep`([n] + 앵커 링크). 선택지 `authoryear`([저자 연도])·`short`([단축명]). manifest·config·CLI로 변경 |
| 참고문헌 링크 | 기본 on — 제목을 DOI/arXiv URL로 하이퍼링크(바로 논문 접근) + 단축명 병기. `--no-links`/`reference_links:false`로 끔 |
| 한국어 문체 | **선택 가능**(`korean_style`): 해라체(기본, ~한다) / 합니다체(~합니다) / 해요체(~해요) / custom 프롬프트. config.toml 기본값 → sections.yaml 논문별 오버라이드 → **웹 UI 주요 설정**으로 실시간 변경(변경 시 재번역). 프롬프트 조각으로 구현되어 시스템 프롬프트에 갈아끼움 |
| 병기(영문 병기) 정책 | 용어집이 지정한 전문용어 첫 등장 시만 병기 |
| 번역 지연 vs 비용 | 동기 호출 기본(기본 모델 ~$0.04/편), `--batch`(~$0.02, 최악 24h)는 opt-in |
| 출력 위치 | 작업 폴더(`<ws>/<이름>/<이름>.md4/`)가 진실원. **결과 마크다운은 전역 '저장 위치'(영어·한국어 폴더 각각)에 사본으로 쌓는다**(§11) — Obsidian 볼트 등에 바로 꽂는 경로 |
| v1 입력 범위 | born-digital PDF(arXiv 등)만; 스캔본은 balanced 모드로 유예 |
| 한국어판 캡션 | 캡션 본문은 번역, "Figure 3"/"Table 2" 라벨은 원문 유지(`figure_label: korean`으로 그림/표 변경 가능) |
| 이미지 임베딩 플레이버 | `standard`(범용); Obsidian 볼트면 `obsidian`, Notion import면 `notion`, HTML 렌더면 `html`. config 기본값 → manifest·웹 UI로 논문별 변경 |

## 9. 번역 파이프라인 v2 — 섹션 병렬 + 논문 구조 활용 컨텍스트 (설계 확정, 구현 대기)

### 목표
품질↑ (섹션 간 일관성·자연스러운 경계·병기 전역 1회) + 속도↑ (병렬, 벽시계 ~3-4배 단축) + UX↑ (섹션별 진행 표시, 설정 정리).

### 검토한 옵션과 결정
| 옵션 | 방식 | 평가 |
|---|---|---|
| A. 순차 + 롤링 메모리 | 번역 결과 요약을 다음 섹션 프롬프트에 전달 | 일관성 최상이지만 병렬 불가(3-5배 느림). 논문은 내러티브가 아니라 과함 — **기각** |
| B. 완전 병렬 + 고정 컨텍스트 | 현행 + 스레드만 추가 | 빠르지만 섹션 경계 어색·전역 컨텍스트 빈약 |
| **C. 병렬 + 원문측 컨텍스트 (채택)** | 컨텍스트를 전부 '원문(영어)'에서 유도 → 의존성 0 → 완전 병렬 | 논문 특성 활용: 초록=전역 요약, 용어집=용어 메모리, 개요=지도, 직전 섹션 원문 꼬리=경계 연결. **품질·속도 양립** |
| D. 웨이브프론트 | 부분 병렬 + 번역 결과 전달 | 복잡도 대비 이득 미미 — 기각 |

핵심 논거: "메모리"가 실제로 하는 일은 ①용어 일관성(→용어집이 이미 담당) ②문체(→고정 지시) ③경계 자연스러움(→직전 섹션 **원문** 끝 2-3문장으로 충분). 번역 결과를 전달해야만 얻는 것이 거의 없으므로, 원문측 컨텍스트로 병렬성을 지킨다.

### 설계
1. **세그먼트 = 섹션 단위** (section_map 순서). 선택 안 된 섹션·참고문헌은 통과(현행). 작은 섹션(<~800자)은 인접 선택 섹션과 한 유닛으로 병합(멤버 sid 목록 보존 — 진행 표시용), 큰 섹션(>~6000자)은 문단 경계로 파트 분할(모든 파트 완료 시 섹션 완료).
2. **컨텍스트 2층**:
   - 시스템 프롬프트(고정·프로바이더 캐시): 문체 + 구조규칙 + 용어집 + 제목/초록 + **문서 개요(전 섹션 제목 트리)** ← 신규.
   - 유저 메시지(유닛별): `[이전 섹션 끝부분 — 참고용, 번역·출력 금지]\n…\n\n[번역할 본문]\n…` ← 직전 섹션 '원문' 꼬리 2-3문장. 원문 유도라 병렬 안전.
3. **병렬 실행**: ThreadPoolExecutor, workers 기본 4 (config `[translate].workers`; TPM 레이트리밋 고려). 캐시 히트·통과 유닛은 즉시 해석. 결과는 유닛 인덱스로 순서 보존 조립. `llm/base.py` usage 누적에 threading.Lock 추가(멀티스레드 비용 집계).
4. **병기(첫 등장 원어 병기) 결정론 후처리** — 신규 `translate/postprocess.py`: 조립된 ko.md에서 policy=병기-first-use 용어별로 `한국어(English)`(전각 괄호 포함) 매치를 찾아 **문서 순서 첫 1개만 남기고** 나머지는 `한국어`로 치환. 처리 전 코드/수식은 chunker.protect로 마스킹. (프롬프트는 "나올 때마다 병기"로 단순화 → 후처리가 전역 1회 보장. 미등장 시 주입은 위험해서 안 함.)
5. **캐시**: 유닛 '원문 텍스트' 해시 유지. 컨텍스트(이전 섹션 꼬리)는 해시에 **미포함** — 섹션 하나 수정 시 이웃까지 연쇄 재번역되는 것 방지(컨텍스트는 참고용이므로 허용).
6. **검증·재시도**: 기존 validate + retry 사다리 그대로(소스=본문 유닛만 비교). 모델이 컨텍스트 블록을 번역해 붙이면 구조 검증에 걸려 재시도 → 지시문으로 예방.
7. **UI**:
   - '번역할 섹션' 트리에 **섹션별 진행 상태** 표시: 라벨 접미사(대기 없음 / ⏳ 진행 / ✓ 완료 / ⚠ 영어 유지). 워커 스레드는 공유 dict(sid→상태)만 쓰고, UI는 0.3s 타이머 폴링으로 refresh(NiceGUI 스레드 안전).
   - '번역 방식' 설정(문체·헤더/참고문헌 번역 스위치 등)은 `ui.expansion`(접힘)으로 → 번역할 섹션 트리가 잘 보이게.
8. **파일**: `translate/apply.py`(유닛화·병렬·후처리·진행), `translate/engine.py`(개요 블록·유저 메시지 컨텍스트), `translate/chunker.py`(문단 분할 헬퍼), `translate/postprocess.py`(신규), `llm/base.py`(Lock), `config.py`(workers), `ui/app.py`(아코디언·트리 상태·폴링), `ui/controller.py`(콜백 전달).
9. **테스트**: 병기 dedup(전각/반각·코드 보호), 유닛 분할(병합·파트·통과 혼재 시 원문 복원), 병렬 조립 순서(FakeProvider), 진행 콜백 sid 매핑, workers=1 폴백.

### 리스크
- TPM 레이트리밋 → workers 설정 노출(기본 4), SDK 자동 재시도 신뢰.
- NiceGUI 크로스스레드 업데이트 → 폴링 패턴으로 회피.
- 컨텍스트 유출(모델이 참고용 텍스트까지 번역) → 지시문 + 구조 검증이 방어.

## 10. 홈 화면 v2 — 멀티 업로드 큐 + 디자인 개편 (설계 확정, 구현 대기)

### 목표
① 여러 PDF를 한 번에 올려 **순차(batch) 변환** ② 변환 후 **자동 이동 제거** — '변환한 논문'에서 골라 진입 ③ 첫 페이지 **디자인 정리** (기본 q-uploader 위젯의 "0.0B/0.00%" 헤더 노출 등 제거).

### UX 설계
```
        md4paper
  논문 PDF를 마크다운+한국어로

┌─ (키 없을 때만) AI 키 미설정 배너 — 참고문헌·번역 기능 꺼짐 · [설정] ─┐

╭┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄╮
┆        ☁  PDF를 끌어다 놓거나 클릭해 선택          ┆   ← 히어로 드롭존 (dashed, 여러 개 가능)
┆      여러 개를 올리면 순서대로 변환합니다           ┆
╰┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄╯
  [변환 방식 ▾]  [□ OCR]        저장 위치는 캡션/툴팁

── 변환 대기열 (있을 때만) ──────────────
  ⏳ paperA.pdf   ① 추출 중… 34초 · 17페이지   [진행바]
  · paperB.pdf   대기 1번째                     [취소]
  ✓ paperC.pdf   완료 → 아래 목록으로

── 변환한 논문 ──────────────────────
  (기존 카드 리스트; 방금 완료된 항목은 좌측 초록 보더로 잠깐 하이라이트)
```

### 아키텍처 — 전역 변환 큐 (페이지 이동에 견고)
1. **업로드**: `ui.upload(multiple=True, auto_upload=True)` — on_upload는 파일마다 발화. 핸들러는 즉시 `save_source()`로 **원본을 디스크에 저장**하고(바이트를 큐에 안 들고 다님 — 크래시 나도 파일 보존) `state["queue"]`에 항목 추가: `{name, src_path, pages, status:"pending", phase:"", error:"", enqueued_at}`. 같은 이름이 이미 대기·진행 중이면 스킵+알림.
2. **워커**: `background_tasks.create(_queue_worker(state))` — **한 번에 하나만**(state["worker_running"] 가드). 큐에서 pending을 꺼내 ① convert(추출) → ② _auto_cite → ③ _auto_glossary 순차 실행(run.io_bound), 항목 dict의 phase/status를 갱신. docling 모델은 프로세스에 이미 로드돼 2번째부터 빠름. **네비게이션 금지** — 완료 시 status="done"만.
3. **UI 갱신**: 홈 페이지는 `ui.timer(0.5s)` 폴링으로 큐 패널·'변환한 논문' 목록을 refresh (translate 진행과 같은 패턴 — 워커 스레드가 UI를 직접 안 건드려 스레드/페이지수명 안전). 페이지를 떠났다 돌아와도 state 큐를 그대로 렌더.
4. **save_and_convert 분리**: `save_source(data, fname, upload_dir) -> src_path` + `convert_source(src_path, wd_dir, backend, ocr)` 로 쪼갬(기존 함수는 둘을 호출하는 래퍼로 유지 — 테스트 호환).
5. **항목 상태**: pending(대기 N번째) → extracting(①, 페이지 기반 진행바) → cite(②) → glossary(③) → done(✓)/failed(✗ 사유). 대기 항목엔 [취소](큐에서 제거). 품질 경고(garbled_chars)는 항목 행에 ⚠로.
6. **완료 처리**: recent_list.refresh() → '변환한 논문' 맨 위 등장, 몇 초간 하이라이트(state에 done_at 기록, 최근 10초면 초록 보더). 큐에서는 done 항목을 잠시 보였다가 제거.

### 디자인 변경 (NiceGUI + CSS만)
- **드롭존**: `_HOME_CSS`로 q-uploader 개조 — `.q-uploader__header{display:none}`(파란 헤더 제거), 본체를 dashed 보더·중앙 아이콘+문구·hover 하이라이트로. 클릭 선택은 숨은 `input[type=file]`을 JS로 트리거하는 전체영역 클릭 핸들러(드래그&드롭은 q-uploader 네이티브 그대로).
- **설정 축소**: 변환 방식 select + OCR 체크를 드롭존 아래 한 줄로. 저장 위치는 회색 캡션 유지. **AI 설정 expansion은 키가 하나라도 있으면 접힘 유지, 키가 없으면 expansion 대신 얇은 경고 배너**(클릭 시 펼침)로 — 첫 화면 수직 부피 축소.
- **폭·간격**: 컬럼 폭 620→700px, 섹션 간 간격 정리, '변환한 논문' 카드 폭 통일.
- 다크모드: 드롭존/배너 색은 prefers-color-scheme 양쪽 지정.

### 파일
- `ui/app.py`: `_HOME_CSS`, `save_source`/`convert_source` 분리, `_queue_worker`, `build_home` 재구성(드롭존·큐 패널·폴링·자동이동 제거), 완료 하이라이트.
- 테스트: 워커를 convert 함수 주입형으로 만들어 서버 없이 검증 — enqueue 2건 → 순차 처리·상태 전이·실패 항목이 다음 항목을 막지 않음·중복 이름 스킵. `save_source` 저장 경로. UI 스모크: 드롭존 문구·"변환 대기열" 렌더(빈 큐일 땐 숨김), 자동 이동 제거(응답에 navigate 없음은 스모크로 불가 → 워커 로직 테스트로 대체).

### 리스크·결정
- 서버 재시작 시 큐 소실 — 원본은 이미 디스크에 있으므로 재업로드 없이 수동 재변환 가능(수용).
- 같은 파일 재변환 = 기존 워크디렉토리 덮어씀(현행 유지).
- CLI로 wd 지정해 띄운 경우(`/`→리뷰 직행)는 그대로.

## 11. 저장 위치 — 변환한 논문을 쌓을 전역 폴더 (구현 완료)

### 목표
매번 zip을 받아 푸는 대신, 변환·번역이 끝나면 **사용자가 고른 폴더에 결과 마크다운이 자동으로 쌓이게** 한다.
**영어와 한국어를 서로 다른 폴더로** 보낼 수 있다(예: Obsidian 볼트의 `Papers/EN`, `Papers/KO`).
폴더는 첫 화면에서 **OS 기본 폴더 선택 대화상자**로 고른다.

### 설계
1. **작업 폴더와 저장 위치는 다른 개념** — 작업 폴더(`[output].workspace`)는 원본 PDF·중간 산출물이 든 작업장(진실원),
   저장 위치(`[library].en_dir`/`ko_dir`)는 결과 마크다운만 모아 노트 앱에 그대로 쓰는 **사본** 폴더다.
   사본이라 지워도 안전하고, 언제든 다시 내보낼 수 있다(`library --export`). 홈에서 둘 다 대화상자로 바꾼다.
2. **레이아웃** — `<폴더>/<논문이름>.md` + `<폴더>/images/<논문이름>/…`. 파일명은 작업 디렉토리 stem
   (`{year}_{ShortTitle}_{1저자}` — 서지 기반 자동 리네임 결과)이라 여러 편이 한 폴더에 쌓여도 충돌하지 않는다.
   이미지도 논문별 하위 폴더로 격리하고 마크다운의 `images/…` 참조(표준 `![](…)`·Obsidian `![[…]]` 둘 다)를 거기에 맞춰 고쳐 쓴다.
   두 언어를 같은 폴더로 지정하면 이름이 겹치므로 `<이름>.en.md`/`<이름>.ko.md`로 자동 구분.
3. **형식** — 다운로드 zip과 같은 `[output].export_target`(범용/Notion/Obsidian)을 그대로 적용(`export_format.to_export_target`).
   en.md/ko.md 원본은 늘 canonical 유지 — 사본만 변환한다(형식을 바꿔 다시 내보내도 손실 없음).
4. **자동 저장 시점**(`[library].auto`, 기본 켜짐) — ① 변환 큐 완료(리네임 후 최종 이름으로) ② 번역 완료
   ③ 리뷰 화면의 `commit()`(구조·설정 변경 → 재조립). 실패는 조용히 무시(`auto_export`) — 사본 때문에 변환·번역이 막히면 안 된다.
   수동 경로: 논문 화면 **폴더로 저장**, 홈의 **이미 변환한 논문도 지금 내보내기**, 목록 다중선택 **폴더로 저장**, CLI `library --export`.
5. **폴더 선택 대화상자**(`ui/folder_dialog.py`) — 브라우저에는 실제 경로를 주는 API가 없다(File System Access는 샌드박스 핸들).
   로컬 앱이라 **서버 프로세스 = 사용자 컴퓨터**이므로 OS 대화상자를 서버에서 띄운다:
   macOS `osascript`(choose folder) · Windows PowerShell `FolderBrowserDialog` · Linux `zenity`/`kdialog`.
   `run.io_bound`로 호출(이벤트 루프 차단 방지), 300초 타임아웃, 취소·미지원은 None. 헤드리스·SSH에서는 `available()`이 False가 되고
   **경로 직접 입력 칸이 항상 함께** 있다(`MD4PAPER_NO_NATIVE_DIALOG`로 강제 비활성 — 테스트용).
6. **재복사 억제** — 이미지는 크기·mtime이 같으면 건너뛴다(설정을 만질 때마다 수백 KB를 다시 쓰지 않도록).
   참조되지 않은 이미지는 애초에 복사하지 않고, 이미 있는 파일도 지우지 않는다(다른 언어 사본이 참조 중일 수 있음).

### 파일
- `library.py`(신규): `dir_for`/`configured`/`auto_enabled`/`same_folder`/`file_name`/`export`/`export_paper`/`auto_export`/`export_many`.
- `config.py`: `[library]` 해석(`resolve_library_dir`/`set_library_dir`/`resolve_library_auto`), `set_section_value(value=None)`으로 키 삭제.
- `ui/folder_dialog.py`(신규), `ui/app.py`(`build_location_settings` 패널·작업 폴더 동적화·자동 저장 훅·`폴더로 저장` 버튼), `cli.py`(`md4paper library`).
- `cite/apply.py`: `ref_urls(wd)` 공개(라이브러리·컨트롤러가 공유 — Notion 인용 링크용).
- 테스트: `test_library.py`(폴더 분리·이미지 격리·형식 적용·덮어쓰기·같은 폴더 접미사·auto 스위치·실패 무시), `test_folder_dialog.py`(AppleScript 이스케이프·취소·타임아웃·경로 정규화), `test_home_queue.py`(변환 완료 시 자동 저장), `test_ui_server.py`(패널 렌더).

### 리스크·결정
- 사본이 원본과 어긋날 수 있음 → 자동 저장을 기본 켜고 재조립 시점에도 갱신. 어긋나면 `library --export`로 전부 재생성.
- 원격 브라우저(SSH 포트포워딩)에서는 대화상자가 서버 쪽에 뜬다 → 경로 직접 입력 칸을 항상 유지.
- 작업 폴더를 바꾸면 이전 폴더의 논문은 목록에서 사라진다(삭제되진 않음) — 되돌리면 다시 보인다.

### 파일 이름 규칙 + PDF 사본 (구현 완료)
"md에서 원본 PDF를 못 찾겠다"는 문제의 해법 두 가지를 §11에 추가했다:
1. **이름 규칙 템플릿** `[output].naming` (기본 `{year}_{title}_{author}`) — 논문 폴더·작업 폴더 PDF·저장 위치 md/PDF가
   전부 이 규칙의 기준명을 공유한다. 조각: {year}/{title}(약칭 CamelCase)/{author}(1저자 성)/{venue}(영숫자 20자).
   빈 조각은 자리 구분자와 함께 빠지고(센티널 방식 — 리터럴 `--`는 보존), 파일명 금지 문자는 렌더에서 제거.
   검증(`config.naming_template_error`): 자리표시자 ≥1 + 금지 문자 없음; 저장값이 깨져 있으면 기본 규칙 폴백.
2. **PDF 저장 위치** `[library].pdf_dir` — 변환 완료 시 원본 PDF(meta.json source)를 `<기준명>.pdf`로 복사(크기·mtime 같으면 스킵).
   md와 같은 이름이라 노트 앱에서 나란히 찾아진다.
3. **일괄 정리** `paper_meta.apply_naming(ws)` — 기존 논문 전체(숨김 포함)를 현재 규칙으로: rename_workdir(폴더·.md4·PDF·meta source)
   → 저장 위치 재내보내기 → 옛 기준명 사본 청소(`library.remove_stem`: <stem>*.md·images/<stem>/·<stem>.pdf만).
   서지 없으면 건너뜀. 멱등(재실행 시 unchanged). UI '기존 논문·PDF 이름 정리' 버튼 + `md4paper naming --apply`.

### 이름 뒤 '(2)' 버그 — 변환 안 된 업로드 잔여물이 이름을 점유 (수정 완료)
증상: 겹치는 논문이 없는데도 폴더·저장 위치 파일이 `2024_Clio_Tamkin (2).md`처럼 됨(실사용 51편 중 8편).
원인: 업로드는 **원본을 즉시 디스크에 저장**하고 변환은 큐에서 나중에 도는데(§10-1), 그 사이에 서버를 끄거나
큐가 날아가면 `<ws>/<이름>/<이름>.pdf`만 든 **껍데기 폴더**가 남는다. 재업로드하면 `save_source`가 이 폴더를
'이미 있는 논문'으로 보고 `(2)` 폴더를 만들고, 이어서 `rename_workdir`도 같은 이유로 `(2)`를 유지 → 최종 산출물
이름이 영구히 오염된다. 껍데기는 sections.yaml이 없어 목록에 안 보이므로 사용자는 충돌 원인을 볼 수도 없다.
수정: `workdir.is_upload_stub()`(= .md4가 없는 폴더)를 **이름 충돌 판정에서 빈 이름으로 취급**한다.
`save_source`는 껍데기를 재사용하고, `rename_workdir`는 껍데기를 흡수(`_absorb_stub`: 내용물 이동 + 같은 이름
PDF는 우리 것으로 덮어씀 + 빈 폴더 제거)한 뒤 원하는 이름을 그대로 쓴다. **진짜 변환된 논문**이 이름을 쓰고
있으면 종전대로 `(2)`(같은 논문을 두 번 변환한 정상 케이스). 기존에 오염된 이름은 '기존 논문·PDF 이름 정리'로 복구된다.

### 추출 깨짐 ② 2바이트 코드가 한자로 묶임 (수정 완료)
증상: 특정 논문의 제목·저자·초록이 `⁌潯歩⁉湴⁴⁂污⁂潸`처럼 한자로 나옴(구형 CID 폰트 PDF, 실사용 57편 중 1편).
원인: docling이 2바이트 문자 코드를 UTF-16 코드포인트로 잘못 묶는다 — `"Looki"`(6바이트) → `"⁌潯歩"`(3글자).
게다가 추출 과정에서 글자가 일부 유실돼 되돌려도 `"Looki Int t Bla Box"`가 된다(단독 복호로는 못 고침).
기존 `garbled_chars`는 U+FFFD만 세므로 **경고조차 뜨지 않았다**(meta에 0).
수정(`text_clean.repair_mojibake_from_pdf`): **pypdfium2가 읽는 PDF 텍스트 레이어는 멀쩡하다**는 점을 이용해,
깨진 줄을 바이트로 되돌려 남은 단어 조각(≥3글자)을 순서대로 잇는 정규식으로 PDF 원문 위치를 찾고 그 구간을 가져온다.
안전장치: ① 두 바이트가 모두 출력 가능 ASCII인 글자만 후보(진짜 한자·한글 제외) ② 줄의 30% 이상 ③ **PDF에서 결과가
하나로 확정될 때만** 교체(머리말 반복처럼 후보가 여럿이어도 문자열이 같으면 안전) ④ 길이가 원본의 0.5~4배 밖이면 기각.
못 고친 글자는 `garbled_chars`에 더해 홈 목록·CLI의 ⚠ 경고로 노출. 실측: 깨진 14줄 전부 복구, 남은 0자.

### 서지 ③ 오래된 논문이 최근 연도로 나옴 (수정 완료)
증상: 1986년 논문(Rouse & Morris)이 `2025_...`로 명명됨. venue도 빈 값.
원인 2단: ① docling이 저널 머리말("Psychological Bulletin / 1986, Vol. 100 / Copyright 1986 by APA")을
**통째로 버려서** `front_text()`에 연도가 없었다(frontmatter.txt도 빈 값) → LLM이 정직하게 year=None 반환.
② 폴백 `pdf_year()`가 PDF 생성일을 썼는데, 이 PDF는 2025년에 다시 만든 재배포본(Producer: PDFlib+PDI)이라 2025.
오래된 논문의 스캔·재배포본에서는 **생성일이 출판연도와 무관**하다.
수정: ① `front_text()`가 `pdfio.first_page_text()`로 **PDF 1페이지 머리말을 앞에 붙인다**(추출기가 버려도
텍스트 레이어에는 남아 있다 — 연도·venue의 가장 확실한 출처). ② `pdf_year()`는 1페이지의 저작권 연도
(`Copyright 1986` / `© 1986`)를 먼저 보고, 없을 때만 생성일로 폴백.
실측: year 2025→1986, venue ""→"Psychological Bulletin", 폴더 `1986_LookingBlackBox_Rouse`로 정리됨.

### 서지 보강 `enrich.py` — 공개 API로 빈 연도·venue 채우기 (구현 완료, §5 --enrich 대체)
PDF에 연도·학회가 아예 없는 논문(프리프린트·구형 스캔본)은 LLM도 못 채운다 → 공개 서지 API를 옵트인으로 조회.
**설계의 핵심은 제목 유사도 게이트(≥0.90)다.** Crossref `query.bibliographic`은 유사도와 무관하게 1등을 반환하므로
게이트 없이 쓰면 오답이 조용히 들어온다(실측: "Designing a Meta-Reflective Dashboard…" → 2010년 책). cite/parse의 반환각 검증과 같은 규율.
- 1차 **OpenAlex**(키 불필요, 저널·학회·arXiv 커버, 실측 4/4 정확) → 저장소(arXiv)만 잡히면 **Crossref**로 출판 venue 보강.
- **비어 있는 필드만** 채움(PDF가 진실원). 템플릿 자리표시자("Journal Title", "Conference acronym 'XX")는 빈 값으로 취급 —
  `paper_meta.extract`도 저장 전에 같은 필터를 거친다.
- 출처를 `meta_source`에 기록. LLM 스키마(`PaperMeta`)와 저장 스키마(`StoredPaperMeta`)를 분리 — 보강 필드를 추출
  스키마에 두면 모델이 DOI를 지어낸다.
- **질의 제목 자체를 검증**(`usable_title`: ≥16자·≥3단어·섹션 헤딩 아님). 유사도 게이트는 '우리 제목이 맞다'는 전제 위에서만
  동작한다 — 제목 추출이 실패해 "1 Introduction"이던 논문이 Crossref "Introduction"(0.92)과 붙어 언어학 저널이 들어온 실제 사고를 막는다.
- 실측(56편): 22편 보강(연도 3·venue 22), 오매치 0. 네트워크 실패·레이트리밋은 조용히 무시.

### 목록 정리 — 숨기기 vs 삭제 (구현 완료)
'변환한 논문' 카드의 휴지통은 두 선택지를 준다: **목록에서 숨기기**(`status.json`의 `hidden` 플래그만 — 파일 무손실)와
**파일 삭제**(기존 `delete_workdir`, 되돌릴 수 없음). 목록이 길어져 치우고 싶은 것과 진짜로 지우고 싶은 것은 다른 요구인데
예전엔 삭제 하나뿐이었다. 숨김은 전역 목록이 아니라 **작업 디렉토리 안**에 기록한다(everything-is-a-file — 폴더를 옮겨도 따라간다).
`recent_workdirs(..., include_hidden=False)`가 기본 필터, 목록 상단의 '숨긴 논문 N편 보기' 토글 + 카드의 '다시 표시' 버튼이 복원 경로다
(숨김이 '영영 사라짐'이 되지 않도록 되돌릴 길을 항상 노출).

## 12. 레이아웃 자동 수정 — 깨진 마크다운을 LLM으로 훑어 고치기 (구현 완료)

### 목표
PDF 추출은 단어는 살리지만 **구조를 잃는다**. 실제 사례: `## 2` 와 `## Background` 가 헤더 두 개로 쪼개지고,
인라인 수식이 `x t` · `h ( x t )` 처럼 풀어헤쳐지고, 문단이 하이픈(`criti-` + `cal`)에서 끊긴다.
섹션 트리는 헤더 **레벨**만 고칠 수 있어서 이런 건 손댈 수 없었다(`2`와 `Background`는 애초에 별개 헤더로 잡힌다).
변환 탭 버튼 하나로 **문서 전체를 처음부터 끝까지 훑어** 이런 것들을 고친다.

### 설계
1. **고치는 대상은 en.md가 아니라 raw.md** — en.md는 `raw.md + 매니페스트`로 매번 다시 조립되므로(assemble),
   en.md만 고치면 레벨을 한 번 바꾸는 순간 사라진다. raw.md를 고치고 **구조를 다시 잡으면**(`run_structure`)
   섹션 트리·일괄 레벨 조정·프리뷰가 전부 새 구조를 그대로 따라간다. 사용자 요구의 핵심이 이 지점이다.
2. **구조는 새로 잡되 사용자의 선택은 이어받는다**(`relayout.inherit`) — 문서 설정(인용 표기·문체·플레이버 등)은
   매니페스트가 권위 소스이므로 통째로 승계하고, **자동값과 다른 레벨**(=실제 교정)과 번역 제외 표시는 `prefs.norm_key`
   (번호·구두점·대소문자 무시)로 이름을 맞춰 옮긴다 → `Background`에 준 선택이 `2 Background`로 살아남는다.
3. **청킹은 라운드트립이 보장돼야 한다** — `"\n".join(split_for_fix(raw)) == raw`(끝 개행 제외). 못 고친 청크를
   **원문 그대로** 되돌려 놓는 안전장치가 여기에 기댄다. 자를 위치는 펜스 밖 문단/헤더 경계이되 **직전 실질 줄이
   헤더면 자르지 않는다** — 쪼개진 헤더가 청크 경계로 갈라지면 어느 쪽에서도 합칠 수 없다(고쳐야 할 대상이 정확히 그것).
   실측 56편 전부 라운드트립 일치 + 경계 위반 0.
4. **안전장치 3겹** — LLM이 문서를 통째로 다시 쓰는 자리라 보수적으로 간다.
   ① 이미지·코드 블록은 센티넬(`⟦MD4_n⟧`)로 가려 노출조차 안 한다(복원 후 개수 검증).
   ② 청크마다 **영숫자 내용 키**(기호·공백·마크다운 표기를 지운 것)를 비교한다 — 레이아웃 수정은 기호와 줄바꿈만
   바꾸므로 이 키는 그대로여야 한다(`x t .`→`$x_t$.`, `## 2`+`## Background`→`## 2 Background` 모두 키가 같다).
   보존율 95% 미만이거나 15% 넘게 불어나면 한 번 재시도하고, 그래도 어긋나면 **그 청크는 원문 유지**(번역의 passthrough와 같은 사다리).
   ③ 적용 직전 `raw.md`·`sections.yaml`·`blocks.json`을 `.pre-fix` 스냅샷으로 남긴다 → 모달의 `직전 상태로 되돌리기`.
5. **커스텀 프롬프트** — 모달의 `추가 지시`는 고정 시스템 프롬프트 뒤에 붙되 "단어를 바꾸지 말라"는 규칙이 우선한다고
   명시한다. 내용을 지우라는 지시는 ②에 걸릴 수 있고, 그때는 몇 구간이 원문 유지됐는지 알림에 그대로 보고한다.
6. **번역과 같은 병렬 노브**(`[translate].workers`) — 청크 간 결과 의존이 없어 스레드로 동시 호출하고,
   워커가 갱신하는 dict를 UI 타이머가 폴링해 진행률을 그린다(NiceGUI 스레드 안전 패턴 재사용).
7. **status.json 정합** — 구조를 다시 잡으면 하위 단계가 무효화된다(`mark_done`). front matter 캐시의 `output_hash`는
   새 raw.md 해시로 맞춰 둔다 — 안 그러면 나중에 재변환할 때 앞부분 정규화가 **방금 고친 앞부분을 되돌린다**.

### 파일
- `relayout.py`(신규): `split_for_fix`/`content_key`/`check`/`build_system_prompt`/`fix_chunk`/`fix_markdown`/
  `snapshot`/`has_snapshot`/`restore`/`inherit`/`rebuild`/`run`.
- `ui/controller.py`: `layout_fix_plan`/`fix_layout`/`can_undo_layout_fix`/`undo_layout_fix`/`_adopt`.
  `_adopt`는 새 매니페스트를 **기존 객체에 옮겨 담는다** — UI 클로저들이 manifest 객체를 직접 붙들고 있어(`m.sections`)
  참조를 갈아치우면 섹션 트리가 옛 구조를 계속 그린다.
- `ui/app.py`: 변환 탭 좌측 상단 버튼 + 모달(설명·규모·추가 지시·진행바·되돌리기) + `_after_relayout()`(파생 뷰 일괄 갱신).
- 테스트: `test_relayout.py`(라운드트립·헤더 경계·검증 통과/기각·센티넬 보호·재시도 사다리·구조 재구축·설정 승계·
  객체 동일성·되돌리기), `test_ui_server.py`(버튼·모달 렌더), `corpus/broken_layout.md`(실제 깨짐 패턴).

### 리스크·결정
- **LLM이 내용을 지우거나 지어내는 것**이 최대 리스크 → 내용 키 검증 + 청크 단위 원문 유지 + 스냅샷 되돌리기.
  청크를 통으로 버리는 쪽이 조용히 문장을 잃는 것보다 낫다(사용자에게 개수로 보고).
- 되돌리기는 **1단계**(마지막 수정 직전)만 — 스냅샷을 하나만 둔다. 버튼 문구도 '직전 상태로'로 정확히 적는다.
- 직접 편집한 en.md는 재조립으로 사라진다 → 모달에 경고를 띄우고 `manual_edit` 플래그를 해제한다.
- 비용은 문서 전체를 한 번 훑는 만큼 든다(≈번역 1회 수준) → 모달에 글자 수·구간 수·모델을 미리 보여준다.

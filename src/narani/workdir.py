"""작업 디렉토리(paper.md4/) 레이아웃과 단계별 상태(status.json) 관리.

모든 단계는 이 디렉토리 위의 재개 가능한 순수 함수다. status.json이 단계별 입력 해시를
기록해 재실행 시 변경 없는 단계는 건너뛰고, 상위 단계가 바뀌면 하위를 무효화한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# 단계 순서 — 앞 단계가 바뀌면 뒤 단계 완료 표시가 무효화된다.
STAGES = ("extract", "formulas", "structure", "assemble", "cite", "translate")

# status.json의 `lang` 읽기 캐시 — WorkDir.ko_md 등 경로마다 status.json을 다시 파싱하지 않게.
# 키: status.json 경로 → ((mtime_ns, size), lang | None). save_status가 os.replace로 갈아끼우므로 mtime이 바뀐다.
_LANG_CACHE: dict[Path, tuple[tuple[int, int], str | None]] = {}


def _lang_name(stem: str, lang: str, ext: str) -> str:
    """언어별 파일 이름 — 한국어는 언어 코드 없는 옛 이름(`cache.json`), 다른 언어는 `cache.ja.json`."""
    return f"{stem}.{ext}" if lang == "ko" else f"{stem}.{lang}.{ext}"


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def hash_file(path: Path) -> str:
    return hash_bytes(path.read_bytes())


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


@dataclass
class WorkDir:
    """paper.md4/ 디렉토리 핸들."""

    root: Path

    @classmethod
    def for_pdf(cls, pdf: Path, out: Path | None = None) -> WorkDir:
        """PDF 경로로부터 작업 디렉토리 결정 (기본: PDF 옆 <stem>.md4/)."""
        root = out if out is not None else pdf.with_suffix(".md4")
        return cls(Path(root))

    # --- 서브디렉토리 ---
    @property
    def extract(self) -> Path:
        return self.root / "extract"

    @property
    def structure(self) -> Path:
        return self.root / "structure"

    @property
    def cite(self) -> Path:
        return self.root / "cite"

    @property
    def translate(self) -> Path:
        return self.root / "translate"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    # --- 아티팩트 경로 ---
    @property
    def marker_json(self) -> Path:
        return self.extract / "marker.json"

    @property
    def raw_md(self) -> Path:
        return self.extract / "raw.md"

    @property
    def extract_images(self) -> Path:
        return self.extract / "images"

    @property
    def meta_json(self) -> Path:
        return self.extract / "meta.json"

    @property
    def frontmatter_txt(self) -> Path:
        return self.extract / "frontmatter.txt"  # 추출 시 걷어낸 저작권/venue 텍스트 (서지 추출용)

    @property
    def paper_meta_json(self) -> Path:
        return self.root / "paper_meta.json"  # 논문 서지(제목·저자·연도·venue) — 목록 표시·검색·정렬

    @property
    def bib_source_json(self) -> Path:
        # 논문 API에서 받아 온 출판 기록(§bibsource) — BibTeX 항목의 원본.
        # paper_meta.json과 따로 두는 이유: 이건 **외부에서 받은 것**이라 지우고 다시 받으면
        # 그만이고, PDF에서 읽은 값(paper_meta)을 덮어써 원본을 잃는 일이 없어야 한다.
        return self.root / "bib_source.json"

    @property
    def authors_json(self) -> Path:
        # 구조화된 저자(이름·이메일·소속) — 표기(author_parts) 변경 시 LLM 재호출 없이 재렌더용
        return self.extract / "authors.json"

    @property
    def footnotes_json(self) -> Path:
        # 각주 {번호: 내용} — 본문 위첨자 링크(#fn-N) 호버 툴팁용
        return self.extract / "footnotes.json"

    @property
    def formulas_json(self) -> Path:
        """수식 레코드 (크롭 그림·PDF 원문·식 번호·확정된 LaTeX) — 수식 단계의 캐시이자 근거."""
        return self.extract / "formulas.json"

    @property
    def figures_json(self) -> Path:
        """그림 자리 {extract 그림 파일명: {page, bbox, manual?}} — 영역 다시 지정의 근거 (§figure_fix)."""
        return self.extract / "figures.json"

    @property
    def figure_alts_json(self) -> Path:
        """그림 설명 {out 그림 파일명: {alt, via}} — 태그 PDF의 대체 텍스트(/Alt)를 그림에 짝지은 것 (§figure_alt)."""
        return self.extract / "figure_alts.json"

    @property
    def figures_ko_json(self) -> Path:
        """그림 속 글자 번역 기록 {extract 그림 파일명: {원본 digest, 줄 상자·영어·번역}} (§figure_translate).
        번역 언어마다 따로 — 한국어는 옛 이름 figures-ko.json."""
        return self.extract / f"figures-{self.lang}.json"

    @property
    def figure_backup(self) -> Path:
        """영역을 다시 지정하기 전의 원래 그림 — extract/images 밖에 둔다(조립이 images를 통째로 복사한다)."""
        return self.extract / "figure-backup"

    @property
    def headings_json(self) -> Path:
        """추출기가 준 헤더별 원본 PDF 페이지/좌표 (섹션↔PDF 대조용)."""
        return self.extract / "headings.json"

    @property
    def sections_yaml(self) -> Path:
        return self.structure / "sections.yaml"

    @property
    def blocks_json(self) -> Path:
        return self.structure / "blocks.json"

    @property
    def en_md(self) -> Path:
        return self.out / "paper.en.md"

    @property
    def ko_md(self) -> Path:
        """번역 마크다운 — 이 논문의 번역 언어로 (`paper.<lang>.md`; 한국어는 옛 이름 그대로 paper.ko.md).
        이름의 ko는 '번역 쪽'이라는 뜻으로 남겼다."""
        return self.out / f"paper.{self.lang}.md"

    @property
    def out_images(self) -> Path:
        return self.out / "images"

    @property
    def sections_map(self) -> Path:
        return self.out / "sections.map.json"

    @property
    def references_json(self) -> Path:
        return self.cite / "references.json"

    @property
    def links_json(self) -> Path:
        return self.cite / "links.json"

    @property
    def context_md(self) -> Path:
        return self.translate / "context.md"

    @property
    def glossary_yaml(self) -> Path:
        """논문 용어집 — 번역어가 언어마다 다르니 언어별 (한국어는 옛 이름 glossary.yaml)."""
        return self.translate / _lang_name("glossary", self.lang, "yaml")

    @property
    def cache_json(self) -> Path:
        """번역 캐시 — 언어별 (한국어는 옛 이름 cache.json)."""
        return self.translate / _lang_name("cache", self.lang, "json")

    # --- 번역 언어 ---
    @property
    def lang(self) -> str:
        """이 논문의 번역 언어 — 논문은 번역을 한 언어로만 둔다.

        status.json `lang`(번역·용어집을 만들 때 적는다) > 옛 한국어 산출물(paper.ko.md·glossary.yaml·
        cache.json)이 있으면 ko > 전역 설정(`config.resolve_target_lang`). 옛 산출물을 보는 이유: `lang`을
        적기 전의 논문은 사용자가 전역 언어를 바꿔도 한국어 번역을 그대로 보여야 한다.
        """
        got = self._status_lang()
        if got:
            return got
        if any(p.exists() for p in (self.out / "paper.ko.md", self.translate / "glossary.yaml",
                                    self.translate / "cache.json")):
            return "ko"
        from narani import config  # config는 가볍지만 workdir가 먼저 import되는 곳이 많다

        return config.resolve_target_lang()

    def _status_lang(self) -> str | None:
        path = self.status_json
        try:
            st = path.stat()
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _LANG_CACHE.get(path)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("lang")
        except (OSError, ValueError, AttributeError):
            raw = None
        from narani import langs

        lang = langs.normalize(raw) if langs.is_valid(raw) else None
        _LANG_CACHE[path] = (stamp, lang)
        return lang

    def set_lang(self, lang: str) -> None:
        """번역 언어를 status.json에 적는다 (이미 같으면 쓰지 않는다). 언어를 바꾸면 경로가 그 언어 것으로
        바뀐다 — 옛 언어 파일은 지우지 않고 두며, 다시 그 언어로 번역하면 캐시를 쓴다."""
        from narani import langs

        lang = langs.normalize(lang)
        status = self.load_status()
        if status.get("lang") == lang:
            return
        status["lang"] = lang
        self.save_status(status)

    @property
    def status_json(self) -> Path:
        return self.root / "status.json"

    @property
    def annotations_json(self) -> Path:
        """뷰어에서 그은 하이라이트 · 메모 (읽는 사람의 것이라 파이프라인이 덮어쓰지 않는다)."""
        return self.root / "annotations.json"

    @property
    def notes_md(self) -> Path:
        """논문 전체에 대한 자유 메모(마크다운) — 왼쪽 레일 '메모' 패널. 읽는 사람의 것이라 파이프라인이 덮어쓰지 않는다."""
        return self.root / "notes.md"

    @property
    def chat_json(self) -> Path:
        """옛 뷰어 챗봇 대화 기록 — 에이전트 창(전역 세션)은 읽지 않지만 사용자의 것이라 지우지 않는다."""
        return self.root / "chat.json"

    @property
    def preview_json(self) -> Path:
        """홈 미리보기 캐시 — 출력 md에서 뽑은 초록(en/ko)과 그때의 md 표지 (preview.py)."""
        return self.root / "preview.json"

    @property
    def thumb_png(self) -> Path:
        """홈 미리보기 대표 이미지 캐시 — 그림이 없을 때 원본 PDF 1쪽 렌더 (preview.py)."""
        return self.root / "thumb.png"

    # --- 생성 ---
    def ensure(self) -> None:
        for d in (self.extract, self.structure, self.cite, self.translate, self.out, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    # --- status.json 북키핑 ---
    def load_status(self) -> dict:
        if self.status_json.exists():
            return json.loads(self.status_json.read_text(encoding="utf-8"))
        return {}

    def save_status(self, status: dict) -> None:
        """status.json 쓰기 — 임시 파일에 쓰고 갈아끼운다.

        앱 창을 여러 개 띄울 수 있게 되면서 같은 논문의 status.json을 두 프로세스가 쓸 수 있다.
        제자리에서 잘라 쓰면 도중에 끊길 때 **반쯤 쓴 JSON**이 남아 그 논문이 목록에서 사라진다.
        os.replace는 원자적이라 최악의 경우도 '한쪽 갱신이 다른 쪽에 덮이는' 정도로 끝난다.
        """
        text = json.dumps(status, ensure_ascii=False, indent=2)
        tmp = self.status_json.with_suffix(f".json.tmp{os.getpid()}")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.status_json)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def mark_done(self, stage: str, input_hash: str, **extra) -> None:
        status = self.load_status()
        status[stage] = {"input_hash": input_hash, "done": True, **extra}
        # 하위 단계 무효화
        idx = STAGES.index(stage) if stage in STAGES else -1
        if idx >= 0:
            for downstream in STAGES[idx + 1 :]:
                status.pop(downstream, None)
        self.save_status(status)

    def is_fresh(self, stage: str, input_hash: str) -> bool:
        """해당 단계가 같은 입력 해시로 이미 완료됐는지."""
        entry = self.load_status().get(stage)
        return bool(entry and entry.get("done") and entry.get("input_hash") == input_hash)


def delete_workdir(root: Path, workspace: Path, *, with_library: bool = True) -> bool:
    """작업 디렉토리(.md4)와 그 부모 논문 폴더를 삭제. 안전 검사 후 True.

    workspace 밖이거나 .md4가 아니면 거부한다(실수로 엉뚱한 폴더를 지우지 않도록).
    `with_library`면 저장 위치(라이브러리)에 쌓아 둔 사본(마크다운·이미지·PDF)도 함께 지운다.
    그 폴더는 작업 폴더 밖(사용자의 옵시디언 볼트 등)이라 여기서 정리하지 않으면 고아로 남는다.
    """
    import shutil

    root = Path(root).resolve()
    ws = Path(workspace).resolve()
    if root.suffix != ".md4" or not root.is_dir():
        return False
    if ws not in root.parents:
        return False  # 작업 폴더 밖 → 거부
    if with_library:
        from narani import library  # 지연 임포트 — library가 workdir를 임포트한다(순환 방지)

        # 라이브러리 파일명은 논문 폴더 이름(root.stem). 프로젝트를 옮겨 다녔다면 옛 프로젝트
        # 폴더에도 사본이 있을 수 있고, 원본이 사라진 뒤엔 어디였는지 알 수 없다 → 전부 훑는다.
        library.remove_stem_everywhere(root.stem)
    shutil.rmtree(root, ignore_errors=True)
    # 논문별 전용 하위 폴더(<이름>/<이름>.md4)면 남은 원본 PDF 등 잔여물까지 폴더째 정리한다.
    # (예전엔 '비었을 때만' 지워서 원본 PDF가 남아 폴더가 남았다.) 단, 다른 논문(.md4)이
    # 들어 있으면 건드리지 않는다(안전).
    parent = root.parent
    if parent != ws and ws in parent.parents and parent.is_dir():
        has_other_md4 = any(p.suffix == ".md4" and p.is_dir() for p in parent.iterdir())
        if not has_other_md4:
            shutil.rmtree(parent, ignore_errors=True)
    return True


def _absorb_stub(container: Path, stub: Path) -> None:
    """우리 논문 폴더의 내용물을 껍데기 폴더로 옮기고, 빈 원본 폴더를 치운다.

    폴더를 통째로 rename할 수 없을 때(목적지가 이미 있음)의 경로. 같은 이름이 있으면
    우리 것으로 덮어쓴다 — 껍데기에 남은 건 변환되지 않은 같은 논문의 PDF이기 때문이다.
    """
    import shutil

    for item in container.iterdir():
        target = stub / item.name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        item.replace(target)
    try:
        container.rmdir()
    except OSError:
        pass  # 예상 밖의 잔여물이 남았으면 폴더를 지우지 않고 둔다


def is_upload_stub(container: Path) -> bool:
    """변환된 논문이 없는 '업로드 잔여물' 폴더인지 (.md4가 하나도 없음).

    업로드는 원본을 즉시 디스크에 저장하고 변환은 큐에서 나중에 돈다 → 도중에 서버를 끄거나
    큐가 날아가면 PDF만 든 폴더가 남는다. 이 껍데기가 이름을 계속 점유하면 같은 논문을 다시
    올릴 때마다 '(2)'가 붙으므로, 이름 충돌 판정에서는 '비어 있는 이름'으로 취급한다.
    """
    try:
        return container.is_dir() and not any(
            c.suffix == ".md4" and c.is_dir() for c in container.iterdir())
    except OSError:
        return False


def rename_workdir(wd: WorkDir, new_base: str, workspace: Path) -> WorkDir:
    """논문 폴더를 새 기준명으로 이동 — 컨테이너 폴더·.md4·PDF·meta.json(source)까지 함께.

    구조: <ws>/<old>/{<old>.pdf, <old>.md4/}  →  <ws>/<new>/{<new>.pdf, <new>.md4/}
    이름이 **다른 변환된 논문**에 쓰이고 있으면 '<new> (2)'로 유일화하고, 변환 안 된 업로드
    잔여물(is_upload_stub)이 점유하고 있을 뿐이면 그 폴더를 흡수해 원하는 이름을 그대로 쓴다.
    예상 구조가 아니거나 이미 그 이름이면 원본 wd를 그대로 반환.
    (뷰어는 meta.json의 source(PDF 절대경로)로 PDF를 찾으므로 그 값도 새 경로로 갱신한다.)
    """
    ws = Path(workspace).resolve()
    old_md4 = Path(wd.root).resolve()
    container = old_md4.parent
    old_stem = old_md4.stem
    # 논문별 전용 하위 폴더(<ws>/<이름>/<이름>.md4) 구조일 때만 안전하게 리네임한다.
    if old_md4.suffix != ".md4" or container.parent != ws or not container.is_dir():
        return wd
    final = new_base
    i = 2
    while (ws / final).exists() and (ws / final) != container and not is_upload_stub(ws / final):
        final = f"{new_base} ({i})"
        i += 1
    if final == old_stem:
        return wd  # 이미 원하는 이름
    new_container = ws / final
    if new_container.exists():
        _absorb_stub(container, new_container)  # 껍데기가 점유 중 → 내용물을 옮겨 담고 이름을 가져온다
    else:
        container.rename(new_container)
    new_md4 = new_container / f"{final}.md4"
    (new_container / f"{old_stem}.md4").rename(new_md4)
    old_pdf = new_container / f"{old_stem}.pdf"
    new_pdf = new_container / f"{final}.pdf"
    if old_pdf.exists():
        old_pdf.replace(new_pdf)  # 껍데기가 남긴 같은 이름 PDF(동일 논문)는 덮어쓴다
    new_wd = WorkDir(new_md4)
    if new_wd.meta_json.exists():  # PDF 절대경로 갱신 (뷰어 대조가 안 깨지게)
        try:
            meta = json.loads(new_wd.meta_json.read_text(encoding="utf-8"))
            if str(meta.get("source", "")).lower().endswith(".pdf"):
                meta["source"] = str(new_pdf)
                new_wd.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    from narani.ui import tabstore  # 열어 둔 논문 탭이 옮겨진 폴더를 따라가게 (nicegui 불필요)

    tabstore.rename_tab(old_md4, new_md4)
    return new_wd


def set_hidden(root: Path, hidden: bool = True) -> bool:
    """논문을 목록에서 숨기거나 되돌린다 — 파일은 그대로 두고 status.json에 표시만 남긴다.

    삭제는 되돌릴 수 없지만 이건 표시만 지우는 안전한 정리 수단이다(목록이 길어졌을 때).
    """
    wd = WorkDir(Path(root))
    if not wd.root.is_dir():
        return False
    try:
        status = wd.load_status()
        if hidden:
            status["hidden"] = True
        else:
            status.pop("hidden", None)
        wd.save_status(status)
    except (OSError, ValueError):
        return False
    return True


def set_left_panel(root: Path, mode: str | None) -> bool:
    """논문별로 마지막에 펴 둔 왼쪽 작업 패널(목차·구조·번역·설정·메모, None = 접힘)을 status.json에 적는다.

    앱을 다시 켜도 그 논문은 그 패널로 열린다. 읽던 자리처럼 보는 쪽 사정이지만, 패널은 논문마다
    하는 일(구조 손보기·번역 확인·메모)이 달라 논문에 붙여 둔다. 고정처럼 폴더 수정시각은 되돌린다.
    """
    wd = WorkDir(Path(root))
    if not wd.status_json.exists():
        return False
    try:
        before = wd.root.stat()
        status = wd.load_status()
        if "left_panel" in status and status["left_panel"] == mode:
            return True
        status["left_panel"] = mode
        wd.save_status(status)
        try:
            os.utime(wd.root, (before.st_atime, before.st_mtime))
        except OSError:
            pass
    except (OSError, ValueError):
        return False
    return True


def left_panel(root: Path) -> str | None:
    """set_left_panel로 적어 둔 패널. 적힌 적이 없으면 '?'(처음 여는 논문 — 기본 패널을 고른다)."""
    try:
        status = WorkDir(Path(root)).load_status()
    except (OSError, ValueError):
        return "?"
    return status.get("left_panel", "?")


def set_pinned(root: Path, pinned: bool = True) -> bool:
    """논문을 목록 위에 고정하거나 해제한다 (status.json의 pinned_at).

    고정한 논문은 홈 목록 맨 위의 칩으로, 리뷰 화면 헤더의 탭으로 나온다 — 자주 오가는
    논문을 최근순 목록에서 매번 찾지 않도록. 고정 시각을 적어 두는 이유는 **탭 순서를
    고정한 순서로** 안정시키기 위해서다(제목·수정시각이 바뀌어도 자리가 튀지 않는다).
    """
    wd = WorkDir(Path(root))
    if not wd.root.is_dir():
        return False
    try:
        before = wd.root.stat()
        status = wd.load_status()
        if pinned:
            status.setdefault("pinned_at", time.time())  # 이미 고정돼 있으면 순서를 유지
        else:
            status.pop("pinned_at", None)
        wd.save_status(status)
        try:  # 고정은 '작업'이 아니다 → 폴더 수정시각을 되돌려 최근순 목록을 흔들지 않는다
            os.utime(wd.root, (before.st_atime, before.st_mtime))
        except OSError:
            pass
    except (OSError, ValueError):
        return False
    return True


def _projects_in(status: dict) -> list[str]:
    """status.json에 적힌 소속 프로젝트 id들 — 순서 유지(첫 항목 = 주 프로젝트), 중복 없음.

    옛 형식(`project` 하나)만 있으면 그 하나로 읽는다. `projects`가 있어도 옛 키 `project`와
    첫 항목이 어긋나면 **옛 앱이 `project`만 고친 것**이다(설치된 앱과 같은 output/을 함께 쓴다)
    → 그쪽이 최신이므로 `project` 하나로 읽는다. 없어진 id인지는 여기서 안 본다(호출자가 거른다).
    """
    one = str(status.get("project") or "")
    many = status.get("projects")
    if not isinstance(many, list):
        return [one] if one else []
    ids = list(dict.fromkeys(str(x) for x in many if x))
    if (ids[0] if ids else "") != one:
        return [one] if one else []
    return ids


def set_projects(root: Path, project_ids) -> bool:  # noqa: ANN001 — iterable[str]
    """이 논문이 속한 프로젝트들을 status.json에 적는다 (빈 목록이면 미분류로).

    논문 하나가 여러 프로젝트에 속할 수 있다 — 첫 항목이 주 프로젝트(이름 규칙·자동 저장처럼
    논문에 걸리는 설정을 따를 곳, §projects). 새 키 `projects`와 함께 옛 키 `project`(첫 항목)도 쓴다:
    설치된 앱(옛 코드)이 같은 output/을 읽으므로 그쪽에서도 주 프로젝트로는 보이게.

    프로젝트 id를 논문 쪽에 두는 이유: 목록을 훑을 때 한 번에 읽히고(status.json은 작다),
    프로젝트 설정 파일이 사라지거나 손으로 지워져도 논문이 사라지지 않는다.
    고정과 마찬가지로 '작업'이 아니므로 폴더 수정시각은 되돌려 최근순 목록을 흔들지 않는다.
    """
    wd = WorkDir(Path(root))
    if not wd.root.is_dir():
        return False
    ids = list(dict.fromkeys(str(x) for x in (project_ids or []) if x))
    try:
        before = wd.root.stat()
        status = wd.load_status()
        if ids:
            status["projects"] = ids
            status["project"] = ids[0]
        else:
            status.pop("projects", None)
            status.pop("project", None)
        wd.save_status(status)
        try:
            os.utime(wd.root, (before.st_atime, before.st_mtime))
        except OSError:
            pass
    except (OSError, ValueError):
        return False
    return True


def projects_of(root: Path) -> list[str]:
    """이 논문에 적힌 프로젝트 id들 (첫 항목 = 주 프로젝트). 없어진 프로젝트인지까지는 안 본다."""
    try:
        return _projects_in(WorkDir(Path(root)).load_status())
    except (OSError, ValueError):
        return []


def add_to_project(root: Path, project_id: str) -> bool:
    """프로젝트에 **더하기** — 이미 속한 곳은 그대로, 새 프로젝트는 뒤에 붙는다(주 프로젝트 유지)."""
    ids = projects_of(root)
    if project_id in ids:
        return Path(root).is_dir()
    return set_projects(root, [*ids, project_id])


def remove_from_project(root: Path, project_id: str) -> bool:
    """프로젝트에서 **빼기** — 다른 소속은 그대로. 주 프로젝트를 빼면 다음 항목이 주가 된다."""
    ids = projects_of(root)
    if project_id not in ids:
        return Path(root).is_dir()
    return set_projects(root, [i for i in ids if i != project_id])


def set_project(root: Path, project_id: str | None) -> bool:
    """이 논문을 프로젝트 **하나에만** 두기 (None이면 미분류로) — 단일 소속 시절 API의 호환 래퍼."""
    return set_projects(root, [project_id] if project_id else [])


def project_of(root: Path) -> str:
    """이 논문의 주 프로젝트 id (소속의 첫 항목, 없으면 빈 문자열). 없어진 프로젝트인지까지는 안 본다."""
    ids = projects_of(root)
    return ids[0] if ids else ""


def is_pinned(root: Path) -> bool:
    """이 논문이 고정돼 있는지 — 그 논문의 status.json만 본다.

    작업 폴더 목록을 훑지 않는 이유: `narani ui <경로>`로 작업 폴더 **밖**의 논문을 열 수도 있어서,
    목록에 없다고 '고정 안 됨'으로 보여 주면 눌러도 상태가 안 바뀌는 단추가 된다.
    """
    try:
        return bool(WorkDir(Path(root)).load_status().get("pinned_at"))
    except (OSError, ValueError):
        return False


def paper_title(md4: Path) -> str:
    """논문 제목 — paper_meta.json(LLM 추출) > sections.yaml의 title > 폴더 이름."""
    import re

    title = md4.stem
    try:
        m = re.search(r"^title:\s*(.+)$", (md4 / "structure" / "sections.yaml").read_text(encoding="utf-8"), re.M)
        if m:
            title = m.group(1).strip().strip("'\"") or title
    except OSError:
        pass
    pm_path = md4 / "paper_meta.json"
    if pm_path.exists():
        try:
            title = json.loads(pm_path.read_text(encoding="utf-8")).get("title") or title
        except (OSError, ValueError):
            pass
    return title


def recent_workdirs(workspace: Path, limit: int = 20, include_hidden: bool = False) -> list[dict]:
    """작업 폴더에서 유효한 .md4 작업 디렉토리를 최근 수정순으로.

    반환 항목: {name, root(Path), title, authors, year, venue, has_ko, opened, hidden, pinned,
    projects, project, mtime} — `projects`는 소속 id 목록(첫 항목 = 주 프로젝트), `project`는 그
    첫 항목(옛 호출자용). 둘 다 정규화 전이라 없어진 id는 호출자가 `projects.normalize_many`로 거른다.
    서지(authors/year/venue)는 paper_meta.json(LLM 추출)이 있으면 채우고, 없으면 빈 값.
    사용자가 숨긴 논문(status.json의 hidden)은 include_hidden일 때만 포함한다.
    고정한 논문(pinned)은 최근 limit 편 밖으로 밀려나도 목록에 남는다 — 고정의 뜻이 그것이다.
    """
    if not workspace.is_dir():
        return []
    import json

    found: list[dict] = []
    for md4 in workspace.rglob("*.md4"):
        if not md4.is_dir() or not (md4 / "structure" / "sections.yaml").exists():
            continue
        title = paper_title(md4)
        authors: list[str] = []
        year = None
        venue = ""
        pm_path = md4 / "paper_meta.json"
        if pm_path.exists():
            try:
                pm = json.loads(pm_path.read_text(encoding="utf-8"))
                authors = pm.get("authors") or []
                year = pm.get("year")
                venue = pm.get("venue") or ""
            except (OSError, ValueError):
                pass
        opened = hidden = False
        pinned_at = 0.0
        member_of: list[str] = []
        st_path = md4 / "status.json"
        if st_path.exists():
            try:
                st = json.loads(st_path.read_text(encoding="utf-8"))
                opened, hidden = bool(st.get("opened_at")), bool(st.get("hidden"))
                pinned_at = float(st.get("pinned_at") or 0.0)
                member_of = _projects_in(st)
            except (OSError, ValueError, TypeError):
                pass
        if hidden and not include_hidden:
            continue
        found.append({
            "name": md4.stem,
            "root": md4,
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "has_ko": WorkDir(md4).ko_md.exists(),  # 번역(이 논문의 언어)이 있는지
            "lang": WorkDir(md4).lang,  # 번역 언어 (단추 이름 KO·JA …)
            "opened": opened,  # 리뷰 화면을 한 번이라도 연 적 있는지 (미열람 표시용)
            "hidden": hidden,  # 사용자가 목록에서 감춘 논문 (파일은 그대로)
            "pinned": bool(pinned_at),  # 사용자가 위에 고정한 논문
            "pinned_at": pinned_at,     # 고정한 순서 (탭·칩 정렬용)
            "projects": member_of,      # 속한 프로젝트 id들 ([] = 미분류; 없어진 id는 호출자가 거른다)
            "project": member_of[0] if member_of else "",  # 주 프로젝트 (옛 호출자용)
            "mtime": md4.stat().st_mtime,
        })
    found.sort(key=lambda d: d["mtime"], reverse=True)
    head = found[:limit]
    # 오래된 논문을 고정해 뒀는데 최근 목록에서 밀려 사라지면 고정이 무의미하다 → 뒤에 붙인다.
    return head + [r for r in found[limit:] if r["pinned"]]

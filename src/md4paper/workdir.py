"""작업 디렉토리(paper.md4/) 레이아웃과 단계별 상태(status.json) 관리.

모든 단계는 이 디렉토리 위의 재개 가능한 순수 함수다. status.json이 단계별 입력 해시를
기록해 재실행 시 변경 없는 단계는 건너뛰고, 상위 단계가 바뀌면 하위를 무효화한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# 단계 순서 — 앞 단계가 바뀌면 뒤 단계 완료 표시가 무효화된다.
STAGES = ("extract", "structure", "assemble", "cite", "translate")


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
    def authors_json(self) -> Path:
        # 구조화된 저자(이름·이메일·소속) — 표기(author_parts) 변경 시 LLM 재호출 없이 재렌더용
        return self.extract / "authors.json"

    @property
    def footnotes_json(self) -> Path:
        # 각주 {번호: 내용} — 본문 위첨자 링크(#fn-N) 호버 툴팁용
        return self.extract / "footnotes.json"

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
        return self.out / "paper.ko.md"

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
        return self.translate / "glossary.yaml"

    @property
    def cache_json(self) -> Path:
        return self.translate / "cache.json"

    @property
    def status_json(self) -> Path:
        return self.root / "status.json"

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
        self.status_json.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

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
        from md4paper import library  # 지연 임포트 — library가 workdir를 임포트한다(순환 방지)

        library.remove_stem(root.stem)  # 라이브러리 파일명은 논문 폴더 이름(root.stem)
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


def recent_workdirs(workspace: Path, limit: int = 20, include_hidden: bool = False) -> list[dict]:
    """작업 폴더에서 유효한 .md4 작업 디렉토리를 최근 수정순으로.

    반환 항목: {name, root(Path), title, authors, year, venue, has_ko, opened, hidden, mtime}
    서지(authors/year/venue)는 paper_meta.json(LLM 추출)이 있으면 채우고, 없으면 빈 값.
    사용자가 숨긴 논문(status.json의 hidden)은 include_hidden일 때만 포함한다.
    """
    if not workspace.is_dir():
        return []
    import json
    import re

    found: list[dict] = []
    for md4 in workspace.rglob("*.md4"):
        if not md4.is_dir() or not (md4 / "structure" / "sections.yaml").exists():
            continue
        title = md4.stem
        try:
            m = re.search(r"^title:\s*(.+)$", (md4 / "structure" / "sections.yaml").read_text(encoding="utf-8"), re.M)
            if m:
                title = m.group(1).strip().strip("'\"") or title
        except OSError:
            pass
        authors: list[str] = []
        year = None
        venue = ""
        pm_path = md4 / "paper_meta.json"
        if pm_path.exists():
            try:
                pm = json.loads(pm_path.read_text(encoding="utf-8"))
                title = pm.get("title") or title
                authors = pm.get("authors") or []
                year = pm.get("year")
                venue = pm.get("venue") or ""
            except (OSError, ValueError):
                pass
        opened = hidden = False
        st_path = md4 / "status.json"
        if st_path.exists():
            try:
                st = json.loads(st_path.read_text(encoding="utf-8"))
                opened, hidden = bool(st.get("opened_at")), bool(st.get("hidden"))
            except (OSError, ValueError):
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
            "has_ko": (md4 / "out" / "paper.ko.md").exists(),
            "opened": opened,  # 리뷰 화면을 한 번이라도 연 적 있는지 (미열람 표시용)
            "hidden": hidden,  # 사용자가 목록에서 감춘 논문 (파일은 그대로)
            "mtime": md4.stat().st_mtime,
        })
    found.sort(key=lambda d: d["mtime"], reverse=True)
    return found[:limit]

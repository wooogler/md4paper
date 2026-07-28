"""웹 UI 컨트롤러 — NiceGUI와 무관한 순수 로직 (테스트 가능).

manifest가 상태다. 편집은 manifest를 변형하고, 저장 시 sections.yaml을 쓰고 en.md를 재조립한다.
CLI와 같은 아티팩트를 읽고 쓴다(별도 데이터 모델 없음).
"""

from __future__ import annotations

import json

from md4paper.ir import Flavor, GlossaryEntry, GlossaryList, Manifest
from md4paper.review import manifest as manifest_io
from md4paper.workdir import WorkDir

LEVEL_OPTIONS = [1, 2, 3, 4, 5, 6, "skip", "merge-up", "drop"]
# run-in 헤더는 italic(이탤릭 표기)도 고를 수 있다
RUNIN_LEVEL_OPTIONS = [1, 2, 3, 4, 5, 6, "italic", "skip", "merge-up", "drop"]
SETTING_FIELDS = (
    "citation_parts", "reference_links", "flavor", "caption_style", "runin_headings",
    "korean_style", "translate_headers", "translate_references",
)


def _format_reference(r) -> str:  # noqa: ANN001 — RefEntry
    """RefEntry → 사람이 읽는 한 줄 서지정보 (호버 툴팁 본문, URL 제외). 필드가 비면 원문으로."""
    authors = ", ".join(r.authors[:6]) + (" et al." if len(r.authors) > 6 else "")
    head = authors
    if r.year:
        head = f"{head} ({r.year})" if head else f"({r.year})"
    parts = [p for p in (head, r.title.strip(" ."), r.venue.strip(" .")) if p]
    return ". ".join(parts).strip() or r.raw.strip()


class UIController:
    """paper.md4/ 작업 디렉토리 위의 리뷰 상태."""

    def __init__(self, wd: WorkDir) -> None:
        self.wd = wd
        self.manifest: Manifest = manifest_io.load(wd)

    # --- 섹션 편집 ---
    def section(self, sid: str):
        return next((s for s in self.manifest.sections if s.id == sid), None)

    def set_level(self, sid: str, level) -> None:
        sec = self.section(sid)
        if sec is None:
            return
        sec.level = int(level) if str(level).isdigit() else level
        sec.needs_review = False  # 사용자가 확정함

    # --- 일괄 레벨 조정 (섹션 트리와 같은 선택지) ---
    @staticmethod
    def group_key(sec) -> tuple[str, int]:  # noqa: ANN001 — ManifestSection
        """같은 모양의 헤더끼리 묶는 키 — (번호 체계, 깊이). 제목·run-in은 별도."""
        if sec.is_title:
            return ("title", 0)
        if sec.runin:
            return ("run-in", 0)
        if sec.detected is not None:
            return (sec.detected.scheme.value, max(sec.detected.depth or 1, 1))
        return ("none", 1)

    def level_groups(self) -> list[dict]:
        """일괄 조정 UI가 보여줄 그룹 목록 (문서에 실제로 있는 것만).

        각 그룹: key/scheme/depth/count/examples/titles/current
        (current: 전부 같은 레벨이면 그 값, 섞였으면 None. titles: 그룹의 모든 섹션 제목 — 툴팁용)
        """
        order = ["title", "dotted-arabic", "roman", "letter", "unnumbered", "none", "run-in"]
        buckets: dict[tuple[str, int], dict] = {}
        for s in self.manifest.sections:
            k = self.group_key(s)
            g = buckets.setdefault(k, {"count": 0, "examples": [], "titles": [], "levels": set()})
            g["count"] += 1
            g["titles"].append(s.text)
            if len(g["examples"]) < 2:
                g["examples"].append(s.text[:38])
            g["levels"].add(s.level)

        out = []
        for (scheme, depth), g in buckets.items():
            out.append({
                "key": f"{scheme}:{depth}",
                "scheme": scheme,
                "depth": depth,
                "count": g["count"],
                "examples": g["examples"],
                "titles": g["titles"],  # 그룹의 모든 섹션 제목 (툴팁에 전체 표시)
                "current": next(iter(g["levels"])) if len(g["levels"]) == 1 else None,
            })
        out.sort(key=lambda d: (order.index(d["scheme"]) if d["scheme"] in order else 99, d["depth"]))
        return out

    def apply_group_level(self, key: str, level) -> int:  # noqa: ANN001 — LevelValue
        """같은 모양의 헤더들에 같은 처리를 일괄 적용. 반환: 변경된 헤더 수."""
        scheme, _, depth_s = key.partition(":")
        depth = int(depth_s or 0)
        value = int(level) if str(level).isdigit() else level
        n = 0
        for s in self.manifest.sections:
            if self.group_key(s) == (scheme, depth):
                s.level = value
                s.needs_review = False
                n += 1
        return n

    # --- 번역 범위 선택 (Tree Select) ---
    def translatable_sections(self) -> list:
        """번역 선택 대상 — 실제 헤더로 남는 본문 섹션만.

        skip/merge-up/drop과 문서 제목(h1)은 제외한다. 제목은 '섹션'이 아니라 논문 전체의
        이름이라 트리에 체크박스로 두면 의미가 애매하다 → 별도 토글(set_translate_title)로 뺀다.
        """
        return [s for s in self.manifest.sections if isinstance(s.level, int) and not s.is_title]

    def title_section(self):
        """문서 제목 섹션 (h1). 없으면 None."""
        return next((s for s in self.manifest.sections if s.is_title), None)

    def translate_title(self) -> bool:
        sec = self.title_section()
        return bool(sec.translate) if sec is not None else False

    def set_translate_title(self, value: bool) -> None:
        sec = self.title_section()
        if sec is not None:
            sec.translate = bool(value)

    def section_tree(self) -> list[dict]:
        """레벨로 중첩한 트리 노드 (NiceGUI ui.tree용)."""
        root: list[dict] = []
        stack: list[tuple[int, list[dict]]] = []
        for s in self.translatable_sections():
            node = {"id": s.id, "label": s.text, "children": []}
            while stack and stack[-1][0] >= int(s.level):
                stack.pop()
            (stack[-1][1] if stack else root).append(node)
            stack.append((int(s.level), node["children"]))
        return root

    def ticked_ids(self) -> list[str]:
        """현재 번역 대상으로 체크된 섹션 id."""
        return [s.id for s in self.translatable_sections() if s.translate]

    def set_translate_ids(self, ids) -> None:  # noqa: ANN001 — iterable[str]
        """체크된 id 집합으로 섹션별 번역 여부를 갱신."""
        chosen = set(ids or [])
        for s in self.translatable_sections():
            s.translate = s.id in chosen

    def set_all_translate(self, value: bool) -> None:
        for s in self.translatable_sections():
            s.translate = value

    # --- 문서 설정 ---
    def set_setting(self, field: str, value) -> None:
        if field not in SETTING_FIELDS:
            raise ValueError(f"알 수 없는 설정: {field}")
        if field == "flavor":
            value = Flavor(value)
        elif field in ("reference_links", "translate_headers", "translate_references"):
            value = bool(value)
        setattr(self.manifest, field, value)

    def set_author_parts(self, parts: list) -> bool:  # noqa: ANN001
        """저자 표기 요소(email/affiliation) 변경. 저장된 구조화 저자로 raw.md 저자 블록만 재렌더.

        반환: 실제로 재렌더했는지. 구조화 저자(authors.json)가 없으면 매니페스트 값만 갱신하고 False
        (그 논문은 재변환해야 반영). LLM 재호출은 하지 않는다.
        """
        from md4paper.extract.front_matter import _render_authors_detail
        from md4paper.ir import AuthorEntry

        new_parts = [p for p in ("email", "affiliation") if p in parts]
        old_parts = list(self.manifest.author_parts)
        self.manifest.author_parts = new_parts
        if old_parts == new_parts or not self.wd.authors_json.exists():
            return False
        try:
            data = json.loads(self.wd.authors_json.read_text(encoding="utf-8"))
            entries = [AuthorEntry(**d) for d in data]
        except (OSError, ValueError, TypeError):
            return False
        if not entries:
            return False
        raw = self.wd.raw_md.read_text(encoding="utf-8")
        old_block = _render_authors_detail(entries, old_parts)
        if old_block and old_block in raw:
            new_block = _render_authors_detail(entries, new_parts)
            self.wd.raw_md.write_text(raw.replace(old_block, new_block, 1), encoding="utf-8")
            return True
        return False  # 원본 블록을 못 찾음(수동 편집 등) → 매니페스트만 갱신

    # --- 저장 / 렌더 ---
    def save(self) -> tuple[int, int]:
        """매니페스트 저장 + 헤더 이름별 선택 학습. 반환: (기억, 잊음) 수."""
        from md4paper import prefs

        manifest_io.save(self.manifest, self.wd)
        return prefs.sync(self.manifest.sections)

    def save_and_reassemble(self) -> None:
        """sections.yaml 저장 후 en.md 재조립 (프리뷰 갱신). 수동 편집은 덮어써진다."""
        from md4paper import pipeline

        self.save()
        pipeline.run_assemble(self.wd, force=True)
        self.clear_manual_edit()

    def rerender(self) -> None:
        """메모리 manifest로 en.md만 다시 렌더 (yaml 저장·학습 없이 — 라이브 프리뷰용).

        레벨/일괄조정/insert 등을 바꾸면 즉시 프리뷰에 반영하기 위한 가벼운 경로.
        인용 링크는 파싱돼 있으면 LLM 없이 다시 적용한다.
        """
        from md4paper.assemble import render

        raw = self.wd.raw_md.read_text(encoding="utf-8")
        render.assemble(raw, self.manifest, self.wd)
        self.reapply_citations()
        self.clear_manual_edit()

    def section_map(self) -> list[dict]:
        """sections.map.json — 렌더된 헤더의 id·레벨·출력 라인 (프리뷰 앵커·스크롤용)."""
        if not self.wd.sections_map.exists():
            return []
        return json.loads(self.wd.sections_map.read_text(encoding="utf-8")).get("sections", [])

    def en_markdown(self) -> str:
        return self.wd.en_md.read_text(encoding="utf-8") if self.wd.en_md.exists() else ""

    # --- 인용 / 참고문헌 ---
    def has_parsed_refs(self) -> bool:
        return self.wd.references_json.exists()

    def parse_references(self, provider) -> dict:
        """참고문헌을 LLM으로 파싱(1회) + 현재 표기 설정으로 본문 인용 링크 적용."""
        from md4paper import pipeline

        return pipeline.run_cite(
            self.wd, provider,
            parts=self.manifest.citation_parts,
            reference_links=self.manifest.reference_links,
            force=True,
        )

    def reapply_citations(self) -> dict:
        """이미 파싱된 참고문헌으로 인용 표기만 다시 적용 (LLM 없이, 무료).

        표기 설정(citation_parts/reference_links)을 바꾼 뒤 프리뷰에 즉시 반영할 때.
        """
        from md4paper.cite.apply import apply_links, load_cached_refs

        refs = load_cached_refs(self.wd)
        if not refs:
            return {"skipped": True}
        return apply_links(self.wd, refs, self.manifest.citation_parts, self.manifest.reference_links)

    def citation_tooltips(self) -> dict[str, dict]:
        """본문 인용 번호 → {text: 서지정보, url: DOI/arXiv, title: 제목} (프리뷰 호버 툴팁용).

        url은 툴팁에서 새 창으로 열리는 링크, title은 '제목 복사'(구글 스콜라 검색용)에 쓴다.
        파싱 전이면 빈 dict.
        """
        from md4paper.cite.apply import load_cached_refs

        tips: dict[str, dict] = {}
        for r in load_cached_refs(self.wd):
            tips[r.label] = {
                "text": _format_reference(r),
                "url": r.url() or "",
                "title": (r.title or "").strip(),
            }
        return tips

    def footnote_tooltips(self) -> dict[str, str]:
        """각주 번호 → 내용 (본문 위첨자 링크 #fn-N 호버 툴팁용). 없으면 빈 dict."""
        if not self.wd.footnotes_json.exists():
            return {}
        try:
            data = json.loads(self.wd.footnotes_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    # --- 수동 편집 (번역 전 손보기) ---
    def save_en_markdown(self, text: str) -> None:
        """프리뷰에서 직접 고친 마크다운을 저장하고 '수동 편집됨'으로 표시.

        재조립은 raw.md에서 en.md를 다시 만들므로 이 편집을 덮어쓴다 → 플래그로 경고한다.
        """
        self.wd.out.mkdir(parents=True, exist_ok=True)
        self.wd.en_md.write_text(text, encoding="utf-8")
        status = self.wd.load_status()
        status["manual_edit"] = True
        self.wd.save_status(status)

    def has_manual_edit(self) -> bool:
        return bool(self.wd.load_status().get("manual_edit"))

    def clear_manual_edit(self) -> None:
        status = self.wd.load_status()
        if status.pop("manual_edit", None) is not None:
            self.wd.save_status(status)

    def ko_markdown(self) -> str:
        return self.wd.ko_md.read_text(encoding="utf-8") if self.wd.ko_md.exists() else ""

    # --- 용어집 (번역 전 검토/수정 흐름) ---
    def glossary_entries(self) -> list[GlossaryEntry]:
        from md4paper.translate import glossary

        return glossary.load(self.wd).entries if self.wd.glossary_yaml.exists() else []

    def save_glossary(self, entries: list[GlossaryEntry]) -> None:
        from md4paper.translate import glossary

        glossary.save(GlossaryList(entries=entries), self.wd)

    def generate_glossary(self, provider) -> list[GlossaryEntry]:
        """LLM으로 용어집 자동 생성 + 저장 (초록·서론 전문 + 섹션 제목 기반). 생성된 항목 반환."""
        from md4paper.translate import context, glossary

        source = context.extract_glossary_source(self.wd, self.manifest)
        gloss = glossary.generate(provider, self.manifest.title, source)
        glossary.save(gloss, self.wd)
        return gloss.entries

    def selected_sections_text(self, cap: int = 40000) -> str:
        """번역 대상으로 체크된 섹션들의 본문 텍스트 (용어 추가용)."""
        ticked = set(self.ticked_ids())
        if not ticked:
            return ""
        smap = sorted(self.section_map(), key=lambda e: e["out_line"])
        lines = self.en_markdown().splitlines()
        parts: list[str] = []
        for idx, sec in enumerate(smap):
            if sec["id"] not in ticked:
                continue
            start = sec["out_line"]
            end = smap[idx + 1]["out_line"] if idx + 1 < len(smap) else len(lines)
            parts.append("\n".join(lines[start:end]).strip())
        return "\n\n".join(p for p in parts if p).strip()[:cap]

    def new_terms_from_selected(self, provider, existing_terms: list[str]) -> list[GlossaryEntry]:
        """선택 섹션 본문에서 existing_terms에 없는 새 용어를 뽑아 반환 (저장 안 함 — 편집 사본에 추가용)."""
        from md4paper.translate import glossary

        text = self.selected_sections_text()
        if not text:
            return []
        return list(glossary.extend(provider, self.manifest.title, text, existing_terms).entries)

    def translate(self, provider, korean_style: str | None = None, on_progress=None) -> dict:
        """현재 용어집·설정으로 번역 실행 → paper.ko.md."""
        from md4paper import pipeline

        return pipeline.run_translate(self.wd, provider, korean_style=korean_style, on_progress=on_progress)

    def export_zip(self, which: str, target: str = "universal") -> tuple[str, bytes]:
        """en/ko 마크다운 + 참조 이미지를 zip 바이트로 묶어 반환. 반환: (파일명, bytes).

        target(universal/notion/obsidian)에 맞춰 마크다운을 변환해 담는다(이미지 임베드·인용 표기).
        zip 구조는 <이름>/<이름>.md + <이름>/images/* 로 통일, 실제 참조된 이미지만 담는다.
        """
        import io
        import zipfile

        if not (self.wd.en_md if which == "en" else self.wd.ko_md).exists():
            raise FileNotFoundError("마크다운이 없습니다 — 먼저 변환/번역하세요.")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            top = _zip_add_paper(z, self.wd, which, target)
        return f"{top}.zip", buf.getvalue()

    def export(self, dest_dir: str, target: str = "universal") -> list[str]:
        """결과물(en/ko 마크다운 + 이미지)을 지정 위치로 복사. target 형식으로 변환해 쓴다."""
        import shutil
        from pathlib import Path

        from md4paper.export_format import to_export_target

        dest = Path(dest_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        ref_urls = _ref_urls(self.wd) if target == "notion" else None
        copied: list[str] = []
        for src in (self.wd.en_md, self.wd.ko_md):
            if src.exists():
                (dest / src.name).write_text(
                    to_export_target(src.read_text(encoding="utf-8"), target, ref_urls), encoding="utf-8")
                copied.append(str(dest / src.name))
        if self.wd.out_images.is_dir() and any(self.wd.out_images.iterdir()):
            img_dest = dest / "images"
            if img_dest.exists():
                shutil.rmtree(img_dest)
            shutil.copytree(self.wd.out_images, img_dest)
            copied.append(str(img_dest))
        return copied

    # --- PDF 대조 ---
    def source_pdf(self) -> str | None:
        if not self.wd.meta_json.exists():
            return None
        meta = json.loads(self.wd.meta_json.read_text(encoding="utf-8"))
        src = meta.get("source")
        return src if src and src.lower().endswith(".pdf") else None

    def pdf_page_count(self) -> int:
        pdf = self.source_pdf()
        if not pdf:
            return 0
        from md4paper import pdfio

        return pdfio.page_count(pdf)

    def pdf_page_png(self, page: int = 0, zoom: float = 1.5) -> bytes | None:
        """PDF 한 페이지를 PNG 바이트로 렌더 (원본 대조용). 없으면 None."""
        pdf = self.source_pdf()
        if not pdf:
            return None
        from md4paper import pdfio

        try:
            return pdfio.render_page_png(pdf, page, zoom=zoom)
        except Exception:  # noqa: BLE001 — 손상 PDF 등
            return None


def _ref_urls(wd: WorkDir) -> dict[str, str]:
    """참고문헌 번호 → DOI/arXiv URL (Notion 인용을 외부 링크로 걸 때 사용). 없으면 빈 dict."""
    from md4paper.cite.apply import load_cached_refs

    return {r.label: r.url() for r in load_cached_refs(wd) if r.url()}


def _zip_add_paper(z, wd: WorkDir, which: str, target: str = "universal") -> str | None:  # noqa: ANN001
    """워크디렉토리의 마크다운(대상 형식 변환)+참조 이미지를 z에 <stem>-<which>/ 아래로 추가.

    top 폴더명 반환(md 없으면 None). 모든 target 공통 구조 <이름>-<which>/ + images/.
    (Notion은 이 zip을 넣으면 content가 정상 import된다. images 하위 폴더가 빈 페이지로 하나 생기지만
    수동 삭제하면 된다 — '루트 이미지'나 '같은 이름 폴더'로 바꾸면 오히려 content import가 깨졌다.)
    obsidian은 임베드에 논문 폴더(top) 접두를 붙여 vault 내 유일 경로로 만든다(여러 논문 한 vault에서 충돌 방지).
    """
    import re

    from md4paper.export_format import to_export_target

    md_path = wd.en_md if which == "en" else wd.ko_md
    if not md_path.exists():
        return None
    ref_urls = _ref_urls(wd) if target == "notion" else None
    md = to_export_target(md_path.read_text(encoding="utf-8"), target, ref_urls)
    stem = wd.root.stem
    top = f"{stem}-{which}"
    # 참조 이미지 파일명(basename) — universal ![](images/x)·obsidian ![[images/x]] 모두 images/ 포함(접두 전)
    referenced = set(re.findall(r"images/([^\s)\"'\]]+)", md))
    if target == "obsidian":  # ![[images/x]] → ![[<top>/images/x]] (vault-relative 유일 경로)
        md = md.replace("![[images/", f"![[{top}/images/")
    z.writestr(f"{top}/{stem}.{which}.md", md)
    if wd.out_images.is_dir():
        for img in sorted(wd.out_images.iterdir()):
            if img.is_file() and (not referenced or img.name in referenced):
                z.write(img, f"{top}/images/{img.name}")
    return top


def bulk_zip(roots, which: str = "en", target: str = "universal") -> tuple[str, bytes]:
    """여러 워크디렉토리의 마크다운(대상 형식)+이미지를 한 zip으로 (각 논문은 자기 폴더)."""
    import io
    import zipfile
    from pathlib import Path

    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root in roots:
            if _zip_add_paper(z, WorkDir(Path(root)), which, target):
                n += 1
    return f"md4paper-{n}편-{which}.zip", buf.getvalue()

"""웹 UI 컨트롤러 — NiceGUI와 무관한 순수 로직 (테스트 가능).

manifest가 상태다. 편집은 manifest를 변형하고, 저장 시 sections.yaml을 쓰고 en.md를 재조립한다.
CLI와 같은 아티팩트를 읽고 쓴다(별도 데이터 모델 없음).
"""

from __future__ import annotations

import json
from pathlib import Path

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


def citation_tooltips(wd: WorkDir) -> dict[str, dict]:
    """본문 인용 번호 → {text: 서지정보, url: DOI/arXiv, title: 제목} (프리뷰 호버 툴팁용).

    url은 툴팁에서 새 창으로 열리는 링크, title은 '제목 복사'(구글 스콜라 검색용)에 쓴다.
    파싱 전이면 빈 dict. 섹션 정보가 필요 없어 모바일 HTML(§mobile)도 이걸 바로 쓴다.
    """
    from md4paper.cite.apply import load_cached_refs

    tips: dict[str, dict] = {}
    for r in load_cached_refs(wd):
        tips[r.label] = {
            "text": _format_reference(r),
            "url": r.url() or "",
            "title": (r.title or "").strip(),
        }
    return tips


def footnote_tooltips(wd: WorkDir) -> dict[str, str]:
    """각주 번호 → 내용 (본문 위첨자 링크 #fn-N 호버 툴팁용). 없으면 빈 dict."""
    if not wd.footnotes_json.exists():
        return {}
    try:
        data = json.loads(wd.footnotes_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


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
            at = raw.index(old_block)
            new_raw = raw.replace(old_block, new_block, 1)
            self.wd.raw_md.write_text(new_raw, encoding="utf-8")
            delta = new_block.count("\n") - old_block.count("\n")
            if delta:
                self._shift_line_anchors(raw.count("\n", 0, at), delta, new_raw)
            return True
        return False  # 원본 블록을 못 찾음(수동 편집 등) → 매니페스트만 갱신

    def _shift_line_anchors(self, after_line: int, delta: int, new_raw: str) -> None:
        """raw.md 줄 수가 바뀌었으니 라인 앵커(매니페스트·blocks.json)를 함께 민다.

        렌더러는 raw.md의 **절대 줄 번호**로 섹션을 찾는다(render_markdown의 by_line).
        안 밀면 헤더가 엉뚱한 줄에 붙어, 원본 헤더는 그대로 남고 매니페스트 헤더가 따로 찍혀
        문서 전체가 중복·뒤섞인다(이메일 표기를 끄면 저자당 한 줄씩 줄어 실제로 발생했다).
        """
        from md4paper.workdir import hash_text

        for s in self.manifest.sections:
            if s.line > after_line:
                s.line += delta
        if not self.wd.blocks_json.exists():
            return
        try:
            blocks = json.loads(self.wd.blocks_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # 앵커 파일이 깨졌으면 건드리지 않는다 (재변환이 정답)
        for item in blocks.get("headings", []):
            if isinstance(item.get("line"), int) and item["line"] > after_line:
                item["line"] += delta
        for item in blocks.get("figures", []):
            for field in ("image_line", "caption_line"):
                if isinstance(item.get(field), int) and item[field] > after_line:
                    item[field] += delta
            item["consumed_lines"] = [
                ln + delta if ln > after_line else ln for ln in item.get("consumed_lines", [])
            ]
        blocks["n_lines"] = len(new_raw.splitlines())
        blocks["raw_md_hash"] = hash_text(new_raw)
        self.wd.blocks_json.write_text(
            json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 레이아웃 자동 수정 (깨진 헤더·수식·문단을 LLM으로 훑어 고치기) ---
    def layout_fix_plan(self) -> dict:
        """수정 규모 미리보기 {chunks, chars} — 모달에 보여줄 값. LLM은 부르지 않는다."""
        from md4paper import relayout

        raw = self.wd.raw_md.read_text(encoding="utf-8") if self.wd.raw_md.exists() else ""
        return {"chunks": len(relayout.split_for_fix(raw)), "chars": len(raw)}

    def fix_layout(self, provider, instructions: str = "", on_progress=None) -> dict:  # noqa: ANN001
        """raw.md의 깨진 레이아웃을 LLM으로 수정 → 구조 재구축 → en.md 재조립.

        구조를 다시 잡으므로 섹션 트리·일괄 레벨 조정이 고친 문서를 그대로 따라간다
        (문서 설정과 사용자가 정한 레벨·번역 여부는 이어받는다). on_progress(done, total).
        """
        from md4paper import relayout

        summary = relayout.run(self.wd, provider, instructions=instructions,
                               manifest=self.manifest, on_progress=on_progress)
        if summary.get("changed"):
            self._adopt(manifest_io.load(self.wd))
            self.clear_manual_edit()  # en.md를 새로 조립했으므로 직접 편집분은 사라졌다
        return summary

    # --- 재변환 (원본 PDF에서 추출부터 다시) ---
    def extract_outdated(self) -> bool:
        """지금 추출기보다 옛 버전으로 뽑은 논문인가 (재변환을 권할 근거)."""
        from md4paper import pipeline

        return pipeline.extract_outdated(self.wd)

    def reconvert(self, provider=None, on_progress=None) -> dict:  # noqa: ANN001
        """원본 PDF에서 다시 변환 — 추출 → 수식 → 앞부분 → 구조 → 조립 → 인용.

        추출기가 나아진 것(굵게·기울임, 띄어쓰기 …)을 이미 변환한 논문에 반영하는 길이다.
        구조는 새 raw.md에서 다시 잡되, 문서 설정과 사용자가 정한 레벨·번역 여부는 레이아웃 수정처럼
        헤더 이름으로 이어받는다. 직전 상태는 스냅샷으로 남겨 되돌릴 수 있다(레이아웃 수정과 같은 되돌리기).
        번역(ko.md)은 건드리지 않는다 — 다시 번역해야 새 원문이 반영된다. on_progress(done, total).
        """
        from md4paper import pipeline, relayout

        pdf = self.source_pdf()
        if not pdf or not Path(pdf).exists():
            raise FileNotFoundError("원본 PDF를 찾을 수 없어 다시 변환할 수 없습니다.")
        meta = json.loads(self.wd.meta_json.read_text(encoding="utf-8"))
        old = self.manifest
        steps = 5
        progress = on_progress or (lambda done, total: None)

        relayout.snapshot(self.wd)  # 되돌리기 1단계 — 레이아웃 수정과 같은 자리
        progress(0, steps)
        new_meta = pipeline.run_extract(Path(pdf), self.wd, backend=meta.get("backend") or "docling",
                                        ocr=bool(meta.get("ocr")), force=True)
        progress(1, steps)
        pipeline.run_formulas(self.wd, provider=provider)
        progress(2, steps)
        pipeline.run_frontmatter(self.wd, provider=provider)
        progress(3, steps)
        relayout.rebuild(self.wd, old, provider=provider)  # 구조 재구축 + 설정·레벨 이어받기 + 조립
        progress(4, steps)
        self._adopt(manifest_io.load(self.wd))
        self.reapply_citations()  # 파싱해 둔 참고문헌으로 인용 링크 다시 (LLM 없이)
        self.clear_manual_edit()
        progress(5, steps)
        return {"sections": len(self.manifest.sections), "emphasis": new_meta.get("emphasis", 0),
                "garbled": new_meta.get("garbled_chars", 0), "has_translation": bool(self.ko_markdown())}

    # --- 수식 한 개 다시 읽기 (캔버스 수식 팝오버) ---
    def formula_marks(self) -> tuple[dict[str, str], dict]:
        """화면 표식용 (블록 원문 → 레코드 id, 인라인 (원문, 순위) → 번호) — §formula_fix.tag."""
        from md4paper import formula_fix, formulas

        en = self.en_markdown()
        records = formulas.load(self.wd)
        return (formula_fix.assign(en, records) if records else {}), formula_fix.inline_index(en)

    def formula_mapping(self) -> dict[str, str]:
        return self.formula_marks()[0]

    def regenerate_formula(self, fid: str, provider, page: int | None = None, rect=None) -> dict:  # noqa: ANN001
        from md4paper import formula_fix

        return formula_fix.regenerate(self.wd, fid, provider, page=page, rect=rect)

    def apply_formula(self, fid: str, latex: str, kind: str, page: int | None = None, rect=None,  # noqa: ANN001
                      expect: str | None = None) -> dict:
        """다시 읽은 수식을 반영 — 원천(raw.md, 레코드면 formulas.json·크롭도) → en.md 재조립 → ko.md 같은 자리.

        직접 편집한 en.md가 있으면 재조립하지 않고(편집이 사라진다) en.md의 그 자리만 바꾼다.
        ko.md에서도 같은 자리를 바꾸면 번역 캐시 키를 새 원문으로 옮겨 그 섹션을 '원문 바뀜'으로
        잡지 않는다. ko.md에서 못 찾으면 캐시를 그대로 둬, 그 섹션이 '원문 바뀜'으로 잡힌다.
        인라인 수식(`inline-N`)은 레코드가 없어 raw.md·en.md·ko.md의 같은 글자 중 같은 순번 자리를 바꾼다.
        """
        from md4paper import formula_fix, formulas

        en_before = self.en_markdown()
        raw_before = self.wd.raw_md.read_text(encoding="utf-8")
        n = formula_fix._inline_n(fid)
        if n is not None:
            text, rank, total = formula_fix.inline_target(en_before, n, expect)
            latex = formula_fix._checked(latex, "inline" if kind != "text" else "text")
            new_text = formula_fix.inline_text(latex, kind)
            raw = self.wd.raw_md.read_text(encoding="utf-8")
            where = formula_fix.nth_same(raw, text, rank, total)
            if where is None:
                raise ValueError("원문(raw.md)에서 이 수식 자리를 하나로 특정하지 못했습니다.")
            self.wd.raw_md.write_text(formula_fix.replace_at(raw, where, new_text), encoding="utf-8")

            def patch(md: str) -> str | None:
                at = formula_fix.nth_same(md, text, rank, total)
                return None if at is None else formula_fix.replace_at(md, at, new_text)
            en_old = text
            en_at = formula_fix.inline_spans(en_before)[n][0]
        else:
            rec = next((r for r in formulas.load(self.wd) if r.get("id") == fid), None)
            if rec is None:
                raise ValueError(f"수식 {fid}를 찾지 못했습니다.")
            en_old = formula_fix.locate(en_before, rec)
            en_at = formula_fix.line_position(en_before, en_old) if en_old else None
            new_text = formula_fix.apply(self.wd, fid, latex, kind, page=page, rect=rect)["new"]

            def patch(md: str) -> str | None:
                return formula_fix.patch_text(md, rec, new_text)
        self._follow_raw_lines(raw_before)
        return self._after_formula(en_before, en_old, en_at, new_text, patch)

    def _follow_raw_lines(self, raw_before: str) -> None:
        """여러 줄 수식(aligned)이 한 줄이 되는 등 raw.md 줄 수가 바뀌었으면 라인 앵커를 민다.

        섹션은 raw.md의 절대 줄 번호에 붙어 있어서, 안 밀면 재조립 때 그 뒤 헤더가 통째로 중복된다(실측).
        """
        raw = self.wd.raw_md.read_text(encoding="utf-8")
        delta = raw.count("\n") - raw_before.count("\n")
        if not delta:
            return
        at = next((i for i, (a, b) in enumerate(zip(raw_before, raw)) if a != b), len(raw_before))
        self._shift_line_anchors(raw_before.count("\n", 0, at), delta, raw)
        manifest_io.save(self.manifest, self.wd)  # 재조립은 sections.yaml을 디스크에서 읽는다

    def _after_formula(self, en_before: str, en_old: str | None, en_at: int | None, new_text: str,
                       patch) -> dict:  # noqa: ANN001
        """원천을 바꾼 뒤 — en.md(재조립 또는 그 자리만) · ko.md 같은 자리 · 번역 캐시 키 옮기기."""
        from md4paper import formula_fix, pipeline
        from md4paper.translate.apply import _build_units

        if self.has_manual_edit():
            patched = patch(en_before)
            if patched is not None:
                self._write_atomic(self.wd.en_md, patched)
        else:
            pipeline.run_assemble(self.wd, force=True)
            self.clear_manual_edit()
        ko_patched = False
        ko = self.ko_markdown()
        if ko:
            new_ko = patch(ko)
            if new_ko is not None:
                self._write_atomic(self.wd.ko_md, new_ko)
                ko_patched = True
        moved = 0
        if ko_patched and en_old and en_at is not None and self.wd.cache_json.exists():
            try:
                stored = json.loads(self.wd.cache_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stored = None
            if isinstance(stored, dict):
                units = _build_units(self.wd, self.manifest, en_before.splitlines())
                sources = [u["source"] for u in units] + [
                    t for u in units for _sid, t in (u.get("sections") or [])]
                chunks = stored.setdefault("chunks", {})
                moved = formula_fix.rekey_cache(chunks, sources, en_before, en_at, en_old, new_text)
                if moved:
                    self.wd.cache_json.write_text(
                        json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"block": new_text, "ko": ko_patched, "cache_moved": moved}

    def can_undo_layout_fix(self) -> bool:
        from md4paper import relayout

        return relayout.has_snapshot(self.wd)

    def undo_layout_fix(self) -> bool:
        """직전 레이아웃 수정을 되돌린다 (raw.md·구조·en.md 원상 복구). 되돌렸으면 True."""
        from md4paper import relayout

        if not relayout.restore(self.wd):
            return False
        self._adopt(manifest_io.load(self.wd))
        self.clear_manual_edit()
        return True

    def _adopt(self, new: Manifest) -> None:
        """새 매니페스트 내용을 기존 객체에 옮겨 담는다.

        UI 클로저들이 manifest 객체를 직접 붙들고 있어(m.sections 등) 참조를 갈아치우면
        섹션 트리가 옛 구조를 계속 그린다 → 같은 객체를 제자리에서 갱신한다.
        """
        for field in type(new).model_fields:
            setattr(self.manifest, field, getattr(new, field))

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
        return citation_tooltips(self.wd)

    def footnote_tooltips(self) -> dict[str, str]:
        return footnote_tooltips(self.wd)

    # --- 수동 편집 (번역 전 손보기) ---
    def save_en_markdown(self, text: str) -> None:
        """프리뷰에서 직접 고친 마크다운을 저장하고 '수동 편집됨'으로 표시.

        재조립은 raw.md에서 en.md를 다시 만들므로 이 편집을 덮어쓴다 → 플래그로 경고한다.
        """
        self._write_atomic(self.wd.en_md, text)
        status = self.wd.load_status()
        status["manual_edit"] = True
        self.wd.save_status(status)

    def save_ko_markdown(self, text: str) -> None:
        """뷰어에서 직접 고친 번역 마크다운을 저장하고 '번역 수동 편집됨'으로 표시.

        번역을 다시 실행하면 ko.md를 번역 결과(캐시 포함)로 다시 쓰므로 이 편집을 덮어쓴다 → 별도
        플래그로 경고한다(원문 재조립은 ko.md를 건드리지 않으므로 en 플래그와 섞지 않는다).
        """
        self._write_atomic(self.wd.ko_md, text)
        status = self.wd.load_status()
        status["manual_edit_ko"] = True
        self.wd.save_status(status)

    # --- 자유 메모 (메모 탭 — 첫 탭은 notes.md, 나머지는 notes/<이름>.md; §md4paper/notes.py) ---
    def notes(self, tab: str | None = None) -> str:
        """메모 탭 내용(마크다운). tab이 없으면 첫 탭. 파일이 없으면 빈 문자열."""
        from md4paper import notes

        return notes.read(self.wd, tab or notes.tab_names(self.wd)[0])

    def save_notes(self, text: str, tab: str | None = None) -> None:
        """메모 패널의 자동 저장 — 원자적으로 쓰고, 비우면 파일을 지운다(빈 껍데기를 남기지 않게)."""
        from md4paper import notes

        notes.write(self.wd, tab or notes.tab_names(self.wd)[0], text)

    def note_tabs(self) -> list[str]:
        from md4paper import notes

        return notes.tab_names(self.wd)

    def _write_atomic(self, path: Path, text: str) -> None:
        import os

        self.wd.out.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓰고 갈아끼운다 — status.json과 같은 이유다(workdir.save_status 참고).
        # 같은 논문을 창 여럿에서 열 수 있고, 분할 편집은 저장을 자주 부르므로 반쯤 쓴 파일이
        # 남을 창이 그만큼 넓어진다. os.replace는 원자적이라 최악이 '한쪽이 덮이는' 정도로 끝난다.
        tmp = path.with_suffix(f".md.tmp{os.getpid()}")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def has_manual_edit(self) -> bool:
        return bool(self.wd.load_status().get("manual_edit"))

    def has_manual_edit_ko(self) -> bool:
        return bool(self.wd.load_status().get("manual_edit_ko"))

    def clear_manual_edit(self) -> None:
        status = self.wd.load_status()
        if status.pop("manual_edit", None) is not None:
            self.wd.save_status(status)

    def clear_manual_edit_ko(self) -> None:
        status = self.wd.load_status()
        if status.pop("manual_edit_ko", None) is not None:
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

        summary = pipeline.run_translate(self.wd, provider, korean_style=korean_style, on_progress=on_progress)
        self.clear_manual_edit_ko()  # ko.md를 번역 결과로 다시 썼다 — 직접 고친 흔적은 이제 없다
        return summary

    def unticked_losing_translation(self) -> set[str]:
        """체크를 푼 섹션 중 보관된 번역이 없어 번역을 실행하면 영어로 바뀔 섹션 id (실행 전 경고용)."""
        from md4paper.translate.apply import unticked_without_cache

        return unticked_without_cache(self.wd, self.manifest)

    def stale_sections(self) -> set[str]:
        """원문이 바뀌어 다음 번역 실행 때 다시 번역될 섹션 id (번역 패널의 '원문 바뀜' 표시)."""
        from md4paper.translate.apply import stale_section_ids

        return stale_section_ids(self.wd, self.manifest)

    def translate_model(self) -> str:
        """번역 모델 드롭다운의 기본값 'provider/model' — 이 논문을 마지막에 번역한 모델, 없으면 전역 기본."""
        from md4paper import config, pipeline

        last = pipeline.last_translate_model(self.wd)
        if last is not None:
            return "/".join(last)
        provider = config.resolve_provider()
        return f"{provider}/{config.resolve_model(provider)}"

    def translate_model_options(self) -> dict[str, str]:
        """번역 모델 드롭다운 {'provider/model': 라벨}. 키가 있는 제공사만, 각 사 저렴→비쌈.

        지난 번역 모델이 목록 밖(구세대·--model 직접 지정)이어도 기본값으로 보이도록 끼워 넣는다.
        """
        from md4paper import config
        from md4paper.llm.base import PRICING

        keys = config.key_status()
        opts: dict[str, str] = {}
        for prov in config.PROVIDERS:
            if not keys.get(prov):
                continue
            for mid in config.MODEL_TIERS.get(prov, ()):
                pr = PRICING.get(mid)
                opts[f"{prov}/{mid}"] = f"{prov} · {mid}" + (f" (${pr[0]:g}/${pr[1]:g})" if pr else "")
        cur = self.translate_model()
        if cur not in opts:
            opts = {cur: cur.replace("/", " · ", 1), **opts}
        return opts

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

    def section_pages(self) -> dict[str, int]:
        """섹션 id → PDF 페이지(1-based). 구조 단계가 쪽을 못 단 섹션(run-in 소제목 등)은 원본 PDF에서
        제목을 찾아 채운다 — 앞뒤 섹션의 쪽 사이에서만 찾으므로 엉뚱한 쪽의 같은 문구는 잡지 않는다.

        뷰어가 텍스트 스크롤에 PDF를 따라오게 할 때 쓴다. 쪽이 비면 그 섹션은 앞 섹션의 쪽을 따라가서,
        긴 절 안의 소제목들을 읽는 동안 PDF가 몇 쪽씩 뒤처졌다.
        """
        import re

        secs = self.manifest.sections
        out = {s.id: s.page for s in secs if s.page}
        if len(out) == len(secs):
            return out
        if getattr(self, "_page_keys", None) is None:
            from md4paper import pdfio

            pdf = self.source_pdf()
            texts = pdfio.page_texts(pdf) if pdf else []
            self._page_keys = [re.sub(r"[^0-9a-z]+", "", t.lower()) for t in texts]
        keys = self._page_keys
        if not keys:
            return out
        lo = 1
        for i, s in enumerate(secs):
            if s.page:
                lo = s.page
                continue
            hi = next((t.page for t in secs[i + 1:] if t.page), len(keys))
            title = re.sub(r"[^0-9a-z]+", "", re.sub(r"<[^>]+>", "", s.text).lower())
            if len(title) < 4:
                continue
            hit = next((p for p in range(lo, min(hi, len(keys)) + 1) if title in keys[p - 1]), None)
            if hit:
                out[s.id] = lo = hit
        return out

    def pdf_page_sizes(self) -> list[tuple[float, float]]:
        """페이지별 (폭, 높이) pt — 그림이 뜨기 전에도 PDF 패널의 쪽 높이를 잡아 두는 데 쓴다."""
        pdf = self.source_pdf()
        if not pdf:
            return []
        from md4paper import pdfio

        return pdfio.page_sizes(pdf)

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
    from md4paper.cite.apply import ref_urls

    return ref_urls(wd)


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

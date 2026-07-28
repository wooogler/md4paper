"""sections.yaml 방출/재파싱 — 인터랙티브 리뷰의 교환 포맷.

YAML은 사람 중심으로 깔끔하게 유지한다: raw.md 라인 앵커(`line`)는 YAML에 넣지 않고
blocks.json에서 id로 다시 잇는다. 편집 후 저장하면 그 파일이 곧 비대화형 재실행 아티팩트다.
"""

from __future__ import annotations

import io
import json

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from md4paper.ir import Detected, Flavor, Manifest, ManifestSection, Scheme
from md4paper.workdir import WorkDir

_LEGEND = (
    "# md4paper 섹션 매니페스트 — 편집 후 저장하세요.\n"
    "# level: 1-6(헤더 레벨) | skip(헤더 해제, 본문 유지) | merge-up(위에 흡수) | drop(섹션 통째 제거).\n"
    "# needs_review: true 는 번호 감지 실패로 marker 레벨을 그대로 쓴 항목 — 확인 필요.\n"
    "# 각 text 옆 주석(↳)은 뒤따르는 본문 스니펫(참고용).\n"
    "# citation_parts: 조합 가능 목록 [number, authoryear, short] — 예: [number, short] → [1, Transformer]\n"
    "# reference_links: true|false  (참고문헌을 DOI/arXiv 링크로 — 바로 논문 접근)\n"
    "# flavor: standard | obsidian | notion | html  (이미지 임베딩 방식 — 뷰어별)\n"
    "# caption_style: bold-italic | blockquote | italic  (그림·표 캡션 표기)\n"
    "# korean_style: 해라체 | 합니다체 | 해요체 | custom:\"...\"  (번역 문체 — 웹 UI 주요 설정)\n"
)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # 긴 줄 자동 줄바꿈 방지
    return y


def to_yaml(manifest: Manifest) -> str:
    root = CommentedMap()
    root["title"] = manifest.title
    root["citation_parts"] = list(manifest.citation_parts)
    root["reference_links"] = manifest.reference_links
    root["flavor"] = manifest.flavor.value
    root["caption_style"] = manifest.caption_style
    root["runin_headings"] = manifest.runin_headings
    root["korean_style"] = manifest.korean_style
    root["translate_headers"] = manifest.translate_headers
    root["translate_references"] = manifest.translate_references

    secs = CommentedSeq()
    for s in manifest.sections:
        m = CommentedMap()
        m["id"] = s.id
        m["text"] = s.text
        m["level"] = s.level
        m["marker_level"] = s.marker_level
        if s.page is not None:
            m["page"] = s.page  # 원본 PDF 페이지 (섹션↔PDF 대조)
        if s.auto_level is not None and s.auto_level != s.level:
            m["auto_level"] = s.auto_level  # 자동 계산값 (사용자 교정 여부 판단용)
        if s.is_title:
            m["is_title"] = True
        if not s.translate:
            m["translate"] = False  # 번역 제외 섹션
        if s.runin:
            m["runin"] = True
            m["runin_body"] = s.runin_body or ""
        if s.detected is not None:
            m["scheme"] = s.detected.scheme.value
            if s.detected.number:
                m["number"] = s.detected.number
        if s.needs_review:
            m["needs_review"] = True
        if s.context:
            m.yaml_add_eol_comment(f"↳ {s.context}", "text")
        secs.append(m)
    root["sections"] = secs


    stream = io.StringIO()
    _yaml().dump(root, stream)
    return _LEGEND + stream.getvalue()


def save(manifest: Manifest, wd: WorkDir) -> None:
    wd.structure.mkdir(parents=True, exist_ok=True)
    wd.sections_yaml.write_text(to_yaml(manifest), encoding="utf-8")


def _line_map(wd: WorkDir) -> dict[str, int]:
    if not wd.blocks_json.exists():
        return {}
    blocks = json.loads(wd.blocks_json.read_text(encoding="utf-8"))
    return {h["id"]: h["line"] for h in blocks.get("headings", [])}


def load(wd: WorkDir) -> Manifest:
    """sections.yaml → Manifest. raw.md 라인 앵커는 blocks.json에서 id로 복원."""
    data = _yaml().load(wd.sections_yaml.read_text(encoding="utf-8"))
    line_by_id = _line_map(wd)

    sections: list[ManifestSection] = []
    for item in data.get("sections", []):
        sid = str(item["id"])
        scheme = item.get("scheme")
        number = str(item.get("number", ""))
        # depth는 번호에서 복원 ("3.1"→2, "A.1"→2, "II"→1) — 일괄 레벨 규칙이 이 값을 쓴다
        detected = (
            Detected(scheme=Scheme(scheme), number=number, depth=number.count(".") + 1 if number else 1)
            if scheme
            else None
        )
        sections.append(
            ManifestSection(
                id=sid,
                text=str(item["text"]),
                line=line_by_id.get(sid, -1),
                page=item.get("page"),
                marker_level=int(item.get("marker_level", 2)),
                level=item.get("level", 2),
                auto_level=item.get("auto_level", item.get("level", 2)),
                detected=detected,
                needs_review=bool(item.get("needs_review", False)),
                is_title=bool(item.get("is_title", False)),
                translate=bool(item.get("translate", True)),
                runin=bool(item.get("runin", False)),
                runin_body=item.get("runin_body"),
            )
        )


    # 하위호환: 옛 citation_style(단일) → citation_parts
    if data.get("citation_parts"):
        parts = [str(p) for p in data["citation_parts"]]
    elif data.get("citation_style"):
        from md4paper.config import style_to_parts

        parts = style_to_parts(str(data["citation_style"]))
    else:
        parts = ["number"]

    return Manifest(
        title=str(data.get("title", "")),
        citation_parts=parts,
        reference_links=bool(data.get("reference_links", True)),
        flavor=Flavor(data.get("flavor", "standard")),
        caption_style=data.get("caption_style", "bold-italic"),
        runin_headings=data.get("runin_headings", "off"),
        korean_style=str(data.get("korean_style", "해라체")),
        translate_headers=bool(data.get("translate_headers", True)),
        translate_references=bool(data.get("translate_references", False)),
        sections=sections,
    )

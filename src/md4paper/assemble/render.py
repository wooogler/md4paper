"""조립 단계 — 승인된 manifest를 raw.md에 적용해 paper.en.md를 만든다.

헤더는 번호 기반으로 재레벨링, 그림은 안정적 파일명 + 뷰어별 플레이버로 렌더, 캡션은 그림에 흡수.
본문·표·수식은 marker 출력 그대로 통과(graceful, 무손실). 결과물 #1이며 항상 실행 가능하다.
"""

from __future__ import annotations

import json
import re
import shutil

from md4paper.assemble import figures
from md4paper.ir import Flavor, Manifest
from md4paper.workdir import WorkDir

# 베어 파일명 이미지 링크(경로/URL 아님)를 images/ 로 접두 — marker가 짝 못 지은 이미지 링크 수정
_BARE_IMG_RE = re.compile(r"(!\[[^\]]*\]\()(?!https?://|images/|/)([^)/]+)(\))")
_BARE_HTML_IMG_RE = re.compile(
    r'(<img\s[^>]*src=["\'])(?!https?://|images/|/|data:)([^"\'/]+)(["\'])', re.IGNORECASE
)


_REF_KW = {"references", "reference", "bibliography"}
_HTML_TAG = re.compile(r"<[^>]+>")
_ENTRY_SPLIT = re.compile(r"(?=\[\d{1,3}\]\s)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")



def _norm_kw(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _reformat_references(out_lines: list[str], section_map: list[dict]) -> tuple[list[str], list[dict]]:
    """흘려 쓴 참고문헌([1]...[2]...)을 항목별 문단으로 분리하고 span 앵커 제거.

    marker는 참고문헌을 한 문단에 이어 붙이므로 마크다운에서 한 덩어리로 보인다. [N] 경계로 쪼갠다.
    """
    ordered = sorted(section_map, key=lambda e: e["out_line"])
    ref = next((e for e in ordered if _norm_kw(e["text"]) in _REF_KW), None)
    if ref is None:
        return out_lines, section_map
    idx = ordered.index(ref)
    start = ref["out_line"]
    end = ordered[idx + 1]["out_line"] if idx + 1 < len(ordered) else len(out_lines)

    body = _HTML_TAG.sub("", " ".join(line.strip() for line in out_lines[start + 1 : end]))
    body = re.sub(r"\s+", " ", body).strip()
    entries = [e.strip() for e in _ENTRY_SPLIT.split(body) if e.strip()]
    if len(entries) < 2:  # 번호 항목으로 못 나누면 그대로 둔다
        return out_lines, section_map

    new_body: list[str] = [""]
    for e in entries:
        new_body.extend((e, ""))
    new_out = out_lines[: start + 1] + new_body + out_lines[end:]
    delta = len(new_out) - len(out_lines)
    new_map = [
        {**e, "out_line": e["out_line"] + delta} if e["out_line"] >= end else e for e in section_map
    ]
    return new_out, new_map


def _drop_ranges(manifest: Manifest, n_lines: int) -> set[int]:
    """level=='drop' 섹션의 헤더~다음 헤더 직전까지 라인(본문 포함)."""
    ordered = sorted(manifest.sections, key=lambda s: s.line)
    drop: set[int] = set()
    for idx, sec in enumerate(ordered):
        if sec.level == "drop":
            start = sec.line
            end = ordered[idx + 1].line if idx + 1 < len(ordered) else n_lines
            drop.update(range(start, end))
    return drop


def render_markdown(raw_md: str, manifest: Manifest) -> tuple[str, list[dict], dict[str, str]]:
    """raw.md + manifest → (en_md 텍스트, sections.map 항목, 이미지 리네임 맵)."""
    lines = raw_md.splitlines()
    by_line = {s.line: s for s in manifest.sections}

    # en.md는 범용(canonical) 형식으로 저장 — 이미지 임베드/인용 표기의 뷰어별 차이는
    # 다운로드 시 export_format.to_export_target으로 변환한다(프리뷰도 항상 표준으로 렌더됨).
    fig_plan = figures.build_plan(lines, Flavor.STANDARD, manifest.caption_style)
    drop_lines = _drop_ranges(manifest, len(lines))

    out_lines: list[str] = []
    section_map: list[dict] = []
    in_fence = False

    for i, line in enumerate(lines):
        # drop 섹션(헤더+본문)은 통째로 제거
        if i in drop_lines:
            continue
        # 그림에 흡수된 캡션 줄은 버린다
        if i in fig_plan.drop_lines:
            continue
        # 이미지 줄 → 플레이버 블록으로 치환
        if i in fig_plan.image_blocks:
            out_lines.extend(fig_plan.image_blocks[i])
            continue
        # 표(마크다운 그리드) 등 캡션 줄 → caption_style로 재스타일 (섹션 헤더가 아닐 때만)
        if i in fig_plan.replace_lines and i not in by_line:
            out_lines.append(fig_plan.replace_lines[i])
            continue

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue

        sec = by_line.get(i)
        if sec is None:
            out_lines.append(line)
            continue

        level = sec.level
        if sec.runin:
            # run-in 헤더: 매니페스트 레벨에 따라 승격 / 이탤릭 / 원문 유지
            body = sec.runin_body or ""
            if level == "merge-up" or level == "skip":
                out_lines.append(line)  # 원래 줄 그대로 (헤더 아님)
            elif level == "italic":
                out_lines.append(f"*{sec.text}.* {body}")
            else:
                out_lines.append(f"{'#' * int(level)} {sec.text}")
                section_map.append(
                    {"id": sec.id, "level": int(level), "text": sec.text, "out_line": len(out_lines) - 1}
                )
                out_lines.append("")
                out_lines.append(body)
        elif level == "merge-up":
            pass  # 헤더 제거 — 본문에 흡수
        elif level == "italic":
            out_lines.append(f"*{sec.text}*")  # 헤더 아님 → 이탤릭 문단
        elif level == "skip":
            out_lines.append(sec.text)  # 헤더 아님 → 일반 문단
        else:
            out_lines.append(f"{'#' * int(level)} {sec.text}")
            section_map.append(
                {"id": sec.id, "level": int(level), "text": sec.text, "out_line": len(out_lines) - 1}
            )


    # 흘려 쓴 참고문헌을 항목별 문단으로 분리 (span 앵커 제거) — cite 전에도 보기 좋게
    out_lines, section_map = _reformat_references(out_lines, section_map)

    text = "\n".join(out_lines) + "\n"
    # 짝 못 지어 베어 파일명으로 남은 이미지 링크를 images/ 로 접두 (파일은 out/images에 복사됨)
    text = _BARE_IMG_RE.sub(r"\1images/\2\3", text)
    text = _BARE_HTML_IMG_RE.sub(r"\1images/\2\3", text)
    return text, section_map, fig_plan.rename


def assemble(raw_md: str, manifest: Manifest, wd: WorkDir) -> None:
    """en_md 렌더 + 이미지 리네임 복사 + sections.map.json 기록."""
    wd.out.mkdir(parents=True, exist_ok=True)
    text, section_map, rename = render_markdown(raw_md, manifest)
    wd.en_md.write_text(text, encoding="utf-8")

    # 이미지 복사 (안정적 파일명으로 리네임; 매핑에 없으면 원래 이름 유지)
    # 재실행 시 옛 파일명이 남지 않도록 out/images를 새로 만든다.
    if wd.extract_images.is_dir():
        if wd.out_images.exists():
            shutil.rmtree(wd.out_images)
        wd.out_images.mkdir(parents=True, exist_ok=True)
        for img in wd.extract_images.iterdir():
            if img.is_file():
                new_name = rename.get(img.name, img.name)
                shutil.copy2(img, wd.out_images / new_name)

    wd.sections_map.write_text(
        json.dumps({"sections": section_map}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

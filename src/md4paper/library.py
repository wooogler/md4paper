"""변환한 논문이 쌓이는 전역 라이브러리 폴더 — 영어·한국어 마크다운, 원본 PDF를 각각 다른 폴더로.

작업 디렉토리(`<이름>.md4/`)는 원본 PDF·추출 중간물·캐시가 든 '작업장'이라 노트 앱에 그대로
넣을 수 없다. 여기서는 **결과 마크다운(+ 원본 PDF 사본)만** 사용자가 고른 폴더에 복사해 쌓는다:

```
<영어 폴더>/2017_Attention_Vaswani.md
<영어 폴더>/images/2017_Attention_Vaswani/fig-01.jpeg
<한국어 폴더>/2017_Attention_Vaswani.md      ← 같은 이름, 다른 폴더
<PDF 폴더>/2017_Attention_Vaswani.pdf        ← md와 같은 기준명 → md에서 바로 찾아진다
```

- 파일명은 논문 폴더 이름(`wd.root.stem` — 이름 규칙 [output].naming으로 정해짐)이라
  여러 논문을 한 폴더에 쌓아도 충돌하지 않고, md·PDF가 같은 기준명을 공유한다.
- 이미지도 논문별 하위 폴더로 격리하고, 마크다운의 `images/…` 참조를 거기에 맞춰 고쳐 쓴다.
- 같은 논문을 다시 내보내면 덮어쓴다(버전이 쌓이는 게 아니라 논문이 쌓인다).
- 내보내기 형식(범용/Notion/Obsidian)은 다운로드 zip과 같은 전역 설정을 따른다.

폴더 경로는 전역 설정 `~/.config/md4paper/config.toml`의 `[library]`에 있다(§config).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from md4paper import config
from md4paper.workdir import WorkDir

WHICH = config.LIBRARY_WHICH  # ("en", "ko") — 마크다운 언어
KINDS = config.LIBRARY_KINDS  # ("en", "ko", "pdf") — 저장 위치 폴더 종류

# 마크다운 이미지 `![alt](images/x)` · Obsidian 위키 임베드 `![[images/x]]` 둘 다 논문별 폴더로.
# (코드 펜스 안의 'images/' 같은 평범한 텍스트는 건드리지 않도록 임베드 문법을 함께 매치한다.)
_IMG_MD_RE = re.compile(r"(!\[[^\]]*\]\()images/")
_IMG_WIKI_RE = re.compile(r"(!\[\[)images/")
# 실제로 참조된 이미지 파일명 — controller의 zip 내보내기와 같은 관례
_IMG_REF_RE = re.compile(r"images/([^\s)\"'\]]+)")


def dir_for(which: str) -> Path | None:
    """해당 종류(en/ko/pdf)의 라이브러리 폴더 (미설정이면 None)."""
    return config.resolve_library_dir(which)


def configured() -> bool:
    """영어·한국어·PDF 중 하나라도 폴더가 설정돼 있는지."""
    return any(dir_for(k) is not None for k in KINDS)


def auto_enabled() -> bool:
    """변환·번역 완료 시 자동으로 쌓을지 (폴더가 있고 자동 저장이 켜져 있을 때)."""
    return configured() and config.resolve_library_auto()


def same_folder() -> bool:
    """영어·한국어를 한 폴더에 넣도록 설정했는지 (그러면 파일명이 겹쳐 언어 접미사가 필요하다)."""
    en, ko = dir_for("en"), dir_for("ko")
    if en is None or ko is None:
        return False
    return _norm(en) == _norm(ko)


def _norm(path: Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def file_name(stem: str, which: str) -> str:
    """라이브러리에 쓰일 파일명. 두 언어(md)가 같은 폴더면 `<이름>.en.md`처럼 구분한다.

    PDF는 확장자가 달라 충돌이 없으므로 언제나 `<이름>.pdf`.
    """
    if which == "pdf":
        return f"{stem}.pdf"
    return f"{stem}.{which}.md" if same_folder() else f"{stem}.md"


def target_path(wd: WorkDir, which: str) -> Path | None:
    """이 논문이 라이브러리의 어디에 쓰일지 (폴더 미설정이면 None). 실제로 쓰지는 않는다."""
    dest = dir_for(which)
    return None if dest is None else dest / file_name(wd.root.stem, which)


def _namespace_images(md: str, stem: str) -> str:
    """`images/x` 참조를 `images/<논문>/x`로 — 여러 논문이 한 폴더에 쌓여도 안 섞이게."""
    prefix = f"images/{stem}/"
    md = _IMG_MD_RE.sub(lambda m: m.group(1) + prefix, md)
    return _IMG_WIKI_RE.sub(lambda m: m.group(1) + prefix, md)


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기가 같고 사본이 더 새로우면 건너뛰는 복사 (설정을 만질 때마다 재복사하지 않게)."""
    src_stat = src.stat()
    if dst.exists():
        dst_stat = dst.stat()
        if dst_stat.st_size == src_stat.st_size and dst_stat.st_mtime >= src_stat.st_mtime:
            return
    shutil.copy2(src, dst)


def _copy_images(wd: WorkDir, dest_dir: Path, referenced: set[str]) -> None:
    """참조된 이미지만 논문별 폴더로 복사. 내용이 같으면 건너뛴다."""
    if not wd.out_images.is_dir():
        return
    imgs = [p for p in sorted(wd.out_images.iterdir())
            if p.is_file() and (not referenced or p.name in referenced)]
    if not imgs:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for img in imgs:
        _copy_if_changed(img, dest_dir / img.name)


def _source_pdf(wd: WorkDir) -> Path | None:
    """이 논문의 원본 PDF 경로 (meta.json의 source). .md 입력 등 PDF가 없으면 None."""
    if not wd.meta_json.exists():
        return None
    try:
        src = str(json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source", ""))
    except (OSError, ValueError):
        return None
    p = Path(src)
    return p if src.lower().endswith(".pdf") and p.is_file() else None


def export_pdf(wd: WorkDir) -> Path | None:
    """원본 PDF를 PDF 저장 위치에 논문 기준명으로 복사 — md와 같은 이름이라 바로 찾아진다.

    폴더 미설정·PDF 없음(.md 입력)이면 아무것도 하지 않고 None.
    """
    dest = dir_for("pdf")
    src = _source_pdf(wd)
    if dest is None or src is None:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / file_name(wd.root.stem, "pdf")
    _copy_if_changed(src, out)
    return out


def export(wd: WorkDir, which: str, target: str | None = None) -> Path | None:
    """한 언어의 결과 마크다운 + 이미지를 라이브러리 폴더로 복사. 반환: 쓴 파일 경로.

    폴더가 설정돼 있지 않거나 그 언어의 마크다운이 아직 없으면 아무것도 하지 않고 None.
    """
    dest = dir_for(which)
    src = wd.en_md if which == "en" else wd.ko_md
    if dest is None or not src.exists():
        return None

    from md4paper.cite.apply import ref_urls
    from md4paper.export_format import to_export_target

    target = target or config.resolve_export_target()
    md = to_export_target(src.read_text(encoding="utf-8"), target,
                          ref_urls(wd) if target == "notion" else None)
    stem = wd.root.stem
    referenced = set(_IMG_REF_RE.findall(md))
    md = _namespace_images(md, stem)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / file_name(stem, which)
    out.write_text(md, encoding="utf-8")
    _copy_images(wd, dest / "images" / stem, referenced)
    return out


def export_paper(wd: WorkDir, target: str | None = None) -> list[Path]:
    """이 논문의 영어·한국어 마크다운과 원본 PDF를 각 폴더로 (있는 것만). 반환: 실제로 쓴 파일들."""
    written = [export(wd, w, target) for w in WHICH]
    written.append(export_pdf(wd))
    return [p for p in written if p is not None]


def remove_stem(stem: str) -> None:
    """이 기준명으로 내보냈던 사본을 정리 — 이름 규칙으로 리네임된 뒤 옛 이름 사본이 남지 않게.

    저장 위치 폴더 안에서 우리가 썼을 경로만 지운다: `<stem>*.md` · `images/<stem>/` · `<stem>.pdf`.
    """
    for which in WHICH:
        d = dir_for(which)
        if d is None:
            continue
        (d / file_name(stem, which)).unlink(missing_ok=True)
        imgs = d / "images" / stem
        if imgs.is_dir():
            shutil.rmtree(imgs, ignore_errors=True)
    pdf_dir = dir_for("pdf")
    if pdf_dir is not None:
        (pdf_dir / f"{stem}.pdf").unlink(missing_ok=True)


def auto_export(wd: WorkDir, target: str | None = None) -> list[Path]:
    """변환·번역 훅에서 부르는 자동 저장 — 꺼져 있으면 무시, 실패해도 파이프라인을 막지 않는다."""
    if not auto_enabled():
        return []
    try:
        return export_paper(wd, target)
    except Exception:  # noqa: BLE001 — 권한·용량 등 어떤 이유든 변환/번역 결과는 이미 안전하다
        return []


def export_many(roots, target: str | None = None) -> tuple[int, int]:  # noqa: ANN001 — iterable[Path]
    """여러 작업 디렉토리를 라이브러리로 한 번에. 반환: (내보낸 논문 수, 실패한 논문 수)."""
    ok = failed = 0
    for root in roots:
        try:
            if export_paper(WorkDir(Path(root)), target):
                ok += 1
        except OSError:
            failed += 1
    return ok, failed

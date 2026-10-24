"""변환한 논문이 쌓이는 전역 라이브러리 폴더 — 영어·한국어 마크다운, 원본 PDF를 각각 다른 폴더로.

작업 디렉토리(`<이름>.md4/`)는 원본 PDF·추출 중간물·캐시가 든 '작업장'이라 노트 앱에 그대로
넣을 수 없다. 여기서는 **결과 마크다운(+ 원본 PDF 사본)만** 사용자가 고른 폴더에 복사해 쌓는다:

```
<프로젝트 폴더>/2017_Attention_Vaswani.md          ← 영어 마크다운은 루트에
<프로젝트 폴더>/images/2017_Attention_Vaswani/     ← 그림은 논문별 폴더로
<프로젝트 폴더>/ko/2017_Attention_Vaswani.md       ← 번역본
<프로젝트 폴더>/pdf/2017_Attention_Vaswani.pdf     ← md와 같은 기준명 → md에서 바로 찾아진다
<프로젝트 폴더>/2017_Attention_Vaswani.notes.md    ← 메모 패널의 자유 메모 (있을 때만, 영어 md 옆에)
<프로젝트 폴더>/references.bib                     ← 항목이 계속 덧붙는다
```

- 파일명은 논문 폴더 이름(`wd.root.stem` — 이름 규칙 [output].naming으로 정해짐)이라
  여러 논문을 한 폴더에 쌓아도 충돌하지 않고, md·PDF가 같은 기준명을 공유한다.
- 이미지도 논문별 하위 폴더로 격리하고, 마크다운의 `images/…` 참조를 거기에 맞춰 고쳐 쓴다.
- 같은 논문을 다시 내보내면 덮어쓴다(버전이 쌓이는 게 아니라 논문이 쌓인다).
- 내보내기 형식(범용/Notion/Obsidian)은 다운로드 zip과 같은 전역 설정을 따른다.

폴더 경로는 두 곳에서 온다:

- **프로젝트**(§projects): 논문이 속한 프로젝트가 폴더 하나를 갖고 있으면 그 안의 자리로
  (`<루트>/`, `<루트>/ko/`, `<루트>/pdf/`). 논문마다 갈 곳이 달라진다. 논문 하나가 여러
  프로젝트에 속하면 **폴더를 가진 소속 프로젝트마다** 사본이 하나씩 생긴다.
- **공통**: 폴더를 가진 소속 프로젝트가 하나도 없으면(미분류 포함) 전역 설정 `[library]`의
  en/ko/pdf 폴더.

이 '갈 곳'들을 **저장 대상**이라 부른다(`export_targets` — 프로젝트 id, 공통은 ""). 각 사본은
**그 폴더의 프로젝트** 설정(내보내기 형식·BibTeX 쌓기)으로 쓰고(공통 폴더면 전역 설정), 파일
기준명만 주 프로젝트(소속의 첫 항목)의 이름 규칙을 따른다 — 폴더마다 쓰는 노트 앱이 다를 수
있어서다. 두 대상이 같은 폴더를 가리키면 한 번만 쓴다.

같은 폴더에 `references.bib`도 함께 쌓는다(§bibtex) — 논문 하나에 항목 하나, 복사해 붙이도록.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from narani import config, projects, workdir
from narani.workdir import WorkDir

WHICH = config.LIBRARY_WHICH  # ("en", "ko") — 마크다운 언어
KINDS = config.LIBRARY_KINDS  # ("en", "ko", "pdf") — 저장 위치 폴더 종류

# 마크다운 이미지 `![alt](images/x)` · Obsidian 위키 임베드 `![[images/x]]` 둘 다 논문별 폴더로.
# (코드 펜스 안의 'images/' 같은 평범한 텍스트는 건드리지 않도록 임베드 문법을 함께 매치한다.)
_IMG_MD_RE = re.compile(r"(!\[[^\]]*\]\()images/")
_IMG_WIKI_RE = re.compile(r"(!\[\[)images/")
# 실제로 참조된 이미지 파일명 — controller의 zip 내보내기와 같은 관례
_IMG_REF_RE = re.compile(r"images/([^\s)\"'\]]+)")


def dir_for(which: str, project: str | None = None) -> Path | None:
    """이 논문(프로젝트)의 종류별 폴더 — 프로젝트 폴더 > 공통 설정. 둘 다 없으면 None."""
    if project:
        own = projects.dir_for(project, which)
        if own is not None:
            return own
    return config.resolve_library_dir(which)


def projects_of(wd: WorkDir) -> list[str]:
    """이 논문이 속한 프로젝트 id들 — 없어진 프로젝트는 빠진다. 첫 항목이 주 프로젝트."""
    return projects.normalize_many(workdir.projects_of(wd.root))


def project_of(wd: WorkDir) -> str:
    """이 논문의 주 프로젝트 id (소속의 첫 항목; 미분류·없어진 프로젝트뿐이면 빈 문자열)."""
    ids = projects_of(wd)
    return ids[0] if ids else ""


def _place(project: str) -> tuple[str, ...]:
    """저장 대상이 실제로 쓰는 폴더들(en·ko·pdf, 정규화) — 같은 폴더를 가리키는 대상을 알아보는 키."""
    return tuple("" if d is None else _norm(d) for d in (dir_for(k, project) for k in KINDS))


def _targets_for(ids: list[str]) -> list[str]:
    """소속 목록 → 저장 대상들: 폴더를 가진 프로젝트마다, 하나도 없으면 공통("").

    두 프로젝트가 같은 폴더를 정해 뒀으면 앞의 것 하나만 남긴다(같은 파일을 두 번 쓰지 않게).
    """
    found: dict[tuple[str, ...], str] = {}
    for pid in [pid for pid in ids if projects.has_dirs(pid)] or [""]:
        found.setdefault(_place(pid), pid)
    return list(found.values())


def export_targets(wd: WorkDir) -> list[str]:
    """이 논문의 사본이 놓이는 곳들 — 프로젝트 id(그 프로젝트 폴더) 또는 ""(공통 저장 위치).

    그림 교체·이름 정리처럼 **이미 내보낸 사본**을 찾아가야 하는 곳도 이 목록을 돈다.
    """
    return _targets_for(projects_of(wd))


def _dest(wd: WorkDir, project: str | None) -> str:
    """`project`가 None이면 이 논문의 첫 저장 대상 (단일 대상 함수들의 기본값)."""
    return export_targets(wd)[0] if project is None else project


def dirs_for(project: str | None = None) -> dict[str, Path | None]:
    """{종류: 폴더} — 어디로 가는지 사용자에게 보여줄 때."""
    return {k: dir_for(k, project) for k in KINDS}


def configured(project: str | None = None) -> bool:
    """영어·한국어·PDF 중 하나라도 폴더가 정해져 있는지."""
    return any(dir_for(k, project) is not None for k in KINDS)


def auto_enabled(project: str | None = None) -> bool:
    """변환·번역 완료 시 자동으로 쌓을지 (폴더가 있고 자동 저장이 켜져 있을 때)."""
    return configured(project) and config.resolve_library_auto(project)


def same_folder(project: str | None = None) -> bool:
    """영어·한국어를 한 폴더에 넣도록 설정했는지 (그러면 파일명이 겹쳐 언어 접미사가 필요하다)."""
    en, ko = dir_for("en", project), dir_for("ko", project)
    if en is None or ko is None:
        return False
    return _norm(en) == _norm(ko)


def _norm(path: Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def file_name(stem: str, which: str, project: str | None = None) -> str:
    """라이브러리에 쓰일 파일명. 두 언어(md)가 같은 폴더면 `<이름>.en.md`처럼 구분한다.

    PDF는 확장자가 달라 충돌이 없으므로 언제나 `<이름>.pdf`.
    """
    if which == "pdf":
        return f"{stem}.pdf"
    return f"{stem}.{which}.md" if same_folder(project) else f"{stem}.md"


def target_path(wd: WorkDir, which: str) -> Path | None:
    """이 논문이 라이브러리의 어디에 쓰일지 — 첫 저장 대상 기준 (폴더 미설정이면 None). 실제로 쓰지는 않는다."""
    project = _dest(wd, None)
    dest = dir_for(which, project)
    return None if dest is None else dest / file_name(wd.root.stem, which, project)


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


def export_pdf(wd: WorkDir, project: str | None = None) -> Path | None:
    """원본 PDF를 PDF 저장 위치에 논문 기준명으로 복사 — md와 같은 이름이라 바로 찾아진다.

    폴더 미설정·PDF 없음(.md 입력)이면 아무것도 하지 않고 None.
    `project`는 저장 대상(None이면 이 논문의 첫 대상, ""는 공통).
    """
    project = _dest(wd, project)
    dest = dir_for("pdf", project)
    src = _source_pdf(wd)
    if dest is None or src is None:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / file_name(wd.root.stem, "pdf", project)
    _copy_if_changed(src, out)
    return out


def bib_path(project: str | None = None) -> Path | None:
    """이 프로젝트의 references.bib 자리 — 프로젝트 폴더, 없으면 공통 폴더 중 먼저 정해진 것.

    영어 마크다운이 놓이는 폴더를 우선한다(주로 그 폴더를 노트 앱에서 열기 때문). 폴더가
    하나도 없으면 None — 쌓을 곳이 없다는 뜻이라 아무것도 하지 않는다.
    """
    from narani.bibtex import BIB_NAME

    root = projects.folder_of(project) if project else None  # 하위 컬렉션은 상위 폴더를 물려받는다
    if root is not None:
        return root / BIB_NAME
    for which in KINDS:
        d = config.resolve_library_dir(which)
        if d is not None:
            return d / BIB_NAME
    return None


def export_bib(wd: WorkDir, project: str | None = None) -> Path | None:
    """이 논문의 BibTeX 항목을 저장 위치의 references.bib에 반영. 반환: 쓴 .bib 경로.

    항목이 계속 덧붙되 같은 논문은 갈아치운다(§bibtex). 서지 정보가 없거나 BibTeX 정리를
    껐으면 아무것도 하지 않는다. `project`는 저장 대상(None이면 첫 대상) — 켜짐 여부도 그 대상의 설정.
    """
    from narani import bibtex

    project = _dest(wd, project)
    if not config.resolve_library_bibtex(project):  # 프로젝트(폴더)마다 켜고 끌 수 있다
        return None
    path = bib_path(project)
    if path is None:
        return None
    made = bibtex.entry_for(wd)
    if made is None:
        return None
    key, entry = made
    return bibtex.sync(path, key, entry)


def notes_dir(project: str | None = None) -> Path | None:
    """자유 메모 사본이 놓일 폴더 — 영어 마크다운 옆, 영어 폴더가 없으면 번역 폴더(bib와 같은 우선순위)."""
    for which in WHICH:
        d = dir_for(which, project)
        if d is not None:
            return d
    return None


def notes_file_name(stem: str) -> str:
    return f"{stem}.notes.md"


def export_notes(wd: WorkDir, project: str | None = None) -> Path | None:
    """메모 패널의 자유 메모(notes.md)를 `<논문>.notes.md`로 함께 복사. 메모가 없거나 폴더 미설정이면 None.

    크기·시각 비교 없이 늘 덮어쓴다 — 같은 길이의 수정("teh"→"the")도 사본에 반영돼야 한다.
    `project`는 저장 대상(None이면 첫 대상).
    """
    from narani import notes

    project = _dest(wd, project)
    dest = notes_dir(project)
    if dest is None:
        return None
    text = notes.combined(wd)  # 메모 탭이 여럿이면 탭마다 '### 이름' 절로 한 파일에
    if not text.strip():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / notes_file_name(wd.root.stem)
    out.write_text(text, encoding="utf-8")
    return out


def export(wd: WorkDir, which: str, target: str | None = None,
           project: str | None = None) -> Path | None:
    """한 언어의 결과 마크다운 + 이미지를 라이브러리 폴더로 복사. 반환: 쓴 파일 경로.

    `project`는 저장 대상(None이면 이 논문의 첫 대상, ""는 공통). 형식(`target`)을 안 주면 그
    대상의 설정(공통이면 전역)을 따른다. 폴더가 설정돼 있지 않거나 그 언어의 마크다운이 아직 없으면 None.
    """
    project = _dest(wd, project)
    dest = dir_for(which, project)
    src = wd.en_md if which == "en" else wd.ko_md
    if dest is None or not src.exists():
        return None

    from narani.cite.apply import ref_urls
    from narani.export_format import to_export_target

    target = target or config.resolve_export_target(project)  # 그 폴더의 노트 앱에 맞게
    md = to_export_target(src.read_text(encoding="utf-8"), target,
                          ref_urls(wd) if target == "notion" else None)
    stem = wd.root.stem
    referenced = set(_IMG_REF_RE.findall(md))
    md = _namespace_images(md, stem)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / file_name(stem, which, project)
    out.write_text(md, encoding="utf-8")
    _copy_images(wd, dest / "images" / stem, referenced)
    return out


def _export_into(wd: WorkDir, project: str, target: str | None) -> list[Path]:
    """저장 대상 하나에 이 논문의 영어·한국어·PDF·메모·bib 항목을 (있는 것만)."""
    written = [export(wd, w, target, project) for w in WHICH]
    written.append(export_pdf(wd, project))
    written.append(export_notes(wd, project))
    written.append(export_bib(wd, project))
    return [p for p in written if p is not None]


def export_paper(wd: WorkDir, target: str | None = None) -> list[Path]:
    """이 논문의 영어·한국어 마크다운·원본 PDF·자유 메모·BibTeX 항목을 각 자리로 (있는 것만).

    반환: 실제로 쓴 파일들. 폴더를 가진 소속 프로젝트마다 그 폴더로, 그런 프로젝트가 없으면
    공통 폴더로(`export_targets`).
    """
    written: list[Path] = []
    for project in export_targets(wd):
        written += _export_into(wd, project, target)
    return list(dict.fromkeys(written))  # 두 대상이 같은 폴더를 가리킬 때 같은 경로가 두 번 나오지 않게


def remove_stem(stem: str, project: str | None = None) -> None:
    """이 기준명으로 내보냈던 사본을 정리 — 이름 규칙으로 리네임된 뒤 옛 이름 사본이 남지 않게.

    저장 위치 폴더 안에서 우리가 썼을 경로만 지운다: `<stem>*.md` · `images/<stem>/` ·
    `<stem>.pdf` · `<stem>.notes.md` · references.bib의 그 항목.
    """
    from narani import bibtex

    for which in WHICH:
        d = dir_for(which, project)
        if d is None:
            continue
        (d / file_name(stem, which, project)).unlink(missing_ok=True)
        (d / f"{stem}.{which}.md").unlink(missing_ok=True)  # 한 폴더 설정이던 시절의 사본까지
        (d / notes_file_name(stem)).unlink(missing_ok=True)
        imgs = d / "images" / stem
        if imgs.is_dir():
            shutil.rmtree(imgs, ignore_errors=True)
    pdf_dir = dir_for("pdf", project)
    if pdf_dir is not None:
        (pdf_dir / f"{stem}.pdf").unlink(missing_ok=True)
    bib = bib_path(project)
    if bib is not None:
        bibtex.remove(bib, bibtex.cite_key(stem))


def remove_stem_everywhere(stem: str) -> None:
    """공통 폴더와 **모든 프로젝트** 폴더에서 이 논문의 사본을 정리.

    논문을 삭제할 때 쓴다 — 프로젝트를 옮겨 다녔다면 옛 프로젝트 폴더에 사본이 남아 있을 수
    있고, 원본이 사라진 뒤에는 그게 어디 있었는지 알 방법이 없다.
    """
    remove_stem(stem, None)
    for proj in projects.all_projects():
        remove_stem(stem, proj["id"])


def set_memberships(wd: WorkDir, project_ids,  # noqa: ANN001 — iterable[str]
                    target: str | None = None) -> list[Path]:
    """논문의 소속 프로젝트를 통째로 바꾼다 — 사본도 바뀐 저장 대상에 맞춰 따라가게.

    옛 소속과 새 소속의 저장 대상(폴더 가진 프로젝트들, 없으면 공통)을 **폴더 기준으로** 비교해,
    빠진 폴더의 사본(마크다운·이미지·PDF·메모·bib 항목)은 정리하고 새로 생긴 폴더에만 내보낸다.
    그대로 남는 폴더의 사본은 건드리지 않는다 — 한 프로젝트를 뺐어도 남은 프로젝트가 같은 폴더를
    쓰면 그 사본은 남아야 한다. 사용자가 **직접 누른 동작**이므로 자동 저장이 꺼져 있어도
    사본을 옮긴다(이름 정리와 같은 규칙). 순서가 곧 주 프로젝트이므로 순서만 바꿔도 소속은 다시 쓴다.
    반환: 새 자리에 쓴 파일들.
    """
    old_ids = projects_of(wd)
    new_ids = projects.normalize_many(project_ids)
    if new_ids == old_ids:
        return []
    old, new = _targets_for(old_ids), _targets_for(new_ids)
    old_places, new_places = {_place(p) for p in old}, {_place(p) for p in new}
    stem = wd.root.stem
    for project in old:
        if _place(project) not in new_places:
            try:
                remove_stem(stem, project)
            except OSError:
                pass  # 옛 사본 정리 실패가 소속 변경 자체를 막지 않게
    workdir.set_projects(wd.root, new_ids)
    written: list[Path] = []
    for project in new:
        if _place(project) not in old_places:
            try:
                written += _export_into(wd, project, target)
            except OSError:
                pass
    return list(dict.fromkeys(written))


def reassign(wd: WorkDir, project: str, target: str | None = None) -> list[Path]:
    """논문을 프로젝트 **하나로** 옮긴다(미분류면 소속을 비운다) — `set_memberships`의 단일 소속 래퍼."""
    return set_memberships(wd, [project], target)


def auto_export(wd: WorkDir, target: str | None = None) -> list[Path]:
    """변환·번역 훅에서 부르는 자동 저장 — 꺼져 있으면 무시, 실패해도 파이프라인을 막지 않는다.

    켜짐 여부는 주 프로젝트 설정으로, 폴더가 정해졌는지는 저장 대상마다 본다(주 프로젝트엔 폴더가
    없어도 다른 소속 프로젝트엔 있을 수 있다).
    """
    if not config.resolve_library_auto(project_of(wd)):
        return []
    if not any(configured(project) for project in export_targets(wd)):
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

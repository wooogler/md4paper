"""md4paper CLI — click 커맨드 그룹. 로직은 각 단계 모듈에, 여기는 배선만."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import click

from md4paper import config, pipeline
from md4paper.extract import BACKENDS, DEFAULT_BACKEND, ExtractError
from md4paper.ir import Flavor
from md4paper.review import manifest as manifest_io
from md4paper.workdir import WorkDir


@click.group()
@click.version_option(package_name="md4paper")
def cli() -> None:
    """논문 PDF → 헤더 정렬 마크다운 + 한국어 번역 마크다운."""


# --- doctor ---------------------------------------------------------------


@cli.command()
def doctor() -> None:
    """환경 점검 (Python·의존성·추출 백엔드·LLM 키)."""
    from md4paper.doctor import run_checks

    required_ok = True
    optional_missing = False
    for label, ok, detail, optional in run_checks():
        if ok:
            mark = click.style("✓", fg="green")
        elif optional:
            mark = click.style("-", fg="yellow")  # 선택 항목은 실패가 아니다
        else:
            mark = click.style("✗", fg="red")
        line = f"{mark} {label}"
        if detail:
            line += click.style(f"  — {detail}", fg="bright_black")
        click.echo(line)
        if optional:
            optional_missing = optional_missing or not ok
        else:
            required_ok = required_ok and ok
    if not required_ok:
        click.echo("\n필수 항목 미충족 — 위 안내를 확인하세요.")
    elif optional_missing:
        click.echo("\n변환(PDF→마크다운)은 바로 쓸 수 있습니다. 번역·인용 링크는 LLM 키가 필요합니다.")
    else:
        click.echo("\n모두 정상.")


# --- keys -----------------------------------------------------------------


@cli.group()
def keys() -> None:
    """LLM API 키 관리 (~/.config/md4paper/config.toml, 0600)."""


@keys.command("set")
@click.argument("provider", type=click.Choice(config.PROVIDERS))
@click.option("--key", prompt=True, hide_input=True, help="입력 숨김")
def keys_set(provider: str, key: str) -> None:
    """프로바이더 API 키 저장."""
    config.set_key(provider, key)
    click.echo(f"{provider} 키 저장됨: {config.CONFIG_PATH}")


@cli.group("prefs")
def prefs_group() -> None:
    """헤더 이름별 기억된 선택 (같은 학회 포맷 논문에 자동 적용)."""


@prefs_group.command("list")
def prefs_list() -> None:
    """기억된 헤더 처리 목록."""
    from md4paper import prefs

    data = prefs.load()
    if not data:
        click.echo("기억된 선택 없음. 웹 UI에서 헤더 레벨을 고치면 이름별로 기억합니다.")
        return
    click.echo(f"기억된 헤더 {len(data)}개 ({prefs.PREFS_PATH}):")
    for key, level in sorted(data.items()):
        click.echo(f"  {key:<40} → {level}")


@prefs_group.command("forget")
@click.argument("heading", required=False)
def prefs_forget(heading: str | None) -> None:
    """특정 헤더의 기억 삭제 (HEADING 생략 시 전체 삭제)."""
    from md4paper import prefs

    if heading:
        prefs.forget(heading)
        click.echo(f"'{heading}' 기억 삭제됨")
    else:
        if prefs.PREFS_PATH.exists():
            prefs.PREFS_PATH.unlink()
        click.echo("모든 헤더 기억 삭제됨")


@cli.command()
@click.argument("path", required=False, type=click.Path(path_type=Path))
def workspace(path: Path | None) -> None:
    """작업 폴더 조회/설정 — 업로드 파일과 결과(.md4)를 모아둘 위치."""
    if path is None:
        cur = config.resolve_workspace()
        click.echo(f"현재 작업 폴더: {cur}")
        click.echo(f"기본값: {config.default_workspace()}")
        click.echo("변경: md4paper workspace <경로>   (예: ~/Documents/papers)")
        return
    config.set_section_value("output", "workspace", str(path))
    click.echo(f"작업 폴더 설정됨: {config.resolve_workspace()}")


@cli.command("library")
@click.option("--en", "en_dir", type=click.Path(path_type=Path), default=None,
              help="영어 마크다운을 쌓을 폴더")
@click.option("--ko", "ko_dir", type=click.Path(path_type=Path), default=None,
              help="한국어 마크다운을 쌓을 폴더")
@click.option("--pdf", "pdf_dir", type=click.Path(path_type=Path), default=None,
              help="원본 PDF 사본을 쌓을 폴더 (md와 같은 기준명)")
@click.option("--off", "which_off", type=click.Choice(["en", "ko", "pdf", "all"]), default=None,
              help="지정 해제")
@click.option("--export", "export_all", is_flag=True, help="작업 폴더의 논문을 지금 전부 내보내기")
def library_cmd(en_dir: Path | None, ko_dir: Path | None, pdf_dir: Path | None,
                which_off: str | None, export_all: bool) -> None:
    """저장 위치 조회/설정 — 변환한 논문의 마크다운·PDF가 쌓일 폴더 (종류별 따로)."""
    from md4paper import library

    for which, path in (("en", en_dir), ("ko", ko_dir), ("pdf", pdf_dir)):
        if path is not None:
            config.set_library_dir(which, str(path))
            click.echo(f"{which} 저장 위치: {config.resolve_library_dir(which)}")
    if which_off:
        for which in (library.KINDS if which_off == "all" else (which_off,)):
            config.set_library_dir(which, None)
            click.echo(f"{which} 저장 위치 해제됨")
    if export_all:
        from md4paper.workdir import recent_workdirs

        roots = [r["root"] for r in recent_workdirs(config.resolve_workspace(), limit=1000)]
        ok, failed = library.export_many(roots)
        click.echo(f"{ok}편 내보냄" + (f" · {failed}편 실패" if failed else ""))
    if en_dir is None and ko_dir is None and pdf_dir is None and not which_off and not export_all:
        for which in library.KINDS:
            cur = config.resolve_library_dir(which)
            click.echo(f"{which}: {cur if cur else '미설정'}")
        click.echo(f"자동 저장: {'켜짐' if config.resolve_library_auto() else '꺼짐'}")
        click.echo("변경: md4paper library --en ~/Papers/EN --ko ~/Papers/KO --pdf ~/Papers/PDF")


@cli.command("enrich")
@click.argument("workdir", required=False, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--all", "do_all", is_flag=True, help="작업 폴더의 모든 논문을 보강")
@click.option("--mailto", default=None, help="서지 API polite pool 연락처 (config [enrich].mailto에 저장)")
@click.option("--rename/--no-rename", default=True, help="보강 후 이름 규칙으로 정리 (기본: 함)")
def enrich_cmd(workdir: Path | None, do_all: bool, mailto: str | None, rename: bool) -> None:
    """비어 있는 연도·venue를 공개 서지 API(OpenAlex·Crossref)로 채운다.

    논문 제목만 전송하며, 제목이 충분히 일치할 때만 채택한다(오매치 방지). PDF에서 읽은 값은 덮어쓰지 않는다.
    """
    from md4paper import enrich, paper_meta
    from md4paper.workdir import recent_workdirs

    if mailto:
        config.set_section_value("enrich", "mailto", mailto)
    contact = config.resolve_enrich_mailto() or None
    if not workdir and not do_all:
        raise click.UsageError("WORKDIR을 주거나 --all을 쓰세요.")
    ws = config.resolve_workspace()
    roots = ([r["root"] for r in recent_workdirs(ws, limit=100_000, include_hidden=True)]
             if do_all else [workdir])

    def show(n: int, root: Path, filled: list) -> None:  # noqa: ANN001
        if filled:
            click.echo(f"  [{n}] {Path(root).stem}: {', '.join(filled)} 채움")

    counts = enrich.enrich_many(roots, mailto=contact, on_progress=show)
    click.echo(f"{counts['checked']}편 확인 · {counts['papers']}편 보강 "
               f"(연도 {counts.get('year', 0)} · venue {counts.get('venue', 0)})")
    if rename and counts["papers"]:
        r = paper_meta.apply_naming(ws)
        click.echo(f"이름 정리: {r['renamed']}편 변경")


@cli.command("naming")
@click.argument("template", required=False)
@click.option("--apply", "apply_now", is_flag=True,
              help="기존 논문의 폴더·PDF·저장 위치 사본 이름을 지금 규칙으로 정리")
@click.option("--reset", is_flag=True, help="기본 규칙으로 되돌리기")
def naming_cmd(template: str | None, apply_now: bool, reset: bool) -> None:
    """논문 파일 이름 규칙 조회/설정 — 폴더·PDF·저장 위치 사본이 모두 이 이름을 쓴다."""
    from md4paper import paper_meta

    if reset:
        config.set_section_value("output", "naming", None)
        click.echo(f"기본 규칙으로 되돌림: {config.DEFAULT_NAMING}")
    if template:
        err = config.naming_template_error(template)
        if err:
            raise click.ClickException(f"이름 규칙 오류: {err}")
        config.set_section_value("output", "naming", template)
        click.echo("이름 규칙 설정됨")
    click.echo(f"현재 규칙: {config.resolve_naming_template()}")
    click.echo(f"예시: {paper_meta.naming_preview()}")
    if apply_now:
        counts = paper_meta.apply_naming(config.resolve_workspace())
        click.echo(f"{counts['renamed']}편 이름 변경 · {counts['unchanged']}편 유지"
                   + (f" · {counts['no_meta']}편 서지 없음(건너뜀)" if counts["no_meta"] else ""))
    elif not template and not reset:
        click.echo("조각: {year} 연도 · {title} 제목 약칭 · {author} 1저자 성 · {venue} 학회")
        click.echo('변경: md4paper naming "{year}_{title}_{author}" · 기존 논문 정리: md4paper naming --apply')


@keys.command("list")
def keys_list() -> None:
    """설정된 프로바이더/키 여부 표시 (값은 마스킹)."""
    status = config.key_status()
    default = config.resolve_provider()
    for p in config.PROVIDERS:
        mark = click.style("설정됨", fg="green") if status[p] else click.style("미설정", fg="yellow")
        star = " (기본)" if p == default else ""
        model = config.resolve_model(p)
        click.echo(f"{p}{star}: {mark}  모델={model}")


# --- convert --------------------------------------------------------------


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=None, help="작업 디렉토리 (기본: <이름>.md4)")
@click.option("--backend", type=click.Choice(BACKENDS), default=DEFAULT_BACKEND,
              help="추출 백엔드 (docling)")
@click.option("--ocr", is_flag=True, help="스캔 PDF용 OCR 켜기 (born-digital 논문엔 불필요)")
@click.option(
    "--flavor",
    type=click.Choice(["standard", "obsidian", "notion"]),
    default=None,
    help="내보내기 형식. standard=범용, obsidian=위키 임베드, notion=Notion용. "
         "미지정 시 config 기본값.",
)
@click.option("--review", is_flag=True, help="구조 감지 후 에디터로 매니페스트 리뷰")
def convert(
    source: Path, out: Path | None, backend: str, ocr: bool, flavor: str | None, review: bool,
) -> None:
    """PDF(또는 .md) → 헤더 정렬 마크다운 (Stage 1-4)."""
    wd = WorkDir.for_pdf(source, out)
    # 내보내기 타깃: --flavor 지정 시 매핑(standard=범용), 아니면 config 기본. 조립은 항상 canonical.
    export_target = ({"standard": "universal", "obsidian": "obsidian", "notion": "notion"}[flavor]
                     if flavor else config.resolve_export_target())
    try:
        meta = pipeline.run_extract(source, wd, backend=backend, ocr=ocr)
    except ExtractError as e:
        raise click.ClickException(str(e)) from e
    if meta.get("text_coverage") == 0 and not ocr:
        click.echo(click.style("경고: 텍스트 레이어 없음 — 스캔 PDF로 보임. --ocr 를 켜세요.", fg="yellow"))
    if meta.get("garbled_chars"):
        click.echo(click.style(
            f"경고: 깨진 문자 {meta['garbled_chars']}개 남음 (복구 {meta.get('garbled_repaired', 0)}개). "
            "다른 백엔드(--backend)를 시도해 보세요.", fg="yellow"))
    if meta.get("images") == 0:
        click.echo(click.style("참고: 추출된 그림이 없습니다.", fg="yellow"))

    # 앞부분(저자·소속·저작권) 정규화 — 웹 UI(pipeline.convert)와 같은 단계.
    # 키가 있으면 LLM 라벨+재조립, 없으면 규칙 폴백. 빠뜨리면 저자 줄이 원문 그대로 뭉쳐 나온다.
    fm_provider = None
    try:
        fm_provider = config.build_provider()
    except RuntimeError:
        click.echo(click.style("참고: LLM 키 없음 — 저자 정리는 규칙 기반으로만 합니다.", fg="yellow"))
    fm = pipeline.run_frontmatter(wd, provider=fm_provider)
    if fm.get("changed"):
        click.echo("앞부분(저자·서지) 정리 완료")

    manifest = pipeline.run_structure(wd, flavor=Flavor.STANDARD)
    click.echo(f"헤더 {len(manifest.sections)}개 감지 → {wd.sections_yaml}")
    n_review = sum(1 for s in manifest.sections if s.needs_review)
    if n_review:
        click.echo(click.style(f"  그중 {n_review}개는 번호 감지 실패 — 리뷰 권장.", fg="yellow"))

    if review:
        _edit_manifest(wd)

    pipeline.run_assemble(wd)
    # en.md는 범용(canonical)으로 조립되므로, CLI 산출물은 요청한 형식으로 변환해 쓴다.
    if export_target != "universal":
        from md4paper.cite.apply import load_cached_refs
        from md4paper.export_format import to_export_target

        ref_urls = ({r.label: r.url() for r in load_cached_refs(wd) if r.url()}
                    if export_target == "notion" else None)
        for p in (wd.en_md, wd.ko_md):
            if p.exists():
                p.write_text(to_export_target(p.read_text(encoding="utf-8"), export_target, ref_urls),
                             encoding="utf-8")
    click.echo(click.style(f"완료: {wd.en_md}", fg="green"))


# --- review ---------------------------------------------------------------


@cli.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def review(workdir: Path) -> None:
    """섹션 매니페스트를 $EDITOR로 열어 수정하고 재조립."""
    wd = WorkDir(workdir)
    if not wd.sections_yaml.exists():
        raise click.ClickException(f"매니페스트 없음: {wd.sections_yaml}. 먼저 convert를 실행하세요.")
    _edit_manifest(wd)
    pipeline.run_assemble(wd, force=True)
    click.echo(click.style(f"재조립 완료: {wd.en_md}", fg="green"))


def _edit_manifest(wd: WorkDir) -> None:
    """click.edit 라운드트립 — 저장 시 검증, 실패하면 다시 연다(rebase -i 방식)."""
    text = wd.sections_yaml.read_text(encoding="utf-8")
    while True:
        edited = click.edit(text, extension=".yaml")
        if edited is None:
            click.echo("변경 없음.")
            return
        wd.sections_yaml.write_text(edited, encoding="utf-8")
        try:
            manifest_io.load(wd)
            return
        except Exception as e:  # noqa: BLE001 — 검증 오류를 주석으로 보여주고 재오픈
            text = f"# ⚠ 검증 실패: {e}\n# 위 오류를 고치고 다시 저장하세요.\n" + edited
            if not click.confirm("검증 실패. 다시 편집할까요?", default=True):
                return


# --- 이후 마일스톤 스텁 ---------------------------------------------------


def _native_available() -> bool:
    """앱 창(pywebview + OS 웹뷰)을 띄울 수 있는지. 안 되면 이유를 알리고 False → 브라우저로 연다.

    아이콘으로 띄웠을 때 '눌렀는데 아무 일도 안 일어남'을 만들지 않으려는 장치다. 리눅스에서
    WebKit2GTK·Qt가 없으면 pywebview는 import까지는 되고 창을 만들 때 죽는데, 그건 사용자가 그
    자리에서 고칠 수 없다 — 브라우저로라도 열어 주는 편이 낫다.
    """
    if importlib.util.find_spec("webview") is None:
        click.echo(click.style("앱 창 의존성(pywebview)이 없어 브라우저로 엽니다 — "
                               "`uv sync --extra ui --extra native`", fg="yellow"), err=True)
        return False
    if sys.platform == "darwin":
        return _cocoa_present()
    try:
        from webview.guilib import initialize

        initialize()  # OS 웹뷰 백엔드 탐색 (윈도우는 WebView2, 리눅스는 GTK/Qt)
    except Exception as e:  # noqa: BLE001 — 백엔드가 여럿이라 예외 종류도 제각각이다
        click.echo(click.style(f"앱 창을 띄울 수 없어 브라우저로 엽니다 ({e})", fg="yellow"), err=True)
        return False
    return True


def _cocoa_present() -> bool:
    """macOS 웹뷰 백엔드가 갖춰졌는지 — **임포트하지 않고** 모듈이 있는지만 본다.

    다른 OS처럼 `guilib.initialize()`로 확인하면 안 된다. 그 호출은 `webview.platforms.cocoa`를
    임포트하는데, 그 모듈은 클래스 본문에서 `NSApplication.sharedApplication()`과
    `setActivationPolicy_(0)`을 실행한다 — 그러면 **창도 이벤트 루프도 없는 서버 프로세스**가
    Dock에 앱으로 등록된다. macOS는 먼저 등록한 이 프로세스를 아이콘을 눌러 시작한 그 앱으로
    보고(`lsappinfo`의 launch·checkin이 여기 붙는다), 정작 창을 가진 웹뷰 프로세스(NiceGUI가
    따로 spawn한다)는 뒤늦게 **요청하지 않은 두 번째 인스턴스**로 체크인한다. 그 상태에서
    웹뷰가 `activateIgnoringOtherApps_`로 앞에 나오려 하면 macOS는 허락 대신 Dock 아이콘을
    튕겨서 알린다 — 백그라운드 작업이 끝났을 때 나오는 그 튕김이다.

    macOS 백엔드는 pywebview를 깔면 pyobjc와 함께 따라오므로, 있는지만 봐도 충분하다.
    """
    missing = [name for name in ("webview.platforms.cocoa", "AppKit", "WebKit")
               if importlib.util.find_spec(name) is None]
    if missing:
        click.echo(click.style(f"앱 창을 띄울 수 없어 브라우저로 엽니다 ({', '.join(missing)} 없음)",
                               fg="yellow"), err=True)
        return False
    return True


@cli.command()
@click.argument("workdir", required=False, type=click.Path(path_type=Path))
@click.option("--upload-dir", type=click.Path(path_type=Path), default=None, help="업로드 파일·결과 저장 위치 (기본: 프로젝트 output/ 폴더)")
@click.option("--port", default=8080, help="로컬 포트")
@click.option("--no-show", is_flag=True, help="브라우저 자동 열기 비활성")
@click.option("--native", is_flag=True, help="브라우저 탭 대신 앱 창으로 열기 (`md4paper app` 런처가 쓰는 모드)")
def ui(workdir: Path | None, upload_dir: Path | None, port: int, no_show: bool, native: bool) -> None:
    """로컬 웹 UI (NiceGUI). WORKDIR 없이 실행하면 PDF 업로드 홈에서 시작.

    섹션 트리 리뷰 · 마크다운/수식 프리뷰 · PDF 대조 · 용어집 검토 · 번역.
    """
    wd = None
    if workdir is not None:
        wd = WorkDir(workdir)
        if not wd.sections_yaml.exists():
            raise click.ClickException(f"매니페스트 없음: {wd.sections_yaml}. 먼저 convert를 실행하세요.")
    if native:
        native = _native_available()  # 못 띄우면 브라우저로 — 아이콘이 '아무 반응 없음'이 되지 않게
    try:
        from md4paper.ui.app import run as run_ui
    except ImportError as e:
        raise click.ClickException("웹 UI 의존성 미설치 — `uv sync --extra ui` 실행하세요.") from e
    where = "업로드 홈" if wd is None else f"리뷰: {wd.root}"
    click.echo(f"UI 시작 ({where})…")  # 실제 주소는 run_ui가 출력 (포트 충돌 시 자동 대체)
    run_ui(wd, upload_dir=upload_dir, port=port, show=not no_show, native=native)


@cli.command("app")
@click.option("--remove", "do_remove", is_flag=True, help="설치한 런처 제거")
@click.option("--dir", "dest", type=click.Path(path_type=Path), default=None,
              help="설치 위치 (기본: macOS ~/Applications, Linux ~/.local/share/applications)")
@click.option("--desktop", "also_desktop", is_flag=True, help="윈도우: 바탕화면에도 바로가기 생성")
def app_cmd(do_remove: bool, dest: Path | None, also_desktop: bool) -> None:
    """더블클릭으로 여는 앱 아이콘 설치 (macOS .app · 윈도우 바로가기 · 리눅스 .desktop).

    아이콘을 누르면 앱 창으로 md4paper가 열립니다(`md4paper ui --native`와 같은 화면).
    """
    from md4paper import launcher

    if do_remove:
        removed = launcher.remove(dest)
        for path in removed:
            click.echo(f"제거됨: {path}")
        if not removed:
            click.echo(f"설치된 런처가 없습니다: {dest or launcher.default_location()}")
        return

    try:
        path = launcher.install(dest, also_desktop=also_desktop)
    except (launcher.LauncherError, OSError) as e:
        raise click.ClickException(str(e)) from e

    click.echo(click.style(f"런처 설치됨: {path}", fg="green"))
    if sys.platform == "darwin":
        click.echo("  Launchpad·Spotlight에서 'md4paper'로 열거나 Finder에서 더블클릭하세요.")
        click.echo("  실행 로그: ~/Library/Logs/md4paper.log")
    click.echo(f"  실행 명령: {' '.join(launcher.launch_command())}")
    if importlib.util.find_spec("webview") is None:
        click.echo(click.style(
            "  참고: pywebview가 없어 지금 누르면 앱 창 대신 브라우저가 열립니다 — "
            "`uv sync --extra ui --extra native`", fg="yellow"))
    env_only = [config.ENV_VARS[p] for p in config.ENV_VARS
                if os.environ.get(config.ENV_VARS[p]) and not config.load_config().get("keys", {}).get(p)]
    if env_only:
        click.echo(click.style(
            f"  참고: 아이콘으로 띄우면 셸 환경변수({', '.join(env_only)})는 상속되지 않습니다 — "
            "홈 화면의 'AI 설정'에 키를 저장하세요.", fg="yellow"))


@cli.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--style",
    type=click.Choice(["keep", "authoryear", "short"]),
    default=None,
    help="인용 스타일: keep([n]) | authoryear([저자 연도]) | short([단축명]). 미지정 시 config 기본값.",
)
@click.option("--no-links", is_flag=True, help="참고문헌 DOI/arXiv 하이퍼링크 비활성")
@click.option("--provider", type=click.Choice(config.PROVIDERS), default=None)
@click.option("--model", default=None)
def cite(workdir: Path, style: str | None, no_links: bool, provider: str | None, model: str | None) -> None:
    """참고문헌 파싱 + 본문 인용 링크/치환 (Stage 5).

    스타일/링크 미지정 시 manifest(sections.yaml) 값을 따른다 — 리뷰/UI에서 편집 가능.
    """
    wd = WorkDir(workdir)
    # CLI가 주면 오버라이드, 아니면 None → manifest 값 사용
    ref_links = False if no_links else None
    parts = config.style_to_parts(style) if style else None  # 다중 조합은 웹 UI/manifest에서
    try:
        prov = config.build_provider(provider, model)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    try:
        summary = pipeline.run_cite(wd, prov, parts=parts, reference_links=ref_links)
    except Exception as e:  # noqa: BLE001 — CiteError 등 사용자에게 친절히
        raise click.ClickException(str(e)) from e
    if summary.get("skipped"):
        click.echo("변경 없음 (이미 최신).")
        return
    click.echo(
        f"참고문헌 {summary['parsed']}개 파싱"
        + (f" (기각 {summary['rejected']})" if summary.get("rejected") else "")
        + f", 본문 인용 {summary['linked']}곳 링크"
        + (f", 범위 밖 {summary['skipped_range']}곳 무시" if summary.get("skipped_range") else "")
    )
    click.echo(f"비용 ≈ ${summary.get('cost_usd', 0):.4f} → {wd.en_md}")


@cli.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--provider", type=click.Choice(config.PROVIDERS), default=None)
@click.option("--model", default=None)
def glossary(workdir: Path, provider: str | None, model: str | None) -> None:
    """번역 전 용어집 자동 생성 (검토·수정용). 웹 UI 검토 흐름의 CLI 버전."""
    wd = WorkDir(workdir)
    try:
        prov = config.build_provider(provider, model)
        n = pipeline.run_glossary(wd, prov, regenerate=True)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(str(e)) from e
    click.echo(f"용어집 {n}개 생성 → {wd.glossary_yaml}")
    click.echo("검토·수정 후 `md4paper translate`를 실행하세요.")


@cli.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--provider", type=click.Choice(config.PROVIDERS), default=None)
@click.option("--model", default=None)
@click.option("--style", "korean_style", default=None, help="문체 override (해라체|합니다체|해요체|custom:...); 기본은 manifest 값")
@click.option("--yes", is_flag=True, help="용어집 편집 단계 건너뛰기")
def translate(
    workdir: Path, provider: str | None, model: str | None, korean_style: str | None, yes: bool
) -> None:
    """한국어 번역 → paper.ko.md (Abstract 컨텍스트 주입, 구조 검증)."""
    wd = WorkDir(workdir)
    try:
        prov = config.build_provider(provider, model)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    # 용어집 생성 → 검토/수정 → 번역 (웹 UI가 이 흐름을 그대로 얹는다)
    try:
        if not wd.glossary_yaml.exists():
            n = pipeline.run_glossary(wd, prov, regenerate=True)
            click.echo(f"용어집 {n}개 자동 생성 → {wd.glossary_yaml}")
        if not yes:
            click.echo("용어집을 검토·수정하세요 (저장 후 번역 진행).")
            edited = click.edit(wd.glossary_yaml.read_text(encoding="utf-8"), extension=".yaml")
            if edited is not None:
                wd.glossary_yaml.write_text(edited, encoding="utf-8")
        summary = pipeline.run_translate(wd, prov, korean_style=korean_style)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(str(e)) from e

    click.echo(
        f"청크 {summary['chunks']}개 (신규 {summary['ok'] + summary['retried']}, 캐시 {summary['cached']}, "
        f"재시도 {summary['retried']}, 통과 {summary['passthrough']}), 용어 {summary['glossary']}개"
    )
    if summary["passthrough"]:
        click.echo(click.style(f"  경고: {summary['passthrough']}개 청크는 구조 검증 실패로 영어 원문 유지.", fg="yellow"))
        # 개수만 알려 주면 원인을 못 좁힌다 — 어느 섹션이 어떤 불변식을 어겼는지까지 적는다.
        for sid, reason in (summary.get("failures") or {}).items():
            click.echo(click.style(f"    · {sid} — {reason}", fg="yellow"))
    click.echo(f"비용 ≈ ${summary.get('cost_usd', 0):.4f} → {wd.ko_md}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

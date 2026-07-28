"""md4paper CLI — click 커맨드 그룹. 로직은 각 단계 모듈에, 여기는 배선만."""

from __future__ import annotations

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


@cli.command()
@click.argument("workdir", required=False, type=click.Path(path_type=Path))
@click.option("--upload-dir", type=click.Path(path_type=Path), default=None, help="업로드 파일·결과 저장 위치 (기본: 프로젝트 output/ 폴더)")
@click.option("--port", default=8080, help="로컬 포트")
@click.option("--no-show", is_flag=True, help="브라우저 자동 열기 비활성")
def ui(workdir: Path | None, upload_dir: Path | None, port: int, no_show: bool) -> None:
    """로컬 웹 UI (NiceGUI). WORKDIR 없이 실행하면 PDF 업로드 홈에서 시작.

    섹션 트리 리뷰 · 마크다운/수식 프리뷰 · PDF 대조 · 용어집 검토 · 번역.
    """
    wd = None
    if workdir is not None:
        wd = WorkDir(workdir)
        if not wd.sections_yaml.exists():
            raise click.ClickException(f"매니페스트 없음: {wd.sections_yaml}. 먼저 convert를 실행하세요.")
    try:
        from md4paper.ui.app import run as run_ui
    except ImportError as e:
        raise click.ClickException("웹 UI 의존성 미설치 — `uv sync --extra ui` 실행하세요.") from e
    where = "업로드 홈" if wd is None else f"리뷰: {wd.root}"
    click.echo(f"UI 시작 ({where})…")  # 실제 주소는 run_ui가 출력 (포트 충돌 시 자동 대체)
    run_ui(wd, upload_dir=upload_dir, port=port, show=not no_show)


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
    click.echo(f"비용 ≈ ${summary.get('cost_usd', 0):.4f} → {wd.ko_md}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

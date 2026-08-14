"""레이아웃 자동 수정 — 추출 과정에서 깨진 마크다운을 나눠서 LLM으로 손본다.

고치는 대상은 en.md가 아니라 **raw.md**다. en.md는 raw.md + 매니페스트로 매번 다시
조립되므로(assemble), en.md만 고치면 레벨을 한 번 바꾸는 순간 사라진다. raw.md를 고친 뒤
구조를 다시 잡으면 섹션 트리·일괄 레벨 조정·프리뷰가 모두 새 구조를 그대로 따라간다.

안전장치 셋 — LLM이 문서를 통째로 다시 쓰는 자리라 보수적으로 간다:
  1) 이미지·코드 블록은 센티넬로 가려 LLM이 건드릴 수 없게 한다.
  2) 청크마다 '영숫자 내용'이 보존됐는지 검사한다. 레이아웃 수정은 기호와 줄바꿈만 바꾸므로
     이 키는 그대로여야 한다("x t ." → "$x_t$." 도, "## 2"+"## Background" → "## 2 Background"도
     키가 같다). 어긋나면 한 번 재시도하고, 그래도 어긋나면 그 청크는 **원문을 그대로** 둔다.
  3) 적용 직전 raw.md·sections.yaml·blocks.json을 스냅샷으로 남겨 통째로 되돌릴 수 있다.
"""

from __future__ import annotations

import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from md4paper import config, prefs
from md4paper.ir import Flavor, Manifest
from md4paper.review import manifest as manifest_io
from md4paper.workdir import WorkDir, hash_text

_TARGET_CHARS = 4000  # 청크 목표 크기 — 호출 수(비용)와 한 번에 보는 문맥의 절충
_MIN_KEPT = 0.95      # 원문 영숫자 내용 보존 하한. 문단이 통째로 사라지면(≈10%) 걸린다.
_MAX_GROWTH = 1.15    # 늘어남 상한 — 설명·요약 등 없던 내용이 붙는 것을 막는다
_MAX_TOKENS = 8192

_ATX = re.compile(r"^#{1,6}\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")
# 비교용 내용 키에서 남길 문자 외 전부 제거 (기호·공백·마크다운 표기는 무시)
_NON_CONTENT = re.compile(r"[^0-9a-z가-힣]+")
# 답 전체를 코드펜스로 감싸는 흔한 습관 (```markdown … ```)
_FENCE_WRAP = re.compile(r"^```[A-Za-z]*\n(.*)\n```$", re.DOTALL)

# LLM이 손대면 안 되는 스팬 — 바이트가 그대로여야 하는 것들만 가린다.
# (인라인 수식·표·헤더는 고쳐야 하는 대상이므로 가리지 않는다.)
_PROTECT = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"~~~.*?~~~", re.DOTALL),
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),        # 마크다운 이미지
    re.compile(r"!?\[\[[^\]]*\]\]"),            # Obsidian 위키링크 임베드
    re.compile(r"<img\s[^>]*>", re.IGNORECASE),  # HTML 이미지
)

_SNAPSHOT = (("raw_md", "raw.pre-fix.md"),
             ("sections_yaml", "sections.pre-fix.yaml"),
             ("blocks_json", "blocks.pre-fix.json"))

# 매니페스트의 문서 수준 설정 — 구조를 다시 잡아도 사용자가 정한 값은 그대로 이어받는다
_DOC_FIELDS = ("citation_parts", "reference_links", "flavor", "caption_style", "runin_headings",
               "korean_style", "translate_headers", "translate_references", "author_parts")

_SYSTEM = """You repair the LAYOUT of Markdown that was extracted from the PDF of an academic paper.
The extractor kept the words but lost the structure. Fix the structure; never rewrite the words.

FIX these:
- A heading split across lines: "## 2" followed by "## Background" is ONE heading -> "## 2 Background".
- A caption, a sentence or a table row that was wrongly marked up as a heading -> plain paragraph.
- A real heading that lost its "#" marker (a short title line sitting alone) -> mark it as a heading
  at the level its numbering implies.
- Broken inline math, where the extractor dropped sub/superscripts and spaced the pieces out:
  "x t" -> "$x_t$", "h ( x t )" -> "$h(x_t)$", "R over T cycles" stays prose but
  "f : ( x, h ( x )) -> { T, F }" -> "$f: (x, h(x)) \\to \\{T, F\\}$". Use the spacing and the
  surrounding sentence to decide what was a subscript; if you cannot tell, leave it alone.
- Paragraphs broken by a blank line mid-sentence -> join them. Words hyphenated by line wrapping
  ("criti-" + "cal component") -> rejoin into one word.
- List items that lost their "-" / "1." markers, and list markers that leaked into a paragraph.
- Table rows flattened into running text, but ONLY when the columns are unambiguous.

NEVER do these:
- Never translate, summarize, reword, paraphrase, or fix grammar/spelling. Every word of the input
  must survive in the output, in the same order.
- Never add commentary, notes, or a code fence around your answer.
- Never alter a ⟦MD4_n⟧ token - those stand for images and code blocks that were hidden from you.
  Copy each one exactly, once, where it stands.
- Never renumber sections or change the WORDS of a heading; only fix how it is marked up.
- If a passage is already fine, output it byte-for-byte unchanged.

Output ONLY the repaired Markdown for the fragment you are given."""


class LayoutFixError(RuntimeError):
    """레이아웃 수정 실패 — 호출부가 사용자에게 그대로 보여준다."""


# --- 청킹 ------------------------------------------------------------------


def _cut_points(lines: list[str]) -> set[int]:
    """청크를 끊어도 되는 줄 인덱스 — 펜스 밖의 문단/헤더 경계.

    **직전 실질 줄이 헤더면 끊지 않는다**: 쪼개진 헤더("## 2" 다음 줄 "## Background")가
    청크 경계로 갈라지면 어느 쪽에서도 합칠 수 없다 — 이게 정확히 고쳐야 할 대상이다.
    """
    points: set[int] = set()
    in_fence = False
    last_solid = ""  # 직전의 빈 줄 아닌 줄
    for i, line in enumerate(lines):
        fence = bool(_FENCE.match(line))
        if i and not in_fence and not fence and not _ATX.match(last_solid) \
                and (_ATX.match(line) or not lines[i - 1].strip()):
            points.add(i)
        if fence:
            in_fence = not in_fence
        if line.strip():
            last_solid = line
    return points


def split_for_fix(raw_md: str, target_chars: int = _TARGET_CHARS) -> list[str]:
    """마크다운을 수정 단위로 분할. "\\n".join(결과)는 원문(끝 개행 제외)과 정확히 같다.

    라운드트립이 보장돼야 LLM이 고치지 못한 청크를 원문 그대로 되돌려 놓을 수 있다.
    """
    lines = raw_md.splitlines()
    if not lines:
        return []
    points = _cut_points(lines)
    bounds = [0]
    size = 0
    for i, line in enumerate(lines):
        if i in points and size >= target_chars:
            bounds.append(i)
            size = 0
        size += len(line) + 1
    bounds.append(len(lines))
    parts = ["\n".join(lines[a:b]) for a, b in zip(bounds, bounds[1:])]
    if len(parts) > 1 and len(parts[-1]) < target_chars // 3:  # 자투리는 앞 청크에 흡수
        parts[-2:] = ["\n".join(parts[-2:])]
    return parts


# --- 보호 / 검증 -----------------------------------------------------------


def _protect(text: str) -> tuple[str, dict[str, str]]:
    """이미지·코드 블록을 센티넬로 치환. (치환된 텍스트, 복원맵)."""
    store: dict[str, str] = {}

    def make(m: re.Match) -> str:
        key = f"⟦MD4_{len(store)}⟧"
        store[key] = m.group(0)
        return key

    for pat in _PROTECT:
        text = pat.sub(make, text)
    return text, store


def _restore(text: str, store: dict[str, str]) -> str:
    for key, val in store.items():
        text = text.replace(key, val)
    return text


def content_key(text: str) -> str:
    """비교용 내용 키 — 소문자 영숫자·한글만 남긴다.

    레이아웃 수정은 기호와 줄바꿈만 건드려야 하므로 이 키는 (거의) 그대로여야 한다.
    """
    return _NON_CONTENT.sub("", text.lower())


def check(before: str, after: str) -> list[str]:
    """수정본이 원문 내용을 보존했는지. 문제 목록(비면 통과)."""
    if not after.strip():
        return ["빈 응답"]
    a, b = content_key(before), content_key(after)
    if not a:
        return []
    matched = sum(bl.size for bl in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    problems: list[str] = []
    if matched / len(a) < _MIN_KEPT:
        problems.append(f"원문 내용의 {round((1 - matched / len(a)) * 100)}%가 사라짐")
    if len(b) > len(a) * _MAX_GROWTH:
        problems.append("원문에 없던 내용이 늘어남")
    return problems


def build_system_prompt(instructions: str = "") -> str:
    """고정 시스템 프롬프트 + 사용자가 모달에 적은 추가 지시."""
    extra = (instructions or "").strip()
    if not extra:
        return _SYSTEM
    return (_SYSTEM + "\n\nEXTRA INSTRUCTIONS FROM THE USER (follow these as well; the rules above "
            "about never changing the words still win if they conflict):\n" + extra)


def _match_edges(out: str, source: str) -> str:
    """원문 청크의 앞뒤 빈 줄을 결과에도 그대로 붙인다.

    청크는 "\\n"으로 다시 이어붙이므로, 끝 빈 줄이 사라지면 다음 청크의 헤더가 앞 문단에 붙는다.
    """
    lead = source[: len(source) - len(source.lstrip("\n"))]
    trail = source[len(source.rstrip("\n")):]
    return lead + out.strip("\n") + trail


def _unwrap(out: str, source: str) -> str:
    """답 전체를 감싼 코드펜스를 벗긴다 (원문이 펜스로 시작하지 않을 때만)."""
    if source.lstrip().startswith(("```", "~~~")):
        return out
    m = _FENCE_WRAP.match(out.strip())
    return m.group(1) if m else out


def _fix_once(provider, system: str, source: str) -> tuple[str, list[str]]:  # noqa: ANN001
    """한 청크: 보호 → 호출 → 센티넬 검증 → 복원. 반환: (수정본, 문제)."""
    protected, store = _protect(source)
    out = _unwrap(provider.complete(system, protected, max_tokens=_MAX_TOKENS), source)
    problems = ["보호 토큰(이미지·코드 블록) 훼손"] if any(out.count(k) != 1 for k in store) else []
    return _match_edges(_restore(out, store), source), problems


def fix_chunk(provider, system: str, source: str, *, max_retries: int = 1) -> tuple[str, str, list[str]]:  # noqa: ANN001
    """청크 하나 수정 + 검증 재시도 사다리.

    반환: (텍스트, 상태[fixed|unchanged|kept], 문제). kept면 검증에 걸려 **원문 그대로** 둔 것.
    """
    if not source.strip():
        return source, "unchanged", []
    sys_prompt = system
    problems: list[str] = []
    for attempt in range(max_retries + 1):
        try:
            out, problems = _fix_once(provider, sys_prompt, source)
        except Exception:  # noqa: BLE001 — 일시적 오류는 한 번 더, 계속 실패하면 호출부로 올린다
            if attempt >= max_retries:
                raise
            sys_prompt = system
            continue
        problems += check(source, out)
        if not problems:
            return out, ("fixed" if out != source else "unchanged"), []
        sys_prompt = (system + "\n\n[RETRY] Your previous answer was rejected: "
                      + ", ".join(problems)
                      + ". Reproduce every word of the input; change only the markup.")
    return source, "kept", problems


def fix_markdown(raw_md: str, provider, *, instructions: str = "",  # noqa: ANN001
                 on_progress=None, workers: int | None = None) -> tuple[str, dict]:
    """마크다운 전체를 나눠 수정. 반환: (수정본, 요약). LLM만 쓰고 파일은 건드리지 않는다."""
    chunks = split_for_fix(raw_md)
    if not chunks:
        return raw_md, {"chunks": 0, "fixed": 0, "unchanged": 0, "kept": 0, "problems": {}}

    system = build_system_prompt(instructions)
    results = list(chunks)
    counts = {"fixed": 0, "unchanged": 0, "kept": 0}
    problems: dict[int, list[str]] = {}
    done = [0]
    lock = threading.Lock()

    def work(i: int) -> None:
        text, status, probs = fix_chunk(provider, system, chunks[i])
        with lock:
            results[i] = text
            counts[status] += 1
            if probs:
                problems[i] = probs
            done[0] += 1
            if on_progress is not None:
                on_progress(done[0], len(chunks))

    # 청크 간 결과 의존이 없으므로 번역과 같은 동시 실행 노브를 그대로 쓴다
    max_workers = workers if workers is not None else config.resolve_translate_workers()
    if max_workers <= 1 or len(chunks) <= 1:
        for i in range(len(chunks)):
            work(i)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed([ex.submit(work, i) for i in range(len(chunks))]):
                fut.result()  # 예외는 여기서 올라온다

    fixed = "\n".join(results)
    if raw_md.endswith("\n"):
        fixed += "\n"
    return fixed, {"chunks": len(chunks), **counts, "problems": problems}


# --- 스냅샷 (되돌리기) -----------------------------------------------------


def _snapshot_paths(wd: WorkDir) -> list[tuple[Path, Path]]:
    """[(원본, 스냅샷)] — 스냅샷은 원본 옆에 .pre-fix 이름으로 둔다."""
    out: list[tuple[Path, Path]] = []
    for attr, name in _SNAPSHOT:
        src: Path = getattr(wd, attr)
        out.append((src, src.with_name(name)))
    return out


def snapshot(wd: WorkDir) -> None:
    """수정 직전 상태를 남긴다. 원본이 없는 항목의 옛 스냅샷은 지운다(짝이 어긋나지 않게)."""
    for src, dst in _snapshot_paths(wd):
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()


def has_snapshot(wd: WorkDir) -> bool:
    return wd.raw_md.with_name(_SNAPSHOT[0][1]).exists()


def restore(wd: WorkDir) -> bool:
    """스냅샷으로 되돌리고 en.md를 다시 조립. 되돌렸으면 True (스냅샷은 소비된다)."""
    from md4paper import pipeline

    if not has_snapshot(wd):
        return False
    for src, dst in _snapshot_paths(wd):
        if dst.exists():
            shutil.copy2(dst, src)
            dst.unlink()
    raw = wd.raw_md.read_text(encoding="utf-8")
    status = wd.load_status()
    status.pop("structure", None)  # 되돌린 raw.md에 맞는 해시를 모르므로 다음 실행이 다시 잡게
    wd.save_status(status)
    pipeline.run_assemble(wd, force=True)
    _refresh_frontmatter_hash(wd, raw)
    return True


# --- 구조 재구축 -----------------------------------------------------------


def inherit(new: Manifest, old: Manifest) -> None:
    """문서 설정과 헤더별 사용자 선택을 새 매니페스트로 옮긴다.

    구조 자체는 고친 raw.md에서 새로 잡아야 섹션 트리가 실제 문서를 따른다. 다만 사용자가 정한
    문서 설정(인용 표기·문체 등), '자동값과 다른 레벨'(=실제 교정), 번역 제외 표시는 헤더 이름으로
    이어받는다 — 번호가 붙거나 떨어져 나가도 prefs.norm_key가 같은 이름으로 묶어준다.
    """
    for field in _DOC_FIELDS:
        setattr(new, field, getattr(old, field))
    if not new.title:
        new.title = old.title
    prev: dict[str, object] = {}
    for s in old.sections:
        prev.setdefault(prefs.norm_key(s.text), s)
    for s in new.sections:
        o = prev.get(prefs.norm_key(s.text))
        if o is None:
            continue
        s.translate = o.translate
        if o.auto_level is not None and o.level != o.auto_level and not s.is_title:
            s.level = o.level
            s.needs_review = False


def _refresh_frontmatter_hash(wd: WorkDir, raw: str) -> None:
    """앞부분 정규화 캐시를 지금의 raw.md에 맞춘다.

    안 맞추면 나중에 재변환할 때 front matter 단계가 '아직 정규화 안 됨'으로 보고 다시 손대
    방금 고친 앞부분을 되돌린다(+ LLM 재호출).
    """
    status = wd.load_status()
    entry = status.get("frontmatter")
    if isinstance(entry, dict) and entry.get("output_hash"):
        entry["output_hash"] = hash_text(raw)
        wd.save_status(status)


def rebuild(wd: WorkDir, old: Manifest | None = None, provider=None) -> Manifest:  # noqa: ANN001
    """고친 raw.md로 구조(sections.yaml·blocks.json)를 다시 잡고 en.md를 재조립."""
    from md4paper import pipeline

    raw = wd.raw_md.read_text(encoding="utf-8")
    flavor = old.flavor if old is not None else Flavor(config.resolve_flavor())
    new = pipeline.run_structure(wd, flavor=flavor, force=True, provider=provider)
    if old is not None:
        inherit(new, old)
        manifest_io.save(new, wd)
    pipeline.run_assemble(wd, force=True)
    _refresh_frontmatter_hash(wd, raw)
    return new


def run(wd: WorkDir, provider, *, instructions: str = "", manifest: Manifest | None = None,  # noqa: ANN001
        on_progress=None, workers: int | None = None) -> dict:
    """레이아웃 수정 전 과정 — raw.md 수정 → 스냅샷 → 구조 재구축 → en.md 재조립.

    on_progress(done, total)로 청크 진행을 보고한다. 요약 dict 반환(changed=False면 아무것도 안 씀).
    """
    if not wd.raw_md.exists():
        raise LayoutFixError(f"raw.md 없음: {wd.raw_md}. 먼저 변환을 실행하세요.")
    old = manifest
    if old is None and wd.sections_yaml.exists():
        old = manifest_io.load(wd)

    raw = wd.raw_md.read_text(encoding="utf-8")
    fixed, summary = fix_markdown(raw, provider, instructions=instructions,
                                  on_progress=on_progress, workers=workers)
    summary["changed"] = fixed != raw
    if summary["changed"]:
        snapshot(wd)  # 수정 직전 상태 (되돌리기 1단계)
        wd.raw_md.write_text(fixed, encoding="utf-8")
        rebuild(wd, old, provider=provider)
    summary["cost_usd"] = provider.cost()
    return summary

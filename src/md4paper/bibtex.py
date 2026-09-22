"""프로젝트 폴더에 쌓이는 BibTeX — 논문 하나에 항목 하나, 그대로 복사해 붙이도록.

저장 위치에 마크다운·PDF가 쌓이는 것과 같은 방식으로, 같은 폴더의 `references.bib` 한 장에
서지 항목이 **계속 덧붙는다**. 논문을 다시 내보내면 새 항목이 생기는 게 아니라 **같은 키의
항목만 갈아치운다**(서지 정보를 보강하면 그 자리에서 최신이 된다).

```bibtex
@inproceedings{2017_Attention_Vaswani,
  author    = {Vaswani, Ashish and Shazeer, Noam},
  booktitle = {Advances in Neural Information Processing Systems},
  title     = {{Attention Is All You Need}},
  year      = {2017}
}
```

인용 키는 논문 폴더의 기준명(이름 규칙)이라 마크다운·PDF 파일명과 **같은 이름**이다 — 노트에
`[[2017_Attention_Vaswani]]`로 적어 둔 것과 `\\cite{2017_Attention_Vaswani}`가 어긋나지 않는다.

## 문자열은 손으로 만들지 않는다 — bibtexparser로 만들고, 그걸로 검증한다

논문 제목에는 `&`, `%`, `_`, `$`, 악센트 문자가 흔히 들어간다. 이스케이프를 직접 짜면 언젠가
`\\cite`가 아니라 **LaTeX 컴파일이** 깨지고, 그 사실을 몇 주 뒤 논문 마감에 알게 된다. 그래서:

1. 값 이스케이프는 `bibtexparser.latexenc.string_to_latex`가 한다.
2. 항목 직렬화는 `BibTexWriter`가 한다 (우리가 중괄호를 찍지 않는다).
3. 만든 항목을 **다시 파싱해** 키·종류·필드가 그대로 돌아오는지 확인한다(`verify`).
4. 파일에 쓴 뒤에도 **파일 전체를 다시 파싱해** 항목 수가 줄지 않았는지 본다. 어긋나면
   쓰기 전 내용으로 되돌린다 — 쌓아 둔 참고문헌 파일을 우리가 깨뜨리는 일은 없어야 한다.

파일을 통째로 재직렬화하지는 **않는다**. 사용자가 손으로 넣은 항목·주석은 우리 것이 아니므로
건드리지 않고, 우리 키의 블록만 갈아치운다(그래서 '정리'가 아니라 '검증'이다).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

BIB_NAME = "references.bib"

# venue 문구로 학회/저널을 가른다 (@inproceedings의 booktitle vs @article의 journal)
_CONF_RE = re.compile(
    r"proceedings|proc\.|conference|symposium|workshop|congress|meeting|\bCHI\b|\bCSCW\b", re.I)
_ARXIV_RE = re.compile(r"arxiv|preprint", re.I)
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
# BibTeX 키에 쓸 수 없는 문자 (공백·쉼표·중괄호 등) — 기준명에 공백이 들어갈 수 있다
_KEY_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_:.\-]+")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")
_SEP_RUN_RE = re.compile(r"[_\-.:]{2,}")
# 한글 음절(U+AC00~U+D7A3)은 이스케이프에서 빼야 한다 — 아래 _escape 주석 참고
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]+")
# bibtexparser\uc758 \ubcc0\ud658 \ud45c\uac00 **\ub193\uce58\ub294** \ubb38\uc790\ub9cc \uc5ec\uae30\uc11c \ucc98\ub9ac\ud55c\ub2e4. \ud45c\uac00 \uc81c\ub300\ub85c \ud558\ub294 \uac83(\ud070\ub530\uc634\ud45c
# `\u201c\u201d`\u2192`\textquotedblleft/right`, \uc545\uc13c\ud2b8)\uc740 \uac74\ub4dc\ub9ac\uc9c0 \uc54a\ub294\ub2e4 \u2014 \ubbf8\ub9ac `` ` ``\ub85c \ubc14\uafd4 \ub450\uba74 \ud45c\uac00
# \uadf8\uac78 \ub2e4\uc2dc `\textasciigrave`\ub85c \ub9cc\ub4e4\uc5b4 \ub418\ub808 \uae68\uc9c4\ub2e4.
# \uc2e4\uce21: \uc791\uc740\ub530\uc634\ud45c `\u2019`\uc640 \ub300\uc2dc `\u2013`\ub294 \ud45c\ub97c \uadf8\ub0e5 \ud1b5\uacfc\ud574 \ube44ASCII\ub85c .bib\uc5d0 \ub0a8\uc558\ub2e4.
_TYPO = (("\u2014", "---"), ("\u2013", "--"), ("\u2018", "'"), ("\u2019", "'"),
         ("\u2026", "..."), ("\u00a0", " "), ("\u2212", "-"))
# bibtexparser\uac00 \uace7\uc740 \uc544\ud3ec\uc2a4\ud2b8\ub85c\ud53c\ub97c `\textquotesingle`\ub85c \uacfc\uc789 \ubcc0\ud658\ud55c \uac83\uc744 \ub418\ub3cc\ub9b0\ub2e4
# (\uc2e4\uce21: `CHI '24` \u2192 `CHI \textquotesingle 24` \u2192 \uc870\ud310 \uacb0\uacfc `CHI \u203224`).
_UNQUOTE_RE = re.compile(r"\\textquotesingle\s?")

# \uc11c\uc9c0 \uae30\ub85d\uc5d0\uc11c \uadf8\ub300\ub85c \uc62e\uaca8 \uc801\ub294 \ud544\ub4dc \u2014 \ud56d\ubaa9 \uc885\ub958\uc5d0 \uad00\uacc4\uc5c6\uc774 \uc788\uc73c\uba74 \uc4f4\ub2e4.
_EXTRA_FIELDS = ("volume", "number", "pages", "publisher", "series", "address", "editor",
                 "school", "institution", "edition", "isbn", "issn", "month", "note")
# \ud56d\ubaa9 \uc885\ub958\ub85c \uc4f8 \uc218 \uc788\ub294 \uac12 (\uc11c\uc9c0 \uae30\ub85d\uc774 \uc5c9\ub6b1\ud55c \uc885\ub958\ub97c \uc8fc\uba74 venue \uaddc\uce59\uc73c\ub85c \ub418\ub3cc\uc544\uac04\ub2e4)
_KINDS = ("article", "inproceedings", "incollection", "book", "inbook", "phdthesis",
          "mastersthesis", "techreport", "misc", "unpublished", "proceedings")
# \uc774 \uc885\ub958\ub4e4\uc740 venue\ub97c booktitle\uc5d0 \uc801\ub294\ub2e4 (@article\ub9cc journal, \ub098\uba38\uc9c0\ub294 \ubcc4\ub3c4 \ud544\ub4dc\uac00 \uc5c6\ub2e4)
_BOOKTITLE_KINDS = ("inproceedings", "incollection", "inbook", "proceedings")


def cite_key(stem: str) -> str:
    """논문 기준명 → BibTeX 인용 키. ASCII 기준명은 이름 그대로, 아니면 짧은 해시를 붙인다.

    키를 ASCII로 유지하는 이유: biber/biblatex는 UTF-8 키를 받지만 고전 bibtex는 깨진다
    (Overleaf에서 pdflatex+bibtex를 쓰는 사람이 아직 많다).
    다만 **비ASCII를 그냥 지우면 서로 다른 논문이 같은 키가 된다** — `2024_가논문_이`와
    `2024_나논문_김`이 둘 다 `2024_`가 되어, 뒤에 내보낸 논문이 앞 항목을 조용히 갈아치운다
    (`sync`의 안전장치는 항목 **수**만 보므로 이걸 못 잡는다). 그래서 지운 글자가 있으면
    원래 기준명의 해시 6자를 붙여 유일성을 만든다.
    """
    raw = str(stem or "").strip()
    ascii_only = _KEY_UNSAFE_RE.sub("", raw.replace(" ", ""))
    key = _SEP_RUN_RE.sub("_", ascii_only).strip("_-.:")
    if key and not _NON_ASCII_RE.search(raw):
        return key
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]  # noqa: S324 — 유일성만 필요
    return f"{key}-{suffix}" if key else f"ref-{suffix}"


def _escape(text: str) -> str:
    r"""LaTeX 이스케이프 — bibtexparser의 변환 표를 쓴다 (직접 치환하지 않는다).

    다만 **한글 음절은 그 표를 통과시키지 않는다.** bibtexparser 1.4의 변환 표는 한글
    11,172자 중 877자를 수학 기호로 잘못 바꾼다(실측: `한`→`\mathbb{k}`, `훈`→`\mathbf{\Eta}`).
    그러면 한국어 제목·저자가 손상된 채 .bib에 들어가고, `verify`는 같은 손상 값끼리
    비교하므로 통과해 버린다. 한자·가나·그리스·키릴·악센트 문자는 표가 제대로 처리하므로
    (한자·가나는 그대로 통과, 나머지는 올바른 명령으로) 한글만 갈라 그대로 둔다.

    표가 **두 군데서 더 어긋난다**(실측, NIRVANA references.bib):

    - 곧은 작은따옴표를 `\textquotesingle`로 바꾼다 — `CHI '24`가 `CHI \textquotesingle 24`가
      되어 조판 결과가 `CHI ′24`(프라임 기호)로 나온다. 아포스트로피는 BibTeX에서 그냥 써도
      되는 문자라 되돌린다.
    - 굽은 따옴표 `’`(U+2019)는 **그대로 통과시킨다** — 표에 없다. 그러면 비ASCII가 .bib에
      남아 `inputenc` 설정에 따라 컴파일이 깨진다. 그래서 표에 넣기 전에 타이포그래피를
      곧은 문자로 먼저 정리한다(`_TYPO`).
    """
    from bibtexparser.latexenc import string_to_latex

    flat = " ".join(str(text or "").split())
    for src, dst in _TYPO:
        flat = flat.replace(src, dst)
    parts = _HANGUL_RE.split(flat)
    hangul = _HANGUL_RE.findall(flat)
    out = []
    for i, part in enumerate(parts):
        out.append(string_to_latex(part) if part else "")
        if i < len(hangul):
            out.append(hangul[i])
    return _UNQUOTE_RE.sub("'", "".join(out)).strip()


def _author(name: str) -> str:
    """'Ashish Vaswani' → 'Vaswani, Ashish' (BibTeX 관례).

    한 단어 이름(한국어 이름 '김영희', 기관명 등)은 중괄호로 감싼다 — BibTeX에 "이건 성·이름으로
    쪼개지 않는 한 덩어리"라고 알리는 표준 방법이다. 안 감싸면 '김영희'가 이름(first)으로 취급돼
    저자 목록이 'ㄱ.'처럼 이니셜만 남는 스타일이 있다.
    """
    parts = str(name or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return "{" + _escape(parts[0]) + "}"
    return f"{_escape(parts[-1])}, {_escape(' '.join(parts[:-1]))}"


def _authors(names) -> str:  # noqa: ANN001 — list[str]
    joined = [_author(n) for n in (names or []) if str(n).strip()]
    return " and ".join(a for a in joined if a)


def entry_kind(venue: str) -> str:
    """venue 문구로 고르는 항목 종류 — 학회는 inproceedings, 저널은 article, 없으면 misc."""
    if not venue:
        return "misc"
    if _ARXIV_RE.search(venue):
        return "misc"
    return "inproceedings" if _CONF_RE.search(venue) else "article"


def fields_from_meta(meta: dict, key: str) -> dict:
    """서지 dict → bibtexparser 항목 dict (값은 이스케이프까지 끝난 상태).

    두 종류의 입력을 같은 함수가 받는다. `paper_meta.json`(PDF에서 읽은 제목·저자·연도·venue)
    만 있으면 예전처럼 venue 문구로 종류를 추정하고, 논문 API가 준 출판 기록(§bibsource)이면
    거기 담긴 `kind`와 pages·volume·publisher까지 그대로 적는다. **출판 기록이 이긴다** —
    종류를 추정하지 않고 받은 대로 쓴다.
    """
    venue = " ".join(str(meta.get("venue") or "").split())
    given = str(meta.get("kind") or "").lower()
    kind = given if given in _KINDS else entry_kind(venue)
    entry = {"ENTRYTYPE": kind, "ID": key}
    title = str(meta.get("title") or "").strip()
    if title:  # 중괄호로 감싼다 — 스타일이 제목의 대문자를 소문자로 내리지 않게
        entry["title"] = "{" + _escape(title) + "}"
    # 출판 기록이 준 저자 문자열이 있으면 그대로 쓴다 (전치사가 든 성을 우리가 다시 접지 않게)
    authors = _escape(str(meta["author_latex"])) if meta.get("author_latex") \
        else _authors(meta.get("authors"))
    if authors:
        entry["author"] = authors
    arxiv = _ARXIV_ID_RE.search(str(meta.get("arxiv") or "")) or _ARXIV_ID_RE.search(venue)
    if venue:
        if kind in _BOOKTITLE_KINDS:
            entry["booktitle"] = _escape(venue)
        elif kind == "article":
            entry["journal"] = _escape(venue)
        elif not arxiv:  # @book·@misc 등 venue를 적을 표준 필드가 없는 종류
            entry["howpublished"] = _escape(venue)
    # eprint는 **출판 기록이 없을 때만**. 학회·저널에 실린 논문에 arXiv 번호까지 달면 프리프린트를
    # 인용한 것처럼 보이고, 스타일에 따라 학회명 대신 arXiv가 찍힌다.
    if arxiv and not (entry.get("booktitle") or entry.get("journal")):
        entry["eprint"] = arxiv.group(1)
        entry["archiveprefix"] = "arXiv"
    if meta.get("year"):
        entry["year"] = str(meta["year"])
    for name in _EXTRA_FIELDS:
        value = str(meta.get(name) or "").strip()
        if value:
            entry[name] = _escape(value)
    doi = str(meta.get("doi") or "").strip()
    if doi:
        entry["doi"] = doi.replace("https://doi.org/", "")
    return entry


def render(entry: dict) -> str:
    """항목 dict → BibTeX 문자열. 직렬화는 bibtexparser가 한다."""
    from bibtexparser.bibdatabase import BibDatabase
    from bibtexparser.bwriter import BibTexWriter

    db = BibDatabase()
    db.entries = [dict(entry)]
    writer = BibTexWriter()
    writer.indent = "  "
    writer.align_values = True
    return writer.write(db).strip() + "\n"


def parse(text: str) -> list[dict]:
    """BibTeX 문자열 → 항목 dict 목록 (bibtexparser). 못 읽는 부분은 조용히 무시된다."""
    import bibtexparser
    from bibtexparser.bparser import BibTexParser

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    try:
        return bibtexparser.loads(text, parser).entries
    except Exception:  # noqa: BLE001 — 손상된 .bib도 예외로 앱을 세우지 않는다
        return []


def verify(text: str, entry: dict) -> tuple[bool, str]:
    """만든 항목을 되읽어 뜻이 그대로인지 확인. 반환: (괜찮은가, 사람이 읽을 이유)."""
    got = parse(text)
    if len(got) != 1:
        return False, f"항목이 1개여야 하는데 {len(got)}개로 읽힙니다"
    back = got[0]
    if back.get("ID") != entry["ID"]:
        return False, f"인용 키가 달라졌습니다: {back.get('ID')!r}"
    if back.get("ENTRYTYPE") != entry["ENTRYTYPE"]:
        return False, f"항목 종류가 달라졌습니다: {back.get('ENTRYTYPE')!r}"
    for name, value in entry.items():
        if name in ("ID", "ENTRYTYPE"):
            continue
        if back.get(name.lower()) != value:
            return False, f"{name} 필드가 달라졌습니다: {back.get(name.lower())!r}"
    return True, "확인됨"


def entry_for(wd) -> tuple[str, str] | None:  # noqa: ANN001 — WorkDir
    """이 논문의 (인용 키, 검증된 BibTeX 항목). 서지 정보가 없거나 검증에 실패하면 None.

    받아 둔 출판 기록(§bibsource, `bib_source.json`)이 있으면 그걸 쓰고, 없으면 PDF에서 읽은
    `paper_meta.json`으로 조립한다. **네트워크를 쓰지 않는다** — 조회는 `bibsource.update_workdir`
    가 따로 하고 여기서는 저장된 것만 읽으므로, 내보내기는 오프라인에서도 늘 같은 속도다.
    """
    from md4paper import bibsource, paper_meta

    meta = paper_meta.load(wd)
    if not meta or not str(meta.get("title") or "").strip():
        return None
    key = cite_key(wd.root.stem)
    record = bibsource.load(wd)
    if record:  # 출판사가 이름을 이니셜로 등록해 둔 경우 PDF의 온전한 표기를 쓴다
        record = bibsource.prefer_fuller_authors(record, meta.get("authors"))
    entry = fields_from_meta(record or meta, key)
    text = render(entry)
    ok, _why = verify(text, entry)
    return (key, text) if ok else None


def _block_span(text: str, key: str) -> tuple[int, int] | None:
    """`@type{key, ...}` 항목의 (시작, 끝) 위치 — 중괄호를 세어 찾는다. 없으면 None.

    파일을 통째로 재직렬화하지 않으려면(사용자 항목 보존) 우리 블록의 자리를 알아야 한다.
    잘못 짚어도 `sync`가 쓴 뒤 파일 전체를 다시 파싱해 되돌리므로 파일이 깨지지는 않는다.
    """
    pattern = re.compile(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*[,}]", re.I)
    m = pattern.search(text)
    if not m:
        return None
    i = text.index("{", m.start())
    depth = 0
    for pos in range(i, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                while text[end:end + 1] == "\n":
                    end += 1
                return m.start(), end
    return m.start(), len(text)  # 닫히지 않은 항목 — 끝까지가 그 항목이다


def sync(path, key: str, entry: str) -> Path | None:  # noqa: ANN001 — Path | str
    """항목을 .bib에 반영 — 같은 키가 있으면 그 자리를 갈아치우고, 없으면 맨 뒤에 덧붙인다.

    쓴 뒤 파일 전체를 다시 파싱해 **항목 수가 줄지 않았고 우리 키가 있는지** 확인한다.
    어긋나면 쓰기 전 내용으로 되돌리고 None을 반환한다(쌓아 둔 파일을 깨뜨리지 않는다).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    before = {e.get("ID") for e in parse(old)} if old.strip() else set()
    span = _block_span(old, key)
    if span:  # 이미 있는 항목은 그 자리에서 갈아치운다 (앞뒤의 다른 항목은 그대로)
        tail = old[span[1]:]
        new = old[:span[0]] + entry + ("\n" if tail.strip() else "") + tail
    else:
        base = old.rstrip("\n")
        new = (base + "\n\n" if base else "") + entry
    if new == old:
        return p
    p.write_text(new, encoding="utf-8")
    after = {e.get("ID") for e in parse(new)}
    if key not in after or not (before - {key}) <= after:
        if old:  # 쓰기 전으로 되돌린다 — 우리가 넣은 항목보다 남의 항목이 중요하다
            p.write_text(old, encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
        return None
    return p


def remove(path, key: str) -> bool:  # noqa: ANN001 — Path | str
    """그 키의 항목을 .bib에서 지운다 (논문을 삭제했을 때). 지웠으면 True."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        old = p.read_text(encoding="utf-8")
    except OSError:
        return False
    span = _block_span(old, key)
    if not span:
        return False
    new = (old[:span[0]] + old[span[1]:]).lstrip("\n")
    try:
        p.write_text(new, encoding="utf-8")
    except OSError:
        return False
    if key in {e.get("ID") for e in parse(new)}:  # 못 지웠으면 되돌린다
        p.write_text(old, encoding="utf-8")
        return False
    return True


def keys_in(path) -> list[str]:  # noqa: ANN001 — Path | str
    """.bib에 들어 있는 인용 키 목록 (표시·중복 확인용)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        return [str(e.get("ID") or "") for e in parse(p.read_text(encoding="utf-8"))]
    except OSError:
        return []

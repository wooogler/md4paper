"""그림 영역 다시 지정 — 추출기가 bbox를 잘못 잡은 그림을 사람이 PDF 쪽에서 골라 다시 자른다.

Docling 그림 bbox는 가끔 어긋난다(옆 그림·캡션까지 물거나, 한 그림을 반쪽만 잡는다). 그림은 글자가
아니라서 LLM이 고칠 것도 없다 — 원본 PDF에서 맞는 영역을 다시 그리면 끝이다. 그래서 수식 팝오버의
영역 지정 화면을 그대로 쓰고, 고른 영역을 변환과 같은 배율(FIG_ZOOM, 288dpi)로 다시 렌더해 바꾼다.

**어느 파일을 바꾸나.** 원천은 `extract/images/img-NN.png`다. 조립이 이걸 `out/images/figure-N.png`로
이름만 바꿔 복사하고, 저장 위치(라이브러리) 사본도 같은 바이트다. 그래서 이름 대응표 없이 **옛 그림과
바이트가 같은 파일**을 찾아 함께 갈아 끼운다(figure_upscale과 같은 방식) — 다시 조립하지 않으므로
마크다운·번역·메모는 한 글자도 바뀌지 않는다. 바꾸기 전 그림은 `extract/figure-backup/`에 한 번 남겨
'원래 그림으로' 되돌릴 수 있다.

**지금 영역.** 새 변환은 그림마다 쪽·bbox를 `extract/figures.json`에 남긴다. 그 전에 변환한 논문은
그림을 PDF 쪽에서 템플릿 매칭으로 찾아(figure_upscale.locate) 처음 사각형으로 보여 준다 — 못 찾으면
빈 쪽에서 사람이 그린다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from md4paper.workdir import WorkDir

_MIN_SIDE_PT = 4.0


def load(wd: WorkDir) -> dict[str, dict]:
    try:
        data = json.loads(wd.figures_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 기록 없음
        return {}
    return data if isinstance(data, dict) else {}


def _save(wd: WorkDir, data: dict) -> None:
    wd.extract.mkdir(parents=True, exist_ok=True)
    wd.figures_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def source_pdf(wd: WorkDir) -> str | None:
    try:
        src = json.loads(wd.meta_json.read_text(encoding="utf-8")).get("source")
    except Exception:  # noqa: BLE001
        return None
    return src if src and str(src).lower().endswith(".pdf") else None


def extract_name(wd: WorkDir, out_name: str) -> str | None:
    """`out/images`의 그림 이름(figure-3.png) → 원천 `extract/images` 파일명(img-04.png).

    조립은 바이트 그대로 복사하므로 내용이 같은 파일을 찾는다. 수식 크롭은 그림이 아니다.
    """
    if not out_name or "/" in out_name or out_name.startswith("formula-"):
        return None
    out = wd.out_images / out_name
    if not out.is_file() or not wd.extract_images.is_dir():
        return None
    want = _digest(out.read_bytes())
    for f in sorted(wd.extract_images.iterdir()):
        if f.is_file() and not f.name.startswith("formula-") and _digest(f.read_bytes()) == want:
            return f.name
    return None


def where(wd: WorkDir, name: str) -> dict | None:
    """그 그림의 지금 자리 {page(1-based), bbox(좌상단 원점 pt), guess?} — 모르면 None."""
    rec = load(wd).get(name)
    if rec and rec.get("page") and rec.get("bbox"):
        return {"page": int(rec["page"]), "bbox": rec["bbox"]}
    src = source_pdf(wd)
    path = wd.extract_images / name
    if not src or not path.is_file():
        return None
    found = _locate(Path(src), path)
    if found is None:
        return None
    page0, score, rect, margin = found
    from md4paper import figure_upscale as fu

    sure = (score >= fu._MIN_SCORE and not (fu._SAME_EPS <= margin < fu._MIN_MARGIN)) or (
        score >= fu._LOW_SCORE and margin >= fu._LOW_MARGIN)
    out = {"page": page0 + 1, "bbox": [round(v, 1) for v in rect]}
    if sure:  # 확실한 것만 기록해 둔다 — 다음엔 쪽 전체를 훑지 않는다
        data = load(wd)
        data[name] = {**out, "located": True}
        _save(wd, data)
        return out
    return {**out, "guess": True}


def _locate(pdf: Path, path: Path):  # noqa: ANN202 — figure_upscale.locate 한 장 (배율을 맞춰서)
    """그림 한 장을 PDF에서 찾는다. 고해상도로 다시 그린 그림(dpi 기록)은 locate의 기준 배율(2)로 줄인다."""
    import tempfile

    from PIL import Image

    from md4paper import figure_upscale as fu

    try:
        with Image.open(path) as im:
            dpi = im.info.get("dpi")
            scale = (float(dpi[0]) / 72.0) if dpi else fu._BASE_SCALE
            with tempfile.TemporaryDirectory() as tmp:
                probe = Path(tmp) / path.name
                if abs(scale - fu._BASE_SCALE) > 0.05:
                    k = fu._BASE_SCALE / scale
                    im.convert("RGB").resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                                             Image.LANCZOS).save(probe, format="PNG")
                else:
                    im.convert("RGB").save(probe, format="PNG")
                return fu.locate(pdf, [probe]).get(path.name)
    except Exception:  # noqa: BLE001 — 짐작일 뿐이다; 못 찾으면 사람이 처음부터 고른다
        return None


def _copies(wd: WorkDir) -> list[Path]:
    """extract 원천이 복사돼 나가는 폴더들 — out/images와 저장 위치 사본(figure_upscale과 같은 목록)."""
    from md4paper import library

    targets = [wd.out_images]
    try:
        project = library.project_of(wd)
        for which in library.WHICH:
            d = library.dir_for(which, project)
            if d is not None:
                targets.append(d / "images" / wd.root.stem)
    except Exception:  # noqa: BLE001 — 저장 위치를 못 읽어도 본문 그림은 바꾼다
        pass
    return list(dict.fromkeys(targets))


def _swap(wd: WorkDir, name: str, new: bytes) -> int:
    """원천과 바이트가 같은 사본을 모두 `new`로, 마지막에 원천을. 바꾼 사본 수."""
    src = wd.extract_images / name
    old = _digest(src.read_bytes())
    n = 0
    for d in _copies(wd):
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() == src.suffix.lower() and _digest(p.read_bytes()) == old:
                p.write_bytes(new)
                n += 1
    src.write_bytes(new)
    return n


def render(wd: WorkDir, page: int, rect) -> bytes:  # noqa: ANN001
    """고른 영역(1-based 쪽, 좌상단 원점 pt [l, t, r, b])을 변환과 같은 배율로 렌더한다."""
    from md4paper import pdfio
    from md4paper.extract.docling_backend import FIG_ZOOM

    src = source_pdf(wd)
    if not src:
        raise ValueError("원본 PDF를 찾을 수 없습니다.")
    try:
        left, top, right, bottom = (float(v) for v in rect)
    except (TypeError, ValueError):
        raise ValueError("영역이 올바르지 않습니다.") from None
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    if right - left < _MIN_SIDE_PT or bottom - top < _MIN_SIDE_PT:
        raise ValueError("영역이 너무 작습니다.")
    png = pdfio.render_region_png(src, int(page) - 1, (left, top, right, bottom), zoom=FIG_ZOOM, mark_dpi=True)
    if png is None:
        raise ValueError("그 영역을 잘라 내지 못했습니다.")
    return png


def replace(wd: WorkDir, name: str, page: int, rect) -> dict:  # noqa: ANN001
    """그 그림을 고른 영역으로 바꾼다 — 원천·out·저장 위치 사본. 처음 바꿀 때 원래 그림을 백업한다."""
    path = wd.extract_images / name
    if not path.is_file() or name.startswith("formula-"):
        raise ValueError("그림 파일을 찾지 못했습니다.")
    if path.suffix.lower() != ".png":
        raise ValueError("PNG 그림만 다시 자를 수 있습니다.")
    png = render(wd, page, rect)
    backup = wd.figure_backup / name
    if not backup.exists():
        wd.figure_backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    copies = _swap(wd, name, png)
    left, top, right, bottom = (float(v) for v in rect)
    data = load(wd)
    prev = data.get(name) or {}
    # 변환 때 자리는 'auto'로 남긴다 — 되돌리면 그 값으로 돌아간다
    auto = prev.get("auto") or (
        {"page": prev["page"], "bbox": prev["bbox"]} if prev.get("bbox") and not prev.get("manual") else None)
    data[name] = {"page": int(page), "bbox": [round(min(left, right), 1), round(min(top, bottom), 1),
                                              round(max(left, right), 1), round(max(top, bottom), 1)],
                  "manual": True, **({"auto": auto} if auto else {})}
    _save(wd, data)
    return {"name": name, "copies": copies}


def has_backup(wd: WorkDir, name: str) -> bool:
    return (wd.figure_backup / name).is_file()


def restore(wd: WorkDir, name: str) -> dict:
    """'원래 그림으로' — 백업을 원천·사본에 되돌리고 기록도 변환 때 값으로."""
    backup = wd.figure_backup / name
    if not backup.is_file() or not (wd.extract_images / name).is_file():
        raise ValueError("되돌릴 원래 그림이 없습니다.")
    copies = _swap(wd, name, backup.read_bytes())
    backup.unlink()
    data = load(wd)
    rec = data.get(name) or {}
    if rec.get("auto"):
        data[name] = rec["auto"]
    else:
        data.pop(name, None)
    _save(wd, data)
    return {"name": name, "copies": copies}

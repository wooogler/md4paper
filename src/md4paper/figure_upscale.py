"""이미 변환한 논문의 그림을 고해상도로 — docling을 다시 돌리지 않고 그림 파일만 바꾼다.

새 변환은 그림 bbox를 원본 PDF에서 288dpi로 다시 그린다(docling_backend.FIG_ZOOM). 예전 변환엔
bbox가 남아 있지 않으니, 144dpi 옛 그림을 같은 배율로 렌더한 PDF 페이지에서 **템플릿 매칭으로
찾아** 자리를 되살린다. 재변환과 달리 마크다운·번역·메모는 한 글자도 건드리지 않는다.

docling 페이지 렌더러는 pdfium과 픽셀이 똑같진 않아 정규화 상관(TM_CCOEFF_NORMED)으로 찾는다.
실측(논문 4편, 그림 182개 — docling으로 다시 뽑은 bbox와 대조): 아이콘을 뺀 147개가 전부 일치.
처음엔 셋이 틀렸다 — 한 변 12pt 안팎의 아이콘(같은 모양이 되풀이돼 남의 자리에 붙음)과,
흑백으로 비교하던 때 같은 틀에 색 선만 다른 EEG 그림. 그래서 작은 그림은 건너뛰고(흐림이 눈에
띄지도 않는 크기다) 정밀 비교는 컬러로 하며, 다른 쪽 후보와 박빙이면 손대지 않는다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from md4paper.workdir import WorkDir

_BASE_SCALE = 2.0  # 옛 그림의 배율 (docling images_scale = 144dpi)
_COARSE = 4  # 1차 탐색은 1/4로 줄여 페이지 전체를 훑고, 찾은 자리 근처만 원래 크기로 맞춘다
_MIN_SCORE = 0.9  # 이보다 낮은 매칭은 버린다 (실측 최저 정답 0.90, 오답 아이콘 0.88)
# 다른 쪽 2등과 점수 차가 이 안이면 어느 쪽인지 확신할 수 없다 — 건너뛴다.
# 차이가 사실상 0인 건 예외: 같은 그림이 두 쪽에 그대로 실린 것이라 어느 쪽을 그려도 같다.
_MIN_MARGIN = 0.01
_SAME_EPS = 1e-4
# 점수가 _MIN_SCORE에 못 미쳐도 다른 후보가 멀찍이 뒤처지면 받는다 — docling 렌더러가 글꼴을
# 뭉개거나 테두리를 몇 pt 더 물고 잘라 점수만 낮은 경우다. 작업 폴더 179편 실측: 0.77~0.90 그림 46개가
# 모두 2등과 0.14 넘게 차이 났고, 가장 낮은 축부터 눈으로 본 표본 5개는 전부 제자리였다.
_LOW_SCORE = 0.75
_LOW_MARGIN = 0.1
_PEAKS = 3  # 쪽마다 정밀 맞춤할 1차 후보 수
_COARSE_GATE = 0.4  # 1차(흑백 1/4) 점수가 이보다 낮은 쪽은 정밀 맞춤을 안 한다 (0.7 미만인 정답도 있었다)
# 긴 변이 이보다 짧은 그림(48pt 미만) = 아이콘·배지 — 오매칭 구간이라 건너뛴다.
# 짧은 변으로 자르지 않는 이유: 가로로 긴 띠 그림(264×18pt 실측)은 모양이 독특해 잘 찾는다.
_MIN_LONG_PX = 96
_MIN_SHORT_PX = 16  # 1/4로 줄인 1차 탐색에서 4px은 남아야 한다


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _arr(pil, mode: str):  # noqa: ANN001, ANN202 — PIL.Image → uint8 ndarray
    import numpy as np

    return np.asarray(pil.convert(mode), dtype=np.uint8)


def locate(pdf: Path, images: list[Path]) -> dict[str, tuple[int, float, tuple, float]]:
    """옛 그림 → {파일명: (0-based 페이지, 점수, 좌상단 원점 rect pt, 2등과의 점수 차)}.

    1차는 흑백 1/4 크기로 페이지 전체를, 정밀 맞춤은 **컬러** 원래 크기로 그 근처만 본다 —
    같은 틀에 데이터(색 선)만 다른 그림이 여러 쪽에 되풀이되는 논문(EEG 연결도 실측)에서
    흑백은 남의 그림을 더 닮았다고 고른다. 쪽마다 1차 상위 몇 자리를 컬러로 맞춰 1·2등 차이도 돌려준다.
    페이지를 하나씩 렌더하므로 수백 쪽 논문도 메모리엔 한 쪽만 올라간다.
    """
    import cv2
    from PIL import Image

    from md4paper import pdfio

    temps = {}
    for f in images:
        with Image.open(f) as im:
            t = _arr(im, "RGB")
        h, w = t.shape[:2]
        if max(h, w) < _MIN_LONG_PX or min(h, w) < _MIN_SHORT_PX or t.std() < 1:
            continue  # 아이콘·빈 그림 — 매칭이 무의미하다
        g = cv2.cvtColor(t, cv2.COLOR_RGB2GRAY)
        temps[f.name] = (t, cv2.resize(g, (w // _COARSE, h // _COARSE), interpolation=cv2.INTER_AREA))
    cands: dict[str, list[tuple[float, int, tuple]]] = {k: [] for k in temps}
    if not temps:
        return {}
    with pdfio.open_document(pdf) as doc:
        for i in range(len(doc)):
            page = _arr(doc[i].render(scale=_BASE_SCALE).to_pil(), "RGB")
            ph, pw = page.shape[:2]
            ps = cv2.resize(cv2.cvtColor(page, cv2.COLOR_RGB2GRAY), (pw // _COARSE, ph // _COARSE),
                            interpolation=cv2.INTER_AREA)
            for name, (t, small) in temps.items():
                h, w = t.shape[:2]
                if small.shape[0] > ps.shape[0] or small.shape[1] > ps.shape[1]:
                    continue
                res = cv2.matchTemplate(ps, small, cv2.TM_CCOEFF_NORMED)
                sh, sw = small.shape
                for _ in range(_PEAKS):  # 한 쪽에 비슷한 그림이 여럿일 수 있다 — 상위 몇 자리를 다 본다
                    _, v, _, (x, y) = cv2.minMaxLoc(res)
                    if v < _COARSE_GATE:
                        break  # 1차에서 이미 딴 그림 — 정밀 맞춤을 아낀다
                    res[max(0, y - sh // 2):y + sh // 2 + 1, max(0, x - sw // 2):x + sw // 2 + 1] = -1
                    m = _COARSE * 2
                    x0, y0 = max(0, x * _COARSE - m), max(0, y * _COARSE - m)
                    win = page[y0:min(ph, y * _COARSE + h + m), x0:min(pw, x * _COARSE + w + m)]
                    if win.shape[0] < h or win.shape[1] < w:
                        continue
                    r2 = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
                    _, v2, _, (dx, dy) = cv2.minMaxLoc(r2)
                    X, Y, s = x0 + dx, y0 + dy, _BASE_SCALE
                    cands[name].append((float(v2), i, (X / s, Y / s, (X + w) / s, (Y + h) / s)))
    out = {}
    for name, cs in cands.items():
        cs.sort(reverse=True)
        # 같은 자리를 두 번 맞춘 후보(1차 봉우리가 한 그림에 둘)는 2등으로 치지 않는다
        cs = [c for k, c in enumerate(cs) if all(
            c[1] != d[1] or max(abs(a - b) for a, b in zip(c[2], d[2])) > 4 for d in cs[:k])]
        if cs:
            v, i, rect = cs[0]
            out[name] = (i, v, rect, v - cs[1][0] if len(cs) > 1 else 1.0)
    return out


def upscale(wd: WorkDir, pdf: Path) -> dict:
    """한 논문의 옛 그림을 고해상도로 바꾼다. 반환: {done, skipped, already}.

    extract/images를 바꾸고, out/images와 저장 위치 사본은 **옛 그림과 바이트가 같은 파일만**
    갈아치운다(조립 때 그대로 복사된 것들) — 그래서 파일 이름 대응표가 필요 없다.
    """
    from PIL import Image

    from md4paper import library, pdfio
    from md4paper.extract.docling_backend import FIG_ZOOM

    stats = {"done": 0, "skipped": 0, "already": 0}
    if not wd.extract_images.is_dir():
        return stats
    todo: list[Path] = []
    for f in sorted(wd.extract_images.glob("img-*.png")):
        with Image.open(f) as im:
            if im.info.get("dpi"):
                stats["already"] += 1
            else:
                todo.append(f)
    if not todo:
        return stats
    found = locate(pdf, todo)
    new_png: dict[Path, bytes] = {}
    for f in todo:
        hit = found.get(f.name)
        png = None
        if hit is not None:
            page, score, rect, margin = hit
            sure = score >= _MIN_SCORE and not (_SAME_EPS <= margin < _MIN_MARGIN)
            if sure or (score >= _LOW_SCORE and margin >= _LOW_MARGIN):
                png = pdfio.render_region_png(pdf, page, rect, zoom=FIG_ZOOM, mark_dpi=True)
        if png is None:
            stats["skipped"] += 1
        else:
            new_png[f] = png
    new_by_digest = {_digest(f): png for f, png in new_png.items()}

    targets = [wd.out_images]
    project = library.project_of(wd)
    for which in library.WHICH:
        d = library.dir_for(which, project)
        if d is not None:
            targets.append(d / "images" / wd.root.stem)
    for d in dict.fromkeys(targets):  # 영어·한국어가 같은 폴더면 한 번만
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() == ".png":
                png = new_by_digest.get(_digest(p))
                if png is not None:
                    p.write_bytes(png)
    # 사본을 먼저 바꾸고 원본(extract)을 마지막에 — 원본의 dpi가 '끝났다' 표시라서, 도중에 멈추면
    # 다음 실행이 옛 바이트로 사본을 다시 찾아 마저 바꾼다.
    for f, png in new_png.items():
        f.write_bytes(png)
    stats["done"] = len(new_png)
    return stats

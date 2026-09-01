"""앱 아이콘(PNG) 생성기 — 결과물은 src/md4paper/ui/assets/icon.png로 커밋해 둔다.

런처를 설치할 때(`md4paper app`) 이 PNG를 OS별 형식(.icns/.ico)으로 변환한다. 아이콘을 쓰는
시점에 그리지 않고 미리 만들어 두는 이유는, 그리기가 시스템 폰트(한글 글리프)에 의존하기
때문이다 — 사용자 컴퓨터마다 결과가 달라지면 안 된다.

    uv run python tools/make_icon.py

디자인: 파란 라운드 사각형 + 흰 종이 + 파란 '한'. 32px에서도 "흰 문서 위의 표식"으로 읽히도록
요소를 셋으로 제한했다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 1024
SS = 4  # 슈퍼샘플링 배율 — 크게 그린 뒤 줄여서 곡선 계단 현상을 없앤다
OUT = Path(__file__).resolve().parents[1] / "src" / "md4paper" / "ui" / "assets" / "icon.png"

TOP = (76, 154, 245)  # 배경 그라데이션 위 (#4C9AF5)
BOTTOM = (27, 95, 203)  # 배경 그라데이션 아래 (#1B5FCB)
INK = (25, 88, 190)  # 종이 위 글자
KO_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]


def _font(px: int) -> ImageFont.FreeTypeFont:
    """한글 '한'을 그릴 수 있는 굵은 폰트. (ttc는 굵은 자족을 index로 고른다)"""
    for path in KO_FONTS:
        if not Path(path).exists():
            continue
        for index in (6, 0):  # AppleSDGothicNeo.ttc의 6 = Bold, 없으면 첫 자족
            try:
                return ImageFont.truetype(path, px, index=index)
            except OSError:
                continue
    raise SystemExit("한글 폰트를 찾지 못했습니다 — KO_FONTS에 경로를 추가하세요.")


def _gradient(box: tuple[int, int, int, int], radius: int) -> Image.Image:
    """세로 그라데이션을 라운드 사각형으로 오려낸 레이어."""
    w, h = SIZE * SS, SIZE * SS
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))  # type: ignore[arg-type]
    grad = grad.resize((w, h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def build() -> Image.Image:
    s = SS
    img = _gradient((92 * s, 92 * s, 932 * s, 932 * s), radius=196 * s)
    draw = ImageDraw.Draw(img)

    page = (302 * s, 236 * s, 722 * s, 788 * s)  # 흰 종이 (세로형)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (page[0], page[1] + 14 * s, page[2], page[3] + 14 * s), radius=28 * s, fill=(10, 40, 90, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12 * s)))
    draw.rounded_rectangle(page, radius=28 * s, fill=(255, 255, 255, 255))

    # 종이 위쪽 본문 줄 3개 — '문서'라는 인상을 주되 작게 보면 사라지도록 옅게
    for i, width in enumerate((300, 300, 214)):
        y = (300 + i * 46) * s
        draw.rounded_rectangle((360 * s, y, (360 + width) * s, y + 20 * s),
                               radius=10 * s, fill=(188, 205, 228, 255))

    font = _font(230 * s)  # 종이 아래쪽 여백 안에 들어가는 크기 (넘치면 글자가 잘린다)
    box = draw.textbbox((0, 0), "한", font=font)
    draw.text(((page[0] + page[2] - box[0] - box[2]) // 2,
               (490 * s + 760 * s - box[1] - box[3]) // 2), "한", font=font, fill=INK)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon = build()
    icon.save(OUT)
    tmp = Path(tempfile.gettempdir())  # 작은 크기에서 뭉개지지 않는지 눈으로 확인하는 용도
    for px in (128, 64, 32):
        icon.resize((px, px), Image.LANCZOS).save(tmp / f"md4paper-icon-{px}.png")
    print(f"미리보기: {tmp}/md4paper-icon-{{128,64,32}}.png")
    print(f"{OUT} ({icon.size[0]}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

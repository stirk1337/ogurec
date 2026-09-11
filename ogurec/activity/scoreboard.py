from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ogurec.activity.loldle_store import format_until_next

MODES = (
    ("classic", "К"),
    ("quote", "Ц"),
    ("ability", "У"),
    ("emoji", "Э"),
    ("splash", "С"),
)
ASSETS = Path(__file__).with_name("assets")
BG = (10, 20, 26)
CARD = (13, 36, 42)
INK = (240, 230, 210)
MUTED = (160, 155, 140)
GOLD = (200, 170, 110)
GOLD_DEEP = (154, 126, 77)
LOGO_WIDTH = 220
BOARD_MIN_WIDTH = 900
BOARD_OUTER = 32
BOARD_GAP = 18
BOARD_STRIPE = 14
LINE = (58, 48, 32)
GOOD = (9, 192, 45)
PARTIAL = (219, 128, 11)
IDLE = (58, 66, 72)
EMPTY = (30, 40, 46)
KIND = {"g": GOOD, "p": PARTIAL, "i": IDLE}
FONTS = {
    "regular": (
        ASSETS / "SourceSans3-Regular.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ),
    "semibold": (
        ASSETS / "SourceSans3-Semibold.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONTS[kind]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _knockout(image: Image.Image) -> Image.Image:
    logo = image.convert("RGBA")
    pixels = logo.load()
    width, height = logo.size
    stack = []
    for x in range(width):
        stack.append((x, 0))
        stack.append((x, height - 1))
    for y in range(height):
        stack.append((0, y))
        stack.append((width - 1, y))
    seen = set()
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or x < 0 or y < 0 or x >= width or y >= height:
            continue
        seen.add((x, y))
        red, green, blue, alpha = pixels[x, y]
        if alpha > 20 and max(red, green, blue) >= 16:
            continue
        pixels[x, y] = (0, 0, 0, 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    box = logo.getbbox()
    return logo.crop(box) if box else logo


@lru_cache(maxsize=4)
def _logo(width: int) -> Image.Image | None:
    path = ASSETS / "logo.png"
    if not path.exists():
        return None
    logo = _knockout(Image.open(path))
    height = max(1, round(logo.height * width / logo.width))
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def _mode_cells(player: dict) -> list[tuple[str, str]]:
    progress = player.get("progress") or {}
    cells = []
    for mode, _glyph in MODES:
        result = progress.get(mode) or {}
        attempts = max(0, int(result.get("attempts") or 0))
        if result.get("done"):
            cells.append(("g", str(max(attempts, 1))))
        elif attempts:
            cells.append(("p", str(attempts)))
        else:
            cells.append(("i", ""))
    return cells


def _grid_size(cell: int, gap: int) -> tuple[int, int]:
    return 5 * cell + 4 * gap, cell


def _circle(image: Image.Image, size: int) -> Image.Image:
    face = image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(face, (0, 0), mask)
    return out


def _placeholder(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((1, 1, size - 2, size - 2), fill=EMPTY, outline=GOLD, width=2)
    return image


def _face(player: dict, avatars: dict[str, Image.Image], size: int) -> Image.Image:
    image = avatars.get(str(player.get("id") or ""))
    return _circle(image, size) if image is not None else _placeholder(size)


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    cells: list[tuple[str, str]],
    cell: int,
    gap: int,
    font: ImageFont.ImageFont,
) -> None:
    radius = max(4, cell // 6)
    for x, (kind, mark) in enumerate(cells):
        box = (
            left + x * (cell + gap),
            top,
            left + x * (cell + gap) + cell,
            top + cell,
        )
        draw.rounded_rectangle(box, radius=radius, fill=KIND.get(kind, IDLE), outline=(8, 16, 18), width=1)
        if mark:
            draw.text((box[0] + cell / 2, box[1] + cell / 2), mark, font=font, fill=INK, anchor="mm")


def _draw_labels(draw: ImageDraw.ImageDraw, left: int, top: int, cell: int, gap: int, font: ImageFont.ImageFont) -> None:
    for index, (_mode, glyph) in enumerate(MODES):
        cx = left + index * (cell + gap) + cell / 2
        draw.text((cx, top), glyph, font=font, fill=GOLD, anchor="mt")


def _card_metrics(*, stacked: bool) -> dict[str, int]:
    avatar = 88 if stacked else 132
    cell = 40 if stacked else 54
    gap = 8 if stacked else 12
    pad = 22 if stacked else 28
    label_h = 24 if stacked else 28
    grid_w, grid_h = _grid_size(cell, gap)
    inner_w = max(avatar, grid_w) if stacked else avatar + 24 + grid_w
    inner_h = avatar + 10 + label_h + grid_h if stacked else max(avatar, label_h + 6 + grid_h)
    return {
        "avatar": avatar,
        "cell": cell,
        "gap": gap,
        "pad": pad,
        "label_h": label_h,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "width": inner_w + pad * 2,
        "height": inner_h + pad * 2,
    }


def _columns_count(count: int) -> int:
    if count <= 1:
        return 1
    if count <= 5:
        return count
    if count <= 6:
        return 3
    return 4


def _draw_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    player: dict,
    avatars: dict[str, Image.Image],
    box: tuple[int, int, int, int],
    columns: list[tuple[str, str]],
    metrics: dict[str, int],
    stacked: bool,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=24, fill=CARD, outline=GOLD_DEEP, width=2)
    pad = metrics["pad"]
    avatar = metrics["avatar"]
    cell = metrics["cell"]
    gap = metrics["gap"]
    face = _face(player, avatars, avatar)
    label_font = _font("semibold", 16 if stacked else 18)
    mark_font = _font("semibold", 22 if stacked else 28)
    if stacked:
        ax = left + (right - left - avatar) // 2
        ay = top + pad
        image.paste(face, (ax, ay), face)
        gx = left + (right - left - metrics["grid_w"]) // 2
        gy = ay + avatar + 10
        _draw_labels(draw, gx, gy, cell, gap, label_font)
        _draw_grid(draw, gx, gy + metrics["label_h"], columns, cell, gap, mark_font)
        return
    ay = top + (bottom - top - avatar) // 2
    image.paste(face, (left + pad, ay), face)
    gx = left + pad + avatar + 24
    gy = top + (bottom - top - (metrics["label_h"] + 6 + metrics["grid_h"])) // 2
    _draw_labels(draw, gx, gy, cell, gap, label_font)
    _draw_grid(draw, gx, gy + metrics["label_h"] + 6, columns, cell, gap, mark_font)


async def fetch_avatars(session, players: list[dict]) -> dict[str, Image.Image]:
    faces: dict[str, Image.Image] = {}
    if session is None:
        return faces
    for player in players:
        user_id = str(player.get("id") or "")
        if not user_id:
            continue
        avatar = player.get("avatar")
        url = (
            f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=256"
            if avatar
            else f"https://cdn.discordapp.com/embed/avatars/{int(user_id) % 6}.png"
        )
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    faces[user_id] = Image.open(BytesIO(await response.read()))
        except Exception:
            continue
    return faces


def _header_space(*, remaining: bool) -> int:
    logo = _logo(LOGO_WIDTH)
    return (logo.height + 8 if logo is not None else 40) + 18 + 28 + (28 if remaining else 0)


def _header(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    width: int,
    outer: int,
    title: str,
    *,
    remaining: bool,
) -> int:
    logo = _logo(LOGO_WIDTH)
    y = outer
    if logo is not None:
        image.paste(logo, ((width - logo.width) // 2, y), logo)
        y += logo.height + 8
    else:
        word = _font("semibold", 36)
        draw.text((width / 2, y), "LoLdle", font=word, fill=GOLD, anchor="mt")
        y += 40
    inset = max(BOARD_OUTER + 16, width // 8)
    draw.line((inset, y + 2, width - inset, y + 2), fill=GOLD_DEEP, width=2)
    y += 18
    subtitle = _font("regular", 22)
    draw.text((width / 2, y), title or "LoLdle", font=subtitle, fill=GOLD, anchor="mt")
    y += 28
    if remaining:
        draw.text((width / 2, y), f"осталось {format_until_next()}", font=subtitle, fill=GOLD, anchor="mt")
        y += 28
    return y


def _png(image: Image.Image) -> BytesIO:
    ImageDraw.Draw(image).rectangle((0, 0, BOARD_STRIPE - 1, image.height - 1), fill=GOLD)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def render_scoreboard(
    players: list[dict],
    avatars: dict[str, Image.Image] | None = None,
    title: str = "LoLdle",
    streak: int = 0,
    remaining: bool = True,
) -> BytesIO:
    del streak
    avatars = avatars or {}
    people = list(players)
    stacked = len(people) != 1
    logo = _logo(LOGO_WIDTH)
    title_h = _header_space(remaining=remaining)
    gap = BOARD_GAP
    outer = BOARD_OUTER
    if not people:
        width = max(BOARD_MIN_WIDTH, (logo.width if logo is not None else 220) + outer * 2)
        height = outer + title_h + outer
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        _header(image, draw, width, outer, title, remaining=remaining)
        return _png(image)

    boards = []
    metrics = _card_metrics(stacked=stacked)
    for player in people:
        boards.append(_mode_cells(player))

    cols = _columns_count(len(people))
    rows_n = (len(people) + cols - 1) // cols
    col_w = metrics["width"]
    row_h = metrics["height"]
    width = max(
        BOARD_MIN_WIDTH,
        outer * 2 + cols * col_w + (cols - 1) * gap,
        (logo.width if logo is not None else 220) + outer * 2,
    )
    height = outer + title_h + rows_n * row_h + (rows_n - 1) * gap + outer
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    y = _header(image, draw, width, outer, title, remaining=remaining)

    extra = (width - (outer * 2 + cols * col_w + (cols - 1) * gap)) // 2
    for index, (player, cells) in enumerate(zip(people, boards)):
        col = index % cols
        band = index // cols
        left = outer + extra + col * (col_w + gap)
        top = y + band * (row_h + gap)
        _draw_card(
            image,
            draw,
            player,
            avatars,
            (left, top, left + col_w, top + row_h),
            cells,
            metrics,
            stacked,
        )

    return _png(image)

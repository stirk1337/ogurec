from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ogurec.activity.loldle_store import ru_days

MODES = (
    ("classic", "Классика", "К"),
    ("quote", "Цитата", "Ц"),
    ("ability", "Умение", "У"),
    ("emoji", "Эмодзи", "Э"),
    ("splash", "Сплеш", "С"),
)
ASSETS = Path(__file__).with_name("assets")
BG = (10, 20, 26)
INK = (240, 230, 210)
MUTED = (160, 155, 140)
GOLD = (200, 170, 110)
GOLD_DEEP = (154, 126, 77)
TEAL = (13, 36, 42)
LINE = (58, 48, 32)
GOOD = (9, 193, 46)
PARTIAL = (219, 128, 11)
BAD = (218, 21, 15)
EMPTY = (30, 40, 46)
KIND = {"g": GOOD, "p": PARTIAL, "b": BAD, "u": BAD, "d": BAD, "i": EMPTY}
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


def _kind(cell) -> str:
    if isinstance(cell, dict):
        return str(cell.get("k") or "")
    return str(cell or "")


def _done(player: dict) -> int:
    progress = player.get("progress") or {}
    return sum(1 for mode, *_ in MODES if progress.get(mode, {}).get("done"))


def _mode_color(result: dict) -> tuple[int, int, int]:
    if result.get("done"):
        return GOOD
    if result.get("attempts"):
        return PARTIAL
    return EMPTY


def _mode_mark(result: dict) -> str:
    attempts = int(result.get("attempts") or 0)
    if result.get("done"):
        return str(attempts or "✓")
    if attempts:
        return str(attempts)
    return ""


def _mode_caption(result: dict) -> str:
    attempts = int(result.get("attempts") or 0)
    if result.get("done"):
        if attempts == 1:
            return "с 1-й"
        if attempts in {2, 3, 4}:
            return f"с {attempts}-й"
        return f"{attempts} поп."
    if attempts:
        return f"{attempts} поп."
    return "ещё нет"


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
    draw.ellipse((1, 1, size - 2, size - 2), fill=TEAL, outline=GOLD, width=2)
    return image


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    clipped = text
    while clipped and draw.textlength(clipped + "…", font=font) > width:
        clipped = clipped[:-1]
    return clipped + "…" if clipped else "…"


def _logo(width: int) -> Image.Image | None:
    path = ASSETS / "logo.png"
    if not path.exists():
        return None
    logo = Image.open(path).convert("RGBA")
    height = max(1, round(logo.height * width / logo.width))
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def _panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=18, fill=TEAL, outline=GOLD_DEEP, width=2)
    draw.rounded_rectangle((box[0] + 6, box[1] + 6, box[2] - 6, box[3] - 6), radius=14, outline=LINE, width=1)


def _grid(draw: ImageDraw.ImageDraw, left: int, top: int, rows: list, cell: int, gap: int) -> int:
    if not rows:
        return 0
    width = max(len(row) for row in rows)
    for y, row in enumerate(rows[:8]):
        for x in range(width):
            kind = _kind(row[x]) if x < len(row) else ""
            box = (
                left + x * (cell + gap),
                top + y * (cell + gap),
                left + x * (cell + gap) + cell,
                top + y * (cell + gap) + cell,
            )
            draw.rounded_rectangle(box, radius=4, fill=KIND.get(kind, EMPTY), outline=(8, 16, 18), width=1)
            if kind in {"u", "d"}:
                cx = (box[0] + box[2]) / 2
                if kind == "u":
                    draw.polygon([(cx, box[1] + 4), (box[2] - 4, box[3] - 5), (box[0] + 4, box[3] - 5)], fill=(8, 16, 12))
                else:
                    draw.polygon([(cx, box[3] - 4), (box[2] - 4, box[1] + 5), (box[0] + 4, box[1] + 5)], fill=(8, 16, 12))
    return len(rows[:8]) * (cell + gap) - gap


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
            f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=128"
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


def _header(image: Image.Image, draw: ImageDraw.ImageDraw, title: str, width: int, streak: int = 0) -> int:
    logo = _logo(248)
    date_font = _font("regular", 16)
    streak_font = _font("semibold", 18)
    y = 28
    if logo is not None:
        image.paste(logo, ((width - logo.width) // 2, y), logo)
        y += logo.height + 6
    else:
        word = _font("semibold", 36)
        draw.text((width / 2, y), "LoLdle", font=word, fill=GOLD, anchor="mt")
        y += 46
    date = title.split("·", 1)[-1].strip() if "·" in title else title
    draw.text((width / 2, y), date, font=date_font, fill=GOLD, anchor="mt")
    y += 26
    if streak > 0:
        draw.text((width / 2, y), f"🔥 {ru_days(streak)} подряд", font=streak_font, fill=GOLD, anchor="mt")
    else:
        draw.text((width / 2, y), "Стрик сервера: 0", font=date_font, fill=MUTED, anchor="mt")
    y += 28
    draw.line((48, y, width - 48, y), fill=GOLD_DEEP, width=1)
    return y + 18


def _modes(draw: ImageDraw.ImageDraw, left: int, top: int, width: int, progress: dict) -> int:
    label_font = _font("semibold", 13)
    mark_font = _font("semibold", 22)
    caption_font = _font("regular", 12)
    col_w = width / 5
    cell = 46
    for index, (mode, label, glyph) in enumerate(MODES):
        result = progress.get(mode) or {}
        cx = left + col_w * index + col_w / 2
        x0 = int(cx - cell / 2)
        draw.text((cx, top), label.upper(), font=label_font, fill=GOLD, anchor="mt")
        box = (x0, top + 20, x0 + cell, top + 20 + cell)
        draw.rounded_rectangle(box, radius=8, fill=_mode_color(result), outline=(8, 16, 18), width=1)
        mark = _mode_mark(result)
        draw.text(
            (cx, top + 20 + cell / 2),
            mark or glyph,
            font=mark_font,
            fill=INK if mark else GOLD_DEEP,
            anchor="mm",
        )
        draw.text((cx, top + 74), _mode_caption(result), font=caption_font, fill=MUTED, anchor="mt")
    return 96


def _mode_pips(draw: ImageDraw.ImageDraw, left: int, top: int, progress: dict, cell: int = 26, gap: int = 5) -> int:
    mark_font = _font("semibold", 14)
    for index, (mode, _label, glyph) in enumerate(MODES):
        result = progress.get(mode) or {}
        x = left + index * (cell + gap)
        box = (x, top, x + cell, top + cell)
        draw.rounded_rectangle(box, radius=5, fill=_mode_color(result), outline=(8, 16, 18), width=1)
        mark = _mode_mark(result)
        draw.text((x + cell / 2, top + cell / 2), mark or glyph, font=mark_font, fill=INK if mark else GOLD_DEEP, anchor="mm")
    return 5 * cell + 4 * gap


def _legend(draw: ImageDraw.ImageDraw, width: int, y: int) -> None:
    legend = ((GOOD, "угадал"), (PARTIAL, "в процессе"), (EMPTY, "ещё не играл"))
    legend_font = _font("regular", 13)
    parts = []
    total = 0
    for color, text in legend:
        part_w = 18 + 8 + int(draw.textlength(text, font=legend_font)) + 22
        parts.append((color, text, part_w))
        total += part_w
    x = (width - total) // 2
    draw.line((48, y - 12, width - 48, y - 12), fill=LINE, width=1)
    for color, text, part_w in parts:
        draw.rounded_rectangle((x, y, x + 16, y + 16), radius=4, fill=color)
        draw.text((x + 22, y + 7), text, font=legend_font, fill=MUTED, anchor="lm")
        x += part_w


def _detailed_player(image: Image.Image, draw: ImageDraw.ImageDraw, player: dict, avatars: dict, left: int, top: int, width: int, pad: int) -> int:
    name_font = _font("semibold", 20)
    score_font = _font("semibold", 18)
    label_font = _font("regular", 13)
    avatar = 52
    progress = player.get("progress") or {}
    user_id = str(player.get("id") or "")
    face = avatars.get(user_id)
    portrait = _circle(face, avatar) if face is not None else _placeholder(avatar)
    image.paste(portrait, (left, top), portrait)
    name = _fit(draw, player.get("name") or "Игрок", name_font, width - avatar - 80)
    draw.text((left + avatar + 14, top + 8), name, font=name_font, fill=INK)
    done = _done(player)
    draw.text((left + width, top + 10), f"{done} / 5", font=score_font, fill=GOLD, anchor="rt")
    draw.text((left + avatar + 14, top + 34), "режимов угадано", font=label_font, fill=MUTED)
    y = top + 70
    y += _modes(draw, left, y, width, progress)
    classic = ((progress.get("classic") or {}).get("cells") or [])[:8]
    if classic:
        draw.text((left, y), "Сетка классики", font=label_font, fill=GOLD)
        y += 22
        widest = max(len(row) for row in classic)
        cell = min(24, max(14, (width - (widest - 1) * 4) // widest))
        grid_w = widest * (cell + 4) - 4
        y += _grid(draw, left + (width - grid_w) // 2, y, classic, cell, 4) + 16
    return y + 10 - top


def _compact_player(image: Image.Image, draw: ImageDraw.ImageDraw, player: dict, avatars: dict, left: int, top: int, width: int) -> None:
    name_font = _font("semibold", 16)
    score_font = _font("semibold", 15)
    avatar = 36
    progress = player.get("progress") or {}
    user_id = str(player.get("id") or "")
    face = avatars.get(user_id)
    portrait = _circle(face, avatar) if face is not None else _placeholder(avatar)
    image.paste(portrait, (left, top + 8), portrait)
    name = _fit(draw, player.get("name") or "Игрок", name_font, width - avatar - 70)
    draw.text((left + avatar + 10, top + 6), name, font=name_font, fill=INK)
    draw.text((left + width, top + 8), f"{_done(player)}/5", font=score_font, fill=GOLD, anchor="rt")
    _mode_pips(draw, left + avatar + 10, top + 32, progress)


def render_scoreboard(
    players: list[dict],
    avatars: dict[str, Image.Image] | None = None,
    title: str = "LoLdle",
    streak: int = 0,
) -> BytesIO:
    avatars = avatars or {}
    label_font = _font("regular", 13)
    people = list(players)
    width = 760 if len(people) <= 4 else 900
    pad = 36
    compact = len(people) >= 3
    columns = 2 if len(people) >= 5 else 1
    row_h = 72
    if not people:
        height = 400
    elif compact:
        rows = (len(people) + columns - 1) // columns
        height = 176 + 28 + rows * row_h + 64
    else:
        classic_rows = max((len(((player.get("progress") or {}).get("classic") or {}).get("cells") or []) for player in people), default=0)
        person_h = 86 + 96 + (28 + classic_rows * 26 if classic_rows else 0) + 18
        height = 176 + person_h * len(people) + 54
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    _panel(draw, (12, 12, width - 13, height - 13))
    y = _header(image, draw, title, width, streak)

    if not people:
        y += _modes(draw, pad, y, width - pad * 2, {})
        draw.text((width / 2, y + 18), "Нажми Играть — пять режимов на сегодня", font=label_font, fill=MUTED, anchor="mt")
    elif compact:
        header_font = _font("semibold", 11)
        col_w = (width - pad * 2 - 20 * (columns - 1)) // columns
        for col in range(columns):
            left = pad + col * (col_w + 20) + 46
            for index, (_mode, label, _glyph) in enumerate(MODES):
                draw.text((left + index * 31 + 13, y), label[:3].upper(), font=header_font, fill=GOLD, anchor="mt")
        y += 22
        for index, player in enumerate(people):
            col = index % columns
            row = index // columns
            left = pad + col * (col_w + 20)
            top = y + row * row_h
            _compact_player(image, draw, player, avatars, left, top, col_w)
    else:
        for player in people:
            y += _detailed_player(image, draw, player, avatars, pad, y, width - pad * 2, pad)

    _legend(draw, width, height - 42)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

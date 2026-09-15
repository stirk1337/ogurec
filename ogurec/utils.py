import random
import re
from datetime import timedelta, timezone

from discord import Guild, GuildSticker, Member, Role

TIME_ZONE = timezone(timedelta(hours=5))  # Russia, Ekaterinburg


def get_random_formatted_emoji(server: Guild) -> str:
    emoji = random.choice(server.emojis)
    if emoji.name.split("_")[0] == "a":
        return f"<a:{emoji.name}:{emoji.id}>"
    else:
        return f"<:{emoji.name}:{emoji.id}>"


def get_random_sticker(server: Guild) -> GuildSticker:
    stickers = [sticker for sticker in server.stickers if sticker.available]
    return random.choice(stickers)


def get_all_users_with_role(server: Guild, role_name: str) -> list[Member]:
    role_id = server.roles[0]
    for role in server.roles:
        if role_name == role.name:
            role_id = role
            break
    users = []
    for member in server.members:
        if role_id in member.roles:
            users.append(member)
    return users


def get_role_by_name(server: Guild, role_name: str) -> Role | None:
    for role in server.roles:
        if role_name == role.name:
            return role


# LLM часто ломает разметку: @123 / <@ник> / <name:id> / :name: / <:name:> вместо <@id> и <:name:id>
_EMOJI_RE = re.compile(
    r"<a?:?([A-Za-z][\w]{1,31})(?::(\d{15,25}))?:?>"
    r"|(?<![\w:]):([A-Za-z][\w]{1,31}):(?![\w:])"
)
_MENTION_RE = re.compile(r"<?@!?(\d{15,25}|[\w.\-]{2,32})>?")


def fix_discord_format(text: str, server: Guild | None) -> str:
    """Чинит пинги и кастомные эмодзи в тексте от LLM."""
    if not text:
        return text

    emojis = server.emojis if server else ()
    emoji_by_name = {e.name.lower(): e for e in emojis}
    emoji_by_id = {str(e.id): e for e in emojis}

    members = server.members if server else ()
    member_by_name = {}
    for m in members:
        member_by_name.setdefault(m.name.lower(), m)
        member_by_name.setdefault(m.display_name.lower(), m)

    def repl_emoji(match: re.Match) -> str:
        name, emoji_id, plain = match.group(1), match.group(2), match.group(3)
        emoji = emoji_by_name.get((name or plain).lower()) or emoji_by_id.get(emoji_id or "")
        if not emoji:
            return ""  # выдуманный эмодзи — выкидываем, чтобы не светить сырым текстом
        return f"<a:{emoji.name}:{emoji.id}>" if emoji.animated else f"<:{emoji.name}:{emoji.id}>"

    def repl_mention(match: re.Match) -> str:
        who = match.group(1)
        if who.isdigit():
            return f"<@{who}>"
        member = member_by_name.get(who.lower())
        # ник не с сервера (@everyone, @here, обычный текст) — оставляем как есть
        return f"<@{member.id}>" if member else f"@{who}"

    text = _EMOJI_RE.sub(repl_emoji, text)
    text = _MENTION_RE.sub(repl_mention, text)
    return re.sub(r"[ \t]{2,}", " ", text)

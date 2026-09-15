"""Идентификаторы Discord: кнопка Играть, id сообщений и поиск канала по инстансу активити."""

import re

PLAY_ID = "loldle:play"


def play_custom_id(day: str) -> str:
    return f"{PLAY_ID}:{day}"


def play_id_day(custom_id: str | None) -> str | None:
    if not custom_id:
        return None
    prefix = f"{PLAY_ID}:"
    if custom_id.startswith(prefix):
        day = custom_id[len(prefix) :]
        return day or None
    return None


def iter_custom_ids(components) -> list[str]:
    found: list[str] = []
    for item in components or []:
        custom_id = getattr(item, "custom_id", None)
        if custom_id:
            found.append(str(custom_id))
        found.extend(iter_custom_ids(getattr(item, "children", None)))
    return found


def message_id(raw) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def coerce_channel_id(raw) -> int | None:
    if raw in (None, "", 0, "0", "null", "undefined"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def parse_instance_channel(instance_id: str | None) -> int | None:
    if not instance_id:
        return None
    text = str(instance_id)
    match = re.search(r"(?:^|-)gc-\d+-(\d+)$", text) or re.search(r"(?:^|-)pc-(\d+)$", text)
    return int(match.group(1)) if match else None


def resolve_channel_id(
    player: dict,
    instance_id: str,
    instances: dict[str, int],
    user_channels: dict[str, int],
    stored_user_channel: int | None = None,
) -> int | None:
    user_id = str(player.get("id") or "")
    for candidate in (
        coerce_channel_id(player.get("channelId")),
        parse_instance_channel(player.get("locationId")),
        instances.get(instance_id),
        parse_instance_channel(instance_id),
        parse_instance_channel(player.get("instanceId")),
        user_channels.get(user_id),
        stored_user_channel,
    ):
        if candidate:
            return int(candidate)
    return None

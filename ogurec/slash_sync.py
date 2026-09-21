from discord.app_commands.errors import MissingApplicationID
from discord.app_commands.models import AppCommand

ACTIVITY_ENTRY_POINT = 4


def with_activity_entry_point(payload: list[dict], remote: list[dict]) -> list[dict]:
    """Discord 50240: bulk upsert cannot drop the Activity Entry Point command."""
    if any(command.get("type") == ACTIVITY_ENTRY_POINT for command in payload):
        return payload
    for command in remote:
        if command.get("type") != ACTIVITY_ENTRY_POINT:
            continue
        kept = {
            "id": command["id"],
            "name": command["name"],
            "type": ACTIVITY_ENTRY_POINT,
            "description": command.get("description") or "",
        }
        if "handler" in command:
            kept["handler"] = command["handler"]
        return [*payload, kept]
    return payload


async def sync_slash_commands(bot) -> list:
    tree = bot.tree
    if bot.application_id is None:
        raise MissingApplicationID
    payload = [command.to_dict(tree) for command in tree._get_all_commands(guild=None)]
    remote = await bot.http.get_global_commands(bot.application_id)
    payload = with_activity_entry_point(payload, remote)
    data = await bot.http.bulk_upsert_global_commands(bot.application_id, payload=payload)
    return [AppCommand(data=d, state=tree._state) for d in data]

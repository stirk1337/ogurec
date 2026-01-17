import random
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

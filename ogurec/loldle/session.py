"""Живые сессии активити в памяти: кто прямо сейчас играет в канале.

Пережить перезапуск должно хранилище, а не это: сокеты всё равно обрываются.
"""

import time

SESSION_GRACE = 120


def session_is_live(*, has_sockets: bool, opened: bool, seen_at: float, now: float, grace: float) -> bool:
    if has_sockets:
        return True
    if not opened:
        return False
    return now - seen_at < grace


class Sessions:
    def __init__(self, rooms: dict, grace: float = SESSION_GRACE):
        self.rooms = rooms
        self.grace = grace
        self.instances: dict[str, int] = {}
        self.user_channels: dict[str, int] = {}
        self._channels_day = ""
        self._players: dict[int, dict[str, dict]] = {}
        self._starters: dict[int, set[int]] = {}
        self._open: dict[int, bool] = {}
        self._seen: dict[int, float] = {}

    def remember_instance(self, instance_id: str, channel_id: int) -> None:
        self.instances[instance_id] = channel_id

    def forget_instance(self, instance_id: str) -> None:
        self.instances.pop(instance_id, None)

    def remember_user(self, user_id: str, channel_id: int, day: str = "") -> None:
        """День сменился — вчерашние привязки к каналам больше не подсказка, а ловушка."""
        if day and day != self._channels_day:
            self.user_channels.clear()
            self._channels_day = day
        self.user_channels[str(user_id)] = channel_id

    def has_sockets(self, channel_id: int) -> bool:
        return any(
            self.rooms.get(instance_id)
            for instance_id, mapped in self.instances.items()
            if mapped == channel_id
        )

    def live(self, channel_id: int) -> bool:
        return session_is_live(
            has_sockets=self.has_sockets(channel_id),
            opened=self._open.get(channel_id, False),
            seen_at=self._seen.get(channel_id, 0),
            now=time.monotonic(),
            grace=self.grace,
        )

    def touch(self, channel_id: int) -> None:
        self._open[channel_id] = True
        self._seen[channel_id] = time.monotonic()

    def begin(self, channel_id: int) -> None:
        self._players[channel_id] = {}
        self._starters[channel_id] = set()
        self.touch(channel_id)

    def open_unless_live(self, channel_id: int) -> None:
        if not self.live(channel_id):
            self.begin(channel_id)
        self.touch(channel_id)

    def close(self, channel_id: int) -> None:
        self._players[channel_id] = {}
        self._starters[channel_id] = set()
        self._open[channel_id] = False

    def add_starter(self, user_id: int | str, channel_id: int) -> None:
        try:
            self._starters.setdefault(channel_id, set()).add(int(user_id))
        except (TypeError, ValueError):
            pass

    def drop_starter(self, user_id: int | str, channel_id: int) -> None:
        try:
            self._starters.get(channel_id, set()).discard(int(user_id))
        except (TypeError, ValueError):
            pass

    def set_player(self, channel_id: int, user_id: str, player: dict) -> None:
        self._players.setdefault(channel_id, {})[str(user_id)] = player

    def drop_player(self, channel_id: int, user_id: str) -> None:
        self._players.get(channel_id, {}).pop(str(user_id), None)

    def starters(self, channel_id: int) -> set[int]:
        return set(self._starters.get(channel_id, set()))

    def player_ids(self, channel_id: int) -> list[int]:
        ids = []
        for user_id in self._players.get(channel_id, {}):
            try:
                ids.append(int(user_id))
            except (TypeError, ValueError):
                continue
        return ids

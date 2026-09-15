"""Контекст для логов: без него падения кнопки Играть не отладить."""


def http_detail(exc: BaseException) -> str:
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    text = getattr(exc, "text", None)
    if status is not None or code is not None or text is not None:
        return f"status={status} code={code} text={text!r}"
    return f"{type(exc).__name__}: {exc}"


def play_custom_id_of(interaction) -> str | None:
    data = interaction.data
    if isinstance(data, dict):
        value = data.get("custom_id")
    else:
        value = getattr(data, "custom_id", None)
    return str(value) if value else None


def play_ctx(interaction) -> str:
    message = interaction.message
    flags = getattr(message, "flags", None)
    return (
        f"user={getattr(interaction.user, 'id', None)} "
        f"channel={interaction.channel_id} guild={interaction.guild_id} "
        f"message={getattr(message, 'id', None)} custom_id={play_custom_id_of(interaction)} "
        f"type={getattr(interaction.type, 'name', interaction.type)} "
        f"responded={interaction.response.is_done()} "
        f"v2={bool(getattr(flags, 'components_v2', False))}"
    )

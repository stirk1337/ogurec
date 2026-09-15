from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    discord_bot_token: str
    klipy_api_key: str = '1'
    gpt_api_key: str = '1'
    steam_api_key: str = '1'
    prefix: str = "!"
    bot_chat_id: int = 749662464538443948
    main_chat_id: int = 670981415306788870
    api_base_url: str = "https://freellmapi.stirk1337.ru/v1/chat/completions"
    llm_model: str = "auto" # auto, auto:smart, auto:fast
    search_query_model: str = "auto:fast"
    fast_model: str = "auto:fast"  # решения, память, комментарии к отчету

    # живое поведение
    reply_debounce_seconds: float = 5.0  # копим сообщения пачкой, отвечаем один раз
    reply_delay_seconds: float = 2.5  # пауза "он читает", перед началом ответа
    replies_per_minute: int = 3  # потолок на канал, пинги не режутся
    mood_hours: int = 3  # настроение держится часами, а не меняется каждый ответ

    memory_enabled: bool = True
    memory_update_every: int = 15  # сообщений от юзера между пересборками досье

    proactive_enabled: bool = True
    proactive_silence_minutes: int = 40  # тишина, после которой бот может написать сам
    proactive_chance: int = 20  # % на каждой проверке (раз в 5 минут)
    search_enabled: bool = True
    search_max_results: int = 5
    search_context_chars: int = 5000
    discord_client_id: str
    discord_client_secret: str
    activity_host: str = "127.0.0.1"
    activity_port: int = 18089

    users_discord_id: list[int] = [
        279945550432829441,  # artem
        310451376612179968,  # roma
        387114624409010176,  # slava
        372629156283940865,  # egor
        279676792409948160,  # stirk
        871973760729747457,  # semen
    ]

    users_steam_id: dict[int, int] = {
        279945550432829441: 76561198215619408,
        310451376612179968: 76561198180111306,
        387114624409010176: 76561198333627960,
        372629156283940865: 76561198132944338,
        279676792409948160: 76561198146633945,
        871973760729747457: 76561198841926720,
    }

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
    )

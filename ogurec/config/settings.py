from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    discord_bot_token: str
    klipy_api_key: str
    gpt_api_key: str
    steam_api_key: str
    prefix: str = "!"
    bot_chat_id: int = 749662464538443948
    main_chat_id: int = 670981415306788870
    api_base_url: str = "https://freellmapi.stirk1337.ru/v1/chat/completions"
    llm_model: str = "auto:smart" # auto, auto:smart, auto:fast
    search_enabled: bool = True
    search_max_results: int = 5
    search_context_chars: int = 5000

    users_discord_id: list[int] = [
        279945550432829441,  # artem
        310451376612179968,  # roma
        372629156283940865,  # egor
        279676792409948160,  # stirk
        871973760729747457,  # semen
        387114624409010176,  # slava
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


from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_bot_token: str
    klipy_api_key: str
    gpt_api_key: str
    steam_api_key: str
    prefix: str = "!"
    bot_chat_id: int = 749662464538443948
    main_chat_id: int = 670981415306788870

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
    )

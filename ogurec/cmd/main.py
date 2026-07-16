import asyncio

from ogurec.bot import OgurecBot
from ogurec.chatgpt import GPTClient
from ogurec.cogs.conversation_cog import ConversationCog
from ogurec.cogs.gif_storage_cog import GifStorage
from ogurec.cogs.help_cog import Help
from ogurec.cogs.presence_game_cog import PresenceGameCog
from ogurec.cogs.rebrand.rebrand_cog import Rebrand
from ogurec.cogs.utils_cog import Utils
from ogurec.config.settings import Settings
from ogurec.steam import SteamClient
from ogurec.klipy import KlipyClient



async def amain():
    settings = Settings()
    bot = OgurecBot(settings)

    klipy_client = KlipyClient(settings.klipy_api_key, '1')
    gpt_client = GPTClient(settings.gpt_api_key)
    steam_client = SteamClient(settings.steam_api_key)
    gif_storage = GifStorage()
    await gif_storage.init()

    await bot.add_cog(Utils(bot))
    await bot.add_cog(Help(bot))
    await bot.add_cog(Rebrand(bot))
    conversation_cog = ConversationCog(bot, gpt_client, gif_storage)
    await bot.add_cog(conversation_cog)
    await bot.add_cog(PresenceGameCog(bot, klipy_client, steam_client, conversation_cog))
    
    await bot.start(token=settings.discord_bot_token)


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()

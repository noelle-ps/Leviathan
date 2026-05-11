import discord
from discord.ext import commands
import asyncio
import os
import logging
import datetime
from utils.keep_alive import keep_alive

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

keep_alive()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class Leviathan(commands.Bot):

    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.start_time = datetime.datetime.now(datetime.timezone.utc)

    async def on_ready(self):
        try:
            await self.tree.sync()
            logger.info(f"✅ Logged in as {self.user} (slash commands synced!)")
        except Exception as e:
            logger.error(f"Error syncing commands: {e}")


bot = Leviathan()


async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py') and not filename.startswith('__'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                logger.info(f"Loaded cog: {filename}")
            except Exception as e:
                logger.error(f"Failed to load cog {filename}: {e}")


async def main():
    token = os.environ.get("TOKEN")
    if not token:
        logger.error("Bot token not found in Replit Secrets. Please set the 'TOKEN' environment variable.")
        raise Exception("Bot token not found in Replit Secrets.")

    try:
        await load_cogs()
        await bot.start(token)
    except discord.errors.HTTPException as e:
        logger.error(f"HTTPException during bot startup, possible rate limit: {e}")
        os.system("kill 1")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise


if __name__ == '__main__':
    logger.info("🚀 Starting bot...")
    asyncio.run(main())

import time
import os
import asyncio
import logging
import discord
from discord.ext import commands
from contextlib import suppress
import traceback

import django

global_logger = None
global_discord_client = None


try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    import warnings
    warnings.warn("Couldn't set uvloop as event_loop_policy, using slower default.")
else:
    print("Using uvloop")


class GenAIClientMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._genai_client = None  # Lazy initialization
    
    @property
    def genai_client(self):
        if self._genai_client is None:
            from google import genai
            from django.conf import settings
            self._genai_client = genai.Client(api_key=settings.GOOGLE_AI_API_KEY)
        return self._genai_client


class AppCommandBotMixin:
    async def setup_hook(self):
        raw_guild_id = await self.aget_main_guild_id()

        if not raw_guild_id:
            global_logger.warning("⚠️ No main Guild ID found, skipping command sync.")
            return

        try:
            guild_id = discord.Object(id=raw_guild_id)
            self.tree.copy_global_to(guild=guild_id)
            await self.tree.sync(guild=guild_id)
        except discord.HTTPException as e:
            global_logger.error(f'❌ Failed to sync commands: {e}')
        else:
            global_logger.info(f'✅ Synced commands to guild: {raw_guild_id}')

        self._register_tree_error_handler()
        await super().setup_hook()

    def _register_tree_error_handler(self):
        from app.main.util.discord_command_checks import (
            MissingDjangoPermission,
            MissingDjangoObjectPermission,
            AccountNotLinked,
        )

        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: Exception):
            if isinstance(error, AccountNotLinked):
                msg = (
                    "**Your Discord isn't linked to a website account yet.**\n"
                    "Head over to the website and link your account first, then try again. "
                    "You can also use `/link` with the code from your profile page."
                )
            elif isinstance(error, MissingDjangoPermission):
                msg = (
                    "**You don't have permission to use this command.**\n"
                    "It looks like your account is missing a required website role or permission. "
                    "If you think this is a mistake, reach out to a staff member."
                )
            elif isinstance(error, MissingDjangoObjectPermission):
                msg = (
                    "**You don't have permission to do that.**\n"
                    "Your account doesn't have the required access for this specific item. "
                    "Contact a staff member if you believe this is incorrect."
                )
            else:
                global_logger.error(
                    "Unhandled app command error in /%s: %s",
                    interaction.command.name if interaction.command else "unknown",
                    error,
                    exc_info=error,
                )
                msg = "Something went wrong while running that command. Please try again later."

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass


class PrivNotificationMixin:
    async def send_private_notification(self, user: discord.User, content: str = None, **kwargs) -> bool:
        ...

    @property
    def send_private_notification(self):
        return self.cogs.get("PrivNotificationCog").send_private_notification


class InterruptableDelayMixin:
    def __init__(self, *args, **kwargs):
        self.delayed_tasks = dict()
        super().__init__(*args, **kwargs)

    def interruptable_delay(self, delay, awaitable, name):
        async def _delay_f(_delay, _awaitable, _name):
            try:
                await asyncio.sleep(_delay)
            except asyncio.CancelledError as exc:
                with suppress(RuntimeWarning):
                    del _awaitable
                raise exc
            finally:
                del self.delayed_tasks[_name]

            with suppress(asyncio.CancelledError):
                await _awaitable

        if name in self.delayed_tasks.keys():
            raise KeyError("name '{}' already exists for interruptable_delay '{}'".format(name, delay))

        global_logger.debug("Delaying '{}' '{}'".format(name, delay))
        task = asyncio.create_task(_delay_f(_delay=delay, _awaitable=awaitable, _name=name))
        self.delayed_tasks[name] = task

        return task

    def cancel_interruptable_delay(self, name):
        """
        Cancels a scheduled interruptable_delay by name. 
        Returns True if the task was found and cancelled, False otherwise.
        """
        if task := self.delayed_tasks.get(name):
            task.cancel()
            global_logger.debug(f"Cancelled delayed task '{name}'")
            return True
        else:
            global_logger.info(f"Attempted to cancel '{name}', but it was not found.")
            return False


class CogLoaderMixin:
    def __init__(self, *args, **kwargs):
        self.extension_info = {"loaded": [], "errored": []}
        super().__init__(*args, **kwargs)

    async def install_extension(self, path):
        try:
            global_logger.info(f"  ➡️ Loading Cog {path}")
            await self.load_extension(path)
        except Exception as exc:
            self.extension_info["errored"].append([path, exc])
            global_logger.exception(f"  ❌ Failed to load extension {path}:\n")
            raise
        else:
            self.extension_info["loaded"].append(path)
            global_logger.info(f"  ✅ Successfully loaded extension {path}")

    async def setup_hook(self):
        extension_list = self.extension_list
        cogs_tasks = {}

        async def safe_install(extension_path):
            try:
                await self.install_extension(extension_path)
            except Exception as exception:
                global_logger.error("-- + SETUP EXCEPTION ---------------")
                global_logger.error(f"Exception while loading extension at path '{extension_path}'")
                global_logger.error("-- ------------------- -------------")
                tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
                for line in tb_lines:
                    global_logger.error(line.rstrip())
                global_logger.error("-- - SETUP EXCEPTION ---------------")

        try:
            async with asyncio.timeout(60.0):
                async with asyncio.TaskGroup() as tg:
                    for extension_path in extension_list:
                        task = tg.create_task(safe_install(extension_path))
                        cogs_tasks[task] = extension_path

        except TimeoutError:
            global_logger.error("❌⚠️ Cog loading timed out after 1 minute!")

            for task, extension_path in cogs_tasks.items():
                if task.cancelled():
                    global_logger.error(f"⏱️ Timed out while loading: {extension_path}")

        await super().setup_hook()


class DjangoCogLoaderMixin:
    def __init__(self, *args, **kwargs):
        self.extension_info = {"loaded": [], "errored": []}
        super().__init__(*args, **kwargs)

    async def install_extension(self, path, app_config):
        try:
            global_logger.info(f"  ➡️  {path} [{app_config.label}] Loading Cog")
            await self.load_extension(path)
        except Exception as exc:
            self.extension_info["errored"].append([path, exc])
            global_logger.exception(f"  ❌  {path}: [{app_config.label}] Failed to load extension: \n")
            raise
        else:
            self.extension_info["loaded"].append(path)
            global_logger.info(f"  ✅  {path} [{app_config.label}] Successfully loaded")

    async def setup_hook(self):
        from django.apps import apps

        cogs_tasks = {}
        global_logger.info("-- + Scanning Django App Registry for Cogs ---")

        async def safe_install(extension_path, app_config):
            try:
                await self.install_extension(extension_path, app_config)
            except Exception as exception:
                global_logger.error("-- + SETUP EXCEPTION ---------------")
                global_logger.error(f"Exception while loading extension at path '{extension_path}'")
                global_logger.error("-- ------------------- -------------")
                tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
                for line in tb_lines:
                    global_logger.error(line.rstrip())
                global_logger.error("-- - SETUP EXCEPTION ---------------")

        try:
            async with asyncio.timeout(60.0):
                async with asyncio.TaskGroup() as tg:
                    for app_config in apps.get_app_configs():
                        app_cogs = app_config.get_cogs() if hasattr(app_config, 'get_cogs') else None

                        if app_cogs:
                            for extension_path in app_cogs:
                                task = tg.create_task(safe_install(extension_path, app_config))
                                cogs_tasks[task] = [extension_path, app_config]

        except TimeoutError:
            global_logger.error("❌⚠️  Cog loading timed out after 1 minute!")

            for task, data in cogs_tasks.items():
                if task.cancelled():
                    extension_path, app_config = data
                    global_logger.error(f"  ⏱️  Timed out while loading: {extension_path} [{app_config.label}]")

        else:
            global_logger.info("  ✅  Cog loading FINALIZED")

        await super().setup_hook()


class LoginTokenFromSettingsMixin:
    def run(self, *args, **kwargs):
        super().run("unknown", *args, **kwargs)

    def get_bot_token(self):
        from app.discordauth.preferences import BotToken
        from app.preferences.utils import get_global_preference
        return get_global_preference(BotToken.import_path)

    async def aget_bot_token(self):
        from app.discordauth.preferences import BotToken
        from app.preferences.utils import aget_global_preference
        return await aget_global_preference(BotToken.import_path)

    async def start(self, token=None, reconnect=True):
        bot_token = await self.aget_bot_token()
        await super().start(bot_token, reconnect=reconnect)


class OrgMainGuildIDMixin:
    def get_main_guild_id(self):
        from app.discordauth.preferences import BotGuildID
        from app.preferences.utils import get_global_preference
        return get_global_preference(BotGuildID.import_path)

    async def aget_main_guild_id(self):
        from app.discordauth.preferences import BotGuildID
        from app.preferences.utils import aget_global_preference
        return await aget_global_preference(BotGuildID.import_path)


class BotClient(OrgMainGuildIDMixin, LoginTokenFromSettingsMixin, PrivNotificationMixin, GenAIClientMixin, DjangoCogLoaderMixin, AppCommandBotMixin, InterruptableDelayMixin, commands.Bot):
    def __init__(self, *args, **kwargs):
        from django.conf import settings

        intents = settings.DISCORD_INTENTS
        self.start_time = None
        self.uptime = None
        self._start_timestamp = None

        super().__init__(
            command_prefix="!bv ",
            description=settings.DISCORD_BOT_DESCRIPTION,
            case_insensitive=True,
            intents=intents,
            *args, **kwargs
        )


def main():
    global global_discord_client, global_logger  # TODO: global_discord_client can be removed if we update the signal that uses it to just use redit

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
    django.setup()

    from django.conf import settings

    if settings.DEBUG:
        logging.basicConfig(level=logging.DEBUG)
    global_logger = logging.getLogger(__name__)

    global_logger.info("Starting Discord.py")

    # Do not run migrations on the discord bot, restart web instead (which should happen regardless)

    client = BotClient()
    global_discord_client = client
    global_logger.info("Setting up Discord Bot Globally")

    try:
        client.run(
            root_logger=True,
            log_handler=logging.StreamHandler(),
            log_level=logging.DEBUG if settings.DEBUG else logging.INFO,
            reconnect=True
        )
    except discord.LoginFailure as e:
        global_logger.error(f"❌ Login failed: {e}")
        global_logger.error("Please check your bot token!")
    except KeyboardInterrupt:
        global_logger.info("Received interrupt, shutting down...")
    except Exception as e:
        global_logger.error(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

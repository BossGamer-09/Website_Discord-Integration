import logging


logger = logging.getLogger(__name__)


def main():
    from app.main import discord_bot
    discord_bot.main()


if __name__ == '__main__':
    main()

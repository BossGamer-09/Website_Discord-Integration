# BlightVeil Website & Discord Integration

Django web app + Discord bot for the BlightVeil org, sharing one ORM. Django handles HTTP views/admin, the bot handles async Discord events, and Celery + Redis handle background tasks and IPC between the two.

## Architecture

| Layer | Role |
|---|---|
| Django App | HTTP views, Admin, ORM |
| Discord Bot | Async event handling, async ORM |
| Celery + Beat | Background/periodic tasks |
| Redis | Broker, cache, Pub/Sub IPC |

IPC flows one way: Django/Celery publish to Redis Pub/Sub, bot cogs subscribe and react. The bot never polls the DB directly.

## Project layout

```
app/orm.py          Django ORM setup
main.py              Loader: wires up Django management commands + the Discord bot
app/<appname>/cogs/  Discord cogs (like d.py "apps")
app/orm_models/      Legacy shared models app (deprecated, see app/<appname>/models.py instead)
app/main/util/       Shared helper functions
```

See `AGENTS.md` / `CLAUDE.md` / `AI_md_files/` for the fuller contributor and architecture guidelines this repo follows.

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Copy your local env config (see `app/settings.py` for the env vars read via `django-environ`: `SECRET_KEY`, `DATABASE_URI`, `REDIS_PASSWORD`, Discord tokens, etc.) into a `.env` file at the repo root — never commit this file.
3. Run migrations:
   ```
   python main.py makemigrations
   python main.py migrate
   ```
   Commit the resulting migration files. Do not hand-edit existing (especially old) migrations.
4. Run the bot / web / worker processes via the relevant entrypoint script (`main_bot.py`, `main_web.py`, `main_celery_worker.py`, `main_celery_beat.py`, or `main_web_and_maintenance.py`).

## Working with the ORM

Always use the async ORM from bot/cog code — discord.py is async, so sync DB calls will freeze the bot.

```python
from django.apps import apps
UserProfile = apps.get_model('orm_models', 'UserProfile')  # dynamic load avoids circular imports

user = await UserProfile.objects.filter(username_istartswith="a").afirst()
users = [u async for u in UserProfile.objects.filter(username__istartswith="a").all()]
```

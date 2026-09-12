---
name: project-guidelines
description: >
  Enforces development standards for the Blightveil project — a Django 6 + Discord.py + Celery + MySQL + Redis
  hybrid monolith. Use this skill whenever the user asks to write new code, review existing code, add features,
  create models, write tasks, handle signals, manage users/permissions, or work with any part of this codebase.
  Trigger even for simple requests like "add a field", "create a view", "write a cog", or "check permissions" —
  the guidelines contain critical rules (async ORM, no hardcoded IDs, deprecated patterns, user model hierarchy)
  that must be applied at every step. If the user pastes code or describes a feature for this project, always
  apply this skill without exception.
---

# Blightveil Project Guidelines

## Response Rules — Token Budget

Maximize code output per response. The user's message limits are finite — every wasted token is stolen coding time.

- **Code first, talk later.** Lead with the implementation. Explanations go after, and only if non-obvious.
- **No narration of what you're about to do.** Don't say "I'll create a model that..." — just write the model.
- **No restating the request.** The user knows what they asked.
- **No repeating guidelines back.** Apply them silently. Only mention a guideline if the user's request conflicts with one.
- **Minimal inline comments.** One-liner max, only where the *why* isn't obvious from the code.
- **Skip boilerplate preambles** like "Sure!", "Great question!", "Here's what I came up with:".
- **Batch related files.** If a feature needs a model + signal + cog, output all of them in one response.
- **Diffs over full files** when editing. Only show the changed section + enough context to locate it.
- **Flag issues inline** with a short `# ⚠️ reason` comment rather than a paragraph below the code.
- When reviewing code, list issues as terse bullet points — no essays.

---

## 1. Architecture

| Layer | Role |
|---|---|
| Django App | HTTP views, Admin, ORM |
| Discord Bot | Async event handling, async ORM |
| Celery + Beat | Background/periodic tasks |
| Redis | Broker, cache, Pub/Sub IPC |

IPC: Django → Redis Pub/Sub → Bot Cogs. Never poll DB from bot.

---

## 2. Structure

```
app/<appname>/
    cogs/               # Discord Cogs
    tasks.py            # Celery tasks
    signals.py          # Signals + Redis publish
    models.py           # Models HERE
    preferences.py      # Runtime config
app/main/util/          # Shared helpers
app/celerytools/        # QueueOnce, TaskLock
app/orm_models/         # ⛔ LEGACY
```

Commands: `python main.py makemigrations|migrate|run_discord_bot`

**Deprecated** → replacement:
- `app/orm_models/` (new models) → `app/<appname>/models.py`
- `generic_constants.py` → `preferences.py` or `settings.py`
- `asyncio.run_coroutine_threadsafe(...)` → Redis Pub/Sub

---

## 3. Models

**Fields**: Snowflake IDs → `BigIntegerField`. Timestamps → `django.utils.timezone`. Audit → `HistoricalRecords`. Bulk signal ops → `SignalEmittingManager`.

**User model**: Always FK to `OrgPlayer` (`app.unifieduser.OrgPlayer`). `DiscordUser` needing perms → OneToOne/FK → `OrgPlayer`. Display names → `OrgPlayer.display_name`. User name changes → `CustomDisplayName` (auto rank prefix). Ranks → `OrgRank`. Perms → Django Groups + `guardian`. No raw role IDs.

**New model checklist**: In `app/<appname>/models.py` · Snowflakes as `BigIntegerField` · `HistoricalRecords` if audited · User FK → `OrgPlayer` · Perms → Django Groups

---

## 4. Async ORM (Bot Code)

Sync DB calls freeze the bot. Always async:

```python
obj = await Model.objects.filter(...).afirst()
obj = await Model.objects.aget(pk=...)
async for obj in Model.objects.filter(...): ...
await obj.asave()
await Model.objects.acreate(...)
# Sync fallback
from asgiref.sync import sync_to_async
result = await sync_to_async(fn)()
```

Flag `.first()`, `.save()`, `.create()` without `await` in any cog/bot code.

---

## 5. Config

No hardcoded Channel/Role/Guild IDs. Permission IDs → Django Groups → `org.models.DiscordRole`. Runtime values → preferences:

```python
from app.preferences.utils import aget_global_preference
chan_id = await aget_global_preference('myapp__ChannelID')
```

---

## 6. Celery

```python
from celery import shared_task
from app.celerytools.utils import QueueOnce
from celery.utils.log import get_task_logger
logger = get_task_logger(__name__)

@shared_task
def my_task(): ...

@shared_task(base=QueueOnce, once={'graceful': True})
def singleton_task(): ...
```

`@shared_task` only. `QueueOnce` for singletons. `register_periodic_task` for schedules. `get_task_logger` for logging.

---

## 7. IPC (Redis Pub/Sub)

```python
# Publish (Django/Celery)
from app.unifieduser.signals import publish
publish({"type": "MyEvent", "data": 123})

# Subscribe (Bot/Cog)
msg = await self.pubsub.get_message(ignore_subscribe_messages=True)
if msg:
    data = json.loads(msg['data'])
```

**Perm sync**: `app.unifieduser.signals` → `post_save`/`m2m_changed` → publishes to `permissions.unifieduser.sync` → Cogs update local cache. Payload: `{"user_id", "permission", "action": "added"|"removed", "obj_id"}`. No DB polling.

**Deprecated**: `asyncio.run_coroutine_threadsafe(...)` → use Redis Pub/Sub.

---

## 8. Cogs

Location: `app/<appname>/cogs/<cog_name>.py`. Registered in `main.py`. `__init__` starts tasks. `cog_unload` cancels tasks + closes Redis.

```python
from app.main.util.discord_command_checks import requires_django_perm, requires_django_object_perm_by_arg

@requires_django_perm("myapp.my_perm")
async def cmd(self, interaction): ...

# Manual
if not await requires_django_perm("myapp.my_perm")(interaction): return
```

Codenames always fully qualified: `"app_label.codename"`.

Helpers: `get_users_with_permission_on(codename, obj)`, `get_groups_with_permission_on(codename, obj)` from `app.main.util.db`.

---

## 9. Feature Notes

- **Voice** (`discordlogger`): `LoggedVoiceState` → `CompiledLoggedVoiceState` via `compile_voice_states_task`. Respect `expected_resolution`.
- **Scorecards**: Dual-write Django + Servitor (Servitor deprecated, fire-and-forget, never block).
- **Admin**: Use project mixins + `simple_history`/`ordered_model`. No bare `ModelAdmin`.
- **Views**: `guardian` for object perms. Bootstrap SCSS at `app/org/static/scss`.

---

## 10. Review Checklist

**Models**: Not in `orm_models/` · `BigIntegerField` for snowflakes · `timezone` for timestamps · FK → `OrgPlayer` · `display_name` for names · Groups + `guardian` for perms · `SignalEmittingManager` for bulk signals

**Bot**: No sync ORM · No `run_coroutine_threadsafe` · Redis Pub/Sub for IPC · `cog_unload` cleans up

**Config**: No hardcoded IDs · Preferences for runtime config · No `generic_constants.py`

**Celery**: `@shared_task` · `QueueOnce` for singletons · `register_periodic_task` · `get_task_logger`
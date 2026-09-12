# Project Guidelines & Architecture

## 1. Architecture Stack
This is a hybrid monolith combining a web backend and a Discord bot, sharing a database and messaging bus.

*   **Backend**: Django 6 (Synchronous Web/Admin) + Django Channels/Redis (Async IPC).
*   **Bot**: Discord.py (Asyncio) running as a management command or separate process.
*   **Database**: Django 6 ORM with MySQL (Shared source of truth).
*   **Queue**: Celery + Redis Broker (Background tasks).
*   **IPC**: Redis Pub/Sub for real-time signaling (Web ↔ Bot).

## 2. Directory Structure & domains
*   `app/<appname>/`: Django apps separated by feature domain.
    *   `cogs/`: Discord bot logic (Cogs) specific to this app.
    *   `tasks.py`: Celery tasks.
    *   `signals.py`: Django signals (syncing, side-effects).
*   `app/main/util/`: Shared utilities (DB helpers, Discord checks).
*   `app/celerytools/`: Custom Celery classes (`QueueOnce`, `TaskLock`).
*   `app/orm_models/`: **[LEGACY]** Shared models container. *New models should go into specific feature apps.*

## 3. Database & Models (Django ORM)
### Async Compatibility (Critical)
The Discord Bot runs on an `asyncio` loop. Blocking calls freeze the bot.
*   **Queries**: Use `await Model.objects.filter(...).afirst()` or `aget()`.
*   **Iteration**: Use `async for obj in Model.objects.filter(...):`.
*   **Writes**: Use `await obj.asave()` or `await Model.objects.acreate()`.
*   **Sync Wrappers**: If an async method doesn't exist, use `asgiref.sync.sync_to_async`.

### Model Best Practices
*   **Snowflakes**: Use `models.BigIntegerField` for Discord IDs.
*   **User Linking**: Link `DiscordUser` to `app.unifieduser.OrgPlayer` (OneToOne/FK) for permission context.
*   **Signal Emission**: For bulk operations (`update`, `bulk_create`) that need to trigger signals (e.g., for sync), use `app.main.util.db.SignalEmittingManager`.

## 4. Permission System
Permissions are managed in Django and synced to Discord/Redis.
*   **Access Control**: Use `guardian` for object-level permissions.

### Sources of Truth
1.  **Django Global Perms**: `user.has_perm('app.codename')`
2.  **Object Permissions**: via `django-guardian` (`UserObjectPermission`, `GroupObjectPermission`).
3.  **Group Inheritance**: Users inherit perms from Django Groups.
4.  **User**: 
    *   **Name** always use `OrgPlayer.display_name` defined at `app.unifieduser.OrgPlayer` and for regular users username updates should go to `CustomDisplayName`, so they can automatically get rank prefixes.
    *   **Model**: Always refer/FK to `OrgPlayer` for user data/atomicity, for example a `DiscordUser` if they needs permissions or further tracking should refer to `OrgPlayer` with a **OneToOneField/ForeignKey**, repeat this pattern where applicable.

### Real-time Sync (Web -> Bot)
The bot does not poll the DB for permissions on every event. Instead, it listens to Redis.
*   **Publisher**: `app.unifieduser.signals` listens for `post_save`, `m2m_changed` (Users, Groups, ObjectPerms) and publishes diffs to Redis.
*   **Subscriber**: Cogs (e.g., `SquirifyCog`) listen to `permissions.unifieduser.sync`.
*   **Payload**: Contains `user_id`, `permission` (app.codename), `action` (added/removed), and optional `obj_id`.

### Utilities
*   `app.main.util.db.get_users_with_permission_on(codename, obj)`: Returns a QuerySet of users who have the permission (checking Direct, Group, and Guardian paths).
*   `app.main.util.db.get_groups_with_permission_on(codename, obj)`: Returns QuerySet of Groups.
*   **Format**: Always use fully qualified codenames: `"app_label.permission_codename"`.

## 5. Bot Development (Cogs)
*   **Location**: `app/<app_name>/cogs/<cog_name>.py`.
*   **Loading**: Cogs are loaded via `main.py` entrypoint.
*   **Check Decorators**: Use `app.main.util.discord_command_checks` for access control.
    *   `@requires_django_perm("app.perm")`
    *   `@requires_django_object_perm_by_arg(...)`
*   **Lifecycle**:
    *   `__init__`: Setup tasks (`self.task.start()`).
    *   `cog_unload`: Cancel tasks and close Redis connections.

## 6. Celery & Background Tasks
*   **Base Class**: Use `app.celerytools.utils.QueueOnce` for tasks that must be singletons (locking).
*   **Definition**:
    ```python
    from celery import shared_task
    from app.celerytools.utils import QueueOnce

    @shared_task(base=QueueOnce, once={'graceful': True})
    def my_task():
        ...
    ```
*   **Scheduling**:
    *   Use `register_periodic_task` decorator for code-based registration.

## 7. Configuration
*   **Preferences**: Use `app.preferences` (dynamic DB-backed settings) over `settings.py` for runtime values (Channel IDs, Feature Flags).
    ```python
    # Usage in code
    from app.preferences.utils import aget_global_preference
    chan_id = await aget_global_preference('MyApp__ChannelID')
    ```
*   **Environment**: Use `django-environ` for infrastructure secrets (DB credentials, Tokens).

## 8. Workflow & Commands
*   **Migrations**:
    *   `python main.py makemigrations`
    *   `python main.py migrate`
*   **Run Bot**: `python main.py run_discord_bot` (or similar command implementation).
*   **Formatting**: Use 4 spaces for indentation.

## 9. Common Patterns (Cheatsheet)

**Publishing a Signal (Sync -> Redis)**
```python
from app.unifieduser.signals import publish
publish({"type": "MyEvent", "data": 123})
```

**Listening to Redis (Bot)**
```python
# inside a task loop
message = await self.pubsub.get_message(ignore_subscribe_messages=True)
if message:
    data = json.loads(message['data'])
```

**Checking Permissions (Bot)**
```python
if not await requires_django_perm("candidacy.can_mod_threads")(interaction):
    return
```

# IMPLEMENTATION.md | BLIGHTVEIL REFERENCE

## 1. CLI WORKFLOW
- Migrations: `python main.py makemigrations` | `python main.py migrate`
- Run Bot: `python main.py run_discord_bot`

## 2. HELPER FUNCTIONS (`app.main.util.db`)
- `get_users_with_permission_on(codename, obj)`: QuerySet of users (Direct/Group/Guardian).
- `get_groups_with_permission_on(codename, obj)`: QuerySet of groups.
- Codename format: `"app_label.permission_codename"`.

## 3. FEATURE SPECIFIC LOGIC
- Voice (`discordlogger`):
    - `LoggedVoiceState` → `CompiledLoggedVoiceState` via `compile_voice_states_task`.
    - Always respect `expected_resolution`.
- Scorecards:
    - Dual-write pattern: Django ORM + Servitor.
    - Servitor calls: Fire-and-forget, never block the main loop.

## 4. ADMIN & VIEWS
- Object Permissions: Use `django-guardian` for views.
- Admin: 
    - Use `simple_history` for audit logs.
    - Use `ordered_model` for sortable lists.
    - Use project-specific Mixins (no bare `ModelAdmin`).
- Static Assets: Bootstrap SCSS located at `app/org/static/scss`.

## 5. CONFIGURATION
- Environment: Use `django-environ` for secrets (DB, Tokens).
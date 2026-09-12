# Development Sprints

## Sprint 1: Foundational Setup
- **Objective**: Create `app/unifieduser` and `app/org` domains.
- **Tasks**: 
  - Define `OrgPlayer` (User model).
  - Setup basic Cog loader in `main.py`.
  - Implement Redis Pub/Sub IPC connection for cogs.

## Sprint 2: V1 - User Lifecycle (Items 1, 6)
- **Objective**: Onboarding & Roles.
- **Tasks**: 
  - `app/onboarding/cogs/onboard.py`: Logic for Join → Role Assignment.
  - `app/onboarding/models.py`: Onboarding progress tracking.

## Sprint 3: V1 - Management & Command Center (Items 2, 7)
- **Objective**: Leadership tools.
- **Tasks**:
  - `app/leadership/cogs/ranks.py`: CRUD logic for ranks/notes.
  - `app/leadership/models.py`: Audit logs, disciplinary data.

## Sprint 4: V1 - Utility (Items 3, 4, 5)
- **Objective**: Channels, Knowledge, Logs.
- **Tasks**:
  - `app/infrastructure/cogs/channels.py`: Template channel generator.
  - `app/knowledge/cogs/knowledge.py`: Searchable Discord message repository.
  - `app/logs/cogs/logger.py`: Event listening for actions.

## Sprint 5: V2 - Ops (Items 8, 9)
- **Objective**: Rosters & Events.
- **Tasks**:
  - `app/ops/tasks.py`: Celery tasks for event pings.
  - `app/ops/cogs/roster.py`: Roster sync logic.
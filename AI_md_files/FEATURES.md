# BlightVeil Discord Bot — Feature Documentation

> Complete reference of every implemented feature, slash command, event listener, and background task.

---

## Table of Contents

1. [Member Onboarding & Applications](#1-member-onboarding--applications)
2. [Rank, Leadership & Discipline](#2-rank-leadership--discipline)
3. [Voice Channels & Attendance](#3-voice-channels--attendance)
4. [Scheduled Events (Sesh-style)](#4-scheduled-events-sesh-style)
5. [Roster & User Management](#5-roster--user-management)
6. [Activity Ping Roles (Self-Assign)](#6-activity-ping-roles-self-assign)
7. [Loot Tracker & Quartermaster](#7-loot-tracker--quartermaster)
    - [7b. Merits & Money Requests](#7b-merits--money-requests)
8. [Distinctions & Nominations](#8-distinctions--nominations)
9. [Goals System](#9-goals-system)
10. [Pilot Board](#10-pilot-board)
11. [FPS Scorecard](#11-fps-scorecard)
12. [Kill Tracker](#12-kill-tracker)
13. [Organic Events (AI-Assisted)](#13-organic-events-ai-assisted)
14. [Knowledge Base / CMS](#14-knowledge-base--cms)
15. [Discord Account Linking](#15-discord-account-linking)
16. [Message & Voice Logging](#16-message--voice-logging)
17. [Announcements](#17-announcements)
18. [RSI / Star Citizen Status Tracking](#18-rsi--star-citizen-status-tracking)
19. [Org Announcements](#19-org-announcements)
20. [Background Infrastructure](#20-background-infrastructure)

---

## 1. Member Onboarding & Applications

### How It Works

When a new user joins the Discord server the bot automatically runs a suspicious account check (account age below threshold, no avatar set) and posts an alert to the suspicious-user staff channel if flagged. It then sends the user a "What brings you to BlightVeil?" dropdown in the configured welcome lobby channel.

**Three paths:**

| Selection | Result |
|---|---|
| I want to join BlightVeil | Routed to RSI verification → membership application |
| I'm part of another organization | ExternalCitizenModal → External Citizen rank + groups |
| Just checking things out | Visitor rank + groups auto-assigned |

### RSI Verification

The applicant clicks "Start Verification" → enters their RSI profile URL → the bot generates a unique verification code they must paste into their RSI bio. Once they confirm, the bot checks the bio via API and marks `RSIVerification.status = VERIFIED`. The verification record stores their RSI handle, profile URL, enlisted date, and org membership data.

We have this option we hav Turned off the code they put in the Their RSI handele and click a button to say is this you yes or no no allows them to Reput a Differnt Name in Yes Changes their Nickname to that continues the flow as above 

### Membership Application

After RSI verification the applicant submits a `MembershipApplicationRecord` through a Discord modal (up to 5 configurable questions per `MembershipApplicationType`). A review thread is created in the staff review channel.

Staff see a review embed with buttons:
- **Approve** — grants permission groups, assigns trial rank, creates thread transcript, notifies applicant
- **Deny** — archives thread, enforces cooldown
- **Reset** — clears status back to pending
- **Evaluate** — opens internal evaluation modal
- **Promote** — promote from trial rank to full rank

On approval the applicant is prompted to select their primary discipline (`/discipline_select`).

### Squire Trials

For members progressing to a knight-tier rank, staff run a structured trial:

- `/adm_squire_trial_start <member>` — creates a private feedback thread, sets `SquireTrialThread.status = ACTIVE`
- `/adm_squire_trial_finalize <Accept|Deny>` — promotes to `SquirifyKnightRankPK` on accept, writes `DecisionLog`, auto-archives thread

### Onboarding Leader Rotation

A Celery task rotates the duty `OnboardingLeaderAssignment` weekly, posting the new assignment to the onboarding leader channel. Applicants who join a voice channel during an active event are flagged to the Chamberlain notify channel. Applicants can use `/request_help <question>` to DM the current duty leaders directly.

### Automated Follow-Ups

| Task | Interval | Action |
|---|---|---|
| Follow-up DM | Configurable days | DM applicant if `follow_up_sent = False` |
| Discipline reminder | Configurable | DM if discipline not selected post-approval |
| Auto-archive | Configurable hours | Lock/archive inactive application threads |
| Cleanup stale | Daily | Close applications inactive beyond threshold |

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/apply_membership` | Public | Open membership application modal |
| `/verify_rsi` | Public | Start RSI bio verification workflow |
| `/discipline_select` | Public | Choose primary discipline post-approval |
| `/request_help <question>` | Public | DM current duty onboarding leaders |
| `/adm_send_join_dropdown <member>` | `org.can_manage_discord_members` | Re-send join routing dropdown to a member |
| `/adm_squire_trial_start <member>` | `candidacy.can_mod_squiretrialthread` | Start squire trial |
| `/adm_squire_trial_finalize <action>` | `candidacy.can_mod_squiretrialthread` | Accept or deny squire trial |
| `/adm_onboarding_assign` | Staff | Manually trigger onboarding leader assignment |

---

## 2. Rank, Leadership & Discipline

### How It Works

All rank changes and disciplinary actions are recorded in `DisciplinaryRecord` and `DecisionLog` with full `HistoricalRecords` audit trail. Every action opens a `ReasonModal` for the staff member to enter a public reason and optional internal notes.

### Disciplinary Actions

Actions that affect the member's Discord state (mute, kick, ban, blacklist) execute the corresponding Discord API call immediately after writing the DB record. Bans and blacklists also set a `is_blacklisted` flag on the member's profile. Mutes accept an optional `duration_hours` parameter (default 24h) and write an `expires_at` timestamp.

Pardons are applied via `/leadership pardon <record_id>` and reverse the Discord action where applicable. High-rank targets may require approval before execution — the approval workflow blocks the action and DMs designated approvers.

### Rank Management

Promote and demote write a `DecisionLog` entry with `rank_before` and `rank_after`. The rank change triggers a Redis Pub/Sub event that syncs Django Group membership and Discord role assignment across the bot's permission cache in real time (no DB polling).

### Command Reference

| Command | Permission | Description |
|---|---|---|
| `/leadership warn <member>` | `leadership.can_issue_warn` | Issue formal warning, creates DisciplinaryRecord |
| `/leadership mute <member> [duration_hours]` | `leadership.can_issue_mute` | Mute with optional duration (default 24h) |
| `/leadership kick <member>` | `leadership.can_issue_kick` | Kick from server |
| `/leadership ban <member>` | `leadership.can_issue_ban` | Ban from server |
| `/leadership blacklist <member>` | `leadership.can_issue_blacklist` | Ban + blacklist flag |
| `/leadership reprimand <member>` | `leadership.can_issue_reprimand` | Formal reprimand (no Discord action) |
| `/leadership inactive <member>` | `leadership.can_mark_inactive` | Mark inactive in backend |
| `/leadership mark_inactive <member> [reason]` | `leadership.can_mark_inactive` | Same with explicit reason |
| `/leadership note <member>` | `leadership.can_add_staff_note` | Add internal staff note |
| `/leadership add_note <member> <note>` | `leadership.can_add_staff_note` | Inline note (no modal) |
| `/leadership force_role <member> <rank>` | `leadership.can_force_role_change` | Override member's rank/role |
| `/leadership promote <member> <rank> <reason>` | `leadership.can_promote` | Promote with DecisionLog entry |
| `/leadership demote <member> <rank> <reason>` | `leadership.can_demote` | Demote with DecisionLog entry |
| `/leadership pardon <record_id> [reason]` | `leadership.can_approve_discipline` | Lift active disciplinary record |
| `/leadership history <member>` | `leadership.can_view_records` | Show last 10 DisciplinaryRecords |
| `/leadership whois <member>` | `leadership.can_view_records` | Full backend profile: rank, groups, notes, history |

---

## 3. Voice Channels & Attendance

### Temporary Voice Channels

`VoiceChannelProfile` defines channel archetypes stored in the database and managed via Django Admin. When a member joins a designated "spawner" channel the bot creates a temporary `TemporaryVoiceChannel` named after the profile's prefix + owner's display name. The new channel inherits permission overwrites defined in `VCRolePermission` rows linked to the profile.

**Channel tiers:**

| Tier | Access |
|---|---|
| BLUE (Legion) | General members and applicants |
| PURPLE (Veiled) | Ops/organized members only |
| RED (Knights) | High-intensity, restricted access |
| CUSTOM | Creator-defined overwrites |
| STAFF | Staff only |

When the last member leaves a non-permanent temp channel the bot deletes it automatically.

### Voice Bans

Members can be banned from protected (RED-tier) voice channels:

- `/adm_vban <member> [reason]` — applies a 60-day VoiceBan, adds permission overwrite blocking connect, auto-kicks if already in channel, sends private notification
- `/adm_unvban <member> [comment]` — removes the ban and overwrites
- `/adm_listvbans` — shows the 25 soonest-expiring bans
- A 5-minute background loop auto-expires bans when `expires_at` passes

### Voice Session Tracking

Every voice join/leave is recorded as a `VoiceSession` with:
- Join and leave timestamps
- Muted, deafened, streaming, video, AFK state booleans
- `duration_seconds` and `talking_time_seconds` computed on save
- `session_quality` rating (EXCELLENT / GOOD / FAIR / POOR / INACTIVE)

Sessions are aggregated daily/weekly/monthly into `VoiceActivitySummary` per user.

### Protected Channel Acknowledgment

Members must acknowledge rules before joining PROTECTED channels. A `ProtectedChannelAck` record tracks the last acknowledgment timestamp per user.

### Event Attendance

- `/attendance_start <event_name> [event_type]` — starts attendance tracking in the caller's current voice channel, creating an `EventAttendance` record
- `/attendance_status` — shows live participant count and average duration
- The `on_voice_state_update` listener auto-records `EventAttendanceRecord` rows as members join and leave
- On stop: summary embed posts to `AttendanceLogChannelID` with total participants, max concurrent, and average duration
- Interactive buttons: **Check In**, **Randomize Teams**, **Stop Tracking**

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/attendance_start <event_name> [event_type]` | `disfunction.add_eventattendance` | Start attendance tracking in current VC |
| `/attendance_status` | Same | Show live attendance stats |
| `/vc_info` | Public | Show info about caller's temp VC |
| `/adm_vban <member> [reason]` | `disfunction.can_manage_voice_bans` | Voice ban member (60 days) |
| `/adm_unvban <member> [comment]` | `disfunction.can_manage_voice_bans` | Lift voice ban |
| `/adm_listvbans` | `disfunction.can_manage_voice_bans` | List 25 soonest-expiring bans |

---

## 4. Scheduled Events (Sesh-style)

### How It Works

`EventPlan` is the core model. Events progress through statuses: `DRAFT → PUBLISHED → ACTIVE → COMPLETED` (or `CANCELLED`). Each status transition syncs with Discord's native `GuildScheduledEvent` API.

### Creating an Event

`/event create` walks through a multi-step flow:

1. Select event indicator (security level: PUBLIC ◻️, BLUE 🔵, RED 🔴, VEILED 🟣, COMP 🔶)
2. Select timezone (IANA, defaults to user's saved timezone)
3. Set capacity (or unlimited)
4. `EventCreateModal` — title, description, start time, duration (supports `2h`, `90m`, `1h30m` formats), location, recurring RRULE

The event is saved as `DRAFT`. Staff then review and `/event publish <codename>` to make it visible and create the Discord native event.

### RSVP System

Published events show a live embed with Going / Maybe / Not Going buttons. RSVP state is stored in `EventRSVP`. If capacity is set, Going slots fill up and additional RSVPs go to a waitlist. RSVP deadline enforcement is configurable per event.

### Reminders

`EventReminder` rows define when to send reminders (e.g., 60 minutes before). A 1-minute Celery task checks for due reminders and sends DMs or channel posts with configurable mentions.

### Recurring Events

Events support RFC 5545 RRULE strings. A Celery task spawns child `EventPlan` instances from the parent's RRULE before the next occurrence is due.

### Post-Event Report

On `/event end` (or auto-completion) the bot generates an `EventReport` with:
- Actual start/end times vs scheduled
- RSVP breakdown (Going / Checked-in / Late / No-show)
- Voice attendance session link
- Lookback thread ID

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/event create` | `schedevents.add_eventplan` or `can_publish_events` | Multi-step event creation wizard |
| `/event publish <codename>` | `schedevents.can_publish_events` | Publish draft, sync to Discord |
| `/event edit <codename>` | `can_publish_events` | Edit title, description, or timing |
| `/event cancel <codename>` | `can_publish_events` | Cancel with reason |
| `/event list` | Public | Browse upcoming events (paginated, 5 per page) |
| `/event info <codename>` | Public | Detailed embed with live RSVP buttons |
| `/event roster <codename>` | Public | View RSVP roster (Going / Maybe / Waitlisted) |
| `/event start <codename>` | `can_publish_events` | Manually transition to ACTIVE |
| `/event end <codename>` | `can_publish_events` | Manually end, generate report |
| `/events` | Public | Public upcoming event listing |

---

## 5. Roster & User Management

### How It Works

Every Discord member who joins gets a `DiscordUser` record linked (or pending link) to an `OrgPlayer`. Display names are computed from `OrgRank.prefix + OrgPlayer.display_name` and cached in `DisplayNameSearchCache` for fast search.

Role changes on Discord sync to Django Groups via a Redis Pub/Sub event — the bot never polls the DB for permission state.

### Member Profile

`/roster member <member>` displays:
- Handle, rank, discipline, timezone
- First event attendance date
- Discord join date
- Group memberships
- Referrer / recruiter
- Recent staff notes (OrgPlayerNote)

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/roster timezone <timezone>` | Public (self) | Set own IANA timezone |
| `/roster list [rank] [discipline] [page]` | `unifieduser.view_orgplayer` | Filtered roster, 15 per page |
| `/roster member <member>` | `unifieduser.view_orgplayer` | Full member profile |
| `/roster events <member> [page]` | `unifieduser.view_orgplayer` | Member's event attendance history |
| `/roster referrals <member>` | `unifieduser.view_orgplayer` | Referrals made by this member |
| `/adm_usersetup <member> [rank] [name]` | `org.can_manage_discord_members` | Manually create/configure backend user |
| `/userinfo <member>` | Staff | Full backend user info embed |

### Event Listeners

| Event | Action |
|---|---|
| `on_member_join` | Creates DiscordUser, syncs roles and display name |
| `on_member_update` | Validates role assignments against Django Groups |
| `on_guild_role_*` | Syncs DiscordRole metadata |
| `on_user_update` | Logs name/avatar changes to DiscordUserGuildEvent |
| `on_raw_member_remove` | Logs departure |

---

## 6. Activity Ping Roles (Self-Assign)

### How It Works

Staff post a persistent embed portal via `/activity_ping setup`. Members click toggle buttons to add or remove Discord roles from themselves. The portal is persistent — it survives bot restarts. Roles are managed in the database (not hardcoded) and grouped by category with optional emoji and description.

Rank roles (linked to `OrgRank`) are blocked from self-assignment regardless of configuration.

### Role Categories

| Category | Examples |
|---|---|
| ACTIVITY_PING | SC PvP, SC PvE, Piracy, Reml, Lost Goblin, Crackhead |
| DISCIPLINE_INTEREST | Pilot, CrewFleet, FPS, Support |
| OTHER | Custom org-specific roles |

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/activity_ping setup` | `org.manage_activity_ping_roles` | Post/refresh portal embed in current channel |
| `/activity_ping refresh` | Same | Re-render portal without moving it |
| `/activity_ping add <role> <label> <category> [description] [emoji] [display_order]` | Same | Add role to portal |
| `/activity_ping remove <role>` | Same | Remove role from portal |

---

## 7. Loot Tracker & Quartermaster

### How It Works

Two parallel inventory systems operate under the loot_tracker app:

**Loot System** — tracks bulk quantities of items (ships, weapons, consumables) by location. Members submit input/withdraw requests which staff approve, adjusting `LootStock.quantity`.

**QM / Stock Unit System** — tracks individual serialized physical items (`StockUnit`) with a full lifecycle: `AVAILABLE → ASSIGNED → RETURNED / LOST`. Each unit has a JSON stats blob, condition notes, and full assignment history via `StockAssignment`.

**Crafted Weapons** — `/qm weapon [image]` uses Gemini AI to scan an attached screenshot and auto-populate weapon name and stats. Weapon state progresses through `TrackedCraftedWeaponState` (status, current owner, purpose).

**Merit Requests** — `/prison_time` converts SC prison time to a merit request. Merit requests (money or merit type) go through the same staff review workflow as loot requests.

### Live Inventory Embed

A singleton `LootStatusMessage` embed stays live in the configured channel. Redis Pub/Sub events from stock changes trigger the bot to edit the embed in real time — no polling.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/loot input` | `loot_tracker.submit_loot_request` | Submit item deposit request (modal) |
| `/loot withdraw` | Same | Submit item withdraw request (modal) |
| `/loot set <item> <qty>` | `loot_tracker.manage_loot` | Directly set stock quantity |
| `/loot add_item <name> <category> [...]` | Same | Add new loot item to catalogue |
| `/loot status` | `loot_tracker.view_loot` | Show live inventory embed |
| `/qm add <name> <category> [image]` | `loot_tracker.manage_stock` | Add stock unit (Gemini scan optional) |
| `/qm list` | `loot_tracker.view_stock` | Full catalogue with unit counts |
| `/qm units <item>` | Same | List all units for an item |
| `/qm assign <unit_uid> <member> [notes]` | `loot_tracker.assign_stock` | Assign unit to knight |
| `/qm return <unit_uid> [notes]` | Same | Mark unit returned |
| `/qm lost <unit_uid> [notes]` | Same | Mark unit lost |
| `/qm stock <item>` | `loot_tracker.view_stock` | Quick stock summary |
| `/qm knight <member>` | Same | All stock held by a member |
| `/qm weapon [image]` | `inventory.can_submit_new_weapon` | Add crafted weapon (Gemini scan) |
| `/prison_time` | Public | Submit merit/money request (routes to Merits system) |

---

## 7b. Merits & Money Requests

### How It Works

`MeritRequest` (app/merits) is a standalone request system separate from the loot tracker. Members submit two request types:

| Kind | Purpose |
|---|---|
| MERIT | Award merit points, optionally linked to an org event |
| MONEY | aUEC payout for a specific purchase |

Requests go through `PENDING → FULFILLED / DENIED` and store the reviewer, review note, and fulfillment timestamp. An optional `linked_item` FK ties a request to a `StockItem` from the inventory app.

Staff fulfill and review requests via the web dashboard. The bot sends fulfillment DM notifications and reminder pings to the configured channel.

### Permissions

| Codename | Description |
|---|---|
| `merits.submit_merit_request` | Submit requests |
| `merits.review_merit_request` | Approve or deny |
| `merits.fulfill_merit_request` | Mark as fulfilled |
| `merits.view_merit_requests` | View all requests |

---

## 8. Distinctions & Nominations

### How It Works

Any member can nominate another for a distinction award using `/nominate`. The nomination stores the nominee, nominator, the event it relates to (schedevents, orgevents, or other), and a reason text.

Staff see a nomination portal (posted via `/nomination_setup`) with pending nominations. Each nomination card has three staff actions:
- **Award** — grants the distinction, advances `UserDistinction.current_level` if threshold met
- **Recognize** — recognizes without full award (increments recognition count)
- **Deny** — rejects with optional denial reason

`DistinctionLevel` defines progression tiers: each level requires a `nominations_required` count, grants a Discord role, and has a color and description. `UserDistinction` tracks the member's current level, total nominations received, approval/denial rates, streaks, and nomination rate.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/nominate <member>` | `disfunction.can_submit_nominations` (or unrestricted) | Nominate member for distinction |
| `/nomination_setup` | `disfunction.can_approve_nominations` | Post/refresh nomination portal |

---

## 9. Goals System

### How It Works

Two goal types exist: org-wide goals and per-leader goals. When a goal is created a Redis event fires; the bot creates a dedicated thread in the configured goals channel and mentions the relevant member (for leader goals). On completion or closure, the thread is archived and locked.

A Celery task fires reminders when a goal's due date is approaching, posting to the configured reminder channel.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/goal org set <title> [description] [due]` | `goals.manage_org_goals` | Create org-wide goal |
| `/goal org complete <goal_id>` | Same | Mark org goal completed |
| `/goal leader set <member> <title> [due]` | `goals.manage_leader_goals` | Create goal for a specific leader |
| `/goal leader complete <goal_id>` | Same | Mark leader goal completed |
| `/goals` | Public | View all open org goals |

---

## 10. Pilot Board

### How It Works

The pilot board is a structured roster of active pilots with skill ratings, tags, and goals. Pilots are linked to `OrgPlayer` records but have their own profile with a name, slug, and tag list.

Skill ratings are scored 0–5 and rendered as a visual bar in embeds. Access to PII (personal identifying information) in the pilot view requires staff confirmation.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/pilot list` | `pilotboard.view_pilot_roster` | Paginated pilot roster |
| `/pilot view <pilot>` | Same | Full stats card (PII confirmation gate for staff) |
| `/pilot add <member> [name]` | `pilotboard.manage_pilot_roster` | Add member to pilot roster |
| `/pilot delete <pilot>` | Same | Remove pilot |
| `/pilot rate <pilot> <skill> <value>` | Same | Update skill rating (0–5) |
| `/pilot tag <pilot> <tag> <add\|remove>` | Same | Manage pilot tags |
| `/pilot status <pilot> <active\|inactive>` | Same | Toggle active status |
| `/pilot goal set <pilot> <text>` | Same | Set pilot goal |
| `/pilot goal complete <pilot>` | Same | Mark pilot goal completed |

---

## 11. FPS Scorecard

### How It Works

`Scorecard` records per-pilot FPS performance ratings across multiple categories (Combat, Tactical, Other). Scorecards have a version counter — every save increments it.

When a scorecard is saved, updated, activated, or deactivated, a Redis event fires and the bot DMs the affected member with a formatted embed showing their scores as visual bars and their tier label (NOVICE through ELITE).

New autocreated scorecards post a notification to the Infantry Lead channel. New pilot records post to the Pilot Lead channel.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/scorecard [member]` | Public (self); `scorecard.manage_scorecards` for others | View FPS scorecard embed |

---

## 12. Kill Tracker

### How It Works

External systems post kill events to the `killtracker.events` Redis channel via API key authentication. The bot subscribes and posts kill embeds to the configured game-mode channel or the default kill tracker channel.

Members generate API keys via slash command to authorize their external tools (Star Citizen overlays, third-party trackers) to post kill events.

`/rsilookup` fetches live RSI citizen data, stores a `RSIProfileSnapshot`, and tracks changes across lookups.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/killtracker generate-key [label]` | `killtracker.can_generate_key` | Generate API key for kill feed integration |
| `/killtracker my-keys` | Same | List own API keys |
| `/rsilookup <handle>` | Public | Look up RSI citizen profile, tracks changes |

---

## 13. Organic Events (AI-Assisted)

### How It Works

Organic events are spontaneous, short-lived org activities (DEATHWATCH, SCPVP, LOOTGOBLIN). Any member can start one from a persistent button portal.

On creation:
- A main embed is posted in the events channel
- A thread is spawned for coordination chat
- A `ThreadControlView` appears with **End Action**, **Set VC** (voice channel select dropdown) controls

As members chat in the thread the bot buffers up to 100 messages (20k character limit). Every 4.5 seconds (debounced) Gemini AI (`gemma-3-27b-it`) analyzes the buffer and updates a "Tactical Highlights" section in the embed. MD5 hash checking prevents redundant AI calls when the buffer hasn't changed.

Active event state is cached in memory and persisted to the database on creation; the `startup_task` reloads active events from DB on bot restart.

**No slash commands** — entirely button/select-menu driven from the persistent portal.

---

## 14. Knowledge Base / CMS

### How It Works

`Post` and `Entry` records are managed via Django Admin or web views. The bot syncs them to Discord forum channels via Redis Pub/Sub:

- `TAGS.UPDATE` → syncs forum channel tags
- `POST.CREATE/UPDATE/DELETE` → creates, edits, or deletes forum threads
- `POST.INTERNALREORDER` → re-syncs post after entry reorder
- `ENTRY.CREATE/UPDATE/DELETE` → creates, edits, or deletes messages within threads
- `LIVE_MSG.UPDATE` → pushes the first text entry of an article into a linked non-forum Discord message (used for pinned summary embeds)

A full sync runs on bot startup.

Members can search and browse from Discord without needing to visit the web dashboard.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/kbtopics <tag>` | Public (STAFF posts require `discordwebcms.view_staff_post`) | Browse KB entries by tag with autocomplete |
| `/kbsearch <query>` | Public (staff see STAFF-visibility posts) | Full-text search by title or content |
| `/kb link <article_id> <channel_id> <message_id>` | `discordwebcms.edit_post` | Link a KB article to a live non-forum Discord message; pushes content immediately |

---

## 15. Discord Account Linking

### How It Works

The web dashboard generates a one-time link code (10-minute TTL). The member uses `/link <code>` in Discord to bind their Discord identity to their `OrgPlayer` backend account. The bot validates the token, creates/updates the `DiscordUser` record, and reports success or conflict.

Staff can force-link a Discord account to a backend account via `/link-account`.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/link <code>` | `discordauth.can_self_link_account` | Link Discord to site account via one-time code |
| `/link-account <member> <account>` | Admin + `discordauth.can_link_discord_accounts` | Staff force-link override |

---

## 16. Message & Voice Logging

### How It Works

Every message event in the guild is captured to `LoggedDiscordMessage` with attachments mirrored to LitterBox for persistence. Edit and delete events are logged with before/after content and the audit-log executor (who deleted it).

Voice state is snapshotted every second. A Celery task compiles raw snapshots into 15-minute `CompiledLoggedVoiceState` sessions every 30 minutes.

Channel and thread metadata is synced on create/update/delete events.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/transcript <channel> [limit=200]` | `discordlogger.view_transcripts` | Export message log as downloadable text file (max 500) |

### Event Listeners

| Event | Action |
|---|---|
| `on_message` | Log + mirror attachments |
| `on_message_edit` | Log edit with before/after |
| `on_message_delete` | Log delete + audit executor |
| `on_bulk_message_delete` | Log bulk delete |
| `on_guild_channel_create/update/delete` | Sync channel metadata |
| `on_thread_create/update/delete` | Sync thread metadata |

---

## 17. Announcements

### How It Works

`/announce post` opens an `AnnounceModal` for title, body, and optional role ping. An optional approval gate (`ApprovalView`) requires a designated reviewer to approve before the post goes out. Approved announcements are posted to the configured channel with the role ping. Staff can view pending announcements via `/announce pending`.

### Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/announce post [audience] [channel]` | `org.can_manage_discord_members` | Create and optionally send announcement |
| `/announce pending` | Same | List announcements awaiting approval |

---

## 18. RSI / Star Citizen Status Tracking

### How It Works

**Hangar Status** — the bot subscribes to `sc_tracker:hangar_events`. On `update_embed` events it updates or posts the hangar status embed with light-phase visualization and role ping on phase transitions.

**RSI Status** — the bot subscribes to `sc_tracker:rsi_events`. On new/updated/resolved issues it updates the live status embed in the configured channel. Subscribed members receive DMs. On resolution the bot cleans up resolved-issue DMs. New subscribers who join when issues are active immediately receive the current issues.

### Buttons (Persistent Portal)

| Button | Permission | Action |
|---|---|---|
| 🔔 Subscribe | `sc_tracker.can_subscribe_status` | Subscribe to RSI status DM alerts |
| 🔕 Unsubscribe | Same | Unsubscribe and clean up DMs |

---

## 19. Org Announcements

Handled by the Announcements section above. The org announce cog supports targeted audience selection and optional approval workflows before posts are sent.

---

## 20. Background Infrastructure

### Redis Pub/Sub Channels

| Channel | Publisher | Subscriber | Purpose |
|---|---|---|---|
| `permissions.unifieduser.sync` | unifieduser signals | usermanager cog | Real-time permission/rank sync |
| `candidacy.*` | candidacy signals | candidacy cogs | Application state changes |
| `schedevents.*` | schedevents signals | event cogs | Event CRUD, RSVP changes |
| `killtracker.events` | External API | killtracker cog | Kill event feed |
| `sc_tracker:hangar_events` | sc_tracker tasks | hangar cog | Hangar status updates |
| `sc_tracker:rsi_events` | sc_tracker tasks | rsi_status cog | RSI issue updates |
| `goals.*` | goals signals | goals cog | Goal CRUD, reminders |
| `STOCK_UPDATED` | loot_tracker signals | loot cog | Live inventory embed refresh |
| `SCORECARD_*` | scorecard signals | scorecard cog | Scorecard DM notifications |
| `tags.*` / `post.*` / `entry.*` / `live_msg.*` | discordwebcms signals | cms cog | Forum sync + live message updates |

### Recurring Celery Tasks

| Task | Interval | App | Purpose |
|---|---|---|---|
| Voice state compilation | 30 min | discordlogger | Compile snapshots to sessions |
| Discord cache refresh | 30 min | discordauth | Refresh bot's Discord cache |
| OAuth token refresh | 2 hours | discordauth | Renew OAuth tokens |
| Role → Group sync | 2 hours | discordauth | Sync Discord roles to Django Groups |
| Guild roles refresh | 2 hours | org | Refresh guild role metadata |
| Event auto-completion | 5 min | schedevents | Complete past events |
| Event reminders | 1 min | schedevents | Fire due reminders |
| Follow-up applicant DMs | 6 hours | candidacy | DM applicants with no submission |
| Auto-close applications | 24 hours | candidacy | Close abandoned applications |
| Onboarding leader rotation | 7 days | candidacy | Rotate duty leaders |
| Weekly event digest | 7 days | schedevents | Post weekly event summary |
| Display name cache rebuild | 6 hours | unifieduser | Rebuild search cache |
| Goal due reminders | Periodic | goals | Notify approaching deadlines |
| Voice ban expiry check | 5 min | disfunction | Auto-lift expired voice bans |
| Kill event processing | On-demand | killtracker | Process incoming kill reports |
| In-game mail fetch | 5 min | mailclient | Poll IMAP inbox |

### Permission Architecture

All command authorization goes through Django Groups + `django-guardian` for object-level permissions. Codenames follow the pattern `app_label.codename`. The `@requires_django_perm` decorator on slash commands checks the caller's cached permission set (kept live via Redis Pub/Sub) — no DB call per interaction.

Role changes on Discord → Django Group sync → Redis broadcast → all bot cog caches updated within the 0.1–0.3s listener loop cycle.

---

*Last updated: 2026-04-29*

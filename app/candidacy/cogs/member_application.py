"""
app/candidacy/cogs/member_application.py

Membership verification + application cog.
"""
import asyncio
import random
import re
import string
from datetime import timedelta, datetime

import discord
from discord.ext import commands, tasks
from asgiref.sync import sync_to_async
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.utils.timezone import now

import redis.asyncio as redis

import hashlib
import json
from app.candidacy.models import MembershipApplicationType, MembershipApplicationRecord, RSIVerification, MembershipRules, RulesAgreement, AssignableDiscipline, MembershipEmbedMessage
from app.main.util.formatting import SafeDict
from app.discordauth.models import DiscordUser
from app.preferences.utils import aget_global_preference
from app.main.util.db import get_users_with_permission_on
from app.main.util import img as img_util
from app.main.util.discord_command_checks import (
    _verify_and_cache_permission,
    MissingDjangoObjectPermission,
    AccountNotLinked,
    requires_django_perm,
)
from ..preferences import (
    MembershipVerificationChannelID,
    MembershipReviewChannelID,
    MembershipApplicationChannelID,
    MembershipTranscriptChannelID,
    MembershipWelcomeChannelID,
    MembershipDisciplineSelectionChannelID,
    MembershipVisitorRankPK,
    MembershipRSILinkingEnabled,
    MembershipRSIVerificationEnabled,
    MembershipMinimumAge,
    MembershipRSIVerificationTimeoutMinutes,
    MembershipRSICheckIntervalSeconds,
    MembershipVisitorPermissionGroups,
    MembershipApprovalMessage,
    MembershipDenialMessage,
    MembershipThreadViewerGroups,
    MembershipWelcomeMessage,
    MembershipAutoArchiveHours,
    MembershipRulesEnabled,
    MembershipDenialCooldownDays,
    MembershipInductionImageEnabled,
    MembershipInductionImagePath,
    ExternalRankPK,
    ExternalPermissionGroups,
    ExternalReviewChannelID,
    ExternalThreadViewerGroups,
)

logger = get_task_logger(__name__)


# ---------------------------------------------------------------------------
# Modal draft persistence (Redis, 7-day TTL, keyed by user+modal type)
# ---------------------------------------------------------------------------

_DRAFT_TTL = 60 * 60 * 24 * 7  # 7 days


def _draft_key(user_id: int, modal: str) -> str:
    return f"draft:modal:{user_id}:{modal}"


async def _load_draft(rc, user_id: int, modal: str) -> dict:
    raw = await rc.get(_draft_key(user_id, modal))
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


async def _save_draft(rc, user_id: int, modal: str, data: dict) -> None:
    await rc.set(_draft_key(user_id, modal), json.dumps(data), ex=_DRAFT_TTL)


async def _clear_draft(rc, user_id: int, modal: str) -> None:
    await rc.delete(_draft_key(user_id, modal))


# ---------------------------------------------------------------------------
# RSI helpers
# ---------------------------------------------------------------------------

def _generate_verification_code() -> str:
    letters = "".join(random.choices(string.ascii_uppercase, k=4))
    digits = "".join(random.choices(string.digits, k=4))
    return f"BV-{letters}-{digits}"


async def _set_display_name_to_handle(discord_id: int, rsi_handle: str) -> None:
    """Store the RSI handle as CustomDisplayName. DisplayNameSource=4 is set later on rank assignment."""
    from app.unifieduser.preferences import CustomDisplayName as _CDN
    try:
        du = await DiscordUser.objects.select_related("user").aget(discorduid=discord_id)
        if du.user is None:
            return
        cdn = await du.user.aget_setting(_CDN.import_path)
        cdn.value = rsi_handle
        await cdn.asave()
    except Exception:
        logger.exception("_set_display_name_to_handle failed discord_id=%s", discord_id)


def _validate_rsi_url(url: str) -> bool:
    patterns = [
        r"^https?://(www\.)?robertsspaceindustries\.com/citizens/[^/]+/?$",
        r"^https?://(www\.)?robertsspaceindustries\.com/(?:en|fr|de|es|ru)/citizens/[^/]+/?$",
    ]
    return any(re.match(p, url.strip(), re.IGNORECASE) for p in patterns)


def _extract_rsi_handle(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


async def _check_rsi_profile(rsi_url: str, code: str) -> bool:
    """Return True if *code* appears in the RSI profile page."""
    try:
        import aiohttp
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BVBot/1.0)"}
        async with aiohttp.ClientSession() as session:
            async with session.get(rsi_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                html = await resp.text()
                return any(
                    re.search(p, html, re.IGNORECASE)
                    for p in [rf">{re.escape(code)}<", rf'"{re.escape(code)}"', rf"\b{re.escape(code)}\b"]
                )
    except Exception as exc:
        logger.error("RSI profile check error: %s", exc)
        return False


async def _extract_rsi_details(rsi_url: str) -> dict:
    """
    Fetch the RSI profile page and extract enlisted date and organisation membership.
    Returns dict with keys 'enlisted_date' (date string or None) and 'org_membership' (str).
    """
    try:
        import aiohttp
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BVBot/1.0)"}
        async with aiohttp.ClientSession() as session:
            async with session.get(rsi_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return {'enlisted_date': None, 'org_membership': None}
                html = await resp.text()
                # Patterns for RSI profile page (subject to change)
                enlisted_pattern = r'Enlisted</div>\s*<div[^>]*>([^<]+)</div>'
                org_pattern = r'Organization</div>\s*<div[^>]*>([^<]+)</div>'
                enlisted = re.search(enlisted_pattern, html)
                org = re.search(org_pattern, html)
                enlisted_date = enlisted.group(1).strip() if enlisted else None
                org_membership = org.group(1).strip() if org else None
                return {'enlisted_date': enlisted_date, 'org_membership': org_membership}
    except Exception as exc:
        logger.error("RSI profile details extraction error: %s", exc)
        return {'enlisted_date': None, 'org_membership': None}


# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------

async def get_reviewer_discord_ids(membership_type: MembershipApplicationType) -> list[int]:
    """
    Get Discord IDs of users who can review the given membership application type.

    This wraps the sync get_users_with_permission_on call properly for async context.
    Guardian's get_users_with_perms performs synchronous DB operations internally.
    """
    def _get_ids():
        users_qs = get_users_with_permission_on(
            "candidacy.can_review_membershipapplication", membership_type
        )
        # Evaluate the union queryset to a list (no further operations allowed)
        users = list(users_qs)
        # Filter out users without a linked Discord account and extract IDs
        return [
            user.discorduser.discorduid
            for user in users
            if hasattr(user, 'discorduser') and user.discorduser
        ]
    return await sync_to_async(_get_ids)()


# ---------------------------------------------------------------------------
# Core submission function
# ---------------------------------------------------------------------------

async def create_membership_application(
    interaction: discord.Interaction,
    membership_type: MembershipApplicationType,
    answers: list[str],
    rsi_verification: RSIVerification | None,
    age_warning: bool = False,
    age: int = None,
    referrer_name: str = "",
    referral_note: str = "",
):
    """Create a MembershipApplicationRecord + Discord thread."""
    # Resolve backend user — must be linked
    try:
        discord_user = await DiscordUser.objects.select_related('user').aget(discorduid=interaction.user.id)
        applicant = discord_user.user
        if not applicant:
            msg = "Your Discord account is linked but the user record is missing. Please contact staff or use `/link` to re-link your account."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return
    except DiscordUser.DoesNotExist:
        msg = "You must link your Discord account before applying. Use `/link` to link your account."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return

    verification_chan_id = await aget_global_preference(MembershipVerificationChannelID.import_path)
    channel = interaction.client.get_channel(verification_chan_id)
    if not channel:
        msg = "❌ Verification channel is not configured. Please contact staff."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return

    # Create private thread
    rsi_handle = rsi_verification.rsi_handle if rsi_verification else ""
    thread_name = f"{rsi_handle or interaction.user.display_name} - {membership_type.name}"[:100]

    if isinstance(channel, discord.ForumChannel):
        thread_with_message = await channel.create_thread(
            name=thread_name,
            content=f"Membership application by {interaction.user.mention}",
        )
        thread = thread_with_message.thread
    else:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False,
        )

    # Add the applicant to the thread – wait and retry
    await asyncio.sleep(0.5)
    for attempt in range(3):
        try:
            await thread.add_user(interaction.user)
            break
        except discord.Forbidden as e:
            if attempt == 2:
                logger.error(f"Failed to add user to thread after 3 attempts: {e}")
                raise
            await asyncio.sleep(1)

    # Resolve referrer OrgPlayer from name/handle (best-effort, never blocks)
    referred_by_player = None
    if referrer_name:
        from app.unifieduser.models import DisplayNameSearchCache
        from asgiref.sync import sync_to_async as _s2a
        def _find_referrer():
            # Try display name cache first (covers Discord display names & RSI handles)
            cache_hit = DisplayNameSearchCache.objects.filter(
                display_name__iexact=referrer_name
            ).select_related("user").first()
            if cache_hit:
                return cache_hit.user
            # Try by Discord snowflake if the input looks numeric
            if referrer_name.strip().isdigit():
                from app.discordauth.models import DiscordUser as _DU
                du = _DU.objects.select_related("user").filter(
                    discorduid=int(referrer_name.strip())
                ).first()
                if du:
                    return du.user
            return None
        referred_by_player = await _s2a(_find_referrer)()

    # Save record
    app_record = await MembershipApplicationRecord.objects.acreate(
        applicant=applicant,
        membership_type=membership_type,
        status=MembershipApplicationRecord.Status.PENDING,
        thread_id=thread.id,
        rsi_handle=rsi_handle,
        rsi_profile_url=rsi_verification.rsi_profile_url if rsi_verification else "",
        rsi_verified=bool(rsi_verification),
        answer_1=answers[0],
        answer_2=answers[1],
        answer_3=answers[2],
        answer_4=answers[3],
        answer_5=answers[4],
        age_warning=age_warning,
        referred_by=referred_by_player,
        referral_note=referral_note,
    )

    _guild = thread.guild

    async def _add_to_thread(uid: int) -> None:
        m = _guild.get_member(uid)
        if m is None:
            try:
                m = await _guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden):
                logger.warning("create_membership_application: member %s not in guild, skipping", uid)
                return
        try:
            await thread.add_user(m)
        except discord.Forbidden:
            logger.warning("create_membership_application: Forbidden adding %s to thread %s", uid, thread.id)
        except Exception as exc:
            logger.error("create_membership_application: add_user(%s) failed: %s", uid, exc)

    # Add reviewers (staff with permission on the type)
    reviewer_ids = await get_reviewer_discord_ids(membership_type)
    for discord_id in reviewer_ids:
        await _add_to_thread(discord_id)
        await asyncio.sleep(0.1)

    # Add members of configured groups
    groups_csv = await aget_global_preference(MembershipThreadViewerGroups.import_path)
    if groups_csv:
        group_ids = [int(x.strip()) for x in groups_csv.split(",") if x.strip().isdigit()]
        for group_id in group_ids:
            try:
                group = await Group.objects.filter(pk=group_id).afirst()
                if group:
                    async for user in group.user_set.all().prefetch_related("discorduser"):
                        if hasattr(user, "discorduser") and user.discorduser:
                            await _add_to_thread(user.discorduser.discorduid)
                            await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to add group {group_id} members to thread: {e}")

    # Build embed (for both thread and review channel)
    embed = discord.Embed(
        title=f"Membership Application: {membership_type.name}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Applicant", value=interaction.user.mention, inline=False)

    # Discord profile info
    member = _guild.get_member(interaction.user.id)
    if member:
        created_at = int(member.created_at.timestamp())
        joined_at = int(member.joined_at.timestamp())
        roles = [r.mention for r in member.roles[1:]]  # exclude @everyone
        roles_str = ", ".join(roles[:10]) if roles else "None"
        profile_info = (
            f"Account created: <t:{created_at}:R>\n"
            f"Joined server: <t:{joined_at}:R>\n"
            f"Roles: {roles_str}"
        )
    else:
        profile_info = "Could not retrieve Discord profile info."
    embed.add_field(name="Discord Profile", value=profile_info, inline=False)

    if rsi_verification:
        embed.add_field(name="RSI Handle", value=f"`{rsi_handle}`", inline=True)
        embed.add_field(name="RSI Profile", value=f"[View]({rsi_verification.rsi_profile_url})", inline=True)
        # Add RSI details if available
        if rsi_verification.enlisted_date:
            embed.add_field(name="RSI Enlisted", value=rsi_verification.enlisted_date.isoformat(), inline=True)
        if rsi_verification.org_membership:
            embed.add_field(name="RSI Organization", value=rsi_verification.org_membership, inline=True)

    # Age warning
    if age_warning:
        embed.add_field(name="⚠️ Age Warning", value=f"Applicant is under the minimum age ({age} years old).", inline=False)
        logger.warning(
            "UNDER-AGE APPLICANT FLAGGED: discord_id=%s username=%r age=%s membership_type=%r",
            interaction.user.id,
            str(interaction.user),
            age,
            membership_type.name,
        )

    has_question = False
    for i in range(1, 6):
        q_text = getattr(membership_type, f"question_{i}")
        if q_text and answers[i - 1]:
            embed.add_field(name=q_text, value=answers[i - 1], inline=False)
            has_question = True

    if not has_question:
        embed.description = "No questions were required for this application type."

    if referrer_name or referral_note:
        referral_parts = []
        if referrer_name:
            referral_parts.append(f"**Referred by:** {referrer_name}")
        if referral_note:
            referral_parts.append(f"**How they heard:** {referral_note}")
        embed.add_field(name="Referral", value="\n".join(referral_parts), inline=False)

    async def _get_channel(client, chan_id):
        ch = client.get_channel(chan_id)
        if ch is None:
            try:
                ch = await client.fetch_channel(chan_id)
            except Exception as exc:
                logger.warning("fetch_channel(%s) failed: %s", chan_id, exc)
        return ch

    # Send to thread (no buttons)
    try:
        await thread.send(embed=embed)
    except Exception:
        logger.exception("Failed to send embed to application thread %s", thread.id)

    # Send to review channel with buttons
    review_chan_id = await aget_global_preference(MembershipReviewChannelID.import_path)
    if review_chan_id:
        review_channel = await _get_channel(interaction.client, review_chan_id)
        if review_channel:
            review_embed = embed.copy()
            review_embed.add_field(name="Application Thread", value=thread.mention, inline=False)
            review_embed.set_footer(text=f"app:{app_record.id}|thread:{thread.id}")
            view = MembershipReviewView(application_id=app_record.id, thread_id=thread.id)
            try:
                await review_channel.send(embed=review_embed, view=view)
            except Exception:
                logger.exception("Failed to send to review channel %s", review_chan_id)
        else:
            logger.warning("Review channel %s not found", review_chan_id)
    else:
        logger.warning("MembershipReviewChannelID preference not set")

    # Send to public application channel (summary)
    app_chan_id = await aget_global_preference(MembershipApplicationChannelID.import_path)
    if app_chan_id:
        app_channel = await _get_channel(interaction.client, app_chan_id)
        if app_channel:
            summary = discord.Embed(
                title=f"New Application: {membership_type.name}",
                description=f"Applicant: {interaction.user.mention}\n"
                            f"RSI Handle: {rsi_handle if rsi_handle else 'Not verified'}",
                color=discord.Color.gold()
            )
            for i, q in enumerate([getattr(membership_type, f"question_{i}") for i in range(1, 6)], start=1):
                if q and answers[i-1]:
                    summary.add_field(name=q, value=answers[i-1][:1024], inline=False)
            summary.add_field(name="Thread", value=thread.mention, inline=False)
            try:
                await app_channel.send(embed=summary)
            except Exception:
                logger.exception("Failed to send to application channel %s", app_chan_id)
        else:
            logger.warning("Application channel %s not found", app_chan_id)
    else:
        logger.warning("MembershipApplicationChannelID preference not set")

    # Send response to user
    msg = f"✅ Application submitted! Check {thread.mention}"
    if not interaction.response.is_done():
        await interaction.response.send_message(msg, ephemeral=True)
    else:
        await interaction.followup.send(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# RSI Verification Modal
# ---------------------------------------------------------------------------

class RSIUrlModal(discord.ui.Modal, title="RSI Profile Verification"):
    rsi_handle_input = discord.ui.TextInput(
        label="RSI Username",
        placeholder="e.g. CrystalBlaze (the part after /citizens/)",
        required=True,
        max_length=60,
    )

    def __init__(self, cog: "MembershipApplicationCog", membership_type: MembershipApplicationType, draft: dict | None = None):
        super().__init__()
        self.cog = cog
        self.membership_type = membership_type
        if draft and draft.get("rsi_handle"):
            self.rsi_handle_input.default = draft["rsi_handle"]

    async def on_submit(self, interaction: discord.Interaction):
        handle = self.rsi_handle_input.value.strip()
        # Strip full URL if someone pastes it anyway
        if "/" in handle:
            handle = handle.rstrip("/").split("/")[-1]
        rsi_url = f"https://robertsspaceindustries.com/en/citizens/{handle}"
        await _save_draft(self.cog.redis_client, interaction.user.id, "rsi_url", {"rsi_handle": handle})
        await interaction.response.send_message(
            f"Is this your RSI profile?\n**<{rsi_url}>**",
            view=_RSIConfirmView(self.cog, self.membership_type, handle, rsi_url),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await _save_draft(self.cog.redis_client, interaction.user.id, "rsi_url", {"rsi_handle": self.rsi_handle_input.value})
        raise error


class _RSIConfirmView(discord.ui.View):
    def __init__(self, cog: "MembershipApplicationCog", membership_type: MembershipApplicationType, handle: str, rsi_url: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.membership_type = membership_type
        self.handle = handle
        self.rsi_url = rsi_url

    @discord.ui.button(label="Yes, that's me", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _clear_draft(self.cog.redis_client, interaction.user.id, "rsi_url")
        self.stop()
        rsi_verify = await aget_global_preference(MembershipRSIVerificationEnabled.import_path)
        if not rsi_verify:
            # Verification OFF — record the handle then open the application modal directly
            rsi_verification, _ = await RSIVerification.objects.aupdate_or_create(
                discord_id=interaction.user.id,
                defaults={
                    "rsi_handle": self.handle,
                    "rsi_profile_url": self.rsi_url,
                    "verification_status": RSIVerification.Status.PENDING,
                    "verification_code": "",
                    "expires_at": now() + timedelta(days=365),
                },
            )
            # Set CustomDisplayName = RSI handle + source = 4 so rank prefix is always prepended on future changes
            await _set_display_name_to_handle(interaction.user.id, self.handle)
            draft = await _load_draft(self.cog.redis_client, interaction.user.id, f"app:{self.membership_type.pk}")
            await interaction.response.send_modal(MembershipApplicationModal(self.membership_type, rsi_verification, rc=self.cog.redis_client, user_id=interaction.user.id, draft=draft))
        else:
            await self.cog.handle_rsi_url_submit(interaction, self.rsi_url, self.membership_type)

    @discord.ui.button(label="No, try again", style=discord.ButtonStyle.danger)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        draft = {"rsi_handle": self.handle}
        await interaction.response.send_modal(RSIUrlModal(self.cog, self.membership_type, draft=draft))


# ---------------------------------------------------------------------------
# Application Modal
# ---------------------------------------------------------------------------

class MembershipApplicationModal(discord.ui.Modal):
    def __init__(self, membership_type: MembershipApplicationType, rsi_verification: RSIVerification | None = None, rc=None, user_id: int | None = None, draft: dict | None = None):
        title = f"Apply: {membership_type.name}"[:45]
        super().__init__(title=title)
        self.membership_type = membership_type
        self.rsi_verification = rsi_verification
        self._rc = rc
        self._user_id = user_id

        self.questions = []
        for i in range(1, 6):
            q_text = getattr(membership_type, f"question_{i}")
            if q_text:
                text_input = discord.ui.TextInput(
                    label=q_text[:45],
                    style=discord.TextStyle.paragraph,
                    required=True,
                    max_length=1000,
                    default=(draft or {}).get(f"q{i}", None),
                )
                self.add_item(text_input)
                self.questions.append(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if self._rc and self._user_id:
            await _clear_draft(self._rc, self._user_id, f"app:{self.membership_type.pk}")

        answers = [q.value for q in self.questions]
        while len(answers) < 5:
            answers.append("")

        # Age validation — uses the explicit age_question_index on the membership type
        min_age = await aget_global_preference(MembershipMinimumAge.import_path)
        age_warning = False
        age_val = None
        age_idx = self.membership_type.age_question_index  # 0 = disabled, 1-5 = which question
        if age_idx and min_age:
            raw = answers[age_idx - 1].strip()
            try:
                age_val = int(raw)
                if age_val < 1 or age_val > 120:
                    raise ValueError("out of range")
                if age_val < min_age:
                    age_warning = True
            except ValueError:
                await interaction.followup.send(
                    f"❌ Question {age_idx} asks for your age — please enter a number (e.g. `21`).",
                    ephemeral=True,
                )
                return

        if self._rc and self._user_id:
            draft_data = {f"q{i+1}": v for i, v in enumerate(answers) if v}
            await _save_draft(self._rc, self._user_id, f"app:{self.membership_type.pk}", draft_data)

        await interaction.followup.send(
            "Almost done! Do you know anyone in BlightVeil who referred you? (optional)",
            view=_ReferralPromptView(
                self.membership_type, answers, self.rsi_verification, age_warning, age_val,
                rc=self._rc, user_id=self._user_id,
            ),
            ephemeral=True,
        )


class _ReferralModal(discord.ui.Modal, title="Referral (Optional)"):
    referrer_name = discord.ui.TextInput(
        label="Who referred you? (Discord or RSI handle)",
        placeholder="Leave blank if no one referred you",
        required=False,
        max_length=100,
    )
    referral_note = discord.ui.TextInput(
        label="How did you hear about us?",
        placeholder="e.g. Reddit, friend, YouTube, random Discord server…",
        required=False,
        max_length=200,
    )

    def __init__(self, membership_type, answers, rsi_verification, age_warning, age_val, rc=None, user_id=None, draft=None, **kwargs):
        super().__init__(**kwargs)
        self.membership_type  = membership_type
        self.answers          = answers
        self.rsi_verification = rsi_verification
        self.age_warning      = age_warning
        self.age_val          = age_val
        self.timeout          = 300
        self._rc              = rc
        self._user_id         = user_id
        if draft:
            if draft.get("referrer_name"):
                self.referrer_name.default = draft["referrer_name"]
            if draft.get("referral_note"):
                self.referral_note.default = draft["referral_note"]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if self._rc and self._user_id:
            await _clear_draft(self._rc, self._user_id, "referral")
        await create_membership_application(
            interaction, self.membership_type, self.answers, self.rsi_verification,
            self.age_warning, self.age_val,
            referrer_name=self.referrer_name.value.strip(),
            referral_note=self.referral_note.value.strip(),
        )


class _ReferralPromptView(discord.ui.View):
    def __init__(self, membership_type, answers, rsi_verification, age_warning, age_val, rc=None, user_id=None):
        super().__init__(timeout=120)
        self.membership_type  = membership_type
        self.answers          = answers
        self.rsi_verification = rsi_verification
        self.age_warning      = age_warning
        self.age_val          = age_val
        self._rc              = rc
        self._user_id         = user_id

    @discord.ui.button(label="Yes, fill in referral", style=discord.ButtonStyle.primary)
    async def fill_referral(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = {}
        if self._rc and self._user_id:
            draft = await _load_draft(self._rc, self._user_id, "referral")
        await interaction.response.send_modal(
            _ReferralModal(self.membership_type, self.answers, self.rsi_verification,
                           self.age_warning, self.age_val, rc=self._rc, user_id=self._user_id, draft=draft)
        )
        self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_referral(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if self._rc and self._user_id:
            await _clear_draft(self._rc, self._user_id, "referral")
        await create_membership_application(
            interaction, self.membership_type, self.answers, self.rsi_verification,
            self.age_warning, self.age_val,
        )
        self.stop()


# ---------------------------------------------------------------------------
# Rules gate view
# ---------------------------------------------------------------------------

class RulesView(discord.ui.View):
    """Paginated rules view. Agree button only appears on the last page."""

    def __init__(self, cog: "MembershipApplicationCog", items: list[MembershipApplicationType], pages: list[str], rules_hash: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.items = items
        self.pages = pages
        self.rules_hash = rules_hash
        self.current = 0
        self._rebuild()

    def _embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📜 Rules & Guidelines",
            description=self.pages[self.current],
            color=discord.Color.yellow(),
        )
        if len(self.pages) > 1:
            embed.set_footer(text=f"Page {self.current + 1} of {len(self.pages)} — Read all pages to continue.")
        else:
            embed.set_footer(text="Please read before continuing.")
        return embed

    def _rebuild(self):
        self.clear_items()

        if len(self.pages) > 1:
            back = discord.ui.Button(
                label="◀ Back",
                style=discord.ButtonStyle.secondary,
                disabled=self.current == 0,
            )
            back.callback = self._back
            self.add_item(back)

            nxt = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.primary,
                disabled=self.current >= len(self.pages) - 1,
            )
            nxt.callback = self._next
            self.add_item(nxt)

        if self.current >= len(self.pages) - 1:
            agree = discord.ui.Button(
                label="✅ I Have Read and Agree to the Rules",
                style=discord.ButtonStyle.success,
            )
            agree.callback = self._agree
            self.add_item(agree)

    async def _back(self, interaction: discord.Interaction):
        self.current -= 1
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.current += 1
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _agree(self, interaction: discord.Interaction):
        await RulesAgreement.objects.aupdate_or_create(
            discord_id=interaction.user.id,
            defaults={"rules_hash": self.rules_hash},
        )
        select_view = discord.ui.View(timeout=300)
        select_view.add_item(MembershipTypeSelect(self.cog, self.items))
        await interaction.response.edit_message(
            content="✅ Rules acknowledged! Please select the membership path you want to apply for:",
            embed=None,
            view=select_view,
        )


# ---------------------------------------------------------------------------
# Membership Type Select
# ---------------------------------------------------------------------------

class MembershipTypeSelect(discord.ui.Select):
    def __init__(self, cog: "MembershipApplicationCog", items: list[MembershipApplicationType]):
        self.cog = cog
        self.items_map = {str(item.id): item for item in items}

        options = [
            discord.SelectOption(
                label=item.name[:100],
                description=(item.description[:97] + "…") if len(item.description) > 97 else item.description or None,
                value=str(item.id),
            )
            for item in items
        ]
        super().__init__(placeholder="Select a membership path…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.items_map[self.values[0]]

        # RSI gate — master switch first, then bio-code verification
        rsi_linking = await aget_global_preference(MembershipRSILinkingEnabled.import_path)
        rsi_verification = None

        if rsi_linking:
            rsi_verify = await aget_global_preference(MembershipRSIVerificationEnabled.import_path)
            if rsi_verify:
                rsi_verification = await RSIVerification.objects.filter(
                    discord_id=interaction.user.id,
                    verification_status=RSIVerification.Status.VERIFIED,
                ).order_by("-verified_at").afirst()

                if not rsi_verification:
                    draft = await _load_draft(self.cog.redis_client, interaction.user.id, "rsi_url")
                    await interaction.response.send_modal(RSIUrlModal(self.cog, selected, draft=draft))
                    return
            # linking on but verification off — collect URL without bio-code
            else:
                rsi_verification = await RSIVerification.objects.filter(
                    discord_id=interaction.user.id,
                    verification_status__in=[
                        RSIVerification.Status.VERIFIED,
                        RSIVerification.Status.PENDING,
                    ],
                ).order_by("-created_at").afirst()
                if not rsi_verification:
                    draft = await _load_draft(self.cog.redis_client, interaction.user.id, "rsi_url")
                    await interaction.response.send_modal(RSIUrlModal(self.cog, selected, draft=draft))
                    return

        # Check for existing pending/approved application
        try:
            discord_user = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message(
                "You must link your Discord account before applying.", ephemeral=True
            )
            return

        existing = await MembershipApplicationRecord.objects.filter(
            applicant=discord_user.user,
            membership_type=selected,
        ).exclude(
            status__in=[
                MembershipApplicationRecord.Status.DENIED,
                MembershipApplicationRecord.Status.RESET,
            ]
        ).afirst()

        if existing:
            label = "pending review" if existing.status == MembershipApplicationRecord.Status.PENDING else "already approved"
            await interaction.response.send_message(
                f"❌ You already have an application for **{selected.name}** that is {label}.",
                ephemeral=True,
            )
            return

        # Denial cooldown check
        cooldown_days = await aget_global_preference(MembershipDenialCooldownDays.import_path)
        if cooldown_days:
            cutoff = now() - timedelta(days=cooldown_days)
            recent_denial = await MembershipApplicationRecord.objects.filter(
                applicant=discord_user.user,
                membership_type=selected,
                status=MembershipApplicationRecord.Status.DENIED,
                reviewed_at__gte=cutoff,
            ).order_by('-reviewed_at').afirst()
            if recent_denial:
                reapply_at = recent_denial.reviewed_at + timedelta(days=cooldown_days)
                reapply_ts = int(reapply_at.timestamp())
                await interaction.response.send_message(
                    f"❌ Your application for **{selected.name}** was recently denied. "
                    f"You may reapply <t:{reapply_ts}:R> (on <t:{reapply_ts}:D>).",
                    ephemeral=True,
                )
                return

        # Skip modal if no questions defined
        if not any(getattr(selected, f"question_{i}") for i in range(1, 6)):
            await interaction.response.defer(thinking=True, ephemeral=True)
            await create_membership_application(interaction, selected, ["", "", "", "", ""], rsi_verification)
        else:
            draft = await _load_draft(self.cog.redis_client, interaction.user.id, f"app:{selected.pk}")
            await interaction.response.send_modal(MembershipApplicationModal(selected, rsi_verification, rc=self.cog.redis_client, user_id=interaction.user.id, draft=draft))


# ---------------------------------------------------------------------------
# Entry View (persistent)
# ---------------------------------------------------------------------------

class ExternalCitizenModal(discord.ui.Modal, title="External Citizen Verification"):
    """Collect org info, create a private review thread, grant provisional access."""

    org_url = discord.ui.TextInput(
        label="RSI Organization URL",
        placeholder="https://robertsspaceindustries.com/orgs/YOURORG",
        required=True,
        max_length=200,
    )
    org_prefix = discord.ui.TextInput(
        label="Your Org Tag / Prefix",
        placeholder="e.g. [ACME]",
        required=True,
        max_length=20,
    )
    position = discord.ui.TextInput(
        label="Your Position / Rank in that Org",
        placeholder="e.g. Officer, Diplomat, Member",
        required=True,
        max_length=80,
    )
    reason = discord.ui.TextInput(
        label="Why are you visiting BlightVeil?",
        placeholder="Diplomatic contact, co-op ops, scouting, etc.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, cog: "MembershipApplicationCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Ensure backend user exists
        result = await sync_to_async(DiscordUser.ensure_with_user)(
            discorduid=interaction.user.id,
            access_token=None, refresh_token=None, access_token_expires=None,
        )
        du = result[0] if result else None

        review_chan_id = int(await aget_global_preference(ExternalReviewChannelID.import_path) or 0)
        if not review_chan_id:
            # Fall back to membership review channel
            review_chan_id = int(await aget_global_preference(MembershipReviewChannelID.import_path) or 0)

        channel = interaction.client.get_channel(review_chan_id)
        if not channel:
            try:
                channel = await interaction.client.fetch_channel(review_chan_id)
            except (discord.NotFound, discord.Forbidden):
                channel = None
        if not channel:
            await interaction.followup.send(
                "❌ The external review channel is not configured. Please contact staff.",
                ephemeral=True,
            )
            return

        thread_name = f"{interaction.user.display_name} - External [{self.org_prefix.value.strip()}]"[:100]

        logger.info(
            "ExternalCitizenModal: creating thread in channel %s (type=%s) for discord_id=%s",
            review_chan_id, type(channel).__name__, interaction.user.id,
        )

        if isinstance(channel, discord.ForumChannel):
            twm = await channel.create_thread(
                name=thread_name,
                content=f"External Citizen request by {interaction.user.mention}",
            )
            thread = twm.thread
        else:
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )

        logger.info("ExternalCitizenModal: thread created id=%s", thread.id)

        guild = channel.guild

        async def _add_member_to_thread(uid: int) -> bool:
            member_obj = guild.get_member(uid)
            if member_obj is None:
                try:
                    member_obj = await guild.fetch_member(uid)
                except (discord.NotFound, discord.Forbidden):
                    logger.warning("ExternalCitizenModal: member %s not found in guild, skipping add", uid)
                    return False
            try:
                await thread.add_user(member_obj)
                return True
            except discord.Forbidden:
                logger.warning("ExternalCitizenModal: Forbidden adding member %s to thread %s", uid, thread.id)
            except Exception as exc:
                logger.error("ExternalCitizenModal: add_user(%s) failed: %s", uid, exc)
            return False

        await asyncio.sleep(0.5)
        await _add_member_to_thread(interaction.user.id)

        # Add configured viewer groups
        viewer_csv = await aget_global_preference(ExternalThreadViewerGroups.import_path) or ""
        if viewer_csv:
            group_ids = [int(x.strip()) for x in viewer_csv.split(",") if x.strip().isdigit()]
            for gid in group_ids:
                group = await Group.objects.filter(pk=gid).afirst()
                if group:
                    async for user in group.user_set.all().prefetch_related("discorduser"):
                        if hasattr(user, "discorduser") and user.discorduser:
                            await _add_member_to_thread(user.discorduser.discorduid)
                            await asyncio.sleep(0.1)

        # Build embed
        member = guild.get_member(interaction.user.id)
        embed = discord.Embed(
            title="🌐 External Citizen Request",
            color=0x5865F2,
        )
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=False)
        if member:
            embed.add_field(
                name="Discord Profile",
                value=(
                    f"Account created: <t:{int(member.created_at.timestamp())}:R>\n"
                    f"Joined server: <t:{int(member.joined_at.timestamp())}:R>"
                ),
                inline=False,
            )
        embed.add_field(name="RSI Org URL", value=self.org_url.value.strip(), inline=False)
        embed.add_field(name="Org Tag / Prefix", value=f"`{self.org_prefix.value.strip()}`", inline=True)
        embed.add_field(name="Position / Rank", value=self.position.value.strip(), inline=True)
        embed.add_field(name="Reason for Visiting", value=self.reason.value.strip(), inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        review_view = ExternalCitizenReviewView(
            discord_id=interaction.user.id,
            cog=self.cog,
        )
        await thread.send(embed=embed, view=review_view)

        # Confirm to user
        await interaction.followup.send(
            "✅ Your request has been submitted! A staff member will review it shortly.\n"
            "You'll receive temporary access once approved.",
            ephemeral=True,
        )
        logger.info(
            "External citizen request submitted: discord_id=%s org=%s prefix=%s",
            interaction.user.id, self.org_url.value.strip(), self.org_prefix.value.strip(),
        )


class ExternalCitizenReviewView(discord.ui.View):
    """Staff buttons inside the external citizen thread."""

    def __init__(self, discord_id: int, cog: "MembershipApplicationCog"):
        super().__init__(timeout=None)
        self.discord_id = discord_id
        self.cog = cog

    def _get_discord_id(self, interaction: discord.Interaction) -> int | None:
        """Read the applicant's discord ID from the embed footer (survives restarts)."""
        if self.discord_id:
            return self.discord_id
        try:
            embed = interaction.message.embeds[0]
            footer = embed.footer.text or ""
            for part in footer.split("|"):
                part = part.strip()
                if part.startswith("User ID:"):
                    return int(part.split(":")[1].strip())
        except Exception:
            pass
        return None

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="ext_citizen_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        discord_id = self._get_discord_id(interaction)
        if not discord_id:
            await interaction.followup.send("❌ Could not determine applicant ID.", ephemeral=True)
            return

        rank_pk = int(await aget_global_preference(ExternalRankPK.import_path) or 0)
        if rank_pk:
            await self.cog._assign_rank(discord_id, rank_pk)

        groups_csv = await aget_global_preference(ExternalPermissionGroups.import_path) or ""
        if groups_csv:
            group_ids = [int(x.strip()) for x in groups_csv.split(",") if x.strip().isdigit()]
            try:
                du = await DiscordUser.objects.select_related("user").aget(discorduid=discord_id)
                groups = []
                for gid in group_ids:
                    g = await Group.objects.filter(pk=gid).afirst()
                    if g:
                        groups.append(g)
                if groups:
                    await du.user.groups.aadd(*groups)
            except Exception:
                logger.exception("External citizen approve: failed to add groups discord_id=%s", discord_id)

        await interaction.channel.send(
            f"✅ <@{discord_id}> — your External Citizen access has been **approved**! "
            "Welcome to BlightVeil. This thread will remain open for any questions."
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.followup.send("Approved and access granted.", ephemeral=True)
        logger.info("External citizen approved discord_id=%s by %s", discord_id, interaction.user)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="ext_citizen_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        discord_id = self._get_discord_id(interaction)
        mention = f"<@{discord_id}>" if discord_id else "the applicant"
        await interaction.channel.send(
            f"❌ {mention} — your External Citizen request has been **denied**. "
            "If you have questions, please contact a staff member."
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        try:
            await interaction.channel.edit(archived=True, locked=True)
        except discord.Forbidden:
            pass
        await interaction.followup.send("Request denied and thread archived.", ephemeral=True)
        logger.info("External citizen denied discord_id=%s by %s", discord_id, interaction.user)


async def _aiter(iterable):
    for item in iterable:
        yield item


class MembershipEntryView(discord.ui.View):
    """Persistent view with two buttons: Start Verification and Become a Visitor."""

    def __init__(self, cog: "MembershipApplicationCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="✅ Start Verification",
        style=discord.ButtonStyle.primary,
        custom_id="candidacy_persistent_membership_verify_btn",
    )
    async def start_verification(self, interaction: discord.Interaction, button: discord.ui.Button):
        visitor_rank_pk = await aget_global_preference(MembershipVisitorRankPK.import_path)
        if visitor_rank_pk:
            await self.cog._assign_rank(interaction.user.id, visitor_rank_pk)

        items = []
        async for mt in MembershipApplicationType.objects.filter(is_active=True):
            items.append(mt)

        if not items:
            await interaction.response.send_message(
                "There are currently no open membership paths. Please check back later.", ephemeral=True
            )
            return

        rules_enabled = await aget_global_preference(MembershipRulesEnabled.import_path)
        rules_obj = await MembershipRules.aget_instance()
        rules_text = rules_obj.rules_text

        if rules_enabled and rules_text and rules_text.strip():
            rules_hash = hashlib.sha256(rules_text.encode()).hexdigest()

            # Check if this user already agreed to this exact version of the rules
            prior = await RulesAgreement.objects.filter(
                discord_id=interaction.user.id,
                rules_hash=rules_hash,
            ).afirst()

            if prior:
                # Already agreed — skip straight to membership selection
                select_view = discord.ui.View(timeout=300)
                select_view.add_item(MembershipTypeSelect(self.cog, items))
                await interaction.response.send_message(
                    "Welcome back! Please select the membership path you want to apply for:",
                    view=select_view,
                    ephemeral=True,
                )
                return

            raw_pages = [p.strip() for p in rules_text.split("\n---\n") if p.strip()]
            if not raw_pages:
                raw_pages = [rules_text.strip()]
            # Discord embed description cap is 4096 chars — split any overlong page at a newline
            pages: list[str] = []
            for raw in raw_pages:
                while len(raw) > 4096:
                    split_at = raw.rfind("\n", 0, 4096)
                    if split_at == -1:
                        split_at = 4096
                    pages.append(raw[:split_at].strip())
                    raw = raw[split_at:].strip()
                if raw:
                    pages.append(raw)
            view = RulesView(self.cog, items, pages, rules_hash)
            await interaction.response.send_message(embed=view._embed(), view=view, ephemeral=True)
        else:
            view = discord.ui.View(timeout=300)
            view.add_item(MembershipTypeSelect(self.cog, items))
            await interaction.response.send_message(
                "Welcome! Please select the membership path you want to apply for:",
                view=view,
                ephemeral=True,
            )

    @discord.ui.button(
        label="🚪 Become a Visitor",
        style=discord.ButtonStyle.secondary,
        custom_id="candidacy_persistent_membership_visitor_btn",
    )
    async def become_visitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Directly grant visitor rank and groups, no RSI verification, no application."""
        await interaction.response.defer(ephemeral=True)

        visitor_rank_pk = await aget_global_preference(MembershipVisitorRankPK.import_path)
        if visitor_rank_pk:
            await self.cog._assign_rank(interaction.user.id, visitor_rank_pk)

        groups_csv = await aget_global_preference(MembershipVisitorPermissionGroups.import_path)
        if groups_csv:
            group_ids = [int(x.strip()) for x in groups_csv.split(",") if x.strip().isdigit()]
            if group_ids:
                try:
                    du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
                    groups = []
                    for gid in group_ids:
                        group = await Group.objects.filter(pk=gid).afirst()
                        if group:
                            groups.append(group)
                    if groups:
                        await du.user.groups.aadd(*groups)
                        self.cog.logger.info("Added groups %s to visitor %s", [g.name for g in groups], du.user)
                except DiscordUser.DoesNotExist:
                    await interaction.followup.send(
                        "Your Discord account is not yet linked. Please link it first using `/link`.",
                        ephemeral=True
                    )
                    return
                except Exception as e:
                    self.cog.logger.error("Failed to add visitor groups: %s", e)

        embed = discord.Embed(
            title="✅ Visitor Status Granted!",
            description=(
                "You have been granted visitor access.\n\n"
                "You can now explore the server. If you later wish to apply for full membership, "
                "use the **Start Verification** button above."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="🌐 External Citizen",
        style=discord.ButtonStyle.secondary,
        custom_id="candidacy_persistent_membership_external_btn",
    )
    async def become_external(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open the External Citizen modal to collect org info and create a review thread."""
        await interaction.response.send_modal(ExternalCitizenModal(self.cog))


# ---------------------------------------------------------------------------
# Transient select views (spawned from the review buttons)
# ---------------------------------------------------------------------------

class _PromoteRankSelect(discord.ui.Select):
    def __init__(self, parent: "PromoteSelectView"):
        self._promote_view = parent
        options = [
            discord.SelectOption(label=label[:100], value=str(pk))
            for label, pk in parent.options_data
        ]
        super().__init__(
            placeholder="Choose promotion rank…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        pk = int(self.values[0])
        label = next(lbl for lbl, p in self._promote_view.options_data if p == pk)
        await self._promote_view.do_promote(interaction, pk, label)


class PromoteSelectView(discord.ui.View):
    """Transient view — asks the promoter to pick Trial or Legion."""

    def __init__(
        self,
        review_view: "MembershipReviewView",
        review_message: discord.Message,
        app_record: MembershipApplicationRecord,
        promoter,
        options_data: list[tuple[str, int]],
    ):
        super().__init__(timeout=300)
        self.review_view = review_view
        self.review_message = review_message
        self.app_record = app_record
        self.promoter = promoter
        self.options_data = options_data
        self.add_item(_PromoteRankSelect(self))

    async def do_promote(self, interaction: discord.Interaction, rank_pk: int, label: str):
        await interaction.response.defer(ephemeral=True)

        if self.app_record.promoted_at is not None:
            await interaction.followup.send("❌ This member has already been promoted.", ephemeral=True)
            return

        discord_uid = self.app_record.applicant.discorduser.discorduid
        await _assign_rank_to_user(discord_uid, rank_pk, guild=interaction.guild)

        self.app_record.promoted_by = self.promoter
        self.app_record.promoted_at = now()
        await self.app_record.asave(update_fields=["promoted_by", "promoted_at"])

        # Post configurable promotion embed to the welcome channel
        mt = self.app_record.membership_type
        if mt is not None:
            if mt.on_promote_trial_rank_pk and rank_pk == mt.on_promote_trial_rank_pk:
                event = MembershipEmbedMessage.Event.PROMOTE_TRIAL
            else:
                event = MembershipEmbedMessage.Event.PROMOTE_LEGION
            await _post_membership_embed(interaction.client, self.app_record, event, rank_pk)

        for c in self.review_view.children:
            if isinstance(c, discord.ui.Button) and c.custom_id == "candidacy_membership_review_promote":
                c.disabled = True

        try:
            embed = self.review_message.embeds[0]
            embed.add_field(
                name="Promoted To",
                value=f"**{label}** by {interaction.user.mention}",
                inline=False,
            )
            await self.review_message.edit(embed=embed, view=self.review_view)
        except Exception:
            logger.exception("Failed to update review embed after promotion")

        display_name = await sync_to_async(lambda: self.app_record.applicant.display_name)()
        await interaction.followup.send(
            f"✅ {display_name} promoted to **{label}**.",
            ephemeral=True,
        )


class _EvaluationGroupSelect(discord.ui.Select):
    def __init__(self, applicant, groups: list[Group]):
        self.applicant = applicant
        self.groups_map = {str(g.pk): g for g in groups}
        options = [discord.SelectOption(label=g.name[:100], value=str(g.pk)) for g in groups]
        super().__init__(
            placeholder="Select evaluation group(s) to grant…",
            options=options,
            min_values=1,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected = [self.groups_map[v] for v in self.values]
        await sync_to_async(self.applicant.groups.add)(*selected)
        names = ", ".join(f"`{g.name}`" for g in selected)
        await interaction.followup.send(f"✅ Granted evaluation group(s): {names}", ephemeral=True)


class EvaluationSelectView(discord.ui.View):
    def __init__(self, applicant, groups: list[Group]):
        super().__init__(timeout=300)
        self.add_item(_EvaluationGroupSelect(applicant, groups))


class _PrimaryFocusSelect(discord.ui.Select):
    def __init__(self, app_record_id: int, disciplines: list[AssignableDiscipline]):
        self.app_record_id = app_record_id
        self.map = {str(d.id): d for d in disciplines}
        options = [
            discord.SelectOption(
                label=d.name[:100],
                description=(d.description[:97] + "…") if len(d.description) > 97 else (d.description or None),
                value=str(d.id),
            )
            for d in disciplines
        ]
        super().__init__(
            placeholder="Confirm applicant's primary focus…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discipline = self.map[self.values[0]]
        app_record = await MembershipApplicationRecord.objects.select_related(
            "applicant", "applicant__discorduser"
        ).aget(id=self.app_record_id)
        app_record.primary_focus = discipline
        await app_record.asave(update_fields=["primary_focus"])
        groups = [g async for g in discipline.permission_groups.all()]
        if groups:
            await sync_to_async(app_record.applicant.groups.add)(*groups)
        await interaction.followup.send(
            f"✅ Primary focus set to **{discipline.name}**"
            + (f" (granted {len(groups)} group(s))." if groups else "."),
            ephemeral=True,
        )


class PrimaryFocusSelectView(discord.ui.View):
    def __init__(self, app_record_id: int, disciplines: list[AssignableDiscipline]):
        super().__init__(timeout=600)
        self.add_item(_PrimaryFocusSelect(app_record_id, disciplines))


# ---------------------------------------------------------------------------
# Review View (with transcript and locking)
# ---------------------------------------------------------------------------

class MembershipReviewView(discord.ui.View):
    def __init__(self, application_id: int, thread_id: int):
        super().__init__(timeout=None)
        self.application_id = application_id
        self.thread_id = thread_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="candidacy_membership_review_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_review(interaction, MembershipApplicationRecord.Status.APPROVED)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="candidacy_membership_review_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_review(interaction, MembershipApplicationRecord.Status.DENIED)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, custom_id="candidacy_membership_review_reset")
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_reset(interaction)

    @discord.ui.button(label="Grant Evaluation", style=discord.ButtonStyle.secondary, custom_id="candidacy_membership_review_evaluate")
    async def evaluate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_evaluate(interaction)

    @discord.ui.button(label="Promote", style=discord.ButtonStyle.primary, custom_id="candidacy_membership_review_promote", disabled=True)
    async def promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_promote(interaction, button)

    def _resolve_ids(self, interaction: discord.Interaction):
        """Recover application_id/thread_id from the embed footer after a bot restart."""
        if self.application_id and self.thread_id:
            return True
        try:
            footer_text = interaction.message.embeds[0].footer.text or ""
            parts = dict(p.split(":", 1) for p in footer_text.split("|") if ":" in p)
            self.application_id = int(parts["app"])
            self.thread_id = int(parts["thread"])
            return True
        except Exception:
            return False

    async def _handle_reset(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.message.embeds:
            await interaction.followup.send("❌ Review message has no embed — cannot identify application.", ephemeral=True)
            return
        if not self._resolve_ids(interaction):
            await interaction.followup.send("❌ Could not identify the application. Please contact staff.", ephemeral=True)
            return

        app_record = await MembershipApplicationRecord.objects.select_related(
            "membership_type", "applicant__discorduser"
        ).aget(id=self.application_id)

        try:
            await _verify_and_cache_permission(
                interaction.user.id,
                "candidacy.can_review_membershipapplication",
                app_record.membership_type,
            )
        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        try:
            reviewer_du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            reviewer = reviewer_du.user
        except DiscordUser.DoesNotExist:
            await interaction.followup.send("Your Discord account must be linked.", ephemeral=True)
            return

        app_record.status = MembershipApplicationRecord.Status.RESET
        app_record.reviewer = reviewer
        app_record.reviewed_at = now()
        await app_record.asave()

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.light_grey()
        embed.add_field(name="Status", value="Reset", inline=False)
        embed.add_field(name="Reset By", value=interaction.user.mention, inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=embed, view=self)

        thread = await self._get_thread(self.thread_id, interaction.client)
        await thread.send(
            f"⏮️ Application reset by {interaction.user.mention}. "
            "You may re-apply at any time. This thread will be archived."
        )
        await thread.edit(locked=True, archived=True)

        await interaction.followup.send("Application reset and thread archived.", ephemeral=True)

    async def _handle_review(self, interaction: discord.Interaction, status):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.message.embeds:
            await interaction.followup.send("❌ Review message has no embed — cannot identify application.", ephemeral=True)
            return
        if not self._resolve_ids(interaction):
            await interaction.followup.send("❌ Could not identify the application. Please contact staff.", ephemeral=True)
            return

        app_record = await MembershipApplicationRecord.objects.select_related(
            "membership_type", "applicant__discorduser"
        ).prefetch_related(
            "membership_type__on_accept_gives_permission_groups",
            "membership_type__evaluation_permission_groups",
        ).aget(id=self.application_id)

        # Permission check
        try:
            await _verify_and_cache_permission(
                interaction.user.id,
                "candidacy.can_review_membershipapplication",
                app_record.membership_type,
            )
        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        # Resolve reviewer backend user
        try:
            reviewer_du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            reviewer = reviewer_du.user
        except DiscordUser.DoesNotExist:
            await interaction.followup.send("Your Discord account must be linked to review applications.", ephemeral=True)
            return

        # Update record
        app_record.status = status
        app_record.reviewer = reviewer
        app_record.reviewed_at = now()
        await app_record.asave()

        # Update the review channel message
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green() if status == MembershipApplicationRecord.Status.APPROVED else discord.Color.red()
        embed.add_field(name="Status", value=status.label, inline=False)
        embed.add_field(name="Reviewed By", value=interaction.user.mention, inline=False)
        mt = app_record.membership_type
        assert mt is not None
        is_approved = status == MembershipApplicationRecord.Status.APPROVED
        has_promote = bool(mt.on_promote_rank_pk) or bool(mt.on_promote_trial_rank_pk)
        has_eval = await mt.evaluation_permission_groups.aexists()
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "candidacy_membership_review_promote":
                child.disabled = not (is_approved and has_promote)
            elif child.custom_id == "candidacy_membership_review_evaluate":
                child.disabled = not (is_approved and has_eval)
            else:
                child.disabled = True
        if interaction.message:
            await interaction.message.edit(embed=embed, view=self)

        # Get the thread
        thread = await self._get_thread(self.thread_id, interaction.client)

        # Post approval/denial message in public application channel
        if status == MembershipApplicationRecord.Status.APPROVED:
            discord_uid = app_record.applicant.discorduser.discorduid
            if mt.on_accept_trial_rank_pk:
                await _assign_rank_to_user(discord_uid, mt.on_accept_trial_rank_pk, guild=interaction.guild)
            groups = [g async for g in mt.on_accept_gives_permission_groups.all()]
            if groups:
                await sync_to_async(app_record.applicant.groups.add)(*groups)

            await self._post_approval_message(interaction.client, app_record)
            posted_embed = await _post_membership_embed(
                interaction.client,
                app_record,
                MembershipEmbedMessage.Event.ACCEPT,
                mt.on_accept_trial_rank_pk,
            )
            if not posted_embed:
                await self._post_welcome_message(interaction.client, app_record)
            # Send a message in thread about auto-archive
            auto_archive_hours = await aget_global_preference(MembershipAutoArchiveHours.import_path)
            await thread.send(
                f"✅ Application approved! This thread will be automatically locked and archived in **{auto_archive_hours} hours**."
            )
        else:
            await self._post_denial_message(interaction.client, app_record)
            await thread.send(f"❌ Application denied by {interaction.user.mention}.")
            # Lock and archive immediately for denied applications
            await thread.edit(locked=True, archived=True)

        await self._send_transcript(thread, interaction.client, app_record)
        await interaction.followup.send(f"Application {status.label} and thread locked.", ephemeral=True)

        # Prompt approver to confirm primary focus (disciplines) on approval
        if is_approved:
            disciplines = [
                d async for d in AssignableDiscipline.objects.filter(is_active=True).prefetch_related("permission_groups")
            ]
            if disciplines:
                view = PrimaryFocusSelectView(app_record.id, disciplines)
                await interaction.followup.send(
                    "Please confirm the applicant's **Primary Focus**:",
                    view=view,
                    ephemeral=True,
                )

    async def _get_thread(self, thread_id: int, client: commands.Bot) -> discord.Thread:
        thread = client.get_channel(thread_id)
        if not thread:
            thread = await client.fetch_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            raise ValueError(f"Channel {thread_id} is not a thread")
        return thread

    async def _handle_promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not self._resolve_ids(interaction):
            await interaction.followup.send("❌ Could not identify the application.", ephemeral=True)
            return

        app_record = await MembershipApplicationRecord.objects.select_related(
            "membership_type", "applicant__discorduser", "applicant"
        ).aget(id=self.application_id)

        if app_record.status != MembershipApplicationRecord.Status.APPROVED:
            await interaction.followup.send("❌ Can only promote an approved applicant.", ephemeral=True)
            return

        if app_record.promoted_at is not None:
            await interaction.followup.send("❌ This member has already been promoted.", ephemeral=True)
            return

        mt = app_record.membership_type
        if mt is None or (not mt.on_promote_rank_pk and not mt.on_promote_trial_rank_pk):
            await interaction.followup.send("❌ No promote ranks are configured for this membership type.", ephemeral=True)
            return

        # Permission check — same permission as approve/deny
        try:
            await _verify_and_cache_permission(
                interaction.user.id,
                "candidacy.can_review_membershipapplication",
                mt,
            )
        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        # Resolve promoter
        try:
            promoter_du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            promoter = promoter_du.user
        except DiscordUser.DoesNotExist:
            await interaction.followup.send("Your Discord account must be linked to promote members.", ephemeral=True)
            return

        # Build Trial/Legion options based on configured ranks
        from app.unifieduser.models import OrgRank
        options_data: list[tuple[str, int]] = []
        if mt.on_promote_trial_rank_pk:
            r = await OrgRank.objects.filter(pk=mt.on_promote_trial_rank_pk).afirst()
            rank_name = f"{r.prefix} {r.name}".strip() if r else str(mt.on_promote_trial_rank_pk)
            options_data.append((f"Trial — {rank_name}", mt.on_promote_trial_rank_pk))
        if mt.on_promote_rank_pk:
            r = await OrgRank.objects.filter(pk=mt.on_promote_rank_pk).afirst()
            rank_name = f"{r.prefix} {r.name}".strip() if r else str(mt.on_promote_rank_pk)
            options_data.append((f"Legion — {rank_name}", mt.on_promote_rank_pk))

        review_message = interaction.message
        view = PromoteSelectView(self, review_message, app_record, promoter, options_data)
        display_name = await sync_to_async(lambda: app_record.applicant.display_name)()
        await interaction.followup.send(
            f"Promote **{display_name}** — choose target:",
            view=view,
            ephemeral=True,
        )

    async def _handle_evaluate(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not self._resolve_ids(interaction):
            await interaction.followup.send("❌ Could not identify the application.", ephemeral=True)
            return

        app_record = await MembershipApplicationRecord.objects.select_related(
            "membership_type", "applicant", "applicant__discorduser"
        ).prefetch_related("membership_type__evaluation_permission_groups").aget(id=self.application_id)

        mt = app_record.membership_type
        if mt is None:
            await interaction.followup.send("❌ Membership type missing.", ephemeral=True)
            return

        try:
            await _verify_and_cache_permission(
                interaction.user.id,
                "candidacy.can_review_membershipapplication",
                mt,
            )
        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        if app_record.status in (
            MembershipApplicationRecord.Status.DENIED,
            MembershipApplicationRecord.Status.RESET,
        ):
            await interaction.followup.send(
                "❌ Cannot grant evaluation groups on a denied or reset application.",
                ephemeral=True,
            )
            return

        groups = [g async for g in mt.evaluation_permission_groups.all()]
        if not groups:
            await interaction.followup.send(
                "❌ No evaluation groups configured for this membership type. "
                "Set `evaluation_permission_groups` in Django admin.",
                ephemeral=True,
            )
            return

        view = EvaluationSelectView(app_record.applicant, groups)
        display_name = await sync_to_async(lambda: app_record.applicant.display_name)()
        await interaction.followup.send(
            f"Grant evaluation access to **{display_name}**:",
            view=view,
            ephemeral=True,
        )

    async def _send_transcript(self, thread: discord.Thread, client: discord.Client, app_record: MembershipApplicationRecord):
        transcript_chan_id = await aget_global_preference(MembershipTranscriptChannelID.import_path)
        if not transcript_chan_id:
            return
        transcript_channel = client.get_channel(transcript_chan_id)
        if not transcript_channel:
            return

        # Fetch all messages
        messages = []
        async for message in thread.history(limit=None, oldest_first=True):
            messages.append(message)

        # Build transcript text
        transcript = f"# Transcript for application {app_record.id} – {app_record.membership_type.name}\n"
        transcript += f"Applicant: {app_record.applicant}\n"
        transcript += f"Status: {app_record.status}\n"
        transcript += f"Thread: {thread.mention}\n\n"
        for msg in messages:
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            transcript += f"[{timestamp}] **{msg.author.display_name}**: {msg.clean_content}\n"
            if msg.attachments:
                transcript += f"  Attachments: {', '.join(a.url for a in msg.attachments)}\n"

        # Split into chunks (Discord message limit 2000 chars)
        chunks = [transcript[i:i+1900] for i in range(0, len(transcript), 1900)]
        for chunk in chunks:
            await transcript_channel.send(chunk)

    async def _post_approval_message(self, client: commands.Bot, app_record: MembershipApplicationRecord):
        app_chan_id = await aget_global_preference(MembershipApplicationChannelID.import_path)
        if not app_chan_id:
            return
        app_channel = client.get_channel(app_chan_id)
        if not app_channel:
            return

        msg_template = await aget_global_preference(MembershipApprovalMessage.import_path)
        rank = await app_record.membership_type.get_rank()
        rank_name = rank.name if rank else "Member"
        applicant_mention = f"<@{app_record.applicant.discorduser.discorduid}>" if hasattr(app_record.applicant, "discorduser") else str(app_record.applicant)

        msg = msg_template.format(
            applicant=applicant_mention,
            rank_name=rank_name,
        )
        await app_channel.send(msg)

    async def _post_welcome_message(self, client: commands.Bot, app_record: MembershipApplicationRecord):
        welcome_chan_id = await aget_global_preference(MembershipWelcomeChannelID.import_path)
        if not welcome_chan_id:
            return
        welcome_channel = client.get_channel(welcome_chan_id)
        if not welcome_channel:
            return

        per_type_msg = app_record.membership_type.welcome_message if app_record.membership_type else ""
        msg_template = per_type_msg or await aget_global_preference(MembershipWelcomeMessage.import_path)
        rank = await app_record.membership_type.get_rank()
        rank_name = rank.name if rank else "Member"
        rank_prefix = rank.prefix if rank else ""
        applicant_mention = f"<@{app_record.applicant.discorduser.discorduid}>" if hasattr(app_record.applicant, "discorduser") else str(app_record.applicant)
        applicant_name = await sync_to_async(lambda: app_record.applicant.display_name)()

        discipline_chan_id = await aget_global_preference(MembershipDisciplineSelectionChannelID.import_path)
        discipline_channel_mention = f"<#{discipline_chan_id}>" if discipline_chan_id else "⁠📜discipline-selection📜"

        msg = msg_template.format(
            applicant=applicant_mention,
            applicant_name=applicant_name,
            rank_name=rank_name,
            rank_prefix=rank_prefix,
            discipline_channel=discipline_channel_mention,
        )

        welcome_file = None
        image_enabled = await aget_global_preference(MembershipInductionImageEnabled.import_path)
        if image_enabled and isinstance(welcome_channel, discord.abc.GuildChannel):
            discord_uid = getattr(getattr(app_record.applicant, 'discorduser', None), 'discorduid', None)
            if discord_uid:
                try:
                    member = welcome_channel.guild.get_member(discord_uid) or await welcome_channel.guild.fetch_member(discord_uid)
                    custom_bg = await aget_global_preference(MembershipInductionImagePath.import_path)
                    card_kwargs = {"background_path": custom_bg} if custom_bg else {}
                    welcome_file = await img_util.create_welcome_card(member, **card_kwargs)
                except Exception:
                    logger.warning("Could not generate welcome card for discord_uid=%s", discord_uid)

        if welcome_file:
            await welcome_channel.send(msg, file=welcome_file)
        else:
            await welcome_channel.send(msg)

    async def _post_denial_message(self, client: commands.Bot, app_record: MembershipApplicationRecord):
        app_chan_id = await aget_global_preference(MembershipApplicationChannelID.import_path)
        if not app_chan_id:
            return
        app_channel = client.get_channel(app_chan_id)
        if not app_channel:
            return

        msg_template = await aget_global_preference(MembershipDenialMessage.import_path)
        applicant_mention = f"<@{app_record.applicant.discorduser.discorduid}>" if hasattr(app_record.applicant, "discorduser") else str(app_record.applicant)
        msg = msg_template.format(applicant=applicant_mention)
        await app_channel.send(msg)


# ---------------------------------------------------------------------------
# Main Cog
# ---------------------------------------------------------------------------

def _render_membership_embed(msg: MembershipEmbedMessage, context: dict) -> tuple[str, discord.Embed]:
    """Render content + embed from a MembershipEmbedMessage, with placeholder substitution."""
    def fmt(s: str) -> str:
        return s.format_map(SafeDict(**context)) if s else ""

    content = fmt(msg.content)
    embed = discord.Embed(
        title=fmt(msg.title) or None,
        description=fmt(msg.description) or None,
    )
    try:
        hex_val = (msg.color_hex or "").lstrip("#")
        embed.color = discord.Color(int(hex_val, 16)) if hex_val else discord.Color.blurple()
    except Exception:
        embed.color = discord.Color.blurple()

    if msg.image_url:
        embed.set_image(url=msg.image_url)
    if msg.thumbnail_url:
        embed.set_thumbnail(url=msg.thumbnail_url)
    if msg.author_name:
        embed.set_author(name=fmt(msg.author_name), icon_url=msg.author_icon_url or None)
    if msg.footer_text:
        embed.set_footer(text=fmt(msg.footer_text), icon_url=msg.footer_icon_url or None)
    return content, embed


async def _post_membership_embed(
    client: commands.Bot,
    app_record: MembershipApplicationRecord,
    event: str,
    rank_pk: int | None = None,
) -> bool:
    """
    Look up the configured embed for (type, event), render it, and post it to the welcome channel.
    Returns True if a message was posted.
    """
    msg = await MembershipEmbedMessage.objects.filter(
        membership_type=app_record.membership_type,
        event=event,
        enabled=True,
    ).afirst()
    if not msg:
        return False

    welcome_chan_id = await aget_global_preference(MembershipWelcomeChannelID.import_path)
    if not welcome_chan_id:
        return False
    welcome_channel = client.get_channel(welcome_chan_id)
    if not welcome_channel:
        return False

    discord_uid = getattr(getattr(app_record.applicant, "discorduser", None), "discorduid", None)
    applicant_mention = f"<@{discord_uid}>" if discord_uid else str(app_record.applicant)
    applicant_name = await sync_to_async(lambda: app_record.applicant.display_name)()

    from app.unifieduser.models import OrgRank
    rank = None
    if rank_pk:
        rank = await OrgRank.objects.filter(pk=rank_pk).afirst()
    if rank is None:
        rank = await app_record.membership_type.get_rank()
    rank_name = rank.name if rank else "Member"
    rank_prefix = rank.prefix if rank else ""

    discipline_chan_id = await aget_global_preference(MembershipDisciplineSelectionChannelID.import_path)
    discipline_channel = f"<#{discipline_chan_id}>" if discipline_chan_id else "⁠📜discipline-selection📜"

    context = {
        "applicant": applicant_mention,
        "applicant_name": applicant_name,
        "rank_name": rank_name,
        "rank_prefix": rank_prefix,
        "discipline_channel": discipline_channel,
    }

    content, embed = _render_membership_embed(msg, context)

    welcome_file = None
    if msg.include_welcome_card and isinstance(welcome_channel, discord.abc.GuildChannel) and discord_uid:
        try:
            member = welcome_channel.guild.get_member(discord_uid) or await welcome_channel.guild.fetch_member(discord_uid)
            custom_bg = await aget_global_preference(MembershipInductionImagePath.import_path)
            card_kwargs = {"background_path": custom_bg} if custom_bg else {}
            welcome_file = await img_util.create_welcome_card(member, **card_kwargs)
            if welcome_file:
                embed.set_image(url=f"attachment://{welcome_file.filename}")
        except Exception:
            logger.warning("Could not generate welcome card for discord_uid=%s", discord_uid)

    try:
        kwargs = {"embed": embed}
        if content:
            kwargs["content"] = content
        if welcome_file:
            kwargs["file"] = welcome_file
        await welcome_channel.send(**kwargs)
        return True
    except Exception:
        logger.exception("Failed to post membership embed event=%s", event)
        return False


async def _assign_rank_to_user(discord_id: int, rank_pk: int, guild: discord.Guild = None):
    """Assign an OrgRank to a user by discord ID. Shared by cog and review view."""
    from app.unifieduser.models import OrgRank
    from app.unifieduser.preferences import CustomDisplayName, DisplayNameSource
    try:
        rank = await OrgRank.objects.aget(pk=rank_pk)
        result = await sync_to_async(DiscordUser.ensure_with_user)(
            discorduid=discord_id, access_token=None, refresh_token=None, access_token_expires=None
        )
        du = result[0]
        if du is None:
            logger.error("_assign_rank_to_user: no DiscordUser for discord_id=%s", discord_id)
            return
        old_rank_pk = du.user.rank_id
        du.user.rank = rank
        await du.user.asave(update_fields=["rank"])
        logger.info("Assigned rank %s (pk=%s) to discord_id=%s", rank.name, rank_pk, discord_id)

        # Seed CustomDisplayName + DisplayNameSource=4 if not already configured,
        # so sync_display_name can apply the rank prefix to the Discord nick.
        cdn = await du.user.aget_setting(CustomDisplayName.import_path)
        if not cdn.value or cdn.value == CustomDisplayName.default_value:
            # Prefer guild nick > global display name > username
            nick_name = None
            if guild is not None:
                try:
                    gm = guild.get_member(discord_id) or await guild.fetch_member(discord_id)
                    nick_name = gm.nick or gm.global_name or gm.name
                except Exception:
                    pass
            if not nick_name:
                nick_name = getattr(du, 'main_guild_nick', None) or du.user.username
            cdn.value = nick_name
            await cdn.asave()
            logger.info("Seeded CustomDisplayName='%s' for discord_id=%s", nick_name, discord_id)

        dn_src = await du.user.aget_setting(DisplayNameSource.import_path)
        if dn_src.value != 4:
            dn_src.value = 4
            await dn_src.asave()

        # Publish so usermanager fires sync_display_name with the new rank prefix
        from app.unifieduser.signals import publish
        publish({
            "type": "unifieduser.OrgRank",
            "action": "User.update",
            "id": str(du.user.pk),
            "rank_before_id": str(old_rank_pk) if old_rank_pk else None,
            "rank_after_id": str(rank.pk),
        })
    except OrgRank.DoesNotExist:
        logger.error("OrgRank pk=%s not found", rank_pk)
    except Exception:
        logger.exception("_assign_rank_to_user error discord_id=%s rank_pk=%s", discord_id, rank_pk)


class MembershipApplicationCog(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        # Note: ReviewView is added with placeholder IDs; actual views will be created per application
        self.client.add_view(MembershipEntryView(self))
        self.client.add_view(MembershipReviewView(application_id=0, thread_id=0))
        self.client.add_view(ExternalCitizenReviewView(discord_id=0, cog=self))
        self.logger = get_task_logger(__name__)
        self.User = get_user_model()
        self.redis_client = None
        self.pubsub = None
        self.verification_chan_id = None
        self.rsi_check_tasks: dict[int, asyncio.Task] = {}

        self.startup_task.start()
        self.auto_archive_task.start()
        self.cleanup_stale_applications_task.start()
        self.follow_up_reminder_task.start()
        self.discipline_reminder_task.start()

    def cog_unload(self):
        self.startup_task.cancel()
        self.sync_listener_loop.cancel()
        self.auto_archive_task.cancel()
        self.cleanup_stale_applications_task.cancel()
        self.follow_up_reminder_task.cancel()
        self.discipline_reminder_task.cancel()
        for task in self.rsi_check_tasks.values():
            task.cancel()
        try:
            if self.pubsub:
                asyncio.create_task(self.pubsub.unsubscribe())
        except Exception:
            pass
        try:
            if self.redis_client:
                asyncio.create_task(self.redis_client.aclose())
        except Exception:
            pass

    @tasks.loop(count=1)
    async def startup_task(self):
        redis_url = settings.CACHES["default"]["LOCATION"]
        pool = redis.ConnectionPool.from_url(redis_url)
        self.redis_client = redis.Redis.from_pool(pool)
        self.pubsub = self.redis_client.pubsub()

        self.sync_listener_loop.start()
        await self.initialize_portal()
        await self.full_sync()

    @startup_task.before_loop
    async def before_startup(self):
        await self.client.wait_until_ready()

    # -----------------------------------------------------------------------
    # Portal setup
    # -----------------------------------------------------------------------

    async def initialize_portal(self):
        self.verification_chan_id = await aget_global_preference(MembershipVerificationChannelID.import_path)

        if not self.verification_chan_id:
            self.logger.error(
                "MembershipVerificationChannelID preference is not set (value=0). "
                "Go to Django admin → Preferences → Global Settings and set MembershipVerificationChannelID "
                "to the Discord channel ID where the portal button should be posted. "
                "The bot will post the embed automatically on next restart once it is set. "
                "(import_path used: %s)",
                MembershipVerificationChannelID.import_path,
            )
            return

        channel = self.client.get_channel(self.verification_chan_id)
        if not channel:
            self.logger.error(
                "Membership verification channel %s not found in Discord. "
                "Check that the bot has access to that channel and the ID is correct.",
                self.verification_chan_id,
            )
            return

        # Clear old bot portal messages
        async for message in channel.history(limit=20):
            if message.author == self.client.user:
                try:
                    if message.embeds and "Verification" in (message.embeds[0].title or ""):
                        await message.delete()
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

        embed = discord.Embed(
            title="Membership Verification",
            description=(
                "**Start Verification** – Go through the full membership application process.\n"
                "**Become a Visitor** – Instantly get visitor access without verification."
            ),
            color=discord.Color.purple(),
        )

        async for mt in MembershipApplicationType.objects.filter(is_active=True):
            embed.add_field(name=mt.name, value=mt.description or "—", inline=False)

        await channel.send(embed=embed, view=MembershipEntryView(self))
        self.logger.info("Membership portal posted in channel %s", self.verification_chan_id)

    # -----------------------------------------------------------------------
    # Full sync
    # -----------------------------------------------------------------------

    async def full_sync(self):
        self.logger.info("Starting full sync of membership application threads…")

        @sync_to_async
        def get_sync_data():
            pending = list(
                MembershipApplicationRecord.objects.filter(
                    status=MembershipApplicationRecord.Status.PENDING
                ).select_related("membership_type", "applicant__discorduser")
            )
            result = []
            for app in pending:
                reviewers_qs = get_users_with_permission_on(
                    "candidacy.can_review_membershipapplication", app.membership_type
                )
                reviewers = list(reviewers_qs)   # Evaluate union queryset
                reviewer_ids = {
                    user.discorduser.discorduid
                    for user in reviewers
                    if hasattr(user, 'discorduser') and user.discorduser
                }
                applicant_id = None
                if hasattr(app.applicant, "discorduser"):
                    applicant_id = app.applicant.discorduser.discorduid
                result.append({
                    "thread_id": app.thread_id,
                    "reviewer_ids": reviewer_ids,
                    "applicant_id": applicant_id,
                })
            return result

        sync_data = await get_sync_data()

        for data in sync_data:
            try:
                thread = await self._get_thread(data["thread_id"])
            except Exception:
                continue

            expected = data["reviewer_ids"] | ({data["applicant_id"]} if data["applicant_id"] else set())
            current = {tm.id for tm in await thread.fetch_members()}
            to_add = expected - current
            to_remove = (current - expected) - {self.client.user.id}

            for uid in to_add:
                if member := await self._get_member(uid):
                    try:
                        await self._add_to_thread(member, thread)
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        self.logger.error("Full sync: add %s to %s failed: %s", uid, thread.name, e)

            for uid in to_remove:
                if member := await self._get_member(uid):
                    try:
                        await self._remove_from_thread(member, thread)
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        self.logger.error("Full sync: remove %s from %s failed: %s", uid, thread.name, e)

        self.logger.info("Completed full sync of membership application threads.")

    # -----------------------------------------------------------------------
    # Redis Pub/Sub listener
    # -----------------------------------------------------------------------

    @tasks.loop(seconds=0.1)
    async def sync_listener_loop(self):
        from app.unifieduser import signals
        if not self.pubsub.subscribed:
            await self.pubsub.subscribe(signals.USER_PERMISSION_PUBSUB_CHANNEL)

        try:
            import json
            message = await self.pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                data = json.loads(message["data"])
                await self.process_event(data)
        except Exception:
            self.logger.exception("MembershipApplicationCog sync error:")

    async def process_event(self, data: dict):
        if data.get("type") != "User.Permission":
            return
        if data.get("permission") != "candidacy.can_review_membershipapplication":
            return

        action = data.get("action")
        user_id = data.get("user_id")

        try:
            user = await self.User.objects.prefetch_related("discorduser").aget(pk=user_id)
            discord_id = user.discorduser.discorduid
        except (self.User.DoesNotExist, AttributeError):
            self.logger.warning("process_event: user %s not found or not linked", user_id)
            return

        member = await self._get_member(discord_id)
        if not member:
            return

        if action == "added":
            apps = []
            async for app in MembershipApplicationRecord.objects.filter(
                status=MembershipApplicationRecord.Status.PENDING
            ).select_related("membership_type"):
                apps.append(app)

            for app in apps:
                has_perm = await sync_to_async(user.has_perm)(
                    "candidacy.can_review_membershipapplication", app.membership_type
                )
                if has_perm:
                    try:
                        thread = await self._get_thread(app.thread_id)
                        await self._add_to_thread(member, thread)
                    except Exception:
                        pass

        elif action == "removed":
            apps = []
            async for app in MembershipApplicationRecord.objects.filter(
                status=MembershipApplicationRecord.Status.PENDING
            ).select_related("membership_type"):
                apps.append(app)

            for app in apps:
                has_perm = await sync_to_async(user.has_perm)(
                    "candidacy.can_review_membershipapplication", app.membership_type
                )
                if not has_perm:
                    try:
                        thread = await self._get_thread(app.thread_id)
                        await self._remove_from_thread(member, thread)
                    except Exception:
                        pass

    # -----------------------------------------------------------------------
    # RSI Verification flow
    # -----------------------------------------------------------------------

    async def handle_rsi_url_submit(
        self,
        interaction: discord.Interaction,
        rsi_url: str,
        membership_type: MembershipApplicationType,
    ):
        if not _validate_rsi_url(rsi_url):
            await interaction.response.send_message(
                "❌ Invalid RSI URL.\n\nExpected: `https://robertsspaceindustries.com/citizens/YourUsername`",
                ephemeral=True,
            )
            return

        rsi_handle = _extract_rsi_handle(rsi_url)

        # --- Check for duplicate verified handle ---
        existing_verified = await RSIVerification.objects.filter(
            rsi_handle=rsi_handle,
            verification_status=RSIVerification.Status.VERIFIED,
        ).exclude(discord_id=interaction.user.id).afirst()

        if existing_verified:
            await interaction.response.send_message(
                f"❌ The RSI handle `{rsi_handle}` is already verified by another user. "
                "If this is your account, please contact staff.",
                ephemeral=True,
            )
            return
        # -----------------------------------------

        # --- Ensure DiscordUser exists and has a linked user ---
        discord_user, created = await DiscordUser.objects.select_related('user').aget_or_create(
            discorduid=interaction.user.id,
            defaults={
                'access_token': None,
                'refresh_token': None,
                'access_token_expires': None,
            }
        )

        # Safely get the linked user (avoid synchronous operation)
        user = await sync_to_async(lambda: discord_user.user, thread_sensitive=False)()

        if user is None:
            # Create a new OrgPlayer user
            User = get_user_model()
            username = f"discord_{interaction.user.id}"
            # Ensure uniqueness (just in case)
            counter = 1
            base_username = username
            while await User.objects.filter(username=username).aexists():
                username = f"{base_username}_{counter}"
                counter += 1
            user = await User.objects.acreate_user(username=username)
            discord_user.user = user
            await discord_user.asave()
            self.logger.info(f"Created missing user {user.username} for Discord user {interaction.user.id}")

        # Seed bot cache with current guild nick so display_name resolves correctly
        # even before the next full member sync runs.
        bot_cache_key = f"bot.discorduid.{interaction.user.id}"
        existing_bot_cache = await sync_to_async(cache.get)(bot_cache_key) or {}
        if not existing_bot_cache.get("membername"):
            await sync_to_async(cache.set)(bot_cache_key, {
                **existing_bot_cache,
                "username":      interaction.user.name,
                "membername":    interaction.user.display_name,
                "discriminator": getattr(interaction.user, "discriminator", None),
            }, None)
        # ----------------------------------------------------

        existing = await RSIVerification.objects.filter(
            discord_id=interaction.user.id,
            verification_status__in=[RSIVerification.Status.PENDING, RSIVerification.Status.VERIFIED],
        ).afirst()

        if existing and existing.verification_status == RSIVerification.Status.VERIFIED:
            draft = await _load_draft(self.redis_client, interaction.user.id, f"app:{membership_type.pk}")
            await interaction.response.send_modal(MembershipApplicationModal(membership_type, existing, rc=self.redis_client, user_id=interaction.user.id, draft=draft))
            return

        if existing and existing.verification_status == RSIVerification.Status.PENDING:
            existing.rsi_handle = rsi_handle
            existing.rsi_profile_url = rsi_url
            await existing.asave(update_fields=["rsi_handle", "rsi_profile_url"])
            verification = existing
        else:
            timeout_minutes = await aget_global_preference(MembershipRSIVerificationTimeoutMinutes.import_path)
            code = _generate_verification_code()
            verification = await RSIVerification.objects.acreate(
                discord_id=interaction.user.id,
                rsi_handle=rsi_handle,
                rsi_profile_url=rsi_url,
                verification_code=code,
                verification_status=RSIVerification.Status.PENDING,
                expires_at=now() + timedelta(minutes=timeout_minutes),
            )

        embed = discord.Embed(
            title="🔐 RSI Verification",
            description=(
                f"RSI Handle: `{rsi_handle}`\n\n"
                f"**Verification Code:**\n```\n{verification.verification_code}\n```\n\n"
                "**Instructions:**\n"
                f"1. Go to: {rsi_url}\n"
                "2. Click **Edit**\n"
                "3. Add the code above to your **Short Bio**\n"
                "4. Save — the bot checks automatically every 20 seconds.\n\n"
                "Once verified, click **Start Verification** again to continue."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        if verification.id not in self.rsi_check_tasks:
            task = asyncio.create_task(
                self._rsi_poll_loop(interaction, verification.id, membership_type),
                name=f"rsi_check_{verification.id}",
            )
            self.rsi_check_tasks[verification.id] = task

    async def _rsi_poll_loop(
        self,
        interaction: discord.Interaction,
        verification_id: int,
        membership_type: MembershipApplicationType,
    ):
        interval = await aget_global_preference(MembershipRSICheckIntervalSeconds.import_path) or 20
        timeout = await aget_global_preference(MembershipRSIVerificationTimeoutMinutes.import_path) or 30
        max_attempts = (timeout * 60) // interval

        for _ in range(max_attempts):
            try:
                v = await RSIVerification.objects.filter(pk=verification_id).afirst()
                if not v or v.verification_status != RSIVerification.Status.PENDING:
                    break

                if await _check_rsi_profile(v.rsi_profile_url, v.verification_code):
                    v.verification_status = RSIVerification.Status.VERIFIED
                    v.verified_at = now()
                    # Fetch RSI profile details
                    details = await _extract_rsi_details(v.rsi_profile_url)
                    if details['enlisted_date']:
                        # Convert to date object if possible
                        try:
                            v.enlisted_date = datetime.strptime(details['enlisted_date'], '%b %d, %Y').date()
                        except Exception:
                            pass
                    v.org_membership = details['org_membership'] or ''
                    await v.asave(update_fields=["verification_status", "verified_at", "enlisted_date", "org_membership"])

                    # Set CustomDisplayName = RSI handle so rank prefix is prepended on all future changes
                    await _set_display_name_to_handle(v.discord_id, v.rsi_handle)

                    guild_id = await self.client.aget_main_guild_id()
                    guild = self.client.get_guild(guild_id)
                    if guild:
                        member = guild.get_member(v.discord_id)
                        if member and guild.me.guild_permissions.manage_nicknames:
                            try:
                                await member.edit(nick=v.rsi_handle)
                            except discord.HTTPException:
                                pass

                    try:
                        await interaction.followup.send(
                            embed=discord.Embed(
                                title="✅ RSI Verified!",
                                description=(
                                    f"Your RSI account `{v.rsi_handle}` is now verified.\n\n"
                                    "Click **Start Verification** again to select your membership path and apply."
                                ),
                                color=discord.Color.green(),
                            ),
                            ephemeral=True,
                        )
                    except Exception:
                        pass
                    break

                await asyncio.sleep(interval)

            except Exception:
                self.logger.exception("RSI poll loop error for verification_id=%s", verification_id)
                await asyncio.sleep(interval)

        self.rsi_check_tasks.pop(verification_id, None)

    async def _assign_rank(self, discord_id: int, rank_pk: int):
        """Assign OrgRank to a user."""
        from app.unifieduser.models import OrgRank
        try:
            rank = await OrgRank.objects.aget(pk=rank_pk)
            du, _ = await sync_to_async(DiscordUser.ensure_with_user)(
                discorduid=discord_id, access_token=None, refresh_token=None, access_token_expires=None
            )
            du.user.rank = rank
            await du.user.asave(update_fields=["rank"])
            self.logger.info("Assigned rank %s (pk=%s) to discord_id=%s", rank.name, rank_pk, discord_id)
        except OrgRank.DoesNotExist:
            self.logger.error("OrgRank pk=%s not found — check MembershipVisitorRankPK preference", rank_pk)
        except Exception:
            self.logger.exception("_assign_rank error for discord_id=%s rank_pk=%s", discord_id, rank_pk)

    # -----------------------------------------------------------------------
    # Auto-archive task
    # -----------------------------------------------------------------------

    @tasks.loop(hours=1)
    async def auto_archive_task(self):
        """Check for approved applications older than auto-archive hours and lock/archive threads."""
        auto_archive_hours = await aget_global_preference(MembershipAutoArchiveHours.import_path)
        if not auto_archive_hours:
            return
        cutoff = now() - timedelta(hours=auto_archive_hours)
        # Get approved applications that have not been archived yet and were reviewed before cutoff
        records = MembershipApplicationRecord.objects.filter(
            status=MembershipApplicationRecord.Status.APPROVED,
            thread_archived=False,
            reviewed_at__lte=cutoff,
        )
        async for record in records:
            try:
                thread = await self._get_thread(record.thread_id)
                # Lock and archive
                await thread.edit(locked=True, archived=True)
                record.thread_archived = True
                await record.asave(update_fields=['thread_archived'])
                self.logger.info(f"Auto-archived thread {thread.id} for application {record.id}")
            except Exception as e:
                self.logger.error(f"Failed to auto-archive thread {record.thread_id}: {e}")

    @auto_archive_task.before_loop
    async def before_auto_archive(self):
        await self.client.wait_until_ready()

    @tasks.loop(hours=24)
    async def cleanup_stale_applications_task(self):
        """Auto-deny PENDING applications older than MembershipAutoCloseDays and lock/archive their threads."""
        from app.candidacy.preferences import MembershipAutoCloseDays, MembershipDenialMessage
        close_days = await aget_global_preference(MembershipAutoCloseDays.import_path)
        if not close_days:
            return

        cutoff = now() - timedelta(days=close_days)
        records = MembershipApplicationRecord.objects.filter(
            status=MembershipApplicationRecord.Status.PENDING,
            submitted_at__lte=cutoff,
        ).select_related("applicant__discorduser")

        denial_tpl = await aget_global_preference(MembershipDenialMessage.import_path) or "❌ Your application has been automatically closed due to inactivity."

        async for record in records:
            try:
                record.status = MembershipApplicationRecord.Status.DENIED
                record.thread_archived = True
                await record.asave(update_fields=["status", "thread_archived"])

                if record.thread_id:
                    try:
                        thread = await self._get_thread(record.thread_id)
                        applicant_name = getattr(record.applicant, "display_name", "Applicant")
                        await thread.send(
                            f"🔒 This application has been automatically closed after {close_days} days of inactivity.\n\n"
                            + denial_tpl.format(
                                applicant=f"<@{record.applicant.discorduser.discorduid}>" if hasattr(record.applicant, "discorduser") else applicant_name,
                                applicant_name=applicant_name,
                                rank_name="",
                            )
                        )
                        await thread.edit(locked=True, archived=True)
                    except Exception as e:
                        self.logger.error("cleanup_stale: thread error for record %s: %s", record.pk, e)

                self.logger.info("Auto-denied stale application pk=%s (submitted %s)", record.pk, record.submitted_at)
            except Exception:
                self.logger.exception("cleanup_stale: error processing record pk=%s", record.pk)

    @cleanup_stale_applications_task.before_loop
    async def before_cleanup(self):
        await self.client.wait_until_ready()

    @tasks.loop(hours=12)
    async def follow_up_reminder_task(self):
        """DM newly approved members a follow-up reminder N days after approval."""
        from app.candidacy.preferences import MembershipFollowUpReminderDays, MembershipFollowUpMessage
        reminder_days = await aget_global_preference(MembershipFollowUpReminderDays.import_path)
        if not reminder_days:
            return

        window_start = now() - timedelta(days=reminder_days + 1)
        window_end   = now() - timedelta(days=reminder_days)

        records = MembershipApplicationRecord.objects.filter(
            status=MembershipApplicationRecord.Status.APPROVED,
            follow_up_sent=False,
            reviewed_at__gte=window_start,
            reviewed_at__lte=window_end,
        ).select_related("applicant__discorduser")

        msg_tpl = await aget_global_preference(MembershipFollowUpMessage.import_path) or ""

        async for record in records:
            try:
                discord_uid = record.applicant.discorduser.discorduid
                member = await self._get_member(int(discord_uid))
                if not member:
                    continue

                msg_text = msg_tpl.format(
                    member=member.mention,
                    member_name=member.display_name,
                )
                await member.send(msg_text)

                record.follow_up_sent = True
                await record.asave(update_fields=["follow_up_sent"])
                self.logger.info("Sent follow-up reminder to %s (application pk=%s)", discord_uid, record.pk)
            except discord.Forbidden:
                self.logger.warning("follow_up_reminder: DMs closed for application pk=%s", record.pk)
                record.follow_up_sent = True
                await record.asave(update_fields=["follow_up_sent"])
            except Exception:
                self.logger.exception("follow_up_reminder: error for application pk=%s", record.pk)

    @follow_up_reminder_task.before_loop
    async def before_follow_up_reminder(self):
        await self.client.wait_until_ready()

    @tasks.loop(hours=12)
    async def discipline_reminder_task(self):
        """DM approved members with no discipline selected after MembershipDisciplineReminderDays."""
        from app.candidacy.preferences import MembershipDisciplineReminderDays, MembershipDisciplineSelectionChannelID as _DSC
        reminder_days = await aget_global_preference(MembershipDisciplineReminderDays.import_path)
        if not reminder_days:
            return

        cutoff = now() - timedelta(days=reminder_days)
        records = MembershipApplicationRecord.objects.filter(
            status=MembershipApplicationRecord.Status.APPROVED,
            primary_focus__isnull=True,
            reviewed_at__lte=cutoff,
            follow_up_sent=True,   # only ping after the general follow-up has gone out
        ).select_related("applicant__discorduser")

        disc_chan_id = await aget_global_preference(_DSC.import_path) or 0

        async for record in records:
            try:
                discord_uid = record.applicant.discorduser.discorduid
                member = await self._get_member(int(discord_uid))
                if not member:
                    continue
                disc_mention = f"<#{disc_chan_id}>" if disc_chan_id else "the discipline selection channel"
                await member.send(
                    f"Hey {member.display_name}! 👋\n\n"
                    f"It looks like you haven't selected a discipline yet.\n"
                    f"Head over to {disc_mention} to pick one and unlock discipline-specific channels and pings!"
                )
                self.logger.info("Sent discipline reminder to %s (application pk=%s)", discord_uid, record.pk)
            except discord.Forbidden:
                pass
            except Exception:
                self.logger.exception("discipline_reminder_task: error for application pk=%s", record.pk)

    @discipline_reminder_task.before_loop
    async def before_discipline_reminder(self):
        await self.client.wait_until_ready()

    # -----------------------------------------------------------------------
    # Thread / member helpers
    # -----------------------------------------------------------------------

    async def _get_member(self, user_id: int) -> discord.Member | None:
        channel = self.client.get_channel(self.verification_chan_id)
        if not channel:
            return None
        guild = channel.guild
        try:
            return guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            self.logger.warning("Member %s not found in guild", user_id)
        except discord.Forbidden:
            self.logger.error("Bot lacks permission to fetch members")
        return None

    async def _get_thread(self, thread_id: int) -> discord.Thread:
        thread = self.client.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.client.fetch_channel(thread_id)
            except discord.NotFound:
                self.logger.error("Thread %s not found", thread_id)
                raise
            except discord.Forbidden:
                self.logger.error("Bot lacks access to thread %s", thread_id)
                raise
        if not isinstance(thread, discord.Thread):
            raise ValueError(f"Channel {thread_id} is not a Thread")
        return thread

    async def _add_to_thread(self, member: discord.Member, thread: discord.Thread):
        try:
            await thread.add_user(member)
        except discord.Forbidden:
            self.logger.error("Missing permissions to add user to thread %s", thread.name)

    async def _remove_from_thread(self, member: discord.Member, thread: discord.Thread):
        try:
            await thread.remove_user(member)
        except discord.Forbidden:
            self.logger.error("Missing permissions to remove user from thread %s", thread.name)


    # -----------------------------------------------------------------------
    # Staff toggle commands
    # -----------------------------------------------------------------------

    from discord import app_commands as _apc

    @_apc.command(
        name="membership_rsi_toggle",
        description="[Staff] Toggle RSI profile linking and/or bio-code verification on/off",
    )
    @_apc.describe(
        linking="Master switch — OFF skips the entire RSI step for all applicants",
        verification="Bio-code check — OFF collects RSI URL without requiring bio-code (only applies when linking is ON)",
    )
    @_apc.choices(
        linking=[
            _apc.Choice(name="ON",  value=1),
            _apc.Choice(name="OFF", value=0),
        ],
        verification=[
            _apc.Choice(name="ON",  value=1),
            _apc.Choice(name="OFF", value=0),
        ],
    )
    async def cmd_rsi_toggle(
        self,
        interaction: discord.Interaction,
        linking: _apc.Choice[int] | None = None,
        verification: _apc.Choice[int] | None = None,
    ):
        from app.main.util.discord_command_checks import _verify_and_cache_generic_permission
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, "candidacy.change_membershipapplicationtype")
        except Exception:
            await interaction.response.send_message("🛡️ Staff only.", ephemeral=True)
            return

        if linking is None and verification is None:
            # Read current state
            cur_link = await aget_global_preference(MembershipRSILinkingEnabled.import_path)
            cur_verify = await aget_global_preference(MembershipRSIVerificationEnabled.import_path)
            embed = discord.Embed(title="🔗 RSI Settings — Current State", color=0x5865F2)
            embed.add_field(name="RSI Profile Linking",      value="✅ ON" if cur_link  else "❌ OFF", inline=True)
            embed.add_field(name="Bio-Code Verification",    value="✅ ON" if cur_verify else "❌ OFF", inline=True)
            embed.set_footer(text="Use /membership_rsi_toggle linking:ON/OFF verification:ON/OFF to change")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        from app.preferences.models import GlobalSetting
        from asgiref.sync import sync_to_async

        changes = []

        if linking is not None:
            new_val = bool(linking.value)
            def _set_linking():
                data = GlobalSetting.get_setting_obj(MembershipRSILinkingEnabled.import_path)
                data.value = new_val
                data.save()
            await sync_to_async(_set_linking)()
            changes.append(f"RSI Profile Linking → **{'ON' if new_val else 'OFF'}**")

        if verification is not None:
            new_val = bool(verification.value)
            def _set_verification():
                data = GlobalSetting.get_setting_obj(MembershipRSIVerificationEnabled.import_path)
                data.value = new_val
                data.save()
            await sync_to_async(_set_verification)()
            changes.append(f"Bio-Code Verification → **{'ON' if new_val else 'OFF'}**")

        embed = discord.Embed(
            title="✅ RSI Settings Updated",
            description="\n".join(changes),
            color=0x22C55E,
        )
        # Show the resulting combined state as a reminder
        cur_link   = await aget_global_preference(MembershipRSILinkingEnabled.import_path)
        cur_verify = await aget_global_preference(MembershipRSIVerificationEnabled.import_path)

        if not cur_link:
            embed.add_field(name="ℹ️ Effect", value="RSI step is **fully skipped** for all new applicants.", inline=False)
        elif not cur_verify:
            embed.add_field(name="ℹ️ Effect", value="RSI URL is **collected but not verified** with a bio-code.", inline=False)
        else:
            embed.add_field(name="ℹ️ Effect", value="RSI URL is collected **and** verified with a bio-code.", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
        self.logger.info(
            "RSI toggles updated by %s: %s",
            interaction.user, ", ".join(changes),
        )


    from discord import app_commands as _apc2

    @_apc2.command(
        name="application_status",
        description="Check the status of your membership application",
    )
    async def application_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        record = await MembershipApplicationRecord.objects.filter(
            applicant__discorduser__discorduid=interaction.user.id,
        ).select_related(
            "membership_type", "reviewer", "reviewer__displaynamesearchcache",
            "reviewer__discorduser", "primary_focus",
        ).order_by("-submitted_at").afirst()

        if not record:
            await interaction.followup.send(
                "You don't have any membership applications on record.",
                ephemeral=True,
            )
            return

        status_emoji = {
            MembershipApplicationRecord.Status.PENDING:  "⏳",
            MembershipApplicationRecord.Status.APPROVED: "✅",
            MembershipApplicationRecord.Status.DENIED:   "❌",
            MembershipApplicationRecord.Status.RESET:    "🔄",
        }.get(record.status, "❓")

        color = {
            MembershipApplicationRecord.Status.APPROVED: 0x22C55E,
            MembershipApplicationRecord.Status.DENIED:   0xEF4444,
            MembershipApplicationRecord.Status.PENDING:  0xF59E0B,
            MembershipApplicationRecord.Status.RESET:    0x6B7280,
        }.get(record.status, 0x5865F2)

        embed = discord.Embed(
            title=f"{status_emoji} Application Status",
            color=color,
            timestamp=record.submitted_at,
        )
        embed.add_field(name="Type",        value=record.membership_type.name if record.membership_type else "—", inline=True)
        embed.add_field(name="Status",      value=record.get_status_display(),                                     inline=True)
        embed.add_field(name="Submitted",   value=discord.utils.format_dt(record.submitted_at, "D"),               inline=True)

        if record.reviewed_at:
            embed.add_field(name="Reviewed",  value=discord.utils.format_dt(record.reviewed_at, "D"),  inline=True)
        if record.reviewer:
            try:
                reviewer_name = record.reviewer.displaynamesearchcache.display_name
            except Exception:
                du = getattr(record.reviewer, 'discorduser', None)
                reviewer_name = getattr(du, 'main_guild_nick', None) or record.reviewer.username
            embed.add_field(name="Reviewer",  value=reviewer_name,  inline=True)
        if record.primary_focus:
            embed.add_field(name="Discipline", value=record.primary_focus.name,                         inline=True)
        if record.rsi_handle:
            embed.add_field(name="RSI Handle", value=record.rsi_handle,                                 inline=True)
        if record.status == MembershipApplicationRecord.Status.PENDING and record.thread_id:
            embed.add_field(
                name="Your Thread",
                value=f"<#{record.thread_id}>",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @_apc2.command(
        name="flag_applicant",
        description="[Staff] Flag an applicant as suspicious and notify the staff channel",
    )
    @_apc2.describe(
        discord_user="The Discord user whose application to flag",
        reason="Why this applicant is being flagged",
    )
    @requires_django_perm("candidacy.can_review_membershipapplication")
    async def flag_applicant(
        self,
        interaction: discord.Interaction,
        discord_user: discord.Member,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=True)

        from app.candidacy.preferences import MembershipSuspiciousUserChannelID, MembershipReviewChannelID

        record = await MembershipApplicationRecord.objects.filter(
            applicant__discorduser__discorduid=discord_user.id,
            status=MembershipApplicationRecord.Status.PENDING,
        ).select_related("applicant").afirst()

        if not record:
            await interaction.followup.send(
                f"No active PENDING application found for {discord_user.mention}.",
                ephemeral=True,
            )
            return

        record.flagged_suspicious = True
        record.flag_reason = reason
        await record.asave(update_fields=["flagged_suspicious", "flag_reason"])

        # Post to suspicious channel (fall back to review channel)
        chan_id = await aget_global_preference(MembershipSuspiciousUserChannelID.import_path) or 0
        if not chan_id:
            chan_id = await aget_global_preference(MembershipReviewChannelID.import_path) or 0

        if chan_id:
            chan = self.client.get_channel(int(chan_id))
            if chan:
                embed = discord.Embed(
                    title="⚠️ Suspicious Applicant Flagged",
                    color=0xF59E0B,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Applicant",   value=f"{discord_user.mention} ({discord_user.id})", inline=True)
                embed.add_field(name="Flagged by",  value=f"{interaction.user.mention}",                 inline=True)
                embed.add_field(name="Application", value=f"Record #{record.pk}",                        inline=True)
                embed.add_field(name="Reason",      value=reason[:1000],                                 inline=False)
                if record.rsi_profile_url:
                    embed.add_field(name="RSI Profile", value=record.rsi_profile_url, inline=False)
                await chan.send(embed=embed)

        await interaction.followup.send(
            f"✅ {discord_user.mention} flagged as suspicious (application #{record.pk}).",
            ephemeral=True,
        )
        self.logger.info(
            "Application pk=%s flagged as suspicious by %s: %s",
            record.pk, interaction.user, reason,
        )


async def setup(client: commands.Bot):
    await client.add_cog(MembershipApplicationCog(client))
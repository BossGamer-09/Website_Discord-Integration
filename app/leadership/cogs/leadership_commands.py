import json
import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

import redis.asyncio as redis
from django.conf import settings

from app.main.util.discord_command_checks import requires_django_perm
from app.preferences.utils import aget_global_preference

from asgiref.sync import sync_to_async

from ..models import DisciplinaryRecord, DecisionLog
from ..preferences import (
    LeadershipLogChannelID,
    LeadershipApprovalChannelID,
    LeadershipDecisionLogChannelID,
    LeadershipApprovalRequiredRankPKs,
    LeadershipInactiveRankPK,
    LeadershipAnnounceBanEnabled,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approval_rank_pks(raw: str) -> set[int]:
    if not raw:
        return set()
    try:
        return {int(x.strip()) for x in raw.split(',') if x.strip()}
    except ValueError:
        return set()


async def _resolve_orgplayer(discord_id: int):
    User = get_user_model()
    return await User.objects.filter(discorduser__discorduid=discord_id).afirst()


async def _post_log(bot, channel_pref, embed: discord.Embed):
    chan_id = await aget_global_preference(channel_pref.import_path)
    if not chan_id:
        return
    chan = bot.get_channel(int(chan_id))
    if chan:
        try:
            await chan.send(embed=embed)
        except discord.HTTPException:
            pass


def _action_color(action_type: str) -> discord.Color:
    return {
        DisciplinaryRecord.ActionType.WARN:       discord.Color.yellow(),
        DisciplinaryRecord.ActionType.MUTE:       discord.Color.orange(),
        DisciplinaryRecord.ActionType.KICK:       discord.Color.red(),
        DisciplinaryRecord.ActionType.BAN:        discord.Color.dark_red(),
        DisciplinaryRecord.ActionType.BLACKLIST:  discord.Color.dark_gray(),
        DisciplinaryRecord.ActionType.INACTIVE:   discord.Color.greyple(),
        DisciplinaryRecord.ActionType.REPRIMAND:  discord.Color.orange(),
        DisciplinaryRecord.ActionType.NOTE:       discord.Color.blurple(),
        DisciplinaryRecord.ActionType.FORCE_ROLE: discord.Color.teal(),
    }.get(action_type, discord.Color.default())


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class ReasonModal(discord.ui.Modal):
    reason_input = discord.ui.TextInput(
        label='Reason',
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    notes_input = discord.ui.TextInput(
        label='Internal Notes (staff only)',
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, title: str, action_type: str, target: discord.Member,
                 cog, duration_hours: int = 0, forced_rank_id: int = None):
        super().__init__(title=title, timeout=300)
        self._action_type = action_type
        self._target = target
        self._cog = cog
        self._duration_hours = duration_hours
        self._forced_rank_id = forced_rank_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._cog.execute_action(
            interaction=interaction,
            target=self._target,
            action_type=self._action_type,
            reason=self.reason_input.value,
            notes=self.notes_input.value or '',
            duration_hours=self._duration_hours,
            forced_rank_id=self._forced_rank_id,
        )


# ---------------------------------------------------------------------------
# Approval View
# ---------------------------------------------------------------------------

class ApprovalView(discord.ui.View):
    def __init__(self, record_id: int, cog):
        super().__init__(timeout=None)
        self._record_id = record_id
        self._cog = cog

    @discord.ui.button(label='Approve', style=discord.ButtonStyle.success, custom_id='leadership_approve')
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        record = await DisciplinaryRecord.objects.filter(pk=self._record_id).afirst()
        if not record or record.status != DisciplinaryRecord.Status.ACTIVE:
            await interaction.followup.send('Record not found or already resolved.', ephemeral=True)
            return

        approver = await _resolve_orgplayer(interaction.user.id)
        record.approved_by = approver
        record.approved_at = timezone.now()
        await record.asave(update_fields=['approved_by', 'approved_at'])

        subject_with_du = await DisciplinaryRecord.objects.select_related('subject__discorduser').filter(pk=record.pk).afirst()
        discord_uid = subject_with_du.subject.discorduser.discorduid if subject_with_du else None
        await self._cog._apply_discord_action(record, interaction.guild, discord_uid=discord_uid)
        await interaction.followup.send('Action approved and applied.', ephemeral=True)
        self.stop()

    @discord.ui.button(label='Deny', style=discord.ButtonStyle.danger, custom_id='leadership_deny')
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        record = await DisciplinaryRecord.objects.filter(pk=self._record_id).afirst()
        if not record:
            await interaction.followup.send('Record not found.', ephemeral=True)
            return
        record.status = DisciplinaryRecord.Status.PARDONED
        record.pardoned_at = timezone.now()
        approver = await _resolve_orgplayer(interaction.user.id)
        record.pardoned_by = approver
        await record.asave(update_fields=['status', 'pardoned_at', 'pardoned_by'])
        await interaction.followup.send('Action denied and cancelled.', ephemeral=True)
        self.stop()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class LeadershipCommandsCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.OrgRank = apps.get_model('unifieduser', 'OrgRank')
        self.startup_task.start()

    async def cog_unload(self):
        self.startup_task.cancel()

    @tasks.loop(count=1)
    async def startup_task(self):
        logger.info('LeadershipCommandsCog ready')

    @startup_task.before_loop
    async def before_startup(self):
        await self.client.wait_until_ready()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        from app.main.util.discord_command_checks import MissingDjangoPermission, AccountNotLinked
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ Missing permission: `{error.missing_perm}`"
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 You need to link your Discord account first."
        else:
            logger.exception("LeadershipCommandsCog command error: %s", error)
            msg = f"❌ An error occurred: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Rank autocomplete
    # ------------------------------------------------------------------

    async def rank_autocomplete(self, interaction: discord.Interaction, current: str):
        qs = self.OrgRank.objects.all()
        if current:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=current) | Q(prefix__icontains=current))
        return [
            app_commands.Choice(name=f'{r.prefix} {r.name}'.strip()[:100], value=r.pk)
            async for r in qs.order_by('order')[:25]
        ]

    # ------------------------------------------------------------------
    # Core execution logic
    # ------------------------------------------------------------------

    async def execute_action(
        self, *, interaction: discord.Interaction, target: discord.Member,
        action_type: str, reason: str, notes: str = '',
        duration_hours: int = 0, forced_rank_id: int = None,
    ):
        issuer = await _resolve_orgplayer(interaction.user.id)
        subject = await _resolve_orgplayer(target.id)

        if not subject:
            await interaction.followup.send(
                f'{target.mention} is not linked to a backend account.', ephemeral=True
            )
            return

        expires_at = None
        if duration_hours > 0:
            expires_at = timezone.now() + timedelta(hours=duration_hours)

        raw_pks = await aget_global_preference(LeadershipApprovalRequiredRankPKs.import_path)
        approval_pks = _approval_rank_pks(raw_pks)
        needs_approval = bool(
            approval_pks and subject.rank_id and subject.rank_id in approval_pks
            and action_type in (
                DisciplinaryRecord.ActionType.BAN,
                DisciplinaryRecord.ActionType.BLACKLIST,
                DisciplinaryRecord.ActionType.KICK,
            )
        )

        record = await DisciplinaryRecord.objects.acreate(
            subject=subject,
            issued_by=issuer,
            action_type=action_type,
            reason=reason,
            internal_notes=notes,
            expires_at=expires_at,
            requires_approval=needs_approval,
            forced_rank_id=forced_rank_id,
        )

        if needs_approval:
            await self._request_approval(record, target, interaction)
            await interaction.followup.send('Action requires approval. Sent to approval channel.', ephemeral=True)
            return

        await self._apply_discord_action(record, interaction.guild, discord_uid=target.id)

        embed = discord.Embed(
            title=f'{record.get_action_type_display()} — {target.display_name}',
            description=reason,
            color=_action_color(action_type),
            timestamp=timezone.now(),
        )
        embed.set_footer(text=f'Issued by {interaction.user.display_name}')
        if expires_at:
            embed.add_field(name='Expires', value=f'<t:{int(expires_at.timestamp())}:R>')

        await _post_log(self.client, LeadershipLogChannelID, embed)
        await _post_log(self.client, LeadershipDecisionLogChannelID, embed)
        await interaction.followup.send(
            f'✅ {record.get_action_type_display()} applied to {target.mention}.', ephemeral=True
        )

    async def _apply_discord_action(self, record: DisciplinaryRecord, guild: discord.Guild, discord_uid: int = None):
        if not guild:
            return

        if discord_uid is None:
            discord_uid = await sync_to_async(
                lambda: record.subject.discorduser.discorduid if hasattr(type(record.subject), 'discorduser') else None
            )()

        discord_user = guild.get_member(discord_uid) if discord_uid else None

        if record.action_type == DisciplinaryRecord.ActionType.KICK and discord_user:
            try:
                await guild.kick(discord_user, reason=record.reason[:512])
            except discord.HTTPException as e:
                logger.warning('Kick failed for %s: %s', record.subject, e)

        elif record.action_type in (
            DisciplinaryRecord.ActionType.BAN,
            DisciplinaryRecord.ActionType.BLACKLIST,
        ):
            try:
                await guild.ban(discord.Object(id=discord_uid), reason=record.reason[:512])
            except discord.HTTPException as e:
                logger.warning('Ban failed for %s: %s', record.subject, e)

        elif record.action_type == DisciplinaryRecord.ActionType.INACTIVE:
            inactive_rank_pk = await aget_global_preference(LeadershipInactiveRankPK.import_path)
            if inactive_rank_pk:
                rank = await self.OrgRank.objects.filter(pk=inactive_rank_pk).afirst()
                if rank:
                    await record.subject.aset_rank(rank)

        elif record.action_type == DisciplinaryRecord.ActionType.FORCE_ROLE and record.forced_rank_id:
            rank = await self.OrgRank.objects.filter(pk=record.forced_rank_id).afirst()
            if rank:
                await record.subject.aset_rank(rank)

    async def _request_approval(self, record: DisciplinaryRecord, target: discord.Member, interaction: discord.Interaction):
        chan_id = await aget_global_preference(LeadershipApprovalChannelID.import_path)
        if not chan_id:
            return
        chan = self.client.get_channel(int(chan_id))
        if not chan:
            return
        embed = discord.Embed(
            title=f'⏳ Approval Required — {record.get_action_type_display()}',
            description=f'**Target:** {target.mention} (`{target.id}`)\n**Reason:** {record.reason}',
            color=discord.Color.orange(),
            timestamp=timezone.now(),
        )
        embed.set_footer(text=f'Requested by {interaction.user.display_name} | Record #{record.pk}')
        view = ApprovalView(record.pk, self)
        await chan.send(embed=embed, view=view)

    # ------------------------------------------------------------------
    # /leadership group
    # ------------------------------------------------------------------

    leadership = app_commands.Group(name='leadership', description='Leadership & disciplinary commands')

    @leadership.command(name='warn', description='Issue a warning to a member')
    @requires_django_perm('leadership.can_issue_warn')
    async def cmd_warn(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Issue Warning', DisciplinaryRecord.ActionType.WARN, member, self)
        )

    @leadership.command(name='mute', description='Mute / silence a member')
    @app_commands.describe(duration_hours='Duration in hours (default 24)')
    @requires_django_perm('leadership.can_issue_mute')
    async def cmd_mute(
        self, interaction: discord.Interaction,
        member: discord.Member,
        duration_hours: app_commands.Range[int, 1, 720] = 24,
    ):
        await interaction.response.send_modal(
            ReasonModal('Mute Member', DisciplinaryRecord.ActionType.MUTE, member, self, duration_hours=duration_hours)
        )

    @leadership.command(name='kick', description='Kick a member from the server')
    @requires_django_perm('leadership.can_issue_kick')
    async def cmd_kick(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Kick Member', DisciplinaryRecord.ActionType.KICK, member, self)
        )

    @leadership.command(name='ban', description='Ban a member')
    @requires_django_perm('leadership.can_issue_ban')
    async def cmd_ban(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Ban Member', DisciplinaryRecord.ActionType.BAN, member, self)
        )

    @leadership.command(name='blacklist', description='Blacklist a member (ban + flag)')
    @requires_django_perm('leadership.can_issue_blacklist')
    async def cmd_blacklist(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Blacklist Member', DisciplinaryRecord.ActionType.BLACKLIST, member, self)
        )

    @leadership.command(name='inactive', description='Mark a member as inactive')
    @requires_django_perm('leadership.can_mark_inactive')
    async def cmd_inactive(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Mark Inactive', DisciplinaryRecord.ActionType.INACTIVE, member, self)
        )

    @leadership.command(name='reprimand', description='Issue a formal reprimand')
    @requires_django_perm('leadership.can_issue_reprimand')
    async def cmd_reprimand(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Formal Reprimand', DisciplinaryRecord.ActionType.REPRIMAND, member, self)
        )

    @leadership.command(name='note', description='Add a staff note to a member')
    @requires_django_perm('leadership.can_add_staff_note')
    async def cmd_note(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(
            ReasonModal('Add Staff Note', DisciplinaryRecord.ActionType.NOTE, member, self)
        )

    @leadership.command(name='force_role', description='Force a rank/role change on a member')
    @app_commands.autocomplete(rank=rank_autocomplete)
    @requires_django_perm('leadership.can_force_role_change')
    async def cmd_force_role(
        self, interaction: discord.Interaction,
        member: discord.Member,
        rank: int,
    ):
        await interaction.response.send_modal(
            ReasonModal('Force Role Change', DisciplinaryRecord.ActionType.FORCE_ROLE, member, self, forced_rank_id=rank)
        )

    @leadership.command(name='history', description='View disciplinary history for a member')
    @requires_django_perm('leadership.can_view_records')
    async def cmd_history(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        subject = await _resolve_orgplayer(member.id)
        if not subject:
            await interaction.followup.send(f'{member.mention} has no backend account.', ephemeral=True)
            return

        lines = []
        async for r in DisciplinaryRecord.objects.filter(subject=subject).order_by('-issued_at')[:10]:
            ts = f'<t:{int(r.issued_at.timestamp())}:d>'
            status = '✅' if r.status == DisciplinaryRecord.Status.ACTIVE else '🔲'
            lines.append(f'{status} `{r.get_action_type_display()}` {ts} — {r.reason[:80]}')

        embed = discord.Embed(
            title=f'Disciplinary History — {member.display_name}',
            description='\n'.join(lines) or 'No records.',
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @leadership.command(name='pardon', description='Pardon / lift an active disciplinary action by record ID')
    @requires_django_perm('leadership.can_approve_discipline')
    async def cmd_pardon(self, interaction: discord.Interaction, record_id: int, reason: str = ''):
        await interaction.response.defer(ephemeral=True)
        record = await DisciplinaryRecord.objects.filter(pk=record_id).afirst()
        if not record:
            await interaction.followup.send('Record not found.', ephemeral=True)
            return
        if record.status != DisciplinaryRecord.Status.ACTIVE:
            await interaction.followup.send('Record is not active.', ephemeral=True)
            return

        issuer = await _resolve_orgplayer(interaction.user.id)
        record.status = DisciplinaryRecord.Status.PARDONED
        record.pardoned_at = timezone.now()
        record.pardoned_by = issuer
        if reason:
            record.internal_notes = (record.internal_notes + f'\nPardon reason: {reason}').strip()
        await record.asave(update_fields=['status', 'pardoned_at', 'pardoned_by', 'internal_notes'])
        await interaction.followup.send(f'✅ Record #{record_id} pardoned.', ephemeral=True)

    @leadership.command(name='promote', description='Promote a member to a specific rank')
    @app_commands.describe(member='Member to promote', rank='Target rank', reason='Reason (logged)')
    @app_commands.autocomplete(rank=rank_autocomplete)
    @requires_django_perm('leadership.can_promote')
    async def cmd_promote(
        self, interaction: discord.Interaction,
        member: discord.Member,
        rank: int,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=True)
        await self._change_rank(interaction, member, rank, reason, is_promotion=True)

    @leadership.command(name='demote', description='Demote a member to a specific rank')
    @app_commands.describe(member='Member to demote', rank='Target rank', reason='Reason (logged)')
    @app_commands.autocomplete(rank=rank_autocomplete)
    @requires_django_perm('leadership.can_demote')
    async def cmd_demote(
        self, interaction: discord.Interaction,
        member: discord.Member,
        rank: int,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=True)
        await self._change_rank(interaction, member, rank, reason, is_promotion=False)

    @leadership.command(name='whois', description='Full backend profile for a member')
    @requires_django_perm('leadership.can_view_records')
    async def cmd_whois(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        subject = await _resolve_orgplayer(member.id)
        if not subject:
            await interaction.followup.send(f'{member.mention} has no backend account.', ephemeral=True)
            return

        def _fetch():
            from app.candidacy.models import MembershipApplicationRecord
            from django.contrib.auth import get_user_model
            _User = get_user_model()

            s = _User.objects.select_related('rank').get(pk=subject.pk)
            rank = s.rank

            app = MembershipApplicationRecord.objects.filter(
                applicant=subject, status='APPROVED'
            ).select_related('primary_focus', 'referred_by', 'reviewer').order_by('-reviewed_at').first()

            pending = MembershipApplicationRecord.objects.filter(
                applicant=subject, status='PENDING'
            ).exists()

            records = list(DisciplinaryRecord.objects.filter(
                subject=subject, status=DisciplinaryRecord.Status.ACTIVE
            ).order_by('-issued_at')[:5])

            return rank, app, pending, records

        rank, app, has_pending, active_records = await sync_to_async(_fetch)()
        rank_str = f'{rank.prefix} {rank.name}'.strip() if rank else 'No Rank'

        embed = discord.Embed(
            title=f'🪪 {subject.display_name}',
            color=discord.Color.blurple(),
            timestamp=timezone.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name='Rank',     value=rank_str,                                          inline=True)
        embed.add_field(name='Discord',  value=f'{member.mention} (`{member.id}`)',               inline=True)
        embed.add_field(name='Timezone', value=subject.timezone_str or 'Not set',                 inline=True)
        embed.add_field(name='Joined',   value=discord.utils.format_dt(member.joined_at, 'D') if member.joined_at else '?', inline=True)

        if subject.first_event_at:
            embed.add_field(name='First Event', value=discord.utils.format_dt(subject.first_event_at, 'D'), inline=True)

        if app:
            disc_str     = app.primary_focus.name if app.primary_focus else '—'
            referrer_str = app.referred_by.display_name if app.referred_by else (app.referral_note or '—')
            embed.add_field(name='Discipline',  value=disc_str,     inline=True)
            embed.add_field(name='Referred By', value=referrer_str, inline=True)
            if app.rsi_handle:
                embed.add_field(name='RSI', value=app.rsi_handle, inline=True)

        if has_pending:
            embed.add_field(name='⚠️ Pending Application', value='Has an open application', inline=False)

        if active_records:
            lines = [f'`{r.get_action_type_display()}` — {r.reason[:60]}' for r in active_records]
            embed.add_field(name=f'⚠️ Active Records ({len(active_records)})', value='\n'.join(lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @leadership.command(name='mark_inactive', description='Mark a member as inactive in the backend')
    @app_commands.describe(member='The member to mark inactive', reason='Reason')
    @requires_django_perm('leadership.can_mark_inactive')
    async def cmd_mark_inactive(self, interaction: discord.Interaction, member: discord.Member, reason: str = ''):
        await interaction.response.defer(ephemeral=True)

        from app.discordauth.models import DiscordUser

        def _do():
            du = DiscordUser.objects.select_related('user').filter(discorduid=member.id).first()
            if not du or not du.user:
                return None
            du.user.is_active = False
            du.user.save(update_fields=['is_active'])
            if reason:
                from app.org.models import OrgPlayerNote
                OrgPlayerNote.objects.create(user=du.user, message=f'[Marked Inactive] {reason}')
            return du.user.pk

        pk = await sync_to_async(_do)()
        if pk is None:
            await interaction.followup.send(f'❌ No backend record found for {member.mention}.', ephemeral=True)
            return
        note_str = f' Reason: *{reason}*' if reason else ''
        await interaction.followup.send(
            f'✅ {member.mention} has been marked **inactive**.{note_str}', ephemeral=True
        )

    @leadership.command(name='add_note', description='Add an internal note to a member\'s record')
    @app_commands.describe(member='The member', note='Note content')
    @requires_django_perm('leadership.can_add_staff_note')
    async def cmd_add_note(self, interaction: discord.Interaction, member: discord.Member, note: str):
        await interaction.response.defer(ephemeral=True)

        from app.discordauth.models import DiscordUser

        def _do():
            from app.org.models import OrgPlayerNote
            du = DiscordUser.objects.select_related('user').filter(discorduid=member.id).first()
            if not du or not du.user:
                return False
            OrgPlayerNote.objects.create(user=du.user, message=note)
            return True

        ok = await sync_to_async(_do)()
        if not ok:
            await interaction.followup.send(f'❌ No backend record found for {member.mention}.', ephemeral=True)
            return
        await interaction.followup.send(f'✅ Note added to **{member.display_name}**\'s record.', ephemeral=True)

    # ------------------------------------------------------------------
    # Shared rank change logic
    # ------------------------------------------------------------------

    async def _change_rank(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        rank_pk: int,
        reason: str,
        is_promotion: bool,
    ):
        subject = await _resolve_orgplayer(member.id)
        if not subject:
            await interaction.followup.send(f'{member.mention} has no backend account.', ephemeral=True)
            return

        new_rank = await self.OrgRank.objects.filter(pk=rank_pk).afirst()
        if not new_rank:
            await interaction.followup.send('Rank not found.', ephemeral=True)
            return

        old_rank = await sync_to_async(lambda: subject.rank)()
        old_rank_str = f'{old_rank.prefix} {old_rank.name}'.strip() if old_rank else 'None'
        new_rank_str = f'{new_rank.prefix} {new_rank.name}'.strip()

        await subject.aset_rank(rank=new_rank)

        issuer = await _resolve_orgplayer(interaction.user.id)
        await DecisionLog.objects.acreate(
            event_type=DecisionLog.EventType.PROMOTION if is_promotion else DecisionLog.EventType.DEMOTION,
            subject=subject,
            actor=issuer,
            summary=f'{"Promoted" if is_promotion else "Demoted"}: {old_rank_str} → {new_rank_str}. Reason: {reason}',
            rank_before_id=old_rank.pk if old_rank else None,
            rank_after_id=new_rank.pk,
        )

        verb = 'Promoted' if is_promotion else 'Demoted'
        color = discord.Color.green() if is_promotion else discord.Color.orange()
        embed = discord.Embed(
            title=f'{"🎖️" if is_promotion else "📉"} {verb} — {member.display_name}',
            description=reason,
            color=color,
            timestamp=timezone.now(),
        )
        embed.add_field(name='Before', value=old_rank_str, inline=True)
        embed.add_field(name='After',  value=new_rank_str, inline=True)
        embed.set_footer(text=f'By {interaction.user.display_name}')

        await _post_log(self.client, LeadershipDecisionLogChannelID, embed)

        pub_embed = discord.Embed(
            title=f'{"🎖️ Promotion" if is_promotion else "📉 Rank Change"}',
            description=f'{member.mention} — **{old_rank_str}** → **{new_rank_str}**',
            color=color,
            timestamp=timezone.now(),
        )
        pub_embed.set_footer(text=f'By {interaction.user.display_name}')

        from app.candidacy.preferences import RankAnnouncementChannelID
        ann_chan_id = await aget_global_preference(RankAnnouncementChannelID.import_path)
        if ann_chan_id:
            ann_chan = self.client.get_channel(int(ann_chan_id))
            if ann_chan:
                await ann_chan.send(embed=pub_embed)

        from app.org.preferences import OrgAnnounceDefaultChannelID
        org_chan_id = await aget_global_preference(OrgAnnounceDefaultChannelID.import_path)
        if org_chan_id and int(org_chan_id) != int(ann_chan_id or 0):
            org_chan = self.client.get_channel(int(org_chan_id))
            if org_chan:
                await org_chan.send(embed=pub_embed)

        await interaction.followup.send(
            f'✅ {verb} {member.mention}: **{old_rank_str}** → **{new_rank_str}**',
            ephemeral=True,
        )


async def setup(client):
    await client.add_cog(LeadershipCommandsCog(client))

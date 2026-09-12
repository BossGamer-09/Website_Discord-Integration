# app/disfunction/cogs/welcome_system.py
import io
import os
import discord
from discord.ext import commands
from discord import app_commands
from io import BytesIO
import textwrap
import asyncio
import logging
import hashlib
from datetime import datetime, timedelta
from django.apps import apps
from django.utils import timezone

from app.main.util import img
from app.preferences.utils import aget_global_preference
from app.disfunction import preferences as disfunction_prefs
from app.main.util.discord_command_checks import requires_django_perm


class WelcomeSystem(commands.Cog):
    """Advanced Welcome/Leave System with Image Generation and Database Integration"""

    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.UserJoinRecord = None

    async def cog_load(self):
        self.logger.info("WelcomeSystem cog loaded successfully")
        await self._register_context_menus()

    async def cog_unload(self):
        self.logger.info("WelcomeSystem cog unloaded")

    async def _register_context_menus(self):
        try:
            self.client.tree.add_command(app_commands.ContextMenu(
                name="View User Info",
                callback=self._user_info_context_menu,
                type=discord.AppCommandType.user,
            ))
            self.client.tree.add_command(app_commands.ContextMenu(
                name="Message Info",
                callback=self._message_info_context_menu,
                type=discord.AppCommandType.message,
            ))
            self.logger.info("Context menu commands registered successfully")
        except Exception as e:
            self.logger.error(f"Error registering context menus: {e}")

    async def _get_user_record_model(self):
        if not self.UserJoinRecord:
            try:
                self.UserJoinRecord = apps.get_model('disfunction', 'UserJoinRecord')
            except Exception as e:
                self.logger.error(f"Failed to get UserJoinRecord model: {e}")
                return None
        return self.UserJoinRecord

    async def _create_welcome_image(self, member: discord.Member) -> discord.File:
        return await img.create_welcome_card(member)

    async def _create_or_update_user_record(self, member: discord.Member, is_join: bool = True,
                                            welcome_message_id: int = None, join_log_message_id: int = None):
        UserJoinRecord = await self._get_user_record_model()
        if not UserJoinRecord:
            return None
        try:
            now = timezone.now()
            avatar_url = str(member.display_avatar.url)
            avatar_hash = hashlib.md5(avatar_url.encode()).hexdigest()

            if is_join:
                existing = await UserJoinRecord.objects.filter(
                    discord_id=member.id
                ).order_by('-joined_at').afirst()

                if existing:
                    if existing.is_active:
                        existing.username = member.name
                        existing.global_name = member.global_name
                        existing.display_name = member.display_name
                        existing.avatar_url = avatar_url
                        existing.avatar_hash = avatar_hash
                        existing.last_seen = now
                        await existing.asave()
                        return existing
                    else:
                        existing.is_active = True
                        existing.membership_status = 'ACTIVE'
                        existing.join_count += 1
                        existing.current_streak_start = now
                        existing.left_at = None
                        existing.username = member.name
                        existing.global_name = member.global_name
                        existing.display_name = member.display_name
                        existing.avatar_url = avatar_url
                        existing.avatar_hash = avatar_hash
                        existing.last_seen = now
                        if welcome_message_id:
                            existing.welcome_message_id = welcome_message_id
                        if join_log_message_id:
                            existing.join_log_message_id = join_log_message_id
                        await existing.asave()
                        return existing
                else:
                    return await UserJoinRecord.objects.acreate(
                        discord_id=member.id,
                        username=member.name,
                        global_name=member.global_name,
                        display_name=member.display_name,
                        avatar_url=avatar_url,
                        avatar_hash=avatar_hash,
                        discriminator=getattr(member, 'discriminator', '0'),
                        first_seen=now,
                        current_streak_start=now,
                        membership_status='ACTIVE',
                        welcome_message_id=welcome_message_id,
                        join_log_message_id=join_log_message_id,
                        is_active=True,
                        flags={
                            'bot': member.bot,
                            'system': member.system if hasattr(member, 'system') else False,
                            'join_method': 'natural',
                            'server_count': member.guild.member_count,
                        }
                    )
            else:
                existing = await UserJoinRecord.objects.filter(
                    discord_id=member.id, is_active=True
                ).afirst()
                if existing:
                    leave_status = 'LEFT'
                    recent_threshold = timezone.now() - timedelta(seconds=10)
                    async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                        if entry.target.id == member.id and entry.created_at >= recent_threshold:
                            leave_status = 'KICKED'
                            existing.flags['kick_reason'] = entry.reason
                            existing.flags['kicked_by'] = str(entry.user.id)
                            break
                    async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                        if entry.target.id == member.id and entry.created_at >= recent_threshold:
                            leave_status = 'BANNED'
                            existing.flags['ban_reason'] = entry.reason
                            existing.flags['banned_by'] = str(entry.user.id)
                            break
                    existing.is_active = False
                    existing.left_at = now
                    existing.last_seen = now
                    existing.membership_status = leave_status
                    await existing.asave()
                    return existing
                return None
        except Exception as e:
            self.logger.error(f"Error creating/updating user record for {member}: {e}")
            return None

    async def _send_welcome_message(self, member: discord.Member) -> tuple:
        try:
            welcome_channel_id = int(await aget_global_preference(disfunction_prefs.WelcomeChannelID.import_path) or 0)
            gatehouse_channel_id = int(await aget_global_preference(disfunction_prefs.GatehouseRolesChannelID.import_path) or 0)
            faq_channel_id = int(await aget_global_preference(disfunction_prefs.FaqChannelID.import_path) or 0)

            welcome_channel = self.client.get_channel(welcome_channel_id)
            if not welcome_channel:
                self.logger.warning(f"Welcome channel {welcome_channel_id} not found")
                return None, None

            UserJoinRecord = await self._get_user_record_model()
            is_returning = False
            if UserJoinRecord:
                prev = await UserJoinRecord.objects.filter(
                    discord_id=member.id, is_active=False
                ).order_by('-left_at').afirst()
                if prev:
                    is_returning = True

            welcome_text = f"Welcome back <@{member.id}>! 👋" if is_returning else f"Welcome <@{member.id}>! 🎉"
            message_content = (
                f"{welcome_text}\n\n"
                f"To access the public area of the BlightVeil server you must go to the "
                f"<#{gatehouse_channel_id}> and follow the instructions provided.\n\n"
                f"If you have any questions about BlightVeil you can ask any of the organization's "
                f"leadership or check out the <#{faq_channel_id}> channel."
            )

            welcome_file = await self._create_welcome_image(member)
            welcome_message = await welcome_channel.send(content=message_content, file=welcome_file)
            return welcome_message.id, welcome_file
        except Exception as e:
            self.logger.error(f"Error sending welcome message: {e}")
            return None, None

    async def _log_member_join(self, member: discord.Member, welcome_file: discord.File | None = None,
                               welcome_message_id: int = None, user_record=None):
        try:
            join_channel_id = int(await aget_global_preference(disfunction_prefs.MemberJoinChannelID.import_path) or 0)
            welcome_channel_id = int(await aget_global_preference(disfunction_prefs.WelcomeChannelID.import_path) or 0)
            gatehouse_channel_id = int(await aget_global_preference(disfunction_prefs.GatehouseRolesChannelID.import_path) or 0)

            join_channel = self.client.get_channel(join_channel_id)
            if not join_channel:
                self.logger.warning(f"Member join channel {join_channel_id} not found")
                return None

            is_returning = user_record and user_record.join_count > 1
            embed = discord.Embed(
                title="📥 Member Joined" + (" (Returning)" if is_returning else ""),
                color=discord.Color.blue() if is_returning else discord.Color.green(),
                timestamp=timezone.now()
            )

            user_display = member.mention
            if member.global_name:
                user_display += f"\n**Global Name:** {member.global_name}"
            if member.display_name and member.display_name != member.name:
                user_display += f"\n**Display Name:** {member.display_name}"

            embed.add_field(name="User", value=user_display, inline=False)
            embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
            embed.add_field(name="ID", value=f"`{member.id}`", inline=True)

            account_created = member.created_at
            if account_created.tzinfo is None:
                account_created = account_created.replace(tzinfo=timezone.utc)
            account_age_days = (timezone.now() - account_created).days
            embed.add_field(
                name="Account Created",
                value=f"<t:{int(member.created_at.timestamp())}:R> ({account_age_days} days)",
                inline=True,
            )
            embed.add_field(name="Join Position", value=f"#{member.guild.member_count}", inline=True)

            if is_returning:
                embed.add_field(name="Returning Member", value=f"**{user_record.join_count - 1}** previous join(s)", inline=True)
                if user_record.longest_streak:
                    embed.add_field(name="Longest Streak", value=f"{user_record.longest_streak.days} day(s)", inline=True)

            embed.add_field(name="Welcome Channel", value=f"<#{welcome_channel_id}>", inline=True)
            embed.add_field(name="Instructions Channel", value=f"<#{gatehouse_channel_id}>", inline=True)

            if member.bot:
                embed.add_field(name="🤖 Bot Account", value="Yes", inline=True)

            embed.set_thumbnail(url=member.display_avatar.url)
            footer = f"User ID: {member.id}"
            if user_record:
                footer += f" | Join #{user_record.join_count}"
            embed.set_footer(text=footer)

            if welcome_file:
                log_message = await join_channel.send(embed=embed, file=welcome_file)
            else:
                log_message = await join_channel.send(embed=embed)
            return log_message.id
        except Exception as e:
            self.logger.error(f"Error logging member join: {e}")
            return None

    async def _log_member_leave(self, member: discord.Member, user_record=None):
        try:
            leave_channel_id = int(await aget_global_preference(disfunction_prefs.MemberLeaveChannelID.import_path) or 0)
            leave_channel = self.client.get_channel(leave_channel_id)
            if not leave_channel:
                self.logger.warning(f"Member leave channel {leave_channel_id} not found")
                return

            leave_status = 'LEFT'
            kicked_by = banned_by = leave_reason = None
            recent_threshold = timezone.now() - timedelta(seconds=10)

            async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if entry.target.id == member.id and entry.created_at >= recent_threshold:
                    leave_status, kicked_by, leave_reason = 'KICKED', entry.user, entry.reason
                    break
            async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if entry.target.id == member.id and entry.created_at >= recent_threshold:
                    leave_status, banned_by, leave_reason = 'BANNED', entry.user, entry.reason
                    break

            color_map = {'KICKED': discord.Color.orange(), 'BANNED': discord.Color.red()}
            title_map = {'KICKED': "🚫 Member Kicked", 'BANNED': "🔨 Member Banned"}
            embed = discord.Embed(
                title=title_map.get(leave_status, "🚪 Member Left"),
                color=color_map.get(leave_status, discord.Color.dark_gray()),
                timestamp=timezone.now()
            )

            user_display = member.mention
            if member.global_name:
                user_display += f"\n**Global Name:** {member.global_name}"
            embed.add_field(name="User", value=user_display, inline=False)
            embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
            embed.add_field(name="ID", value=f"`{member.id}`", inline=True)

            if member.joined_at:
                joined_tz = member.joined_at
                if joined_tz.tzinfo is None:
                    joined_tz = joined_tz.replace(tzinfo=timezone.utc)
                embed.add_field(name="Membership Duration", value=f"{(timezone.now() - joined_tz).days} day(s)", inline=True)
                embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)

            account_created = member.created_at
            if account_created.tzinfo is None:
                account_created = account_created.replace(tzinfo=timezone.utc)
            embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)

            if leave_status in ('KICKED', 'BANNED'):
                action_by = kicked_by if leave_status == 'KICKED' else banned_by
                embed.add_field(name=f"{leave_status.title()} by", value=action_by.mention, inline=True)
                if leave_reason:
                    embed.add_field(name="Reason", value=leave_reason, inline=False)

            if user_record:
                if user_record.join_count > 1:
                    embed.add_field(name="Total Joins", value=f"{user_record.join_count} time(s)", inline=True)
                if user_record.message_count > 0:
                    embed.add_field(name="Messages Sent", value=str(user_record.message_count), inline=True)
                if user_record.last_message_at:
                    embed.add_field(name="Last Message", value=f"<t:{int(user_record.last_message_at.timestamp())}:R>", inline=True)

            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"User ID: {member.id} | Server Members: {member.guild.member_count}")
            await leave_channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error logging member leave: {e}")

    # ============= CONTEXT MENU COMMANDS =============

    async def _user_info_context_menu(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        UserJoinRecord = await self._get_user_record_model()
        if not UserJoinRecord:
            await interaction.followup.send("❌ Database model not available", ephemeral=True)
            return
        try:
            user_record = await UserJoinRecord.objects.filter(
                discord_id=member.id
            ).order_by('-joined_at').afirst()

            if not user_record:
                embed = await self._create_basic_user_embed(member)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            embed = await self._create_detailed_user_embed(member, user_record)
            view = discord.ui.View(timeout=60)

            refresh_button = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
            async def refresh_callback(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                updated = await UserJoinRecord.objects.filter(discord_id=member.id).order_by('-joined_at').afirst()
                new_embed = await self._create_detailed_user_embed(member, updated or user_record)
                await interaction.edit_original_response(embed=new_embed, view=view)
            refresh_button.callback = refresh_callback
            view.add_item(refresh_button)

            voice_button = discord.ui.Button(label="🎤 Voice Stats", style=discord.ButtonStyle.primary)
            async def voice_stats_callback(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                voice_embed = await self._create_voice_stats_embed(member)
                await interaction.followup.send(embed=voice_embed, ephemeral=True)
            voice_button.callback = voice_stats_callback
            view.add_item(voice_button)

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in user info context menu: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    async def _create_voice_stats_embed(self, member: discord.Member) -> discord.Embed:
        try:
            VoiceSession = apps.get_model('disfunction', 'VoiceSession')
            week_ago = timezone.now() - timedelta(days=7)
            total_time = talking_time = session_count = 0
            async for session in VoiceSession.objects.filter(
                user_id=member.id, guild_id=member.guild.id, join_time__gte=week_ago
            ):
                total_time += session.duration_seconds or 0
                talking_time += session.talking_time_seconds
                session_count += 1

            def fmt(s):
                h, m = s // 3600, (s % 3600) // 60
                return f"{h}h {m}m" if h > 0 else f"{m}m"

            embed = discord.Embed(
                title=f"🎤 Voice Statistics for {member.display_name}",
                color=discord.Color.blue(),
                timestamp=timezone.now()
            )
            if total_time > 0:
                talking_pct = (talking_time / total_time) * 100
                embed.add_field(
                    name="Last 7 Days",
                    value=(
                        f"**Total Time:** {fmt(total_time)}\n"
                        f"**Talking Time:** {fmt(talking_time)}\n"
                        f"**Sessions:** {session_count}\n"
                        f"**Active Ratio:** {talking_pct:.1f}%"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Daily Average",
                    value=f"**Time:** {fmt(int(total_time / min(session_count, 7)))}\n**Sessions:** {session_count / 7:.1f}/day",
                    inline=True,
                )
            else:
                embed.description = "No voice activity recorded in the last 7 days."
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"User ID: {member.id}")
            return embed
        except Exception as e:
            self.logger.error(f"Error creating voice stats embed: {e}")
            return discord.Embed(
                title="🎤 Voice Statistics",
                description="Error loading voice statistics. Try using `/voice_stats` command.",
                color=discord.Color.red()
            )

    async def _message_info_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer(ephemeral=True)
        UserJoinRecord = await self._get_user_record_model()
        if not UserJoinRecord:
            await interaction.followup.send("❌ Database model not available", ephemeral=True)
            return
        try:
            author = message.author
            if isinstance(author, discord.User):
                await interaction.followup.send("❌ Cannot get info for users not in this server", ephemeral=True)
                return

            user_record = await UserJoinRecord.objects.filter(
                discord_id=author.id
            ).order_by('-joined_at').afirst()

            embed = discord.Embed(title="📨 Message Info", color=discord.Color.blue(), timestamp=timezone.now())
            embed.add_field(name="Author", value=author.mention, inline=False)
            embed.add_field(name="Message ID", value=f"`{message.id}`", inline=True)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(name="Sent", value=f"<t:{int(message.created_at.timestamp())}:R>", inline=True)

            content = message.content
            if not content and message.embeds:
                content = "[Embedded Content]"
            elif not content and message.attachments:
                content = f"[{len(message.attachments)} Attachment(s)]"
            elif not content:
                content = "[No Content]"
            if len(content) > 500:
                content = content[:497] + "..."
            embed.add_field(name="Content", value=content, inline=False)

            if user_record:
                embed.add_field(name="User Status", value=user_record.membership_status, inline=True)
                embed.add_field(name="Join Count", value=str(user_record.join_count), inline=True)
                embed.add_field(name="Messages Sent", value=str(user_record.message_count), inline=True)

            embed.add_field(name="Edited", value="✅" if message.edited_at else "❌", inline=True)
            if message.reactions:
                embed.add_field(name="Total Reactions", value=str(sum(r.count for r in message.reactions)), inline=True)

            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
            embed.set_footer(text=f"Author ID: {author.id}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in message info context menu: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    async def _create_basic_user_embed(self, member: discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title=f"👤 User Info: {member.display_name}",
            color=discord.Color.blue(),
            timestamp=timezone.now()
        )
        embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        if member.global_name:
            embed.add_field(name="Global Name", value=member.global_name, inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        if member.joined_at:
            embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
        if len(member.roles) > 1:
            roles = [r.mention for r in member.roles[1:6]]
            if len(member.roles) > 6:
                roles.append(f"... (+{len(member.roles) - 6} more)")
            embed.add_field(name="Top Roles", value=" ".join(roles), inline=False)
        status_emoji = {'online': '🟢', 'idle': '🟡', 'dnd': '🔴', 'offline': '⚫'}
        status = str(member.status)
        embed.add_field(name="Status", value=f"{status_emoji.get(status, '❓')} {status.title()}", inline=True)
        if member.bot:
            embed.add_field(name="🤖 Bot", value="Yes", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="No database record found")
        return embed

    async def _create_detailed_user_embed(self, member: discord.Member, user_record) -> discord.Embed:
        embed = discord.Embed(
            title=f"📊 User Record: {user_record.discord_tag}",
            color=discord.Color.green() if user_record.is_active else discord.Color.dark_gray(),
            timestamp=timezone.now()
        )
        embed.add_field(name="Discord ID", value=f"`{user_record.discord_id}`", inline=False)

        user_display = member.mention
        if member.global_name:
            user_display += f"\n**Global Name:** {member.global_name}"
        if member.display_name and member.display_name != member.name:
            user_display += f"\n**Display Name:** {member.display_name}"
        embed.add_field(name="User", value=user_display, inline=False)

        status_badge = "🟢" if user_record.is_active else "🔴"
        embed.add_field(name="Status", value=f"{status_badge} {user_record.membership_status}", inline=True)
        embed.add_field(name="Active", value="✅" if user_record.is_active else "❌", inline=True)

        account_created = member.created_at
        if account_created.tzinfo is None:
            account_created = account_created.replace(tzinfo=timezone.utc)
        embed.add_field(name="Account Age", value=f"{(timezone.now() - account_created).days} day(s)", inline=True)
        embed.add_field(name="First Seen", value=f"<t:{int(user_record.first_seen.timestamp())}:R>" if user_record.first_seen else "Unknown", inline=True)
        embed.add_field(name="Join Count", value=str(user_record.join_count), inline=True)

        if user_record.current_streak_start:
            embed.add_field(name="Current Streak", value=f"{(timezone.now() - user_record.current_streak_start).days} day(s)", inline=True)
        if user_record.longest_streak:
            embed.add_field(name="Longest Streak", value=f"{user_record.longest_streak.days} day(s)", inline=True)

        embed.add_field(name="Messages Sent", value=str(user_record.message_count), inline=True)
        if user_record.last_message_at:
            embed.add_field(name="Last Message", value=f"<t:{int(user_record.last_message_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Last Seen", value=f"<t:{int(user_record.last_seen.timestamp())}:R>", inline=True)

        if member.joined_at:
            joined_tz = member.joined_at
            if joined_tz.tzinfo is None:
                joined_tz = joined_tz.replace(tzinfo=timezone.utc)
            embed.add_field(name="Server Member For", value=f"{(timezone.now() - joined_tz).days} day(s)", inline=True)

        if member.bot:
            embed.add_field(name="🤖 Bot Account", value="Yes", inline=True)
        if user_record.avatar_hash:
            embed.add_field(name="Avatar Hash", value=f"`{user_record.avatar_hash[:8]}...`", inline=True)
        if user_record.flags and isinstance(user_record.flags, dict) and user_record.flags.get('bot'):
            embed.add_field(name="Account Type", value="🤖 Bot", inline=True)

        embed = await self._add_voice_stats_to_embed(embed, member, user_record)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Record ID: {user_record.id} | Updated: {user_record.last_updated.strftime('%Y-%m-%d %H:%M')}")
        return embed

    async def _add_voice_stats_to_embed(self, embed: discord.Embed, member: discord.Member, user_record) -> discord.Embed:
        try:
            VoiceSession = apps.get_model('disfunction', 'VoiceSession')
            week_ago = timezone.now() - timedelta(days=7)
            total_time = talking_time = session_count = 0
            async for session in VoiceSession.objects.filter(
                user_id=member.id, guild_id=member.guild.id, join_time__gte=week_ago
            ):
                total_time += session.duration_seconds or 0
                talking_time += session.talking_time_seconds
                session_count += 1

            if total_time > 0:
                def fmt(s):
                    h, m = s // 3600, (s % 3600) // 60
                    return f"{h}h {m}m" if h > 0 else f"{m}m"
                talking_pct = (talking_time / total_time) * 100
                embed.add_field(
                    name="🎤 Voice Activity (7 days)",
                    value=(
                        f"**Total Time:** {fmt(total_time)}\n"
                        f"**Talking Time:** {fmt(talking_time)}\n"
                        f"**Sessions:** {session_count}\n"
                        f"**Active Ratio:** {talking_pct:.1f}%"
                    ),
                    inline=False,
                )
        except Exception as e:
            self.logger.error(f"Error adding voice stats: {e}")
        return embed

    # ============= EVENT HANDLERS =============

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.logger.info(f"Member joined: {member} ({member.id})")
        try:
            welcome_message_id, welcome_file = await self._send_welcome_message(member)
            user_record = await self._create_or_update_user_record(member, is_join=True, welcome_message_id=welcome_message_id)
            await self._log_member_join(member, welcome_file, welcome_message_id, user_record)
        except Exception as e:
            self.logger.error(f"Error in member join process for {member}: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.logger.info(f"Member left: {member} ({member.id})")
        try:
            user_record = await self._create_or_update_user_record(member, is_join=False)
            await self._log_member_leave(member, user_record)
        except Exception as e:
            self.logger.error(f"Error in member leave process for {member}: {e}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        self.logger.info(f"Member banned: {user} ({user.id})")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        self.logger.info(f"Member unbanned: {user} ({user.id})")
        try:
            leave_channel_id = int(await aget_global_preference(disfunction_prefs.MemberLeaveChannelID.import_path) or 0)
            leave_channel = self.client.get_channel(leave_channel_id)
            if leave_channel:
                embed = discord.Embed(title="🔓 Member Unbanned", color=discord.Color.green(), timestamp=timezone.now())
                embed.add_field(name="User", value=f"{user.mention} ({user})", inline=False)
                embed.add_field(name="ID", value=f"`{user.id}`", inline=False)
                await leave_channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error logging unban: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.author, discord.Member):
            return
        try:
            UserJoinRecord = await self._get_user_record_model()
            if not UserJoinRecord:
                return
            user_record = await UserJoinRecord.objects.filter(
                discord_id=message.author.id, is_active=True
            ).afirst()
            if user_record:
                user_record.message_count += 1
                user_record.last_message_at = message.created_at
                await user_record.asave(update_fields=['message_count', 'last_message_at'])
        except Exception as e:
            self.logger.error(f"Error tracking message for {message.author}: {e}")

    # ============= SLASH COMMANDS =============

    @app_commands.command(name="adm_show_welcome_banner", description="Display a Member's welcome banner [admin]")
    @requires_django_perm("org.can_manage_discord_members")
    async def cmd_show_welcome_banner(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        ephemeral: bool = True,
    ):
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        welcome_file = await self._create_welcome_image(member)
        await interaction.followup.send(file=welcome_file, ephemeral=ephemeral)

    @app_commands.command(name="userinfo", description="View detailed user information from database")
    @requires_django_perm("org.can_manage_discord_members")
    async def user_info_slash(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        await self._user_info_context_menu(interaction, member)


async def setup(client):
    await client.add_cog(WelcomeSystem(client))

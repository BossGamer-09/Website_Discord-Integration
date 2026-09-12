"""
app/schedevents/cogs/event_rsvp.py

Persistent RSVP button view + the EventRSVPCog that handles all RSVP interactions.

RSVPView is a persistent discord.ui.View — buttons survive bot restarts.
Custom IDs encode the codename_id so no in-memory state is needed.

  sched_rsvp_going_{codename_id}
  sched_rsvp_maybe_{codename_id}
  sched_rsvp_notgoing_{codename_id}

EventRSVPCog registers all persistent views on startup by scanning active
EventPlan rows whose status is PUBLISHED or ACTIVE.
"""
import logging

import discord
from discord.ext import commands, tasks
from django.apps import apps
from django.utils import timezone

log = logging.getLogger(__name__)

GOING_PREFIX    = "sched_rsvp_going_"
MAYBE_PREFIX    = "sched_rsvp_maybe_"
NOTGOING_PREFIX = "sched_rsvp_notgoing_"
OPTION_PREFIX   = "sched_rsvp_opt_"
UNRSVP_PREFIX   = "sched_rsvp_unrsvp_"
REMIND_PREFIX   = "sched_rsvp_remind_"


# ---------------------------------------------------------------------------
# Persistent RSVP View — standard 3-button (no custom options)
# ---------------------------------------------------------------------------

class RSVPView(discord.ui.View):
    """
    Three-button RSVP panel attached to announcement embeds.
    is_closed=True disables all buttons (used for COMPLETED/CANCELLED events).
    """
    def __init__(self, codename_id: str, is_closed: bool = False):
        super().__init__(timeout=None)  # persistent

        going_btn = discord.ui.Button(
            label="Going",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{GOING_PREFIX}{codename_id}",
            disabled=is_closed,
        )
        maybe_btn = discord.ui.Button(
            label="Maybe",
            style=discord.ButtonStyle.secondary,
            emoji="❓",
            custom_id=f"{MAYBE_PREFIX}{codename_id}",
            disabled=is_closed,
        )
        notgoing_btn = discord.ui.Button(
            label="Not Going",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{NOTGOING_PREFIX}{codename_id}",
            disabled=is_closed,
        )

        self.add_item(going_btn)
        self.add_item(maybe_btn)
        self.add_item(notgoing_btn)


# ---------------------------------------------------------------------------
# RSVPOptionView — dynamic buttons built from RSVPOption records
# ---------------------------------------------------------------------------

class RSVPOptionView(discord.ui.View):
    """
    Button-based RSVP panel where each button corresponds to an RSVPOption.
    Custom IDs: sched_rsvp_opt_{codename_id}_{option_pk}
    codename_id uses CamelCase (no underscores), so the last "_N" is the PK.
    """
    def __init__(self, codename_id: str, options, is_closed: bool = False):
        super().__init__(timeout=None)
        for opt in options[:25]:  # Discord max 25 components per view
            style = {
                "GOING":     discord.ButtonStyle.success,
                "MAYBE":     discord.ButtonStyle.secondary,
                "NOT_GOING": discord.ButtonStyle.danger,
            }.get(opt.maps_to_status, discord.ButtonStyle.primary)
            self.add_item(discord.ui.Button(
                label=opt.label,
                emoji=opt.emoji or None,
                custom_id=f"{OPTION_PREFIX}{codename_id}_{opt.pk}",
                style=style,
                disabled=is_closed,
            ))


# ---------------------------------------------------------------------------
# RSVPConfirmView — persistent buttons shown on the Sesh-style DM confirmation
# ---------------------------------------------------------------------------

class RSVPConfirmView(discord.ui.View):
    """🔔 Reminders + UnRSVP buttons attached to the DM confirmation embed."""
    def __init__(self, codename_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Reminders",
            style=discord.ButtonStyle.secondary,
            emoji="🔔",
            custom_id=f"{REMIND_PREFIX}{codename_id}",
        ))
        self.add_item(discord.ui.Button(
            label="UnRSVP",
            style=discord.ButtonStyle.danger,
            custom_id=f"{UNRSVP_PREFIX}{codename_id}",
        ))


def build_rsvp_confirmation_embed(plan, option=None, status: str = "GOING") -> discord.Embed:
    """Sesh-style DM confirmation embed."""
    ts = int(plan.planned_start_at.timestamp())

    if status == "WAITLISTED":
        role_str = f"the **waitlist** for {option.emoji} {option.label}" if option else "the **waitlist**"
        desc = f"You have joined {role_str} for **{plan.title}**!"
    elif status == "PENDING":
        role_str = f"{option.emoji} {option.label} " if option else ""
        desc = f"Your {role_str}RSVP for **{plan.title}** is **pending approval**."
    elif status == "MAYBE":
        role_str = f"{option.emoji} {option.label} " if option else ""
        desc = f"You are **tentatively** signed up as {role_str}for **{plan.title}**."
    else:
        role_str = f"{option.emoji} {option.label} " if option else ""
        desc = f"You have joined {role_str}for **{plan.title}**!"

    embed = discord.Embed(
        title="RSVP Confirmation",
        description=desc,
        color=plan.indicator.discord_color,
    )
    embed.add_field(
        name="Event time",
        value=f"<t:{ts}:F> (<t:{ts}:R>)",
        inline=False,
    )
    embed.set_footer(
        text="Use /event info in this server to see details.\n"
             "Reply /list in DM to see your personal schedule.",
    )
    return embed


# ---------------------------------------------------------------------------
# Helper — pick the right view for a plan (async, hits DB)
# ---------------------------------------------------------------------------

async def get_rsvp_view(plan, is_closed: bool = False):
    """
    Returns RSVPOptionView if the event has active RSVPOptions (per-event first,
    then guild-wide defaults), otherwise returns the standard RSVPView.
    """
    RSVPOption = apps.get_model("schedevents", "RSVPOption")

    options = [o async for o in RSVPOption.objects.filter(
        event=plan, is_active=True,
    ).order_by("sort_order", "id")]

    if not options:
        options = [o async for o in RSVPOption.objects.filter(
            guild_id=plan.guild_id, event=None, is_active=True,
        ).order_by("sort_order", "id")]

    if options:
        return RSVPOptionView(plan.codename_id, options, is_closed=is_closed)
    return RSVPView(plan.codename_id, is_closed=is_closed)


# ---------------------------------------------------------------------------
# EventRSVPCog
# ---------------------------------------------------------------------------

class EventRSVPCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.EventPlan = apps.get_model("schedevents", "EventPlan")
        self.EventRSVP = apps.get_model("schedevents", "EventRSVP")
        self.startup_task.start()

    def cog_unload(self):
        self.startup_task.cancel()

    # ------------------------------------------------------------------ #
    # Startup — register all active persistent views
    # ------------------------------------------------------------------ #

    @tasks.loop(count=1)
    async def startup_task(self):
        await self.client.wait_until_ready()
        count = 0
        async for plan in self.EventPlan.objects.filter(
            status__in=[
                self.EventPlan.Status.PUBLISHED,
                self.EventPlan.Status.ACTIVE,
            ]
        ):
            view = await get_rsvp_view(plan)
            self.client.add_view(view)
            count += 1
        self.logger.info("EventRSVPCog: registered %d persistent RSVP views", count)

    # ------------------------------------------------------------------ #
    # Interaction dispatcher — catches all sched_rsvp_* button interactions
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")

        if custom_id.startswith(GOING_PREFIX):
            codename_id = custom_id[len(GOING_PREFIX):]
            await self._handle_rsvp(interaction, codename_id, "GOING")

        elif custom_id.startswith(MAYBE_PREFIX):
            codename_id = custom_id[len(MAYBE_PREFIX):]
            await self._handle_rsvp(interaction, codename_id, "MAYBE")

        elif custom_id.startswith(NOTGOING_PREFIX):
            codename_id = custom_id[len(NOTGOING_PREFIX):]
            await self._handle_rsvp(interaction, codename_id, "NOT_GOING")

        elif custom_id.startswith(OPTION_PREFIX):
            # custom_id = "sched_rsvp_opt_{CamelCaseCodename}_{option_pk}"
            # codename_id is CamelCase (no underscores), so split on last "_"
            rest = custom_id[len(OPTION_PREFIX):]
            sep = rest.rfind("_")
            if sep == -1:
                return
            codename_id = rest[:sep]
            option_pk = rest[sep + 1:]
            await self._handle_rsvp_option(interaction, codename_id, option_pk)

        elif custom_id.startswith(UNRSVP_PREFIX):
            codename_id = custom_id[len(UNRSVP_PREFIX):]
            await self._handle_unrsvp(interaction, codename_id)

        elif custom_id.startswith(REMIND_PREFIX):
            codename_id = custom_id[len(REMIND_PREFIX):]
            await self._handle_reminders_button(interaction, codename_id)

    # ------------------------------------------------------------------ #
    # Core RSVP handler
    # ------------------------------------------------------------------ #

    async def _handle_rsvp(
        self,
        interaction: discord.Interaction,
        codename_id: str,
        new_status: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # 1. Load event plan
        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=codename_id,
            guild_id=interaction.guild.id,
        ).afirst()

        if not plan:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        if plan.status in (self.EventPlan.Status.COMPLETED, self.EventPlan.Status.CANCELLED):
            await interaction.followup.send(
                f"❌ This event is {plan.get_status_display()} — RSVPs are closed.",
                ephemeral=True,
            )
            return

        # 2. Check RSVP deadline
        if plan.rsvp_deadline and timezone.now() > plan.rsvp_deadline:
            await interaction.followup.send(
                "❌ The RSVP deadline for this event has passed.",
                ephemeral=True,
            )
            return

        # 3. Resolve OrgPlayer
        org_user = await self._get_org_user(interaction.user.id)
        if not org_user:
            await interaction.followup.send(
                "❌ Your Discord account isn't linked to a Blightveil profile. "
                "Please complete registration first.",
                ephemeral=True,
            )
            return

        # 4. Load existing RSVP (if any)
        existing = await self.EventRSVP.objects.filter(
            event=plan, user=org_user
        ).afirst()

        # If user is toggling the same status → remove RSVP
        if existing and existing.status == new_status:
            await existing.adelete()
            await interaction.followup.send(
                f"🔄 Your RSVP for **{plan.title}** has been removed.",
                ephemeral=True,
            )
            return

        # 5. Handle capacity + waitlist for GOING
        if new_status == "GOING":
            result_status = await self._resolve_going_status(plan, existing)
        else:
            result_status = new_status

        # 6. If user was GOING and now switching to NOT_GOING → promote waitlist
        was_going = existing and existing.status == "GOING"

        if existing:
            old_status = existing.status
            existing.status = result_status
            existing.rsvp_time = timezone.now()
            await existing.asave(update_fields=["status", "rsvp_time"])
        else:
            old_status = None
            existing = await self.EventRSVP.objects.acreate(
                event=plan,
                user=org_user,
                status=result_status,
            )

        # 7. Promote waitlist if a GOING slot opened up
        if was_going and new_status == "NOT_GOING":
            await self._promote_waitlist(plan)

        # 8. Send approval/require-approval flow
        if result_status == "PENDING":
            await interaction.followup.send(
                f"🕐 Your RSVP for **{plan.title}** is **pending approval** by event staff.",
                ephemeral=True,
            )
            await self._notify_organizers_pending(plan, interaction.user)
            return

        # 9. Confirm to user — NOT_GOING gets a plain ack; others get the DM embed
        if result_status == "NOT_GOING":
            await interaction.followup.send(
                f"❌ Marked as **Not Going** for **{plan.title}**.", ephemeral=True
            )
            return
        await self._send_rsvp_confirmation(interaction, plan, option=None, status=result_status)

    # ------------------------------------------------------------------ #
    # Option-based RSVP handler
    # ------------------------------------------------------------------ #

    async def _handle_rsvp_option(
        self,
        interaction: discord.Interaction,
        codename_id: str,
        option_pk: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        RSVPOption = apps.get_model("schedevents", "RSVPOption")

        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=codename_id, guild_id=interaction.guild.id
        ).afirst()
        if not plan:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        if plan.status in (self.EventPlan.Status.COMPLETED, self.EventPlan.Status.CANCELLED):
            await interaction.followup.send(
                f"❌ This event is {plan.get_status_display()} — RSVPs are closed.", ephemeral=True
            )
            return

        if plan.rsvp_deadline and timezone.now() > plan.rsvp_deadline:
            await interaction.followup.send("❌ The RSVP deadline has passed.", ephemeral=True)
            return

        try:
            option = await RSVPOption.objects.aget(pk=int(option_pk), is_active=True)
        except (RSVPOption.DoesNotExist, ValueError):
            await interaction.followup.send("❌ RSVP option not found.", ephemeral=True)
            return

        org_user = await self._get_org_user(interaction.user.id)
        if not org_user:
            await interaction.followup.send(
                "❌ Your Discord account isn't linked to a Blightveil profile. "
                "Please complete registration first.",
                ephemeral=True,
            )
            return

        existing = await self.EventRSVP.objects.filter(event=plan, user=org_user).afirst()

        # Toggle off if same option clicked again
        if existing and existing.rsvp_option_id == option.pk:
            await existing.adelete()
            await interaction.followup.send(
                f"🔄 Your RSVP for **{plan.title}** has been removed.", ephemeral=True
            )
            return

        # Derive system status from option
        new_status = option.maps_to_status

        # Check per-option capacity first
        if option.capacity_limit is not None:
            option_going = await self.EventRSVP.objects.filter(
                event=plan, rsvp_option=option,
                status__in=[self.EventRSVP.RSVPStatus.GOING, self.EventRSVP.RSVPStatus.MAYBE],
            ).acount()
            if option_going >= option.capacity_limit:
                if plan.waitlist_enabled:
                    new_status = self.EventRSVP.RSVPStatus.WAITLISTED
                else:
                    await interaction.followup.send(
                        f"❌ **{option.emoji} {option.label}** is full.", ephemeral=True
                    )
                    return

        # For GOING-mapped options also check overall event capacity
        if new_status == self.EventRSVP.RSVPStatus.GOING:
            if plan.capacity is not None:
                current_going = await self.EventRSVP.objects.filter(
                    event=plan, status=self.EventRSVP.RSVPStatus.GOING
                ).acount()
                is_already_going = existing and existing.status == self.EventRSVP.RSVPStatus.GOING
                if not is_already_going and current_going >= plan.capacity:
                    if plan.waitlist_enabled:
                        new_status = self.EventRSVP.RSVPStatus.WAITLISTED
                    else:
                        await interaction.followup.send("❌ Event is full.", ephemeral=True)
                        return

        was_going = existing and existing.status == self.EventRSVP.RSVPStatus.GOING

        if existing:
            existing.status = new_status
            existing.rsvp_option = option
            existing.rsvp_time = timezone.now()
            await existing.asave(update_fields=["status", "rsvp_option", "rsvp_time"])
        else:
            existing = await self.EventRSVP.objects.acreate(
                event=plan, user=org_user, status=new_status, rsvp_option=option,
            )

        if was_going and new_status != self.EventRSVP.RSVPStatus.GOING:
            await self._promote_waitlist(plan)

        if new_status == self.EventRSVP.RSVPStatus.PENDING:
            await interaction.followup.send(
                f"🕐 Your RSVP for **{plan.title}** as **{option.label}** is pending approval.",
                ephemeral=True,
            )
            await self._notify_organizers_pending(plan, interaction.user)
            return

        if new_status == self.EventRSVP.RSVPStatus.NOT_GOING:
            await interaction.followup.send(
                f"❌ Marked as **Not Going** for **{plan.title}**.", ephemeral=True
            )
            return
        await self._send_rsvp_confirmation(interaction, plan, option=option, status=new_status)

    # ------------------------------------------------------------------ #
    # Capacity / waitlist helpers
    # ------------------------------------------------------------------ #

    async def _resolve_going_status(self, plan, existing_rsvp) -> str:
        """Returns the effective RSVP status for a GOING request."""
        if plan.capacity is None:
            return self.EventRSVP.RSVPStatus.GOING  # unlimited

        current_going = await self.EventRSVP.objects.filter(
            event=plan, status=self.EventRSVP.RSVPStatus.GOING
        ).acount()

        # If user is already GOING, the slot count won't change
        if existing_rsvp and existing_rsvp.status == self.EventRSVP.RSVPStatus.GOING:
            return self.EventRSVP.RSVPStatus.GOING

        if current_going < plan.capacity:
            return self.EventRSVP.RSVPStatus.GOING

        if plan.waitlist_enabled:
            return self.EventRSVP.RSVPStatus.WAITLISTED

        return self.EventRSVP.RSVPStatus.NOT_GOING  # full, no waitlist

    async def _promote_waitlist(self, plan):
        """Promotes the first WAITLISTED RSVP to GOING when a slot opens."""
        if plan.capacity is None:
            return

        current_going = await self.EventRSVP.objects.filter(
            event=plan, status=self.EventRSVP.RSVPStatus.GOING
        ).acount()

        if current_going >= plan.capacity:
            return

        next_in_line = await self.EventRSVP.objects.filter(
            event=plan, status=self.EventRSVP.RSVPStatus.WAITLISTED
        ).select_related("user__discorduser").order_by("rsvp_time").afirst()

        if not next_in_line:
            return

        next_in_line.status = self.EventRSVP.RSVPStatus.GOING
        await next_in_line.asave(update_fields=["status"])

        # DM the promoted user
        guild = self.client.get_guild(plan.guild_id)
        if guild:
            discord_id = getattr(getattr(next_in_line.user, "discorduser", None), "discorduid", None)
            member = guild.get_member(discord_id) if discord_id else None
            if member:
                ts = int(plan.planned_start_at.timestamp())
                try:
                    await member.send(
                        f"🎉 A spot opened up! You've been moved from the waitlist to **Going** "
                        f"for **{plan.title}** (<t:{ts}:F>)."
                    )
                except discord.Forbidden:
                    pass

        self.logger.info(
            "Promoted %s from waitlist to GOING for event %s",
            next_in_line.user_id, plan.codename_id,
        )

    async def _waitlist_position(self, plan, org_user) -> int:
        return await self.EventRSVP.objects.filter(
            event=plan,
            status=self.EventRSVP.RSVPStatus.WAITLISTED,
            rsvp_time__lt=timezone.now(),
        ).acount()

    async def _notify_organizers_pending(self, plan, discord_user: discord.User):
        """Notify event organizers that a pending RSVP needs approval."""
        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        from app.preferences.utils import aget_global_preference
        from app.schedevents import preferences as prefs

        staff_log_id = await aget_global_preference(prefs.EventStaffLogChannelID.import_path)
        if not staff_log_id:
            return

        ch = guild.get_channel(int(staff_log_id))
        if not ch:
            return

        embed = discord.Embed(
            title="🕐 Pending RSVP Approval",
            description=(
                f"{discord_user.mention} has requested to join "
                f"**{plan.title}** (`{plan.codename_id}`) and is pending approval."
            ),
            color=discord.Color.orange(),
        )
        view = _PendingApprovalView(
            cog=self,
            codename_id=plan.codename_id,
            user_discord_id=discord_user.id,
        )
        await ch.send(embed=embed, view=view)

    # ------------------------------------------------------------------ #
    # Pending RSVP approval/denial (staff action)
    # ------------------------------------------------------------------ #

    async def approve_pending_rsvp(
        self,
        interaction: discord.Interaction,
        codename_id: str,
        user_discord_id: int,
    ):
        await interaction.response.defer(ephemeral=True)

        org_user = await self._get_org_user(user_discord_id)
        if not org_user:
            await interaction.followup.send("❌ User not found.", ephemeral=True)
            return

        plan = await self.EventPlan.objects.afirst_or_none(codename_id=codename_id)  # type: ignore[attr-defined]
        if not plan:
            plan = await self.EventPlan.objects.filter(codename_id=codename_id).afirst()
        if not plan:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        rsvp = await self.EventRSVP.objects.filter(
            event=plan, user=org_user, status=self.EventRSVP.RSVPStatus.PENDING
        ).afirst()
        if not rsvp:
            await interaction.followup.send("❌ No pending RSVP found.", ephemeral=True)
            return

        rsvp.status = self.EventRSVP.RSVPStatus.GOING
        await rsvp.asave(update_fields=["status"])

        # Notify user
        guild = self.client.get_guild(plan.guild_id)
        if guild:
            member = guild.get_member(user_discord_id)
            if member:
                ts = int(plan.planned_start_at.timestamp())
                try:
                    await member.send(
                        f"✅ Your RSVP for **{plan.title}** (<t:{ts}:F>) has been **approved**!"
                    )
                except discord.Forbidden:
                    pass

        await interaction.followup.send("✅ RSVP approved.", ephemeral=True)

    async def deny_pending_rsvp(
        self,
        interaction: discord.Interaction,
        codename_id: str,
        user_discord_id: int,
    ):
        await interaction.response.defer(ephemeral=True)

        org_user = await self._get_org_user(user_discord_id)
        plan = await self.EventPlan.objects.filter(codename_id=codename_id).afirst()

        if org_user and plan:
            rsvp = await self.EventRSVP.objects.filter(
                event=plan, user=org_user, status=self.EventRSVP.RSVPStatus.PENDING
            ).afirst()
            if rsvp:
                rsvp.status = self.EventRSVP.RSVPStatus.NOT_GOING
                await rsvp.asave(update_fields=["status"])

            guild = self.client.get_guild(plan.guild_id)
            if guild:
                member = guild.get_member(user_discord_id)
                if member:
                    try:
                        await member.send(
                            f"❌ Your RSVP request for **{plan.title}** was not approved."
                        )
                    except discord.Forbidden:
                        pass

        await interaction.followup.send("❌ RSVP denied.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _get_org_user(self, discord_user_id: int):
        from app.unifieduser.models import OrgPlayer
        try:
            return await OrgPlayer.objects.aget(discorduser__discorduid=discord_user_id)
        except OrgPlayer.DoesNotExist:
            return None

    # ------------------------------------------------------------------ #
    # Sesh-style DM confirmation
    # ------------------------------------------------------------------ #

    async def _send_rsvp_confirmation(self, interaction, plan, option, status):
        """DM the user a Sesh-style confirmation + Reminders/UnRSVP buttons.
        Falls back to an ephemeral reply if DMs are closed.
        """
        embed = build_rsvp_confirmation_embed(plan, option, status)
        view = RSVPConfirmView(plan.codename_id)
        try:
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send(
                f"✅ RSVP confirmed — confirmation sent to your DMs.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # UnRSVP button (from DM confirmation)
    # ------------------------------------------------------------------ #

    async def _handle_unrsvp(self, interaction: discord.Interaction, codename_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self.EventPlan.objects.filter(codename_id=codename_id).afirst()
        if not plan:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        org_user = await self._get_org_user(interaction.user.id)
        if not org_user:
            await interaction.followup.send("❌ Profile not linked.", ephemeral=True)
            return

        rsvp = await self.EventRSVP.objects.filter(event=plan, user=org_user).afirst()
        if not rsvp:
            await interaction.followup.send(
                f"You have no RSVP for **{plan.title}**.", ephemeral=True
            )
            return

        was_going = rsvp.status == self.EventRSVP.RSVPStatus.GOING
        await rsvp.adelete()
        if was_going:
            await self._promote_waitlist(plan)

        # Disable the buttons on the original DM
        try:
            if interaction.message:
                disabled = RSVPConfirmView(codename_id)
                for item in disabled.children:
                    item.disabled = True
                await interaction.message.edit(view=disabled)
        except (discord.HTTPException, discord.Forbidden):
            pass

        await interaction.followup.send(
            f"🔄 Your RSVP for **{plan.title}** has been removed.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Reminders button (from DM confirmation)
    # ------------------------------------------------------------------ #

    async def _handle_reminders_button(self, interaction: discord.Interaction, codename_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        EventReminder = apps.get_model("schedevents", "EventReminder")
        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=codename_id
        ).afirst()
        if not plan:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        reminders = [r async for r in EventReminder.objects.filter(event=plan).order_by("minutes_before")]

        embed = discord.Embed(
            title=f"🔔 Reminders — {plan.title}",
            color=plan.indicator.discord_color,
        )
        if not reminders:
            embed.description = "No reminders are configured for this event."
        else:
            lines = []
            for r in reminders:
                target = "DM" if r.target == "DM" else f"<#{r.channel_id}>" if r.channel_id else "channel"
                mins = r.minutes_before
                if mins >= 60 and mins % 60 == 0:
                    when = f"{mins // 60}h before"
                else:
                    when = f"{mins}m before"
                lines.append(f"• **{when}** → {target}")
            embed.description = "\n".join(lines)

        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Pending approval buttons view (staff)
# ---------------------------------------------------------------------------

class _PendingApprovalView(discord.ui.View):
    def __init__(self, cog: EventRSVPCog, codename_id: str, user_discord_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.codename_id = codename_id
        self.user_discord_id = user_discord_id

        approve_btn = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"sched_approve_{codename_id}_{user_discord_id}",
        )
        approve_btn.callback = self._approve

        deny_btn = discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"sched_deny_{codename_id}_{user_discord_id}",
        )
        deny_btn.callback = self._deny

        self.add_item(approve_btn)
        self.add_item(deny_btn)

    async def _approve(self, interaction: discord.Interaction):
        await self.cog.approve_pending_rsvp(interaction, self.codename_id, self.user_discord_id)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    async def _deny(self, interaction: discord.Interaction):
        await self.cog.deny_pending_rsvp(interaction, self.codename_id, self.user_discord_id)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)


async def setup(client):
    await client.add_cog(EventRSVPCog(client))

"""discord.py adapter: wires the rules to Discord events, slash commands, buttons and roles."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import ClientConfig
from .rules import Activation, Levels, Moderation, PaidRoles, Verdict, level_for_xp, render, ticket_channel_name
from .store import Store, parse
from .stripe_pay import Stripe, StripeError

log = logging.getLogger(__name__)


# Buttons -----------------------------------------------------------------------------


class TicketPanel(discord.ui.View):
    def __init__(self, bot: "BotziBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Deschide un tichet", style=discord.ButtonStyle.primary, custom_id="botzi:ticket:open", emoji="🎫")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.open_ticket(interaction)


class TicketClose(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Închide tichetul", style=discord.ButtonStyle.secondary, custom_id="botzi:ticket:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.client.close_ticket(interaction)  # type: ignore[attr-defined]


class TriviaView(discord.ui.View):
    def __init__(self, bot: "BotziBot", question: dict):
        super().__init__(timeout=90)
        self.bot, self.question, self.answered, self.wrong = bot, question, False, set()
        self.message: discord.Message | None = None
        for i, option in enumerate(question["options"]):
            self.add_item(TriviaButton(i, option))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message and not self.answered:
            answer = self.question["options"][self.question["answer"]]
            try:
                await self.message.edit(content=f"❓ {self.question['q']}\n⌛ Timpul a expirat. Răspunsul era **{answer}**.", view=self)
            except discord.HTTPException:
                pass


class TriviaButton(discord.ui.Button):
    def __init__(self, index: int, label: str):
        super().__init__(label=f"{'ABCD'[index]}. {label}"[:80], style=discord.ButtonStyle.secondary)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: TriviaView = self.view  # type: ignore[assignment]
        if view.answered:
            await interaction.response.send_message("Cineva a răspuns deja.", ephemeral=True)
            return
        if interaction.user.id in view.wrong:
            await interaction.response.send_message("Ai avut deja o încercare la întrebarea asta.", ephemeral=True)
            return
        if self.index != view.question["answer"]:
            view.wrong.add(interaction.user.id)
            await interaction.response.send_message("Nu e asta. Poate știe altcineva.", ephemeral=True)
            return
        view.answered = True
        for item in view.children:
            item.disabled = True  # type: ignore[attr-defined]
        xp = view.bot.cfg.games.trivia_xp
        total, level_up = view.bot.levels.bonus(interaction.user.id, xp)
        await interaction.response.edit_message(
            content=f"❓ {view.question['q']}\n✅ Corect: **{view.question['options'][self.index]}**. {interaction.user.mention} câștigă {xp} XP!", view=view
        )
        if level_up and isinstance(interaction.user, discord.Member):
            await view.bot.announce_level(interaction.channel, interaction.user, level_up)
        view.stop()


# The bot -----------------------------------------------------------------------------


class BotziBot(commands.Bot):
    def __init__(self, cfg: ClientConfig, store: Store, stripe: Stripe | None = None, public_url: str = ""):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.cfg, self.store = cfg, store
        self.moderation = Moderation(cfg.moderation, store)
        self.levels = Levels(cfg, store)
        self.paid = PaidRoles(cfg, store)
        self.stripe, self.public_url = stripe, public_url.rstrip("/")
        self.guild_obj = discord.Object(id=cfg.guild_id)
        register_commands(self)

    async def setup_hook(self) -> None:
        self.add_view(TicketPanel(self))
        self.add_view(TicketClose())
        self.tree.copy_global_to(guild=self.guild_obj)
        await self.tree.sync(guild=self.guild_obj)
        if self.cfg.paid_roles.enabled:
            self.paid_roles_sweep.start()

    async def on_ready(self) -> None:
        log.info("Conectat ca %s pentru %s (server %s)", self.user, self.cfg.business_name, self.cfg.guild_id)

    # Lookups -------------------------------------------------------------------------

    def guild(self) -> discord.Guild | None:
        return self.get_guild(self.cfg.guild_id)

    @staticmethod
    def channel_named(guild: discord.Guild, name: str | None) -> discord.TextChannel | None:
        return discord.utils.get(guild.text_channels, name=name) if name else None

    @staticmethod
    def role_named(guild: discord.Guild, name: str | None) -> discord.Role | None:
        return discord.utils.get(guild.roles, name=name) if name else None

    async def log_mod(self, guild: discord.Guild, text: str) -> None:
        channel = self.channel_named(guild, self.cfg.moderation.log_channel)
        if channel:
            try:
                await channel.send(text)
            except discord.HTTPException as e:
                log.warning("Nu pot scrie în canalul de log: %s", e)

    # Welcome -------------------------------------------------------------------------

    async def on_member_join(self, member: discord.Member) -> None:
        w = self.cfg.welcome
        if not w.enabled or member.guild.id != self.cfg.guild_id or member.bot:
            return
        rules = self.channel_named(member.guild, w.rules_channel)
        values = dict(mention=member.mention, name=member.display_name, server=member.guild.name, count=member.guild.member_count, rules=rules.mention if rules else f"#{w.rules_channel}")
        channel = self.channel_named(member.guild, w.channel)
        if channel:
            try:
                await channel.send(render(w.message, **values))
            except discord.HTTPException as e:
                log.warning("Nu pot trimite bun venit: %s", e)
        role = self.role_named(member.guild, w.auto_role)
        if role:
            try:
                await member.add_roles(role, reason="rol automat la intrare")
            except discord.HTTPException as e:
                log.warning("Nu pot da rolul automat: %s", e)
        if w.dm_message:
            try:
                await member.send(render(w.dm_message, **values))
            except discord.HTTPException:
                pass

    # Moderation + XP -----------------------------------------------------------------

    def is_exempt(self, member: discord.Member) -> bool:
        return member.guild_permissions.manage_messages or any(r.name in self.cfg.moderation.exempt_roles for r in member.roles)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or message.guild.id != self.cfg.guild_id or not isinstance(message.author, discord.Member):
            return
        member = message.author
        verdict = self.moderation.judge(member.id, message.content, exempt=self.is_exempt(member))
        if verdict.delete:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        if verdict.warn_reason:
            await self.apply_verdict(message.channel, member, verdict)
            return
        if verdict.delete:
            return
        if self.cfg.games.enabled and self.cfg.games.levels:
            _, level_up = self.levels.on_message(member.id)
            if level_up:
                await self.announce_level(message.channel, member, level_up)
        await self.process_commands(message)

    async def apply_verdict(self, channel, member: discord.Member, verdict: Verdict, moderator: discord.Member | None = None) -> None:
        by = f" de la {moderator.mention}" if moderator else ""
        if verdict.timeout_minutes:
            try:
                await member.timeout(timedelta(minutes=verdict.timeout_minutes), reason=verdict.warn_reason)
            except discord.HTTPException as e:
                log.warning("Nu pot pune pauză lui %s: %s", member, e)
            text = f"⛔ {member.mention} a primit pauză de {verdict.timeout_minutes} de minute ({verdict.warn_reason}). Avertismentele au fost resetate."
        else:
            text = f"⚠️ {member.mention}: avertisment {verdict.warnings}/{self.cfg.moderation.warn_limit}{by}: {verdict.warn_reason}."
        try:
            await channel.send(text, delete_after=None if moderator else 60)
        except discord.HTTPException:
            pass
        await self.log_mod(member.guild, f"{text} (în {getattr(channel, 'mention', '?')})")

    async def announce_level(self, channel, member: discord.Member, level: int) -> None:
        g = self.cfg.games
        try:
            await channel.send(render(g.level_up_message, mention=member.mention, level=level, name=member.display_name))
        except discord.HTTPException:
            pass
        role_name = self.levels.role_for_level(level)
        if not role_name:
            return
        role = self.role_named(member.guild, role_name)
        if role and role not in member.roles:
            lower = [self.role_named(member.guild, name) for lvl, name in g.level_roles.items() if lvl < level]
            try:
                await member.add_roles(role, reason=f"nivelul {level}")
                await member.remove_roles(*[r for r in lower if r and r in member.roles], reason="rol de nivel mai mare")
            except discord.HTTPException as e:
                log.warning("Nu pot schimba rolul de nivel: %s", e)

    # Tickets -------------------------------------------------------------------------

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        t = self.cfg.tickets
        guild, user = interaction.guild, interaction.user
        if not t.enabled or guild is None:
            await interaction.response.send_message("Tichetele nu sunt active pe acest server.", ephemeral=True)
            return
        open_tickets = self.store.open_tickets(user.id)
        if len(open_tickets) >= t.max_open_per_user:
            existing = guild.get_channel(open_tickets[0]["channel_id"] or 0)
            where = f": {existing.mention}" if existing else ""
            await interaction.response.send_message(f"Ai deja un tichet deschis{where}.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        category = discord.utils.get(guild.categories, name=t.category) or await guild.create_category(t.category, reason="tichete de suport")
        support = self.role_named(guild, t.support_role)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_permissions=True),
        }
        if support:
            overwrites[support] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        ticket_id = self.store.open_ticket(user.id)
        channel = await guild.create_text_channel(ticket_channel_name(ticket_id, user.display_name), category=category, overwrites=overwrites, reason=f"tichet #{ticket_id}")
        self.store.set_ticket_channel(ticket_id, channel.id)
        await channel.send(render(t.opening_text, mention=user.mention, id=ticket_id) + (f" {support.mention}" if support else ""), view=TicketClose())
        await interaction.followup.send(f"Tichetul tău e deschis: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        ticket = self.store.ticket_by_channel(channel.id) if channel else None
        if not ticket or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Acesta nu e un canal de tichet deschis.", ephemeral=True)
            return
        is_staff = interaction.user.guild_permissions.manage_channels or any(r.name == self.cfg.tickets.support_role for r in interaction.user.roles)
        if interaction.user.id != ticket["user_id"] and not is_staff:
            await interaction.response.send_message("Doar cine a deschis tichetul sau echipa îl poate închide.", ephemeral=True)
            return
        self.store.close_ticket(ticket["id"])
        await interaction.response.send_message(f"🔒 Tichet închis de {interaction.user.mention}. Canalul se șterge în 30 de secunde.")
        member = interaction.guild.get_member(ticket["user_id"]) if interaction.guild else None
        if member:
            try:
                await channel.set_permissions(member, send_messages=False)
            except discord.HTTPException:
                pass
        await asyncio.sleep(30)
        try:
            await channel.delete(reason=f"tichet #{ticket['id']} închis")
        except discord.HTTPException:
            pass

    # Paid roles ----------------------------------------------------------------------

    def checkout_url(self, user: discord.abc.User, plan) -> str:
        assert self.stripe is not None
        return self.stripe.checkout_url(
            amount_cents=plan.cents,
            currency=self.cfg.paid_roles.currency,
            product_name=f"{self.cfg.business_name} · {plan.name}",
            success_url=f"{self.public_url}/success",
            cancel_url=f"{self.public_url}/cancel",
            metadata={"discord_user_id": str(user.id), "guild_id": str(self.cfg.guild_id), "code": plan.code, "client": self.cfg.slug},
        )

    def handle_stripe_event(self, event: dict) -> str:
        """Called from the webhook thread; role changes are handed to the bot's event loop."""
        if event.get("type") != "checkout.session.completed":
            return "ignored"
        session = event["data"]["object"]
        if session.get("payment_status") != "paid":
            return "unpaid"
        meta = session.get("metadata") or {}
        if meta.get("client") and meta["client"] != self.cfg.slug:
            return "other-client"
        if str(meta.get("guild_id", self.cfg.guild_id)) != str(self.cfg.guild_id):
            return "other-guild"
        plan = self.cfg.paid_roles.plan(meta.get("code", ""))
        user_id = int(meta.get("discord_user_id") or 0)
        if not plan or not user_id:
            log.error("Webhook Stripe fără user/rol: %s", meta)
            return "bad-metadata"
        activation = self.paid.activate(user_id, plan, reference=session["id"], amount_cents=int(session.get("amount_total") or 0))
        if activation.already_processed:
            return "duplicate"
        self._schedule(self.grant_paid_role(activation))
        return "activated"

    def _schedule(self, coro) -> None:
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except (AttributeError, RuntimeError) as e:  # bot not connected yet
            coro.close()
            log.error("Nu pot programa acțiunea pe loop-ul botului: %s", e)

    async def grant_paid_role(self, activation: Activation) -> None:
        guild = self.guild()
        if guild is None:
            return
        member = guild.get_member(activation.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(activation.user_id)
            except discord.HTTPException:
                member = None
        role = self.role_named(guild, activation.plan.role)
        when = activation.expires_at.strftime("%d.%m.%Y")
        if member and role:
            try:
                await member.add_roles(role, reason=f"plată {activation.plan.code}")
            except discord.HTTPException as e:
                log.error("Nu pot da rolul %s lui %s: %s", role, member, e)
        if member:
            try:
                await member.send(f"Mulțumim! Ai rolul **{activation.plan.role}** pe {guild.name} până pe {when}.")
            except discord.HTTPException:
                pass
        await self.log_mod(guild, f"💳 {member.mention if member else activation.user_id}: {activation.plan.name}, până pe {when}.")

    @tasks.loop(minutes=10)
    async def paid_roles_sweep(self) -> None:
        guild = self.guild()
        if guild is None:
            return
        expired, remind = self.paid.sweep()
        for user_id, plan in expired:
            member, role = guild.get_member(user_id), self.role_named(guild, plan.role)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="abonament expirat")
                except discord.HTTPException as e:
                    log.error("Nu pot scoate rolul %s: %s", role, e)
            if member:
                try:
                    await member.send(f"Rolul **{plan.role}** pe {guild.name} a expirat. Îl poți reînnoi cu /abonamente pe server.")
                except discord.HTTPException:
                    pass
            await self.log_mod(guild, f"⏰ {member.mention if member else user_id}: rolul {plan.role} a expirat și a fost scos.")
        for user_id, plan, expires in remind:
            member = guild.get_member(user_id)
            if member:
                try:
                    await member.send(f"Rolul **{plan.role}** pe {guild.name} expiră pe {expires:%d.%m.%Y}. Reînnoiește-l cu /abonamente pe server.")
                except discord.HTTPException:
                    pass

    @paid_roles_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.wait_until_ready()


# Slash commands ----------------------------------------------------------------------


def register_commands(bot: BotziBot) -> None:
    tree, cfg = bot.tree, bot.cfg

    @tree.command(name="avertizeaza", description="Dă un avertisment unui membru")
    @app_commands.describe(membru="Membrul", motiv="Motivul")
    @app_commands.default_permissions(manage_messages=True)
    async def avertizeaza(interaction: discord.Interaction, membru: discord.Member, motiv: str):
        verdict = bot.moderation.warn(membru.id, interaction.user.id, motiv)
        await interaction.response.send_message(f"Avertisment înregistrat pentru {membru.mention} ({verdict.warnings}/{cfg.moderation.warn_limit}).", ephemeral=True)
        await bot.apply_verdict(interaction.channel, membru, verdict, moderator=interaction.user)  # type: ignore[arg-type]

    @tree.command(name="avertismente", description="Avertismentele active ale unui membru")
    async def avertismente(interaction: discord.Interaction, membru: discord.Member | None = None):
        target = membru or interaction.user
        if target.id != interaction.user.id and not interaction.user.guild_permissions.manage_messages:  # type: ignore[union-attr]
            await interaction.response.send_message("Poți vedea doar avertismentele tale.", ephemeral=True)
            return
        ws = bot.store.warnings(target.id)
        text = "Niciun avertisment activ." if not ws else "\n".join(f"{i}. {w['reason']} ({w['created_at'][:10]})" for i, w in enumerate(ws, 1))
        await interaction.response.send_message(f"Avertismente {target.mention}: {len(ws)}/{cfg.moderation.warn_limit}\n{text}", ephemeral=True)

    @tree.command(name="iarta", description="Șterge avertismentele unui membru")
    @app_commands.default_permissions(manage_messages=True)
    async def iarta(interaction: discord.Interaction, membru: discord.Member):
        bot.store.clear_warnings(membru.id)
        await interaction.response.send_message(f"Avertismentele lui {membru.mention} au fost șterse.", ephemeral=True)
        await bot.log_mod(membru.guild, f"🧹 {interaction.user.mention} a șters avertismentele lui {membru.mention}.")

    @tree.command(name="curata", description="Șterge ultimele mesaje din canal")
    @app_commands.describe(numar="Câte mesaje (1-100)")
    @app_commands.default_permissions(manage_messages=True)
    async def curata(interaction: discord.Interaction, numar: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=numar)  # type: ignore[union-attr]
        await interaction.followup.send(f"Am șters {len(deleted)} mesaje.", ephemeral=True)

    @tree.command(name="tichet", description="Deschide un tichet de suport")
    async def tichet(interaction: discord.Interaction):
        await bot.open_ticket(interaction)

    @tree.command(name="inchide", description="Închide tichetul din acest canal")
    async def inchide(interaction: discord.Interaction):
        await bot.close_ticket(interaction)

    @tree.command(name="panou_tichete", description="Postează butonul de tichete în acest canal")
    @app_commands.default_permissions(administrator=True)
    async def panou_tichete(interaction: discord.Interaction):
        await interaction.channel.send(cfg.tickets.panel_text, view=TicketPanel(bot))  # type: ignore[union-attr]
        await interaction.response.send_message("Panoul de tichete e postat.", ephemeral=True)

    @tree.command(name="nivel", description="Nivelul și XP-ul unui membru")
    async def nivel(interaction: discord.Interaction, membru: discord.Member | None = None):
        target = membru or interaction.user
        await interaction.response.send_message(bot.levels.card(target.id, target.display_name))

    @tree.command(name="top", description="Clasamentul serverului")
    async def top(interaction: discord.Interaction):
        rows = bot.store.top(10)
        if not rows:
            await interaction.response.send_message("Nimeni nu are XP încă.")
            return
        lines = [f"{i}. <@{r['user_id']}>: nivel {level_for_xp(r['xp'])}, {r['xp']} XP" for i, r in enumerate(rows, 1)]
        await interaction.response.send_message("🏆 Top membri\n" + "\n".join(lines), allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="trivia", description="O întrebare, primul răspuns corect ia XP")
    async def trivia(interaction: discord.Interaction):
        if not (cfg.games.enabled and cfg.games.trivia and cfg.trivia_questions):
            await interaction.response.send_message("Trivia nu e activă pe acest server.", ephemeral=True)
            return
        question = random.choice(cfg.trivia_questions)
        view = TriviaView(bot, question)
        await interaction.response.send_message(f"❓ {question['q']}\nPrimul răspuns corect ia {cfg.games.trivia_xp} XP.", view=view)
        view.message = await interaction.original_response()

    @tree.command(name="abonamente", description="Roluri plătite: ce oferă și cum le iei")
    async def abonamente(interaction: discord.Interaction):
        p = cfg.paid_roles
        if not p.enabled or not p.roles:
            await interaction.response.send_message("Nu există roluri plătite pe acest server.", ephemeral=True)
            return
        mine = {r["code"]: parse(r["expires_at"]) for r in bot.store.user_paid_roles(interaction.user.id)}
        lines, view = [], discord.ui.View(timeout=600)
        for plan in p.roles:
            line = "• " + bot.paid.describe(plan)
            if plan.code in mine:
                line += f" — îl ai până pe {mine[plan.code]:%d.%m.%Y}"
            lines.append(line)
            if bot.stripe is not None:
                try:
                    view.add_item(discord.ui.Button(label=f"Plătește {plan.name}"[:80], url=bot.checkout_url(interaction.user, plan), style=discord.ButtonStyle.link))
                except StripeError as e:
                    log.error("Stripe: %s", e)
        note = "" if bot.stripe is not None else "\nPlata cu cardul nu e configurată încă; scrie unui administrator."
        await interaction.response.send_message("\n".join(lines) + note, view=view, ephemeral=True)

    @tree.command(name="setari", description="Ce module rulează pe acest server")
    @app_commands.default_permissions(administrator=True)
    async def setari(interaction: discord.Interaction):
        m, t, g, p, w = cfg.moderation, cfg.tickets, cfg.games, cfg.paid_roles, cfg.welcome
        text = (
            f"**{cfg.business_name}** · client `{cfg.slug}`\n"
            f"Bun venit: {'da' if w.enabled else 'nu'} (#{w.channel}, rol automat: {w.auto_role or 'niciunul'})\n"
            f"Moderare: {'da' if m.enabled else 'nu'} ({len(m.banned_words)} cuvinte interzise, spam {m.spam_messages}/{m.spam_seconds}s, pauză la {m.warn_limit} avertismente: {m.timeout_minutes} min, log #{m.log_channel})\n"
            f"Tichete: {'da' if t.enabled else 'nu'} (categoria {t.category}, echipa @{t.support_role})\n"
            f"Niveluri: {'da' if g.enabled and g.levels else 'nu'} ({g.xp_min}-{g.xp_max} XP/mesaj, roluri: {', '.join(f'{k}→{v}' for k, v in sorted(g.level_roles.items())) or 'niciunul'}); trivia: {len(cfg.trivia_questions)} întrebări\n"
            f"Roluri plătite: {'da' if p.enabled else 'nu'} ({len(p.roles)} planuri, Stripe: {'configurat' if bot.stripe else 'lipsă'})"
        )
        await interaction.response.send_message(text, ephemeral=True)

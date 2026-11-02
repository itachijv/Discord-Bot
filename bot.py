import os, sqlite3, asyncio, io, logging, re, csv, json
from datetime import datetime, timezone, timedelta, time as dtime
from zoneinfo import ZoneInfo
import discord
from discord import app_commands, ui
from discord.ext import tasks
from dotenv import load_dotenv
import aiohttp

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID", "").strip()
TERMS_VERSION = "2026-10-v1"
IST = ZoneInfo("Asia/Kolkata")
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db = sqlite3.connect("bot.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS agreements(guild INTEGER,user INTEGER,version TEXT,ts TEXT);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,pair TEXT,side TEXT,r REAL,note TEXT,ts TEXT);
CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,mod INTEGER,reason TEXT,ts TEXT);
""")
db.commit()

ACCENT = 0x65D6CE
VIOLET = 0xA9A1FF
DANGER = 0xED4245
GOLD = 0xF1C40F
BLUE = 0x5865F2
GREEN = 0x2ECC71


def card(title, text="", color=ACCENT):
    e = discord.Embed(
        title=title,
        description=(text or "")[:4000],
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    e.set_author(name="J A R V I S  /  COMMUNITY OS")
    e.set_footer(text="PRIVATE COMMUNITY • EDUCATION FIRST")
    return e


def find(guild, name):
    return discord.utils.get(guild.text_channels, name=name)


async def audit(guild, title, text):
    c = find(guild, "mod-log")
    if c:
        try:
            await c.send(
                embed=card(title, text, color=VIOLET),
                allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            logging.exception("Audit failed")


RULES = """01 / Respect members. No harassment, hate, spam or impersonation.
02 / No scams, promotions, paid signals, broker referrals or fund collection.
03 / Never request passwords, OTPs, broker access or money.
04 / No assured returns, fabricated results or claims of guaranteed profits.
05 / Keep discussion educational. No personalised recommendations or account management.
06 / This community is restricted to adults aged 18+. This is a server policy.
07 / Moderators may restrict access for breaches. Contact the owner to appeal."""

TERMS = """PURPOSE / Educational discussion only; membership is not a regulated advisory service. Do not represent this community as SEBI- or RBI-approved. A disclaimer does not legalise regulated activities.

INDIA / Residents must comply with FEMA and applicable RBI directions. Forex transactions must be with authorised persons, for permitted purposes, through permitted routes. Electronic transactions must use RBI-authorised platforms or recognised exchanges as applicable. Verify permissions, products and purposes independently; absence from the RBI Alert List is not proof of authorisation. Do not use this server to promote unauthorised platforms or prohibited transactions.

RISK / Leveraged trading can cause substantial loss. Data and examples may be inaccurate or delayed. Past performance does not predict future results. No guaranteed returns are offered. Do not trade based solely on community posts.

RESPONSIBILITY / Follow applicable law and obtain independent qualified advice when needed. These terms do not exclude statutory rights or liability that cannot lawfully be excluded. Members outside India must comply with local requirements.

PRIVACY / The bot records your Discord IDs, accepted terms version and UTC time, moderation records and voluntarily logged trades. Moderation logs can contain message content. The owner controls this data; contact the owner for access/deletion requests and applicable retention information. Do not post sensitive information.

CONSENT / Accepting confirms you say you are 18+, have read these rules and terms, and agree to the community policy. This is not identity verification, KYC, legal approval or proof you understood every term. Material changes require renewed acceptance."""

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class Jarvis(discord.Client):
    def __init__(self):
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.sent_news_keys = set()

    async def setup_hook(self):
        self.add_view(Gate())
        self.add_view(Tickets())
        self.add_view(SignalView(0))

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()

        logging.info("Synced %s commands", len(synced))

        daily_news_digest.start()
        news_alert.start()


bot = Jarvis()


class ReadAgreement(ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.user_id = user_id

    @ui.button(label="I AGREE — Verify me", style=discord.ButtonStyle.success, emoji="✅")
    async def agree(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Open your own agreement.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        role = discord.utils.get(interaction.guild.roles, name="Verified")

        if not role:
            return await interaction.followup.send(
                embed=card("SETUP NEEDED", "Create a role named `Verified` first.", color=DANGER),
                ephemeral=True
            )

        if role >= interaction.guild.me.top_role or not interaction.guild.me.guild_permissions.manage_roles:
            return await interaction.followup.send(
                embed=card(
                    "MISSING PERMISSION",
                    "Move the bot role above `Verified` and give the bot Manage Roles.",
                    color=DANGER
                ),
                ephemeral=True
            )

        db.execute(
            "INSERT INTO agreements VALUES(?,?,?,?)",
            (
                interaction.guild.id,
                interaction.user.id,
                TERMS_VERSION,
                datetime.now(timezone.utc).isoformat()
            )
        )
        db.commit()

        try:
            await interaction.user.add_roles(role, reason=f"Accepted {TERMS_VERSION}")
        except discord.HTTPException:
            return await interaction.followup.send(
                embed=card(
                    "ACCEPTED / ROLE FAILED",
                    "Your acceptance was saved, but the Verified role could not be assigned.",
                    color=DANGER
                ),
                ephemeral=True
            )

        await interaction.followup.send(
            embed=card(
                "ACCESS GRANTED ✓",
                "Thank you for accepting. The server is now unlocked."
            ),
            ephemeral=True
        )

        await audit(
            interaction.guild,
            "MEMBERSHIP / ACCEPTED",
            f"User {interaction.user.id} • {TERMS_VERSION}"
        )


class Gate(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Read & verify", style=discord.ButtonStyle.primary, emoji="🔐", custom_id="verify_btn")
    async def enter(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            embeds=[
                card("01 / COMMUNITY CODE", RULES),
                card("02 / TRADING TERMS", TERMS, color=VIOLET)
            ],
            view=ReadAgreement(interaction.user.id),
            ephemeral=True
        )

    @ui.button(label="Why verification?", style=discord.ButtonStyle.secondary, custom_id="jarvis_gate_info")
    async def info(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "Read the community policies, then explicitly accept. Only the Verified role unlocks member channels. This is not financial KYC.",
            ephemeral=True
        )


class Tickets(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Contact owner", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="jarvis_ticket")
    async def ticket(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)

        name = f"ticket-{interaction.user.id}"
        channel = find(interaction.guild, name)

        if not channel:
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                interaction.guild.owner: discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }

            channel = await interaction.guild.create_text_channel(
                name,
                overwrites=overwrites,
                topic=f"Private support for {interaction.user.id}"
            )

            await channel.send(
                embed=card(
                    "SUPPORT / PRIVATE",
                    "Describe your issue. Only you, the owner, administrators and the bot can access this ticket. Owner: use /close_ticket when finished."
                )
            )

        await interaction.followup.send(
            f"Your support channel: {channel.mention}",
            ephemeral=True
        )


class SignalView(ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    async def close_signal(self, interaction: discord.Interaction, status: str, colour: int, icon: str):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                embed=card(
                    "RESTRICTED",
                    "Only the signal owner can update this trade.",
                    color=DANGER
                ),
                ephemeral=True
            )

        e = interaction.message.embeds[0]
        pair = e.title.split("•")[0].strip() if e.title else "TRADE"

        e.title = f"{icon} {pair} • {status}"
        e.colour = colour

        for field in list(e.fields):
            if field.name.lower() == "status":
                e.remove_field(e.fields.index(field))
                break

        e.add_field(
            name="STATUS",
            value=f"**{status}**\nUpdated by {interaction.user.mention}",
            inline=False
        )

        e.add_field(
            name="⚠️ RISK NOTICE",
            value=(
                "**Take trades at your own risk.**\n"
                "We are **not financial advisors**. This is **not financial advice**. "
                "Educational example only. Enter only with your own LTF confirmation and risk management."
            ),
            inline=False
        )

        await interaction.response.edit_message(embed=e, view=None)
        await audit(
            interaction.guild,
            "SIGNAL / CLOSED",
            f"{pair} • {status} • by {interaction.user}"
        )

    @ui.button(label="WIN", emoji="✅", style=discord.ButtonStyle.success, custom_id="sig_win")
    async def win(self, interaction: discord.Interaction, button: ui.Button):
        await self.close_signal(interaction, "WIN", GREEN, "✅")

    @ui.button(label="LOSS", emoji="❌", style=discord.ButtonStyle.danger, custom_id="sig_loss")
    async def loss(self, interaction: discord.Interaction, button: ui.Button):
        await self.close_signal(interaction, "LOSS", DANGER, "❌")

    @ui.button(label="BREAK-EVEN", emoji="⚪", style=discord.ButtonStyle.secondary, custom_id="sig_be")
    async def breakeven(self, interaction: discord.Interaction, button: ui.Button):
        await self.close_signal(interaction, "BREAK-EVEN", GOLD, "⚪")

    @ui.button(label="CANCEL / AVOID", emoji="🚫", style=discord.ButtonStyle.secondary, custom_id="sig_cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await self.close_signal(interaction, "CANCELLED / AVOIDED", VIOLET, "🚫")


async def panel(channel):
    await channel.send(
        embeds=[
            card(
                "WELCOME / YOUR NEXT CHAPTER",
                "A focused space for disciplined learning.\n\n01  Read the code\n02  Review trading risks\n03  Accept to unlock\n\nUse **Read & verify** below. Acceptance is saved with a policy version."
            ),
            card(
                "POLICY / VERSION",
                f"{TERMS_VERSION} • 18+ community\nQuestions? Contact the server owner.",
                color=VIOLET
            )
        ],
        view=Gate()
    )


@bot.tree.command(name="setup_verify", description="Owner: post agreement and verification panel")
@app_commands.guild_only()
async def setup_verify(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await panel(interaction.channel)
    await interaction.followup.send("Panel posted. Remove old verify panels.", ephemeral=True)


@bot.tree.command(name="setup_server", description="Owner: configure named channels (does not hide unrelated channels)")
@app_commands.guild_only()
async def setup_server(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    role = discord.utils.get(guild.roles, name="Verified") or await guild.create_role(
        name="Verified",
        colour=discord.Colour(ACCENT)
    )

    me = guild.me

    for name in [
        "verify", "announcements", "news", "sessions", "daily-bias", "signals",
        "general", "bot-commands", "journal", "trade-ideas", "support", "mod-log"
    ]:
        public = name == "verify"
        staff = name == "mod-log"
        readonly = name in ["announcements", "news", "sessions", "daily-bias", "signals", "support"]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=public,
                send_messages=False
            ),
            role: discord.PermissionOverwrite(
                view_channel=not public and not staff,
                send_messages=not readonly and not staff,
                create_public_threads=False,
                create_private_threads=False
            ),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True
            )
        }

        channel = find(guild, name)

        if channel:
            await channel.edit(overwrites=overwrites)
        else:
            await guild.create_text_channel(name, overwrites=overwrites)

    await panel(find(guild, "verify"))
    await find(guild, "support").send(
        embed=card("SUPPORT / OWNER DESK", "Need help? Open a private ticket."),
        view=Tickets()
    )

    await interaction.followup.send(
        "Named channels configured. IMPORTANT: manually hide any other channels/categories and remove other roles that grant access. Place Verified below the bot role. Test with a non-admin account.",
        ephemeral=True
    )


@bot.tree.command(name="setup_tickets", description="Owner: post support panel")
@app_commands.guild_only()
async def setup_tickets(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    await interaction.channel.send(
        embed=card("SUPPORT / OWNER DESK", "Open a private ticket."),
        view=Tickets()
    )
    await interaction.response.send_message("Posted.", ephemeral=True)


@bot.tree.command(name="close_ticket", description="Owner: delete current ticket channel")
@app_commands.guild_only()
async def close_ticket(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id or not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message("Owner only, in a ticket channel.", ephemeral=True)

    await interaction.response.send_message("Deleting ticket in 5 seconds. No transcript is retained.", ephemeral=True)
    await asyncio.sleep(5)
    await interaction.channel.delete(reason="Owner closed ticket")


@bot.tree.command(name="ping", description="Connection status")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=card("SYSTEM / ONLINE", f"Gateway latency: **{bot.latency * 1000:.0f} ms**"),
        ephemeral=True
    )


@bot.tree.command(name="help", description="View command directory")
async def help_command(interaction: discord.Interaction):
    commands = " • ".join(f"`/{c.name}`" for c in bot.tree.get_commands())
    await interaction.response.send_message(
        embed=card("DIRECTORY / COMMANDS", commands),
        ephemeral=True
    )


async def report(guild):
    verified = discord.utils.get(guild.roles, name="Verified")
    embed = card(
        "OWNER / MORNING BRIEF",
        datetime.now(IST).strftime("%d %b %Y • %I:%M %p IST")
    )

    embed.add_field(
        name="MEMBERS",
        value=f"{guild.member_count} total • {sum(not m.bot for m in guild.members)} humans"
    )
    embed.add_field(
        name="VERIFIED",
        value=str(len(verified.members) if verified else 0)
    )
    embed.add_field(
        name="CHANNELS",
        value=f"{len(guild.text_channels)} text • {len(guild.voice_channels)} voice"
    )

    count = db.execute(
        "SELECT COUNT(*) FROM agreements WHERE guild=? AND version=?",
        (guild.id, TERMS_VERSION)
    ).fetchone()[0]

    embed.add_field(
        name="ACCEPTANCES",
        value=f"{count} records • {TERMS_VERSION}"
    )

    return embed


@bot.tree.command(name="goodmorning", description="Owner-only private dashboard")
@app_commands.guild_only()
async def goodmorning(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    await interaction.response.send_message(
        embed=await report(interaction.guild),
        ephemeral=True
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if message.author.id == message.guild.owner_id and message.content.strip().lower() == "good morning jarvis":
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        try:
            await message.author.send(embed=await report(message.guild))
        except discord.HTTPException:
            logging.warning("Owner DMs unavailable; use /goodmorning")


@bot.event
async def on_member_join(member):
    channel = find(member.guild, "verify")

    if channel:
        try:
            await channel.send(
                embed=card(
                    "ARRIVAL / WELCOME",
                    f"Welcome {member.display_name}. Use the Read & verify panel to unlock the community."
                )
            )
        except discord.HTTPException:
            logging.exception("Welcome failed")

    await audit(member.guild, "MEMBER / JOINED", f"{member} • {member.id}")


@bot.event
async def on_member_remove(member):
    await audit(member.guild, "MEMBER / LEFT", f"{member} • {member.id}")


async def owner_only(interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return False

    return True


@bot.tree.command(name="warn", description="Owner: record a warning (no automatic ban)")
@app_commands.guild_only()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await owner_only(interaction):
        return

    db.execute(
        "INSERT INTO warns(guild,user,mod,reason,ts) VALUES(?,?,?,?,?)",
        (interaction.guild.id, member.id, interaction.user.id, reason, datetime.now(timezone.utc).isoformat())
    )
    db.commit()

    await interaction.response.send_message("Warning saved.", ephemeral=True)
    await audit(interaction.guild, "MODERATION / WARNING", f"User {member.id}: {reason[:1000]}")


@bot.tree.command(name="warnings", description="Owner: view member warnings")
@app_commands.guild_only()
async def warnings(interaction: discord.Interaction, member: discord.Member):
    if not await owner_only(interaction):
        return

    rows = db.execute(
        "SELECT id,reason FROM warns WHERE guild=? AND user=?",
        (interaction.guild.id, member.id)
    ).fetchall()

    await interaction.response.send_message(
        embed=card(
            "MODERATION / RECORDS",
            "\n".join(f"#{a}: {b}" for a, b in rows) or "No warnings."
        ),
        ephemeral=True
    )


@bot.tree.command(name="timeout", description="Owner: timeout a member")
@app_commands.guild_only()
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "Server moderation"):
    if not await owner_only(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await interaction.followup.send("Timeout applied.", ephemeral=True)
    await audit(interaction.guild, "MODERATION / TIMEOUT", f"{member.id} • {minutes} minutes • {reason}")


class ConfirmRemoval(ui.View):
    def __init__(self, user_id, member, reason, ban=False):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.member = member
        self.reason = reason
        self.ban = ban

    @ui.button(label="Confirm removal", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return

        await interaction.response.defer(ephemeral=True)

        if self.ban:
            await self.member.ban(reason=self.reason)
        else:
            await self.member.kick(reason=self.reason)

        self.stop()
        await interaction.edit_original_response(content="Action completed.", embed=None, view=None)
        await audit(
            interaction.guild,
            "MODERATION / REMOVAL",
            f"{self.member.id} • ban={self.ban} • {self.reason}"
        )


@bot.tree.command(name="kick", description="Owner: confirm and kick a member")
@app_commands.guild_only()
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await owner_only(interaction):
        return

    await interaction.response.send_message(
        embed=card(
            "CONFIRM / KICK",
            f"{member} ({member.id})\nReason: {reason}",
            color=DANGER
        ),
        view=ConfirmRemoval(interaction.user.id, member, reason),
        ephemeral=True
    )


@bot.tree.command(name="ban", description="Owner: confirm and ban a member")
@app_commands.guild_only()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await owner_only(interaction):
        return

    await interaction.response.send_message(
        embed=card(
            "CONFIRM / BAN",
            f"{member} ({member.id})\nReason: {reason}",
            color=DANGER
        ),
        view=ConfirmRemoval(interaction.user.id, member, reason, True),
        ephemeral=True
    )


@bot.tree.command(name="purge", description="Owner: delete up to 100 recent messages")
@app_commands.guild_only()
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not await owner_only(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(name="slowmode", description="Owner: set channel slowmode")
@app_commands.guild_only()
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
    if not await owner_only(interaction):
        return

    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"Slowmode: {seconds}s.", ephemeral=True)


@bot.tree.command(name="risk", description="Sizing using explicit pip value in account currency")
async def risk(
    interaction: discord.Interaction,
    balance: float,
    risk_percent: float,
    sl_pips: float,
    pip_value_per_lot: float
):
    if balance <= 0 or risk_percent <= 0 or sl_pips <= 0 or pip_value_per_lot <= 0:
        return await interaction.response.send_message(
            "All values must be greater than zero.",
            ephemeral=True
        )

    risk_amount = balance * risk_percent / 100
    lots = risk_amount / (sl_pips * pip_value_per_lot)

    await interaction.response.send_message(
        embed=card(
            "RISK LAB / POSITION SIZE",
            f"Risk: **{risk_amount:.2f}** account currency\n"
            f"Calculated lots: **{lots:.4f}**\n"
            "Use your broker's pip value; round DOWN to its lot step. Costs/slippage excluded."
        ),
        ephemeral=True
    )


@bot.tree.command(name="calculate", description="Risk amount from balance and risk percent")
async def calculate(interaction: discord.Interaction, balance: float, risk_percent: float):
    if balance <= 0 or risk_percent <= 0:
        return await interaction.response.send_message(
            "Balance and risk percent must be greater than zero.",
            ephemeral=True
        )

    amount = balance * risk_percent / 100

    await interaction.response.send_message(
        embed=card(
            "RISK LAB / RISK AMOUNT",
            f"Balance: **{balance:,.2f}**\nRisk: **{risk_percent}%**\nRisk amount: **{amount:,.2f}** account currency"
        ),
        ephemeral=True
    )


@bot.tree.command(name="pip", description="Distance between FX prices")
async def pip(interaction: discord.Interaction, price1: float, price2: float, jpy_pair: bool = False):
    pip_size = 0.01 if jpy_pair else 0.0001
    pips = abs(price1 - price2) / pip_size

    await interaction.response.send_message(
        embed=card("RISK LAB / DISTANCE", f"{pips:.1f} pips (standard FX convention)."),
        ephemeral=True
    )


@bot.tree.command(name="rr", description="Validate levels and calculate reward/risk")
async def rr(interaction: discord.Interaction, entry: float, sl: float, tp: float):
    if not (sl < entry < tp or tp < entry < sl):
        return await interaction.response.send_message(
            "SL and TP must be on opposite sides of entry.",
            ephemeral=True
        )

    reward = abs(tp - entry) / abs(entry - sl)

    await interaction.response.send_message(
        embed=card("RISK LAB / REWARD", f"1 : {reward:.2f}"),
        ephemeral=True
    )


@bot.tree.command(name="margin", description="Notional and leverage must use the same account currency")
async def margin(interaction: discord.Interaction, notional: float, leverage: int):
    if notional <= 0 or leverage <= 0:
        return await interaction.response.send_message(
            "Notional and leverage must be greater than zero.",
            ephemeral=True
        )

    await interaction.response.send_message(
        embed=card(
            "RISK LAB / MARGIN ESTIMATE",
            f"{notional / leverage:.2f} account currency. Broker margin rules may differ."
        ),
        ephemeral=True
    )


@bot.tree.command(name="trade_plan", description="Create a structured trade plan")
async def trade_plan(
    interaction: discord.Interaction,
    pair: str,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    concept: str = "FVG",
    notes: str = ""
):
    if not (stop_loss < entry < take_profit or take_profit < entry < stop_loss):
        return await interaction.response.send_message(
            "Stop loss and take profit must be on opposite sides of entry.",
            ephemeral=True
        )

    reward = abs(take_profit - entry) / abs(entry - stop_loss)

    await interaction.response.send_message(
        embed=card(
            f"📋 TRADE PLAN / {pair.upper()}",
            f"**Direction:** {direction}\n"
            f"**Entry:** `{entry}`\n"
            f"**Stop loss:** `{stop_loss}`\n"
            f"**Take profit:** `{take_profit}`\n"
            f"**Risk : reward:** `1 : {reward:.2f}`\n"
            f"**Concept:** {concept}\n"
            f"**Notes:** {notes or '—'}\n\n"
            "Plan only. Not financial advice.",
            color=BLUE
        ),
        ephemeral=True
    )


@bot.tree.command(name="session", description="Current forex session status in IST")
async def session(interaction: discord.Interaction):
    rows = []

    for name, zone, hour in [
        ("Sydney", "Australia/Sydney", 7),
        ("Tokyo", "Asia/Tokyo", 9),
        ("London", "Europe/London", 8),
        ("New York", "America/New_York", 8)
    ]:
        local = datetime.now(ZoneInfo(zone))
        open_time = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        close_time = open_time + timedelta(hours=8)
        active = open_time <= local < close_time
        rows.append(
            f"{'🟢' if active else '⚪'} **{name}** • "
            f"{open_time.astimezone(IST):%I:%M %p}–{close_time.astimezone(IST):%I:%M %p} IST"
        )

    await interaction.response.send_message(
        embed=card(
            "MARKET CLOCK / SESSIONS",
            "\n".join(rows) +
            "\nConventions only. Weekends and holidays are not accounted for."
        ),
        ephemeral=True
    )


@bot.tree.command(name="sessions", description="Conventional session opens in IST, DST-aware")
async def sessions(interaction: discord.Interaction):
    rows = []

    for name, zone, hour in [
        ("Sydney", "Australia/Sydney", 7),
        ("Tokyo", "Asia/Tokyo", 9),
        ("London", "Europe/London", 8),
        ("New York", "America/New_York", 8)
    ]:
        time = datetime.now(ZoneInfo(zone)).replace(hour=hour, minute=0, second=0, microsecond=0)
        rows.append(f"**{name}** • {time.astimezone(IST):%I:%M %p} IST")

    await interaction.response.send_message(
        embed=card(
            "MARKET CLOCK / SESSIONS",
            "\n".join(rows) +
            "\nConventions, not exchange schedules. Weekends/holidays excluded from trading assumptions."
        ),
        ephemeral=True
    )


@bot.tree.command(name="news", description="High-impact economic events this week")
async def news(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        data = await fetch_news(retries=2)
    except Exception as e:
        logging.error("News fetch failed: %s", e)
        return await interaction.followup.send("News feed unavailable. Try again later.", ephemeral=True)

    now = datetime.now(timezone.utc)
    rows = []

    for event in data:
        if event.get("impact") != "High":
            continue

        event_time = datetime.fromisoformat(event["date"]).astimezone(timezone.utc)

        if event_time >= now:
            rows.append(
                f"🔴 **{event['country']}** {event['title']} • "
                f"{event_time.astimezone(IST):%d %b, %I:%M %p} IST"
            )

    await interaction.followup.send(
        embed=card(
            "📰 HIGH IMPACT / THIS WEEK",
            "\n".join(rows[:15]) or "No upcoming high-impact events found."
        ),
        ephemeral=True
    )


@bot.tree.command(name="price", description="Indicative Yahoo FX price (personal use, not broker quotes)")
async def price(interaction: discord.Interaction, pair: str):
    if not re.fullmatch(r"[A-Za-z]{6}", pair):
        return await interaction.response.send_message("Use six letters, e.g. EURUSD.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    import yfinance as yf

    data = await asyncio.to_thread(
        lambda: yf.Ticker(pair.upper() + "=X").history(period="5d", interval="1h")
    )

    if data.empty:
        return await interaction.followup.send("No data available.", ephemeral=True)

    await interaction.followup.send(
        embed=card(
            "MARKET DATA / INDICATIVE",
            f"{pair.upper()} • {float(data['Close'].iloc[-1]):.5f}\n"
            f"Timestamp: {data.index[-1]}\n"
            "May be stale/delayed. Not executable. Check data licensing before redistribution."
        ),
        ephemeral=True
    )


@bot.tree.command(name="levels", description="Previous day/week high & low")
async def levels(interaction: discord.Interaction, pair: str):
    if not re.fullmatch(r"[A-Za-z]{6}", pair):
        return await interaction.response.send_message("Use six letters, e.g. EURUSD.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    import yfinance as yf

    def get():
        t = yf.Ticker(pair.upper() + "=X")
        return t.history(period="10d", interval="1d"), t.history(period="2mo", interval="1wk")

    daily, weekly = await asyncio.to_thread(get)

    if len(daily) < 2 or len(weekly) < 2:
        return await interaction.followup.send("Not enough data.", ephemeral=True)

    d = daily.iloc[-2]
    w = weekly.iloc[-2]

    await interaction.followup.send(
        embed=card(
            f"📊 {pair.upper()} KEY LEVELS",
            f"**Previous day**\n🔺 {d['High']:.5f}\n🔻 {d['Low']:.5f}\n\n"
            f"**Previous week**\n🔺 {w['High']:.5f}\n🔻 {w['Low']:.5f}",
            color=BLUE
        ),
        ephemeral=True
    )


@bot.tree.command(name="chart", description="1H candle chart, last 5 days")
async def chart(interaction: discord.Interaction, pair: str):
    if not re.fullmatch(r"[A-Za-z]{6}", pair):
        return await interaction.response.send_message("Use six letters, e.g. EURUSD.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    try:
        import yfinance as yf
        import mplfinance as mpf
    except ImportError:
        return await interaction.followup.send(
            "Run: pip install yfinance mplfinance",
            ephemeral=True
        )

    def make():
        data = yf.Ticker(pair.upper() + "=X").history(period="5d", interval="1h")
        path = f"chart_{pair.upper()}.png"
        mpf.plot(data, type="candle", style="nightclouds", title=pair.upper(), savefig=path)
        return path

    path = await asyncio.to_thread(make)

    await interaction.followup.send(
        embed=card(f"🕯️ {pair.upper()} • 1H", color=BLUE),
        file=discord.File(path)
    )

    os.remove(path)


@bot.tree.command(name="bias", description="Owner: set today's market bias")
@app_commands.choices(direction=[
    app_commands.Choice(name="Bullish", value="Bullish"),
    app_commands.Choice(name="Bearish", value="Bearish"),
    app_commands.Choice(name="Neutral", value="Neutral")
])
@app_commands.guild_only()
async def bias(
    interaction: discord.Interaction,
    pair: str,
    direction: app_commands.Choice[str],
    note: str = ""
):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    channel = find(interaction.guild, "daily-bias")

    if channel:
        colour = GREEN if direction.value == "Bullish" else DANGER if direction.value == "Bearish" else VIOLET
        icon = "🟢" if direction.value == "Bullish" else "🔴" if direction.value == "Bearish" else "⚪"

        await channel.send(
            embed=card(
                f"🎯 {pair.upper()} BIAS",
                f"{icon} **{direction.value}**\n{note}",
                color=colour
            )
        )

    await interaction.followup.send("Bias posted.", ephemeral=True)


@bot.tree.command(name="signal", description="Owner: post an educational trade idea with LTF confirmation")
@app_commands.choices(side=[
    app_commands.Choice(name="Buy", value="Buy"),
    app_commands.Choice(name="Sell", value="Sell")
])
@app_commands.choices(bias=[
    app_commands.Choice(name="Bullish", value="Bullish"),
    app_commands.Choice(name="Bearish", value="Bearish"),
    app_commands.Choice(name="Neutral", value="Neutral")
])
@app_commands.guild_only()
async def signal(
    interaction: discord.Interaction,
    pair: str,
    side: app_commands.Choice[str],
    bias: app_commands.Choice[str],
    entry: float,
    sl: float,
    tp: float,
    htf_concept: str = "FVG",
    ltf_confirmation: str = "Wait for LTF CHOCH / bullish FVG"
):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("Owner only.", ephemeral=True)

    if not (sl < entry < tp or tp < entry < sl):
        return await interaction.response.send_message(
            "SL and TP must be on opposite sides of entry.",
            ephemeral=True
        )

    await interaction.response.defer()

    reward = abs(tp - entry) / abs(entry - sl)
    channel = find(interaction.guild, "signals") or interaction.channel
    is_buy = side.value == "Buy"

    e = discord.Embed(
        title=f"{'🟢' if is_buy else '🔴'} {pair.upper()} • {side.value.upper()} SETUP",
        description=(
            f"**HTF BIAS** — `{bias.value}`\n"
            f"**HTF CONCEPT** — `{htf_concept}`\n"
            f"**LTF CONFIRMATION** — {ltf_confirmation}\n\n"
            f"**ENTRY ZONE** — `{entry}`\n"
            f"**STOP LOSS** — `{sl}`\n"
            f"**TAKE PROFIT** — `{tp}`\n"
            f"**RISK : REWARD** — `1 : {reward:.2f}`"
        ),
        color=ACCENT if is_buy else DANGER,
        timestamp=datetime.now(timezone.utc)
    )
    e.set_author(name="J A R V I S  /  TRADE DESK")
    e.add_field(
        name="EXECUTION CHECKLIST",
        value=(
            "01 — HTF bias matches\n"
            "02 — Liquidity / POI identified\n"
            "03 — LTF confirmation received\n"
            "04 — Risk within your plan\n"
            "05 — No news conflict"
        ),
        inline=False
    )
    e.add_field(
        name="STATUS",
        value="🟡 **ACTIVE — WAITING FOR RESULT**",
        inline=False
    )
    e.add_field(
        name="⚠️ RISK NOTICE",
        value=(
            "**Take trades at your own risk.**\n"
            "We are **not financial advisors**. This is **not financial advice**. "
            "Educational example only. Enter only with your own LTF confirmation and risk management."
        ),
        inline=False
    )
    e.set_footer(text=f"TERMS {TERMS_VERSION} • EDUCATION FIRST")

    await channel.send(embed=e, view=SignalView(interaction.user.id))
    await interaction.followup.send("Trade idea posted with result buttons.", ephemeral=True)


@bot.tree.command(name="log", description="Record your own completed or simulated trade in R")
@app_commands.guild_only()
async def log(interaction: discord.Interaction, pair: str, side: str, r_result: float, note: str = ""):
    db.execute(
        "INSERT INTO trades(guild,user,pair,side,r,note,ts) VALUES(?,?,?,?,?,?,?)",
        (interaction.guild.id, interaction.user.id, pair.upper(), side, r_result, note, datetime.now(timezone.utc).isoformat())
    )
    db.commit()

    await interaction.response.send_message(
        embed=card("JOURNAL / SAVED", f"{pair.upper()} • {r_result:+.2f}R"),
        ephemeral=True
    )


@bot.tree.command(name="delete_trade", description="Delete one of your logged trades by ID")
@app_commands.guild_only()
async def delete_trade(interaction: discord.Interaction, trade_id: int):
    row = db.execute(
        "SELECT user FROM trades WHERE id=? AND guild=?",
        (trade_id, interaction.guild.id)
    ).fetchone()

    if not row or row[0] != interaction.user.id:
        return await interaction.response.send_message("Trade not found or not yours.", ephemeral=True)

    db.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    db.commit()

    await interaction.response.send_message(f"Trade #{trade_id} deleted.", ephemeral=True)


@bot.tree.command(name="stats", description="Your self-reported trading statistics")
@app_commands.guild_only()
async def stats(interaction: discord.Interaction):
    results = [
        row[0] for row in db.execute(
            "SELECT r FROM trades WHERE guild=? AND user=? ORDER BY id",
            (interaction.guild.id, interaction.user.id)
        )
    ]

    if not results:
        return await interaction.response.send_message("No trades. Use /log.", ephemeral=True)

    win_rate = sum(x > 0 for x in results) / len(results) * 100
    equity = peak = drawdown = 0

    for result in results:
        equity += result
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    losses = -sum(x for x in results if x < 0)
    profit_factor = f"{sum(x for x in results if x > 0) / losses:.2f}" if losses else "N/A (no losses)"

    embed = card(
        "JOURNAL / PERFORMANCE",
        f"{'▰' * round(win_rate / 10)}{'▱' * (10 - round(win_rate / 10))}  {win_rate:.1f}% wins\n"
        "Self-reported; not independently verified."
    )

    for name, value in [
        ("Trades", len(results)),
        ("Total R", f"{sum(results):+.2f}"),
        ("Average R", f"{sum(results) / len(results):.2f}"),
        ("Profit factor (R)", profit_factor),
        ("Drawdown", f"{drawdown:.2f}R")
    ]:
        embed.add_field(name=name, value=str(value))

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="leaderboard", description="Self-reported results, not verified performance")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT user,SUM(r) FROM trades WHERE guild=? GROUP BY user ORDER BY SUM(r) DESC LIMIT 10",
        (interaction.guild.id,)
    ).fetchall()

    await interaction.response.send_message(
        embed=card(
            "JOURNAL / COMMUNITY",
            "Self-reported results only.\n" +
            "\n".join(f"{n}. <@{user}> • {result:+.2f}R" for n, (user, result) in enumerate(rows, 1))
        ),
        ephemeral=True
    )


@bot.tree.command(name="export", description="Export your trade journal to CSV")
@app_commands.guild_only()
async def export(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rows = db.execute(
        "SELECT id,pair,side,r,note,ts FROM trades WHERE guild=? AND user=? ORDER BY id",
        (interaction.guild.id, interaction.user.id)
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "pair", "side", "r", "note", "utc_time"])
    writer.writerows(rows)

    await interaction.followup.send(
        file=discord.File(
            io.BytesIO(output.getvalue().encode()),
            filename="my_trades.csv"
        ),
        ephemeral=True
    )


@bot.tree.command(name="export_agreements", description="Owner: private CSV of agreement records")
@app_commands.guild_only()
async def export_agreements(interaction: discord.Interaction):
    if not await owner_only(interaction):
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["guild", "user", "version", "utc"])
    writer.writerows(
        db.execute(
            "SELECT * FROM agreements WHERE guild=?",
            (interaction.guild.id,)
        )
    )

    await interaction.response.send_message(
        file=discord.File(
            io.BytesIO(output.getvalue().encode()),
            filename="agreements.csv"
        ),
        ephemeral=True
    )


# ---------------- NEWS FETCH WITH RETRY (FIXED) ----------------

async def fetch_news(retries: int = 3):
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    NEWS_URL,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisBot/1.0)"}
                ) as r:
                    if r.status == 429:
                        wait = 30 * (attempt + 1)
                        logging.warning(
                            "News rate-limited (429), retry %s/%s in %ss",
                            attempt + 1, retries, wait
                        )
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    text = await r.text()
                    return json.loads(text)
        except Exception as e:
            logging.error("News fetch attempt %s failed: %s", attempt + 1, e)
            if attempt < retries - 1:
                await asyncio.sleep(15)
    raise RuntimeError("News feed unavailable after retries")


# ---------------- NEWS SCHEDULED TASKS ----------------

@tasks.loop(time=dtime(hour=0, minute=0, tzinfo=IST))
async def daily_news_digest():
    """Posts moderate + high impact events for the day at 12:00 AM IST."""
    try:
        data = await fetch_news(retries=5)
    except Exception as e:
        logging.error("Daily news digest failed after retries: %s", e)
        return

    now = datetime.now(IST)
    today = now.date()
    rows = []

    for event in data:
        if event.get("impact") not in ("High", "Medium"):
            continue

        event_time = datetime.fromisoformat(event["date"]).astimezone(IST)

        if event_time.date() != today:
            continue

        icon = "🔴" if event["impact"] == "High" else "🟠"
        rows.append(
            f"{icon} **{event['country']}** {event['title']} • "
            f"{event_time:%I:%M %p} IST"
        )

    embed = card(
        "📰 TODAY'S ECONOMIC CALENDAR",
        "\n".join(rows) if rows else "No moderate or high impact events today.",
        color=GOLD if rows else BLUE
    )
    embed.add_field(
        name="⚠️ NOTE",
        value="High impact events can cause volatility. Plan risk accordingly. Educational only.",
        inline=False
    )

    for guild in bot.guilds:
        channel = find(guild, "news")
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                logging.exception("Daily digest send failed")


@daily_news_digest.before_loop
async def before_daily_digest():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def news_alert():
    """Alerts 15 minutes before moderate and high impact events."""
    try:
        data = await fetch_news(retries=2)
    except Exception as e:
        logging.error("News alert fetch failed: %s", e)
        return

    now = datetime.now(timezone.utc)

    for event in data:
        if event.get("impact") not in ("High", "Medium"):
            continue

        event_time = datetime.fromisoformat(event["date"]).astimezone(timezone.utc)
        delta = event_time - now

        if timedelta(0) < delta <= timedelta(minutes=15):
            key = f"{event['title']}{event['date']}"
            if key in bot.sent_news_keys:
                continue
            bot.sent_news_keys.add(key)

            icon = "🔴" if event["impact"] == "High" else "🟠"
            colour = DANGER if event["impact"] == "High" else GOLD

            embed = card(
                f"{icon} {event['impact'].upper()} IMPACT • {event['country']}",
                f"**{event['title']}**\n"
                f"**Time:** {event_time.astimezone(IST):%I:%M %p} IST\n"
                f"**Forecast:** {event.get('forecast') or '-'}\n"
                f"**Previous:** {event.get('previous') or '-'}\n\n"
                f"Releasing in ~{int(delta.total_seconds() // 60)} minutes.",
                color=colour
            )

            for guild in bot.guilds:
                channel = find(guild, "news")
                if channel:
                    try:
                        await channel.send(embed=embed)
                    except discord.HTTPException:
                        logging.exception("News alert send failed")


@news_alert.before_loop
async def before_news_alert():
    await bot.wait_until_ready()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logging.error("Command failed: %s", error)

    message = "Action failed. Check the terminal, bot permissions and role order."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready():
    logging.info("Online as %s", bot.user)


if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN in .env")

bot.run(TOKEN)

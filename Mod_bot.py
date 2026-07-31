import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import datetime
import sqlite3
import time
from collections import defaultdict, deque

load_dotenv()

Base_dir = os.path.dirname(os.path.abspath(__file__))

# ---- DATABASE SETUP ----

def create_polls_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "polls.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS poll_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            message_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

def create_logs_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            guild_id INTEGER,
            infraction_type TEXT,
            message_content TEXT,
            timestamp DATETIME
        )
    """)
    conn.commit()
    conn.close()

def create_user_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users_per_guild (
            user_id INTEGER,
            warning_count INTEGER,
            guild_id INTEGER,
            PRIMARY KEY(user_id, guild_id)
        )
    """)
    conn.commit()
    conn.close()

def naughty_words_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS naughty_words (
            word TEXT,
            guild_id INTEGER,
            PRIMARY KEY(word, guild_id)
        )
    """)
    conn.commit()
    conn.close()

def create_guild_settings_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            ban_feature INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

create_logs_table()
naughty_words_table()
create_user_table()
create_guild_settings_table()
create_polls_table()

def migrate_db():
    """Add new columns to existing tables without losing data."""
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    for column_def in [
        "spam_protection INTEGER DEFAULT 0",
        "raid_protection INTEGER DEFAULT 0",
    ]:
        try:
            cursor.execute(f"ALTER TABLE guild_settings ADD COLUMN {column_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()

migrate_db()

# ---- DB HELPERS ----

def log_infraction(user_id, username, guild_id, infraction_type, content):
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO mod_logs (user_id, username, guild_id, infraction_type, message_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, guild_id, infraction_type, content, datetime.datetime.now()))
    conn.commit()
    conn.close()

def increase_and_get_warning_count(user_id, guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT warning_count FROM users_per_guild WHERE user_id = ? AND guild_id = ?",
        (user_id, guild_id)
    )
    result = cursor.fetchone()
    if result is None:
        cursor.execute(
            "INSERT INTO users_per_guild (user_id, warning_count, guild_id) VALUES (?, 1, ?)",
            (user_id, guild_id)
        )
        conn.commit()
        conn.close()
        return 1
    cursor.execute(
        "UPDATE users_per_guild SET warning_count = ? WHERE user_id = ? AND guild_id = ?",
        (result[0] + 1, user_id, guild_id)
    )
    conn.commit()
    conn.close()
    return result[0] + 1

def get_naughty_words(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT word FROM naughty_words WHERE guild_id = ?", (guild_id,))
    result = cursor.fetchall()
    conn.close()
    return [word[0] for word in result]

def get_ban_feature_enabled(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT ban_feature FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0])

def set_ban_feature_enabled(guild_id, enabled: bool):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO guild_settings (guild_id, ban_feature)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET ban_feature = excluded.ban_feature
    """, (guild_id, int(enabled)))
    conn.commit()
    conn.close()

def get_spam_protection_enabled(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT spam_protection FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0])

def set_spam_protection_enabled(guild_id, enabled: bool):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO guild_settings (guild_id, spam_protection)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET spam_protection = excluded.spam_protection
    """, (guild_id, int(enabled)))
    conn.commit()
    conn.close()

def get_raid_protection_enabled(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT raid_protection FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0])

def set_raid_protection_enabled(guild_id, enabled: bool):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO guild_settings (guild_id, raid_protection)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET raid_protection = excluded.raid_protection
    """, (guild_id, int(enabled)))
    conn.commit()
    conn.close()

def save_poll_message(channel_id, message_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "polls.db"))
    cursor = conn.cursor()
    cursor.execute("INSERT INTO poll_messages (channel_id, message_id) VALUES (?, ?)", (channel_id, message_id))
    conn.commit()
    conn.close()

def get_poll_messages():
    conn = sqlite3.connect(os.path.join(Base_dir, "polls.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, message_id FROM poll_messages")
    rows = cursor.fetchall()
    conn.close()
    return rows

def clear_poll_messages():
    conn = sqlite3.connect(os.path.join(Base_dir, "polls.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM poll_messages")
    conn.commit()
    conn.close()

# ---- TEXT NORMALIZATION ----

LEET_MAP = str.maketrans({
    '@': 'a', '4': 'a',
    '!': 'i', '1': 'i', 'l': 'i',
    '0': 'o', '3': 'e',
    '$': 's', '5': 's',
    '7': 't', '+': 't',
    '(': 'c', '*': '',
})

def normalize_text(text):
    return text.lower().translate(LEET_MAP)

def contains_banned_word(content, banned_words):
    variants = [
        content.lower(),
        normalize_text(content),
        content.lower().replace(' ', ''),
        normalize_text(content).replace(' ', ''),
    ]
    return any(word in variant for word in banned_words for variant in variants)

# ---- SPAM / RAID CONSTANTS & TRACKERS ----

SPAM_MESSAGE_LIMIT = 5    # max messages allowed within the time window
SPAM_TIME_WINDOW   = 5    # seconds
MENTION_LIMIT      = 5    # max user/role mentions in a single message
RAID_JOIN_LIMIT    = 10   # joins that trigger a raid alert
RAID_TIME_WINDOW   = 30   # seconds

# (user_id, guild_id) -> deque of timestamps
message_tracker: dict = defaultdict(deque)
# guild_id -> deque of timestamps
join_tracker: dict = defaultdict(deque)

def is_message_spam(user_id: int, guild_id: int) -> bool:
    """Return True if the user has exceeded the message rate limit."""
    key = (user_id, guild_id)
    now = time.time()
    timestamps = message_tracker[key]
    timestamps.append(now)
    cutoff = now - SPAM_TIME_WINDOW
    while timestamps and timestamps[0] < cutoff:
        timestamps.popleft()
    return len(timestamps) > SPAM_MESSAGE_LIMIT

def is_mention_spam(message: discord.Message) -> bool:
    """Return True if the message contains too many mentions."""
    return len(message.mentions) + len(message.role_mentions) > MENTION_LIMIT

# ---- BOT SETUP ----

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---- HELPERS ----

def get_target_channel(guild):
    """Return the first channel the bot can send messages to."""
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel
    return next(
        (ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages),
        None
    )

async def broadcast_to_guilds(embed=None, poll=None):
    """Send an embed or poll to the first available channel in every guild."""
    sent = 0
    for guild in bot.guilds:
        channel = get_target_channel(guild)
        if not channel:
            continue
        try:
            if poll:
                msg = await channel.send(poll=poll)
                save_poll_message(channel.id, msg.id)
            else:
                await channel.send(embed=embed)
            sent += 1
        except discord.Forbidden:
            pass
    return sent

# ---- BACKGROUND TASKS ----

@tasks.loop(hours=24)
async def cleanup_old_logs():
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM mod_logs WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"[Log Cleanup] Deleted {deleted} log(s) older than 30 days.")

# ---- EVENTS ----

@bot.event
async def on_ready():
    await bot.tree.sync()
    cleanup_old_logs.start()
    assert bot.user
    print(f"Logged in as {bot.user.name}, bot is online")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. Use `/commands` to see how to use it.")
    elif not isinstance(error, commands.CommandNotFound):
        raise error

@bot.event
async def on_guild_join(guild):
    embed = discord.Embed(
        title="👋 Hey there! I'm Mod Bot.",
        description="Built by Allancash123 to help keep your server clean and safe.",
        color=discord.Color.blue()
    )
    embed.add_field(name="What I do", value="I automatically filter banned words, detect spam, and alert you to raids — keeping your server clean and safe.", inline=False)
    embed.add_field(
        name="⚠️ Warning System",
        value=(
            "• **1st warning** — User is warned\n"
            "• **2nd warning** — 1 hour timeout\n"
            "• **3rd warning** — 2 hour timeout\n"
            "• **4th warning** — Ban *(optional, off by default — enable with `/banfeature`)*"
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Spam Protection *(off by default)*",
        value=(
            "Automatically actions users who send too many messages too quickly or mass-mention members.\n"
            "Enable with `/spamprotection`."
        ),
        inline=False
    )
    embed.add_field(
        name="🚨 Raid Protection *(off by default)*",
        value=(
            "Alerts you and the server owner if a large wave of members joins in a short window.\n"
            "Enable with `/raidprotection`."
        ),
        inline=False
    )
    embed.add_field(name="Get started", value="Use `/commands` to see everything I can do, or `/about` to learn more.", inline=False)
    embed.add_field(name="💬 Support Server", value="Need help or have questions? [Join our support server](https://discord.gg/jQvZXXXzf)", inline=False)
    channel = get_target_channel(guild)
    if channel:
        await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if bot.user and message.author.id == bot.user.id:
        return
    if message.guild is None or not isinstance(message.author, discord.Member):
        await bot.process_commands(message)
        return

    if not message.author.guild_permissions.moderate_members:
        banned_words = get_naughty_words(message.guild.id)
        if banned_words and contains_banned_word(message.content, banned_words):
            await handle_infraction(message)
        elif get_spam_protection_enabled(message.guild.id):
            if is_mention_spam(message) or is_message_spam(message.author.id, message.guild.id):
                await handle_infraction(message)

    await bot.process_commands(message)

async def handle_infraction(message):
    member = message.author
    num_warnings = increase_and_get_warning_count(member.id, message.guild.id)
    clean_content = (message.content[:100] + '..') if len(message.content) > 100 else message.content
    ban_enabled = get_ban_feature_enabled(message.guild.id)

    if num_warnings >= 4 and ban_enabled:
        log_infraction(member.id, str(member), message.guild.id, "Ban (4th warning)", clean_content)
        try:
            await message.delete()
            try:
                await member.send(f"You have been **banned** from **{message.guild.name}** for repeatedly using banned words.")
            except discord.Forbidden:
                pass
            await message.guild.ban(member, reason="4th warning — repeated use of banned words.")
            await message.channel.send(f"🔨 {member.mention} has been banned for repeatedly using banned words.")
        except discord.Forbidden:
            await message.channel.send(
                f"⚠️ I was unable to ban {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**."
            )

    elif num_warnings >= 3:
        log_infraction(member.id, str(member), message.guild.id, "Timeout (2hr)", clean_content)
        try:
            await member.timeout(datetime.timedelta(minutes=120), reason="3rd warning — exceeded naughty word limit")
            ban_notice = " This is your final warning — one more and you will be **banned**." if ban_enabled else ""
            await message.channel.send(
                f"{member.mention} has been timed out for 2 hours for saying too many naughty words.{ban_notice}"
            )
            await message.delete()
        except discord.Forbidden:
            await message.channel.send(
                f"⚠️ I was unable to timeout {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**. "
                f"An admin needs to fix this for me to enforce timeouts properly."
            )

    elif num_warnings == 2:
        log_infraction(member.id, str(member), message.guild.id, "Timeout (1hr)", clean_content)
        try:
            await member.timeout(datetime.timedelta(minutes=60), reason="Second naughty word warning")
            try:
                await member.send("You have been timed out for an hour for saying too many naughty words. One more time and you'll be timed out for 2 hours.")
            except discord.Forbidden:
                pass
            await message.channel.send(f"{member.mention} has been timed out for an hour for saying too many naughty words.")
            await message.delete()
        except discord.Forbidden:
            await message.channel.send(
                f"⚠️ I was unable to timeout {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**. "
                f"An admin needs to fix this for me to enforce timeouts properly."
            )

    else:  # 1st warning
        log_infraction(member.id, str(member), message.guild.id, "Warning #1", clean_content)
        try:
            await member.send("Please do not say naughty words. You have been warned. One more time and you'll be timed out for an hour.")
        except discord.Forbidden:
            pass
        await message.channel.send(f"{member.mention} Please do not say naughty words.")
        await message.delete()

@bot.event
async def on_member_join(member):
    guild = member.guild
    if not get_raid_protection_enabled(guild.id):
        return

    now = time.time()
    joins = join_tracker[guild.id]
    joins.append(now)
    cutoff = now - RAID_TIME_WINDOW
    while joins and joins[0] < cutoff:
        joins.popleft()

    if len(joins) >= RAID_JOIN_LIMIT:
        joins.clear()  # Reset to avoid repeated alerts for the same wave
        embed = discord.Embed(
            title="⚠️ Potential Raid Detected!",
            description=(
                f"**{RAID_JOIN_LIMIT}+ members joined within {RAID_TIME_WINDOW} seconds.**\n"
                "Please review recent joins and take action if necessary."
            ),
            color=discord.Color.red()
        )
        channel = get_target_channel(guild)
        if channel:
            await channel.send(embed=embed)
        if guild.owner:
            try:
                await guild.owner.send(
                    f"⚠️ **Raid alert for {guild.name}!**\n"
                    f"{RAID_JOIN_LIMIT}+ members joined within {RAID_TIME_WINDOW} seconds. "
                    "Please check your server immediately."
                )
            except discord.Forbidden:
                pass

# ---- OWNER COMMANDS ----

@bot.command()
@commands.is_owner()
async def announce(ctx):
    embed = discord.Embed(title="Mod Bot Update", color=discord.Color.blue())
    embed.add_field(
        name="🔨 Optional Ban Feature",
        value=(
            "Mod Bot now supports an optional **4th warning ban**.\n\n"
            "How it works:\n"
            "• 1st warning — User is warned\n"
            "• 2nd warning — 1 hour timeout\n"
            "• 3rd warning — 2 hour timeout\n"
            "• 4th warning — **Ban** *(if enabled)*\n\n"
            "This is **off by default**. Moderators can enable it with `/banfeature`."
        ),
        inline=False
    )
    embed.add_field(
        name="🔗 Links",
        value=(
            "⬆️ [Vote for the bot](https://top.gg/bot/1482092741352624228?s=0f59ebc3a1c91)\n"
            "📩 [Invite the bot](https://discord.com/oauth2/authorize?client_id=1482092741352624228&permissions=4504974285417526&integration_type=0&scope=bot)\n"
            "💬 [Join our support server](https://discord.gg/jQvZXXXzf)"
        ),
        inline=False
    )
    sent = await broadcast_to_guilds(embed=embed)
    await ctx.send(f"Update message sent to {sent} server(s).")

@bot.command()
@commands.is_owner()
async def supportserver(ctx):
    embed = discord.Embed(
        title="💬 Join the Mod Bot Support Server!",
        description="We now have an official support server! Join for help, updates, and to share feedback directly with the team.",
        color=discord.Color.green()
    )
    embed.add_field(name="Join here", value="[Click to join the support server](https://discord.gg/jQvZXXXzf)", inline=False)
    sent = await broadcast_to_guilds(embed=embed)
    await ctx.send(f"Support server announcement sent to {sent} server(s).")

@bot.command()
@commands.is_owner()
async def survey(ctx):
    embed = discord.Embed(
        title="We Want Your Feedback!",
        description="Got a suggestion or running into an issue with Mod Bot? We'd love to hear from you — it helps make the bot better for everyone.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="📋 Fill out the form",
        value="[Click here to share your feedback](https://docs.google.com/forms/d/e/1FAIpQLSe-R37QQdaBwu3tefEsRuxYNuc00soZ6BWcp-rNo8eXYn4dNw/viewform?usp=dialog)",
        inline=False
    )
    embed.set_footer(text="It only takes a minute — thank you for helping improve Mod Bot!")
    sent = await broadcast_to_guilds(embed=embed)
    await ctx.send(f"Feedback announcement sent to {sent} server(s).")

@bot.command()
@commands.is_owner()
async def pollannounce(ctx):
    poll = discord.Poll(
        question="Should Mod Bot add a 4th warning that bans the user from the server?",
        duration=datetime.timedelta(days=7)
    )
    poll.add_answer(text="Yes, add it!", emoji="✅")
    poll.add_answer(text="No, keep it at 3 warnings", emoji="❌")
    poll.add_answer(text="Yes, but make it optional per server", emoji="⚙️")
    sent = await broadcast_to_guilds(poll=poll)
    await ctx.send(f"Poll sent to {sent} server(s).")

@bot.command()
@commands.is_owner()
async def pollresults(ctx):
    await ctx.send("Scanning all servers for poll messages...")
    clear_poll_messages()

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).read_message_history:
                continue
            try:
                async for msg in channel.history(limit=50):
                    if bot.user and msg.author.id == bot.user.id and msg.poll:
                        save_poll_message(channel.id, msg.id)
                        break
            except discord.Forbidden:
                continue

    records = get_poll_messages()
    if not records:
        await ctx.send("No poll messages found.")
        return

    totals, checked, failed = {}, 0, 0
    for channel_id, message_id in records:
        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            failed += 1
            continue
        try:
            msg = await channel.fetch_message(message_id)
            if msg.poll:
                for answer in msg.poll.answers:
                    totals[answer.text] = totals.get(answer.text, 0) + answer.vote_count
                checked += 1
        except (discord.NotFound, discord.Forbidden):
            failed += 1

    if not totals:
        await ctx.send("Could not retrieve any poll results.")
        return

    total_votes = sum(totals.values())
    embed = discord.Embed(
        title="Poll Results — 4th Warning Ban Feature",
        description=f"Collected from {checked} server(s).",
        color=discord.Color.blue()
    )
    for label, count in totals.items():
        percent = round((count / total_votes) * 100) if total_votes > 0 else 0
        embed.add_field(name=label, value=f"{count} vote(s) — {percent}%", inline=False)
    if failed:
        embed.set_footer(text=f"{failed} server(s) could not be reached.")
    await ctx.send(embed=embed)

@bot.command()
@commands.is_owner()
async def servers(ctx):
    if not bot.guilds:
        await ctx.send("I am not in any servers.")
        return
    server_list = "\n".join(f"{i+1}. {g.name} ({g.member_count} members)" for i, g in enumerate(bot.guilds))
    await ctx.send(f"**Servers I'm in ({len(bot.guilds)}):**\n{server_list}")

# ---- SLASH COMMANDS ----

@bot.tree.command(name="addword", description="Add a banned word to this server's list (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_addword(interaction: discord.Interaction, word: str):
    if interaction.guild is None:
        return
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO naughty_words (word, guild_id) VALUES (?, ?)", (word.lower(), interaction.guild.id))
        conn.commit()
        await interaction.response.send_message(f"Added `{word}` to the banned words list.")
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"`{word}` is already in the banned words list.")
    finally:
        conn.close()

@bot.tree.command(name="removeword", description="Remove a banned word from this server's list (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_removeword(interaction: discord.Interaction, word: str):
    if interaction.guild is None:
        return
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM naughty_words WHERE word = ? AND guild_id = ?", (word.lower(), interaction.guild.id))
    conn.commit()
    removed = cursor.rowcount
    conn.close()
    if removed:
        await interaction.response.send_message(f"Removed `{word}` from the banned words list.")
    else:
        await interaction.response.send_message(f"`{word}` is not in the banned words list.")

@bot.tree.command(name="listwords", description="See all banned words for this server (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_listwords(interaction: discord.Interaction):
    if interaction.guild is None:
        return
    words = get_naughty_words(interaction.guild.id)
    if not words:
        await interaction.response.send_message("There are currently no banned words in this server.")
        return
    embed = discord.Embed(
        title="Banned Words List",
        description=", ".join(f"`{w}`" for w in words),
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="banfeature", description="Toggle the 4th warning ban feature on or off (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_banfeature(interaction: discord.Interaction):
    if interaction.guild is None:
        return
    new_state = not get_ban_feature_enabled(interaction.guild.id)
    set_ban_feature_enabled(interaction.guild.id, new_state)
    status = "**enabled** ✅" if new_state else "**disabled** ❌"
    consequence = "Users will now be banned on their 4th warning." if new_state else "Users will only receive timeouts."
    await interaction.response.send_message(f"The 4th warning ban feature is now {status}.\n{consequence}")

@bot.tree.command(name="clearwarnings", description="Clear a user's warnings (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        return
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users_per_guild WHERE user_id = ? AND guild_id = ?", (member.id, interaction.guild.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"Warnings for {member.mention} have been cleared.")

@bot.tree.command(name="logs", description="View recent infractions for a user (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_logs(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        return
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT infraction_type, message_content, timestamp
        FROM mod_logs WHERE user_id = ? AND guild_id = ?
        ORDER BY timestamp DESC LIMIT 10
    """, (member.id, interaction.guild.id))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message(f"No logs found for {member.display_name}.")
        return
    log_text = f"**Recent logs for {member.mention}:**\n"
    for infraction_type, content, time in rows:
        log_text += f"• `[{time[:19]}]` **{infraction_type}**: \"{content}\"\n"
    await interaction.response.send_message(log_text)

@bot.tree.command(name="spamprotection", description="Toggle spam protection on or off (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_spamprotection(interaction: discord.Interaction):
    if interaction.guild is None:
        return
    new_state = not get_spam_protection_enabled(interaction.guild.id)
    set_spam_protection_enabled(interaction.guild.id, new_state)
    status = "**enabled** ✅" if new_state else "**disabled** ❌"
    detail = (
        f"Messages exceeding **{SPAM_MESSAGE_LIMIT} messages in {SPAM_TIME_WINDOW}s** "
        f"or containing more than **{MENTION_LIMIT} mentions** will be actioned."
        if new_state else
        "Message and mention spam will no longer be automatically actioned."
    )
    await interaction.response.send_message(f"Spam protection is now {status}.\n{detail}")

@bot.tree.command(name="raidprotection", description="Toggle raid protection on or off (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_raidprotection(interaction: discord.Interaction):
    if interaction.guild is None:
        return
    new_state = not get_raid_protection_enabled(interaction.guild.id)
    set_raid_protection_enabled(interaction.guild.id, new_state)
    status = "**enabled** ✅" if new_state else "**disabled** ❌"
    detail = (
        f"An alert will be sent if **{RAID_JOIN_LIMIT}+ members join within {RAID_TIME_WINDOW}s**."
        if new_state else
        "Raid join alerts have been turned off."
    )
    await interaction.response.send_message(f"Raid protection is now {status}.\n{detail}")

@bot.tree.command(name="about", description="Learn about this bot")
async def slash_about(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**Moderation Bot**\n"
        "This bot helps moderate the server by filtering banned words and issuing warnings to users who use them.\n\n"
        "**How it works:**\n"
        "- If a user says a banned word, they get a warning.\n"
        "- 2nd warning = 1 hour timeout.\n"
        "- 3rd warning = 2 hour timeout.\n"
        "- 4th warning = Ban *(optional, off by default — Moderators can enable it with `/banfeature`)*\n\n"
        "Use `/commands` to see the full list of commands."
    )

@bot.tree.command(name="commands", description="See the full list of bot commands")
async def slash_list_commands(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**Bot Command List**\n\n"
        "`/about` - Shows information about the bot\n"
        "`/commands` - Shows this command list\n"
        "`/addword <word>` - Add a banned word (Moderators only)\n"
        "`/removeword <word>` - Remove a banned word (Moderators only)\n"
        "`/listwords` - See all banned words (Moderators only)\n"
        "`/clearwarnings <member>` - Clear a user's warnings (Moderators only)\n"
        "`/logs <member>` - View a user's infractions (Moderators only)\n"
        "`/banfeature` - Toggle the 4th warning ban on or off (Moderators only)\n"
        "`/spamprotection` - Toggle message & mention spam protection (Moderators only)\n"
        "`/raidprotection` - Toggle raid join alerts (Moderators only)\n"
    )

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command.")
    else:
        await interaction.response.send_message("Something went wrong. Please try again.")

# ---- RUN ----

TOKEN = os.getenv("DISCORD_TOKEN_TEST")
assert TOKEN, "DISCORD_TOKEN_TEST environment variable is not set"
bot.run(TOKEN)

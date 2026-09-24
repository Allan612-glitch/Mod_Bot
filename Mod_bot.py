import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import datetime
import sqlite3
import time
import re
import unicodedata
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
    """Add new settings and reason-specific warning storage without losing data."""
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    for column_def in [
        "spam_protection INTEGER DEFAULT 0",
        "raid_protection INTEGER DEFAULT 0",
        "warning_expiry_days INTEGER DEFAULT 30",
        "mod_log_channel_id INTEGER",
    ]:
        try:
            cursor.execute(f"ALTER TABLE guild_settings ADD COLUMN {column_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()

migrate_db()

# ---- DB HELPERS ----

def create_warning_counts_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warning_counts (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            offense_type TEXT NOT NULL,
            warning_count INTEGER NOT NULL DEFAULT 0,
            last_warning_at TEXT NOT NULL,
            PRIMARY KEY (user_id, guild_id, offense_type)
        )
    """)
    # Preserve historical shared warnings as banned-word warnings. Since the
    # old schema had no timestamp or reason, start their expiry window now.
    cursor.execute("""
        INSERT OR IGNORE INTO warning_counts
            (user_id, guild_id, offense_type, warning_count, last_warning_at)
        SELECT user_id, guild_id, 'banned_word', warning_count, ?
        FROM users_per_guild
    """, (datetime.datetime.now().isoformat(),))
    conn.commit()
    conn.close()

create_warning_counts_table()

def log_infraction(user_id, username, guild_id, infraction_type, content):
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO mod_logs (user_id, username, guild_id, infraction_type, message_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, guild_id, infraction_type, content, datetime.datetime.now()))
    conn.commit()
    conn.close()

def increase_and_get_warning_count(user_id, guild_id, offense_type, expiry_days):
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT warning_count, last_warning_at FROM warning_counts
        WHERE user_id = ? AND guild_id = ? AND offense_type = ?
        """,
        (user_id, guild_id, offense_type)
    )
    result = cursor.fetchone()
    now = datetime.datetime.now()
    count = result[0] if result else 0
    if result and expiry_days > 0:
        last_warning_at = datetime.datetime.fromisoformat(result[1])
        if now - last_warning_at >= datetime.timedelta(days=expiry_days):
            count = 0

    count += 1
    cursor.execute("""
        INSERT INTO warning_counts
            (user_id, guild_id, offense_type, warning_count, last_warning_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, guild_id, offense_type) DO UPDATE SET
            warning_count = excluded.warning_count,
            last_warning_at = excluded.last_warning_at
    """, (user_id, guild_id, offense_type, count, now.isoformat()))
    conn.commit()
    conn.close()
    return count

def get_warning_count(user_id, guild_id, offense_type, expiry_days):
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT warning_count, last_warning_at FROM warning_counts
        WHERE user_id = ? AND guild_id = ? AND offense_type = ?
    """, (user_id, guild_id, offense_type))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return 0
    if expiry_days > 0:
        last_warning_at = datetime.datetime.fromisoformat(row[1])
        if datetime.datetime.now() - last_warning_at >= datetime.timedelta(days=expiry_days):
            return 0
    return row[0]

def get_warning_expiry_days(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT warning_expiry_days FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] is not None else 30

def set_warning_expiry_days(guild_id, days):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO guild_settings (guild_id, warning_expiry_days)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET warning_expiry_days = excluded.warning_expiry_days
    """, (guild_id, days))
    conn.commit()
    conn.close()

def get_mod_log_channel_id(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT mod_log_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_mod_log_channel_id(guild_id, channel_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "guild_settings.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO guild_settings (guild_id, mod_log_channel_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET mod_log_channel_id = excluded.mod_log_channel_id
    """, (guild_id, channel_id))
    conn.commit()
    conn.close()

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
    '!': 'i', '1': 'i', 'l': 'i', '|': 'i',
    '0': 'o', '3': 'e',
    '$': 's', '5': 's',
    '7': 't', '+': 't',
    '(': 'c', '*': '',
})

HOMOGLYPH_MAP = str.maketrans({
    # Common Cyrillic characters that resemble Latin letters.
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0445": "x", "\u0443": "y", "\u0456": "i",
    "\u0458": "j", "\u043a": "k", "\u043c": "m", "\u0442": "t",
    "\u0432": "b", "\u043d": "h", "\u0455": "s",
    # Frequently used Greek lookalikes.
    "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k",
    "\u03bd": "v", "\u03bf": "o", "\u03c1": "p", "\u03c4": "t",
    "\u03c5": "y", "\u03c7": "x",
})

def _unicode_normalize(text):
    """Normalize compatibility forms and remove accents/invisible controls."""
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(
        char for char in text
        if not unicodedata.category(char).startswith(("M", "C"))
    )

def normalize_text(text):
    return _unicode_normalize(text).translate(HOMOGLYPH_MAP).translate(LEET_MAP)

def _matching_variants(text):
    """Build conservative variants for punctuation, spacing, and stretched text."""
    unicode_text = _unicode_normalize(text)
    leet_text = unicode_text.translate(HOMOGLYPH_MAP).translate(LEET_MAP)
    variants = {unicode_text, leet_text}
    for value in (unicode_text, leet_text):
        compact = "".join(char for char in value if char.isalnum())
        if compact:
            variants.add(compact)
            # Catch elongated text without changing the original candidate.
            variants.add(re.sub(r"(.)\1{2,}", r"\1\1", compact))
            variants.add(re.sub(r"(.)\1+", r"\1", compact))
    return {variant for variant in variants if variant}

def contains_banned_word(content, banned_words):
    content_variants = _matching_variants(content)
    for word in banned_words:
        word_variants = _matching_variants(word)
        if any(
            candidate in content_variant
            for candidate in word_variants
            for content_variant in content_variants
        ):
            return True
    return False

# ---- SPAM / RAID CONSTANTS & TRACKERS ----

SPAM_MESSAGE_LIMIT = 5    # max messages allowed within the time window
SPAM_TIME_WINDOW   = 5    # seconds
MENTION_LIMIT      = 5    # max user/role mentions in a single message
REPEAT_MESSAGE_LIMIT = 3  # identical messages within the repeat window
REPEAT_TIME_WINDOW = 10   # seconds
LINK_MESSAGE_LIMIT = 3   # link-containing messages within the link window
LINK_TIME_WINDOW = 10    # seconds
LINKS_PER_MESSAGE_LIMIT = 3
SPAM_ACTION_COOLDOWN = 15  # avoid stacking warnings for one uninterrupted burst
RAID_JOIN_LIMIT    = 10   # joins that trigger a raid alert
RAID_TIME_WINDOW   = 30   # seconds
NEW_ACCOUNT_DAYS   = 7

# (user_id, guild_id) -> deque of timestamps
message_tracker: dict = defaultdict(deque)
# (user_id, guild_id) -> deque of (timestamp, normalized message)
repeat_tracker: dict = defaultdict(deque)
# (user_id, guild_id) -> deque of timestamps for link-containing messages
link_tracker: dict = defaultdict(deque)
spam_action_tracker: dict = {}
# guild_id -> deque of (join timestamp, account age in days)
join_tracker: dict = defaultdict(deque)
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

def is_mention_spam(message: discord.Message) -> bool:
    """Return True if the message contains too many mentions."""
    return len(message.mentions) + len(message.role_mentions) > MENTION_LIMIT

def get_spam_reason(message: discord.Message):
    """Return a reason string when a message crosses any spam threshold."""
    if message.guild is None:
        return None
    key = (message.author.id, message.guild.id)
    now = time.monotonic()

    # One ongoing burst should cause one action, not a warning for every message.
    last_action = spam_action_tracker.get(key)
    if last_action is not None and now - last_action < SPAM_ACTION_COOLDOWN:
        return None

    if is_mention_spam(message):
        spam_action_tracker[key] = now
        return "mass mentions"

    timestamps = message_tracker[key]
    timestamps.append(now)
    while timestamps and timestamps[0] < now - SPAM_TIME_WINDOW:
        timestamps.popleft()
    if len(timestamps) > SPAM_MESSAGE_LIMIT:
        spam_action_tracker[key] = now
        return "message rate limit"

    normalized = " ".join(message.content.casefold().split())
    if normalized:
        repeats = repeat_tracker[key]
        repeats.append((now, normalized))
        while repeats and repeats[0][0] < now - REPEAT_TIME_WINDOW:
            repeats.popleft()
        if sum(text == normalized for _, text in repeats) >= REPEAT_MESSAGE_LIMIT:
            spam_action_tracker[key] = now
            return "repeated identical messages"

    links_in_message = len(URL_PATTERN.findall(message.content))
    if links_in_message >= LINKS_PER_MESSAGE_LIMIT:
        spam_action_tracker[key] = now
        return "multiple links in one message"
    if links_in_message:
        links = link_tracker[key]
        links.append(now)
        while links and links[0] < now - LINK_TIME_WINDOW:
            links.popleft()
        if len(links) >= LINK_MESSAGE_LIMIT:
            spam_action_tracker[key] = now
            return "link flood"

    return None

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
            "Alerts moderators and the server owner about join bursts, including how many new accounts are under 7 days old.\n"
            "Enable with `/raidprotection`."
        ),
        inline=False
    )
    embed.add_field(
        name="🧾 Moderator tools",
        value=(
            "Set a private action-log channel with `/setmodlog` and warning expiry with `/warningexpiry` "
            "(default: 30 days). Spam and banned-word warnings are tracked separately."
        ),
        inline=False
    )
    embed.add_field(name="Get started", value="Use `/commands` to see everything I can do, or `/about` to learn more.", inline=False)
    embed.add_field(name="💬 Support Server", value="Need help or have questions? [Join our support server](https://discord.gg/fEWnEHPXH)", inline=False)
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
            await handle_infraction(message, offense_type="banned_word")
        elif get_spam_protection_enabled(message.guild.id):
            spam_reason = get_spam_reason(message)
            if spam_reason:
                await handle_infraction(message, offense_type="spam", detail=spam_reason)

    await bot.process_commands(message)

async def send_mod_action_log(guild, member, offense_label, action, warning_count, content, detail=None):
    channel_id = get_mod_log_channel_id(guild.id)
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            print(f"[Mod Log] Could not fetch configured channel {channel_id}: {error}")
            return
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        print(f"[Mod Log] Configured mod-log channel {channel_id} is unavailable in guild {guild.id}.")
        return

    embed = discord.Embed(
        title=f"Moderation action: {action}",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=offense_label, inline=True)
    embed.add_field(name="Warnings", value=str(warning_count), inline=True)
    if detail:
        embed.add_field(name="Detection", value=detail[:256], inline=False)
    if content:
        embed.add_field(name="Message", value=content[:1000], inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"[Mod Log] Could not post to channel {channel_id}: {error}")

async def handle_infraction(message, offense_type="banned_word", detail=None):
    member = message.author
    guild = message.guild
    if guild is None:
        return

    expiry_days = get_warning_expiry_days(guild.id)
    num_warnings = increase_and_get_warning_count(
        member.id, guild.id, offense_type, expiry_days
    )
    clean_content = (message.content[:100] + '..') if len(message.content) > 100 else message.content
    ban_enabled = get_ban_feature_enabled(guild.id)

    if offense_type == "spam":
        offense_label = "Spam"
        dm_warn    = "Please do not spam. You have been warned. One more time and you'll be timed out for an hour."
        dm_1hr     = "You have been timed out for an hour for spamming. One more time and you'll be timed out for 2 hours."
        pub_warn   = f"{member.mention} Please do not spam."
        pub_1hr    = f"{member.mention} has been timed out for an hour for spamming."
        pub_2hr    = f"{member.mention} has been timed out for 2 hours for spamming."
        pub_ban    = f"🔨 {member.mention} has been banned for repeated spamming."
        dm_ban     = f"You have been **banned** from **{guild.name}** for repeated spamming."
        ban_reason = "4th warning — repeated spamming."
        timeout_reason_3 = "3rd warning — repeated spamming"
        timeout_reason_2 = "2nd warning — spamming"
    else:
        offense_label = "Banned-word filter"
        dm_warn    = "Please do not say naughty words. You have been warned. One more time and you'll be timed out for an hour."
        dm_1hr     = "You have been timed out for an hour for saying too many naughty words. One more time and you'll be timed out for 2 hours."
        pub_warn   = f"{member.mention} Please do not say naughty words."
        pub_1hr    = f"{member.mention} has been timed out for an hour for saying too many naughty words."
        pub_2hr    = f"{member.mention} has been timed out for 2 hours for saying too many naughty words."
        pub_ban    = f"🔨 {member.mention} has been banned for repeatedly using banned words."
        dm_ban     = f"You have been **banned** from **{guild.name}** for repeatedly using banned words."
        ban_reason = "4th warning — repeated use of banned words."
        timeout_reason_3 = "3rd warning — exceeded naughty word limit"
        timeout_reason_2 = "Second naughty word warning"

    if num_warnings >= 4 and ban_enabled:
        action = "Ban (4th warning)"
        try:
            await message.delete()
            try:
                await member.send(dm_ban)
            except discord.Forbidden:
                pass
            await guild.ban(member, reason=ban_reason)
            await message.channel.send(pub_ban)
        except discord.Forbidden:
            action = "Ban failed (permission issue)"
            await message.channel.send(
                f"⚠️ I was unable to ban {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**."
            )

    elif num_warnings >= 3:
        action = "Timeout (2hr)"
        try:
            await member.timeout(datetime.timedelta(minutes=120), reason=timeout_reason_3)
            ban_notice = " This is your final warning — one more and you will be **banned**." if ban_enabled else ""
            await message.channel.send(f"{pub_2hr}{ban_notice}")
            await message.delete()
        except discord.Forbidden:
            action = "Timeout failed (permission issue)"
            await message.channel.send(
                f"⚠️ I was unable to timeout {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**. "
                f"An admin needs to fix this for me to enforce timeouts properly."
            )

    elif num_warnings == 2:
        action = "Timeout (1hr)"
        try:
            await member.timeout(datetime.timedelta(minutes=60), reason=timeout_reason_2)
            try:
                await member.send(dm_1hr)
            except discord.Forbidden:
                pass
            await message.channel.send(pub_1hr)
            await message.delete()
        except discord.Forbidden:
            action = "Timeout failed (permission issue)"
            await message.channel.send(
                f"⚠️ I was unable to timeout {member.mention}. "
                f"Please make sure my role is placed **above** all other roles in **Server Settings > Roles**. "
                f"An admin needs to fix this for me to enforce timeouts properly."
            )

    else:  # 1st warning
        action = "Warning #1"
        try:
            await member.send(dm_warn)
        except discord.Forbidden:
            pass
        await message.channel.send(pub_warn)
        await message.delete()

    infraction_type = f"{offense_label}: {action}"
    log_infraction(member.id, str(member), guild.id, infraction_type, clean_content)
    await send_mod_action_log(
        guild, member, offense_label, action, num_warnings, clean_content, detail
    )

@bot.event
async def on_member_join(member):
    guild = member.guild
    if not get_raid_protection_enabled(guild.id):
        return

    now = time.monotonic()
    joins = join_tracker[guild.id]
    account_age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
    joins.append((now, account_age.total_seconds() / 86400))
    cutoff = now - RAID_TIME_WINDOW
    while joins and joins[0][0] < cutoff:
        joins.popleft()

    if len(joins) >= RAID_JOIN_LIMIT:
        recent_join_count = len(joins)
        new_account_count = sum(age_days < NEW_ACCOUNT_DAYS for _, age_days in joins)
        joins.clear()  # Avoid repeating alerts for the same join wave
        embed = discord.Embed(
            title="⚠️ Potential Raid Detected!",
            description=(
                f"**{recent_join_count} members joined within {RAID_TIME_WINDOW} seconds.**\n"
                f"**{new_account_count}** of those accounts are less than {NEW_ACCOUNT_DAYS} days old.\n"
                "Review recent joins and take action if necessary; no members were automatically punished."
            ),
            color=discord.Color.red()
        )
        log_channel_id = get_mod_log_channel_id(guild.id)
        if log_channel_id:
            channel = bot.get_channel(log_channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(log_channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
                    print(f"[Raid Alert] Could not fetch configured mod-log channel: {error}")
                    channel = None
            if isinstance(channel, discord.TextChannel) and channel.guild.id == guild.id:
                try:
                    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(f"[Raid Alert] Could not send to configured mod-log channel: {error}")
        else:
            channel = get_target_channel(guild)
            if channel:
                try:
                    await channel.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(f"[Raid Alert] Could not send alert in {guild.name}: {error}")
        if guild.owner:
            try:
                await guild.owner.send(
                    f"⚠️ **Raid alert for {guild.name}!**\n"
                    f"{recent_join_count} members joined within {RAID_TIME_WINDOW} seconds; "
                    f"{new_account_count} accounts are less than {NEW_ACCOUNT_DAYS} days old. "
                    "Please review your server."
                )
            except discord.Forbidden:
                pass

# ---- OWNER COMMANDS ----

@bot.command()
@commands.is_owner()
async def announce(ctx):
    embed = discord.Embed(
        title="Mod Bot Update — Improved Moderation",
        description="New moderation protections and clearer server controls are available:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🔎 Improved banned-word detection",
        value=(
            "The filter now catches common attempts to disguise banned words with leetspeak, punctuation, "
            "invisible characters, accented or full-width letters, Greek/Cyrillic lookalikes, and stretched letters.\n"
            "Your server's existing banned-word list is unchanged."
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Spam protection",
        value=(
            "Can detect rapid messages, repeated identical messages, mass mentions, and link floods. "
            "Detected spam uses its own warning count, separate from banned-word warnings.\n"
            "**Off by default** — enable with `/spamprotection`."
        ),
        inline=False
    )
    embed.add_field(
        name="🚨 Raid alerts",
        value=(
            "Alerts moderators and the server owner when 10 or more people join within 30 seconds, "
            "and reports how many joining accounts are under 7 days old. The bot does not automatically punish joiners.\n"
            "**Off by default** — enable with `/raidprotection`."
        ),
        inline=False
    )
    embed.add_field(
        name="🧾 Private moderation log — what it means",
        value=(
            "Choose a channel with `/setmodlog #channel`. Mod Bot will post an audit note there when it takes "
            "a moderation action or detects a raid. Notes show who was involved, why the bot acted, the action, "
            "the warning count, and a short message excerpt when available.\n"
            "Only people who can view that channel can read these notes, so choose a staff-only channel and give "
            "the bot permission to send messages and embeds. This is an extra staff record; normal warnings and "
            "action notices may still appear in the original conversation. Use `/setmodlog` without a channel to clear it."
        ),
        inline=False
    )
    embed.add_field(
        name="⚠️ Warning escalation and expiry",
        value=(
            "• 1st warning — User is warned\n"
            "• 2nd warning — 1 hour timeout\n"
            "• 3rd warning — 2 hour timeout\n"
            "• 4th warning — **Ban** *(optional, enable with `/banfeature`)*\n"
            "Spam and banned-word warnings are counted separately. Warnings expire after 30 days by default; "
            "change this with `/warningexpiry <days>` or use `0` to keep them from expiring."
        ),
        inline=False
    )
    embed.add_field(
        name="🔗 Links",
        value=(
            "⬆️ [Vote for the bot](https://top.gg/bot/1482092741352624228?s=0f59ebc3a1c91)\n"
            "📩 [Invite the bot](https://discord.com/oauth2/authorize?client_id=1482092741352624228&permissions=4504974285417526&integration_type=0&scope=bot)\n"
            "💬 [Join our support server](https://discord.gg/fEWnEHPXH)"
        ),
        inline=False
    )
    embed.add_field(name="❤️ Support Mod Bot", value="[Click here to support Mod Bot's development](https://paystack.shop/pay/v0bzt1fsan)", inline=False)
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
    embed.add_field(name="Join here", value="[Click to join the support server](https://discord.gg/fEWnEHPXH)", inline=False)
    embed.add_field(name="❤️ Support Mod Bot", value="[Click here to support Mod Bot's development](https://paystack.shop/pay/v0bzt1fsan)", inline=False)
    sent = await broadcast_to_guilds(embed=embed)
    await ctx.send(f"Support server announcement sent to {sent} server(s).")

@bot.command()
@commands.is_owner()
async def supportpay(ctx):
    embed = discord.Embed(
        title="❤️ Support Mod Bot",
        description="Enjoying Mod Bot? Consider supporting its development — it helps keep the bot running and improving!",
        color=discord.Color.green()
    )
    embed.add_field(
        name="💳 Make a contribution",
        value="[Click here to support Mod Bot](https://paystack.shop/pay/v0bzt1fsan)",
        inline=False
    )
    embed.set_footer(text="Every contribution is greatly appreciated. Thank you! 🙏")
    sent = await broadcast_to_guilds(embed=embed)
    await ctx.send(f"Support payment announcement sent to {sent} server(s).")

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

    header = f"**Servers I'm in ({len(bot.guilds)}):**\n"

    chunks = []
    current_chunk = header

    for i, guild in enumerate(bot.guilds, 1):
        line = f"{i}. {guild.name} ({guild.member_count} members)\n"

        # Keep each message under Discord's 2000-character limit
        if len(current_chunk) + len(line) > 2000:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await ctx.send(chunk)

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
    cursor.execute(
        "DELETE FROM warning_counts WHERE user_id = ? AND guild_id = ?",
        (member.id, interaction.guild.id)
    )
    # Retain cleanup of the legacy row after its values have been migrated.
    cursor.execute("DELETE FROM users_per_guild WHERE user_id = ? AND guild_id = ?", (member.id, interaction.guild.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"Warnings for {member.mention} have been cleared.")

@bot.tree.command(name="warnings", description="View a member's active warning counts by category")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        return
    expiry_days = get_warning_expiry_days(interaction.guild.id)
    spam_count = get_warning_count(member.id, interaction.guild.id, "spam", expiry_days)
    word_count = get_warning_count(member.id, interaction.guild.id, "banned_word", expiry_days)
    expiry_text = f"{expiry_days} days" if expiry_days else "never"
    embed = discord.Embed(
        title=f"Active warnings for {member.display_name}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Spam", value=str(spam_count), inline=True)
    embed.add_field(name="Banned words", value=str(word_count), inline=True)
    embed.set_footer(text=f"Warnings expire after {expiry_text} without a new warning.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

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

@bot.tree.command(name="setmodlog", description="Set or clear this server's private moderation-log channel")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_setmodlog(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None
):
    if interaction.guild is None:
        return
    set_mod_log_channel_id(interaction.guild.id, channel.id if channel else None)
    if channel:
        await interaction.response.send_message(
            f"Moderation actions and raid alerts will be sent to {channel.mention}.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "Private moderation logging is disabled. Use `/setmodlog` with a channel to enable it.",
            ephemeral=True
        )

@bot.tree.command(name="warningexpiry", description="Set how long warnings remain active (Moderators only)")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_warningexpiry(
    interaction: discord.Interaction,
    days: app_commands.Range[int, 0, 365]
):
    if interaction.guild is None:
        return
    set_warning_expiry_days(interaction.guild.id, days)
    if days == 0:
        response = "Warning expiry is disabled; warnings will not expire automatically."
    else:
        response = f"Warnings now expire after **{days} days** without another warning of that category."
    await interaction.response.send_message(response, ephemeral=True)

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

@bot.tree.command(name="support", description="Support Mod Bot's development")
async def slash_support(interaction: discord.Interaction):
    embed = discord.Embed(
        title="❤️ Support Mod Bot",
        description="Enjoying Mod Bot? Consider supporting its development — it helps keep the bot running and improving!",
        color=discord.Color.green()
    )
    embed.add_field(
        name="💳 Make a contribution",
        value="[Click here to support Mod Bot](https://paystack.shop/pay/v0bzt1fsan)",
        inline=False
    )
    embed.set_footer(text="Every contribution is greatly appreciated. Thank you! 🙏")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="about", description="Learn about this bot")
async def slash_about(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**Moderation Bot**\n"
        "This bot filters banned words, detects spam and helps moderators identify possible raids.\n\n"
        "**How it works:**\n"
        "- If a user says a banned word, they get a warning.\n"
        "- 2nd warning = 1 hour timeout.\n"
        "- 3rd warning = 2 hour timeout.\n"
        "- 4th warning = Ban *(optional, off by default — Moderators can enable it with `/banfeature`)*\n\n"
        "Spam and banned-word warnings have separate counts and expire after 30 days by default. "
        "Moderators can change expiry with `/warningexpiry` and configure a private action-log channel with `/setmodlog`.\n\n"
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
        "`/warnings <member>` - View active warning counts by category (Moderators only)\n"
        "`/clearwarnings <member>` - Clear a user's warnings (Moderators only)\n"
        "`/logs <member>` - View a user's infractions (Moderators only)\n"
        "`/banfeature` - Toggle the 4th warning ban on or off (Moderators only)\n"
        "`/spamprotection` - Toggle message, repeat, mention & link-spam protection (Moderators only)\n"
        "`/raidprotection` - Toggle raid join alerts (Moderators only)\n"
        "`/setmodlog <channel>` - Configure private moderation and raid logs (Moderators only; omit channel to clear)\n"
        "`/warningexpiry <days>` - Set warning expiry from 0 to 365 days; 0 disables expiry (Moderators only)\n"
        "`/support` - Support Mod Bot's development\n"
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

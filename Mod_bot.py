import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import datetime
import sqlite3
from google import genai
# Base directory
Base_dir = os.path.dirname(os.path.abspath(__file__))
# Add your naughty words here

load_dotenv()
intents = discord.Intents.default()
intents.message_content = True

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

LEET_MAP = str.maketrans({
    '@': 'a', '4': 'a',
    '!': 'i', '1': 'i',
    '0': 'o',
    '3': 'e',
    '$': 's', '5': 's',
    '7': 't', '+': 't',
    '(': 'c',
    '*': '',
})

def normalize_text(text):
    return text.lower().translate(LEET_MAP)

def remove_spaces(text):
    return text.lower().replace(' ', '')

async def ai_bypass_check(message_content, banned_words):
    if not banned_words:
        return False
    word_list = ", ".join(banned_words)
    prompt = (
        "You are a content moderation assistant. "
        "Your job is to detect if a message contains any of the listed banned words, "
        "even if they are disguised using special characters, numbers, symbols, or misspellings. "
        "Reply with only 'yes' or 'no'.\n\n"
        f"Banned words: {word_list}.\n"
        f"Message: '{message_content}'\n"
        "Does this message contain any banned word in disguised form?"
    )
    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt
        )
        answer = response.text.strip().lower()
        return answer.startswith("yes")
    except Exception as e:
        print(f"[AI check error] {e}")
        return False

def create_logs_table():
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            guild_id INTEGER,
            infraction_type TEXT, -- 'Warning' or 'Timeout'
            message_content TEXT,
            timestamp DATETIME
        )
    """)
    conn.commit()
    conn.close()

# save data
def log_infraction(user_id, username, guild_id, infraction_type, content):
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO mod_logs (user_id, username, guild_id, infraction_type, message_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, guild_id, infraction_type, content, datetime.datetime.now()))
    conn.commit()
    conn.close()

#create a database to store the number of warnings per user
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
#create a database to store the naughty words
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

create_logs_table()
naughty_words_table()
create_user_table()

#increase the number of warnings per user
def increase_and_get_warning_count(user_id, guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT warning_count 
        FROM users_per_guild 
        WHERE user_id = ? AND guild_id = ?
    """,
        (user_id, guild_id),
    )
    result = cursor.fetchone()

    if result is None:
        cursor.execute(
            """
            INSERT INTO users_per_guild (user_id, warning_count, guild_id)
            VALUES (?, 1, ?)
        """,
            (user_id, guild_id),
        )
        conn.commit()
        conn.close()
        return 1

    cursor.execute(
        """
        UPDATE users_per_guild
        SET warning_count = ?
        WHERE user_id = ? AND guild_id = ?
    """,
        (result[0] + 1, user_id, guild_id),
    )

    conn.commit()
    conn.close()
    return result[0] + 1

def get_naughty_words(guild_id):
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT word
        FROM naughty_words
        WHERE guild_id = ?
    """,
        (guild_id,),
    )
    result = cursor.fetchall()
    conn.close()
    return [word[0] for word in result]


bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}, bot is online")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. Use `!commands` to see how to use it.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        raise error
    
#send a message when the bot joins a server
@bot.event
async def on_guild_join(guild):
    intro_message = ("Hello! I am a moderation bot built by Allancash123.\n"
                     "My main purpose is to assist with moderation tasks.\n"
                     "I automatically filter banned words and warn users.\n\n"
                     "Use `!about` to learn about me.\n"
                     "Use `!commands` to see my command list.")

    target_channel = None

    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        target_channel = guild.system_channel
    else:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                target_channel = channel
                break

    if target_channel:
        await target_channel.send(intro_message)

#check if the message contains a naughty word
@bot.event
async def on_message(message):
    

    if message.author.id == bot.user.id:
        return

    if not message.author.guild_permissions.moderate_members:
        naughty_words = get_naughty_words(message.guild.id)
        detected = False

        normalized_content = normalize_text(message.content)
        no_spaces_content = remove_spaces(message.content)
        no_spaces_normalized = remove_spaces(normalized_content)

        for word in naughty_words:
            w = word.lower()
            if (w in message.content.lower() or
                w in normalized_content or
                w in no_spaces_content or
                w in no_spaces_normalized):
                detected = True
                break

        if not detected:
            detected = await ai_bypass_check(message.content, naughty_words)

        if detected:
            num_warnings = increase_and_get_warning_count(
                message.author.id, message.guild.id
            )
            clean_content = (message.content[:100] + '..') if len(message.content) > 100 else message.content

            if num_warnings >= 3:
                log_infraction(message.author.id, str(message.author), message.guild.id, "Timeout (2hr)", clean_content)
                await message.author.timeout(
                    datetime.timedelta(minutes=120),
                    reason="Exceeded naughty word limit (3+ warnings)",
                )
                await message.channel.send(
                    f"{message.author.mention} has been timed out for 2 hours for saying too many naughty words."
                )
                await message.delete()

            elif num_warnings == 1:
                log_infraction(message.author.id, str(message.author), message.guild.id, "Warning #1", clean_content)
                try:
                    await message.author.send(
                        "Please do not say naughty words. You have been warned. One more time and you'll be timed out for an hour."
                    )
                except discord.Forbidden:
                    pass
                await message.channel.send(
                    f"{message.author.mention} Please do not say naughty words."
                )
                await message.delete()

            elif num_warnings == 2:
                log_infraction(message.author.id, str(message.author), message.guild.id, "Timeout (1hr)", clean_content)
                await message.author.timeout(
                    datetime.timedelta(minutes=60),
                    reason="Second naughty word warning",
                )
                try:
                    await message.author.send(
                        "You have been timed out for an hour for saying too many naughty words. One more time and you'll be timed out for 2 hours."
                    )
                except discord.Forbidden:
                    pass
                await message.channel.send(
                    f"{message.author.mention} has been timed out for an hour for saying too many naughty words."
                )
                await message.delete()

    await bot.process_commands(message)

#clear the number of warnings per user
@bot.command()
@commands.has_permissions(moderate_members=True)
async def clearwarnings(ctx, member: discord.Member):
    conn = sqlite3.connect(os.path.join(Base_dir, "users_warning.db"))
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM users_per_guild
        WHERE user_id = ? AND guild_id = ?
    """,
        (member.id, ctx.guild.id),
    )
    conn.commit()
    conn.close()
    await ctx.send(f"Warnings for {member.mention} have been cleared.")


@clearwarnings.error
async def clearwarnings_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found.")
# add a naughty word to the database
@bot.command()
@commands.has_permissions(moderate_members=True)
async def addword(ctx, word: str):
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO naughty_words (word, guild_id) VALUES (?, ?)",
            (word.lower(), ctx.guild.id)
        )
        conn.commit()
        await ctx.send(f"Added '{word}' to the banned words list for this server.")
    except sqlite3.IntegrityError:
        await ctx.send(f"'{word}' is already in the banned words list.")
    finally:
        conn.close()

#remove a naughty word from the database
@bot.command()
@commands.has_permissions(moderate_members=True)
async def removeword(ctx, word: str):
    conn = sqlite3.connect(os.path.join(Base_dir, "naughty_words.db"))
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM naughty_words WHERE word = ? AND guild_id = ?",
        (word.lower(), ctx.guild.id)
    )
    conn.commit()
    removed = cursor.rowcount
    conn.close()
    if removed:
        await ctx.send(f"Removed '{word}' from the banned words list for this server.")
    else:
         await ctx.send(f"'{word}' is not in the banned words list.")
         
# Print logs of what happened        
@bot.command()
@commands.has_permissions(moderate_members=True)
async def logs(ctx, member: discord.Member):
    conn = sqlite3.connect(os.path.join(Base_dir, "mod_logs.db"))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT infraction_type, message_content, timestamp 
        FROM mod_logs 
        WHERE user_id = ? AND guild_id = ?
        ORDER BY timestamp DESC LIMIT 10
    """, (member.id, ctx.guild.id))
    
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await ctx.send(f"No logs found for {member.display_name}.")
        return

    log_text = f"**Recent logs for {member.mention}:**\n"
    for type, content, time in rows:
        # Formatting the timestamp string for readability
        clean_time = time[:19] 
        log_text += f"• `[{clean_time}]` **{type}**: \"{content}\"\n"

    await ctx.send(log_text)     
         
# List all naughty words for the current server
@bot.command()
@commands.has_permissions(moderate_members=True)
async def listwords(ctx):
    words = get_naughty_words(ctx.guild.id)
    
    if not words:
        await ctx.send("There are currently no banned words in this server.")
        return

    # Joining the words into a readable string
    word_list_string = ", ".join(f"`{word}`" for word in words)
    
    embed = discord.Embed(
        title="Banned Words List",
        description=word_list_string,
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)
         
#command list
@bot.command(name="commands")
async def list_commands(ctx):
    commands_list = ("**Bot Command List**\n\n"
                    "`!about` - Shows information about the bot\n"
                    "`!commands` - Shows this command list\n"
                    "`!addword <word>` - Add a banned word (Moderators only)\n"
                    "`!removeword <word>` - Remove a banned word (Moderators only)\n"
                    "`!listwords` - See all banned words (Moderators only)\n"
                    "`!clearwarnings <member>` - Clear a user's warnings (Moderators only)\n")
    await ctx.send(commands_list)
    
#one-time announcement to all servers (owner only)
@bot.command()
@commands.is_owner()
async def announce(ctx):
    update_message = ("**Mod Bot Update**\n\n"
                      "Here's what's new:\n"
                      "- Moderators are now immune to the banned word filter.\n"
                      "- Improved error messages when a command is misused or used without permission.\n"
                      "- The intro message is no longer sent to every channel when I join a server.\n\n"
                      "Use `!commands` to see the full command list.")

    sent = 0
    for guild in bot.guilds:
        target_channel = None
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            target_channel = guild.system_channel
        else:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break

        if target_channel:
            try:
                await target_channel.send(update_message)
                sent += 1
            except discord.Forbidden:
                pass

    await ctx.send(f"Update message sent to {sent} server(s).")

#about section
@bot.command()
async def about(ctx):
    about_message = ("**Moderation Bot**\n"
                    "This bot helps moderate the server by filtering banned words "
                    "and issuing warnings to users who use them.\n\n"
                    "**How it works:**\n"
                    "- If a user says a banned word, they get a warning.\n"
                    "- 2nd warning = 1 hour timeout.\n"
                    "- 3rd warning = 2 hour timeout.\n\n"
                    "Use `!commands` to see the full list of commands."
                    )
    await ctx.send(about_message)




TOKEN = os.getenv("DISCORD_TOKEN_TEST")

bot.run(TOKEN)

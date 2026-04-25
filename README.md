# Discord Moderation Bot

A Python moderation bot built with **discord.py** and **SQLite** for automated word filtering, warnings, timeouts, and moderation logging.

## Features

### Automatic Moderation
- Detects banned words in messages
- Per-server (guild-specific) banned word lists
- Warning system:
  - 1st offense → Warning
  - 2nd offense → 1 hour timeout
  - 3rd+ offense → 2 hour timeout
- Automatically deletes offending messages
- Moderators are exempt from filtering

### Logging System
Stores moderation actions in SQLite:
- Warnings
- Timeouts
- Offending message content
- Timestamps
- User moderation history

### Admin Commands
Moderators can:

| Command | Description |
|--------|-------------|
| `!addword <word>` | Add banned word |
| `!removeword <word>` | Remove banned word |
| `!listwords` | Show banned words |
| `!clearwarnings <member>` | Reset a user's warnings |
| `!logs <member>` | View recent infractions |
| `!commands` | Show bot commands |
| `!about` | About the bot |

---

# Tech Stack

- Python 3
- discord.py
- SQLite3
- python-dotenv

---

# Installation

## Clone Repository

```bash
git clone https://github.com/yourusername/discord-moderation-bot.git
cd discord-moderation-bot
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install discord.py python-dotenv
```

---

# Setup

Create a `.env` file:

```env
DISCORD_TOKEN=your_bot_token_here
```

---

# Run Bot

```bash
python bot.py
```

---

# Required Bot Permissions

Give the bot:

- Read Messages/View Channels
- Send Messages
- Manage Messages
- Moderate Members (required for timeouts)
- Send Direct Messages (optional)

Also make sure the bot’s role is **above members it needs to timeout**.

---

# Database Files

The bot creates these automatically:

| File | Purpose |
|------|---------|
| `users_warning.db` | User warning counts |
| `naughty_words.db` | Banned words per server |
| `mod_logs.db` | Moderation logs |

No manual setup needed.

---

# How It Works

## Warning Flow

```text
User says banned word
↓
Warning #1 issued
↓
Second offense
↓
1 hour timeout
↓
Third+ offense
↓
2 hour timeout
```

Warnings are stored per user **per server**.

---

# Example Usage

Add banned word:

```bash
!addword spamword
```

Remove banned word:

```bash
!removeword spamword
```

View logs:

```bash
!logs @user
```

Clear warnings:

```bash
!clearwarnings @user
```

---

# Project Structure

```text
discord-moderation-bot/
│
├── bot.py
├── .env
├── users_warning.db
├── naughty_words.db
├── mod_logs.db
└── README.md
```

---

# Future Improvements
Planned features:

- Slash commands
- Auto-expiring warnings
- Kick/ban escalation
- Web dashboard
- Regex-based word filtering
- Better anti-bypass filtering
- Logging channel support

---

# Notes

Current word filtering uses simple substring matching:

```python
if word.lower() in message.content.lower()
```

This may trigger false positives (`ass` inside `class`).

Future improvement:
- Whole-word regex matching
- Leetspeak detection
- Anti-obfuscation filtering

---

# Invite Bot
Generate invite link with scopes:

- `bot`
- `applications.commands`

Permissions integer should include moderation permissions.



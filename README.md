# Local AI Personal Assistant

A private personal assistant that runs on my own laptop with no paid APIs and no recurring cost. I message it from Telegram, and a local LLM running through Ollama decides which tool to call.

## Features
- Chat with persistent history (SQLite)
- Long-term memory: `/remember`, `/facts`, `/forget`
- Natural-language reminders ("tomorrow at 5pm") delivered to Telegram by a background scheduler
- Notes, plus a sandboxed file workspace
- Open allowlisted apps on the laptop (Notepad, Calculator, Paint)
- Auto-start at login with restart-on-crash
- Read-only Gmail: lists unread senders and subjects (`/mail`); no sending, deleting or reading message bodies

## Architecture
Telegram -> bot.py (Python) -> Ollama (Qwen3-4B, local) -> tools -> SQLite / workspace folder / Gmail (read-only)

The model can only call a fixed list of Python functions. It has no shell access.

## Security design
- Telegram user-ID allowlist: messages from anyone else are ignored and logged
- File tools are limited to one folder; path traversal (`..\..`), absolute paths and unsafe file types are blocked (tested in `test_guard.py`)
- File writes need my approval: the bot sends the diff to Telegram with Approve/Deny buttons, enforced in code, not in the prompt
- Backups of overwritten files; no delete tool
- Gmail uses the `gmail.readonly` scope only; the email list is returned directly instead of being passed back through the model
- Secrets (`.env`, Google credentials and tokens) are git-ignored

## Results
- Runs on an RTX 4050 (6 GB VRAM) at about 60 tokens/s (Qwen3-4B); the 8B model was tested and rejected because it spilled to CPU (about 10 tokens/s)
- Tool selection: 10/10 on a simple test set and 10/10 on a harder set, stable across repeated runs. These are small hand-written sets, not a benchmark.

## Limitations
- Only runs while the laptop is on and online
- A 4B model sometimes ignores the tool instructions, picks the wrong arguments, or summarizes poorly (it listed 1 of 5 emails until the count was fixed in code)
- Not tested against adversarial prompt injection at scale

## Setup
1. Install Ollama and run `ollama pull qwen3:4b`
2. Python 3.13: `python -m venv .venv`, activate it, then `pip install ollama python-telegram-bot python-dotenv dateparser google-api-python-client google-auth-oauthlib google-auth-httplib2`
3. Create a bot with @BotFather and put its token in a `.env` file:
   `TELEGRAM_BOT_TOKEN=...` and `ALLOWED_USER_ID=...`
4. Optional Gmail: create a Google Cloud OAuth desktop client, save it as `credentials.json`, then run `python gmail_auth.py` once
5. Run `python bot.py`

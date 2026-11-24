import asyncio
import difflib
import logging
import os
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import dateparser
import ollama
from dotenv import load_dotenv
from gmail_tool import check_unread_email
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_ID = int(os.environ["ALLOWED_USER_ID"])

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

MODEL = "qwen3:4b"
BASE_SYSTEM = "You are a helpful personal assistant. Be concise. Answer the user directly. Use a tool only when the request needs one. To create or change a file you MUST call the write_file tool. Never ask the user for approval yourself: the system asks them automatically after you call the tool."
DB_FILE = "assistant.db"

WORKSPACE = (Path(__file__).parent / "workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)
ALLOWED_EXT = {".txt", ".md", ".csv", ".json"}
APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe"}

PENDING = {}      # id -> {"path": Path, "content": str}
NEW_PENDING = []  # ids waiting to be shown to the user as buttons


def q(sql, params=(), commit=False):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        rows = conn.execute(sql, params).fetchall()
        if commit:
            conn.commit()
        return rows
    finally:
        conn.close()


q("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)", commit=True)
q("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)", commit=True)
q("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due_at TEXT NOT NULL, fired INTEGER DEFAULT 0)", commit=True)
q("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)", commit=True)


def safe_path(name):
    p = (WORKSPACE / name).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise PermissionError("path is outside the workspace folder")
    if ".versions" in p.relative_to(WORKSPACE).parts:
        raise PermissionError("that folder is not accessible")
    if p.suffix.lower() not in ALLOWED_EXT:
        raise PermissionError("only .txt, .md, .csv and .json files are allowed")
    return p


# ---------- tools (the model can call ONLY these) ----------
def add_reminder(text: str, when: str) -> str:
    """Set a reminder.

    Args:
        text: What to remind the user about.
        when: When to remind, exactly as the user said it (e.g. 'in 30 minutes', 'tomorrow at 5pm').
    """
    dt = dateparser.parse(
        when,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": "Asia/Kolkata",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if dt is None:
        return f"ERROR: could not understand the time '{when}'. Ask the user to rephrase."
    if dt <= datetime.now():
        return f"ERROR: that time ({dt:%Y-%m-%d %H:%M}) is in the past. Ask the user for a future time."
    q("INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, dt.isoformat()), True)
    return f"Reminder set for {dt:%A %d %b %Y, %I:%M %p}."


def add_note(text: str) -> str:
    """Save a note.

    Args:
        text: The note content.
    """
    q("INSERT INTO notes (text) VALUES (?)", (text,), True)
    return "Note saved."


def list_reminders() -> str:
    """List the user's pending reminders."""
    rows = q("SELECT id, text, due_at FROM reminders WHERE fired = 0 ORDER BY due_at")
    if not rows:
        return "No pending reminders."
    return "\n".join(
        f"{rid}: {text} at {datetime.fromisoformat(due):%a %d %b, %I:%M %p}"
        for rid, text, due in rows
    )


def get_time() -> str:
    """Get the current date and time."""
    return datetime.now().strftime("%A %d %B %Y, %I:%M %p")


def open_app(name: str) -> str:
    """Open an application on the user's laptop. Allowed apps: notepad, calculator, paint.

    Args:
        name: The app name.
    """
    key = name.strip().lower()
    if key not in APPS:
        return f"ERROR: '{name}' is not allowed. Allowed apps: {', '.join(APPS)}."
    subprocess.Popen([APPS[key]])
    return f"Opened {key}."


def list_files() -> str:
    """List the files in the user's workspace folder."""
    files = [
        str(p.relative_to(WORKSPACE))
        for p in sorted(WORKSPACE.rglob("*"))
        if p.is_file() and ".versions" not in p.parts
    ]
    return "\n".join(files) if files else "The workspace is empty."


def read_file(filename: str) -> str:
    """Read a text file from the workspace folder.

    Args:
        filename: File name inside the workspace, e.g. 'todo.txt'.
    """
    p = safe_path(filename)
    if not p.exists():
        return "ERROR: file not found."
    return p.read_text(encoding="utf-8", errors="replace")[:4000]


def write_file(filename: str, content: str) -> str:
    """Create or overwrite a text file in the workspace folder. The user must approve it before anything is written.

    Args:
        filename: File name inside the workspace, e.g. 'todo.txt'.
        content: The complete new content of the file.
    """
    p = safe_path(filename)
    if len(content) > 20000:
        return "ERROR: content too large."
    pid = uuid.uuid4().hex[:8]
    PENDING[pid] = {"path": p, "content": content}
    NEW_PENDING.append(pid)
    return "Not written yet. Waiting for the user to approve on their phone."


TOOLS = {
    f.__name__: f
    for f in (
        add_reminder,
        add_note,
        list_reminders,
        get_time,
        open_app,
        list_files,
        read_file,
        write_file,
        check_unread_email,
    )
}


# ---------- helpers ----------
def clean(text):
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def save(role, content):
    q("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content), True)


def load_history(limit=20):
    rows = q("SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,))
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]


def build_system():
    rows = q("SELECT fact FROM facts ORDER BY id")
    system = BASE_SYSTEM
    if rows:
        system += "\n\nKnown facts about the user (treat as true):\n" + "\n".join(
            f"- {r[0]}" for r in rows
        )
    return system + "\n/no_think"


def handle_command(user):
    if user == "/start":
        return "Assistant ready. Commands: /remember /facts /forget /reminders /clear"
    if user.startswith("/remember "):
        fact = user[len("/remember "):].strip()
        if fact:
            q("INSERT INTO facts (fact) VALUES (?)", (fact,), True)
            return "Saved."
        return "Use: /remember <text>"
    if user == "/facts":
        rows = q("SELECT id, fact FROM facts ORDER BY id")
        return "\n".join(f"{i}: {f}" for i, f in rows) if rows else "No facts saved."
    if user.startswith("/forget "):
        arg = user[len("/forget "):].strip()
        if arg.isdigit():
            q("DELETE FROM facts WHERE id = ?", (int(arg),), True)
            return "Deleted."
        return "Use: /forget <number>"
    if user == "/reminders":
        return list_reminders()
    if user == "/clear":
        q("DELETE FROM messages", commit=True)
        return "Chat history cleared. Facts, notes and reminders kept."
    if user == "/mail":
        return check_unread_email()
    return None


def chat_turn(user):
    save("user", user)
    messages = [{"role": "system", "content": build_system()}] + load_history()
    for _ in range(4):
        response = ollama.chat(model=MODEL, messages=messages, tools=list(TOOLS.values()))
        msg = response.message
        if not msg.tool_calls:
            return clean(msg.content or "")
        messages.append(msg)
        for call in msg.tool_calls:
            name = call.function.name
            args = dict(call.function.arguments)
            if name in TOOLS:
                try:
                    result = TOOLS[name](**args)
                except Exception as e:
                    result = f"ERROR: {e}"
            else:
                result = f"ERROR: unknown tool '{name}'"
            shown = {k: (v[:60] if isinstance(v, str) else v) for k, v in args.items()}
            print(f"  [tool: {name} {shown} -> {str(result)[:100]}]")
            if name == "check_unread_email" and not str(result).startswith("ERROR"):
                return str(result)
            messages.append({"role": "tool", "tool_name": name, "content": str(result)})
    return "Sorry, I got stuck. Please try again."


def make_diff(item):
    p, new = item["path"], item["content"]
    old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), "current", "proposed", lineterm="", n=1
        )
    )
    return (diff or "(no changes)")[:2500]


def do_write(item):
    p = item["path"]
    if p.exists():
        vdir = WORKSPACE / ".versions"
        vdir.mkdir(exist_ok=True)
        (vdir / f"{p.name}.{int(time.time())}.bak").write_bytes(p.read_bytes())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(item["content"], encoding="utf-8")


# ---------- Telegram ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = (update.message.text or "").strip()
    if not user:
        return
    cmd = handle_command(user)
    if cmd is not None:
        await update.message.reply_text(cmd)
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    reply = await asyncio.to_thread(chat_turn, user)
    save("assistant", reply)
    await update.message.reply_text(reply[:4000] or "(no reply)")
    ids = NEW_PENDING[:]
    NEW_PENDING.clear()
    for pid in ids:
        item = PENDING.get(pid)
        if not item:
            continue
        rel = item["path"].relative_to(WORKSPACE)
        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Approve", callback_data=f"ok:{pid}"),
                InlineKeyboardButton("Deny", callback_data=f"no:{pid}"),
            ]]
        )
        await update.message.reply_text(
            f"Write to {rel}?\n\n{make_diff(item)}", reply_markup=kb
        )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ALLOWED_ID:
        return
    await query.answer()
    action, pid = query.data.split(":", 1)
    item = PENDING.pop(pid, None)
    if item is None:
        await query.edit_message_text("Expired or already handled.")
        return
    if action == "ok":
        try:
            do_write(item)
            await query.edit_message_text(f"Written: {item['path'].name}")
        except Exception as e:
            await query.edit_message_text(f"Failed: {e}")
    else:
        await query.edit_message_text("Cancelled. Nothing was written.")


async def on_stranger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else "unknown"
    print(f"Ignored a message from unauthorized user ID {uid}")


async def reminder_loop(app):
    while True:
        try:
            now = datetime.now().isoformat()
            rows = await asyncio.to_thread(
                q, "SELECT id, text FROM reminders WHERE fired = 0 AND due_at <= ?", (now,)
            )
            for rid, text in rows:
                await app.bot.send_message(chat_id=ALLOWED_ID, text=f"Reminder: {text}")
                await asyncio.to_thread(q, "UPDATE reminders SET fired = 1 WHERE id = ?", (rid,), True)
        except Exception as e:
            print(f"[reminder error: {type(e).__name__}]")
        await asyncio.sleep(15)


async def post_init(app):
    app.bot_data["reminder_task"] = asyncio.create_task(reminder_loop(app))


app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
only_me = filters.User(user_id=ALLOWED_ID)
app.add_handler(MessageHandler(filters.TEXT & only_me, on_message))
app.add_handler(CallbackQueryHandler(on_button))
app.add_handler(MessageHandler(filters.ALL & ~only_me, on_stranger))
print("Bot running. Press Ctrl+C to stop.")
app.run_polling()
import re
import sqlite3
import threading
import time
from datetime import datetime

import dateparser
import ollama

MODEL = "qwen3:4b"
BASE_SYSTEM = "You are a helpful personal assistant. Be concise. Answer the user directly. Use a tool only when the request needs one."

DB_FILE = "assistant.db"
db = sqlite3.connect(DB_FILE, timeout=10)
db.execute(
    "CREATE TABLE IF NOT EXISTS messages ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, "
    "content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS facts ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, "
    "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS reminders ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
    "due_at TEXT NOT NULL, fired INTEGER DEFAULT 0)"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS notes ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
    "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
)
db.commit()


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
    db.execute("INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, dt.isoformat()))
    db.commit()
    return f"Reminder set for {dt:%A %d %b %Y, %I:%M %p}."


def add_note(text: str) -> str:
    """Save a note.

    Args:
        text: The note content.
    """
    db.execute("INSERT INTO notes (text) VALUES (?)", (text,))
    db.commit()
    return "Note saved."


def list_reminders() -> str:
    """List the user's pending reminders."""
    rows = db.execute(
        "SELECT id, text, due_at FROM reminders WHERE fired = 0 ORDER BY due_at"
    ).fetchall()
    if not rows:
        return "No pending reminders."
    return "\n".join(
        f"{rid}: {text} at {datetime.fromisoformat(due):%a %d %b, %I:%M %p}"
        for rid, text, due in rows
    )


def get_time() -> str:
    """Get the current date and time."""
    return datetime.now().strftime("%A %d %B %Y, %I:%M %p")


TOOLS = {f.__name__: f for f in (add_reminder, add_note, list_reminders, get_time)}


# ---------- background reminder checker ----------
def reminder_loop():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    while True:
        try:
            now = datetime.now().isoformat()
            rows = conn.execute(
                "SELECT id, text FROM reminders WHERE fired = 0 AND due_at <= ?", (now,)
            ).fetchall()
            for rid, text in rows:
                print(f"\n*** REMINDER: {text} ***\nYou: ", end="", flush=True)
                conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (rid,))
                conn.commit()
        except Exception as e:
            print(f"\n[reminder checker error: {e}]")
        time.sleep(15)


# ---------- helpers ----------
def clean(text):
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def save(role, content):
    db.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    db.commit()


def load_history(limit=20):
    rows = db.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]


def build_system():
    rows = db.execute("SELECT fact FROM facts ORDER BY id").fetchall()
    system = BASE_SYSTEM
    if rows:
        system += "\n\nKnown facts about the user (treat as true):\n" + "\n".join(
            f"- {r[0]}" for r in rows
        )
    return system + "\n/no_think"


def handle_command(user):
    if user.startswith("/remember "):
        fact = user[len("/remember "):].strip()
        if fact:
            db.execute("INSERT INTO facts (fact) VALUES (?)", (fact,))
            db.commit()
            print("Saved.")
        return True
    if user == "/facts":
        rows = db.execute("SELECT id, fact FROM facts ORDER BY id").fetchall()
        print("\n".join(f"{i}: {f}" for i, f in rows) if rows else "No facts saved.")
        return True
    if user.startswith("/forget "):
        arg = user[len("/forget "):].strip()
        if arg.isdigit():
            db.execute("DELETE FROM facts WHERE id = ?", (int(arg),))
            db.commit()
            print("Deleted.")
        else:
            print("Use: /forget <number>")
        return True
    if user == "/reminders":
        print(list_reminders())
        return True
    if user == "/clear":
        db.execute("DELETE FROM messages")
        db.commit()
        print("Chat history cleared. Facts, notes and reminders kept.")
        return True
    return False


def chat_turn(user):
    save("user", user)
    messages = [{"role": "system", "content": build_system()}] + load_history()
    for _ in range(4):  # max 4 tool rounds, then stop
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
            print(f"  [tool: {name} {args} -> {result}]")
            messages.append({"role": "tool", "tool_name": name, "content": str(result)})
    return "Sorry, I got stuck. Please try again."


threading.Thread(target=reminder_loop, daemon=True).start()

print("Assistant ready. Commands: /remember /facts /forget /reminders /clear. Type 'exit' to quit.")
while True:
    user = input("You: ").strip()
    if user.lower() in ("exit", "quit"):
        break
    if not user:
        continue
    if handle_command(user):
        continue
    reply = chat_turn(user)
    save("assistant", reply)
    print("Assistant:", reply)
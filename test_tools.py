import ollama

MODEL = "qwen3:4b"


def add_reminder(text: str, minutes_from_now: int) -> str:
    """Set a reminder.

    Args:
        text: What to remind the user about.
        minutes_from_now: How many minutes from now the reminder should fire.
    """
    return "ok"


def add_note(text: str) -> str:
    """Save a note.

    Args:
        text: The note content.
    """
    return "ok"


def get_time() -> str:
    """Get the current date and time."""
    return "ok"


TESTS = [
    ("Remind me to call mom in 30 minutes", "add_reminder(text~call mom, minutes=30)"),
    ("Set a reminder to drink water in 2 hours", "add_reminder(text~drink water, minutes=120)"),
    ("Write down that I need to buy milk", "add_note(text~buy milk)"),
    ("What time is it?", "get_time()"),
    ("Remind me in 10 minutes to check the oven", "add_reminder(text~check oven, minutes=10)"),
    ("Save a note: project idea, build a habit tracker", "add_note(text~habit tracker)"),
    ("What is the capital of France?", "NO TOOL"),
    ("Hi, how are you?", "NO TOOL"),
    ("Remind me to stretch in an hour", "add_reminder(text~stretch, minutes=60)"),
    ("What's today's date?", "get_time()"),
]

SYSTEM = "You are a personal assistant. Use a tool only when the request needs one. /no_think"

for i, (prompt, expected) in enumerate(TESTS, 1):
    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        tools=[add_reminder, add_note, get_time],
    )
    calls = response.message.tool_calls or []
    if calls:
        got = "; ".join(f"{c.function.name}({dict(c.function.arguments)})" for c in calls)
    else:
        got = "NO TOOL"
    print(f"{i}. {prompt}")
    print(f"   expected: {expected}")
    print(f"   got:      {got}")
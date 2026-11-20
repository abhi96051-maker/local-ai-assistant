import ast
from pathlib import Path

src = Path("bot.py").read_text(encoding="utf-8")
func = next(
    n for n in ast.parse(src).body
    if isinstance(n, ast.FunctionDef) and n.name == "safe_path"
)
ns = {
    "Path": Path,
    "WORKSPACE": (Path("bot.py").parent / "workspace").resolve(),
    "ALLOWED_EXT": {".txt", ".md", ".csv", ".json"},
}
exec(compile(ast.Module(body=[func], type_ignores=[]), "bot.py", "exec"), ns)

tests = [
    "todo.txt",
    "sub\\a.txt",
    "..\\..\\evil.txt",
    "C:\\Users\\itsme\\evil.txt",
    "C:\\Windows\\win.ini",
    ".versions\\x.txt",
    "evil.exe",
]
for name in tests:
    try:
        print(f"{name:28} -> ALLOWED {ns['safe_path'](name)}")
    except Exception as e:
        print(f"{name:28} -> BLOCKED ({e})")
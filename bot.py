# Vibezzzzz — one-folder bot (assembles local bp0..bp9)
import base64, importlib, sys
from pathlib import Path
_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)
chunks = []
for i in range(10):
    m = importlib.import_module(f"bp{i}")
    chunks.append(base64.b64decode("".join(m._CODE)).decode())
exec(compile("".join(chunks), "bot.py", "exec"), globals())

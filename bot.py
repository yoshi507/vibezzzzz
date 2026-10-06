# Vibezzzzz bot entry
import base64, importlib
chunks = []
for i in range(8):
    m = importlib.import_module(f"bot_part_{i}")
    chunks.append(base64.b64decode("".join(m._CODE)).decode())
exec(compile("".join(chunks), "bot.py", "exec"), globals())

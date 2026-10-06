# Vibezzzzz web entry
import base64, importlib
chunks = []
for i in range(4):
    m = importlib.import_module(f"web_part_{i}")
    chunks.append(base64.b64decode("".join(m._CODE)).decode())
exec(compile("".join(chunks), "web.py", "exec"), globals())

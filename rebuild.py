#!/usr/bin/env python3
"""Rebuild source files from _src/*.b64 chunks. Run once after clone: python rebuild.py"""
import base64
from pathlib import Path

CHUNK_MAP = {'logging_setup.py': ['_src/logging_setup.py.0.b64', '_src/logging_setup.py.1.b64'], 'cats.py': ['_src/cats.py.0.b64', '_src/cats.py.1.b64', '_src/cats.py.2.b64', '_src/cats.py.3.b64'], 'music.py': ['_src/music.py.0.b64', '_src/music.py.1.b64', '_src/music.py.2.b64', '_src/music.py.3.b64'], 'server_logs.py': ['_src/server_logs.py.0.b64', '_src/server_logs.py.1.b64', '_src/server_logs.py.2.b64', '_src/server_logs.py.3.b64'], 'web.py': ['_src/web.py.0.b64', '_src/web.py.1.b64', '_src/web.py.2.b64', '_src/web.py.3.b64', '_src/web.py.4.b64'], 'security.py': ['_src/security.py.0.b64', '_src/security.py.1.b64', '_src/security.py.2.b64', '_src/security.py.3.b64', '_src/security.py.4.b64', '_src/security.py.5.b64'], 'moderation.py': ['_src/moderation.py.0.b64', '_src/moderation.py.1.b64', '_src/moderation.py.2.b64', '_src/moderation.py.3.b64', '_src/moderation.py.4.b64', '_src/moderation.py.5.b64', '_src/moderation.py.6.b64', '_src/moderation.py.7.b64', '_src/moderation.py.8.b64', '_src/moderation.py.9.b64', '_src/moderation.py.10.b64'], 'extras.py': ['_src/extras.py.0.b64', '_src/extras.py.1.b64', '_src/extras.py.2.b64', '_src/extras.py.3.b64', '_src/extras.py.4.b64', '_src/extras.py.5.b64', '_src/extras.py.6.b64', '_src/extras.py.7.b64', '_src/extras.py.8.b64', '_src/extras.py.9.b64', '_src/extras.py.10.b64', '_src/extras.py.11.b64'], 'bot.py': ['_src/bot.py.0.b64', '_src/bot.py.1.b64', '_src/bot.py.2.b64', '_src/bot.py.3.b64', '_src/bot.py.4.b64', '_src/bot.py.5.b64', '_src/bot.py.6.b64', '_src/bot.py.7.b64', '_src/bot.py.8.b64', '_src/bot.py.9.b64', '_src/bot.py.10.b64']}

def main():
    root = Path(__file__).resolve().parent
    for name, parts in CHUNK_MAP.items():
        data = b"".join(base64.b64decode((root / p).read_text().strip()) for p in parts)
        (root / name).write_bytes(data)
        print("wrote", name, len(data), "bytes")
    print("Done.")

if __name__ == "__main__":
    main()

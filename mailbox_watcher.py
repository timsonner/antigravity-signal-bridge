#!/usr/bin/env python3
import os
import sys
import time
import json
import re
from pathlib import Path

INBOUND_DIR = Path("/root/antigravity_mailbox/inbound")

def load_env():
    env_path = Path("/root/antigravity_mailbox/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

def main():
    load_env()
    streamer_mode = os.getenv("STREAMER_MODE", "false").lower() == "true"
    
    print(f"Mailbox watcher started (Streamer Mode: {streamer_mode}). Monitoring /root/antigravity_mailbox/inbound for new messages...", flush=True)
    
    def redact(text):
        if not streamer_mode or not isinstance(text, str):
            return text
        return re.sub(r'\+\d{10,15}', '+[REDACTED]', text)
    
    # Register existing files on startup so we only alert on NEW files
    seen_files = set()
    if INBOUND_DIR.exists():
        seen_files = {f.name for f in INBOUND_DIR.glob("*.json")}
    
    while True:
        try:
            if not INBOUND_DIR.exists():
                time.sleep(5)
                continue
                
            current_files = {f.name for f in INBOUND_DIR.glob("*.json")}
            new_files = current_files - seen_files
            
            for fname in sorted(new_files):
                fpath = INBOUND_DIR / fname
                try:
                    content = fpath.read_text()
                    # Parse to verify JSON structure, then pretty print
                    data = json.loads(content)
                    
                    if streamer_mode:
                        if "sender" in data:
                            data["sender"] = redact(data["sender"])
                        if "text" in data:
                            data["text"] = redact(data["text"])
                    
                    print(f"\n[NEW_MESSAGE_DETECTED] {redact(fname)}\n{json.dumps(data, indent=2)}", flush=True)
                except Exception as e:
                    print(f"\n[NEW_MESSAGE_DETECTED] {redact(fname)} (Error reading file: {e})", flush=True)
            
            # Keep seen_files synchronized (handles additions and removals cleanly)
            seen_files = current_files
            
        except Exception as e:
            print(f"Watcher error: {e}", flush=True)
            
        time.sleep(5)

if __name__ == "__main__":
    main()


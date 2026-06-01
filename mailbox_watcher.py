#!/usr/bin/env python3
import os
import sys
import time
import json
from pathlib import Path

INBOUND_DIR = Path("/root/antigravity_mailbox/inbound")

def main():
    print("Mailbox watcher started. Monitoring /root/antigravity_mailbox/inbound for new messages...", flush=True)
    
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
                    print(f"\n[NEW_MESSAGE_DETECTED] {fname}\n{json.dumps(data, indent=2)}", flush=True)
                except Exception as e:
                    print(f"\n[NEW_MESSAGE_DETECTED] {fname} (Error reading file: {e})", flush=True)
            
            # Keep seen_files synchronized (handles additions and removals cleanly)
            seen_files = current_files
            
        except Exception as e:
            print(f"Watcher error: {e}", flush=True)
            
        time.sleep(5)

if __name__ == "__main__":
    main()

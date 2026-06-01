#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
import httpx

import re

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path.home() / "antigravity_mailbox" / "bridge.log", mode="a"),
    ],
)
logger = logging.getLogger("signal_bridge")
# Silence httpx logs from showing up in standard streams
logging.getLogger("httpx").setLevel(logging.WARNING)

# Configurations from Environment
SIGNAL_HTTP_URL = os.getenv("SIGNAL_HTTP_URL", "http://127.0.0.1:8080").rstrip("/")
SIGNAL_ACCOUNT = os.getenv("SIGNAL_ACCOUNT")
MAILBOX_DIR = Path(os.getenv("MAILBOX_DIR", str(Path.home() / "antigravity_mailbox")))
STREAMER_MODE = os.getenv("STREAMER_MODE", "false").lower() == "true"

def redact_phone(val: str) -> str:
    if not STREAMER_MODE or not isinstance(val, str):
        return val
    return re.sub(r'\+\d{10,15}', '+[REDACTED]', val)

if not SIGNAL_ACCOUNT:

    logger.critical("SIGNAL_ACCOUNT environment variable is not configured! Please set it in your environment or .env file.")
    sys.exit(1)

INBOUND_DIR = MAILBOX_DIR / "inbound"
OUTBOUND_DIR = MAILBOX_DIR / "outbound"

# Ensure directories exist
INBOUND_DIR.mkdir(parents=True, exist_ok=True)
OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)

async def handle_inbound_envelope(envelope_data: dict):
    """Processes incoming dataMessage or syncMessage envelopes and writes them to the inbound mailbox."""
    data_message = envelope_data.get("dataMessage")
    
    # Check for Note to Self / syncMessage
    is_sync = False
    if "syncMessage" in envelope_data:
        sync_msg = envelope_data.get("syncMessage") or {}
        sent_msg = sync_msg.get("sentMessage")
        if sent_msg and isinstance(sent_msg, dict):
            data_message = sent_msg
            is_sync = True
            
    if not data_message:
        return

    sender = (
        envelope_data.get("sourceNumber")
        or envelope_data.get("sourceUuid")
        or envelope_data.get("source")
    )
    # If it is Note to Self, sender is the recipient / own account
    if is_sync and not sender:
        sender = SIGNAL_ACCOUNT

    message_text = data_message.get("message")
    timestamp = data_message.get("timestamp") or int(time.time() * 1000)

    if not sender or not message_text:
        return

    logger.info(f"New Inbound Message from {redact_phone(sender)} (Sync={is_sync}): {redact_phone(message_text)}")

    # Save to inbound directory as a JSON file
    filename = f"{timestamp}_{sender}.json".replace(":", "_")
    filepath = INBOUND_DIR / filename
    
    payload = {
        "sender": sender,
        "text": message_text,
        "timestamp": timestamp,
        "sourceName": envelope_data.get("sourceName", "Self" if is_sync else ""),
        "isSyncMessage": is_sync
    }

    try:
        filepath.write_text(json.dumps(payload, indent=2))
        logger.info(f"Saved message to mailbox: {redact_phone(filepath.name)}")
    except Exception as e:
        logger.error(f"Failed to save message {redact_phone(filename)}: {e}")

async def sse_listener_task():
    """Streams Server-Sent Events (SSE) from the signal-cli daemon."""
    url = f"{SIGNAL_HTTP_URL}/api/v1/events?account={SIGNAL_ACCOUNT}"
    logger.info(f"Starting SSE Listener connecting to: {redact_phone(url)}")
    
    async with httpx.AsyncClient(timeout=None) as client:
        while True:
            try:
                async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as response:
                    logger.info("Signal SSE: Connected successfully!")
                    
                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if not data_str:
                                    continue
                                try:
                                    event_data = json.loads(data_str)
                                    envelope = event_data.get("envelope", event_data)
                                    await handle_inbound_envelope(envelope)
                                except json.JSONDecodeError:
                                    pass
                                except Exception as e:
                                    logger.error(f"Error handling event: {e}")
            except httpx.HTTPError as e:
                logger.warning(f"Signal SSE Connection error: {e}. Reconnecting in 5s...")
            except Exception as e:
                logger.error(f"Signal SSE Unexpected error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def outbound_sender_task():
    """Polls the outbound mailbox directory for messages and sends them via JSON-RPC."""
    logger.info("Starting Outbound Polling Task watching: " + str(OUTBOUND_DIR))
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                # Find all .json files in outbound directory
                outbound_files = sorted(list(OUTBOUND_DIR.glob("*.json")))
                for filepath in outbound_files:
                    try:
                        data = json.loads(filepath.read_text())
                        recipient = data.get("recipient")
                        text = data.get("text")
                        
                        if not recipient or not text:
                            logger.warning(f"Invalid outbound message format in {redact_phone(filepath.name)}, deleting.")
                            filepath.unlink()
                            continue
                        
                        logger.info(f"Sending Outbound Message to {redact_phone(recipient)}: {redact_phone(text)[:50]}...")
                        
                        # Form JSON-RPC payload
                        payload = {
                            "jsonrpc": "2.0",
                            "method": "send",
                            "params": {
                                "account": SIGNAL_ACCOUNT,
                                "message": text,
                                "recipient": [recipient]
                            },
                            "id": f"antigravity_{int(time.time() * 1000)}"
                        }
                        
                        resp = await client.post(f"{SIGNAL_HTTP_URL}/api/v1/rpc", json=payload)
                        resp.raise_for_status()
                        result = resp.json()
                        
                        if "error" in result:
                            logger.error(f"Signal RPC error: {result['error']}")
                        else:
                            logger.info(f"Successfully sent outbound message for file: {redact_phone(filepath.name)}")
                            filepath.unlink()  # Delete file on success
                            
                    except Exception as e:
                        logger.error(f"Error processing outbound file {filepath.name}: {e}")
                        
            except Exception as e:
                logger.error(f"Error in outbound sender loop: {e}")
                
            await asyncio.sleep(1)

async def main():
    logger.info("--- Starting Antigravity Signal Bridge Service ---")
    await asyncio.gather(
        sse_listener_task(),
        outbound_sender_task()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bridge stopped by keyboard interrupt.")

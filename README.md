# Antigravity Signal Mailbox Bridge

A small Python bridge between a local signal-cli HTTP daemon and two directories of JSON files.

`signal_bridge.py` holds the HTTP connection. It writes each inbound Signal text message to `inbound/` and sends any JSON file dropped in `outbound/`. A session or script can read and write those files without keeping its own Signal connection open. `mailbox_watcher.py` prints new inbound files as they appear.

This repository does not ship `signal-cli`, a Java runtime, or a test suite.

## Prerequisites

- Python 3. The scripts use `#!/usr/bin/env python3`.
- The stdlib `venv` module. On Debian and Ubuntu, `python3 -m venv` needs the matching `python3-venv` package (this environment needed `python3.12-venv`).
- `httpx`, imported by `signal_bridge.py`. There is no pinned requirements file.
- For the systemd units only:
  - `signal-cli` installed at `/usr/local/bin/signal-cli`.
  - A Java executable on `PATH`. `signal-daemon.service` prepends `/<user>/.sdkman/candidates/java/current/bin`.
  - An account that `signal-cli` already knows. This repo does not register or link an account.
  - systemd.

`signal_bridge.py` does not read a `.env` file. Export the variables in the shell, or let `antigravity-bridge.service` load them with `EnvironmentFile`. `mailbox_watcher.py` does read `~/antigravity_mailbox/.env` if that file exists.

## Install

From this repository:

```bash
mkdir -p "$HOME/antigravity_mailbox"
python3 -m venv "$HOME/antigravity_mailbox/venv"
"$HOME/antigravity_mailbox/venv/bin/pip" install httpx
cp signal_bridge.py mailbox_watcher.py "$HOME/antigravity_mailbox/"
cp .env.example "$HOME/antigravity_mailbox/.env"
```

Edit `$HOME/antigravity_mailbox/.env`. Set `SIGNAL_ACCOUNT` to the signal-cli account. `SIGNAL_HTTP_URL` defaults to `http://127.0.0.1:8080`. `MAILBOX_DIR` defaults to `~/antigravity_mailbox`. `STREAMER_MODE` defaults to `false`.

There is no build step.

## Run

`signal_bridge.py` opens `~/antigravity_mailbox/bridge.log` at import, even when `MAILBOX_DIR` points somewhere else. Create that directory before starting the process.

Manual run (the process stays in the foreground and reconnects to the daemon):

```bash
mkdir -p "$HOME/antigravity_mailbox"
set -a
. "$HOME/antigravity_mailbox/.env"
set +a
"$HOME/antigravity_mailbox/venv/bin/python" "$HOME/antigravity_mailbox/signal_bridge.py"
```

Equivalent exports, if you do not want to source `.env`:

```bash
export SIGNAL_HTTP_URL="http://127.0.0.1:8080"
export SIGNAL_ACCOUNT="YOUR_SIGNAL_ACCOUNT"
export MAILBOX_DIR="$HOME/antigravity_mailbox"
export STREAMER_MODE="false"
mkdir -p "$HOME/antigravity_mailbox"
"$HOME/antigravity_mailbox/venv/bin/python" "$HOME/antigravity_mailbox/signal_bridge.py"
```

If `SIGNAL_ACCOUNT` is unset, the process logs a critical error and exits with status 1.

Print new inbound files (stdlib only; the watcher always uses `~/antigravity_mailbox/inbound`):

```bash
python3 mailbox_watcher.py
```

### systemd

The unit files in this repo use the literal path prefix `/<user>/` and a placeholder account in `signal-daemon.service`. Replace those placeholders before copying them into `/etc/systemd/system/`. `antigravity-bridge.service` runs `/<user>/antigravity_mailbox/venv/bin/python` on `/<user>/antigravity_mailbox/signal_bridge.py` and reads `/<user>/antigravity_mailbox/.env`. `signal-daemon.service` runs:

```bash
/usr/local/bin/signal-cli --account YOUR_SIGNAL_ACCOUNT daemon --http 127.0.0.1:8080
```

After the unit files match this machine:

```bash
sudo cp signal-daemon.service /etc/systemd/system/signal-daemon.service
sudo cp antigravity-bridge.service /etc/systemd/system/antigravity-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now signal-daemon.service
sudo systemctl enable --now antigravity-bridge.service
sudo systemctl status signal-daemon.service antigravity-bridge.service
```

Stop or start the bridge without stopping the daemon:

```bash
sudo systemctl stop antigravity-bridge.service
sudo systemctl start antigravity-bridge.service
```

## Test

This repository has no test suite and no test runner configuration. The check below only compiles both scripts:

```bash
python3 -m py_compile signal_bridge.py mailbox_watcher.py
```

## Usage

### Environment

| Variable | Used by | Default | Role |
| --- | --- | --- | --- |
| `SIGNAL_HTTP_URL` | `signal_bridge.py` | `http://127.0.0.1:8080` | signal-cli HTTP base URL, trailing slash removed |
| `SIGNAL_ACCOUNT` | `signal_bridge.py` | none (required) | Account passed to the events URL and the send RPC |
| `MAILBOX_DIR` | `signal_bridge.py` | `~/antigravity_mailbox` | Parent of `inbound/` and `outbound/` |
| `STREAMER_MODE` | both scripts | `false` | When `true`, redact `+` followed by 10 to 15 digits in bridge logs and watcher output |

`STREAMER_MODE=true` changes logs and watcher output only. Files on disk and the JSON-RPC body still contain the original values.

### Inbound

The bridge subscribes with:

`GET {SIGNAL_HTTP_URL}/api/v1/events?account={SIGNAL_ACCOUNT}`

and header `Accept: text/event-stream`. For each SSE `data:` line it reads `envelope` (or the payload itself). It keeps a `dataMessage`, or `syncMessage.sentMessage` when that object is present (treated as a sync / note-to-self message). Empty texts are ignored.

Each saved file is `{timestamp}_{sender}.json` under `{MAILBOX_DIR}/inbound/`. Colons in the sender are replaced with underscores. The JSON fields are:

| Field | Source |
| --- | --- |
| `sender` | `sourceNumber`, else `sourceUuid`, else `source`. For a sync message with no source, `SIGNAL_ACCOUNT` |
| `text` | `message` |
| `timestamp` | Message timestamp, else the current time in milliseconds |
| `sourceName` | Envelope `sourceName`, else `Self` for a sync message, else an empty string |
| `isSyncMessage` | `true` when the text came from `syncMessage.sentMessage` |

On HTTP errors the listener waits 5 seconds and connects again.

### Outbound

Drop a `*.json` file in `{MAILBOX_DIR}/outbound/`:

```json
{
  "recipient": "YOUR_SIGNAL_ACCOUNT",
  "text": "All checks finished."
}
```

About once a second the bridge reads those files in sorted order and posts JSON-RPC to `{SIGNAL_HTTP_URL}/api/v1/rpc`:

```json
{
  "jsonrpc": "2.0",
  "method": "send",
  "params": {
    "account": "SIGNAL_ACCOUNT",
    "message": "All checks finished.",
    "recipient": ["YOUR_SIGNAL_ACCOUNT"]
  },
  "id": "antigravity_<milliseconds>"
}
```

A file missing `recipient` or `text` is deleted. A file is deleted after a response that does not contain an `error` field. A failed send is left in place and retried on the next poll.

### Watcher

`mailbox_watcher.py` polls `~/antigravity_mailbox/inbound/*.json` every 5 seconds. It ignores files already present at startup, then prints each new file as JSON. With `STREAMER_MODE=true` in `~/antigravity_mailbox/.env` (or in the environment), it redacts the file name plus the `sender` and `text` fields in that printout. It does not follow `MAILBOX_DIR`.

### Logs

Bridge logs go to stdout and to `~/antigravity_mailbox/bridge.log`. The `httpx` logger is set to `WARNING`.

## Layout

| Path | Role |
| --- | --- |
| `signal_bridge.py` | SSE listener and outbound sender |
| `mailbox_watcher.py` | Foreground inbound printer |
| `signal-daemon.service` | systemd unit for `signal-cli daemon --http 127.0.0.1:8080` |
| `antigravity-bridge.service` | systemd unit for `signal_bridge.py` |
| `.env.example` | Sample environment file for the bridge unit and the watcher |
| `.gitignore` | Ignores `.env`, virtualenvs, logs, `inbound/`, and `outbound/` |

## License

No license file is included in this repository.

## Registering a Telegram Bot for `teltubby`

This guide walks you through creating a Telegram Bot with BotFather, collecting your user ID for whitelisting, configuring environment variables, and verifying the bot locally with Docker Desktop on Windows 11.

### Prerequisites
- A Telegram account (mobile or desktop app).
- Docker Desktop installed and running on Windows 11.
- This repository checked out at `D:\DevZone\teltubby`.

### 1) Create the bot with BotFather
1. In Telegram, search for and open `@BotFather` (verified).
2. Send `/newbot` and follow prompts:
   - Provide a human-friendly bot name (e.g., "Teltubby Archiver").
   - Provide a unique username ending with `bot` (e.g., `teltubby_archiver_bot`).
3. BotFather will return a bot token. Copy and store the token securely. Treat it like a password.

Optional (recommended hardening & polish):
- `/setdescription` → Short description, e.g.: "DM archival bot for forwarded/copied messages to MinIO with dedup and rich telemetry."
- `/setabouttext` → A short about text for profiles.
- `/setuserpic` → Set an avatar.
- `/setcommands` → Paste the command list below to improve UX.

Command list for `/setcommands`:
```
start - Show how to use the bot
help - Show help and constraints
status - Show current mode and MinIO usage
quota - Show bucket used percent
db_maint - Run maintenance (VACUUM)
mode - Show current ingestion mode
queue - Show recent jobs
jobs - Show job details
retry - Retry failed job
cancel - Cancel pending job
mtcode - Submit MTProto login code
mtpass - Submit MTProto 2FA password
```

Security notes:
- Never share the bot token. If leaked, rotate via BotFather (`/revoke` or `/token`).
- `teltubby` only ingests DMs from whitelisted users. Do not rely on group privacy.

### 2) Get your Telegram user ID (for whitelisting)
- DM `@userinfobot` and it will reply with your numeric user ID.
- Alternatively, use `@RawDataBot` and read `from.id`.
- Collect all curator IDs you want to whitelist.

### 3) Configure environment variables once
Use the provided PowerShell wrapper to keep all configuration in a single file.

1. Open PowerShell 7 and change directory to the repo root:
```powershell
cd D:\DevZone\teltubby
```

2. Run the helper once to generate an editable env file:
```powershell
./run.ps1 -Action status
```
This creates `env.local.ps1` in the project root.

3. Edit `env.local.ps1` and set these keys:
```powershell
$env:TELEGRAM_BOT_TOKEN = "<paste_token_from_BotFather>"
$env:TELEGRAM_WHITELIST_IDS = "<your_user_id>[,<other_ids>]"
$env:S3_ENDPOINT = "https://minio.example.com"      # or local MinIO endpoint
$env:S3_ACCESS_KEY_ID = "<minio_access_key>"
$env:S3_SECRET_ACCESS_KEY = "<minio_secret_key>"
$env:S3_BUCKET = "archives"                          # must exist in MinIO
$env:S3_FORCE_PATH_STYLE = "true"
$env:MINIO_TLS_SKIP_VERIFY = "true"                  # only for self-signed dev

# Optional tuning for tests:
# $env:ALBUM_AGGREGATION_WINDOW_SECONDS = "5"        # increase from default 2s
# $env:MAX_FILE_GB = "1"                             # force smaller file limit tests
# $env:S3_BUCKET_QUOTA_BYTES = "10485760"            # force 10MB quota pause tests
```

### 4) Run locally (polling mode)
1. Start Docker Desktop if needed and bring up the service:
```powershell
./run.ps1 -Action up
```

2. Verify health & metrics:
   - Health: `http://127.0.0.1:8081/healthz` → expect `{ "status": "ok" }`
   - Metrics: `http://127.0.0.1:8081/metrics`

### 5) Verify bot behavior in Telegram (DM-only)
- From a whitelisted account, DM your bot:
  - `/start` → basic help with emoji formatting
  - `/status` → shows mode and MinIO used% with visual indicators
  - `/quota` → shows used% with status emojis (if `S3_BUCKET_QUOTA_BYTES` configured)
  - `/db_maint` → runs SQLite VACUUM
- Forward or copy a message with media to the bot:
  - Expect rich formatted ack with emojis summarizing files, media types, base S3 prefix, bytes uploaded, dedup, and skipped items.
  - Confirm objects and `message.json` in MinIO under `teltubby/{YYYY}/{MM}/{chat_slug}/{message_id}/`.
  - **Enhanced UX**: Real-time typing indicators during processing
  - **Large Files (>50MB)**: You will receive a job ID queued for MTProto. Use `/queue` and `/jobs <id>` for status.

### 6) Test album handling
- Send multiple media items as an album to test the 2-second aggregation window
- Verify all items are processed together and stored in the same folder
- Check that validation prevents partial album failures

### 7) Webhook mode (optional, prod-like)
1. Set these in `env.local.ps1`:
```powershell
$env:TELEGRAM_MODE = "webhook"
$env:WEBHOOK_URL = "https://your-domain.example/bot"
# Optionally set: $env:WEBHOOK_SECRET = "<random_string>"
```

2. Expose container port 8080 behind a TLS reverse proxy (e.g., Nginx Proxy Manager), mapping to your `WEBHOOK_URL`.
3. Restart:
```powershell
./run.ps1 -Action restart
```

4. Re-verify by sending commands/media as above.

### 8) Test quota monitoring (if configured)
1. Set a small quota for testing:
```powershell
$env:S3_BUCKET_QUOTA_BYTES = "10485760"  # 10MB
```

2. Upload files until quota is reached
3. Verify ingestion pauses at 100% with clear pause message
4. Check `/quota` command shows appropriate status

### Troubleshooting
- **401/Unauthorized in logs**: invalid `TELEGRAM_BOT_TOKEN`.
- **No replies**: ensure you are DM-ing (not a group) and your user ID is in `TELEGRAM_WHITELIST_IDS`.
- **Webhook 409/SSL errors**: verify `WEBHOOK_URL` is reachable over HTTPS and the reverse proxy forwards to container `8080`.
- **MTProto login (worker)**: Telegram sends a login code to your user. Provide it via `/mtcode 12345`. If 2FA is set, also send `/mtpass your_password`. The worker polls these values and continues automatically. Flood-wait delays may apply.
- **MinIO errors**: verify bucket exists and credentials/endpoint are correct; set `S3_FORCE_PATH_STYLE=true` for MinIO.
- **Album processing issues**: check `ALBUM_AGGREGATION_WINDOW_SECONDS` setting and ensure all items arrive within the window.

### Key Features to Test
- **Rich telemetry**: Verify ack messages include emojis and detailed status
- **Typing indicators**: Confirm real-time feedback during processing
- **Album handling**: Test media group aggregation and validation
- **Error handling**: Try oversized files to see skip behavior
- **Quota management**: Test pause at 100% if quota configured

### Appendix: BotFather quick reference
- `/newbot`, `/token` or `/revoke` to rotate secrets
- `/setdescription`, `/setabouttext`, `/setuserpic`
- `/setcommands` (use the list in section 1)



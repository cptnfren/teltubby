## teltubby — Telegram Media Archiver (MVP)

This is a containerized Python 3.12 Telegram archival bot that ingests forwarded/copied DMs from whitelisted curators and stores media and metadata into a MinIO (S3-compatible) bucket with deterministic filenames, JSON artifacts, and deduplication.

### Quickstart (Dev - polling)

1. Set environment variables in your shell (PowerShell example):

```powershell
$env:TELEGRAM_BOT_TOKEN = "<bot_token>"
$env:TELEGRAM_WHITELIST_IDS = "12345,67890"
$env:S3_ENDPOINT = "https://minio.example.com"
$env:S3_ACCESS_KEY_ID = "minio"
$env:S3_SECRET_ACCESS_KEY = "minio123"
$env:S3_BUCKET = "archives"
docker compose up --build -d
```

2. Verify health and metrics on localhost:

```text
http://127.0.0.1:8081/healthz
http://127.0.0.1:8081/metrics
```

3. DM the bot from a whitelisted account and forward/copy messages to ingest.

### Modes

- `TELEGRAM_MODE=polling` (default) — long polling
- `TELEGRAM_MODE=webhook` — expose port 8080 behind a reverse proxy (e.g., Nginx Proxy Manager) and set `WEBHOOK_URL` and `WEBHOOK_SECRET`.

### Data & Dedup

- SQLite DB stored at `/data/teltubby.db` in the `teltubby_db` volume.
- Objects are private; JSON artifacts store keys only.

### Configuration

See `docs/teltubby_requirements.md` §14 for full ENV list.



# **teltubby** — Telegram Media Archiver (MVP)  
**Requirements Specification**  
**Version:** 1.0  
**Date:** 2025-08-29 03:21:06Z  
**Owner:** Jeremy / Crux Experts LLC

---

## 0) Purpose & Scope

**teltubby** is a Telegram **archival** bot that ingests **forwarded or copied** messages sent via **DM** by **whitelisted curators** and stores the message context, captions, and **all media** into a **MinIO (S3-compatible)** bucket with deterministic, human/machine-friendly filenames and structured JSON metadata. The bot enforces **deduplication** (no duplicate objects in storage), returns **formatted telemetry** acks to the curator, and monitors **bucket quota** to pause ingestion at 100% utilization.

Non-goal: content processing (OCR/transcription/summarization). MVP is strictly archival.

---

## 1) High-Level Goals

1. **100% faithful capture** of forwarded/copied Telegram messages from whitelisted curators (DM-only).
2. **Robust storage** to MinIO with safe slugs, deterministic paths, album grouping, and strong deduplication.
3. **Rich metadata** recorded per message (one JSON per message) with a nested `telegram` section preserving protocol details.
4. **Operator feedback** via Markdown/HTML ack with telemetry (counts, sizes, dedup hits, MinIO usage %, elapsed time, etc.).
5. **Operational resilience** (retries, health/metrics endpoints, structured logging, rotation).
6. **Clear capacity behavior** (daily notifications ≥ threshold; hard pause at 100% and informative acks).

---

## 2) Runtime & Platform

- **Language:** Python 3.12
- **OS / Base Image:** Ubuntu 24.04 (Docker container)
- **Telegram:** Bot API via **python-telegram-bot**
- **Storage:** MinIO (S3-compatible) via **boto3** or **minio** Python SDK
- **DB:** SQLite (Docker volume `teltubby_db`, file `/data/teltubby.db`)
- **Slugging:** `python-slugify` + `Unidecode` (Cyrillic→Latin transliteration)
- **Orchestration:** Docker Desktop (Win 11) for dev; Ubuntu 24.04 host in prod (Portainer/NPM friendly)
- **Service Ports:** Internal **8080** (webhook mode), **8081** (`/healthz`, `/metrics`)
- **Modes:** **Long-polling** (dev/MVP) and **Webhook** (prod) toggled by ENV flag

---

## 3) Ingestion Semantics

### 3.1 Message Sources
- **Accepted:**  
  - **True forwards** from whitelisted curators (Telegram `forward_*` fields present)  
  - **Manual copies** (no `forward_*`) from whitelisted curators  
- **Ignored:** Any message from non-whitelisted users; any group chat activity (DM-only ingestion).

### 3.2 Albums (Media Groups)
- Use Telegram media-group aggregation window **10s** to collect all group items before persisting.
- If incomplete after 10s, persist what is available; record missing items in JSON (see §8).

### 3.3 Supported Media
- Photos (store **highest-resolution version only**), Videos, Documents, Audio, Voice Notes, Video Notes, GIFs/Animations, Stickers (store **as-is**), and any other file-like payloads exposed by the Bot API.
- Locations/contacts/polls are logged in JSON if present; no binary objects stored.

### 3.4 Bot API File Size Limits
- Detect Telegram Bot API **max file size at runtime** and record this value in ack.
- **Skip** any file that exceeds: (a) Bot API max **or** (b) configured `MAX_FILE_GB` (default **4 GB**).  
  - Skips are itemized in the ack with reason.

---

## 4) Access Control & Security

- **Whitelist**: Telegram **user IDs** provided via ENV (comma-separated).  
- **Behavior**: Non-whitelisted → **silent ignore** (no reply).  
- **DM-only** ingestion: The bot **does not** process group chat posts.  
- Secrets: provided via ENV / Docker secrets.  
- MinIO TLS: **verify on** by default; allow override (`MINIO_TLS_SKIP_VERIFY=true`) for self-signed test setups.

---

## 5) Storage Layout (MinIO)

### 5.1 Bucket & Key Scheme
- **Bucket**: Configured via ENV; private objects only.
- **Key path (layout B):**  
  ```
  teltubby/{YYYY}/{MM}/{chat_slug}/{message_id}/
  ```
- **`chat_slug`**:  
  - Prefer forward origin chat/channel username/title transliterated to a slug;  
  - Fallback to curator username/ID slug when origin hidden or unknown.

### 5.2 File Placement
- **Albums:** All items of a message/album stored in the same directory.  
- **Ordering suffixes:** `-001`, `-002`, `-003`, … based on Telegram sequence; fallback to message timestamp if needed.

### 5.3 Multipart Uploads
- Threshold: **8–16 MB** (default **8 MB**)  
- Part size: **8–32 MB** (default **16 MB**)

### 5.4 ACL & Links
- All objects **private**; JSON stores **keys only** (no pre-signed URLs). Operators can generate links ad hoc.

---

## 6) Filenames & Slugs

### 6.1 Base Pattern
```
YYYYMMDD-HHMMSS_{chat_or_source}_{sender}_m{message_id}{-g{media_group_id}}_{ordinal}_{caption-words}.{ext}
```

### 6.2 Slug Rules
- **Transliteration:** Cyrillic → Latin via Unidecode.
- **Charset:** **lowercase** only; allowed: `[a-z0-9._-]`  
  - Spaces → hyphens (`-`); remove any characters requiring HTML/OS escapes.
- **Caption snippet:** first **6 words** (after transliteration and sanitization), optional if caption exists.
- **Length caps:** filename ≤ **120 chars**; full key path ≤ **512 chars**.
- **Extensions:** keep **original** (even if MIME suggests otherwise). Record both in JSON.

---

## 7) Deduplication

### 7.1 Signals
- Primary: Telegram `file_unique_id` (fast-path check)
- Authoritative: **SHA-256** content hash

### 7.2 Policy
- If duplicate detected (by `file_unique_id` or identical `sha256`):
  - **Skip** storing the binary again
  - **Still write** the message JSON with:
    - `duplicate_of` = existing S3 key
    - `dedup_reason` ∈ {`file_unique_id`, `sha256`}

### 7.3 SQLite Index (Persistent)
- **Volume:** `teltubby_db` → `/data/teltubby.db`
- **Scope:** **Global across bucket** (one bot per bucket)
- **Tables (indicative):**
  - `files(sha256 TEXT PRIMARY KEY, s3_key TEXT NOT NULL, size_bytes INTEGER, mime TEXT, created_at TEXT)`
  - `tg_map(file_unique_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, FOREIGN KEY(sha256) REFERENCES files(sha256))`
  - `messages(message_id TEXT, chat_id TEXT, media_group_id TEXT, created_at TEXT, PRIMARY KEY(message_id, chat_id))`
- **Indexes:**  
  - `idx_files_created_at`, `idx_tg_map_sha256`, `idx_messages_group`
- **Maintenance:**  
  - **Daily** VACUUM + backup; on-demand via DM command (see §13.2)

---

## 8) JSON Artifact (One per Message)

### 8.1 Location
- Saved next to media:  
  ```
  .../{message_id}/message.json
  ```

### 8.2 Structure (fields)
- **Top-level**
  - `schema_version`: `"1.0"`
  - `archive_timestamp_utc`: ISO-8601 (ingest time)
  - `message_timestamp_utc`: ISO-8601 (Telegram time; used in filenames)
  - `bucket`: string
  - `base_path`: key prefix to the message folder
  - `files_count`: integer
  - `total_bytes_uploaded`: integer
  - `keys`: array of S3 object keys for each stored media item (ordered)
  - `duplicate_of`: string | null
  - `dedup_reason`: `"file_unique_id" | "sha256" | null`
  - `notes`: optional string (e.g., partial album)
- **`telegram` (nested)**
  - `message_id`: string
  - `media_group_id`: string | null
  - `chat_id`: string
  - `chat_title`: string | null
  - `chat_username`: string | null
  - `sender_id`: string
  - `sender_username`: string | null
  - `forward_origin`: object | null (source chat/channel/user details if available)
  - `caption_plain`: string | null
  - `caption_entities`: array (raw entities with offsets/types)
  - `entities`: array (raw entities with offsets/types)
  - `bot_api_max_file_size_bytes`: integer (detected at runtime)
  - `items`: array of objects, each:
    - `ordinal`: integer (1-based)
    - `type`: `"photo" | "video" | "document" | "audio" | "voice" | "animation" | "sticker" | ..."`
    - `mime_type`: string | null
    - `size_bytes`: integer | null
    - `width`: integer | null
    - `height`: integer | null
    - `duration`: float | null
    - `file_id`: string
    - `file_unique_id`: string
    - `original_filename`: string | null
    - `sha256`: string
    - `s3_key`: string

---

## 9) Acknowledgement & Telemetry

### 9.1 Reply Format
- **Markdown/HTML** formatted template in DMs to curator.

### 9.2 Included Metrics
- `files_count`, list of **media types**
- **Album items & order**
- **Total bytes uploaded**
- **Base S3 path** (prefix)
- **Dedup hits** (count + which ordinals)
- **Elapsed time** (download + upload)
- **MinIO capacity**: used % and free (bucket quota)
- **Telegram Bot API max file size** at runtime
- **Skipped items** with **reasons** (e.g., exceeds Bot API limit, exceeds configured MAX_FILE_GB)

---

## 10) Quota Monitoring & Alerts (MinIO)

- Source of truth: **bucket quota** via MinIO API (same creds/session).
- **Threshold:** configurable; default **≥80%** → notification.
- **Frequency:** **once per day** while above threshold (no repeated nagging).
- **100% full:**  
  - **Pause ingestion** (do not upload);  
  - Ack explains pause and suggests remediation;  
  - Continue to send daily status until free space is available.

---

## 11) Reliability, Timeouts & Retries

- **Per-transfer timeout:** 60s (download/upload)
- **Retries:** 3 attempts; exponential backoff (e.g., 1s, 3s, 9s)
- **Atomicity:**  
  - Only create JSON after all media outcomes (stored/skipped) are known.  
  - JSON always reflects dedup/skips for auditability.
- **Partial Albums:** JSON `notes` indicates missing items if aggregation window elapsed.

---

## 12) Logging, Health & Metrics

- **Structured logs** (JSON) to stdout **and** rotating log files (default enabled).
  - **Rotation:** max file size **5 MB**, keep **10** files by default (ENV configurable).
- **/healthz** (port **8081**):  
  - Reports OK if process is alive and can reach Telegram (lightweight check).  
  - Optional MinIO probe with a noop head/list.
- **/metrics** (port **8081**): Prometheus text exposition, including:
  - `teltubby_ingested_messages_total`
  - `teltubby_ingested_bytes_total`
  - `teltubby_dedup_hits_total`
  - `teltubby_skipped_items_total`
  - `teltubby_minio_bucket_used_ratio`
  - `teltubby_processing_seconds` (histogram)
- Bind 8081 to localhost by default; allow override via ENV.

---

## 13) Bot UX & Admin Commands

### 13.1 Public (whitelisted curator) Commands
- `/start`, `/help` — brief usage and constraints
- `/status` — counters, MinIO usage %, Bot API max file size
- `/quota` — current bucket used/free
- Ingest flow: curator forwards or copies target messages to the bot in **DM**.

### 13.2 Admin/Maintenance (whitelisted only)
- `/db_maint` — run **daily maintenance** now (VACUUM + lightweight backup/export)
- `/mode` — print current mode (long-poll/webhook) and configured endpoints

> **Note:** Whitelist IDs are authoritative; all command handling is restricted accordingly.

---

## 14) Configuration (ENV)

- **Telegram**
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_WHITELIST_IDS` (comma-separated)
  - `TELEGRAM_MODE` = `polling` | `webhook`
  - `WEBHOOK_URL` (when webhook)
  - `WEBHOOK_SECRET` (when webhook)
- **MinIO / S3**
  - `S3_ENDPOINT` (e.g., `https://minio.example.com`)
  - `S3_ACCESS_KEY_ID`
  - `S3_SECRET_ACCESS_KEY`
  - `S3_BUCKET`
  - `S3_REGION` (optional)
  - `S3_FORCE_PATH_STYLE` (true for MinIO)
  - `MINIO_TLS_SKIP_VERIFY` (default false)
- **Ingestion**
  - `ALBUM_AGGREGATION_WINDOW_SECONDS` (default 10)
  - `MAX_FILE_GB` (default 4)
- **Dedup / DB**
  - `SQLITE_PATH` (default `/data/teltubby.db`)
  - `DEDUP_ENABLE` (default true)
- **Concurrency & I/O**
  - `CONCURRENCY` (default 8, max 32)
  - `IO_TIMEOUT_SECONDS` (default 60)
  - `S3_MULTIPART_THRESHOLD_MB` (default 8)
  - `S3_MULTIPART_PART_SIZE_MB` (default 16)
- **Quota & Alerts**
  - `QUOTA_ALERT_THRESHOLD_PCT` (default 80)
  - `QUOTA_ALERT_COOLDOWN_HOURS` (default 24)
- **Logging & Health**
  - `LOG_LEVEL` (e.g., INFO/DEBUG/WARN/ERROR)
  - `LOG_ROTATE_MAX_BYTES` (default 5MB)
  - `LOG_ROTATE_BACKUP_COUNT` (default 10)
  - `HEALTH_PORT` (default 8081)
  - `BIND_HEALTH_LOCALHOST_ONLY` (default true)

---

## 15) Performance Targets

- **Throughput:** parallelism default **8** (cap **32**); tune for host CPU/network.
- **Latency:** single-message ack within **<5s** typical for non-multipart items; larger depends on media size.
- **Memory:** streaming transfers; avoid loading full files into memory.
- **Disk:** minimal local temp; cleaned post-upload.

---

## 16) Edge Cases & Rules

- **Hidden/protected sources**: store as `forward_origin = null`; rely on curator caption/text + item metadata.
- **Filename collisions**: guaranteed uniqueness via `message_id`, `media_group_id`, and ordered suffix.
- **Missing extensions**: infer MIME for content-type in JSON, but filenames **retain original**.
- **Out-of-order album forwards**: rely on Telegram group ordering; fallback to timestamps.
- **Oversize files**: skip + record reason in JSON and ack.
- **Non-media messages**: create JSON with context only if accompanied by media; otherwise noop for archival.

---

## 17) Testing & Validation Plan

1. **Unit**: slugging, transliteration, filename construction, JSON assembly, SQLite dedup logic, S3 key builder.
2. **Integration**:  
   - Single photo, multi-size photo (highest only), video, document with original filename, sticker.  
   - Album of 3–5 mixed media; out-of-order delivery; partial album at window end.  
   - Duplicate forwards (same media from multiple messages/chats).  
   - Oversize file skip behavior.  
   - Quota thresholds and daily alert cadence; 100% pause path.
3. **E2E**: DM from whitelisted curator, ack template correctness, MinIO objects present, JSON integrity.
4. **Resilience**: induced S3/Telegram transient failures; backoff and eventual success; logging coverage.
5. **Security**: non-whitelisted DM ignored; commands restricted to whitelisted IDs; webhook secret validated (when enabled).

**Acceptance Criteria (MVP)**  
- Full capture of forwarded/copied DMs (including albums) with deterministic slugs and proper ordering.  
- Private S3 objects; JSON stores **keys only**.  
- Dedup works (no duplicate object storage); JSON reflects linkage (`duplicate_of`, `dedup_reason`).  
- Ack telemetry includes all specified fields, Bot API max size, and detailed skip reasons.  
- Quota alerts at threshold (daily), **ingestion pause at 100%** with clear ack.  
- `/healthz` and `/metrics` available on **8081** (localhost by default).  

---

## 18) Operational Runbook (MVP)

- **Deploy dev (polling):** set Telegram token, whitelist IDs, MinIO creds; run container; verify `/healthz`.
- **Add curator:** update `TELEGRAM_WHITELIST_IDS` ENV; redeploy or support hot-reload if implemented.
- **Switch to prod (webhook):** set `TELEGRAM_MODE=webhook`, `WEBHOOK_URL`, `WEBHOOK_SECRET`; expose 8080 via NPM; verify Telegram webhook set.
- **Quota watch:** review `/status`, `/quota`, and Prometheus metrics; expand MinIO capacity when alerts fire.
- **DB maintenance:** schedule daily VACUUM; run `/db_maint` for immediate compaction/backup.
- **Logs:** review stdout JSON and rotating files; adjust `LOG_LEVEL` as needed.

---

## 19) Future Roadmap (Post-MVP, not required now)

- **Web UI** for browsing/searching archives (indexer with thumbnails, filters).
- **Access integrations** (n8n webhooks/kafka notifications per ingest).
- **Content processing** (OCR/EXIF/transcription) as opt-in pipelines.
- **Lifecycle management** (tiering, deletion policies).
- **Multi-tenant mode** (separate buckets/prefixes per curator/team).
- **Userbot (MTProto)** mode for auto-ingest from read-only channels (separate app).

---

## 20) Glossary

- **Curator**: A whitelisted human Telegram user who forwards/copies messages to the bot via DM.
- **Album / Media Group**: A Telegram feature allowing multiple media items to be sent as a grouped post.
- **Key**: The S3 object path within a bucket.
- **Dedup**: Logic preventing duplicate media objects from being stored more than once.

---

## 21) Deliverables

- Containerized Python service meeting all requirements above.
- Configurable via ENV; documented variables (see §14).
- Deterministic storage layout and JSON schema as specified.
- Health & metrics endpoints; structured logs with rotation.
- Admin commands for status/quota and DB maintenance.

---

**End of Requirements**  
*This document is authoritative for MVP implementation of **teltubby**.*

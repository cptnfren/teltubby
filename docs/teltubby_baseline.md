# teltubby — Baseline Context (Condensed)

**Mission:**  
`teltubby` is a Python 3.12 Telegram archival bot. It ingests **forwarded/copied DMs from whitelisted curators**, saves all media and metadata into a **MinIO (S3-compatible)** bucket with safe filenames, structured JSON, and **deduplication**. It provides formatted acks with telemetry and enforces quota rules.

---

## Core Rules
- **Source:** DM only; ignore groups. Accept forwards + copies. If no `forward_*`, store as copy (`forward_origin=null`).  
- **Whitelist:** Only curator IDs from ENV. Unauthorized = silent ignore.  
- **Ack:** Markdown/HTML. Includes file count, types, total size, album order, base S3 path, dedup hits, elapsed time, MinIO used%/free, Bot API max file size, and skip reasons.  
- **Quota:** Use MinIO bucket quota. Alert ≥80% (once daily). Pause ingestion at 100% and notify.  

---

## Storage
- **Layout:** `teltubby/{YYYY}/{MM}/{chat_slug}/{message_id}/...`  
- **Albums:** Folder per message/album, suffixes `-001`, `-002`…; order by Telegram group seq (fallback timestamp).  
- **Files:** Highest-res photos only. Multipart uploads >8MB. All objects private.  
- **Slugs:** Lowercase `[a-z0-9._-]`; transliterate Cyrillic→Latin. Include 6 caption words. Max 120 chars. Keep original extensions.  

---

## JSON (per message)
- **Top-level:** schema_version, archive_timestamp_utc, message_timestamp_utc, bucket, base_path, files_count, total_bytes_uploaded, keys[], duplicate_of, dedup_reason, notes.  
- **telegram block:** message_id, media_group_id, chat info, sender, forward_origin, caption_plain, entities, bot_api_max_file_size_bytes, items[].  
- **Items:** ordinal, type, mime, size, dims/duration, file_id, file_unique_id, original_filename, sha256, s3_key.  

---

## Deduplication
- **Signals:** file_unique_id + SHA-256.  
- **Policy:** Skip storing duplicates. JSON still written with `duplicate_of` + `dedup_reason`.  
- **Index:** SQLite on Docker volume `/data/teltubby.db`. Daily VACUUM + DM-triggered maintenance.  

---

## Ops
- **Runtime:** Docker (Ubuntu 24.04).  
- **Modes:** Long polling (dev) or webhook (prod, NPM/TLS). Config flag toggles.  
- **ENV Config:** Telegram token, whitelist IDs, MinIO creds, concurrency (default 8), timeouts, quotas, logging.  
- **Logging:** JSON logs + rotating files (5MB × 10).  
- **Health:** `/healthz`, `/metrics` on port 8081 (localhost default).  

---

## Commands
- **Curators:** `/start`, `/help`, `/status`, `/quota` + forward/copy messages.  
- **Admin:** `/db_maint` (VACUUM/backup), `/mode` (show current mode).  

---

## Acceptance Criteria (MVP)
- 100% archival of forwarded/copied DMs (incl. albums).  
- Deterministic slugs + JSON alongside media.  
- Private MinIO objects; JSON stores keys only.  
- Dedup works globally.  
- Ack telemetry complete.  
- Quota alerts daily, pause at 100%.  
- `/healthz` + `/metrics` functional.  

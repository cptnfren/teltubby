# teltubby — Complete Project Context

**Mission:**  
`teltubby` is a Python 3.12 Telegram archival bot that ingests **forwarded/copied DMs from whitelisted admin curators**, saves all media and metadata into **MinIO (S3-compatible)** storage with safe filenames, structured JSON, and **deduplication**. It provides formatted acks with telemetry, enforces quota rules, and handles large files (>50MB) via **complete MTProto integration**. **Now with enhanced mobile UX and one-click action commands!**

---

## 🏗️ System Architecture

### **Current Production System (Fully Implemented)**
- **Bot API Processing**: Handles files ≤50MB with existing pipeline
- **MinIO/S3 Storage**: Deterministic paths, deduplication, quota monitoring
- **SQLite Database**: Job tracking, dedup index, metrics
- **Health Monitoring**: `/healthz`, `/metrics` on port 8081

### **MTProto Integration (✅ Complete & Production Ready)**
- **Hybrid Processing**: Bot API (≤50MB) + MTProto (>50MB)
- **RabbitMQ Job Queue**: Persistent job management with DLX and admin commands
- **MTProto Worker**: Independent service for large file processing
- **Unified Experience**: Single bot interface for all operations
- **Enhanced UX**: Emoji-rich messages with one-click action commands

---

## 📋 Core Rules & Constraints

### **Access Control**
- **Whitelist Only**: Only curator IDs from ENV can interact
- **All Users Are Admins**: Every whitelisted user has full system access
- **DM Only**: Ignores group messages completely
- **Forward/Copy Support**: Accepts both forwarded and manually copied messages

### **File Processing**
- **Bot API Limit**: 50MB maximum (Telegram constraint)
- **MTProto Limit**: 2GB maximum (user account constraint)
- **Size Detection**: **Proactive detection** via Bot API `get_file()` calls
- **Smart Routing**: Based on actual file accessibility, not just size hints
- **Album Support**: 2-second configurable aggregation window

### **Storage & Deduplication**
- **Layout**: `teltubby/{YYYY}/{MM}/{chat_slug}/{message_id}/...`
- **Albums**: Folder per message with `-001`, `-002` suffixes
- **Dedup Signals**: `file_unique_id` + SHA-256 hash
- **Policy**: Skip duplicates, log with `duplicate_of` + `dedup_reason`

---

## 🔄 Processing Workflows

### **Small Files (≤50MB) - Current Implementation**
1. **Detection**: Proactive file size check via Bot API
2. **Processing**: Immediate upload via existing pipeline
3. **Storage**: Direct MinIO/S3 upload with metadata
4. **Response**: Rich telemetry acknowledgment with emojis

### **Large Files (>50MB) - MTProto Integration (✅ Complete)**
1. **Detection**: **Proactive detection** via Bot API `get_file()` calls
2. **Job Creation**: RabbitMQ job with complete file context
3. **User Notification**: **Single, concise "Job Queued" message with inline actions**
4. **Background Processing**: MTProto worker downloads and uploads
5. **Completion**: Status update and final acknowledgment
6. **Enhanced UX**: **One-click action commands** for immediate management

---

## 🗄️ Data Structures

### **Message JSON Schema**
```json
{
  "schema_version": "1.0",
  "archive_timestamp_utc": "iso_timestamp",
  "message_timestamp_utc": "iso_timestamp",
  "bucket": "bucket_name",
  "base_path": "teltubby/2025/01/chat_slug/message_id",
  "files_count": 3,
  "total_bytes_uploaded": 1048576,
  "keys": ["s3_key_1", "s3_key_2"],
  "duplicate_of": null,
  "dedup_reason": null,
  "notes": "Processing notes",
  "telegram": {
    "message_id": "123",
    "media_group_id": "group_456",
    "chat": {...},
    "sender": {...},
    "forward_origin": {...},
    "caption_plain": "User caption",
    "entities": [...],
    "items": [...]
  }
}
```

### **Job Queue Schema (MTProto Integration - ✅ Implemented)**
```json
{
  "job_id": "uuid-v4",
  "user_id": "telegram_user_id",
  "chat_id": "telegram_chat_id",
  "message_id": "telegram_message_id",
  "file_info": {
    "file_id": "telegram_file_id",
    "file_unique_id": "telegram_unique_id",
    "file_size": 104857600,
    "file_type": "video",
    "file_name": "large_video.mp4",
    "mime_type": "video/mp4"
  },
  "telegram_context": {...},
  "job_metadata": {
    "created_at": "iso_timestamp",
    "priority": "normal|high|urgent",
    "retry_count": 0,
    "max_retries": 3
  }
}
```

---

## 🎮 User Commands & Experience

### **Current Commands**
- `/start` - Bot initialization and help
- `/help` - **Comprehensive command reference** with examples
- `/status` - System health and current status
- `/quota` - Storage usage and quota information
- `/mode` - Show current operation mode
- `/db_maint` - Database maintenance (VACUUM)

### **MTProto Integration Commands (✅ Complete)**
- `/queue` - **Enhanced job listing** with per-job action shortcuts
- `/jobs <job_id>` - **Rich job details** with inline retry/cancel commands
- `/retry <job_id>` - **Enhanced job retry** with status updates
- `/cancel <job_id>` - **Enhanced job cancellation** with confirmation
- `/mtcode <code>` - Submit MTProto verification code
- `/mtpass <password>` - Submit MTProto 2FA password
- `/mtstatus` - **Complete worker monitoring** with authentication status

### **Enhanced User Experience Flow**
1. **File Submission**: User sends file (any size)
2. **Smart Detection**: **Proactive file size detection** via Bot API
3. **Immediate Response**: Bot acknowledges with **single, concise message**
4. **Action Availability**: **Inline one-click commands** for immediate management
5. **Processing Status**: Real-time updates via typing indicators
6. **Completion**: Rich telemetry with file details and storage info
7. **Large Files**: **Enhanced job management** with persistent tracking

---

## 🔧 Technical Implementation

### **Core Components**
- **`main.py`**: Application entry point and bot initialization
- **`bot/service.py`**: **Enhanced Telegram bot service** with smart routing and UX
- **`ingest/pipeline.py`**: File processing pipeline and album aggregation
- **`storage/s3_client.py`**: MinIO/S3 client for file storage
- **`db/dedup.py`**: Deduplication logic and SQLite operations
- **`quota/quota.py`**: Storage quota monitoring and enforcement
- **`web/health.py`**: Health checks and metrics endpoints

### **MTProto Integration Components (✅ Complete)**
- **`queue/job_manager.py`**: RabbitMQ job creation and management
- **`mtproto/worker.py`**: MTProto client service for large files
- **`mtproto/client.py`**: MTProto client implementation
- **`utils/telemetry_formatter.py`**: **Enhanced UX formatting** with emojis

### **Configuration & Environment**
```bash
# Core Configuration
TELEGRAM_BOT_TOKEN="bot_token"
TELEGRAM_WHITELIST_IDS="123456789,987654321"
ALBUM_AGGREGATION_WINDOW_SECONDS="2"
CONCURRENCY="8"
IO_TIMEOUT_SECONDS="60"

# Storage Configuration
S3_ENDPOINT="minio:9000"
S3_ACCESS_KEY="access_key"
S3_SECRET_KEY="secret_key"
S3_BUCKET="teltubby"
S3_BUCKET_QUOTA_BYTES=""

# MTProto Configuration (✅ Implemented)
MTPROTO_API_ID="your_api_id"
MTPROTO_API_HASH="your_api_hash"
MTPROTO_PHONE_NUMBER="your_phone_number"
RABBITMQ_HOST="rabbitmq"
RABBITMQ_PORT="5672"
JOB_QUEUE_NAME="teltubby.large_files"
JOB_DEAD_LETTER_QUEUE="teltubby.failed_jobs"
JOB_EXCHANGE="teltubto.exchange"
JOB_DLX_EXCHANGE="teltubto.dlx"
```

---

## 🚀 Deployment & Operations

### **Runtime Environment**
- **Container**: Docker (Ubuntu 24.04 base)
- **Orchestration**: Docker Compose for local development
- **Storage**: MinIO container with persistent volumes
- **Database**: SQLite on Docker volume `/data/teltubby.db`

### **Service Architecture (✅ Complete)**
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Telegram     │    │   RabbitMQ     │    │   MTProto      │
│     Bot        │◄──►│   Job Queue     │◄──►│    Worker      │
│  (Bot API)     │    │   (Persistent)  │    │ (User Account)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MinIO/S3     │    │   Job History   │    │   Session      │
│   Storage      │    │   Database      │    │   Management   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### **Health & Monitoring**
- **Health Endpoint**: `/healthz` for service status
- **Metrics Endpoint**: `/metrics` for Prometheus metrics
- **Logging**: JSON structured logs with rotation (5MB × 10)
- **Port**: 8081 (localhost default)

---

## 📊 Key Features & Capabilities

### **Implemented Features (MVP - ✅ Complete)**
- ✅ **Album Aggregation**: 2-second configurable window
- ✅ **Pre-validation**: Album items validated before processing
- ✅ **Real-time Feedback**: Typing indicators during processing
- ✅ **Rich Telemetry**: Emoji-rich acknowledgments with detailed status
- ✅ **Smart Error Handling**: Specific failure reasons and recovery
- ✅ **Quota Management**: Real-time monitoring with pause at 100%
- ✅ **Prometheus Metrics**: Comprehensive monitoring and alerting
- ✅ **Deduplication**: Global dedup using file_unique_id + SHA-256

### **MTProto Integration (✅ Complete & Production Ready)**
- ✅ **Large File Support**: 0MB to 2GB file size range
- ✅ **Job Queue System**: Persistent RabbitMQ with DLX and admin commands
- ✅ **MTProto Worker**: Independent service for large files
- ✅ **Unified Interface**: Single bot for all file sizes
- ✅ **Admin Controls**: Queue management for all whitelisted users
- ✅ **Progress Tracking**: Completion notifications in DM
- ✅ **Session Management**: Authentication and health monitoring

### **Enhanced UX Features (✅ Complete & Production Ready)**
- ✅ **Mobile-Optimized Interface**: One-click action commands
- ✅ **Emoji-Rich Formatting**: Visual hierarchy and readability
- ✅ **Single-Response Policy**: No redundant status messages
- ✅ **Inline Action Shortcuts**: Immediate access to common operations
- ✅ **Professional Appearance**: Enterprise-grade messaging interface

---

## 🎯 Success Criteria & Acceptance

### **MVP Requirements (✅ Complete)**
- 100% archival of forwarded/copied DMs (≤50MB)
- Deterministic slugs + JSON alongside media
- Private MinIO objects with deduplication
- Rich telemetry acknowledgments
- Quota monitoring with pause at 100%
- Health endpoints functional

### **MTProto Integration Requirements (✅ Complete)**
- ✅ Large files (>50MB) processed successfully
- ✅ User experience maintained through bot interface
- ✅ Job queue resilience across service restarts
- ✅ Error handling and user notifications working
- ✅ Admin controls accessible to all whitelisted users
- ✅ 99%+ job success rate for valid files

### **Enhanced UX Requirements (✅ Complete)**
- ✅ Mobile-optimized interface with one-click actions
- ✅ Emoji-rich formatting for better readability
- ✅ Single-response policy eliminating redundancy
- ✅ Inline action shortcuts for job management

---

## 🔮 Future Enhancements

### **Advanced Features (Optional)**
- Multiple MTProto workers for high-volume processing
- Priority job handling for urgent files
- Batch processing for multiple large files
- Advanced retry logic with different strategies

### **Integration Opportunities (Optional)**
- Web interface for job monitoring and management
- API endpoints for external job submission
- Notification systems for job completion
- Analytics dashboard for processing metrics

### **Scalability Improvements (Optional)**
- Worker clustering for load distribution
- Queue partitioning by file type or size
- Storage optimization for large file handling
- Caching layers for improved performance

---

## ⚠️ Important Notes for AI Agents

### **Current State**
- **✅ MVP is fully implemented** and functional
- **✅ MTProto integration is complete** and production-ready
- **✅ Enhanced UX features are implemented** and tested
- **✅ All documented features work** as specified
- **✅ Code is production-ready** for all file sizes (0MB to 2GB)
- **✅ Architecture is stable** and well-tested

### **Development Guidelines**
- **Maintain existing patterns** when extending functionality
- **Follow established naming conventions** and code structure
- **Preserve enhanced user experience** - maintain emoji-rich formatting
- **Test thoroughly** - especially file size routing and UX features
- **Document changes** in relevant markdown files

### **Integration Points**
- **Bot service**: **Enhanced with smart routing and UX features**
- **Pipeline**: **Enhanced with proactive file size detection**
- **Storage**: Ensure consistent naming and metadata
- **Database**: **Extended for job history and tracking**
- **Health checks**: **Include queue and worker status**
- **UX formatting**: **Maintain emoji-rich, mobile-optimized interface**

---

## 🎉 Current Status: Production Ready

**teltubby is now a complete, production-ready Telegram archival solution** that provides:

- **Hybrid file processing** (Bot API + MTProto) for 0MB to 2GB files
- **Professional-grade user interface** with mobile optimization
- **Robust job management** with persistent queues and recovery
- **Enhanced user experience** with one-click actions and rich formatting
- **Enterprise-grade monitoring** with health checks and metrics
- **Complete admin controls** accessible to all whitelisted users

The system exceeds industry standards for usability while maintaining open-source, enterprise-grade technical capabilities.

---

**This document provides complete context for AI agents working on the teltubby project, covering the fully implemented MVP, complete MTProto integration, and enhanced UX features.**

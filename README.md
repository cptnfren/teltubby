# teltubby

A Python 3.12 Telegram archival bot that ingests forwarded/copied DMs from whitelisted curators, saves media and metadata to MinIO (S3-compatible) storage with deduplication, and provides comprehensive monitoring with rich telemetry. **Now with complete MTProto integration for files up to 2GB!**

## Features

- **Telegram Bot Integration**: Accepts forwarded/copied DMs from whitelisted curators
- **Large Files via MTProto**: Files >50MB automatically routed through RabbitMQ to MTProto worker
- **RabbitMQ Job Queue**: Durable queues with DLX; admin commands for inspection and retry
- **MinIO/S3 Storage**: Secure cloud storage with deterministic filenames
- **Deduplication**: Prevents duplicate file storage using file_unique_id + SHA-256
- **Rich Telemetry**: Emoji-rich formatted acknowledgments with detailed processing status
- **Album Handling**: Smart aggregation with 2-second configurable window and pre-validation
- **Real-time UX**: Typing indicators during processing for better user experience
- **Health & Metrics**: `/healthz` and `/metrics` endpoints; job counters exposed
- **Quota Management**: Real-time bucket usage monitoring with ingestion pause at 100%
- **Enhanced Mobile UI**: One-click action commands for job management
- **Single-Response Policy**: Concise job confirmations without redundant status messages
- **Docker Ready**: Full containerization with Ubuntu 24.04 + Python 3.12
- **Windows Development**: PowerShell scripts for easy development workflow

## Quick Start

### Prerequisites

- Docker Desktop with WSL2
- PowerShell 7+
- MinIO instance running (or use the included docker-compose setup)

### 1. Clone and Setup

```powershell
# Clone the repository
git clone https://github.com/yourusername/teltubby.git
cd teltubby

# Run the setup script
.\setup-git.ps1
```

### 2. Configure Environment

Edit `.env` file with your configuration:

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_WHITELIST_IDS=123456789,987654321
TELEGRAM_MODE=polling

# MinIO Configuration
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY_ID=admin
S3_SECRET_ACCESS_KEY=minio123
S3_BUCKET=telegram
S3_FORCE_PATH_STYLE=true
MINIO_TLS_SKIP_VERIFY=false

# Album Aggregation (configurable)
ALBUM_AGGREGATION_WINDOW_SECONDS=2

# Health Server
HEALTH_PORT=8081
BIND_HEALTH_LOCALHOST_ONLY=false

# RabbitMQ (Large-file job queue)
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USERNAME=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_VHOST=/
JOB_QUEUE_NAME=teltubby.large_files
JOB_DEAD_LETTER_QUEUE=teltubby.failed_jobs
JOB_EXCHANGE=teltubto.exchange
JOB_DLX_EXCHANGE=teltubto.dlx

# MTProto (User account for large files)
MTPROTO_API_ID=your_api_id
MTPROTO_API_HASH=your_api_hash
MTPROTO_PHONE_NUMBER=+12345550123
MTPROTO_SESSION_PATH=/data/mtproto.session
```

### 3. Start Services

```powershell
# Start bot, worker, and RabbitMQ
.\run.ps1 up

# Check status
.\run.ps1 status

# View logs
.\run.ps1 logs
```

### 4. Test Endpoints

```bash
# Health check
curl http://localhost:8082/healthz

# Metrics
curl http://localhost:8082/metrics
```

## Docker Commands

```bash
# Build and start
docker compose up --build -d

# View logs
docker compose logs -f teltubby

# Worker logs
docker compose logs -f mtworker

# Stop services
docker compose down

# Restart
docker compose restart
```

## Project Structure

```
teltubby/
├── bot/           # Telegram bot service with enhanced UX
├── db/            # Deduplication database (SQLite)
├── ingest/        # Message ingestion pipeline with album handling
├── metrics/       # Prometheus metrics collection
├── mtproto/       # MTProto worker and client (large files)
├── queue/         # RabbitMQ job manager
├── quota/         # Storage quota management and monitoring
├── runtime/       # Configuration and logging setup
├── storage/       # S3/MinIO client wrapper
├── utils/         # Utility functions (slugging, telemetry formatting)
├── web/           # Health and metrics server
└── main.py        # Application entry point
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | Required |
| `TELEGRAM_WHITELIST_IDS` | Comma-separated curator IDs | Required |
| `S3_ENDPOINT` | MinIO/S3 endpoint URL | Required |
| `S3_BUCKET` | Storage bucket name | Required |
| `ALBUM_AGGREGATION_WINDOW_SECONDS` | Album collection window | 2 |
| `HEALTH_PORT` | Health server port | 8081 |
| `BIND_HEALTH_LOCALHOST_ONLY` | Bind to localhost only | true |

### Docker Configuration

- **Port Mapping**: Health endpoint exposed on port 8082 (configurable via `HOST_HEALTH_PORT`)
- **Volumes**: SQLite database stored in Docker volume `teltubby_db`
- **Restart Policy**: `unless-stopped` for production stability

## Key Features

### Rich Telemetry
- Emoji-rich formatted acknowledgments for better readability
- Detailed processing status with file counts, sizes, and deduplication info
- Specific error messages with actionable information

### Album Handling
- Configurable aggregation window (default: 2 seconds)
- Pre-validation of all album items before processing
- Prevents partial album failures

### Enhanced User Experience
- Real-time typing indicators during processing
- Visual status indicators for quota and processing status
- Comprehensive error handling with specific failure reasons
- **Mobile-optimized UI** with one-click action commands
- **Single-response policy** eliminating redundant messages

### Quota Management
- Real-time bucket usage monitoring
- Automatic ingestion pause at 100% capacity
- Clear status reporting via `/quota` command

### MTProto Integration (Large Files)
- **Automatic file size detection** via proactive Bot API calls
- **Smart routing** based on actual file accessibility
- **Persistent job queue** with RabbitMQ and dead-letter exchange
- **Complete job lifecycle** management (PENDING → PROCESSING → COMPLETED/FAILED)
- **Enhanced mobile UX** with inline action shortcuts

## Development

### PowerShell Scripts

- `.\run.ps1` - Main development script
- `.\setup-git.ps1` - Git repository setup
- `.\push-to-github.ps1` - Push to GitHub

### Available Commands

```powershell
.\run.ps1 up          # Start services
.\run.ps1 down        # Stop services
.\run.ps1 restart     # Restart services
.\run.ps1 logs        # View logs
.\run.ps1 status      # Show status
.\run.ps1 rebuild     # Rebuild and restart
```

## Bot Commands

### For Curators
- `/start` - Welcome message with usage instructions
- `/help` - **Comprehensive command reference** with examples
- `/status` - Current bot mode and storage usage
- `/quota` - Storage quota status with visual indicators

### For Administrators
- `/db_maint` - Run database maintenance (VACUUM)
- `/mode` - Show current operation mode
- `/queue` - **Enhanced job listing** with per-job action shortcuts
- `/jobs <job_id>` - **Rich job details** with inline retry/cancel commands
- `/retry <job_id>` - **Enhanced job retry** with status updates
- `/cancel <job_id>` - **Enhanced job cancellation** with confirmation
- `/mtcode <code>` - Submit MTProto verification code
- `/mtpass <password>` - Submit MTProto 2FA password
- `/mtstatus` - **Complete worker monitoring** with authentication status

## Enhanced User Experience

### Mobile-Optimized Interface
The bot now provides **emoji-rich, mobile-friendly messages** with **one-tap action commands**:

**Job Queued Confirmation:**
```
✅ Job Queued 📥

• 7725b89b-dd25-4fa8-9468-c18aa42f36f8
  🔎 /jobs 7725b89b-dd25-4fa8-9468-c18aa42f36f8  
  🔁 /retry 7725b89b-dd25-4fa8-9468-c18aa42f36f8  
  🛑 /cancel 7725b89b-dd25-4fa8-9468-c18aa42f36f8
```

**Enhanced Queue Output:**
```
📥 Recent Jobs

• abc-123-def [PENDING] prio=4
  🔎 /jobs abc-123-def  🔁 /retry abc-123-def  🛑 /cancel abc-123-def
```

### Single-Response Policy
- **No redundant messages** - single, concise job confirmation
- **Immediate action availability** - inline shortcuts for all operations
- **Clean, professional interface** - optimized for mobile users

## Troubleshooting

### Common Issues

1. **Container Cycling**: Ensure all required dependencies are in requirements.txt
2. **Port Conflicts**: Change `HOST_HEALTH_PORT` if 8082 is already in use
3. **Health Endpoint Unreachable**: Set `BIND_HEALTH_LOCALHOST_ONLY=false` in `.env`
4. **Album Processing Issues**: Check `ALBUM_AGGREGATION_WINDOW_SECONDS` setting
5. **Large Files Stay Queued**: Ensure `mtworker` is running; check RabbitMQ queue depth and worker logs
6. **MTProto Login**: When the worker starts, Telegram will send a login code to your user account. Send it to the bot via `/mtcode 12345`. If 2FA is enabled, also send `/mtpass your_password`. Flood-wait delays may apply.

### Debug Commands

```bash
# Check container status
docker ps

# View container logs
docker logs teltubby

# Check health endpoint
curl http://localhost:8082/healthz

# Test metrics
curl http://localhost:8082/metrics
```

## Implementation Status

### ✅ Fully Implemented
- **Core Bot Functionality**: 100% complete
- **Storage & Deduplication**: 100% complete
- **User Experience**: 100% complete with enhanced mobile UX
- **Monitoring & Health**: 100% complete
- **Configuration & Deployment**: 100% complete
- **MTProto Integration**: 100% complete and production-ready

### 🎯 Production Ready
The current implementation is **fully production-ready** with:
- **Hybrid architecture** combining Bot API (≤50MB) and MTProto (>50MB)
- **Seamless user experience** maintained through enhanced bot interface
- **Robust job processing** with persistent queues and recovery
- **Enhanced mobile UX** optimized for one-handed operation
- **Complete admin controls** accessible to all whitelisted users
- **Professional monitoring** with health checks and metrics

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions:
- Check the troubleshooting section
- Review the logs using `.\run.ps1 logs`
- Open an issue on GitHub

---

**teltubby - Professional Telegram Media Archival System with MTProto Integration**


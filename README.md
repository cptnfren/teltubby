# teltubby

A Python 3.12 Telegram archival bot that ingests forwarded/copied DMs from whitelisted curators, saves media and metadata to MinIO (S3-compatible) storage with deduplication, and provides comprehensive monitoring.

## Features

- **Telegram Bot Integration**: Accepts forwarded/copied DMs from whitelisted curators
- **MinIO/S3 Storage**: Secure cloud storage with deterministic filenames
- **Deduplication**: Prevents duplicate file storage using file_unique_id + SHA-256
- **Health Monitoring**: `/healthz` and `/metrics` endpoints for monitoring
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

# Health Server
HEALTH_PORT=8081
BIND_HEALTH_LOCALHOST_ONLY=false
```

### 3. Start Services

```powershell
# Start the service
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
docker-compose up --build -d

# View logs
docker-compose logs -f teltubby

# Stop services
docker-compose down

# Restart
docker-compose restart
```

## Project Structure

```
teltubby/
├── bot/           # Telegram bot service
├── db/            # Deduplication database
├── ingest/        # Message ingestion pipeline
├── metrics/       # Prometheus metrics
├── quota/         # Storage quota management
├── runtime/       # Configuration and logging
├── storage/       # S3/MinIO client
├── utils/         # Utility functions
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
| `HEALTH_PORT` | Health server port | 8081 |
| `BIND_HEALTH_LOCALHOST_ONLY` | Bind to localhost only | false |

### Docker Configuration

- **Port Mapping**: Health endpoint exposed on port 8082 (configurable via `HOST_HEALTH_PORT`)
- **Volumes**: SQLite database stored in Docker volume `teltubby_db`
- **Restart Policy**: `unless-stopped` for production stability

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

## Troubleshooting

### Common Issues

1. **Container Cycling**: Ensure `python-telegram-bot[rate-limiter]` is in requirements.txt
2. **Port Conflicts**: Change `HOST_HEALTH_PORT` if 8082 is already in use
3. **Health Endpoint Unreachable**: Set `BIND_HEALTH_LOCALHOST_ONLY=false` in `.env`

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


# Git commit script for teltubby project
Write-Host "Committing teltubby project to git..." -ForegroundColor Green

# Check git status
Write-Host "`nGit status:" -ForegroundColor Yellow
git status

# Add all files
Write-Host "`nAdding all files..." -ForegroundColor Yellow
git add .

# Create commit
Write-Host "`nCreating commit..." -ForegroundColor Yellow
git commit -m "Initial commit: teltubby Telegram archival bot with Docker fixes

- Complete Telegram bot implementation for archiving forwarded/copied DMs
- MinIO/S3 storage integration with deduplication
- Health and metrics endpoints (/healthz, /metrics)
- Docker containerization with Ubuntu 24.04 + Python 3.12
- Fixed Docker container cycling issue:
  * Added python-telegram-bot[rate-limiter] dependency
  * Resolved port conflict (8081 -> 8082)
  * Fixed health server binding (0.0.0.0 vs 127.0.0.1)
  * Fixed metrics endpoint content-type charset issue
- Comprehensive configuration management (.env + env.local.ps1)
- PowerShell run script for Windows development workflow
- Full documentation and requirements specification"

# Show commit log
Write-Host "`nCommit log:" -ForegroundColor Yellow
git log --oneline

Write-Host "`nCommit completed successfully!" -ForegroundColor Green

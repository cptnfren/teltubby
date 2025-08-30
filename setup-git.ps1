# Git and GitHub setup script for teltubby project
Write-Host "Setting up Git and GitHub for teltubby project..." -ForegroundColor Green

# Check if git is available
try {
    $gitVersion = git --version
    Write-Host "Git version: $gitVersion" -ForegroundColor Green
} catch {
    Write-Host "Git is not available. Please install Git first." -ForegroundColor Red
    exit 1
}

# Initialize git repository (if not already done)
if (-not (Test-Path ".git")) {
    Write-Host "`nInitializing git repository..." -ForegroundColor Yellow
    git init
} else {
    Write-Host "`nGit repository already initialized." -ForegroundColor Green
}

# Add all files
Write-Host "`nAdding all files to git..." -ForegroundColor Yellow
git add .

# Check status
Write-Host "`nGit status:" -ForegroundColor Yellow
git status

# Create initial commit
Write-Host "`nCreating initial commit..." -ForegroundColor Yellow
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

Write-Host "`n`n=== GITHUB SETUP INSTRUCTIONS ===" -ForegroundColor Cyan
Write-Host "1. Go to GitHub.com and create a new repository named 'teltubby'" -ForegroundColor White
Write-Host "2. Copy the repository URL (e.g., https://github.com/yourusername/teltubby.git)" -ForegroundColor White
Write-Host "3. Run the following commands:" -ForegroundColor White
Write-Host "   git remote add origin https://github.com/yourusername/teltubby.git" -ForegroundColor Yellow
Write-Host "   git branch -M main" -ForegroundColor Yellow
Write-Host "   git push -u origin main" -ForegroundColor Yellow
Write-Host "`n4. Or run: .\push-to-github.ps1" -ForegroundColor White

Write-Host "`nGit setup completed successfully!" -ForegroundColor Green

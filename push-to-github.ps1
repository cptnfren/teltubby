# Push teltubby project to GitHub
Write-Host "Pushing teltubby project to GitHub..." -ForegroundColor Green

# Check if remote origin is configured
$remoteOrigin = git remote get-url origin 2>$null
if (-not $remoteOrigin) {
    Write-Host "`nNo remote origin configured. Please run the setup first:" -ForegroundColor Red
    Write-Host "1. Create a repository on GitHub.com" -ForegroundColor White
    Write-Host "2. Run: git remote add origin https://github.com/yourusername/teltubby.git" -ForegroundColor Yellow
    Write-Host "3. Then run this script again" -ForegroundColor White
    exit 1
}

Write-Host "Remote origin: $remoteOrigin" -ForegroundColor Green

# Check current branch
$currentBranch = git branch --show-current
Write-Host "Current branch: $currentBranch" -ForegroundColor Green

# Rename branch to main if needed
if ($currentBranch -ne "main") {
    Write-Host "`nRenaming branch to 'main'..." -ForegroundColor Yellow
    git branch -M main
}

# Push to GitHub
Write-Host "`nPushing to GitHub..." -ForegroundColor Yellow
git push -u origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSuccessfully pushed to GitHub!" -ForegroundColor Green
    Write-Host "Repository URL: $remoteOrigin" -ForegroundColor Cyan
} else {
    Write-Host "`nFailed to push to GitHub. Please check your credentials and try again." -ForegroundColor Red
}

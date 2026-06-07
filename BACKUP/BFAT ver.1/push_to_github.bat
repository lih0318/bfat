@echo off
cd /d "%~dp0"
echo Checking .env is ignored...
git check-ignore backend/.env 2>nul && echo [OK] backend/.env is ignored || echo [WARN] Add backend/.env to .gitignore
echo.
if not exist .git (
  git init
  git add .
  git status
  echo.
  set /p confirm="Review above. Is backend/.env NOT listed? (y/n): "
  if /i not "%confirm%"=="y" exit /b 1
  git commit -m "Initial commit: Binance Futures Auto Trader"
  git branch -M main
  git remote add origin https://github.com/lih0318/bfat.git
  git push -u origin main
) else (
  git add .
  git status
  git commit -m "Update" 2>nul || echo No changes to commit.
  git remote get-url origin 2>nul || git remote add origin https://github.com/lih0318/bfat.git
  git push -u origin main
)
pause

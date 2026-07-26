@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM setup_github.bat — ONE-TIME: create the GitHub repo from this folder and
REM push the source. After this, use git_release.bat for every build.
REM
REM Requires: git and gh (GitHub CLI) on PATH.
REM   1. Install git:  https://git-scm.com/download/win
REM   2. Install gh:   https://cli.github.com/
REM   3. Run once:     gh auth login   (pick GitHub.com, HTTPS, login in browser)
REM ─────────────────────────────────────────────────────────────────────────
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set /p REPO=Repo name (e.g. richmor_config):
if "!REPO!"=="" ( echo Repo name required. & pause & exit /b 1 )

set VIS=--private
set /p PUB=Make it public? (y/N):
if /i "!PUB!"=="y" set VIS=--public

echo.
echo === Initializing git (respects .gitignore whitelist) ===
if not exist ".git" git init -b main
git add .
git commit -m "Initial commit: Richmor MDVR config app" 2>nul

echo.
echo === Creating GitHub repo and pushing ===
gh repo create "!REPO!" !VIS! --source=. --remote=origin --push
if errorlevel 1 (
    echo.
    echo repo create/push failed. If the repo already exists, add the remote manually:
    echo    git remote add origin https://github.com/^<you^>/!REPO!.git
    echo    git push -u origin main
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done. Repo created and source pushed.
echo  The first build is already running on GitHub.
echo  From now on, just run  git_release.bat  to build + download.
echo ============================================================
pause
endlocal

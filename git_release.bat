@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM git_release.bat — one-click: commit + push -> GitHub builds all OS binaries ->
REM download them into .\downloads\.
REM
REM Produces: RichmorConfig-win64.exe, -win32.exe, -linux, -macos-arm64
REM Requires: git + gh on PATH, and `gh auth login` done once (setup_github.bat).
REM ─────────────────────────────────────────────────────────────────────────
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set /p MSG=Commit message (Enter for "Update"):
if "!MSG!"=="" set MSG=Update

echo.
echo === [1/4] Committing and pushing ===
git add .
git commit -m "!MSG!"
if errorlevel 1 echo   (nothing new to commit — will just re-check the latest run)
git push
if errorlevel 1 ( echo Push failed. & pause & exit /b 1 )

echo.
echo === [2/4] Waiting for GitHub to register the run ===
timeout /t 6 /nobreak >nul

echo.
echo === [3/4] Watching build (5-15 min across all OSes) ===
for /f %%i in ('gh run list --limit 1 --json databaseId --jq ".[0].databaseId"') do set RUN_ID=%%i
for /f %%r in ('gh repo view --json nameWithOwner --jq ".nameWithOwner"') do set REPO=%%r
echo Run ID: !RUN_ID!
echo Web view: https://github.com/!REPO!/actions/runs/!RUN_ID!
gh run watch !RUN_ID! --exit-status --interval 10
if errorlevel 1 (
    echo.
    echo One or more jobs failed ^(Windows is the important one^) — check the web view above.
    echo Downloading whatever succeeded anyway...
)

echo.
echo === [4/4] Downloading artifacts to .\git_dist\ ===
if exist git_dist rmdir /s /q git_dist
gh run download !RUN_ID! --dir git_dist
if errorlevel 1 ( echo Download failed. & pause & exit /b 1 )

REM gh puts each artifact in its own subfolder — flatten so the binaries sit
REM directly in git_dist\ (no folder-named-like-the-exe nesting).
for /d %%d in ("git_dist\*") do (
    for %%f in ("%%d\*") do move /y "%%f" "git_dist\" >nul
    rmdir /s /q "%%d"
)

echo.
echo ============================================================
echo  Done. Binaries are in:  %CD%\git_dist
echo ============================================================
dir /b git_dist
echo.
pause
endlocal

@echo off
setlocal

cd /d "%~dp0"

set "COMPOSE="
docker compose version >nul 2>&1
if not errorlevel 1 (
  set "COMPOSE=docker compose"
) else (
  docker-compose version >nul 2>&1
  if errorlevel 1 (
    echo Docker Compose was not found. Install Docker Desktop or docker-compose first.
    exit /b 1
  )
  set "COMPOSE=docker-compose"
)

set "NEXUS_ENABLE_DEMO_MODE=true"
set "NEXUS_DEMO_HEARTBEAT_INTERVAL_SECONDS=6"
set "NEXUS_DEMO_RECOVERY_DELAY_SECONDS=5"

echo Starting NexusCore in live demo mode...
%COMPOSE% up --build -d
if errorlevel 1 (
  echo Failed to start the stack.
  exit /b 1
)

echo Waiting for the dashboard on http://127.0.0.1:8000/ ...
for /l %%I in (1,1,60) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $response = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/' -TimeoutSec 3; if ($response.StatusCode -ge 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto ready
  timeout /t 2 /nobreak >nul
)

echo NexusCore did not become ready in time.
echo Check the containers with: %COMPOSE% logs
exit /b 1

:ready
start "" "http://127.0.0.1:8000/"

echo Injecting a live CPU storm demo so you can see self-healing immediately...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $body = @{ scenario = 'cpu' } | ConvertTo-Json -Compress; Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/demo/chaos' -ContentType 'application/json' -Body $body | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo Automatic demo injection did not complete. You can still use the Chaos Simulator tab manually.
)

echo.
echo NexusCore is running.
echo Dashboard: http://127.0.0.1:8000/
echo Open the Chaos Simulator tab to trigger real backend healing actions.
echo To stop everything later, run: %COMPOSE% down

endlocal

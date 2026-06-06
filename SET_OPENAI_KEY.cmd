@echo off
setlocal
cd /d D:\data\krita-guide-agent
powershell -NoProfile -ExecutionPolicy Bypass -Command "$key = Read-Host 'Paste your OpenAI API key'; if ([string]::IsNullOrWhiteSpace($key)) { Write-Host 'No key entered.'; exit 1 }; $envPath = 'D:\data\krita-guide-agent\.env'; $lines = @('OPENAI_API_KEY=' + $key.Trim(), 'OPENAI_MODEL=gpt-5.4-mini', 'PORT=8788', 'KRITA_PATH=C:\Program Files\Krita (x64)\bin\krita.exe', 'GUIDE_AGENT_KEEP_LATEST=20'); Set-Content -LiteralPath $envPath -Value $lines -Encoding UTF8; Write-Host 'Wrote ' $envPath"
echo.
echo Now restart the app:
echo   Stop-Process -Id ^<current server pid^>
echo   D:\data\krita-guide-agent\START_KRITA_GUIDE_AGENT.cmd
pause

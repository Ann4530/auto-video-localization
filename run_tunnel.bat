@echo off
REM Tao link web CONG KHAI (https://...trycloudflare.com) tro toi server local.
REM YEU CAU: da chay run_web.bat (port 8000) va da dat APP_PASSWORD trong .env.
REM Link doi moi lan chay (quick tunnel). Muon link CO DINH -> dung named tunnel
REM (can tai khoan Cloudflare + domain), xem WEB.md.
cd /d "%~dp0"
echo Dang tao link cong khai... (giu cua so nay mo de link song)
"%~dp0bin\cloudflared.exe" tunnel --url http://localhost:8000 --no-autoupdate
pause

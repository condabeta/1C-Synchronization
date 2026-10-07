@echo off
REM Download Dekomo's images, on their own, until none are left.
REM
REM Double-click this file (or run it) and leave the window open. It keeps going
REM after Claude is closed. Safe to stop any time - closing the window pauses it,
REM and running it again resumes from where it stopped (a photo is recorded only
REM once it is on disk). Progress is written to D:\projects\1C\dekomo_images.log.

cd /d D:\projects\1C\project
title Dekomo images - download

:loop
echo [%date% %time%] starting a pass...
python scripts\download_images.py --supplier dekomo --workers 10 --delay 0.05 >> D:\projects\1C\dekomo_images.log 2>&1

REM Stop when nothing is left, otherwise wait a moment and go round again (this
REM also recovers from a network drop mid-run).
python scripts\_images_remaining.py dekomo
if errorlevel 1 (
    timeout /t 15 /nobreak >nul
    goto loop
)

echo.
echo All Dekomo images downloaded.
pause

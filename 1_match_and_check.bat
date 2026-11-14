@echo off
REM ---- ld2singe step 1: scan both videos, line them up, and make the check sheets.
REM ---- Nothing is built yet. Edit these three lines to match your folders.
set "LD=C:\mame\timetrv.avi"
set "GAME=C:\Hypseus Singe\singe\Hologram"
set "OUT=C:\mame\ld2singe_work"

if exist "%~dp0ld2singe.exe" (
    "%~dp0ld2singe.exe" --ld "%LD%" --game "%GAME%" --out "%OUT%" --step match
) else (
    py "%~dp0ld2singe.py" --ld "%LD%" --game "%GAME%" --out "%OUT%" --step match
)
echo.
echo Done. Open "%OUT%\check" to look at the check sheets, and "%OUT%\report.csv" for the details.
pause

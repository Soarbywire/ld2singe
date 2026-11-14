@echo off
REM ---- ld2singe step 2: build the new game folder from the laserdisc.
REM ---- Use the same three settings as in 1_match_and_check.bat.
set "LD=C:\mame\timetrv.avi"
set "GAME=C:\Hypseus Singe\singe\Hologram"
set "OUT=C:\mame\ld2singe_work"

if exist "%~dp0ld2singe.exe" (
    "%~dp0ld2singe.exe" --ld "%LD%" --game "%GAME%" --out "%OUT%"
) else (
    py "%~dp0ld2singe.py" --ld "%LD%" --game "%GAME%" --out "%OUT%"
)
echo.
echo Done. The new game folder is "%OUT%\game". See README.md, step 5, for how to install it.
pause

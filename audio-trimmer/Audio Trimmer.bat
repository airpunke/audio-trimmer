@echo off
REM  Double-click this to start the audio trimmer.
cd /d "%~dp0"

set PY=
where py >nul 2>&1 && set PY=py
if "%PY%"=="" ( where python >nul 2>&1 && set PY=python )

if "%PY%"=="" (
  echo.
  echo   Python isn't installed, or this computer can't find it.
  echo.
  echo   Get it from https://www.python.org/downloads/ and run this again.
  echo   Tick "Add Python to PATH" during setup.
  echo.
  pause
  exit /b 1
)

cls
echo.
echo   Audio trimmer
echo.
echo   Leave this window open while you work.
echo   Closing it stops the trimmer.
echo.
%PY% trimmer.py
echo.
pause

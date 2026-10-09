@echo off
setlocal
cd /d "%~dp0"
title Building MiniSpectrum.exe

rem ---- find Python: PATH, the py launcher, or the Microsoft Store install
set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py"
)
if not defined PY (
  for /d %%D in ("%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.*") do (
    if exist "%%D\python.exe" set "PY=%%D\python.exe"
  )
)
if not defined PY (
  echo Couldn't find Python. Install it from python.org or the Microsoft Store, then run this again.
  pause
  exit /b 1
)
echo Using Python: %PY%
echo.

echo [1/2] Installing build tools...
"%PY%" -m pip install --upgrade pyinstaller numpy pyaudiowpatch || goto :failed

echo.
echo [2/2] Building MiniSpectrum.exe (takes a minute)...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name MiniSpectrum ^
  --icon mini_spectrum\icon.ico ^
  --add-data "mini_spectrum\icon.ico;mini_spectrum" ^
  --collect-all pyaudiowpatch ^
  run_mini_spectrum.py || goto :failed

echo.
echo Done! Your app is dist\MiniSpectrum.exe - that single file is all your friends need.
explorer dist
pause
exit /b 0

:failed
echo.
echo The build stopped with an error. Scroll up to see what went wrong.
pause
exit /b 1

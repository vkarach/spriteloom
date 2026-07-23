@echo off
set DEST=%APPDATA%\Aseprite\extensions\spriteloom
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%~dp0plugin\*.*" "%DEST%\" >nul
echo Installed to %DEST%. Restart Aseprite.
pause

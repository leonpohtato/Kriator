@echo off
setlocal
set SRC=D:\data\krita-guide-agent\app\krita_live_plugin
set DEST=%APPDATA%\krita\pykrita
if not exist "%DEST%" mkdir "%DEST%"
xcopy /E /I /Y "%SRC%\krita_guide_live" "%DEST%\krita_guide_live"
copy /Y "%SRC%\krita_guide_live.desktop" "%DEST%\krita_guide_live.desktop"
echo.
echo Installed Krita Guide Live Coach to:
echo   %DEST%
echo.
echo Restart Krita, then enable it:
echo   Settings ^> Configure Krita ^> Python Plugin Manager ^> Krita Guide Live Coach
echo Then restart Krita again and open the docker:
echo   Settings ^> Dockers ^> Krita Guide Live Coach
pause

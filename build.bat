@echo off
echo Building NetSpeed...

python -m PyInstaller ^
  --noconsole ^
  --clean ^
  --onefile ^
  --icon=resource\netspeed.ico ^
  --add-data "resource//netspeed@2x.png;resource" ^
  --name NetSpeed ^
  netspeed.py

if %errorlevel% neq 0 (
    echo Build failed!
    pause
    exit /b %errorlevel%
)

del NetSpeed.spec
rmdir /s /q build

echo Done! Output in dist\NetSpeed.exe
pause
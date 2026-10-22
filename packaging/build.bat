@echo off
rem Build the Windows application into dist\SensorTrackingStudio.
rem Then run packaging\install.ps1 to put it on the Desktop and Start menu.
cd /d "%~dp0.."
pyinstaller --noconfirm --clean packaging\SensorTrackingStudio.spec
pause

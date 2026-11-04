@echo off
setlocal EnableExtensions DisableDelayedExpansion

title Xingc USB Fastboot Diagnostic
echo ========================================
echo Xingc USB Fastboot Diagnostic
echo Time: %date% %time%
echo ========================================
echo.

set "FASTBOOT=%~dp0..\platform-tools\fastboot.exe"
if not exist "%FASTBOOT%" set "FASTBOOT=fastboot.exe"

echo [1] fastboot devices
"%FASTBOOT%" devices 2>&1
echo.

set "SERIAL="
for /f "tokens=1,2" %%S in ('"%FASTBOOT%" devices 2^>nul') do if /i "%%T"=="fastboot" if not defined SERIAL set "SERIAL=%%S"

if defined SERIAL (
    echo [2] fastboot variables for serial: %SERIAL%
    "%FASTBOOT%" -s "%SERIAL%" getvar is-userspace 2>&1
    "%FASTBOOT%" -s "%SERIAL%" getvar product 2>&1
    "%FASTBOOT%" -s "%SERIAL%" getvar current-slot 2>&1
) else (
    echo [2] No fastboot serial returned; getvar skipped.
)
echo.

echo [3] Windows USB/PnP identity and driver-facing fields
where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo PowerShell not found; falling back to pnputil.
    pnputil /enum-devices /connected /class USB
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'USB\\VID_' -or $_.Name -match '(?i)fastboot|bootloader|android' } | Select-Object Name,Status,PNPDeviceID,Service,Manufacturer,ConfigManagerErrorCode | Format-List"
)
echo.
echo Copy all output above, especially USB\VID_xxxx^&PID_yyyy, PNPDeviceID and Service.
pause
endlocal

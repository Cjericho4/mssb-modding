@rem MSSB_SHOT_CMD for the self-test on Windows: save the window as %SHOT_DIR%\<name>.png
@pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0shot.ps1" %1

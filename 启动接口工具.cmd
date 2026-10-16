@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ROLE_READER_PY="
where py >nul 2>nul
if not errorlevel 1 set "ROLE_READER_PY=py -3"
if not defined ROLE_READER_PY (
  where python >nul 2>nul
  if not errorlevel 1 set "ROLE_READER_PY=python"
)
if not defined ROLE_READER_PY if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set ROLE_READER_PY="%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined ROLE_READER_PY (
  echo 需要 Python 3.10 或更新版本。请安装 Python 后重新运行。
  pause
  exit /b 1
)
%ROLE_READER_PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  %ROLE_READER_PY% -m pip install -r requirements.txt
  if errorlevel 1 goto finished
)
%ROLE_READER_PY% api_server.py
:finished
pause

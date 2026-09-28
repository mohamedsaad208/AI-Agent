@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
title AI Code Engineer
pushd "%~dp0"
if errorlevel 1 goto location_error

if not exist "agent.py" goto missing_files
if not exist "launcher.py" goto missing_files

if not exist ".venv\Scripts\python.exe" goto try_py
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
".venv\Scripts\python.exe" launcher.py
goto finished

:try_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py -3 launcher.py
goto finished

:try_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
python launcher.py
goto finished

:finished
set "AGENT_EXIT=%ERRORLEVEL%"
if "%AGENT_EXIT%"=="0" goto close
echo.
echo The launcher stopped with an error. Read the message above.
pause
:close
popd
exit /b %AGENT_EXIT%

:missing_python
echo Python 3.11 or newer was not found. Install Python and enable Add to PATH.
goto failure
:missing_files
echo Keep Run-Agent.bat and launcher.py next to agent.py.
:failure
pause
popd
exit /b 1
:location_error
echo Cannot open the application folder.
pause
exit /b 1

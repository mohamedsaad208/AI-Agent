@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist "desktop.pyw" goto missing_files
if not exist ".venv\Scripts\pythonw.exe" goto try_py
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.pyw"
goto close
:try_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
where pyw >nul 2>&1
if errorlevel 1 goto py_console
start "" pyw -3 "%~dp0desktop.pyw"
goto close
:py_console
py -3 "%~dp0desktop.pyw"
goto close
:try_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
where pythonw >nul 2>&1
if errorlevel 1 goto python_console
start "" pythonw "%~dp0desktop.pyw"
goto close
:python_console
python "%~dp0desktop.pyw"
goto close
:missing_python
echo Python 3.11 or newer is required. Tkinter is only used as a fallback.
echo Install Python with Tcl/Tk and Add to PATH enabled.
pause
popd
exit /b 1
:missing_files
echo Keep Run-Agent.bat next to desktop.pyw and the src folder.
pause
popd
exit /b 1
:close
popd
exit /b 0

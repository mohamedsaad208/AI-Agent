@echo off
setlocal
title AI Code Engineer - Tests

REM Run from wherever this file lives, so the caller's CWD never matters. The tests insert
REM src/ themselves; this script changes no interpreter setting (no PYTHONUTF8, no chcp),
REM because the workspace suite asserts on the platform's own text encoding.
pushd "%~dp0"
if errorlevel 1 goto location_error
if not exist "tests" goto missing_tests

REM The project's own interpreter is 3.11, so 3.11 is named rather than "py -3": on a box with
REM 3.13 installed py -3 picks 3.13, and the suite counts subtests differently there. Order is
REM the same as Run-Agent-CLI.bat apart from that - project venv first, then PATH python.
if not exist ".venv\Scripts\python.exe" goto try_pinned
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
".venv\Scripts\python.exe" -m unittest discover -s tests
goto finished

:try_python
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto try_pinned
python -m unittest discover -s tests
goto finished

:try_pinned
py -3.11 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
py -3.11 -m unittest discover -s tests
goto finished

:finished
set "TESTS_EXIT=%ERRORLEVEL%"
popd
if not "%TESTS_EXIT%"=="0" echo The suite failed with exit code %TESTS_EXIT%.
exit /b %TESTS_EXIT%

:missing_tests
echo No tests folder next to this script. Keep run-tests.cmd in the project root.
popd
exit /b 1

:missing_python
echo Python 3.11 was not found. Install it and enable Add to PATH, or create .venv here.
popd
exit /b 1

:location_error
echo Cannot open the application folder.
exit /b 1

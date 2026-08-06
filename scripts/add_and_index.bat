@echo off
REM Quick Document Add & Reindex Script for Windows
REM Usage: add_and_index.bat [source_directory] [test_query]
REM Example: add_and_index.bat ..\new_docs "What is machine learning?"

setlocal enabledelayedexpansion

REM Get project root
set PROJECT_ROOT=%~dp0..
cd /d %PROJECT_ROOT%

REM Activate virtual environment
echo.
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Check if source directory provided
if "%1"=="" (
    echo.
    echo Usage: add_and_index.bat [source_directory] [test_query]
    echo Example: add_and_index.bat ..\new_docs "What is in the new docs?"
    echo.
    exit /b 1
)

REM Set variables
set SOURCE_DIR=%1
set TEST_QUERY=%2

REM Build command
set CMD=python scripts\quick_add_and_index.py --source "%SOURCE_DIR%"

if not "%TEST_QUERY%"=="" (
    set CMD=!CMD! --test "%TEST_QUERY%"
)

REM Run
echo.
echo Running: !CMD!
echo.

python scripts\quick_add_and_index.py --source "%SOURCE_DIR%"

if not "%TEST_QUERY%"=="" (
    python scripts\quick_add_and_index.py --source "%SOURCE_DIR%" --test "%TEST_QUERY%"
) else (
    python scripts\quick_add_and_index.py --source "%SOURCE_DIR%" --no-test
)

echo.
echo Done!
echo.
pause

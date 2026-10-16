@echo off
if exist "Matchups.xlsx" (
    echo Removing old Matchups.xlsx...
    del /f /q "Matchups.xlsx"
) else (
    echo No existing Matchups.xlsx found.
)

echo Running matchup checker...
python -m lol_matchup_checker gen --lane jg --champs elise j4

echo Waiting 3 seconds...
timeout /t 3 /nobreak >nul

echo Opening Matchups.xlsx...
start "" "Matchups.xlsx"
@echo off
rem MusicDashboard scheduled sync: save new Spotify plays and the podcast episode playing now,
rem look up a few more tracks from the privacy export (until data\import.done exists),
rem then rebuild the site's data (docs/data/listening.js). Output is appended to data\sync.log.
rem For Windows Task Scheduler, repeat every 30 minutes (no "Start in" needed). To run without a window:
rem Program = conhost.exe, Arguments = --headless "C:\code\MusicDashboard\sync.bat"
rem The import is capped per run so it spreads the app's daily catalog-lookup quota over the day.
cd /d "%~dp0"
set PY="C:\Users\zamdr\AppData\Local\Programs\Python\Python313\python.exe"
>> data\sync.log echo === %DATE% %TIME%
%PY% musicdashboard.py sync %* >> data\sync.log 2>&1
set SYNC_RESULT=%ERRORLEVEL%
if not exist data\import.done %PY% musicdashboard.py import --limit 20>> data\sync.log 2>&1
rem Genres for artists not looked up yet, from MusicBrainz (about a request a second, no daily cap).
%PY% musicdashboard.py genres --limit 15 >> data\sync.log 2>&1
rem Once a day: re-saves playlists\ (BACKUP_PLAYLISTS, default My Favs) and commits it, if the playlist changed.
%PY% musicdashboard.py backup >> data\sync.log 2>&1
%PY% musicdashboard.py build >> data\sync.log 2>&1 || exit /b %ERRORLEVEL%
rem Commits and pushes docs\data\ for GitHub Pages, only when there's more than a new timestamp.
%PY% musicdashboard.py publish >> data\sync.log 2>&1
rem Sends at most once a day (first run from REPORT_HOUR), and only once SMTP settings are in .env.
%PY% musicdashboard.py report >> data\sync.log 2>&1
exit /b %SYNC_RESULT%

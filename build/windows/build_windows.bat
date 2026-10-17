@echo off
REM Simpl'd — native Windows build (run from the project root on a Windows PC).
REM Produces dist\Simpld\Simpld.exe (onedir). Optionally compiles the installer
REM with Inno Setup (ISCC on PATH) into dist\Simpld-Setup-<version>.exe.
setlocal
cd /d "%~dp0..\.."

python -m pip install -r requirements.txt || exit /b 1
python -m pip install -r build\windows\build_requirements.txt || exit /b 1
python -m PyInstaller --noconfirm build\windows\simpld.spec || exit /b 1

REM Fail the build if Qt platform plugins are missing — this is the
REM "no Qt platform plugin could be initialized" error at startup.
if not exist "dist\Simpld\_internal\PySide6\plugins\platforms\qwindows.dll" (
  echo FATAL: qwindows.dll missing in dist\Simpld\_internal\PySide6\plugins\platforms\ — Qt plugins were not bundled. 1>&2
  echo Check PySide6 install and build\windows\simpld.spec plugin section. 1>&2
  exit /b 1
)
echo Qt plugins OK: qwindows.dll present.

REM Version from factory/version.py - single source of truth for the app,
REM the exe metadata and the installer. Read it at top level, before the
REM if block below is parsed, so %APPVER% expands to the real value.
for /f "delims=" %%v in ('python -c "from factory.version import VERSION; print(VERSION)"') do set APPVER=%%v

where iscc >nul 2>nul
if %errorlevel%==0 (
  if not defined APPVER (
    echo FATAL: could not read the version from factory/version.py 1>&2
    exit /b 1
  )
  iscc /DMyAppVersion=%APPVER% build\windows\simpld.iss || exit /b 1
  echo BUILD OK: dist\Simpld\Simpld.exe + installer in dist\
) else (
  echo BUILD OK: dist\Simpld\Simpld.exe
  echo Inno Setup (iscc) not on PATH — installer step skipped.
)

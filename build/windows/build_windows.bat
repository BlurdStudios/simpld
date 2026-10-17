@echo off
REM Simpl'd — native Windows build (run from the project root on a Windows PC).
REM Produces dist\Simpld\Simpld.exe (onedir). Optionally compiles the installer
REM with Inno Setup (ISCC on PATH) into dist\Simpld-Setup-1.0.0.exe.
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

where iscc >nul 2>nul
if %errorlevel%==0 (
  iscc build\windows\simpld.iss || exit /b 1
  echo BUILD OK: dist\Simpld\Simpld.exe + installer in dist\
) else (
  echo BUILD OK: dist\Simpld\Simpld.exe
  echo Inno Setup (iscc) not on PATH — installer step skipped.
)

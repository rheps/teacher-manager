@echo off
rem Teacher Manager installed-app launcher. Keep lines before chcp ASCII-only.
chcp 65001 >nul
title Teacher Manager 시작 도우미
setlocal

set "INSTALL_DIR="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\BigSilverEduLab\TeacherManager" /v InstallDir 2^>nul ^| findstr /i "InstallDir"') do set "INSTALL_DIR=%%B"
if not defined INSTALL_DIR goto :repair

set "APP_EXE=%INSTALL_DIR%\TeacherManager.exe"
set "TOOLS_EXE=%INSTALL_DIR%\TeacherManagerTools.exe"
if not exist "%APP_EXE%" goto :repair

if /i "%~1"=="/check-gws" goto :check_gws

echo 프로그램을 여는 중이에요...
start "" "%APP_EXE%"
endlocal
exit /b 0

:check_gws
if not exist "%TOOLS_EXE%" goto :repair
"%TOOLS_EXE%" gws --version
set "RESULT=%ERRORLEVEL%"
endlocal & exit /b %RESULT%

:repair
echo.
echo Teacher Manager 설치 위치를 찾지 못했어요.
echo 받아 둔 설치 파일을 다시 실행하고 복구 또는 재설치를 진행해 주세요.
echo.
endlocal
exit /b 20

@echo off
rem Teacher Manager bootstrap. Keep every line up to chcp ASCII-only.
chcp 65001 >nul
title Teacher Manager 시작 도우미
setlocal

set "PYEXE="
set "PYOPT="

rem ---- Python 찾기: py 런처 -> python 순서. 스토어 가짜 python은 오류코드라 걸러진다 ----
py -3 --version >nul 2>&1
if not errorlevel 1 (set "PYEXE=py" & set "PYOPT=-3")
if defined PYEXE goto :have_python

python --version >nul 2>&1
if not errorlevel 1 set "PYEXE=python"
if defined PYEXE goto :have_python

rem ---- Python 설치: 키보드 입력 없이 ----
where winget >nul 2>&1
if errorlevel 1 goto :no_winget

echo [1/4] Python을 설치하는 중이에요... 1~2분 걸려요.
winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :fail_python

rem 설치 직후엔 이 창의 PATH가 낡아서 설치 경로를 직접 찾는다
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
  goto :have_python
)
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
  if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
if not defined PYEXE goto :fail_python

:have_python
"%PYEXE%" %PYOPT% -c "import webview, pypdf" >nul 2>&1
if not errorlevel 1 goto :have_webview

echo [2/4] 화면 부품과 문서 읽기 도구를 설치하는 중이에요... 1분 정도예요.
"%PYEXE%" %PYOPT% -m pip install pywebview pypdf --quiet --disable-pip-version-check
if errorlevel 1 goto :fail_pip

:have_webview
node --version >nul 2>&1
if not errorlevel 1 goto :have_node
where winget >nul 2>&1
if errorlevel 1 goto :launch_dashboard
echo [3/4] 구글 연결 준비 프로그램을 설치하는 중이에요...
winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"

:have_node
:launch_dashboard
rem 창 없는 런처(pythonw/pyw)로 대시보드를 떼어내 실행하고 이 창은 닫는다.
set "PYWEXE=%PYEXE%"
if /I "%PYEXE%"=="py" set "PYWEXE=pyw"
if /I "%PYEXE%"=="python" set "PYWEXE=pythonw"
if /I "%PYEXE:~-10%"=="python.exe" set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if /I "%PYWEXE:~-11%"=="pythonw.exe" if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"
echo [4/4] 프로그램을 여는 중이에요...
start "" "%PYWEXE%" %PYOPT% "%~dp0scripts\dashboard"
endlocal
exit /b 0

:no_winget
echo.
echo Python 설치 도구 winget이 이 컴퓨터에 없어요.
echo 아래 주소에서 Python을 직접 설치한 뒤 이 파일을 다시 눌러 주세요.
echo.
echo     https://www.python.org/downloads/
echo.
echo 설치 화면 맨 아래 "Add python.exe to PATH"를 꼭 체크해 주세요.
exit /b 10

:fail_python
echo.
echo Python 설치가 끝나지 않았어요. 잠시 후 이 파일을 다시 눌러 주세요.
echo 계속 안 되면 아래 주소에서 직접 설치할 수 있어요. 학교 관리 PC라면 IT 담당자에게 문의해 주세요.
echo.
echo     https://www.python.org/downloads/
echo.
exit /b 11

:fail_pip
echo.
echo 화면 부품과 문서 읽기 도구 설치가 실패했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.
echo.
exit /b 12

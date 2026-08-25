Teacher Task Manager 처음 설정

설정 폴더 전체 경로:
{CONFIG_DIR}

이 안내 파일 전체 경로:
{README_PATH}

이 폴더는 개인 설정 폴더입니다.
스킬을 업데이트하거나 다시 설치해도 이 폴더의 개인 설정 파일은 지워지지 않습니다.

지원하는 컴퓨터:
- 64비트 프로그램 실행이 가능한 Windows 10/11을 지원합니다.
- x64 Windows와 x64 프로그램 실행을 지원하는 Windows 11 ARM64가 이 범위에 들어갑니다.
- 32비트 Windows는 Setup이 설치 시작 전에 지원되지 않는다는 안내를 보이고 멈춥니다.
- Windows 11 ARM64 실제 설치는 아직 현장 미검증입니다.

설치 전에 준비할 계정:
1. 교육디지털원패스에서 교직원으로 가입합니다.
   https://edupass.neisplus.kr/
2. 경기도교육청 교육용 클라우드 지원시스템에 가입합니다.
   https://www.goedu.kr/
3. 경기도교육청 클라우드 지원시스템 내 서비스인 Google Workspace 사용을 별도로 신청해
   선생님 @goedu.kr 계정을 준비합니다.
   https://www.goedu.kr/bbs/3/view/63

Google Chat을 쓸 학급의 학생도 교육디지털원패스와 경기도교육청 교육용 클라우드 지원시스템에
가입하고 Google Workspace 신청을 마쳐 학생 @goedu.kr 계정을 준비해야 합니다.
학생 준비 도움말: https://www.goedu.kr/bbs/2/view/55
선생님이 https://chat.google.com/ 에서 학생 계정을 직접 초대하여 학급 단체톡방을
준비해 주세요. Teacher Manager는 학생 가입·초대·삭제를 자동으로 하지 않습니다.

Teacher Manager는 경기도교육청 소속 교사 전용입니다. @goedu.kr 주소만으로 교사와
학생의 신분까지 구별할 수는 없습니다. 학생에게 프로그램이나 출결 Sheet 편집 권한을
주지 마세요. 학생은 Google Chat 학급 단체톡방에만 초대합니다. 출결 Sheet의 설정 탭에는
Chat 연결값이 있으므로 학생이나 믿을 수 없는 공동 편집자와 공유하지 마세요.

설정 질문보다 먼저 GWS를 준비합니다.
GWS는 계속 켜두는 프로그램이 아니라, 필요할 때 한 번 실행하고 끝나는 명령어입니다.
선생님 컴퓨터에 Python, Node.js, npm, gws를 따로 설치하지 않습니다.
Python과 Google Workspace CLI는 공식 Setup 안의 확인된 파일을 사용합니다.
Node.js는 AI 비서 연결을 실제로 시작할 때만 Teacher Manager 전용 폴더에 준비됩니다.

1. Setup이 현재 사용자 폴더에 만든 안전한 명령 파일을 찾습니다. 이 파일은 실제 설치 폴더의 TeacherManagerTools.exe만 실행합니다:
   $gws = Join-Path $env:LOCALAPPDATA "BigSilverEduLab\TeacherManager\bin\teacher-manager-gws.cmd"
   & $gws --help

   teacher-manager-gws.cmd가 없거나 설치 위치 오류를 알리면 PATH에서 이름으로 찾은 다른 GWS나 npm을 대신 쓰지
   않습니다. 받아 둔 공식 Setup을 다시 실행해 복구 또는 재설치합니다.

2. Teacher Manager 설정 화면에서 세 줄을 따로 확인합니다:
   - Google Workspace CLI: 준비됨
   - Google 로그인 준비: 준비됨
   - Google 계정: 로그인됨 또는 로그인 필요

   Google 로그인 준비가 없거나 서로 다른 OAuth 클라이언트 준비 파일이 충돌하면 로그인하지 않습니다.
   화면의 설치 파일 복구 안내를 따르고, 계속되면 오류 문구만 배포 담당자에게 알립니다.
   OAuth 값, 파일 내용, 개인 폴더 경로는 보내지 않습니다. 개발자용 OAuth 준비 명령, 클라우드 개발 도구 설치,
   keyring 방식 강제 변경으로 우회하지 않습니다.

3. 로그인 전에 현재 상태를 확인합니다:
   & $gws auth status

   준비가 되어 있고 아직 로그인하지 않았다면 Google Calendar, Tasks, Drive, Sheets, Docs,
   Apps Script 권한으로 로그인합니다:
   & $gws auth login --scopes "email,profile,openid,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/tasks,https://www.googleapis.com/auth/script.projects,https://www.googleapis.com/auth/script.deployments,https://www.googleapis.com/auth/script.container.ui"

   로그인은 브라우저에서 사용자가 직접 마무리해야 합니다.

   새 정본이나 연결 확인 표시가 없는 정본에서만 출석부를 열어
   출결 업무 자동화 -> AI 출결 입력 연결 확인을 한 번 누릅니다.
   연결 확인 표시가 정상이면 나중 업데이트는 기존 감지기 하나를 그대로 유지합니다.

   [중요] 로그인 계정은 반드시 @goedu.kr로 끝나는 경기도교육청 공식 계정이어야 합니다.
   & $gws auth status의 user 값이 @goedu.kr로 끝나지 않으면 이 계정으로는 진행할 수
   없습니다. 교육디지털원패스 및 경기도교육청 클라우드 지원시스템 계정으로 다시
   로그인해 주세요. (@goedu.kr)

4. 로그인 뒤 캘린더와 Tasks 목록을 확인합니다:
   & $gws calendar calendarList list --params '{"maxResults":250}' --format table
   & $gws tasks tasklists list --format table

표준 Google 공간:
- 캘린더는 업무, 학사일정 2개를 기본으로 씁니다.
- 휴일, 생일, 기본 캘린더, 학급 이름 캘린더는 자동화 대상으로 쓰지 않습니다.
- Tasks는 조종례시 담임학급 안내사항 목록 하나에 업무 목록을 더해 2개를 씁니다.
  (출결 미제출 확인 할 일도 조종례시 담임학급 안내사항 목록에 함께 등록됩니다.)
- 같은 이름의 목록이 이미 있으면 새로 만들지 않고 기존 목록을 연결합니다.
- Google이 기본 Tasks 목록 삭제를 막으면 그 목록 이름을 조종례시 담임학급 안내사항으로 바꿔 사용합니다.

직접 고칠 파일:
- {PROFILE_CSV}
- {TIMETABLE_XLSX}

담임이면 teacher-profile.csv의 담임여부를 예로 적고 담임학년, 담임반, 담임안내Tasks목록ID를 채웁니다.
비담임이면 담임여부를 아니오로 적고 담임학년, 담임반, 담임안내Tasks목록ID는 비워둡니다.

점심종료시간은 점심시간이 끝나고 5교시가 시작하는 시각입니다.

월요일마지막교시부터 금요일마지막교시까지의 값은 그날 몇 교시까지 수업이 있는지를 나타냅니다.
종례는 따로 적지 않습니다. 마지막 교시가 끝난 바로 뒤 10분을 그 요일의 종례 시간으로 자동 계산합니다.

직접 고치지 말 파일:
- {GENERATED_JSON}

weekly-timetable.xlsx는 칸에 글자가 있으면 바쁜 시간, 빈칸이면 공강입니다.
weekly-timetable.xlsx에는 1교시부터 7교시까지만 적습니다.
이 파일은 칸 속성을 텍스트로 잡아두어서 2-1처럼 적어도 02-01 같은 날짜로 바뀌지 않습니다.
점심시간은 시간표 파일에 적지 않습니다.
조회와 종례는 teacher-profile.csv 값으로 계산합니다.
예: "2-3 역사", "회의", "상담"처럼 무엇을 적어도 바쁜 시간으로 봅니다.

Google Chat 발송을 쓰려면 설치 때 개발자가 배포한 중앙 발송소 주소가 시트 설정에 들어가 있어야 합니다.
공개 배포판은 설치 명령에 --central-chat-sender-url 값을 넣거나 CENTRAL_CHAT_SENDER_URL 환경값을 넣어 배포합니다.
선생님은 시트 메뉴에서 Google Chat 최초 발송 연결하기를 한 번 눌러 주세요.
이 작업은 Google Cloud 설정이 아니라 선생님 계정의 발송 허락입니다.

캘린더와 Tasks ID를 모를 때는 에이전트에게 "Teacher Manager 캘린더 목록 보여줘"라고 말하세요.

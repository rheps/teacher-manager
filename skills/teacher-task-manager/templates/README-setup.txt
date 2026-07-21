Teacher Task Manager 처음 설정

설정 폴더 전체 경로:
{CONFIG_DIR}

이 안내 파일 전체 경로:
{README_PATH}

이 폴더는 개인 설정 폴더입니다.
스킬을 업데이트하거나 다시 설치해도 이 폴더의 개인 설정 파일은 지워지지 않습니다.

설정 질문보다 먼저 GWS를 준비합니다.
GWS는 계속 켜두는 프로그램이 아니라, 필요할 때 한 번 실행하고 끝나는 명령어입니다.
에이전트는 안내만 하지 않고, 설치가 필요하면 먼저 동의를 받은 뒤 직접 실행합니다.
전역 설치나 Node.js 설치처럼 컴퓨터 상태를 바꾸는 작업은 동의 없이 실행하지 않습니다.

1. Node.js와 npm 확인:
   node --version
   npm --version

2. 둘 중 하나라도 안 되면 에이전트가 이렇게 묻습니다:
   Node.js가 없어 GWS CLI를 설치할 수 없습니다. 제가 백그라운드에서 Node.js LTS를 설치해도 될까요?

   동의하면 에이전트가 직접 실행합니다:
   winget install OpenJS.NodeJS.LTS

3. gws CLI가 없으면 에이전트가 이렇게 묻습니다:
   GWS CLI가 없어 Google Calendar, Tasks, Drive, Sheets, Docs, Apps Script를 연결할 수 없습니다. 제가 백그라운드에서 GWS CLI를 전역 설치해도 될까요?

   동의하면 에이전트가 직접 전역 설치합니다:
   npm install -g @googleworkspace/cli
   gws --help

   Windows PowerShell에서 실행 정책 때문에 gws가 막히면 gws.cmd --help로 실행합니다.
   아래 명령들도 같은 상황에서는 gws.cmd로 바꿔 쓰면 됩니다.

4. 터미널에서 gws를 쓸 때는 먼저 키 보관 방식을 앱과 같게 고정합니다
   (안 하면 앱과 터미널이 서로 로그아웃시키는 문제가 재발합니다):
   $env:GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND = "file"

   그리고 로그인 전에 준비 상태를 먼저 확인합니다:
   gws auth status

   "No OAuth client configured" 같은 안내가 나오면 OAuth 클라이언트 준비가 먼저입니다.
   가장 쉬운 길: 배포자에게 gws-oauth-client.json 파일을 받아
   내 폴더의 TeacherTaskManager 폴더(예: C:\Users\<사용자>\TeacherTaskManager)에
   넣으면 앱과 도우미가 자동으로 사용합니다.
   파일이 없으면 에이전트가 gws auth setup을 안내합니다 (gcloud CLI 필요).
   gcloud가 없으면 gws auth setup --help가 알려주는 공식 대안을 따릅니다.

   준비가 되어 있으면 Google Calendar, Tasks, Drive, Sheets, Docs, Apps Script 권한으로 로그인:
   gws auth login --scopes "email,profile,openid,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/tasks,https://www.googleapis.com/auth/script.projects,https://www.googleapis.com/auth/script.deployments,https://www.googleapis.com/auth/script.container.ui"

   로그인은 브라우저에서 사용자가 직접 마무리해야 합니다.

   [중요] 로그인 계정은 반드시 @goedu.kr로 끝나는 경기도교육청 공식 계정이어야 합니다.
   개인 Gmail 계정으로 로그인하면 학교 공유 캘린더, Chat 학급 스페이스, Apps Script 배포가
   막히거나 학교 정책에 맞지 않을 수 있습니다. gws auth status의 user 값이 @goedu.kr로
   끝나지 않으면 @goedu.kr 계정으로 다시 로그인하세요.

5. 로그인 뒤 캘린더와 Tasks 목록 확인:
   gws calendar calendarList list --params '{"maxResults":250}' --format table
   gws tasks tasklists list --format table

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

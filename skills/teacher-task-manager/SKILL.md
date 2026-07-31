---
name: teacher-task-manager
description: "Use when a Korean teacher first installs or uses teacher Google automation, sets up GWS, teacher profile, timetable, Google Calendar/Tasks, registers official notices or classroom reminders, or installs/operates attendance-report automation with Google Sheets, Docs, Drive, Apps Script, and Tasks."
---

# Teacher Task Manager

한국 교사용 Google 자동화 대표 스킬. 처음 설정, 교사 업무 캘린더/Tasks 등록, 출결신고서 자동화를 모두 이 스킬 하나에서 처리한다.

## 단일 진입점

사용자에게 별도 보조 스킬 이름을 안내하지 않는다. GitHub에서 설치하면 사용자가 보는 스킬은 `teacher-task-manager` 하나다.

요청을 받으면 내부에서 이렇게 판단한다.

| 상황 | 처리 |
| --- | --- |
| 처음 설치, 처음 사용, 설정 없음, GWS 없음 | 아래 `처음 시작 프로토콜`을 실행 |
| 공문, 메모, 학급 안내, 학사일정, 업무 등록 | 아래 `워크플로우`로 Calendar/Tasks에 등록 |
| 출결신고서, 결석신고서, 출결 자동화, Apps Script 설치 | 아래 `출결신고서 자동화` 절차 실행 |
| 쪽지 보내기, 종례 쪽지, 단체방/개인 DM 발송, 안내장 | 아래 `Google Chat 쪽지 발송` 절차 실행 — 쪽지 원본은 시트의 쪽지 대장 |
| Brity 메신저, 브리티, 메신저 자동 등록, 단축키 등록 도우미 | 아래 `Brity 메시지 캘린더 연결 도우미` 절차 실행 |
| 설정 대시보드, 설정 화면, 설정 고치기, 대시보드 | 아래 `설정 대시보드`를 실행 |

## 핵심 원칙

1. **설정 우선**: 처음 사용하거나 설정이 비어 있으면 업무 등록을 하지 말고 먼저 설정을 끝낸다.
2. **즉시 실행**: 설정이 끝난 뒤에는 분석 완료 시 사용자 확인 없이 바로 gws CLI 명령 실행
3. **분석 비공개**: TaskObject, scheduling_meta 등 내부 분석은 응답에 출력하지 않음
4. **캘린더-Tasks 독립**: 캘린더(`target_system`)와 Tasks(`need_homeroom_task`)는 **완전히 별개 축**으로 동시 등록 가능
5. **MCP 미사용**: 모든 Google API 호출은 gws CLI(`@googleworkspace/cli`) 명령으로 실행
6. **사용자는 하나만 기억**: 처음 설정, 업무 등록, 출결 자동화 순서를 사용자에게 떠넘기지 말고 이 스킬이 알아서 라우팅한다.

## 설정 대시보드

처음 설치와 설정 변경의 기본 경로다. 사용자가 `설정 대시보드`, `설정 화면`, `설정 고치기`를 말하거나 처음 설치를 시작하면 아래 명령을 실행해 화면으로 안내한다.

```powershell
python "<이 스킬 폴더>\scripts\dashboard"
```

이 화면은 웹 부품(pywebview)을 쓴다. 배포판 프로그램에는 포함돼 있고, 소스로 실행할 때만 한 번 설치가 필요하다: `pip install pywebview`. 실행 후 창이 안 뜨면 `python -m brity_bridge doctor`와 콘솔 안내문을 확인한다.

선생님께 배포할 때의 기본 실행은 스킬 폴더의 `시작하기.vbs`(화면에는 `시작하기`로 보일 수 있음) 더블클릭이다. 이 파일이 Python과 화면 부품(pywebview)을 화면 뒤에서 자동으로 준비한 뒤 위 명령을 대신 실행한다 — 설치 과정에서 키보드로 답할 일은 없다.

- 홈은 `내 정보`, `시간표`, `연결`, `설정` 네 카드로 구성되고, 처음 설정은 시작하기 → 내 정보 → 하루 일과 → 시간표 → 설정 → 연결 → 마무리 순서로 진행한다. 문제가 있으면 홈 카드에 `확인 필요`와 `N/M 정상` 숫자가 붙고, 실제 원인은 해당 화면의 입력칸 아래에 정확한 문장으로 보인다.
- `내 정보`에는 `학년도` 고르기 칸이 있다. 새로 설치하면 오늘 날짜 기준 학년도가 기본으로 골라져 있고, 이 값이 출결 탭 출석부의 학년도 잠김/풀림을 판정하는 기준이 된다.
- Node.js는 처음 실행에서 자동으로 준비하며, 컴퓨터 준비·Google Workspace CLI 설치·Google 로그인·로그아웃은 모두 설정 화면에서 관리한다. 다른 계정으로 바꾸려면 설정에서 로그아웃한 뒤 다시 로그인한다 (`gws auth logout`). 설정의 `다시 점검` 하나가 컴퓨터·GWS·로그인·Calendar/Tasks 목록을 함께 다시 확인한다.
- 연결 화면은 `Brity 메신저`·`출결`·`AI 에이전트` 세 탭이다(AI 에이전트 탭은 공개 준비 중 안내만 보인다). 메신저 탭은 Calendar·Tasks·Gemini API key를, 출결 탭은 출결 Google Sheet·Docs·Tasks 준비 상태를 각각 저장한다.
- 출결 탭은 Google Sheet·Docs·Tasks·Chat 네 칸이고, 각 칸이 자기 상태와 자기 단추만 갖는다.
  Sheet 칸에 `열기`와 `새 시트에 출석부 만들기`(내 정보의 학년도와 연결된 출석부의 학년도가
  다를 때만 눌린다 — 누르면 `{학년도}학년도 {학년}학년 {반}반 출석부`를 만들어 바로 연다), Docs 칸에 `서식 열기`, Chat 칸에 `▶ 연결방법`과
  `학급 단톡방`이 들어간다. Tasks는 특정 목록으로 가는 브라우저 주소가 없어 여는 단추가 없다.
- 학급 단톡방이 하나도 없으면 Chat 칸 안에서 이름을 정해 바로 만들 수 있다. 만들면 곧바로 학급
  단톡방으로 골라 둔다. 학생 초대는 프로그램이 못 하므로 선생님이 Google Chat에서 하신다.
  학교가 방 만들기를 막아 두었으면 손으로 만드는 순서를 보여준다.
- 출결 자료는 별도 설치 버튼 없이 출결 탭의 첫 `저장하기` 또는 처음 설정 마지막 `모두 저장하고 적용`에서 자동으로 준비된다. 기존 설치 기록이 있으면 새 Sheet를 만들지 않고 그대로 재사용하며, 저장을 반복해도 자료가 중복 생성되지 않는다.
- 마무리 탭의 `모두 저장하고 적용` 하나가 저장 → 설정 파서 → 출결 자동 준비 → 도우미 재시작을 순서대로 실행한다.
- 대시보드도 정본 파일은 같다: `teacher-profile.csv`와 `weekly-timetable.xlsx`에 쓰고 설정 파서로 `profile.generated.json`을 만든다. 아래 대화 절차와 섞어 써도 안전하다.
- 문제가 생기면 `python -m brity_bridge doctor` 출력을 받아 원인 항목부터 해결한다.

GUI를 쓸 수 없는 환경에서만 아래 `처음 시작 프로토콜`의 대화 절차를 처음부터 진행한다.

## 처음 시작 프로토콜

업무를 등록하기 전에 항상 GWS 준비와 개인 설정을 먼저 확인한다. 처음 설치의 기본 경로는 위 `설정 대시보드`이고, 아래 대화 절차는 GUI를 쓸 수 없을 때의 예비 경로다 — 정본 파일이 같아 두 경로를 섞어도 안전하다.

처음 설치 직후이거나 Google Drive/Sheets/Script까지 함께 준비해야 하면 이 스킬 안의 처음 설정 절차를 먼저 실행한다. 설정이 끝난 뒤에만 업무 등록이나 출결 자동화를 진행한다.

**설정 폴더**: 실행 중인 컴퓨터에서 `Path.home() / "TeacherTaskManager"`로 계산한 전체 경로. Windows에서는 `C:\Users\<사용자이름>\TeacherTaskManager` 형태다. 사용자에게 말할 때는 절대 `홈 폴더`라고 줄여 말하지 말고, 그 컴퓨터에서 계산한 실제 전체 경로를 쓴다.

**사용자가 직접 고치는 파일**:
- `teacher-profile.csv`
- `weekly-timetable.xlsx`

**자동 생성 파일**:
- `profile.generated.json`

설정 파일은 스킬 폴더 안이 아니라 위 설정 폴더에 둔다. 스킬을 업데이트하거나 다시 설치해도 이 폴더 안의 개인 설정 파일은 지워지지 않는다.

### GWS 먼저 설치하고 로그인하기

처음 설정에서는 선생님 이름, 학교, 담임, 시간표 질문보다 GWS 준비를 먼저 끝낸다. GWS 준비가 끝나기 전에는 이름, 학교, 담임, 시간표 질문을 시작하지 않는다.

이 스킬은 MCP나 커넥터를 쓰지 않는다. `gws`는 계속 켜두는 프로그램이 아니라, 필요할 때 한 번 실행하고 끝나는 명령어다.

안내만 하지 말고, 설치가 필요한지 먼저 확인한 뒤 필요한 설치는 에이전트가 직접 처리한다. 단, 전역 설치나 Node.js 설치처럼 컴퓨터 상태를 바꾸는 작업은 먼저 짧게 동의를 받는다. 동의 없이 전역 설치를 실행하지 않는다.

먼저 Node.js와 npm이 있는지 확인한다.

```powershell
node --version
npm --version
```

둘 중 하나라도 안 되면 이렇게 묻는다.

> Node.js가 없어 GWS CLI를 설치할 수 없습니다. 제가 백그라운드에서 Node.js LTS를 설치해도 될까요?

동의하면 에이전트가 직접 실행한다.

```powershell
winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
```

`--silent --accept-package-agreements --accept-source-agreements`는 winget이 영어로 라이선스 동의를 묻는 프롬프트에서 멈추지 않게 한다. 설치 뒤에는 PowerShell이나 터미널을 새로 열고 다시 `node --version`, `npm --version`을 확인한다.

그다음 gws CLI가 있는지 확인한다.

```powershell
gws --help
```

Windows PowerShell에서 실행 정책 때문에 `gws`가 막히면 `gws.cmd --help`로 실행한다. 아래 모든 `gws` 명령도 같은 상황에서는 `gws.cmd`로 바꿔 쓸 수 있다.

없으면 이렇게 묻는다.

> GWS CLI가 없어 Google Calendar, Tasks, Drive, Sheets, Docs, Apps Script를 연결할 수 없습니다. 제가 백그라운드에서 GWS CLI를 전역 설치해도 될까요?

동의하면 에이전트가 직접 실행한다. gws CLI는 전역 설치로 설치한다.

```powershell
npm install -g @googleworkspace/cli
gws --help
```

터미널에서 gws를 실행할 때는 먼저 키 보관 방식을 앱과 같은 file 백엔드로 고정한다. 앱(대시보드·도우미)은 이 값을 고정해 쓰는데, 터미널이 기본 keyring을 쓰면 양쪽이 서로의 로그인 토큰을 지우는 수시 로그아웃이 재발한다.

```powershell
$env:GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND = "file"
```

로그인 명령을 실행하기 전에 로그인 준비 상태부터 확인한다. (새 터미널 창을 열었다면 위 환경 변수 설정부터 다시 실행한다.)

```powershell
gws auth status
```

- 이미 원하는 계정으로 로그인돼 있으면 다시 로그인하지 않는다.
- `No OAuth client configured` 같은 안내가 나오면 OAuth 클라이언트 준비가 먼저다. 에이전트가 사용자에게 "로그인 준비가 안 되어 있어 준비 단계를 먼저 진행하겠습니다"라고 알린 뒤 `gws auth setup`을 안내한다. `gws auth setup`은 gcloud CLI가 필요하다. gcloud가 없는 컴퓨터에서는 `gws auth setup --help`와 gws 공식 안내가 제시하는 대안을 그대로 안내하고, 추측으로 명령을 만들어내지 않는다.

준비가 되어 있으면 Google Calendar, Tasks, Drive, Sheets, Docs, Apps Script 권한으로 로그인한다. 이 단계도 명령은 에이전트가 실행하되, 로그인은 브라우저에서 사용자가 직접 마무리해야 한다. 브라우저가 열리면 선생님 계정으로 로그인하고 권한을 허용해야 한다.

```powershell
gws auth login --scopes "email,profile,openid,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/tasks,https://www.googleapis.com/auth/script.projects,https://www.googleapis.com/auth/script.deployments,https://www.googleapis.com/auth/script.container.ui"
```

여기서는 Calendar, Drive, Docs, Sheets, Tasks, Apps Script 설치와 실행에 필요한 권한만 로그인한다. Google Chat 직접 발송 권한은 Apps Script 로그인에 넣지 않는다. Chat 발송은 시트 메뉴의 `처음 한 번 설정하기 -> 처음 설정 한 번에 끝내기`가 부르는 `Google Chat 최초 발송 연결하기`에서 선생님 계정 허락을 따로 받고, 실제 발송은 중앙 발송기로 넘긴다.

**[중요] 로그인 계정은 반드시 `@goedu.kr` 계정이어야 한다.** 경기도교육청은 `@goedu.kr`로 끝나는 공식 계정에만 구글 워크스페이스(Google Workspace) 기능(Calendar 공유, Tasks, Docs, Apps Script, Chat 등)을 온전히 제공한다. 개인 Gmail 계정(`@gmail.com` 등)으로 로그인하면 학교 공유 캘린더 접근, Chat 학급 스페이스, Apps Script 배포 등이 막히거나 학교 정책에 맞지 않을 수 있다.

`gws auth status` 결과의 `user` 값을 반드시 확인한다. `@goedu.kr`로 끝나지 않으면 설정 질문으로 넘어가지 말고 아래처럼 안내한 뒤 다시 로그인한다.

> 지금 로그인된 계정은 `{user}`입니다. 이 스킬은 경기도교육청 공식 계정(예: 이름@goedu.kr)으로 로그인해야 캘린더 공유, Tasks, Chat 쪽지가 학교 계정 기준으로 정상 동작합니다. `@goedu.kr` 계정으로 다시 로그인해도 될까요?

동의하면 같은 scope로 `gws auth login`을 다시 실행하고, 브라우저 계정 선택 화면에서 `@goedu.kr` 계정을 고르도록 안내한다. 로그인이 끝나면 `gws auth status`로 `user` 값이 `@goedu.kr`로 끝나는지 다시 확인한 뒤에만 다음 단계로 넘어간다. 사용자가 개인 Gmail 계정으로 계속 진행하겠다고 명시적으로 말하면 그 결정은 존중하되, Chat 쪽지 등 일부 기능이 제한될 수 있다고 짧게 안내한다.

로그인 계정 확인이 끝난 뒤에만 캘린더와 Tasks 목록을 조회한다.

```powershell
gws calendar calendarList list --params '{"maxResults":250}' --format table
gws tasks tasklists list --format table
```

목록 조회가 성공하면 그때부터 설정 질문을 시작한다. 개인 업무 일정 캘린더, 학사일정 캘린더, 담임 안내 Tasks 목록은 사용자가 이름으로 고르게 하고, 실제 저장은 ID로 한다. 선택한 개인 업무용 캘린더와 학사일정 캘린더의 ID와 이름을 `teacher-profile.csv`에 저장한 뒤 파서를 다시 실행한다. 담임 교사일 때만 담임 안내 Tasks 목록도 고르게 한다. 비담임이면 담임 안내 Tasks 목록은 묻지 않는다.

ID는 선생님이 직접 적지 않는다. 에이전트가 `gws calendar calendarList list`와 `gws tasks tasklists list` 결과에서 이름 목록을 보여주고, 선생님이 이름을 고르면 에이전트가 해당 ID를 teacher-profile.csv에 대신 적는다.

### 표준 Google 공간

처음 설정과 기존 계정 정리는 아래 표준을 따른다. 사용자가 다른 이름을 명시하지 않으면 이 이름을 기본값으로 사용한다.

| 종류 | 표준 이름 | 용도 |
| --- | --- | --- |
| Calendar | 업무 | 선생님이 직접 처리할 개인 업무 일정 |
| Calendar | 학사일정 | 학교 전체 일정, 시험, 행사, 기간 일정 |
| Tasks | 조종례시 담임학급 안내사항 | 담임 학급에 조회/종례 때 전달할 안내 + 출결 미제출 확인 업무 |

캘린더는 업무와 학사일정 2개를 자동화 대상으로 삼는다. 휴일, 생일, 기본 캘린더, 학급 이름 캘린더는 자동화 대상으로 쓰지 않는다. 사용자가 기존 계정 정리를 요청하면 업무와 학사일정 외 캘린더는 가능한 경우 제거하고, Google이 제거를 막는 기본 캘린더는 목록에서 숨긴다.

Tasks는 `조종례시 담임학급 안내사항` 목록 하나로 담임 안내와 출결 미제출 확인을 함께 처리한다. Google이 기본 Tasks 목록 삭제를 막으면 그 기본 목록을 `조종례시 담임학급 안내사항`으로 이름을 바꿔 사용한다. 같은 이름의 목록이 이미 있으면 새로 만들지 말고 기존 목록을 연결한다.

출결 자동화를 함께 설치하면 미제출 할 일도 `조종례시 담임학급 안내사항` 목록에 등록한다 — 별도 출결 목록을 만들지 않는다. 예전 방식으로 설치된 시트는 대시보드가 출결 탭을 열 때 자동으로 이 목록으로 전환하고, 옛 `출결 미제출 확인` 목록은 안의 할 일 보호를 위해 지우지 않는다.

메신저 인쇄 자동감시처럼 스킬 밖에서 돌아가는 자동화도 개인 이름, 학교, 시간표, 캘린더 ID, Tasks ID를 하드코딩하지 않는다. 항상 설정 폴더의 `profile.generated.json`에서 읽은 최신 값을 사용하게 맞춘다.

### 설정이 없을 때

설정 폴더나 설정 파일이 없으면 업무 등록을 멈추고 아래 순서로 처리한다. 사용자에게 안내할 때는 실제 계산된 전체 경로를 포함한다.

1. 위 `GWS 먼저 설치하고 로그인하기`를 끝낸다.
2. 설정 폴더와 설정 견본을 만든다.
   ```bash
   python "<이 스킬 폴더>/scripts/setup_teacher_google_automation.py" --config-dir "$HOME/TeacherTaskManager" --init
   ```
3. Windows에서는 폴더를 바로 열어준다.
   ```powershell
   explorer "$HOME\TeacherTaskManager"
   ```
4. 사용자에게 아래 내용을 짧고 확실하게 안내한다.
   - 방금 열린 `TeacherTaskManager` 폴더가 개인 설정 폴더다.
   - 이 컴퓨터의 설정 폴더 전체 경로를 말한다. 예: `C:\Users\<사용자이름>\TeacherTaskManager`
   - 자세한 안내 파일 전체 경로를 말한다. 예: `C:\Users\<사용자이름>\TeacherTaskManager\README-setup.txt`
   - 스킬을 업데이트해도 이 폴더의 설정 파일은 사라지지 않는다.
   - `teacher-profile.csv`와 `weekly-timetable.xlsx`만 채우면 된다.
   - `profile.generated.json`은 자동 생성 파일이라 직접 고치지 않는다.
   - 설정 파일을 다 채운 뒤 다시 Teacher Manager를 부르면 자동으로 JSON을 만든다.
5. 이 상태에서는 캘린더/Tasks 등록을 하지 않는다.

### 설정 파일을 JSON으로 바꾸기

`profile.generated.json`이 없거나, `teacher-profile.csv` 또는 `weekly-timetable.xlsx`가 JSON보다 새로우면 먼저 파서를 실행한다.

```bash
python "<이 스킬 폴더>/scripts/parse_settings.py" --config-dir "$HOME/TeacherTaskManager"
```

파서가 실패하면 부족한 항목만 알려주고, 다시 `explorer "$HOME\TeacherTaskManager"`로 폴더를 열어준다. 이때도 설정 폴더와 `README-setup.txt`의 전체 경로를 함께 말한다.

### 설정 입력 규칙

`teacher-profile.csv`는 `항목,값` 형식이다. 학교급은 `초`, `중`, `고` 중 하나로 적는다. 초는 40분, 중은 45분, 고는 50분 수업으로 계산한다. 쉬는 시간은 항상 10분, 조회와 종례는 각각 10분으로 본다.

**견본 CSV에 기본값을 넣지 않는다.** 처음 설치할 때 만들어지는 `teacher-profile.csv`의 값 칸은 전부 비어 있어야 한다. 출근시간, 조회시작, 마지막 교시 같은 값을 미리 채워두면 선생님이 안 고친 견본 값이 진짜 설정인 것처럼 저장되는 사고가 난다. 모든 값은 선생님에게 직접 묻거나 선생님이 직접 적은 것만 쓴다. 질문할 때도 "견본에는 08:30으로 들어가 있는데" 같은 말로 기본값을 제시하지 않는다.

`담임여부`는 `예` 또는 `아니오`로 적는다. `예`이면 `담임학년`, `담임반`, `담임안내Tasks목록ID`가 필요하다. `아니오`이면 이 세 값은 비워둬도 되고, 담임 안내 Tasks 생성은 하지 않는다. `업무Tasks목록ID`/`업무Tasks목록이름`은 업무 체크리스트용으로 담임 여부와 무관하며, 비워두면 업무 일정은 캘린더에만 등록된다.

**점심시간(=5교시 시작)**: `점심종료시간`은 점심시간이 끝나고 5교시가 시작하는 시각이다. 1~4교시는 `1교시시작`에서 계산하지만 5교시 이후는 이 값이 있어야 계산된다. 처음 설정 질문에서 반드시 선생님에게 직접 묻는다. 빼먹거나 짐작으로 채우지 않는다.

**종례는 따로 묻지 않는다**: 요일별 마지막 교시만 물으면 그날 수업이 끝나는 시각이 자동으로 나온다. 마지막 교시가 끝난 바로 뒤 10분을 그 요일의 종례 시간으로 계산한다.

`weekly-timetable.xlsx`는 `교시,월,화,수,목,금` 형식이다. weekly-timetable.xlsx에는 1교시부터 7교시까지만 적는다. 이 파일은 칸 속성을 텍스트로 잡아두어서 2-1처럼 적어도 02-01 같은 날짜로 바뀌지 않는다. 점심시간은 시간표 파일에 적지 않는다. 조회와 종례는 teacher-profile.csv 값으로 계산한다. 칸에 어떤 글자라도 있으면 바쁜 시간이고, 빈칸이면 공강이다. 업무 배치는 마지막 교시 안에 있는 빈칸에만 넣는다.

시간표 표를 채팅에 입력하라고 하지 않는다. 시간표는 양이 많고 표 모양이라 채팅보다 파일에서 고치는 편이 안전하다. 사용자에게 weekly-timetable.xlsx의 전체 경로를 알려주고, 그 파일을 열어서 고치게 한다. 파일을 저장하고 돌아오라고 안내한다.

시간표 안내 예시:

> 시간표는 채팅에 적지 말고 이 파일에서 고쳐주세요: `C:\Users\<사용자이름>\TeacherTaskManager\weekly-timetable.xlsx`
> 1교시부터 7교시까지만 적으면 됩니다. 점심시간은 적지 않습니다.
> 이 파일은 2-1처럼 적어도 02-01 같은 날짜로 바뀌지 않게 만들어져 있습니다.
> 칸에 글자가 있으면 바쁜 시간, 빈칸이면 공강으로 봅니다. 저장하고 돌아오면 제가 자동으로 읽겠습니다.

### 처음 설정 질문 말투

첫 설정 질문은 가장 중요하다. 한국 선생님이 실제 화면을 떠올릴 수 있게 생활 언어로 풀어 묻는다. 짧은 전문용어만 던지지 않는다.

처음 설정 질문에서는 내부 설명을 덧붙이지 않는다. 예를 들어 "처음 설정에서는 원래 gws로 목록을 보여준다" 같은 말은 사용자에게 하지 않는다. 필요한 질문만 한 번에 하나씩 묻는다.

캘린더를 물을 때는 "업무 캘린더"라고만 묻지 않는다. 구글 캘린더에서 왼쪽 위 줄 3개짜리 메뉴를 누르면 `내 캘린더`에 여러 색상으로 보이는 캘린더들이 나온다고 설명하고, 그중 하나를 개인업무 일정을 등록할 캘린더로 고르게 한다. 학사일정도 같은 방식으로 학교 전체 일정이나 학사일정을 등록할 캘린더를 고르게 한다.

질문 예시 1:

> 구글 캘린더 왼쪽 위 줄 3개 메뉴를 누르면 `내 캘린더` 목록에 여러 색상으로 보이는 캘린더가 있습니다. 그중 선생님 개인업무 일정을 등록할 캘린더 이름은 무엇인가요?

질문 예시 2:

> 구글 캘린더 왼쪽 위 줄 3개 메뉴를 누르면 `내 캘린더` 목록에 여러 색상으로 보이는 캘린더가 있습니다. 그중 학교 전체 일정이나 학사일정을 등록할 캘린더 이름은 무엇인가요?

담임이면 이렇게 묻는다.

> 조종례 때 담임학급 안내사항으로 볼 Google Tasks 목록은 무엇인가요?

**점심시간이 끝나는 시각(=5교시 시작)도 반드시 묻는다.** 1교시 시작만 묻고 넘어가지 않는다. 종례는 따로 묻지 않는다 — 요일별 마지막 교시에서 자동 계산된다.

질문 예시 3 (점심시간):

> 점심시간은 몇 시에 끝나나요? (점심 끝나고 5교시가 시작하는 시각입니다)

## 출결신고서 자동화

사용자가 `출결신고서`, `결석신고서`, `출결 자동화`, `Apps Script`, `신고서 템플릿`을 말하면 이 섹션을 따른다.

먼저 개인 설정 JSON이 있어야 한다. 없으면 `처음 시작 프로토콜`을 끝낸다.

출결신고서 자동화도 같이 설치할지 먼저 묻는다.

> 출결신고서 자동화도 같이 설치할까요? 설치하면 결석신고서 DOCX 템플릿을 Google Docs로 자동 업로드하고, 출결 자동화 Google Sheet에 템플릿 문서 ID를 자동으로 적어둡니다. 선생님이 TEMPLATE_DOC_ID를 손으로 복사해서 붙여넣지 않아도 됩니다.

원하면 먼저 명령 모양을 확인한다.

```powershell
python "<이 스킬 폴더>\scripts\setup_teacher_google_automation.py" --config-dir "C:\Users\<사용자이름>\TeacherTaskManager" --install-attendance --dry-run
```

사용자가 실제 Google Docs, Google Sheets, Drive 폴더, Tasks 목록, Apps Script 프로젝트 생성을 승인하면 `--dry-run`을 빼고 실행한다.
공개 배포판에서 Google Chat 발송까지 켤 때는 개발자가 배포한 중앙 발송소 주소를 함께 넣는다.

```powershell
python "<이 스킬 폴더>\scripts\setup_teacher_google_automation.py" --config-dir "C:\Users\<사용자이름>\TeacherTaskManager" --install-attendance --central-chat-sender-url "<중앙 발송소 URL>"
```

설치가 끝나면 Google Sheet 링크와 Google Docs 템플릿 링크를 알려준다. 설치 도우미는 `attendance-install.generated.json`도 함께 만들어서, 나중에 LLM 흐름이 시트의 쪽지 대장을 바로 채울 수 있게 한다.

그 다음은 이 순서로 안내한다.

1. Google Sheet를 연다.
2. 상단 메뉴에 `출결 업무 자동화`와 `교육청 메신저 정리·발송`이 보일 때까지 기다린다.
3. 기본 시트(쪽지 대장·발송기록 포함)는 설치 도우미가 자동으로 준비한다. 설치 출력에 자동 준비가 막혔다는 안내가 나온 경우에만 `처음 한 번 설정하기 -> 처음 설정 한 번에 끝내기`를 한 번 실행하게 한다. 이 항목 하나가 기본 시트 점검, AI 출결 입력 켜기, Google Chat 최초 발송 연결, 학급 단톡방 고르기를 순서대로 하고 결과를 한 화면에 보여 준다. 이미 끝난 것은 건너뛰므로 여러 번 눌러도 안전하다.
4. `Google에서 확인하지 않은 앱`이 뜨면 시트 오류가 아니라 Google OAuth 검증 문제라고 설명한다. 개인 테스트는 고급 옵션으로 계속할 수 있지만, 공개 배포 전에는 OAuth 검증이 필요하다.
5. 시트가 준비돼도 이것은 기본 시트와 드롭다운을 준비한 것이다. 출결 자동화 전체가 끝났다고 말하지 않는다.
6. `학생명단` 시트를 채우게 한다. 열은 왼쪽부터 `번호`(A), `이름`(B), `번호+이름`(C), `학생 Google 이메일`(D) 4개다.
   - **선생님이 직접 채우는 열**
     - `번호`(A), `이름`(B): 나이스나 엑셀 명단에서 그대로 붙여넣으면 된다.
     - `학생 Google 이메일`(D): 개인 DM을 보낼 학생만 적는다. 이메일이 있으면 그 학생은 자동으로 개인 DM 대상이고, 없으면 발송에서 건너뛰고 발송기록에 남는다. 별도의 사용 여부(Y/N) 표시는 없다.
   - **자동으로 채워지는 열**
     - `번호+이름`(C): A열과 B열을 입력하는 즉시 자동 생성된다. 월별 시트 학생 드롭다운의 원본이다. 반대로 C열에 `3김민수`처럼 직접 적으면 번호/이름이 자동 분리된다 — 양방향 모두 동작한다.
   - DM 방(Space)은 발송할 때마다 이메일로 자동 연결되므로 시트에 Space ID를 적어둘 필요가 없다. 발송 이력은 `발송기록` 시트에 남는다.
7. 결석, 지각, 조퇴, 결과 내용은 해당 `월별 시트`에 입력하게 한다.
8. 신고서를 만들 때는 출결 행 하나를 선택하고 `출결 업무 자동화 -> 선택 행 출결신고서 Google Docs에 만들기`를 실행하게 한다.
9. 확인 업무를 등록할 때는 출결 행 하나를 선택하고 `출결 업무 자동화 -> 선택 행 미제출 서류 Google Tasks에 추가하기`를 실행하게 한다. 이 메뉴는 Tasks 등록을 처리한다.
10. 그 자리에서 바로 그 학생에게 개인톡까지 보내고 싶으면 출결 행을 선택하고 `출결 업무 자동화 -> 선택 행 미제출 서류 Google Chat 개인톡 보내기`를 실행한다. 출결 독촉은 월별 시트에서 바로 보내고, 결과도 월별 시트 맨 끝에서 확인하는 흐름으로 안내한다. 이 메뉴는 개인톡만 대상이다 — 출결 행 하나는 그 학생 개인 사안이라 `메신저 단체톡 내용`은 채우지 않는다.

메뉴 이름은 아래처럼 고정해서 안내한다. 상단 메뉴는 사전 세팅(`처음 한 번 설정하기`)과 실행(`출결 업무 자동화`, `교육청 메신저 정리·발송`)으로 갈라져 있다.
- `처음 한 번 설정하기`
- `출결 업무 자동화`
- `교육청 메신저 정리·발송`
- `처음 설정 한 번에 끝내기`
- `메신저 쪽지 내용 Google Chat으로 개인톡 보내기`
- `메신저 쪽지 내용 Google Chat으로 단체톡 보내기`

쪽지 발송은 새 Google Sheet 대장을 기본으로 설명한다.

- `메신저 개인톡 내용`과 `메신저 단체톡 내용`은 설치 때 자동으로 만들어진다. 열려면 시트 하단의 같은 이름 탭을 누르면 된다 (메뉴에 열기 항목은 두지 않는다 — 사용자 결정 2026-07-21).
- `메신저 개인톡 내용`에는 학생 한 명에게 보낼 일반 안내를 모은다. 출결 독촉은 여기로 쌓는다고 안내하지 말고, 월별 시트에서 바로 보내는 흐름으로 설명한다.
- `메신저 단체톡 내용`에는 학급 전체에게 보낼 내용을 모은다.
- 자동 분석으로 들어온 안내는 바로 보내지 말고 `확인필요` 상태로 대장에 저장한다.
- 선생님이 검토한 뒤 보낼 줄만 `대기` 상태로 바꾸면 된다. `제외`, `보냄`, `실패` 상태는 종례 발송 대상이 아니다.
- 정해진 값인 들어온 곳, 상태, 쪽지 종류는 직접 타이핑보다 선택 목록으로 고르게 한다.
- 종례 때는 `교육청 메신저 정리·발송 -> 메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기`를 실행한다. 이 메뉴는 오늘과 그 이전 날짜의 `대기` 줄을 모아 단체 쪽지는 한 번에, 개인 쪽지는 학생별로 묶어서 보낸다.

첫 메뉴만 실행하고 자동화가 완성됐다고 말하지 않는다. 샘플 신고서 문서가 만들어졌거나 사용자가 기본 세팅만 원한다고 명확히 말했을 때만 완료로 본다.

출결 자동화 자산은 이 스킬 안에 있다.

- `assets/attendance-workbook.xlsx`
- `assets/absence-report-template.docx`
- `assets/Code.gs`
- `assets/appsscript.json`
- `scripts/install_attendance_automation.py`

### 이미 쓰던 출결 시트가 있을 때

설치 도우미는 **아무것도 만들기 전에** 내 드라이브에서 `출결신고서 자동화`라는 이름의
시트를 먼저 찾는다. 그다음은 찾은 개수에 따라 갈린다.

| 찾은 개수 | 설치 도우미가 하는 일 |
| --- | --- |
| 0개 | 지금까지처럼 새로 만든다 |
| 1개 | 그 시트를 **그대로 이어 쓴다**. 아무것도 만들지 않고 시트에도 쓰지 않는다 |
| 2개 이상 | 어느 것인지 알 수 없으므로 멈추고 찾은 시트를 링크와 함께 보여준다 |

1개일 때 이어 쓰는 절차는 시트의 `설정` 탭에 이미 들어 있는
`TEMPLATE_DOC_ID`, `DEST_FOLDER_ID`, `TASK_LIST_ID`, `SCRIPT_ID`, `DEPLOYMENT_ID`를 읽어
로컬 설치 기록만 만드는 것이다. 다섯 값 중 하나라도 비어 있으면 멈추고 어느 값이
비었는지 이름을 알려 준다.

그 시트에 붙어 있는 Apps Script가 `release.json`의 `minimumAppsScriptVersion`보다
오래된 판이면 이어 쓰지 않고 멈춘다. 옛 판 스크립트를 그대로 몰고 가면 시트 모양이
어긋난다.

`설정` 탭을 덮어쓰지 않는 이유가 있다. 거기에 `CENTRAL_CHAT_SHEET_ID`와
`CENTRAL_CHAT_SHEET_SECRET`이 들어 있어서, 덮어쓰면 Google Chat 발송 등록이 끊어진다.

시트가 여러 개 나와 멈췄을 때는 어느 것이 쓰던 시트인지 사용자에게 물어본다. 마지막
수정 시각과 자료가 들어 있는지를 함께 보여주면 고르기 쉽다. 쓰지 않는 시트의 이름을
바꾸거나 휴지통으로 옮겨 하나만 남기면, 다시 실행했을 때 그 시트로 이어진다.

시트 ID를 직접 지정해서 연결해야 할 때만 아래를 쓴다. 이 절차도 시트에는 한 글자도
쓰지 않으며, 바꾸기 전 설치 기록을
`attendance-install.before-connect.generated.json`으로 한 번 남긴다.

```powershell
python -c "import sys; sys.path.insert(0, r'<이 스킬 폴더>\scripts'); from connect_existing_attendance_sheet import connect_existing_attendance_sheet; print(connect_existing_attendance_sheet(r'C:\Users\<사용자이름>\TeacherTaskManager', '<쓰던 시트 ID>', account='<gws auth status로 읽은 계정>'))"
```

### Apps Script ID 확인과 사본 시트 복구

Code.gs 반영, 버전 확인, Apps Script API 실행에 필요한 스크립트 ID는 이 순서로 찾는다. 사용자에게 ID를 먼저 물어보지 않는다.

1. 설정 폴더의 `attendance-install.generated.json`에서 `script_id`를 읽는다.
2. 없으면 Google Sheet `설정` 시트의 `SCRIPT_ID` 값을 읽는다 (`gws sheets spreadsheets values get`).
3. 둘 다 비어 있으면(수동 사본 시트 등) 그때만 사용자에게 한 번 요청한다: 시트에서 `확장 프로그램 -> Apps Script`를 열고 편집기 주소(URL)를 붙여넣어 달라고 안내한다. URL의 `/projects/`와 `/edit` 사이 문자열이 스크립트 ID다.
4. 3번으로 받은 ID는 곧바로 `설정` 시트의 `SCRIPT_ID`에 기록해서 같은 시트에 대해 다시 묻지 않는다.

시트를 사본으로 복사하면 Apps Script도 새 ID로 함께 복사되지만 설치 기록 파일은 남지 않는다. 위 3~4번이 그 복구 절차다. 설치 도우미와 `기본 시트/설정 점검`은 스크립트 ID를 `설정` 시트에 자동 기록한다.

선생님이 Drive에서 직접 복사해 만든 사본 시트는 새 시트로 본다. 원본 시트의 Google Chat 발송 연결이나 학급 단톡방 선택을 이어받은 것으로 안내하지 않는다. 원본 시트의 연결 상태를 이어받았다고 안내하지 않는다. 그런 사본에서는 `처음 한 번 설정하기 -> 처음 설정 한 번에 끝내기`로 새 발송 연결을 마치고, 학급 단톡방도 다시 고른다.

아래 `출결 사본으로 바꾸고 1행 AI 입력 켜기` 절차로 만든 사본은 예외다. 그 절차는 발송 연결을 사본으로 옮기는 단계를 포함한다.

### 출결 사본으로 바꾸고 1행 AI 입력 켜기

사용자가 `1행 AI 입력`, `문장으로 출결 입력`, `출결 사본으로 바꾸기`, `AI 출결 입력 켜기`를 말하면 이 절차를 따른다.

먼저 안전선을 지킨다.

- 원본 Google Sheet에는 쓰지 않는다. 쓰는 대상은 사본과 로컬 설치 기록뿐이다.
- Google이나 중앙 발송 서버의 답이 불분명하면 같은 요청을 자동으로 다시 보내지 않는다. 멈추고 사용자에게 지금 상태를 그대로 알린다.
- 어느 단계에서 멈춰도 기존 설치 기록과 원본 시트는 그대로 남는다.

시작 전에 `attendance-install.generated.json`이 있어야 하고, gws는 선생님 본인 학교 계정으로 로그인돼 있어야 한다. 현재 계정은 `gws auth status`로 확인한다.

**1단계 — 비공개 사본 하나 만들기**

```python
import sys
sys.path.insert(0, r"<이 스킬 폴더>\scripts")
from attendance_install_record import load_attendance_install_record
from prepare_attendance_copy import prepare_attendance_copy

config_dir = r"C:\Users\<사용자이름>\TeacherTaskManager"
record = load_attendance_install_record(config_dir + r"\attendance-install.generated.json")
result = prepare_attendance_copy(config_dir, record, current_account="<gws auth status로 읽은 계정>")
print(result.state, result.copy_spreadsheet_id, result.copy_spreadsheet_url)
```

사본 이름은 `<원본 이름> - AI 입력 준비 사본 (연-월-일 시분초)`가 된다. 이 이름은 나중에 AI 입력을 켤 수 있는 파일인지 판단하는 기준이므로 사용자에게 바꾸지 말라고 안내한다.

**시작하기 전에 사용자에게 반드시 알린다.** 이 절차가 끝나면 앞으로 쓰는 파일이 사본으로 바뀌고, 원본 시트에서는 Google Chat 발송이 막힌다. 즐겨찾기나 다른 곳에 걸어 둔 출결 시트 링크를 사본 주소로 바꿔야 한다.

**사본에서 `처음 한 번 설정하기` 메뉴는 누르지 않게 안내한다.** 연결 바꾸기가 끝나기 전에 그 메뉴를 누르면 사본이 자기 발송 번호와 확인값을 새로 만들어 버려서, 4단계 연결 옮기기가 `사본 설정 시트의 발송 확인값이 기존 시트와 다릅니다`로 막힌다. 자동으로 고치지 않는다. 사본을 열어 보거나 `확장 프로그램 -> Apps Script`를 누르는 것은 안전하다.

이 절차가 정상으로 받아들이는 실제 시트 모양이다. 아래는 멈추는 이유가 아니다.

- 설정의 `MONTH_SHEET_NAMES`에 적혀 있지만 **선생님이 지운 지난 달 탭**은 건너뛴다. 같은 이름이 두 개면 멈춘다.
- `Google Chat 시도시각` 칸은 글자로 써도 Google이 날짜로 알아보고 숫자로 담는다. **날짜 서식이 입혀진 숫자**는 정상으로 본다. 서식 없는 숫자는 멈춘다.
- 예전에 보낸 줄의 `Google Chat 내용기준` 칸이 **완전히 비어 있으면** 그 줄의 값으로 계산한 표식을 채운다. 그때는 프로그램이 그 칸을 적지 않았기 때문이다. 값이 들어 있는데 다르면 멈춘다.

`Google Chat 내용기준` 제목이 일부 월 탭에만 있고 나머지에는 없으면 멈춘다. 그 칸은 재발송을 막는 표식이 들어가는 자리라, 빠진 탭의 `L1`에 그 제목을 채운 뒤 다시 실행한다. 채우기 전에 그 칸과 아래 줄이 비어 있는지 먼저 읽어 확인한다.

`state`가 `complete`가 아니면 다음 단계로 넘어가지 않는다. `copy_check_required`, `layout_check_required`, `ui_check_required`는 사본이나 행 삽입 결과가 불분명하다는 뜻이다. 사용자에게 사본을 직접 열어 확인해 달라고 안내하고, 같은 명령을 다시 실행하지 않는다.

**2단계 — 사본의 Apps Script에 새 코드 올리기**

사본의 스크립트 ID는 사본 시트에서 `확장 프로그램 -> Apps Script`를 열어 주소에서 읽는다. 원본의 `script_id`를 쓰면 안 된다.

먼저 아무것도 쓰지 않는 확인 실행을 한다.

```powershell
python "<이 스킬 폴더>\scripts\prepare_attendance_copy_script.py" --copied-spreadsheet-id "<사본 Sheet ID>" --copied-script-id "<사본 Script ID>"
```

`state`가 `ready_for_apply`면 사용자 승인을 받고 `--apply`를 붙여 한 번 실행한다. 성공하면 `version_number`, `deployment_id`, `bundle_sha256`이 나온다. 이 세 값을 3단계에 그대로 쓴다.

`hold`가 나오면 다시 실행하지 않는다. 사본 스크립트에 정식 파일 두 개(`Code`, `appsscript`) 외의 파일이 있거나 부모 시트가 다르다는 뜻이다.

**3단계 — 올린 코드를 다시 읽어 확인하고 기록하기**

```python
from switch_attendance_connection import record_prepared_copy_script

record_prepared_copy_script(
    config_dir,
    copy_spreadsheet_id="<사본 Sheet ID>",
    copy_script_id="<사본 Script ID>",
    version_number=<2단계 version_number>,
    deployment_id="<2단계 deployment_id>",
    bundle_sha256="<2단계 bundle_sha256>",
)
```

이 호출은 사본의 현재 코드, 지정한 버전, 지정한 배포판을 각각 다시 읽어 정식 `Code.gs`와 정확히 같은지 확인한다. 모두 맞을 때만 사본 진행 기록에 남기고 옛 코드 보류를 푼다. 하나라도 다르면 `ATTENDANCE_CONNECTION_SWITCH_HOLD`로 멈추고 아무것도 기록하지 않는다.

**4단계 — 연결을 사본으로 바꾸기**

```python
from switch_attendance_connection import switch_attendance_connection

switch_attendance_connection(
    config_dir,
    new_script_id="<사본 Script ID>",
    new_deployment_id="<2단계 deployment_id>",
)
```

이 호출 하나가 같은 출결 잠금 안에서 아래를 순서대로 한다.

1. 현재 Google 계정, 원본·사본 소유자, 공유 사용자, 댓글을 읽어 확인한다.
2. 3단계에 기록한 값으로 사본 Apps Script를 다시 확인한다.
3. 사본 설정 시트의 `CENTRAL_CHAT_SHEET_ID`를 사본 자기 번호로 맞춘다.
4. 중앙 발송 서버에 등록과 연결이 있으면 발송 연결을 사본으로 한 번 옮기고, 옛 시트에서는 더 이상 발송되지 않게 막는다. 등록이나 연결이 없으면 서버 자료를 만들지도 바꾸지도 않고 `등록 없음`으로 넘어간다.
5. 로컬 설치 기록에서 `spreadsheet_id`, `spreadsheet_url`, `script_id`, `deployment_id` 네 값만 바꾼다. 나머지 설정과 알 수 없는 값은 그대로 둔다. 바꾸기 전 원본은 `attendance-install.before-copy-switch.generated.json`으로 한 번만 남는다.

중앙 서버의 답을 받지 못했거나 결과가 애매하면 로컬 연결도 바꾸지 않고 멈춘다. 이때 다시 실행하면 `앞선 중앙 확인 결과가 불명확해 사람이 먼저 확인해야 합니다`로 멈춘다. 이 상태에서는 자동으로 이어가지 말고, 중앙 발송 상태를 사람이 확인한 뒤 어떻게 할지 사용자에게 묻는다.

**5단계 — 사본 시트에서 AI 입력 켜기**

여기부터는 선생님이 직접 한다.

1. 사본 시트를 연다.
2. 상단 `처음 한 번 설정하기` 메뉴에서 `처음 설정 한 번에 끝내기`를 누른다. 이 항목은 어느 시트에서나 보인다.
3. 그 항목을 누르면 AI 입력도 함께 켜진다. **시트에서는 Gemini API 키를 묻지 않는다.** 컴퓨터의 티처 매니저 `연결` 화면에 넣은 키가 `설정` 탭 `GEMINI_API_KEY` 줄에 들어와 있고, 시트는 그 값을 읽는다. 처음 한 번은 Google 권한 승인 화면이 뜬다.
4. 키가 없거나 모양이 아니면 `컴퓨터의 티처 매니저를 열고 [연결] 화면에서 Gemini API key를 넣어 저장한 다음 이 메뉴를 다시 눌러 주세요`라고 알리고 아무것도 켜지 않는다.
5. 월 시트 1행 B열부터 K열까지가 한 칸으로 합쳐진 입력칸이다. 거기에 `7월 25일 홍길동 감기로 병결`처럼 적고 Enter를 누르면 맨 아래에 출결행이 하나 생기고, 그 줄 M열에 `AI`라고 적힌다. A열에는 `AI 출결 입력`이라는 이름표가 있다.

월 시트 1행 모양은 이렇다.

| 자리 | 들어가는 것 | 색 |
| --- | --- | --- |
| A1 | `AI 출결 입력` 이름표 | 연한 파랑 `#E8F2FF` |
| B1~K1 (한 칸) | 문장을 적는 칸. 비어 있으면 회색 안내 문구가 들어 있다 | 흰 바탕 + 회색 테두리 |
| L1~P1 | 아무것도 없다 | 색 없음 |
| 2행 A~M | 제목 12개 + `AI 입력` | 진한 파랑 `#1F4E79` |
| M열 3행 아래 | AI가 넣은 줄에만 `AI` | 날짜 줄무늬 그대로 |
| N~P 열 전체 | 아무 자료도 안 들어간다 | 색 없음 |

Enter를 누른 뒤 그 칸은 회색 안내 문구로 되돌아간다. 출결행이 생겼는지는 맨 아래 줄 M열의 `AI` 표시로 확인한다. 이름을 못 찾거나 날짜·구분을 해석하지 못하거나 통신이 실패하면 아무 줄도 만들지 않고, 실패를 시트에 표시하지도 않는다. 다른 처리가 돌고 있어 아무 일도 못 했을 때만 적은 문장을 그대로 남긴다. 같은 메뉴를 다시 눌러도 두 번째 감지기를 만들지 않는다.

원본 시트와 4단계가 끝나지 않은 사본에서는 `AI 출결 입력 켜기` 단계만 건너뛰고, 결과 화면에 `AI 입력을 켤 수 있는 사본이 아닙니다`라고 적힌다. 나머지 사전 세팅은 그대로 진행된다. 사용자에게 원본 시트에서 켜는 방법을 안내하지 않는다.

이 절차는 시트 스크립트 `5.10.0`부터 이 모양이다. 감지기를 만들고 정리하는 데 `https://www.googleapis.com/auth/script.scriptapp` 권한이 필요해서, 이미 쓰던 선생님도 처음 한 번 다시 승인해야 한다. `5.9.0` 이전 판에서 시트 메뉴로 직접 키를 넣어 둔 선생님은 그 값이 그대로 쓰인다.

## Google Chat 쪽지 발송

담임 교사가 `쪽지`, `종례 쪽지`, `단체방 발송`, `개인 DM 발송`, `안내장`을 말하면 이 섹션을 따른다.

이 기능은 출결 자동화 Google Sheet 안의 Apps Script 메뉴로 실행한다. Google Forms 설문, Gmail 발송, 읽음 확인, 답장 수집은 하지 않는다.

Google Chat 발송은 두 출처로 나눈다.

1. `출결 업무 자동화`: 월별 시트에서 미제출 서류 행을 선택하고 `선택 행 미제출 서류 Google Chat 개인톡 보내기`를 누른다. 결과는 월별 시트 맨 끝의 Google Chat 발송상태, Google Chat 시도시각, Google Chat 결과에서 확인한다.
2. `교육청 메신저 정리·발송`: 교육청 메신저에서 온 개인 안내는 `메신저 개인톡 내용`, 학급 안내는 `메신저 단체톡 내용`에 정리하고 Google Chat으로 보낸다.

처음 발송 전에는 `처음 한 번 설정하기 -> 처음 설정 한 번에 끝내기`를 눌러 선생님 계정의 발송 허락을 끝낸다. 이 절차는 Google Cloud 설정이 아니라 선생님 계정 연결이다. 선생님은 `Google Cloud Console`을 열지 않는다.

출결 미제출 독촉은 월별 시트에서 보내고 월별 시트에서 결과를 확인한다.

쪽지 원본은 출처별로 나눈다. 출결 독촉 원본은 월별 시트의 선택 행이고, 메신저 개인 쪽지는 `메신저 개인톡 내용`, 메신저 단체 쪽지는 `메신저 단체톡 내용`이 정본이다. 어느 경로든 처음 보내기 전에는 시트의 `처음 한 번 설정하기 -> 처음 설정 한 번에 끝내기`를 한 번 먼저 눌러야 한다. 그 항목 하나가 기본 시트 점검, AI 출결 입력 켜기, `Google Chat 최초 발송 연결하기`, `Google Chat 학급 단톡방 고르기`를 순서대로 실행하고 무엇이 됐고 무엇이 남았는지 한 화면에 보여 준다. 중간에 뜨는 구글 권한 허용 화면과 단톡방 목록은 사람이 골라야 하는 것이라 그대로 뜬다. 준비가 됐는지는 같은 메뉴의 `연결 상태 확인`으로 본다. 종례 때 오늘과 그 이전 날짜의 대기 중인 줄을 한꺼번에 보내려면 `교육청 메신저 정리·발송 -> 메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기`를 안내한다. 출결 행 하나를 골라 그 학생에게 바로 보내고 싶으면 `출결 업무 자동화 -> 선택 행 미제출 서류 Google Chat 개인톡 보내기`를 안내한다(개인톡 전용, `출결신고서 자동화` 9~10단계 참고). Google Docs 안내장 경로는 제거됐다. 발송 요청을 받아도 Google Docs 문서를 만들거나 읽지 않고, Chat에 Docs 링크도 보내지 않는다.

LLM 입력(공문 분석 등)으로 담임 Tasks를 만들었으면 같은 안내 문장을 쪽지 대장에도 저장한다. 학급 전체 안내는 `메신저 단체톡 내용`에, 특정 학생 안내는 `메신저 개인톡 내용`에 `자동분석` / `확인필요` 상태로 넣는다. 시트 ID는 `attendance-install.generated.json`의 `spreadsheet_id`를 쓰고, 먼저 같은 날짜 줄을 읽어 같은 내용이 이미 있으면 추가하지 않는다. 선생님을 Google Sheet 메뉴로 다시 보내지 않는다.

```powershell
# 1) 중복 확인: 오늘 날짜에 같은 내용이 이미 있으면 추가하지 않는다.
gws sheets spreadsheets values get --params '{"spreadsheetId":"<출결 시트 ID>","range":"메신저 단체톡 내용!A:G"}' --format json

# 2) 새 안내 문장 추가 (열: 보낼 날짜, 안내 종류, 안내 내용, 들어온 곳, 상태, 보낸 시각, 결과)
gws sheets spreadsheets values append --params '{"spreadsheetId":"<출결 시트 ID>","range":"메신저 단체톡 내용!A1","valueInputOption":"RAW","insertDataOption":"INSERT_ROWS"}' --json '{"majorDimension":"ROWS","values":[["<오늘 YYYY-MM-DD>","기타","<안내 문장>","자동분석","확인필요","",""]]}'
```

메신저 개인톡 내용의 열은 `보낼 날짜, 번호, 이름, 쪽지 종류, 쪽지 내용, 들어온 곳, 상태, 연결 표시, 보낸 시각, 결과` 10개다. 같은 방식으로 append하되 번호와 이름을 채운다. 번호와 이름은 추측하지 말고 아래 절차로 학생명단과 대조해 확정한다.

1. 등록 전에 `학생명단` 시트에서 번호(A)와 이름(B)만 읽는다. D열(학생 Google 이메일)은 대화 컨텍스트로 가져오지 않는다.

```powershell
gws sheets spreadsheets values get --params '{"spreadsheetId":"<출결 시트 ID>","range":"학생명단!A:B"}' --format json
```

2. "길동이", "길동 학생" 같은 호칭은 명단과 대조해 정식 번호·이름(예: 10, 홍길동)으로 바꾼다. 등록을 마치면 "10번 홍길동으로 등록했습니다"처럼 해석 결과를 함께 보고한다.
3. 동명이인이거나 명단에서 찾지 못하면 추측으로 등록하지 말고 선생님에게 어느 학생인지 바로 물어본다. 명단이 비어 있으면 `학생명단` 채우기부터 안내한다.
4. 중복 확인: 같은 날짜에 같은 번호·이름·쪽지 내용으로 `보냄`이 아닌 줄이 이미 있으면 추가하지 않는다.
5. 쪽지 종류는 `출결서류`, `준비물`, `개별안내`, `상담/확인`, `기타` 중에서 고르고 애매하면 `개별안내`. 상태는 기본 `확인필요`, 선생님이 대기로 넣어 달라고 명시하면 `대기`. 연결 표시는 빈 값으로 둔다.

설치 도우미는 이 시트를 별도의 Google Chat 앱으로 등록하지 않는다. 선생님용 시트는 쪽지 대장과 발송 버튼만 맡고, 실제 Google Chat 발송 권한과 앱 설정은 개발자가 관리하는 중앙 발송기가 맡는다.

기본 배포판은 쪽지 대장 자동 준비를 안정 기본값으로 둔다. 선생님에게 별도 관리 화면으로 들어가라고 안내하지 않는다. 자동 발송은 중앙 발송 방식이 준비된 배포에서만 켠다. 공개 배포판은 릴리스 정보에 들어 있는 중앙 발송소 주소를 설치 때 자동으로 시트에 채운다. 이 값이 시트의 중앙 발송 URL이다. 개발 테스트에서는 `--central-chat-sender-url` 또는 `CENTRAL_CHAT_SENDER_URL` 환경값으로 다른 주소를 넣을 수 있다. 자동 발송 준비가 막히면 같은 실패를 반복하지 말고 쪽지 내용은 보낼 상태로 시트에 남겨둔다. 중앙 발송 준비가 끝난 뒤 같은 시트 메뉴를 다시 누르면 재시도되게 안내한다.

단체방 발송을 쓰려면 선생님이 먼저 Google Chat에서 학급 스페이스를 만들어야 한다. 학생 개인 Google 계정을 쓸 경우 그 방은 반드시 외부 사용자 허용 스페이스여야 한다. 이미 일반 방으로 만든 방은 외부 허용으로 바꾸기 어려우므로, 학생용 방은 새로 만드는 편이 안전하다.

교육청 설정에서 외부 스페이스가 막혀 있으면, 선생님이 개인 Gmail 학생과 1:1 Chat은 할 수 있어도 학급 스페이스에는 초대하지 못한다. 이 경우 방 목록에 스페이스가 보여도 개인 Gmail 학생용 단체방으로 쓰면 안 된다. 학생 개인 Gmail 대상 안내는 `메신저 쪽지 내용 Google Chat으로 개인톡 보내기`로 돌리고, 실패한 학생은 발송기록에 남긴다.

학생 초대와 방 참여는 선생님이 수작업으로 진행한다. 스킬은 학생을 학급 방에 자동 초대하지 않는다.

개인 DM은 `학생명단`의 학생 Google 이메일을 기준으로 보낸다. 학생 Google 이메일이 있으면 개인 DM 대상이고, 없거나 외부 DM이 막혀 있으면 해당 학생은 건너뛰고 발송기록에 남긴다.

설치 뒤 선생님에게 이렇게 안내한다.

> Google Sheet의 `메신저 개인톡 내용`과 `메신저 단체톡 내용`을 확인하세요.
> 자동으로 들어온 줄은 먼저 `확인필요` 상태입니다. 보낼 내용만 `대기`로 바꾸고, 보내지 않을 줄은 `제외`로 바꾸면 됩니다.
> 출결표에서 미제출로 표시한 출결서류 독촉은 월별 시트에서 바로 보내고, 결과는 월별 시트 맨 끝에서 확인합니다.
> 종례 때 `교육청 메신저 정리·발송 -> 메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기`를 누르면 오늘과 그 이전 날짜의 `대기` 줄을 보냅니다.

## Brity 메시지 캘린더 연결 도우미

사용자가 `Brity`, `브리티`, `메신저 자동 등록`, `단축키 등록`을 말하면 이 섹션을 따른다.

학교 Windows PC에서 Brity Messenger 메시지를 화면에 띄우고 `Ctrl+Alt+Win`을 누르면, 화면의 글과 첨부파일을 직접 읽어 이 스킬의 판단 규칙으로 Google Calendar/Tasks에 등록하는 작업표시줄 도우미다. 소스는 이 스킬의 `scripts/brity_bridge`에 있다. 분석은 Gemini API를 사용한다 — 별도 코딩 CLI 설치가 필요 없다.

먼저 개인 설정 JSON(`profile.generated.json`)과 gws CLI 로그인, Gemini API key가 있어야 한다. 셋 다 `설정 대시보드`가 안내한다. key 발급은 연결의 Brity 메신저 탭에서 `Google API key 발급 URL`을 열어 진행한다.

실행:

```powershell
cd "<이 스킬 폴더>\scripts"
python -m brity_bridge run
```

- 누르면 바로 등록된다. 잘못 등록한 항목은 대시보드 홈의 처리한 메시지 목록에서 확인하고 Google Calendar/Tasks에서 지우면 된다.
- 도우미가 시작되면 `도우미가 시작됐습니다` 알림이 뜬다. 알림이 없으면 실행되지 않은 것이다 — `python -m brity_bridge doctor`로 점검한다.
- 브리티 글자가 선택된 것처럼 보여도 오른쪽 클릭과 복사가 작동하지 않는다. 따라서 복사를 시도하거나 사용자에게 복사를 부탁하지 않고, 쪽지나 대화를 열어 둔 뒤 `Ctrl+Alt+Win`을 누르면 화면의 글을 직접 읽어 분석한다.
- 화면에 첨부파일이 보이면 모두 내려받은 뒤에만 분석한다. 하나라도 없으면 `첨부파일을 먼저 내려받아 주세요.`라고 알리고 Calendar·Tasks·학생 안내 시트 어느 곳에도 등록하지 않는다.
- 기본 첨부파일 다운로드 폴더는 `C:\BrityWorks\BrityMessenger\download`이며 설정 화면에서 바꿀 수 있다.
- 지원 첨부파일은 HWP/HWPX, PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT/CSV, JPG/JPEG/PNG다. 구형 DOC/XLS/PPT는 Microsoft Office가 있어야 안정적으로 읽는다.
- 메시지 본문과 읽어낸 첨부 내용은 Gemini로 전송된다. 사진과 스캔 PDF는 파일 화면도 함께 전송된다.
- 단축키를 누른 뒤 다른 프로그램을 앞으로 띄우는 것은 괜찮다. 처리 알림이 뜰 때까지 브리티를 닫거나 최소화하거나 다른 대화방으로 바꾸지만 않는다.
- 화면을 읽지 못하면 오른쪽 아래 알림으로 실패 이유와 다시 누르는 방법을 안내한다. 복사 방식으로 돌아가지 않는다.
- 단축키가 다른 프로그램과 겹치면 설정 화면에서 원하는 조합을 직접 눌러 바꾼다.
- 설정과 본문 없는 기록은 설정 폴더의 `brity-bridge` 아래에 있다: `settings.json`, `history.json`, `logs`. Gemini API key는 settings.json에만 저장하고 남과 공유하지 않는다. 입력이 제품 개선에 쓰일 수 있으므로 학생 개인정보가 담긴 메시지는 등록하지 않는다.
- 같은 메시지를 다시 누르면 새로 만들지 않고 이미 등록됐다고 알려준다.
- 메시지에 학생·학급 안내가 있으면 출결 자동화 시트의 `메신저 개인톡 내용`/`메신저 단체톡 내용` 시트에 상태 `확인필요`로 들어간다. 학생에게 바로 발송되지 않는다. 학생 안내를 개인톡·단체톡 시트에 확인필요로 옮긴 것은 아직 학생에게 보낸 것이 아니다. 선생님이 확인 후 발송 대기로 바꾼 줄만 나중에 보낸다. 출결 자동화 시트가 없으면 `옮기지 못함 · 처음 설정 필요`로 남긴다.
- Google Tasks에는 날짜·시간을 지정하지 않는다. 원문·메모 안의 날짜와 시간 문장은 보존하되, Tasks 제목 끝의 (몇교시) 표시는 제거한다.
- 사용 한도에 도달하면 그날은 분석이 멈춘다. 잠시 뒤 또는 다음 날 다시 시도한다.
- 마지막 결과는 `python -m brity_bridge status`, 전체 점검은 `python -m brity_bridge doctor`로 본다.
- Windows 시작 시 자동 실행은 대시보드 완료 단계가 기본으로 켠다.

## 워크플로우 (4단계 + 예외처리)

```
입력 텍스트 → [1] 퀵체크 → [2] 캘린더/Tasks 선택 → [3] 시간 배치 → [4] 실행 → [99] 예외처리
```

### 1단계: 퀵체크 (TaskObject 초기화)
> 상세: `references/1_quick_check.md`

원문에서 추출할 정보:
- `summary`: 한 줄 요약
- `due`: 마감일 (YYYY-MM-DD), `due_meta.source`: explicit/inferred/none
- `d_day`: 오늘 기준 D-day
- `priority`: Critical(🔴) / High(🟠) / Medium(🟡) / Low(⚪)
- `estimation`: 소요시간 (minutes, periods)
- `school_schedule_info`: 기간 행사 시 {start_date, end_date, all_day}

**우선순위 판단**:
| 조건 | 우선순위 |
|------|----------|
| D-day=0 + 남은시간 2시간 미만 | Critical 🔴 |
| D-day ≤ 3 또는 학교폭력/성적처리 | High 🟠 |
| 3 < D-day ≤ 7 | Medium 🟡 |
| D-day > 7 | Low ⚪ |

**소요시간 기준 (교시 길이는 `profile.generated.json.school.class_minutes` 사용)**:
| 작업 유형 | 기준 |
|----------|------|
| 문서 읽기 | 1페이지당 4~5분 |
| 문서 작성 | 1페이지당 25~35분 |
| 학생당 의견 기입 | 5~10분 |
| 학교폭력 문서 | 기본 30분 + 페이지당 20분 |

**학사일정 기간 표현 감지 시**:
- `12/10~12/12`, `10/30-10/31` 등 기간 표현 → `school_schedule_info` 생성
- 주말 보정은 4단계 actions 생성 후 99단계에서 처리

### 2단계: 캘린더/Tasks 선택
> 상세: `references/2_calendar_selection.md`

**캘린더 분류 (target_system)** - 독립 결정:

| 조건 | target_system |
|------|---------------|
| 내가 직접 수행하는 업무 | `work_calendar` |
| 학교 전체 공유 일정 | `school_calendar` |
| 둘 다 해당 | `split` |
| 해당 없음 | `none` |

**work_calendar 키워드**: 준비, 작성, 검토, 입력, 정산, 결재, 감독, 지도, 설치, 순시, 점검

**school_calendar 키워드**: 개학, 종업식, 지필평가, 수련활동, 체험학습, 학부모 총회

**담임 Tasks (need_homeroom_task)** - `profile.generated.json.homeroom.enabled == true`일 때만 판단한다. 비담임이면 항상 `false`다. 담임일 때는 캘린더와 **독립 결정**, 아래 3조건 동시 충족:
1. 즉시성: 오늘, 내일, 꼭, 잊지말고, 바로
2. 학생 대상: 학생, 반, 체육복, 준비물
3. 업무 키워드 없음: 작성, 결재, 정산 등 없어야 함

**[중요] 시점 명시 시 캘린더 강제**: "~교시에", "종례 후", "점심시간에" 등 특정 시점 있으면 무조건 `work_calendar` 포함 (절대 `none` 불가)

**가능한 조합**: work+Tasks, school+Tasks, split+Tasks, none+Tasks 모두 가능

### 3단계: 시간 배치
> 상세: `references/3_time_analysis.md`

**work_calendar 전용**. school_calendar나 Tasks만 있으면 건너뜀.

`target_system = "none" && need_homeroom_task = true`인 Tasks 전용 안내는 시간 블록 생성하지 않음.

**교시 시간표**:
- 실제 교시 시간은 설정 폴더의 `profile.generated.json` 안에 있는 `period_times`를 사용한다.
- 요일별 종례 시간은 `profile.generated.json` 안에 있는 `afternoon_homeroom_times`를 사용한다.
- 스킬 문서의 고정 시간표 예시는 무시하고, 항상 사용자 설정 파일에서 만든 JSON을 우선한다.

**배치 원칙**:
- 마감일 직전부터 역산하여 공강 교시에 배치
- Critical/High → 이번 주, Medium/Low → 상황에 따라 다음 주
- 주말 사용: Critical/High이면서 평일에 다 못 채웠을 때만 (work_calendar 전용)

### 4단계: 실행 (gws CLI)
> 상세: `references/4_execution_guide.md`

**캘린더/Tasks ID**:
- 업무 캘린더: `profile.generated.json.calendars.work_calendar_id`
- 학사일정 캘린더: `profile.generated.json.calendars.school_calendar_id`
- 담임 안내 Tasks 목록: `profile.generated.json.homeroom.enabled == true`일 때만 `profile.generated.json.calendars.homeroom_tasks_id`

**제목 포맷**:
- work_calendar: `[{카테고리}] {업무명} {우선순위이모지} {우선순위} ({교시})`
- school_calendar: `{행사명} ({기간})`
- Tasks: `[학생안내] {안내문장} (마감: {M/DD})` — 마감일 있으면 항상 표시, 없으면 생략

**⚠️ 캘린더 출력 일관성**:
- 업무 캘린더 제목과 설명은 같은 우선순위 이름과 같은 이모지를 사용한다.
- 설명 첫 줄은 항상 `{우선순위이모지} 우선순위: {priority}`로 시작한다.
- 마감이나 D-day는 우선순위 첫 줄의 괄호 안에 함께 표시한다.
- 판단한 우선순위를 제목에는 넣고 설명에는 빼면 실패로 본다.
- 판단한 우선순위를 설명에는 넣고 제목에는 빼면 실패로 본다.

**⚠️ 캘린더 설명은 실제 줄바꿈 사용**:
- 캘린더 설명은 실제 줄바꿈으로 읽기 좋게 나눈다.
- 줄바꿈 표시 글자가 그대로 보이면 실패로 본다.
- 굵은글씨 표시용 문법은 넣지 않는다. 캘린더 화면에서 그대로 보일 수 있다.
- 이모지는 제목과 구역 이름에 직접 넣는다.
- 구역 이름은 `🟠 우선순위: High (마감: 12/03 퇴근 전)`, `✅ 처리 순서`, `⚠️ 확인`, `📎 참고`처럼 화면에서 바로 읽히게 쓴다.
- 원문 세부사항을 버리지 않는다. 장소, 대상, 주요 안건, 유의사항, 문의, 발신, 첨부파일이 있으면 설명칸에 각각 살려 쓴다.
- 짧은 요약문만 넣으면 실패로 본다. 제목은 짧게, 설명은 선생님이 바로 처리할 수 있을 만큼 충분히 적는다.

**학사일정 all-day 이벤트 end 처리**:
- `school_schedule_info.end_date`는 포함 기준
- API 호출 시 `end = end_date + 1일` (exclusive)

### 예외 처리
> 상세: `references/99_exception_handling.md`

**핵심 원칙**: 캘린더 실패해도 Tasks 독립 실행, 절대 서로 롤백하지 않음

- 시간 부족 시: 가능한 만큼만 배치 + `warnings` 기록
- `target_system=none`: `calendar_events = []`
- 학사일정 주말 포함 시: 단일 날짜면 이전 평일로 이동, 기간이면 경고만

## 실행 프로토콜

```
0. 개인 설정 확인 → 없으면 처음 시작 프로토콜 실행 후 멈춤
1. `teacher-profile.csv` 또는 `weekly-timetable.xlsx`가 JSON보다 새로우면 parse_settings.py 실행
2. profile.generated.json 읽기
3. 텍스트 받으면 → 1~3단계 내부 처리 (출력 금지)
4. actions 확정 → 즉시 gws CLI 명령 실행 (확인 요청 금지)
5. 완료 후 → "등록했습니다" 한 마디만
```

## gws CLI 호출 예시

**캘린더 이벤트 생성** (업무 캘린더, 시간 지정):
```bash
gws calendar events insert \
  --params '{"calendarId":"<work_calendar_id>"}' \
  --json '{
    "summary": "[정산] 수련활동 지원금 정산 🟠 High (2-3교시)",
    "start": {"dateTime": "2025-12-03T10:05:00+09:00", "timeZone": "Asia/Seoul"},
    "end": {"dateTime": "2025-12-03T11:45:00+09:00", "timeZone": "Asia/Seoul"},
    "description": "🟠 우선순위: High (마감: 12/03 퇴근 전)\n\n수련활동 지원금 정산\n\n✅ 처리 순서\n1. 영수증 정리\n2. 정산서 작성\n\n📎 참고\n- 수련활동_참가동의서.hwp\n- 저소득층_지원금_정산서.xlsx\n\n📁 처리 시각: 2025-12-03 10:32"
  }'
```

**학사일정 (all-day) 생성**:
```bash
gws calendar events insert \
  --params '{"calendarId":"<school_calendar_id>"}' \
  --json '{
    "summary": "2학년 기말고사 (12/10~12/12)",
    "start": {"date": "2025-12-10"},
    "end": {"date": "2025-12-13"},
    "description": "📅 구분: 학사일정\n기간: 12/10 ~ 12/12"
  }'
```
> all-day end 처리: end_date(12/12) + 1일 = 12/13 (exclusive)

**Tasks 생성**:
```bash
# Tasks 목록 ID 조회 (최초 1회)
gws tasks tasklists list

# Tasks 등록
gws tasks tasks insert \
  --params '{"tasklist":"<homeroom_tasks_id>"}' \
  --json '{
    "title": "[학생안내] 내일 체육복 입고 오기 (마감: 12/03)",
    "notes": "내일 체육복 꼭 입고 오기\n\n- 대상: 담임 학급 학생\n- 전달 시점: 조회/종례"
  }'
```
> Google Tasks 등록 요청에는 날짜·시간 값을 보내지 않는다. 메모 안의 날짜·시간 문장은 그대로 남긴다.
> `<work_calendar_id>`, `<school_calendar_id>`, `<homeroom_tasks_id>`는 `profile.generated.json`에서 읽은 실제 ID로 바꿔 실행한다.

담임 Tasks를 1개라도 만들었으면 같은 안내 문장을 `메신저 단체톡 내용`에도 저장한다 (`Google Chat 쪽지 발송` 섹션의 append 절차, 중복 확인 포함).

이 단계까지 끝난 뒤에만 "등록했습니다"라고 보고한다. 선생님을 Google Sheet 메뉴로 다시 보내지 않는다.

## 참고 정보

- 개인 정보, 학교 정보, 시간표, 캘린더 ID, Tasks 목록 ID는 설정 폴더의 `profile.generated.json`에서 읽는다.
- 스킬 문서에는 개인 이름, 학교, 캘린더 ID를 고정해서 두지 않는다. 실제 실행에서는 사용자 설정 JSON이 항상 우선한다.
- 비담임이면 담임 안내 Tasks 관련 판단과 생성은 하지 않는다.
- 학사일정 캘린더는 월~금만 사용한다. 주말 날짜가 들어오면 예외 처리 규칙을 따른다.

AI가 넣은 줄에 배경색을 칠하지 않는다. 배경은 날짜 줄무늬가 쓰는 자리다 — 같은 날짜끼리 묶어
회색·흰색을 번갈아 칠해 나이스에 날짜별로 옮겨 적을 때 덩어리 경계를 보여 준다. 거기에 색을
덮으면 한 날짜 덩어리가 쪼개져 보인다. 그래서 표시는 M열 글자로 하고, 넣은 직후 줄무늬를 다시
입혀 새 줄도 제 날짜 덩어리의 색을 갖게 한다.

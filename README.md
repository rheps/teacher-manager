# Teacher Manager

**경기도교육청 소속 선생님을 위한 Google 업무 자동화 프로그램**

교육청 업무 메신저(Brity)의 쪽지·공지를 Google Calendar와 Tasks에 정리하고,
출결 Google Sheet에서 결석 신고서·할 일·학생 및 학급 Google Chat 안내를 이어서 처리합니다.

- 만든 사람: **Big-Silver EDU LAB** (부천 중원고등학교 교사 김대은)
- 제품 안내: https://big-silver.xyz/teacher-google-automation
- 문의: https://big-silver.xyz/support/teacher-google-automation

## 설치 전에 준비할 것

Teacher Manager는 현재 **경기도교육청 소속 교사 전용**이며, Google 로그인은
정확히 `@goedu.kr`로 끝나는 계정만 받을 수 있습니다. 개인 Gmail 계정으로는 진행할 수 없습니다.

선생님은 설치 전에 다음 순서로 가입과 신청을 마쳐 주세요.

1. [교육디지털원패스](https://edupass.neisplus.kr/)에서 교직원 회원가입
2. [경기도교육청 교육용 클라우드 지원시스템](https://www.goedu.kr/) 가입
3. **경기도교육청 클라우드 지원시스템 내** [Google Workspace 사용 신청](https://www.goedu.kr/bbs/3/view/63)을 마치고 선생님 `@goedu.kr` 계정 준비

Google Chat으로 학생 개인톡이나 학급 단체톡을 보낼 때는 아래 준비도 필요합니다.

1. 학생의 `@goedu.kr` 계정을 준비합니다. 학생 가입은 [학생 클라우드 서비스 가입 안내](https://www.goedu.kr/bbs/2/view/55)를 참고해 주세요.
2. 선생님이 [Google Chat](https://chat.google.com/)에서 학생 계정을 직접 초대합니다.
3. 학급 단체톡방을 직접 만들거나, 이미 쓰는 학급 단체톡방에 학생을 초대합니다.

Teacher Manager가 학생 가입이나 Chat 초대를 대신하지는 않습니다. 학생에게 이 프로그램이나
선생님의 출결 Sheet 편집 권한을 주지 말고, 학생은 필요한 Chat방에만 초대해 주세요.

## 내려받기

이번 **3.0 공개판은 아직 남은 문제를 찾기 위한 공개 현장시험판**입니다.
일반 배포가 모두 확인된 판은 아니며, **Setup 설치 파일 하나만** 제공합니다.

**[최신 Teacher Manager 내려받기](https://github.com/rheps/teacher-manager/releases/latest)**

Release 페이지에서 `TeacherManager-Setup-3.0.exe`를 받습니다.
`Portable ZIP`, `MSIX`, Microsoft Store 설치판은 현재 제공하지 않습니다.

## 설치 순서

1. 받은 `TeacherManager-Setup-<버전>.exe`를 더블클릭합니다.
2. 파란 **Windows의 PC 보호** 창이 뜨면 **추가 정보 → 실행** 순서로 누릅니다.
3. 한국어 설치 화면에서 안내를 읽고 **다음**을 눌러 설치합니다.
4. 설치가 끝나면 Teacher Manager를 실행합니다.
5. 왼쪽에 보이는 9단계 순서를 따라 `@goedu.kr` 계정으로 Google 로그인과 설정을 마칩니다.

처음 설정의 왼쪽에는 아래 순서가 계속 보여서 지금 어디까지 했는지 확인할 수 있습니다.

1. 시작 전 준비
2. Google 로그인
3. 내 정보
4. 하루 일과
5. 시간표
6. 이 컴퓨터 설정
7. Google 연결
8. 학생 계정 준비
9. 모두 저장

이 Setup은 아직 코드 서명을 하지 않았기 때문에 Windows가 낯선 앱으로 표시할 수 있습니다.
`Windows의 PC 보호` 창은 **추가 정보 → 실행**으로 넘어갈 수 있습니다. 반대로
**스마트 앱 컨트롤이 차단했습니다**처럼 실행 선택 자체가 없는 창이 뜬다면, 이 프로그램 하나 때문에
Windows 보호 기능을 바로 끄지 말고 다른 시험용 컴퓨터를 쓰거나 문의해 주세요.

## 2.7에서 2.8로 업데이트하는 경우

프로그램 안에 **새 버전** 안내가 뜨면 **지금 업데이트**를 누릅니다. 설치 파일을 다시 실행해도
현재 Windows 계정의 `%USERPROFILE%\TeacherTaskManager` 폴더에 저장된 내 정보, 시간표,
Calendar·Tasks·메신저·출결 연결 설정을 보존하도록 되어 있습니다.

중요한 학교 자료는 평소처럼 별도로 백업해 두는 것이 좋습니다. 업데이트 뒤 처음 설정을 다시 하라고
나오거나 기존 자료가 보이지 않으면 새 값을 저장하지 말고 문의해 주세요. 실제 2.7에서 2.8로 바꾸는
과정은 이번 공개 현장시험에서 더 확인해야 합니다.

## 현재 확인하지 못한 부분

이 2.8 공개 현장시험판에는 아직 사람의 확인이 더 필요한 부분이 있습니다.

- Google의 공개 OAuth 검토가 아직 끝나지 않았습니다. 로그인 중 **Google에서 확인하지 않은 앱** 안내가 보일 수 있습니다.
- 실제 학교 `@goedu.kr` 계정으로 로그인하여 Calendar·Tasks·Sheet·Docs 작업을 처음부터 끝까지 하는 과정은 아직 **현장 미검증**입니다.
- 출결 Sheet 안의 Google 자동화 내용을 실제로 새 판으로 올린 뒤, 배포본과 한 글자도 다르지 않은지 대조하는 과정은 아직 **현장 미검증**입니다.
- 실제 Google Chat 메시지를 보내는 과정은 아직 **현장 미검증**입니다.
- 학교 안에서 Brity 메시지를 가져오고 학교 인증을 거치는 과정도 아직 **현장 미검증**입니다.
- 필요한 프로그램이 없는 깨끗한 Windows 10에서 화면 표시용 Microsoft 도구(WebView2)까지 설치하는 과정도 아직 **현장 미검증**입니다.

자동 확인이 끝난 부분과 실제 학교 계정으로 사람이 확인해야 하는 부분은 다릅니다. 위 항목을 직접
확인하기 전에는 실제 학교 동작까지 모두 확인됐다고 안내하지 않습니다. 시험 중에는 실제 학생 자료 대신
가상의 학생 이름과 시험용 Google 자료를 사용해 주세요. 문제가 생기면 화면에 보인 오류 안내를
그대로 적어 보내 주세요.

## 필요한 환경

- 64비트 Windows 10 또는 Windows 11
- 인터넷 연결
- 선생님의 `@goedu.kr` Google Workspace 계정

컴퓨터에 Python, Node.js, npm, Google Workspace CLI를 따로 설치할 필요는 없습니다.
필요한 Google Workspace CLI는 Setup 안에 함께 들어 있습니다.

## AI 비서에서 스킬로 쓰는 경우

공개 스킬 이름은 `teacher-task-manager` 하나입니다. Codex나 Claude Code 같은 AI 비서에서
직접 연결할 때는 아래 명령을 사용합니다.

프로그램이 AI 비서용 기능을 자동으로 설치하는 길은 2.8에서도 **공개 준비 중**으로 닫혀 있습니다.
아래 방법은 사용자가 직접 연결할 때만 사용해 주세요.

```powershell
npx skills add rheps/teacher-manager -g --all
```

개인 설정은 `Path.home() / "TeacherTaskManager"`에 저장됩니다. Windows에서는
`C:\Users\<사용자이름>\TeacherTaskManager` 모양이며, 안내할 때는 사용자가 확인할 수 있도록
the exact full path를 먼저 보여 줍니다. 스킬이나 프로그램을 업데이트해도 이 폴더를 지우지 않습니다.

## 문의와 문제 제보

- 지원 페이지: https://big-silver.xyz/support/teacher-google-automation
- 개인정보처리방침: https://big-silver.xyz/privacy/teacher-google-automation
- GitHub Issues: https://github.com/rheps/teacher-manager/issues

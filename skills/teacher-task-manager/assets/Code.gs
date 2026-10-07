/**
 * 출결 신고서 자동화 · 기존 Google Docs 템플릿 유지
 * 버전: 5.13.14
 *   (아래 APP_VERSION과 항상 같아야 한다. 버전을 올릴 때 두 곳을 함께 고친다 — 테스트가 대조 검사함)
 * for Google Sheets + Google Docs + Google Tasks
 *
 * 핵심 원칙:
 * - 월별 입력표는 A:M이 실제 표다. N열부터는 월 시트에 남기지 않는다.
 * - 신고서 양식은 이미 만들어 둔 Google Docs 템플릿을 그대로 복사해서 사용
 * - 새 템플릿 문서 생성 기능은 넣지 않음
 * - 학생명단은 A열 번호, B열 이름, C열 학생 이메일만 두고, 번호+이름은 드롭다운 시트의 숨은 목록에서 자동 생성
 */

const APP_NAME = '출결 신고서 자동화';
const APP_VERSION = '5.13.14';
// 제작자 정보는 설정 시트가 아니라 코드에 고정한다.
// 설정 시트에 두면 사용자가 지웠을 때 되살릴 방법이 없다.
const APP_AUTHOR_NAME = 'Big-Silver EDU LAB (http://big-silver.xyz)\n부천 중원고등학교 김대은';
const APP_REPO_URL = 'https://github.com/rheps/teacher-manager';
const CONFIG_SHEET_NAME = '설정';
const DEFAULT_MONTH_SHEETS = ['3월','4월','5월','6월','7월','8월','9월','10월','11월','12월','1월','2월'];

const FALLBACK_TEMPLATE_DOC_ID = '';   // 설정 시트 TEMPLATE_DOC_ID 우선. 비상용으로만 사용.
const FALLBACK_TASK_LIST_ID = '';      // 설정 시트 TASK_LIST_ID 우선.
const FALLBACK_CLASS_LABEL = '';
const FALLBACK_HOLIDAY_SHEET_NAME = '휴일';

/***** 월별 출결표 행 구조와 날짜 줄무늬 설정 *****/
const MONTHLY_ATTENDANCE_INPUT_ROW = 1;
const MONTHLY_ATTENDANCE_HEADER_ROW = 2;
const MONTHLY_ATTENDANCE_DATA_START_ROW = 3;
// 예전 판이 A1에 넣던 문구다. 이미 만들어진 사본을 새 1행 모양으로 갈아입힐 때 알아보는 데 쓴다.
const MONTHLY_ATTENDANCE_AI_INPUT_PLACEHOLDER = 'AI 출결 입력 (준비 중)';
// 1행 A열은 무엇을 하는 자리인지 알려 주는 이름표, B~M열은 한 칸으로 합친 입력칸이다.
const MONTHLY_ATTENDANCE_AI_INPUT_LABEL = 'AI 출결 입력';
// 입력칸의 실제 값에는 넣지 않는다. B1의 메모에서만 보여 준다.
const MONTHLY_ATTENDANCE_AI_INPUT_HINT =
  '여기에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르세요';
const MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR = '#000000';
const MONTHLY_ATTENDANCE_AI_INPUT_COL = 2;        // B
// 입력칸은 M열까지 이어 붙인다. K에서 끊으면 오른쪽에 짜투리 칸이 남아
// 글 적는 자리처럼 보이지 않는다. 가운데 L열은 숨긴 열이라 화면에는 안 보인다.
const MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL = 13;  // M
// 제목과 자료가 실제로 있는 마지막 열이다. 월 탭은 M열에서 끝낸다.
const MONTHLY_ATTENDANCE_LAST_DATA_COL = 13;      // M (AI 입력 표시까지가 표다)
const STRIPE_END_COL  = 13;      // A(1)~M(13)
const STRIPE_COLOR_WHITE = '#ffffff';
const STRIPE_COLOR_GRAY  = '#bdbdbd'; // 구글 시트 팔레트 '회색' 중앙 톤 근사값
// 날짜 줄무늬는 조건부 서식이다. 행마다 칠한 색은 정렬 때 행을 따라가 날짜와 어긋났다.
// INDIRECT로 A3·A2 기준을 고정해 첫 자료 행을 넣거나 지워도 규칙이 깨지지 않는다.
// attendance_sheet_layout.py의 STRIPE_RULES와 같은 식이다.
const DATE_STRIPE_RULES = Object.freeze([
  Object.freeze({
    formula: '=AND(COUNTA($A3:$M3)>0,ISODD(SUMPRODUCT((INDIRECT("A3:A"&ROW())<>"")'
      + '*(INDIRECT("A3:A"&ROW())<>INDIRECT("A2:A"&(ROW()-1))))))',
    color: STRIPE_COLOR_GRAY
  }),
  Object.freeze({ formula: '=ROW()>=3', color: STRIPE_COLOR_WHITE })
]);
const ATTENDANCE_AI_INTERACTIONS_URL =
  'https://generativelanguage.googleapis.com/v1beta/interactions';
const ATTENDANCE_AI_MODEL = 'gemini-3.5-flash-lite';
// AI가 넣은 줄은 배경색으로 칠하지 않는다. 배경은 날짜 줄무늬가 쓰는 자리라,
// 초록을 덮으면 한 날짜 덩어리가 회색과 초록으로 쪼개져 보인다.
// 대신 M열에 글자로 적는다.
const MONTHLY_ATTENDANCE_AI_MARK_COL = 13;          // M
const MONTHLY_ATTENDANCE_AI_MARK_HEADER = 'AI 입력';
const MONTHLY_ATTENDANCE_AI_MARK_TEXT = 'AI';
const ATTENDANCE_AI_CATEGORIES = Object.freeze(['질병','미인정','기타','출석인정']);
// 새 줄의 구분(C)을 정하지 못했을 때 쓰지 않고 멈추는 안내(SHEET-AI-04).
const ATTENDANCE_AI_CATEGORY_MISSING_MESSAGE =
  '구분(질병·미인정·출석인정·기타)을 정하지 못했어요. 입력칸을 지우고 문장에 구분을 함께 적어 다시 입력해 주세요.';
const ATTENDANCE_AI_KINDS = Object.freeze(['결석함','지각함','조퇴함','결과함']);
const ATTENDANCE_AI_PERIODS = Object.freeze(
  ['','1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례']
);
const ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY =
  'ATTENDANCE_AI_TARGET_SPREADSHEET_ID';
const ATTENDANCE_AI_GEMINI_API_KEY_PROPERTY = 'ATTENDANCE_AI_GEMINI_API_KEY';
// 기록 결과를 확인하지 못한 문장의 지문(문장 글자는 남기지 않는다)을 월 탭별로 둔다.
// 대시보드가 입력칸에 남은 그 문장을 [다시 입력]하기 전에 경고한다(SHEET-DASH-05).
const ATTENDANCE_AI_UNCERTAIN_PROPERTY_PREFIX = 'TM_AI_UNCERTAIN_';
// 컴퓨터의 티처 매니저 연결 화면에 넣은 키가 설정 탭 이 이름으로 들어온다.
// install_attendance_automation.build_config_rows / central_chat._upsert_settings_value와 같은 이름이어야 한다.
const ATTENDANCE_AI_GEMINI_API_KEY_SETTING = 'GEMINI_API_KEY';
// Teacher Manager 정식 출석부의 설정 탭에 적는 값이다. '예'면 이 시트에서
// 1행 AI 입력을 켤 수 있다. 파일 이름은 권한 근거로 사용하지 않는다.
// install_attendance_automation.build_config_rows와 이름·값이 같아야 한다.
const ATTENDANCE_AI_ALLOWED_SETTING = 'ATTENDANCE_AI_ALLOWED';
const ATTENDANCE_AI_ALLOWED_VALUE = '예';
const ATTENDANCE_AI_EDIT_TRIGGER_HANDLER = 'onAttendanceAiEdit';
const ATTENDANCE_AI_SETUP_TITLE = 'AI 출결 입력 연결';
const ATTENDANCE_AI_VERIFICATION_SETTING = 'ATTENDANCE_AI_SETUP_VERIFICATION';
const ATTENDANCE_CONNECTION_CODE_SETTING = 'ATTENDANCE_CONNECTION_CODE';
const ATTENDANCE_AI_VERIFICATION_SCHEMA_VERSION = 1;
const ATTENDANCE_AI_SETUP_VERSION = '1';
const STUDENT_DROPDOWN_SHEET_NAME = '드롭다운';
const STUDENT_DROPDOWN_HEADER = '학생_번호이름';
const STUDENT_DROPDOWN_COLUMN = 10; // J — 드롭다운 시트에서 숨겨 두는 내부 목록
const STUDENT_DROPDOWN_FIRST_ROW = 2;
const STUDENT_DROPDOWN_LAST_ROW = 200;
const STUDENT_DROPDOWN_RANGE = 'J2:J200';

const DEFAULT_CONFIG = Object.freeze({
  SCHOOL_NAME: '',
  // Workbook identity is supplied by the verified installer; a clock never fills it.
  SCHOOL_YEAR: '',
  GRADE: '',
  CLASS_NUMBER: '',
  CLASS_LABEL: FALLBACK_CLASS_LABEL,
  TEACHER_NAME: '',
  TEMPLATE_DOC_ID: '',
  DEST_FOLDER_ID: '',
  DEST_FOLDER_NAME: '출결 증빙',
  TASK_LIST_ID: '',
  TASK_LIST_TITLE: '출결 미제출 확인',
  HOLIDAY_SHEET_NAME: FALLBACK_HOLIDAY_SHEET_NAME,
  ROSTER_SHEET_NAME: '학생명단',
  STUDENT_DROPDOWN_RANGE: STUDENT_DROPDOWN_RANGE,
  TIMEZONE: 'Asia/Seoul',
  MONTH_SHEET_NAMES: DEFAULT_MONTH_SHEETS.join(','),
  HOMEROOM_TASK_LIST_ID: '',
  CENTRAL_CHAT_SENDER_URL: '',
  CENTRAL_CHAT_SHEET_ID: '',
  CENTRAL_CHAT_SHEET_SECRET: '',
  CLASS_CHAT_SPACE_ID: '',
  CLASS_CHAT_SPACE_NAME: '',
  CHAT_LOG_SHEET_NAME: '발송기록',
  SCRIPT_ID: ''
});

const INPUT_HEADERS = ['날짜','번호+이름','구분','종류','사유','교시','신고서','첨부'];
const ROSTER_HEADERS = ['번호','이름','학생 Google 이메일'];
const MESSENGER_PERSONAL_SHEET_NAME = '메신저 개인톡 내용';
const MESSENGER_CLASS_SHEET_NAME = '메신저 단체톡 내용';
const LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES = ['개인톡 내용', '개인 쪽지 대장'];
const LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES = ['단체톡 내용', '단체 쪽지 대장'];
const PERSONAL_MESSAGE_QUEUE_HEADERS = ['보낼 날짜','번호','이름','쪽지 종류','쪽지 내용','들어온 곳','상태','연결 표시','보낸 시각','결과'];
const CLASS_MESSAGE_QUEUE_HEADERS = ['보낼 날짜','안내 종류','안내 내용','들어온 곳','상태','보낸 시각','결과'];
const MONTHLY_CHAT_RESULT_HEADERS = ['Google Chat\n발송상태','Google Chat\n발송시각','Google Chat\n결과','Google Chat\n내용기준'];
const MESSAGE_QUEUE_SOURCES = ['출결표','자동분석','직접입력'];
const MESSAGE_QUEUE_STATUSES = ['대기','발송중','제외','보냄','실패'];
const PERSONAL_MESSAGE_TYPES = ['출결서류','준비물','개별안내','상담/확인','기타'];
const CLASS_MESSAGE_TYPES = ['준비물','제출물','일정','생활지도','기타'];
const CHAT_MESSAGE_LIMIT_BYTES = 30000;

/*************************************************
 * 메뉴
 *************************************************/
function onOpen() {  addChatAttendanceMenu_();
  const ui = SpreadsheetApp.getUi();

  // 메뉴는 '사전 세팅'과 '교사가 직접 실행하는 일'로 가른다.
  // Chat 연결은 출결뿐 아니라 교육청 메신저 발송의 사전 세팅이기도 해서 어느 한쪽 실행
  // 메뉴 밑에 둘 수 없다. 그래서 최상위에 따로 세우고, 사전 세팅은 항목 하나로 합친다.
  ui.createMenu('🔵 처음 한 번 설정하기')
    .addItem('▶ 처음 설정 한 번에 끝내기', 'runFirstTimeSetup')
    .addItem('연결 상태 확인', 'checkCentralChatStatus')
    .addToUi();

  // 최초 연결과 자동 복구는 위의 한 번 설정에서 맡고, 여기에는 평소 실행할 일만 둔다.
  ui.createMenu('출결 업무 자동화')
    // 대시보드가 출석부를 쓰는 기본 화면이라 맨 위에 둔다(사용자 결정 2026-10-01, SHEET-DASH-01).
    .addItem('출결·메신저 대시보드 열기', 'openAttendanceDashboard')
    .addSeparator()
    .addItem('선택 행 출결신고서 Google Docs에 만들기', 'createDocFromTemplate')
    .addItem('선택 행 미제출 서류 Google Tasks에 추가하기', 'addSelectedRowToTasks')
    .addItem('선택 행 미제출 서류 Google Chat 개인톡 보내기', 'sendSelectedRowsChatNow')
    .addSeparator()
    .addItem('날짜별 정렬·음영 다시 맞추기', 'sortActiveMonthByDateAndStripes')
    .addSeparator()
    .addItem('ⓘ 만든 사람 / 버전', 'showAbout')
    .addToUi();

  ui.createMenu('경기도교육청 메신저(Brity) 정리ㆍ발송')
    // 탭 열기 항목은 두지 않는다 — 시트 탭을 누르면 되는 일이라 메뉴 중복이다 (사용자 결정 2026-07-21).
    .addItem('메신저 쪽지 내용 Google Chat으로 개인톡 보내기', 'sendMessengerPersonalMessages')
    .addItem('메신저 쪽지 내용 Google Chat으로 단체톡 보내기', 'sendMessengerClassMessages')
    .addItem('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기', 'sendMessengerAllMessages')
    // 발송 기록 보기·발송 연결 끊기는 메뉴에서 뺐다(사용자 결정 2026-09-29). 함수는 남긴다.
    .addToUi();
}

/*************************************************
 * 처음 한 번 설정하기 — 사전 세팅 네 가지를 한 번에
 *************************************************/

/** 실제 Google Sheet 번호를 노출하지 않고 양쪽 화면에서 대조할 48비트 확인번호를 만든다. */
function attendanceConnectionCodeForSpreadsheetId_(spreadsheetId) {
  const checked = String(spreadsheetId || '').trim();
  if (!checked) return '';
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    checked,
    Utilities.Charset.UTF_8
  );
  if (!bytes || bytes.length < 6) return '';
  const hex = bytes.slice(0, 6).map(function (value) {
    const unsigned = (Number(value) + 256) % 256;
    return ('0' + unsigned.toString(16)).slice(-2);
  }).join('').toUpperCase();
  return 'TM-' + hex.slice(0, 6) + '-' + hex.slice(6);
}

/**
 * 사전 세팅 네 가지를 순서대로 돌리고 결과를 마지막에 한 화면으로 보여 준다.
 *
 * 1행 편집을 알아차리는 설치형 감지기는 시트 안에서 승인된 실행이 있어야 만들어진다.
 * 컴퓨터에서 대신 실행하면 권한 오류가 나고, 시트를 열 때 도는 onOpen은 권한이 제한돼
 * 감지기를 만들 수 없다. 그래서 선생님이 시트에서 누르는 한 번은 구글이 요구하는 것이라
 * 없앨 수 없다. 없앨 수 없다면 그 한 번에 나머지를 모두 태워, 메뉴 세 군데를 돌아다니지
 * 않게 하는 것이 이 함수의 목적이다.
 *
 * 네 단계는 모두 여러 번 눌러도 안전하다. 이미 된 것은 건너뛰고, 하나가 실패해도
 * 나머지는 계속 진행한 뒤 실패한 것만 결과 화면에 적는다.
 */
function runFirstTimeSetup() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  ui.showModalDialog(
    HtmlService.createHtmlOutput(firstTimeSetupProgressHtml_()).setWidth(520).setHeight(480),
    '처음 한 번 설정하기'
  );

  // Keep setup in the authorized menu execution, as before the progress dialog
  // was introduced. The dialog must never start a second browser/server request.
  let result;
  let failure = null;
  try {
    result = runFirstTimeSetupWork();
  } catch (error) {
    failure = error;
    result = firstTimeSetupFailureResult_(error);
  }
  // Consent has opened its own dialog. Leave it visible until the user finishes.
  if (result && result.pending) return;
  ui.showModalDialog(
    HtmlService.createHtmlOutput(firstTimeSetupProgressHtml_(result)).setWidth(520).setHeight(480),
    '처음 한 번 설정하기'
  );
  // Preserve the original exception in Google's execution record after showing
  // safe feedback. Do not turn an unsuccessful execution into a successful one.
  if (failure) throw failure;
}

/** Render status only; all setup work belongs to the original menu execution. */
function firstTimeSetupProgressHtml_(result) {
  const waiting = arguments.length === 0;
  const valid = result && typeof result.message === 'string';
  const title = waiting ? '처음 설정을 진행하고 있어요'
    : !valid ? '설정 결과를 확인해 주세요'
    : result.code ? result.title
    : result.complete ? '처음 설정이 끝났어요' : '남은 설정을 확인해 주세요';
  const message = waiting
    ? '완료 안내가 나올 때까지 이 창과 출결 시트를 닫지 말고 기다려 주세요.\n준비에 시간이 걸릴 수 있습니다. 권한 허용이나 방 선택 안내가 나타나면 따라 주세요.'
    : valid ? result.message
    : '설정 결과 응답을 확인하지 못했어요. Teacher Manager로 돌아가 현재 설정 상태를 확인해 주세요. 처음 설정 메뉴를 바로 다시 누르지 마세요.';
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
    body{font:14px/1.7 sans-serif;color:#24364b;margin:24px}
    h2{font-size:19px;color:#2265b5;margin:0 0 14px}
    #message{white-space:pre-wrap;overflow-wrap:anywhere}
    #spinner{width:24px;height:24px;border:3px solid #dbeafe;border-top-color:#2563eb;border-radius:50%;animation:spin 1s linear infinite;margin-bottom:16px}
    button{background:#e8f2ff;color:#1760b5;border:0;border-radius:8px;padding:10px 18px;margin-top:16px;cursor:pointer}
    @keyframes spin{to{transform:rotate(360deg)}}
  </style></head><body>
    <div id="spinner" aria-hidden="true"${waiting ? '' : ' hidden'}></div>
    <h2 id="title">${escapeHtml_(title)}</h2>
    <div id="message" role="status" aria-live="polite">${escapeHtml_(message)}</div>
    <button id="close"${waiting ? ' hidden' : ''} onclick="google.script.host.close()">확인</button>
  </body></html>`;
}

function firstTimeSetupFailureResult_(error) {
  let cause = String(error && error.name || '') + ' ' + String(error && error.message || error || '');
  let title = '처음 설정의 완료 여부를 확인하지 못했어요';
  let message = 'Teacher Manager로 돌아가 현재 설정 상태를 확인해 주세요. 일부 작업은 이미 끝났을 수 있으므로 처음 설정 메뉴를 바로 다시 누르지 마세요.';
  let code = 'SETUP_RESULT_UNKNOWN';
  if (/Authorization is required|Required permissions|insufficient authentication scopes|권한 부여가 필요|승인이 필요/i.test(cause)) {
    title = 'Google 권한 허용이 더 필요해요';
    message = 'Google에서 필요한 권한이 아직 허용되지 않았다고 응답했어요. 권한 요청 화면의 선택 항목을 확인하고 허용을 마쳐 주세요. 이미 끝난 설정은 Teacher Manager에서 먼저 확인해 주세요.';
    code = 'SETUP_AUTHORIZATION_REQUIRED';
  } else if (/exceeded maximum execution time|maximum execution time|execution timed out|실행 시간이 초과/i.test(cause)) {
    title = '설정 처리 결과를 확인해 주세요';
    message = 'Google에서 한 번에 실행할 수 있는 시간이 끝났어요. 일부 설정은 저장되었을 수 있어요. Teacher Manager로 돌아가 현재 상태를 확인하고, 처음 설정 메뉴를 바로 다시 누르지 마세요.';
    code = 'SETUP_EXECUTION_TIMEOUT';
  } else if (/NetworkError|Failed to fetch|Network error|Error communicating|Service unavailable|서버와.*통신/i.test(cause)) {
    title = '설정 결과 응답을 받지 못했어요';
    message = '응답을 받지 못해 설정이 끝났는지 아직 알 수 없어요. 잠시 뒤 Teacher Manager에서 상태를 확인해 주세요. 설정 메뉴를 다시 누르면 작업이 겹칠 수 있어요.';
    code = 'SETUP_RESPONSE_UNAVAILABLE';
  } else if (/TypeError|ReferenceError|SyntaxError|is not defined|is not a function|Script function not found/i.test(cause)) {
    title = '처음 설정 중 프로그램 오류가 발생했어요';
    message = '설정 프로그램을 실행하는 중 오류가 발생했어요. 계정 변경이나 권한 재허용으로 해결된다고 판단할 수 없어요. 아래 확인 코드를 함께 알려 주세요.';
    code = 'SETUP_PROGRAM_ERROR';
  } else if (/Access denied|PERMISSION_DENIED|permission denied/i.test(cause)) {
    title = 'Google에서 설정 요청을 허용하지 않았어요';
    message = '접근이 거절된 정확한 항목을 실행 기록에서 확인해야 해요. 계정을 바꾸거나 설정을 반복하기 전에 아래 확인 코드를 함께 알려 주세요.';
    code = 'SETUP_ACCESS_DENIED';
  }
  return { title: title, message: message + '\n\n오류 확인 코드: ' + code, code: code, complete: false };
}

function readAttendanceConfigStrict_(spreadsheet) {
  const sheet = spreadsheet.getSheetByName(CONFIG_SHEET_NAME);
  if (!sheet) throw new Error('출석부 설정을 확인하지 못했습니다. 기존 자료는 그대로 두었습니다.');
  const rows = sheet.getDataRange().getValues();
  if (!Array.isArray(rows)) throw new Error('출석부 설정 응답을 확인하지 못했습니다.');
  const config = Object.create(null);
  rows.forEach(row => {
    if (!Array.isArray(row)) throw new Error('출석부 설정 응답을 확인하지 못했습니다.');
    const key = String(row[0] || '').trim();
    if (!key) return;
    if (Object.prototype.hasOwnProperty.call(config, key)) {
      throw new Error('출석부 설정에 같은 항목이 두 번 있어 자동 처리를 멈췄습니다.');
    }
    config[key] = row[1];
  });
  return config;
}

function parseAttendanceMonthSheetIds_(value) {
  let result = value;
  if (typeof value === 'string') {
    const keys = value.match(/"(?:\\.|[^"\\])*"\s*:/g) || [];
    if (keys.length !== 12) throw new Error('월별 출결표 연결 기록을 확인하지 못했습니다.');
    try { result = JSON.parse(value); } catch (err) {
      throw new Error('월별 출결표 연결 기록을 읽지 못했습니다.');
    }
  }
  if (!result || typeof result !== 'object' || Array.isArray(result)
      || Object.keys(result).length !== 12) {
    throw new Error('월별 출결표 연결 기록이 없습니다. 빈 월별 탭을 만들지 않았습니다.');
  }
  const checked = {};
  const seen = new Set();
  for (let month = 1; month <= 12; month++) {
    const id = result[String(month)];
    if (!Number.isSafeInteger(id) || id < 0 || id > 2147483647 || seen.has(id)) {
      throw new Error('월별 출결표 연결 기록이 서로 겹치거나 올바르지 않습니다.');
    }
    checked[String(month)] = id;
    seen.add(id);
  }
  return checked;
}

function attendanceMonthSheetsFor_(spreadsheet, config) {
  const manifest = parseAttendanceMonthSheetIds_(config.ATTENDANCE_MONTH_SHEET_IDS);
  // A renamed monthly tab must never become an auxiliary tab that setup or
  // queue helpers may rewrite. This is a role collision, not title identity.
  const reservedNames = new Set(['설정','드롭다운','학생명단','휴일','템플릿_치환표','발송기록','00_사용법',
    '메신저 개인톡 내용','메신저 단체톡 내용','개인톡 내용','개인 쪽지 대장','단체톡 내용','단체 쪽지 대장',
    config.ROSTER_SHEET_NAME,config.HOLIDAY_SHEET_NAME,config.CHAT_LOG_SHEET_NAME].filter(Boolean));
  const sheets = spreadsheet.getSheets();
  const byId = new Map();
  sheets.forEach(sheet => {
    const id = sheet.getSheetId();
    if (byId.has(id)) throw new Error('월별 출결표 응답이 중복되어 멈췄습니다.');
    byId.set(id, sheet);
  });
  return [3,4,5,6,7,8,9,10,11,12,1,2].map(month => {
    const sheet = byId.get(manifest[String(month)]);
    if (!sheet) throw new Error('연결된 월별 출결표를 찾지 못했습니다. 빈 탭을 만들지 않았습니다.');
    if (reservedNames.has(sheet.getName())) throw new Error('월별 출결표와 다른 업무 탭의 역할이 겹쳐 기존 자료를 보존하고 멈췄습니다.');
    return { month: month, sheet: sheet };
  });
}

function attendanceMonthLayoutKind_(sheet) {
  const rows = sheet.getRange(1, 1, 2, 13).getValues();
  if (!Array.isArray(rows) || rows.length !== 2 || rows.some(row => !Array.isArray(row))) {
    throw new Error('월별 출결표의 제목을 읽지 못했습니다.');
  }
  const matches = row => INPUT_HEADERS.every((value, index) => String(row[index] || '').trim() === value);
  const kind = matches(rows[1]) ? 'current' : matches(rows[0]) ? 'one-header-row' : '';
  if (!kind) throw new Error('알 수 없는 출결표 구조입니다. 기존 내용을 덮어쓰지 않았습니다.');
  const header = kind === 'current' ? rows[1] : rows[0];
  MONTHLY_CHAT_RESULT_HEADERS.concat([MONTHLY_ATTENDANCE_AI_MARK_HEADER]).forEach((label, index) => {
    const value = String(header[index + 8] || '').replace(/\s+/g, ' ').trim();
    const allowed = ['', label.replace(/\s+/g, ' ').trim()];
    if (index < 4) allowed.push(['Google Chat 발송상태','Google Chat 시도시각','Google Chat 결과','Google Chat 내용기준'][index]);
    if (allowed.indexOf(value) < 0) throw new Error('출결표에 직접 추가한 제목이 있어 덮어쓰지 않았습니다.');
  });
  if (kind === 'current' && (rows[0].slice(2).some(value => value !== '' && value !== null)
      || ['', MONTHLY_ATTENDANCE_AI_INPUT_LABEL, MONTHLY_ATTENDANCE_AI_INPUT_PLACEHOLDER].indexOf(String(rows[0][0] || '')) < 0)) {
    throw new Error('출결 입력칸에 기존 내용이 있어 합치지 않았습니다.');
  }
  return kind;
}

function prepareAttendanceMonthManifest_(spreadsheet, config) {
  if (config.ATTENDANCE_MONTH_SHEET_IDS) return attendanceMonthSheetsFor_(spreadsheet, config);
  if (config.FIRST_TIME_SETUP_DONE || config.FIRST_TIME_SETUP_SHEET_DONE) {
    throw new Error('기존 출석부의 월별 연결 기록을 먼저 복구해야 합니다. 빈 탭을 만들지 않았습니다.');
  }
  // Only an explicit initial setup may bootstrap the intact shipped template.
  // Inspect all twelve roles before writing a manifest or touching any cells.
  const manifest = {};
  const months = [3,4,5,6,7,8,9,10,11,12,1,2].map(month => {
    const sheet = spreadsheet.getSheetByName(String(month) + '월');
    if (!sheet) throw new Error('처음 출석부의 월별 탭이 없습니다. 빈 탭을 만들지 않았습니다.');
    attendanceMonthLayoutKind_(sheet);
    manifest[String(month)] = sheet.getSheetId();
    return {month: month, sheet: sheet};
  });
  parseAttendanceMonthSheetIds_(manifest);
  setConfigValue_('ATTENDANCE_MONTH_SHEET_IDS', JSON.stringify(manifest));
  SpreadsheetApp.flush();
  const reread = parseAttendanceMonthSheetIds_(readAttendanceConfigStrict_(spreadsheet).ATTENDANCE_MONTH_SHEET_IDS);
  if (JSON.stringify(reread) !== JSON.stringify(parseAttendanceMonthSheetIds_(manifest))) {
    throw new Error('월별 연결 기록의 저장 결과를 확인하지 못했습니다.');
  }
  return months;
}

function attendanceSetupMarkerMatches_(marker, spreadsheetId, account) {
  const parts = String(marker || '').trim().split(/\s+/);
  return parts.length >= 3 && parts[0] === attendanceConnectionCodeForSpreadsheetId_(spreadsheetId)
    && !!account && parts[1].toLowerCase() === String(account).trim().toLowerCase();
}

function attendanceWorkbookHealthFor_(spreadsheet, expectedAccount) {
  const result = { setupCompletionEvidence: false, currentReadState: 'unavailable',
    structureClassification: 'unknown', automationState: 'unverified', recoveryAction: 'diagnose-workbook' };
  try {
    const config = readAttendanceConfigStrict_(spreadsheet);
    const setupMarker = config.FIRST_TIME_SETUP_SHEET_DONE || config.FIRST_TIME_SETUP_DONE;
    result.setupMarkerPresent = !!String(setupMarker || '').trim();
    result.setupCompletionEvidence = attendanceSetupMarkerMatches_(setupMarker, spreadsheet.getId(), expectedAccount);
    if (result.setupMarkerPresent && !result.setupCompletionEvidence) {
      result.recoveryAction = 'verify-setup-account';
      throw new Error('기존 설정 완료 기록의 계정 또는 출석부가 현재 대상과 다릅니다. 기존 설정을 보존했습니다.');
    }
    const year = String(config.SCHOOL_YEAR || '').trim();
    if (!/^\d{4}$/.test(year)) throw new Error('이 출석부의 학년도를 확인하지 못했습니다.');
    result.workbookSchoolYear = Number(year);
    const months = attendanceMonthSheetsFor_(spreadsheet, config);
    const kinds = months.map(item => attendanceMonthLayoutKind_(item.sheet));
    result.currentReadState = 'verified';
    result.structureClassification = kinds.every(kind => kind === 'current') ? 'current' : 'repairable';
    result.recoveryAction = result.structureClassification === 'current' ? '' : 'repair-layout';
    result.monthlySheetIds = parseAttendanceMonthSheetIds_(config.ATTENDANCE_MONTH_SHEET_IDS);
  } catch (err) {
    result.detail = String(err && err.message || '현재 출석부 상태를 확인하지 못했습니다.');
  }
  return result;
}

function apiAttendanceWorkbookHealth() {
  const account = requireGoeduTeacherAccount_();
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const health = attendanceWorkbookHealthFor_(spreadsheet, account);
  health.aiReadState = 'unavailable';
  health.aiReady = false;
  if (health.currentReadState === 'verified') {
    try {
      const authority = authorizeAttendanceOperation_('health', '');
      health.bindingVerified = true;
      health.generation = authority.generation;
      health.currentSchoolYear = authority.currentSchoolYear;
      const ai = attendanceAiSetupStatusFor_(spreadsheet, account, spreadsheet);
      health.aiReady = ai.ok === true;
      // The legacy AI inspector intentionally suppresses some read exceptions;
      // only its fully positive result is current readiness evidence.
      health.aiReadState = health.aiReady ? 'verified' : 'unavailable';
      health.automationState = health.setupCompletionEvidence
        && health.structureClassification === 'current' && health.aiReady
        && authority.automationState === 'AUTHORIZED' ? 'ready' : 'blocked';
      if (health.automationState !== 'ready') {
        health.recoveryAction = !health.setupCompletionEvidence ? 'complete-initial-setup'
          : !health.aiReady ? 'repair-ai-input' : 'check-current-connection';
      }
    } catch (err) {
      if (health.bindingVerified !== true) health.bindingVerified = false;
      health.automationState = 'blocked';
      health.recoveryAction = 'check-current-connection';
      health.detail = String(err && err.message || '현재 처리 권한을 확인하지 못했습니다.');
    }
  }
  return health;
}

function runFirstTimeSetupWork() {
  const setupAccount = String(requireGoeduTeacherAccount_() || '').trim();
  const previousHealth = attendanceWorkbookHealthFor_(SpreadsheetApp.getActiveSpreadsheet(), setupAccount);
  if (previousHealth.setupMarkerPresent) {
    return { title: '처음 한 번 설정하기', complete: false,
      sheetReady: previousHealth.currentReadState === 'verified' && previousHealth.structureClassification === 'current',
      setupCompletionEvidence: previousHealth.setupCompletionEvidence, currentHealth: previousHealth,
      message: !previousHealth.setupCompletionEvidence
        ? '기존 설정 완료 기록의 계정 또는 출석부를 확인해야 합니다. 처음 설정을 다시 실행하지 않았습니다.'
        : previousHealth.currentReadState === 'verified'
        ? '처음 시트 설정은 이미 마쳤습니다. Teacher Manager에서 현재 연결 상태를 확인합니다.'
        : '처음 시트 설정을 마친 기록은 있습니다. 현재 상태를 확인하지 못해 설정을 다시 실행하지 않았습니다.' };
  }
  const steps = [
    { title: '기본 시트/설정 점검', run: firstTimeSetupWorkbookStep_ },
    { title: 'AI 출결 입력 켜기', run: firstTimeSetupAiStep_ },
    { title: 'Google Chat 최초 발송 연결', run: firstTimeSetupChatStep_ },
    { title: 'Google Chat 학급 단톡방 고르기', run: firstTimeSetupClassSpaceStep_ }
  ];

  // 뒤 단계가 앞 단계의 결과를 알아야 하는 곳이 있다 — 연결이 끝나야 단톡방 목록을 받는다.
  const context = { chatReady: false, sheetReady: false, chatPending: false };
  const lines = [];
  const leftovers = [];
  let connectionCode = '';
  let resultUncertain = false;

  steps.forEach(function (step, index) {
    let result;
    try {
      result = context.workbookFailed
        ? { ok: false, message: '기본 시트 준비가 끝나지 않아 이 단계는 시작하지 않았습니다.' }
        : step.run(context);
    } catch (err) {
      // 기본 시트 실패는 종속 단계를 멈춘다. AI 실패 뒤에도 독립적인 Chat 확인은 이어 간다.
      resultUncertain = true;
      result = { ok: false, message: sheetFacingErrorMessage_(err, step.title) };
    }
    result = result || { ok: false, message: '결과를 확인하지 못했습니다.' };
    if (index === 0 && result.ok !== true) context.workbookFailed = true;
    const mark = result.ok === true ? (result.skipped === true ? '[이미]' : '[됨]') : '[못 함]';
    lines.push(mark + ' ' + step.title + (result.message ? ' — ' + firstTimeSetupOneLine_(result.message) : ''));
    if (result.ok !== true) leftovers.push(step.title + '\n' + String(result.message || ''));
    if (result.pending === true) context.chatPending = true;

    // Save Sheet prerequisites before consent; Chat and room selection can continue in TM.
    // Keep the legacy all-four marker separate so older apps cannot mistake partial setup for completion.
    if (index === 1 && leftovers.length === 0) {
      try {
        const spreadsheetId = String(SpreadsheetApp.getActiveSpreadsheet().getId() || '').trim();
        connectionCode = attendanceConnectionCodeForSpreadsheetId_(spreadsheetId);
        if (!connectionCode) throw new Error('지금 열린 출석부의 연결 확인번호를 읽지 못했습니다.');
        setConfigValue_(ATTENDANCE_CONNECTION_CODE_SETTING, connectionCode);
        setConfigValue_('FIRST_TIME_SETUP_SHEET_DONE',
          (connectionCode + ' ' + setupAccount + ' ' + new Date().toISOString()).trim());
        SpreadsheetApp.flush();
        context.sheetReady = true;
      } catch (err) {
        resultUncertain = true;
        const connectionError = sheetFacingErrorMessage_(err, '출석부 연결 저장');
        lines.push('[못 함] 출석부 연결 저장 — ' + connectionError);
        leftovers.push('출석부 연결 저장\n' + connectionError);
      }
    }
  });

  // The authorization dialog is asynchronous; a summary alert would cover it before consent.
  if (context.sheetReady && context.chatPending) return { pending: true };

  // 확인번호까지 준비됐을 때만 완료 표시를 적는다 — 설치 프로그램이 이 값을 읽어
  // 마법사의 [다음]을 켠다. 일부 실패면 적지 않는다(거짓 완료 방지).
  if (leftovers.length === 0) {
    try {
      // 완료 표시는 현재 Sheet에서 만든 확인번호에 묶는다. Sheet를 복사해 옛 완료
      // 값이 따라와도, 새 Sheet의 확인번호나 처음 통과한 학교 계정과 다르면
      // Teacher Manager가 완료로 믿지 않는다.
      setConfigValue_(
        'FIRST_TIME_SETUP_DONE',
        (connectionCode + ' ' + setupAccount + ' ' + new Date().toISOString()).trim()
      );
      SpreadsheetApp.flush();
    } catch (err) {
      // 네 단계가 끝났어도 표시가 없으면 프로그램의 [다음]이 계속 잠긴다.
      // 저장 결과가 불확실하므로 재실행을 권하지 않는다.
      resultUncertain = true;
      const markerError = sheetFacingErrorMessage_(err, '처음 설정 완료 여부 저장');
      lines.push('[못 함] 처음 설정 완료 여부 저장 — ' + markerError);
      leftovers.push('처음 설정 완료 여부 저장\n' + markerError);
    }
  }

  const chatOnlyPending = context.sheetReady && leftovers.length > 0
    && leftovers.every(item => item.indexOf('Google Chat ') === 0);
  const closing = resultUncertain
    ? '결과를 확인하지 못한 설정이 있어요. 아래 안내를 확인하고, 같은 작업을 다시 실행하기 전에 문의해 주세요.\n\n' + leftovers.join('\n\n')
    : context.chatCheckUncertain && context.sheetReady
    ? '시트 설정은 끝났습니다. Google Chat 연결 상태를 확인하지 못해 기존 연결을 바꾸지 않았습니다.\n' +
      'Teacher Manager로 돌아가 연결 상태를 확인해 주세요. 이 시트 메뉴는 다시 누르지 않아도 됩니다.'
    : chatOnlyPending
    ? '시트 설정은 끝났습니다. Teacher Manager로 돌아가 Google Chat 연결과 학급 단톡방 선택을 마쳐 주세요.\n' +
      '이 메뉴는 다시 누르지 않아도 됩니다.\n\n아직 남은 것\n\n' + leftovers.join('\n\n')
    : leftovers.length
    ? '아직 남은 것\n\n' + leftovers.join('\n\n') + '\n\n' +
      '위 안내대로 마친 뒤 [처음 한 번 설정하기 → 처음 설정 한 번에 끝내기]를 다시 누르면 됩니다.\n' +
      '이미 끝난 것은 건너뛰니 여러 번 눌러도 안전합니다.'
    : '네 가지가 모두 준비됐습니다. 이 메뉴는 다시 누르지 않아도 됩니다.\n\n' +
      '연결 확인번호: ' + connectionCode + '\n' +
      'Teacher Manager에 보이는 번호와 같은지 확인해 주세요.';

  // Report roster readiness with the other results, without changing saved setup checkpoints.
  const roster = firstTimeSetupRosterStep_();
  const rosterMark = roster.ok ? '[됨]' : roster.unavailable ? '[확인 필요]' : '[안 됨]';
  lines.push(rosterMark + ' 학생명단 입력 — ' + roster.message);
  return {
    title: '처음 한 번 설정하기',
    message: lines.join('\n') + '\n\n' + closing,
    complete: leftovers.length === 0 && roster.ok,
    sheetReady: context.sheetReady
  };
}

function firstTimeSetupRosterStep_() {
  try {
    const name = String(readConfigValueReadOnly_('ROSTER_SHEET_NAME') || '학생명단').trim();
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
    if (!sheet || sheet.getLastRow() < 1) throw new Error('Roster unavailable');
    const rows = sheet.getRange(1, 1, sheet.getLastRow(), 3).getDisplayValues();
    const header = rows.shift();
    if (header[0] !== '번호' || header[1] !== '이름' || String(header[2]).indexOf('이메일') < 0) {
      throw new Error('Roster layout unavailable');
    }
    const students = rows.map(row => row.map(value => String(value || '').trim()))
      .filter(row => row.some(Boolean));
    const numbers = new Set();
    const emails = new Set();
    const complete = students.length > 0 && students.every(function (row) {
      const number = /^[0-9]+$/.test(row[0]) ? row[0].replace(/^0+/, '') : '';
      const email = row[2].toLowerCase();
      if (!number || !row[1] || !isExactGoeduEmail_(email)
          || numbers.has(number) || emails.has(email)) return false;
      numbers.add(number);
      emails.add(email);
      return true;
    });
    return {
      ok: complete,
      message: complete
        ? students.length + '명의 번호·이름·학생 이메일을 확인했습니다. 우리 반 학생이 모두 있는지도 확인해 주세요.'
        : '[학생명단] 탭에 번호·이름·학생 이메일을 빠짐없이 입력하고, 중복이나 잘못된 값이 없는지 확인해 주세요.'
    };
  } catch (_) {
    return { ok: false, unavailable: true, message: '학생명단을 읽지 못했습니다. Teacher Manager의 [학생명단 입력]에서 확인해 주세요.' };
  }
}

/** 결과 목록 한 줄에는 첫 문장만 싣는다 — 자세한 안내는 아래 '아직 남은 것'에 그대로 나온다. */
function firstTimeSetupOneLine_(message) {
  const first = String(message || '').split('\n')[0].trim();
  return first.length > 60 ? first.slice(0, 60) + '…' : first;
}

/** 1단계 — 기본 시트·드롭다운 정리. 여러 번 돌려도 같은 상태가 되므로 건너뛰기 판단을 두지 않는다. */
function firstTimeSetupWorkbookStep_() {
  setupAttendanceWorkbookCore_();
  return { ok: true, message: '월별 시트·학생명단·설정 시트를 확인했습니다.' };
}

/** 2단계 — 맨 위 입력칸의 AI 출결 입력 켜기. */
function firstTimeSetupAiStep_() {
  // 켤 수 있는 사본인지 먼저 본다. 아닌 시트에서도 나머지 단계는 계속하되 전체 완료로 적지는 않는다.
  let state = null;
  try {
    state = attendanceAiWorkbookState_();
  } catch (err) {
    state = null;
  }
  if (!state || state.ok !== true) {
    return {
      ok: false,
      message: state && state.message ? state.message : 'AI 입력을 켤 수 있는 사본이 아닙니다.'
    };
  }
  const result = enableAttendanceAiInput({ quiet: true }) || { ok: false, message: '결과를 확인하지 못했습니다.' };
  // 이미 준비된 경우는 새로 켠 경우와 구분해 보여 준다.
  if (result.ok === true && result.created !== true) result.skipped = true;
  return result;
}

/** 3단계 — Google Chat 최초 발송 연결. 권한 허용 화면은 사람이 눌러야 끝난다. */
function firstTimeSetupChatStep_(context) {
  let status = null;
  try {
    status = callCentralChatSender_('/v1/status', {});
  } catch (err) {
    // A failed read does not establish that existing consent is missing.
    status = null;
  }
  if (!status || typeof status.connected !== 'boolean') {
    if (context) context.chatCheckUncertain = true;
    return {
      ok: false,
      message: '연결 상태를 확인하지 못해 기존 연결을 바꾸지 않았습니다. Teacher Manager로 돌아가 현재 상태를 확인해 주세요.'
    };
  }
  if (status.connected === true) {
    if (context) context.chatReady = true;
    return {
      ok: true,
      skipped: true,
      message: '이미 연결되어 있습니다' + (status.account ? ': ' + status.account : '.')
    };
  }
  return startCentralChatConnection({ quiet: true, sheetSetupReady: !!(context && context.sheetReady) })
    || { ok: false, message: '결과를 확인하지 못했습니다.' };
}

/** 4단계 — 학급 단톡방 고르기. 목록에서 고르는 화면은 사람이 눌러야 끝난다. */
function firstTimeSetupClassSpaceStep_(context) {
  if (context && context.chatCheckUncertain) {
    return { ok: false, message: '연결 상태가 확인되면 Teacher Manager에서 단톡방을 확인해 주세요. 기존 선택은 그대로입니다.' };
  }
  if (context && context.chatReady !== true) {
    // 연결 전에는 단톡방 목록 자체를 받아올 수 없어, 물어봐야 실패만 한다.
    return {
      ok: false,
      message: 'Google Chat 최초 발송 연결을 먼저 마쳐야 단톡방 목록을 받아올 수 있습니다.'
    };
  }
  const spaceId = readConfigValueReadOnly_('CLASS_CHAT_SPACE_ID');
  if (spaceId) {
    const spaceName = readConfigValueReadOnly_('CLASS_CHAT_SPACE_NAME');
    return { ok: true, skipped: true, message: '이미 고른 단톡방이 있습니다: ' + (spaceName || spaceId) };
  }
  return connectClassChatSpace({ quiet: true }) || { ok: false, message: '결과를 확인하지 못했습니다.' };
}

function showAbout() {
  SpreadsheetApp.getUi().alert(
    `${APP_NAME}\n` +
    `버전: ${APP_VERSION}\n` +
    `제작: ${APP_AUTHOR_NAME}\n\n` +
    '기존 Google Docs 신고서 템플릿을 그대로 복사해 문서를 생성합니다.\n' +
    '입력표는 월별 시트 A~H 구조를 유지합니다.\n\n' +
    `저장소: ${APP_REPO_URL}`
  );
}

/*************************************************
 * 기본 세팅/설정/드롭다운
 *************************************************/
function setupAttendanceWorkbook() {
  requireGoeduTeacherAccount_();
  setupAttendanceWorkbookCore_();

  SpreadsheetApp.getUi().alert(
    '기본 시트/설정 점검 완료.\n\n' +
    '- 월별 입력 시트는 맨 앞에 정렬했습니다.\n' +
    '- 학생명단 A열 번호와 B열 이름을 합쳐 월별 시트 B열 드롭다운을 연결했습니다.\n' +
    '- 신고서 템플릿 문서 ID는 설치 도우미가 설정 시트에 자동으로 입력합니다.'
  );
}

// LLM/설치 도우미가 Apps Script API로 UI 없이 실행하는 진입점.
function apiSetupAttendanceWorkbook() {
  requireGoeduTeacherAccount_();
  setupAttendanceWorkbookCore_();
  return 'ok';
}

// Teacher Manager가 기존 출결 기능을 갱신한 직후 부르는 좁은 정리 작업.
// 새 시트는 만들지 않고, 이미 있는 학생명단의 옛 4칸 배치만 3칸으로 옮긴다.
function apiMigrateRosterLayoutAfterUpdate() {
  requireGoeduTeacherAccount_();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const cfg = getConfig_();
  const rosterName = cfg.ROSTER_SHEET_NAME || '학생명단';
  if (!ss.getSheetByName(rosterName)) {
    throw new Error('학생명단 시트를 찾을 수 없습니다.');
  }
  if (!ss.getSheetByName(STUDENT_DROPDOWN_SHEET_NAME)) {
    throw new Error('드롭다운 시트를 찾을 수 없습니다.');
  }
  ensureRosterSheet_(ss);
  syncStudentDropdownValues_(ss, cfg);
  applyStudentDropdowns_(ss, cfg);
  if (ss.getSheetByName('00_사용법')) ensureUsageSheet_(ss);
  return 'ok';
}

function setupAttendanceWorkbookCore_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const initial = readAttendanceConfigStrict_(ss);
  if (!/^\d{4}$/.test(String(initial.SCHOOL_YEAR || '').trim())) {
    throw new Error('이 출석부의 학년도를 확인하지 못했습니다. 현재 연도로 대신 설정하지 않았습니다.');
  }
  const months = prepareAttendanceMonthManifest_(ss, initial);
  months.forEach(item => attendanceMonthLayoutKind_(item.sheet));
  ensureConfigSheet_(ss);
  recordScriptIdInConfig_();
  ensureCentralChatConfig_();
  const cfg = getConfig_();
  const monthNames = months.map(item => item.sheet.getName());

  months.forEach(item => ensureMonthSheet_(ss, item.sheet));
  ensureRosterSheet_(ss);
  ensureHolidaySheet_(ss);
  ensureDropdownSheet_(ss);
  ensureTemplateMapSheet_(ss);
  ensurePersonalMessageQueueSheet_(ss);
  ensureClassMessageQueueSheet_(ss);
  ensureChatLogSheet_(ss);
  ensureUsageSheet_(ss);
  moveSheetsInOrder_(ss, monthNames.concat([
    CONFIG_SHEET_NAME,
    cfg.ROSTER_SHEET_NAME || '학생명단',
    MESSENGER_PERSONAL_SHEET_NAME,
    MESSENGER_CLASS_SHEET_NAME,
    cfg.HOLIDAY_SHEET_NAME || '휴일',
    '드롭다운',
    '템플릿_치환표',
    cfg.CHAT_LOG_SHEET_NAME || '발송기록',
    '00_사용법'
  ]));

  months.forEach(item => {
    applyInputSheetFormatting_(item.sheet);
    ensureMonthlyChatResultColumns_(item.sheet);
  });
  applyStudentDropdowns_(ss, cfg);
}

function ensureConfigSheet_(ss) {
  let sh = ss.getSheetByName(CONFIG_SHEET_NAME);
  if (!sh) sh = ss.insertSheet(CONFIG_SHEET_NAME);

  sh.getRange(1, 1, 1, 4).setValues([
    ['자동 처리용 설정 — 프로그램이 채웁니다','값','설명','예시/필수']
  ]);

  const existing = readConfigMapFromSheet_(sh);
  const rows = [
    ['SCHOOL_NAME', DEFAULT_CONFIG.SCHOOL_NAME, '신고서 양식의 학교명 자리에 들어갈 이름입니다.', '예: ○○중학교 / 필수'],
    ['SCHOOL_YEAR', DEFAULT_CONFIG.SCHOOL_YEAR, '출결표에서 사용하는 학년도입니다. 신고서 날짜 연도는 출결 날짜에 맞춰 계산합니다.', '예: 2026'],
    ['GRADE', DEFAULT_CONFIG.GRADE, '신고서 양식에 학년을 자동으로 채울 때 사용합니다.', '예: 2'],
    ['CLASS_NUMBER', DEFAULT_CONFIG.CLASS_NUMBER, '신고서 양식에 반을 자동으로 채울 때 사용합니다.', '예: 2'],
    ['CLASS_LABEL', DEFAULT_CONFIG.CLASS_LABEL, '신고서 파일명과 할 일 제목에 쓰는 학년·반 표시입니다.', '예: 2-2'],
    ['TEACHER_NAME', DEFAULT_CONFIG.TEACHER_NAME, '신고서 양식에 담임 이름을 자동으로 채울 때 사용합니다.', '예: 홍길동'],
    ['TEMPLATE_DOC_ID', '', '신고서 양식 연결입니다. Teacher Manager가 자동으로 채웁니다.', '자동 입력 / 필수'],
    ['DEST_FOLDER_ID', '', '완성한 신고서를 저장할 Google Drive 폴더 연결입니다. Teacher Manager가 자동으로 채웁니다.', '자동 입력 / 필수'],
    ['DEST_FOLDER_NAME', DEFAULT_CONFIG.DEST_FOLDER_NAME, '출력 폴더 자동 생성 시 사용할 폴더명입니다.', '출결 증빙'],
    ['TASK_LIST_ID', '', '출결 미제출 할 일을 저장할 Google Tasks 목록 연결입니다. Teacher Manager가 자동으로 채웁니다.', '자동 입력 / 할 일 사용 시 필수'],
    ['TASK_LIST_TITLE', DEFAULT_CONFIG.TASK_LIST_TITLE, '출결 미제출 할 일을 모아 둘 목록 이름입니다.', '출결 미제출 확인'],
    ['HOLIDAY_SHEET_NAME', DEFAULT_CONFIG.HOLIDAY_SHEET_NAME, '수업일 계산에서 제외할 휴일 시트 이름입니다.', '휴일'],
    ['ROSTER_SHEET_NAME', DEFAULT_CONFIG.ROSTER_SHEET_NAME, '학생명단 시트입니다. A열 번호, B열 이름, C열 학생 Google 이메일을 씁니다.', '학생명단'],
    ['STUDENT_DROPDOWN_RANGE', DEFAULT_CONFIG.STUDENT_DROPDOWN_RANGE, '월별 출결표의 학생 선택목록을 만드는 자리입니다.', '프로그램이 자동으로 관리'],
    ['TIMEZONE', DEFAULT_CONFIG.TIMEZONE, '날짜 표시 시간대입니다.', 'Asia/Seoul'],
    ['MONTH_SHEET_NAMES', DEFAULT_CONFIG.MONTH_SHEET_NAMES, '자동화 대상 월별 입력 시트 이름입니다.', DEFAULT_CONFIG.MONTH_SHEET_NAMES],
    ['HOMEROOM_TASK_LIST_ID', DEFAULT_CONFIG.HOMEROOM_TASK_LIST_ID, '조종례 안내를 저장할 담임학급 Google Tasks 목록 연결입니다.', '담임일 때 자동 입력'],
    ['CENTRAL_CHAT_SENDER_URL', DEFAULT_CONFIG.CENTRAL_CHAT_SENDER_URL, 'Google Chat 자동 발송에 필요한 연결 정보입니다.', '프로그램이 자동으로 관리'],
    ['CENTRAL_CHAT_SHEET_ID', DEFAULT_CONFIG.CENTRAL_CHAT_SHEET_ID, '이 출석부의 Google Chat 연결 정보입니다.', '프로그램이 자동으로 관리'],
    ['CENTRAL_CHAT_SHEET_SECRET', DEFAULT_CONFIG.CENTRAL_CHAT_SHEET_SECRET, '이 출석부의 Google Chat 연결을 보호하는 값입니다.', '프로그램이 자동으로 관리'],
    ['CLASS_CHAT_SPACE_ID', DEFAULT_CONFIG.CLASS_CHAT_SPACE_ID, '학급 쪽지를 보낼 Google Chat 단톡방 연결입니다. Teacher Manager에서 학급 단톡방을 고르면 채워집니다.', '자동 입력'],
    ['CLASS_CHAT_SPACE_NAME', DEFAULT_CONFIG.CLASS_CHAT_SPACE_NAME, '선생님이 알아볼 학급 Chat 방 이름입니다.', '예: 2학년 3반'],
    ['CHAT_LOG_SHEET_NAME', DEFAULT_CONFIG.CHAT_LOG_SHEET_NAME, '교육청 메신저 발송 기록 시트 이름입니다.', '발송기록'],
    ['PERSONAL_MESSAGE_QUEUE_SHEET_NAME', MESSENGER_PERSONAL_SHEET_NAME, '개인에게 보낼 쪽지를 모아두는 시트 이름입니다.', '메신저 개인톡 내용'],
    ['CLASS_MESSAGE_QUEUE_SHEET_NAME', MESSENGER_CLASS_SHEET_NAME, '학급 전체에게 보낼 쪽지를 모아두는 시트 이름입니다.', '메신저 단체톡 내용'],
    ['ATTENDANCE_AI_ALLOWED', ATTENDANCE_AI_ALLOWED_VALUE, '이 출석부에서 AI 입력을 사용할 수 있게 하는 값입니다.', '프로그램이 자동으로 관리'],
    ['ATTENDANCE_CONNECTION_CODE', '', 'Teacher Manager가 현재 사용할 출석부를 확인하는 값입니다.', '프로그램이 자동으로 관리'],
    ['SCRIPT_ID', DEFAULT_CONFIG.SCRIPT_ID, 'Teacher Manager가 이 출석부의 자동 기능을 확인할 때 사용하는 연결 정보입니다.', '프로그램이 자동으로 관리']
  ];

  const existingKeys = new Set(Object.keys(existing));
  const toAppend = rows.filter(row => !existingKeys.has(row[0]));
  if (toAppend.length) sh.getRange(sh.getLastRow() + 1, 1, toAppend.length, 4).setValues(toAppend);
  removeStaleConfigRows_(sh);

  sh.getRange(1, 1, 1, 4).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 1, 180);
  sh.setColumnWidths(2, 1, 260);
  sh.setColumnWidths(3, 1, 520);
  sh.setColumnWidths(4, 1, 260);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

/** Prepare every month in place; inserting the input row preserves all records. */
function ensureMonthSheet_(ss, name) {
  const sh = typeof name === 'string' ? ss.getSheetByName(name) : name;
  if (!sh) throw new Error('연결된 월별 출결표가 없습니다. 빈 탭을 만들지 않았습니다.');
  if (hasMonthlyHeaderInFirstRow_(sh)) {
    sh.insertRowBefore(MONTHLY_ATTENDANCE_INPUT_ROW);
    sh.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, INPUT_HEADERS.length)
      .setValues([[MONTHLY_ATTENDANCE_AI_INPUT_LABEL, '']
        .concat(new Array(INPUT_HEADERS.length - 2).fill(''))]);
    sh.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, INPUT_HEADERS.length)
      .setValues([INPUT_HEADERS]);
  }
  applyInputSheetFormatting_(sh);
  return sh;
}

// Recognize a previous layout only to upgrade it, never to generate it again.
function hasMonthlyHeaderInFirstRow_(sheet) {
  if (!sheet || typeof sheet.getRange !== 'function') return false;
  const firstRow = sheet
    .getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, INPUT_HEADERS.length)
    .getValues()[0];
  return INPUT_HEADERS.every(
    (name, index) => String(firstRow[index] || '').trim() === name
  );
}

/**
 * 월 시트 1행을 이름표 한 칸과 입력칸 한 칸으로 만든다.
 *
 * A열에는 여기가 무엇을 하는 자리인지 적어 두고, B열부터 M열까지는 한 칸으로 합쳐
 * 흰 바탕에 테두리를 둘러 글을 적는 자리로 보이게 한다.
 * 1행은 아래 제목 줄 위에 끼워 넣은 줄이라 진한 파랑을 그대로 물려받는다.
 *
 * 선생님이 적어 둔 문장이 입력칸에 남아 있으면 지우지 않는다.
 */
function applyAttendanceAiInputRow_(sh) {
  const boxWidth =
    MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1;
  const labelCell = sh.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, 1);
  const box = sh.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL, 1, boxWidth
  );
  box.merge();

  // 우리가 써 둔 옛 안내 문구가 아니면 선생님이 적어 둔 문장이다. 어느 칸에 있든 지우지 않는다.
  const ourWords = [
    '',
    MONTHLY_ATTENDANCE_AI_INPUT_LABEL,
    MONTHLY_ATTENDANCE_AI_INPUT_HINT,
    MONTHLY_ATTENDANCE_AI_INPUT_PLACEHOLDER
  ];
  const inLabelCell = String(labelCell.getValue() || '').trim();
  const inBox = String(box.getValue() || '').trim();
  const labelCellIsOurs = ourWords.indexOf(inLabelCell) >= 0;
  const boxIsOurs = ourWords.indexOf(inBox) >= 0;

  if (labelCellIsOurs) {
    if (inLabelCell !== MONTHLY_ATTENDANCE_AI_INPUT_LABEL) {
      labelCell.setValue(MONTHLY_ATTENDANCE_AI_INPUT_LABEL);
    }
    if (boxIsOurs && inBox) {
      if (typeof box.clearContent === 'function') box.clearContent();
      else box.setValue('');
    }
  } else if (boxIsOurs) {
    // 옛 판에서 A칸에 적어 두신 문장이다. 새 입력칸으로 옮기고 이름표를 세운다.
    box.setValue(inLabelCell);
    labelCell.setValue(MONTHLY_ATTENDANCE_AI_INPUT_LABEL);
  }
  // 두 칸에 다 적혀 있으면 어느 쪽도 버릴 수 없으므로 값은 그대로 두고 색만 입힌다.

  labelCell
    .setBackground('#E8F2FF')
    .setFontColor('#000000')
    .setFontWeight('bold')
    .setHorizontalAlignment('left')
    .setVerticalAlignment('middle');
  box
    .setBackground('#FFFFFF')
    .setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR)
    .setFontWeight('normal')
    .setHorizontalAlignment('left')
    .setVerticalAlignment('middle')
    .setBorder(true, true, true, true, false, false, '#C7C7C7', SpreadsheetApp.BorderStyle.SOLID);
  const noteCell = sh.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL, 1, 1
  );
  if (typeof noteCell.setNote === 'function') {
    noteCell.setNote(MONTHLY_ATTENDANCE_AI_INPUT_HINT);
  }
  sh.setRowHeight(MONTHLY_ATTENDANCE_INPUT_ROW, 34);
}

function applyInputSheetFormatting_(sh) {
  if (sh.getMaxRows() < 250) sh.insertRowsAfter(sh.getMaxRows(), 250 - sh.getMaxRows());
  const maxColumns = sh.getMaxColumns();
  if (maxColumns < MONTHLY_ATTENDANCE_LAST_DATA_COL) {
    sh.insertColumnsAfter(
      maxColumns, MONTHLY_ATTENDANCE_LAST_DATA_COL - maxColumns
    );
  } else if (
    maxColumns > MONTHLY_ATTENDANCE_LAST_DATA_COL
    && typeof sh.getLastColumn === 'function'
    && typeof sh.deleteColumns === 'function'
    && sh.getLastColumn() <= MONTHLY_ATTENDANCE_LAST_DATA_COL
  ) {
    const tailRange = sh.getRange(
      1,
      MONTHLY_ATTENDANCE_LAST_DATA_COL + 1,
      sh.getMaxRows(),
      maxColumns - MONTHLY_ATTENDANCE_LAST_DATA_COL
    );
    const notes = typeof tailRange.getNotes === 'function' ? tailRange.getNotes() : null;
    const hasNotes = notes === null || notes.some(row => row.some(note => String(note || '').trim()));
    if (!hasNotes) {
      sh.deleteColumns(
        MONTHLY_ATTENDANCE_LAST_DATA_COL + 1,
        maxColumns - MONTHLY_ATTENDANCE_LAST_DATA_COL
      );
    }
  }

  applyAttendanceAiInputRow_(sh);

  sh.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL)
    .setBackground('#1F4E79')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle');

  // M열 제목. 선생님이 직접 적어 두신 제목이 있으면 손대지 않는다.
  const markHeaderCell = sh.getRange(
    MONTHLY_ATTENDANCE_HEADER_ROW, MONTHLY_ATTENDANCE_AI_MARK_COL, 1, 1
  );
  const markHeader = String(markHeaderCell.getValue() || '').trim();
  if (!markHeader) {
    markHeaderCell.setValue(MONTHLY_ATTENDANCE_AI_MARK_HEADER);
  }

  sh.setFrozenRows(MONTHLY_ATTENDANCE_HEADER_ROW);
  sh.setRowHeight(MONTHLY_ATTENDANCE_HEADER_ROW, 40);
  sh.setColumnWidths(1, 1, 90);
  sh.setColumnWidths(2, 1, 120);
  sh.setColumnWidths(3, 2, 90);
  sh.setColumnWidths(5, 1, 220);
  sh.setColumnWidths(6, 3, 90);
  sh.setColumnWidths(9, 4, 70);

  const n = sh.getMaxRows() - MONTHLY_ATTENDANCE_HEADER_ROW;
  if (n > 0) {
    if (typeof sh.setRowHeights === 'function') {
      sh.setRowHeights(MONTHLY_ATTENDANCE_DATA_START_ROW, n, 24);
    }
    sh.getRange(
      MONTHLY_ATTENDANCE_DATA_START_ROW, 1, n, MONTHLY_ATTENDANCE_LAST_DATA_COL
    ).setBorder(
      true, true, true, true, true, true,
      '#A6A6A6', SpreadsheetApp.BorderStyle.SOLID
    );
    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 1, n, 1).setNumberFormat('yyyy-mm-dd');
    // 3행 아래 배경은 날짜 줄무늬만 쓴다. 예전에는 입력하는 칸임을 알리려고
    // B열·C~D열·F~H열에 옅은 색을 따로 칠했는데, 그 칸에서 줄무늬가 지워져
    // 한 날짜 덩어리가 A열부터 M열까지 이어지지 않고 구멍이 뚫렸다(2026-07-27).
    // 어느 칸에 적는지는 그 칸을 누를 때 나오는 드롭다운 화살표로 알 수 있다.
    applyDateStripeRules_(sh);

    const categoryRule = SpreadsheetApp.newDataValidation().requireValueInList(['질병','미인정','기타','출석인정'], true).setAllowInvalid(false).build();
    const kindRule = SpreadsheetApp.newDataValidation().requireValueInList(['결석함','지각함','조퇴함','결과함'], true).setAllowInvalid(false).build();
    const periodRule = SpreadsheetApp.newDataValidation().requireValueInList(['','1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례'], true).setAllowInvalid(true).build();
    const statusRule = SpreadsheetApp.newDataValidation().requireValueInList(['','제출','미제출','해당없음'], true).setAllowInvalid(true).build();

    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 3, n, 1).setDataValidation(categoryRule);
    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 4, n, 1).setDataValidation(kindRule);
    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 6, n, 1).setDataValidation(periodRule);
    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 7, n, 2).setDataValidation(statusRule);
  }

  sh.getRange(1, 1, sh.getMaxRows(), MONTHLY_ATTENDANCE_LAST_DATA_COL)
    .setVerticalAlignment('middle')
    .setWrap(true);
}

function getAttendanceAiCalendarYear_(schoolYear, month) {
  const schoolYearText = String(
    schoolYear === null || schoolYear === undefined ? '' : schoolYear
  ).trim();
  const monthNumber = Number(month);
  if (
    !/^\d{4}$/.test(schoolYearText)
    || !Number.isInteger(monthNumber)
    || monthNumber < 1
    || monthNumber > 12
  ) {
    return null;
  }
  return Number(schoolYearText) + (monthNumber <= 2 ? 1 : 0);
}

// The class roster (number and name only, never email) goes to Gemini with every
// request so a given name or nickname ("유빈이가") resolves to one roster row (R12-1).
function attendanceAiRosterStudentRule_() {
  return (
    'student에는 roster 목록에서 문장이 가리키는 학생 한 명의 student 값(번호와 이름, 예: 3홍길동)을 그대로 적으세요. ' +
    '문장에는 번호, 성을 뺀 이름, 이름 뒤의 이, 조사(이·가·은·는·을·를·도·랑 등)가 붙을 수 있습니다. ' +
    '같은 이름의 학생이 둘 이상인데 번호나 성이 없어 한 명으로 정할 수 없거나 roster에 없는 학생이면 ' +
    '추측해서 고르지 말고 그 학생의 기록을 넣지 마세요.'
  );
}

function attendanceAiRosterForGemini_(rosterRows) {
  const seen = new Set();
  return (Array.isArray(rosterRows) ? rosterRows : []).map(row => {
    if (!Array.isArray(row)) return null;
    const number = String(row[0] === null || row[0] === undefined ? '' : row[0]).trim();
    const name = String(row[1] === null || row[1] === undefined ? '' : row[1]).trim();
    const student = combineStudentNumberAndName_(number, name);
    if (!student || seen.has(student)) return null;
    seen.add(student);
    return { student: student, number: number, name: name };
  }).filter(Boolean);
}

function attendanceAiSentenceHasIdentity_(sentence, identity, blockedSuffixes) {
  const text = String(sentence || '');
  const particles = [
    '에게서','한테서','께서','으로','에게','한테','부터','까지','이랑','랑','하고',
    '은','는','이','가','을','를','와','과','의','께','도','만','로'
  ];
  const isIdentityCharacter = character => (
    !!character && /[0-9A-Za-z가-힣]/.test(character)
  );
  const hasBoundaryOrParticle = value => (
    !isIdentityCharacter(value.charAt(0))
    || particles.some(particle => (
      value.indexOf(particle) === 0
      && !isIdentityCharacter(value.charAt(particle.length))
    ))
  );
  if (!identity) return false;
  let searchFrom = 0;
  while (searchFrom <= text.length - identity.length) {
    const foundAt = text.indexOf(identity, searchFrom);
    if (foundAt < 0) return false;
    searchFrom = foundAt + 1;
    if (isIdentityCharacter(text.charAt(foundAt - 1))) continue;
    const tail = text.slice(foundAt + identity.length);
    if ((blockedSuffixes || []).some(suffix => suffix && tail.indexOf(suffix) === 0)) continue;
    if (hasBoundaryOrParticle(tail)) return true;
    if (tail.indexOf('학생') === 0 && hasBoundaryOrParticle(tail.slice(2))) return true;
  }
  return false;
}

// Given name = Korean full name of 3+ syllables without its first syllable (surname).
function attendanceAiGivenName_(student) {
  const name = String(student && student.name || '');
  return /^[가-힣]{3,}$/.test(name) ? name.slice(1) : '';
}

// A given name ("유빈", "유빈이", "유빈이가") identifies a student only when no other
// roster student shares it. Returns false for an ambiguous or missing given name.
function attendanceAiGivenNameMentioned_(sentence, student, roster) {
  const given = attendanceAiGivenName_(student);
  if (!given) return false;
  const others = (roster || []).filter(other => other.combined !== student.combined);
  if (others.some(other => attendanceAiGivenName_(other) === given || other.name === given)) {
    return false;
  }
  const blocked = [];
  others.forEach(other => {
    [attendanceAiGivenName_(other), other.name].forEach(value => {
      if (value && value !== given && value.indexOf(given) === 0) blocked.push(value.slice(given.length));
    });
  });
  return attendanceAiSentenceHasIdentity_(sentence, given, blocked)
    || attendanceAiSentenceHasIdentity_(sentence, given + '이', blocked
      .filter(suffix => suffix.indexOf('이') === 0).map(suffix => suffix.slice(1)));
}

function attendanceAiNumberMentioned_(sentence, student) {
  return new RegExp(
    '(^|[^0-9])' + String(student.number).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*번([^0-9]|$)'
  ).test(String(sentence || ''));
}

// A given name mentioned in the sentence that two or more roster students share,
// with none of them named by full name, number+name or number (R12-1).
function attendanceAiAmbiguousGivenNameMentioned_(sentence, roster) {
  const groups = {};
  (roster || []).forEach(student => {
    const given = attendanceAiGivenName_(student);
    if (given) (groups[given] = groups[given] || []).push(student);
  });
  return Object.keys(groups).some(given => {
    const sharing = groups[given];
    if (sharing.length < 2) return false;
    const mentioned = attendanceAiSentenceHasIdentity_(sentence, given, [])
      || attendanceAiSentenceHasIdentity_(sentence, given + '이', []);
    if (!mentioned) return false;
    return !sharing.some(student => (
      attendanceAiSentenceHasIdentity_(sentence, student.name, [])
      || attendanceAiSentenceHasIdentity_(sentence, student.combined, [])
      || attendanceAiNumberMentioned_(sentence, student)
    ));
  });
}

function buildAttendanceAiGeminiRequest_(sentence, context, rosterRows) {
  const roster = attendanceAiRosterForGemini_(rosterRows);
  const studentSchema = { type: 'string', minLength: 1 };
  if (roster.length) studentSchema.enum = roster.map(row => row.student);
  const recordProperties = {
    date: { type: 'string', format: 'date' },
    end_date: { type: 'string', format: 'date' },
    student: studentSchema,
    category: { type: 'string', enum: [''].concat(ATTENDANCE_AI_CATEGORIES) },
    kind: { type: 'string', enum: [''].concat(ATTENDANCE_AI_KINDS) },
    reason: { type: 'string' },
    period: { type: 'string', enum: ATTENDANCE_AI_PERIODS.slice() }
  };
  return {
    model: ATTENDANCE_AI_MODEL,
    input: JSON.stringify({
      instruction: (
        '출결 문장에서 요청한 학생별 출결 자료만 JSON으로 추출하세요. ' +
        'date는 시작일, end_date는 마지막 날이며 둘 다 포함합니다. ' +
        '하루뿐이면 두 날짜를 같게 적고, 기간은 날짜별로 나누지 말고 한 건으로 적으세요. ' +
        '날짜가 없거나 오늘이면 기준 오늘 날짜를 date와 end_date에 적으세요. ' +
        '원문에 없는 kind, reason, period만 빈 문자열로 적고 추측하지 마세요. ' +
        'category(구분)는 새 줄에 반드시 들어가야 합니다. 원문에 구분이 없으면 사유로 정하세요: ' +
        '질병·부상·병원 진료는 질병, 늦잠·무단처럼 정당한 사유가 없으면 미인정, ' +
        '체험학습·대회·학교 행사·경조사·생리는 출석인정, 그 밖에 학교장이 인정한 부득이한 사유는 기타입니다. ' +
        '구분도 사유도 없어 정할 수 없을 때만 category를 빈 문자열로 적으세요. ' +
        '원문에 값이 있지만 서로 충돌하거나 허용 목록 밖이면 빈칸으로 숨기지 마세요. ' +
        '사유가 생리(생리통·생리결석·생리조퇴·생리공결 등)이면 category는 항상 출석인정으로 적고 ' +
        '질병이나 미인정으로 적지 마세요. ' +
        'requested_student_count에는 서로 다른 학생 수를 적으세요. ' +
        attendanceAiRosterStudentRule_()
      ),
      sentence: sentence,
      roster: roster,
      today: String(context.today || ''),
      school_year: String(context.schoolYear),
      calendar_year: getAttendanceAiCalendarYear_(context.schoolYear, context.month),
      month: Number(context.month),
      allowed_values: {
        category: [''].concat(ATTENDANCE_AI_CATEGORIES),
        kind: [''].concat(ATTENDANCE_AI_KINDS),
        period: ATTENDANCE_AI_PERIODS.slice()
      },
      reason_format: {
        rule: (
          'reason에는 출결의 원인이나 목적만 짧은 명사형으로 적고, ' +
          '행동·서술어·문장 끝맺음은 넣지 마세요.'
        ),
        examples: [
          { input: '체험학습 갔어', reason: '체험학습' },
          { input: '감기로 쉬었어', reason: '감기' },
          { input: '대회에 참가했어', reason: '대회 참가' }
        ]
      }
    }),
    store: false,
    response_format: {
      type: 'text',
      mime_type: 'application/json',
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          requested_student_count: { type: 'integer', minimum: 1 },
          records: {
            type: 'array',
            minItems: 1,
            items: {
              type: 'object',
              additionalProperties: false,
              properties: recordProperties,
              required: ['date','end_date','student','category','kind','reason','period']
            }
          }
        },
        required: ['requested_student_count','records']
      }
    }
  };
}

function extractAttendanceAiGeminiPayload_(interactionResponse) {
  if (
    !interactionResponse
    || typeof interactionResponse !== 'object'
    || Array.isArray(interactionResponse)
    || interactionResponse.status !== 'completed'
    || !Array.isArray(interactionResponse.steps)
  ) {
    return null;
  }
  const outputs = interactionResponse.steps.filter(
    step => step && step.type === 'model_output'
  );
  if (outputs.length !== 1 || !Array.isArray(outputs[0].content)) return null;
  const content = outputs[0].content;
  if (
    content.length !== 1
    || !content[0]
    || content[0].type !== 'text'
    || typeof content[0].text !== 'string'
  ) {
    return null;
  }
  try {
    return JSON.parse(content[0].text);
  } catch (err) {
    return null;
  }
}

// User decision 2026-10-01 (SHEET-AI-02): "생리는 무조건 질병 아니고 출석인정". A word counts
// only when it is 생리 itself or 생리 followed by an attendance word (생리통·생리결석·생리조퇴·
// 생리공결); 생리현상·생리적·생리학 and words that merely contain the syllables (학생리더십) do not.
function attendanceAiIsMenstruationReason_(reason) {
  return String(reason === null || reason === undefined ? '' : reason)
    .split(/[^0-9A-Za-z가-힣]+/)
    .some(word => /^생리(?:통|결석|조퇴|지각|결과|공결|기간|중)?(?:함)?(?:으로|로|이|가|은|는|을|를|와|과|도|때문에|때문)?$/.test(word));
}

function normalizeAttendanceAiReason_(value) {
  let reason = String(value === null || value === undefined ? '' : value).trim();
  reason = reason.replace(/[.!?。！？]+$/g, '').trim();
  const predicateEndings = [
    /(?:\s*(?:을|를|에|으로|로))?\s*갔(?:어(?:요)?|습니다|다|음)$/,
    /\s*다녀왔(?:어(?:요)?|습니다|다|음)$/,
    /\s*했(?:어(?:요)?|습니다|다|음)$/,
    /(?:\s*(?:때문에|으로|로))?\s*쉬었(?:어(?:요)?|습니다|다|음)$/
  ];
  for (let index = 0; index < predicateEndings.length; index++) {
    const shortened = reason.replace(predicateEndings[index], '').trim();
    if (shortened !== reason) return shortened;
  }
  return reason;
}

function validateAttendanceAiRecords_(payload, rosterRows, sheetContext, holidayDateKeys) {
  const isPlainObject = value => (
    value !== null && typeof value === 'object' && !Array.isArray(value)
  );
  const hasExactKeys = (value, expected) => {
    if (!isPlainObject(value)) return false;
    const actual = Object.keys(value).sort();
    const wanted = expected.slice().sort();
    return actual.length === wanted.length
      && actual.every((key, index) => key === wanted[index]);
  };
  const topKeys = ['requested_student_count','records'];
  const recordKeys = ['date','end_date','student','category','kind','reason','period'];
  if (!hasExactKeys(payload, topKeys)) return null;
  if (
    typeof payload.requested_student_count !== 'number'
    || !Number.isSafeInteger(payload.requested_student_count)
    || payload.requested_student_count < 1
    || !Array.isArray(payload.records)
    || payload.records.length < 1
    || !Array.isArray(rosterRows)
    || !isPlainObject(sheetContext)
    || typeof sheetContext.sentence !== 'string'
  ) {
    return null;
  }

  const schoolYear = String(sheetContext.schoolYear === undefined
    ? ''
    : sheetContext.schoolYear).trim();
  const month = Number(sheetContext.month);
  const calendarYear = getAttendanceAiCalendarYear_(schoolYear, month);
  if (calendarYear === null) return null;
  const holidays = holidayDateKeys instanceof Set ? holidayDateKeys : new Set();

  const roster = rosterRows.map(row => {
    if (!Array.isArray(row)) return null;
    return {
      number: String(row[0] === null || row[0] === undefined ? '' : row[0]).trim(),
      name: String(row[1] === null || row[1] === undefined ? '' : row[1]).trim(),
      combined: combineStudentNumberAndName_(row[0], row[1])
    };
  }).filter(row => row && row.number && row.name && row.combined);
  const sentence = sheetContext.sentence;
  const hasExactIdentityMention = (identity, blockedSuffixes) => (
    attendanceAiSentenceHasIdentity_(sentence, identity, blockedSuffixes)
  );
  const studentAppearsInSentence = student => {
    const longerRosterNameSuffixes = roster
      .filter(other => (
        other.combined !== student.combined
        && other.name.indexOf(student.name) === 0
      ))
      .map(other => other.name.slice(student.name.length));
    const numberPattern = new RegExp(
      '(^|[^0-9])' + student.number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '번([^0-9]|$)'
    );
    return hasExactIdentityMention(student.name, longerRosterNameSuffixes)
      || hasExactIdentityMention(student.combined, [])
      || numberPattern.test(sentence)
      || attendanceAiGivenNameMentioned_(sentence, student, roster);
  };

  const matchedStudents = new Set();
  const seenRows = new Set();
  const validated = [];
  for (let index = 0; index < payload.records.length; index++) {
    const record = payload.records[index];
    if (!hasExactKeys(record, recordKeys)) return null;
    if (recordKeys.some(key => typeof record[key] !== 'string')) return null;

    const dateText = record.date;
    const endDateText = record.end_date;
    const studentText = record.student.trim();
    let category = record.category.trim();
    const kind = record.kind.trim();
    const reason = normalizeAttendanceAiReason_(record.reason);
    const period = record.period.trim();
    if (!studentText) {
      return null;
    }
    const matches = roster.filter(student => attendanceAiStudentTextMatches_(studentText, student));
    if (
      matches.length !== 1
      || !studentAppearsInSentence(matches[0])
    ) {
      return null;
    }
    if (
      (category && ATTENDANCE_AI_CATEGORIES.indexOf(category) < 0)
      || (kind && ATTENDANCE_AI_KINDS.indexOf(kind) < 0)
      || ATTENDANCE_AI_PERIODS.indexOf(period) < 0
    ) {
      return null;
    }
    // A wrong model category cannot write 질병 or 미인정 for a 생리 reason (SHEET-AI-02).
    if (attendanceAiIsMenstruationReason_(reason)) category = '출석인정';
    // A new row never gets an empty 구분 (SHEET-AI-04); the detailed check names it.
    if (!category) return null;

    const dateMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText);
    const endDateMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(endDateText);
    if (!dateMatch || !endDateMatch) return null;
    const year = Number(dateMatch[1]);
    const dateMonth = Number(dateMatch[2]);
    const day = Number(dateMatch[3]);
    const endYear = Number(endDateMatch[1]);
    const endMonth = Number(endDateMatch[2]);
    const endDay = Number(endDateMatch[3]);
    const parsedDate = new Date(Date.UTC(year, dateMonth - 1, day));
    const parsedEndDate = new Date(Date.UTC(endYear, endMonth - 1, endDay));
    if (
      year !== calendarYear
      || dateMonth !== month
      || endYear !== calendarYear
      || endMonth !== month
      || parsedDate.getUTCFullYear() !== year
      || parsedDate.getUTCMonth() + 1 !== dateMonth
      || parsedDate.getUTCDate() !== day
      || parsedEndDate.getUTCFullYear() !== endYear
      || parsedEndDate.getUTCMonth() + 1 !== endMonth
      || parsedEndDate.getUTCDate() !== endDay
      || parsedEndDate.getTime() < parsedDate.getTime()
    ) {
      return null;
    }

    matchedStudents.add(matches[0].combined);
    for (
      let cursor = parsedDate.getTime();
      cursor <= parsedEndDate.getTime();
      cursor += 86400000
    ) {
      const current = new Date(cursor);
      const currentDate = [
        String(current.getUTCFullYear()).padStart(4, '0'),
        String(current.getUTCMonth() + 1).padStart(2, '0'),
        String(current.getUTCDate()).padStart(2, '0')
      ].join('-');
      const weekday = current.getUTCDay();
      if (weekday === 0 || weekday === 6 || holidays.has(currentDate)) continue;
      const rowKey = [
        currentDate,
        matches[0].combined,
        category,
        kind,
        reason,
        period
      ].join('\u0000');
      if (seenRows.has(rowKey)) return null;
      seenRows.add(rowKey);
      validated.push({
        date: currentDate,
        rosterCombined: matches[0].combined,
        category: category,
        kind: kind,
        reason: reason,
        period: period
      });
    }
  }
  if (matchedStudents.size !== payload.requested_student_count) return null;
  validated.sort((left, right) => left.date.localeCompare(right.date));
  return validated;
}

/** 기존 자료/없음 반환을 유지하면서 화면에 보여 줄 실패 이유를 함께 만든다. */
function validateAttendanceAiRecordsDetailed_(
  payload, rosterRows, sheetContext, holidayDateKeys
) {
  const records = validateAttendanceAiRecords_(
    payload, rosterRows, sheetContext, holidayDateKeys
  );
  if (records !== null) return { records: records, code: '', message: '' };

  const failed = (code, message) => ({ records: null, code: code, message: message });
  const generic = () => failed(
    'shape',
    '입력한 문장을 출결표에 넣을 내용으로 정리하지 못했어요. 날짜·학생·출결 내용을 나누어 적어 주세요.'
  );
  const isPlainObject = value => (
    value !== null && typeof value === 'object' && !Array.isArray(value)
  );
  const expectedKeys = ['date','end_date','student','category','kind','reason','period'];
  if (
    !isPlainObject(payload)
    || !Array.isArray(payload.records)
    || !Array.isArray(rosterRows)
    || !isPlainObject(sheetContext)
  ) {
    return generic();
  }

  const roster = rosterRows.map(row => {
    if (!Array.isArray(row)) return null;
    return {
      number: String(row[0] === null || row[0] === undefined ? '' : row[0]).trim(),
      name: String(row[1] === null || row[1] === undefined ? '' : row[1]).trim(),
      combined: combineStudentNumberAndName_(row[0], row[1])
    };
  }).filter(row => row && row.number && row.name && row.combined);
  const calendarYear = getAttendanceAiCalendarYear_(sheetContext.schoolYear, sheetContext.month);
  const month = Number(sheetContext.month);
  if (attendanceAiAmbiguousGivenNameMentioned_(sheetContext.sentence, roster)) {
    return failed(
      'ambiguous_student',
      '이름이 같은 학생이 여러 명이라 누구인지 정하지 못했어요. 입력칸을 지우고 번호를 함께 적어 다시 입력해 주세요(예: 3번 이름).'
    );
  }

  for (let index = 0; index < payload.records.length; index++) {
    const record = payload.records[index];
    if (
      !isPlainObject(record)
      || Object.keys(record).length !== expectedKeys.length
      || expectedKeys.some(key => (
        !Object.prototype.hasOwnProperty.call(record, key)
        || typeof record[key] !== 'string'
      ))
    ) {
      return generic();
    }

    const studentText = record.student.trim();
    const matches = roster.filter(student => attendanceAiStudentTextMatches_(studentText, student));
    if (!studentText || matches.length !== 1) {
      return failed(
        'student',
        '학생명단에서 학생을 한 명으로 찾지 못했습니다. 이름이나 번호를 확인해 주세요.'
      );
    }
    // With the roster enum Gemini must name a roster student even for a name that is
    // not on the roster; say so instead of the generic notice (R12-1).
    const named = matches[0];
    if (!(
      attendanceAiSentenceHasIdentity_(sheetContext.sentence, named.name, [])
      || attendanceAiSentenceHasIdentity_(sheetContext.sentence, named.combined, [])
      || attendanceAiNumberMentioned_(sheetContext.sentence, named)
      || attendanceAiGivenNameMentioned_(sheetContext.sentence, named, roster)
    )) {
      return failed(
        'student',
        '문장에 적힌 학생을 학생명단에서 찾지 못했어요. 입력칸을 지우고 학생명단의 이름이나 번호로 다시 입력해 주세요.'
      );
    }

    const category = record.category.trim();
    const kind = record.kind.trim();
    const period = record.period.trim();
    if (!category && !attendanceAiIsMenstruationReason_(normalizeAttendanceAiReason_(record.reason))) {
      return failed(
        'category',
        ATTENDANCE_AI_CATEGORY_MISSING_MESSAGE
      );
    }
    if (
      (category && ATTENDANCE_AI_CATEGORIES.indexOf(category) < 0)
      || (kind && ATTENDANCE_AI_KINDS.indexOf(kind) < 0)
      || ATTENDANCE_AI_PERIODS.indexOf(period) < 0
    ) {
      return failed(
        'attendance_value',
        '출결 구분은 질병·미인정·기타·출석인정, 종류는 결석·지각·조퇴·결과 중에서 적어 주세요. 지각·조퇴·결과에는 교시도 적어 주세요.'
      );
    }

    const parseDate = value => {
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
      if (!match) return null;
      const year = Number(match[1]);
      const dateMonth = Number(match[2]);
      const day = Number(match[3]);
      const parsed = new Date(Date.UTC(year, dateMonth - 1, day));
      if (
        parsed.getUTCFullYear() !== year
        || parsed.getUTCMonth() + 1 !== dateMonth
        || parsed.getUTCDate() !== day
      ) {
        return null;
      }
      return { value: parsed, year: year, month: dateMonth };
    };
    const start = parseDate(record.date);
    const end = parseDate(record.end_date);
    if (
      !start
      || !end
      || calendarYear === null
      || start.year !== calendarYear
      || end.year !== calendarYear
      || start.month !== month
      || end.month !== month
      || end.value.getTime() < start.value.getTime()
    ) {
      return failed(
        'date',
        '날짜를 확인하지 못했습니다. 입력한 날짜와 지금 열어 둔 월 시트가 같은지 확인해 주세요.'
      );
    }
  }
  return generic();
}

function buildAttendanceAiBatchUpdate_(records, writeContext) {
  if (!Array.isArray(records) || !records.length || !writeContext) return null;
  const rows = records.map(record => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(record.date);
    if (!match) return null;
    const serial = Math.floor(
      Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / 86400000
    ) + 25569;
    const textCell = value => {
      const text = String(value === null || value === undefined ? '' : value);
      return text ? { userEnteredValue: { stringValue: text } } : {};
    };
    const values = [
      { userEnteredValue: { numberValue: serial } },
      textCell(record.rosterCombined),
      textCell(record.category),
      textCell(record.kind),
      textCell(record.reason),
      textCell(record.period)
    ];
    // 신고서(G)와 첨부(H)는 미제출로 시작한다(R11-3). I~L은 비워 둔다.
    values.push({ userEnteredValue: { stringValue: '미제출' } });
    values.push({ userEnteredValue: { stringValue: '미제출' } });
    // M열에만 AI가 넣은 줄이라고 적는다.
    // 배경색은 건드리지 않는다 — 그 자리는 날짜 줄무늬가 쓴다.
    const betweenCount = MONTHLY_ATTENDANCE_AI_MARK_COL - values.length - 1;
    const between = [];
    for (let index = 0; index < betweenCount; index++) between.push({});
    return {
      values: values.concat(between).concat([
        { userEnteredValue: { stringValue: MONTHLY_ATTENDANCE_AI_MARK_TEXT } }
      ])
    };
  });
  if (rows.some(row => !row)) return null;
  return {
    requests: [{
      appendCells: {
        sheetId: writeContext.sheetId,
        rows: rows,
        fields: 'userEnteredValue,userEnteredFormat.backgroundColor'
      }
    }]
  };
}

/** 바꿔·고쳐·지워 같은 고침 말이나 날짜의 학생 전체를 고치는 말이 있는지 본다. */
function attendanceAiExplicitEditRequested_(sentence) {
  const text = String(sentence || '').replace(/\s+/g, ' ').trim();
  const editWords = /(?:바꿔|바꾸|고쳐|수정|변경|정정|비워|지워|삭제|없애)/;
  const groupWords = /(?:전부|모두|전체|애들|학생들)/;
  const groupChange = /(?:날짜|학생|구분|종류|사유|교시|신고서|첨부|제출|미제출|해당없음|냈(?:어|습니다|다)|안\s*냈)/;
  return editWords.test(text) || (groupWords.test(text) && groupChange.test(text));
}

/** 기존 줄을 고치려는 표현인지 새 줄 추가 전에 가른다. */
function isAttendanceAiExistingUpdateSentence_(sentence) {
  const text = String(sentence || '').replace(/\s+/g, ' ').trim();
  if (!text) return false;
  return /아니(?:고|라|야|라서)/.test(text) || attendanceAiExplicitEditRequested_(text);
}

// "생리는 질병 아니고 출석인정이야" can explain a value of a new event. Only a sentence with no
// edit word (바꿔·고쳐·지워…) that names an attendance kind of its own may become a new entry
// when the student has no row on that date (SHEET-AI-02).
function attendanceAiNewEventFallbackAllowed_(sentence) {
  const text = String(sentence || '');
  return !attendanceAiExplicitEditRequested_(text) && /(?:결석|지각|조퇴|결과)/.test(text);
}

/** 기존 줄 수정 문장을 새 줄 추가와 다른 모양으로 해석하게 한다. */
function buildAttendanceAiExistingUpdateGeminiRequest_(sentence, context, rosterRows) {
  const roster = attendanceAiRosterForGemini_(rosterRows);
  const studentSchema = { type: 'string' };
  if (roster.length) studentSchema.enum = [''].concat(roster.map(row => row.student));
  const fieldNames = [
    'date','student','category','kind','reason','period',
    'report_status','attachment_status'
  ];
  const changeProperties = {
    field: { type: 'string', enum: fieldNames },
    old_value_stated: { type: 'boolean' },
    old_value: { type: 'string' },
    new_value: { type: 'string' }
  };
  return {
    model: ATTENDANCE_AI_MODEL,
    input: JSON.stringify({
      instruction: (
        '이 문장은 이미 월별 출결표에 있는 줄을 고치는 요청입니다. 새 줄을 만들지 마세요. ' +
        'date는 고치기 전 대상 날짜, student는 고치기 전 대상 학생입니다. ' +
        '날짜의 기존 학생들을 모두 고치라는 말이면 scope를 all_existing_on_date로 하고 ' +
        'student는 빈 문자열로 적으세요. 그 밖에는 named_students로 적으세요. ' +
        '각 change에는 고칠 칸, 새 값, 문장에 기존 값이 직접 적혔는지를 담으세요. ' +
        '기존 값이 적혔으면 old_value_stated를 true로 하고 old_value에 그 값을 적으세요. ' +
        '한 학생의 여러 칸을 함께 고치면 changes에 모두 넣으세요. ' +
        '제출 또는 미제출이라고만 하고 신고서라는 말이 없으면 attachment_status로 보세요. ' +
        'category는 질병·미인정·기타·출석인정, kind는 결석함·지각함·조퇴함·결과함, ' +
        'period는 빈 값·1~7교시·조회·종례, 신고서와 첨부는 빈 값·제출·미제출·해당없음만 쓰세요. ' +
        '날짜와 학생은 문장에 적힌 값을 그대로 근거로 삼고 추측하지 마세요. ' +
        '문장에 오늘이라고 적혔으면 date에 today 값을 적으세요. ' +
        '사유가 생리(생리통·생리결석·생리조퇴·생리공결 등)인 줄의 구분(category)은 항상 출석인정입니다. ' +
        '질병이나 미인정으로 바꾸지 마세요. ' +
        '학생(student와 학생 칸의 old_value·new_value)은 ' + attendanceAiRosterStudentRule_()
      ),
      sentence: String(sentence || ''),
      roster: roster,
      today: String(context.today || ''),
      school_year: String(context.schoolYear || ''),
      calendar_year: getAttendanceAiCalendarYear_(context.schoolYear, context.month),
      month: Number(context.month),
      allowed_values: {
        category: ATTENDANCE_AI_CATEGORIES.slice(),
        kind: ATTENDANCE_AI_KINDS.slice(),
        period: ATTENDANCE_AI_PERIODS.slice(),
        report_status: ['','제출','미제출','해당없음'],
        attachment_status: ['','제출','미제출','해당없음']
      }
    }),
    store: false,
    response_format: {
      type: 'text',
      mime_type: 'application/json',
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          operation: { type: 'string', enum: ['update_existing'] },
          scope: {
            type: 'string',
            enum: ['named_students','all_existing_on_date']
          },
          date: { type: 'string', format: 'date' },
          requested_student_count: { type: 'integer', minimum: 0 },
          targets: {
            type: 'array',
            minItems: 1,
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                student: studentSchema,
                changes: {
                  type: 'array',
                  minItems: 1,
                  items: {
                    type: 'object',
                    additionalProperties: false,
                    properties: changeProperties,
                    required: ['field','old_value_stated','old_value','new_value']
                  }
                }
              },
              required: ['student','changes']
            }
          }
        },
        required: ['operation','scope','date','requested_student_count','targets']
      }
    }
  };
}

function attendanceAiExistingUpdateFieldSpecs_() {
  return {
    date: { column: 0 },
    student: { column: 1 },
    category: { column: 2 },
    kind: { column: 3 },
    reason: { column: 4 },
    period: { column: 5 },
    report_status: { column: 6 },
    attachment_status: { column: 7 }
  };
}

function attendanceAiDateKeyFromValue_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    return [
      String(value.getFullYear()).padStart(4, '0'),
      String(value.getMonth() + 1).padStart(2, '0'),
      String(value.getDate()).padStart(2, '0')
    ].join('-');
  }
  if (typeof value === 'number' && isFinite(value)) {
    const date = new Date(Math.round(value - 25569) * 86400000);
    return [
      String(date.getUTCFullYear()).padStart(4, '0'),
      String(date.getUTCMonth() + 1).padStart(2, '0'),
      String(date.getUTCDate()).padStart(2, '0')
    ].join('-');
  }
  const text = String(value === null || value === undefined ? '' : value).trim();
  const match = /^(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})$/.exec(text);
  if (!match) return text;
  return [
    match[1],
    String(Number(match[2])).padStart(2, '0'),
    String(Number(match[3])).padStart(2, '0')
  ].join('-');
}

function attendanceAiParseDateKey_(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() + 1 !== month
    || date.getUTCDate() !== day
  ) {
    return null;
  }
  return { key: match[0], year: year, month: month, day: day, date: date };
}

// "오늘" names the request's today (Asia/Seoul, from getTodayDate). A correction written
// with 오늘 had no way to name its date and always stopped here (SHEET-AI-02).
function attendanceAiSentenceMentionsDate_(sentence, parsedDate, today) {
  if (!parsedDate) return false;
  const text = String(sentence || '');
  if (today && parsedDate.key === String(today).trim() && /오늘(?!날)/.test(text)) return true;
  const dayPattern = new RegExp('(^|[^0-9])' + parsedDate.day + '\\s*일([^0-9]|$)');
  const monthDayPattern = new RegExp(
    '(^|[^0-9])' + parsedDate.month + '\\s*월\\s*' + parsedDate.day + '\\s*일([^0-9]|$)'
  );
  return text.indexOf(parsedDate.key) !== -1
    || monthDayPattern.test(text)
    || dayPattern.test(text);
}

function attendanceAiUpdateRoster_(rosterRows) {
  return (rosterRows || []).map(row => {
    if (!Array.isArray(row)) return null;
    const number = String(row[0] === null || row[0] === undefined ? '' : row[0]).trim();
    const name = String(row[1] === null || row[1] === undefined ? '' : row[1]).trim();
    const combined = combineStudentNumberAndName_(number, name);
    return number && name && combined
      ? { number: number, name: name, combined: combined }
      : null;
  }).filter(Boolean);
}

// Gemini returns the student as "5번 김OO" (number, 번, space, name) as often as "5번"
// or the bare name; that form named exactly one roster row but was rejected (R11-1).
function attendanceAiStudentTextMatches_(text, student) {
  const value = String(text === null || text === undefined ? '' : text).trim();
  if (!value || !student) return false;
  if (
    value === student.combined
    || value === student.name
    || value === student.number
    || value === student.number + '번'
  ) {
    return true;
  }
  const compact = value.replace(/\s+/g, '');
  return compact === student.number + '번' + student.name;
}

function attendanceAiResolveUpdateStudent_(value, roster) {
  const text = String(value === null || value === undefined ? '' : value).trim();
  const matches = (roster || []).filter(student => attendanceAiStudentTextMatches_(text, student));
  return matches.length === 1 ? matches[0] : null;
}

function attendanceAiSentenceMentionsStudent_(sentence, student, roster) {
  if (!student) return false;
  if (roster && attendanceAiGivenNameMentioned_(sentence, student, roster)) return true;
  const text = String(sentence || '');
  if (text.indexOf(student.combined) !== -1 || text.indexOf(student.name) !== -1) {
    return true;
  }
  return new RegExp(
    '(^|[^0-9])' + student.number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*번([^0-9]|$)'
  ).test(text);
}

function attendanceAiNormalizeExistingUpdateValue_(
  field, value, context, roster, holidayDateKeys, sentence, options
) {
  let text = String(value === null || value === undefined ? '' : value).trim();
  if (field === 'date') {
    const parsed = attendanceAiParseDateKey_(text);
    const calendarYear = getAttendanceAiCalendarYear_(context.schoolYear, context.month);
    if (
      !parsed
      || parsed.year !== calendarYear
      || parsed.month !== Number(context.month)
      || !attendanceAiSentenceMentionsDate_(sentence, parsed, context.today)
    ) {
      return null;
    }
    return { comparable: parsed.key, value: parsed.key };
  }
  if (field === 'student') {
    const student = attendanceAiResolveUpdateStudent_(text, roster);
    if (!student || !attendanceAiSentenceMentionsStudent_(sentence, student, roster)) return null;
    return { comparable: student.combined, value: student.combined };
  }
  if (field === 'category' && ATTENDANCE_AI_CATEGORIES.indexOf(text) < 0) return null;
  if (field === 'kind' && ATTENDANCE_AI_KINDS.indexOf(text) < 0) return null;
  if (field === 'period' && ATTENDANCE_AI_PERIODS.indexOf(text) < 0) return null;
  if (
    (field === 'report_status' || field === 'attachment_status')
    && ['','제출','미제출','해당없음'].indexOf(text) < 0
  ) {
    return null;
  }
  if (field === 'reason') text = normalizeAttendanceAiReason_(text);
  if (!text && !/(?:비워|지워|삭제|없애)/.test(String(sentence || ''))) return null;
  return { comparable: text, value: text };
}

function attendanceAiExistingCellComparable_(field, value, roster) {
  if (field === 'date') return attendanceAiDateKeyFromValue_(value);
  if (field === 'student') {
    const student = attendanceAiResolveUpdateStudent_(value, roster);
    return student ? student.combined : String(value || '').trim();
  }
  const text = String(value === null || value === undefined ? '' : value).trim();
  return field === 'reason' ? normalizeAttendanceAiReason_(text) : text;
}

function attendanceAiExistingUpdateFailure_(code, message) {
  return { ok: false, code: code, message: message, changes: [] };
}

/** 모델이 고른 수정 대상을 실제 현재 행과 다시 맞춰 한 번의 변경 묶음으로 만든다. */
function planAttendanceAiExistingUpdates_(
  payload, rosterRows, readState, context, holidayDateKeys
) {
  const failed = attendanceAiExistingUpdateFailure_;
  const isObject = value => value && typeof value === 'object' && !Array.isArray(value);
  if (
    !isObject(payload)
    || payload.operation !== 'update_existing'
    || ['named_students','all_existing_on_date'].indexOf(payload.scope) < 0
    || typeof payload.date !== 'string'
    || !Number.isSafeInteger(payload.requested_student_count)
    || !Array.isArray(payload.targets)
    || !payload.targets.length
    || !readState
  ) {
    return failed('shape', '수정할 내용을 정확히 확인하지 못했습니다. 입력 문장을 그대로 남겨 두었습니다.');
  }

  const sourceDate = attendanceAiParseDateKey_(payload.date);
  const calendarYear = getAttendanceAiCalendarYear_(context.schoolYear, context.month);
  if (
    sourceDate
    && (sourceDate.year !== calendarYear || sourceDate.month !== Number(context.month))
    && attendanceAiSentenceMentionsDate_(context.sentence, sourceDate, context.today)
  ) {
    // A named date in another month (오늘 = 10월 1일 typed on the 9월 tab): say which tab.
    return failed('date', '수정할 날짜가 지금 열어 둔 월 시트의 달과 달라요. 그 달의 월 시트에서 다시 입력해 주세요.');
  }
  if (
    !sourceDate
    || sourceDate.year !== calendarYear
    || sourceDate.month !== Number(context.month)
    || !attendanceAiSentenceMentionsDate_(context.sentence, sourceDate, context.today)
  ) {
    return failed('date', '수정할 날짜를 정확히 확인하지 못했습니다. 날짜와 학생을 함께 적어 주세요.');
  }

  const rows = Array.isArray(readState.dataRows)
    ? readState.dataRows
    : (Array.isArray(readState.values) ? readState.values : null);
  const formulas = Array.isArray(readState.formulas)
    ? readState.formulas
    : [];
  if (!Array.isArray(rows)) {
    return failed('read', '기존 출결 내용을 읽지 못했습니다. 입력 문장을 그대로 남겨 두었습니다.');
  }
  const firstDataRow = Number.isSafeInteger(readState.firstDataRow)
    ? readState.firstDataRow
    : MONTHLY_ATTENDANCE_DATA_START_ROW;
  const roster = attendanceAiUpdateRoster_(rosterRows);
  const fields = attendanceAiExistingUpdateFieldSpecs_();
  const isAll = payload.scope === 'all_existing_on_date';
  if (isAll) {
    if (
      payload.requested_student_count !== 0
      || !/(?:전부|모두|전체|애들|학생들)/.test(String(context.sentence || ''))
    ) {
      return failed('scope', '날짜 전체를 바꾸려면 날짜와 전부 또는 모두를 함께 적어 주세요.');
    }
  } else if (payload.requested_student_count < 1) {
    return failed('scope', '수정할 학생을 확인하지 못했습니다. 날짜와 학생을 함께 적어 주세요.');
  }

  const pending = new Map();
  const selectedRows = new Set();
  let shouldSort = false;
  const targetStudents = new Set();

  for (let targetIndex = 0; targetIndex < payload.targets.length; targetIndex++) {
    const target = payload.targets[targetIndex];
    if (!isObject(target) || typeof target.student !== 'string' || !Array.isArray(target.changes) || !target.changes.length) {
      return failed('shape', '수정할 학생과 내용을 정확히 확인하지 못했습니다.');
    }
    let sourceStudent = null;
    if (isAll) {
      if (target.student.trim()) {
        return failed('scope', '날짜 전체 수정과 특정 학생 수정을 한 문장에 섞지 말아 주세요.');
      }
    } else {
      sourceStudent = attendanceAiResolveUpdateStudent_(target.student, roster);
      if (!sourceStudent || !attendanceAiSentenceMentionsStudent_(context.sentence, sourceStudent, roster)) {
        if (attendanceAiAmbiguousGivenNameMentioned_(context.sentence, roster)) {
          return failed('ambiguous_student', '이름이 같은 학생이 여러 명이라 누구인지 정하지 못했어요. 입력칸을 지우고 번호를 함께 적어 다시 입력해 주세요(예: 3번 이름).');
        }
        return failed('student', '학생명단에서 수정할 학생을 한 명으로 찾지 못했습니다.');
      }
      targetStudents.add(sourceStudent.combined);
    }

    const normalizedChanges = [];
    const seenFields = new Set();
    for (let changeIndex = 0; changeIndex < target.changes.length; changeIndex++) {
      const change = target.changes[changeIndex];
      if (
        !isObject(change)
        || !Object.prototype.hasOwnProperty.call(fields, change.field)
        || typeof change.old_value_stated !== 'boolean'
        || typeof change.old_value !== 'string'
        || typeof change.new_value !== 'string'
        || seenFields.has(change.field)
        || (isAll && change.field === 'student')
      ) {
        return failed('change', '바꿀 출결 내용을 정확히 확인하지 못했습니다.');
      }
      seenFields.add(change.field);
      const normalizedNew = attendanceAiNormalizeExistingUpdateValue_(
        change.field,
        change.new_value,
        context,
        roster,
        holidayDateKeys,
        context.sentence
      );
      if (!normalizedNew) {
        return failed('value', '바꿀 값을 출결표의 선택값에 맞춰 적어 주세요. 구분은 질병·미인정·기타·출석인정, 종류는 결석·지각·조퇴·결과를 사용할 수 있습니다.');
      }
      let normalizedOld = null;
      if (change.old_value_stated) {
        normalizedOld = attendanceAiNormalizeExistingUpdateValue_(
          change.field,
          change.old_value,
          context,
          roster,
          holidayDateKeys,
          context.sentence,
          { existingValue: true }
        );
        if (!normalizedOld) {
          return failed('old_value', '현재 값을 정확히 확인하지 못했습니다. 기존 값과 새 값을 함께 적어 주세요.');
        }
      }
      normalizedChanges.push({
        field: change.field,
        column: fields[change.field].column,
        oldValueStated: change.old_value_stated,
        oldComparable: normalizedOld ? normalizedOld.comparable : '',
        newComparable: normalizedNew.comparable,
        newValue: normalizedNew.value
      });
    }

    let candidates = [];
    rows.forEach((row, rowOffset) => {
      if (!Array.isArray(row)) return;
      if (attendanceAiDateKeyFromValue_(row[0]) !== sourceDate.key) return;
      const studentText = String(row[1] || '').trim();
      if (!studentText) return;
      if (!isAll && studentText !== sourceStudent.combined) return;
      candidates.push(rowOffset);
    });
    if (!isAll && !candidates.length) {
      // No row at all for this student on that date (not one a stated old value ruled out).
      // When every named student is in that state the handler may try a new entry (SHEET-AI-02).
      const allMissing = payload.targets.every(other => {
        const student = isObject(other) && typeof other.student === 'string'
          ? attendanceAiResolveUpdateStudent_(other.student, roster)
          : null;
        return !!student && !rows.some(item => (
          Array.isArray(item)
          && attendanceAiDateKeyFromValue_(item[0]) === sourceDate.key
          && String(item[1] || '').trim() === student.combined
        ));
      });
      return failed(
        allMissing ? 'target_missing' : 'target',
        '수정할 기존 줄을 하나로 찾지 못했습니다. 날짜·학생과 현재 값을 더 정확히 적어 주세요.'
      );
    }
    normalizedChanges.filter(change => change.oldValueStated).forEach(change => {
      candidates = candidates.filter(rowOffset => (
        attendanceAiExistingCellComparable_(
          change.field,
          rows[rowOffset][change.column],
          roster
        ) === change.oldComparable
      ));
    });
    if (!candidates.length || (!isAll && candidates.length !== 1)) {
      return failed(
        'target',
        '수정할 기존 줄을 하나로 찾지 못했습니다. 날짜·학생과 현재 값을 더 정확히 적어 주세요.'
      );
    }

    candidates.forEach(rowOffset => {
      selectedRows.add(rowOffset);
      normalizedChanges.forEach(change => {
        const formulaRow = formulas[rowOffset] || [];
        if (String(formulaRow[change.column] || '').trim()) {
          throw attendanceAiExistingUpdateFailure_(
            'formula',
            '자동 계산이 들어 있는 칸은 안전을 위해 바꾸지 않았습니다.'
          );
        }
        const oldComparable = attendanceAiExistingCellComparable_(
          change.field,
          rows[rowOffset][change.column],
          roster
        );
        if (oldComparable === change.newComparable) return;
        const key = rowOffset + ':' + change.column;
        const existing = pending.get(key);
        if (existing && existing.newComparable !== change.newComparable) {
          throw attendanceAiExistingUpdateFailure_(
            'conflict',
            '한 칸을 서로 다른 값으로 바꾸라는 내용이 함께 있어 수정하지 않았습니다.'
          );
        }
        pending.set(key, {
          rowOffset: rowOffset,
          rowNumber: firstDataRow + rowOffset,
          column: change.column,
          field: change.field,
          newComparable: change.newComparable,
          newValue: change.newValue
        });
        if (change.field === 'date' || change.field === 'student') shouldSort = true;
      });
    });
  }

  if (!isAll && targetStudents.size !== payload.requested_student_count) {
    return failed('student_count', '문장에 적힌 학생 수를 정확히 확인하지 못했습니다.');
  }

  // A row whose reason is 생리 keeps 구분 출석인정 (user decision 2026-10-01, SHEET-AI-02):
  // a category change on it writes 출석인정, and a reason changed to 생리 brings 출석인정 along.
  // Rows whose category and reason are both untouched are left as they are.
  selectedRows.forEach(rowOffset => {
    const categoryColumn = fields.category.column;
    const categoryKey = rowOffset + ':' + categoryColumn;
    const reasonChange = pending.get(rowOffset + ':' + fields.reason.column);
    if (!reasonChange && !pending.has(categoryKey)) return;
    const reason = reasonChange
      ? reasonChange.newComparable
      : attendanceAiExistingCellComparable_('reason', rows[rowOffset][fields.reason.column], roster);
    if (!attendanceAiIsMenstruationReason_(reason)) return;
    if (attendanceAiExistingCellComparable_('category', rows[rowOffset][categoryColumn], roster) === '출석인정') {
      pending.delete(categoryKey);
      return;
    }
    if (String((formulas[rowOffset] || [])[categoryColumn] || '').trim()) {
      throw attendanceAiExistingUpdateFailure_(
        'formula',
        '자동 계산이 들어 있는 칸은 안전을 위해 바꾸지 않았습니다.'
      );
    }
    pending.set(categoryKey, {
      rowOffset: rowOffset,
      rowNumber: firstDataRow + rowOffset,
      column: categoryColumn,
      field: 'category',
      newComparable: '출석인정',
      newValue: '출석인정'
    });
  });

  const changes = Array.from(pending.values()).sort((left, right) => (
    left.rowOffset - right.rowOffset || left.column - right.column
  ));
  return {
    ok: true,
    code: '',
    message: '',
    changes: changes,
    selectedRowCount: selectedRows.size,
    shouldSort: shouldSort
  };
}

function attendanceAiExistingUpdateCellData_(change) {
  if (change.field === 'date') {
    const parsed = attendanceAiParseDateKey_(change.newValue);
    if (!parsed) return null;
    return {
      userEnteredValue: {
        numberValue: Math.floor(parsed.date.getTime() / 86400000) + 25569
      }
    };
  }
  return change.newValue === ''
    ? {}
    : { userEnteredValue: { stringValue: String(change.newValue) } };
}

function buildAttendanceAiExistingUpdateBatch_(plan, sheetId) {
  if (!plan || !plan.ok || !Number.isSafeInteger(sheetId)) return null;
  const requests = [];
  for (let index = 0; index < plan.changes.length; index++) {
    const change = plan.changes[index];
    const cell = attendanceAiExistingUpdateCellData_(change);
    if (!cell) return null;
    requests.push({
      updateCells: {
        start: {
          sheetId: sheetId,
          rowIndex: change.rowNumber - 1,
          columnIndex: change.column
        },
        rows: [{ values: [cell] }],
        fields: 'userEnteredValue'
      }
    });
  }
  return { requests: requests };
}

function verifyAttendanceAiExistingUpdates_(plan, readState, rosterRows) {
  if (!plan || !plan.ok || !readState) return false;
  const rows = Array.isArray(readState.dataRows)
    ? readState.dataRows
    : (Array.isArray(readState.values) ? readState.values : null);
  if (!Array.isArray(rows)) return false;
  const roster = attendanceAiUpdateRoster_(rosterRows);
  for (let index = 0; index < plan.changes.length; index++) {
    const change = plan.changes[index];
    const row = rows[change.rowOffset];
    if (
      !Array.isArray(row)
      || attendanceAiExistingCellComparable_(
        change.field,
        row[change.column],
        roster
      ) !== change.newComparable
    ) {
      return false;
    }
  }
  return true;
}

/**
 * 1행에서 고친 자리가 AI 입력칸인지 본다.
 *
 * A열 이름표에 그대로 적는 분도 있고, B~K열 합친 칸을 고치면 구글이 편집 범위를
 * B1:K1 전체로 알려 주기도 한다. 두 가지를 모두 받는다.
 */
function isAttendanceAiInputRange_(column, numColumns) {
  if (column === 1) return numColumns === 1;
  if (column !== MONTHLY_ATTENDANCE_AI_INPUT_COL) return false;
  const boxWidth =
    MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1;
  return numColumns >= 1 && numColumns <= boxWidth;
}

/**
 * 월 시트 1행 입력칸 하나를 집어 온다. 월 시트가 아니면 아무것도 돌려주지 않는다.
 *
 * 이름만 보고 판단한다. 칸을 고를 때마다 도는 자리라서, 설정 탭을 읽는 무거운 검사를
 * 걸어 두면 클릭 한 번마다 시트가 느려지고 그 검사가 걸리는 순간 조용히 아무 일도
 * 일어나지 않는다(2026-07-27).
 */
function attendanceAiInputBoxFor_(sheet) {
  if (!sheet || typeof sheet.getRange !== 'function') return null;
  if (typeof sheet.getName !== 'function') return null;
  if (!/^\d{1,2}월$/.test(String(sheet.getName() || '').trim())) return null;
  return sheet.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW,
    MONTHLY_ATTENDANCE_AI_INPUT_COL,
    1,
    MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1
  );
}

/**
 * 칸을 고를 때마다 도는 장치.
 *
 * 옛 판의 회색 예시가 아직 실제 값으로 남아 있으면 지운다. 빈칸을 벗어나도
 * 예시를 다시 값으로 쓰지 않으며, 같은 안내는 B1 메모에서만 보여 준다.
 */
function onSelectionChange(e) {
  try {
    if (!mayRunLocalSheetTrigger_(e)) return;
    if (!e || !e.range || typeof e.range.getSheet !== 'function') return;
    const sheet = e.range.getSheet();
    const box = attendanceAiInputBoxFor_(sheet);
    if (!box) return;
    const inBox = e.range.getRow() === MONTHLY_ATTENDANCE_INPUT_ROW
      && e.range.getColumn() === MONTHLY_ATTENDANCE_AI_INPUT_COL;
    const shown = String(box.getValue() || '').trim();
    if (
      shown === MONTHLY_ATTENDANCE_AI_INPUT_HINT
      || shown === MONTHLY_ATTENDANCE_AI_INPUT_PLACEHOLDER
    ) {
      if (typeof box.clearContent === 'function') box.clearContent();
      else box.setValue('');
    }
    const noteCell = sheet.getRange(
      MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL, 1, 1
    );
    if (typeof noteCell.setNote === 'function') {
      noteCell.setNote(MONTHLY_ATTENDANCE_AI_INPUT_HINT);
    }
    if (inBox) {
      box.setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR);
    }
  } catch (err) {
    // 칸을 고를 때마다 도는 자리다 — 무슨 일이 있어도 시트에 오류를 띄우지 않는다.
  }
}

/** 1행을 이름표와 빈 입력칸으로 되돌린다. 안내는 B1 메모에만 둔다. */
function resetAttendanceAiInputRow_(sheet) {
  sheet.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, 1)
    .setValue(MONTHLY_ATTENDANCE_AI_INPUT_LABEL);
  const box = sheet.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW,
    MONTHLY_ATTENDANCE_AI_INPUT_COL,
    1,
    MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1
  );
  if (typeof box.clearContent === 'function') box.clearContent();
  else box.setValue('');
  box.setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR);
  const noteCell = sheet.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL, 1, 1
  );
  if (typeof noteCell.setNote === 'function') {
    noteCell.setNote(MONTHLY_ATTENDANCE_AI_INPUT_HINT);
  }
}

// 조용히 건너뛴 이유를 실행 기록에 남긴다. 1행 입력 편집에서만 부르므로 잡음이 없다.
// Apps Script에서는 실행 기록의 로그로, Node 시험에서는 stderr로 가서 결과 JSON을 더럽히지 않는다.
function attendanceAiSkipLog_(reason) {
  try {
    if (typeof console !== 'undefined' && typeof console.error === 'function') {
      console.error('AI 출결 입력 건너뜀 — ' + reason);
    }
  } catch (err) { /* 기록 실패는 동작에 영향 주지 않는다 */ }
}

// Gemini answers 429 (free-tier 15 requests/minute) and 503 ("high demand") for a
// moment; one such reply dropped the teacher's sentence (R11-1). The call happens
// before any Sheet write, so these transient codes are tried at most 3 times.
// One input may ask twice (a correction tried as a new entry, SHEET-AI-02); a shared
// budget keeps the whole input at three sends.
function callAttendanceAiGeminiWithRetry_(send, sleep, budget) {
  const transient = [429, 500, 502, 503, 504];
  const delaysMs = [5000, 15000];
  const left = budget && typeof budget === 'object' ? budget : { remaining: delaysMs.length + 1 };
  let code = 0;
  for (let attempt = 0; attempt <= delaysMs.length && left.remaining > 0; attempt++) {
    if (attempt) sleep(delaysMs[attempt - 1]);
    left.remaining--;
    const reply = send();
    code = reply.code;
    if (code >= 200 && code < 300) return JSON.parse(reply.text());
    if (transient.indexOf(code) < 0) break;
  }
  throw new Error('Gemini HTTP ' + code);
}

/**
 * 설치형 감지기와 대시보드가 함께 쓰는 AI 출결 입력의 바깥 연결이다.
 * handleAttendanceAiEdit가 시험용 연결을 받지 않았을 때만 만든다.
 */
function attendanceAiDefaultPorts_(source) {
  // Built once per input: both Gemini requests of that input share these sends (SHEET-AI-02).
  const geminiBudget = { remaining: 3 };
  return {
    getTargetSpreadsheetId: () => PropertiesService.getScriptProperties()
      .getProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY),
    getGeminiApiKey: () => attendanceAiGeminiApiKey_(source),
    getTodayDate: () => Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd'),
    authorizeWrite: eventDate => authorizeAttendanceOperation_('historical-manual', eventDate),
    tryDocumentLock: () => {
      const lock = LockService.getDocumentLock();
      return lock && lock.tryLock(5000) ? lock : null;
    },
    readHeaderRow: targetSheet => targetSheet
      .getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, 12)
      .getValues()[0],
    resetInputRow: targetSheet => resetAttendanceAiInputRow_(targetSheet),
    // 잠금을 풀기 전에 입력칸 비우기와 결과 지문을 시트에 반영한다(SHEET-DASH-05).
    flush: () => SpreadsheetApp.flush(),
    reStripe: targetSheet => reStripeSheet_(targetSheet),
    sortRows: targetSheet => sortMonthlyAttendanceRows_(targetSheet, 'date'),
    readRosterRows: (spreadsheet, context) => {
      const rosterSheet = spreadsheet.getSheetByName(context.rosterSheetName);
      if (!rosterSheet || rosterSheet.getLastRow() < 2) return [];
      return rosterSheet.getRange(2, 1, rosterSheet.getLastRow() - 1, ROSTER_HEADERS.length).getValues();
    },
    readHolidayDateKeys: (spreadsheet, calendarYear) => {
      const holidaySheet = spreadsheet.getSheetByName(getHolidaySheetName_());
      if (!holidaySheet) return null;
      const holidays = loadHolidaySet_(spreadsheet);
      const yearPrefix = String(calendarYear) + '-';
      const hasCurrentYear = Array.from(holidays).some(
        value => String(value).indexOf(yearPrefix) === 0
      );
      return hasCurrentYear ? holidays : null;
    },
    showMessage: message => {
      try {
        source.toast(String(message), 'AI 출결 입력', 7);
      } catch (err) {
        // 화면 안내가 막혀도 출결행 안전 판단은 그대로 유지한다.
      }
    },
    callGemini: (request, apiKey) => callAttendanceAiGeminiWithRetry_(
      () => {
        const response = UrlFetchApp.fetch(ATTENDANCE_AI_INTERACTIONS_URL, {
          method: 'post',
          contentType: 'application/json',
          headers: { 'x-goog-api-key': apiKey },
          payload: JSON.stringify(request),
          muteHttpExceptions: true
        });
        return { code: response.getResponseCode(), text: () => response.getContentText() };
      },
      milliseconds => Utilities.sleep(milliseconds),
      geminiBudget
    ),
    readWriteState: targetSheet => ({
      headerRow: targetSheet
        .getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, 12)
        .getValues()[0],
      lastDataRow: targetSheet.getLastRow(),
      rowCount: targetSheet.getMaxRows(),
      sheetId: targetSheet.getSheetId()
    }),
    readExistingAttendanceRows: targetSheet => {
      const lastRow = targetSheet.getLastRow();
      const rowCount = Math.max(0, lastRow - MONTHLY_ATTENDANCE_HEADER_ROW);
      if (!rowCount) {
        return {
          dataRows: [],
          values: [],
          formulas: [],
          firstDataRow: MONTHLY_ATTENDANCE_DATA_START_ROW,
          sheetId: targetSheet.getSheetId()
        };
      }
      const range = targetSheet.getRange(
        MONTHLY_ATTENDANCE_DATA_START_ROW,
        1,
        rowCount,
        MONTHLY_ATTENDANCE_LAST_DATA_COL
      );
      const values = range.getValues();
      return {
        dataRows: values,
        values: values,
        formulas: range.getFormulas(),
        firstDataRow: MONTHLY_ATTENDANCE_DATA_START_ROW,
        sheetId: targetSheet.getSheetId()
      };
    },
    batchUpdate: (spreadsheetId, request) => (
      Sheets.Spreadsheets.batchUpdate(request, spreadsheetId)
    ),
    // 고친 결과의 확인 읽기. Sheets API로 쓴 뒤 같은 실행의 SpreadsheetApp 읽기는 쓰기 전
    // 값을 돌려줄 수 있어(2026-10-01 현장) flush한 뒤 Sheets API로 읽는다(SHEET-AI-03).
    readUpdatedAttendanceRows: (targetSheet, rowCount) => ({
      values: rowCount > 0
        ? tmDashboardReadRowsFresh_(source, targetSheet, MONTHLY_ATTENDANCE_DATA_START_ROW,
          MONTHLY_ATTENDANCE_DATA_START_ROW + rowCount - 1, MONTHLY_ATTENDANCE_LAST_DATA_COL)
        : [],
      sheetId: targetSheet.getSheetId()
    }),
    // 기록 결과를 확인하지 못한 문장과, 기록했지만 입력칸에 남은 문장만 지문을 남긴다.
    recordOutcome: (targetSheet, text, applied, keptInBox) => (
      attendanceAiRecordOutcome_(targetSheet, text, applied, keptInBox)
    )
  };
}

function attendanceAiPortsWithOverrides_(defaults, portOverrides) {
  // 대시보드가 바꿀 수 있는 연결은 이 셋뿐이다. Gemini 호출·권한 확인·쓰기 연결은
  // 감지기와 같은 것을 써야 두 번째 AI 경로가 생기지 않는다(SHEET-AI-01).
  const ports = Object.assign({}, defaults);
  if (portOverrides && typeof portOverrides === 'object') {
    ['showMessage', 'resetInputRow', 'stillCurrent'].forEach(name => {
      if (typeof portOverrides[name] === 'function') ports[name] = portOverrides[name];
    });
  }
  return ports;
}

/** 문장 앞뒤 공백을 뺀 SHA-256 앞 16바이트다. 대시보드가 입력칸 문장과 대조한다. */
function attendanceAiSentenceHash_(sentence) {
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256, String(sentence || '').trim(), Utilities.Charset.UTF_8
  );
  return bytes.slice(0, 16)
    .map(value => ('0' + ((Number(value) + 256) % 256).toString(16)).slice(-2)).join('');
}

function attendanceAiUncertainEntries_(raw) {
  try {
    const parsed = JSON.parse(String(raw || ''));
    return Array.isArray(parsed) ? parsed.filter(item => item && typeof item.h === 'string') : [];
  } catch (err) {
    return [];
  }
}

// 결과를 확인하지 못한 문장은 입력칸에 남는다. 그 문장의 지문만 월 탭별로 최근 5개까지
// 남겨 대시보드가 [다시 입력] 전에 경고하게 한다. 기록했는데 입력칸을 비우지 못한 문장은
// e 표시를 붙여 남긴다(입력하지 않은 문장처럼 보이지 않게). 기록되고 비운 문장은 지운다.
function attendanceAiRecordOutcome_(sheet, sentence, applied, keptInBox) {
  const props = PropertiesService.getDocumentProperties();
  const key = ATTENDANCE_AI_UNCERTAIN_PROPERTY_PREFIX + sheet.getSheetId();
  const saved = attendanceAiUncertainEntries_(props.getProperty(key));
  const remember = !applied || keptInBox === true;
  if (!remember && !saved.length) return;
  const hash = attendanceAiSentenceHash_(sentence);
  const kept = saved.filter(item => item.h !== hash);
  if (remember) kept.push(applied ? { h: hash, at: Date.now(), e: 1 } : { h: hash, at: Date.now() });
  const recent = kept.slice(-5);
  if (recent.length) props.setProperty(key, JSON.stringify(recent));
  else props.deleteProperty(key);
}

function handleAttendanceAiEdit(e, testPorts, portOverrides) {
  // 설치형 감지기는 실제 편집자 주소가 숨겨질 수 있다. 주소가 보이면 허용 계정인지
  // 확인하고, 주소가 안 보여도 감지기를 만든 계정은 허용 계정인지 따로 확인한다.
  requireGoeduTeacherAccount_({ event: e, requireEffectiveUser: true });
  if (
    !e
    || !e.source
    || typeof e.source.getId !== 'function'
    || !e.range
    || typeof e.range.getSheet !== 'function'
    || typeof e.range.getRow !== 'function'
    || typeof e.range.getColumn !== 'function'
    || typeof e.range.getNumRows !== 'function'
    || typeof e.range.getNumColumns !== 'function'
    || e.range.getRow() !== MONTHLY_ATTENDANCE_INPUT_ROW
    || e.range.getNumRows() !== 1
  ) {
    // 1행이 아닌 보통 편집은 전부 여기로 온다 — 기록을 남기면 잡음이라 남기지 않는다.
    return { status: 'ignored' };
  }
  if (!isAttendanceAiInputRange_(e.range.getColumn(), e.range.getNumColumns())) {
    attendanceAiSkipLog_(
      '1행이지만 입력칸 밖 편집(열 ' + e.range.getColumn() + ', 폭 ' + e.range.getNumColumns() + ')'
    );
    return { status: 'ignored' };
  }

  // 한 칸짜리 편집이면 구글이 알려 준 값만 쓴다. 감지기가 도는 사이 그 칸이 다시
  // 바뀌었을 수 있어 나중 값을 읽으면 엉뚱한 문장을 처리하게 된다.
  // 합친 칸 전체로 알려 온 편집(붙여넣기 등)에는 값을 함께 주지 않으므로 그때만 직접 읽는다.
  let sentence = typeof e.value === 'string' ? e.value : '';
  if (!sentence.trim() && e.range.getNumColumns() > 1) {
    try {
      const inBox = e.range.getValue();
      sentence = String(inBox === null || inBox === undefined ? '' : inBox);
    } catch (err) {
      sentence = '';
    }
  }
  if (!sentence.trim()) {
    attendanceAiSkipLog_('입력칸이 비어 있음(지우기 또는 빈 편집)');
    return { status: 'ignored' };
  }

  const sheet = e.range.getSheet();
  if (!sheet || typeof sheet.getName !== 'function') return { status: 'ignored' };
  const source = e.source;
  const ports = testPorts || attendanceAiPortsWithOverrides_(attendanceAiDefaultPorts_(source), portOverrides);

  let targetSpreadsheetId;
  let apiKey;
  try {
    targetSpreadsheetId = String(ports.getTargetSpreadsheetId() || '').trim();
    apiKey = String(ports.getGeminiApiKey() || '').trim();
  } catch (err) {
    attendanceAiSkipLog_('대상 시트/키를 읽지 못함: ' + (err && err.message ? err.message : err));
    ports.showMessage('AI 출결 입력 연결을 읽지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 출결 기능 상태를 확인해 주세요.');
    return { status: 'check_required' };
  }
  if (!targetSpreadsheetId || !apiKey) {
    attendanceAiSkipLog_(
      !targetSpreadsheetId
        ? 'AI 입력이 켜진 기록(대상 시트 번호)이 없음 — 처음 설정을 다시 실행 필요'
        : 'Gemini API 키를 설정 탭에서 찾지 못함'
    );
    ports.showMessage(
      !targetSpreadsheetId
        ? 'AI 출결 입력 연결을 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 출결 기능 상태를 확인해 주세요.'
        : 'AI 출결 입력에 필요한 Gemini 연결 키를 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 확인해 주세요.'
    );
    return { status: 'disabled' };
  }
  if (String(source.getId()) !== targetSpreadsheetId) {
    attendanceAiSkipLog_('이 시트가 AI 입력 대상으로 기록된 시트와 다름');
    ports.showMessage('이 출석부는 현재 AI 입력 대상으로 연결된 출석부가 아닙니다.');
    return { status: 'ignored' };
  }

  let sheetContext = testPorts && testPorts.context ? testPorts.context : null;
  if (!sheetContext) {
    try {
      const configSheet = source.getSheetByName(CONFIG_SHEET_NAME);
      if (!configSheet) {
        ports.showMessage('이 출석부의 자동 처리 설정을 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부를 다시 확인해 주세요.');
        return { status: 'check_required' };
      }
      const config = readAttendanceConfigStrict_(source);
      const monthSheets = attendanceMonthSheetsFor_(source, config);
      const role = monthSheets.find(item => item.sheet.getSheetId() === sheet.getSheetId());
      if (!role) {
        attendanceAiSkipLog_('월별 연결 기록에 없는 탭');
        ports.showMessage('현재 탭을 연결된 월별 출결표로 확인하지 못했어요. 입력 문장을 그대로 남겼습니다.');
        return { status: 'ignored' };
      }
      sheetContext = {
        schoolYear: String(config.SCHOOL_YEAR || '').trim(),
        month: role.month,
        configuredMonthNames: monthSheets.map(item => item.sheet.getName()),
        rosterSheetName: String(config.ROSTER_SHEET_NAME || '학생명단').trim()
      };
    } catch (err) {
      attendanceAiSkipLog_('설정 탭을 읽지 못함: ' + (err && err.message ? err.message : err));
      ports.showMessage('이 출석부의 자동 처리 설정을 읽지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부를 다시 확인해 주세요.');
      return { status: 'check_required' };
    }
  }

  const monthName = sheet.getName();
  if (
    !sheetContext
    || !Array.isArray(sheetContext.configuredMonthNames)
    || sheetContext.configuredMonthNames.indexOf(monthName) < 0
  ) {
    attendanceAiSkipLog_('월별 연결 기록과 현재 탭이 일치하지 않음');
    ports.showMessage('현재 탭을 연결된 월별 출결표로 확인하지 못했어요. 입력 문장은 그대로 남겨 두었습니다.');
    return { status: 'ignored' };
  }
  if (
    getAttendanceAiCalendarYear_(sheetContext.schoolYear, sheetContext.month) === null
  ) {
    attendanceAiSkipLog_(
      '출석부의 고정 학년도를 확인하지 못함 — Teacher Manager에서 원래 연결 기록을 복구해야 함'
    );
    ports.showMessage('출석부의 학년도를 확인하지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 현재 출석부의 학년도를 확인해 주세요.');
    return { status: 'check_required' };
  }
  const expectedHeaders = INPUT_HEADERS.concat(MONTHLY_CHAT_RESULT_HEADERS);
  const headersMatch = values => (
    Array.isArray(values)
    && values.length === expectedHeaders.length
    && values.every((value, index) => value === expectedHeaders[index])
  );
  try {
    const actualHeaders = ports.readHeaderRow(sheet);
    if (!headersMatch(actualHeaders)) {
      attendanceAiSkipLog_(
        '2행 제목 줄이 기대와 다름. 실제: ' + JSON.stringify(actualHeaders) +
        ' / 기대: ' + JSON.stringify(expectedHeaders)
      );
      ports.showMessage('월별 출결표의 제목 줄이 바뀌어 AI 입력을 멈췄어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부의 상태를 확인해 주세요.');
      return { status: 'ignored' };
    }
  } catch (err) {
    attendanceAiSkipLog_('2행 제목 줄을 읽지 못함: ' + (err && err.message ? err.message : err));
    ports.showMessage('월별 출결표를 읽지 못했습니다. 잠시 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요.');
    return { status: 'ignored' };
  }

  let lock = null;
  let applied = false;
  let writeAttempted = false;
  let editingExisting = false;
  let outcome = null;
  try {
    lock = ports.tryDocumentLock();
    if (!lock) {
      attendanceAiSkipLog_('다른 처리가 도는 중(자물쇠 잡기 실패) — 잠시 뒤 다시 입력');
      ports.showMessage('다른 AI 입력을 처리 중입니다. 잠시 뒤 같은 문장으로 다시 시도해 주세요.');
      return { status: 'busy' };
    }
    // 대시보드가 입력칸 문장을 다시 처리할 때, 잠금을 기다리는 사이 감지기가 먼저
    // 처리했으면 멈춘다. 감지기는 입력칸 비우기와 결과 지문을 잠금을 풀기 전에 끝내므로,
    // 입력칸이 바뀌었으면 stale, 결과 미확인 지문이 있으면 그 표시를 돌려준다(SHEET-DASH-05).
    if (typeof ports.stillCurrent === 'function') {
      const current = ports.stillCurrent(sheet);
      if (current !== true) {
        return { status: current === 'uncertain-pending' || current === 'entered-pending' ? current : 'stale' };
      }
    }
    const today = String(ports.getTodayDate() || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(today)) {
      attendanceAiSkipLog_('한국 기준 오늘 날짜를 확인하지 못함');
      ports.showMessage('오늘 날짜를 확인하지 못했습니다. 잠시 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요.');
      return { status: 'check_required' };
    }
    const requestContext = {
      schoolYear: sheetContext.schoolYear,
      month: sheetContext.month,
      today: today,
      sentence: sentence
    };
    const calendarYear = getAttendanceAiCalendarYear_(
      requestContext.schoolYear,
      requestContext.month
    );
    const holidayDateKeys = ports.readHolidayDateKeys(source, calendarYear);
    if (!(holidayDateKeys instanceof Set)) {
      attendanceAiSkipLog_(
        '휴일 탭에서 이 학년도의 휴일을 확인하지 못함 — 휴일 탭을 확인한 뒤 다시 입력'
      );
      ports.showMessage(calendarYear + '년 휴일을 확인하지 못했어요. [휴일] 탭의 A열 날짜와 B열 휴일 이름을 확인해 주세요.');
      return { status: 'check_required' };
    }

    editingExisting = isAttendanceAiExistingUpdateSentence_(sentence);
    // Set when a correction sentence names a student with no row on that date (SHEET-AI-02).
    let newEntryFallback = null;
    if (editingExisting) {
      const rosterRows = ports.readRosterRows(source, sheetContext);
      const request = buildAttendanceAiExistingUpdateGeminiRequest_(sentence, requestContext, rosterRows);
      const interaction = ports.callGemini(request, apiKey);
      const payload = extractAttendanceAiGeminiPayload_(interaction);
      const existingState = ports.readExistingAttendanceRows(sheet);
      let plan;
      try {
        plan = planAttendanceAiExistingUpdates_(
          payload,
          rosterRows,
          existingState,
          requestContext,
          holidayDateKeys
        );
      } catch (planError) {
        if (planError && planError.ok === false && planError.message) {
          ports.showMessage(planError.message);
          return { status: 'check_required' };
        }
        throw planError;
      }
      if (plan && plan.code === 'target_missing' && attendanceAiNewEventFallbackAllowed_(sentence)) {
        // "오늘 OO 2교시부터 생리조퇴야. 질병 아니고 출석인정이야" with no row for OO that day is a
        // new event. Nothing has been written; ask Gemini once more in new-entry mode inside the
        // same three-send budget and check the result against these rows below (C7, SHEET-AI-01).
        newEntryFallback = { state: existingState, message: plan.message };
        editingExisting = false;
      } else if (!plan || !plan.ok) {
        attendanceAiSkipLog_(
          '기존 줄 수정 대상을 확정하지 못함' + (plan && plan.code ? ': ' + plan.code : '')
        );
        ports.showMessage(
          plan && plan.message
            ? plan.message
            : '수정할 기존 줄을 정확히 찾지 못했습니다. 입력 문장을 그대로 남겨 두었습니다.'
        );
        return { status: 'check_required' };
      }
      if (!newEntryFallback) {
        const existingSheetId = Number(existingState && existingState.sheetId);
        const sheetId = Number.isSafeInteger(existingSheetId)
          ? existingSheetId
          : sheet.getSheetId();
        const batchRequest = buildAttendanceAiExistingUpdateBatch_(plan, sheetId);
        if (!batchRequest) {
          ports.showMessage('입력한 문장에서 바꿀 출결 내용을 정리하지 못했어요. 날짜·학생·바꿀 내용을 나누어 적어 주세요. 입력 문장은 그대로 남겨 두었습니다.');
          return { status: 'check_required' };
        }
        let finalState = existingState;
        if (batchRequest.requests.length) {
          if (ports.authorizeWrite) {
            // Existing-update validation already fixes every selected row to this
            // immutable workbook year/month; validate its actual event date too.
            const first = plan.changes[0];
            const value = first.field === 'date' ? first.newValue : existingState.values[first.rowOffset][0];
            ports.authorizeWrite(attendanceAiDateKeyFromValue_(value));
          }
          writeAttempted = true;
          const response = ports.batchUpdate(targetSpreadsheetId, batchRequest);
          if (
            !response
            || typeof response !== 'object'
            || response.spreadsheetId !== targetSpreadsheetId
          ) {
            ports.showMessage(
              '수정 결과를 확인하지 못했습니다. 바로 다시 입력하지 말고 출결표를 먼저 확인해 주세요.'
            );
            return { status: 'check_required', uncertain: true, updatedExisting: true };
          }
          const existingRows = Array.isArray(existingState.values) ? existingState.values.length : 0;
          const verifiedState = typeof ports.readUpdatedAttendanceRows === 'function'
            ? ports.readUpdatedAttendanceRows(sheet, existingRows)
            : ports.readExistingAttendanceRows(sheet);
          if (!verifyAttendanceAiExistingUpdates_(plan, verifiedState, rosterRows)) {
            ports.showMessage(
              '수정 결과를 확인하지 못했습니다. 바로 다시 입력하지 말고 출결표를 먼저 확인해 주세요.'
            );
            return { status: 'check_required', uncertain: true, updatedExisting: true };
          }
          finalState = verifiedState;
        }
        applied = true;
        // 고친 줄을 화면에 자료로 보이도록 확인한 값 그대로 돌려준다(감지기는 쓰지 않는다).
        const changedOffsets = [];
        plan.changes.forEach(change => {
          if (changedOffsets.indexOf(change.rowOffset) < 0) changedOffsets.push(change.rowOffset);
        });
        const finalRows = finalState && Array.isArray(finalState.values) ? finalState.values : [];
        outcome = {
          status: 'applied',
          rows: plan.selectedRowCount,
          updatedExisting: true,
          records: changedOffsets.map(offset => finalRows[offset]).filter(Array.isArray).map(attendanceAiRowSummary_)
        };
        if (plan.shouldSort) {
          try {
            if (!ports.sortRows(sheet)) ports.reStripe(sheet);
          } catch (err) {
            // 수정은 이미 확인됐다. 정렬 실패 때문에 같은 수정을 다시 요청하지 않는다.
          }
        }
        return outcome;
      }
    }

    const rosterRows = ports.readRosterRows(source, sheetContext);
    const request = buildAttendanceAiGeminiRequest_(sentence, requestContext, rosterRows);
    const interaction = ports.callGemini(request, apiKey);
    const payload = extractAttendanceAiGeminiPayload_(interaction);
    const crossesIntoAnotherMonth = (
      payload
      && Array.isArray(payload.records)
      && payload.records.some(record => {
        if (!record || typeof record.date !== 'string' || typeof record.end_date !== 'string') {
          return false;
        }
        const start = /^(\d{4})-(\d{2})-(\d{2})$/.exec(record.date);
        const end = /^(\d{4})-(\d{2})-(\d{2})$/.exec(record.end_date);
        if (!start || !end) return false;
        return Number(start[1]) === calendarYear
          && Number(start[2]) === Number(requestContext.month)
          && (
            Number(end[1]) !== calendarYear
            || Number(end[2]) !== Number(requestContext.month)
          );
      })
    );
    if (crossesIntoAnotherMonth) {
      ports.showMessage(
        '기간이 다음 달까지 이어집니다. 각 월 시트에서 나누어 입력해 주세요.'
      );
      return { status: 'check_required' };
    }
    const validation = validateAttendanceAiRecordsDetailed_(
      payload,
      rosterRows,
      requestContext,
      holidayDateKeys
    );
    const records = validation.records;
    if (!records) {
      attendanceAiSkipLog_(
        '문장을 출결로 확정하지 못함 — 학생 이름이 학생명단과 정확히 일치하는지, 날짜의 달이 이 시트의 달과 같은지 확인'
      );
      ports.showMessage(validation.message);
      return { status: 'check_required' };
    }
    if (!records.length) {
      attendanceAiSkipLog_('입력한 기간에 수업일이 없음');
      ports.showMessage('입력한 기간에 수업일이 없습니다.');
      return { status: 'check_required' };
    }
    if (newEntryFallback) {
      // Write only an event the sentence itself names (결석·지각·조퇴·결과) for a student and
      // date that still have no row; a second row for the same day is never added (C7).
      const fallbackRoster = attendanceAiUpdateRoster_(rosterRows);
      const state = newEntryFallback.state || {};
      const existingRows = Array.isArray(state.dataRows)
        ? state.dataRows
        : (Array.isArray(state.values) ? state.values : []);
      const taken = new Set(existingRows.filter(Array.isArray).map(item => (
        attendanceAiDateKeyFromValue_(item[0]) + '\u0000'
        + attendanceAiExistingCellComparable_('student', item[1], fallbackRoster)
      )));
      const kindNamed = records.every(record => (
        !!record.kind && sentence.indexOf(record.kind.replace(/함$/, '')) !== -1
      ));
      if (!kindNamed || records.some(record => taken.has(record.date + '\u0000' + record.rosterCombined))) {
        attendanceAiSkipLog_('고침 문장을 새 줄로 넣지 않음 — 문장에 출결 종류가 없거나 같은 날짜·학생 줄이 있음');
        ports.showMessage(newEntryFallback.message);
        return { status: 'check_required' };
      }
    }

    const writeState = ports.readWriteState(sheet);
    if (
      !writeState
      || !headersMatch(writeState.headerRow)
      || typeof writeState.lastDataRow !== 'number'
      || !Number.isSafeInteger(writeState.lastDataRow)
      || typeof writeState.sheetId !== 'number'
      || !Number.isSafeInteger(writeState.sheetId)
    ) {
      ports.showMessage('출결표의 저장 위치를 확인하지 못했습니다. 입력 문장을 남겨 두었습니다.');
      return { status: 'check_required' };
    }
    const batchRequest = buildAttendanceAiBatchUpdate_(records, writeState);
    if (!batchRequest) {
      ports.showMessage('입력한 문장을 출결표에 넣을 내용으로 정리하지 못했어요. 날짜·학생·출결 내용을 나누어 적어 주세요. 입력 문장은 그대로 남겨 두었습니다.');
      return { status: 'check_required' };
    }
    if (ports.authorizeWrite) ports.authorizeWrite(records[0].date);
    writeAttempted = true;
    const response = ports.batchUpdate(targetSpreadsheetId, batchRequest);
    if (
      !response
      || typeof response !== 'object'
      || response.spreadsheetId !== targetSpreadsheetId
    ) {
      ports.showMessage(
        '등록 결과를 확인하지 못했습니다. 바로 다시 입력하지 말고 출결표에 줄이 생겼는지 먼저 확인해 주세요.'
      );
      return { status: 'check_required', uncertain: true };
    }
    applied = true;
    outcome = {
      status: 'applied',
      rows: records.length,
      startRow: Math.max(MONTHLY_ATTENDANCE_HEADER_ROW, writeState.lastDataRow) + 1,
      // 넣은 줄을 화면에 자료로 보인다. 신고서·첨부는 buildAttendanceAiBatchUpdate_와 같이 미제출이다.
      records: records.map(record => ({
        date: record.date, student: String(record.rosterCombined || ''), category: String(record.category || ''),
        kind: String(record.kind || ''), reason: String(record.reason || ''), period: String(record.period || ''),
        report: '미제출', attach: '미제출'
      }))
    };
    // 맨 아래에 붙은 새 줄을 기존 출결과 함께 날짜순으로 다시 모은다.
    // 정렬 함수가 끝난 정확한 자리에서 줄무늬도 다시 칠한다. 시트 모양이 예상과 달라
    // 안전 정렬을 멈춘 경우에는 현재 순서를 바꾸지 않고 줄무늬만 다시 칠한다.
    try {
      if (!ports.sortRows(sheet)) ports.reStripe(sheet);
    } catch (err) {
      // 자료 기록은 이미 끝났다. 정렬·색칠 실패 때문에 같은 출결을 다시 넣지는 않는다.
    }
    return outcome;
  } catch (err) {
    // Gemini HTTP 오류(키 불량 등)와 기록 단계 오류가 전부 여기로 온다 — 이유를 남긴다.
    attendanceAiSkipLog_('처리 중 오류: ' + (err && err.message ? err.message : err));
    ports.showMessage(
      writeAttempted
        ? (
            editingExisting
              ? '수정 결과를 확인하지 못했습니다. 바로 다시 입력하지 말고 출결표를 먼저 확인해 주세요.'
              : '등록 결과를 확인하지 못했습니다. 바로 다시 입력하지 말고 출결표에 줄이 생겼는지 먼저 확인해 주세요.'
          )
        // The same text typed again over the kept sentence fires no edit event (R11-7).
        : String(err && err.message ? err.message : err) === 'Gemini HTTP 429'
          ? 'Gemini 무료 사용량(1분에 15번)을 넘었어요. 입력 문장을 남겨 두었습니다. 1분 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요.'
          : 'AI 출결 입력을 처리하지 못했습니다. 입력 문장을 남겨 두었습니다. 잠시 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요.'
    );
    // 쓰기 요청을 보낸 뒤의 오류는 기록 여부를 모른다(대시보드가 다시 처리를 막는다).
    return { status: 'check_required', uncertain: writeAttempted, updatedExisting: writeAttempted && editingExisting };
  } finally {
    // 입력칸 비우기와 결과 지문은 잠금을 잡은 채로 끝내고 시트에 반영한 뒤에 잠금을 푼다.
    // 풀고 나서 하면, 잠금을 기다리던 대시보드의 [다시 입력]이 아직 남은 문장을 보고
    // 같은 줄을 한 번 더 넣을 수 있다(SHEET-DASH-05, C7).
    // 저장 결과까지 확인된 편집만 되돌린다. 실패했거나 다른 처리가 돌고 있으면
    // 적은 문장을 그대로 남겨 고치거나 다시 시도할 수 있게 한다.
    let keptInBox = false;
    if (lock && applied) {
      try {
        ports.resetInputRow(sheet);
      } catch (err) {
        // 되돌리지 못해도 이미 만든 출결행은 그대로 둔다. 남은 문장은 지문으로 표시한다.
        keptInBox = true;
        if (outcome) outcome.inputBoxKept = true;
      }
    }
    if (lock && (applied || writeAttempted) && typeof ports.recordOutcome === 'function') {
      try {
        ports.recordOutcome(sheet, sentence, applied, keptInBox);
      } catch (err) {
        // 지문 기록은 경고용이다. 실패해도 출결행과 결과는 그대로다.
      }
    }
    if (lock && typeof ports.flush === 'function') {
      try {
        ports.flush();
      } catch (err) {
        // 반영에 실패해도 잠금은 푼다. Apps Script는 실행이 끝날 때 남은 변경을 반영한다.
      }
    }
    if (lock && typeof lock.releaseLock === 'function') {
      try {
        lock.releaseLock();
      } catch (err) {
        // 이미 끝난 요청을 다시 보내거나 Sheet에 실패 표시를 쓰지 않는다.
      }
    }
  }
}

/** 확인한 출결 줄(A~H)을 화면에 보일 값으로 옮긴다. */
function attendanceAiRowSummary_(row) {
  const text = value => String(value === null || value === undefined ? '' : value).trim();
  return {
    date: attendanceAiDateKeyFromValue_(row[0]), student: text(row[1]), category: text(row[2]),
    kind: text(row[3]), reason: text(row[4]), period: text(row[5]), report: text(row[6]), attach: text(row[7])
  };
}

/*************************************************
 * Teacher Manager 정식 출석부에서 1행 AI 입력 켜기
 *************************************************/

/** 설정 시트를 새로 만들지 않고 값 하나만 읽는다. 읽지 못하면 빈 값으로 본다. */
function readConfigValueReadOnly_(key) {
  try {
    return readConfigValueReadOnlyFor_(SpreadsheetApp.getActiveSpreadsheet(), key);
  } catch (err) {
    return '';
  }
}

/** 명시적으로 받은 출석부의 설정 탭만 읽는다. 없으면 만들지 않는다. */
function readConfigValueReadOnlyFor_(spreadsheet, key) {
  try {
    const sheet = spreadsheet && typeof spreadsheet.getSheetByName === 'function'
      ? spreadsheet.getSheetByName(CONFIG_SHEET_NAME)
      : null;
    if (!sheet) return '';
    const value = readConfigMapFromSheet_(sheet)[key];
    return String(value === undefined || value === null ? '' : value).trim();
  } catch (err) {
    return '';
  }
}

/**
 * 지금 열려 있는 파일이 1행 AI 입력을 켤 수 있는 정식 출석부인지 확인한다.
 * 확인하지 못하면 켤 수 없다고 본다.
 */
function attendanceAiWorkbookState_() {
  try {
    return attendanceAiWorkbookStateFor_(SpreadsheetApp.getActiveSpreadsheet());
  } catch (err) {
    return {
      ok: false,
      spreadsheetId: '',
      message: '지금 열려 있는 파일 정보를 읽지 못했습니다. 시트를 다시 열고 눌러 주세요.'
    };
  }
}

/** 명시적으로 받은 출석부가 AI 입력을 켤 수 있는 정식 출석부인지 확인한다. */
function attendanceAiWorkbookStateFor_(spreadsheet) {
  let spreadsheetId = '';
  try {
    spreadsheetId = String(spreadsheet && spreadsheet.getId() || '').trim();
  } catch (err) {
    return {
      ok: false,
      spreadsheetId: '',
      message: '지금 열려 있는 파일 정보를 읽지 못했습니다. 시트를 다시 열고 눌러 주세요.'
    };
  }
  if (!spreadsheetId) {
    return {
      ok: false,
      spreadsheetId: '',
      message: '지금 열려 있는 파일 번호를 읽지 못했습니다. 시트를 다시 열고 눌러 주세요.'
    };
  }
  // 이 시트에서 이미 켠 적이 있으면 이름과 상관없이 통과시킨다.
  // 사본이 매일 쓰는 시트가 되면 이름을 다듬는 것이 자연스러운데,
  // 이름으로만 판단하면 그 순간 켜기 메뉴가 사라져 키를 다시 넣을 수 없다.
  let alreadyEnabledHere = '';
  try {
    alreadyEnabledHere = String(
      PropertiesService.getScriptProperties()
        .getProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY) || ''
    ).trim();
  } catch (err) {
    alreadyEnabledHere = '';
  }
  if (alreadyEnabledHere === spreadsheetId) {
    return { ok: true, spreadsheetId: spreadsheetId, message: '' };
  }
  // Teacher Manager가 정식으로 만든 시트는 설정값으로만 알아본다.
  // 파일 이름은 사용자가 바꿀 수 있으므로 AI 허용 근거로 쓰지 않는다.
  if (readConfigValueReadOnlyFor_(spreadsheet, ATTENDANCE_AI_ALLOWED_SETTING) === ATTENDANCE_AI_ALLOWED_VALUE) {
    return { ok: true, spreadsheetId: spreadsheetId, message: '' };
  }
  return {
    ok: false,
    spreadsheetId: spreadsheetId,
    message:
      '이 파일을 Teacher Manager에서 만든 출석부로 확인하지 못했어요.\n\n' +
      'Teacher Manager의 [Google 연결 → 출결]에서 [사용할 출석부 고르기]를 눌러 이번에 사용할 파일을 선택해 주세요. 다른 파일은 그대로 두셔도 됩니다.'
  };
}

/**
 * 이 시트에서 쓸 Gemini 키를 찾는다.
 *
 * 설정 탭 GEMINI_API_KEY가 먼저다 — 컴퓨터의 티처 매니저 연결 화면에 한 번 넣으면
 * 그 값이 여기로 들어오므로 선생님이 시트에서 다시 붙여넣지 않아도 된다.
 * 예전 판에서 시트 메뉴로 직접 넣어 둔 분은 계정에 저장된 값을 그대로 쓴다.
 */
function attendanceAiGeminiApiKey_(spreadsheet) {
  let fromSettings = '';
  try {
    const sheet = spreadsheet && typeof spreadsheet.getSheetByName === 'function'
      ? spreadsheet.getSheetByName(CONFIG_SHEET_NAME)
      : null;
    if (sheet) {
      const value = readConfigMapFromSheet_(sheet)[ATTENDANCE_AI_GEMINI_API_KEY_SETTING];
      fromSettings = String(value === undefined || value === null ? '' : value).trim();
    }
  } catch (err) {
    fromSettings = '';
  }
  if (fromSettings) return fromSettings;
  try {
    return String(
      PropertiesService.getUserProperties()
        .getProperty(ATTENDANCE_AI_GEMINI_API_KEY_PROPERTY) || ''
    ).trim();
  } catch (err) {
    return '';
  }
}

/** 붙여넣은 값이 Gemini API 키 모양인지만 본다. 실제 통신은 하지 않는다. */
function isAttendanceAiApiKeyShape_(value) {
  const key = String(value === undefined || value === null ? '' : value).trim();
  if (key.length < 20 || key.length > 200) return false;
  // 마침표를 받는다 — 요즘 구글이 내주는 키는 `AQ.`로 시작하고 가운데 마침표가 있다.
  // 옛 `AIzaSy…` 모양만 생각하고 막았더니, 키가 설정 탭에 제대로 들어와 있는데도
  // "키를 찾지 못했습니다"만 뜨는 일이 있었다(2026-07-27).
  // 붙여넣은 문장을 걸러내는 목적은 그대로다 — 띄어쓰기와 한글은 여전히 거부한다.
  return /^[A-Za-z0-9._-]+$/.test(key);
}

/** 이 사본의 1행 편집을 받는 설치형 감지기만 골라낸다. */
function attendanceAiEditTriggersFor_(triggers, spreadsheetId) {
  const wanted = String(spreadsheetId || '').trim();
  if (!wanted) return [];
  return (triggers || []).filter(trigger => {
    try {
      return Boolean(trigger)
        && typeof trigger.getHandlerFunction === 'function'
        && trigger.getHandlerFunction() === ATTENDANCE_AI_EDIT_TRIGGER_HANDLER
        && typeof trigger.getEventType === 'function'
        && trigger.getEventType() === ScriptApp.EventType.ON_EDIT
        && typeof trigger.getTriggerSourceId === 'function'
        && String(trigger.getTriggerSourceId() || '').trim() === wanted;
    } catch (err) {
      return false;
    }
  });
}

/** 같은 감지기를 두 번 만들지 않는다. 이미 여러 개면 하나만 남긴다. */
function ensureAttendanceAiEditTrigger_(spreadsheetId, spreadsheet) {
  const ss = spreadsheet || SpreadsheetApp.getActiveSpreadsheet();
  const activeId = String(ss.getId() || '').trim();
  const wanted = String(spreadsheetId || '').trim();
  if (!wanted || !activeId || wanted !== activeId) {
    throw new Error('AI 입력 감지기를 만들 파일을 확인하지 못했습니다.');
  }
  const existing = attendanceAiEditTriggersFor_(ScriptApp.getProjectTriggers(), wanted);
  if (existing.length > 1) {
    // 감지기가 여러 개면 같은 문장으로 출결행이 여러 번 써진다. 첫 하나만 남긴다.
    for (let i = 1; i < existing.length; i++) ScriptApp.deleteTrigger(existing[i]);
    return { created: false, removed: existing.length - 1, count: 1 };
  }
  if (existing.length === 1) {
    return { created: false, removed: 0, count: 1 };
  }
  ScriptApp.newTrigger(ATTENDANCE_AI_EDIT_TRIGGER_HANDLER)
    .forSpreadsheet(ss)
    .onEdit()
    .create();
  const after = attendanceAiEditTriggersFor_(ScriptApp.getProjectTriggers(), wanted);
  if (after.length !== 1) {
    throw new Error('AI 입력 감지기가 정확히 하나인지 확인하지 못했습니다.');
  }
  return { created: true, removed: 0, count: 1 };
}

/** 실행 계정이 화면에서 넘긴 교사 계정과 정확히 같은지만 돌려준다. */
function attendanceAiExpectedAccountMatches_(expectedAccount) {
  const expectedValue = typeof expectedAccount === 'string' ? expectedAccount : '';
  if (!isExactGoeduEmail_(expectedValue)) return false;
  const expected = expectedValue.trim().toLowerCase();
  let actual = '';
  try { actual = String(Session.getActiveUser().getEmail() || '').trim().toLowerCase(); } catch (err) {}
  if (!actual) {
    try { actual = String(Session.getEffectiveUser().getEmail() || '').trim().toLowerCase(); } catch (err) {}
  }
  return actual === expected;
}

/** 실행 경계의 예상 밖 오류는 외부 정보 없이 같은 실패 상태로 돌려준다. */
function attendanceAiSetupSafeFailure_() {
  return {
    ok: false,
    account_matches: false,
    spreadsheet_matches: false,
    key_present: false,
    target_matches: false,
    trigger_count: 0,
    setup_done: false
  };
}

/** 밖으로는 계정·키 원문 없이 여섯 가지 확인 결과만 돌려준다. */
function attendanceAiSetupStatusFor_(spreadsheet, expectedAccount, boundSpreadsheet) {
  const spreadsheetId = String(spreadsheet && spreadsheet.getId() || '').trim();
  const boundId = String(boundSpreadsheet && boundSpreadsheet.getId() || '').trim();
  const accountMatches = attendanceAiExpectedAccountMatches_(expectedAccount);
  const spreadsheetMatches = !!spreadsheetId && spreadsheetId === boundId;
  const keyPresent = isAttendanceAiApiKeyShape_(attendanceAiGeminiApiKey_(spreadsheet));
  let targetMatches = false;
  try {
    targetMatches = !!spreadsheetId && String(PropertiesService.getScriptProperties()
      .getProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY) || '').trim() === spreadsheetId;
  } catch (err) {}
  const triggerCount = spreadsheetId
    ? attendanceAiEditTriggersFor_(ScriptApp.getProjectTriggers(), spreadsheetId).length
    : 0;
  let setupDone = false;
  try {
    const rawMarker = readConfigValueReadOnlyFor_(
      spreadsheet, ATTENDANCE_AI_VERIFICATION_SETTING
    );
    const marker = rawMarker ? JSON.parse(rawMarker) : null;
    setupDone = Boolean(marker)
      && marker.schema_version === ATTENDANCE_AI_VERIFICATION_SCHEMA_VERSION
      && marker.setup_version === ATTENDANCE_AI_SETUP_VERSION
      && String(marker.spreadsheet_id || '').trim() === spreadsheetId
      && marker.handler_name === ATTENDANCE_AI_EDIT_TRIGGER_HANDLER
      && marker.trigger_count === 1
      && marker.success === true;
  } catch (err) {
    setupDone = false;
  }
  const ok = accountMatches && spreadsheetMatches && keyPresent && targetMatches
    && triggerCount === 1 && setupDone;
  return {
    ok: ok,
    account_matches: accountMatches,
    spreadsheet_matches: spreadsheetMatches,
    key_present: keyPresent,
    target_matches: targetMatches,
    trigger_count: triggerCount,
    setup_done: setupDone
  };
}

/** 감지기 개수를 다시 읽어 증명한 뒤, 비밀값 없는 확인 표시를 마지막에 적는다. */
function writeAttendanceAiVerificationMarkerFor_(spreadsheet, spreadsheetId) {
  const sheet = spreadsheet && typeof spreadsheet.getSheetByName === 'function'
    ? spreadsheet.getSheetByName(CONFIG_SHEET_NAME)
    : null;
  if (!sheet) throw new Error('설정 탭을 찾지 못했습니다.');
  const actualSpreadsheetId = String(
    spreadsheet && typeof spreadsheet.getId === 'function' ? spreadsheet.getId() : ''
  ).trim();
  const checkedSpreadsheetId = String(spreadsheetId || '').trim();
  if (!checkedSpreadsheetId || actualSpreadsheetId !== checkedSpreadsheetId) {
    throw new Error('AI 입력 연결을 확인할 출석부 번호가 서로 다릅니다.');
  }
  const connectionCode = attendanceConnectionCodeForSpreadsheetId_(checkedSpreadsheetId);
  if (!connectionCode) throw new Error('출석부 연결 확인번호를 만들지 못했습니다.');
  const lastRow = Math.max(2, Number(sheet.getLastRow() || 0));
  const rows = sheet.getRange(2, 1, Math.max(1, lastRow - 1), 2).getValues();
  const markerIndexes = [];
  const connectionIndexes = [];
  rows.forEach(function (row, index) {
    if (String(row[0] || '').trim() === ATTENDANCE_AI_VERIFICATION_SETTING) {
      markerIndexes.push(index);
    }
    if (String(row[0] || '').trim() === ATTENDANCE_CONNECTION_CODE_SETTING) {
      connectionIndexes.push(index);
    }
  });
  function writeUniqueSetting_(key, value, indexes) {
    // 손상된 중복 표시는 먼저 비운다. 정확한 값은 아래 마지막 쓰기 한 번이다.
    for (let index = 1; index < indexes.length; index++) {
      sheet.getRange(indexes[index] + 2, 1).setValue('');
      sheet.getRange(indexes[index] + 2, 2).setValue('');
    }
    if (indexes.length) {
      sheet.getRange(indexes[0] + 2, 2).setValue(value);
    } else if (typeof sheet.appendRow === 'function') {
      sheet.appendRow([key, value]);
    } else {
      const appendAt = Math.max(2, Number(sheet.getLastRow() || 0) + 1);
      sheet.getRange(appendAt, 1).setValue(key);
      sheet.getRange(appendAt, 2).setValue(value);
    }
  }
  const marker = JSON.stringify({
    schema_version: ATTENDANCE_AI_VERIFICATION_SCHEMA_VERSION,
    setup_version: ATTENDANCE_AI_SETUP_VERSION,
    spreadsheet_id: checkedSpreadsheetId,
    handler_name: ATTENDANCE_AI_EDIT_TRIGGER_HANDLER,
    trigger_count: 1,
    success: true
  });
  writeUniqueSetting_(ATTENDANCE_CONNECTION_CODE_SETTING, connectionCode, connectionIndexes);
  // 자동화 확인 JSON은 기존 형식을 그대로 지켜 Windows 프로그램의 읽기 계약을 깨지 않는다.
  writeUniqueSetting_(ATTENDANCE_AI_VERIFICATION_SETTING, marker, markerIndexes);
}

/** 대상 이외의 같은 편집 감지기는 남기지 않는다. */
function removeUnexpectedAttendanceAiEditTriggers_(spreadsheetId) {
  const wanted = String(spreadsheetId || '').trim();
  (ScriptApp.getProjectTriggers() || []).forEach(trigger => {
    const sameHandler = trigger.getHandlerFunction() === ATTENDANCE_AI_EDIT_TRIGGER_HANDLER;
    const editTrigger = trigger.getEventType() === ScriptApp.EventType.ON_EDIT;
    const sourceId = String(trigger.getTriggerSourceId() || '').trim();
    if (sameHandler && editTrigger && sourceId !== wanted) ScriptApp.deleteTrigger(trigger);
  });
}

/** 열린 Sheet 메뉴가 쓰는 실제 연결 절차다. */
function setupAttendanceAiInputFor_(spreadsheet, expectedAccount, boundSpreadsheet) {
  try {
    const before = attendanceAiSetupStatusFor_(spreadsheet, expectedAccount, boundSpreadsheet);
    if (!before.account_matches || !before.spreadsheet_matches || !before.key_present) return before;
    const state = attendanceAiWorkbookStateFor_(spreadsheet);
    if (!state || state.ok !== true) return before;
    removeUnexpectedAttendanceAiEditTriggers_(state.spreadsheetId);
    ensureAttendanceAiEditTrigger_(state.spreadsheetId, spreadsheet);
    PropertiesService.getScriptProperties()
      .setProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY, state.spreadsheetId);
    const checked = attendanceAiSetupStatusFor_(spreadsheet, expectedAccount, boundSpreadsheet);
    if (!checked.account_matches || !checked.spreadsheet_matches || !checked.key_present
      || !checked.target_matches || checked.trigger_count !== 1) return checked;
    writeAttendanceAiVerificationMarkerFor_(spreadsheet, state.spreadsheetId);
    return {
      ok: true,
      account_matches: true,
      spreadsheet_matches: true,
      key_present: true,
      target_matches: true,
      trigger_count: 1,
      setup_done: true
    };
  } catch (err) {
    return attendanceAiSetupSafeFailure_();
  }
}

/** 메뉴: 이 사본에서만 1행 AI 입력을 켠다. */
function enableAttendanceAiInput(options) {
  const expectedAccount = requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  // 통합 설정이 부를 때는 단계마다 창을 띄우지 않고 결과만 돌려준다 — 화면은 마지막에 한 번만 뜬다.
  const quiet = !!(options && options.quiet === true);
  function finish_(ok, message, created) {
    if (!quiet) ui.alert(ATTENDANCE_AI_SETUP_TITLE, message, ui.ButtonSet.OK);
    return { ok: ok, created: created === true, message: message };
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const before = attendanceAiWorkbookStateFor_(spreadsheet);
  const triggerAlreadyThere = attendanceAiEditTriggersFor_(
    ScriptApp.getProjectTriggers(), String(spreadsheet.getId() || '').trim()
  ).length === 1;
  const setup = setupAttendanceAiInputFor_(spreadsheet, expectedAccount, spreadsheet);
  if (!setup.ok) {
    return finish_(false, String(before && before.message || 'AI 입력을 켜지 못했습니다.'));
  }

  return finish_(
    true,
    'AI 출결 입력을 켰어요.\n\n' +
    (!triggerAlreadyThere ? '맨 위 입력칸을 사용할 준비를 마쳤습니다.\n' : '이미 준비된 입력칸을 그대로 사용합니다.\n') +
    '\n월 탭 맨 위 입력칸에 "3월 12일 김철수 병결"처럼 적고 Enter를 누르면\n' +
    '맨 아래에 연한 초록색 출결행이 생깁니다.\n\n' +
    '이름을 못 찾거나 날짜·구분을 해석하지 못하면 아무 줄도 만들지 않습니다.',
    !triggerAlreadyThere
  );
}

/** 설치형 감지기가 부르는 함수 — 실패해도 시트에 아무 표시를 남기지 않는다. */
function onAttendanceAiEdit(e) {
  try {
    return handleAttendanceAiEdit(e);
  } catch (err) {
    console.log('onAttendanceAiEdit error:', err);
    return { status: 'check_required' };
  }
}

function ensureRosterSheet_(ss) {
  const cfg = getConfig_();
  const name = cfg.ROSTER_SHEET_NAME || '학생명단';
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  sh.setTabColor('#3B82F6');

  if (sh.getMaxColumns() < ROSTER_HEADERS.length) {
    sh.insertColumnsAfter(sh.getMaxColumns(), ROSTER_HEADERS.length - sh.getMaxColumns());
  }

  const lastRow = Math.max(sh.getLastRow(), 1);
  const headerA = String(sh.getRange(1, 1).getValue() || '').trim();
  const headerB = String(sh.getRange(1, 2).getValue() || '').trim();
  const headerC = String(sh.getRange(1, 3).getValue() || '').trim();

  const clearFormerFourthColumn = () => {
    if (sh.getMaxColumns() < 4) return;
    const former = sh.getRange(1, 4, lastRow, 1);
    former.clearContent();
    if (typeof former.clearFormat === 'function') former.clearFormat();
  };

  if (headerA === '번호+이름') {
    // 가장 오래된 배치(A=번호+이름, B=번호, C=이름, D=이메일)를
    // A=번호, B=이름, C=이메일 세 칸으로 옮긴다. 전체 시트는 비우지 않는다.
    const width = Math.min(4, sh.getMaxColumns());
    const values = lastRow > 1
      ? sh.getRange(2, 1, lastRow - 1, width).getValues()
      : [];
    const migrated = values.map(row => {
      const combined = String(row[0] || '').trim();
      const number = String(row[1] || '').trim();
      const studentName = String(row[2] || '').trim();
      const email = String(row[3] || '').trim();
      const parsed = parseStudentLabel_(combined);
      return [number || parsed.number, studentName || parsed.name, email];
    });
    sh.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues([ROSTER_HEADERS]);
    if (migrated.length) {
      sh.getRange(2, 1, migrated.length, ROSTER_HEADERS.length).setValues(migrated);
    }
    clearFormerFourthColumn();
  } else if (headerA === '번호' && headerB === '이름' && headerC === '번호+이름') {
    // 바로 전 배치(A=번호, B=이름, C=번호+이름, D=이메일)는
    // D의 이메일을 먼저 읽어 C로 옮긴 뒤 옛 D열을 비운다.
    const emails = lastRow > 1
      ? sh.getRange(2, 4, lastRow - 1, 1).getValues()
      : [];
    if (emails.length) sh.getRange(2, 3, emails.length, 1).setValues(emails);
    sh.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues([ROSTER_HEADERS]);
    clearFormerFourthColumn();
  } else {
    sh.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues([ROSTER_HEADERS]);
  }

  if (String(cfg.STUDENT_DROPDOWN_RANGE || '').trim() !== STUDENT_DROPDOWN_RANGE) {
    setConfigValue_('STUDENT_DROPDOWN_RANGE', STUDENT_DROPDOWN_RANGE);
  }

  sh.getRange(1, 1, 1, ROSTER_HEADERS.length)
    .setBackground('#1F4E79')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 2, 90);
  sh.setColumnWidths(3, 1, 240);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

function parseStudentLabel_(value) {
  const text = String(value || '').trim();
  const match = text.match(/^(\d+)\s*(.+)$/);
  if (!match) return { number: '', name: text };
  return { number: match[1], name: String(match[2] || '').trim() };
}

function combineStudentNumberAndName_(number, name) {
  const cleanNumber = String(number === null || number === undefined ? '' : number).trim();
  const cleanName = String(name === null || name === undefined ? '' : name).trim();
  return cleanNumber && cleanName ? cleanNumber + cleanName : '';
}

function buildStudentDropdownValues_(rows) {
  return (rows || []).map(row => (
    Array.isArray(row) ? combineStudentNumberAndName_(row[0], row[1]) : ''
  )).filter(Boolean);
}

function syncStudentDropdownValues_(ss, cfg) {
  const rosterName = (cfg && cfg.ROSTER_SHEET_NAME) || '학생명단';
  const roster = ss.getSheetByName(rosterName);
  const dropdown = ss.getSheetByName(STUDENT_DROPDOWN_SHEET_NAME);
  if (!roster || !dropdown) return;

  if (dropdown.getMaxColumns() < STUDENT_DROPDOWN_COLUMN) {
    dropdown.insertColumnsAfter(
      dropdown.getMaxColumns(),
      STUDENT_DROPDOWN_COLUMN - dropdown.getMaxColumns()
    );
  }

  const rows = roster.getLastRow() > 1
    ? roster.getRange(2, 1, roster.getLastRow() - 1, 2).getValues()
    : [];
  const labels = buildStudentDropdownValues_(rows);
  const output = Array.from(
    { length: STUDENT_DROPDOWN_LAST_ROW - STUDENT_DROPDOWN_FIRST_ROW + 1 },
    (_, index) => [labels[index] || '']
  );
  dropdown.getRange(1, STUDENT_DROPDOWN_COLUMN).setValue(STUDENT_DROPDOWN_HEADER);
  dropdown.getRange(
    STUDENT_DROPDOWN_FIRST_ROW,
    STUDENT_DROPDOWN_COLUMN,
    output.length,
    1
  ).setValues(output);
  dropdown.hideColumns(STUDENT_DROPDOWN_COLUMN, 1);
}

function loadStudentRosterForDm_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const cfg = getConfig_();
  const roster = ss.getSheetByName(cfg.ROSTER_SHEET_NAME || '학생명단');
  if (!roster) throw new Error('학생명단 시트를 찾을 수 없습니다.');
  ensureRosterSheet_(ss);
  const lastRow = roster.getLastRow();
  if (lastRow < 2) return {};
  const values = roster.getRange(2, 1, lastRow - 1, ROSTER_HEADERS.length).getValues();
  return buildRosterKeyMap_(values);
}

// 학생명단 행 배열로 "키 → 학생" 맵을 만든다. GAS API를 쓰지 않는 순수 함수라
// 테스트가 Node로 직접 실행한다. rows: [번호, 이름, 학생 Google 이메일]
function buildRosterKeyMap_(rows) {
  const map = {};
  const ambiguous = new Set();
  function register(key, student) {
    const clean = String(key || '').replace(/\s+/g, '');
    if (!clean) return;
    if (map[clean] && map[clean].rowNumber !== student.rowNumber) {
      ambiguous.add(clean);
      return;
    }
    map[clean] = student;
  }
  (rows || []).forEach((row, index) => {
    const number = String(row[0] || '').trim();
    const name = String(row[1] || '').trim();
    const combined = combineStudentNumberAndName_(number, name);
    // 학생 Google 이메일은 그대로 읽고, 실제 발송 직전에 주소 한 개의 형식을 확인한다.
    const email = String(row[2] || '').trim();
    const student = {
      rowNumber: index + 2,
      combined: combined || (number + name),
      number: number,
      name: name,
      email: email
    };
    if (!student.combined) return;
    register(combined, student);
    register(number + name, student);
    // 번호만·이름만으로도 쓸 수 있게 한다. 두 학생에게 겹치는 값은 아래에서 뺀다.
    register(number, student);
    register(name, student);
  });
  ambiguous.forEach(key => { delete map[key]; });
  return map;
}

function applyStudentDropdowns_(ss, cfg) {
  const dropdown = ss.getSheetByName(STUDENT_DROPDOWN_SHEET_NAME);
  if (!dropdown) return;
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInRange(dropdown.getRange(STUDENT_DROPDOWN_RANGE), true)
    .setAllowInvalid(true)
    .build();

  getInputSheets_(ss, cfg).forEach(sh => {
    const n = sh.getMaxRows() - MONTHLY_ATTENDANCE_HEADER_ROW;
    if (n > 0) {
      sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 2, n, 1)
        .setDataValidation(rule);
    }
  });
}

function ensureHolidaySheet_(ss) {
  const cfg = getConfig_();
  const name = cfg.HOLIDAY_SHEET_NAME || '휴일';
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  sh.getRange(1, 1, 1, 6).setValues([['날짜','명칭','구분','상태','비고','출처']]);

  const hasData = sh.getLastRow() >= 2 && sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues().some(r => r[0]);
  if (!hasData) {
    const rows = getDefaultHolidayRows_();
    sh.getRange(2, 1, rows.length, 6).setValues(rows);
  }

  sh.getRange(1, 1, 1, 6).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 1, 110);
  sh.setColumnWidths(2, 1, 190);
  sh.setColumnWidths(3, 2, 120);
  sh.setColumnWidths(5, 1, 360);
  sh.setColumnWidths(6, 1, 520);
  sh.getRange(2, 1, Math.max(1, sh.getMaxRows() - 1), 1).setNumberFormat('yyyy-mm-dd');
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

function getDefaultHolidayRows_() {
  return [
    [new Date(2026, 0, 1), "신정", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 1, 16), "설날 연휴", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 1, 17), "설날", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 1, 18), "설날 연휴", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 2, 1), "삼일절", "공휴일", "공식", "일요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 2, 2), "대체공휴일(삼일절)", "대체공휴일", "공식", "삼일절 대체공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 4, 1), "노동절", "공휴일", "공식", "2026년 5월 1일부터 관공서 공휴일 반영", "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285779&viewCls=lsRvsDocInfoR"],
    [new Date(2026, 4, 5), "어린이날", "공휴일", "공식", "법정 공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 4, 24), "부처님오신날", "공휴일", "공식", "일요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 4, 25), "대체공휴일(부처님오신날)", "대체공휴일", "공식", "부처님오신날 대체공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 5, 3), "제9회 전국동시지방선거", "선거일", "공식", "중앙선거관리위원회 선거일정", "https://img.nec.go.kr/common/board/Download.do?bcIdx=294445&cbIdx=1084&streFileNm=b7498932-8ad9-487c-ac6d-37b420175dc0.pdf"],
    [new Date(2026, 5, 6), "현충일", "공휴일", "공식", "토요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 6, 17), "제헌절", "공휴일", "공식", "2026년부터 관공서 공휴일 반영", "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285779&viewCls=lsRvsDocInfoR"],
    [new Date(2026, 7, 15), "광복절", "공휴일", "공식", "토요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 7, 17), "대체공휴일(광복절)", "대체공휴일", "공식", "광복절 대체공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 8, 24), "추석 연휴", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 8, 25), "추석", "공휴일", "공식", "우주항공청 2026 월력요항 기준", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 8, 26), "추석 연휴", "공휴일", "공식", "토요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 9, 3), "개천절", "공휴일", "공식", "토요일과 겹침", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 9, 5), "대체공휴일(개천절)", "대체공휴일", "공식", "개천절 대체공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 9, 9), "한글날", "공휴일", "공식", "법정 공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2026, 11, 25), "성탄절", "공휴일", "공식", "법정 공휴일", "https://www.kasi.re.kr/kor/publication/post/newsMaterial/32031"],
    [new Date(2027, 0, 1), "신정", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 1, 6), "설날 연휴", "공휴일", "공식", "토요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 1, 7), "설날", "공휴일", "공식", "일요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 1, 8), "설날 연휴", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 1, 9), "대체공휴일(설날)", "대체공휴일", "공식", "설날 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 2, 1), "삼일절", "공휴일", "공식", "법정 공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 4, 1), "노동절", "공휴일", "공식", "토요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 4, 3), "대체공휴일(노동절)", "대체공휴일", "공식", "노동절 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 4, 5), "어린이날", "공휴일", "공식", "법정 공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 4, 13), "부처님오신날", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 5, 6), "현충일", "공휴일", "공식", "일요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 6, 17), "제헌절", "공휴일", "공식", "토요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 6, 19), "대체공휴일(제헌절)", "대체공휴일", "공식", "제헌절 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 7, 15), "광복절", "공휴일", "공식", "일요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 7, 16), "대체공휴일(광복절)", "대체공휴일", "공식", "광복절 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 8, 14), "추석 연휴", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 8, 15), "추석", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 8, 16), "추석 연휴", "공휴일", "공식", "우주항공청 2027 월력요항 기준", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 9, 3), "개천절", "공휴일", "공식", "일요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 9, 4), "대체공휴일(개천절)", "대체공휴일", "공식", "개천절 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 9, 9), "한글날", "공휴일", "공식", "토요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 9, 11), "대체공휴일(한글날)", "대체공휴일", "공식", "한글날 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 11, 25), "성탄절", "공휴일", "공식", "토요일과 겹침", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2027, 11, 27), "대체공휴일(성탄절)", "대체공휴일", "공식", "성탄절 대체공휴일", "https://www.kasa.go.kr/prog/plcyBrf/brief/kor/sub01_01_04/view.do?plcyBrfNo=431"],
    [new Date(2028, 0, 1), "신정", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 0, 26), "설날 연휴", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 0, 27), "설날", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 0, 28), "설날 연휴", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 2, 1), "삼일절", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 3, 12), "제23대 국회의원 선거", "선거일", "예상/법령계산", "임기만료 국회의원 선거일 예정", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 4, 1), "노동절", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285779&viewCls=lsRvsDocInfoR"],
    [new Date(2028, 4, 2), "부처님오신날", "공휴일", "예상/법령계산", "음력 4월 8일 = 양력 2028-05-02", "https://datedb.net/tool/conversion/lunar_to_solar/20280408/"],
    [new Date(2028, 4, 5), "어린이날", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 5, 6), "현충일", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 6, 17), "제헌절", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285779&viewCls=lsRvsDocInfoR"],
    [new Date(2028, 7, 15), "광복절", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 9, 2), "추석 연휴", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 9, 3), "추석·개천절", "공휴일", "예상/법령계산", "추석과 개천절이 같은 날", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 9, 4), "추석 연휴", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 달력·법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 9, 5), "대체공휴일(개천절)", "대체공휴일", "예상/법령계산", "추석·개천절 중복에 따른 대체공휴일", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 9, 9), "한글날", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"],
    [new Date(2028, 11, 25), "성탄절", "공휴일", "예상/법령계산", "2028 공식 월력요항 발표 전: 법령 기준 계산", "https://time.is/ko/calendar/2028/South%20Korea"]
  ];
}

function ensureDropdownSheet_(ss) {
  let sh = ss.getSheetByName(STUDENT_DROPDOWN_SHEET_NAME);
  if (!sh) sh = ss.insertSheet(STUDENT_DROPDOWN_SHEET_NAME);
  const columns = [
    ['구분','질병','미인정','기타','출석인정'],
    ['종류','결석함','지각함','조퇴함','결과함'],
    ['교시','1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례'],
    ['제출상태','제출','미제출','해당없음'],
    ['휴일구분','공휴일','대체공휴일','선거일','재량휴업일','개교기념일','기타'],
    ['쪽지_들어온곳'].concat(MESSAGE_QUEUE_SOURCES),
    ['쪽지_상태'].concat(MESSAGE_QUEUE_STATUSES),
    ['개인쪽지_종류'].concat(PERSONAL_MESSAGE_TYPES),
    ['단체쪽지_종류'].concat(CLASS_MESSAGE_TYPES)
  ];
  const height = Math.max.apply(null, columns.map(col => col.length));
  const values = [];
  for (let r = 0; r < height; r++) {
    values.push(columns.map(col => col[r] || ''));
  }
  if (sh.getMaxColumns() < columns.length) sh.insertColumnsAfter(sh.getMaxColumns(), columns.length - sh.getMaxColumns());
  sh.getRange(1, 1, height, columns.length).setValues(values);
  sh.getRange(1,1,1,columns.length).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1,columns.length,120);
  syncStudentDropdownValues_(ss, getConfig_());
}

function ensurePersonalMessageQueueSheet_(ss) {
  const sh = ensureMessageQueueSheet_(ss, MESSENGER_PERSONAL_SHEET_NAME, LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES, PERSONAL_MESSAGE_QUEUE_HEADERS);
  applyMessageQueueDropdown_(sh, 4, PERSONAL_MESSAGE_TYPES);
  applyMessageQueueDropdown_(sh, 6, MESSAGE_QUEUE_SOURCES);
  applyMessageQueueDropdown_(sh, 7, MESSAGE_QUEUE_STATUSES);
  sh.setColumnWidths(1, 3, 95);
  sh.setColumnWidths(4, 1, 110);
  sh.setColumnWidths(5, 1, 420);
  sh.setColumnWidths(6, 2, 110);
  sh.setColumnWidths(8, 1, 220);
  sh.setColumnWidths(9, 2, 150);
  return sh;
}

function migrateClassQueueNumberColumn_(sh) {
  // 5.8.1 이전 시트는 B열이 발송에 쓰이지 않는 순번('번호')이었다. 첫 사용 때 한 번만 지운다.
  if (!sh || sh.getMaxColumns() < 3) return false;
  const header = sh.getRange(1, 1, 1, 3).getValues()[0].map(value => String(value || '').trim());
  if (header[1] !== '번호' || header[2] !== '안내 종류') return false;
  sh.deleteColumn(2);
  return true;
}

function ensureClassMessageQueueSheet_(ss) {
  migrateClassQueueNumberColumn_(getOrRenameSheet_(ss, MESSENGER_CLASS_SHEET_NAME, LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES));
  const sh = ensureMessageQueueSheet_(ss, MESSENGER_CLASS_SHEET_NAME, LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES, CLASS_MESSAGE_QUEUE_HEADERS);
  applyMessageQueueDropdown_(sh, 2, CLASS_MESSAGE_TYPES);
  applyMessageQueueDropdown_(sh, 4, MESSAGE_QUEUE_SOURCES);
  applyMessageQueueDropdown_(sh, 5, MESSAGE_QUEUE_STATUSES);
  sh.setColumnWidths(1, 1, 95);
  sh.setColumnWidths(2, 1, 110);
  sh.setColumnWidths(3, 1, 520);
  sh.setColumnWidths(4, 2, 110);
  sh.setColumnWidths(6, 2, 150);
  return sh;
}

function getOrRenameSheet_(ss, preferredName, legacyNames) {
  let sh = ss.getSheetByName(preferredName);
  if (sh) return sh;
  const names = Array.isArray(legacyNames) ? legacyNames : (legacyNames ? [legacyNames] : []);
  for (let i = 0; i < names.length; i++) {
    const legacy = ss.getSheetByName(names[i]);
    if (legacy) {
      legacy.setName(preferredName);
      return legacy;
    }
  }
  return ss.insertSheet(preferredName);
}

function ensureMessageQueueSheet_(ss, preferredName, legacyNames, headers) {
  const sh = getOrRenameSheet_(ss, preferredName, legacyNames);
  if (sh.getMaxColumns() < headers.length) sh.insertColumnsAfter(sh.getMaxColumns(), headers.length - sh.getMaxColumns());
  if (sh.getMaxRows() < 300) sh.insertRowsAfter(sh.getMaxRows(), 300 - sh.getMaxRows());
  sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  sh.getRange(1, 1, 1, headers.length)
    .setBackground('#1F4E79')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle');
  sh.setFrozenRows(1);
  sh.getRange(1, 1, sh.getMaxRows(), headers.length).setWrap(true).setVerticalAlignment('middle');
  // 옛 판의 '확인필요'는 더 이상 선택값이 아니다. 발송은 여전히 선생님이 버튼을
  // 눌러 확인해야 시작되므로, 기존 줄은 안전하게 '대기'로 한 번 바꾼다.
  const statusColumn = headers.indexOf('상태') + 1;
  const lastRow = sh.getLastRow();
  if (statusColumn > 0 && lastRow >= 2) {
    const statusRange = sh.getRange(2, statusColumn, lastRow - 1, 1);
    const statuses = statusRange.getValues();
    let changed = false;
    statuses.forEach(row => {
      if (String(row[0] || '').trim() !== '확인필요') return;
      row[0] = '대기';
      changed = true;
    });
    if (changed) statusRange.setValues(statuses);
  }
  return sh;
}

function applyMessageQueueDropdown_(sh, column, values) {
  const rows = sh.getMaxRows() - 1;
  if (rows <= 0) return;
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(values, true)
    .setAllowInvalid(false)
    .build();
  sh.getRange(2, column, rows, 1).setDataValidation(rule);
}

function ensureTemplateMapSheet_(ss) {
  let sh = ss.getSheetByName('템플릿_치환표');
  if (!sh) sh = ss.insertSheet('템플릿_치환표');
  if (String(sh.getRange(1,1).getValue() || '').trim()) return;
  const rows = [
    ['자동으로 채워지는 자리','값 출처','적용 위치/규칙','비고'],
    ['{학교명} 또는 {{학교명}}','Teacher Manager에 저장한 학교명','하단 학교명장 귀하','신고서 양식에 있으면 자동으로 채움'],
    ['{반번호}','Teacher Manager에 저장한 학급과 B열 학생 번호','반번호 자리','예: 2-2 3번'],
    ['{번호}','B열 번호+이름에서 번호 분리','상단 번호','예: 3김가온 → 3'],
    ['{성명}','B열 번호+이름에서 이름 분리','성명/학생 서명','예: 3김가온 → 김가온'],
    ['{사유}','E열 사유','사유/확인내용',''],
    ['{시작교시}, {종료교시}','F열 교시','지각/조퇴/결과 행','결과는 시작 교시와 종료 교시를 같게 채움'],
    ['{확인월}, {확인일}','종료 다음 수업일','신고 날짜와 하단 확인 날짜','주말·휴일 제외']
  ];
  sh.getRange(1,1,rows.length,4).setValues(rows);
  sh.getRange(1,1,1,4).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1,1,180);
  sh.setColumnWidths(2,1,220);
  sh.setColumnWidths(3,1,420);
  sh.setColumnWidths(4,1,300);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

function ensureChatLogSheet_(ss) {
  const cfg = getConfig_();
  const name = cfg.CHAT_LOG_SHEET_NAME || '발송기록';
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (!String(sh.getRange(1, 1).getValue() || '').trim()) {
    sh.getRange(1, 1, 1, 7).setValues([['발송시각','종류','대상','Chat방','내용 미리보기','결과','오류']]);
  }
  sh.getRange(1, 1, 1, 7).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 1, 160);
  sh.setColumnWidths(2, 2, 120);
  sh.setColumnWidths(4, 1, 240);
  sh.setColumnWidths(5, 1, 360);
  sh.setColumnWidths(6, 2, 160);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
  return sh;
}

function openChatLogSheet() {
  requireGoeduTeacherAccount_();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureChatLogSheet_(ss);
  ss.setActiveSheet(sh);
}

function ensureUsageSheet_(ss) {
  let sh = ss.getSheetByName('00_사용법');
  if (!sh) sh = ss.insertSheet('00_사용법');
  const title = String(sh.getRange(1,1).getValue() || '').trim();
  const currentTitle = 'Teacher Manager 출결 사용 안내';
  // Replace only the shipped guide; leave a teacher's own guide untouched.
  if (title && title !== currentTitle && title !== "출결 신고서 자동화 사용 순서 — 기존 Google Docs 템플릿 그대로 사용") return;
  const heading = sh.getRange(1,1,1,3);
  if (heading.getValues()[0].slice(1).some(value => value !== '' && value !== null)
      || heading.getFormulas()[0].slice(1).some(Boolean) || heading.getNotes()[0].slice(1).some(Boolean)) return;
  const merges = heading.getMergedRanges();
  if (merges.some(range => range.getRow() !== 1 || range.getColumn() !== 1 || range.getNumRows() !== 1
      || ![3, 6].includes(range.getNumColumns()))) return;
  if (!merges.some(range => range.getNumColumns() === 3)) {
    merges.forEach(range => range.breakApart());
    heading.merge();
  }
  if (title !== currentTitle) heading.setValue(currentTitle);
  heading.setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setFontSize(14)
    .setHorizontalAlignment('center').setVerticalAlignment('middle').setWrap(true);
  sh.setRowHeight(1, 36);
  // The desktop preparer already writes this title. Repair its heading only;
  // retain all existing instructions and any notes the teacher added below it.
  if (title === currentTitle) return;
  const rows = [
  [
    "순서",
    "작업",
    "설명",
    "",
    "",
    ""
  ],
  [
    "1",
    "처음 설정",
    "Teacher Manager에서 출결 준비를 마친 뒤, 출석부 메뉴 [처음 한 번 설정하기 → 처음 설정 한 번에 끝내기]를 실행합니다.",
    "",
    "",
    ""
  ],
  [
    "2",
    "학생명단",
    "학생명단에 번호, 이름, 학생 Google 이메일을 각각 입력합니다.",
    "",
    "",
    ""
  ],
  [
    "3",
    "월별 출결 입력",
    "각 월의 맨 위 AI 출결 입력칸에 문장을 적거나, 3행부터 날짜·학생·구분·종류·사유를 입력합니다.",
    "",
    "",
    ""
  ],
  [
    "4",
    "학생 선택",
    "월별 출결표의 학생 선택목록은 학생명단의 번호와 이름으로 만들어집니다.",
    "",
    "",
    ""
  ],
  [
    "5",
    "신고서",
    "출결표에서 행을 선택한 뒤 [출결 업무 자동화 → 선택 행 출결신고서 Google Docs에 만들기]를 누릅니다.",
    "",
    "",
    ""
  ],
  [
    "6",
    "Google Chat",
    "발송상태·발송시각·결과를 월별 출결표에서 확인합니다.",
    "",
    "",
    ""
  ],
  [
    "7",
    "안내와 할 일",
    "메신저 개인톡 내용과 메신저 단체톡 내용에서 안내를 확인하고 출결 미제출 할 일을 관리합니다.",
    "",
    "",
    ""
  ],
  [
    "8",
    "휴일",
    "휴일 탭에서 학교의 수업일과 휴무일을 확인합니다.",
    "",
    "",
    ""
  ]
];
  while (rows.length < 13) rows.push(new Array(6).fill(''));
  sh.getRange(2,1,rows.length,6).setValues(rows);
  sh.getRange(2,1,1,6).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.setFrozenRows(2);
  sh.setColumnWidths(1,1,60);
  sh.setColumnWidths(2,1,200);
  sh.setColumnWidths(3,4,260);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

function moveSheetsInOrder_(ss, names) {
  let pos = 1;
  names.forEach(name => {
    const sh = ss.getSheetByName(name);
    if (!sh) return;
    ss.setActiveSheet(sh);
    ss.moveActiveSheet(pos);
    pos++;
  });
}

// 코드로 옮겼거나 더 이상 쓰지 않는 설정 키를 시트에서 지운다.
// 옛 설치본에서 넘어온 중복 행도 이 과정에서 함께 정리된다.
function removeStaleConfigRows_(sh) {
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return;
  const stale = new Set([
    "AUTHOR_NAME", "APP_REPO_URL", "DEFAULT_REPORT_STATUS", "DEFAULT_ATTACHMENT_STATUS",
    // Docs 안내장 경로 제거로 더 이상 쓰지 않는 키. 옛 시트에서 자동 정리한다.
    "DAILY_NOTICE_FOLDER_ID", "DAILY_NOTICE_FOLDER_NAME", "DAILY_NOTICE_DOC_DATE", "DAILY_NOTICE_DOC_ID"
  ]);
  const keys = sh.getRange(2, 1, lastRow - 1, 1).getValues();
  for (let i = keys.length - 1; i >= 0; i--) {
    if (stale.has(String(keys[i][0] || '').trim())) sh.deleteRow(i + 2);
  }
}

function readConfigMapFromSheet_(sh) {
  const map = {};
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return map;
  const values = sh.getRange(2, 1, lastRow - 1, 2).getValues();
  values.forEach(row => {
    const key = String(row[0] || '').trim();
    if (key) map[key] = row[1];
  });
  return map;
}

function getConfig_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(CONFIG_SHEET_NAME);
  if (!sh) {
    ensureConfigSheet_(ss);
    sh = ss.getSheetByName(CONFIG_SHEET_NAME);
  }
  return Object.assign({}, DEFAULT_CONFIG, readConfigMapFromSheet_(sh));
}

// 시트가 자기 스크립트 ID를 설정 시트에 스스로 기록한다.
// 설치 기록 파일이 없는 컴퓨터나 사본 시트에서도 시트만 읽으면 스크립트를 찾을 수 있게 하기 위함이다.
function recordScriptIdInConfig_() {
  try {
    const id = ScriptApp.getScriptId();
    if (!id) return;
    const cfg = getConfig_();
    if (String(cfg.SCRIPT_ID || '').trim() === id) return;
    setConfigValue_('SCRIPT_ID', id);
  } catch (err) {
    // 권한 문제로 ID를 읽지 못하면 설정 시트를 건드리지 않는다. 설치 도우미가 대신 기록한다.
  }
}

function ensureCentralChatConfig_() {
  const cfg = getConfig_();
  let changed = false;
  const activeSpreadsheetId = SpreadsheetApp.getActiveSpreadsheet().getId();
  let sheetId = String(cfg.CENTRAL_CHAT_SHEET_ID || '').trim();
  let sheetSecret = String(cfg.CENTRAL_CHAT_SHEET_SECRET || '').trim();
  if (!sheetId || !sheetId.startsWith(activeSpreadsheetId + ':')) {
    sheetId = activeSpreadsheetId + ':' + Utilities.getUuid();
    setConfigValue_('CENTRAL_CHAT_SHEET_ID', sheetId);
    sheetSecret = Utilities.getUuid() + Utilities.getUuid();
    setConfigValue_('CENTRAL_CHAT_SHEET_SECRET', sheetSecret);
    setConfigValue_('CLASS_CHAT_SPACE_ID', '');
    setConfigValue_('CLASS_CHAT_SPACE_NAME', '');
    changed = true;
  } else if (!sheetSecret) {
    sheetSecret = Utilities.getUuid() + Utilities.getUuid();
    setConfigValue_('CENTRAL_CHAT_SHEET_SECRET', sheetSecret);
    changed = true;
  }
  return {
    url: String(cfg.CENTRAL_CHAT_SENDER_URL || '').trim(),
    sheetId: sheetId,
    sheetSecret: sheetSecret,
    changed: changed
  };
}

function setConfigValue_(key, value) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ensureConfigSheet_(ss);
  const sh = ss.getSheetByName(CONFIG_SHEET_NAME);
  const lastRow = sh.getLastRow();
  const vals = sh.getRange(2, 1, Math.max(1, lastRow - 1), 1).getValues();
  for (let i = 0; i < vals.length; i++) {
    if (String(vals[i][0]).trim() === key) {
      sh.getRange(i + 2, 2).setValue(value);
      return;
    }
  }
  sh.getRange(lastRow + 1, 1, 1, 2).setValues([[key, value]]);
}

function getMonthSheetNames_(cfg) {
  return String((cfg && cfg.MONTH_SHEET_NAMES) || DEFAULT_CONFIG.MONTH_SHEET_NAMES)
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);
}

function getInputSheets_(ss, cfg) {
  return attendanceMonthSheetsFor_(ss, cfg || readAttendanceConfigStrict_(ss)).map(item => item.sheet);
}

function isInputMonthSheet_(sheet) {
  return isConfiguredMonthlyAttendanceSheetReadOnly_(sheet);
}

function isConfiguredMonthlyAttendanceSheetReadOnly_(sheet) {
  try {
    if (!sheet || typeof sheet.getParent !== 'function') return false;
    const spreadsheet = sheet.getParent();
    if (!spreadsheet || typeof spreadsheet.getSheetByName !== 'function') return false;
    const configSheet = spreadsheet.getSheetByName(CONFIG_SHEET_NAME);
    if (!configSheet) return false;
    const config = readAttendanceConfigStrict_(spreadsheet);
    return attendanceMonthSheetsFor_(spreadsheet, config).some(item => item.sheet.getSheetId() === sheet.getSheetId());
  } catch (err) {
    return false;
  }
}

function validateMonthlyAttendanceSortRange_(sheet, range) {
  if (!isConfiguredMonthlyAttendanceSheetReadOnly_(sheet)) return false;
  if (sheet.getFrozenRows() !== 2 || sheet.getFilter()) return false;

  const firstRow = sheet.getRange(1, 1, 1, 12).getValues()[0]
    .map(value => String(value === null || value === undefined ? '' : value).trim());
  const headerRow = sheet.getRange(2, 1, 1, 12).getValues()[0]
    .map(value => String(value === null || value === undefined ? '' : value));
  const inputHeadersMatch = INPUT_HEADERS.every((header, index) => headerRow[index] === header);
  const oldHeaderStillOnFirstRow = INPUT_HEADERS.every((header, index) => firstRow[index] === header);
  const chatHeaders = headerRow.slice(8, 12);
  const chatHeadersMatch = MONTHLY_CHAT_RESULT_HEADERS.every(
    (header, index) => chatHeaders[index] === header
  );
  const chatHeadersAreBlank = chatHeaders.every(value => value === '');
  if (!inputHeadersMatch || oldHeaderStillOnFirstRow || (!chatHeadersMatch && !chatHeadersAreBlank)) {
    return false;
  }

  const lastRow = sheet.getLastRow();
  const lastColumn = Math.max(12, sheet.getLastColumn());
  if (lastRow <= 2) return range === null || range === undefined;
  if (!range) return false;

  return range.getRow() === 3
    && range.getColumn() === 1
    && range.getNumRows() === lastRow - 2
    && range.getNumColumns() === lastColumn
    && range.getLastRow() === lastRow
    && range.getLastColumn() === lastColumn;
}

function sortMonthlyAttendanceRows_(sheet, mode) {
  if (mode !== 'date' && mode !== 'student') return false;
  if (!sheet) return false;
  const lastRow = sheet.getLastRow();
  if (lastRow <= 2) return validateMonthlyAttendanceSortRange_(sheet, null);

  const lastColumn = Math.max(12, sheet.getLastColumn());
  const range = sheet.getRange(3, 1, lastRow - 2, lastColumn);
  if (!validateMonthlyAttendanceSortRange_(sheet, range)) return false;

  // 학생 칸은 `1김민수`, `10박서준`, `2이영희`처럼 번호와 이름이 붙은 글자다.
  // 그대로 정렬하면 1, 10, 2가 되므로, 정렬하는 동안만 N열에 실제 번호를 넣는다.
  // N열 이후에 선생님 자료가 하나라도 있으면 덮거나 지우지 않고 정렬을 멈춘다.
  const tableLastColumn = MONTHLY_ATTENDANCE_LAST_DATA_COL;
  const helperColumn = tableLastColumn + 1;
  const maxColumns = sheet.getMaxColumns();
  if (maxColumns < tableLastColumn) return false;
  if (maxColumns > tableLastColumn) {
    const tail = sheet.getRange(
      1,
      helperColumn,
      sheet.getMaxRows(),
      maxColumns - tableLastColumn
    );
    const grids = [tail.getValues(), tail.getFormulas(), tail.getNotes()];
    const hasUserTail = grids.some(grid => (
      grid.some(row => row.some(value => String(value || '').trim()))
    ));
    if (hasUserTail) return false;
  }

  let helperRange = null;
  let sorted = false;
  let cleanupComplete = true;
  try {
    if (maxColumns === tableLastColumn) {
      sheet.insertColumnAfter(tableLastColumn);
    }
    sheet.hideColumns(helperColumn, sheet.getMaxColumns() - tableLastColumn);
    const studentValues = sheet
      .getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 2, lastRow - 2, 1)
      .getValues();
    const largestSortNumber = Number.MAX_SAFE_INTEGER;
    // 날짜 정렬은 A열 값 대신 `날짜(yyyymmdd) × 10000 + 번호` 열쇠로 한다. A열을 그대로
    // 정렬하면 글자로 적힌 날짜가 모든 날짜 뒤로 가고, 빈 날짜가 앞에 올 수 있다.
    const dayKeys = mode === 'date'
      ? monthlyAttendanceDayKeys_(sheet.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 1, lastRow - 2, 1).getValues())
      : null;
    const numberValues = studentValues.map((row, index) => {
      const parsed = parseStudentLabel_(row[0]);
      const number = Number(parsed.number);
      if (dayKeys) {
        return [dayKeys[index] * 10000 + (Number.isSafeInteger(number) && number >= 0 && number < 9999 ? number : 9999)];
      }
      return [
        Number.isSafeInteger(number) && number >= 0
          ? number
          : largestSortNumber
      ];
    });
    helperRange = sheet.getRange(
      MONTHLY_ATTENDANCE_DATA_START_ROW,
      helperColumn,
      lastRow - 2,
      1
    );
    helperRange.setValues(numberValues);
    const sortSpec = mode === 'date'
      ? [
          { column: helperColumn, ascending: true },
          { column: 2, ascending: true }
        ]
      : [
          { column: helperColumn, ascending: true },
          { column: 1, ascending: true },
          { column: 2, ascending: true }
        ];
    sheet.getRange(
      MONTHLY_ATTENDANCE_DATA_START_ROW,
      1,
      lastRow - 2,
      helperColumn
    ).sort(sortSpec);
    sorted = true;
  } catch (err) {
    sorted = false;
  } finally {
    if (helperRange) {
      try {
        helperRange.clearContent();
      } catch (err) {
        cleanupComplete = false;
      }
    }
    try {
      const removableColumns = sheet.getMaxColumns() - tableLastColumn;
      if (removableColumns > 0) {
        sheet.deleteColumns(helperColumn, removableColumns);
      }
    } catch (err) {
      cleanupComplete = false;
    }
  }
  if (!sorted || !cleanupComplete) return false;
  // 줄무늬는 조건부 서식이라 정렬 결과로 다시 계산된다. 규칙이 없거나 옛 모양이면 여기서 맞춘다.
  reStripeSheet_(sheet);
  return true;
}

/**
 * A열 값마다 정렬용 날짜 열쇠(yyyymmdd)를 만든다. 날짜 값과 글자로 적힌 날짜를 같은
 * 기준으로 비교한다. 연도가 없는 글자(`9/21`, `9월 21일`)는 이 탭의 날짜 값에서 가장
 * 많이 쓴 연도를 쓴다. 날짜로 읽을 수 없는 글자는 날짜 뒤, 빈 날짜는 맨 뒤에 둔다.
 */
function monthlyAttendanceDayKeys_(columnValues) {
  const years = {};
  columnValues.forEach(row => {
    const value = row[0];
    if (value instanceof Date && !isNaN(value.getTime())) {
      years[value.getFullYear()] = (years[value.getFullYear()] || 0) + 1;
    }
  });
  const known = Object.keys(years).sort((a, b) => years[b] - years[a]);
  const defaultYear = known.length ? Number(known[0]) : new Date().getFullYear();
  const valid = (y, m, d) => {
    const probe = new Date(y, m - 1, d);
    return probe.getFullYear() === y && probe.getMonth() === m - 1 && probe.getDate() === d;
  };
  return columnValues.map(row => {
    const value = row[0];
    if (value instanceof Date && !isNaN(value.getTime())) {
      return value.getFullYear() * 10000 + (value.getMonth() + 1) * 100 + value.getDate();
    }
    const text = String(value === null || value === undefined ? '' : value).trim();
    if (!text) return 99999999;
    const nums = (text.match(/\d+/g) || []).map(Number);
    let y = 0, m = 0, d = 0;
    const parts = text.match(/\d+/g) || [];
    if (parts.length >= 3 && parts[0].length === 4) { y = nums[0]; m = nums[1]; d = nums[2]; }
    else if (parts.length >= 3 && parts[2].length === 4) { y = nums[2]; m = nums[0]; d = nums[1]; }
    else if (parts.length === 2) { y = defaultYear; m = nums[0]; d = nums[1]; }
    return y && valid(y, m, d) ? y * 10000 + m * 100 + d : 99999998;
  });
}

/**
 * 메뉴 [날짜별 정렬·음영 다시 맞추기]: 지금 연 월별 출결 탭의 3행부터 마지막 줄까지를
 * 날짜 → 번호 순으로 줄 전체(숨긴 L열 포함)를 옮겨 정렬하고 날짜 음영 규칙을 다시 맞춘다.
 * 1행 AI 입력칸과 2행 제목은 건드리지 않는다(SHEET-MENU-01).
 */
function sortActiveMonthByDateAndStripes() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = spreadsheet.getActiveSheet();
  const title = '날짜별 정렬·음영';
  if (!isInputMonthSheet_(sheet)) {
    spreadsheet.toast('월별 출결 탭(예: 9월)을 연 뒤 다시 눌러 주세요.', title, 6);
    return;
  }
  if (sheet.getLastRow() < MONTHLY_ATTENDANCE_DATA_START_ROW) {
    reStripeSheet_(sheet);
    spreadsheet.toast('정렬할 출결 줄이 없어요.', title, 5);
    return;
  }
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(10000)) {
    spreadsheet.toast('다른 작업이 진행 중이에요. 잠시 뒤 다시 눌러 주세요.', title, 6);
    return;
  }
  try {
    const sorted = sortMonthlyAttendanceRows_(sheet, 'date');
    spreadsheet.toast(sorted
      ? '날짜·번호 순으로 정렬하고 음영을 다시 맞췄어요.'
      : '표 모양이 달라 정렬하지 않았어요. 필터를 끄고, M열 오른쪽에 적은 내용이 있으면 옮긴 뒤 다시 눌러 주세요.',
      title, 6);
  } finally {
    lock.releaseLock();
  }
}

function getHolidaySheetName_() {
  const cfg = getConfig_();
  return String(cfg.HOLIDAY_SHEET_NAME || FALLBACK_HOLIDAY_SHEET_NAME).trim();
}

function getClassLabel_() {
  const cfg = getConfig_();
  return String(cfg.CLASS_LABEL || FALLBACK_CLASS_LABEL).trim();
}

function getTemplateDocId_() {
  const cfg = getConfig_();
  const id = String(cfg.TEMPLATE_DOC_ID || FALLBACK_TEMPLATE_DOC_ID || '').trim();
  if (!id) throw new Error('신고서 양식 연결을 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부를 다시 확인해 주세요.');
  return id;
}

function getDestinationFolder_() {
  const cfg = getConfig_();
  const folderId = String(cfg.DEST_FOLDER_ID || '').trim();
  if (!folderId) {
    throw new Error('신고서 저장 폴더 연결을 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부를 다시 확인해 주세요.');
  }
  return DriveApp.getFolderById(folderId);
}

function getTaskListId_() {
  const cfg = getConfig_();
  const id = String(cfg.TASK_LIST_ID || FALLBACK_TASK_LIST_ID || '').trim();
  if (!id) throw new Error('출결 미제출 할 일 목록 연결을 찾지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 할 일 목록을 다시 확인해 주세요.');
  return id;
}

function todayKey_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Seoul', 'yyyy-MM-dd');
}

function replaceOptionalConfigPlaceholders_(body) {
  const cfg = getConfig_();
  replaceAll_(body, '{{학교명}}', String(cfg.SCHOOL_NAME || ''));
  replaceAll_(body, '{{학년}}', String(cfg.GRADE || ''));
  replaceAll_(body, '{{반}}', String(cfg.CLASS_NUMBER || ''));
  replaceAll_(body, '{{담임}}', String(cfg.TEACHER_NAME || ''));
  replaceAll_(body, '{학교명}', String(cfg.SCHOOL_NAME || ''));
  replaceAll_(body, '{학년}', String(cfg.GRADE || ''));
  replaceAll_(body, '{반}', String(cfg.CLASS_NUMBER || ''));
  replaceAll_(body, '{담임}', String(cfg.TEACHER_NAME || ''));
}

function isChatAppConfigurationError_(err) {
  return isCentralChatConnectionError_(err);
}

function isExactGoeduEmail_(value) {
  // 기존 이름은 호환용이다. Google 세션에서 받은 주소의 형식만 확인하며 도메인은 제한하지 않는다.
  if (typeof value !== 'string' || /[\u0000-\u001F\u007F-\u009F]/.test(value)) return false;
  const email = value.trim();
  if ((email.match(/@/g) || []).length !== 1) return false;
  const parts = email.split('@');
  const local = parts[0];
  const labels = parts[1].split('.');
  if (!/^[\p{L}\p{N}!#$%&'*+\-/=?^_`{|}~]+(?:\.[\p{L}\p{N}!#$%&'*+\-/=?^_`{|}~]+)*$/u.test(local)) return false;
  if (labels.length < 2) return false;
  return labels.every(label => /^[\p{L}\p{N}](?:[\p{L}\p{N}-]*[\p{L}\p{N}])?$/u.test(label));
}

function readSessionEmail_(event, allowEffectiveUser) {
  let eventEmail = '';
  try {
    eventEmail = String(
      event && event.user && typeof event.user.getEmail === 'function'
        ? event.user.getEmail()
        : ''
    ).trim();
  } catch (ignored) {}
  if (eventEmail) return eventEmail;

  let activeEmail = '';
  try {
    activeEmail = String(Session.getActiveUser().getEmail() || '').trim();
  } catch (ignored) {}
  // 현재 사용자가 보이면 그 계정을 그대로 판단한다.
  // 소유자의 허용 계정으로 바꿔 판단하면 안 된다.
  if (activeEmail) return activeEmail;
  // 설치형 자동 감지기는 실제 편집 계정을 모를 때 소유자 계정으로 대신 판단하지 않는다.
  // 누가 고쳤는지 확인할 수 없는 편집은 조용히 멈추는 것이 학생 자료를 잘못 다루는 것보다 안전하다.
  if (allowEffectiveUser === false) return '';
  try {
    return String(Session.getEffectiveUser().getEmail() || '').trim();
  } catch (ignored) {
    return '';
  }
}

function mayRunLocalSheetTrigger_(event) {
  // 단순 onEdit/onSelectionChange에서는 Google이 사용자 주소를 주지 않을 수 있다.
  // 이 두 함수는 현재 시트의 표시·서식만 고치므로, 주소가 정말 안 보일 때는 계속
  // 동작하게 한다. 다만 주소가 보인다면 허용 교사 계정인지 확인한다.
  const email = readSessionEmail_(event, false);
  return !email || isExactGoeduEmail_(email);
}

function requireGoeduTeacherAccount_(options) {
  const opts = options || {};
  const email = readSessionEmail_(opts.event, opts.allowEffectiveUser !== false);
  if (!isExactGoeduEmail_(email)) {
    throw new Error(
      '현재 로그인한 Google 계정을 확인하지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 연결을 다시 시작해 주세요.'
    );
  }
  if (opts.requireEffectiveUser === true) {
    let effectiveEmail = '';
    try {
      effectiveEmail = String(Session.getEffectiveUser().getEmail() || '').trim();
    } catch (ignored) {
      effectiveEmail = '';
    }
    if (!isExactGoeduEmail_(effectiveEmail)) {
      throw new Error(
        'AI 출결 입력을 켠 Google 계정을 확인하지 못했어요. 현재 사용할 Google 계정으로 AI 출결 입력을 다시 켜 주세요.'
      );
    }
    return effectiveEmail;
  }
  return email;
}

function requireStudentChatAccount_(value) {
  // 발신 계정 제한과 수신 계정 허용 범위를 섞지 않는다.
  const email = typeof value === 'string' ? value.trim() : '';
  if (!isExactGoeduEmail_(value)) {
    throw new Error(
      '학생의 Google 계정 이메일 주소 한 개를 입력해 주세요.'
    );
  }
  return email;
}

function attendanceManagedScope_(purpose, eventDate, operationId) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const cfg = readAttendanceConfigStrict_(spreadsheet);
  const year = String(cfg.SCHOOL_YEAR || '').trim();
  const generation = Number(cfg.ATTENDANCE_BINDING_GENERATION);
  if (String(cfg.ATTENDANCE_PROTOCOL_VERSION || '') !== '1' || !/^\d{4}$/.test(year)
      || !Number.isSafeInteger(generation) || generation < 1) {
    throw new Error('현재 출석부의 자동 처리 연결을 확인하지 못했습니다. Teacher Manager에서 연결 상태를 확인해 주세요.');
  }
  if (['automatic','historical-manual','health'].indexOf(purpose) < 0) {
    throw new Error('출결 작업의 목적을 확인하지 못했습니다.');
  }
  const result = { protocolVersion: 1, spreadsheetId: String(spreadsheet.getId()),
    workbookSchoolYear: Number(year), generation: generation, purpose: purpose,
    operationId: String(operationId || '').trim(),
    monthlySheetIds: parseAttendanceMonthSheetIds_(cfg.ATTENDANCE_MONTH_SHEET_IDS) };
  if (eventDate) result.eventDate = String(eventDate);
  if (purpose === 'historical-manual') {
    const parsed = attendanceAiParseDateKey_(result.eventDate);
    if (!parsed || (parsed.month >= 3 ? parsed.year : parsed.year - 1) !== result.workbookSchoolYear) {
      throw new Error('출결 날짜가 이 출석부의 학년도와 다릅니다. 다른 출석부로 옮겨 기록하지 않았습니다.');
    }
  }
  return result;
}

function readAttendanceCentralConfig_(spreadsheet) {
  const cfg = readAttendanceConfigStrict_(spreadsheet);
  const result = {url: String(cfg.CENTRAL_CHAT_SENDER_URL || '').trim(),
    sheetId: String(cfg.CENTRAL_CHAT_SHEET_ID || '').trim(),
    sheetSecret: String(cfg.CENTRAL_CHAT_SHEET_SECRET || '').trim()};
  if (!result.sheetId.startsWith(String(spreadsheet.getId()) + ':') || !result.sheetSecret) {
    throw new Error('이 출석부의 서버 연결을 확인하지 못했습니다. 기존 연결값은 바꾸지 않았습니다.');
  }
  return result;
}

function authorizeAttendanceOperation_(purpose, eventDate) {
  const scope = attendanceManagedScope_(purpose, eventDate, '');
  const response = callCentralChatSender_('/v1/attendance/authorize-operation',
    Object.assign({}, scope, {readOnly: true}));
  if (!response || response.authorized !== true
      || response.spreadsheetId !== scope.spreadsheetId
      || Number(response.workbookSchoolYear) !== scope.workbookSchoolYear
      || Number(response.generation) !== scope.generation
      || JSON.stringify(parseAttendanceMonthSheetIds_((response.resourceManifest || {}).monthlySheetIds))
        !== JSON.stringify(scope.monthlySheetIds)
      || ((purpose === 'automatic' || purpose === 'health')
          && Number(response.currentSchoolYear) !== scope.workbookSchoolYear)) {
    throw new Error('현재 출석부의 처리 권한을 확인하지 못했습니다. 기존 자료는 바꾸지 않았습니다.');
  }
  return response;
}

function centralChatPathNeedsTeacher_(path) {
  // 연결을 끊거나 서버 기록을 지우는 길만 예외다. 새 작업 주소가 나중에 생겨도
  // 목록에 깜빡하고 더하지 않았다는 이유로 계정 확인 없이 열리지 않게 기본은 차단한다.
  return ['/v1/disconnect', '/v1/account/delete']
    .indexOf(String(path || '').trim()) === -1;
}

function callCentralChatSender_(path, payload) {
  if (centralChatPathNeedsTeacher_(path)) requireGoeduTeacherAccount_();
  const safePayload = Object.assign({}, payload || {});
  if (String(path || '').trim() === '/v1/send/personal') {
    safePayload.studentEmail = requireStudentChatAccount_(safePayload.studentEmail);
  }
  const managed = String(path).indexOf('/v1/attendance/') === 0 || String(path).indexOf('/v1/send/') === 0;
  if (String(path).indexOf('/v1/send/') === 0) {
    Object.assign(safePayload, attendanceManagedScope_('automatic', '', safePayload.requestId));
  }
  const central = managed
    ? readAttendanceCentralConfig_(SpreadsheetApp.getActiveSpreadsheet()) : ensureCentralChatConfig_();
  if (!central.url) {
    throw new Error('Google Chat 자동 발송 기능을 사용할 수 없어요. Teacher Manager의 [Google 연결 → 출결]에서 연결 상태를 확인해 주세요.');
  }
  const body = Object.assign({}, safePayload, {
    sheetId: central.sheetId,
    sheetSecret: central.sheetSecret
  });
  const response = UrlFetchApp.fetch(central.url.replace(/\/$/, '') + path, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(body),
    muteHttpExceptions: true
  });
  const status = response.getResponseCode();
  const text = response.getContentText() || '{}';
  let data = {};
  try {
    data = JSON.parse(text);
  } catch (err) {
    throw centralSenderError_(status, { error: 'INVALID_RESPONSE' });
  }
  if (status < 200 || status >= 300) {
    throw centralSenderError_(status, data);
  }
  return data;
}

function centralSenderError_(status, data) {
  const code = String(data && (data.error || data.code) || 'CENTRAL_SENDER_ERROR').trim();
  const err = new Error('Google Chat 요청을 마치지 못했어요.');
  err.centralCode = code;
  err.httpStatus = Number(status || 0);
  return err;
}

function errorMessage_(err) {
  return String(err && err.message ? err.message : err || '알 수 없는 오류');
}

function sheetFacingErrorMessage_(err, operation) {
  if (err && err.centralCode) return centralChatErrorMessage_(err);
  return String(operation || '작업') + '의 결과를 확인하지 못했어요. 출결표에서 바뀐 내용이 있는지 먼저 확인해 주세요. 같은 작업을 다시 실행하지 말고 contact@big-silver.xyz로 문의해 주세요.';
}

function escapeHtml_(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showLinkDialog_(title, url, message) {
  const safeUrl = String(url || '').trim();
  if (!/^https:\/\/[^\s/$.?#].[^\s]*$/i.test(safeUrl)) throw new Error('안전한 연결 주소를 받지 못했습니다.');
  const html = HtmlService.createHtmlOutput(
    '<p>' + escapeHtml_(message) + '</p>' +
    '<p><a href="' + escapeHtml_(safeUrl) + '" target="_blank" rel="noopener noreferrer">연결 화면 열기</a></p>'
  ).setWidth(420).setHeight(180);
  SpreadsheetApp.getUi().showModalDialog(html, title);
}

function getSheetAuthorizationUrl_() {
  const info = ScriptApp.getAuthorizationInfo(ScriptApp.AuthMode.FULL);
  if (info.getAuthorizationStatus() !== ScriptApp.AuthorizationStatus.REQUIRED) return '';
  return String(info.getAuthorizationUrl() || '').trim();
}

function stableChatRequestId_(parts) {
  const source = (parts || []).map(value => String(value || '')).join('|');
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, source, Utilities.Charset.UTF_8);
  const hex = digest.map(byte => ('0' + ((byte + 256) % 256).toString(16)).slice(-2)).join('');
  return 'req-' + hex.slice(0, 48);
}

function centralSheetIdentityForRequest_() {
  try {
    return ensureCentralChatConfig_().sheetId;
  } catch (err) {
    try {
      const cfg = getConfig_();
      const configured = String(cfg.CENTRAL_CHAT_SHEET_ID || '').trim();
      if (configured) return configured;
    } catch (ignored) {}
    try {
      return SpreadsheetApp.getActiveSpreadsheet().getId();
    } catch (ignored) {}
  }
  return '';
}

function sendCentralPersonalChat_(studentEmail, text, meta) {
  const safeMeta = Object.assign({}, meta || {});
  const requestId = String(safeMeta.requestId || '').trim();
  delete safeMeta.requestId;
  return callCentralChatSender_('/v1/send/personal', {
    requestId: requestId,
    studentEmail: studentEmail,
    text: text,
    meta: safeMeta
  });
}

function sendCentralClassChat_(spaceName, text, meta) {
  const safeMeta = Object.assign({}, meta || {});
  const requestId = String(safeMeta.requestId || '').trim();
  delete safeMeta.requestId;
  return callCentralChatSender_('/v1/send/class', {
    requestId: requestId,
    spaceName: spaceName,
    text: text,
    meta: safeMeta
  });
}

// 발송을 현재 사용할 출석부로 옮긴 뒤 옛 시트에서 보내려 할 때 서버가 주는 답.
// 코드 글자를 그대로 보여주면 선생님은 고장 난 줄 알게 된다.
const CENTRAL_SHEET_MOVED_CODE = 'SHEET_MOVED';
const CENTRAL_SHEET_MOVED_MESSAGE =
  '이 출석부의 Google Chat 발송은 현재 사용할 출석부로 옮겼습니다.\n\n' +
  'Teacher Manager에서 현재 출석부를 열어 보내 주세요.\n\n' +
  '이 출석부의 내용은 그대로 남아 있습니다.';

/** Google Chat 오류를 선생님이 읽을 문장으로 바꾼다. 원문과 영문 코드는 보여주지 않는다. */
function centralChatErrorMessage_(err) {
  const code = String(err && err.centralCode || '').trim();
  if (code === CENTRAL_SHEET_MOVED_CODE) return CENTRAL_SHEET_MOVED_MESSAGE;
  if (['CHAT_NOT_CONNECTED', 'SHEET_AUTH_REQUIRED', 'AUTH_STATE_EXPIRED'].indexOf(code) !== -1) {
    return 'Google Chat 연결이 필요해요. Teacher Manager의 [Google 연결 → 출결]에서 [연결하기]를 눌러 주세요.';
  }
  if (code === 'GOEDU_ACCOUNT_REQUIRED') return '현재 로그인한 Google 계정을 확인하지 못했어요. Teacher Manager에서 연결을 다시 시작해 주세요.';
  if (code === 'CLASS_SPACE_REQUIRED') return '학급 단톡방을 먼저 골라 주세요. Teacher Manager의 [Google 연결 → 출결]에서 학급 단톡방을 선택해 주세요.';
  if (code === 'STUDENT_CHAT_ACCOUNT_REQUIRED') return '학생의 Google 이메일을 확인하지 못했어요. [학생명단] 탭 C열에서 해당 학생의 이메일을 확인해 주세요.';
  if (code === 'MESSAGE_TOO_LARGE') return '보낼 내용이 너무 길어요. 내용을 나누어 다시 보내 주세요.';
  // CHAT-REJECT-01: Google answered that nothing was delivered, so the same message may be sent again.
  if (code === 'STUDENT_CHAT_RECIPIENT_REJECTED') {
    return '학생의 Google Chat 계정으로 보낼 수 없어요. [학생명단] 탭 C열의 이메일이 맞는지 확인해 주세요. 보내지 않았으니 이메일을 고친 뒤 다시 보내면 돼요.';
  }
  if (code === 'CHAT_MESSAGE_REJECTED') return 'Google Chat이 이 메시지를 받지 않았어요. 보내지 않았으니 내용을 확인한 뒤 다시 보내 주세요.';
  if (code === 'ATTENDANCE_SEND_RESULT_UNKNOWN') {
    return '같은 메시지를 앞서 보낸 결과를 확인하지 못했어요. 다시 보내지 말고 [발송기록]에서 학생이 받았는지 먼저 확인해 주세요.';
  }
  // Unknown codes: name the code only when it is a safe code word, never blame the connection (C11).
  const safeCode = /^[A-Z][A-Z0-9_]{2,79}$/.test(code) ? ' (' + code + ')' : '';
  return 'Google Chat 요청을 마치지 못했어요' + safeCode + '. 메시지를 보내던 중이었다면 [발송기록]에서 결과를 먼저 확인하고, 같은 문제가 계속되면 contact@big-silver.xyz로 문의해 주세요.';
}

function isCentralChatConnectionError_(err) {
  const code = String(err && err.centralCode || '').trim();
  if (code === CENTRAL_SHEET_MOVED_CODE) return true;
  if (['CHAT_NOT_CONNECTED', 'SHEET_AUTH_REQUIRED', 'AUTH_STATE_EXPIRED', 'GOEDU_ACCOUNT_REQUIRED'].indexOf(code) !== -1) return true;
  const message = String(err && err.message ? err.message : err || '');
  return message.indexOf('최초 발송 연결') !== -1 ||
    message.indexOf('연결') !== -1 ||
    message.indexOf('권한') !== -1;
}

function showChatApiSetupRequired_(ui) {
  ui.alert(
    'Google Chat 자동 발송 준비가 아직 끝나지 않았습니다.\n\n' +
    '같은 실패를 반복하지 않도록 발송을 멈췄습니다.\n\n' +
    '보낼 내용은 원래 시트의 상태 칸에 남겨두었습니다.\n' +
    'Teacher Manager의 [Google 연결 → 출결]에서 [연결하기]를 누르고, 학급 단톡방을 골라 주세요.'
  );
}

function connectClassChatSpace(options) {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  // 통합 설정이 부를 때는 결과 창을 띄우지 않고 결과만 돌려준다.
  // 단톡방 목록에서 고르는 화면은 조용해도 그대로 뜬다 — 사람만 고를 수 있는 일이다.
  const quiet = !!(options && options.quiet === true);
  function finish_(ok, message) {
    if (!quiet) ui.alert(message);
    return { ok: ok, message: message };
  }

  try {
    const response = callCentralChatSender_('/v1/spaces', {});
    const spaces = response.spaces || [];
    if (!spaces.length) {
      return finish_(false, '선생님이 들어가 있는 Google Chat 단톡방을 찾지 못했습니다.');
    }
    const choices = spaces.slice(0, 20).map((space, index) => `${index + 1}. ${space.displayName} (${space.name})`).join('\n');
    const answer = ui.prompt(
      'Google Chat 학급 단톡방 고르기',
      '학급 쪽지를 보낼 Google Chat 단톡방 번호를 입력하세요.\n\n' + choices,
      ui.ButtonSet.OK_CANCEL
    );
    // 취소는 선생님이 고른 결과이므로 창을 하나 더 띄우지 않는다.
    if (answer.getSelectedButton() !== ui.Button.OK) {
      return { ok: false, message: '학급 단톡방을 아직 고르지 않았습니다.' };
    }
    const index = Number(String(answer.getResponseText() || '').trim()) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= Math.min(spaces.length, 20)) {
      return finish_(false, '번호를 확인할 수 없습니다. 목록에 보이는 번호를 입력해 주세요.');
    }
    const selected = spaces[index];
    // 실제 발송이 읽는 설정 시트를 먼저 저장한다. 서버부터 바꾸면 시트 저장이
    // 실패했을 때 화면에는 선택된 것처럼 보이지만 발송은 방을 찾지 못한다.
    // ID를 이름보다 먼저 적어, 이름 저장에서 멈춰도 재시도와 발송이 가능하게 한다.
    setConfigValue_('CLASS_CHAT_SPACE_ID', selected.name);
    setConfigValue_('CLASS_CHAT_SPACE_NAME', selected.displayName);
    callCentralChatSender_('/v1/class-space', {
      spaceName: selected.name,
      displayName: selected.displayName
    });
    return finish_(true, 'Google Chat 학급 단톡방을 골랐습니다.\n\n' + selected.displayName);
  } catch (err) {
    return finish_(false, 'Google Chat 학급 단톡방을 고르지 못했습니다.\n\n' + centralChatErrorMessage_(err));
  }
}

function splitChatMessage_(text) {
  const source = String(text || '').trim();
  if (!source) return [];
  const parts = [];
  let current = '';

  function pushChunkedLine_(line) {
    if (!line) return;
    let chunk = '';
    for (let i = 0; i < line.length; i++) {
      const candidate = chunk + line.charAt(i);
      if (Utilities.newBlob(candidate).getBytes().length > CHAT_MESSAGE_LIMIT_BYTES) {
        if (chunk) {
          parts.push(chunk);
          chunk = line.charAt(i);
        } else {
          parts.push(line.charAt(i));
          chunk = '';
        }
      } else {
        chunk = candidate;
      }
    }
    if (chunk) parts.push(chunk);
  }

  source.split(/\r?\n/).forEach(line => {
    const next = current ? current + '\n' + line : line;
    if (Utilities.newBlob(next).getBytes().length > CHAT_MESSAGE_LIMIT_BYTES) {
      if (current) parts.push(current);
      if (Utilities.newBlob(line).getBytes().length > CHAT_MESSAGE_LIMIT_BYTES) {
        pushChunkedLine_(line);
        current = '';
      } else {
        current = line;
      }
    } else {
      current = next;
    }
  });
  if (current) parts.push(current);
  return parts;
}

function appendChatLog_(kind, target, spaceId, text, result, error) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureChatLogSheet_(ss);
  const preview = String(text || '').replace(/\s+/g, ' ').slice(0, 200);
  sh.appendRow([
    Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss'),
    kind,
    target,
    spaceId,
    preview,
    result,
    error
  ]);
}

function formatAttendanceNoticeDate_(value) {
  if (value === null || value === undefined || value === '') return '';
  const date = value instanceof Date ? value : new Date(String(value).trim());
  if (isNaN(date.getTime())) return String(value).trim();
  const dayNames = ['일요일', '월요일', '화요일', '수요일', '목요일', '금요일', '토요일'];
  return date.getFullYear() + '년 ' + (date.getMonth() + 1) + '월 ' + date.getDate() + '일 ' + dayNames[date.getDay()];
}

function buildAttendanceChatLinesForRow_(sheetName, rowIdx, row, sendDateKey) {
  const values = row || [];
  const studentText = String(values[1] || '').trim();
  const parsed = parseStudentLabel_(studentText);
  const number = String(parsed.number || '').trim();
  const name = String(parsed.name || studentText).trim();
  const attendanceDate = formatAttendanceNoticeDate_(values[0]);
  const lines = [];

  if (String(values[6] || '').trim() === '미제출') {
    lines.push(attendanceDate + ' 결석신고서 미제출입니다. 제출해주세요.');
  }
  if (String(values[7] || '').trim() === '미제출') {
    lines.push(attendanceDate + ' 출결 첨부서류 미제출입니다. 제출해주세요.');
  }

  return {
    rowIdx: Number(rowIdx),
    sheetName: String(sheetName || ''),
    sendDate: String(sendDateKey || todayKey_()).trim(),
    number: number,
    name: name,
    lines: lines
  };
}

function buildAttendanceChatGroupsForSelectedRows_(sheet, selectedRows) {
  const dataRows = (selectedRows || [])
    .map(rowIdx => Number(rowIdx))
    .filter(rowIdx => Number.isInteger(rowIdx) && rowIdx >= MONTHLY_ATTENDANCE_DATA_START_ROW);
  if (!dataRows.length) return [];
  const resultCols = ensureMonthlyChatResultColumns_(sheet);
  if (!resultCols) return [];
  const rosterMap = loadStudentRosterForDm_();
  const values = sheet.getDataRange().getValues();
  return dataRows.map(rowIdx => {
    const row = values[Number(rowIdx) - 1] || [];
    const status = resultCols ? String(row[resultCols.statusCol - 1] || '').trim() : '';
    const currentSignature = attendanceChatSignature_(sheet.getName(), rowIdx, row);
    const savedSignature = resultCols && resultCols.signatureCol ? String(row[resultCols.signatureCol - 1] || '').trim() : '';
    if (status === '보냄' && savedSignature === currentSignature) return null;
    const group = buildAttendanceChatLinesForRow_(sheet.getName(), rowIdx, row, todayKey_());
    const student = findQueueStudent_(group, rosterMap);
    group.email = student && student.email ? student.email : '';
    group.target = (student && student.combined) || `${group.number || ''}${group.name || ''}` || group.name || group.number;
    group.signature = currentSignature;
    return group;
  }).filter(group => group && group.lines.length);
}

function formatQueueDateKey_(value, fallback) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    const year = value.getFullYear();
    const month = ('0' + (value.getMonth() + 1)).slice(-2);
    const day = ('0' + value.getDate()).slice(-2);
    return year + '-' + month + '-' + day;
  }
  const text = String(value || '').trim();
  if (!text) return String(fallback || '').trim();
  const match = text.match(/^(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})$/);
  if (!match) return text;
  return match[1] + '-' + ('0' + match[2]).slice(-2) + '-' + ('0' + match[3]).slice(-2);
}

function attendanceChatLegacySignatureV1_(sheetName, rowIdx, row) {
  const values = row || [];
  return stableChatRequestId_([
    sheetName,
    rowIdx,
    formatQueueDateKey_(values[0], ''),
    values[1],
    values[6],
    values[7]
  ]);
}

function attendanceChatSignatureV2_(row) {
  const values = row || [];
  return stableChatRequestId_([JSON.stringify([
    'attendance-chat-v2',
    formatQueueDateKey_(values[0], ''),
    String(values[1] || '').trim(),
    String(values[6] || '').trim(),
    String(values[7] || '').trim()
  ])]);
}

function attendanceChatSignature_(sheetName, rowIdx, row) {
  return attendanceChatSignatureV2_(row);
}

function attendanceChatRequestId_(sheetIdentity, email, signature, text) {
  return stableChatRequestId_([JSON.stringify([
    'attendance-chat-send-v2',
    String(sheetIdentity || '').trim(),
    String(email || '').trim(),
    String(signature || '').trim(),
    String(text || '')
  ])]);
}

function groupPersonalMessageQueueRows_(rows, targetDateKey) {
  const targetDate = String(targetDateKey || '').trim();
  const groups = [];
  const byKey = {};
  (rows || []).forEach((row, index) => {
    const dateKey = formatQueueDateKey_(row[0], '');
    const status = String(row[6] || '').trim();
    const text = String(row[4] || '').trim();
    if (!dateKey || dateKey > targetDate || status !== '대기' || !text) return;
    const number = String(row[1] || '').trim();
    const name = String(row[2] || '').trim();
    const key = `${number}|${name}`;
    if (!byKey[key]) {
      byKey[key] = { key, number, name, lines: [], rowNumbers: [] };
      groups.push(byKey[key]);
    }
    byKey[key].lines.push(text);
    byKey[key].rowNumbers.push(index + 2);
  });
  return groups;
}

// 오늘 날짜 전체가 아니라 '이 출결 행(들)에서 나온 줄'만 학생별로 묶는다.
// 상태가 '대기'인 것만 대상으로 삼아 이미 '보냄'인 줄은 다시 보내지 않는다.
function groupPersonalQueueRowsByLinkPrefixes_(rows, linkPrefixes) {
  const prefixes = linkPrefixes || [];
  const groups = [];
  const byKey = {};
  (rows || []).forEach((row, index) => {
    const status = String(row[6] || '').trim();
    const link = String(row[7] || '').trim();
    const text = String(row[4] || '').trim();
    if (status !== '대기' || !text || !link) return;
    if (!prefixes.some(prefix => link.startsWith(prefix))) return;
    const number = String(row[1] || '').trim();
    const name = String(row[2] || '').trim();
    const key = `${number}|${name}`;
    const rowIdxMatch = link.match(/^출결표\|[^|]+\|(\d+)\|/);
    const rowIdx = rowIdxMatch ? Number(rowIdxMatch[1]) : 0;
    if (!byKey[key]) {
      byKey[key] = { key, number, name, lines: [], rowNumbers: [], rowIdx };
      groups.push(byKey[key]);
    }
    byKey[key].lines.push(text);
    byKey[key].rowNumbers.push(index + 2);
  });
  return groups;
}

function groupClassMessageQueueRows_(rows, targetDateKey) {
  const targetDate = String(targetDateKey || '').trim();
  const result = { lines: [], rowNumbers: [] };
  (rows || []).forEach((row, index) => {
    const dateKey = formatQueueDateKey_(row[0], '');
    const status = String(row[4] || '').trim();
    const text = String(row[2] || '').trim();
    if (!dateKey || dateKey > targetDate || status !== '대기' || !text) return;
    result.lines.push(text);
    result.rowNumbers.push(index + 2);
  });
  return result;
}

function getQueueRows_(sheet, width) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  return sheet.getRange(2, 1, lastRow - 1, width).getValues();
}

function normalizeMessageLine_(line) {
  return String(line || '').replace(/\s+/g, ' ').trim();
}

function appendClassMessageQueueLines_(lines, sendDateKey, source, status, kind) {
  const cleanLines = (lines || []).map(line => normalizeMessageLine_(line)).filter(Boolean);
  if (!cleanLines.length) return { added: 0, lines: [] };
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureClassMessageQueueSheet_(ss);
  const targetDate = String(sendDateKey || todayKey_()).trim();
  const cleanSource = String(source || '자동분석').trim();
  const requestedStatus = String(status || '').trim();
  const cleanStatus = !requestedStatus || requestedStatus === '확인필요' ? '대기' : requestedStatus;
  const cleanKind = String(kind || '기타').trim();
  const existing = new Set();
  const rows = getQueueRows_(sh, CLASS_MESSAGE_QUEUE_HEADERS.length);
  rows.forEach(row => {
    const rowDate = formatQueueDateKey_(row[0], '');
    const rowText = normalizeMessageLine_(row[2]);
    if (rowDate && rowText && String(row[4] || '').trim() !== '보냄') {
      existing.add(`${rowDate}|${rowText}`);
    }
  });
  const toAppend = [];
  cleanLines.forEach(line => {
    const key = `${targetDate}|${line}`;
    if (existing.has(key)) return;
    existing.add(key);
    toAppend.push([targetDate, cleanKind, line, cleanSource, cleanStatus, '', '']);
  });
  if (toAppend.length) {
    sh.getRange(sh.getLastRow() + 1, 1, toAppend.length, CLASS_MESSAGE_QUEUE_HEADERS.length).setValues(toAppend);
  }
  return { added: toAppend.length, lines: cleanLines };
}

function appendPersonalMessageQueueItemsForAutomation(items) {
  requireGoeduTeacherAccount_();
  authorizeAttendanceOperation_('automatic', '');
  const rows = [];
  (items || []).forEach(item => {
    const content = normalizeMessageLine_(item && item.content);
    const name = String(item && item.name || '').trim();
    if (!content || !name) return;
    const requestedStatus = String(item.status || '').trim();
    rows.push([
      String(item.sendDate || todayKey_()).trim(),
      String(item.number || '').trim(),
      name,
      String(item.type || '기타').trim(),
      content,
      String(item.source || '자동분석').trim(),
      !requestedStatus || requestedStatus === '확인필요' ? '대기' : requestedStatus,
      String(item.link || '').trim(),
      '',
      ''
    ]);
  });
  if (!rows.length) return { added: 0 };
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensurePersonalMessageQueueSheet_(ss);
  sh.getRange(sh.getLastRow() + 1, 1, rows.length, PERSONAL_MESSAGE_QUEUE_HEADERS.length).setValues(rows);
  return { added: rows.length };
}

function appendClassMessageQueueItemsForAutomation(items) {
  requireGoeduTeacherAccount_();
  authorizeAttendanceOperation_('automatic', '');
  const lines = [];
  (items || []).forEach(item => {
    const content = item && item.content;
    if (content) lines.push(content);
  });
  return appendClassMessageQueueLines_(lines, todayKey_(), '자동분석', '대기', '기타');
}

function appendAnalyzedMessageQueueItemsForAutomation(payload) {
  requireGoeduTeacherAccount_();
  const data = payload || {};
  return {
    personal: appendPersonalMessageQueueItemsForAutomation(data.personal || []),
    class: appendClassMessageQueueItemsForAutomation(data.class || data.group || [])
  };
}

function findQueueStudent_(group, rosterMap) {
  const candidates = [
    String(group.number || '') + String(group.name || ''),
    String(group.name || ''),
    String(group.number || '')
  ];
  for (const candidate of candidates) {
    const key = candidate.replace(/\s+/g, '');
    if (key && rosterMap[key]) return rosterMap[key];
  }
  return null;
}

function setPersonalQueueRowsResult_(sheet, rowNumbers, status, sentAt, result) {
  setQueueRowsResult_(sheet, rowNumbers, 7, 9, 10, status, sentAt, result);
}

function setClassQueueRowsResult_(sheet, rowNumbers, status, sentAt, result) {
  setQueueRowsResult_(sheet, rowNumbers, 5, 6, 7, status, sentAt, result);
}

function setQueueRowsResult_(sheet, rowNumbers, statusCol, sentAtCol, resultCol, status, sentAt, result) {
  const sorted = (rowNumbers || []).map(Number).filter(rowNumber => rowNumber >= 2).sort((a, b) => a - b);
  let index = 0;
  while (index < sorted.length) {
    const startRow = sorted[index];
    const block = [startRow];
    index++;
    while (index < sorted.length && sorted[index] === block[block.length - 1] + 1) {
      block.push(sorted[index]);
      index++;
    }
    if (sentAtCol === statusCol + 1 && resultCol === statusCol + 2) {
      const values = block.map(() => [status || '', sentAt || '', result || '']);
      sheet.getRange(startRow, statusCol, block.length, 3).setValues(values);
    } else {
      const width = resultCol - statusCol + 1;
      const offsetSentAt = sentAtCol - statusCol;
      const offsetResult = resultCol - statusCol;
      const range = sheet.getRange(startRow, statusCol, block.length, width);
      const values = range.getValues().map(row => {
        const next = row.slice();
        next[0] = status || '';
        next[offsetSentAt] = sentAt || '';
        next[offsetResult] = result || '';
        return next;
      });
      range.setValues(values);
    }
  }
}

function withDocumentLock_(work) {
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(5000)) throw new Error('다른 발송이 진행 중입니다. 잠시 후 다시 눌러 주세요.');
  try {
    return work();
  } finally {
    lock.releaseLock();
  }
}

function queueClaimIsStale_(sentAt, now) {
  const text = String(sentAt || '').trim();
  if (!text) return false;
  const parsed = new Date(text.replace(' ', 'T'));
  if (isNaN(parsed.getTime())) return false;
  return now.getTime() - parsed.getTime() > 10 * 60 * 1000;
}

function recoverStaleQueueClaims_(sheet, width, statusIndex, sentAtIndex) {
  const rows = getQueueRows_(sheet, width);
  const staleRows = [];
  const now = new Date();
  rows.forEach((row, index) => {
    if (String(row[statusIndex] || '').trim() === '발송중' && queueClaimIsStale_(row[sentAtIndex], now)) {
      staleRows.push(index + 2);
    }
  });
  if (staleRows.length) {
    setQueueRowsResult_(sheet, staleRows, statusIndex + 1, sentAtIndex + 1, sentAtIndex + 2, '대기', '', '');
  }
  return staleRows.length;
}

function queueStoredRequestId_(rows, rowNumbers, resultIndex) {
  for (const rowNumber of (rowNumbers || [])) {
    const row = rows[Number(rowNumber) - 2] || [];
    const match = String(row[resultIndex] || '').match(/req-[a-f0-9]+/);
    if (match) return match[0];
  }
  return '';
}

// acceptRow는 대시보드 보내기만 넘긴다. 잠금 안에서 그 줄이 선생님이 확인한 내용 그대로인지 본다.
function claimPersonalQueueRows_(sheet, group, requestId, acceptRow) {
  return withDocumentLock_(() => {
    const rows = getQueueRows_(sheet, PERSONAL_MESSAGE_QUEUE_HEADERS.length);
    const canClaim = (group.rowNumbers || []).every(rowNumber => {
      const row = rows[Number(rowNumber) - 2] || [];
      if (typeof acceptRow === 'function' && !acceptRow(Number(rowNumber), row)) return false;
      return String(row[6] || '').trim() === '대기';
    });
    if (!canClaim) return false;
    setPersonalQueueRowsResult_(sheet, group.rowNumbers, '발송중', timestampKey_(), requestId);
    return true;
  });
}

function claimClassQueueRows_(sheet, rowNumbers, requestId, acceptRow) {
  return withDocumentLock_(() => {
    const rows = getQueueRows_(sheet, CLASS_MESSAGE_QUEUE_HEADERS.length);
    const canClaim = (rowNumbers || []).every(rowNumber => {
      const row = rows[Number(rowNumber) - 2] || [];
      if (typeof acceptRow === 'function' && !acceptRow(Number(rowNumber), row)) return false;
      return String(row[4] || '').trim() === '대기';
    });
    if (!canClaim) return false;
    setClassQueueRowsResult_(sheet, rowNumbers, '발송중', timestampKey_(), requestId);
    return true;
  });
}

function safeAppendChatLog_(args) {
  try {
    appendChatLog_.apply(null, args || []);
    return '';
  } catch (err) {
    return '메시지는 보냈어요. 다시 보내지 말고 발송 결과를 확인해 주세요.';
  }
}

function ensureSheetHasColumns_(sheet, minColumns) {
  if (!sheet || !minColumns || typeof sheet.getMaxColumns !== 'function' || typeof sheet.insertColumnsAfter !== 'function') return;
  const maxColumns = sheet.getMaxColumns();
  if (maxColumns < minColumns) sheet.insertColumnsAfter(maxColumns, minColumns - maxColumns);
}

function ensureMonthlyChatResultColumns_(sheet) {
  if (!sheet || !isInputMonthSheet_(sheet)) return null;
  const startCol = INPUT_HEADERS.length + 1;
  const requiredLastCol = startCol + MONTHLY_CHAT_RESULT_HEADERS.length - 1;
  const headerWidth = Math.max(Math.min(sheet.getMaxColumns(), requiredLastCol), 1);
  const headerRow = sheet.getRange(
    MONTHLY_ATTENDANCE_HEADER_ROW,
    1,
    1,
    headerWidth
  ).getValues()[0].map(v => String(v || '').trim());
  // 제목이 1행에 있는 옛 시트에서는 2행이 첫 학생 기록이다.
  // 그 줄을 제목 줄로 보고 글자를 쓰면 학생 기록을 덮어쓴다.
  // 2행 앞부분이 정확한 제목이 아니면 아무것도 하지 않는다.
  const inputHeadersMatch = INPUT_HEADERS.every(
    (name, index) => String(headerRow[index] || '').trim() === name
  );
  if (!inputHeadersMatch) return null;
  const currentHeaders = MONTHLY_CHAT_RESULT_HEADERS.map(
    (_, index) => String(headerRow[startCol - 1 + index] || '')
  );
  const normalizedHeader = value => String(value || '').replace(/\s+/g, ' ').trim();
  // 비었거나 제 이름인 칸만 있으면 손대도 된다. 다른 글자가 하나라도 있으면
  // 선생님이 직접 쓰신 제목이므로 아무것도 하지 않는다.
  const onlyOursOrEmpty = currentHeaders.every(
    (value, index) => {
      const normalized = normalizedHeader(value);
      return !normalized
        || normalized === normalizedHeader(MONTHLY_CHAT_RESULT_HEADERS[index])
        || (index === 1 && normalized === 'Google Chat 시도시각');
    }
  );
  if (!onlyOursOrEmpty) return null;

  ensureSheetHasColumns_(sheet, requiredLastCol);
  // 빈 칸만 채운다. 예전 판으로 만든 시트에는 앞의 세 칸만 있고 네 번째
  // `Google Chat 내용기준`이 비어 있다. 그 한 칸 때문에 그 달 전체를
  // 건너뛰면 발송이 조용히 멈춘다.
  currentHeaders.forEach((value, index) => {
    if (value === MONTHLY_CHAT_RESULT_HEADERS[index]) return;
    sheet.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, startCol + index, 1, 1)
      .setValues([[MONTHLY_CHAT_RESULT_HEADERS[index]]]);
  });
  const range = sheet.getRange(
    MONTHLY_ATTENDANCE_HEADER_ROW,
    startCol,
    Math.max(sheet.getMaxRows() - MONTHLY_ATTENDANCE_INPUT_ROW, 1),
    MONTHLY_CHAT_RESULT_HEADERS.length
  );
  range.setWrap(true).setVerticalAlignment('middle');
  sheet.setColumnWidths(startCol, 1, 130);
  sheet.setColumnWidths(startCol + 1, 1, 150);
  sheet.setColumnWidths(startCol + 2, 1, 360);
  if (typeof sheet.setRowHeight === 'function') {
    sheet.setRowHeight(MONTHLY_ATTENDANCE_HEADER_ROW, 40);
  }
  if (typeof sheet.hideColumns === 'function') sheet.hideColumns(startCol + 3);
  return {
    statusCol: startCol,
    attemptedAtCol: startCol + 1,
    resultCol: startCol + 2,
    signatureCol: startCol + 3
  };
}

function setMonthlyChatResult_(sheet, rowIdx, status, result, signature) {
  const rowIdxAllowed = (
    (typeof rowIdx === 'number' && Number.isSafeInteger(rowIdx))
    || (
      typeof rowIdx === 'string'
      && /^\d+$/.test(rowIdx)
      && Number.isSafeInteger(Number(rowIdx))
    )
  );
  if (!rowIdxAllowed) return;
  const safeRowIdx = Number(rowIdx);
  if (safeRowIdx < MONTHLY_ATTENDANCE_DATA_START_ROW) return;
  const cols = ensureMonthlyChatResultColumns_(sheet);
  if (!cols) return;
  sheet.getRange(safeRowIdx, cols.statusCol, 1, 4).setValues([[
    status || '',
    timestampKey_(),
    result || '',
    signature || ''
  ]]);
}

function clearMonthlyChatResultForRows_(sheet, startRow, endRow) {
  const startRowAllowed = (
    (typeof startRow === 'number' && Number.isSafeInteger(startRow))
    || (
      typeof startRow === 'string'
      && /^\d+$/.test(startRow)
      && Number.isSafeInteger(Number(startRow))
    )
  );
  const endRowAllowed = (
    (typeof endRow === 'number' && Number.isSafeInteger(endRow))
    || (
      typeof endRow === 'string'
      && /^\d+$/.test(endRow)
      && Number.isSafeInteger(Number(endRow))
    )
  );
  if (!startRowAllowed || !endRowAllowed) return;
  const numericStartRow = Number(startRow);
  const numericEndRow = Number(endRow);
  if (numericStartRow < 1 || numericEndRow < 1) return;
  const safeStartRow = Math.max(MONTHLY_ATTENDANCE_DATA_START_ROW, numericStartRow);
  if (numericEndRow < safeStartRow) return;
  const cols = ensureMonthlyChatResultColumns_(sheet);
  if (!cols) return;
  sheet.getRange(safeStartRow, cols.statusCol, numericEndRow - safeStartRow + 1, 4).clearContent();
}

function timestampKey_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss');
}

// options는 대시보드 보내기만 넘긴다(메뉴는 넘기지 않아 동작이 같다).
// skipStaleRecovery: 결과를 모르는 '발송중' 줄을 선생님 확인 없이 '대기'로 되돌리지 않는다(C7).
// acceptRow(rowNumber, row): 선생님이 확인 창에서 본 그대로인 줄만 보낸다.
function sendTodayClassMessageQueue_(targetDateKey, options) {
  const opts = options || {};
  authorizeAttendanceOperation_('automatic', '');
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureClassMessageQueueSheet_(ss);
  if (!opts.skipStaleRecovery) recoverStaleQueueClaims_(sh, CLASS_MESSAGE_QUEUE_HEADERS.length, 4, 5);
  const rows = getQueueRows_(sh, CLASS_MESSAGE_QUEUE_HEADERS.length)
    .map((row, index) => (typeof opts.acceptRow === 'function' && !opts.acceptRow(index + 2, row) ? [] : row));
  const targetDate = String(targetDateKey || todayKey_()).trim();
  const grouped = groupClassMessageQueueRows_(rows, targetDate);
  if (!grouped.lines.length) return { sent: 0, skipped: 0, failed: 0, sentButRecordUnconfirmed: 0, rows: 0 };

  const cfg = getConfig_();
  const classSpaceId = String(cfg.CLASS_CHAT_SPACE_ID || '').trim();
  const classSpaceName = String(cfg.CLASS_CHAT_SPACE_NAME || '').trim() || classSpaceId;
  if (!classSpaceId) throw new Error('학급 Chat 방이 아직 연결되지 않았습니다.');

  const text = grouped.lines.join('\n');
  const requestId = stableChatRequestId_([
    centralSheetIdentityForRequest_(),
    MESSENGER_CLASS_SHEET_NAME,
    grouped.rowNumbers.join(','),
    classSpaceId,
    text
  ]);
  if (!claimClassQueueRows_(sh, grouped.rowNumbers, requestId, opts.acceptRow)) return { sent: 0, skipped: 1, failed: 0, sentButRecordUnconfirmed: 0, rows: grouped.rowNumbers.length };
  let sendResult;
  try {
    sendResult = sendCentralClassChat_(classSpaceId, text, {
      requestId: requestId,
      source: 'messenger-class',
      targetDate: targetDate
    });
  } catch (err) {
    const error = centralChatErrorMessage_(err);
    if (isCentralChatConnectionError_(err)) {
      setClassQueueRowsResult_(sh, grouped.rowNumbers, '대기', timestampKey_(), error);
      appendChatLog_('단체방', classSpaceName, classSpaceId, text, '중단', error);
      throw err;
    }
    setClassQueueRowsResult_(sh, grouped.rowNumbers, '실패', timestampKey_(), error);
    appendChatLog_('단체방', classSpaceName, classSpaceId, text, '실패', error);
    throw err;
  }

  const messageCount = Number(sendResult.messageCount || 1);
  const sentAt = timestampKey_();
  const success = `성공 ${messageCount}건`;
  try {
    setClassQueueRowsResult_(sh, grouped.rowNumbers, '보냄', sentAt, success);
    const logWarning = safeAppendChatLog_(['단체방', classSpaceName, sendResult.spaceId || classSpaceId, text, success, '']);
    if (logWarning) {
      setClassQueueRowsResult_(sh, grouped.rowNumbers, '보냄', sentAt, success + '\n' + logWarning);
      return { sent: 1, skipped: 0, failed: 0, sentButRecordUnconfirmed: 1, rows: grouped.rowNumbers.length, failures: [logWarning] };
    }
    return { sent: 1, skipped: 0, failed: 0, sentButRecordUnconfirmed: 0, rows: grouped.rowNumbers.length };
  } catch (err) {
    const warning = '메시지는 보냈어요. 다시 보내지 말고 발송 결과를 확인해 주세요.';
    safeAppendChatLog_(['단체방', classSpaceName, sendResult.spaceId || classSpaceId, text, '기록주의', warning]);
    return { sent: 1, skipped: 0, failed: 0, sentButRecordUnconfirmed: 1, rows: grouped.rowNumbers.length, recordWarning: warning, failures: [warning] };
  }
}

// options는 sendTodayClassMessageQueue_와 같다(대시보드만 넘긴다).
function sendTodayPersonalMessageQueue_(targetDateKey, options) {
  const opts = options || {};
  authorizeAttendanceOperation_('automatic', '');
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensurePersonalMessageQueueSheet_(ss);
  if (!opts.skipStaleRecovery) recoverStaleQueueClaims_(sh, PERSONAL_MESSAGE_QUEUE_HEADERS.length, 6, 8);
  const rows = getQueueRows_(sh, PERSONAL_MESSAGE_QUEUE_HEADERS.length)
    .map((row, index) => (typeof opts.acceptRow === 'function' && !opts.acceptRow(index + 2, row) ? [] : row));
  const targetDate = String(targetDateKey || todayKey_()).trim();
  const groups = groupPersonalMessageQueueRows_(rows, targetDate);
  const rosterMap = loadStudentRosterForDm_();
  const result = { sent: 0, skipped: 0, failed: 0, sentButRecordUnconfirmed: 0, rows: 0, failures: [], chatApiSetupBlocked: false };

  for (const group of groups) {
    const student = findQueueStudent_(group, rosterMap);
    const target = `${group.number || ''}${group.name || ''}` || group.name || group.number;
    const text = group.lines.join('\n');
    if (!student || !student.email) {
      const error = '학생 Google 이메일 없음';
      setPersonalQueueRowsResult_(sh, group.rowNumbers, '실패', timestampKey_(), error);
      appendChatLog_('개인DM', target, '', text, '건너뜀', error);
      result.failed++;
      result.rows += group.rowNumbers.length;
      result.failures.push(target + ': ' + error);
      continue;
    }

    const requestId = stableChatRequestId_([
      centralSheetIdentityForRequest_(),
      MESSENGER_PERSONAL_SHEET_NAME,
      group.rowNumbers.join(','),
      student.email,
      text
    ]);
    if (!claimPersonalQueueRows_(sh, group, requestId, opts.acceptRow)) {
      result.skipped++;
      result.rows += group.rowNumbers.length;
      continue;
    }

    let sendResult;
    try {
      sendResult = sendCentralPersonalChat_(student.email, text, {
        requestId: requestId,
        source: 'messenger-personal',
        targetDate: targetDate,
        studentNumber: student.number || group.number || '',
        studentName: student.name || group.name || ''
      });
    } catch (err) {
      const error = centralChatErrorMessage_(err);
      if (isCentralChatConnectionError_(err)) {
        result.chatApiSetupBlocked = true;
        setPersonalQueueRowsResult_(sh, group.rowNumbers, '대기', timestampKey_(), error);
        appendChatLog_('개인DM', student.combined || target, '', text, '중단', error);
        result.failed++;
        result.rows += group.rowNumbers.length;
        result.failures.push((student.combined || target) + ': ' + error);
        return result;
      }
      setPersonalQueueRowsResult_(sh, group.rowNumbers, '실패', timestampKey_(), error);
      appendChatLog_('개인DM', student.combined || target, '', text, '실패', error);
      result.failed++;
      result.rows += group.rowNumbers.length;
      result.failures.push((student.combined || target) + ': ' + error);
      continue;
    }

    const messageCount = Number(sendResult.messageCount || 1);
    const sentAt = timestampKey_();
    const success = `성공 ${messageCount}건`;
    try {
      setPersonalQueueRowsResult_(sh, group.rowNumbers, '보냄', sentAt, success);
      const logWarning = safeAppendChatLog_(['개인DM', student.combined || target, sendResult.spaceId || '', text, success, '']);
      if (logWarning) {
        setPersonalQueueRowsResult_(sh, group.rowNumbers, '보냄', sentAt, success + '\n' + logWarning);
        result.sentButRecordUnconfirmed++;
        result.failures.push((student.combined || target) + ': ' + logWarning);
      }
    } catch (err) {
      const warning = '메시지는 보냈어요. 다시 보내지 말고 발송 결과를 확인해 주세요.';
      safeAppendChatLog_(['개인DM', student.combined || target, sendResult.spaceId || '', text, '기록주의', warning]);
      result.failures.push((student.combined || target) + ': ' + warning);
      result.sentButRecordUnconfirmed++;
    }
    result.sent++;
    result.rows += group.rowNumbers.length;
  }
  return result;
}

function personalSendResultSummary_(result) {
  const safe = result || {};
  let message = '보냄 ' + Number(safe.sent || 0) + '명' +
    ' · 이미 보내서 건너뜀 ' + Number(safe.skipped || 0) + '명' +
    ' · 보내지 못함 ' + Number(safe.failed || 0) + '명';
  const unconfirmed = Number(safe.sentButRecordUnconfirmed || 0);
  if (unconfirmed) message += ' · 발송 결과를 확인할 항목 ' + unconfirmed + '명';
  return message;
}

// 메신저 단체톡 내용의 오늘 '대기' 줄만 학급 단톡방으로 보낸다.
function sendTodayClassMessagesOnly() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const classSheet = ensureClassMessageQueueSheet_(ss);
    const today = todayKey_();
    const classRows = getQueueRows_(classSheet, CLASS_MESSAGE_QUEUE_HEADERS.length);
    const classGroup = groupClassMessageQueueRows_(classRows, today);

    if (!classGroup.lines.length) {
      ui.alert('보낼 대기 단체 쪽지가 없습니다.');
      return;
    }

    const confirmLines = [
      '메신저 단체톡 내용의 보낼 대기 줄을 학급 단톡방으로 보냅니다.',
      '',
      '단체 쪽지: ' + classGroup.lines.length + '줄'
    ];
    const confirm = ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기', confirmLines.join('\n'), ui.ButtonSet.OK_CANCEL);
    if (confirm !== ui.Button.OK) return;

    const result = sendTodayClassMessageQueue_(today);
    ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기를 마쳤습니다.\n\n처리한 줄: ' + result.rows + '줄');
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기 중 오류가 났습니다.\n\nGoogle Chat 발송 결과를 확인하지 못했어요. 다시 보내지 말고 contact@big-silver.xyz로 문의해 주세요.');
  }
}

// 메신저 개인톡 내용의 오늘 '대기' 줄만 학생별 개인톡으로 보낸다.
function sendTodayPersonalMessagesOnly() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const personalSheet = ensurePersonalMessageQueueSheet_(ss);
    const today = todayKey_();
    const personalRows = getQueueRows_(personalSheet, PERSONAL_MESSAGE_QUEUE_HEADERS.length);
    const personalGroups = groupPersonalMessageQueueRows_(personalRows, today);

    if (!personalGroups.length) {
      ui.alert('보낼 대기 개인 쪽지가 없습니다.');
      return;
    }

    const totalLines = personalGroups.reduce((sum, group) => sum + group.lines.length, 0);
    const confirmLines = [
      '메신저 개인톡 내용의 보낼 대기 줄을 학생별 개인톡으로 보냅니다.',
      '',
      '개인 쪽지: ' + personalGroups.length + '명 / ' + totalLines + '줄'
    ];
    const confirm = ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡 보내기', confirmLines.join('\n'), ui.ButtonSet.OK_CANCEL);
    if (confirm !== ui.Button.OK) return;

    const result = sendTodayPersonalMessageQueue_(today);
    if (result.chatApiSetupBlocked) {
      showChatApiSetupRequired_(ui);
      return;
    }

    const done = [
      '메신저 쪽지 내용 Google Chat으로 개인톡 보내기를 마쳤습니다.',
      '',
      personalSendResultSummary_(result)
    ];
    if (result.failures.length) done.push('', '확인 필요:', ...result.failures.slice(0, 10));
    ui.alert(done.join('\n'));
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡 보내기 중 오류가 났습니다.\n\nGoogle Chat 발송 결과를 확인하지 못했어요. 다시 보내지 말고 contact@big-silver.xyz로 문의해 주세요.');
  }
}

function sendTodayDismissalMessages() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const personalSheet = ensurePersonalMessageQueueSheet_(ss);
    const classSheet = ensureClassMessageQueueSheet_(ss);
    const today = todayKey_();
    const personalRows = getQueueRows_(personalSheet, PERSONAL_MESSAGE_QUEUE_HEADERS.length);
    const classRows = getQueueRows_(classSheet, CLASS_MESSAGE_QUEUE_HEADERS.length);
    const personalGroups = groupPersonalMessageQueueRows_(personalRows, today);
    const classGroup = groupClassMessageQueueRows_(classRows, today);

    if (!personalGroups.length && !classGroup.lines.length) {
      ui.alert('보낼 대기 쪽지가 없습니다.');
      return;
    }

    const confirmLines = [
      '오늘 종례 쪽지를 보냅니다.',
      '',
      '단체 쪽지: ' + classGroup.lines.length + '줄',
      '개인 쪽지: ' + personalGroups.length + '명 / ' + personalGroups.reduce((sum, group) => sum + group.lines.length, 0) + '줄'
    ];
    const confirm = ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기', confirmLines.join('\n'), ui.ButtonSet.OK_CANCEL);
    if (confirm !== ui.Button.OK) return;

    SpreadsheetApp.getActive().toast('Google Chat 연결 상태를 확인하는 중입니다.', '진행 중', 3);
    const status = callCentralChatSender_('/v1/status', {});
    if (!status.connected) {
      showChatApiSetupRequired_(ui);
      return;
    }

    let classResult = { sent: 0, skipped: 0, failed: 0, sentButRecordUnconfirmed: 0, rows: 0, error: '' };
    let personalResult = { sent: 0, skipped: 0, failed: 0, sentButRecordUnconfirmed: 0, rows: 0, failures: [], chatApiSetupBlocked: false };
    SpreadsheetApp.getActive().toast('단체 쪽지를 보내는 중입니다.', '진행 중', 3);
    try {
      classResult = sendTodayClassMessageQueue_(today);
    } catch (err) {
      if (isCentralChatConnectionError_(err)) {
        showChatApiSetupRequired_(ui);
        return;
      }
      classResult = { sent: 0, skipped: 0, failed: 1, sentButRecordUnconfirmed: 0, rows: classGroup.rowNumbers.length, error: centralChatErrorMessage_(err) };
    }
    SpreadsheetApp.getActive().toast('개인 쪽지를 보내는 중입니다.', '진행 중', 3);
    try {
      personalResult = sendTodayPersonalMessageQueue_(today);
    } catch (err) {
      if (isCentralChatConnectionError_(err)) {
        showChatApiSetupRequired_(ui);
        return;
      }
      personalResult = { sent: 0, skipped: 0, failed: 1, sentButRecordUnconfirmed: 0, rows: 0, failures: [centralChatErrorMessage_(err)], chatApiSetupBlocked: false };
    }
    if (personalResult.chatApiSetupBlocked) {
      showChatApiSetupRequired_(ui);
      return;
    }
    SpreadsheetApp.getActive().toast('Google Chat 발송 결과를 정리하는 중입니다.', '진행 중', 3);

    const done = [
      '메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기를 마쳤습니다.',
      '',
      '단체: 보냄 ' + classResult.sent + '건 · 이미 보내서 건너뜀 ' + classResult.skipped + '건 · 보내지 못함 ' + classResult.failed + '건' +
        (classResult.sentButRecordUnconfirmed ? ' · 발송 결과를 확인할 항목 ' + classResult.sentButRecordUnconfirmed + '건' : ''),
      '개인: ' + personalSendResultSummary_(personalResult)
    ];
    if (classResult.error) done.push('', '단체 확인 필요:', classResult.error);
    if (personalResult.failures.length) done.push('', '확인 필요:', ...personalResult.failures.slice(0, 10));
    ui.alert(done.join('\n'));
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기 중 오류가 났습니다.\n\nGoogle Chat 발송 결과를 확인하지 못했어요. 다시 보내지 말고 contact@big-silver.xyz로 문의해 주세요.');
  }
}

function sendMessengerPersonalMessages() {
  requireGoeduTeacherAccount_();
  return sendTodayPersonalMessagesOnly();
}

function sendMessengerClassMessages() {
  requireGoeduTeacherAccount_();
  return sendTodayClassMessagesOnly();
}

function sendMessengerAllMessages() {
  requireGoeduTeacherAccount_();
  return sendTodayDismissalMessages();
}

function startCentralChatConnection(options) {
  const ui = SpreadsheetApp.getUi();
  // Chat approval starts only in Teacher Manager; the Sheet shows no competing link.
  const quiet = !!(options && options.quiet === true);
  try {
    requireGoeduTeacherAccount_();
    const message = (options && options.sheetSetupReady ? '시트 설정은 끝났습니다. ' : '') +
      'Teacher Manager의 [연결 → 출결 → Google Chat]에서 [연결하기]를 눌러 주세요. ' +
      '그 버튼으로 열린 Google 화면에서 권한을 허용한 뒤 Teacher Manager에서 학급 단톡방을 고르세요. ' +
      '이 시트 메뉴는 다시 누르지 않아도 됩니다.';
    if (!quiet) ui.alert('Google Chat 연결은 Teacher Manager에서 진행해 주세요', message, ui.ButtonSet.OK);
    return { ok: false, message: message };
  } catch (err) {
    const message = 'Google Chat 최초 발송 연결을 시작하지 못했습니다.\n\n' + centralChatErrorMessage_(err);
    if (!quiet) ui.alert(message);
    return { ok: false, message: message };
  }
}

function checkCentralChatStatus() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  let connectionCode = '';
  let connectionSettingNote = '';
  try {
    // Chat 연결 여부와 상관없이 지금 열린 출석부의 확인번호부터 보여 준다.
    connectionCode = attendanceConnectionCodeForSpreadsheetId_(
      SpreadsheetApp.getActiveSpreadsheet().getId()
    );
    if (!connectionCode) throw new Error('지금 열린 출석부의 연결 확인번호를 읽지 못했습니다.');
    try {
      // 복사된 Sheet에 원본 확인번호가 남아 있어도, 상태 확인을 누른 현재 Sheet의
      // 실제 ID로 설정 탭 복사용 값을 바로 고친다.
      setConfigValue_(ATTENDANCE_CONNECTION_CODE_SETTING, connectionCode);
    } catch (settingError) {
      connectionSettingNote = '\n\n설정 탭의 복사용 번호는 갱신하지 못했습니다. 이 창의 번호를 사용해 주세요.';
    }
    const status = callCentralChatSender_('/v1/status', {});
    if (!status.connected) {
      ui.alert(
        '연결 확인번호: ' + connectionCode + '\n\n' +
        (status.reason || 'Google Chat 연결이 아직 끝나지 않았습니다.\n\nTeacher Manager의 [Google 연결 → 출결]에서 [연결하기]를 눌러 주세요.') +
        connectionSettingNote
      );
      return;
    }
    ui.alert(
      'Google Chat 발송 연결됨\n\n' +
      '연결 확인번호: ' + connectionCode + '\n' +
      '연결 계정: ' + (status.account || '') + '\n' +
      '발송 방식: 선생님 이름으로 발송\n' +
      '개인톡: ' + (status.personalEnabled ? '가능' : '확인 필요') + '\n' +
      '단체톡: ' + (status.classEnabled ? '가능' : '학급 단톡방 고르기 필요') +
      connectionSettingNote
    );
  } catch (err) {
    ui.alert(
      (connectionCode ? '연결 확인번호: ' + connectionCode + '\n\n' : '') +
      'Google Chat 연결 상태를 확인하지 못했습니다.\n\n' + centralChatErrorMessage_(err) +
      connectionSettingNote
    );
  }
}

function disconnectCentralChatSender() {
  const ui = SpreadsheetApp.getUi();
  const answer = ui.alert(
    'Google Chat 발송 연결 끊기',
    'Google Chat 발송 연결을 끊을까요?\n\n연결을 끊으면 이 시트에서는 더 이상 선생님 이름으로 Google Chat 쪽지를 보낼 수 없습니다.\n쪽지 내용은 삭제되지 않습니다.',
    ui.ButtonSet.OK_CANCEL
  );
  if (answer !== ui.Button.OK) return;
  try {
    callCentralChatSender_('/v1/disconnect', {});
    ui.alert('Google Chat 발송 연결을 끊었습니다.');
  } catch (err) {
    ui.alert('Google Chat 발송 연결을 끊지 못했어요.\n\n' + centralChatErrorMessage_(err));
  }
}

/*************************************************
 * 공통 안전 유틸
 *************************************************/
function escapeRegex_(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getSelectedDataRows_(sheet) {
  const rangeList = sheet.getActiveRangeList();
  const ranges = rangeList ? rangeList.getRanges()
                           : (sheet.getActiveRange() ? [sheet.getActiveRange()] : []);
  const rows = new Set();
  ranges.forEach(r => {
    const startRow = Math.max(MONTHLY_ATTENDANCE_DATA_START_ROW, r.getRow());
    for (let rowIdx = startRow; rowIdx <= r.getLastRow(); rowIdx++) rows.add(rowIdx);
  });
  return Array.from(rows).sort((a, b) => a - b);
}

function shouldSkipSheet_(sheet) {
  if (!sheet) return true;
  const name = sheet.getName();
  if (name === getHolidaySheetName_() || name === CONFIG_SHEET_NAME || name === '학생명단' || name === MESSENGER_PERSONAL_SHEET_NAME || name === MESSENGER_CLASS_SHEET_NAME || LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES.indexOf(name) !== -1 || LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES.indexOf(name) !== -1 || name === '드롭다운' || name === '템플릿_치환표' || name === '00_사용법' || name === (getConfig_().CHAT_LOG_SHEET_NAME || '발송기록')) return true;
  return !isInputMonthSheet_(sheet);
}



/*************************************************
 * 선택 행(들) → 신고서 생성 (다중 범위 지원)
 * 시트 컬럼(A~F): 날짜 / 번호+이름 / 구분 / 종류 / 사유 / (지각·조퇴·결과 교시)
 *************************************************/
function createDocFromTemplate() {
  requireGoeduTeacherAccount_();
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

    const selectedRows = getSelectedDataRows_(sheet);
    if (selectedRows.length === 0) {
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 3행 이후의 대상 행(들)을 선택한 후 실행하세요.');
      return;
    }

    const result = createDocsFromRows_(sheet, selectedRows);

    const msg = [`문서 생성: ${result.created}건`];
    if (result.fails.length) msg.push('', '실패:', ...result.fails.slice(0, 10), result.fails.length > 10 ? `...외 ${result.fails.length - 10}건` : '');
    SpreadsheetApp.getUi().alert(msg.join('\n'));
  } catch (err) {
    SpreadsheetApp.getActive().toast('신고서를 만들지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부를 다시 확인해 주세요.', '오류', 6);
  }
}

function createDocFromRowForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
  authorizeAttendanceOperation_('automatic', '');
  const sheet = getSheetForAutomation_(sheetName);
  return createDocsFromRows_(sheet, [Number(rowIdx)]);
}

function createDocsFromRows_(sheet, selectedRows) {
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 신고서를 만들 수 있습니다.');
  }
  const dataRows = (selectedRows || [])
    .map(rowIdx => Number(rowIdx))
    .filter(rowIdx => Number.isInteger(rowIdx) && rowIdx >= MONTHLY_ATTENDANCE_DATA_START_ROW);
  if (!dataRows.length) return { created: 0, fails: [] };
  const ss = sheet.getParent ? sheet.getParent() : SpreadsheetApp.getActiveSpreadsheet();
  const holidaySet = loadHolidaySet_(ss);
  const absIndex = buildAbsenceIndex_(sheet); // 현재 월 시트 기준
  const issuedSpanKeys = new Set();           // 연속결석 묶음 중복 생성 방지

  let created = 0, fails = [];
  dataRows.forEach(rowIdx => {
    try {
      const ok = processRowToDoc_(sheet, Number(rowIdx), absIndex, holidaySet, issuedSpanKeys);
      if (ok) created++;
    } catch (e) {
      fails.push(`${rowIdx}행: ${e && e.message ? e.message : e}`);
    }
  });
  return { created, fails };
}

/*************************************************
 * 단일 행 처리 (생성 시 true, 스킵 시 false)
 *************************************************/
function processRowToDoc_(sheet, rowIdx, absIndex, holidaySet, issuedSpanKeys) {
  const lastCol = sheet.getLastColumn();
  const rowVals = sheet.getRange(rowIdx, 1, 1, lastCol).getValues()[0];

  // A) 날짜
  let date = toDate_(rowVals[0]);
  if (!date) throw new Error(rowIdx + '행 A열에 날짜를 입력해 주세요. 예: 2026-09-09');

  // B) 번호+이름
  const studentInfoRaw = String(rowVals[1] || '').trim().replace(/\s+/g, '');
  const m = studentInfoRaw.match(/^(\d{1,2})(.+)$/);
  if (!m) throw new Error(rowIdx + '행 B열의 목록에서 학생을 골라 주세요.');
  const studentNumber = m[1];
  const studentName   = m[2];

  // C~F
  const category = String(rowVals[2] || '').trim();  // (질병/미인정/기타/출석인정)
  const kind     = String(rowVals[3] || '').trim();  // (결석함/지각함/조퇴함/결과함)
  const reason   = String(rowVals[4] || '').trim();
  const periodRaw= String(rowVals[5] || '').trim();
  const periodNum = (() => { const x = periodRaw.match(/\d+/); return x ? parseInt(x[0], 10) : null; })();

  // === 라벨 정규화 ===
  const kindLabel =
    /결석/.test(kind) ? '결석' :
    /지각/.test(kind) ? '지각' :
    /조퇴/.test(kind) ? '조퇴' :
    /결과/.test(kind) ? '결과' : null;

  if (!kindLabel) throw new Error(rowIdx + '행 D열에서 결석·지각·조퇴·결과 중 하나를 골라 주세요.');
  if ((kindLabel === '지각' || kindLabel === '조퇴' || kindLabel === '결과') && !periodNum) {
    throw new Error(rowIdx + '행 F열에서 해당 교시를 골라 주세요.');
  }

  // === 기간/일수 계산 ===
  let startDate = date, endDate = date, daysCount = 1;

  if (kindLabel === '결석') {
    const { start, end, count } = findAbsenceSpanForRow_(sheet, rowIdx, studentInfoRaw, reason, absIndex, holidaySet);
    startDate = start; endDate = end; daysCount = count;

    // 같은 연속결석 묶음이면 스킵
    const spanKey = `${studentInfoRaw}|${reason}|${dkey_(startDate)}|${dkey_(endDate)}`;
    if (issuedSpanKeys.has(spanKey)) return false;
    issuedSpanKeys.add(spanKey);
  }

  // 확인일(상단용) = 종료 다음 수업일
  const confirmDate = nextSchoolDay_(endDate, holidaySet);

  // === 문서 생성 ===
  const fileDate = startDate; // 파일명은 시작일 기준
  const y  = fileDate.getFullYear();
  const mm = ('0' + (fileDate.getMonth() + 1)).slice(-2);
  const dd = ('0' + fileDate.getDate()).slice(-2);

  const templateFile = DriveApp.getFileById(getTemplateDocId_());
  const newFileName  = `[${kind || '유형미기재'}] ${studentInfoRaw}_${y}-${mm}-${dd}`;

  const destFolder = getDestinationFolder_();

  const newDocFile = templateFile.makeCopy(newFileName, destFolder);
  const doc  = DocumentApp.openById(newDocFile.getId());
  const body = doc.getBody();

  // === 기본 치환 ===
  replaceAll_(body, '{{반번호}}', `${getClassLabel_()} ${studentNumber}번`);
  replaceAll_(body, '{{번호}}', String(studentNumber));
  replaceOptionalConfigPlaceholders_(body);
  replaceAll_(body, '{{성명}}', studentName);
  replaceAll_(body, '{{사유}}', reason);
  replaceAll_(body, '{{확인내용}}', reason);

  // === 표시/서식 ===
  const alsoAttendance = /출석인정/.test(category);
  applyBoldMarks_(body, kindLabel, alsoAttendance); // '결석/지각/조퇴/결과/출석인정' Bold

  // === 연도 치환(컨텍스트 기반) ===
  replaceYearsContextual_(body, startDate, endDate, confirmDate);

  // === 날짜 치환(맥락 기반) ===
  replaceDatesContextual_(body, kindLabel, startDate, endDate); // ※ 확인일자 행도 '시작일'로 채움

  // === 교시 치환(맥락 기반) ===
  replacePeriodsContextual_(body, kindLabel, periodNum);

  // === 일수 처리 ===
  if (kindLabel === '결석') fillDaysCount_(body, daysCount);
  else clearDaysCount_(body); // 지각/조퇴/결과는 비움

  // === 상단(이름 위) 날짜가 있다면 확인일로 ===
  replaceAll_(body, '{{확인월}}', String(confirmDate.getMonth() + 1));
  replaceAll_(body, '{{확인일}}', String(confirmDate.getDate()));
  fillBlankYMD_(body, confirmDate); // "YYYY년  월  일" 패턴도 채움

  doc.saveAndClose();
  return true;
}

/*************************************************
 * 선택 범위(연속·비연속) → Google Tasks 다건 추가
 * - G열=‘미제출’ → "결석신고서 미제출 확인 필요"
 * - H열=‘미제출’ → "첨부서류 미제출 확인 필요"
 * - 제목에 A열 날짜(yyyy-MM-dd) 포함 → 행마다 고유
 * - 기존 Tasks 중복 체크: 완료(completed)는 제외, 페이지네이션 처리
 *************************************************/
function addSelectedRowToTasks() {
  requireGoeduTeacherAccount_();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getActiveSheet();
    const sheetName = sheet.getName();

    // 가드: 월별 입력 시트에서만 실행
    if (!isInputMonthSheet_(sheet)) {
      SpreadsheetApp.getUi().alert('월별 입력 시트에서만 Tasks를 추가합니다.\n현재 시트: ' + sheetName);
      return;
    }

    if (typeof Tasks === 'undefined') {
      SpreadsheetApp.getUi().alert(
        '출석부의 할 일 등록 기능이 준비되지 않았어요.\n\n' +
        'Teacher Manager의 [Google 연결 → 출결]에서 출결 기능 상태를 확인해 주세요.'
      );
      return;
    }

    // 선택 범위(연속·비연속 모두) — 행 번호는 중복 제거
    const selectedRows = getSelectedDataRows_(sheet);
    if (!selectedRows.length) {
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 3행 이후의 대상 행(들)을 선택한 후 실행하세요.');
      return;
    }

    const result = addRowsToTasks_(sheet, selectedRows);

    SpreadsheetApp.getUi().alert(taskCreationResultMessage_(result));
  } catch (err) {
    SpreadsheetApp.getActive().toast('출결 미제출 할 일을 추가하지 못했어요. Teacher Manager의 [Google 연결 → 출결]에서 할 일 목록을 확인해 주세요.', '오류', 6);
  }
}

function addRowToTasksForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
  authorizeAttendanceOperation_('automatic', '');
  const sheet = getSheetForAutomation_(sheetName);
  return addRowsToTasks_(sheet, [Number(rowIdx)]);
}

function addRowsToTasks_(sheet, selectedRows) {
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 Tasks를 추가합니다.');
  }
  const dataRows = (selectedRows || [])
    .map(rowIdx => Number(rowIdx))
    .filter(rowIdx => Number.isInteger(rowIdx) && rowIdx >= MONTHLY_ATTENDANCE_DATA_START_ROW);
  if (!dataRows.length) return { created: 0, requested: 0, skippedExisting: 0, titles: [] };
  const taskListId = getTaskListId_();

  // 기존 미완료 Task 제목들 수집 (페이지네이션)
  const existingTitles = new Set();
  let pageToken = null;
  do {
    const resp = Tasks.Tasks.list(taskListId, {
      showCompleted: true,   // 받아오긴 하되…
      showHidden: true,
      maxResults: 100,
      pageToken
    });
    const items = (resp && resp.items) || [];
    items.forEach(t => {
      if (t.status !== 'completed') { // ✅ 완료된 건 중복판정에서 제외
        existingTitles.add(t.title);
      }
    });
    pageToken = resp && resp.nextPageToken ? resp.nextPageToken : null;
  } while (pageToken);

  // 이번 선택에서 만들 제목들(중복 제거)
  const titlesToCreate = new Set();
  const lastCol = sheet.getLastColumn();

  for (const rowIdx of dataRows) {
    const row = sheet.getRange(rowIdx, 1, 1, lastCol).getValues()[0];

    // A열 날짜 태그(없으면 행번호로 대체)
    const date = toDate_(row[0]);
    const dateTag = date
      ? Utilities.formatDate(date, Session.getScriptTimeZone() || 'Asia/Seoul', 'yyyy-MM-dd')
      : `R${rowIdx}`;

    const studentName = String(row[1] || '').trim(); // B열(번호+이름/이름)
    if (!studentName) continue;

    const gStatus = String(row[6] || '').trim(); // G열
    const hStatus = String(row[7] || '').trim(); // H열

    const base = `${studentName} (${sheet.getName() || ''} ${dateTag})`;
    if (gStatus === '미제출') {
      titlesToCreate.add(`${base} 결석신고서 미제출 확인 필요`);
    }
    if (hStatus === '미제출') {
      titlesToCreate.add(`${base} 첨부서류 미제출 확인 필요`);
    }
  }

  // 실제 생성 (기존 미완료 제목과만 비교)
  let created = 0;
  for (const title of titlesToCreate) {
    if (!existingTitles.has(title)) {
      Tasks.Tasks.insert({ title }, taskListId);
      created++;
    }
  }

  return {
    created,
    requested: titlesToCreate.size,
    skippedExisting: titlesToCreate.size - created,
    titles: Array.from(titlesToCreate)
  };
}

function taskCreationResultMessage_(result) {
  const created = Number(result && result.created || 0);
  const requested = Number(result && result.requested || 0);
  const skippedExisting = Number(result && result.skippedExisting || 0);
  if (!requested) return '선택한 행에 ‘미제출’ 서류가 없어요.';
  if (!created) return '이미 등록된 할 일 ' + skippedExisting + '건은 다시 만들지 않았어요.';
  let message = '할 일 ' + created + '건을 추가했어요.';
  if (skippedExisting) message += '\n이미 등록된 할 일 ' + skippedExisting + '건은 다시 만들지 않았어요.';
  return message;
}

/*************************************************
 * 선택 행(들) → 월별 시트 결과 열에 기록하며 즉시 개인톡 발송
 * - 메신저 개인톡 내용/단체톡 내용 시트는 건드리지 않는다.
 * - 이미 월별 결과가 '보냄'인 행은 다시 보내지 않는다.
 *************************************************/
function sendSelectedRowsChatNow() {
  requireGoeduTeacherAccount_();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getActiveSheet();
    const sheetName = sheet.getName();

    if (!isInputMonthSheet_(sheet)) {
      SpreadsheetApp.getUi().alert('월별 입력 시트에서만 개인톡을 보냅니다.\n현재 시트: ' + sheetName);
      return;
    }

    const selectedRows = getSelectedDataRows_(sheet);
    if (!selectedRows.length) {
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 3행 이후의 대상 행(들)을 선택한 후 실행하세요.');
      return;
    }

    // 발송 전 확인 — 회수할 수 없는 개인톡이 오클릭 한 번으로 대량 발송되면 안 된다.
    // 메신저 발송 세 경로와 같은 관문(인원수 포함 OK_CANCEL)이다.
    const ui = SpreadsheetApp.getUi();
    const previewGroups = buildAttendanceChatGroupsForSelectedRows_(sheet, selectedRows);
    if (!previewGroups.length) {
      ui.alert(
        '보낼 개인톡이 없습니다.\n- G열(신고서)/H열(첨부)이 "미제출"인지 확인\n- 이미 발송된 내용이면 다시 보내지 않습니다.'
      );
      return;
    }
    const confirm = ui.alert(
      '선택 행 미제출 서류 Google Chat 개인톡 보내기',
      '선택한 행에서 미제출 학생 ' + previewGroups.length + '명에게 개인톡을 보냅니다.\n\n' +
        '이미 발송된 행은 다시 보내지 않습니다.',
      ui.ButtonSet.OK_CANCEL
    );
    if (confirm !== ui.Button.OK) return;

    const result = sendSelectedRowsPersonalMessagesNow_(sheet, selectedRows);

    if (result.chatApiSetupBlocked) {
      showChatApiSetupRequired_(SpreadsheetApp.getUi());
      return;
    }

    if (!result.sent && !result.failed && !result.skipped) {
      SpreadsheetApp.getUi().alert(
        '보낼 개인톡이 없습니다.\n- G열(신고서)/H열(첨부)이 "미제출"인지 확인\n- 이미 발송된 내용이면 다시 보내지 않습니다.'
      );
    } else {
      SpreadsheetApp.getActive().toast('개인톡 ' + personalSendResultSummary_(result), '완료', 5);
    }
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(SpreadsheetApp.getUi());
      return;
    }
    SpreadsheetApp.getActive().toast('개인톡을 보내지 못했어요. ' + centralChatErrorMessage_(err), '오류', 6);
  }
}

function sendSelectedRowChatForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
  authorizeAttendanceOperation_('automatic', '');
  const sheet = getSheetForAutomation_(sheetName);
  return sendSelectedRowsPersonalMessagesNow_(sheet, [Number(rowIdx)]);
}

function claimAttendanceChatRow_(sheet, group, rowGuard) {
  // 큐 발송(claimPersonalQueueRows_)과 같은 잠금 + '발송중' 선점 —
  // 더블클릭·두 탭 동시 실행 방어를 서버 requestId dedup 하나에만 맡기지 않는다.
  // 크래시로 남은 '발송중'은 시도시각 기준 10분 뒤 다시 집어갈 수 있다.
  return withDocumentLock_(() => {
    const cols = ensureMonthlyChatResultColumns_(sheet);
    if (!cols) return false;
    // rowGuard는 대시보드만 넘긴다. 그 줄이 선생님이 확인한 내용 그대로일 때만 잡는다.
    if (typeof rowGuard === 'function' && !rowGuard(group.rowIdx)) return false;
    const rowValues = sheet.getRange(group.rowIdx, cols.statusCol, 1, 4).getValues()[0];
    const status = String(rowValues[0] || '').trim();
    const savedSignature = String(rowValues[3] || '').trim();
    if (status === '보냄' && savedSignature === group.signature) return false;
    if (status === '발송중' && !queueClaimIsStale_(rowValues[1], new Date())) return false;
    setMonthlyChatResult_(sheet, group.rowIdx, '발송중', '', group.signature);
    return true;
  });
}

// options.rowGuard(rowIdx)는 대시보드 독려 보내기만 넘긴다. 잠금 안에서 그 줄이 선생님이
// 확인한 내용 그대로일 때만 기록·발송한다(정렬로 줄이 옮겨졌으면 건너뛴다).
function sendSelectedRowsPersonalMessagesNow_(sheet, selectedRows, options) {
  const rowGuard = options && typeof options.rowGuard === 'function' ? options.rowGuard : null;
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 개인톡을 보냅니다.');
  }
  const result = {
    sent: 0,
    skipped: 0,
    failed: 0,
    sentButRecordUnconfirmed: 0,
    rows: 0,
    failures: [],
    chatApiSetupBlocked: false
  };
  const dataRows = (selectedRows || [])
    .map(rowIdx => Number(rowIdx))
    .filter(rowIdx => Number.isInteger(rowIdx) && rowIdx >= MONTHLY_ATTENDANCE_DATA_START_ROW);
  if (!dataRows.length) return result;
  if (!ensureMonthlyChatResultColumns_(sheet)) return result;
  const groups = buildAttendanceChatGroupsForSelectedRows_(sheet, dataRows);
  if (!groups.length) return result;

  const rosterMap = loadStudentRosterForDm_();
  for (const group of groups) {
    const student = findQueueStudent_(group, rosterMap);
    const text = group.name
      ? group.name + ' 학생, 확인할 내용입니다.\n\n- ' + group.lines.join('\n- ')
      : group.lines.join('\n');
    if (rowGuard && !withDocumentLock_(() => rowGuard(group.rowIdx))) {
      result.skipped++;
      result.rows++;
      continue;
    }
    if (!group.email) {
      const error = '학생 Google 이메일 없음';
      setMonthlyChatResult_(sheet, group.rowIdx, '실패', error);
      appendChatLog_('출결 개인톡', group.target || group.name, '', text, '건너뜀', error);
      result.failed++;
      result.rows++;
      result.failures.push((group.target || group.name || group.rowIdx) + ': ' + error);
      continue;
    }

    if (!claimAttendanceChatRow_(sheet, group, rowGuard)) {
      // 다른 실행이 이미 이 행을 보내는 중이거나 방금 보냈다 — 건너뛴다.
      result.skipped++;
      result.rows++;
      continue;
    }

    const requestId = attendanceChatRequestId_(
      centralSheetIdentityForRequest_(),
      group.email,
      group.signature,
      text
    );
    let sendResult;
    try {
      sendResult = sendCentralPersonalChat_(group.email, text, {
        requestId: requestId,
        source: '출결',
        sheetName: group.sheetName,
        rowIdx: group.rowIdx
      });
    } catch (err) {
      const error = centralChatErrorMessage_(err);
      const status = isCentralChatConnectionError_(err) ? '연결필요' : '실패';
      setMonthlyChatResult_(sheet, group.rowIdx, status, error);
      appendChatLog_('출결 개인톡', group.target || group.email, '', text, status, error);
      result.failed++;
      result.rows++;
      result.failures.push((group.target || group.email || group.rowIdx) + ': ' + error);
      if (status === '연결필요') {
        result.chatApiSetupBlocked = true;
        return result;
      }
      continue;
    }

    const messageCount = Number(sendResult.messageCount || 1);
    const success = '성공 ' + messageCount + '건';
    try {
      setMonthlyChatResult_(sheet, group.rowIdx, '보냄', success, group.signature);
      const logWarning = safeAppendChatLog_(['출결 개인톡', group.target || group.email, sendResult.spaceId || '', text, success, '']);
      if (logWarning) {
        setMonthlyChatResult_(sheet, group.rowIdx, '보냄', success + '\n' + logWarning, group.signature);
        result.sentButRecordUnconfirmed++;
        result.failures.push((group.target || group.email || group.rowIdx) + ': ' + logWarning);
      }
    } catch (err) {
      const warning = '메시지는 보냈어요. 다시 보내지 말고 발송 결과를 확인해 주세요.';
      safeAppendChatLog_(['출결 개인톡', group.target || group.email, sendResult.spaceId || '', text, '기록주의', warning]);
      result.failures.push((group.target || group.email || group.rowIdx) + ': ' + warning);
      result.sentButRecordUnconfirmed++;
    }
    result.sent++;
    result.rows++;
  }
  return result;
}

function getSheetForAutomation_(sheetName) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheets = attendanceMonthSheetsFor_(ss, readAttendanceConfigStrict_(ss));
  const matches = sheets.filter(item => Number.isSafeInteger(sheetName)
    ? item.sheet.getSheetId() === sheetName : item.sheet.getName() === String(sheetName || '').trim());
  if (matches.length !== 1) throw new Error('연결된 월별 출결표를 확인하지 못했습니다. 다른 탭으로 대신 처리하지 않았습니다.');
  return matches[0].sheet;
}

/*************************************************
 * ===== 휴일/연속결석 유틸 =====
 *************************************************/
function toDate_(v) {
  if (v instanceof Date && !isNaN(v.getTime())) {
    return new Date(v.getFullYear(), v.getMonth(), v.getDate());
  }
  if (!v) return null;
  const s = String(v).trim();
  // 숫자만 추출해서 Y,M,D로 파싱 (예: 2025.10.02, 2025/10/2, 10/2/2025 등)
  const nums = s.match(/\d+/g);
  if (!nums || nums.length < 3) {
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }
  // 휴리스틱: 4자리 포함이면 보통 YYYY, 아니면 MM/DD/YYYY 순으로 가정
  let Y, M, D;
  if (nums[0].length === 4) { Y = +nums[0]; M = +nums[1]; D = +nums[2]; }
  else if (nums[2] && nums[2].length === 4) { Y = +nums[2]; M = +nums[0]; D = +nums[1]; }
  else { const d = new Date(s); return isNaN(d.getTime()) ? null : new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  const d2 = new Date(Y, M-1, D);
  if (isNaN(d2.getTime())) return null;
  // 2025-02-31 같은 값이 3월 3일로 보정되는 것을 방지
  if (d2.getFullYear() !== Y || d2.getMonth() !== M - 1 || d2.getDate() !== D) return null;
  return d2;
}

function dkey_(d) {
  return Utilities.formatDate(
    new Date(d.getFullYear(), d.getMonth(), d.getDate()),
    Session.getScriptTimeZone() || 'Asia/Seoul',
    'yyyy-MM-dd'
  );
}

function loadHolidaySet_(ss) {
  const set = new Set();
  const sh = ss.getSheetByName(getHolidaySheetName_());
  if (!sh) return set;

  const lastRow = sh.getLastRow();
  if (lastRow < 2) return set;

  const vals = sh.getRange(2, 1, lastRow - 1, 1).getValues().flat();
  vals.forEach(v => {
    const d = toDate_(v);
    if (d) set.add(dkey_(d));
  });
  return set;
}

function isWeekend_(d){ const w=d.getDay(); return w===0 || w===6; }
function isHoliday_(d, holidays){ return holidays.has(dkey_(d)); }
function isSchoolDay_(d, holidays){ return !isWeekend_(d) && !isHoliday_(d, holidays); }

function nextSchoolDay_(d, holidays){
  let cur = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  do { cur = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate()+1); } while (!isSchoolDay_(cur, holidays));
  return cur;
}
function prevSchoolDay_(d, holidays){
  let cur = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  do { cur = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate()-1); } while (!isSchoolDay_(cur, holidays));
  return cur;
}

// 현재 시트(한 달)에서 같은 학생+사유의 '결석' 날짜 집합
function buildAbsenceIndex_(sheet){
  const lastCol = sheet.getLastColumn();
  const vals = sheet.getRange(1,1, sheet.getLastRow(), lastCol).getValues();
  const map = new Map(); // key = "번호이름|사유" -> Set(yyyy-mm-dd)
  for (let r=1; r<=vals.length; r++){
    const row = vals[r-1];
    const dt  = toDate_(row[0]);
    const whoRaw = String(row[1] || '').trim().replace(/\s+/g,'');
    const kind = String(row[3] || '').trim();
    const reason = String(row[4] || '').trim();
    if (!dt || !/결석/.test(kind) || !whoRaw) continue;
    const key = `${whoRaw}|${reason}`;
    const set = map.get(key) || new Set();
    set.add(dkey_(dt));
    map.set(key, set);
  }
  return { map };
}

// 현재 행 기준 연속 결석 구간(연속 '수업일') 찾기
function findAbsenceSpanForRow_(sheet, rowIdx, whoRaw, reason, absIndex, holidays){
  const row = sheet.getRange(rowIdx, 1, 1, sheet.getLastColumn()).getValues()[0];
  const date = toDate_(row[0]);
  const key  = `${whoRaw}|${reason}`;
  const set  = absIndex.map.get(key) || new Set();

  let start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  while (true){
    const prev = prevSchoolDay_(start, holidays);
    if (dkey_(prev) === dkey_(start)) break;
    if (set.has(dkey_(prev))) start = prev; else break;
  }

  let end = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  while (true){
    const next = nextSchoolDay_(end, holidays);
    if (dkey_(next) === dkey_(end)) break;
    if (set.has(dkey_(next))) end = next; else break;
  }

  // 실제 결석 '수업일' 수(휴일은 기간엔 포함돼도 일수에선 제외)
  let count = 0, cur = new Date(start.getTime());
  while (cur <= end){
    if (set.has(dkey_(cur))) count++;
    cur = nextSchoolDay_(cur, holidays);
  }
  if (count < 1) count = 1;

  return { start, end, count };
}

/*************************************************
 * ===== 날짜/교시 치환(컨텍스트) =====
 *************************************************/
// {{연도}} occurrence 마다, 같은 행/문단의 날짜 placeholder를 보고 값 결정
function replaceYearsContextual_(body, startDate, endDate, confirmDate){
  const placeholder = '{{연도}}';
  const yStart = String(startDate.getFullYear());
  const yEnd = String(endDate.getFullYear());
  const yConfirm = String(confirmDate.getFullYear());

  const containers = collectContainers_(body);
  containers.forEach(container => {
    const elements = collectTextElements_(container);
    if (!elements.length) return;

    const parts = elements.map(el => el.getText());
    const text = parts.join('');
    if (text.indexOf(placeholder) === -1) return;

    const occurrences = [];
    let idx = 0;
    while ((idx = text.indexOf(placeholder, idx)) !== -1) {
      occurrences.push(idx);
      idx += placeholder.length;
    }

    const hasStart = text.includes('{{시작월}}') || text.includes('{{시작일}}');
    const hasEnd = text.includes('{{종료월}}') || text.includes('{{종료일}}');
    const hasConfirm = text.includes('{{확인월}}') || text.includes('{{확인일}}');

    let years;
    if (hasConfirm && !hasStart && !hasEnd) {
      years = occurrences.map(() => yConfirm);
    } else if (hasStart && hasEnd) {
      if (occurrences.length <= 1) {
        years = occurrences.map(() => yStart);
      } else {
        years = occurrences.map((_, i) => (i === 0 ? yStart : yEnd));
      }
    } else if (hasStart) {
      years = occurrences.map(() => yStart);
    } else if (hasEnd) {
      years = occurrences.map(() => yEnd);
    } else if (hasConfirm) {
      years = occurrences.map(() => yConfirm);
    } else {
      years = occurrences.map(() => yStart);
    }

    const positions = [];
    let cursor = 0;
    for (let i = 0; i < parts.length; i++) {
      positions[i] = cursor;
      cursor += parts[i].length;
    }

    for (let i = occurrences.length - 1; i >= 0; i--) {
      const startIndex = occurrences[i];
      const endIndex = startIndex + placeholder.length - 1;
      const yearVal = years[i] || yStart;
      replaceAcrossElements_(elements, positions, startIndex, endIndex, yearVal);
    }
  });
}

function collectContainers_(container){
  const out = [];
  const seen = new Set();
  const walk = (el) => {
    if (!el) return;
    const type = el.getType ? el.getType() : null;
    if (type === DocumentApp.ElementType.TABLE_ROW) {
      if (!seen.has(el)) { out.push(el.asTableRow()); seen.add(el); }
      return;
    }
    if (type === DocumentApp.ElementType.PARAGRAPH || type === DocumentApp.ElementType.LIST_ITEM) {
      if (!seen.has(el)) { out.push(el); seen.add(el); }
      return;
    }
    if (el.getNumChildren && el.getNumChildren() > 0) {
      for (let i = 0; i < el.getNumChildren(); i++) walk(el.getChild(i));
    }
  };
  walk(container);
  return out;
}

function collectTextElements_(container){
  const out = [];
  const walk = (el) => {
    if (!el) return;
    const type = el.getType ? el.getType() : null;
    if (type === DocumentApp.ElementType.TEXT) { out.push(el.asText()); return; }
    if (el.getNumChildren && el.getNumChildren() > 0) {
      for (let i = 0; i < el.getNumChildren(); i++) walk(el.getChild(i));
    }
  };
  walk(container);
  return out;
}

function findElementAtIndex_(elements, positions, index){
  for (let i = 0; i < elements.length; i++) {
    const text = elements[i].getText();
    if (!text) continue;
    const start = positions[i];
    const end = start + text.length - 1;
    if (index >= start && index <= end) {
      return { el: elements[i], idx: i, offset: index - start };
    }
  }
  return null;
}

function replaceAcrossElements_(elements, positions, startIndex, endIndex, replacement){
  const startInfo = findElementAtIndex_(elements, positions, startIndex);
  const endInfo = findElementAtIndex_(elements, positions, endIndex);
  if (!startInfo || !endInfo) return;

  if (startInfo.idx === endInfo.idx) {
    startInfo.el.deleteText(startInfo.offset, endInfo.offset);
    startInfo.el.insertText(startInfo.offset, replacement);
    return;
  }

  const startEl = startInfo.el;
  const startText = startEl.getText();
  if (startText.length > 0 && startInfo.offset <= startText.length - 1) {
    startEl.deleteText(startInfo.offset, startText.length - 1);
  }

  const endEl = endInfo.el;
  const endText = endEl.getText();
  if (endText.length > 0) {
    const endOffset = Math.min(endInfo.offset, endText.length - 1);
    if (endOffset >= 0) endEl.deleteText(0, endOffset);
  }

  for (let i = startInfo.idx + 1; i < endInfo.idx; i++) {
    const mid = elements[i];
    const t = mid.getText();
    if (t.length > 0) mid.deleteText(0, t.length - 1);
  }

  startEl.insertText(startInfo.offset, replacement);
}

// {{시작월/시작일/종료월/종료일}} occurrence 마다, 들어있는 표 행의 라벨을 보고 값 결정
function replaceDatesContextual_(body, kindLabel, startDate, endDate){
  const mStart = String(startDate.getMonth()+1);
  const dStart = String(startDate.getDate());
  const mEnd   = String(endDate.getMonth()+1);
  const dEnd   = String(endDate.getDate());

  const PH = ['{{시작월}}','{{시작일}}','{{종료월}}','{{종료일}}'];
  PH.forEach(ph => {
    // 같은 Text 요소에 같은 placeholder가 여러 번 있어도 누락되지 않도록
    // 먼저 전부 찾고, 뒤에서부터 지웁니다.
    const hits = findAllOccurrences_(body, ph).reverse();
    hits.forEach(hit => {
      const row = ascendToRow_(hit.getElement());
      const rowText = row ? getRowText_(row) : '';

      let value = ''; // 기본 공란

      // 1) 확인일자 행도 실제로는 '당일(시작일)'을 써야 함
      if (rowText.includes('확인일자')) {
        if (ph === '{{시작월}}') value = mStart;
        else if (ph === '{{시작일}}') value = dStart;
        else value = '';
      }
      // 2) 결석 행
      else if (rowText.includes('결석')) {
        if (kindLabel === '결석') {
          if (ph === '{{시작월}}') value = mStart;
          else if (ph === '{{시작일}}') value = dStart;
          else if (ph === '{{종료월}}') value = mEnd;
          else if (ph === '{{종료일}}') value = dEnd;
        } else {
          value = '';
        }
      }
      // 3) 지각/조퇴/결과 행: 날짜는 당일 1개만 사용
      else if (rowText.includes('지각') || rowText.includes('조퇴') || rowText.includes('결과')) {
        if (kindLabel === '지각' || kindLabel === '조퇴' || kindLabel === '결과') {
          if (ph === '{{시작월}}') value = mStart;
          else if (ph === '{{시작일}}') value = dStart;
          else value = '';
        } else {
          value = '';
        }
      }
      // 4) 기타 위치는 공란(의도치 않은 자리 오염 방지)
      else {
        value = '';
      }

      const el = hit.getElement().asText();
      const s  = hit.getStartOffset();
      const e  = hit.getEndOffsetInclusive();
      el.deleteText(s, e);
      if (value !== '') el.insertText(s, value);
    });
  });
}

// {{시작교시}}/{{종료교시}} occurrence 마다, 해당 행 라벨을 보고 값 결정
function replacePeriodsContextual_(body, kindLabel, periodNum) {
  const periodText = periodNum ? `${periodNum}교시` : '';
  const PH = ['{{시작교시}}','{{종료교시}}'];
  PH.forEach(ph => {
    // 같은 Text 요소에 같은 placeholder가 여러 번 있어도 누락되지 않도록
    // 먼저 전부 찾고, 뒤에서부터 지웁니다.
    const hits = findAllOccurrences_(body, ph).reverse();
    hits.forEach(hit => {
      const row = ascendToRow_(hit.getElement());
      const rowText = row ? getRowText_(row) : '';

      let value = ''; // 기본 공란

      if (rowText.includes('지각') || rowText.includes('조퇴') || rowText.includes('결과')) {
        if (kindLabel === '지각') {
          value = (ph === '{{시작교시}}') ? '조회' : periodText;
        } else if (kindLabel === '조퇴') {
          value = (ph === '{{시작교시}}') ? periodText : '종례';
        } else if (kindLabel === '결과') {
          // 결과는 시작교시와 종료교시가 동일합니다.
          value = periodText;
        } else {
          value = '';
        }
      } else {
        value = ''; // 결석/기타 행은 공란
      }

      const el = hit.getElement().asText();
      const s  = hit.getStartOffset();
      const e  = hit.getEndOffsetInclusive();
      el.deleteText(s, e);
      if (value !== '') el.insertText(s, value);
    });
  });
}

/*************************************************
 * ===== 표시/치환 유틸 =====
 *************************************************/
// "( 일간 )" 또는 {{일수}}
function fillDaysCount_(body, days){
  replaceAll_(body, '{{일수}}', String(days));

  const re = '\\(\\s*일간\\s*\\)';
  const hits = findAllPatternOccurrences_(body, re).reverse();
  hits.forEach(hit => {
    const el = hit.getElement().asText();
    const s = hit.getStartOffset();
    const e = hit.getEndOffsetInclusive();
    el.deleteText(s, e);
    el.insertText(s, `(${days}일간)`);
  });
}
function clearDaysCount_(body){
  replaceAll_(body, '{{일수}}', '');

  const re = '\\(\\s*일간\\s*\\)';
  const hits = findAllPatternOccurrences_(body, re).reverse();
  hits.forEach(hit => {
    const el = hit.getElement().asText();
    const s = hit.getStartOffset();
    const e = hit.getEndOffsetInclusive();
    el.deleteText(s, e);
  });
}

// 문서 전체의 placeholder 모든 발생 위치 수집
function findAllOccurrences_(body, placeholder) {
  const hits = [];
  const pattern = escapeRegex_(placeholder);
  let rangeElement = null;
  while (rangeElement = body.findText(pattern, rangeElement)) hits.push(rangeElement);
  return hits;
}

function findAllPatternOccurrences_(body, pattern) {
  const hits = [];
  let rangeElement = null;
  while (rangeElement = body.findText(pattern, rangeElement)) hits.push(rangeElement);
  return hits;
}

// 문서 전체 동일 placeholder 치환
// 같은 Text 요소에 동일 placeholder가 여러 번 있어도 offset이 밀리지 않도록 뒤에서부터 치환
// insertText는 문단 맨 앞에서는 물려받을 앞 글자가 없어 문서 기본 서식으로 들어간다.
// 그래서 '{{학교명}}장 귀하'처럼 줄 맨 앞 placeholder는 치환 후 글자가 작아지므로,
// placeholder 자리의 서식을 먼저 읽어 삽입한 값에 그대로 다시 적용한다.
function replaceAll_(body, placeholder, value) {
  const hits = findAllOccurrences_(body, placeholder);
  hits.reverse().forEach(hit => {
    const el = hit.getElement().asText();
    const start = hit.getStartOffset();
    const end   = hit.getEndOffsetInclusive();
    const sourceAttrs = el.getAttributes(start) || {};
    el.deleteText(start, end);
    if (value === undefined || value === null || value === '') return;
    const text = String(value);
    el.insertText(start, text);
    const attrs = {};
    Object.keys(sourceAttrs).forEach(key => {
      if (sourceAttrs[key] !== null && sourceAttrs[key] !== undefined) attrs[key] = sourceAttrs[key];
    });
    if (Object.keys(attrs).length) el.setAttributes(start, start + text.length - 1, attrs);
  });
}

// "YYYY년  월  일" 빈 블록 채움(상단용).
// 문서 전체를 모두 채우면 결석/지각/조퇴/결과의 비대상 행까지 오염될 수 있어 첫 번째 안전 후보만 채움.
function fillBlankYMD_(body, date){
  const y = date.getFullYear();
  const m = date.getMonth()+1;
  const d = date.getDate();
  const re = '\\d{4}년\\s*월\\s*일';
  let hit = null;
  while (hit = body.findText(re, hit)) {
    const row = ascendToRow_(hit.getElement());
    const context = row ? getRowText_(row) : getParentText_(hit.getElement());
    // 출결 종류 선택 표의 빈 날짜칸은 건드리지 않음
    if (/결석|지각|조퇴|결과|확인일자/.test(context)) continue;

    const el = hit.getElement().asText();
    const s = hit.getStartOffset();
    const e = hit.getEndOffsetInclusive();
    el.deleteText(s, e);
    el.insertText(s, `${y}년 ${m}월 ${d}일`);
    return;
  }
}

function getParentText_(el) {
  let cur = el;
  while (cur && cur.getParent && cur.getType &&
         cur.getType() !== DocumentApp.ElementType.PARAGRAPH &&
         cur.getType() !== DocumentApp.ElementType.LIST_ITEM) {
    cur = cur.getParent();
  }
  try { return cur && cur.getText ? cur.getText() : ''; } catch(e) { return ''; }
}

// Bold 처리(경계 인식)
function tokenBoundarySpans_(full, label) {
  const spans = []; let from = 0;
  while (true) {
    const idx = full.indexOf(label, from);
    if (idx === -1) break;
    const preCh  = idx === 0 ? '' : full[idx - 1];
    const postIx = idx + label.length;
    const postCh = postIx >= full.length ? '' : full[postIx];
    const BOUND = /[\s,(){}\[\]~\-–—\/·•|:;ㆍ]/;
    const okBefore = idx === 0 || BOUND.test(preCh);
    const okAfter  = postIx === full.length || BOUND.test(postCh);
    if (okBefore && okAfter) spans.push([idx, idx + label.length - 1]);
    from = idx + label.length;
  }
  return spans;
}
function applyBoldMarks_(body, kindLabel, alsoAttendance) {
  const toBold = new Set();
  if (kindLabel) toBold.add(kindLabel);
  if (alsoAttendance) toBold.add('출석인정');

  const labels = ['출석인정', '결석', '지각', '조퇴', '결과'];

  traverseElements_(body, (textEl) => {
    const full = textEl.getText();
    if (!full) return;

    const spansMap = new Map();
    labels.forEach(lbl => {
      const spans = tokenBoundarySpans_(full, lbl);
      if (spans.length) spansMap.set(lbl, spans);
    });
    if (spansMap.size === 0) return;

    for (const spans of spansMap.values()) {
      for (const [s, e] of spans) safeSetBold_(textEl, s, e, false);
    }
    for (const lbl of toBold) {
      const spans = spansMap.get(lbl) || [];
      for (const [s, e] of spans) safeSetBold_(textEl, s, e, true);
    }
  });
}
function traverseElements_(container, onText) {
  const type = container.getType ? container.getType() : null;
  if (type === DocumentApp.ElementType.TEXT) { onText(container.asText()); return; }
  if (container.getNumChildren && container.getNumChildren() > 0) {
    for (let i=0;i<container.getNumChildren();i++) traverseElements_(container.getChild(i), onText);
  }
}
function safeSetBold_(textEl, start, end, flag) { try { textEl.setBold(start, end, flag); } catch(e) {} }

// 테이블 행 추적
function ascendToRow_(el){
  while (el && el.getParent && el.getType && el.getType() !== DocumentApp.ElementType.TABLE_ROW) {
    el = el.getParent();
  }
  try { return el ? el.asTableRow() : null; } catch(e) { return null; }
}
function getRowText_(row){
  let txt = '';
  const n = row.getNumCells ? row.getNumCells() : 0;
  for (let i=0;i<n;i++) txt += row.getCell(i).getText();
  return txt;
}
function onEdit(e) {
  try {
    if (!mayRunLocalSheetTrigger_(e)) return;
    if (!e || !e.range) return;
    const range = e.range;
    const sheet = range.getSheet();
    const startCol = range.getColumn();
    const endCol = range.getLastColumn();

    // 학생명단의 번호(A)/이름(B)을 고치면 드롭다운 시트의 숨은 목록만 갱신한다.
    const cfg = getConfig_();
    if (sheet.getName() === (cfg.ROSTER_SHEET_NAME || '학생명단')) {
      if (startCol <= 2 && endCol >= 1) {
        syncStudentDropdownValues_(e.source || SpreadsheetApp.getActiveSpreadsheet(), cfg);
      }
      return;
    }

    if (isInputMonthSheet_(sheet)) {
      const editsMessageFields = (startCol <= 2 && endCol >= 1) || (startCol <= 8 && endCol >= 7);
      if (editsMessageFields) {
        const startRow = Math.max(MONTHLY_ATTENDANCE_DATA_START_ROW, range.getRow());
        const endRow = range.getLastRow();
        if (endRow >= startRow) clearMonthlyChatResultForRows_(sheet, startRow, endRow);
      }
    }

    // A열 편집 때 줄무늬 규칙 범위만 확인한다(행 삽입으로 범위가 밀린 경우). 색은 칠하지 않는다.
    if (
      startCol <= 1
      && endCol >= 1
      && range.getLastRow() >= MONTHLY_ATTENDANCE_DATA_START_ROW
    ) {
      reStripeSheet_(sheet);
    }
  } catch (err) {
    console.log('onEdit error:', err);
  }
}
/** 월 탭의 날짜 줄무늬 조건부 서식을 확인한다. 행마다 색을 칠하지 않는다. */
function reStripeSheet_(sheet) {
  if (shouldSkipSheet_(sheet)) return;
  applyDateStripeRules_(sheet);
}

function dateStripeFormulaKey_(value) {
  return String(value || '').replace(/[\s$']/g, '').replace(/^=/, '').toLowerCase();
}

/** 우리가 넣는 날짜 줄무늬 규칙인지 본다. 예전 $A$3 모양 규칙도 같은 것으로 본다. */
function isDateStripeRule_(rule) {
  const condition = rule && typeof rule.getBooleanCondition === 'function'
    ? rule.getBooleanCondition() : null;
  if (!condition || condition.getCriteriaType() !== SpreadsheetApp.BooleanCriteria.CUSTOM_FORMULA) {
    return false;
  }
  const key = dateStripeFormulaKey_((condition.getCriteriaValues() || [])[0]);
  if (DATE_STRIPE_RULES.some(item => key === dateStripeFormulaKey_(item.formula))) return true;
  return key.indexOf('and(') === 0 && key.indexOf('mod(sumproduct((') >= 0
    && /,2\)=[01]\)$/.test(key);
}

// 정렬·행 삽입 뒤에도 Google 시트가 A열 날짜로 색을 다시 계산하게 조건부 서식 두 개를
// 맨 뒤에 둔다. 선생님이 만든 다른 규칙은 순서와 우선순위를 그대로 둔다. 이미 맞으면 쓰지 않는다.
function applyDateStripeRules_(sheet) {
  const numRows = sheet.getMaxRows() - MONTHLY_ATTENDANCE_DATA_START_ROW + 1;
  const endCol = Math.min(STRIPE_END_COL, sheet.getMaxColumns());
  if (numRows < 1 || endCol < 1) return false;
  const range = sheet.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 1, numRows, endCol);
  const wanted = range.getA1Notation();
  const rules = sheet.getConditionalFormatRules();
  const others = rules.filter(rule => !isDateStripeRule_(rule));
  const tail = rules.slice(others.length);
  const current = tail.length === DATE_STRIPE_RULES.length && tail.every((rule, index) => {
    const condition = rule.getBooleanCondition();
    const ranges = rule.getRanges();
    return isDateStripeRule_(rule)
      && dateStripeFormulaKey_(condition.getCriteriaValues()[0])
        === dateStripeFormulaKey_(DATE_STRIPE_RULES[index].formula)
      && String(condition.getBackground() || '').toLowerCase() === DATE_STRIPE_RULES[index].color
      && ranges.length === 1 && ranges[0].getA1Notation() === wanted;
  });
  if (current) return false;
  const ours = DATE_STRIPE_RULES.map(item => SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied(item.formula)
    .setBackground(item.color)
    .setRanges([range])
    .build());
  sheet.setConditionalFormatRules(others.concat(ours));
  return true;
}

/*************************************************
 * 출결·메신저 대시보드 (1단계: 읽기 + 출결 입력, 2단계: Google Chat 보내기)
 * 시트 메뉴 [출결 업무 자동화 → 출결·메신저 대시보드 열기]로 여는 대화상자다.
 * 화면 코드는 파일 끝의 TM_DASHBOARD_CSS_/TM_DASHBOARD_JS_ 문자열이다
 * (scripts/embed_dashboard.py가 Teacher Manager 원본의 sheet-dashboard 폴더에서 만든다).
 * 읽기는 잠그지 않는다. 쓰기는 문서 잠금 안에서 줄 지문을 다시 대조한 뒤에만 한다.
 * 학생 이메일·설정 비밀값·내부 ID·L열 값은 화면으로 보내지 않는다(SHEET-DASH-01~07).
 *************************************************/
const TM_DASHBOARD_TITLE = '출결·메신저 대시보드';
const TM_DASHBOARD_DOC_VALUES = Object.freeze(['', '제출', '미제출', '해당없음']);
const TM_DASHBOARD_REQUEST_PREFIX = 'tmdash-req-';
const TM_DASHBOARD_REASON_MAX = 200;
const TM_DASHBOARD_SENTENCE_MAX = 500;
const TM_DASHBOARD_QUEUE_RECENT_DAYS = 30;
const TM_DASHBOARD_LOG_LIMIT = 300;
const TM_DASHBOARD_MONTH_ROW_LIMIT = 3000;
const TM_DASHBOARD_STALE_MESSAGE = '시트가 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인한 뒤 다시 해 주세요.';
const TM_DASHBOARD_READ_FAILED = '시트를 읽지 못했어요. [↻ 새로 읽기]를 눌러 주세요.';
const TM_DASHBOARD_CONNECTION_CHECK = 'Teacher Manager의 [Google 연결 → 출결]에서 연결 상태를 확인해 주세요.';

function openAttendanceDashboard() {
  requireGoeduTeacherAccount_();
  const output = HtmlService.createHtmlOutput(tmDashboardHtml_(tmDashboardViewerKey_()))
    .setWidth(1000)
    .setHeight(620);
  SpreadsheetApp.getUi().showModelessDialog(output, TM_DASHBOARD_TITLE);
}

/** 화면 HTML. 시트 값은 넣지 않는다. 모든 자료는 google.script.run의 JSON으로 온다. */
function tmDashboardHtml_(viewer) {
  // 화면 코드를 따옴표 문자열로 넘겨 script 요소로 실행한다. HtmlService가 백틱 문자열
  // 안의 // 와 /* 를 주석으로 지우는 문제(Google Issue 510815052)를 피하려고 / * < > 를 이스케이프한다.
  const boot = 'window.TM_DASH_VIEWER=' + tmDashboardScriptLiteral_(String(viewer || '')) + ';'
    + 'var s=document.createElement("script");s.text=' + tmDashboardScriptLiteral_(TM_DASHBOARD_JS_)
    + ';document.body.appendChild(s);';
  return '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><base target="_top"><style>'
    + TM_DASHBOARD_CSS_ + '</style></head><body><div id="app"><p class="boot">시트를 읽는 중…</p></div>'
    + '<script>' + boot + '</script></body></html>';
}

function tmDashboardScriptLiteral_(text) {
  return JSON.stringify(String(text))
    .replace(/\//g, '\\/')
    .replace(/\*/g, '\\u002a')
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

function tmDashboardHex_(bytes, count) {
  return bytes.slice(0, count)
    .map(value => ('0' + ((Number(value) + 256) % 256).toString(16)).slice(-2)).join('');
}

// 한 브라우저에 Google 계정이 여럿이면 google.script.run이 다른 계정으로 돌 수 있다.
// 메뉴를 연 계정의 지문을 화면에 두고 요청마다 대조한다(주소 자체는 화면에 넣지 않는다).
function tmDashboardViewerKey_() {
  let email = '';
  try {
    email = String(Session.getEffectiveUser().getEmail() || '').trim().toLowerCase();
  } catch (err) {
    email = '';
  }
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256, 'tm-dashboard-viewer|' + email, Utilities.Charset.UTF_8
  );
  return tmDashboardHex_(bytes, 16);
}

function tmDashboardViewerMatches_(req) {
  const viewer = req && typeof req === 'object' ? String(req.viewer || '') : '';
  return !!viewer && viewer === tmDashboardViewerKey_();
}

/** 화면에 보여도 되는 우리 문장만 그대로 돌려준다. 영문 오류·번호가 섞이면 기본 문장을 쓴다. */
function tmDashboardSafeMessage_(err, fallback) {
  const text = String(err && err.message || '').trim();
  if (!text || text.length > 240 || !/[가-힣]/.test(text)) return fallback;
  const plain = text.replace(/Teacher Manager|Google|Chat|Gemini|AI|Tasks|Docs|Apps Script/g, '');
  return /[A-Za-z]|\d{5,}/.test(plain) ? fallback : text;
}

function tmDashboardServe_(req, work) {
  let account;
  try {
    account = requireGoeduTeacherAccount_();
  } catch (err) {
    return { status: 'failed', message: tmDashboardSafeMessage_(err, '현재 로그인한 Google 계정을 확인하지 못했어요.') };
  }
  if (!tmDashboardViewerMatches_(req)) return { status: 'account-mismatch' };
  try {
    return work(SpreadsheetApp.getActiveSpreadsheet(), account);
  } catch (err) {
    try {
      console.error('대시보드 처리 실패: ' + String(err && err.stack || err));
    } catch (ignored) { /* 기록 실패는 화면 응답을 막지 않는다 */ }
    return { status: 'failed', message: tmDashboardSafeMessage_(err, TM_DASHBOARD_READ_FAILED) };
  }
}

/* ---------- 읽기 ---------- */

function tmDashboardSnapshot(req) {
  return tmDashboardServe_(req, (ss, account) => tmDashboardReadSnapshot_(ss, account, new Date()));
}

function tmDashboardCellText_(value) {
  if (value === null || value === undefined) return '';
  if (value instanceof Date) return isNaN(value.getTime()) ? '' : tmDashboardDateTimeText_(value).slice(0, 10);
  return String(value).trim();
}

// 자유 글 칸(사유·결과·발송기록·비고)에 적힌 이메일 모양 글자와 Google Chat 방 번호
// (spaces/…)는 화면으로 보내지 않는다(R17, SHEET-DASH-02).
function tmDashboardRedact_(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+/g, '(이메일 가림)')
    .replace(/\bspaces\/[A-Za-z0-9_-]+/g, '(Chat방 번호 가림)');
}

function tmDashboardDateTimeText_(date) {
  return Utilities.formatDate(date, 'Asia/Seoul', 'yyyy-MM-dd HH:mm');
}

// 숫자로 저장된 날짜·시각(시트 일련번호, 예: 46295.95544)은 시트에 보이는 벽시계 시각 글자로 바꾼다.
// 글자 시각과 같은 모양이어야 화면의 정렬·표시가 맞는다(SHEET-DASH-08 보완 2026-10-01).
function tmDashboardSerialText_(value) {
  if (typeof value !== 'number' || !isFinite(value) || value < 20000 || value > 80000) return '';
  const wall = new Date(Math.round((value - 25569) * 86400) * 1000);
  const two = n => ('0' + n).slice(-2);
  return wall.getUTCFullYear() + '-' + two(wall.getUTCMonth() + 1) + '-' + two(wall.getUTCDate())
    + ' ' + two(wall.getUTCHours()) + ':' + two(wall.getUTCMinutes());
}

function tmDashboardTimeText_(value) {
  if (value instanceof Date) return isNaN(value.getTime()) ? '' : tmDashboardDateTimeText_(value);
  const serial = tmDashboardSerialText_(value);
  if (serial) return serial;
  const text = String(value === null || value === undefined ? '' : value).trim();
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(text);
  return match ? match[1] + ' ' + match[2] : tmDashboardRedact_(text).slice(0, 40);
}

/** 월 탭 A열 값의 날짜 열쇠. 연도가 없는 글자 날짜는 그 달의 달력 연도로 읽는다. */
function tmDashboardDateKey_(value, calendarYear) {
  const valid = (y, m, d) => {
    const probe = new Date(Date.UTC(y, m - 1, d));
    return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d;
  };
  const key = (y, m, d) => y + '-' + ('0' + m).slice(-2) + '-' + ('0' + d).slice(-2);
  if (value instanceof Date) {
    return isNaN(value.getTime()) ? '' : key(value.getFullYear(), value.getMonth() + 1, value.getDate());
  }
  if (typeof value === 'number' && isFinite(value)) {
    const date = new Date(Math.round(value - 25569) * 86400000);
    return key(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
  }
  const text = String(value === null || value === undefined ? '' : value).trim();
  const parts = text.match(/\d+/g) || [];
  let y = 0, m = 0, d = 0;
  if (parts.length >= 3 && parts[0].length === 4) { y = Number(parts[0]); m = Number(parts[1]); d = Number(parts[2]); }
  else if (parts.length === 2 && calendarYear) { y = calendarYear; m = Number(parts[0]); d = Number(parts[1]); }
  return y && valid(y, m, d) ? key(y, m, d) : '';
}

function tmDashboardCanonicalCell_(value) {
  if (value instanceof Date) return 'D:' + (isNaN(value.getTime()) ? '' : value.getTime());
  if (typeof value === 'number') return 'N:' + String(value);
  if (typeof value === 'boolean') return 'B:' + String(value);
  return 'S:' + String(value === null || value === undefined ? '' : value);
}

// 줄 지문: A~M(숨긴 L 포함)의 값을 이어 붙인 53비트 해시다. 읽을 때와 쓰기 직전에 같은
// 방법으로 만들어, 그 사이 칸 하나라도 바뀌었으면 쓰지 않는다(SHEET-DASH-03).
function tmDashboardFingerprint_(row) {
  return tmDashboardHashCells_(row, MONTHLY_ATTENDANCE_LAST_DATA_COL);
}

// 보낼 메시지 줄(개인톡 A~J, 단체톡 A~G)도 같은 방법으로 지문을 만든다(2단계, §5.1).
function tmDashboardHashCells_(row, width) {
  const cells = [];
  for (let index = 0; index < width; index++) {
    cells.push(tmDashboardCanonicalCell_((row || [])[index]));
  }
  const text = cells.join('\u001f');
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

function tmDashboardIsStaleClaim_(value, now) {
  if (value instanceof Date) return !isNaN(value.getTime()) && now.getTime() - value.getTime() > 10 * 60 * 1000;
  return queueClaimIsStale_(value, now);
}

/** 보낸 기록은 발송 코드와 같은 L열 대조로 판단한다. L열 값은 화면으로 보내지 않는다. */
function tmDashboardReminderState_(row, now) {
  const status = String(row[8] || '').trim();
  if (!status) return 'none';
  if (status === '보냄') {
    return String(row[11] || '').trim() === attendanceChatSignatureV2_(row) ? 'current' : 'mismatch';
  }
  if (status === '발송중') return tmDashboardIsStaleClaim_(row[9], now) ? 'sending-stale' : 'sending';
  if (status === '실패') return 'failed';
  if (status === '연결필요') return 'needs-connection';
  return 'other';
}

function tmDashboardHeaderMatches_(header) {
  const expected = INPUT_HEADERS.concat(MONTHLY_CHAT_RESULT_HEADERS);
  return expected.every((value, index) => header[index] === value)
    && ['', MONTHLY_ATTENDANCE_AI_MARK_HEADER].indexOf(String(header[12] === undefined ? '' : header[12]).trim()) >= 0;
}

function tmDashboardInputText_(raw) {
  const text = String(raw === null || raw === undefined ? '' : raw).trim();
  if (text === MONTHLY_ATTENDANCE_AI_INPUT_HINT || text === MONTHLY_ATTENDANCE_AI_INPUT_PLACEHOLDER) return '';
  return text;
}

function tmDashboardReadMonth_(sheet, month, calendarYear, entry, rows, uncertain, now) {
  const lastRow = sheet.getLastRow();
  const height = Math.max(2, Math.min(lastRow, TM_DASHBOARD_MONTH_ROW_LIMIT + 2));
  const values = sheet.getRange(1, 1, height, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues();
  const top = (values[0] || []).map(value => String(value === null || value === undefined ? '' : value).trim());
  const header = values[1] || [];
  const inputMatches = row => INPUT_HEADERS.every((label, index) => String(row[index] === undefined ? '' : row[index]).trim() === label);
  const layout = inputMatches(header) ? 'current' : inputMatches(top) ? 'one-header-row' : '';
  if (!layout) {
    entry.problem = 'layout';
    return;
  }
  entry.readable = true;
  entry.writable = layout === 'current' && tmDashboardHeaderMatches_(header);
  if (!entry.writable) entry.problem = layout === 'current' ? 'header' : 'old-layout';
  // 쓰기 관문(tmDashboardWriteGuard_)이 거절할 탭은 처음부터 보기만 하게 한다(설계 §3.6).
  if (entry.writable && tmDashboardRightSideUsed_(sheet)) {
    entry.writable = false;
    entry.problem = 'extra-columns';
  }
  const firstData = layout === 'current' ? MONTHLY_ATTENDANCE_DATA_START_ROW : 2;
  if (layout === 'current') {
    const raw = tmDashboardInputText_(values[0] ? values[0][1] : '');
    if (raw) {
      entry.inputText = tmDashboardRedact_(raw);
      const hash = attendanceAiSentenceHash_(raw);
      const marks = (uncertain[String(entry.sheetId)] || []).filter(item => item.h === hash);
      entry.inputUncertain = marks.some(item => !item.e);
      entry.inputEntered = !entry.inputUncertain && marks.some(item => item.e);
    }
  }
  for (let rowNumber = firstData; rowNumber <= height; rowNumber++) {
    const row = values[rowNumber - 1];
    if (!row || !row.slice(0, 8).some(value => tmDashboardCellText_(value) !== '')) continue;
    const dateKey = tmDashboardDateKey_(row[0], calendarYear);
    rows.push({
      s: entry.sheetId,
      r: rowNumber,
      fp: tmDashboardFingerprint_(row),
      d: dateKey,
      dt: dateKey ? '' : tmDashboardRedact_(tmDashboardCellText_(row[0])).slice(0, 40),
      st: tmDashboardRedact_(tmDashboardCellText_(row[1])),
      c: tmDashboardCellText_(row[2]),
      k: tmDashboardCellText_(row[3]),
      e: tmDashboardRedact_(tmDashboardCellText_(row[4])),
      p: tmDashboardCellText_(row[5]),
      g: tmDashboardCellText_(row[6]),
      h: tmDashboardCellText_(row[7]),
      rs: tmDashboardReminderState_(row, now),
      j: tmDashboardTimeText_(row[9]),
      kr: tmDashboardQueueResult_(row[10]),
      ai: String(row[12] === undefined ? '' : row[12]).trim() === MONTHLY_ATTENDANCE_AI_MARK_TEXT
    });
  }
  if (lastRow > height) entry.truncated = true;
}

function tmDashboardUncertainMarkers_() {
  const out = {};
  try {
    const all = PropertiesService.getDocumentProperties().getProperties() || {};
    Object.keys(all).forEach(key => {
      if (key.indexOf(ATTENDANCE_AI_UNCERTAIN_PROPERTY_PREFIX) !== 0) return;
      out[key.slice(ATTENDANCE_AI_UNCERTAIN_PROPERTY_PREFIX.length)] = attendanceAiUncertainEntries_(all[key]);
    });
  } catch (err) { /* 경고용 기록을 읽지 못하면 경고 없이 보여 준다 */ }
  return out;
}

function tmDashboardReadRoster_(ss, config, out) {
  const sheet = ss.getSheetByName(String(config.ROSTER_SHEET_NAME || '학생명단').trim());
  if (!sheet) {
    out.rosterProblem = 'missing';
    return;
  }
  const last = sheet.getLastRow();
  if (last < 1) return;
  const values = sheet.getRange(1, 1, last, ROSTER_HEADERS.length).getValues();
  const head = values[0].map(value => String(value === null || value === undefined ? '' : value).trim());
  if (head[0] !== ROSTER_HEADERS[0] || head[1] !== ROSTER_HEADERS[1]) {
    out.rosterProblem = 'layout';
    return;
  }
  const seen = {};
  values.slice(1).forEach(row => {
    const number = String(row[0] === null || row[0] === undefined ? '' : row[0]).trim();
    const name = String(row[1] === null || row[1] === undefined ? '' : row[1]).trim();
    if (!number && !name) return;
    const label = combineStudentNumberAndName_(number, name) || name || number;
    // 이메일 주소는 보내지 않는다. 형식이 맞는지만 알린다(R17).
    const email = typeof row[2] === 'string' ? row[2].trim() : '';
    const student = { n: number, name: tmDashboardRedact_(name), label: tmDashboardRedact_(label), hasEmail: isExactGoeduEmail_(email) };
    if (seen[label]) {
      seen[label].dup = true;
      student.dup = true;
    }
    seen[label] = student;
    out.roster.push(student);
  });
}

function tmDashboardReadHolidays_(ss, config, out) {
  const sheet = ss.getSheetByName(String(config.HOLIDAY_SHEET_NAME || FALLBACK_HOLIDAY_SHEET_NAME).trim());
  out.holidaysPresent = !!sheet;
  if (!sheet || sheet.getLastRow() < 2) return;
  const values = sheet.getRange(2, 1, sheet.getLastRow() - 1, 6).getValues();
  values.forEach(row => {
    const date = toDate_(row[0]);
    if (!date) return;
    out.holidays.push({
      d: tmDashboardDateKey_(date, null),
      name: tmDashboardRedact_(tmDashboardCellText_(row[1])),
      type: tmDashboardCellText_(row[2]),
      state: tmDashboardCellText_(row[3]),
      note: tmDashboardRedact_(tmDashboardCellText_(row[4])),
      src: tmDashboardRedact_(tmDashboardCellText_(row[5])).slice(0, 300)
    });
  });
}

function tmDashboardQueueSheet_(ss, preferred, legacyNames) {
  const sheet = ss.getSheetByName(preferred);
  if (sheet) return sheet;
  for (let index = 0; index < legacyNames.length; index++) {
    const legacy = ss.getSheetByName(legacyNames[index]);
    if (legacy) return legacy;
  }
  return null;
}

// 보내는 중에는 결과 칸에 요청 번호가 있다. 번호는 내부 값이라 화면으로 보내지 않는다.
function tmDashboardQueueResult_(value) {
  return tmDashboardRedact_(tmDashboardCellText_(value)).replace(/req-[a-f0-9]+/g, '').trim().slice(0, 300);
}

function tmDashboardReadQueue_(ss, kind, now) {
  const personal = kind === 'personal';
  const headers = personal ? PERSONAL_MESSAGE_QUEUE_HEADERS : CLASS_MESSAGE_QUEUE_HEADERS;
  const sheet = tmDashboardQueueSheet_(ss, personal ? MESSENGER_PERSONAL_SHEET_NAME : MESSENGER_CLASS_SHEET_NAME,
    personal ? LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES : LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES);
  const result = { present: !!sheet, layoutOk: true, rows: [] };
  if (!sheet) return result;
  const last = sheet.getLastRow();
  if (last < 1) return result;
  const values = sheet.getRange(1, 1, last, headers.length).getValues();
  const head = values[0].map(value => String(value === null || value === undefined ? '' : value).trim());
  if (!headers.every((label, index) => head[index] === label)) {
    result.layoutOk = false;
    return result;
  }
  const cutoff = Utilities.formatDate(new Date(now.getTime() - TM_DASHBOARD_QUEUE_RECENT_DAYS * 86400000), 'Asia/Seoul', 'yyyy-MM-dd');
  for (let index = 1; index < values.length; index++) {
    const row = values[index];
    if (!row.some(value => tmDashboardCellText_(value) !== '')) continue;
    const status = tmDashboardCellText_(row[personal ? 6 : 4]);
    const sentAt = tmDashboardSerialText_(row[personal ? 8 : 5]) || row[personal ? 8 : 5];
    // 대기 줄의 날짜는 보내는 함수(formatQueueDateKey_)와 같게 둔다. 숫자 날짜를 화면에서만 오늘로 읽으면 보내지 않을 줄을 센다.
    const dateKey = tmDashboardRedact_((status !== '대기' && tmDashboardSerialText_(row[0]).slice(0, 10))
      || formatQueueDateKey_(row[0], '')).slice(0, 40);
    const sentText = tmDashboardTimeText_(sentAt);
    const open = ['대기', '발송중', '실패'].indexOf(status) >= 0;
    if (!open && !(dateKey >= cutoff || sentText.slice(0, 10) >= cutoff)) continue;
    const item = {
      r: index + 1,
      fp: tmDashboardHashCells_(row, headers.length),
      d: dateKey,
      type: tmDashboardCellText_(row[personal ? 3 : 1]),
      text: tmDashboardRedact_(tmDashboardCellText_(row[personal ? 4 : 2])).slice(0, 1000),
      src: tmDashboardCellText_(row[personal ? 5 : 3]),
      st: status,
      at: sentText,
      res: status === '발송중' ? '' : tmDashboardQueueResult_(row[personal ? 9 : 6]),
      stale: status === '발송중' && tmDashboardIsStaleClaim_(sentAt, now)
    };
    if (personal) {
      item.n = tmDashboardCellText_(row[1]);
      item.name = tmDashboardRedact_(tmDashboardCellText_(row[2]));
      item.att = tmDashboardCellText_(row[7]).indexOf('출결표|') === 0;
    }
    result.rows.push(item);
  }
  return result;
}

function tmDashboardReadChatLog_(ss, config) {
  const sheet = ss.getSheetByName(String(config.CHAT_LOG_SHEET_NAME || '발송기록').trim());
  const result = { present: !!sheet, total: 0, rows: [] };
  if (!sheet) return result;
  const last = sheet.getLastRow();
  if (last < 2) return result;
  result.total = last - 1;
  const start = Math.max(2, last - TM_DASHBOARD_LOG_LIMIT + 1);
  const values = sheet.getRange(start, 1, last - start + 1, 7).getValues();
  // D열 Chat방 번호는 내부 값이라 보내지 않는다. 단톡방 이름이 비어 있던 때의 단체방 기록은
  // C열에도 같은 방 번호가 있으므로 '학급 단톡방'으로 바꿔 보낸다(Code.gs의 단체방 기록 참고).
  for (let index = values.length - 1; index >= 0; index--) {
    const row = values[index];
    if (!row.some(value => tmDashboardCellText_(value) !== '')) continue;
    const rawTarget = tmDashboardCellText_(row[2]);
    const spaceCell = tmDashboardCellText_(row[3]);
    const roomOnly = !!rawTarget && (rawTarget === spaceCell || /^spaces\//.test(rawTarget));
    result.rows.push({
      at: tmDashboardTimeText_(row[0]),
      kind: tmDashboardCellText_(row[1]),
      target: roomOnly ? '학급 단톡방' : tmDashboardRedact_(rawTarget).slice(0, 100),
      text: tmDashboardRedact_(tmDashboardCellText_(row[4])).slice(0, 300),
      result: tmDashboardCellText_(row[5]).slice(0, 40),
      error: tmDashboardQueueResult_(row[6])
    });
  }
  return result;
}

/** 처음 설정 표시가 이 출석부·이 계정의 것인지 가른다. 다른 계정이면 보기만 한다(Q11 제안). */
function tmDashboardSetupState_(config, spreadsheetId, account) {
  const marker = String(config.FIRST_TIME_SETUP_SHEET_DONE || config.FIRST_TIME_SETUP_DONE || '').trim();
  if (!marker) return 'not-done';
  if (attendanceSetupMarkerMatches_(marker, spreadsheetId, account)) return 'confirmed';
  const parts = marker.split(/\s+/);
  return parts[0] === attendanceConnectionCodeForSpreadsheetId_(spreadsheetId) ? 'other-account' : 'mismatch';
}

function tmDashboardReadSnapshot_(ss, account, now) {
  const out = {
    status: 'ok',
    readAt: tmDashboardDateTimeText_(now),
    today: Utilities.formatDate(now, 'Asia/Seoul', 'yyyy-MM-dd'),
    workbook: { ok: true },
    setup: 'not-done',
    canWrite: false,
    classInfo: {},
    flags: {},
    monthsProblem: '',
    months: [],
    rows: [],
    roster: [],
    rosterProblem: '',
    holidays: [],
    holidaysPresent: false,
    personalQueue: { present: false, layoutOk: true, rows: [] },
    classQueue: { present: false, layoutOk: true, rows: [] },
    chatLog: { present: false, total: 0, rows: [] }
  };
  let config;
  try {
    config = readAttendanceConfigStrict_(ss);
  } catch (err) {
    out.workbook = { ok: false, message: '출석부 설정을 확인하지 못했어요. ' + TM_DASHBOARD_CONNECTION_CHECK };
    return out;
  }
  const text = key => tmDashboardRedact_(tmDashboardCellText_(config[key])).slice(0, 100);
  out.setup = tmDashboardSetupState_(config, ss.getId(), account);
  out.canWrite = out.setup === 'confirmed';
  // 학급 표시에 필요한 값만 보낸다. 비밀값·연결 번호·ID는 보내지 않는다(SHEET-DASH-02).
  out.classInfo = {
    schoolName: text('SCHOOL_NAME'), schoolYear: text('SCHOOL_YEAR'), grade: text('GRADE'),
    classNumber: text('CLASS_NUMBER'), classLabel: text('CLASS_LABEL'), teacherName: text('TEACHER_NAME'),
    classRoomName: text('CLASS_CHAT_SPACE_NAME'), taskListTitle: text('TASK_LIST_TITLE'),
    destFolderName: text('DEST_FOLDER_NAME')
  };
  let aiTargetMatches = false;
  try {
    aiTargetMatches = String(PropertiesService.getScriptProperties()
      .getProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY) || '').trim() === String(ss.getId());
  } catch (err) { aiTargetMatches = false; }
  const allowed = tmDashboardCellText_(config[ATTENDANCE_AI_ALLOWED_SETTING]);
  out.flags = {
    aiAllowed: allowed ? allowed === ATTENDANCE_AI_ALLOWED_VALUE : null,
    hasGeminiKey: isAttendanceAiApiKeyShape_(attendanceAiGeminiApiKey_(ss)),
    aiTargetMatches: aiTargetMatches,
    classRoomChosen: !!tmDashboardCellText_(config.CLASS_CHAT_SPACE_ID)
  };
  let manifest = null;
  try {
    manifest = parseAttendanceMonthSheetIds_(config.ATTENDANCE_MONTH_SHEET_IDS);
  } catch (err) {
    out.monthsProblem = '월별 출결표 연결 기록을 확인하지 못했어요. ' + TM_DASHBOARD_CONNECTION_CHECK;
  }
  if (manifest) {
    const byId = new Map();
    ss.getSheets().forEach(sheet => byId.set(sheet.getSheetId(), sheet));
    // 월별 탭 이름이 다른 업무 탭과 겹치면 그 달은 읽지 않는다(attendanceMonthSheetsFor_와 같은 기준).
    const reserved = new Set(['설정', '드롭다운', '학생명단', '휴일', '템플릿_치환표', '발송기록', '00_사용법',
      MESSENGER_PERSONAL_SHEET_NAME, MESSENGER_CLASS_SHEET_NAME].concat(LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES)
      .concat(LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES)
      .concat([config.ROSTER_SHEET_NAME, config.HOLIDAY_SHEET_NAME, config.CHAT_LOG_SHEET_NAME].filter(Boolean)));
    const uncertain = tmDashboardUncertainMarkers_();
    [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2].forEach(month => {
      const sheetId = manifest[String(month)];
      const entry = { month: month, sheetId: sheetId, name: '', readable: false, writable: false, problem: '',
        inputText: '', inputUncertain: false, inputEntered: false, truncated: false };
      out.months.push(entry);
      const sheet = byId.get(sheetId);
      if (!sheet) {
        entry.problem = 'missing';
        return;
      }
      entry.name = tmDashboardRedact_(sheet.getName());
      if (reserved.has(sheet.getName())) {
        entry.problem = 'role';
        return;
      }
      const calendarYear = getAttendanceAiCalendarYear_(config.SCHOOL_YEAR, month);
      tmDashboardReadMonth_(sheet, month, calendarYear, entry, out.rows, uncertain, now);
    });
  }
  tmDashboardReadRoster_(ss, config, out);
  tmDashboardReadHolidays_(ss, config, out);
  out.personalQueue = tmDashboardReadQueue_(ss, 'personal', now);
  out.classQueue = tmDashboardReadQueue_(ss, 'class', now);
  out.chatLog = tmDashboardReadChatLog_(ss, config);
  return out;
}

/** 머리글 칩 세 개. 표를 늦추지 않도록 스냅숏과 따로 부른다(§3.2). */
function tmDashboardHealth(req) {
  return tmDashboardServe_(req, ss => {
    const result = { status: 'ok', ai: 'unknown', chat: 'unknown', classRoom: 'unknown', classRoomName: '' };
    let config = null;
    try {
      config = readAttendanceConfigStrict_(ss);
    } catch (err) {
      return result;
    }
    result.classRoomName = tmDashboardRedact_(tmDashboardCellText_(config.CLASS_CHAT_SPACE_NAME)).slice(0, 100);
    try {
      const health = apiAttendanceWorkbookHealth();
      if (health.aiReadState === 'verified') result.ai = 'ready';
      else if (health.recoveryAction === 'repair-ai-input') result.ai = 'action';
    } catch (err) { /* 확인하지 못하면 회색으로 둔다 */ }
    const allowed = tmDashboardCellText_(config[ATTENDANCE_AI_ALLOWED_SETTING]);
    if (result.ai !== 'ready' && allowed && allowed !== ATTENDANCE_AI_ALLOWED_VALUE) result.ai = 'action';
    const spaceId = tmDashboardCellText_(config.CLASS_CHAT_SPACE_ID);
    if (!spaceId) result.classRoom = 'none';
    try {
      const status = tmDashboardCentralStatus_(ss);
      result.chat = status.connected === true ? 'ok' : status.connected === false ? 'needs_connect' : 'unknown';
      if (spaceId && status.connected === true && status.classEnabled === true
          && String(status.classSpaceResource || '') === spaceId) {
        result.classRoom = 'ok';
      }
    } catch (err) { /* 연결 상태를 모르면 회색으로 둔다(CONSENT-10) */ }
    return result;
  });
}

// /v1/status를 읽기만 한다. callCentralChatSender_의 일반 경로는 복사된 출석부에서
// 연결값을 새로 쓰므로(ensureCentralChatConfig_) 여기서는 저장된 값만 쓴다.
function tmDashboardCentralStatus_(ss) {
  const central = readAttendanceCentralConfig_(ss);
  if (!/^https:\/\//i.test(central.url)) throw new Error('발송 서버 주소를 확인하지 못했어요.');
  const response = UrlFetchApp.fetch(central.url.replace(/\/$/, '') + '/v1/status', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ sheetId: central.sheetId, sheetSecret: central.sheetSecret }),
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  if (code < 200 || code >= 300) throw centralSenderError_(code, {});
  return JSON.parse(response.getContentText() || '{}');
}

/** 월 탭의 줄 또는 알려진 탭을 시트에서 고른다. 화면은 성공하면 대화상자를 닫는다. */
function tmDashboardSelectInSheet(req) {
  return tmDashboardServe_(req, ss => {
    const input = req && typeof req === 'object' ? req : {};
    const config = readAttendanceConfigStrict_(ss);
    if (input.tab) {
      const names = {
        roster: config.ROSTER_SHEET_NAME || '학생명단', holiday: config.HOLIDAY_SHEET_NAME || FALLBACK_HOLIDAY_SHEET_NAME,
        log: config.CHAT_LOG_SHEET_NAME || '발송기록', personal: MESSENGER_PERSONAL_SHEET_NAME,
        class: MESSENGER_CLASS_SHEET_NAME, config: CONFIG_SHEET_NAME, dropdown: STUDENT_DROPDOWN_SHEET_NAME,
        template: '템플릿_치환표', guide: '00_사용법'
      };
      const name = names[String(input.tab)];
      const sheet = name ? ss.getSheetByName(String(name).trim()) : null;
      if (!sheet) return { status: 'failed', message: '시트에서 그 탭을 찾지 못했어요.' };
      ss.setActiveSheet(sheet);
      return { status: 'applied' };
    }
    const target = tmDashboardMonthSheet_(ss, config, input.sheetId);
    const sheet = target.sheet;
    if (input.row === undefined || input.row === null) {
      ss.setActiveSheet(sheet);
      return { status: 'applied' };
    }
    let rowNumber = Number(input.row);
    const expected = String(input.expectedFp || '');
    const last = sheet.getLastRow();
    if (!Number.isSafeInteger(rowNumber) || rowNumber < MONTHLY_ATTENDANCE_DATA_START_ROW || rowNumber > last
        || tmDashboardFingerprint_(sheet.getRange(rowNumber, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues()[0]) !== expected) {
      // 정렬로 줄이 옮겨졌으면 같은 지문의 줄을 찾는다.
      rowNumber = 0;
      if (last >= MONTHLY_ATTENDANCE_DATA_START_ROW) {
        const values = sheet.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 1,
          last - MONTHLY_ATTENDANCE_DATA_START_ROW + 1, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues();
        const found = values.findIndex(row => tmDashboardFingerprint_(row) === expected);
        if (found >= 0) rowNumber = found + MONTHLY_ATTENDANCE_DATA_START_ROW;
      }
    }
    if (!rowNumber) return { status: 'stale', message: '시트가 바뀌어 그 줄을 찾지 못했어요. 다시 읽었어요.' };
    ss.setActiveSheet(sheet);
    sheet.setActiveRange(sheet.getRange(rowNumber, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL));
    return { status: 'applied' };
  });
}

/* ---------- 쓰기 ---------- */

function tmDashboardMonthSheet_(ss, config, sheetId) {
  const id = Number(sheetId);
  if (!Number.isSafeInteger(id)) throw new Error('월별 출결표를 확인하지 못했어요.');
  const match = attendanceMonthSheetsFor_(ss, config).find(item => item.sheet.getSheetId() === id);
  if (!match) throw new Error('월별 출결표를 확인하지 못했어요. 다른 탭에 쓰지 않았어요.');
  return match;
}

function tmDashboardRequestId_(req) {
  const id = req && typeof req === 'object' ? String(req.clientRequestId || '') : '';
  return /^[A-Za-z0-9_-]{8,64}$/.test(id) ? id : '';
}

function tmDashboardCachedResult_(cache, requestId) {
  if (!cache || !requestId) return null;
  const raw = cache.get(TM_DASHBOARD_REQUEST_PREFIX + requestId);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

/**
 * 모든 쓰기의 공통 관문이다. 이 출석부의 처음 설정 계정만 쓴다(§5.7). 같은 요청 번호는 한 번만
 * 처리한다(두 번 누르기). lock이면 문서 잠금을 5초 기다리고, 못 잡으면 아무것도 하지 않고 busy다.
 */
function tmDashboardWrite_(req, options, work) {
  return tmDashboardServe_(req, (ss, account) => {
    const config = readAttendanceConfigStrict_(ss);
    const setup = tmDashboardSetupState_(config, ss.getId(), account);
    if (setup !== 'confirmed') {
      return { status: 'disabled', message: setup === 'other-account'
        ? '이 출석부를 설정한 계정만 입력하고 보낼 수 있어요.'
        : setup === 'mismatch' ? '이 출석부의 처음 설정 기록을 확인하지 못했어요. ' + TM_DASHBOARD_CONNECTION_CHECK
          : '처음 설정이 끝나지 않았어요. 시트 메뉴 [🔵 처음 한 번 설정하기] → [▶ 처음 설정 한 번에 끝내기]를 눌러 주세요.' };
    }
    const requestId = tmDashboardRequestId_(req);
    let cache = null;
    try {
      cache = CacheService.getDocumentCache();
    } catch (err) {
      cache = null;
    }
    const earlier = tmDashboardCachedResult_(cache, requestId);
    if (earlier) return earlier;
    const key = TM_DASHBOARD_REQUEST_PREFIX + requestId;
    let result;
    if (options && options.lock) {
      const lock = LockService.getDocumentLock();
      if (!lock || !lock.tryLock(5000)) return { status: 'busy', message: '다른 작업이 끝난 뒤 다시 눌러 주세요.' };
      try {
        const again = tmDashboardCachedResult_(cache, requestId);
        if (again) return again;
        result = work(ss, config, account);
        SpreadsheetApp.flush();
      } finally {
        lock.releaseLock();
      }
    } else {
      // AI 처리처럼 안에서 잠그는 작업은 처리 중 표시로 같은 요청이 겹치지 않게 한다.
      // 보내기는 몇 분 걸릴 수 있고 도중에 끊기면 결과를 모르므로 '결과 확인 필요'를 오래 둔다(C7).
      const pending = (options && options.pending) || { status: 'busy', message: '앞의 요청을 처리하고 있어요.' };
      if (cache && requestId) cache.put(key, JSON.stringify(pending), (options && options.pendingTtl) || 120);
      try {
        result = work(ss, config, account);
      } catch (err) {
        if (cache && requestId) cache.remove(key);
        throw err;
      }
    }
    if (cache && requestId) {
      if (result && result.status !== 'busy') cache.put(key, JSON.stringify(result), 21600);
      else cache.remove(key);
    }
    return result;
  });
}

// 읽을 때 쓰는 빠른 확인이다. 값·수식이 있으면 getLastColumn()이 M열보다 크고, 메모는
// getNotes()로 본다. M열 오른쪽 열이 없는 보통의 출석부는 getMaxColumns() 한 번으로 끝난다.
// 쓰기 직전에는 tmDashboardWriteGuard_가 값·수식·메모를 모두 다시 읽는다.
function tmDashboardRightSideUsed_(sheet) {
  try {
    const maxColumns = sheet.getMaxColumns();
    if (maxColumns <= MONTHLY_ATTENDANCE_LAST_DATA_COL) return false;
    if (sheet.getLastColumn() > MONTHLY_ATTENDANCE_LAST_DATA_COL) return true;
    const notes = sheet.getRange(1, MONTHLY_ATTENDANCE_LAST_DATA_COL + 1, sheet.getMaxRows(),
      maxColumns - MONTHLY_ATTENDANCE_LAST_DATA_COL).getNotes();
    return notes.some(row => row.some(note => String(note === null || note === undefined ? '' : note).trim()));
  } catch (err) {
    return false; // 확인하지 못하면 쓰기 관문에 맡긴다
  }
}

/** 제목 줄이 정확하고 M열 오른쪽이 비었을 때만 쓴다. 아니면 이유 문장을 돌려준다. */
function tmDashboardWriteGuard_(sheet) {
  const header = sheet.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues()[0];
  if (!tmDashboardHeaderMatches_(header)) {
    return '월별 출결표의 제목 줄이 바뀌어 저장하지 않았어요. Teacher Manager의 [Google 연결 → 출결]에서 이 출석부의 상태를 확인해 주세요.';
  }
  const maxColumns = sheet.getMaxColumns();
  if (maxColumns > MONTHLY_ATTENDANCE_LAST_DATA_COL) {
    const tail = sheet.getRange(1, MONTHLY_ATTENDANCE_LAST_DATA_COL + 1, sheet.getMaxRows(), maxColumns - MONTHLY_ATTENDANCE_LAST_DATA_COL);
    const used = [tail.getValues(), tail.getFormulas(), tail.getNotes()]
      .some(grid => grid.some(row => row.some(value => String(value === null || value === undefined ? '' : value).trim())));
    if (used) return 'M열 오른쪽에 적은 내용이 있어 저장하지 않았어요. 그 내용을 옮긴 뒤 다시 해 주세요.';
  }
  return '';
}

function tmDashboardRosterLabels_(ss, config) {
  const sheet = ss.getSheetByName(String(config.ROSTER_SHEET_NAME || '학생명단').trim());
  if (!sheet || sheet.getLastRow() < 2) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 2).getValues()
    .map(row => combineStudentNumberAndName_(row[0], row[1])).filter(Boolean);
}

/** 대시보드에서 넣는 값 하나를 월 탭의 입력 규칙으로 확인한다. 맞지 않으면 message를 채운다. */
function tmDashboardCheckValue_(field, raw, context) {
  const text = String(raw === null || raw === undefined ? '' : raw).trim();
  if (/[\u0000-\u001F\u007F]/.test(text)) return { message: '줄바꿈이나 특수 문자는 넣을 수 없어요.' };
  if (field === 'date') {
    const parsed = attendanceAiParseDateKey_(text);
    if (!parsed || parsed.month !== context.month || parsed.year !== context.calendarYear) {
      return { message: '날짜는 ' + context.month + '월 출결표의 날짜로 골라 주세요.' };
    }
    return { value: parsed.key };
  }
  if (field === 'student') {
    const matches = context.roster.filter(label => label === text);
    if (matches.length !== 1) return { message: '학생은 학생명단의 번호+이름으로 골라 주세요.' };
    return { value: text };
  }
  if (field === 'category') return ATTENDANCE_AI_CATEGORIES.indexOf(text) >= 0 ? { value: text } : { message: '구분을 골라 주세요.' };
  if (field === 'kind') return ATTENDANCE_AI_KINDS.indexOf(text) >= 0 ? { value: text } : { message: '종류를 골라 주세요.' };
  if (field === 'period') return ATTENDANCE_AI_PERIODS.indexOf(text) >= 0 ? { value: text } : { message: '교시를 골라 주세요.' };
  if (field === 'report' || field === 'attach') {
    return TM_DASHBOARD_DOC_VALUES.indexOf(text) >= 0 ? { value: text } : { message: '신고서·첨부 값을 골라 주세요.' };
  }
  if (field === 'reason') {
    return text.length <= TM_DASHBOARD_REASON_MAX ? { value: text } : { message: '사유는 ' + TM_DASHBOARD_REASON_MAX + '자까지 적을 수 있어요.' };
  }
  return { message: '바꿀 수 없는 항목이에요.' };
}

const TM_DASHBOARD_FIELD_COLUMNS = Object.freeze({
  date: 0, student: 1, category: 2, kind: 3, reason: 4, period: 5, report: 6, attach: 7
});

// 날짜는 날짜 값(일련번호)으로, 나머지는 글자 그대로 넣는다. 사유가 =나 3/4처럼 보여도
// 수식·날짜로 바뀌지 않는다(AI 입력과 같은 방식).
function tmDashboardCellData_(field, value) {
  if (field === 'date') {
    const parsed = attendanceAiParseDateKey_(value);
    const serial = Math.floor(Date.UTC(parsed.year, parsed.month - 1, parsed.day) / 86400000) + 25569;
    return { userEnteredValue: { numberValue: serial } };
  }
  return value ? { userEnteredValue: { stringValue: String(value) } } : {};
}

function tmDashboardWriteContext_(ss, config, target) {
  return {
    month: target.month,
    calendarYear: getAttendanceAiCalendarYear_(config.SCHOOL_YEAR, target.month),
    roster: tmDashboardRosterLabels_(ss, config)
  };
}

/** [항목을 골라 출결 입력]. 신고서·첨부는 따로 고르지 않으면 미제출로 시작한다(사용자 결정 2026-10-01). */
function tmDashboardAppendAttendance(input) {
  return tmDashboardWrite_(input, { lock: true }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    const target = tmDashboardMonthSheet_(ss, config, data.sheetId);
    const context = tmDashboardWriteContext_(ss, config, target);
    if (context.calendarYear === null) return { status: 'disabled', message: '출석부의 학년도를 확인하지 못했어요. ' + TM_DASHBOARD_CONNECTION_CHECK };
    const record = {};
    const raw = {
      date: data.date, student: data.student, category: data.category, kind: data.kind, reason: data.reason,
      period: data.period === undefined || data.period === null ? '' : data.period,
      report: data.report === undefined || data.report === null ? '미제출' : data.report,
      attach: data.attach === undefined || data.attach === null ? '미제출' : data.attach
    };
    const fields = Object.keys(TM_DASHBOARD_FIELD_COLUMNS);
    for (let index = 0; index < fields.length; index++) {
      const checked = tmDashboardCheckValue_(fields[index], raw[fields[index]], context);
      if (checked.message) return { status: 'invalid', message: checked.message };
      record[fields[index]] = checked.value;
    }
    const sheet = target.sheet;
    const guard = tmDashboardWriteGuard_(sheet);
    if (guard) return { status: 'disabled', message: guard };
    authorizeAttendanceOperation_('historical-manual', record.date);
    const request = { requests: [{ appendCells: {
      sheetId: sheet.getSheetId(),
      rows: [{ values: fields.map(field => tmDashboardCellData_(field, record[field])) }],
      fields: 'userEnteredValue'
    } }] };
    const uncertain = { status: 'uncertain', message: '등록 결과를 확인하지 못했어요. ' + target.month + '월 출결 기록에 줄이 생겼는지 확인한 뒤 [↻ 새로 읽기]를 눌러 주세요.' };
    let response;
    try {
      response = Sheets.Spreadsheets.batchUpdate(request, ss.getId());
    } catch (err) {
      console.error('대시보드 출결 입력 결과 미확인: ' + String(err && err.message || err));
      return uncertain;
    }
    if (!response || response.spreadsheetId !== ss.getId()) return uncertain;
    // 시트에서 직접 넣은 줄과 같게 날짜순으로 모으고 줄무늬를 맞춘다(SHEET-STRIPE-01).
    try {
      if (!sortMonthlyAttendanceRows_(sheet, 'date')) reStripeSheet_(sheet);
    } catch (err) { /* 기록은 끝났다. 정렬 실패 때문에 같은 줄을 다시 넣지 않는다 */ }
    return { status: 'applied', month: target.month, message: target.month + '월 출결 기록에 1줄을 입력했어요.' };
  });
}

function tmDashboardUpdateAttendance(input) {
  return tmDashboardWrite_(input, { lock: true }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    return tmDashboardUpdateRow_(ss, config, data, data.values && typeof data.values === 'object' ? data.values : {});
  });
}

/** 신고서(G)·첨부(H) 선택. 선생님이 고른 값만 쓴다(한 번 누르기 순환 없음, SHEET-REPORT-02). */
function tmDashboardSetDocStatus(input) {
  return tmDashboardWrite_(input, { lock: true }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    if (data.field !== 'report' && data.field !== 'attach') return { status: 'invalid', message: '바꿀 수 없는 항목이에요.' };
    const values = {};
    values[data.field] = data.value;
    return tmDashboardUpdateRow_(ss, config, data, values);
  });
}

function tmDashboardSameValue_(field, current, next) {
  if (field === 'date') return tmDashboardDateKey_(current, null) === next;
  return tmDashboardCellText_(current) === next;
}

/**
 * 쓴 결과를 확인할 때 쓰는 읽기. 같은 실행 안에서 SpreadsheetApp로 먼저 읽어 둔 칸은
 * Sheets API 쓰기나 그 뒤의 쓰기 뒤에도 쓰기 전 값으로 돌아올 수 있었다(2026-10-01 현장,
 * SHEET-DASH-08). 그래서 flush한 뒤 Sheets API로 직접 읽는다. 날짜는 일련번호, 빈칸은 ''이다.
 */
function tmDashboardReadRowsFresh_(ss, sheet, firstRow, lastRow, width) {
  SpreadsheetApp.flush();
  let letters = '';
  for (let n = width; n > 0; n = Math.floor((n - 1) / 26)) letters = String.fromCharCode(65 + (n - 1) % 26) + letters;
  const a1 = "'" + sheet.getName().replace(/'/g, "''") + "'!A" + firstRow + ':' + letters + lastRow;
  const reply = Sheets.Spreadsheets.Values.get(ss.getId(), a1,
    { valueRenderOption: 'UNFORMATTED_VALUE', dateTimeRenderOption: 'SERIAL_NUMBER' });
  const rows = reply && Array.isArray(reply.values) ? reply.values : [];
  const out = [];
  for (let index = 0; index <= lastRow - firstRow; index++) {
    const row = Array.isArray(rows[index]) ? rows[index] : [];
    const filled = [];
    for (let column = 0; column < width; column++) {
      filled.push(row[column] === undefined || row[column] === null ? '' : row[column]);
    }
    out.push(filled);
  }
  return out;
}

// 줄 지문이 같을 때만, 바뀐 칸만 쓴다. 시트 편집(onEdit)과 같게 A·B·G·H가 바뀌면 I:L을
// 비우고, 날짜가 바뀌면 다시 정렬한다. 스크립트 쓰기는 onEdit을 부르지 않기 때문이다.
function tmDashboardUpdateRow_(ss, config, data, values) {
  const target = tmDashboardMonthSheet_(ss, config, data.sheetId);
  const sheet = target.sheet;
  const rowNumber = Number(data.row);
  if (!Number.isSafeInteger(rowNumber) || rowNumber < MONTHLY_ATTENDANCE_DATA_START_ROW || rowNumber > sheet.getLastRow()) {
    return { status: 'stale', message: TM_DASHBOARD_STALE_MESSAGE };
  }
  const guard = tmDashboardWriteGuard_(sheet);
  if (guard) return { status: 'disabled', message: guard };
  const range = sheet.getRange(rowNumber, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL);
  const current = range.getValues()[0];
  if (tmDashboardFingerprint_(current) !== String(data.expectedFp || '')) {
    return { status: 'stale', message: TM_DASHBOARD_STALE_MESSAGE };
  }
  const formulas = range.getFormulas()[0];
  const context = tmDashboardWriteContext_(ss, config, target);
  if (context.calendarYear === null) return { status: 'disabled', message: '출석부의 학년도를 확인하지 못했어요. ' + TM_DASHBOARD_CONNECTION_CHECK };
  const changes = [];
  const fields = Object.keys(values).filter(field => Object.prototype.hasOwnProperty.call(TM_DASHBOARD_FIELD_COLUMNS, field));
  if (fields.length !== Object.keys(values).length) return { status: 'invalid', message: '바꿀 수 없는 항목이에요.' };
  for (let index = 0; index < fields.length; index++) {
    const field = fields[index];
    const column = TM_DASHBOARD_FIELD_COLUMNS[field];
    const checked = tmDashboardCheckValue_(field, values[field], context);
    if (checked.message) return { status: 'invalid', message: checked.message };
    if (tmDashboardSameValue_(field, current[column], checked.value)) continue;
    if (String(formulas[column] || '').trim()) {
      return { status: 'disabled', message: '수식이 들어 있는 칸이라 대시보드에서 바꾸지 않았어요. 시트에서 고쳐 주세요.' };
    }
    changes.push({ field: field, column: column, value: checked.value });
  }
  if (!changes.length) return { status: 'applied', unchanged: true, message: '바뀐 내용이 없어요.' };
  const dateChange = changes.find(change => change.field === 'date');
  const eventDate = dateChange ? dateChange.value : tmDashboardDateKey_(current[0], context.calendarYear);
  if (!eventDate) return { status: 'disabled', message: '날짜를 읽을 수 없는 줄이라 저장하지 않았어요. 시트에서 날짜를 고쳐 주세요.' };
  authorizeAttendanceOperation_('historical-manual', eventDate);
  const request = { requests: changes.map(change => ({ updateCells: {
    start: { sheetId: sheet.getSheetId(), rowIndex: rowNumber - 1, columnIndex: change.column },
    rows: [{ values: [tmDashboardCellData_(change.field, change.value)] }],
    fields: 'userEnteredValue'
  } })) };
  const uncertain = { status: 'uncertain', message: '저장 결과를 확인하지 못했어요. ' + target.month + '월 출결 기록에서 바뀐 내용을 확인한 뒤 다시 해 주세요.' };
  let response;
  try {
    response = Sheets.Spreadsheets.batchUpdate(request, ss.getId());
  } catch (err) {
    console.error('대시보드 출결 수정 결과 미확인: ' + String(err && err.message || err));
    return uncertain;
  }
  if (!response || response.spreadsheetId !== ss.getId()) return uncertain;
  // SpreadsheetApp로 다시 읽으면 위에서 읽어 둔 쓰기 전 값이 돌아와 저장된 수정도 '확인하지
  // 못함'이 되었다(2026-10-01 현장). 쓴 값은 Sheets API로 직접 읽어 확인한다(SHEET-DASH-08).
  let written;
  try {
    written = tmDashboardReadRowsFresh_(ss, sheet, rowNumber, rowNumber, MONTHLY_ATTENDANCE_LAST_DATA_COL)[0];
  } catch (err) {
    console.error('대시보드 출결 수정 확인 읽기 실패: ' + String(err && err.message || err));
    return uncertain;
  }
  if (!changes.every(change => tmDashboardSameValue_(change.field, written[change.column], change.value))) return uncertain;
  if (changes.some(change => [0, 1, 6, 7].indexOf(change.column) >= 0)) {
    try {
      clearMonthlyChatResultForRows_(sheet, rowNumber, rowNumber);
    } catch (err) { /* 수정은 끝났다. 시트 편집의 onEdit도 이 실패를 삼킨다 */ }
  }
  if (dateChange) {
    try {
      if (!sortMonthlyAttendanceRows_(sheet, 'date')) reStripeSheet_(sheet);
    } catch (err) { /* 수정은 끝났다. 정렬 실패로 같은 수정을 다시 하지 않는다 */ }
  }
  const only = changes.length === 1 ? changes[0] : null;
  if (only && (only.field === 'report' || only.field === 'attach')) {
    const label = only.field === 'report' ? '신고서' : '첨부';
    return { status: 'applied', message: only.value
      ? label + '를 ‘' + only.value + '’' + (only.value === '해당없음' ? '으로' : '로') + ' 바꿨어요.'
      : label + '를 비웠어요.' };
  }
  return { status: 'applied', message: target.month + '월 출결 기록을 고쳤어요.' };
}

/* ---------- AI 문장과 입력칸 ---------- */

function tmDashboardInputBoxRaw_(sheet) {
  return tmDashboardInputText_(sheet.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL).getValue());
}

// 감지기 안내는 시트 입력칸 기준이다. 대시보드 문장에는 [AI로 입력]으로 바꿔 보여 준다.
function tmDashboardAiMessage_(message) {
  return String(message || '')
    .replace(/잠시 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요\./g, '잠시 뒤 [AI로 입력]을 다시 눌러 주세요.')
    .replace(/1분 뒤 입력칸을 지우고 같은 문장을 다시 입력해 주세요\./g, '1분 뒤 [AI로 입력]을 다시 눌러 주세요.')
    .replace(/입력칸을 지우고 문장에 구분을 함께 적어 다시 입력해 주세요\./g, '문장에 구분을 함께 적은 뒤 [AI로 입력]을 다시 눌러 주세요.')
    .replace(/입력칸을 지우고 /g, '');
}

// 입력칸 문장을 처리해 기록이 끝났을 때만, 입력칸이 아직 그 문장이면 비운다.
// handleAttendanceAiEdit가 잡은 문서 잠금 안에서 부르므로 따로 잠그지 않는다. 비우지 못하면
// 예외를 올려 그 문장을 '입력했지만 입력칸에 남음'으로 표시하게 한다(SHEET-DASH-05).
function tmDashboardResetInputBoxIfSame_(sheet, sentence) {
  if (tmDashboardRedact_(tmDashboardInputBoxRaw_(sheet)) === sentence) resetAttendanceAiInputRow_(sheet);
}

/** 입력칸 문장의 지문이 결과 미확인(uncertain) 또는 입력했지만 남음(entered)으로 남았는지. */
function tmDashboardInputBoxPending_(sheet, raw) {
  const hash = attendanceAiSentenceHash_(raw);
  const saved = attendanceAiUncertainEntries_(PropertiesService.getDocumentProperties()
    .getProperty(ATTENDANCE_AI_UNCERTAIN_PROPERTY_PREFIX + sheet.getSheetId()));
  const found = saved.filter(item => item.h === hash);
  if (!found.length) return '';
  return found.some(item => item.e) ? 'entered' : 'uncertain';
}

/** 화면에 보일 AI 결과 줄. 자유 글 칸은 이메일 모양을 가린다(R17). */
function tmDashboardAiRecords_(records) {
  if (!Array.isArray(records)) return [];
  const text = value => tmDashboardRedact_(String(value === null || value === undefined ? '' : value)).slice(0, 200);
  return records.slice(0, 62).map(record => ({
    date: text(record.date).slice(0, 10), student: text(record.student), category: text(record.category),
    kind: text(record.kind), reason: text(record.reason), period: text(record.period),
    report: text(record.report), attach: text(record.attach)
  }));
}

/** [AI로 입력]. 감지기와 같은 handleAttendanceAiEdit 경로를 그대로 쓴다(SHEET-AI-01). */
function tmDashboardRunAiSentence(input) {
  return tmDashboardWrite_(input, { lock: false }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    const target = tmDashboardMonthSheet_(ss, config, data.sheetId);
    const sentence = String(data.sentence === null || data.sentence === undefined ? '' : data.sentence).trim();
    if (!sentence) return { status: 'invalid', message: '출결 문장을 입력해 주세요.' };
    if (sentence.length > TM_DASHBOARD_SENTENCE_MAX) return { status: 'invalid', message: '출결 문장은 ' + TM_DASHBOARD_SENTENCE_MAX + '자까지 적을 수 있어요.' };
    const sheet = target.sheet;
    const fromInputBox = data.fromInputBox === true;
    // [그래도 다시 입력] 경고를 본 뒤에만 오는 표시다. 이것 없이는 결과 미확인 문장을 다시 처리하지 않는다.
    const confirmed = data.confirmUncertain === true;
    const messages = [];
    const overrides = {
      showMessage: message => messages.push(tmDashboardAiMessage_(message)),
      resetInputRow: targetSheet => { if (fromInputBox) tmDashboardResetInputBoxIfSame_(targetSheet, sentence); }
    };
    if (fromInputBox) {
      // 잠금을 잡은 뒤에 입력칸을 다시 본다. 감지기가 그사이 처리해 비웠으면 stale, 결과를
      // 확인하지 못했거나 입력했지만 남은 문장이면 경고부터 보이게 멈춘다(SHEET-DASH-05, C7).
      overrides.stillCurrent = targetSheet => {
        const raw = tmDashboardInputBoxRaw_(targetSheet);
        if (!raw || tmDashboardRedact_(raw) !== sentence) return false;
        const pending = confirmed ? '' : tmDashboardInputBoxPending_(targetSheet, raw);
        return pending ? pending + '-pending' : true;
      };
    }
    const event = {
      source: ss,
      range: sheet.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, MONTHLY_ATTENDANCE_AI_INPUT_COL, 1,
        MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1),
      value: sentence
    };
    const result = handleAttendanceAiEdit(event, null, overrides) || {};
    const status = result.status === 'check_required' && result.uncertain === true ? 'uncertain' : String(result.status || 'failed');
    const message = status === 'stale' ? '입력칸의 문장이 바뀌어 처리하지 않고 다시 읽었어요.'
      : status === 'uncertain' ? (result.updatedExisting === true
        ? '수정 결과를 확인하지 못했어요. ' + target.month + '월 출결 기록에서 그 줄이 바뀌었는지 확인한 뒤 [↻ 새로 읽기]를 눌러 주세요.'
        : '등록 결과를 확인하지 못했어요. ' + target.month + '월 출결 기록에 줄이 생겼는지 확인한 뒤 [↻ 새로 읽기]를 눌러 주세요.')
      : status === 'uncertain-pending' ? '등록 결과를 확인하지 못한 문장이에요. ' + target.month + '월 출결 기록을 먼저 확인해 주세요.'
      : status === 'entered-pending' ? '이미 출결 기록에 입력한 문장이에요. ' + target.month + '월 출결 기록을 먼저 확인해 주세요.'
      : messages.length ? messages[messages.length - 1] : '';
    const reply = { status: status, month: target.month, rows: Number(result.rows) || 0,
      updatedExisting: result.updatedExisting === true, message: message };
    if (status === 'applied') {
      reply.records = tmDashboardAiRecords_(result.records);
      if (result.inputBoxKept === true) reply.inputBoxKept = true;
    }
    return reply;
  });
}

/** [지우기]. 입력칸이 아직 화면에 보인 그 문장일 때만 비운다(§5.3). */
function tmDashboardClearInputBox(input) {
  return tmDashboardWrite_(input, { lock: true }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    const target = tmDashboardMonthSheet_(ss, config, data.sheetId);
    const raw = tmDashboardInputBoxRaw_(target.sheet);
    const expected = String(data.expectedText === null || data.expectedText === undefined ? '' : data.expectedText).trim();
    if (!raw || !expected || tmDashboardRedact_(raw) !== expected) {
      return { status: 'stale', message: '입력칸의 문장이 바뀌어 지우지 않고 다시 읽었어요.' };
    }
    resetAttendanceAiInputRow_(target.sheet);
    try {
      attendanceAiRecordOutcome_(target.sheet, raw, true);
    } catch (err) { /* 경고용 지문을 지우지 못해도 입력칸은 비웠다 */ }
    return { status: 'applied', message: target.month + '월 입력칸을 비웠어요.' };
  });
}

/* ---------- Google Chat 보내기 (2단계) ----------
 * 보내기는 기존 메뉴 발송 함수(sendTodayClassMessageQueue_, sendTodayPersonalMessageQueue_,
 * sendSelectedRowsPersonalMessagesNow_)를 그대로 부른다. 대시보드는 ui.alert 대신 확인 창을 쓰고,
 * 선생님이 확인 창에서 본 줄(지문)만 보내게 하며, 결과를 모르는 '발송중' 줄은 선생님이 정하기
 * 전까지 보내지 않는다(C7, 설계 §5.4). 결과는 보낸 뒤 시트를 다시 읽어 줄마다 알린다.
 */
const TM_DASHBOARD_SEND_UNCERTAIN = 'Google Chat 발송 결과를 확인하지 못했어요. 다시 보내지 말고 발송기록을 확인해 주세요.';
const TM_DASHBOARD_SEND_PENDING = Object.freeze({ status: 'uncertain',
  message: '앞의 보내기 요청의 결과를 아직 확인하지 못했어요. 다시 보내지 말고 [↻ 새로 읽기]로 결과를 확인해 주세요.' });
const TM_DASHBOARD_QUEUE_CHANGED = '보낼 메시지가 바뀌었어요. 바뀐 내용을 확인한 뒤 다시 보내 주세요.';
const TM_DASHBOARD_STALE_BLOCK = '결과를 확인하지 못한 메시지가 있어 보내지 않았어요. [확인하기]에서 먼저 정해 주세요.';
const TM_DASHBOARD_MESSAGE_MAX = 1000;

function tmDashboardQueueSpec_(kind) {
  if (kind === 'personal') {
    return { kind: 'personal', headers: PERSONAL_MESSAGE_QUEUE_HEADERS, width: PERSONAL_MESSAGE_QUEUE_HEADERS.length,
      status: 6, sentAt: 8, result: 9, text: 4, type: 3, types: PERSONAL_MESSAGE_TYPES, logKind: '개인DM',
      preferred: MESSENGER_PERSONAL_SHEET_NAME, legacy: LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES };
  }
  if (kind === 'class') {
    return { kind: 'class', headers: CLASS_MESSAGE_QUEUE_HEADERS, width: CLASS_MESSAGE_QUEUE_HEADERS.length,
      status: 4, sentAt: 5, result: 6, text: 2, type: 1, types: CLASS_MESSAGE_TYPES, logKind: '단체방',
      preferred: MESSENGER_CLASS_SHEET_NAME, legacy: LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES };
  }
  return null;
}

/** 보낼 메시지 탭을 만들거나 고치지 않고 읽는다. 제목 줄이 다르면 layoutOk가 거짓이다. */
function tmDashboardOpenQueue_(ss, spec) {
  const sheet = tmDashboardQueueSheet_(ss, spec.preferred, spec.legacy);
  if (!sheet) return { sheet: null, layoutOk: true, rows: [] };
  const head = sheet.getRange(1, 1, 1, spec.width).getValues()[0]
    .map(value => String(value === null || value === undefined ? '' : value).trim());
  if (!spec.headers.every((label, index) => head[index] === label)) return { sheet, layoutOk: false, rows: [] };
  return { sheet, layoutOk: true, rows: getQueueRows_(sheet, spec.width) };
}

/** 학생명단 A:C로 보내는 함수와 같은 학생 찾기 표를 만든다(loadStudentRosterForDm_는 시트를 고치므로 쓰지 않는다). */
function tmDashboardRosterMap_(ss, config) {
  const sheet = ss.getSheetByName(String(config.ROSTER_SHEET_NAME || '학생명단').trim());
  if (!sheet || sheet.getLastRow() < 2) return {};
  return buildRosterKeyMap_(sheet.getRange(2, 1, sheet.getLastRow() - 1, ROSTER_HEADERS.length).getValues());
}

function tmDashboardTokenOf_(parts) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,
    'tm-dashboard-send|' + JSON.stringify(parts), Utilities.Charset.UTF_8);
  return tmDashboardHex_(bytes, 16);
}

function tmDashboardLogPreview_(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
}

/** 결과를 모르는 메시지와 맞는 발송기록 한 줄(최근 300줄 안). 없으면 null. */
function tmDashboardFindLogLine_(log, kind, text) {
  const preview = tmDashboardLogPreview_(text);
  if (!preview) return null;
  const found = (log || []).find(line => line.kind === kind && line.text.indexOf(preview.slice(0, 120)) >= 0);
  return found ? { at: found.at, result: found.result } : null;
}

function tmDashboardRecentLog_(ss, config) {
  const sheet = ss.getSheetByName(String(config.CHAT_LOG_SHEET_NAME || '발송기록').trim());
  if (!sheet || sheet.getLastRow() < 2) return [];
  const last = sheet.getLastRow();
  const start = Math.max(2, last - TM_DASHBOARD_LOG_LIMIT + 1);
  return sheet.getRange(start, 1, last - start + 1, 6).getValues().reverse().map(row => ({
    at: tmDashboardTimeText_(row[0]), kind: tmDashboardCellText_(row[1]),
    text: tmDashboardLogPreview_(tmDashboardCellText_(row[4])), result: tmDashboardCellText_(row[5]).slice(0, 40)
  }));
}

/**
 * 보내기 전 미리보기. 메뉴 발송과 같은 묶음(groupPersonalMessageQueueRows_ /
 * groupClassMessageQueueRows_)으로 오늘까지의 '대기' 줄을 모으고, 10분이 지난 '발송중' 줄을
 * staleClaims로 따로 돌려준다(메뉴처럼 몰래 '대기'로 되돌리지 않는다, §5.4). token은 보낼 줄과
 * 결과를 모르는 줄의 지문으로 만든다. 학생 이메일은 돌려주지 않는다(R17).
 */
function tmDashboardBuildQueuePreview_(ss, config, kind, now) {
  const kinds = kind === 'both' ? ['class', 'personal'] : [kind];
  const today = todayKey_();
  const roomName = tmDashboardRedact_(tmDashboardCellText_(config.CLASS_CHAT_SPACE_NAME)).slice(0, 100);
  const out = { kind, today, problem: '', personal: null, class: null, staleClaims: [], expected: {}, sendCount: 0 };
  const tokenParts = [kind, today];
  let rosterMap = null;
  let log = null;
  kinds.forEach(name => {
    const spec = tmDashboardQueueSpec_(name);
    const queue = tmDashboardOpenQueue_(ss, spec);
    const expected = {};
    out.expected[name] = expected;
    if (!queue.layoutOk) {
      out.problem = (name === 'personal' ? '메신저 개인톡 내용' : '메신저 단체톡 내용') + ' 탭의 제목 줄이 달라 보내지 않았어요.';
      return;
    }
    const fps = queue.rows.map(row => tmDashboardHashCells_(row, spec.width));
    queue.rows.forEach((row, index) => {
      if (String(row[spec.status] || '').trim() !== '발송중' || !tmDashboardIsStaleClaim_(row[spec.sentAt], now)) return;
      if (log === null) log = tmDashboardRecentLog_(ss, config);
      const text = tmDashboardCellText_(row[spec.text]);
      out.staleClaims.push({
        kind: name, row: index + 2, fp: fps[index],
        target: name === 'personal' ? tmDashboardRedact_(combineStudentNumberAndName_(row[1], row[2]) || tmDashboardCellText_(row[2])) : roomName,
        text: tmDashboardRedact_(text).slice(0, TM_DASHBOARD_MESSAGE_MAX),
        at: tmDashboardTimeText_(row[spec.sentAt]),
        log: tmDashboardFindLogLine_(log, spec.logKind, text)
      });
      tokenParts.push('stale:' + name + ':' + (index + 2) + ':' + fps[index]);
    });
    const line = rowNumber => ({
      row: rowNumber, fp: fps[rowNumber - 2],
      type: tmDashboardCellText_(queue.rows[rowNumber - 2][spec.type]),
      text: tmDashboardRedact_(tmDashboardCellText_(queue.rows[rowNumber - 2][spec.text])).slice(0, TM_DASHBOARD_MESSAGE_MAX)
    });
    if (name === 'personal') {
      if (rosterMap === null) rosterMap = tmDashboardRosterMap_(ss, config);
      const groups = groupPersonalMessageQueueRows_(queue.rows, today);
      out.personal = groups.map(group => {
        const student = findQueueStudent_(group, rosterMap);
        group.rowNumbers.forEach(rowNumber => { expected[rowNumber] = fps[rowNumber - 2]; });
        return {
          target: tmDashboardRedact_((student && student.combined) || combineStudentNumberAndName_(group.number, group.name) || group.name || group.number),
          hasEmail: !!(student && student.email),
          lines: group.rowNumbers.map(line)
        };
      });
      out.sendCount += out.personal.length;
    } else {
      const grouped = groupClassMessageQueueRows_(queue.rows, today);
      grouped.rowNumbers.forEach(rowNumber => { expected[rowNumber] = fps[rowNumber - 2]; });
      out.class = { target: roomName, lines: grouped.rowNumbers.map(line) };
      if (grouped.rowNumbers.length) {
        out.sendCount += 1;
        if (!tmDashboardCellText_(config.CLASS_CHAT_SPACE_ID)) {
          out.problem = '학급 단톡방이 정해지지 않았어요. Teacher Manager의 [Google 연결 → 출결]에서 골라 주세요.';
        }
      }
    }
    Object.keys(expected).forEach(rowNumber => tokenParts.push(name + ':' + rowNumber + ':' + expected[rowNumber]));
  });
  out.token = tmDashboardTokenOf_(tokenParts);
  return out;
}

function tmDashboardPreviewReply_(preview) {
  return { status: 'ok', kind: preview.kind, today: preview.today, problem: preview.problem,
    personal: preview.personal, class: preview.class, staleClaims: preview.staleClaims, previewToken: preview.token };
}

/** [보내기] 전 미리보기(읽기만). */
function tmDashboardPreviewQueue(req) {
  return tmDashboardServe_(req, ss => {
    const kind = req && typeof req === 'object' ? String(req.kind || '') : '';
    if (['personal', 'class', 'both'].indexOf(kind) < 0) return { status: 'invalid', message: '보낼 메시지 종류를 확인하지 못했어요.' };
    const config = readAttendanceConfigStrict_(ss);
    return tmDashboardPreviewReply_(tmDashboardBuildQueuePreview_(ss, config, kind, new Date()));
  });
}

/** 보낸 뒤(또는 예외 뒤) 미리보기에 있던 줄을 다시 읽어 줄마다 실제 상태를 알린다. */
function tmDashboardQueueOutcome_(ss, preview) {
  const results = [];
  ['class', 'personal'].forEach(name => {
    const spec = tmDashboardQueueSpec_(name);
    const groups = name === 'personal' ? (preview.personal || []) : (preview.class && preview.class.lines.length ? [preview.class] : []);
    if (!groups.length) return;
    const queue = tmDashboardOpenQueue_(ss, spec);
    // 보내는 함수가 방금 쓴 상태를 SpreadsheetApp가 쓰기 전 값으로 돌려줄 수 있어 Sheets API로 다시 읽는다.
    const lastLine = Math.max.apply(null, groups.map(group => Math.max.apply(null, group.lines.map(line => line.row))));
    const fresh = queue.sheet && queue.layoutOk ? tmDashboardReadRowsFresh_(ss, queue.sheet, 2, lastLine, spec.width) : [];
    groups.forEach(group => {
      const states = group.lines.map(line => {
        const row = fresh[line.row - 2] || [];
        const status = String(row[spec.status] || '').trim();
        const result = tmDashboardQueueResult_(row[spec.result]);
        if (status === '보냄') return { state: result.indexOf('다시 보내지 말고') >= 0 ? 'record-check' : 'sent', detail: '' };
        if (status === '발송중') return { state: 'unknown', detail: '' };
        if (status === '실패') return { state: 'failed', detail: result };
        if (status === '대기') return result ? { state: 'stopped', detail: result } : { state: 'waiting', detail: '' };
        return { state: 'changed', detail: '' };
      });
      // 한 학생의 줄은 한 번에 보내므로 보통 상태가 같다. 다르면 가장 확인이 필요한 상태를 보인다.
      const order = ['unknown', 'record-check', 'failed', 'stopped', 'changed', 'waiting', 'sent'];
      const worst = states.slice().sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state))[0];
      results.push({ kind: name, target: group.target, lines: group.lines.length, state: worst.state, detail: worst.detail });
    });
  });
  return results;
}

/**
 * [보내기]. 미리보기를 다시 만들어 화면의 token과 같을 때만 보낸다. 결과를 모르는 '발송중' 줄이
 * 있으면 보내지 않는다(decide-stale). 보내는 함수 안의 줄 잡기(발송중 표시)가 같은 줄을 두 번
 * 보내지 않게 하고, 같은 요청 번호는 한 번만 처리한다. 보낸 뒤 예외가 나면 다시 보내지 않고
 * 시트를 다시 읽어 결과를 알린다(C7).
 */
function tmDashboardSendQueue(req) {
  return tmDashboardWrite_(req, { lock: false, pending: TM_DASHBOARD_SEND_PENDING, pendingTtl: 21600 }, (ss, config) => {
    const data = req && typeof req === 'object' ? req : {};
    const kind = String(data.kind || '');
    if (['personal', 'class', 'both'].indexOf(kind) < 0) return { status: 'invalid', message: '보낼 메시지 종류를 확인하지 못했어요.' };
    const preview = tmDashboardBuildQueuePreview_(ss, config, kind, new Date());
    const previewReply = tmDashboardPreviewReply_(preview);
    if (preview.problem) return { status: 'disabled', message: preview.problem, preview: previewReply };
    if (preview.staleClaims.length) return { status: 'decide-stale', message: TM_DASHBOARD_STALE_BLOCK, preview: previewReply };
    if (preview.token !== String(data.previewToken || '')) return { status: 'changed', message: TM_DASHBOARD_QUEUE_CHANGED, preview: previewReply };
    if (!preview.sendCount) return { status: 'applied', message: '보낼 메시지가 없어요.', results: [] };
    if (kind === 'both') {
      // 메뉴의 한 번에 보내기처럼 먼저 연결 상태를 본다. 확인하지 못했으면(null) 보내지 않는다(§5.4).
      let connected = null;
      try {
        connected = tmDashboardCentralStatus_(ss).connected;
      } catch (err) {
        connected = null;
      }
      if (connected === false) return { status: 'disabled', tone: 'warn', message: 'Google Chat 연결이 필요해요. ' + TM_DASHBOARD_CONNECTION_CHECK };
      if (connected !== true) return { status: 'disabled', tone: 'muted', message: 'Google Chat 연결 상태를 확인하지 못해 보내지 않았어요.' };
    }
    const acceptFor = name => (rowNumber, row) => preview.expected[name][rowNumber] !== undefined
      && preview.expected[name][rowNumber] === tmDashboardHashCells_(row, tmDashboardQueueSpec_(name).width);
    const options = name => ({ skipStaleRecovery: true, acceptRow: acceptFor(name) });
    let blocked = false;
    let thrown = false;
    try {
      if ((kind === 'class' || kind === 'both') && preview.class && preview.class.lines.length) {
        try {
          sendTodayClassMessageQueue_(preview.today, options('class'));
        } catch (err) {
          // 연결 문제면 보내는 함수가 줄을 '대기'로 되돌리고 기록을 남겼다. 그 밖의 실패는 '실패'로 남겼다.
          // 어느 쪽인지는 아래에서 시트를 다시 읽어 알린다. 개인톡은 이어서 보내지 않는다.
          if (isCentralChatConnectionError_(err)) blocked = true;
          else throw err;
        }
      }
      if (!blocked && (kind === 'personal' || kind === 'both') && preview.personal && preview.personal.length) {
        const result = sendTodayPersonalMessageQueue_(preview.today, options('personal'));
        if (result && result.chatApiSetupBlocked) blocked = true;
      }
    } catch (err) {
      thrown = true;
      try {
        console.error('대시보드 보내기 결과 미확인: ' + String(err && err.message || err));
      } catch (ignored) { /* 기록 실패는 화면 응답을 막지 않는다 */ }
    }
    let results = [];
    try {
      results = tmDashboardQueueOutcome_(ss, preview);
    } catch (err) {
      thrown = true;
    }
    if (thrown) return { status: 'uncertain', message: TM_DASHBOARD_SEND_UNCERTAIN, results };
    if (blocked) {
      return { status: 'applied', blocked: true, results,
        message: '연결 문제로 멈췄어요. 보내지 않은 줄은 ‘대기’로 남았어요. ' + TM_DASHBOARD_CONNECTION_CHECK };
    }
    return { status: 'applied', results, message: '' };
  });
}

/**
 * 결과를 모르는 '발송중' 줄을 선생님이 정한다. 받았으면 '제외', 받지 못했으면 '대기'로 되돌려
 * 다음 [보내기]에 다시 넣는다. 잠금 안에서 줄 지문·'발송중'·10분 지남을 모두 다시 확인하고,
 * 하나라도 다르면 아무것도 쓰지 않는다(C7: 선생님이 정한 줄만 다시 보낸다).
 */
function tmDashboardResolveStaleClaims(input) {
  return tmDashboardWrite_(input, { lock: true }, ss => {
    const data = input && typeof input === 'object' ? input : {};
    const decision = String(data.decision || '');
    if (decision !== 'exclude' && decision !== 'requeue') return { status: 'invalid', message: '정할 내용을 확인하지 못했어요.' };
    const rows = Array.isArray(data.rows) ? data.rows.slice(0, 200) : [];
    if (!rows.length) return { status: 'invalid', message: '정할 메시지를 확인하지 못했어요.' };
    const now = new Date();
    const plan = {};
    for (let index = 0; index < rows.length; index++) {
      const item = rows[index] || {};
      const spec = tmDashboardQueueSpec_(String(item.kind || ''));
      const rowNumber = Number(item.row);
      if (!spec || !Number.isSafeInteger(rowNumber) || rowNumber < 2) return { status: 'invalid', message: '정할 메시지를 확인하지 못했어요.' };
      if (!plan[spec.kind]) plan[spec.kind] = { spec, queue: tmDashboardOpenQueue_(ss, spec), rows: [] };
      const entry = plan[spec.kind];
      const row = entry.queue.rows[rowNumber - 2];
      if (!entry.queue.sheet || !entry.queue.layoutOk || !row
          || tmDashboardHashCells_(row, spec.width) !== String(item.expectedFp || '')
          || String(row[spec.status] || '').trim() !== '발송중' || !tmDashboardIsStaleClaim_(row[spec.sentAt], now)) {
        return { status: 'stale', message: '메시지의 상태가 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인해 주세요.' };
      }
      entry.rows.push(rowNumber);
    }
    Object.keys(plan).forEach(name => {
      const entry = plan[name];
      const spec = entry.spec;
      entry.rows.forEach(rowNumber => {
        const row = entry.queue.rows[rowNumber - 2];
        if (decision === 'exclude') {
          const note = name === 'personal' ? '선생님 확인: 받음' : '선생님 확인: 단톡방에 올라옴';
          setQueueRowsResult_(entry.queue.sheet, [rowNumber], spec.status + 1, spec.sentAt + 1, spec.result + 1, '제외', row[spec.sentAt], note);
        } else {
          setQueueRowsResult_(entry.queue.sheet, [rowNumber], spec.status + 1, spec.sentAt + 1, spec.result + 1, '대기', '', '');
        }
      });
    });
    return { status: 'applied', message: decision === 'exclude' ? '보낸 것으로 정리했어요.' : '다시 보낼 목록에 넣었어요.' };
  });
}

function tmDashboardMessageText_(raw) {
  const text = normalizeMessageLine_(raw);
  if (!text) return { message: '메시지 내용을 입력해 주세요.' };
  if (text.length > TM_DASHBOARD_MESSAGE_MAX) return { message: '메시지는 ' + TM_DASHBOARD_MESSAGE_MAX + '자까지 적을 수 있어요.' };
  // 시트에 글자로 넣으면 =로 시작하는 내용은 수식이 된다.
  if (/^[=+@]/.test(text) || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(text)) {
    return { message: '=, +, @로 시작하는 내용이나 특수 문자는 넣을 수 없어요.' };
  }
  return { value: text };
}

/** [보낼 메시지에 추가]. 늘 '대기'·'직접입력'으로 넣는다. 같은 날짜·같은 대상·같은 내용의 '대기' 줄이 있으면 넣지 않는다. */
function tmDashboardAddQueueItem(input) {
  return tmDashboardWrite_(input, { lock: true }, (ss, config) => {
    const data = input && typeof input === 'object' ? input : {};
    const spec = tmDashboardQueueSpec_(String(data.kind || ''));
    if (!spec) return { status: 'invalid', message: '개인톡인지 단체톡인지 골라 주세요.' };
    const sendDate = String(data.sendDate || '').trim();
    const parsed = attendanceAiParseDateKey_(sendDate);
    if (!parsed || parsed.key !== sendDate) return { status: 'invalid', message: '보낼 날짜를 골라 주세요.' };
    if (sendDate < todayKey_()) return { status: 'invalid', message: '보낼 날짜는 오늘이나 그 뒤로 골라 주세요.' };
    const type = String(data.type || '').trim() || '기타';
    if (spec.types.indexOf(type) < 0) return { status: 'invalid', message: '종류를 골라 주세요.' };
    const checked = tmDashboardMessageText_(data.content);
    if (checked.message) return { status: 'invalid', message: checked.message };
    const content = checked.value;
    if (spec.kind === 'class') {
      // 메뉴의 단체톡 넣기와 같은 함수다(같은 날짜·같은 내용의 '보냄' 아닌 줄이 있으면 넣지 않는다).
      authorizeAttendanceOperation_('automatic', '');
      const added = appendClassMessageQueueLines_([content], sendDate, '직접입력', '대기', type);
      if (!added.added) return { status: 'applied', duplicate: true, message: '같은 날짜에 같은 단체톡 메시지가 이미 있어요.' };
      return { status: 'applied', message: '단체톡 메시지를 보낼 목록에 넣었어요.' };
    }
    const label = String(data.student || '').trim();
    const rosterSheet = ss.getSheetByName(String(config.ROSTER_SHEET_NAME || '학생명단').trim());
    const rosterRows = rosterSheet && rosterSheet.getLastRow() >= 2
      ? rosterSheet.getRange(2, 1, rosterSheet.getLastRow() - 1, 2).getValues() : [];
    const matches = rosterRows.filter(row => combineStudentNumberAndName_(row[0], row[1]) === label);
    if (!label || matches.length !== 1) return { status: 'invalid', message: '학생은 학생명단의 번호+이름으로 골라 주세요.' };
    const number = String(matches[0][0] === null || matches[0][0] === undefined ? '' : matches[0][0]).trim();
    const name = String(matches[0][1] === null || matches[0][1] === undefined ? '' : matches[0][1]).trim();
    authorizeAttendanceOperation_('automatic', '');
    // 메뉴의 자동 넣기(appendPersonalMessageQueueItemsForAutomation)와 같은 줄 모양이다.
    const sheet = ensurePersonalMessageQueueSheet_(ss);
    const duplicate = getQueueRows_(sheet, spec.width).some(row => formatQueueDateKey_(row[0], '') === sendDate
      && String(row[1] || '').trim() === number && String(row[2] || '').trim() === name
      && normalizeMessageLine_(row[4]) === content && String(row[6] || '').trim() === '대기');
    if (duplicate) return { status: 'applied', duplicate: true, message: '같은 날짜에 같은 개인톡 메시지가 이미 있어요.' };
    sheet.getRange(sheet.getLastRow() + 1, 1, 1, spec.width)
      .setValues([[sendDate, number, name, type, content, '직접입력', '대기', '', '', '']]);
    return { status: 'applied', message: label + ' 개인톡 메시지를 보낼 목록에 넣었어요.' };
  });
}

/** 보낼 메시지 줄 하나를 잠금 안에서 다시 읽어, 지문이 같고 '대기'일 때만 돌려준다. */
function tmDashboardPendingQueueRow_(ss, data) {
  const spec = tmDashboardQueueSpec_(String(data.kind || ''));
  const rowNumber = Number(data.row);
  if (!spec || !Number.isSafeInteger(rowNumber) || rowNumber < 2) return { reply: { status: 'invalid', message: '메시지를 확인하지 못했어요.' } };
  const sheet = tmDashboardQueueSheet_(ss, spec.preferred, spec.legacy);
  if (!sheet || rowNumber > sheet.getLastRow()) return { reply: { status: 'stale', message: TM_DASHBOARD_STALE_MESSAGE } };
  const head = sheet.getRange(1, 1, 1, spec.width).getValues()[0].map(value => String(value === null || value === undefined ? '' : value).trim());
  if (!spec.headers.every((label, index) => head[index] === label)) {
    return { reply: { status: 'disabled', message: '보낼 메시지 탭의 제목 줄이 달라 바꾸지 않았어요.' } };
  }
  const range = sheet.getRange(rowNumber, 1, 1, spec.width);
  const row = range.getValues()[0];
  // 보냄·발송중·실패·제외 줄은 바꾸거나 지우지 않는다. 받은 메시지를 다시 보내게 될 수 있다(C7).
  if (tmDashboardHashCells_(row, spec.width) !== String(data.expectedFp || '') || String(row[spec.status] || '').trim() !== '대기') {
    return { reply: { status: 'stale', message: TM_DASHBOARD_STALE_MESSAGE } };
  }
  if (range.getFormulas()[0].some(formula => String(formula || '').trim())) {
    return { reply: { status: 'disabled', message: '수식이 들어 있는 줄이라 대시보드에서 바꾸지 않았어요. 시트에서 고쳐 주세요.' } };
  }
  return { spec, sheet, rowNumber, row };
}

/**
 * [삭제]. '대기' 줄의 칸을 비운다(행 삭제가 아니다, 설계 §3.5·Q22). 메뉴 발송이 시트를 한 번
 * 읽은 뒤 줄 번호로 결과를 적으므로, 줄을 지우면 아래 학생의 줄 번호가 밀려 다른 학생 줄이 '보냄'이
 * 될 수 있다. 비운 줄은 '대기'가 아니라 잡히지 않고, 묶을 때도 건너뛴다.
 */
function tmDashboardDeleteQueueRow(input) {
  return tmDashboardWrite_(input, { lock: true }, ss => {
    const found = tmDashboardPendingQueueRow_(ss, input && typeof input === 'object' ? input : {});
    if (found.reply) return found.reply;
    found.sheet.getRange(found.rowNumber, 1, 1, found.spec.width).clearContent();
    return { status: 'applied', message: '메시지를 삭제했어요.' };
  });
}

/** [수정]. '대기' 줄의 내용 칸만 바꾼다. */
function tmDashboardEditQueueText(input) {
  return tmDashboardWrite_(input, { lock: true }, ss => {
    const data = input && typeof input === 'object' ? input : {};
    const checked = tmDashboardMessageText_(data.content);
    if (checked.message) return { status: 'invalid', message: checked.message };
    const found = tmDashboardPendingQueueRow_(ss, data);
    if (found.reply) return found.reply;
    if (normalizeMessageLine_(found.row[found.spec.text]) === checked.value) return { status: 'applied', unchanged: true, message: '바뀐 내용이 없어요.' };
    found.sheet.getRange(found.rowNumber, found.spec.text + 1).setValue(checked.value);
    return { status: 'applied', message: '메시지를 고쳤어요.' };
  });
}

/** 월 탭의 독려 대상 줄을 읽는다. 지문이 다르거나 보낼 것이 없는 줄은 excluded로 돌려준다. */
function tmDashboardBuildReminderPreview_(ss, config, rows) {
  const out = { groups: [], excluded: [], targets: [], problem: '' };
  const tokenParts = ['reminders'];
  const rosterMap = tmDashboardRosterMap_(ss, config);
  const sheets = {};
  const seen = {};
  (rows || []).slice(0, 200).forEach(item => {
    const data = item && typeof item === 'object' ? item : {};
    const rowNumber = Number(data.row);
    const key = String(data.sheetId) + ':' + rowNumber;
    if (seen[key]) return;
    seen[key] = true;
    let target;
    try {
      if (!sheets[data.sheetId]) sheets[data.sheetId] = tmDashboardMonthSheet_(ss, config, data.sheetId);
      target = sheets[data.sheetId];
    } catch (err) {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'changed' });
      return;
    }
    const sheet = target.sheet;
    if (!Number.isSafeInteger(rowNumber) || rowNumber < MONTHLY_ATTENDANCE_DATA_START_ROW || rowNumber > sheet.getLastRow()) {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'changed' });
      return;
    }
    const values = sheet.getRange(rowNumber, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues()[0];
    const fp = tmDashboardFingerprint_(values);
    if (fp !== String(data.expectedFp || '')) {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'changed' });
      return;
    }
    const status = String(values[8] || '').trim();
    if (status === '보냄' && String(values[11] || '').trim() === attendanceChatSignatureV2_(values)) {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'current' });
      return;
    }
    // 보내는 중이거나 결과를 모르는 줄은 다시 보내지 않는다(C7). 메뉴의 줄 잡기는 10분 뒤 다시 잡지만 대시보드는 막는다.
    if (status === '발송중') {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'sending' });
      return;
    }
    const group = buildAttendanceChatLinesForRow_(sheet.getName(), rowNumber, values, todayKey_());
    if (!group.lines.length) {
      out.excluded.push({ sheetId: data.sheetId, row: rowNumber, reason: 'no-missing' });
      return;
    }
    const student = findQueueStudent_(group, rosterMap);
    const text = group.name
      ? group.name + ' 학생, 확인할 내용입니다.\n\n- ' + group.lines.join('\n- ')
      : group.lines.join('\n');
    out.groups.push({
      sheetId: target.sheet.getSheetId(), row: rowNumber, month: target.month,
      target: tmDashboardRedact_((student && student.combined) || tmDashboardCellText_(values[1])),
      date: tmDashboardDateKey_(values[0], getAttendanceAiCalendarYear_(config.SCHOOL_YEAR, target.month)),
      hasEmail: !!(student && student.email),
      text: tmDashboardRedact_(text).slice(0, TM_DASHBOARD_MESSAGE_MAX)
    });
    out.targets.push({ sheet, rowNumber, fp });
    tokenParts.push(target.sheet.getSheetId() + ':' + rowNumber + ':' + fp);
  });
  out.token = tmDashboardTokenOf_(tokenParts);
  return out;
}

/** 독려 개인톡 미리보기(읽기만). 학생 이메일은 돌려주지 않는다. */
function tmDashboardPreviewRowReminders(req) {
  return tmDashboardServe_(req, ss => {
    const config = readAttendanceConfigStrict_(ss);
    const preview = tmDashboardBuildReminderPreview_(ss, config, req && Array.isArray(req.rows) ? req.rows : []);
    return { status: 'ok', groups: preview.groups, excluded: preview.excluded, previewToken: preview.token };
  });
}

/**
 * 선택한 줄의 신고서·첨부 미제출 독려 개인톡. 메뉴의 [선택 행 미제출 서류 Google Chat 개인톡
 * 보내기]와 같은 sendSelectedRowsPersonalMessagesNow_를 탭마다 부르고, 줄 지문이 확인 창과 같을
 * 때만 보낸다. 결과는 보낸 뒤 그 줄의 I·K열을 다시 읽어 알린다.
 */
function tmDashboardSendRowReminders(req) {
  return tmDashboardWrite_(req, { lock: false, pending: TM_DASHBOARD_SEND_PENDING, pendingTtl: 21600 }, (ss, config) => {
    const data = req && typeof req === 'object' ? req : {};
    const preview = tmDashboardBuildReminderPreview_(ss, config, Array.isArray(data.rows) ? data.rows : []);
    const previewReply = { status: 'ok', groups: preview.groups, excluded: preview.excluded, previewToken: preview.token };
    if (preview.token !== String(data.previewToken || '')) return { status: 'changed', message: TM_DASHBOARD_QUEUE_CHANGED, preview: previewReply };
    if (!preview.targets.length) return { status: 'applied', message: '보낼 독려 개인톡이 없어요.', results: [] };
    const bySheet = new Map();
    preview.targets.forEach(item => {
      if (!bySheet.has(item.sheet)) bySheet.set(item.sheet, []);
      bySheet.get(item.sheet).push(item);
    });
    let blocked = false;
    let thrown = false;
    try {
      bySheet.forEach((items, sheet) => {
        if (blocked) return;
        const expected = {};
        items.forEach(item => { expected[item.rowNumber] = item.fp; });
        const rowGuard = rowIdx => expected[rowIdx] !== undefined && rowIdx <= sheet.getLastRow()
          && tmDashboardFingerprint_(sheet.getRange(rowIdx, 1, 1, MONTHLY_ATTENDANCE_LAST_DATA_COL).getValues()[0]) === expected[rowIdx];
        const result = sendSelectedRowsPersonalMessagesNow_(sheet, items.map(item => item.rowNumber), { rowGuard });
        if (result && result.chatApiSetupBlocked) blocked = true;
      });
    } catch (err) {
      thrown = true;
      try {
        console.error('대시보드 독려 보내기 결과 미확인: ' + String(err && err.message || err));
      } catch (ignored) { /* 기록 실패는 화면 응답을 막지 않는다 */ }
    }
    const results = [];
    try {
      preview.targets.forEach((item, index) => {
        // 잡을 때 읽은 쓰기 전 값('발송중')이 돌아오지 않게 Sheets API로 다시 읽는다(2026-10-01 현장).
        const values = tmDashboardReadRowsFresh_(ss, item.sheet, item.rowNumber, item.rowNumber, MONTHLY_ATTENDANCE_LAST_DATA_COL)[0];
        const status = String(values[8] || '').trim();
        const detail = tmDashboardQueueResult_(values[10]);
        const group = preview.groups[index];
        const state = status === '보냄' ? (detail.indexOf('다시 보내지 말고') >= 0 ? 'record-check' : 'sent')
          : status === '발송중' ? 'unknown' : status === '실패' ? 'failed' : status === '연결필요' ? 'stopped' : 'waiting';
        results.push({ kind: 'reminder', target: group.target, date: group.date, state, detail: state === 'failed' || state === 'stopped' ? detail : '' });
      });
    } catch (err) {
      thrown = true;
    }
    if (thrown) return { status: 'uncertain', message: TM_DASHBOARD_SEND_UNCERTAIN, results };
    if (blocked) return { status: 'applied', blocked: true, results, message: '연결 문제로 멈췄어요. ' + TM_DASHBOARD_CONNECTION_CHECK };
    return { status: 'applied', results, message: '' };
  });
}

// ===== BEGIN TM DASHBOARD PAGE =====
// scripts/embed_dashboard.py가 Teacher Manager 원본의 sheet-dashboard 폴더에서 만든다. 손으로 고치지 않는다.
// 들어 있는 외부 코드: Preact 10.29.8 · preact/hooks 10.29.8 (MIT), htm 3.1.1 (Apache-2.0).
// Preact 10.29.8 license:
//   The MIT License (MIT)
//
//   Copyright (c) 2015-present Jason Miller
//
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction, including without limitation the rights
//   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//   copies of the Software, and to permit persons to whom the Software is
//   furnished to do so, subject to the following conditions:
//
//   The above copyright notice and this permission notice shall be included in all
//   copies or substantial portions of the Software.
//
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//   SOFTWARE.
// htm 3.1.1 — Copyright 2018 Google Inc.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file
// except in compliance with the License. You may obtain a copy of the License at
// http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software distributed under the
// License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
// either express or implied. See the License for the specific language governing permissions
// and limitations under the License.
const TM_DASHBOARD_CSS_ = [
  ":root{\n",
  "  --tm-blue:#3182F6; --tm-blue-soft:#E8F3FF;\n",
  "  --tm-ink:#191F28; --tm-sub:#6B7684; --tm-faint:#8B95A1;\n",
  "  --tm-frame:#D1D6DB; --tm-line:#E5E8EB; --tm-fill:#F2F4F6; --tm-surface:#F9FAFB;\n",
  "  --tm-green:#00A05C; --tm-green-soft:#E6F6EE;\n",
  "  --tm-amber:#D9820B; --tm-amber-soft:#FFF4DE; --tm-amber-line:#FFD591;\n",
  "  --lit-ink2:#333D4B; --lit-amber-text:#8A5A10; --lit-amber-bg:#FFFDF7;\n",
  "  --lit-green-text:#087443; --lit-green-line:#BDE8D3;\n",
  "  --font:\"Pretendard Variable\",Pretendard,\"Malgun Gothic\",\"Apple SD Gothic Neo\",system-ui,sans-serif;\n",
  "}\n",
  "*{box-sizing:border-box}\n",
  "html,body{margin:0;background:#fff;color:var(--tm-ink);font-family:var(--font);font-size:14px;letter-spacing:-0.1px}\n",
  "button,input,select,textarea{font-family:inherit;font-size:inherit;color:inherit}\n",
  "#app{padding:0 20px 24px}\n",
  ".boot{color:var(--tm-sub);padding:18px 0}\n",
  "\n",
  ".head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:6px 0 12px;flex-wrap:wrap}\n",
  ".head h1{margin:0;font-size:19px;font-weight:700;letter-spacing:-0.3px}\n",
  ".head .sub{margin:3px 0 0;color:var(--tm-sub);font-size:13px}\n",
  ".head-right{display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:flex-end;margin-left:auto}\n",
  ".tabs{display:flex;gap:2px;border-bottom:1px solid var(--tm-line);margin-bottom:16px;position:sticky;top:0;background:#fff;z-index:3;padding-top:4px;overflow-x:auto;scrollbar-width:none}\n",
  ".tab{border:0;background:none;padding:9px 14px 10px;font-size:14px;color:var(--tm-sub);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:600;white-space:nowrap;flex:none}\n",
  ".tab.on{color:var(--tm-ink);border-bottom-color:var(--tm-ink)}\n",
  ".tab .cnt{display:inline-block;min-width:18px;padding:0 5px;margin-left:5px;border-radius:9px;font-size:11.5px;line-height:18px;text-align:center}\n",
  ".tab .cnt.warn{background:var(--tm-amber-soft);color:var(--lit-amber-text)}\n",
  ".tab .cnt.muted{background:var(--tm-fill);color:var(--tm-sub)}\n",
  "\n",
  ".chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap;line-height:18px;border:1px solid transparent}\n",
  ".chip.muted{background:var(--tm-fill);color:var(--tm-sub)}\n",
  ".chip.warn{background:var(--tm-amber-soft);color:var(--lit-amber-text);border-color:var(--tm-amber-line)}\n",
  ".chip.ok{background:var(--tm-green-soft);color:var(--lit-green-text);border-color:var(--lit-green-line)}\n",
  ".chip.lg{padding:4px 10px;font-size:12.5px}\n",
  ".tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:12px;background:var(--tm-fill);color:var(--lit-ink2);white-space:nowrap}\n",
  ".ai{display:inline-block;padding:0 5px;border-radius:4px;font-size:11px;font-weight:700;color:var(--tm-sub);box-shadow:inset 0 0 0 1px var(--tm-frame)}\n",
  "\n",
  ".btn{border:0;border-radius:8px;background:var(--tm-blue);color:#fff;font-weight:600;padding:8px 14px;cursor:pointer;white-space:nowrap}\n",
  ".btn:hover{filter:brightness(.95)}\n",
  ".btn[disabled],.btn-tonal[disabled],.btn-quiet[disabled]{background:var(--tm-fill);color:var(--tm-faint);cursor:default;filter:none;border-color:var(--tm-line)}\n",
  ".btn-tonal{border:0;border-radius:8px;background:var(--tm-blue-soft);color:var(--tm-blue);font-weight:600;padding:7px 12px;cursor:pointer;white-space:nowrap}\n",
  ".btn-quiet{border:1px solid var(--tm-line);border-radius:8px;background:#fff;color:var(--lit-ink2);font-weight:500;padding:6px 11px;cursor:pointer;white-space:nowrap}\n",
  ".btn-quiet:hover,.btn-tonal:hover{filter:brightness(.97)}\n",
  ".btn-sm{padding:4px 9px;font-size:12.5px;border-radius:7px}\n",
  ".link{border:0;background:none;padding:0;color:var(--tm-blue);font-weight:600;cursor:pointer;font-size:12.5px;white-space:nowrap;text-decoration:none}\n",
  ".link[disabled]{color:var(--tm-faint);cursor:default}\n",
  ".actions{display:flex;gap:6px;justify-content:flex-end;align-items:center;flex-wrap:wrap}\n",
  "\n",
  ".card{border:1px solid var(--tm-line);border-radius:12px;background:#fff;padding:16px 18px}\n",
  ".card-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:12px}\n",
  ".card-h h3{margin:0;font-size:15px;font-weight:700}\n",
  ".card-h .note{font-size:12px;color:var(--tm-faint);text-align:right}\n",
  ".grid{display:grid;gap:14px}\n",
  ".g-2{grid-template-columns:repeat(2,minmax(0,1fr))}\n",
  ".g-32{grid-template-columns:minmax(0,1.65fr) minmax(0,1fr)}\n",
  ".mt{margin-top:14px}\n",
  ".row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}\n",
  ".spacer{flex:1}\n",
  ".grow{flex:1;min-width:0}\n",
  ".muted{color:var(--tm-sub)} .faint{color:var(--tm-faint)} .small{font-size:12.5px}\n",
  "\n",
  ".filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;border-radius:12px;background:var(--tm-surface);border:1px solid var(--tm-line);margin-bottom:14px}\n",
  ".seg{display:inline-flex;background:#fff;border:1px solid var(--tm-line);border-radius:9px;padding:2px;max-width:100%;overflow-x:auto;scrollbar-width:none}\n",
  ".seg button{border:0;background:none;padding:5px 10px;border-radius:7px;cursor:pointer;color:var(--tm-sub);font-weight:600;font-size:13px;white-space:nowrap;flex:none}\n",
  ".seg button.on{background:var(--tm-ink);color:#fff}\n",
  ".sel{border:1px solid var(--tm-line);border-radius:8px;background:#fff;padding:6px 8px;font-size:13px}\n",
  ".search{border:1px solid var(--tm-line);border-radius:8px;background:#fff;padding:6px 10px;font-size:13px;min-width:170px}\n",
  ".flabel{font-size:12px;color:var(--tm-faint);font-weight:600;margin-left:4px}\n",
  "\n",
  ".kgrid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px 14px}\n",
  ".kg{font-size:12px;font-weight:700;color:var(--tm-faint);padding-bottom:4px;border-bottom:1px solid var(--tm-line);display:flex;justify-content:space-between;gap:8px}\n",
  ".kg span{font-weight:500}\n",
  ".kg.a{grid-column:span 3}.kg.b{grid-column:span 2}\n",
  ".kpi{border:1px solid var(--tm-line);border-radius:12px;padding:13px 15px 12px;background:#fff;min-width:0;display:flex;flex-direction:column}\n",
  ".kpi.warn{border:1.5px solid var(--tm-amber-line);background:var(--lit-amber-bg)}\n",
  ".kpi-top{display:flex;justify-content:space-between;align-items:center;gap:6px;min-height:22px}\n",
  ".kpi-l{font-size:13px;color:var(--lit-ink2);font-weight:600}\n",
  ".kpi-v{font-size:30px;font-weight:700;letter-spacing:-0.8px;margin:6px 0 2px;line-height:1.1}\n",
  ".kpi-v small{font-size:14px;font-weight:600;color:var(--tm-sub);margin-left:2px;letter-spacing:0}\n",
  ".kpi-s{font-size:12.5px;color:var(--tm-sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n",
  ".kpi-d{font-size:12px;color:var(--tm-faint);margin-top:4px}\n",
  ".kpi-a{margin-top:auto;padding-top:8px;display:flex;justify-content:flex-end}\n",
  "\n",
  ".chart svg{display:block;width:100%;height:auto;overflow:visible}\n",
  ".legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--lit-ink2)}\n",
  ".lk{display:inline-block;width:18px;height:2px;vertical-align:4px;margin-right:5px}\n",
  ".lk.solid{background:var(--tm-ink)}\n",
  ".lk.hollow{background:var(--tm-faint)}\n",
  ".donut-wrap{display:flex;align-items:center;gap:18px}\n",
  ".dleg{flex:1;min-width:0;display:grid;gap:8px}\n",
  ".dleg div{display:flex;align-items:center;gap:8px;font-size:13px}\n",
  ".dleg .sw{width:10px;height:10px;border-radius:3px;flex:none}\n",
  ".dleg .v{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--lit-ink2)}\n",
  ".dleg .p{width:38px;text-align:right;color:var(--tm-faint);font-variant-numeric:tabular-nums}\n",
  ".bars{display:grid;gap:9px}\n",
  ".bar{display:grid;grid-template-columns:110px 1fr;align-items:center;gap:10px;font-size:13px}\n",
  ".bar .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--lit-ink2)}\n",
  ".bar .tr{display:flex;align-items:center;gap:6px;min-width:0}\n",
  ".bar .fl{height:14px;background:var(--tm-sub);border-radius:0 4px 4px 0;min-width:3px}\n",
  ".bar .vl{font-size:12.5px;color:var(--lit-ink2);font-variant-numeric:tabular-nums;white-space:nowrap}\n",
  ".meter{margin-bottom:14px}\n",
  ".meter:last-child{margin-bottom:0}\n",
  ".meter-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px}\n",
  ".meter-top b{font-weight:700}\n",
  ".meter-track{height:8px;border-radius:4px;background:var(--tm-green-soft);margin:7px 0 5px;overflow:hidden}\n",
  ".meter-fill{height:100%;background:var(--tm-green);border-radius:4px}\n",
  ".meter-foot{font-size:12px;color:var(--tm-sub);display:flex;align-items:center;gap:6px}\n",
  ".meter-foot .link{margin-left:auto}\n",
  ".dotw{width:7px;height:7px;border-radius:50%;background:var(--tm-amber);display:inline-block;flex:none}\n",
  ".doto{width:7px;height:7px;border-radius:50%;background:var(--tm-green);display:inline-block;flex:none}\n",
  ".tbtn{border:0;background:none;color:var(--tm-sub);font-size:12px;cursor:pointer;text-decoration:underline;padding:0}\n",
  "\n",
  ".al{display:flex;align-items:center;gap:12px;padding:11px 2px;border-top:1px solid var(--tm-line)}\n",
  ".al:first-child{border-top:0}\n",
  ".al .ic{width:26px;height:26px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex:none;font-weight:800;font-size:13px}\n",
  ".al .ic.warn{background:var(--tm-amber-soft);color:var(--tm-amber)}\n",
  ".al .ic.muted{background:var(--tm-fill);color:var(--tm-faint)}\n",
  ".al .tx{flex:1;min-width:0}\n",
  ".al .t1{font-size:13.5px;font-weight:600;overflow-wrap:anywhere}\n",
  ".al .t2{font-size:12.5px;color:var(--tm-sub);margin-top:2px;line-height:1.45;overflow-wrap:anywhere}\n",
  ".al .ac{display:flex;gap:6px;flex:none;margin-left:auto;align-items:center}\n",
  ".al.more{justify-content:flex-end}\n",
  "\n",
  ".tbl-wrap{overflow:auto;border:1px solid var(--tm-line);border-radius:10px}\n",
  "table{width:100%;border-collapse:collapse;font-size:13px}\n",
  "th{position:sticky;top:0;background:var(--tm-surface);text-align:left;font-weight:600;color:var(--tm-sub);font-size:12.5px;padding:8px 10px;border-bottom:1px solid var(--tm-line);white-space:nowrap;z-index:1}\n",
  "th.sortable{cursor:pointer}\n",
  "th .ar{color:var(--tm-faint);font-size:11px;margin-left:3px}\n",
  "td{padding:7px 10px;border-bottom:1px solid var(--tm-line);vertical-align:middle;white-space:nowrap}\n",
  "tr:last-child td{border-bottom:0}\n",
  "td.num{font-variant-numeric:tabular-nums}\n",
  "td.wrap{white-space:normal;min-width:200px;overflow-wrap:anywhere}\n",
  "td.act{text-align:right}\n",
  "td.empty{text-align:center;padding:20px;color:var(--tm-faint)}\n",
  "tr.rw td:first-child{box-shadow:inset 3px 0 0 var(--tm-amber)}\n",
  "tr.rw td{background:var(--lit-amber-bg)}\n",
  ".cell-sel{border:1px solid var(--tm-line);border-radius:6px;padding:2px 4px;font-size:12.5px;background:#fff}\n",
  ".cell-sel.warn{border-color:var(--tm-amber-line);background:var(--tm-amber-soft);color:var(--lit-amber-text);font-weight:600}\n",
  ".ic-btn{border:0;background:none;color:var(--tm-sub);cursor:pointer;padding:2px 5px;border-radius:5px;font-size:12.5px;white-space:nowrap}\n",
  ".ic-btn:hover{background:var(--tm-fill)}\n",
  ".ic-btn[disabled]{color:var(--tm-frame);cursor:default;background:none}\n",
  ".more-row{display:flex;justify-content:flex-end;margin-top:8px}\n",
  "\n",
  ".fgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 12px}\n",
  ".fld{display:grid;gap:5px;font-size:12.5px;color:var(--tm-sub);font-weight:600}\n",
  ".fld input,.fld select,.fld textarea{border:1px solid var(--tm-line);border-radius:8px;padding:7px 9px;font-weight:400;color:var(--tm-ink);background:#fff;font-size:13.5px}\n",
  ".fld textarea{resize:vertical;min-height:64px}\n",
  ".fld.full{grid-column:1/-1}\n",
  ".pills{display:flex;gap:5px;flex-wrap:wrap}\n",
  ".pill{border:1px solid var(--tm-line);background:#fff;border-radius:999px;padding:4px 10px;font-size:12.5px;cursor:pointer;color:var(--lit-ink2);font-weight:500}\n",
  ".pill.on{background:var(--tm-ink);color:#fff;border-color:var(--tm-ink)}\n",
  ".pill[disabled]{cursor:default;color:var(--tm-faint)}\n",
  ".foot{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:12px}\n",
  ".b1line{display:flex;align-items:center;gap:10px;padding:8px 10px 8px 12px;border-radius:10px;background:var(--tm-fill);font-size:13px;color:var(--lit-ink2);margin-bottom:12px}\n",
  ".b1line .tx{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n",
  ".b1line .actions{flex:none}\n",
  ".notice{border-radius:10px;padding:11px 13px;font-size:13px;line-height:1.5;overflow-wrap:anywhere}\n",
  ".notice.warn{background:var(--tm-amber-soft);color:var(--lit-amber-text)}\n",
  ".notice.muted{background:var(--tm-fill);color:var(--lit-ink2)}\n",
  ".notice.ok{background:var(--tm-green-soft);color:var(--lit-green-text)}\n",
  ".notices{display:grid;gap:8px;margin-bottom:14px}\n",
  ".res{display:grid;gap:8px}\n",
  ".res .r{display:flex;gap:8px;align-items:flex-start;font-size:13px;line-height:1.5}\n",
  ".res .r .chip{flex:none}\n",
  ".res .ai-rec{padding-left:2px;color:var(--lit-ink2)}\n",
  ".sec-l{font-size:12px;font-weight:700;color:var(--tm-faint);margin:14px 0 8px;letter-spacing:.02em}\n",
  "\n",
  ".q-grp{border:1px solid var(--tm-line);border-radius:10px;padding:10px 12px;margin-bottom:8px}\n",
  ".q-grp.warn{border-color:var(--tm-amber-line)}\n",
  ".q-line{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--lit-ink2);margin-top:5px;padding-left:10px;border-left:2px solid var(--tm-line);line-height:1.5}\n",
  ".q-line .tx{flex:1;min-width:0;overflow-wrap:anywhere}\n",
  ".q-line .actions{flex:none}\n",
  ".q-row .actions{flex:none}\n",
  ".pre{white-space:pre-wrap}\n",
  ".q-meta{font-size:12px;color:var(--tm-faint)}\n",
  ".q-row{display:flex;gap:8px;align-items:center;font-size:12.5px;padding:4px 0}\n",
  ".q-row .txt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--tm-sub)}\n",
  "\n",
  ".gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}\n",
  ".st{border:1px solid var(--tm-line);border-radius:12px;padding:12px 13px;display:flex;flex-direction:column}\n",
  ".st.warn{border-color:var(--tm-amber-line);background:var(--lit-amber-bg)}\n",
  ".st-h{display:flex;align-items:center;gap:8px}\n",
  ".st-n{min-width:30px;height:30px;padding:0 4px;border-radius:9px;background:var(--tm-fill);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;color:var(--lit-ink2)}\n",
  ".st-nm{font-weight:700;font-size:14.5px}\n",
  ".st-c{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin:10px 0 8px}\n",
  ".st-c div{background:var(--tm-surface);border-radius:7px;padding:5px 0;text-align:center;font-size:11.5px;color:var(--tm-sub)}\n",
  ".st-c b{display:block;font-size:14px;color:var(--tm-ink)}\n",
  ".st-last{font-size:12px;color:var(--tm-sub);min-height:17px}\n",
  ".st .actions{margin-top:auto;padding-top:8px}\n",
  "\n",
  ".ov{position:fixed;inset:0;background:rgba(25,31,40,.28);display:flex;align-items:center;justify-content:center;z-index:20}\n",
  ".ov-box{background:#fff;border-radius:14px;width:min(520px,92%);max-height:92%;overflow:auto;padding:20px 22px;box-shadow:0 10px 30px rgba(0,0,0,.2)}\n",
  ".ov-box.wide{width:min(660px,94%)}\n",
  ".ov-box h4{margin:0 0 6px;font-size:16px}\n",
  ".ov-list{margin:12px 0;border:1px solid var(--tm-line);border-radius:10px;max-height:240px;overflow:auto}\n",
  ".ov-list > div{padding:8px 12px;border-top:1px solid var(--tm-line);font-size:13px;overflow-wrap:anywhere}\n",
  ".ov-list > div:first-child{border-top:0}\n",
  ".ov-foot{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:14px;flex-wrap:wrap}\n",
  ".toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:var(--tm-ink);color:#fff;border-radius:10px;padding:9px 14px;font-size:13px;z-index:30;max-width:90%}\n"
].join('');
const TM_DASHBOARD_JS_ = [
  "!function(n){\"function\"==typeof define&&define.amd?define(n):n()}(function(){var n,t,e,i,r,o,f,u,c,a,s,h,l,p,y,d,v,_=\"http://www.w3.org/2000/svg\",w=\"http://www.w3.org/1999/xhtml\",m=null,g=void 0,b={},k=[],x=/acit|ex(?:s|g|n|p|$)|rph|grid|ows|mnc|ntw|ine[ch]|zoo|^ord|itera/i,C=Array.isArray;function M(n,t){for(var e in t)n[e]=t[e];return n}function S(n){n&&n.parentNode&&n.parentNode.removeChild(n)}function $(t,e,i){var r,o,f,u={};for(f in e)\"key\"==f?r=e[f]:\"ref\"==f?o=e[f]:u[f]=e[f];if(arguments.length>2&&(u.children=arguments.length>3?n.call(arguments,2):i),\"function\"==typeof t&&t.defaultProps!=m)for(f in t.defaultProps)u[f]===g&&(u[f]=t.defaultProps[f]);return A(t,u,r,o,m)}function A(n,i,r,o,f){var u={type:n,props:i,key:r,ref:o,__k:m,__:m,__b:0,__e:m,__c:m,constructor:g,__v:f==m?++e:f,__i:-1,__u:0};return f==m&&t.vnode!=m&&t.vnode(u),u}function I(n){return n.children}function P(n,t){this.props=n,this.context=t}function E(n,t){if(t==m)return n.__?E(n.__,n.__i+1):m;for(var e;t<n.__k.length;t++)if((e=n.__k[t])!=m&&e.__e!=m)return e.__e;return\"function\"==typeof n.type?E(n):m}function F(n){if(n.__P&&n.__d){var e=n.__v,i=e.__e,r=[],o=[],f=M({},e);f.__v=e.__v+1,t.vnode&&t.vnode(f),B(n.__P,f,e,n.__n,n.__P.namespaceURI,32&e.__u?[i]:m,r,i==m?E(e):i,!!(32&e.__u),o),f.__v=e.__v,f.__.__k[f.__i]=f,G(r,f,o),e.__e=e.__=null,f.__e!=i&&H(f)}}function H(n){if((n=n.__)!=m&&n.__c!=m)return n.__e=n.__c.base=m,n.__k.some(function(t){if(t!=m&&t.__e!=m)return n.__e=n.__c.base=t.__e}),H(n)}function L(n){(!n.__d&&(n.__d=!0)&&r.push(n)&&!T.__r++||o!=t.debounceRendering)&&((o=t.debounceRendering)||f)(T)}function T(){try{for(var n,t=1;r.length;)r.length>t&&r.sort(u),n=r.shift(),t=r.length,F(n)}finally{r.length=T.__r=0}}function j(n,t,e,i,r,o,f,u,c,a,s){var h,l,p,y,d,v,_=i&&i.__k||k,w=t.length;for(c=O(e,t,_,c,w),h=0;h<w;h++)(p=e.__k[h])!=m&&(l=-1!=p.__i&&_[p.__i]||b,p.__i=h,v=B(n,p,l,r,o,f,u,c,a,s),y=p.__e,p.ref&&l.ref!=p.ref&&(l.ref&&Q(l.ref,m,p),s.push(p.ref,p.__c||y,p)),d==m&&y!=m&&(d=y),4&p.__u?(c=V(p,c,n),l.__e&&(l.__e=m)):\"function\"==typeof p.type&&v!==g?c=v:y&&(c=y.nextSibling),p.__u&=-7);return e.__e=d,c}function O(n,t,e,i,r){var o,f,u,c,a,s=e.length,h=s,l=0;for(n.__k=new Array(r),o=0;o<r;o++)(f=t[o])!=m&&\"boolean\"!=typeof f&&\"function\"!=typeof f?(\"string\"==typeof f||\"number\"==typeof f||\"bigint\"==typeof f||f.constructor==String?f=n.__k[o]=A(m,f,m,m,m):C(f)?f=n.__k[o]=A(I,{children:f},m,m,m):f.constructor===g&&f.__b>0?f=n.__k[o]=A(f.type,f.props,f.key,f.ref?f.ref:m,f.__v):n.__k[o]=f,c=o+l,f.__=n,f.__b=n.__b+1,a=f.__i=z(f,e,c,h),u=m,-1!=a&&(h--,(u=e[a])&&(u.__u|=2)),u==m||u.__v==m?(-1==a&&(r>s?l--:r<s&&l++),\"function\"!=typeof f.type&&(f.__u|=4)):a!=c&&(a==c-1?l--:a==c+1?l++:(a>c?l--:l++,f.__u|=4))):n.__k[o]=m;if(h)for(o=0;o<s;o++)(u=e[o])!=m&&0==(2&u.__u)&&(u.__e==i&&(i=E(u)),U(u,u));return i}function V(n,t,e){var i,r;if(\"function\"==typeof n.type){for(i=n.__k,r=0;i&&r<i.length;r++)i[r]&&(i[r].__=n,t=V(i[r],t,e));return t}n.__e!=t&&(t&&n.type&&!t.parentNode&&(t=E(n)),t=e.insertBefore(n.__e,t||m));do{t=t&&t.nextSibling}while(t!=m&&8==t.nodeType);return t}function z(n,t,e,i){var r,o,f,u=n.key,c=n.type,a=t[e],s=a!=m&&0==(2&a.__u);if(a===m&&null==u||s&&u==a.key&&c==a.type)return e;if(i>(s?1:0))for(r=e-1,o=e+1;r>=0||o<t.length;)if((a=t[f=r>=0?r--:o++])!=m&&0==(2&a.__u)&&u==a.key&&c==a.type)return f;return-1}function N(n,t,e){\"-\"==t[0]?n.setProperty(t,e==m?\"\":e):n[t]=e==m?\"\":\"number\"!=typeof e||x.test(t)?e:e+\"px\"}function R(n,t,e,i,r){var o,f;n:if(\"style\"==t)if(\"string\"==typeof e)n.style.cssText=e;else{if(\"string\"==typeof i&&(n.style.cssText=i=\"\"),i)for(t in i)e&&t in e||N(n.style,t,\"\");if(e)for(t in e)i&&e[t]==i[t]||N(n.style,t,e[t])}else if(\"o\"==t[0]&&\"n\"==t[1])o=t!=(t=t.replace(h,\"$1\")),f=t.toLowerCase(),t=f in n||\"onFocusOut\"==t||\"onFocusIn\"==t?f.slice(2):t.slice(2),n.l||(n.l={}),n.l[t+o]=e,e?i?e[s]=i[s]:(e[s]=l,n.addEventListener(t,o?y:p,o)):n.removeEventListener(t,o?y:p,o);else{if(r==_)t=t.replace(/xlink(H|:h)/,\"h\").replace(/sName$/,\"s\");else if(\"width\"!=t&&\"height\"!=t&&\"href\"!=t&&\"list\"!=t&&\"form\"!=t&&\"tabIndex\"!=t&&\"download\"!=t&&\"rowSpan\"!=t&&\"colSpan\"!=t&&\"role\"!=t&&\"popover\"!=t&&t in n)try{n[t]=e==m?\"\":e;break n}catch(n){}\"function\"==typeof e||(e==m||!1===e&&\"-\"!=t[4]?n.removeAttribute(t):n.setAttribute(t,\"popover\"==t&&1==e?\"\":e))}}function q(n){return function(e){if(this.l){var i=this.l[e.type+n];if(e[a]==m)e[a]=l++;else if(e[a]<i[s])return;return i(t.event?t.event(e):e)}}}function B(n,e,i,r,o,f,u,c,a,s){var h,l,p,y,d,v,_,w,b,x,$,A,F,H,L,T,O=e.type;if(e.constructor!==g)return m;128&i.__u&&(a=!!(32&i.__u),f=[c=e.__e=i.__e]),(h=t.__b)&&h(e);n:if(\"function\"==typeof O){l=u.length;try{if(b=e.props,x=O.prototype&&O.prototype.render,$=(h=O.contextType)&&r[h.__c],A=h?$?$.props.value:h.__:r,i.__c?w=(p=e.__c=i.__c).__=p.__E:(x?e.__c=p=new O(b,A):(e.__c=p=new P(b,A),p.constructor=O,p.render=W),$&&$.sub(p),p.state||(p.state={}),p.__n=r,y=p.__d=!0,p.__h=[],p._sb=[]),x&&p.__s==m&&(p.__s=p.state),x&&O.getDerivedStateFromProps!=m&&(p.__s==p.state&&(p.__s=M({},p.__s)),M(p.__s,O.getDerivedStateFromProps(b,p.__s))),d=p.props,v=p.state,p.__v=e,y)x&&O.getDerivedStateFromProps==m&&p.componentWillMount!=m&&p.componentWillMount(),x&&p.componentDidMount!=m&&p.__h.push(p.componentDidMount);else{if(x&&O.getDerivedStateFromProps==m&&b!==d&&p.componentWillReceiveProps!=m&&p.componentWillReceiveProps(b,A),e.__v==i.__v||!p.__e&&p.shouldComponentUpdate!=m&&!1===p.shouldComponentUpdate(b,p.__s,A)){e.__v!=i.__v&&(p.props=b,p.state=p.__s,p.__d=!1),e.__e=i.__e,e.__k=i.__k,e.__k.some(function(n){n&&(n.__=e)}),k.push.apply(p.__h,p._sb),p._sb=[],p.__h.length&&u.push(p),c=E(i);break n}p.componentWillUpdate!=m&&p.componentWillUpdate(b,p.__s,A),x&&p.componentDidUpdate!=m&&p.__h.push(function(){p.componentDidUpdate(d,v,_)})}if(p.context=A,p.props=b,p.__P=n,p.__e=!1,F=t.__r,H=0,x)p.state=p.__s,p.__d=!1,F&&F(e),h=p.render(p.props,p.state,p.context),k.push.apply(p.__h,p._sb),p._sb=[];else do{p.__d=!1,F&&F(e),h=p.render(p.props,p.state,p.context),p.state=p.__s}while(p.__d&&++H<25);p.state=p.__s,p.getChildContext!=m&&(r=M(M({},r),p.getChildContext())),x&&!y&&p.getSnapshotBeforeUpdate!=m&&(_=p.getSnapshotBeforeUpdate(d,v)),L=h!=m&&h.type===I&&h.key==m?J(h.props.children):h,c=j(n,C(L)?L:[L],e,i,r,o,f,u,c,a,s),p.base=e.__e,e.__u&=-161,p.__h.length&&u.push(p),w&&(p.__E=p.__=m)}catch(n){if(u.length=l,e.__v=m,a||f!=m){if(n.then){for(e.__u|=a?160:128;c&&8==c.nodeType&&c.nextSibling;)c=c.nextSibling;f!=m&&(f[f.indexOf(c)]=m),e.__e=c}else if(f!=m)for(T=f.length;T--;)S(f[T])}else e.__e=i.__e;e.__k==m&&(e.__k=i.__k||[]),n.then||D(e),t.__e(n,e,i)}}else f==m&&e.__v==i.__v?(e.__k=i.__k,e.__e=i.__e):c=e.__e=K(i.__e,e,i,r,o,f,u,a,s);return(h=t.diffed)&&h(e),128&e.__u?void 0:c}function D(n){n&&(n.__c&&(n.__c.__e=!0),n.__k&&n.__k.some(D))}function G(n,e,i){for(var r=0;r<i.length;r++)Q(i[r],i[++r],i[++r]);t.__c&&t.__c(e,n),n.some(function(e){try{n=e.__h,e.__h=[],n.some(function(n){n.call(e)})}catch(n){t.__e(n,e.__v)}})}function J(n){return\"object\"!=typeof n||n==m||n.__b>0?n:C(n)?n.map(J):n.constructor!==g?null:M({},n)}function K(e,i,r,o,f,u,c,a,s){var h,l,p,y,d,v,k,x=r.props||b,M=i.props,$=i.type;if(\"svg\"==$?f=_:\"math\"==$?f=\"http://www.w3.org/1998/Math/MathML\":f||(f=w),u!=m)for(h=0;h<u.length;h++)if((d=u[h])&&\"setAttribute\"in d==!!$&&($?d.localName==$:3==d.nodeType)){e=d,u[h]=m;break}if(e==m){if($==m)return document.createTextNode(M);e=document.createElementNS(f,$,M.is&&M),a&&(t.__m&&t.__m(i,u),a=!1),u=m}if($==m)x===M||a&&e.data==M||(e.data=M);else{if(u=\"textarea\"==$&&M.defaultValue!=m?m:u&&n.call(e.childNodes),!a&&u!=m)for(x={},h=0;h<e.attributes.length;h++)x[(d=e.attributes[h]).name]=d.value;for(h in x)d=x[h],\"dangerouslySetInnerHTML\"==h?p=d:\"children\"==h||h in M||\"value\"==h&&\"defaultValue\"in M||\"checked\"==h&&\"defaultChecked\"in M||R(e,h,m,d,f);for(h in M)d=M[h],\"children\"==h?y=d:\"dangerouslySetInnerHTML\"==h?l=d:\"value\"==h?v=d:\"checked\"==h?k=d:a&&\"function\"!=typeof d||x[h]===d||R(e,h,d,x[h],f);if(l)a||p&&(l.__html==p.__html||l.__html==e.innerHTML)||(e.innerHTML=l.__html),i.__k=[];else if(p&&(e.innerHTML=\"\"),j(\"template\"==i.type?e.content:e,C(y)?y:[y],i,r,o,\"foreignObject\"==$?w:f,u,c,u?u[0]:r.__k&&E(r,0),a,s),u!=m)for(h=u.length;h--;)S(u[h]);a&&\"textarea\"!=$||(h=\"value\",\"progress\"==$&&v==m?e.removeAttribute(\"value\"):v!=g&&(v!==e[h]||\"progress\"==$&&!v||\"option\"==$&&v!=x[h])&&R(e,h,v,x[h],f),h=\"checked\",k!=g&&k!=e[h]&&R(e,h,k,x[h],f))}return e}function Q(n,e,i){try{if(\"function\"==typeof n){var r=\"function\"==typeof n.__u;r&&n.__u(),r&&e==m||(n.__u=n(e))}else n.current=e}catch(n){t.__e(n,i)}}function U(n,e,i){var r,o;if(t.unmount&&t.unmount(n),(r=n.ref)&&(r.current&&r.current!=n.__e||Q(r,m,e)),(r=n.__c)!=m){if(r.componentWillUnmount)try{r.componentWillUnmount()}catch(n){t.__e(n,e)}r.base=r.__P=r.__n=m}if(r=n.__k)for(o=0;o<r.length;o++)r[o]&&U(r[o],e,i||\"function\"!=typeof n.type);i||S(n.__e),n.__c=n.__=n.__e=g}function W(n,t,e){return this.constructor(n,e)}function X(e,i,r){var o,f,u,c;i==document&&(i=document.documentElement),t.__&&t.__(e,i),f=(o=\"function\"==typeof r)?m:r&&r.__k||i.__k,u=[],c=[],B(i,e=(!o&&r||i).__k=$(I,m,[e]),f||b,b,i.namespaceURI,!o&&r?[r]:f?m:i.firstChild?n.call(i.childNodes):m,u,!o&&r?r:f?f.__e:i.firstChild,o,c),G(u,e,c),e.props.children=m}n=k.slice,t={__e:function(n,t,e,i){for(var r,o,f;t=t.__;)if((r=t.__c)&&!r.__)try{if((o=r.constructor)&&o.getDerivedStateFromError!=m&&(r.setState(o.getDerivedStateFromError(n)),f=r.__d),r.componentDidCatch!=m&&(r.componentDidCatch(n,i||{}),f=r.__d),f)return r.__E=r}catch(t){n=t}throw n}},e=0,i=function(n){return n!=m&&n.constructor===g},P.prototype.setState=function(n,t){var e;e=this.__s!=m&&this.__s!=this.state?this.__s:this.__s=M({},this.state),\"function\"==typeof n&&(n=n(M({},e),this.props)),n&&M(e,n),n!=m&&this.__v&&(t&&this._sb.push(t),L(this))},P.prototype.forceUpdate=function(n){this.__v&&(this.__e=!0,n&&this.__h.push(n),L(this))},P.prototype.render=I,r=[],f=\"function\"==typeof Promise?Promise.prototype.then.bind(Promise.resolve()):setTimeout,u=function(n,t){return n.__v.__b-t.__v.__b},T.__r=0,c=Math.random().toString(8),a=\"__d\"+c,s=\"__a\"+c,h=/(PointerCapture)$|Capture$/i,l=0,p=q(!1),y=q(!0),d=0,v={__proto__:null,render:X,hydrate:function n(t,e){X(t,e,n)},createElement:$,h:$,Fragment:I,createRef:function(){return{current:m}},isValidElement:i,Component:P,cloneElement:function(t,e,i){var r,o,f,u,c=M({},t.props);for(f in t.type&&t.type.defaultProps&&(u=t.type.defaultProps),e)\"key\"==f?r=e[f]:\"ref\"==f?o=e[f]:c[f]=e[f]===g&&u!=g?u[f]:e[f];return arguments.length>2&&(c.children=arguments.length>3?n.call(arguments,2):i),A(t.type,c,r||t.key,o||t.ref,m)},createContext:function(n){function t(n){var e,i;return this.getChildContext||(e=new Set,(i={})[t.__c]=this,this.getChildContext=function(){return i},this.componentWillUnmount=function(){e=m},this.shouldComponentUpdate=function(n){this.props.value!=n.value&&e.forEach(function(n){n.__e=!0,L(n)})},this.sub=function(n){e.add(n);var t=n.componentWillUnmount;n.componentWillUnmount=function(){e&&e.delete(n),t&&t.call(n)}}),n.children}return t.__c=\"__cC\"+d++,t.__=n,t.Provider=t.__l=(t.Consumer=function(n,t){return n.children(t)}).contextType=t,t},toChildArray:function n(t,e){return e=e||[],t==m||\"boolean\"==typeof t||(C(t)?t.some(function(t){n(t,e)}):e.push(t)),e},options:t},typeof module<\"u\"?module.exports=v:self.preact=v});\n",
  ";\n",
  "!function(n,t){\"object\"==typeof exports&&\"undefined\"!=typeof module?t(exports,require(\"preact\")):\"function\"==typeof define&&define.amd?define([\"exports\",\"preact\"],t):t((n||self).preactHooks={},n.preact)}(this,function(n,t){var u,i,r,o,f=0,c=[],e=t.options,a=e.__b,v=e.__r,l=e.diffed,d=e.__c,p=e.unmount,s=e.__;function y(n,t){e.__h&&e.__h(i,n,f||t),f=0;var u=i.__H||(i.__H={__:[],__h:[]});return n>=u.__.length&&u.__.push({}),u.__[n]}function h(n){return f=1,m(j,n)}function m(n,t,r){var o=y(u++,2);if(o.t=n,!o.__c&&(o.__=[r?r(t):j(void 0,t),function(n){var t=o.__N?o.__N[0]:o.__[0],u=o.t(t,n);t!==u&&(o.__N=[u,o.__[1]],o.__c.setState({}))}],o.__c=i,!i.__f)){var f=function(n,t,u){if(!o.__c.__H)return!0;var i=!1,r=o.__c.props!==n;if(o.__c.__H.__.some(function(n){if(n.__N){i=!0;var t=n.__[0];n.__=n.__N,n.__N=void 0,t!==n.__[0]&&(r=!0)}}),c){var f=c.call(this,n,t,u);return i?f||r:f}return!i||r};i.__f=!0;var c=i.shouldComponentUpdate,e=i.componentWillUpdate;i.componentWillUpdate=function(n,t,u){if(this.__e){var i=c;c=void 0,f(n,t,u),c=i}e&&e.call(this,n,t,u)},i.shouldComponentUpdate=f}return o.__N||o.__}function T(n,t){var r=y(u++,4);!e.__s&&g(r.__H,t)&&(r.__=n,r.u=t,i.__h.push(r))}function _(n,t){var i=y(u++,7);return g(i.__H,t)&&(i.__=n(),i.__H=t,i.__h=n),i.__}function b(){for(var n;n=c.shift();){var t=n.__H;if(n.__P&&t)try{t.__h.some(A),t.__h.some(F),t.__h=[]}catch(u){t.__h=[],e.__e(u,n.__v)}}}e.__b=function(n){i=null,a&&a(n)},e.__=function(n,t){n&&t.__k&&t.__k.__m&&(n.__m=t.__k.__m),s&&s(n,t)},e.__r=function(n){v&&v(n),u=0;var t=(i=n.__c).__H;t&&(r===i?(t.__h=[],i.__h=[],t.__.some(function(n){n.__N&&(n.__=n.__N),n.u=n.__N=void 0})):(t.__h.some(A),t.__h.some(F),t.__h=[],u=0)),r=i},e.diffed=function(n){l&&l(n);var t=n.__c;t&&t.__H&&(t.__H.__h.length&&(1!==c.push(t)&&o===e.requestAnimationFrame||((o=e.requestAnimationFrame)||x)(b)),t.__H.__.some(function(n){n.u&&(n.__H=n.u,n.u=void 0)})),r=i=null},e.__c=function(n,t){t.some(function(n){try{n.__h.some(A),n.__h=n.__h.filter(function(n){return!n.__||F(n)})}catch(u){t.some(function(n){n.__h&&(n.__h=[])}),t=[],e.__e(u,n.__v)}}),d&&d(n,t)},e.unmount=function(n){p&&p(n);var t,u=n.__c;u&&u.__H&&(u.__H.__.some(function(n){try{A(n)}catch(n){t=n}}),u.__H=void 0,t&&e.__e(t,u.__v))};var q=\"function\"==typeof requestAnimationFrame;function x(n){var t,u=function(){clearTimeout(i),q&&cancelAnimationFrame(t),setTimeout(n)},i=setTimeout(u,35);q&&(t=requestAnimationFrame(u))}function A(n){var t=i,u=n.__c;\"function\"==typeof u&&(n.__c=void 0,u()),i=t}function F(n){var t=i;n.__c=n.__(),i=t}function g(n,t){return!n||n.length!==t.length||t.some(function(t,u){return t!==n[u]})}function j(n,t){return\"function\"==typeof t?t(n):t}n.useCallback=function(n,t){return f=8,_(function(){return n},t)},n.useContext=function(n){var t=i.context[n.__c],r=y(u++,9);return r.c=n,t?(null==r.__&&(r.__=!0,t.sub(i)),t.props.value):n.__},n.useDebugValue=function(n,t){e.useDebugValue&&e.useDebugValue(t?t(n):n)},n.useEffect=function(n,t){var r=y(u++,3);!e.__s&&g(r.__H,t)&&(r.__=n,r.u=t,i.__H.__h.push(r))},n.useErrorBoundary=function(n){var t=y(u++,10),r=h();return t.__=n,i.componentDidCatch||(i.componentDidCatch=function(n,u){t.__&&t.__(n,u),r[1](n)}),[r[0],function(){r[1](void 0)}]},n.useId=function(){var n=y(u++,11);if(!n.__){for(var t=i.__v;null!==t&&!t.__m&&null!==t.__;)t=t.__;var r=t.__m||(t.__m=[0,0]);n.__=\"P\"+r[0]+\"-\"+r[1]++}return n.__},n.useImperativeHandle=function(n,t,u){f=6,T(function(){if(\"function\"==typeof n){var u=n(t());return function(){n(null),u&&\"function\"==typeof u&&u()}}if(n)return n.current=t(),function(){return n.current=null}},null==u?u:u.concat(n))},n.useLayoutEffect=T,n.useMemo=_,n.useReducer=m,n.useRef=function(n){return f=5,_(function(){return{current:n}},[])},n.useState=h});\n",
  ";\n",
  "!function(n,e){\"object\"==typeof exports&&\"undefined\"!=typeof module?module.exports=e():\"function\"==typeof define&&define.amd?define(e):n.htm=e()}(this,function(){var n=function(e,t,u,s){var r;t[0]=0;for(var p=1;p<t.length;p++){var h=t[p++],o=t[p]?(t[0]|=h?1:2,u[t[p++]]):t[++p];3===h?s[0]=o:4===h?s[1]=Object.assign(s[1]||{},o):5===h?(s[1]=s[1]||{})[t[++p]]=o:6===h?s[1][t[++p]]+=o+\"\":h?(r=e.apply(o,n(e,o,u,[\"\",null])),s.push(r),o[0]?t[0]|=2:(t[p-2]=0,t[p]=r)):s.push(o)}return s},e=new Map;return function(t){var u=e.get(this);return u||(u=new Map,e.set(this,u)),(u=n(this,u.get(t)||(u.set(t,u=function(n){for(var e,t,u=1,s=\"\",r=\"\",p=[0],h=function(n){1===u&&(n||(s=s.replace(/^\\s*\\n\\s*|\\s*\\n\\s*$/g,\"\")))?p.push(0,n,s):3===u&&(n||s)?(p.push(3,n,s),u=2):2===u&&\"...\"===s&&n?p.push(4,n,0):2===u&&s&&!n?p.push(5,0,!0,s):u>=5&&((s||!n&&5===u)&&(p.push(u,0,s,t),u=6),n&&(p.push(u,n,0,t),u=6)),s=\"\"},o=0;o<n.length;o++){o&&(1===u&&h(),h(o));for(var f=0;f<n[o].length;f++)e=n[o][f],1===u?\"<\"===e?(h(),p=[p],u=3):s+=e:4===u?\"--\"===s&&\">\"===e?(u=1,s=\"\"):s=e+s[0]:r?e===r?r=\"\":s+=e:'\"'===e||\"'\"===e?r=e:\">\"===e?(h(),u=1):u&&(\"=\"===e?(u=5,t=s,s=\"\"):\"/\"===e&&(u<5||\">\"===n[o][f+1])?(h(),3===u&&(p=p[0]),u=p,(p=p[0]).push(2,0,u),u=0):\" \"===e||\"\\t\"===e||\"\\n\"===e||\"\\r\"===e?(h(),u=2):s+=e),3===u&&\"!--\"===s&&(u=4,p=p[0])}return h(),p}(t)),u),arguments,[])).length>1?u:u[0]}});\n",
  ";\n",
  "/* 출결·메신저 대시보드 화면 (1단계: 읽기 + 출결 입력, 2단계: Google Chat 보내기).\n",
  "   scripts/embed_dashboard.py가 이 파일을 Code.gs의 TM_DASHBOARD_JS_ 문자열로 넣는다.\n",
  "   시트에서 읽은 값은 모두 Preact가 글자와 속성으로 넣는다. innerHTML은 쓰지 않는다(SHEET-DASH-04). */\n",
  "(function () {\n",
  "  'use strict';\n",
  "  const h = window.preact.h;\n",
  "  const render = window.preact.render;\n",
  "  const useState = window.preactHooks.useState;\n",
  "  const useEffect = window.preactHooks.useEffect;\n",
  "  const useMemo = window.preactHooks.useMemo;\n",
  "  const useRef = window.preactHooks.useRef;\n",
  "  const html = window.htm.bind(h);\n",
  "  const VIEWER = String(window.TM_DASH_VIEWER || '');\n",
  "\n",
  "  const TM_CONN = 'Teacher Manager의 [Google 연결 → 출결]에서 연결 상태를 확인해 주세요.';\n",
  "  const TM_CHECK = 'Teacher Manager의 [Google 연결 → 출결]에서 확인해 주세요.';\n",
  "  const TM_ROSTER = 'Teacher Manager 홈의 [시간표 · 담임학급 학생명단] → [담임학급 학생명단] 탭에서 이메일을 입력하고 [명단 저장]을 눌러 주세요.';\n",
  "  const ROSTER_EMPTY = '학생명단이 비어 있어요. Teacher Manager 홈의 [시간표 · 담임학급 학생명단] → [담임학급 학생명단] 탭에서 명단을 저장해 주세요.';\n",
  "  const STALE_MSG = '시트가 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인한 뒤 다시 해 주세요.';\n",
  "  const READ_FAILED = '시트를 읽지 못했어요. [↻ 새로 읽기]를 눌러 주세요.';\n",
  "  const ACCOUNTS_MSG = '여러 Google 계정이 로그인된 브라우저에서는 이 화면이 열리지 않을 수 있어요. 학교 계정만 로그인된 브라우저 프로필에서 시트를 열어 주세요.';\n",
  "  const SETUP_MSG = '처음 설정이 끝나지 않았어요. 시트 메뉴 [🔵 처음 한 번 설정하기] → [▶ 처음 설정 한 번에 끝내기]를 눌러 주세요.';\n",
  "  const OTHER_ACCOUNT_MSG = '이 출석부를 설정한 계정만 입력하고 보낼 수 있어요.';\n",
  "  const MARKER_MSG = '이 출석부의 처음 설정 기록을 확인하지 못했어요. ' + TM_CONN;\n",
  "  const AI_OFF_MSG = 'AI 출결 입력을 쓸 수 없어요. ' + TM_CHECK;\n",
  "  const BUSY_FINAL = '다른 작업이 끝난 뒤 다시 눌러 주세요.';\n",
  "  const UNKNOWN_WRITE = '저장 결과를 확인하지 못했어요. 출결 기록에서 바뀐 내용을 확인한 뒤 다시 해 주세요.';\n",
  "  const SEND_UNCERTAIN = 'Google Chat 발송 결과를 확인하지 못했어요. 다시 보내지 말고 발송기록을 확인해 주세요.';\n",
  "  const SEND_CAUTION = '보낸 메시지는 되돌릴 수 없어요.';\n",
  "  const PERSONAL_TYPES = ['출결서류', '준비물', '개별안내', '상담/확인', '기타'];\n",
  "  const CLASS_TYPES = ['준비물', '제출물', '일정', '생활지도', '기타'];\n",
  "  const CATS = ['미인정', '질병', '출석인정', '기타'];\n",
  "  const KINDS = ['결석함', '지각함', '조퇴함', '결과함'];\n",
  "  const CAT_COLOR = { '미인정': '#191F28', '질병': '#6B7684', '출석인정': '#8B95A1', '기타': '#D1D6DB' };\n",
  "  const WD = ['일', '월', '화', '수', '목', '금', '토'];\n",
  "  const SAFE_URL = /^https:\\/\\/[^\\s/$.?#].[^\\s]*$/i;\n",
  "  const TABLE_PAGE = 200;\n",
  "\n",
  "  /* ---------- 날짜 ---------- */\n",
  "  const pad = n => String(n).padStart(2, '0');\n",
  "  const isDateKey = s => /^\\d{4}-\\d{2}-\\d{2}$/.test(String(s || ''));\n",
  "  const ymd = s => String(s).split('-').map(Number);\n",
  "  const dObj = s => { const p = ymd(s); return new Date(Date.UTC(p[0], p[1] - 1, p[2])); };\n",
  "  const iso = d => d.toISOString().slice(0, 10);\n",
  "  const addDays = (s, n) => { const d = dObj(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };\n",
  "  const lastDayOf = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();\n",
  "  const monthEnd = (y, m) => y + '-' + pad(m) + '-' + pad(lastDayOf(y, m));\n",
  "  const addMonthsClamp = (s, n) => {\n",
  "    const p = ymd(s);\n",
  "    const t = new Date(Date.UTC(p[0], p[1] - 1 + n, 1));\n",
  "    const y = t.getUTCFullYear(), m = t.getUTCMonth() + 1;\n",
  "    return y + '-' + pad(m) + '-' + pad(Math.min(p[2], lastDayOf(y, m)));\n",
  "  };\n",
  "  const dow = s => dObj(s).getUTCDay();\n",
  "  const md = s => { const p = ymd(s); return p[1] + '/' + p[2]; };\n",
  "  const mdw = s => (isDateKey(s) ? md(s) + '(' + WD[dow(s)] + ')' : String(s || ''));\n",
  "  const span = (a, b) => Math.round((dObj(b) - dObj(a)) / 864e5);\n",
  "  const hm = t => (t ? String(t).slice(11, 16) : '');\n",
  "  const mdhm = t => {\n",
  "    const text = String(t || '');\n",
  "    if (!isDateKey(text.slice(0, 10))) return text;\n",
  "    return md(text.slice(0, 10)) + (text.length >= 16 ? ' ' + hm(text) : '');\n",
  "  };\n",
  "  const kShort = k => String(k || '').replace('함', '');\n",
  "  const combine = (n, name) => {\n",
  "    const a = String(n === null || n === undefined ? '' : n).trim();\n",
  "    const b = String(name === null || name === undefined ? '' : name).trim();\n",
  "    return a && b ? a + b : b || a;\n",
  "  };\n",
  "  const koreanDate = (text) => {\n",
  "    if (!isDateKey(String(text || '').slice(0, 10))) return String(text || '');\n",
  "    const p = ymd(String(text).slice(0, 10));\n",
  "    return p[1] + '월 ' + p[2] + '일(' + WD[dow(String(text).slice(0, 10))] + ')' + (String(text).length >= 16 ? ' ' + hm(text) : '');\n",
  "  };\n",
  "  const newRequestId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 12);\n",
  "\n",
  "  /* ---------- 서버 호출 ---------- */\n",
  "  function serverCall(name, arg) {\n",
  "    return new Promise(function (resolve, reject) {\n",
  "      try {\n",
  "        const runner = window.google.script.run\n",
  "          .withSuccessHandler(value => resolve(value))\n",
  "          .withFailureHandler(error => reject(error || new Error('failed')));\n",
  "        runner[name](Object.assign({ viewer: VIEWER }, arg || {}));\n",
  "      } catch (err) {\n",
  "        reject(err);\n",
  "      }\n",
  "    });\n",
  "  }\n",
  "  const looksLikeAccountError = err => /authoriz|권한|승인|로그인/i.test(String(err && err.message || err || ''));\n",
  "  // 잠금을 얻지 못한 요청(busy)만 두 번 더 보낸다. 아무 작업도 시작하지 않은 경우다(§3.6).\n",
  "  function writeCall(name, arg, onBusy) {\n",
  "    const clientRequestId = newRequestId();\n",
  "    let tries = 0;\n",
  "    return new Promise(function (resolve) {\n",
  "      const go = () => serverCall(name, Object.assign({}, arg, { clientRequestId })).then(res => {\n",
  "        const result = res && typeof res === 'object' ? res : { status: 'failed' };\n",
  "        if (result.status === 'busy' && tries < 2) {\n",
  "          tries += 1;\n",
  "          if (onBusy) onBusy(tries);\n",
  "          setTimeout(go, 2000);\n",
  "          return;\n",
  "        }\n",
  "        resolve(result);\n",
  "      }, err => resolve({ status: looksLikeAccountError(err) ? 'account-mismatch' : 'unknown', message: '' }));\n",
  "      go();\n",
  "    });\n",
  "  }\n",
  "  function closeDialogWindow() {\n",
  "    try { window.google.script.host.close(); } catch (err) { /* 창을 닫지 못해도 화면은 그대로 쓴다 */ }\n",
  "  }\n",
  "  function fitDialog() {\n",
  "    try {\n",
  "      const w = Math.min(1200, Math.max(1000, (window.screen.availWidth || 0) - 80));\n",
  "      const hgt = Math.min(800, Math.max(620, (window.screen.availHeight || 0) - 180));\n",
  "      window.google.script.host.setWidth(w);\n",
  "      window.google.script.host.setHeight(hgt);\n",
  "    } catch (err) { /* 크기를 바꾸지 못하면 처음 크기로 쓴다 */ }\n",
  "  }\n",
  "\n",
  "  /* ---------- 스냅숏 정리 ---------- */\n",
  "  function calendarFor(res) {\n",
  "    const sy = Number((res.classInfo || {}).schoolYear);\n",
  "    const today = String(res.today || '');\n",
  "    const periods = {};\n",
  "    if (!Number.isInteger(sy) || sy < 2000 || !isDateKey(today)) {\n",
  "      periods.year = { label: '학년 전체', short: '전체', start: '0000-01-01', end: '9999-12-31', noCompare: true, all: true };\n",
  "      return { periods, inYear: false, start: '', end: '', sy: 0, term: null, defaultPeriod: 'year' };\n",
  "    }\n",
  "    const start = sy + '-03-01';\n",
  "    const end = monthEnd(sy + 1, 2);\n",
  "    const inYear = today >= start && today <= end;\n",
  "    let term = null;\n",
  "    if (inYear) {\n",
  "      const p = ymd(today);\n",
  "      const monthStart = p[0] + '-' + pad(p[1]) + '-01';\n",
  "      periods.month = { label: '이번 달(' + p[1] + '월)', short: p[1] + '월', start: monthStart, end: today };\n",
  "      const prev = addMonthsClamp(monthStart, -1);\n",
  "      if (prev >= start) {\n",
  "        const q = ymd(prev);\n",
  "        periods.last = { label: '지난달(' + q[1] + '월)', short: q[1] + '월', start: prev, end: monthEnd(q[0], q[1]) };\n",
  "      }\n",
  "      const first = p[1] >= 3 && p[1] <= 8;\n",
  "      term = { label: first ? '1학기' : '2학기', start: first ? start : sy + '-09-01', end: today };\n",
  "      periods.term = { label: '이번 학기(' + term.label + ')', short: term.label, start: term.start, end: today, noCompare: true };\n",
  "    }\n",
  "    periods.year = { label: '학년 전체', short: sy + '학년도', start, end: inYear ? today : end, noCompare: true, all: true };\n",
  "    return { periods, inYear, start, end, sy, term, defaultPeriod: inYear ? 'month' : 'year' };\n",
  "  }\n",
  "\n",
  "  function prepare(res) {\n",
  "    const months = (res.months || []).slice();\n",
  "    const monthBySheet = new Map();\n",
  "    const monthByNumber = new Map();\n",
  "    months.forEach(m => { monthBySheet.set(m.sheetId, m); monthByNumber.set(m.month, m); });\n",
  "    const roster = (res.roster || []).slice();\n",
  "    const byLabel = new Map();\n",
  "    roster.forEach(s => { if (s.label && !byLabel.has(s.label)) byLabel.set(s.label, s); });\n",
  "    const rows = (res.rows || []).map(r => {\n",
  "      const m = monthBySheet.get(r.s) || {};\n",
  "      const num = /^(\\d+)/.exec(String(r.st || ''));\n",
  "      return {\n",
  "        id: r.s + ':' + r.r, sheetId: r.s, row: r.r, fp: r.fp, month: m.month || 0,\n",
  "        date: r.d || '', dateText: r.dt || '', label: r.st || '', n: num ? Number(num[1]) : 9999,\n",
  "        cat: r.c || '', kind: r.k || '', reason: r.e || '', period: r.p || '', g: r.g || '', h: r.h || '',\n",
  "        rs: r.rs || 'none', j: r.j || '', kr: r.kr || '', ai: !!r.ai\n",
  "      };\n",
  "    });\n",
  "    const pq = (((res.personalQueue || {}).rows) || []).map(q => Object.assign({ kind: 'p', label: combine(q.n, q.name) }, q));\n",
  "    const cq = (((res.classQueue || {}).rows) || []).map(q => Object.assign({ kind: 'c' }, q));\n",
  "    // 이번 읽기가 모든 월 탭과 보낼 메시지 탭을 끝까지 읽었는지. 못 읽은 곳이 있으면 0건이어도\n",
  "    // 초록 '모두 제출'·'없음'·'모두 끝났어요'를 칠하지 않는다(SHEET-DASH-06, C6·C8).\n",
  "    const workbookOk = !(res.workbook && res.workbook.ok === false);\n",
  "    const cover = {\n",
  "      months: workbookOk && !res.monthsProblem && months.length > 0 && months.every(m => m.readable && !m.truncated),\n",
  "      queues: workbookOk && (res.personalQueue || {}).layoutOk !== false && (res.classQueue || {}).layoutOk !== false\n",
  "    };\n",
  "    return {\n",
  "      raw: res, today: res.today, months, monthBySheet, monthByNumber, roster, byLabel, rows, pq, cq, cover,\n",
  "      log: ((res.chatLog || {}).rows) || [], holidays: res.holidays || [], cal: calendarFor(res)\n",
  "    };\n",
  "  }\n",
  "\n",
  "  const missing = r => r.g === '미제출' || r.h === '미제출';\n",
  "  const missingText = r => [r.g === '미제출' ? '신고서' : '', r.h === '미제출' ? '첨부' : ''].filter(Boolean).join('·') + ' 미제출';\n",
  "  const RS = {\n",
  "    current: ['ok', '보냄'], mismatch: ['warn', '기록 확인 필요'], sending: ['muted', '보내는 중'],\n",
  "    'sending-stale': ['warn', '결과 확인 필요'], failed: ['warn', '실패'], 'needs-connection': ['warn', '연결 필요'], other: ['muted', '확인 필요']\n",
  "  };\n",
  "  const monthOf = (D, r) => D.monthBySheet.get(r.sheetId) || {};\n",
  "  const rowEditable = (D, r) => !!(D.raw.canWrite && monthOf(D, r).writable);\n",
  "  const emailKnownMissing = (D, label) => { const s = D.byLabel.get(label); return !!s && s.hasEmail === false; };\n",
  "\n",
  "  function qState(q, today) {\n",
  "    if (q.st === '대기') {\n",
  "      if (isDateKey(q.d) && q.d > today) return ['muted', md(q.d) + ' 예정'];\n",
  "      if (q.res) return ['warn', '연결 문제로 멈춤'];\n",
  "      return ['warn', '오늘 보낼 차례'];\n",
  "    }\n",
  "    if (q.st === '발송중') return q.stale ? ['warn', '결과 확인 필요'] : ['muted', '보내는 중'];\n",
  "    if (q.st === '보냄') return String(q.res || '').indexOf('다시 보내지 말고') >= 0 ? ['warn', '보냄 · 기록 확인'] : ['ok', '보냄'];\n",
  "    if (q.st === '실패') return ['warn', '실패'];\n",
  "    return ['muted', q.st || '상태 없음'];\n",
  "  }\n",
  "  function queuesOf(D) {\n",
  "    const today = D.today;\n",
  "    const groups = new Map();\n",
  "    D.pq.forEach(q => {\n",
  "      if (q.st !== '대기' || !q.d || q.d > today || !String(q.text || '').trim()) return;\n",
  "      const key = String(q.n || '') + '|' + String(q.name || '');\n",
  "      if (!groups.has(key)) groups.set(key, { key, label: q.label || String(q.name || q.n || ''), lines: [] });\n",
  "      groups.get(key).lines.push(q);\n",
  "    });\n",
  "    const classPend = D.cq.filter(q => q.st === '대기' && q.d && q.d <= today && String(q.text || '').trim());\n",
  "    const staleP = D.pq.filter(q => q.st === '발송중' && q.stale);\n",
  "    const staleC = D.cq.filter(q => q.st === '발송중' && q.stale);\n",
  "    const sentToday = new Set(D.pq.filter(q => q.st === '보냄' && String(q.at || '').slice(0, 10) === today)\n",
  "      .map(q => String(q.n || '') + '|' + String(q.name || '')));\n",
  "    const classSentToday = D.cq.some(q => q.st === '보냄' && String(q.at || '').slice(0, 10) === today);\n",
  "    return { groups, classPend, staleP, staleC, sentToday, classSentToday };\n",
  "  }\n",
  "  function problemsOf(D) {\n",
  "    const out = { fail: [], conn: [], stale: [], record: [] };\n",
  "    D.rows.forEach(r => {\n",
  "      if (r.rs === 'failed') out.fail.push(r);\n",
  "      if (r.rs === 'needs-connection') out.conn.push(r);\n",
  "      if (r.rs === 'sending-stale') out.stale.push(r);\n",
  "      if (r.rs === 'mismatch') out.record.push(r);\n",
  "    });\n",
  "    D.pq.concat(D.cq).forEach(q => {\n",
  "      const l = qState(q, D.today)[1];\n",
  "      if (l === '실패') out.fail.push(q);\n",
  "      if (l === '연결 문제로 멈춤') out.conn.push(q);\n",
  "      if (l === '결과 확인 필요') out.stale.push(q);\n",
  "      if (l === '보냄 · 기록 확인') out.record.push(q);\n",
  "    });\n",
  "    out.total = out.fail.length + out.conn.length + out.stale.length + out.record.length;\n",
  "    return out;\n",
  "  }\n",
  "  function topReasons(D, limit) {\n",
  "    const count = new Map(), last = new Map();\n",
  "    D.rows.forEach(r => {\n",
  "      const k = String(r.reason || '').trim();\n",
  "      if (!k) return;\n",
  "      count.set(k, (count.get(k) || 0) + 1);\n",
  "      if (!last.has(k) || r.date > last.get(k)) last.set(k, r.date);\n",
  "    });\n",
  "    return Array.from(count.keys()).sort((a, b) => count.get(b) - count.get(a)\n",
  "      || (last.get(b) > last.get(a) ? 1 : last.get(b) < last.get(a) ? -1 : (a > b ? 1 : -1))).slice(0, limit);\n",
  "  }\n",
  "  function termMi(D) {\n",
  "    const counts = {};\n",
  "    const term = D.cal.term;\n",
  "    if (!term) return counts;\n",
  "    D.rows.forEach(r => {\n",
  "      if (r.cat === '미인정' && r.date >= term.start && r.date <= term.end) counts[r.label] = (counts[r.label] || 0) + 1;\n",
  "    });\n",
  "    return counts;\n",
  "  }\n",
  "  function periodOf(D, view) {\n",
  "    if (view.period === 'custom' && view.custom) return view.custom;\n",
  "    return D.cal.periods[view.period] || D.cal.periods.year;\n",
  "  }\n",
  "  function filterRows(D, view, range) {\n",
  "    const p = range || periodOf(D, view);\n",
  "    const q = String(view.q || '').trim();\n",
  "    return D.rows.filter(r => {\n",
  "      if (r.date) { if (r.date < p.start || r.date > p.end) return false; }\n",
  "      else if (!p.all || range) return false;\n",
  "      if (view.cat && r.cat !== view.cat) return false;\n",
  "      if (view.kind && r.kind !== view.kind) return false;\n",
  "      if (view.student && r.label !== view.student) return false;\n",
  "      if (q && [r.label, r.reason, r.date, r.dateText, r.cat, r.kind, r.g, r.h, r.ai ? 'AI' : ''].join(' ').indexOf(q) < 0) return false;\n",
  "      return true;\n",
  "    });\n",
  "  }\n",
  "  function prevRange(p) {\n",
  "    if (p.noCompare) return null;\n",
  "    const s = addMonthsClamp(p.start, -1);\n",
  "    return { start: s, end: addDays(s, span(p.start, p.end)) };\n",
  "  }\n",
  "  function buckets(start, end) {\n",
  "    const out = [];\n",
  "    if (span(start, end) > 70) {\n",
  "      let p = ymd(start), y = p[0], m = p[1];\n",
  "      for (let guard = 0; guard < 40; guard++) {\n",
  "        const ms = y + '-' + pad(m) + '-01';\n",
  "        if (ms > end) break;\n",
  "        const me = monthEnd(y, m);\n",
  "        out.push({ s: ms < start ? start : ms, e: me > end ? end : me, label: m + '월' });\n",
  "        m += 1; if (m > 12) { m = 1; y += 1; }\n",
  "      }\n",
  "      return out;\n",
  "    }\n",
  "    let ws = addDays(start, -((dow(start) + 6) % 7));\n",
  "    for (let guard = 0; ws <= end && guard < 60; guard++) {\n",
  "      const s = ws < start ? start : ws, fri = addDays(ws, 4), e = addDays(ws, 6);\n",
  "      out.push({ s, e: e > end ? end : e, label: md(s) + '~' + md(fri < end ? fri : end) });\n",
  "      ws = addDays(ws, 7);\n",
  "    }\n",
  "    return out;\n",
  "  }\n",
  "\n",
  "  /* ---------- 작은 부품 ---------- */\n",
  "  const Chip = p => html`<span class=${'chip ' + p.tone + (p.lg ? ' lg' : '')} title=${p.title || null}>${p.children}</span>`;\n",
  "  const Notice = p => html`<div class=${'notice ' + p.tone} role=\"status\">${p.children}</div>`;\n",
  "  function RsCell(p) {\n",
  "    const r = p.r;\n",
  "    if (!r.rs || r.rs === 'none') return html`<span class=\"faint small\">보낸 적 없음</span>`;\n",
  "    const pair = RS[r.rs] || RS.other;\n",
  "    const lines = [];\n",
  "    if (r.j) lines.push((r.rs === 'sending' ? '시작 ' : '') + mdhm(r.j));\n",
  "    if (r.rs === 'mismatch') lines.push('보낸 내용 기록과 다름');\n",
  "    else if (r.kr) lines.push(r.kr);\n",
  "    return html`<${Chip} tone=${pair[0]} title=${['Google Chat 알림 · ' + pair[1]].concat(lines).join('\\n')}>${pair[1]}</${Chip}>`;\n",
  "  }\n",
  "  const DocSelect = p => html`\n",
  "    <select class=${'cell-sel' + (p.value === '미제출' ? ' warn' : '')} aria-label=${p.label} disabled=${p.disabled}\n",
  "      value=${p.value || ''} onChange=${e => p.onChange(e.target.value)}>\n",
  "      ${['', '제출', '미제출', '해당없음'].map(o => html`<option value=${o} selected=${o === p.value}>${o || '(비움)'}</option>`)}\n",
  "    </select>`;\n",
  "\n",
  "  /* ---------- 앱 ---------- */\n",
  "  function App() {\n",
  "    const [S, setS] = useState({\n",
  "      loading: true, fatal: '', readFailed: '', snap: null, health: null, tab: 'home', toast: '',\n",
  "      dialog: null, ledgerNotice: null, busyIds: {}, docPending: {},\n",
  "      view: { period: '', custom: null, cat: '', kind: '', student: '', q: '', sort: { key: 'date', dir: -1 }, limit: TABLE_PAGE, allAlerts: false, trendTable: false },\n",
  "      ai: { sheetId: null, text: '', boxSheetId: null, state: 'idle', message: '', records: [], locked: '', month: 0, confirmedText: '' },\n",
  "      manual: { date: '', student: '', cat: '질병', kind: '결석함', reason: '', period: '', g: '미제출', h: '미제출', state: 'idle', message: '', locked: '' },\n",
  "      sending: false, remind: {},\n",
  "      addMsg: { kind: 'personal', date: '', student: '', type: '기타', content: '', state: 'idle', message: '', locked: '' },\n",
  "      holiday: { year: '', type: '', q: '' }\n",
  "    });\n",
  "    const set = patch => setS(prev => Object.assign({}, prev, typeof patch === 'function' ? patch(prev) : patch));\n",
  "    const setIn = (key, patch) => setS(prev => Object.assign({}, prev, { [key]: Object.assign({}, prev[key], typeof patch === 'function' ? patch(prev[key]) : patch) }));\n",
  "    const readSeq = useRef(0);\n",
  "    const healthSeq = useRef(0);\n",
  "    const manualPending = useRef(false);\n",
  "    const toastTimer = useRef(null);\n",
  "    const D = useMemo(() => (S.snap ? prepare(S.snap) : null), [S.snap]);\n",
  "\n",
  "    function showToast(text) {\n",
  "      set({ toast: text });\n",
  "      clearTimeout(toastTimer.current);\n",
  "      toastTimer.current = setTimeout(() => set({ toast: '' }), 2800);\n",
  "    }\n",
  "    // 머리글 칩(연결 상태)은 처음 열 때와 [↻ 새로 읽기]에서만 다시 읽는다. 저장 뒤에는 표만 다시 읽는다.\n",
  "    // opts.manual은 선생님이 누른 [↻ 새로 읽기]다. 결과를 확인하지 못해 잠근 입력과 지난 저장\n",
  "    // 안내는 이때만 푼다. 저장 뒤의 자동 다시 읽기로는 풀지 않는다(§3.6, C7, R09).\n",
  "    function refresh(opts) {\n",
  "      const o = opts === true ? { health: true } : (opts || {});\n",
  "      // 뒤따른 자동 읽기가 [↻ 새로 읽기]를 덮어도 그 요청은 잊지 않는다.\n",
  "      if (o.manual) manualPending.current = true;\n",
  "      const mine = ++readSeq.current;\n",
  "      set({ loading: true });\n",
  "      serverCall('tmDashboardSnapshot', {}).then(res => {\n",
  "        if (mine !== readSeq.current) return;\n",
  "        if (res && res.status === 'account-mismatch') { set({ loading: false, fatal: ACCOUNTS_MSG, snap: null, docPending: {} }); return; }\n",
  "        if (!res || res.status !== 'ok') { set({ loading: false, readFailed: (res && res.message) || READ_FAILED, snap: null, docPending: {} }); return; }\n",
  "        const unlock = manualPending.current;\n",
  "        manualPending.current = false;\n",
  "        setS(prev => {\n",
  "          const cal = calendarFor(res);\n",
  "          const view = Object.assign({}, prev.view);\n",
  "          if (!view.period || (view.period !== 'custom' && !cal.periods[view.period])) view.period = cal.defaultPeriod;\n",
  "          const next = { loading: false, fatal: '', readFailed: '', snap: res, view, docPending: {} };\n",
  "          if (unlock) {\n",
  "            // 결과를 확인하지 못해 잠근 입력은 [↻ 새로 읽기] 뒤에 다시 열어 준다(§5.5).\n",
  "            next.ai = Object.assign({}, prev.ai, { locked: '' });\n",
  "            if (next.ai.state === 'uncertain') next.ai.state = 'idle';\n",
  "            next.manual = Object.assign({}, prev.manual, { locked: '' });\n",
  "            next.addMsg = Object.assign({}, prev.addMsg, { locked: '' });\n",
  "            next.ledgerNotice = null;\n",
  "          }\n",
  "          return Object.assign({}, prev, next);\n",
  "        });\n",
  "      }, err => {\n",
  "        if (mine !== readSeq.current) return;\n",
  "        if (looksLikeAccountError(err)) set({ loading: false, fatal: ACCOUNTS_MSG, snap: null, docPending: {} });\n",
  "        else set({ loading: false, readFailed: READ_FAILED, snap: null, docPending: {} });\n",
  "      });\n",
  "      if (o.health !== true) return;\n",
  "      const hMine = ++healthSeq.current;\n",
  "      set({ health: null });\n",
  "      serverCall('tmDashboardHealth', {}).then(res => {\n",
  "        if (hMine !== healthSeq.current) return;\n",
  "        set({ health: res && res.status === 'ok' ? res : { ai: 'unknown', chat: 'unknown', classRoom: 'unknown' } });\n",
  "      }, () => { if (hMine === healthSeq.current) set({ health: { ai: 'unknown', chat: 'unknown', classRoom: 'unknown' } }); });\n",
  "    }\n",
  "    useEffect(() => { fitDialog(); refresh(true); }, []);\n",
  "\n",
  "    const ctx = { S, D, set, setIn, refresh, showToast };\n",
  "    const title = D ? headerTitle(D) : '출결·메신저';\n",
  "    return html`\n",
  "      <div class=\"head\">\n",
  "        <div><h1>${title}</h1>\n",
  "          <p class=\"sub\">${D ? headerSub(D) : S.loading ? '시트를 읽는 중…' : ''}</p></div>\n",
  "        <div class=\"head-right\">\n",
  "          ${S.fatal ? null : html`<${HealthChips} health=${S.health} D=${D} />`}\n",
  "          <button class=\"btn-quiet btn-sm\" disabled=${S.loading} onClick=${() => refresh({ health: true, manual: true })}>${S.loading ? '읽는 중…' : '↻ 새로 읽기'}</button>\n",
  "        </div>\n",
  "      </div>\n",
  "      ${S.fatal ? html`<${Notice} tone=\"warn\">${S.fatal}</${Notice}>`\n",
  "        : S.readFailed ? html`<${Notice} tone=\"muted\">${S.readFailed}</${Notice}>`\n",
  "        : !D ? html`<p class=\"boot\">시트를 읽는 중…</p>`\n",
  "        : html`<${Body} ctx=${ctx} />`}\n",
  "      ${S.dialog ? html`<${DialogHost} ctx=${ctx} />` : null}\n",
  "      ${S.toast ? html`<div class=\"toast\" role=\"status\">${S.toast}</div>` : null}`;\n",
  "  }\n",
  "\n",
  "  function headerTitle(D) {\n",
  "    const c = D.raw.classInfo || {};\n",
  "    if (c.schoolYear && c.grade && c.classNumber) return c.schoolYear + '학년도 ' + c.grade + '학년 ' + c.classNumber + '반 출결·메신저';\n",
  "    return (c.classLabel ? c.classLabel + ' ' : '') + '출결·메신저';\n",
  "  }\n",
  "  function headerSub(D) {\n",
  "    const c = D.raw.classInfo || {};\n",
  "    const read = String(D.raw.readAt || '');\n",
  "    const when = isDateKey(read.slice(0, 10)) ? koreanDate(read.slice(0, 10)) + ' ' + hm(read) + '에 읽음' : '';\n",
  "    return [c.teacherName ? '담임 ' + c.teacherName : '', when].filter(Boolean).join(' · ');\n",
  "  }\n",
  "\n",
  "  function HealthChips(p) {\n",
  "    const hl = p.health;\n",
  "    if (!hl) {\n",
  "      return html`<${Chip} tone=\"muted\" lg>AI 출결 입력 확인 중…</${Chip}><${Chip} tone=\"muted\" lg>Google Chat 확인 중…</${Chip}><${Chip} tone=\"muted\" lg>학급 단톡방 확인 중…</${Chip}>`;\n",
  "    }\n",
  "    const ai = hl.ai === 'ready' ? html`<${Chip} tone=\"ok\" lg>AI 출결 입력 켜짐</${Chip}>`\n",
  "      : hl.ai === 'action' ? html`<${Chip} tone=\"warn\" lg title=${TM_CHECK}>AI 출결 입력 확인 필요</${Chip}>`\n",
  "      : html`<${Chip} tone=\"muted\" lg>AI 출결 입력 확인하지 못했어요</${Chip}>`;\n",
  "    const chat = hl.chat === 'ok' ? html`<${Chip} tone=\"ok\" lg>Google Chat 보낼 수 있음</${Chip}>`\n",
  "      : hl.chat === 'needs_connect' ? html`<${Chip} tone=\"warn\" lg title=${TM_CHECK}>Google Chat 연결 필요</${Chip}>`\n",
  "      : html`<${Chip} tone=\"muted\" lg>Google Chat 확인하지 못했어요</${Chip}>`;\n",
  "    const name = String(hl.classRoomName || '');\n",
  "    const room = hl.classRoom === 'ok' ? html`<${Chip} tone=\"ok\" lg>${'학급 단톡방 · ' + name}</${Chip}>`\n",
  "      : hl.classRoom === 'none' ? html`<${Chip} tone=\"warn\" lg title=${TM_CHECK}>학급 단톡방 정하기 필요</${Chip}>`\n",
  "      : html`<${Chip} tone=\"muted\" lg title=${name || null}>학급 단톡방 확인하지 못했어요</${Chip}>`;\n",
  "    return html`${ai}${chat}${room}`;\n",
  "  }\n",
  "\n",
  "  function globalNotices(D) {\n",
  "    const list = [];\n",
  "    const raw = D.raw;\n",
  "    if (raw.workbook && raw.workbook.ok === false) list.push(['warn', raw.workbook.message || TM_CONN]);\n",
  "    if (raw.monthsProblem) list.push(['warn', raw.monthsProblem]);\n",
  "    if (raw.setup === 'not-done') list.push(['warn', SETUP_MSG]);\n",
  "    else if (raw.setup === 'other-account') list.push(['muted', OTHER_ACCOUNT_MSG]);\n",
  "    else if (raw.setup === 'mismatch') list.push(['warn', MARKER_MSG]);\n",
  "    const missingTabs = D.months.filter(m => m.problem === 'missing' || m.problem === 'role').map(m => m.month + '월');\n",
  "    if (missingTabs.length) list.push(['warn', missingTabs.join(', ') + ' 출결표를 찾지 못했어요. ' + TM_CONN]);\n",
  "    const readOnly = D.months.filter(m => ['layout', 'header', 'old-layout'].indexOf(m.problem) >= 0).map(m => m.month + '월');\n",
  "    if (readOnly.length) list.push(['warn', readOnly.join(', ') + ' 출결표의 제목 줄이 달라 보기만 할 수 있어요. ' + TM_CONN]);\n",
  "    const extra = D.months.filter(m => m.problem === 'extra-columns').map(m => m.month + '월');\n",
  "    if (extra.length) list.push(['warn', extra.join(', ') + ' 출결표의 M열 오른쪽에 적은 내용이 있어 보기만 할 수 있어요.']);\n",
  "    const truncated = D.months.filter(m => m.truncated).map(m => m.month + '월');\n",
  "    if (truncated.length) list.push(['warn', truncated.join(', ') + ' 출결표의 줄이 너무 많아 앞부분만 읽었어요.']);\n",
  "    if (raw.rosterProblem === 'missing' || (raw.workbook && raw.workbook.ok !== false && !D.roster.length)) list.push(['warn', ROSTER_EMPTY]);\n",
  "    else if (raw.rosterProblem === 'layout') list.push(['warn', '학생명단의 제목 줄이 달라 번호와 이름을 확인하지 못했어요. ' + TM_CHECK]);\n",
  "    return list;\n",
  "  }\n",
  "\n",
  "  function Body(p) {\n",
  "    const ctx = p.ctx, S = ctx.S, D = ctx.D;\n",
  "    const Q = queuesOf(D);\n",
  "    const msgCount = Q.groups.size + (Q.classPend.length ? 1 : 0) + Q.staleP.length + Q.staleC.length;\n",
  "    const b1Count = D.months.filter(m => m.inputText).length;\n",
  "    const tabs = [['home', '종합'], ['entry', '출결 입력'], ['msg', '메시지 보내기'], ['students', '학생별 출결'], ['holidays', '휴일·학사일정'], ['data', '학생명단·설정']];\n",
  "    const notices = globalNotices(D);\n",
  "    const View = { home: HomeTab, entry: EntryTab, msg: MessagesTab, students: StudentsTab, holidays: HolidaysTab, data: DataTab }[S.tab] || HomeTab;\n",
  "    return html`\n",
  "      ${notices.length ? html`<div class=\"notices\">${notices.map(n => html`<${Notice} tone=${n[0]}>${n[1]}</${Notice}>`)}</div>` : null}\n",
  "      <div class=\"tabs\" role=\"tablist\">\n",
  "        ${tabs.map(t => html`<button class=${'tab' + (S.tab === t[0] ? ' on' : '')} role=\"tab\" aria-selected=${S.tab === t[0]}\n",
  "          onClick=${() => { ctx.set({ tab: t[0] }); window.scrollTo(0, 0); }}>${t[1]}${t[0] === 'msg' && msgCount ? html`<span class=\"cnt warn\">${msgCount}</span>` : null}${t[0] === 'entry' && b1Count ? html`<span class=\"cnt muted\">${b1Count}</span>` : null}</button>`)}\n",
  "      </div>\n",
  "      <${View} ctx=${ctx} Q=${Q} />`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 종합 ---------- */\n",
  "  function Filters(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, v = ctx.S.view;\n",
  "    const setView = patch => ctx.setIn('view', Object.assign({ limit: TABLE_PAGE }, patch));\n",
  "    const students = D.roster.map(s => s.label).filter(Boolean);\n",
  "    return html`\n",
  "      <div class=\"filters\" role=\"group\" aria-label=\"필터\">\n",
  "        <span class=\"flabel\">기간</span>\n",
  "        <div class=\"seg\">\n",
  "          ${Object.keys(D.cal.periods).map(k => html`<button class=${v.period === k ? 'on' : ''} onClick=${() => setView({ period: k })}>${D.cal.periods[k].label}</button>`)}\n",
  "          <button class=${v.period === 'custom' ? 'on' : ''} onClick=${() => ctx.set({ dialog: { kind: 'period' } })}>${v.period === 'custom' && v.custom ? v.custom.label : '기간 선택…'}</button>\n",
  "        </div>\n",
  "        <span class=\"flabel\">구분</span>\n",
  "        <select class=\"sel\" value=${v.cat} onChange=${e => setView({ cat: e.target.value })}>\n",
  "          <option value=\"\" selected=${!v.cat}>전체</option>${CATS.map(c => html`<option value=${c} selected=${v.cat === c}>${c}</option>`)}\n",
  "        </select>\n",
  "        <span class=\"flabel\">종류</span>\n",
  "        <select class=\"sel\" value=${v.kind} onChange=${e => setView({ kind: e.target.value })}>\n",
  "          <option value=\"\" selected=${!v.kind}>전체</option>${KINDS.map(c => html`<option value=${c} selected=${v.kind === c}>${c}</option>`)}\n",
  "        </select>\n",
  "        <span class=\"flabel\">학생</span>\n",
  "        <select class=\"sel\" value=${v.student} onChange=${e => setView({ student: e.target.value })}>\n",
  "          <option value=\"\" selected=${!v.student}>${'전체 (' + students.length + '명)'}</option>\n",
  "          ${students.map(s => html`<option value=${s} selected=${v.student === s}>${s}</option>`)}\n",
  "        </select>\n",
  "        <span class=\"spacer\"></span>\n",
  "        <input class=\"search\" placeholder=\"이름·사유 검색\" value=${v.q} onInput=${e => setView({ q: e.target.value })} />\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function Kpis(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, Q = p.Q, v = ctx.S.view;\n",
  "    const P = periodOf(D, v);\n",
  "    const rows = filterRows(D, v);\n",
  "    const pr = prevRange(P);\n",
  "    const prev = pr ? filterRows(D, v, pr) : null;\n",
  "    const miss = D.rows.filter(missing);\n",
  "    const pb = problemsOf(D);\n",
  "    const today = Q.groups.size + (Q.classPend.length ? 1 : 0);\n",
  "    const delta = (cur, before) => {\n",
  "      const d = cur - before;\n",
  "      return (d > 0 ? '▲ ' + d + '건 많음' : d < 0 ? '▼ ' + (-d) + '건 적음' : '변화 없음') + ' · 한 달 전 같은 기간(' + md(pr.start) + '~' + md(pr.end) + ') ' + before + '건';\n",
  "    };\n",
  "    const mi = rows.filter(r => r.cat === '미인정');\n",
  "    const kindText = KINDS.map(k => kShort(k) + ' ' + rows.filter(r => r.kind === k).length).join(' · ');\n",
  "    const unread = html`<${Chip} tone=\"muted\">확인하지 못했어요</${Chip}>`;\n",
  "    const goAlerts = () => { const el = document.getElementById('alerts-card'); if (el) el.scrollIntoView({ block: 'start' }); };\n",
  "    return html`\n",
  "      <div class=\"kgrid\">\n",
  "        <div class=\"kg a\">지금 해야 할 일<span>전체 기간</span></div>\n",
  "        <div class=\"kg b\">선택한 기간<span>${P.label}</span></div>\n",
  "        <div class=${'kpi' + (miss.length ? ' warn' : '')}>\n",
  "          <div class=\"kpi-top\"><span class=\"kpi-l\">신고서·첨부 미제출</span>${miss.length ? html`<${Chip} tone=\"warn\">조치 필요</${Chip}>` : D.cover.months ? html`<${Chip} tone=\"ok\">모두 제출</${Chip}>` : unread}</div>\n",
  "          <div class=\"kpi-v\">${miss.length}<small>건</small></div>\n",
  "          <div class=\"kpi-s\">${'신고서 ' + miss.filter(r => r.g === '미제출').length + ' · 첨부 ' + miss.filter(r => r.h === '미제출').length + ' · AI 입력 줄 ' + miss.filter(r => r.ai && r.rs === 'none').length}</div>\n",
  "          <div class=\"kpi-d\">${'독려 보냄 ' + miss.filter(r => r.rs === 'current').length + '건'}</div>\n",
  "          <div class=\"kpi-a\"><button class=\"btn-tonal btn-sm\" onClick=${() => goRemind(ctx)}>미제출 독려하기</button></div>\n",
  "        </div>\n",
  "        <div class=${'kpi' + (today ? ' warn' : '')}>\n",
  "          <div class=\"kpi-top\"><span class=\"kpi-l\">오늘 보낼 메시지</span>${today ? html`<${Chip} tone=\"warn\">보내기 필요</${Chip}>` : D.cover.queues ? html`<${Chip} tone=\"ok\">없음</${Chip}>` : unread}</div>\n",
  "          <div class=\"kpi-v\">${today}<small>건</small></div>\n",
  "          <div class=\"kpi-s\">${'개인톡 ' + Q.groups.size + '명 · 단체톡 ' + Q.classPend.length + '줄'}</div>\n",
  "          <div class=\"kpi-a\"><button class=\"btn-tonal btn-sm\" onClick=${() => { ctx.set({ tab: 'msg' }); window.scrollTo(0, 0); }}>메시지 보내기</button></div>\n",
  "        </div>\n",
  "        <div class=${'kpi' + (pb.total ? ' warn' : '')}>\n",
  "          <div class=\"kpi-top\"><span class=\"kpi-l\">발송 결과 확인 필요</span>${pb.total ? html`<${Chip} tone=\"warn\">확인 필요</${Chip}>` : D.cover.months && D.cover.queues ? html`<${Chip} tone=\"ok\">없음</${Chip}>` : unread}</div>\n",
  "          <div class=\"kpi-v\">${pb.total}<small>건</small></div>\n",
  "          <div class=\"kpi-s\" title=${['실패 ' + pb.fail.length + '건', '연결 문제로 멈춤·연결 필요 ' + pb.conn.length + '건', '결과를 확인하지 못함 ' + pb.stale.length + '건', '보낸 기록 확인 ' + pb.record.length + '건'].join('\\n')}>${'실패 ' + pb.fail.length + ' · 연결 ' + pb.conn.length + ' · 결과 확인 ' + pb.stale.length + ' · 기록 ' + pb.record.length}</div>\n",
  "          <div class=\"kpi-a\"><button class=\"btn-tonal btn-sm\" onClick=${goAlerts}>조치할 일 보기</button></div>\n",
  "        </div>\n",
  "        <div class=\"kpi\">\n",
  "          <div class=\"kpi-top\"><span class=\"kpi-l\">출결 변동</span></div>\n",
  "          <div class=\"kpi-v\">${rows.length}<small>건</small></div>\n",
  "          <div class=\"kpi-s\">${kindText}</div>\n",
  "          <div class=\"kpi-d\">${prev ? delta(rows.length, prev.length) : '비교 기간 없음'}</div>\n",
  "        </div>\n",
  "        <div class=\"kpi\">\n",
  "          <div class=\"kpi-top\"><span class=\"kpi-l\">미인정 출결</span></div>\n",
  "          <div class=\"kpi-v\">${mi.length}<small>건</small></div>\n",
  "          <div class=\"kpi-s\">${'학생 ' + new Set(mi.map(r => r.label)).size + '명'}</div>\n",
  "          <div class=\"kpi-d\">${prev ? delta(mi.length, prev.filter(r => r.cat === '미인정').length) : '비교 기간 없음'}</div>\n",
  "        </div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function TrendCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, v = ctx.S.view;\n",
  "    const P = periodOf(D, v);\n",
  "    const rows = filterRows(D, v);\n",
  "    const start = P.start < D.cal.start && D.cal.start ? D.cal.start : P.start;\n",
  "    const end = P.end > D.today && D.cal.inYear ? D.today : P.end;\n",
  "    const valid = isDateKey(start) && isDateKey(end) && start <= end && start !== '0000-01-01';\n",
  "    const bks = valid ? buckets(start, end) : [];\n",
  "    const s1 = bks.map(b => rows.filter(r => r.date >= b.s && r.date <= b.e && r.kind === '결석함').length);\n",
  "    const s2 = bks.map(b => rows.filter(r => r.date >= b.s && r.date <= b.e && r.kind && r.kind !== '결석함').length);\n",
  "    const monthly = valid && span(start, end) > 70;\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>${monthly ? '월별 출결 변동' : '주별 출결 변동'}</h3>\n",
  "          <div class=\"legend\"><span><i class=\"lk solid\"></i>결석</span><span><i class=\"lk hollow\"></i>지각·조퇴·결과</span></div></div>\n",
  "        ${bks.length ? html`<div class=\"chart\"><${LineChart} bks=${bks} s1=${s1} s2=${s2} /></div>` : html`<div class=\"faint small\">표시할 기간이 없어요.</div>`}\n",
  "        <div class=\"row small faint\" style=\"margin-top:6px\"><span>${P.label}${valid ? ' · ' + md(start) + '~' + md(end) : ''}</span><span class=\"spacer\"></span>\n",
  "          ${bks.length ? html`<button class=\"tbtn\" onClick=${() => ctx.setIn('view', { trendTable: !v.trendTable })}>${v.trendTable ? '표 닫기' : '표로 보기'}</button>` : null}</div>\n",
  "        ${v.trendTable && bks.length ? html`<div class=\"tbl-wrap mt\"><table><thead><tr><th>기간</th><th>결석</th><th>지각·조퇴·결과</th></tr></thead>\n",
  "          <tbody>${bks.map((b, i) => html`<tr><td>${b.label}</td><td class=\"num\">${s1[i]}</td><td class=\"num\">${s2[i]}</td></tr>`)}</tbody></table></div>` : null}\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function LineChart(p) {\n",
  "    const bks = p.bks, s1 = p.s1, s2 = p.s2;\n",
  "    const W = 640, H = 220, m = { l: 30, r: 20, t: 14, b: 30 };\n",
  "    const iw = W - m.l - m.r, ih = H - m.t - m.b;\n",
  "    const mx = Math.max(2, ...s1, ...s2), step = mx <= 4 ? 1 : mx <= 10 ? 2 : 5, top = Math.ceil(mx / step) * step;\n",
  "    const x = i => m.l + (bks.length === 1 ? iw / 2 : iw * i / (bks.length - 1));\n",
  "    const y = val => m.t + ih - ih * val / top;\n",
  "    const grid = [];\n",
  "    for (let val = 0; val <= top; val += step) grid.push(val);\n",
  "    const every = Math.max(1, Math.ceil(bks.length / Math.max(1, Math.floor(iw / 78))));\n",
  "    const path = s => s.map((val, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(val).toFixed(1)).join(' ');\n",
  "    const colW = bks.length > 1 ? iw / (bks.length - 1) : iw;\n",
  "    const label = '출결 변동 추이: ' + bks.map((b, i) => b.label + ' 결석 ' + s1[i] + '건, 지각·조퇴·결과 ' + s2[i] + '건').join('; ');\n",
  "    return html`\n",
  "      <svg viewBox=${'0 0 ' + W + ' ' + H} role=\"img\" aria-label=${label}>\n",
  "        ${grid.map(val => html`<line x1=${m.l} x2=${m.l + iw} y1=${y(val)} y2=${y(val)} stroke=${val ? '#E5E8EB' : '#D1D6DB'} stroke-width=\"1\" />`)}\n",
  "        ${grid.map(val => html`<text x=${m.l - 8} y=${y(val) + 4} text-anchor=\"end\" font-size=\"11\" fill=\"#8B95A1\">${val}</text>`)}\n",
  "        ${bks.map((b, i) => (i % every === 0 || i === bks.length - 1) ? html`<text x=${x(i)} y=${H - 8} text-anchor=\"middle\" font-size=\"11\" fill=\"#8B95A1\">${b.label}</text>` : null)}\n",
  "        <path d=${path(s2)} fill=\"none\" stroke=\"#8B95A1\" stroke-width=\"2\" stroke-linejoin=\"round\" stroke-linecap=\"round\" />\n",
  "        <path d=${path(s1)} fill=\"none\" stroke=\"#191F28\" stroke-width=\"2\" stroke-linejoin=\"round\" stroke-linecap=\"round\" />\n",
  "        ${s2.map((val, i) => html`<circle cx=${x(i)} cy=${y(val)} r=\"4\" fill=\"#fff\" stroke=\"#8B95A1\" stroke-width=\"2\" />`)}\n",
  "        ${s1.map((val, i) => html`<circle cx=${x(i)} cy=${y(val)} r=\"5\" fill=\"#191F28\" stroke=\"#fff\" stroke-width=\"2\" />`)}\n",
  "        ${bks.map((b, i) => html`<rect x=${x(i) - colW / 2} y=${m.t} width=${colW} height=${ih} fill=\"transparent\"><title>${md(b.s) + ' ~ ' + md(b.e) + '\\n결석 ' + s1[i] + '건\\n지각·조퇴·결과 ' + s2[i] + '건'}</title></rect>`)}\n",
  "      </svg>`;\n",
  "  }\n",
  "\n",
  "  function DonutCard(p) {\n",
  "    const D = p.ctx.D, rows = filterRows(D, p.ctx.S.view);\n",
  "    const cc = CATS.map(c => rows.filter(r => r.cat === c).length);\n",
  "    const tot = cc.reduce((a, b) => a + b, 0);\n",
  "    const size = 150, R = 70, r = 48, c = size / 2;\n",
  "    const gap = tot && cc.filter(Boolean).length > 1 ? 0.035 : 0;\n",
  "    const pt = (rad, ang) => [c + rad * Math.cos(ang), c + rad * Math.sin(ang)];\n",
  "    let a0 = -Math.PI / 2;\n",
  "    const parts = [];\n",
  "    cc.forEach((val, i) => {\n",
  "      if (!val) return;\n",
  "      const tip = CATS[i] + '\\n' + val + '건 · ' + Math.round(val / tot * 100) + '%';\n",
  "      const a1 = a0 + (val / tot) * Math.PI * 2, s = a0 + gap / 2, e = a1 - gap / 2, large = e - s > Math.PI ? 1 : 0;\n",
  "      if (val === tot) {\n",
  "        parts.push(html`<circle cx=${c} cy=${c} r=${(R + r) / 2} fill=\"none\" stroke=${CAT_COLOR[CATS[i]]} stroke-width=${R - r}><title>${tip}</title></circle>`);\n",
  "      } else {\n",
  "        const p1 = pt(R, s), p2 = pt(R, e), p3 = pt(r, e), p4 = pt(r, s);\n",
  "        parts.push(html`<path d=${'M' + p1[0] + ' ' + p1[1] + ' A' + R + ' ' + R + ' 0 ' + large + ' 1 ' + p2[0] + ' ' + p2[1] + ' L' + p3[0] + ' ' + p3[1] + ' A' + r + ' ' + r + ' 0 ' + large + ' 0 ' + p4[0] + ' ' + p4[1] + 'Z'} fill=${CAT_COLOR[CATS[i]]}><title>${tip}</title></path>`);\n",
  "      }\n",
  "      a0 = a1;\n",
  "    });\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>출결 구분 비율</h3></div>\n",
  "        <div class=\"donut-wrap\">\n",
  "          <div style=\"width:150px;flex:none\">\n",
  "            <svg viewBox=\"0 0 150 150\" role=\"img\" aria-label=${'구분 비율: ' + CATS.map((k, i) => k + ' ' + cc[i] + '건').join(', ')}>\n",
  "              ${tot ? parts : html`<circle cx=${c} cy=${c} r=${(R + r) / 2} fill=\"none\" stroke=\"#F2F4F6\" stroke-width=${R - r} />`}\n",
  "              <text x=${c} y=${c - 2} text-anchor=\"middle\" font-size=\"22\" font-weight=\"700\" fill=\"#191F28\">${tot}</text>\n",
  "              <text x=${c} y=${c + 16} text-anchor=\"middle\" font-size=\"11.5\" fill=\"#6B7684\">건</text>\n",
  "            </svg>\n",
  "          </div>\n",
  "          <div class=\"dleg\">${CATS.map((k, i) => html`<div><span class=\"sw\" style=${'background:' + CAT_COLOR[k]}></span>${k}<span class=\"v\">${cc[i] + '건'}</span><span class=\"p\">${(tot ? Math.round(cc[i] / tot * 100) : 0) + '%'}</span></div>`)}</div>\n",
  "        </div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function ReasonsCard(p) {\n",
  "    const rows = filterRows(p.ctx.D, p.ctx.S.view);\n",
  "    const count = {};\n",
  "    rows.forEach(r => { const k = String(r.reason || '').trim(); if (k) count[k] = (count[k] || 0) + 1; });\n",
  "    const top = Object.keys(count).map(k => [k, count[k]]).sort((a, b) => b[1] - a[1] || (a[0] > b[0] ? 1 : -1)).slice(0, 5);\n",
  "    const mx = Math.max(1, ...top.map(t => t[1]));\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>많이 나온 사유 5가지</h3></div>\n",
  "        <div class=\"bars\">\n",
  "          ${top.length ? top.map(t => html`<div class=\"bar\" title=${t[0] + '\\n' + t[1] + '건 · 전체의 ' + Math.round(t[1] / rows.length * 100) + '%'}>\n",
  "            <span class=\"nm\">${t[0]}</span><span class=\"tr\"><span class=\"fl\" style=${'width:calc((100% - 44px) * ' + (t[1] / mx) + ')'}></span><span class=\"vl\">${t[1] + '건'}</span></span></div>`)\n",
  "            : html`<div class=\"faint small\">조건에 맞는 출결이 없어요.</div>`}\n",
  "        </div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function MetersCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, Q = p.Q, v = ctx.S.view;\n",
  "    const P = periodOf(D, v), rows = filterRows(D, v);\n",
  "    const req = rows.filter(r => ['제출', '미제출'].indexOf(r.g) >= 0 || ['제출', '미제출'].indexOf(r.h) >= 0);\n",
  "    const done = req.filter(r => !missing(r)).length;\n",
  "    const miss = rows.filter(missing), rem = miss.filter(r => r.rs === 'current').length;\n",
  "    const tSent = Q.sentToday.size + (Q.classSentToday ? 1 : 0), tAll = tSent + Q.groups.size + (Q.classPend.length ? 1 : 0);\n",
  "    const showMissing = () => { ctx.setIn('view', { q: '미제출', limit: TABLE_PAGE }); setTimeout(() => { const el = document.getElementById('ledger-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0); };\n",
  "    const toMsg = () => { ctx.set({ tab: 'msg' }); window.scrollTo(0, 0); };\n",
  "    // ok가 거짓이면 그 막대의 근거 탭을 다 읽지 못한 것이다. 남은 일이 보이지 않아도 끝났다고 하지 않는다.\n",
  "    const meter = (label, a, b, left, act, ok) => {\n",
  "      const pct = b ? Math.round(a / b * 100) : ok ? 100 : 0;\n",
  "      return html`<div class=\"meter\" title=${label + '\\n' + a + ' / ' + b + ' · ' + pct + '%'}>\n",
  "        <div class=\"meter-top\"><span>${label}</span><span><b>${a}</b>${' / ' + b}</span></div>\n",
  "        <div class=\"meter-track\" role=\"progressbar\" aria-valuenow=${a} aria-valuemax=${b} aria-label=${label}><div class=\"meter-fill\" style=${'width:' + pct + '%'}></div></div>\n",
  "        <div class=\"meter-foot\">${b - a > 0 ? html`<span class=\"dotw\"></span><span>${left.replace('#', b - a)}</span>${act}`\n",
  "          : ok ? html`<span class=\"doto\"></span><span>모두 끝났어요</span>` : html`<${Chip} tone=\"muted\">확인하지 못했어요</${Chip}>`}</div></div>`;\n",
  "    };\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>서류·메시지 처리 진행</h3></div>\n",
  "        ${meter('신고서·첨부 제출 (' + P.short + ')', done, req.length, '남은 서류 #건', html`<button class=\"link\" onClick=${showMissing}>출결 기록에서 보기</button>`, D.cover.months)}\n",
  "        ${meter('미제출 독려 보냄 (' + P.short + ')', rem, miss.length, '남은 줄 #건', html`<button class=\"link\" onClick=${() => goRemind(ctx)}>미제출 독려하기</button>`, D.cover.months)}\n",
  "        ${meter('오늘 메시지 보냄', tSent, tAll, '남은 메시지 #건', html`<button class=\"link\" onClick=${toMsg}>메시지 보기</button>`, D.cover.queues)}\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function alertItems(ctx, Q) {\n",
  "    const D = ctx.D, items = [];\n",
  "    const toMsg = () => { ctx.set({ tab: 'msg' }); window.scrollTo(0, 0); };\n",
  "    const toLog = () => { ctx.set({ tab: 'msg' }); setTimeout(() => { const el = document.getElementById('log-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0); };\n",
  "    const sheetBtn = r => html`<button class=\"btn-quiet btn-sm\" onClick=${() => selectInSheet(ctx, r)}>시트에서 보기</button>`;\n",
  "    const msgBtn = html`<button class=\"btn-quiet btn-sm\" onClick=${toMsg}>메시지 보기</button>`;\n",
  "    const logBtn = html`<button class=\"btn-quiet btn-sm\" onClick=${toLog}>발송기록 보기</button>`;\n",
  "    const who = q => (q.kind === 'p' ? (q.label || q.name || '') + ' 개인톡' : '단체톡');\n",
  "    const staleQ = Q.staleP.concat(Q.staleC);\n",
  "    if (staleQ.length) {\n",
  "      const recWarn = D.log.some(l => l.result === '기록주의' && staleQ.some(q => q.text === l.text));\n",
  "      items.push(['warn', '결과를 확인하지 못한 메시지 ' + staleQ.length + '건 · ' + staleQ.map(who).join(', '), recWarn ? '발송기록: 보냄 · 기록 확인' : '',\n",
  "        html`<button class=\"btn-quiet btn-sm\" disabled=${!D.raw.canWrite || ctx.S.sending} onClick=${() => openStale(ctx)}>확인하기</button>`]);\n",
  "    }\n",
  "    // 출결 알림은 메시지 탭에 없으므로 그 줄을 시트에서 보게 한다.\n",
  "    D.rows.filter(r => r.rs === 'sending-stale').forEach(r => items.push(['warn', r.label + ' · ' + mdw(r.date || r.dateText) + ' 출결 알림 결과를 확인하지 못했어요', '', sheetBtn(r)]));\n",
  "    D.pq.concat(D.cq).filter(q => qState(q, D.today)[1] === '연결 문제로 멈춤').forEach(q => items.push(['warn', '연결 문제로 멈춘 ' + who(q) + ' · ' + mdhm(q.at), TM_CONN, msgBtn]));\n",
  "    D.rows.filter(r => r.rs === 'needs-connection').forEach(r => items.push(['warn', r.label + ' · ' + mdw(r.date || r.dateText) + ' 출결 알림 연결 필요', TM_CONN, sheetBtn(r)]));\n",
  "    D.rows.filter(r => r.rs === 'failed').forEach(r => items.push(['warn', r.label + ' · ' + mdw(r.date || r.dateText) + ' 출결 알림 실패',\n",
  "      r.kr === '학생 Google 이메일 없음' ? '학생 Google 이메일이 없어요. ' + TM_ROSTER : r.kr, sheetBtn(r)]));\n",
  "    D.pq.concat(D.cq).filter(q => q.st === '실패').forEach(q => items.push(['warn', who(q) + ' 실패 · ' + mdhm(q.at), q.res, msgBtn]));\n",
  "    D.cq.concat(D.pq).filter(q => qState(q, D.today)[1] === '보냄 · 기록 확인').forEach(q => items.push(['warn', who(q) + ' · ' + mdhm(q.at) + ' 보냄 · 발송기록 확인 필요', '다시 보내지 말고 발송기록을 확인해 주세요.', logBtn]));\n",
  "    const weekAgo = addDays(D.today, -7);\n",
  "    D.log.filter(l => l.result === '기록주의' && String(l.at || '').slice(0, 10) >= weekAgo\n",
  "      && !D.pq.concat(D.cq).some(q => q.text === l.text && q.st === '보냄')).forEach(l => items.push(['warn', (l.target || l.kind) + ' · ' + mdhm(l.at) + ' 보냄 · 발송기록 확인 필요', '다시 보내지 말고 발송기록을 확인해 주세요.', logBtn]));\n",
  "    const remindBtn = r => (D.raw.canWrite && remindable(D, r) ? html`<button class=\"btn-quiet btn-sm\" disabled=${ctx.S.sending} onClick=${() => startRemind(ctx, [r])}>독려 보내기…</button>` : null);\n",
  "    D.rows.filter(r => r.rs === 'mismatch').forEach(r => items.push(['warn', '보낸 기록 확인 필요 · ' + r.label + ' · ' + mdw(r.date || r.dateText) + ' ' + missingText(r), '',\n",
  "      html`<span class=\"actions\">${logBtn}${remindBtn(r)}</span>`]));\n",
  "    const aiDef = D.rows.filter(r => r.ai && missing(r) && r.rs === 'none');\n",
  "    if (aiDef.length) {\n",
  "      items.push(['warn', 'AI 입력 줄 ' + aiDef.length + '건 · 신고서·첨부 미제출', aiDef.map(r => r.label + ' ' + (r.date ? md(r.date) : r.dateText)).join(', '),\n",
  "        html`<button class=\"btn-quiet btn-sm\" onClick=${() => { ctx.setIn('view', { q: 'AI', period: 'year', limit: TABLE_PAGE }); setTimeout(() => { const el = document.getElementById('ledger-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0); }}>출결 기록에서 보기</button>`]);\n",
  "    }\n",
  "    D.rows.filter(r => missing(r) && r.rs === 'none' && !r.ai).forEach(r => items.push(['warn', r.label + ' · ' + mdw(r.date || r.dateText) + ' ' + kShort(r.kind) + ' · ' + missingText(r),\n",
  "      emailKnownMissing(D, r.label) ? '학생 Google 이메일 없음' : '', html`<span class=\"actions\">${remindBtn(r)}${sheetBtn(r)}</span>`]));\n",
  "    if (Q.groups.size || Q.classPend.length) items.push(['warn', '오늘 보낼 메시지 · 개인톡 ' + Q.groups.size + '명, 단체톡 ' + Q.classPend.length + '줄', '',\n",
  "      html`<button class=\"btn-quiet btn-sm\" onClick=${toMsg}>메시지 보내기</button>`]);\n",
  "    const tm = termMi(D);\n",
  "    Object.keys(tm).filter(k => tm[k] >= 3).forEach(k => items.push(['warn', k + ' · ' + D.cal.term.label + ' 미인정 ' + tm[k] + '회 · 상담 확인', '',\n",
  "      html`<button class=\"btn-quiet btn-sm\" onClick=${() => { ctx.set({ tab: 'students' }); window.scrollTo(0, 0); }}>학생 보기</button>`]));\n",
  "    D.roster.filter(s => s.hasEmail === false && (D.pq.some(q => q.label === s.label && q.st === '대기') || D.rows.some(r => r.label === s.label && missing(r))))\n",
  "      .forEach(s => items.push(['warn', '학생 Google 이메일 없음 · ' + s.label, TM_ROSTER, null]));\n",
  "    if (D.cal.sy) {\n",
  "      [D.cal.sy, D.cal.sy + 1].filter(y => !D.holidays.some(x => String(x.d || '').slice(0, 4) === String(y))).forEach(y => items.push(['warn',\n",
  "        y + '년 휴일이 없어 ' + y + '년 날짜의 AI 출결 입력이 멈춰요.', '', html`<button class=\"btn-quiet btn-sm\" onClick=${() => { ctx.set({ tab: 'holidays' }); window.scrollTo(0, 0); }}>휴일 탭</button>`]));\n",
  "    }\n",
  "    D.months.filter(m => m.inputText).forEach(m => items.push(['muted', b1Text(m), '', html`<${B1Buttons} ctx=${ctx} m=${m} />`, b1Chip(m)]));\n",
  "    D.rows.filter(r => r.rs === 'sending').forEach(r => items.push(['muted', r.label + ' 출결 알림 보내는 중 · ' + hm(r.j) + ' 시작', '', null]));\n",
  "    D.pq.concat(D.cq).filter(q => q.st === '발송중' && !q.stale).forEach(q => items.push(['muted', who(q) + ' 보내는 중 · ' + hm(q.at) + ' 시작', '', null]));\n",
  "    return items;\n",
  "  }\n",
  "\n",
  "  function AlertsCard(p) {\n",
  "    const ctx = p.ctx, items = alertItems(ctx, p.Q), all = ctx.S.view.allAlerts;\n",
  "    const shown = all ? items : items.slice(0, 6);\n",
  "    return html`\n",
  "      <div class=\"card mt\" id=\"alerts-card\">\n",
  "        <div class=\"card-h\"><h3>지금 조치할 일</h3><span class=\"note\">${items.length + '건'}</span></div>\n",
  "        <div>\n",
  "          ${items.length ? null : html`<div class=\"faint small\">${ctx.D.cover.months && ctx.D.cover.queues ? '지금 조치할 일이 없어요.' : '일부 탭을 읽지 못해 조치할 일을 모두 확인하지 못했어요.'}</div>`}\n",
  "          ${shown.map(it => html`<div class=\"al\"><div class=${'ic ' + it[0]}>${it[0] === 'warn' ? '!' : '…'}</div>\n",
  "            <div class=\"tx\"><div class=\"t1\">${it[1]}${it[4] ? html` <${Chip} tone=\"warn\">${it[4]}</${Chip}>` : null}</div>${it[2] ? html`<div class=\"t2\">${it[2]}</div>` : null}</div>\n",
  "            ${it[3] ? html`<div class=\"ac\">${it[3]}</div>` : null}</div>`)}\n",
  "          ${items.length > 6 ? html`<div class=\"al more\"><button class=\"link\" onClick=${() => ctx.setIn('view', { allAlerts: !all })}>${all ? '접기' : '나머지 ' + (items.length - 6) + '건 더 보기'}</button></div>` : null}\n",
  "        </div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function LedgerCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, v = ctx.S.view;\n",
  "    const P = periodOf(D, v);\n",
  "    const rows = filterRows(D, v).slice();\n",
  "    const key = v.sort.key, dir = v.sort.dir;\n",
  "    rows.sort((a, b) => {\n",
  "      const va = key === 'n' ? a.n : a[key], vb = key === 'n' ? b.n : b[key];\n",
  "      return (va > vb ? 1 : va < vb ? -1 : (a.n - b.n)) * dir;\n",
  "    });\n",
  "    const shown = rows.slice(0, v.limit);\n",
  "    const th = (k, l) => html`<th class=\"sortable\" onClick=${() => ctx.setIn('view', { sort: v.sort.key === k ? { key: k, dir: -v.sort.dir } : { key: k, dir: k === 'date' ? -1 : 1 } })}>${l}<span class=\"ar\">${v.sort.key === k ? (v.sort.dir < 0 ? '▼' : '▲') : '⇅'}</span></th>`;\n",
  "    const busy = ctx.S.busyIds || {};\n",
  "    const pend = ctx.S.docPending || {};\n",
  "    const docValue = (r, field, value) => (pend[r.id + ':' + field] !== undefined ? pend[r.id + ':' + field] : value);\n",
  "    return html`\n",
  "      <div class=\"card mt\" id=\"ledger-card\">\n",
  "        <div class=\"card-h\"><h3>${P.short + ' 출결 기록'}</h3><span class=\"note\">${rows.length + '건'}</span></div>\n",
  "        ${ctx.S.ledgerNotice ? html`<div style=\"margin-bottom:10px\"><${Notice} tone=${ctx.S.ledgerNotice.tone}>${ctx.S.ledgerNotice.text}</${Notice}></div>` : null}\n",
  "        <div class=\"tbl-wrap\" style=\"max-height:420px\"><table>\n",
  "          <thead><tr>${th('date', '날짜')}${th('n', '번호+이름')}${th('cat', '구분')}${th('kind', '종류')}<th>사유</th><th>교시</th><th>신고서</th><th>첨부</th><th>Google Chat 알림</th><th>AI</th><th></th></tr></thead>\n",
  "          <tbody>\n",
  "            ${shown.length ? shown.map(r => {\n",
  "              const editable = rowEditable(D, r) && !busy[r.id];\n",
  "              const warnRow = missing(r) || (RS[r.rs] && RS[r.rs][0] === 'warn');\n",
  "              return html`<tr class=${warnRow ? 'rw' : ''} key=${r.id}>\n",
  "                <td class=\"num\">${r.date ? mdw(r.date) : r.dateText}</td><td><b>${r.label}</b></td>\n",
  "                <td>${r.cat ? html`<span class=\"tag\">${r.cat}</span>` : null}</td><td>${r.kind ? html`<span class=\"tag\">${r.kind}</span>` : null}</td>\n",
  "                <td>${r.reason}</td><td class=\"faint\">${r.period || '—'}</td>\n",
  "                <td>${rowEditable(D, r) ? html`<${DocSelect} value=${docValue(r, 'report', r.g)} label=\"신고서\" disabled=${!editable} onChange=${val => setDocStatus(ctx, r, 'report', val)} />` : (r.g || '—')}</td>\n",
  "                <td>${rowEditable(D, r) ? html`<${DocSelect} value=${docValue(r, 'attach', r.h)} label=\"첨부\" disabled=${!editable} onChange=${val => setDocStatus(ctx, r, 'attach', val)} />` : (r.h || '—')}</td>\n",
  "                <td><${RsCell} r=${r} /></td><td>${r.ai ? html`<span class=\"ai\">AI</span>` : null}</td>\n",
  "                <td class=\"act\">${rowEditable(D, r) ? html`<button class=\"ic-btn\" disabled=${!editable} onClick=${() => ctx.set({ dialog: { kind: 'edit', row: r } })}>수정</button>` : null}<button class=\"ic-btn\" onClick=${() => selectInSheet(ctx, r)}>시트에서 보기</button></td>\n",
  "              </tr>`;\n",
  "            }) : html`<tr><td colspan=\"11\" class=\"empty\">조건에 맞는 출결이 없어요.</td></tr>`}\n",
  "          </tbody>\n",
  "        </table></div>\n",
  "        ${rows.length > shown.length ? html`<div class=\"more-row\"><button class=\"btn-quiet btn-sm\" onClick=${() => ctx.setIn('view', { limit: v.limit + TABLE_PAGE })}>${'더 보기 (' + (rows.length - shown.length) + '건 남음)'}</button></div>` : null}\n",
  "        <div class=\"foot\"><button class=\"btn-quiet btn-sm\" onClick=${() => { ctx.set({ tab: 'entry' }); window.scrollTo(0, 0); }}>+ 출결 입력</button></div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function HomeTab(p) {\n",
  "    const ctx = p.ctx;\n",
  "    return html`\n",
  "      <${Filters} ctx=${ctx} />\n",
  "      <${Kpis} ctx=${ctx} Q=${p.Q} />\n",
  "      <div class=\"grid g-32 mt\"><${TrendCard} ctx=${ctx} /><${DonutCard} ctx=${ctx} /></div>\n",
  "      <div class=\"grid g-2 mt\"><${ReasonsCard} ctx=${ctx} /><${MetersCard} ctx=${ctx} Q=${p.Q} /></div>\n",
  "      <${AlertsCard} ctx=${ctx} Q=${p.Q} />\n",
  "      <${LedgerCard} ctx=${ctx} />`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 쓰기 ---------- */\n",
  "  function markBusy(ctx, id, on) {\n",
  "    ctx.set(prev => { const next = Object.assign({}, prev.busyIds); if (on) next[id] = true; else delete next[id]; return { busyIds: next }; });\n",
  "  }\n",
  "  function handleWriteResult(ctx, res, okText, where) {\n",
  "    const notify = where || (text => ctx.set({ ledgerNotice: { tone: 'warn', text } }));\n",
  "    if (res.status === 'applied') {\n",
  "      if (okText || res.message) ctx.showToast(okText || res.message);\n",
  "      ctx.set({ ledgerNotice: null });\n",
  "      ctx.refresh();\n",
  "      return true;\n",
  "    }\n",
  "    if (res.status === 'stale') { notify(res.message || STALE_MSG); ctx.refresh(); return false; }\n",
  "    if (res.status === 'busy') { notify(BUSY_FINAL); return false; }\n",
  "    if (res.status === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null }); return false; }\n",
  "    if (res.status === 'unknown' || res.status === 'uncertain') { notify(res.message || UNKNOWN_WRITE); ctx.refresh(); return false; }\n",
  "    notify(res.message || '저장하지 못했어요. [↻ 새로 읽기]를 누른 뒤 다시 해 주세요.');\n",
  "    return false;\n",
  "  }\n",
  "  function setDocStatus(ctx, r, field, value) {\n",
  "    markBusy(ctx, r.id, true);\n",
  "    const label = field === 'report' ? '신고서' : '첨부';\n",
  "    writeCall('tmDashboardSetDocStatus', { sheetId: r.sheetId, row: r.row, expectedFp: r.fp, field, value }).then(res => {\n",
  "      // 저장이 확인된 값만 다시 읽기 전까지 보여 준다. 거절된 값은 칸이 시트 값으로 돌아간다.\n",
  "      if (res.status === 'applied') ctx.set(prev => ({ docPending: Object.assign({}, prev.docPending, { [r.id + ':' + field]: value }) }));\n",
  "      markBusy(ctx, r.id, false);\n",
  "      const okText = value ? label + '를 ‘' + value + '’' + (value === '해당없음' ? '으로' : '로') + ' 바꿨어요.' : label + '를 비웠어요.';\n",
  "      handleWriteResult(ctx, res, res.unchanged ? '' : okText);\n",
  "    });\n",
  "  }\n",
  "  function selectInSheet(ctx, r) {\n",
  "    serverCall('tmDashboardSelectInSheet', { sheetId: r.sheetId, row: r.row, expectedFp: r.fp }).then(res => {\n",
  "      if (res && res.status === 'applied') { closeDialogWindow(); return; }\n",
  "      if (res && res.status === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null }); return; }\n",
  "      ctx.showToast((res && res.message) || '시트에서 그 줄을 찾지 못했어요.');\n",
  "      if (res && res.status === 'stale') ctx.refresh();\n",
  "    }, () => ctx.showToast('시트에서 그 줄을 찾지 못했어요.'));\n",
  "  }\n",
  "  function openTab(ctx, target) {\n",
  "    serverCall('tmDashboardSelectInSheet', target).then(res => {\n",
  "      if (res && res.status === 'applied') { closeDialogWindow(); return; }\n",
  "      ctx.showToast((res && res.message) || '시트에서 그 탭을 찾지 못했어요.');\n",
  "    }, () => ctx.showToast('시트에서 그 탭을 찾지 못했어요.'));\n",
  "  }\n",
  "\n",
  "  /* ---------- 출결 입력 ---------- */\n",
  "  const b1Text = m => (m.inputEntered\n",
  "    ? m.month + '월 입력칸에 이미 입력한 문장이 남아 있어요: “' + m.inputText + '”'\n",
  "    : m.month + '월 입력칸에 처리되지 않은 문장이 있어요: “' + m.inputText + '”');\n",
  "  const b1Chip = m => (m.inputUncertain ? '결과 확인 필요' : m.inputEntered ? '이미 입력함' : '');\n",
  "  function B1Buttons(p) {\n",
  "    const ctx = p.ctx, m = p.m, D = ctx.D;\n",
  "    const disabled = !D.raw.canWrite || !m.writable || !!ctx.S.busyIds['b1:' + m.sheetId];\n",
  "    const retry = () => {\n",
  "      if (m.inputUncertain || m.inputEntered) { ctx.set({ dialog: { kind: 'b1warn', month: m } }); return; }\n",
  "      startRetry(ctx, m, false);\n",
  "    };\n",
  "    const clear = () => {\n",
  "      markBusy(ctx, 'b1:' + m.sheetId, true);\n",
  "      writeCall('tmDashboardClearInputBox', { sheetId: m.sheetId, expectedText: m.inputText }).then(res => {\n",
  "        markBusy(ctx, 'b1:' + m.sheetId, false);\n",
  "        if (res.status === 'stale') { ctx.showToast(res.message || '입력칸의 문장이 바뀌어 지우지 않고 다시 읽었어요.'); ctx.refresh(); return; }\n",
  "        handleWriteResult(ctx, res, m.month + '월 입력칸을 비웠어요.', text => ctx.showToast(text));\n",
  "      });\n",
  "    };\n",
  "    return html`<span class=\"actions\"><button class=\"btn-quiet btn-sm\" disabled=${disabled} onClick=${retry}>다시 입력</button><button class=\"btn-quiet btn-sm\" disabled=${disabled} onClick=${clear}>지우기</button></span>`;\n",
  "  }\n",
  "  // confirmed는 경고 창의 [그래도 다시 입력]에서만 참이다. 그 문장 한 번에만 서버 확인을 건너뛴다.\n",
  "  function startRetry(ctx, m, confirmed) {\n",
  "    ctx.set(prev => ({\n",
  "      tab: 'entry', dialog: null,\n",
  "      ai: Object.assign({}, prev.ai, { sheetId: m.sheetId, text: m.inputText, boxSheetId: m.sheetId, state: 'idle', message: '', records: [],\n",
  "        locked: '', month: m.month, confirmedText: confirmed ? m.inputText : '' })\n",
  "    }));\n",
  "    window.scrollTo(0, 0);\n",
  "  }\n",
  "  const docPair = r => (r.report === r.attach\n",
  "    ? (r.report ? '신고서·첨부 ' + r.report : '')\n",
  "    : [r.report ? '신고서 ' + r.report : '', r.attach ? '첨부 ' + r.attach : ''].filter(Boolean).join(' · '));\n",
  "  const aiRecordLine = r => [[isDateKey(r.date) ? mdw(r.date) : r.date, r.student].filter(Boolean).join(' '),\n",
  "    r.category, r.kind, r.reason, r.period, docPair(r)].filter(Boolean).join(' · ');\n",
  "\n",
  "  function aiAvailable(D) {\n",
  "    const f = D.raw.flags || {};\n",
  "    return !!(D.raw.canWrite && f.aiAllowed !== false && f.hasGeminiKey && f.aiTargetMatches && D.roster.length);\n",
  "  }\n",
  "  function AiCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, a = ctx.S.ai;\n",
  "    const writable = D.months.filter(m => m.writable);\n",
  "    const todayMonth = Number(String(D.today).slice(5, 7));\n",
  "    const fallback = (writable.find(m => m.month === todayMonth) || writable[0] || {}).sheetId;\n",
  "    const sheetId = writable.some(m => m.sheetId === a.sheetId) ? a.sheetId : fallback;\n",
  "    const month = D.monthBySheet.get(sheetId) || {};\n",
  "    const running = a.state === 'running' || a.state === 'busy';\n",
  "    const locked = a.locked && a.locked === a.text.trim();\n",
  "    const usable = aiAvailable(D) && !!sheetId;\n",
  "    const run = () => {\n",
  "      const text = String(a.text || '').trim();\n",
  "      if (!text) { ctx.showToast('출결 문장을 입력해 주세요.'); return; }\n",
  "      const box = D.monthBySheet.get(a.boxSheetId);\n",
  "      const fromInputBox = !!box && box.sheetId === sheetId && box.inputText === text;\n",
  "      const confirmUncertain = fromInputBox && a.confirmedText === text;\n",
  "      // 결과를 확인하지 못했거나 이미 입력한 입력칸 문장은 경고부터 보인다(SHEET-DASH-05).\n",
  "      if (fromInputBox && (box.inputUncertain || box.inputEntered) && !confirmUncertain) { ctx.set({ dialog: { kind: 'b1warn', month: box } }); return; }\n",
  "      ctx.setIn('ai', { state: 'running', message: '', records: [], month: month.month, sheetId, confirmedText: '' });\n",
  "      writeCall('tmDashboardRunAiSentence', { sheetId, sentence: text, fromInputBox, confirmUncertain }, () => ctx.setIn('ai', { state: 'busy', message: '다른 AI 출결 입력이 끝나면 이어서 처리해요.' })).then(res => {\n",
  "        const s = res.status;\n",
  "        if (s === 'applied') {\n",
  "          const n = Number(res.rows) || 0;\n",
  "          const records = Array.isArray(res.records) ? res.records : [];\n",
  "          // 넣거나 고친 줄을 자료로 보인다(설계 §5.5). 한 줄이면 한 문장에, 여러 줄이면 아래에 줄마다.\n",
  "          const one = records.length === 1 ? ' · ' + aiRecordLine(records[0]) : '';\n",
  "          const message = res.updatedExisting\n",
  "            ? month.month + '월 출결 기록의 ' + n + '줄을 고쳤어요' + (one || '.')\n",
  "            : one ? month.month + '월 출결 기록에 ' + n + '줄' + one : month.month + '월 출결 기록에 ' + n + '줄을 입력했어요.';\n",
  "          ctx.setIn('ai', { state: 'applied', message, records: records.length > 1 ? records : [], text: '', boxSheetId: null });\n",
  "          ctx.refresh();\n",
  "        } else if (s === 'uncertain-pending' || s === 'entered-pending') {\n",
  "          ctx.setIn('ai', { state: 'idle', message: '' });\n",
  "          ctx.set({ dialog: { kind: 'b1warn', month: Object.assign({}, box, { inputText: text, inputUncertain: s === 'uncertain-pending', inputEntered: s === 'entered-pending' }) } });\n",
  "          ctx.refresh();\n",
  "        } else if (s === 'uncertain' || s === 'unknown') {\n",
  "          // 고침 문장이면 줄이 생겼는지가 아니라 그 줄이 바뀌었는지 보게 한다(SHEET-AI-03).\n",
  "          const what = s === 'uncertain' && res.updatedExisting\n",
  "            ? '수정 결과를 확인하지 못했어요. ' + month.month + '월 출결 기록에서 그 줄이 바뀌었는지'\n",
  "            : s === 'uncertain'\n",
  "              ? '등록 결과를 확인하지 못했어요. ' + month.month + '월 출결 기록에 줄이 생겼는지'\n",
  "              : '처리 결과를 확인하지 못했어요. ' + month.month + '월 출결 기록에 줄이 생기거나 바뀌었는지';\n",
  "          ctx.setIn('ai', { state: 'uncertain', message: what + ' 확인한 뒤 [↻ 새로 읽기]를 눌러 주세요.', locked: text });\n",
  "        } else if (s === 'busy') {\n",
  "          ctx.setIn('ai', { state: 'busy-final', message: '다른 AI 출결 입력이 끝나지 않았어요. 잠시 뒤 [AI로 입력]을 다시 눌러 주세요.' });\n",
  "        } else if (s === 'stale') {\n",
  "          ctx.setIn('ai', { state: 'check', message: res.message || '입력칸의 문장이 바뀌어 처리하지 않고 다시 읽었어요.', boxSheetId: null });\n",
  "          ctx.refresh();\n",
  "        } else if (s === 'account-mismatch') {\n",
  "          ctx.set({ fatal: ACCOUNTS_MSG, snap: null });\n",
  "        } else if (s === 'ignored') {\n",
  "          ctx.setIn('ai', { state: 'ignored', message: res.message || '처리하지 않았어요.' });\n",
  "        } else {\n",
  "          ctx.setIn('ai', { state: 'check', message: res.message || 'AI 출결 입력을 처리하지 못했어요. 잠시 뒤 [AI로 입력]을 다시 눌러 주세요.' });\n",
  "        }\n",
  "      });\n",
  "    };\n",
  "    const R = {\n",
  "      running: ['muted', '처리 중…'], busy: ['muted', '다른 입력 처리 중'], 'busy-final': ['warn', '다른 입력 처리 중'],\n",
  "      applied: ['ok', '입력함'], check: ['warn', '확인 필요'], uncertain: ['warn', '결과 확인 못 함'], ignored: ['muted', '처리하지 않음']\n",
  "    }[a.state];\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>문장으로 출결 입력 (AI)</h3></div>\n",
  "        ${D.months.filter(m => m.inputText).map(m => html`<div class=\"b1line\"><span class=\"tx\" title=${m.inputText}>${b1Text(m)}</span>${b1Chip(m) ? html`<${Chip} tone=\"warn\">${b1Chip(m)}</${Chip}>` : null}<${B1Buttons} ctx=${ctx} m=${m} /></div>`)}\n",
  "        ${usable || !D.raw.canWrite || !D.roster.length ? null : html`<div style=\"margin-bottom:12px\"><${Notice} tone=\"warn\">${AI_OFF_MSG}</${Notice}></div>`}\n",
  "        <div class=\"fgrid\">\n",
  "          <label class=\"fld\">입력할 달\n",
  "            <select value=${sheetId} disabled=${running || !writable.length} onChange=${e => ctx.setIn('ai', { sheetId: Number(e.target.value) })}>\n",
  "              ${writable.map(m => html`<option value=${m.sheetId} selected=${m.sheetId === sheetId}>${m.month + '월'}</option>`)}\n",
  "            </select></label>\n",
  "          <div></div>\n",
  "          <label class=\"fld full\">출결 문장\n",
  "            <textarea placeholder=\"예: 7번 조OO 오늘 늦잠으로 지각\" value=${a.text} disabled=${running}\n",
  "              onInput=${e => ctx.setIn('ai', { text: e.target.value })}></textarea></label>\n",
  "        </div>\n",
  "        <div class=\"foot\"><button class=\"btn\" disabled=${running || locked || !usable} onClick=${run}>${running ? '처리 중…' : 'AI로 입력'}</button></div>\n",
  "        ${R ? html`<div class=\"sec-l\">AI 처리 결과</div><div class=\"res\"><div class=\"r\"><${Chip} tone=${R[0]}>${R[1]}</${Chip}>${a.message ? html`<span>${a.message}</span>` : null}</div>\n",
  "          ${a.state === 'applied' ? (a.records || []).map(r => html`<div class=\"r ai-rec\">${aiRecordLine(r)}</div>`) : null}</div>` : null}\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function ManualCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, f = ctx.S.manual;\n",
  "    const reasons = topReasons(D, 6);\n",
  "    const labels = D.roster.map(s => s.label).filter(Boolean);\n",
  "    const student = labels.indexOf(f.student) >= 0 ? f.student : (labels[0] || '');\n",
  "    const date = f.date || (D.cal.inYear ? D.today : '');\n",
  "    const set = patch => ctx.setIn('manual', patch);\n",
  "    const key = JSON.stringify([date, student, f.cat, f.kind, f.reason.trim(), f.period, f.g, f.h]);\n",
  "    const running = f.state === 'running' || f.state === 'busy';\n",
  "    const monthEntry = isDateKey(date) ? D.monthByNumber.get(Number(date.slice(5, 7))) : null;\n",
  "    const usable = D.raw.canWrite && labels.length > 0;\n",
  "    const save = () => {\n",
  "      if (!isDateKey(date)) { set({ state: 'check', message: '날짜를 골라 주세요.' }); return; }\n",
  "      if (!f.reason.trim()) { set({ state: 'check', message: '사유를 입력해 주세요.' }); return; }\n",
  "      if (!monthEntry || !monthEntry.writable) { set({ state: 'check', message: Number(date.slice(5, 7)) + '월 출결표에는 대시보드에서 입력할 수 없어요. 시트에서 입력해 주세요.' }); return; }\n",
  "      if (D.cal.start && (date < D.cal.start || date > D.cal.end)) { set({ state: 'check', message: '이 출석부의 학년도 날짜만 입력할 수 있어요.' }); return; }\n",
  "      set({ state: 'running', message: '' });\n",
  "      writeCall('tmDashboardAppendAttendance', {\n",
  "        sheetId: monthEntry.sheetId, date, student, category: f.cat, kind: f.kind, reason: f.reason.trim(), period: f.period, report: f.g, attach: f.h\n",
  "      }, () => set({ state: 'busy' })).then(res => {\n",
  "        if (res.status === 'applied') {\n",
  "          set({ state: 'applied', message: res.message || monthEntry.month + '월 출결 기록에 1줄을 입력했어요.', reason: '' });\n",
  "          ctx.refresh();\n",
  "        } else if (res.status === 'uncertain' || res.status === 'unknown') {\n",
  "          set({ state: 'uncertain', message: '등록 결과를 확인하지 못했어요. ' + monthEntry.month + '월 출결 기록에 줄이 생겼는지 확인한 뒤 [↻ 새로 읽기]를 눌러 주세요.', locked: key });\n",
  "          ctx.refresh();\n",
  "        } else if (res.status === 'busy') {\n",
  "          set({ state: 'check', message: BUSY_FINAL });\n",
  "        } else if (res.status === 'account-mismatch') {\n",
  "          ctx.set({ fatal: ACCOUNTS_MSG, snap: null });\n",
  "        } else {\n",
  "          set({ state: 'check', message: res.message || '저장하지 못했어요.' });\n",
  "        }\n",
  "      });\n",
  "    };\n",
  "    const R = { running: ['muted', '저장 중…'], busy: ['muted', '저장 중…'], applied: ['ok', '입력함'], check: ['warn', '확인 필요'], uncertain: ['warn', '결과 확인 못 함'] }[f.state];\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>항목을 골라 출결 입력</h3></div>\n",
  "        <div class=\"fgrid\">\n",
  "          <label class=\"fld\">날짜<input type=\"date\" value=${date} min=${D.cal.start || null} max=${D.cal.end || null} onInput=${e => set({ date: e.target.value })} /></label>\n",
  "          <label class=\"fld\">학생 (번호+이름)\n",
  "            <select value=${student} onChange=${e => set({ student: e.target.value })}>\n",
  "              ${labels.map(l => html`<option value=${l} selected=${l === student}>${l}</option>`)}\n",
  "            </select></label>\n",
  "          <div class=\"fld full\">구분<div class=\"pills\">${CATS.map(c => html`<button class=${'pill' + (f.cat === c ? ' on' : '')} onClick=${() => set({ cat: c })}>${c}</button>`)}</div></div>\n",
  "          <div class=\"fld full\">종류<div class=\"pills\">${KINDS.map(c => html`<button class=${'pill' + (f.kind === c ? ' on' : '')} onClick=${() => set({ kind: c })}>${c}</button>`)}</div></div>\n",
  "          <label class=\"fld full\">사유<input placeholder=\"예: 감기\" maxlength=\"200\" value=${f.reason} onInput=${e => set({ reason: e.target.value })} /></label>\n",
  "          ${reasons.length ? html`<div class=\"fld full\" style=\"margin-top:-4px\"><div class=\"pills\">${reasons.map(c => html`<button class=\"pill\" onClick=${() => set({ reason: c })}>${c}</button>`)}</div></div>` : null}\n",
  "          <label class=\"fld\">교시 (선택)\n",
  "            <select value=${f.period} onChange=${e => set({ period: e.target.value })}>\n",
  "              <option value=\"\" selected=${!f.period}>—</option>\n",
  "              ${['1교시', '2교시', '3교시', '4교시', '5교시', '6교시', '7교시', '조회', '종례'].map(o => html`<option value=${o} selected=${f.period === o}>${o}</option>`)}\n",
  "            </select></label>\n",
  "          <div></div>\n",
  "          <label class=\"fld\">신고서<${DocField} value=${f.g} onChange=${val => set({ g: val })} /></label>\n",
  "          <label class=\"fld\">첨부<${DocField} value=${f.h} onChange=${val => set({ h: val })} /></label>\n",
  "        </div>\n",
  "        <div class=\"foot\"><button class=\"btn\" disabled=${running || !usable || f.locked === key} onClick=${save}>${running ? '저장 중…' : '출결 등록'}</button></div>\n",
  "        ${R ? html`<div class=\"res mt\"><div class=\"r\"><${Chip} tone=${R[0]}>${R[1]}</${Chip}>${f.message ? html`<span>${f.message}</span>` : null}</div></div>` : null}\n",
  "      </div>`;\n",
  "  }\n",
  "  const DocField = p => html`<select value=${p.value} onChange=${e => p.onChange(e.target.value)}>${['', '제출', '미제출', '해당없음'].map(o => html`<option value=${o} selected=${o === p.value}>${o || '(비움)'}</option>`)}</select>`;\n",
  "\n",
  "  function RecentCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D;\n",
  "    const todayMonth = Number(String(D.today).slice(5, 7));\n",
  "    const m = D.monthByNumber.get(todayMonth) || D.months[0] || { month: todayMonth };\n",
  "    const rows = D.rows.filter(r => r.sheetId === m.sheetId).sort((a, b) => b.row - a.row).slice(0, 6);\n",
  "    const toLedger = () => { ctx.set({ tab: 'home' }); setTimeout(() => { const el = document.getElementById('ledger-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0); };\n",
  "    return html`\n",
  "      <div class=\"card mt\">\n",
  "        <div class=\"card-h\"><h3>${m.month + '월 출결 기록 · 최근 입력 ' + rows.length + '줄'}</h3><button class=\"link\" onClick=${toLedger}>출결 기록에서 보기</button></div>\n",
  "        <div class=\"tbl-wrap\"><table><thead><tr><th>날짜</th><th>번호+이름</th><th>구분</th><th>종류</th><th>사유</th><th>신고서</th><th>첨부</th><th>AI</th></tr></thead><tbody>\n",
  "          ${rows.length ? rows.map(r => html`<tr class=${missing(r) ? 'rw' : ''}><td class=\"num\">${r.date ? mdw(r.date) : r.dateText}</td><td><b>${r.label}</b></td>\n",
  "            <td>${r.cat ? html`<span class=\"tag\">${r.cat}</span>` : null}</td><td>${r.kind ? html`<span class=\"tag\">${r.kind}</span>` : null}</td><td>${r.reason}</td>\n",
  "            <td>${r.g === '미제출' ? html`<${Chip} tone=\"warn\">미제출</${Chip}>` : (r.g || '—')}</td><td>${r.h === '미제출' ? html`<${Chip} tone=\"warn\">미제출</${Chip}>` : (r.h || '—')}</td>\n",
  "            <td>${r.ai ? html`<span class=\"ai\">AI</span>` : null}</td></tr>`) : html`<tr><td colspan=\"8\" class=\"empty\">아직 입력한 출결이 없어요.</td></tr>`}\n",
  "        </tbody></table></div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function EntryTab(p) {\n",
  "    const ctx = p.ctx;\n",
  "    return html`<div class=\"grid g-2\"><${AiCard} ctx=${ctx} /><${ManualCard} ctx=${ctx} /></div><${RecentCard} ctx=${ctx} />`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 메시지 보내기 (2단계) ---------- */\n",
  "  // 보내기는 늘 미리보기 → 확인 창 → 보내기 순서다. 미리보기의 token이 서버에서 다시 만든 것과\n",
  "  // 같을 때만 보낸다. 결과를 모르는 '발송중' 메시지가 있으면 선생님이 정하기 전까지 그 종류를\n",
  "  // 보내지 않는다(설계 §5.4, C7). 보내는 동안과 미리보는 동안 모든 보내기 버튼을 잠근다.\n",
  "  const goRemind = ctx => {\n",
  "    ctx.set({ tab: 'msg' });\n",
  "    setTimeout(() => { const el = document.getElementById('remind-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0);\n",
  "  };\n",
  "  const remindable = (D, r) => !!(monthOf(D, r).readable && missing(r)\n",
  "    && ['current', 'sending', 'sending-stale'].indexOf(r.rs) < 0);\n",
  "  function previewFailed(ctx, res) {\n",
  "    if (res && res.status === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null, dialog: null }); return; }\n",
  "    ctx.showToast((res && res.message) || '보낼 메시지를 읽지 못했어요. [↻ 새로 읽기]를 눌러 주세요.');\n",
  "  }\n",
  "  function startQueueSend(ctx, kind) {\n",
  "    if (ctx.S.sending) return;\n",
  "    ctx.set({ sending: true });\n",
  "    serverCall('tmDashboardPreviewQueue', { kind }).then(res => {\n",
  "      ctx.set({ sending: false });\n",
  "      if (!res || res.status !== 'ok') { previewFailed(ctx, res); return; }\n",
  "      if ((res.staleClaims || []).length) { ctx.set({ dialog: { kind: 'stale', items: res.staleClaims } }); return; }\n",
  "      const count = (res.personal || []).length + (res.class && res.class.lines.length ? 1 : 0);\n",
  "      if (!count) { ctx.showToast('보낼 메시지가 없어요.'); ctx.refresh(); return; }\n",
  "      ctx.set({ dialog: { kind: 'send', mode: 'queue', sendKind: kind, preview: res, notice: res.problem ? { tone: 'warn', text: res.problem } : null } });\n",
  "    }, err => { ctx.set({ sending: false }); previewFailed(ctx, looksLikeAccountError(err) ? { status: 'account-mismatch' } : null); });\n",
  "  }\n",
  "  function openStale(ctx) {\n",
  "    if (ctx.S.sending) return;\n",
  "    ctx.set({ sending: true });\n",
  "    serverCall('tmDashboardPreviewQueue', { kind: 'both' }).then(res => {\n",
  "      ctx.set({ sending: false });\n",
  "      if (!res || res.status !== 'ok') { previewFailed(ctx, res); return; }\n",
  "      if (!(res.staleClaims || []).length) { ctx.showToast('결과를 확인하지 못한 메시지가 없어요.'); ctx.refresh(); return; }\n",
  "      ctx.set({ dialog: { kind: 'stale', items: res.staleClaims } });\n",
  "    }, err => { ctx.set({ sending: false }); previewFailed(ctx, looksLikeAccountError(err) ? { status: 'account-mismatch' } : null); });\n",
  "  }\n",
  "  function startRemind(ctx, rows) {\n",
  "    if (ctx.S.sending || !rows.length) return;\n",
  "    const req = rows.map(r => ({ sheetId: r.sheetId, row: r.row, expectedFp: r.fp }));\n",
  "    ctx.set({ sending: true });\n",
  "    serverCall('tmDashboardPreviewRowReminders', { rows: req }).then(res => {\n",
  "      ctx.set({ sending: false });\n",
  "      if (!res || res.status !== 'ok') { previewFailed(ctx, res); return; }\n",
  "      if (!(res.groups || []).length) { ctx.showToast('보낼 독려 개인톡이 없어요.'); ctx.refresh(); return; }\n",
  "      ctx.set({ dialog: { kind: 'send', mode: 'reminder', rows: req, preview: res, notice: null } });\n",
  "    }, err => { ctx.set({ sending: false }); previewFailed(ctx, looksLikeAccountError(err) ? { status: 'account-mismatch' } : null); });\n",
  "  }\n",
  "\n",
  "  const QUEUE_ACTIONS = (ctx, q) => {\n",
  "    const D = ctx.D;\n",
  "    if (!D.raw.canWrite || q.st !== '대기' || !q.fp) return null;\n",
  "    const busy = !!ctx.S.busyIds['q:' + q.kind + ':' + q.r] || ctx.S.sending;\n",
  "    return html`<span class=\"actions\"><button class=\"ic-btn\" disabled=${busy} onClick=${() => ctx.set({ dialog: { kind: 'q-edit', q } })}>수정</button><button class=\"ic-btn\" disabled=${busy} onClick=${() => ctx.set({ dialog: { kind: 'q-delete', q } })}>삭제</button></span>`;\n",
  "  };\n",
  "  const queueWho = (q, roomName) => (q.kind === 'p' ? (q.label || q.name || '') + ' 개인톡' : roomName + ' 단체톡');\n",
  "\n",
  "  function MessagesTab(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, Q = p.Q, hl = ctx.S.health;\n",
  "    const roomName = String((D.raw.classInfo || {}).classRoomName || '학급');\n",
  "    const canWrite = !!D.raw.canWrite, sending = ctx.S.sending;\n",
  "    const qRow = (q, who, actions) => {\n",
  "      const st = qState(q, D.today);\n",
  "      const tip = [st[1] + (q.at ? ' · ' + mdhm(q.at) : ''), q.text].concat(q.res && q.st !== '발송중' ? [q.res] : []).join('\\n');\n",
  "      // 처리한 줄과 결과 미확인 줄은 처리 시각을 칩 글자에 함께 보인다(툴팁에만 두지 않음, SHEET-DASH-08 보완).\n",
  "      const label = st[1] + (!actions && q.at ? ' · ' + mdhm(q.at) : '');\n",
  "      return html`<div class=\"q-row\"><${Chip} tone=${st[0]}>${label}</${Chip}>${who ? html`<b>${who}</b>` : null}<span class=\"txt\" title=${tip}>${q.text}</span>${actions ? QUEUE_ACTIONS(ctx, q) : null}</div>`;\n",
  "    };\n",
  "    const none = html`<div class=\"faint small\">없음</div>`;\n",
  "    const pq = D.raw.personalQueue || {}, cqInfo = D.raw.classQueue || {};\n",
  "    const futureP = D.pq.filter(q => q.st === '대기' && isDateKey(q.d) && q.d > D.today);\n",
  "    // 최근 처리한 줄은 처리 시각(at)이 늦은 것부터 보인다. 시트 순서(오래된 것 먼저)로 두면 방금 보낸 줄이 맨 아래에 묻혔다(2026-10-01 현장).\n",
  "    const latestFirst = (a, b) => String(b.at || '').localeCompare(String(a.at || '')) || b.r - a.r;\n",
  "    const doneP = D.pq.filter(q => ['보냄', '실패', '제외'].indexOf(q.st) >= 0).sort(latestFirst);\n",
  "    const futureC = D.cq.filter(q => q.st === '대기' && isDateKey(q.d) && q.d > D.today);\n",
  "    const doneC = D.cq.filter(q => ['보냄', '실패', '제외'].indexOf(q.st) >= 0).sort(latestFirst);\n",
  "    const notices = [];\n",
  "    if (hl && hl.chat === 'needs_connect') notices.push('Google Chat 연결이 필요해요. Teacher Manager의 [Google 연결 → 출결]에서 연결해 주세요.');\n",
  "    if (!(D.raw.flags || {}).classRoomChosen) notices.push('학급 단톡방이 정해지지 않았어요. Teacher Manager의 [Google 연결 → 출결]에서 골라 주세요.');\n",
  "    if (pq.present && pq.layoutOk === false) notices.push('메신저 개인톡 내용 탭의 제목 줄이 달라 읽지 못했어요.');\n",
  "    if (cqInfo.present && cqInfo.layoutOk === false) notices.push('메신저 단체톡 내용 탭의 제목 줄이 달라 읽지 못했어요.');\n",
  "    const logInfo = D.raw.chatLog || {};\n",
  "    const staleN = Q.staleP.length + Q.staleC.length;\n",
  "    const staleWhat = Q.staleP.length && Q.staleC.length ? '메시지를' : Q.staleP.length ? '개인톡을' : '단체톡을';\n",
  "    const pN = Q.groups.size, cN = Q.classPend.length;\n",
  "    const canP = canWrite && !sending && pN > 0 && !Q.staleP.length && pq.layoutOk !== false;\n",
  "    const canC = canWrite && !sending && cN > 0 && !Q.staleC.length && cqInfo.layoutOk !== false;\n",
  "    const canBoth = canWrite && !sending && (pN > 0 || cN > 0) && !staleN && pq.layoutOk !== false && cqInfo.layoutOk !== false;\n",
  "    return html`\n",
  "      ${notices.length ? html`<div class=\"notices\">${notices.map(t => html`<${Notice} tone=\"warn\">${t}</${Notice}>`)}</div>` : null}\n",
  "      <div class=\"card\">\n",
  "        <div class=\"row\"><div><b>오늘 보낼 메시지</b> <span class=\"muted small\">${'개인톡 ' + pN + '명 · 단체톡 ' + cN + '줄'}</span></div><span class=\"spacer\"></span>\n",
  "          ${canWrite ? html`<button class=\"btn\" disabled=${!canBoth} onClick=${() => startQueueSend(ctx, 'both')}>${sending ? '확인 중…' : '개인톡+단체톡 한 번에 보내기'}</button>` : null}</div>\n",
  "      </div>\n",
  "      ${staleN ? html`<div class=\"mt\"><div class=\"notice warn row\" role=\"status\"><span>${'결과를 확인하지 못한 메시지 ' + staleN + '건이 있어 ' + staleWhat + ' 보낼 수 없어요.'}</span><span class=\"spacer\"></span>\n",
  "        ${canWrite ? html`<button class=\"btn-quiet btn-sm\" disabled=${sending} onClick=${() => openStale(ctx)}>확인하기</button>` : null}</div></div>` : null}\n",
  "      <div class=\"grid g-2 mt\">\n",
  "        <div class=\"card\">\n",
  "          <div class=\"card-h\"><h3>아직 보내지 못한 개인톡</h3>${canWrite ? html`<button class=\"btn-tonal btn-sm\" disabled=${!canP} onClick=${() => startQueueSend(ctx, 'personal')}>${'오늘 보낼 ' + pN + '명에게 보내기'}</button>` : null}</div>\n",
  "          ${pN ? Array.from(Q.groups.values()).map(g => html`<div class=\"q-grp warn\">\n",
  "            <div class=\"row\"><b>${g.label}</b><span class=\"q-meta\">${g.lines.length + '줄 · ' + g.lines.map(q => q.type).filter(Boolean).join(', ')}</span><span class=\"spacer\"></span>\n",
  "              ${emailKnownMissing(D, g.label) ? html`<${Chip} tone=\"warn\">이메일 없음</${Chip}>` : html`<${Chip} tone=\"warn\">오늘 보낼 차례</${Chip}>`}</div>\n",
  "            ${g.lines.map(q => html`<div class=\"q-line\"><span class=\"tx\">${q.text} <span class=\"q-meta\">${'· ' + (q.src || '')}</span>\n",
  "              ${q.res ? html`<br /><${Chip} tone=\"warn\" title=${q.res}>${'연결 문제로 멈춤 · ' + mdhm(q.at)}</${Chip}>` : null}</span>${QUEUE_ACTIONS(ctx, q)}</div>`)}\n",
  "          </div>`) : none}\n",
  "          ${Q.staleP.length ? html`<div class=\"sec-l\">결과를 확인하지 못한 개인톡</div>${Q.staleP.map(q => qRow(q, q.label, false))}` : null}\n",
  "          <div class=\"sec-l\">보낼 날짜가 아직 안 된 개인톡</div>${futureP.length ? futureP.map(q => qRow(q, q.label, true)) : none}\n",
  "          <div class=\"sec-l\">최근 처리한 개인톡</div>${doneP.length ? doneP.map(q => qRow(q, q.label, false)) : none}\n",
  "        </div>\n",
  "        <div class=\"card\">\n",
  "          <div class=\"card-h\"><h3>아직 보내지 못한 단체톡</h3>${canWrite ? html`<button class=\"btn-tonal btn-sm\" disabled=${!canC} onClick=${() => startQueueSend(ctx, 'class')}>${'오늘 보낼 ' + cN + '줄 보내기'}</button>` : null}</div>\n",
  "          ${cN ? html`<div class=\"q-grp warn\"><div class=\"row\"><b>${roomName + ' 단톡방에 보낼 메시지'}</b><span class=\"spacer\"></span><${Chip} tone=\"warn\">오늘 보낼 차례</${Chip}></div>\n",
  "            ${Q.classPend.map(q => html`<div class=\"q-line\"><span class=\"tx\">${q.text} <span class=\"q-meta\">${'· ' + [q.type, q.src].filter(Boolean).join(' · ')}</span>\n",
  "              ${q.res ? html`<br /><${Chip} tone=\"warn\" title=${q.res}>${'연결 문제로 멈춤 · ' + mdhm(q.at)}</${Chip}>` : null}</span>${QUEUE_ACTIONS(ctx, q)}</div>`)}</div>` : none}\n",
  "          ${Q.staleC.length ? html`<div class=\"sec-l\">결과를 확인하지 못한 단체톡</div>${Q.staleC.map(q => qRow(q, '', false))}` : null}\n",
  "          <div class=\"sec-l\">보낼 날짜가 아직 안 된 단체톡</div>${futureC.length ? futureC.map(q => qRow(q, '', true)) : none}\n",
  "          <div class=\"sec-l\">최근 처리한 단체톡</div>${doneC.length ? doneC.map(q => qRow(q, '', false)) : none}\n",
  "        </div>\n",
  "      </div>\n",
  "      <div class=\"grid g-2 mt\"><${AddMessageCard} ctx=${ctx} /><${RemindCard} ctx=${ctx} /></div>\n",
  "      <div class=\"card mt\" id=\"log-card\">\n",
  "        <div class=\"card-h\"><h3>Google Chat 발송기록</h3><span class=\"note\">${logInfo.total ? (logInfo.total > D.log.length ? '최근 ' + D.log.length + '건 · 전체 ' + logInfo.total + '건' : D.log.length + '건') : ''}</span></div>\n",
  "        <div class=\"tbl-wrap\" style=\"max-height:340px\"><table><thead><tr><th>발송시각</th><th>종류</th><th>대상</th><th>내용 미리보기</th><th>결과</th><th>오류</th></tr></thead><tbody>\n",
  "          ${D.log.length ? D.log.map(l => html`<tr class=${String(l.result || '').indexOf('성공') === 0 ? '' : 'rw'}><td class=\"num\">${mdhm(l.at)}</td><td>${l.kind}</td><td><b>${l.target}</b></td>\n",
  "            <td class=\"wrap muted\">${l.text}</td><td><${LogChip} result=${l.result} /></td><td class=\"wrap muted\">${l.error || '—'}</td></tr>`)\n",
  "            : html`<tr><td colspan=\"6\" class=\"empty\">발송기록이 없어요.</td></tr>`}\n",
  "        </tbody></table></div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function AddMessageCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, f = ctx.S.addMsg;\n",
  "    const set = patch => ctx.setIn('addMsg', patch);\n",
  "    const labels = D.roster.map(s => s.label).filter(Boolean);\n",
  "    const personal = f.kind === 'personal';\n",
  "    const student = labels.indexOf(f.student) >= 0 ? f.student : (labels[0] || '');\n",
  "    const date = f.date || D.today;\n",
  "    const types = personal ? PERSONAL_TYPES : CLASS_TYPES;\n",
  "    const type = types.indexOf(f.type) >= 0 ? f.type : '기타';\n",
  "    const running = f.state === 'running';\n",
  "    const key = JSON.stringify([f.kind, date, personal ? student : '', type, f.content.trim()]);\n",
  "    const usable = D.raw.canWrite && (!personal || labels.length > 0);\n",
  "    const add = () => {\n",
  "      const content = f.content.trim();\n",
  "      if (!content) { set({ state: 'check', message: '메시지 내용을 입력해 주세요.' }); return; }\n",
  "      if (!isDateKey(date) || date < D.today) { set({ state: 'check', message: '보낼 날짜는 오늘이나 그 뒤로 골라 주세요.' }); return; }\n",
  "      set({ state: 'running', message: '' });\n",
  "      writeCall('tmDashboardAddQueueItem', { kind: f.kind, sendDate: date, student: personal ? student : '', type, content }).then(res => {\n",
  "        if (res.status === 'applied') {\n",
  "          set({ state: res.duplicate ? 'dup' : 'applied', message: res.message || '보낼 목록에 넣었어요.', content: res.duplicate ? f.content : '' });\n",
  "          ctx.refresh();\n",
  "        } else if (res.status === 'unknown') {\n",
  "          set({ state: 'uncertain', message: '저장 결과를 확인하지 못했어요. [↻ 새로 읽기]로 보낼 목록을 확인해 주세요.', locked: key });\n",
  "          ctx.refresh();\n",
  "        } else if (res.status === 'busy') {\n",
  "          set({ state: 'check', message: BUSY_FINAL });\n",
  "        } else if (res.status === 'account-mismatch') {\n",
  "          ctx.set({ fatal: ACCOUNTS_MSG, snap: null });\n",
  "        } else {\n",
  "          set({ state: 'check', message: res.message || '저장하지 못했어요.' });\n",
  "        }\n",
  "      });\n",
  "    };\n",
  "    const R = { running: ['muted', '저장 중…'], applied: ['ok', '넣음'], dup: ['muted', '이미 있음'], check: ['warn', '확인 필요'], uncertain: ['warn', '결과 확인 못 함'] }[f.state];\n",
  "    return html`\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>보낼 메시지 추가</h3></div>\n",
  "        <div class=\"fgrid\">\n",
  "          <div class=\"fld full\">보낼 곳<div class=\"pills\">${[['personal', '개인톡'], ['class', '단체톡']].map(k => html`<button class=${'pill' + (f.kind === k[0] ? ' on' : '')} disabled=${running} onClick=${() => set({ kind: k[0], state: 'idle', message: '' })}>${k[1]}</button>`)}</div></div>\n",
  "          <label class=\"fld\">보낼 날짜<input type=\"date\" value=${date} min=${D.today} onInput=${e => set({ date: e.target.value })} /></label>\n",
  "          ${personal ? html`<label class=\"fld\">학생 (번호+이름)\n",
  "            <select value=${student} onChange=${e => set({ student: e.target.value })}>${labels.map(l => html`<option value=${l} selected=${l === student}>${l}</option>`)}</select></label>` : html`<div></div>`}\n",
  "          <label class=\"fld\">종류<select value=${type} onChange=${e => set({ type: e.target.value })}>${types.map(t => html`<option value=${t} selected=${t === type}>${t}</option>`)}</select></label>\n",
  "          <div></div>\n",
  "          <label class=\"fld full\">내용<textarea maxlength=\"1000\" value=${f.content} disabled=${running} onInput=${e => set({ content: e.target.value })}></textarea></label>\n",
  "        </div>\n",
  "        <div class=\"foot\"><button class=\"btn\" disabled=${running || !usable || f.locked === key} onClick=${add}>${running ? '저장 중…' : '보낼 메시지에 추가'}</button></div>\n",
  "        ${R ? html`<div class=\"res mt\"><div class=\"r\"><${Chip} tone=${R[0]}>${R[1]}</${Chip}>${f.message ? html`<span>${f.message}</span>` : null}</div></div>` : null}\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function RemindCard(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, checked = ctx.S.remind || {};\n",
  "    const rows = D.rows.filter(r => remindable(D, r)).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.n - b.n));\n",
  "    const picked = rows.filter(r => checked[r.id]);\n",
  "    const toggle = r => ctx.set(prev => { const next = Object.assign({}, prev.remind); if (next[r.id]) delete next[r.id]; else next[r.id] = true; return { remind: next }; });\n",
  "    return html`\n",
  "      <div class=\"card\" id=\"remind-card\">\n",
  "        <div class=\"card-h\"><h3>신고서·첨부 미제출 독려 개인톡</h3><span class=\"note\">${rows.length + '줄'}</span></div>\n",
  "        <div class=\"tbl-wrap\" style=\"max-height:300px\"><table><tbody>\n",
  "          ${rows.length ? rows.map(r => html`<tr key=${r.id}>\n",
  "            <td><input type=\"checkbox\" aria-label=${r.label + ' ' + (r.date ? mdw(r.date) : r.dateText)} checked=${!!checked[r.id]} disabled=${ctx.S.sending || !D.raw.canWrite} onChange=${() => toggle(r)} /></td>\n",
  "            <td class=\"num\">${r.date ? mdw(r.date) : r.dateText}</td><td><b>${r.label}</b></td><td>${kShort(r.kind)}</td><td>${missingText(r)}</td>\n",
  "            <td><${RsCell} r=${r} />${emailKnownMissing(D, r.label) ? html` <${Chip} tone=\"warn\">이메일 없음</${Chip}>` : null}</td></tr>`)\n",
  "            : html`<tr><td class=\"empty\">${D.cover.months ? '독려할 줄이 없어요.' : '일부 출결표를 읽지 못했어요.'}</td></tr>`}\n",
  "        </tbody></table></div>\n",
  "        <div class=\"foot\"><button class=\"btn\" disabled=${!picked.length || ctx.S.sending || !D.raw.canWrite} onClick=${() => startRemind(ctx, picked)}>${'선택한 ' + picked.length + '줄 개인톡 보내기'}</button></div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  function LogChip(p) {\n",
  "    const res = String(p.result || '');\n",
  "    if (res.indexOf('성공') === 0) return html`<${Chip} tone=\"ok\">${res}</${Chip}>`;\n",
  "    const map = { '연결필요': '연결 필요', '중단': '연결 문제로 멈춤', '건너뜀': '보내지 않음 · 이메일 없음', '기록주의': '보냄 · 기록 확인', '실패': '실패' };\n",
  "    return html`<${Chip} tone=\"warn\">${map[res] || res || '결과 없음'}</${Chip}>`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 학생별 출결 ---------- */\n",
  "  function StudentsTab(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, v = ctx.S.view;\n",
  "    const P = periodOf(D, v), tm = termMi(D);\n",
  "    const rows = filterRows(D, Object.assign({}, v, { cat: '', kind: '', student: '', q: '' }));\n",
  "    const setView = patch => ctx.setIn('view', patch);\n",
  "    return html`\n",
  "      <div class=\"filters\"><span class=\"flabel\">기간</span>\n",
  "        <div class=\"seg\">${Object.keys(D.cal.periods).map(k => html`<button class=${v.period === k ? 'on' : ''} onClick=${() => setView({ period: k })}>${D.cal.periods[k].label}</button>`)}</div>\n",
  "      </div>\n",
  "      ${D.roster.length ? null : html`<${Notice} tone=\"warn\">${ROSTER_EMPTY}</${Notice}>`}\n",
  "      <div class=\"gal\">${D.roster.map(s => {\n",
  "        const my = rows.filter(r => r.label === s.label);\n",
  "        const counts = KINDS.map(k => my.filter(r => r.kind === k).length);\n",
  "        const mi = my.filter(r => r.cat === '미인정').length;\n",
  "        const ms = D.rows.filter(r => r.label === s.label && missing(r)).length;\n",
  "        const t = tm[s.label] || 0;\n",
  "        const last = my.slice().sort((a, b) => (a.date < b.date ? 1 : -1))[0];\n",
  "        const warn = ms || s.hasEmail === false || t >= 3;\n",
  "        return html`<div class=${'st' + (warn ? ' warn' : '')} key=${s.label}>\n",
  "          <div class=\"st-h\"><div class=\"st-n\">${s.n}</div><div class=\"st-nm\">${s.name}</div><span class=\"spacer\"></span>${mi ? html`<span class=\"tag\">${'미인정 ' + mi}</span>` : null}</div>\n",
  "          <div class=\"st-c\">${KINDS.map((k, i) => html`<div>${kShort(k)}<b>${counts[i]}</b></div>`)}</div>\n",
  "          <div class=\"row\" style=\"gap:5px;min-height:22px\">\n",
  "            ${ms ? html`<${Chip} tone=\"warn\">${'서류 미제출 ' + ms}</${Chip}>` : null}\n",
  "            ${s.hasEmail === false ? html`<${Chip} tone=\"warn\">이메일 없음</${Chip}>` : null}\n",
  "            ${t >= 3 ? html`<${Chip} tone=\"warn\">${'미인정 ' + t + '회 · 상담 확인'}</${Chip}>` : null}\n",
  "            ${warn ? null : html`<span class=\"faint small\">조치할 일 없음</span>`}</div>\n",
  "          <div class=\"st-last\">${last ? '최근 ' + (last.date ? mdw(last.date) : last.dateText) + ' ' + last.reason + ' ' + kShort(last.kind) : '이 기간 출결 변동 없음'}</div>\n",
  "          <div class=\"actions\"><button class=\"btn-quiet btn-sm\" disabled=${!D.raw.canWrite}\n",
  "            onClick=${() => { ctx.set(prev => ({ tab: 'entry', manual: Object.assign({}, prev.manual, { student: s.label, state: 'idle', message: '' }) })); window.scrollTo(0, 0); }}>출결 입력</button></div>\n",
  "        </div>`;\n",
  "      })}</div>\n",
  "      <div class=\"small faint mt\">${P.label}</div>`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 휴일 ---------- */\n",
  "  function HolidaysTab(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, hs = ctx.S.holiday;\n",
  "    const years = Array.from(new Set(D.holidays.map(x => String(x.d || '').slice(0, 4)).filter(y => /^\\d{4}$/.test(y))\n",
  "      .concat(D.cal.sy ? [String(D.cal.sy), String(D.cal.sy + 1)] : []))).sort();\n",
  "    const year = hs.year || (years.indexOf(String(D.today).slice(0, 4)) >= 0 ? String(D.today).slice(0, 4) : years[0] || '');\n",
  "    const q = String(hs.q || '').trim();\n",
  "    const list = D.holidays.filter(x => String(x.d || '').slice(0, 4) === year && (!hs.type || x.type === hs.type)\n",
  "      && (!q || [x.name, x.d, x.note].join(' ').indexOf(q) >= 0));\n",
  "    const types = Array.from(new Set(D.holidays.map(x => x.type).filter(Boolean)));\n",
  "    const need = D.cal.sy ? [D.cal.sy, D.cal.sy + 1].filter(y => !D.holidays.some(x => String(x.d || '').slice(0, 4) === String(y))) : [];\n",
  "    const set = patch => ctx.setIn('holiday', patch);\n",
  "    return html`\n",
  "      <div class=\"filters\">\n",
  "        <span class=\"flabel\">연도</span><div class=\"seg\">${years.map(y => html`<button class=${year === y ? 'on' : ''} onClick=${() => set({ year: y })}>${y + '년'}</button>`)}</div>\n",
  "        <span class=\"flabel\">구분</span><select class=\"sel\" value=${hs.type} onChange=${e => set({ type: e.target.value })}><option value=\"\" selected=${!hs.type}>전체</option>${types.map(t => html`<option value=${t} selected=${hs.type === t}>${t}</option>`)}</select>\n",
  "        <span class=\"spacer\"></span><input class=\"search\" placeholder=\"명칭·날짜 검색\" value=${hs.q} onInput=${e => set({ q: e.target.value })} />\n",
  "      </div>\n",
  "      ${D.raw.holidaysPresent === false ? html`<div style=\"margin-bottom:12px\"><${Notice} tone=\"warn\">휴일 탭을 찾지 못했어요.</${Notice}></div>` : null}\n",
  "      ${need.map(y => html`<div style=\"margin-bottom:12px\"><${Notice} tone=\"warn\">${y + '년 휴일이 없어 ' + y + '년 날짜의 AI 출결 입력이 멈춰요.'}</${Notice}></div>`)}\n",
  "      <div class=\"card\">\n",
  "        <div class=\"card-h\"><h3>${year + '년 휴일·학사일정 ' + list.length + '개'}</h3></div>\n",
  "        <div class=\"tbl-wrap\"><table><thead><tr><th>날짜</th><th>명칭</th><th>구분</th><th>상태</th><th>비고</th><th>출처</th></tr></thead><tbody>\n",
  "          ${list.length ? list.map(x => html`<tr><td class=\"num\">${mdw(x.d)}</td><td><b>${x.name}</b></td><td>${x.type ? html`<span class=\"tag\">${x.type}</span>` : null}</td>\n",
  "            <td class=\"muted\">${x.state}</td><td class=\"faint\">${x.note || '—'}</td>\n",
  "            <td>${SAFE_URL.test(String(x.src || '')) ? html`<a class=\"link\" href=${x.src} target=\"_blank\" rel=\"noopener noreferrer\">바로가기</a>` : html`<span class=\"muted small\">${x.src || '—'}</span>`}</td></tr>`)\n",
  "            : html`<tr><td colspan=\"6\" class=\"empty\">조건에 맞는 휴일이 없어요.</td></tr>`}\n",
  "        </tbody></table></div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 학생명단·설정 ---------- */\n",
  "  function DataTab(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, c = D.raw.classInfo || {}, f = D.raw.flags || {}, hl = ctx.S.health;\n",
  "    const aiCell = !hl ? html`<${Chip} tone=\"muted\">확인 중…</${Chip}>` : hl.ai === 'ready' ? html`<${Chip} tone=\"ok\">켜짐</${Chip}>`\n",
  "      : hl.ai === 'action' ? html`<${Chip} tone=\"warn\" title=${TM_CHECK}>확인 필요</${Chip}>` : html`<${Chip} tone=\"muted\">확인하지 못했어요</${Chip}>`;\n",
  "    const setupCell = D.raw.setup === 'confirmed' ? html`<${Chip} tone=\"ok\">확인됨</${Chip}>`\n",
  "      : D.raw.setup === 'not-done' ? html`<${Chip} tone=\"warn\">끝나지 않음</${Chip}>`\n",
  "      : D.raw.setup === 'other-account' ? html`<${Chip} tone=\"muted\">다른 계정이 설정함</${Chip}>` : html`<${Chip} tone=\"warn\">확인 필요</${Chip}>`;\n",
  "    const settings = [['학교', c.schoolName], ['학년도', c.schoolYear], ['학년·반', c.grade && c.classNumber ? c.grade + '학년 ' + c.classNumber + '반' : c.classLabel],\n",
  "      ['담임', c.teacherName], ['학급 단톡방', c.classRoomName], ['Tasks 목록', c.taskListTitle], ['신고서 저장 폴더', c.destFolderName]];\n",
  "    const current = D.monthByNumber.get(Number(String(D.today).slice(5, 7)));\n",
  "    const monthTarget = current && current.name ? current : D.months.find(m => m.name);\n",
  "    const tabs = [\n",
  "      [D.months.length ? D.months[0].month + '월 ~ ' + D.months[D.months.length - 1].month + '월 (' + D.months.length + '개)' : '월별 출결표', '보기·입력·수정', monthTarget ? { sheetId: monthTarget.sheetId } : null],\n",
  "      ['학생명단', '보기', { tab: 'roster' }], ['메신저 개인톡 내용', '보기', { tab: 'personal' }], ['메신저 단체톡 내용', '보기', { tab: 'class' }],\n",
  "      ['발송기록', '보기', { tab: 'log' }], ['휴일', '보기', { tab: 'holiday' }], ['설정', '일부 보기', { tab: 'config' }], ['드롭다운', '입력 선택지', { tab: 'dropdown' }],\n",
  "      ['템플릿_치환표', '쓰지 않음', { tab: 'template' }], ['00_사용법', '쓰지 않음', { tab: 'guide' }]];\n",
  "    return html`\n",
  "      <div class=\"grid g-2\">\n",
  "        <div class=\"card\">\n",
  "          <div class=\"card-h\"><h3>${'학생명단 ' + D.roster.length + '명'}</h3></div>\n",
  "          <div class=\"tbl-wrap\" style=\"max-height:470px\"><table><thead><tr><th>번호</th><th>이름</th><th>Google 이메일</th></tr></thead><tbody>\n",
  "            ${D.roster.length ? D.roster.map(s => html`<tr class=${s.hasEmail ? '' : 'rw'}><td class=\"num\">${s.n}</td><td><b>${s.name}</b>${s.dup ? html` <${Chip} tone=\"warn\">같은 번호+이름 있음</${Chip}>` : null}</td>\n",
  "              <td>${s.hasEmail ? html`<span class=\"muted\">등록됨</span>` : html`<${Chip} tone=\"warn\">없음</${Chip}>`}</td></tr>`)\n",
  "              : html`<tr><td colspan=\"3\" class=\"empty\">학생명단이 비어 있어요.</td></tr>`}\n",
  "          </tbody></table></div>\n",
  "        </div>\n",
  "        <div>\n",
  "          <div class=\"card\">\n",
  "            <div class=\"card-h\"><h3>학급 설정</h3></div>\n",
  "            <div class=\"tbl-wrap\"><table><tbody>\n",
  "              ${settings.map(sv => html`<tr><td class=\"muted\" style=\"width:40%\">${sv[0]}</td><td>${sv[1] ? String(sv[1]) : '—'}</td></tr>`)}\n",
  "              <tr><td class=\"muted\">AI 출결 입력</td><td>${aiCell}</td></tr>\n",
  "              <tr><td class=\"muted\">Gemini 연결 키</td><td><span class=\"muted\">${f.hasGeminiKey ? '등록됨' : '없음'}</span></td></tr>\n",
  "              <tr><td class=\"muted\">처음 설정</td><td>${setupCell}</td></tr>\n",
  "            </tbody></table></div>\n",
  "          </div>\n",
  "          <div class=\"card mt\">\n",
  "            <div class=\"card-h\"><h3>시트 탭별 대시보드 사용 범위</h3></div>\n",
  "            <div class=\"tbl-wrap\"><table><thead><tr><th>탭</th><th>대시보드에서</th><th></th></tr></thead><tbody>\n",
  "              ${tabs.map(t => html`<tr><td><b>${t[0]}</b></td><td class=\"muted\">${t[1]}</td><td class=\"act\">${t[2] ? html`<button class=\"link\" onClick=${() => openTab(ctx, t[2])}>시트에서 열기</button>` : null}</td></tr>`)}\n",
  "            </tbody></table></div>\n",
  "          </div>\n",
  "        </div>\n",
  "      </div>`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 대화상자 ---------- */\n",
  "  function DialogHost(p) {\n",
  "    const ctx = p.ctx, d = ctx.S.dialog;\n",
  "    const close = () => ctx.set({ dialog: null });\n",
  "    const inner = d.kind === 'edit' ? html`<${EditDialog} ctx=${ctx} row=${d.row} close=${close} />`\n",
  "      : d.kind === 'period' ? html`<${PeriodDialog} ctx=${ctx} close=${close} />`\n",
  "      : d.kind === 'b1warn' ? html`<${B1WarnDialog} ctx=${ctx} m=${d.month} close=${close} />`\n",
  "      : d.kind === 'send' ? html`<${SendDialog} ctx=${ctx} d=${d} close=${close} />`\n",
  "      : d.kind === 'send-result' ? html`<${SendResultDialog} ctx=${ctx} d=${d} close=${close} />`\n",
  "      : d.kind === 'stale' ? html`<${StaleDialog} ctx=${ctx} d=${d} close=${close} />`\n",
  "      : d.kind === 'q-edit' ? html`<${QueueEditDialog} ctx=${ctx} q=${d.q} close=${close} />`\n",
  "      : d.kind === 'q-delete' ? html`<${QueueDeleteDialog} ctx=${ctx} q=${d.q} close=${close} />` : null;\n",
  "    const wide = ['send', 'send-result', 'stale'].indexOf(d.kind) >= 0;\n",
  "    return html`<div class=\"ov\" onClick=${e => { if (e.target === e.currentTarget && !ctx.S.dialogBusy) close(); }}><div class=${'ov-box' + (wide ? ' wide' : '')} role=\"dialog\" aria-modal=\"true\">${inner}</div></div>`;\n",
  "  }\n",
  "\n",
  "  function EditDialog(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, r = p.row;\n",
  "    const [f, setF] = useState({ date: r.date, student: r.label, cat: r.cat, kind: r.kind, reason: r.reason, period: r.period, g: r.g, h: r.h });\n",
  "    const [state, setState] = useState({ running: false, message: '' });\n",
  "    const labels = D.roster.map(s => s.label).filter(Boolean);\n",
  "    const upd = patch => setF(Object.assign({}, f, patch));\n",
  "    const monthStart = r.date ? r.date.slice(0, 8) + '01' : null;\n",
  "    const monthLast = r.date ? monthEnd(Number(r.date.slice(0, 4)), Number(r.date.slice(5, 7))) : null;\n",
  "    const save = () => {\n",
  "      const values = {};\n",
  "      if (f.date !== r.date) values.date = f.date;\n",
  "      if (f.student !== r.label) values.student = f.student;\n",
  "      if (f.cat !== r.cat) values.category = f.cat;\n",
  "      if (f.kind !== r.kind) values.kind = f.kind;\n",
  "      if (f.reason.trim() !== r.reason.trim()) values.reason = f.reason.trim();\n",
  "      if (f.period !== r.period) values.period = f.period;\n",
  "      if (f.g !== r.g) values.report = f.g;\n",
  "      if (f.h !== r.h) values.attach = f.h;\n",
  "      if (!Object.keys(values).length) { p.close(); return; }\n",
  "      setState({ running: true, message: '' });\n",
  "      ctx.set({ dialogBusy: true });\n",
  "      writeCall('tmDashboardUpdateAttendance', { sheetId: r.sheetId, row: r.row, expectedFp: r.fp, values }).then(res => {\n",
  "        ctx.set({ dialogBusy: false });\n",
  "        if (res.status === 'applied') { p.close(); handleWriteResult(ctx, res, res.message || r.month + '월 출결 기록을 고쳤어요.'); return; }\n",
  "        const text = res.status === 'stale' ? (res.message || STALE_MSG) : res.status === 'busy' ? BUSY_FINAL\n",
  "          : res.status === 'uncertain' || res.status === 'unknown' ? (res.message || UNKNOWN_WRITE) : (res.message || '저장하지 못했어요.');\n",
  "        setState({ running: false, message: text, done: res.status === 'stale' || res.status === 'uncertain' || res.status === 'unknown' });\n",
  "        if (res.status === 'stale' || res.status === 'uncertain' || res.status === 'unknown') ctx.refresh();\n",
  "        if (res.status === 'account-mismatch') ctx.set({ fatal: ACCOUNTS_MSG, snap: null, dialog: null });\n",
  "      });\n",
  "    };\n",
  "    const opt = (list, value, blank) => (blank ? [''] : []).concat(list).map(o => html`<option value=${o} selected=${o === value}>${o || blank || ''}</option>`);\n",
  "    return html`\n",
  "      <h4>${'출결 기록 수정 · ' + r.label + ' ' + (r.date ? mdw(r.date) : r.dateText)}</h4>\n",
  "      ${state.message ? html`<div class=\"mt\"><${Notice} tone=\"warn\">${state.message}</${Notice}></div>` : null}\n",
  "      <div class=\"fgrid mt\">\n",
  "        <label class=\"fld\">날짜<input type=\"date\" value=${f.date} min=${monthStart} max=${monthLast} onInput=${e => upd({ date: e.target.value })} /></label>\n",
  "        <label class=\"fld\">학생<select value=${f.student} onChange=${e => upd({ student: e.target.value })}>${opt(labels.indexOf(r.label) >= 0 || !r.label ? labels : [r.label].concat(labels), f.student, r.label ? '' : '—')}</select></label>\n",
  "        <label class=\"fld\">구분<select value=${f.cat} onChange=${e => upd({ cat: e.target.value })}>${opt(CATS.indexOf(r.cat) >= 0 || !r.cat ? CATS : [r.cat].concat(CATS), f.cat, r.cat ? '' : '—')}</select></label>\n",
  "        <label class=\"fld\">종류<select value=${f.kind} onChange=${e => upd({ kind: e.target.value })}>${opt(KINDS.indexOf(r.kind) >= 0 || !r.kind ? KINDS : [r.kind].concat(KINDS), f.kind, r.kind ? '' : '—')}</select></label>\n",
  "        <label class=\"fld full\">사유<input value=${f.reason} maxlength=\"200\" onInput=${e => upd({ reason: e.target.value })} /></label>\n",
  "        <label class=\"fld\">교시<select value=${f.period} onChange=${e => upd({ period: e.target.value })}>${opt(['1교시', '2교시', '3교시', '4교시', '5교시', '6교시', '7교시', '조회', '종례'], f.period, '—')}</select></label>\n",
  "        <div></div>\n",
  "        <label class=\"fld\">신고서<${DocField} value=${f.g} onChange=${val => upd({ g: val })} /></label>\n",
  "        <label class=\"fld\">첨부<${DocField} value=${f.h} onChange=${val => upd({ h: val })} /></label>\n",
  "      </div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" disabled=${state.running} onClick=${p.close}>${state.done ? '닫기' : '취소'}</button>\n",
  "        ${state.done ? null : html`<button class=\"btn\" disabled=${state.running} onClick=${save}>${state.running ? '저장 중…' : '변경 저장'}</button>`}</div>`;\n",
  "  }\n",
  "\n",
  "  function PeriodDialog(p) {\n",
  "    const ctx = p.ctx, D = ctx.D, P = periodOf(D, ctx.S.view);\n",
  "    const [s, setS] = useState(isDateKey(P.start) && P.start !== '0000-01-01' ? P.start : D.cal.start || '');\n",
  "    const [e, setE] = useState(isDateKey(P.end) && P.end !== '9999-12-31' ? P.end : D.cal.end || '');\n",
  "    const [msg, setMsg] = useState('');\n",
  "    const apply = () => {\n",
  "      if (!isDateKey(s) || !isDateKey(e) || s > e) { setMsg('시작 날짜가 끝 날짜보다 늦어요.'); return; }\n",
  "      ctx.setIn('view', { period: 'custom', custom: { label: md(s) + '~' + md(e), short: md(s) + '~' + md(e), start: s, end: e }, limit: TABLE_PAGE });\n",
  "      p.close();\n",
  "    };\n",
  "    return html`\n",
  "      <h4>기간 선택</h4>\n",
  "      ${msg ? html`<div class=\"mt\"><${Notice} tone=\"warn\">${msg}</${Notice}></div>` : null}\n",
  "      <div class=\"fgrid mt\">\n",
  "        <label class=\"fld\">시작 날짜<input type=\"date\" value=${s} min=${D.cal.start || null} max=${D.cal.end || null} onInput=${ev => setS(ev.target.value)} /></label>\n",
  "        <label class=\"fld\">끝 날짜<input type=\"date\" value=${e} min=${D.cal.start || null} max=${D.cal.end || null} onInput=${ev => setE(ev.target.value)} /></label>\n",
  "      </div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" onClick=${p.close}>취소</button><button class=\"btn\" onClick=${apply}>적용</button></div>`;\n",
  "  }\n",
  "\n",
  "  function B1WarnDialog(p) {\n",
  "    const ctx = p.ctx, m = p.m;\n",
  "    const toLedger = () => {\n",
  "      ctx.set(prev => ({ dialog: null, tab: 'home', view: Object.assign({}, prev.view, { q: '', period: 'year', limit: TABLE_PAGE }) }));\n",
  "      setTimeout(() => { const el = document.getElementById('ledger-card'); if (el) el.scrollIntoView({ block: 'start' }); }, 0);\n",
  "    };\n",
  "    const entered = !!m.inputEntered && !m.inputUncertain;\n",
  "    return html`\n",
  "      <h4>${entered ? '이미 출결 기록에 입력한 문장이에요' : '등록 결과를 확인하지 못한 문장이에요'}</h4>\n",
  "      <div class=\"ov-list\"><div>${m.inputText}</div></div>\n",
  "      <${Notice} tone=\"warn\">${(entered ? '다시 입력하면 같은 줄이 두 번 들어가요. ' : '같은 줄이 두 번 들어갈 수 있어요. ') + m.month + '월 출결 기록을 먼저 확인해 주세요.'}</${Notice}>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" onClick=${p.close}>취소</button><button class=\"btn-quiet\" onClick=${toLedger}>${m.month + '월 출결 기록 보기'}</button>\n",
  "        <button class=\"btn\" onClick=${() => startRetry(ctx, m, true)}>그래도 다시 입력</button></div>`;\n",
  "  }\n",
  "\n",
  "  function sendTitle(d, roomName) {\n",
  "    const pv = d.preview || {};\n",
  "    if (d.mode === 'reminder') return '학생 ' + (pv.groups || []).length + '명에게 미제출 서류 독려 개인톡을 보낼까요?';\n",
  "    const pN = (pv.personal || []).length, cN = pv.class && pv.class.lines.length ? 1 : 0;\n",
  "    const room = roomName ? roomName + ' 단톡방에' : '학급 단톡방에';\n",
  "    if (pN && cN) return '단체톡 1건과 개인톡 ' + pN + '명을 보낼까요?';\n",
  "    if (cN) return room + ' 단체톡을 보낼까요?';\n",
  "    return '학생 ' + pN + '명에게 개인톡을 보낼까요?';\n",
  "  }\n",
  "  function resultTitle(d) {\n",
  "    if (d.mode === 'reminder') return '독려 개인톡 보낸 결과';\n",
  "    return d.sendKind === 'personal' ? '개인톡 보낸 결과' : d.sendKind === 'class' ? '단체톡 보낸 결과' : '보낸 결과';\n",
  "  }\n",
  "  const NO_EMAIL = html`<${Chip} tone=\"warn\">이메일 없음 · 실패로 기록돼요</${Chip}>`;\n",
  "\n",
  "  function SendDialog(p) {\n",
  "    const ctx = p.ctx, d = p.d, pv = d.preview || {};\n",
  "    const roomName = String((ctx.D.raw.classInfo || {}).classRoomName || '');\n",
  "    const [state, setState] = useState({ running: false, notice: d.notice || null });\n",
  "    const blocked = d.mode === 'queue' && !!pv.problem;\n",
  "    const send = () => {\n",
  "      setState({ running: true, notice: null });\n",
  "      ctx.set({ dialogBusy: true, sending: true });\n",
  "      const call = d.mode === 'reminder'\n",
  "        ? writeCall('tmDashboardSendRowReminders', { rows: d.rows, previewToken: pv.previewToken })\n",
  "        : writeCall('tmDashboardSendQueue', { kind: d.sendKind, previewToken: pv.previewToken });\n",
  "      call.then(res => {\n",
  "        ctx.set({ dialogBusy: false, sending: false });\n",
  "        const s = res.status;\n",
  "        if (s === 'applied' || s === 'uncertain' || s === 'unknown') {\n",
  "          ctx.set({ remind: d.mode === 'reminder' ? {} : ctx.S.remind,\n",
  "            dialog: { kind: 'send-result', mode: d.mode, sendKind: d.sendKind, status: s, blocked: !!res.blocked,\n",
  "              message: s === 'applied' ? (res.message || '') : SEND_UNCERTAIN, results: res.results || [] } });\n",
  "          ctx.refresh();\n",
  "          return;\n",
  "        }\n",
  "        if (s === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null, dialog: null }); return; }\n",
  "        if (s === 'decide-stale' && res.preview) { ctx.set({ dialog: { kind: 'stale', items: res.preview.staleClaims || [] } }); ctx.refresh(); return; }\n",
  "        if (s === 'changed' && res.preview) {\n",
  "          // 보낼 메시지가 바뀌었으면 새 내용을 보이고 다시 묻는다(§5.4 4단계).\n",
  "          ctx.set({ dialog: Object.assign({}, d, { preview: res.preview, notice: { tone: 'warn', text: res.message || '보낼 메시지가 바뀌었어요.' } }) });\n",
  "          setState({ running: false, notice: { tone: 'warn', text: res.message || '보낼 메시지가 바뀌었어요.' } });\n",
  "          ctx.refresh();\n",
  "          return;\n",
  "        }\n",
  "        setState({ running: false, notice: { tone: res.tone || 'warn', text: s === 'busy' ? BUSY_FINAL : (res.message || '보내지 못했어요. [↻ 새로 읽기]를 누른 뒤 다시 해 주세요.') } });\n",
  "      });\n",
  "    };\n",
  "    const notice = state.notice || d.notice;\n",
  "    const pRows = d.mode === 'reminder' ? (pv.groups || []) : (pv.personal || []);\n",
  "    return html`\n",
  "      <h4>${sendTitle(d, roomName)}</h4>\n",
  "      ${notice ? html`<div class=\"mt\"><${Notice} tone=${notice.tone}>${notice.text}</${Notice}></div>` : null}\n",
  "      <div class=\"ov-list\">\n",
  "        ${d.mode === 'queue' && pv.class && pv.class.lines.length ? html`<div><b>${(pv.class.target || roomName || '학급') + ' 단톡방'}</b>\n",
  "          ${pv.class.lines.map(l => html`<div class=\"q-line\"><span class=\"tx\">${l.text}</span></div>`)}</div>` : null}\n",
  "        ${pRows.map(g => html`<div><div class=\"row\"><b>${g.target}</b>${g.date ? html`<span class=\"q-meta\">${mdw(g.date)}</span>` : null}<span class=\"spacer\"></span>${g.hasEmail ? null : NO_EMAIL}</div>\n",
  "          ${d.mode === 'reminder' ? html`<div class=\"q-line\"><span class=\"tx pre\">${g.text}</span></div>` : g.lines.map(l => html`<div class=\"q-line\"><span class=\"tx\">${l.text}</span></div>`)}</div>`)}\n",
  "      </div>\n",
  "      <${Notice} tone=\"muted\">${SEND_CAUTION}</${Notice}>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" disabled=${state.running} onClick=${p.close}>취소</button>\n",
  "        <button class=\"btn\" disabled=${state.running || blocked} onClick=${send}>${state.running ? '보내는 중…' : '보내기'}</button></div>`;\n",
  "  }\n",
  "\n",
  "  const OUTCOME = {\n",
  "    sent: ['ok', '보냄'], 'record-check': ['warn', '보냄 · 기록 확인'], unknown: ['warn', '결과 확인 필요'],\n",
  "    failed: ['warn', '실패'], stopped: ['warn', '연결 문제로 멈춤'], waiting: ['muted', '대기 그대로'], changed: ['muted', '확인 필요']\n",
  "  };\n",
  "  function outcomeDetail(r) {\n",
  "    if (r.state === 'record-check') return '메시지는 보냈어요. 다시 보내지 말고 발송기록을 확인해 주세요.';\n",
  "    if (r.state === 'stopped') return TM_CONN;\n",
  "    if (r.state === 'failed') return r.detail === '학생 Google 이메일 없음' ? '학생 Google 이메일이 없어요. ' + TM_ROSTER : (r.detail || '');\n",
  "    return '';\n",
  "  }\n",
  "  function SendResultDialog(p) {\n",
  "    const d = p.d;\n",
  "    const tone = d.status === 'applied' && !d.blocked ? 'muted' : 'warn';\n",
  "    return html`\n",
  "      <h4>${resultTitle(d)}</h4>\n",
  "      ${d.message ? html`<div class=\"mt\"><${Notice} tone=${tone}>${d.message}</${Notice}></div>` : null}\n",
  "      <div class=\"ov-list\">\n",
  "        ${(d.results || []).length ? d.results.map(r => {\n",
  "          const pair = OUTCOME[r.state] || OUTCOME.changed;\n",
  "          const detail = outcomeDetail(r);\n",
  "          return html`<div><div class=\"row\"><${Chip} tone=${pair[0]}>${pair[1]}</${Chip}><b>${r.kind === 'class' ? r.target + ' 단톡방' : r.target}</b>\n",
  "            ${r.date ? html`<span class=\"q-meta\">${mdw(r.date)}</span>` : null}${r.lines ? html`<span class=\"q-meta\">${r.lines + '줄'}</span>` : null}</div>\n",
  "            ${detail ? html`<div class=\"q-meta\">${detail}</div>` : null}</div>`;\n",
  "        }) : html`<div class=\"faint small\">${d.status === 'applied' ? '보낸 메시지가 없어요.' : '보낸 결과를 읽지 못했어요.'}</div>`}\n",
  "      </div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn\" onClick=${p.close}>닫기</button></div>`;\n",
  "  }\n",
  "\n",
  "  function StaleDialog(p) {\n",
  "    const ctx = p.ctx;\n",
  "    const [items, setItems] = useState(p.d.items || []);\n",
  "    const [state, setState] = useState({ running: '', notice: null });\n",
  "    const decide = (item, decision) => {\n",
  "      const id = item.kind + ':' + item.row;\n",
  "      setState({ running: id, notice: null });\n",
  "      ctx.set({ dialogBusy: true });\n",
  "      writeCall('tmDashboardResolveStaleClaims', { rows: [{ kind: item.kind, row: item.row, expectedFp: item.fp }], decision }).then(res => {\n",
  "        ctx.set({ dialogBusy: false });\n",
  "        if (res.status === 'applied') {\n",
  "          const rest = items.filter(x => x !== item);\n",
  "          ctx.showToast(res.message || '정리했어요.');\n",
  "          ctx.refresh();\n",
  "          if (!rest.length) { p.close(); return; }\n",
  "          setItems(rest);\n",
  "          setState({ running: '', notice: null });\n",
  "          return;\n",
  "        }\n",
  "        if (res.status === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null, dialog: null }); return; }\n",
  "        if (res.status === 'stale') { ctx.refresh(); }\n",
  "        setState({ running: '', notice: res.status === 'busy' ? BUSY_FINAL : res.status === 'unknown' ? UNKNOWN_WRITE : (res.message || '저장하지 못했어요.') });\n",
  "      });\n",
  "    };\n",
  "    return html`\n",
  "      <h4>결과를 확인하지 못한 메시지</h4>\n",
  "      ${state.notice ? html`<div class=\"mt\"><${Notice} tone=\"warn\">${state.notice}</${Notice}></div>` : null}\n",
  "      <div class=\"ov-list\">\n",
  "        ${items.map(item => {\n",
  "          const personal = item.kind === 'personal';\n",
  "          const busy = !!state.running;\n",
  "          const log = item.log;\n",
  "          return html`<div><div class=\"row\"><b>${item.target + (personal ? ' 개인톡' : ' 단톡방 단체톡')}</b><span class=\"q-meta\">${mdhm(item.at) + ' 보내기 시작'}</span></div>\n",
  "            <div class=\"q-line\"><span class=\"tx\">${item.text}</span></div>\n",
  "            <div class=\"row small\"><span>${log ? '발송기록: ' + mdhm(log.at) + ' ' : '발송기록 없음'}</span>${log ? html`<${LogChip} result=${log.result} />` : null}</div>\n",
  "            <div class=\"ov-foot\">\n",
  "              <button class=\"btn-quiet btn-sm\" disabled=${busy} onClick=${() => decide(item, 'exclude')}>${personal ? '받았어요' : '단톡방에 올라왔어요'}</button>\n",
  "              <button class=\"btn-quiet btn-sm\" disabled=${busy} onClick=${() => decide(item, 'requeue')}>${personal ? '받지 못했어요 · 다시 보낼 목록에 넣기' : '올라오지 않았어요 · 다시 보낼 목록에 넣기'}</button></div></div>`;\n",
  "        })}\n",
  "      </div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" disabled=${!!state.running} onClick=${p.close}>나중에</button></div>`;\n",
  "  }\n",
  "\n",
  "  function queueWriteResult(ctx, res, setNotice, close) {\n",
  "    if (res.status === 'applied') { ctx.showToast(res.message || '저장했어요.'); ctx.refresh(); close(); return; }\n",
  "    if (res.status === 'account-mismatch') { ctx.set({ fatal: ACCOUNTS_MSG, snap: null, dialog: null }); return; }\n",
  "    if (res.status === 'stale') { ctx.refresh(); setNotice(res.message || STALE_MSG); return; }\n",
  "    if (res.status === 'unknown') { ctx.refresh(); setNotice('저장 결과를 확인하지 못했어요. [↻ 새로 읽기]로 보낼 목록을 확인해 주세요.'); return; }\n",
  "    setNotice(res.status === 'busy' ? BUSY_FINAL : (res.message || '저장하지 못했어요.'));\n",
  "  }\n",
  "  function QueueEditDialog(p) {\n",
  "    const ctx = p.ctx, q = p.q;\n",
  "    const roomName = String((ctx.D.raw.classInfo || {}).classRoomName || '학급');\n",
  "    const [text, setText] = useState(q.text || '');\n",
  "    const [state, setState] = useState({ running: false, notice: '', done: false });\n",
  "    const save = () => {\n",
  "      if (!text.trim()) { setState({ running: false, notice: '메시지 내용을 입력해 주세요.' }); return; }\n",
  "      setState({ running: true, notice: '' });\n",
  "      ctx.set({ dialogBusy: true });\n",
  "      writeCall('tmDashboardEditQueueText', { kind: q.kind === 'p' ? 'personal' : 'class', row: q.r, expectedFp: q.fp, content: text.trim() }).then(res => {\n",
  "        ctx.set({ dialogBusy: false });\n",
  "        queueWriteResult(ctx, res, notice => setState({ running: false, notice, done: res.status === 'stale' || res.status === 'unknown' }), p.close);\n",
  "      });\n",
  "    };\n",
  "    return html`\n",
  "      <h4>${queueWho(q, roomName) + ' 메시지 수정'}</h4>\n",
  "      ${state.notice ? html`<div class=\"mt\"><${Notice} tone=\"warn\">${state.notice}</${Notice}></div>` : null}\n",
  "      <div class=\"fgrid mt\"><label class=\"fld full\">내용<textarea maxlength=\"1000\" value=${text} disabled=${state.running || state.done} onInput=${e => setText(e.target.value)}></textarea></label></div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" disabled=${state.running} onClick=${p.close}>${state.done ? '닫기' : '취소'}</button>\n",
  "        ${state.done ? null : html`<button class=\"btn\" disabled=${state.running} onClick=${save}>${state.running ? '저장 중…' : '변경 저장'}</button>`}</div>`;\n",
  "  }\n",
  "  function QueueDeleteDialog(p) {\n",
  "    const ctx = p.ctx, q = p.q;\n",
  "    const roomName = String((ctx.D.raw.classInfo || {}).classRoomName || '학급');\n",
  "    const [state, setState] = useState({ running: false, notice: '', done: false });\n",
  "    const remove = () => {\n",
  "      setState({ running: true, notice: '' });\n",
  "      ctx.set({ dialogBusy: true });\n",
  "      writeCall('tmDashboardDeleteQueueRow', { kind: q.kind === 'p' ? 'personal' : 'class', row: q.r, expectedFp: q.fp }).then(res => {\n",
  "        ctx.set({ dialogBusy: false });\n",
  "        queueWriteResult(ctx, res, notice => setState({ running: false, notice, done: true }), p.close);\n",
  "      });\n",
  "    };\n",
  "    return html`\n",
  "      <h4>${queueWho(q, roomName) + ' 메시지를 삭제할까요?'}</h4>\n",
  "      ${state.notice ? html`<div class=\"mt\"><${Notice} tone=\"warn\">${state.notice}</${Notice}></div>` : null}\n",
  "      <div class=\"ov-list\"><div>${q.text}</div></div>\n",
  "      <div class=\"ov-foot\"><button class=\"btn-quiet\" disabled=${state.running} onClick=${p.close}>${state.done ? '닫기' : '취소'}</button>\n",
  "        ${state.done ? null : html`<button class=\"btn\" disabled=${state.running} onClick=${remove}>${state.running ? '삭제 중…' : '삭제'}</button>`}</div>`;\n",
  "  }\n",
  "\n",
  "  /* ---------- 시작 ---------- */\n",
  "  const root = document.getElementById('app');\n",
  "  try {\n",
  "    root.textContent = '';\n",
  "    render(html`<${App} />`, root);\n",
  "  } catch (err) {\n",
  "    root.textContent = '대시보드를 열지 못했어요. 창을 닫고 시트 메뉴에서 다시 열어 주세요.';\n",
  "  }\n",
  "})();\n"
].join('');
// ===== END TM DASHBOARD PAGE =====


/** Student DM attendance: preserves B1 and existing rows; never sends Chat replies. */
const CHAT_ATTENDANCE_CONFIG_KEY='CHAT_ATTENDANCE_CONFIG_V1';
const CHAT_ATTENDANCE_MESSAGE_PREFIX='CHAT_ATTENDANCE_MESSAGE_V1_';
function addChatAttendanceMenu_(){SpreadsheetApp.getUi().createMenu('학생 Chat 출결').addItem('자동입력 켜기 (새 쪽지부터)','enableChatAttendance').addItem('자동입력 끄기','disableChatAttendance').addItem('처리 상태 확인','showChatAttendanceStatus').addToUi();}
function chatAttendanceScopeKey_(s){return JSON.stringify([s.protocolVersion,s.spreadsheetId,s.workbookSchoolYear,s.generation,s.monthlySheetIds]);}
function chatAttendanceHash_(s){return Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,String(s),Utilities.Charset.UTF_8)).replace(/=+$/,'');}
function chatAttendanceGet_(path){
 const r=UrlFetchApp.fetch('https://chat.googleapis.com/v1/'+path,{headers:{Authorization:'Bearer '+ScriptApp.getOAuthToken()},muteHttpExceptions:true});
 if(r.getResponseCode()===404)return null;
if(r.getResponseCode()!==200){
 try{
  const http=r.getResponseCode();
  let reason='UNCLASSIFIED';
  try{
   const parsed=JSON.parse(r.getContentText());
   const allowed=['SERVICE_DISABLED','ACCESS_TOKEN_SCOPE_INSUFFICIENT'];
   const details=parsed&&parsed.error&&parsed.error.details;
   const info=Array.isArray(details)?details.find(v=>v&&v['@type']==='type.googleapis.com/google.rpc.ErrorInfo'&&allowed.includes(v.reason)):null;
   if(info)reason=info.reason;
  }catch(_){}
  if(Number.isInteger(http))console.log('CHAT_READ_DIAGNOSTIC HTTP='+http+' reason='+reason);
 }catch(_){}
 throw new Error('CHAT_READ_FAILED');
}
 return JSON.parse(r.getContentText());
}
function chatAttendanceConfig_(){return JSON.parse(PropertiesService.getScriptProperties().getProperty(CHAT_ATTENDANCE_CONFIG_KEY)||'null');}
function chatAttendanceAssertCurrent_(source,c,date){
 const n=chatAttendanceConfig_();
 if(!n||!n.enabled||n.generation!==c.generation||n.cutoff!==c.cutoff||n.teacher!==c.teacher||n.spreadsheetId!==c.spreadsheetId||n.binding!==c.binding||n.rosterSheetName!==c.rosterSheetName||source.getId()!==c.spreadsheetId)throw new Error('CONTEXT_CHANGED');
 if(requireGoeduTeacherAccount_({requireEffectiveUser:true})!==c.teacher)throw new Error('ACCOUNT_CHANGED');
 authorizeAttendanceOperation_('automatic',date);
 if(chatAttendanceScopeKey_(attendanceManagedScope_('automatic',date,''))!==c.binding)throw new Error('BINDING_CHANGED');
}
function enableChatAttendance(){
 const lock=LockService.getDocumentLock();
 if(!lock||!lock.tryLock(1000))throw new Error('다른 출결 작업이 끝난 뒤 다시 눌러 주세요.');
 try{
  const source=SpreadsheetApp.getActiveSpreadsheet(),teacher=requireGoeduTeacherAccount_({requireEffectiveUser:true});
  const date=Utilities.formatDate(new Date(),'Asia/Seoul','yyyy-MM-dd');
  authorizeAttendanceOperation_('automatic',date);
  const ports=attendanceAiDefaultPorts_(source),settings=readAttendanceConfigStrict_(source);
  if(String(ports.getTargetSpreadsheetId())!==source.getId()||!ports.getGeminiApiKey())throw new Error('현재 출결 연결과 AI 설정을 확인해 주세요.');
  const rosterSheetName=String(settings.ROSTER_SHEET_NAME||'학생명단').trim();
  const roster=ports.readRosterRows(source,{rosterSheetName:rosterSheetName});
  chatAttendanceValidateRoster_(roster);
  const emails=roster.filter(r=>String(r[0]||'').trim()||String(r[1]||'').trim()).map(r=>String(r[2]||'').trim());
  if(!emails.length||new Set(emails).size!==emails.length||emails.some(e=>!isExactGoeduEmail_(e)))throw new Error('학생 Google 이메일을 중복 없이 확인해 주세요.');
  let checked=false;
  for(const email of emails){
   const dm=chatAttendanceGet_('spaces:findDirectMessage?name='+encodeURIComponent('users/'+email));
   if(!dm)continue;
   if(dm.spaceType!=='DIRECT_MESSAGE')throw new Error('CHAT_DM_INVALID');
   const student=chatAttendanceGet_(dm.name+'/members/'+encodeURIComponent(email));
   const own=chatAttendanceGet_(dm.name+'/members/'+encodeURIComponent(teacher));
   if(!student||!own||!student.member||!own.member||!/^users\/[^/]+$/.test(String(student.member.name||''))||!/^users\/[^/]+$/.test(String(own.member.name||''))||student.member.name===own.member.name)throw new Error('CHAT_MEMBER_INVALID');
   const messages=chatAttendanceGet_(dm.name+'/messages?pageSize=1');if(!messages)throw new Error('CHAT_MESSAGES_UNAVAILABLE');checked=true;break;
  }
  if(!checked)throw new Error('학생과의 기존 Google Chat 개인 대화가 있어야 켤 수 있어요.');
  const p=PropertiesService.getScriptProperties();
  let macKey=p.getProperty('CHAT_ATTENDANCE_RECEIPT_KEY_V1');
  if(!macKey){macKey=Utilities.getUuid();p.setProperty('CHAT_ATTENDANCE_RECEIPT_KEY_V1',macKey);}
  const c={enabled:true,generation:Utilities.getUuid(),teacher:teacher,spreadsheetId:source.getId(),rosterSheetName:rosterSheetName,binding:chatAttendanceScopeKey_(attendanceManagedScope_('automatic',date,'')),cutoff:new Date().toISOString(),macKey:macKey};
  ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()==='pollChatAttendance').forEach(t=>ScriptApp.deleteTrigger(t));
  p.setProperty(CHAT_ATTENDANCE_CONFIG_KEY,JSON.stringify(c));
  try{ScriptApp.newTrigger('pollChatAttendance').timeBased().everyMinutes(10).create();}
  catch(_){c.enabled=false;p.setProperty(CHAT_ATTENDANCE_CONFIG_KEY,JSON.stringify(c));throw new Error('자동 확인을 예약하지 못했어요. 다시 켜 주세요.');}
  source.toast('지금부터 오는 학생 개인 쪽지만 확인해요. 기존 쪽지는 입력하지 않아요.','학생 Chat 출결',10);
 }finally{lock.releaseLock();}
}
function disableChatAttendance(){
 const lock=LockService.getDocumentLock();if(!lock||!lock.tryLock(1000))throw new Error('출결 처리 중이에요. 잠시 뒤 다시 눌러 주세요.');
 try{
  const c=chatAttendanceConfig_();
  if(c&&requireGoeduTeacherAccount_({requireEffectiveUser:true})!==c.teacher)throw new Error('ACCOUNT_CHANGED');
  if(c){c.enabled=false;PropertiesService.getScriptProperties().setProperty(CHAT_ATTENDANCE_CONFIG_KEY,JSON.stringify(c));}
  ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()==='pollChatAttendance').forEach(t=>ScriptApp.deleteTrigger(t));
  SpreadsheetApp.getActiveSpreadsheet().toast('학생 쪽지 자동입력을 껐어요.','학생 Chat 출결',7);
 }finally{lock.releaseLock();}
}
function showChatAttendanceStatus(){
 const lock=LockService.getDocumentLock();
 if(!lock||!lock.tryLock(1000)){SpreadsheetApp.getUi().alert('학생 쪽지 출결을 처리 중이에요. 잠시 뒤 다시 확인해 주세요.');return;}
 try{
  const c=chatAttendanceConfig_(),teacher=requireGoeduTeacherAccount_({requireEffectiveUser:true});
  if(c&&c.teacher!==teacher)throw new Error('ACCOUNT_CHANGED');
  const props=PropertiesService.getScriptProperties(),p=props.getProperties(),counts={applied:0,review:0,uncertain:0},details=[];
  const sheets=SpreadsheetApp.getActiveSpreadsheet().getSheets();
  Object.keys(p).filter(k=>k.indexOf(CHAT_ATTENDANCE_MESSAGE_PREFIX)===0).forEach(k=>{
   const r=JSON.parse(p[k]);
   if(!c||r.teacher!==c.teacher||r.spreadsheetId!==c.spreadsheetId)return;
   if(r.state==='writing'){r.state='uncertain';r.code='INTERRUPTED_WRITE';props.setProperty(k,JSON.stringify(r));}
   if(Object.prototype.hasOwnProperty.call(counts,r.state))counts[r.state]++;
   if(r.state==='review'||r.state==='uncertain'){
    const sheet=sheets.find(s=>s.getSheetId()===r.sheetId);
    const location=r.row?' / '+(sheet?sheet.getName():'출석부')+' '+r.row+'행':'';
    const reason=r.state==='uncertain'?'입력 결과 확인 필요':r.code==='CORRECTION_REQUIRES_REVIEW'?'학생 정정 쪽지 확인 필요':r.code==='AMBIGUOUS_EDITED_OR_CORRECTED'?'날짜·수정·취소 확인 필요':'기존 출결·분류 확인 필요';
    details.push((r.student?r.student+'번':'학생 확인 필요')+' '+(r.date||'')+location+' / '+reason);
   }
  });
  SpreadsheetApp.getUi().alert('학생 Chat 출결',(c&&c.enabled?'자동입력 켜짐':'자동입력 꺼짐')+'\n입력 확인: '+counts.applied+'\n선생님 확인 필요: '+counts.review+'\n결과 불확실 (자동 재입력 안 함): '+counts.uncertain+'\n최근 확인: '+(p.CHAT_ATTENDANCE_LAST_POLL_V1||'아직 확인 전')+'\n조회 실패: '+(p.CHAT_ATTENDANCE_LAST_ERROR_V1?'연결을 확인해 주세요.':'없음')+(details.length?'\n\n'+details.slice(-20).join('\n'):''),SpreadsheetApp.getUi().ButtonSet.OK);
 }finally{lock.releaseLock();}
}
function chatAttendanceMessageVersion_(m){return chatAttendanceHash_(JSON.stringify([m.name,m.sender&&m.sender.name,m.createTime,m.lastUpdateTime||'',m.text||'']));}
function chatAttendanceDay_(m){const d=new Date(m.createTime);if(!Number.isFinite(d.getTime()))throw new Error('MESSAGE_DATE_INVALID');return Utilities.formatDate(d,'Asia/Seoul','yyyy-MM-dd');}
function chatAttendanceMessages_(space,cutoff){
 const result=[];let token='',pages=0;
 const since=new Date(new Date(cutoff).getTime()-1000).toISOString();
 do{
  const path=space+'/messages?pageSize=100&orderBy='+encodeURIComponent('createTime ASC')+'&filter='+encodeURIComponent('createTime > "'+since+'"')+(token?'&pageToken='+encodeURIComponent(token):'');
  const page=chatAttendanceGet_(path);if(!page)throw new Error('CHAT_SPACE_UNAVAILABLE');
  result.push.apply(result,page.messages||[]);token=page.nextPageToken||'';
  if(++pages>=5&&token)throw new Error('CHAT_PAGE_LIMIT');
 }while(token);
 return result;
}
function chatAttendanceCandidate_(s){return /(?:결석|못\s*(?:가|갈)|학교\s*못)/.test(s)&&/(?:아파|아픈|통증|생리|병원|몸살|감기|열이)/.test(s);}
function chatAttendanceAmbiguous_(s){return /(?:취소|정정|수정|아니|괜찮|갈게|등교할|회복|어제|내일|모레|다음|지난|[월화수목금토일]요일|\d{1,2}[/.]\d{1,2}|["'“”‘’>]|\d{1,2}\s*[월일])/.test(s);}
function chatAttendanceSetState_(m,state,code,student,c){PropertiesService.getScriptProperties().setProperty(CHAT_ATTENDANCE_MESSAGE_PREFIX+chatAttendanceHash_(m.name),JSON.stringify({state:state,code:code,student:student||null,date:chatAttendanceDay_(m),teacher:c.teacher,spreadsheetId:c.spreadsheetId}));}
function chatAttendanceCorrection_(s){return /(?:이제.{0,12}괜찮|(?:학교|등교).{0,12}(?:갈게|가겠|할게)|(?:결석|못.{0,3}가).{0,12}(?:취소|아니)|^\s*취소(?:할게요|해요|합니다)?\s*[.!?]?\s*$)/.test(s);}
function chatAttendanceProxy_(text,row,roster){return /(?:못\s*간대|못\s*갈\s*거래|전해\s*달|전해\s*줘|대신\s*(?:연락|말|전)|친구|걔|그\s*아이)/.test(text)||roster.some(r=>{const name=String(r[1]||'').trim();if(String(r[0])===String(row[0])||!name)return false;if(text.indexOf(name)!==-1)return true;if(/^[가-힣]{3,4}$/.test(name)){const given=name.slice(1);return [given+'가',given+'이가',given+'는',given+'이는'].some(s=>text.indexOf(s)!==-1);}return false;});}
function chatAttendanceDayMessages_(space,date,sender){const start=date+'T00:00:00+09:00';return chatAttendanceMessages_(space,start).filter(m=>m.sender&&m.sender.name===sender&&chatAttendanceDay_(m)===date);}
function chatAttendanceReceiptCell_(v,zone){
 if(v instanceof Date){const p=Utilities.formatDate(v,zone,'yyyy-MM-dd').split('-').map(Number);return{numberValue:Date.UTC(p[0],p[1]-1,p[2])/86400000+25569};}
 if(v===''||v===null)return{};
 if(typeof v==='number')return{numberValue:v};
 if(typeof v==='string')return{stringValue:v};
 throw new Error('RECEIPT_TYPE_INVALID');
}
function chatAttendanceCellDigest_(v,key){return Utilities.base64EncodeWebSafe(Utilities.computeHmacSha256Signature(JSON.stringify(v),key));}
function chatAttendanceSameDate_(v,key,zone){
 if(v instanceof Date)return Utilities.formatDate(v,zone,'yyyy-MM-dd')===key;
 if(typeof v==='number')return Utilities.formatDate(new Date((v-25569)*86400000),'UTC','yyyy-MM-dd')===key;
 const text=String(v||'').trim();if(/^\d{4}-\d{2}-\d{2}$/.test(text))return text===key;
 return true;
}
function chatAttendanceWrite_(source,c,student,m,ports,role,record){
 const p=PropertiesService.getScriptProperties(),key=CHAT_ATTENDANCE_MESSAGE_PREFIX+chatAttendanceHash_(m.name),old=p.getProperty(key);
 if(old){const saved=JSON.parse(old);if(saved.state==='writing'){saved.state='uncertain';saved.code='INTERRUPTED_WRITE';p.setProperty(key,JSON.stringify(saved));}return;}
 chatAttendanceAssertCurrent_(source,c,record.date);
 const settings=readAttendanceConfigStrict_(source);
 if(String(ports.getTargetSpreadsheetId())!==source.getId()||String(settings.ROSTER_SHEET_NAME||'학생명단').trim()!==c.rosterSheetName)throw new Error('TARGET_OR_ROSTER_CHANGED');
 const currentRole=attendanceMonthSheetsFor_(source,settings).find(i=>Number(i.month)===Number(record.date.substring(5,7)));
 if(!currentRole||role.sheet.getParent().getId()!==source.getId()||currentRole.sheet.getSheetId()!==role.sheet.getSheetId())throw new Error('MONTH_TARGET_CHANGED');
 const matched=ports.readRosterRows(source,{rosterSheetName:c.rosterSheetName}).filter(r=>String(r[2]||'').trim()===student.email);
 if(matched.length!==1||JSON.stringify(matched[0])!==JSON.stringify(student.row))throw new Error('ROSTER_CHANGED');
 const zone=source.getSpreadsheetTimeZone(),existing=ports.readExistingAttendanceRows(role.sheet);
 if(existing.dataRows.some(r=>String(r[1])===String(record.rosterCombined)&&chatAttendanceSameDate_(r[0],record.date,zone)))throw new Error('EXISTING_RECORD');
 const state=ports.readWriteState(role.sheet);
 if(JSON.stringify(state.headerRow)!==JSON.stringify(INPUT_HEADERS.concat(MONTHLY_CHAT_RESULT_HEADERS)))throw new Error('HEADER_CHANGED');
 const batch=buildAttendanceAiBatchUpdate_([record],state);
 const append=batch&&batch.requests&&batch.requests.length===1&&batch.requests[0].appendCells;
 if(!append||append.sheetId!==role.sheet.getSheetId()||!Array.isArray(append.rows)||append.rows.length!==1||append.fields!=='userEnteredValue,userEnteredFormat.backgroundColor')throw new Error('BATCH_INVALID');
 const expected=append.rows[0].values.map(cell=>cell.userEnteredValue||{});
 if(expected.some(v=>Object.keys(v).some(k=>k!=='numberValue'&&k!=='stringValue')))throw new Error('BATCH_TYPE_INVALID');
 const fresh=chatAttendanceGet_(m.name);
 if(!fresh||chatAttendanceMessageVersion_(fresh)!==chatAttendanceMessageVersion_(m))throw new Error('MESSAGE_CHANGED');
 chatAttendanceAssertCurrent_(source,c,record.date);
 const row=Math.max(MONTHLY_ATTENDANCE_HEADER_ROW,state.lastDataRow)+1;
 const receipt={state:'writing',code:'WRITE_STARTED',generation:c.generation,teacher:c.teacher,spreadsheetId:c.spreadsheetId,sheetId:state.sheetId,row:row,student:Number(student.row[0]),date:record.date,digest:chatAttendanceCellDigest_(expected,c.macKey)};
 p.setProperty(key,JSON.stringify(receipt));
 try{
  const response=ports.batchUpdate(source.getId(),batch);
  if(!response||response.spreadsheetId!==source.getId())throw new Error('WRITE_RESPONSE_UNCERTAIN');
  SpreadsheetApp.flush();
  const range=role.sheet.getRange(row,1,1,expected.length);
  if(range.getFormulas()[0].some(Boolean))throw new Error('WRITE_RECEIPT_UNCERTAIN');
  const actual=range.getValues()[0].map(v=>chatAttendanceReceiptCell_(v,zone));
  if(chatAttendanceCellDigest_(actual,c.macKey)!==receipt.digest)throw new Error('WRITE_RECEIPT_UNCERTAIN');
  const count=ports.readExistingAttendanceRows(role.sheet).dataRows.filter(r=>String(r[1])===String(record.rosterCombined)&&chatAttendanceSameDate_(r[0],record.date,zone)).length;
  if(count!==1)throw new Error('WRITE_CONFLICT_UNCERTAIN');
  receipt.state='applied';receipt.code='RECEIPT_CONFIRMED';
 }catch(error){receipt.state='uncertain';receipt.code=/^WRITE_/.test(String(error.message))?error.message:'WRITE_REQUEST_UNCERTAIN';}
 p.setProperty(key,JSON.stringify(receipt));
}
function pollChatAttendance(){
 const c=chatAttendanceConfig_();if(!c||!c.enabled)return;
 if(String(Session.getEffectiveUser().getEmail()||'').trim()!==c.teacher)return;
 const lock=LockService.getDocumentLock();if(!lock||!lock.tryLock(1000))return;
 const p=PropertiesService.getScriptProperties();
 try{
  const source=SpreadsheetApp.getActiveSpreadsheet(),now=Utilities.formatDate(new Date(),'Asia/Seoul','yyyy-MM-dd');
  chatAttendanceAssertCurrent_(source,c,now);
  const ports=attendanceAiDefaultPorts_(source),settings=readAttendanceConfigStrict_(source),roles=attendanceMonthSheetsFor_(source,settings);
  if(String(ports.getTargetSpreadsheetId())!==source.getId())throw new Error('TARGET_CHANGED');
  const roster=ports.readRosterRows(source,{rosterSheetName:c.rosterSheetName}),emails=roster.filter(r=>String(r[0]||'').trim()||String(r[1]||'').trim()).map(r=>String(r[2]||'').trim());
  chatAttendanceValidateRoster_(roster);
  if(new Set(emails).size!==emails.length||emails.some(e=>!isExactGoeduEmail_(e)))throw new Error('ROSTER_EMAIL_MISSING_OR_DUPLICATE');
  for(const row of roster){
   const email=String(row[2]||'').trim();if(!email)continue;if(!isExactGoeduEmail_(email))throw new Error('ROSTER_EMAIL_INVALID');
   const dm=chatAttendanceGet_('spaces:findDirectMessage?name='+encodeURIComponent('users/'+email));if(!dm)continue;
   if(dm.spaceType!=='DIRECT_MESSAGE')throw new Error('CHAT_DM_INVALID');
   const member=chatAttendanceGet_(dm.name+'/members/'+encodeURIComponent(email)),own=chatAttendanceGet_(dm.name+'/members/'+encodeURIComponent(c.teacher));
   if(!member||!own||!member.member||!own.member||!/^users\/[^/]+$/.test(String(member.member.name||''))||!/^users\/[^/]+$/.test(String(own.member.name||''))||member.member.name===own.member.name)throw new Error('CHAT_MEMBER_INVALID');
   const cursorKey='CHAT_ATTENDANCE_CURSOR_V1_'+chatAttendanceHash_(c.generation+email),cursor=p.getProperty(cursorKey)||c.cutoff;
   const messages=chatAttendanceMessages_(dm.name,cursor).filter(m=>m.sender&&m.sender.name===member.member.name&&new Date(m.createTime).getTime()>=new Date(c.cutoff).getTime());
   for(const m of messages){
    const key=CHAT_ATTENDANCE_MESSAGE_PREFIX+chatAttendanceHash_(m.name);if(p.getProperty(key))continue;
    const text=String(m.text||'').trim(),date=chatAttendanceDay_(m);
    const candidate=chatAttendanceCandidate_(text),correction=chatAttendanceCorrection_(text);
    if(!candidate&&!correction)continue;
    const sameDay=chatAttendanceDayMessages_(dm.name,date,member.member.name);
    if(correction&&!candidate){if(sameDay.some(x=>chatAttendanceCandidate_(String(x.text||''))))chatAttendanceSetState_(m,'review','CORRECTION_REQUIRES_REVIEW',Number(row[0]),c);continue;}
    if(chatAttendanceProxy_(text,row,roster)||(m.lastUpdateTime&&new Date(m.lastUpdateTime).getTime()>new Date(m.createTime).getTime())||chatAttendanceAmbiguous_(text)||sameDay.some(x=>chatAttendanceCorrection_(String(x.text||'')))||sameDay.filter(x=>chatAttendanceCandidate_(String(x.text||''))).length!==1){chatAttendanceSetState_(m,'review','AMBIGUOUS_EDITED_OR_CORRECTED',Number(row[0]),c);continue;}
    try{
     chatAttendanceAssertCurrent_(source,c,date);
     const role=roles.find(i=>Number(i.month)===Number(date.substring(5,7)));if(!role)throw new Error('MONTH_TARGET_MISSING');
     const context={schoolYear:String(settings.SCHOOL_YEAR||'').trim(),month:role.month,today:date,sentence:String(row[0])+'번 '+String(row[1])+' '+text};
     const holidays=ports.readHolidayDateKeys(source,Number(date.substring(0,4)));if(!holidays)throw new Error('HOLIDAY_CONTEXT_MISSING');
     const request=buildAttendanceAiGeminiRequest_(context.sentence,context,[row]);
     const payload=extractAttendanceAiGeminiPayload_(attendanceAiDefaultPorts_(source).callGemini(request,ports.getGeminiApiKey()));
     const records=validateAttendanceAiRecords_(payload,[row],context,holidays);
     if(!Array.isArray(records)||records.length!==1||records[0].date!==date||records[0].kind!=='결석함')throw new Error('AI_RESULT_REQUIRES_REVIEW');
     chatAttendanceWrite_(source,c,{email:email,row:row},m,ports,role,records[0]);
    }catch(_){if(!p.getProperty(key))chatAttendanceSetState_(m,'review','NOT_WRITTEN_REQUIRES_REVIEW',Number(row[0]),c);}
   }
   p.setProperty(cursorKey,messages.reduce((max,m)=>m.createTime>max?m.createTime:max,cursor));
  }
  p.setProperty('CHAT_ATTENDANCE_LAST_POLL_V1',Utilities.formatDate(new Date(),'Asia/Seoul','yyyy-MM-dd HH:mm:ss'));p.deleteProperty('CHAT_ATTENDANCE_LAST_ERROR_V1');
 }catch(_){p.setProperty('CHAT_ATTENDANCE_LAST_ERROR_V1','CHAT_CHECK_FAILED');}
 finally{lock.releaseLock();}
}

function chatAttendanceValidateRoster_(roster){const active=roster.filter(r=>String(r[0]||'').trim()||String(r[1]||'').trim());const numbers=active.map(r=>String(r[0]||'').trim());if(!active.length||numbers.some(n=>!/^\d+$/.test(n)||Number(n)<1)||new Set(numbers.map(Number)).size!==numbers.length||active.some(r=>!String(r[1]||'').trim()))throw new Error('ROSTER_INVALID_OR_DUPLICATE_NUMBER');}

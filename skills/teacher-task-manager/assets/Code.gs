/**
 * 출결 신고서 자동화 · 기존 Google Docs 템플릿 유지
 * 버전: 5.11.0
 *   (아래 APP_VERSION과 항상 같아야 한다. 버전을 올릴 때 두 곳을 함께 고친다 — 테스트가 대조 검사함)
 * for Google Sheets + Google Docs + Google Tasks
 *
 * 핵심 원칙:
 * - 월별 입력표는 A:L이 실제 표다. M~P는 값도 제목도 없고 색도 칠하지 않는다.
 * - 신고서 양식은 이미 만들어 둔 Google Docs 템플릿을 그대로 복사해서 사용
 * - 새 템플릿 문서 생성 기능은 넣지 않음
 * - 학생명단은 A열 번호, B열 이름을 적으면 C열 '번호+이름'이 자동 생성되고, 월별 시트 B열 드롭다운으로 연결
 */

const APP_NAME = '출결 신고서 자동화';
const APP_VERSION = '5.11.0';
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
// 1행 A열은 무엇을 하는 자리인지 알려 주는 이름표, B~K열은 한 칸으로 합친 입력칸이다.
const MONTHLY_ATTENDANCE_AI_INPUT_LABEL = 'AI 출결 입력';
const MONTHLY_ATTENDANCE_AI_INPUT_HINT =
  '여기에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르세요';
const MONTHLY_ATTENDANCE_AI_INPUT_HINT_COLOR = '#9AA0A6';
// 선생님이 적는 글은 회색 예시와 눈에 띄게 갈라 보이도록 검정으로 쓴다.
const MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR = '#000000';
const MONTHLY_ATTENDANCE_AI_INPUT_COL = 2;        // B
// 입력칸은 M열까지 이어 붙인다. K에서 끊으면 오른쪽에 짜투리 칸이 남아
// 글 적는 자리처럼 보이지 않는다. 가운데 L열은 숨긴 열이라 화면에는 안 보인다.
const MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL = 13;  // M
// 제목이 실제로 있는 마지막 열이다. M~P에는 아무 자료도 들어가지 않는다.
const MONTHLY_ATTENDANCE_LAST_DATA_COL = 13;      // M (AI 입력 표시까지가 표다)
const STRIPE_END_COL  = 13;      // A(1)~M(13)
const STRIPE_COLOR_WHITE = '#ffffff';
const STRIPE_COLOR_GRAY  = '#bdbdbd'; // 구글 시트 팔레트 '회색' 중앙 톤 근사값
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
const ATTENDANCE_AI_KINDS = Object.freeze(['결석함','지각함','조퇴함','결과함']);
const ATTENDANCE_AI_PERIODS = Object.freeze(
  ['','1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례']
);
const ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY =
  'ATTENDANCE_AI_TARGET_SPREADSHEET_ID';
const ATTENDANCE_AI_GEMINI_API_KEY_PROPERTY = 'ATTENDANCE_AI_GEMINI_API_KEY';
// 컴퓨터의 티처 매니저 연결 화면에 넣은 키가 설정 탭 이 이름으로 들어온다.
// install_attendance_automation.build_config_rows / central_chat._upsert_settings_value와 같은 이름이어야 한다.
const ATTENDANCE_AI_GEMINI_API_KEY_SETTING = 'GEMINI_API_KEY';
// Teacher Manager 정식 출석부의 설정 탭에 적는 값이다. '예'면 이 시트에서
// 1행 AI 입력을 켤 수 있다. 파일 이름은 권한 근거로 사용하지 않는다.
// install_attendance_automation.build_config_rows와 이름·값이 같아야 한다.
const ATTENDANCE_AI_ALLOWED_SETTING = 'ATTENDANCE_AI_ALLOWED';
const ATTENDANCE_AI_ALLOWED_VALUE = '예';
const ATTENDANCE_AI_EDIT_TRIGGER_HANDLER = 'onAttendanceAiEdit';
const ATTENDANCE_AI_MENU_ITEM = 'AI 출결 입력 켜기';

const DEFAULT_CONFIG = Object.freeze({
  SCHOOL_NAME: '',
  SCHOOL_YEAR: String(new Date().getFullYear()),
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
  STUDENT_DROPDOWN_RANGE: 'C2:C200',
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
const ROSTER_HEADERS = ['번호','이름','번호+이름','학생 Google 이메일'];
const MESSENGER_PERSONAL_SHEET_NAME = '메신저 개인톡 내용';
const MESSENGER_CLASS_SHEET_NAME = '메신저 단체톡 내용';
const LEGACY_PERSONAL_MESSAGE_QUEUE_SHEET_NAMES = ['개인톡 내용', '개인 쪽지 대장'];
const LEGACY_CLASS_MESSAGE_QUEUE_SHEET_NAMES = ['단체톡 내용', '단체 쪽지 대장'];
const PERSONAL_MESSAGE_QUEUE_HEADERS = ['보낼 날짜','번호','이름','쪽지 종류','쪽지 내용','들어온 곳','상태','연결 표시','보낸 시각','결과'];
const CLASS_MESSAGE_QUEUE_HEADERS = ['보낼 날짜','안내 종류','안내 내용','들어온 곳','상태','보낸 시각','결과'];
const MONTHLY_CHAT_RESULT_HEADERS = ['Google Chat 발송상태','Google Chat 시도시각','Google Chat 결과','Google Chat 내용기준'];
const MESSAGE_QUEUE_SOURCES = ['출결표','자동분석','직접입력'];
const MESSAGE_QUEUE_STATUSES = ['확인필요','대기','발송중','제외','보냄','실패'];
const PERSONAL_MESSAGE_TYPES = ['출결서류','준비물','개별안내','상담/확인','기타'];
const CLASS_MESSAGE_TYPES = ['준비물','제출물','일정','생활지도','기타'];
const CHAT_MESSAGE_LIMIT_BYTES = 30000;

/*************************************************
 * 메뉴
 *************************************************/
function onOpen() {
  const ui = SpreadsheetApp.getUi();

  // 메뉴는 '사전 세팅'과 '교사가 직접 실행하는 일'로 가른다.
  // Chat 연결은 출결뿐 아니라 교육청 메신저 발송의 사전 세팅이기도 해서 어느 한쪽 실행
  // 메뉴 밑에 둘 수 없다. 그래서 최상위에 따로 세우고, 사전 세팅은 항목 하나로 합친다.
  ui.createMenu('처음 한 번 설정하기')
    .addItem('처음 설정 한 번에 끝내기', 'runFirstTimeSetup')
    .addItem('연결 상태 확인', 'checkCentralChatStatus')
    .addToUi();

  // 아래 두 메뉴는 계열이 다르지만 둘 다 실행 전용이다 — 사전 세팅 항목을 넣지 않는다.
  const attendanceMenu = ui.createMenu('출결 업무 자동화')
    .addItem('선택 행 출결신고서 Google Docs에 만들기', 'createDocFromTemplate')
    .addItem('선택 행 미제출 서류 Google Tasks에 추가하기', 'addSelectedRowToTasks')
    .addItem('선택 행 미제출 서류 Google Chat 개인톡 보내기', 'sendSelectedRowsChatNow');

  attendanceMenu
    .addSeparator()
    .addSubMenu(ui.createMenu('문제가 생겼을 때')
      .addItem('입력 색/드롭다운 다시 적용', 'refreshInputFormattingAndDropdowns')
      .addItem('기존 템플릿 ID/접근 점검', 'checkExistingTemplateDoc')
      .addItem('출력 폴더 만들기/연결', 'connectDestinationFolder')
      .addItem('Tasks 목록 만들기/연결', 'connectTasksList')
      .addSeparator()
      .addItem('날짜 줄무늬: 현재 월 시트', 'reStripeActiveSheet')
      .addItem('날짜 줄무늬: 모든 월 시트', 'reStripeAllSheets'))
    .addItem('ⓘ 만든 사람 / 버전', 'showAbout')
    .addToUi();

  ui.createMenu('교육청 메신저 정리·발송')
    // 탭 열기 항목은 두지 않는다 — 시트 탭을 누르면 되는 일이라 메뉴 중복이다 (사용자 결정 2026-07-21).
    .addItem('메신저 쪽지 내용 Google Chat으로 개인톡 보내기', 'sendMessengerPersonalMessages')
    .addItem('메신저 쪽지 내용 Google Chat으로 단체톡 보내기', 'sendMessengerClassMessages')
    .addItem('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기', 'sendMessengerAllMessages')
    .addSeparator()
    .addItem('Google Chat 발송 기록 보기', 'openChatLogSheet')
    .addItem('Google Chat 발송 연결 끊기', 'disconnectCentralChatSender')
    .addToUi();
}

/*************************************************
 * 처음 한 번 설정하기 — 사전 세팅 네 가지를 한 번에
 *************************************************/

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
  const steps = [
    { title: '기본 시트/설정 점검', run: firstTimeSetupWorkbookStep_ },
    { title: 'AI 출결 입력 켜기', run: firstTimeSetupAiStep_ },
    { title: 'Google Chat 최초 발송 연결', run: firstTimeSetupChatStep_ },
    { title: 'Google Chat 학급 단톡방 고르기', run: firstTimeSetupClassSpaceStep_ }
  ];

  // 뒤 단계가 앞 단계의 결과를 알아야 하는 곳이 있다 — 연결이 끝나야 단톡방 목록을 받는다.
  const context = { chatReady: false };
  const lines = [];
  const leftovers = [];

  steps.forEach(function (step) {
    let result;
    try {
      result = step.run(context);
    } catch (err) {
      // 한 단계가 무너져도 나머지는 이어서 한다.
      result = { ok: false, message: errorMessage_(err) };
    }
    result = result || { ok: false, message: '결과를 확인하지 못했습니다.' };
    const mark = result.ok === true ? (result.skipped === true ? '[이미]' : '[됨]') : '[못 함]';
    lines.push(mark + ' ' + step.title + (result.message ? ' — ' + firstTimeSetupOneLine_(result.message) : ''));
    if (result.ok !== true) leftovers.push(step.title + '\n' + String(result.message || ''));
  });

  // 네 단계가 모두 끝났을 때만 완료 표시를 적는다 — 설치 프로그램이 이 값을 읽어
  // 마법사의 [다음]을 켠다. 일부 실패면 적지 않는다(거짓 완료 방지).
  if (leftovers.length === 0) {
    try {
      let doneAccount = '';
      try { doneAccount = String(Session.getActiveUser().getEmail() || ''); } catch (e) {}
      setConfigValue_('FIRST_TIME_SETUP_DONE', (doneAccount + ' ' + new Date().toISOString()).trim());
    } catch (err) {
      // 네 단계가 끝났어도 표시가 없으면 프로그램의 [다음]이 계속 잠긴다.
      // 다시 눌렀을 때 이미 끝난 네 단계는 건너뛰고 이 표시만 다시 적게 안내한다.
      const markerError = errorMessage_(err);
      lines.push('[못 함] 완료 표시 기록 — ' + markerError);
      leftovers.push('완료 표시 기록\n' + markerError);
    }
  }

  const closing = leftovers.length
    ? '아직 남은 것\n\n' + leftovers.join('\n\n') + '\n\n' +
      '위 안내대로 마친 뒤 [처음 한 번 설정하기 → 처음 설정 한 번에 끝내기]를 다시 누르면 됩니다.\n' +
      '이미 끝난 것은 건너뛰니 여러 번 눌러도 안전합니다.'
    : '네 가지가 모두 준비됐습니다. 이 메뉴는 다시 누르지 않아도 됩니다.';

  ui.alert('처음 한 번 설정하기', lines.join('\n') + '\n\n' + closing, ui.ButtonSet.OK);
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

/** 2단계 — 1행 AI 입력 켜기(편집 감지기 만들기). */
function firstTimeSetupAiStep_() {
  // 켤 수 있는 사본인지 먼저 본다. 아닌 시트에서는 이 단계만 건너뛰고 나머지는 그대로 한다.
  let state = null;
  try {
    state = attendanceAiWorkbookState_();
  } catch (err) {
    state = null;
  }
  if (!state || state.ok !== true) {
    return {
      ok: true,
      skipped: true,
      message: state && state.message ? state.message : 'AI 입력을 켤 수 있는 사본이 아닙니다.'
    };
  }
  const result = enableAttendanceAiInput({ quiet: true }) || { ok: false, message: '결과를 확인하지 못했습니다.' };
  // 이미 있던 감지기를 그대로 쓴 경우는 새로 만든 것과 구분해 보여 준다.
  if (result.ok === true && result.created !== true) result.skipped = true;
  return result;
}

/** 3단계 — Google Chat 최초 발송 연결. 권한 허용 화면은 사람이 눌러야 끝난다. */
function firstTimeSetupChatStep_(context) {
  let status = null;
  try {
    status = callCentralChatSender_('/v1/status', {});
  } catch (err) {
    // 상태를 못 읽었다고 건너뛰면 첫 연결이 영영 시작되지 않는다. 연결부터 시도한다.
    status = null;
  }
  if (status && status.connected) {
    if (context) context.chatReady = true;
    return {
      ok: true,
      skipped: true,
      message: '이미 연결되어 있습니다' + (status.account ? ': ' + status.account : '.')
    };
  }
  return startCentralChatConnection({ quiet: true }) || { ok: false, message: '결과를 확인하지 못했습니다.' };
}

/** 4단계 — 학급 단톡방 고르기. 목록에서 고르는 화면은 사람이 눌러야 끝난다. */
function firstTimeSetupClassSpaceStep_(context) {
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
    '- 학생명단 C열(번호+이름) → 월별 시트 B열 드롭다운을 연결했습니다.\n' +
    '- 신고서 템플릿 문서 ID는 설치 도우미가 설정 시트에 자동으로 입력합니다.'
  );
}

// LLM/설치 도우미가 Apps Script API로 UI 없이 실행하는 진입점.
function apiSetupAttendanceWorkbook() {
  requireGoeduTeacherAccount_();
  setupAttendanceWorkbookCore_();
  return 'ok';
}

function setupAttendanceWorkbookCore_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ensureConfigSheet_(ss);
  recordScriptIdInConfig_();
  ensureCentralChatConfig_();
  const cfg = getConfig_();
  const monthNames = getMonthSheetNames_(cfg);

  // 견본 그대로인지는 첫 탭을 바꾸기 전에 한 번만 판단한다.
  const moveTemplateRows = isPristineTemplateWorkbook_(ss, monthNames);
  monthNames.forEach(name => ensureMonthSheet_(ss, name, moveTemplateRows));
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

  monthNames.forEach(name => {
    const sh = ss.getSheetByName(name);
    if (sh) {
      applyInputSheetFormatting_(sh);
      ensureMonthlyChatResultColumns_(sh);
    }
  });
  applyStudentDropdowns_(ss, cfg);
}

function refreshInputFormattingAndDropdowns() {
  requireGoeduTeacherAccount_();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ensureConfigSheet_(ss);
  ensureRosterSheet_(ss);
  ensureDropdownSheet_(ss);
  ensurePersonalMessageQueueSheet_(ss);
  ensureClassMessageQueueSheet_(ss);
  const cfg = getConfig_();
  getInputSheets_(ss, cfg).forEach(sh => {
    applyInputSheetFormatting_(sh);
    ensureMonthlyChatResultColumns_(sh);
  });
  applyStudentDropdowns_(ss, cfg);
  SpreadsheetApp.getUi().alert('월별 입력 시트의 색/드롭다운을 다시 적용했습니다.');
}

function ensureConfigSheet_(ss) {
  let sh = ss.getSheetByName(CONFIG_SHEET_NAME);
  if (!sh) sh = ss.insertSheet(CONFIG_SHEET_NAME);

  if (!String(sh.getRange(1, 1).getValue() || '').trim()) {
    sh.getRange(1, 1, 1, 4).setValues([['설정키','값','설명','예시/필수']]);
  }

  const existing = readConfigMapFromSheet_(sh);
  const rows = [
    ['SCHOOL_NAME', DEFAULT_CONFIG.SCHOOL_NAME, '기존 Google Docs 템플릿의 {학교명} 자리에 들어갈 학교명입니다.', '예: ○○중학교 / 필수'],
    ['SCHOOL_YEAR', DEFAULT_CONFIG.SCHOOL_YEAR, '학년도 표기용 기본값입니다. 실제 신고서 날짜 연도는 A열 날짜/확인일에서 계산됩니다.', '예: 2026'],
    ['GRADE', DEFAULT_CONFIG.GRADE, '템플릿에 {학년} placeholder가 있을 때만 사용합니다.', '예: 2'],
    ['CLASS_NUMBER', DEFAULT_CONFIG.CLASS_NUMBER, '템플릿에 {반} placeholder가 있을 때만 사용합니다.', '예: 2'],
    ['CLASS_LABEL', DEFAULT_CONFIG.CLASS_LABEL, '{반번호} 또는 파일명/Tasks 제목에 쓰는 학반 표시입니다.', '예: 2-2'],
    ['TEACHER_NAME', DEFAULT_CONFIG.TEACHER_NAME, '템플릿에 {담임} placeholder가 있을 때만 사용합니다.', '예: 홍길동'],
    ['TEMPLATE_DOC_ID', '', '설치 도우미가 자동으로 입력합니다. 비어 있으면 출결 자동화 시트를 만든 설치 도우미를 다시 실행하세요.', '필수 / Google Docs 문서 ID'],
    ['DEST_FOLDER_ID', '', '생성된 신고서가 저장될 Google Drive 폴더 ID입니다. 메뉴로 자동 연결 가능합니다.', '비워두고 메뉴 실행 권장'],
    ['DEST_FOLDER_NAME', DEFAULT_CONFIG.DEST_FOLDER_NAME, '출력 폴더 자동 생성 시 사용할 폴더명입니다.', '출결 증빙'],
    ['TASK_LIST_ID', '', 'Google Tasks 목록 ID입니다. Tasks API 고급 서비스를 켠 뒤 메뉴 실행으로 자동 입력 가능합니다.', 'Tasks 사용 시 필수'],
    ['TASK_LIST_TITLE', DEFAULT_CONFIG.TASK_LIST_TITLE, 'Tasks 목록 자동 생성 시 사용할 이름입니다.', '출결 미제출 확인'],
    ['HOLIDAY_SHEET_NAME', DEFAULT_CONFIG.HOLIDAY_SHEET_NAME, '수업일 계산에서 제외할 휴일 시트 이름입니다.', '휴일'],
    ['ROSTER_SHEET_NAME', DEFAULT_CONFIG.ROSTER_SHEET_NAME, '학생 드롭다운 원본 시트입니다. A열 번호, B열 이름을 채우면 C열 번호+이름이 자동으로 만들어집니다.', '학생명단'],
    ['STUDENT_DROPDOWN_RANGE', DEFAULT_CONFIG.STUDENT_DROPDOWN_RANGE, '학생명단에서 월별 시트 B열 드롭다운으로 사용할 범위입니다.', 'C2:C200'],
    ['TIMEZONE', DEFAULT_CONFIG.TIMEZONE, '날짜 표시 시간대입니다.', 'Asia/Seoul'],
    ['MONTH_SHEET_NAMES', DEFAULT_CONFIG.MONTH_SHEET_NAMES, '자동화 대상 월별 입력 시트 이름입니다.', DEFAULT_CONFIG.MONTH_SHEET_NAMES],
    ['HOMEROOM_TASK_LIST_ID', DEFAULT_CONFIG.HOMEROOM_TASK_LIST_ID, '조종례시 담임학급 안내사항 Google Tasks 목록 ID입니다. 설치 도우미가 담임 설정에서 가져옵니다.', '담임일 때 자동 입력'],
    ['CENTRAL_CHAT_SENDER_URL', DEFAULT_CONFIG.CENTRAL_CHAT_SENDER_URL, '중앙 Google Chat 발송소 주소입니다. 공개 배포판에서 설정됩니다.', '예: https://chat-sender.example.com'],
    ['CENTRAL_CHAT_SHEET_ID', DEFAULT_CONFIG.CENTRAL_CHAT_SHEET_ID, '이 시트를 중앙 발송소가 구분하는 번호입니다. 자동 생성됩니다.', '자동'],
    ['CENTRAL_CHAT_SHEET_SECRET', DEFAULT_CONFIG.CENTRAL_CHAT_SHEET_SECRET, '이 시트에서 온 요청인지 확인하는 값입니다. 자동 생성됩니다.', '자동'],
    ['CLASS_CHAT_SPACE_ID', DEFAULT_CONFIG.CLASS_CHAT_SPACE_ID, '학급 단체방 Google Chat 스페이스 ID입니다. 교육청 메신저 정리·발송 메뉴에서 선택합니다.', '예: spaces/AAA...'],
    ['CLASS_CHAT_SPACE_NAME', DEFAULT_CONFIG.CLASS_CHAT_SPACE_NAME, '선생님이 알아볼 학급 Chat 방 이름입니다.', '예: 2학년 3반'],
    ['CHAT_LOG_SHEET_NAME', DEFAULT_CONFIG.CHAT_LOG_SHEET_NAME, '교육청 메신저 발송 기록 시트 이름입니다.', '발송기록'],
    ['PERSONAL_MESSAGE_QUEUE_SHEET_NAME', MESSENGER_PERSONAL_SHEET_NAME, '개인에게 보낼 쪽지를 모아두는 시트 이름입니다.', '메신저 개인톡 내용'],
    ['CLASS_MESSAGE_QUEUE_SHEET_NAME', MESSENGER_CLASS_SHEET_NAME, '학급 전체에게 보낼 쪽지를 모아두는 시트 이름입니다.', '메신저 단체톡 내용'],
    ['ATTENDANCE_AI_ALLOWED', ATTENDANCE_AI_ALLOWED_VALUE, 'Teacher Manager 정식 출석부에서 AI 입력을 켤 수 있게 하는 값입니다.', '예'],
    ['SCRIPT_ID', DEFAULT_CONFIG.SCRIPT_ID, '이 시트에 연결된 Apps Script 프로젝트 ID입니다. 설치/점검 때 자동 기록되어, 설치 기록 파일이 없는 컴퓨터나 사본 시트에서도 스크립트를 찾을 수 있습니다.', '자동 입력']
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

/**
 * 월 탭 하나를 준비한다.
 *
 * 모양은 탭 하나가 아니라 시트 전체를 보고 정한다. 자료가 있는 달은 그대로 두고
 * 빈 달만 내리면 같은 파일에서 달마다 줄 위치가 달라지고, 자료가 있는 달에서
 * 맨 윗줄 학생이 말없이 빠진다. 쓰던 시트는 사본 절차로만 새 모양이 된다.
 *
 * 그 판단은 첫 탭을 바꾸는 순간 달라지므로 부르는 쪽에서 한 번만 하고
 * 그 결과를 `moveTemplateRows`로 넘긴다.
 */
function ensureMonthSheet_(ss, name, moveTemplateRows) {
  let sh = ss.getSheetByName(name);
  const created = !sh;
  if (created) sh = ss.insertSheet(name);
  const moveThisTab = moveTemplateRows === true
    && isPristineTemplateMonthSheet_(sh);
  if (created || moveThisTab) {
    if (!created) sh.insertRowBefore(MONTHLY_ATTENDANCE_INPUT_ROW);
    // A열은 이름표, B열은 합친 입력칸의 첫 칸이다. 나머지는 비워 둔다.
    sh.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, INPUT_HEADERS.length)
      .setValues([[
        MONTHLY_ATTENDANCE_AI_INPUT_LABEL,
        MONTHLY_ATTENDANCE_AI_INPUT_HINT
      ].concat(new Array(INPUT_HEADERS.length - 2).fill(''))]);
    sh.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, INPUT_HEADERS.length)
      .setValues([INPUT_HEADERS]);
  }
  applyInputSheetFormatting_(sh);
  return sh;
}

/**
 * 시트 전체가 설치 견본 그대로인지 본다.
 * 있는 월 탭이 하나도 빠짐없이 견본 모양이어야 참이다.
 * 한 달이라도 자료가 있으면 거짓이다 — 쓰던 시트이므로 손대지 않는다.
 */
function isPristineTemplateWorkbook_(ss, monthNames) {
  if (!ss || typeof ss.getSheetByName !== 'function') return false;
  const names = (monthNames && monthNames.length)
    ? monthNames
    : getMonthSheetNames_(getConfig_());
  let seen = 0;
  for (let index = 0; index < names.length; index++) {
    const sheet = ss.getSheetByName(names[index]);
    if (!sheet) continue;  // 지운 지난 달은 판단에 넣지 않는다
    if (!isPristineTemplateMonthSheet_(sheet)) return false;
    seen++;
  }
  return seen > 0;
}

/**
 * 설치 견본 그대로인 월 탭인지 본다.
 * 1행이 정확한 제목 줄이고 그 아래에는 아무 자료도 없어야 참이다.
 */
function isPristineTemplateMonthSheet_(sheet) {
  if (!sheet || typeof sheet.getRange !== 'function') return false;
  if (typeof sheet.getLastRow !== 'function') return false;
  if (sheet.getLastRow() !== MONTHLY_ATTENDANCE_INPUT_ROW) return false;
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
 * A열에는 여기가 무엇을 하는 자리인지 적어 두고, B열부터 K열까지는 한 칸으로 합쳐
 * 흰 바탕에 테두리를 둘러 글을 적는 자리로 보이게 한다. 오른쪽 L~P는 색을 지운다 —
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

  // 우리가 써 둔 문구가 아니면 선생님이 적어 둔 문장이다. 어느 칸에 있든 지우지 않는다.
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
    if (boxIsOurs && inBox !== MONTHLY_ATTENDANCE_AI_INPUT_HINT) {
      box.setValue(MONTHLY_ATTENDANCE_AI_INPUT_HINT);
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
    .setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_HINT_COLOR)
    .setFontWeight('normal')
    .setHorizontalAlignment('left')
    .setVerticalAlignment('middle')
    .setBorder(true, true, true, true, false, false, '#C7C7C7', SpreadsheetApp.BorderStyle.SOLID);

  const tailStart = MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL + 1;
  const tailWidth = sh.getMaxColumns() - MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL;
  if (tailWidth > 0) {
    sh.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, tailStart, 1, tailWidth).setBackground(null);
  }
  sh.setRowHeight(MONTHLY_ATTENDANCE_INPUT_ROW, 34);
}

function applyInputSheetFormatting_(sh) {
  if (sh.getMaxRows() < 250) sh.insertRowsAfter(sh.getMaxRows(), 250 - sh.getMaxRows());
  if (sh.getMaxColumns() < 16) sh.insertColumnsAfter(sh.getMaxColumns(), 16 - sh.getMaxColumns());

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

  // M열부터 오른쪽은 제목도 자료도 없는 빈 열이다. 물려받은 색이 남지 않도록 지운다.
  const unusedColumns = sh.getMaxColumns() - MONTHLY_ATTENDANCE_LAST_DATA_COL;
  if (unusedColumns > 0) {
    sh.getRange(1, MONTHLY_ATTENDANCE_LAST_DATA_COL + 1, sh.getMaxRows(), unusedColumns)
      .setBackground(null);
  }

  sh.setFrozenRows(MONTHLY_ATTENDANCE_HEADER_ROW);
  sh.setColumnWidths(1, 1, 90);
  sh.setColumnWidths(2, 1, 120);
  sh.setColumnWidths(3, 2, 90);
  sh.setColumnWidths(5, 1, 220);
  sh.setColumnWidths(6, 3, 90);
  sh.setColumnWidths(9, 4, 70);

  const n = sh.getMaxRows() - MONTHLY_ATTENDANCE_HEADER_ROW;
  if (n > 0) {
    sh.getRange(MONTHLY_ATTENDANCE_DATA_START_ROW, 1, n, 1).setNumberFormat('yyyy-mm-dd');
    // 3행 아래 배경은 날짜 줄무늬만 쓴다. 예전에는 입력하는 칸임을 알리려고
    // B열·C~D열·F~H열에 옅은 색을 따로 칠했는데, 그 칸에서 줄무늬가 지워져
    // 한 날짜 덩어리가 A열부터 M열까지 이어지지 않고 구멍이 뚫렸다(2026-07-27).
    // 어느 칸에 적는지는 그 칸을 누를 때 나오는 드롭다운 화살표로 알 수 있다.

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

function buildAttendanceAiGeminiRequest_(sentence, context) {
  const recordProperties = {
    date: { type: 'string', format: 'date' },
    end_date: { type: 'string', format: 'date' },
    student: { type: 'string', minLength: 1 },
    category: { type: 'string', enum: ATTENDANCE_AI_CATEGORIES.slice() },
    kind: { type: 'string', enum: ATTENDANCE_AI_KINDS.slice() },
    reason: { type: 'string', minLength: 1 },
    period: { type: 'string', enum: ATTENDANCE_AI_PERIODS.slice() }
  };
  return {
    model: ATTENDANCE_AI_MODEL,
    input: JSON.stringify({
      instruction: (
        '출결 문장에서 요청한 학생별 출결 자료만 JSON으로 추출하세요. ' +
        'date는 시작일, end_date는 마지막 날이며 둘 다 포함합니다. ' +
        '하루뿐이면 두 날짜를 같게 적고, 기간은 날짜별로 나누지 말고 한 건으로 적으세요. ' +
        'requested_student_count에는 서로 다른 학생 수를 적으세요.'
      ),
      sentence: sentence,
      school_year: String(context.schoolYear),
      calendar_year: getAttendanceAiCalendarYear_(context.schoolYear, context.month),
      month: Number(context.month),
      allowed_values: {
        category: ATTENDANCE_AI_CATEGORIES.slice(),
        kind: ATTENDANCE_AI_KINDS.slice(),
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
      combined: String(row[2] === null || row[2] === undefined ? '' : row[2]).trim()
    };
  }).filter(row => row && row.number && row.name && row.combined);
  const sentence = sheetContext.sentence;
  const particles = [
    '에게서','한테서','께서','으로','에게','한테','부터','까지',
    '은','는','이','가','을','를','와','과','의','께','도','만','로'
  ];
  const isIdentityCharacter = character => (
    !!character && /[0-9A-Za-z가-힣]/.test(character)
  );
  const hasExactIdentityMention = (identity, blockedSuffixes) => {
    let searchFrom = 0;
    while (searchFrom <= sentence.length - identity.length) {
      const foundAt = sentence.indexOf(identity, searchFrom);
      if (foundAt < 0) return false;
      searchFrom = foundAt + 1;
      if (isIdentityCharacter(sentence.charAt(foundAt - 1))) continue;

      const tail = sentence.slice(foundAt + identity.length);
      if (blockedSuffixes.some(suffix => tail.indexOf(suffix) === 0)) continue;
      const hasBoundaryOrParticle = value => (
        !isIdentityCharacter(value.charAt(0))
        || particles.some(particle => (
          value.indexOf(particle) === 0
          && !isIdentityCharacter(value.charAt(particle.length))
        ))
      );
      if (hasBoundaryOrParticle(tail)) return true;
      if (tail.indexOf('학생') === 0 && hasBoundaryOrParticle(tail.slice(2))) {
        return true;
      }
    }
    return false;
  };
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
      || numberPattern.test(sentence);
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
    const reason = normalizeAttendanceAiReason_(record.reason);
    if (!studentText || !reason) {
      return null;
    }
    const matches = roster.filter(student => (
      studentText === student.combined
      || studentText === student.name
      || studentText === student.number
      || studentText === student.number + '번'
    ));
    if (
      matches.length !== 1
      || !studentAppearsInSentence(matches[0])
    ) {
      return null;
    }
    if (
      ATTENDANCE_AI_CATEGORIES.indexOf(record.category) < 0
      || ATTENDANCE_AI_KINDS.indexOf(record.kind) < 0
      || ATTENDANCE_AI_PERIODS.indexOf(record.period) < 0
    ) {
      return null;
    }

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
        record.category,
        record.kind,
        reason,
        record.period
      ].join('\u0000');
      if (seenRows.has(rowKey)) return null;
      seenRows.add(rowKey);
      validated.push({
        date: currentDate,
        rosterCombined: matches[0].combined,
        category: record.category,
        kind: record.kind,
        reason: reason,
        period: record.period
      });
    }
  }
  if (matchedStudents.size !== payload.requested_student_count) return null;
  validated.sort((left, right) => left.date.localeCompare(right.date));
  return validated;
}

function buildAttendanceAiBatchUpdate_(records, writeContext) {
  if (!Array.isArray(records) || !records.length || !writeContext) return null;
  const rows = records.map(record => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(record.date);
    if (!match) return null;
    const serial = Math.floor(
      Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / 86400000
    ) + 25569;
    const values = [
      { numberValue: serial },
      { stringValue: record.rosterCombined },
      { stringValue: record.category },
      { stringValue: record.kind },
      { stringValue: record.reason },
      { stringValue: record.period }
    ].map(userEnteredValue => ({ userEnteredValue: userEnteredValue }));
    // G~L은 비워 두고, M열에만 AI가 넣은 줄이라고 적는다.
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
 * 입력칸에 들어가면 회색 예시를 지워 빈 칸으로 만들고 글자색을 검정으로 바꾼다.
 * 예시가 글자로 남아 있으면 선생님이 그걸 먼저 지우고 써야 하기 때문이다.
 * 비운 채 다른 데를 누르면 예시를 되돌려 무엇을 적는 자리인지 다시 알려 준다.
 *
 * 휴대전화 구글 시트 앱에서는 이 장치가 돌지 않는다. 그때는 예시가 글자로 남아 있고,
 * 칸을 눌러 적으면 그 글자가 통째로 바뀐다 — 지금까지와 같다.
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
    if (inBox) {
      if (shown === MONTHLY_ATTENDANCE_AI_INPUT_HINT) box.setValue('');
      box.setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_TEXT_COLOR);
      return;
    }
    if (shown) return;
    // 빈 칸이라고 아무 데나 예시를 써 넣지 않는다. 1행이 우리 모양인 시트에서만 되돌린다.
    const label = String(
      sheet.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, 1).getValue() || ''
    ).trim();
    if (label !== MONTHLY_ATTENDANCE_AI_INPUT_LABEL) return;
    box.setValue(MONTHLY_ATTENDANCE_AI_INPUT_HINT);
    box.setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_HINT_COLOR);
  } catch (err) {
    // 칸을 고를 때마다 도는 자리다 — 무슨 일이 있어도 시트에 오류를 띄우지 않는다.
  }
}

/** 1행을 이름표와 회색 안내 문구로 되돌린다. 적었던 문장은 사라진다. */
function resetAttendanceAiInputRow_(sheet) {
  sheet.getRange(MONTHLY_ATTENDANCE_INPUT_ROW, 1, 1, 1)
    .setValue(MONTHLY_ATTENDANCE_AI_INPUT_LABEL);
  sheet.getRange(
    MONTHLY_ATTENDANCE_INPUT_ROW,
    MONTHLY_ATTENDANCE_AI_INPUT_COL,
    1,
    MONTHLY_ATTENDANCE_AI_INPUT_LAST_COL - MONTHLY_ATTENDANCE_AI_INPUT_COL + 1
  )
    .setValue(MONTHLY_ATTENDANCE_AI_INPUT_HINT)
    // 적을 때 검정으로 바꿔 뒀으므로 예시를 되돌릴 때 회색도 함께 돌려놓는다.
    .setFontColor(MONTHLY_ATTENDANCE_AI_INPUT_HINT_COLOR);
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

function handleAttendanceAiEdit(e, testPorts) {
  // 설치형 감지기는 실제 편집자 주소가 숨겨질 수 있다. 주소가 보이면 Gmail 편집을
  // 막고, 주소가 안 보여도 감지기를 만든 계정은 반드시 @goedu.kr인지 따로 확인한다.
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
  const ports = testPorts || {
    getTargetSpreadsheetId: () => PropertiesService.getScriptProperties()
      .getProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY),
    getGeminiApiKey: () => attendanceAiGeminiApiKey_(source),
    tryDocumentLock: () => {
      const lock = LockService.getDocumentLock();
      return lock && lock.tryLock(5000) ? lock : null;
    },
    readHeaderRow: targetSheet => targetSheet
      .getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, 12)
      .getValues()[0],
    resetInputRow: targetSheet => resetAttendanceAiInputRow_(targetSheet),
    reStripe: targetSheet => reStripeSheet_(targetSheet),
    readRosterRows: (spreadsheet, context) => {
      const rosterSheet = spreadsheet.getSheetByName(context.rosterSheetName);
      if (!rosterSheet || rosterSheet.getLastRow() < 2) return [];
      return rosterSheet.getRange(2, 1, rosterSheet.getLastRow() - 1, 4).getValues();
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
    callGemini: (request, apiKey) => {
      const response = UrlFetchApp.fetch(ATTENDANCE_AI_INTERACTIONS_URL, {
        method: 'post',
        contentType: 'application/json',
        headers: { 'x-goog-api-key': apiKey },
        payload: JSON.stringify(request),
        muteHttpExceptions: true
      });
      const responseCode = response.getResponseCode();
      if (responseCode < 200 || responseCode >= 300) {
        throw new Error('Gemini HTTP ' + responseCode);
      }
      return JSON.parse(response.getContentText());
    },
    readWriteState: targetSheet => ({
      headerRow: targetSheet
        .getRange(MONTHLY_ATTENDANCE_HEADER_ROW, 1, 1, 12)
        .getValues()[0],
      lastDataRow: targetSheet.getLastRow(),
      rowCount: targetSheet.getMaxRows(),
      sheetId: targetSheet.getSheetId()
    }),
    batchUpdate: (spreadsheetId, request) => (
      Sheets.Spreadsheets.batchUpdate(request, spreadsheetId)
    )
  };

  let targetSpreadsheetId;
  let apiKey;
  try {
    targetSpreadsheetId = String(ports.getTargetSpreadsheetId() || '').trim();
    apiKey = String(ports.getGeminiApiKey() || '').trim();
  } catch (err) {
    attendanceAiSkipLog_('대상 시트/키를 읽지 못함: ' + (err && err.message ? err.message : err));
    return { status: 'check_required' };
  }
  if (!targetSpreadsheetId || !apiKey) {
    attendanceAiSkipLog_(
      !targetSpreadsheetId
        ? 'AI 입력이 켜진 기록(대상 시트 번호)이 없음 — 처음 설정을 다시 실행 필요'
        : 'Gemini API 키를 설정 탭에서 찾지 못함'
    );
    return { status: 'disabled' };
  }
  if (String(source.getId()) !== targetSpreadsheetId) {
    attendanceAiSkipLog_('이 시트가 AI 입력 대상으로 기록된 시트와 다름');
    return { status: 'ignored' };
  }

  let sheetContext = testPorts && testPorts.context ? testPorts.context : null;
  if (!sheetContext) {
    try {
      const configSheet = source.getSheetByName(CONFIG_SHEET_NAME);
      if (!configSheet) return { status: 'ignored' };
      const configRows = configSheet.getDataRange().getValues();
      const config = {};
      configRows.forEach(row => {
        const key = String(row[0] || '').trim();
        if (key) config[key] = row[1];
      });
      const configuredMonthNames = String(config.MONTH_SHEET_NAMES || '')
        .split(',')
        .map(name => name.trim())
        .filter(Boolean);
      const monthMatch = /^(\d{1,2})월$/.exec(sheet.getName());
      if (!monthMatch) {
        attendanceAiSkipLog_('월 시트가 아님(시트 이름: ' + sheet.getName() + ')');
        return { status: 'ignored' };
      }
      sheetContext = {
        schoolYear: String(config.SCHOOL_YEAR || '').trim(),
        month: Number(monthMatch[1]),
        configuredMonthNames: configuredMonthNames,
        rosterSheetName: String(config.ROSTER_SHEET_NAME || '학생명단').trim()
      };
    } catch (err) {
      attendanceAiSkipLog_('설정 탭을 읽지 못함: ' + (err && err.message ? err.message : err));
      return { status: 'ignored' };
    }
  }

  const monthName = sheet.getName();
  if (
    !sheetContext
    || !Array.isArray(sheetContext.configuredMonthNames)
    || sheetContext.configuredMonthNames.indexOf(monthName) < 0
    || monthName !== String(Number(sheetContext.month)) + '월'
  ) {
    attendanceAiSkipLog_('시트 이름이 설정의 월 목록과 맞지 않음(시트: ' + monthName + ')');
    return { status: 'ignored' };
  }
  if (
    getAttendanceAiCalendarYear_(sheetContext.schoolYear, sheetContext.month) === null
  ) {
    attendanceAiSkipLog_(
      '설정 탭 SCHOOL_YEAR가 4자리 연도가 아님(지금 값: "' + sheetContext.schoolYear + '") — 설정 탭에서 2026처럼 고치면 됨'
    );
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
      return { status: 'ignored' };
    }
  } catch (err) {
    attendanceAiSkipLog_('2행 제목 줄을 읽지 못함: ' + (err && err.message ? err.message : err));
    return { status: 'ignored' };
  }

  let lock = null;
  try {
    lock = ports.tryDocumentLock();
    if (!lock) {
      attendanceAiSkipLog_('다른 처리가 도는 중(자물쇠 잡기 실패) — 잠시 뒤 다시 입력');
      return { status: 'busy' };
    }
    const requestContext = {
      schoolYear: sheetContext.schoolYear,
      month: sheetContext.month,
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
      ports.showMessage('휴일 탭에서 이 학년도의 휴일을 확인하지 못했습니다.');
      return { status: 'check_required' };
    }
    const request = buildAttendanceAiGeminiRequest_(sentence, requestContext);
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
    const rosterRows = ports.readRosterRows(source, sheetContext);
    const records = validateAttendanceAiRecords_(
      payload,
      rosterRows,
      requestContext,
      holidayDateKeys
    );
    if (!records) {
      attendanceAiSkipLog_(
        '문장을 출결로 확정하지 못함 — 학생 이름이 학생명단과 정확히 일치하는지, 날짜의 달이 이 시트의 달과 같은지 확인'
      );
      return { status: 'check_required' };
    }
    if (!records.length) {
      attendanceAiSkipLog_('입력한 기간에 수업일이 없음');
      ports.showMessage('입력한 기간에 수업일이 없습니다.');
      return { status: 'check_required' };
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
      return { status: 'check_required' };
    }
    const batchRequest = buildAttendanceAiBatchUpdate_(records, writeState);
    if (!batchRequest) return { status: 'check_required' };
    const response = ports.batchUpdate(targetSpreadsheetId, batchRequest);
    if (
      !response
      || typeof response !== 'object'
      || response.spreadsheetId !== targetSpreadsheetId
    ) {
      return { status: 'check_required' };
    }
    // 새 줄도 제 날짜 덩어리의 줄무늬 색을 갖게 한다. 칠하지 못해도 이미 넣은 줄은 그대로 둔다.
    try {
      ports.reStripe(sheet);
    } catch (err) {
      // 색은 다음에 A열을 고칠 때 다시 입혀진다.
    }
    return {
      status: 'applied',
      rows: records.length,
      startRow: Math.max(MONTHLY_ATTENDANCE_HEADER_ROW, writeState.lastDataRow) + 1
    };
  } catch (err) {
    // Gemini HTTP 오류(키 불량 등)와 기록 단계 오류가 전부 여기로 온다 — 이유를 남긴다.
    attendanceAiSkipLog_('처리 중 오류: ' + (err && err.message ? err.message : err));
    return { status: 'check_required' };
  } finally {
    if (lock && typeof lock.releaseLock === 'function') {
      try {
        lock.releaseLock();
      } catch (err) {
        // 이미 끝난 요청을 다시 보내거나 Sheet에 실패 표시를 쓰지 않는다.
      }
    }
    // 자물쇠를 잡고 실제로 처리한 편집만 되돌린다. 적었던 문장을 치우고 안내 문구를
    // 돌려놓아, 다음에 쓸 때 직접 지우지 않아도 되게 한다.
    // 다른 처리가 돌고 있어 잡지 못했으면(busy) 적은 문장을 그대로 남긴다 —
    // 아무 일도 일어나지 않았는데 글만 사라지면 다시 적어야 한다.
    if (lock) {
      try {
        ports.resetInputRow(sheet);
      } catch (err) {
        // 되돌리지 못해도 이미 만든 출결행은 그대로 둔다.
      }
    }
  }
}

/*************************************************
 * Teacher Manager 정식 출석부에서 1행 AI 입력 켜기
 *************************************************/

/** 설정 시트를 새로 만들지 않고 값 하나만 읽는다. 읽지 못하면 빈 값으로 본다. */
function readConfigValueReadOnly_(key) {
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CONFIG_SHEET_NAME);
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
  let spreadsheetId = '';
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    spreadsheetId = String(ss.getId() || '').trim();
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
  if (readConfigValueReadOnly_(ATTENDANCE_AI_ALLOWED_SETTING) === ATTENDANCE_AI_ALLOWED_VALUE) {
    return { ok: true, spreadsheetId: spreadsheetId, message: '' };
  }
  return {
    ok: false,
    spreadsheetId: spreadsheetId,
    message:
      '이 파일을 Teacher Manager 정식 출석부로 확인하지 못했습니다.\n\n' +
      '컴퓨터의 Teacher Manager에서 출결 시트를 하나로 정리하거나 처음 출결 준비를 끝낸 뒤 다시 눌러 주세요.'
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
function ensureAttendanceAiEditTrigger_(spreadsheetId) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
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

/** 메뉴: 이 사본에서만 1행 AI 입력을 켠다. */
function enableAttendanceAiInput(options) {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  // 통합 설정이 부를 때는 단계마다 창을 띄우지 않고 결과만 돌려준다 — 화면은 마지막에 한 번만 뜬다.
  const quiet = !!(options && options.quiet === true);
  function finish_(ok, message, created) {
    if (!quiet) ui.alert(ATTENDANCE_AI_MENU_ITEM, message, ui.ButtonSet.OK);
    return { ok: ok, created: created === true, message: message };
  }

  const state = attendanceAiWorkbookState_();
  if (!state || state.ok !== true) {
    return finish_(false, String(state && state.message || '이 파일에서는 켤 수 없습니다.'));
  }

  // 키는 컴퓨터의 티처 매니저에서 이미 받아 설정 탭에 들어와 있다. 여기서 다시 묻지 않는다.
  const apiKey = attendanceAiGeminiApiKey_(SpreadsheetApp.getActiveSpreadsheet());
  if (!isAttendanceAiApiKeyShape_(apiKey)) {
    return finish_(
      false,
      '이 시트에 쓸 Gemini API key를 찾지 못했습니다.\n\n' +
      '컴퓨터의 티처 매니저를 열고 [연결] 화면에서 Gemini API key를 넣어 저장한 다음\n' +
      '이 메뉴를 다시 눌러 주세요.\n\n' +
      'AI 입력은 아직 켜지지 않았습니다.'
    );
  }

  try {
    PropertiesService.getScriptProperties()
      .setProperty(ATTENDANCE_AI_TARGET_SPREADSHEET_ID_PROPERTY, state.spreadsheetId);
  } catch (err) {
    return finish_(
      false,
      '이 사본을 AI 입력 대상으로 기록하지 못했습니다.\n\n' + errorMessage_(err) + '\n\n' +
      'AI 입력은 켜지지 않았습니다.'
    );
  }

  let trigger;
  try {
    trigger = ensureAttendanceAiEditTrigger_(state.spreadsheetId);
  } catch (err) {
    return finish_(
      false,
      '1행 편집을 받는 감지기를 만들지 못했습니다.\n\n' + errorMessage_(err) + '\n\n' +
      '권한 승인 화면이 나오면 허용한 뒤 같은 메뉴를 다시 눌러 주세요.\n' +
      'AI 입력은 아직 켜지지 않았습니다.'
    );
  }

  return finish_(
    true,
    'AI 출결 입력을 켰습니다.\n\n' +
    (trigger.created ? '1행 편집을 받는 감지기를 새로 하나 만들었습니다.\n' : '이미 있던 감지기 하나를 그대로 씁니다.\n') +
    '\n월 시트 1행에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르면\n' +
    '맨 아래에 연한 초록색 출결행이 생깁니다.\n\n' +
    '이름을 못 찾거나 날짜·구분을 해석하지 못하면 아무 줄도 만들지 않습니다.',
    trigger.created
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

  if (sh.getMaxColumns() < ROSTER_HEADERS.length) {
    sh.insertColumnsAfter(sh.getMaxColumns(), ROSTER_HEADERS.length - sh.getMaxColumns());
  }

  const headerA = String(sh.getRange(1, 1).getValue() || '').trim();

  if (headerA === '번호+이름') {
    // 옛 배치(번호+이름이 A열)를 새 배치(번호/이름/번호+이름/이메일)로 옮긴다.
    const lastRow = Math.max(sh.getLastRow(), 2);
    const width = Math.min(4, sh.getMaxColumns());
    const vals = lastRow > 1 ? sh.getRange(2, 1, lastRow - 1, width).getValues() : [];
    const migrated = vals.map(row => {
      const combined = String(row[0] || '').trim();
      const no = String(row[1] || '').trim();
      const nm = String(row[2] || '').trim();
      const email = String((row[3] !== undefined ? row[3] : '') || '').trim();
      const parsed = parseStudentLabel_(combined);
      return [no || parsed.number, nm || parsed.name, combined || (no && nm ? no + nm : ''), email];
    }).filter(row => row[2]);
    // 옛 열(개인 DM 사용, Space ID, 비고, 마지막 DM 발송일)은 더 이상 쓰지 않는다.
    const hadDmColumns = sh.getMaxColumns() > ROSTER_HEADERS.length &&
      String(sh.getRange(1, 5).getValue() || '').trim() === '개인 DM 사용';
    sh.clear();
    if (hadDmColumns) {
      sh.deleteColumns(ROSTER_HEADERS.length + 1, Math.min(4, sh.getMaxColumns() - ROSTER_HEADERS.length));
    }
    sh.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues([ROSTER_HEADERS]);
    if (migrated.length) sh.getRange(2, 1, migrated.length, ROSTER_HEADERS.length).setValues(migrated);
  } else {
    sh.getRange(1, 1, 1, ROSTER_HEADERS.length).setValues([ROSTER_HEADERS]);
    fillRosterCombinedColumns_(sh);
  }

  // 드롭다운 원본이 옛 설정(A열)으로 남아 있으면 C열로 바꿔준다.
  const dropdownRange = String(cfg.STUDENT_DROPDOWN_RANGE || '').trim();
  const oldRangeMatch = dropdownRange.match(/^A(\d+):A(\d+)$/i);
  if (oldRangeMatch) {
    setConfigValue_('STUDENT_DROPDOWN_RANGE', 'C' + oldRangeMatch[1] + ':C' + oldRangeMatch[2]);
  }

  sh.getRange(1, 1, 1, ROSTER_HEADERS.length)
    .setBackground('#1F4E79')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center');
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 2, 90);
  sh.setColumnWidths(3, 1, 140);
  sh.setColumnWidths(4, 1, 240);
  sh.getDataRange().setWrap(true).setVerticalAlignment('middle');
}

// A열 번호, B열 이름을 적으면 C열(번호+이름)을 자동으로 만들고,
// 반대로 C열만 적으면 번호/이름을 자동으로 나눈다. 양방향 모두 지원한다.
function fillRosterCombinedColumns_(sh) {
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return;
  const values = sh.getRange(2, 1, lastRow - 1, 3).getValues();
  const updates = values.map(row => {
    const no = String(row[0] || '').trim();
    const nm = String(row[1] || '').trim();
    const combined = String(row[2] || '').trim();
    if (!combined && no && nm) return [no, nm, no + nm];
    const parsed = parseStudentLabel_(combined);
    return [no || parsed.number, nm || parsed.name, combined];
  });
  sh.getRange(2, 1, updates.length, 3).setValues(updates);
}

function parseStudentLabel_(value) {
  const text = String(value || '').trim();
  const match = text.match(/^(\d+)\s*(.+)$/);
  if (!match) return { number: '', name: text };
  return { number: match[1], name: String(match[2] || '').trim() };
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
// 테스트가 Node로 직접 실행한다. rows: [번호, 이름, 번호+이름, 학생 Google 이메일]
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
    const combined = String(row[2] || '').trim();
    // 학생 Google 이메일 — 있으면 개인 DM 대상이다.
    const email = String(row[3] || '').trim();
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
  const rosterName = cfg.ROSTER_SHEET_NAME || '학생명단';
  const roster = ss.getSheetByName(rosterName);
  if (!roster) return;
  const rangeA1 = cfg.STUDENT_DROPDOWN_RANGE || 'C2:C200';
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInRange(roster.getRange(rangeA1), true)
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
  let sh = ss.getSheetByName('드롭다운');
  if (!sh) sh = ss.insertSheet('드롭다운');
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
    ['placeholder','값 출처','적용 위치/규칙','비고'],
    ['{학교명} 또는 {{학교명}}','설정!SCHOOL_NAME','하단 학교명장 귀하','기존 템플릿에 있으면 치환'],
    ['{반번호}','설정!CLASS_LABEL + B열 번호','반번호 자리','예: 2-2 3번'],
    ['{번호}','B열 번호+이름에서 번호 분리','상단 번호','예: 3김가온 → 3'],
    ['{성명}','B열 번호+이름에서 이름 분리','성명/학생 서명','예: 3김가온 → 김가온'],
    ['{사유}','E열 사유','사유/확인내용',''],
    ['{시작교시}, {종료교시}','F열 교시','지각/조퇴/결과 row','결과는 시작=종료'],
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
  if (String(sh.getRange(1,1).getValue() || '').trim()) return;
  sh.getRange(1,1,1,6).merge().setValue('출결 신고서 자동화 사용 순서 — 기존 Google Docs 템플릿 그대로 사용')
    .setBackground('#111827').setFontColor('#ffffff').setFontWeight('bold').setFontSize(14).setHorizontalAlignment('center');
  const rows = [
    ['순서','작업','설명','','',''],
    [1,'Google Sheets로 열기','엑셀 파일을 구글 드라이브에 올린 뒤 Google Sheets로 엽니다.','','',''],
    [2,'Apps Script 붙여넣기','확장 프로그램 → Apps Script에 Code.gs 전체를 붙여넣고 저장합니다.','','',''],
    [3,'설정 입력','설정 시트 B열의 학교명, 학반, 담임, TEMPLATE_DOC_ID를 입력합니다.','','',''],
    [4,'기존 템플릿 사용','이미 있는 Google Docs 템플릿 ID를 사용합니다. 새 문서 템플릿은 만들지 않습니다.','','',''],
    [5,'학생명단 입력','학생명단에 번호와 이름을 나눠 입력하면 번호+이름은 자동으로 채워집니다. 개인 DM을 쓸 학생만 Google 이메일을 적습니다.','','',''],
    [6,'드롭다운 적용','메뉴의 입력 색/드롭다운 다시 적용을 누르면 B열 학생 드롭다운이 갱신됩니다.','','',''],
    [7,'신고서 생성','월별 시트에서 행을 선택하고 선택 행으로 신고서 만들기를 실행합니다.','','',''],
    [8,'Tasks 사용','Tasks 기능은 Google Tasks API 고급 서비스를 별도로 켜야 합니다.','','','']
  ];
  sh.getRange(3,1,rows.length,6).setValues(rows);
  sh.getRange(3,1,1,6).setBackground('#1F4E79').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
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
  const names = new Set(getMonthSheetNames_(cfg || getConfig_()));
  return ss.getSheets().filter(sh => names.has(sh.getName()));
}

function isInputMonthSheet_(sheet) {
  const names = new Set(getMonthSheetNames_(getConfig_()));
  return names.has(sheet.getName());
}

function isConfiguredMonthlyAttendanceSheetReadOnly_(sheet) {
  try {
    if (!sheet || typeof sheet.getParent !== 'function') return false;
    const spreadsheet = sheet.getParent();
    if (!spreadsheet || typeof spreadsheet.getSheetByName !== 'function') return false;
    const configSheet = spreadsheet.getSheetByName(CONFIG_SHEET_NAME);
    if (!configSheet) return false;
    const config = readConfigMapFromSheet_(configSheet);
    if (!Object.prototype.hasOwnProperty.call(config, 'MONTH_SHEET_NAMES')) return false;
    const configuredNames = config.MONTH_SHEET_NAMES;
    if (typeof configuredNames !== 'string' || !configuredNames.trim()) return false;
    const rawNames = configuredNames.split(',').map(name => name.trim());
    if (!rawNames.length || rawNames.some(name => !name)) return false;
    const names = getMonthSheetNames_(config);
    const uniqueNames = new Set(names);
    if (names.length !== rawNames.length || uniqueNames.size !== names.length) return false;
    return uniqueNames.has(sheet.getName());
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
  let sortSpec;
  if (mode === 'date') {
    sortSpec = [
      { column: 1, ascending: true },
      { column: 2, ascending: true }
    ];
  } else if (mode === 'student') {
    sortSpec = [
      { column: 2, ascending: true },
      { column: 1, ascending: true }
    ];
  } else {
    return false;
  }

  if (!sheet) return false;
  const lastRow = sheet.getLastRow();
  if (lastRow <= 2) return validateMonthlyAttendanceSortRange_(sheet, null);

  const lastColumn = Math.max(12, sheet.getLastColumn());
  const range = sheet.getRange(3, 1, lastRow - 2, lastColumn);
  if (!validateMonthlyAttendanceSortRange_(sheet, range)) return false;
  range.sort(sortSpec);
  return true;
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
  if (!id) throw new Error('설정 시트의 TEMPLATE_DOC_ID가 비어 있습니다. 출결 자동화 시트를 만든 설치 도우미를 다시 실행하세요.');
  return id;
}

function getDestinationFolder_() {
  const cfg = getConfig_();
  const folderId = String(cfg.DEST_FOLDER_ID || '').trim();
  if (folderId) return DriveApp.getFolderById(folderId);
  const folderName = String(cfg.DEST_FOLDER_NAME || DEFAULT_CONFIG.DEST_FOLDER_NAME).trim() || '출결 증빙';
  const folders = DriveApp.getFoldersByName(folderName);
  return folders.hasNext() ? folders.next() : DriveApp.createFolder(folderName);
}

function getTaskListId_() {
  const cfg = getConfig_();
  const id = String(cfg.TASK_LIST_ID || FALLBACK_TASK_LIST_ID || '').trim();
  if (!id) throw new Error('설정 시트의 TASK_LIST_ID가 비어 있습니다. 먼저 Tasks 목록 만들기/연결을 실행하세요.');
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

function checkExistingTemplateDoc() {
  try {
    requireGoeduTeacherAccount_();
    const id = getTemplateDocId_();
    const file = DriveApp.getFileById(id);
    const doc = DocumentApp.openById(id);
    SpreadsheetApp.getUi().alert('기존 템플릿 접근 확인 완료\n\n문서명: ' + file.getName() + '\n본문 길이: ' + doc.getBody().getText().length + '자');
  } catch (err) {
    SpreadsheetApp.getUi().alert('기존 템플릿 접근 실패\n\n' + (err && err.message ? err.message : err) + '\n\n확인: 설정!TEMPLATE_DOC_ID가 Google Docs 문서 ID인지, 현재 계정에 접근 권한이 있는지 확인하세요.');
  }
}

function connectDestinationFolder() {
  try {
    requireGoeduTeacherAccount_();
    const folder = getDestinationFolder_();
    setConfigValue_('DEST_FOLDER_ID', folder.getId());
    SpreadsheetApp.getUi().alert('출력 폴더 연결 완료\n\n폴더명: ' + folder.getName() + '\nID: ' + folder.getId());
  } catch (err) {
    SpreadsheetApp.getUi().alert('출력 폴더 연결 오류\n\n' + (err && err.message ? err.message : err));
  }
}

function connectTasksList() {
  requireGoeduTeacherAccount_();
  if (typeof Tasks === 'undefined') {
    SpreadsheetApp.getUi().alert(
      'Google Tasks API 고급 서비스가 켜져 있지 않습니다.\n\n' +
      'Apps Script 편집기 왼쪽 [서비스 +] → Google Tasks API 추가 후 다시 실행하세요.\n' +
      '이 설정은 Tasks 기능을 쓰는 사용자/스크립트마다 한 번 필요합니다.'
    );
    return;
  }

  try {
    const cfg = getConfig_();
    const title = String(cfg.TASK_LIST_TITLE || DEFAULT_CONFIG.TASK_LIST_TITLE).trim() || '출결 미제출 확인';
    const lists = Tasks.Tasklists.list().items || [];
    let found = lists.find(list => list.title === title);
    if (!found) found = Tasks.Tasklists.insert({ title });
    setConfigValue_('TASK_LIST_ID', found.id);
    SpreadsheetApp.getUi().alert('Tasks 목록 연결 완료\n\n목록명: ' + found.title + '\nID: ' + found.id);
  } catch (err) {
    SpreadsheetApp.getUi().alert('Google Tasks 연결 중 오류가 났습니다.\n\n' + (err && err.message ? err.message : err));
  }
}

function isChatAppConfigurationError_(err) {
  return isCentralChatConnectionError_(err);
}

function isExactGoeduEmail_(value) {
  return /^[^@\s]+@goedu\.kr$/i.test(String(value || '').trim());
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
  // 현재 사용자가 보이면 그 계정을 그대로 판단한다. Gmail 사용자가 시트를 열었는데
  // 소유자의 @goedu.kr 계정으로 바꿔 판단하면 안 된다.
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
  // 동작하게 한다. 다만 주소가 보인다면 정확한 @goedu.kr만 허용한다.
  const email = readSessionEmail_(event, false);
  return !email || isExactGoeduEmail_(email);
}

function requireGoeduTeacherAccount_(options) {
  const opts = options || {};
  const email = readSessionEmail_(opts.event, opts.allowEffectiveUser !== false);
  if (!isExactGoeduEmail_(email)) {
    throw new Error(
      '이 계정으로는 진행할 수 없어요. 교육디지털원패스 및 경기도교육청 ' +
      '클라우드 지원시스템에서 준비한 @goedu.kr 계정으로 다시 로그인해 주세요.'
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
        '이 감지기를 만든 계정으로는 진행할 수 없어요. 교육디지털원패스 및 경기도교육청 ' +
        '클라우드 지원시스템에서 준비한 @goedu.kr 계정으로 AI 출결 입력을 다시 켜 주세요.'
      );
    }
    return effectiveEmail;
  }
  return email;
}

function requireGoeduStudentAccount_(value) {
  const email = String(value || '').trim();
  if (!isExactGoeduEmail_(email)) {
    throw new Error(
      '학생 개인톡은 @goedu.kr 학생 계정으로만 보낼 수 있어요. 학생 계정을 확인해 주세요.'
    );
  }
  return email;
}

function centralChatPathNeedsTeacher_(path) {
  // 연결을 끊거나 서버 기록을 지우는 길만 예외다. 새 작업 주소가 나중에 생겨도
  // 목록에 깜빡하고 더하지 않았다는 이유로 개인 계정에서 열리지 않게 기본은 차단한다.
  return ['/v1/disconnect', '/v1/account/delete']
    .indexOf(String(path || '').trim()) === -1;
}

function callCentralChatSender_(path, payload) {
  if (centralChatPathNeedsTeacher_(path)) requireGoeduTeacherAccount_();
  const safePayload = Object.assign({}, payload || {});
  if (String(path || '').trim() === '/v1/send/personal') {
    safePayload.studentEmail = requireGoeduStudentAccount_(safePayload.studentEmail);
  }
  const central = ensureCentralChatConfig_();
  if (!central.url) {
    throw new Error('Google Chat 중앙 발송소 주소가 비어 있습니다. 공개 배포 설정을 확인해 주세요.');
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
    throw new Error('중앙 발송소 응답을 읽지 못했습니다: ' + text.slice(0, 200));
  }
  if (status < 200 || status >= 300) {
    throw centralSenderError_(status, data);
  }
  return data;
}

function centralSenderError_(status, data) {
  const code = String(data && (data.error || data.code) || 'CENTRAL_SENDER_ERROR').trim();
  const err = new Error(String(data && data.message || code));
  err.centralCode = code;
  err.httpStatus = Number(status || 0);
  return err;
}

function errorMessage_(err) {
  return String(err && err.message ? err.message : err || '알 수 없는 오류');
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

// 발송을 새 정식 출석부로 옮긴 뒤 옛 시트에서 보내려 할 때 서버가 주는 답.
// 코드 글자를 그대로 보여주면 선생님은 고장 난 줄 알게 된다.
const CENTRAL_SHEET_MOVED_CODE = 'SHEET_MOVED';
const CENTRAL_SHEET_MOVED_MESSAGE =
  '이 시트의 Google Chat 발송은 새 정식 출석부로 옮겼습니다.\n\n' +
  'Teacher Manager에서 현재 출석부를 열어 보내 주세요.\n\n' +
  '이 시트에서는 더 이상 보내지지 않습니다.';

/** 중앙 발송소 오류를 선생님이 읽을 문장으로 바꾼다. 모르는 오류는 그대로 둔다. */
function centralChatErrorMessage_(err) {
  const code = String(err && err.centralCode || '').trim();
  if (code === CENTRAL_SHEET_MOVED_CODE) return CENTRAL_SHEET_MOVED_MESSAGE;
  if (code === 'GOEDU_ACCOUNT_REQUIRED') {
    return '이 계정으로는 진행할 수 없어요. @goedu.kr 계정으로 다시 로그인해 주세요.';
  }
  return errorMessage_(err);
}

function isCentralChatConnectionError_(err) {
  const code = String(err && err.centralCode || '').trim();
  if (code === CENTRAL_SHEET_MOVED_CODE) return true;
  if (['CHAT_NOT_CONNECTED', 'SHEET_AUTH_REQUIRED', 'AUTH_STATE_EXPIRED', 'GOEDU_ACCOUNT_REQUIRED'].indexOf(code) !== -1) return true;
  const message = String(err && err.message ? err.message : err || '');
  return message.indexOf('최초 발송 연결') !== -1 ||
    message.indexOf('연결') !== -1 ||
    message.indexOf('권한') !== -1 ||
    message.indexOf('중앙 발송소') !== -1;
}

function showChatApiSetupRequired_(ui) {
  ui.alert(
    'Google Chat 자동 발송 준비가 아직 끝나지 않았습니다.\n\n' +
    '같은 실패를 반복하지 않도록 발송을 멈췄습니다.\n\n' +
    '보낼 내용은 원래 시트의 상태 칸에 남겨두었습니다.\n' +
    '준비가 끝난 뒤 같은 시트 메뉴를 다시 누르면 재시도됩니다.\n\n' +
    '공개 배포판에서는 선생님이 별도 관리 화면을 열지 않아도 되도록 중앙 발송 방식이 준비되어야 합니다.'
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
    return finish_(false, 'Google Chat 학급 단톡방을 고르지 못했습니다.\n\n' + (err && err.message ? err.message : err));
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
  const cleanStatus = String(status || '확인필요').trim();
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
  const rows = [];
  (items || []).forEach(item => {
    const content = normalizeMessageLine_(item && item.content);
    const name = String(item && item.name || '').trim();
    if (!content || !name) return;
    rows.push([
      String(item.sendDate || todayKey_()).trim(),
      String(item.number || '').trim(),
      name,
      String(item.type || '기타').trim(),
      content,
      String(item.source || '자동분석').trim(),
      String(item.status || '확인필요').trim(),
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
  const lines = [];
  (items || []).forEach(item => {
    const content = item && item.content;
    if (content) lines.push(content);
  });
  return appendClassMessageQueueLines_(lines, todayKey_(), '자동분석', '확인필요', '기타');
}

function appendAnalyzedMessageQueueItemsForAutomation(payload) {
  requireGoeduTeacherAccount_();
  const data = payload || {};
  return {
    personal: appendPersonalMessageQueueItemsForAutomation(data.personal || []),
    class: appendClassMessageQueueItemsForAutomation(data.class || data.group || [])
  };
}

function countQueueRowsByDateAndStatus_(rows, dateIndex, statusIndex, targetDate, status) {
  let count = 0;
  (rows || []).forEach(row => {
    const dateKey = formatQueueDateKey_(row[dateIndex], '');
    if (dateKey === targetDate && String(row[statusIndex] || '').trim() === status) count++;
  });
  return count;
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

function claimPersonalQueueRows_(sheet, group, requestId) {
  return withDocumentLock_(() => {
    const rows = getQueueRows_(sheet, PERSONAL_MESSAGE_QUEUE_HEADERS.length);
    const canClaim = (group.rowNumbers || []).every(rowNumber => {
      const row = rows[Number(rowNumber) - 2] || [];
      return String(row[6] || '').trim() === '대기';
    });
    if (!canClaim) return false;
    setPersonalQueueRowsResult_(sheet, group.rowNumbers, '발송중', timestampKey_(), requestId);
    return true;
  });
}

function claimClassQueueRows_(sheet, rowNumbers, requestId) {
  return withDocumentLock_(() => {
    const rows = getQueueRows_(sheet, CLASS_MESSAGE_QUEUE_HEADERS.length);
    const canClaim = (rowNumbers || []).every(rowNumber => {
      const row = rows[Number(rowNumber) - 2] || [];
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
    return '메시지는 발송됐지만 발송기록 저장에 실패했습니다: ' + errorMessage_(err);
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
    (_, index) => String(headerRow[startCol - 1 + index] || '').trim()
  );
  // 비었거나 제 이름인 칸만 있으면 손대도 된다. 다른 글자가 하나라도 있으면
  // 선생님이 직접 쓰신 제목이므로 아무것도 하지 않는다.
  const onlyOursOrEmpty = currentHeaders.every(
    (value, index) => !value || value === MONTHLY_CHAT_RESULT_HEADERS[index]
  );
  if (!onlyOursOrEmpty) return null;

  ensureSheetHasColumns_(sheet, requiredLastCol);
  // 빈 칸만 채운다. 예전 판으로 만든 시트에는 앞의 세 칸만 있고 네 번째
  // `Google Chat 내용기준`이 비어 있다. 그 한 칸 때문에 그 달 전체를
  // 건너뛰면 발송이 조용히 멈춘다.
  let headerChanged = false;
  currentHeaders.forEach((value, index) => {
    if (value) return;
    sheet.getRange(MONTHLY_ATTENDANCE_HEADER_ROW, startCol + index, 1, 1)
      .setValues([[MONTHLY_CHAT_RESULT_HEADERS[index]]]);
    headerChanged = true;
  });
  if (headerChanged) {
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
    if (typeof sheet.hideColumns === 'function') sheet.hideColumns(startCol + 3);
  }
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

function sendTodayClassMessageQueue_(targetDateKey) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensureClassMessageQueueSheet_(ss);
  recoverStaleQueueClaims_(sh, CLASS_MESSAGE_QUEUE_HEADERS.length, 4, 5);
  const rows = getQueueRows_(sh, CLASS_MESSAGE_QUEUE_HEADERS.length);
  const targetDate = String(targetDateKey || todayKey_()).trim();
  const grouped = groupClassMessageQueueRows_(rows, targetDate);
  if (!grouped.lines.length) return { sent: 0, failed: 0, rows: 0 };

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
  if (!claimClassQueueRows_(sh, grouped.rowNumbers, requestId)) return { sent: 0, failed: 0, rows: 0 };
  let sendResult;
  try {
    sendResult = sendCentralClassChat_(classSpaceId, text, {
      requestId: requestId,
      source: 'messenger-class',
      targetDate: targetDate
    });
  } catch (err) {
    const error = errorMessage_(err);
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
    if (logWarning) setClassQueueRowsResult_(sh, grouped.rowNumbers, '보냄', sentAt, success + '\n' + logWarning);
    return { sent: 1, failed: 0, rows: grouped.rowNumbers.length };
  } catch (err) {
    const warning = '메시지는 발송됐지만 발송 기록 저장에 실패했습니다: ' + errorMessage_(err);
    safeAppendChatLog_(['단체방', classSpaceName, sendResult.spaceId || classSpaceId, text, '기록주의', warning]);
    return { sent: 1, failed: 0, rows: grouped.rowNumbers.length, recordWarning: warning, failures: [warning] };
  }
}

function sendTodayPersonalMessageQueue_(targetDateKey) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ensurePersonalMessageQueueSheet_(ss);
  recoverStaleQueueClaims_(sh, PERSONAL_MESSAGE_QUEUE_HEADERS.length, 6, 8);
  const rows = getQueueRows_(sh, PERSONAL_MESSAGE_QUEUE_HEADERS.length);
  const targetDate = String(targetDateKey || todayKey_()).trim();
  const groups = groupPersonalMessageQueueRows_(rows, targetDate);
  const rosterMap = loadStudentRosterForDm_();
  const result = { sent: 0, failed: 0, rows: 0, failures: [], chatApiSetupBlocked: false };

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
    if (!claimPersonalQueueRows_(sh, group, requestId)) continue;

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
      const error = errorMessage_(err);
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
      if (logWarning) setPersonalQueueRowsResult_(sh, group.rowNumbers, '보냄', sentAt, success + '\n' + logWarning);
    } catch (err) {
      const warning = '메시지는 발송됐지만 발송 기록 저장에 실패했습니다: ' + errorMessage_(err);
      safeAppendChatLog_(['개인DM', student.combined || target, sendResult.spaceId || '', text, '기록주의', warning]);
      result.failures.push((student.combined || target) + ': ' + warning);
    }
    result.sent++;
    result.rows += group.rowNumbers.length;
  }
  return result;
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
    const waitingReview = countQueueRowsByDateAndStatus_(classRows, 0, 4, today, '확인필요');

    if (!classGroup.lines.length) {
      const msg = waitingReview
        ? `보낼 대기 단체 쪽지가 없습니다.\n\n확인필요 상태 ${waitingReview}줄이 있습니다. 검토 후 상태를 대기로 바꾸면 보낼 수 있습니다.`
        : '보낼 대기 단체 쪽지가 없습니다.';
      ui.alert(msg);
      return;
    }

    const confirmLines = [
      '메신저 단체톡 내용의 보낼 대기 줄을 학급 단톡방으로 보냅니다.',
      '',
      '단체 쪽지: ' + classGroup.lines.length + '줄'
    ];
    if (waitingReview) confirmLines.push('', '확인필요 상태 ' + waitingReview + '줄은 보내지 않습니다.');
    const confirm = ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기', confirmLines.join('\n'), ui.ButtonSet.OK_CANCEL);
    if (confirm !== ui.Button.OK) return;

    const result = sendTodayClassMessageQueue_(today);
    ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기를 마쳤습니다.\n\n처리한 줄: ' + result.rows + '줄');
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 단체톡 보내기 중 오류가 났습니다.\n\n' + (err && err.message ? err.message : err));
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
    const waitingReview = countQueueRowsByDateAndStatus_(personalRows, 0, 6, today, '확인필요');

    if (!personalGroups.length) {
      const msg = waitingReview
        ? `보낼 대기 개인 쪽지가 없습니다.\n\n확인필요 상태 ${waitingReview}줄이 있습니다. 검토 후 상태를 대기로 바꾸면 보낼 수 있습니다.`
        : '보낼 대기 개인 쪽지가 없습니다.';
      ui.alert(msg);
      return;
    }

    const totalLines = personalGroups.reduce((sum, group) => sum + group.lines.length, 0);
    const confirmLines = [
      '메신저 개인톡 내용의 보낼 대기 줄을 학생별 개인톡으로 보냅니다.',
      '',
      '개인 쪽지: ' + personalGroups.length + '명 / ' + totalLines + '줄'
    ];
    if (waitingReview) confirmLines.push('', '확인필요 상태 ' + waitingReview + '줄은 보내지 않습니다.');
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
      '성공 ' + result.sent + '명, 실패/건너뜀 ' + result.failed + '명'
    ];
    if (result.failures.length) done.push('', '확인 필요:', ...result.failures.slice(0, 10));
    ui.alert(done.join('\n'));
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡 보내기 중 오류가 났습니다.\n\n' + (err && err.message ? err.message : err));
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
    const waitingReview =
      countQueueRowsByDateAndStatus_(personalRows, 0, 6, today, '확인필요') +
      countQueueRowsByDateAndStatus_(classRows, 0, 4, today, '확인필요');

    if (!personalGroups.length && !classGroup.lines.length) {
      const msg = waitingReview
        ? `보낼 대기 쪽지가 없습니다.\n\n확인필요 상태 ${waitingReview}줄이 있습니다. 검토 후 상태를 대기로 바꾸면 종례 때 보낼 수 있습니다.`
        : '보낼 대기 쪽지가 없습니다.';
      ui.alert(msg);
      return;
    }

    const confirmLines = [
      '오늘 종례 쪽지를 보냅니다.',
      '',
      '단체 쪽지: ' + classGroup.lines.length + '줄',
      '개인 쪽지: ' + personalGroups.length + '명 / ' + personalGroups.reduce((sum, group) => sum + group.lines.length, 0) + '줄'
    ];
    if (waitingReview) confirmLines.push('', '확인필요 상태 ' + waitingReview + '줄은 보내지 않습니다.');
    const confirm = ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기', confirmLines.join('\n'), ui.ButtonSet.OK_CANCEL);
    if (confirm !== ui.Button.OK) return;

    SpreadsheetApp.getActive().toast('Google Chat 연결 상태를 확인하는 중입니다.', '진행 중', 3);
    const status = callCentralChatSender_('/v1/status', {});
    if (!status.connected) {
      showChatApiSetupRequired_(ui);
      return;
    }

    let classResult = { sent: 0, failed: 0, rows: 0, error: '' };
    let personalResult = { sent: 0, failed: 0, rows: 0, failures: [], chatApiSetupBlocked: false };
    SpreadsheetApp.getActive().toast('단체 쪽지를 보내는 중입니다.', '진행 중', 3);
    try {
      classResult = sendTodayClassMessageQueue_(today);
    } catch (err) {
      if (isCentralChatConnectionError_(err)) {
        showChatApiSetupRequired_(ui);
        return;
      }
      classResult = { sent: 0, failed: 1, rows: classGroup.rowNumbers.length, error: errorMessage_(err) };
    }
    SpreadsheetApp.getActive().toast('개인 쪽지를 보내는 중입니다.', '진행 중', 3);
    try {
      personalResult = sendTodayPersonalMessageQueue_(today);
    } catch (err) {
      if (isCentralChatConnectionError_(err)) {
        showChatApiSetupRequired_(ui);
        return;
      }
      personalResult = { sent: 0, failed: 1, rows: 0, failures: [errorMessage_(err)], chatApiSetupBlocked: false };
    }
    if (personalResult.chatApiSetupBlocked) {
      showChatApiSetupRequired_(ui);
      return;
    }
    SpreadsheetApp.getActive().toast('Google Chat 발송 결과를 정리하는 중입니다.', '진행 중', 3);

    const done = [
      '메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기를 마쳤습니다.',
      '',
      '단체: ' + classResult.rows + '줄 처리',
      '개인: 성공 ' + personalResult.sent + '명, 실패/건너뜀 ' + personalResult.failed + '명'
    ];
    if (classResult.error) done.push('', '단체 확인 필요:', classResult.error);
    if (personalResult.failures.length) done.push('', '확인 필요:', ...personalResult.failures.slice(0, 10));
    ui.alert(done.join('\n'));
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(ui);
      return;
    }
    ui.alert('메신저 쪽지 내용 Google Chat으로 개인톡+단체톡 보내기 중 오류가 났습니다.\n\n' + (err && err.message ? err.message : err));
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
  // 통합 설정이 부를 때는 실패 창을 띄우지 않고 결과만 돌려준다.
  // 권한 허용·연결 화면은 조용해도 그대로 띄운다 — 사람이 눌러야 끝나는 일이라 없앨 수 없다.
  const quiet = !!(options && options.quiet === true);
  try {
    requireGoeduTeacherAccount_();
    const info = ScriptApp.getAuthorizationInfo(ScriptApp.AuthMode.FULL);
    const authorizationUrl = info.getAuthorizationStatus() === ScriptApp.AuthorizationStatus.REQUIRED
      ? String(info.getAuthorizationUrl() || '').trim()
      : '';
    if (authorizationUrl) {
      showLinkDialog_('Google 권한 연결', authorizationUrl, '권한 허용을 마친 뒤 이 메뉴를 다시 눌러 주세요.');
      return { ok: false, message: '권한 허용 화면을 열었습니다. 허용을 마친 뒤 이 메뉴를 다시 눌러 주세요.' };
    }
    const result = callCentralChatSender_('/v1/auth/start', {});
    showLinkDialog_('Google Chat 최초 발송 연결하기', result.authUrl, '연결을 마친 뒤 시트로 돌아오세요.');
    // 브라우저에서 연결을 마쳐야 끝나므로 이 자리에서는 아직 됐다고 하지 않는다.
    return { ok: false, message: '연결 화면을 열었습니다. 브라우저에서 연결을 마친 뒤 이 메뉴를 다시 눌러 주세요.' };
  } catch (err) {
    const message = 'Google Chat 최초 발송 연결을 시작하지 못했습니다.\n\n' + centralChatErrorMessage_(err);
    if (!quiet) ui.alert(message);
    return { ok: false, message: message };
  }
}

function checkCentralChatStatus() {
  requireGoeduTeacherAccount_();
  const ui = SpreadsheetApp.getUi();
  try {
    const status = callCentralChatSender_('/v1/status', {});
    if (!status.connected) {
      ui.alert(status.reason || 'Google Chat 최초 발송 연결이 아직 안 됐습니다.\n\n[Google Chat 최초 발송 연결하기]를 먼저 눌러 주세요.');
      return;
    }
    ui.alert(
      'Google Chat 발송 연결됨\n\n' +
      '연결 계정: ' + (status.account || '') + '\n' +
      '발송 방식: 선생님 이름으로 발송\n' +
      '개인톡: ' + (status.personalEnabled ? '가능' : '확인 필요') + '\n' +
      '단체톡: ' + (status.classEnabled ? '가능' : '학급 단톡방 고르기 필요')
    );
  } catch (err) {
    ui.alert('Google Chat 연결 상태를 확인하지 못했습니다.\n\n' + centralChatErrorMessage_(err));
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
    ui.alert('Google Chat 발송 연결을 끊지 못했습니다.\n\n' + (err && err.message ? err.message : err));
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
    SpreadsheetApp.getActive().toast('신고서 생성 오류: ' + err, '오류', 6);
  }
}

function createDocFromRowForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
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
  if (!date) throw new Error('A열(날짜) 없음/형식 오류');

  // B) 번호+이름
  const studentInfoRaw = String(rowVals[1] || '').trim().replace(/\s+/g, '');
  const m = studentInfoRaw.match(/^(\d{1,2})(.+)$/);
  if (!m) throw new Error('B열 형식 오류(예: 23최예향 / 3김가온)');
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

  if (!kindLabel) throw new Error('D열(종류) 오류: 결석/지각/조퇴/결과 중 하나가 필요합니다.');
  if ((kindLabel === '지각' || kindLabel === '조퇴' || kindLabel === '결과') && !periodNum) {
    throw new Error('F열(교시) 오류: 지각/조퇴/결과는 교시 숫자가 필요합니다.');
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
        'Google Tasks API 고급 서비스가 켜져 있지 않습니다.\n\n' +
        'Apps Script 편집기 왼쪽 [서비스 +] → Google Tasks API 추가 후 다시 실행하세요.\n' +
        '이 설정은 Tasks 기능을 쓰는 사용자/스크립트마다 한 번 필요합니다.'
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

    if (result.created === 0) {
      SpreadsheetApp.getUi().alert('추가된 Task가 없습니다.\n- G/H열이 ‘미제출’인지 확인\n- 같은 제목(같은 날짜)이 이미 미완료로 있을 수 있습니다.');
    } else {
      SpreadsheetApp.getActive().toast(`Tasks ${result.created}건 추가됨`, '완료', 3);
    }
  } catch (err) {
    SpreadsheetApp.getActive().toast('Tasks 추가 오류: ' + err, '오류', 6);
  }
}

function addRowToTasksForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
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
  if (!dataRows.length) return { created: 0, titles: [] };
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
    titles: Array.from(titlesToCreate)
  };
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

    if (!result.sent && !result.failed) {
      SpreadsheetApp.getUi().alert(
        '보낼 개인톡이 없습니다.\n- G열(신고서)/H열(첨부)이 "미제출"인지 확인\n- 이미 발송된 내용이면 다시 보내지 않습니다.'
      );
    } else {
      SpreadsheetApp.getActive().toast(`개인톡 성공 ${result.sent}명, 실패/건너뜀 ${result.failed}명`, '완료', 5);
    }
  } catch (err) {
    if (isChatAppConfigurationError_(err)) {
      showChatApiSetupRequired_(SpreadsheetApp.getUi());
      return;
    }
    SpreadsheetApp.getActive().toast('개인톡 발송 오류: ' + err, '오류', 6);
  }
}

function sendSelectedRowChatForAutomation(sheetName, rowIdx) {
  requireGoeduTeacherAccount_();
  const sheet = getSheetForAutomation_(sheetName);
  return sendSelectedRowsPersonalMessagesNow_(sheet, [Number(rowIdx)]);
}

function claimAttendanceChatRow_(sheet, group) {
  // 큐 발송(claimPersonalQueueRows_)과 같은 잠금 + '발송중' 선점 —
  // 더블클릭·두 탭 동시 실행 방어를 서버 requestId dedup 하나에만 맡기지 않는다.
  // 크래시로 남은 '발송중'은 시도시각 기준 10분 뒤 다시 집어갈 수 있다.
  return withDocumentLock_(() => {
    const cols = ensureMonthlyChatResultColumns_(sheet);
    if (!cols) return false;
    const rowValues = sheet.getRange(group.rowIdx, cols.statusCol, 1, 4).getValues()[0];
    const status = String(rowValues[0] || '').trim();
    const savedSignature = String(rowValues[3] || '').trim();
    if (status === '보냄' && savedSignature === group.signature) return false;
    if (status === '발송중' && !queueClaimIsStale_(rowValues[1], new Date())) return false;
    setMonthlyChatResult_(sheet, group.rowIdx, '발송중', '', group.signature);
    return true;
  });
}

function sendSelectedRowsPersonalMessagesNow_(sheet, selectedRows) {
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 개인톡을 보냅니다.');
  }
  const result = {
    sent: 0,
    failed: 0,
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
    if (!group.email) {
      const error = '학생 Google 이메일 없음';
      setMonthlyChatResult_(sheet, group.rowIdx, '실패', error);
      appendChatLog_('출결 개인톡', group.target || group.name, '', text, '건너뜀', error);
      result.failed++;
      result.rows++;
      result.failures.push((group.target || group.name || group.rowIdx) + ': ' + error);
      continue;
    }

    if (!claimAttendanceChatRow_(sheet, group)) {
      // 다른 실행이 이미 이 행을 보내는 중이거나 방금 보냈다 — 건너뛴다.
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
      const error = errorMessage_(err);
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
      if (logWarning) setMonthlyChatResult_(sheet, group.rowIdx, '보냄', success + '\n' + logWarning, group.signature);
    } catch (err) {
      const warning = '메시지는 발송됐지만 발송 기록 저장에 실패했습니다: ' + errorMessage_(err);
      safeAppendChatLog_(['출결 개인톡', group.target || group.email, sendResult.spaceId || '', text, '기록주의', warning]);
      result.failures.push((group.target || group.email || group.rowIdx) + ': ' + warning);
    }
    result.sent++;
    result.rows++;
  }
  return result;
}

function getSheetForAutomation_(sheetName) {
  const name = String(sheetName || '').trim();
  if (!name) throw new Error('sheetName이 비어 있습니다.');
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(name);
  if (!sheet) throw new Error(`시트를 찾을 수 없습니다: ${name}`);
  return sheet;
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

    // 학생명단에서 번호(A)/이름(B)/번호+이름(C)을 고치면 C열을 바로 자동 채움
    const cfg = getConfig_();
    if (sheet.getName() === (cfg.ROSTER_SHEET_NAME || '학생명단')) {
      if (startCol <= 3 && endCol >= 1) fillRosterCombinedColumns_(sheet);
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

    // A열을 포함한 편집/붙여넣기 때만 실행 → 성능 최적화
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
/** 메뉴: 현재 탭만 줄무늬 재적용 */
function reStripeActiveSheet() {
  requireGoeduTeacherAccount_();
  const sheet = SpreadsheetApp.getActiveSheet();
  reStripeSheet_(sheet);
}

/** 메뉴: 모든 탭에 줄무늬 재적용 */
function reStripeAllSheets() {
  requireGoeduTeacherAccount_();
  const ss = SpreadsheetApp.getActive();
  ss.getSheets().forEach(sh => reStripeSheet_(sh));
}

/** 핵심: A열의 '같은 날짜 블록'마다 교대로 색을 칠함 (A:L) */
function reStripeSheet_(sheet) {
  if (shouldSkipSheet_(sheet)) return;

  const lastRow = sheet.getLastRow();
  if (lastRow < MONTHLY_ATTENDANCE_DATA_START_ROW) return;

  const endCol = Math.min(STRIPE_END_COL, sheet.getMaxColumns());
  if (endCol < 1) return;

  const numRows = lastRow - MONTHLY_ATTENDANCE_DATA_START_ROW + 1;
  const stripeRange = sheet.getRange(
    MONTHLY_ATTENDANCE_DATA_START_ROW,
    1,
    numRows,
    endCol
  );
  const vals = stripeRange.getValues();
  const bgs  = new Array(numRows);

  let currentDate = null; // 현재 블록 기준 날짜
  let groupIndex  = -1;   // 0,1,0,1… 토글

  for (let r = 0; r < numRows; r++) {
    const v = vals[r][0];
    const isDate = (v instanceof Date) && !isNaN(v.getTime());
    const rowHasContent = vals[r].some(cell => cell !== '' && cell !== null);

    if (isDate) {
      if (!currentDate || !isSameYMD_(currentDate, v)) {
        currentDate = v;
        groupIndex++;
      }
      const color = (groupIndex % 2 === 0) ? STRIPE_COLOR_WHITE : STRIPE_COLOR_GRAY;
      bgs[r] = Array(endCol).fill(color);
    } else if (rowHasContent && currentDate) {
      // 병합 셀처럼 A열 날짜가 첫 줄에만 있는 경우 같은 날짜 블록으로 간주
      const color = (groupIndex % 2 === 0) ? STRIPE_COLOR_WHITE : STRIPE_COLOR_GRAY;
      bgs[r] = Array(endCol).fill(color);
    } else {
      currentDate = null;
      bgs[r] = Array(endCol).fill(null);
    }
  }

  stripeRange.setBackgrounds(bgs);
}

/** 날짜(연-월-일) 동일성 비교 */
function isSameYMD_(d1, d2) {
  return d1.getFullYear() === d2.getFullYear() &&
         d1.getMonth()    === d2.getMonth() &&
         d1.getDate()     === d2.getDate();
}

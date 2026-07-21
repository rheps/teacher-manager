/**
 * 출결 신고서 자동화 · 기존 Google Docs 템플릿 유지
 * 버전: 5.8.4
 *   (아래 APP_VERSION과 항상 같아야 한다. 버전을 올릴 때 두 곳을 함께 고친다 — 테스트가 대조 검사함)
 * for Google Sheets + Google Docs + Google Tasks
 *
 * 핵심 원칙:
 * - 월별 입력표 A:P 구조 유지
 * - 신고서 양식은 이미 만들어 둔 Google Docs 템플릿을 그대로 복사해서 사용
 * - 새 템플릿 문서 생성 기능은 넣지 않음
 * - 학생명단은 A열 번호, B열 이름을 적으면 C열 '번호+이름'이 자동 생성되고, 월별 시트 B열 드롭다운으로 연결
 */

const APP_NAME = '출결 신고서 자동화';
const APP_VERSION = '5.8.4';
// 제작자 정보는 설정 시트가 아니라 코드에 고정한다.
// 설정 시트에 두면 사용자가 지웠을 때 되살릴 방법이 없다.
const APP_AUTHOR_NAME = 'Big-Silver EDU LAB (http://big-silver.xyz)\n부천 중원고등학교 김대은';
const APP_REPO_URL = 'https://github.com/rheps/teacher-task-manager-skill';
const CONFIG_SHEET_NAME = '설정';
const DEFAULT_MONTH_SHEETS = ['3월','4월','5월','6월','7월','8월','9월','10월','11월','12월','1월','2월'];

const FALLBACK_TEMPLATE_DOC_ID = '';   // 설정 시트 TEMPLATE_DOC_ID 우선. 비상용으로만 사용.
const FALLBACK_TASK_LIST_ID = '';      // 설정 시트 TASK_LIST_ID 우선.
const FALLBACK_CLASS_LABEL = '';
const FALLBACK_HOLIDAY_SHEET_NAME = '휴일';

/***** 날짜 줄무늬 설정 *****/
const STRIPE_START_ROW = 2;      // 헤더 1행, 데이터는 2행부터
const STRIPE_END_COL  = 16;      // A(1)~P(16)
const STRIPE_COLOR_WHITE = '#ffffff';
const STRIPE_COLOR_GRAY  = '#bdbdbd'; // 구글 시트 팔레트 '회색' 중앙 톤 근사값

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

  // 연결 관련 3항목은 전용 메뉴 하나로 모은다 — 다른 메뉴와 중복 배치하지 않는다.
  ui.createMenu('Google Chat 연결')
    .addItem('Google Chat 최초 발송 연결하기', 'startCentralChatConnection')
    .addItem('Google Chat 연결 상태 확인', 'checkCentralChatStatus')
    .addItem('Google Chat 학급 단톡방 고르기', 'connectClassChatSpace')
    .addToUi();

  ui.createMenu('출결 업무 자동화')
    .addItem('선택 행 출결신고서 Google Docs에 만들기', 'createDocFromTemplate')
    .addItem('선택 행 미제출 서류 Google Tasks에 추가하기', 'addSelectedRowToTasks')
    .addItem('선택 행 미제출 서류 Google Chat 개인톡 보내기', 'sendSelectedRowsChatNow')
    .addSeparator()
    .addSubMenu(ui.createMenu('고급 설정')
      .addItem('기본 시트/설정 점검', 'setupAttendanceWorkbook')
      .addItem('입력 색/드롭다운 다시 적용', 'refreshInputFormattingAndDropdowns')
      .addItem('기존 템플릿 ID/접근 점검', 'checkExistingTemplateDoc')
      .addItem('출력 폴더 만들기/연결', 'connectDestinationFolder')
      .addItem('Tasks 목록 만들기/연결', 'connectTasksList')
      .addItem('Chat 발송 연결 점검', 'checkCentralChatStatus')
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

  monthNames.forEach(name => ensureMonthSheet_(ss, name));
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

function ensureMonthSheet_(ss, name) {
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  const firstRow = sh.getRange(1, 1, 1, 8).getValues()[0];
  const empty = firstRow.every(v => String(v || '').trim() === '');
  if (empty) sh.getRange(1, 1, 1, 8).setValues([INPUT_HEADERS]);
  applyInputSheetFormatting_(sh);
  return sh;
}

function applyInputSheetFormatting_(sh) {
  if (sh.getMaxRows() < 250) sh.insertRowsAfter(sh.getMaxRows(), 250 - sh.getMaxRows());
  if (sh.getMaxColumns() < 16) sh.insertColumnsAfter(sh.getMaxColumns(), 16 - sh.getMaxColumns());

  sh.getRange(1, 1, 1, 16)
    .setBackground('#1F4E79')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle');

  sh.getRange(1, 1, 1, 8).setValues([INPUT_HEADERS]);
  sh.setFrozenRows(1);
  sh.setColumnWidths(1, 1, 90);
  sh.setColumnWidths(2, 1, 120);
  sh.setColumnWidths(3, 2, 90);
  sh.setColumnWidths(5, 1, 220);
  sh.setColumnWidths(6, 3, 90);
  sh.setColumnWidths(9, 8, 70);

  const n = sh.getMaxRows() - 1;
  if (n > 0) {
    sh.getRange(2, 1, n, 1).setNumberFormat('yyyy-mm-dd');
    sh.getRange(2, 2, n, 1).setBackground('#EAF4FF');
    sh.getRange(2, 3, n, 2).setBackground('#FFF7E6');
    sh.getRange(2, 6, n, 3).setBackground('#F3F4F6');

    const categoryRule = SpreadsheetApp.newDataValidation().requireValueInList(['질병','미인정','기타','출석인정'], true).setAllowInvalid(false).build();
    const kindRule = SpreadsheetApp.newDataValidation().requireValueInList(['결석함','지각함','조퇴함','결과함'], true).setAllowInvalid(false).build();
    const periodRule = SpreadsheetApp.newDataValidation().requireValueInList(['','1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례'], true).setAllowInvalid(true).build();
    const statusRule = SpreadsheetApp.newDataValidation().requireValueInList(['','제출','미제출','해당없음'], true).setAllowInvalid(true).build();

    sh.getRange(2, 3, n, 1).setDataValidation(categoryRule);
    sh.getRange(2, 4, n, 1).setDataValidation(kindRule);
    sh.getRange(2, 6, n, 1).setDataValidation(periodRule);
    sh.getRange(2, 7, n, 2).setDataValidation(statusRule);
  }

  sh.getRange(1, 1, sh.getMaxRows(), 16).setVerticalAlignment('middle').setWrap(true);
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
    const n = sh.getMaxRows() - 1;
    if (n > 0) sh.getRange(2, 2, n, 1).setDataValidation(rule);
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
    const folder = getDestinationFolder_();
    setConfigValue_('DEST_FOLDER_ID', folder.getId());
    SpreadsheetApp.getUi().alert('출력 폴더 연결 완료\n\n폴더명: ' + folder.getName() + '\nID: ' + folder.getId());
  } catch (err) {
    SpreadsheetApp.getUi().alert('출력 폴더 연결 오류\n\n' + (err && err.message ? err.message : err));
  }
}

function connectTasksList() {
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

function callCentralChatSender_(path, payload) {
  const central = ensureCentralChatConfig_();
  if (!central.url) {
    throw new Error('Google Chat 중앙 발송소 주소가 비어 있습니다. 공개 배포 설정을 확인해 주세요.');
  }
  const body = Object.assign({}, payload || {}, {
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

function isCentralChatConnectionError_(err) {
  const code = String(err && err.centralCode || '').trim();
  if (['CHAT_NOT_CONNECTED', 'SHEET_AUTH_REQUIRED', 'AUTH_STATE_EXPIRED'].indexOf(code) !== -1) return true;
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

function connectClassChatSpace() {
  const ui = SpreadsheetApp.getUi();
  try {
    const response = callCentralChatSender_('/v1/spaces', {});
    const spaces = response.spaces || [];
    if (!spaces.length) {
      ui.alert('선생님이 들어가 있는 Google Chat 단톡방을 찾지 못했습니다.');
      return;
    }
    const choices = spaces.slice(0, 20).map((space, index) => `${index + 1}. ${space.displayName} (${space.name})`).join('\n');
    const answer = ui.prompt(
      'Google Chat 학급 단톡방 고르기',
      '학급 쪽지를 보낼 Google Chat 단톡방 번호를 입력하세요.\n\n' + choices,
      ui.ButtonSet.OK_CANCEL
    );
    if (answer.getSelectedButton() !== ui.Button.OK) return;
    const index = Number(String(answer.getResponseText() || '').trim()) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= Math.min(spaces.length, 20)) {
      ui.alert('번호를 확인할 수 없습니다. 목록에 보이는 번호를 입력해 주세요.');
      return;
    }
    const selected = spaces[index];
    callCentralChatSender_('/v1/class-space', {
      spaceName: selected.name,
      displayName: selected.displayName
    });
    setConfigValue_('CLASS_CHAT_SPACE_ID', selected.name);
    setConfigValue_('CLASS_CHAT_SPACE_NAME', selected.displayName);
    ui.alert('Google Chat 학급 단톡방을 골랐습니다.\n\n' + selected.displayName);
  } catch (err) {
    ui.alert('Google Chat 학급 단톡방을 고르지 못했습니다.\n\n' + (err && err.message ? err.message : err));
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
  const resultCols = ensureMonthlyChatResultColumns_(sheet);
  const rosterMap = loadStudentRosterForDm_();
  const values = sheet.getDataRange().getValues();
  return (selectedRows || []).map(rowIdx => {
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

function attendanceChatSignature_(sheetName, rowIdx, row) {
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
  const lines = [];
  (items || []).forEach(item => {
    const content = item && item.content;
    if (content) lines.push(content);
  });
  return appendClassMessageQueueLines_(lines, todayKey_(), '자동분석', '확인필요', '기타');
}

function appendAnalyzedMessageQueueItemsForAutomation(payload) {
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
  const headerRow = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0].map(v => String(v || '').trim());
  let startCol = headerRow.indexOf(MONTHLY_CHAT_RESULT_HEADERS[0]) + 1;
  let headerChanged = false;
  if (!startCol) {
    startCol = sheet.getLastColumn() + 1;
    ensureSheetHasColumns_(sheet, startCol + MONTHLY_CHAT_RESULT_HEADERS.length - 1);
    sheet.getRange(1, startCol, 1, MONTHLY_CHAT_RESULT_HEADERS.length).setValues([MONTHLY_CHAT_RESULT_HEADERS]);
    headerChanged = true;
  } else {
    const signatureCol = startCol + 3;
    ensureSheetHasColumns_(sheet, signatureCol);
    const signatureHeader = String(headerRow[signatureCol - 1] || '').trim();
    if (signatureHeader !== MONTHLY_CHAT_RESULT_HEADERS[3]) {
      sheet.getRange(1, signatureCol).setValue(MONTHLY_CHAT_RESULT_HEADERS[3]);
      headerChanged = true;
    }
  }
  if (headerChanged) {
    const range = sheet.getRange(1, startCol, Math.max(sheet.getMaxRows(), 1), MONTHLY_CHAT_RESULT_HEADERS.length);
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
  const cols = ensureMonthlyChatResultColumns_(sheet);
  if (!cols || !rowIdx || rowIdx < 2) return;
  sheet.getRange(rowIdx, cols.statusCol, 1, 4).setValues([[
    status || '',
    timestampKey_(),
    result || '',
    signature || ''
  ]]);
}

function clearMonthlyChatResultForRows_(sheet, startRow, endRow) {
  const cols = ensureMonthlyChatResultColumns_(sheet);
  if (!cols || startRow < 2 || endRow < startRow) return;
  sheet.getRange(startRow, cols.statusCol, endRow - startRow + 1, 4).clearContent();
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
  return sendTodayPersonalMessagesOnly();
}

function sendMessengerClassMessages() {
  return sendTodayClassMessagesOnly();
}

function sendMessengerAllMessages() {
  return sendTodayDismissalMessages();
}

function startCentralChatConnection() {
  const ui = SpreadsheetApp.getUi();
  try {
    const info = ScriptApp.getAuthorizationInfo(ScriptApp.AuthMode.FULL);
    const authorizationUrl = info.getAuthorizationStatus() === ScriptApp.AuthorizationStatus.REQUIRED
      ? String(info.getAuthorizationUrl() || '').trim()
      : '';
    if (authorizationUrl) {
      showLinkDialog_('Google 권한 연결', authorizationUrl, '권한 허용을 마친 뒤 이 메뉴를 다시 눌러 주세요.');
      return;
    }
    const result = callCentralChatSender_('/v1/auth/start', {});
    showLinkDialog_('Google Chat 최초 발송 연결하기', result.authUrl, '연결을 마친 뒤 시트로 돌아오세요.');
  } catch (err) {
    ui.alert('Google Chat 최초 발송 연결을 시작하지 못했습니다.\n\n' + errorMessage_(err));
  }
}

function checkCentralChatStatus() {
  const ui = SpreadsheetApp.getUi();
  try {
    const status = callCentralChatSender_('/v1/status', {});
    if (!status.connected) {
      ui.alert('Google Chat 최초 발송 연결이 아직 안 됐습니다.\n\n[Google Chat 최초 발송 연결하기]를 먼저 눌러 주세요.');
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
    ui.alert('Google Chat 연결 상태를 확인하지 못했습니다.\n\n' + (err && err.message ? err.message : err));
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
    const startRow = Math.max(STRIPE_START_ROW, r.getRow());
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
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

    const selectedRows = getSelectedDataRows_(sheet);
    if (selectedRows.length === 0) {
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 2행 이하의 대상 행(들)을 선택한 후 실행하세요.');
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
  const sheet = getSheetForAutomation_(sheetName);
  return createDocsFromRows_(sheet, [Number(rowIdx)]);
}

function createDocsFromRows_(sheet, selectedRows) {
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 신고서를 만들 수 있습니다.');
  }
  const ss = sheet.getParent ? sheet.getParent() : SpreadsheetApp.getActiveSpreadsheet();
  const holidaySet = loadHolidaySet_(ss);
  const absIndex = buildAbsenceIndex_(sheet); // 현재 월 시트 기준
  const issuedSpanKeys = new Set();           // 연속결석 묶음 중복 생성 방지

  let created = 0, fails = [];
  selectedRows.forEach(rowIdx => {
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
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 2행 이하의 대상 행(들)을 선택한 후 실행하세요.');
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
  const sheet = getSheetForAutomation_(sheetName);
  return addRowsToTasks_(sheet, [Number(rowIdx)]);
}

function addRowsToTasks_(sheet, selectedRows) {
  if (!sheet || !isInputMonthSheet_(sheet)) {
    throw new Error('월별 입력 시트에서만 Tasks를 추가합니다.');
  }
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

  for (const rowIdx of selectedRows) {
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
      SpreadsheetApp.getUi().alert('선택된 데이터 행이 없습니다. 2행 이하의 대상 행(들)을 선택한 후 실행하세요.');
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
  ensureMonthlyChatResultColumns_(sheet);
  const groups = buildAttendanceChatGroupsForSelectedRows_(sheet, selectedRows);
  const result = {
    sent: 0,
    failed: 0,
    rows: 0,
    failures: [],
    chatApiSetupBlocked: false
  };
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

    const requestId = stableChatRequestId_([
      centralSheetIdentityForRequest_(),
      group.sheetName,
      group.rowIdx,
      group.email,
      text
    ]);
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
        clearMonthlyChatResultForRows_(sheet, Math.max(2, range.getRow()), Math.max(2, range.getLastRow()));
      }
    }

    // A열을 포함한 편집/붙여넣기 때만 실행 → 성능 최적화
    if (startCol <= 1 && endCol >= 1) {
      reStripeSheet_(sheet);
    }
  } catch (err) {
    console.log('onEdit error:', err);
  }
}
/** 메뉴: 현재 탭만 줄무늬 재적용 */
function reStripeActiveSheet() {
  const sheet = SpreadsheetApp.getActiveSheet();
  reStripeSheet_(sheet);
}

/** 메뉴: 모든 탭에 줄무늬 재적용 */
function reStripeAllSheets() {
  const ss = SpreadsheetApp.getActive();
  ss.getSheets().forEach(sh => reStripeSheet_(sh));
}

/** 핵심: A열의 '같은 날짜 블록'마다 교대로 색을 칠함 (A:P) */
function reStripeSheet_(sheet) {
  if (shouldSkipSheet_(sheet)) return;

  const lastRow = sheet.getLastRow();
  if (lastRow < STRIPE_START_ROW) return;

  const endCol = Math.min(STRIPE_END_COL, sheet.getMaxColumns());
  if (endCol < 1) return;

  const numRows = lastRow - STRIPE_START_ROW + 1;
  const vals = sheet.getRange(STRIPE_START_ROW, 1, numRows, endCol).getValues();
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

  sheet.getRange(STRIPE_START_ROW, 1, numRows, endCol).setBackgrounds(bgs);
}

/** 날짜(연-월-일) 동일성 비교 */
function isSameYMD_(d1, d2) {
  return d1.getFullYear() === d2.getFullYear() &&
         d1.getMonth()    === d2.getMonth() &&
         d1.getDate()     === d2.getDate();
}

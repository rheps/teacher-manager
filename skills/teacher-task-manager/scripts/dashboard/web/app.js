"use strict";
/* Teacher Manager 화면. 규칙: 화면은 bridge만 부른다. 업무 판단은 전부 파이썬에 있다. */

/* ---------- 상태 ---------- */
const S = {
  network: null,  // 배경 이름 확인(예열) 상태 — 느린 컴퓨터에서 기다려 달라는 안내에 쓴다
  mode: "loading",           // loading | wizard | home | edit | about
  step: 1,
  draft: { profile: {}, grid: null, bridge: {} },
  info: null,                // get_app_info data
  checks: [],
  google: null,
  gwsUpdate: null,            // 승인 확인 결과와 현재 실제 Google 도구 판
  gwsUpdateInstalling: false, // 화면을 다시 그려도 갱신을 두 번 시작하지 않는다
  computer: null,
  computerLoading: false,
  attachmentFolderStatus: null,
  lists: { calendars: [], tasklists: [] },
  linkLoading: false,
  listReads: {},
  listsLoaded: false,
  listsError: false,
  maps: { calendars: {}, tasklists: {} },
  edit: null,
  busy: {},
  banner: null,              // {kind, text}
  problemIssue: null,        // needs_user | failed only; normal input validation stays beside its field
  externalLinkFallback: null, // 브라우저가 안 열렸을 때만 {url}; Google 로그인 주소는 오지 않는다
  toast: null,
  applyResults: null,
  firstHomeNotice: false,     // 마법사 직후 홈에 한 번 보여주는 사용법 안내 띠
  updateInfo: null,           // get_update_info 결과 (새 버전 있을 때만 채움)
  updateCheck: null,          // 업데이트 확인 결과 (null | "latest" | "available" | "failed")
  updating: false,            // 지금 업데이트 진행 중 (중복 클릭·재실행 방지)
  updateOffer: null,          // update_offer 결과 — 켤 때 한 번 묻는 자리에서 씀
  aiTools: null,              // AI 에이전트 탭 — 도구 감지 결과 (null=미조회, "loading"=조회 중)
  aiNode: null,               // 사용자가 연결을 누른 뒤에만 읽는 전용 Node 준비 상태
  aiInstall: null,            // AI 에이전트 탭 — 연결 실행 결과
  aiSelected: null,           // 사용자가 고른 정확한 AI 목록 — 화면을 다시 그려도 보존
  aiConnecting: false,        // 화면을 다시 그려도 두 번째 다운로드를 시작하지 않는다
  maxStep: 1,                 // 마법사에서 한 번이라도 도달한 가장 먼 단계
  login: null,
  progress: null,            // capture_progress 스냅샷 (active일 때만)
  lastLiveStep: null,        // 실패 시 어느 단계에서 멈췄는지 표시용
  doneShown: "",             // 결과 카드를 이미 보여준 run_id
  caps: null,                // recent_captures 목록 (null=미로딩)
  capsPage: 1,
  capsTotalPages: 0,
  capsLoading: false,
  capsOpen: {},              // 펼친 기록 줄 key -> true
  freshWhen: "",             // "방금" 배지를 달 기록의 when
  fieldIssues: {},           // target -> {key, target, message, tab}
  connectTab: "messenger",   // messenger | attendance
  attendance: null,          // attendance_status/ensure_attendance 응답
  firstSetupDone: false,     // 시트 [처음 설정 한 번에 끝내기] 완료 확인 (마법사 출결 탭)
  firstSetupReadState: null, // checking / ready / unavailable are not completion values
  firstSetupConnectionCode: "", // 완료 표시가 어느 출석부 확인번호에 묶였는지
  attendanceStaleNotice: false, // 준비 뒤 실제로 바꾼 출석부 설정/로컬 화면 묶음만 기록
  attendanceSaving: false,   // 탭을 오가며 다시 그려도 출결 준비 중복 클릭을 막는다
  attendanceScriptUpdate: null, // 사용자가 눌러 확인한 기존 출결 Apps Script 상태
  attendanceScriptDialog: null, // null | "update"
  attendanceScriptUpdating: false,
  attendanceConnection: null, // null | 후보 조회·선택 대화상자 상태
  attendanceConnectionBusy: false,
  attendanceTransitioning: false, // 정리·새 학년도 전환 중에는 화면을 다시 그려도 재실행 금지
  workspaceGuideOpen: false,   // 1단계 "화면에서 찾기"로 뜨는 구글 워크스페이스 위치 캡처
  chatStatus: null,          // attendance_chat_status 응답 (null=미조회, "loading"=질의 중)
  spaceDraftName: undefined,  // 방 이름칸에 쓴 값 (undefined면 내 정보로 만든 기본값을 쓴다)
  spaceCreate: null,          // null | "ok" | "blocked" | 실패 사유 문자열
  chatSpacesError: false,     // 방 목록을 못 읽었는지 — "방이 없다"와 갈라 놓는다
  chatNewSpaceBrowserOpen: false, // 새 단톡방을 만들려고 Google Chat을 열었는지
  chatNewSpaceBrowserBlurred: false, // 실제로 브라우저로 자리를 옮겼다가 돌아왔는지
  helperRestartPending: false, // 설정은 확인했지만 도우미 시작만 다시 확인할 상태
};
let attendanceScriptRequestToken = 0;
let attendanceConnectionRequestToken = 0;
let aiToolsRequestToken = 0;

const WIZARD_STEPS = [
  "시작 전 준비", "Google 로그인", "내 정보", "하루 일과", "시간표",
  "담임학급 학생명단", "이 컴퓨터 설정", "Google 연결", "모두 저장",
];
const GOEDU_REQUIRED_MESSAGE = "Google 계정으로 다시 로그인해 주세요.";
function isGoeduGoogleStatus(status) {
  return Boolean(status && status.logged_in && status.account_allowed === true);
}
function isGoogleReady(status) {
  return isGoeduGoogleStatus(status) && status.authorization_state === "ready";
}
function googleAuthorizationMessage(status) {
  if (googleAuthCheckFailed(status)) return GOOGLE_AUTH_CHECK_MESSAGE;
  if (!isGoeduGoogleStatus(status)) return status?.logged_in ? GOEDU_REQUIRED_MESSAGE : FIELD_MESSAGES["google-login"];
  return status.authorization_detail || "Google 권한을 다시 승인해야 해요. 다시 로그인하고 승인해 주세요.";
}
function attendanceUiEnabled() {
  return S.info?.features?.attendance_ui_enabled === true;
}

/* ---------- bridge ---------- */
class AppIssueError extends Error {
  constructor(issue) {
    super(issue?.message || issue?.title || "작업을 마치지 못했어요.");
    this.issue = issue || null;
  }
}
let accountWireToken = null;
let accountUiEpoch = 0;
let accountProfileNeedsReload = false;
let accountProfileReloadPromise = null;
class StaleAccountResponse extends Error {}
const googleAccountReads = new Map();
function call(name, ...args) {
  if (name === "google_status" || name === "gws_login_status") {
    const epoch = googleLoginEpoch, accountEpoch = accountUiEpoch;
    const key = JSON.stringify([name, epoch, accountEpoch]);
    if (googleAccountReads.has(key)) return googleAccountReads.get(key);
    const result = callBridge(name, ...args).catch(error => {
      if (!(error instanceof StaleAccountResponse) && name === "google_status"
          && epoch === googleLoginEpoch && accountEpoch === accountUiEpoch) {
        adoptGoogleStatus({ ...S.google, logged_in: false, account_allowed: false, user: "",
          login_state: "error", error_code: "GWS_AUTH_STATUS_FAILED", authorization_state: "check_failed",
          authorization_reason: "", authorization_detail: GOOGLE_AUTH_CHECK_MESSAGE });
        render();
      }
      throw error;
    }).finally(() => { if (googleAccountReads.get(key) === result) googleAccountReads.delete(key); });
    googleAccountReads.set(key, result);
    return result;
  }
  if (!ATTENDANCE_READ_METHODS.has(name)) return callBridge(name, ...args);
  const epoch = accountUiEpoch;
  const contextForRead = () => ["attendance_status", "attendance_status_cached"].includes(name)
    ? googleListContext() : chatReadContext();
  const context = contextForRead();
  const captured = JSON.parse(JSON.stringify(args));
  const key = JSON.stringify([epoch, context, name, captured]);
  if (attendanceReads.has(key)) return attendanceReads.get(key);
  // Share identical reads, but let independent panels obtain their own result.
  const result = callBridge(name, ...captured).finally(() => {
    if (attendanceReads.get(key) === result) attendanceReads.delete(key);
  });
  attendanceReads.set(key, result);
  return result;
}
const ATTENDANCE_READ_METHODS = new Set([
  "attendance_status_cached", "attendance_status", "attendance_chat_status",
  "attendance_first_setup_status", "attendance_roster_status", "attendance_chat_spaces",
]);
const ATTENDANCE_TIMED_READS = new Set(["attendance_roster_status", "attendance_chat_spaces"]);
const attendanceReads = new Map();
function callBridge(name, ...args) {
  const epoch = accountUiEpoch;
  const loginEpoch = googleLoginEpoch;
  const currentReadContext = () => ["attendance_status", "attendance_status_cached"].includes(name)
    ? googleListContext() : chatReadContext();
  const readContext = ATTENDANCE_READ_METHODS.has(name) ? currentReadContext() : null;
  const api = window.pywebview.api;
  const expectedToken = accountWireToken ? [...accountWireToken] : null;
  const requestArgs = JSON.parse(JSON.stringify(args));
  const send = async () => {
    const response = typeof api.account_call === "function"
      ? api.account_call(name, requestArgs, expectedToken) : api[name](...requestArgs);
    let timer;
    try {
      return await (ATTENDANCE_TIMED_READS.has(name) ? Promise.race([response,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("목록 응답을 기다리는 시간이 지났어요.")), 120000);
        }),
      ]) : response);
    } finally { if (timer) clearTimeout(timer); }
  };
  return send().then((res) => {
    if (["google_status", "gws_login_status", "gws_login_start"].includes(name)
        && loginEpoch !== googleLoginEpoch) throw new StaleAccountResponse();
    if (epoch !== accountUiEpoch) throw new StaleAccountResponse("계정이 바뀌어 이전 화면의 작업을 멈췄어요.");
    if (readContext !== null && readContext !== currentReadContext()) throw new StaleAccountResponse();
    if (!res || res.ok !== true) throw new AppIssueError(res?.issue);
    if (Array.isArray(res.session)) {
      const previous = accountWireToken;
      accountWireToken = res.session;
      if (previous && JSON.stringify(previous) !== JSON.stringify(res.session)) {
        accountUiEpoch += 1;
        if (previous[0] !== res.session[0]) {
          accountProfileNeedsReload = true;
          clearAccountScreen();
        }
      }
    }
    return res.data;
  });
}

function callForAccount(epoch, name, ...args) {
  if (epoch !== accountUiEpoch) throw new StaleAccountResponse();
  return call(name, ...args);
}

function clearAccountScreen() {
  S.rosterEditor = null; S.timetableTab = "timetable"; rosterEditorRequest += 1;
  clearTimeout(editAutoSaveTimer);
  editAutoSavePending = false;
  settingsAutoSavePending = false;
  editDirtyFields.clear();
  stopCapturePoll();
  clearGoogleDependentState();
  S.draft = { profile: {}, grid: null, bridge: {} };
  S.profileCache = null;
  S.caps = null;
  S.capsOpen = {};
  S.progress = null;
  S.doneShown = "";
  S.applyResults = null;
  S.fieldIssues = {};
  S.attendanceConnection = null;
  S.attendanceConnectionBusy = false;
  S.helperRestartPending = false;
  S.attendanceTransitioning = false;
  S.maps = { calendars: {}, tasklists: {} };
  rosterReadVersion += 1;
  rosterInFlight = "";
  rosterStatus = null;
  rosterContext = "";
}

function reloadAccountProfile() {
  if (!accountProfileNeedsReload) return Promise.resolve(false);
  if (accountProfileReloadPromise) return accountProfileReloadPromise;
  const epoch = accountUiEpoch;
  accountProfileReloadPromise = (async () => {
    const info = await call("get_app_info");
    if (epoch !== accountUiEpoch) return false;
    adoptAppInfo(info);
    accountProfileNeedsReload = false;
    render();
    return true;
  })().finally(() => { accountProfileReloadPromise = null; });
  return accountProfileReloadPromise;
}
async function callWithLocalRecovery(action) {
  // 실제 파일 읽기·저장 재시도는 브리지의 세 번 확인 경계에서 끝난다.
  // 화면이 이미 최종 구조화 안내를 다시 호출하면 시도 횟수와 원인을 왜곡한다.
  return await action();
}

/* ---------- 렌더 유틸 ---------- */
function esc(text) {
  return String(text == null ? "" : text)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
const GWS_OFFER_NOTE_LIMIT = 300;
function safeGwsOfferText(value) {
  // 승인 파일은 Python에서도 검사하지만, 화면은 원격 글을 그대로 코드로 만들지
  // 않는다. 숨은 제어 문자와 글 방향 뒤집기를 걷고 300자로 제한한다.
  const cleaned = String(value == null ? "" : value)
    .replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return Array.from(cleaned).slice(0, GWS_OFFER_NOTE_LIMIT).join("");
}
function safeGwsOfferDate(value) {
  const cleaned = safeGwsOfferText(value);
  return /^\d{4}-\d{2}-\d{2}$/.test(cleaned) ? cleaned : "";
}
function icon(name, cls) {
  return `<svg class="ic ${cls || ""}"><use href="#ic-${name}"/></svg>`;
}
function badge(kind, label) {
  const mark = { g: "✓", y: "!", r: "✕", n: "·" }[kind] || "·";
  return `<span class="badge ${kind}">${mark} ${esc(label)}</span>`;
}
function root() { return document.getElementById("app"); }

function setBanner(kind, text, topic = "") { S.banner = text ? { kind, text, topic } : null; render(); }
function showToast(text) {
  S.toast = text; render();
  setTimeout(() => { S.toast = null; render(); }, 2200);
}
// 문제 안내는 그 요청을 시작한 화면에만 속한다. 다른 화면으로 옮기거나 새 요청을
// 시작하면 이전 안내와 그 안내의 재개 권한도 함께 버린다.
let problemIssueOwner = "";
let problemIssueToken = 0;
let problemIssueResumeAction = "";
function clearProblemIssue(refresh) {
  const hadProblemIssue = Boolean(S.problemIssue);
  problemIssueToken += 1;
  problemIssueOwner = "";
  problemIssueResumeAction = "";
  S.problemIssue = null;
  invalidateIssueResume();
  if (refresh && hadProblemIssue) render();
}
function beginIssueRequest(preserveIssue, actionKey) {
  if (!preserveIssue) clearProblemIssue(true);
  return { screen: screenKey(), token: problemIssueToken, action: String(actionKey || "") };
}
function ownsIssueRequest(request) {
  return !request || (request.screen === screenKey() && request.token === problemIssueToken);
}
function completeIssueRequest(request) {
  if (ownsIssueRequest(request)) clearProblemIssue(true);
}
function clearResolvedReadIssue(operation) {
  if (problemIssueOwner === screenKey() && S.problemIssue?.operation === operation) clearProblemIssue(false);
}
// 문제 화면에 나올 수 있는 버튼은 모두 '단계로 바로 가는' 것뿐이다. 문의·복사 단추는 없다.
const DIRECT_ISSUE_ACTIONS = {
  "google-login": "Google 로그인 설정 열기",
  "chat-permission": "Google Chat 권한 확인",
  "chat-space-list": "학급 단체톡방 다시 확인",
  "attendance-first-setup": "출석부 설정 확인",
  "attachment-download": "첨부파일 상태 다시 확인",
  "inspect-calendar-candidates": "Google Calendar에서 직접 확인",
  "inspect-tasklist-candidates": "Google Tasks에서 직접 확인",
  "attendance-tab": "출결 탭으로",
  "open-download-page": "다운로드 페이지 열기",
  "open-current-attendance": "현재 출석부 열기",
  "open-script-api-settings": "Apps Script API 사용",
  "settings": "설정 열기",
};
const GOOGLE_TARGET_ID_FIELDS = [
  "업무캘린더ID", "학사일정캘린더ID", "업무Tasks목록ID", "담임안내Tasks목록ID",
];
const LIVE_CONNECT_TARGETS = new Set([...GOOGLE_TARGET_ID_FIELDS, "gemini_api_key"]);
function clearDraftGoogleTargetIds(fields = GOOGLE_TARGET_ID_FIELDS) {
  if (!S.draft?.profile) return;
  fields.forEach((field) => { S.draft.profile[field] = ""; });
}
function applyInvalidatedGoogleTargets() {
  clearDraftGoogleTargetIds(S.invalidatedGoogleTargetFields || []);
}
function acknowledgeSavedGoogleTargets(values) {
  for (const field of Object.keys(values)) S.invalidatedGoogleTargetFields?.delete(field);
}
function issueValue(issue, key, fallback = "") {
  const value = issue && typeof issue === "object" ? issue[key] : fallback;
  return value == null ? fallback : value;
}
function directIssueActionHtml(issue) {
  const actions = Array.isArray(issue?.actions) ? issue.actions : [];
  const seen = new Set();
  return actions
    .map((row) => ({ key: String(row?.key || ""), label: String(row?.label || "") }))
    .filter((row) => {
      const allowed = Object.prototype.hasOwnProperty.call(DIRECT_ISSUE_ACTIONS, row.key)
        || /^(select|inspect)-(calendar|tasklist):[^\s:]+$/.test(row.key);
      return allowed && !seen.has(row.key) && (seen.add(row.key), true);
    })
    .map((row) => `<button class="btn-tonal" data-action="issue-direct" data-preserve-issue="true" data-issue-action="${esc(row.key)}">${esc(DIRECT_ISSUE_ACTIONS[row.key] || row.label)}</button>`)
    .join("");
}
function problemPanelHtml(issue) {
  if (!issue || !["needs_user", "failed"].includes(issue.state)) return "";
  const readUncertain = ATTENDANCE_READ_METHODS.has(issue.operation) || issue.operation === "attendance_prepare_status";
  // 교사가 지금 할 수 있는 일만 보여 준다. 진단용 값(코드·횟수·판·식별번호)은
  // 로컬 기록과 개발자 보고에만 남는다 (2026-09-02 사용자 결정).
  const steps = (Array.isArray(issue.steps) ? issue.steps : []).map((s) => String(s || "")).filter(Boolean);
  const reason = issue.state === "needs_user"
    ? issueValue(issue, "message", "")
    : issueValue(issue, "reason", "");
  const stepsHtml = steps.length
    ? `<div class="problem-steps"><b>지금 할 수 있는 일</b><ol>${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>`
    : "";
  const reportStatus = issue.reported === true
    ? "오류 내용을 개발자에게 보냈어요."
    : issue.report_queued === true
      ? "오류 내용을 이 컴퓨터에 저장했어요. 개발자에게 전달됐는지는 아직 확인하지 못했습니다."
      : "";
  return `<section class="problem-panel${readUncertain ? " read-uncertain" : ""}" role="alert">
    <h2>${esc(readUncertain ? "현재 상태를 확인하지 못했어요." : issueValue(issue, "title", "작업을 마치지 못했어요."))}</h2>
    <p class="problem-reason">${esc(reason || "다시 확인해 주세요.")}</p>
    ${stepsHtml}
    <p class="problem-status">${esc(issueValue(issue, "change_status", "작업 결과를 확인하지 못했어요. 같은 작업을 다시 하기 전에 현재 자료를 확인해 주세요."))}</p>
    <div class="problem-actions action-line">${directIssueActionHtml(issue)}</div>
    ${reportStatus ? `<p class="problem-footer">${esc(reportStatus)}</p>` : ""}
  </section>`;
}
function showProblemIssue(error, request) {
  if (!(error instanceof AppIssueError)) return false;
  if (!ownsIssueRequest(request)) return true;
  const issue = error.issue;
  if (!issue || !["needs_user", "failed"].includes(issue.state)) return false;
  if (["ensure_attendance", "attendance_prepare_start"].includes(issue.operation)
      && S.attendance?.state === "installing") {
    S.attendance = { ...S.attendance, state: "failed", failed_service: "setup" };
  }
  S.problemIssue = issue;
  problemIssueOwner = screenKey();
  problemIssueResumeAction = String(request?.action || "");
  problemIssueToken += 1;
  S.banner = null;
  render();
  return true;
}
function handleCaughtError(error, request) {
  if (error instanceof StaleAccountResponse) return true;
  if (showProblemIssue(error, request)) return true;
  if (!request || ownsIssueRequest(request)) {
    const topic = ["attendance-script-update-resolve", "attendance-script-dialog-confirm"].includes(request?.action)
      ? "attendance-script-update" : "";
    setBanner("error", error?.message || "작업을 마치지 못했어요.", topic);
  }
  return false;
}
function bannerHtml() {
  const localSettings = S.google?.local_settings_error
    ? `<div class="banner error"><span><b>이 컴퓨터의 설정을 확인해 주세요.</b> ${esc(S.google.local_settings_error)}</span>${settingsRefreshButtonHtml()}</div>`
    : "";
  // 기록 폴더를 여는 단추는 두지 않는다. 폴더만 열릴 뿐 거기서 뭘 하라는 안내가 없고,
  // 그 폴더에 보이는 파일은 Gemini API key가 든 settings.json이다 (2026-07-30 사용자 결정).
  const banner = S.banner
    ? `<div class="banner ${S.banner.kind}"><span>${esc(S.banner.text)}</span></div>`
    : "";
  const fallback = S.externalLinkFallback
    ? `<div class="external-link-fallback">
        <b>브라우저를 열지 못했어요</b>
        <span>아래 주소를 복사해 브라우저 주소창에 붙여넣어 주세요.</span>
        <div class="linkrow"><span class="url" title="${esc(S.externalLinkFallback.url)}">${esc(S.externalLinkFallback.url)}</span>
          <span class="acts"><button class="btn-quiet" data-action="link-copy" data-url="${esc(S.externalLinkFallback.url)}">링크 복사</button></span>
        </div>
      </div>`
    : "";
  const currentIssue = problemIssueOwner === screenKey() ? S.problemIssue : null;
  return localSettings + (isClassSpaceIssue(currentIssue) ? "" : problemPanelHtml(currentIssue)) + networkWaitNoticeHtml() + banner + fallback;
}
function toastHtml() { return S.toast ? `<div class="toast">${esc(S.toast)}</div>` : ""; }

/* ---------- 입력칸 문제(빨간 표시) 공통 상태 ---------- */
const FIELD_MESSAGES = {
  "선생님이름": "선생님 이름을 입력해 주세요.",
  "학교명": "학교 이름을 입력해 주세요.",
  "학교급": "학교급을 골라 주세요.",
  "담임여부": "담임 여부를 골라 주세요.",
  "담임학년": "담임 학년을 입력해 주세요.",
  "담임반": "담임 반을 입력해 주세요.",
  "업무캘린더ID": "개인 업무 일정을 등록할 Calendar를 골라 주세요.",
  "학사일정캘린더ID": "학사 일정을 등록할 Calendar를 골라 주세요.",
  "업무Tasks목록ID": "개인 업무를 등록할 Tasks 목록을 골라 주세요.",
  "담임안내Tasks목록ID": "조종례 전달사항을 등록할 Tasks 목록을 골라 주세요.",
  "gemini_api_key": "Gemini 연결 키가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
  "google-login": "Google 로그인이 필요해요. 설정에서 Google 계정으로 로그인해 주세요.",
};
// 사용자가 직접 고치는 입력칸의 name — 편집 중엔 저장된 점검 대신 현재 입력을 본다.
const EDITABLE_TARGETS = new Set([
  "선생님이름", "학교명", "학교급", "담임여부", "담임학년", "담임반",
  "출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간",
  "월요일마지막교시", "화요일마지막교시", "수요일마지막교시", "목요일마지막교시", "금요일마지막교시",
  "업무캘린더ID", "학사일정캘린더ID", "업무Tasks목록ID", "담임안내Tasks목록ID",
  "업무캘린더이름", "학사일정캘린더이름", "업무Tasks목록이름", "담임안내Tasks목록이름",
  "gemini_api_key", "hotkey", "brity_download_dir",
]);
function issue(key, target, message, tab) {
  return { key, target, message, tab: tab || "" };
}
function setFieldIssues(rows) {
  S.fieldIssues = Object.fromEntries(rows.map((row) => [row.target, row]));
}
function replaceEditableIssues(rows) {
  const kept = Object.values(S.fieldIssues || {}).filter((row) => !EDITABLE_TARGETS.has(row.target));
  setFieldIssues(kept.concat(rows));
}
function firstIssueMessage(rows) {
  return rows.length ? rows[0].message : "";
}
function fieldError(name) {
  if (editingCard() === "connect" && LIVE_CONNECT_TARGETS.has(name)) {
    return connectIssues().find((row) => row.target === name && !row.pending)?.message || "";
  }
  const row = (S.fieldIssues || {})[name];
  return row && !row.pending ? row.message : "";
}
function fieldNoteHtml(name) {
  const error = fieldError(name);
  if (error) return `<span class="field-error">${esc(error)}</span>`;
  const pending = editingCard() === "connect" && LIVE_CONNECT_TARGETS.has(name)
    ? connectIssues().find((row) => row.target === name && row.pending) : null;
  return pending ? `<span class="hint">${esc(pending.message)}</span>` : "";
}

/* ---------- 입력 ---------- */
function field(name, label, value, opts) {
  const o = opts || {};
  const error = o.error || fieldError(name);
  const hint = !error && o.hint ? `<span class="hint">${esc(o.hint)}</span>` : "";
  const type = o.type || "text";
  const invalid = error ? ' aria-invalid="true"' : "";
  const placeholder = o.placeholder ? ` placeholder="${esc(o.placeholder)}"` : "";
  const input = `<input name="${esc(name)}" type="${esc(type)}" value="${esc(value == null ? "" : value)}"${placeholder}${invalid}>`;
  const note = error ? `<span class="field-error">${esc(error)}</span>` : hint;
  return `<div class="field${error ? " has-error" : ""}"><label>${esc(label)}</label>${input}${note}</div>`;
}
function readFields(names) {
  const out = {};
  for (const name of names) {
    const el = document.querySelector(`[name="${name}"]`);
    if (el) out[name] = el.value.trim();
  }
  return out;
}

/* ---------- 표 레이아웃: 왼쪽 설명 th · 오른쪽 입력 td ---------- */
function fieldInner(name, value, opts) {
  const o = opts || {};
  const error = o.error || fieldError(name);
  const hint = !error && o.hint ? `<span class="hint">${esc(o.hint)}</span>` : "";
  const type = o.type || "text";
  const invalid = error ? ' aria-invalid="true"' : "";
  const placeholder = o.placeholder ? ` placeholder="${esc(o.placeholder)}"` : "";
  const disabled = o.disabled ? " disabled" : "";
  const input = `<input name="${esc(name)}" type="${esc(type)}" value="${esc(value == null ? "" : value)}"${placeholder}${invalid}${disabled}>`;
  const note = error ? `<span class="field-error">${esc(error)}</span>` : hint;
  // 가려진 칸에는 눈 단추를 붙인다 — 붙여넣은 값이 맞는지 눈으로 확인할 방법이 있어야 한다.
  const box = type === "password"
    ? `<div class="field-with-eye">${input}<button type="button" class="field-eye" data-action="field-eye" data-field="${esc(name)}" aria-label="입력한 값 보기" title="보기">${icon("eye", "small")}</button></div>`
    : input;
  return `<div class="field${error ? " has-error" : ""}">${box}${note}</div>`;
}
function fieldRow(name, label, value, opts) {
  return `<tr><th scope="row">${esc(label)}</th><td>${fieldInner(name, value, opts)}</td></tr>`;
}
function rawRow(label, innerHtml) {
  return `<tr><th scope="row">${esc(label)}</th><td>${innerHtml}</td></tr>`;
}
function formTable(rowsHtml) {
  return `<table class="form-table">${rowsHtml}</table>`;
}

/* ---------- 행동 위임 ---------- */
const actions = {};
function bindActions(map) { Object.assign(actions, map); }
async function busyWrap(el, fn) {
  // busy 문구가 있는 버튼만 문구를 바꾼다 — 아이콘(svg) 든 버튼의 내용을 지우지 않기 위해.
  const hasBusyText = Boolean(el.dataset.busyText);
  const original = hasBusyText ? el.textContent : "";
  el.disabled = true;
  if (hasBusyText) el.textContent = el.dataset.busyText;
  const preserveIssue = el.dataset.preserveIssue === "true"
    || (el.dataset.action === "back-home" && Boolean(settingsAutoSavePromise || editAutoSavePromise));
  let request = beginIssueRequest(true, el.dataset.action);
  try {
    if (S.mode === "edit" && S.edit === "settings" && settingsAutoSavePromise
        && ["attachment-folder-choose", "hk-record"].includes(el.dataset.action)) {
      const owner = screenKey(), epoch = accountUiEpoch;
      if (!(await settingsAutoSavePromise) || owner !== screenKey() || epoch !== accountUiEpoch) return false;
    }
    request = beginIssueRequest(preserveIssue, el.dataset.action);
    const result = await fn(el, request);
    if (!preserveIssue && result !== false) completeIssueRequest(request);
  }
  catch (error) {
    handleCaughtError(error, request);
  }
  finally { el.disabled = false; if (hasBusyText) el.textContent = original; }
}
document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-action]");
  if (!el) return;
  const fn = actions[el.dataset.action];
  if (fn) busyWrap(el, fn);
});

/* ---------- 링크 3종 세트: 자동 열기 + 주소 노출 + 복사 ---------- */
function copyText(text) {
  const legacy = () => {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    return ok;
  };
  const done = () => showToast("링크를 복사했어요");
  const fail = () => setBanner("warn", "복사가 안 됐어요 — 주소를 드래그해서 복사해 주세요");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => (legacy() ? done() : fail()));
  } else if (legacy()) { done(); } else { fail(); }
}
function linkRow(url) {
  return `<div class="linkrow"><span class="url" title="${esc(url)}">${esc(url)}</span>` +
    `<span class="acts">` +
    `<button class="btn-tonal" data-action="link-open" data-url="${esc(url)}">${icon("external-link", "small blue")} 열기</button>` +
    `<button class="btn-quiet" data-action="link-copy" data-url="${esc(url)}">링크 복사</button>` +
    `</span></div>`;
}
bindActions({
  "issue-direct": async (el, request) => {
    const key = String(el.dataset.issueAction || "");
    if (key === "open-download-page") { await call("open_url", LATEST_RELEASE_URL); return; }
    if (key === "open-script-api-settings") { await openAttendanceScriptSettings(); return; }
    if (key === "settings") { await actions["goto-settings"](); return; }
    if (key === "attendance-tab") {
      if (S.mode === "wizard") { await goStepAsync(8); } else { await openCard("connect"); }
      S.connectTab = "attendance"; S.attendance = null; S.chatStatus = null;
      render();
      return;
    }
    if (key === "open-current-attendance") { await actions["attendance-open"](); return; }
    if (await inspectGoogleTargetCandidate(S.problemIssue, key, request)) return;
    if (await openGoogleCandidateInspector(key)) return;
    const selection = await applyGoogleTargetSelection(S.problemIssue, key, request);
    if (selection) {
      if (selection === "selected") await resumeGoogleTargetSelection();
      return;
    }
    await dispatchIssueResume(S.problemIssue, key);
  },
  "link-open": async (el) => {
    const url = String(el.dataset.url || "");
    try {
      await call("open_url", url);
      if (S.externalLinkFallback) {
        S.externalLinkFallback = null;
        render();
      }
    } catch (_error) {
      S.externalLinkFallback = { url };
      render();
    }
  },
  "link-copy": (el) => { copyText(el.dataset.url); },
  "attendance-open": async () => {
    const context = chatReadContext(), owner = screenKey();
    try {
      const expected = attendanceExpectedWorkbookOpen(S.attendance);
      if (!expected) throw new Error("현재 출석부 연결을 다시 확인한 뒤 열어 주세요.");
      await call("open_current_attendance", expected, false);
      if (context !== chatReadContext() || owner !== screenKey()) return;
      if (S.mode === "edit" && S.edit === "connect" && S.connectTab === "attendance") startChatConnectPoll(true);
    } catch (error) {
      if (context === chatReadContext() && owner === screenKey()) setBanner("warn", error.message || "출석부를 열지 못했어요. 다시 눌러 주세요.");
    }
  },
  "show-workspace-guide": () => { S.workspaceGuideOpen = true; render(); },
  "close-workspace-guide": () => { S.workspaceGuideOpen = false; render(); },
});

/* ---------- 마법사 골격 ---------- */
const validators = {};   // step -> () => "" | "막는 이유"
const stepBodies = {};   // step -> () => html   (T8~T10이 채움)

function railHtml() {
  const reached = S.maxStep || S.step;
  const rows = WIZARD_STEPS.map((title, index) => {
    const n = index + 1;
    const cls = n < S.step ? "done" : n === S.step ? "active" : "";
    const mark = n < S.step ? "✓" : String(n);
    // 한 번이라도 지나간 단계는 앞뒤 어디로든 바로 이동할 수 있다.
    const skipped = n === 6 && !isHomeroomTeacher();
    const attr = skipped ? " disabled" : n !== S.step && n <= reached ? ` data-action="go-step" data-step="${n}"` : "";
    const current = n === S.step ? ' aria-current="step"' : "";
    return `<button class="step ${cls}"${attr}${current}><span class="n">${mark}</span><span class="step-label">${esc(title)}</span></button>`;
  }).join("");
  const info = S.info || { version: "", branding: { name: "" } };
  return `<div class="rail">${rows}` +
    `<div class="rail-tail">${esc(info.branding.name)} v${esc(info.version)}</div></div>`;
}

/* ---------- 로그인 상시 감시 — 끊어지면 끊어졌다고 말한다 ---------- */
const LOGIN_WATCH_INTERVAL_MS = 3 * 60 * 1000;  // 검증 전 앱은 토큰이 수시로 회수된다
let loginWatchTimer = null;
let loginWatchChecking = false;
let googleLoginEpoch = 0;
let googleContextVersion = 0;
let linkListsReadVersion = 0;
let targetStatusReadVersion = 0;
function verifiedGoogleAccount(status) {
  return status?.logged_in && !googleAuthCheckFailed(status)
    ? String(status.user || "").trim().toLowerCase() : "";
}
function googleReadContext() {
  return JSON.stringify([screenKey(), googleContextVersion, googleLoginEpoch, verifiedGoogleAccount(S.google)]);
}
function adoptGoogleStatus(status) {
  const previousAuthorizationMessage = S.google?.authorization_detail;
  // An unknown check cannot replace the last confirmed account identity.
  const previousAccount = S.lastVerifiedGoogleAccount || verifiedGoogleAccount(S.google);
  const nextAccount = verifiedGoogleAccount(status);
  const accountChanged = Boolean(previousAccount && nextAccount && previousAccount !== nextAccount);
  const changed = S.google && googleStatusKey(S.google) !== googleStatusKey(status);
  const checkUnavailable = googleAuthCheckFailed(status);
  const sameAccountRechecked = googleAuthCheckFailed(S.google) && nextAccount === previousAccount;
  S.lastVerifiedGoogleAccount = nextAccount || previousAccount;
  if (accountChanged && status?.local_settings_error) {
    // Failed local activation may leave the old wire token; discard its screen anyway.
    accountUiEpoch += 1;
    clearAccountScreen();
    accountProfileNeedsReload = true;
  }
  if (accountChanged || (changed && !checkUnavailable && !sameAccountRechecked)) {
    clearGoogleDependentState({ preserveResume: true, accountChanged });
  } else if (checkUnavailable) {
    // A failed check cannot erase a workbook or a completed setup. Keep its
    // identity, while requiring an actual successful read to mark it current.
    S.firstSetupReadState = "unavailable";
    if (S.chatStatus && typeof S.chatStatus === "object") {
      S.chatStatus = { ...S.chatStatus, connected: null, read_failed: true };
    }
  }
  S.google = status;
  if (isGoogleReady(status)) {
    delete S.fieldIssues["google-login"];
    const oldLoginWarnings = [FIELD_MESSAGES["google-login"], GOEDU_REQUIRED_MESSAGE, GOOGLE_AUTH_CHECK_MESSAGE, previousAuthorizationMessage,
      "Google 로그인이 풀렸어요. 설정에서 다시 로그인해 주세요."];
    if (S.banner && (S.banner.topic === "google-login" || oldLoginWarnings.includes(S.banner.text))) S.banner = null;
    if (["google_status", "gws_login_start", "gws_login_status"].includes(S.problemIssue?.operation)) clearProblemIssue();
  }
  if (accountProfileNeedsReload) reloadAccountProfile().catch(() => {});
}
function googleStatusKey(status) {
  if (!status) return "";
  return [status.login_state || "", status.logged_in ? "1" : "0", status.user || "", status.account_allowed === true ? "1" : "0", status.authorization_state || "", status.authorization_reason || ""].join("|");
}
function clearGoogleDependentState(options) {
  const preserveResume = options?.preserveResume === true;
  attendanceConnectionRequestToken += 1;
  S.attendanceConnection = null;
  S.attendanceConnectionBusy = false;
  stopAttendanceBoundaryCheck();
  if (S.banner?.topic === "attendance-gate") S.banner = null;
  attendanceAccountAuthorizationVersion += 1;
  S.attendanceAccountAuthorizing = false;
  rosterAuthorizationRetry = null;
  googleContextVersion += 1;
  linkListsReadVersion += 1;
  targetStatusReadVersion += 1;
  attendanceStatusReadVersion += 1;
  checksReadVersion += 1;
  checksRetry.inflight = false;
  S.linkLoading = false;
  S.listReads = {};
  S.attendanceLoading = false;
  S.attendanceReadFailed = false;
  S.googleTargetStatuses = {};
  checksRetry.lastGood = [];
  checksRetry.dirtyCards.clear();
  clearAttendanceScriptDialogState();
  stopAttendancePermissionReturn();
  stopAttendancePreparePoll();
  stopChatConnectPoll();
  chatStatusReadVersion += 1;
  chatSpacesReadVersion += 1;
  S.chatSpaces = undefined;
  S.chatSpaceName = undefined;
  S.chatSpacesContext = "";
  S.chatStatusContext = "";
  S.chatSpacesError = false;
  S.chatSpacesLoading = false;
  S.spaceCreate = null;
  S.spaceDraftName = "";
  S.attendance = null;
  S.attendanceScriptUpdate = null;
  S.firstSetupDone = false; S.firstSetupReason = "";   // 계정이 바뀌면 다른 시트의 완료 표시일 수 있다
  S.firstSetupReadState = null;
  S.firstSetupConnectionCode = "";
  S.attendanceStaleNotice = false;
  S.chatStatus = null;
  S.checks = [];
  S.lists = { calendars: [], tasklists: [] };
  S.listsLoaded = false;
  S.listsError = false;
  S.verifiedGoogleTargetChoices = {};
  S.pendingGoogleTarget = null;
  if (options?.accountChanged) {
    S.invalidatedGoogleTargetFields = new Set(GOOGLE_TARGET_ID_FIELDS);
    applyInvalidatedGoogleTargets();
    GOOGLE_TARGET_ID_FIELDS.forEach((field) => {
      editDirtyFields.delete(field);
      editDirtyFields.delete(field.replace(/ID$/, "이름"));
    });
  }
  if (!preserveResume) {
    googleLoginResumeGeneration += 1;
    googleLoginResumeContext = null;
    S.problemIssue = null;
    problemIssueOwner = "";
    problemIssueResumeAction = "";
    invalidateIssueResume();
  }
}
async function checkGoogleOnReturn() {
  if (S.login || loginWatchChecking || hasCurrentSettingsStatusRequest()) return;
  loginWatchChecking = true;
  const epoch = googleLoginEpoch;
  try {
    const displayState = () => JSON.stringify([S.google, S.banner, S.problemIssue, S.fieldIssues["google-login"]]);
    const beforeDisplay = displayState();
    const before = S.google;
    const status = await call("google_status");
    if (epoch !== googleLoginEpoch || S.login || hasCurrentSettingsStatusRequest()) return;
    adoptGoogleStatus(status);
    if (googleAuthCheckFailed(status)) { render(); return; }
    if (before?.logged_in && !status.logged_in) {
      setBanner("warn", "Google 로그인이 풀렸어요. 설정에서 다시 로그인해 주세요.");
    } else if (beforeDisplay !== displayState()) {
      render();
    }
  } catch (error) { /* A failed actual check is marked unknown by call(); never infer logout. */ }
  finally { loginWatchChecking = false; }
}
function startLoginWatch() {
  if (loginWatchTimer) return;
  const tick = async () => {
    try { await checkGoogleOnReturn(); }
    finally { loginWatchTimer = setTimeout(tick, LOGIN_WATCH_INTERVAL_MS); }
  };
  loginWatchTimer = setTimeout(tick, 0);
}
window.addEventListener("focus", () => {
  if (!window.pywebview?.api) return;
  if (S.login?.logging_out) return;
  if (S.login) { if (!loginPollRunning) pollLogin(0); }
  else checkGoogleOnReturn();
});

function stepStub(n) {
  return `<h1>${esc(WIZARD_STEPS[n - 1])}</h1><p class="sub">이 화면을 불러오지 못했어요. 프로그램을 다시 열어 주세요.</p>`;
}

let wizardNextPending = null;
function hasPendingWizardNext() {
  return wizardNextPending?.screen === screenKey() && wizardNextPending.epoch === accountUiEpoch;
}
function wizardFootHtml() {
  const back = S.step > 1 ? `<button class="btn-prev" data-action="go-prev">${icon("chevron-left", "small")} 이전</button>` : "<span></span>";
  const nextLabel = S.step === WIZARD_STEPS.length ? ""
    : S.step === 1 ? "준비됐어요, 시작하기"
      : hasPendingWizardNext() ? "확인 중…" : "다음";
  const nextLocked = (S.step === 6 && !rosterSavedForNext()) || hasPendingWizardNext() || (S.step === 2 && (Boolean(S.login) || !isGoogleReady(S.google) || Boolean(S.google?.local_settings_error)))
    || (attendanceUiEnabled() && S.step === 8 && S.connectTab === "attendance" && !attendanceWizardGateOpen());
  const disabled = nextLocked ? " disabled" : "";
  const nextClass = "btn";
  const next = nextLabel ? `<button class="${nextClass}" data-action="go-next" data-busy-text="확인 중…"${disabled}>${nextLabel}</button>` : "";
  return `<div class="foot">${back}${next}</div>`;
}

function renderWizard() {
  const body = (stepBodies[S.step] || (() => stepStub(S.step)))();
  const foot = S.step === WIZARD_STEPS.length ? "" : wizardFootHtml();
  const currentBody = document.querySelector(".shell > .body > .body-inner");
  const currentFoot = document.querySelector(".shell > .body > .foot");
  if (lastScreenKey === screenKey() && currentBody && currentFoot && foot) {
    // A background result may arrive between pointer-down and pointer-up. Keep
    // unchanged navigation buttons attached so that click is not discarded.
    currentBody.innerHTML = `<div class="page">${bannerHtml()}${body}</div>`;
    document.querySelector(".shell > .rail").outerHTML = railHtml();
    const template = document.createElement("template");
    template.innerHTML = foot;
    const nextFoot = template.content.firstElementChild;
    if (!currentFoot.isEqualNode(nextFoot)) currentFoot.replaceWith(nextFoot);
    root().querySelector(":scope > .toast")?.remove();
    root().insertAdjacentHTML("beforeend", toastHtml());
    return;
  }
  root().innerHTML =
    `<div class="shell">${railHtml()}` +
    `<div class="body"><div class="body-inner"><div class="page">${bannerHtml()}${body}</div></div>${foot}</div></div>` + toastHtml();
}

function currentState() {
  return { version: 3, completed: false, step: S.step, max_step: S.maxStep, draft: S.draft };
}
function saveDraft() { return call("save_setup_state", currentState()); }

/* 마법사 7단계 완주 게이트 — 출결 준비 완료 + 시트 처음 설정 완료 전에는 7단계를 넘어갈 수 없다.
   출결 탭 [다음], AI 탭 [다음](goNextAsync 낙하), 왼쪽 단계 목록(go-step) 등 7단계 경계를
   넘어가는 모든 길이 이 판정 하나를 지난다 — 준비 스레드와 9단계 apply_all의 겹침을 막는
   유일한 방어라 느슨하게 만들면 안 된다. */
function attendanceWizardGateOpen() {
  const chat = S.chatStatus;
  return Boolean(!S.classSpaceSaving && attendanceSheetSetupDone() && chat && chat.connected && !chat.read_failed
    && S.chatStatusContext === chatReadContext()
    && ((S.draft.profile["담임여부"] || S.profileCache?.["담임여부"]) === "아니오"
      || (classRoomReadiness() === "ready" && attendanceRosterReady())));
}
function attendanceRosterReady() {
  return rosterContext === chatReadContext() && rosterStatus?.state === "ready"
    && !(S.rosterEditor?.context === rosterEditorContext() && S.rosterEditor.pending)
    && Number.isInteger(rosterStatus.count) && rosterStatus.count > 0;
}
function attendanceSheetSetupDone() {
  return Boolean(
    S.attendance
    && S.attendance.state === "ready"
    && S.firstSetupDone
    && !["checking", "unavailable"].includes(S.firstSetupReadState)
    && S.firstSetupConnectionCode
    && S.firstSetupConnectionCode === String(S.attendance.connection_code || "").trim().toUpperCase()
  );
}
function firstSetupCodeFromValue(value) {
  const match = String(value || "").trim().match(/^(TM-[0-9A-F]{6}-[0-9A-F]{6})(?:\s|$)/i);
  return match ? match[1].toUpperCase() : "";
}
function applyFirstSetupRead(first) {
  if (typeof first?.done !== "boolean") {
    S.firstSetupReadState = "unavailable";
    return;
  }
  const code = firstSetupCodeFromValue(first.value);
  S.firstSetupReason = first.reason || "";
  S.firstSetupConnectionCode = code;
  S.firstSetupDone = Boolean(first.done && code && code === String(S.attendance?.connection_code || "").trim().toUpperCase());
  S.firstSetupReadState = "ready";
  clearResolvedReadIssue("attendance_first_setup_status");
  clearResolvedAttendanceGateBanner();
}
function firstSetupProblemMessage() {
  return {
    account_mismatch: "프로그램에 연결한 계정과 출석부 설정 계정이 달라요. 출석부에 설정한 Google 계정으로 로그인해 주세요.",
    account_missing: "출석부에 연결한 계정을 확인하지 못했어요. Google 로그인 상태를 확인해 주세요.",
    connection_mismatch: "현재 출석부와 저장된 확인 표시가 달라요. 설정을 다시 실행하지 말고 연결 상태를 확인해 주세요.",
    connection_invalid: "저장된 출석부 연결을 확인하지 못했어요. 현재 출결 연결을 확인해 주세요.",
    marker_invalid: "출석부의 설정 완료 표시를 확인할 수 없어요. 설정을 다시 실행하지 말고 연결 상태를 확인해 주세요.",
  }[S.firstSetupReason] || "시트의 처음 설정이 끝나야 다음으로 갈 수 있어요";
}
async function refreshAttendanceWizardGate() {
  // 준비 중이라고 이미 확인한 화면에서 단계 건너뛰기를 누르면, 일반 상태 조회가
  // 먼저 완성된 옛 연결 기록을 돌려주더라도 준비 스레드가 끝난 것으로 보지 않는다.
  if (S.attendance?.state === "installing") return false;
  let context = chatReadContext();
  const owner = screenKey();
  const current = () => context === chatReadContext() && owner === screenKey();
  // 다른 창이 현재 출석부를 바꿨을 수 있으므로 7단계를 넘기 직전에 실제 상태와
  // 그 Sheet에 묶인 완료 표시를 다시 읽는다. 확인 중에는 다음 단계를 막되,
  // 읽기 실패를 이미 마친 설정의 취소로 취급하지 않는다.
  S.firstSetupReadState = "checking";
  let attendanceChecked = false;
  try {
    const attendance = await call("attendance_status");
    if (!current()) return false;
    attendanceChecked = true;
    S.attendanceReadFailed = false;
    S.attendance = attendance;
    context = chatReadContext();
    if (!S.attendance || S.attendance.state !== "ready") return false;
    const first = await call("attendance_first_setup_status");
    if (!current()) return false;
    applyFirstSetupRead(first);
    await loadChatStatus(true);
    if (current() && S.chatStatus?.connected && !S.chatStatus.read_failed && isHomeroomTeacher()) await loadChatSpaces(true);
    if (!current()) return false;
    await loadAttendanceRosterStatus(true);
  } catch (_error) {
    if (!current()) return false;
    if (!attendanceChecked) S.attendanceReadFailed = true;
    S.firstSetupReadState = "unavailable";
  }
  return attendanceWizardGateOpen();
}
function attendanceWizardGateMessage() {
  const state = S.attendance ? S.attendance.state : "";
  if (state === "connection-repair-required") return "출결 탭에서 저장된 출석부의 연결을 확인해 주세요.";
  if (state === "installing") return "출결 준비가 끝나야 다음으로 갈 수 있어요";
  if (state === "initial-setup-required") return "출석부의 [설정하러 가기]에서 처음 설정을 마쳐 주세요.";
  if (state === "ready") {
    if (["checking", "unavailable"].includes(S.firstSetupReadState)) return "출석부의 설정 완료 여부를 확인해야 해요. 설정을 다시 실행할 필요는 없어요.";
    if (!attendanceSheetSetupDone()) return firstSetupProblemMessage();
    if (!S.chatStatus || S.chatStatus === "loading" || S.chatStatus.read_failed || typeof S.chatStatus.connected !== "boolean") return "Google Chat 연결 상태를 확인해야 해요. 잠시 뒤 다시 확인해 주세요.";
    if (!S.chatStatus?.connected) return "Google Chat의 [연결(권한 승인)하러 가기]에서 권한 승인을 마쳐 주세요.";
    if (isHomeroomTeacher() && classRoomReadiness() !== "ready") return classRoomReadinessMessage();
    if (S.rosterEditor?.context === rosterEditorContext() && S.rosterEditor.pending) return S.rosterEditor.detail || "저장한 학생명단을 출석부에 반영하고 있어요.";
    if (!rosterStatus || rosterContext !== chatReadContext() || ["checking", "unavailable"].includes(rosterStatus.state)) return "학생명단을 아직 확인하지 못했어요. 학생명단 화면에서 저장 상태를 확인해 주세요.";
    return "학생명단 화면에서 번호·이름·이메일을 입력하고 [명단 저장]을 눌러 주세요.";
  }
  if (state === "failed") return "출결 준비가 실패했어요. 출결 탭에서 [다시 시도]를 눌러 주세요.";
  return "Brity 메신저 탭에서 [다음]을 누르면 여기에서 준비가 시작돼요.";
}
function setAttendanceGateBanner(message = attendanceWizardGateMessage()) {
  S.banner = { kind: "warn", text: message, topic: "attendance-gate",
    context: chatReadContext(), owner: screenKey(),
    reason: !attendanceSheetSetupDone() ? "first-setup" : "remaining-setup" };
  render();
}
function clearResolvedAttendanceGateBanner() {
  const banner = S.banner;
  if (banner?.topic !== "attendance-gate") return;
  if (banner.context !== chatReadContext() || banner.owner !== screenKey()
      || (banner.reason === "first-setup" && attendanceSheetSetupDone())
      || attendanceWizardGateOpen()) S.banner = null;
}
async function goStepAsync(n) {
  const owner = screenKey(), epoch = accountUiEpoch;
  const current = () => owner === screenKey() && epoch === accountUiEpoch;
  let target = Math.max(1, Math.min(WIZARD_STEPS.length, n));
  if (target === 6 && !isHomeroomTeacher()) target = S.step > 6 ? 5 : 7;
  if (attendanceUiEnabled() && S.mode === "wizard" && S.step <= 8 && target > 8 && !(await refreshAttendanceWizardGate())) {
    if (!current()) return;
    setAttendanceGateBanner();
    return;
  }
  if (!current()) return;
  await stopHotkeyRecording();
  if (!current()) return;
  if (S.connectTab === "attendance") clearAttendanceScriptDialogState();
  S.banner = null;
  S.step = target;
  S.maxStep = Math.max(S.maxStep || 1, S.step);
  if (attendanceUiEnabled() && S.mode === "wizard" && S.step === 8 && S.connectTab === "attendance") {
    // 단계 목록으로 출결 탭에 곧장 돌아와도 진행 표시와 완료 자동 확인이 다시 돈다.
    startAttendancePreparePoll();
  }
  await saveDraft();
  render();
}
async function goNextAsync() {
  // A status panel can repaint the footer while this operation is awaiting Google.
  // Keep the pending action in state, rather than only on the replaced button.
  if (hasPendingWizardNext()) return;
  const pending = { screen: screenKey(), epoch: accountUiEpoch };
  wizardNextPending = pending;
  try { return await advanceWizardNext(); }
  finally {
    if (wizardNextPending === pending) {
      const current = hasPendingWizardNext();
      wizardNextPending = null;
      if (current) render();
    }
  }
}
async function advanceWizardNext() {
  const owner = screenKey(), epoch = accountUiEpoch;
  const mode = S.mode, step = S.step, edit = S.edit;
  const current = () => owner === screenKey() && epoch === accountUiEpoch;
  const validate = validators[S.step];
  const problem = validate ? await validate() : "";
  if (problem) {
    // Validation may intentionally reveal the messenger tab's offending field.
    if (epoch === accountUiEpoch && mode === S.mode && step === S.step && edit === S.edit) setBanner("warn", problem);
    return;
  }
  if (!current()) return;
  if (S.step === 8 && S.connectTab === "messenger") {
    // Persist the screen before the background worker takes the account lock.
    await saveDraft();
    if (!current()) return;
    // 공개용은 출결 안내만 보여 주고, 별도 시험 설치본만 기존 준비 흐름을 실행한다.
    let startReply = null;
    if (attendanceUiEnabled()) {
      startReply = await call("attendance_prepare_start", S.draft.profile, S.draft.grid, S.draft.bridge);
      if (!current()) return;
      if (!startReply.started && !startReply.status) { setBanner("warn", startReply.reason); return; }
      S.banner = null;  // 이전에 띄운 게이트 안내가 남아 있으면 걷는다 (검토 C2)
    }
    S.connectTab = "attendance";
    S.chatStatus = null;
    S.attendance = startReply?.status || null;
    if (attendanceUiEnabled() && startReply?.started) startAttendancePreparePoll();
    render();
    return;
  }
  if (S.step === 8 && S.connectTab === "attendance") {
    if (attendanceUiEnabled() && S.mode === "wizard" && !(await refreshAttendanceWizardGate())) {
      if (!current()) return;
      // 게이트 판정과 문구는 goStepAsync의 7단계 경계와 같은 함수 하나를 쓴다 (검토 C1).
      setAttendanceGateBanner();
      return;
    }
    if (!current()) return;
    await saveDraft();
    if (!current()) return;
    S.banner = null;  // 게이트 통과 — 남은 안내 배너를 걷고 다음 탭으로 (검토 C2)
    stopAttendancePreparePoll();
    clearAttendanceScriptDialogState();
    S.connectTab = "ai";
    S.aiTools = null;
    S.aiInstall = null;
    stopChatConnectPoll();
    render();
    return;
  }
  await goStepAsync(S.step + 1);
}

bindActions({
  "go-prev": () => {
    if (S.step === 8 && S.connectTab === "ai") {
      S.connectTab = "attendance";
      S.chatStatus = null;
      S.attendance = null;
      if (attendanceUiEnabled()) startAttendancePreparePoll();
      render();
      return;
    }
    if (S.step === 8 && S.connectTab === "attendance") {
      clearAttendanceScriptDialogState();
      stopAttendancePreparePoll();
      S.connectTab = "messenger";
      stopChatConnectPoll();
      render();
      return;
    }
    goStepAsync(S.step - 1);
  },
  "go-next": () => goNextAsync(),
  "go-step": async (el) => {
    const target = Number(el.dataset.step);
    if (target > S.step) {
      // 앞으로 건너뛸 때도 지금 화면의 입력은 검증하고 간다.
      // 7단계 경계를 넘는 건너뛰기는 goStepAsync 안의 완주 게이트가 막는다 (검토 C1).
      const validate = validators[S.step];
      const problem = validate ? await validate() : "";
      if (problem) { setBanner("warn", problem); return; }
    }
    goStepAsync(target);
  },
});

/* ---------- 1단계: 시작하기 ---------- */
stepBodies[1] = function stepStart() {
  const linkButton = (url, label) =>
    `<button class="text-link" data-action="link-open" data-url="${esc(url)}">${esc(label)}</button>`;
  const prepRow = (number, title, detail, buttonHtml) => `
    <div class="prep-row">
      <span class="prep-num">${number}</span>
      <span class="prep-copy"><b>${esc(title)}</b><span>${esc(detail)}</span></span>
      ${buttonHtml}
    </div>`;
  return `
    <p class="start-eyebrow">처음 설치하는 선생님</p>
    <h1>Google 계정을 준비해 주세요</h1>
    <p class="sub start-lede">학교 계정이나 개인 Gmail 등 Google에 로그인할 수 있는 계정으로 시작할 수 있어요. 이미 Google 계정이 있으면 아래 가입 없이 다음 단계로 진행하세요. 경기도교육청 학교 계정을 새로 만들려면 아래 순서를 따라 주세요.</p>
    <div class="prep-list">
      ${prepRow(1, "교육디지털원패스 교직원 회원가입", "https://edupass.neisplus.kr/에서 교직원 계정을 준비합니다.", linkButton("https://edupass.neisplus.kr/", "교육디지털원패스 열기"))}
      ${prepRow(2, "경기도교육청 교육용 클라우드 지원시스템 가입", "https://www.goedu.kr/에서 디지털원패스 계정을 통해 교직원 가입을 마칩니다.", linkButton("https://www.goedu.kr/", "공식 사이트 열기"))}
      ${prepRow(3, "경기도교육청 클라우드 지원시스템 내 서비스인 Google Workspace에 가입", "계정 가입에 그치지 않고 추가로 교육용 클라우드 서비스인 Google Workspace를 신청합니다.", `<button class="text-link" data-action="show-workspace-guide">화면에서 찾기</button>`)}
    </div>
    ${S.workspaceGuideOpen ? workspaceGuideOverlayHtml() : ""}`;
};
function workspaceGuideOverlayHtml() {
  return `<div class="guide-overlay">
    <section class="guide-box" role="dialog" aria-modal="true" aria-label="구글 워크스페이스 위치 안내">
      <div class="guide-head">
        <b>구글 워크스페이스는 여기서 신청해요</b>
        <button class="btn-quiet" data-action="close-workspace-guide">닫기</button>
      </div>
      <img class="guide-shot" src="assets/goedu-workspace-guide.png"
        alt="교육용 클라우드 지원시스템 첫 화면에서 구글 워크스페이스 링크 위치" />
      <p class="guide-note">경기도교육청 교육용 클라우드 지원시스템(goedu.kr)에 로그인한 뒤, 화면 아래 <b>교육용 클라우드 서비스</b>에서 빨간 칸으로 표시한 <b>구글 워크스페이스</b>를 누르세요.</p>
    </section>
  </div>`;
}

/* ---------- 구글 로그인 폴링 ---------- */
let loginTimer = null;
let loginPollRunning = false;
function stopLoginPoll() {
  if (loginTimer) { clearTimeout(loginTimer); loginTimer = null; }
}
async function pollLoginOnce(request) {
  if (loginPollRunning) return true;
  loginPollRunning = true;
  const epoch = googleLoginEpoch;
  const requestScreen = request?.screen || screenKey();
  const current = () => epoch === googleLoginEpoch && requestScreen === screenKey();
  try {
  const snap = await call("gws_login_status");
  if (!current()) return false;
  if (snap.ok === true) {
    request = beginIssueRequest(false);
    delete S.fieldIssues["google-login"];
    clearGoogleDependentState({ preserveResume: true });  // 재승인은 선택을 보존하고 상태만 새로 읽는다
    await refreshSettingsStatus(request, { loginComplete: true, loginEpoch: epoch, googleStatus: snap.google_status });
    if (!ownsIssueRequest(request)) return false;
    if (isGoogleReady(S.google)) {
      if (!(await resumeInterruptedGoogleScreen(request))) showToast("Google 계정으로 로그인했어요");
      refreshChecks().catch(() => {});
    } else setBanner("warn", googleAuthorizationMessage(S.google), "google-login");
    return false;
  }
  if (snap.ok === false) {
    S.login = null;
    if (snap.google_status) adoptGoogleStatus(snap.google_status);
    setBanner("error", snap.detail || "Google 로그인을 끝내지 못했어요. 실패 원인은 아직 확인하지 못했습니다.", "google-login");
    return false;
  }
  const viewChanged = !S.login
    || String(S.login.url || "") !== String(snap.url || "")
    || Boolean(S.login.browser_opened) !== Boolean(snap.browser_opened);
  S.login = snap;
  if (viewChanged) render();
  return true;
  } catch (error) {
    // A parallel status read can advance the account generation during this
    // same login. Discard its old result, but keep observing the current login.
    if (error instanceof StaleAccountResponse) return Boolean(current() && S.login && !S.login.logging_out);
    throw error;
  } finally { loginPollRunning = false; }
}
function pollLogin(delay = 1000) {
  stopLoginPoll();
  const epoch = googleLoginEpoch;
  loginTimer = setTimeout(async () => {
    if (!S.login || epoch !== googleLoginEpoch) return;
    const request = beginIssueRequest(false);
    try {
      if (await pollLoginOnce(request)) pollLogin();
    } catch (error) {
      if (epoch !== googleLoginEpoch || request.screen !== screenKey()) return;
      S.login = null;
      handleCaughtError(error, request);
    }
  }, delay);
}
bindActions({
  "install-gws-update": async (_el, request) => {
    if (S.gwsUpdateInstalling) return;
    const offer = S.gwsUpdate && S.gwsUpdate.offer;
    if (!offer) throw new Error("Google 연결 기능 업데이트를 다시 확인해 주세요.");
    const offerNote = safeGwsOfferText(offer.notes);
    const offerDate = safeGwsOfferDate(offer.verified_on);
    const offerDetails = [
      offerDate ? `승인 확인: ${offerDate}` : "",
      offerNote ? `변경 설명: ${offerNote}` : "",
    ].filter(Boolean).join("\n");
    if (!window.confirm(
      `Google 연결 기능을 ${offer.version} 버전으로 업데이트할까요?\n` +
      (offerDetails ? `\n${offerDetails}\n` : "\n") +
      "받은 파일이 공식 파일과 같은지 확인한 뒤 적용합니다."
    )) return;
    S.gwsUpdateInstalling = true;
    render();
    let result;
    try {
      // 화면이 처음 받은 offer를 그대로 돌려준다. Python 쪽은 메모리에 둔 승인
      // 원문과 한 글자라도 다르면 설치 전에 거부한다.
      result = await call("install_gws_update", offer);
    } finally {
      S.gwsUpdateInstalling = false;
    }
    if (!ownsIssueRequest(request)) return;
    await refreshSettingsStatus(request);
    if (!ownsIssueRequest(request)) return;
    if (!result.success) {
      const fallback = result.can_continue
        ? "현재 Google 연결 기능을 계속 쓸 수 있어요."
        : "Google 연결 기능을 업데이트하지 못했어요. Teacher Manager 설치 파일을 다시 실행해 주세요.";
      throw new Error(result.detail || fallback);
    }
    showToast("Google 연결 기능을 업데이트했어요");
  },
  "gws-login": async () => {
    if (S.login) return;
    const epoch = ++googleLoginEpoch;
    S.banner = null;
    S.login = { running: true };
    render();
    try {
      const snapshot = await call("gws_login_start");
      if (epoch !== googleLoginEpoch) return;
      S.login = snapshot;
      render();
      pollLogin();
    } catch (error) {
      if (epoch !== googleLoginEpoch) return;
      if (error instanceof StaleAccountResponse) {
        if (S.login) pollLogin(0);
        return;
      }
      S.login = null;
      render();
      throw error;
    }
  },
  "google-targets-recheck": async () => {
    syncConnectFields();
    S.listsLoaded = false;
    S.listsError = false;
    await loadLinkLists();
  },
  "reauthorize-google": async () => {
    if (!(S.mode === "edit" && S.edit === "settings") && !(S.mode === "wizard" && S.step === 2)) {
      await actions["goto-settings"]();
    }
    await actions["gws-login"]();
  },
  "gws-logout": async () => {
    if (S.login?.logging_out) return;
    if (!window.confirm("Teacher Manager에서 로그아웃할까요? 기존 자료는 보존됩니다. 다른 계정으로 로그인하면 연결을 다시 선택해야 합니다. Google 시트의 자동 발송은 별도로 관리됩니다.")) return;
    if (!(await flushEditSave())) return;
    if (S.login?.logging_out) return;
    const epoch = ++googleLoginEpoch;
    stopLoginPoll();
    S.login = { running: true, logging_out: true };
    render();
    try {
    const result = await call("gws_logout");
    if (epoch !== googleLoginEpoch) return;
    S.login = null;
    if (!result.success) {
      clearGoogleDependentState();
      S.google = { ...S.google, account_allowed: false, authorization_state: "check_failed" };
      render();
      throw new Error(result.detail || "로그아웃하지 못했어요");
    }
    S.login = null;
    clearAccountScreen();
    S.google = { ...S.google, logged_in: false, user: "", account_allowed: false, authorization_state: "login_required",
      login_state: "logged_out", authorization_reason: "", authorization_detail: "", local_settings_error: "" };
    render();
    accountProfileNeedsReload = true;
    await reloadAccountProfile();
    if (epoch !== googleLoginEpoch) return;
    showToast("Teacher Manager에서 로그아웃했어요");
    } catch (error) {
      if (epoch !== googleLoginEpoch) return;
      S.login = null;
      if (S.google?.login_state !== "logged_out") {
        adoptGoogleStatus({ ...S.google, account_allowed: false, authorization_state: "check_failed" });
      }
      render();
      throw error;
    }
  },
  "login-cancel": async () => {
    const epoch = ++googleLoginEpoch;
    stopLoginPoll();
    try {
      const result = await call("gws_login_cancel");
      if (epoch !== googleLoginEpoch) return;
      if (result.cancelled) {
        S.login = null;
        setBanner("warn", "로그인을 취소했어요. 다시 시도할 수 있어요.", "google-login");
      } else {
        // Completion may beat Cancel. Display the actual account result rather
        // than claiming that a completed login was cancelled.
        const status = await call("google_status");
        if (epoch !== googleLoginEpoch) return;
        S.login = null;
        adoptGoogleStatus(status);
        render();
      }
    } catch (error) {
      if (epoch !== googleLoginEpoch) return;
      S.login = null;
      adoptGoogleStatus({ ...S.google, account_allowed: false, authorization_state: "check_failed" });
      render();
      throw error;
    }
  },
  "gws-repair-oauth": async () => {
    // 로그인이 중간에 끊겨 gws가 남긴 깨진 준비 파일을 치우고 다시 점검한다.
    await call("gws_repair_oauth_client");
    adoptGoogleStatus(await call("google_status"));
    render();
  },
});

/* ---------- 2단계: 내 정보 ---------- */
const IDENTITY_FIELDS = ["선생님이름", "학교명"];
function radioGroup(name, value, options) {
  const buttons = options.map(([v, label]) =>
    `<label><input type="radio" name="${esc(name)}" value="${esc(v)}" ${value === v ? "checked" : ""}> ${esc(label)}</label>`
  ).join("");
  return `<div class="choice">${buttons}</div>`;
}
function segChoice(name, value, options) {
  const items = options.map(([v, label]) =>
    `<label><input type="radio" name="${esc(name)}" value="${esc(v)}" ${value === v ? "checked" : ""}>${esc(label)}</label>`
  ).join("");
  return `<div class="seg">${items}</div>`;
}
function readRadio(name) {
  const el = document.querySelector(`[name="${name}"]:checked`);
  return el ? el.value : "";
}
function displayedSchoolYear(now = new Date()) {
  if (S.attendance?.year_verified && S.attendance.current_school_year) return S.attendance.current_school_year;
  // Display only: workbook creation and binding still require the server-verified year.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", year: "numeric", month: "numeric",
  }).formatToParts(now);
  const year = Number(parts.find(part => part.type === "year").value);
  const month = Number(parts.find(part => part.type === "month").value);
  return month >= 3 ? year : year - 1;
}
function stepIdentity() {
  const p = S.draft.profile;
  const homeroom = p["담임여부"] || "";
  const choiceCell = (name, options) => {
    const error = fieldError(name);
    return `<div class="field${error ? " has-error" : ""}">${segChoice(name, p[name] || "", options)}${fieldNoteHtml(name)}</div>`;
  };
  const locked = homeroom !== "예";
  const homeroomRow = rawRow("담임 학년 · 반",
    `<span class="pair-cell homeroom-cell${locked ? " row-off" : ""}">
      ${fieldInner("담임학년", locked ? "" : p["담임학년"], { placeholder: "2", disabled: locked })}<span class="suffix">학년</span>
      ${fieldInner("담임반", locked ? "" : p["담임반"], { placeholder: "3", disabled: locked })}<span class="suffix">반</span>
      ${locked ? `<span class="cell-hint">담임일 때만 입력해요</span>` : ""}
    </span>`);
  const year = displayedSchoolYear();
  return `
    <h1>선생님을 알려주세요</h1>
    <p class="sub">캘린더 제목과 안내 문구에 쓰여요</p>
    ${formTable(
      fieldRow("선생님이름", "이름", p["선생님이름"]) +
      rawRow("학년도", `<div class="field"><span data-school-year="automatic">${esc(year)}학년도</span><span class="hint">한국 시간 기준으로 자동 적용</span></div>`) +
      fieldRow("학교명", "학교 이름", p["학교명"], { placeholder: "예: OO고등학교" }) +
      rawRow("학교급 (수업 시간을 자동 계산해요)",
        choiceCell("학교급", [["초", "초등 (40분)"], ["중", "중학 (45분)"], ["고", "고등 (50분)"]])) +
      rawRow("담임을 맡고 있나요?", choiceCell("담임여부", [["예", "예"], ["아니오", "아니오"]])) +
      homeroomRow
    )}`;
}
function syncProfileFields() {
  Object.assign(S.draft.profile, readFields(IDENTITY_FIELDS));
  S.draft.profile["학교급"] = readRadio("학교급");
  S.draft.profile["담임여부"] = readRadio("담임여부");
  if (S.draft.profile["담임여부"] === "예") {
    Object.assign(S.draft.profile, readFields(["담임학년", "담임반"]));
  }
  // A stored historical year is never submitted as an editable profile value.
  delete S.draft.profile["학년도"];
}

const ATTENDANCE_SHEET_PROFILE_FIELDS = {
  "선생님이름": "선생님 이름",
  "학교명": "학교 이름",
  "담임학년": "담임 학년",
  "담임반": "담임 반",
};
function recordAttendanceStaleChange(box) {
  if (!box || S.mode !== "wizard" || S.step < 3 || S.step > 5 || !S.attendance
      || !["ready", "installing"].includes(S.attendance.state)) return;
  const data = box.dataset || {};
  let previous;
  let current = String(box.value == null ? "" : box.value);
  let sheetField = "";
  let localGroup = "";
  if (S.step === 3 && box.name) {
    previous = String((S.draft.profile || {})[box.name] || "");
    sheetField = ATTENDANCE_SHEET_PROFILE_FIELDS[box.name] || "";
    localGroup = sheetField ? "" : "내 정보";
  } else if (S.step === 4) {
    const dayName = data.dayHour !== undefined ? data.dayHour
      : data.dayMinute !== undefined ? data.dayMinute : box.name;
    if (!dayName) return;
    const saved = String((S.draft.profile || {})[dayName] || "");
    if (data.dayHour !== undefined) previous = saved.split(":")[0] || "";
    else if (data.dayMinute !== undefined) previous = saved.split(":")[1] || "";
    else previous = saved;
    localGroup = "하루 일과";
  } else if (S.step === 5 && data.grid !== undefined) {
    const [row, column] = String(data.grid).split(":").map(Number);
    previous = String((((S.draft.grid || [])[row] || [])[column]) || "");
    current = current.trim();
    localGroup = "시간표";
  } else {
    return;
  }
  if (current === previous) return;
  const notice = S.attendanceStaleNotice && typeof S.attendanceStaleNotice === "object"
    ? S.attendanceStaleNotice : { sheet_fields: [], local_groups: [] };
  if (sheetField && !notice.sheet_fields.includes(sheetField)) notice.sheet_fields.push(sheetField);
  if (localGroup && !notice.local_groups.includes(localGroup)) notice.local_groups.push(localGroup);
  S.attendanceStaleNotice = notice;
}
// 변경값이 draft에 복사되기 전에 이전 값과 비교한다.
document.addEventListener("input", (event) => recordAttendanceStaleChange(event.target));
document.addEventListener("change", (event) => recordAttendanceStaleChange(event.target));
function validateIdentity() {
  syncProfileFields();
  const rows = identityIssues();
  replaceEditableIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
}
document.addEventListener("change", (event) => {
  if (event.target.name !== "담임여부") return;
  if (S.mode === "wizard" && S.step === 3) { syncProfileFields(); render(); return; }
  if (S.mode === "edit" && S.edit === "identity") { syncProfileFields(); syncDayFields(); render(); return; }
});

/* ---------- 3단계: 하루 일과 ---------- */
const DAY_TIME_FIELDS = [
  ["출근시간", "출근 시간", "08:30"], ["퇴근시간", "퇴근 시간", "16:30"],
  ["조회시작", "조회 시작", "08:40"], ["1교시시작", "1교시 시작", "09:00"],
  ["점심종료시간", "점심 끝 (= 5교시 시작)", "13:10"],
];
const DAY_LAST_FIELDS = ["월", "화", "수", "목", "금"].map((day) => `${day}요일마지막교시`);
const TIME_PATTERN = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
function timeOptionValues(current, values) {
  // 저장된 값이 5분 단위나 시 범위 밖이어도 잃지 않도록 선택지에 끼워 넣는다.
  const list = values.slice();
  if (current && !list.includes(current)) { list.push(current); list.sort(); }
  return list;
}
function stepDay() {
  const p = S.draft.profile;
  const hourValues = Array.from({ length: 17 }, (_, i) => String(i + 6).padStart(2, "0"));
  const minuteValues = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, "0"));
  const times = DAY_TIME_FIELDS.map(([name, label, fallback]) => {
    const current = TIME_PATTERN.test(p[name] || "") ? p[name] : fallback;
    const [hour, minute] = current.split(":");
    const hours = timeOptionValues(hour, hourValues)
      .map((v) => `<option value="${v}" ${v === hour ? "selected" : ""}>${v}</option>`).join("");
    const minutes = timeOptionValues(minute, minuteValues)
      .map((v) => `<option value="${v}" ${v === minute ? "selected" : ""}>${v}</option>`).join("");
    return rawRow(label, `<div class="field"><span class="time-pick">
      <select data-day-hour="${esc(name)}" aria-label="시 (24시간제)">${hours}</select><span class="colon">:</span>
      <select data-day-minute="${esc(name)}" aria-label="분">${minutes}</select>
      <span class="time-unit">시 : 분</span></span>${fieldNoteHtml(name)}</div>`);
  }).join("");
  const lasts = DAY_LAST_FIELDS.map((name) => {
    const options = ["1", "2", "3", "4", "5", "6", "7"].map((n) =>
      `<option value="${n}" ${(p[name] || "7") === n ? "selected" : ""}>${n}교시</option>`
    ).join("");
    return rawRow(`${name[0]}요일 마지막 교시`,
      `<div class="field"><select name="${esc(name)}">${options}</select></div>`);
  }).join("");
  return `
    <h1>하루 일과를 알려주세요</h1>
    <p class="sub">일정을 교시 시간에 맞춰 배치하는 데 써요</p>
    ${formTable(times)}
    <div class="section-h">요일별 마지막 교시</div>
    ${formTable(lasts)}`;
}
function syncDayFields() {
  for (const [name] of DAY_TIME_FIELDS) {
    const hour = document.querySelector(`[data-day-hour="${name}"]`);
    const minute = document.querySelector(`[data-day-minute="${name}"]`);
    if (hour && minute) S.draft.profile[name] = `${hour.value}:${minute.value}`;
  }
  for (const name of DAY_LAST_FIELDS) {
    const el = document.querySelector(`[name="${name}"]`);
    if (el) S.draft.profile[name] = el.value;
  }
}
function validateDay() {
  syncDayFields();
  const rows = dayIssues();
  replaceEditableIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
}

/* ---------- 4단계: 시간표 ---------- */
const GRID_DAYS = ["월", "화", "수", "목", "금"];
async function ensureGridLoaded(request) {
  if (S.draft.grid && S.draft.grid.length) return true;
  const grid = await call("read_grid");
  if (!ownsIssueRequest(request)) return false;
  S.draft.grid = grid;
  return true;
}
function stepTimetable() {
  if (!S.draft.grid || !S.draft.grid.length) {
    const request = beginIssueRequest(false);
    ensureGridLoaded(request)
      .then((loaded) => { if (loaded && ownsIssueRequest(request)) render(); })
      .catch((error) => handleCaughtError(error, request));
    return `<h1>시간표를 채워주세요</h1><p class="sub">불러오는 중이에요…</p>`;
  }
  const head = `<tr><th></th>${GRID_DAYS.map((d) => `<th>${d}</th>`).join("")}</tr>`;
  const rows = S.draft.grid.map((row, r) =>
    `<tr><td class="period">${esc(row[0])}교시</td>` +
    GRID_DAYS.map((_, c) =>
      `<td><input data-grid="${r}:${c + 1}" value="${esc(row[c + 1] || "")}"></td>`
    ).join("") + "</tr>"
  ).join("");
  return `
    <h1>시간표를 채워주세요</h1>
    <p class="sub">공강 시간을 중심으로 행정업무 일정을 등록해 드려요. 입력 형식은 자유로워요 (예: 2-1, 2학년 1반, 201…)</p>
    <p class="sub">엑셀에서 요일·교시 제목을 제외한 칸을 복사한 뒤, 시작할 칸에 붙여넣으세요. 가로는 월~금, 세로는 1~7교시예요. 빈칸도 그대로 반영돼요.</p>
    <table class="grid-table">${head}${rows}</table>`;
}
function syncGridFields() {
  document.querySelectorAll("[data-grid]").forEach((el) => {
    const [r, c] = el.dataset.grid.split(":").map(Number);
    S.draft.grid[r][c] = el.value.trim();
  });
}
function validateTimetable() { syncGridFields(); return ""; }

document.addEventListener("paste", event => {
  const key = event.target.dataset?.grid;
  if (!key || !S.draft.grid?.length || event.target.disabled || event.target.readOnly) return;
  const text = event.clipboardData?.getData("text/plain") || "";
  if (!/[\t\r\n]/.test(text)) return; // 한 칸 붙여넣기는 기존 입력 동작을 유지한다.
  event.preventDefault();
  const [start, column] = key.split(":").map(Number);
  const pasted = text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n").map(line => line.split("\t"));
  if (!Number.isInteger(start) || !Number.isInteger(column) || start < 0 || column < 1
      || start + pasted.length > S.draft.grid.length
      || pasted.some(row => row.length + column > GRID_DAYS.length + 1)) {
    setBanner("warn", "시간표 범위를 넘어서 붙여넣지 않았어요. 월~금, 1~7교시 안에 들어오도록 복사 범위나 시작 칸을 확인해 주세요.");
    return;
  }
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text)) {
    setBanner("warn", "붙여넣을 내용에 사용할 수 없는 제어 문자가 있어요. 엑셀의 시간표 칸만 다시 복사해 주세요.");
    return;
  }
  syncGridFields();
  pasted.forEach((row, offset) => row.forEach((value, c) => {
    S.draft.grid[start + offset][column + c] = value.trim();
  }));
  document.querySelectorAll("[data-grid]").forEach(el => {
    const [r, c] = el.dataset.grid.split(":").map(Number);
    el.value = S.draft.grid[r][c];
  });
  if (S.mode === "edit" && S.edit === "timetable") {
    markEditDirtyField("__timetable__");
    scheduleEditAutoSave();
  }
});

/* Homeroom roster stays inside the timetable window and the setup wizard. */
let rosterEditorRequest = 0;
let rosterEditorPending = null;
function isHomeroomTeacher() { return (S.draft.profile["담임여부"] || S.profileCache?.["담임여부"]) === "예"; }
function rosterEditorContext() { return JSON.stringify([accountUiEpoch, accountWireToken?.[0] || S.google?.user || ""]); }
function rosterEditableRows(rows = [], minimum = 30) {
  const result = rows.map(row => [...row]);
  let next = Math.max(0, ...result.map(row => Number(row[0]) || 0)) + 1;
  while (result.length < minimum) result.push([String(next++), "", ""]);
  return result;
}
function rosterRowHasStudent(row) { return row.slice(1).some(value => String(value || "").trim()); }
async function loadRosterEditor(refresh = false) {
  if (!isHomeroomTeacher()) return;
  const context = rosterEditorContext();
  const owner = screenKey();
  if (rosterEditorPending?.context === context && rosterEditorPending.owner === owner) return rosterEditorPending.promise;
  if (!refresh && S.rosterEditor?.context === context && S.rosterEditor.state !== "loading") return;
  const version = ++rosterEditorRequest;
  let finishPending;
  rosterEditorPending = { context, owner, promise: new Promise(resolve => { finishPending = resolve; }) };
  S.rosterEditor = { rows: [], revision: "", state: "loading", context };
  try {
    const data = await call("read_roster_editor", refresh);
    if (version !== rosterEditorRequest || context !== rosterEditorContext() || owner !== screenKey()) return;
    S.rosterEditor = { ...data, rows: rosterEditableRows(data.rows), context, dirty: false };
    if (data.state !== "unavailable" && S.banner?.topic === "roster-validation") S.banner = null;
  } catch (_) {
    if (version !== rosterEditorRequest || context !== rosterEditorContext() || owner !== screenKey()) return;
    S.rosterEditor = { rows: [], revision: "", state: "unavailable", context, detail: "명단을 불러오지 못했어요. 다시 불러오기를 눌러 주세요." };
  } finally {
    if (version === rosterEditorRequest) {
      rosterEditorPending = null;
      if (context === rosterEditorContext() && owner !== screenKey() && S.rosterEditor?.state === "loading") S.rosterEditor = null;
    }
    finishPending();
    if (version === rosterEditorRequest && context === rosterEditorContext() && owner === screenKey()) render();
  }
}
function rosterInputProblem() {
  const rows = S.rosterEditor?.rows || [];
  if (!rows.some(rosterRowHasStudent) || rows.length > 199) return "학생명단은 1명부터 199명까지 입력해 주세요.";
  const numbers = new Set(), emails = new Set();
  for (let i = 0; i < rows.length; i++) {
    if (!rosterRowHasStudent(rows[i])) continue;
    const [number, name, email] = rows[i].map(v => String(v || "").trim());
    if (!/^[0-9]{1,4}$/.test(number) || Number(number) < 1) return `${i + 1}번째 학생 번호를 확인해 주세요.`;
    if (!name) return `${i + 1}번째 학생 이름을 입력해 주세요.`;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return `${i + 1}번째 학생 이메일을 빠짐없이 입력해 주세요.`;
    if (numbers.has(Number(number)) || emails.has(email.toLowerCase())) return `${i + 1}번째 학생 번호 또는 이메일이 중복돼요.`;
    numbers.add(Number(number)); emails.add(email.toLowerCase());
  }
  return "";
}
let rosterAuthorizationRetry = null;
async function saveRosterEditor(sync = false, authorize = true, explicitConnect = false, useCurrentWorkbook = false) {
  let editor = S.rosterEditor;
  if (!editor || editor.busy || S.attendanceAccountAuthorizing || editor.context !== rosterEditorContext()) return false;
  const problem = rosterInputProblem();
  if (problem) { setBanner("warn", problem, "roster-validation"); return false; }
  const context = editor.context, owner = screenKey();
  const connection = attendanceScopeKey(S.attendance) || S.attendance?.spreadsheet_id || S.attendance?.spreadsheet_url || "";
  let requestedRevision = editor.revision;
  // The saved editor can outlive its screen; a replacement editor cannot inherit this request.
  const current = () => context === rosterEditorContext() && S.rosterEditor === editor;
  const visible = () => owner === screenKey();
  const rows = editor.rows.filter(rosterRowHasStudent).map(row => row.map(value => String(value).trim()));
  editor.busy = true; render();
  try {
    if (sync && explicitConnect && !editor.dirty && editor.revision) {
      const pending = editor.pending, linked = Boolean(editor.linked);
      const fresh = await call("read_roster_editor", false, true, requestedRevision);
      if (!current() || !visible() || connection !== (attendanceScopeKey(S.attendance)
          || S.attendance?.spreadsheet_id || S.attendance?.spreadsheet_url || "")) return false;
      const unchanged = !editor.dirty && editor.revision === requestedRevision
        && editor.pending === pending && Boolean(editor.linked) === linked
        && JSON.stringify(editor.rows.filter(rosterRowHasStudent).map(row => row.map(value => String(value).trim()))) === JSON.stringify(rows);
      if (!unchanged || fresh?.revision_accepted !== true || !fresh.revision
          || fresh.pending !== pending || Boolean(fresh.linked) !== linked
          || JSON.stringify(fresh.rows) !== JSON.stringify(rows)) {
        editor.state = "conflict";
        editor.detail = "저장된 명단이 바뀌었어요. 현재 명단을 확인한 뒤 연결해 주세요.";
        setBanner("warn", editor.detail);
        return false;
      }
      // Only the verified local protection record may advance this saved revision.
      editor.revision = fresh.revision;
    }
    let saved = editor;
    if (editor.dirty || !editor.revision) saved = await call("save_roster_editor", rows, editor.revision || "");
    if (!current()) return false;
    S.rosterEditor = editor = { ...saved, rows: rosterEditableRows(saved.rows), context, dirty: false, busy: true };
    if (sync) {
      requestedRevision = editor.revision;
      const result = explicitConnect
        ? await call("sync_roster_editor", requestedRevision, ...(useCurrentWorkbook ? [true] : []))
        : await call("sync_roster_editor");
      if (!current()) return false;
      S.rosterEditor = editor = { ...result, rows: rosterEditableRows(result.rows), context, dirty: false, busy: true };
      rosterStatus = null;
    }
    if (["unavailable", "conflict"].includes(S.rosterEditor.state)) {
      if (visible()) setBanner("warn", S.rosterEditor.detail || "출석부에 명단을 저장하지 못했어요. 명단 저장을 다시 눌러 주세요.");
      if (authorize && visible() && S.rosterEditor.failure_code === "ATTENDANCE_AUTH_REQUIRED") {
        rosterAuthorizationRetry = {context, owner, revision: requestedRevision, explicitConnect, useCurrentWorkbook, connection};
        await actions["attendance-account-authorize"]();
      }
      return false;
    }
    if (visible()) S.banner = null;
    return true;
  } catch (_) {
    if (current()) {
      S.rosterEditor.detail = "명단 저장 결과를 확인하지 못했어요. 입력한 내용은 이 화면에 남아 있어요. 다시 저장해 주세요.";
      S.rosterEditor.state = "unavailable";
      if (visible()) setBanner("warn", S.rosterEditor.detail);
    }
    return false;
  } finally {
    // Cleanup belongs to this editor, even when its response was fenced out.
    // A same-account epoch change repaints into the existing read-only reload path.
    editor.busy = false;
    if (S.rosterEditor === editor && visible()) render();
  }
}
function rosterSavedForNext() {
  if (!isHomeroomTeacher()) return true;
  const editor = S.rosterEditor;
  return Boolean(editor?.context === rosterEditorContext() && editor.revision && !editor.dirty && !editor.busy
    && !["loading", "unavailable", "conflict"].includes(editor.state) && !rosterInputProblem());
}
async function validateRoster() {
  if (!isHomeroomTeacher()) return "";
  await loadRosterEditor();
  const problem = rosterInputProblem();
  if (problem) return problem;
  const editor = S.rosterEditor;
  if (editor?.revision && !editor.dirty && !editor.busy && ["conflict", "unavailable"].includes(editor.state)) {
    return editor.detail || "명단은 이 컴퓨터에 저장되어 있어요. 출석부 연결 상태를 확인해 주세요.";
  }
  return rosterSavedForNext() ? "" : "명단 저장을 먼저 눌러 주세요. 저장한 뒤 다음으로 진행할 수 있어요.";
}
function stepRoster() {
  if (!isHomeroomTeacher()) return `<h1>담임학급 학생명단</h1><p class="sub">담임을 맡지 않아 이 단계는 사용하지 않아요.</p>`;
  if (S.rosterEditor?.context !== rosterEditorContext()) loadRosterEditor();
  const editor = S.rosterEditor || { state: "loading", rows: [] };
  if (editor.state === "loading") return `<h1>담임학급 학생명단</h1><p class="sub">명단을 불러오고 있어요…</p>`;
  const disabled = editor.busy ? " disabled" : "";
  const rows = editor.rows.map((row, index) => `<tr>${row.map((value, column) => `<td><input data-roster-cell="${index}:${column}" aria-label="${index + 1}번째 학생 ${["번호", "이름", "이메일"][column]}" ${column === 2 ? 'type="email"' : column === 0 ? 'inputmode="numeric"' : ''} value="${esc(value)}"${disabled}></td>`).join("")}</tr>`).join("");
  return `<h1>담임학급 학생명단</h1>
    <p class="sub">학생 이메일은 경기도교육청 클라우드 계정(@goedu.kr)을 권장합니다.<br>엑셀·구글시트에서 복사·붙여넣기 가능합니다.</p>
    <div class="roster-scroll"><table class="grid-table roster-table"><thead><tr><th scope="col">번호</th><th scope="col">이름</th><th scope="col">이메일 · 필수</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="roster-actions"><button class="btn-tonal" data-action="roster-add"${editor.busy || editor.rows.length >= 199 ? " disabled" : ""}>행 추가</button>
      ${editor.state === "conflict" || (editor.state === "unavailable" && !editor.dirty && !editor.pending) ? `<button class="btn-quiet" data-action="roster-reload"${disabled}>${editor.state === "conflict" ? "시트 명단 다시 불러오기" : "다시 불러오기"}</button>` : ""}
      <button class="btn" data-action="roster-save"${disabled}>${editor.busy ? "저장 중…" : "명단 저장"}</button>
    </div>
    <p class="sub" role="status">${esc(editor.busy ? "명단 저장 결과를 확인하고 있어요." : editor.state === "unavailable" ? editor.detail : editor.dirty ? "수정한 명단을 저장해 주세요." : editor.detail || "출석부 연결 전에 입력해 둘 수 있어요.")}</p>`;
}
function timetableEditBody() {
  const active = S.timetableTab || "timetable";
  return `<div class="connect-tabs timetable-tabs" role="tablist" aria-label="시간표와 명단"><button role="tab" aria-selected="${active === "timetable"}" class="connect-tab ${active === "timetable" ? "active" : ""}" data-action="timetable-tab" data-tab="timetable"><span class="tab-title">시간표</span></button><button role="tab" aria-selected="${active === "roster"}" class="connect-tab ${active === "roster" ? "active" : ""}" data-action="timetable-tab" data-tab="roster" ${isHomeroomTeacher() ? "" : "disabled"}><span class="tab-title">담임학급 학생명단</span></button></div>${active === "roster" ? stepRoster() : stepTimetable()}`;
}
document.addEventListener("input", event => {
  const key = event.target.dataset?.rosterCell;
  if (!key || !S.rosterEditor || S.rosterEditor.busy) return;
  const [row, column] = key.split(":").map(Number);
  S.rosterEditor.rows[row][column] = event.target.value;
  S.rosterEditor.dirty = true;
  if (S.mode === "wizard" && S.step === 6) {
    const next = document.querySelector('[data-action="go-next"]');
    if (next) next.disabled = true;
  }
});
document.addEventListener("paste", event => {
  const key = event.target.dataset?.rosterCell;
  if (!key || !S.rosterEditor || S.rosterEditor.busy) return;
  const text = event.clipboardData?.getData("text/plain") || "";
  if (!/[\t\r\n]/.test(text)) return;
  event.preventDefault();
  const [start, column] = key.split(":").map(Number);
  const pasted = text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n").map(line => line.split("\t"));
  if (pasted.length + start > 199 || pasted.some(row => row.length + column > 3)) {
    setBanner("warn", "번호·이름·이메일 세 열만 복사해 주세요. 최대 199명까지 입력할 수 있어요."); return;
  }
  S.rosterEditor.rows = rosterEditableRows(S.rosterEditor.rows, Math.max(30, start + pasted.length));
  pasted.forEach((row, offset) => {
    const target = S.rosterEditor.rows[start + offset];
    row.forEach((value, c) => { target[column + c] = value.trim(); });
  });
  S.rosterEditor.dirty = true; render();
  document.querySelector(`[data-roster-cell="${key}"]`)?.focus();
});
bindActions({
  "roster-add": () => {
    if (!S.rosterEditor || S.rosterEditor.busy || S.rosterEditor.rows.length >= 199) return;
    const next = Math.max(0, ...S.rosterEditor.rows.map(row => Number(row[0]) || 0)) + 1;
    S.rosterEditor.rows.push([String(next), "", ""]); S.rosterEditor.dirty = true; render();
    document.querySelector(`[data-roster-cell="${S.rosterEditor.rows.length - 1}:1"]`)?.focus();
  },
  "roster-save": () => saveRosterEditor(S.mode === "edit" || S.rosterEditor?.linked === true || attendanceSheetSetupDone(), true, true, true),
  "roster-reload": async () => {
    if (S.rosterEditor?.busy) return;
    if ((S.rosterEditor?.dirty || S.rosterEditor?.pending || S.rosterEditor?.state === "conflict") && !window.confirm("이 화면에 입력한 명단 대신 현재 시트의 명단을 불러올까요?")) return;
    await loadRosterEditor(true);
  },
  "timetable-tab": async el => {
    if (el.dataset.tab === "roster" && !isHomeroomTeacher()) return;
    if (S.rosterEditor?.busy) return;
    const context = rosterEditorContext(), owner = screenKey();
    if (S.timetableTab === "roster" && S.rosterEditor?.dirty && !(await saveRosterEditor(true))) return;
    if (!(await flushEditSave())) return;
    if (context !== rosterEditorContext() || owner !== screenKey()) return;
    S.timetableTab = el.dataset.tab; render();
  },
});

/* ---------- 연결·설정 공통 ---------- */
const KEY_MESSAGES = {
  ok: "연결 키가 정상이에요",
  missing: "Gemini 연결 키가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
  invalid: "Gemini 연결 키가 맞지 않아요. Google AI Studio에서 다시 복사해 주세요.",
  "rate-limited": "Gemini 사용 한도에 도달했어요. 한도가 언제 다시 열리는지는 확인하지 못했습니다. Google AI Studio에서 사용량을 확인해 주세요.",
  network: "Gemini 응답을 받지 못했어요. 잠시 후 다시 확인해 주세요.",
  "service-unavailable": "Google Gemini가 일시적으로 응답하지 못했어요. 키를 바꾸지 말고 잠시 후 다시 확인해 주세요.",
  "model-unavailable": "선택한 AI 모델을 사용할 수 없어요. 다른 모델을 선택해 확인해 주세요.",
  forbidden: "Google에서 이 키의 사용을 허용하지 않았어요. Google AI Studio에서 키의 사용 권한을 확인해 주세요.",
  "request-rejected": "Google이 키 확인 요청을 처리하지 못했어요. 키 오류로 확인된 것은 아닙니다. 잠시 후 다시 확인해 주세요.",
};
const PROBE_MESSAGES = {
  available: "사용할 수 있어요",
  taken: "이 단축키를 사용할 수 없어요. [다른 조합 직접 누르기]에서 다른 조합을 눌러 주세요.",
  invalid: "보조키 두 개 이상 또는 보조키와 일반 키를 함께 눌러 주세요",
};
const DEFAULT_HOTKEY = "ctrl+alt+win";
const HK_MODS = ["ctrl", "alt", "shift", "win"];
function prettyHotkey(text) {
  const names = { ctrl: "Ctrl", alt: "Alt", shift: "Shift", win: "Win" };
  return String(text || "").split("+").map((p) => names[p] || p.toUpperCase()).join(" + ");
}

const hotkeyCapture = {
  active: false, paused: false, generation: 0,
  down: new Set(), captured: new Set(), timer: null,
};

function ensureHotkeyState(d) {
  if (!d.hotkey) d.hotkey = DEFAULT_HOTKEY;
  if (!S.hk) S.hk = { current: d.hotkey, recording: false, status: null };
}
function hotkeyRow(d) {
  ensureHotkeyState(d);
  const msg = S.hk.status ? `<span class="hk-msg ${S.hk.status.kind}">${esc(S.hk.status.text)}</span>` : "";
  return rawRow("메신저 단축키", `<div class="field">
    <div class="hk-actions"><b class="now-hk">${esc(prettyHotkey(S.hk.current))}</b>
      <button class="btn-tonal" data-action="hk-record" data-busy-text="준비 중…">${S.hk.recording ? "원하는 조합을 눌러 주세요…" : "다른 조합 직접 누르기"}</button>${msg}</div>
    <span class="hint hk-hint">Ctrl·Alt·Shift·Win 중 두 개 이상만 눌러도 되고, 문자나 숫자를 함께 눌러도 돼요. Esc를 누르면 취소해요.</span>
  </div>`);
}

function recordedKeyName(event) {
  const modifiers = { Control: "ctrl", Alt: "alt", Shift: "shift", Meta: "win" };
  if (modifiers[event.key]) return modifiers[event.key];
  if (/^[a-z0-9]$/i.test(event.key)) return event.key.toLowerCase();
  if (/^F(?:[1-9]|1[0-2])$/i.test(event.key)) return event.key.toLowerCase();
  return "";
}
function recordedHotkeyText(keys) {
  const modifiers = HK_MODS.filter((name) => keys.has(name));
  const ordinary = Array.from(keys).filter((name) => !HK_MODS.includes(name));
  if (ordinary.length > 1) return "";
  return modifiers.concat(ordinary).join("+");
}
async function stopHotkeyRecording(message) {
  if (!hotkeyCapture.active && !hotkeyCapture.paused) return;
  hotkeyCapture.generation += 1;  // 진행 중이던 충돌 확인 결과도 무효로 만든다.
  hotkeyCapture.active = false;
  clearTimeout(hotkeyCapture.timer);
  hotkeyCapture.timer = null;
  hotkeyCapture.down.clear();
  hotkeyCapture.captured.clear();
  if (S.hk) {
    S.hk.recording = false;
    if (message) S.hk.status = { kind: "bad", text: message };
  }
  const shouldResume = hotkeyCapture.paused;
  hotkeyCapture.paused = false;
  if (shouldResume) {
    try { await call("hotkey_recording_end"); } catch (error) { /* 화면 이동은 막지 않는다 */ }
  }
}
async function finishHotkeyRecording() {
  if (!hotkeyCapture.active) return;
  const generation = hotkeyCapture.generation;
  const text = recordedHotkeyText(hotkeyCapture.captured);
  hotkeyCapture.active = false;
  clearTimeout(hotkeyCapture.timer);
  hotkeyCapture.timer = null;
  if (S.hk) S.hk.recording = false;
  let autoSaveWhenDone = false;
  let request = null;
  try {
    if (!text) {
      S.hk.status = { kind: "bad", text: "한 조합으로 함께 눌러 주세요" };
      return;
    }
    if (text === S.hk.current) {
      S.draft.bridge.hotkey = text;
      S.hk.status = { kind: "ok", text: "지금 쓰는 단축키예요" };
      return;
    }
    if (settingsAutoSavePromise) {
      const owner = screenKey(), epoch = accountUiEpoch;
      if (!(await settingsAutoSavePromise) || owner !== screenKey() || epoch !== accountUiEpoch
          || hotkeyCapture.generation !== generation) return;
    }
    request = beginIssueRequest(false);
    const result = await call("probe_hotkey", text);
    if (hotkeyCapture.generation !== generation || !ownsIssueRequest(request)) return;
    if (result.status === "available") {
      S.draft.bridge.hotkey = text;
      if (S.mode === "edit" && S.edit === "settings") {
        // 설정 화면은 자동 저장 — 녹음이 끝나면(아래 finally에서 등록 재개 후) 바로 저장한다.
        S.hk.status = { kind: "ok", text: `${prettyHotkey(text)} · 저장하는 중…` };
        editDirtyFields.add("hotkey");
        autoSaveWhenDone = true;
      } else {
        S.hk.status = { kind: "ok", text: `${prettyHotkey(text)} · 저장할 수 있어요` };
      }
    } else if (result.status === "taken") {
      S.hk.status = { kind: "bad", text: PROBE_MESSAGES.taken };
    } else {
      S.hk.status = { kind: "bad", text: PROBE_MESSAGES.invalid };
    }
  } catch (error) {
    if (!ownsIssueRequest(request) || showProblemIssue(error, request)) return;
    if (hotkeyCapture.generation === generation && S.hk) {
      S.hk.status = { kind: "bad", text: error.message };
    }
  } finally {
    if (hotkeyCapture.generation === generation) {
      hotkeyCapture.down.clear();
      hotkeyCapture.captured.clear();
      const shouldResume = hotkeyCapture.paused;
      hotkeyCapture.paused = false;
      if (shouldResume) {
        try { await call("hotkey_recording_end"); } catch (error) { /* 다음 저장 때 재검사한다 */ }
      }
      if (autoSaveWhenDone) {
        try {
          await autoSaveSettings(true, request);
        } catch (error) {
          if (S.hk) S.hk.status = { kind: "bad", text: error.message };
          handleCaughtError(error, request);
        }
      }
      render();
    }
  }
}
document.addEventListener("keydown", (event) => {
  if (!hotkeyCapture.active) return;
  if (event.key === "Escape") {
    event.preventDefault();
    stopHotkeyRecording("기존 단축키를 그대로 둘게요").then(render);
    return;
  }
  const key = recordedKeyName(event);
  if (!key) return;
  event.preventDefault();
  hotkeyCapture.down.add(key);
  hotkeyCapture.captured.add(key);
}, true);
document.addEventListener("keyup", (event) => {
  if (!hotkeyCapture.active) return;
  const key = recordedKeyName(event);
  if (!key) return;
  event.preventDefault();
  hotkeyCapture.down.delete(key);
  if (hotkeyCapture.captured.size && hotkeyCapture.down.size === 0) finishHotkeyRecording();
}, true);

const API_KEY_URL = "https://aistudio.google.com/apikey";
const API_KEY_GUIDE_URL = "https://youtube.com/shorts/FMZmdpcLlM0?si=KT-_oblorYxE5ZE4";
function apiKeyLinkRow() {
  return `<div class="section-note" style="margin:0 0 4px">Gemini 연결 키 발급받기</div>
    <div class="linkrow"><span class="url" title="${esc(API_KEY_URL)}">${esc(API_KEY_URL)}</span>
    <span class="acts">
      <button class="btn-tonal" data-action="link-open" data-url="${esc(API_KEY_URL)}">${icon("external-link", "small blue")} 열기</button>
      <button class="btn-tonal youtube" data-action="link-open" data-url="${esc(API_KEY_GUIDE_URL)}">${icon("youtube", "small")} 발급방법</button>
    </span></div>`;
}
function geminiSectionHtml(d) {
  const keyLine = S.keyStatus ? badge(S.keyStatus.kind, S.keyStatus.text) : "";
  const model = d.gemini_model || "gemini-3.5-flash";
  const modelRow = rawRow("AI 모델", `<div class="field"><select name="gemini_model">
    <option value="gemini-3.5-flash" ${model === "gemini-3.5-flash" ? "selected" : ""}>Gemini 3.5 Flash · 추천</option>
    <option value="gemini-3.1-flash-lite" ${model === "gemini-3.1-flash-lite" ? "selected" : ""}>Gemini 3.1 Flash-Lite · 빠른 처리</option>
  </select></div>`);
  return `<div class="section-h">Gemini 연결 키</div>
    <p class="sub" style="margin-bottom:10px">AI 분석을 위해 선택한 메시지와 첨부파일 내용을 Google Gemini로 보냅니다. 이 키는 이 컴퓨터에 저장됩니다. AI 출결 입력을 사용하면 연결한 출석부의 [설정] 탭에도 저장되며, 그 출석부를 편집할 수 있는 사람도 키를 볼 수 있습니다.</p>
    ${apiKeyLinkRow()}
    <div style="margin-top:12px"></div>
    ${formTable(
      fieldRow("gemini_api_key", "Gemini 연결 키", d.gemini_api_key || "", { type: "password", placeholder: "붙여넣기" }) +
      modelRow
    )}
    <div class="action-line">${keyLine}<button class="btn-tonal" data-action="check-key" data-busy-text="확인 중…">키 확인</button></div>`;
}
function syncGeminiDraft() {
  const values = readFields(["gemini_api_key", "gemini_model"]);
  if (values.gemini_api_key !== undefined) S.draft.bridge.gemini_api_key = values.gemini_api_key;
  if (values.gemini_model !== undefined) S.draft.bridge.gemini_model = values.gemini_model;
}

/* ---------- 5단계: 연결 ---------- */
const NEW_LIST_DEFAULTS = {
  "업무캘린더이름": "업무", "학사일정캘린더이름": "학사일정",
  "업무Tasks목록이름": "업무", "담임안내Tasks목록이름": "조종례시 담임학급 안내사항",
};
const CAL_LINK_FIELDS = [
  ["업무캘린더ID", "업무캘린더이름", "1. 개인 업무 관련 메시지 내용 정리 후 일정 등록"],
  ["학사일정캘린더ID", "학사일정캘린더이름", "2. 학사 일정 관련 메시지 내용 정리 후 일정 등록"],
];
const TASK_LINK_FIELDS = [
  ["업무Tasks목록ID", "업무Tasks목록이름", "1. 개인 업무 관련 메시지 내용 정리 후 할일 등록"],
  ["담임안내Tasks목록ID", "담임안내Tasks목록이름", "2. 조종례시 담임학급 전달사항 관련 메시지 내용 정리 후 할일 등록"],
];
function linkModes() {
  if (!S.draft.linkModes) S.draft.linkModes = { cal: "existing", task: "existing" };
  return S.draft.linkModes;
}
function taskLinkFields() {
  return S.draft.profile["담임여부"] === "예" ? TASK_LINK_FIELDS : TASK_LINK_FIELDS.slice(0, 1);
}
function googleListContext() {
  return JSON.stringify([accountUiEpoch, googleContextVersion, googleLoginEpoch, verifiedGoogleAccount(S.google)]);
}
function currentListRows(kind) {
  const read = S.listReads[kind];
  return read?.phase === "ready" && read.context === googleListContext() ? read.value : [];
}
let linkListsPending = null;
async function loadLinkLists(force = false, owns = () => true) {
  const context = googleListContext();
  if (S.linkLoading && linkListsPending?.context === context && linkListsPending.owns()) return linkListsPending.promise;
  const promise = readLinkLists(force, owns);
  const pending = linkListsPending = { context, promise, owns };
  const clear = () => { if (linkListsPending === pending) linkListsPending = null; };
  promise.then(clear, clear);
  return promise;
}
async function readLinkLists(force, owns) {
  if (!force && S.listsLoaded && ["calendars", "tasklists"].every(kind => S.listReads[kind]?.context === googleListContext())) return;
  if (!isGoogleReady(S.google)) return;
  S.linkLoading = true;
  S.listsError = false;
  S.listsLoaded = false;
  const resourceContext = googleListContext();
  const version = ++linkListsReadVersion;
  const current = () => version === linkListsReadVersion && resourceContext === googleListContext() && owns();
  const paint = () => { if (editingCard() === "settings") paintSettingsReadiness(); else render(); };
  S.lists = { calendars: [], tasklists: [] };
  S.googleTargetStatuses = {};
  for (const kind of ["calendars", "tasklists"]) {
    S.listReads[kind] = { phase: "pending", context: resourceContext, generation: version, value: [], error: null };
  }
  paint();
  try {
    await Promise.all(["calendars", "tasklists"].map(async kind => {
      try {
        const rows = await call(kind === "calendars" ? "list_calendars" : "list_tasklists");
        if (!current()) return;
        if (!Array.isArray(rows) || rows.some(row => !row || typeof row.id !== "string" || !row.id.trim()))
          throw new Error("목록 응답을 확인하지 못했어요.");
        S.listReads[kind] = { phase: "ready", context: resourceContext, generation: version, value: rows, error: null };
        S.lists[kind] = rows;
        paint();
        await refreshGoogleTargetStatuses(current, kind);
      } catch (error) {
        if (!current()) return;
        S.listReads[kind] = { phase: "failed", context: resourceContext, generation: version, value: [], error: String(error.message || error) };
        S.lists[kind] = [];
        S.listsError = true;
        paint();
      }
    }));
    if (current()) S.listsLoaded = !S.listsError;
  } finally {
    if (version === linkListsReadVersion) S.linkLoading = false;
    if (current()) paint();
  }
}
const targetReadVersions = { calendars: 0, tasklists: 0 };
async function refreshGoogleTargetStatuses(owns = () => true, kind = null) {
  if (!isGoogleReady(S.google)) return;
  S.googleTargetStatuses ||= {};
  const targets = Object.fromEntries(GOOGLE_TARGET_ID_FIELDS
    .filter(field => !kind || (field.includes("Tasks") ? "tasklists" : "calendars") === kind)
    .filter((field) => S.draft.profile[field] && (field !== "담임안내Tasks목록ID" || S.draft.profile["담임여부"] === "예"))
    .map((field) => [field, S.draft.profile[field]]));
  const context = googleListContext();
  const kinds = kind ? [kind] : ["calendars", "tasklists"];
  const versions = Object.fromEntries(kinds.map(key => [key, ++targetReadVersions[key]]));
  const version = targetStatusReadVersion;
  let statuses;
  try { statuses = await call("google_target_statuses", targets); }
  catch (_) { statuses = {}; }
  if (!owns() || version !== targetStatusReadVersion || context !== googleListContext()) return;
  for (const [field, id] of Object.entries(targets)) {
    const key = field.includes("Tasks") ? "tasklists" : "calendars";
    if (versions[key] !== targetReadVersions[key]) continue;
    const result = statuses?.[field];
    S.googleTargetStatuses[field] = result?.id === id ? result : { id, state: "check_failed", detail: "저장된 연결을 확인하지 못했어요. 선택은 그대로 두었어요. [연결 다시 확인]을 눌러 주세요." };
  }
}
function linkSelectRow(idField, title, options, value, excludeId, savedName, phase = "ready") {
  const filtered = options.filter((o) => o.id && o.id !== excludeId);
  const savedOutsideList = value && !filtered.some((o) => o.id === value);
  const rows = filtered
    .map((o) => `<option value="${esc(o.id)}" ${o.id === value ? "selected" : ""}>${esc(o.name)}</option>`)
    .join("");
  const empty = phase === "pending" ? "불러오는 중…" : phase === "failed" ? "목록 확인 필요" : "골라 주세요";
  const error = fieldError(idField);
  return rawRow(title, `<div class="field${error ? " has-error" : ""}">
    <select name="${esc(idField)}" data-link-select${phase !== "ready" ? " disabled" : ""}${error ? ' aria-invalid="true"' : ""}>
      <option value="">${empty}</option>${rows}
    </select>${savedOutsideList && phase !== "pending" ? '<p class="hint">저장된 선택은 보관하고 있어요. 현재 목록에서 확인한 뒤 연결해 주세요.</p>' : ""}${fieldNoteHtml(idField)}</div>`);
}
function linkExistingRowsHtml(kind) {
  const cal = kind === "cal";
  const p = S.draft.profile;
  const fields = cal ? CAL_LINK_FIELDS : taskLinkFields();
  const kindKey = cal ? "calendars" : "tasklists";
  const options = currentListRows(kindKey);
  const phase = S.listReads[kindKey]?.context === googleListContext() ? S.listReads[kindKey].phase : "pending";
  return fields.map(([idField, nameField, title], index) => {
    const other = fields[index === 0 ? 1 : 0];
    const excludeId = other ? (p[other[0]] || "") : "";
    return linkSelectRow(idField, title, options, p[idField] || "", excludeId, p[nameField] || "", phase);
  }).join("");
}
function linkGroupHtml(kind) {
  const cal = kind === "cal";
  const p = S.draft.profile;
  const fields = cal ? CAL_LINK_FIELDS : taskLinkFields();
  const mode = linkModes()[kind];
  const choices = cal
    ? [["existing", "기존 캘린더 연결하기"], ["new", "캘린더 새로 만들기"]]
    : [["existing", "기존 목록 연결하기"], ["new", "목록 새로 만들기"]];
  const segments = segChoice(`link-${kind}-mode`, mode, choices);
  let body;
  if (mode === "existing") {
    body = formTable(linkExistingRowsHtml(kind));
  } else {
    body = formTable(fields.map(([_idField, nameField, title]) => {
      const value = p[nameField] || NEW_LIST_DEFAULTS[nameField];
      return fieldRow(nameField, title, value, { hint: "같은 이름이 이미 있으면 그대로 연결해요" });
    }).join(""));
  }
  const skipNote = !cal && p["담임여부"] !== "예"
    ? `<p class="sub">담임이 아니라서 조종례 전달용 목록은 건너뛰어요.</p>` : "";
  const taskRule = cal ? "" : `<span class="section-note">Google Tasks 목록 어디에 등록해도 날짜·시간은 지정하지 않아요</span>`;
  return `<div class="section-h section-head"><span>${cal ? "Calendar" : "Tasks"}</span>${taskRule}</div>
    <div style="margin-bottom:10px">${segments}</div>
    <div class="google-role-fields">${body}</div>${skipNote}`;
}
const NETWORK_STATUS_POLL_MS = 3000;
const NETWORK_WAIT_SHOW_AFTER_SECONDS = 2;
let networkPollTimer = null;
function networkWaitNoticeHtml() {
  // 고정 IP 유선 설정이 남은 노트북이 Wi-Fi로 오면 Windows가 닿지 않는 유선 DNS에 먼저
  // 물어 이름마다 약 10초씩 기다린다(2026-09-05 측정 11.1초). 프로그램은 배경에서 필요한
  // 이름을 미리 찾아 두는데, 그 첫 확인이 끝나기 전에는 Google 연결이 늦어질 수 있다고
  // 알린다. 컴퓨터의 네트워크 설정은 건드리지 않는다(사용자 결정: 고정 IP·유선 우선 유지).
  const n = S.network;
  if (!n || n.state !== "warming" || (n.elapsed_seconds || 0) < NETWORK_WAIT_SHOW_AFTER_SECONDS) return "";
  return `<div class="banner warn" data-network-wait="true"><span>Google에 연결하는 데 시간이 걸리고 있어요. 현재 화면에 표시된 준비 단계를 마칠 때까지 기다려 주세요.</span></div>`;
}
function networkWaitVisible() { return networkWaitNoticeHtml() !== ""; }
function watchNetworkStatus() {
  if (networkPollTimer) { clearTimeout(networkPollTimer); networkPollTimer = null; }
  call("network_status")
    .then((status) => {
      const before = networkWaitVisible();
      S.network = status;
      const waiting = Boolean(status && status.state === "warming");
      if (waiting) networkPollTimer = setTimeout(watchNetworkStatus, NETWORK_STATUS_POLL_MS);
      // 입력 중인 화면을 3초마다 다시 그리지 않는다 — 안내가 나타나거나 사라질 때만 그린다.
      if (before !== networkWaitVisible()) render();
    })
    .catch(() => { S.network = null; });
}
function listsErrorNoticeHtml() {
  if (!S.listsError) return "";
  return `<div class="banner warn"><span>목록을 가져오지 못했어요. 설정에서 다시 점검해 주세요.</span>
    <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>`;
}

/* ---------- 연결 세 탭 — 이름은 짧게, 설명은 아랫줄로 ----------
   전에는 이름 자리에 서비스 나열을 통째로 넣어 12.5px 한 줄에 셋을 늘어놓았고,
   자리가 모자라 끝이 잘렸다 (사용자 결정 2026-07-30). */
const CONNECT_TABS = [
  { tab: "messenger", title: "Brity 메신저", detail: "Calendar · Tasks · Chat" },
  { tab: "attendance", title: "출결", detail: "Sheet · Docs · Tasks" },
  { tab: "ai", title: "AI 프로그램", detail: "선택 기능" },
];
function tabProblemCount(tab) {
  return checkSummary(checksForTab(tab)).bad;
}
function connectTabsHtml() {
  const buttons = CONNECT_TABS.map((entry) => {
    const active = S.connectTab === entry.tab ? " active" : "";
    const count = tabProblemCount(entry.tab);
    const countHtml = `<span class="tab-count" data-tab-count="${entry.tab}"${count ? "" : ' style="display:none"'}>${count || ""}</span>`;
    const detail = entry.tab === "attendance" && !attendanceUiEnabled() ? "곧 제공" : entry.detail;
    return `<button class="connect-tab${active}" data-action="connect-tab" data-tab="${entry.tab}">
      <b class="tab-title">${esc(entry.title)}${countHtml}</b>
      <small class="tab-detail">${esc(detail)}</small></button>`;
  }).join("");
  return `<div class="connect-tabs">${buttons}</div>`;
}
function messengerTabHtml() {
  const locked = !isGoogleReady(S.google);
  if (googleAuthCheckFailed(S.google)) {
    return `<div class="banner warn"><span>${GOOGLE_AUTH_CHECK_MESSAGE}</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>
      <p class="sub" style="margin-top:14px">Google 연결과 로그인 상태는 설정 화면에서 다시 점검해요.</p>`;
  }
  if (S.google && S.google.logged_in && locked) {
    return `<div class="banner warn"><span>${esc(GOEDU_REQUIRED_MESSAGE)}</span>
      <button class="btn-quiet" data-action="goto-settings">Google 로그인 열기</button></div>
      <p class="sub" style="margin-top:14px">설정에서 Google 계정과 권한 승인을 확인해 주세요.</p>`;
  }
  if (locked) {
    return `<div class="banner warn"><span>${esc(FIELD_MESSAGES["google-login"])}</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>
      <p class="sub" style="margin-top:14px">Google 연결과 로그인 상태는 설정 화면에서 확인해요.</p>`;
  }
  const listProgress = S.linkLoading
    ? `<p class="sub">목록을 다시 확인하고 있어요</p>` : "";
  return `<div class="attendance-head"><div>
      <h2>메신저 내용을 어디에 등록할지 정해요</h2>
      <p>Google 연결과 로그인 상태는 설정 화면에서 확인해요.</p>
    </div><button class="btn-tonal" data-action="google-targets-recheck" ${S.linkLoading ? "disabled" : ""}>연결 다시 확인</button></div>
    ${listProgress}
    ${listsErrorNoticeHtml()}
    <div class="connect-section">${linkGroupHtml("cal")}</div>
    <div class="connect-section">${linkGroupHtml("task")}</div>
    <div class="connect-section">${geminiSectionHtml(S.draft.bridge)}</div>`;
}
const ATTENDANCE_SERVICES = [
  { role: "출석부", name: "Google Sheets", logo: "assets/google-sheets.svg", service: "sheet" },
  { role: "미제출 출결서류 안내를 Google Chat으로 보내기", name: "Google Chat", logo: "assets/google-chat.svg", service: "chat" },
  { role: "결석 신고서 자동완성", name: "Google Docs", logo: "assets/google-docs.svg", service: "docs" },
  { role: "조종례시 출결서류 미제출 안내", name: "Google Tasks", logo: "assets/google-tasks.svg", service: "tasks" },
];

let chatStatusReadVersion = 0;
let chatSpacesReadVersion = 0;
let chatStatusPending = null;
let chatSpacesPending = null;
function chatReadContext() {
  // The account and workbook own these results; opening another screen does not.
  return JSON.stringify([googleContextVersion, googleLoginEpoch, accountUiEpoch, verifiedGoogleAccount(S.google),
    S.attendance?.spreadsheet_id || S.attendance?.spreadsheet_url || S.attendance?.connection_code || "",
    S.attendance?.account || "", S.attendance?.attendance_scope?.subjectKey || "",
    S.attendance?.attendance_scope?.currentSchoolYear || "", S.attendance?.attendance_scope?.generation || 0]);
}
function chatStatusReading() {
  return Boolean(chatStatusPending?.active && chatStatusPending.context === chatReadContext()
    && chatStatusPending.version === chatStatusReadVersion);
}
function classRoomReadiness() {
  if (!isHomeroomTeacher()) return "not-applicable";
  if (!isGoogleReady(S.google)) return "account-required";
  const context = chatReadContext(), cs = S.chatStatus;
  const account = String(S.attendance?.account || S.attendance?.current_user || "").trim().toLowerCase();
  if (S.attendanceReadFailed) return "read-failed";
  if (S.attendance?.state !== "ready"
      || (account && account !== verifiedGoogleAccount(S.google))) return "unverified";
  if (S.attendanceLoading || S.classSpaceSaving || chatStatusReading() || homeClassRoomReading()) return "loading";
  if (!cs || cs === "loading" || S.chatStatusContext !== context) return "unverified";
  if (cs.read_failed || typeof cs.connected !== "boolean") return "read-failed";
  if (!cs.connected) return "account-required";
  if (S.chatSpacesLoading) return "loading";
  if (S.chatSpacesContext !== context) return "unverified";
  if (S.chatSpacesError) return "read-failed";
  if (!Array.isArray(S.chatSpaces)) return "unverified";
  if (!S.chatSpaces.length) return "empty";
  if (!cs.class_space_id) return "not-selected";
  return S.chatSpaces.some(room => room.name === cs.class_space_id) ? "ready" : "missing";
}
function classRoomReadinessMessage(state = classRoomReadiness()) {
  return {
    ready: "학급 단톡방 연결을 확인했어요.",
    "account-required": "Google Chat의 연결과 권한을 확인해 주세요.",
    unverified: "학급 단톡방 연결을 아직 확인하지 못했어요.",
    loading: "학급 단톡방 연결을 확인하고 있어요.",
    "read-failed": "학급 단톡방 연결을 읽지 못했어요. 기존 단톡방 선택은 그대로예요.",
    "not-selected": "출결 탭의 목록에서 사용할 학급 단톡방을 골라 주세요.",
    empty: "이 계정에서 참여 중인 스페이스가 없어요. [단톡방(스페이스) 만들기]에서 만든 뒤 돌아와 주세요.",
    missing: "저장된 학급 단톡방이 현재 목록에서 확인되지 않아요. 기존 선택은 그대로예요. 출결 탭에서 확인해 주세요.",
  }[state] || "";
}
function loadChatStatus(force, renderResult = true) {
  if (S.attendanceConnection) return Promise.resolve(null);
  if (S.chatStatusContext !== chatReadContext()) S.chatStatus = null;
  if (chatStatusPending?.active && chatStatusPending.context === chatReadContext()) return chatStatusPending.promise;
  if (!force && S.chatStatus != null) return chatStatusPending?.context === chatReadContext() ? chatStatusPending.promise : undefined;
  if (S.chatStatus == null) S.chatStatus = "loading";
  const context = S.chatStatusContext = chatReadContext();
  const version = ++chatStatusReadVersion;
  const current = () => version === chatStatusReadVersion && context === chatReadContext();
  const request = beginIssueRequest(true);
  const promise = call("attendance_chat_status")
    .then((data) => {
      if (!current()) return;
      chatStatusPending.active = false;
      S.chatStatus = data?.read_failed || typeof data?.connected !== "boolean"
        ? { ...(typeof S.chatStatus === "object" ? S.chatStatus : {}), connected: null, read_failed: true }
        : data;
      if (!S.chatStatus.read_failed) clearResolvedReadIssue("attendance_chat_status");
      if (renderResult) render();
      return data;
    })
    .catch((error) => {
      if (!current()) return;
      chatStatusPending.active = false;
      S.chatStatus = { ...(typeof S.chatStatus === "object" ? S.chatStatus : {}), connected: null, read_failed: true };
      if (ownsIssueRequest(request) && showProblemIssue(error, request)) return;
      if (renderResult) render();
    });
  chatStatusPending = { context, version, promise, active: true };
  return promise;
}
/* 연결하기 뒤 재확인 — 출결 탭에 있는 동안 3초 간격, 최대 10분 */
let chatPollTimer = null;
let chatPollUntil = 0;
let chatPollGen = 0;
function stopChatConnectPoll() {
  chatPollGen += 1;
  if (chatPollTimer) { clearTimeout(chatPollTimer); chatPollTimer = null; }
}
function startChatConnectPoll(waitForClassSpace = false) {
  stopChatConnectPoll();
  const gen = chatPollGen;
  const context = chatReadContext();
  chatPollUntil = Date.now() + 10 * 60 * 1000;
  const stillOnAttendanceTab = () => S.connectTab === "attendance" &&
    ((S.mode === "edit" && S.edit === "connect") ||
      (S.mode === "wizard" && WIZARD_CARD_BY_STEP[S.step] === "connect"));
  const tick = async () => {
    chatPollTimer = null;
    if (gen !== chatPollGen || context !== chatReadContext() || !stillOnAttendanceTab() || Date.now() > chatPollUntil) return;
    const request = beginIssueRequest(true);
    try {
      const data = await loadChatStatus(true, false);
      if (gen !== chatPollGen || context !== chatReadContext() || !stillOnAttendanceTab()) return;
      if (data?.connected && !data.read_failed && (!waitForClassSpace || data.class_space_id)) {
        render();
        showToast("Google Chat 연결이 끝났어요");
        return;
      }
    } catch (error) {
      if (gen !== chatPollGen || context !== chatReadContext() || !stillOnAttendanceTab() || !ownsIssueRequest(request)) return;
      if (showProblemIssue(error, request)) {
        stopChatConnectPoll();
        return;
      }
    }
    if (gen !== chatPollGen || !stillOnAttendanceTab()) return;
    render();
    chatPollTimer = setTimeout(tick, 3000);
  };
  chatPollTimer = setTimeout(tick, 3000);
}
/* 마법사 7단계 출결 준비 폴링 — 준비가 도는 동안 attendance_prepare_status를 3초 간격으로
   읽어 진행을 보여주고, 준비됨 뒤에는 attendance_first_setup_status를 3초 간격으로 읽어
   시트의 처음 설정 완료를 자동 확인한다. 완료·실패·탭 이탈이면 스스로 멈춘다. */
// Only resume the already-approved preparation after returning from Google's
// settings. A definite API-disabled rejection may be retried; an uncertain
// write or any other failure must never be replayed by this flow.
let attendancePermissionReturn = null;
function stopAttendancePermissionReturn() {
  if (attendancePermissionReturn?.timer) clearTimeout(attendancePermissionReturn.timer);
  attendancePermissionReturn = null;
}
function attendancePermissionRecord() {
  return S.attendance?.spreadsheet_id || S.attendance?.progress?.spreadsheet_id
    || String(S.attendance?.spreadsheet_url || "").match(/\/spreadsheets\/d\/([^/]+)/)?.[1] || "";
}
function ownsAttendancePermissionReturn(flow) {
  return Boolean(flow && flow === attendancePermissionReturn && flow.context === googleReadContext()
    && (!flow.record || !attendancePermissionRecord() || flow.record === attendancePermissionRecord())
    && !S.attendanceTransitioning && !S.attendanceConnectionBusy && !S.attendanceScriptUpdating
    && isGoogleReady(S.google) && S.connectTab === "attendance"
    && ((S.mode === "wizard" && S.step === 8) || (S.mode === "edit" && S.edit === "connect")));
}
async function openAttendanceScriptSettings() {
  if (attendancePermissionReturn?.phase === "opening") return;
  stopAttendancePermissionReturn();
  const flow = S.attendance?.state === "script-permission-required"
    ? { context: googleReadContext(), record: attendancePermissionRecord(), phase: "opening", blurred: false, attempts: 0, timer: null } : null;
  attendancePermissionReturn = flow;
  render();
  try {
    await call("open_attendance_script_settings");
    if (!ownsAttendancePermissionReturn(flow)) return;
    flow.phase = "waiting";
    render();
    if (flow.blurred && document.hasFocus()) await resumeAttendanceAfterPermission(flow);
  } catch (error) {
    if (attendancePermissionReturn === flow) stopAttendancePermissionReturn();
    render();
    throw error;
  }
}
async function resumeAttendanceAfterPermission(flow) {
  if (!ownsAttendancePermissionReturn(flow) || flow.phase === "checking"
      || flow.phase === "opening" || S.attendance?.state !== "script-permission-required") return;
  if (flow.timer) { clearTimeout(flow.timer); flow.timer = null; }
  flow.phase = "checking";
  flow.attempts += 1;
  stopAttendancePreparePoll();
  const request = beginIssueRequest(true);
  render();
  try {
    const reply = await call("attendance_prepare_resume");
    if (!ownsAttendancePermissionReturn(flow)) return;
    if (!reply?.started) {
      stopAttendancePermissionReturn();
      setBanner("warn", reply?.reason || "출결 준비를 시작하지 못했어요.");
      return;
    }
    if (S.problemIssue?.actions?.some(action => action.key === "open-script-api-settings")) clearProblemIssue(false);
    S.banner = null;
    S.attendance = { ...S.attendance, state: "installing" };
    startAttendancePreparePoll();
    render();
  } catch (error) {
    if (!ownsAttendancePermissionReturn(flow)) return;
    stopAttendancePermissionReturn();
    handleCaughtError(error, request);
    render();
  }
}
function observeAttendancePermissionResult(data) {
  const flow = attendancePermissionReturn;
  if (!ownsAttendancePermissionReturn(flow) || flow.phase !== "checking" || data?.running) return;
  if (data?.status?.state !== "script-permission-required") {
    stopAttendancePermissionReturn();
    return;
  }
  flow.phase = flow.attempts < 3 ? "propagating" : "waiting";
  if (flow.phase === "propagating") {
    flow.timer = setTimeout(() => {
      flow.timer = null;
      if (document.hasFocus()) resumeAttendanceAfterPermission(flow);
    }, flow.attempts === 1 ? 15000 : 45000);
  }
}
function attendanceScriptPermissionHtml() {
  const flow = ownsAttendancePermissionReturn(attendancePermissionReturn) ? attendancePermissionReturn : null;
  const busy = flow && ["opening", "checking"].includes(flow.phase);
  const message = flow?.phase === "checking" || flow?.phase === "propagating"
    ? "Google의 사용 허용 반영을 확인하고 있어요. 확인되면 다음 준비 단계로 자동으로 이어집니다."
    : flow?.attempts >= 3
    ? "아직 사용 허용을 확인하지 못했어요. Google 설정에서 [Google Apps Script API]가 켜져 있는지 확인해 주세요. 이 창으로 돌아오면 자동으로 다시 확인합니다."
    : "아래 버튼을 눌러 출결 준비에 쓰는 Google 계정의 [Google Apps Script API]를 켜 주세요. 이 창으로 돌아오면 다음 준비 단계로 자동으로 이어집니다.";
  return `<div class="banner warn attendance-script-permission" role="status"><span>${esc(message)}</span>
    <button class="btn" data-action="attendance-script-settings"${busy ? " disabled" : ""}>Apps Script API 사용</button></div>`;
}
window.addEventListener("blur", () => {
  if (ownsAttendancePermissionReturn(attendancePermissionReturn)) attendancePermissionReturn.blurred = true;
});
window.addEventListener("focus", () => {
  const flow = attendancePermissionReturn;
  if (!ownsAttendancePermissionReturn(flow) || !flow.blurred || flow.phase === "opening") return;
  flow.blurred = false;
  if (flow.phase === "waiting") flow.attempts = 0;
  resumeAttendanceAfterPermission(flow);
});
let attendancePrepareTimer = null;
let attendancePrepareGen = 0;       // 늦게 도착한 옛 폴 결과가 새 화면을 덮지 않게 한다
let attendancePreparePollOn = false;
function stopAttendancePreparePoll() {
  attendancePrepareGen += 1;
  attendancePreparePollOn = false;
  if (attendancePrepareTimer) { clearTimeout(attendancePrepareTimer); attendancePrepareTimer = null; }
}
function startAttendancePreparePoll(flow = null) {
  stopAttendancePreparePoll();
  const gen = attendancePrepareGen;
  const owner = screenKey();
  attendancePreparePollOn = true;
  const tick = async () => {
    attendancePrepareTimer = null;
    if (gen !== attendancePrepareGen) return;
    const onTab = owner === screenKey() && S.connectTab === "attendance" &&
      ((S.mode === "wizard" && WIZARD_CARD_BY_STEP[S.step] === "connect") ||
       (S.mode === "edit" && S.edit === "connect"));
    if (!onTab) { attendancePreparePollOn = false; return; }
    let context = chatReadContext();
    const current = () => {
      const valid = gen === attendancePrepareGen && owner === screenKey() && context === chatReadContext();
      if (!valid && gen === attendancePrepareGen) attendancePreparePollOn = false;
      return valid;
    };
    const issueRequest = beginIssueRequest(true);
    const before = JSON.stringify([
      S.attendance, S.firstSetupDone, S.firstSetupConnectionCode, S.firstSetupReadState, S.chatStatus,
    ]);
    let keepPolling = false;
    try {
      let a = S.attendance;
      if (!a || a.state !== "ready") {
        const data = flow?.action ? await call("attendance_prepare_status", flow.action) : await call("attendance_prepare_status");
        if (!current()) return;
        if (data && data.status && typeof data.status === "object") {
          if (flow && !acceptAttendanceActionResult(data.status, flow)) {
            throw new Error("출석부 작업 정보가 일치하지 않아 진행 결과를 확인하지 못했어요. 현재 작업을 다시 확인해 주세요.");
          }
          S.attendance = { ...data.status, preparation_running: data.running === true };
          a = S.attendance;
          context = chatReadContext();
        }
        keepPolling = Boolean(data && data.running);
        observeAttendancePermissionResult(data);
        if (!flow && !keepPolling && a?.initial_preparation_allowed === true) {
          await maybeStartInitialAttendancePreparation(a);
          if (gen !== attendancePrepareGen) return;
        }
        if (flow) await maybeOpenCreatedAttendance(a, flow);
      }
      if (S.mode === "wizard" && a && a.state === "ready" && !attendanceWizardGateOpen()) {
        if (!attendanceSheetSetupDone() && a.initial_setup_required !== true) {
          await loadFirstSetupStatus(true);
          if (!current()) return;
        }
        // Account consent and room selection can finish after the Sheet menu has returned.
        // Read them independently, including when only the Sheet checkpoint is present.
        if (!S.chatStatus?.connected || S.chatStatus.read_failed || !S.chatStatus.class_space_id) {
          await loadChatStatus(true, false);
        }
        if (!current()) return;
        if (hasCurrentFinalIssue(["attendance_chat_status"])) {
          attendancePreparePollOn = false;
          return;
        }
        if (isHomeroomTeacher() && attendanceSheetSetupDone() && (!S.rosterEditor || S.rosterEditor.pending)) await loadAttendanceRosterStatus(true);
        if (!current()) return;
        // A missing roster does not invalidate completed setup or consent. Its
        // own return-to-window check refreshes it when the teacher edits it.
        keepPolling = false; // Further verification is driven by return, consent and save events.
        clearResolvedAttendanceGateBanner();
      }
    } catch (error) {
      if (!current()) return;
      if (flow && S.attendance?.state !== "ready") {
        S.attendance = { ...S.attendance, state: "verification-unavailable", preparation_running: false,
          creation_allowed: false, replacement_allowed: false,
          recovery_action: "reconcile-attendance-operation", detail: error.message };
      }
      if (S.attendance?.state === "ready") S.firstSetupReadState = "unavailable";
      // 문제 화면을 그리는 동안에는 일반 상태 읽기가 끼어들어 안내를 지우지
      // 않게 폴링 소유권을 유지하고, 그린 직후에만 폴링을 끝낸다.
      if (showProblemIssue(error, issueRequest)) {
        stopAttendancePermissionReturn();
        attendancePreparePollOn = false;
        return;
      }
      keepPolling = false;
      setBanner("warn", error.message || "출석부 준비 결과를 확인하지 못했어요. 같은 작업을 다시 확인해 주세요.");
    }
    // 받은 내용이 직전과 같으면 다시 그리지 않는다 — 3초마다 render가 학급 단톡방
    // 이름 입력의 타이핑·포커스를 지우던 문제 (검토 C3).
    if (JSON.stringify([
      S.attendance, S.firstSetupDone, S.firstSetupConnectionCode, S.firstSetupReadState, S.chatStatus,
    ]) !== before) render();
    if (!keepPolling) { attendancePreparePollOn = false; return; }
    attendancePrepareTimer = setTimeout(tick, 3000);
  };
  attendancePrepareTimer = setTimeout(tick, 0);  // 첫 확인은 바로 — 그다음부터 3초 간격
}
/* 방 이름 기본값 — 내 정보의 학교명·담임학년·담임반으로 만든다. 선생님이 고칠 수 있다. */
function defaultClassSpaceName() {
  const saved = S.profileCache || {};
  const draft = S.draft.profile || {};
  const pick = (key) => String(draft[key] || saved[key] || "").trim();
  const room = pick("담임학년") && pick("담임반") ? `${pick("담임학년")}-${pick("담임반")}` : "";
  return [pick("학교명"), room].filter(Boolean).join(" ");
}
function isClassSpaceIssue(issue) {
  return issue && ["attendance_chat_create_space", "attendance_chat_set_space", "attendance_chat_spaces"].includes(issue.operation)
    && (issue.actions || []).every(action => action.key === "chat-space-list");
}
function loadChatSpaces(force = false, renderResult = true) {
  if (S.attendanceConnection) return Promise.resolve(null);
  const context = chatReadContext();
  if (S.chatSpacesContext !== context) {
    S.chatSpaces = undefined;
    S.chatSpaceName = undefined;
    S.chatSpacesLoading = false;
  }
  if (S.chatSpacesLoading) return chatSpacesPending?.context === context ? chatSpacesPending.promise : undefined;
  if (!force && S.chatSpaces !== undefined) return;
  // Saved room identity lives in chatStatus; only current rows are selectable.
  S.chatSpaces = null;
  S.chatSpacesLoading = true;
  S.chatSpacesError = false;
  S.chatSpacesContext = context;
  const version = ++chatSpacesReadVersion;
  const ownsRoomRead = () => version === chatSpacesReadVersion && context === chatReadContext();
  const request = beginIssueRequest(true);
  const promise = call("attendance_chat_spaces")
    .then((rows) => {
      if (!ownsRoomRead()) return;
      if (!Array.isArray(rows)) throw new Error("방 목록 응답을 확인하지 못했어요.");
      S.chatSpaces = rows;
      S.chatSpacesLoading = false;
      clearResolvedReadIssue("attendance_chat_spaces");
      if (renderResult) render();
    })
    .catch((error) => {
      if (!ownsRoomRead()) return;
      S.chatSpaces = [];
      S.chatSpacesLoading = false;
      S.chatSpacesError = true;
      if (ownsIssueRequest(request) && showProblemIssue(error, request)) return;
      if (renderResult) render();
    });
  chatSpacesPending = { context, promise };
  return promise;
}

function classSpaceSubrowHtml(a, view = attendanceViewKind()) {
  if (!isHomeroomTeacher()) return "";
  const content = classSpaceContentHtml(a, view);
  const state = classRoomReadiness();
  const waiting = state === "unverified" && attendancePresentation(a).pending;
  const reading = state === "loading" || (state === "unverified" && S.attendanceLoading) || waiting;
  const connected = state === "ready";
  const label = S.classSpaceSaving ? "저장 중…" : connected ? "연결됨" : waiting ? "출석부 준비 중…" : reading ? "확인 중…" : state === "empty" ? "스페이스 없음" : state === "not-selected" ? "방 선택 필요" : "확인 필요";
  const localIssue = problemIssueOwner === screenKey() && isClassSpaceIssue(S.problemIssue) ? problemPanelHtml(S.problemIssue) : "";
  const open = view === "installation" ? `<button class="btn-tonal" data-action="open-chat-new-space"${attendanceChatOpenBlockReason() ? " disabled" : ""}>${icon("external-link", "small")} 단톡방(스페이스) 만들러 가기</button>` : "";
  const retry = state === "read-failed" && S.chatSpacesError && !S.attendanceReadFailed && !S.chatStatus?.read_failed
    ? `<button class="btn-tonal" data-action="class-space-reload">다시 확인</button>` : "";
  return `<section class="svc-subrow class-space-section">
    <div class="first-setup-head"><b>학급 단톡방</b><span class="chat-space-reload-status">${open}${retry}
      <span class="chat-space-status ${S.classSpaceSaving || reading ? "muted" : connected ? "connected" : "warn"}">${S.classSpaceSaving ? "저장 중…" : label}</span></span></div>
    ${content}${view === "installation" ? studentChatGuideHtml() : ""}${localIssue}</section>`;
}

function classSpaceContentHtml(a, view = attendanceViewKind()) {
  if (S.attendanceLoading) return `<p class="hint">출결 연결 상태를 확인하고 있어요.</p>`;
  if (S.attendanceReadFailed) return `<p class="hint">현재 연결 상태를 읽지 못했어요. 기존 단톡방 선택은 그대로예요.</p>`;
  if (a.state === "initial-setup-required") return `<p class="hint">출석부 설정 후 Google Chat을 연결해 주세요.</p>`;
  if (a.state !== "ready") return view === "installation" ? `<p class="hint">위의 Google Chat 연결을 마치면 방 목록을 불러올 수 있어요.</p>` : "";
  const cs = S.chatStatus;
  if (chatStatusReading()) return `<p class="hint">Google Chat 연결 상태를 확인하고 있어요.</p>`;
  if (S.chatStatusContext !== chatReadContext() || !cs || cs === "loading") return `<p class="hint">Google Chat 연결 상태를 아직 확인하지 못했어요.</p>`;
  if (cs.read_failed) return `<p class="hint">현재 연결 상태를 읽지 못했어요. 기존 단톡방 선택은 그대로예요.</p>`;
  if (!cs.connected) return view === "installation" ? `<p class="hint">위의 Google Chat 연결을 마치면 방 목록을 불러올 수 있어요.</p>` : "";
  if (!connectionRefreshActive()) loadChatSpaces();
  if (S.chatSpaces === null || S.chatSpacesLoading) return `<p class="hint">방 목록을 가져오는 중이에요…</p>`;
  if (S.chatSpacesError) return `<p class="hint">방 목록을 가져오지 못했어요. 기존 선택은 그대로예요.</p>`;
  if (Array.isArray(S.chatSpaces) && !S.chatSpaces.length) return `<p class="hint">이 계정에서 참여 중인 스페이스가 없어요.</p>`;
  const rooms = S.chatSpaces || [];
  const savedOutsideList = cs.class_space_id && !rooms.some(s => String(s.name || "") === String(cs.class_space_id));
  const options = rooms.map(s => `<option value="${esc(s.name)}"${String(cs.class_space_id || "") === String(s.name || "") ? " selected" : ""}>${esc(s.displayName)}</option>`).join("");
  return `<div class="chat-space-existing">
    <select name="class-space-select" aria-label="학급 단톡방" data-action-change="class-space-pick" data-current-space="${esc(cs.class_space_id || "")}"${S.classSpaceSaving ? " disabled" : ""}><option value="">학급 단톡방</option>${options}</select>
    ${savedOutsideList ? `<p class="hint">저장된 단톡방이 현재 목록에 없어요. 기존 선택은 그대로예요.</p>` : ""}</div>`;
}

let attendanceReplacementAttempt = null;
function attendanceReplacementScope(a) {
  const scope = a?.attendance_scope;
  if (a?.replacement_allowed !== true || !["replace-trashed", "replace-unavailable"].includes(a.creation_reason)
      || !a.replacement_previous_spreadsheet_id || !scope?.subjectKey
      || !Number.isInteger(scope.currentSchoolYear) || !Number.isInteger(scope.generation)
      || scope.generation < 0) return null;
  return {
    reason: a.creation_reason,
    ...(a.creation_reason === "replace-unavailable" ? { explicitConfirmation: true, failureCode: scope.replacement?.failureCode, failureStage: scope.replacement?.failureStage } : {}),
    previousSpreadsheetId: a.replacement_previous_spreadsheet_id,
    ...(scope.replacement?.previousOperationId ? { previousOperationId: scope.replacement.previousOperationId } : {}),
    expectedSchoolYear: scope.currentSchoolYear,
    expectedGeneration: scope.generation,
    subjectKey: scope.subjectKey,
  };
}
function attendanceReplacementContext(a) {
  const scope = attendanceReplacementScope(a);
  return scope ? JSON.stringify([googleReadContext(), scope]) : "";
}
function attendanceReplacementResultIsCurrent(intent, data) {
  const operationId = data?.replacement_operation_id;
  if (!operationId || data.replacement_request_key !== intent.idempotencyKey) return false;
  const belongs = scope => scope?.subjectKey === intent.subjectKey
    && scope.currentSchoolYear === intent.expectedSchoolYear && scope.operationId === operationId;
  const pending = scope => belongs(scope)
    && ["PREPARING", "CREATE_RESULT_UNKNOWN", "RECOVERY_REQUIRED"].includes(scope.bindingState)
    && scope.generation === intent.expectedGeneration && !scope.spreadsheetId
    && scope.operationReason === intent.reason && scope.previousSpreadsheetId === intent.previousSpreadsheetId
    && (scope.previousOperationId || null) === (intent.previousOperationId || null);
  const published = scope => belongs(scope) && scope.bindingState === "ACTIVE"
    && scope.verificationState === "VERIFIED" && scope.generation === intent.expectedGeneration + 1
    && scope.workbookSchoolYear === intent.expectedSchoolYear
    && Boolean(scope.spreadsheetId) && scope.spreadsheetId !== intent.previousSpreadsheetId;
  const result = data.attendance_scope, current = S.attendance?.attendance_scope;
  if (!pending(result) && !published(result)) return false;
  const original = attendanceReplacementScope(S.attendance);
  if (original && Object.keys(original).every(key => original[key] === intent[key])) return true;
  if (pending(current)) return true;
  return published(current) && published(result) && current.spreadsheetId === result.spreadsheetId;
}
let attendanceActionFlow = null;
let attendanceInitialAttempt = null;
function attendanceScopeKey(a) {
  const s = a?.attendance_scope;
  return s ? JSON.stringify([s.subjectKey, s.currentSchoolYear, s.generation, s.spreadsheetId || "", s.operationId || ""]) : "";
}
function attendanceActionMatches(action, a) {
  const s = a?.attendance_scope;
  if (!action || !s || action.subjectKey !== s.subjectKey || action.expectedSchoolYear !== s.currentSchoolYear) return false;
  // A saved replacement request precedes admission: its scope still names
  // the old active workbook, not a newly published replacement.
  if (action.phase === "requested" && !action.operationId && ["replace-trashed", "replace-unavailable"].includes(action.reason)
      && s.bindingState === "ACTIVE") {
    const replacement = s.replacement;
    return replacement?.eligible === true && s.generation === action.expectedGeneration
      && replacement.reason === action.reason
      && s.spreadsheetId === action.previousSpreadsheetId
      && replacement.previousSpreadsheetId === action.previousSpreadsheetId
      && replacement.expectedGeneration === action.expectedGeneration
      && replacement.currentSchoolYear === action.expectedSchoolYear
      && (action.reason === "replace-trashed"
        ? s.verificationState === "ATTENDANCE_FILE_TRASHED" && !action.previousOperationId && !replacement.previousOperationId
        : action.explicitConfirmation === true && Boolean(action.previousOperationId)
          && s.operationId === action.previousOperationId && replacement.previousOperationId === action.previousOperationId
          && s.verificationState === action.failureCode && replacement.failureCode === action.failureCode
          && Boolean(action.failureStage) && replacement.failureStage === action.failureStage);
  }
  if (s.bindingState === "ACTIVE") return s.verificationState === "VERIFIED"
    && s.workbookSchoolYear === action.expectedSchoolYear && s.generation === action.expectedGeneration + 1
    && Boolean(action.operationId) && s.operationId === action.operationId
    && s.spreadsheetId === action.publishedSpreadsheetId && s.generation === action.publishedGeneration
    && (!["replace-trashed", "replace-unavailable"].includes(action.reason) || s.spreadsheetId !== action.previousSpreadsheetId);
  return s.generation === action.expectedGeneration && (!action.operationId || s.operationId === action.operationId)
    && (!["replace-trashed", "replace-unavailable"].includes(action.reason) || !action.operationId
      || (s.operationReason === action.reason && s.previousSpreadsheetId === action.previousSpreadsheetId));
}
function acceptAttendanceActionResult(data, flow) {
  const action = data?.attendance_action;
  if (!flow.active || flow.account !== googleReadContext() || flow.owner !== screenKey() || !action
      || action.provenance !== "saved-request" || action.origin !== flow.origin
      || !attendanceActionMatches(action, data)) return false;
  if (flow.before && (action.subjectKey !== flow.before.subjectKey
      || action.expectedSchoolYear !== flow.before.currentSchoolYear
      || action.expectedGeneration !== flow.before.generation)) return false;
  if (flow.action && (flow.action.idempotencyKey !== action.idempotencyKey
      || (flow.action.operationId && flow.action.operationId !== action.operationId))) return false;
  if (flow.requestKey && flow.requestKey !== action.idempotencyKey) return false;
  if (flow.replacementIntent && (action.reason !== flow.replacementIntent.reason
      || action.previousSpreadsheetId !== flow.replacementIntent.previousSpreadsheetId
      || (action.previousOperationId || null) !== (flow.replacementIntent.previousOperationId || null))) return false;
  if (flow.beforeKey !== attendanceScopeKey(S.attendance) && !attendanceActionMatches(action, S.attendance)) return false;
  flow.action = action;
  return true;
}
function attendanceExpectedWorkbookOpen(a) {
  const s = a?.attendance_scope;
  // Opening the server-confirmed file does not require AI/Chat setup to finish.
  // Display URLs and former PC records never choose this navigation target.
  if (!s || s.bindingState !== "ACTIVE" || s.verificationState !== "VERIFIED"
      || !s.subjectKey || !s.spreadsheetId || s.workbookSchoolYear !== s.currentSchoolYear
      || !Number.isInteger(s.generation) || s.generation < 1) return null;
  return { subjectKey: s.subjectKey, currentSchoolYear: s.currentSchoolYear, generation: s.generation,
    operationId: s.operationId || null, spreadsheetId: s.spreadsheetId };
}
async function maybeOpenCreatedAttendance(data, flow) {
  if (!flow || !flow.active || flow.openAttempted || flow.origin !== "explicit-create"
      || flow.account !== googleReadContext() || flow.owner !== screenKey()
      || flow.action?.provenance !== "saved-request" || flow.action.origin !== "explicit-create"
      || !attendanceActionMatches(flow.action, data) || !attendanceActionMatches(flow.action, S.attendance)) return;
  const expected = attendanceExpectedWorkbookOpen(data);
  if (!expected || flow.action.phase !== "published") return;
  flow.openAttempted = true;
  showToast("출석부를 새로 만들었어요");
  try { await call("open_current_attendance", expected, true); }
  catch (error) {
    if (flow.account === googleReadContext() && flow.owner === screenKey()) setBanner("warn", error.message || "출석부를 열지 못했어요. 설정 버튼을 눌러 주세요.");
  }
}
async function maybeStartInitialAttendancePreparation(a) {
  if (S.mode !== "wizard" || S.step !== 8 || S.connectTab !== "attendance" || !isGoogleReady(S.google)
      || S.attendanceReadFailed || a !== S.attendance || a?.initial_preparation_allowed !== true
      || a.attendance_scope?.bindingState !== "UNBOUND_CONFIRMED" || a.attendance_scope.verificationState !== "VERIFIED"
      || a.creation_allowed !== true) return;
  const key = JSON.stringify([googleReadContext(), attendanceScopeKey(a)]);
  if (attendanceInitialAttempt?.key === key) return;
  const flow = { key, origin: "initial-auto", account: googleReadContext(), owner: screenKey(), active: true,
    before: a.attendance_scope, beforeKey: attendanceScopeKey(a), action: null, requesting: true };
  attendanceInitialAttempt = attendanceActionFlow = flow;
  render();
  try {
    const reply = await call("attendance_prepare_start");
    if (flow.account !== googleReadContext() || flow.owner !== screenKey()) return;
    const data = reply?.status;
    if (data && acceptAttendanceActionResult(data, flow)) {
      S.attendance = { ...data, preparation_running: reply.started === true };
      if (["preparing", "installing"].includes(data.state)) startAttendancePreparePoll(flow);
    } else if (data) {
      // A denied admission may return a newer diagnosis, never permission to make a second request.
      if (attendanceScopeKey(S.attendance) === flow.beforeKey) S.attendance = data;
    } else setBanner("warn", reply?.reason || "출석부 준비 결과를 확인하지 못했어요. 현재 작업을 다시 확인해 주세요.");
  } catch (error) {
    if (flow.account === googleReadContext() && flow.owner === screenKey()) setBanner("warn", error.message || "출석부 준비 결과를 확인하지 못했어요.");
  } finally {
    flow.requesting = false;
    if (flow.account === googleReadContext() && flow.owner === screenKey()) render();
  }
}
function attendancePresentation(a) {
  const flow = attendanceActionFlow?.account === googleReadContext() && attendanceActionFlow?.owner === screenKey()
    && (attendanceActionFlow.beforeKey === attendanceScopeKey(a) || attendanceActionMatches(attendanceActionFlow.action, a)) ? attendanceActionFlow : null;
  const action = a?.attendance_action || flow?.action;
  const initial = !attendanceReplacementScope(a) && (action?.origin === "initial-auto" || flow?.origin === "initial-auto"
    || (attendanceViewKind() === "installation" && a?.initial_preparation_allowed === true));
  const pendingState = ["preparing", "installing"].includes(a?.state);
  const running = pendingState && (a?.preparation_running === true || a?.attendance_scope?.preparationRunning === true)
    && !["requested", "blocked", "reconciling"].includes(action?.phase);
  const requesting = Boolean(flow?.requesting || S.attendanceTransitioning
    || (a?.preparation_running === true && action?.phase === "requested"));
  if (requesting || running) return { phase: "pending", tone: "muted", initial,
    text: initial ? "출석부 준비 중…" : running ? "만드는 중…" : "요청 중…", pending: true };
  if (pendingState && S.attendanceLoading) return {phase:"checking", tone:"muted", text:"결과 확인 중…", initial, pending:true};
  if (pendingState) return { phase: "required", tone: "warn", text: "확인 필요", initial };
  if (S.attendanceLoading || a?.state === "checking") return {phase:"checking", tone:"muted", text:"확인 중…", initial};
  if (a?.account_authorization_required) return {phase:"required", tone:"warn", text:"권한 승인 필요", initial};
  if (S.attendanceReadFailed || ["verification-unavailable", "recovery-required", "binding-required"].includes(a?.state)) {
    return {phase:"required", tone:"warn", text:a?.creation_allowed ? "생성 필요" : "확인 필요", initial};
  }
  if (a?.state === "initial-setup-required" || a?.initial_setup_required === true) return {phase:"setup", tone:"warn", text:"설정 필요", initial};
  if (a?.state === "ready") return {phase:"connected", tone:"ok", text:"연결됨", initial};
  return {phase:"required", tone:"warn", text:"생성 필요", initial};
}
function attendanceInstallationHasExplicitCreation(a) {
  const scope = a?.attendance_scope;
  return Boolean(attendanceReplacementScope(a) || a?.creation_reason === "new-school-year"
    || (a?.creation_allowed === true && scope?.initialPreparationAllowed === false
      && scope.bindingState === "UNBOUND_CONFIRMED" && scope.verificationState === "VERIFIED")
    || (a?.attendance_action?.origin === "explicit-create" && attendanceActionMatches(a.attendance_action, a)));
}
function serviceOpenButtonHtml(entry, a, view = attendanceViewKind()) {
  if (entry.service !== "sheet") return "";
  const choose = `<button class="btn-tonal" data-action="attendance-select-open"${!isGoogleReady(S.google) || S.attendanceTransitioning ? " disabled" : ""}>사용할 출석부 선택</button>`;
  const presentation = attendancePresentation(a);
  const allowed = a.creation_allowed === true || Boolean(attendanceReplacementScope(a));
  if (view === "installation" && (presentation.initial
      || !attendanceInstallationHasExplicitCreation(a)
      || (!allowed && !presentation.pending))) return choose;
  const enabled = allowed && !S.attendanceTransitioning && !S.attendanceLoading && !S.attendanceReadFailed;
  return choose + `<button class="btn-tonal" data-action="new-attendance-go" data-busy-text="요청 중…"${enabled && !presentation.pending ? "" : " disabled"}>${presentation.pending ? presentation.text : "출석부 새로 만들기"}</button>`;
}

function attendanceViewKind() {
  // Explicit navigation context, independent of cloud setup completion.
  return S.mode === "wizard" ? "installation" : "management";
}
function renderInstallationAttendance(a) {
  return `<div class="promise attendance-installation" data-attendance-view="installation">${ATTENDANCE_SERVICES.map(entry => attendanceServiceRow(entry, a, "installation")).join("")}</div>`;
}
function renderManagedAttendance(a) {
  return `<div class="promise attendance-management" data-attendance-view="management">${ATTENDANCE_SERVICES.map(entry => attendanceServiceRow(entry, a, "management")).join("")}</div>`;
}
function attendanceHasConnectedWorkbook(a) {
  return a?.state === "ready" || a?.state === "initial-setup-required";
}
function serviceSubrowsHtml(entry, a, view = attendanceViewKind()) {
  if (entry.service === "chat") return classSpaceSubrowHtml(a, view);
  if (entry.service === "sheet" && attendanceHasConnectedWorkbook(a)) return firstSetupCardHtml(a, view) + studentRosterSubrowHtml(a, view);
  return "";
}
function attendanceTransitionNeedsAttention(state) {
  return [
    "connection-repair-required", "ai-action-required",
  ].includes(state);
}
function serviceStatusHtml(entry, a) {
  const presentation = attendancePresentation(a);
  if (presentation.pending) return `<span class="svc-status muted">${entry.service === "sheet" ? presentation.text : presentation.phase === "waiting" ? "출석부 확인 대기" : "출석부 준비 중…"}</span>`;
  if (["preparing", "installing"].includes(a.state)) return `<span class="svc-status warn">${entry.service === "sheet" ? "확인 필요" : "출석부 준비 필요"}</span>`;
  if (S.attendanceLoading) return `<span class="svc-status muted">확인 중…</span>`;
  if (S.attendanceReadFailed) return `<span class="svc-status warn">확인 필요</span>`;
  if (a.state === "checking") return `<span class="svc-status muted">확인 중…</span>`;
  if (["verification-unavailable", "recovery-required", "binding-required"].includes(a.state)) return `<span class="svc-status warn">확인 필요</span>`;
  if (a.account_authorization_required) return `<span class="svc-status warn">권한 승인 필요</span>`;
  const scriptCheck = a.state === "script-check-required";
  const scriptUpdate = a.state === "script-update-required";
  const scriptAttention = scriptCheck || scriptUpdate;
  if (scriptAttention) return `<span class="svc-status" aria-hidden="true"></span>`;
  if (a.state === "connection-repair-required") {
    return `<span class="svc-status warn">${entry.service === "sheet" ? "복구 확인 필요" : "출석부 연결 후 사용 가능"}</span>`;
  }
  if (attendanceTransitionNeedsAttention(a.state)) {
    return `<span class="svc-status warn">다음 단계 확인 필요</span>`;
  }
  if (["script-recovery-required", "script-permission-required"].includes(a.state)
      || (a.state === "failed" && Object.keys(a.progress || {}).length)) {
    return `<span class="svc-status warn">${a.state === "script-permission-required" ? "권한 승인 필요" : "확인 필요"}</span>`;
  }
  if (presentation.phase === "setup" && entry.service !== "sheet") return `<span class="svc-status warn">출석부 설정 필요</span>`;
  if (entry.service === "chat") {
    const cs = S.chatStatus;
    if (a.state === "ready" && chatStatusReading()) {
      return `<span class="svc-status muted">확인 중…</span>`;
    }
    if (a.state === "ready" && (S.chatStatusContext !== chatReadContext() || !cs || cs === "loading"
        || cs.read_failed || hasCurrentFinalIssue(["attendance_chat_status"]))) {
      return `<span class="svc-status warn">확인 필요</span>`;
    }
    if (a.state === "ready" && cs && cs !== "loading" && cs.connected) {
      return `<span class="svc-status ok">연결됨</span>`;
    }
    if (a.state === "ready" && cs && cs.moved) {
      return `<span class="svc-status warn">이동됨</span>`;
    }
    if (a.state === "ready") {
      return `<span class="svc-status warn">연결 필요</span>`;
    }
    return `<span class="svc-status warn">출석부 준비 필요</span>`;
  }
  if (entry.service === "sheet") {
    if (attendanceHasConnectedWorkbook(a) && !a.year_mismatch) return `<span class="svc-status ok">연결됨</span>`;
    if (a.state === "ready" && a.year_mismatch) return `<span class="svc-status warn">준비 필요</span>`;
    if (a.state === "failed" && a.failed_service === entry.service) return `<span class="svc-status bad">준비 실패</span>`;
    return `<span class="svc-status warn">${presentation.text}</span>`;
  }
  // docs · tasks
  if (a.state === "ready") return `<span class="svc-status ok">연결됨</span>`;
  if (a.state === "failed" && a.failed_service === entry.service) return `<span class="svc-status bad">준비 실패</span>`;
  return `<span class="svc-status warn">출석부 준비 필요</span>`;
}
function attendanceChatOpenBlockReason() {
  if (!S.google) return "Google 로그인을 확인하고 있어요.";
  if (!isGoogleReady(S.google)) return "설정에서 Google 로그인과 권한 승인을 먼저 마쳐 주세요.";
  if (S.attendance?.account && S.attendance.account.toLowerCase() !== String(S.google.user || "").toLowerCase()) {
    return "출결 자료를 만든 Google 계정으로 로그인해 주세요.";
  }
  return "";
}
function attendanceChatConnectBlockReason(a = S.attendance) {
  const loginReason = attendanceChatOpenBlockReason();
  if (loginReason) return loginReason;
  if (S.chatConnectOpening) return "Google 권한 승인 창을 열고 있어요.";
  if (S.attendanceLoading || !a || a.state === "checking") return "출결 자료의 준비 상태를 확인하고 있어요.";
  if (S.attendanceReadFailed) return "출결 연결 상태를 확인하지 못했어요. 다시 확인해 주세요.";
  const presentation = attendancePresentation(a);
  if (presentation.phase === "pending") return "출석부 준비가 끝나면 연결할 수 있어요.";
  if (presentation.phase === "waiting") return "기존 출석부 준비 결과를 확인한 뒤 연결할 수 있어요.";
  if (["preparing", "installing"].includes(a.state)) return "출석부 준비에 필요한 확인을 먼저 마쳐 주세요.";
  if (S.attendanceSaving) return "출결 자료의 저장이 끝나면 연결할 수 있어요.";
  if (S.attendanceScriptUpdating || S.attendanceTransitioning) return "출결 자료를 변경하고 있어요. 끝나면 연결할 수 있어요.";
  if (["script-check-required", "script-update-required"].includes(a.state)) return "위의 출결 기능 확인·업데이트를 먼저 마쳐 주세요.";
  if (a.state !== "ready") return "출결 자료 준비를 먼저 마쳐 주세요.";
  const cs = S.chatStatus;
  if (chatStatusReading()) return "Google Chat 연결 상태를 확인하고 있어요.";
  if (S.chatStatusContext !== chatReadContext() || !cs || cs === "loading") return "Google Chat 연결 상태를 아직 확인하지 못했어요.";
  if (cs.read_failed || hasCurrentFinalIssue(["attendance_chat_status"]) || typeof cs.connected !== "boolean") return "Google Chat 연결 상태를 확인하지 못했어요.";
  if (cs.moved) return "출결 연결이 바뀌었어요. 현재 출결 자료를 먼저 확인해 주세요.";
  if (cs.connected) {
    if (S.mode === "wizard" && !attendanceSheetSetupDone()) return "Google Chat 연결은 끝났어요. 위 출석부에서 [처음 설정 한 번에 끝내기]를 마쳐 주세요.";
    if (S.mode === "wizard" && !cs.class_space_id) return "계정 연결은 끝났어요. 아래 학급 단톡방의 [단톡방(스페이스) 만들기]에서 학생을 초대하고 방을 골라 주세요.";
    return "자동발송 계정이 연결됐어요. 다시 연결할 필요가 없어요.";
  }
  // First-time Sheet setup, student invitations and a selected room are not
  // prerequisites for starting Chat authorization.
  return "";
}
function attendanceServiceRow(entry, a, view = attendanceViewKind()) {
  let chatActs = "";
  let chatHint = "";
  if (entry.service === "chat") {
    const reason = attendanceChatConnectBlockReason(a);
    const connected = S.chatStatusContext === chatReadContext() && S.chatStatus?.connected && !S.chatStatus.read_failed;
    const connect = connected ? "" : `<button class="btn-tonal" data-action="chat-connect" data-busy-text="여는 중…"${reason ? ' disabled aria-describedby="attendance-chat-action-hint"' : ""}>${icon("external-link", "small")} 연결(권한 승인)하러 가기</button>`;
    chatActs = connect + (S.chatStatus?.read_failed ? `<button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button>` : "");
    const hint = connected ? "" : reason || S.chatStatus?.reason || "";
    chatHint = hint ? `<p class="hint attendance-chat-action-hint" id="attendance-chat-action-hint">${esc(hint)}</p>` : "";
  }
  const note = (a.state === "failed" && a.failed_service === entry.service)
    ? `<span class="field-error">${esc(a.detail || "")}</span>`
    : (entry.service === "chat" && S.chatStatus && S.chatStatus.moved)
      ? `<span class="field-error">${esc(S.chatStatus.reason || "")}</span>` : "";
  const connectionCode = entry.service === "sheet" && S.mode !== "wizard" && a.connection_code
    ? `<small class="attendance-connection-code">연결 확인번호 ${esc(a.connection_code)}</small>`
    : "";
  const layoutNotice = entry.service === "sheet" && a.layout_check === "unavailable"
    ? `<div class="hint action-line"><span>출석부의 현재 서식을 읽지 못했어요. 연결 상태를 다시 확인해 주세요.</span><button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button></div>` : "";
  return `<div class="svc-group" data-service="${entry.service}">
    <div class="attendance-service">
      <img class="service-logo" src="${entry.logo}" alt="${esc(entry.name)} 로고">
      <span class="nameblock"><b>${esc(entry.role)}</b><small>${esc(
        entry.service === "sheet" && a.workbook_name ? a.workbook_name : entry.name)}</small>${connectionCode}${note}</span>
      <span class="svc-acts">${serviceOpenButtonHtml(entry, a, view)}${chatActs}</span>
      ${serviceStatusHtml(entry, a)}</div>${layoutNotice}${chatHint}
    ${serviceSubrowsHtml(entry, a, view)}</div>`;
}
let attendanceBoundaryCheck = null;
function attendanceViewVisible() {
  return S.connectTab === "attendance" && ((S.mode === "edit" && S.edit === "connect")
    || (S.mode === "wizard" && S.step === 8));
}
function stopAttendanceBoundaryCheck() {
  if (attendanceBoundaryCheck) clearTimeout(attendanceBoundaryCheck.timer);
  attendanceBoundaryCheck = null;
}
function scheduleAttendanceBoundaryCheck(a) {
  // Only server timestamps determine the delay. The PC calendar never chooses a school year.
  if (!attendanceViewVisible() || !a?.year_verified || S.attendanceLoading || S.attendanceReadFailed) return;
  const scope = a.attendance_scope;
  const serverNow = Date.parse(scope?.serverNow), boundary = Date.parse(scope?.boundaryAt);
  if (!Number.isFinite(serverNow) || !Number.isFinite(boundary) || boundary <= serverNow) return;
  const context = chatReadContext(), key = JSON.stringify([context, scope.boundaryAt]);
  if (attendanceBoundaryCheck?.key === key) return;
  stopAttendanceBoundaryCheck();
  const pending = { key, context, timer: null };
  attendanceBoundaryCheck = pending;
  pending.timer = setTimeout(() => {
    if (attendanceBoundaryCheck !== pending) return;
    attendanceBoundaryCheck = null;
    if (pending.context !== chatReadContext() || !attendanceViewVisible()) return;
    // Refresh only: crossing March 1 never creates or selects a workbook by itself.
    refreshAttendanceStatus();
  }, Math.min(boundary - serverNow, 2147483647));
}
let attendanceStatusReadVersion = 0;
let attendanceStatusReadContext = "";
function refreshAttendanceStatus(followups = true) {
  if (S.attendanceConnection || S.attendanceConnectionBusy) return;
  // 화면에 이미 있는 내용은 그대로 두고 다시 읽는다. 결과가 오면 그때 갈아 끼운다.
  const context = chatReadContext();
  const record = S.attendance;
  const googleContext = googleReadContext();
  const accountContext = googleListContext();
  if (S.attendanceLoading && attendanceStatusReadContext === context) return;
  attendanceStatusReadContext = context;
  const version = ++attendanceStatusReadVersion;
  const current = () => version === attendanceStatusReadVersion && context === chatReadContext() && S.attendance === record;
  S.attendanceLoading = true;
  S.attendanceReadFailed = false;
  return call("attendance_status")
    .then((data) => {
      if (!current()) return;
      S.attendance = data;
      // Carry the pending home check into the workbook just verified by its own
      // attendance request, before that request paints its completion frame.
      if (homeClassRoomPending?.attendanceVersion === version && homeClassRoomPending.context === context) {
        homeClassRoomPending.context = chatReadContext();
      }
      if (context !== chatReadContext()) S.chatStatus = null;
      if (data?.state === "ready") {
        clearResolvedReadIssue("attendance_status");
        if (followups && googleReadContext() === googleContext && S.connectTab === "attendance"
            && (editingCard() === "connect" || (S.mode === "wizard" && S.step === 8))) {
          loadChatStatus(true).then(() => {
            if (googleReadContext() === googleContext && S.chatStatus?.connected && !S.chatStatus.read_failed) {
              loadChatSpaces(true);
              render();
            }
          });
          loadFirstSetupStatus(true);
          loadAttendanceRosterStatus(true);
        }
      }
    })
    .catch(() => { if (current()) S.attendanceReadFailed = true; })
    .finally(() => {
      if (version !== attendanceStatusReadVersion) return;
      S.attendanceLoading = false;
      // A successful reply may itself change the attendance record identity.
      // Closing the connection window does not end this account's read.
      if (googleListContext() === accountContext) {
        render();
        if (followups && googleReadContext() === googleContext && !S.attendanceReadFailed) {
          maybeStartInitialAttendancePreparation(S.attendance);
        }
      }
    });
}
function loadAttendanceStatus() {
  if (S.attendanceTransitioning || S.attendanceConnection || S.attendanceConnectionBusy) return;
  if (S.attendance || S.attendanceLoading || S.attendanceReadFailed) return;
  S.attendanceLoading = true;
  const request = beginIssueRequest(false);
  const context = googleListContext();
  const version = ++attendanceStatusReadVersion;
  let record = S.attendance;
  let readCompleted = false;
  const current = () => version === attendanceStatusReadVersion && context === googleListContext() && S.attendance === record;
  // 켠 직후에는 마지막으로 확인해 둔 상태부터 즉시 보여준다 — "확인하는 중이에요…"를
  // 프로그램을 켤 때마다 보여주지 않는다(사용자 결정 2026-07-30). 저장본을 보여준 뒤에도
  // 실제 확인은 반드시 다시 한다 — 로그인이 풀린 것을 저장본은 모른다.
  call("attendance_status_cached")
    .then((saved) => {
      if (current() && saved && !S.attendance) { S.attendance = record = saved; render(); }
    })
    .catch(() => {});
  call("attendance_status")
    .then((data) => { if (current()) { S.attendance = record = data; readCompleted = true; } })
    .catch((error) => {
      if (!current()) return;
      S.attendanceReadFailed = true;
      if (showProblemIssue(error, request)) return;
      render();
    })
    .finally(() => {
      if (version !== attendanceStatusReadVersion) return;
      S.attendanceLoading = false;
      if (current()) {
        render();
        if (readCompleted) maybeStartInitialAttendancePreparation(S.attendance);
      }
    });
}
function hasCurrentAttendanceFinalIssue(operations) {
  return hasCurrentFinalIssue(operations, ["failed"]);
}
function hasCurrentFinalIssue(operations, states = ["failed", "needs_user"]) {
  if (problemIssueOwner !== screenKey() || !states.includes(S.problemIssue?.state)) return false;
  return operations.includes(String(S.problemIssue.operation || ""));
}
function attendanceScriptUpdateHtml(a) {
  if (!a || !["ready", "script-check-required", "script-update-required"].includes(a.state)) return "";
  if (a.state === "ready") return "";
  if (hasCurrentAttendanceFinalIssue([
    "attendance_script_update_status", "attendance_script_update_apply",
  ])) return `<div class="attendance-script-update warn">
    <button class="btn-tonal" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">출결 기능 다시 확인</button></div>`;
  const update = S.attendanceScriptUpdate;
  if (update?.state === "verification-unavailable") return `<div class="attendance-script-update warn"><span>${esc(update.detail)}</span><button class="btn-tonal" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">다시 확인</button></div>`;
  if (update?.state === "ai-action-required") {
    const detail = String(update.detail || "").trim()
      || "출석부의 [처음 한 번 설정하기]에서 [처음 설정 한 번에 끝내기]를 눌러 주세요.";
    return `<div class="attendance-script-update warn"><span>${esc(detail)}</span>
      ${update.spreadsheet_url ? `<button class="btn-quiet" data-action="attendance-open">출석부 열기</button>` : ""}
      <button class="btn-tonal" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">연결 확인하고 계속</button></div>`;
  }
  if (update?.state === "permission-required") {
    const detail = String(update.detail || "").trim()
      || "출결 기능 업데이트에 필요한 Google 권한을 다시 승인해야 해요. [다시 로그인하고 승인]을 눌러 출석부에 연결한 Google 계정으로 승인해 주세요.";
    return `<div class="attendance-script-update warn"><span>${esc(detail)}</span>
      <button class="btn-tonal" data-action="reauthorize-google">다시 로그인 (권한 승인)</button></div>`;
  }
  if (update?.state === "customized") {
    return `<div class="attendance-script-update warn"><span>${esc(attendanceScriptProtectedMessage(update))}</span>
      <button class="btn-tonal" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">출결 기능 다시 확인</button>
      </div>`;
  }
  if (update?.state === "hold") {
    const detail = String(update.detail || "").trim()
      || "출석부의 자동 처리 기능과 프로그램 버전이 맞는지 확인하지 못했어요. [다시 확인]을 눌러 현재 상태를 확인해 주세요.";
    return `<div class="attendance-script-update warn"><span>${esc(detail)}</span>
      <button class="btn-quiet" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">다시 확인</button></div>`;
  }
  return `<div class="attendance-script-update warn"><span>출석부의 자동 처리 기능과 프로그램 버전이 맞지 않아요.</span>
    <button class="btn-tonal" data-action="attendance-script-update-resolve" data-busy-text="확인 중…">출결 기능 업데이트</button></div>`;
}
function attendanceScriptProtectedMessage(update) {
  // 알려진 안전한 이유만 화면에 옮긴다. 원격 상세 정보는 그대로 표시하지 않는다.
  const reasons = {
    "현재 편집본과 실제 배포 중인 버전이 달라요.": "출석부의 자동 처리 내용이 현재 프로그램 버전과 달라 덮어쓰지 않았어요.",
    "추가한 스크립트 파일이 있어 자동으로 덮어쓰지 않아요.": "출석부의 자동 처리 내용에 추가 파일이 있어 덮어쓰지 않았어요.",
  };
  return Object.prototype.hasOwnProperty.call(reasons, update?.detail)
    ? reasons[update.detail]
    : "출석부의 자동 처리 내용이 현재 프로그램 버전과 달라 덮어쓰지 않았어요.";
}
function attendanceScriptAccountKey() {
  const attendanceAccount = S.attendance?.account || S.attendance?.current_user || "";
  return `${S.google?.user || ""}|${attendanceAccount}`;
}
function clearAttendanceScriptDialogState() {
  attendanceScriptRequestToken += 1;
  S.attendanceScriptDialog = null;
}
function focusAttendanceScriptDialog() {
  const first = document.querySelector(
    '.attendance-update-dialog [data-action="attendance-script-dialog-close"]'
  );
  if (first && first.focus) first.focus();
}
function focusAttendanceScriptResolve() {
  const trigger = document.querySelector('[data-action="attendance-script-update-resolve"]');
  if (trigger && trigger.focus) trigger.focus();
}
function attendanceConnectionFooterHtml(flow) {
  const selected = flow.candidates?.find(row => row.spreadsheet_id === flow.selected_id);
  const disabled = !selected || !selected.can_edit || flow.loading || flow.saving;
  return `${flow.selection_error ? `<p class="field-error" role="alert">${esc(flow.selection_error)}</p>` : ""}
    <div class="attendance-update-dialog-actions">
      <button class="btn-quiet" data-action="attendance-select-cancel"${flow.saving ? " disabled" : ""}>닫기</button>
      <button class="btn-quiet" data-action="attendance-select-reload"${flow.loading || flow.saving ? " disabled" : ""}>목록 새로고침</button>
      <button class="btn-tonal" data-action="attendance-select-preview"${!selected || flow.saving ? " disabled" : ""}>파일 열어보기</button>
      <button class="btn" data-action="attendance-select-confirm"${disabled ? " disabled" : ""}>${flow.saving ? "연결 중…" : "이 출석부로 연결"}</button>
    </div>`;
}
function attendanceConnectionDialogHtml() {
  const flow = S.attendanceConnection;
  if (!flow) return "";
  return `<div class="attendance-update-dialog-overlay"><section class="attendance-update-dialog attendance-selection-dialog" role="dialog" aria-modal="true" aria-label="사용할 출석부 선택">
    <h3>사용할 출석부 선택</h3><p>내 Google Drive에서 원래 사용하던 출석부를 골라 주세요. 이름이 같으면 파일을 열어 내용을 확인할 수 있어요.</p>
    ${flow.loading ? `<p role="status">목록을 불러오는 중…</p>` : `<label class="field">출석부
      <select name="attendance-workbook-choice"${flow.saving ? " disabled" : ""}>
      <option value="">${flow.candidates?.length ? "사용할 파일을 선택하세요" : flow.selection_error ? "목록을 다시 불러와 주세요" : "내 Drive의 시트 파일이 없습니다"}</option>
      ${(flow.candidates || []).map(row => `<option value="${esc(row.spreadsheet_id)}"${row.spreadsheet_id === flow.selected_id ? " selected" : ""}${row.can_edit ? "" : " disabled"}>${esc(row.name)}${row.modified_time ? ` · ${esc(row.modified_time.slice(0, 10))}` : ""}</option>`).join("")}
      </select></label>`}
    <div class="attendance-picker-footer">${attendanceConnectionFooterHtml(flow)}</div>
    </section></div>`;
}
async function openAttendanceConnectionPicker() {
  if (S.attendanceConnection?.saving) return;
  const resumePoll = S.attendanceConnection?.resumePoll || attendancePreparePollOn;
  stopAttendancePreparePoll();
  ++attendanceStatusReadVersion;
  S.attendanceLoading = false;
  const flow = { loading: true, saving: false, selected_id: "", candidates: [], resumePoll, accountContext: googleReadContext() };
  const version = ++attendanceConnectionRequestToken;
  S.attendanceConnection = flow;
  S.attendanceConnectionBusy = true;
  render();
  try {
    const data = await call("attendance_connection_candidates");
    if (S.attendanceConnection !== flow || version !== attendanceConnectionRequestToken || flow.accountContext !== googleReadContext()) return;
    flow.candidates = data.candidates; flow.context = data.context;
  } catch (error) {
    if (S.attendanceConnection === flow) flow.selection_error = error.message || "출석부 목록을 읽지 못했어요.";
  } finally {
    if (S.attendanceConnection === flow) { flow.loading = false; S.attendanceConnectionBusy = false; render(); }
  }
}
bindActions({
  "attendance-select-open": openAttendanceConnectionPicker,
  "attendance-select-reload": openAttendanceConnectionPicker,
  "attendance-select-cancel": () => {
    if (S.attendanceConnection?.saving) return;
    const resumePoll = S.attendanceConnection?.resumePoll;
    const attempted = Boolean(S.attendanceConnection?.request);
    ++attendanceConnectionRequestToken; S.attendanceConnection = null; S.attendanceConnectionBusy = false; render();
    if (attempted) refreshAttendanceStatus();
    else if (resumePoll) startAttendancePreparePoll();
  },
  "attendance-select-preview": async () => {
    const flow = S.attendanceConnection;
    const selected = flow?.candidates?.find(row => row.spreadsheet_id === flow.selected_id);
    if (selected) await call("open_url", selected.spreadsheet_url);
  },
  "attendance-select-confirm": async () => {
    const flow = S.attendanceConnection;
    const selected = flow?.candidates?.find(row => row.spreadsheet_id === flow.selected_id);
    if (!selected?.can_edit || flow.loading || flow.saving || flow.accountContext !== googleReadContext()) return;
    if (!flow.request || flow.request.spreadsheetId !== selected.spreadsheet_id) flow.request = {
      spreadsheetId: selected.spreadsheet_id, key: crypto.randomUUID() };
    flow.saving = true; S.attendanceConnectionBusy = true; delete flow.selection_error; render();
    try {
      const data = await call("select_attendance_connection", selected.spreadsheet_id, flow.context, flow.request.key);
      if (S.attendanceConnection !== flow || flow.accountContext !== googleReadContext()) return;
      if (data.state !== "selected" || data.attendance_scope?.spreadsheetId !== selected.spreadsheet_id) throw new Error("선택한 출석부의 등록 결과를 확인하지 못했어요.");
      S.attendanceConnection = null; S.attendanceConnectionBusy = false;
      S.attendance = { state: "checking", attendance_scope: data.attendance_scope, current_user: data.current_user,
        workbook_name: data.workbook_name, spreadsheet_url: data.spreadsheet_url };
      S.chatStatus = null; S.firstSetupDone = false; S.firstSetupReadState = null; S.firstSetupConnectionCode = "";
      S.attendanceReadFailed = false;
      clearResolvedReadIssue("attendance_status");
      setBanner("ok", `선택한 출석부로 연결했어요: ${data.workbook_name}`);
      await refreshAttendanceStatus();
    } catch (error) {
      if (S.attendanceConnection === flow) {
        flow.selection_error = error.message || "선택한 출석부의 연결을 마치지 못했어요. 같은 선택으로 다시 확인해 주세요.";
        // The server may already have saved this choice before a reply failed.
        S.attendanceReadFailed = true;
      }
    } finally {
      if (S.attendanceConnection === flow) { flow.saving = false; S.attendanceConnectionBusy = false; }
      render();
    }
  }
});
function attendanceScriptUpdateDialogHtml() {
  const kind = S.attendanceScriptDialog;
  if (!kind) return "";
  const title = "출결 기능 업데이트";
  const message = "학생 명단과 출결 기록은 그대로 두고 기능만 최신으로 바꿉니다.";
  const action = "업데이트";
  // 실행 중에는 두 단추 모두 잠근다 — 취소로 빠져나가면 다이얼로그 없이 성공 안내만
  // 뜬금없이 뜨게 된다(실행은 계속 진행 중이라 취소로 멈출 수 없다).
  const busy = S.attendanceScriptUpdating;
  const busyDisabled = busy ? " disabled" : "";
  const actionLabel = busy ? `${action} 중…` : action;
  // 실제 갱신은 Google 호출 40여 건으로 2분 넘게 걸린다(2026-09-03 실측 2분 7초).
  // 걸리는 시간을 미리 말하지 않으면 '업데이트 중…'에서 멈춘 줄 안다.
  const note = busy
    ? "업데이트 중이에요. Google에 새 기능을 올리고 다시 확인하는 데 보통 2~3분 걸려요. 끝날 때까지 이 창을 닫지 말고 기다려 주세요."
    : "Google에 새 기능을 올리고 다시 확인하는 데 보통 2~3분 걸려요. 시작하면 끝날 때까지 이 창을 닫지 말고 기다려 주세요.";
  return `<div class="attendance-update-dialog-overlay">
    <section class="attendance-update-dialog" role="dialog" aria-modal="true" aria-label="${title}">
      <h3>${title}</h3><p>${message}</p>
      <p class="attendance-update-dialog-note">${note}</p>
      <div class="attendance-update-dialog-actions">
        <button class="btn-quiet" data-action="attendance-script-dialog-close"${busyDisabled}>취소</button>
        <button class="btn" data-action="attendance-script-dialog-confirm" data-busy-text="${action} 중…"${busyDisabled}>${actionLabel}</button>
      </div>
    </section></div>`;
}
function firstSetupCardHtml(a, view = attendanceViewKind()) {
  const initialRequired = a.initial_setup_required === true
    && (a.state === "initial-setup-required" || !S.firstSetupReadState);
  if (!initialRequired && !connectionRefreshActive()) loadFirstSetupStatus();
  const complete = attendanceSheetSetupDone();
  const checking = !initialRequired && S.firstSetupReadState === "checking";
  const unavailable = !initialRequired && S.firstSetupReadState === "unavailable";
  const label = checking ? "확인 중…" : unavailable ? "확인 필요" : complete ? "설정 완료" : "설정 필요";
  const setupRequired = initialRequired || (S.firstSetupReadState === "ready" && S.firstSetupReason === "setup_required");
  const open = !complete && !unavailable && setupRequired
    ? `<button class="btn-tonal" data-action="attendance-open">${icon("external-link", "small")} ${view === "installation" ? "처음 한 번 연결하러 가기" : "설정하러 가기"}</button>` : "";
  const retry = unavailable ? `<button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button>` : "";
  const problem = unavailable ? "설정 완료 여부를 읽지 못했어요. 설정을 다시 실행하지 말고 다시 확인해 주세요."
    : !complete && S.firstSetupReason && S.firstSetupReason !== "setup_required" ? firstSetupProblemMessage() : "";
  return `<div class="svc-subrow first-setup-subrow">
    <div class="first-setup-head"><b>${view === "installation" ? "처음 한 번 설정하기" : "출석부 설정 상태"}</b><span class="chat-space-reload-status">${open}${retry}
      <span class="svc-status ${checking ? "muted" : complete ? "ok" : "warn"}">${label}</span></span></div>
    ${problem ? `<p>${esc(problem)}</p>` : ""}
    ${view === "installation" ? `<p class="attendance-picture-guide"><button type="button" class="text-link" data-action="attendance-first-setup-guide" data-preserve-issue="true">설정 방법 그림으로 보기</button></p>` : ""}</div>`;
}
let firstSetupReadContext = "";
let firstSetupReadVersion = 0;
let firstSetupInFlight = "";
async function loadFirstSetupStatus(force = false, background = false) {
  if (S.attendanceConnection) return;
  if ((!background && (S.connectTab !== "attendance" || !((S.mode === "edit" && S.edit === "connect")
      || (S.mode === "wizard" && S.step === 8))))
      || S.attendance?.state !== "ready" || !isGoogleReady(S.google)) return;
  const context = chatReadContext();
  if (firstSetupInFlight === context) return;
  if (!force && firstSetupReadContext === context) return;
  const before = JSON.stringify([S.firstSetupDone, S.firstSetupConnectionCode, S.firstSetupReadState, S.firstSetupReason]);
  const requestScreen = screenKey();
  const keepAcrossScreens = background || S.mode !== "wizard";
  const previousReadState = firstSetupReadContext === context ? S.firstSetupReadState : null;
  if (firstSetupReadContext !== context) {
    S.firstSetupDone = false; S.firstSetupReason = "";
    S.firstSetupConnectionCode = "";
    S.firstSetupReadState = null;
  }
  firstSetupReadContext = context;
  const version = ++firstSetupReadVersion;
  const current = () => version === firstSetupReadVersion && context === chatReadContext()
    && (keepAcrossScreens || requestScreen === screenKey());
  firstSetupInFlight = context;
  S.firstSetupReadState = "checking";
  if (force) render();
  try {
    const first = await call("attendance_first_setup_status");
    if (!current()) return;
    applyFirstSetupRead(first);
  } catch (_) {
    if (!current()) return;
    S.firstSetupReadState = "unavailable";
  } finally {
    if (version === firstSetupReadVersion) {
      firstSetupInFlight = "";
      if (!keepAcrossScreens && context === chatReadContext() && requestScreen !== screenKey()) {
        S.firstSetupReadState = previousReadState;
        if (!previousReadState) firstSetupReadContext = "";
      }
    }
  }
  if (current() && before !== JSON.stringify([S.firstSetupDone, S.firstSetupConnectionCode, S.firstSetupReadState, S.firstSetupReason])) render();
}
let rosterReadVersion = 0;
let rosterContext = "";
let rosterStatus = null;
let rosterInFlight = "";
let rosterAutoSyncAttempt = "";
let rosterAutoSyncPending = null;
function syncPreparedRoster() {
  if (rosterConnecting) return Promise.resolve();
  if (!isHomeroomTeacher() || !attendanceSheetSetupDone()) return Promise.resolve();
  const context = chatReadContext();
  if (rosterAutoSyncPending?.context === context) return rosterAutoSyncPending.promise;
  const pending = { context };
  pending.promise = (async () => {
    if (S.rosterEditor?.context !== rosterEditorContext()) await loadRosterEditor();
    if (context !== chatReadContext()) return;
    const editor = S.rosterEditor;
    if (!editor?.pending || editor.dirty || editor.busy) return;
    const attempt = JSON.stringify([context, editor.revision]);
    if (rosterAutoSyncAttempt === attempt) return;
    rosterAutoSyncAttempt = attempt;
    editor.busy = true;
    try {
      const result = await call("sync_roster_editor");
      if (context !== chatReadContext()) return;
      S.rosterEditor = { ...result, rows: rosterEditableRows(result.rows), context: rosterEditorContext(), dirty: false };
    } catch (_) {
      if (context === chatReadContext()) { editor.state = "unavailable"; editor.detail = "시트 반영 결과를 확인하지 못했어요. 명단 수정에서 다시 확인해 주세요."; }
    } finally { if (context === chatReadContext()) { S.rosterEditor.busy = false; render(); } }
  })().finally(() => { if (rosterAutoSyncPending === pending) rosterAutoSyncPending = null; });
  rosterAutoSyncPending = pending;
  return pending.promise;
}
async function loadAttendanceRosterStatus(force = false, readOnly = false) {
  if (S.attendanceConnection) return;
  if (!readOnly && isHomeroomTeacher() && attendanceSheetSetupDone()) await syncPreparedRoster();
  if (S.attendance?.state !== "ready" || !isGoogleReady(S.google)) return;
  const context = chatReadContext();
  if (rosterInFlight?.context === context) return rosterInFlight.promise;
  if (rosterContext === context && !force && rosterStatus) return;
  rosterStatus = {state:"checking"};
  rosterContext = context;
  const version = ++rosterReadVersion;
  let finishPending;
  const pending = { context, version, promise: new Promise(resolve => { finishPending = resolve; }) };
  rosterInFlight = pending;
  const sheet = S.attendance.spreadsheet_id
    || String(S.attendance.spreadsheet_url || "").match(/\/spreadsheets\/d\/([^/]+)/)?.[1] || "";
  const current = () => version === rosterReadVersion && context === chatReadContext();
  if (force) render();
  try {
    const result = await call("attendance_roster_status");
    if (!current()) return;
    const validCount = Number.isInteger(result?.count) && (result.state === "empty" ? result.count === 0 : result.count > 0);
    rosterStatus = result && sheet && result.spreadsheet_id === sheet && validCount && ["empty","incomplete","ready"].includes(result.state)
      ? result : {state:"unavailable"};
  } catch (_) {
    if (!current()) return;
    rosterStatus = {state:"unavailable"};
  } finally {
    if (rosterInFlight === pending) rosterInFlight = "";
    if (current()) render();
    finishPending();
  }
}
let rosterConnecting = false;
function studentRosterSubrowHtml(a, view = attendanceViewKind()) {
  if (!isHomeroomTeacher()) return "";
  const initialRequired = a.roster_input_required === true;
  if (!initialRequired && !connectionRefreshActive()
      && (rosterContext !== chatReadContext() || !rosterStatus)) loadAttendanceRosterStatus();
  if (view === "installation") {
    const editor = S.rosterEditor?.context === rosterEditorContext() ? S.rosterEditor : null;
    const complete = attendanceSheetSetupDone() && rosterContext === chatReadContext() && rosterStatus?.state === "ready" && !editor?.pending;
    const busy = rosterConnecting || editor?.busy || S.attendanceAccountAuthorizing;
    const detail = editor?.state === "unavailable" || editor?.state === "conflict" ? editor.detail
      : !attendanceSheetSetupDone() ? "출석부 설정을 먼저 완료해 주세요."
      : complete ? "저장한 학생명단을 출석부에서 확인했어요." : "6번에서 저장한 학생명단을 출석부에 연결해 주세요.";
    return `<div class="svc-subrow student-roster-subrow"><div class="first-setup-head"><b>학생명단 연결</b><span class="chat-space-reload-status">
      <button class="btn-tonal" data-action="attendance-roster-connect"${busy || !attendanceSheetSetupDone() || complete ? " disabled" : ""}>${busy ? "연결 중…" : "학생명단 연결"}</button>
      <span class="svc-status ${busy ? "muted" : complete ? "ok" : "warn"}">${busy ? "연결 중…" : complete ? "연결됨" : "연결 필요"}</span></span></div><p>${esc(detail)}</p></div>`;
  }
  const status = rosterContext === chatReadContext() ? rosterStatus : null;
  const state = status?.state === "unavailable" ? "unavailable" : initialRequired ? "empty" : status?.state || "checking";
  const pending = S.rosterEditor?.context === rosterEditorContext() && S.rosterEditor.pending;
  const complete = state === "ready" && !pending;
  const label = pending ? "반영 필요" : complete ? "입력됨" : state === "empty" ? "입력 필요" : state === "incomplete" ? "수정 필요" : state === "unavailable" ? "확인 필요" : "확인 중…";
  let detail = "";
  if (state === "unavailable") detail = "현재 출석부의 학생명단을 확인하지 못했어요. 입력한 명단은 그대로 보관하고 있어요.";
  if (state === "incomplete") {
    const missing = Object.entries(status.missing || {}).filter(([, count]) => count > 0).map(([field, count]) => `${field} ${count}명 누락`);
    if (status.invalid_rows?.length) missing.push(`${status.invalid_rows.join(", ")}행의 번호·이메일 형식 또는 중복 확인`);
    detail = missing.join(" · ");
  }
  if (pending) detail = S.rosterEditor.detail || "저장한 학생명단을 출석부에 반영하고 있어요.";
  return `<div class="svc-subrow student-roster-subrow">
    <div class="first-setup-head"><b>${view === "installation" ? "학생명단 입력" : "학생 명단"}</b><span class="chat-space-reload-status">
      <button class="btn-tonal" data-action="attendance-roster-edit">${view === "installation" ? `${icon("external-link", "small")} 명단 입력하러 가기` : "명단 수정"}</button>
      <span class="svc-status ${complete ? "ok" : state === "checking" ? "muted" : "warn"}">${esc(label)}</span></span></div>
    ${detail ? `<p>${esc(detail)}</p>` : ""}<p class="attendance-picture-guide"><button type="button" class="text-link" data-action="attendance-roster-guide" data-preserve-issue="true">입력 방법 그림으로 보기</button></p></div>`;
}
function openAttendancePictureGuide(kind) { return call("open_picture_guide", kind); }
let attendanceAccountAuthorizationVersion = 0;
bindActions({"attendance-account-authorize": async () => {
  if (S.attendanceAccountAuthorizing) return;
  const version = ++attendanceAccountAuthorizationVersion;
  const context = googleReadContext();
  const current = () => version === attendanceAccountAuthorizationVersion && context === googleReadContext();
  S.attendanceAccountAuthorizing = true;
  render();
  try {
    const started = await call("attendance_account_authorize");
    if (!current()) return;
    if (started?.state !== "pending") throw new Error(started?.detail || "권한 승인 창을 열지 못했어요.");
    const deadline = Date.now() + 180000;
    const poll = async () => {
      if (!current()) return;
      try {
        const result = await call("attendance_account_authorization_status");
        if (!current()) return;
        if (result?.state === "complete") {
          S.attendanceAccountAuthorizing = false;
          const rosterRetry = rosterAuthorizationRetry;
          rosterAuthorizationRetry = null;
          if (rosterRetry && rosterRetry.context === rosterEditorContext() && rosterRetry.owner === screenKey()
              && rosterRetry.revision === S.rosterEditor?.revision && !S.rosterEditor?.dirty
              && (!rosterRetry.explicitConnect || rosterRetry.connection === (attendanceScopeKey(S.attendance)
                || S.attendance?.spreadsheet_id || S.attendance?.spreadsheet_url || ""))) {
            await saveRosterEditor(true, false, rosterRetry.explicitConnect, rosterRetry.useCurrentWorkbook === true);
          } else {
            await refreshAttendanceStatus();
          }
          if (current()) render();
          return;
        }
        if (result?.state !== "pending" || Date.now() >= deadline) {
          S.attendanceAccountAuthorizing = false;
          setBanner("warn", result?.detail || "권한 승인을 확인하지 못했어요. 연결 버튼에서 다시 시작해 주세요.");
          return;
        }
        setTimeout(poll, 3000);
      } catch (_) {
        if (!current()) return;
        S.attendanceAccountAuthorizing = false;
        setBanner("warn", "권한 승인 결과를 읽지 못했어요. 연결 버튼에서 다시 확인해 주세요.");
      }
    };
    setTimeout(poll, 1000);
  } catch (error) {
    if (current()) {
      S.attendanceAccountAuthorizing = false;
      setBanner("warn", error.message || "권한 승인 창을 열지 못했어요.");
    }
  } finally { if (current()) render(); }
}});
bindActions({
  "google-login-guide": () => openAttendancePictureGuide("login"),
  "attendance-first-setup-guide": () => openAttendancePictureGuide("setup"),
  "attendance-chat-space-guide": () => openAttendancePictureGuide("chat"),
  "attendance-roster-guide": () => openAttendancePictureGuide("roster"),
});
bindActions({"attendance-roster-connect": async () => {
  if (rosterConnecting || !attendanceSheetSetupDone()) return;
  const context = rosterEditorContext(), owner = screenKey();
  const current = () => context === rosterEditorContext() && owner === screenKey();
  rosterConnecting = true; render();
  try {
    await loadRosterEditor();
    if (!current()) return;
    rosterAutoSyncAttempt = JSON.stringify([chatReadContext(), S.rosterEditor?.revision]);
    if (await saveRosterEditor(true, true, true)) {
      if (current()) await loadAttendanceRosterStatus(true);
    }
  } finally { rosterConnecting = false; if (current()) render(); }
}});
bindActions({"attendance-roster-edit": async () => {
  if (S.mode === "wizard") { await goStepAsync(6); return; }
  const context = rosterEditorContext();
  await openCard("timetable");
  if (context !== rosterEditorContext() || S.mode !== "edit" || S.edit !== "timetable") return;
  S.timetableTab = "roster"; render();
}});
bindActions({"attendance-roster-open": async () => {
  rosterReadVersion += 1;
  rosterInFlight = "";
  rosterStatus = null;
  render();
  await call("open_attendance_roster");
}});
bindActions({"attendance-existing-repair": async () => {
  if (S.attendanceRepairing || S.attendanceReadFailed || S.attendance?.recovery_action !== "repair-existing") return;
  const context = chatReadContext();
  const screen = screenKey();
  const current = () => context === chatReadContext() && screen === screenKey();
  S.attendanceRepairing = true;
  render();
  try {
    const result = await call("attendance_script_update_apply");
    if (!current()) return;
    if (["updated", "current"].includes(result?.state)) {
      await refreshAttendanceStatus();
    } else {
      setBanner("warn", result?.detail || "기존 출석부의 복구를 마치지 못했어요. 다시 확인해 주세요.");
    }
  } catch (error) {
    if (current()) setBanner("warn", error.message || "기존 출석부의 복구 결과를 확인하지 못했어요.");
  } finally {
    S.attendanceRepairing = false;
    if (screen === screenKey()) render();
  }
}});
bindActions({"attendance-status-recheck": async () => {
  if (S.mode === "edit") return refreshConnectionStatus();
  S.attendanceReadFailed = false;
  await refreshAttendanceWizardGate();
  render();
}});
function attendanceRecoveryHtml(a) {
  // A stopped preparation needs an action even when an old server omitted its diagnostic.
  if (!a?.recovery_action && ["preparing", "installing"].includes(a?.state)
      && !attendancePresentation(a).pending && !attendanceReplacementScope(a)) {
    return `<div class="banner warn attendance-recovery" role="status"><span>출석부 준비가 완료되지 않았어요. 기존 작업의 결과를 다시 확인해 주세요.</span><button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button></div>`;
  }
  const choices = {
    "authorize-attendance-account": ["Google 권한 승인", "출석부 연결 권한을 승인한 뒤 같은 작업을 확인해 주세요."],
    "check-file-access": ["접근 권한 다시 확인", "해당 출석부의 접근 권한을 확인해야 해요. 새 출석부로 바꾸지 않습니다."],
    "retry-verification": ["다시 확인", "현재 출석부의 확인을 마치지 못했어요."],
    "update-attendance-service": ["업데이트 확인", "출석부 확인 서비스의 수정이 필요해요. 같은 Google 권한 승인을 반복해도 해결되지 않습니다."],
    "reconcile-attendance-operation": ["같은 작업 확인", "기존 출석부 준비 작업의 결과를 확인해 주세요."],
    "resume-attendance-preparation": ["같은 작업 이어서 확인", "기존 출석부 준비 작업의 결과를 확인해 주세요."],
  };
  const choice = choices[a?.recovery_action];
  if (!choice) return "";
  return `<div class="banner warn attendance-recovery" role="status"><span>${esc(a.detail || choice[1])}</span>
    <button class="btn-tonal" data-action="attendance-recovery" data-attendance-context="${esc(chatReadContext())}" data-recovery-action="${esc(a.recovery_action)}">${choice[0]}</button></div>`;
}
bindActions({"attendance-recovery": async (el) => {
  if (el?.dataset.attendanceContext !== chatReadContext() || el.dataset.recoveryAction !== S.attendance?.recovery_action) return;
  const action = el.dataset.recoveryAction;
  if (action === "authorize-attendance-account") return actions["attendance-account-authorize"]();
  if (action === "update-attendance-service") return actions["update-check"](el, beginIssueRequest(true));
  if (["reconcile-attendance-operation", "resume-attendance-preparation"].includes(action)) {
    const context = chatReadContext(), owner = screenKey();
    if (!S.attendance?.attendance_action && S.attendance?.attendance_scope?.operationId) {
      const beforeKey = attendanceScopeKey(S.attendance);
      const snapshot = await call("attendance_prepare_status");
      if (context !== chatReadContext() || owner !== screenKey()) return;
      if (!snapshot?.status || attendanceScopeKey(snapshot.status) !== beforeKey) {
        setBanner("warn", "출석부 연결 정보가 바뀌었어요. 현재 상태를 다시 확인해 주세요.");
        return;
      }
      S.attendance = { ...snapshot.status, preparation_running: snapshot.running === true };
      if (snapshot.running) { startAttendancePreparePoll(); render(); return; }
    }
    const scope = S.attendance?.attendance_scope;
    const expected = S.attendance?.attendance_action || (scope?.operationId ? {
      provenance: "legacy-existing-operation", explicitResume: true, subjectKey: scope.subjectKey,
      expectedSchoolYear: scope.currentSchoolYear, expectedGeneration: scope.generation, operationId: scope.operationId,
    } : null);
    const result = await call("attendance_prepare_resume", expected);
    if (context !== chatReadContext() || owner !== screenKey()) return;
    if (result?.status) S.attendance = result.status;
    if (result?.started) startAttendancePreparePoll();
    else if (result?.reason) setBanner("warn", result.reason);
    render();
    return;
  }
  return actions["attendance-status-recheck"]();
}});

function attendanceComingSoonHtml() {
  return `<div class="attendance-head"><div>
    <h2>출결</h2>
    <p>출결 기능은 준비 중이며 곧 제공됩니다.</p>
  </div></div>`;
}
function attendanceTabHtml() {
  // 마법사에서 준비 폴링이 도는 동안에는 같은 상태를 두 경로로 읽지 않는다 —
  // 느린 attendance_status 응답이 폴링이 방금 놓은 새 상태를 옛 값으로 덮는 것을 막는다.
  if (!connectionRefreshActive()) {
    if (!attendancePreparePollOn) loadAttendanceStatus();
    if (S.attendance && S.attendance.state === "ready") loadChatStatus(false);
  }
  const a = S.attendance && typeof S.attendance === "object" ? S.attendance : { state: "checking" };
  scheduleAttendanceBoundaryCheck(a);
  if (attendanceReplacementAttempt?.context !== attendanceReplacementContext(a)) attendanceReplacementAttempt = null;
  const account = a.account || a.current_user || "";
  let statusArea = "";
  const presentation = attendancePresentation(a);
  const recovery = attendanceRecoveryHtml(a);
  if (S.attendanceReadFailed && !presentation.pending) {
    statusArea = `<div class="banner warn" role="status"><span>출석부의 현재 상태를 확인하지 못했어요. [다시 확인]을 눌러 주세요.</span><button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button></div>`;
  } else if (recovery && !presentation.pending) {
    statusArea = recovery;
  } else if (presentation.pending) {
    statusArea = "";
  } else if (a.account_authorization_required) {
    statusArea = `<div class="banner warn attendance-account-consent"><span>출석부 연결을 확인하려면 Google 권한 승인이 필요해요.</span><button class="btn-tonal" data-action="attendance-account-authorize"${S.attendanceAccountAuthorizing ? " disabled" : ""}>${icon("external-link", "small")} ${S.attendanceAccountAuthorizing ? "승인 기다리는 중…" : "연결(권한 승인)하러 가기"}</button></div>`;
  } else if (S.attendanceReadFailed) {
    statusArea = `<div class="banner" role="status"><span>출석부의 현재 상태를 확인하지 못했어요. 잠시 후 [다시 확인]을 눌러 주세요.</span><button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button></div>`;
  } else if (attendanceReplacementScope(a)) {
    statusArea = "";
  } else if (a.state === "account-required") {
    statusArea = `<div class="banner warn"><span>${esc(a.detail || GOEDU_REQUIRED_MESSAGE)}</span>
      <button class="btn-quiet" data-action="goto-settings">Google 로그인 열기</button></div>`;
  } else if (a.state === "login-required" || a.state === "gws-required") {
    statusArea = `<div class="banner warn"><span>설정에서 Google 로그인을 마쳐 주세요</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>`;
  } else if (a.state === "auth-error") {
    statusArea = `<div class="banner warn"><span>${esc(a.detail || GOOGLE_AUTH_CHECK_MESSAGE)}</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>`;
  } else if (["connection-repair-required", "verification-unavailable", "recovery-required", "binding-required"].includes(a.state)) {
    statusArea = `<div class="banner warn"><span>${esc(a.detail || "출석부의 현재 연결을 확인하지 못했어요.")}</span>
      ${a.recovery_action === "repair-existing" ? `<button class="btn-tonal" data-action="attendance-existing-repair"${S.attendanceRepairing ? " disabled" : ""}>${S.attendanceRepairing ? "복구 중…" : "기존 출석부 복구"}</button>` : `<button class="btn-tonal" data-action="attendance-status-recheck">다시 확인</button>`}</div>`;
  } else if (a.state === "profile-required"
      && (identityIssues().length || dayIssues().length)) {
    // 초안이 진짜 비어 있을 때만 안내한다.
    statusArea = `<div class="banner warn"><span>${esc(a.detail || "내 정보와 하루 일과를 먼저 입력해 주세요.")}</span>
      <button class="btn-quiet" data-action="goto-identity">내 정보 열기</button></div>`;
  } else if (a.state === "script-permission-required") {
    statusArea = attendanceScriptPermissionHtml();
  } else if (a.state === "script-recovery-required") {
    statusArea = `<p class="field-error" style="margin:0 0 13px">${esc(a.detail || "출결 자동화 연결을 확인하지 못해 준비를 멈췄어요. 기존 출결 자료는 그대로입니다.")}</p>`;
  } else if (a.state === "failed" && (!a.failed_service || a.failed_service === "setup")) {
    statusArea = `<p class="field-error" style="margin:0 0 13px">${esc(a.detail || "출결 자료를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.")}</p>`;
  }
  const ready = attendanceHasConnectedWorkbook(a);
  const scriptCheckRequired = a.state === "script-check-required";
  const scriptUpdateRequired = a.state === "script-update-required";
  const scriptAttentionRequired = scriptCheckRequired || scriptUpdateRequired;
  const transitionAttentionRequired = attendanceTransitionNeedsAttention(a.state);
  const pendingGuide = presentation.pending ? presentation.text
    : presentation.phase === "checking" ? "출결 준비 상태를 확인하는 중이에요…" : "";
  const chip = account ? `<span class="account-chip">${esc(account)}</span>` : "";
  const rows = attendanceViewKind() === "installation" ? renderInstallationAttendance(a) : renderManagedAttendance(a);
  const staleNotice = attendanceStaleNoticeHtml();
  return `${statusArea}${attendanceScriptUpdateHtml(a)}
    <div class="attendance-head">
      <div><h2>출결 업무에 필요한 Google 항목</h2>${ready || scriptAttentionRequired || a.state === "script-recovery-required" || !pendingGuide ? "" : `<p>${pendingGuide}</p>${presentation.text === "만드는 중…" ? `<small class="muted">출석부 준비에는 약 2~3분이 걸릴 수 있어요. 잠시만 기다려 주세요.</small>` : ""}`}</div>
      <span class="attendance-head-right">${chip}</span>
    </div>
    ${staleNotice}
    ${rows}
    ${S.mode === "wizard" && a.state === "failed" && !hasCurrentAttendanceFinalIssue([
      "ensure_attendance", "attendance_prepare_start", "attendance_first_setup_status",
    ])
      ? `<div class="attendance-action"><button class="btn" data-action="attendance-prepare-retry" data-busy-text="다시 시작하는 중…">다시 시도</button></div>`
      : ""}
    ${attendanceScriptUpdateDialogHtml()}${attendanceConnectionDialogHtml()}`;
}
function attendanceStaleNoticeHtml() {
  if (S.mode !== "wizard" || !S.attendanceStaleNotice
      || typeof S.attendanceStaleNotice !== "object") return "";
  const sheetFields = S.attendanceStaleNotice.sheet_fields || [];
  const localGroups = S.attendanceStaleNotice.local_groups || [];
  const messages = [];
  if (sheetFields.length) {
    messages.push(`기존 출석부의 [설정] 탭에 아직 반영되지 않은 항목: ${sheetFields.join(", ")}.`);
  }
  if (localGroups.length) {
    messages.push(`Teacher Manager에만 저장되고 기존 출석부 값은 바뀌지 않습니다: ${localGroups.join(", ")}.`);
  }
  if (!messages.length) return "";
  return `<p class="hint" style="margin:0 0 12px">${esc(messages.join(" "))} </p>`;
}
/* ---------- 연결 3탭: AI 에이전트 ---------- */
function aiTabHtml() {
  if (!S.info?.features?.ai_skill_install_enabled) {
    return `<div class="attendance-head"><div>
      <h2>AI 프로그램 연결 <span class="tab-optional">(선택)</span></h2>
      <p>다른 AI 프로그램과 연결하는 기능은 준비 중이에요. 이 단계는 건너뛰어도 됩니다.</p>
    </div></div>`;
  }
  if (S.aiTools === null) {
    S.aiTools = "loading";
    const requestToken = ++aiToolsRequestToken;
    const requestScreen = screenKey();
    call("ai_tools_status")
      .then((rows) => {
        if (requestToken === aiToolsRequestToken && requestScreen === screenKey()) S.aiTools = rows;
      })
      .catch(() => {
        if (requestToken === aiToolsRequestToken && requestScreen === screenKey()) S.aiTools = [];
      })
      .finally(() => {
        if (requestToken === aiToolsRequestToken && requestScreen === screenKey()) render();
      });
  }
  if (S.aiTools === "loading" || S.aiTools === null) {
    return `<p class="sub">이 컴퓨터의 AI 도구를 찾는 중이에요…</p>`;
  }
  const anyFound = S.aiTools.some((tool) => tool.found);
  const selected = Array.isArray(S.aiSelected) ? new Set(S.aiSelected) : null;
  const rows = S.aiTools.map((tool) => `
    <label class="ai-row${tool.found ? "" : " off"}">
      <input type="checkbox" name="ai-${esc(tool.key)}" ${tool.found && (!selected || selected.has(tool.key)) ? "checked" : ""} ${tool.found ? "" : "disabled"}>
      <b>${esc(tool.name)}</b>
      <span class="st${tool.found ? " ok" : ""}">${tool.found ? "발견됨" : "설치 안 됨"}</span>
    </label>`).join("");
  let result = "";
  if (S.aiInstall) {
    result = S.aiInstall.success
      ? `<div class="ready-hero"><span class="check">✓</span>
          <span><b>AI 프로그램과 연결했어요.</b> 이제 AI에게 말로 학교 업무를 시킬 수 있어요.</span></div>`
      : `<div class="banner warn" style="margin-top:12px"><span>${esc(S.aiInstall.detail || "자동 연결을 마치지 못했어요. 잠시 뒤 다시 눌러 주세요.")}</span></div>
        <p class="hint">AI 연결 파일을 추가하지 못했어요. 다른 Teacher Manager 기능은 그대로 사용할 수 있어요.</p>`;
  }
  const nodeLine = S.aiNode && S.aiNode.success
    ? `<p class="hint">AI 연결에 필요한 파일을 준비했어요.</p>`
    : "";
  return `<div class="attendance-head"><div>
      <h2>AI 프로그램과 Google을 연결할까요? <span class="tab-optional">(선택)</span></h2>
      <p>연결하면 AI에게 말로 일정·결석·신고서·Google Chat 안내를 만들 수 있어요. 이 기능은 건너뛰어도 됩니다.</p>
    </div></div>
    <div class="ai-rows">${rows}</div>
    ${anyFound && !hasCurrentFinalIssue(["ai_node_status", "ai_node_prepare", "ai_skills_install"])
      ? `<div class="attendance-action"><button class="btn" data-action="ai-connect" data-busy-text="연결하는 중… (1~2분 걸릴 수 있어요)" ${S.aiConnecting ? "disabled" : ""}>${S.aiConnecting ? "연결하는 중…" : "선택한 AI와 연결"}</button></div>`
      : `<p class="hint" style="margin-top:12px">이 컴퓨터에서 AI 도구를 찾지 못했어요. AI 도구를 설치한 뒤 이 탭에 다시 들어오면 돼요.</p>`}
    ${nodeLine}${result}`;
}
bindActions({
  "connect-tab": (el) => {
    const tab = el.dataset.tab;
    if (S.connectTab === tab) return;
    if (editingCard() === "connect" && S.connectTab === "messenger") syncConnectFields();
    if (S.connectTab === "attendance") clearAttendanceScriptDialogState();
    S.banner = null;  // 이전 탭의 안내 배너(게이트 포함)가 새 탭까지 따라가지 않는다 (검토 C2)
    S.connectTab = tab;
    if (tab === "attendance" && attendanceUiEnabled()) {
      if (S.mode === "wizard") {
        // 마법사에서는 준비 폴링이 상태를 읽는다 — 같은 정보를 두 경로로 묻지 않는다.
        startAttendancePreparePoll();
      }
    } else {
      stopChatConnectPoll();
      stopAttendancePreparePoll();
    }
    if (tab === "ai") {
      // AI 도구도 열 때마다 다시 감지 — 그 사이 설치했을 수 있다.
      S.aiTools = null;
      S.aiNode = null;
      S.aiInstall = null;
    } else {
      aiToolsRequestToken += 1;
    }
    render();
  },
  "update-check": async (_el, request) => {
    const update = await call("get_update_info");
    if (!ownsIssueRequest(request)) return;
    S.updateInfo = update && update.available ? update : null;
    S.updateCheck = update && update.status ? update.status : "failed";
    render();
  },
  "update-now": async (_el, request) => {
    if (S.updating) return;  // 다운로드 중 재클릭 방지
    if (!S.updateInfo || !S.updateInfo.available) { showToast("먼저 업데이트를 확인해 주세요"); return; }
    const exactOffer = S.updateInfo;
    S.updating = true; render();
    try {
      // 화면이 이미 아는 주소를 넘겨 재조회 없이 받는다(통신 깜빡임 오안내 방지).
      const result = await call("start_update", exactOffer.url, exactOffer.latest, exactOffer.sha256);
      if (!result.started) throw new Error(result.reason || "업데이트를 시작하지 못했어요.");
      setTimeout(() => { call("quit_app").catch(() => {}); }, 300);
      if (ownsIssueRequest(request)) showToast("설치 파일을 확인했어요. 설치 창을 열게요.");
    } catch (error) {
      S.updating = false;
      if (ownsIssueRequest(request)) render();
      throw error;
    }
  },
  "ai-connect": async (_el, request) => {
    if (S.aiConnecting) return;
    if (!S.info?.features?.ai_skill_install_enabled) {
      showToast("다른 AI 프로그램과 연결하는 기능은 준비 중이에요");
      return;
    }
    const keys = Array.from(document.querySelectorAll('.ai-row input:checked'))
      .map((box) => box.name.replace(/^ai-/, ""));
    if (!keys.length) { showToast("연결할 AI를 하나 이상 선택해 주세요"); return; }
    S.aiSelected = [...keys];
    S.aiConnecting = true;
    S.aiInstall = null;
    render();
    try {
      const nodeStatus = await call("ai_node_status");
      if (!ownsIssueRequest(request)) return;
      S.aiNode = nodeStatus;
      if (!S.aiNode.success) {
        if (String(S.aiNode.code || "") !== "NODE_NOT_INSTALLED") {
          S.aiInstall = S.aiNode;
          return;
        }
        const approved = window.confirm(
          "AI 연결에 필요한 파일을 받을까요?\n이 Windows 계정의 Teacher Manager 앱 폴더에 저장합니다."
        );
        if (!approved) return;
        const prepared = await call("ai_node_prepare");
        if (!ownsIssueRequest(request)) return;
        S.aiNode = prepared;
        if (!S.aiNode.success) {
          S.aiInstall = S.aiNode;
          return;
        }
      }
      const permissionApproved = window.confirm(
        "AI 연결 권한 안내\n\n" +
        "선택한 AI 프로그램의 설정 폴더에 Teacher Manager 연결 파일을 추가합니다. 같은 이름의 Teacher Manager 연결 파일이 있으면 새 파일로 바꿉니다.\n" +
        "설치된 Teacher Manager 안내는 AI에게 다음 일을 요청할 수 있어요.\n" +
        "- 내 컴퓨터의 필요한 파일 읽기\n" +
        "- Teacher Manager 명령 실행\n" +
        "- Google 일정·할 일·문서·시트 자료 만들기와 변경\n\n" +
        "이 내용을 확인했고 선택한 AI에 연결할까요?"
      );
      if (!permissionApproved) return;
      const installed = await call("ai_skills_install", keys, true);
      if (!ownsIssueRequest(request)) return;
      S.aiInstall = installed;
    } finally {
      S.aiConnecting = false;
      if (ownsIssueRequest(request)) render();
    }
  },
  "save-attendance": async () => {
    if (S.attendanceSaving) return;
    S.attendanceSaving = true;
    render();
    try {
      S.attendance = await call("ensure_attendance");
      S.checks = await call("home_checks");
      // 출결 준비가 끝난 직후 현재 Chat 허용 상태까지 읽어야 별도의 "연결하기"
      // 단추를 바로 보여 줄 수 있다. 화면을 먼저 그리면 바깥 클릭 작업이 끝날 때
      // 그 읽기가 취소되어 "확인 중"에 계속 머물 수 있다.
      S.chatStatus = S.attendance.state === "ready"
        ? await call("attendance_chat_status")
        : null;
      if (S.attendance.state === "ready") showToast("출결 업무 준비를 마쳤어요");
    } finally {
      S.attendanceSaving = false;
      render();
    }
  },
  "attendance-prepare-retry": async () => {
    // Resume persisted progress, including from settings, without re-saving a stale draft.
    const startReply = await call("attendance_prepare_resume");
    if (!startReply.started) { setBanner("warn", startReply.reason); return; }
    S.banner = null;
    S.attendance = null;
    startAttendancePreparePoll();
    render();
  },
  "attendance-script-settings": async () => { await openAttendanceScriptSettings(); },
  "attendance-script-update-resolve": async () => {
    const requestToken = ++attendanceScriptRequestToken;
    const requestScreen = screenKey();
    const requestAccount = attendanceScriptAccountKey();
    const requestContext = chatReadContext();
    const current = () => requestToken === attendanceScriptRequestToken
      && requestScreen === screenKey() && requestAccount === attendanceScriptAccountKey()
      && requestContext === chatReadContext();
    let update;
    let resuming = ["ai-action-required", "verification-unavailable", "verification_required"].includes(S.attendanceScriptUpdate?.state);
    try {
      update = await call(resuming ? "attendance_script_update_resume" : "attendance_script_update_status");
      if (!current()) return;
      if (update.state === "verification_required") {
        resuming = true;
        update = await call("attendance_script_update_resume");
      }
    }
    catch (error) { if (!current()) throw new StaleAccountResponse(); throw error; }
    if (!current()) return;
    if (S.banner?.topic === "attendance-script-update") S.banner = null;
    S.attendanceScriptUpdate = update;
    S.attendanceScriptDialog = null;
    if (resuming && update.state === "current" && update.verified) {
      const attendance = await call("attendance_status");
      if (!current()) return;
      S.attendance = attendance;
      S.attendanceScriptUpdate = null;
      if (S.mode === "wizard") startAttendancePreparePoll();
      showToast("출석부의 남은 설정 확인이 끝났어요.");
      render();
      return;
    }
    if (["update_available", "current", "finishing_required"].includes(S.attendanceScriptUpdate.state)) {
      S.attendanceScriptDialog = "update";
    }
    render();
  },
  "attendance-script-dialog-close": () => {
    clearAttendanceScriptDialogState();
    render();
    focusAttendanceScriptResolve();
  },
  "attendance-script-dialog-confirm": async () => {
    if (S.attendanceScriptUpdating) return;
    S.attendanceScriptUpdating = true;
    render();
    try {
      S.attendanceScriptUpdate = await call("attendance_script_update_apply");
      if (S.attendanceScriptUpdate.state === "updated" || S.attendanceScriptUpdate.state === "current") {
        if (S.banner?.topic === "attendance-script-update") S.banner = null;
        S.attendanceScriptDialog = null;
        S.attendanceScriptUpdate = null;
        S.attendance = await call("attendance_status");
        S.chatStatus = null;
        // 홈 점검 결과도 같이 다시 읽는다 — 안 읽으면 연결 카드의 `확인 필요`와
        // 출결 탭의 빨간 숫자가 프로그램을 다시 켤 때까지 그대로 남는다.
        refreshChecks().catch(() => {});
        // 마무리로 상태가 준비됨이 되면, 마법사에서는 시트 처음 설정 완료 확인이
        // 이어서 돌아야 "기다리는 중…"이 풀린다 (검토 C6).
        if (S.mode === "wizard") startAttendancePreparePoll();
        showToast("출결 기능을 최신판으로 바꿨어요.");
      } else {
        S.attendanceScriptDialog = null;
        if (S.attendanceScriptUpdate.state === "permission-required") {
          adoptGoogleStatus(await call("google_status"));
          refreshChecks().catch(() => {});
        }
      }
    } finally {
      S.attendanceScriptUpdating = false;
      render();
      focusAttendanceScriptResolve();
    }
  },
  "new-attendance-go": async () => {
    const a = S.attendance || {};
    const replacement = attendanceReplacementScope(a);
    if ((a.creation_allowed !== true && !replacement) || (attendanceViewKind() === "installation"
        && (attendancePresentation(a).initial || !attendanceInstallationHasExplicitCreation(a)))
        || S.attendanceTransitioning || S.attendanceLoading || S.attendanceReadFailed) return;
    const name = a.canonical_workbook_name || "현재 학년도 출석부";
    const context = googleReadContext();
    const replacementContext = attendanceReplacementContext(a);
    const message = replacement?.reason === "replace-unavailable"
      ? "현재 계정의 기존 출석부를 확인할 수 없어요. 새 출석부를 만들까요? 기존 파일이 남아 있어도 삭제하거나 변경하지 않습니다. 이전 학생·출결·쪽지·발송 기록은 새 파일에 옮기지 않습니다."
      : replacement
      ? "현재 학년도 출석부를 새로 만들까요? 기존 파일은 휴지통에 그대로 남습니다. 이전 학생·출결·쪽지·발송 기록은 새 파일에 옮기지 않습니다."
      : `${name}\n\n출석부를 새로 만들까요?\n지금 출석부는 그대로 남습니다. 이전 학생·출결·쪽지·발송 기록은 새 파일에 넣지 않습니다.`;
    if (!window.confirm(message)) { attendanceReplacementAttempt = null; return; }
    if (context !== googleReadContext() || replacementContext !== attendanceReplacementContext(S.attendance)) {
      attendanceReplacementAttempt = null;
      setBanner("warn", "출석부 연결 정보가 바뀌었어요. 현재 상태를 확인한 뒤 다시 시작해 주세요.");
      return;
    }
    if (replacement && attendanceReplacementAttempt?.context !== replacementContext) {
      attendanceReplacementAttempt = { context: replacementContext, intent: { ...replacement, idempotencyKey: crypto.randomUUID() } };
    }
    const intent = replacement ? attendanceReplacementAttempt.intent : null;
    const flow = { origin: "explicit-create", account: context, owner: screenKey(), active: true,
      before: a.attendance_scope, beforeKey: attendanceScopeKey(a), requestKey: intent?.idempotencyKey, replacementIntent: intent,
      action: null, requesting: true };
    attendanceActionFlow = flow;
    S.attendanceTransitioning = true;
    render();
    try {
      const data = intent ? await call("start_new_attendance", intent) : await call("start_new_attendance");
      if (context !== googleReadContext()) return;
      if (!acceptAttendanceActionResult(data, flow)) {
        if (replacementContext === attendanceReplacementContext(S.attendance)) {
          setBanner("warn", data.detail || "새 출석부의 준비 결과를 확인하지 못했어요. 현재 상태를 다시 확인해 주세요.");
        }
        return;
      }
      S.attendance = { ...data, preparation_running: ["preparing", "installing"].includes(data.state) };
      S.attendanceScriptUpdate = null;
      S.chatStatus = null;
      S.chatSpaces = undefined;
      S.chatSpaceName = undefined;
      stopChatConnectPoll();
      if (attendanceHasConnectedWorkbook(data)) {
        S.firstSetupDone = false; S.firstSetupReason = "";
        S.firstSetupConnectionCode = "";
        await maybeOpenCreatedAttendance(data, flow);
      } else if (["preparing", "installing"].includes(data.state)) {
        startAttendancePreparePoll(flow);
      } else {
        setBanner("warn", data.detail || "출석부를 새로 만들지 못했어요.");
      }
    } catch (error) {
      if (flow.account === googleReadContext() && flow.owner === screenKey()) {
        S.attendance = { ...S.attendance, state: "verification-unavailable", creation_allowed: false,
          replacement_allowed: false, detail: error.message || "출석부 준비 결과를 확인하지 못했어요.",
          recovery_action: "reconcile-attendance-operation" };
      }
      throw error;
    } finally {
      flow.requesting = false;
      S.attendanceTransitioning = false;
      render();
    }
  },
  "chat-connect": async () => {
    if (attendanceChatConnectBlockReason()) return;
    const context = chatReadContext();
    const owner = screenKey();
    S.chatConnectOpening = true;
    render();
    try {
      await call("attendance_chat_connect");
      if (context !== chatReadContext() || owner !== screenKey()) return;
      showToast("브라우저에서 구글 허용을 마쳐 주세요");
      if (S.mode === "wizard") startAttendancePreparePoll();
      else startChatConnectPoll();
    } finally {
      S.chatConnectOpening = false;
      if (context === chatReadContext() && owner === screenKey()) render();
    }
  },
  "open-chat-new-space": async () => {
    if (attendanceChatOpenBlockReason()) return;
    S.chatNewSpaceBrowserOpen = true;
    S.chatNewSpaceBrowserBlurred = false;
    S.chatNewSpaceBrowserContext = chatReadContext();
    S.chatNewSpaceBrowserOwner = screenKey();
    await call("open_attendance_chat");
  },
  "class-space-create": async () => {
    const box = document.querySelector('input[name="class-space-name"]');
    const name = (box ? box.value : "").trim() || defaultClassSpaceName();
    S.spaceDraftName = name;
    chatStatusReadVersion += 1;
    const data = await call("attendance_chat_create_space", name);
    if (data.state === "ok") {
      chatStatusReadVersion += 1;
      S.spaceCreate = "ok";
      S.chatSpaceName = data.display_name;
      S.chatSpaces = undefined;   // 방금 만든 방이 목록에 잡히게 다시 읽는다
      if (S.chatStatus && typeof S.chatStatus === "object") {
        S.chatStatus.class_space_id = data.space_name;
        S.chatStatus.class_space_name = data.display_name;
      }
      showToast("학급 단톡방을 만들고 골라 뒀어요");
    } else if (data.state === "blocked") {
      S.spaceCreate = "blocked";
    } else {
      S.spaceCreate = data.detail || "방을 만들지 못했어요. 다시 시도해 주세요.";
      // 만들기는 됐고 고르기만 실패했으면 목록에 그 방이 있다 — 다시 읽어 고를 수 있게.
      // 목록이 오면 곧 고르기 모습으로 바뀌어 사유가 안 보이니, 배너로도 남긴다(배너는 다시 그려도 남는다).
      if (data.space_name) {
        S.chatSpaces = undefined;
        setBanner("warn", data.detail || "방은 만들었어요. 목록에서 골라 주세요.");
      }
    }
    render();
  },
  "class-space-reload": async () => {
    S.spaceCreate = null;
    const status = loadChatStatus(true, false);
    const reading = loadChatSpaces(true);
    render();
    await Promise.all([status, reading]);
    render();
  },
  "chat-guide": () => openAttendancePictureGuide("chat"),
  "goto-identity": async () => {
    if (S.mode === "wizard") { await goStepAsync(3); return; }
    await openCard("identity");
  },
});
function syncConnectFields() {
  const p = S.draft.profile;
  const nameById = {
    cal: Object.fromEntries(currentListRows("calendars").map((o) => [o.id, o.name])),
    task: Object.fromEntries(currentListRows("tasklists").map((o) => [o.id, o.name])),
  };
  for (const [kind, fields] of [["cal", CAL_LINK_FIELDS], ["task", taskLinkFields()]]) {
    const known = currentListRows(kind === "cal" ? "calendars" : "tasklists");
    for (const [idField, nameField] of fields) {
      const select = document.querySelector(`select[name="${idField}"]`);
      if (select) {
        // 토큰이 풀려 목록을 못 불러온 동안에는, 비어 보이는 select가
        // 이미 골라 둔 값을 지우지 않게 한다.
        if (select.disabled || (!select.value && p[idField] && !known.some(row => row.id === p[idField]))) continue;
        if (select.value && !known.some(row => row.id === select.value)) continue;
        const chosenName = nameById[kind][select.value];
        p[idField] = select.value;
        if (chosenName !== undefined) p[nameField] = chosenName;
        else if (!select.value) p[nameField] = "";
        // (목록 밖 저장값이 그대로 선택돼 있으면 기존 이름을 유지한다)
        continue;
      }
      const input = document.querySelector(`input[name="${nameField}"]`);
      if (input) p[nameField] = input.value.trim();
    }
  }
  syncGeminiDraft();
}
document.addEventListener("change", (event) => {
  const name = event.target.name || "";
  if (name === "attendance-workbook-choice") {
    const flow = S.attendanceConnection;
    if (!flow || S.attendanceConnectionBusy) return;
    if (!flow.candidates?.some(candidate => candidate.spreadsheet_id === event.target.value)) return;
    flow.selected_id = event.target.value;
    delete flow.selection_error;
    // Keep the native radio nodes/focus for arrow-key selection.
    const footer = document.querySelector(".attendance-picker-footer");
    if (footer) footer.innerHTML = attendanceConnectionFooterHtml(flow);
    return;
  }
  if (name === "link-cal-mode" || name === "link-task-mode") {
    syncConnectFields();
    linkModes()[name === "link-cal-mode" ? "cal" : "task"] = event.target.value;
    if (event.target.value === "existing") loadLinkLists();
    render();
    return;
  }
  if (event.target.matches("[data-link-select]")) {
    syncConnectFields();
    // A selection may commit during blur before the user's Next click. Update
    // only this table; replacing the whole page would remove the clicked button.
    const table = event.target.closest(".form-table");
    if (table) table.innerHTML = linkExistingRowsHtml(name.includes("Tasks") ? "task" : "cal");
    updateTabBadges();
    return;
  }
  if (event.target.matches('[data-action-change="class-space-pick"]')) {
    if (S.classSpaceSaving) return;
    const select = event.target;
    const spaceName = select.value;
    select.closest(".chat-space-controls")?.querySelector(".chat-space-status")?.remove();
    if (!spaceName) return;
    const label = select.options[select.selectedIndex].textContent;
    const expected = String(select.dataset.currentSpace || "");
    const request = beginIssueRequest(false);
    const context = chatReadContext();
    S.classSpaceSaving = true;
    render();
    chatStatusReadVersion += 1;
    call("attendance_chat_set_space", spaceName, label, expected)
      .then(() => {
        if (!ownsIssueRequest(request) || context !== chatReadContext()) return;
        chatStatusReadVersion += 1;
        S.chatSpaceName = label;
        if (S.chatStatus && typeof S.chatStatus === "object") {
          S.chatStatus.class_space_id = spaceName;
          S.chatStatus.class_space_name = label;
        }
        showToast("학급 단톡방을 골랐어요");
        render();
      })
      .catch((error) => { if (context === chatReadContext()) handleCaughtError(error, request); })
      .finally(() => { S.classSpaceSaving = false; if (context === chatReadContext()) render(); });
  }
});
window.addEventListener("blur", () => {
  if (S.chatNewSpaceBrowserOpen) S.chatNewSpaceBrowserBlurred = true;
});
window.addEventListener("focus", () => {
  if (S.connectTab !== "attendance" || !((S.mode === "wizard" && S.step === 8)
      || (S.mode === "edit" && S.edit === "connect"))) return;
  if (S.chatNewSpaceBrowserOpen && (S.chatNewSpaceBrowserOwner !== screenKey()
      || S.chatNewSpaceBrowserContext !== chatReadContext())) {
    S.chatNewSpaceBrowserOpen = false;
    S.chatNewSpaceBrowserBlurred = false;
    return;
  }
  const returningFromChatSetup = S.chatNewSpaceBrowserOpen && S.chatNewSpaceBrowserBlurred;
  S.chatNewSpaceBrowserOpen = false;
  S.chatNewSpaceBrowserBlurred = false;
  S.spaceCreate = null;
  // Ordinary window focus is navigation, not a new verification request.
  // Returning from an explicit Chat setup still completes that operation.
  if (S.mode === "wizard" || returningFromChatSetup) {
    refreshAttendanceStatus();
    render();
  }
});

function stepConnect() {
  // Keep the Chat actions visible while first-install login is being checked,
  // without starting any attendance or Chat requests before verification.
  const pendingChat = () => S.connectTab === "attendance" && attendanceUiEnabled()
    ? attendanceServiceRow(ATTENDANCE_SERVICES.find(entry => entry.service === "chat"), {state:"checking"}) : "";
  if (!S.google) {
    const request = beginIssueRequest(false);
    call("google_status")
      .then((data) => { if (ownsIssueRequest(request)) { adoptGoogleStatus(data); render(); } })
      .catch((error) => handleCaughtError(error, request));
    return `<h1>연결</h1><p class="sub">상태를 확인하는 중이에요…</p>${pendingChat()}`;
  }
  if (googleAuthCheckFailed(S.google)) {
    return `<h1>Google 연결</h1><div class="banner warn"><span>${esc(GOOGLE_AUTH_CHECK_MESSAGE)}</span>
      <button class="btn-quiet" data-action="goto-settings">Google 로그인 열기</button></div>${pendingChat()}`;
  }
  if (isGoeduGoogleStatus(S.google) && !isGoogleReady(S.google)) {
    return `<h1>Google 연결</h1><div class="banner warn"><span>${esc(googleAuthorizationMessage(S.google))}</span>
      <button class="btn-tonal" data-action="reauthorize-google">다시 로그인 (권한 승인)</button></div>${pendingChat()}`;
  }
  if (!isGoeduGoogleStatus(S.google)) {
    const message = S.google.logged_in ? GOEDU_REQUIRED_MESSAGE : FIELD_MESSAGES["google-login"];
    return `<h1>Google 연결</h1><div class="banner warn"><span>${esc(message)}</span>
      <button class="btn-quiet" data-action="goto-settings">Google 로그인 열기</button></div>
      <p class="sub">Calendar·Tasks·Sheet·Docs·Chat 작업은 Google 계정과 필요한 권한을 확인한 뒤 시작해요.</p>${pendingChat()}`;
  }
  if (!connectionRefreshActive() && S.connectTab === "messenger" && !S.listsLoaded && !S.linkLoading && !S.listsError &&
      (linkModes().cal === "existing" || linkModes().task === "existing")) {
    loadLinkLists();
  }
  const body = S.connectTab === "attendance" ? (attendanceUiEnabled() ? attendanceTabHtml() : attendanceComingSoonHtml())
    : S.connectTab === "ai" ? aiTabHtml() : messengerTabHtml();
  return `
    <h1>Google 연결</h1>
    <p class="sub">Google 서비스를 연결하여 학교 업무를 자동화해요.</p>
    ${connectTabsHtml()}
    ${body}`;
}
async function validateConnect() {
  if (!S.google) adoptGoogleStatus(await call("google_status"));
  if (S.google.local_settings_error) return S.google.local_settings_error;
  if (googleAuthCheckFailed(S.google)) return GOOGLE_AUTH_CHECK_MESSAGE;
  if (!S.google.logged_in) return "구글 로그인을 마쳐야 다음으로 갈 수 있어요.";
  if (!isGoogleReady(S.google)) return googleAuthorizationMessage(S.google);
  syncConnectFields();
  const rows = connectIssues();
  // 선택 항목(gemini key)은 표시만 하고 진행을 막지 않는다 — 마법사에서는 표시도 하지 않는다.
  const blocking = rows.filter((row) => !row.optional);
  replaceEditableIssues(S.mode === "wizard" ? blocking : rows);
  if (blocking.length) {
    // 출결 탭을 보고 있어도 메신저 입력 문제면 메신저 탭으로 이동해 첫 문제 칸을 보여준다.
    const messengerIssue = blocking.find((row) => row.tab === "messenger");
    if (messengerIssue && S.connectTab !== "messenger") {
      if (S.connectTab === "attendance") clearAttendanceScriptDialogState();
      S.connectTab = "messenger";
    }
    // 이어지는 배너 render가 탭 이동과 첫 문제 입력칸 초점을 함께 적용한다.
    S.focusTarget = (messengerIssue || blocking[0]).target;
    return firstIssueMessage(blocking);
  }
  await provisionConnectTargets();
  acknowledgeSavedGoogleTargets(Object.fromEntries(GOOGLE_TARGET_ID_FIELDS
    .filter((field) => S.draft.profile[field]).map((field) => [field, S.draft.profile[field]])));
  return "";
}
/* '새로 만들기'를 고른 캘린더·Tasks 목록을 구글에 실제로 만들고 그 ID를 프로필에 채운다.
   구글에 무언가를 만드는 일이라 타자 한 자마다 돌면 안 된다 — 마법사에서 [다음]을 누를 때와
   연결 화면 창을 닫을 때만 부른다. */
async function provisionConnectTargets() {
  if (!isGoogleReady(S.google)) throw new Error(googleAuthorizationMessage(S.google));
  const p = S.draft.profile;
  const homeroom = p["담임여부"] === "예";
  const modes = linkModes();
  if (modes.cal === "new") {
    const work = await ensureConnectTarget("업무캘린더ID", "ensure_calendar_named", p["업무캘린더이름"]);
    p["업무캘린더ID"] = work.id;
    markEditDirtyField("업무캘린더ID");
    const school = await ensureConnectTarget("학사일정캘린더ID", "ensure_calendar_named", p["학사일정캘린더이름"]);
    p["학사일정캘린더ID"] = school.id;
    markEditDirtyField("학사일정캘린더ID");
  }
  if (modes.task === "new") {
    const workTasks = await ensureConnectTarget("업무Tasks목록ID", "ensure_tasklist_named", p["업무Tasks목록이름"]);
    p["업무Tasks목록ID"] = workTasks.id;
    markEditDirtyField("업무Tasks목록ID");
    if (homeroom) {
      const homeTasks = await ensureConnectTarget("담임안내Tasks목록ID", "ensure_tasklist_named", p["담임안내Tasks목록이름"]);
      p["담임안내Tasks목록ID"] = homeTasks.id;
      markEditDirtyField("담임안내Tasks목록ID");
    }
  }
}

/* ---------- 6단계: 설정 ---------- */
// 화면 기본값: C:\BrityWorks\BrityMessenger\download
const DEFAULT_ATTACHMENT_FOLDER = "C:\\BrityWorks\\BrityMessenger\\download";

function readinessRow(title, note, state, retryAction) {
  const right = state && state.ready
    ? `<span class="st ok">준비됐어요</span>`
    : `<span class="badge y">설치 필요</span>` +
      (retryAction ? `<button class="btn-tonal" data-action="${retryAction}" data-busy-text="준비 중…">다시 설치하기</button>` : "");
  return `<div class="row"><span class="nameblock"><b>${esc(title)}</b><small>${esc(note)}</small></span><span class="row-actions">${right}</span></div>`;
}
let settingsStatusRequest = null;
function hasCurrentSettingsStatusRequest() {
  return Boolean(settingsStatusRequest && ownsIssueRequest(settingsStatusRequest));
}
async function refreshSettingsStatus(request, options = {}) {
  const owner = request || beginIssueRequest(false);
  // 같은 요청만 막는다. 닫은 화면의 늦은 요청은 이미 소유권을 잃었으므로,
  // 다시 연 설정 화면의 새 점검을 가로막으면 안 된다.
  if (hasCurrentSettingsStatusRequest()
      && settingsStatusRequest.screen === owner.screen
      && settingsStatusRequest.token === owner.token) return false;
  settingsStatusRequest = owner;
  // 실제 계정 확인 결과를 먼저 표시하고, 나머지 컴퓨터·목록 점검을 이어 간다.
  try {
    const previousGoogle = S.google;
    const nextGoogle = options.googleStatus || await call("google_status");
    if (!ownsIssueRequest(owner)) return false;
    if (options.loginEpoch !== undefined && options.loginEpoch !== googleLoginEpoch) return false;
    if (options.loginComplete) S.login = null;
    adoptGoogleStatus(nextGoogle);
    paintSettingsReadiness();
    if (previousGoogle && googleStatusKey(previousGoogle) !== googleStatusKey(nextGoogle)) {
      S.checks = [];
      checksRetry.lastGood = [];
    }
    const context = googleReadContext();
    const current = () => ownsIssueRequest(owner) && context === googleReadContext();
    const computer = await call("computer_status");
    if (!current()) return false;
    S.computer = computer;
    paintSettingsReadiness();
    // 갱신 확인은 선택 기능이다. 새 Windows에서 GitHub 인증서 검증이 실패해도 로그인
    // 상태(S.google)는 이미 채워졌으니 문제 화면 대신 줄 안에 표시만 남긴다.
    const gwsUpdate = await call("gws_update_status").catch(() => ({ unavailable: true }));
    if (!current()) return false;
    S.gwsUpdate = gwsUpdate;
    if (isGoogleReady(S.google)) {
      try {
        await loadLinkLists(true, current);
      } catch (error) {
        if (!current()) return false;
        // List failures belong to their current read, independently of login.
        S.listsError = true;
      }
    } else {
      S.lists = { calendars: [], tasklists: [] };
      S.listReads = {};
      S.listsLoaded = false;
      S.listsError = false;
    }
    if (current()) paintSettingsReadiness();
    return true;
  } catch (error) {
    handleCaughtError(error, owner);
    return false;
  } finally {
    if (settingsStatusRequest === owner) settingsStatusRequest = null;
  }
}

async function ensureConnectTarget(idField, apiName, name) {
  const chosen = S.verifiedGoogleTargetChoices?.[idField];
  if (chosen) return { id: chosen, name };
  const pending = {
    field: idField,
    kind: idField.includes("Tasks") ? "tasklist" : "calendar",
    name,
    account: String(S.google?.user || "").trim().toLowerCase(),
    screen: screenKey(),
  };
  S.pendingGoogleTarget = pending;
  let result;
  try {
    result = await call(apiName, name);
  } catch (error) {
    const hasChoices = error instanceof AppIssueError && error.issue?.actions?.some(
      action => String(action.key || "").startsWith(`select-${pending.kind}:`));
    if (hasChoices && S.pendingGoogleTarget === pending && pending.screen === screenKey()) {
      linkModes()[pending.kind === "calendar" ? "cal" : "task"] = "existing";
      S.draft.profile[idField] = "";
      S.focusTarget = idField;
      replaceEditableIssues(connectIssues());
      S.listsLoaded = false;
      await loadLinkLists();
    }
    throw error;
  }
  if (S.pendingGoogleTarget === pending) S.pendingGoogleTarget = null;
  return result;
}

async function openGoogleCandidateInspector(actionKey) {
  const urls = {
    "inspect-calendar-candidates": "https://calendar.google.com/calendar/u/0/r/settings",
    "inspect-tasklist-candidates": "https://tasks.google.com/",
  };
  const url = urls[String(actionKey || "")];
  if (!url) return false;
  await call("open_url", url);
  return true;
}

async function inspectGoogleTargetCandidate(issue, actionKey, request) {
  const match = /^inspect-(calendar|tasklist):([^\s:]+)$/.exec(String(actionKey || ""));
  const pending = S.pendingGoogleTarget;
  if (!match || !pending) return false;
  const declaredAction = Array.isArray(issue?.actions)
    ? issue.actions.find((row) => String(row?.key || "") === actionKey)
    : null;
  if (!declaredAction || match[1] !== pending.kind || pending.screen !== screenKey()) return false;
  const codeMatch = /확인코드 ([0-9A-F]{6,})/.exec(String(declaredAction.label || ""));
  const displayCode = codeMatch ? codeMatch[1] : "선택한";
  const reply = await call(
    "verify_google_target_candidate",
    pending.kind,
    match[2],
    pending.name,
    pending.account,
  );
  if (!ownsIssueRequest(request) || S.pendingGoogleTarget !== pending) return true;
  const displayedAccount = String(S.google?.user || "").trim().toLowerCase();
  if (!reply?.verified || displayedAccount !== pending.account) {
    setBanner("warn", `확인코드 ${displayCode} 후보를 현재 Google 계정에서 확인하지 못했어요. 계정과 목록을 다시 확인해 주세요.`);
    return true;
  }
  setBanner("ok", `확인코드 ${displayCode} 후보가 현재 Google 계정에 그대로 있어요. 연결하려면 같은 확인코드의 선택 단추를 눌러 주세요.`);
  return true;
}

async function applyGoogleTargetSelection(issue, actionKey, request) {
  const match = /^select-(calendar|tasklist):([^\s:]+)$/.exec(String(actionKey || ""));
  const pending = S.pendingGoogleTarget;
  if (!match || !pending || !S.draft.profile) return "";
  const declared = Array.isArray(issue?.actions)
    && issue.actions.some((row) => String(row?.key || "") === actionKey);
  if (!declared || match[1] !== pending.kind || pending.screen !== screenKey()) return "";
  const reply = await call(
    "verify_google_target_candidate",
    pending.kind,
    match[2],
    pending.name,
    pending.account,
  );
  if (!ownsIssueRequest(request) || S.pendingGoogleTarget !== pending) return "rejected";
  const displayedAccount = String(S.google?.user || "").trim().toLowerCase();
  if (!reply?.verified || displayedAccount !== pending.account) {
    clearGoogleDependentState({ accountChanged: true });
    setBanner("warn", "Google 계정이 바뀌었어요. 현재 계정에서 Calendar와 Tasks를 다시 골라 주세요.");
    return "rejected";
  }
  S.draft.profile[pending.field] = match[2];
  if (!S.verifiedGoogleTargetChoices) S.verifiedGoogleTargetChoices = {};
  S.verifiedGoogleTargetChoices[pending.field] = match[2];
  markEditDirtyField(pending.field);
  S.pendingGoogleTarget = null;
  clearProblemIssue();
  showToast("연결할 Google 항목을 골랐어요");
  return "selected";
}
async function resumeGoogleTargetSelection() {
  const request = beginIssueRequest(false);
  try {
    if (S.mode === "wizard" && S.step === 8) {
      await goNextAsync();
      return;
    }
    if (S.mode === "edit" && S.edit === "connect") {
      await provisionConnectTargets();
      await autoSaveEdit({ leaving: true, request });
    }
  } catch (error) {
    handleCaughtError(error, request);
  }
}
const GOOGLE_AUTH_CHECK_MESSAGE = "Google 계정 상태 확인을 마치지 못했어요. [다시 점검]을 눌러 주세요. 로그인이 해제된 것으로 판단하지 않았습니다.";
function updateFailureText(reason, fallback, buttonLabel) {
  const base = String(reason || fallback || "").trim();
  if (base.includes(`'${buttonLabel}'`)) return base;
  if (/공식|일치|지문|hash|sha/i.test(base)) {
    return "받은 파일이 공식 파일과 달라 실행하지 않았어요.";
  }
  if (/저장|폴더|권한/.test(base)) {
    return `${base} 저장하지 못한 위치와 폴더 권한을 확인해 주세요.`;
  }
  const action = buttonLabel === "지금 업데이트"
    ? "설치 파일을 내려받지 못했어요. 인터넷 연결을 확인한 뒤 '지금 업데이트'를 다시 눌러 주세요."
    : "업데이트 정보를 불러오지 못했어요. 인터넷 연결을 확인한 뒤 '업데이트 다시 확인'을 눌러 주세요.";
  return `${base} ${action}`;
}
const OAUTH_REPAIR_MESSAGES = {
  GWS_ACCOUNT_STORAGE_OUTSIDE_USER: {
    status: "Google 로그인을 안전하게 사용할 수 없는 상태예요",
    repair: "Teacher Manager 설치 파일을 다시 실행해 주세요.",
  },
  OAUTH_CLIENT_ENV_INCOMPLETE: {
    status: "프로그램의 Google 로그인 기능을 준비하지 못했어요",
    repair: "Teacher Manager 설치 파일을 다시 실행해 주세요.",
  },
  OAUTH_CONFIG_CLIENT_INVALID: {
    status: "저장된 Google 로그인 설정을 사용할 수 없어요",
    repair: "[로그인 설정 복구]는 Google 도구가 만든 사용할 수 없는 로컬 로그인 설정 파일만 삭제합니다. 복구 뒤 Google 로그인을 다시 진행해 주세요.",
  },
  OAUTH_BUNDLED_CLIENT_INVALID: {
    status: "프로그램의 Google 로그인 설정을 읽지 못했어요",
    repair: "최신 Teacher Manager 설치 파일을 다시 실행해 주세요.",
  },
  OAUTH_CLIENT_MISSING: {
    status: "프로그램의 Google 로그인에 필요한 파일이 없습니다",
    repair: "최신 Teacher Manager 설치 파일을 다시 실행해 주세요.",
  },
  OAUTH_CLIENT_CONFLICT: {
    status: "Google 로그인 설정이 서로 달라 로그인을 시작할 수 없어요",
    repair: "Teacher Manager 설치 파일을 다시 실행해 주세요.",
  },
};
function oauthRepairMessage(g) {
  const code = String(g?.error_code || "");
  return OAUTH_REPAIR_MESSAGES[code] || null;
}
function googleAuthCheckFailed(g) {
  return Boolean(g && (g.login_state === "error" || g.error_code === "GWS_AUTH_STATUS_FAILED" || g.authorization_state === "check_failed"));
}
function settingsRefreshButtonHtml() {
  return `<button class="btn-quiet" data-action="settings-refresh" data-busy-text="점검 중…">다시 점검</button>`;
}
function computerSectionHtml(includeGoogle) {
  const c = S.computer;
  const rows = c
    ? readinessRow("프로그램 실행 기능", "Teacher Manager를 실행해요", c.python)
      + readinessRow("문서 읽기 기능", "PDF와 첨부 문서를 읽어요", c.documents)
      + readinessRow("화면 표시 기능", "Teacher Manager 화면을 보여줘요", c.screen)
    : `<div class="row"><span class="st">준비 상태를 확인하는 중이에요…</span></div>`;
  return `<div class="section-h section-head"><span>컴퓨터 준비</span>${settingsRefreshButtonHtml()}</div>
    <div class="panel">${rows}</div>
    ${includeGoogle ? googleAccountSectionHtml(false) : ""}`;
}
function paintSettingsReadiness() {
  const region = S.mode === "edit" && S.edit === "settings"
    ? document.querySelector("#settings-readiness") : null;
  // Preserve typing only within the same account. A pending profile change must
  // also remove the previous account's unsaved form, as clearAccountScreen intends.
  if (region && !accountProfileNeedsReload) {
    const messages = document.querySelector("#settings-status-messages");
    if (messages) messages.innerHTML = bannerHtml();
    region.innerHTML = computerSectionHtml(true);
  } else render();
}

function googleLoginRowsHtml() {
  const g = S.google;
  if (!g) return `<div class="row"><span class="nameblock"><b>Google 연결 기능</b><small>일정·할 일·출결 자료를 연결해요</small></span><span class="st">확인 중이에요…</span></div>
    <div class="row"><span class="nameblock"><b>Google 로그인 사용 가능</b><small>프로그램에서 로그인을 시작할 수 있는지 확인해요</small></span><span class="st">확인 중이에요…</span></div>
    <div class="row"><span class="nameblock"><b>Google 로그인</b></span><span class="st">확인 중이에요…</span></div>`;
  const loginError = fieldError("google-login");
  const updateUnavailable = Boolean(S.gwsUpdate && S.gwsUpdate.unavailable);
  const update = updateUnavailable ? null : S.gwsUpdate;
  const authCheckFailed = googleAuthCheckFailed(g);
  const runtimeReadiness = update?.runtime_ready ?? g.gws_runtime_ready;
  const runtimeReady = Boolean(runtimeReadiness);
  const runtimeUnknown = authCheckFailed && runtimeReadiness == null;
  const oauthUnknown = authCheckFailed && g.oauth_client_ready == null;
  const unavailableNote = updateUnavailable
    ? `<br><span data-gws-update-unavailable="true">새 판 확인은 지금 하지 못했어요.${runtimeReady ? " 현재 Google 연결 기능은 그대로 쓸 수 있어요." : runtimeUnknown ? " 준비 상태를 다시 점검해 주세요." : " Teacher Manager 설치 파일을 다시 실행해 주세요."}</span>`
    : "";
  const accountStorageProblem = g.error_code === "GWS_ACCOUNT_STORAGE_OUTSIDE_USER"
    ? OAUTH_REPAIR_MESSAGES.GWS_ACCOUNT_STORAGE_OUTSIDE_USER
    : null;
  const offerNote = update && update.offer ? safeGwsOfferText(update.offer.notes) : "";
  const offerDate = update && update.offer ? safeGwsOfferDate(update.offer.verified_on) : "";
  const offerExplanation = update && runtimeReady
    ? [update.current_version ? `현재 버전 ${esc(update.current_version)}` : "", offerDate ? `확인 날짜 ${esc(offerDate)}` : "", offerNote ? esc(offerNote) : ""]
      .filter(Boolean).join(" · ")
    : "";
  const offerExplanationHtml = offerExplanation
    ? `<details data-gws-update-note="true"><summary>업데이트 정보</summary><span>${offerExplanation}</span></details>`
    : "";
  const updateButton = update && update.offer && runtimeReady
    && !hasCurrentFinalIssue(["gws_update_status", "install_gws_update"])
    ? `<button class="btn-tonal" data-action="install-gws-update" data-busy-text="업데이트 중…" ${S.gwsUpdateInstalling ? "disabled" : ""}>Google 연결 기능 업데이트</button>`
    : "";
  const cliRight = accountStorageProblem
    ? `<span class="st warn">Google 연결 기능을 사용할 수 없어요</span>`
    : runtimeUnknown
    ? `<span class="st warn">Google 연결 기능의 준비 상태를 확인하지 못했어요</span>`
    : runtimeReady
    ? `<span class="st ok">사용할 수 있어요</span>${updateButton}`
    : `<span class="st warn">Google 연결 기능을 사용할 수 없어요 · Teacher Manager 설치 파일을 다시 실행해 주세요</span>`;
  const cliRow = `<div class="row"><span class="nameblock"><b>Google 연결 기능</b><small>일정·할 일·출결 자료를 연결해요${offerExplanationHtml}${unavailableNote}</small></span><span class="row-actions">${cliRight}</span></div>`;
  const oauthBlock = `<span class="nameblock"><b>Google 로그인 사용 가능</b><small>프로그램에서 로그인을 시작할 수 있는지 확인해요</small></span>`;
  // 예전 화면 다리에서 오류 글자 없이 충돌 여부만 돌려줘도 로그인은 막는다.
  const oauthProblem = oauthRepairMessage(g) || (g.oauth_client_conflict ? {
    status: "Google 로그인 설정이 서로 달라 로그인을 시작할 수 없어요",
    repair: "Teacher Manager 설치 파일을 다시 실행해 주세요.",
  } : null);
  const oauthCleanable = String(g.error_code || "") === "OAUTH_CONFIG_CLIENT_INVALID";
  const oauthRight = oauthProblem
    ? `<span class="st warn">${esc(oauthProblem.status)}</span><small>${esc(oauthProblem.repair)}</small>${oauthCleanable ? `<button class="btn-tonal" data-action="gws-repair-oauth" data-busy-text="복구 중…">로그인 설정 복구</button>` : ""}`
    : g.oauth_client_ready
      ? `<span class="st ok">준비됐어요</span>`
      : oauthUnknown
      ? `<span class="st warn">Google 로그인의 준비 상태를 확인하지 못했어요</span>`
      : `<span class="st warn">프로그램의 Google 로그인에 필요한 파일이 없습니다 · 최신 설치 파일을 다시 실행해 주세요</span>`;
  const oauthRow = `<div class="row">${oauthBlock}<span class="row-actions">${oauthRight}</span></div>`;
  const loginBlock = `<span class="nameblock"><b>Google 로그인</b></span>`;
  const canLogin = Boolean(runtimeReady && g.oauth_client_ready && !g.oauth_client_conflict && !oauthProblem);
  const loginButton = `<button class="btn-tonal" data-action="gws-login" data-busy-text="진행 중…">로그인 (권한 승인)</button>`;
  const blockedReason = oauthProblem
    ? "Google 로그인 사용 가능 상태를 먼저 확인해 주세요"
    : !runtimeReady
      ? "Google 연결 기능을 먼저 준비해 주세요"
      : "프로그램의 Google 로그인에 필요한 파일이 없습니다";
  const loginRow = S.login
    ? `<div class="row">${loginBlock}<span class="st">진행 중…</span></div>`
    : isGoeduGoogleStatus(g) && !isGoogleReady(g)
      ? `<div class="row">${loginBlock}<span class="row-actions"><span class="st warn">${esc(g.user)} · ${authCheckFailed ? "권한 확인 필요" : "다시 승인 필요"}</span>${canLogin ? `<button class="btn-tonal" data-action="gws-login" data-busy-text="진행 중…">다시 로그인 (권한 승인)</button>` : ""}</span></div>`
    : isGoogleReady(g)
      ? `<div class="row">${loginBlock}<span class="row-actions"><span class="st ok">${esc(g.user || "완료")}</span><button class="btn-quiet" data-action="gws-logout">로그아웃</button></span></div>`
      : accountStorageProblem
        ? `<div class="row${loginError ? " problem-row" : ""}">${loginBlock}<span class="row-actions"><span class="st warn">${esc(accountStorageProblem.status)}. ${esc(accountStorageProblem.repair)}</span></span></div>`
      : authCheckFailed
        ? `<div class="row${loginError ? " problem-row" : ""}">${loginBlock}<span class="row-actions"><span class="st warn">${GOOGLE_AUTH_CHECK_MESSAGE}</span>${canLogin ? loginButton : ""}</span></div>`
      : g.logged_in
        ? `<div class="row">${loginBlock}<span class="row-actions"><span class="st warn">${esc(googleAuthorizationMessage(g))}</span>${canLogin ? loginButton : ""}</span></div>`
      : canLogin
        ? `<div class="row${loginError ? " problem-row" : ""}">${loginBlock}<span class="row-actions">${loginError ? `<span class="field-error">${esc(loginError)}</span>` : ""}${loginButton}</span></div>`
        : `<div class="row${loginError ? " problem-row" : ""}">${loginBlock}<span class="row-actions">${loginError ? `<span class="field-error">${esc(loginError)}</span>` : ""}<span class="st warn">${esc(blockedReason)}</span></span></div>`;
  return `${cliRow}
    ${oauthRow}
    ${loginRow}`;
}
function loginWaitHtml() {
  if (!S.login) return "";
  if (S.login.logging_out) return '<p class="sub">Teacher Manager에서 로그아웃하고 있어요…</p>';
  const url = String(S.login.url || "");
  const browserMessage = !url
    ? "로그인 주소를 준비하는 중이에요…"
    : S.login.browser_opened
      ? "브라우저가 자동으로 열렸어요. 창이 보이지 않으면 아래 주소를 다시 열어 주세요."
      : "브라우저를 자동으로 열지 못했어요. 아래 주소를 직접 열거나 복사해 주세요.";
  return `<div class="panel" style="margin-top:12px">
      <p class="sub" style="margin:0 0 8px">${browserMessage}</p>
      ${url ? linkRow(url) : ""}
      <div class="action-line"><button class="btn-quiet" data-action="login-cancel">취소</button></div>
    </div>`;
}
function accountAvatar(user) {
  const text = String(user || "G").trim();
  return esc((text[0] || "G").toUpperCase());
}
function lockedGoogleServicesHtml() {
  const rows = [
    ["일정", "일정 (Google Calendar)", "업무 일정 등록"],
    ["할 일", "할 일 (Google Tasks)", "업무·전달사항 등록"],
    ["출결", "메신저 개인톡 내용·메신저 단체톡 내용·결석 신고서", "출결 자료 준비"],
    ["단톡", "학급 단톡방 (Google Chat)", "학급 공간 연결과 메시지 발송"],
  ];
  return `<div class="locked-services" aria-label="잠긴 Google 기능">${rows.map(([mark, name, note]) =>
    `<div class="service-row"><span class="service-mark">${mark}</span><span class="service-copy"><b>${name}</b><span>${note}</span></span><span class="lock-label">계정 확인 필요</span></div>`
  ).join("")}</div>`;
}
function googleAccountDecisionHtml() {
  const g = S.google;
  if (!g || !g.logged_in || S.login) return "";
  const allowed = isGoeduGoogleStatus(g);
  const ready = isGoogleReady(g);
  const checkFailed = googleAuthCheckFailed(g);
  const label = checkFailed ? "계정 상태 확인 필요" : !allowed ? "사용할 수 없음" : ready ? "확인 완료" : "다시 승인 필요";
  const account = `<div class="account-box">
    <span class="avatar${ready ? " good" : ""}">${accountAvatar(g.user)}</span>
    <span class="account-copy"><span>${checkFailed ? "마지막으로 확인한 계정" : "현재 선택한 계정"}</span><b>${esc(g.user || "계정 확인 필요")}</b></span>
    <span class="account-state ${ready ? "good" : "bad"}">${label}</span>
  </div>`;
  if (checkFailed) return `${account}<div class="banner warn">${esc(GOOGLE_AUTH_CHECK_MESSAGE)}</div>`;
  if (!allowed) {
    return `${account}<div class="decision-banner error">
      <h3>${esc(GOEDU_REQUIRED_MESSAGE)}</h3><p>Google 계정으로 진행할 수 있습니다.</p>
    </div>${lockedGoogleServicesHtml()}`;
  }
  if (!ready) return `${account}<div class="banner warn">${esc(googleAuthorizationMessage(g))}</div>`;
  return account;
}
function googleAccountSectionHtml(includeRefresh) {
  return `<div class="section-h section-head google-account-section-head"><span>Google 계정 준비</span>
    <div class="google-account-actions">${includeRefresh ? settingsRefreshButtonHtml() : ""}</div></div>
    <div class="panel">${googleLoginRowsHtml()}<p class="attendance-picture-guide"><button type="button" class="text-link" data-action="google-login-guide" data-preserve-issue="true">Google 로그인 그림 안내</button></p></div>
    ${googleAccountDecisionHtml()}
    ${loginWaitHtml()}`;
}
async function refreshComputerStatus() {
  S.computer = await call("computer_status");
  paintSettingsReadiness();
}
function ensureComputerStatus() {
  if ((S.computer && S.google && S.gwsUpdate) || S.computerLoading || hasCurrentSettingsStatusRequest()) return;
  S.computerLoading = true;
  const request = beginIssueRequest(false);
  // 갱신 확인(선택 기능)은 따로 부른다. 새 Windows(VirtualBox 첫 설치)에서 GitHub 인증서
  // 검증이 실패했을 때 컴퓨터·로그인 상태까지 버려 로그인 준비 줄이 '확인 중이에요…'에
  // 멈추고 빨간 문제 화면만 떴다(2026-09-04). 실패는 줄 안의 한 줄 표시로만 남긴다.
  const gwsUpdatePromise = S.gwsUpdate
    ? Promise.resolve(S.gwsUpdate)
    : call("gws_update_status").catch(() => ({ unavailable: true }));
  Promise.all([
    S.computer ? Promise.resolve(S.computer) : call("computer_status"),
    S.google ? Promise.resolve(S.google) : call("google_status"),
  ])
    .then(async ([computer, google]) => {
      if (!ownsIssueRequest(request)) return;
      S.computer = computer; adoptGoogleStatus(google);
      paintSettingsReadiness();
      const update = await gwsUpdatePromise;
      if (ownsIssueRequest(request)) S.gwsUpdate = update;
    })
    .catch((error) => handleCaughtError(error, request))
    .finally(() => { S.computerLoading = false; if (ownsIssueRequest(request)) paintSettingsReadiness(); });
}
function attachmentFolderRow(d) {
  const value = d.brity_download_dir || DEFAULT_ATTACHMENT_FOLDER;
  const status = S.attachmentFolderStatus;
  const statusLine = status
    ? `<p class="field-status ${status.ready ? "ok" : "bad"}" style="margin:8px 0 0">${esc(status.detail)}</p>`
    : "";
  return rawRow("첨부파일 다운로드 폴더", `<div class="field"><div class="folder-line">
      <input name="brity_download_dir" value="${esc(value)}">
      <button class="btn-tonal" data-action="attachment-folder-choose">폴더 선택</button>
    </div>${statusLine}</div>`);
}
function stepGoogleLogin() {
  ensureComputerStatus();
  const g = S.google;
  const title = S.login?.logging_out ? "Google 로그아웃을 확인하고 있어요"
    : S.login ? "Google 로그인을 확인하고 있어요"
    : googleAuthCheckFailed(g) ? "Google 계정 상태를 확인해 주세요"
    : g && g.logged_in && !isGoeduGoogleStatus(g)
    ? "이 계정으로는 진행할 수 없어요"
    : isGoeduGoogleStatus(g) && !isGoogleReady(g)
      ? "Google 권한을 다시 확인해 주세요"
    : isGoogleReady(g)
      ? "Google 계정으로 확인됐어요"
      : "Google 계정으로 로그인해 주세요";
  const lead = S.login?.logging_out
    ? "Teacher Manager의 Google 연결을 해제하고 있어요. 완료되면 이 화면에 표시돼요."
    : S.login
      ? "브라우저에서 Google 로그인을 마치면 이 화면에 자동으로 반영돼요."
    : googleAuthCheckFailed(g)
      ? "다시 점검을 눌러 현재 Google 계정 상태를 확인해 주세요."
    : g && g.logged_in && !isGoeduGoogleStatus(g)
      ? "Teacher Manager 앱은 Google 계정으로 사용할 수 있습니다."
    : isGoeduGoogleStatus(g) && !isGoogleReady(g)
      ? "현재 Google 계정에서 필요한 권한을 다시 승인해 주세요."
    : isGoogleReady(g)
      ? "확인된 Google 계정으로 일정·할 일·출결 자료·결석 신고서·학급 단톡방을 사용할 수 있어요."
      : "일정·할 일·출결 자료·결석 신고서·학급 단톡방을 사용할 Google 계정으로 로그인해 주세요.";
  return `<h1>${title}</h1><p class="sub">${lead}</p>${googleAccountSectionHtml(true)}`;
}
async function validateGoogleLogin() {
  try {
    adoptGoogleStatus(await call("google_status"));
  } catch (error) {
    if (error instanceof StaleAccountResponse) throw error;
    return GOOGLE_AUTH_CHECK_MESSAGE;
  }
  if (S.google.error_code === "GWS_ACCOUNT_STORAGE_OUTSIDE_USER") {
    const problem = OAUTH_REPAIR_MESSAGES.GWS_ACCOUNT_STORAGE_OUTSIDE_USER;
    return `${problem.status}. ${problem.repair}`;
  }
  if (!S.google.gws_runtime_ready) return "Google 연결 기능을 사용할 수 없어요. Teacher Manager 설치 파일을 다시 실행해 주세요.";
  if (S.google.oauth_client_conflict) return "Google 로그인 설정이 서로 달라 로그인을 시작할 수 없어요. Teacher Manager 설치 파일을 다시 실행해 주세요.";
  if (!S.google.oauth_client_ready) return "프로그램의 Google 로그인에 필요한 파일이 없습니다. 최신 Teacher Manager 설치 파일을 다시 실행해 주세요.";
  if (googleAuthCheckFailed(S.google)) return GOOGLE_AUTH_CHECK_MESSAGE;
  if (!S.google.logged_in) return FIELD_MESSAGES["google-login"];
  if (!isGoogleReady(S.google)) return googleAuthorizationMessage(S.google);
  return "";
}
function settingsSectionHtml(d, includeGoogle = true) {
  // 이 화면에서 확인: 제품에 포함된 Python · 문서 읽기 · Microsoft Edge WebView2
  ensureComputerStatus();
  if (d.autostart === undefined) d.autostart = true;
  if (d.error_reports_enabled === undefined) d.error_reports_enabled = true;
  ensureHotkeyState(d);
  return `<div id="settings-readiness">${computerSectionHtml(includeGoogle)}</div>
    <div class="section-h">동작 설정</div>
    ${formTable(
      hotkeyRow(d) +
      rawRow("Windows 시작 시 자동 실행 (권장)",
        `<div class="field"><label class="check" style="margin:0"><input type="checkbox" name="autostart" ${d.autostart ? "checked" : ""}> 켜기</label></div>`) +
      rawRow("오류 자동 보고",
        `<div class="field"><label class="check" style="margin:0"><input type="checkbox" name="error_reports_enabled" ${d.error_reports_enabled ? "checked" : ""}> 켜기</label><span class="hint">문제 화면이 뜨면 어느 작업에서 무엇이 잘못됐는지를 개발자에게 자동으로 보내요.</span></div>`) +
      attachmentFolderRow(d)
    )}
    <p class="hint">오래된 Word·Excel·PowerPoint 파일은 Microsoft Office가 설치되어 있어야 안정적으로 읽을 수 있어요.</p>
    <p class="hint">메시지 본문과 읽어낸 첨부 내용은 분석을 위해 Gemini로 전송돼요. 사진과 스캔 PDF는 파일 화면도 함께 전송돼요.</p>`;
}
function stepSettings() {
  return `
    <h1>이 컴퓨터에서의 동작을 정할게요</h1>
    <p class="sub">Brity 대화방에 메시지를 띄우고 단축키를 누르면 화면에서 직접 읽어 바로 구글에 등록해요.</p>
    ${settingsSectionHtml(S.draft.bridge, false)}`;
}
function syncMessengerDraft() {
  S.draft.bridge.hotkey = S.draft.bridge.hotkey || DEFAULT_HOTKEY;
  const auto = document.querySelector('[name="autostart"]');
  if (auto) S.draft.bridge.autostart = auto.checked;
  const report = document.querySelector('[name="error_reports_enabled"]');
  if (report) S.draft.bridge.error_reports_enabled = report.checked;
  const folder = document.querySelector('[name="brity_download_dir"]');
  if (folder) S.draft.bridge.brity_download_dir = folder.value.trim();
}
async function validateAttachmentFolder() {
  syncMessengerDraft();
  const status = await call(
    "check_attachment_folder", S.draft.bridge.brity_download_dir || DEFAULT_ATTACHMENT_FOLDER
  );
  S.attachmentFolderStatus = status;
  if (!status.ready) { render(); return status.detail; }
  return "";
}
async function validateSettings() {
  syncMessengerDraft();
  const rows = [];
  const folderProblem = await validateAttachmentFolder();
  if (folderProblem) rows.push(issue("settings.attachment-folder", "brity_download_dir", folderProblem));
  const googleProblem = await validateGoogleLogin();
  if (googleProblem) rows.push(issue("settings.google-login", "google-login", googleProblem));
  setFieldIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
}

// 숫자에 직접 기대지 않도록 함수를 이름으로 둔 뒤 배선한다.
stepBodies[2] = stepGoogleLogin;
validators[2] = validateGoogleLogin;
stepBodies[3] = stepIdentity;
validators[3] = validateIdentity;
stepBodies[4] = stepDay;
validators[4] = validateDay;
stepBodies[5] = stepTimetable;
validators[5] = validateTimetable;
stepBodies[6] = stepRoster;
validators[6] = validateRoster;
stepBodies[7] = stepSettings;
validators[7] = validateSettings;
stepBodies[8] = stepConnect;
validators[8] = validateConnect;

bindActions({
  "settings-refresh": (_el, request) => refreshSettingsStatus(request),
  "goto-settings": async () => {
    // 연결 화면에서 로그인 문제를 발견해 설정으로 보낸 경우에는 출발 화면을 기억한다.
    // 선생님이 로그인을 마치면 별도 "다시 불러오기" 없이 그 화면으로 돌아가
    // Calendar·Tasks 목록을 자동으로 다시 읽는다.
    googleLoginResumeGeneration += 1;
    googleLoginResumeContext = captureGoogleResumeContext();
    if (S.mode === "wizard") { await goStepAsync(2); return; }
    await openCard("settings");
  },
  "attachment-folder-choose": async (_el, request) => {
    syncMessengerDraft();
    const result = await call(
      "choose_attachment_folder", S.draft.bridge.brity_download_dir || ""
    );
    if (result.cancelled) return;
    S.draft.bridge.brity_download_dir = result.path;
    editDirtyFields.add("brity_download_dir");
    S.attachmentFolderStatus = await call("check_attachment_folder", result.path);
    render();
    await autoSaveSettings(false, request);
  },
  "hk-record": async () => {
    syncMessengerDraft();
    await stopHotkeyRecording();
    await call("hotkey_recording_start");
    hotkeyCapture.generation += 1;
    hotkeyCapture.active = true;
    hotkeyCapture.paused = true;
    hotkeyCapture.down.clear();
    hotkeyCapture.captured.clear();
    S.hk.recording = true;
    S.hk.status = { kind: "ok", text: "원하는 조합을 눌러 주세요" };
    hotkeyCapture.timer = setTimeout(() => {
      stopHotkeyRecording("시간이 지나 취소했어요. 다시 눌러 주세요").then(render);
    }, 15000);
    render();
  },
  "check-key": async () => {
    syncGeminiDraft();
    const r = await call(
      "verify_gemini_key", S.draft.bridge.gemini_api_key, S.draft.bridge.gemini_model
    );
    const kind = r.status === "ok" ? "g" : ["missing", "invalid"].includes(r.status) ? "r" : "y";
    S.keyStatus = { kind, text: KEY_MESSAGES[r.status] || r.status };
    render();
  },
});

/* The supplied picture guide stays below the initial setup row. */
function studentChatGuideHtml() {
  return `<p class="attendance-picture-guide"><button type="button" class="text-link" data-action="attendance-chat-space-guide" data-preserve-issue="true">학급 단톡방 준비 방법 그림으로 보기</button></p>`;
}

/* ---------- 8단계: 모두 저장 ---------- */
function summaryRow(label, value) {
  return `<div class="row"><span class="name">${esc(label)}</span><span class="st">${esc(value || "—")}</span></div>`;
}
stepBodies[9] = function stepFinish() {
  const p = S.draft.profile;
  return `
    <h1>설정을 저장할게요</h1>
    <p class="sub">내 정보·시간표·메신저 설정을 저장하고 도우미를 시작한 뒤 홈으로 가요</p>
    <div class="panel">
      ${summaryRow("이름 · 학교", `${p["선생님이름"] || ""} · ${p["학교명"] || ""}`)}
      ${summaryRow("담임", p["담임여부"] === "예" ? `${p["담임학년"]}학년 ${p["담임반"]}반` : "아니오")}
      ${summaryRow("업무 캘린더", p["업무캘린더이름"])}
      ${summaryRow("학사일정 캘린더", p["학사일정캘린더이름"])}
      ${summaryRow("업무 할일 목록", p["업무Tasks목록이름"])}
      ${summaryRow("조종례 안내 목록", p["담임여부"] === "예" ? p["담임안내Tasks목록이름"] : "비담임 — 없음")}
      ${summaryRow("Gemini API key", S.draft.bridge.gemini_api_key ? "입력됨" : "나중에 (홈에서 안내)")}
      ${summaryRow("단축키", S.draft.bridge.hotkey || DEFAULT_HOTKEY)}
    </div>
    <div class="foot">
      <button class="btn-prev" data-action="go-prev">${icon("chevron-left", "small")} 이전</button>
      <button class="btn" data-action="apply-all" data-busy-text="저장하는 중… (1~2분 걸릴 수 있어요)">모두 저장</button>
    </div>`;
};
bindActions({
  "apply-all": async () => {
    try {
      adoptGoogleStatus(await call("google_status"));
    } catch (error) {
      setBanner("warn", GOOGLE_AUTH_CHECK_MESSAGE);
      return;
    }
    if (!isGoogleReady(S.google)) {
      setBanner("warn", googleAuthorizationMessage(S.google));
      return;
    }
    // 모두 저장 화면엔 격자 입력이 없다 — draft에 저장된 최신 값을 그대로 쓴다.
    if (attendanceUiEnabled() && !(await refreshAttendanceWizardGate())) {
      const message = attendanceWizardGateMessage();
      S.connectTab = "attendance";
      await goStepAsync(8);
      setAttendanceGateBanner(message);
      return;
    }
    await ensureGridLoaded();
    const results = await call("apply_all", S.draft.profile, S.draft.grid, S.draft.bridge);
    const failed = results.filter((r) => r.status === "failed");
    // 성공이든 실패든 곧장 홈으로 — 실패 항목은 홈 점검과 출결 탭이 이유를 보여준다.
    await call("finish_setup");
    if (attendanceUiEnabled()) {
      try {
        S.attendance = await call("attendance_status");
      } catch (_error) {
        S.attendance = null;
      }
    } else {
      S.attendance = null;
    }
    S.attendanceScriptUpdate = null;
    clearAttendanceScriptDialogState();
    S.chatStatus = null;
    S.connectTab = S.attendance && ["connection-repair-required", "script-check-required", "script-update-required"].includes(S.attendance.state)
      ? "attendance"
      : "messenger";
    S.mode = "home";
    S.checks = [];
    S.applyResults = null;
    S.firstHomeNotice = failed.length === 0;
    showToast(failed.length
      ? "일부 항목을 준비하지 못했어요 — 홈에서 확인해 주세요"
      : "설정을 모두 저장했어요");
    render();
  },
  "dismiss-first-notice": () => { S.firstHomeNotice = false; render(); },
});

/* ---------- 화면별 문제 수집 ---------- */
function identityIssues() {
  const p = S.draft.profile;
  const rows = [];
  if (!p["선생님이름"]) rows.push(issue("identity.teacher-name", "선생님이름", FIELD_MESSAGES["선생님이름"]));
  if (!p["학교명"]) rows.push(issue("identity.school-name", "학교명", FIELD_MESSAGES["학교명"]));
  if (!p["학교급"]) rows.push(issue("identity.school-level", "학교급", FIELD_MESSAGES["학교급"]));
  if (!p["담임여부"]) rows.push(issue("identity.homeroom", "담임여부", FIELD_MESSAGES["담임여부"]));
  if (p["담임여부"] === "예") {
    if (!p["담임학년"]) rows.push(issue("identity.homeroom-grade", "담임학년", FIELD_MESSAGES["담임학년"]));
    if (!p["담임반"]) rows.push(issue("identity.homeroom-class", "담임반", FIELD_MESSAGES["담임반"]));
  }
  return rows;
}
function dayIssues() {
  const p = S.draft.profile;
  const rows = [];
  for (const [name, label] of DAY_TIME_FIELDS) {
    if (!TIME_PATTERN.test(p[name] || "")) {
      rows.push(issue(`identity.time.${name}`, name, `${label}을(를) 시:분으로 입력해 주세요.`));
    }
  }
  for (const name of DAY_LAST_FIELDS) {
    if (!/^[1-7]$/.test(p[name] || "")) {
      rows.push(issue(`identity.last-period.${name[0]}`, name, `${name[0]}요일 마지막 교시를 골라 주세요.`));
    }
  }
  return rows;
}
function connectIssues() {
  const p = S.draft.profile;
  const modes = linkModes();
  const homeroom = p["담임여부"] === "예";
  const rows = [];
  if (modes.cal === "existing") {
    if (!p["업무캘린더ID"]) rows.push(issue("connect.work-calendar", "업무캘린더ID", FIELD_MESSAGES["업무캘린더ID"], "messenger"));
    if (!p["학사일정캘린더ID"]) rows.push(issue("connect.school-calendar", "학사일정캘린더ID", FIELD_MESSAGES["학사일정캘린더ID"], "messenger"));
  } else {
    if (!p["업무캘린더이름"]) rows.push(issue("connect.work-calendar", "업무캘린더이름", "새로 만들 Calendar 이름을 적어 주세요.", "messenger"));
    if (!p["학사일정캘린더이름"]) rows.push(issue("connect.school-calendar", "학사일정캘린더이름", "새로 만들 Calendar 이름을 적어 주세요.", "messenger"));
    else if (p["업무캘린더이름"] && p["업무캘린더이름"] === p["학사일정캘린더이름"]) {
      rows.push(issue("connect.school-calendar", "학사일정캘린더이름", "두 Calendar 이름을 다르게 적어 주세요.", "messenger"));
    }
  }
  if (modes.task === "existing") {
    if (!p["업무Tasks목록ID"]) rows.push(issue("connect.work-tasks", "업무Tasks목록ID", FIELD_MESSAGES["업무Tasks목록ID"], "messenger"));
    if (homeroom && !p["담임안내Tasks목록ID"]) rows.push(issue("connect.homeroom-tasks", "담임안내Tasks목록ID", FIELD_MESSAGES["담임안내Tasks목록ID"], "messenger"));
  } else {
    if (!p["업무Tasks목록이름"]) rows.push(issue("connect.work-tasks", "업무Tasks목록이름", "새로 만들 Tasks 목록 이름을 적어 주세요.", "messenger"));
    if (homeroom && !p["담임안내Tasks목록이름"]) {
      rows.push(issue("connect.homeroom-tasks", "담임안내Tasks목록이름", "조종례 전달용 목록 이름을 적어 주세요.", "messenger"));
    } else if (homeroom && p["업무Tasks목록이름"] === p["담임안내Tasks목록이름"]) {
      rows.push(issue("connect.homeroom-tasks", "담임안내Tasks목록이름", "두 Tasks 목록 이름을 다르게 적어 주세요.", "messenger"));
    }
  }
  if (isGoogleReady(S.google)) {
    for (const [field, id] of Object.entries(p)) {
      if (!GOOGLE_TARGET_ID_FIELDS.includes(field) || !id || (field === "담임안내Tasks목록ID" && !homeroom)) continue;
      const kind = field.includes("Tasks") ? "tasklists" : "calendars";
      if (modes[kind === "tasklists" ? "task" : "cal"] !== "existing") continue;
      const read = S.listReads[kind];
      const status = S.googleTargetStatuses?.[field];
      const key = {"업무캘린더ID":"connect.work-calendar", "학사일정캘린더ID":"connect.school-calendar", "업무Tasks목록ID":"connect.work-tasks", "담임안내Tasks목록ID":"connect.homeroom-tasks"}[field];
      const inCurrentList = currentListRows(kind).some(row => row.id === id);
      // Keep waiting selections in validation/Next gates, but do not paint them
      // as failed connections. A returned list still awaits its target check.
      const pending = S.linkLoading && read?.context === googleListContext()
        && (read.phase === "pending" || (read.phase === "ready" && inCurrentList
          && (status?.id !== id || !status?.state)));
      if (pending) {
        rows.push({...issue(key, field, "현재 연결을 확인하고 있어요.", "messenger"), pending: true});
        continue;
      }
      if (read && !currentListRows(kind).some(row => row.id === id)) {
        rows.push(issue(key, field, read.phase === "pending" ? "현재 목록을 확인하고 있어요." : "저장된 선택을 현재 목록에서 확인하지 못했어요. 연결을 다시 확인해 주세요.", "messenger"));
      }
    }
    for (const [field, status] of Object.entries(S.googleTargetStatuses || {})) {
      if (!GOOGLE_TARGET_ID_FIELDS.includes(field) || status.id !== p[field] || !p[field]) continue;
      if (field === "담임안내Tasks목록ID" && !homeroom) continue;
      if (modes[field.includes("Tasks") ? "task" : "cal"] !== "existing") continue;
      if (["unavailable", "check_failed"].includes(status.state)) {
        const key = {"업무캘린더ID":"connect.work-calendar", "학사일정캘린더ID":"connect.school-calendar", "업무Tasks목록ID":"connect.work-tasks", "담임안내Tasks목록ID":"connect.homeroom-tasks"}[field];
        rows.push({...issue(key, field, status.detail, "messenger"), optional: status.state === "check_failed"});
      }
    }
  }
  if (!(S.draft.bridge.gemini_api_key || "").trim()) {
    // key는 선택(승인 결정 2026-07-14 §4⑦) — 홈 문제 집계에는 잡히지만 진행은 막지 않는다.
    rows.push({ ...issue("connect.gemini-key", "gemini_api_key", FIELD_MESSAGES["gemini_api_key"], "messenger"), optional: true });
  }
  return rows;
}

/* ---------- 점검 결과 요약 ---------- */
function uniqueChecks(rows) {
  const seen = new Set();
  return (rows || []).filter((row) => {
    if (seen.has(row.key)) return false;
    seen.add(row.key);
    return true;
  });
}
// 편집 중인 카드 — 그 카드의 입력칸은 저장된 점검 대신 현재 입력을 본다.
const WIZARD_CARD_BY_STEP = { 3: "identity", 4: "identity", 5: "timetable", 6: "timetable", 7: "settings", 8: "connect" };
function editingCard() {
  if (S.mode === "edit") return S.edit || "";
  if (S.mode === "wizard") return WIZARD_CARD_BY_STEP[S.step] || "";
  return "";
}
function effectiveChecks() {
  const editing = editingCard();
  const currentConnections = connectIssues();
  const saved = uniqueChecks(S.checks).filter(
    (row) => attendanceUiEnabled() || row.tab !== "attendance"
  ).map((row) => {
    if (checksRetry.dirtyCards.has(row.card)) return { ...row, ok: null,
      detail: checksRetry.failedAt ? "점검에 실패했어요. 다시 확인해 주세요." : "현재 상태를 점검하고 있어요." };
    if (row.card === "connect" && GOOGLE_TARGET_ID_FIELDS.includes(row.target) && isGoogleReady(S.google)) {
      const read = S.listReads[row.target.includes("Tasks") ? "tasklists" : "calendars"];
      if (read?.context === googleListContext()) {
        const current = currentConnections.find(issue => issue.target === row.target);
        if (current) return {...row, ok: current.pending ? null : false, pending: Boolean(current.pending),
          detail: current.message, fix: current.pending ? "" : current.message};
        const target = S.googleTargetStatuses?.[row.target];
        if (target?.id === S.draft.profile[row.target] && target.state === "ready") {
          return {...row, ok: true, pending: false, detail: target.detail || "연결됨", fix: ""};
        }
      }
    }
    const a = S.attendance;
    if (row.key === "connect.attendance" && attendanceUiEnabled() && isGoogleReady(S.google) && S.attendanceLoading) {
      return {...row, ok: null, pending: true, detail: "확인 중…", fix: ""};
    }
    if (row.key === "connect.attendance" && attendanceUiEnabled() && a
        && String(a.account || a.current_user || "").trim().toLowerCase() === verifiedGoogleAccount(S.google)) {
      const presentation = attendancePresentation(a);
      const pending = Boolean(presentation.pending || presentation.phase === "checking");
      if (presentation.phase !== "connected") return { ...row, ok: pending ? null : false, pending,
        detail: presentation.text, fix: presentation.tone === "warn" ? (a.detail || presentation.text) : "" };
    }
    if (row.key !== "connect.attendance" || !attendanceUiEnabled()
        || !a || !isGoogleReady(S.google) || S.attendanceLoading || S.attendanceReadFailed
        || !a.state || ["checking", "installing", "unknown", "unavailable"].includes(a.state)) return row;
    const account = String(a.account || a.current_user || "").trim().toLowerCase();
    if (!account || account !== verifiedGoogleAccount(S.google)) return row;
    // Keep the backend check's meaning: workbook preparation only. Current
    // setup, roster and Chat requirements still own their notices and Next gate.
    const ok = a.state === "ready" ? true
      : ["gws-required", "login-required", "account-required", "auth-error", "profile-required"].includes(a.state) ? null : false;
    const detail = a.detail || (ok === true ? "출결 업무 준비가 끝났어요" : row.detail || "");
    return { ...row, ok, detail, fix: ok === false ? (a.detail || row.fix || "") : "" };
  });
  const kept = editing
    ? saved.filter((row) => !(row.card === editing && EDITABLE_TARGETS.has(row.target)))
    : saved;
  const fieldRows = Object.values(S.fieldIssues || {}).filter((row) => editing !== "connect" || !LIVE_CONNECT_TARGETS.has(row.target));
  if (editing === "connect") {
    // Programmatic changes need the same current validation as keyboard changes.
    fieldRows.push(...currentConnections.filter((row) => !fieldRows.some((existing) => existing.target === row.target)));
  }
  const live = fieldRows.map((row) => ({
    key: row.key, label: row.target, ok: row.pending ? null : false, pending: Boolean(row.pending),
    detail: row.pending ? row.message : "", fix: row.pending ? "" : row.message,
    card: editing || "", tab: row.tab || "", target: row.target,
  }));
  const rows = uniqueChecks(kept.concat(live)).filter(row => row.key !== "connect.class-space");
  if (connectionRefreshActive()) rows.push({key:"connect.refresh", label:"연결", card:"connect",
    tab:"", target:"", ok:null, pending:true, detail:"연결 확인 중…", fix:""});
  if (attendanceUiEnabled() && isHomeroomTeacher() && isGoogleReady(S.google)) {
    const state = classRoomReadiness(), presentation = attendancePresentation(S.attendance);
    const waiting = state === "unverified" && (S.attendanceLoading || presentation.pending);
    const pending = state === "loading" || waiting;
    const detail = waiting ? presentation.text : classRoomReadinessMessage(state);
    rows.push({key:"connect.class-space", label:"학급 단톡방", card:"connect", tab:"attendance",
      target:"class-space-select", ok:pending ? null : state === "ready", pending,
      detail, fix:pending || state === "ready" ? "" : detail});
  }
  if (!S.google) return rows;
  // The latest shared Google result wins over a previous home snapshot.
  const withoutGoogle = rows.filter((row) => !["settings.google-login", "settings.goedu-account", "connect.google-login"].includes(row.key));
  const ready = isGoogleReady(S.google);
  const detail = ready ? (S.google.user || "") : googleAuthCheckFailed(S.google) ? GOOGLE_AUTH_CHECK_MESSAGE : googleAuthorizationMessage(S.google);
  const googleRows = [{ key: "settings.google-login", label: "Google 로그인과 권한", ok: ready, detail, fix: ready ? "" : detail, card: "settings", target: "google-login" }];
  if (!ready) googleRows.push({ ...googleRows[0], key: "connect.google-login", card: "connect" });
  return withoutGoogle.concat(googleRows);
}
function checksForCard(card) {
  return effectiveChecks().filter((row) => row.card === card);
}
function checksForTab(tab) {
  return effectiveChecks().filter((row) => row.tab === tab);
}
function checkSummary(rows) {
  const counted = rows.filter((row) => row.ok !== null && row.ok !== undefined);
  const bad = counted.filter((row) => row.ok === false).length;
  const pending = rows.filter((row) => row.pending).length;
  return { good: counted.length - bad, total: counted.length, bad, pending };
}

/* ---------- 입력 즉시 재검사: 문제 표시와 숫자를 함께 줄인다 ---------- */
function currentScreenIssues() {
  if (S.mode === "edit") {
    if (S.edit === "identity") { syncProfileFields(); syncDayFields(); return identityIssues().concat(dayIssues()); }
    if (S.edit === "connect") { syncConnectFields(); return connectIssues(); }
    return null;
  }
  if (S.mode === "wizard") {
    if (S.step === 3) { syncProfileFields(); return identityIssues(); }
    if (S.step === 4) { syncDayFields(); return dayIssues(); }
    if (WIZARD_CARD_BY_STEP[S.step] === "connect") {
      syncConnectFields();
      return connectIssues().filter((row) => !row.optional);
    }
  }
  return null;
}
function updateTabBadges() {
  document.querySelectorAll("[data-tab-count]").forEach((el) => {
    const count = checkSummary(checksForTab(el.dataset.tabCount)).bad;
    el.textContent = count ? String(count) : "";
    el.style.display = count ? "" : "none";
  });
}
function updateIssueDom() {
  document.querySelectorAll("[name]").forEach((el) => {
    const name = el.getAttribute("name");
    if (!EDITABLE_TARGETS.has(name)) return;
    const wrap = el.closest(".field");
    if (!wrap) return;
    const message = fieldError(name);
    wrap.classList.toggle("has-error", Boolean(message));
    if (message) el.setAttribute("aria-invalid", "true");
    else el.removeAttribute("aria-invalid");
    let note = wrap.querySelector(".field-error");
    if (message) {
      if (!note) {
        note = document.createElement("span");
        note.className = "field-error";
        wrap.appendChild(note);
      }
      note.textContent = message;
    } else if (note) {
      note.remove();
    }
  });
  updateTabBadges();
}
function liveRevalidate() {
  const rows = currentScreenIssues();
  if (rows === null) return;
  replaceEditableIssues(rows);
  updateIssueDom();
}
document.addEventListener("input", (event) => {
  const target = event.target;
  const data = (target && target.dataset) || {};
  // 시간표 격자·하루일과 시/분은 name이 없는 입력 — 즉시 draft에 동기화해
  // [이전]·레일 이동 때 값이 유실되지 않게 한다 (2026-07-20 감사 H1·H2).
  if (data.grid !== undefined) { syncGridFields(); return; }
  if (data.dayHour !== undefined || data.dayMinute !== undefined) { liveRevalidate(); return; }
  const name = target && target.name;
  if (!name || !EDITABLE_TARGETS.has(name)) return;
  liveRevalidate();
});
document.addEventListener("input", (event) => {
  if (event.target && event.target.name === "class-space-name") {
    S.spaceDraftName = event.target.value;
  }
});

/* ---------- 처리 관측: 방금 작업 카드 + 처리한 메시지 목록 ---------- */
const LIVE_STEPS = [["capture", "읽는 중"], ["analyze", "분석 중"], ["register", "등록 중"], ["done", "완료"]];
const LIVE_INDEX = { capture: 0, analyze: 1, register: 2, done: 3 };
function fmtShort(when) {
  const m = String(when || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(when || "");
  const md = `${Number(m[2])}/${Number(m[3])}`;
  return m[4] ? `${md} ${m[4]}:${m[5]}` : md;
}
function liveCardHtml() {
  const p = S.progress;
  if (!p || !p.active) return "";
  const failing = p.step === "fail";
  const doneAll = p.step === "done";
  const nowIndex = LIVE_INDEX[p.step] ?? 3;
  const failAt = failing ? (LIVE_INDEX[S.lastLiveStep] ?? 3) : -1;
  const parts = [];
  LIVE_STEPS.forEach(([key, label], i) => {
    let cls = "";
    if (failing) cls = i < failAt ? "done" : i === failAt ? "fail" : "";
    else if (doneAll) cls = "done";
    else cls = i < nowIndex ? "done" : i === nowIndex ? "now" : "";
    const mark = cls === "done" ? "✓" : cls === "fail" ? "✗" : String(i + 1);
    parts.push(`<div class="stepv ${cls}"><div class="sic">${mark}</div><div class="lb">${esc(label)}</div></div>`);
    if (i < LIVE_STEPS.length - 1) {
      const full = (doneAll || (!failing && i < nowIndex) || (failing && i < failAt)) ? " full" : "";
      parts.push(`<div class="sbar${full}"></div>`);
    }
  });
  const result = doneAll ? "result-ok" : failing ? "result-bad" : "";
  const title = doneAll ? "방금 작업 — 완료" : failing ? "방금 작업 — 실패" : "방금 작업 — 처리 중";
  const msg = (doneAll || failing) && p.message
    ? `<div class="live-msg ${doneAll ? "ok" : "bad"}">${esc(p.message)}</div>` : "";
  return `<div class="live-card ${result}"><div class="live-title">${esc(title)}</div><div class="steps">${parts.join("")}</div>${msg}</div>`;
}

const ITEM_RES = { created: ["등록", "ok"], duplicate: ["중복", "dup"], failed: ["실패", "bad"], preview: ["미리보기", "dup"] };
const RESULT_GROUPS = [
  { kind: "calendar", title: "캘린더" },
  { kind: "task", title: "Tasks" },
  { kind: "notice", title: "학생 안내 · Google Sheet" },
];
function resultLabel(it) {
  if (it.kind === "notice" && it.result === "created") return ["대기", "ok"];
  return ITEM_RES[it.result] || ["", "dup"];
}
function groupedItemsHtml(cap) {
  return RESULT_GROUPS.map((group) => {
    const items = (cap.items || []).filter((item) => item.kind === group.kind);
    if (!items.length) return "";
    const rows = items.map((it) => {
      const [label, cls] = resultLabel(it);
      const when = it.kind === "calendar" ? fmtShort(it.when) : "";
      const detail = it.detail ? `<div class="item-detail">${esc(it.detail)}</div>` : "";
      return `<div class="item"><span class="t">${esc(when)}</span>` +
        `<span class="title">${esc(it.target)} — ${esc(it.title)}</span>` +
        `<span class="res ${cls}">${esc(label)}</span></div>${detail}`;
    }).join("");
    const guide = group.kind === "notice"
      ? `<div class="sheet-guide">쪽지 대장에 발송 대기로 넣었어요. 아직 학생에게 보내지는 않았습니다.</div>`
      : "";
    return `<section class="result-group"><div class="result-group-head">${esc(group.title)} ${items.length}건</div>${rows}${guide}</section>`;
  }).join("");
}
function attachmentResultLead(cap) {
  const count = Number(cap.attachment_count || 0);
  const names = Array.isArray(cap.attachment_names) ? cap.attachment_names.filter(Boolean) : [];
  const read = count ? `메시지 본문과 첨부파일 ${count}개를 함께 읽었어요.` : "";
  const files = names.length ? ` ${names.map(esc).join(", ")}` : "";
  const status = cap.attachment_link_status || "";
  let linked = "";
  if (status === "confirmed") linked = `일정에 연결 확인:${files}`;
  else if (status === "failed") linked = `첨부파일을 일정에 연결했는지 확인하지 못했어요.${files}`;
  else if (status === "no-calendar") linked = `첨부파일을 읽었지만 연결할 일정이 없었어요.${files}`;
  else if (status === "not-registered") linked = `등록이 중단되어 첨부파일을 일정에 연결하지 않았어요.${files}`;
  else if (names.length) linked = `첨부파일:${files}`;
  if (!read && !linked) return "";
  return `<div class="attachment-result-lead">${esc(read)}${read && linked ? "<br>" : ""}${linked}</div>`;
}
function captureMessageLead(cap) {
  const sender = String(cap.message_sender || "").trim();
  const sentAt = String(cap.message_sent_at || "").trim();
  const preview = String(cap.message_preview || "").trim();
  if (sender || sentAt || preview) {
    const meta = [sender, sentAt ? fmtShort(sentAt) : ""].filter(Boolean).join(" · ");
    return `<div class="capture-context"><b>메시지</b>${meta ? ` · ${esc(meta)}` : ""}` +
      `${preview ? `<div>${esc(preview)}</div>` : ""}</div>`;
  }
  if (!cap.ok || cap.stage === "duplicate") {
    return '<div class="capture-context"><b>메시지</b> · 이전 기록이라 어떤 메시지였는지 확인할 수 없어요.</div>';
  }
  return "";
}
function captureOriginalLead(cap) {
  if (!cap.original_when) return "";
  return `<div class="capture-context">처음 등록 · ${esc(fmtShort(cap.original_when))}</div>`;
}
const EMPTY_ITEM_LINES = {
  done: "분석 결과 일정·할 일이 없어 아무것도 만들지 않았어요.",
  duplicate: "이미 등록한 메시지라 새로 만들지 않았어요.",
};
function capKey(cap) { return `${cap.when}|${cap.source_hash || ""}`; }
function capHtml(cap) {
  const key = capKey(cap);
  const open = S.capsOpen[key] ? " open" : "";
  const dot = cap.ok
    ? (cap.mode === "trial" ? '<span class="dot try">◐</span>' : '<span class="dot ok">✓</span>')
    : '<span class="dot bad">✗</span>';
  const fresh = S.freshWhen && cap.when === S.freshWhen ? '<span class="badge b">방금</span>' : "";
  const trial = cap.mode === "trial" ? '<span class="badge y">시험</span>' : "";
  const items = captureMessageLead(cap) + captureOriginalLead(cap) + attachmentResultLead(cap) + groupedItemsHtml(cap);
  const emptyLine = !(cap.items || []).length && cap.ok
    ? `<div class="item"><span class="none">${esc(EMPTY_ITEM_LINES[cap.stage] || "만든 항목이 없어요.")}</span></div>` : "";
  const reason = cap.reason ? `<div class="cap-reason">${esc(cap.reason)}</div>` : "";
  const retry = !cap.retryable && cap.retry ? `<div class="capture-retry">${esc(cap.retry)}</div>` : "";
  const retryButton = cap.retryable
    ? `<div class="capture-retry-action"><button class="btn-tonal" data-action="cap-retry" data-source-hash="${esc(cap.source_hash || "")}" data-when="${esc(cap.when || "")}" data-busy-text="다시 처리하는 중…">실패한 항목 다시 시도</button></div>`
    : "";
  return `<div class="cap${open}">
    <button class="cap-row" data-action="cap-toggle" data-key="${esc(key)}">
      ${dot}<span class="cap-when">${esc(fmtShort(cap.when))}</span><span class="cap-sum">${esc(cap.summary || "")}</span>${fresh}${trial}<span class="chev">▶</span>
    </button>
    <div class="cap-items">${items}${emptyLine}${retry}${retryButton}</div>${reason}</div>`;
}
const CAP_PAGE_SIZE = 10;
function capturePageNumbers(current, total) {
  if (total <= 1) return [];
  const wanted = new Set([1, total]);
  let start = Math.max(1, current - 2);
  let end = Math.min(total, current + 2);
  if (current <= 3) end = Math.min(total, 5);
  if (current >= total - 2) start = Math.max(1, total - 4);
  for (let page = start; page <= end; page += 1) wanted.add(page);
  return [...wanted].sort((a, b) => a - b);
}
function capPagesHtml() {
  const total = S.capsTotalPages || 0;
  if (total <= 1) return "";
  const current = S.capsPage || 1;
  const pages = capturePageNumbers(current, total);
  const numbered = [];
  pages.forEach((page, index) => {
    if (index && page - pages[index - 1] > 1) numbered.push('<span class="cap-page-gap">…</span>');
    numbered.push(`<button class="cap-page-number${page === current ? " current" : ""}" data-action="cap-page" data-page="${page}"${page === current ? ' aria-current="page"' : ""}>${page}</button>`);
  });
  return `<nav class="cap-pages" aria-label="처리한 메시지 페이지">
    <button class="cap-page-move" data-action="cap-page" data-page="${current - 1}"${current === 1 ? " disabled" : ""}>이전</button>
    ${numbered.join("")}
    <button class="cap-page-move" data-action="cap-page" data-page="${current + 1}"${current === total ? " disabled" : ""}>다음</button>
  </nav>`;
}
function capListHtml() {
  if (S.caps === null) {
    return `<div class="panel"><div class="row"><span class="st">기록을 불러오는 중이에요…</span></div></div>`;
  }
  if (!S.caps.length) {
    if (S.capsError) {
      // 못 읽은 것을 "없다"고 말하면 사실과 다른 단정 + 잘못된 행동 유도가 된다.
      return `<div class="panel"><div class="row"><span class="st">기록을 불러오지 못했어요 — 잠시 뒤 홈을 다시 열어 보세요.</span></div></div>`;
    }
    return `<div class="panel"><div class="row"><span class="st">아직 처리한 메시지가 없어요 — Brity 메시지에서 단축키를 눌러 보세요.</span></div></div>`;
  }
  return `<div class="panel">${S.caps.map(capHtml).join("")}</div>${capPagesHtml()}`;
}
function applyCapturePage(data) {
  S.caps = Array.isArray(data.items) ? data.items : [];
  S.capsPage = Number(data.page || 1);
  S.capsTotalPages = Number(data.total_pages || 0);
  S.capsError = false;
}
async function loadCapturePage(page, options = {}) {
  if (S.capsLoading) return;
  S.capsLoading = true;
  try {
    let data = await callWithLocalRecovery(() => call("capture_history_page", page, CAP_PAGE_SIZE));
    // 오래된 화면 시험 도구와 함께 열렸을 때는 기존 최근 목록 응답도 받아들인다.
    if (!data || !Array.isArray(data.items)) {
      const rows = await callWithLocalRecovery(() => call("recent_captures"));
      data = {
        items: rows.slice(0, CAP_PAGE_SIZE), page: 1, page_size: CAP_PAGE_SIZE,
        total: rows.length, total_pages: Math.ceil(rows.length / CAP_PAGE_SIZE),
      };
    }
    applyCapturePage(data);
    S.capsOpen = {};
    if (options.markFresh) S.freshWhen = S.caps.length ? S.caps[0].when : "";
  } catch (error) {
    if (!options.quiet && S.caps === null) {
      S.caps = [];
      S.capsPage = 1;
      S.capsTotalPages = 0;
      S.capsError = true;
    }
  } finally {
    S.capsLoading = false;
    if (S.mode === "home") render();
  }
}
bindActions({
  "cap-toggle": (el) => {
    const key = el.dataset.key;
    S.capsOpen[key] = !S.capsOpen[key];
    render();
  },
  "cap-page": (el) => {
    const page = Number(el.dataset.page || 1);
    if (page >= 1 && page <= S.capsTotalPages && page !== S.capsPage) loadCapturePage(page);
  },
  "cap-retry": async (el) => {
    const result = await call(
      "retry_capture",
      String(el.dataset.sourceHash || ""),
      String(el.dataset.when || ""),
    );
    if (result.success) showToast(result.message);
    else setBanner("warn", result.message);
    await loadCapturePage(1, { quiet: true, markFresh: true });
  },
});

/* ---------- 처리 진행 폴링: 평소 2초, active면 0.4초 ---------- */
let capTimer = null;
function stopCapturePoll() { if (capTimer) { clearTimeout(capTimer); capTimer = null; } }
function startCapturePoll() { if (capTimer || S.capPollBusy) return; capTimer = setTimeout(runCapturePoll, 0); }
async function runCapturePoll() {
  capTimer = null;
  if (S.mode !== "home" || S.capPollBusy) return;
  S.capPollBusy = true;
  let active = false;
  try {
    const p = await call("capture_progress");
    active = p.active === true;
    applyProgress(p);
  } catch (error) { /* 폴링 실패는 다음 틱에 다시 */ }
  finally { S.capPollBusy = false; }
  if (S.mode === "home") capTimer = setTimeout(runCapturePoll, active ? 400 : 2000);
}
function applyProgress(p) {
  const prev = S.progress;
  if (!p.active) {
    if (prev && prev.step !== "done" && prev.step !== "fail") {
      S.progress = null; S.lastLiveStep = null; render();  // 스테일 — 카드 제거
    }
    return;
  }
  if (p.step !== "done" && p.step !== "fail") {
    if (!prev || prev.run_id !== p.run_id || prev.step !== p.step) {
      S.progress = p; S.lastLiveStep = p.step; render();
    }
    return;
  }
  if (S.doneShown === p.run_id) return;  // 결과 카드는 한 번만
  S.doneShown = p.run_id;
  S.progress = p;
  render();
  setTimeout(async () => {
    S.progress = null; S.lastLiveStep = null;
    await loadCapturePage(1, { quiet: true, markFresh: true });
  }, 2500);
}

/* ---------- 홈 ---------- */
const CARDS = [
  { key: "identity", icon: "user", title: "내 정보", detail: "이름 · 학교 · 담임 · 하루 일과" },
  { key: "timetable", icon: "table", title: "시간표 · 담임학급 학생명단", detail: "주간 시간표 수정" },
  { key: "settings", icon: "sliders", title: "설정", detail: "컴퓨터 준비 · Google 로그인 · 단축키 · 자동 실행" },
  { key: "connect", icon: "link", title: "연결", detail: "Calendar · Tasks · Gemini API key · 출결 시트" },
];
function cardStatus(card) {
  if (checksRetry.dirtyCards.has(card.key)) return checksRetry.failedAt
    ? { kind: "y", label: "점검 실패 · 다시 확인", bad: false }
    : { kind: "n", label: "변경 사항 점검 중…", bad: false };
  if (!S.checks.length) return { kind: "n", label: "점검 중…", bad: false };
  const summary = checkSummary(checksForCard(card.key));
  if (summary.bad) return { kind: "y", label: "확인 필요", bad: true, summary };
  if (summary.pending) return { kind: "n", label: "확인 중…", bad: false, summary };
  return { kind: "g", label: card.key === "timetable" ? "저장됨" : "정상", bad: false };
}
function cardBadges(card) {
  const st = cardStatus(card);
  if (!st.bad) return badge(st.kind, st.label);
  return badge("y", st.label) + badge("n", `${st.summary.good}/${st.summary.total} 정상`)
    + (st.summary.pending ? badge("n", `${st.summary.pending}개 확인 중…`) : "");
}
function cardDetail(card) {
  if (card.key === "connect" && !attendanceUiEnabled()) {
    return "Calendar · Tasks · Gemini API key";
  }
  return card.detail;
}
/* A failed batch ends here. Explicit recheck or edited fields can start another;
   routine Home redraws cannot repeat a failed request indefinitely. */
const checksRetry = { inflight: false, failedAt: 0, lastGood: [], dirtyCards: new Set(), changes: 0 };
let checksReadVersion = 0;
function invalidateCardChecks(...cards) {
  cards.forEach(card => checksRetry.dirtyCards.add(card));
  checksRetry.changes += 1;
  checksRetry.failedAt = 0;
}
let homeClassRoomPending = null;
function homeClassRoomReading() {
  return Boolean(homeClassRoomPending && homeClassRoomPending.context === chatReadContext());
}
function loadHomeClassRoom(force = false) {
  if (!attendanceUiEnabled() || !isHomeroomTeacher() || !isGoogleReady(S.google)) return;
  const accountContext = () => JSON.stringify([accountUiEpoch, googleContextVersion, googleLoginEpoch, verifiedGoogleAccount(S.google)]);
  const account = accountContext();
  const targetContext = chatReadContext();
  if (homeClassRoomPending?.account === account && homeClassRoomPending.context === targetContext) return homeClassRoomPending.promise;
  const readsAttendance = !S.attendance && !S.attendanceLoading && !S.attendanceReadFailed;
  const promise = (async () => {
    if (!S.attendance && !S.attendanceLoading && !S.attendanceReadFailed) await refreshAttendanceStatus();
    if (account !== accountContext() || S.attendance?.state !== "ready" || S.attendanceLoading || S.attendanceReadFailed) return;
    const context = chatReadContext();
    await loadChatStatus(force, false);
    if (context !== chatReadContext() || !S.chatStatus?.connected || S.chatStatus.read_failed) return;
    await loadChatSpaces(force, false);
  })();
  const pending = homeClassRoomPending = { account, context: targetContext, promise,
    attendanceVersion: readsAttendance ? attendanceStatusReadVersion : null };
  const clear = () => { if (homeClassRoomPending === pending) homeClassRoomPending = null; };
  promise.then(clear, clear);
  return promise;
}
async function refreshChecks() {
  if (checksRetry.inflight) return;
  // A saved-card refresh owns only that scope; an explicit full refresh owns all cards.
  if (!checksRetry.dirtyCards.size) CARDS.forEach(card => checksRetry.dirtyCards.add(card.key));
  checksRetry.failedAt = 0;
  checksRetry.inflight = true;
  const request = beginIssueRequest(false);
  // Home cards remain visible behind Settings and Connection. Navigation and
  // unrelated notices must not discard a valid check for the same account.
  const accountContext = () => JSON.stringify([
    accountUiEpoch, googleContextVersion, googleLoginEpoch, verifiedGoogleAccount(S.google),
  ]);
  const context = accountContext();
  const version = ++checksReadVersion;
  const changes = checksRetry.changes;
  const current = () => version === checksReadVersion && changes === checksRetry.changes
    && context === accountContext();
  const paint = () => {
    if (S.mode === "home") render();
    else if (S.mode === "edit" || S.mode === "about") {
      // Preserve the open form, including text not yet committed by change.
      const background = document.querySelector("#app > .body");
      if (background) background.outerHTML = homeHtml(true);
    }
    // Accepted checks must refresh the visible numbers in both wizard and edit
    // screens without replacing fields the teacher may still be entering.
    updateTabBadges();
  };
  try {
    if (S.checks.length) paint();
    const checks = await call("home_checks");
    if (!current()) return;
    S.checks = checks;
    checksRetry.lastGood = checks;
    checksRetry.failedAt = 0;
    checksRetry.dirtyCards.clear();
    if (S.mode === "home") await loadHomeClassRoom(true);
    return;
  } catch (error) {
    if (!current()) return;
    checksRetry.failedAt = Date.now();
    handleCaughtError(error, request);
  } finally {
    if (version === checksReadVersion) {
      checksRetry.inflight = false;
      // Clear the visible loading state too. A stale result can start a fresh
      // check when Home is visible; modal rendering does not start another one.
      paint();
    }
  }
}
function shouldAutoRefreshChecks() {
  if ((S.checks.length && !checksRetry.dirtyCards.size) || checksRetry.inflight) return false;
  return !checksRetry.failedAt;
}
// A completed connection read belongs to its account/workbook, not to a tab.
const CONNECTION_REFRESH_INTERVAL_MS = 30 * 60 * 1000;
let connectionRefreshTimer = null;
let connectionRefreshPending = null;
function connectionRefreshActive() {
  return Boolean(connectionRefreshPending?.context === googleListContext());
}
function connectionRefreshBlocked() {
  return Boolean(S.login || S.attendanceLoading || S.linkLoading || chatStatusReading()
    || S.chatSpacesLoading || firstSetupInFlight || rosterInFlight || checksRetry.inflight
    || S.attendanceConnection || S.attendanceConnectionBusy || S.attendanceScriptUpdating
    || S.attendanceTransitioning || S.attendanceSaving || S.attendanceAccountAuthorizing
    || S.attendanceRepairing || S.classSpaceSaving
    || attendancePreparePollOn || rosterConnecting || S.rosterEditor?.busy
    || editAutoSavePromise || settingsAutoSavePromise || editDirtyFields.size);
}
function connectionRefreshButtonHtml() {
  const busy = connectionRefreshActive();
  const label = busy ? "연결 확인 중…" : "연결 상태 새로고침";
  return `<button type="button" class="win-refresh${busy ? " checking" : ""}" data-action="connection-refresh"
    title="${label}" aria-label="${label}" aria-busy="${busy}"${busy || connectionRefreshBlocked() ? " disabled" : ""}>
    ${icon("refresh", "small")}</button>`;
}
function scheduleConnectionRefresh(delay = CONNECTION_REFRESH_INTERVAL_MS) {
  if (connectionRefreshTimer) clearTimeout(connectionRefreshTimer);
  connectionRefreshTimer = setTimeout(async () => {
    connectionRefreshTimer = null;
    // Do not interrupt setup, an edit, or another ongoing read/write.
    if (!["home", "about"].includes(S.mode) || !isGoogleReady(S.google)
        || connectionRefreshActive() || connectionRefreshBlocked()) {
      scheduleConnectionRefresh(60000);
      return;
    }
    await refreshConnectionStatus();
  }, delay);
}
function refreshConnectionStatus() {
  if (connectionRefreshActive()) return connectionRefreshPending.promise;
  if (connectionRefreshBlocked()) return Promise.resolve();
  const pending = { context: googleListContext(), promise: null };
  connectionRefreshPending = pending;
  const current = () => connectionRefreshPending === pending && pending.context === googleListContext();
  pending.promise = (async () => {
    try {
      const status = await call("google_status");
      if (!current()) return;
      adoptGoogleStatus(status);
      if (!current() || !isGoogleReady(S.google)) return;
      await Promise.all([
        loadLinkLists(true, current),
        attendanceUiEnabled() ? refreshAttendanceStatus(false) : Promise.resolve(),
      ]);
      if (!current() || S.attendanceReadFailed || S.attendance?.state !== "ready" || !attendanceUiEnabled()) return;
      const target = chatReadContext();
      await Promise.all([
        loadFirstSetupStatus(true, true),
        loadAttendanceRosterStatus(true, true),
        loadChatStatus(true, false),
      ]);
      if (current() && target === chatReadContext() && isHomeroomTeacher()
          && S.chatStatus?.connected && !S.chatStatus.read_failed) await loadChatSpaces(true, false);
    } catch (error) {
      // Account verification failures already update the shared login state.
      if (current() && !(error instanceof StaleAccountResponse)) render();
    } finally {
      if (connectionRefreshPending === pending) {
        connectionRefreshPending = null;
        scheduleConnectionRefresh();
        render();
      }
    }
  })();
  render();
  return pending.promise;
}
bindActions({ "connection-refresh": () => refreshConnectionStatus() });
function homeHtml(behind) {
  const info = S.info;
  const visibleChecks = effectiveChecks().filter(
    (row) => attendanceUiEnabled() || row.tab !== "attendance"
  );
  const summary = checkSummary(visibleChecks);
  const problems = summary.bad;
  const pill = checksRetry.dirtyCards.size ? badge(checksRetry.failedAt ? "y" : "n", checksRetry.failedAt ? "변경 사항 확인 필요" : "변경 사항 점검 중…")
    : !S.checks.length ? badge("n", "점검 중…")
    : problems ? badge("y", `확인할 항목 ${problems}개`)
      + (summary.pending ? badge("n", `${summary.pending}개 확인 중…`) : "")
    : summary.pending ? badge("n", "연결 확인 중…") : badge("g", "모두 정상");
  const name = (S.profileCache && S.profileCache["선생님이름"]) ? `${S.profileCache["선생님이름"]} 선생님, ` : "";
  const tiles = CARDS.map((card) => {
    const st = cardStatus(card);
    const warn = st.kind === "y" ? " warn" : "";
    return `<button class="tile${warn}" data-action="open-card" data-card="${card.key}">
      <span class="icbox${warn}">${icon(card.icon)}</span>
      <span class="tx"><span class="tt">${esc(card.title)} ${cardBadges(card)}</span>
      <span class="ds">${esc(cardDetail(card))}</span></span></button>`;
  }).join("");
  const firstNotice = S.firstHomeNotice
    ? `<div class="first-banner"><span><b>모두 준비됐어요.</b> 이제 Brity 메신저에 메시지를 띄우고
        <b>${esc(prettyHotkey(S.draft.bridge.hotkey || DEFAULT_HOTKEY))}</b> 조합을 눌러 보세요 — 구글에 자동으로 정리돼요.</span>
      <button class="x" data-action="dismiss-first-notice" title="닫기">✕</button></div>`
    : "";
  const updateBanner = S.updateInfo && S.updateInfo.available
    && !hasCurrentFinalIssue(["start_update"])
    ? `<div class="update-banner"><span><b>새 버전(${esc(S.updateInfo.latest)})이 나왔어요.</b> ${esc(S.updateInfo.notes || "")}</span>
        <button class="btn" data-action="update-now" ${S.updating ? "disabled" : ""} data-busy-text="받는 중… (1~2분)">${S.updating ? "받는 중…" : "지금 업데이트"}</button></div>`
    : "";
  return `<div class="body"${behind ? " inert" : ""}><div class="body-inner"><div class="page">
    ${behind ? "" : bannerHtml()}
    ${updateBanner}
    ${firstNotice}
    <div class="hero"><span class="hi">${esc(name)}안녕하세요</span><span class="pill">${pill}</span></div>
    ${checksRetry.inflight && S.checks.length ? '<p class="sub" style="margin:-6px 0 10px">다시 확인하고 있어요</p>' : ""}
    ${liveCardHtml()}
    <div class="tiles">${tiles}</div>
    <div class="section-h" style="margin-top:22px">처리한 메시지</div>
    <p class="sub" style="margin:-2px 0 12px">메시지를 정리해서 캘린더와 할일에 등록한 내역이에요</p>
    ${capListHtml()}
    <div class="infobar"><b>${esc(info.branding.name)} v${esc(info.version)}</b> · ${esc(info.branding.credit)}
      <span class="links">
        <button data-action="open-about">버전 및 제작 정보</button>
      </span></div>
  </div></div></div>`;
}
function renderHome() {
  root().innerHTML = homeHtml(false) + toastHtml();
  if (shouldAutoRefreshChecks()) refreshChecks();
  if (!S.profileCache) call("read_profile").then(async (p) => {
    S.profileCache = p;
    if (S.mode === "home") await loadHomeClassRoom();
    render();
  }).catch(() => {});
  if (S.caps === null) {
    loadCapturePage(1);
  }
  startCapturePoll();
}

/* ---------- 카드 창 — 홈 위에 뜨는 창 (사용자 결정 2026-07-31, ㄱ안) ---------- */
function windowHtml(title, body, big) {
  const messages = S.mode === "edit" && S.edit === "settings"
    ? `<div id="settings-status-messages">${bannerHtml()}</div>` : bannerHtml();
  return `<div class="win-overlay">
    <div class="win-modal${big ? " big" : ""}" role="dialog" aria-label="${esc(title)}">
      <div class="win-head"><h1 class="win-title">${esc(title)}</h1>
        <span class="save-state" id="save-state"></span>
        ${S.mode === "edit" && S.edit === "connect" ? connectionRefreshButtonHtml() : ""}
        <button class="win-x" data-action="back-home" aria-label="닫기">✕</button></div>
      <div class="win-body"><div class="page">${messages}${body}</div></div>
    </div></div>`;
}
/* 창 닫기 — ✕·어두운 바깥 클릭·Esc·다른 카드로 넘어가기 전, 모두 이 하나를 탄다.
   저장을 마친 뒤에만 닫힌다. 저장이 실패하면 창은 남고 배너에 이유가 적힌다. */
async function closeWindow() {
  if (S.rosterEditor?.busy) return;
  if (S.mode === "edit" && S.edit === "timetable" && S.rosterEditor?.dirty && !(await saveRosterEditor(true))) return;
  if (S.mode !== "edit" && S.mode !== "about") return true;
  const request = beginIssueRequest(Boolean(settingsAutoSavePromise || editAutoSavePromise));
  clearAttendanceScriptDialogState();
  await stopHotkeyRecording();
  let saved = false;
  try { saved = await flushEditSave(); } catch (error) { handleCaughtError(error, request); return false; }
  if (!saved) return false;  // 배너에 이유가 적혀 있다
  stopChatConnectPoll();
  S.fieldIssues = {};
  clearProblemIssue();
  S.mode = "home"; S.edit = null; S.banner = null; S.hk = null; render();
  return true;
}
/* 저장 상태 한 줄 — 창 제목 줄에 잠깐 나타난다.
   render()를 부르지 않고 글자만 갈아 끼운다. 다시 그리면 입력 중이던 칸에서
   커서가 튕겨 나가고 쓰던 글자가 끊긴다. */
let saveStateTimer = null;
function showSaveState(text, fadeAfter) {
  const el = document.getElementById("save-state");
  if (!el) return;
  el.textContent = text;
  clearTimeout(saveStateTimer);
  if (fadeAfter) saveStateTimer = setTimeout(() => { el.textContent = ""; }, fadeAfter);
}
/* 설정 화면 자동 저장 — 저장 버튼 없이, 값을 바꾸는 즉시 저장하고 도우미에 적용한다.
   저장(도우미 재시작)이 도는 사이의 새 변경은 버리지 않고 끝난 뒤 한 번 더 저장한다. */
let settingsAutoSaveBusy = false;
let settingsAutoSavePending = false;
let settingsAutoSavePromise = null;
function isHelperStartFailure(error) {
  return error instanceof AppIssueError && error.issue?.operation === "helper_start";
}
async function autoSaveSettings(afterHotkey, request) {
  if (!(S.mode === "edit" && S.edit === "settings")) return; // 마법사는 마지막에 한꺼번에 적용
  syncMessengerDraft();
  if (settingsAutoSaveBusy) { settingsAutoSavePending = true; return await settingsAutoSavePromise; }
  const owner = request || beginIssueRequest(false);
  const sessionEpoch = accountUiEpoch;
  settingsAutoSaveBusy = true;
  const savePromise = (async () => {
    do {
      if (!ownsIssueRequest(owner) || sessionEpoch !== accountUiEpoch) return false;
      settingsAutoSavePending = false;
      if (S.helperRestartPending) {
        try {
          await call("restart_helper");
          if (!ownsIssueRequest(owner) || sessionEpoch !== accountUiEpoch) return false;
          S.helperRestartPending = false;
        } catch (error) {
        if (sessionEpoch !== accountUiEpoch || error instanceof StaleAccountResponse) return false;
          handleCaughtError(error, owner);
          return false;
        }
      }
      const dirtyFields = new Set(editDirtyFields);
      editDirtyFields.clear();
      syncMessengerDraft();
      const folderProblem = await validateAttachmentFolder();
      if (sessionEpoch !== accountUiEpoch) return false;
      if (folderProblem) {
        dirtyFields.forEach((name) => editDirtyFields.add(name));
        setBanner("warn", folderProblem);
        return;
      }
      const updates = {};
      if (dirtyFields.has("hotkey")) updates.hotkey = S.draft.bridge.hotkey || DEFAULT_HOTKEY;
      if (dirtyFields.has("autostart")) updates.autostart = S.draft.bridge.autostart !== false;
      if (dirtyFields.has("error_reports_enabled")) updates.error_reports_enabled = S.draft.bridge.error_reports_enabled !== false;
      if (dirtyFields.has("brity_download_dir")) {
        updates.brity_download_dir = S.draft.bridge.brity_download_dir || DEFAULT_ATTACHMENT_FOLDER;
      }
      if (!Object.keys(updates).length) continue;
      let result = null;
      try {
        showSaveState("저장 중…");
        result = await callWithLocalRecovery(() => call("save_messenger", updates));
      } catch (error) {
        if (sessionEpoch !== accountUiEpoch || error instanceof StaleAccountResponse) return false;
        if (isHelperStartFailure(error)) {
          // 파일과 자동 시작 선택은 이미 확인했다. 다음 화면 동작에서는 도우미만 다시 시작한다.
          S.helperRestartPending = true;
          handleCaughtError(error, owner);
          return false;
        }
        dirtyFields.forEach((name) => editDirtyFields.add(name));
        handleCaughtError(error, owner);
        return false;
      }
      if (!ownsIssueRequest(owner) || sessionEpoch !== accountUiEpoch) return false;
      if (!result.saved) {
        dirtyFields.forEach((name) => editDirtyFields.add(name));
        S.hk.status = { kind: "bad", text: result.reason };
        setBanner("warn", result.reason);
        render();
        return;
      }
      S.hk.current = result.hotkey;
      if (afterHotkey) S.hk.status = { kind: "ok", text: `${prettyHotkey(result.hotkey)} · 저장했어요` };
      invalidateCardChecks("settings");
      showToast("저장했어요 — 도우미가 새 설정으로 실행 중이에요");
      render();
      showSaveState("저장됨", 2500);
    } while (settingsAutoSavePending || editDirtyFields.size);
    completeIssueRequest(owner);
    return true;
  })();
  settingsAutoSavePromise = savePromise;
  try {
    return await savePromise;
  } finally {
    if (settingsAutoSavePromise === savePromise) settingsAutoSavePromise = null;
    settingsAutoSaveBusy = false;
  }
}

/* 내 정보·시간표·연결 자동 저장 — 저장 버튼 없이, 값을 바꾸면 잠깐 뒤 저장한다.
   마법사(S.mode === "wizard")는 마지막에 한꺼번에 적용하므로 여기서 건드리지 않는다. */
const AUTO_SAVE_SCREENS = ["identity", "timetable", "connect"];
const AUTO_SAVE_DELAY_MS = 700;  // 타자를 치는 중간중간 저장하지 않을 만큼만 기다린다
let editAutoSaveTimer = null;
let editAutoSaveBusy = false;
let editAutoSavePending = false;
let editAutoSavePromise = null;
// 글자·선택 입력은 같은 조작에서 input 뒤 change가 한 번 더 온다. input으로 이미
// 저장한 값을 창을 닫을 때 change가 다시 저장하지 않도록 같은 입력칸의 두 이벤트를 묶는다.
const editInputTargets = new WeakSet();
/* 사람이 실제로 손댄 적이 있는지. 화면을 열어 보기만 하고 나오는 것은 저장할 일이 아니다 —
   그때도 저장하면 홈 점검 결과를 버리게 되어, 되돌아갈 때마다 홈이 처음부터 다시 점검한다. */
let editDirtyFields = new Set();
const SCOPED_IDENTITY_FIELDS = new Set([
  "선생님이름", "학년도", "학교명", "학교급", "담임여부", "담임학년", "담임반",
  "출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간",
  "월요일마지막교시", "화요일마지막교시", "수요일마지막교시", "목요일마지막교시", "금요일마지막교시",
]);
const SCOPED_CALENDAR_FIELDS = new Set(CAL_LINK_FIELDS.flatMap(([id, name]) => [id, name]));
const SCOPED_TASK_FIELDS = new Set(TASK_LINK_FIELDS.flatMap(([id, name]) => [id, name]));
const SCOPED_GEMINI_FIELDS = new Set(["gemini_api_key", "gemini_model"]);
const CONNECT_FIELD_PAIRS = Object.fromEntries(
  [...CAL_LINK_FIELDS, ...TASK_LINK_FIELDS].flatMap(([id, name]) => [[id, name], [name, id]])
);
function markEditDirtyField(name) {
  if (!name) return;
  editDirtyFields.add(name);
  if (CONNECT_FIELD_PAIRS[name]) editDirtyFields.add(CONNECT_FIELD_PAIRS[name]);
}
function editedFieldName(box) {
  if (!box) return "";
  if (box.dataset && box.dataset.grid !== undefined) return "__timetable__";
  if (box.dataset && box.dataset.dayHour) return box.dataset.dayHour;
  if (box.dataset && box.dataset.dayMinute) return box.dataset.dayMinute;
  return box.name || "";
}
function dirtyValues(source, allowed, dirtyFields) {
  return Object.fromEntries([...dirtyFields]
    .filter((name) => allowed.has(name))
    .map((name) => [name, source[name]]));
}
function autoSaveScreen() {
  return S.mode === "edit" && AUTO_SAVE_SCREENS.includes(S.edit) ? S.edit : null;
}
function checkedEditSaveNotice(result) {
  const fail = message => { throw Object.assign(new Error(message), { editSaveResult: true }); };
  if (result?.parsed === false) {
    fail(result.detail || "이 컴퓨터에 입력을 저장했지만 설정을 적용하지 못했어요. 입력 내용을 확인해 주세요.");
  }
  const sync = result?.settings_sync;
  if (sync && !["applied", "not-linked", "waiting"].includes(sync.state)) {
    fail(sync.detail || "입력은 이 컴퓨터에 보관했지만 출석부 반영 결과를 확인하지 못했어요.");
  }
  const push = result?.sheet_push;
  if (push?.state === "failed") {
    fail(push.detail || "입력은 이 컴퓨터에 보관했지만 출석부 반영 결과를 확인하지 못했어요.");
  }
  if (sync && ["not-linked", "waiting"].includes(sync.state)) {
    return "이 컴퓨터에 저장됨 · " + (sync.detail || "출석부 준비 후 반영이 필요해요.");
  }
  if (push?.state === "skipped" && push.detail) return "이 컴퓨터에 저장됨 · " + push.detail;
  return "";
}
async function autoSaveEdit(options) {
  const key = autoSaveScreen();
  if (!key) return true;
  if (editAutoSavePromise) {
    if (editDirtyFields.size) editAutoSavePending = true;
    return await editAutoSavePromise;
  }
  if (!editDirtyFields.size) return true;  // 고친 게 없다
  // 여기서 미리 내린다 — 저장하는 동안 들어온 수정은 다시 올라가서 한 번 더 저장된다.
  // 저장이 끝난 뒤에 내리면 그 사이의 수정을 놓친다.
  editAutoSaveBusy = true;
  showSaveState("저장 중…");
  const request = options?.request || beginIssueRequest(false);
  const sessionEpoch = accountUiEpoch;
  const call = (name, ...args) => callForAccount(sessionEpoch, name, ...args);
  let savedNotice = "";
  const checkSaved = result => { savedNotice = checkedEditSaveNotice(result) || savedNotice; };
  const savePromise = (async () => {
    do {
      if (sessionEpoch !== accountUiEpoch) return false;
      editAutoSavePending = false;
      const dirtyFields = new Set(editDirtyFields);
      editDirtyFields.clear();
      try {
        syncEditFields(key);
        const identity = dirtyValues(S.draft.profile, SCOPED_IDENTITY_FIELDS, dirtyFields);
        const calendars = dirtyValues(S.draft.profile, SCOPED_CALENDAR_FIELDS, dirtyFields);
        const tasks = dirtyValues(S.draft.profile, SCOPED_TASK_FIELDS, dirtyFields);
        const googleSaveContext = googleContextVersion;
        const gemini = dirtyValues(S.draft.bridge, SCOPED_GEMINI_FIELDS, dirtyFields);
        if (Object.keys(identity).length) checkSaved(await callWithLocalRecovery(() => call("save_identity", identity)));
        if (dirtyFields.has("__timetable__")) {
          if (!(await ensureGridLoaded(request))) return false;
          checkSaved(await callWithLocalRecovery(() => call("save_timetable", S.draft.grid)));
        }
        if (Object.keys(calendars).length) {
          checkSaved(await callWithLocalRecovery(() => call("save_calendars", calendars)));
          if (googleSaveContext === googleContextVersion) acknowledgeSavedGoogleTargets(calendars);
        }
        if (Object.keys(tasks).length) {
          checkSaved(await callWithLocalRecovery(() => call("save_tasks", tasks)));
          if (googleSaveContext === googleContextVersion) acknowledgeSavedGoogleTargets(tasks);
        }
        if (Object.keys(gemini).length) {
          const saved = await callWithLocalRecovery(() => call("save_gemini", gemini));
          checkSaved(saved);
        }
        // Keep unrelated cards' confirmed results while rechecking saved changes.
        invalidateCardChecks(key);
        if (dirtyFields.has("담임여부")) invalidateCardChecks("connect");
        S.profileCache = null;
        if (googleSaveContext === googleContextVersion && (Object.keys(calendars).length || Object.keys(tasks).length)) {
          refreshGoogleTargetStatuses(() => ownsIssueRequest(request)).then(() => {
            if (ownsIssueRequest(request)) updateIssueDom();
          });
        }
      } catch (error) {
        if (sessionEpoch !== accountUiEpoch || error instanceof StaleAccountResponse) return false;
        // 실패한 묶음은 다음 저장에서 다시 시도한다. 저장 도중 들어온 새 변경은 이미
        // editDirtyFields에 있으므로 합치기만 하고 지우지 않는다.
        dirtyFields.forEach((name) => editDirtyFields.add(name));
        if (error.editSaveResult) setBanner("warn", error.message, `edit-save:${key}`);
        else handleCaughtError(error, request);
        showSaveState("저장·반영 확인 필요");
        return false;
      }
      if (!ownsIssueRequest(request)) return false;
    } while (editAutoSavePending || editDirtyFields.size);
    completeIssueRequest(request);
    if (S.banner?.topic === `edit-save:${key}`) setBanner("warn", "");
    showSaveState(savedNotice || "저장됨", savedNotice ? undefined : 2500);
    return true;
  })();
  editAutoSavePromise = savePromise;
  try {
    return await savePromise;
  } finally {
    if (editAutoSavePromise === savePromise) editAutoSavePromise = null;
    editAutoSaveBusy = false;
  }
}
function scheduleEditAutoSave() {
  if (!autoSaveScreen()) return;
  clearTimeout(editAutoSaveTimer);
  editAutoSaveTimer = setTimeout(() => {
    const request = beginIssueRequest(false);
    autoSaveEdit({ request }).catch((error) => handleCaughtError(error, request));
  }, AUTO_SAVE_DELAY_MS);
}
/* 창을 닫을 때 기다리던 저장을 지금 끝내고, 끝난 뒤에 홈으로 간다. */
/* 저장까지 마쳤으면 true. 저장이 실패했으면 false — 그때는 홈으로 보내지 않는다.
   이유가 적힌 배너를 못 보고 지나치면 무엇이 안 됐는지 알 길이 없다. */
async function flushEditSave() {
  clearTimeout(editAutoSaveTimer);
  if (S.mode !== "edit") return true;
  if (S.edit === "settings") {
    // 설정도 같은 규칙 — 열어 보기만 하고 나오면 저장(도우미 재시작)도, 홈 점검
    // 다시 돌기도 없어야 한다(사용자 확인 2026-07-31). 값을 바꾸면 그 자리에서
    // 이미 저장되므로, 여기서는 저장이 미처 못 따라온 경우만 마저 저장한다.
    if (settingsAutoSavePromise && !(await settingsAutoSavePromise)) return false;
    if (S.helperRestartPending || editDirtyFields.size) return await autoSaveSettings();
    return true;
  }
  if (!autoSaveScreen()) return true;
  const inFlightSave = editAutoSavePromise;
  if (inFlightSave) await inFlightSave;
  // 열어 보기만 한 창은 아무것도 만들지 않는다. 반면 저장 중 닫았다면 그 저장이
  // 끝난 뒤에도 아래의 연결 대상 만들기와 반환 ID 저장까지 계속해야 한다.
  if (!editDirtyFields.size && !inFlightSave) return true;
  if (S.edit === "connect") {
    // '새로 만들기'를 고른 캘린더·Tasks 목록은 여기서 실제로 만들어 ID를 채운다.
    // 타자를 칠 때마다 만들면 안 되므로 나갈 때 한 번만 한다. 로그인이 필요하거나
    // 최종 실패가 나면 현재 화면에서 안내하고, 해결 뒤 이 닫기 동작을 자동으로 잇는다.
    await provisionConnectTargets();
  }
  if (!editDirtyFields.size) return true;
  return await autoSaveEdit({ leaving: true });
}
for (const eventName of ["input", "change"]) {
  document.addEventListener(eventName, (event) => {
    const box = event.target;
    if (box?.dataset?.rosterCell) return;
    // 학급 단톡방 이름은 치는 즉시 상태로 보관한다 — 폴링 render가 지우지 않게 (검토 C3)
    if (box && box.name === "class-space-name") S.spaceDraftName = box.value;
    if (box && /^ai-/.test(String(box.name || ""))) {
      S.aiSelected = Array.from(document.querySelectorAll('.ai-row input:checked'))
        .map((input) => input.name.replace(/^ai-/, ""));
    }
    if (S.mode !== "edit") return;
    const changedField = editedFieldName(box);
    const duplicateChange = eventName === "change" && box && editInputTargets.has(box);
    if (eventName === "input" && box) editInputTargets.add(box);
    if (eventName === "change" && box) editInputTargets.delete(box);
    if (!duplicateChange) markEditDirtyField(changedField);
    if (S.edit === "settings") {
      if (eventName === "change"
          && (changedField === "autostart" || changedField === "brity_download_dir"
              || changedField === "error_reports_enabled")) {
        // 저장 예외(레지스트리 거부 등)가 무통지로 사라지면 화면과 실제 값이 어긋난다.
        const request = beginIssueRequest(settingsAutoSaveBusy);
        autoSaveSettings(false, request).catch((error) => handleCaughtError(error, request));
      }
      return;
    }
    if (!autoSaveScreen()) return;
    if (duplicateChange) return;
    scheduleEditAutoSave();
  });
}
function settingsEditBody() {
  // 도우미가 살아 있는지 보여주던 줄은 두지 않는다 — 죽었을 때는 홈 점검이
  // 설정 카드에 "꺼져 있음 — 단축키가 동작하지 않아요"를 띄우므로, 멀쩡할 때도
  // 늘 떠 있던 그 줄은 겹침이었다 (사용자 결정 2026-07-31).
  return `
    <h1>설정</h1>
    <p class="sub">단축키와 자동 실행을 관리해요. 바꾸면 바로 저장되고 도우미가 새 설정으로 다시 시작해요.</p>
    ${settingsSectionHtml(S.draft.bridge)}`;
}
// 업데이트 상태·버튼 — '버전 및 제작 정보' 화면의 한 줄에서 쓴다.
function updateControls() {
  if (hasCurrentFinalIssue(["get_update_info", "start_update"])) {
    return { st: `<span class="st warn">위 문제 안내를 확인해 주세요</span>`, btn: "" };
  }
  if (S.updating) {
    return { st: `<span class="st new">설치 파일을 받는 중… (1~2분)</span>`,
             btn: `<button class="btn" disabled>받는 중…</button>` };
  }
  if (S.updateInfo && S.updateInfo.available) {
    return { st: `<span class="st new">새 버전 ${esc(S.updateInfo.latest)}가 나왔어요</span>`,
             btn: `<button class="btn" data-action="update-now" data-busy-text="받는 중… (1~2분)">지금 업데이트</button>` };
  }
  if (S.updateCheck === "latest") {
    return { st: `<span class="st ok">지금이 최신 버전이에요</span>`,
             btn: `<button class="btn-quiet" data-action="update-check" data-busy-text="확인 중…">업데이트 확인</button>` };
  }
  if (S.updateCheck === "failed") {
    return { st: `<span class="st warn">업데이트 확인을 하지 못했어요</span>`,
             btn: `<button class="btn-quiet" data-action="update-check" data-busy-text="확인 중…">업데이트 다시 확인</button>` };
  }
  return { st: `<span class="st"></span>`,
           btn: `<button class="btn-quiet" data-action="update-check" data-busy-text="확인 중…">업데이트 확인</button>` };
}
// 창을 닫으면 기다리던 저장을 끝낸 뒤에 홈으로 간다(closeWindow → flushEditSave).
function renderEdit(key) {
  let body = "";
  if (key === "connect") body = stepConnect();
  else if (key === "identity") body = stepIdentity() + `<div class="section-h" style="margin-top:26px">하루 일과</div>` + stepDay().replace(/^[\s\S]*?<\/p>/, "");
  else if (key === "timetable") body = timetableEditBody();
  else if (key === "settings") body = settingsEditBody();
  // 화면 제목은 창 제목 줄이 맡는다 — 본문 맨 앞의 <h1>은 뗀다. 마법사는 이 길을
  // 지나지 않으므로 원래 <h1>을 그대로 쓴다.
  body = body.replace(/^\s*<h1>[\s\S]*?<\/h1>\s*/, "");
  const card = CARDS.find((c) => c.key === key);
  root().innerHTML = homeHtml(true)
    + windowHtml(card ? card.title : "", body, key === "connect" || key === "timetable")
    + toastHtml();
}
function syncEditFields(key) {
  if (key === "identity") { syncProfileFields(); syncDayFields(); }
  if (key === "timetable") syncGridFields();
  if (key === "connect") syncConnectFields();
  if (key === "settings") syncMessengerDraft();
}
async function loadForEdit(key, request) {
  const profile = await callWithLocalRecovery(() => call("read_profile"));
  if (!ownsIssueRequest(request)) return false;
  let grid = null;
  let settings = null;
  if (key === "timetable" || key === "identity") {
    grid = await callWithLocalRecovery(() => call("read_grid"));
    if (!ownsIssueRequest(request)) return false;
  }
  if (key === "connect" || key === "settings") {
    settings = await callWithLocalRecovery(() => call("get_messenger_settings"));
    if (!ownsIssueRequest(request)) return false;
  }
  S.profileCache = profile;
  // 저장본이 이긴다 — 저장 없이 홈으로 나가며 버린 편집이 재진입 화면에
  // 남거나 연결 화면 저장에 편승 커밋되면 안 된다. (bridge·grid와 같은 규칙)
  S.draft.profile = Object.assign({}, profile);
  if (key === "connect") {
    applyInvalidatedGoogleTargets();
  }
  if (grid !== null) S.draft.grid = grid;
  if (settings !== null) {
    S.draft.bridge = Object.assign({}, settings);
    S.hk = {
      current: settings.hotkey || DEFAULT_HOTKEY,
      recording: false,
      status: null,
    };
  }
  if (key === "connect" && attendanceUiEnabled()) {
    // Keep the last account-bound result, including an in-flight read, on reopen.
    // First entry without evidence still performs the initial verification.
    if (!S.attendance && !S.attendanceLoading && !S.attendanceReadFailed) await refreshAttendanceStatus(false);
    if (!ownsIssueRequest(request)) return false;
    clearAttendanceScriptDialogState();
    S.connectTab = S.attendance && ["connection-repair-required", "script-check-required", "script-update-required"].includes(S.attendance.state)
      ? "attendance"
      : "messenger";
  } else if (key === "connect") {
    S.attendance = null;
    S.attendanceScriptUpdate = null;
    clearAttendanceScriptDialogState();
    S.chatStatus = null;
    S.connectTab = "messenger";
  }
  // openCard가 화면 소유권을 먼저 settings로 바꾼 뒤 refreshSettingsStatus()를 시작한다.
  if (key === "settings") S.settingsRefreshOnOpen = true;
  return true;
}

/* ---------- 버전 및 제작 정보 ---------- */
const LATEST_RELEASE_URL = "https://github.com/rheps/teacher-manager/releases/latest";
function renderAbout() {
  const b = S.info.branding;
  const u = updateControls();
  const directDisabled = S.updating ? " disabled" : "";
  const manualInstallGuide = "자동 업데이트가 정상적으로 되지 않는 경우, 이 안내를 눌러 GitHub 릴리즈 페이지에서 최신 설치 파일을 직접 내려받은 뒤 실행해 주세요. 기존 프로그램을 삭제할 필요 없이 그대로 설치하면 업데이트됩니다.";
  root().innerHTML = homeHtml(true) + windowHtml("버전 및 제작 정보", `
    <h2 class="about-brand">${esc(b.name)}</h2>
    <p class="sub">${esc(b.tagline)}</p>
    <div class="panel">
      <div class="row"><span class="name">버전</span><span class="st">v${esc(S.info.version)}</span></div>
      <div class="row"><span class="name">만든 사람</span><span class="st">${esc(b.credit.replace("만든 사람: ", ""))}</span></div>
      <div class="row"><span class="name">게시자</span><span class="st">${esc(b.publisher)}</span></div>
      <div class="row"><span class="name">프로그램 업데이트</span>${u.st}${u.btn}</div>
      <div class="update-manual-note"><button type="button" class="update-manual-link" role="link" data-action="link-open" data-url="${LATEST_RELEASE_URL}"${directDisabled}>${esc(manualInstallGuide)}</button></div>
    </div>
    <div class="section-h">웹사이트</div>
    ${linkRow(b.website)}`, false) + toastHtml();
}

async function openCard(key) {
  // 창이 떠 있는 채로 다른 카드를 여는 길(홈 점검의 "설정 열기" 등)은 지금 창을
  // 같은 닫기 흐름(저장 마무리 포함)으로 닫고 나서 연다 — 한 번에 한 창.
  if (S.mode === "edit" || S.mode === "about") {
    if (!(await closeWindow())) return;
  }
  S.banner = null;
  S.firstHomeNotice = false;  // 카드에 다녀오면 처음 안내 띠는 접는다
  const request = beginIssueRequest(false);
  if (!(await loadForEdit(key, request))) return;
  // 연결 자료를 읽으며 출결 수리가 필요한 것을 확인하면 첫 탭을 출결로 바꾼다.
  // 이 변화는 사용자가 다른 화면으로 옮긴 것이 아니므로, 방금 끝난 읽기의 소유 화면도
  // 새 탭으로 맞춘 뒤 창을 연다. 각 await 직후의 검사는 늦은 응답을 이미 걸러 냈다.
  if (key === "connect") request.screen = screenKey();
  if (!ownsIssueRequest(request)) return;
  // 저장된 점검에서 이 카드의 문제를 실제 입력칸 오류로 옮겨 보여준다.
  S.fieldIssues = {};
  uniqueChecks(S.checks)
    .filter((c) => c.card === key && c.ok === false)
    .forEach((c) => { S.fieldIssues[c.target] = issue(c.key, c.target, c.fix || c.detail || c.label, c.tab); });
  if (!ownsIssueRequest(request)) return;
  S.mode = "edit"; S.edit = key; editDirtyFields.clear();
  // 설정 화면은 열 때마다 실제 상태를 다시 확인한다 — 홈 점검과 화면이 어긋나지 않게.
  // 화면을 먼저 지정해야 늦은 결과가 홈 요청으로 잘못 귀속되지 않는다.
  if (S.settingsRefreshOnOpen) {
    S.settingsRefreshOnOpen = false;
    refreshSettingsStatus().catch(() => {});
  }
  render();
}
bindActions({
  "field-eye": (el) => {
    // 가려진 값을 잠깐 보여준다. 다시 누르면 도로 가린다.
    const input = document.querySelector(`input[name="${el.dataset.field}"]`);
    if (!input) return;
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    el.title = showing ? "보기" : "숨기기";
    el.setAttribute("aria-label", showing ? "입력한 값 보기" : "입력한 값 가리기");
    el.innerHTML = icon(showing ? "eye" : "eye-off", "small");
  },
  "open-card": (el) => openCard(el.dataset.card),
  "back-home": () => closeWindow(),
  "open-about": () => { S.mode = "about"; render(); },
});

/* 어두운 바깥을 눌러 닫기 — mousedown이 가림막에서 시작했을 때만 닫는다.
   창 안에서 글자를 긁다 바깥에서 손을 떼는 것은 닫기가 아니다. */
let backdropPressed = false;
document.addEventListener("mousedown", (event) => {
  backdropPressed = !!(event.target.classList && event.target.classList.contains("win-overlay"));
});
document.addEventListener("click", (event) => {
  if (!backdropPressed) return;
  backdropPressed = false;
  if (event.target.classList && event.target.classList.contains("win-overlay")) closeWindow();
});
/* Esc로 닫기 — 단축키를 새로 누르는 중이면 위쪽의 잡기 전용 리스너가 녹음 취소만 하고
   preventDefault를 걸어 두므로(defaultPrevented), 그때는 창을 닫지 않는다. */
document.addEventListener("keydown", (event) => {
  if (event.key === "Tab" && S.attendanceScriptDialog) {
    const dialog = document.querySelector(".attendance-update-dialog");
    const buttons = dialog ? Array.from(dialog.querySelectorAll("button:not([disabled])")) : [];
    if (!buttons.length) { event.preventDefault(); return; }
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (!dialog.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
    return;
  }
  if (event.key !== "Escape" || event.defaultPrevented) return;
  if (hotkeyCapture.active) return;
  if (S.workspaceGuideOpen) { S.workspaceGuideOpen = false; render(); return; }
  if (S.attendanceScriptDialog) {
    // 취소 단추와 같은 규칙 — 실행 중에는 Esc로도 빠져나가지 못한다.
    if (S.attendanceScriptUpdating) return;
    clearAttendanceScriptDialogState();
    render();
    focusAttendanceScriptResolve();
    return;
  }
  if (S.mode !== "edit" && S.mode !== "about") return;
  closeWindow();
});

/* ---------- 라우터 ---------- */
function screenKey() { return `${S.mode}|${S.step}|${S.edit}|${S.connectTab}`; }
const resumeHandlers = {
  "google-login": null,
  "chat-permission": null,
  "chat-space-list": null,
  "attendance-first-setup": null,
  "attachment-download": null,
};
let googleLoginResumeContext = null;
let googleLoginResumeGeneration = 0;
let resumeRequestToken = 0;
function registerResumeHandler(key, handler) {
  if (!Object.prototype.hasOwnProperty.call(resumeHandlers, key)) return false;
  resumeHandlers[key] = typeof handler === "function" ? handler : null;
  return true;
}
async function openGoogleLoginSettings() {
  const request = beginIssueRequest(false);
  if (!(await loadForEdit("settings", request)) || !ownsIssueRequest(request)) return false;
  S.fieldIssues = {};
  S.mode = "edit";
  S.edit = "settings";
  editDirtyFields.clear();
  render();
  refreshSettingsStatus().catch(() => {});
  return true;
}
function copyGoogleResumeValue(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}
function captureGoogleResumeContext() {
  if (S.mode === "edit" && S.edit === "settings") return null;
  if (S.mode !== "edit" && S.mode !== "wizard") return null;
  return {
    generation: googleLoginResumeGeneration,
    originScreen: screenKey(),
    interruptedAction: problemIssueResumeAction,
    mode: S.mode,
    step: S.step,
    edit: S.edit,
    connectTab: S.connectTab,
    draft: copyGoogleResumeValue(S.draft),
    fieldIssues: copyGoogleResumeValue(S.fieldIssues),
  };
}
async function replayInterruptedGoogleAction(context) {
  const actionKey = String(context?.interruptedAction || "");
  if (!actionKey || screenKey() !== context.originScreen) return false;
  const request = beginIssueRequest(false, actionKey);
  try {
    if (actionKey === "go-next") await goNextAsync();
    else if (actionKey === "back-home") await closeWindow();
    else return false;
  } catch (error) {
    handleCaughtError(error, request);
  }
  return true;
}
async function resumeInterruptedGoogleScreen(request) {
  const context = googleLoginResumeContext;
  googleLoginResumeContext = null;
  const onGoogleLoginScreen = (S.mode === "edit" && S.edit === "settings")
    || (S.mode === "wizard" && S.step === 2);
  if (!context
      || context.generation !== googleLoginResumeGeneration
      || !ownsIssueRequest(request)
      || !onGoogleLoginScreen) return false;
  S.mode = context.mode;
  S.step = context.step;
  S.edit = context.edit;
  S.connectTab = context.connectTab;
  S.draft = context.draft;
  applyInvalidatedGoogleTargets();
  S.fieldIssues = context.fieldIssues || {};
  S.banner = null;
  clearProblemIssue();
  render();
  await replayInterruptedGoogleAction(context);
  return true;
}
registerResumeHandler("google-login", async () => {
  googleLoginResumeGeneration += 1;
  googleLoginResumeContext = captureGoogleResumeContext();
  await openGoogleLoginSettings();
});
registerResumeHandler("chat-space-list", async ({ ownsRequest }) => {
  const context = chatReadContext();
  // Reuse the same loading/error ownership as manual and external-return reads.
  S.chatSpaces = undefined;
  await loadChatStatus(true);
  if (!ownsRequest() || context !== chatReadContext()) return;
  if (!S.chatStatus?.connected || S.chatStatus.read_failed) return;
  await loadChatSpaces(true);
  if (!ownsRequest() || context !== chatReadContext()) return;
  if (S.chatSpacesError) return;
  S.chatSpaceName = String(S.chatStatus.class_space_name || "");
  S.spaceCreate = null;
  clearProblemIssue();
  render();
});
function invalidateIssueResume() { resumeRequestToken += 1; }
async function dispatchIssueResume(issue, actionKey) {
  const resume = String(issue?.resume || "");
  if (!resume || actionKey !== resume) return;
  const handler = resumeHandlers[resume];
  if (typeof handler !== "function") return;
  const requestToken = ++resumeRequestToken;
  const requestScreen = screenKey();
  const ownsRequest = () => requestToken === resumeRequestToken && requestScreen === screenKey();
  await handler({ issue, ownsRequest });
}
let lastScreenKey = "";
function render() {
  if (attendanceActionFlow && attendanceActionFlow.owner !== screenKey()) attendanceActionFlow.active = false;
  clearResolvedAttendanceGateBanner();
  if (attendanceBoundaryCheck && (!attendanceViewVisible() || attendanceBoundaryCheck.context !== chatReadContext())) stopAttendanceBoundaryCheck();
  if (attendancePermissionReturn && !ownsAttendancePermissionReturn(attendancePermissionReturn)) stopAttendancePermissionReturn();
  if (rosterContext && rosterContext !== chatReadContext()) {
    rosterReadVersion += 1;
    rosterInFlight = "";
    rosterContext = "";
    rosterStatus = null;
  }
  if (S.problemIssue && problemIssueOwner !== screenKey()) clearProblemIssue();
  const onLoginScreen = (S.mode === "wizard" && S.step === 2) || (S.mode === "edit" && S.edit === "settings");
  if (S.login && !S.login.logging_out && !onLoginScreen) {
    googleLoginEpoch += 1;
    stopLoginPoll();
    S.login = null;
    googleLoginResumeGeneration += 1;
    googleLoginResumeContext = null;
    call("gws_login_cancel").catch(() => {});
  }
  if (S.mode !== "home") stopCapturePoll();
  document.body.classList.toggle("win-open", S.mode === "edit" || S.mode === "about");
  if (S.mode === "loading") { root().innerHTML = '<div class="boot">여는 중이에요…</div>'; return; }
  // 같은 화면을 다시 그릴 때는 스크롤을 유지한다 — 세그먼트·선택 조작으로 위로 튀지 않게.
  // 마법사(.shell)는 .body가, 홈·편집 화면은 문서 전체가 스크롤되므로 둘 다 기억한다.
  const prevBody = document.querySelector(".body");
  const prevScroll = prevBody ? prevBody.scrollTop : 0;
  const prevDocScroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
  const prevWin = document.querySelector(".win-body");
  const prevWinScroll = prevWin ? prevWin.scrollTop : 0;
  const sameScreen = lastScreenKey === screenKey();
  if (!sameScreen) invalidateIssueResume();
  if (S.mode === "wizard") renderWizard();
  else if (S.mode === "edit") renderEdit(S.edit);
  else if (S.mode === "about") renderAbout();
  else renderHome();
  lastScreenKey = screenKey();
  if (sameScreen) {
    if (prevScroll) {
      const nextBody = document.querySelector(".body");
      if (nextBody) nextBody.scrollTop = prevScroll;
    }
    if (prevDocScroll && document.scrollingElement) document.scrollingElement.scrollTop = prevDocScroll;
    if (prevWinScroll) {
      const nextWin = document.querySelector(".win-body");
      if (nextWin) nextWin.scrollTop = prevWinScroll;
    }
  }
  if (S.focusTarget) {
    const el = document.querySelector(`[name="${S.focusTarget}"]`);
    S.focusTarget = "";
    if (el && el.focus) el.focus();
  }
  // 실행 중에는 두 단추 모두 잠겨 있어 초점을 옮길 곳이 없다 — 취소로 포커스를
  // 보내면(기존 버그) 실행 중에도 Enter 한 번으로 빠져나갈 수 있었다.
  if (S.attendanceScriptDialog && !S.attendanceScriptUpdating) focusAttendanceScriptDialog();
}

/* ---------- 부팅 ---------- */
// 프로그램을 켤 때 한 번만 묻는다. 쓰는 중에는 창을 띄우지 않는다 — 일이 끊긴다.
// 마법사를 도는 중에는 아예 확인하러 나가지도 않는다 — 그 사이 "다음"을 누르기 전
// 입력은 아직 저장 전이라, 확인창에서 "확인"을 누르면 적던 내용이 그대로 사라지고
// 설치 파일이 창을 강제로 닫아 마법사가 설명 없이 꺼진 것처럼 보인다. 마법사를
// 마치고 홈에 온 뒤, 다음에 켤 때 물으면 된다.
async function askUpdateOnStart() {
  if (S.mode === "wizard") return;
  const request = beginIssueRequest(true, "update_offer");
  let offer = null;
  try { offer = await call("update_offer"); } catch (_) { return; }
  if (!ownsIssueRequest(request)) return;
  // '버전 및 제작 정보' 배너·상태도 이 응답 하나로 채운다 — get_update_info를 따로
  // 부르면 부팅할 때마다 같은 배포 정보를 인터넷에서 두 번 받아 오게 된다.
  if (offer && offer.available) S.updateInfo = offer;
  if (offer && offer.status && offer.status !== "failed") S.updateCheck = offer.status;
  render();
  if (!offer || !offer.ask) return;
  const lines = (offer.notes || "").split("\n").filter(Boolean).slice(0, 3);
  const body = `새 버전 ${offer.latest}이 나왔습니다.\n지금 설치할까요?`
    + (lines.length ? "\n\n" + lines.map(l => "· " + l).join("\n") : "");
  if (window.confirm(body)) {
    // 설정의 '지금 업데이트'(update-now)와 같은 마무리여야 한다 — 설치가 시작되면
    // 프로그램이 스스로 닫혀야 파일 잠금 때문에 설치가 되돌려지지 않는다. 되돌려져도
    // /SUPPRESSMSGBOXES라 아무 말 없이 끝나서 선생님은 이유를 알 수 없다.
    const startRequest = beginIssueRequest(false, "start_update");
    S.updating = true; render();
    try {
      const result = await call("start_update", offer.url, offer.latest, offer.sha256);
      if (!result.started) {
        throw new Error(result.reason || "업데이트를 시작하지 못했어요.");
      }
      setTimeout(() => { call("quit_app").catch(() => {}); }, 300);
      if (ownsIssueRequest(startRequest)) showToast("설치 파일을 확인했어요. 설치 창을 열게요.");
    } catch (error) {
      S.updating = false;
      if (ownsIssueRequest(startRequest)) {
        render();
        handleCaughtError(error, startRequest);
      }
    }
    return;
  }
  if (!ownsIssueRequest(request)) return;
  try {
    await call("decline_update", offer.latest);
  } catch (error) {
    // 오늘 그만 묻겠다는 기록을 못 남겨도 쓰던 일은 계속돼야 한다. 다음에 켤 때 다시 묻는다.
  }
}

async function boot() {
  try {
    // Verify identity before showing saved teacher data on app startup.
    adoptGoogleStatus(await call("google_status"));
    const info = await call("get_app_info");
    adoptAppInfo(info);
    render();
    watchNetworkStatus();
    askUpdateOnStart();
    startLoginWatch();
    scheduleConnectionRefresh();
  } catch (error) {
    root().innerHTML = `<div class="boot">${esc(error.message)}</div>`;
  }
}
function adoptAppInfo(info) {
  S.info = info;
  S.mode = info.mode;
  S.edit = null;
  S.step = Math.min(info.step || 1, WIZARD_STEPS.length);
  S.maxStep = Math.min(Math.max(info.max_step || S.step, S.step), WIZARD_STEPS.length);
  S.draft = Object.assign({ profile: {}, grid: null, bridge: {} }, info.draft || {});
}
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);

"use strict";
/* Teacher Manager 화면. 규칙: 화면은 bridge만 부른다. 업무 판단은 전부 파이썬에 있다. */

/* ---------- 상태 ---------- */
const S = {
  network: null,  // 배경 이름 확인(예열) 상태 — 느린 컴퓨터에서 기다려 달라는 안내에 쓴다
  mode: "loading",           // loading | wizard | home | edit | about | attendance
  step: 1,
  draft: { profile: {}, grid: null, bridge: {} },
  info: null,                // get_app_info data
  checks: [],
  google: null,
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
  maxStep: 1,                 // 마법사에서 한 번이라도 도달한 가장 먼 단계
  login: null,
  loginCode: "",             // 코드 입력 칸의 글자 — 다시 그려도 지우지 않는다
  loginUnreachable: false,   // 로그인을 시작할 때 서버가 답하지 않았다 (회색 + 다시 시도)
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
  classYear: null,           // 서버의 학년도 읽기 하나 — 홈 타일, 출결 탭, 출결·메신저 창이 함께 쓴다
  classTaskSaving: false,    // Google Tasks 목록을 연결하는 중
  workspaceGuideOpen: false,   // 1단계 "화면에서 찾기"로 뜨는 구글 워크스페이스 위치 캡처
  chatStatus: null,          // attendance_chat_status 응답 (null=미조회, "loading"=질의 중)
  spaceDraftName: undefined,  // 방 이름칸에 쓴 값 (undefined면 내 정보로 만든 기본값을 쓴다)
  spaceCreate: null,          // null | "ok" | "blocked" | 실패 사유 문자열
  classGroupCreating: false,  // [환영 인사 보내면서 Google Chat 대화 상대로 연결하기]가 진행 중인지 (두 번 누름 방지)
  classChatStart: null,       // [환영 인사 보내면서 Google Chat 대화 상대로 연결하기] 창 {kind: edit|sending|result}
  chatSpacesError: false,     // 방 목록을 못 읽었는지 — "방이 없다"와 갈라 놓는다
  helperRestartPending: false, // 설정은 확인했지만 도우미 시작만 다시 확인할 상태
};

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

// Structural diagnostics only. Recording must never interrupt the screen or create another rejection.
function logAttendanceUiDiagnostic(event) {
  try {
    const api = window.pywebview?.api;
    if (typeof api?.log_attendance_ui_event !== "function") return;
    const result = api.log_attendance_ui_event(event);
    if (result && typeof result.catch === "function") result.catch(() => {});
  } catch (_) { /* Diagnostics are best effort and contain no user text. */ }
}
window.addEventListener("error", event => {
  try {
    logAttendanceUiDiagnostic({ event: "page_exception", stage: "error",
      errorType: event.error?.name || "Unknown", script: String(event.filename || "").split(/[\\/]/).pop().split(/[?#]/)[0],
      line: event.lineno, column: event.colno });
  } catch (_) { /* Do not turn diagnostic extraction into a page error. */ }
});
window.addEventListener("unhandledrejection", event => {
  try {
    logAttendanceUiDiagnostic({ event: "page_exception", stage: "unhandledrejection",
      errorType: event.reason instanceof Error ? event.reason.name : "Unknown" });
  } catch (_) { /* Never inspect a rejection's text or stack. */ }
});

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
const CONTINUABLE_READS = new Set([
  ...ATTENDANCE_READ_METHODS, "google_status",
  "list_calendars", "list_tasklists", "google_target_statuses",
]);
function incompleteGoogleObservation(status) {
  return googleAuthCheckFailed(status) && !status.local_settings_error
    && !["logged_out", "account_invalid"].includes(status.login_state)
    && !["login_required", "reauth_required", "account_required"].includes(status.authorization_state);
}
function pendingReadResponse(name, response) {
  if (!CONTINUABLE_READS.has(name)) return false;
  if (response?.ok !== true) return response?.read_pending === true;
  if (name === "google_status") return incompleteGoogleObservation(response.data);
  if (name === "gws_login_status") return response.data?.verification_pending === true
    && incompleteGoogleObservation(response.data.google_status);
  return response.data?.read_pending === true;
}
const attendanceReads = new Map();
const pendingGoogleReads = new Set();
let bootIdentityPending = false;
// LOGIN-32: grey wait after a failed boot Google check, before showing step 2.
const BOOT_GOOGLE_RECHECK_DELAYS_MS = [10000, 30000];
const BOOT_GOOGLE_RECHECK_MESSAGE = "Google 계정 상태를 아직 확인하지 못했어요. 잠시 뒤 자동으로 다시 확인해요. 로그인이 해제된 것으로 판단하지 않았습니다.";
let bootGoogleRecheckWaiting = false;
function googleObservationPending() {
  return [...pendingGoogleReads].some(read => read.epoch === accountUiEpoch && read.loginEpoch === googleLoginEpoch);
}
const ATTENDANCE_TIMED_READS = new Set(["attendance_roster_status", "attendance_chat_spaces"]);
function callBridge(name, ...args) {
  const attempts = name === "google_status" && S.mode === "loading" ? 1 : 3;
  const epoch = accountUiEpoch;
  const loginEpoch = googleLoginEpoch;
  const currentReadContext = () => ["attendance_status", "attendance_status_cached"].includes(name)
    ? googleListContext() : chatReadContext();
  const readContext = ATTENDANCE_READ_METHODS.has(name) ? currentReadContext() : null;
  const api = window.pywebview.api;
  const expectedToken = accountWireToken ? [...accountWireToken] : null;
  const requestArgs = JSON.parse(JSON.stringify(args));
  const stillCurrent = () => {
    if (epoch !== accountUiEpoch || loginEpoch !== googleLoginEpoch) throw new StaleAccountResponse();
    if (readContext !== null && readContext !== currentReadContext()) throw new StaleAccountResponse();
  };
  const send = async () => {
    for (let attempt = 0; attempt < attempts; attempt++) {
      stillCurrent();
      const pending = typeof api.account_call === "function"
        ? api.account_call(name, requestArgs, expectedToken) : api[name](...requestArgs);
      let timer, response;
      try {
        response = await (ATTENDANCE_TIMED_READS.has(name) ? Promise.race([pending,
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error("목록 응답을 기다리는 시간이 지났어요.")), 120000);
          }),
        ]) : pending);
      } finally { if (timer) clearTimeout(timer); }
      stillCurrent();
      if (!pendingReadResponse(name, response) || attempt === attempts - 1) return response;
      if (expectedToken && Array.isArray(response.session)
          && JSON.stringify(expectedToken) !== JSON.stringify(response.session)) throw new StaleAccountResponse();
      await new Promise(resolve => setTimeout(resolve, attempt === 0 ? 2000 : 5000));
    }
  };
  const observation = name === "google_status" ? {epoch, loginEpoch} : null;
  if (observation) {
    pendingGoogleReads.add(observation);
    Promise.resolve().then(() => { if (S.info && pendingGoogleReads.has(observation)) paintGoogleObservation(); });
  }
  const result = send().then((res) => {
    if (["google_status", "gws_login_status", "gws_login_start"].includes(name)
        && loginEpoch !== googleLoginEpoch) throw new StaleAccountResponse();
    if (epoch !== accountUiEpoch) throw new StaleAccountResponse("계정이 바뀌어 이전 화면의 작업을 멈췄어요.");
    if (readContext !== null && readContext !== currentReadContext()) throw new StaleAccountResponse();
    if (!res || res.ok !== true) {
      if (res?.issue?.code === "SIGNIN_REQUIRED") checkGoogleOnReturn().catch(reportBackgroundFailure);
      throw new AppIssueError(res?.issue);
    }
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
  return observation ? result.finally(() => pendingGoogleReads.delete(observation)) : result;
}

function callForAccount(epoch, name, ...args) {
  if (epoch !== accountUiEpoch) throw new StaleAccountResponse();
  return call(name, ...args);
}

function clearAccountScreen() {
  window.TMAttendance?.forget();
  identityPendingWrite = null;
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
  S.helperRestartPending = false;
  S.maps = { calendars: {}, tasklists: {} };
  S.classYear = null;
  S.devices = null;
  yearSetupRun = null;
  // The window closes through this handling (SCREENS, `Account changed`).
  leaveAttendanceWindow();
  attendanceSessionEnded = false;
}

function reloadAccountProfile() {
  if (!accountProfileNeedsReload) return Promise.resolve(false);
  if (accountProfileReloadPromise) return accountProfileReloadPromise;
  const epoch = accountUiEpoch;
  accountProfileReloadPromise = (async () => {
    const info = await call("get_app_info");
    if (epoch !== accountUiEpoch) return false;
    adoptAppInfo(info);
    // ACCT-01: the reread folder belongs to the account now signed in (its own finished
    // folder or a new empty one), so its saved Calendar/Tasks choices are not the previous account's.
    S.invalidatedGoogleTargetFields = new Set();
    accountProfileNeedsReload = false;
    render();
    return true;
  })().finally(() => { accountProfileReloadPromise = null; });
  return accountProfileReloadPromise;
}
// The one door to the server's data routes: {status, body, unknown} (server_session.py).
// A 401 means this PC's session ended, so the status is read once and the screen follows it.
// The open attendance window answers it with its own band and keeps its last data (G1), so
// there the status waits until the window closes or the teacher presses 다시 로그인.
async function dataCall(route, body) {
  const reply = await call("data_call", route, body);
  if (reply?.status === 401) {
    if (S.mode === "attendance") attendanceSessionEnded = true;
    else checkGoogleOnReturn().catch(reportBackgroundFailure);
  }
  return reply;
}
function newRequestId() { return crypto.randomUUID(); }
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
function icon(name, cls) {
  return `<svg class="ic ${cls || ""}"><use href="#ic-${name}"/></svg>`;
}
function badge(kind, label) {
  const mark = { g: "✓", y: "·", r: "✕", n: "·" }[kind] || "·";
  return `<span class="badge ${kind}">${mark} ${esc(label)}</span>`;
}
function root() { return document.getElementById("app"); }

function setBanner(kind, text, topic = "") { S.banner = text ? { kind, text, topic } : null; render(); }
const SCREEN_PAINT_FAILED = "화면을 새로 표시하지 못했어요. 프로그램을 다시 열어 주세요.";
// Background reads and repaints never fail silently (OBS-09). A bridge failure is already
// shown by call() or its caller; a script error while adopting or painting a reply would
// otherwise leave a pending frame on screen, so it ends in the plain error banner.
function reportBackgroundFailure(error) {
  console.error(error);
  if (!(error instanceof TypeError || error instanceof ReferenceError || error instanceof RangeError)) return;
  try { setBanner("error", SCREEN_PAINT_FAILED); } catch (paintError) { console.error(paintError); paintFallbackNotice(); }
}
// Last resort when render() itself keeps throwing: replace the frozen frame with a
// fixed plain notice so the screen never stays on 확인 중 silently (PEND-02).
function paintFallbackNotice() {
  try {
    const app = root();
    if (!app) return;
    app.innerHTML = `<div class="boot" role="alert"><p>${esc(SCREEN_PAINT_FAILED)}</p>
      <button class="btn" type="button" id="screen-paint-reload">다시 열기</button></div>`;
    app.querySelector("#screen-paint-reload")?.addEventListener("click", () => window.location.reload());
  } catch (fallbackError) { console.error(fallbackError); }
}
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
  "reauthorize-google": "권한 다시 승인",
  "chat-permission": "Google Chat 권한 확인",
  "chat-space-list": "학급 단체톡방 다시 확인",
  "attachment-download": "첨부파일 상태 다시 확인",
  "inspect-calendar-candidates": "Google Calendar에서 직접 확인",
  "inspect-tasklist-candidates": "Google Tasks에서 직접 확인",
  "open-download-page": "다운로드 페이지 열기",
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
// The panel is drawn before the background send ends, so the reply can only say
// "queued". The footer then follows the real send result (UPD-01 footer fix).
let problemReportTimer = null;
function followProblemReport(issue) {
  clearTimeout(problemReportTimer);
  const id = String(issue?.diagnostic_id || "");
  if (!id || issue.report_queued !== true) return;
  let tries = 0;
  const tick = async () => {
    if (S.problemIssue !== issue) return;
    let state = "";
    try { state = String((await call("error_report_status", id))?.state || ""); } catch (_) { return; }
    if (S.problemIssue !== issue) return;
    if (state && state !== "unknown") {
      issue.report_state = state;
      // Only the footer text changes; the panel's buttons stay attached (LOCK-02).
      const footer = document.querySelector(".problem-panel .problem-footer");
      const next = document.createElement("div");
      next.innerHTML = problemPanelHtml(issue);
      const text = next.querySelector(".problem-footer")?.textContent || "";
      if (footer && text) footer.textContent = text;
    }
    if (state === "sent" || state === "retry" || ++tries >= 15) return;
    problemReportTimer = setTimeout(tick, 2000);
  };
  problemReportTimer = setTimeout(tick, 1500);
}
function problemPanelHtml(issue) {
  if (!issue || !["needs_user", "failed"].includes(issue.state)) return "";
  const readUncertain = ATTENDANCE_READ_METHODS.has(issue.operation);
  // 교사가 지금 할 수 있는 일만 보여 준다. 진단용 값(코드·횟수·판·식별번호)은
  // 로컬 기록과 개발자 보고에만 남는다 (2026-09-02 사용자 결정).
  const steps = (Array.isArray(issue.steps) ? issue.steps : []).map((s) => String(s || "")).filter(Boolean);
  const reason = issue.state === "needs_user"
    ? issueValue(issue, "message", "")
    : issueValue(issue, "reason", "");
  const stepsHtml = steps.length
    ? `<div class="problem-steps"><b>지금 할 수 있는 일</b><ol>${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>`
    : "";
  const reportState = issue.reported === true ? "sent"
    : String(issue.report_state || (issue.report_queued === true ? "queued" : ""));
  const reportStatus = {
    sent: "오류 내용을 개발자에게 보냈어요.",
    sending: "오류 내용을 개발자에게 보내고 있어요.",
    retry: "오류 내용을 이 컴퓨터에 저장했어요. 지금은 개발자에게 보내지 못해 다음에 프로그램을 켤 때 다시 보냅니다.",
    queued: "오류 내용을 이 컴퓨터에 저장했어요. 개발자에게 전달됐는지는 아직 확인하지 못했습니다.",
  }[reportState] || "";
  return `<section class="problem-panel${readUncertain ? " read-uncertain" : ""}" role="alert">
    <h2>${esc(readUncertain ? "현재 상태를 확인하지 못했어요." : issueValue(issue, "title", "작업을 마치지 못했어요."))}</h2>
    <p class="problem-reason">${esc(reason || "다시 확인해 주세요.")}</p>
    ${stepsHtml}
    <p class="problem-status">${esc(issueValue(issue, "change_status", "작업 결과를 확인하지 못했어요. 같은 작업을 다시 하기 전에 현재 자료를 확인해 주세요."))}</p>
    <div class="problem-actions action-line">${directIssueActionHtml(issue)}</div>
    ${reportStatus ? `<p class="problem-footer">${esc(reportStatus)}</p>` : ""}
  </section>`;
}
function showProblemIssue(error, request, paint = render) {
  if (!(error instanceof AppIssueError)) return false;
  if (!ownsIssueRequest(request)) return true;
  const issue = error.issue;
  if (!issue || !["needs_user", "failed"].includes(issue.state)) return false;
  S.problemIssue = issue;
  problemIssueOwner = screenKey();
  problemIssueResumeAction = String(request?.action || "");
  problemIssueToken += 1;
  S.banner = null;
  paint();
  followProblemReport(issue);
  return true;
}
function handleCaughtError(error, request) {
  if (error instanceof StaleAccountResponse) return true;
  if (showProblemIssue(error, request)) return true;
  if (!request || ownsIssueRequest(request)) setBanner("error", error?.message || "작업을 마치지 못했어요.");
  return false;
}
function bannerHtml() {
  const localSettings = S.google?.local_settings_error
    ? `<div class="banner error"><span><b>이 컴퓨터의 설정을 확인해 주세요.</b> ${esc(S.google.local_settings_error)}</span>${inSettingsWindow() ? "" : settingsRecheckButtonHtml()}</div>`
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
  "gemini_api_key": "Gemini 연결 API 키가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
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
    return connectIssues().find((row) => row.target === name && !row.pending && !row.unread)?.message || "";
  }
  const row = (S.fieldIssues || {})[name];
  return row && !row.pending ? row.message : "";
}
function fieldNoteHtml(name) {
  const error = fieldError(name);
  if (error) return `<span class="field-error">${esc(error)}</span>`;
  const pending = editingCard() === "connect" && LIVE_CONNECT_TARGETS.has(name)
    ? connectIssues().find((row) => row.target === name && (row.pending || row.unread)) : null;
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
    if (key === "settings") { await actions["goto-settings"](); return; }
    if (key === "reauthorize-google") { await actions["reauthorize-google"](); return; }
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
const LOGIN_WATCH_INTERVAL_MS = 30 * 60 * 1000;  // 로그인 해제를 알리는 주기. 토큰 유지와 무관하며 설정 열기·창 복귀·작업 시작 때 따로 확인한다
let loginWatchTimer = null;
let loginWatchChecking = false;
let googleLoginEpoch = 0;
let googleContextVersion = 0;
let linkListsReadVersion = 0;
let targetStatusReadVersion = 0;
let googleRecoveryNeeded = false;
let googleRecoveryTimer = null;
function scheduleGoogleRecovery(delay = 0) {
  if (!googleRecoveryNeeded) return;
  if (googleRecoveryTimer) clearTimeout(googleRecoveryTimer);
  googleRecoveryTimer = setTimeout(async () => {
    googleRecoveryTimer = null;
    if (!googleRecoveryNeeded || !isGoogleReady(S.google)) return;
    if (connectionRefreshActive() || connectionRefreshBlocked()) {
      scheduleGoogleRecovery(60000);
      return;
    }
    await refreshConnectionStatus({ googleStatus: S.google });
  }, delay);
}
function verifiedGoogleAccount(status) {
  return status?.logged_in && !googleAuthCheckFailed(status)
    ? String(status.user || "").trim().toLowerCase() : "";
}
function googleReadContext() {
  return JSON.stringify([screenKey(), googleContextVersion, googleLoginEpoch, verifiedGoogleAccount(S.google)]);
}
let yearReloadEpoch = -1;
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
    // A failed check erases nothing. Its account stays, and the next good check reads again.
    googleRecoveryNeeded = Boolean(previousAccount || status?.server_unreachable);
    if (S.classYear) S.classYear = { ...S.classYear, stale: true, taskState: "check_failed" };
  }
  // The saved place, the typed sentence and the locked requests belong to one account. An ended
  // session keeps them for that account, because a locked request still decides a press (C7).
  if (nextAccount && attendanceMemoAccount && nextAccount !== attendanceMemoAccount) {
    window.TMAttendance?.forget();
    attendanceMemoAccount = "";
    attendanceReturnPending = false;
  }
  S.google = status;
  // The server ended this PC's session, so it is signed out here, and a restart would open step 2
  // with 다시 로그인. The account token keeps the email, so nothing else rereads the screen. The
  // open attendance window keeps its band and its last data until it closes (G1).
  if (status?.session_ended) {
    if (S.mode === "attendance") attendanceSessionEnded = true;
    else if (S.mode !== "loading" && !(S.mode === "wizard" && S.step <= 2)) accountProfileNeedsReload = true;
  }
  // LOGIN-32: get_app_info showed the step-2 login screen only because identity was not
  // verified yet (e.g. the boot check failed before the network was up). The first later
  // verified check rereads the saved screen; a sign-in in progress does its own reread.
  if (!previousAccount && nextAccount && !bootIdentityPending && S.mode === "wizard"
      && S.step <= 2 && S.maxStep <= 2 && !S.login && !loginPollRunning
      && !googleLoginVerificationPending && !accountProfileReloadPromise) {
    accountProfileNeedsReload = true;
  }
  // MAIN §7.14: Home with no year goes back to the wizard. get_app_info decides, from the saved
  // 담임 여부; one read per sign-in, so a non-homeroom teacher's Home is not reread every check.
  if (S.mode === "home" && status?.logged_in && status.hasAnyYear === false && yearReloadEpoch !== googleLoginEpoch) {
    yearReloadEpoch = googleLoginEpoch;
    accountProfileNeedsReload = true;
  }
  if (isGoogleReady(status)) {
    S.loginUnreachable = false;
    delete S.fieldIssues["google-login"];
    const oldLoginWarnings = [FIELD_MESSAGES["google-login"], GOEDU_REQUIRED_MESSAGE, GOOGLE_AUTH_CHECK_MESSAGE, GOOGLE_AUTH_CHECK_MESSAGE_SETTINGS, previousAuthorizationMessage];
    if (S.banner && (S.banner.topic === "google-login" || oldLoginWarnings.includes(S.banner.text))) S.banner = null;
    if (["google_status", "gws_login_start", "gws_login_status"].includes(S.problemIssue?.operation)) clearProblemIssue();
    scheduleGoogleRecovery();
  }
  if (accountProfileNeedsReload) reloadAccountProfile().catch(reportBackgroundFailure);
}
function googleStatusKey(status) {
  if (!status) return "";
  return [status.login_state || "", status.logged_in ? "1" : "0", status.user || "", status.account_allowed === true ? "1" : "0", status.authorization_state || "", status.authorization_reason || ""].join("|");
}
function clearGoogleDependentState(options) {
  const preserveResume = options?.preserveResume === true;
  googleRecoveryNeeded = false;
  if (googleRecoveryTimer) clearTimeout(googleRecoveryTimer);
  googleRecoveryTimer = null;
  googleContextVersion += 1;
  linkListsReadVersion += 1;
  targetStatusReadVersion += 1;
  checksReadVersion += 1;
  checksRetry.inflight = false;
  S.linkLoading = false;
  S.listReads = {};
  S.googleTargetStatuses = {};
  checksRetry.lastGood = [];
  checksRetry.dirtyCards.clear();
  S.classYear = null;
  S.classChat = null;
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
const GOOGLE_RESULT_PAINT_FAILED = "Google 확인 결과를 화면에 표시하지 못했어요. 프로그램을 다시 열어 주세요.";
async function checkGoogleOnReturn() {
  // The open attendance window has its own session band and keeps its last data (G1).
  if (S.mode === "attendance" || S.login || loginWatchChecking || hasCurrentSettingsStatusRequest()) return;
  loginWatchChecking = true;
  const epoch = googleLoginEpoch;
  try {
    if (googleLoginVerificationPending) {
      await pollLoginOnce(beginIssueRequest(false)).catch(reportBackgroundFailure);
      return;
    }
    const displayState = () => JSON.stringify([S.google, S.banner, S.problemIssue, S.fieldIssues["google-login"]]);
    const beforeDisplay = displayState();
    let status;
    // call() marks a failed actual check unknown and repaints it; never infer logout.
    try { status = await call("google_status"); } catch (error) { reportBackgroundFailure(error); return; }
    if (epoch !== googleLoginEpoch || S.login || hasCurrentSettingsStatusRequest()) return;
    adoptGoogleStatus(status);
    if (googleAuthCheckFailed(status)) { paintSettingsReadiness(); return; }
    // An ended session speaks through the login row and step title: `Teacher Manager에 다시 로그인해 주세요.` and [다시 로그인].
    if (beforeDisplay !== displayState()) {
      paintSettingsReadiness();
    } else {
      // An unchanged successful reply still ends the visible pending state.
      paintGoogleObservation();
    }
  } catch (error) {
    // The reply arrived but adopting or painting it failed: end the pending frame
    // on screen instead of hiding the failure (OBS-01).
    console.error(error);
    try { setBanner("error", GOOGLE_RESULT_PAINT_FAILED, "google-login"); } catch (paintError) { console.error(paintError); paintFallbackNotice(); }
  } finally { loginWatchChecking = false; }
}
function startLoginWatch() {
  if (loginWatchTimer) return;
  const tick = async () => {
    try { await checkGoogleOnReturn(); }
    finally { loginWatchTimer = setTimeout(tick, LOGIN_WATCH_INTERVAL_MS); }
  };
  loginWatchTimer = setTimeout(tick, LOGIN_WATCH_INTERVAL_MS);
}
window.addEventListener("focus", () => {
  if (!window.pywebview?.api) return;
  if (S.login?.logging_out) return;
  if (S.login) { if (!loginPollRunning) pollLogin(0); }
  else checkGoogleOnReturn();
  scheduleYearRevisionCheck();
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
  // The reason a locked 다음 stays locked, on the line beside it. The span is always there on step 8, so a changed reason replaces only the span (LOCK-01, LOCK-02).
  const why = S.step === 8 ? wizardGateWhy() : null;
  const whyHtml = S.step === 8 ? `<span class="foot-why${why ? " " + why.tone : ""}" role="status">${why ? esc(why.text) : ""}</span>` : "";
  return `<div class="foot">${back}${whyHtml}${next}</div>`;
}
// Grey while the answer is being read or could not be, amber once the teacher has something to do (C6, C8).
function wizardGateWhy() {
  if (!(attendanceUiEnabled() && S.mode === "wizard" && S.step === 8 && S.connectTab === "attendance") || attendanceWizardGateOpen()) return null;
  return { text: attendanceWizardGateMessage(), tone: attendanceWizardGateTone() };
}
function attendanceWizardGateTone() {
  const year = currentClassYear();
  if (!year || year.phase !== "ready") return "muted";
  if (year.pendingWrite || !classTaskLinkedAsChosen(year) || year.taskState === "unavailable") return "warn";
  return year.taskState === "ready" ? "warn" : "muted";
}
function paintGateWhy() {
  const el = document.querySelector(".foot .foot-why");
  if (!el) return;
  const why = wizardGateWhy(), text = why ? why.text : "", cls = "foot-why" + (why ? " " + why.tone : "");
  if (el.textContent !== text) el.textContent = text;
  if (el.className !== cls) el.className = cls;
}

// A background answer leaves an unchanged element attached, so a click in progress lands (LOCK-02).
function replaceIfChanged(element, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const next = template.content.firstElementChild;
  if (!next) element.remove();
  else if (!element.isEqualNode(next)) element.replaceWith(next);
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
    if (!currentFoot.isEqualNode(nextFoot)) {
      // Child by child: a button that did not change stays attached (LOCK-01, LOCK-02).
      const same = currentFoot.children.length === nextFoot.children.length;
      if (!same) currentFoot.replaceWith(nextFoot);
      else Array.from(nextFoot.children).forEach((child, i) => { const old = currentFoot.children[i]; if (!old.isEqualNode(child)) old.replaceWith(child); });
    }
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

/* Step 8's gate (MAIN §7.14, LOCK-04, LOCK-05). Every way past step 8 passes it: [다음], the
   left step list and direct moves all go through goStepAsync. In P2 it asks for a linked Tasks
   list that opens now and one student; the report template arrives with P4 and the Chat connection with P3.
   Optional items never block: neither the Gemini key nor the greetings. */
function attendanceWizardGateOpen() {
  if (!isHomeroomTeacher()) return true;
  const year = currentClassYear();
  return Boolean(year?.phase === "ready" && !year.pendingWrite && classTaskLinkedAsChosen(year)
    && year.taskState === "ready" && (year.answer.students || []).length > 0);
}
function classTaskLinkedAsChosen(year) {
  const linked = String(year?.answer?.yearDoc?.taskListId || "");
  const chosen = String(S.draft.profile["담임안내Tasks목록ID"] || "").trim();
  return Boolean(linked && (!chosen || chosen === linked));
}
async function refreshAttendanceWizardGate() {
  if (!isHomeroomTeacher()) return true;
  const context = googleListContext(), owner = screenKey();
  await loadClassYear(true);
  if (context !== googleListContext() || owner !== screenKey()) return false;
  return attendanceWizardGateOpen();
}
function attendanceWizardGateMessage() {
  const year = currentClassYear();
  if (!year || year.phase === "pending") return "출결 항목을 확인하고 있어요.";
  if (year.phase === "failed") return year.detail || "확인하지 못했어요";
  if (year.pendingWrite) return attendanceUnknownText();
  if (!classTaskLinkedAsChosen(year)) return String(S.draft.profile["담임안내Tasks목록ID"] || "").trim()
    ? "Google Tasks 목록의 [연결]을 눌러 주세요." : "Brity 메신저 탭에서 조종례 전달 Tasks 목록을 골라 주세요.";
  if (year.taskState === "pending") return "출결 항목을 확인하고 있어요.";
  if (year.taskState === "unavailable") return "저장된 할 일 목록을 사용할 수 없어요. 접근 권한을 확인하거나 다른 목록을 골라 주세요.";
  // Not read or not readable now: the Tasks row says the same, and ↻ or [다음] reads it again (C6, C8).
  if (year.taskState !== "ready") return googleTargetCheckFailedMessage();
  return "학생명단 화면에서 번호·이름·이메일을 입력하고 [명단 저장]을 눌러 주세요.";
}
function setAttendanceGateBanner(message = attendanceWizardGateMessage()) {
  S.banner = { kind: attendanceWizardGateTone(), text: message, topic: "attendance-gate" };
  render();
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
  S.banner = null;
  if (target === 3 && attendanceUiEnabled() && isGoogleReady(S.google)) {
    await loadClassYear(true);
    if (!current()) return;
    const currentYear = currentClassYear();
    const doc = currentYear?.phase === "ready" ? currentYear.answer?.yearDoc : null;
    if (doc) {
      const context = googleListContext();
      const run = yearSetupRun?.context === context ? yearSetupRun : null;
      // A fresh second-PC opening displays the server. Back navigation keeps an edited draft.
      if (!run || run.fields === JSON.stringify(classFieldsFromProfile())) {
        hydrateClassIdentity(doc);
        yearSetupRun = { context, year: currentYear.answer.year, version: doc.version, fields: JSON.stringify(classFieldsFromProfile()) };
      }
    }
  }
  S.step = target;
  S.maxStep = Math.max(S.maxStep || 1, S.step);
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
function wizardProblemKind(problem) { return problem === SERVER_UNREACHABLE_TEXT ? "muted" : "warn"; }
async function advanceWizardNext() {
  const owner = screenKey(), epoch = accountUiEpoch;
  const mode = S.mode, step = S.step, edit = S.edit;
  const current = () => owner === screenKey() && epoch === accountUiEpoch;
  const validate = validators[S.step];
  const problem = validate ? await validate() : "";
  if (problem) {
    // Validation may intentionally reveal the messenger tab's offending field.
    if (epoch === accountUiEpoch && mode === S.mode && step === S.step && edit === S.edit) setBanner(wizardProblemKind(problem), problem);
    return;
  }
  if (!current()) return;
  if (S.step === 8 && S.connectTab === "messenger") {
    await saveDraft();
    if (!current()) return;
    S.banner = null;
    S.connectTab = "attendance";
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
    // The attendance tab is the last tab of step 8 (the AI tab is gone, D18). The gate passed
    // once above: save the next step while 다음 stays locked, then show it.
    const next = S.step + 1;
    await call("save_setup_state", { ...currentState(), step: next, max_step: Math.max(S.maxStep || 1, next) });
    if (!current()) return;
    await stopHotkeyRecording();
    if (!current()) return;
    S.banner = null;  // 게이트 통과 — 남은 안내 배너를 걷는다 (검토 C2)
    S.step = next;
    S.maxStep = Math.max(S.maxStep || 1, next);
    render();
    return;
  }
  await goStepAsync(S.step + 1);
}

bindActions({
  "go-prev": () => {
    if (S.step === 8 && S.connectTab === "attendance") {
      S.connectTab = "messenger";
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
      if (problem) { setBanner(wizardProblemKind(problem), problem); return; }
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
let googleLoginVerificationPending = false;
async function readGoogleStatus() {
  if (!googleLoginVerificationPending) return call("google_status");
  const snap = await call("gws_login_status");
  googleLoginVerificationPending = snap.verification_pending === true;
  return snap.google_status || call("google_status");
}
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
  if (snap.verification_pending === true) {
    googleLoginVerificationPending = true;
    S.login = null;
    if (snap.google_status) adoptGoogleStatus(snap.google_status);
    setBanner("warn", googleAuthCheckMessage(), "google-login");
    return false;
  }
  googleLoginVerificationPending = false;
  if (snap.ok === true) {
    S.loginCode = "";
    // A different email has already queued its reread through the account token change.
    const signInFromWizard = S.mode === "wizard" && !googleLoginResumeContext
      && !accountProfileNeedsReload && !accountProfileReloadPromise;
    request = beginIssueRequest(false);
    delete S.fieldIssues["google-login"];
    clearGoogleDependentState({ preserveResume: true });  // 재승인은 선택을 보존하고 상태만 새로 읽는다
    await refreshSettingsStatus(request, { loginComplete: true, loginEpoch: epoch, googleStatus: snap.google_status });
    if (!ownsIssueRequest(request)) return false;
    if (isGoogleReady(S.google)) {
      // ACCT-01: a same-email sign-in may return this account to its own finished folder
      // (only the generation changes), so read the saved screen instead of the empty wizard.
      if (signInFromWizard && !accountProfileReloadPromise) {
        accountProfileNeedsReload = true;
        await reloadAccountProfile();
      }
      if (!(await resumeInterruptedGoogleScreen(request))) showToast("Google 계정으로 로그인했어요");
      refreshChecks().catch(reportBackgroundFailure);
      googleRecoveryNeeded = true;
      scheduleGoogleRecovery();
      returnToAttendanceAfterSignin();
    } else setBanner("warn", googleAuthorizationMessage(S.google), "google-login");
    return false;
  }
  if (snap.ok === false) {
    S.login = null;
    S.loginCode = "";
    if (snap.google_status) adoptGoogleStatus(snap.google_status);
    if (snap.error_code === "SIGNIN_SERVER_UNREACHABLE") {
      // Nothing changed at the server or on this PC: grey, with one retry (SIGN-IN).
      S.loginUnreachable = true;
      setBanner("warn", "", "google-login");
      return false;
    }
    setBanner("error", snap.detail || "Google 로그인을 끝내지 못했어요. 실패 원인은 아직 확인하지 못했습니다.", "google-login");
    return false;
  }
  const viewChanged = !S.login
    || String(S.login.url || "") !== String(snap.url || "")
    || Boolean(S.login.browser_opened) !== Boolean(snap.browser_opened)
    || String(S.login.phase || "") !== String(snap.phase || "")
    || Boolean(S.login.code_entry) !== Boolean(snap.code_entry)
    || String(S.login.code_error || "") !== String(snap.code_error || "");
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
  "gws-login": () => startSignin("gws_login_start"),
  // 권한 다시 승인: the same browser steps from the session; this PC's family continues (SIGN-IN).
  "reauthorize-google": async () => {
    if (!(S.mode === "edit" && S.edit === "settings") && !(S.mode === "wizard" && S.step === 2)) {
      await actions["goto-settings"]();
    }
    await startSignin("gws_reapprove_start");
  },
  "login-code-submit": async () => {
    if (!S.login) return;
    const box = document.querySelector('input[name="login-code"]');
    const code = String(box ? box.value : S.loginCode).trim();
    S.loginCode = code;
    if (!code) return;
    const epoch = googleLoginEpoch;
    const reply = await call("gws_login_submit_code", code);
    if (epoch !== googleLoginEpoch || !S.login) return;
    if (!reply?.accepted) { S.login = { ...S.login, code_error: "SIGNIN_CODE_INVALID" }; render(); return; }
    S.login = { ...S.login, code_error: "", phase: "redeeming" };
    render();
    pollLogin(0);
  },
  "gws-logout": async () => {
    if (S.login?.logging_out) return;
    if (!window.confirm("이 컴퓨터의 Teacher Manager에서 로그아웃할까요? 다른 컴퓨터는 그대로 쓸 수 있어요. 다시 로그인할 때 Google 권한 승인을 다시 받아요. 로그아웃한 동안 받은 Brity 메신저 내용은 다시 로그인하면 등록돼요.")) return;
    if (!(await flushEditSave())) return;
    if (S.login?.logging_out) return;
    const epoch = ++googleLoginEpoch;
    googleLoginVerificationPending = false;
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
    window.TMAttendance?.forget();
    attendanceReturnPending = false;
    S.google = { ...S.google, logged_in: false, user: "", account_allowed: false, authorization_state: "login_required",
      login_state: "logged_out", authorization_reason: "", authorization_detail: "", local_settings_error: "" };
    render();
    accountProfileNeedsReload = true;
    await reloadAccountProfile();
    if (epoch !== googleLoginEpoch) return;
    showToast("Teacher Manager에서 로그아웃했어요");
    if (S.mode === "home") setBanner("warn", SIGNED_OUT_HOME_TEXT, "google-login");
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
    googleLoginVerificationPending = false;
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
});
async function startSignin(method) {
  if (S.login) return;
  if (googleLoginVerificationPending) {
    await pollLoginOnce(beginIssueRequest(false));
    return;
  }
  const epoch = ++googleLoginEpoch;
  S.banner = null;
  S.loginUnreachable = false;
  S.loginCode = "";
  S.login = { running: true };
  render();
  try {
    const snapshot = await call(method);
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
}
document.addEventListener("input", (event) => {
  if (event.target?.name === "login-code") S.loginCode = event.target.value;
});

/* ---------- 2단계: 내 정보 ---------- */
const IDENTITY_FIELDS = ["선생님이름", "학교명", "반이름"];
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
  const known = currentClassYear()?.answer?.currentSchoolYear;
  if (known) return known;
  // Display only: the year document is made with the year the server answers.
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
  const serverClass = attendanceUiEnabled() && S.mode === "edit" ? currentClassYear() : null;
  const classUnavailable = attendanceUiEnabled() && S.mode === "edit" && (!serverClass || serverClass.phase !== "ready" || serverClass.stale);
  const locked = homeroom !== "예" || classUnavailable;
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
    ${classUnavailable ? `<p class="sub" role="status">${serverClass?.phase === "failed" ? esc(serverClass.detail || "학급 정보를 확인하지 못했어요.") : "학급 정보를 확인 중…"}</p>` : ""}
    ${formTable(
      fieldRow("선생님이름", "이름", p["선생님이름"], { disabled: classUnavailable }) +
      rawRow("학년도", `<div class="field"><span data-school-year="automatic">${esc(year)}학년도</span><span class="hint">한국 시간 기준으로 자동 적용</span></div>`) +
      fieldRow("학교명", "학교 이름", p["학교명"], { placeholder: "예: OO고등학교", disabled: classUnavailable }) +
      rawRow("학교급 (수업 시간을 자동 계산해요)",
        choiceCell("학교급", [["초", "초등 (40분)"], ["중", "중학 (45분)"], ["고", "고등 (50분)"]])) +
      rawRow("담임을 맡고 있나요?", choiceCell("담임여부", [["예", "예"], ["아니오", "아니오"]])) +
      homeroomRow +
      (homeroom === "예" ? fieldRow("반이름", "반 이름", p["반이름"] || "", { disabled: classUnavailable }) : "")
    )}`;
}
function syncProfileFields() {
  Object.assign(S.draft.profile, readFields(IDENTITY_FIELDS.filter(name => document.querySelector(`[name="${name}"]`) && !document.querySelector(`[name="${name}"]`).disabled)));
  S.draft.profile["학교급"] = readRadio("학교급");
  S.draft.profile["담임여부"] = readRadio("담임여부");
  if (S.draft.profile["담임여부"] === "예" && !document.querySelector(`[name="담임학년"]`)?.disabled) {
    Object.assign(S.draft.profile, readFields(["담임학년", "담임반"]));
  }
  // A stored historical year is never submitted as an editable profile value.
  delete S.draft.profile["학년도"];
}

async function validateIdentity() {
  syncProfileFields();
  const rows = identityIssues();
  replaceEditableIssues(rows);
  if (rows.length) { render(); return firstIssueMessage(rows); }
  return await ensureYearForProfile();
}
// MAIN §7.14: the year document follows 내 정보 for a homeroom teacher. A second PC's setup
// returns the existing year unchanged; fields changed in this run update it with its version.
// A change to 아니오 deletes nothing. A failed read changes nothing.
let yearSetupRun = null;
function classFieldsFromProfile() {
  const p = S.draft.profile;
  const grade = String(p["담임학년"] || "").trim(), classNumber = String(p["담임반"] || "").trim();
  return { schoolName: String(p["학교명"] || "").trim(), grade, classNumber,
    classLabel: String(p["반이름"] || "").trim() || (grade && classNumber ? `${grade}학년 ${classNumber}반` : ""), teacherName: String(p["선생님이름"] || "").trim() };
}
const CLASS_IDENTITY_MAP = { "학교명": "schoolName", "담임학년": "grade", "담임반": "classNumber", "반이름": "classLabel", "선생님이름": "teacherName" };
function hydrateClassIdentity(doc) {
  Object.entries(CLASS_IDENTITY_MAP).forEach(([local, server]) => { S.draft.profile[local] = String(doc[server] || ""); });
}
let identityPendingWrite = null;
async function saveClassIdentity(dirtyFields) {
  if (!Object.keys(CLASS_IDENTITY_MAP).some(field => dirtyFields.has(field)) && !dirtyFields.has("담임여부")) return;
  const context = googleListContext();
  let current = currentClassYear();
  if (!current || current.phase !== "ready" || current.stale) throw Object.assign(new Error("학급 정보를 확인하지 못했어요. 창을 다시 열어 확인해 주세요."), { editSaveResult: true });
  if (!current.answer?.yearDoc) {
    if (!isHomeroomTeacher()) return;
    const problem = await ensureYearForProfile();
    if (problem) throw Object.assign(new Error(problem), { editSaveResult: true });
    return;
  }
  const fields = {};
  Object.entries(CLASS_IDENTITY_MAP).forEach(([local, server]) => {
    if (dirtyFields.has(local) && String(S.draft.profile[local] || "").trim() !== String(current.answer.yearDoc[server] || "")) fields[server] = String(S.draft.profile[local] || "").trim();
  });
  if (!Object.keys(fields).length && !identityPendingWrite) return;
  const held = identityPendingWrite?.context === context ? identityPendingWrite : null;
  const body = held?.body || { requestId: newRequestId(), year: current.answer.year, version: current.answer.yearDoc.version, ...fields };
  const reply = await dataCall("/v1/data/settings", body);
  if (context !== googleListContext()) throw new StaleAccountResponse();
  if (reply?.unknown) identityPendingWrite = { context, body };
  else identityPendingWrite = null;
  if (reply?.status !== 200) {
    if (reply?.body?.error === "STALE") await loadClassYear(true);
    throw Object.assign(new Error(dataFailureText(reply)), { editSaveResult: true });
  }
  const doc = { ...current.answer.yearDoc, ...body, version: reply.body.version };
  delete doc.requestId; delete doc.year;
  adoptClassYearAnswer({ ...current.answer, yearDoc: doc }, context);
  if (held) {
    // The replay confirms A only. Save the caller's B against A's new version before mirroring B.
    await saveClassIdentity(dirtyFields);
  }
  // A queued edit that arrived during the write stays dirty; retry never replays this confirmed save.
}
async function ensureYearForProfile() {
  if (!isHomeroomTeacher()) return "";
  const classFields = classFieldsFromProfile();
  const fields = JSON.stringify(classFields);
  const context = googleListContext();
  const run = yearSetupRun?.context === context ? yearSetupRun : null;
  if (run?.fields === fields) return "";
  const check = await dataCall("/v1/data/check", {});
  if (context !== googleListContext()) return "";
  if (check?.status !== 200 || !check.body?.currentSchoolYear) return dataFailureText(check);
  const year = check.body.currentSchoolYear;
  const send = version => dataCall("/v1/data/years/setup", { requestId: newRequestId(), year, classFields,
    ...(Number.isInteger(version) ? { version } : {}) });
  const reply = await send(run?.year === year ? run.version : null);
  if (context !== googleListContext()) return "";
  if (reply?.status !== 200 || !Number.isInteger(reply.body?.version)) {
    if (reply?.body?.error === "STALE") {
      // Learn the current version; the next press sends the change with it (G5).
      const fresh = await send(null);
      if (fresh?.status === 200 && context === googleListContext()) yearSetupRun = { context, year, version: fresh.body.version, fields: run?.fields || "" };
    }
    return dataFailureText(reply);
  }
  const read = await dataCall("/v1/data/read", { year });
  if (context !== googleListContext()) return "";
  if (read?.status !== 200 || !read.body?.yearDoc) return dataFailureText(read);
  adoptClassYearAnswer(read.body, context);
  hydrateClassIdentity(read.body.yearDoc);
  yearSetupRun = { context, year, version: read.body.yearDoc.version, fields: JSON.stringify(classFieldsFromProfile()) };
  if (S.google) S.google.hasAnyYear = true;
  S.classYear = null;
  return "";
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
// A row is [number, name, email, status, studentId, version]: the last two are never shown.
function rosterEditableRows(rows = [], minimum = 30) {
  const result = rows.map(row => [...row]);
  let next = Math.max(0, ...result.map(row => Number(row[0]) || 0)) + 1;
  while (result.length < minimum) result.push([String(next++), "", ""]);
  return result;
}
function rosterRowHasStudent(row) { return [row[1], row[2]].some(value => String(value || "").trim()); }
// 학적: 재적만 우리 반 학생이다. 재적은 세 칸 행으로 저장해 학적 열이 없던 명단과 같은 모양을 쓴다.
const ROSTER_STATUSES = ["재적", "전출", "자퇴(퇴학)"];
function rosterCells(row) {
  const [number, name, email] = row.slice(0, 3).map(value => String(value ?? "").trim());
  return [number, name, email, String(row[3] ?? "").trim() || "재적"];
}
function rosterRowsFromServer(students) {
  return [...students].sort((a, b) => (Number(a.number) || 0) - (Number(b.number) || 0))
    .map(s => [String(s.number ?? ""), String(s.name ?? ""), String(s.email ?? ""), String(s.status || "재적"), String(s.id), s.version]);
}
function rosterEditorFromRead(answer, context) {
  const rows = rosterRowsFromServer(answer.students || []);
  return { rows: rosterEditableRows(rows), state: "ready", context, dirty: false, busy: false, detail: "", pendingWrite: null,
    baseline: Object.fromEntries(rows.map(row => [row[4], rosterCells(row).join("\u0000")])) };
}
// There is no local-first copy: the roster is read from and saved to the server (MAIN §7.14).
async function loadRosterEditor(refresh = false) {
  if (!isHomeroomTeacher()) return;
  const context = rosterEditorContext();
  const owner = screenKey();
  if (rosterEditorPending?.context === context && rosterEditorPending.owner === owner) return rosterEditorPending.promise;
  if (!refresh && S.rosterEditor?.context === context && S.rosterEditor.state !== "loading") return;
  const version = ++rosterEditorRequest;
  let finishPending;
  rosterEditorPending = { context, owner, promise: new Promise(resolve => { finishPending = resolve; }) };
  S.rosterEditor = { rows: [], state: "loading", context };
  try {
    const reply = await dataCall("/v1/data/read", {});
    if (version !== rosterEditorRequest || context !== rosterEditorContext() || owner !== screenKey()) return;
    if (reply?.status === 200 && reply.body?.complete === true) S.rosterEditor = rosterEditorFromRead(reply.body, context);
    // A failed read is never an empty roster (C1, C2): no rows to type over, only 다시 불러오기.
    else S.rosterEditor = { rows: [], state: "unavailable", context, detail: dataFailureText(reply) };
  } catch (_) {
    if (version !== rosterEditorRequest || context !== rosterEditorContext() || owner !== screenKey()) return;
    S.rosterEditor = { rows: [], state: "unavailable", context, detail: "명단을 불러오지 못했어요. 다시 불러오기를 눌러 주세요." };
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
    if (!ROSTER_STATUSES.includes(String(rows[i][3] || "재적").trim())) return `${i + 1}번째 학생 학적은 재적·전출·자퇴(퇴학) 중에서 골라 주세요.`;
    numbers.add(Number(number)); emails.add(email.toLowerCase());
  }
  return "";
}
// What this save changes, with the row each change belongs to. A row emptied by hand is a delete.
function rosterChanges(editor) {
  const changes = [], rows = [];
  for (const row of editor.rows) {
    const [number, name, email, status] = rosterCells(row);
    const studentId = row[4], version = row[5];
    if (!studentId) {
      if (rosterRowHasStudent(row)) { changes.push({ number, name, email, status }); rows.push(row); }
    } else if (!rosterRowHasStudent(row)) {
      changes.push({ studentId, version, delete: true }); rows.push(row);
    } else if (editor.baseline?.[studentId] !== rosterCells(row).join("\u0000")) {
      changes.push({ studentId, version, number, name, email, status }); rows.push(row);
    }
  }
  return { changes, rows };
}
function applyRosterAnswer(editor, rows, answer) {
  const results = Array.isArray(answer?.students) ? answer.students : [];
  const gone = new Set();
  rows.forEach((row, index) => {
    const result = results[index];
    if (!result) return;
    if (result.deleted) { gone.add(row); return; }
    row[3] = rosterCells(row)[3];
    row[4] = String(result.studentId);
    row[5] = result.version;
  });
  const kept = editor.rows.filter(row => !gone.has(row));
  const saved = kept.filter(row => row[4]);
  return { ...editor, rows: rosterEditableRows(kept), state: "ready", dirty: false, detail: "명단을 저장했어요.", pendingWrite: null, saveFailed: false,
    baseline: Object.fromEntries(saved.map(row => [row[4], rosterCells(row).join("\u0000")])) };
}
function rosterUnknownText() {
  return "저장됐는지 확인하지 못했어요. 서버에 연결되면 [다시 불러오기]를 눌러 확인해 주세요.";
}
async function saveRosterEditor() {
  let editor = S.rosterEditor;
  if (!editor || editor.busy || editor.pendingWrite || editor.context !== rosterEditorContext()) return false;
  const problem = rosterInputProblem();
  if (problem) { setBanner("warn", problem, "roster-validation"); return false; }
  if (S.banner?.topic === "roster-sync" || S.banner?.topic === "roster-validation") S.banner = null;
  const context = editor.context, owner = screenKey();
  // The saved editor can outlive its screen; a replacement editor cannot inherit this request.
  const current = () => context === rosterEditorContext() && S.rosterEditor === editor;
  const visible = () => owner === screenKey();
  const { changes, rows } = rosterChanges(editor);
  if (!changes.length) {
    editor.dirty = false;
    editor.detail = "바뀐 내용이 없어요.";
    if (visible()) render();
    return true;
  }
  editor.busy = true;
  try {
    render();  // inside try: a failed paint must still clear editor.busy in finally (OBS-10)
    const body = { requestId: newRequestId(), changes };
    let reply = await dataCall("/v1/data/students", body);
    if (!current()) return false;
    if (reply?.body?.error === "YEAR_NOT_STARTED") {
      // A refusal stored nothing, so the same request can go once more after the year exists.
      const yearProblem = await ensureYearForProfile();
      if (!current()) return false;
      if (yearProblem) { editor.detail = yearProblem; editor.saveFailed = true; if (visible()) setBanner(wizardProblemKind(yearProblem), yearProblem, "roster-sync"); return false; }
      reply = await dataCall("/v1/data/students", body);
      if (!current()) return false;
    }
    if (reply?.unknown) {
      // G4: the input stays locked with its request ID; 다시 불러오기 replays it once, then reads.
      editor.pendingWrite = body;
      editor.detail = rosterUnknownText();
      if (visible()) setBanner("warn", editor.detail, "roster-sync");
      return false;
    }
    if (reply?.status === 200) {
      S.rosterEditor = editor = { ...applyRosterAnswer(editor, rows, reply.body), busy: true };
      dropClassYearRead();
      S.classYear = null;   // The Tasks row and the gate count students from a new read.
      if (visible()) S.banner = null;
      // A saved roster may hold fixed emails: re-read the student Chat state (CLASS-GROUP-01).
      studentConnect = null;
      S.rosterEmailFix = null;
      return true;
    }
    const text = dataFailureText(reply);
    if (reply?.body?.error === "STALE") {
      // G5: amber in place, then a read.
      editor.busy = false;
      if (visible()) { S.banner = { kind: "warn", text, topic: "roster-sync" }; await loadRosterEditor(true); }
      return false;
    }
    editor.detail = text;
    editor.saveFailed = true;
    if (visible()) setBanner(wizardProblemKind(text), text, "roster-sync");
    return false;
  } catch (_) {
    if (current()) {
      editor.pendingWrite = null;
      editor.saveFailed = true;
      editor.detail = "명단 저장 결과를 확인하지 못했어요. 입력한 내용은 이 화면에 남아 있어요. 다시 저장해 주세요.";
      if (visible()) setBanner("warn", editor.detail, "roster-sync");
    }
    return false;
  } finally {
    // Cleanup belongs to this editor, even when its response was fenced out.
    editor.busy = false;
    if (S.rosterEditor === editor && visible()) render();
  }
}
async function settleRosterWrite(editor) {
  editor.busy = true;
  S.banner = null;
  render();
  let answered = false;
  try {
    const reply = await dataCall("/v1/data/students", editor.pendingWrite);
    if (S.rosterEditor !== editor) return;
    answered = !reply?.unknown && Boolean(reply?.status);
    if (!answered) { setBanner("warn", rosterUnknownText(), "roster-sync"); return; }
    editor.pendingWrite = null;
    if (reply.status !== 200) S.banner = { kind: "warn", text: dataFailureText(reply), topic: "roster-sync" };
    else { dropClassYearRead(); S.classYear = null; }
  } finally {
    editor.busy = false;
    if (S.rosterEditor === editor && !answered) render();
  }
  await loadRosterEditor(true);
}
// Leaving saves first. A locked request stays with the editor, so leaving loses nothing (G4).
async function rosterSavedBeforeLeaving() {
  const editor = S.rosterEditor;
  return !editor?.dirty || Boolean(editor.pendingWrite) || await saveRosterEditor();
}
function rosterSavedForNext() {
  if (!isHomeroomTeacher()) return true;
  const editor = S.rosterEditor;
  return Boolean(editor?.context === rosterEditorContext() && editor.state === "ready" && !editor.dirty && !editor.busy
    && !editor.pendingWrite && editor.rows.some(row => row[4]) && !rosterInputProblem());
}
async function validateRoster() {
  if (!isHomeroomTeacher()) return "";
  await loadRosterEditor();
  const editor = S.rosterEditor;
  if (editor?.state === "unavailable" || editor?.pendingWrite) return editor.detail || rosterUnknownText();
  const problem = rosterInputProblem();
  if (problem) return problem;
  return rosterSavedForNext() ? "" : "명단 저장을 먼저 눌러 주세요. 저장한 뒤 다음으로 진행할 수 있어요.";
}
function stepRoster() {
  if (!isHomeroomTeacher()) return `<h1>담임학급 학생명단</h1><p class="sub">담임을 맡지 않아 이 단계는 사용하지 않아요.</p>`;
  if (S.rosterEditor?.context !== rosterEditorContext()) loadRosterEditor();
  const editor = S.rosterEditor || { state: "loading", rows: [] };
  if (editor.state === "loading") return `<h1>담임학급 학생명단</h1><p class="sub">명단을 불러오고 있어요…</p>`;
  const disabled = editor.busy || editor.pendingWrite ? " disabled" : "";
  const rows = editor.rows.map((row, index) => {
    const status = String(row[3] || "재적").trim();
    const options = (ROSTER_STATUSES.includes(status) ? ROSTER_STATUSES : [...ROSTER_STATUSES, status])
      .map(value => `<option value="${esc(value)}"${value === status ? " selected" : ""}>${esc(value)}</option>`).join("");
    const fix = rosterEmailFixActive(row) ? ' class="roster-email-fix"' : "";
    return `<tr>${row.slice(0, 3).map((value, column) => `<td><input data-roster-cell="${index}:${column}" aria-label="${index + 1}번째 학생 ${["번호", "이름", "이메일"][column]}" ${column === 2 ? 'type="email"' + fix : column === 0 ? 'inputmode="numeric"' : ''} value="${esc(value)}"${disabled}></td>`).join("")}<td><select data-roster-cell="${index}:3" aria-label="${index + 1}번째 학생 학적"${disabled}>${options}</select></td></tr>`;
  }).join("");
  if (S.rosterEmailFix) setTimeout(rosterEmailFixAfterPaint, 0);
  const unavailable = editor.state === "unavailable";
  // After a failed save it is also the way out: the window closes only on a saved or reread roster.
  const reloadShown = unavailable || Boolean(editor.pendingWrite) || Boolean(editor.saveFailed);
  const status = editor.busy ? "명단 저장 결과를 확인하고 있어요." : editor.dirty ? "수정한 명단을 저장해 주세요." : editor.detail || "";
  return `<h1>담임학급 학생명단</h1>
    <p class="sub">학생 이메일은 경기도교육청 클라우드 계정(@goedu.kr)을 권장합니다.<br>엑셀·구글시트에서 복사·붙여넣기 가능합니다.</p>
    <div class="roster-scroll"><table class="grid-table roster-table"><thead><tr><th scope="col">번호</th><th scope="col">이름</th><th scope="col">이메일 · 필수</th><th scope="col">학적</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="roster-actions"><button class="btn-tonal" data-action="roster-add"${disabled || unavailable || editor.rows.length >= 199 ? " disabled" : ""}>행 추가</button>
      ${reloadShown ? `<button class="btn-quiet" data-action="roster-reload"${editor.busy ? " disabled" : ""}>다시 불러오기</button>` : ""}
      <button class="btn" data-action="roster-save"${disabled || unavailable ? " disabled" : ""}>${editor.busy ? "저장 중…" : "명단 저장"}</button>
    </div>
    <p class="sub" role="status">${esc(status)}</p>`;
}
function timetableEditBody() {
  const active = S.timetableTab || "timetable";
  return `<div class="connect-tabs timetable-tabs" role="tablist" aria-label="시간표와 명단"><button role="tab" aria-selected="${active === "timetable"}" class="connect-tab ${active === "timetable" ? "active" : ""}" data-action="timetable-tab" data-tab="timetable"><span class="tab-title">시간표</span></button><button role="tab" aria-selected="${active === "roster"}" class="connect-tab ${active === "roster" ? "active" : ""}" data-action="timetable-tab" data-tab="roster" ${isHomeroomTeacher() ? "" : "disabled"}><span class="tab-title">담임학급 학생명단</span></button><button role="tab" aria-selected="${active === "holidays"}" class="connect-tab ${active === "holidays" ? "active" : ""}" data-action="timetable-tab" data-tab="holidays" ${isHomeroomTeacher() ? "" : "disabled"}><span class="tab-title">휴일·학사일정</span></button></div>${active === "roster" ? stepRoster() : active === "holidays" ? `<div id="attendance-card"></div>` : stepTimetable()}`;
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
  "roster-save": () => saveRosterEditor(),
  "roster-reload": async () => {
    const editor = S.rosterEditor;
    if (editor?.busy) return;
    if (editor?.pendingWrite) { await settleRosterWrite(editor); return; }
    if (editor?.dirty && !window.confirm("이 화면에 입력한 명단 대신 저장된 명단을 불러올까요?")) return;
    if (S.banner?.topic === "roster-sync" || S.banner?.topic === "roster-validation") S.banner = null;
    await loadRosterEditor(true);
  },
  "timetable-tab": async el => {
    if (attendanceCardMount && !attendanceCardMount.canLeave()) return;
    if (el.dataset.tab === "roster" && !isHomeroomTeacher()) return;
    if (S.rosterEditor?.busy) return;
    const context = rosterEditorContext(), owner = screenKey();
    if (S.timetableTab === "roster" && !(await rosterSavedBeforeLeaving())) return;
    if (!(await flushEditSave())) return;
    if (context !== rosterEditorContext() || owner !== screenKey()) return;
    S.timetableTab = el.dataset.tab; render();
  },
});

/* ---------- 연결·설정 공통 ---------- */
const KEY_MESSAGES = {
  ok: "연결 API 키가 정상이에요",
  missing: "Gemini 연결 API 키가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
  invalid: "Gemini 연결 API 키가 맞지 않아요. Google AI Studio에서 다시 복사해 주세요.",
  "rate-limited": "Gemini 사용 한도에 도달했어요. 한도가 언제 다시 열리는지는 확인하지 못했습니다. Google AI Studio에서 사용량을 확인해 주세요.",
  network: "Gemini 응답을 받지 못했어요. 잠시 후 다시 확인해 주세요.",
  "service-unavailable": "Google Gemini가 일시적으로 응답하지 못했어요. 키를 바꾸지 말고 잠시 후 다시 확인해 주세요.",
  "model-unavailable": "선택한 AI 모델을 사용할 수 없어요. 키를 발급한 계정에서 이 모델을 쓸 수 있는지 확인해 주세요.",
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
  return `<div class="section-note" style="margin:0 0 4px">Gemini 연결 API 키 발급받기</div>
    <div class="linkrow"><span class="url" title="${esc(API_KEY_URL)}">${esc(API_KEY_URL)}</span>
    <span class="acts">
      <button class="btn-tonal" data-action="link-open" data-url="${esc(API_KEY_URL)}">${icon("external-link", "small blue")} 열기</button>
      <button class="btn-tonal youtube" data-action="link-open" data-url="${esc(API_KEY_GUIDE_URL)}">${icon("youtube", "small")} 발급방법</button>
    </span></div>`;
}
const GEMINI_MODEL_CHOICES = [
  ["gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite (기본)", "하루 약 500건 · 가끔 날짜를 틀릴 수 있어요"],
  ["gemini-3.6-flash", "Gemini 3.6 Flash", "학교 계정 키로 하루 약 20건 · Flash-Lite보다 정확해요"],
];
function geminiModelNote(id) {
  return (GEMINI_MODEL_CHOICES.find(([value]) => value === id) || GEMINI_MODEL_CHOICES[0])[2];
}
// Static text only (AI-KEY-03): limits and times measured on a goedu.kr key on 2026-10-28.
// No per-message cost: the user found it misleading.
const GEMINI_MODEL_HELP_HTML = `<div class="model-help-pop" data-model-help-pop role="dialog" aria-label="모델별 하루 사용량과 특징" hidden>
  <b class="model-help-title">모델별 하루 사용량과 특징</b>
  <table class="model-help-table">
    <thead><tr><th>모델</th><th>학교 계정(무료) 하루 사용량</th><th>처리 시간(쪽지 1건)</th><th>정확도</th></tr></thead>
    <tbody>
      <tr><td>Gemini 3.5 Flash-Lite (기본)</td><td>약 500건</td><td>15~30초</td><td>가끔 날짜를 틀릴 수 있어요</td></tr>
      <tr><td>Gemini 3.6 Flash</td><td>약 20건</td><td>약 30초</td><td>자체 시험에서 Flash-Lite보다 정확했어요</td></tr>
    </tbody>
  </table>
  <ul class="model-help-notes">
    <li><b>무료 API 키는 하루 사용량에 한도가 있고, 보낸 내용이 서비스 개선에 쓰일 수 있어요. 유료 API 키는 쓸 때마다 요금이 나가지만, 보낸 내용은 서비스 개선에 쓰이지 않아요. 학생 개인정보가 담긴 쪽지를 자주 분석한다면 유료 API 키를 권장해요.</b></li>
    <li>하루 사용량과 처리 시간은 2026년 10월 28일 학교 계정으로 측정했어요.</li>
    <li>무료 하루 사용량은 매일 오후 4~5시(한국 시간)쯤 다시 채워져요.</li>
    <li>이 수치는 Google 정책에 따라 바뀔 수 있어요.</li>
  </ul>
</div>`;
function setModelHelpOpen(button, open) {
  const pop = button?.closest(".model-field")?.querySelector("[data-model-help-pop]");
  if (!pop) return;
  pop.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  if (open && pop.scrollIntoView) pop.scrollIntoView({ block: "nearest" });
}
function closeModelHelp() {
  let closed = false;
  document.querySelectorAll("[data-model-help]").forEach((button) => {
    if (button.getAttribute("aria-expanded") === "true") { setModelHelpOpen(button, false); closed = true; }
  });
  return closed;
}
document.addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest("[data-model-help]");
  if (button) { setModelHelpOpen(button, button.getAttribute("aria-expanded") !== "true"); return; }
  if (!(event.target.closest && event.target.closest("[data-model-help-pop]"))) closeModelHelp();
});
// Capture phase: Esc closes the help first and must not also close the window.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && closeModelHelp()) { event.preventDefault(); event.stopPropagation(); }
}, true);
document.addEventListener("change", (event) => {
  if (event.target.name !== "gemini_model") return;
  S.draft.bridge.gemini_model = event.target.value;
  const note = event.target.closest(".model-field")?.querySelector("[data-model-note]");
  if (note) note.textContent = geminiModelNote(event.target.value);
});
function geminiSectionHtml(d) {
  const keyLine = S.keyStatus ? badge(S.keyStatus.kind, S.keyStatus.text) : "";
  // AI-KEY-03: two explicit ids only (3.8 removed 2026-10-29); the alias that is served as 3.6 is never offered.
  const chosen = GEMINI_MODEL_CHOICES.some(([id]) => id === d.gemini_model) ? d.gemini_model : GEMINI_MODEL_CHOICES[0][0];
  const options = GEMINI_MODEL_CHOICES.map(([id, label]) =>
    `<option value="${esc(id)}"${id === chosen ? " selected" : ""}>${esc(label)}</option>`).join("");
  const modelRow = rawRow("AI 모델", `<div class="field model-field"><div class="model-line">
      <select name="gemini_model" aria-label="AI 모델">${options}</select>
      <button type="button" class="btn-quiet model-help-btn" data-model-help aria-label="모델별 하루 사용량과 특징" aria-expanded="false">?</button>
    </div><span class="hint" data-model-note>${esc(geminiModelNote(chosen))}</span>${GEMINI_MODEL_HELP_HTML}</div>`);
  return `<div class="section-h">Gemini 연결 API 키</div>
    <div class="sheet-guide"><b>API 키는 반드시 경기도교육청 goedu.kr 계정으로 로그인해 발급받아 주세요.</b> API 키는 결제를 연결했는지에 따라 두 가지로 나뉘어요.
      <ul style="margin:4px 0;padding-left:18px">
        <li>무료 API 키(결제 연결 안 함): 요금은 나가지 않지만 하루 사용량에 한도가 있어요. 보낸 내용은 Google이 서비스 개선에 쓸 수 있고, 사람이 검토할 수도 있어요.</li>
        <li>유료 API 키(Google AI Studio에서 결제 연결): 쓴 만큼 요금이 나가지만, 보낸 내용은 서비스 개선에 쓰이지 않아요. 쪽지 1건에 약 30원이 들어요(Gemini 3.5 Flash-Lite 기준).</li>
      </ul>Gemini 앱 유료 구독(Google AI Pro 등)을 하고 있어도 무료 API 키는 무료 API 키예요. 유료 API 키를 쓰려면 결제를 따로 연결해야 해요.</div>
    ${apiKeyLinkRow()}
    <div style="margin-top:12px"></div>
    ${formTable(
      fieldRow("gemini_api_key", "Gemini 연결 API 키", d.gemini_api_key || "", { type: "password", placeholder: "붙여넣기" }) +
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
  const promise = readLinkLists(force);
  const pending = linkListsPending = { context, promise, owns };
  const clear = () => { if (linkListsPending === pending) linkListsPending = null; };
  promise.then(clear, clear);
  return promise;
}
async function readLinkLists(force) {
  if (!force && S.listsLoaded && ["calendars", "tasklists"].every(kind => S.listReads[kind]?.context === googleListContext())) return;
  if (!isGoogleReady(S.google)) return;
  S.linkLoading = true;
  S.listsError = false;
  S.listsPermissionMissing = false;
  S.listsLoaded = false;
  const resourceContext = googleListContext();
  const version = ++linkListsReadVersion;
  // One open/refresh = one batch: the bridge runs `gws auth status` once for all its reads.
  const batch = `links-${Date.now().toString(36)}-${version}`;
  // Same account/target result: keep it after the window that asked is closed (OBS-02).
  // Dropping it left the rows "pending" with nothing reading them (OBS-14).
  const current = () => version === linkListsReadVersion && resourceContext === googleListContext();
  const paint = () => { if (editingCard() === "settings") paintSettingsReadiness(); else render(); };
  S.lists = { calendars: [], tasklists: [] };
  S.googleTargetStatuses = {};
  for (const kind of ["calendars", "tasklists"]) {
    S.listReads[kind] = { phase: "pending", context: resourceContext, generation: version, value: [], error: null };
  }
  try {
    // Inside try: a failed paint must still clear S.linkLoading, or the 30-minute refresh stays blocked.
    paint();
    await Promise.all(["calendars", "tasklists"].map(async kind => {
      try {
        const rows = await call(kind === "calendars" ? "list_calendars" : "list_tasklists", batch);
        if (!current()) return;
        if (!Array.isArray(rows) || rows.some(row => !row || typeof row.id !== "string" || !row.id.trim()))
          throw new Error("목록 응답을 확인하지 못했어요.");
        S.listReads[kind] = { phase: "ready", context: resourceContext, generation: version, value: rows, error: null };
        S.lists[kind] = rows;
        paint();
        await refreshGoogleTargetStatuses(current, kind, batch);
      } catch (error) {
        if (!current()) return;
        S.listReads[kind] = { phase: "failed", context: resourceContext, generation: version, value: [], error: String(error.message || error) };
        S.lists[kind] = [];
        S.listsError = true;
        if (error?.issue?.code === "GOOGLE_PERMISSION_REQUIRED") S.listsPermissionMissing = true;
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
async function refreshGoogleTargetStatuses(owns = () => true, kind = null, batch = "") {
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
  try { statuses = await call("google_target_statuses", targets, ...(batch ? [batch] : [])); }
  catch (_) { statuses = {}; }
  if (!owns() || version !== targetStatusReadVersion || context !== googleListContext()) return;
  for (const [field, id] of Object.entries(targets)) {
    const key = field.includes("Tasks") ? "tasklists" : "calendars";
    if (versions[key] !== targetReadVersions[key]) continue;
    const result = statuses?.[field];
    S.googleTargetStatuses[field] = result?.id === id ? result : { id, state: "check_failed", detail: googleTargetCheckFailedMessage() };
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
  // No read can run while the sign-in is unread: that is a failed list, never a list still loading.
  const phase = googleAuthCheckFailed(S.google) ? "failed"
    : S.listReads[kindKey]?.context === googleListContext() ? S.listReads[kindKey].phase : "pending";
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
// The only re-read button is the ↻ refresh: the 연결 window title bar, or the
// top right of setup step 8 (user decision 2026-09-28).
function connectionRefreshHint() {
  return `${S.mode === "wizard" ? "화면" : "연결 창"} 위쪽 [연결 상태 새로고침] 버튼(↻)을`;
}
function googleTargetCheckFailedMessage() {
  return `저장된 연결을 확인하지 못했어요. 선택은 그대로 두었어요. ${connectionRefreshHint()} 눌러 주세요.`;
}
function listsErrorNoticeHtml() {
  // Failure is never an empty list (C1, C2): the rows keep their 목록 확인 필요 placeholder.
  if (S.listsPermissionMissing) {
    return `<div class="banner warn"><span>${esc(GRANT_MISSING_TEXT)}</span><button class="btn-tonal" data-action="reauthorize-google" data-busy-text="진행 중…">권한 다시 승인</button></div>`;
  }
  if (!S.listsError && !googleAuthCheckFailed(S.google)) return "";
  return `<div class="banner"><span>목록을 불러오지 못했어요. ${connectionRefreshHint()} 눌러 목록을 다시 불러와 주세요.</span></div>`;
}

/* ---------- 연결 세 탭 — 이름은 짧게, 설명은 아랫줄로 ----------
   전에는 이름 자리에 서비스 나열을 통째로 넣어 12.5px 한 줄에 셋을 늘어놓았고,
   자리가 모자라 끝이 잘렸다 (사용자 결정 2026-07-30). */
const CONNECT_TABS = [
  { tab: "messenger", title: "Brity 메신저", detail: "Calendar · Tasks" },
  { tab: "attendance", title: "출결", detail: "Tasks · Google Chat" },
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
  const locked = !isGoogleReady(S.google) && !googleAuthCheckFailed(S.google);
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
  const unread = googleAuthCheckFailed(S.google) && S.google.server_unreachable
    ? `<div class="banner"><span>${SERVER_UNREACHABLE_TEXT}</span></div>` : "";
  return `<div class="attendance-head"><div>
      <h2>메신저 내용을 어디에 등록할지 정해요</h2>
      <p>Google 연결과 로그인 상태는 설정 화면에서 확인해요.</p>
    </div></div>
    ${listProgress}
    ${unread}
    ${listsErrorNoticeHtml()}
    <div class="connect-section">${linkGroupHtml("cal")}</div>
    <div class="connect-section">${linkGroupHtml("task")}</div>
    <div class="connect-section">${geminiSectionHtml(S.draft.bridge)}</div>`;
}
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
function chatStatusAwaitingRead() {
  // refreshConnectionStatus reads Chat only after the Calendar/Tasks lists: until then an unread status is pending, not a warning.
  return chatStatusReading() || (connectionRefreshActive()
    && (S.chatStatusContext !== chatReadContext() || !S.chatStatus || S.chatStatus === "loading"));
}
function classRoomReadiness() {
  if (!isHomeroomTeacher()) return "not-applicable";
  if (!isGoogleReady(S.google)) return "account-required";
  const year = currentClassYear(), read = S.classChat;
  if (year?.phase === "pending" || classChatPending?.context === classChatContext()
      || (connectionRefreshActive() && (!read || read.context !== classChatContext()))) return "loading";
  if (!year || year.phase !== "ready" || year.stale) return "unverified";
  if (!read || read.context !== classChatContext()) return "unverified";
  if (read.phase !== "ready" || read.value?.connected == null) return "read-failed";
  if (read.value.connected !== true) return "account-required";
  const group = read.value.groupState;
  if (group === "ready" && read.value.classSpace?.name) return "ready";
  return ["not-selected", "missing"].includes(group) ? group : "read-failed";
}
let classChatPending = null;
function classChatContext() {
  const answer = currentClassYear()?.answer;
  return JSON.stringify([googleListContext(), answer?.year || null, answer?.revision ?? null,
    answer?.yearDoc?.revision ?? null, answer?.yearDoc?.version ?? null, answer?.yearDoc?.classSpace?.name || ""]);
}
function loadClassChat(force = false) {
  const year = currentClassYear()?.answer?.year, context = classChatContext();
  if (!isGoogleReady(S.google) || !isHomeroomTeacher() || currentClassYear()?.phase !== "ready" || !Number.isInteger(year)) return Promise.resolve();
  if (classChatPending?.context === context) return classChatPending.promise;
  if (!force && S.classChat?.context === context) return Promise.resolve();
  const entry = {context, promise:null};
  classChatPending = entry;
  entry.promise = (async () => {
    try {
      paintClassChat();
      const reply = await dataCall("/v1/data/chat/status", {year});
      if (context !== classChatContext() || classChatPending !== entry) return;
      S.classChat = {context, phase:reply?.status === 200 && reply.body?.year === year ? "ready" : "failed", value:reply?.body};
    } catch (error) {
      if (context !== classChatContext() || classChatPending !== entry) return;
      if (!(error instanceof StaleAccountResponse)) console.error(error);
      S.classChat = {context, phase:"failed"};
    } finally {
      if (classChatPending === entry) classChatPending = null;
      if (context === classChatContext()) paintClassChat();
    }
  })();
  return entry.promise;
}
async function readClassChat(year) {
  const context = classChatContext();
  if (year !== currentClassYear()?.answer?.year) return {status:0};
  await loadClassChat(true);
  const read = S.classChat;
  return context === classChatContext() && read?.context === context && read.phase === "ready"
    ? {status:200, body:read.value} : {status:0};
}
const classChatObservers = new Set();
function watchClassChat(year, notify) {
  const observer = {account:googleListContext(), year, notify};
  classChatObservers.add(observer);
  return () => classChatObservers.delete(observer);
}
function paintClassChat() {
  const context = classChatContext(), read = S.classChat;
  const value = classChatPending?.context === context ? undefined
    : read?.context === context && read.phase === "ready" ? read.value : null;
  for (const observer of classChatObservers) {
    if (observer.account === googleListContext() && observer.year === currentClassYear()?.answer?.year) {
      try { observer.notify(value); } catch (error) { reportBackgroundFailure(error); }
    }
  }
  // Updating a summary must preserve the open connection form and its mounted card.
  const home = document.querySelector("#app > .body");
  if (home) home.outerHTML = homeHtml(S.mode !== "home");
}
function classRoomReadinessMessage(state = classRoomReadiness()) {
  return {
    ready: "학급 단톡방 연결을 확인했어요.",
    "account-required": "Google Chat의 연결과 권한을 확인해 주세요.",
    unverified: "학급 단톡방 연결을 아직 확인하지 못했어요.",
    loading: "학급 단톡방 연결을 확인하고 있어요.",
    "read-failed": "학급 단톡방 연결을 읽지 못했어요. 기존 단톡방 선택은 그대로예요.",
    // Name the button this screen actually shows (C11).
    "not-selected": "출결 탭의 [환영 인사 보내면서 Google Chat 대화 상대로 연결하기]로 학급 단톡방을 만들어 주세요.",
    missing: "저장된 학급 단톡방을 Google Chat에서 찾지 못했어요. 출결 탭의 [환영 인사 보내면서 Google Chat 대화 상대로 연결하기]로 다시 만들어 주세요.",
  }[state] || "";
}
function loadChatStatus(force, renderResult = true) {
  if (S.chatStatusContext !== chatReadContext()) S.chatStatus = null;
  // A read whose version was bumped by another action is not this screen's read any more:
  // returning its promise left Chat on "아직 확인하지 못했어요" forever (CLASS-GROUP-01, 2026-11-02).
  const livePending = chatStatusPending?.active && chatStatusPending.context === chatReadContext()
    && chatStatusPending.version === chatStatusReadVersion;
  if (livePending) return chatStatusPending.promise;
  if (!force && S.chatStatus != null && S.chatStatus !== "loading") return chatStatusPending?.context === chatReadContext() ? chatStatusPending.promise : undefined;
  if (S.chatStatus == null) S.chatStatus = "loading";
  const context = S.chatStatusContext = chatReadContext();
  const version = ++chatStatusReadVersion;
  const current = () => version === chatStatusReadVersion && context === chatReadContext();
  const request = beginIssueRequest(true);
  // A dropped read still ends its own pending mark, so the next read really starts.
  const settleDropped = () => {
    if (chatStatusPending?.version === version) chatStatusPending.active = false;
    if (S.chatStatus === "loading" && context === chatReadContext()) S.chatStatus = null;
  };
  const promise = call("attendance_chat_status")
    .then((data) => {
      if (!current()) { settleDropped(); return; }
      chatStatusPending.active = false;
      S.chatStatus = data?.read_failed || typeof data?.connected !== "boolean"
        ? { ...(typeof S.chatStatus === "object" ? S.chatStatus : {}), connected: null, read_failed: true }
        : data;
      if (!S.chatStatus.read_failed) clearResolvedReadIssue("attendance_chat_status");
      if (renderResult) render();
      return data;
    })
    .catch((error) => {
      if (!current()) { settleDropped(); return; }
      chatStatusPending.active = false;
      S.chatStatus = { ...(typeof S.chatStatus === "object" ? S.chatStatus : {}), connected: null, read_failed: true };
      if (ownsIssueRequest(request) && showProblemIssue(error, request, paintSettingsReadiness)) return;
      if (renderResult) render();
    });
  chatStatusPending = { context, version, promise, active: true };
  return promise;
}
/* ---------- 학생 Google Chat 연결 (CLASS-GROUP-01) ----------
   P3 connects this row and its dialogs to the new Chat routes. In P2 nothing renders them. */
function isClassSpaceIssue(issue) {
  return issue && ["attendance_chat_create_space", "attendance_chat_set_space", "attendance_chat_spaces"].includes(issue.operation)
    && (issue.actions || []).every(action => action.key === "chat-space-list");
}
function loadChatSpaces(force = false, renderResult = true) {
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
      if (ownsIssueRequest(request) && showProblemIssue(error, request, paintSettingsReadiness)) return;
      if (renderResult) render();
    });
  chatSpacesPending = { context, promise };
  return promise;
}

// One row "학생 Google Chat 연결" (2026-11-02 user decision, CLASS-GROUP-01): the class group
// chat and the 1:1 greetings together. Grey while checking, orange while the teacher has
// something to do, green only when the room, its greeting and every 1:1 greeting are done.
function studentChatRowState(a) {
  const state = classRoomReadiness();
  const waiting = false;
  const roomReading = state === "loading" || (state === "unverified" && S.attendanceLoading);
  const ready = studentConnectReady(a);
  if (ready && S.connectTab === "attendance" && !S.classChatStart && !connectionRefreshActive()
      && studentConnect?.context !== chatReadContext()) loadStudentConnect(false);
  const d = ready && studentConnect?.context === chatReadContext() && studentConnect.state === "ready" ? studentConnect : null;
  const roomReady = state === "ready";
  const roomMissing = ["not-selected", "missing"].includes(state);
  const cs = S.chatStatus && typeof S.chatStatus === "object" ? S.chatStatus : {};
  const greet = d && roomReady && d.class_greeting?.space === String(cs.class_space_id || "") ? d.class_greeting.state : "";
  const groupNeeded = roomMissing || (roomReady && !["sent", "unknown", "pending"].includes(greet));
  const fresh = d ? d.targets.filter(row => !row.failed && !row.fixed) : [];
  const unknown = d ? [...(greet === "unknown" ? [{ key: "__class__", label: "우리 반 단톡방 인사" }] : []), ...d.unknown] : [];
  return { state, waiting, roomReading, ready, d, roomReady, roomMissing, greet, groupNeeded, fresh, unknown,
    studentReading: ready && (!studentConnect || studentConnect.context !== chatReadContext() || studentConnect.state === "loading"),
    readFailed: ready && studentConnect?.context === chatReadContext() && studentConnect.state === "unavailable" };
}
function classSpaceSubrowHtml(a, view = attendanceViewKind()) {
  if (!isHomeroomTeacher()) return "";
  const content = classSpaceContentHtml(a, view);
  const r = studentChatRowState(a);
  const localIssue = problemIssueOwner === screenKey() && isClassSpaceIssue(S.problemIssue) ? problemPanelHtml(S.problemIssue) : "";
  const busy = S.classGroupCreating || S.classSpaceSaving || Boolean(S.classChatStart);
  let chip, tone = "muted", lines = [], buttons = "";
  if (S.classChatStart?.kind === "sending") chip = "보내는 중…";
  else if (r.waiting) chip = "출석부 준비 중…";
  else if (r.roomReading) chip = "확인 중…";
  else if (r.roomMissing && !r.d) {
    // The room is known to be missing: offer the button now; it reads the students first.
    chip = r.state === "missing" ? "확인 필요" : "학급 단톡방 없음"; tone = "warn";
    buttons = `<button class="btn-tonal" data-action="class-space-group-create"${busy ? " disabled" : ""}>${S.classGroupCreating ? "준비하는 중…" : "환영 인사 보내면서 Google Chat 대화 상대로 연결하기"}</button>`;
  } else if (r.studentReading) chip = "확인 중…";
  else if (r.state === "read-failed" || r.readFailed) {
    chip = "확인 필요"; tone = "warn";
    buttons = r.readFailed ? `<button class="btn-tonal" data-action="student-chat-recheck">다시 확인</button>`
      : S.chatSpacesError && !S.attendanceReadFailed && !S.chatStatus?.read_failed ? `<button class="btn-tonal" data-action="class-space-reload">다시 확인</button>` : "";
  } else if (!r.d) {
    // The 1:1 state is not readable yet (for example the roster is not linked): show the room only.
    chip = r.roomReady ? "학급 단톡방 연결됨" : "확인 필요"; tone = "warn";
  } else {
    const d = r.d;
    chip = `학급 단톡방 ${r.roomReady ? "연결됨" : "없음"} · 1:1 ${d.sent}/${d.total}명`;
    const done = r.roomReady && r.greet === "sent" && d.sent === d.total && !r.unknown.length && !d.targets.length;
    tone = d.pending || r.greet === "pending" ? "muted" : done ? "ok" : "warn";
    if (d.pending || r.greet === "pending") chip += " · 보내는 중";
    if (r.groupNeeded || r.fresh.length) {
      buttons += `<button class="btn-tonal" data-action="class-space-group-create"${busy ? " disabled" : ""}>${S.classGroupCreating ? "준비하는 중…" : "환영 인사 보내면서 Google Chat 대화 상대로 연결하기"}</button>`;
    }
    if (r.unknown.length) {
      buttons += `<button class="btn-tonal" data-action="student-chat-unknown"${busy ? " disabled" : ""}>확인하기</button>`;
      lines.push(`<p class="hint">${esc(SCC.unknown(r.unknown.length))}</p>`);
    }
    if (d.failed) {
      lines.push(`<p class="hint student-chat-rejected">${esc(`이메일 때문에 받지 못한 학생: ${(d.failed_labels || []).join(", ")}`)}</p>
        <p class="hint">이메일을 확인하고, 학생 계정에서 Google Chat 앱을 사용하게 해주세요.</p>
        <div class="class-space-actions"><button class="btn-tonal" data-action="student-email-fix"${busy ? " disabled" : ""}>학생 이메일 고치러 가기</button></div>`);
    }
    if ((d.fixed || []).length) {
      lines.push(`<p class="hint">${esc(`이메일을 고친 학생 ${d.fixed.length}명에게 아직 인사를 보내지 않았어요.`)}</p>
        <div class="class-space-actions"><button class="btn-tonal" data-action="student-chat-fixed-send"${busy ? " disabled" : ""}>고친 학생에게 다시 보내기</button></div>`);
    }
  }
  return `<section class="svc-subrow class-space-section student-chat-row">
    <div class="first-setup-head"><b>학생 Google Chat 연결</b><span class="chat-space-reload-status">${buttons}
      <span class="chat-space-status ${tone === "ok" ? "connected" : tone}">${esc(chip)}</span></span></div>
    ${content}${lines.join("")}${localIssue}</section>`;
}

function classSpaceContentHtml(a, view = attendanceViewKind()) {
  if (S.attendanceLoading) return `<p class="hint">출결 연결 상태를 확인하고 있어요.</p>`;
  if (S.attendanceReadFailed) return `<p class="hint">현재 연결 상태를 읽지 못했어요. 기존 단톡방 선택은 그대로예요.</p>`;
  if (a.state === "initial-setup-required") return `<p class="hint">출석부 설정 후 Google Chat을 연결해 주세요.</p>`;
  if (a.state !== "ready") return view === "installation" ? `<p class="hint">위의 Google Chat 연결을 마치면 학급 단톡방을 만들 수 있어요.</p>` : "";
  const cs = S.chatStatus;
  if (chatStatusAwaitingRead()) return `<p class="hint">Google Chat 연결 상태를 확인하고 있어요.</p>`;
  if (S.chatStatusContext !== chatReadContext() || !cs || cs === "loading") return `<p class="hint">Google Chat 연결 상태를 아직 확인하지 못했어요.</p>`;
  if (cs.read_failed) return `<p class="hint">현재 연결 상태를 읽지 못했어요. 기존 단톡방 선택은 그대로예요.</p>`;
  if (!cs.connected) return view === "installation" ? `<p class="hint">위의 Google Chat 연결을 마치면 학급 단톡방을 만들 수 있어요.</p>` : "";
  // One class group chat (2026-10-30): no saved room shows only the create button; a saved one shows its name.
  if (!cs.class_space_id) return "";
  if (!connectionRefreshActive()) loadChatSpaces();
  const saved = esc(String(cs.class_space_name || S.chatSpaceName || "학생 그룹 채팅"));
  if (S.chatSpaces === null || S.chatSpacesLoading) return `<p class="hint">${saved} · 확인하고 있어요…</p>`;
  if (S.chatSpacesError) return `<p class="hint">${saved} · Google Chat에서 확인하지 못했어요. 저장된 학급 단톡방은 그대로예요.</p>`;
  const rooms = Array.isArray(S.chatSpaces) ? S.chatSpaces : [];
  if (!rooms.some(s => String(s.name || "") === String(cs.class_space_id))) return `<p class="hint">${saved} · Google Chat에서 찾지 못했어요. [환영 인사 보내면서 Google Chat 대화 상대로 연결하기]로 다시 만들어 주세요.</p>`;
  return `<p class="hint">${saved}</p>`;
}

/* 학생과 Chat 처음 연결하기 (2026-10-29). Optional: never a wizard gate, never counted in
   effectiveChecks. Strings come from the bsv-korean-output-quality receipts. */
const SCC = {
  notConnected: "학생마다 짧은 인사를 한 번 보내 1:1 채팅을 열어요. 그다음부터 출결 서류 안내가 이 채팅으로 학생에게 가요.",
  precondition: "학생명단과 Google Chat을 연결하면 학생과 연결할 수 있어요.",
  inProgress: (done, total) => `학생에게 인사를 보내고 있어요. (${done}/${total}명)`,
  partial: (failed) => `아직 연결되지 않은 학생이 ${failed}명 있어요. [학생명단] 탭 C열의 이메일을 확인한 뒤 이 학생들만 다시 연결해 주세요.`,
  unknown: (n) => `${n}명은 인사가 전달됐는지 확인하지 못했어요. 다시 보내지 말고 Google Chat에서 학생이 받았는지 확인해 주세요.`,
  newStudents: (n) => `학생명단에 학생 ${n}명이 새로 추가됐어요. 새로 추가된 학생만 연결해 주세요.`,
  done: (total) => `학생 ${total}명 모두 연결됐어요.`,
  previewTitle: "보낼 인사말",
  previewHint: "보내기 전에 인사말을 고칠 수 있어요. 이미 연결된 학생에게는 다시 보내지 않아요.",
  recipients: (n) => `받는 학생 ${n}명`,
  noEmail: "[학생명단] 탭에 이 학생의 Google 이메일이 없어 인사를 보내지 않아요.",
  resultTitle: "인사 보내기 결과",
  resultSummary: (sent, failed) => `${sent}명에게 보냈어요. ${failed}명에게는 보내지 못했어요.`,
  resultUnknown: "인사가 전달됐는지 확인하지 못했어요. 다시 보내지 말고 Google Chat에서 학생이 받았는지 확인해 주세요.",
  confirm: (n) => `학생 ${n}명에게 환영 인사를 보내 Chat을 연결할까요?`,
  irreversible: "보낸 메시지는 되돌릴 수 없어요.",
  readFailed: "학생 연결 상태를 읽지 못했어요.",
};
let studentConnect = null;
let studentConnectVersion = 0;
function studentConnectReady(a) {
  return a.state === "ready" && !S.attendanceLoading && !S.attendanceReadFailed && S.chatStatusContext === chatReadContext()
    && S.chatStatus && typeof S.chatStatus === "object" && S.chatStatus.connected === true;
}
function loadStudentConnect(force) {
  const context = chatReadContext();
  if (!force && studentConnect?.context === context) return studentConnect.promise;
  const version = ++studentConnectVersion;
  const entry = studentConnect = { context, state: "loading", promise: null };
  entry.promise = call("student_chat_connect_status").then(data => {
    if (version !== studentConnectVersion || context !== chatReadContext()) return;
    studentConnect = { ...data, context, state: data?.state === "ready" ? "ready" : "unavailable", promise: entry.promise };
  }).catch(error => {
    console.error(error);
    if (version === studentConnectVersion && context === chatReadContext()) studentConnect = { context, state: "unavailable", promise: entry.promise };
  }).finally(() => { if (context === chatReadContext()) render(); });
  return entry.promise;
}
function studentChatConnectDialogHtml() {
  const dlg = S.studentConnectDialog;
  if (!dlg || dlg.kind !== "unknown") return "";
  const body = `<h3>결과를 확인하지 못한 메시지</h3><p>${esc(SCC.resultUnknown)}</p>
      <ul class="student-chat-list">${dlg.rows.map(row => `<li><span>${esc(row.label)}</span>
        <span class="chat-space-reload-status"><button class="btn-tonal" data-action="student-chat-received" data-key="${esc(row.key)}">받았어요</button>
        <button class="btn-tonal" data-action="student-chat-not-received" data-key="${esc(row.key)}">받지 못했어요 · 다시 보낼 목록에 넣기</button></span></li>`).join("")}</ul>`;
  return `<div class="attendance-update-dialog-overlay"><section class="attendance-update-dialog attendance-selection-dialog student-chat-dialog" role="dialog" aria-modal="true">
    ${body}<div class="attendance-update-dialog-actions"><button class="btn" data-action="student-chat-dialog-close">닫기</button></div></section></div>`;
}
bindActions({
  "student-chat-open": () => call("open_attendance_chat"),
  "student-chat-recheck": () => loadStudentConnect(true),
  "student-chat-dialog-close": () => { S.studentConnectDialog = null; render(); },
  "student-chat-unknown": () => {
    const a = S.attendance || {};
    const r = studentChatRowState(a);
    if (!r.unknown.length) return;
    S.studentConnectDialog = { kind: "unknown", context: chatReadContext(), rows: r.unknown };
    render();
  },
  "student-chat-received": (el) => resolveStudentConnect(el.dataset.key, true),
  "student-chat-not-received": (el) => resolveStudentConnect(el.dataset.key, false),
  // [학생 이메일 고치러 가기]: the roster editor with the rejected students' emails outlined.
  "student-email-fix": async () => {
    const d = studentConnect;
    if (d?.state !== "ready" || !(d.failed_labels || []).length) return;
    S.rosterEmailFix = { labels: new Set(d.failed_labels), originals: null, focusPending: true };
    if (S.mode === "wizard") { await goStepAsync(6); return; }
    await openCard("timetable");
    if (S.mode === "edit" && S.edit === "timetable" && isHomeroomTeacher()) { S.timetableTab = "roster"; render(); }
  },
  "student-chat-fixed-send": async () => {
    if (S.classChatStart || S.classGroupCreating) return;
    const context = chatReadContext();
    await loadStudentConnect(true);
    const d = studentConnect;
    if (context !== chatReadContext() || d?.state !== "ready" || !(d.fixed || []).length) return;
    if (!(await closeWindow(true))) return;
    attendanceChatSetupPending = { ids: d.fixed.map(row => row.studentId).filter(Boolean) };
    openAttendanceWindow();
  },
});
async function resolveStudentConnect(key, received) {
  const dlg = S.studentConnectDialog;
  if (!dlg || dlg.kind !== "unknown" || dlg.context !== chatReadContext()) return;
  await call("student_chat_connect_resolve", key, received);
  dlg.rows = dlg.rows.filter(row => row.key !== key);
  if (!dlg.rows.length) S.studentConnectDialog = null;
  await loadStudentConnect(true);
}
// Roster editor outline for rejected emails: pulse, then a steady outline until the email changes.
function rosterEmailFixActive(row) {
  const fix = S.rosterEmailFix;
  if (!fix) return false;
  const label = `${String(row[0] || "").trim()}${String(row[1] || "").trim()}`;
  if (!fix.labels.has(label)) return false;
  if (!fix.originals) return true;
  return !(label in fix.originals) || String(row[2] || "").trim() === fix.originals[label];
}
function rosterEmailFixAfterPaint() {
  const fix = S.rosterEmailFix;
  if (!fix || !S.rosterEditor?.rows) return;
  if (!fix.originals) {
    fix.originals = {};
    S.rosterEditor.rows.forEach(row => {
      const label = `${String(row[0] || "").trim()}${String(row[1] || "").trim()}`;
      if (fix.labels.has(label)) fix.originals[label] = String(row[2] || "").trim();
    });
  }
  if (!fix.focusPending) return;
  const first = document.querySelector(".roster-email-fix");
  if (!first) return;
  fix.focusPending = false;
  first.scrollIntoView({ block: "center" });
  first.focus({ preventScroll: true });
}
document.addEventListener("input", event => {
  const key = event.target.dataset?.rosterCell;
  if (!key || !S.rosterEmailFix || !S.rosterEditor) return;
  const [row, column] = key.split(":").map(Number);
  if (column !== 2) return;
  event.target.classList.toggle("roster-email-fix", rosterEmailFixActive(S.rosterEditor.rows[row] || []));
});

// [환영 인사 보내면서 Google Chat 대화 상대로 연결하기] dialog (CLASS-GROUP-01). Only the panes still needed
// are shown (2026-11-02). No counts and no emails on screen.
function classChatStartDialogHtml() {
  const dlg = S.classChatStart;
  if (!dlg) return "";
  const title = "환영 인사 보내면서 Google Chat 대화 상대로 연결하기";
  let body = "", actions = "";
  if (dlg.kind === "edit" || dlg.kind === "sending") {
    const sending = dlg.kind === "sending";
    const p = S.studentConnectProgress || { done: 0, total: 0 };
    const off = sending ? " disabled" : "";
    const panes = [
      dlg.showPersonal ? `<label class="class-chat-start-pane"><b>학생 각자에게 (1:1)</b>
          <textarea name="class-chat-personal-text" rows="8" aria-label="학생 각자에게 (1:1)"${off}>${esc(dlg.personalText)}</textarea></label>` : "",
      dlg.showGroup ? `<label class="class-chat-start-pane"><b>우리 반 단톡방</b>
          <textarea name="class-chat-group-text" rows="8" aria-label="우리 반 단톡방"${off}>${esc(dlg.groupText)}</textarea></label>` : "",
    ].filter(Boolean);
    body = `<h3>${title}</h3><p>학생들에게 첫 인사를 보내면 Google Chat으로 연락할 수 있어요.</p>
      <div class="class-chat-start-panes${panes.length === 1 ? " single" : ""}">${panes.join("")}</div>
      <p class="attendance-update-dialog-note">학생명단의 이메일이 틀리면 그 학생에게는 가지 않아요.</p>
      ${sending ? `<p class="hint">${p.total ? `학생에게 1:1 인사를 보내고 있어요. (${p.done}/${p.total}명)` : "인사를 보내고 있어요…"}</p>` : ""}`;
    actions = `<button class="btn-quiet" data-action="class-chat-start-close"${off}>취소</button>
      <button class="btn class-chat-start-send" data-action="class-chat-start-send"${off}>${sending ? "보내는 중…" : title}</button>`;
  } else if (dlg.kind === "result") {
    const d = dlg.data || {};
    const personal = d.personal || null;
    const results = personal?.results || [];
    const labels = rows => rows.map(row => row.label).join(", ");
    const sentPersonal = results.some(row => row.state === "sent");
    const rejected = results.filter(row => row.state === "rejected");
    const unknown = results.filter(row => ["unknown", "pending"].includes(row.state));
    const lines = [];
    const groupSent = d.group === "sent";
    if (groupSent && sentPersonal) lines.push(["ok", "✓ 단톡방과 1:1 채팅에 인사를 보냈어요."]);
    else if (groupSent) lines.push(["ok", "✓ 단톡방에 인사를 보냈어요."]);
    else if (sentPersonal) lines.push(["ok", "✓ 1:1 채팅에 인사를 보냈어요."]);
    if (d.group === "rejected") lines.push(["warn", "! 단톡방에 인사를 보내지 못했어요."]);
    else if (d.group === "stop") lines.push(["warn", "! Google Chat 연결을 확인해야 해서 단톡방과 1:1 인사를 보내지 않았어요."]);
    else if (d.group === "unknown" && !d.send_error) lines.push(["warn", "! 단톡방에 인사가 전달됐는지 확인하지 못했어요. 다시 보내지 말고 Google Chat에서 확인해 주세요."]);
    if (rejected.length) {
      lines.push(["warn", `이메일 때문에 받지 못한 학생: ${labels(rejected)}`]);
      lines.push(["warn", "이메일을 확인하고, 학생 계정에서 Google Chat 앱을 사용하게 해주세요."]);
    }
    if (unknown.length) lines.push(["warn", `! 전달됐는지 확인하지 못한 학생: ${labels(unknown)} — 다시 보내지 말고 Google Chat에서 받았는지 확인해 주세요.`]);
    if (personal?.stopped) lines.push(["warn", `! ${personal.stopped_detail || ""}`]);
    if (d.send_error) lines.push(["warn", `! ${d.send_error}`]);
    body = `<h3>${title}</h3>${d.space_name ? "<p>학급 단톡방을 만들었어요.</p>" : ""}
      ${lines.map(([tone, text]) => `<p class="class-chat-start-result ${tone}">${esc(text)}</p>`).join("")}`;
    actions = (rejected.length ? `<button class="btn-tonal" data-action="student-email-fix-from-result">학생 이메일 고치러 가기</button>` : "")
      + `<button class="btn" data-action="class-chat-start-close">닫기</button>`;
  }
  return `<div class="attendance-update-dialog-overlay"><section class="attendance-update-dialog attendance-selection-dialog student-chat-dialog class-chat-start-dialog" role="dialog" aria-modal="true">
    ${body}<div class="attendance-update-dialog-actions">${actions}</div></section></div>`;
}
document.addEventListener("input", (event) => {
  const dlg = S.classChatStart;
  if (dlg?.kind !== "edit") return;
  if (event.target.matches?.('textarea[name="class-chat-group-text"]')) dlg.groupText = event.target.value;
  if (event.target.matches?.('textarea[name="class-chat-personal-text"]')) dlg.personalText = event.target.value;
});
bindActions({
  "student-email-fix-from-result": async () => {
    S.classChatStart = null;
    await loadStudentConnect(true);
    await actions["student-email-fix"]();
  },
});

function attendanceViewKind() {
  // Explicit navigation context, independent of cloud setup completion.
  return S.mode === "wizard" ? "installation" : "management";
}
function hasCurrentFinalIssue(operations, states = ["failed", "needs_user"]) {
  if (problemIssueOwner !== screenKey() || !states.includes(S.problemIssue?.state)) return false;
  return operations.includes(String(S.problemIssue.operation || ""));
}
function openAttendancePictureGuide(kind) { return call("open_picture_guide", kind); }
bindActions({
  "google-login-guide": () => openAttendancePictureGuide("login"),
});
function attendanceComingSoonHtml() {
  return `<div class="attendance-head"><div>
    <h2>출결</h2>
    <p>출결 기능은 준비 중이며 곧 제공됩니다.</p>
  </div></div>`;
}
/* ---------- the year read, and step 8's `Google Tasks 목록` row ----------
   One /v1/data/read (MAIN §7.1) is shared by the Home tile, this tab and the attendance window's
   first paint. MAIN §7.14 names three rows for this tab: `결석신고서 서식` arrives with P4 and
   `학생 Google Chat 연결` with P3, so until then only the Tasks row is here. */
const STALE_TEXT = "다른 컴퓨터에서 먼저 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인한 뒤 다시 해 주세요.";
function attendanceUnknownText() {
  return `저장됐는지 확인하지 못했어요. 서버에 연결되면 ${connectionRefreshHint()} 눌러 확인해 주세요.`;
}
function dataFailureText(reply) {
  const code = String(reply?.body?.error || "");
  if (reply?.status === 401) return SESSION_ENDED_TEXT;
  if (code === "GOOGLE_PERMISSION_REQUIRED") return GRANT_MISSING_TEXT;
  if (code === "STALE") return STALE_TEXT;
  if (reply?.body?.message) return String(reply.body.message);
  if (!reply?.status || reply.status >= 500) return SERVER_UNREACHABLE_TEXT;
  return "저장하지 못했어요. 잠시 뒤 다시 해 주세요.";
}
let classYearVersion = 0;
let classYearPending = null;
// A read in flight when the roster was written answers from before the write, with no students. Drop it, and the share of it, so the next read is a new one (OBS-05).
function dropClassYearRead() {
  classYearVersion += 1;
  classYearPending = null;
}
function currentClassYear() {
  return S.classYear?.context === googleListContext() ? S.classYear : null;
}
function loadClassYear(force = false) {
  const context = googleListContext();
  if (!isGoogleReady(S.google)) return Promise.resolve(null);
  // The same read is shared, never started twice (OBS-05).
  if (classYearPending?.context === context) return classYearPending.promise;
  const held = currentClassYear();
  if (!force && held) return Promise.resolve(held);
  const version = ++classYearVersion;
  // A new read shows grey, never the last result as if it were current (C6, C8).
  S.classYear = { context, phase: "pending", answer: held?.answer || null, pendingWrite: held?.pendingWrite || null, notice: held?.notice || "",
    permissionMissing: held?.permissionMissing || false, taskState: "pending" };
  const current = () => version === classYearVersion && context === googleListContext();
  const pending = { context, promise: null };
  pending.promise = (async () => {
    try {
      const reply = await dataCall("/v1/data/read", {});
      if (!current()) return null;
      if (reply?.status === 200 && reply.body?.complete === true) {
        adoptClassYearAnswer(reply.body, context);
        loadClassChat(true).catch(reportBackgroundFailure);
        await checkClassTaskList(context);
      } else {
        S.classYear = { ...S.classYear, phase: "failed", detail: dataFailureText(reply) };
      }
    } catch (error) {
      if (error instanceof StaleAccountResponse || !current()) return null;
      console.error(error);
      S.classYear = { ...S.classYear, phase: "failed", detail: "확인하지 못했어요" };
    } finally {
      if (classYearPending === pending) classYearPending = null;
      if (current()) paintClassYear();
    }
    return S.classYear;
  })();
  classYearPending = pending;
  paintClassYear();
  return pending.promise;
}
function adoptClassYearAnswer(answer, context = googleListContext()) {
  const held = S.classYear?.context === context ? S.classYear : null;
  // A card read started before a confirmed settings write can finish afterward.
  // Complete the read without replacing that newer snapshot with the old version.
  if (Number.isInteger(held?.answer?.year) && held.answer.year === answer.year && Number.isInteger(held.answer.yearDoc?.version)
      && Number.isInteger(answer.yearDoc?.version) && answer.yearDoc.version < held.answer.yearDoc.version) answer = held.answer;
  S.classYear = { context, phase: "ready", answer, taskState: held?.answer?.yearDoc?.taskListId === answer.yearDoc?.taskListId ? held?.taskState : "",
    pendingWrite: held?.pendingWrite || null, notice: held?.notice || "", permissionMissing: held?.permissionMissing || false, stale: false };
}
// The saved list must open now: an old answer is not a current green (C6, C8).
async function checkClassTaskList(context) {
  const id = currentClassYear()?.answer?.yearDoc?.taskListId;
  if (!id) return;
  let state = "check_failed";
  try {
    const statuses = await call("google_target_statuses", { "담임안내Tasks목록ID": id });
    state = statuses?.["담임안내Tasks목록ID"]?.state || "check_failed";
  } catch (error) {
    if (error instanceof StaleAccountResponse) return;
    console.error(error);
  }
  if (context === googleListContext() && S.classYear?.context === context) S.classYear = { ...S.classYear, taskState: state };
}
// Only the row or tile that shows it changes: a background answer must not replace a form or
// a button under a click in progress (LOCK-01, LOCK-02).
function paintClassYear() {
  if (S.banner?.topic === "attendance-gate" && attendanceWizardGateOpen()) { S.banner = null; render(); return; }
  paintAttendanceTile();
  paintConnectionRefreshButton();
  const row = document.querySelector("#class-task-row");
  if (row && S.connectTab === "attendance") replaceIfChanged(row, classTaskRowHtml());
  if (S.mode === "wizard" && S.step === 8) {
    const next = document.querySelector('.foot [data-action="go-next"]');
    if (next && !hasPendingWizardNext()) next.disabled = S.connectTab === "attendance" && !attendanceWizardGateOpen();
    paintGateWhy();
  }
}
const classTaskField = "담임안내Tasks목록ID";
function classTaskRowHtml() {
  const year = currentClassYear();
  const chosen = String(S.draft.profile[classTaskField] || "").trim();
  const doc = year?.answer?.yearDoc || null;
  const linked = String(doc?.taskListId || "");
  const name = String(doc?.taskListTitle || S.draft.profile["담임안내Tasks목록이름"] || "");
  const unread = googleAuthCheckFailed(S.google);
  let tone = "muted", status = "확인 중…", button = "", note = "";
  if (S.classTaskSaving) status = "연결 중…";
  else if (unread) { status = "확인하지 못했어요"; note = S.google.server_unreachable ? SERVER_UNREACHABLE_TEXT : ""; }
  else if (!year || year.phase === "pending") status = "확인 중…";
  else if (year.pendingWrite) { tone = "warn"; status = "확인 필요"; note = attendanceUnknownText(); }
  else if (year.phase === "failed") { status = "확인하지 못했어요"; note = year.detail || ""; }
  else if (year.taskState === "pending") status = "확인 중…";
  else if (linked && (!chosen || linked === chosen) && year.taskState === "ready") { tone = "ok"; status = "연결됨"; }
  else if (linked && (!chosen || linked === chosen) && year.taskState !== "unavailable") { status = "확인하지 못했어요"; note = googleTargetCheckFailedMessage(); }
  else {
    tone = "warn"; status = "연결 필요";
    if (chosen) button = `<button class="btn-tonal" data-action="class-task-link">연결</button>`;
    else note = "Brity 메신저 탭에서 조종례 전달 Tasks 목록을 골라 주세요.";
    if (linked && year.taskState === "unavailable" && (!chosen || linked === chosen)) note = "저장된 할 일 목록을 사용할 수 없어요. 접근 권한을 확인하거나 다른 목록을 골라 주세요.";
  }
  const notice = year?.notice && !year.pendingWrite ? `<p class="hint attendance-chat-action-hint">${esc(year.notice)}</p>` : "";
  const grant = year?.permissionMissing
    ? `<button class="btn-tonal" data-action="reauthorize-google" data-busy-text="진행 중…">권한 다시 승인</button>` : "";
  return `<div class="svc-group" id="class-task-row" data-service="tasks">
    <div class="attendance-service">
      <img class="service-logo" src="assets/google-tasks.svg" alt="Google Tasks 로고">
      <span class="nameblock"><b>Google Tasks 목록</b><small>${esc(name || "조종례시 출결서류 미제출 안내")}</small></span>
      <span class="svc-acts">${button}${grant}</span>
      <span class="svc-status ${tone}">${esc(status)}</span>
    </div>${note ? `<p class="hint attendance-chat-action-hint">${esc(note)}</p>` : ""}${notice}</div>`;
}
let wizardAttendanceEntered = false;
function attendanceTabHtml() {
  if (!isHomeroomTeacher()) {
    return `<div class="attendance-head"><div><h2>출결</h2><p>담임을 맡지 않아 이 단계는 사용하지 않아요.</p></div></div>`;
  }
  const year = currentClassYear();
  const unchecked = year?.phase === "ready" && year.answer?.yearDoc?.taskListId && !year.taskState;
  // One forced read per entry to step 8's tab, shared when one is already running (OBS-05); a repaint does not read again. The gate then never judges from an earlier step's read.
  const entry = S.mode === "wizard" && S.step === 8 && !wizardAttendanceEntered && isGoogleReady(S.google);
  if (entry) wizardAttendanceEntered = true;
  if (entry || (!connectionRefreshActive() && isGoogleReady(S.google) && (!year || unchecked))) loadClassYear(true);
  const account = S.google?.user ? `<span class="account-chip">${esc(S.google.user)}</span>` : "";
  return `<div class="attendance-head"><div><h2>출결 업무에 필요한 Google 항목</h2></div>
      <span class="attendance-head-right">${account}</span></div>
    ${classTaskRowHtml()}${S.mode === "edit" ? `<div id="attendance-card"></div>` : ""}`;
}
// One press, one request ID. A lost answer keeps the ID and locks the row until ↻ (G4).
async function sendClassTaskList(body) {
  const context = googleListContext();
  S.classTaskSaving = true;
  try {
    paintClassYear();
    const reply = await dataCall("/v1/data/settings", body);
    if (context !== googleListContext()) return;
    if (reply?.unknown) {
      S.classYear = { ...S.classYear, phase: "ready", pendingWrite: body, notice: "" };
      return;
    }
    S.classYear = { ...S.classYear, pendingWrite: null, permissionMissing: false,
      notice: reply?.status === 200 ? "" : classTaskFailureText(reply) };
    if (reply?.body?.error === "GOOGLE_PERMISSION_REQUIRED") S.classYear.permissionMissing = true;
  } finally {
    S.classTaskSaving = false;
    // A read that started before this write cannot show its result.
    if (classYearPending) await classYearPending.promise;
    if (context === googleListContext()) await loadClassYear(true);
  }
}
function classTaskFailureText(reply) {
  const code = String(reply?.body?.error || "");
  if (code === "TARGET_UNAVAILABLE") return "저장된 할 일 목록을 사용할 수 없어요. 접근 권한을 확인하거나 다른 목록을 골라 주세요.";
  if (code === "GOOGLE_LIST_UNAVAILABLE") return "목록을 불러오지 못했어요";
  return dataFailureText(reply);
}
bindActions({
  "class-task-link": async () => {
    let year = currentClassYear();
    const chosen = String(S.draft.profile[classTaskField] || "").trim();
    if (!chosen || S.classTaskSaving || year?.pendingWrite) return;
    if (!year?.answer?.yearDoc) {
      // The year can be missing here after 담임 여부 changed: it is made once, then read.
      const problem = await ensureYearForProfile();
      if (problem) { setBanner("warn", problem); return; }
      await loadClassYear(true);
      year = currentClassYear();
      if (!year?.answer?.yearDoc) return;
    }
    await sendClassTaskList({ requestId: newRequestId(), version: year.answer.yearDoc.version, taskListId: chosen });
  },
});
// ↻ first replays a locked request once with the same ID, then reads (G4).
async function refreshClassYear() {
  const held = currentClassYear();
  if (held?.pendingWrite) { await sendClassTaskList(held.pendingWrite); return; }
  await loadClassYear(true);
}
bindActions({
  "connect-tab": (el) => {
    if (attendanceCardMount && !attendanceCardMount.canLeave()) return;
    const tab = el.dataset.tab;
    if (S.connectTab === tab) return;
    if (editingCard() === "connect" && S.connectTab === "messenger") syncConnectFields();
    S.banner = null;  // 이전 탭의 안내 배너(게이트 포함)가 새 탭까지 따라가지 않는다 (검토 C2)
    S.connectTab = tab;
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
  // [환영 인사 보내면서 Google Chat 대화 상대로 연결하기] (2026-10-30, CLASS-GROUP-01): one dialog with the 1:1 greeting
  // and the group greeting side by side. The send button creates the group chat, sends the
  // group greeting once and the 1:1 greeting once per student; nothing is resent on its own (C7).
  // [환영 인사 보내면서 Google Chat 대화 상대로 연결하기] (CLASS-GROUP-01): the dialog shows only the panes still
  // needed (2026-11-02): the group pane while the room or its greeting is missing, the 1:1 pane
  // while some students were never greeted. Nothing is resent on its own (C7).
  "class-space-group-create": async () => {
    if (S.classGroupCreating || S.classSpaceSaving || S.classChatStart) return;
    if (!(await closeWindow(true))) return;
    attendanceChatSetupPending = true;
    openAttendanceWindow();
  },
  "class-chat-start-close": () => { if (S.classChatStart?.kind !== "sending") { S.classChatStart = null; render(); } },
  "class-chat-start-send": async () => {
    const dlg = S.classChatStart;
    if (!dlg || dlg.kind !== "edit" || S.classGroupCreating || dlg.context !== chatReadContext()) return;
    const groupBox = document.querySelector('textarea[name="class-chat-group-text"]');
    const personalBox = document.querySelector('textarea[name="class-chat-personal-text"]');
    const groupText = dlg.showGroup ? (groupBox ? groupBox.value : dlg.groupText).trim() : "";
    const personalText = dlg.showPersonal ? (personalBox ? personalBox.value : dlg.personalText).trim() : "";
    if ((dlg.showGroup && !groupText) || (dlg.showPersonal && !personalText)) return;
    const context = dlg.context;
    S.classChatStart = { ...dlg, kind: "sending", groupText, personalText };
    S.classGroupCreating = true;
    S.studentConnectProgress = { done: 0, total: 0 };
    const timer = setInterval(() => {
      call("student_chat_connect_progress").then(p => {
        if (S.classChatStart?.kind === "sending" && p?.total) { S.studentConnectProgress = p; render(); }
      }).catch(() => {});
    }, 1500);
    let data = null;
    try {
      render();
      data = await call("attendance_chat_start", dlg.count, groupText, personalText, dlg.keys || [], Boolean(dlg.useExisting));
    } catch (error) {
      // Unknown outcome: re-read the state, never resend on our own (C7).
      if (S.classChatStart?.kind === "sending") S.classChatStart = null;
      studentConnect = null;
      S.chatSpaces = undefined;
      throw error;
    } finally {
      clearInterval(timer);
      S.classGroupCreating = false;
      render();
    }
    if (context !== chatReadContext()) { S.classChatStart = null; render(); return; }
    if (data?.state === "ok") {
      if (data.space_name) {
        S.chatSpaceName = data.display_name;
        S.chatSpaces = undefined;   // 방금 만든 그룹 채팅이 목록에 잡히게 다시 읽는다
        if (S.chatStatus && typeof S.chatStatus === "object") {
          S.chatStatus.class_space_id = data.space_name;
          S.chatStatus.class_space_name = data.display_name;
        }
      }
      studentConnect = null;      // 결과는 이 PC 기록에서 다시 읽는다
      S.classChatStart = { kind: "result", data };
    } else {
      // A failed or unknown create is never sent again automatically (C7).
      if (data?.space_name) S.chatSpaces = undefined;
      S.classChatStart = null;
      setBanner("warn", data?.detail || "그룹 채팅을 만들지 못했어요.");
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
});
// Setup step 8 has no title bar: the same ↻ refresh sits at the top right of its header.
// The 연결 window drops this <h1> and keeps its own title-bar ↻.
function wizardConnectHead(title) {
  return S.mode === "wizard"
    ? `<div class="wizard-head"><h1>${esc(title)}</h1>${connectionRefreshButtonHtml()}</div>` : `<h1>${esc(title)}</h1>`;
}
function stepConnect() {
  if (!S.google) {
    const request = beginIssueRequest(false);
    call("google_status")
      .then((data) => { if (ownsIssueRequest(request)) { adoptGoogleStatus(data); render(); } })
      .catch((error) => handleCaughtError(error, request));
    return `${wizardConnectHead("연결")}<p class="sub">상태를 확인하는 중이에요…</p>`;
  }
  if (isGoeduGoogleStatus(S.google) && !isGoogleReady(S.google)) {
    return `${wizardConnectHead("Google 연결")}<div class="banner warn"><span>${esc(googleAuthorizationMessage(S.google))}</span>
      <button class="btn-tonal" data-action="reauthorize-google">권한 다시 승인</button></div>`;
  }
  if (!isGoeduGoogleStatus(S.google) && !googleAuthCheckFailed(S.google)) {
    const message = S.google.logged_in ? GOEDU_REQUIRED_MESSAGE : FIELD_MESSAGES["google-login"];
    return `${wizardConnectHead("Google 연결")}<div class="banner warn"><span>${esc(message)}</span>
      <button class="btn-quiet" data-action="goto-settings">Google 로그인 열기</button></div>
      <p class="sub">Calendar·Tasks·Docs·Chat 작업은 Google 계정과 필요한 권한을 확인한 뒤 시작해요.</p>`;
  }
  if (!connectionRefreshActive() && S.connectTab === "messenger" && !S.listsLoaded && !S.linkLoading && !S.listsError &&
      (linkModes().cal === "existing" || linkModes().task === "existing")) {
    loadLinkLists();
  }
  const body = S.connectTab === "attendance" ? (attendanceUiEnabled() ? attendanceTabHtml() : attendanceComingSoonHtml())
    : messengerTabHtml();
  return `
    ${wizardConnectHead("Google 연결")}
    <p class="sub">Google 서비스를 연결하여 학교 업무를 자동화해요.</p>
    ${connectTabsHtml()}
    ${body}`;
}
async function validateConnect() {
  if (!S.google) adoptGoogleStatus(await call("google_status"));
  if (S.google.local_settings_error) return S.google.local_settings_error;
  if (googleAuthCheckFailed(S.google)) return S.google.server_unreachable ? SERVER_UNREACHABLE_TEXT : GOOGLE_AUTH_CHECK_MESSAGE;
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
    if (messengerIssue && S.connectTab !== "messenger") S.connectTab = "messenger";
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
    const nextGoogle = options.googleStatus || await readGoogleStatus();
    if (!ownsIssueRequest(owner)) return false;
    if (options.loginEpoch !== undefined && options.loginEpoch !== googleLoginEpoch) return false;
    if (options.loginComplete) S.login = null;
    adoptGoogleStatus(nextGoogle);
    paintSettingsReadiness();
    if (inSettingsWindow() && isGoogleReady(S.google)) loadDevices().catch(reportBackgroundFailure);
    const context = googleReadContext();
    const current = () => ownsIssueRequest(owner) && context === googleReadContext();
    const computer = await call("computer_status");
    if (!current()) return false;
    S.computer = computer;
    paintSettingsReadiness();
    if (isGoogleReady(S.google)) {
      try {
        await loadLinkLists(true, current);
      } catch (error) {
        if (!current()) return false;
        // List failures belong to their current read, independently of login.
        S.listsError = true;
      }
    } else if (!googleAuthCheckFailed(S.google)) {
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
    paintSettingsRefreshButton();
    if (isGoogleReady(S.google)) scheduleGoogleRecovery();
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
  const noun = pending.kind === "calendar" ? "캘린더" : "할 일 목록";
  let result;
  try {
    result = await call(apiName, name);
    if (result?.state === "confirm-required") {
      // The hold has passed and Google still does not list it: one new attempt, only on the
      // teacher's answer, never by itself (C7, SIGN-IN).
      if (!window.confirm(`앞서 만든 ${noun} "${name}"을 Google에서 찾지 못했어요. 새로 만들까요?`)) {
        throw new Error(`${noun}을 새로 만들지 않았어요.`);
      }
      result = await call(apiName, name, true);
    }
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
  if (result?.state === "pending" || result?.state === "confirm-required") {
    const seconds = Number(result.retry_after);
    throw new Error(`${noun}을 만드는 중일 수 있어요. ${Number.isFinite(seconds) && seconds > 0 ? `${Math.ceil(seconds)}초` : "잠시"} 뒤 다시 눌러 주세요.`);
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
// The settings window has no in-window [다시 점검]; name its title-bar ↻ instead.
const GOOGLE_AUTH_CHECK_MESSAGE_SETTINGS = "Google 계정 상태 확인을 마치지 못했어요. 창 위쪽 [다시 점검] 버튼(↻)을 눌러 주세요. 로그인이 해제된 것으로 판단하지 않았습니다.";
function inSettingsWindow() { return S.mode === "edit" && S.edit === "settings"; }
function googleAuthCheckMessage() { return inSettingsWindow() ? GOOGLE_AUTH_CHECK_MESSAGE_SETTINGS : GOOGLE_AUTH_CHECK_MESSAGE; }
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
// A failed check says nothing about the sign-in (LOGIN-17): name what is known, in grey.
const SERVER_UNREACHABLE_TEXT = "서버에 연결하지 못했어요";
function googleUnreadText(g = S.google) {
  return g?.server_unreachable ? SERVER_UNREACHABLE_TEXT : googleAuthCheckMessage();
}
function googleAuthCheckFailed(g) {
  return Boolean(g && (g.login_state === "error" || g.error_code === "GWS_AUTH_STATUS_FAILED" || g.authorization_state === "check_failed"));
}
// Wizard screens (steps 2 and 7) and Home keep this text button; the settings window has
// only the title-bar ↻ (settingsRefreshButtonHtml), like the 연결 window.
function settingsRecheckButtonHtml() {
  return `<button class="btn-quiet" data-action="settings-refresh" data-busy-text="점검 중…">다시 점검</button>`;
}
let settingsRefreshRunning = false;
function settingsRefreshButtonHtml() {
  const busy = settingsRefreshRunning || hasCurrentSettingsStatusRequest();
  const label = busy ? "점검 중…" : "다시 점검";
  return `<button type="button" class="win-refresh${busy ? " checking" : ""}" data-action="settings-refresh"
    title="${label}" aria-label="${label}" aria-busy="${busy}"${busy ? " disabled" : ""}>
    ${icon("refresh", "small")}</button>`;
}
function paintSettingsRefreshButton() {
  const button = document.querySelector('.win-head [data-action="settings-refresh"]');
  if (button) button.outerHTML = settingsRefreshButtonHtml();
}
function computerSectionHtml(includeGoogle) {
  const c = S.computer;
  const rows = c
    ? readinessRow("프로그램 실행 기능", "Teacher Manager를 실행해요", c.python)
      + readinessRow("문서 읽기 기능", "PDF와 첨부 문서를 읽어요", c.documents)
      + readinessRow("화면 표시 기능", "Teacher Manager 화면을 보여줘요", c.screen)
    : `<div class="row"><span class="st">준비 상태를 확인하는 중이에요…</span></div>`;
  return `<div class="section-h section-head"><span>컴퓨터 준비</span>${inSettingsWindow() ? "" : settingsRecheckButtonHtml()}</div>
    <div class="panel">${rows}</div>
    ${includeGoogle ? googleAccountSectionHtml(false) + devicesSectionHtml() + settingsCheckRowsHtml() : ""}`;
}
// Home counts every failing settings check; the settings window shows the same rows with an
// action that exists here (R5-3). The Google rows are already drawn above.
function settingsCheckRowsHtml() {
  const rows = effectiveChecks().filter(row => row.card === "settings" && row.ok === false
    && !["settings.google-login", "settings.goedu-account", "settings.grant"].includes(row.key));
  if (!rows.length) return "";
  return `<div class="section-h">확인이 필요한 항목</div><div class="panel">${rows.map(row => {
    const action = row.key === "settings.helper"
      ? `<button class="btn-tonal" data-action="helper-restart" data-busy-text="켜는 중…">메신저 단축키 다시 켜기</button>` : "";
    return `<div class="row" data-settings-check="${esc(row.key)}"><span class="nameblock"><b>${esc(row.label)}</b><small>${esc(row.detail || "")}</small></span><span class="row-actions"><span class="st warn">확인 필요</span>${action}</span></div>`;
  }).join("")}</div>`;
}
bindActions({
  "helper-restart": async () => {
    // The verified restart (stop, then at most three starts) used by the settings auto-save.
    await call("restart_helper");
    S.helperRestartPending = false;
    invalidateCardChecks("settings");
    await refreshChecks();
    paintSettingsReadiness();
  },
});
function paintGoogleObservation() {
  // A background identity check must not replace an open form's unsaved input.
  const background = (S.mode === "edit" && S.edit !== "settings") || S.mode === "about" || S.mode === "attendance"
    ? document.querySelector("#app > .body") : null;
  if (background && !accountProfileNeedsReload) {
    background.outerHTML = homeHtml(true);
    updateTabBadges();
  } else paintSettingsReadiness();
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
    paintSettingsRefreshButton();
    const background = document.querySelector("#app > .body");
    if (background) background.outerHTML = homeHtml(true);
  } else render();
}

function googleLoginRowsHtml() {
  const g = S.google;
  const row = (right, problem) => `<div class="row${problem ? " problem-row" : ""}"><span class="nameblock"><b>Google 로그인</b></span><span class="row-actions">${right}</span></div>`;
  if (!g) return row(`<span class="st">확인 중이에요…</span>`);
  const loginError = fieldError("google-login");
  const errorNote = loginError ? `<span class="field-error">${esc(loginError)}</span>` : "";
  const logoutButton = `<button class="btn-quiet" data-action="gws-logout">로그아웃</button>`;
  const loginButton = label => `<button class="btn-tonal" data-action="gws-login" data-busy-text="진행 중…">${label}</button>`;
  if (S.login) return row(`<span class="st">${S.login.phase === "redeeming" ? "로그인 확인 중…" : "진행 중…"}</span>`);
  if (googleObservationPending()) return row(`<span class="st">확인 중…</span>${g.logged_in ? logoutButton : ""}`);
  // The server did not answer the start of a sign-in: nothing changed, here or there.
  if (S.loginUnreachable) return row(`<span class="st">${SERVER_UNREACHABLE_TEXT}</span>${loginButton("다시 시도")}`);
  if (googleAuthCheckFailed(g)) {
    // Keep the way to sign in open: a check that failed is not a logout (LOGIN-18).
    return row(`<span class="st warn">${esc(googleUnreadText(g))}</span>${inSettingsWindow() ? "" : settingsRecheckButtonHtml()}${loginButton("Google로 로그인")}`, Boolean(loginError));
  }
  if (g.session_ended) return row(`<span class="st act">${esc(SESSION_ENDED_TEXT)}</span>${loginButton("다시 로그인")}`);
  if (isGoogleReady(g)) return row(`<span class="st ok">${esc(g.user || "완료")}</span>${logoutButton}`) + googleGrantRowHtml(g);
  return row(`${errorNote}${loginButton("Google로 로그인")}`, Boolean(loginError));
}
const SESSION_ENDED_TEXT = "Teacher Manager에 다시 로그인해 주세요.";
const GRANT_MISSING_TEXT = "Google 권한을 다시 승인해 주세요.";
// Declined scopes do not stop a sign-in: a missing capability asks here (SIGN-IN).
function grantNeedsApproval(g = S.google) {
  return isGoogleReady(g) && ["partial", "refresh-required"].includes(g.grant?.state);
}
function googleGrantRowHtml(g) {
  const name = `<span class="nameblock"><b>Google 권한</b></span>`;
  if (grantNeedsApproval(g)) {
    return `<div class="row">${name}<span class="row-actions"><span class="st act">${esc(GRANT_MISSING_TEXT)}</span><button class="btn-tonal" data-action="reauthorize-google" data-busy-text="진행 중…">권한 다시 승인</button></span></div>`;
  }
  return g.grant?.state === "check-failed"
    ? `<div class="row">${name}<span class="row-actions"><span class="st">확인하지 못했어요</span></span></div>` : "";
}
function loginWaitHtml() {
  if (!S.login) return "";
  if (S.login.logging_out) return '<p class="sub">Teacher Manager에서 로그아웃하고 있어요…</p>';
  if (S.login.phase === "redeeming") return `<div class="panel" style="margin-top:12px"><p class="sub" style="margin:0">로그인 확인 중…</p></div>`;
  const url = String(S.login.url || "");
  const browserMessage = !url
    ? "로그인 주소를 준비하는 중이에요…"
    : S.login.browser_opened
      ? "브라우저가 자동으로 열렸어요. 창이 보이지 않으면 아래 주소를 다시 열어 주세요."
      : "브라우저를 자동으로 열지 못했어요. 아래 주소를 직접 열거나 복사해 주세요.";
  // Shown when the browser could not reach this PC: the page keeps the code for this field.
  const codeRow = S.login.code_entry ? `<div class="login-code">
      <label for="login-code">코드 입력</label>
      <input id="login-code" name="login-code" autocomplete="off" spellcheck="false" value="${esc(S.loginCode)}"${S.login.code_error ? ' aria-invalid="true"' : ""}>
      <button class="btn-tonal" data-action="login-code-submit">확인</button>
      ${S.login.code_error ? `<span class="field-error">코드가 맞지 않아요. 브라우저에 보이는 코드를 다시 붙여 넣어 주세요.</span>` : ""}
    </div>` : "";
  return `<div class="panel" style="margin-top:12px">
      <p class="sub" style="margin:0 0 8px">${browserMessage}</p>
      ${url ? linkRow(url) : ""}
      ${codeRow}
      <div class="action-line"><button class="btn-quiet" data-action="login-cancel">취소</button></div>
    </div>`;
}
/* ---------- 로그인한 컴퓨터 (SCREENS, SIGN-IN 'Sessions, devices') ----------
   These end Teacher Manager sessions only and never call Google (SIGNIN-02). Read when 설정 opens
   and on its ↻, never from a paint. A failed read is grey, never an empty list. */
let devicesReadVersion = 0;
async function loadDevices() {
  const context = googleListContext();
  const version = ++devicesReadVersion;
  S.devices = { context, phase: "pending", rows: S.devices?.context === context ? S.devices.rows : [] };
  paintDevices();
  let reply = null;
  try { reply = await dataCall("/v1/me/devices"); } catch (error) {
    if (error instanceof StaleAccountResponse) return;
    console.error(error);
  }
  if (version !== devicesReadVersion || context !== googleListContext()) return;
  const rows = reply?.status === 200 && Array.isArray(reply.body?.devices) ? reply.body.devices : null;
  S.devices = rows ? { context, phase: "ready", rows } : { context, phase: "failed", rows: [] };
  paintDevices();
}
function paintDevices() {
  const section = document.querySelector("#devices-section");
  if (section) replaceIfChanged(section, devicesSectionHtml());
}
function deviceLastUsedText(value) {
  const at = new Date(value);
  if (!value || Number.isNaN(at.getTime())) return "";
  return `마지막 사용 ${at.getMonth() + 1}월 ${at.getDate()}일 ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}
function devicesSectionHtml() {
  if (!inSettingsWindow() || !isGoogleReady(S.google)) return "";
  const read = S.devices?.context === googleListContext() ? S.devices : null;
  const section = inner => `<div id="devices-section"><div class="section-h">로그인한 컴퓨터</div><div class="panel">${inner}</div></div>`;
  if (read?.phase === "failed") return section(`<div class="row"><span class="st">확인하지 못했어요</span></div>`);
  if (!read?.rows.length) return section(`<div class="row"><span class="st">확인 중…</span></div>`);
  const rows = read.rows.map(row => {
    const note = [row.thisPc ? "이 컴퓨터" : "", deviceLastUsedText(row.lastUsedAt), row.appVersion ? `v${row.appVersion}` : ""].filter(Boolean).join(" · ");
    const action = row.current ? "gws-logout" : "device-logout";
    return `<div class="row"><span class="nameblock"><b>${esc(row.deviceLabel || "이름 없는 컴퓨터")}</b><small>${esc(note)}</small></span>
      <span class="row-actions"><button class="btn-quiet" data-action="${action}" data-family="${esc(row.familyId)}" data-busy-text="진행 중…"${S.devicesBusy ? " disabled" : ""}>이 컴퓨터 로그아웃</button></span></div>`;
  }).join("");
  const others = read.rows.length > 1
    ? `<div class="action-line"><button class="btn-quiet" data-action="devices-logout-others" data-busy-text="진행 중…"${S.devicesBusy ? " disabled" : ""}>다른 모든 컴퓨터에서 로그아웃</button></div>` : "";
  return section(rows + others);
}
// One press sends once. Whatever came back, the list is read again and shows what is true now.
async function endDeviceSessions(route) {
  S.devicesBusy = true;
  try {
    if (S.banner?.topic === "devices") setBanner("", "");
    paintDevices();
    const reply = await dataCall(route, {});
    if (reply?.status !== 200 && reply?.status !== 401 && inSettingsWindow()) setBanner("warn", dataFailureText(reply), "devices");
  } finally {
    S.devicesBusy = false;
    await loadDevices();
  }
}
bindActions({
  "device-logout": el => endDeviceSessions(`/v1/me/devices/${el.dataset.family}/revoke`),
  "devices-logout-others": () => endDeviceSessions("/v1/me/devices/revoke-others"),
});
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
  if (googleObservationPending()) return `<div class="account-box">
    <span class="avatar">${accountAvatar(g.user)}</span>
    <span class="account-copy"><span>마지막으로 확인한 계정</span><b>${esc(g.user)}</b></span>
    <span class="account-state st">확인 중…</span></div>`;
  const allowed = isGoeduGoogleStatus(g);
  const ready = isGoogleReady(g);
  const checkFailed = googleAuthCheckFailed(g);
  const label = checkFailed ? "계정 상태 확인 필요" : !allowed ? "사용할 수 없음" : ready ? "확인 완료" : "다시 승인 필요";
  const account = `<div class="account-box">
    <span class="avatar${ready ? " good" : ""}">${accountAvatar(g.user)}</span>
    <span class="account-copy"><span>${checkFailed ? "마지막으로 확인한 계정" : "현재 선택한 계정"}</span><b>${esc(g.user || "계정 확인 필요")}</b></span>
    <span class="account-state ${ready ? "good" : "bad"}">${label}</span>
  </div>`;
  if (checkFailed) return `${account}<div class="banner warn">${esc(googleAuthCheckMessage())}</div>`;
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
    <div class="google-account-actions">${includeRefresh ? settingsRecheckButtonHtml() : ""}</div></div>
    <div class="panel">${googleLoginRowsHtml()}<p class="attendance-picture-guide"><button type="button" class="text-link" data-action="google-login-guide" data-preserve-issue="true">Google 로그인 방법 안내</button></p></div>
    ${googleAccountDecisionHtml()}
    ${loginWaitHtml()}`;
}
async function refreshComputerStatus() {
  S.computer = await call("computer_status");
  paintSettingsReadiness();
}
function ensureComputerStatus() {
  if ((S.computer && S.google) || S.computerLoading || hasCurrentSettingsStatusRequest()) return;
  S.computerLoading = true;
  const request = beginIssueRequest(false);
  Promise.all([
    S.computer ? Promise.resolve(S.computer) : call("computer_status"),
    S.google ? Promise.resolve(S.google) : call("google_status"),
  ])
    .then(([computer, google]) => {
      if (!ownsIssueRequest(request)) return;
      S.computer = computer; adoptGoogleStatus(google);
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
    : g?.session_ended ? SESSION_ENDED_TEXT
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
      ? (g.server_unreachable ? `${SERVER_UNREACHABLE_TEXT}. 로그인이 해제된 것으로 판단하지 않았습니다.` : "다시 점검을 눌러 현재 Google 계정 상태를 확인해 주세요.")
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
  if (googleAuthCheckFailed(S.google)) return S.google.server_unreachable ? SERVER_UNREACHABLE_TEXT : GOOGLE_AUTH_CHECK_MESSAGE;
  if (S.google.session_ended) return SESSION_ENDED_TEXT;
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
  "settings-refresh": async (_el, request) => {
    settingsRefreshRunning = true;
    paintSettingsRefreshButton();
    try {
      const done = await refreshSettingsStatus(request);
      // The Helper row lives in home_checks; re-read it or a stopped Helper stays `정상` (R6-4).
      invalidateCardChecks("settings");
      await refreshChecks();
      paintSettingsReadiness();
      return done;
    } finally {
      settingsRefreshRunning = false;
      paintSettingsRefreshButton();
    }
  },
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
    // Show which HTTP answer Google gave so a busy service is not mistaken for a bad key.
    const http = r.status !== "ok" && /^http \d{3}( [A-Z_]+)?$/.test(r.detail || "") ? ` (Google 응답: HTTP ${r.detail.slice(5)})` : "";
    S.keyStatus = { kind, text: (KEY_MESSAGES[r.status] || r.status) + http };
    render();
  },
});

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
      ${summaryRow("Gemini 연결 API 키", S.draft.bridge.gemini_api_key ? "입력됨" : "나중에 (홈에서 안내)")}
      ${summaryRow("단축키", S.draft.bridge.hotkey || DEFAULT_HOTKEY)}
    </div>
    <div class="foot">
      <button class="btn-prev" data-action="go-prev">${icon("chevron-left", "small")} 이전</button>
      <button class="btn" data-action="apply-all" data-busy-text="저장하는 중… (1~2분 걸릴 수 있어요)"${applyAllPending ? " disabled" : ""}>모두 저장</button>
    </div>`;
};
// Step 9 has no footer, so every repaint re-created an enabled [모두 저장] while the
// gate check ran; one run at a time, and a stale gate result stays off other screens (OBS-17).
let applyAllPending = false;
bindActions({
  "apply-all": async () => {
    if (applyAllPending) return;
    applyAllPending = true;
    try { await applyAllOnce(); } finally {
      applyAllPending = false;
      if (S.mode === "wizard" && S.step === WIZARD_STEPS.length) render();
    }
  },
});
async function applyAllOnce() {
    const owner = screenKey();
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
      if (owner !== screenKey()) return;
      const message = attendanceWizardGateMessage();
      S.connectTab = "attendance";
      await goStepAsync(8);
      setAttendanceGateBanner(message);
      return;
    }
    await ensureGridLoaded();
    const bridgeToSave = {...S.draft.bridge};
    if (bridgeToSave.gemini_api_key === "") delete bridgeToSave.gemini_api_key;
    const results = await call("apply_all", S.draft.profile, S.draft.grid, bridgeToSave);
    const failed = results.filter((r) => r.status === "failed");
    if (isHomeroomTeacher()) {
      // Sets intakeStartedAt once; a second PC's wizard changes nothing. It stays on this step
      // when it fails, and pressing again repeats nothing that matters.
      const finished = await dataCall("/v1/data/years/finish-setup", { requestId: newRequestId() });
      if (owner !== screenKey()) return;
      if (finished?.status !== 200) {
        setBanner(finished?.unknown || !finished?.status || finished.status >= 500 ? "muted" : "warn", dataFailureText(finished));
        return;
      }
    }
    // 성공이든 실패든 곧장 홈으로 — 실패 항목은 홈 점검이 이유를 보여준다.
    await call("finish_setup");
    S.connectTab = "messenger";
    S.mode = "home";
    S.checks = [];
    S.applyResults = null;
    S.firstHomeNotice = failed.length === 0;
    showToast(failed.length
      ? "일부 항목을 준비하지 못했어요 — 홈에서 확인해 주세요"
      : "설정을 모두 저장했어요");
    render();
}
bindActions({
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
        rows.push({...issue(key, field, read.phase === "pending" ? "현재 목록을 확인하고 있어요."
          : read.phase === "failed" && S.listsPermissionMissing ? GRANT_MISSING_TEXT
          : `저장된 선택을 현재 목록에서 확인하지 못했어요. ${connectionRefreshHint()} 눌러 주세요.`, "messenger"),
          unread: read.phase === "failed"});
      }
    }
    for (const [field, status] of Object.entries(S.googleTargetStatuses || {})) {
      if (!GOOGLE_TARGET_ID_FIELDS.includes(field) || status.id !== p[field] || !p[field]) continue;
      if (field === "담임안내Tasks목록ID" && !homeroom) continue;
      if (modes[field.includes("Tasks") ? "task" : "cal"] !== "existing") continue;
      if (["unavailable", "check_failed"].includes(status.state)) {
        const key = {"업무캘린더ID":"connect.work-calendar", "학사일정캘린더ID":"connect.school-calendar", "업무Tasks목록ID":"connect.work-tasks", "담임안내Tasks목록ID":"connect.homeroom-tasks"}[field];
        const detail = status.state === "check_failed" ? googleTargetCheckFailedMessage() : status.detail;
        rows.push({...issue(key, field, detail, "messenger"), optional: status.state === "check_failed", unread: status.state === "check_failed"});
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
      // A restarted Home has an empty draft (finish_setup clears it): keep the saved check.
      const selected = S.draft.profile[row.target];
      if (selected && read?.context === googleListContext()) {
        const current = currentConnections.find(issue => issue.target === row.target);
        if (current) return {...row, ok: current.pending || current.unread ? null : false, pending: Boolean(current.pending), unread: Boolean(current.unread),
          detail: current.message, fix: current.pending || current.unread ? "" : current.message};
        const target = S.googleTargetStatuses?.[row.target];
        if (target?.id === selected && target.state === "ready") {
          return {...row, ok: true, pending: false, unread: false, detail: target.detail || "연결됨", fix: ""};
        }
      }
    }
    return row;
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
    key: row.key, label: row.target, ok: row.pending || row.unread ? null : false, pending: Boolean(row.pending), unread: Boolean(row.unread),
    detail: row.pending || row.unread ? row.message : "", fix: row.pending || row.unread ? "" : row.message,
    card: editing || "", tab: row.tab || "", target: row.target,
  }));
  const rows = uniqueChecks(kept.concat(live)).filter(row => row.key !== "connect.class-space");
  if (attendanceUiEnabled() && isHomeroomTeacher()) {
    const state = classRoomReadiness(), pending = state === "loading";
    const unread = ["unverified", "read-failed", "account-required"].includes(state);
    const detail = classRoomReadinessMessage(state);
    rows.push({key:"connect.class-space", label:"학급 단톡방", card:"connect", tab:"attendance", target:"class-space",
      ok:pending || unread ? null : state === "ready", pending, unread, detail, fix:pending || unread || state === "ready" ? "" : detail});
  }
  if (connectionRefreshActive()) rows.push({key:"connect.refresh", label:"연결", card:"connect",
    tab:"", target:"", ok:null, pending:true, detail:"연결 확인 중…", fix:""});
  if (googleObservationPending()) {
    const pendingRows = rows.map(row => row.card === "connect" || row.target === "google-login"
      ? {...row, ok:null, pending:true, detail:"확인 중…", fix:""} : row);
    if (!pendingRows.some(row => row.key === "settings.google-login")) pendingRows.push({
      key:"settings.google-login", label:"Google 로그인과 권한", card:"settings", target:"google-login",
      ok:null, pending:true, detail:"확인 중…", fix:"",
    });
    return pendingRows;
  }
  if (!S.google) return rows;
  // The latest shared Google result wins over a previous home snapshot.
  const withoutGoogle = rows.filter((row) => !["settings.google-login", "settings.goedu-account", "settings.grant", "connect.google-login"].includes(row.key));
  const ready = isGoogleReady(S.google);
  // A check that did not finish is grey and never green or a task (LOGIN-17, C6, C8).
  const unread = googleAuthCheckFailed(S.google);
  const detail = ready ? (S.google.user || "") : unread ? googleUnreadText() : googleAuthorizationMessage(S.google);
  const googleRows = [{ key: "settings.google-login", label: "Google 로그인과 권한", ok: unread ? null : ready, unread, detail, fix: ready || unread ? "" : detail, card: "settings", target: "google-login" }];
  if (!ready) googleRows.push({ ...googleRows[0], key: "connect.google-login", card: "connect" });
  if (grantNeedsApproval(S.google)) {
    googleRows.push({ key: "settings.grant", label: "Google 권한", ok: false, detail: GRANT_MISSING_TEXT, fix: GRANT_MISSING_TEXT, card: "settings", target: "google-login" });
  } else if (ready && S.google.grant?.state === "check-failed") {
    googleRows.push({ key: "settings.grant", label: "Google 권한", ok: null, unread: true, detail: "확인하지 못했어요", fix: "", card: "settings", target: "google-login" });
  }
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
  const unread = rows.filter((row) => row.unread && !row.pending).length;
  return { good: counted.length - bad, total: counted.length, bad, pending, unread };
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
function outboxStateHtml(cap) {
  const state = cap.outbox;
  if (!state || state.skipped) return "";
  const button = (key, choice, label) => `<button class="btn-tonal" data-action="outbox-decision" data-source-hash="${esc(cap.source_hash || "")}" data-action-key="${esc(key)}" data-choice="${choice}">${label}</button>`;
  if (state.old) return `<div class="capture-retry-action">${button("", "send", "보내기")}${button("", "skip", "보내지 않기")}</div>`;
  return (state.actions || []).map(item => {
    const unknown = ["unknown", "needs-decision"].includes(item.status);
    const waiting = ["deferred", "in-progress"].includes(item.status);
    const rejected = ["rejected", "refused"].includes(item.status);
    const activity = [item.device, item.at ? fmtShort(item.at) : ""].filter(Boolean).map(esc).join(" · ");
    if (!unknown && !waiting && item.status !== "not-registered" && !rejected) return activity ? `<div class="capture-context">${activity}</div>` : "";
    const text = unknown ? "Google에 이미 만들었을 수 있으니 같은 메시지를 다시 누르지 말고 기록에서 확인해 주세요." : item.status === "not-registered" ? "등록되지 않았어요" : "로그인하면 등록돼요";
    const permission = rejected && item.code === "GOOGLE_PERMISSION_REQUIRED";
    return `<div class="capture-retry-action"><span>${esc(item.title)} · ${permission ? "Google 권한을 다시 승인해 주세요." : rejected ? "등록되지 않았어요" : text}${activity ? ` · ${activity}` : ""}</span>${permission ? '<button class="btn-tonal" data-action="reauthorize-google">권한 다시 승인</button>' : ""}${unknown ? button(item.key, "confirmed", "Google에서 확인했어요") + button(item.key, "not-registered", "등록되지 않았어요") : item.status === "not-registered" || item.status === "rejected" ? button(item.key, "retry", "다시 등록") : ""}</div>`;
  }).join("");
}
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
    <div class="cap-items">${items}${emptyLine}${retry}${retryButton}${outboxStateHtml(cap)}</div>${reason}</div>`;
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
  "outbox-decision": async (el) => {
    await call("brity_outbox_decide", String(el.dataset.sourceHash || ""), String(el.dataset.actionKey || ""), String(el.dataset.choice || ""));
    await loadCapturePage(S.capsPage || 1, { quiet: true });
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
  } catch (error) { console.error(error); /* 폴링 실패는 다음 틱에 다시 */ }
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
  { key: "identity", icon: "user", title: "내 정보", detail: "이름 · 학교 · 담임 · 결석신고서 · AI 출결 입력" },
  { key: "timetable", icon: "table", title: "시간표 · 담임학급 학생명단", detail: "주간 시간표 · 학생명단 · 휴일" },
  { key: "settings", icon: "sliders", title: "설정", detail: "컴퓨터 준비 · Google 로그인 · 단축키 · 자동 실행" },
  { key: "connect", icon: "link", title: "연결", detail: "Calendar · Tasks · Gemini 연결 API 키 · 출결" },
];
function cardStatus(card) {
  if (checksRetry.dirtyCards.has(card.key)) return checksRetry.failedAt
    ? { kind: "y", label: "점검 실패 · 다시 확인", bad: false }
    : { kind: "n", label: "변경 사항 점검 중…", bad: false };
  if (!S.checks.length) return { kind: "n", label: "점검 중…", bad: false };
  const summary = checkSummary(checksForCard(card.key));
  if (summary.bad) return { kind: "y", label: "확인 필요", bad: true, summary };
  if (summary.pending) return { kind: "n", label: "확인 중…", bad: false, summary };
  if (summary.unread) return { kind: "n", label: "확인하지 못했어요", bad: false, summary };
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
    return "Calendar · Tasks · Gemini 연결 API 키";
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
    else if (S.mode === "edit" || S.mode === "about" || S.mode === "attendance") {
      // Preserve the open form, including text not yet committed by change.
      const background = document.querySelector("#app > .body");
      if (background) background.outerHTML = homeHtml(true);
    }
    // Accepted checks must refresh the visible numbers in both wizard and edit
    // screens without replacing fields the teacher may still be entering.
    updateTabBadges();
    paintConnectionRefreshButton();
  };
  try {
    if (S.checks.length) paint();
    const checks = await call("home_checks");
    if (!current()) return;
    S.checks = checks;
    checksRetry.lastGood = checks;
    checksRetry.failedAt = 0;
    checksRetry.dirtyCards.clear();
    // The server refused this PC's session during the check: the status read follows it.
    if (isGoogleReady(S.google) && checks.some(row => row.key === "settings.google-login" && row.ok === false)) {
      checkGoogleOnReturn().catch(reportBackgroundFailure);
    }
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
  if (bootIdentityPending || (!S.google && googleObservationPending())) return false;
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
  return Boolean(S.login || S.linkLoading || classYearPending || S.classTaskSaving || checksRetry.inflight
    || S.rosterEditor?.busy || editAutoSavePromise || settingsAutoSavePromise || editDirtyFields.size);
}
function paintConnectionRefreshButton() {
  const button = document.querySelector('[data-action="connection-refresh"]');
  if (button) button.disabled = connectionRefreshActive() || connectionRefreshBlocked();
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
    if (!["home", "about"].includes(S.mode) || (!isGoogleReady(S.google) && !googleAuthCheckFailed(S.google))
        || connectionRefreshActive() || connectionRefreshBlocked()) {
      scheduleConnectionRefresh(60000);
      return;
    }
    await refreshConnectionStatus();
  }, delay);
}
function refreshConnectionStatus(options = {}) {
  if (connectionRefreshActive()) return connectionRefreshPending.promise;
  if (connectionRefreshBlocked()) return Promise.resolve();
  const pending = { context: googleListContext(), epoch: googleLoginEpoch, accountEpoch: accountUiEpoch,
    account: verifiedGoogleAccount(S.google) || S.lastVerifiedGoogleAccount, promise: null };
  connectionRefreshPending = pending;
  attendanceCardMount?.read?.();
  const current = () => connectionRefreshPending === pending && pending.context === googleListContext();
  pending.promise = (async () => {
    try {
      const status = options.googleStatus || await readGoogleStatus();
      if (!current()) return;
      adoptGoogleStatus(status);
      // An unknown -> verified observation changes the read context, not its
      // account. Rebind this explicit read only when the identity is unchanged.
      if (pending.epoch === googleLoginEpoch && pending.accountEpoch === accountUiEpoch
          && isGoogleReady(status) && verifiedGoogleAccount(status) === pending.account) {
        pending.context = googleListContext();
      }
      if (!current() || !isGoogleReady(S.google)) return;
      googleRecoveryNeeded = false;
      const onAttendanceTab = editingCard() === "connect" && S.connectTab === "attendance";
      const readsClassYear = attendanceUiEnabled() && isHomeroomTeacher() && onAttendanceTab;
      await Promise.all([
        loadLinkLists(true, current),
        readsClassYear ? refreshClassYear() : Promise.resolve(),
      ]);
      if (!readsClassYear) checkYearRevision().catch(reportBackgroundFailure);
      if (current()) await loadClassChat(!readsClassYear);
      // Rows that a failed check left grey are read again now that the check works (C6, C8).
      if (current() && S.checks.some(row => row.unread)) {
        invalidateCardChecks("connect", "settings");
        await refreshChecks();
      }
    } catch (error) {
      // Account verification failures already update the shared login state.
      if (current() && !(error instanceof StaleAccountResponse)) paintSettingsReadiness();
    } finally {
      if (connectionRefreshPending === pending) {
        connectionRefreshPending = null;
        scheduleConnectionRefresh();
        paintSettingsReadiness();
      }
    }
  })();
  paintSettingsReadiness();
  return pending.promise;
}
// Keep a Calendar/Tasks choice or Gemini key typed on this screen before the re-read.
bindActions({ "connection-refresh": () => { syncConnectFields(); return refreshConnectionStatus(); } });
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
    : summary.pending ? badge("n", "연결 확인 중…")
    : summary.unread ? badge("n", S.google?.server_unreachable ? SERVER_UNREACHABLE_TEXT : "확인하지 못했어요")
    : badge("g", "모두 정상");
  const name = (S.profileCache && S.profileCache["선생님이름"]) ? `${S.profileCache["선생님이름"]} 선생님, ` : "";
  const tiles = attendanceTileHtml() + CARDS.map((card) => {
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
  if (!S.profileCache) call("read_profile").then((p) => {
    S.profileCache = p;
    render();
  }).catch(reportBackgroundFailure);
  // Tile and window share the full-year read made at app start (SCREENS, MAIN §7.1).
  if (attendanceTileVisible() && !currentClassYear() && isGoogleReady(S.google)) loadClassYear();
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
        ${inSettingsWindow() ? settingsRefreshButtonHtml() : ""}
        <button class="win-x" data-action="back-home" aria-label="닫기">✕</button></div>
      <div class="win-body"><div class="page">${messages}${body}</div></div>
    </div></div>`;
}
/* 창 닫기 — ✕·어두운 바깥 클릭·Esc·다른 카드로 넘어가기 전, 모두 이 하나를 탄다.
   저장을 마친 뒤에만 닫힌다. 저장이 실패하면 창은 남고 배너에 이유가 적힌다. */
async function closeWindow(opensAnother = false) {
  if (attendanceCardMount && !attendanceCardMount.canLeave()) return false;
  if (S.mode === "attendance") {
    leaveAttendanceWindow();
    render();
    // The window kept its band while it was open: now the screen follows the ended session.
    if (attendanceSessionEnded) { attendanceSessionEnded = false; checkGoogleOnReturn().catch(reportBackgroundFailure); }
    return true;
  }
  if (S.rosterEditor?.busy) return;
  // 이름·이메일을 하나도 넣지 않은 명단은 저장할 것이 없으니 그대로 닫는다(빈 명단 창이 ✕로 닫히지 않던 문제).
  if (S.mode === "edit" && S.edit === "timetable" && S.rosterEditor?.dirty && !S.rosterEditor.rows.some(rosterRowHasStudent)) S.rosterEditor = null;
  if (S.mode === "edit" && S.edit === "timetable" && !(await rosterSavedBeforeLeaving())) return;
  if (S.mode !== "edit" && S.mode !== "about") return true;
  const request = beginIssueRequest(Boolean(settingsAutoSavePromise || editAutoSavePromise));
  await stopHotkeyRecording();
  let saved = false;
  try { saved = await flushEditSave(); } catch (error) { handleCaughtError(error, request); return false; }
  if (!saved) return false;  // 배너에 이유가 적혀 있다
  S.fieldIssues = {};
  clearProblemIssue();
  S.mode = "home"; S.edit = null; S.banner = null; S.hk = null; render();
  // A linked window came from the attendance window: closing it goes back there (SCREENS).
  if (attendanceReturnPending && !opensAnother) openAttendanceWindow();
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
  "선생님이름", "학년도", "학교명", "학교급", "담임여부", "담임학년", "담임반", "반이름",
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
        if (Object.keys(identity).length) {
          if (key === "identity" && attendanceUiEnabled()) await saveClassIdentity(dirtyFields);
          checkSaved(await callWithLocalRecovery(() => call("save_identity", identity)));
          if (attendanceUiEnabled() && key !== "identity" && (dirtyFields.has("담임여부") || yearSetupRun?.context === googleListContext()) && S.draft.profile["담임여부"] === "예") {
            const yearProblem = await ensureYearForProfile();
            if (yearProblem) savedNotice = `이 컴퓨터에 저장됨 · ${yearProblem}`;
          }
        }
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
    // Clearing a structured save problem can redraw; keep this identity correction first.
    if (autoSaveScreen() === "identity") syncEditFields("identity");
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
    if (box?.dataset?.rosterCell || box?.closest?.(".att")) return;
    // 학급 단톡방 이름은 치는 즉시 상태로 보관한다 — 폴링 render가 지우지 않게 (검토 C3)
    if (box && box.name === "class-space-name") S.spaceDraftName = box.value;
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
  else if (key === "identity") body = stepIdentity() + (attendanceUiEnabled() && isHomeroomTeacher() ? `<div id="attendance-card"></div>` : "") + `<div class="section-h" style="margin-top:26px">하루 일과</div>` + stepDay().replace(/^[\s\S]*?<\/p>/, "");
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
  if (key === "identity" && attendanceUiEnabled()) {
    await loadClassYear(true);
    if (!ownsIssueRequest(request)) return false;
    const doc = currentClassYear()?.phase === "ready" ? currentClassYear().answer?.yearDoc : null;
    if (doc) hydrateClassIdentity(doc);
  }
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
  if (key === "connect") S.connectTab = "messenger";
  // The roster lives on the server: a new opening reads it again, unless typing or a locked request waits here.
  if (key === "timetable" && S.rosterEditor && !S.rosterEditor.dirty && !S.rosterEditor.busy && !S.rosterEditor.pendingWrite) S.rosterEditor = null;
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
  const manualInstallGuide = "자동 업데이트가 정상적으로 되지 않는 경우, 아래 주소를 눌러 GitHub 릴리즈 페이지에서 최신 설치 파일을 직접 내려받은 뒤 실행해 주세요. 기존 프로그램을 삭제할 필요 없이 그대로 설치하면 업데이트됩니다.";
  root().innerHTML = homeHtml(true) + windowHtml("버전 및 제작 정보", `
    <h2 class="about-brand">${esc(b.name)}</h2>
    <p class="sub">${esc(b.tagline)}</p>
    <div class="panel">
      <div class="row"><span class="name">버전</span><span class="st">v${esc(S.info.version)}</span></div>
      <div class="row"><span class="name">만든 사람</span><span class="st">${esc(b.credit.replace("만든 사람: ", ""))}</span></div>
      <div class="row"><span class="name">게시자</span><span class="st">${esc(b.publisher)}</span></div>
      <div class="row"><span class="name">프로그램 업데이트</span>${u.st}${u.btn}</div>
      <div class="update-manual-note"><p class="update-manual-text">${esc(manualInstallGuide)}</p>
        <button type="button" class="update-manual-link" role="link" data-action="link-open" data-url="${LATEST_RELEASE_URL}"${directDisabled}>${esc(LATEST_RELEASE_URL)}</button></div>
    </div>
    <div class="section-h">웹사이트</div>
    ${linkRow(b.website)}`, false) + toastHtml();
}

async function openCard(key) {
  // 창이 떠 있는 채로 다른 카드를 여는 길(홈 점검의 "설정 열기" 등)은 지금 창을
  // 같은 닫기 흐름(저장 마무리 포함)으로 닫고 나서 연다 — 한 번에 한 창.
  if (S.mode === "edit" || S.mode === "about" || S.mode === "attendance") {
    if (!(await closeWindow(true))) return;
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
    refreshSettingsStatus().catch(reportBackgroundFailure);
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
  "open-attendance": () => openAttendanceWindow(),
});

/* ---------- the attendance window (web/attendance.js) ----------
   Preact keeps its tree in #att-root, beside #app, because render() replaces #app on every paint.
   While it is open S.mode is "attendance" and Home paints behind it. */
let attendanceMount = null;
let attendanceChatSetupPending = false;
let attendanceSessionEnded = false;
let attendanceReturnPending = false;   // A linked window is open and goes back to this window.
let attendanceMemoAccount = "";        // The account the window's memory belongs to.
let attendancePcInfoValue = { hasGeminiKey: null, profile: null };
function attendanceTileVisible() {
  // For a homeroom teacher with a school year on the server. A failed /v1/me leaves hasAnyYear
  // unread, which changes nothing (MAIN §7.14): the tile stays, grey.
  return attendanceUiEnabled() && isHomeroomTeacher() && S.google?.hasAnyYear !== false;
}
// A failure here is logged and the tile stays grey: the rest of Home does not depend on it (OBS-09).
function attendanceTileSummary(answer) {
  try {
    return window.TMAttendance.homeSummary(answer);
  } catch (error) {
    console.error(error);
    return { tone: "muted", text: "확인하지 못했어요", detail: "" };
  }
}
function attendanceTileHtml() {
  if (!attendanceTileVisible()) return "";
  const year = currentClassYear();
  const reading = googleAuthCheckFailed(S.google) ? false : !isGoogleReady(S.google) || !year || year.phase === "pending";
  const summary = reading ? { tone: "muted", text: "확인 중…", detail: "" }
    : attendanceTileSummary(year?.phase === "ready" && !year.stale ? year.answer : null);
  const kind = summary.tone === "ok" ? "g" : summary.tone === "warn" ? "y" : "n";
  return `<button class="tile tile-wide" id="attendance-tile" data-action="open-attendance">
      <span class="icbox">${icon("calendar-check")}</span>
      <span class="tx"><span class="tt">출결·메신저 ${badge(kind, summary.text)}</span>
      <span class="ds">${esc(summary.detail || "")}</span></span></button>`;
}
function paintAttendanceTile() {
  const tile = document.querySelector("#attendance-tile");
  if (tile) replaceIfChanged(tile, attendanceTileHtml());
}
function attendancePcInfo() { return attendancePcInfoValue; }
function readAttendancePcInfo() {
  const p = S.profileCache || S.draft.profile || {};
  const grade = String(p["담임학년"] || "").trim(), classNumber = String(p["담임반"] || "").trim();
  attendancePcInfoValue = { hasGeminiKey: null, profile: { schoolName: String(p["학교명"] || "").trim(), grade, classNumber,
    classLabel: String(p["반이름"] || "").trim() || (grade && classNumber ? `${grade}학년 ${classNumber}반` : ""), teacherName: String(p["선생님이름"] || "").trim() } };
  // Only whether a key is here leaves this function, never the key.
  call("get_messenger_settings")
    .then(saved => { attendancePcInfoValue = { ...attendancePcInfoValue, hasGeminiKey: Boolean(String(saved?.gemini_api_key || "").trim()) }; })
    .catch(reportBackgroundFailure);
}
function mountAttendanceWindow() {
  if (attendanceMount) return;  // One window, one tree.
  const year = currentClassYear();
  attendanceMount = window.TMAttendance.mount(document.getElementById("att-root"), {
    dataCall, readChatStatus: readClassChat, watchChatStatus: watchClassChat,
    chatSetup: attendanceChatSetupPending,
    aiInput: args => call("attendance_ai_input", args),
    diagnostic: logAttendanceUiDiagnostic,
    openLinked: openAttendanceLink,
    onClose: () => closeWindow(),
    pcInfo: attendancePcInfo,
    initialRead: year?.phase === "ready" && !year.stale && year.answer?.year ? year.answer : undefined,
    onRead: answer => { adoptClassYearAnswer(answer); paintAttendanceTile(); loadClassChat(true).catch(reportBackgroundFailure); },
  });
  attendanceChatSetupPending = false;
}
function unmountAttendanceWindow() {
  const mounted = attendanceMount;
  attendanceMount = null;
  if (mounted) mounted.unmount();
}
function leaveAttendanceWindow() {
  unmountAttendanceWindow();
  if (S.mode === "attendance") { S.mode = "home"; S.edit = null; }
}
function openAttendanceWindow() {
  if (S.mode === "attendance" || !window.TMAttendance) return;
  attendanceReturnPending = false;
  attendanceMemoAccount = verifiedGoogleAccount(S.google) || attendanceMemoAccount;
  S.timetableTab = "timetable";  // A link's roster tab ends with that link.
  S.banner = null;
  S.firstHomeNotice = false;
  clearProblemIssue();
  readAttendancePcInfo();
  S.mode = "attendance";
  S.edit = null;
  render();
  mountAttendanceWindow();
}
// One window at a time: a link closes this window and opens the target, and closing the target
// comes back here, on the same tab, scroll and filters (SCREENS, Q27 default).
async function openAttendanceLink(target) {
  const kind = String(target?.kind || "");
  if (kind === "external") { await call("open_url", String(target.url || "")); return; }
  leaveAttendanceWindow();
  attendanceReturnPending = true;
  try {
    if (kind === "roster") {
      const student = target.studentId && currentClassYear()?.answer?.students?.find(row => row.id === target.studentId);
      const label = student ? `${String(student.number || "").trim()}${String(student.name || "").trim()}`
        : String(target.label || "").replace(/^(\d{1,3})번\s+(.+)$/, (_match, number, name) => number + name.trim());
      if (target.focusEmail && label) S.rosterEmailFix = { labels: new Set([label]), originals: null, focusPending: true };
      S.timetableTab = "roster";
      await openCard("timetable");
    } else if (kind === "holidays") {
      S.timetableTab = "holidays";
      await openCard("timetable");
    } else if (kind === "attendance-connect") {
      await openCard("connect"); S.connectTab = "attendance"; render();
    } else if (kind === "brity") {
      await openCard("connect");
    } else if (kind === "profile") {
      await openCard("identity");
    } else if (kind === "reapprove") {
      await actions["reauthorize-google"]();
    } else if (kind === "relogin") {
      await signInAgainFromAttendance();
    } else {
      attendanceReturnPending = false;
      openAttendanceWindow();
    }
  } catch (error) {
    // The target did not open: Home shows the reason, which the window would have covered.
    attendanceReturnPending = false;
    handleCaughtError(error);
  }
}
// 다시 로그인: the status read signs this PC out if the session really ended, then the sign-in starts.
async function signInAgainFromAttendance() {
  attendanceSessionEnded = false;
  render();
  await checkGoogleOnReturn();
  if (accountProfileReloadPromise) await accountProfileReloadPromise;
  if (isGoogleReady(S.google) || googleAuthCheckFailed(S.google)) { openAttendanceWindow(); return; }
  if (S.mode !== "wizard" || S.step !== 2) { await actions["goto-settings"](); }
  await startSignin("gws_login_start");
}
function returnToAttendanceAfterSignin() {
  if (!attendanceReturnPending) return;
  if (S.mode === "edit" && S.edit === "settings") closeWindow().catch(reportBackgroundFailure);
  else if (S.mode === "home") openAttendanceWindow();
  else attendanceReturnPending = false;
}
// MAIN §6.5: on focus return and on the 30-minute refresh the tile reloads when `revision`
// changed. The open window runs the same check itself, and the wizard has no tile.
let yearRevisionTimer = null;
function scheduleYearRevisionCheck() {
  // Deferred, so the click that brought the window back lands first (OBS-07).
  clearTimeout(yearRevisionTimer);
  yearRevisionTimer = setTimeout(() => { checkYearRevision().catch(reportBackgroundFailure); }, 400);
}
async function checkYearRevision() {
  if (["attendance", "wizard", "loading"].includes(S.mode) || !attendanceTileVisible() || !isGoogleReady(S.google) || classYearPending) return;
  const year = currentClassYear();
  if (!year) { loadClassYear(); return; }
  if (year.phase !== "ready") { if (year.phase === "failed") loadClassYear(true); return; }
  const reply = await dataCall("/v1/data/check", {});
  if (year !== currentClassYear()) return;
  if (reply?.status !== 200 || !reply.body) {
    if (reply?.status !== 401) { S.classYear = { ...year, stale: true }; paintAttendanceTile(); }
    return;
  }
  const a = year.answer;
  if (reply.body.year !== a.year || (reply.body.revision || 0) !== (a.revision || 0)
      || reply.body.activeYear !== a.activeYear || reply.body.currentYearReady !== a.currentYearReady) {
    await loadClassYear(true);
  } else if (year.stale) {
    S.classYear = { ...year, stale: false };
    paintAttendanceTile();
  }
}

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
  if (event.key !== "Escape" || event.defaultPrevented) return;
  if (hotkeyCapture.active) return;
  if (attendanceCardMount?.hasDialog()) return;
  if (S.workspaceGuideOpen) { S.workspaceGuideOpen = false; render(); return; }
  // The attendance window decides for itself: an open dialog or the selection first (SCREENS).
  if (S.mode !== "edit" && S.mode !== "about") return;
  closeWindow();
});

/* ---------- 라우터 ---------- */
function screenKey() { return `${S.mode}|${S.step}|${S.edit}|${S.connectTab}`; }
const resumeHandlers = {
  "google-login": null,
  "chat-permission": null,
  "chat-space-list": null,
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
  refreshSettingsStatus().catch(reportBackgroundFailure);
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
let attendanceCardMount = null;
function unmountAttendanceCard() { if (attendanceCardMount) { attendanceCardMount.unmount(); attendanceCardMount = null; } }
function mountAttendanceCard() {
  const container = document.getElementById("attendance-card");
  attendanceMemoAccount = verifiedGoogleAccount(S.google) || attendanceMemoAccount;
  if (!container || !window.TMAttendance?.mountCard) return;
  const card = S.edit === "identity" ? "identity" : S.edit === "timetable" ? "holidays" : "connect";
  attendanceCardMount = window.TMAttendance.mountCard(container, { card, dataCall, readChatStatus: readClassChat, watchChatStatus: watchClassChat,
    openLinked: openAttendanceLink, onClose: () => closeWindow(),
    exportAll: () => call("attendance_export"), onRead: answer => { adoptClassYearAnswer(answer); paintAttendanceTile(); loadClassChat(true).catch(reportBackgroundFailure); } });
}
let lastScreenKey = "";
function render() {
  unmountAttendanceCard();
  // No window tree outlives its mode, whichever path changed the mode.
  if (S.mode !== "attendance") unmountAttendanceWindow();
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
  document.body.classList.toggle("win-open", S.mode === "edit" || S.mode === "about" || S.mode === "attendance");
  if (S.mode === "loading") {
    root().innerHTML = `<div class="boot">${bootGoogleRecheckWaiting ? esc(BOOT_GOOGLE_RECHECK_MESSAGE) : "여는 중이에요…"}</div>`;
    return;
  }
  // 같은 화면을 다시 그릴 때는 스크롤을 유지한다 — 세그먼트·선택 조작으로 위로 튀지 않게.
  // 마법사(.shell)는 .body가, 홈·편집 화면은 문서 전체가 스크롤되므로 둘 다 기억한다.
  const prevBody = document.querySelector(".body");
  const prevScroll = prevBody ? prevBody.scrollTop : 0;
  const prevDocScroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
  const prevWin = document.querySelector(".win-body");
  const prevWinScroll = prevWin ? prevWin.scrollTop : 0;
  const sameScreen = lastScreenKey === screenKey();
  if (!sameScreen) invalidateIssueResume();
  if (!(S.mode === "wizard" && S.step === 8 && S.connectTab === "attendance")) wizardAttendanceEntered = false;
  if (S.mode === "wizard") renderWizard();
  else if (S.mode === "edit") renderEdit(S.edit);
  else if (S.mode === "about") renderAbout();
  else if (S.mode === "attendance") root().innerHTML = homeHtml(true) + toastHtml();  // #app only, never #att-root
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
  mountAttendanceCard();
  if (S.focusTarget) {
    const el = document.querySelector(`[name="${S.focusTarget}"]`);
    S.focusTarget = "";
    if (el && el.focus) el.focus();
  }
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
  bootIdentityPending = true;
  try {
    // get_app_info hides saved account data until backend identity is verified.
    // Preserve the fast verified startup, but never gate the local shell on I/O.
    const identity = call("google_status").then(adoptGoogleStatus)
      .finally(() => { bootIdentityPending = false; });
    const identitySettled = identity.catch(reportBackgroundFailure);
    const localInfo = await call("get_app_info");
    await Promise.race([identitySettled, new Promise(resolve => setTimeout(resolve, 0))]);
    let startupScreen = null;
    if (bootIdentityPending) {
      adoptAppInfo(localInfo);
      // Step 2 may only mean identity is still unknown; do not render its login
      // prompt (or start its readiness work) before the first observation ends.
      if (localInfo.mode === "wizard" && localInfo.step === 2) {
        S.mode = "loading";
        bootGoogleRecheckWaiting = true;
      }
      render();
      startupScreen = screenKey();
    }
    try { await identity; } catch (error) {
      // call() records an unknown observation without declaring logout.
    }
    let verifiedInfo = await call("get_app_info");
    // LOGIN-32: a boot check that failed (not a confirmed logout) must not open the
    // step-2 login screen as if signed out. Wait in grey and recheck; the boot check plus
    // these rechecks are at most three google_status reads (OBS-04).
    const bootEpoch = accountUiEpoch;
    const stillBootScreen = () => bootEpoch === accountUiEpoch && !S.login
      && (startupScreen === null || screenKey() === startupScreen || S.mode === "loading");
    for (const delay of BOOT_GOOGLE_RECHECK_DELAYS_MS) {
      if (!stillBootScreen() || !incompleteGoogleObservation(S.google)
          || verifiedInfo.mode !== "wizard" || verifiedInfo.step !== 2) break;
      S.mode = "loading";
      bootGoogleRecheckWaiting = true;
      render();
      await new Promise(resolve => setTimeout(resolve, delay));
      if (!stillBootScreen() || S.mode !== "loading") break;
      // A focus check may already have verified the account (shared read, OBS-05).
      if (incompleteGoogleObservation(S.google)) {
        try { adoptGoogleStatus(await call("google_status")); } catch (error) {
          // call() records an unknown observation without declaring logout.
        }
      }
      if (!stillBootScreen()) break;
      verifiedInfo = await call("get_app_info");
    }
    bootGoogleRecheckWaiting = false;
    if (bootEpoch !== accountUiEpoch && S.mode === "loading") verifiedInfo = await call("get_app_info");
    if (startupScreen === null || screenKey() === startupScreen || S.mode === "loading") adoptAppInfo(verifiedInfo);
    if (verifiedInfo.signed_out && S.mode === "home" && !isGoogleReady(S.google)) {
      setBanner("warn", verifiedInfo.signed_out_reason === "logout" ? SIGNED_OUT_HOME_TEXT : SIGNIN_AGAIN_HOME_TEXT, "google-login");
    }
    paintSettingsReadiness();
    watchNetworkStatus();
    askUpdateOnStart();
    startLoginWatch();
    scheduleConnectionRefresh();
  } catch (error) {
    bootIdentityPending = false;
    if (S.info) paintSettingsReadiness();
    else root().innerHTML = `<div class="boot">${esc(error.message)}</div>`;
  }
}
const SIGNED_OUT_HOME_TEXT = "로그아웃했어요. [설정]에서 Google로 로그인하면 다시 쓸 수 있어요.";
// No logout happened here: an update from the sheet version or an ended session needs one sign-in.
const SIGNIN_AGAIN_HOME_TEXT = "Google 로그인을 한 번 다시 해 주세요. 업데이트 뒤나 로그인이 오래되면 필요해요. [설정]에서 로그인하면 저장한 자료를 그대로 이어서 써요.";
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

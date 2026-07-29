"use strict";
/* Teacher Manager 화면. 규칙: 화면은 bridge만 부른다. 업무 판단은 전부 파이썬에 있다. */

/* ---------- 상태 ---------- */
const S = {
  mode: "loading",           // loading | wizard | home | edit | about
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
  listsLoaded: false,
  listsError: false,
  maps: { calendars: {}, tasklists: {} },
  edit: null,
  busy: {},
  banner: null,              // {kind, text}
  toast: null,
  applyResults: null,
  firstHomeNotice: false,     // 마법사 직후 홈에 한 번 보여주는 사용법 안내 띠
  updateInfo: null,           // get_update_info 결과 (새 버전 있을 때만 채움)
  updateCheck: null,          // 업데이트 확인 결과 (null | "latest" | "available" | "failed")
  updating: false,            // 지금 업데이트 진행 중 (중복 클릭·재실행 방지)
  updateOffer: null,          // update_offer 결과 — 켤 때 한 번 묻는 자리에서 씀
  aiTools: null,              // AI 비서 탭 — 도구 감지 결과 (null=미조회, "loading"=조회 중)
  aiInstall: null,            // AI 비서 탭 — 연결 실행 결과
  maxStep: 1,                 // 마법사에서 한 번이라도 도달한 가장 먼 단계
  login: null,
  progress: null,            // capture_progress 스냅샷 (active일 때만)
  lastLiveStep: null,        // 실패 시 어느 단계에서 멈췄는지 표시용
  doneShown: "",             // 결과 카드를 이미 보여준 run_id
  caps: null,                // recent_captures 목록 (null=미로딩)
  capsOpen: {},              // 펼친 기록 줄 key -> true
  freshWhen: "",             // "방금" 배지를 달 기록의 when
  fieldIssues: {},           // target -> {key, target, message, tab}
  connectTab: "messenger",   // messenger | attendance
  attendance: null,          // attendance_status/ensure_attendance 응답
  attendanceSaving: false,   // 탭을 오가며 다시 그려도 출결 준비 중복 클릭을 막는다
  chatStatus: null,          // attendance_chat_status 응답 (null=미조회, "loading"=질의 중)
};

const WIZARD_STEPS = [
  "시작하기", "내 정보", "하루 일과", "시간표", "설정", "연결", "마무리",
];

/* ---------- bridge ---------- */
function call(name, ...args) {
  return window.pywebview.api[name](...args).then((res) => {
    if (!res || res.ok !== true) throw new Error((res && res.error) || "알 수 없는 오류가 났어요");
    return res.data;
  });
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
  const mark = { g: "✓", y: "!", r: "✕", n: "·" }[kind] || "·";
  return `<span class="badge ${kind}">${mark} ${esc(label)}</span>`;
}
function root() { return document.getElementById("app"); }

function setBanner(kind, text) { S.banner = text ? { kind, text } : null; render(); }
function showToast(text) {
  S.toast = text; render();
  setTimeout(() => { S.toast = null; render(); }, 2200);
}
function bannerHtml() {
  if (!S.banner) return "";
  const logs = S.banner.kind === "error"
    ? `<button class="btn-quiet" data-action="open-logs">기록 폴더 열기</button>` : "";
  return `<div class="banner ${S.banner.kind}"><span>${esc(S.banner.text)}</span>${logs}</div>`;
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
  "gemini_api_key": "Gemini API key가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
  "google-login": "Google 로그인이 필요해요. 설정에서 경기도교육청 클라우드 계정(@goedu.kr)으로 로그인해 주세요.",
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
  const row = (S.fieldIssues || {})[name];
  return row ? row.message : "";
}
function fieldNoteHtml(name) {
  const error = fieldError(name);
  return error ? `<span class="field-error">${esc(error)}</span>` : "";
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
  return `<div class="field${error ? " has-error" : ""}">${input}${note}</div>`;
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
  try { await fn(el); }
  catch (error) { setBanner("error", error.message); }
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
  "link-open": (el) => call("open_url", el.dataset.url),
  "link-copy": (el) => { copyText(el.dataset.url); },
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
    const attr = n !== S.step && n <= reached ? ` data-action="go-step" data-step="${n}"` : "";
    return `<button class="step ${cls}"${attr}><span class="n">${mark}</span>${esc(title)}</button>`;
  }).join("");
  const info = S.info || { version: "", branding: { name: "" } };
  return `<div class="rail">${rows}` +
    `<div class="rail-tail">${esc(info.branding.name)} v${esc(info.version)}</div></div>`;
}

/* ---------- 로그인 상시 감시 — 끊어지면 끊어졌다고 말한다 ---------- */
const LOGIN_WATCH_INTERVAL_MS = 3 * 60 * 1000;  // 검증 전 앱은 토큰이 수시로 회수된다
let loginWatchTimer = null;
function startLoginWatch() {
  if (loginWatchTimer) return;
  const tick = async () => {
    loginWatchTimer = setTimeout(tick, LOGIN_WATCH_INTERVAL_MS);
    if (S.login) return;  // 로그인 진행 중에는 전용 폴링이 따로 본다
    try {
      const status = await call("google_status");
      const before = S.google ? Boolean(S.google.logged_in) : null;
      S.google = status;
      if (before === true && !status.logged_in) {
        // 끊김 감지 — 화면 곳곳이 실상을 다시 읽게 비우고 알린다
        S.attendance = null;
        S.chatStatus = null;
        S.checks = [];
        setBanner("warn", "Google 로그인이 풀렸어요. 설정에서 다시 로그인해 주세요.");
      } else if (before === false && status.logged_in) {
        S.attendance = null;
        S.chatStatus = null;
        S.checks = [];
        render();
      }
    } catch (error) { /* 다음 틱에 다시 */ }
  };
  loginWatchTimer = setTimeout(tick, LOGIN_WATCH_INTERVAL_MS);
}

function stepStub(n) {
  return `<h1>${esc(WIZARD_STEPS[n - 1])}</h1><p class="sub">이 단계 화면은 다음 태스크에서 채워요.</p>`;
}

function wizardFootHtml() {
  const back = S.step > 1 ? `<button class="btn-back" data-action="go-prev">${icon("chevron-left", "small")} 이전</button>` : "<span></span>";
  const nextLabel = S.step === WIZARD_STEPS.length ? "" : "다음";
  const next = nextLabel ? `<button class="btn" data-action="go-next" data-busy-text="확인 중…">${nextLabel}</button>` : "";
  return `<div class="foot">${back}${next}</div>`;
}

function renderWizard() {
  const body = (stepBodies[S.step] || (() => stepStub(S.step)))();
  const foot = S.step === WIZARD_STEPS.length ? "" : wizardFootHtml();
  root().innerHTML =
    `<div class="shell">${railHtml()}` +
    `<div class="body"><div class="body-inner"><div class="page">${bannerHtml()}${body}</div></div>${foot}</div></div>` + toastHtml();
}

function currentState() {
  return { version: 1, completed: false, step: S.step, max_step: S.maxStep, draft: S.draft };
}
function saveDraft() { return call("save_setup_state", currentState()); }

async function goStepAsync(n) {
  await stopHotkeyRecording();
  S.banner = null;
  S.step = Math.max(1, Math.min(WIZARD_STEPS.length, n));
  S.maxStep = Math.max(S.maxStep || 1, S.step);
  await saveDraft();
  render();
}
async function goNextAsync() {
  const validate = validators[S.step];
  const problem = validate ? await validate() : "";
  if (problem) { setBanner("warn", problem); return; }
  if (S.step === 6 && S.connectTab === "messenger") {
    // 연결은 메신저 → 출결 → AI 비서 세 탭을 차례로 지난 뒤에 마무리로 간다.
    S.connectTab = "attendance";
    S.chatStatus = null;
    S.attendance = null;  // 탭 클릭 경로와 동일하게 새로 확인 — 오래된 상태 재사용 방지
    await saveDraft();
    render();
    return;
  }
  if (S.step === 6 && S.connectTab === "attendance") {
    S.connectTab = "ai";
    S.aiTools = null;
    S.aiInstall = null;
    stopChatConnectPoll();
    await saveDraft();
    render();
    return;
  }
  await goStepAsync(S.step + 1);
}

bindActions({
  "go-prev": () => {
    if (S.step === 6 && S.connectTab === "ai") {
      S.connectTab = "attendance";
      S.chatStatus = null;
      S.attendance = null;
      render();
      return;
    }
    if (S.step === 6 && S.connectTab === "attendance") {
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
      const validate = validators[S.step];
      const problem = validate ? await validate() : "";
      if (problem) { setBanner("warn", problem); return; }
    }
    goStepAsync(target);
  },
});

/* ---------- 1단계: 시작하기 ---------- */
stepBodies[1] = function stepStart() {
  const b = S.info.branding;
  const dest = (logo, name, role) =>
    `<span class="dest"><img class="dest-logo" src="assets/${logo}" alt="${esc(name)} 로고">
      <span><b>${esc(name)}</b><small>${esc(role)}</small></span></span>`;
  return `
    <p class="start-eyebrow">${esc(b.publisher)}</p>
    <p class="wordmark">${esc(b.name)}</p>
    <p class="start-tagline">${esc(b.tagline)}</p>
    <p class="start-value">학교 업무 <b>두 가지</b>를 자동으로 해 드려요</p>
    <div class="start-flows">
      <div class="sflow">
        <div class="srcbox">
          <div class="from">① 교육청 업무 메신저 (Brity)</div>
          <div class="bubble">3/20(금) 2학년 수학여행 사전답사 계획 제출 바랍니다…</div>
          <div class="bubble">5월 2일 종례 마치고 각 학급의 회장, 부회장은 대의원회의에 참석하도록 안내해주세요</div>
        </div>
        <div class="sarrow"><span class="key">단축키 한 번</span><span class="line"></span></div>
        <div class="dests">
          ${dest("google-calendar.svg", "Calendar", "일정 등록")}
          ${dest("google-tasks.svg", "Tasks", "할 일 등록")}
          ${dest("google-chat.svg", "Chat", "학생·학급 안내 문자")}
          ${dest("google-sheets.svg", "Sheet", "보낸 안내 자동 기록")}
        </div>
      </div>
      <div class="sflow">
        <div class="srcbox">
          <div class="from">② 출결 DB Google Sheet</div>
          <div class="sheet-entry">
            <span class="chips"><span class="cell">10월 5일</span><span class="cell">김OO</span><span class="cell">질병조퇴</span><span class="cell">3교시</span><span class="cell">사유-감기</span></span>
            <span class="plain">결석신고서-미제출 첨부서류-미제출</span>
          </div>
          <div class="sheet-entry">
            <span class="chips"><span class="cell">12월 17일</span><span class="cell">박OO</span><span class="cell">인정결석</span><span class="cell">사유-조부상</span></span>
            <span class="plain">결석신고서-제출 첨부서류-미제출</span>
          </div>
        </div>
        <div class="sarrow"><span class="key">자동으로</span><span class="line"></span></div>
        <div class="dests">
          ${dest("google-docs.svg", "Docs", "결석 신고서 자동완성")}
          ${dest("google-chat.svg", "Chat", "서류 지참 요청 문자")}
          ${dest("google-tasks.svg", "Tasks", "조종례 미제출 확인")}
        </div>
      </div>
    </div>
    <div class="start-bottom">
      <span class="account-note"><span class="mark">!</span><span>교육디지털원패스 및 경기도교육청 클라우드서비스에 가입한 계정(@goedu.kr)이 있어야 해요</span></span>
      <p class="start-credit">${esc(b.credit)}</p>
    </div>`;
};

/* ---------- 구글 로그인 폴링 ---------- */
let loginTimer = null;
function stopLoginPoll() {
  if (loginTimer) { clearTimeout(loginTimer); loginTimer = null; }
}
async function pollLoginOnce() {
  const snap = await call("gws_login_status");
  if (snap.ok === true) {
    S.login = null;
    delete S.fieldIssues["google-login"];
    S.attendance = null;  // 로그인 전의 "로그인을 마쳐 주세요" 상태를 버린다
    await refreshSettingsStatus();
    showToast("로그인됐어요");
    return false;
  }
  if (snap.ok === false) {
    S.login = null;
    setBanner("error", "로그인이 끝나지 않았어요. " + (snap.detail || "다시 시도해 주세요."));
    return false;
  }
  const urlArrived = S.login && S.login.url !== snap.url;
  S.login = snap;
  if (urlArrived) render();
  return true;
}
function pollLogin() {
  stopLoginPoll();
  loginTimer = setTimeout(async () => {
    if (!S.login) return;
    try {
      if (await pollLoginOnce()) pollLogin();
    } catch (error) {
      S.login = null;
      setBanner("error", error.message);
    }
  }, 1000);
}
bindActions({
  "install-gws": async () => {
    if (!window.confirm("gws 도구를 npm 전역으로 설치할까요?")) return;
    const r = await call("install_gws");
    if (!r.success) throw new Error("설치에 실패했어요: " + r.detail);
    await refreshSettingsStatus();
  },
  "gws-login": async () => {
    S.banner = null;
    S.login = await call("gws_login_start");
    render();
    pollLogin();
  },
  "gws-logout": async () => {
    if (!window.confirm("현재 구글 계정에서 로그아웃할까요?")) return;
    const result = await call("gws_logout");
    if (!result.success) throw new Error(result.detail || "로그아웃하지 못했어요");
    S.login = null;
    S.lists = { calendars: [], tasklists: [] };
    S.listsLoaded = false;
    S.listsError = false;
    await refreshSettingsStatus();
    showToast("Teacher Manager에서 로그아웃했어요");
  },
  "login-cancel": async () => {
    stopLoginPoll();
    await call("gws_login_cancel");
    S.login = null;
    setBanner("warn", "로그인을 취소했어요. 다시 시도할 수 있어요.");
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
stepBodies[2] = function stepIdentity() {
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
  return `
    <h1>선생님을 알려주세요</h1>
    <p class="sub">캘린더 제목과 안내 문구에 쓰여요</p>
    ${formTable(
      fieldRow("선생님이름", "이름", p["선생님이름"]) +
      fieldRow("학교명", "학교 이름", p["학교명"], { placeholder: "예: OO고등학교" }) +
      rawRow("학교급 (수업 시간을 자동 계산해요)",
        choiceCell("학교급", [["초", "초등 (40분)"], ["중", "중학 (45분)"], ["고", "고등 (50분)"]])) +
      rawRow("담임을 맡고 있나요?", choiceCell("담임여부", [["예", "예"], ["아니오", "아니오"]])) +
      homeroomRow
    )}`;
};
function syncProfileFields() {
  Object.assign(S.draft.profile, readFields(IDENTITY_FIELDS));
  S.draft.profile["학교급"] = readRadio("학교급");
  S.draft.profile["담임여부"] = readRadio("담임여부");
  if (S.draft.profile["담임여부"] === "예") {
    Object.assign(S.draft.profile, readFields(["담임학년", "담임반"]));
  }
}
validators[2] = function validateIdentity() {
  syncProfileFields();
  const rows = identityIssues();
  replaceEditableIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
};
document.addEventListener("change", (event) => {
  if (event.target.name !== "담임여부") return;
  if (S.mode === "wizard" && S.step === 2) { syncProfileFields(); render(); return; }
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
stepBodies[3] = function stepDay() {
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
};
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
validators[3] = function validateDay() {
  syncDayFields();
  const rows = dayIssues();
  replaceEditableIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
};

/* ---------- 4단계: 시간표 ---------- */
const GRID_DAYS = ["월", "화", "수", "목", "금"];
async function ensureGridLoaded() {
  if (!S.draft.grid || !S.draft.grid.length) S.draft.grid = await call("read_grid");
}
stepBodies[4] = function stepTimetable() {
  if (!S.draft.grid || !S.draft.grid.length) {
    ensureGridLoaded().then(render).catch((e) => setBanner("error", e.message));
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
    <table class="grid-table">${head}${rows}</table>`;
};
function syncGridFields() {
  document.querySelectorAll("[data-grid]").forEach((el) => {
    const [r, c] = el.dataset.grid.split(":").map(Number);
    S.draft.grid[r][c] = el.value.trim();
  });
}
validators[4] = function validateTimetable() { syncGridFields(); return ""; };

/* ---------- 연결·설정 공통 ---------- */
const KEY_MESSAGES = {
  ok: "key가 정상이에요",
  missing: "Gemini API key가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요.",
  invalid: "Gemini API key가 맞지 않아요. AI Studio에서 다시 복사해 주세요.",
  "rate-limited": "현재 사용 한도에 도달했어요. 잠시 뒤 다시 확인해 주세요.",
  network: "인터넷 연결을 확인한 뒤 Gemini API key를 다시 확인해 주세요.",
};
const PROBE_MESSAGES = {
  available: "사용할 수 있어요",
  taken: "다른 프로그램이 쓰고 있어요 (도우미가 이미 이 단축키로 실행 중이면 정상이에요)",
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
    <span class="hint">Ctrl·Alt·Shift·Win 중 두 개 이상만 눌러도 되고, 문자나 숫자를 함께 눌러도 돼요. Esc를 누르면 취소해요.</span>
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
    const result = await call("probe_hotkey", text);
    if (hotkeyCapture.generation !== generation) return;
    if (result.status === "available") {
      S.draft.bridge.hotkey = text;
      if (S.mode === "edit" && S.edit === "settings") {
        // 설정 화면은 자동 저장 — 녹음이 끝나면(아래 finally에서 등록 재개 후) 바로 저장한다.
        S.hk.status = { kind: "ok", text: `${prettyHotkey(text)} · 저장하는 중…` };
        autoSaveWhenDone = true;
      } else {
        S.hk.status = { kind: "ok", text: `${prettyHotkey(text)} · 저장할 수 있어요` };
      }
    } else if (result.status === "taken") {
      S.hk.status = { kind: "bad", text: "다른 프로그램이 이 조합을 쓰고 있어요" };
    } else {
      S.hk.status = { kind: "bad", text: PROBE_MESSAGES.invalid };
    }
  } catch (error) {
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
          await autoSaveSettings(true);
        } catch (error) {
          if (S.hk) S.hk.status = { kind: "bad", text: error.message };
          setBanner("error", error.message);
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
  return `<div class="section-note" style="margin:0 0 4px">Google API key 발급 URL</div>
    <div class="linkrow"><span class="url" title="${esc(API_KEY_URL)}">${esc(API_KEY_URL)}</span>
    <span class="acts">
      <button class="btn-tonal" data-action="link-open" data-url="${esc(API_KEY_URL)}">${icon("external-link", "small blue")} 열기</button>
      <button class="btn-tonal youtube" data-action="link-open" data-url="${esc(API_KEY_GUIDE_URL)}">${icon("youtube", "small")} 발급방법</button>
    </span></div>`;
}
function geminiSectionHtml(d) {
  const keyLine = S.keyStatus ? badge(S.keyStatus.kind, S.keyStatus.text) : "";
  const model = d.gemini_model || "gemini-3.5-flash";
  const modelRow = rawRow("Gemini model", `<div class="field"><select name="gemini_model">
    <option value="gemini-3.5-flash" ${model === "gemini-3.5-flash" ? "selected" : ""}>Gemini 3.5 Flash · 추천</option>
    <option value="gemini-3.1-flash-lite" ${model === "gemini-3.1-flash-lite" ? "selected" : ""}>Gemini 3.1 Flash-Lite · 빠른 처리</option>
  </select></div>`);
  return `<div class="section-h">Gemini API key</div>
    <p class="sub" style="margin-bottom:10px">입력 내용이 제품 개선에 쓰일 수 있어요. 학생 개인정보가 담긴 메시지는 등록하지 마세요. key는 이 컴퓨터에만 저장돼요.</p>
    ${apiKeyLinkRow()}
    <div style="margin-top:12px"></div>
    ${formTable(
      fieldRow("gemini_api_key", "Gemini API key", d.gemini_api_key || "", { type: "password", placeholder: "붙여넣기" }) +
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
async function loadLinkLists() {
  if (S.linkLoading || S.listsLoaded) return;
  S.linkLoading = true;
  S.listsError = false;
  render();
  try {
    const [calendars, tasklists] = await Promise.all([call("list_calendars"), call("list_tasklists")]);
    S.lists = { calendars, tasklists };
    S.listsLoaded = true;
  } catch (error) {
    S.listsError = true;  // 기존 S.lists와 선택 ID는 그대로 둔다
  } finally {
    S.linkLoading = false;
    render();
  }
}
function linkSelectRow(idField, title, options, value, excludeId, savedName) {
  const filtered = options.filter((o) => o.id && o.id !== excludeId);
  // 목록을 못 불러온 동안에도 이미 골라 둔 값은 화면에 남긴다.
  const keepRow = value && !filtered.some((o) => o.id === value)
    ? `<option value="${esc(value)}" selected>${esc(savedName || "저장된 연결")}</option>` : "";
  const rows = filtered
    .map((o) => `<option value="${esc(o.id)}" ${o.id === value ? "selected" : ""}>${esc(o.name)}</option>`)
    .join("");
  const empty = S.linkLoading ? "불러오는 중…" : "골라 주세요";
  const error = fieldError(idField);
  return rawRow(title, `<div class="field${error ? " has-error" : ""}">
    <select name="${esc(idField)}" data-link-select${error ? ' aria-invalid="true"' : ""}>
      <option value="">${empty}</option>${keepRow}${rows}
    </select>${fieldNoteHtml(idField)}</div>`);
}
function linkGroupHtml(kind) {
  const cal = kind === "cal";
  const p = S.draft.profile;
  const fields = cal ? CAL_LINK_FIELDS : taskLinkFields();
  const options = cal ? S.lists.calendars : S.lists.tasklists;
  const mode = linkModes()[kind];
  const choices = cal
    ? [["existing", "기존 캘린더 연결하기"], ["new", "캘린더 새로 만들기"]]
    : [["existing", "기존 목록 연결하기"], ["new", "목록 새로 만들기"]];
  const segments = segChoice(`link-${kind}-mode`, mode, choices);
  let body;
  if (mode === "existing") {
    body = formTable(fields.map(([idField, nameField, title], index) => {
      const other = fields[index === 0 ? 1 : 0];
      const excludeId = other ? (p[other[0]] || "") : "";
      return linkSelectRow(idField, title, options, p[idField] || "", excludeId, p[nameField] || "");
    }).join(""));
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
    ${body}${skipNote}`;
}
function listsErrorNoticeHtml() {
  if (!S.listsError) return "";
  return `<div class="banner warn"><span>목록을 가져오지 못했어요. 설정에서 다시 점검해 주세요.</span>
    <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>`;
}

/* ---------- 연결 세 탭 — 폭은 글자 길이대로, 한 줄 유지 ---------- */
const CONNECT_TABS = [
  { tab: "messenger", line1: "Brity 메신저 ↔ Google Calendar & Tasks ↔ Chat" },
  { tab: "attendance", line1: "출결 Google Sheet ↔ Docs, Tasks, Chat" },
  { tab: "ai", line1: "AI ← MCP, Skill → Google" },
];
function tabProblemCount(tab) {
  return checkSummary(checksForTab(tab)).bad;
}
function connectTabsHtml() {
  const buttons = CONNECT_TABS.map((entry) => {
    const active = S.connectTab === entry.tab ? " active" : "";
    const count = tabProblemCount(entry.tab);
    const countHtml = `<span class="tab-count" data-tab-count="${entry.tab}"${count ? "" : ' style="display:none"'}>${count || ""}</span>`;
    return `<button class="connect-tab${active}" data-action="connect-tab" data-tab="${entry.tab}">
      <span class="tab-label"><span class="tab-line1">${esc(entry.line1)}</span></span>${countHtml}</button>`;
  }).join("");
  return `<div class="connect-tabs">${buttons}</div>`;
}
function messengerTabHtml() {
  const locked = !S.google || !S.google.logged_in;
  if (locked) {
    return `<div class="banner warn"><span>${esc(FIELD_MESSAGES["google-login"])}</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>
      <p class="sub" style="margin-top:14px">Google 연결과 로그인 상태는 설정 화면에서 확인해요.</p>`;
  }
  return `<div class="attendance-head"><div>
      <h2>메신저 내용을 어디에 등록할지 정해요</h2>
      <p>Google 연결과 로그인 상태는 설정 화면에서 확인해요.</p>
    </div></div>
    ${listsErrorNoticeHtml()}
    <div class="connect-section">${linkGroupHtml("cal")}</div>
    <div class="connect-section">${linkGroupHtml("task")}</div>
    <div class="connect-section">${geminiSectionHtml(S.draft.bridge)}</div>`;
}
const ATTENDANCE_SERVICES = [
  { role: "출결 DB 관리", name: "Google Sheet", logo: "assets/google-sheets.svg", service: "sheet" },
  { role: "결석 신고서 자동완성", name: "Google Docs", logo: "assets/google-docs.svg", service: "docs" },
  { role: "조종례시 출결서류 미제출 안내", name: "Google Tasks", logo: "assets/google-tasks.svg", service: "tasks" },
  { role: "미제출 출결서류 지참 요청 문자 전송", name: "Google Chat", logo: "assets/google-chat.svg", service: "chat" },
];
const ATTENDANCE_CHAT_GUIDE_URL = "";  // 유튜브 안내 영상 — 링크가 생기면 여기만 채운다
function loadChatStatus(force) {
  if (!force && S.chatStatus != null) return;  // 결과가 없을 때(null)만 새로 묻는다
  S.chatStatus = "loading";
  call("attendance_chat_status")
    .then((data) => { S.chatStatus = data; })
    .catch(() => { S.chatStatus = { connected: false, registered: false, account: "", class_space_name: "", reason: "" }; })
    .finally(render);
}
/* 연결하기 뒤 재확인 — 출결 탭에 있는 동안 3초 간격, 최대 10분 */
let chatPollTimer = null;
let chatPollUntil = 0;
function stopChatConnectPoll() {
  if (chatPollTimer) { clearTimeout(chatPollTimer); chatPollTimer = null; }
}
function startChatConnectPoll() {
  stopChatConnectPoll();
  chatPollUntil = Date.now() + 10 * 60 * 1000;
  const tick = async () => {
    chatPollTimer = null;
    const onTab = S.connectTab === "attendance" &&
      ((S.mode === "edit" && S.edit === "connect") || (S.mode === "wizard" && WIZARD_CARD_BY_STEP[S.step] === "connect"));
    if (!onTab || Date.now() > chatPollUntil) return;
    try {
      const data = await call("attendance_chat_status");
      S.chatStatus = data;
      if (data.connected) { showToast("Google Chat 연결이 끝났어요"); return; }
    } catch (error) { /* 다음 틱에 다시 */ }
    render();
    chatPollTimer = setTimeout(tick, 3000);
  };
  chatPollTimer = setTimeout(tick, 3000);
}
function classSpaceRowHtml(a) {
  const cs = S.chatStatus;
  if (a.state !== "ready" || !cs || cs === "loading" || !cs.connected) return "";
  if ((S.draft.profile["담임여부"] || (S.profileCache || {})["담임여부"]) !== "예") return "";
  if (S.chatSpaces === undefined) {
    S.chatSpaces = null;
    call("attendance_chat_spaces")
      .then((rows) => { S.chatSpaces = rows; })
      .catch(() => { S.chatSpaces = []; })
      .finally(render);
  }
  const current = S.chatSpaceName !== undefined ? S.chatSpaceName : (cs.class_space_name || "");
  let control;
  if (S.chatSpaces === null) {
    control = `<span class="st">방 목록을 가져오는 중이에요…</span>`;
  } else if (!S.chatSpaces.length) {
    control = `<span class="st">들어가 있는 Google Chat 방을 찾지 못했어요</span>`;
  } else {
    const options = S.chatSpaces.map((s) =>
      `<option value="${esc(s.name)}" ${s.displayName === current ? "selected" : ""}>${esc(s.displayName)}</option>`
    ).join("");
    control = `<select data-action-change="class-space-pick">
      <option value="">${current ? esc(current) : "학급 단톡방을 골라 주세요"}</option>${options}</select>`;
  }
  return `<div class="class-space-row">
    <span class="nameblock"><b>학급 단톡방</b><small>단체 문자를 보낼 방이에요</small></span>${control}</div>`;
}
function attendanceServiceRow(entry, a) {
  let state;
  let note = "";
  if (entry.service === "chat") {
    const cs = S.chatStatus;
    let stateHtml;
    if (a.state !== "ready") {
      stateHtml = `<span class="attendance-state muted">준비 뒤 연결할 수 있어요</span>`;
    } else if (!cs || cs === "loading") {
      stateHtml = `<span class="attendance-state muted">확인 중…</span>`;
    } else if (cs.connected) {
      stateHtml = `<span class="attendance-state">연결됨</span>`;
    } else {
      stateHtml = `<span class="attendance-state chat">처음 한 번 연결이 필요해요</span>` +
        `<button class="btn-tonal" data-action="chat-connect" data-busy-text="여는 중…">연결하기</button>`;
    }
    const guide = `<button class="btn-tonal youtube" data-action="chat-guide">${icon("youtube", "small")} 연결방법</button>`;
    state = stateHtml + guide;
  } else if (a.state === "ready") {
    state = `<span class="attendance-state">${entry.service === "sheet" ? "준비됨" : "연결됨"}</span>`;
  } else if (a.state === "failed" && a.failed_service === entry.service) {
    state = `<span class="attendance-state bad">준비 실패</span>`;
    note = `<span class="field-error">${esc(a.detail || "")}</span>`;
  } else if (a.state === "failed") {
    state = `<span class="attendance-state muted">준비되지 않음</span>`;
  } else {
    // not-ready·login-required 등 준비 전 상태 — "준비 완료"로 보이면 안 된다.
    state = `<span class="attendance-state muted">준비 전이에요</span>`;
  }
  return `<div class="attendance-service">
    <img class="service-logo" src="${entry.logo}" alt="${esc(entry.name)} 로고">
    <span class="nameblock"><b>${esc(entry.role)}</b><small>${esc(entry.name)}</small>${note}</span>
    ${state}</div>`;
}
function loadAttendanceStatus() {
  if (S.attendance || S.attendanceLoading) return;
  S.attendanceLoading = true;
  call("attendance_status")
    .then((data) => { S.attendance = data; })
    .catch((error) => {
      S.attendance = {
        state: "failed", account: "", current_user: "", spreadsheet_url: "",
        detail: error.message, failed_service: "setup", created: false,
      };
    })
    .finally(() => { S.attendanceLoading = false; render(); });
}
function attendanceTabHtml() {
  loadAttendanceStatus();
  if (S.attendance && S.attendance.state === "ready") loadChatStatus(false);
  const a = S.attendance;
  if (!a || typeof a !== "object") return `<p class="sub">출결 준비 상태를 확인하는 중이에요…</p>`;
  const account = a.account || a.current_user || "";
  let statusArea = "";
  if (a.state === "login-required" || a.state === "gws-required") {
    statusArea = `<div class="banner warn"><span>설정에서 Google 로그인을 마쳐 주세요</span>
      <button class="btn-quiet" data-action="goto-settings">설정 열기</button></div>`;
  } else if (a.state === "profile-required"
      && (identityIssues().length || dayIssues().length)) {
    // 초안이 진짜 비어 있을 때만 안내한다.
    statusArea = `<div class="banner warn"><span>${esc(a.detail || "내 정보와 하루 일과를 먼저 입력해 주세요.")}</span>
      <button class="btn-quiet" data-action="goto-identity">내 정보 열기</button></div>`;
  } else if (a.state === "failed" && (!a.failed_service || a.failed_service === "setup")) {
    statusArea = `<p class="field-error" style="margin:0 0 13px">${esc(a.detail || "출결 자료를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.")}</p>`;
  }
  const ready = a.state === "ready";
  const pendingGuide = S.mode === "wizard"
    ? "마지막 단계에서 모두 저장하고 적용하면 함께 준비해요"
    : "아래 버튼을 누르면 로그인한 계정에 자동으로 준비해요.";
  const chip = account ? `<span class="account-chip">${esc(account)}</span>` : "";
  const rows = ATTENDANCE_SERVICES.map((entry) => attendanceServiceRow(entry, a)).join("");
  return `${statusArea}
    <div class="attendance-head">
      <div><h2>출결 업무에 필요한 Google 항목</h2>${ready ? "" : `<p>${pendingGuide}</p>`}</div>
      <span class="attendance-head-right">${chip}${ready && a.spreadsheet_url
        ? `<button class="btn-quiet" data-action="link-open" data-url="${esc(a.spreadsheet_url)}">${icon("external-link", "small")} 출결 시트 열기</button>`
        : ""}</span>
    </div>
    <div class="promise">
      ${rows}
    </div>
    ${classSpaceRowHtml(a)}
    ${ready
      ? `<div class="ready-hero"><span class="check">✓</span>
          <span><b>출결 업무 준비가 끝났어요</b>${esc(account)} 계정에서 바로 사용할 수 있어요.</span></div>${newWorkbookSectionHtml()}`
      : S.mode === "wizard" ? ""
      : `<div class="attendance-action"><button class="btn" data-action="save-attendance" data-busy-text="준비 중…" ${S.attendanceSaving ? "disabled" : ""}>${S.attendanceSaving ? "준비 중…" : "출결 준비 시작하기"}</button></div>`}`;
}
function newWorkbookSectionHtml() {
  // 새 학년도 경로 — 처음 설정(마법사) 중에는 보이지 않는다.
  if (S.mode !== "edit") return "";
  if (!S.newWorkbookConfirm) {
    return `<div class="attendance-action" style="margin-top:6px">
      <button class="btn-quiet" data-action="new-attendance-ask">새 출결부 만들기 (새 학년도)</button></div>`;
  }
  return `<div class="banner warn" style="margin-top:12px"><span>기존 출결부는 구글 드라이브에 그대로 남아요.
      새 출결부를 만들면 이후 출결 기록은 새 출결부에 쌓이고,
      Chat 발송 연결과 학급 단톡방은 새 출결부에서 다시 연결해요.</span></div>
    <div class="attendance-action">
      <button class="btn" data-action="new-attendance-go" data-busy-text="만드는 중… (1~2분 걸릴 수 있어요)">새로 만들기</button>
      <button class="btn-quiet" data-action="new-attendance-cancel">취소</button>
    </div>`;
}
/* ---------- 연결 3탭: AI 비서 ---------- */
const AI_SKILL_COMMAND = "npx skills add rheps/teacher-manager -g --all";
function aiTabHtml() {
  if (!S.info?.features?.ai_skill_install_enabled) {
    return `<div class="attendance-head"><div>
      <h2>AI 비서 연결 <span class="tab-optional">(선택)</span></h2>
      <p>AI 연결 기능은 공개 준비 중이에요. 준비가 끝나면 업데이트로 알려드릴게요.</p>
    </div></div>`;
  }
  if (S.aiTools === null) {
    S.aiTools = "loading";
    call("ai_tools_status")
      .then((rows) => { S.aiTools = rows; })
      .catch(() => { S.aiTools = []; })
      .finally(render);
  }
  if (S.aiTools === "loading" || S.aiTools === null) {
    return `<p class="sub">이 컴퓨터의 AI 도구를 찾는 중이에요…</p>`;
  }
  const anyFound = S.aiTools.some((tool) => tool.found);
  const rows = S.aiTools.map((tool) => `
    <label class="ai-row${tool.found ? "" : " off"}">
      <input type="checkbox" name="ai-${esc(tool.key)}" ${tool.found ? "checked" : "disabled"}>
      <b>${esc(tool.name)}</b>
      <span class="st${tool.found ? " ok" : ""}">${tool.found ? "발견됨" : "설치 안 됨"}</span>
    </label>`).join("");
  let result = "";
  if (S.aiInstall) {
    result = S.aiInstall.success
      ? `<div class="ready-hero"><span class="check">✓</span>
          <span><b>AI 비서와 연결했어요.</b> 이제 AI에게 말로 학교 업무를 시킬 수 있어요.</span></div>`
      : `<div class="banner warn" style="margin-top:12px"><span>자동 연결이 안 됐어요. 아래 명령을 복사해 AI 도구의 터미널에 붙여넣으면 돼요.</span></div>
        <div class="ai-cmd"><code>${esc(AI_SKILL_COMMAND)}</code>
          <button class="btn-quiet" data-action="link-copy" data-url="${esc(AI_SKILL_COMMAND)}">복사</button></div>
        ${S.aiInstall.detail ? `<p class="hint">${esc(S.aiInstall.detail)}</p>` : ""}`;
  }
  return `<div class="attendance-head"><div>
      <h2>AI 비서와 연결할까요? <span class="tab-optional">(선택)</span></h2>
      <p>연결하면 AI에게 말로 학교 업무(일정·결석·신고서·문자)를 시킬 수 있어요. 안 써도 프로그램 사용에는 지장 없어요.</p>
    </div></div>
    <div class="ai-rows">${rows}</div>
    ${anyFound
      ? `<div class="attendance-action"><button class="btn" data-action="ai-connect" data-busy-text="연결하는 중… (1~2분 걸릴 수 있어요)">선택한 AI와 연결</button></div>`
      : `<p class="hint" style="margin-top:12px">이 컴퓨터에서 AI 도구를 찾지 못했어요. AI 도구를 설치한 뒤 이 탭에 다시 들어오면 돼요.</p>`}
    ${result}`;
}
bindActions({
  "connect-tab": (el) => {
    const tab = el.dataset.tab;
    if (S.connectTab === tab) return;
    if (editingCard() === "connect" && S.connectTab === "messenger") syncConnectFields();
    S.connectTab = tab;
    if (tab === "attendance") {
      // 탭을 열 때마다 다시 확인 — 로그인·준비 상태가 바뀌었을 수 있다.
      S.chatStatus = null;
      S.attendance = null;
    } else {
      stopChatConnectPoll();
    }
    if (tab === "ai") {
      // AI 도구도 열 때마다 다시 감지 — 그 사이 설치했을 수 있다.
      S.aiTools = null;
      S.aiInstall = null;
    }
    S.chatSpaces = undefined;
    S.chatSpaceName = undefined;
    render();
  },
  "update-check": async () => {
    try {
      const update = await call("get_update_info");
      S.updateInfo = update && update.available ? update : null;
      S.updateCheck = update && update.status ? update.status : "failed";
      if (S.updateCheck === "failed") {
        showToast((update && update.reason) || "업데이트 확인을 하지 못했어요");
      }
    } catch (error) {
      S.updateInfo = null;
      S.updateCheck = "failed";
      showToast("업데이트 확인을 하지 못했어요");
    }
    render();
  },
  "update-now": async () => {
    if (S.updating) return;  // 다운로드 중 재클릭 방지
    if (!S.updateInfo || !S.updateInfo.available) { showToast("먼저 업데이트를 확인해 주세요"); return; }
    S.updating = true; render();
    try {
      // 화면이 이미 아는 주소를 넘겨 재조회 없이 받는다(통신 깜빡임 오안내 방지).
      const result = await call("start_update", S.updateInfo.url, S.updateInfo.latest, S.updateInfo.sha256);
      if (!result.started) { S.updating = false; showToast(result.reason || "업데이트를 시작하지 못했어요"); render(); return; }
      setTimeout(() => { call("quit_app").catch(() => {}); }, 300);
      showToast("설치 파일을 확인했어요. 설치 창을 열게요.");
    } catch (error) {
      S.updating = false; showToast("업데이트를 시작하지 못했어요"); render();
    }
  },
  "ai-connect": async () => {
    if (!S.info?.features?.ai_skill_install_enabled) {
      showToast("AI 연결 기능은 공개 준비 중이에요");
      return;
    }
    const keys = Array.from(document.querySelectorAll('.ai-row input:checked'))
      .map((box) => box.name.replace(/^ai-/, ""));
    if (!keys.length) { showToast("연결할 AI를 하나 이상 선택해 주세요"); return; }
    S.aiInstall = await call("ai_skills_install", keys);
    render();
  },
  "save-attendance": async () => {
    if (S.attendanceSaving) return;
    S.attendanceSaving = true;
    render();
    try {
      S.attendance = await call("ensure_attendance");
      S.checks = await call("home_checks");
      S.chatStatus = null;
      if (S.attendance.state === "ready") showToast("출결 업무 준비를 마쳤어요");
    } finally {
      S.attendanceSaving = false;
      render();
    }
  },
  "new-attendance-ask": () => { S.newWorkbookConfirm = true; render(); },
  "new-attendance-cancel": () => { S.newWorkbookConfirm = false; render(); },
  "new-attendance-go": async () => {
    const data = await call("start_new_attendance");
    S.newWorkbookConfirm = false;
    S.attendance = data;
    // 새 출결부는 발송 연결·단톡방이 새로 시작된다 — 상태를 다시 확인한다.
    S.chatStatus = null;
    S.chatSpaces = undefined;
    S.chatSpaceName = undefined;
    stopChatConnectPoll();
    if (data.state === "ready") showToast("새 출결부를 만들었어요");
    else setBanner("warn", data.detail || "새 출결부를 만들지 못했어요. 다시 시도해 주세요.");
    render();
  },
  "chat-connect": async () => {
    await call("attendance_chat_connect");
    showToast("브라우저에서 구글 허용을 마쳐 주세요");
    startChatConnectPoll();
  },
  "chat-guide": () => {
    if (ATTENDANCE_CHAT_GUIDE_URL) { call("open_url", ATTENDANCE_CHAT_GUIDE_URL); return; }
    showToast("안내 영상을 준비 중이에요");
  },
  "goto-identity": async () => {
    if (S.mode === "wizard") { await goStepAsync(2); return; }
    await openCard("identity");
  },
});
function syncConnectFields() {
  const p = S.draft.profile;
  const nameById = {
    cal: Object.fromEntries(S.lists.calendars.map((o) => [o.id, o.name])),
    task: Object.fromEntries(S.lists.tasklists.map((o) => [o.id, o.name])),
  };
  for (const [kind, fields] of [["cal", CAL_LINK_FIELDS], ["task", taskLinkFields()]]) {
    const known = kind === "cal" ? S.lists.calendars : S.lists.tasklists;
    for (const [idField, nameField] of fields) {
      const select = document.querySelector(`select[name="${idField}"]`);
      if (select) {
        // 토큰이 풀려 목록을 못 불러온 동안에는, 비어 보이는 select가
        // 이미 골라 둔 값을 지우지 않게 한다.
        if (!select.value && p[idField] && (S.listsError || !known.length)) continue;
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
  if (event.target.matches("[data-link-select]")) { syncConnectFields(); render(); return; }
  if (event.target.matches('[data-action-change="class-space-pick"]')) {
    const select = event.target;
    const spaceName = select.value;
    if (!spaceName) return;
    const label = select.options[select.selectedIndex].textContent;
    call("attendance_chat_set_space", spaceName, label)
      .then(() => { S.chatSpaceName = label; showToast("학급 단톡방을 골랐어요"); })
      .catch((error) => setBanner("error", error.message));
  }
});
function stepConnect() {
  if (!S.google) {
    call("google_status").then((data) => { S.google = data; render(); }).catch((e) => setBanner("error", e.message));
    return `<h1>연결</h1><p class="sub">상태를 확인하는 중이에요…</p>`;
  }
  const locked = !S.google.logged_in;
  if (!locked && S.connectTab === "messenger" && !S.listsLoaded && !S.linkLoading && !S.listsError &&
      (linkModes().cal === "existing" || linkModes().task === "existing")) {
    loadLinkLists();
  }
  const body = S.connectTab === "attendance" ? attendanceTabHtml()
    : S.connectTab === "ai" ? aiTabHtml() : messengerTabHtml();
  return `
    <h1>연결</h1>
    <p class="sub">메신저 내용 정리와 출결 시트를 각각 설정해요.</p>
    ${connectTabsHtml()}
    ${body}`;
}
async function validateConnect() {
  if (!S.google) S.google = await call("google_status");
  if (!S.google.logged_in) return "구글 로그인을 마쳐야 다음으로 갈 수 있어요.";
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
  return "";
}
/* '새로 만들기'를 고른 캘린더·Tasks 목록을 구글에 실제로 만들고 그 ID를 프로필에 채운다.
   구글에 무언가를 만드는 일이라 타자 한 자마다 돌면 안 된다 — 마법사에서 [다음]을 누를 때와
   연결 화면에서 [< 홈]으로 나갈 때만 부른다. */
async function provisionConnectTargets() {
  const p = S.draft.profile;
  const homeroom = p["담임여부"] === "예";
  const modes = linkModes();
  if (modes.cal === "new") {
    const work = await call("ensure_calendar_named", p["업무캘린더이름"]);
    p["업무캘린더ID"] = work.id;
    const school = await call("ensure_calendar_named", p["학사일정캘린더이름"]);
    p["학사일정캘린더ID"] = school.id;
  }
  if (modes.task === "new") {
    const workTasks = await call("ensure_tasklist_named", p["업무Tasks목록이름"]);
    p["업무Tasks목록ID"] = workTasks.id;
    if (homeroom) {
      const homeTasks = await call("ensure_tasklist_named", p["담임안내Tasks목록이름"]);
      p["담임안내Tasks목록ID"] = homeTasks.id;
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
async function refreshSettingsStatus() {
  // 컴퓨터 → Google → 로그인 시 Calendar·Tasks 목록까지 한 번에 재점검한다.
  S.computer = await call("computer_status");
  S.google = await call("google_status");
  if (S.google && S.google.logged_in) {
    try {
      const [calendars, tasklists] = await Promise.all([call("list_calendars"), call("list_tasklists")]);
      S.lists = { calendars, tasklists };
      S.listsLoaded = true;
      S.listsError = false;
      // 새 목록에도 기존 선택 ID가 있으면 select가 같은 값으로 유지된다.
    } catch (error) {
      // 한 목록이라도 실패하면 기존 목록과 선택 ID를 그대로 둔다.
    }
  }
  render();
}
function computerSectionHtml() {
  const c = S.computer;
  if (!c) return `<div class="panel"><div class="row"><span class="st">준비 상태를 확인하는 중이에요…</span></div></div>`;
  return `<div class="section-h section-head"><span>컴퓨터 준비</span><span>처음 실행할 때 자동으로 준비해요</span></div>
    <div class="panel">
      ${readinessRow("Windows 자동 설치 기능", "없는 프로그램을 자동으로 준비해요", c.installer)}
      ${readinessRow("Python", "Teacher Manager를 실행해요", c.python)}
      ${readinessRow("Node.js", "구글 업무 도구를 준비해요", c.node, "install-node")}
      ${readinessRow("문서 읽기 도구", "PDF와 첨부 문서를 읽어요", c.documents)}
      ${readinessRow("Microsoft Edge WebView2", "Teacher Manager 화면을 보여줘요", c.screen)}
      ${googleLoginRowsHtml()}
    </div>
    ${goeduWarnHtml()}
    ${loginWaitHtml()}
    <div class="action-line"><button class="btn-quiet" data-action="settings-refresh" data-busy-text="점검 중…">다시 점검</button></div>
    <p class="settings-check-note">다시 점검 한 번으로 컴퓨터 준비와 Google 연결을 함께 확인해요.</p>`;
}
function googleLoginRowsHtml() {
  const g = S.google;
  if (!g) return `<div class="row"><span class="nameblock"><b>Google Workspace CLI</b><small>Calendar·Tasks·Sheet를 연결해요</small></span><span class="st">확인 중이에요…</span></div>`;
  const loginError = fieldError("google-login");
  const cliRight = g.gws
    ? `<span class="st ok">준비됐어요</span>`
    : `<button class="btn-tonal" data-action="install-gws" data-busy-text="진행 중…">설치하기</button>`;
  const cliRow = `<div class="row"><span class="nameblock"><b>Google Workspace CLI</b><small>Calendar·Tasks·Sheet를 연결해요</small></span><span class="row-actions">${cliRight}</span></div>`;
  const loginBlock = `<span class="nameblock"><b>Google 로그인</b><small>이 계정에 출결 업무를 준비해요</small></span>`;
  const loginRow = S.login
    ? `<div class="row">${loginBlock}<span class="st">진행 중…</span></div>`
    : g.logged_in
      ? `<div class="row">${loginBlock}<span class="row-actions"><span class="st ok">${esc(g.user || "완료")}</span><button class="btn-quiet" data-action="gws-logout">로그아웃</button></span></div>`
      : `<div class="row${loginError ? " problem-row" : ""}">${loginBlock}<span class="row-actions">${loginError ? `<span class="field-error">${esc(loginError)}</span>` : ""}<button class="btn-tonal" data-action="gws-login" data-busy-text="진행 중…">로그인</button></span></div>`;
  return `${cliRow}
    ${loginRow}`;
}
function loginWaitHtml() {
  if (!S.login) return "";
  return `<div class="panel" style="margin-top:12px">
      <p class="sub" style="margin:0 0 8px">브라우저가 자동으로 열렸어요.
        <b class="login-account-em">반드시 경기도교육청 클라우드 계정(@goedu.kr)으로 로그인해 주세요.</b><br>
        개인 계정 화면이 열리면 [다른 계정 사용]을 눌러 @goedu.kr 계정을 고르면 돼요.
        창이 안 열렸으면 아래 주소로 직접 여세요.</p>
      ${S.login.url ? linkRow(S.login.url) : `<p class="sub">로그인 주소를 준비하는 중이에요…</p>`}
      <div class="action-line"><button class="btn-quiet" data-action="login-cancel">취소</button></div>
    </div>`;
}
function goeduWarnHtml() {
  const g = S.google;
  if (!g || !g.logged_in || !g.user || g.user.endsWith("@goedu.kr")) return "";
  return `<div class="banner warn">경기도교육청 계정(@goedu.kr)이 아니에요 — 일부 기능이 제한될 수 있어요.</div>`;
}
async function refreshComputerStatus() {
  S.computer = await call("computer_status");
  render();
}
function ensureComputerStatus() {
  if ((S.computer && S.google) || S.computerLoading) return;
  S.computerLoading = true;
  Promise.all([
    S.computer ? Promise.resolve(S.computer) : call("computer_status"),
    S.google ? Promise.resolve(S.google) : call("google_status"),
  ])
    .then(([computer, google]) => { S.computer = computer; S.google = google; })
    .catch((error) => { S.banner = { kind: "error", text: error.message }; })
    .finally(() => { S.computerLoading = false; render(); });
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
    </div>${statusLine}
    <div class="action-line" style="margin-top:8px; justify-content:flex-start">
      <button class="btn-quiet" data-action="attachment-folder-default">기본 폴더로 되돌리기</button></div></div>`);
}
function settingsSectionHtml(d) {
  // 이 화면에서 확인: Windows 자동 설치 기능 · Python · Node.js · Microsoft Edge WebView2
  ensureComputerStatus();
  if (d.autostart === undefined) d.autostart = true;
  ensureHotkeyState(d);
  return `${computerSectionHtml()}
    <div class="section-h">동작 설정</div>
    ${formTable(
      hotkeyRow(d) +
      rawRow("Windows 시작 시 자동 실행 (권장)",
        `<div class="field"><label class="check" style="margin:0"><input type="checkbox" name="autostart" ${d.autostart ? "checked" : ""}> 켜기</label></div>`) +
      attachmentFolderRow(d)
    )}
    <p class="hint">오래된 Word·Excel·PowerPoint 파일은 Microsoft Office가 설치되어 있어야 안정적으로 읽을 수 있어요.</p>
    <p class="hint">메시지 본문과 읽어낸 첨부 내용은 분석을 위해 Gemini로 전송돼요. 사진과 스캔 PDF는 파일 화면도 함께 전송돼요.</p>`;
}
function stepSettings() {
  return `
    <h1>이 컴퓨터에서의 동작을 정할게요</h1>
    <p class="sub">Brity 대화방에 메시지를 띄우고 단축키를 누르면 화면에서 직접 읽어 바로 구글에 등록해요.</p>
    ${settingsSectionHtml(S.draft.bridge)}`;
}
function syncMessengerDraft() {
  S.draft.bridge.hotkey = S.draft.bridge.hotkey || DEFAULT_HOTKEY;
  const auto = document.querySelector('[name="autostart"]');
  if (auto) S.draft.bridge.autostart = auto.checked;
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
  try {
    S.google = await call("google_status");
  } catch (error) {
    S.google = S.google || null;
  }
  if (!S.google || !S.google.gws) {
    rows.push(issue("settings.gws-cli", "gws-cli", "Google Workspace CLI를 준비해 주세요."));
  } else if (!S.google.logged_in) {
    rows.push(issue("settings.google-login", "google-login", FIELD_MESSAGES["google-login"]));
  }
  setFieldIssues(rows);
  if (rows.length) render();
  return firstIssueMessage(rows);
}

// 숫자에 직접 기대지 않도록 함수를 이름으로 둔 뒤 배선한다.
stepBodies[5] = stepSettings;
validators[5] = validateSettings;
stepBodies[6] = stepConnect;
validators[6] = validateConnect;

bindActions({
  "settings-refresh": () => refreshSettingsStatus(),
  "goto-settings": async () => {
    if (S.mode === "wizard") { await goStepAsync(5); return; }
    await openCard("settings");
  },
  "install-node": async () => {
    const result = await call("install_node");
    await refreshComputerStatus();
    if (!result.success) throw new Error("자동 설치에 실패했어요: " + result.detail);
    showToast("Node.js 준비를 마쳤어요");
  },
  "attachment-folder-choose": async () => {
    syncMessengerDraft();
    const result = await call(
      "choose_attachment_folder", S.draft.bridge.brity_download_dir || ""
    );
    if (result.cancelled) return;
    S.draft.bridge.brity_download_dir = result.path;
    S.attachmentFolderStatus = await call("check_attachment_folder", result.path);
    render();
    await autoSaveSettings();
  },
  "attachment-folder-default": async () => {
    S.draft.bridge.brity_download_dir = DEFAULT_ATTACHMENT_FOLDER;
    S.attachmentFolderStatus = await call(
      "check_attachment_folder", DEFAULT_ATTACHMENT_FOLDER
    );
    render();
    await autoSaveSettings();
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
    const kind = r.status === "ok" ? "g" : r.status === "rate-limited" ? "y" : "r";
    S.keyStatus = { kind, text: KEY_MESSAGES[r.status] || r.status };
    render();
  },
});

/* ---------- 7단계: 마무리 ---------- */
function summaryRow(label, value) {
  return `<div class="row"><span class="name">${esc(label)}</span><span class="st">${esc(value || "—")}</span></div>`;
}
stepBodies[7] = function stepFinish() {
  const p = S.draft.profile;
  return `
    <h1>모두 저장하고 적용할게요</h1>
    <p class="sub">저장 → 설정 확인 → 출결 자동화 설치 → 도우미 시작까지 한 번에 하고, 끝나면 홈으로 가요</p>
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
      <button class="btn-back" data-action="go-prev">${icon("chevron-left", "small")} 이전</button>
      <button class="btn" data-action="apply-all" data-busy-text="적용하는 중… (1~2분 걸릴 수 있어요)">모두 저장하고 적용</button>
    </div>`;
};
bindActions({
  "apply-all": async () => {
    // 마무리 화면엔 격자 입력이 없다 — draft에 저장된 최신 값을 그대로 쓴다.
    await ensureGridLoaded();
    const results = await call("apply_all", S.draft.profile, S.draft.grid, S.draft.bridge);
    const failed = results.filter((r) => r.status === "failed");
    // 성공이든 실패든 곧장 홈으로 — 실패 항목은 홈 점검과 출결 탭이 이유를 보여준다.
    await call("finish_setup");
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
const WIZARD_CARD_BY_STEP = { 2: "identity", 3: "identity", 4: "timetable", 5: "settings", 6: "connect" };
function editingCard() {
  if (S.mode === "edit") return S.edit || "";
  if (S.mode === "wizard") return WIZARD_CARD_BY_STEP[S.step] || "";
  return "";
}
function effectiveChecks() {
  const editing = editingCard();
  const saved = uniqueChecks(S.checks);
  const kept = editing
    ? saved.filter((row) => !(row.card === editing && EDITABLE_TARGETS.has(row.target)))
    : saved;
  const live = Object.values(S.fieldIssues || {}).map((row) => ({
    key: row.key, label: row.target, ok: false, detail: "", fix: row.message,
    card: editing || "", tab: row.tab || "", target: row.target,
  }));
  return uniqueChecks(kept.concat(live));
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
  return { good: counted.length - bad, total: counted.length, bad };
}

/* ---------- 입력 즉시 재검사: 문제 표시와 숫자를 함께 줄인다 ---------- */
function currentScreenIssues() {
  if (S.mode === "edit") {
    if (S.edit === "identity") { syncProfileFields(); syncDayFields(); return identityIssues().concat(dayIssues()); }
    if (S.edit === "connect") { syncConnectFields(); return connectIssues(); }
    return null;
  }
  if (S.mode === "wizard") {
    if (S.step === 2) { syncProfileFields(); return identityIssues(); }
    if (S.step === 3) { syncDayFields(); return dayIssues(); }
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
  if (it.kind === "notice" && it.result === "created") return ["확인필요", "review"];
  return ITEM_RES[it.result] || ["", "dup"];
}
function groupedItemsHtml(cap) {
  return RESULT_GROUPS.map((group) => {
    const items = (cap.items || []).filter((item) => item.kind === group.kind);
    if (!items.length) return "";
    const rows = items.map((it) => {
      const [label, cls] = resultLabel(it);
      const when = it.kind === "calendar" ? fmtShort(it.when) : "";
      return `<div class="item"><span class="t">${esc(when)}</span>` +
        `<span class="title">${esc(it.target)} — ${esc(it.title)}</span>` +
        `<span class="res ${cls}">${esc(label)}</span></div>`;
    }).join("");
    const guide = group.kind === "notice"
      ? `<div class="sheet-guide">아직 학생에게 보내지 않았어요. Google Sheet에서 대상과 내용을 확인한 뒤 발송 대기로 바꿔 주세요.</div>`
      : "";
    return `<section class="result-group"><div class="result-group-head">${esc(group.title)} ${items.length}건</div>${rows}${guide}</section>`;
  }).join("");
}
function attachmentResultLead(cap) {
  const count = Number(cap.attachment_count || 0);
  if (!count) return "";
  return `<div class="attachment-result-lead">메시지 본문과 첨부파일 ${count}개를 함께 읽었어요.</div>`;
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
  const items = attachmentResultLead(cap) + groupedItemsHtml(cap);
  const emptyLine = !(cap.items || []).length && cap.ok
    ? `<div class="item"><span class="none">${esc(EMPTY_ITEM_LINES[cap.stage] || "만든 항목이 없어요.")}</span></div>` : "";
  const reason = cap.reason ? `<div class="cap-reason">${esc(cap.reason)}</div>` : "";
  return `<div class="cap${open}">
    <button class="cap-row" data-action="cap-toggle" data-key="${esc(key)}">
      ${dot}<span class="cap-when">${esc(fmtShort(cap.when))}</span><span class="cap-sum">${esc(cap.summary || "")}</span>${fresh}${trial}<span class="chev">▶</span>
    </button>
    <div class="cap-items">${items}${emptyLine}</div>${reason}</div>`;
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
  return `<div class="panel">${S.caps.map(capHtml).join("")}</div>`;
}
bindActions({
  "cap-toggle": (el) => {
    const key = el.dataset.key;
    S.capsOpen[key] = !S.capsOpen[key];
    render();
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
    try {
      S.caps = await call("recent_captures");
      S.freshWhen = S.caps.length ? S.caps[0].when : "";
    } catch (error) { /* 기록 갱신 실패는 다음 진입 때 */ }
    if (S.mode === "home") render();
  }, 2500);
}

/* ---------- 홈 ---------- */
const CARDS = [
  { key: "identity", icon: "user", title: "내 정보", detail: "이름 · 학교 · 담임 · 하루 일과" },
  { key: "timetable", icon: "table", title: "시간표", detail: "주간 시간표 수정" },
  { key: "settings", icon: "sliders", title: "설정", detail: "컴퓨터 준비 · Google 로그인 · 단축키 · 자동 실행" },
  { key: "connect", icon: "link", title: "연결", detail: "Calendar · Tasks · Gemini API key · 출결 시트" },
];
function cardStatus(card) {
  if (!S.checks.length) return { kind: "n", label: "점검 중…", bad: false };
  const summary = checkSummary(checksForCard(card.key));
  if (summary.bad) return { kind: "y", label: "확인 필요", bad: true, summary };
  return { kind: "g", label: card.key === "timetable" ? "저장됨" : "정상", bad: false };
}
function cardBadges(card) {
  const st = cardStatus(card);
  if (!st.bad) return badge(st.kind, st.label);
  return badge("y", st.label) + badge("n", `${st.summary.good}/${st.summary.total} 정상`);
}
function cardDetail(card) {
  return card.detail;
}
/* 점검 실패 → 배너 → 재렌더 → 재조회의 자기지속 루프를 끊는다:
   진행 중이면 겹쳐 부르지 않고, 실패 뒤에는 30초 지나야 자동 재조회한다. */
const checksRetry = { inflight: false, failedAt: 0 };
async function refreshChecks() {
  if (checksRetry.inflight) return;
  checksRetry.inflight = true;
  try {
    S.checks = await call("home_checks");
    checksRetry.failedAt = 0;
    render();
  } catch (error) {
    checksRetry.failedAt = Date.now();
    setBanner("error", error.message);
  } finally {
    checksRetry.inflight = false;
  }
}
function shouldAutoRefreshChecks() {
  if (S.checks.length || checksRetry.inflight) return false;
  return !checksRetry.failedAt || Date.now() - checksRetry.failedAt > 30000;
}
function renderHome() {
  const info = S.info;
  const problems = checkSummary(uniqueChecks(S.checks)).bad;
  const pill = !S.checks.length ? badge("n", "점검 중…")
    : problems ? badge("y", `확인할 항목 ${problems}개`) : badge("g", "모두 정상");
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
    ? `<div class="update-banner"><span><b>새 버전(${esc(S.updateInfo.latest)})이 나왔어요.</b> ${esc(S.updateInfo.notes || "")}</span>
        <button class="btn" data-action="update-now" ${S.updating ? "disabled" : ""} data-busy-text="받는 중… (1~2분)">${S.updating ? "받는 중…" : "지금 업데이트"}</button></div>`
    : "";
  root().innerHTML = `<div class="body"><div class="body-inner"><div class="page">
    ${bannerHtml()}
    ${updateBanner}
    ${firstNotice}
    <div class="hero"><span class="hi">${esc(name)}안녕하세요</span><span class="pill">${pill}</span></div>
    ${liveCardHtml()}
    <div class="tiles">${tiles}</div>
    <div class="section-h" style="margin-top:22px">처리한 메시지</div>
    <p class="sub" style="margin:-2px 0 12px">메시지를 정리해서 캘린더와 할일에 등록한 내역이에요</p>
    ${capListHtml()}
    <div class="infobar"><b>${esc(info.branding.name)} v${esc(info.version)}</b> · ${esc(info.branding.credit)}
      <span class="links">
        <button data-action="open-about">버전 및 제작 정보</button>
      </span></div>
  </div></div></div>` + toastHtml();
  if (shouldAutoRefreshChecks()) refreshChecks();
  if (!S.profileCache) call("read_profile").then((p) => { S.profileCache = p; render(); }).catch(() => {});
  if (S.caps === null) {
    call("recent_captures")
      .then((rows) => { S.caps = rows; S.capsError = false; render(); })
      .catch(() => { S.caps = []; S.capsError = true; render(); });
  }
  startCapturePoll();
}

/* ---------- 편집 화면 ---------- */
function backBarHtml() {
  return `<div class="backbar">
    <button class="btn-back" data-action="back-home">${icon("chevron-left", "small")} 홈</button>
    <span class="save-state" id="save-state"></span>
  </div>`;
}
/* 저장 상태 한 줄 — [< 홈] 오른쪽에 잠깐 나타난다.
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
async function autoSaveSettings(afterHotkey) {
  if (!(S.mode === "edit" && S.edit === "settings")) return; // 마법사는 마지막에 한꺼번에 적용
  if (settingsAutoSaveBusy) { settingsAutoSavePending = true; return; }
  settingsAutoSaveBusy = true;
  try {
    do {
      settingsAutoSavePending = false;
      syncMessengerDraft();
      const folderProblem = await validateAttachmentFolder();
      if (folderProblem) { setBanner("warn", folderProblem); return; }
      const updates = {
        hotkey: S.draft.bridge.hotkey || DEFAULT_HOTKEY,
        autostart: S.draft.bridge.autostart !== false,
        brity_download_dir: S.draft.bridge.brity_download_dir || DEFAULT_ATTACHMENT_FOLDER,
      };
      const result = await call("save_messenger", updates);
      if (!result.saved) {
        S.hk.status = { kind: "bad", text: result.reason };
        setBanner("warn", result.reason);
        render();
        return;
      }
      S.hk.current = result.hotkey;
      if (afterHotkey) S.hk.status = { kind: "ok", text: `${prettyHotkey(result.hotkey)} · 저장했어요` };
      S.checks = [];
      showToast("저장했어요 — 도우미가 새 설정으로 실행 중이에요");
      render();
    } while (settingsAutoSavePending);
  } finally {
    settingsAutoSaveBusy = false;
  }
}
document.addEventListener("change", (event) => {
  if (!(S.mode === "edit" && S.edit === "settings")) return;
  const name = event.target && event.target.name;
  if (name === "autostart" || name === "brity_download_dir") {
    // 저장 예외(레지스트리 거부 등)가 무통지로 사라지면 화면과 실제 값이 어긋난다.
    autoSaveSettings().catch((error) => setBanner("error", error.message));
  }
});

/* 내 정보·시간표·연결 자동 저장 — 저장 버튼 없이, 값을 바꾸면 잠깐 뒤 저장한다.
   마법사(S.mode === "wizard")는 마지막에 한꺼번에 적용하므로 여기서 건드리지 않는다. */
const AUTO_SAVE_SCREENS = ["identity", "timetable", "connect"];
const AUTO_SAVE_DELAY_MS = 700;  // 타자를 치는 중간중간 저장하지 않을 만큼만 기다린다
let editAutoSaveTimer = null;
let editAutoSaveBusy = false;
let editAutoSavePending = false;
/* 사람이 실제로 손댄 적이 있는지. 화면을 열어 보기만 하고 나오는 것은 저장할 일이 아니다 —
   그때도 저장하면 홈 점검 결과를 버리게 되어, 되돌아갈 때마다 홈이 처음부터 다시 점검한다. */
let editDirty = false;
function autoSaveScreen() {
  return S.mode === "edit" && AUTO_SAVE_SCREENS.includes(S.edit) ? S.edit : null;
}
async function autoSaveEdit(options) {
  const leaving = !!(options && options.leaving);
  const key = autoSaveScreen();
  if (!key) return true;
  if (!editDirty) return true;  // 고친 게 없다
  if (editAutoSaveBusy) { editAutoSavePending = true; return true; }
  // 여기서 미리 내린다 — 저장하는 동안 들어온 수정은 다시 올라가서 한 번 더 저장된다.
  // 저장이 끝난 뒤에 내리면 그 사이의 수정을 놓친다.
  editDirty = false;
  editAutoSaveBusy = true;
  showSaveState("저장 중…");
  try {
    do {
      editAutoSavePending = false;
      syncEditFields(key);
      await ensureGridLoaded();
      // 연결 화면은 링크가 다 차 있는지까지 보고(require_links), Gemini 값도 함께 저장한다.
      await call("save_profile_grid", S.draft.profile, S.draft.grid, key === "connect");
      if (key === "connect") {
        const messenger = await call("save_messenger", {
          gemini_api_key: S.draft.bridge.gemini_api_key || "",
          gemini_model: S.draft.bridge.gemini_model || "gemini-3.5-flash",
        });
        if (!messenger.saved) { setBanner("warn", messenger.reason); return false; }
        // Gemini key는 이 컴퓨터뿐 아니라 출결 시트 설정 탭에도 들어가야 시트에서 다시 묻지
        // 않는다. 타자를 칠 때마다 경고를 띄우면 쓰던 것이 끊기므로 나갈 때만 알린다.
        const push = messenger.sheet_push;
        if (leaving && push && push.state === "failed") setBanner("warn", "저장했어요. 다만 " + push.detail);
      }
      // 홈 점검과 프로필 사본은 다음에 홈으로 갈 때 새로 읽는다.
      S.checks = [];
      S.profileCache = null;
    } while (editAutoSavePending);
    showSaveState("저장됨", 2500);
    return true;
  } finally {
    editAutoSaveBusy = false;
  }
}
function scheduleEditAutoSave() {
  if (!autoSaveScreen()) return;
  clearTimeout(editAutoSaveTimer);
  editAutoSaveTimer = setTimeout(() => {
    autoSaveEdit().catch((error) => setBanner("error", error.message));
  }, AUTO_SAVE_DELAY_MS);
}
/* [< 홈]을 누르면 기다리던 저장을 지금 끝내고, 끝난 뒤에 홈으로 간다. */
/* 저장까지 마쳤으면 true. 저장이 실패했으면 false — 그때는 홈으로 보내지 않는다.
   이유가 적힌 배너를 못 보고 지나치면 무엇이 안 됐는지 알 길이 없다. */
async function flushEditSave() {
  clearTimeout(editAutoSaveTimer);
  if (S.mode !== "edit") return true;
  if (S.edit === "settings") { await autoSaveSettings(); return true; }
  if (!autoSaveScreen()) return true;
  if (!editDirty) return true;  // 열어 보기만 하고 나온다 — 저장할 것도, 만들 것도 없다
  if (S.edit === "connect") {
    // '새로 만들기'를 고른 캘린더·Tasks 목록은 여기서 실제로 만들어 ID를 채운다.
    // 타자를 칠 때마다 만들면 안 되므로 나갈 때 한 번만 한다. 실패해도 입력한 값은
    // 그대로 저장하고 홈으로 보낸다 — 남은 문제는 홈 점검이 알려 준다.
    try { await provisionConnectTargets(); } catch (error) { /* 홈 점검이 알려 준다 */ }
  }
  return await autoSaveEdit({ leaving: true });
}
for (const eventName of ["input", "change"]) {
  document.addEventListener(eventName, () => {
    if (!autoSaveScreen()) return;
    editDirty = true;
    scheduleEditAutoSave();
  });
}
function settingsEditBody() {
  const helper = S.checks.find((c) => c.key === "settings.helper");
  const helperRow = helper
    ? `<div class="panel" style="margin-top:16px"><div class="row"><span class="name">도우미 실행 상태</span><span class="st">${esc(helper.detail)}</span></div></div>`
    : "";
  return `
    <h1>설정</h1>
    <p class="sub">단축키와 자동 실행을 관리해요. 바꾸면 바로 저장되고 도우미가 새 설정으로 다시 시작해요.</p>
    ${settingsSectionHtml(S.draft.bridge)}
    ${helperRow}`;
}
// 업데이트 상태·버튼 — '버전 및 제작 정보' 화면의 한 줄에서 쓴다.
function updateControls() {
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
function renderEdit(key) {
  let body = "";
  if (key === "connect") body = stepConnect();
  else if (key === "identity") body = stepBodies[2]() + `<div class="section-h" style="margin-top:26px">하루 일과</div>` + stepBodies[3]().replace(/^[\s\S]*?<\/p>/, "");
  else if (key === "timetable") body = stepBodies[4]();
  else if (key === "settings") body = settingsEditBody();
  // 편집 화면에는 저장 버튼이 없다 — 네 화면 모두 바꾸는 즉시 저장하고,
  // [< 홈]을 누르면 기다리던 저장을 끝낸 뒤에 홈으로 간다(flushEditSave).
  root().innerHTML = `<div class="body"><div class="body-inner"><div class="page">
    ${backBarHtml()}${bannerHtml()}${body}</div></div></div>` + toastHtml();
}
function syncEditFields(key) {
  if (key === "identity") { syncProfileFields(); syncDayFields(); }
  if (key === "timetable") syncGridFields();
  if (key === "connect") syncConnectFields();
  if (key === "settings") syncMessengerDraft();
}
async function loadForEdit(key) {
  const profile = await call("read_profile");
  S.profileCache = profile;
  // 저장본이 이긴다 — 저장 없이 홈으로 나가며 버린 편집이 재진입 화면에
  // 남거나 연결 화면 저장에 편승 커밋되면 안 된다. (bridge·grid와 같은 규칙)
  S.draft.profile = Object.assign({}, profile);
  if (key === "timetable" || key === "identity") S.draft.grid = await call("read_grid");
  if (key === "connect" || key === "settings") {
    const settings = await call("get_messenger_settings");
    S.draft.bridge = Object.assign({}, settings);
    S.hk = {
      current: settings.hotkey || DEFAULT_HOTKEY,
      recording: false,
      status: null,
    };
  }
  // 설정 화면은 열 때마다 실제 상태를 다시 확인한다 — 홈 점검과 화면이 어긋나지 않게.
  if (key === "settings") refreshSettingsStatus().catch(() => {});
}

/* ---------- 버전 및 제작 정보 ---------- */
function renderAbout() {
  const b = S.info.branding;
  const u = updateControls();
  root().innerHTML = `<div class="body"><div class="body-inner"><div class="page">
    ${backBarHtml()}${bannerHtml()}
    <h1>${esc(b.name)}</h1>
    <p class="sub">${esc(b.tagline)}</p>
    <div class="panel">
      <div class="row"><span class="name">버전</span><span class="st">v${esc(S.info.version)}</span></div>
      <div class="row"><span class="name">만든 사람</span><span class="st">${esc(b.credit.replace("만든 사람: ", ""))}</span></div>
      <div class="row"><span class="name">게시자</span><span class="st">${esc(b.publisher)}</span></div>
      <div class="row"><span class="name">프로그램 업데이트</span>${u.st}${u.btn}</div>
    </div>
    <div class="section-h">웹사이트</div>
    ${linkRow(b.website)}
  </div></div></div>` + toastHtml();
}

async function openCard(key) {
  S.banner = null;
  S.firstHomeNotice = false;  // 카드에 다녀오면 처음 안내 띠는 접는다
  await loadForEdit(key);
  // 저장된 점검에서 이 카드의 문제를 실제 입력칸 오류로 옮겨 보여준다.
  S.fieldIssues = {};
  uniqueChecks(S.checks)
    .filter((c) => c.card === key && c.ok === false)
    .forEach((c) => { S.fieldIssues[c.target] = issue(c.key, c.target, c.fix || c.detail || c.label, c.tab); });
  S.mode = "edit"; S.edit = key; editDirty = false; render();
}
bindActions({
  "open-card": (el) => openCard(el.dataset.card),
  "back-home": async () => {
    await stopHotkeyRecording();
    // 저장이 끝난 뒤에 홈으로 간다 — 나가는 도중에 저장이 잘리면 방금 쓴 것이 사라진다.
    let saved = false;
    try { saved = await flushEditSave(); } catch (error) { setBanner("error", error.message); return; }
    if (!saved) return;  // 배너에 이유가 적혀 있다
    stopChatConnectPoll();
    S.fieldIssues = {};
    S.chatStatus = null;
    S.chatSpaces = undefined;
    S.chatSpaceName = undefined;
    S.mode = "home"; S.edit = null; S.banner = null; S.hk = null; render();
  },
  "open-about": () => { S.mode = "about"; render(); },
  "open-logs": () => call("open_logs"),
});

/* ---------- 라우터 ---------- */
function screenKey() { return `${S.mode}|${S.step}|${S.edit}|${S.connectTab}`; }
let lastScreenKey = "";
function render() {
  const onLoginScreen = (S.mode === "wizard" && S.step === 5) || (S.mode === "edit" && S.edit === "settings");
  if (S.login && !onLoginScreen) {
    stopLoginPoll();
    S.login = null;
    call("gws_login_cancel").catch(() => {});
  }
  if (S.mode !== "home") stopCapturePoll();
  if (S.mode === "loading") { root().innerHTML = '<div class="boot">여는 중이에요…</div>'; return; }
  // 같은 화면을 다시 그릴 때는 스크롤을 유지한다 — 세그먼트·선택 조작으로 위로 튀지 않게.
  // 마법사(.shell)는 .body가, 홈·편집 화면은 문서 전체가 스크롤되므로 둘 다 기억한다.
  const prevBody = document.querySelector(".body");
  const prevScroll = prevBody ? prevBody.scrollTop : 0;
  const prevDocScroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
  const sameScreen = lastScreenKey === screenKey();
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
  }
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
  let offer = null;
  try { offer = await call("update_offer"); } catch (_) { return; }
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
    S.updating = true; render();
    try {
      const result = await call("start_update", offer.url, offer.latest, offer.sha256);
      if (!result.started) {
        S.updating = false;
        showToast(result.reason || "업데이트를 시작하지 못했어요");
        render();
        return;
      }
      setTimeout(() => { call("quit_app").catch(() => {}); }, 300);
      showToast("설치 파일을 확인했어요. 설치 창을 열게요.");
    } catch (error) {
      // call()은 실패하면 던진다 — 감싸지 않으면 화면이 '받는 중…'에 멈춘 채 남는다.
      S.updating = false; showToast("업데이트를 시작하지 못했어요"); render();
    }
    return;
  }
  try {
    await call("decline_update", offer.latest);
  } catch (error) {
    // 오늘 그만 묻겠다는 기록을 못 남겨도 쓰던 일은 계속돼야 한다. 다음에 켤 때 다시 묻는다.
  }
}

async function boot() {
  try {
    const info = await call("get_app_info");
    S.info = info;
    S.mode = info.mode;
    S.step = Math.min(info.step || 1, WIZARD_STEPS.length);
    S.maxStep = Math.min(Math.max(info.max_step || S.step, S.step), WIZARD_STEPS.length);
    if (info.draft && typeof info.draft === "object") {
      S.draft = Object.assign({ profile: {}, grid: null, bridge: {} }, info.draft);
    }
    render();
    askUpdateOnStart();
    startLoginWatch();
  } catch (error) {
    root().innerHTML = `<div class="boot">${esc(error.message)}</div>`;
  }
}
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);

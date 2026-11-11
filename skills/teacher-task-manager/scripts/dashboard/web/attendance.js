/* 출결·메신저 창 (서버 출결 P2). Port of sheet-dashboard/dashboard.js to the server data model,
   built to the server attendance screens design and its mockup.

   window.TMAttendance.mount(container, options) returns { unmount() }. The window knows nothing of
   app.js. Everything it needs comes through options:

     dataCall(route, body)   Promise of { status, body, unknown }: the only server access. status is
                             HTTP; body is parsed JSON, coded errors as { error, message?, ... };
                             status 0 with unknown: true means a write got no answer.
     aiInput(args)           Promise of the dict of dashboard/attendance_ai.py run_attendance_ai_input.
                             args is { sentence, year } for a press, or { resume } to replay a
                             lost apply. One run can take minutes, so it needs no short time limit.
                             Only aiOutcome() below knows the shape of its answer.
     openLinked(target)      opens another TM window or the browser. target.kind is one of: roster
                             ({ studentId?, label?, focusEmail? }), brity, profile, relogin, reapprove,
                             external ({ url }). The window keeps its place for the next mount.
     onClose()               closes this window.
   Optional:
     pcInfo()                { hasGeminiKey: true | false | null, profile: { schoolName, grade,
                             classNumber, classLabel, teacherName } | null }
     initialRead             a /v1/data/read answer already in hand (the home tile's read)
     onRead(answer)          every read answer, for the home tile

   window.TMAttendance.homeSummary(answer) gives the home tile its badge and detail line from the same
   eligibility module. window.TMAttendance.forget() drops the saved place, the sentence and the locked
   requests; call it when the account changes.

   Server values are rendered only as text nodes or attributes (SHEET-DASH-04). A student's email never
   leaves prepare(): the screens only know whether one is registered (R17). */
(function () {
 function createAttendanceModule() {
  'use strict';
  if (!window.preact || !window.preactHooks || !window.htm) {
    // The vendor files did not load: a plain line instead of a page that throws (OBS-09).
    console.error('attendance window: vendor scripts missing');
    return {
      mount(container) { container.textContent = '화면을 새로 표시하지 못했어요. 프로그램을 다시 열어 주세요.'; return { canLeave() { return true; }, hasDialog() { return false; }, unmount() { container.textContent = ''; } }; },
      homeSummary() { return { tone: 'muted', text: '확인하지 못했어요', detail: '', count: null }; },
      forget() {}
    };
  }
  const h = window.preact.h;
  const render = window.preact.render;
  const hooks = window.preactHooks;
  const spaceLabel = text => { const t = String(text || ''), m = /^(\d{1,3})번?\s*([^\d\s].*)$/.exec(t); return m && !/학년|단톡방|채팅|스페이스/.test(t) ? m[1] + '번 ' + m[2] : t; };
  const useState = hooks.useState, useEffect = hooks.useEffect, useMemo = hooks.useMemo, useRef = hooks.useRef;
  const useLayoutEffect = hooks.useLayoutEffect, useErrorBoundary = hooks.useErrorBoundary;
  const html = window.htm.bind(h);

  const CATS = ['미인정', '질병', '출석인정', '기타'];
  const KINDS = ['결석함', '지각함', '조퇴함', '결과함'];
  const PERIODS = ['', '조회', '1교시', '2교시', '3교시', '4교시', '5교시', '6교시', '7교시', '종례'];
  const DOCV = ['', '제출', '미제출', '해당없음'];
  const DOC_BULK = ['그대로', '제출', '미제출', '해당없음', '(비움)'];
  const HOL_KINDS = ['공휴일', '대체공휴일', '선거일', '재량휴업일', '개교기념일', '기타'];
  const MONTHS = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2];
  const CAT_COLOR = { '미인정': '#191F28', '질병': '#6B7684', '출석인정': '#8B95A1', '기타': '#D1D6DB' };
  const WD = ['일', '월', '화', '수', '목', '금', '토'];
  const SAFE_URL = /^https:\/\/[^\s/$.?#].[^\s]*$/i;
  const LIMIT_TICKED = 200, LIMIT_DELETE = 100, MI_LIST_LIMIT = 12;
  const FOCUS_CHECK_DELAY_MS = 400, CHECK_EVERY_MS = 30 * 60 * 1000, FLASH_MS = 2000, TOAST_MS = 2600, UNDO_MS = 15000;
  const UNKNOWN_AFTER_MS = 10 * 60 * 1000;
  const ATT = '/v1/data/attendance', HOL = '/v1/data/holidays', SET = '/v1/data/settings';
  const READ = '/v1/data/read', CHECK = '/v1/data/check', TASKLISTS = '/v1/data/targets/tasklists';
  const REPORTS = '/v1/data/student-reports', LINKS = '/v1/data/student-links';
  // 메시지 보내기 comes with Chat in P3.
  const TABS = [['rec', '출결 기록'], ['work', '조치할 일'], ['msg', '메시지 보내기'], ['sum', '종합']];

  const T = {
    RO: '서버에 연결되면 쓸 수 있어요.',
    PICK: '먼저 표에서 줄을 골라 주세요.',
    STALE: '다른 컴퓨터에서 먼저 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인한 뒤 다시 해 주세요.',
    SESSION: 'Teacher Manager에 다시 로그인해 주세요.',
    PERM: 'Google 권한을 다시 승인해 주세요.',
    READ_FAIL: '출결 기록을 읽지 못했어요. 창 위쪽 [새로 읽기] 버튼(↻)을 눌러 주세요.',
    PARTIAL: '일부를 읽지 못해 조치할 일을 모두 확인하지 못했어요.',
    NOT_READ: '확인하지 못했어요',
    LOST_WRITE: '저장됐는지 확인하지 못했어요. 서버에 연결되면 창 위쪽 [새로 읽기] 버튼(↻)을 눌러 확인해 주세요.',
    SAVE_FAILED: '저장하지 못했어요. 창 위쪽 [새로 읽기] 버튼(↻)을 누른 뒤 다시 해 주세요.',
    DUPLICATE: '같은 날 같은 학생의 같은 줄이 이미 있어요.',
    IMPORTING: '출결 자료를 가져오는 중이에요. 끝나면 창 위쪽 [새로 읽기] 버튼(↻)을 눌러 주세요.',
    IMPORT_FAILED: '출결 자료 가져오기가 끝나지 않았어요. 가져오기를 다시 실행해 주세요.',
    NO_YEAR: '처음 설정이 끝나지 않았어요.',
    AI_LABEL: '자유로운 문장으로 입력하여 AI로 기록하기',
    AI_OFF: 'AI 출결 입력을 쓸 수 없어요.',
    ROSTER_EMPTY: '학생명단이 비어 있어요.',
    AI_LOCKED: '등록 결과를 확인하지 못한 문장이에요. 창 위쪽 [새로 읽기] 버튼(↻)을 누르거나 문장을 고친 뒤 다시 해 주세요.',
    AI_FAILED: 'AI 출결 입력을 처리하지 못했어요. 잠시 뒤 [AI로 입력]을 다시 눌러 주세요.',
    NOT_ENTERED: '입력되지 않았어요',
    PAINT_FAILED: '화면을 새로 표시하지 못했어요. 프로그램을 다시 열어 주세요.',
    REFRESHING: '새로 읽는 중이에요.'
  };

  /* ---------- dates ---------- */
  const pad = n => String(n).padStart(2, '0');
  const isDateKey = s => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ''));
  const ymd = s => String(s).split('-').map(Number);
  const dObj = s => { const p = ymd(s); return new Date(Date.UTC(p[0], p[1] - 1, p[2])); };
  const iso = d => d.toISOString().slice(0, 10);
  const addDays = (s, n) => { const d = dObj(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
  const lastDayOf = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();
  const monthEnd = (y, m) => y + '-' + pad(m) + '-' + pad(lastDayOf(y, m));
  const ymAdd = (ym, n) => { const p = ym.split('-').map(Number), t = new Date(Date.UTC(p[0], p[1] - 1 + n, 1)); return t.getUTCFullYear() + '-' + pad(t.getUTCMonth() + 1); };
  const addMonthsClamp = (s, n) => {
    const p = ymd(s), t = new Date(Date.UTC(p[0], p[1] - 1 + n, 1));
    const y = t.getUTCFullYear(), m = t.getUTCMonth() + 1;
    return y + '-' + pad(m) + '-' + pad(Math.min(p[2], lastDayOf(y, m)));
  };
  const dow = s => dObj(s).getUTCDay();
  const md = s => { const p = ymd(s); return p[1] + '/' + p[2]; };
  const mdw = s => md(s) + '(' + WD[dow(s)] + ')';
  const monOf = s => Number(String(s).slice(5, 7));
  const span = (a, b) => Math.round((dObj(b) - dObj(a)) / 864e5);
  const mondayOf = s => addDays(s, -((dow(s) + 6) % 7));
  // Server times are ISO UTC; every screen shows Asia/Seoul as 'yyyy-mm-dd HH:mm'.
  const kst = t => { const ms = Date.parse(String(t || '')); return Number.isFinite(ms) ? new Date(ms + 9 * 36e5).toISOString().slice(0, 16).replace('T', ' ') : ''; };
  const atLong = t => { const k = kst(t); return k ? md(k.slice(0, 10)) + ' ' + k.slice(11) : ''; };
  const atShort = (t, today) => { const k = kst(t); return !k ? '' : k.slice(0, 10) === today ? k.slice(11) : md(k.slice(0, 10)); };
  const readStamp = t => { const k = kst(t); if (!k) return ''; const p = ymd(k.slice(0, 10)); return p[1] + '월 ' + p[2] + '일(' + WD[dow(k.slice(0, 10))] + ') ' + k.slice(11); };
  const kindWord = k => String(k || '').replace('함', '');
  const lastHangul = w => { const s = String(w); for (let i = s.length - 1; i >= 0; i--) { const c = s.charCodeAt(i) - 0xAC00; if (c >= 0 && c <= 11171) return c; } return -1; };
  const josaRo = w => { const c = lastHangul(w); if (c < 0) return '로'; const j = c % 28; return j === 0 || j === 8 ? '로' : '으로'; };
  const josaEul = w => { const c = lastHangul(w); return c < 0 || c % 28 === 0 ? '를' : '을'; };
  const newRequestId = () => {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 14);
  };

  /* ---------- the read answer ---------- */
  function calendarFor(year, today) {
    const sy = Number(year);
    const start = sy + '-03-01', end = monthEnd(sy + 1, 2);
    const inYear = isDateKey(today) && today >= start && today <= end;
    let term = null;
    if (inYear) {
      const m = monOf(today), first = m >= 3 && m <= 8;
      term = first ? { label: '1학기', s: start, e: sy + '-08-31' } : { label: '2학기', s: sy + '-09-01', e: end };
    }
    return { sy, start, end, inYear, term, thisYm: inYear ? today.slice(0, 7) : '', lastYm: inYear && ymAdd(today.slice(0, 7), -1) >= start.slice(0, 7) ? ymAdd(today.slice(0, 7), -1) : '' };
  }

  function prepare(res) {
    const doc = res.yearDoc || {};
    const today = kst(res.serverNow).slice(0, 10);
    const cal = calendarFor(res.year, today);
    // 발송중 for 10 minutes or more, an imported one included, is an unknown result and not a send in progress.
    const nowMs = Date.parse(String(res.serverNow || ''));
    const effects = new Map((res.effects || []).map(e => [e.id, e]));
    const remUnknown = rem => {
      if (!rem || rem.state !== '발송중') return false;
      const effect = effects.get(rem.effectId);
      if (effect?.phase === 'CLAIMED') return false;
      return effect?.phase === 'NEEDS_DECISION' || !(nowMs - Date.parse(String(effect?.admittedAt || rem.claimedAt || rem.sentAt || '')) < UNKNOWN_AFTER_MS);
    };
    const prior = (row, slot) => {
      const effect = effects.get(slot === 'reminder' ? row.reminder?.effectId : row.effectId);
      if (effect?.phase !== 'CLAIMED' || effect.leaseUntil > nowMs) return row;
      const index = effect.targetRefs.findIndex(ref => ref.endsWith('/' + row.id));
      return index >= 0 ? { ...row, ...(slot === 'reminder' ? { reminder: effect.priorItem[index].reminder || null } : { status: effect.priorItem[index].status }) } : row;
    };
    const students = (res.students || []).map(s => ({
      id: s.id, version: s.version, n: Number(s.number) || 9999, name: String(s.name || ''),
      raw: String(s.label || ((s.number || '') + (s.name || ''))), label: s.number && s.name ? s.number + '번 ' + s.name : spaceLabel(s.label), hasEmail: !!String(s.email || '').trim(), status: String(s.status || '재적')
    })).sort((a, b) => a.n - b.n || (a.label > b.label ? 1 : -1));
    const byId = new Map(students.map(s => [s.id, s]));
    const toRow = r => {
      const st = r.studentId ? byId.get(r.studentId) || null : null;
      const label = st ? st.label : spaceLabel(r.studentLabel);
      const num = /^(\d+)/.exec(label);
      return {
        id: r.id, version: r.version, date: isDateKey(r.date) ? r.date : '', dateRaw: String(r.dateRaw || (r.raw && r.raw.date) || ''),
        studentId: st ? st.id : '', st, label, n: st ? st.n : num ? Number(num[1]) : 9999,
        cat: String(r.category || ''), kind: String(r.kind || ''), reason: String(r.reason || ''), period: String(r.period || ''),
        g: String(r.reportStatus || ''), h: String(r.attachmentStatus || ''), reminder: r.reminder || null, remUnknown: remUnknown(r.reminder),
        ai: !!r.aiInputId || r.source === 'ai' || r.source === 'intake', student: r.source === 'student', source: String(r.source || ''), hasReport: !!r.reportDocId,
        raw: r.raw || null, needsReview: !!r.needsReview, updatedBy: r.updatedBy || null, updatedAt: r.updatedAt || '', deletedAt: r.deletedAt || ''
      };
    };
    const rows = (res.attendance || []).map(r => toRow(prior(r, 'reminder')));
    const deleted = (res.deletedAttendance || []).map(toRow);
    const holidays = (res.holidays || []).map(x => ({
      d: x.id, name: String(x.name || ''), type: String(x.type || ''), status: String(x.status || ''),
      note: String(x.note || ''), source: String(x.source || ''), version: x.version
    })).sort((a, b) => (a.d < b.d ? -1 : 1));
    // The student absence link (2026-11-11 design §4.2, §6.3): submissions waiting for the teacher, and
    // each student's link state. A note is a student's own words: text only, never HTML (SHEET-DASH-04).
    const reports = (res.studentReports || []).filter(x => x && typeof x === 'object').map(x => reportOf(x, byId))
      .filter(x => x.id).sort((a, b) => String(a.at).localeCompare(String(b.at)) || (a.id > b.id ? 1 : -1));
    const links = new Map((res.studentLinks || []).filter(x => x && typeof x === 'object' && (x.studentId || x.id)).map(x => [String(x.studentId || x.id), x]));
    return {
      reports, links,
      raw: { ...res, messages: (res.messages || []).map(r => prior(r, 'message')) }, year: res.year, doc, today, cal, students, byId, rows, deleted, holidays,
      rowById: new Map(rows.concat(deleted).map(r => [r.id, r])),
      hasReminders: rows.concat(deleted).some(r => !!r.reminder && !!r.reminder.state),
      readOnly: !!res.readOnly, complete: res.complete === true,
      effects: res.effects || [], importChecks: res.importChecks || [], earlierOpen: res.earlierOpen || []
    };
  }

  /* ---------- student absence reports (2026-11-11 design, §13.1) ---------- */
  // The same matcher as the server's attendanceAiIsMenstruationReason_ (SHEET-AI-02): such a report records 출석인정 (Q-L5).
  const isMenses = reason => String(reason == null ? '' : reason).split(/[^0-9A-Za-z가-힣]+/)
    .some(word => /^생리(?:통|결석|조퇴|지각|결과|공결|기간|중)?(?:함)?(?:으로|로|이|가|은|는|을|를|와|과|도|때문에|때문)?$/.test(word));
  const KIND_OF = { '결석': '결석함', '지각': '지각함', '조퇴': '조퇴함', '결과': '결과함' };
  function reportOf(x, byId) {
    const st = x.studentId ? byId.get(String(x.studentId)) || null : null;
    const kind = KIND_OF[x.kind] || String(x.kind || '');
    const dates = (Array.isArray(x.dates) ? x.dates : []).filter(isDateKey).sort();
    const start = isDateKey(x.dateStart) ? x.dateStart : dates[0] || '', end = isDateKey(x.dateEnd) ? x.dateEnd : dates[dates.length - 1] || start;
    const note = String(x.note || x.reason || '');
    const raw = String(x.category || '');
    const cat = x.menstruation === true || isMenses(note) ? '출석인정' : x.categoryNeeded === true ? '' : CATS.indexOf(raw) >= 0 ? raw : '';
    return {
      id: String(x.id || x.reportId || ''), version: x.version, state: String(x.state || 'submitted'), studentId: st ? st.id : String(x.studentId || ''), st,
      label: st ? st.label : spaceLabel(x.studentLabel), kind, periods: (Array.isArray(x.periods) ? x.periods : x.period ? [x.period] : []).map(String).filter(v => PERIODS.indexOf(v) > 0),
      cat, rawCat: raw, note, start, end, dates: dates.length ? dates : start ? [start] : [], at: String(x.submittedAt || x.createdAt || ''), generation: Number(x.generation) || 0,
      olderLink: x.olderLink === true
    };
  }
  const reportRange = r => (r.start && r.end && r.end !== r.start ? mdw(r.start) + '~' + mdw(r.end) + ' ' + r.dates.length + '일' : r.start ? mdw(r.start) : '');
  const reportText = (r, today) => [r.label, kindWord(r.kind) || '—', reportRange(r), r.periods.join('·'), r.cat, r.note || '—', '학생', atLong(r.at)]
    .filter(Boolean).join(' · ');
  // Rows the report becomes: one per school day, and for 결과 one per period (design §6.1).
  const reportRowCount = r => r.dates.length * (r.kind === '결과함' ? Math.max(1, r.periods.length) : 1);
  const sourceMark = r => (!r ? null : r.student ? html`<span class="att-ai-mark" title="학생 결석 신고">학생</span>` : r.ai ? html`<span class="att-ai-mark" title="AI 출결 입력">AI</span>` : null);

  const classLabelOf = doc => String(doc.classLabel || (doc.grade && doc.classNumber ? doc.grade + '학년 ' + doc.classNumber + '반' : ''));
  const titleOf = D => D.year + '학년도 ' + (classLabelOf(D.doc) ? classLabelOf(D.doc) + ' ' : '') + '출결·메신저';
  const dlab = r => (r.date ? mdw(r.date) : '(' + r.dateRaw + ')');
  const docPair = r => (r.g === '미제출' && r.h === '미제출' ? '신고서·첨부 미제출' : '신고서 ' + (r.g || '비움') + ' · 첨부 ' + (r.h || '비움'));
  const recLine = r => dlab(r) + ' ' + r.label + ' · ' + (r.cat || '—') + ' · ' + (r.kind || '—') + ' · ' + (r.reason || '—') + (r.period ? ' · ' + r.period : '') + ' · ' + docPair(r);

  /* ---------- one eligibility module (SCREENS "Build notes") ---------- */
  const rawOf = (r, field) => (r.raw && r.raw[field] !== undefined && r.raw[field] !== null ? String(r.raw[field]) : '');
  const E = {
    inRoster: r => !!r.st,
    missing: r => r.g === '미제출' || r.h === '미제출',
    catBad: r => (!!r.cat && CATS.indexOf(r.cat) < 0) || !!rawOf(r, 'category'),
    kindBad: r => (!!r.kind && KINDS.indexOf(r.kind) < 0) || !!rawOf(r, 'kind'),
    periodBad: r => (!!r.period && PERIODS.indexOf(r.period) < 0) || !!rawOf(r, 'period'),
    docBad: (r, f) => (DOCV.indexOf(r[f]) < 0) || !!rawOf(r, f === 'g' ? 'reportStatus' : 'attachmentStatus'),
    // needsReview: a null date or a value outside the lists.
    review: r => r.needsReview || !r.date || E.catBad(r) || E.kindBad(r) || E.periodBad(r) || E.docBad(r, 'g') || E.docBad(r, 'h'),
    remProblem: r => r.remUnknown || (!!r.reminder && r.reminder.state === '실패'),
    needs: r => !r.deletedAt && (E.missing(r) || E.remProblem(r) || !E.inRoster(r) || E.review(r)),
    canDelete: r => !r.deletedAt && !(r.reminder && r.reminder.state === '발송중'),
  };
  const holidayYears = D => [D.cal.sy, D.cal.sy + 1];
  const holidaysMissing = D => holidayYears(D).filter(y => !D.holidays.some(x => x.d.slice(0, 4) === String(y)));
  const holidayPreset = (D, y) => (y === D.cal.sy ? y + '-03-01' : y + '-01-01');
  function topReasons(D, limit) {
    const count = new Map(), last = new Map();
    D.rows.forEach(r => {
      const k = r.reason.trim();
      if (!k) return;
      count.set(k, (count.get(k) || 0) + 1);
      if (!last.has(k) || r.date > last.get(k)) last.set(k, r.date);
    });
    return Array.from(count.keys()).sort((a, b) => count.get(b) - count.get(a) || (last.get(b) > last.get(a) ? 1 : last.get(b) < last.get(a) ? -1 : (a > b ? 1 : -1))).slice(0, limit);
  }

  /* The cards of 조치할 일 (D28). P3 adds AL2 and AL3 and the decision buttons; P4 adds AL9, the catch-up line and the report and Tasks lines. */
  function workItems(D) {
    const s1 = [], nowMs = Date.parse(String(D.raw.serverNow || ''));
    D.effects.filter(x => x.phase === 'NEEDS_DECISION' || (x.phase === 'DISPATCHED_OR_UNKNOWN' && !(nowMs - Date.parse(String(x.admittedAt || x.updatedAt || '')) < UNKNOWN_AFTER_MS)))
      .forEach(x => s1.push({ t: 'effect', k: 'e:' + x.id, id: x.id, label: spaceLabel(x.label), at: x.admittedAt || x.updatedAt || '', kind: x.kind, refs: x.targetRefs }));
    D.importChecks.forEach(x => s1.push({ t: 'legacy', k: 'i:' + x.id, checkId: x.id, at: x.at || '' }));
    D.earlierOpen.forEach(x => s1.push({ t: 'earlier', k: 'y:' + x.year, year: x.year, n: x.count }));
    const byStudent = new Map();
    D.rows.filter(r => r.date && E.missing(r) && E.inRoster(r)).forEach(r => {
      if (!byStudent.has(r.studentId)) byStudent.set(r.studentId, []);
      byStudent.get(r.studentId).push(r);
    });
    const s4 = Array.from(byStudent.entries()).map(e => ({ id: e[0], label: e[1][0].label, st: e[1][0].st, rows: e[1].sort((a, b) => (a.date < b.date ? -1 : 1)) }))
      .sort((a, b) => (a.rows[0].date < b.rows[0].date ? -1 : a.rows[0].date > b.rows[0].date ? 1 : a.st.n - b.st.n));
    const s5 = [];
    const noRoster = D.rows.filter(r => !E.inRoster(r));
    if (noRoster.length) s5.push({ t: 'noroster', k: 'noroster', n: noRoster.length });
    const noEmail = D.students.filter(s => !s.hasEmail && s.status === '재적');
    if (noEmail.length) s5.push({ t: 'email', k: 'email', list: noEmail });
    const missRows = s4.reduce((a, g) => a + g.rows.length, 0);
    const queue = D.raw.messages || [];
    const unmatched = queue.filter(x=>x.channel==='personal' && !x.studentId);
    if (unmatched.length) s5.push({ t:'message-student',k:'message-student',n:unmatched.length });
    const invalid = queue.filter(x=>x.needsReview || !x.date || !x.type || !x.status);
    if (invalid.length) s5.push({ t:'message-value',k:'message-value',n:invalid.length });
    const s3 = queue.filter(x=>x.status==='실패').map(x=>({id:x.id,label:D.byId.get(x.studentId)?.label||spaceLabel(x.studentLabel)||'학급 단톡방',effectId:x.effectId,mode:'queue'}));
    D.rows.filter(r=>r.reminder?.state==='실패').forEach(r=>s3.push({id:r.id,label:r.label,effectId:r.reminder.effectId,mode:'reminders'}));
    const s7 = D.effects.filter(x=>(x.phase==='CLAIMED' && x.leaseUntil>nowMs) || (x.phase==='DISPATCHED_OR_UNKNOWN' && nowMs-Date.parse(x.admittedAt)<UNKNOWN_AFTER_MS));
    const due = (D.raw.messages || []).filter(x => x.status === '대기' && x.date && x.date <= D.today && x.type && (x.channel === 'class' || x.studentId));
    // AL9 (MAIN §7.13): student DMs the server could not turn into a row by itself.
    const s9 = (D.raw.intakeItems || []).filter(x => x.state === 'review').sort((a, b) => String(a.sentAt).localeCompare(String(b.sentAt)))
      .map(x => ({ id: x.id, k: 'q:' + x.id, label: D.byId.get(x.studentId)?.label || spaceLabel(x.studentLabel), date: x.date, text: x.text || '', code: x.reasonCode || '', studentId: x.studentId }));
    const catchUp = D.doc && D.doc.intakeCatchUp;
    // AL10 (2026-11-11 design §6.3): a student's submission waits for the teacher's 기록하기 (O5).
    const s10 = D.reports.filter(x => x.state === 'submitted');
    const count = s1.length + s3.length + s4.length + s5.length + s7.length + s9.length + s10.length + (due.length ? 1 : 0);
    return { s1, s3, s4, s5, s7, s9, s10, catchUp, due, missRows, count, partial: !D.complete };
  }

  // The home tile's badge and detail line, from the same functions as the window (SCREENS "Home tile").
  function homeSummary(answer) {
    if (!answer || typeof answer !== 'object' || !answer.year) return { tone: 'muted', text: T.NOT_READ, detail: '', count: null };
    const D = prepare(answer), W = workItems(D);
    if (!D.complete) return { tone: 'muted', text: T.NOT_READ, detail: '', count: null };
    const parts = [];
    if (W.s10.length) parts.push('학생 결석 신고 ' + W.s10.length);
    if (W.s4.length) parts.push('신고서·첨부 미제출 ' + W.s4.length + '명');
    if (W.due.length) parts.push('오늘 보낼 메시지 ' + W.due.length);
    if (W.s1.length) parts.push('전달됐는지 모르는 메시지 ' + W.s1.length);
    return W.count
      ? { tone: 'warn', text: '조치할 일 ' + W.count + '개', detail: parts.join(' · '), count: W.count }
      : { tone: 'ok', text: '조치할 일 없음', detail: '', count: 0 };
  }

  /* ---------- views over the rows ---------- */
  const newView = () => ({ period: '', ym: '', wk: '', custom: null, stu: '', cat: '', kind: '', q: '', trendTable: false });
  // 출결 기록 is a sheet of one month at a time (D26, D27); 종합 keeps its period in the view above.
  const newRec = () => ({ ym: '', sort: { key: 'date', dir: 1 } });
  function periodNow(D, v) {
    const cal = D.cal;
    if (v.period === 'week' && v.wk) return { kind: 'week', s: v.wk, e: addDays(v.wk, 6), title: md(v.wk) + '~' + md(addDays(v.wk, 4)) + ' 출결 기록', short: md(v.wk) + '~' + md(addDays(v.wk, 4)), step: true };
    if (v.period === 'month' && v.ym) { const m = monOf(v.ym + '-01'); return { kind: 'month', s: v.ym + '-01', e: v.ym + '-' + pad(lastDayOf(Number(v.ym.slice(0, 4)), m)), title: m + '월 출결 기록', short: m + '월', step: true }; }
    if (v.period === 'term' && cal.term) return { kind: 'term', s: cal.term.s, e: cal.term.e, title: cal.term.label + ' 출결 기록', short: cal.term.label };
    if (v.period === 'custom' && v.custom) return { kind: 'custom', s: v.custom.s, e: v.custom.e, title: md(v.custom.s) + '~' + md(v.custom.e) + ' 출결 기록', short: md(v.custom.s) + '~' + md(v.custom.e) };
    return { kind: 'year', s: cal.start, e: cal.end, title: cal.sy + '학년도 출결 기록', short: '학년 전체' };
  }
  const matchQ = (r, q) => !q || [r.label, r.reason, r.date, r.date ? md(r.date) : r.dateRaw, r.cat, r.kind, r.g, r.h, r.student ? '학생' : r.ai ? 'AI' : ''].join(' ').indexOf(q) >= 0;
  function cmpRows(sort) {
    const k = sort.key, d = sort.dir;
    const byDate = (x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : 0);
    const idx = (list, v) => { const i = list.indexOf(v); return i < 0 ? 99 : i; };
    if (k === 'date') return (a, b) => d * (a.date < b.date ? -1 : a.date > b.date ? 1 : 0) || a.n - b.n;
    if (k === 'name') return (a, b) => d * (a.n - b.n) || byDate(a, b);
    if (k === 'cat') return (a, b) => d * (idx(CATS, a.cat) - idx(CATS, b.cat)) || byDate(a, b) || a.n - b.n;
    return (a, b) => d * (idx(KINDS, a.kind) - idx(KINDS, b.kind)) || byDate(a, b) || a.n - b.n;
  }
  // The month's rows from the loaded full year (MAIN §7.1); rows without a date are pinned apart.
  function sheetRows(D, rec) {
    return D.rows.filter(r => r.date && r.date.slice(0, 7) === rec.ym).sort(cmpRows(rec.sort));
  }
  function sheetUndated(D) {
    return D.rows.filter(r => !r.date);
  }
  const monthYm = (cal, m) => (m >= 3 ? cal.sy : cal.sy + 1) + '-' + pad(m);
  function lastYmWithRows(res, cal) {
    const dates = (res.attendance || []).map(r => r.date).filter(isDateKey).sort();
    return dates.length ? dates[dates.length - 1].slice(0, 7) : cal.start.slice(0, 7);
  }

  /* ---------- the sheet's input: dates and students as typed, the row being entered ---------- */
  function parseDateInput(text, ym, cal) {
    const t = String(text || '').replace(/\(.*?\)/g, '').replace(/\s+/g, '').replace(/[일.]$/, '');
    let y = 0, m = 0, d = 0, a = /^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/.exec(t);
    if (a) { y = Number(a[1]); m = Number(a[2]); d = Number(a[3]); }
    else if ((a = /^(\d{1,2})[-./월](\d{1,2})$/.exec(t))) { m = Number(a[1]); d = Number(a[2]); y = m >= 3 ? cal.sy : cal.sy + 1; }
    else if ((a = /^(\d{1,2})$/.exec(t)) && ym) { y = Number(ym.slice(0, 4)); m = Number(ym.slice(5, 7)); d = Number(a[1]); }
    else return '';
    if (m < 1 || m > 12 || d < 1 || d > lastDayOf(y, m)) return '';
    return y + '-' + pad(m) + '-' + pad(d);
  }
  const studentQuery = text => String(text || '').replace(/\s+/g, '').replace(/^(\d+)번/, '$1');
  function findStudents(D, text) {
    const q = studentQuery(text), list = D.students.filter(s => s.status === '재적');
    if (!q) return list;
    const num = /^\d+$/.test(q) ? Number(q) : null;
    const rank = s => {
      if (num !== null) return s.n === num ? 0 : String(s.n).indexOf(q) === 0 ? 1 : 9;
      if (s.label === q || s.raw === q || s.name === q) return 0;
      if (s.label.indexOf(q) === 0 || s.raw.indexOf(q) === 0 || s.name.indexOf(q) === 0) return 2;
      return s.label.indexOf(q) >= 0 || s.raw.indexOf(q) >= 0 ? 3 : 9;
    };
    return list.map(s => [rank(s), s]).filter(x => x[0] < 9).sort((a, b) => a[0] - b[0] || a[1].n - b[1].n).map(x => x[1]);
  }
  function resolveStudent(D, text) {
    const q = studentQuery(text), found = findStudents(D, text);
    if (!q || !found.length) return null;
    const exact = found.filter(s => s.label === q || s.raw === q || s.name === q || (/^\d+$/.test(q) && s.n === Number(q)));
    return exact.length === 1 ? exact[0] : found.length === 1 ? found[0] : null;
  }
  // The manual form's defaults; today's date only on today's month.
  const newRowDefaults = (D, rec) => ({ date: D && D.cal.inYear && rec.ym === D.cal.thisYm ? D.today : '', studentId: '', cat: '질병', kind: '결석함', reason: '', period: '', g: '미제출', h: '미제출' });
  const newRowDirty = f => !!(f.studentId || String(f.studentText || '').trim() || String(f.reason || '').trim() || f.cat !== '질병' || f.kind !== '결석함' || f.period || f.g !== '미제출' || f.h !== '미제출');
  const dateInYear = (date, D) => isDateKey(date) && date >= D.cal.start && date <= D.cal.end;
  function newRowProblem(f, D) {
    if (!dateInYear(f.date, D)) return { text: '날짜는 이번 학년도 안에서 골라 주세요.', field: 'date' };
    if (!f.studentId) return { text: '학생을 골라 주세요.', field: 'student' };
    if (!String(f.reason || '').trim()) return { text: '사유를 입력해 주세요.', field: 'reason' };
    return null;
  }
  function savedRowProblem(f, D) {
    if (f.dateText !== undefined || (f.date !== undefined && !dateInYear(f.date, D))) return { text: '날짜는 이번 학년도 안에서 골라 주세요.', field: 'date' };
    if (f.studentText !== undefined || (f.studentId !== undefined && !f.studentId)) return { text: '학생을 골라 주세요.', field: 'student' };
    return null;
  }
  // Only what the teacher touched, compared with the row as it is now: a field nobody touched is never sent back.
  function changesOf(r, f) {
    const out = {};
    if (f.date !== undefined && f.date && f.date !== r.date) out.date = f.date;
    if (f.studentId !== undefined && f.studentId && f.studentId !== r.studentId) out.studentId = f.studentId;
    if (f.cat !== undefined && f.cat !== r.cat) out.category = f.cat;
    if (f.kind !== undefined && f.kind !== r.kind) out.kind = f.kind;
    if (f.reason !== undefined && f.reason.trim() !== r.reason.trim()) out.reason = f.reason.trim();
    if (f.period !== undefined && f.period !== r.period) out.period = f.period;
    if (f.g !== undefined && f.g !== r.g) out.reportStatus = f.g;
    if (f.h !== undefined && f.h !== r.h) out.attachmentStatus = f.h;
    return out;
  }
  function rowVals(draft, r, defaults) {
    const base = r ? { date: r.date, studentId: r.studentId, cat: r.cat, kind: r.kind, reason: r.reason, period: r.period, g: r.g, h: r.h } : defaults;
    return draft ? Object.assign({}, base, draft.f) : base;
  }
  // Only unsaved edits whose exact row was deleted elsewhere are exposed here (D29).
  // This recovers the teacher's draft; it is no deleted-records list and restores/writes nothing.
  function deletedDrafts(D, drafts) {
    return D.deleted.filter(r => drafts[r.id]).map(r => {
      const v = rowVals(drafts[r.id], r), student = D.byId.get(v.studentId);
      return { id: r.id, text: recLine(Object.assign({}, r, v, {
        date: v.dateText !== undefined ? '' : v.date,
        dateRaw: v.dateText !== undefined ? v.dateText : r.dateRaw,
        label: v.studentText !== undefined ? v.studentText : student ? student.label : r.label
      })) };
    });
  }

  // 종합's period, 학생, 구분, 종류 and search (SCREENS "종합").
  function summaryRows(D, v, range) {
    const q = v.q.trim();
    return D.rows.filter(r => r.date && r.date >= range.s && r.date <= range.e
      && (!v.stu || r.studentId === v.stu) && (!v.cat || r.cat === v.cat) && (!v.kind || r.kind === v.kind) && matchQ(r, q));
  }
  function buckets(start, end, today) {
    const out = [];
    if (span(start, end) > 70) {
      let p = ymd(start), y = p[0], m = p[1];
      for (let guard = 0; guard < 40; guard++) {
        const ms = y + '-' + pad(m) + '-01';
        if (ms > end || (today && ms > today)) break;
        const me = monthEnd(y, m);
        out.push({ s: ms < start ? start : ms, e: me > end ? end : me, label: m + '월' });
        m += 1; if (m > 12) { m = 1; y += 1; }
      }
      return out;
    }
    let ws = mondayOf(start);
    for (let guard = 0; ws <= end && guard < 60; guard++) {
      const s = ws < start ? start : ws, fri = addDays(ws, 4), e = addDays(ws, 6);
      const last = fri < end ? fri : end;
      if (!today || s <= today) out.push({ s, e: e > end ? end : e, label: last > s ? md(s) + '~' + md(last) : md(s) });
      ws = addDays(ws, 7);
    }
    return out;
  }

  /* ---------- small parts ---------- */
  const Chip = p => html`<span class=${'att-chip att-' + p.tone + (p.wrap ? ' att-chip-wrap' : '')} title=${p.title || null}>${p.children}</span>`;
  const faint = t => html`<span class="att-faint">${t}</span>`;
  // One written action per button. A button that cannot act stays focusable and says why when pressed (G7).
  function Btn(p) {
    const why = p.why || null;
    return html`<button type="button" class=${p.cls} title=${p.title || null} tabindex=${p.tabindex === undefined ? null : p.tabindex}
      aria-disabled=${why ? 'true' : null} aria-label=${p.label || null} disabled=${p.disabled ? true : null}
      data-primary=${p.primary ? '1' : null} data-autofocus=${p.autofocus ? '1' : null} data-cancel=${p.cancel ? '1' : null} data-edit=${p.edit ? '1' : null}
      onClick=${ev => { if (why) { ev.preventDefault(); p.ctx.explain(why); return; } if (p.onClick) p.onClick(ev); }}>${p.children}</button>`;
  }
  const FixBtn = p => (p.fix ? html`<button type="button" class="att-btn-quiet att-btn-sm" onClick=${p.fix.run}>${p.fix.label}</button>` : null);
  // The notice slot. Sticky, because a press in a scrolled table must still show its reason (C11). It keeps
  // its place in the flow; the table bar and header sit under it through --noteh.
  function NoticeSlot(p) {
    const ctx = p.ctx, n = ctx.S.notice, box = useRef(null);
    useLayoutEffect(() => {
      const el = box.current, body = el && el.closest('.att-body');
      if (body) body.style.setProperty('--noteh', (n ? el.offsetHeight : 0) + 'px');
    });
    return html`<div class=${'att-nslot' + (n ? ' att-nslot-on' : '')} ref=${box}>${n ? html`<div class=${'att-notice att-nrow att-' + (n.tone || 'warn')} role="status">
      <span class="att-notice-tx">${n.text}</span><${FixBtn} fix=${n.fix} />
      <button type="button" class="att-ic-btn" aria-label="안내 닫기" onClick=${() => ctx.set({ notice: null })}>✕</button></div>` : null}</div>`;
  }
  function DraftRecovery(p) {
    const ctx = p.ctx, entries = deletedDrafts(ctx.D, ctx.S.drafts);
    if (!entries.length) return null;
    return html`<div class="att-draft-recovery" role="status">${entries.map(entry => html`<div class="att-notice att-warn" key=${entry.id}>
      <div class="att-nrow"><${Chip} tone="warn">확인 필요</${Chip}><span class="att-notice-tx">${T.STALE.slice(0, T.STALE.indexOf('.') + 1)}</span></div>
      <div class="att-nrow"><textarea readonly rows="2" aria-label="출결 기록" value=${entry.text}></textarea>
        <button type="button" class="att-btn-quiet att-btn-sm" onClick=${ev => {
          const box = ev.currentTarget.previousElementSibling;
          box.focus(); box.select();
          if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(entry.text).catch(err => { console.error(err); box.focus(); box.select(); });
        }}>복사</button></div></div>`)}</div>`;
  }
  const RefreshIcon = () => html`<svg class="att-refresh-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"></path><path d="M21 3v5h-5"></path></svg>`;

  /* ---------- memory that outlives one mount: the window's place and the sentence ---------- */
  // gen counts the times the memory was dropped: a result that began before forget() must not write into the memory after it.
  const memo = { reportDup: {}, place: null, aiText: '', aiq: [], aiWorker: null, aiListener: null, aiReplays: new Set(), locks: {}, drafts: {}, pending: [], rowNote: {}, seq: 0, mountSeq: 0, gen: 1 };
  function forget() {
    memo.aiq.forEach(e => uiEvent(e.owner || {}, 'removed', { queueId: e.id, stage: 'removed', state: 'removed' }));
    memo.gen += 1; memo.reportDup = {}; memo.place = null; memo.aiText = ''; memo.aiq = []; memo.aiWorker = null; memo.aiListener = null; memo.aiReplays = new Set(); memo.locks = {}; memo.drafts = {}; memo.pending = []; memo.rowNote = {};
  }
  function uiEvent(opts, event, extra) {
    if (!opts.diagnostic) return;
    try { opts.diagnostic(Object.assign({ event, mountId: opts.mountId, generation: opts.generation || memo.gen }, extra)); }
    catch (err) { console.error(err); }
  }
  function publishQueue() { if (memo.aiListener) { try { memo.aiListener(); } catch (err) { console.error(err); } } }
  // Accepted work belongs to the account generation, not to a DOM mount. A replacement window
  // subscribes to these same entries and completions; it never starts the running request again.
  async function pumpAcceptedAi() {
    if (memo.aiWorker) return;
    const entry = memo.aiq.find(e => e.state === 'waiting');
    if (!entry) return;
    const worker = { gen: memo.gen, id: entry.id };
    memo.aiWorker = worker;
    memo.aiq = memo.aiq.map(e => e.id === entry.id ? Object.assign({}, e, { state: 'running' }) : e);
    uiEvent(entry.owner, 'pump_start', { queueId: entry.id, stage: 'pump_start', state: 'running' });
    publishQueue();
    let out;
    try { out = aiOutcome(await entry.run({ sentence: entry.text, year: entry.year })); }
    catch (err) { console.error(err); out = aiOutcome(null); }
    if (worker.gen !== memo.gen || memo.aiWorker !== worker) return;
    memo.aiWorker = null;
    memo.aiq = memo.aiq.map(e => e.id === entry.id ? Object.assign({}, e, { state: 'completed', outcome: out, doneAt: Date.now() }) : e);
    const state = out.state === 'applied' ? 'success' : out.state === 'unknown' ? 'uncertain' : out.state === 'blocked' ? 'blocked' : out.state === 'stale' ? 'stale' : 'error';
    uiEvent(entry.owner, 'answer', { queueId: entry.id, stage: 'answer', state });
    publishQueue();
    pumpAcceptedAi();
  }

  function initialState(opts) {
    const place = opts.card ? null : memo.place;
    if (!opts.card) memo.place = null;
    // A note's fix belongs to the mount that made it.
    const notes = {};
    Object.keys(memo.rowNote).forEach(k => { notes[k] = { text: memo.rowNote[k].text, tone: memo.rowNote[k].tone }; });
    memo.rowNote = notes;
    return {
      snap: opts.initialRead && opts.initialRead.year && opts.initialRead.complete === true ? opts.initialRead : null,
      reading: false, readError: '', stateNote: '', net: 'ok', readAt: '',
      tab: place && TABS.some(t => t[0] === place.tab) ? place.tab : 'rec', view: place ? place.view : newView(), rec: place ? place.rec : newRec(),
      restoreTop: place && place.tab !== 'rec' ? place.scrollTop : 0,
      sheetGo: place && place.tab === 'rec' ? { seq: 1, top: place.scrollTop } : { seq: 1, center: true, focus: true },
      sel: new Set(), anchor: null, notice: null, dialog: null, dialogBusy: false, dlgNotice: null, lb: null, pop: null, toast: null,
      busy: {}, pendingDoc: {}, locks: memo.locks, flash: {}, remotePending: false,
      drafts: memo.drafts, pending: memo.pending, rowNote: notes,
      s4all: false, s4rows: {}, miAll: false, short: false, scrollTo: null, gotoId: null,
      setEdit: null, setBusy: false, holiday: { year: '', kind: '', q: '' },
      aiText: memo.aiText, aiNoKey: false,
      aiq: memo.aiq, reportDup: memo.reportDup
    };
  }

  /* ---------- what a write's answer means ---------- */
  const NOT_WRITTEN_5XX = new Set(['GOOGLE_LIST_UNAVAILABLE']);
  function classifyWrite(reply) {
    const status = Number(reply && reply.status) || 0;
    const body = reply && reply.body && typeof reply.body === 'object' ? reply.body : null;
    const code = body && body.error ? String(body.error) : '';
    if (status >= 200 && status < 300 && body && !code) return { kind: 'ok', body };
    if (!status || (reply && reply.unknown)) return { kind: 'unknown' };
    if (status === 401) return { kind: 'session', code };
    // A 5xx without a code that says nothing was written may have committed (C7).
    if (status >= 500 && !NOT_WRITTEN_5XX.has(code)) return { kind: 'unknown' };
    if (code === 'STALE') return { kind: 'stale' };
    if (!code) return { kind: 'unknown' };
    return { kind: 'refused', code, message: body && body.message ? String(body.message) : '', details: body || {} };
  }
  function refusalText(out, year) {
    if (out.message) return out.message;
    switch (out.code) {
      case 'ATTENDANCE_DUPLICATE': return T.DUPLICATE;
      case 'IMPORT_IN_PROGRESS': return T.IMPORTING;
      case 'YEAR_READ_ONLY': return earlierYearText(year);
      case 'TARGET_UNAVAILABLE': return '할 일 목록을 찾지 못했어요.';
      case 'GOOGLE_LIST_UNAVAILABLE': return '목록을 불러오지 못했어요';
      case 'GOOGLE_PERMISSION_REQUIRED': return T.PERM;
    }
    return T.SAVE_FAILED;
  }
  const earlierYearText = year => year + '학년도 자료를 보고 있어요. 결과 확인만 할 수 있어요.';

  /* Only this function knows the answer shape of options.aiInput (dashboard/attendance_ai.py). */
  function aiOutcome(raw) {
    const r = raw && typeof raw === 'object' ? raw : {};
    const rows = (Array.isArray(r.rows) ? r.rows : []).filter(x => x && typeof x === 'object');
    const state = ['applied', 'check_required', 'blocked', 'stale', 'expired', 'unavailable', 'signin_required'].indexOf(r.state) >= 0 ? r.state : 'unknown';
    return {
      state, message: String(r.message || ''), rows, updated: r.updated_existing === true, code: String(r.code || ''),
      fix: String(r.fix || ''), recordId: String(r.record_id || ''), replay: r.resume || null, calendarYear: Number(r.calendar_year) || 0
    };
  }
  const CHANGE_LABEL = { date: '날짜', studentLabel: '번호+이름', category: '구분', kind: '종류', reason: '사유', period: '교시', reportStatus: '신고서', attachmentStatus: '첨부' };
  const aiRowDisplay = x => ({ date: isDateKey(x.date) ? x.date : '', dateRaw: '', label: spaceLabel(x.studentLabel), cat: String(x.category || ''), kind: String(x.kind || ''),
    reason: String(x.reason || ''), period: String(x.period || ''), g: String(x.reportStatus || ''), h: String(x.attachmentStatus || '') });

  /* ---------- actions: created once per mount, they read the latest state through ref ---------- */
  function createActions(opts, setS, ref, alive, bodyRef, sheetRef) {
    const st = () => ref.current.S;
    const data = () => ref.current.D;
    const set = patch => setS(prev => Object.assign({}, prev, typeof patch === 'function' ? patch(prev) : patch));
    const setIn = (key, patch) => setS(prev => Object.assign({}, prev, { [key]: Object.assign({}, prev[key], typeof patch === 'function' ? patch(prev[key]) : patch) }));
    const readSeq = { n: 0 };
    const toastTimer = { id: 0 };
    const dirty = { on: false };
    const lastFocus = { el: null };
    const s4 = { list: null };
    const saves = new Set(), savingRows = new Set();
    let liveEl = null, dialogSeq = 0, sheetSeq = 1, activeKey = null, readTimer = 0;
    let closing = false, saveFailed = false, calendarActive = false, deferredRead = null;
    let calendarPressed = false, calendarReleaseTimer = 0, queueStartup = null;
    const generation = memo.gen;
    const thisCalendarYear = D => (D.cal.inYear ? Number(D.today.slice(0, 4)) : D.cal.sy);

    function announce(text) {
      if (!liveEl || !text) return;
      liveEl.textContent = '';
      setTimeout(() => { if (liveEl) liveEl.textContent = text; }, 30);
    }
    function toast(text, act) {
      set({ toast: { text, act: act || null } });
      clearTimeout(toastTimer.id);
      toastTimer.id = setTimeout(() => { if (alive.current) set({ toast: null }); }, act ? UNDO_MS : TOAST_MS);
      announce(text);
    }
    // An undo lasts until the next write or 15 seconds (SCREENS "Delete with undo").
    function dropUndo() { if (st().toast && st().toast.act) { clearTimeout(toastTimer.id); set({ toast: null }); } }
    function flash(ids) {
      if (!ids || !ids.length) return;
      set(prev => { const next = Object.assign({}, prev.flash); ids.forEach(id => { next[id] = true; }); return { flash: next }; });
      setTimeout(() => {
        if (!alive.current) return;
        set(prev => { const next = Object.assign({}, prev.flash); ids.forEach(id => { delete next[id]; }); return { flash: next }; });
      }, FLASH_MS);
    }
    function explain(why) {
      if (!why) return;
      if (st().dialog) set({ dlgNotice: why }); else set({ notice: why });
      announce(why.text);
    }
    function savePlace() {
      const S = st(), box = S.tab === 'rec' ? sheetRef.current : bodyRef.current;
      memo.place = { tab: S.tab, view: S.view, rec: S.rec, scrollTop: box ? box.scrollTop : 0 };
    }
    const queueActive = () => memo.aiq.some(e => e.state === 'waiting' || e.state === 'running');
    // A link closes this window and opens the target; the next mount returns to this tab, scroll and filters, without the selection (Q27 default).
    async function link(target, confirmed) {
      if (target.kind !== 'external') {
        if (confirmed !== true && queueActive()) { openDialog({ kind: 'closeq', then: () => link(target, true) }); return; }
        if (activeKey) { const k = activeKey; activeKey = null; leaveRow(k); }
        if (saves.size) {
          saveFailed = false;
          await Promise.all(Array.from(saves));
          if (!alive.current || saveFailed) return;
        }
        savePlace();
      }
      if (!opts.openLinked) return;
      if (opts.card && st().dialog) {
        if (st().dialogBusy) return;
        closeDialogNow();
        await new Promise(resolve => setTimeout(resolve, 0));
        if (!alive.current) return;
      }
      Promise.resolve(opts.openLinked(target)).then(() => {
        if (alive.current && (target.kind === 'reapprove' || target.kind === 'relogin')) read();
      }, err => { console.error(err); });
    }
    const relogin = () => link({ kind: 'relogin' });
    const reapprove = () => link({ kind: 'reapprove' });
    const sessionWhy = () => ({ text: T.SESSION, fix: { label: '다시 로그인', run: relogin } });
    // G1: writes wait for the server; navigation stays. An earlier year is read-only too.
    function roWhy() {
      const S = st(), D = data();
      if (S.net === 'session') return sessionWhy();
      if (S.net === 'down') return { text: T.RO, tone: 'muted' };
      if (!D) return { text: T.REFRESHING, tone: 'muted' };
      if (D.readOnly) return { text: earlierYearText(D.year), tone: 'muted' };
      return null;
    }
    function rowWhy(r) {
      const ro = roWhy();
      if (ro) return ro;
      const S = st();
      if (S.busy[r.id]) return { text: '저장하는 중이에요.', tone: 'muted' };
      if (lockedRow(r.id)) return { text: T.LOST_WRITE };
      return null;
    }
    const lockedRow = id => Object.keys(memo.locks).some(k => (memo.locks[k].rowIds || []).indexOf(id) >= 0);

    async function call(route, body) {
      try {
        const reply = await opts.dataCall(route, body);
        return reply && typeof reply === 'object' ? reply : { status: 0, unknown: true };
      } catch (err) {
        console.error(err);
        return { status: 0, unknown: true };
      }
    }

    /* ---------- reads ---------- */
    // First read: the default period and month: this month, outside the school year 학년 전체 and the last month with rows.
    function withDefaults(prev, res) {
      const today = kst(res.serverNow).slice(0, 10), cal = calendarFor(res.year, today);
      const view = Object.assign({}, prev.view);
      if (!view.period) {
        view.period = cal.inYear ? 'month' : 'year';
        view.ym = cal.thisYm;
        view.wk = cal.inYear ? mondayOf(today) : '';
      }
      const ym = prev.rec.ym;
      const known = ym && ym >= cal.start.slice(0, 7) && ym <= cal.end.slice(0, 7);
      const rec = known ? prev.rec : Object.assign({}, prev.rec, { ym: cal.inYear ? cal.thisYm : lastYmWithRows(res, cal) });
      return { view, rec };
    }
    function adoptInitial(alreadyChecking) {
      set(prev => Object.assign({ readAt: prev.snap.serverNow }, withDefaults(prev, prev.snap)));
      if (!alreadyChecking) check();
    }
    function adopt(res, o) {
      // A native popup need not keep the text input focused. Keep its element (and the row's
      // order) intact until the picker is picked, cancelled or left (LOCK-01, LOCK-02).
      if (calendarActive) { deferredRead = { res, o }; set({ reading: false }); return; }
      const S = st();
      if (!res.year) { set({ reading: false, readError: '', stateNote: 'no-year', net: 'ok', snap: null }); return; }
      if (res.yearDoc && res.yearDoc.import && res.yearDoc.import.state === 'failed') { set({ reading: false, readError: '', stateNote: 'import-failed', net: 'ok', snap: null }); return; }
      const inList = new Set((res.attendance || []).concat(res.deletedAttendance || []).map(r => r.id));
      const kept = new Set(Array.from(S.sel).filter(id => inList.has(id)));
      const vanished = S.sel.size - kept.size;
      if (o.manual) s4.list = null;
      // A half-typed row stays through any read; only the edit of a row that no longer exists goes with it.
      // A row deleted on another PC still exists: its edit stays with the stale note and is never sent by itself.
      const live = (res.attendance || []).map(r => r.id), removed = (res.deletedAttendance || []).map(r => r.id);
      const notLive = Object.keys(memo.drafts).filter(k => k !== 'new' && !pendingOf(k) && live.indexOf(k) < 0);
      const gone = notLive.filter(k => removed.indexOf(k) < 0), deletedElsewhere = notLive.filter(k => removed.indexOf(k) >= 0);
      if (gone.length) {
        const next = Object.assign({}, memo.drafts);
        gone.forEach(k => { delete next[k]; });
        memo.drafts = next;
      }
      const unnoted = deletedElsewhere.filter(k => !memo.rowNote[k]);
      if (unnoted.length) {
        const notes = Object.assign({}, memo.rowNote);
        unnoted.forEach(k => { notes[k] = { text: T.STALE }; });
        memo.rowNote = notes;
      }
      set(prev => Object.assign({ reading: false, readError: '', stateNote: '', net: 'ok', snap: res, readAt: res.serverNow, sel: kept, drafts: memo.drafts, rowNote: memo.rowNote }, unnoted.length ? { notice: { text: T.STALE } } : {}, withDefaults(prev, res)));
      if (opts.onRead) { try { opts.onRead(res); } catch (err) { console.error(err); } }
      if (vanished) toast('다른 곳에서 지운 ' + vanished + '줄을 선택에서 뺐어요.');
      else if (o.remote) toast('다른 컴퓨터에서 바뀐 내용을 불러왔어요.');
    }
    function read(o) {
      // Retained completions are settled together while mounting. They share that one fresh
      // read; later writes and explicit refreshes still start their own sequenced read.
      if (queueStartup && queueStartup.read) return queueStartup.read;
      const pending = readNow(o);
      if (queueStartup) queueStartup.read = pending;
      return pending;
    }
    async function readNow(o) {
      const opt = o || {};
      const mine = ++readSeq.n;
      // A calendar-held answer is superseded too, even if this newer read later fails.
      deferredRead = null;
      clearTimeout(readTimer);
      set({ reading: true });
      const reply = await call(READ, {});
      if (!alive.current || mine !== readSeq.n) return;
      // An early Enter can explain the initial read. Its progress notice ends with
      // this current reply too; unrelated teacher-action notices stay (OBS-01).
      if (st().notice && st().notice.text === T.REFRESHING && st().notice.tone === 'muted') set({ notice: null });
      const body = reply.body && typeof reply.body === 'object' ? reply.body : null;
      const code = body && body.error ? String(body.error) : '';
      if (reply.status === 200 && body && !code) {
        try {
          adopt(body, opt);
          return true;
        } catch (err) {
          console.error(err);
          set(prev => (prev.snap ? { reading: false, net: 'down' } : { reading: false, readError: T.READ_FAIL }));
        }
        return false;
      }
      if (reply.status === 401) { set(prev => ({ reading: false, net: 'session', readError: prev.snap ? '' : T.READ_FAIL })); return; }
      if (code === 'IMPORT_IN_PROGRESS') { set({ reading: false, readError: '', stateNote: 'import', snap: null }); return; }
      set(prev => (prev.snap ? { reading: false, net: 'down' } : { reading: false, readError: T.READ_FAIL }));
    }
    // One read after a run of saves: a teacher entering row after row does not reload the year each time.
    function readSoon() {
      clearTimeout(readTimer);
      readTimer = setTimeout(() => { if (alive.current) read(); }, READ_SOON_MS);
    }
    const isBusy = S => !!(S.dialogBusy || S.setBusy || Object.keys(S.busy).length || S.pending.some(x => x.state === 'saving'));
    const isIdle = S => !calendarActive && !S.dialog && !S.setEdit && !isBusy(S);
    function calendarStart() { calendarActive = true; }
    function calendarEnd(afterPress) {
      if (afterPress && calendarActive) calendarPressed = true;
      // The picker blur belongs to the same press as the capture handler. Neither may
      // remove or reorder its target before mouseup and click have been dispatched.
      if (calendarPressed) return;
      calendarActive = false;
      if (deferredRead) { const pending = deferredRead; deferredRead = null; adopt(pending.res, pending.o); }
      flushRemote();
    }
    function calendarRelease() {
      if (!calendarPressed) return;
      clearTimeout(calendarReleaseTimer);
      calendarReleaseTimer = setTimeout(() => {
        calendarPressed = false;
        if (alive.current && generation === memo.gen) calendarEnd();
      }, 0);
    }
    function calendarBlur() {
      // A press released in another window has no document mouseup. Focus return can end
      // that old picker, while a new press after return still gets its own click guard.
      clearTimeout(calendarReleaseTimer);
      calendarPressed = false;
    }
    // MAIN §6.5: on focus return and every 30 minutes. A change reloads now if idle, otherwise when the work ends.
    function check(o) {
      if (queueStartup && queueStartup.needsRead) return Promise.resolve();
      if (queueStartup && queueStartup.check) return queueStartup.check;
      const pending = checkNow(o);
      if (queueStartup) queueStartup.check = pending;
      return pending;
    }
    async function checkNow(o) {
      const own = !!(o && o.own);
      const before = st();
      if (before.reading) return;
      if (!before.snap && !before.stateNote) return;
      const reply = await call(CHECK, {});
      if (!alive.current) return;
      const S = st(), body = reply.body && typeof reply.body === 'object' ? reply.body : null, code = body && body.error ? String(body.error) : '';
      if (reply.status === 401) { set({ net: 'session' }); return; }
      if (code === 'IMPORT_IN_PROGRESS') { read(); return; }
      if (reply.status !== 200 || !body || code) { if (S.snap) set({ net: 'down' }); return; }
      if (S.net === 'down') set({ net: 'ok' });
      if (!S.snap) { read(); return; }
      const changed = body.year !== S.snap.year || (body.revision || 0) > (S.snap.revision || 0)
        || body.activeYear !== S.snap.activeYear || body.currentYearReady !== S.snap.currentYearReady;
      if (!changed) return;
      // After our own unconfirmed write the new revision may be that write, so nothing says another PC did it.
      if (isIdle(S)) read({ remote: !own }); else set(prev => ({ remotePending: prev.remotePending === 'remote' || !own ? 'remote' : 'own' }));
    }
    // Reloads what check() deferred, once the dialog, form or run has ended. Called by an effect.
    function flushRemote() {
      const S = st();
      if (S.remotePending && isIdle(S) && !S.reading) { set({ remotePending: false }); read({ remote: S.remotePending === 'remote' }); }
    }

    /* ---------- writes (G4, G5) ---------- */
    function setLocks(next) { memo.locks = next; set({ locks: next }); }
    function lock(key, entry) { const next = Object.assign({}, memo.locks); next[key] = entry; setLocks(next); }
    function unlock(key) { if (memo.locks[key]) { const next = Object.assign({}, memo.locks); delete next[key]; setLocks(next); } }
    // One request ID per press. data_call owns the replays of that ID (three at most, OBS-04), so an
    // unknown answer here is final: the input locks and keeps its ID until the teacher presses ↻.
    async function runWrite(w) {
      dropUndo();
      const body = Object.assign({ requestId: newRequestId() }, w.body);
      const year = st().snap && st().snap.year;
      if (year) body.year = year;
      const gen = memo.gen;
      const out = classifyWrite(await call(w.route, body));
      // forget() ran while this write was away: it belongs to an account that is gone and leaves nothing in the memory.
      if (gen !== memo.gen) return Object.assign(out, { forgotten: true });
      if (out.kind === 'unknown') {
        if (w.lockKey) lock(w.lockKey, { route: w.route, body, rowIds: w.rowIds || [], formKey: w.formKey || '', draftKey: w.draftKey || '' });
        check({ own: true });
        return out;
      }
      if (w.lockKey) unlock(w.lockKey);
      if (!alive.current) return out;
      if (out.kind === 'session') set({ net: 'session' });
      if (out.kind === 'ok' && Number.isInteger(out.body.revision)) patchSnap(snap => Object.assign({}, snap, { revision: Math.max(snap.revision || 0, out.body.revision) }));
      if (out.kind === 'stale') read();
      return out;
    }
    function whyOf(out) {
      if (out.kind === 'stale') return { text: T.STALE };
      if (out.kind === 'unknown') return { text: T.LOST_WRITE };
      if (out.kind === 'session') return sessionWhy();
      if (out.code === 'GOOGLE_PERMISSION_REQUIRED') return { text: T.PERM, fix: { label: '권한 다시 승인', run: reapprove } };
      if (out.code === 'ATTENDANCE_DUPLICATE' && out.details && out.details.recordId) {
        const id = out.details.recordId;
        return { text: refusalText(out, data() && data().year), fix: { label: '그 줄 보기', run: () => gotoRow(id) } };
      }
      return { text: refusalText(out, data() && data().year) };
    }
    function patchSnap(fn) { set(prev => (prev.snap ? { snap: fn(prev.snap) } : {})); }
    // The answer's versions apply at once, so a second change on the same row does not read as stale while the read runs.
    function patchRows(results, fields) {
      const byId = new Map((results || []).map(x => [x.recordId, x]));
      patchSnap(snap => Object.assign({}, snap, {
        attendance: (snap.attendance || []).map(r => {
          const x = byId.get(r.id);
          if (!x || !Number.isInteger(x.version)) return r;
          const next = Object.assign({}, r, fields || {}, { version: x.version });
          // A send in progress keeps its reminder, as the server does (MAIN §7.5).
          if (r.reminder && r.reminder.state === '발송중') next.reminder = r.reminder;
          return next;
        })
      }));
    }
    function moveRows(results, deleting) {
      const byId = new Map((results || []).map(x => [x.recordId, x]));
      const at = new Date().toISOString();
      // Deleting a row here ends its half-typed edit; only a delete on another PC leaves it with a note (adopt).
      if (deleting && Object.keys(memo.drafts).some(k => byId.has(k))) {
        const next = Object.assign({}, memo.drafts);
        byId.forEach((x, id) => { delete next[id]; });
        setDrafts(next);
      }
      patchSnap(snap => {
        const from = deleting ? snap.attendance || [] : snap.deletedAttendance || [];
        const to = deleting ? snap.deletedAttendance || [] : snap.attendance || [];
        const moved = from.filter(r => byId.has(r.id)).map(r => Object.assign({}, r, { version: byId.get(r.id).version, deletedAt: deleting ? at : null }));
        const rest = from.filter(r => !byId.has(r.id));
        const nextTo = moved.concat(to);
        return Object.assign({}, snap, deleting ? { attendance: rest, deletedAttendance: nextTo } : { deletedAttendance: rest, attendance: nextTo });
      });
    }
    // ↻ replays each locked request once with its same ID, then reloads, so the table shows whether it was saved (G4).
    async function replayLocks() {
      const keys = Object.keys(memo.locks), gen = memo.gen;
      for (let i = 0; i < keys.length; i++) {
        const entry = memo.locks[keys[i]];
        if (!entry) continue;
        const out = classifyWrite(await call(entry.route, entry.body));
        if (!alive.current || gen !== memo.gen) return;
        if (out.kind === 'session') { set({ net: 'session' }); return; }
        if (out.kind !== 'unknown') unlock(keys[i]);
        if (entry.draftKey) {
          if (entry.body.action === 'create') afterCreate(entry.draftKey, out, entry.body);
          else afterEdit(entry.draftKey, out, entry.body.changes || {});
        }
      }
    }
    async function manualRefresh() {
      const S = st();
      if (S.reading) return;
      set({ reading: true, notice: null });
      await replayLocks();
      if (!alive.current) return;
      await replayHeld();
      if (!alive.current) return;
      const fresh = await read({ manual: true });
      if (!alive.current || !fresh) return;
      // Nothing to replay: the fresh read is the answer, and the table shows whether a row came.
      updateQ(list => list.filter(e => !(e.state === 'uncertain' && !e.replay)));
    }

    /* ---------- window, dialogs, navigation ---------- */
    async function closeWindow(fromButton, confirmed) {
      const S = st();
      if (closing) return;
      if (S.dialogBusy || S.setBusy) {
        if (fromButton === true) explain({ text: '저장하는 중이에요.', tone: 'muted' });
        return;
      }
      if (confirmed !== true && queueActive()) { openDialog({ kind: 'closeq', then: () => closeWindow(true, true) }); return; }
      // The row being edited saves as it does on leaving; a refused one stays on screen with its reason.
      if (activeKey) { const k = activeKey; activeKey = null; leaveRow(k); }
      if (saves.size) {
        closing = true;
        saveFailed = false;
        await Promise.all(Array.from(saves));
        closing = false;
        if (!alive.current || saveFailed) return;
      }
      memo.place = null;
      if (opts.onClose) opts.onClose();
    }
    function openDialog(d) {
      const active = document.activeElement;
      if (!st().dialog) lastFocus.el = active;
      dirty.on = false;
      dialogSeq += 1;
      set({ dialog: Object.assign({ seq: dialogSeq }, d), dlgNotice: null, lb: null, pop: null });
    }
    function closeDialog() {
      const S = st();
      if (S.dialog && !S.dialogBusy) closeDialogNow();
    }
    // For the end of a run: st() may still hold dialogBusy until the next paint.
    function closeDialogNow() {
      dirty.on = false;
      set({ dialog: null, dlgNotice: null, dialogBusy: false });
      const el = lastFocus.el;
      lastFocus.el = null;
      setTimeout(() => { if (el && document.contains(el)) { try { el.focus({ preventScroll: true }); } catch (err) { /* nothing to refocus */ } } }, 0);
    }
    function setTab(k) {
      const S = st();
      if (S.tab !== k) {
        s4.list = null;
        set({ tab: k, sel: new Set(), anchor: null, notice: null, setEdit: null, s4all: false, s4rows: {}, miAll: false, lb: null, pop: null });
        if (k === 'rec') goSheet({ center: true, focus: true });
        else if (bodyRef.current) bodyRef.current.scrollTop = 0;
      }
    }
    // Period, filter and search changes clear the selection (selection rule 5).
    function setView(patch) { set(prev => ({ view: Object.assign({}, prev.view, patch), sel: new Set(), anchor: null })); }
    function setPeriod(k) {
      const D = data();
      if (!D) return;
      if (k === 'custom') { openDialog({ kind: 'period' }); return; }
      if (k === 'week') setView({ period: 'week', wk: mondayOf(D.today) });
      else if (k === 'this') setView({ period: 'month', ym: D.cal.thisYm });
      else if (k === 'last') setView({ period: 'month', ym: D.cal.lastYm });
      else setView({ period: k });
    }
    function toggleRow(id, checked, shift) {
      set(prev => {
        const sel = new Set(prev.sel);
        if (shift && prev.anchor !== null) {
          const ids = shownRowIds();
          const a = ids.indexOf(prev.anchor), b = ids.indexOf(id);
          if (a >= 0 && b >= 0) ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => sel.add(x)); else sel.add(id);
        } else if (checked) sel.add(id); else sel.delete(id);
        return { sel, anchor: id };
      });
    }
    function shownRowIds() {
      const D = data();
      return D ? sheetRows(D, st().rec).map(r => r.id) : [];
    }
    function selectRange(toId) {
      set(prev => {
        const ids = shownRowIds(), sel = new Set(prev.sel), anchor = prev.anchor === null ? toId : prev.anchor;
        const a = ids.indexOf(anchor), b = ids.indexOf(toId);
        if (a >= 0 && b >= 0) ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => sel.add(x)); else sel.add(toId);
        return { sel, anchor };
      });
    }
    function clearSel() { set({ sel: new Set(), anchor: null }); }
    /* ---------- the sheet's place: month, opening position, links in ---------- */
    function goSheet(o) { sheetSeq += 1; set({ sheetGo: Object.assign({ seq: sheetSeq }, o) }); }
    function chooseMonth(ym) {
      const d = memo.drafts.new;
      if (d && !newRowDirty(d.f)) { const next = Object.assign({}, memo.drafts); delete next.new; setDrafts(next); }
      set(prev => ({ rec: Object.assign({}, prev.rec, { ym }), sel: new Set(), anchor: null, notice: null, lb: null, pop: null }));
      goSheet({ center: true, focus: true });
    }
    function setRecSort(k) {
      set(prev => {
        const sort = prev.rec.sort;
        return { rec: Object.assign({}, prev.rec, { sort: sort.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: 1 } }) };
      });
    }
    // 그 줄 보기, 수정: its month, scrolled to it, ticked and highlighted, replacing the selection.
    function gotoRow(id) {
      const D = data();
      const r = D && D.rowById.get(id);
      // A duplicate may name a row made on another PC after this read.
      if (!r) { set({ gotoId: id }); read(); return; }
      const hadDialog = !!st().dialog;
      if (hadDialog) { dirty.on = false; lastFocus.el = null; }
      s4.list = null;
      set(prev => ({
        tab: 'rec', dialog: null, dialogBusy: false, dlgNotice: null, notice: null, lb: null, pop: null,
        rec: Object.assign({}, prev.rec, { ym: r.date ? r.date.slice(0, 7) : prev.rec.ym }),
        sel: r.date ? new Set([r.id]) : new Set(), anchor: r.date ? r.id : null, scrollTo: r.id
      }));
      flash([r.id]);
    }
    function markDirty() { dirty.on = true; }

    /* ---------- the sheet's cells: drafts live in memo, so they outlive reads, tabs and the mount ---------- */
    const pendingOf = key => memo.pending.find(x => x.key === key) || null;
    const isNewKey = key => key === 'new' || !!pendingOf(key);
    const lockKeyOf = key => (isNewKey(key) ? 'new:' + key : 'row:' + key);
    const rowLockedByKey = key => !!memo.locks[lockKeyOf(key)];
    function setDrafts(next) { memo.drafts = next; set({ drafts: next }); }
    function setNote(key, note) {
      const next = Object.assign({}, memo.rowNote);
      if (note) next[key] = note; else delete next[key];
      memo.rowNote = next;
      set({ rowNote: next });
    }
    function setPending(list) { memo.pending = list; set({ pending: list }); }
    function editCell(key, patch) {
      const D = data();
      if (!D) return;
      const cur = memo.drafts[key], isNew = isNewKey(key), r = isNew ? null : D.rowById.get(key);
      const f = Object.assign({}, cur ? cur.f : isNew ? newRowDefaults(D, st().rec) : {}, patch);
      Object.keys(f).forEach(k => { if (f[k] === undefined) delete f[k]; });
      // Back at the saved value is not an edit.
      if (r) ['date', 'studentId', 'cat', 'kind', 'reason', 'period', 'g', 'h'].forEach(k => { if (f[k] !== undefined && f[k] === r[k]) delete f[k]; });
      const next = Object.assign({}, memo.drafts);
      if (r && !Object.keys(f).length) delete next[key];
      else next[key] = Object.assign({}, cur, { f, base: cur ? cur.base : r ? r.version : 0 });
      setDrafts(next);
      // An edit ends an unknown lock (G4) and the row's last result.
      if (memo.locks[lockKeyOf(key)]) unlock(lockKeyOf(key));
      if (pendingOf(key) && pendingOf(key).state === 'locked') setPending(memo.pending.map(x => (x.key === key ? Object.assign({}, x, { state: 'failed' }) : x)));
      if (memo.rowNote[key]) setNote(key, null);
    }
    const ymOfKey = key => {
      const D = data(), r = D && !isNewKey(key) ? D.rowById.get(key) : null;
      return r && r.date ? r.date.slice(0, 7) : st().rec.ym;
    };
    function commitDate(key) {
      const d = memo.drafts[key], D = data();
      if (!d || d.f.dateText === undefined || !D) return;
      const text = d.f.dateText, parsed = parseDateInput(text, ymOfKey(key), D.cal);
      if (parsed) editCell(key, { date: parsed, dateText: undefined });
      else if (!text.trim()) editCell(key, { date: '', dateText: undefined });
      else editCell(key, { date: '', dateText: text });
    }
    function commitStudent(key) {
      const d = memo.drafts[key], D = data();
      if (!d || d.f.studentText === undefined || !D) return;
      const found = resolveStudent(D, d.f.studentText);
      if (found) editCell(key, { studentId: found.id, studentText: undefined });
      else if (!d.f.studentText.trim()) editCell(key, { studentId: '', studentText: undefined });
    }
    const focusCell = (key, field) => {
      const box = sheetRef.current, tr = box && box.querySelector('tr[data-key="' + CSS.escape(key) + '"]');
      const el = tr && tr.querySelector('[data-cell="' + field + '"]');
      if (el) el.focus({ preventScroll: true });
      return el;
    };
    function moveCell(el, dr, dc, shift) {
      const tr = el.closest('tr[data-key]');
      if (!tr) return;
      const field = el.dataset.cell;
      if (dc) {
        const i = CELL_ORDER.indexOf(field);
        for (let j = i + dc; j >= 0 && j < CELL_ORDER.length; j += dc) {
          const t = tr.querySelector('[data-cell="' + CELL_ORDER[j] + '"]');
          if (t) { t.focus(); return; }
        }
        return;
      }
      const rows = Array.from(tr.parentElement.querySelectorAll('tr[data-key]'));
      const next = rows[rows.indexOf(tr) + dr];
      if (!next) return;
      const t = next.querySelector('[data-cell="' + field + '"]') || next.querySelector('[data-cell="date"]');
      if (t) t.focus();
      if (shift && field === 'ck' && next.dataset.rid) selectRange(next.dataset.rid);
    }
    function revertRow(key) {
      const next = Object.assign({}, memo.drafts), f = next[key] && next[key].f;
      if (key === 'new') next.new = { f: Object.assign(newRowDefaults(data(), st().rec), f && f.date ? { date: f.date } : {}), base: 0 };
      else delete next[key];
      setDrafts(next);
      setNote(key, null);
      if (pendingOf(key)) setPending(memo.pending.filter(x => x.key !== key));
    }
    function escapeCell(key, el) {
      if (st().pop) { closePop(); return; }
      const d = memo.drafts[key];
      const edited = !!d && (key === 'new' ? newRowDirty(d.f) || d.f.dateText !== undefined : true);
      const saving = savingRows.has(key) || (pendingOf(key) && pendingOf(key).state === 'saving');
      if (edited && !rowLockedByKey(key) && !saving) { revertRow(key); return; }
      // The window opens with the focus here: on an untouched input row Esc is the window's, as anywhere else.
      if (key === 'new' && !edited) { if (st().sel.size) clearSel(); else closeWindow(); return; }
      el.blur();
    }
    // Enter: a complete row saves once. A new row hands the focus to the next input row at once, so typing goes on while it saves.
    function enterRow(key) {
      closePop();
      commitDate(key);
      commitStudent(key);
      const D = data(), d = memo.drafts[key];
      if (!D) return;
      if (rowLockedByKey(key)) { setNote(key, { text: T.LOST_WRITE }); return; }
      if (isNewKey(key)) {
        if (!d || !newRowDirty(d.f)) return;
        const ro = roWhy();
        if (ro) { setNote(key, ro); return; }
        const bad = newRowProblem(d.f, D);
        if (bad) { setNote(key, { text: bad.text }); focusCell(key, bad.field); return; }
        submitNew(key, true);
        return;
      }
      const el = document.activeElement;
      if (d) {
        const ro = roWhy();
        if (ro) { setNote(key, ro); return; }
        const bad = savedRowProblem(d.f, D);
        if (bad) { setNote(key, { text: bad.text }); focusCell(key, bad.field); return; }
        saveRow(key);
      }
      if (el && el.dataset && el.dataset.cell) moveCell(el, 1, 0);
    }
    function leaveRow(key) {
      commitDate(key);
      commitStudent(key);
      const D = data(), d = memo.drafts[key];
      if (!D || !d || rowLockedByKey(key) || savingRows.has(key)) return;
      if (isNewKey(key)) {
        if (!newRowDirty(d.f) || newRowProblem(d.f, D) || roWhy()) return;
        if (d.tried === JSON.stringify(d.f)) return;
        submitNew(key, false);
        return;
      }
      const ro = roWhy();
      if (ro) { setNote(key, ro); return; }
      const bad = savedRowProblem(d.f, D);
      if (bad) { setNote(key, { text: bad.text }); return; }
      if (d.tried === JSON.stringify(d.f)) return;
      saveRow(key);
    }
    function rowFocus(key) {
      if (key === activeKey) return;
      const prev = activeKey;
      activeKey = key;
      if (prev) leaveRow(prev);
    }
    function track(promise) {
      saves.add(promise);
      promise.then(() => saves.delete(promise), () => saves.delete(promise));
      return promise;
    }
    function saveRow(id) { return track(writeRow(id)); }
    async function writeRow(id) {
      const D = data(), r = D && D.rowById.get(id), d = memo.drafts[id];
      if (!r || !d || savingRows.has(id)) return;
      const values = changesOf(r, d.f);
      if (!Object.keys(values).length) { const next = Object.assign({}, memo.drafts); delete next[id]; setDrafts(next); return; }
      savingRows.add(id);
      const tried = JSON.stringify(d.f);
      setDrafts(Object.assign({}, memo.drafts, { [id]: Object.assign({}, d, { tried }) }));
      setNote(id, null);
      set(prev => ({ busy: Object.assign({}, prev.busy, { [id]: true }) }));
      try {
        // After a stale refusal the teacher has seen the row re-read: the new try goes against that version.
        const version = d.rebase ? r.version : d.base;
        const out = await runWrite({ route: ATT, body: { action: 'edit', recordId: id, version, changes: values }, lockKey: 'row:' + id, rowIds: [id], draftKey: id });
        if (alive.current) afterEdit(id, out, values);
      } finally {
        savingRows.delete(id);
        if (alive.current) set(prev => { const busy = Object.assign({}, prev.busy); delete busy[id]; return { busy }; });
      }
    }
    const movedToast = (id, date) => toast(monOf(date) + '월 ' + Number(date.slice(8, 10)) + '일로 옮겼어요.', { label: '그 줄 보기', run: () => { set({ toast: null }); gotoRow(id); } });
    function afterEdit(id, out, values) {
      if (out.forgotten) return;
      if (out.kind === 'ok') {
        const D = data(), fields = {};
        Object.keys(values).forEach(k => { if (k !== 'studentId') fields[k] = values[k]; });
        if (values.studentId) {
          const s = D && D.byId.get(values.studentId);
          fields.studentId = values.studentId;
          if (s) fields.studentLabel = s.raw;
        }
        if (values.date || values.studentId || values.reportStatus !== undefined || values.attachmentStatus !== undefined) fields.reminder = null;
        const next = Object.assign({}, memo.drafts);
        delete next[id];
        setDrafts(next);
        setNote(id, null);
        patchRows(out.body.rows, fields);
        flash([id]);
        const rec = st().rec;
        if (values.date && values.date.slice(0, 7) !== rec.ym) movedToast(id, values.date);
        readSoon();
        return;
      }
      saveFailed = true;
      if (out.kind === 'stale') {
        const d = memo.drafts[id];
        if (d) memo.drafts = Object.assign({}, memo.drafts, { [id]: Object.assign({}, d, { rebase: true }) });
      }
      setNote(id, whyOf(out));
    }
    function submitNew(key, viaEnter) {
      const D = data(), d = memo.drafts[key], f = d.f, next = Object.assign({}, memo.drafts);
      const tried = JSON.stringify(f);
      let pkey = key;
      if (key === 'new') {
        memo.seq += 1;
        pkey = 'p' + memo.seq;
        next[pkey] = { f, base: 0, tried };
        next.new = { f: Object.assign(newRowDefaults(D, st().rec), { date: f.date }), base: 0 };
        setPending(memo.pending.concat({ key: pkey, state: 'saving' }));
      } else {
        next[key] = Object.assign({}, d, { tried });
        setPending(memo.pending.map(x => (x.key === key ? Object.assign({}, x, { state: 'saving' }) : x)));
      }
      setDrafts(next);
      setNote(key, null);
      if (viaEnter) {
        const entered = next.new ? next.new.f.date : newRowDefaults(D, st().rec).date;
        focusCell('new', entered ? 'student' : 'date');
        goSheet({ center: true });
      }
      track(writeNew(pkey));
    }
    async function writeNew(pkey) {
      const d = memo.drafts[pkey];
      if (!d) return;
      const f = d.f;
      const body = { action: 'create', date: f.date, studentId: f.studentId, category: f.cat, kind: f.kind, reason: f.reason.trim(), period: f.period, reportStatus: f.g, attachmentStatus: f.h };
      savingRows.add(pkey);
      const out = await runWrite({ route: ATT, body, lockKey: 'new:' + pkey, draftKey: pkey });
      savingRows.delete(pkey);
      afterCreate(pkey, out, body, f.intakeItemId);
    }
    function afterCreate(pkey, out, body, intakeItemId) {
      if (out.forgotten) return;
      if (out.kind === 'ok') {
        // A row typed from a student DM card closes that card (MAIN §7.13, AL9 `entered`).
        if (intakeItemId) call('/v1/data/intake/resolve', { itemId: intakeItemId, action: 'entered' }).then(r => { if (r.status === 200 && alive.current) readSoon(); });
        const made = (out.body.rows || [])[0] || {}, D = data();
        const next = Object.assign({}, memo.drafts);
        delete next[pkey];
        memo.drafts = next;
        memo.pending = memo.pending.filter(x => x.key !== pkey);
        const rest = Object.assign({}, memo.rowNote);
        delete rest[pkey];
        memo.rowNote = rest;
        if (!alive.current) return;
        const s = D && D.byId.get(body.studentId);
        const doc = { id: made.recordId, version: made.version, date: body.date, month: monOf(body.date) + '월', studentId: body.studentId, studentLabel: s ? s.raw : '',
          category: body.category, kind: body.kind, reason: body.reason, period: body.period, reportStatus: body.reportStatus, attachmentStatus: body.attachmentStatus,
          reminder: null, aiInputId: null, reportDocId: null, source: 'manual', raw: null, needsReview: false, updatedAt: new Date().toISOString(), deletedAt: null };
        set({ drafts: memo.drafts, pending: memo.pending, rowNote: memo.rowNote });
        patchSnap(snap => Object.assign({}, snap, { attendance: (snap.attendance || []).filter(r => r.id !== doc.id).concat(doc) }));
        flash([doc.id]);
        const rec = st().rec;
        if (body.date.slice(0, 7) !== rec.ym) movedToast(doc.id, body.date);
        readSoon();
        return;
      }
      saveFailed = true;
      const state = out.kind === 'unknown' ? 'locked' : 'failed';
      memo.pending = memo.pending.map(x => (x.key === pkey ? Object.assign({}, x, { state }) : x));
      memo.rowNote = Object.assign({}, memo.rowNote, { [pkey]: whyOf(out) });
      if (alive.current) set({ pending: memo.pending, rowNote: memo.rowNote });
    }
    /* ---------- lists under typed cells ---------- */
    function placePop(el, count, width) {
      const rect = el.getBoundingClientRect(), height = Math.min(count, 8) * 31 + 10;
      const top = rect.bottom + 3 + height > window.innerHeight ? Math.max(4, rect.top - height - 3) : rect.bottom + 3;
      return { left: rect.left, top, width: Math.min(Math.max(width, rect.width), 280) };
    }
    function openStudentPop(el, key, text) {
      const D = data();
      if (!D) return;
      const found = findStudents(D, text);
      if (!found.length) { closePop(); return; }
      const items = found.slice(0, 40).map(s => ({ v: s.id, label: s.label }));
      const pick = (item, viaEnter) => {
        editCell(key, { studentId: item.v, studentText: undefined });
        closePop();
        if (viaEnter) moveCell(el, 0, 1);
      };
      set({ pop: Object.assign({ key, field: 'student', el, items, act: text.trim() ? 0 : -1, pick }, placePop(el, items.length, 120)), lb: null });
    }
    function openReasonPop(el, key, text, onFocus) {
      const D = data();
      if (!D) return;
      const t = String(text || '').trim();
      if (onFocus && t) return;
      const items = topReasons(D, REASON_SUGGESTIONS).filter(x => x !== t && x.indexOf(t) >= 0).map(x => ({ v: x, label: x }));
      if (!items.length) { closePop(); return; }
      const pick = item => {
        editCell(key, { reason: item.v });
        closePop();
      };
      set({ pop: Object.assign({ key, field: 'reason', el, items, act: -1, pick }, placePop(el, items.length, 160)), lb: null });
    }
    function closePop() { if (st().pop) set({ pop: null }); }
    function movePop(step) {
      set(prev => {
        if (!prev.pop) return {};
        const n = prev.pop.items.length, act = prev.pop.act < 0 ? (step > 0 ? 0 : n - 1) : (prev.pop.act + step + n) % n;
        return { pop: Object.assign({}, prev.pop, { act }) };
      });
    }
    function pickPop(item, viaEnter) { const pop = st().pop; if (pop) pop.pick(item, viaEnter); }
    function followPop() {
      const pop = st().pop;
      if (!pop || !pop.el || !document.contains(pop.el)) return;
      const place = placePop(pop.el, pop.items.length, pop.width);
      if (place.left !== pop.left || place.top !== pop.top) set({ pop: Object.assign({}, pop, place) });
    }

    /* ---------- value lists ---------- */
    function placeList(el, count) {
      const rect = el.getBoundingClientRect(), height = count * 31 + 10;
      const top = rect.bottom + 3 + height > window.innerHeight ? Math.max(4, rect.top - height - 3) : rect.bottom + 3;
      return { left: rect.left, top };
    }
    function openLB(el, r, f) {
      const S = st();
      if (S.lb && S.lb.el === el) { set({ lb: null }); return; }
      const act = Math.max(0, DOC_OPTIONS.findIndex(o => o[0] === r[f]));
      set({ lb: Object.assign({ el, id: r.id, f, options: DOC_OPTIONS, cur: r[f], act }, placeList(el, DOC_OPTIONS.length)), pop: null });
    }
    function openCellList(el, key, field, value) {
      const S = st();
      if (S.lb && S.lb.el === el) { set({ lb: null }); return; }
      const options = sheetOptions(field);
      const act = Math.max(0, options.findIndex(o => o[0] === value));
      set({ lb: Object.assign({ el, sheet: { key, field }, options, cur: value, act }, placeList(el, options.length)), pop: null });
    }
    function closeLB(refocus) {
      const lb = st().lb;
      if (!lb) return;
      set({ lb: null });
      if (refocus && lb.el && document.contains(lb.el)) lb.el.focus();
    }
    async function pickLB(v) {
      const lb = st().lb, D = data();
      if (!lb || !D) return;
      set({ lb: null });
      if (lb.el && document.contains(lb.el)) lb.el.focus();
      if (lb.sheet) {
        if (v !== lb.cur) editCell(lb.sheet.key, { [lb.sheet.field]: v });
        return;
      }
      const r = D.rowById.get(lb.id), f = lb.f;
      if (r && r[f] !== v) await setDoc(r, f, v);
    }
    async function setDoc(r, f, value) {
      const label = f === 'g' ? '신고서' : '첨부';
      const pendKey = r.id + ':' + f;
      set(prev => ({ busy: Object.assign({}, prev.busy, { [r.id]: true }), pendingDoc: Object.assign({}, prev.pendingDoc, { [pendKey]: value }), notice: null }));
      try {
        const body = { action: 'status', rows: [{ recordId: r.id, version: r.version }] };
        body[f === 'g' ? 'reportStatus' : 'attachmentStatus'] = value;
        const out = await runWrite({ route: ATT, body, lockKey: 'row:' + r.id, rowIds: [r.id] });
        if (!alive.current) return;
        if (out.kind === 'ok') {
          const fields = {};
          fields[f === 'g' ? 'reportStatus' : 'attachmentStatus'] = value;
          fields.reminder = null;
          patchRows(out.body.rows, fields);
          const word = value || '비움';
          toast(label + '를 ‘' + word + '’' + josaRo(word) + ' 바꿨어요.');
          flash([r.id]);
          read();
        } else {
          explain(whyOf(out));
        }
      } finally {
        if (alive.current) {
          set(prev => {
            const busy = Object.assign({}, prev.busy), pendingDoc = Object.assign({}, prev.pendingDoc);
            delete busy[r.id];
            delete pendingDoc[pendKey];
            return { busy, pendingDoc };
          });
        }
      }
    }

    /* ---------- AI input: a queue, one sentence at a time. The box stays free (D26, D27). ---------- */
    function updateQ(fn) {
      if (generation !== memo.gen) return;
      const previous = memo.aiq;
      memo.aiq = fn(previous);
      const kept = new Set(memo.aiq.map(e => e.id));
      previous.filter(e => !kept.has(e.id)).forEach(e => uiEvent(opts, 'removed', { queueId: e.id, stage: 'removed', state: 'removed' }));
      publishQueue();
    }
    function patchQ(id, patch) { updateQ(list => list.map(e => (e.id === id ? Object.assign({}, e, patch) : e))); }
    function setAiText(text) { memo.aiText = text; set({ aiText: text }); }
    function aiFix(kind, year) {
      if (kind === 'gemini-key') return { label: '연결 → Brity 메신저 열기', run: () => link({ kind: 'brity' }) };
      if (kind === 'class-settings') return { label: '내 정보 열기', run: () => link({ kind: 'profile' }) };
      if (kind === 'roster') return { label: '학생명단 고치기', run: () => link({ kind: 'roster' }) };
      if (kind === 'holidays') return { label: '휴일·학사일정 열기', run: () => link({ kind: 'holidays' }) };
      if (kind === 'sign-in') return { label: '다시 로그인', run: relogin };
      return null;
    }
    // The confirmed causes under the bar. The first three stop every sentence, as the server does.
    // Holidays only stop the sentences that land in that year (D27): today's year stands here, and any other comes back in the sentence's own answer.
    function aiCause() {
      const D = data();
      if (!D) return null;
      const info = opts.pcInfo ? opts.pcInfo() || {} : {};
      if (info.hasGeminiKey === false) return { text: T.AI_OFF, fix: aiFix('gemini-key') };
      if (D.doc.aiInputAllowed !== true) return { text: T.AI_OFF, fix: aiFix('class-settings') };
      if (!D.students.length) return { text: T.ROSTER_EMPTY, fix: aiFix('roster') };
      const y = thisCalendarYear(D);
      if (!D.holidays.some(x => x.d.slice(0, 4) === String(y))) return { soft: true, text: y + '년 휴일이 없어 ' + y + '년 날짜의 AI 출결 입력이 멈춰요.', fix: aiFix('holidays', y) };
      return null;
    }
    function aiBlock() {
      const ro = roWhy();
      if (ro) return ro;
      const c = aiCause();
      if (c && !c.soft) return c;
      const text = String(memo.aiText || '').trim();
      if (!text) return { text: '문장을 먼저 적어 주세요.', tone: 'muted' };
      if (memo.aiq.some(e => e.state === 'uncertain' && e.text === text)) return { text: T.AI_LOCKED };
      return null;
    }
    // One line per corrected row, naming the changed fields. A replayed answer has no changes (attendance_ai.py).
    function aiChangeLine(x, was) {
      const row = aiRowDisplay(x), head = dlab(row) + ' ' + row.label;
      const value = (field, v) => (!v ? '비움' : field === 'date' ? mdw(v) : v);
      if (Array.isArray(x.changes)) {
        return head + x.changes.map(c => ' · ' + (CHANGE_LABEL[c.field] || c.field) + ' ' + value(c.field, c.from) + ' → ' + value(c.field, c.to)).join('');
      }
      if (!was) return recLine(row);
      const diff = [['date', '날짜'], ['label', '번호+이름'], ['cat', '구분'], ['kind', '종류'], ['reason', '사유'], ['period', '교시'], ['g', '신고서'], ['h', '첨부']]
        .filter(p => was[p[0]] !== row[p[0]]).map(p => ' · ' + p[1] + ' ' + value(p[0], was[p[0]]) + ' → ' + value(p[0], row[p[0]]));
      return diff.length ? head + diff.join('') : recLine(row);
    }
    // What each state means for the sentence is in the docstring of attendance_ai.py. retry: nothing was written, so it may go back in the box.
    function finishAi(entry, out) {
      const result = patch => patchQ(entry.id, Object.assign({ msg: '', fix: null, head: '', lines: [], gotoId: '', retry: false, replay: null, hidden: false, outcome: out }, patch));
      if (out.state === 'applied') {
        const before = data() ? data().rowById : new Map();
        const ids = out.rows.map(x => x.recordId).filter(Boolean);
        const lines = out.updated ? out.rows.map(x => aiChangeLine(x, before.get(x.recordId))) : out.rows.map(aiRowDisplay).map(recLine);
        result({ state: 'applied', head: out.message, lines });
        // 입력함 shows for a few seconds and clears; nothing about it is kept.
        setTimeout(() => dismissAi(entry.id), Math.max(0, (entry.doneAt || Date.now()) + AI_DONE_MS - Date.now()));
        read().then(() => { if (alive.current) flash(ids); });
        return;
      }
      if (out.state === 'unknown') {
        result({ state: 'uncertain', msg: T.LOST_WRITE, replay: out.replay });
        check({ own: true });
        return;
      }
      if (out.state === 'blocked') {
        if (out.fix === 'gemini-key') set({ aiNoKey: true });
        result({ state: 'blocked', msg: out.message || T.AI_FAILED, fix: aiFix(out.fix, out.calendarYear), retry: true });
      } else if (out.state === 'stale') {
        result({ state: 'check', msg: T.STALE, retry: true });
        read();
      } else if (out.state === 'expired') {
        result({ state: 'not-entered', retry: true });
      } else if (out.state === 'unavailable') {
        result({ state: 'unavailable', msg: out.message || T.RO, retry: true });
        check();
      } else if (out.state === 'signin_required') {
        result({ state: 'check', msg: T.SESSION, fix: { label: '다시 로그인', run: relogin }, retry: true });
        set({ net: 'session' });
      } else {
        const fix = out.code === 'ATTENDANCE_DUPLICATE' && out.recordId ? { label: '그 줄 보기', run: () => gotoRow(out.recordId) } : null;
        result({ state: 'check', msg: out.message || T.AI_FAILED, fix, retry: true });
      }
    }
    function settleAi(entry, out) {
      try {
        finishAi(entry, out);
      } catch (err) {
        console.error(err);
        patchQ(entry.id, { state: 'check', msg: T.AI_FAILED, fix: null });
      }
      uiEvent(opts, 'settled', { queueId: entry.id, stage: 'settled' });
    }
    // Returns whether the sentence went into the queue. memo.aiText is cleared here, so a second Enter before the next render finds nothing to send.
    function queueAi() {
      const text = String(memo.aiText || '').trim();
      if (!data() || !text) return false;
      dropUndo();
      memo.aiText = '';
      memo.seq += 1;
      const entry = { id: memo.seq, text, year: data().year, run: opts.aiInput, owner: opts, state: 'waiting', msg: '', fix: null, head: '', lines: [], gotoId: '', retry: false, replay: null };
      uiEvent(opts, 'submit', { queueId: entry.id, stage: 'submit' });
      set({ aiText: '', notice: null, aiNoKey: false });
      // 확인 필요 lines stay until the next sentence goes in; a sentence still open (대기, 처리 중, 결과 확인 못 함) never leaves, and 입력함 leaves by its own timer.
      updateQ(list => list.filter(e => ['blocked', 'check', 'not-entered', 'unavailable'].indexOf(e.state) < 0).concat(entry));
      uiEvent(opts, 'queued', { queueId: entry.id, stage: 'queued', state: 'queued' });
      pumpAcceptedAi();
      return true;
    }
    function receiveQueue() {
      if (!alive.current || generation !== memo.gen) return;
      // Mark each raw completion consumed before settling, because finishAi patches the queue.
      const completed = memo.aiq.filter(e => e.state === 'completed');
      if (completed.length) {
        const ids = new Set(completed.map(e => e.id));
        memo.aiq = memo.aiq.map(e => ids.has(e.id) ? Object.assign({}, e, { state: 'settling', doneAt: e.doneAt || Date.now() }) : e);
        completed.forEach(e => settleAi(e, e.outcome));
      }
      set({ aiq: memo.aiq });
    }
    function attachQueue() {
      // A retained applied/stale answer already needs fresh data; revision checks from
      // other answers in the same batch must not race that full read into another one.
      const startup = { read: null, check: null, needsRead: memo.aiq.some(e => e.state === 'completed' && e.outcome && (e.outcome.state === 'applied' || e.outcome.state === 'stale')) };
      queueStartup = startup;
      memo.aiListener = receiveQueue;
      // Rebind only UI actions from completed answers; the worker and its request stay untouched.
      memo.aiq = memo.aiq.map(e => {
        if (!e.outcome || !e.fix) return e;
        const out = e.outcome;
        const fix = out.fix ? aiFix(out.fix, out.calendarYear) : out.state === 'signin_required'
          ? { label: '다시 로그인', run: relogin } : out.recordId ? { label: '그 줄 보기', run: () => gotoRow(out.recordId) } : null;
        return Object.assign({}, e, { fix });
      });
      try { receiveQueue(); } finally { queueStartup = null; }
      return startup;
    }
    function detachQueue() {
      if (memo.aiListener === receiveQueue) memo.aiListener = null;
    }
    // ↻ replays each held apply once with its same ID (G4, MAIN §7.3); nothing repeats by itself. An answer settles that sentence.
    async function replayHeld() {
      const held = memo.aiq.filter(e => e.state === 'uncertain' && e.replay);
      for (let i = 0; i < held.length; i++) {
        const entry = held[i];
        if (memo.aiReplays.has(entry.id)) continue;
        memo.aiReplays.add(entry.id);
        let out;
        const gen = memo.gen;
        try {
          out = aiOutcome(await opts.aiInput({ resume: entry.replay }));
        } catch (err) {
          console.error(err);
          if (gen === memo.gen) memo.aiReplays.delete(entry.id);
          continue;
        }
        if (gen !== memo.gen) return;
        memo.aiReplays.delete(entry.id);
        if (out.state === 'unknown') { patchQ(entry.id, { replay: out.replay || entry.replay }); continue; }
        // A requested replay may finish after another same-account window has mounted too.
        // Keep that completion in the same queue; only a generation change may discard it.
        updateQ(list => list.map(e => e.id === entry.id ? Object.assign({}, e, { state: 'completed', outcome: out, doneAt: Date.now() }) : e));
      }
    }
    function putBackAi(entry) {
      if (st().aiText.trim()) { explain({ text: '입력칸의 문장을 먼저 처리해 주세요.', tone: 'muted' }); return; }
      setAiText(entry.text);
      updateQ(list => list.filter(e => e.id !== entry.id));
      const box = bodyRef.current && bodyRef.current.querySelector('.att-entry-box');
      if (box) box.focus();
    }
    // ✕ only hides a line. A result still unknown stays held, so ↻ can settle it; nothing is written or repeated here (C7).
    function dismissAi(id) {
      if (generation !== memo.gen) return;
      if (memo.aiq.some(e => e.id === id && e.state === 'uncertain')) uiEvent(opts, 'removed', { queueId: id, stage: 'removed', state: 'hidden' });
      updateQ(list => list.reduce((out, e) => { if (e.id !== id) out.push(e); else if (e.state === 'uncertain') out.push(Object.assign({}, e, { hidden: true })); return out; }, []));
    }

    /* ---------- holidays ---------- */
    function openHolidayAdd(y) {
      const D = data();
      if (!D) return;
      openDialog({ kind: 'hol', orig: '', v: { d: holidayPreset(D, y), name: '', type: '공휴일', status: '', note: '', source: '' } });
    }

    /* ---------- class settings ---------- */
    async function saveSetting(fields, okDone) {
      const D = data();
      if (!D) return;
      set({ setBusy: true, notice: null });
      try {
        const out = await runWrite({ route: SET, body: Object.assign({ version: D.doc.version }, fields), lockKey: 'set' });
        if (!alive.current) return;
        if (out.kind === 'ok') {
          const docFields = Object.assign({}, fields);
          patchSnap(snap => Object.assign({}, snap, { yearDoc: Object.assign({}, snap.yearDoc, docFields, { version: out.body.version }) }));
          toast('학급 설정을 저장했어요.');
          if (okDone) okDone();
          read();
        } else {
          explain(whyOf(out));
          // Keep typed values on a conflict; the fresh read supplies the next version.
        }
      } finally {
        if (alive.current) set({ setBusy: false });
      }
    }

    function bindLive(el) { liveEl = el; }
    function primaryFromKeyboard(box) {
      const p = box && box.querySelector('[data-primary]');
      if (p) p.click();
    }
    function escapeInDialog(box) {
      const S = st();
      if (S.dialogBusy) return;
      const cancel = box && box.querySelector('[data-cancel]');
      if (dirty.on && cancel && document.activeElement !== cancel) { cancel.focus(); dirty.on = false; return; }
      closeDialog();
    }
    function s4Groups(W, D) {
      if (!s4.list) s4.list = W.s4.map(g => ({ id: g.id, ids: g.rows.map(r => r.id) }));
      const known = new Set(s4.list.map(g => g.id));
      W.s4.forEach(g => { if (!known.has(g.id)) s4.list.push({ id: g.id, ids: g.rows.map(r => r.id) }); });
      return s4.list.map(g => {
        const now = (W.s4.find(x => x.id === g.id) || { rows: [] }).rows;
        const ids = new Set(g.ids.concat(now.map(r => r.id)));
        const rows = Array.from(ids).map(id => D.rowById.get(id)).filter(r => r && !r.deletedAt).sort((a, b) => (a.date < b.date ? -1 : 1));
        return { id: g.id, st: D.byId.get(g.id), label: rows.length ? rows[0].label : '', rows };
      }).filter(g => g.rows.length);
    }

    /* ---------- AL10 student reports (2026-11-11 design §6.2, §13.1, §15) ---------- */
    // 기록하기 and 수정해서 기록 are one route: record, with the teacher's edits for the latter. One press, one
    // requestId. A lost answer locks the line as 기록 결과 확인 중 and ↻ replays the same requestId (replayLocks,
    // G4); it is never shown as recorded before a confirmed answer (C7).
    async function recordReport(rep, edits) {
      const key = 'report:' + rep.id;
      if (memo.locks[key] || st().busy[key]) return { kind: 'busy' };
      if (!rep.cat && !(edits && edits.category)) { openDialog({ kind: 'report-edit', rep }); return { kind: 'busy' }; }
      setIn('busy', { [key]: true });
      const body = { action: 'record', reportId: rep.id, version: rep.version };
      if (edits && Object.keys(edits).length) body.edits = edits;
      const out = await runWrite({ route: REPORTS, body, lockKey: key });
      if (out.forgotten) return out;
      set(prev => { const b = Object.assign({}, prev.busy); delete b[key]; return { busy: b }; });
      if (!alive.current) return out;
      if (out.kind === 'ok') {
        const ids = (out.body.rows || []).map(x => x && x.recordId).filter(Boolean);
        if (memo.reportDup[rep.id]) { const d = Object.assign({}, memo.reportDup); delete d[rep.id]; memo.reportDup = d; set({ reportDup: d }); }
        toast(out.body.recordStatus === 'already-recorded' ? '이미 기록된 신고예요.' : '출결 기록에 ' + ids.length + '줄 기록했어요.', ids.length ? { label: '그 줄 보기', run: () => gotoRow(ids[0]) } : null);
        await read();
        if (alive.current) flash(ids);
        return out;
      }
      if (out.kind === 'refused' && out.code === 'ATTENDANCE_DUPLICATE') {
        const holder = String(out.details.recordId || ''), r = holder && data() ? data().rowById.get(holder) : null;
        memo.reportDup = Object.assign({}, memo.reportDup, { [rep.id]: { recordId: holder, date: r ? r.date : '' } });
        set({ reportDup: memo.reportDup });
      } else if (out.kind === 'refused' && out.code === 'CATEGORY_REQUIRED' && !edits) {
        openDialog({ kind: 'report-edit', rep });
        return out;
      } else if (out.kind === 'refused' && (out.code === 'REPORT_DECIDED' || out.code === 'REPORT_NOT_FOUND')) {
        toast(out.message || '학생이 취소한 신고예요.');
        read();
        return out;
      }
      if (out.kind !== 'unknown' && !edits) explain(whyOf(out));
      return out;
    }

    return {
      recordReport,
      set, setIn, announce, toast, dropUndo, flash, explain, link, relogin, reapprove, roWhy, rowWhy, lockedRow, call, read, check, flushRemote,
      runWrite, whyOf, patchSnap, patchRows, moveRows, manualRefresh, closeWindow, openDialog, closeDialog, closeDialogNow, setTab, setView, setPeriod,
      toggleRow, selectRange, clearSel, gotoRow, chooseMonth, setRecSort, markDirty, openLB, openCellList, closeLB, pickLB,
      editCell, commitDate, commitStudent, moveCell, escapeCell, revertRow, enterRow, rowFocus, openStudentPop, openReasonPop, closePop, movePop, pickPop, followPop,
      setAiText, aiCause, aiBlock, queueAi, putBackAi, dismissAi, focusCell,
      openHolidayAdd, saveSetting, bindLive, primaryFromKeyboard, escapeInDialog, s4Groups, isBusy, adoptInitial,
      attachQueue, detachQueue, calendarStart, calendarEnd, calendarRelease, calendarBlur
    };
  }

  /* ---------- the window ---------- */
  function App(props) {
    const opts = props.opts;
    const [S, setS] = useState(() => initialState(opts));
    const D = useMemo(() => (S.snap ? prepare(S.snap) : null), [S.snap]);
    const W = useMemo(() => (D ? workItems(D) : null), [D]);
    const ref = useRef({ S, D });
    ref.current = { S, D };
    const alive = useRef(true);
    const bodyRef = useRef(null);
    const sheetRef = useRef(null);
    const dialogBox = useRef(null);
    const A = useRef(null);
    if (!A.current) A.current = createActions(opts, setS, ref, alive, bodyRef, sheetRef);
    const ctx = Object.assign({ S, D, W, opts, dialogBox, sheetRef }, A.current);
    if (opts.controller) opts.controller.current = {
      read: () => A.current.manualRefresh(),
      hasDialog: () => !!ref.current.S.dialog,
      canLeave: () => {
        const current = ref.current.S;
        if (current.dialog || current.dialogBusy || current.setBusy) {
          A.current.explain({ text: current.dialogBusy || current.setBusy ? '저장하는 중이에요.' : '열린 창에서 저장하거나 취소해 주세요.', tone: 'muted' });
          return false;
        }
        return true;
      }
    };

    useEffect(() => {
      alive.current = true;
      const startup = opts.card ? {} : A.current.attachQueue();
      if (!startup.read) { if (opts.initialRead && S.snap) A.current.adoptInitial(!!startup.check); else A.current.read(); }
      let blurred = false, focusTimer = 0;
      const onBlur = () => { blurred = true; A.current.calendarBlur(); };
      // Deferred, so the click that brought the window back lands first (LOCK-01, OBS-07).
      const onFocus = () => { if (!blurred) return; blurred = false; clearTimeout(focusTimer); focusTimer = setTimeout(() => { A.current.calendarEnd(); A.current.check(); }, FOCUS_CHECK_DELAY_MS); };
      window.addEventListener('blur', onBlur);
      window.addEventListener('focus', onFocus);
      const every = setInterval(() => A.current.check(), CHECK_EVERY_MS);
      const onKey = ev => { if (opts.card && !ref.current.S.dialog && !ev.target.closest?.('.att-card-embed')) return; onDocumentKey(ev, ref, A.current, dialogBox); };
      document.addEventListener('keydown', onKey);
      // Which row of the sheet the teacher is in. Leaving it saves it, whether by focus or by a press on anything that takes no focus. Switching to another program leaves nothing.
      const keyOfTarget = t => {
        if (!t || !t.closest) return null;
        const tr = t.closest('tr[data-key]');
        if (tr) return tr.dataset.key;
        if (t.closest('.att-lb')) { const lb = ref.current.S.lb; return lb && lb.sheet ? lb.sheet.key : null; }
        return null;
      };
      const onFocusIn = ev => A.current.rowFocus(keyOfTarget(ev.target));
      const onPress = ev => {
        const t = ev.target;
        if (!t.closest || !t.closest('.att-date-picker')) A.current.calendarEnd(true);
        if (t === sheetRef.current || (t.closest && t.closest('.att-pop'))) return;
        A.current.rowFocus(keyOfTarget(t));
      };
      const onRelease = () => A.current.calendarRelease();
      document.addEventListener('focusin', onFocusIn);
      document.addEventListener('mousedown', onPress, true);
      document.addEventListener('mouseup', onRelease, true);
      document.addEventListener('click', onRelease, true);
      return () => {
        alive.current = false;
        if (!opts.card) A.current.detachQueue();
        clearTimeout(focusTimer);
        clearInterval(every);
        window.removeEventListener('blur', onBlur);
        window.removeEventListener('focus', onFocus);
        document.removeEventListener('keydown', onKey);
        document.removeEventListener('focusin', onFocusIn);
        document.removeEventListener('mousedown', onPress, true);
        document.removeEventListener('mouseup', onRelease, true);
        document.removeEventListener('click', onRelease, true);
      };
    }, []);

    useEffect(() => { A.current.flushRemote(); }, [S.remotePending, S.dialog, S.dialogBusy, S.setEdit, S.setBusy, S.busy, S.pending, S.reading]);
    const setupOpened = useRef(false);
    useEffect(() => {
      if (D && opts.chatSetup && !setupOpened.current) {
        setupOpened.current = true;
        A.current.openDialog({ kind: 'welcome', ...(opts.chatSetup.ids ? { ids: opts.chatSetup.ids } : {}) });
      }
    }, [D]);

    // 그 줄 보기 for a row the last read did not have: once the new read is in.
    useEffect(() => {
      if (!S.gotoId || S.reading || !D) return;
      const id = S.gotoId;
      A.current.set({ gotoId: null });
      if (D.rowById.has(id)) A.current.gotoRow(id);
    }, [S.gotoId, S.reading, D]);

    // The count goes through the polite live region (SCREENS "Keyboard").
    const announcedSel = useRef(0);
    useEffect(() => {
      if (S.sel.size === announcedSel.current) return;
      announcedSel.current = S.sel.size;
      A.current.announce(S.sel.size ? S.sel.size + '줄 선택' : '선택 없음');
    }, [S.sel.size]);

    // Window height decides the short head.
    useLayoutEffect(() => {
      const measure = () => {
        const short = window.innerHeight < SHORT_HEIGHT;
        if (short !== ref.current.S.short) A.current.set({ short });
      };
      measure();
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }, []);

    // The window's place, restored after a linked window closes.
    const restored = useRef(false);
    useLayoutEffect(() => {
      if (restored.current || !D || !bodyRef.current) return;
      restored.current = true;
      if (S.restoreTop) bodyRef.current.scrollTop = S.restoreTop;
    }, [D]);

    // Scrolls to a row once it is rendered (출결 기록에서 보기, 그 줄 보기).
    useEffect(() => {
      if (S.scrollTo === null || !bodyRef.current) return;
      const tr = bodyRef.current.querySelector('tr[data-rid="' + CSS.escape(String(S.scrollTo)) + '"]');
      if (tr) {
        tr.scrollIntoView({ block: 'center' });
        (tr.querySelector('[data-cell="student"]') || tr).focus({ preventScroll: true });
        A.current.set({ scrollTo: null });
      } else if (D && !D.rowById.has(S.scrollTo)) {
        A.current.set({ scrollTo: null });
      }
    });
    useEffect(() => {
      if (!S.lb) return undefined;
      const onDown = ev => {
        const t = ev.target;
        if (t.closest && (t.closest('.att-lb') || t === S.lb.el || S.lb.el.contains(t))) return;
        A.current.set({ lb: null });
      };
      document.addEventListener('mousedown', onDown);
      return () => document.removeEventListener('mousedown', onDown);
    }, [S.lb]);

    if (opts.card) return html`<div class="att att-card-embed">
      <${Bands} ctx=${ctx} />
      ${opts.card === 'connect' ? null : html`<div class="att-pbar"><span class="att-subtle" role="status">${S.reading ? '읽는 중…' : S.readError || (S.readAt ? readStamp(S.readAt) + '에 읽음' : '')}</span><span class="att-spacer"></span><${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${S.reading ? { text: T.REFRESHING, tone: 'muted' } : null} onClick=${ctx.manualRefresh}>새로 읽기</${Btn}></div>`}
      <div ref=${bodyRef}><${Boundary} opts=${opts}><${Body} ctx=${Object.assign({}, ctx, { card: opts.card })} /></${Boundary}></div>
      ${S.dialog ? html`<${DialogHost} ctx=${ctx} />` : null}
      ${S.toast ? html`<div class="att-toast" role="status">${S.toast.text}</div>` : null}
      <div class="att-sr" aria-live="polite" ref=${el => A.current.bindLive(el)}></div>
    </div>`;
    const onBackdrop = ev => { if (ev.target === ev.currentTarget && !S.dialog) A.current.closeWindow(); };
    const sheetMode = S.tab === 'rec' && !S.stateNote && !(S.readError && !D);
    return html`
      <div class=${'att att-overlay' + (S.short ? ' att-short' : '')} onClick=${onBackdrop}>
        <div class="att-modal" role="dialog" aria-labelledby="att-title" inert=${S.dialog ? true : null}>
          <${Head} ctx=${ctx} />
          <${Tabs} ctx=${ctx} />
          <${Bands} ctx=${ctx} />
          <div class=${'att-body' + (sheetMode ? ' att-body-rec' : '')} ref=${bodyRef} onScroll=${S.lb ? () => A.current.closeLB() : null}>
            ${D ? html`<${DraftRecovery} ctx=${ctx} />` : null}
            <${Boundary} opts=${opts}><${Body} ctx=${ctx} /></${Boundary}>
          </div>
        </div>
        ${S.dialog ? html`<${DialogHost} ctx=${ctx} />` : null}
        ${S.lb ? html`<${ValueList} ctx=${ctx} />` : null}
        ${S.pop ? html`<${CellPop} ctx=${ctx} />` : null}
        ${S.toast ? html`<div class="att-toast" role="status"><span>${S.toast.text}</span>${S.toast.act ? html`<button type="button" onClick=${S.toast.act.run}>${S.toast.act.label}</button>` : null}</div>` : null}
        <div class="att-sr" aria-live="polite" ref=${el => A.current.bindLive(el)}></div>
      </div>`;
  }

  // A paint failure must not leave a blank window or hide itself (OBS-09).
  function Boundary(p) {
    const pair = useErrorBoundary(err => { console.error(err); uiEvent(p.opts, 'page_exception', { errorType: err && err.name || 'Unknown', script: 'attendance.js' }); });
    if (pair[0]) return html`<div class="att-notice att-warn" role="alert">${T.PAINT_FAILED}</div>`;
    return p.children;
  }

  // A failure outside the tab views, such as an answer that cannot be prepared: the window can still close.
  function TopBoundary(p) {
    const pair = useErrorBoundary(err => { console.error(err); uiEvent(p.opts, 'page_exception', { errorType: err && err.name || 'Unknown', script: 'attendance.js' }); });
    if (!pair[0]) return p.children;
    return html`<div class="att att-overlay"><div class="att-modal" role="alert">
      <div class="att-head"><h2 class="att-title">출결·메신저</h2><button type="button" class="att-x" aria-label="닫기" onClick=${() => p.opts.onClose && p.opts.onClose()}>✕</button></div>
      <div class="att-body"><div class="att-notice att-warn">${T.PAINT_FAILED}</div></div></div></div>`;
  }

  function onDocumentKey(ev, ref, A, dialogBox) {
    if (ev.isComposing || ev.keyCode === 229) return;
    const S = ref.current.S;
    if (ev.key === 'Escape') {
      if (S.lb) { ev.preventDefault(); A.closeLB(true); return; }
      if (S.pop) { ev.preventDefault(); A.closePop(); return; }
      if (S.dialog) { ev.preventDefault(); A.escapeInDialog(dialogBox.current); return; }
      const t = ev.target;
      if (t && t.closest && t.closest('input,select,textarea')) return;
      if (S.sel.size) { A.clearSel(); return; }
      A.closeWindow();
      return;
    }
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
      if (S.dialog) { ev.preventDefault(); A.primaryFromKeyboard(dialogBox.current); }
    }
  }

  function Head(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    const spin = S.reading || (!D && !S.readError && !S.stateNote && S.net !== 'session');
    const read = S.readError ? '' : !D && spin ? '읽는 중…' : S.readAt ? readStamp(S.readAt) + '에 읽음' : '';
    return html`<div class="att-head">
      <h2 class="att-title" id="att-title">${D ? titleOf(D) : '출결·메신저'}</h2>
      <span class="att-save">${read}</span>
      <${Btn} ctx=${ctx} cls=${'att-refresh' + (spin ? ' att-checking' : '')} title="새로 읽기" label="새로 읽기" why=${spin ? { text: T.REFRESHING, tone: 'muted' } : null} onClick=${ctx.manualRefresh}><${RefreshIcon} /></${Btn}>
      <button type="button" class="att-x" aria-label="닫기" onClick=${() => ctx.closeWindow(true)}>✕</button>
    </div>`;
  }

  function Tabs(p) {
    const ctx = p.ctx, S = ctx.S, W = ctx.W;
    const keyNav = ev => {
      if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
      ev.preventDefault();
      const i = TABS.findIndex(x => x[0] === S.tab), n = TABS[(i + (ev.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length][0];
      ctx.setTab(n);
      const box = ev.currentTarget;
      setTimeout(() => { const el = box.querySelector('[data-tab="' + n + '"]'); if (el) el.focus(); }, 0);
    };
    return html`<div class="att-tabs" role="tablist" aria-label="출결·메신저 화면" onKeyDown=${keyNav}>
      ${TABS.map(t => {
        const n = W && !W.partial ? t[0] === 'work' ? W.count : t[0] === 'msg' ? W.due.length + W.s1.filter(x => x.refs?.some(r => r.includes('/messages/'))).length : 0 : 0;
        return html`<button type="button" key=${t[0]} data-tab=${t[0]} class=${'att-tab' + (S.tab === t[0] ? ' att-on' : '')} role="tab" aria-selected=${S.tab === t[0] ? 'true' : 'false'}
          tabindex=${S.tab === t[0] ? 0 : -1} onClick=${() => ctx.setTab(t[0])}>${t[1]}${n ? html`<span class="att-cnt" aria-label=${n + '개'}>${n}</span>` : null}</button>`;
      })}
    </div>`;
  }

  function Bands(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    const bands = [];
    if (S.net === 'down') bands.push(html`<div key="down" class="att-band att-muted" role="status">${'서버에 연결하지 못했어요' + (S.readAt ? ' · ' + readStamp(S.readAt).replace(/\(.\)/, '') + '에 읽은 내용이에요' : '')}</div>`);
    if (S.net === 'session') bands.push(html`<div key="session" class="att-band att-warn" role="status"><span>${T.SESSION}</span><span class="att-spacer"></span><button type="button" class="att-btn-quiet att-btn-sm" onClick=${ctx.relogin}>다시 로그인</button></div>`);
    // MAIN §7.9: from 1 March the newest year turns read-only until the teacher starts the new one.
    const offer = D && D.readOnly && D.raw.currentYearReady === false && D.year === D.raw.activeYear && S.net === 'ok';
    if (D && D.readOnly) bands.push(html`<div key="earlier" class=${'att-band ' + (offer ? 'att-warn' : 'att-muted')} role="status"><span>${offer ? D.raw.currentSchoolYear + '학년도가 시작됐어요. 새 학년도를 시작하면 이어서 쓸 수 있어요.' : earlierYearText(D.year)}</span>${offer ? html`<span class="att-spacer"></span><button type="button" class="att-btn-quiet att-btn-sm" onClick=${() => ctx.openDialog({ kind: 'rollover' })}>새 학년도 시작…</button>` : null}</div>`);
    if (D && !D.readOnly && D.doc.rolledOverFrom && !D.students.length && S.net === 'ok') bands.push(html`<div key="rostercopy" class="att-band att-warn" role="status"><span>${D.doc.rolledOverFrom + '학년도 학생명단을 그대로 가져올 수 있어요.'}</span><span class="att-spacer"></span><button type="button" class="att-btn-quiet att-btn-sm" onClick=${() => ctx.openDialog({ kind: 'rostercopy' })}>작년 학생명단 복사…</button></div>`);
    if (S.remotePending === 'remote') bands.push(html`<div key="remote" class="att-band att-muted" role="status">다른 컴퓨터에서 바뀐 내용이 있어요. 지금 하는 일을 마치면 불러와요.</div>`);
    return bands.length ? bands : null;
  }

  function Body(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    if (S.stateNote === 'import') return html`<div class="att-notice att-muted" role="status">${T.IMPORTING}</div>`;
    if (S.stateNote === 'import-failed') return html`<div class="att-notice att-muted" role="status">${T.IMPORT_FAILED}</div>`;
    if (S.stateNote === 'no-year') return html`<div class="att-notice att-muted" role="status">${T.NO_YEAR}</div>`;
    if (S.readError && !D) return html`<div class="att-notice att-muted" role="status">${S.readError}</div>`;
    if (ctx.card === 'holidays') return html`<${HolidaysTab} ctx=${ctx} />`;
    if (ctx.card === 'identity') return html`<${IdentitySettings} ctx=${ctx} />`;
    if (ctx.card === 'connect') return D ? html`<${NoticeSlot} ctx=${ctx} /><${ClassChatRow} ctx=${ctx} /><${StudentLinkRow} ctx=${ctx} />` : chatRows(CHAT_ROWS_READING);
    const View = { work: WorkTab, rec: RecordsTab, msg: MessagesTab, sum: SummaryTab }[S.tab] || RecordsTab;
    return html`<${View} ctx=${ctx} />`;
  }

  /* ---------- entry bar ---------- */
  function EntryBar(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    const boxRef = useRef(null);
    useLayoutEffect(() => {
      const t = boxRef.current;
      if (!t) return;
      t.style.height = '34px';
      t.style.height = Math.min(78, Math.max(34, t.scrollHeight + 2)) + 'px';
    });
    const cause = D ? ctx.aiCause() : null, block = ctx.aiBlock();
    // Enter sends and Shift+Enter breaks the line. While a Hangul syllable is still being composed the Enter belongs to the IME (isComposing or keyCode 229);
    // the Enter that follows finds the last syllable already in the box, which is what is read here, not the state a render behind.
    const onKey = ev => {
      if (ev.isComposing || ev.keyCode === 229) return;
      if (ev.key !== 'Enter' || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      const box = ev.currentTarget, strong = ev.ctrlKey || ev.metaKey;
      if (ev.repeat || (!box.value.trim() && !strong)) return;
      ctx.setAiText(box.value);
      const why = ctx.aiBlock();
      if (why) ctx.explain(why);
      else if (ctx.queueAi()) box.value = '';
    };
    return html`
      <div class="att-entry">
        <span class="att-el">${T.AI_LABEL}</span>
        <textarea class="att-entry-box" ref=${boxRef} rows="1" placeholder="예: 7번 조OO 오늘 늦잠으로 지각" aria-label=${T.AI_LABEL} value=${S.aiText}
          onInput=${ev => ctx.setAiText(ev.target.value)} onKeyDown=${onKey}></textarea>
        <span class="att-entry-acts">
          <${Btn} ctx=${ctx} cls="att-btn" title="Enter" why=${block} onClick=${ctx.queueAi}>AI로 입력</${Btn}>
        </span>
      </div>
      ${cause ? html`<div class="att-ecause"><div class="att-notice att-warn att-grow">${cause.text}</div><${FixBtn} fix=${cause.fix} /></div>` : null}
      <${AiQueue} ctx=${ctx} />`;
  }

  // A space of two lines that is always there, so nothing below it moves: 처리 중… and the count waiting, 입력함 for a few seconds, then empty.
  // Amber lines (확인 필요, 결과 확인 못 함) stay until the next sentence or ↻ and may make the space taller.
  function AiQueue(p) {
    const ctx = p.ctx, q = ctx.S.aiq.filter(e => !e.hidden);
    const boxRef = useRef(null);
    useLayoutEffect(() => { if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight; }, [q.length]);
    const waiting = q.filter(e => e.state === 'waiting');
    const shown = q.filter(e => e.state !== 'waiting' || e === waiting[0]);
    const grow = q.some(e => ['waiting', 'running', 'applied'].indexOf(e.state) < 0);
    return html`<div class=${'att-q' + (grow ? ' att-q-grow' : '')} ref=${boxRef} role="status">${shown.map(e => (e.state === 'waiting'
      ? html`<div class="att-resl" key="waiting"><${Chip} tone="muted">대기</${Chip}><span class="att-resl-tx">${'기다리는 문장 ' + waiting.length + '개'}</span></div>`
      : html`<${AiLine} key=${e.id} ctx=${ctx} e=${e} />`))}</div>`;
  }
  function AiLine(p) {
    const ctx = p.ctx, e = p.e;
    const said = html`<span class="att-q-said" title=${e.text}>${'“' + e.text + '”'}</span>`;
    const again = e.retry ? html`<button type="button" class="att-btn-quiet att-btn-sm" onClick=${() => ctx.putBackAi(e)}>다시 입력</button>` : null;
    const dismiss = html`<button type="button" class="att-ic-btn" aria-label="안내 닫기" onClick=${() => ctx.dismissAi(e.id)}>✕</button>`;
    if (e.state === 'running' || e.state === 'completed' || e.state === 'settling') return html`<div class="att-resl"><${Chip} tone="muted">처리 중…</${Chip}><span class="att-resl-tx">${said}</span></div>`;
    if (e.state === 'uncertain') return html`<div class="att-resl"><${Chip} tone="warn">결과 확인 못 함</${Chip}><span class="att-resl-tx">${e.msg} ${said}</span>${dismiss}</div>`;
    if (e.state === 'blocked') return html`<div class="att-notice att-warn att-nrow att-q-line"><span class="att-notice-tx">${e.msg} ${said}</span><${FixBtn} fix=${e.fix} />${again}${dismiss}</div>`;
    if (e.state === 'check') return html`<div class="att-resl"><${Chip} tone="warn">확인 필요</${Chip}><span class="att-resl-tx">${e.msg} ${said}</span><${FixBtn} fix=${e.fix} />${again}${dismiss}</div>`;
    if (e.state === 'not-entered') return html`<div class="att-resl"><${Chip} tone="muted">${T.NOT_ENTERED}</${Chip}><span class="att-resl-tx">${said}</span>${again}${dismiss}</div>`;
    if (e.state === 'unavailable') return html`<div class="att-resl"><${Chip} tone="muted">${e.msg}</${Chip}><span class="att-resl-tx">${said}</span>${again}${dismiss}</div>`;
    return html`<div class="att-resl"><${Chip} tone="ok">입력함</${Chip}><span class="att-resl-tx">${e.head}
      ${e.lines.map(line => html`<div class="att-rec">${line}</div>`)}</span>
    </div>`;
  }

  /* ---------- period and filter bars ---------- */
  function PeriodSeg(p) {
    const ctx = p.ctx, D = ctx.D, v = ctx.S.view, cal = D.cal;
    const segs = [];
    if (cal.inYear) {
      segs.push(['week', '이번 주', v.period === 'week' && v.wk === mondayOf(D.today)]);
      segs.push(['this', '이번 달(' + monOf(cal.thisYm + '-01') + '월)', v.period === 'month' && v.ym === cal.thisYm]);
      if (cal.lastYm) segs.push(['last', '지난달(' + monOf(cal.lastYm + '-01') + '월)', v.period === 'month' && v.ym === cal.lastYm]);
      segs.push(['term', '이번 학기(' + cal.term.label + ')', v.period === 'term']);
    }
    segs.push(['year', '학년 전체', v.period === 'year']);
    segs.push(['custom', '기간 선택…', v.period === 'custom']);
    return html`<span class="att-seg" role="group" aria-label="기간">
      ${segs.map(s => html`<button type="button" key=${s[0]} class=${s[2] ? 'att-on' : ''} aria-pressed=${s[2] ? 'true' : 'false'} onClick=${() => ctx.setPeriod(s[0])}>${s[1]}</button>`)}</span>`;
  }
  const FilterSelect = p => html`<label class="att-fl">${p.label} <select value=${p.value} onChange=${ev => p.onChange(ev.target.value)}>${p.options.map(o => html`<option value=${o[0]} selected=${o[0] === p.value}>${o[1]}</option>`)}</select></label>`;
  function studentFilter(ctx) {
    const D = ctx.D, v = ctx.S.view;
    return html`<${FilterSelect} label="학생" value=${v.stu} onChange=${val => ctx.setView({ stu: val })}
      options=${[['', '전체 (' + D.students.length + '명)']].concat(D.students.map(s => [s.id, s.label + (s.status !== '재적' ? ' (' + s.status + ')' : '')]))} />`;
  }
  const catFilter = ctx => html`<${FilterSelect} label="구분" value=${ctx.S.view.cat} onChange=${val => ctx.setView({ cat: val })} options=${[['', '전체']].concat(CATS.map(c => [c, c]))} />`;
  const kindFilter = ctx => html`<${FilterSelect} label="종류" value=${ctx.S.view.kind} onChange=${val => ctx.setView({ kind: val })} options=${[['', '전체']].concat(KINDS.map(c => [c, c]))} />`;
  const searchBox = ctx => html`<input class="att-search" type="search" placeholder="이름·사유 검색" aria-label="이름·사유 검색" value=${ctx.S.view.q} onInput=${ev => ctx.setView({ q: ev.target.value })} />`;

  /* ---------- 출결 기록: a sheet of one month (D26, D27) ---------- */
  // Column widths. No stacking at any width: below the sum the table scrolls sideways with the first three columns pinned.
  // Google Chat 알림 shows only when some row of the year has a reminder state (D28).
  const sheetCols = (D, rec) => [28, 100, 88, 78, 62, null, 58, 80, 80].concat(D && D.hasReminders ? [104] : [], [28, 64]);
  const REASON_MIN = 90;
  const SHEET_ROW_H = 34;
  const SHORT_HEIGHT = 480;
  const READ_SOON_MS = 1200, REASON_SUGGESTIONS = 6, AI_DONE_MS = 5000;
  const CELL_ORDER = ['ck', 'date', 'student', 'cat', 'kind', 'reason', 'period', 'g', 'h'];
  const DOC_OPTIONS = [['', '(비움)'], ['제출', '제출'], ['미제출', '미제출'], ['해당없음', '해당없음']];
  const sheetOptions = field => (field === 'cat' ? CATS.map(c => [c, c]) : field === 'kind' ? KINDS.map(c => [c, c])
    : field === 'period' ? PERIODS.map(x => [x, x || '—']) : DOC_OPTIONS);

  function RecordsTab(p) {
    const ctx = p.ctx;
    return html`
      <${EntryBar} ctx=${ctx} />
      <${SheetBar} ctx=${ctx} />
      <${NoticeSlot} ctx=${ctx} />
      <${SheetBlock} ctx=${ctx} />`;
  }

  function barCheck(ctx, k, rows) {
    const ro = ctx.roWhy();
    if (ro) return ro;
    if (!rows.length) return { text: T.PICK, tone: 'muted' };
    if (rows.length > LIMIT_TICKED) return { text: '한 번에 ' + LIMIT_TICKED + '줄까지 할 수 있어요. 줄을 줄여 다시 골라 주세요.', tone: 'muted' };
    if (rows.some(r => ctx.lockedRow(r.id))) return { text: T.LOST_WRITE };
    if (k === 'docs') return rows.some(r => !r.deletedAt) ? null : { text: '선택한 줄에는 서류 상태를 바꿀 줄이 없어요.', tone: 'muted' };
    if (k === 'reports') return rows.some(r => !r.deletedAt && r.date && /결석|지각|조퇴|결과/.test(r.kind || '')) ? null : { text: '선택한 줄에는 신고서를 만들 줄이 없어요.', tone: 'muted' };
    if (k === 'tasks') {
      if (!ctx.D.doc.taskListId) return { text: 'Tasks 목록을 아직 고르지 않았어요. 연결 → 출결의 Tasks에서 골라 주세요.' };
      return rows.some(r => !r.deletedAt && (r.g === '미제출' || r.h === '미제출')) ? null : { text: '선택한 줄에는 미제출 서류가 없어요.', tone: 'muted' };
    }
    if (k === 'del') {
      const n = rows.filter(E.canDelete).length;
      if (!n) return { text: '선택한 줄은 지금 삭제할 수 없어요.', tone: 'muted' };
      return n > LIMIT_DELETE ? { text: '한 번에 ' + LIMIT_DELETE + '줄까지 삭제할 수 있어요. 줄을 줄여 다시 골라 주세요.', tone: 'muted' } : null;
    }
    return null;
  }

  // The month strip and, on the same line, the table bar.
  function SheetBar(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D, rec = S.rec;
    const months = MONTHS.map(m => {
      const ym = D ? monthYm(D.cal, m) : '', on = !!D && rec.ym === ym;
      return html`<button type="button" key=${m} class=${on ? 'att-on' : ''} aria-pressed=${on ? 'true' : 'false'} onClick=${() => { if (D) ctx.chooseMonth(ym); }}>${m + '월'}</button>`;
    });
    const strip = html`<span class="att-seg att-months" role="group" aria-label="달">${months}</span>`;
    if (!D) return html`<div class="att-sheetbar">${strip}</div>`;
    const rows = Array.from(S.sel).map(id => D.rowById.get(id)).filter(Boolean), n = S.sel.size;
    const count = sheetRows(D, rec).length + sheetUndated(D).length;
    const left = n
      ? html`<span class="att-tl"><b>${n + '줄 선택'}</b><button type="button" class="att-link" onClick=${ctx.clearSel}>선택 해제</button></span>`
      : html`<span class="att-tl"><span class="att-tbar-cnt">${count + '건'}</span></span>`;
    const lab = (base, cnt, unit) => (n && cnt ? base + ' ' + cnt + unit + '…' : base + '…');
    const acts = html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${barCheck(ctx, 'docs', rows)} onClick=${() => ctx.openDialog({ kind: 'docs', ids: rows.map(r => r.id) })}>${lab('서류 상태 바꾸기', rows.length, '줄')}</${Btn}>
        <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${barCheck(ctx, 'reminders', rows)} onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'reminders', ids: rows.map(r => r.id) })}>독려 보내기…</${Btn}>
        <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${barCheck(ctx, 'reports', rows)} onClick=${() => ctx.openDialog({ kind: 'reports', ids: rows.map(r => r.id) })}>신고서 만들기…</${Btn}>
        <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${barCheck(ctx, 'tasks', rows)} onClick=${() => ctx.openDialog({ kind: 'tasks', ids: rows.map(r => r.id) })}>Tasks에 추가…</${Btn}>
        <span class="att-vsep" aria-hidden="true"></span>
        <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${barCheck(ctx, 'del', rows)} onClick=${() => ctx.openDialog({ kind: 'del', ids: rows.map(r => r.id) })}>${lab('삭제', rows.filter(E.canDelete).length, '줄')}</${Btn}>`;
    return html`<div class="att-sheetbar">${strip}${left}
      <span class="att-ta">${acts}</span></div>`;
  }

  function SortHead(p) {
    const ctx = p.ctx, sort = ctx.S.rec.sort, on = sort.key === p.k;
    return html`<button type="button" class="att-thb" onClick=${() => ctx.setRecSort(p.k)}>${p.label}<span class=${'att-ar' + (on ? ' att-on' : '')} aria-hidden="true">${on ? (sort.dir < 0 ? '▼' : '▲') : '⇅'}</span></button>`;
  }

  function SheetBlock(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D, rec = S.rec;
    const done = useRef(0);
    // The opening position: the input row near the middle of the table area, the last rows above it and room below, so entry reads as continuing to write.
    useLayoutEffect(() => {
      const box = ctx.sheetRef.current, go = S.sheetGo;
      if (!box || !D || !go || go.seq === done.current || !rec.ym) return;
      done.current = go.seq;
      if (S.scrollTo !== null) return;
      if (go.top !== undefined) { box.scrollTop = go.top; return; }
      const entered = box.querySelectorAll('tbody tr[data-key]');
      const row = box.querySelector('tr[data-key="new"]') || entered[entered.length - 1] || null;
      if (go.center) {
        if (row) {
          const head = box.querySelector('thead'), headH = head ? head.offsetHeight : 0;
          const top = row.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
          box.scrollTop = Math.max(0, top - headH - (box.clientHeight - headH - row.offsetHeight) / 2);
        } else box.scrollTop = 0;
      }
      if (go.focus && row && row.dataset.key === 'new') {
        const tr = row, draft = S.drafts.new, date = draft ? draft.f.date : newRowDefaults(D, rec).date;
        const el = tr.querySelector('[data-cell="' + (date ? 'student' : 'date') + '"]');
        const active = document.activeElement;
        // Never away from a box the teacher is already typing in.
        if (el && !(active && active.matches && active.matches('textarea,input:not([type="checkbox"])'))) el.focus({ preventScroll: true });
      }
    });
    const cols = sheetCols(D, rec), showChat = !!D && D.hasReminders;
    const colgroup = html`<colgroup>${cols.map(w => (w ? html`<col style=${'width:' + w + 'px'} />` : html`<col />`))}</colgroup>`;
    const minWidth = 'min-width:' + (cols.reduce((a, w) => a + (w || 0), 0) + REASON_MIN) + 'px';
    if (!D) {
      return html`<div class="att-sheetwrap" ref=${ctx.sheetRef}><table class="att-rt att-sheet" style=${minWidth}>${colgroup}<tbody>${[70, 55, 80, 60, 75, 50].map(w => html`<tr class="att-ph"><td colspan=${cols.length}><span class="att-phb" style=${'width:' + w + '%'}></span></td></tr>`)}</tbody></table></div>`;
    }
    const rows = sheetRows(D, rec), undated = sheetUndated(D);
    const showInput = !D.readOnly;
    const allSel = rows.length > 0 && rows.every(r => S.sel.has(r.id)), someSel = rows.some(r => S.sel.has(r.id));
    const aria = k => (rec.sort.key === k ? (rec.sort.dir < 0 ? 'descending' : 'ascending') : 'none');
    const tickAll = ev => {
      const on = ev.target.checked;
      ctx.set(prev => { const sel = new Set(prev.sel); rows.forEach(r => { if (on) sel.add(r.id); else sel.delete(r.id); }); return { sel }; });
    };
    let shade = false, prevD = null, prevKey = null;
    const keyOf = r => (rec.sort.key === 'name' ? r.label : rec.sort.key === 'cat' ? r.cat : rec.sort.key === 'kind' ? r.kind : r.date);
    const body = rows.map((r, i) => {
      const start = r.date !== prevD;
      if (start && i > 0) shade = !shade;
      prevD = r.date;
      const key = keyOf(r), keyFirst = key !== prevKey;
      prevKey = key;
      return html`<${SheetRow} key=${r.id} ctx=${ctx} k=${r.id} r=${r} o=${{ shade, runStart: (start && i > 0) || (!i && undated.length > 0), dateFull: start, keyFirst }} />`;
    });
    const defaults = newRowDefaults(D, rec);
    const fill = Math.ceil(window.innerHeight / 2 / SHEET_ROW_H) + 1;
    const fillers = [];
    for (let i = 0; i < fill; i++) fillers.push(html`<tr class="att-fill" key=${'fill' + i} aria-hidden="true">${cols.map(() => html`<td></td>`)}</tr>`);
    return html`<div class="att-sheetwrap" ref=${ctx.sheetRef} onScroll=${S.pop ? ctx.followPop : S.lb ? () => ctx.closeLB() : null}>
      <table class="att-rt att-sheet" style=${minWidth} aria-label=${monOf(rec.ym + '-01') + '월 출결 기록'}>${colgroup}
        <thead><tr>
          <th class="att-ck" scope="col"><label class="att-ckl"><input type="checkbox" checked=${allSel} ref=${el => { if (el) el.indeterminate = !allSel && someSel; }} aria-label="보이는 줄 모두 선택" onChange=${tickAll} /></label></th>
          <th aria-sort=${aria('date')}><${SortHead} ctx=${ctx} k="date" label="날짜" /></th>
          <th aria-sort=${aria('name')}><${SortHead} ctx=${ctx} k="name" label="번호+이름" /></th>
          <th aria-sort=${aria('cat')}><${SortHead} ctx=${ctx} k="cat" label="구분" /></th>
          <th aria-sort=${aria('kind')}><${SortHead} ctx=${ctx} k="kind" label="종류" /></th>
          <th>사유</th><th>교시</th><th>신고서</th><th>첨부</th>${showChat ? html`<th>Google Chat 알림</th>` : null}<th>AI</th><th><span class="att-sr">작업</span></th>
        </tr></thead>
        <tbody>
          ${undated.length ? html`<tr class="att-pinh" key="pin"><td colspan=${cols.length}><span class="att-pin-tx">${'날짜 확인 필요 ' + undated.length + '줄'}</span></td></tr>` : null}
          ${undated.map(r => html`<${SheetRow} key=${r.id} ctx=${ctx} k=${r.id} r=${r} o=${{ shade: false, runStart: false, dateFull: true, keyFirst: true }} />`)}
          ${body}
          ${showInput ? S.pending.map(x => html`<${SheetRow} key=${x.key} ctx=${ctx} k=${x.key} r=${null} defaults=${defaults} o=${{ entry: x }} />`) : null}
          ${showInput ? html`<${SheetRow} key="new" ctx=${ctx} k="new" r=${null} defaults=${defaults} o=${{ input: true }} />` : null}
          ${fillers}
        </tbody></table></div>`;
  }

  function remCell(ctx, r) {
    const rem = r.reminder, today = ctx.D.today;
    if (r.deletedAt || !r.date || !E.inRoster(r) || !rem || !rem.state) return faint('—');
    if (rem.state === '보냄') {
      const live = ctx.S.net === 'ok';
      return html`<${Chip} tone=${live ? 'ok' : 'muted'} title=${'Google Chat 알림 · ' + atLong(rem.sentAt) + (live ? '' : ' · ' + atLong(ctx.S.readAt) + ' 기준')}>${'보냄 · ' + atShort(rem.sentAt, today)}</${Chip}>`;
    }
    if (r.remUnknown) return html`<${Chip} tone="warn" title=${atLong(rem.sentAt)}>결과 확인 필요</${Chip}>`;
    if (rem.state === '발송중') return html`<${Chip} tone="muted" title=${'Google Chat 알림 · ' + atLong(rem.sentAt)}>보내는 중</${Chip}>`;
    if (rem.state === '실패') return html`<${Chip} tone="warn" title=${String(rem.result || '') + (rem.sentAt ? ' · ' + atLong(rem.sentAt) : '')}>실패</${Chip}>`;
    return faint('—');
  }

  function valueCell(ctx, r, f) {
    const S = ctx.S, lab = f === 'g' ? '신고서' : '첨부';
    const pendKey = r.id + ':' + f, pending = S.pendingDoc[pendKey] !== undefined;
    const value = pending ? S.pendingDoc[pendKey] : r[f];
    if (r.deletedAt) return value ? html`<span class=${'att-vtx' + (value === '미제출' ? ' att-warn' : '')}>${value}</span>` : faint('—');
    if (E.docBad(r, f)) return html`<span class="att-why att-vtx att-warn" title=${rawOf(r, f === 'g' ? 'reportStatus' : 'attachmentStatus') || r[f]}>값 확인 필요</span>`;
    const why = ctx.rowWhy(r);
    const open = S.lb && S.lb.id === r.id && S.lb.f === f;
    const cls = 'att-vc' + (pending ? ' att-pending' : value === '미제출' ? ' att-warn' : '') + (value ? '' : ' att-blank');
    return html`<button type="button" class=${cls} role="combobox" aria-haspopup="listbox" aria-expanded=${open ? 'true' : 'false'}
      aria-label=${r.label + ' ' + (r.date ? md(r.date) : r.dateRaw) + ' ' + lab + ': ' + (value || '비움')} tabindex="-1" aria-disabled=${why ? 'true' : null}
      onClick=${ev => { if (why) { ctx.explain(why); return; } ctx.openLB(ev.currentTarget, r, f); }}
      onKeyDown=${ev => { if (ev.altKey && ev.key === 'ArrowDown' && !why) { ev.preventDefault(); ev.stopPropagation(); ctx.openLB(ev.currentTarget, r, f); } }}>
      <span>${value || '(비움)'}</span><span class="att-car" aria-hidden="true">▾</span></button>`;
  }

  /* ---------- the sheet's rows and cells ---------- */
  // Keys shared by the cells. Returns true when the key was taken.
  function cellKeys(ev, ctx, k, field) {
    if (ev.isComposing || ev.keyCode === 229) return false;
    const key = ev.key, el = ev.currentTarget, typed = el.tagName === 'INPUT' && el.type !== 'checkbox';
    const took = () => { ev.preventDefault(); ev.stopPropagation(); return true; };
    if (key === 'Enter' && !ev.ctrlKey && !ev.metaKey) {
      if (field === 'ck') return false;
      took();
      ctx.enterRow(k);
      return true;
    }
    if (key === 'ArrowDown' || key === 'ArrowUp') { took(); ctx.moveCell(el, key === 'ArrowDown' ? 1 : -1, 0, ev.shiftKey); return true; }
    if (key === 'ArrowRight' || key === 'ArrowLeft') {
      if (typed) {
        const a = el.selectionStart, b = el.selectionEnd, n = el.value.length;
        const edge = (a === 0 && b === n) || (key === 'ArrowRight' ? a === n && b === n : a === 0 && b === 0);
        if (!edge) return false;
      }
      took();
      ctx.moveCell(el, 0, key === 'ArrowRight' ? 1 : -1);
      return true;
    }
    if (key === 'Escape') {
      if (field === 'ck') return false;
      took();
      ctx.escapeCell(k, el);
      return true;
    }
    // The last cell of a row being entered: Tab finishes it, as Enter does.
    if (key === 'Tab' && !ev.shiftKey && field === 'h' && !ctx.D.rowById.has(k)) { took(); ctx.enterRow(k); return true; }
    return false;
  }
  // Keys while a list is open under a typed cell.
  function popKeys(ev, ctx) {
    const pop = ctx.S.pop;
    if (!pop || ev.isComposing || ev.keyCode === 229) return false;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); ev.stopPropagation(); ctx.movePop(ev.key === 'ArrowDown' ? 1 : -1); return true; }
    if (ev.key === 'Enter' && pop.act >= 0) { ev.preventDefault(); ev.stopPropagation(); ctx.pickPop(pop.items[pop.act], true); return true; }
    if (ev.key === 'Tab') { if (pop.act >= 0) ctx.pickPop(pop.items[pop.act], false); else ctx.closePop(); return false; }
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); ctx.closePop(); return true; }
    return false;
  }

  // The standard browser calendar is a separate small, focusable native control. Its stable node
  // is never replaced while open; typed and picked dates use the same draft/save path.
  function DatePicker(p) {
    const ctx = p.ctx, [active, setActive] = useState(false);
    const start = () => { setActive(true); ctx.calendarStart(); };
    const end = () => { setActive(false); ctx.calendarEnd(); };
    return html`<input type="date" class="att-date-picker" aria-label=${p.label || '날짜'} value=${isDateKey(p.date) ? p.date : ''}
      min=${ctx.D.cal.start} max=${ctx.D.cal.end} disabled=${p.why && !active ? true : null} tabindex=${p.tabindex === undefined ? null : p.tabindex}
      onPointerDown=${start} onFocus=${start} onBlur=${end}
      onChange=${ev => { if (!p.why) p.onPick(ev.target.value); end(); }}
      onKeyDown=${ev => { if (ev.key === 'Escape') { ev.stopPropagation(); end(); } }} />`;
  }
  // Forms share the sheet's free typing, strict date parsing and native calendar. Invalid text stays visible.
  function FormDate(p) {
    const ctx = p.ctx, [focused, setFocused] = useState(false);
    const text = String(p.value || ''), date = parseDateInput(text, p.ym || ctx.S.rec.ym, ctx.D.cal);
    const normalize = () => { if (date) p.onValue(date); };
    return html`<span class="att-date-field att-form-date"><input value=${text} aria-label=${p.label || '날짜'} placeholder="날짜" autocomplete="off"
      onInput=${ev => p.onValue(ev.target.value)} onFocus=${() => setFocused(true)} onBlur=${() => { setFocused(false); normalize(); }}
      class=${text && !date && !focused ? 'att-cell-warn' : ''} />
      <${DatePicker} ctx=${ctx} label=${p.label} date=${date} onPick=${p.onValue} /></span>`;
  }
  // Text only while focused: the cell shows `11/20(금)` and edits `11/20`. A day number means the row's month.
  function DateCell(p) {
    const ctx = p.ctx, v = p.v, r = p.r, k = p.k;
    const [focused, setFocused] = useState(false);
    const box = useRef(null);
    useLayoutEffect(() => { if (focused && box.current) box.current.select(); }, [focused]);
    const raw = r && !r.date ? r.dateRaw : '';
    const typed = v.dateText !== undefined;
    const shown = typed ? v.dateText : v.date ? (focused ? md(v.date) : mdw(v.date)) : raw;
    const warn = (typed || (r && !v.date)) && !focused;
    const cls = 'att-cell att-cell-date' + (warn ? ' att-cell-warn' : '') + (p.soft && !warn ? ' att-cell-soft' : '');
    return html`<span class="att-date-field"><input ref=${box} class=${cls} data-cell="date" value=${shown} placeholder="날짜" aria-label=${p.label + ' 날짜'} readOnly=${p.why ? true : null} autocomplete="off"
      title=${raw && !typed ? '출석부에 적힌 날짜' : null}
      onFocus=${() => setFocused(true)} onBlur=${() => { setFocused(false); ctx.commitDate(k); }}
      onClick=${() => { if (p.why) ctx.explain(p.why); }}
      onInput=${ev => { if (!p.why) ctx.editCell(k, { dateText: ev.target.value }); }}
      onKeyDown=${ev => cellKeys(ev, ctx, k, 'date')} />
      <${DatePicker} ctx=${ctx} label=${p.label + ' 날짜'} date=${v.date} why=${p.why}
        onPick=${date => ctx.editCell(k, { date, dateText: undefined })} /></span>`;
  }

  // Finds a student by number or name as it is typed.
  function StudentCell(p) {
    const ctx = p.ctx, D = ctx.D, v = p.v, r = p.r, k = p.k;
    const [focused, setFocused] = useState(false);
    const box = useRef(null);
    useLayoutEffect(() => { if (focused && box.current) box.current.select(); }, [focused]);
    const st = v.studentId ? D.byId.get(v.studentId) : null;
    const label = st ? st.label : r && v.studentId === r.studentId ? r.label : '';
    const left = st && st.status !== '재적' ? ' (' + st.status + ')' : '';
    const typed = v.studentText !== undefined;
    const shown = typed ? v.studentText : focused ? label : label + left;
    const lost = !st && !typed && !!r && !focused;
    const open = !!ctx.S.pop && ctx.S.pop.key === k && ctx.S.pop.field === 'student';
    const cls = 'att-cell att-cell-name' + ((typed && !focused) || lost ? ' att-cell-warn' : '');
    return html`<input ref=${box} class=${cls} data-cell="student" value=${shown} placeholder="번호+이름" aria-label=${p.label + ' 번호+이름'} readOnly=${p.why ? true : null} autocomplete="off"
      role="combobox" aria-autocomplete="list" aria-expanded=${open ? 'true' : 'false'} aria-controls=${open ? 'att-pop' : null}
      aria-activedescendant=${open && ctx.S.pop.act >= 0 ? 'att-pop-' + ctx.S.pop.act : null} title=${lost ? '명단에 없음' : shown}
      onFocus=${() => setFocused(true)} onBlur=${() => { setFocused(false); ctx.closePop(); ctx.commitStudent(k); }}
      onClick=${() => { if (p.why) ctx.explain(p.why); }}
      onInput=${ev => { if (p.why) return; const text = ev.target.value; ctx.editCell(k, { studentId: '', studentText: text }); ctx.openStudentPop(ev.target, k, text); }}
      onKeyDown=${ev => {
        if (popKeys(ev, ctx)) return;
        if (ev.altKey && ev.key === 'ArrowDown' && !p.why) { ev.preventDefault(); ev.stopPropagation(); ctx.openStudentPop(ev.currentTarget, k, ''); return; }
        cellKeys(ev, ctx, k, 'student');
      }} />`;
  }

  // Typed, with the year's frequent reasons under it.
  function ReasonCell(p) {
    const ctx = p.ctx, v = p.v, k = p.k, value = v.reason || '';
    const open = !!ctx.S.pop && ctx.S.pop.key === k && ctx.S.pop.field === 'reason';
    return html`<input class="att-cell att-cell-why" data-cell="reason" value=${value} maxlength="200" placeholder=${p.isNew ? '사유' : null} aria-label=${p.label + ' 사유'} readOnly=${p.why ? true : null} autocomplete="off"
      role="combobox" aria-autocomplete="list" aria-expanded=${open ? 'true' : 'false'} aria-controls=${open ? 'att-pop' : null}
      aria-activedescendant=${open && ctx.S.pop.act >= 0 ? 'att-pop-' + ctx.S.pop.act : null} title=${value || null}
      onFocus=${ev => { if (!p.why) ctx.openReasonPop(ev.currentTarget, k, ev.currentTarget.value, true); }} onBlur=${() => ctx.closePop()}
      onClick=${() => { if (p.why) ctx.explain(p.why); }}
      onInput=${ev => { if (p.why) return; ctx.editCell(k, { reason: ev.target.value }); ctx.openReasonPop(ev.target, k, ev.target.value, false); }}
      onKeyDown=${ev => { if (popKeys(ev, ctx)) return; cellKeys(ev, ctx, k, 'reason'); }} />`;
  }

  // 구분, 종류, 교시, 신고서, 첨부. Space or Alt+↓ opens the list; a value changes only on a pick.
  function ListCell(p) {
    const ctx = p.ctx, f = p.f, k = p.k, value = p.value, doc = f === 'g' || f === 'h';
    const lb = ctx.S.lb, open = !!lb && !!lb.sheet && lb.sheet.key === k && lb.sheet.field === f;
    const opt = sheetOptions(f).find(o => o[0] === value);
    const text = p.bad ? '값 확인 필요' : opt ? opt[1] : value || '—';
    const warn = p.bad || (doc && value === '미제출' && !p.isNew);
    const cls = (doc ? 'att-vc att-vc-cell' : 'att-cell att-lc') + (warn ? (doc ? ' att-warn' : ' att-cell-warn') : '') + (doc && !value && !p.bad ? ' att-blank' : '') + (p.soft && !warn ? ' att-cell-soft' : '');
    return html`<button type="button" class=${cls} data-cell=${f} role="combobox" aria-haspopup="listbox" aria-expanded=${open ? 'true' : 'false'} aria-label=${p.label + ' ' + p.name + ': ' + (p.bad ? text : value || '비움')}
      aria-disabled=${p.why ? 'true' : null} title=${p.bad ? p.rawText : null}
      onClick=${ev => { if (p.why) { ctx.explain(p.why); return; } ctx.openCellList(ev.currentTarget, k, f, value); }}
      onKeyDown=${ev => {
        if (!ev.isComposing && !p.why && (ev.key === ' ' || (ev.altKey && ev.key === 'ArrowDown'))) { ev.preventDefault(); ev.stopPropagation(); ctx.openCellList(ev.currentTarget, k, f, value); return; }
        cellKeys(ev, ctx, k, f);
      }}><span class="att-lc-tx">${text}</span><span class="att-car" aria-hidden="true">▾</span></button>`;
  }

  function SheetRow(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D, r = p.r, k = p.k, o = p.o;
    const v = rowVals(S.drafts[k], r, p.defaults);
    const isNew = !r;
    const entry = o.entry || null;
    const saving = r ? !!S.busy[k] : !!entry && entry.state === 'saving';
    const barLocked = !!r && ctx.lockedRow(r.id) && !S.locks['row:' + r.id];
    const why = saving ? { text: '저장하는 중이에요.', tone: 'muted' } : barLocked ? { text: T.LOST_WRITE } : null;
    const note = S.rowNote[k] || null;
    const cols = sheetCols(D, S.rec).length, showChat = D.hasReminders;
    const sel = !!r && S.sel.has(k), sortKey = S.rec.sort.key;
    const cls = ['att-r'];
    if (o.shade) cls.push('att-sh');
    if (o.runStart) cls.push('att-rs');
    if (r && E.needs(r)) cls.push('att-need');
    if (sel) cls.push('att-sel');
    if (isNew) cls.push('att-rnew');
    if (o.input) cls.push('att-rinput');
    if (S.flash[k]) cls.push('att-jw');
    const noteRow = note ? html`<tr class="att-rnote" key=${k + ':note'}><td colspan=${cols}><div class=${'att-rnote-in att-' + (note.tone || 'warn')} role="status"><span>${note.text}</span><${FixBtn} fix=${note.fix} /></div></td></tr>` : null;
    const attrs = { class: cls.join(' '), 'data-key': k, 'data-rid': r ? r.id : null, tabindex: -1 };
    if (r && D.readOnly) {
      // Plain text: an earlier year changes only through a later year.
      const plainDoc = value => (value ? html`<span class=${'att-vtx' + (value === '미제출' ? ' att-warn' : '')}>${value}</span>` : faint('—'));
      return html`<tr ...${attrs}>
        <td class="att-ck">${r.date ? html`<label class="att-ckl"><input type="checkbox" data-cell="ck" checked=${sel} tabindex="-1" aria-label=${r.label + ' ' + mdw(r.date) + ' 선택'} onClick=${ev => ctx.toggleRow(r.id, ev.target.checked, ev.shiftKey)} onKeyDown=${ev => cellKeys(ev, ctx, k, 'ck')} /></label>` : null}</td>
        <td><span class="att-plain att-dt">${r.date ? mdw(r.date) : r.dateRaw}</span></td><td><span class="att-plain att-nm" title=${r.label}>${r.label}</span></td>
        <td><span class="att-plain">${r.cat}</span></td><td><span class="att-plain">${r.kind}</span></td><td class="att-whyc"><span class="att-plain att-why" title=${r.reason}>${r.reason || '—'}</span></td><td><span class="att-plain">${r.period || '—'}</span></td>
        <td><span class="att-plain">${plainDoc(r.g)}</span></td><td><span class="att-plain">${plainDoc(r.h)}</span></td>${showChat ? html`<td>${remCell(ctx, r)}</td>` : null}<td>${sourceMark(r)}</td>
        <td class="att-acts"></td></tr>${noteRow}`;
    }
    const name = isNew ? (o.input ? '입력할 줄' : '저장 전 줄') : r.label + ' ' + (r.date ? md(r.date) : r.dateRaw);
    const softDate = !!r && !o.dateFull;
    const fk = key => !!r && sortKey === key && !o.keyFirst;
    const list = (f, nm, bad, rawText, soft) => html`<${ListCell} ctx=${ctx} k=${k} f=${f} name=${nm} label=${name} value=${v[f]} bad=${bad} rawText=${rawText} why=${why} isNew=${isNew} soft=${soft} />`;
    const touched = field => !!S.drafts[k] && S.drafts[k].f[field] !== undefined;
    return html`<tr ...${attrs}>
      <td class="att-ck">${r && r.date ? html`<label class="att-ckl"><input type="checkbox" data-cell="ck" checked=${sel} tabindex="-1" aria-label=${r.label + ' ' + mdw(r.date) + ' 선택'}
        onClick=${ev => ctx.toggleRow(r.id, ev.target.checked, ev.shiftKey)} onKeyDown=${ev => cellKeys(ev, ctx, k, 'ck')} /></label>` : null}</td>
      <td><${DateCell} ctx=${ctx} k=${k} r=${r} v=${v} label=${name} why=${why} soft=${softDate} /></td>
      <td class="att-nmc"><${StudentCell} ctx=${ctx} k=${k} r=${r} v=${v} label=${name} why=${why} />${r && !r.st && !touched('studentId') ? html`<div class="att-nmw"><${Chip} tone="warn">명단에 없음</${Chip}></div>` : null}</td>
      <td>${list('cat', '구분', !!r && !touched('cat') && E.catBad(r), r ? rawOf(r, 'category') || r.cat : '', fk('cat'))}</td>
      <td>${list('kind', '종류', !!r && !touched('kind') && E.kindBad(r), r ? rawOf(r, 'kind') || r.kind : '', fk('kind'))}</td>
      <td class="att-whyc"><${ReasonCell} ctx=${ctx} k=${k} v=${v} label=${name} why=${why} isNew=${isNew} /></td>
      <td>${list('period', '교시', !!r && !touched('period') && E.periodBad(r), r ? rawOf(r, 'period') || r.period : '')}</td>
      <td>${list('g', '신고서', !!r && !touched('g') && E.docBad(r, 'g'), r ? rawOf(r, 'reportStatus') || r.g : '')}</td>
      <td>${list('h', '첨부', !!r && !touched('h') && E.docBad(r, 'h'), r ? rawOf(r, 'attachmentStatus') || r.h : '')}</td>
      ${showChat ? html`<td>${r ? remCell(ctx, r) : null}</td>` : null}
      <td>${sourceMark(r)}</td>
      <td class="att-acts">${saving ? html`<span class="att-faint att-small">저장 중…</span>`
        : entry && entry.state === 'failed' ? html`<button type="button" class="att-link" tabindex="-1" onClick=${() => ctx.revertRow(k)}>취소</button>` : r ? html`<${ReminderButton} ctx=${ctx} row=${r} />` : null}</td></tr>${noteRow}`;
  }

  // 조치할 일's rows: roving focus, the body is one tab stop (SCREENS "Keyboard").
  function tableKey(ev, ctx) {
    if (ev.isComposing || ev.keyCode === 229) return;
    const tr = ev.target.closest && ev.target.closest('tr[data-rid]');
    if (!tr) return;
    const onRow = ev.target === tr, k = ev.key;
    const rows = Array.from(tr.parentElement.querySelectorAll('tr[data-rid]')), i = rows.indexOf(tr);
    const go = j => {
      const x = rows[Math.max(0, Math.min(rows.length - 1, j))];
      if (x) x.focus();
      return x;
    };
    if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Home' || k === 'End' || k === 'PageDown' || k === 'PageUp') {
      if (ev.target.tagName === 'INPUT' && ev.target.type !== 'checkbox') return;
      ev.preventDefault();
      ev.stopPropagation();
      const x = go(k === 'ArrowDown' ? i + 1 : k === 'ArrowUp' ? i - 1 : k === 'Home' ? 0 : k === 'End' ? rows.length - 1 : k === 'PageDown' ? i + 8 : i - 8);
      if (ev.shiftKey && x && x.querySelector('input[type="checkbox"]')) ctx.selectRange(x.dataset.rid);
      return;
    }
    const ctrls = Array.from(tr.querySelectorAll('input,button'));
    if (k === 'ArrowRight' || k === 'ArrowLeft') {
      ev.preventDefault();
      ev.stopPropagation();
      if (onRow) { if (k === 'ArrowRight' && ctrls[0]) ctrls[0].focus(); return; }
      const c = ctrls.indexOf(ev.target), n = c + (k === 'ArrowRight' ? 1 : -1);
      if (n < 0) tr.focus(); else if (ctrls[n]) ctrls[n].focus();
      return;
    }
    if (onRow && k === ' ') {
      ev.preventDefault();
      ev.stopPropagation();
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) cb.click();
      return;
    }
    if (onRow && k === 'Enter') {
      ev.preventDefault();
      ev.stopPropagation();
      const edit = tr.querySelector('[data-edit]');
      if (edit) edit.click();
      return;
    }
    if (k === 'Escape' && !onRow) {
      ev.preventDefault();
      ev.stopPropagation();
      tr.focus();
    }
  }

  function ValueList(p) {
    const ctx = p.ctx, lb = ctx.S.lb;
    const boxRef = useRef(null);
    const options = lb.options;
    useEffect(() => { if (boxRef.current) boxRef.current.focus(); }, []);
    const onKey = ev => {
      ev.stopPropagation();
      if (ev.isComposing || ev.keyCode === 229) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        ctx.set(prev => (prev.lb ? { lb: Object.assign({}, prev.lb, { act: (prev.lb.act + (ev.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length }) } : {}));
      } else if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        ctx.pickLB(options[lb.act][0]);
      } else if (ev.key === 'Escape' || ev.key === 'Tab') {
        ev.preventDefault();
        ctx.closeLB(true);
      }
    };
    return html`<div class="att-lb" role="listbox" tabindex="-1" ref=${boxRef} style=${'left:' + lb.left + 'px;top:' + lb.top + 'px'} aria-activedescendant=${'att-lb-' + lb.act} onKeyDown=${onKey}>
      ${options.map((o, i) => {
        const cur = lb.cur === o[0];
        return html`<div id=${'att-lb-' + i} role="option" aria-selected=${cur ? 'true' : 'false'} class=${'att-lb-opt' + (i === lb.act ? ' att-on' : '')} onClick=${() => ctx.pickLB(o[0])}>${o[1]}${cur ? html`<span class="att-chk">✓</span>` : null}</div>`;
      })}</div>`;
  }

  // The list under a typed cell (번호+이름, 사유). Focus stays in the cell, so typing goes on; a press never takes it away.
  function CellPop(p) {
    const ctx = p.ctx, pop = ctx.S.pop;
    const boxRef = useRef(null);
    useLayoutEffect(() => {
      const on = boxRef.current && boxRef.current.querySelector('.att-on');
      if (on) on.scrollIntoView({ block: 'nearest' });
    }, [pop.act]);
    return html`<div class="att-lb att-pop" id="att-pop" role="listbox" ref=${boxRef} style=${'left:' + pop.left + 'px;top:' + pop.top + 'px;min-width:' + pop.width + 'px'}
      onMouseDown=${ev => ev.preventDefault()}>
      ${pop.items.map((item, i) => html`<div key=${item.v} id=${'att-pop-' + i} role="option" aria-selected=${i === pop.act ? 'true' : 'false'} class=${'att-lb-opt' + (i === pop.act ? ' att-on' : '')}
        onClick=${() => ctx.pickPop(item, false)}>${item.label}</div>`)}</div>`;
  }

  /* ---------- 조치할 일: cards in the language of 종합 (D28) ---------- */
  function Line(p) {
    return html`<div class="att-al"><div class="att-t1">${p.children}</div>${p.btns ? html`<div class="att-ac">${p.btns}</div>` : null}</div>`;
  }
  function WorkCard(p) {
    return html`<section class=${'att-card att-wcard' + (p.wide ? ' att-wfull' : '')}>
      <div class="att-card-h att-wcard-h"><h3>${p.title}</h3><span class=${'att-sn att-'+(p.tone||'warn')}>${p.n}</span>${p.act ? html`<span class="att-spacer"></span>${p.act}` : null}</div>${p.children}</section>`;
  }
  // What a send with an unknown result was and to whom, as far as the data says. It stays here until decided (C7); P3 adds the decisions.
  function unknownWhat(it, D) {
    if (it.t === 'legacy') return '예전 출석부에서 보낸 메시지';
    if (it.kind === 'chat-class') return '학급 단톡방에 보낸 메시지';
    if (it.kind === 'chat-link') return (it.label ? it.label + '에게 ' : '') + '보낸 결석 신고 링크';
    const ref = String((Array.isArray(it.refs) && it.refs[0]) || '').split('/').slice(-2);
    if (ref[0] === 'greetings' && ref[1]?.startsWith('c_')) return '학급 단톡방에 보낸 메시지';
    if (ref[0] === 'attendance') {
      const r = D.rowById.get(ref[1]);
      if (r && r.label) return r.label + '에게 보낸 미제출 안내';
    }
    if (ref[0] === 'messages') {
      const m = (D.raw.messages || []).find(x => x.id === ref[1]);
      const st = m && m.studentId ? D.byId.get(m.studentId) : null;
      if (st) return st.label + '에게 보낸 메시지';
    }
    return it.label ? it.label + '에게 보낸 메시지' : '메시지 1건';
  }
  function unknownLine(it, D) {
    const tail = '학생이 받았는지 확인하지 못했어요';
    if (it.t === 'earlier') return it.year + '학년도 메시지 ' + it.n + '건 · ' + tail;
    return [unknownWhat(it, D), atLong(it.at), tail].filter(Boolean).join(' · ');
  }

  function WorkTab(p) {
    const ctx = p.ctx, D = ctx.D, W = ctx.W;
    if (!D || !W) {
      return html`<div class="att-wgrid">${[65, 50, 72].map(w => html`<div class="att-card"><span class="att-phb" style=${'width:' + w + '%'}></span></div>`)}</div>`;
    }
    const nothing = W.count === 0 && !W.partial;
    // A card alone on its row takes the whole row, so no half of the window stays empty.
    const alone = (W.s1.length ? 1 : 0) + (W.s5.length ? 1 : 0) === 1;
    const quiet = (label, run) => html`<button type="button" class="att-btn-quiet att-btn-sm" onClick=${run}>${label}</button>`;
    const cu = W.catchUp;
    return html`
      <${NoticeSlot} ctx=${ctx} />
      ${W.partial ? html`<div class="att-notice att-muted att-nrow att-gap" role="status"><span class="att-notice-tx">${T.PARTIAL}</span></div>` : null}
      ${cu && (cu.read || cu.running) ? html`<div class="att-notice att-muted att-nrow att-gap" role="status"><span class="att-notice-tx">${cu.running ? '학생 쪽지를 확인하는 중이에요.' : '학생 쪽지 ' + cu.read + '건 확인 · 출결 ' + (cu.rows || 0) + '줄 입력'}${!cu.running && cu.review ? html` · <span class="att-warn-tx">${'확인 필요 ' + cu.review + '건'}</span>` : null}</span></div>` : null}
      ${nothing ? html`<p class="att-empty-msg">지금 조치할 일이 없어요.</p>` : html`<div class="att-wgrid">
        ${W.s10.length ? html`<${ReportCard} ctx=${ctx} items=${W.s10} />` : null}
        ${W.s1.length ? html`<${WorkCard} title="전달됐는지 모르는 메시지" n=${W.s1.length} wide=${alone}>
          ${W.s1.map(it => html`<${Line} key=${it.k} btns=${it.id ? quiet('확인하기', () => ctx.openDialog({ kind: 'chat-decide', effectId: it.id })) : it.checkId ? quiet('확인했어요', () => legacyResolve(ctx, it)) : null}>${unknownLine(it, D)}</${Line}>`)}</${WorkCard}>` : null}
        ${W.due.length ? html`<${WorkCard} title="오늘 보낼 메시지" n=${W.due.length}><${ChatHealth} ctx=${ctx} /><${Line} btns=${quiet('개인톡+단체톡 한 번에 보내기', () => ctx.openDialog({ kind: 'chat-send', mode: 'queue' }))}>${'오늘 보낼 메시지 ' + W.due.length}</${Line}></${WorkCard}>` : null}
        ${W.s5.length ? html`<${WorkCard} title="학생명단 확인" n=${W.s5.length} wide=${alone}>
          ${W.s5.map(it => (it.t === 'noroster'
            ? html`<${Line} key=${it.k}>${'학생명단에 없는 이름이 적혀있습니다. (' + it.n + '줄)'}</${Line}>`
            : it.t==='message-student'||it.t==='message-value' ? html`<${Line} key=${it.k} btns=${quiet('메시지 보기',()=>ctx.setTab('msg'))}>${(it.t==='message-student'?'학생명단에 없는 이름이 적힌 개인톡 ':'보낼 메시지 값 확인 필요 ')+it.n+'건'}</${Line}>`
            : html`<${Line} key=${it.k} btns=${quiet('학생명단 고치기', () => ctx.link({ kind: 'roster', studentId: it.list[0].id, label: it.list[0].label, focusEmail: true }))}>${'Google 이메일 없음 - ' + it.list.map(s => s.n + '번 ' + s.name).join(', ')}</${Line}>`))}</${WorkCard}>` : null}
        ${W.s3.length ? html`<${WorkCard} title="발송 결과 확인 필요" n=${W.s3.length}>${W.s3.map(it=>{
          return html`<${Line} btns=${html`${it.effectId?quiet('다시 보내기…',()=>ctx.openDialog({kind:'chat-send',mode:it.mode,resendOf:it.effectId,...(it.mode==='reminders'?{ids:[it.id]}:{})})):null}${quiet('학생명단 고치기',()=>ctx.link({kind:'roster',focusEmail:true}))}${it.mode==='queue'?quiet('메시지 보기',()=>ctx.setTab('msg')):null}`}>${it.label+' · 실패'}</${Line}>`;
        })}</${WorkCard}>`:null}
        ${W.s9.length ? html`<${WorkCard} title="확인이 필요한 학생 쪽지" n=${W.s9.length}>${W.s9.map(it => html`<${Line} key=${it.k} btns=${html`${quiet('출결 직접 입력', () => intakeEnter(ctx, it))}${quiet('처리함', () => intakeResolve(ctx, it))}`}>${it.label + ' · ' + (it.date ? md(it.date) : '') + ' · “' + it.text + '”' + (INTAKE_WHY[it.code] ? ' · ' + INTAKE_WHY[it.code] : '')}</${Line}>`)}</${WorkCard}>` : null}
        ${W.s7.length ? html`<${WorkCard} title="진행 중인 일" n=${W.s7.length} tone="muted">${W.s7.map(it=>html`<${Line}>${it.label+' · 보내는 중'}</${Line}>`)}</${WorkCard}>`:null}
        <${MissingSection} ctx=${ctx} />
      </div>`}`;
  }

  // AL10 학생 결석 신고 (2026-11-11 design §6.3, §13.1): amber, it needs the teacher. No 반려 (Q-L8).
  function ReportCard(p) {
    const ctx = p.ctx, D = ctx.D, S = ctx.S, items = p.items;
    const quiet = (label, run, why) => html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${why || ctx.roWhy()} onClick=${run}>${label}</${Btn}>`;
    return html`<${WorkCard} title="학생 결석 신고" n=${items.length} wide=${true}
      act=${html`<button type="button" class="att-btn-quiet att-btn-sm" onClick=${() => ctx.link({ kind: 'attendance-connect' })}>링크 보내기…</button>`}>
      ${items.map(rep => {
        const key = 'report:' + rep.id, held = !!S.locks[key], busy = !!S.busy[key], dup = S.reportDup && S.reportDup[rep.id];
        const link = D.links.get(rep.studentId), old = rep.olderLink || (!!link && Number(link.generation) > rep.generation && rep.generation > 0);
        const chips = [];
        if (!rep.cat) chips.push(html`<${Chip} tone="warn">구분 선택 필요</${Chip}>`);
        if (dup) chips.push(html`<${Chip} tone="warn">${'같은 줄이 이미 있어요' + (dup.date ? ' · ' + md(dup.date) : '')}</${Chip}>`);
        if (held) chips.push(html`<${Chip} tone="muted">기록 결과 확인 중</${Chip}>`);
        if (old) chips.push(html`<${Chip} tone="muted">이전 주소로 보냄</${Chip}>`);
        const wait = held ? { text: T.LOST_WRITE } : busy ? { text: '기록하는 중이에요.', tone: 'muted' } : null;
        const btns = html`${dup && dup.recordId ? quiet('그 줄 보기', () => ctx.gotoRow(dup.recordId), wait) : quiet(busy ? '기록하는 중…' : '기록하기', () => ctx.recordReport(rep), wait)}
          ${quiet('수정해서 기록', () => ctx.openDialog({ kind: 'report-edit', rep }), wait)}
          ${old && rep.st ? quiet('링크 새로 만들기…', () => ctx.openDialog({ kind: 'chat-send', mode: 'links', studentIds: [rep.studentId], rotate: true, label: rep.label }), wait) : null}`;
        return html`<${Line} key=${rep.id} btns=${btns}><span>${reportText(rep, D.today)}</span>${chips.length ? html`<span class="att-chips">${chips}</span>` : null}</${Line}>`;
      })}</${WorkCard}>`;
  }

  const INTAKE_WHY = { OTHER_YEAR: '지난 학년도 날짜', AMBIGUOUS_EDITED_OR_CORRECTED: '날짜·취소·대신 전한 말 등이 있어 확인이 필요해요', CORRECTION_REQUIRES_REVIEW: '취소나 정정 쪽지',
    ATTENDANCE_DUPLICATE: '그날 이미 출결 줄이 있어요', MESSAGE_CHANGED: '쪽지가 바뀌었거나 지워졌어요', ROSTER_CHANGED: '학생명단이 바뀌었어요', GEMINI_FAILED: 'AI가 답하지 않았어요' };
  // `출결 직접 입력`: the new-row line of that month, with the student and the date set.
  function intakeEnter(ctx, it) {
    ctx.setTab('rec');
    if (it.date) ctx.chooseMonth(it.date.slice(0, 7));
    ctx.editCell('new', { date: it.date || '', studentId: it.studentId || '', intakeItemId: it.id });
    ctx.toast('나머지 칸을 채워 저장하면 이 쪽지도 처리돼요.');
  }
  // MAIN §8: an old send of the workbook whose result nobody knows. This only records that the teacher looked.
  async function legacyResolve(ctx, it) {
    const out = await ctx.call('/v1/data/import-checks/resolve', { checkId: it.checkId });
    if (out.status === 200) { ctx.toast('예전 발송을 확인했어요.'); ctx.read(); }
    else ctx.toast(out.unknown ? '응답을 받지 못했어요. 잠시 뒤 다시 눌러 주세요.' : ctx.whyOf({ kind: 'refused', status: out.status, code: out.body && out.body.error }).text);
  }
  async function intakeResolve(ctx, it) {
    const out = await ctx.call('/v1/data/intake/resolve', { itemId: it.id, action: 'dismissed' });
    if (out.status === 200) { ctx.toast('처리했어요.'); ctx.read(); }
    else ctx.toast(out.unknown ? '응답을 받지 못했어요. 잠시 뒤 다시 눌러 주세요.' : ctx.whyOf({ kind: 'refused', status: out.status, code: out.body && out.body.error }).text);
  }

  function remSummary(ctx, rows) {
    const sent = rows.filter(r => r.reminder && r.reminder.state === '보냄'), out = [];
    if (rows.some(r => r.remUnknown)) out.push(html`<${Chip} tone="warn">결과 확인 필요</${Chip}>`);
    if (sent.length) out.push(html`<${Chip} tone=${ctx.S.net === 'ok' ? 'ok' : 'muted'}>${'독려 보냄 · ' + atShort(sent[0].reminder.sentAt, ctx.D.today)}</${Chip}>`);
    if (rows.some(r => r.reminder && r.reminder.state === '실패')) out.push(html`<${Chip} tone="warn">실패</${Chip}>`);
    if (rows.some(r => r.reminder && r.reminder.state === '발송중' && !r.remUnknown)) out.push(html`<${Chip} tone="muted">보내는 중</${Chip}>`);
    return out;
  }

  // One card per student, oldest missing date first; the heading spans the grid.
  function MissingSection(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D, W = ctx.W;
    const groups = ctx.s4Groups(W, D);
    if (!W.s4.length && !groups.length) return null;
    const shown = S.s4all ? groups : groups.slice(0, 8);
    return html`
      <div class="att-whead" id="att-sec-s4"><h3>신고서·첨부 미제출</h3>
        ${W.s4.length ? html`<span class="att-meta">${'학생 ' + W.s4.length + '명 · ' + W.missRows + '줄'}</span>` : null}
      </div>
      ${shown.map(g => {
        const miss = g.rows.filter(E.missing), all = !!S.s4rows[g.id], rows = all ? g.rows : g.rows.slice(0, 4);
        return html`<div class="att-card att-scard" key=${g.id}>
          <div class="att-scard-h"><b class="att-nm">${g.label}</b>
            ${miss.length ? html`<span class="att-subtle">${'미제출 ' + miss.length + '줄'}</span>${remSummary(ctx, miss)}` : html`<${Chip} tone=${S.net === 'ok' ? 'ok' : 'muted'}>처리함</${Chip}>`}</div>
          <${MiniTable} ctx=${ctx} rows=${rows} />
          <div class="att-more-in"><${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ctx.roWhy()} onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'reminders', ids: miss.map(r => r.id) })}>독려 보내기…</${Btn}></div>
          ${g.rows.length > rows.length ? html`<div class="att-more-in"><button type="button" class="att-link" onClick=${() => ctx.set(prev => ({ s4rows: Object.assign({}, prev.s4rows, { [g.id]: true }) }))}>${'나머지 ' + (g.rows.length - rows.length) + '줄 더 보기'}</button></div>` : null}
        </div>`;
      })}
      ${groups.length > shown.length ? html`<div class="att-wfull att-sec-more"><button type="button" class="att-btn-quiet att-btn-sm" onClick=${() => ctx.set({ s4all: true })}>${'나머지 ' + (groups.length - shown.length) + '명 더 보기'}</button></div>` : null}`;
  }

  // One line per row at every width: 사유 truncates, and below the columns' sum the table scrolls sideways. 수정 goes to the row in 출결 기록.
  function MiniTable(p) {
    const ctx = p.ctx, S = ctx.S, rows = p.rows;
    let shade = false, prev = null;
    const firstId = rows.length ? rows[0].id : null;
    const bad = raw => html`<span class="att-why att-vtx att-warn" title=${raw}>값 확인 필요</span>`;
    return html`<div class="att-mini"><table class="att-rt" aria-label="출결 기록">
      <colgroup><col style="width:62px" /><col style="width:54px" /><col style="width:44px" /><col /><col style="width:36px" /><col style="width:60px" /><col style="width:60px" /><col style="width:20px" /><col style="width:32px" /></colgroup>
      <thead><tr><th>날짜</th><th>구분</th><th>종류</th><th>사유</th><th>교시</th><th>신고서</th><th>첨부</th><th>AI</th><th><span class="att-sr">수정</span></th></tr></thead>
      <tbody onKeyDown=${ev => tableKey(ev, ctx)}>${rows.map((r, i) => {
        const start = r.date !== prev;
        if (start && i > 0) shade = !shade;
        prev = r.date;
        const cls = ['att-r'];
        if (shade) cls.push('att-sh');
        if (start && i > 0) cls.push('att-rs');
        if (E.needs(r)) cls.push('att-need');
        if (S.flash[r.id]) cls.push('att-jw');
        return html`<tr key=${r.id} class=${cls.join(' ')} data-rid=${r.id} tabindex=${r.id === firstId ? 0 : -1}>
          <td><span class="att-dt">${mdw(r.date)}</span></td>
          <td>${E.catBad(r) ? bad(rawOf(r, 'category') || r.cat) : r.cat}</td>
          <td>${E.kindBad(r) ? bad(rawOf(r, 'kind') || r.kind) : r.kind}</td>
          <td class="att-whyc"><span class="att-why" title=${r.reason}>${r.reason || '—'}</span></td>
          <td>${E.periodBad(r) ? bad(rawOf(r, 'period') || r.period) : r.period || '—'}</td>
          <td>${valueCell(ctx, r, 'g')}</td><td>${valueCell(ctx, r, 'h')}</td>
          <td>${sourceMark(r)}</td>
          <td><button type="button" class="att-ic-btn" tabindex="-1" data-edit="1" onClick=${() => ctx.gotoRow(r.id)}>수정</button></td></tr>`;
      })}</tbody></table></div>`;
  }

  /* ---------- 종합 ---------- */
  function SummaryTab(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    if (!D) {
      return html`<div class="att-kgrid">${[1, 2, 3].map(() => html`<div class="att-kpi"><div class="att-kpi-top"><${Chip} tone="muted">확인 중…</${Chip}></div><div class="att-kpi-v">—<small>건</small></div></div>`)}</div>`;
    }
    const v = S.view, P = periodNow(D, v), rows = summaryRows(D, v, P);
    const prevR = prevRange(P, D.today);
    const prev = prevR ? summaryRows(D, v, prevR) : null;
    const miss = D.rows.filter(r => E.missing(r) && E.inRoster(r));
    const mi = rows.filter(r => r.cat === '미인정');
    const delta = (cur, before) => {
      const d = cur - before, head = d > 0 ? '▲ ' + d + '건 많음' : d < 0 ? '▼ ' + (-d) + '건 적음' : '변화 없음';
      return prevR.month ? head + ' · 한 달 전 같은 기간(' + md(prevR.s) + '~' + md(prevR.e) + ') ' + before + '건' : head;
    };
    const unread = html`<${Chip} tone="muted">${T.NOT_READ}</${Chip}>`;
    const okChip = text => (S.net === 'ok' ? html`<${Chip} tone="ok">${text}</${Chip}>` : html`<${Chip} tone="muted">${atLong(S.readAt) + ' 기준'}</${Chip}>`);
    return html`
      <div class="att-pbar" style="margin-top:0"><${PeriodSeg} ctx=${ctx} /></div>
      <div class="att-fbar">${studentFilter(ctx)}${catFilter(ctx)}${kindFilter(ctx)}<span class="att-spacer"></span>${searchBox(ctx)}</div>
      <${NoticeSlot} ctx=${ctx} />
      <div class="att-kgrid">
        <div class="att-kg att-kg-a">지금 해야 할 일<span>전체 기간</span></div>
        <div class="att-kg att-kg-b">선택한 기간<span>${P.short}</span></div>
        <div class=${'att-kpi' + (miss.length && D.complete ? ' att-kpi-warn' : '')}>
          <div class="att-kpi-top"><span class="att-kpi-l">신고서·첨부 미제출</span>${!D.complete ? unread : miss.length ? html`<${Chip} tone="warn">조치 필요</${Chip}>` : okChip('모두 제출')}</div>
          <div class="att-kpi-v">${D.complete ? miss.length : '—'}<small>건</small></div>
          <div class="att-kpi-s">${D.complete ? '신고서 ' + miss.filter(r => r.g === '미제출').length + ' · 첨부 ' + miss.filter(r => r.h === '미제출').length : ''}</div>
          <div class="att-kpi-a"><button type="button" class="att-btn-tonal att-btn-sm" onClick=${() => { ctx.setTab('work'); setTimeout(() => { const el = document.getElementById('att-sec-s4'); if (el) el.scrollIntoView({ block: 'start' }); }, 30); }}>미제출 보기</button></div>
        </div>
        <div class="att-kpi">
          <div class="att-kpi-top"><span class="att-kpi-l">출결 변동</span></div>
          <div class="att-kpi-v">${rows.length}<small>건</small></div>
          <div class="att-kpi-s">${KINDS.map(k => kindWord(k) + ' ' + rows.filter(r => r.kind === k).length).join(' · ')}</div>
          <div class="att-kpi-d">${prev ? delta(rows.length, prev.length) : '비교 기간 없음'}</div>
        </div>
        <div class="att-kpi">
          <div class="att-kpi-top"><span class="att-kpi-l">미인정 출결</span></div>
          <div class="att-kpi-v">${mi.length}<small>건</small></div>
          <div class="att-kpi-s">${'학생 ' + new Set(mi.map(r => r.studentId || r.label)).size + '명'}</div>
          <div class="att-kpi-d">${prev ? delta(mi.length, prev.filter(r => r.cat === '미인정').length) : '비교 기간 없음'}</div>
        </div>
      </div>
      <div class="att-charts">
        <${TrendCard} ctx=${ctx} P=${P} rows=${rows} />
        <${DonutCard} rows=${rows} />
        <${ReasonsCard} rows=${rows} />
        <${MeterCard} ctx=${ctx} P=${P} rows=${rows} />
        <${MiStudentsCard} ctx=${ctx} rows=${rows} />
      </div>`;
  }
  function prevRange(P, today) {
    if (P.kind === 'month') {
      const end = P.e > today ? today : P.e;
      if (end < P.s) return null;
      const s = addMonthsClamp(P.s, -1);
      return { s, e: addDays(s, span(P.s, end)), month: true };
    }
    if (P.kind === 'week') return { s: addDays(P.s, -7), e: addDays(P.e, -7), month: false };
    return null;
  }
  function TrendCard(p) {
    const ctx = p.ctx, D = ctx.D, v = ctx.S.view, P = p.P, rows = p.rows;
    const start = P.s < D.cal.start ? D.cal.start : P.s, end = P.e > D.cal.end ? D.cal.end : P.e;
    const valid = isDateKey(start) && isDateKey(end) && start <= end;
    const bks = valid ? buckets(start, end, D.cal.inYear ? D.today : '') : [];
    const s1 = bks.map(b => rows.filter(r => r.date >= b.s && r.date <= b.e && r.kind === '결석함').length);
    const s2 = bks.map(b => rows.filter(r => r.date >= b.s && r.date <= b.e && r.kind && r.kind !== '결석함').length);
    const monthly = valid && span(start, end) > 70;
    return html`<div class="att-card att-chart">
      <div class="att-card-h"><h3>${monthly ? '월별 출결 변동' : '주별 출결 변동'}</h3>
        <div class="att-legend"><span><i class="att-lk att-lk-solid"></i>결석</span><span><i class="att-lk att-lk-hollow"></i>지각·조퇴·결과</span></div>
        <span class="att-spacer"></span>
        ${bks.length ? html`<button type="button" class="att-tbtn" onClick=${() => ctx.setIn('view', { trendTable: !v.trendTable })}>${v.trendTable ? '그래프로 보기' : '표로 보기'}</button>` : null}</div>
      ${bks.length ? (v.trendTable
        ? html`<table class="att-rt"><thead><tr><th>기간</th><th>결석</th><th>지각·조퇴·결과</th></tr></thead><tbody>${bks.map((b, i) => html`<tr><td>${b.label}</td><td>${s1[i]}</td><td>${s2[i]}</td></tr>`)}</tbody></table>`
        : html`<${LineChart} bks=${bks} s1=${s1} s2=${s2} />`) : html`<div class="att-faint att-small">표시할 기간이 없어요.</div>`}
    </div>`;
  }
  function LineChart(p) {
    const bks = p.bks, s1 = p.s1, s2 = p.s2;
    const W = 640, H = 220, m = { l: 30, r: 20, t: 14, b: 30 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const mx = Math.max(2, ...s1, ...s2), step = mx <= 4 ? 1 : mx <= 10 ? 2 : 5, top = Math.ceil(mx / step) * step;
    const x = i => m.l + (bks.length === 1 ? iw / 2 : iw * i / (bks.length - 1));
    const y = val => m.t + ih - ih * val / top;
    const grid = [];
    for (let val = 0; val <= top; val += step) grid.push(val);
    const every = Math.max(1, Math.ceil(bks.length / Math.max(1, Math.floor(iw / 78))));
    const path = s => s.map((val, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(val).toFixed(1)).join(' ');
    const colW = bks.length > 1 ? iw / (bks.length - 1) : iw;
    const label = '출결 변동 추이: ' + bks.map((b, i) => b.label + ' 결석 ' + s1[i] + '건, 지각·조퇴·결과 ' + s2[i] + '건').join('; ');
    return html`<svg viewBox=${'0 0 ' + W + ' ' + H} role="img" aria-label=${label}>
      ${grid.map(val => html`<line x1=${m.l} x2=${m.l + iw} y1=${y(val)} y2=${y(val)} stroke=${val ? '#E5E8EB' : '#D1D6DB'} stroke-width="1"></line>`)}
      ${grid.map(val => html`<text x=${m.l - 8} y=${y(val) + 4} text-anchor="end" font-size="11" fill="#8B95A1">${val}</text>`)}
      ${bks.map((b, i) => ((i % every === 0 || i === bks.length - 1) ? html`<text x=${x(i)} y=${H - 8} text-anchor="middle" font-size="11" fill="#8B95A1">${b.label}</text>` : null))}
      <path d=${path(s2)} fill="none" stroke="#8B95A1" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
      <path d=${path(s1)} fill="none" stroke="#191F28" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
      ${s2.map((val, i) => html`<circle cx=${x(i)} cy=${y(val)} r="4" fill="#fff" stroke="#8B95A1" stroke-width="2"></circle>`)}
      ${s1.map((val, i) => html`<circle cx=${x(i)} cy=${y(val)} r="5" fill="#191F28" stroke="#fff" stroke-width="2"></circle>`)}
      ${bks.map((b, i) => html`<rect x=${x(i) - colW / 2} y=${m.t} width=${colW} height=${ih} fill="transparent"><title>${md(b.s) + ' ~ ' + md(b.e) + '\n결석 ' + s1[i] + '건\n지각·조퇴·결과 ' + s2[i] + '건'}</title></rect>`)}
    </svg>`;
  }
  function DonutCard(p) {
    const rows = p.rows, cc = CATS.map(c => rows.filter(r => r.cat === c).length), tot = cc.reduce((a, b) => a + b, 0);
    const size = 150, R = 70, r = 48, c = size / 2;
    const gap = tot && cc.filter(Boolean).length > 1 ? 0.035 : 0;
    const pt = (rad, ang) => [c + rad * Math.cos(ang), c + rad * Math.sin(ang)];
    let a0 = -Math.PI / 2;
    const parts = [];
    cc.forEach((val, i) => {
      if (!val) return;
      const tip = CATS[i] + '\n' + val + '건 · ' + Math.round(val / tot * 100) + '%';
      const a1 = a0 + (val / tot) * Math.PI * 2, s = a0 + gap / 2, e = a1 - gap / 2, large = e - s > Math.PI ? 1 : 0;
      if (val === tot) {
        parts.push(html`<circle cx=${c} cy=${c} r=${(R + r) / 2} fill="none" stroke=${CAT_COLOR[CATS[i]]} stroke-width=${R - r}><title>${tip}</title></circle>`);
      } else {
        const p1 = pt(R, s), p2 = pt(R, e), p3 = pt(r, e), p4 = pt(r, s);
        parts.push(html`<path d=${'M' + p1[0] + ' ' + p1[1] + ' A' + R + ' ' + R + ' 0 ' + large + ' 1 ' + p2[0] + ' ' + p2[1] + ' L' + p3[0] + ' ' + p3[1] + ' A' + r + ' ' + r + ' 0 ' + large + ' 0 ' + p4[0] + ' ' + p4[1] + 'Z'} fill=${CAT_COLOR[CATS[i]]}><title>${tip}</title></path>`);
      }
      a0 = a1;
    });
    return html`<div class="att-card"><div class="att-card-h"><h3>출결 구분 비율</h3></div>
      <div class="att-donut"><div style="width:120px;flex:none">
        <svg viewBox="0 0 150 150" role="img" aria-label=${'구분 비율: ' + CATS.map((k, i) => k + ' ' + cc[i] + '건').join(', ')}>
          ${tot ? parts : html`<circle cx=${c} cy=${c} r=${(R + r) / 2} fill="none" stroke="#F2F4F6" stroke-width=${R - r}></circle>`}
          <text x=${c} y=${c - 2} text-anchor="middle" font-size="22" font-weight="700" fill="#191F28">${tot}</text>
          <text x=${c} y=${c + 16} text-anchor="middle" font-size="11.5" fill="#6B7684">건</text></svg></div>
        <div class="att-dleg">${CATS.map((k, i) => html`<div><span class="att-sw" style=${'background:' + CAT_COLOR[k]}></span>${k}<span class="att-v">${cc[i] + '건'}</span></div>`)}</div>
      </div></div>`;
  }
  function ReasonsCard(p) {
    const rows = p.rows, count = {};
    rows.forEach(r => { const k = r.reason.trim(); if (k) count[k] = (count[k] || 0) + 1; });
    const top = Object.keys(count).map(k => [k, count[k]]).sort((a, b) => b[1] - a[1] || (a[0] > b[0] ? 1 : -1)).slice(0, 5);
    const mx = Math.max(1, ...top.map(t => t[1]));
    return html`<div class="att-card"><div class="att-card-h"><h3>많이 나온 사유 5가지</h3></div>
      <div class="att-bars">${top.length ? top.map(t => html`<div class="att-bar" title=${t[0] + '\n' + t[1] + '건 · 전체의 ' + Math.round(t[1] / rows.length * 100) + '%'}>
        <span class="att-bar-nm">${t[0]}</span><span class="att-bar-tr"><span class="att-bar-fl" style=${'width:calc((100% - 44px) * ' + (t[1] / mx) + ')'}></span><span class="att-bar-vl">${t[1] + '건'}</span></span></div>`)
        : html`<div class="att-faint att-small">조건에 맞는 출결이 없어요.</div>`}</div></div>`;
  }
  function MeterCard(p) {
    const D = p.ctx.D, P = p.P, rows = p.rows;
    const need = rows.filter(r => !(r.g === '해당없음' && r.h === '해당없음'));
    const done = need.filter(r => !E.missing(r) && r.g && r.h).length;
    const pct = need.length ? Math.round(done / need.length * 100) : 0;
    return html`<div class="att-card"><div class="att-card-h"><h3>서류·메시지 처리 진행</h3></div>
      <div class="att-meter">
        <div class="att-meter-top"><b>서류 제출</b>${D.complete ? html`<span>${done + '/' + need.length + '줄'}</span>` : html`<${Chip} tone="muted">${T.NOT_READ}</${Chip}>`}</div>
        <div class="att-meter-track" role="progressbar" aria-valuenow=${done} aria-valuemax=${need.length} aria-label="서류 제출"><div class="att-meter-fill" style=${'width:' + pct + '%'}></div></div>
        <div class="att-meter-foot">${P.short + ' 기준'}</div>
      </div></div>`;
  }

  // Students with at least one 미인정 in the chosen period, most first (D28: the count moved here from 조치할 일).
  function MiStudentsCard(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D, by = new Map();
    p.rows.filter(r => r.cat === '미인정').forEach(r => {
      const key = r.studentId || r.label, e = by.get(key) || { key, label: r.label, n: r.n, count: 0 };
      e.count += 1;
      by.set(key, e);
    });
    const list = Array.from(by.values()).sort((a, b) => b.count - a.count || a.n - b.n);
    const shown = S.miAll ? list : list.slice(0, MI_LIST_LIMIT);
    return html`<div class="att-card att-micard"><div class="att-card-h"><h3>미인정 많은 학생</h3>${D.complete ? null : html`<${Chip} tone="muted">${T.NOT_READ}</${Chip}>`}</div>
      ${shown.length ? html`<div class="att-milist">${shown.map(e => html`<div key=${e.key}><span class="att-nm">${e.label}</span><span class="att-v">${e.count + '회'}</span></div>`)}</div>`
        : D.complete ? html`<div class="att-faint att-small">조건에 맞는 출결이 없어요.</div>` : null}
      ${list.length > shown.length ? html`<div class="att-more-in"><button type="button" class="att-link" onClick=${() => ctx.set({ miAll: true })}>${'나머지 ' + (list.length - shown.length) + '명 더 보기'}</button></div>` : null}</div>`;
  }

  /* ---------- 휴일·학사일정 ---------- */
  function HolidaysTab(p) {
    const ctx = p.ctx, S = ctx.S, D = ctx.D;
    if (!D) return html`<p class="att-faint">읽는 중…</p>`;
    const hs = S.holiday, ro = ctx.roWhy();
    const years = holidayYears(D);
    const year = hs.year && years.indexOf(Number(hs.year)) >= 0 ? Number(hs.year) : (D.cal.inYear ? Number(D.today.slice(0, 4)) : D.cal.sy);
    const q = hs.q.trim();
    const list = D.holidays.filter(x => x.d.slice(0, 4) === String(year) && (!hs.kind || x.type === hs.kind) && (!q || (x.name + x.d + md(x.d)).indexOf(q) >= 0));
    const need = holidaysMissing(D);
    return html`
      <${NoticeSlot} ctx=${ctx} />
      <div class="att-pbar" style="margin-top:0">
        <span class="att-seg" role="group" aria-label="연도">${years.map(y => html`<button type="button" key=${y} class=${year === y ? 'att-on' : ''} aria-pressed=${year === y ? 'true' : 'false'} onClick=${() => ctx.setIn('holiday', { year: y })}>${y + '년'}</button>`)}</span>
        <${FilterSelect} label="구분" value=${hs.kind} onChange=${val => ctx.setIn('holiday', { kind: val })} options=${[['', '전체']].concat(HOL_KINDS.map(k => [k, k]))} />
        <input class="att-search" type="search" placeholder="명칭·날짜 검색" aria-label="명칭·날짜 검색" value=${hs.q} onInput=${ev => ctx.setIn('holiday', { q: ev.target.value })} />
        <span class="att-spacer"></span>
        <${Btn} ctx=${ctx} cls="att-btn-tonal att-btn-sm" why=${ro} onClick=${() => ctx.openHolidayAdd(year)}>휴일 추가</${Btn}>
      </div>
      ${need.map(y => html`<div class="att-notice att-warn att-nrow att-gap" key=${y}><span class="att-notice-tx">${y + '년 휴일이 없어 ' + y + '년 날짜의 AI 출결 입력이 멈춰요.'}</span><${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ro} onClick=${() => ctx.openHolidayAdd(y)}>${y + '년 휴일 추가'}</${Btn}></div>`)}
      <div class="att-tblock"><table class="att-rt" aria-label="휴일·학사일정">
        <colgroup><col style="width:96px" /><col /><col style="width:100px" /><col style="width:90px" /><col style="width:110px" /><col style="width:80px" /><col style="width:130px" /></colgroup>
        <thead><tr><th>날짜(요일)</th><th>명칭</th><th>구분</th><th>상태</th><th>비고</th><th>출처</th><th><span class="att-sr">작업</span></th></tr></thead>
        <tbody>${list.length ? list.map(x => html`<tr key=${x.d}><td><span class="att-dt">${mdw(x.d)}</span></td><td class="att-whyc"><b title=${x.name}>${x.name}</b></td><td>${x.type}</td><td class="att-whyc"><span class="att-why" title=${x.status}>${x.status || '—'}</span></td><td class="att-whyc"><span class="att-why" title=${x.note}>${x.note || '—'}</span></td>
          <td>${SAFE_URL.test(x.source) ? html`<button type="button" class="att-link" onClick=${() => ctx.link({ kind: 'external', url: x.source })}>바로가기</button>` : html`<span class="att-why att-subtle" title=${x.source}>${x.source || '—'}</span>`}</td>
          <td class="att-acts"><span class="att-actions">
            <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${holidayWhy(ctx, x)} onClick=${() => ctx.openDialog({ kind: 'hol', orig: x.d, v: Object.assign({}, x) })}>수정</${Btn}>
            <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${holidayWhy(ctx, x)} onClick=${() => ctx.openDialog({ kind: 'hdel', k: x.d })}>삭제…</${Btn}></span></td></tr>`)
          : html`<tr><td class="att-empty" colspan="7">${year + '년 휴일이 없어요.'}</td></tr>`}</tbody></table></div>`;
  }
  function holidayWhy(ctx, x) {
    const ro = ctx.roWhy();
    if (ro) return ro;
    return ctx.S.locks['hol:' + x.d] ? { text: T.LOST_WRITE } : null;
  }

  /* D36: settings live in dashboard cards and share the existing write/holiday machinery. */
  function IdentitySettings(p) {
    const ctx = p.ctx, D = ctx.D, S = ctx.S;
    if (!D) return html`<p class="att-subtle">읽는 중…</p>`;
    const doc = D.doc, why = ctx.roWhy() || (S.locks.set ? { text: T.LOST_WRITE } : null) || (S.setBusy ? { text: '저장 중…', tone: 'muted' } : null);
    const row = (label, value, action) => html`<div class="att-srow"><span class="att-sk">${label}</span><span class="att-sv">${value}</span><span class="att-sb">${action}</span></div>`;
    const open = (id, base, label) => id ? html`<button class="att-btn-quiet att-btn-sm" onClick=${() => ctx.link({ kind: 'external', url: base + encodeURIComponent(id) })}>${label}</button>` : null;
    // D17: the teacher's own template, picked once in Google Picker in the browser; the read after it shows the name.
    const pickTemplate = async () => {
      const out = await ctx.call('/v1/data/picker/report-template', {});
      if (out.status === 200 && out.body && out.body.url) { ctx.link({ kind: 'external', url: out.body.url }); ctx.toast('브라우저에서 양식을 고른 뒤 이 창을 다시 열면 고른 양식이 보여요.'); }
      else ctx.toast(out.unknown ? '응답을 받지 못했어요. 잠시 뒤 다시 눌러 주세요.' : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code: out.body && out.body.error }).text);
    };
    return html`<${NoticeSlot} ctx=${ctx} />
      <div class="att-sgrp"><div class="att-sgrp-h">출결</div>
        ${row('신고서 양식', doc.reportTemplateDocId ? (doc.reportTemplateName || '연결됨') : '정해지지 않음', html`${open(doc.reportTemplateDocId, 'https://docs.google.com/document/d/', '문서 열기')}
          <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ctx.roWhy()} onClick=${pickTemplate}>양식 고르기…</${Btn}>`)}
        ${row('신고서 폴더', doc.reportFolderId ? '연결됨' : '정해지지 않음', open(doc.reportFolderId, 'https://drive.google.com/drive/folders/', '폴더 열기'))}
        ${row('AI 출결 입력', doc.aiInputAllowed === true ? '켜짐' : '꺼짐', html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${why} onClick=${() => ctx.saveSetting({ aiInputAllowed: doc.aiInputAllowed !== true })}>바꾸기</${Btn}>`)}
        ${row('전체 자료', '모든 학년도의 출결 자료', html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${why} onClick=${async () => { ctx.set({ setBusy: true }); try { const result = await ctx.opts.exportAll(); if (result?.saved) ctx.toast('전체 자료를 저장했어요.'); } catch (e) { ctx.explain({ text: e.message || '전체 자료를 저장하지 못했어요.' }); } finally { ctx.set({ setBusy: false }); } }}>전체 내보내기</${Btn}>`)}
      </div>`;
  }

  /* ---------- dialogs ---------- */
  function DialogHost(p) {
    const ctx = p.ctx, S = ctx.S, d = S.dialog;
    useEffect(() => {
      const box = ctx.dialogBox.current;
      if (!box) return;
      const first = box.querySelector('[data-autofocus]') || box.querySelector('input,select,textarea,button');
      if (first) first.focus();
    }, [d.seq]);
    const onBackdrop = ev => { if (ev.target === ev.currentTarget) ctx.closeDialog(); };
    const Comp = { docs: DocsDialog, del: DeleteDialog, period: PeriodDialog, hol: HolidayDialog, hdel: HolidayDeleteDialog, tasklist: TaskListDialog, tasks: TasksDialog, reports: ReportsDialog, closeq: CloseQueueDialog,
      'chat-send': ChatSendDialog, 'chat-decide': ChatDecisionDialog, 'message-edit': MessageEditDialog, welcome: WelcomeDialog, 'link-show': LinkShowDialog, 'report-edit': ReportEditDialog,
      rollover: RolloverDialog, rostercopy: RosterCopyDialog }[d.kind];
    return html`<div class="att-dlg-ov" onClick=${onBackdrop}>
      <div class="att-dlg" role="dialog" aria-modal="true" aria-labelledby="att-dlg-title" ref=${ctx.dialogBox} onInput=${ctx.markDirty} onChange=${ctx.markDirty}>
        <${Boundary}><${Comp} key=${d.seq} ctx=${ctx} d=${d} /></${Boundary}>
      </div></div>`;
  }
  function DlgNotice(p) {
    const n = p.ctx.S.dlgNotice;
    if (!n) return null;
    return html`<div class=${'att-notice att-dlg-note att-' + (n.tone || 'warn')} role="status">${n.text}${n.fix ? ' ' : null}<${FixBtn} fix=${n.fix} /></div>`;
  }
  const Foot = p => html`<div class="att-ov-foot">${p.children}</div>`;
  const CancelBtn = p => html`<button type="button" class=${'att-btn-quiet' + (p.cls || '')} data-cancel="1" data-autofocus=${p.noFocus ? null : '1'} disabled=${p.busy ? true : null} onClick=${p.ctx.closeDialog}>${p.label || '취소'}</button>`;
  const opt = (value, label, cur) => html`<option value=${value} selected=${value === cur}>${label}</option>`;
  function DocsDialog(p) {
    const ctx = p.ctx, D = ctx.D, rows = p.d.ids.map(id => D.rowById.get(id)).filter(r => r && !r.deletedAt);
    const [g, setG] = useState('그대로'), [hh, setH] = useState('그대로');
    const [running, setRunning] = useState(false);
    const val = (v, cur) => (v === '그대로' ? cur : v === '(비움)' ? '' : v);
    const changed = g !== '그대로' || hh !== '그대로';
    const why = ctx.roWhy() || (rows.some(r => ctx.lockedRow(r.id)) ? { text: T.LOST_WRITE } : null) || (running ? { text: '저장하는 중이에요.', tone: 'muted' } : null) || (changed ? null : { text: '신고서나 첨부 가운데 하나를 바꿔 주세요.', tone: 'muted' });
    const sel = (value, on) => html`<select class="att-fsel" value=${value} onChange=${ev => on(ev.target.value)}>${DOC_BULK.map(x => opt(x, x, value))}</select>`;
    const save = async () => {
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const body = { action: 'status', rows: rows.map(r => ({ recordId: r.id, version: r.version })) };
        if (g !== '그대로') body.reportStatus = val(g, '');
        if (hh !== '그대로') body.attachmentStatus = val(hh, '');
        const ids = rows.map(r => r.id);
        const out = await ctx.runWrite({ route: ATT, body, lockKey: 'rows:' + ids.join(','), rowIds: ids });
        if (out.kind === 'ok') {
          closing = true;
          const fields = { reminder: null };
          if (body.reportStatus !== undefined) fields.reportStatus = body.reportStatus;
          if (body.attachmentStatus !== undefined) fields.attachmentStatus = body.attachmentStatus;
          ctx.patchRows(out.body.rows, fields);
          ctx.set({ dialogBusy: false, sel: new Set(), anchor: null });
          ctx.closeDialogNow();
          ctx.toast(rows.length + '줄의 서류 상태를 바꿨어요.');
          ctx.flash(ids);
          ctx.read();
        } else {
          ctx.set({ dlgNotice: ctx.whyOf(out) });
        }
      } finally {
        if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); }
      }
    };
    return html`<h4 id="att-dlg-title">${'선택한 ' + rows.length + '줄의 서류 상태를 바꿀까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-row att-mt"><label class="att-fl">신고서 ${sel(g, setG)}</label><label class="att-fl">첨부 ${sel(hh, setH)}</label></div>
      <div class="att-ov-list">${rows.map(r => html`<div key=${r.id}><b>${r.label}</b>${' ' + dlab(r) + ' ' + r.kind + ' · 신고서 ' + (r.g || '비움') + ' → ' + (val(g, r.g) || '비움') + ' · 첨부 ' + (r.h || '비움') + ' → ' + (val(hh, r.h) || '비움')}</div>`)}</div>
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" why=${why} onClick=${save}>${running ? '바꾸는 중…' : rows.length + '줄 바꾸기'}</${Btn}></${Foot}>`;
  }

  // SIGN-IN 'Tasks steps': one Google task per 미제출 document, on demand. The server skips a task that is already there.
  const TASK_RESULT = { added: '추가함', already: '이미 추가함', existing: '이미 있음', 'not-missing': '미제출 아님', 'no-student': '학생 없음', 'in-progress': '다른 창에서 추가하는 중',
    'needs-decision': '결과 확인 필요', unknown: '결과 확인 필요', rejected: '추가하지 못함', deferred: '나중에 다시', stale: '다시 읽어 주세요' };
  function TasksDialog(p) {
    const ctx = p.ctx, D = ctx.D, rows = p.d.ids.map(id => D.rowById.get(id)).filter(r => r && !r.deletedAt && (r.g === '미제출' || r.h === '미제출'));
    const [st, setSt] = useState({ running: false, results: null });
    const items = rows.flatMap(r => [r.g === '미제출' ? [r, 'report', '결석신고서'] : null, r.h === '미제출' ? [r, 'attachment', '첨부서류'] : null].filter(Boolean));
    const run = async () => {
      setSt({ running: true, results: null });
      ctx.set({ dialogBusy: true, dlgNotice: null });
      try {
        const out = await ctx.call('/v1/data/tasks', { rows: rows.map(r => ({ recordId: r.id, version: r.version })) });
        if (out.status === 200 && out.body && Array.isArray(out.body.results)) setSt({ running: false, results: out.body.results });
        else {
          setSt({ running: false, results: null });
          ctx.set({ dlgNotice: out.unknown ? { text: '할 일을 추가했는지 확인하지 못했어요. 다시 누르지 말고 Google Tasks에서 먼저 확인해 주세요.' } : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code: out.body && out.body.error, details: out.body && out.body.details }) });
        }
      } finally { ctx.set({ dialogBusy: false }); ctx.read(); }
    };
    const res = st.results, byKey = new Map((res || []).map(x => [x.recordId + ':' + x.kind, x]));
    const list = D.doc.taskListTitle || 'Tasks 목록';
    return html`<h4 id="att-dlg-title">${res ? '할 일 추가 결과' : '‘' + list + '’에 할 일 ' + items.length + '개를 추가할까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-ov-list">${items.map(([r, kind, word]) => { const x = byKey.get(r.id + ':' + kind);
        return html`<div key=${r.id + kind}><b>${r.label}</b>${' ' + dlab(r) + ' ' + word + ' 미제출 확인 필요'}${x ? html` · <b>${TASK_RESULT[x.status] || x.status}</b>` : null}</div>`; })}</div>
      ${res ? null : html`<p class="att-subtle">같은 제목의 끝나지 않은 할 일이 이미 있으면 다시 만들지 않아요.</p>`}
      <${Foot}>${res ? html`<button class="att-btn" onClick=${ctx.closeDialogNow}>닫기</button>`
        : html`<${CancelBtn} ctx=${ctx} busy=${st.running} /><${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (st.running ? { text: '추가하는 중이에요.', tone: 'muted' } : null)} onClick=${run}>${st.running ? '추가하는 중…' : '추가하기'}</${Btn}>`}</${Foot}>`;
  }

  // SIGN-IN 'Report steps': one Doc per row, or one per 결석 span, copied from the year's template and filled
  // as the sheet did. The first press makes the folder `출결 증빙` and the template when they are missing.
  const REPORT_RESULT = { made: '만들었어요', already: '이미 만든 신고서', 'same-span': '같은 연속 결석에 포함', 'in-progress': '다른 창에서 만드는 중',
    'needs-decision': '결과 확인 필요', rejected: '만들지 못했어요', deferred: '나중에 다시', stale: '다시 읽어 주세요', 'no-date': '날짜 없음',
    'no-student': '학생 없음', 'no-kind': '종류 없음', 'no-period': '교시를 골라 주세요' };
  function ReportsDialog(p) {
    const ctx = p.ctx, D = ctx.D, rows = p.d.ids.map(id => D.rowById.get(id)).filter(r => r && !r.deletedAt && r.date && /결석|지각|조퇴|결과/.test(r.kind || ''));
    const [st, setSt] = useState({ running: false, results: null, step: '' });
    const run = async recreate => {  // recreate: the report-files kinds to make again
      setSt({ running: true, results: null, step: '' });
      ctx.set({ dialogBusy: true, dlgNotice: null });
      const refuse = out => ctx.set({ dlgNotice: out.unknown ? { text: '신고서를 만들었는지 확인하지 못했어요. 다시 누르지 말고 Google Drive의 ‘출결 증빙’ 폴더를 먼저 확인해 주세요.' }
        : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code: out.body && out.body.error, details: out.body && out.body.details }) });
      try {
        // Only the folder is ever made here; the template is the teacher's own, picked once (owner, 2026-11-09).
        if (recreate || !D.doc.reportFolderId) {
          setSt({ running: true, results: null, step: '‘출결 증빙’ 폴더를 준비하는 중…' });
          const made = await ctx.call('/v1/data/years/report-files', { requestId: 'rf-' + Date.now().toString(36), kinds: ['report-folder'], ...(recreate ? { recreate: ['report-folder'] } : {}) });
          const res = made.status === 200 && made.body && made.body.results;
          if (!res || !['created', 'already'].includes((res['report-folder'] || {}).status)) {
            if (made.status === 200) ctx.set({ dlgNotice: { text: '‘출결 증빙’ 폴더를 준비하지 못했어요. 조치할 일을 확인해 주세요.' } }); else refuse(made);
            setSt({ running: false, results: null, step: '' });
            return;
          }
        }
        setSt({ running: true, results: null, step: '신고서를 만드는 중…' });
        const out = await ctx.call('/v1/data/reports', { rows: rows.map(r => ({ recordId: r.id, version: r.version })) });
        if (out.status === 200 && out.body && Array.isArray(out.body.results)) setSt({ running: false, results: out.body.results, step: '' });
        else if (out.body && out.body.error === 'REPORT_FILES_MISSING') setSt({ running: false, results: null, step: '', needPick: true });
        else { setSt({ running: false, results: null, step: '' }); refuse(out); }
      } finally { ctx.set({ dialogBusy: false }); ctx.read(); }
    };
    // Google Picker in the browser: the chosen document itself becomes the template (D17).
    const pick = async () => {
      ctx.set({ dlgNotice: null });
      const out = await ctx.call('/v1/data/picker/report-template', {});
      if (out.status === 200 && out.body && out.body.url) { ctx.link({ kind: 'external', url: out.body.url }); setSt({ running: false, results: null, step: '', picking: true }); }
      else refuse(out);
    };
    const refuse = out => ctx.set({ dlgNotice: out.unknown ? { text: '응답을 받지 못했어요. 잠시 뒤 다시 눌러 주세요.' } : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code: out.body && out.body.error, details: out.body && out.body.details }) });
    const res = st.results, byId = new Map((res || []).map(x => [x.recordId, x]));
    const failed = (res || []).find(x => ['REPORT_TEMPLATE_UNAVAILABLE', 'REPORT_FOLDER_UNAVAILABLE'].includes(x.code));
    const lost = !failed ? [] : failed.missing && failed.missing.length ? failed.missing : [failed.code === 'REPORT_FOLDER_UNAVAILABLE' ? 'report-folder' : 'report-template'];
    const open = id => ctx.link({ kind: 'external', url: 'https://docs.google.com/document/d/' + encodeURIComponent(id) + '/edit' });
    return html`<h4 id="att-dlg-title">${res ? '신고서 만들기 결과' : '선택한 ' + rows.length + '줄의 출결 신고서를 만들까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-ov-list">${rows.map(r => { const x = byId.get(r.id);
        return html`<div key=${r.id}><b>${r.label}</b>${' ' + dlab(r) + ' ' + (r.kind || '')}${x ? html` · <b>${REPORT_RESULT[x.status] || x.status}</b>` : null}${x && x.docId ? html` <button class="att-link" onClick=${() => open(x.docId)}>열기</button>` : null}</div>`; })}</div>
      ${st.needPick || lost.includes('report-template') ? html`<p class="att-band att-warn">${st.needPick ? '결석신고서 양식을 아직 고르지 않았어요.' : '연결된 신고서 양식을 열 수 없어요.'} 지금 쓰는 양식(Google 문서)을 한 번 골라 주세요. 고른 문서를 그대로 써요.</p>
        <div class="att-row"><${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ctx.roWhy()} onClick=${pick}>양식 고르기…</${Btn}></div>` : null}
      ${st.picking ? html`<p class="att-subtle">브라우저에서 양식을 고른 뒤 돌아와 [만들기]를 다시 눌러 주세요.</p>` : null}
      ${lost.length === 1 && lost[0] === 'report-folder' ? html`<p class="att-band att-warn">‘출결 증빙’ 폴더를 열 수 없어요. 새 폴더를 만들 수 있어요. 예전 폴더는 그대로 둬요.</p>
        <div class="att-row"><${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ctx.roWhy() || (st.running ? { text: '만드는 중이에요.', tone: 'muted' } : null)} onClick=${() => run(true)}>폴더 다시 만들기…</${Btn}></div>` : null}
      ${res ? null : html`<p class="att-subtle">${st.step || '연속된 결석은 신고서 하나로 만들어요. 만든 신고서는 Google Drive의 ‘출결 증빙’ 폴더에 들어가요.'}</p>`}
      <${Foot}>${res ? html`<button class="att-btn" onClick=${ctx.closeDialogNow}>닫기</button>`
        : html`<${CancelBtn} ctx=${ctx} busy=${st.running} /><${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (st.running ? { text: '만드는 중이에요.', tone: 'muted' } : null)} onClick=${() => run(false)}>${st.running ? '만드는 중…' : '만들기'}</${Btn}>`}</${Foot}>`;
  }

  const sendBlock = r => (r.remUnknown
    ? { why: '독려 개인톡 결과 확인 필요', text: '독려 개인톡의 결과를 확인하지 못해 지금 삭제할 수 없어요.' }
    : { why: '독려 개인톡 보내는 중', text: '독려 개인톡을 보내는 중이라 지금 삭제할 수 없어요.' });
  function delFacts(r) {
    const sent = r.reminder && r.reminder.state === '보냄';
    const when = sent ? kst(r.reminder.sentAt).slice(0, 10) : '';
    return [r.hasReport ? '신고서 문서 있음' : '', sent ? '독려 개인톡 ' + (when ? md(when) + ' ' : '') + '보냄' : ''].filter(Boolean).join(' · ');
  }
  const delLine = r => [dlab(r), r.label, r.cat, r.kind, r.reason, r.period].filter(Boolean).join(' · ');
  function DeleteDialog(p) {
    const ctx = p.ctx, D = ctx.D, rows = p.d.ids.map(id => D.rowById.get(id)).filter(Boolean);
    const ok = rows.filter(E.canDelete), no = rows.filter(r => !E.canDelete(r) && !r.deletedAt);
    const [running, setRunning] = useState(false);
    const r0 = rows[0];
    const title = rows.length === 1 && r0 ? r0.label + ' · ' + dlab(r0) + ' ' + r0.kind + ' 줄을 삭제할까요?' : '선택한 ' + rows.length + '줄을 삭제할까요?';
    const why = ctx.roWhy() || (ok.some(r => ctx.lockedRow(r.id)) ? { text: T.LOST_WRITE } : null) || (running ? { text: '저장하는 중이에요.', tone: 'muted' } : null) || (ok.length ? null : { text: '선택한 줄은 지금 삭제할 수 없어요.', tone: 'muted' });
    const remove = async () => {
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const ids = ok.map(r => r.id);
        const out = await ctx.runWrite({ route: ATT, body: { action: 'delete', rows: ok.map(r => ({ recordId: r.id, version: r.version })) }, lockKey: 'rows:' + ids.join(','), rowIds: ids });
        if (out.kind === 'ok') {
          closing = true;
          const results = out.body.rows || [];
          ctx.moveRows(results, true);
          ctx.set(prev => { const sel = new Set(prev.sel); ids.forEach(id => sel.delete(id)); return { dialogBusy: false, sel, anchor: sel.size ? prev.anchor : null }; });
          ctx.closeDialogNow();
          ctx.toast(ids.length + '줄을 삭제했어요.', { label: '되돌리기', run: () => undoDelete(ctx, results) });
          ctx.read();
        } else {
          ctx.set({ dlgNotice: ctx.whyOf(out) });
        }
      } finally {
        if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); }
      }
    };
    return html`<h4 id="att-dlg-title">${title}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-ov-list">${ok.map(r => html`<div key=${r.id}>${delLine(r)}${delFacts(r) ? html`<div class="att-q-meta">${delFacts(r)}</div>` : null}</div>`)}</div>
      ${no.length ? html`<div class="att-ov-sub">지울 수 없는 줄</div><div class="att-ov-list">${no.map(r => html`<div key=${r.id}>${r.label + ' ' + dlab(r) + ' · ' + sendBlock(r).why}</div>`)}</div>` : null}
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" why=${why} onClick=${remove}>${running ? '삭제 중…' : no.length && ok.length ? '나머지 ' + ok.length + '줄 삭제' : '삭제'}</${Btn}></${Foot}>`;
  }
  async function undoDelete(ctx, results) {
    ctx.set({ toast: null });
    const out = await ctx.runWrite({ route: ATT, body: { action: 'restore', rows: results.map(x => ({ recordId: x.recordId, version: x.version })) } });
    if (out.kind === 'ok') {
      ctx.moveRows(out.body.rows, false);
      ctx.toast('삭제를 되돌렸어요.');
      ctx.flash((out.body.rows || []).map(x => x.recordId));
      ctx.read();
    } else if (out.code === 'ATTENDANCE_DUPLICATE') {
      ctx.explain({ text: restoreDupText(ctx, results) });
    } else {
      ctx.explain(ctx.whyOf(out));
    }
  }
  function restoreDupText(ctx, results) {
    const D = ctx.D, r = D && results.length === 1 ? D.rowById.get(results[0].recordId) : null;
    return r ? '같은 줄이 이미 있어 되살리지 않았어요: ' + r.label + ' ' + dlab(r) + ' ' + r.kind : '같은 줄이 이미 있어 되살리지 않았어요.';
  }
  function PeriodDialog(p) {
    const ctx = p.ctx, D = ctx.D, P = periodNow(D, ctx.S.view);
    const [s, setStart] = useState(P.s), [e, setEnd] = useState(P.e);
    const apply = () => {
      const start = parseDateInput(s, ctx.S.rec.ym, D.cal), end = parseDateInput(e, ctx.S.rec.ym, D.cal);
      if (!dateInYear(start, D) || !dateInYear(end, D)) { ctx.explain({ text: '날짜는 이번 학년도 안에서 골라 주세요.' }); return; }
      if (start > end) { ctx.explain({ text: '시작 날짜가 끝 날짜보다 늦어요.' }); return; }
      ctx.setView({ period: 'custom', custom: { s: start, e: end } });
      ctx.closeDialog();
    };
    return html`<h4 id="att-dlg-title">기간 선택</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-fgrid"><label class="att-fld">시작 날짜<${FormDate} ctx=${ctx} label="시작 날짜" value=${s} onValue=${setStart} /></label>
        <label class="att-fld">끝 날짜<${FormDate} ctx=${ctx} label="끝 날짜" value=${e} onValue=${setEnd} /></label></div>
      <${Foot}><${CancelBtn} ctx=${ctx} noFocus /><button type="button" class="att-btn" data-primary="1" onClick=${apply}>적용</button></${Foot}>`;
  }

  // 수정해서 기록 (2026-11-11 design §6.3, §13.1 Q-L9): the student's values, changed by the teacher, recorded in one
  // press. Only what changed goes as edits; the server plans the rows and keeps 출석인정 for a 생리 reason (Q-L5).
  function ReportEditDialog(p) {
    const ctx = p.ctx, D = ctx.D, rep = p.d.rep;
    const [f, setF] = useState(() => ({ start: rep.start, end: rep.end || rep.start, kind: KINDS.indexOf(rep.kind) >= 0 ? rep.kind : '결석함',
      periods: rep.periods.slice(), cat: rep.cat, reason: rep.note }));
    const [running, setRunning] = useState(false);
    const put = (k, v) => setF(old => Object.assign({}, old, { [k]: v }));
    const off = new Set(D.holidays.map(x => x.d));
    const dates = [];
    if (isDateKey(f.start) && isDateKey(f.end) && f.start <= f.end && span(f.start, f.end) <= 60) {
      for (let d = f.start; d <= f.end; d = addDays(d, 1)) if (dow(d) !== 0 && dow(d) !== 6 && !off.has(d)) dates.push(d);
    }
    const menses = isMenses(f.reason), cat = menses ? '출석인정' : f.cat;
    const one = f.kind === '지각함' || f.kind === '조퇴함', many = f.kind === '결과함';
    const periods = f.kind === '결석함' ? [] : one ? f.periods.slice(0, 1) : f.periods;
    const why = !dates.length ? '학교 가는 날이 없어요. 날짜를 다시 골라 주세요.' : dates.length > 10 ? '한 번에 10일까지 기록할 수 있어요.'
      : dates.some(d => !dateInYear(d, D)) ? '날짜는 이번 학년도 안에서 골라 주세요.' : !cat ? '구분을 골라 주세요.'
      : one && periods.length !== 1 ? '교시를 하나 골라 주세요.' : many && !periods.length ? '빠진 교시를 골라 주세요.'
      : !String(f.reason).trim() ? '사유를 입력해 주세요.' : '';
    const rows = dates.length * (many ? Math.max(1, periods.length) : 1);
    const save = async () => {
      if (running) return;
      if (why) { ctx.set({ dlgNotice: { text: why } }); return; }
      const edits = {};
      if (dates.join() !== rep.dates.join()) edits.dates = dates;
      if (f.kind !== rep.kind) edits.kind = f.kind;
      if (periods.join() !== rep.periods.join()) edits.periods = periods;
      if (cat !== rep.cat || rep.rawCat !== cat) edits.category = cat;
      if (String(f.reason).trim() !== rep.note) edits.reason = String(f.reason).trim();
      setRunning(true);
      try {
        ctx.set({ dialogBusy: true, dlgNotice: null });
        const out = await ctx.recordReport(rep, edits);
        if (out.forgotten || memo.gen !== ctx.opts.generation) return;
        if (out.kind === 'ok') ctx.closeDialogNow();
        else if (out.kind !== 'busy') ctx.set({ dlgNotice: ctx.whyOf(out) });
      } finally { if (memo.gen === ctx.opts.generation) { ctx.set({ dialogBusy: false }); setRunning(false); } }
    };
    const togglePeriod = v => put('periods', one ? [v] : f.periods.indexOf(v) >= 0 ? f.periods.filter(x => x !== v) : PERIODS.filter(x => x && (f.periods.indexOf(x) >= 0 || x === v)));
    return html`<h4 id="att-dlg-title">${rep.label + ' 학생의 신고를 고쳐서 기록'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-fgrid">
        <label class="att-fld">시작 날짜<input type="date" value=${f.start} onInput=${e => put('start', e.target.value)} /></label>
        <label class="att-fld">끝 날짜<input type="date" value=${f.end} onInput=${e => put('end', e.target.value)} /></label>
        <label class="att-fld">종류<select value=${f.kind} onChange=${e => put('kind', e.target.value)}>${KINDS.map(k => html`<option value=${k}>${kindWord(k)}</option>`)}</select></label>
        <label class="att-fld">구분<select value=${cat} disabled=${menses ? true : null} onChange=${e => put('cat', e.target.value)}><option value="">골라 주세요</option>${CATS.map(c => html`<option value=${c}>${c}</option>`)}</select></label>
      </div>
      ${menses ? html`<p class="att-faint">생리 사유는 출석인정으로 기록돼요.</p>` : null}
      ${f.kind !== '결석함' ? html`<div class="att-fld" role="group" aria-label="교시">교시<span class="att-chips">${PERIODS.filter(Boolean).map(v => html`<label class="att-ckl"><input type=${one ? 'radio' : 'checkbox'} name="report-period" checked=${periods.indexOf(v) >= 0} onChange=${() => togglePeriod(v)} />${v}</label>`)}</span></div>` : null}
      <label class="att-fld">사유<input type="text" maxlength="200" value=${f.reason} onInput=${e => put('reason', e.target.value)} /></label>
      <p class="att-faint">${dates.length ? dates.map(mdw).join(', ') + ' · ' + rows + '줄' : '학교 가는 날이 없어요.'}</p>
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" primary=${true} why=${ctx.roWhy() || (running ? { text: '기록하는 중이에요.', tone: 'muted' } : null)} onClick=${save}>${running ? '기록하는 중…' : rows > 1 ? rows + '줄 기록하기' : '기록하기'}</${Btn}></${Foot}>`;
  }

  function HolidayDialog(p) {
    const ctx = p.ctx, D = ctx.D, S = ctx.S, d = p.d;
    const [v, setV] = useState({ d: d.v.d, name: d.v.name, type: d.v.type, status: d.v.status, note: d.v.note, source: d.v.source });
    const [running, setRunning] = useState(false);
    const up = patch => setV(Object.assign({}, v, patch));
    const key = JSON.stringify([d.orig, v.d, v.name.trim(), v.type, v.status.trim(), v.note.trim(), v.source.trim()]);
    const lockKey = 'hol:' + (d.orig || 'add');
    const lockEntry = S.locks[lockKey];
    const why = ctx.roWhy() || (lockEntry && lockEntry.formKey === key ? { text: T.LOST_WRITE } : null) || (running ? { text: '저장하는 중이에요.', tone: 'muted' } : null);
    const save = async () => {
      const date = parseDateInput(v.d, (d.orig || d.v.d).slice(0, 7), D.cal);
      if (!date || !v.name.trim()) { ctx.explain({ text: '날짜와 명칭을 적어 주세요.' }); return; }
      if (!dateInYear(date, D)) { ctx.explain({ text: '날짜는 이번 학년도 안에서 골라 주세요.' }); return; }
      const clean = { name: v.name.trim(), type: v.type, status: v.status.trim(), note: v.note.trim(), source: v.source.trim() };
      const body = d.orig ? { action: 'edit', date: d.orig, version: d.v.version } : Object.assign({ action: 'add', date }, clean);
      if (d.orig) {
        if (date !== d.orig) body.newDate = date;
        Object.keys(clean).forEach(f => { if (clean[f] !== d.v[f]) body[f] = clean[f]; });
        if (Object.keys(body).length === 3) { ctx.closeDialog(); return; }
      }
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const out = await ctx.runWrite({ route: HOL, body, lockKey, formKey: key });
        if (out.kind === 'ok') {
          closing = true;
          ctx.setIn('holiday', { year: Number(date.slice(0, 4)) });
          ctx.set({ dialogBusy: false });
          ctx.closeDialogNow();
          ctx.toast(d.orig ? '휴일을 고쳤어요.' : '휴일을 추가했어요.');
          await ctx.read();
        } else {
          ctx.set({ dlgNotice: ctx.whyOf(out) });
        }
      } finally {
        if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); }
      }
    };
    return html`<h4 id="att-dlg-title">${d.orig ? '휴일 수정 · ' + mdw(d.orig) + ' ' + d.v.name : '휴일 추가'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-fgrid">
        <label class="att-fld">날짜<${FormDate} ctx=${ctx} value=${v.d} ym=${(d.orig || d.v.d).slice(0, 7)} onValue=${date => up({ d: date })} /></label>
        <label class="att-fld">명칭<input value=${v.name} maxlength="100" onInput=${ev => up({ name: ev.target.value })} /></label>
        <label class="att-fld">구분<select value=${v.type} onChange=${ev => up({ type: ev.target.value })}>${HOL_KINDS.map(k => opt(k, k, v.type))}</select></label>
        <label class="att-fld">상태<input value=${v.status} maxlength="40" onInput=${ev => up({ status: ev.target.value })} /></label>
        <label class="att-fld">비고<input value=${v.note} maxlength="500" onInput=${ev => up({ note: ev.target.value })} /></label>
        <label class="att-fld">출처<input value=${v.source} maxlength="1000" placeholder="https://…" onInput=${ev => up({ source: ev.target.value })} /></label></div>
      <${Foot}>
        ${d.orig ? html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-sep" why=${ctx.roWhy()} onClick=${() => ctx.openDialog({ kind: 'hdel', k: d.orig })}>이 휴일 삭제…</${Btn}>` : null}
        <${CancelBtn} ctx=${ctx} noFocus busy=${running} />
        <${Btn} ctx=${ctx} cls="att-btn" primary why=${why} onClick=${save}>${running ? '저장 중…' : '저장'}</${Btn}></${Foot}>`;
  }

  // MAIN §7.9, SCREENS 'Other dialogs': the class fields come from last year and can be changed first.
  const rid = prefix => prefix + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  function RolloverDialog(p) {
    const ctx = p.ctx, D = ctx.D, y = D.raw.currentSchoolYear, doc = D.doc;
    const [v, setV] = useState({ schoolName: doc.schoolName || '', grade: doc.grade || '', classNumber: doc.classNumber || '', classLabel: '', teacherName: doc.teacherName || '' });
    const [req] = useState(() => rid('roll'));
    const [running, setRunning] = useState(false);
    const up = patch => setV(Object.assign({}, v, patch));
    const run = async () => {
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const out = await ctx.call('/v1/data/years/rollover', { requestId: req, year: y, classFields: v });
        if (out.status === 200 && out.body && ['created', 'exists'].includes(out.body.status)) {
          closing = true;
          ctx.set({ dialogBusy: false });
          ctx.closeDialogNow();
          ctx.toast(y + '학년도를 시작했어요.');
          await ctx.read();
        } else {
          ctx.set({ dlgNotice: out.unknown ? { text: '새 학년도를 시작했는지 확인하지 못했어요. 창을 다시 읽은 뒤 확인해 주세요.' }
            : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code: out.body && out.body.error, details: out.body && out.body.details }) });
        }
      } finally { if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); } }
    };
    const fact = (label, value) => html`<div><b>${label}</b> ${value || '없음'}</div>`;
    return html`<h4 id="att-dlg-title">${y + '학년도를 시작할까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-fgrid">
        <label class="att-fld">학교 이름<input value=${v.schoolName} maxlength="100" onInput=${ev => up({ schoolName: ev.target.value })} /></label>
        <label class="att-fld">학년<input value=${v.grade} maxlength="100" onInput=${ev => up({ grade: ev.target.value })} /></label>
        <label class="att-fld">반<input value=${v.classNumber} maxlength="100" onInput=${ev => up({ classNumber: ev.target.value })} /></label>
        <label class="att-fld">반 이름<input value=${v.classLabel} maxlength="100" placeholder="비우면 학년·반으로 적어요" onInput=${ev => up({ classLabel: ev.target.value })} /></label>
        <label class="att-fld">담임 이름<input value=${v.teacherName} maxlength="100" onInput=${ev => up({ teacherName: ev.target.value })} /></label></div>
      <div class="att-ov-list">${fact('서식 문서', doc.reportTemplateName)}${fact('저장 폴더', doc.reportFolderId ? '출결 증빙' : '')}${fact('할 일 목록', doc.taskListTitle)}
        <div><b>휴일</b> 올해 공휴일을 넣어 둬요.</div><div><b>학급 단톡방 · 학생 인사</b> 새 반에서 다시 시작해요.</div></div>
      <p class="att-subtle">학생명단은 따로 복사해요.</p>
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" primary why=${running ? { text: '시작하는 중이에요.', tone: 'muted' } : null} onClick=${run}>${running ? '시작하는 중…' : '시작하기'}</${Btn}></${Foot}>`;
  }

  function RosterCopyDialog(p) {
    const ctx = p.ctx, D = ctx.D, from = D.doc.rolledOverFrom;
    const [req] = useState(() => rid('rcopy'));
    const [running, setRunning] = useState(false);
    const run = async () => {
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const out = await ctx.call('/v1/data/students', { requestId: req, action: 'copy-previous-year', year: D.year });
        if (out.status === 200 && out.body && Number.isInteger(out.body.copied)) {
          closing = true;
          ctx.set({ dialogBusy: false });
          ctx.closeDialogNow();
          ctx.toast('학생 ' + out.body.copied + '명을 복사했어요.');
          await ctx.read();
        } else {
          const code = out.body && out.body.error;
          ctx.set({ dlgNotice: out.unknown ? { text: '복사했는지 확인하지 못했어요. 창을 다시 읽은 뒤 확인해 주세요.' }
            : code === 'NO_EARLIER_ROSTER' ? { text: from + '학년도 학생명단에 재적 학생이 없어요.' }
            : code === 'ROSTER_NOT_EMPTY' ? { text: '학생명단에 이미 학생이 있어 복사하지 않았어요.' }
            : ctx.whyOf({ kind: out.status === 401 ? 'session' : 'refused', status: out.status, code, details: out.body && out.body.details }) });
        }
      } finally { if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); } }
    };
    return html`<h4 id="att-dlg-title">${from + '학년도 학생명단을 복사할까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <p>재적 학생의 번호·이름·이메일을 그대로 가져와요. 복사한 뒤 번호가 바뀐 학생은 학생명단에서 고쳐 주세요.</p>
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" primary why=${running ? { text: '복사하는 중이에요.', tone: 'muted' } : null} onClick=${run}>${running ? '복사하는 중…' : '복사'}</${Btn}></${Foot}>`;
  }

  function HolidayDeleteDialog(p) {
    const ctx = p.ctx, D = ctx.D, x = D.holidays.find(item => item.d === p.d.k);
    const [running, setRunning] = useState(false);
    if (!x) return html`<h4 id="att-dlg-title">휴일 수정</h4><div class="att-notice att-warn att-mt">${T.STALE}</div><${Foot}><${CancelBtn} ctx=${ctx} label="닫기" /></${Foot}>`;
    const why = ctx.roWhy() || (ctx.S.locks['hol:' + x.d] ? { text: T.LOST_WRITE } : null) || (running ? { text: '저장하는 중이에요.', tone: 'muted' } : null);
    const title = mdw(x.d) + ' ' + x.name;
    const remove = async () => {
      setRunning(true);
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const out = await ctx.runWrite({ route: HOL, body: { action: 'delete', date: x.d, version: x.version }, lockKey: 'hol:' + x.d });
        if (out.kind === 'ok') {
          closing = true;
          const version = out.body.version;
          ctx.patchSnap(snap => Object.assign({}, snap, { holidays: (snap.holidays || []).filter(item => item.id !== x.d) }));
          ctx.set({ dialogBusy: false });
          ctx.closeDialogNow();
          ctx.toast(title + josaEul(title) + ' 삭제했어요.', { label: '되돌리기', run: () => undoHoliday(ctx, x.d, version) });
          ctx.read();
        } else {
          ctx.set({ dlgNotice: ctx.whyOf(out) });
        }
      } finally {
        if (!closing) { ctx.set({ dialogBusy: false }); setRunning(false); }
      }
    };
    return html`<h4 id="att-dlg-title">${title + josaEul(title) + ' 삭제할까요?'}</h4><${DlgNotice} ctx=${ctx} />
      <div class="att-ov-list"><div>${[mdw(x.d), x.name, x.type, x.status].filter(Boolean).join(' · ')}</div></div>
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} /><${Btn} ctx=${ctx} cls="att-btn" why=${why} onClick=${remove}>${running ? '삭제 중…' : '삭제'}</${Btn}></${Foot}>`;
  }
  async function undoHoliday(ctx, date, version) {
    ctx.set({ toast: null });
    const out = await ctx.runWrite({ route: HOL, body: { action: 'restore', date, version } });
    if (out.kind === 'ok') { ctx.toast('삭제를 되돌렸어요.'); ctx.read(); } else ctx.explain(ctx.whyOf(out));
  }

  // The teacher's lists are read each time (C1); a failed read is never an empty list. No list creation here.
  function TaskListDialog(p) {
    const ctx = p.ctx, D = ctx.D;
    const [st, setSt] = useState({ phase: 'reading', lists: [], pick: D.doc.taskListId || '', running: false });
    useEffect(() => {
      let live = true;
      ctx.call(TASKLISTS, { action: 'list' }).then(reply => {
        if (!live) return;
        const body = reply.body && typeof reply.body === 'object' ? reply.body : null;
        const code = body && body.error;
        if (reply.status === 200 && body && Array.isArray(body.tasklists)) {
          const lists = body.tasklists;
          setSt(prev => Object.assign({}, prev, { phase: 'list', lists, pick: lists.some(x => x.id === prev.pick) ? prev.pick : '' }));
          return;
        }
        if (code === 'GOOGLE_PERMISSION_REQUIRED') ctx.set({ dlgNotice: { text: T.PERM, fix: { label: '권한 다시 승인', run: ctx.reapprove } } });
        setSt(prev => Object.assign({}, prev, { phase: 'fail' }));
      });
      return () => { live = false; };
    }, []);
    const pickWhy = st.phase === 'reading' ? { text: '목록을 불러오는 중…', tone: 'muted' } : st.phase === 'fail' ? { text: '목록을 불러오지 못했어요', tone: 'muted' } : !st.pick ? { text: '할 일 목록을 골라 주세요.', tone: 'muted' } : null;
    const why = ctx.roWhy() || (ctx.S.locks.set ? { text: T.LOST_WRITE } : null) || (st.running ? { text: '저장하는 중이에요.', tone: 'muted' } : null) || pickWhy;
    const apply = async () => {
      setSt(prev => Object.assign({}, prev, { running: true }));
      ctx.set({ dialogBusy: true, dlgNotice: null });
      let closing = false;
      try {
        const chosen = st.lists.find(x => x.id === st.pick);
        const out = await ctx.runWrite({ route: SET, body: { version: D.doc.version, taskListId: st.pick }, lockKey: 'set' });
        if (out.kind === 'ok') {
          closing = true;
          ctx.patchSnap(snap => Object.assign({}, snap, { yearDoc: Object.assign({}, snap.yearDoc, { taskListId: st.pick, taskListTitle: chosen ? chosen.title : '', version: out.body.version }) }));
          ctx.set({ dialogBusy: false });
          ctx.closeDialogNow();
          ctx.toast('학급 설정을 저장했어요.');
          ctx.read();
        } else {
          ctx.set({ dlgNotice: ctx.whyOf(out) });
        }
      } finally {
        if (!closing) { ctx.set({ dialogBusy: false }); setSt(prev => Object.assign({}, prev, { running: false })); }
      }
    };
    return html`<h4 id="att-dlg-title">할 일 목록 바꾸기</h4><${DlgNotice} ctx=${ctx} />
      ${st.phase === 'reading' ? html`<div class="att-notice att-muted att-mt" role="status">목록을 불러오는 중…</div>`
        : st.phase === 'fail' ? html`<div class="att-notice att-muted att-mt" role="status">목록을 불러오지 못했어요</div>`
        : html`<div class="att-radio">${st.lists.map(x => html`<label key=${x.id}><input type="radio" name="att-tasklist" value=${x.id} checked=${x.id === st.pick} onChange=${() => setSt(prev => Object.assign({}, prev, { pick: x.id }))} />${' ' + x.title}</label>`)}</div>`}
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${st.running} /><${Btn} ctx=${ctx} cls="att-btn" why=${why} onClick=${apply}>${st.running ? '저장 중…' : '이 목록으로 바꾸기'}</${Btn}></${Foot}>`;
  }

  // Accepted sentences keep running after the window closes; their results return on the next mount.
  function CloseQueueDialog(p) {
    const ctx = p.ctx, d = p.d;
    const title = '처리 중인 문장의 결과는 출결 기록에서 확인해 주세요.';
    return html`<h4 id="att-dlg-title">${title}</h4>
      <${Foot}><${CancelBtn} ctx=${ctx} /><button type="button" class="att-btn" onClick=${() => { ctx.closeDialogNow(); d.then(); }}>창 닫기</button></${Foot}>`;
  }

  /* ---------- Chat (P3): preview, named confirmation, one send ---------- */
  function chatError(reply) {
    const code = String(reply?.body?.error || '');
    if (code === 'CHAT_NOT_CONNECTED' || code === 'GOOGLE_PERMISSION_REQUIRED') return T.PERM;
    if (code === 'TEACHER_RATE_LIMIT') return '오늘 Google Chat으로 보낼 수 있는 메시지 수를 넘었어요.';
    if (code === 'CHAT_RATE_LIMITED') return 'Google Chat이 잠시 발송을 제한했어요. 아직 보내지 않은 메시지는 대기 중이에요.';
    return reply?.body?.message || (reply?.status === 401 ? T.SESSION : reply?.status === 409 ? T.STALE : T.NOT_READ);
  }
  function useChatStatus(ctx) {
    const [state, setState] = useState(null);
    const value = state?.raw === ctx.D.raw ? state.value : undefined;
    useEffect(() => {
      let ended = false;
      const raw = ctx.D.raw;
      setState({ raw, value: undefined });
      let observations = 0;
      const unwatch = ctx.opts.watchChatStatus?.(ctx.D.year, value => {
        observations++;
        if (!ended && memo.gen === ctx.opts.generation) setState({raw, value});
      });
      const reading = ctx.opts.readChatStatus ? ctx.opts.readChatStatus(ctx.D.year) : ctx.call('/v1/data/chat/status', { year: ctx.D.year });
      const atStart = observations;
      reading.then(r => {
        if (!ended && memo.gen === ctx.opts.generation && observations === atStart) setState({raw, value:r.status === 200 ? r.body : null});
      }).catch(error => {
        console.error(error);
        if (!ended && memo.gen === ctx.opts.generation && observations === atStart) setState({raw, value:null});
      });
      return () => { ended = true; unwatch?.(); };
    }, [ctx.D.raw]);
    return value;
  }
  function ChatHealth(p) {
    const ctx = p.ctx, value = useChatStatus(ctx);
    const connected = value?.connected;
    const space = value?.classSpace?.displayName, group = value?.groupState;
    return html`<div class="att-chat-health">
      <${Chip} tone=${connected === true ? 'ok' : connected === false ? 'warn' : 'muted'}>${value === undefined ? 'Google Chat 확인 중…' : connected === true ? 'Google Chat 연결됨' : connected === false ? 'Google Chat 연결 필요' : 'Google Chat 확인하지 못했어요'}</${Chip}>
      ${connected === false ? html`<button class="att-link" onClick=${ctx.reapprove}>권한 다시 승인</button>` : null}
      ${group === 'ready' && space ? html`<${Chip} tone="ok" title=${space}>${'학급 단톡방 · ' + space}</${Chip}>` : null}
      ${group === 'not-selected' || group === 'missing' ? html`<${Chip} tone="warn">학급 단톡방 연결 필요</${Chip}>${ctx.opts.card === 'connect' ? null : html`<button class="att-link" onClick=${() => { ctx.closeDialog(); ctx.link({ kind: 'attendance-connect' }); }}>연결 → 출결 열기</button>`}` : null}
      ${group === 'denied' || group === 'unverified' ? html`<${Chip} tone="muted">학급 단톡방 확인하지 못했어요</${Chip}><button class="att-link" onClick=${ctx.read}>다시 확인</button>` : null}
    </div>`;
  }
  function ReminderButton(p) {
    const ctx = p.ctx, r = p.row;
    if (!E.missing(r) || !r.st || r.deletedAt) return null;
    const effect = ctx.D.effects.find(x => x.id === r.reminder?.effectId);
    if (r.remUnknown) return html`<button class="att-link" onClick=${() => ctx.openDialog({ kind: 'chat-decide', effectId: effect?.id })}>확인하기</button>`;
    if (!r.st.hasEmail) return html`<button class="att-link" onClick=${() => ctx.link({ kind: 'roster', studentId: r.studentId, focusEmail: true })}>학생명단 고치기</button>`;
    if (r.reminder?.state === '실패' && r.reminder.effectId) return html`<button class="att-link" onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'reminders', resendOf: r.reminder.effectId, ids: [r.id] })}>다시 보내기…</button>`;
    if (r.reminder && ['보냄', '실패', '발송중'].includes(r.reminder.state)) return null;
    return html`<button class="att-link" onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'reminders', ids: [r.id] })}>독려 보내기…</button>`;
  }
  // 연결 → 출결: one Google Chat row in the shape of app.js classTaskRowHtml, with the class room and the
  // 1:1 greetings as two sub-rows under it (svc-subrow), one icon in all.
  function ClassChatRow(p) {
    const ctx = p.ctx, D = ctx.D, records = D.raw.greetings || [];
    const value = useChatStatus(ctx), group = value?.groupState, connected = value?.connected;
    const reading = value === undefined, ready = group === 'ready' && !!value?.classSpace?.name, none = ['not-selected', 'missing'].includes(group);
    const active = D.students.filter(x => x.status === '재적' && x.hasEmail);
    const sent = records.filter(x => String(x.id).startsWith('p_') && x.state === 'sent' && active.some(s => s.id === x.studentId)).length;
    const rejected = records.filter(x => String(x.id).startsWith('p_') && x.state === 'rejected' && active.some(s => s.id === x.studentId));
    const btn = (label, run) => html`<${Btn} ctx=${ctx} cls="btn-tonal" why=${ctx.roWhy()} onClick=${run}>${label}</${Btn}>`;
    const welcome = () => ctx.openDialog({ kind: 'welcome' });
    const chat = reading ? ['muted', '확인 중…'] : connected === true ? ['ok', '연결됨'] : connected === false ? ['warn', '연결 필요'] : ['muted', '확인하지 못했어요'];
    const room = reading ? ['muted', '확인 중…'] : ready ? ['ok', '연결됨'] : none ? ['warn', '연결 필요'] : ['muted', '확인하지 못했어요'];
    const allSent = active.length > 0 && sent === active.length;
    const people = reading ? ['muted', '확인 중…'] : allSent ? ['ok', '연결됨'] : ['warn', sent + '/' + active.length + '명'];
    return chatRows({ chat, chatAct: connected === false ? btn('권한 다시 승인', ctx.reapprove) : null,
      room, roomSmall: ready ? value.classSpace.displayName : '', roomAct: none ? btn('환영 인사 보내면서 Google Chat 대화 상대로 연결하기', welcome) : !reading && !ready ? btn('다시 확인', ctx.read) : null,
      people, peopleAct: !reading && ready && !allSent ? btn('환영 인사 보내기', welcome) : null,
      note: rejected.length ? '받지 못한 학생: ' + rejected.map(x => D.byId.get(x.studentId)?.label || '').filter(Boolean).join(', ') + '. 학생명단의 이메일을 확인해 주세요.' : '' });
  }
  /* ---------- 학생 결석 신고 링크 (2026-11-11 design §7.3, §13.1 Q-L3) ---------- */
  // One link state per student, from the read: 없음, 보내는 중, 보냄, 결과 확인 필요 (C7: never resent by itself), 보내지 못함.
  function linkStateOf(D, st) {
    const link = D.links.get(st.id) || null, nowMs = Date.parse(String(D.raw.serverNow || ''));
    const effect = link && link.effectId ? D.effects.find(x => x.id === link.effectId) : null;
    const old = effect && effect.phase === 'DISPATCHED_OR_UNKNOWN' && !(nowMs - Date.parse(String(effect.admittedAt || effect.updatedAt || '')) < UNKNOWN_AFTER_MS);
    const raw = String(link && link.sendState || '');
    // sendState (§15.3): "not-sent" | "unknown" | "sent" | "rejected"; sendPending: a send not settled yet.
    let state = 'none';
    if (effect && (effect.phase === 'NEEDS_DECISION' || old)) state = 'unknown';
    else if (effect && (effect.phase === 'CLAIMED' || effect.phase === 'DISPATCHED_OR_UNKNOWN')) state = 'sending';
    else if (raw === 'unknown') state = 'unknown';
    else if (link && link.sendPending === true) state = 'sending';
    else if (raw === 'rejected') state = 'rejected';
    else if (raw === 'sent') state = 'sent';
    else if (Number(link && link.activeCount) > 0) state = 'copied';
    // A student whose 1:1 greeting was rejected cannot get a link by Chat (design §7.1): copy or QR only.
    const greeting = (D.raw.greetings || []).find(x => String(x.id).startsWith('p_') && x.studentId === st.id);
    if ((state === 'none' || state === 'rejected') && greeting && greeting.state === 'rejected') state = 'blocked';
    return { link, effect, state };
  }
  const LINK_CHIP = { blocked: ['warn', '보낼 수 없음'], copied: ['muted', '복사한 링크'], none: ['muted', '없음'], sending: ['muted', '보내는 중'], sent: ['ok', '보냄'], unknown: ['warn', '결과 확인 필요'], rejected: ['warn', '보내지 못함'] };
  function StudentLinkRow(p) {
    const ctx = p.ctx, D = ctx.D, [open, setOpen] = useState(false);
    const active = D.students.filter(s => s.status === '재적');
    const rows = active.map(st => Object.assign({ st }, linkStateOf(D, st)));
    const n = k => rows.filter(x => x.state === k).length;
    const sent = n('sent'), sending = n('sending'), unknown = n('unknown'), rejected = n('rejected') + n('blocked');
    const toSend = rows.filter(x => x.state === 'none' && x.st.hasEmail).map(x => x.st.id);
    const known = 'studentLinks' in D.raw;
    const all = active.length > 0 && sent === active.length;
    const status = !known ? ['muted', '확인하지 못했어요'] : all ? ['ok', '모두 보냄'] : ['warn', sent + '/' + active.length + '명'];
    const ro = ctx.roWhy();
    const btn = (label, run, why) => html`<${Btn} ctx=${ctx} cls="btn-tonal" why=${why || ro} onClick=${run}>${label}</${Btn}>`;
    const small = (label, run, why) => html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${why || ro} onClick=${run}>${label}</${Btn}>`;
    const send = (ids, rotate, label) => ctx.openDialog({ kind: 'chat-send', mode: 'links', studentIds: ids, rotate: rotate === true, label: label || '' });
    const show = x => ctx.openDialog({ kind: 'link-show', studentId: x.st.id, label: x.st.label, had: x.state !== 'none', version: x.link && x.link.version });
    const acts = x => {
      if (x.state === 'sending') return null;
      if (x.state === 'unknown') return x.effect ? small('확인하기', () => ctx.openDialog({ kind: 'chat-decide', effectId: x.effect.id })) : null;
      const out = [];
      if (!x.st.hasEmail || x.state === 'rejected' || x.state === 'blocked') out.push(small('학생명단 고치기', () => ctx.link({ kind: 'roster', studentId: x.st.id, label: x.st.label, focusEmail: true })));
      if (x.st.hasEmail && x.state !== 'blocked') {
        if (x.state === 'sent' || x.state === 'copied') out.push(small('링크 다시 보내기…', () => send([x.st.id], false, x.st.label)), small('링크 새로 만들기…', () => send([x.st.id], true, x.st.label)));
        else out.push(small(x.state === 'rejected' ? '다시 보내기…' : '링크 보내기…', () => send([x.st.id], false, x.st.label)));
      }
      out.push(small('링크 복사·QR 코드…', () => show(x)));
      return out;
    };
    const when = x => {
      const l = x.link || {}, parts = [];
      if (x.state === 'sent' && (l.issuedAt || l.sentAt)) parts.push(atShort(l.issuedAt || l.sentAt, D.today));
      if (l.lastViewAt) parts.push('열어 봄 ' + atShort(l.lastViewAt, D.today));
      if (x.state === 'unknown') parts.push('학생이 받았는지 확인하지 못했어요');
      if (x.state === 'rejected' || x.state === 'blocked') parts.push(sendErrorText('STUDENT_CHAT_RECIPIENT_REJECTED'));
      if (!x.st.hasEmail) parts.push('Google 이메일 없음');
      return parts.join(' · ');
    };
    return html`<div class="svc-group att-connect-chat att-links" data-service="student-link"><div class="svc-subrow">
        <span class="nameblock"><b>학생 결석 신고 링크</b><small>학생이 결석·지각·조퇴·결과를 선생님께 알리는 학생별 주소예요</small></span>
        <span class="svc-acts">${known ? html`<span class="att-chips">${sent ? html`<${Chip} tone="ok">${'링크 받은 학생 ' + sent + '/' + active.length + '명'}</${Chip}>` : null}${sending ? html`<${Chip} tone="muted">${'보내는 중 ' + sending + '명'}</${Chip}>` : null}${unknown ? html`<${Chip} tone="warn">${'결과 확인 필요 ' + unknown + '명'}</${Chip}>` : null}${rejected ? html`<${Chip} tone="warn">${'보내지 못함 ' + rejected + '명'}</${Chip}>` : null}</span>` : null}
          ${known && toSend.length ? btn('링크 보내기…', () => send(toSend, false)) : null}
          ${known && active.length ? html`<button type="button" class="att-btn-quiet att-btn-sm" aria-expanded=${open ? 'true' : 'false'} onClick=${() => setOpen(!open)}>${open ? '접기' : '학생별 보기'}</button>` : null}</span>
        <span class=${'svc-status ' + status[0]}>${status[1]}</span></div>
      ${open ? html`<div class="att-link-list">${rows.map(x => { const chip = LINK_CHIP[x.state], tail = when(x);
        return html`<div class="att-al" key=${x.st.id}><div class="att-t1"><b>${x.st.label}</b> <${Chip} tone=${chip[0]}>${chip[1]}</${Chip}>${tail ? html` <span class="att-faint">${'· ' + tail}</span>` : null}</div><div class="att-ac">${acts(x)}</div></div>`; })}</div>` : null}
    </div>`;
  }
  // 링크 복사 and the QR code (Q-L3). Only the hash is stored, so showing a link makes a new one and the old one stops.
  // The plaintext lives in this dialog's state only: never logged, never stored (LINK-02).
  function LinkShowDialog(p) {
    const ctx = p.ctx, d = p.d, [url, setUrl] = useState(''), [error, setError] = useState(''), [running, setRunning] = useState(false), [copied, setCopied] = useState('');
    const done = useRef(false);
    const make = async () => {
      if (done.current || running) return;
      done.current = true;
      setRunning(true);
      try {
        ctx.set({ dialogBusy: true });
        const out = await ctx.runWrite({ route: LINKS, body: Object.assign({ action: 'issue', studentId: d.studentId }, Number.isInteger(d.version) ? { version: d.version } : {}) });
        if (out.forgotten) return;
        const link = out.kind === 'ok' ? String(out.body.link || out.body.url || '') : '';
        if (link && SAFE_URL.test(link)) setUrl(link);
        else setError(out.kind === 'ok' ? '링크를 받지 못했어요. 창을 닫고 다시 해 주세요.' : out.kind === 'unknown' ? '새 링크를 만들었는지 확인하지 못했어요. 다시 만들면 그 링크도 새 링크로 바뀌어요.' : ctx.whyOf(out).text);
        ctx.read();
      } finally { if (memo.gen === ctx.opts.generation) { ctx.set({ dialogBusy: false }); setRunning(false); } }
    };
    const copy = () => {
      const box = document.createElement('textarea');
      const legacy = () => { box.value = url; box.setAttribute('readonly', ''); box.style.position = 'fixed'; box.style.opacity = '0'; document.body.appendChild(box); box.select(); let ok = false; try { ok = document.execCommand('copy'); } catch (err) { ok = false; } box.remove(); return ok; };
      const fine = () => setCopied('복사했어요.'), fail = () => setCopied('복사하지 못했어요. 주소를 직접 골라 복사해 주세요.');
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(fine).catch(() => (legacy() ? fine() : fail()));
      else if (legacy()) fine(); else fail();
    };
    if (!url) return html`<h4 id="att-dlg-title">${d.label + ' 학생의 링크를 새로 만들어 보여 줄까요?'}</h4><${DlgNotice} ctx=${ctx}/>
      ${d.had ? html`<p>지금 쓰는 주소는 바로 쓸 수 없게 돼요.</p>` : null}<p>이 주소는 이 학생 전용이에요. 다른 사람에게 보이지 않게 해 주세요.</p>
      ${error ? html`<p class="att-warn">${error}</p>` : null}
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running}/>${!error ? html`<${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (running ? { text: '만드는 중…', tone: 'muted' } : null)} onClick=${make}>${running ? '만드는 중…' : '새 링크 만들기'}</${Btn}>` : null}</${Foot}>`;
    return html`<h4 id="att-dlg-title">${d.label + ' 학생의 결석 신고 링크'}</h4>
      <div class="att-linkshow"><${QrCode} text=${url} /><div class="att-linkshow-r">
        <input class="att-linkshow-url" readOnly=${true} value=${url} aria-label="결석 신고 링크" onFocus=${ev => ev.target.select()} />
        <div class="att-ac"><button type="button" class="att-btn-quiet att-btn-sm" onClick=${copy}>링크 복사</button><span class="att-faint" role="status">${copied}</span></div>
        <p class="att-faint">학생이 휴대폰 카메라로 QR 코드를 찍으면 바로 열려요. 이 창을 닫으면 링크를 다시 볼 수 없어요.</p></div></div>
      <${Foot}><button type="button" class="att-btn" data-autofocus="1" data-cancel="1" onClick=${ctx.closeDialog}>닫기</button></${Foot}>`;
  }

  /* ---------- QR code (ISO/IEC 18004, byte mode, level M). Drawn here: no external service, no CDN. ---------- */
  const QR_ECC_M = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28];
  const QR_BLOCKS_M = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49];
  function qrRawModules(ver) {
    let r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) { const a = Math.floor(ver / 7) + 2; r -= (25 * a - 10) * a - 55; if (ver >= 7) r -= 36; }
    return r;
  }
  const qrDataBytes = ver => Math.floor(qrRawModules(ver) / 8) - QR_ECC_M[ver] * QR_BLOCKS_M[ver];
  function qrMul(x, y) { let z = 0; for (let i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * 0x11D); z ^= ((y >>> i) & 1) * x; } return z; }
  function qrDivisor(degree) {
    const r = new Array(degree).fill(0); r[degree - 1] = 1; let root = 1;
    for (let i = 0; i < degree; i++) { for (let j = 0; j < r.length; j++) { r[j] = qrMul(r[j], root); if (j + 1 < r.length) r[j] ^= r[j + 1]; } root = qrMul(root, 0x02); }
    return r;
  }
  function qrRemainder(data, div) {
    const r = div.map(() => 0);
    data.forEach(b => { const f = b ^ r.shift(); r.push(0); div.forEach((c, i) => { r[i] ^= qrMul(c, f); }); });
    return r;
  }
  function qrEncode(text) {
    const bytes = Array.from(new TextEncoder().encode(text));
    let ver = 1;
    for (; ver <= 40; ver++) if (4 + (ver <= 9 ? 8 : 16) + bytes.length * 8 <= qrDataBytes(ver) * 8) break;
    if (ver > 40) return null;
    const cap = qrDataBytes(ver) * 8, bits = [];
    const put = (v, n) => { for (let i = n - 1; i >= 0; i--) bits.push((v >>> i) & 1); };
    put(4, 4); put(bytes.length, ver <= 9 ? 8 : 16); bytes.forEach(b => put(b, 8));
    put(0, Math.min(4, cap - bits.length)); put(0, (8 - bits.length % 8) % 8);
    for (let pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) put(pad, 8);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
    // Blocks with their error correction, interleaved.
    const nb = QR_BLOCKS_M[ver], eccLen = QR_ECC_M[ver], raw = Math.floor(qrRawModules(ver) / 8);
    const nShort = nb - raw % nb, shortLen = Math.floor(raw / nb), div = qrDivisor(eccLen), blocks = [];
    for (let i = 0, k = 0; i < nb; i++) {
      const dat = data.slice(k, k + shortLen - eccLen + (i < nShort ? 0 : 1));
      k += dat.length;
      const ecc = qrRemainder(dat, div);
      if (i < nShort) dat.push(0);
      blocks.push(dat.concat(ecc));
    }
    const words = [];
    for (let i = 0; i < blocks[0].length; i++) blocks.forEach((b, j) => { if (i !== shortLen - eccLen || j >= nShort) words.push(b[i]); });
    // Function patterns.
    const size = ver * 4 + 17, mod = [], fn = [];
    for (let y = 0; y < size; y++) { mod.push(new Array(size).fill(false)); fn.push(new Array(size).fill(false)); }
    const setF = (x, y, dark) => { mod[y][x] = dark; fn[y][x] = true; };
    for (let i = 0; i < size; i++) { setF(6, i, i % 2 === 0); setF(i, 6, i % 2 === 0); }
    [[3, 3], [size - 4, 3], [3, size - 4]].forEach(c => {
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        const x = c[0] + dx, y = c[1] + dy, dist = Math.max(Math.abs(dx), Math.abs(dy));
        if (x >= 0 && x < size && y >= 0 && y < size) setF(x, y, dist !== 2 && dist !== 4);
      }
    });
    const align = [];
    if (ver > 1) {
      const na = Math.floor(ver / 7) + 2, step = Math.floor((ver * 8 + na * 3 + 5) / (na * 4 - 4)) * 2;
      align.push(6);
      for (let pos = size - 7; align.length < na; pos -= step) align.splice(1, 0, pos);
    }
    align.forEach((ax, i) => align.forEach((ay, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === align.length - 1) || (i === align.length - 1 && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) setF(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }));
    // Error correction level M is 00 in the format bits.
    const format = mask => {
      const v = mask;
      let r = v;
      for (let i = 0; i < 10; i++) r = (r << 1) ^ ((r >>> 9) * 0x537);
      const b = ((v << 10) | r) ^ 0x5412, bit = i => ((b >>> i) & 1) === 1;
      for (let i = 0; i <= 5; i++) setF(8, i, bit(i));
      setF(8, 7, bit(6)); setF(8, 8, bit(7)); setF(7, 8, bit(8));
      for (let i = 9; i < 15; i++) setF(14 - i, 8, bit(i));
      for (let i = 0; i < 8; i++) setF(size - 1 - i, 8, bit(i));
      for (let i = 8; i < 15; i++) setF(8, size - 15 + i, bit(i));
      setF(8, size - 8, true);
    };
    format(0);
    if (ver >= 7) {
      let r = ver;
      for (let i = 0; i < 12; i++) r = (r << 1) ^ ((r >>> 11) * 0x1F25);
      const b = (ver << 12) | r;
      for (let i = 0; i < 18; i++) { const dark = ((b >>> i) & 1) === 1, a = size - 11 + i % 3, c = Math.floor(i / 3); setF(a, c, dark); setF(c, a, dark); }
    }
    // Data in the zigzag.
    let bi = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) for (let j = 0; j < 2; j++) {
        const x = right - j, up = ((right + 1) & 2) === 0, y = up ? size - 1 - vert : vert;
        if (!fn[y][x] && bi < words.length * 8) { mod[y][x] = ((words[bi >>> 3] >>> (7 - (bi & 7))) & 1) === 1; bi++; }
      }
    }
    const masks = [(x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, x => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
      (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => (x * y) % 2 + (x * y) % 3 === 0,
      (x, y) => ((x * y) % 2 + (x * y) % 3) % 2 === 0, (x, y) => ((x + y) % 2 + (x * y) % 3) % 2 === 0];
    const applyMask = m => { for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fn[y][x] && masks[m](x, y)) mod[y][x] = !mod[y][x]; };
    // Penalty rules 1, 2 and 4 pick a readable mask; any mask decodes.
    const penalty = () => {
      let p = 0, dark = 0;
      for (let a = 0; a < size; a++) {
        let rr = 1, rc = 1;
        for (let b = 1; b < size; b++) {
          if (mod[a][b] === mod[a][b - 1]) { rr++; if (rr === 5) p += 3; else if (rr > 5) p++; } else rr = 1;
          if (mod[b][a] === mod[b - 1][a]) { rc++; if (rc === 5) p += 3; else if (rc > 5) p++; } else rc = 1;
        }
      }
      for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        if (mod[y][x]) dark++;
        if (x < size - 1 && y < size - 1 && mod[y][x] === mod[y][x + 1] && mod[y][x] === mod[y + 1][x] && mod[y][x] === mod[y + 1][x + 1]) p += 3;
      }
      return p + Math.max(0, Math.ceil(Math.abs(dark * 20 - size * size * 10) / (size * size)) - 1) * 10;
    };
    let best = 0, bestP = Infinity;
    for (let m = 0; m < 8; m++) { applyMask(m); format(m); const pm = penalty(); if (pm < bestP) { bestP = pm; best = m; } applyMask(m); }
    applyMask(best); format(best);
    return mod;
  }
  function QrCode(p) {
    const mod = useMemo(() => { try { return qrEncode(p.text); } catch (err) { console.error(err); return null; } }, [p.text]);
    if (!mod) return html`<p class="att-faint">QR 코드를 만들지 못했어요. 링크를 복사해 주세요.</p>`;
    const n = mod.length, q = 4, size = n + q * 2;
    let d = '';
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (mod[y][x]) d += 'M' + (x + q) + ' ' + (y + q) + 'h1v1h-1z';
    return html`<svg class="att-qr" viewBox=${'0 0 ' + size + ' ' + size} width="200" height="200" role="img" aria-label="결석 신고 링크 QR 코드" shape-rendering="crispEdges">
      <rect width=${size} height=${size} fill="#fff" /><path d=${d} fill="#000" /></svg>`;
  }

  // The same three lines before the first read: the Google Chat row stays in place, grey 확인 중… (C6·C8).
  function chatRows(v) {
    const sub = (title, small, tone, status, acts, note) => html`<div class="svc-subrow">
      <span class="nameblock"><b>${title}</b><small>${small}</small></span>
      <span class="svc-acts">${acts || null}</span><span class=${'svc-status ' + tone}>${status}</span>
      ${note ? html`<p class="hint attendance-chat-action-hint att-connect-note">${note}</p>` : null}</div>`;
    return html`<div class="svc-group att-connect-chat" data-service="chat"><div class="attendance-service">
        <img class="service-logo" src="assets/google-chat.svg" alt="" />
        <span class="nameblock"><b>Google Chat</b><small>학생에게 메시지를 보낼 때 쓰는 계정 권한</small></span>
        <span class="svc-acts">${v.chatAct || null}</span>
        <span class=${'svc-status ' + v.chat[0]}>${v.chat[1]}</span></div>
      ${sub('학급 단톡방', v.roomSmall || '학생들과 함께 쓰는 그룹 채팅', v.room[0], v.room[1], v.roomAct)}
      ${sub('학생 1:1 대화', '학생마다 처음 한 번 인사를 보내 대화를 열어요', v.people[0], v.people[1], v.peopleAct, v.note)}
    </div>`;
  }
  const CHAT_ROWS_READING = { chat: ['muted', '확인 중…'], room: ['muted', '확인 중…'], people: ['muted', '확인 중…'] };
  function MessagesTab(p) {
    const ctx = p.ctx, D = ctx.D, W = ctx.W;
    if (!D || !W) return null;
    const open = W.s1.filter(x => x.refs?.some(ref => ref.includes('/messages/')));
    const person = messageColumn(D, W, 'personal'), room = messageColumn(D, W, 'class');
    const people = new Set(person.due.map(x => x.studentId)).size;
    const known = !W.partial;
    const summary = !known ? '확인하지 못했어요' : people || room.due.length ? ['개인톡 ' + people + '명', '단체톡 ' + room.due.length + '줄'].join(' · ') : '없음';
    const activity = D.raw.registrationActivity || [];
    const chat = useChatStatus(ctx), roomWhy = chat !== undefined && chat?.groupState !== 'ready'
      ? { text: ['not-selected', 'missing'].includes(chat?.groupState) ? '학급 단톡방이 연결되지 않았어요. 연결 → 출결에서 연결해 주세요.' : '학급 단톡방을 확인하지 못했어요. 창 위쪽 [새로 읽기] 버튼(↻)을 눌러 주세요.' } : null;
    return html`<${NoticeSlot} ctx=${ctx} /><div class="att-mtab">
      <section class="att-card att-mhero">
        <div class="att-mhero-r"><div class="att-mhero-t"><h3>오늘 보낼 메시지</h3><span>${summary}</span></div>
          <${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (!person.due.length && !room.due.length ? { text: '오늘 보낼 메시지가 없어요.' } : !person.due.length && roomWhy ? roomWhy : null)} onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'queue' })}>개인톡+단체톡 한 번에 보내기</${Btn}></div>
        <${ChatHealth} ctx=${ctx} />
      </section>
      ${open.length ? html`<${WorkCard} title="전달됐는지 모르는 메시지" n=${open.length}>${open.map(x => html`<${Line} key=${x.id} btns=${html`<button class="att-btn-quiet att-btn-sm" onClick=${() => ctx.openDialog({ kind: 'chat-decide', effectId: x.id })}>확인하기</button>`}>${unknownLine(x, D)}</${Line}>`)}</${WorkCard}>` : null}
      <div class="att-mcols">
        <${MessageColumn} ctx=${ctx} channel="personal" col=${person} known=${known} send=${'오늘 보낼 ' + people + '명에게 보내기'} />
        <${MessageColumn} ctx=${ctx} channel="class" col=${room} known=${known} blocked=${roomWhy} send=${'오늘 보낼 ' + room.due.length + '줄 보내기'} />
      </div>
      <${SendLogCard} ctx=${ctx} />
      <${LaterMessages} ctx=${ctx} rows=${[...person.later, ...room.later].sort((a, b) => String(a.date).localeCompare(String(b.date)))} />
      ${activity.length ? html`<section class="att-card"><div class="att-card-h"><h3>일정·할 일 등록 기록</h3></div>${activity.map(x => html`<p class="att-ml" key=${x.id || x.at}><span class="att-ml-t">${[atLong(x.at), x.kind === 'calendar' ? '일정' : '할 일', x.phase === 'CONFIRMED_RESULT' ? '등록됨' : x.phase === 'CONFIRMED_REJECTED' ? '등록되지 않았어요' : '확인 필요', x.updatedBy?.deviceLabel || ''].filter(Boolean).join(' · ')}</span></p>`)}</section>` : null}
    </div>`;
  }
  // One channel's messages sorted by what the teacher does with them. "due" is the same test as W.due and the server's preview.
  // Failed and finished messages live in the send log, not here.
  function messageColumn(D, W, channel) {
    const unknown = new Set(W.s1.map(x => x.id)), sending = new Set(W.s7.map(x => x.id));
    const col = { due: [], later: [], sending: [], review: [] };
    const order = (a, b) => String(a.date).localeCompare(String(b.date)) || String(a.id).localeCompare(String(b.id));
    (D.raw.messages || []).filter(x => x.channel === channel).slice().sort(order).forEach(row => {
      if (['보냄', '제외', '실패'].includes(row.status) || (row.effectId && unknown.has(row.effectId))) return;
      if (row.status === '발송중' || (row.effectId && sending.has(row.effectId))) col.sending.push(row);
      else if (row.needsReview || !row.date || !row.type || !row.status || (channel === 'personal' && !row.studentId)) col.review.push(row);
      else if (row.date > D.today) col.later.push(row);
      else col.due.push(row);
    });
    return col;
  }
  // The checked messages of one list. Checks on messages that left the list are ignored.
  function usePicked(rows) {
    const [ids, setIds] = useState(() => new Set());
    const picked = rows.filter(x => ids.has(x.id));
    const toggle = id => setIds(old => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
    return { picked, has: id => ids.has(id), toggle, clear: () => setIds(new Set()) };
  }
  function PickedButtons(p) {
    const ctx = p.ctx, pick = p.pick, n = pick.picked.length, ro = ctx.roWhy();
    return html`<${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ro || (n === 1 ? null : { text: '수정할 메시지를 하나만 골라 주세요.' })} onClick=${() => { const row = pick.picked[0]; ctx.openDialog({ kind: 'message-edit', row, channel: row.channel }); }}>수정</${Btn}>
      <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ro || (n ? null : { text: '삭제할 메시지를 골라 주세요.' })} onClick=${() => { const rows = pick.picked; ctx.openDialog({ kind: 'message-edit', rows, row: rows[0], channel: rows[0].channel, remove: true }); }}>${n > 1 ? '삭제 ' + n + '개…' : '삭제…'}</${Btn}>`;
  }
  function MessageColumn(p) {
    const ctx = p.ctx, D = ctx.D, col = p.col, personal = p.channel === 'personal', word = personal ? '개인톡' : '단체톡';
    const pick = usePicked([...col.due, ...col.review]);
    const name = row => (personal ? (D.byId.get(row.studentId)?.label || spaceLabel(row.studentLabel) || '') : '');
    const line = (row, chip, prefix) => html`<label class="att-ml att-ml-pick" key=${row.id}>
      <input type="checkbox" checked=${pick.has(row.id)} aria-label=${row.text} onChange=${() => pick.toggle(row.id)} /><span class="att-ml-dot">·</span>
      <span class="att-ml-t" title=${row.text}>${prefix ? prefix + ' · ' : ''}${row.text}</span>${chip || null}</label>`;
    const groups = [];
    if (personal) {
      col.due.forEach(row => { const g = groups.find(x => x.id === row.studentId); if (g) g.rows.push(row); else groups.push({ id: row.studentId, st: D.byId.get(row.studentId), rows: [row] }); });
      groups.sort((a, b) => (a.st?.n ?? 999) - (b.st?.n ?? 999));
    }
    const hasWork = col.due.length || col.sending.length || col.review.length;
    return html`<section class="att-card att-mcol">
      <div class="att-mcol-h"><h3>${word}</h3><span class="att-mcol-a">
        <${Btn} ctx=${ctx} cls="att-btn-quiet att-btn-sm" why=${ctx.roWhy()} onClick=${() => ctx.openDialog({ kind: 'message-edit', channel: p.channel })}>추가</${Btn}>
        <${PickedButtons} ctx=${ctx} pick=${pick} />
        <${Btn} ctx=${ctx} cls="att-btn-tonal att-btn-sm" why=${ctx.roWhy() || (col.due.length ? p.blocked || null : { text: '오늘 보낼 ' + word + '이 없어요.' })} onClick=${() => ctx.openDialog({ kind: 'chat-send', mode: 'queue', channel: p.channel })}>${col.due.length ? p.send : word + ' 보내기'}</${Btn}></span></div>
      <div class="att-mcol-b">
      ${!p.known ? html`<p class="att-mempty">확인하지 못했어요</p>` : !hasWork ? html`<p class="att-mempty">${'오늘 보낼 ' + word + ' 없음'}</p>` : null}
      ${personal ? groups.map(g => html`<div class="att-mg" key=${g.id}><div class="att-mg-h"><b>${g.st?.label || spaceLabel(g.rows[0].studentLabel) || '—'}</b>${g.rows.length > 1 ? html`<span class="att-faint">${g.rows.length + '줄'}</span>` : null}
          ${g.st && !g.st.hasEmail ? html`<${Chip} tone="warn">이메일 없음</${Chip}><button class="att-link" onClick=${() => ctx.link({ kind: 'roster', studentId: g.id, focusEmail: true })}>학생명단 고치기</button>` : null}</div>
          ${g.rows.map(row => line(row))}</div>`)
        : col.due.length ? html`<div class="att-mg">${col.due.map(row => line(row))}</div>` : null}
      ${col.review.length ? html`<div class="att-mg">${col.review.map(row => line(row, html`<${Chip} tone="warn">${personal && !row.studentId ? '명단에 없음' : '값 확인 필요'}</${Chip}>`, name(row)))}</div>` : null}
      ${col.sending.length ? html`<div class="att-mg">${col.sending.map(row => html`<div class="att-ml" key=${row.id}><span class="att-ml-dot att-ml-nock">·</span><span class="att-ml-t" title=${row.text}>${name(row) ? name(row) + ' · ' : ''}${row.text}</span><${Chip} tone="muted">보내는 중</${Chip}></div>`)}</div>` : null}
      </div>
    </section>`;
  }
  // Greeting rows keep the greeting record's states (sent, rejected, not-sent); show them in the same words as messages.
  const LOG_RESULT = { sent: '보냄', rejected: '실패', 'not-sent': '대기' };
  const SEND_ERROR_TEXT = { STUDENT_CHAT_RECIPIENT_REJECTED: '학생의 Google Chat 계정으로 보낼 수 없어요. 학생명단의 이메일을 확인해 주세요.', CHAT_MESSAGE_REJECTED: 'Google Chat이 이 메시지를 받지 않았어요.', CHAT_NOT_CONNECTED: 'Google Chat 연결이 필요해요.' };
  const sendErrorText = code => (!code ? '' : SEND_ERROR_TEXT[code] || (/^[A-Z0-9_]+$/.test(code) ? '보내지 못했어요.' : code));
  function SendLogCard(p) {
    const ctx = p.ctx, D = ctx.D, log = D.raw.sendLog || [], messages = D.raw.messages || [];
    const acts = x => {
      if (x.result !== '실패' || !x.effectId) return null;
      const row = messages.find(m => m.effectId === x.effectId && m.status === '실패');
      const att = row ? null : D.rows.find(r => r.reminder?.effectId === x.effectId && r.reminder.state === '실패');
      if (!row && !att) return null;
      const ro = ctx.roWhy();
      return html`<${Btn} ctx=${ctx} cls="att-link" why=${ro} onClick=${() => ctx.openDialog(row ? { kind: 'chat-send', mode: 'queue', resendOf: x.effectId } : { kind: 'chat-send', mode: 'reminders', resendOf: x.effectId, ids: [att.id] })}>다시 보내기…</${Btn}>
        ${row ? html`<${Btn} ctx=${ctx} cls="att-link" why=${ro} onClick=${() => ctx.openDialog({ kind: 'message-edit', row, channel: row.channel, remove: true })}>삭제…</${Btn}>` : null}`;
    };
    return html`<section class="att-card"><div class="att-card-h"><h3>Google Chat 발송기록</h3><span class="att-faint">${'최근 300건 · 전체 ' + (D.raw.sendLogCount || 0) + '건'}</span></div>
      ${log.length ? html`<div class="att-mlog-b"><table class="att-mlog"><colgroup><col style="width:112px" /><col style="width:48px" /><col style="width:80px" /><col /><col style="width:72px" /><col style="width:132px" /><col style="width:130px" /></colgroup>
        <thead><tr><th>발송시각</th><th>종류</th><th>대상</th><th>내용 미리보기</th><th>결과</th><th>오류</th><th></th></tr></thead>
        <tbody>${log.map(x => { const err = sendErrorText(x.error), res = LOG_RESULT[x.result] || x.result; return html`<tr key=${x.effectId || x.id}><td title=${x.updatedBy?.deviceLabel || null}>${atLong(x.at)}</td><td>${x.kind === 'chat-class' ? '단체톡' : x.kind === 'chat-greeting' ? '인사' : '개인톡'}</td><td title=${spaceLabel(x.targetLabel)}>${spaceLabel(x.targetLabel)}</td><td title=${x.preview}>${x.preview}</td><td title=${res}><${Chip} tone=${/^(성공|보냄)/.test(String(res)) ? 'ok' : /^(대기|발송중)/.test(String(res)) ? 'muted' : 'warn'}>${res}</${Chip}></td><td title=${err || null}>${err || '—'}</td><td class="att-mlog-a">${acts(x)}</td></tr>`; })}</tbody></table></div>`
        : html`<p class="att-mempty">발송기록 없음</p>`}
    </section>`;
  }
  function LaterMessages(p) {
    const ctx = p.ctx, D = ctx.D, rows = p.rows, pick = usePicked(rows);
    if (!rows.length) return null;
    const date = d => { const m = /^\d{4}-(\d{2})-(\d{2})/.exec(String(d || '')); return m ? Number(m[1]) + '/' + Number(m[2]) : '—'; };
    return html`<section class="att-card"><div class="att-card-h"><h3>보낼 날짜가 아직 안 된 메시지</h3><span class="att-mcol-a"><${PickedButtons} ctx=${ctx} pick=${pick} /></span></div>
      <div class="att-mlog-b"><table class="att-mlog"><colgroup><col style="width:30px" /><col style="width:72px" /><col style="width:48px" /><col style="width:96px" /><col /></colgroup>
        <thead><tr><th></th><th>보낼 날짜</th><th>종류</th><th>대상</th><th>내용 미리보기</th></tr></thead>
        <tbody>${rows.map(row => { const who = row.channel === 'class' ? '학급 단톡방' : (D.byId.get(row.studentId)?.label || spaceLabel(row.studentLabel) || '—'); return html`<tr key=${row.id}><td><input type="checkbox" checked=${pick.has(row.id)} aria-label=${row.text} onChange=${() => pick.toggle(row.id)} /></td><td>${date(row.date)}</td><td>${row.channel === 'class' ? '단체톡' : '개인톡'}</td><td title=${who}>${who}</td><td title=${row.text}>${row.text}</td></tr>`; })}</tbody></table></div>
    </section>`;
  }
  function ChatSendDialog(p) {
    const ctx=p.ctx,d=p.d,[preview,setPreview]=useState(null),[error,setError]=useState(''),[running,setRunning]=useState(false),[result,setResult]=useState(null);
    const attempted=useRef(false);
    useEffect(()=>{
      let ended=false;
      ctx.call('/v1/data/chat/preview',{year:ctx.D.year,mode:d.mode||'queue',...(d.ids?{ids:d.ids}:{}),...(d.mode==='links'?{ids:d.studentIds||[],rotate:d.rotate===true}:{}),...(d.channel?{channel:d.channel}:{}),...(d.resendOf?{resendOf:d.resendOf}:{}),
        ...(d.personalText?{personalText:d.personalText}:{}),...(d.classText?{classText:d.classText}:{}),...(d.createGroup?{createGroup:true}:{}),...(d.mode==='greetings'&&d.links?{links:true}:{})}).then(reply=>{
        if(ended || memo.gen!==ctx.opts.generation)return;
        if(reply.status===200)setPreview(reply.body);else setError(chatError(reply));
      });
      return ()=>{ended=true;};
    },[]);
    const send=async()=>{
      if(attempted.current || running || !preview?.groups?.length || result)return;
      attempted.current=true;
      setRunning(true);
      try{
        ctx.set({dialogBusy:true});
        const reply=await ctx.call('/v1/data/chat/send',{previewToken:preview.previewToken});
        if(memo.gen!==ctx.opts.generation)return;
        if(reply.status===200)setResult(reply.body);else {setError(chatError(reply));setResult({results:[]});}
        await ctx.read();
      }finally{if(memo.gen===ctx.opts.generation){ctx.set({dialogBusy:false});setRunning(false);}}
    };
    const groups=preview?.groups||[];
    const links=d.mode==='links', afterLinks=groups.filter(x=>x.afterGreeting);
    const title=result?'보낸 결과':links&&d.rotate?`${d.label||'이'} 학생의 링크를 새로 만들까요?`:links?`학생 ${groups.length}명에게 결석 신고 링크를 보낼까요?`:d.resendOf?'다시 보낼까요?':d.mode==='reminders'?`학생 ${groups.length}명에게 미제출 서류 독려 개인톡을 보낼까요?`:'보낼까요?';
    return html`<h4 id="att-dlg-title">${title}</h4><${DlgNotice} ctx=${ctx}/><${ChatHealth} ctx=${ctx}/>
      ${links&&!result?html`${d.rotate?html`<p>지금 쓰는 주소는 바로 쓸 수 없게 돼요.</p>`:null}<p>보낸 메시지는 되돌릴 수 없어요.</p>`:null}
      ${error?html`<p class="att-warn">${error}</p>`:!preview?html`<p>확인 중…</p>`:null}
      ${preview && !groups.length && !preview.excluded?.length ? html`<p class="att-muted">0건</p>` : null}
      ${groups.filter(x=>!x.afterGreeting).map(x=>html`<section class="att-message"><b>${spaceLabel(x.target)}</b><pre class="att-message-text">${x.text}</pre></section>`)}
      ${afterLinks.length?html`<section class="att-message"><b>${'결석 신고 링크 · 학생 '+afterLinks.length+'명'}</b><pre class="att-message-text">${afterLinks[0].text}</pre></section>`:null}
      ${preview?.excluded?.length?html`<p class="att-warn">보내지 않는 줄${preview.excluded.map(x=>html`<span> · ${spaceLabel(x.target)||'—'}${x.reason==='email-missing'?' · 이메일 없음':x.reason==='class-missing'?' · 학급 단톡방 정하기 필요':x.reason==='greeting-rejected'?' · 1:1 인사를 받지 못함':x.reason==='link-send-pending'?' · 보내는 중이거나 결과 확인 필요':''}</span>`)}
        ${preview.excluded.some(x=>x.reason==='email-missing')?html`<button class="att-link" onClick=${()=>ctx.link({kind:'roster',focusEmail:true})}>학생명단 고치기</button>`:null}
        ${preview.excluded.some(x=>x.reason==='class-missing')?html`<button class="att-link" onClick=${()=>{ctx.closeDialog();ctx.link({ kind: 'attendance-connect' });}}>연결 → 출결 열기</button>`:null}</p>`:null}
      ${result&&d.mode==='greetings'?(result.results||[]).map(x=>{const r=greetResultLine(x);return html`<p>${x.label?spaceLabel(x.label)+' · ':''}${x.kind==='chat-link'?'결석 신고 링크':'인사'} <${Chip} tone=${r[0]}>${r[1]}</${Chip}></p>`;}):result?(result.results||[]).map(x=>html`<p>${x.code==='TEACHER_RATE_LIMIT'||x.code==='CHAT_RATE_LIMITED'?chatError({body:{error:x.code}}):x.phase==='CONFIRMED_RESULT'?'보냄':x.phase==='CONFIRMED_REJECTED'?'실패':x.phase==='released'?'보내지 못했어요. 다시 보낼 수 있어요.':links?'결과 확인 필요':'전달됐는지 모르는 메시지'}</p>`):null}
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running} label=${result?'닫기':'취소'}/>${!result?html`<${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (running?{text:'보내는 중'}:!groups.length?{text:error || (preview?'0건':'확인 중…')}:null)} onClick=${send}>${running?'보내는 중':'보내기'}</${Btn}>`:null}</${Foot}>`;
  }
  // One result line of the greeting flow: green only for a confirmed send; grey for a link held back
  // because its greeting was not confirmed; amber where the teacher has to act (C7: nothing resent).
  function greetResultLine(x) {
    if (x.code === 'TEACHER_RATE_LIMIT' || x.code === 'CHAT_RATE_LIMITED') return ['warn', chatError({ body: { error: x.code } })];
    if (x.phase === 'CONFIRMED_RESULT') return ['ok', '보냄'];
    if (x.phase === 'CONFIRMED_REJECTED') return ['warn', '보내지 못함'];
    if (x.phase === 'skipped') return ['muted', '인사가 확인되지 않아 보내지 않음'];
    if (x.phase === 'released') return ['muted', '보내지 못했어요. 다시 보낼 수 있어요.'];
    return ['warn', '결과 확인 필요'];
  }
  function ChatDecisionDialog(p) {
    const ctx=p.ctx,[answer,setAnswer]=useState(null),[error,setError]=useState(''),[running,setRunning]=useState(false);
    useEffect(()=>{let ended=false;ctx.call('/v1/data/read',{year:ctx.D.year}).then(r=>{if(!ended && memo.gen===ctx.opts.generation){if(r.status===200)setAnswer(r.body);else setError(chatError(r));}});return()=>{ended=true;};},[]);
    const effect=answer?.effects?.find(x=>x.id===p.d.effectId);
    const ready=effect && (effect.phase==='NEEDS_DECISION'||(effect.phase==='DISPATCHED_OR_UNKNOWN' && Date.parse(answer.serverNow)-Date.parse(effect.admittedAt)>=UNKNOWN_AFTER_MS));
    const decide=async choice=>{
      if(!ready||running)return;
      setRunning(true);
      try{
        ctx.set({dialogBusy:true});
        const versions=effect.targetRefs.map(ref=>{const parts=ref.split('/'), col=parts.at(-2),id=parts.at(-1);return (answer[col]||[]).find(x=>x.id===id||(col==='studentLinks'&&x.studentId===id))?.version;});
        const reply=await ctx.call('/v1/data/effects/'+effect.id+'/decide',{year:answer.year,choice,versions});
        if(memo.gen!==ctx.opts.generation)return;
        if(reply.status===200){ctx.closeDialogNow();await ctx.read();}else setError(chatError(reply));
      }finally{if(memo.gen===ctx.opts.generation){ctx.set({dialogBusy:false});setRunning(false);}}
    };
    return html`<h4 id="att-dlg-title">전달됐는지 모르는 메시지</h4><p>${effect?unknownLine({kind:effect.kind,label:effect.label,refs:effect.targetRefs,at:effect.admittedAt||effect.updatedAt},prepare(answer)):'확인 중…'}</p>
      ${error?html`<p class="att-warn">${error}</p>`:null}<${Foot}><${CancelBtn} ctx=${ctx} busy=${running}/>
      <${Btn} ctx=${ctx} cls="att-btn-quiet" why=${!ready||running?{text:'확인 중…'}:null} onClick=${()=>decide('sent')}>받았어요</${Btn}>
      <${Btn} ctx=${ctx} cls="att-btn" why=${!ready||running?{text:'확인 중…'}:null} onClick=${()=>decide('not-sent')}>${ctx.D.readOnly?'받지 못했어요':'받지 못했어요 · 다시 보낼 목록에 넣기'}</${Btn}></${Foot}>`;
  }
  function WelcomeDialog(p) {
    const ctx=p.ctx,[personal,setPersonal]=useState(''),[group,setGroup]=useState('');
    useEffect(()=>{let ended=false;ctx.call('/v1/data/greetings',{year:ctx.D.year}).then(r=>{if(!ended && r.status===200){setPersonal(r.body.defaults.personal);setGroup(r.body.defaults.class);}});return()=>{ended=true;};},[]);
    return html`<h4 id="att-dlg-title">환영 인사 보내면서 Google Chat 대화 상대로 연결하기</h4><p>학생들에게 첫 인사를 보내면 Google Chat으로 연락할 수 있어요.</p><${ChatHealth} ctx=${ctx}/>
      <div class="att-message-grid"><label class="att-fld">학생 각자에게 (1:1)<textarea rows="8" value=${personal} onInput=${ev=>setPersonal(ev.target.value)}/></label>
      <label class="att-fld">우리 반 단톡방<textarea rows="8" value=${group} onInput=${ev=>setGroup(ev.target.value)}/></label></div><p>학생명단의 이메일이 틀리면 그 학생에게는 가지 않아요.</p><p>인사와 함께 결석 신고 링크도 1:1로 보내요.</p>
      <${Foot}><${CancelBtn} ctx=${ctx}/><${Btn} ctx=${ctx} cls="att-btn" why=${!personal.trim()||!group.trim()?{text:'확인 중…'}:ctx.roWhy()} onClick=${()=>ctx.openDialog({kind:'chat-send',mode:'greetings',links:true,personalText:personal,classText:group,createGroup:!p.d.ids,...(p.d.ids?{ids:p.d.ids,channel:'personal'}:{})})}>미리보기</${Btn}></${Foot}>`;
  }
  function MessageEditDialog(p) {
    const ctx=p.ctx,d=p.d,row=d.row,[fields,setFields]=useState(()=>({channel:d.channel,date:row?.date||ctx.D.today,studentId:row?.studentId||'',type:row?.type||'기타',text:row?.text||''})),[running,setRunning]=useState(false);
    const set=(key,value)=>setFields(old=>({...old,[key]:value}));
    const lockKey='message:'+(row?.id||'new'), held=!!ctx.S.locks[lockKey], executing=useRef(false);
    const removeRows=d.remove?(d.rows||[row]):[];
    const removeAll=async()=>{if(executing.current||running||held)return;executing.current=true;setRunning(true);try{ctx.set({dialogBusy:true});for(const one of removeRows){const out=await ctx.runWrite({route:'/v1/data/messages',body:{action:'delete',id:one.id,version:one.version,fields},lockKey:'message:'+one.id});if(out.forgotten)return;if(out.kind!=='ok'){ctx.set({dlgNotice:ctx.whyOf(out)});await ctx.read();return;}}ctx.closeDialogNow();await ctx.read();}finally{if(memo.gen===ctx.opts.generation){ctx.set({dialogBusy:false});setRunning(false);executing.current=false;}}};
    const save=async()=>{if(executing.current||running||held)return;executing.current=true;setRunning(true);try{ctx.set({dialogBusy:true});const out=await ctx.runWrite({route:'/v1/data/messages',body:{action:d.remove?'delete':row?'edit':'add',...(row?{id:row.id,version:row.version}:{}),fields},lockKey});if(out.forgotten)return;if(out.kind==='ok'){ctx.closeDialogNow();await ctx.read();}else ctx.set({dlgNotice:ctx.whyOf(out)});}finally{if(memo.gen===ctx.opts.generation){ctx.set({dialogBusy:false});setRunning(false);executing.current=false;}}};
    return html`<h4 id="att-dlg-title">${d.remove?(removeRows.length>1?'메시지 '+removeRows.length+'개를 삭제할까요?':'이 메시지를 삭제할까요?'):row?'수정':d.channel==='personal'?'개인톡 추가':'단체톡 추가'}</h4><${DlgNotice} ctx=${ctx}/>
      ${d.remove?html`<div class="att-mdel">${removeRows.map(one=>html`<p key=${one.id}>${one.text}</p>`)}</div>`:html`<div class="att-fgrid"><label class="att-fld">보낼 날짜<input type="date" value=${fields.date} onInput=${e=>set('date',e.target.value)}/></label>
      ${d.channel==='personal'?html`<label class="att-fld">학생<select value=${fields.studentId} onChange=${e=>set('studentId',e.target.value)}><option value="">—</option>${ctx.D.students.map(x=>html`<option value=${x.id}>${x.label}</option>`)}</select></label>`:null}
      <label class="att-fld">종류<select value=${fields.type} onChange=${e=>set('type',e.target.value)}>${(d.channel==='personal'?['출결서류','준비물','개별안내','상담/확인','기타']:['준비물','제출물','일정','생활지도','기타']).map(x=>html`<option value=${x}>${x}</option>`)}</select></label></div>
      <label class="att-fld">내용<textarea rows="6" value=${fields.text} onInput=${e=>set('text',e.target.value)}/></label>`}
      <${Foot}><${CancelBtn} ctx=${ctx} busy=${running}/><${Btn} ctx=${ctx} cls="att-btn" why=${ctx.roWhy() || (held?{text:T.LOST_WRITE}:running?{text:'저장 중…'}:null)} onClick=${d.remove?removeAll:save}>${running?'저장 중…':d.remove?'삭제':'저장'}</${Btn}></${Foot}>`;
  }

  /* ---------- the public API ---------- */
  function mount(container, options) {
    const opts = Object.assign({}, options, { controller: {}, mountId: ++memo.mountSeq, generation: memo.gen });
    uiEvent(opts, 'mount');
    container.textContent = '';
    render(html`<${TopBoundary} opts=${opts}><${App} opts=${opts} /></${TopBoundary}>`, container);
    return { read() { opts.controller.current?.read?.(); }, canLeave() { return !opts.controller.current || opts.controller.current.canLeave(); }, hasDialog() { return !!opts.controller.current?.hasDialog(); }, unmount() { uiEvent(opts, 'unmount'); render(null, container); container.textContent = ''; } };
  }
  return { mount, homeSummary, forget };
 }
 const attendance = createAttendanceModule();
 const cards = {};
 const forgetWindow = attendance.forget;
 attendance.forget = () => { forgetWindow(); Object.values(cards).forEach(card => card.forget()); };
 attendance.mountCard = (container, options) => (cards[options.card] || (cards[options.card] = createAttendanceModule())).mount(container, options);
 window.TMAttendance = attendance;
})();

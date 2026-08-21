# 🚀 4. 실행_캘린더·Tasks등록 (gws CLI 실행)

## Google 계정 안전 확인

Calendar·Tasks·Sheet·Docs·출결·Google Chat처럼 Google 자료를 읽거나 바꾸기 전에 설치된 안전한 GWS 명령 파일로 현재 계정을 다시 확인한다. 정확한 `이름@goedu.kr` 주소가 아니거나 계정을 읽지 못하면 Google 자료 작업을 시작하지 않는다. 개인 Gmail을 선택해 계속하는 예외는 두지 않는다. 로그아웃과 `@goedu.kr` 계정으로 다시 로그인하는 일만 허용한다.

출결 Sheet는 선생님만 사용한다. 학생에게는 프로그램이나 출결 Sheet 편집 권한을 주지 않고 Google Chat 학급 단체톡방에만 초대한다. 출결 Sheet의 `설정` 탭에는 Chat 연결값이 있으므로 학생이나 믿을 수 없는 공동 편집자와 공유하지 않는다.

> 이 파일은 1~3단계를 거친 `TaskObject`를 받아  
> **실제로 어떤 캘린더 이벤트와 어떤 Tasks를 동시에 생성할지**를 결정하는 최종 실행 규칙이다.
>
> - 업무 캘린더 (work_calendar)
> - 학사일정 캘린더 (school_calendar)
> - Google Tasks – `담임 안내 Tasks 목록` (homeroom_task)
>
> ⚠ **v3.2 핵심 변화**
> - 예전처럼 “캘린더 vs Tasks 택1”이 아니다.
> - **캘린더(target_system)** 와 **담임 Tasks(need_homeroom_task)** 를 **완전히 분리**해서 본다.
>   - 예) work_calendar + Tasks 동시 생성 가능
>   - 예) split(학사+업무) + Tasks 3개 동시에 가능
>   - 예) 캘린더 없음(target_system=none) + Tasks만 생성도 가능

## [META_RULE] 출력 제한 및 실행 강제 (최우선 적용)

1. **분석 과정 비공개:** 1~3단계의 분석 결과(`TaskObject`, `scheduling_meta` 등)는 **내부 사고 과정(Chain of Thought)**으로만 수행하고, **절대 최종 답변(Response)에 텍스트로 출력하지 않는다.**
2. **보고 금지:** 사용자에게 "이렇게 등록하겠습니다"라고 계획을 브리핑하지 않는다.
3. **즉시 실행:** `actions` 리스트가 확정되는 즉시, **아무런 서론/본론 텍스트 없이** 오직 **gws CLI Bash 명령**을 실행한다.
4. **실행 후 보고:** 텍스트 응답은 반드시 **gws CLI 실행이 성공적으로 끝난 뒤에** "등록 완료했습니다" 한 마디만 남긴다.

---

## 0. 입력 / 출력 구조

### 0-1. 입력: 1~3단계까지 처리된 `TaskObject`

공통 스키마는 `1. quick_check_1min.md`의 [COMMON_SCHEMA]를 참조한다. 아래는 실행에 필요한 핵심 필드만 표시한다.

이 단계에 들어오는 `TaskObject`는 최소 다음 필드를 갖고 있다고 가정한다.

```json
{
  "업무명": "문장 한 줄 요약 또는 제목",
  "summary": "한 줄 요약",
  "raw_input": "원본 전체 텍스트",

  "due": "2025-12-05",                 // 마감일 (없을 수도 있음)
  "priority": "Critical|High|Medium|Low",
  "d_day": 2,                          // 오늘 기준 D-Day (없으면 null)

  "target_system": "work_calendar|school_calendar|split|none",
  "target_calendar": "업무 캘린더|학사일정|null",

  "need_homeroom_task": true,
  "target_task_list": "담임 안내 Tasks 목록",

  "scheduled_slots": [
    {
      "date": "2025-12-03",
      "period": 2,
      "start_time": "10:05",
      "end_time": "10:50"
    }
  ],
  "scheduling_meta": {
    "use_time_blocking": true,
    "week_scope": "this_week|next_week|none",
    "requires_weekend": false,
    "estimated_periods": 2,
    "reason": "시간 배치 근거 문장"
  },

  "school_schedule_info": {
    "start_date": "2025-12-10",
    "end_date": "2025-12-12",
    "all_day": true
  },

  "homeroom_message": "내일 체육복 꼭 입고 오기",
  "homeroom_due": "2025-12-03",
  "homeroom_reminder_time": "08:30"
}
```

> ⚠ `school_schedule_info`는 기간 표현이 있을 때 1단계에서 생성된 값을 사용한다.
> `homeroom_message`, `homeroom_due`, `homeroom_reminder_time`은 없으면 4단계에서 추출/추론한다.

---

### 0-2. 출력: 실제 실행용 액션(actions) 목록

이 파일의 최종 목적은 `TaskObject`에 아래와 같이 **실행 계획**을 붙이는 것이다.

```json
"actions": {
  "calendar_events": [
    {
      "kind": "calendar_event",
      "target": "work_calendar",
      "calendar_id": "profile.generated.json.calendars.work_calendar_id",
      "title": "[정산] 2학년 수련활동 저소득층 지원금 정산 🟠 High (2-3교시)",
      "description": "🟠 우선순위: High (마감: 12/05)\n\n2학년 수련활동 저소득층 지원금 정산\n\n✅ 처리 순서\n1. 영수증 확인\n2. 정산서 작성\n3. 회계실 제출",
      "start": "2025-12-03T10:05:00+09:00",
      "end": "2025-12-03T11:45:00+09:00"
    },
    {
      "kind": "calendar_event",
      "target": "school_calendar",
      "calendar_id": "profile.generated.json.calendars.school_calendar_id",
      "title": "2학년 수련활동 (10/30~10/31)",
      "description": "2학년 수련활동\n\n📅 구분: 학사일정\n기간: 2025-10-30 ~ 2025-10-31\n\n요약\n- 2학년 전체 수련활동 기간",
      "start": "2025-10-30",
      "end": "2025-11-01",
      "all_day": true
    }
  ],
  "tasks": [
    {
      "kind": "task",
      "target": "homeroom_task",
      "task_list": "profile.generated.json.calendars.homeroom_tasks_id",
      "title": "내일 수련활동 준비물 안내",
      "notes": "세면도구, 여벌 옷, 운동화 꼭 챙길 것."
    }
  ]
}
```

> Google Tasks 등록 요청에는 날짜·시간 값을 보내지 않는다 (SKILL.md 규칙, 2026-07-16).
> 마감은 제목의 `(마감: M/DD)` 표기로만 전한다. 원문 속 날짜·시간 문장은 notes에 그대로 남긴다.

* 이 `actions`를 gws CLI Bash 명령으로 순회하면서 실행:

  * `kind == "calendar_event"` → `gws calendar events insert --params ... --json ...`
  * `kind == "task"` → `gws tasks tasks insert --params ... --json ...`

---

## 1. 공통 텍스트 템플릿 (제목·설명)

### 1-1. 공통 우선순위 이모지

```text
Critical → 🔴
High     → 🟠
Medium   → 🟡
Low      → ⚪
```

### 1-1-1. 캘린더 출력 일관성

* 업무 캘린더 제목과 설명은 같은 우선순위 이름과 같은 이모지를 사용한다.
* 설명 첫 줄은 항상 `{우선순위이모지} 우선순위: {priority}`로 시작한다.
* 마감이나 D-day는 우선순위 첫 줄의 괄호 안에 함께 표시한다.
* 판단한 우선순위를 제목에는 넣고 설명에는 빼면 실패로 본다.
* 판단한 우선순위를 설명에는 넣고 제목에는 빼면 실패로 본다.

### 1-2. 업무 캘린더용 제목 포맷

```text
[{카테고리}] {업무명 또는 summary} {우선순위이모지} {priority} ({시작교시}-{종료교시}교시)
```

* 예:

  * `[정산] 2학년 수련활동 저소득층 지원금 정산 🟠 High (2-3교시)`
  * `[평가] 2학년 세계사 기말고사 문항 출제 및 검토 🟡 Medium (4-5교시)`

※ `{카테고리}`는 TaskObject에 별도 필드가 있으면 사용, 없으면 생략 가능.

### 1-3. 학사일정 캘린더용 제목 포맷

```text
{행사명 또는 학사정보} ({시작일~종료일})
```

* 예:

  * `2학년 수련활동 (10/30~10/31)`
  * `2학년 2학기 기말고사(지필평가) 기간 (12/10~12/12)`

※ 하루짜리면 `(~종료일)` 없이 `날짜`만 써도 무방.

### 1-4. 업무 캘린더용 설명 템플릿

```text
{우선순위이모지} 우선순위: {priority} ({D-day 또는 마감 설명})

{업무명 또는 summary}

마감: {due 또는 마감 설명}
예상 소요: {estimation.minutes}분 ({estimation.periods}교시)
배치 사유: {scheduling_meta.reason}

✅ 처리 순서
1. {Action Step 1}
2. {Action Step 2}
3. {Action Step 3}

⚠️ 확인
- {주의사항 1}
- {주의사항 2}

⏰ 시간 정보
- 배정 교시: {날짜} {시작교시}~{종료교시}교시
- 주차 설정: {scheduling_meta.week_scope} (주말 사용 여부: {scheduling_meta.requires_weekend})

📎 첨부파일
- {첨부파일명 1}
- {첨부파일명 2}

📁 처리 시각: {YYYY-MM-DD HH:MM} (정리완료 폴더 참조)
```

> **첨부파일 섹션 규칙:**
> - 원본 텍스트에 첨부파일명이 있으면 표시, **없으면 `📎 첨부파일` 섹션 자체를 생략**
> - 처리 시각은 **항상 표시** (KST 기준, 스킬 실행 시점의 now())
### 1-5. 학사일정 캘린더용 설명 템플릿

```text
{행사명 또는 학사 정보}

📅 구분: 학사일정
기간: {start_date} ~ {end_date}

요약
- {학사 일정의 목적/대상/주요 내용 한 줄}
```

### 1-6. 담임 Tasks용 제목/메모 템플릿

* 제목(title)

```text
[학생안내] {한 줄 안내 문장} (마감: {M/DD})
```

- 마감일(`homeroom_due`)이 있으면 항상 표시: `[학생안내] 체육복 입고 오기 (마감: 3/10)`
- 마감일이 없는 경우에만 생략: `[학생안내] 안내 문장`

* 메모(notes)

```text
{homeroom_message}

- 대상: 2학년 2반 학생
- 전달 시점: 조회/종례 또는 해당 수업 직전
- 비고: {필요 시 추가 메모}
```

---

## 2. 캘린더 이벤트 생성 규칙

> 이 섹션은 **캘린더 이벤트만** 다룬다.
> Tasks는 3번 섹션에서 별도로 처리한다.
> (즉, 여기서는 **target_system만 본다**)

### 2-1. 분기 구조 (핵심)

```pseudo
calendar_events = []

if (target_system == "work_calendar") {
  // 업무 캘린더만
  calendar_events += build_work_calendar_events(TaskObject)
}

if (target_system == "school_calendar") {
  // 학사일정 캘린더만
  calendar_events += build_school_calendar_events(TaskObject)
}

if (target_system == "split") {
  // 둘 다
  calendar_events += build_work_calendar_events(TaskObject)
  calendar_events += build_school_calendar_events(TaskObject)
}

// target_system == "none" 이면 캘린더 이벤트 생성하지 않음
```

> ⚠ 주의: **if / else if** 가 아니라 **if / if / if** 구조다.
> 즉, `split`일 때 실제로 **두 종류 이벤트를 모두 만든다.**

---

### 2-2. 업무 캘린더 이벤트 생성 함수

```pseudo
function build_work_calendar_events(TaskObject):

  events = []

  if (scheduling_meta.use_time_blocking == false) {
    // 시간을 블록으로 쓰지 않기로 한 업무면,
    // 필요 시 all-day 또는 due 기준 한 번짜리 이벤트로 만들 수도 있고,
    // 아예 만들지 않을 수도 있음 (정책에 따라 선택)
    return events
  }

  for each slot in scheduled_slots:
    // slot: { date, period, start_time, end_time }

    title = make_work_title(TaskObject, slot)
    description = make_work_description(TaskObject, slot)

    // ISO8601로 변환
    start = toKoreanISO(slot.date, slot.start_time)
    end   = toKoreanISO(slot.date, slot.end_time)

    event = {
      "kind": "calendar_event",
      "target": "work_calendar",
      "calendar_id": "profile.generated.json.calendars.work_calendar_id",
      "title": title,
      "description": description,
      "start": start,
      "end": end
    }

    events.push(event)

  return events
```

* `make_work_title`
  → 1-2에서 정의한 포맷 사용.
* `make_work_description`
  → 1-4 템플릿 사용.
  → 제목에 넣은 우선순위 이름과 이모지를 설명 첫 줄에도 같은 값으로 반복한다.
  → **첨부파일**: 원본 텍스트에서 파일 확장자 패턴(`.hwp`, `.pdf`, `.xlsx` 등)으로 첨부파일명 추출하여 포함. 없으면 해당 섹션 생략.
  → **처리 시각**: 스킬 실행 시점의 `now()` (KST) 를 `YYYY-MM-DD HH:MM` 형식으로 포함.

※ 연속 교시 묶기는 3번 파일(시간분석)에서 이미 처리했다고 가정하고,
여기서는 `slot`이 이미 “하나의 연속 구간(2~3교시)” 단위라고 취급해도 된다.

---

### 2-3. 학사일정 캘린더 이벤트 생성 함수

```pseudo
function build_school_calendar_events(TaskObject):

  events = []

  info = TaskObject.school_schedule_info
  if (!info) {
    // 학교일정 정보가 없다면 아무 것도 만들지 않음
    return events
  }

  start_date = info.start_date   // "YYYY-MM-DD" (포함)
  end_date   = info.end_date     // "YYYY-MM-DD" (포함)
  all_day    = info.all_day      // 기본 true
  end_exclusive = (all_day) ? addDays(end_date, 1) : end_date

  title = make_school_title(TaskObject, info)
  description = make_school_description(TaskObject, info)

  event = {
    "kind": "calendar_event",
    "target": "school_calendar",
    "calendar_id": "profile.generated.json.calendars.school_calendar_id",
    "title": title,
    "description": description,
    "start": start_date,
    "end": end_exclusive,
    "all_day": all_day
  }

  // 주말 포함 보정은 actions 생성 후 99번에서 처리하므로,
  // 여기서는 주어진 값 그대로 사용.
  events.push(event)

  return events
```

* `make_school_title`
  → 1-3 포맷 사용.
* `make_school_description`
  → 1-5 템플릿 사용.

---
## 2-4. Description 필드 생성 시 절대 규칙 (실제 줄바꿈)

캘린더 설명은 실제 줄바꿈을 사용한다.
줄바꿈 표시 글자가 그대로 보이면 실패로 본다.
굵은글씨 표시용 문법은 넣지 않는다. 캘린더 화면에서 그대로 보일 수 있다.
이모지는 제목과 구역 이름에 직접 넣는다.
원문 세부사항을 버리지 않는다. 장소, 대상, 주요 안건, 유의사항, 문의, 발신, 첨부파일이 있으면 설명칸에 각각 살려 쓴다.
짧은 요약문만 넣으면 실패로 본다. 제목은 짧게, 설명은 선생님이 바로 처리할 수 있을 만큼 충분히 적는다.

1. **줄바꿈은 실제 줄바꿈 사용**
   * 문단 사이는 빈 줄 1줄로 띄운다.
   * 목록은 한 줄에 한 항목씩 쓴다.

2. **구역 이름으로 가독성 확보**
   * `🟠 우선순위: High (마감: 오늘 퇴근 전)`, `✅ 처리 순서`, `⚠️ 확인`, `⏰ 시간 정보`, `📎 첨부파일`처럼 화면에서 바로 읽히는 구역 이름을 쓴다.
   * 굵게 보이게 하려고 별도 표시 문자를 넣지 않는다.

3. **적용 예시**

```text
🟠 우선순위: High (마감: 오늘 퇴근 전)

✅ 처리 순서
1. 창문 닫기
2. 전원 끄기

⚠️ 확인
- 마지막 퇴실자 확인
```
4. **원문 세부사항 보존**
   * 원문에 `일시`, `장소`, `대상`, `주요 안건`, `유의`, `문의`, `발신`, `첨부파일`에 해당하는 내용이 있으면 설명에서 생략하지 않는다.
   * 단순히 한 줄 요약과 마감만 넣지 않는다.
   * 첨부파일명은 파일 이름 그대로 남긴다.
---

## 3. 담임 Tasks(조종례시 안내사항) 생성 규칙

> 이 섹션은 **Tasks만** 다룬다.
> 캘린더와 완전히 별개의 라인이다.
>
> * `need_homeroom_task == true`이면 Tasks 생성
> * `false`면 Tasks 생성하지 않음
> * `target_system` 값과는 **독립**

### 3-1. 기본 분기 구조

```pseudo
tasks = []

if (need_homeroom_task == true) {
  tasks.push(build_homeroom_task(TaskObject))
}
```

---

### 3-2. 담임 Tasks 생성 함수

```pseudo
function build_homeroom_task(TaskObject):

  message = TaskObject.homeroom_message
  if (!message) {
    // 별도의 필드가 없으면 summary나 raw_input 중
    // "학생 안내용 한 줄"을 뽑아 사용 (단순 구현해도 됨)
    message = extractHomeroomMessageFromRaw(TaskObject.raw_input)
  }

  // Tasks에는 날짜·시간 값을 보내지 않는다 (SKILL.md 무날짜 규칙, 2026-07-16).
  // 마감일은 아래처럼 제목 표시용으로만 사용
  deadline = TaskObject.homeroom_due
  if (!deadline) {
    deadline = inferHomeroomDueDate(TaskObject)
  }

  // 제목에 마감일 포함 (마감일이 있을 때만)
  title_base = "[학생안내] " + shorten(message, 30)
  if (deadline) {
    title = title_base + " (마감: " + formatShortDate(deadline) + ")"
    // formatShortDate: "2025-12-04" → "12/04"
  } else {
    title = title_base
  }
  notes = make_homeroom_notes(message)

  task = {
    "kind": "task",
    "target": "homeroom_task",
    "task_list": "profile.generated.json.calendars.homeroom_tasks_id",
    "title": title,
    "notes": notes
  }

  return task
```

* `make_homeroom_notes(message)` 예시:

```text
{message}

- 대상: 담임 학급 학생
- 전달 시점: 조회/종례 또는 해당 수업 직전
```

---

## 4. actions 조립 및 TaskObject 최종 업데이트

### 4-1. 전체 조립 의사코드

```pseudo
function build_actions(TaskObject):

  calendar_events = []
  tasks = []

  // 1. 캘린더 이벤트들
  if (TaskObject.target_system == "work_calendar") {
    calendar_events += build_work_calendar_events(TaskObject)
  }

  if (TaskObject.target_system == "school_calendar") {
    calendar_events += build_school_calendar_events(TaskObject)
  }

  if (TaskObject.target_system == "split") {
    calendar_events += build_work_calendar_events(TaskObject)
    calendar_events += build_school_calendar_events(TaskObject)
  }

  // 2. 담임 Tasks
  if (TaskObject.need_homeroom_task == true) {
    tasks.push(build_homeroom_task(TaskObject))
  }

  // 3. TaskObject에 붙이기
  TaskObject.actions = {
    "calendar_events": calendar_events,
    "tasks": tasks
  }

  return TaskObject
```

> ⚠ **중요 포인트**
>
> * 캘린더 분기와 Tasks 분기는 완전히 독립이다.
> * 따라서 아래 조합들이 모두 가능하다.
>
>   * work_calendar만
>   * school_calendar만
>   * split(둘 다)
>   * none(캘린더 없음)
>   * 위 + `need_homeroom_task` true/false 조합
>   * 예: split + Tasks → 업무 캘린더 2개 + 학사일정 1개 + Tasks 1개 동시에 등록

---

## 5. 사람용 예시 정리

### 5-1. 예시 1 — work_calendar + Tasks 동시

> 텍스트:
> `2학년 세계사 기말고사 문항 출제 및 검토, 내일 종례 때 학생들에게 시험 범위 다시 안내하기`

* 2번 파일 결과 (핵심만):

```json
"target_system": "work_calendar",
"target_calendar": "업무 캘린더",
"need_homeroom_task": true,
"target_task_list": "담임 안내 Tasks 목록"
```

* 3번 파일 결과 (예):

```json
"scheduled_slots": [
  { "date": "2025-12-03", "period": 4, "start_time": "11:55", "end_time": "12:40" },
  { "date": "2025-12-03", "period": 5, "start_time": "13:30", "end_time": "14:15" }
]
```

* 4번 파일 실행 결과(actions):

```json
"actions": {
  "calendar_events": [
    {
      "kind": "calendar_event",
      "target": "work_calendar",
      "calendar_id": "profile.generated.json.calendars.work_calendar_id",
      "title": "[평가] 2학년 세계사 기말고사 문항 출제 및 검토 🟡 Medium (4-5교시)",
      "description": "🟡 우선순위: Medium (마감: 12/05)\n\n2학년 세계사 기말고사 문항 출제 및 검토\n\n예상 소요: 90분 (2교시)\n배치 사유: 기말고사 대비 문항 출제\n\n✅ 처리 순서\n1. 시험 범위 확인\n2. 문항 출제\n3. 검토 및 수정\n\n⚠️ 확인\n- 교육과정 성취기준 반영 확인\n\n⏰ 시간 정보\n- 배정 교시: 12/03 4~5교시\n- 주차 설정: this_week\n\n📎 첨부파일\n- 세계사_기말고사_출제범위.hwp\n\n📁 처리 시각: 2025-12-01 09:15 (정리완료 폴더 참조)",
      "start": "2025-12-03T11:55:00+09:00",
      "end": "2025-12-03T14:15:00+09:00"
    }
  ],
  "tasks": [
    {
      "kind": "task",
      "target": "homeroom_task",
      "task_list": "profile.generated.json.calendars.homeroom_tasks_id",
      "title": "[학생안내] 내일 시험 범위 안내 (마감: 12/02)"
    }
  ]
}
```

---

### 5-2. 예시 2 — split + Tasks 없음

> 텍스트:
> `2학년 수련활동 일정 안내 및 인솔교사 준비 사항`

* 2번 결과:

```json
"target_system": "split",
"need_homeroom_task": false
```

* 3번 결과:

```json
"scheduled_slots": [
  { "date": "2025-10-28", "period": 2, "start_time": "10:05", "end_time": "10:50" },
  { "date": "2025-10-29", "period": 3, "start_time": "11:00", "end_time": "11:45" }
],
"school_schedule_info": {
  "start_date": "2025-10-30",
  "end_date": "2025-11-01",
  "all_day": true
}
```

* 4번 결과(actions):

```json
"actions": {
  "calendar_events": [
    {
      "kind": "calendar_event",
      "target": "work_calendar",
      "calendar_id": "profile.generated.json.calendars.work_calendar_id",
      "title": "[수련활동] 인솔교사 준비 사항 정리 🟠 High (2-3교시)",
      "description": "🟠 우선순위: High (D-2)\n\n인솔교사 준비 사항 정리\n\n✅ 처리 순서\n1. 일정 확인\n2. 인솔 준비물 점검\n3. 학생 안내 필요 사항 확인\n\n⚠️ 확인\n- 실제 일정과 준비 사항이 분리되어 등록되는지 확인",
      "start": "2025-10-28T10:05:00+09:00",
      "end": "2025-10-29T11:45:00+09:00"
    },
    {
      "kind": "calendar_event",
      "target": "school_calendar",
      "calendar_id": "profile.generated.json.calendars.school_calendar_id",
      "title": "2학년 수련활동 (10/30~10/31)",
      "description": "2학년 수련활동\n\n📅 구분: 학사일정\n기간: 2025-10-30 ~ 2025-10-31\n\n요약\n- 2학년 수련활동 기간",
      "start": "2025-10-30",
      "end": "2025-11-01",
      "all_day": true
    }
  ],
  "tasks": []
}
```

---

### 5-3. 예시 3 — 캘린더 없음 + Tasks만

> 텍스트:
> `내일 체육복 꼭 입고 오기`

* 2번 결과:

```json
"target_system": "none",
"need_homeroom_task": true
```

* 3번 결과:

```json
"scheduled_slots": [],
"scheduling_meta": {
  "use_time_blocking": false,
  "week_scope": "none"
}
```

* 4번 결과(actions):

```json
"actions": {
  "calendar_events": [],
  "tasks": [
    {
      "kind": "task",
      "target": "homeroom_task",
      "task_list": "profile.generated.json.calendars.homeroom_tasks_id",
      "title": "[학생안내] 내일 체육복 꼭 입고 오기 (마감: 12/03)"
    }
  ]
}
```
---

## [CORE_RULES] 6. 최종 행동 프로토콜 (즉시 실행 원칙)

> **⚠️ 가장 중요한 규칙**
> "계획을 세웠으면, 사용자에게 묻지 말고 즉시 실행하라."

### 6-1. gws CLI 즉시 실행
- 4-1단계에서 `actions` 리스트가 완성되었다면, **그 즉시** 해당 내용으로 Teacher Manager가 설치한 안전한 GWS 명령 파일을 실행해야 한다.
- 사용자에게 "이렇게 등록할까요?"라고 묻거나 확인받지 않는다.
- **등록이 완료된 후**에 "등록했습니다"라고 보고한다.

### 6-3. gws CLI 실행 패턴
먼저 `$gws`를 공개 안내서의 고정 `teacher-manager-gws.cmd` 경로로 준비한 뒤, 각 action을 아래 형식으로 PowerShell에서 실행한다. PATH의 다른 GWS를 대신 쓰지 않는다.

```powershell
# calendar_event → gws calendar events insert
& $gws calendar events insert `
  --params '{"calendarId":"ACTION.calendar_id"}' `
  --json '{"summary":"ACTION.title","start":{...},"end":{...},"description":"ACTION.description"}'

# task → gws tasks tasks insert (무날짜 규칙: due·reminder를 보내지 않는다)
& $gws tasks tasks insert `
  --params '{"tasklist":"<homeroom_tasks_id>"}' `
  --json '{"title":"ACTION.title","notes":"ACTION.notes"}'
```

- 담임 Tasks를 만들었으면 같은 안내 문장을 `단체톡 내용`에도 저장한 뒤 완료 보고를 한다 (SKILL.md `Google Chat 쪽지 발송` 섹션의 append 절차, 중복 확인 포함).
- 선생님을 Google Sheet 메뉴로 다시 보내지 않는다.

- `--json` 내의 description은 실제 줄바꿈이 들어간 읽기용 문장으로 작성한다.
- 시간대는 `+09:00` (KST) 명시
- all-day 이벤트는 `{"date":"YYYY-MM-DD"}` 형식 사용

### 6-2. 예외 (묻는 경우)
- 오직 `TaskObject`의 필수 정보(날짜 등)가 누락되어 `99. exception_handling.md`의 "Interactive Query"가 발동된 경우에만 질문한다.
- 그 외에는 **100% 즉시 실행**을 원칙으로 한다.

## [HUMAN_NOTES] 한 줄 요약

```text
1. 이 파일은 TaskObject를 받아 "calendar_events[]"와 "tasks[]" 두 리스트를 만든다.
2. 캘린더는 target_system(work_calendar / school_calendar / split / none)을 기준으로,
   Tasks는 need_homeroom_task(true/false)를 기준으로 완전히 따로 결정한다.
3. 그래서 work + Tasks, split + Tasks, none + Tasks 같은 동시 조합이 모두 가능해진다.
4. gws CLI로 actions.calendar_events와 actions.tasks를 순회하면서
   각각 gws calendar events insert / gws tasks tasks insert 명령을 실행한다.
```

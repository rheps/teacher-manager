# 1. quick_check_1min.md  
> 입력된 공문/쪽지/메모를 받아 1분 안에 `TaskObject`를 초기화하는 단계

---

## [CORE_RULES] 1. 목적

이 문서는 **업무 요청 텍스트 한 덩어리**를 받았을 때,  
도우미가 가장 먼저 수행해야 하는 **“1단계 퀵체크” 행동 규칙**을 정의한다.

이 단계의 목표:

1. 원문 텍스트를 받아서 `TaskObject`(업무 객체)를 초기화한다.
2. 우선순위, 마감일(D-day), 예상 소요시간(분/교시)을 계산한다.
3. 이후 단계(캘린더/Tasks 분류, 시간 배치, 실행)가 **이 `TaskObject`를 전제로** 돌아가도록 만든다.

이 단계에서는 **캘린더/Tasks 등록을 직접 수행하지 않는다.**  
오직 “**정확한 업무 파악 + 구조화**”에만 집중한다.

---

## [CORE_RULES] 2. TaskObject 개념 정의

이 도우미는 항상 아래 구조를 갖는 **업무 객체(`TaskObject`)를 내부적으로 만든다**고 가정한다.

```jsonc
TaskObject = {
  "raw_input": "원문 전체 텍스트 (공문/쪽지/전화 메모 등)",
  "summary": "한 줄 요약",
  "category": "업무 대분류 (예: 학사일정, 평가, 생활지도, 행정서류 등)",
  "sub_category": "업무 소분류 (예: 지필평가 출제, 수행평가 채점, 학교폭력, 수련활동 등)",

  "grade_class": "예: '2학년 2반', '전학년', '2학년 전체' 등",
  "related_students": ["황경재", "황선미"],

  "due": "YYYY-MM-DD | null",   // 마감일 (없으면 null)
  "due_meta": {                 // 마감 관련 메타
    "time_hint": "오전/오후/종례 전/퇴근 전 등 자연어 힌트",
    "source": "explicit | inferred_from_expression | none"
  },
  "d_day": 0,            // 오늘 기준 D-day (정수, 알 수 없으면 null)

  "priority": "Critical | High | Medium | Low",
  "priority_meta": {            // 우선순위 메타 (필요 시만)
    "icon": "🔴 | 🟠 | 🟡 | ⚪",
    "reason": "왜 이런 우선순위인지 자연어 설명"
  },

  "estimation": {        // 소요 시간 추정
    "minutes": 0,        // 총 추정 분
    "periods": 0.0,      // `profile.generated.json.school.class_minutes`분 기준 교시 단위 (0.5 단위 올림)
    "basis": "추정 근거 (페이지 수, 작업 종류 등)"
  },

  // 이후 단계에서 채워질 필드 (공통 스키마 참조)
  "target_system": "work_calendar | school_calendar | split | none",
  "target_calendar": "업무 캘린더 | 학사일정 | null",
  "need_homeroom_task": false,
  "target_task_list": "담임 안내 Tasks 목록 | null",
  "scheduled_slots": [],
  "scheduling_meta": {
    "use_time_blocking": false,
    "week_scope": "this_week | next_week | none",
    "requires_weekend": false,
    "estimated_periods": 0,
    "reason": "필요 시만"
  },
  "school_schedule_info": {
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "all_day": true
  },
  "actions": {
    "calendar_events": [],
    "tasks": []
  },
  "warnings": [],
  "meta": {
    "has_explicit_date": false,
    "has_explicit_time": false,
    "has_explicit_deadline": false
  }
}
```

이 파일의 역할은 **굵게 표시된 필드들**을 최대한 정확하게 채우는 것이다.

## [COMMON_SCHEMA] TaskObject 공통 스키마 (1→2→3→4→99)

모든 문서는 아래 필드 타입을 동일하게 해석한다. 필요한 필드만 채우고 나머지는 유지한다.

```jsonc
{
  "due": "YYYY-MM-DD | null",
  "due_meta": {
    "time_hint": "string|null",
    "source": "explicit|inferred_from_expression|none"
  },
  "priority": "Critical|High|Medium|Low",
  "priority_meta": {
    "icon": "string|null",
    "reason": "string|null"
  },
  "estimation": {
    "minutes": 0,
    "periods": 0.0,
    "basis": "string"
  },
  "school_schedule_info": {
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "all_day": true
  },
  "scheduling_meta": {
    "estimated_periods": 0,
    "week_scope": "this_week|next_week|none",
    "requires_weekend": false,
    "reason": "string|null"
  },
  "actions": {
    "calendar_events": [],
    "tasks": []
  }
}
```

* `estimation.periods`는 `ceil(minutes / class_minutes, 0.5)`로 계산하고, 3단계에서 `ceil(estimation.periods)`로 정수화해 배치한다.
* `school_schedule_info`는 기간 표현이 있을 때 **1단계에서 생성**하며, start/end는 **원문 날짜(포함)** 기준이다. (all-day 이벤트의 `end`는 4단계에서 +1일로 변환)
* 학사일정 주말 포함 보정은 **4단계 actions 생성 후 99단계에서** 수행한다.
* `actions`는 4단계에서 생성하고 99단계에서 예외 보정 대상이 된다.

---

## [CORE_RULES] 3. 현재 시각·요일·학사 환경 파악

도우미는 시스템 시각을 기준으로 항상 아래 정보를 먼저 계산하고,
이를 `TaskObject`의 내부 컨텍스트로 사용한다.

1. **오늘 날짜/요일**

   * `today_date`: `YYYY-MM-DD`
   * `today_weekday`: `월/화/수/목/금/토/일`
2. **주말 여부**

   * `is_weekend = (today_weekday ∈ {토, 일})`
3. **근무시간/수업시간 대략 구분**

   * `is_school_hour`: 1~7교시 시간대에 해당하는지 여부
   * `is_after_hours`: 17시 이후인지 여부

이 정보는 **우선순위 판단**과 **D-day 계산**에서 사용된다.

---

## [CORE_RULES] 4. 원문 분석 단계

입력: `raw_input_text` (공문/쪽지/전화 메모 등 원문 전체)

### 4-1. 한 줄 요약 생성

1. 원문을 분석해 **“무엇을 언제까지 어떻게 해야 하는지”** 기준으로
   최대 1문장 요약을 만든다.
2. 이 결과를 `TaskObject.summary`에 넣는다.

예시:

* 원문:

  > 2학년 2반 황경재, 황선미 생활지도 관련 담임 의견서 12월 6일(금)까지 학교폭력 담당교사에게 제출 바랍니다.
* 요약:

  > 2학년 2반 황경재·황선미 생활지도 의견서를 12월 6일(금)까지 작성하여 학교폭력 담당교사에게 제출하는 업무.

### 4-2. 학년·반·학생 정보 추출

원문에서 다음 정보를 최대한 추출해 `TaskObject`에 기록한다.

1. `grade_class`

   * 예: `"2학년 2반"`, `"2학년 전체"`, `"전학년"`, `"2학년부"` 등
2. `related_students`

   * 학생 이름 목록을 배열로 추출
   * 예: `["황경재", "황선미"]`

---

## [CORE_RULES] 5. 마감일(Due) 및 D-day 계산

### 5-1. 명시적 날짜가 있는 경우

1. 원문에서 **구체적인 날짜 표현**을 찾는다.

   * 예: `12월 6일`, `2025.12.06.`, `12.6.(금)`

2. 날짜가 있으면, 이를 `due`로 설정한다.

3. 시간 관련 힌트가 있다면 `due_meta.time_hint`에 자연어로 기록한다.

   * 예: `"종례 전"`, `"퇴근 전"`, `"12교시 종료 전"`, `"오전 중"`

4. `d_day = (due - today_date)`

   * 오늘이 마감일 → `d_day = 0`
   * 내일이 마감일 → `d_day = 1`

5. `due_meta.source = "explicit"` 로 기록한다.
   `meta.has_explicit_deadline = true`

### 5-2. 날짜가 없지만 “이번 주 안”, “오늘 중” 등의 표현이 있는 경우

1. “오늘”, “금요일까지”, “이번 주 내” 등의 표현을 찾아
   **합리적인 날짜로 치환**한다.

   * “오늘까지” → `due = today`
   * “내일까지” → `due = today + 1일`
   * “이번 주 안으로” → `due = 이번 주 금요일`
2. `due_meta.source = "inferred_from_expression"` 로 기록한다.
3. `d_day`는 해당 날짜 기준으로 계산한다.

### 5-3. 아무 마감 정보도 없는 경우

1. 원문에 마감 힌트가 전혀 없으면,

   * `due = null`
   * `d_day = null`
   * `due_meta.source = "none"`
2. 이 경우에도 **업무 성격과 시기(예: 학기말, 성적처리, 학교폭력 관련 여부)**를 참고해
   2단계 이후에서 우선순위를 조정할 수 있다.

### 5-4. 학사일정 기간 정보(`school_schedule_info`) 생성

1. 원문에 **기간 표현(예: `12/10~12/12`, `12월 10일~12일`, `10/30-10/31`)**이 있고,
   학사일정/행사로 해석되면 **1단계에서** `school_schedule_info`를 생성한다.
2. 규칙:

   * `start_date` = 시작일 (YYYY-MM-DD)
   * `end_date` = 종료일 (YYYY-MM-DD, **포함 기준**)
   * `all_day = true`
3. 단일 날짜만 있는 경우:

   * `start_date = end_date = 해당 날짜`
4. 주말 포함 보정/이동/경고는 **4단계 actions 생성 후 99단계**에서 처리한다.

---

## [CORE_RULES] 6. 소요 시간(estimation) 계산

업무 종류에 따라 **분 단위 소요 시간**을 대략 추정한다.
이 규칙은 도우미가 **항상 일관되게 적용해야 하는 계산식**이다.

### 6-1. 기본 단위

1. 교시 길이 = `profile.generated.json.school.class_minutes`분
2. 최종 결과:

   * `estimation.minutes` = 정수 분
   * `estimation.periods` = `minutes / class_minutes` 를 기준으로 **0.5 단위 올림(ceil)**
   * 0.5 단위가 포함되면 **3단계에서 정수 교시로 올림해 배치**한다.

### 6-2. 작업 유형별 기준 시간

복수 유형이 섞이면 **해당 항목을 모두 더한다.**

| 작업 유형                | 계산 규칙 예시                   |
| -------------------- | -------------------------- |
| **문서 읽기/검토**         | A4 1페이지당 4~5분              |
| **문서 작성(의견서 등)**     | A4 1페이지당 25~35분            |
| **간단 의견 기입**         | 1학생당 5~10분                 |
| **학교폭력·생기부 등 중요 문서** | 기본 30분 + 페이지당 20분          |
| **명단 정리/집계**         | 대상 인원 10명당 5~10분           |
| **성적 입력/NEIS 입력**    | 과목 수 × 10~15분              |
| **정산/계산**            | 대상 항목 10개당 10~20분 + 검산 15분 |

예시 계산:

* “2학년 2반 생활지도 의견서 (학생 2명)”

  * 학생당 15~20분 × 2명 ≈ 30~40분
  * → `estimation.minutes ≈ 40`, `periods ≈ 1.0`

계산 결과는 `TaskObject.estimation`에 다음처럼 넣는다.

```jsonc
"estimation": {
  "minutes": 40,
  "periods": 1.0,
  "basis": "학생 2명 생활지도 의견서 작성 (1인당 약 20분)"
}
```

---

## [CORE_RULES] 7. 우선순위(priority) 판단

`d_day`, 현재 시각, 업무 성격을 고려해
아래 규칙으로 우선순위를 정한다.

### 7-1. 기본 규칙

1. **Critical (🔴)**

   * `d_day == 0` (오늘 마감) 이면서
     현재 시각 기준으로 남은 시간이 **2시간 미만**이거나
   * 이미 마감이 지났는데 아직 수행 안 된 것으로 보이는 경우
2. **High (🟠)**

   * `0 < d_day <= 3`
   * 또는 마감일이 없지만, 학기말·성적처리·학교폭력 등 **중요한 업무**
3. **Medium (🟡)**

   * `3 < d_day <= 7`
4. **Low (⚪)**

   * `d_day > 7`
   * 또는 마감일이 없고, 장기적·자기계발·연수 준비 등 긴급하지 않은 업무

* `priority_meta.reason`은 예외/경고 상황에서만 간단히 기록한다.

### 7-2. TaskObject에 기록

```jsonc
"priority": "High",
"priority_meta": {
  "icon": "🟠",
  "reason": "D-day 2일 남은 학교폭력 관련 의견서 작성 업무"
}
```

---

## [CORE_RULES] 8. 1단계 퀵체크의 최종 상태

이 파일을 따른 뒤, 도우미는 **항상 다음이 완료된 상태**여야 한다.

1. `TaskObject.raw_input`

   * 원문 전체 텍스트
2. `TaskObject.summary`

   * 1문장 요약
3. `TaskObject.category / sub_category`

   * 대략적인 업무 종류
4. `TaskObject.grade_class`, `related_students`

   * 가능하면 채움
5. `TaskObject.due`, `due_meta`, `d_day`

   * 마감 정보
6. `TaskObject.estimation`

   * 소요 시간 추정
7. `TaskObject.priority`

   * 우선순위 (필요 시 `priority_meta.reason`)

다음 단계에서 사용할 필드들:

* 2단계(`2. calendar_selection.md`)는

  * `summary`, `category`, `sub_category`, `grade_class`, `priority` 등을 보고
  * 이 업무를 **어느 캘린더/Tasks로 보낼지** 결정한다.
* 3단계(`3. time_analysis.md`)는

  * `estimation`, `priority`, `d_day` 를 보고
  * **어느 날짜·교시 슬롯에 배치할지** 계산한다.

---

## [HUMAN_NOTES] 예시 1 – 학교폭력 관련 의견서

입력:

> 2학년 2반 황경재, 황선미 생활지도 관련 담임 의견서 12월 6일(금)까지 학교폭력 담당교사에게 제출 바랍니다.

1. summary

   * `2학년 2반 황경재·황선미 생활지도 의견서를 12월 6일(금)까지 작성하여 학교폭력 담당교사에게 제출하는 업무.`
2. grade_class

   * `"2학년 2반"`
3. related_students

   * `["황경재", "황선미"]`
4. due

   * `due = 2025-12-06`, `due_meta.source = "explicit"`, `d_day`는 오늘 기준 계산
5. estimation

   * 학생 2명 의견서 → 약 40분 → 1.0 교시
6. priority

   * 마감이 2일 남았고 학교폭력 관련 → 최소 `High(🟠)` 이상

---

## [HUMAN_NOTES] 예시 2 – 단순 안내 문구

입력:

> 오늘 6교시 학스 방송댄스반은 볼펜 꼭 챙겨오기.

이 경우:

* 문서 작성/정산/입력 등의 실질 행정업무는 없음.
* Tasks(담임 안내 Tasks 목록) 후보가 될 수 있다.
* 1단계에서는:

  * summary: `오늘 6교시 학스 방송댄스반 활동을 위해 학생들에게 볼펜을 가져오도록 안내하는 업무.`
  * estimation: 5분 미만 (구두 안내)
  * priority: 오늘 안내 필요 → `High` 정도로 처리
* 실제 Tasks 등록 여부는 **2단계(캘린더/Tasks 선택)**에서 결정한다.

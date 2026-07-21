# ⏰ 3. 시간분석_배치전략 (v3.3 – work_calendar 전용)

> 이 파일은 1단계(퀵체크)·2단계(캘린더선택)를 거친 `TaskObject`를 받아  
> **업무 캘린더(work_calendar)에 필요한 시간 블록을 계산·배치**하는 규칙을 정의한다.

---

## 0. 이 파일이 다루는 것 / 다루지 않는 것

### 0-1. 이 파일이 **하는 일**

- `TaskObject`를 받아서:
  - **얼마나 시간이 필요한지** (`estimated_periods`)
  - **이번 주에 넣을지 / 다음 주로 넘길지**
  - **어느 날짜·몇 교시에 배치할지**
- 를 계산하고, 아래와 같이 결과를 기록한다.

```json
"scheduled_slots": [
  { "date": "YYYY-MM-DD", "period": 2, "start_time": "10:05", "end_time": "10:50" },
  { "date": "YYYY-MM-DD", "period": 3, "start_time": "11:00", "end_time": "11:45" }
],
"scheduling_meta": {
  "use_time_blocking": true,
  "week_scope": "this_week" | "next_week",
  "requires_weekend": false,
  "reason": "priority/d_day 기준 배치"
}
```

### 0-2. 이 파일이 **다루지 않는 것**

* **학사일정 캘린더**에 들어갈 “날짜 정보(기간, 행사명)”만 있는 일정의 시간 배치는 **하지 않는다.**

  * 학사일정은 원칙적으로 “날짜 단위 정보”이며,
    교시 단위 블록은 필요하지 않다고 본다.
* **담임 Tasks**의 알림 시각·마감일 결정은 **이 파일에서 하지 않는다.**

  * Tasks 관련 로직은 2번(캘린더·Tasks 목적지 결정)과 4번(실행_캘린더·Tasks등록)에서 처리한다.
  * 특히 `target_system = "none"` 이면서 `need_homeroom_task = true` 인 경우처럼
    **조회/종례 안내 전용 업무는 이 단계에서 아무 시간 블록도 만들지 않고 바로 종료**한다.

### 0-3. 핵심 전제 (v3.3 변경점)

* **시간 블록 배치는 “업무 캘린더(work_calendar)가 포함된 경우에만 수행”**한다.
* `need_homeroom_task` 여부와는 **무관**하다.

  * 즉, `work_calendar + Tasks 동시 등록`이든,
  * `Tasks만 있는 경우`든,
  * 이 파일은 **오로지 work_calendar 일정만** 보고 판단한다.

---

## 1. 입력: TaskObject에서 사용하는 필드

공통 스키마는 `1. quick_check_1min.md`의 [COMMON_SCHEMA]를 참조한다. 아래는 이 단계에 필요한 필드만 나열한다.
`time_flags`가 없으면 이 단계에서 시스템 시각으로 계산해도 된다.

이 파일이 기대하는 `TaskObject`의 최소 구조는 다음과 같다.

```json
{
  "업무명": "문장 한 줄 요약 또는 제목",
  "summary": "한 줄 요약",
  "due": "2025-12-05",             // 마감일 (없을 수도 있음)
  "priority": "Critical|High|Medium|Low",
  "d_day": 2,                      // 오늘 기준 D-Day (없으면 null)

  "estimation": {
    "minutes": 90,                 // 1단계에서 계산
    "periods": 2.0                 // `profile.generated.json.school.class_minutes`분 단위로 환산한 값
  },

  "target_system": "work_calendar|school_calendar|split|none",
  "target_calendar": "업무 캘린더|학사일정|null",

  "need_homeroom_task": true,      // 있어도 시간분석에는 영향 없음
  "time_flags": {
    "today": "2025-12-02",
    "weekday": "화",
    "isWeekend": false
  }
}
```

> ⚠ `target_system`과 `target_calendar`, `need_homeroom_task`는
> 2번 파일(캘린더선택_절대규칙)의 결과를 그대로 사용한다.

---

## 2. 출력: 시간 배치 결과 구조

### 2-1. 필수 출력 필드

```json
{
  "scheduled_slots": [
    // work_calendar가 포함될 때만 채운다.
    { "date": "YYYY-MM-DD", "period": 2, "start_time": "10:05", "end_time": "10:50" }
  ],
  "scheduling_meta": {
    "use_time_blocking": true | false,
    "week_scope": "this_week" | "next_week" | "none",
    "requires_weekend": true | false,
    "estimated_periods": 2,
    "reason": "필요 시만 간단히"
  }
}
```

* `scheduled_slots`

  * **업무 캘린더에 실제로 등록할 시간 블록** 목록.
  * `target_system`이 `work_calendar` 또는 `split`인 경우에만 채운다.
* `scheduling_meta.use_time_blocking`

  * `true`  → 실제 시간 블록을 배정했다.
  * `false` → 이 업무는 시간 블록을 따로 잡지 않는다.

    * 예: `target_system = "school_calendar"`이거나,
    * 단순 메모/아이디어처럼 캘린더 블록이 필요 없는 경우.

---

## 3. 시간 단위 및 기본 상수

### 3-1. 교시 시간표 (사용자 설정 기준)

* 실제 교시 시작·종료 시각은 `profile.generated.json.period_times`를 사용한다.
* 한 교시 길이는 `profile.generated.json.school.class_minutes` 값을 사용한다.
* 초등학교는 40분, 중학교는 45분, 고등학교는 50분으로 설정 파일에서 계산된다.
* 1단계에서 `estimation.minutes`를 기반으로
  `estimation.periods = ceil(minutes / class_minutes, 0.5단위)`로 환산했다고 가정한다.
* 0.5 단위가 포함되면 **이 단계에서 정수 교시로 올림해 배치**한다.

### 3-2. 요일별 공강 슬롯

* 실제 공강은 `profile.generated.json.free_periods`를 사용한다.
* `weekly-timetable.xlsx`에서 칸이 비어 있고, 그 요일의 마지막 교시 안에 있는 교시만 공강으로 본다.
* 점심시간, 조회, 종례 시간에는 업무 블록을 넣지 않는다.
* 같은 날에 공강이 여러 개 있으면 마감일에 가까운 날짜부터, 그날 안에서는 앞 교시부터 채운다.

---

## 4. 이 업무에 시간 블록을 쓸지 여부 결정

### 4-1. work_calendar 관련 여부 먼저 확인

```pseudo
if (target_system == "work_calendar" or target_system == "split") {
  // 업무 캘린더가 포함됨 → 시간 블록 배치 대상
} else {
  // "school_calendar" 또는 "none"
  // → 시간 블록 배치하지 않음
  //   (예: 학사일정만 있는 일정, 또는 Tasks 전용 안내)
  use_time_blocking = false
  week_scope = "none"
  scheduled_slots = []
  return
}
```

* `school_calendar` 단독이거나,
* `none`(캘린더 미사용)인 경우:

  * 이 파일은 **시간 블록을 만들지 않고 종료**한다.
  * 특히 `target_system = "none" && need_homeroom_task = true` 인
    **Tasks 전용 조회/종례 안내 업무**는 여기서 바로 반환한다.

### 4-2. 우선순위·D-Day 기반 주차 결정

`target_system`이 `work_calendar` 또는 `split`인 경우에만 아래 규칙 적용.

```pseudo
if (priority == "Critical") {
  // 오늘 마감 또는 D-Day=0인 경우 포함
  기본값: this_week
}
else if (priority == "High") {
  if (d_day <= 3) this_week else next_week
}
else if (priority == "Medium") {
  if (d_day <= 7) this_week else next_week
}
else { // Low
  기본값: next_week
}
```

예외:

* 오늘이 **목/금**인데,

  * priority가 Medium/Low이고,
  * d_day가 충분히 여유 있는 경우 → **next_week** 선호.

이 결정 결과를 `scheduling_meta.week_scope`에 기록.

---

## 5. 시간 블록 수 계산

```pseudo
needed_periods = ceil(estimation.periods)  // 0.5 포함 시 정수 교시로 올림

if (needed_periods <= 0) {
  use_time_blocking = false
  scheduled_slots = []
  return
}
```

* `needed_periods` 단위는 사용자 설정의 교시 길이(`profile.generated.json.school.class_minutes`)를 따른다.
* `scheduling_meta.estimated_periods`에는 `needed_periods`(정수)를 기록한다.

---

## 6. 구체적인 배치 알고리즘

### 6-1. 날짜 범위 설정

1. `base_week = scheduling_meta.week_scope`

   * `this_week`이면 오늘 ~ 이번 주 금요일
   * `next_week`이면 다음 주 월요일 ~ 금요일
2. `search_dates` 배열 구성

   * 우선순위:

     1. **마감일 직전날 → 현재 시점 방향으로 역산**
     2. 우선 평일(월~금)만 포함

   * 예:

     ```pseudo
     if (week_scope == "this_week") {
       search_dates = [금, 목, 수, 화, 월] (역순)
     }
     else { // next_week
       search_dates = [다음주 금, 목, 수, 화, 월]
     }
     ```

### 6-2. 날짜별 공강 슬롯 탐색

각 날짜에 대해 다음을 수행한다.

```pseudo
remaining_periods = needed_periods
scheduled_slots = []

for date in search_dates:

  if (remaining_periods <= 0) break

  // 1) 해당 날짜가 토/일이면 work_calendar에서도 기본적으로 건너뛴다.
  //    (필요 시 7장에서 주말 예외 처리)
  if (date_is_weekend(date)) continue

  free_periods = profile.generated.json.free_periods[date.weekday]

  for p in free_periods:

    if (remaining_periods <= 0) break

    if (period_is_already_booked(date, p)) continue

    allocate_period(date, p)
    remaining_periods -= 1.0
```

* `profile.generated.json.free_periods[weekday]`

  * 설정 파일과 주간 시간표에서 계산한 실제 공강 리스트를 반환.
* `period_is_already_booked(date, p)`

  * 이미 캘린더에 수업·회의 등으로 차 있는 교시인지 검사.
* `allocate_period(date, p)`

  * 해당 날짜·교시를 `scheduled_slots`에 추가.

### 6-3. 0.5 단위 처리 (고정 규칙)

* `estimation.periods`가 0.5 단위를 포함하더라도,
  배치용 `needed_periods`는 **정수 교시로 올림**한다.
* 따라서 이 단계에서는 **반 교시 배정은 하지 않는다.**

---

## 7. 주말 사용 예외 규칙 (work_calendar 전용)

> 이 섹션은 **업무 캘린더 work_calendar**에만 적용된다.
> 학사일정은 별도 규칙에 따라 **주말 금지**이며, 이 파일은 학사일정 시간 블록을 만들지 않는다.

### 7-1. 주말 사용 조건

다음 세 조건을 모두 만족할 때만 **주말 사용 후보**로 본다.

1. `priority`가 **Critical 또는 High**
2. 평일 `search_dates`를 다 돌았는데도 `remaining_periods > 0`
3. 업무 성격이

   * 개인 연수/자기계발/불가피한 행정에 가까운 경우
     (예: 연수 과제 제출, 절대 마감이 코앞인 행정 문서 등)

### 7-2. 주말 배치 로직

```pseudo
if (remaining_periods > 0 && (priority == "Critical" or priority == "High")) {

  weekend_dates = [이번주 토요일, 이번주 일요일] 또는 [다음주 토/일]

  for date in weekend_dates:
    if (remaining_periods <= 0) break

    preferred_periods = [5, 6, 7교시 시간대에 해당하는 오후 블록 등]

    for p in preferred_periods:
      if (remaining_periods <= 0) break
      if (period_is_already_booked(date, p)) continue

      allocate_period(date, p)
      remaining_periods -= 1.0
      scheduling_meta.requires_weekend = true
}
```

> 주말을 사용했다면
> `scheduling_meta.requires_weekend = true`로 명시적으로 기록한다.

---

## 8. 최종 정리 및 TaskObject 업데이트

### 8-1. 시간 블록을 배치하지 않는 경우

다음 중 하나에 해당하면:

* `target_system`이 `school_calendar` 또는 `none`인 경우
  (예: 학사일정만 있는 일정, 또는 Tasks 전용 조회/종례 안내)
* `estimation.periods <= 0`인 경우

```pseudo
TaskObject.scheduled_slots = []
TaskObject.scheduling_meta = {
  "use_time_blocking": false,
  "week_scope": "none",
  "requires_weekend": false,
  "estimated_periods": needed_periods,
  "reason": "캘린더 시간 블록 불필요"
}
```

### 8-2. 시간 블록을 성공적으로 배치한 경우

```pseudo
TaskObject.scheduled_slots = scheduled_slots
TaskObject.scheduling_meta = {
  "use_time_blocking": true,
  "week_scope": "this_week" or "next_week",
  "requires_weekend": (주말 사용 여부),
  "estimated_periods": needed_periods,
  "reason": "규칙 기반 배치"
}
```

### 8-3. 일부만 배치된 경우 (시간 부족)

* 평일·주말을 모두 썼는데도 `remaining_periods > 0`인 경우:

```pseudo
TaskObject.scheduled_slots = scheduled_slots
TaskObject.scheduling_meta = {
  "use_time_blocking": true,
  "week_scope": "this_week" or "next_week",
  "requires_weekend": (주말 사용 여부),
  "estimated_periods": needed_periods,
  "reason": "가능한 범위 내에서만 배치됨"
}
```

> 나머지 잔여 교시는
> 99번 파일(예외처리/충돌해결)에서
> “다음 주로 이월”, “추가 수동 조정 필요” 등의 정책으로 처리할 수 있다.

---

## [HUMAN_NOTES] 사람 눈으로 본 핵심 요약

```text
1. 이 파일은 오직 work_calendar(업무 캘린더)에 쓸 시간 블록만 계산한다.
2. target_system이 work_calendar 또는 split일 때만 시간 배치를 한다.
3. school_calendar만 있는 일정이나 Tasks만 필요한 안내(target_system="none" && need_homeroom_task=true)는 여기서 시간 블록을 만들지 않는다.
4. 필요 교시 수(정수화된 estimated_periods)를 기준으로 이번 주/다음 주의 공강 교시에 역산 배치한다.
5. 평일에 다 못 넣었을 때, Critical/High이면서 정말 급할 때만 주말 사용을 허용한다.
6. 최종 결과는 scheduled_slots + scheduling_meta로 TaskObject에 기록하고, 4번 파일이 이 정보를 이용해 실제 캘린더 이벤트를 만든다.
```

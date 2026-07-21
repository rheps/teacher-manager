# 🚨 99. 예외처리·충돌해결 (gws CLI 실행, 캘린더 + Tasks 동시 처리 전제)

> 이 파일은 1~4단계까지의 기본 로직이 **정상적으로 동작하지 못했을 때**  
> 또는 **경계 상황·충돌 상황**에서 어떻게 처리할지 정의하는 “마지막 방어선”이다.
>
> ⚠ v3.2 핵심 전제
> - **캘린더(target_system)** 과 **담임 Tasks(need_homeroom_task)** 는 **완전히 별개 축**이다.
> - 예외 상황에서도  
>   - **캘린더가 망가졌다고 Tasks까지 죽이면 안 된다.**
>   - **Tasks 생성에 문제가 생겨도 캘린더까지 롤백하면 안 된다.**
> - 이 파일의 목적은 **“최대한 많이, 안전하게, 분리해서” 살려내는 것**이다.

---

## 0. 입력 / 역할 범위

### 0-1. 이 파일이 받는 최소 정보

공통 스키마는 `1. quick_check_1min.md`의 [COMMON_SCHEMA]를 참조한다. 아래는 예외 처리에 필요한 핵심 필드만 표시한다.

- 1~4단계 처리 이후의 `TaskObject` (대략 구조)

```json
{
  "summary": "한 줄 요약",
  "priority": "Critical|High|Medium|Low",
  "d_day": 2,

  "target_system": "work_calendar|school_calendar|split|none",
  "target_calendar": "업무 캘린더|학사일정|null",
  "need_homeroom_task": true|false,

  "scheduled_slots": [ ... ],
  "scheduling_meta": {
    "use_time_blocking": true|false,
    "week_scope": "this_week|next_week|none",
    "requires_weekend": false,
    "estimated_periods": 2,
    "reason": "시간 배치 근거 문장"
  },

  "school_schedule_info": { ... },

  "actions": {
    "calendar_events": [ ... ],
    "tasks": [ ... ]
  }
}
```

> 예외처리는
>
> 1. `scheduled_slots` / `scheduling_meta` 단계에서의 실패,
> 2. `actions.calendar_events` / `actions.tasks` 구성 단계에서의 충돌,
> 3. 실제 API 호출(캘린더/Tasks) 실패를 다룬다.

### 0-2. 이 파일이 “하지 않는 것”

* 새로운 분류/캘린더 선택을 다시 하지 않는다.
  (1~2단계 로직을 다시 뒤집지 않음)
* 시간 배치를 “완전히 처음부터 재계산”하지 않는다.
  (3단계의 세부 알고리즘을 바꾸지 않음)
* 인간에게 물어보는 인터랙션까지 강요하지 않는다.
  → 대신 `TaskObject`에 **“문제 상황 메모”**를 남겨 상위 레이어에서 활용할 수 있게 한다.

---

## 1. 시간 슬롯 관련 예외 처리 (work_calendar 전용)

> 전제: 3번 파일(시간분석_배치전략 v3.2)은
> **work_calendar가 포함된 경우에만** `scheduled_slots`를 채운다.

### 1-1. 슬롯이 전혀 잡히지 않은 경우

조건:

```pseudo
(target_system == "work_calendar" or target_system == "split")
AND scheduling_meta.use_time_blocking == true
AND (scheduled_slots == [] or scheduled_slots 누락)
```

처리:

1. `scheduling_meta`를 보정한다.

```json
"scheduling_meta": {
  "use_time_blocking": false,
  "week_scope": "none",
  "requires_weekend": false,
  "estimated_periods": 기존 값 유지,
  "reason": "시간분석 단계에서 교시 배치 실패 → 시간 블록 없이 실행 필요"
}
```

2. **actions.calendar_events는 생성하지 않는다.**
3. 대신, 상위 레이어(또는 4번 파일)에서 사용할 수 있도록
   `TaskObject.warnings`에 메시지를 추가한다.

```json
"warnings": [
  "업무 캘린더에 필요한 시간 블록을 배치하지 못했음. 수동으로 시간 배치 필요."
]
```

> ✅ 원칙: **“이상하면 캘린더를 억지로 만들지 말고, 실패 사실을 남긴다.”**

---

### 1-2. 일부만 배치된 경우 (remaining_periods > 0)

3번 파일에서 계산 결과:

* `scheduling_meta.estimated_periods = 3`인데,
* 실제 `scheduled_slots`가 교시 기준 2만 채운 경우 등.

검출 규칙(간단 근사):

```pseudo
required_periods = scheduling_meta.estimated_periods
total_assigned_periods = sum(각 slot의 교시 수)
if (total_assigned_periods + 0.4 < required_periods) {
  // 0.5 오차 허용
  부분 배치로 간주
}
```

처리:

1. `scheduling_meta`에 잔여 정보 추가:

```json
"scheduling_meta": {
  ...,
  "partial_assigned": true,
  "assigned_periods": total_assigned_periods,
  "remaining_periods": required_periods - total_assigned_periods,
  "reason": "공강/주말 범위 내에서 최대한 배치했으나 모든 필요 교시를 채우지 못함."
}
```

2. **이미 배치된 `scheduled_slots`는 그대로 쓴다.**
   → 즉, 캘린더 이벤트는 가능한 만큼 생성.
3. `TaskObject.warnings`에 메시지 추가:

```json
"warnings": [
  "필요 교시 3 중 2교시만 캘린더에 배치됨. 나머지 시간은 수동 조정 필요."
]
```

> ✅ 원칙: “전부 못 넣었으니 아예 안 만든다(X)” →
> “**넣을 수 있는 만큼은 넣고, 부족하다는 사실을 명시한다(O)**”

---

## 2. 캘린더 vs Tasks 구조 충돌 처리

> v3.2에서 **가장 중요한 변경점**:
> **“캘린더 vs Tasks 택1” 구조가 아니라는 것**을 예외처리에서도 반드시 지킨다.

### 2-1. target_system과 need_homeroom_task는 독립

예외처리 단계에서 **절대 해서는 안 되는 일**:

```text
1. work_calendar 오류가 났다고 need_homeroom_task를 강제로 false로 바꾸지 말 것.
2. school_calendar 이벤트를 못 만든다고 해서 Tasks(actions.tasks)를 비우지 말 것.
3. Tasks 생성 중 오류가 났다고, 이미 만들어둔 calendar_events까지 삭제/롤백하지 말 것.
```

> ✅ 캘린더와 Tasks는 **논리적으로 다른 레이어**이며,
> 예외 처리에서도 **결과를 분리**하는 것이 원칙.

### 2-2. 동일 내용 중복 등록 방지 (필요 시)

경우:

* 같은 문장에서

  * work_calendar 이벤트
  * school_calendar 이벤트
  * homeroom_task
* 이 모두가 같은 표현을 제목에 쓸 수 있음.

필요할 경우:

* 제목이 완전히 동일하고,
* 같은 target(예: homeroom_task에서만 중복)일 때

```pseudo
actions.tasks 안에서 title이 완전히 같은 항목이 2개 이상이면
→ 하나만 남기고 나머지는 제거
```

* 단, **캘린더와 Tasks 사이의 제목 중복은 충돌이 아니다.**

  * 예:

    * work_calendar: `[평가] 기말고사 범위 재검토 🟡 Medium (4교시)`
    * homeroom_task: `[학생안내] 기말고사 범위 안내`
  * → 역할이 다르므로 중복으로 보지 않는다.

---

## 3. API 호출 실패에 대한 예외 처리

> 이 부분은 gws CLI 명령 실행 시 실패에 대한 정책이다.
> gws CLI가 반환하는 stderr/returncode를 기준으로 실패를 판단한다.

### 3-1. 기본 원칙

1. **리소스 단위로 독립 처리**

   * work_calendar, school_calendar, homeroom_task 각각에 대해
     “성공/실패”를 따로 기록.
2. **한 곳의 실패가 다른 곳의 성공을 무효화하지 않는다**

   * work_calendar 생성 성공 + school_calendar 실패 →
     **성공한 work_calendar는 그대로 유지.**
   * Tasks 생성 실패 + 캘린더 생성 성공 →
     **캘린더는 유지, Tasks만 재시도 대상.**

### 3-2. 실패 기록 포맷 (예시)

실제 코드에서 API 결과를 모아:

```json
"api_results": {
  "work_calendar": {
    "success": true,
    "failed_events": []
  },
  "school_calendar": {
    "success": false,
    "failed_events": [
      {
        "title": "2학년 수련활동 (10/30~10/31)",
        "error_code": 403,
        "error_message": "Forbidden"
      }
    ]
  },
  "homeroom_task": {
    "success": true,
    "failed_tasks": []
  }
}
```

그리고 `TaskObject.warnings`에 요약:

```json
"warnings": [
  "학사일정 캘린더(403 Forbidden)로 인해 일부 학사 일정 이벤트 생성 실패. 나머지 캘린더/Tasks는 정상 생성됨."
]
```

> ✅ 원칙: **“어디서 무엇이 실패했는지, 나머지는 어떻게 됐는지”**를
> 한눈에 알 수 있게 기록만 남겨주면 된다.

---

## 4. 학사일정·주말 관련 예외 처리

> 학사일정은 1단계에서 기간만 생성하고,
> 주말 포함 보정/이동/경고는 4단계 actions 생성 후 99단계에서 적용한다.

### 4-1. 학사일정이 주말에 걸려 있는 경우

검출:

```pseudo
for each e in actions.calendar_events where e.target == "school_calendar":

  if (e.all_day == true) {
    // 시작일~종료일 범위 안에 토/일이 포함되는지 검사
  } else {
    // start 또는 end가 토/일인 경우 검사
  }
```

처리 정책(간단 버전):

1. **단일 날짜 all-day 이벤트**이고, 그 날짜가 토/일이면:

   * 날짜를 가장 가까운 **이전 평일(보통 금요일)**로 이동.
   * `TaskObject.warnings`에 기록:

```json
"warnings": [
  "학사일정이 주말로 설정되어 있어 가장 가까운 이전 평일(금요일)로 자동 이동함."
]
```

2. 여러 날짜에 걸친 기간이라면:

   * 그대로 두되, **“실제 행사가 주말이더라도, 알림용 일정은 평일 시작일 기준으로 두는 것이 원칙”**이라는 노트를 description에 추가해도 된다.
   * 또는, 상위 레이어 정책에 따라 **수동 확인 필요**로만 표시.

> ✅ 이 부분은 “자동 수정”과 “수동 확인 요구” 중
> 선생님이 선호하는 쪽에 맞춰 조정하면 된다.
> v3.2 기본값은 **단일 날짜 주말 → 이전 평일로 이동**을 추천.

---

## 5. split / none 관련 예외

### 5-1. split인데 하나만 생성된 경우

`target_system == "split"`인데, actions를 만들고 나서:

* `calendar_events` 안에

  * work_calendar 이벤트는 있는데 school_calendar 이벤트가 없다, 또는 반대.

검출:

```pseudo
has_work = calendar_events.some(e => e.target == "work_calendar")
has_school = calendar_events.some(e => e.target == "school_calendar")

if (target_system == "split" && (!has_work || !has_school)) {
  // split 요구 조건 불충족
}
```

처리:

1. 어딘가에서 정보 부족으로 못 만든 것일 가능성이 크므로,
2. `TaskObject.warnings`에 남긴다.

```json
"warnings": [
  "target_system=split으로 분류되었으나 work_calendar 또는 school_calendar 중 한 쪽 이벤트가 생성되지 않았음. 원본 공문을 다시 확인할 것."
]
```

> ✅ 자동으로 다시 만들려 하기보다는,
> **“split인데 둘 중 하나가 누락됐다”는 사실만 명확히 표시**하는 쪽이 안전하다.

### 5-2. target_system == "none"인데 actions가 있는 경우

* 이론적으로는

  * `target_system == "none"`이면 캘린더 이벤트는 없어야 정상.
  * 단, Tasks(actions.tasks)는 있을 수 있다 (예: 안내만 필요한 경우).

검출:

```pseudo
if (target_system == "none") {
  if (calendar_events.length > 0) {
    // 이상 상태
  }
}
```

처리:

* `calendar_events`를 **모두 버리고** 경고를 남긴다.

```json
"actions": {
  "calendar_events": [],
  "tasks": [ ... ]
},
"warnings": [
  "target_system=none인데 calendar_events가 생성되어 있어 모두 제거함. Tasks만 유지."
]
```

> ✅ “none이면 캘린더는 만들지 않는다”는 원칙을
> 예외처리에서도 강제한다.

---

## 6. homeroom_task 관련 예외

### 6-1. need_homeroom_task == true인데 메시지가 없는 경우

```pseudo
if (need_homeroom_task == true && !homeroom_message && actions.tasks.length == 0) {
  // 담임 Tasks를 만들 수 없는 상태
}
```

처리:

1. `TaskObject.warnings`에 기록:

```json
"warnings": [
  "학생 안내용 Tasks가 필요하다고 판단되었으나, 안내 문장을 추출하지 못해 Tasks를 생성하지 못함."
]
```

2. **캘린더 이벤트는 그대로 둔다.**
   → 담임안내 Tasks만 실패로 처리.

### 6-2. Tasks API 실패 시

* 3-2의 API 결과 구조를 따른다고 하면:

```json
"api_results": {
  "homeroom_task": {
    "success": false,
    "failed_tasks": [
      { "title": "[학생안내] 내일 체육복 꼭 입고 오기", "error_code": 500 }
    ]
  }
}
```

* 이 경우에도 **캘린더 쪽은 절대 롤백하지 않는다.**

`TaskObject.warnings` 예시:

```json
"warnings": [
  "담임 안내 Tasks 생성 실패(500). 동일 내용을 조회/종례 시간에 수동으로 안내할 것."
]
```

---

## 7. [HUMAN_NOTES] 사람 기준 핵심 요약

```text
1. 시간 분석에서 교시를 다 못 채우면, 채운 만큼 캘린더에 넣고 "얼마나 부족한지"를 기록한다.
2. work_calendar / school_calendar / homeroom_task는 예외 상황에서도 서로 독립 처리한다.
   - 한쪽이 실패했다고 다른 쪽을 같이 없애지 않는다.
3. split인데 한쪽 캘린더만 생성된 경우, "이상 상태"라는 경고만 남기고 자동으로 억지 복구하지 않는다.
4. target_system=none이면, 캘린더 이벤트는 최종적으로 모두 제거하고 Tasks만 남긴다.
5. 학사일정이 주말에 걸리면, 단일 날짜라면 금요일로 당기거나 최소한 경고를 남긴다.
6. 담임 안내 Tasks를 만들 수 없는 상황(문장 추출 실패, API 에러 등)은
   "학생에게 말로 직접 안내하라"는 식의 경고만 남기고, 캘린더는 그대로 둔다.
7. 전체 원칙:
   - "죽이기보다는 최대한 살려두고"
   - "무엇이 실패했는지 TaskObject.warnings에 명확히 남긴다."
```

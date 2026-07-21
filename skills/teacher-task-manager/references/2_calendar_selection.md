# 📋 2. 캘린더선택_절대규칙 (v3.2 – Targets 확장)

> 이 파일은 1단계에서 만들어진 `TaskObject`를 받아  
> **어디에 무엇을 등록할지** 결정하는 규칙을 정의한다.
>
> - 업무 캘린더 (work_calendar)
> - 학사일정 캘린더 (school_calendar)
> - 담임 안내 Tasks (homeroom_task)
>
> ⚠️ **중요 변경점 (v3.2)**  
> - 예전처럼 `target_system`을 “하나만 고르는” 구조에서 벗어나  
>   **캘린더(target_system)**와 **담임 Tasks(need_homeroom_task)**를 **독립적으로** 결정한다.  
> - 즉, **캘린더 + Tasks 동시 등록 가능**이 기본 전제이다.

---

## [MCP_CORE] 캘린더·Tasks 대상 분류 규칙

---

## 0. 입력 / 출력 개요

### 0-1. 입력: `TaskObject` (1번 파일 결과)

예시 구조 (핵심 필드만):

> 공통 스키마는 `1. quick_check_1min.md`의 [COMMON_SCHEMA]를 참조한다. 아래는 핵심 필드만 표기.

```json
{
  "raw_input": "원본 전체 텍스트",
  "업무명": "문장 한 줄 요약 또는 제목",
  "summary": "한 줄 요약",
  "due": "2025-12-05",           // 마감일 (있을 수도, 없을 수도 있음)
  "priority": "High",            // Critical | High | Medium | Low
  "d_day": 2,

  "analysis": {
    "has_work_keywords": true,
    "has_schedule_keywords": false,
    "has_task_immediate_keywords": false,
    "has_task_student_keywords": false
  },

  "time_flags": {
    "isWeekend": false,
    "weekday": "화"
  }
}
```

> ⚠ `analysis.has_*`와 `time_flags`는 1번 단계에서 미리 계산해둘 수도 있고,
> 이 파일에서 직접 문자열 검색/시각 계산으로 계산해도 된다.

---

### 0-2. 출력: 캘린더/Tasks 대상 결정 결과

```json
{
  "target_system": "work_calendar" | "school_calendar" | "split" | "none",
  "target_calendar": "업무 캘린더" | "학사일정" | null,

  "need_homeroom_task": true | false,
  "target_task_list": "담임 안내 Tasks 목록" | null,

  "classification_notes": [
    "work_keyword=작성/정산 감지 → work_calendar 후보",
    "schedule_keyword=수련활동 감지 → school_calendar 후보",
    "학생 즉시 안내 표현 감지 → homeroom_task 필요"
  ]
}
```

* `target_system`

  * **캘린더 기준의 주 대상**만 표현한다.
  * `none`은 “캘린더는 필요 없고 Tasks만 필요한 경우”를 의미한다.
* `need_homeroom_task`

  * 담임 학급 안내용 Tasks(조종례시)를 **추가로 만들지 여부**.
  * 캘린더와 **독립**이다 → work_calendar + Tasks 동시 가능.

---

## 1. 상위 질문 흐름 (사람 기준)

사람 기준으로는 이렇게 생각하면 된다.

1. **캘린더부터 결정**

   * Q1. “이 일을 내가 직접 하는가?”
     → YES = 업무 캘린더
     → NO  = 학사일정(날짜 정보용) 또는 캘린더 없음
   * Q2. “날짜 자체가 학교 전체가 알아야 할 정보인가?”
     → YES = 학사일정 포함 (단독 또는 split)

2. **그 다음에 담임 Tasks 결정**

   * Q3. “이 내용 중 **2-2 학생들에게 오늘/내일 말로 바로 전달할 안내**가 있는가?”
     → YES = `need_homeroom_task = true`
     → NO  = false

> ⚠ **중요**
>
> * Q1·Q2 = **캘린더 후보 결정**
> * Q3 = **Tasks의 “추가 여부” 결정**
> * “Tasks냐 캘린더냐”를 택1하는 게 아니라,
>   “캘린더를 어느 쪽에 넣고, Tasks가 추가로 필요한가?”를 따로 본다.

---

## 2. 업무 캘린더 후보 규칙

> **질문:** "이 텍스트에 적힌 일이, 결과적으로 **내(교사)가 시간을 써서 수행해야 하는 일**인가?"

### 2-1. 기본 YES 조건

아래 중 하나라도 해당되면 **기본값 = 업무 캘린더(work_calendar)**

1. **내가 손을 쓰는 구체 작업**이 있는 경우
   * 문서 **읽기/검토/작성/수정**
   * **정산, 계산, 집계, 통계**
   * 회의 준비, 안건 정리, 회의록 작성
   * NEIS/나이스 입력, 성적 입력, 수행평가 기록
   * 명단 정리, 확인, 점검, 상담, 연락, 회신

2. 텍스트에 아래 **동사/명사 키워드** 포함
   * 동사/표현: 
     `준비`, `작성`, `검토`, `수정`, `보완`, `입력`, `등록`, `발송`, 
     `안내문`, `통신문`, `결재`, `품의`, `보고`, `정리`, `집계`, `산출`, 
     `계산`, `정산`, `처리`, `회의자료`, `회의록`, `성적`,
     **`설치`, `감독`, `지도`, `순시`, `배열`, `부착`, `가리기`**
   * 명사/구: 
     `명단 정리`, `점검`, `확인`, `상담`, `통화`, `연락`, `회신`, 
     `계획서`, `보고서`, `정산서`, `공지문`, `가정통신문`, 
     `NEIS 입력`, `수행평가 기록`, `설문 응답 정리`,
     **`고사장 설치`, `청소 감독`, `환경 미화`, `시설물 확인`, `안전 점검`, `대청소`**

### 2-2. 문맥적 업무 판단 (최우선 적용)

단순 키워드가 없더라도, 아래와 같은 **'행동의 뉘앙스'**가 읽히면 `work_calendar` 후보로 지정한다.

1. **실행 요구**: "검사해 주시고", "참관 바랍니다", "감독 필요", "참석 요망"
2. **확인/숙지**: 단순 공람이 아니라, 링크를 타고 들어가서 **무언가를 확인하고 후속 조치를 해야 하는 상황** (예: "명단 확인 후 연락", "유형 확인 후 지도")
3. **책임 소재**: 문맥상 주어가 '담임교사' 또는 '담당자'인 경우.
4. **[중요] 현장 감독 및 이동**: 
   * 책상 업무가 아니더라도, **교사가 교실/복도 등으로 이동하여 감독하거나 점검해야 하는 상황**이면 무조건 `work_calendar`로 분류한다.
   * 예: "청소 지도", "고사장 설치 확인", "소지품 검사", "급식 지도", "복도 점검"
   * **판단 기준:** "이 일을 처리하기 위해 내가 그 시간에 그 장소에 서 있어야 하는가?" → **YES면 캘린더 등록.**
5. **[중요] 특정 시점 명시**:
   * 텍스트에 **"종례 후", "점심시간에", "~교시에", "방과 후"** 등 **특정 시간대(Time Slot)**가 명시되어 있다면, 
   * 단순 확인 업무라도 그 시간을 점유하므로 **무조건 캘린더** 포함 대상으로 본다. (Tasks 단독 처리 금지)

### 2-3. 보조 키워드 매칭

아래 키워드는 판단을 돕는 힌트일 뿐, 절대적인 기준은 아니지만 **포함 시 캘린더 등록 확률을 높인다.**

* `검사`, `감독`, `실시`, `운영`, `지도`, `인솔`, `참관`
* `반장`, `부반장` 선거 관리, `청소` 구역 배정
* `배부`, `수합`, `걷기`, `나눠주기` (단, 수업 시간 내에 1분 만에 끝나는 단순 배부는 제외될 수 있으나, **별도 시간을 내야 하면 캘린더**)

---

## 3. 학사일정 캘린더 후보 규칙

> **질문:** “이건 **학교 전체가 공유해야 할 날짜/행사 정보**인가?”

### 3-1. 기본 YES 조건

아래가 **주어/핵심**이면 학사일정 후보 (`school_calendar_candidate = true`)

1. 학기·학사 관련

   * `개학`, `종업식`, `졸업식`, `방학 시작/종료`
   * `지필평가 기간`, `수행평가 기간`, `성적 처리일`, `성적 통지 예정일`

2. 행사

   * `수련활동`, `체험학습`, `체육 행사`, `진로 행사`, `학교 축제`
   * `학부모 총회`, `학부모 상담 주간`, `학부모 설명회`

3. 기타 날짜 정보

   * `재량휴업일`, `공휴일`, `학교장 재량 일정`

### 3-2. 주말 금지 규칙 (플래그 수준에서 기억)

* 학사일정 캘린더는 **월~금만 사용**.
* 토·일 날짜가 텍스트에 나오더라도:

  * 일단 `school_calendar_candidate = true`로 표시해 두고,
  * 주말 포함 보정/이동/경고는 **4단계 actions 생성 후 99단계**에서 처리한다.

---

## 4. 담임학급 Tasks(조종례시 안내사항) 후보 규칙

> **질문:** “이 내용 중, **2-2 학생들에게 오늘/내일 말로 바로 전달할 안내**가 있는가?”

### 4-1. Tasks가 다루는 것

* **당일/익일 즉시 안내**:

  * 조회/종례, 수업 직전에 **말로 바로 할 멘트**
* **학생 대상**:

  * 2학년 2반 학생들에게 직접 말할 내용
* **업무가 아닌 안내**:

  * 문서 작성, 결재, 정산이 아니라 **“말만 하면 끝나는 것”**
* **시간 블록이 필요 없음**:

  * 캘린더에 “내 시간”을 따로 잡을 필요가 없음

### 4-2. 자동 판별 조건 (기계용)

**아래 세 조건을 동시에 만족하면 `need_homeroom_task = true` 후보**

1. **즉시성/시점 키워드 (하나 이상 포함)**

   * `오늘`, `내일`, `지금`, `바로`, `꼭`, `잊지 말고`, `반드시`
   * `당일`, `즉시`, `준비물`, `가져오기`, `챙기기`

2. **학생/반 대상 키워드 (하나 이상 포함)**

   * `학생`, `여러분`, `반`, `2-2`, `2학년 2반`, `교실`, `수업`
   * `체육복`, `준비물`, `필기도구`, `볼펜`, `연필`

3. **업무 키워드 부재 (아래 단어가 없어야 함)**

   * `작성`, `기안`, `결재`, `보고`, `정산`, `입력`, `등록`, `계획`, `검토`, `정리`, `처리` …

> 👉 세 조건을 모두 만족하면
> **“이건 담임 종례 때 말로 안내해야 하는 내용이므로, Tasks 대상”**이다.

### 4-3. 사람 기준 예시

* ✅ `오늘 6교시 학스 방송댄스반 볼펜 챙기기`

  * 오늘/6교시/볼펜/학생 안내 → `need_homeroom_task = true`
* ✅ `내일 체육복 꼭 입고 오기`

  * 내일/체육복/학생 안내 → true
* ✅ `수련활동 준비물 안내 (세면도구, 여벌 옷, 운동화)`

  * 수련활동 전날 안내 멘트 → true
* ❌ `수련활동 준비물 안내문 작성`

  * “작성” 포함 → 업무 → work_calendar, Tasks 아님

---

## 5. 최종 타겟 결정 알고리즘

> 아래 로직은 **개념적 의사코드**이다.
> MCP 구현 시 언어에 맞게 그대로 옮기면 된다.

### 5-1. 캘린더 후보 플래그 계산

```pseudo
let work_candidate = false
let school_candidate = false

// 1) work_candidate
if (텍스트에 work 키워드 포함) {
  work_candidate = true
}
if ("내가 직접 해야 하는 구체 작업"으로 분석되면) {
  work_candidate = true
}

// 2) school_candidate
if (텍스트에 schedule 키워드 포함) {
  school_candidate = true
}
if ("날짜 자체가 정보"인 학사일정으로 판정되면) {
  school_candidate = true
}
```

### 5-2. 캘린더 `target_system` 결정 규칙

```pseudo
if (work_candidate && school_candidate) {
  // 예: "2학년 수련활동 일정 안내 및 인솔교사 준비 사항"
  target_system = "split"
  target_calendar = null   // 실제 캘린더 ID는 4번 파일에서 사용
}
else if (work_candidate && !school_candidate) {
  target_system = "work_calendar"
  target_calendar = "업무 캘린더"
}
else if (!work_candidate && school_candidate) {
  target_system = "school_calendar"
  target_calendar = "학사일정"
}
else {
  // 둘 다 아닌 경우:
  // - 순수 학생 안내만 있는 경우 (Tasks만 필요)
  // - 단순 메모/아이디어 등 캘린더 등록 불필요
  target_system = "none"
  target_calendar = null
}
```

> ⚠ `target_system = "none"`은
> **“이번 입력에서는 캘린더를 쓰지 않는다”**를 의미한다.
> 이 경우라도 **`need_homeroom_task`가 true일 수 있다.**

---

### 5-3. 담임 Tasks 플래그 결정 규칙

```pseudo
let need_homeroom_task = false
let target_task_list = null

if (텍스트에 즉시성 키워드 포함 &&
    텍스트에 학생/반 키워드 포함 &&
    텍스트에 업무 키워드(작성/정산/입력 등) 없음) {

  need_homeroom_task = true
  target_task_list = "담임 안내 Tasks 목록"
}
else {
  need_homeroom_task = false
  target_task_list = null
}
```

> ⚠ 이 결정은 `target_system`과 **독립**이다.
>
> * 예) work_calendar + Tasks 동시:
>
>   * “금요일 2교시 설문 조사 실시” + “목요일 종례 때 조사 있다고 미리 말하기”
> * 예) none + Tasks만:
>
>   * “내일 체육복 꼭 입고 오기” (캘린더 필요 없음, 안내만 필요)

---

### 5-4. 최종 출력 조립

```pseudo
TaskObject.target_system = target_system           // "work_calendar" | "school_calendar" | "split" | "none"
TaskObject.target_calendar = target_calendar       // "업무 캘린더" | "학사일정" | null

TaskObject.need_homeroom_task = need_homeroom_task
TaskObject.target_task_list = target_task_list     // "담임 안내 Tasks 목록" | null

TaskObject.classification_notes = [
  ... 판정 과정에서 생긴 근거 메모 문자열들 ...
]
```
### [CRITICAL_RULE] 시점 명시 시 캘린더 강제 규칙
* 입력 텍스트에 **"~시간에", "~교시에", "종례 후", "점심시간에"** 등 **교사가 움직여야 하는 특정 시점**이 명시되어 있다면,
* 해당 업무 내용이 사소해 보이더라도(단순 확인 등), **반드시 `work_calendar` (또는 split)를 포함**해야 한다.
* 이유: 그 시간에 교사의 몸이 묶이기 때문임.
* **절대 `target_system = "none"`으로 분류하지 말 것.**

---

## 6. 사람용 예시 요약

### 6-1. 예시 1 — work_calendar만

> `2학년 수련활동 저소득층 지원금 정산서 작성 및 회계실 제출`

* work 키워드:

  * `정산서`, `작성`, `제출` → **work_candidate = true**
* schedule 키워드:

  * 없음 → `school_candidate = false`
* 학생 즉시 안내:

  * 없음 → `need_homeroom_task = false`

**결과**

```json
"target_system": "work_calendar",
"target_calendar": "업무 캘린더",
"need_homeroom_task": false,
"target_task_list": null
```

---

### 6-2. 예시 2 — school_calendar만

> `2학년 2학기 기말고사(지필평가) 기간: 12/10~12/12`

* work 키워드:

  * 없음 → `work_candidate = false`
* schedule 키워드:

  * `기말고사`, `지필평가`, `기간` → `school_candidate = true`
* 학생 즉시 안내:

  * 이 문장 자체는 안내라기보다는 일정 정보 → false

**결과**

```json
"target_system": "school_calendar",
"target_calendar": "학사일정",
"need_homeroom_task": false,
"target_task_list": null
```

(※ 실제로 “조회 때 기말고사 일정 안내”가 필요하면,
그건 별도의 안내 문장/Task에서 다뤄야 한다.)

---

### 6-3. 예시 3 — split (학사일정 + 업무 캘린더)

> `2학년 수련활동 일정 안내 및 인솔교사 준비 사항`

* work 키워드:

  * `준비`, `안내문` 등 → `work_candidate = true`
* schedule 키워드:

  * `수련활동` → `school_candidate = true`
* 학생 즉시 안내:

  * 문장 자체에는 학생 직접 안내 표현이 없다고 가정 → false

**결과**

```json
"target_system": "split",
"target_calendar": null,
"need_homeroom_task": false,
"target_task_list": null
```

→ 이후 4번 파일에서

* school_calendar 이벤트: `2학년 수련활동 (10/30~10/31)`
* work_calendar 이벤트: `2학년 수련활동 안전계획서 작성 및 제출`, `인솔교사 준비물 점검`
  으로 분리 생성.

---

### 6-4. 예시 4 — Tasks만 (캘린더 없음)

> `내일 체육복 꼭 입고 오기`

* work 키워드:

  * 없음 → `work_candidate = false`
* schedule 키워드:

  * 없음 → `school_candidate = false`
* 학생 즉시 안내:

  * `내일`, `체육복`, (학생 대상 문맥) → 조건 3개 모두 충족 → `need_homeroom_task = true`

**결과**

```json
"target_system": "none",
"target_calendar": null,
"need_homeroom_task": true,
"target_task_list": "담임 안내 Tasks 목록"
```

→ 4번 파일에서 **캘린더 이벤트는 만들지 않고**,
Tasks에만 등록한다.

---

### 6-5. 예시 5 — work_calendar + Tasks 동시

> `금요일 2교시 2학년 2반 설문조사 실시, 목요일 종례 때 미리 안내`

(실제 입력이 이런 식으로 들어왔다고 가정)

* work 키워드:

  * `실시`, `설문조사` → `work_candidate = true`
* schedule 키워드:

  * 시험/행사 키워드로 볼 수도 있지만,
    “내가 2교시에 직접 하는 활동”으로 해석 → school_candidate = false (상황에 따라 조정)
* 학생 즉시 안내:

  * `종례`, `미리 안내` + 학생 대상 문맥 → `need_homeroom_task = true`

**결과**

```json
"target_system": "work_calendar",
"target_calendar": "업무 캘린더",
"need_homeroom_task": true,
"target_task_list": "담임 안내 Tasks 목록"
```

→ 4번 파일에서

* work_calendar: 금요일 2교시 설문조사 시간 블록
* Tasks: 목요일 종례용 “내일 2교시 설문조사 있다” 안내 Task
  를 **동시에 생성**하는 것이 정상 동작이 된다.

---

## [HUMAN_NOTES] 한 줄 요약

```text
1. 먼저 “이건 업무 캘린더냐, 학사일정이냐, 둘 다냐, 아니냐”를 보고 → target_system 결정.
2. 그 다음 “2-2 학생들에게 오늘/내일 말로 바로 안내할 게 있냐”를 보고 → need_homeroom_task 결정.
3. Tasks는 캘린더를 대체하는 게 아니라, 캘린더 위에 얹는 추가 안내 채널이다.
4. 그래서 work_calendar + Tasks, school_calendar + Tasks, split + Tasks, none + Tasks(Tasks만) 모두 가능하다.
```

---
title: "Flyway Invalid version: 1 디버깅 기록 — 인라인 주석과 보이지 않는 공백 2단 함정"
description: "Spring Boot에 Flyway baseline을 설정하다 같은 에러를 두 번 만난 디버깅 기록. 첫 cycle은 .properties의 인라인 주석 함정, 두 번째 cycle은 보이지 않는 trailing space 한 칸. cat -A 한 명령으로 가설을 사실로 바꿨던 과정"
date: 2026-05-09
tags: ["flyway", "spring boot", "properties"]
---

## 진행 사항

backend에 Flyway를 도입하면서 `V1__baseline.sql` 패턴을 설정하던 중이었습니다. `application.properties`의 baseline 설정 줄 옆에, *미래의 본인을 위해* 친절한 인라인 주석을 달았습니다.

```properties
spring.flyway.baseline-version=1   # 기존 DB는 V1 미실행, 빈 DB는 V1 실행 → 둘 다 V1 보존
```

배포 후 backend 부팅이 실패했습니다. 그리고 같은 `Invalid version: 1` 에러를 *두 번* 만났습니다. 첫 번째는 위 인라인 주석이 원인이었고, 그걸 풀고 나니 두 번째 cycle이 시작됐습니다. Flyway 자체의 원리는 [별도 글](/posts/flyway-migration-principles/)에서 다루고, 이 글은 그 설정을 들여다보면서 만난 두 함정에 대한 기록입니다.

## 발생한 문제

### Cycle 1 — 인라인 주석이 통째로 version 값으로 읽힘

```text
Caused by: org.flywaydb.core.api.FlywayException:
  Version may only contain 0..9 and . (dot). Invalid version: 1 # 기존 DB는 V1 미실행, 빈 DB는 V1 실행 → 둘 다 V1 보존
```

에러 메시지 안에 *주석으로 작성한 텍스트 전체*가 들어가 있었습니다. Flyway에 넘어간 `baseline-version`의 실제 값이 `1` 뿐이 아니라 `1 # 기존 DB는 V1 미실행, ...` 한 줄 전체였다는 의미였습니다.

### Cycle 2 — 인라인 주석을 분리한 후에도 같은 에러

`#` 이후의 주석 부분을 별도 줄로 분리하고 다시 배포했습니다. 그런데 부팅이 또 실패했습니다.

```text
Caused by: org.flywaydb.core.api.FlywayException:
  Version may only contain 0..9 and . (dot). Invalid version: 1
```

이번엔 메시지 끝이 깔끔하게 `1`로 끝나 보였습니다. *`1`이 왜 invalid라는 거지?* — 한참을 들여다봤습니다.

## 원인 분석

### 1. `.properties`의 인라인 주석 규칙 (Cycle 1)

Java `.properties` 파일에서 `#`과 `!`는 **줄 첫 글자일 때만 주석**으로 처리됩니다. 줄 중간에 등장하면 그 시점부터 끝까지가 *value의 일부*로 읽힙니다. YAML, Python, INI 등 다른 형식은 인라인 주석을 허용하지만 `.properties`는 그렇지 않습니다.

그래서 다음 줄은:

```properties
spring.flyway.baseline-version=1   # 기존 DB는 V1 미실행, ...
```

Spring/Flyway가 받는 실제 값이 이렇게 됩니다:

```text
baseline-version = "1   # 기존 DB는 V1 미실행, ..."
```

Flyway는 *"version에는 `0..9`와 `.`만 허용된다"*는 규칙을 가지고 있어서, 공백·`#`·한글이 섞인 이 문자열을 그대로 reject한 것이었습니다.

**1차 수정**:

```diff
- spring.flyway.baseline-version=1   # 기존 DB는 V1 미실행, 빈 DB는 V1 실행 → 둘 다 V1 보존
+ # baseline-version=1: V1을 baseline으로 마킹.
+ # 빈 DB → V1 SQL 실제 실행으로 스키마 생성.
+ # 이미 테이블 있는 DB → baseline-on-migrate으로 V1을 "이미 적용"으로 마킹.
+ spring.flyway.baseline-version=1
```

### 2. `cat -A` 한 명령으로 본 실제 byte (Cycle 2)

2차 에러는 메시지에 단서가 거의 없었습니다 (`Invalid version: 1`로 깔끔하게 끝나니까). 추측만으로 *"혹시 보이지 않는 공백?"* 같은 가설을 쌓는 단계에 들어가기 직전, `cat -A`로 파일의 실제 byte를 가시화해봤습니다.

```bash
$ grep -n "baseline" src/main/resources/application.properties | cat -A
49:spring.flyway.baseline-on-migrate=${FLYWAY_BASELINE_ON_MIGRATE:true}$
50:# baseline-version=1: V1을 baseline으로 마킹.$
53:spring.flyway.baseline-version=1 $    ← '1' 다음 공백 + $ (LF)
54:spring.flyway.baseline-description=baseline_schema$
```

`1` 다음에 스페이스 한 칸이 trailing으로 붙어 있었고, 그 뒤에 LF(`$`)가 이어졌습니다. 1차 수정 때 `# 기존 DB는 ...` 부분을 지우면서 *주석 앞의 공백 한 칸은 그대로 남긴* 것이었습니다.

### 3. 공통 원인 — Java `Properties.load()`의 trim 규칙

두 cycle 모두 같은 뿌리에 있었습니다. `Properties.load()`가 각 부분에 적용하는 trim 규칙이 비대칭이라는 점입니다.

| 위치 | trim 여부 |
| --- | --- |
| key 앞 leading whitespace | 트림함 |
| key와 value 사이의 `=` 주변 공백 | 트림함 |
| value의 **trailing whitespace** | **트림 안 함** ← 함정 |
| 줄 끝 `\` (line continuation) | 다음 줄도 value의 일부 |

`1 `의 trailing space는 `Properties.load()` 단계에서 제거되지 않고 Flyway까지 그대로 도달했습니다. Flyway는 `"공백은 0-9가 아니야"`로 reject한 것이었습니다.

## 해결 방법

### 1. 공백 한 칸 제거 + 전체 파일 trailing whitespace 일괄 정리

```diff
- spring.flyway.baseline-version=1·    # ← 보이지 않는 공백 한 칸
+ spring.flyway.baseline-version=1     # ← 공백 제거
```

같은 함정을 다른 줄에서도 만나지 않도록, 파일 전체의 trailing whitespace를 한 번에 정리해뒀습니다.

```bash
$ sed -i 's/[[:space:]]*$//' src/main/resources/application.properties
# 정규식: 줄 끝($) 직전의 모든 공백문자([[:space:]]*)를 제거
# tab, space 다 잡아내고 멱등 (여러 번 돌려도 같은 결과)
```

### 2. 적용 결과

- `Invalid version` 에러 해소
- Flyway가 정상적으로 baseline-version=1을 인식
- backend 부팅 성공, `flyway_schema_history` 정상 생성

## 새로 배운 점

- `.properties` 파일에서 `#`은 *줄 첫 글자에서만* 주석으로 동작하고, value 안의 trailing whitespace는 트림되지 않습니다. 두 규칙이 함께 작용해 인라인 주석을 적은 줄에서 *주석 뒤 텍스트* + *주석 앞 공백 한 칸* 두 종류의 함정이 따로따로 만들어집니다. 인라인 주석 자체를 쓰지 않는 게 가장 안전한 default입니다.
- 보이지 않는 문자를 추측으로 진단하면 가설이 가설로만 남습니다. `cat -A` 같은 *5글자짜리 가시화 명령*이 가설을 사실로 5초 만에 바꿔줍니다. 비슷한 도구로 `file`(인코딩), `od -c`(byte 단위), `xxd`(hex dump), `diff`(두 파일 비교)가 있고, 각자 다른 종류의 보이지 않는 문제를 보이게 만들어 줍니다.
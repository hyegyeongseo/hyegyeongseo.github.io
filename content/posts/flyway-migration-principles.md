---
title: "Flyway 동작 원리 정리 — flyway_schema_history와 baseline 패턴"
description: "Flyway의 데이터 모델과 baseline 패턴을 정리합니다. flyway_schema_history, checksum, migration 실행 흐름을 중심으로 설명합니다."
date: 2026-05-09
tags: ["flyway", "database", "migration", "spring boot"]
---

## 이 글을 쓰게 된 계기

backend 레포에 Flyway를 도입하면서 baseline 패턴을 설계하는 과정에서, Spring Boot 부팅 시 `Invalid version: 1`이라는 에러를 만났습니다. 표면적으로는 단순한 문법 오류처럼 보였지만, 실제로는 *Flyway의 데이터 모델과 baseline 패턴을 정확히 이해하지 못한 채 따라 쓰던* 결과였습니다. 사건 자체는 [별도 글](/posts/flyway-baseline-version-trailing-space/)에서 다루고, 이 글에서는 사건의 전제가 되는 Flyway의 핵심 동작 원리를 정리합니다.

## Flyway란

Flyway는 **데이터베이스 스키마의 git 같은 도구**입니다. 코드를 git으로 버전 관리하듯, DB 스키마 변경도 SQL 파일로 기록하고 순서대로 적용합니다.

한 줄로 정의하면: **SQL 파일들 + 적용 기록 테이블 = 재현 가능한 DB 상태**.

다시 말해 Flyway는 *SQL 실행 도구*가 아니라 *DB 상태를 고정하는 시스템*입니다. baseline, checksum, validate 같은 모든 동작이 이 관점에서 출발합니다.

## flyway_schema_history — 진실의 원천

Flyway의 모든 동작은 `flyway_schema_history` 테이블 하나가 결정합니다. *어떤 마이그레이션이 적용됐는지*, *다음에 무엇을 적용할지*, *기존 DB와 새 DB를 어떻게 다르게 처리할지* — 답이 모두 이 테이블에 있습니다.

| 컬럼 | 의미 |
| --- | --- |
| `installed_rank` | 적용 순서 |
| `version` | 마이그레이션 버전 (예: `1`, `2.1`) |
| `description` | 사람이 읽는 설명 |
| `type` | `SQL` 또는 `BASELINE` |
| `script` | 파일명 (예: `V1__init.sql`) |
| `checksum` | 파일 내용의 해시 |
| `installed_on` | 적용 시각 |
| `success` | 성공/실패 |

Spring Boot 부팅 시 Flyway는 **항상** 두 가지를 수행합니다.

1. `flyway_schema_history`를 읽어 *지금까지 적용된 마이그레이션 목록*을 확인
2. classpath의 마이그레이션 파일 목록과 비교해 *아직 적용되지 않은 것*만 순서대로 실행

## Migration 파일 구조 — V1__name.sql

마이그레이션 파일은 일정한 명명 규칙을 따릅니다. 예를 들어 이번 backend의 `src/main/resources/db/migration/` 경로에는 다음 파일들이 있습니다.

```
V1__baseline.sql
V2__add_image_blobs.sql
V3__search_logs_fk_set_null.sql
```

각 파일명은 다음 구조입니다.

```
V1__create_user_table.sql
│  │  └─ description (사람이 읽기 위해)
│  └─ 구분자 (밑줄 2개, 필수)
└─ prefix + version
```

- **Prefix**:
  - `V` (Versioned) — 한 번 적용되면 변경 불가
  - `R` (Repeatable) — checksum이 변경될 때마다 재실행
  - `U` (Undo) — 롤백 (유료 기능)
- **Version**: 정렬 가능한 숫자. `1`, `2.0`, `2.1.5` 등
- **Description**: 사람이 읽는 부분. 공백은 `_`로 표기

## Checksum — applied = immutable

Flyway는 각 마이그레이션 파일의 내용을 해시로 계산해 `checksum` 컬럼에 저장합니다. 부팅 시 파일과 DB의 checksum을 비교하고, **일치하지 않으면 부팅이 중단**됩니다 (`validate-on-migrate=true`일 때).

이 규칙의 의미는 *이미 한 번 적용된 마이그레이션 파일은 절대 수정할 수 없다*는 것입니다. 변경이 필요하면 새 버전(`V2__fix.sql`)을 추가해야 합니다. 이 immutability가 Flyway의 핵심 가치입니다 — 모든 환경에서 같은 순서, 같은 내용으로 적용되어야 재현 가능한 DB 상태가 보장됩니다.

## Baseline — 이미 존재하는 DB와의 만남

Flyway는 두 가지 상황을 모두 다뤄야 합니다.

| 시나리오 | 문제 |
| --- | --- |
| 빈 DB | `V1__init.sql`부터 차례로 적용. 문제 없음 |
| 이미 데이터가 있는 DB | `V1`이 정의하는 schema가 이미 존재. 그대로 적용하면 충돌 |

두 번째 경우를 위해 **baseline**이라는 개념이 있습니다. *"여기서부터 시작했다고 치자"* 는 의미로, 특정 버전까지를 *이미 적용된 것으로 표시*하고 그 다음부터 실제 실행을 시작하는 패턴입니다.

### baseline-on-migrate 옵션

```properties
spring.flyway.baseline-on-migrate=true
```

이 옵션이 `true`일 때, **`flyway_schema_history` 테이블이 없고 DB에 다른 테이블이 이미 존재하는 상황**에서 Flyway는 자동으로 baseline을 생성합니다. `flyway_schema_history`를 만들고 baseline 항목을 기록한 뒤, 그 다음 버전부터 적용을 시작합니다.

### baseline-version 옵션

```properties
spring.flyway.baseline-version=1
```

"V1까지는 baseline으로 잡고, V2부터 적용한다"는 의미입니다. baseline 항목의 version 값이 됩니다.

## V1__baseline.sql 패턴 — 빈 schema와 데이터 있는 schema에서 동작하는 방식

본 backend의 `application.properties`에 실제 적용한 Flyway 설정은 다음과 같습니다.

```properties
spring.flyway.enabled=${FLYWAY_ENABLED:true}
spring.flyway.locations=classpath:db/migration
spring.flyway.baseline-on-migrate=${FLYWAY_BASELINE_ON_MIGRATE:true}
spring.flyway.baseline-version=1
spring.flyway.baseline-description=baseline_schema
spring.flyway.validate-on-migrate=true
spring.flyway.out-of-order=false
```

그리고 `V1__baseline.sql`에는 *prod의 현재 schema 전체*를 담아둡니다 (`mysqldump --no-data`로 schema-only dump를 받은 뒤 `CREATE TABLE` 문을 정리해 넣는 패턴). 이 패턴이 우아한 이유는 **schema가 빈 상태든 데이터가 이미 있는 상태든 동일한 코드·설정으로 동작**한다는 것입니다.

| DB 상태 | Flyway의 동작 |
| --- | --- |
| 빈 schema (history 없음) | `flyway_schema_history` 생성 → `V1__baseline.sql`을 *실제 실행* → schema 생성 |
| 데이터 있는 schema (history 없음) | `flyway_schema_history` 생성 → `V1__baseline.sql`은 *baseline 항목으로 기록만* (실행 안 함) |

두 경우 모두 `V2__add_image_blobs.sql`부터는 동일하게 실행됩니다. 결과적으로 환경별 분기 처리 없이, 같은 코드가 두 상황을 자연스럽게 처리합니다.

## Flyway와 Hibernate `ddl-auto`의 환경별 분리

Flyway를 도입한 환경에서 Hibernate의 `ddl-auto` 설정은 함께 조정되어야 합니다. 본 backend에서는 다음과 같이 분리했습니다.

| 환경 | `JPA_DDL_AUTO` | 의미 |
| --- | --- | --- |
| dev | `update` (default) | Entity 변경 시 Hibernate가 자동으로 ALTER 수행. 빠른 개발 |
| prod | `validate` (ECS task env에서 override) | DDL은 Flyway만 담당. Hibernate는 entity ↔ 적용된 schema 일치 여부만 검증 |

prod에서 `validate`로 두면 *DDL 권한은 Flyway에게만 있고*, Hibernate는 *현재 entity 정의와 실제 schema가 어긋나지 않았는지*만 점검하는 안전장치 역할이 됩니다. Flyway 미적용 변경이 있으면 부팅 자체가 실패하므로, schema drift가 운영에 도달하지 못합니다.

## 운영 명령 — info, validate, repair

부팅이 안 되는 상황에서도 명령어로 직접 진단할 수 있습니다.

| 명령 | 용도 |
| --- | --- |
| `info` | 현재 상태 보기 (적용된 마이그레이션, 대기 중인 것, 실패한 것) |
| `validate` | 파일과 DB의 checksum 비교 |
| `repair` | history 테이블 정리 (실패 항목 제거, checksum 재계산) |
| `baseline` | 수동 baseline (`baseline-on-migrate` 안 쓸 때) |
| `migrate` | 명시적 적용 (Spring Boot 부팅 외에 수동 실행) |

`info`는 가장 자주 쓰는 진단 명령입니다. *지금 DB가 어디까지 와 있는지*를 한 번에 보여줍니다.

운영 환경에서는 Flyway 자체를 Spring Boot Actuator endpoint로 노출해두면 HTTP로도 상태를 확인할 수 있습니다.

```properties
management.endpoints.web.exposure.include=health,info,prometheus,flyway
```

이렇게 두면 `/actuator/flyway`로 적용된 마이그레이션 목록과 상태를 조회할 수 있습니다. 

참고로 Flyway migration이 부팅 중 실패하면 Spring Boot application context 초기화가 실패해 ECS task가 RUNNING 상태에 도달하지 못합니다. ALB target health check도 자연스럽게 unhealthy로 잡혀, 실패 자체가 운영 환경의 트래픽에 도달하지 못합니다.

## 정리

Flyway는 결국 *한 테이블*(`flyway_schema_history`), *한 invariant*(applied = immutable), *한 패턴*(baseline)이 전부입니다.

- `flyway_schema_history`가 진실의 원천
- 한 번 적용된 migration은 checksum이 변경되면 안 됨
- 기존 DB와 만나면 baseline으로 "여기서부터" 표시
- `V1__baseline.sql` 패턴은 빈 schema와 데이터 있는 schema 양쪽에서 동일 코드로 동작
- Hibernate `ddl-auto=validate`와 결합하면 schema drift를 부팅 단계에서 차단

이 기본 모델만 이해하면, baseline 패턴이나 migration 충돌 같은 상황도 표면 에러 너머의 원리로 풀어볼 수 있습니다.
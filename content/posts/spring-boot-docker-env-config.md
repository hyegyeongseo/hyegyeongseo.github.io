---
title: "Spring Boot를 도커 컨테이너로 실행 시 민감 정보 외부화"
description: "민감정보를 application.properties 하드코딩 시 발생하는 문제 기록 및 해결"
date: 2026-04-26
tags: ["spring boot", "docker"]
---

## 진행 사항
Spring Boot를 Docker Compose 환경에서 Docker 컨테이너로 실행했습니다.

## 발생한 문제
GitHub에서 프로젝트를 Clone한 뒤 Docker Compose로 실행했을 때 컨테이너가 즉시 종료되는 문제가 발생했습니다.

application.properties가 포함되지 않은 상태로 컨테이너를 실행하면서 발생한 문제였습니다. application.properties에 민감정보를 다시 하드코딩하면 동작은 하지만, Git에 민감정보가 노출되는 문제가 있었습니다.

## 테스트 사항
> **1. application.properties ❌**
> → 빌드 성공 / 컨테이너 실행 실패
>
> **2. application.properties ❌ + .env ⭕**
> → 빌드 성공 / 컨테이너 실행 실패
> → .env의 일부 환경변수는 자동 매핑되었지만, 애플리케이션 기동에 필요한 필수 설정값이 모두 제공되지 않아 정상적으로 실행되지 않음
>
> **3. application.properties ⭕ + .env ⭕**
> → 빌드 성공 / 컨테이너 실행 성공
> → 로그인 기능 정상 동작

## 해결 방법
### 4-1. .env 파일 설정

.env에 민감정보를 작성하고, docker-compose가 이를 컨테이너 환경변수로 전달하도록 설정합니다.

.env 예시
```dotenv
SPRING_DATASOURCE_PASSWORD=XXXX
```
### 4-2. docker-compose.yml에서 환경변수 참조
docker-compose.yml 예시
```yaml
env_file:
  - .env
```

### 4-3. Spring Boot에서 환경변수 참조

application.properties 예시
```properties
spring.datasource.password=${SPRING_DATASOURCE_PASSWORD}
```

### 4-4. .gitignore 수정
- application.properties는 Git에 포함하고, 민감정보는 환경변수(.env)로 분리합니다.
- .env 파일은 민감 정보를 포함하므로 .gitignore에 추가하여 Git에 포함되지 않도록 합니다.

### 4-5. 핵심 정리
- 민감정보는 .env 한 곳에서만 관리하고, application.properties와 docker-compose.yml은 이를 참조만 합니다.
- 같은 값을 여러 파일에 하드코딩하지 않으므로, 비밀번호 변경 시 .env만 수정하면 됩니다.


## 새로 배운 점
- docker-compose와 application.properties는 환경변수를 통해 동일한 설정 구조로 통합 관리할 수 있습니다.
- 환경변수 기반 구조로 변경하면 로컬 / CI / 운영 환경을 동일한 방식으로 구성할 수 있습니다.
- 환경별로 민감정보 관리 방식은 다음과 같이 구분할 수 있습니다.

| 환경 | 비밀 관리 방식 |
| --- | --- |
| 로컬 개발 | `.env` |
| CI/CD (GitHub Actions) | GitHub Secrets |
| 운영(Production) | Secret Manager (AWS/GCP/Vault) |
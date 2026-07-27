---
title: "Spring Boot actuator와 ALB health check를 함께 맞춰야 했던 이유"
description: "ALB health check가 Spring Security에 의해 401로 실패했던 문제를 actuator permitAll 설정으로 해결한 과정"
date: 2026-05-09
tags: ["aws", "alb", "spring boot", "spring security", "actuator", "health check"]
categories: ["Troubleshooting"]
---

## 진행 사항

ECS 위에 띄운 Spring Boot backend를 ALB target group에 등록하고, `/actuator/health`를 health check path로 설정해 운영하는 구조였습니다.

## 발생한 문제

컨테이너는 정상적으로 RUNNING이었고, CloudWatch 로그에도 Spring Boot의 부팅 완료 로그(`Tomcat started on port(s)`, `Started ... in N.NNN seconds`)가 정상적으로 찍혀 있었습니다. JVM은 정상적으로 실행 중이었지만, ALB target group health check는 계속 실패하며 unhealthy 상태로 유지되고 있었습니다.

인프라가 application에 보내는 health check 요청이 어딘가에서 막혀 있었습니다.

## 원인 분석

### ALB가 받은 응답 확인

application이 살아있는데 ALB만 unhealthy라면, ALB가 application에 보낸 요청이 *실제로 어떤 응답을 받고 있는지*를 먼저 확인해야 했습니다.

```bash
$ aws elbv2 describe-target-health \
    --target-group-arn "$TG_ARN" \
    --query "TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason,TargetHealth.Description]"
```

처음 받은 응답은 **401**이었습니다.

### 응답 코드별 의미

ALB health check 실패 응답 코드를 어떤 계층에서 요청이 실패했는지에 대한 신호로 해석했습니다.

| 응답 코드 | 의미 (해석) | 의심할 계층 |
| --- | --- | --- |
| 401 | 인증 필터에서 요청이 거부됨 | Spring Security / Authentication layer |
| 404 | 해당 경로가 애플리케이션에 존재하지 않음 | Routing / Actuator exposure 설정 |
| 503 | 애플리케이션의 Health 상태가 DOWN 또는 OUT_OF_SERVICE로 평가됨 | Application dependency (DB, Redis 등) / HealthIndicator |

ALB health check는 인증 정보 없이 호출되기 때문에,
해당 경로는 인증 없이 접근이 가능하도록 열려 있어야 합니다.

### SecurityConfig의 permitAll 점검

SecurityConfig를 보니 actuator 경로가 빠져 있었습니다.

```java
.requestMatchers(
    "/", "/login/**", "/oauth2/**",
    "/swagger-ui/**", "/swagger-ui.html",
    "/v3/api-docs/**"
).permitAll()
```

ALB가 호출하는 `/actuator/health`가 이 목록에 없으니 인증이 요구되어 401이 반환된 것이었습니다.

### 세 곳의 설정이 반드시 정렬되어야 하는 이유

ALB health check는 단순히 “경로 하나를 호출하는 구조”가 아니라,
여러 계층이 서로 다른 책임을 나눠 갖고 있는 구조입니다.

이 때문에 하나의 요청이 정상 처리되기 위해서는 다음 3가지가 모두 일관되게 설정되어야 합니다.

| 위치 | 설정 항목 | 역할 |
| --- | --- | --- |
| ALB target group | `health_check.path = "/actuator/health"` | ALB가 호출하는 경로 |
| Spring Boot Actuator | `management.endpoints.web.exposure.include = health` | 해당 endpoint를 외부에 노출 |
| Spring Security | `permitAll("/actuator/health")` | 인증 없이 접근 허용 |

이 세 요소는 각각 다른 계층의 책임이기 때문에,
하나라도 어긋나면 계층별로 다른 방식의 실패가 발생합니다.

Spring Boot 공식 문서는 이 동작을 명시적으로 설명합니다:

> If you define a custom SecurityFilterChain bean, Spring Boot auto-configuration backs off and lets you fully control the actuator access rules.
>
> — [Spring Boot Reference - Actuator Endpoints](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)

즉 SecurityFilterChain을 직접 정의한 순간,
Spring Boot가 제공하던 기본 actuator 보안 설정은 더 이상 적용되지 않고,
모든 접근 제어는 애플리케이션 레벨에서 직접 관리해야 합니다.

이번 케이스는 Spring Boot auto-configuration이 제공하던 기본 보안 설정이 끊기면서, actuator 접근 제어가 전적으로 애플리케이션 책임으로 넘어간 경계에서 발생한 문제였습니다.

## 해결 방법

### SecurityConfig의 permitAll에 actuator 경로 추가

```diff
.requestMatchers(
    "/", "/login/**", "/oauth2/**",
    "/swagger-ui/**", "/swagger-ui.html",
-   "/v3/api-docs/**"
+   "/v3/api-docs/**",
+   "/actuator/health",
+   "/actuator/health/**",
+   "/actuator/info",
+   "/actuator/prometheus"
).permitAll()
```

`/actuator/health` 외에 `/actuator/health/**`도 함께 등록한 것은, Spring Boot Actuator가 `/actuator/health/liveness`나 `/actuator/health/readiness` 같은 group 경로를 함께 제공하기 때문입니다.

### 적용 결과

수정 후 다시 `describe-target-health`로 확인해봤습니다.

```bash
+-------------+-----------+-----------------------------+--------------------------------+
|  10.0.10.209|  unhealthy| Target.ResponseCodeMismatch | Health checks failed: [503]    |
+-------------+-----------+-----------------------------+--------------------------------+
```

- 401은 사라짐 — Spring Security 단의 경로 정렬은 해결됨
- 응답이 503으로 진척 — 인증 단은 통과했지만 애플리케이션의 Health 상태가 DOWN 또는 OUT_OF_SERVICE로 평가되었습니다. 이는 [다음 layer의 문제로 별도 글]({{< ref "posts/spring-boot-readiness-valkey-password-rotation.md" >}})에서 다룹니다.

## 새로 배운 점

- ALB health check는 애플리케이션 실행 여부가 아니라, 실제 HTTP 응답 코드 기준으로 동작합니다.
- ALB health check가 정상 동작하려면, ALB / Actuator / Security 계층 간 경로 설정이 일관되어야 합니다.
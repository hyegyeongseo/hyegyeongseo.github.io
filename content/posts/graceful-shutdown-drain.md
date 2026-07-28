---
title: "60초를 줬는데 왜 5ms 만에 종료됐을까 — Graceful Shutdown의 실제 경계 찾기"
date: 2026-07-24
lastmod: 2026-07-27
weight: 55
description: "Kubernetes가 60초 유예를 줬는데 앱은 그 유예를 안 쓰고 있었다. 오진을 두 번 걷어내고, '로그가 찍힌다'가 아니라 '실제로 드레인한다'를 대조 실험으로 증명한 기록."
tags: ["kubernetes", "graceful-shutdown", "spring-boot", "drawe"]
categories: ["Troubleshooting"]
---

## 문제 — k8s는 60초를 주는데, 앱이 안 쓰고 있었다

파드를 롤링 재시작하다가, 종료되는 파드가 `Completed`가 아니라 **`Error`** 로 끝나는 걸 봤습니다.

```text
backend-...-9lqc2   1/1   Terminating   0   3d14h
backend-...-9lqc2   0/1   Error         0   3d14h
```

확인해 보니 같은 클러스터인데 **서비스마다 종료 정책이 달랐습니다.**

| 서비스 | 설정 | SIGTERM 수신 시 |
| --- | --- | --- |
| fastapi-guide | graceful timeout 설정됨 | 진행 중 요청 마무리 후 종료 |
| backend | `server.shutdown` **미설정** → Spring 기본 `immediate` | 기다리지 않고 커넥터 종료 |

Kubernetes는 `terminationGracePeriodSeconds: 60`으로 종료 유예를 주고 있었지만, backend는 Spring Boot 기본 종료 모드(`immediate`)를 쓰고 있어 **그 시간을 활용하지 못하는** 구조였습니다.

## 오진을 두 번 걷어냈다

**① "Error를 Completed로 만들자"는 잘못된 목표였다.**
Dockerfile이 `ENTRYPOINT ["java", "-jar", ...]`(exec form)이라 JVM이 PID 1입니다. JVM은 셧다운 훅을 정상 수행해도 SIGTERM에 **128+15=143**으로 끝납니다. 즉 `Error` 표시는 *graceful 여부와 무관한 정상 동작*이고, 이걸 바꾸려 ENTRYPOINT를 래퍼 스크립트로 교체하는 건 얻는 것보다 잃는 게 많습니다. → 목표를 **"롤링 중 in-flight 요청 보호"** 로 재설정했습니다.

**② "이미지를 다시 빌드해야 한다"는 착각.**
`application.properties`를 고치면 CI 빌드 → 레지스트리 → 오버레이 bump가 도는데, 마침 dev 환경이 내려가 있어 검증 경로가 없었습니다. 그런데 Deployment가 `envFrom: configMapRef`로 env를 받고 있었고 Spring은 표준 프로퍼티를 환경변수로 바인딩합니다. → **ConfigMap 설정 두 개만으로 해결할 수 있는 문제**였습니다(이미지 태그가 안 바뀌니 드리프트 게이트도 자동 통과).

## 해결 — ConfigMap 설정 변경

```yaml
# graceful shutdown: 30s < terminationGracePeriodSeconds(60s)
SERVER_SHUTDOWN: "graceful"
SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE: "30s"
```

설정은 둘입니다 — 종료 모드를 `graceful`로, 앱 유예(30s)를 k8s 유예(60s)보다 **작게**. 앱이 먼저 드레인을 끝내고 k8s가 강제 종료하지 않도록 한 것입니다.

## "동작한다"와 "일한다"는 다르다 — 3단계 측정

ConfigMap은 plain resource라 동기화만으로는 파드가 재시작되지 않습니다. 명시적으로 롤아웃한 뒤, 세 단계로 **측정**했습니다.

**1차 — 활성화만 확인(in-flight 없음).**
```text
Commencing graceful shutdown. Waiting for active requests to complete
Graceful shutdown complete   → 7ms
```
로그는 찍혔지만 **기다릴 게 없어** 드레인은 미검증입니다. 즉 *기능이 활성화됐다*는 것만 확인됐을 뿐, *기능이 필요한 상황에서 실제로 동작하는지*는 아직 알 수 없었습니다. 그래서 부하를 건 상태로 2·3차를 설계했습니다.

**2차 — 롤아웃 중 우연한 대비.**
부하를 건 채 롤아웃하자 먼저 종료된 파드는 **4.916s**, 나중 파드는 **5ms**가 나왔습니다. 대비는 얻었지만 어느 파드에 요청이 걸릴지 통제하지 못한 상태였습니다.

**3차 — 통제된 실험(파드를 지정해 삭제).**
변수를 하나만 남겼습니다. 대상을 지정해 삭제하고, A에만 부하를 건 뒤 삭제·B는 idle로 삭제했습니다.

![같은 방식으로 지운 두 파드 — 7120ms vs 7ms](/images/posts/graceful-drain-compare.png)

idle 파드(B)는 즉시 종료됐고, 요청을 처리 중이던 파드(A)는 약 7초 동안 드레인한 뒤 종료됐습니다. 같은 설정·같은 삭제 방식인데 갈린 변수는 *"종료 순간 처리 중인 요청이 있었는가"* 하나뿐입니다. B가 대조군이 되어, 7.1초가 고정 오버헤드가 아니라 **드레인한 만큼의 시간**임을 증명합니다. 7초를 기다려 요청 9건 전부 200으로 완료했고 잘린 흔적은 0건입니다. **적용 전**에는 in-flight 호출이 `InterruptedException`으로 종료된 로그를 확인했고, **적용 후**에는 동일 조건에서 요청 9건이 모두 200으로 완료되는 것을 확인했습니다.

## graceful shutdown이 못 막는 것 (남은 과제)

정직하게 남겨둡니다. 두 가지는 역할이 다릅니다.

- **graceful shutdown** → *이미 받은* 요청을 마무리하고 종료 (이 글이 해결한 부분)
- **preStop 훅 + 엔드포인트 드레인** → *새로 들어오는* 요청의 유입을 먼저 차단

이 Deployment에는 **preStop 훅이 없습니다.** Service가 NodePort, ALB가 `target-type: instance`라 파드 교체가 kube-proxy 엔드포인트 전파에 의존하는데, 파드 삭제 시 SIGTERM 전달과 엔드포인트 제거는 **순서 보장이 없습니다.** 그래서 전파 전에 도착한 신규 요청은 거부될 수 있습니다 — graceful shutdown이 못 막는 영역이고, preStop의 몫입니다.

## 배운 점

1. **"동작한다"와 "일한다"는 다르다.** 로그가 찍히는 것과 실제로 요청을 드레인하는 것은 별개다 — 대조군을 만들어 측정해야 증명된다.
2. **증상을 고치지 말고 목표를 고쳐라.** `Error`(exit 143)는 JVM의 정상 동작이었다. 진짜 목표는 exit code가 아니라 in-flight 요청 보호였다.
3. **가장 작은 변경 경로를 찾아라.** 이미지 재빌드가 아니라 ConfigMap 2줄이면 됐다.
4. **해결 범위를 명확히 정의하라.** graceful shutdown은 이미 받은 요청을 보호하지만, 신규 요청 라우팅은 preStop과 엔드포인트 드레인의 영역이다 — 무엇을 해결했고 무엇은 아직 아닌지 구분하는 것까지가 해결이다.

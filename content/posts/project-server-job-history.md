---
title: "server-job-history — 관리형이 가려주던 것을 직접 세워보기 (개인 프로젝트)"
description: "서버 운영 작업의 이력·권한·감사를 다루는 REST API를, 그 앱 자체를 kubeadm 클러스터 위에 GitOps로 배포하고 3신호로 관측하며 만든 개인 프로젝트. 목적은 앱이 아니라 운영 패턴의 검증이었습니다."
summary: "운영 작업의 이력·권한·감사를 다루는 REST API를, 그 앱 자체를 kubeadm 클러스터 위에 GitOps로 배포하고 3신호로 관측하며 만들었습니다. 목적은 앱이 아니라 운영 패턴의 검증이었습니다."
date: 2026-07-28
lastmod: 2026-07-28
weight: 3
tags: ["server-job-history", "project", "personal", "kubernetes", "kubeadm", "gitops", "argocd", "observability", "slo", "django"]
categories: ["Projects"]
---

| | |
| --- | --- |
| **기간** | 2026.02 ~ 2026.06 (4개월) |
| **인원** | 1명 (개인 프로젝트) |
| **결과물** | [hyegyeongseo/server-job-history](https://github.com/hyegyeongseo/server-job-history) |

### 제가 구축한 것

- **kubeadm 클러스터** — cp-1 + worker-1~3 직접 구성 (flannel · MetalLB · local-path)
- **GitOps 배포** — ArgoCD self-heal · PreSync 마이그레이션 · sealed-secrets
- **관측성 3신호** — Prometheus · Loki · Tempo를 `trace_id`로 상관
- **SLO / error-budget** — multi-window 알림 → Alertmanager → Slack + 런북
- **애플리케이션** — Django REST · 2단계 RBAC · 감사 로그 · 정정 체인 도메인

---

## 왜 만들었나

EKS는 운영 부담을 줄여주는 좋은 서비스지만, 관리형 Kubernetes에서는 클러스터 내부 구성 요소가 추상화되어 있습니다.  
Kubernetes가 실제로 어떻게 동작하는지 이해하기 위해 노트북 VM 위에 kubeadm으로 직접 클러스터를 구축했습니다.

비용을 들이지 않고 스케줄러, CNI, 스토리지, 인증서 구성까지 직접 다루며 Kubernetes의 동작 원리를 검증하는 것이 목표였습니다.

앱은 그 실험의 부하이자 관측 대상이었습니다. 도메인은 운영 작업의 **이력·권한·감사** — 지우면 안 되는 데이터를 다루는 설계는 흔한 CRUD 예제와 다르게 생겼을 거라고 생각했습니다.

{{< diagram src="sjh-architecture.svg"
            alt="server-job-history 아키텍처 — 요청·배포·관측 세 흐름"
            caption="앱은 관측 대상이자 부하원이고, 나머지 전부가 실험 대상이었다" >}}

## 핵심 설계 결정

### ① 이력은 지워지지 않는다 — 삭제 대신 정정 체인

운영 이력은 지우면 안 되는 자산입니다. 그래서 **작업은 하드 삭제 불가**(DELETE API 자체가 없음), 수정은 생성 후 30분 창 안에서만, 그 이후의 정정은 **새 작업을 만들어 체인으로 연결**하게 했습니다. `GET /api/jobs/{id}/chain/` 하나로 "이 작업이 어떻게 정정돼 왔는지"가 시간순으로 나옵니다. **삭제를 막는 대신 추적을 남기는 쪽**을 택한 것이고, 이 도메인에서 가장 중요한 결정이었습니다.

### ② 권한은 2단계로 — 역할 게이트 + 객체 소유권

`ADMIN / OPERATOR / VIEWER` 3역할이지만 역할만으로는 부족했습니다. OPERATOR가 **다른 사람의 작업**을 고칠 수 있으면 이력의 신뢰가 깨지니까요. 그래서 DRF permission을 역할 게이트 → 객체 소유권 두 층으로 나눴습니다. 권한 거부(403)는 흘려보내지 않고 `forbidden_requests_total{path, role}`로 집계합니다 — 권한 설계가 실제로 어떻게 부딪히는지를 메트릭으로 보려고.

### ③ 세 신호를 `trace_id`로 묶는다

메트릭의 "느리다"만으로는 원인을 모릅니다. 모든 로그 레코드에 `trace_id`/`span_id`를 주입하고 OTEL로 Django 요청 + psycopg2 쿼리에 자동 span을 걸었습니다. 실제로 답을 준 적이 있습니다 — 하나의 `trace_id`로 Loki 로그와 Tempo 트레이스를 교차했더니 **DB 쿼리는 ms인데 요청 전체는 6.2초.** 병목이 DB가 아니라는 걸 추측이 아니라 트레이싱으로 규명했습니다.

배포는 ArgoCD가 self-heal 동기화하고, DB 마이그레이션은 **PreSync Job(멱등) → 롤링** 순서로 돕니다. Secret은 git에 못 올리니 sealed-secrets로 **암호문만 커밋**했습니다.

## 결과 — 부숴서 확인한 것

개인 프로젝트 규모에서 응답속도 절대값은 의미가 없습니다. 중요한 건 **측정·검증 체계가 실제로 반응하는가**였습니다.

**SLO** — DB를 일부러 죽이니 가용성 SLI가 100% → **41%** 로 급락하고 error-budget burn이 올라가는 게 실시간으로 잡혔습니다.

| 정상 | DB 장애 주입 |
| --- | --- |
| ![정상 상태 SLO 대시보드](/images/projects/sjh-slo.png) | ![장애 시 error-budget burn](/images/projects/sjh-slo-burn.png) |

**오토스케일** — k6로 부하를 걸어 CPU **86%** 를 넘기니 replicas가 **2 → 6** 으로 늘었습니다.

| ① CPU 임계 초과 | ② 6 replicas로 스케일 |
| --- | --- |
| ![HPA 트리거](/images/projects/sjh-hpa-trigger.png) | ![6 replicas 스케일](/images/projects/sjh-hpa-scaled.png) |

그 밖에 노드 다운 시 재스케줄, sealed-secrets 복호화 경로, 지연 분포 p50/p95/p99를 확인했습니다.

## 까다로웠던 문제 셋

**GitOps ↔ HPA 소유권 충돌** — ArgoCD self-heal이 HPA가 조정한 `replicas`를 되돌려 플래핑이 났습니다. 둘 다 "옳은" 동작이라는 게 문제였고, `ignoreDifferences(/spec/replicas)`로 **필드 소유권을 명시적으로 나눠** 해결했습니다.

**StatefulSet + local-path의 노드 고정** — 노드를 껐더니 postgres가 `Pending`, prometheus가 `Terminating`에 멈췄습니다. PVC가 노드에 묶여 재스케줄이 안 되는 특성이었습니다.

**gunicorn 멀티워커에서 Gauge가 stale해진다** — 프로세스별 값이 남아 실제와 어긋났습니다. **매 scrape마다 DB를 집계하는 커스텀 Collector**로 바꿨습니다. 상태를 프로세스에 들고 있지 말고, 물어볼 때 계산하는 쪽으로.

| 영역 | 사용 |
| --- | --- |
| Backend | Python 3.12 · Django 5.2 · DRF · SimpleJWT · drf-spectacular |
| Data | PostgreSQL 17 |
| Observability | django-prometheus · OpenTelemetry · python-json-logger |
| Infra | Docker · GHCR · Kubernetes(kubeadm) · Kustomize · ArgoCD |
| Monitoring | kube-prometheus-stack · Loki · Tempo · Alloy · Grafana |
| Security | sealed-secrets (Bitnami) |

## 배운 것

관리형에서는 "된다/안 된다"만 보이는 지점에서, 직접 세운 클러스터에서는 **왜 그렇게 되는지**가 보였습니다 — PVC가 왜 노드에 묶이는지, self-heal이 왜 HPA와 싸우는지, 멀티프로세스 메트릭이 왜 어긋나는지.

그리고 **"구성했다"와 "동작한다"는 다릅니다.** SLO도 HPA도 노드 복구도 일부러 부수고 나서야 진짜로 도는지 알 수 있었습니다. 가장 값어치 있었던 작업은 기능 구현이 아니라 **장애를 주입해본 것**이었습니다.

---

| | |
| --- | --- |
| GitHub | [hyegyeongseo/server-job-history](https://github.com/hyegyeongseo/server-job-history) |
| README | [아키텍처 · 검증 결과 · 스크린샷](https://github.com/hyegyeongseo/server-job-history#readme) |
| 주요 문서 | [ADR — 대안·트레이드오프·한계](https://github.com/hyegyeongseo/server-job-history/blob/main/docs/design-decisions.md) · [SLO 런북](https://github.com/hyegyeongseo/server-job-history/blob/main/docs/runbooks/error-budget.md) |

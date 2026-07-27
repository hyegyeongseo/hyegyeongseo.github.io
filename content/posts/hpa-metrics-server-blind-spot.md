---
title: "Healthy인데 왜 죽어 있었을까 — HPA가 12일간 동작하지 않은 이유"
description: "모니터링이 전부 정상인데 HPA 3개가 12일간 죽어 있었다. '고장인가 부재인가'를 먼저 가르는 진단과, metrics-server가 IaC에서 누락된 사각지대를 알림으로 봉인하기까지."
date: 2026-07-19
lastmod: 2026-07-27
weight: 50
tags: ["kubernetes", "hpa", "metrics-server", "eks", "iac", "sre", "drawe"]
categories: ["Troubleshooting"]
---

## 문제 — 전부 Healthy인데, HPA는 죽어 있었다

prod EKS에 관측 스택(로그·메트릭·트레이스)이 전부 떠 있고 ArgoCD도 모두 Healthy였습니다. 그런데 정기 점검 중 이런 걸 봤습니다.

```text
$ kubectl get hpa -A
NAME            TARGETS           AGE
backend         cpu:<unknown>/70%  12d
fastapi-embed   cpu:<unknown>/70%  12d
fastapi-guide   cpu:<unknown>/70%  12d
```

**HPA 3개가 전부 `<unknown>`, 그것도 12일째.** 어떤 대시보드에도, ArgoCD에도 경보는 없었습니다.

이건 "장애가 났다"가 아니라 **"정상처럼 보이는 상태에서 숨어 있던 리스크"** 였습니다. 저에게는 이 유형이 더 무섭습니다 — 아무도 모르니까요.

## 왜 12일간 아무도 몰랐나

장애가 사용자에게 드러나지 않는 조건이 *완벽하게* 갖춰져 있었습니다.

- 트래픽이 0에 가까워 **스케일업할 일이 없었고**,
- `minReplicas`로 파드는 떠 있어 **서비스는 정상으로 보였고**,
- 노드 스케일(Karpenter)은 자체 메트릭을 써서 **무관하게 정상**이었습니다.

그래서 "필요할 때 확장이 안 되는" 리스크가 12일간 조용히 잠들어 있었습니다. 진실은 대시보드가 아니라 **이벤트**에 있었습니다 — ArgoCD 이벤트에 `FailedGetResourceMetric`이 **61,049회** 쌓여 있었습니다.

> 대시보드가 아니라 **이벤트**가 진실을 말할 때가 있습니다.

## 조사 — "고장인가, 부재인가"를 먼저 갈랐다

메트릭이 안 나오는 원인은 크게 둘입니다: 컴포넌트가 **있는데 오작동** vs **아예 없음**. 조사 방향이 완전히 달라서, 이걸 **먼저** 갈랐습니다.

```text
$ kubectl get apiservice v1beta1.metrics.k8s.io
Error from server (NotFound): "v1beta1.metrics.k8s.io" not found
```

Resource Metrics API가 **등록조차 안 됨 → 부재**. 이 한 번의 조회로 조사 대상이 *런타임*에서 *IaC*로 넘어갔습니다.

플랫폼 모듈을 대조해 보니 — ALB 컨트롤러·ArgoCD·External Secrets·Karpenter는 전부 코드(helm_release)로 관리되는데 **metrics-server만 빠져** 있었습니다. HPA 매니페스트는 있었지만, 그걸 구동할 컴포넌트가 처음부터 없었던 겁니다.

## 해결

- 플랫폼 모듈에 **metrics-server를 helm_release로 추가**.
- **차트 버전은 기억으로 하드코딩하지 않고**, EKS 실제 버전을 조회하고 `helm search repo`로 최신 안정 버전을 확인해 고정.
- **`--kubelet-insecure-tls`** — EKS + Karpenter 노드는 kubelet serving cert가 클러스터 CA로 서명되지 않아(IP SAN 부재) x509로 막히는 사례가 흔합니다. 대상이 VPC 내부 노드 자신이라 노출이 제한적이라 이 플래그를 채택하되, 근본 해결(cert 로테이션+CSR 승인)은 별도 트랙으로 두고 *"이 args만 제거하면 된다"*를 코드 주석에 명시.
- 적용 전 `terraform plan`으로 **1 add / 0 change / 0 destroy**를 확인(다른 리소스 드리프트 없음)하고 apply.

결과: metrics-server 1/1 Running, apiservice AVAILABLE=True, HPA 3개가 `<unknown>` → `0%/70%` 정상 수치로 돌아왔습니다.

## 고친 것으로 끝내지 않았다 — 사각지대를 알림으로 봉인

같은 유형의 *침묵 장애*가 반복되지 않도록 관측 계층을 신설했습니다. kube-state-metrics를 도입하고, **HPA의 ScalingActive가 0이면 발화하는 알림**(`InfraHpaScalingInactive`)을 걸었습니다 — "다음 12일"은 사람이 아니라 알람이 잡습니다.

(주의: `absent()` 계열 룰은 대상 컴포넌트 배포 *전에* 적재하면 즉시 오발화하므로, 배포 → 룰 적재 순서를 지켜야 합니다.)

## 배운 점

1. **"깔려 있다"와 "동작한다"는 다르다** — 관측은 배포가 아니라 *경로 검증까지가* 완성이다.
2. 대시보드가 아니라 **이벤트**가 진실을 말할 때가 있다.
3. 진단의 첫 질문은 **"고장 vs 부재"** — 갈림길을 먼저 정하면 조사 시간이 준다.
4. 플랫폼 컴포넌트도 **IaC 목록으로 관리**해야 누락이 보인다.
5. 고친 것으로 끝내지 않고 **같은 부류를 감시하는 알림**을 심어야 사각지대가 닫힌다.

이 경험 이후로 저는 *"수집되고 있다"*가 아니라 *"경로가 이어져 있다"*를 관측 완료의 기준으로 삼습니다.

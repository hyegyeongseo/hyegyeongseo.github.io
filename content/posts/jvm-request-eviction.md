---
title: "exit 137인데 OOMKill이 아니었다 — JVM은 limit을 보고, 쿠버네티스는 request를 본다"
description: "무부하 상태에서도 request를 넘고 있던 파드가 노드 압박이 오자 가장 먼저 축출됐다. exit 137을 OOMKill로 오진했다가 판정 주체를 가르고, MaxRAMPercentage와 request가 구조적으로 어긋나 있던 것을 찾기까지."
date: 2026-07-22
tags: ["kubernetes", "jvm", "eviction", "qos", "resource-limits", "eks", "drawe"]
categories: ["Troubleshooting"]
---

## 문제 — 파드 하나가 Evicted 되어 있었다

ArgoCD를 열었더니 `Degraded 1`이 떠 있었습니다. 파드 하나가 **Evicted** 상태였습니다.

![ArgoCD 리소스 트리 — 파드 하나에 하트가 깨진 아이콘이 붙어 있다](/images/posts/argocd-tree-degraded.png)

```text
STATE          ContainerStatusUnknown
CONTAINER      exited with exit code 137
HEALTH         The node was low on resource: memory.
               Threshold quantity: 100Mi, available: 97296Ki.
               Container backend was using 1227368Ki, request is 1Gi,
               has larger consumption of memory.
```

![파드 Events 탭 — 맨 위 Evicted 이벤트가 노드 메모리 부족을 이유로 든다](/images/posts/argocd-events-evicted.png)

`exit 137`이라 처음엔 OOMKill로 봤습니다. **아니었습니다.**

## 오진 — exit 137은 OOMKill과 eviction을 구분해주지 않는다

`137`은 `128 + 9`, 즉 SIGKILL로 종료됐다는 뜻입니다. 그런데 컨테이너에 SIGKILL을 보내는 주체는 하나가 아닙니다.

| | OOMKill | kubelet eviction |
| --- | --- | --- |
| 판정 주체 | 커널 (cgroup) | **kubelet** |
| 기준 | 컨테이너가 **limit** 초과 | **노드** 메모리 부족 |
| 대상 선정 | 초과한 그 컨테이너 | **노드 위 파드들을 순위 매겨 선택** |

둘 다 `exit 137`로 끝나기 때문에 exit code만으로는 갈리지 않습니다. **이벤트 메시지를 봐야 합니다.**

측정값을 넣어보면 답이 나옵니다.

| 항목 | 값 |
| --- | --- |
| 축출 시점 사용량 | 1227368Ki ≈ **1198Mi** |
| request | 1Gi = **1024Mi** |
| limit | **1536Mi** |

limit(1536Mi)을 **넘지 않았습니다.** 그러니 커널이 죽인 게 아닙니다. 노드 가용 메모리가 축출 임계(100Mi) 아래로 떨어지자 kubelet이 축출 대상을 골랐고, request를 초과해 쓰던 이 파드가 그 대상이 된 것입니다.

kubelet은 아무 파드나 내보내지 않습니다. **request를 초과해 쓰는 파드가 먼저 불리해지고, 거기에 우선순위(Priority)와 얼마나 초과했는지가 함께 고려됩니다.**

이번 상황에서는 다음 요소들이 축출 대상 선정에 영향을 줬습니다.

- **request 초과 여부** — request를 넘겨 쓰는 Burstable 파드들이 우선적으로 불리해집니다
- **Pod Priority** — 우선순위가 낮은 쪽이 먼저
- **request 대비 초과 정도** — 같은 조건이면 더 많이 넘긴 쪽이 먼저

QoS 등급이 직접적인 정렬 키는 아니지만 결과적으로 영향을 줍니다. Guaranteed(`request == limit`)는 정의상 request를 초과할 수 없어 첫 번째 조건에 걸리지 않고, BestEffort(request 미선언)는 항상 걸립니다. backend는 Burstable(`request ≠ limit`)이면서 request를 넘고 있었으니 불리한 쪽에 서 있었던 셈입니다.

즉 **eviction 로직은 설계대로 동작했습니다.** 문제는 그 판정에 걸린 이유 쪽이었습니다.

## 진짜 원인 — 힙 상한을 limit 기준으로 계산하고 있었다

축출 후 살아남은 파드를 재봤습니다.

```text
$ kubectl top pods -l app=backend
NAME                CPU(cores)   MEMORY(bytes)
backend-...-p98h4   8m           1046Mi
backend-...-vvmvk   6m           1121Mi
```

**CPU 8m·6m — 사실상 무부하인데도 둘 다 request(1024Mi)를 넘고 있습니다.** 부하 스파이크가 만든 일시적 초과가 아니라 상시 상태였습니다.

원인은 컨테이너 이미지의 JVM 옵션에 있었습니다.

```dockerfile
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/otel-javaagent.jar -XX:MaxRAMPercentage=75 -XX:+UseG1GC"
```

현대 JVM은 컨테이너 환경을 인식해서 **cgroup의 메모리 limit을 "사용 가능한 RAM"으로 읽습니다.** `MaxRAMPercentage`는 그렇게 인식한 값을 기준으로 힙 상한을 계산합니다.

```text
JVM이 인식하는 RAM = 컨테이너 limit = 1536Mi
힙 상한 = 1536Mi × 75% = 1152Mi
실제 RSS = 힙 + 비힙(메타스페이스·스레드 스택·다이렉트 버퍼·OTel 에이전트)
```

여기서 근거가 두 개로 갈립니다. 섞으면 논리가 약해지므로 나눠서 봤습니다.

**① 계산이 증명하는 것 — 초과가 구조적으로 허용되어 있다.**
힙 상한 1152Mi는 이미 request 1024Mi보다 큽니다. 비힙을 0으로 놓아도 넘습니다. 다만 JVM이 힙 상한까지 항상 커밋하는 건 아니므로, 계산만으로는 *"넘을 수 있다"* 까지입니다.

**② 실측이 증명하는 것 — 실제로 계속 넘고 있었다.**
무부하에서 1046~1121Mi. 문이 열려 있었던 게 아니라, **계속 그 문을 지나고 있었습니다.**

두 근거가 만나는 지점이 이 문제의 정체입니다. **즉 JVM과 쿠버네티스가 서로 다른 기준으로 "메모리를 얼마나 써도 되는지"를 판단하고 있었던 것입니다.**

JVM은 컨테이너 limit을 기준으로 힙 상한을 계산하고, 스케줄러와 eviction은 request를 기준으로 판단합니다. 두 값 사이가 벌어져 있으면 파드는 *"정상 동작 중인데 항상 축출 후보"* 가 됩니다.

### 왜 backend에서만 났나

같은 클러스터의 세 서비스를 나란히 놓으면 이유가 분명해집니다.

| 서비스 | request | limit | 비율 | 런타임 |
| --- | --- | --- | --- | --- |
| backend | 1Gi | 1536Mi | 67% | **JVM** |
| fastapi-embed | 2Gi | 3Gi | 67% | Python |
| fastapi-guide | 5Gi | 6Gi | 83% | Python |

**request/limit 비율은 셋 다 비슷한데 문제는 backend에서만 났습니다.** Python 런타임에는 JVM처럼 컨테이너 limit 기준으로 힙 상한을 자동 계산하는 구조가 없어서, 동일한 request/limit 비율에서도 결과가 다르게 나타났습니다.

## 그런데 왜 평소엔 멀쩡히 도나

request를 넘고 있는데도 서비스가 정상인 이유는, **request가 실행 중에는 강제되지 않기 때문**입니다. 세 값은 각각 다른 주체가 다른 시점에 씁니다.

| 값 | 누가 보나 | 언제 작동하나 |
| --- | --- | --- |
| request | 스케줄러 | 파드를 **어느 노드에 놓을지** 정할 때 |
| request | kubelet | 노드가 압박받을 때 **누굴 축출할지** 고를 때 |
| limit | 커널(cgroup) | 초과하면 즉시 **OOMKill** |

request는 스케줄링 시점에 노드 용량을 **예약**하지만, 실행 중에 그 선을 지키도록 강제하는 주체는 없습니다. 컨테이너는 limit까지 자유롭게 씁니다. 1121Mi는 limit(1536Mi) 아래이므로 커널 입장에선 아무 문제가 없고, 그래서 정상 동작합니다.

좌석에 비유하면 request는 **예약석**, limit은 **최대 허용**, 그 사이는 **빈자리 앉기**입니다. 평소엔 괜찮지만 주인이 오면 비켜야 합니다.

세 관점을 한 줄로 정리하면 이렇습니다.

| 관점 | 판정 |
| --- | --- |
| 커널 | limit 안 넘음 → **정상** |
| 스케줄러 | request보다 많이 씀 → **배치 계산이 어긋남** |
| kubelet | request 초과 → **축출 후보** |

> 평소엔 커널 관점만 작동합니다. 나머지 둘은 **노드가 압박받는 순간에만** 드러납니다.

### `request < limit` 자체는 잘못이 아니다

Burstable QoS는 노드에 파드를 촘촘히 배치해 비용을 아끼는 **의도적 오버커밋 전략**입니다. 문제는 backend가 "가끔 빌려 쓰는" 게 아니라 **"항상 빌려 쓰는"** 상태라는 것입니다.

그 결과 스케줄러는 backend 2개를 **2Gi**로 계산해 노드에 배치하는데 실제로는 **2.2Gi**를 씁니다. 노드가 처음부터 계산보다 빡빡하게 채워집니다.

## 왜 하필 그때 터졌나

원인은 상시 존재했지만, 그날 터진 데는 별도의 트리거가 있었습니다. 원인과 트리거는 층이 다르므로 나눠서 봤습니다.

**① 파드 교체가 반복됐다.** 다른 작업(롤아웃·파드 삭제 반복)으로 파드가 여러 번 교체되며 일시적으로 3~4개가 공존했습니다.

**② Karpenter가 노드를 통합하고 있었다.**

```yaml
disruption:
  consolidationPolicy: WhenEmptyOrUnderutilized
  consolidateAfter: 1m
```

**1분만 저활용이면 통합**합니다. 비용 관점에선 합리적인 설정이지만, 통합 직후의 노드는 그만큼 빡빡해집니다.

**③ 배치 가능한 노드가 좁았다.** backend는 파드 단위 보안그룹(SecurityGroupPolicy)이 branch ENI를 요구해서, ENI trunking이 되는 인스턴스 계열로만 스케줄되도록 제한돼 있습니다.

```yaml
nodeAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    nodeSelectorTerms:
      - matchExpressions:
          - key: karpenter.k8s.aws/instance-category
            operator: In
            values: ["m", "c", "r"]
```

통합은 공격적이고 배치 대상은 좁은데, 스케줄러의 계산은 실제보다 0.2Gi 낮습니다. 세 조건이 겹치자 한 노드가 먼저 임계에 닿았습니다.

**메모리 최고 사용률 91%** 로 임계(90%)를 넘겼는데, 노드별 그래프를 보면 **대부분 30~50%대인데 하나만 90%대로 튀어 있었습니다.** 클러스터 전체가 부족했던 게 아니라 **그 노드 하나**가 빡빡해졌고, 거기 있던 backend 파드가 request를 넘고 있어 먼저 나간 것입니다.

트리거는 우연이지만 원인은 상시 존재했습니다. **언젠가는 터질 문제였습니다.**

## HPA는 이 유형을 못 막는다

"오토스케일이 있는데 왜 못 막았나"는 자연스러운 질문인데, 설정을 보면 답이 나옵니다.

```yaml
metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

**CPU 단일 지표입니다.** 메모리는 HPA의 판단 대상이 아닙니다. 게다가 이번 상황은 CPU 8m — HPA 입장에서는 확장할 이유가 전혀 없는 상태였습니다.

메모리 지표를 HPA에 추가하는 것도 방법이지만, 이 경우엔 맞지 않습니다. JVM은 GC 전까지 확보한 메모리를 잘 반납하지 않아서 **메모리 기반 HPA는 스케일인이 잘 안 되는** 특성이 있기 때문입니다. 여기서 필요한 건 파드를 늘리는 게 아니라 **선언한 값과 실제 사용량을 맞추는 것**이었습니다. 스케일링 문제와 리소스 선언 문제는 다릅니다.

## 조치 — 두 안과 트레이드오프

둘 중 하나로 정합을 맞춰야 합니다.

**① request를 실측에 맞춘다** — `memory: 1Gi → 1280Mi` 내외
실사용(1.05~1.12Gi)보다 약간 위로 잡습니다. 스케줄러가 실제 필요량을 알게 되어 배치 계산이 맞아떨어지고, 축출 후보에서 빠집니다. 대신 노드당 배치 가능한 파드 수가 줄어 **비용이 오릅니다.**

**② JVM 힙 상한을 낮춘다** — `MaxRAMPercentage=75 → 60` 내외
컨테이너 RSS를 request 아래로 눌러 넣습니다. 이미지 재빌드가 필요하고, 힙이 줄어 GC 압박이 커질 수 있습니다.

그런데 ②는 숫자를 넣어보면 **충분하지 않을 가능성**이 보입니다.

```text
60% 적용 시 힙 상한 = 1536Mi × 60% = 921Mi
+ 비힙(메타스페이스·스레드 스택·다이렉트 버퍼·OTel 에이전트)
→ request 1024Mi를 다시 넘길 여지
```

비힙 사용량을 실측하지 않고는 몇 %가 안전한지 정할 수 없고, 더 낮추면 GC 압박이 커집니다. **측정 없이 고르기엔 위험이 큰 선택지**입니다.

그래서 **①을 택했습니다.** 현재 사용량이 비정상이라는 근거가 없고(무부하 1.05Gi는 Spring Boot + OTel 에이전트 스택에서 타당한 범위), request를 현실에 맞추는 쪽이 부작용이 적습니다. 다만 **limit은 그대로 두어** Burstable의 여유는 유지합니다.

비용 관점에서 손해 아니냐는 반문이 가능한데, 저는 이렇게 정리했습니다. **request 상향은 빈 패킹 효율을 떨어뜨리지만, 실사용을 반영하지 않은 request는 노드가 압박받을 때마다 예측 불가능한 축출을 만드는 비용입니다.** 절감은 예측 가능한 상태 위에서 다시 설계할 수 있지만, 선언값과 현실값이 어긋난 채로는 어떤 절감도 신뢰할 수 없습니다. 그래서 정합을 먼저 맞추는 쪽을 골랐습니다.

## 남은 과제

정직하게 남겨둡니다. 이 글은 **원인을 규명한 기록이지, 조치를 적용하고 재측정한 기록은 아닙니다.**

- request 상향은 아직 반영 전입니다. 적용 후에는 `kubectl top`으로 request 아래로 들어왔는지, 노드당 배치 밀도가 얼마나 줄었는지를 함께 재야 합니다.
- 비힙 사용량을 실측하면 ②(힙 상한 조정)를 다시 저울질할 수 있습니다. 비용이 문제가 되는 시점에 다시 볼 카드입니다.
- 근본적으로는 **컨테이너 리소스 선언과 런타임 메모리 설정을 함께 검토하는 절차**가 없었던 게 원인입니다. 새 JVM 서비스를 올릴 때 `MaxRAMPercentage × limit`과 request를 대조하는 단계를 넣는 게 재발 방지에 가깝습니다.

## 배운 점

1. **`exit 137`은 OOMKill과 eviction을 구분해주지 않는다.** 판정 주체(커널 vs kubelet)와 기준(limit vs 노드 압박)이 다르므로 exit code가 아니라 이벤트 메시지를 봐야 한다.
2. **request는 예약량이면서 동시에 운영 판단의 기준이다.** 스케줄링 시점엔 노드 용량을 예약하지만 실행 중엔 강제되지 않고, 대신 노드가 압박받을 때 축출 순위를 정하는 값으로 다시 쓰인다 — 그래서 평시엔 잘 돌다가 압박이 오는 순간 가장 먼저 나간다.
3. **"잘 돌고 있다"가 "제대로 설정됐다"는 아니다.** 커널·스케줄러·kubelet이 각각 다른 값을 보므로, 한 관점에서 정상이어도 다른 관점에선 이미 어긋나 있을 수 있다.
4. **런타임이 컨테이너 limit을 인식하는지 확인해야 한다.** JVM은 limit을 기준으로 힙 상한을 계산하지만 쿠버네티스는 request로 판단한다. 같은 오버커밋 비율이어도 JVM 서비스에서만 구조적 축출 후보가 만들어지는 이유다.
5. **계산과 실측은 증명하는 것이 다르다.** 상한 계산은 "초과가 허용되어 있다"까지, 실측은 "실제로 넘고 있다"까지를 보인다. 둘을 함께 놓아야 원인이 확정된다.
6. **다른 작업이 만든 압박이 상시 갭을 드러냈다.** 장애 재현이 아니라 '드러내기'로도 테스트는 값을 한다.

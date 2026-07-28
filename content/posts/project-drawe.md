---
title: "DraWe — 그림에서 막히는 지점을 짚고 다음 연습까지 잇는 AI 드로잉 코치 (팀 프로젝트)"
description: "창작 지원 AI 에이전트 개발 프로젝트. AI 한 끗 가이드 기능을 개발하고, 서비스를 운영하기 위한 AWS 클라우드 인프라와 GitOps·Observability 기반 운영 체계를 구축했습니다."
summary: "창작 지원 AI 에이전트 개발 프로젝트. AI 한 끗 가이드 기능을 개발하고, 서비스를 운영하기 위한 AWS 클라우드 인프라와 GitOps·Observability 기반 운영 체계를 구축했습니다."
date: 2026-07-28
lastmod: 2026-07-28
weight: 1
tags: ["drawe", "project", "team", "eks", "gitops", "observability", "ai-engineering"]
categories: ["Projects"]
---

**창작 지원 AI 에이전트 개발 프로젝트.** 그림을 대신 그려주지 않습니다. 올린 그림에서 **관찰 가능한 시각 신호**를 뽑아, "지금 한 끗을 바꾸면 좋아지는 지점"을 **그림 위에서 근거와 함께** 짚어줍니다.

| | |
| --- | --- |
| **기간** | 2026.03 ~ 2026.07 (4개월) |
| **인원** | 5명 (팀 프로젝트) |
| **결과물** | [DraWeTeam/drawe](https://github.com/DraWeTeam/drawe) |

### 제가 맡은 것

- **AI 한 끗 가이드** — 제품의 **에이전트 층을 처음 구현**. 관찰(VLM) → 진단 → 검색 → 코칭 파이프라인을 [개인 프로토타입](/posts/project-artcoach/)에서 이식하고, **그림 위 오버레이 마커·즉시 수정 체크리스트·추천 이유**로 고도화
- **완성작 갤러리 개발** — 업로드 누적과 코칭 결과를 잇는 성장 데이터 흐름의 **데이터 구조 · API · 서비스 로직** 구현
- **AWS 인프라** — Terraform IaC, dev/prod 계정 분리
- **ECS → EKS 마이그레이션** — 관측·시크릿·트래픽 연속성을 유지한 점진 컷오버
- **GitOps · CI/CD** — ArgoCD 자동 동기화, GitHub Actions OIDC, ARM64 빌드
- **관측성** — OpenTelemetry · Alloy · Loki · Tempo · AMP · 알림 설계
- **운영 자동화 · 비용 최적화** — 재우기/깨우기 런북, 비용 모델링

> 가이드의 범위와 상세 방향, 성장 흐름을 어떻게 보여줄지는 **기획에서 정한 팀 방향**입니다. 저는 그 방향을 동작하는 서비스로 만드는 쪽을 맡았습니다.

---

이 글은 입구입니다. 아래의 설계 결정과 운영 중 마주친 문제는 각각 [따로 쓴 글](#더-깊이-읽을-글)이 있습니다.

## 아키텍처

{{< diagram src="drawe-architecture.svg"
            alt="DraWe 운영 아키텍처 — 요청·배포·관측 세 흐름"
            caption="요청 · 배포 · 관측 세 흐름" >}}

앱은 **OTLP만 알고** 저장은 수집기(Alloy)가 정합니다 — dev는 SaaS, prod는 직접 운영이라는 선택이 앱 코드 변경 없이 가능했습니다.

벡터 DB가 둘인 건 **코퍼스와 질의를 만드는 방식이 달라서**입니다. **Qdrant**(가이드)는 진단이 고른 축에 대해 taxonomy에 **미리 박아둔 영문 질의문**으로 조회합니다 — 비율 축이면 `human body proportions head units figure chart` 처럼요. **Pinecone**(보드)은 사용자 검색어에서 **미술 용어 키워드를 뽑아**(한→영 사전 247개, 미스율이 높으면 LLM 폴백) 조회합니다. 둘 다 CLIP 텍스트 임베딩으로 유사 이미지를 찾지만, **질의문이 고정이냐 매번 달라지느냐**가 다릅니다.

## 무엇을 만들었나

DraWe의 주제는 창작 지원 AI 에이전트지만, 처음 동작한 건 **레퍼런스 추천 — 검색**이었습니다. **검색은 사용자가 무엇을 찾아야 하는지 알 때 동작하는데**, 그림 앞에서 막힌 사람은 그걸 모릅니다.

**한 끗 가이드는 그 간극을 메우면서 제품에 처음으로 에이전트를 들여온 층**입니다 — 평가하는 도구가 아니라 다음 연습 행동을 제시하는 코치. 이름도 골격도 [개인 프로토타입](/posts/project-artcoach/)에서 나왔고, 팀 서비스로 오면서 **표현이 세분화되고 분석 데이터가 얹혔습니다.**

손 그림을 올리고 *"손가락 비율이 어색해요"* 라고 물으면 이렇게 나옵니다.

![한 끗 가이드 결과 — 그림 위 ①② 마커 · 추천 레퍼런스 · 추천 이유](/images/projects/drawe-guide.png)

말이 아니라 **그림 위에서** 짚습니다 — 반복 관찰됐고 측정된 축에 한해서만. 레퍼런스도 나열이 아니라 **왜 이걸 보라는지**가 함께 붙습니다.

## 핵심 설계 결정 셋

**① AI의 불확실성을 계층별로 분리했다.** 관찰은 VLM(Bedrock Claude)이, 판단은 taxonomy 기반 결정 로직이, 표현만 LLM(Grok)이 맡습니다. **어떤 모델이냐보다 역할을 어디까지 주느냐**가 핵심이었습니다.
→ [LLM의 역할을 제한했다](/posts/llm-role-constraint/)

**② 진단과 추천을 분리했다.** 진단 결과를 LLM으로 재해석하지 않고 그대로 검색 조건에 넣습니다. 그래서 추천이 "비슷한 그림"이 아니라 **"지금 이 축을 연습하는 데 맞는 참고물"** 이 됩니다. 취향은 진단을 바꾸지 않고 **순위만** 조정합니다.
→ [진단과 추천을 분리한 이유](/posts/diagnosis-recommend-separation/)

**③ 한 번의 피드백으로 끝내지 않았다.** 성장과 커리큘럼을 함께 보여주는 방향은 팀 논의와 교육 과정 멘토링을 거쳐 발전시킨 제품 방향이고, 제 역할은 그걸 **판정 가능한 규칙과 상태머신으로 구현하는 것**이었습니다. 점수를 안 매기는 이상 "나아지고 있다"를 보여줄 다른 방법이 필요했고, 택한 건 **요청의 변화**였습니다 — 같은 걸 계속 묻지 않게 되는 것 자체가 성장이니까.
→ [AI 드로잉 코치를 설계한 이유](/posts/ai-drawing-coach-design/)

## 인프라

에이전트 워크로드는 CRUD API와 다릅니다 — 요청 하나가 초 단위로 길고, 외부 모델·벡터 스토어·오브젝트 스토리지를 차례로 오가서 실패할 수 있는 지점이 흩어져 있고, 비용이 요청 수가 아니라 토큰·이미지 수에 붙습니다.

ECS(EC2)로 시작했지만 **운영을 선언적으로 관리하기 어려운 부분**이 남았습니다. 배포 상태를 git으로 수렴시키는 것, 파드 요구에 맞춰 인스턴스를 고르고 저활용 노드를 정리하는 것, HPA·SecurityGroupPolicy 같은 프리미티브로 다루는 것 — 이걸 얻으려고 EKS로 옮겼습니다. Kubernetes 도입이 목적이 아니라 **운영을 코드로 선언하고 재현 가능하게 만드는 것**이 목적이었습니다. ([전환 과정](/posts/ecs-to-eks-cutover/))

![prod 노드 목록 — 전부 arm64, Karpenter가 고른 인스턴스 타입](/images/projects/drawe-nodes-arm64.png)

| 영역 | 기술 |
| --- | --- |
| **Compute · IaC** | AWS EKS · Karpenter · Graviton ARM64 · Terraform |
| **배포** | ArgoCD GitOps · Kustomize · GitHub Actions(OIDC · ARM64 → ECR) |
| **관측성** | OpenTelemetry · Alloy · Loki · Tempo · AMP · Grafana |
| **보안** | IRSA · ESO + SSM SecureString · SecurityGroupPolicy · 계정 분리 |
| **Application** | Spring Boot · FastAPI · React(Cloudflare Pages) |
| **데이터 · 벡터** | RDS MySQL · ElastiCache Valkey · S3 · Qdrant · Pinecone |
| **AI** | CLIP · AWS Bedrock(VLM 관찰 · 이미지 생성) · LLM(코칭 문장 생성) |

비용은 워크로드 특성에 맞춰 손봤습니다. 이미지에만 의존하는 VLM 결과는 캐시해 재호출을 없앴고, 아웃바운드는 NAT Gateway 대신 **fck-nat 인스턴스(t4g.nano)를 AZ마다 하나씩** 두고 ASG로 자가복구시켰습니다.

![prod fck-nat 인스턴스 — AZ별 1대, ASG로 교체 복구](/images/projects/drawe-nat-fck.png)

비용 최적화의 끝은 **아예 내리는 것**이었습니다. 지금 prod는 재우기 런북으로 EKS와 ElastiCache를 destroy한 상태라 **서비스 주소는 열려 있지 않습니다.** 그게 가능한 건 **상태를 컴퓨트 밖에 뒀기 때문**입니다 — 코드는 git, 인프라는 Terraform state(S3), 데이터는 RDS·S3·SSM. 깨우기 런북으로 **~1시간 내 복구**되고, 그 런북에는 *모노레포가 아닌 복사본에서 apply하면 44개를 destroy하려 든다* 같은 **실제로 터졌던 함정 6가지**가 적혀 있습니다.

팀에서 지킨 규칙 하나. **변경 전에는 골든셋으로 품질을 검증하고, 변경 후에는 관측으로 상태를 확인한다.**

골든셋은 라벨을 먼저 달았습니다 — **시스템 출력을 보기 전에** 그림만 보고 "이 그림의 1차 문제는 무엇인가"를 적었습니다. 현재 동작에 끌려가지 않으려고요. 이게 잡아준 게 *"손 스케치를 올렸는데 명암 가이딩"* 같은 오분류입니다. 진단을 건드릴 때마다 축 정확도를 before/after로 재니 역회귀가 바로 드러났습니다.

**모델도 바꿀 수 있는 구조로 만들었습니다.** 프롬프트·골든셋·기대값을 고정한 채 `VLM_BACKEND`만 교체해 정확도·게이트·레이턴시·실패율·토큰비용을 1:1로 대조하고, **정확도 동급 · 실패 0 · VLM 호출 1건 기준 레이턴시 2.8×**를 확인한 뒤에야 적용했습니다(파이프라인 전체 P95가 아니라 관찰 호출 하나의 wall-clock입니다). 관측 설정을 바꿀 때도 마찬가지여서, 그럴듯한 가설이 [실측에 뒤집히는 일](/posts/amp-spanmetrics-cost/)도 잡았습니다.

![로그의 trace_id에서 Tempo 트레이스로 점프](/images/projects/drawe-trace-jump.png)

로그의 `trace_id`가 Grafana에서 **클릭 가능한 링크**로 잡힙니다. 앱이 `trace_id:<hex>`로 찍고 Loki datasource의 `matcherRegex`가 그걸 뽑아 Tempo로 연결하는 — **양쪽이 맞아야 성립하는 계약**입니다.

## 더 깊이 읽을 글

**제품 설계**
- [AI 드로잉 코치를 설계한 이유](/posts/ai-drawing-coach-design/)
- [진단과 추천을 분리한 이유](/posts/diagnosis-recommend-separation/)
- [LLM의 역할을 제한했다](/posts/llm-role-constraint/)

**인프라 · 운영**
- [ECS에서 EKS로](/posts/ecs-to-eks-cutover/)
- [GitOps로 배포 상태를 코드화하다](/posts/gitops-argocd-drift/)
- [관측성 설계 ① 수집과 저장](/posts/opentelemetry-observability-stack/)
- [관측성 설계 ② 무엇을 알릴 것인가](/posts/alert-design-what-and-how/)
- [관측성 설계 ③ 왜 AMP가 알림을 맡았나](/posts/alerting-with-amp-not-grafana/)
- [ARM64 CI 빌드 전략](/posts/arm64-ci-build-strategy/)
- [AWS dev 환경 VPC 비용 최적화](/posts/aws-vpc-endpoint-nat-cost-optimization/)

**운영 중 마주친 문제**
- [Healthy인데 왜 죽어 있었을까 — HPA 12일](/posts/hpa-metrics-server-blind-spot/)
- [관측성을 늘렸더니 비용이 늘었다 — AMP 43%](/posts/amp-spanmetrics-cost/)
- [60초를 줬는데 왜 5ms 만에 종료됐을까](/posts/graceful-shutdown-drain/)
- [GitOps에서 git은 데이터베이스다](/posts/gitops-overlay-bump-race/)
- [설정은 맞는데 왜 안 될까 — SGP 적용 시점](/posts/sgp-branch-eni-timing/)

## 배운 것

가장 오래 붙잡은 건 어떤 모델을 쓸지가 아니라 **불확실성을 어디서 통제할지 정하는 일**이었습니다. 관찰은 VLM이, 판단은 코드가, 표현은 LLM이 — 이 경계를 그은 뒤에야 사용자가 *"왜 지금 이걸 연습하라는지"* 를 납득할 수 있게 됐습니다.

그리고 **운영에서는 "동작한다"보다 "관측된다"가 중요했습니다.** HPA 3개가 12일간 죽어 있는 동안에도 대시보드는 초록색이었습니다. 운영 시스템은 정상 상태를 보여주는 것만으로 부족하고, **정상이라고 믿는 상태가 틀렸을 때 그 사실을 알려줄 수 있어야 한다**는 것을 배웠습니다.

---

| | |
| --- | --- |
| GitHub | [DraWeTeam/drawe](https://github.com/DraWeTeam/drawe) |
| README | [프로젝트 개요 · 실행 방법 · 데모](https://github.com/DraWeTeam/drawe#readme) |
| 주요 문서 | [인프라 레이어](https://github.com/DraWeTeam/drawe/blob/main/infra/README.md) · [AI 파이프라인](https://github.com/DraWeTeam/drawe/blob/main/fastapi/README.md) |

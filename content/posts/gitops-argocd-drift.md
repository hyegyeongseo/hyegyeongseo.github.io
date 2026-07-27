---
title: "GitOps로 배포 상태를 코드화하다 — 왜 ArgoCD였고, 드리프트를 어떻게 막았나"
date: 2026-06-20
lastmod: 2026-07-27
weight: 42
description: "ECS 시절 CI가 배포 상태를 직접 갱신하던 구조를 ArgoCD GitOps로 옮긴 이유. git을 단일 출처로 삼고, branch-per-env 드리프트를 2층으로 막고, drift-gate가 diff가 아니라 '머지 결과'를 검사하게 한 설계."
tags: ["gitops", "argocd", "cicd", "eks", "kubernetes", "sre", "drawe"]
categories: ["Infra Observability"]
---

## 배경 — 배포 상태의 단일 출처가 git이 아니라 AWS에 있었다

ECS 시절에도 배포는 버전 관리됐습니다. GitHub Actions가 JAR을 빌드해 ECR에 **`:커밋SHA`** 태그로 푸시하고, 현재 task definition을 읽어 이미지만 바꾼 **새 리비전을 등록**한 뒤(`register-task-definition`) **서비스를 갱신**하고(`update-service`) 안정화를 기다렸습니다. 이미지가 커밋 SHA로 고정되니 이전 리비전으로 되돌리는 롤백도 가능했습니다.

부족했던 건 *이력*이 아니라 **운영 상태의 기준점**이었습니다. 눈여겨볼 부분은, 새 task definition의 원본을 **git이 아니라 AWS의 '현재 살아있는 task definition'** 에서 읽어 왔다는 점입니다(`describe-task-definition` → 이미지만 교체 → 재등록). 즉 **배포 명세(desired state)의 출처가 git이 아니라 AWS**였습니다. git은 애플리케이션 코드와 Dockerfile은 담았지만, *지금 무엇이 어떻게 떠 있는가*를 **선언하거나 보장하지는 않았습니다.** 콘솔에서 수동 변경이 생겨도 git과 다시 맞춰지지 않았고, 실제 운영 상태를 확인하려면 git보다 AWS를 먼저 봐야 했습니다.

EKS로 옮기면서 이 **기준점을 git으로** 옮기고 싶었습니다. 핵심 질문은 하나였습니다 — **배포 상태를 어디에 두고, 무엇을 단일 출처로 삼을 것인가.**

## 선택지 — push형 vs pull형

| 방식 | 성격 | 트레이드오프 |
| --- | --- | --- |
| CI가 `kubectl apply` (push형) | CI가 클러스터를 직접 변경 | git↔실제 상태를 지속적으로 대조·복원하려면 별도 구현이 필요 · CI가 클러스터 자격증명을 보유 |
| **ArgoCD GitOps (pull형, 채택)** | git이 단일 출처 | 초기 학습 비용 + 운영 컴포넌트 추가 |
| Flux | 기능 유사 | 앱 트리 시각화·UI에서 ArgoCD 선호 |

## 왜 ArgoCD였나

- **git = 배포 상태의 단일 출처.** `main`에 push하면 ArgoCD가 auto-sync(prune + selfHeal)로 클러스터를 git 상태에 **수렴**시킵니다. 이력·롤백이 전부 git으로 추적됩니다 — **배포는 커밋, 롤백은 revert.**
- **CI와 CD의 책임 분리.** CI는 이미지 빌드 + 오버레이 `newTag` bump 커밋**까지만** 합니다. CD(클러스터 반영)는 ArgoCD 몫이라, **CI에 클러스터 자격증명이 필요 없습니다.** 공격면이 줄어듭니다.
- **selfHeal로 드리프트 자동 원복.** 콘솔에서 손으로 바꾼 것("몰래 고친 것")이 git 상태로 되돌아갑니다. 수동 변경이 남지 않습니다.

## 운영에서 만난 문제 — branch-per-env 드리프트

이론은 깔끔하지만 운영은 그렇지 않았습니다. 환경을 브랜치로 나누자(dev=`develop`, prod=`main`) **prod 오버레이 파일이 두 브랜치에 복제**되는 구조가 됐습니다.

`develop`에도 prod 오버레이가 복제돼 있어서, **낡은 prod 오버레이가 `main`으로 승격되면 실제 운영 이미지가 과거 버전으로 되돌아갈 수 있었습니다.** 배포가 앞이 아니라 뒤로 가는 것이죠. 이걸 **2층으로 막았습니다.**

1. **자동 동기화(평시)** — CD가 `main`의 prod bump를 하면 `develop` 사본도 함께 동기화. 사본이 낡지 않게 상시 유지.
2. **drift-gate(승격 시)** — `main` 대상 PR을 머지하면 prod가 바뀌는지 검사하는 게이트가 승격을 차단.

### drift-gate가 'diff'가 아니라 '머지 결과'를 검사하는 이유

이 부분이 설계의 핵심입니다. 흔히 PR의 **diff**를 보고 싶지만, diff는 *충돌 자동 해소 이전* 상태라 부정확합니다. "이 PR을 머지하면 prod가 정말 바뀌는가"는 **실제 머지했을 때의 트리**(`refs/pull/N/merge`)를 봐야 정확히 판정됩니다.

그래서 게이트는 머지 결과 트리를 검사합니다. 정상 케이스(3-way 병합이 `main` 값을 유지)는 "같음"으로 통과해 **오탐이 0**입니다. **게이트는 변경 의도를 검사하는 것이 아니라, 머지 이후 실제 운영 상태를 검사합니다** — 이게 게이트를 신뢰할 수 있게 만든 지점입니다.

(오버레이 동시 bump가 겹쳐 non-fast-forward로 충돌하는 race도 있었는데, 이건 별도 글에서 다룹니다 — 작업트리 정리 + rebase 진행 가드 + 재시도로 해결.)

## 서비스별 이미지 빌드 전략 — 짧게

배포 파이프라인에서 backend(Spring)와 fastapi(Python)의 이미지 빌드 방식을 다르게 가져갔습니다. backend는 아키텍처 중립인 JAR을 CI에서 먼저 빌드하고 Docker에는 결과물만 COPY했고, fastapi는 mediapipe·CLIP 같은 아키텍처 의존 라이브러리가 있어 Docker 내부에서 arm64 기준으로 빌드했습니다. **서비스 특성에 따라 빌드 전략은 달라도, GitOps 관점에서는 둘 다 git이 배포 상태의 기준이 되도록 유지했습니다.**

(빌드 방식의 arm64/QEMU·buildcache·ECR lifecycle 디테일은 [ARM64 CI 빌드 전략](/posts/arm64-ci-build-strategy/) 글로 분리했습니다.)

## 적용 결과와 운영 철학

배포 = git 커밋, 롤백 = revert가 됐습니다. 마이그레이션·재기동 때도 ArgoCD가 클러스터를 git 상태로 수렴시켜 **재현성**이 확보됐습니다. 콘솔을 열지 않아도 "지금 prod가 무엇을 추적하는가"가 `main`의 오버레이 경로에 그대로 드러납니다.

**GitOps의 핵심은 ArgoCD가 아닙니다.** 배포 상태를 git으로 선언하고, 선언과 실제 상태 사이의 드리프트를 어떻게 관리할지 설계하는 일입니다. ArgoCD는 그 설계를 실행하는 도구였습니다.

- **git을 단일 출처로** — 배포·롤백·이력이 한곳에
- **CI와 CD의 책임 분리로** 공격면 축소
- **드리프트를 2층으로 막고**, 게이트는 *추측이 아니라 머지 결과*를 본다

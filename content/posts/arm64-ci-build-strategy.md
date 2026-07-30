---
title: "ARM64 CI 빌드 전략 — 같은 클러스터, 두 개의 다른 Docker 빌드"
date: 2026-06-20
lastmod: 2026-07-27
description: "Graviton(arm64) 위에서 Spring과 Python 서비스를 빌드하는 방식을 왜 다르게 가져갔나. JVM 바이트코드의 아키텍처 중립성으로 QEMU 비용을 0으로 만들고, 네이티브 의존성은 buildcache로 감당한 이야기."
tags: ["arm64", "graviton", "docker", "cicd", "ecr", "buildx", "drawe"]
categories: ["Infra Observability"]
---

## 배경 — Graviton으로 갔더니 빌드가 갈렸다

노드를 Graviton(arm64)으로 쓰면서 이미지도 arm64로 빌드해야 했습니다. 그런데 backend(Spring)와 fastapi(Python)는 **빌드 방식이 자연히 갈렸습니다.** 공통점은 셋 다 네이티브 arm64 러너(`ubuntu-24.04-arm`)에서 `platforms: linux/arm64`로 빌드하고 buildx 레지스트리 캐시를 쓴다는 것. 차이는 하나였습니다 — **"무엇을 Docker 안에서 빌드하느냐."**

## backend — JAR을 Docker 밖에서 빌드해 COPY

```text
CI 단계(Docker 밖): ./gradlew bootJar   → app.jar
Dockerfile:         COPY app.jar        (base = JRE 런타임, gradle 없음)
```

핵심은 **JAR이 아키텍처 중립(JVM 바이트코드)** 이라는 점입니다. 어디서 빌드하든 결과가 같으니, Gradle 컴파일을 **Docker 밖 CI 단계**에서 끝내고 Dockerfile은 완성된 JAR만 COPY합니다.

- arm64 빌드에서 **QEMU 에뮬레이션 컴파일 비용이 0** — Docker 내부에서 gradle을 돌리지 않으니까요.
- 중복 빌드 제거 — 빌드는 CI에서 한 번.
- Docker 빌드 단계에 캐싱할 무거운 중간 레이어가 거의 없음 → (backend도 동일하게 buildcache를 쓰지만) 캐시가 가벼워 ECR에 태그 없는 부산물이 잘 안 쌓입니다.

## fastapi — 네이티브 의존성을 Docker 안에서 arm64로 빌드

fastapi는 사정이 다릅니다. **mediapipe·CLIP 같은 아키텍처별 네이티브 의존성**은 JAR처럼 중립이지 않아서, Docker 빌드 중에 arm64로 컴파일/설치해야 합니다. 무거운 빌드 레이어가 생기죠.

- 이 레이어를 **buildx 레지스트리 캐시(`buildcache`)** 로 캐싱해 빌드 속도를 잡습니다.
- `provenance: false`로 **ECR 호환 단일 매니페스트**로 푸시합니다(멀티아키 인덱스 대신).

## 부산물 — 태그 없는 `-` 이미지는 결함이 아니다

fastapi 리포를 보면 **태그 없는(`-`) 이미지**가 쌓입니다. 매 빌드마다 `buildcache` 태그가 새 캐시로 옮겨가면서 옛 캐시 이미지가 태그를 잃기 때문입니다. 이건 결함이 아니라 **buildcache 최적화의 정상 부산물**입니다.

![ECR 이미지 목록 — git SHA 태그·buildcache 태그와 함께 태그를 잃은 `-` 이미지가 보인다](/images/posts/ecr-images-detail.png)

방치하면 ECR이 비대해지니, **lifecycle policy(Keep last 10)** 로 자동 정리합니다(정리 이벤트에서 여러 이미지가 실제로 정리되는 걸 확인). backend 리포에 이 부산물이 훨씬 적은 건 캐시를 안 써서가 아니라 — 두 파이프라인 모두 buildcache를 씁니다 — JAR을 Docker 밖에서 빌드해 COPY하므로 **캐싱되는 레이어 자체가 가볍기** 때문입니다.

## 정리 — 전략은 언어 특성을 따른다

| | backend (Spring) | fastapi (Python) |
| --- | --- | --- |
| 빌드 위치 | JAR은 Docker **밖** CI 단계 | 네이티브 의존성은 Docker **안** |
| 이유 | JVM 바이트코드 = 아키텍처 중립 | mediapipe·CLIP = 아키텍처 의존 |
| arm64 비용 | QEMU 컴파일 0 | buildcache로 상쇄 |
| ECR 부산물 | 적음(가벼운 캐시) | 태그 없는 캐시 다수 → lifecycle 정리 |

같은 클러스터에 올라가는 두 서비스라도, **아키텍처 중립성 여부**에 따라 빌드 전략은 달라집니다. "arm64로 빌드했다"가 아니라, *어디까지를 Docker 밖으로 빼고 무엇을 캐싱할 것인가*를 서비스마다 결정한 것이 요점입니다.

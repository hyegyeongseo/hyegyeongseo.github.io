---
title: "ECS에서 exec format error가 발생한 이유 — Graviton(ARM64) 이미지 아키텍처 문제"
description: "Graviton(ARM64) 기반 ECS에서 발생한 exec format error를 추적해 amd64 이미지 아키텍처 mismatch 문제를 해결한 과정"
date: 2026-05-08
tags: ["aws", "ecs", "docker", "arm64", "graviton", "ci-cd", "github actions"]
---

## 진행 사항

dev 환경용 ECS 클러스터를 구성하고, Spring Boot backend 이미지를 배포하고 있었습니다.

## 발생한 문제

로컬에서는 정상 실행되던 컨테이너가 ECS 환경에서는 시작 직후 종료되었습니다. 

CloudWatch 로그에는 다음 한 줄만 찍히고 컨테이너가 즉시 죽었습니다.

```text
exec /opt/java/openjdk/bin/java: exec format error
```

## 원인 분석

### exec format error는 무엇인가

`exec format error`는 실행 파일 형식이나 아키텍처가 현재 환경과 맞지 않을 때 발생하는 에러였습니다. AWS에서도 해당 에러에 대한 [해결 방법](https://repost.aws/ko/knowledge-center/ecs-task-exec-format-error)을 간략히 안내하고 있습니다.

ECS 호스트로 Graviton을 선택했으므로 호스트 아키텍처가 ARM64라는 것은 처음부터 알고 있었습니다. 모르고 있었던 건 — *그 사실이 컨테이너 이미지 빌드와 컨테이너 실행 결과에까지 영향을 줄 수 있다*는 점이었습니다.

### 이미지 아키텍처 확인

push한 이미지의 아키텍처를 확인했습니다. 제 로컬 개발 환경은 amd64 기반 노트북이었기 때문에, 기본 `docker build` 결과도 amd64 이미지로 생성되었습니다.

```bash
$ docker build -t test-backend .

$ docker image inspect test-backend --format '{{.Architecture}}'
amd64
```

ARM64 호스트에서는 amd64용으로 빌드된 `java` 바이너리를 실행할 수 없었고,
그 단계에서 `exec format error`가 발생했습니다.

### amd64 이미지가 만들어진 이유

다음 의문은 왜 이미지가 amd64로 빌드되었는가였습니다. 로컬에서는 `docker build`로 만들어 잘 써왔기 때문입니다.

찾아보니 기본 `docker build`는 **별도 플랫폼 지정이 없으면 빌더 호스트의 아키텍처를 기준으로 이미지를 생성한다**는 것이 핵심이었습니다. 로컬은 amd64 환경에서 amd64 이미지를 만들어 amd64로 실행하던 구조라서, 빌드와 실행이 같은 아키텍처라 mismatch가 드러날 일이 없었습니다.

GitHub Actions의 cd.yml도 같은 패턴이었습니다.

```yaml
# 기존 cd.yml의 build 단계
- name: Build and push
  run: |
    docker build -t $REGISTRY/$REPO:$TAG .
    docker push $REGISTRY/$REPO:$TAG
```

`ubuntu-latest` 러너도 amd64 호스트입니다. 같은 명령이 러너 위에서 실행되면 amd64 이미지가 만들어지고, 그것이 ECR로 push되어, ECS의 ARM64 호스트가 pull해 실행하려다 에러가 난 흐름이었습니다.

| 환경 | 빌더 아키텍처 | 실행 아키텍처 | 결과 |
| --- | --- | --- | --- |
| 로컬 | amd64 | amd64 | 정상 |
| GitHub Actions 러너 | amd64 | amd64 (러너 위) | 빌드 정상 |
| ECS Graviton | (러너에서 빌드된 amd64 이미지) | arm64 | exec format error |

## 해결 방법

### cross-platform 빌드 도입

ARM64 호스트에서 동작할 이미지를 amd64 러너에서 만들려면 cross-platform 빌드가 필요합니다. Docker는 이를 위해 `buildx`와 QEMU 조합을 제공합니다. cd.yml에 두 setup action을 추가하고 build 단계를 `docker buildx build`로 교체했습니다.

```yaml
# .github/workflows/cd.yml

- name: Set up QEMU
  uses: docker/setup-qemu-action@v3

- name: Set up Docker Buildx
  uses: docker/setup-buildx-action@v3

- name: Build and push
  run: |
    docker buildx build \
      --platform linux/arm64 \
      -t $REGISTRY/$REPO:$TAG \
      --push \
      .
```

- `setup-qemu-action` — amd64 러너에서도 ARM64 이미지를 빌드할 수 있도록 QEMU 기반 에뮬레이션을 활성화합니다.
- `setup-buildx-action` — 멀티 플랫폼 빌드를 지원하는 buildx 빌더를 활성화합니다. 
- `--platform linux/arm64` — 결과 이미지의 타겟 아키텍처를 명시합니다.
- `--push` — buildx는 로컬 docker 이미지 저장소를 거치지 않고 빌드 결과를 곧바로 레지스트리로 push합니다.

### 적용 결과

cd.yml 변경 후 다시 배포한 뒤, `jq`로 architecture 필드만 추려 확인했습니다.

```bash
docker manifest inspect $REGISTRY/drawe-dev-backend:latest \
  | jq '.manifests[] | select(.platform.architecture != "unknown") | .platform'
```

결과:

```json
{
  "architecture": "arm64",
  "os": "linux"
}
```

- ECR에 push된 이미지의 아키텍처가 `arm64`로 변경됨
- ECS task에서 `exec format error` 사라짐

## 새로 배운 점

- 이미지 빌드는 실행 환경(예: ARM/AMD64)에 직접 영향을 받습니다.
- 로컬에서 정상이어도 운영 환경에서는 아키텍처 차이로 실패할 수 있습니다.
---
title: "ECS에서 unknown command \"sh\"가 발생한 이유 — task definition command와 ENTRYPOINT 동작 방식"
description: "ECS task definition의 command와 Docker ENTRYPOINT/CMD 동작 차이 때문에 발생한 `unknown command \"sh\"` 에러를 추적하고 해결한 과정"
date: 2026-05-08
tags: ["aws", "ecs", "docker", "task definition", "entrypoint", "cmd"]
---

## 진행 사항

ECS 클러스터에 Grafana Alloy 컨테이너를 띄우려 했습니다. Alloy의 config 파일은 환경변수로 주입받는 패턴이었기 때문에, `sh -c`로 디코딩과 실행을 함께 수행하는 형태로 task definition을 작성했습니다.

```hcl
# task definition (요약)
command = [
  "sh", "-c",
  "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec /bin/alloy run /tmp/config.alloy"
]
```

## 발생한 문제

컨테이너가 시작 직후 종료되었습니다. CloudWatch 로그에는 다음 한 줄만 찍혀 있었습니다.

```text
Error: unknown command "sh" for "/bin/alloy"
```

## 원인 분석

### 에러 메시지를 다시 읽기

`unknown command "sh" for "/bin/alloy"` — 이 메시지는 alloy 바이너리가 "sh"라는 *subcommand*를 받았다는 의미였습니다. 즉 컨테이너 안에서 실제로 실행된 명령은 다음과 같은 형태일 가능성이 높았습니다.

```text
/bin/alloy sh -c "echo $ALLOY_CONFIG_B64 | ..."
```

제가 의도한 것은 `sh -c '...'`가 *직접* 실행되어 그 안에서 alloy를 호출하는 것이었지, alloy를 첫 명령으로 두고 그 뒤에 sh를 args로 붙이는 것이 아니었습니다. 두 명령이 합쳐져 실행된 것처럼 보였습니다.

### 이미지의 ENTRYPOINT 확인

`grafana/alloy` 이미지의 Dockerfile에는 `ENTRYPOINT ["/bin/alloy"]`가 설정되어 있었습니다. 이것이 단서였습니다.

Docker의 ENTRYPOINT와 CMD가 함께 동작하는 방식은 AWS Open Source 블로그가 한 줄로 정리하고 있었습니다.

> ENTRYPOINT + CMD = default container command arguments
>
> — [Demystifying ENTRYPOINT and CMD in Docker (AWS Open Source Blog)](https://aws.amazon.com/blogs/opensource/demystifying-entrypoint-cmd-docker/)

즉 Docker는 최종 실행 명령을 다음 형태로 조합합니다.

```text
ENTRYPOINT + CMD(args)
```

즉 Docker는 ENTRYPOINT를 기반으로 CMD를 뒤에 붙여 최종 실행 명령을 구성합니다.
ENTRYPOINT가 정의된 이미지에서는 CMD가 ENTRYPOINT를 대체하지 않고 인자로 전달됩니다.

### ECS task definition의 command가 매핑되는 위치

ECS 공식 문서에 따르면 task definition의 `command`와 `entryPoint`는 각각 Docker의 다음 항목에 매핑됩니다.

| ECS task definition | Docker (`docker run`) | Dockerfile |
| --- | --- | --- |
| `command` | image 뒤 command/args | `CMD` |
| `entryPoint` | `--entrypoint` | `ENTRYPOINT` |

→ [Amazon ECS task definition parameters - AWS Documentation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)

ECS task definition에서 `command`만 지정하면, 이미지의 `ENTRYPOINT`는 그대로 유지되고 그 뒤에 args로 붙습니다. 의도한 실행과 실제 실행을 비교해보면 다음과 같습니다.

```text
# 의도한 실행
/bin/sh -c "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec /bin/alloy run /tmp/config.alloy"

# 실제 실행된 명령
/bin/alloy sh -c "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec /bin/alloy run /tmp/config.alloy"
   └─ 이미지 ENTRYPOINT          └─ task definition command (args로 붙음)
```

alloy 입장에서는 "sh"가 자신의 subcommand로 들어온 것이라, `unknown command "sh"`로 응답한 것이었습니다.

## 해결 방법

### task definition에 entryPoint를 함께 명시

이미지의 ENTRYPOINT를 덮어쓰려면 task definition에 `entryPoint`를 명시해야 했습니다.

```diff
- command = [
-   "sh", "-c",
-   "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec alloy run /tmp/config.alloy"
- ]
+ entryPoint = ["/bin/sh", "-c"]
+ command = [
+   "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec /bin/alloy run /tmp/config.alloy"
+ ]
```

이렇게 하면 컨테이너 안에서 실제 실행되는 명령은 다음 형태가 됩니다.

```text
/bin/sh -c "echo $ALLOY_CONFIG_B64 | base64 -d > /tmp/config.alloy && exec /bin/alloy run /tmp/config.alloy"
   └─ entryPoint (이미지의 ENTRYPOINT를 덮어씀)
```

### 적용 결과

- `unknown command "sh"` 에러 사라짐
- sh가 실행되어 config 디코딩 → alloy 실행 흐름이 의도대로 동작
- alloy 컨테이너가 정상적으로 RUNNING 상태에 진입

## 새로 배운 점

- ECS의 `command`는 Docker의 `CMD`에 해당하며, ENTRYPOINT가 있는 이미지에서는 ENTRYPOINT 뒤에 인자로 붙습니다.
- ENTRYPOINT가 설정된 이미지는 실행 구조 자체를 결정하므로, task definition에서는 반드시 ENTRYPOINT 여부를 확인해야 합니다.
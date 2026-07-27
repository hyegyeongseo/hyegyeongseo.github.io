---
title: "Spring Boot readiness probe DOWN으로 ALB가 503을 받던 이유 — Valkey 비밀번호 위치 어긋남과 user_data 함정"
description: "Spring Boot의 readiness probe가 fail로 잡혀 ALB가 503을 반환한 문제를 추적해, Valkey 비밀번호가 IaC와 EC2 instance 사이에 어긋나 있던 원인을 확인하고 user_data 시점 특성에 따라 instance를 수동 replace한 과정"
date: 2026-05-09
tags: ["aws", "alb", "spring boot", "valkey", "redis", "terraform", "ec2", "user_data", "readiness"]
categories: ["Troubleshooting"]
---

## 진행 사항

dev 환경의 Spring Boot backend가 Valkey(Redis fork)를 종속 리소스로 사용하는 구조였습니다. dev 환경에서는 비용 절약을 위해 ElastiCache 대신 EC2 인스턴스에 Valkey를 직접 설치해 운영했고, Terraform으로 관리하면서 `user_data`에서 `valkey.conf`의 `requirepass`를 주입하는 패턴이었습니다.

Spring Boot Actuator의 readiness probe는 Valkey 연결 상태를 함께 점검하도록 설정되어 있어, Valkey 연결이 실패하면 readiness가 DOWN으로 잡힙니다.

## 발생한 문제

ALB target이 unhealthy로 잡혔습니다. 응답 코드는 503이었습니다.

```bash
$ aws elbv2 describe-target-health --target-group-arn "$TG_ARN"
+-------------+-----------+-----------------------------+--------------------------------+
|  10.0.10.209|  unhealthy| Target.ResponseCodeMismatch | Health checks failed: [503]    |
+-------------+-----------+-----------------------------+--------------------------------+
```

application까지는 요청이 도달하고 있었지만, Spring Boot가 자기 자신을 "준비 안 됨"이라고 응답하고 있는 상태였습니다.

여기서 중요한 건 process 자체는 살아 있었기 때문에 *liveness 문제가 아니라 readiness 문제*였다는 점입니다. application은 실행 중이었지만, 종속 리소스(Valkey)에 연결할 수 없어 트래픽을 받을 준비가 안 된 상태였습니다.

## 원인 분석

### 503의 의미 좁히기 — readiness DOWN

`/actuator/health`가 503을 반환하는 것은 Spring Boot Actuator의 health indicator 중 하나가 DOWN 상태라는 의미입니다. 이 backend의 readiness probe는 Valkey 연결을 포함하고 있었으므로, Valkey 연결이 가장 의심스러웠습니다.

### Valkey 연결을 직접 확인 — backend 컨테이너에서 ping

Spring Boot가 무엇을 보고 "DOWN"이라고 판단했는지를 확인하기 위해, 같은 환경(backend 컨테이너)에서 Valkey에 직접 연결을 시도해봤습니다. ECS Exec(Systems Manager 기반)로 RUNNING 중인 task의 backend 컨테이너에 진입한 뒤, `redis-cli`로 ping을 보냈습니다.

```bash
$ aws ecs execute-command \
    --cluster drawe-dev-cluster \
    --task $TASK_ID \
    --container backend \
    --interactive \
    --command "/bin/sh"

# 컨테이너 내부
$ redis-cli -h $REDIS_HOST -p 6379 -a "$REDIS_PASSWORD" PING
(error) WRONGPASS invalid username-password pair or user is disabled.
```

응답에서 세 가지가 동시에 확인됐습니다.

- ✓ Valkey daemon 살아있음 (응답이 옴)
- ✓ 네트워크 / 보안 그룹 OK (TCP 연결 됨)
- ✗ 비밀번호 불일치 (`WRONGPASS`)

Valkey 자체가 죽거나 네트워크가 끊긴 것이 아니라, **인증 단계에서 거부되고 있다**는 점이 핵심이었습니다.

### 비밀번호가 저장된 위치 점검

backend가 보낸 `$REDIS_PASSWORD`가 Valkey가 기대하는 값과 다르다는 의미였습니다. Valkey 비밀번호는 IaC와 런타임 양쪽에 분산되어 있어서, 각 위치의 값을 하나씩 점검했습니다.

| 위치 | 상태 |
| --- | --- |
| `terraform.tfvars` (`valkey_password`) | ✓ 현재 값 |
| SSM Parameter Store (`/drawe/dev/redis-password`) | ✓ 현재 값 (terraform이 갱신) |
| ECS task definition env (`REDIS_PASSWORD`) | ✓ 현재 값 (SSM에서 주입) |
| Valkey instance `/etc/valkey/valkey.conf` (`requirepass`) | ✗ 옛 값 |

terraform이 관리하는 IaC 측 값과 SSM, ECS env는 모두 같은 값이었는데, Valkey 인스턴스의 실제 설정 파일만 옛 값으로 남아있었습니다. backend는 *현재* 비밀번호로 AUTH를 보냈고, Valkey는 *옛* 비밀번호로 검증하니 `WRONGPASS`가 나올 수밖에 없는 상황이었습니다.

### 왜 instance만 옛 값인가 — user_data의 시점 특성

`/etc/valkey/valkey.conf`는 EC2 instance의 `user_data` 스크립트에서 작성됩니다. terraform의 `aws_instance` 리소스에서 user_data를 통해 `requirepass`를 주입하는 패턴입니다.

dev 환경에서는 단순 AMI 갱신이나 user_data 텍스트 변경만으로 instance가 불필요하게 recreate되는 것을 막기 위해 `ignore_changes`를 사용하고 있었습니다. 이 trade-off가 이번 사건의 한쪽 축이었습니다.

여기서 두 가지가 맞물려 어긋남이 발생했습니다.

```hcl
resource "aws_instance" "valkey" {
  user_data = templatefile(...)

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}
```

EC2 `user_data`는 인스턴스 첫 launch 때 한 번만 실행되며, terraform `lifecycle.ignore_changes`에 포함되면 이후 변경이 있어도 추적·재적용되지 않습니다. 이로 인해 IaC가 관리하는 user_data 값과 실제 instance 내부 상태 사이에 drift가 발생하고, `terraform plan`에서도 표시되지 않으므로 IaC의 시야 밖에서 누적되었습니다.

다만 같은 블록의 코드 코멘트가 이 시나리오를 미리 가리키고 있었습니다 — *"비밀번호 회전이 필요하면 instance 수동 replace"*. user_data 추적을 끄는 trade-off 대신 회전이 필요할 때의 절차를 코드 코멘트로 남겨둔 형태였고, 이번 시점에 해결 경로를 미리 안내해주는 역할을 했습니다.

## 해결 방법

### instance 수동 replace + ECS service 재배포

instance를 새 user_data로 재실행시키려면 instance 자체를 destroy + recreate해야 합니다. `terraform apply -replace=`로 instance를 replace 대상으로 지정해 apply를 돌리고, 그 후 ECS service를 재배포해 backend가 새 Valkey 인스턴스와 통신하도록 했습니다.

```bash
# user_data 변경이므로 기존 instance를 수정하는 방식이 아니라 재생성이 필요
# (terraform taint도 동일한 효과지만 v0.15.2부터 deprecated)
$ terraform apply -replace="aws_instance.valkey"
# instance destroy + recreate, user_data가 현재 password로 실행

$ aws ecs update-service \
    --cluster drawe-dev-cluster \
    --service drawe-dev-backend \
    --task-definition drawe-dev-backend \
    --force-new-deployment
```

### 적용 결과

- 새 Valkey instance의 `/etc/valkey/valkey.conf`에 현재 비밀번호 반영됨
- backend에서 같은 명령으로 다시 ping → `PONG` 응답
- readiness probe UP, ALB target healthy

## 새로 배운 점

- ALB 503은 *"프로세스가 죽었다"*보다 *"프로세스는 살아있는데 종속 리소스 연결이 실패했다"*에 가까운 경우가 많습니다. application 코드부터 의심하기보다 종속 리소스 연결을 먼저 점검하는 게 빠릅니다.
- EC2 instance의 `user_data`는 launch 시점에 한 번만 실행됩니다. IaC가 관리하는 user_data 값이 변경되어도 instance 내부 상태는 그대로이므로, 변경 반영을 위해서는 instance를 명시적으로 replace(`terraform apply -replace=` 등)해야 합니다. 특히 `lifecycle.ignore_changes`에 `user_data`가 포함되어 있으면 terraform은 변경을 추적조차 하지 않아 IaC 시야 밖에서 어긋남이 누적될 수 있습니다.
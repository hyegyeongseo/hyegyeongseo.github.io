---
title: "AWS dev 환경 VPC 비용 최적화 — Interface Endpoint를 NAT Instance로 우회한 의사결정"
description: "AWS dev 환경 VPC 비용 최적화 — Interface VPC Endpoint를 NAT Instance로 전환한 의사결정과 운영 이슈 분석"
date: 2026-05-17
tags: ["aws", "vpc-endpoint", "cost-optimization"]
categories: ["Infra Observability"]
---

## 시작 — VPC 카테고리에 $23.74

AWS 청구서 화면에서 한 줄이 눈에 띄었습니다.

> Amazon Virtual Private Cloud: **$23.74**

VPC 리소스 자체(subnet, route table, IGW)는 무료라고 알고 있어서 의문이 생겼습니다. *"VPC 자체로 돈이 나가나?"*

답은 VPC 카테고리 *아래* 묶이는 유료 컴포넌트들 — **Interface VPC Endpoint**, **Public IPv4** 였습니다.

## 비용 분해 — Bills 화면 그대로 펼치기

Billing → Bills → 해당 월을 선택하면 서비스 단위로 *수량과 단가*까지 다 나옵니다. VPC 카테고리만 펼쳐 보니 다음과 같았습니다.

```text
Virtual Private Cloud                                              USD 23.74
└─ Asia Pacific (Seoul)
   ├─ Amazon Virtual Private Cloud Public IPv4 Addresses           USD  3.15
   │  ├─ $0.005 per Idle public IPv4 address per hour     48.596 Hrs  $0.24
   │  └─ $0.005 per In-use public IPv4 address per hour  582.549 Hrs  $2.91
   └─ Amazon Virtual Private Cloud VpcEndpoint                     USD 20.59
      ├─ $0.01  per GB data processed by VPC Endpoints     0.37 GB    $0.00
      └─ $0.013 per VPC Endpoint Hour                  1,584    Hrs  $20.59
```

압도적 1위는 `VPC Endpoint Hour` — **$20.59**. `terraform-dev/vpc-endpoints.tf`를 확인해보니 원인이 명확했습니다.

```bash
$ grep -E '^resource|vpc_endpoint_type' terraform-dev/vpc-endpoints.tf
resource "aws_vpc_endpoint" "s3" {        # Gateway — 무료
  vpc_endpoint_type = "Gateway"
resource "aws_vpc_endpoint" "ecr_api" {   # Interface — 유료
  vpc_endpoint_type = "Interface"
resource "aws_vpc_endpoint" "ecr_dkr" {   # Interface — 유료
  vpc_endpoint_type = "Interface"
resource "aws_vpc_endpoint" "logs" {      # Interface — 유료
  vpc_endpoint_type = "Interface"
```

3개의 Interface Endpoint(ECR API, ECR Docker, CloudWatch Logs)가 AZ 2개씩 떠 있는 구조 — 즉 **ENI 6개**. 서울 리전 단가는 ENI(=AZ)당 **시간당 $0.013**.

### 1,584 시간 — endpoint는 "끌 수 없다"

빌에 찍힌 1,584 시간을 ENI 수로 나누면:

```
1,584 hr ÷ 6 ENI = ENI당 264 시간 ≈ 11일치 연속 운영
```

여기서 *중요한 사실 하나*: **Interface VPC Endpoint는 정지 상태가 없습니다.** 한 번 만들어두면 `Available` 상태로 계속 과금되고, *지우는 것 외에는 멈출 방법이 없습니다*. 즉 dev 환경에서 stop 스크립트로 ECS / NAT / RDS / Valkey 등을 다 내려도 endpoint는 그대로 시간을 쌓아요. 264 시간은 *endpoint가 살아있던 전 기간*에 해당합니다.

이게 dev 환경에서 endpoint가 *상대적으로 비싸 보이는* 본질적인 이유입니다 — dev에서 다른 자원들은 stop 스크립트로 운영비를 깎아내는데, endpoint만 24/7로 누적되거든요. 24시간 가동 가정의 이론 최대치는:

```
6 ENI × 730 hr × $0.013 ≈ $56.94 / 월
```

### VPC 카테고리 안의 진짜 분해

| 항목 | 실측 | 비고 |
| --- | --- | --- |
| Interface Endpoint × 3 (ENI 6개) | **$20.59** (1,584 hr) | ★ 제거 대상. 24/7 이론 최대치 $56.94 |
| VPC Endpoint Data Processing | $0.00 (0.37 GB) | dev 트래픽이 작아 무시 가능 |
| Public IPv4 In-use (ALB × 2 AZ + NAT EIP 등) | $2.91 (582 hr) | 손 댈 수 없음. 자원 가동 시간만큼만 |
| Public IPv4 Idle | $0.24 (48 hr) | 운영 사이클 중 일시적 unassociate 흔적 |
| NAT Instance × 1 (t4g.micro) | (EC2 카테고리에 별도) | 유지. endpoint 제거 시 모든 outbound 수용 |

ALB-managed EIP는 internet-facing ALB가 각 AZ의 ENI에 자동 할당한 것이라 사용자가 release 불가. 외부 트래픽을 받으려면 필수 자원이라 손대지 않습니다. *손볼 곳은 Interface Endpoint 3개 한 묶음*이었습니다.

## 의사결정 — Interface Endpoint vs NAT의 트레이드오프

두 방식 모두 *private subnet에서 AWS 서비스에 도달하는* 수단이지만, 강점이 다릅니다.

| 측면 | Interface Endpoint | NAT (Instance/Gateway) |
| --- | --- | --- |
| 가용성 | AZ별 별도 ENI, AZ 장애 무관 | 단일 NAT은 SPOF, AZ별 NAT은 비용 증가 |
| 보안 감사 | AWS 트래픽이 인터넷을 경유하지 않음 | NAT 거쳐 인터넷으로 |
| 트래픽 비용 | endpoint 시간당 + 데이터 처리비 | NAT 시간당 + 데이터 처리비 |
| 고정 비용 | AZ당 $0.013/시간 × 사용한 서비스 수 (24/7 누적) | NAT Instance: ~$6/월, NAT Gateway: ~$43/월 |
| *stop 가능 여부* | **불가** — 지우는 것만이 정지 | NAT Instance: stop 가능 / NAT Gateway: 불가 |

### prod와 dev의 가용성 비대칭

**prod 환경**에서는 Interface Endpoint가 정당화됩니다.

- AZ 분산된 endpoint가 단일 NAT보다 가용성이 좋음
- 대용량 트래픽에서는 NAT 데이터 처리비가 endpoint 고정 비용을 추월
- 보안 감사상 AWS 서비스 트래픽이 NAT을 안 거치는 게 선호됨
- 24/7 가동되는 워크로드라 "endpoint는 끌 수 없다"는 단점이 단점이 아님

**dev 환경**은 가치가 다릅니다.

- 트래픽 자체가 작음 (실측 데이터 처리비 $0.00)
- SLA가 느슨 — AZ 장애 시 잠시 끊겨도 OK
- *나머지 자원이 stop 스크립트로 절약되는 상황*에서 endpoint만 24/7 과금되어 상대 비중이 자연스럽게 커짐

같은 자원이라도 dev와 prod에서 *얻는 가치가 다릅니다*. **prod의 best practice가 dev에서는 비용 낭비가 되는 영역**입니다.

**결정**: dev에서는 Interface Endpoint 3개를 제거하고 기존 NAT Instance로 우회. prod는 손대지 않음 (Interface Endpoint 유지).

## 실행 — 정지 윈도우 활용

dev는 마침 stop 스크립트로 다 내려가 있는 상태였습니다 — ECS 0, ASG 0, NAT/Valkey/RDS 정지. **VPC Endpoint를 사용할 클라이언트가 하나도 없는 시점**이라 전환 비용이 가장 낮은 순간이었습니다.

### vpc-endpoints.tf — S3 Gateway만 남기고 주석 처리

S3 Gateway Endpoint는 무료이고 ECR 이미지 레이어가 S3에 저장되므로 유지. 나머지 3개 Interface + 그들이 공유하던 SG 1개 = 정확히 4개만 주석 처리.

```diff
  # ── S3 Gateway Endpoint (무료) — 유지
  resource "aws_vpc_endpoint" "s3" {
    vpc_id            = aws_vpc.main.id
    vpc_endpoint_type = "Gateway"
    route_table_ids   = [aws_route_table.private.id]
  }

- resource "aws_security_group" "vpce"   { ... }
- resource "aws_vpc_endpoint"  "ecr_api" { ... Interface }
- resource "aws_vpc_endpoint"  "ecr_dkr" { ... Interface }
- resource "aws_vpc_endpoint"  "logs"    { ... Interface }
```

```bash
$ terraform plan
# aws_security_group.vpce       will be destroyed
# aws_vpc_endpoint.ecr_api      will be destroyed
# aws_vpc_endpoint.ecr_dkr      will be destroyed
# aws_vpc_endpoint.logs         will be destroyed
Plan: 0 to add, 0 to change, 4 to destroy.

$ terraform apply
Apply complete! Resources: 0 added, 0 changed, 4 destroyed.
```

### 왜 정지 윈도우가 가장 안전한 타이밍인가

서비스가 돌아가는 중에 VPC Endpoint를 삭제하면, 기존에 연결되어 있던 시스템들이 이전 주소(사설 IP)를 계속 사용하다가 실패할 수 있습니다.

하지만 서비스가 이미 멈춰 있는 상태에서는 요청을 보내는 주체가 없어서, 이런 연결 문제나 캐시 문제 없이 안전하게 변경을 적용할 수 있습니다.

## 정리

| 결정 | 이유 |
| --- | --- |
| dev의 Interface Endpoint 3개 제거 | 실측 $20.59 / 이론 최대 $56.94. 24/7 누적되는 유일한 자원이라 dev에서 상대 비중이 큼 |
| prod는 손대지 않음 | 가용성·보안 감사·트래픽 규모상 Interface Endpoint가 정당화됨 |
| 정지 윈도우에 변경 | DNS 캐시 / 연결 풀 같은 stateful 자원의 전이 비용 zero |
| S3 Gateway Endpoint는 유지 | 무료이고 ECR 이미지 레이어가 S3에 저장 |

핵심 통찰 두 가지를 남깁니다.

1. **Interface Endpoint는 정지 상태가 없다.** stop 스크립트로 다른 자원을 깎는 dev 환경에서 endpoint만 24/7 누적되며 상대 비중이 자연스럽게 커집니다. 이 *비대칭*이 "dev에서 endpoint를 빼라"는 결정을 이끌어내는 핵심 논리입니다.
2. **환경별 자원 가치는 비대칭이다.** 같은 자원이라도 dev와 prod에서 가치가 다릅니다. prod의 best practice를 dev에 그대로 가져가면 비용 낭비가 되는 영역이 존재합니다.

---

VPC endpoint 제거 및 dev 재시작 직후 통신 확인을 위해 SSH 대신 SSM Session Manager를 사용하려 했으나 SSM이 응답하지 않는 문제가 발생했습니다. 이는 NAT과는 무관한 AL2023 minimal AMI 구성 이슈였습니다. 해당 내용은 [별도 글](/posts/nat-ssm-al2023-minimal-ami/)에서 다룹니다.
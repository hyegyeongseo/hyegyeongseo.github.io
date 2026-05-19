---
title: "NAT은 무죄 — SSM이 연결되지 않던 진짜 이유 (AL2023 minimal AMI 함정)"
description: "NAT 문제처럼 보였던 SSM 연결 실패를 추적하다, AL2023 minimal AMI에 amazon-ssm-agent가 없다는 함정을 발견한 과정"
date: 2026-05-17
tags: ["aws", "ssm", "nat", "al2023", "ami"]
---

## 진행 사항

dev 환경의 VPC Interface Endpoint 3개를 제거하고 기존 NAT Instance로 outbound를 우회시키는 작업이 [별도 글](/posts/aws-vpc-endpoint-nat-cost-optimization/)에서 마무리됐습니다. 변경은 정지 윈도우에서 깔끔하게 4개 destroy만 일어났고, 다음 단계는 단순히 dev를 다시 켜는 것이었습니다.

NAT, Valkey, RDS 차례로 start. NAT을 거친 outbound가 정상인지를 *SSH 대신 SSM Session Manager로 검증*해 보기로 했습니다 — SSM Agent의 등록 자체가 *NAT 경로를 통한 공개 SSM endpoint 도달*에 성공해야만 일어나는 일이라, 간접 검증이 되거든요. 콘솔에서 EC2 → Valkey → SSM Session Manager 탭을 열어봤습니다.

> **Ping status: ✗ 오프라인**
> Last ping time: —
> Agent version: —

## 발생한 문제

NAT, Valkey 양쪽 모두 SSM에 *한 번도 연결되지 못한 상태*였습니다. 콘솔의 *Last ping time*과 *Agent version*이 모두 `—`로 비어 있다는 것이 핵심이었습니다 — 등록 이력이 있었다면 *과거 버전 정보*라도 남았을 텐데, 그 자리가 비어 있다는 건 *Agent가 자신을 한 번도 등록하지 못했다*는 뜻이었습니다. CLI에서도 동일.

```bash
$ aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=i-0e5ab796e2b090ce4" \
    --query 'InstanceInformationList[0].PingStatus' --output text
None
```

NAT을 통한 outbound가 깨졌다는 의심이 들었습니다. 어쩌면 endpoint 제거가 부수적으로 영향을 미쳤거나, NAT 재시작 과정에서 무언가 휘발됐을 수도. 일단 외부에서 확인 가능한 모든 항목부터 점검했습니다.

## 원인 분석

private subnet의 인스턴스가 SSM에 못 닿는 증상은 *NAT 경로가 깨졌을 때의 전형적인 모습*이기도 합니다. 그래서 첫 가설은 자연스럽게 NAT으로 수렴했고, 그 가설이 무너지기까지 두 시간이 걸렸습니다.

### 가설 1 — NAT의 iptables가 stop/start 사이에 휘발

NAT Instance의 `user_data`는 부팅 시 `iptables -t nat -A POSTROUTING -j MASQUERADE` 같은 룰을 깝니다. 그런데 `user_data`는 *첫 launch 때 한 번만 실행*되니, stop/start 사이에 iptables가 어떤 이유로 휘발됐다면 룰이 빠진 상태일 수 있습니다.

외부에서 볼 수 있는 NAT의 속성은 다 정상이었습니다.

```bash
$ aws ec2 describe-instances --instance-ids i-... \
    --query '...{State, SrcDstCheck, PubIP}'
{ "State":       "running",     ✓
  "SrcDstCheck": false,         ✓  NAT의 핵심 설정
  "PubIP":       "43.x.x.x" }   ✓

$ aws ec2 describe-route-tables ...   # 0.0.0.0/0 → NAT ENI 정확
$ aws ec2 describe-network-acls ...   # default (전체 허용)
```

외부 진단으로는 결점 0개. 그러나 SSM은 여전히 오프라인이었습니다. *내부의 iptables가 휘발됐을 것*이라는 가설이 점점 굳어갔습니다.

`lifecycle { ignore_changes = [user_data] }`로 인해 `terraform apply`로는 user_data가 재실행되지 않습니다. `terraform taint`로 인스턴스를 destroy + create 시켜 fresh한 user_data를 돌리기로 했습니다.

```bash
$ terraform taint aws_instance.nat
$ terraform apply
# 1 added, 2 changed, 1 destroyed.
# - aws_instance.nat → destroy + create
# - aws_route_table.private → update (새 NAT ENI를 가리키도록)
```

새 NAT으로 교체된 뒤 Valkey의 SSM 상태를 다시 확인했습니다.

```bash
$ aws ssm describe-instance-information ...
null
```

**여전히 오프라인이었습니다.** 두 시간이 지나가고 있었습니다.

### 시도 2 — NAT을 SSM 진입점으로 만들기 위해 IAM attach

Valkey의 SSM이 안 되니 *다른 진입로*가 필요했습니다. NAT은 원래 IAM role이 없어서 SSM이 안 되는 게 *설계상 정상* — 거꾸로 말하면 *IAM만 채워주면 NAT을 SSM 디버깅 진입점으로 쓸 수 있다*는 뜻이기도 했습니다. Valkey의 SSM profile을 NAT에 임시로 attach하면, 2~3분 안에 NAT의 SSM Agent가 credential을 받고 등록될 거라는 기대.

```bash
$ aws ec2 associate-iam-instance-profile \
    --instance-id i-0a1f9431652a16fc4 \
    --iam-instance-profile Name=drawe-dev-valkey-ssm-profile
"State": "associating"  ✓

$ for i in {1..20}; do
    STATUS=$(aws ssm describe-instance-information ... --output text)
    echo "attempt $i: $STATUS"
    [ "$STATUS" = "Online" ] && break
    sleep 15
  done
attempt 1:  None
attempt 2:  None
...
attempt 20: None    # 5분 폴링, 끝까지 None
```

**IAM을 분명히 attach했는데 5분 폴링이 끝까지 `None`이었습니다.** *"IAM 넣었는데 왜 안 돼지?"* — IAM이라는 *마지막으로 채울 수 있는 외부 변수*까지 정상으로 만들었는데도 SSM이 올라오지 않았습니다. 외부에서 만질 수 있는 건 다 만진 셈이었습니다.

### 같은 도구로 같은 도구를 디버깅할 수 없는 함정

여기서 자각한 게 있었습니다. **"SSM 안에서 SSM Agent 문제를 진단"하는 건 본질적으로 불가능**했습니다. SSM이 동작하면 진단할 게 없고, SSM이 안 동작하면 진단할 수단이 없으니까요. *같은 layer에서 자신을 디버깅할 수 없는* 함정입니다.

한 단계 *옆*의 진입 수단이 필요했습니다 — EC2 Instance Connect는 권한이 없었고 직렬 콘솔도 차단. 남은 선택은 SSH였습니다.

### SG 22 임시 개방 — 안에 들여다보기

*"SSH 포트 잠깐 여는 게 보안상 찝찝하다"*는 본능적 거부감이 있었습니다. 하지만 이미 두 시간을 가설 검증에 썼고, *내부를 안 보면 다음 가설을 세울 수가 없는 상황*이었습니다. 내 IP만 정확히 허용하고 작업 후 즉시 revoke하면 노출은 1분 미만.

```bash
$ MYIP=$(curl -s https://checkip.amazonaws.com)
$ aws ec2 authorize-security-group-ingress \
    --group-id sg-0322e7bec13df4de7 \
    --protocol tcp --port 22 \
    --cidr ${MYIP}/32

$ ssh -i ~/drawe-dev-keypair.pem ec2-user@43.203.121.131
       , #_
      ~\_  ####_         Amazon Linux 2023
     ~~  \_#####\
[ec2-user@ip-10-0-1-240 ~]$    ✓ 진입 성공
```

### 470 packets — NAT은 처음부터 정상이었다

진입하자마자 한 묶음으로 점검했습니다.

```bash
[nat]$ which amazon-ssm-agent && rpm -q amazon-ssm-agent
/usr/bin/which: no amazon-ssm-agent in (...)
package amazon-ssm-agent is not installed       ★ 핵심 단서

[nat]$ sudo systemctl status amazon-ssm-agent
Unit amazon-ssm-agent.service could not be found.

[nat]$ sudo iptables -t nat -L POSTROUTING -n -v
Chain POSTROUTING (policy ACCEPT 0 packets, 0 bytes)
 pkts bytes target     prot in   out
  470 34161 MASQUERADE  all  *    ens5   10.0.0.0/16   0.0.0.0/0

[nat]$ sysctl net.ipv4.ip_forward
net.ipv4.ip_forward = 1                          ✓

[nat]$ sudo tail /var/log/cloud-init-output.log
Cloud-init v. 22.2.2 finished at Sun, 17 May 2026 04:22:13 +0000.   ✓
```

한 묶음의 출력에서 두 가지가 동시에 드러났습니다.

**(1) NAT은 처음부터 정상이었습니다.** `iptables`의 MASQUERADE 룰이 살아있고, *짧은 관측 구간 동안 이미 470 packets / 34KB가 통과한 흔적*이 쌓여 있었습니다. SSM Agent는 없었지만 Valkey의 dnf, NTP 같은 *agent와 무관한 배경 outbound*가 여전히 NAT을 거치고 있었던 것 — *NAT이 죽었다면 나올 수 없는 수치*이고, 최소한 *forward path는 정상*임을 의미했습니다. user_data도 정상 완료. 두 시간의 NAT 가설이 이 출력 한 묶음에 무너졌습니다.

같은 출력은 *endpoint 제거 영향* 가설도 함께 기각해 줬습니다. dev에는 *처음부터 SSM endpoint가 존재한 적이 없고* (`terraform-dev/vpc-endpoints.tf`에 ECR API/DKR/Logs 3개만 있었지 SSM endpoint는 없음), SSM Agent는 원래부터 NAT을 통해 공개 SSM endpoint로 가도록 설계되어 있었습니다. *endpoint 제거가 SSM 경로에 영향을 줄 물리적 통로 자체가 없었던* 셈입니다. — 이 사실은 코드만 봐도 진단 *시작 전*에 확인할 수 있었습니다.

**(2) `amazon-ssm-agent`가 아예 설치되어 있지 않았습니다.** IAM도 NAT도 endpoint도 무관한 문제였습니다. *Agent 자체가 없으니* 어떤 변경을 해도 SSM이 올 수 없는 상황이었던 것입니다.

### 진짜 원인 — AL2023의 minimal 변형 함정

`vpc.tf`와 `valkey.tf`가 모두 `data.aws_ami.al2023.id`를 사용해 AMI를 잡고 있었습니다. AL2023에는 두 변형이 있고, 각각 다른 도구 묶음을 포함합니다.

| 변형 | name pattern | 크기 | 사전 설치된 운영 도구 |
| --- | --- | --- | --- |
| Standard | `al2023-ami-2023.*-kernel-*-{arch}` | 약 800MB | **SSM Agent, cron, 일반 운영 도구 포함** |
| Minimal | `al2023-ami-minimal-2023.*-kernel-*-{arch}` | 약 300MB | **미포함**. 컨테이너 베이스나 경량 워크로드용 |

문제의 data source는 다음과 비슷한 형태였습니다.

```hcl
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*"]    # ← minimal도 함께 매칭됨
  }
}
```

`al2023-ami-*` 패턴은 standard와 minimal을 *모두 매칭*합니다. `most_recent = true`가 그 시점의 최신 AMI를 골라주는데, AWS가 minimal 변형을 더 최근에 release하면 *그게 선택됩니다*. NAT과 Valkey가 어느 시점에 minimal AMI로 만들어졌고, 그 결과 양쪽 다 SSM Agent가 없는 상태였습니다. 코드 한 줄의 filter pattern이 *운영 도구의 존재 자체를 바꾼* 셈입니다.

## 해결 방법

### SSM Agent 수동 설치 — NAT에 직접

NAT 안에 SSH로 들어와 있는 김에 SSM Agent를 설치했습니다. 다음 디버깅 때부터는 SSH 임시 개방 없이 들어갈 수 있도록.

```bash
[nat]$ sudo dnf install -y \
    https://s3.ap-northeast-2.amazonaws.com/amazon-ssm-ap-northeast-2/\
latest/linux_arm64/amazon-ssm-agent.rpm

[nat]$ sudo systemctl enable --now amazon-ssm-agent
[nat]$ sudo systemctl status amazon-ssm-agent --no-pager | head -3
Active: active (running)    ✓
```

### NAT을 jump host 삼아 Valkey에도 설치

Valkey는 private subnet에 있어 외부에서 직접 SSH 불가. NAT을 jump host로 활용해 진입했습니다.

```bash
# 로컬에서 ssh-agent에 키 등록, forwarding(-A)으로 진입
$ eval "$(ssh-agent)" && ssh-add ~/drawe-dev-keypair.pem
$ ssh -A -i ~/drawe-dev-keypair.pem ec2-user@43.203.121.131

# NAT 안에서 Valkey로 — 키 파일 없이 forwarded agent가 인증
[nat]$ ssh ec2-user@10.0.10.36
[valkey]$ sudo dnf install -y amazon-ssm-agent
[valkey]$ sudo systemctl enable --now amazon-ssm-agent
```

### 작업 후 SG 22 즉시 revoke

```bash
$ aws ec2 revoke-security-group-ingress \
    --group-id sg-0322e7bec13df4de7 \
    --protocol tcp --port 22 \
    --cidr ${MYIP}/32
```

### AMI data source 명시화 (후속)

근본 원인을 막으려면 `data.aws_ami.al2023`의 name filter에서 minimal을 명시적으로 배제해야 합니다.

```hcl
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-arm64"]
    #         ↑ minimal(al2023-ami-minimal-*)을 자연스럽게 배제
  }
}
```

임시 설치는 *지금의 이 인스턴스*만 해결하지만, AMI 명시화는 *다음 인스턴스를 재생성할 때*까지 막아줍니다. 코드만 봐도 *어떤 AMI*를 가져오는지 명확해진다는 점도 부수 효과입니다.

## 새로 배운 점

- **같은 도구로 같은 도구를 디버깅할 수 없는 함정**이 있습니다. SSM이 동작 안 할 때 SSM으로 들어가서 진단하려는 시도는 본질적으로 닫혀 있습니다. *한 단계 옆의 진입 수단(SSH, 직렬 콘솔, Instance Connect)을 미리 확보*해두는 게 진단의 전제조건입니다. 임시 SSH 개방의 보안 부담은 1분 미만이지만, 그것 없이 가설을 검증할 때의 비용은 시간 단위였습니다.

- **가설은 증상이 아니라 경로로 먼저 걸러낼 수 있습니다.** dev 환경의 SSM은 처음부터 NAT 경유 구조였고 endpoint는 존재한 적이 없었습니다. `vpc-endpoints.tf`를 먼저 확인했다면 *"endpoint 제거 영향"* 가설은 초기에 기각할 수 있었습니다.

- **AMI data source는 변형을 명시적으로 고정해야 합니다.** `most_recent = true`와 느슨한 filter 조합은 standard/minimal 같은 서로 다른 변형을 시점마다 가져올 수 있고, 그 차이가 운영 도구(ssm-agent)의 존재 자체를 바꿀 수 있습니다.

- **외부 신호와 내부 상태 사이에는 간극이 있습니다.** NAT route, ENI, SG, NACL이 모두 정상이어도 인스턴스 내부 agent는 존재하지 않을 수 있습니다. 클라우드 디버깅에서는 외부 지표만으로 내부 상태를 단정하지 않는 습관이 중요합니다.
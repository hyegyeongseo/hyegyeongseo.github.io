---
title: "AWS에 장기 자격증명을 하나도 두지 않기 — OIDC · IRSA · ESO"
description: "CI도 파드도 Access Key 없이 인증하게 만든 과정. 시크릿은 레포가 아니라 SSM에 두고, IaC가 그 값을 덮어쓰지 않도록 무장해제하기까지."
date: 2026-07-29
tags: ["aws", "iam", "oidc", "irsa", "external-secrets", "eks", "terraform", "drawe"]
categories: ["Infra Observability"]
---

## 원칙 — 키를 어디에도 저장하지 않는다

인프라를 짜면서 자격증명이 필요한 곳이 셋이었습니다.

- **CI**가 AWS에 이미지를 올리고 배포해야 합니다
- **파드**가 S3·Bedrock·Cost Explorer에 접근해야 합니다
- **시크릿 18종**(DB·JWT·외부 API 키 등)이 여러 서비스에 주입돼야 합니다

가장 쉬운 방법은 IAM User를 만들어 Access Key를 발급하고, CI는 그걸 GitHub Secrets에, 파드는 환경변수에 넣는 것입니다. 동작은 합니다. 대신 **유출되면 되돌릴 방법이 로테이션밖에 없고, 유출됐는지 알기도 어렵습니다.**

그래서 원칙을 하나 세웠습니다. **키를 어디에도 저장하지 않는다.** 저장된 키가 없으면 유출될 키도 없습니다.

이 글은 그 원칙을 세 축으로 구현한 기록입니다 — CI는 OIDC, 파드는 IRSA, 시크릿은 ESO.

## CI — Access Key 대신 OIDC

GitHub Actions는 워크플로 실행마다 **자기 자신을 증명하는 짧은 수명의 토큰**을 발급할 수 있습니다. AWS가 그 토큰을 신뢰하도록 등록해두면, CI는 키 없이 역할을 assume할 수 있습니다.

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
```

중요한 건 provider 등록이 아니라 **신뢰 조건을 얼마나 좁히느냐**입니다. `repo:*` 같은 느슨한 조건이면 그 조직의 아무 레포나 prod 배포 역할을 가져갈 수 있습니다.

```hcl
data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # prod 는 main branch 또는 prod environment 에서만 deploy
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = flatten([
        for repo in var.github_repos : [
          "repo:${var.github_owner}/${repo}:ref:refs/heads/main",
          "repo:${var.github_owner}/${repo}:environment:prod",
        ]
      ])
    }
  }
}
```

`sub` 클레임에 **레포·브랜치·환경**이 함께 들어갑니다. 즉 이 역할을 가져가려면 *우리 레포 + main 브랜치* 이거나 *우리 레포 + prod 환경* 이어야 합니다. feature 브랜치나 포크에서는 assume 자체가 실패합니다.

![신뢰 정책 — sub 조건에 레포·브랜치·환경이 함께 걸려 있다](/images/posts/security-oidc-trust-policy.png)

워크플로 쪽은 이렇게 짧습니다.

```yaml
permissions:
  id-token: write   # AWS OIDC
  contents: write

- name: Configure AWS credentials (OIDC)
  uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/drawe-prod-github-deploy-role
    aws-region: ap-northeast-2
```

`id-token: write` 권한이 없으면 토큰 자체가 발급되지 않습니다. **비밀은 없고, 권한 선언만 있습니다.**

## 파드 — 노드 Role 공유 대신 IRSA

EKS에서 파드에 AWS 권한을 주는 가장 쉬운 방법은 노드 IAM Role에 정책을 붙이는 것입니다. 그런데 그러면 **그 노드 위의 모든 파드가 같은 권한을 갖습니다.** backend가 필요한 S3 권한을 fastapi도, 심지어 관측 에이전트도 갖게 됩니다.

IRSA는 그 단위를 **ServiceAccount**로 내립니다. 클러스터 자체가 OIDC provider가 되고, 신뢰 조건에 SA 이름을 박습니다.

```hcl
condition {
  test     = "StringEquals"
  variable = "${local.backend_oidc_url}:sub"
  values   = ["system:serviceaccount:${var.project}-${var.env}:backend"]
}
```

`system:serviceaccount:<네임스페이스>:<SA 이름>` — 이 SA를 쓰는 파드만 이 역할을 가져갑니다. 다른 SA를 쓰는 파드는 같은 노드에 있어도 못 씁니다.

![ServiceAccount의 role-arn 어노테이션 — 여기가 파드와 IAM 역할을 잇는 지점](/images/posts/security-irsa-sa-annotation.png)

권한도 필요한 것만 붙였습니다. 예를 들어 어드민 화면의 비용 탭이 Cost Explorer를 호출해야 했는데, 이렇게 좁혔습니다.

```hcl
statement {
  sid       = "CostExplorerReadOnly"
  effect    = "Allow"
  actions   = ["ce:GetCostAndUsage"]   # GetCostForecast 는 미사용 → 부여 안 함
  resources = ["*"]                    # CE 는 리소스 레벨 권한을 지원하지 않는다
}
```

`resources = ["*"]`가 마음에 걸려서 좁혀보려 했는데, **Cost Explorer는 리소스 레벨 권한을 지원하지 않습니다.** 서비스가 지원하지 않는 건 IAM으로 못 좁힙니다. 대신 액션 쪽을 실제 호출하는 하나로만 제한하고, 그 사실을 코드 주석에 남겼습니다. 좁힐 수 없는 축이 있으면 **좁힐 수 있는 다른 축을 최대한 좁히는 것**이 차선입니다.

## 시크릿 — 레포 대신 SSM, 주입은 ESO

시크릿 18종을 어디에 둘지가 남았습니다. 후보는 셋이었습니다.

| 안 | 문제 |
| --- | --- |
| 매니페스트에 Secret 직접 작성 | base64는 암호화가 아니다. 레포에 평문이 남는 것과 같음 |
| Sealed Secrets | 암호화는 되지만 **복호화 키를 클러스터가 갖는다** — 그 키의 백업·로테이션을 또 관리해야 함 |
| **SSM Parameter Store + ESO** | 원본은 AWS에, 레포엔 경로만. 채택 |

External Secrets Operator는 SSM을 읽어 K8s Secret으로 동기화합니다. 매니페스트에는 **"어디서 가져올지"만 있고 "값"은 없습니다.**

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: backend-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-ssm
    kind: ClusterSecretStore
  target:
    name: drawe-backend-secrets
    creationPolicy: Owner
  data:
    - secretKey: DB_PASSWORD
      remoteRef: { key: /drawe/prod/db-password }
    - secretKey: JWT_SECRET
      remoteRef: { key: /drawe/prod/jwt-secret }
    # ... 총 18종, 전부 SSM 경로만
```

`refreshInterval: 1h`이라 SSM 값이 바뀌면 한 시간 안에 K8s Secret이 따라옵니다.

![실제 클러스터의 ExternalSecret — 값은 없고 SSM 경로만 있고, status는 SecretSynced=True](/images/posts/security-eso-externalsecret.png)

ESO 자신이 SSM을 읽을 권한도 IRSA로 받는데, 여기도 경로를 좁혔습니다.

```hcl
statement {
  sid       = "ReadSSM"
  actions   = ["ssm:GetParameter", "ssm:GetParameters",
               "ssm:GetParametersByPath", "ssm:DescribeParameters"]
  resources = ["arn:aws:ssm:${region}:${account}:parameter${var.ssm_path_prefix}/*"]
}

statement {
  sid       = "DecryptSecureString"
  actions   = ["kms:Decrypt"]
  resources = ["*"]
  condition {
    test     = "StringEquals"
    variable = "kms:ViaService"
    values   = ["ssm.${var.aws_region}.amazonaws.com"]   # SSM 경유일 때만
  }
}
```

`kms:Decrypt`도 리소스를 좁히기 어려운 축이었습니다(SecureString이 쓰는 KMS 키를 미리 특정하기 번거로움). 대신 `kms:ViaService` 조건으로 **SSM을 경유한 복호화만** 허용했습니다. 이 역할로 다른 경로의 KMS 복호화는 못 합니다. 여기서도 좁힐 수 있는 축을 골라 좁힌 셈입니다.

결과적으로 **git에도, 매니페스트에도 시크릿 평문이 한 줄도 없습니다.** 실수로 커밋할 경로 자체가 사라집니다.

## 이 구조는 ECS 시절에 데인 경험에서 나왔다

처음부터 이렇게 설계한 건 아닙니다. ECS를 쓰던 시절, 시크릿 한 줄을 배선하는 "5분짜리" 작업이 며칠짜리가 된 적이 있습니다.

`bria.api-key=${BRIA_API_KEY}` — 이 placeholder를 채우려고 Parameter Store에 값을 넣었는데 컨테이너 env가 안 채워졌습니다. 이유는 **진실이 세 곳에 나뉘어 있었기 때문**입니다.

1. **Parameter Store** — KV 저장소일 뿐, 스스로 주입하지 않습니다
2. **ECS task definition의 `secrets` 매핑** — SSM ARN ↔ env 이름 매핑을 따로 명시해야 주입됩니다
3. **Terraform** — 위 매핑을 콘솔/CLI로 손수 넣었더니, 코드의 진실과 실제가 어긋났습니다

특히 3번이 오래 남았습니다. 기능은 이미 동작하는데 `terraform plan`에 diff가 남아 있었거든요. "동작하니까 됐지"로 넘길 수도 있었지만, 그 diff는 **다음 apply 때 터질 뇌관**입니다. `terraform import`로 state를 실제와 맞추고 나서야 끝났습니다.

> **기능 일치와 state 일치는 다릅니다.** 동작한다는 사실이 IaC가 현실을 알고 있다는 뜻은 아닙니다.

ESO를 고른 건 이 경험 때문입니다. **주입 경로에 사람 손이 들어가는 단계를 없애고 싶었습니다.** ESO 구조에서는 SSM에 값을 넣는 것 하나로 끝나고, 매핑은 매니페스트에 선언돼 git이 관리합니다.

## 지뢰 — TF_VAR 없이 plan하면 암호를 갈아엎는다

구조를 다 짜고 나서 발견한 문제입니다. 비밀번호를 이렇게 만들고 있었습니다.

```hcl
resource "random_password" "db" {
  count  = var.db_password == "" ? 1 : 0
  length = 32
}

locals {
  db_password = var.db_password != "" ? var.db_password : random_password.db[0].result
}
```

"변수가 비어 있으면 자동 생성" — 초기 구축엔 편한 패턴입니다. 그런데 운영에 들어간 뒤 **`TF_VAR_db_password`를 export하지 않은 채 `terraform plan`을 돌리면** 조건이 참이 되어 `random_password`가 새로 생성되고, "SSM 파라미터를 새 암호로 덮겠다"는 diff가 뜹니다.

그대로 apply하면 두 갈래로 터집니다.

| 대상 | 결과 |
| --- | --- |
| DB | RDS 마스터 암호와 SSM이 **함께** 로테이션 → 앱이 즉시 접속 불가 |
| Redis | ElastiCache는 `ignore_changes=[auth_token]`이라 그대로인데 **SSM만 갱신** → split-brain |

Redis 쪽이 더 고약합니다. 한쪽만 바뀌니 에러 메시지가 "인증 실패"로만 나오고, 원인이 IaC라는 걸 알아채기 어렵습니다.

해소는 **실제 암호의 소스를 SSM으로 확정**하는 것이었습니다.

```hcl
resource "aws_ssm_parameter" "db_password" {
  name  = "/${var.project}/${var.env}/db-password"
  type  = "SecureString"
  value = local.db_password

  lifecycle { ignore_changes = [value] }
}
```

`ignore_changes = [value]`로 Terraform이 값 변경을 무시하게 했습니다. Terraform은 **파라미터가 존재한다는 것**까지만 관리하고, 값의 진실은 SSM에 둡니다. 로테이션이 필요하면 별도 절차(새 값 주입 + 앱 재기동)로 명시적으로 합니다.

같은 패턴을 외부에서 주입되는 파라미터 전체에 적용했습니다. API 키들은 아예 `CHANGE_ME`를 초기값으로 두고 `ignore_changes`를 겁니다 — **"자리는 코드가 만들고, 값은 사람이 채운다"** 는 계약입니다.

> IaC가 관리하는 대상이 "리소스의 존재"인지 "리소스의 값"인지 구분해야 합니다. 시크릿은 전자만 맡기는 게 안전합니다.

## 사고 대응 절차가 실제로 동작하는지 확인한 일

설계가 끝나고 얼마 뒤, GA4 서비스계정 키가 노출된 사건이 있었습니다. 대응 절차가 이렇게 돌았습니다.

1. 키 재발급
2. **SSM 값만 교체** — 새 키를 `file://`로 주입해서 셸 히스토리·터미널에 값이 남지 않게
3. ESO force-sync → K8s Secret 갱신
4. `kubectl rollout restart` → 파드가 새 값을 읽음

레포·매니페스트·CI 어디도 건드리지 않았습니다. **시크릿의 원본이 한 곳이면 사고 대응도 한 곳에서 끝납니다.** 만약 매니페스트에 값이 박혀 있었다면 커밋 히스토리 정리까지 따라왔을 겁니다.

부끄러운 사고지만, 설계해둔 로테이션 경로가 실제 상황에서 동작하는지 확인할 기회이기도 했습니다. **평소에 안 써본 절차는 사고 때 안 돌아갑니다.**

## 계정과 state를 나눠 폭발 반경을 줄이기

권한을 아무리 좁혀도, 사람이 잘못된 환경에 apply하면 소용없습니다. 그래서 경계를 두 층 더 뒀습니다.

**① dev/prod AWS 계정 분리.** IAM 경계와 청구가 계정 수준에서 갈립니다. 실수로 prod를 건드리는 사고를 구조적으로 차단합니다.

**② tfstate 3분할.** 같은 버킷 안에서 key만 나눴습니다.

| state | 담당 |
| --- | --- |
| `drawe/prod` | 앱 인프라 — VPC·RDS·ALB |
| `eks/prod/cluster` | EKS 클러스터 |
| `eks/prod/platform` | ArgoCD·Karpenter·ESO 등 플랫폼 |

이렇게 나누면 **플랫폼을 고치다 클러스터 state를 건드릴 일이 없습니다.** 나중에 dev 계정을 정리할 때도 플랫폼 → 클러스터 → 앱 순서로 계층별 destroy가 가능해서, 순서 의존을 안전하게 통제할 수 있었습니다.

## 언제 이 구조를 쓰고, 언제는 과한가

정직하게 적어둡니다.

**쓸 만한 조건**

- CI가 클라우드에 접근하는 경우 — **OIDC는 거의 항상 이득**입니다. 설정 비용이 낮고 키 관리 부담이 통째로 사라집니다
- 파드마다 필요한 AWS 리소스가 다를 때 → IRSA. 전부 같은 권한이면 굳이 나눌 실익이 적습니다
- 시크릿이 10종을 넘고 환경이 둘 이상일 때 → ESO. 값이 두세 개 고정이면 과합니다

**과한 조건**

- 서비스 하나에 시크릿 두세 개면 ESO 운영 부담이 이득을 넘습니다. ESO는 그 자체가 클러스터에 상주하는 컴포넌트고, 버전 관리와 장애 대응 대상이 하나 더 느는 겁니다
- 계정 분리도 팀이 한 명이고 환경이 하나면 오히려 계정 전환 비용만 늘어납니다

## 남은 과제

- **시크릿 로테이션 주기가 없습니다.** 사고가 나면 도는 절차는 검증했지만, 정기 로테이션은 수동이고 주기도 정해두지 않았습니다. `refreshInterval`이 SSM→K8s 동기화 주기일 뿐 로테이션 주기는 아닙니다
- **`kms:Decrypt`의 `resources = ["*"]`** 는 `ViaService` 조건으로 막았지만, SecureString용 CMK를 따로 두면 리소스까지 좁힐 수 있습니다
- OIDC 신뢰 정책에 과거 분리 레포 시절 조건이 남아 있습니다. 지금은 모노레포라 실제로는 안 쓰이지만, **안 쓰는 조건도 공격 표면**이니 정리 대상입니다

## 배운 점

1. **저장하지 않은 키는 유출되지 않는다.** 로테이션 부담은 키를 잘 관리해서 줄이는 게 아니라, 키를 없애서 없애는 편이 확실하다.
2. **최소권한은 "좁힐 수 있는 축을 찾는 일"이다.** Cost Explorer는 리소스를 못 좁히고 KMS는 키를 특정하기 번거롭다. 한 축이 막히면 다른 축(액션·조건)을 좁힌다 — 못 좁힌다고 포기하는 것과 다르다.
3. **기능 일치와 state 일치는 다르다.** 동작한다고 IaC가 현실을 아는 건 아니다. 남겨둔 diff는 다음 apply의 뇌관이다.
4. **IaC에 무엇을 맡길지 구분해야 한다.** 시크릿은 "존재"만 코드가 관리하고 "값"은 저장소에 둔다. 값까지 맡기면 변수 하나 안 넣은 plan이 운영 암호를 갈아엎는다.
5. **평소에 안 써본 절차는 사고 때 안 돌아간다.** 로테이션 경로는 설계해두는 것과 실제로 돌려보는 것이 다르다.
6. **인증과 시크릿은 같은 문제가 아니다.** 인증은 OIDC·IRSA처럼 필요할 때만 권한을 받아 해결하고, 시크릿은 SSM처럼 원본을 한 곳에 두고 배포 시 주입하는 편이 관리와 사고 대응 모두 단순하다.

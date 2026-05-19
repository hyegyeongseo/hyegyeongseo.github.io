---
title: "Spring Boot에서 RDS SSL 연결 시 CertPathValidatorException이 발생한 이유 — JVM truststore와 RDS CA"
description: "RDS SSL 검증을 VERIFY_IDENTITY로 설정했지만 HikariPool 초기화가 trust anchor 에러로 실패한 문제를 추적해, JVM cacerts에 RDS CA가 없었다는 원인을 확인하고 Dockerfile에 import 단계를 추가한 과정"
date: 2026-05-09
tags: ["aws", "rds", "spring boot", "ssl", "tls", "jvm", "hikaricp", "docker"]
---

## 진행 사항

ECS에 띄운 Spring Boot backend가 RDS(MySQL)에 SSL로 연결하는 구조였습니다. 로컬과 운영 환경에서 SSL 모드를 다르게 가져가도록, application.properties에서 환경변수로 받게 해두었습니다.

```properties
# application.properties
# 로컬은 PREFERRED, ECS는 VERIFY_IDENTITY
spring.datasource.url=jdbc:mysql://...?sslMode=${DB_SSL_MODE:PREFERRED}
```

ECS task definition에서는 `DB_SSL_MODE=VERIFY_IDENTITY`로 운영 등급 SSL 검증이 활성화되도록 환경변수를 주입했습니다.

## 발생한 문제

ECS에 배포한 후 Spring Boot가 부팅을 시작하다 HikariPool 초기화 단계에서 막혔습니다.

```text
HikariPool : DraweHikariCP - Exception during pool initialization
com.mysql.cj.jdbc.exceptions.CommunicationsException: Communications link failure
Caused by: javax.net.ssl.SSLHandshakeException:
  java.security.cert.CertPathValidatorException:
    Path does not chain with any of the trust anchors
```

application 단까지는 부팅이 진행됐지만, DB 연결 풀을 만드는 단계에서 SSL handshake가 실패했습니다.

## 원인 분석

### 에러 스택의 의미 따라가기

에러 스택을 가장 안쪽부터 따라가면 원인은 다음과 같이 정리됩니다.

- `CertPathValidatorException: Path does not chain with any of the trust anchors`
  → 서버가 제시한 인증서의 발급 CA를 *내가 신뢰하는 CA 목록(trust anchors)* 에서 찾을 수 없음
- `SSLHandshakeException`
  → 그래서 SSL handshake 자체가 실패
- `CommunicationsException`
  → 결과적으로 DB와 통신을 시작도 못 함

즉 application은 SSL 검증을 요청했고, 서버는 정상적으로 인증서를 제시했는데, **JVM이 그 인증서를 신뢰할 수 없다**고 판단한 상태였습니다.

### 설계 자체는 정공법이었는지 확인

먼저 SSL 검증을 요청한 쪽 — application과 인프라 설정 — 을 점검했습니다.

- `application.properties`: `sslMode=${DB_SSL_MODE:PREFERRED}` 으로 환경변수 매핑 ✓
- ECS task definition 환경변수: `DB_SSL_MODE=VERIFY_IDENTITY` ✓

두 곳 모두 운영 등급 SSL 검증(`VERIFY_IDENTITY`)을 의도하고 있었습니다. 설계 자체에는 빠진 게 없었습니다.

### 빠진 한 조각 — JVM cacerts에 RDS CA가 없음

문제는 SSL 검증을 *수행하는 쪽* — JVM truststore — 에 있었습니다. JVM은 기본 truststore(`$JAVA_HOME/lib/security/cacerts`)에 등록된 CA만 신뢰합니다. 그 안에는 공개 CA들(DigiCert, Let's Encrypt, VeriSign 등)이 들어있지만, **RDS CA는 들어있지 않습니다**.

```text
cacerts (JVM truststore)
├─ DigiCert       ✓
├─ Let's Encrypt  ✓
├─ VeriSign       ✓
└─ RDS CA         ✗  ← 없음
```

RDS는 AWS가 자체적으로 발급한 CA(`rds-ca-rsa2048-g1` 등)로 서버 인증서를 서명하기 때문에, 이 CA를 JVM truststore에 따로 등록하지 않으면 `VERIFY_IDENTITY` 모드에서는 trust chain을 만들지 못합니다.

application의 의도(`VERIFY_IDENTITY`)와 인프라의 설정(`DB_SSL_MODE=VERIFY_IDENTITY` 환경변수)은 일치했지만, 그 둘을 *실행 시점에 잇는 한 단계* — JVM의 trust 단계 — 가 누락된 패턴이었습니다.

결국 문제는 "SSL 설정 여부"가 아니라 "신뢰할 CA가 JVM에 존재하느냐"의 문제였습니다.

## 해결 방법

### Dockerfile에서 RDS CA bundle을 cacerts에 import

AWS는 region별로 RDS CA bundle을 PEM 형식으로 공개하고 있습니다(`https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem`). 이를 다운로드해 JVM cacerts에 import하는 단계를 Dockerfile에 추가했습니다.

```dockerfile
# USER drawe 전 (root 권한 필요)
ADD https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem \
    /tmp/rds-bundle.pem

RUN cd /tmp && \
    csplit -z -s -f rds-cert- rds-bundle.pem '/-----BEGIN CERTIFICATE-----/' '{*}' && \
    n=0; \
    for cert in rds-cert-*; do \
      n=$((n+1)); \
      keytool -importcert -trustcacerts -noprompt \
        -alias "rds-ca-${n}" \
        -file "$cert" \
        -keystore "$JAVA_HOME/lib/security/cacerts" \
        -storepass changeit; \
    done && \
    rm -f rds-bundle.pem rds-cert-*
```

각 단계의 역할:

- `ADD` — RDS CA bundle 파일을 컨테이너로 다운로드
- `csplit` — bundle은 여러 인증서가 합쳐진 파일이라, `-----BEGIN CERTIFICATE-----` 단위로 분리해 개별 파일로 만듦
- `keytool -importcert` — 각 인증서를 JVM cacerts에 import. alias는 중복되지 않게 번호로 매김
- `-storepass changeit` — JVM cacerts의 기본 비밀번호
- 마지막 `rm` — 임시 파일 정리

`USER drawe` 같은 비루트 사용자 전환 단계가 있다면, 이 단계는 반드시 그 *전*에 두어야 합니다. cacerts 수정에는 root 권한이 필요하기 때문입니다.

### 적용 결과

- SSL handshake 정상 완료
- HikariPool 초기화 성공, DB 연결 풀 생성
- Spring Boot 부팅 완료

## 새로 배운 점

- 운영 요구사항(`VERIFY_IDENTITY`)이 JVM 기본 cacerts의 커버 범위를 넘어서면서 trust chain이 끊긴 케이스였습니다. 프레임워크의 기본 동작을 깬 것이 아니라, 자동 영역이 커버하지 않는 운영 요구사항으로 확장하면서 만난 경계 문제였습니다.
- RDS는 AWS 자체 발급한 CA로 서버 인증서를 서명하므로, JVM의 공개 CA 목록만으로는 trust chain을 만들 수 없습니다. region별로 제공되는 RDS CA bundle을 cacerts에 별도로 import하는 단계가 필요합니다.
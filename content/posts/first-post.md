---
title: "nginx에서 React SPA 라우팅이 깨지는 문제와 해결"
description: "React(Vite) 앱을 nginx 컨테이너로 실행할 때 발생한 SPA 라우팅 문제를 분석하고 해결한 과정"
date: 2025-04-26
tags: ["react", "nginx", "docker", "spa"]
---

## 진행 사항
React SPA를 nginx 기반 Docker 컨테이너로 실행했습니다.

## 발생한 문제
Vite dev 서버(`npm run dev`)로 실행 시에는 로그인 페이지가 정상 렌더링되었으나,

도커 컨테이너로 띄울 때는 다음과 같은 이상 현상이 발생했습니다.

- 로그인하지 않은 상태임에도 보호된 메인 페이지의 일부 UI가 먼저 렌더링되는 현상이 발생했습니다.

## 문제 재현 및 추가 실험
문제를 더 정확히 이해하기 위해 아래 4가지 케이스로 나누어 테스트했습니다.

```
1. PrivateRoute ❌ + nginx.conf ❌ → 흰 화면
2. PrivateRoute ❌ + nginx.conf ⭕ → 흰 화면
3. PrivateRoute ⭕ + nginx.conf ❌ → 로그인 페이지는 뜨지만 새로고침 시 404
4. PrivateRoute ⭕ + nginx.conf ⭕ → 로그인 페이지 로딩 후 새로고침 시에도 페이지 정상 로딩
```

→ 실험을 통해 nginx 설정과 인증 로직은 서로 다른 문제를 해결하며, 둘 다 필요하다는 것을 확인했습니다.

## 원인 분석
### 4-1. nginx와 SPA 라우팅의 차이

Vite dev server는 Node.js 기반이라 모든 요청을 받아서 index.html로 전달해주기 때문에 SPA 라우팅이 자동으로 동작했습니다.

React Router는 클라이언트 사이드 라우팅을 사용합니다.

즉, 실제 URL 경로(`/login`, `/dashboard`)는 브라우저에서 해석됩니다.

반면 nginx는 정적 파일 서버이기 때문에:

```
GET /login
→ /login 파일 찾음
→ 없으면 404
```

이 동작을 합니다.

→ 따라서 별도 설정이 없으면 SPA 라우팅이 깨집니다.

---

### 4-2. PrivateRoute의 역할

```
const PrivateRoute = ({ children }) => {
  const token = localStorage.getItem("accessToken");
  return token ? children : <Navigate to="/login" replace />;
};
```

역할:

- 인증되지 않은 사용자를 `/login`으로 리다이렉트

<br><br>

하지만:


> ❗ PrivateRoute는 클라이언트 렌더링 이후에만 동작하므로,
>    서버 레벨에서 발생하는 404 문제를 해결할 수 없습니다.
> 

---

### 4-3. 왜 새로고침 시 404가 발생했는가

초기 진입 시:

```
/ → index.html → React 실행 → /login으로 리다이렉트
```

하지만 새로고침 시:

```
/login → nginx 요청 → 파일 없음 → 404
```

→ 클라이언트 라우팅이 개입하기 전에 서버에서 요청이 끝나버립니다.

---

### 4-4. 핵심 정리

| 요소 | 레이어 | 역할 |
| --- | --- | --- |
| nginx.conf | 서버 | SPA 라우팅을 위한 fallback 처리 |
| PrivateRoute | 클라이언트 | 인증 기반 접근 제어 |

→ 두 요소는 서로 다른 레이어에서 동작하며, 둘 다 필요합니다.

## 해결 방법
### 5-1. nginx 설정 추가

```
location / {
  try_files $uri $uri/ /index.html;
}
```

→ 존재하지 않는 경로를 `index.html`로 전달하여 SPA 라우팅을 가능하게 합니다.

---

### 5-2. Dockerfile에서 설정 적용

```
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

→ nginx가 실제로 설정 파일을 읽도록 경로에 복사합니다.

---

### 5-3. 적용 결과

- `/login` 페이지에 정상 접근 가능
- 새로고침 시에도 404 발생 ❌
- SPA 라우팅이 정상 동작

## 새로 배운 점
- SPA는 클라이언트 라우팅을 사용하므로 서버에서 별도의 fallback 설정이 필요합니다.
- Vite dev 서버는 이를 자동으로 처리하지만, 운영 환경에서는 직접 설정해야 합니다.
- 클라이언트 라우팅과 서버 라우팅은 완전히 다른 개념입니다.
- 이 문제가 nginx에 국한된 게 아니라 CDN(Cloudflare, CloudFront) 등 정적 서버로 배포할 때도 고려해야 합니다.
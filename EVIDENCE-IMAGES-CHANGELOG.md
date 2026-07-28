# 증적 이미지 재배치 (v2 — 최소 구성)

> 블로그 프로젝트 루트에서 `unzip -o` 로 풀어주세요.
> 이전 zip을 적용한 상태를 전제로 만들었습니다 — 이전에 수정했던 글 17개를 **전부** 담아,
> 이미지를 뺀 글은 원본으로 되돌리고, 압축 해제 중 잘렸던 파일(글 끝 � 기호)도 함께 복구됩니다.
> 확인 후 이 파일은 삭제해도 됩니다.

## 남긴 이미지 8곳 (본문 주장 바로 옆에 그 주장의 증거가 오는 경우만)

| 글 | 위치 | 이미지 | 남긴 이유 |
| --- | --- | --- | --- |
| amp-spanmetrics-cost | "AMP가 컨트롤 플레인보다 컸다" 직후 | admin-cost-amp.png | 그 문장의 실청구 증거 ($48.21 vs $35.82) |
| aws-vpc-endpoint-nat | "Bills 화면 그대로 펼치기" 직후 | dev-bills-line-items.png | 본문이 텍스트로 옮겨 적은 바로 그 화면 |
| ecs-to-eks-cutover | "틀만 남긴 콜드 스탠바이" 직후 | ecs-alb-cold-standby.png | "대상 0개"는 글보다 그림이 빠름 |
| gitops-overlay-bump-race | "태그를 커밋하는 것까지만" 직후 | overlay-bump-commit.png | CI 봇 커밋 한 줄 diff = 그 문장의 실물 |
| arm64-ci-build-strategy | "태그 없는 `-` 이미지" 직후 | ecr-images-detail.png | 글이 설명하는 부산물을 눈으로 확인 |
| opentelemetry-stack | 파이프라인 다이어그램 직후 | alloy-daemonset.png | "노드마다 DaemonSet"의 kubectl 실물 |
| alerting-with-amp | "룰 상태를 코드와 일치" 직후 | amp-workspace-rules.png | 네임스페이스 3개 = Terraform 산출물 |
| ai-drawing-coach-design | 네 가지 산출 목록 직후 | (기존) drawe-guide.png | 목록으로 설명한 산출물의 실제 화면 |

## 뺀 것 (이전 zip에서 넣었다가 이번에 제거)

gitops-argocd-drift(3장), drift-gate-log, terraform-plan-no-changes, ecr-arm64-images(2곳),
sgp-policy, alb-target-groups, reference-search, growth-record, graceful-shutdown-sequence,
flyway-schema-history, vlm-backend-config, goldenset-paired-report
— 해당 글 9개는 이 zip으로 원본 복원됩니다.

## 이전 zip이 남긴 미사용 이미지 정리 (선택)

글에서 더 이상 참조하지 않으므로 남아 있어도 무해하지만, 지우려면:

```bash
cd static/images/posts
rm -f argocd-apps.png argocd-sync-history.png drift-gate-check.png drift-gate-log.png \
      terraform-plan-no-changes.png argocd-observability-tree.png ecr-arm64-images.png \
      sgp-policy.png alb-target-groups.png reference-search.png growth-record.png \
      graceful-shutdown-sequence.png flyway-schema-history.png vlm-backend-config.png \
      goldenset-paired-report.png
```

## 글 끝 깨진 기호(�)에 대해

zip 안의 md 파일은 전부 정상 UTF-8로 검증됐습니다. 기호가 보였던 건 압축 해제 시
일부 파일이 끝까지 기록되지 않은 것으로 보입니다. 이 zip을 다시 풀면(덮어쓰기) 복구되며,
이후에도 보인다면 `hugo` 재빌드 후 브라우저 캐시를 비워 확인해 주세요.

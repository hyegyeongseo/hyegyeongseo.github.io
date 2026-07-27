---
title: "Git 동작 원리 정리 — commit graph, merge-base, cherry-pick"
description: "unrelated histories, merge-base, cherry-pick을 중심으로 Git의 commit graph 동작 원리를 정리"
date: 2026-05-10
tags: ["git"]
categories: ["Software Engineering"]
---

## 이 글을 쓰게 된 계기

팀 레포의 backend 코드를 GitHub의 *Download ZIP*으로 받아서 본인 개인 레포에서 한참 변경 작업을 했습니다. 이후 팀 레포의 develop에 변경사항을 합류시키려고 팀 레포를 두 번째 remote(`team`)로 추가하고 push를 시도했는데, *"git push 한 줄이면 끝"*일 거라 생각한 것과 달리 `! [rejected] non-fast-forward`로 거부됐습니다. rebase로 풀어보려다 formatter 적용까지 겹치며 대량 modified 상태가 됐고, `git reset --hard origin/develop`으로 원래 상태에 복귀한 뒤 graph 관계부터 다시 진단했습니다.

결정적인 한 줄은 다음이었습니다.

```bash
$ git merge-base develop team/develop
(빈 출력)
```

두 branch의 공통 조상이 *하나도 없다*는 의미였습니다. 이 한 줄이 그동안의 모든 의문을 풀어줬고, 이후 cherry-pick으로 11개 commit을 팀 history 위에 다시 얹는 작업으로 이어졌습니다.

결론부터 말하면 이 문제는 *같은 코드베이스처럼 보이는 두 Git history가 사실은 완전히 별개의 DAG였다*는 것이었습니다.

이 글은 그 사건을 풀어내는 과정에서 정리한 Git의 핵심 동작 원리를 정리합니다.

## Git의 데이터 모델 — commit graph

Git의 모든 명령은 결국 한 가지 자료구조 위에서 동작합니다 — **commit으로 만들어진 directed acyclic graph(DAG)**입니다. acyclic인 이유는 단순한데, commit은 자신이 만들어지는 시점에 *과거의 parent commit*만 가리킬 수 있고 *미래 commit*을 가리킬 수 없기 때문에 cycle이 생길 수 없습니다.

각 commit은 다음 두 가지로 구성됩니다.

- **Snapshot**: 그 시점의 working tree 전체 상태 (tree object)
- **Parent commit ID**(들): 직전 commit을 가리키는 포인터

`A ← B ← C` 처럼 자식이 부모를 가리키는 화살표가 모여 history가 됩니다. *Git은 파일을 추적하는 도구라기보다, commit이라는 노드의 그래프를 관리하는 도구*입니다. 모든 명령(`log`, `merge`, `rebase`, `cherry-pick`, `reset`)은 이 graph를 어떻게 읽거나 조작하느냐의 차이일 뿐입니다.

## Branch / HEAD / Remote — 포인터의 세계

| 개념 | 정체 |
| --- | --- |
| **Branch** | 특정 commit hash를 가리키는 *이름 붙은 포인터*. 새 commit이 생기면 자동으로 따라감 |
| **HEAD** | *지금 어디에 있는지*를 가리키는 포인터. 보통 어떤 branch를 가리키지만, 직접 commit을 가리킬 수도 있음 (detached HEAD) |
| **Remote** | 다른 repo의 *별명*. `origin`은 본인의 원격, `team` 같은 다른 별명도 추가 가능 |

branch가 "선"처럼 보이는 건 시각화 도구의 표현일 뿐이고, 실제로는 commit hash 하나를 가리키는 작은 ref 파일입니다. `git checkout <branch>`는 HEAD를 그 ref로 옮기는 동작이고, `git merge`나 `git rebase`도 결국 ref를 어디로 옮길지의 문제입니다.

이번 사건의 remote 구성은 다음과 같았습니다.

```bash
$ git remote -v
origin  https://github.com/<personal>/backend.git (fetch/push)  # 본인 개인 레포
team    https://github.com/<team>/backend.git     (fetch/push)  # 팀 레포 (나중에 추가)
```

`origin`이 본인 개인 레포이고, 팀 레포를 두 번째 remote(`team`)로 추가한 구조였습니다.

## 두 history를 비교하는 진단 명령

두 branch 사이의 관계를 정확히 파악하려면 다음 명령들이 쓰입니다.

| 명령 | 답해주는 질문 |
| --- | --- |
| `git log A..B --oneline` | A에서 도달 가능한 commit을 뺀, B에서 도달 가능한 commit |
| `git log A...B --oneline` | A와 B 사이에 *서로 다른* commit (양쪽 모두) |
| `git merge-base A B` | A와 B의 *공통 조상* commit |
| `git diff A B --stat` | 파일별로 *얼마나* 다른지 |

이번 사건에서 가장 결정적이었던 건 `merge-base`였습니다.

```bash
$ git merge-base develop team/develop
(빈 출력)

$ git log develop..team/develop --oneline | wc -l
50+

$ git log team/develop..develop --oneline | wc -l
11
```

공통 조상이 0개. 두 history가 *공통 commit hash를 단 하나도 공유하지 않는* 별개 graph라는 결론이었습니다.

## Fast-forward의 조건과 push 거부

`git push`가 기본적으로 허용하는 건 **fast-forward**입니다. *fast-forward는 현재 branch HEAD가 대상 branch HEAD의 조상(ancestor)일 때, 새 commit 없이 단순히 branch 포인터만 이동하는 업데이트 방식*입니다. graph 위에서 한 줄 경로로 따라갈 수 있을 때만 ref를 새 위치로 옮기는 동작이고, 그 결과 *어떤 새로운 history도 만들어지지 않습니다*.

이번 케이스에서 두 history는 *별개 graph*였으니, fast-forward 자체가 정의되지 않는 상황이었습니다. `! [rejected] non-fast-forward`는 "내가 모르는 변경이 있다"가 아니라 "내가 따라갈 수 있는 경로 자체가 없다"에 가까웠습니다.

`git push --force`로 덮어쓰는 건 기술적으로 가능하지만, 그 순간 remote branch ref가 강제로 이동하면서 팀의 기존 50+ commit이 branch history에서 이탈(unreachable)하게 됩니다. reflog/GC 이전까지는 복구가 가능하지만 협업 history를 깨뜨리므로 *사실상 금기*. 안전한 대안은 `--force-with-lease`(다른 사람의 변경이 있으면 거부) 또는 cherry-pick으로 history를 새로 만드는 것입니다.

## Unrelated histories — ZIP 다운로드로 시작한 레포의 정체

정상적인 fork는 다음 모습입니다.

```
team/develop:  A ─ B ─ C ─ D ─ E ─ F
                       │
fork/develop:          C ─ X ─ Y ─ Z
                       └─ 공통 조상 (C)
```

`merge-base`는 `C`를 반환하고, fork는 `C` 위에 자기 commit을 쌓은 형태입니다.

이번 케이스는 fork가 아니었습니다. **팀 레포의 코드를 GitHub의 *Download ZIP*으로 받아서 본인 개인 레포에서 작업을 시작**한 케이스였습니다.

여기서 결정적인 한 가지 — **GitHub의 ZIP 다운로드는 `.git` 폴더를 포함하지 않습니다.** 코드 파일만 압축됩니다. 그 zip을 풀고 새 레포로 시작한 순간, git history는 거기서 0부터 새로 시작됩니다.

```
team/develop:           A ─ B ─ C ─ D ─ E ─ F ─ ... (50+ commit)

본인 개인 레포(origin):  init' ─ X ─ Y ─ Z (11 commit)
                          ↑
                    이건 새 root commit. 팀 레포의 어떤 commit과도 hash 무관
```

코드 내용은 비슷할 수 있어도 **commit hash는 완전히 다른 commit**이고, 그 위에 쌓인 11개 commit도 모두 *팀 history에 없는 새 commit*들입니다.

Git 용어로는 이걸 ***unrelated histories***라고 부릅니다. 두 history가 공통 commit을 단 하나도 공유하지 않는 상태. `git merge --allow-unrelated-histories` 같은 옵션이 따로 존재하는 것도, 이 케이스가 일반 fork와 본질적으로 다르기 때문입니다.

| 시작 방법 | git history | 나중에 합치기 |
| --- | --- | --- |
| `git clone` (또는 GitHub Fork 버튼) | 원본 레포의 모든 commit hash 보존 | `merge` / `rebase` 가능 |
| **Download ZIP → 새 레포** | history가 처음부터 새로 시작 | 일반 merge/rebase 어려움. 보통 cherry-pick 사용 |
| `git clone --depth 1` | 가장 최근 commit만 가져옴 | 대체로 가능, 일부 제약 |

이런 history는 *lineage가 끊긴 별개 DAG*이기 때문에, merge-base 계산에서 공통 조상이 사라지고, `merge`나 `rebase`처럼 공통 조상을 전제로 동작하는 명령은 *의미 있는 결과를 만들기 어렵습니다*. 특히 `rebase`는 공통 조상을 기준으로 commit들을 다시 적용하는 명령이라, merge-base가 없으면 *정렬할 기준점 자체가 사라집니다*. `git merge --allow-unrelated-histories` 옵션으로 강제 merge하는 길도 있지만, 두 history가 *코드는 비슷하고 hash만 다른* 상황에서는 거의 모든 파일이 충돌로 잡혀 실용성이 떨어집니다. 본인이 실제로 도입한 변경만 깔끔하게 옮기려면 cherry-pick으로 *commit 하나하나의 의미*를 재적용하는 쪽이 자연스럽습니다.

ZIP으로 시작하는 게 잘못된 선택은 아닙니다. 권한 문제, 개인 작업 분리, 처음엔 합칠 의도가 없다가 나중에 합치게 된 경우 등 합리적인 이유들이 있습니다. 다만 *나중에 그 작업을 팀 레포에 합치려 할 때*, git 입장에서는 두 history가 별개라는 사실을 알고 시작해야 한다는 점이 핵심입니다.

## Cherry-pick — 공통 조상이 없을 때의 답

`cherry-pick`은 **특정 commit이 도입한 변경(patch)을 추출해 현재 branch에 재적용**합니다. 부모 commit이 무엇이든 상관없이, *그 commit이 만든 diff*만 가져옵니다.

```bash
$ git cherry-pick <commit-hash>
```

공통 조상이 없는 두 history 사이에서도 commit 하나하나의 변경 의미는 살아있으므로, 그 의미를 다른 history 위에 다시 적용할 수 있습니다. 이번엔 11개 commit을 팀 develop 위에 차례로 cherry-pick하는 패턴이었습니다.

`A^..B` 형식으로 범위 cherry-pick도 가능합니다(`A` 다음부터 `B`까지).

**주의 한 가지**: cherry-pick은 *history를 재구성*하는 것이 아니라 *복제*하는 것입니다. 같은 변경이지만 hash가 다른 새 commit이 생기므로, 이후 양쪽 history를 다시 merge할 일이 있으면 중복 commit으로 잡힐 수 있습니다.

### 충돌의 다섯 가지 종류

같은 파일을 양쪽에서 건드렸으면 cherry-pick은 충돌을 만듭니다. 이번 사건에서 만난 충돌은 다섯 가지 패턴이었습니다.

| 유형 | 의미 | 결정 |
| --- | --- | --- |
| `modify/delete` | 본인이 만든 파일을 팀이 갖고 있지 않음 | 본인 파일을 새로 add |
| `merge conflict` (양쪽 수정) | 같은 파일을 양쪽 다 의미 있게 수정 | Accept Both — 영역이 다르면 양쪽 살리기 |
| `merge conflict` (팀이 더 풍부) | 팀 버전이 더 발전된 상태 | Accept Current (팀 버전 채택) |
| `merge conflict` (본인 영역) | 본인 영역만 추가됨 | Accept Incoming (본인 변경 채택) |
| `commit empty after pick` | 앞 commit이 이미 같은 변경을 적용 | `git cherry-pick --skip` |

해결의 핵심 질문은 "이 변경의 source of truth는 어느 쪽인가"였습니다. 팀이 그 사이에 발전시킨 부분은 팀이 truth, 본인이 도입한 인프라/배포 영역은 본인이 truth.

```bash
$ git cherry-pick --continue   # 충돌 해결 후 진행
$ git cherry-pick --skip       # 이미 적용된 변경 건너뛰기
$ git cherry-pick --abort      # 전체 취소, 시작 전 상태로 복귀
```

## Reset과 Reflog — 안전망

`git reset --hard origin/develop`이 *destructive* 명령처럼 보여도, 다음 두 가지가 함께 있는 한 거의 항상 복구 가능합니다.

- **Remote**: GitHub 서버에 commit이 그대로 남아 있음
- **Reflog**: 모든 HEAD 이동을 기본적으로 *일정 기간* 기록 (`gc.reflogExpire` 설정에 따라 달라짐). `git reflog`로 확인 가능

이번 사건에서 대량 modified 상태에 빠졌을 때 `reset --hard origin/develop` 한 줄로 원래 상태로 돌아갈 수 있었던 것도 origin과 reflog가 안전망이었기 때문입니다. *Git은 좀처럼 데이터를 진짜로 잃지 않는다*는 점이 destructive 명령을 안전하게 만들어줍니다.

## 본인의 명령 시퀀스 — 진단부터 통합까지

이번 사건을 풀어낸 흐름을 명령 단위로 정리하면 다음과 같습니다.

```bash
# 0. 팀 레포를 두 번째 remote로 추가
$ git remote add team <team-repo-url>
$ git fetch team

# 1. 진단 — 두 history의 관계 파악
$ git merge-base develop team/develop                 # (빈 출력)
$ git log develop..team/develop --oneline | wc -l     # 50+
$ git log team/develop..develop --oneline | wc -l     # 11

# 2. 안전망 — backup branch (이름은 임의)
$ git branch backup develop

# 3. 새 branch — 팀 develop 위에서 시작
$ git checkout -b feat/migration team/develop

# 4. Cherry-pick — 11개 commit을 하나씩 가져옴
$ git log team/develop..backup --oneline --reverse   # 옮길 commit 목록 확인

$ git cherry-pick <hash1>
# 충돌 발생 시: 해결 → git add → git cherry-pick --continue
# 이미 적용된 변경: git cherry-pick --skip
# 잘못 시작했으면: git cherry-pick --abort

$ git cherry-pick <hash2>
# ... 11개 commit을 차례로 반복

# 5. Push + PR
$ git push team feat/migration
```

흐름이 명확했던 건 "진단 → 결정 → 실행"의 순서가 분명했기 때문이고, 그 명확함은 commit graph라는 데이터 모델이 보였기 때문이었습니다.

## 정리

Git은 결국 *commit이라는 노드와 parent 화살표로 만들어진 DAG* 위에서 동작합니다.

- branch / HEAD / remote는 그 graph 위의 *포인터*일 뿐
- `merge-base`, `log A..B`, `diff A B --stat`는 graph를 읽는 진단 명령
- fast-forward / merge / rebase는 graph가 어떤 모양일 때 가능한지의 조건이 있음
- GitHub Download ZIP은 `.git`을 포함하지 않으므로, 그렇게 시작한 레포는 원본과 *unrelated histories* 관계
- unrelated histories에서는 **cherry-pick**이 가장 안전하고 실용적인 통합 방식인 경우가 많음
- reset의 destructive함은 remote와 reflog가 받쳐주는 한 안전망 안에 있음

명령어를 외우기 전에 graph가 보이면, *"왜 push가 거부됐지?"*, *"왜 rebase가 의미가 없지?"* 같은 질문이 자연스럽게 풀립니다. 이번 사건에서 `merge-base` 한 줄이 모든 의문을 풀어줬던 것도, 그 명령이 graph의 가장 중요한 한 가지 사실을 직접 보여줬기 때문이었습니다.
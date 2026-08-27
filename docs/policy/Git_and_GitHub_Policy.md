# Git 및 GitHub 작업 정책

## 1. 기본 원칙

- `main`은 운영 환경에 배포된 안정 버전만 관리하며 항상 배포 가능한 상태로 유지한다.
- `develop`은 다음 릴리스에 포함될 변경사항을 통합하는 브랜치다.
- `main`과 `develop`에는 직접 커밋하거나 push하지 않는다. 모든 변경은 Pull Request(PR)로 반영한다.
- 공유된 커밋의 이력을 변경하지 않는다. 병합 방식은 `merge commit`으로 통일하며, 공유 브랜치에서 `rebase`와 force push를 사용하지 않는다.
- 운영 배포가 완료된 `main`의 커밋에는 버전 태그를 생성한다.

## 2. 브랜치 전략

### 2.1 장기 브랜치

| 브랜치 | 용도 |
| --- | --- |
| `main` | 운영 환경에 배포된 안정 버전 |
| `develop` | 다음 릴리스 변경사항 통합 |

### 2.2 작업 브랜치

브랜치 이름은 다음 형식을 사용한다.

```text
<type>/<issue-number>-<short-description>
```

| 접두사 | 용도 | 생성 기준 | PR 대상 |
| --- | --- | --- | --- |
| `feature/` | 새로운 기능 | `develop` | `develop` |
| `bugfix/` | 개발 중 버그 수정 | `develop` | `develop` |
| `docs/` | 문서 작성 및 수정 | `develop` | `develop` |
| `refactor/` | 기능 변경 없는 코드 개선 | `develop` | `develop` |
| `test/` | 테스트 추가 및 수정 | `develop` | `develop` |
| `chore/` | 설정·의존성 등 기타 작업 | `develop` | `develop` |
| `hotfix/` | 운영 버전 긴급 수정 | `main` | `main`, `develop` |

- `hotfix` 브랜치는 운영 장애 또는 치명적인 버그와 같은 긴급한 수정에만 사용한다.

### 2.3 이슈와 브랜치 연결

- 모든 작업은 GitHub Issue를 먼저 생성하고, 작업 유형·배경·범위·완료 조건·담당자를 기록한다.
- Issue 번호를 작업의 공통 식별자로 사용한다. 예를 들어 `#1`은 `feature/#1`로 표현한다.
- 작업 브랜치를 생성한 뒤 Issue의 `Development` 항목에 브랜치를 연결하고, 하나의 작업 브랜치는 하나의 Issue 또는 하나의 명확한 목적만 다룬다.

## 3. 커밋 메시지

커밋 메시지는 다음 형식을 사용한다.

```text
<type>: <summary>
```

| 접두사 | 용도 |
| --- | --- |
| `feat` | 새로운 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 변경 |
| `refactor` | 리팩토링 |
| `test` | 테스트 변경 |
| `chore` | 빌드·설정·의존성 등 기타 변경 |
| `style` | 포맷팅 및 코드 스타일 |
| `perf` | 성능 개선 |
| `merge` | 병합 및 충돌 해결 |

예시: `feat: 로그인 기능 추가`, `fix: 비밀번호 검증 수정`

- 하나의 커밋에는 하나의 논리적인 변경만 포함한다.
- `수정`, `완료`, `작업`처럼 변경 내용을 알 수 없는 메시지는 사용하지 않는다.
- 병합 커밋은 `merge:` 형식을 사용하거나 Git/GitHub가 생성한 메시지를 사용한다.
- 비밀번호, API 키, 개인정보 등 민감한 정보를 커밋하지 않는다.

## 4. 일반 작업 흐름

### 4.1 이슈 생성

작업 전에 GitHub Issue를 생성하고 작업 유형·범위·완료 조건·담당자·의존성을 기록한 뒤 담당자를 지정한다.

### 4.2 작업 브랜치 생성

최신 `develop`에서 Issue 번호를 포함한 작업 브랜치를 생성한다.

```bash
git fetch origin
git switch develop
git pull --no-rebase origin develop
git switch -c feature/1-login
```

### 4.3 작업 및 PR

1. 변경사항을 의미 있는 단위로 나누어 커밋한다.
2. 작업 브랜치를 원격 저장소에 push한다.
3. 작업 브랜치에서 `develop`을 대상으로 PR을 생성하고 Issue를 연결한다.

```bash
git add <변경한-파일>
git commit -m "feat: 로그인 기능 추가"
git push -u origin feature/#1
```

PR 본문에는 관련 Issue를 `Refs #번호` 또는 `Closes #번호`로 명시한다.

PR 제목은 커밋 메시지 형식과 동일하게 작성하며, 변경 목적·주요 변경사항·관련 Issue·테스트 결과·리뷰어 확인 사항을 포함한다.

### 4.4 리뷰 및 병합

- 최소 1명 이상의 리뷰 승인과 로컬 테스트 통과 후 병합한다.
- 리뷰 의견을 반영한 후 재승인을 받으며, 충돌은 작업자가 해결하고 테스트한 뒤 push한다.
- GitHub의 `Create a merge commit` 방식으로만 병합한다.
- `Rebase and merge`, `Squash and merge`, 장기 브랜치로의 직접 push는 사용하지 않는다.

GitHub 기본 브랜치가 `main`인 경우 `develop` 대상 PR에는 `Refs #번호`를 사용하고, 실제 릴리스 PR의 `main` 병합 대상 Issue에는 `Closes #번호`를 사용한다. `Closes`는 `main` 병합 후 Issue를 종료한다.

## 5 긴급 수정

운영 중인 `main`에서 긴급한 오류가 발견되면 `hotfix/<issue-number>-<short-description>`을 `main`에서 생성한다.

- 수정 및 검증 후 다음 두 PR을 모두 생성한다.
  1. `hotfix/*` → `main`
  2. `hotfix/*` → `develop`
- `main` 대상 PR은 `Closes #번호`, `develop` 대상 PR은 `Refs #번호`로 연결한다.
- `main` 병합 후 패치 버전 태그를 생성한다.

## 6. 최신 기준 반영 및 충돌 해결

기준 브랜치는 `merge`로 반영한다.

```bash
git fetch origin
git merge origin/develop
```

`hotfix` 브랜치에는 `origin/main`을 merge한다.

충돌을 해결한 뒤 파일을 stage하고 병합 커밋을 생성하여 push한다.

```bash
git add <충돌을-해결한-파일>
git commit -m "merge: 기준 브랜치 충돌 해결"
git push origin <현재-브랜치>
```

- 공유 브랜치에서 `git rebase`를 사용하지 않는다.
- `git push --force`와 `git push --force-with-lease`를 사용하지 않는다.
- PR 병합 전 최신 기준 브랜치를 반영하고 테스트한다.
- `hotfix`의 수정사항이 `main`과 `develop` 양쪽에 반영되었는지 확인한다.

## 7. 브랜치 보호 설정

`main`과 `develop`에 다음 규칙을 적용한다.

- PR을 통해서만 변경 가능하며 최소 1명 이상의 리뷰 승인이 필요하다.
- 필수 CI·테스트·빌드 통과 필수
- 병합 전 충돌 해결 및 최신 기준 반영 필수
- force push와 브랜치 삭제를 금지하며, `merge commit`만 허용하고 `Rebase and merge`, `Squash and merge`를 비활성화한다.

`hotfix/*`에도 필요에 따라 PR, 리뷰 승인, CI 통과, force push 금지 규칙을 적용한다.

## 8. PR 병합 전 체크리스트

- [ ] `main` 또는 `develop`에 직접 커밋하거나 push하지 않았다.
- [ ] 브랜치 이름과 접두사, 이슈 번호가 규칙에 맞다.
- [ ] 브랜치 생성 기준과 PR 대상이 올바르다.
- [ ] 커밋 및 PR 제목에 올바른 접두사를 사용했다.
- [ ] 작업 전에 Issue를 생성하고 브랜치 번호가 Issue 번호와 일치한다.
- [ ] Issue의 `Development`에 작업 브랜치를 연결했다.
- [ ] PR 본문에 `Refs #번호` 또는 `Closes #번호`를 작성했다.
- [ ] 테스트·빌드를 실행하고 리뷰 승인과 CI 통과를 확인했다.
- [ ] 최신 기준 브랜치와 충돌이 없다.
- [ ] `merge commit` 방식으로 병합하도록 설정했다.
- [ ] `hotfix`의 변경사항을 `main`과 `develop`에 모두 반영했다.
- [ ] `main` 병합 후 버전 태그를 생성했다.
- [ ] 병합 후 작업 브랜치를 정리했다.

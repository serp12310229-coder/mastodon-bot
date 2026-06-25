# mastodon-coc

CoC 7판 룰을 위한 마스토돈 자동봇. Google Sheets 의 캐릭터 시트를 기반으로 능력치 / 기능 / 무기 판정과 스탯 변동을 처리합니다.

## 사전 조건

다음 환경에서만 동작합니다. 나머지 패키지(`git`, `python3-venv`, `python3-pip`, `tmux`, `nano` 등)는 `install.sh` 가 자동으로 설치합니다.

- **OS**: Debian 12 (bookworm) 또는 그 이상의 Debian/Ubuntu 계열 — `apt-get` 기반 배포판
- **권한**: `sudo` 권한이 있는 사용자 계정
- **사전 설치 필요**: `curl` (스크립트 다운로드용), `bash`
- **Python**: 3.10 이상 — Debian 12 의 기본 `python3` (3.11) 이면 추가 작업 없이 OK
- **네트워크**: GitHub, Debian apt 미러, PyPI 접근 가능

`curl` 이 없다면 먼저 설치하세요:

```bash
sudo apt-get update && sudo apt-get install -y curl
```

## 빠른 설치 (Debian VM)

```bash
curl -fsSL https://raw.githubusercontent.com/long-while/mastodon-coc/main/install.sh | bash
```

스크립트가 자동으로 처리하는 것:

- `apt` 로 시스템 의존성 (`git`, `python3`, `python3-venv`, `python3-pip`, `tmux`, `nano`) 설치
- 저장소 클론 → `~/coc/`
- Python 가상환경(`venv`) 생성 + `requirements.txt` 설치
- 마스토돈 / 시트 ID / 관리자 계정 등을 대화형으로 입력받아 `.env` 작성
- 입력값 자동 정제 — `https://` 제거, 공백 제거, 선두 `@` 제거 등

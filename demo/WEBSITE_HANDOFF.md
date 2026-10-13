# MyOS Broker 데모: 웹사이트 이식 가이드

이 문서는 개인 웹사이트(JihoonOS)에 **MyOS Broker 재생 데모 앱("Run Lab")**을 만드는 작업 지시서다.
이 문서를 받은 에이전트는 Broker 저장소를 몰라도 이 문서만으로 작업할 수 있어야 한다.

> **우선순위**: 웹사이트 저장소의 `CLAUDE.md`와 `Steps.md` 규칙(언어 규칙, 디자인 토큰, 검증 절차,
> 커밋 규칙, 앱 등록 방식)이 이 문서보다 우선한다. 이 문서는 **무엇을** 만들지 정하고,
> **어떻게** 사이트에 맞출지는 사이트 규칙을 따른다. 충돌하면 사이트 규칙을 따르고 사용자에게 알린다.
> 사이트 규칙상 UI 문구는 전부 영어이고 `t(key)` 문자열 표에 모은다. 아래 §4의 영어 문구를 그대로 쓴다.

> **다른 데모와의 관계**
> - `~/Desktop/my-os/demo/WEBSITE_HANDOFF.md`(MyOS 셸 투어)와 `~/Desktop/T1_Hypervisor/demo/WEBSITE_HANDOFF.md`는
>   **QEMU 시리얼 출력을 한 줄씩 재생하는 터미널**이다. 이 데모는 그것과 모양이 다르다.
>   터미널 재생은 시리얼 로그 패널(§4.4)에서만 쓰고, 나머지는 **작업 큐 보드**와 **타임라인 차트**다.
> - 터미널 컴포넌트는 MyOS/T1 데모가 만든 것을 **그대로 재사용**한다(같은 태그 색 규칙, 같은 자동 스크롤 규칙).
>   이 데모가 먼저 만들어지면 이 데모가 그 컴포넌트를 만든다.
> - Broker는 MyOS 위에서 도는 프로젝트다. MyOS 프로젝트 카드와 서로 링크한다(§8).

---

## 1. 무엇을 만드나 (한 줄)

방문자가 **업로드 버튼을 누르면 실제로 녹화된 8번의 업로드가 큐에 쌓이고, 워커 2개가 슬롯 2개를 나눠 잡아
MyOS VM에서 하나씩 실행하는 과정**을 보고, 이어서 **행 잠금 없이 같은 일을 시키면 워커 4개가 같은 슬롯과 같은 작업을
동시에 잡아 버리는 경쟁 상태**를 잠금 버전과 나란히 보는 앱.
클라이맥스는 2막이다. 데이터베이스 표는 "바쁜 슬롯 2개"라고 말하는데 실제로는 VM 4개가 같은 작업을 돌리고 있다.

데이터는 **실제 Django 서버, MySQL, 워커 프로세스, QEMU를 돌려 녹화한 것**이다. 재생만 하고, 내용은 한 글자도 바꾸지 않는다.

---

## 2. 배경: Broker가 무엇이고 데모가 무엇을 보여주나

- **MyOS Broker**: Python/Django 서버. MyOS용 ELF 프로그램을 업로드하면 MySQL 큐에 넣고, 워커 프로세스가 작업마다
  새 디스크 이미지를 만들어 QEMU로 MyOS를 부팅한 뒤 그 프로그램을 실행하고, 시리얼 출력으로 결과를 판정한다.
  - GitHub: https://github.com/JihoonChoi1/myos-broker
  - MyOS(32비트 x86 OS, 별도 프로젝트): https://github.com/JihoonChoi1/my-os
  - Django와 워커는 호스트(macOS)에서 돈다. MyOS 안에서 도는 것은 업로드한 ELF 하나뿐이다.
- **방문자가 알아야 할 개념 4개**
  - **작업(Job)과 슬롯(Slot)**: 작업은 업로드 하나. 슬롯은 "동시에 몇 개까지 VM을 돌릴지"를 정하는 허가증이다(기본 2개).
    슬롯은 미리 떠 있는 VM이 아니다. 작업마다 새 QEMU를 띄운다.
  - **판정**: 프로그램이 0으로 끝나면 `success`, 0이 아니면 `failed`, 커널이 멈추면 `crashed`, 3초 안에 결과가 없으면 `timeout`.
    ELF 형식이 틀리거나 24 KiB를 넘으면 큐에 들어가기 전에 HTTP 400으로 거절한다.
  - **claim → run → release**: 워커는 짧은 트랜잭션으로 슬롯과 작업을 잡고(claim), 트랜잭션 밖에서 QEMU를 돌리고(run),
    다시 짧은 트랜잭션으로 결과를 저장하고 슬롯을 돌려준다(release). 몇 초짜리 VM 실행 동안 DB 잠금을 쥐고 있지 않는다.
  - **행 잠금**: `SELECT … FOR UPDATE SKIP LOCKED`. 잠금 없이 읽고 쓰면(`transaction.atomic()` 안이어도) 워커 여럿이 같은 행을
    "비어 있음"으로 동시에 읽고 모두 자기 것이라고 믿는다. `SKIP LOCKED`는 다른 워커가 잡고 있는 행을 기다리지 않고 건너뛴다.

**데모의 2막**

1. **큐(Queue)**: 샘플 8개 업로드 → 6개 접수, 2개 거절(400) → 워커 2개가 슬롯 2개로 6개를 처리 → 판정 5종이 다 나온다.
   방문자는 작업을 눌러 그 VM의 실제 시리얼 로그를 본다.
2. **경쟁(Race)**: 워커 4개, 작업 4개, 슬롯 2개. 같은 순간에 출발시킨다.
   `unsafe`(잠금 없음)는 작업 1을 VM 4대가 동시에 돌리고, `locked`(행 잠금)는 정확히 2대씩, 작업마다 한 번씩 돈다.

---

## 3. 입력 데이터: `demo/transcript.json`

**파일 위치** (이 문서만 받았다면 여기서 가져온다):
- 같은 Mac: `~/Desktop/myos-broker/demo/transcript.json` (Broker 저장소 루트는 `~/Desktop/myos-broker`)
- 그 밖의 환경: https://raw.githubusercontent.com/JihoonChoi1/myos-broker/master/demo/transcript.json
  (기본 브랜치는 `master`다. 2026-10-12 현재 커밋은 했지만 push 전이라 이 URL은 404다.)
- 둘 다 접근할 수 없으면 추측하거나 지어내지 말고 사용자에게 파일을 달라고 요청한다.
- 받은 파일의 `dirty`와 `myos_dirty`가 둘 다 `false`인지 먼저 확인한다(§3.1).

이 파일을 웹사이트로 **복사**한다. 위치는 사이트 규칙을 따르되, 예를 들면 `src/content/broker-demo.json`처럼
빌드에 포함되는 정적 데이터로 둔다. 런타임에 GitHub에서 받아오지 않는다. 원본 약 44 KB, gzip 약 5 KB다.
대부분이 시리얼 로그 6개이므로 **데모 앱을 열 때만 로드**한다.

### 3.1 생성 방법 (Broker 저장소에서)

```sh
cd ~/Desktop/myos-broker
docker start myos-broker-mysql          # MySQL (Docker Desktop이 켜져 있어야 한다)
.venv/bin/python demo/capture_demo.py   # -> demo/transcript.json, 약 30초
```

- 필요한 것: Docker의 MySQL 컨테이너, QEMU, 빌드된 MyOS(`~/Desktop/my-os`), `samples/*.elf`(`./samples/build.sh`). 이 Mac에는 다 있다.
- 스크립트는 MyOS를 `make disk.img`로 빌드하고, 데모 전용 DB(`myos_broker_demo`, 매번 비운다)와 `media/demo/`만 쓴다.
  사용자의 실제 작업 기록은 건드리지 않는다.
- **배포 전 확인**: `"dirty": false`, `"myos_dirty": false`이고, `"commit"`은 녹화한 코드의 커밋이다. 그 뒤에 transcript나 문서만 바꾼 커밋이 있는 것은 괜찮다(MyOS 데모와 같다).
  `dirty: true`이면 커밋되지 않은 코드로 찍은 것이니 커밋 후 다시 캡처한다.
  - 현재 파일: `commit: aff6342, dirty: false`, MyOS `7229d04`(`myos_dirty: false`), 2026-10-13 00:29 UTC 캡처, QEMU 11.1.2, MySQL 8.4.11.
- 재캡처할 때마다 슬롯 배정, 시각, 경쟁 결과 수치가 조금씩 바뀐다. 문구와 그림은 **값을 transcript에서 읽고**, 하드코딩하지 않는다.

### 3.2 스키마

```jsonc
{
  "captured_at": "2026-10-13T00:17:29+00:00",  // UTC
  "commit": "aff6342", "dirty": false,         // Broker. dirty면 배포 금지
  "myos_commit": "7229d04", "myos_dirty": false,  // 작업이 실제로 부팅한 MyOS 빌드
  "versions": { "python": "3.14.8", "django": "6.1.1", "mysql": "8.4.11", "qemu": "QEMU emulator version 11.1.2" },

  "queue": {                                   // 1막
    "config": { "workers": 2, "slots": 2, "timeout_seconds": 3.0, "max_binary_bytes": 24576,
                "worker_idle_poll_seconds": 0.5 },
    "submissions": [                           // 항상 8개, 이 순서. HTTP로 실제 업로드한 요청과 응답
      { "t": 0.0, "t_response": 0.08, "file": "hello.elf", "bytes": 10712,
        "request": "POST /jobs", "http_status": 201,
        "response": { "id": 1, "status": "queued", "exit_code": null, "detail": "", "binary": "hello.elf",
                      "slot": null, "created_at": "…", "started_at": null, "finished_at": null } },
      …,
      { "t": 1.39, "file": "corrupt.elf", "request": "POST /jobs", "http_status": 400,
        "response": { "error": { "code": "not_elf", "message": "missing ELF magic (\\x7fELF)" } } }
    ],
    "worker_log": [                            // 워커 프로세스의 실제 로그 줄 (W1, W2)
      { "t": 0.486, "worker": "W1", "level": "INFO", "logger": "jobs.worker", "message": "claimed job 1 on slot 2" },
      { "t": 1.505, "worker": "W1", "level": "INFO", "logger": "jobs.scheduler",
        "message": "job 1 on slot 2: success (process 2 exited with code 0, 0.54s)" }, …
    ],
    "jobs": [                                  // 접수된 6개. GET /jobs/<id>의 응답 + 상대 시각
      { "id": 1, "status": "success", "exit_code": 0, "detail": "process 2 exited with code 0",
        "binary": "hello.elf", "slot": 2,
        "created_at": "…", "started_at": "…", "finished_at": "…",   // DB에 저장된 원본 (UTC ISO)
        "t_created": 0.056, "t_started": 0.482, "t_finished": 1.516, // 같은 값을 첫 업로드 기준 초로
        "serial_log": "MyOS - a 32-bit x86 kernel built from scratch\n[boot] …\n> exec hello.elf\n…" }, …
    ]
  },

  "race": {                                    // 2막
    "config": { "workers": 4, "jobs": 4, "slots": 2, "race_window_seconds": 0.05, "run": "real QEMU", "rounds": 3 },
    "modes": {
      "unsafe": [ {                            // 라운드 3개
        "round": 1, "violated": true, "peak": 4, "over_capacity_moments": …,
        "double_booked_claims": 6, "duplicate_runs": 5, "executions": 9,
        "db_max_busy_slots": 2, "db_max_running_jobs": 2,
        "events": [ { "t": 0.0, "kind": "start", "worker": "W3", "slot": 1, "job": 1, "status": null },
                    { "t": 0.554, "kind": "end", "worker": "W3", "slot": 1, "job": 1, "status": "success" }, … ],
        "timeline": [ "  + 0.000s  W3  START job 1 on slot 1   running=1", … ]   // test_concurrency.py가 찍는 그대로
      }, … ],
      "locked": [ … ]                          // 같은 모양
    },
    "sql": {                                   // 각 함수가 MySQL에 실제로 보낸 문장 (Django가 기록)
      "claim_next":   [ "BEGIN", "SELECT … FOR UPDATE SKIP LOCKED", "SELECT … FOR UPDATE SKIP LOCKED", "UPDATE …", "UPDATE …", "COMMIT" ],
      "claim_unsafe": [ "BEGIN", "SELECT … LIMIT 1", "SELECT … LIMIT 1", "UPDATE …", "UPDATE …", "COMMIT" ],
      "release":      [ "BEGIN", "UPDATE … WHERE (current_job_id = … AND id = …)", "UPDATE … WHERE (id = … AND status = 'running')", "COMMIT" ]
    }
  }
}
```

- **시각**: 1막의 `t`, `t_*`는 **첫 업로드 요청을 보낸 순간 기준 초**다. 2막 `events[].t`는 **그 라운드 첫 이벤트 기준 초**다.
- **1막 애니메이션은 `jobs[]`의 `t_created / t_started / t_finished`와 `slot`으로 만든다.** DB가 저장한 값이라 정확하다.
  슬롯은 `t_started`에 그 작업으로 차고 `t_finished`에 빈다(release가 같은 트랜잭션에서 둘 다 바꾼다).
- 2막 `events`는 워커 프로세스가 스스로 기록한 START/END다. `timeline`은 같은 내용을 스크립트가 찍은 문자열이다(표시용 참고).
- `serial_log`는 VM 한 대의 **전체** 시리얼 출력이다. 매 작업이 MyOS를 새로 부팅하므로 앞 26줄 정도는 매번 같은 부팅 로그다.
  그 뒤에 `Welcome to User Land Shell!`, `Type 'help' for commands.`, `> exec hello.elf`가 오고 프로그램 출력이 이어진다.
- **주의: 모든 로그에 `exec hello.elf`가 찍힌다.** 버그가 아니다. Broker는 업로드한 파일을 디스크의 `hello.elf` 자리에 넣어 실행한다
  (MyOS의 `mkfs`가 파일 이름을 고정해 두었기 때문). 로그 패널에 이 사실을 한 줄로 알린다(§4.4 문구).
- `spin.elf`(timeout)는 로그 끝에 셸 프롬프트가 없다. 무한 루프 중에 3초가 되어 VM을 죽였기 때문이다.
- 전부 ASCII다. 가장 긴 로그 줄은 71자다. 부팅 로그 태그(`[cpu ]`, `[cow ]` …)는 폭이 고정이니 trim하지 않는다.
- SQL의 `started_at = '2026-10-13 …'` 같은 값도 녹화 그대로다. 줄이지 않는다(보여줄 때 가로 스크롤 또는 접기).

### 3.3 실제 내용 요약 (현재 캡처 기준. 값은 transcript에서 읽는다)

**1막**: 업로드 8개(약 0.2초 간격, 1.5초 동안)

| # | 파일 | 요청 | 결과 | 판정 근거(detail) |
|---|---|---|---|---|
| 1 | `hello.elf` | `POST /jobs` | 201 → `success`, exit 0 | `process 2 exited with code 0` |
| 2 | `producer_consumer.elf` | `POST /jobs` | 201 → `success`, exit 0 | 스레드 6개가 먼저 끝나도 셸이 기다린 프로세스의 종료 코드만 본다 |
| 3 | `fail.elf` | `POST /jobs` | 201 → `failed`, exit 3 | `process 2 exited with code 3` |
| 4 | `crash.elf` | `POST /jobs` | 201 → `crashed` | `kernel halted: Page Fault` (null 주소 쓰기) |
| 5 | `spin.elf` | `POST /jobs` | 201 → `timeout` | `no verdict within 3.0s (while running the job)` |
| 6 | `corrupt.elf` | `POST /jobs?validate=false` | 201 → `failed`, exit 1 | MyOS ELF 로더가 거절: `Invalid ELF Magic.` |
| 7 | `corrupt.elf` | `POST /jobs` | **400** `not_elf` | 서버의 헤더 검사가 큐에 넣기 전에 거절 |
| 8 | `too_big.elf` | `POST /jobs` | **400** `file_too_large` | 24,577바이트 > 48 블록 × 512 |

- 전체 약 5.7초. `spin.elf`가 3초를 다 쓰는 동안 다른 워커가 나머지를 처리한다.
- 워커는 일이 없으면 0.5초마다 큐를 확인하므로 첫 claim은 업로드 약 0.4초 뒤다(녹화 그대로 둔다).
- 같은 파일 `corrupt.elf`가 6번은 MyOS 로더까지 가서 `failed`, 7번은 서버에서 400이다. 검증을 건너뛰는 옵션의 의미를 보여주는 쌍이다.

**2막**: 라운드마다 워커 4개가 barrier에서 동시에 출발

| 지표 | unsafe | locked |
|---|---:|---:|
| 위반이 있었던 라운드 | 3/3 | 0/3 |
| 동시에 돈 VM 최대 (용량 2) | 4 | 2 |
| 실행 횟수 / 작업 수 (라운드당) | 9/4 | 4/4 |
| 같은 슬롯 중복 claim (라운드당) | 6 | 0 |
| **DB가 본 바쁜 슬롯 최대** | **2** | 2 |

- unsafe 1라운드: 0.01초 안에 W1~W4가 모두 `job 1 on slot 1`을 시작한다. 작업 1이 4번 돈다.
- 마지막 행이 핵심이다. Slot 행이 2개뿐이라 DB는 잘못된 구현에서도 "바쁜 슬롯 2"로 보인다. 워커가 스스로 남긴 시각만이 경쟁을 드러낸다.

---

## 4. 화면, 입력, 단계별 문구 (영어 UI 문구는 그대로 사용)

### 4.1 화면 구성

창 하나에 탭이나 단계 두 개: **`1 Queue`**, **`2 Race`**. 넓은 창 기준 배치:

```
┌ Queue ───────────────────────────────────────────────────────────┐
│ [Upload 8 samples]                         Stop 1 of 4  ■■□□     │
│ ┌ Slots ─────────┐ ┌ Queue / Jobs ─────────────────────────────┐ │
│ │ Slot 1  #4 crash│ │ #1 hello.elf        ● success   slot 2    │ │
│ │ Slot 2  #5 spin │ │ #5 spin.elf         ◌ running   slot 2    │ │
│ └─────────────────┘ │ #6 corrupt.elf      ○ queued              │ │
│ ┌ Uploads ────────┐ │ corrupt.elf   400 not_elf                 │ │
│ │ POST /jobs  201 │ └───────────────────────────────────────────┘ │
│ └─────────────────┘ ┌ Serial log · #4 crash.elf ────────────────┐ │
│                     │ (터미널 컴포넌트)                           │ │
│ 설명 카드            └───────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

- **슬롯 2칸**: 비었으면 `Idle`, 차면 작업 번호와 파일 이름, 경과 시간 막대.
- **작업 목록**: 접수된 작업과 400 거절이 업로드 순서로 쌓인다. 상태 배지 6종(`queued`, `running`, `success`, `failed`, `crashed`, `timeout`).
  색은 사이트 토큰 안에서: `running` = 포인트 색, `success` = `--jos-success-text`, `failed`/`crashed` = `--jos-danger-text`,
  `queued` = 흐린 글씨, `timeout` = 기본 글씨 + 테두리. 색만으로 구분하지 않도록 배지에 글자를 쓴다.
  (Broker 저장소의 실제 대시보드 `jobs/static/jobs/dashboard.css`도 2026-10-12에 이 토큰으로 맞췄다. 참고용.)
- **워커 로그(선택, 권장)**: `worker_log`를 시각에 맞춰 한 줄씩. `W1`/`W2` 앞머리로 구분.
- **시리얼 로그 패널**: 작업을 누르면 그 작업의 `serial_log`. MyOS 데모의 터미널 컴포넌트를 재사용한다.
- **설명 카드**: 단계가 끝날 때마다 하나. 넓은 창에서는 옆, 좁은 창과 컴팩트 셸에서는 아래.
- **진행 표시**: `Stop N of 4`와 4칸 막대(Upload, Results, Race, Fix).
- **"Read the code" 링크**: `https://github.com/JihoonChoi1/myos-broker/blob/<commit>/<path>`. `<commit>`은 transcript의 `commit`.

### 4.2 단계 0. 인트로

- Title: `MyOS Broker`
- Subtitle: `A job queue that boots a fresh MyOS VM for every program you upload.`
- Body:
  `This is a recorded session of the real system: a Django server, a MySQL queue, two worker processes and QEMU. Upload the samples, watch two workers share two slots, then see what happens when the same workers claim jobs without row locks.`
- What to watch (3 bullets):
  - `Every upload gets its own disk image and its own VM. Two slots cap how many run at once.`
  - `The verdict comes from the VM's serial output: exit code, kernel halt, or a 3-second timeout.`
  - `Without a row lock, four workers grab the same job at the same moment, and the database never shows it.`
- Button: `Upload 8 samples` (단축키 Enter)

### 4.3 Stop 1. Upload (업로드와 큐)

버튼을 누르면 `submissions` 8개를 녹화 시각대로 재생한다. 접수된 것은 `queued`로 목록에 들어가고, 이어서 `jobs[]` 시각대로
슬롯을 잡고(`running`) 끝난다(판정). 6개가 모두 끝나면(마지막 `t_finished` 뒤) 카드가 뜬다.

- Title: `Six queued, two turned away`
- What just happened:
  `Each upload is checked before it reaches the queue: an i386 ELF header, and at most 24 KiB, because MyOS's file system holds 48 blocks of 512 bytes per file. Two files failed those checks and got HTTP 400. The other six became rows in MySQL with status queued. Two worker processes poll that table. Each one claims a free slot and the oldest queued job in one short transaction, builds a disk image with the job's ELF on it, and boots MyOS in QEMU.`
- 강조: 400 두 줄(응답의 `error.code`와 `error.message`를 그대로 보여준다), 두 슬롯이 동시에 차는 순간.
- Read the code: `jobs/views.py`, `jobs/validation.py`, `jobs/scheduler.py`
- 다음: `See the results`

### 4.4 Stop 2. Results (판정과 시리얼 로그)

- Title: `One VM per job, judged by its serial log`
- What just happened:
  `QEMU's serial port is the only window into the VM. The worker types exec into MyOS's shell, then reads the output. The shell prints "Child exited with code" when the program ends, and the kernel's last exit line before it is the program's own, not one of its threads'. A kernel halt means crashed; no verdict in 3 seconds means timeout, and the VM is killed. The database lock is released while QEMU runs, so a slow job never blocks the other worker.`
- 판정 5종 미니 표(권장). 각 행을 누르면 그 작업의 로그가 열린다. `detail` 값은 transcript에서 읽는다.

  | job | verdict | why |
  |---|---|---|
  | `hello.elf` | success | `[proc] PID 2 exited with code 0` |
  | `fail.elf` | failed | exit code 3 |
  | `crash.elf` | crashed | `[!] EXCEPTION: Page Fault!` then `System Halted.` |
  | `spin.elf` | timeout | no verdict within 3.0 s |
  | `corrupt.elf` | failed | MyOS's ELF loader: `Invalid ELF Magic.` |

- 로그 패널 머리말(흐린 글씨, 한 줄):
  `Every job's file is written onto the disk as hello.elf, so each log runs exec hello.elf.`
- 로그 강조(로그를 열었을 때. 매칭 0개면 개발 모드 경고):
  - `> exec hello.elf` (명령 줄. 그 위의 부팅 로그는 기본으로 접어 두고 `Boot log (N lines)`로 펼칠 수 있게 한다)
  - prefix `Child exited with code:`, 그 바로 앞의 마지막 `[proc] PID` 줄
  - crash: prefix `[!] EXCEPTION`, `System Halted.` / corrupt: prefix `[elf ] Error:`
- `producer_consumer.elf`의 로그에서 `[proc] PID N exited` 줄이 여러 개인 것을 짧게 설명해도 좋다(선택):
  `Six threads exit before the program does. Only the exit right before the shell's notice counts.`
- Read the code: `jobs/runner.py` (`classify`, `run_qemu`)
- 다음: `Race the workers`

### 4.5 Stop 3. Race (클라이맥스, unsafe)

2막으로 넘어간다. 위에 설정 한 줄: `4 workers · 4 jobs · 2 slots · all released at once`.
`modes.unsafe[0].events`를 타임라인 차트로 재생한다.

- **차트**: 가로축 시간(초), 세로축 워커 4줄(W1~W4). 각 START~END를 막대로 그리고 막대에 `job N · slot S`.
  위쪽에 "동시에 도는 VM 수" 계단 그래프와 용량선 `capacity 2`. 2를 넘는 구간을 포인트 색으로 칠한다.
  같은 작업 번호가 겹치는 막대는 연결선이나 같은 무늬로 묶는다.
- 차트 옆이나 아래에 숫자 두 개를 나란히(이 대비가 이 데모에서 가장 중요한 그림이다):
  - `VMs running at once (workers' own clocks): 4` ← `peak`
  - `Busy slots the database ever showed: 2` ← `db_max_busy_slots`
- Title: `Four workers, one job, same instant`
- What just happened:
  `This claim reads a free slot and a queued job, then writes busy, inside a transaction but without a lock. A plain SELECT doesn't block anyone, so all four workers read slot 1 as idle and job 1 as queued before any of them wrote. Each believes it owns them. Job 1 ran four times at once. The slots table only has two rows, so it never showed more than two busy slots: the database looked fine the whole time.`
- 문구의 `four workers`, `slot 1`, `job 1`, `four times`는 현재 캡처의 1라운드 값이다(0.01초 안에 W1~W4가 모두 job 1 on slot 1).
  재캡처 후 1라운드가 다르면 문구를 그 값에 맞춘다. 숫자는 가능하면 transcript에서 채운다.
- `sql.claim_unsafe`의 두 SELECT를 코드 블록으로 보여준다(`FOR UPDATE`가 없다는 것이 보이게).
- 라운드 요약(작게): `Rounds with a violation: 3/3` ← unsafe 라운드 중 `violated` 개수
- Read the code: `jobs/scheduler.py` (`claim_unsafe`), `test_concurrency.py`
- 다음: `Add the row lock`

### 4.6 Stop 4. The fix (locked)

`modes.locked[0].events`를 같은 차트로 재생한다. 가능하면 unsafe 차트 아래에 같은 축으로 쌓아 비교한다.

- Title: `SELECT … FOR UPDATE SKIP LOCKED`
- What just happened:
  `The same claim with a row lock. The first worker to read slot 1 locks it; the others skip locked rows instead of waiting, and take slot 2 or find nothing free. The job row is locked too, so two workers holding different slots can't pick the same job. Slot first, then job, in every transaction. Never more than two VMs, and each job ran exactly once.`
- `sql.claim_next`의 두 SELECT를 코드 블록으로(끝의 `FOR UPDATE SKIP LOCKED`를 포인트 색으로).
- unsafe/locked 비교 미니 표(권장. §3.3의 2막 표와 같은 행. 값은 transcript의 라운드들에서 계산)
- 작은 주석(카드 하단, 흐린 글씨):
  `The unsafe claim waits 50 ms between reading and writing to make the race easy to see; it double-books even at 0. Timings are QEMU on a laptop, not a benchmark.`
- Read the code: `jobs/scheduler.py` (`claim_next`, `release`)

### 4.7 마무리 화면

- Title: `What you just saw`
- 3 bullets:
  - `A Django and MySQL job queue that boots a fresh MyOS VM in QEMU for every uploaded program.`
  - `Verdicts read from the VM's serial output: success, failed, crashed or timed out, with the log kept.`
  - `Row locks with SKIP LOCKED so concurrent workers never claim the same slot or job, and a race demo that proves it.`
- Buttons: `Replay`, `View source` (Broker GitHub), `See MyOS` (MyOS 데모 앱이 있으면 그 앱을 열고, 없으면 MyOS GitHub)

---

## 5. 재생 규칙

### 5.1 시간

- 순서와 내용은 transcript 그대로다. 작업 번호, 슬롯 번호, 판정, 로그를 빼거나 고치거나 지어내지 않는다.
- 1막은 **실제 시간 그대로(1배속)** 재생해도 약 6초다. 그대로 쓴다. 너무 빠르면 0.5배속까지 늦춰도 된다(전체를 같은 비율로).
- 2막 unsafe/locked 라운드는 각각 약 2~3초다. 1배속 또는 0.5배속. 차트 아래에 `Recorded timing` 표시.
- 재생 중 Enter나 클릭 → 그 단계를 끝 상태로 즉시 건너뛴다.
- 모션 줄이기(`prefers-reduced-motion` 또는 사이트 설정)가 켜져 있으면 애니메이션 없이 끝 상태를 바로 그린다.
- 2막은 기본으로 1라운드만 보여준다. 라운드 2, 3은 `Round 1 / 2 / 3` 선택으로 볼 수 있으면 좋다(선택).
  특정 라운드만 골라 보여주지 않는다. 기본은 항상 1라운드다.

### 5.2 화면 표현

- JihoonOS 디자인 토큰(다크 그래파이트, 포인트 색 하나)을 따른다. 장식, 가짜 CRT 효과 없음.
- 시리얼 로그의 색 규칙은 MyOS 데모 터미널과 같다(커널 태그 줄은 흐린 보조 색, `[cow ]`는 포인트 색, 프로그램 출력은 기본 색).
- 차트는 얇은 선 SVG. 3D 화면 텍스처로 캡처돼도 읽히게 글자를 사이트 기준보다 작게 만들지 않는다.
- 긴 SQL과 로그 줄은 줄바꿈하지 말고 가로 스크롤한다(컴팩트 셸에서는 줄바꿈 허용).

### 5.3 접근성

- 설명 카드는 `aria-live="polite"`로 한 번만 읽힌다. 작업 목록 갱신이나 로그 줄을 live region으로 읽지 않는다.
- 작업 행, 단계 버튼은 실제 `<button>`이고 포커스 링이 보인다.
- 타임라인 차트에는 같은 내용을 담은 텍스트 대체(예: 접을 수 있는 `timeline` 문자열 목록, 또는 `<table>`)를 둔다.

---

## 6. 선택(권장): claim → run → release 다이어그램

Stop 1이나 Stop 2 카드에 작은 그림. 세 단계 중 DB 트랜잭션은 앞뒤 둘뿐이고 가운데(QEMU 실행)는 잠금 없이 돈다는 것을 보여준다.

```
 worker ──▶ [claim]  BEGIN · lock slot + job · busy/running · COMMIT      (ms)
        ──▶ [run]    copy artifacts · mkfs · QEMU · read serial         (0.2–3 s, no transaction)
        ──▶ [release] BEGIN · save verdict + log · slot idle · COMMIT     (ms)
```

1막 재생 중 해당 워커가 지금 어느 칸에 있는지 불을 켜면 좋다(`t_started`, `t_finished` 기준. claim과 release는 순간으로 표시).

---

## 7. 신뢰 표시 (반드시 넣는다)

데모 창 하단에 항상 보이게 둔다.

`Recorded from a real run: Django, MySQL 8.4, two workers, QEMU · commit aff6342 · captured 2026-10-13 · Run it yourself: github.com/JihoonChoi1/myos-broker`

- `commit`, 날짜, MySQL 버전은 transcript의 `commit`, `captured_at`, `versions.mysql`에서 읽는다. 하드코딩하지 않는다.
- commit은 `https://github.com/JihoonChoi1/myos-broker/commit/<commit>` 링크로 만든다(push된 뒤에만 열린다).
- `dirty: true` 또는 `myos_dirty: true`인 transcript가 들어오면 빌드나 테스트가 실패하게 한다(배포 사고 방지).
- Broker에는 Docker 이미지가 없다(MySQL만 Docker로 띄운다). "Run it yourself"는 GitHub 링크만 둔다.

---

## 8. 프로젝트 카드 콘텐츠 (Projects 앱용)

사이트의 `src/content/projects.js` 스키마(`CONTENT_GUIDE.md` §2)에 맞춘 값이다. 데모 앱 링크는 `kind: 'demo'`를 쓴다.
`demo` 링크가 외부 URL만 받는 구조라면 내부 앱을 여는 방법을 사이트 규칙에 맞게 추가한다(MyOS, T1 데모와 같은 방법).
태그 `backend`는 이미 있다. MyOS 핸드오프가 `systems` 태그를 추가하면 같이 단다.

```js
{
  id: 'myos-broker',
  title: 'MyOS Broker',
  summary: 'A job queue that runs uploaded programs on MyOS in QEMU',   // ≤ 70자
  year: 2026, period: { start: '2026-03', end: '2026-03' },          // 커밋 기준 2026-03-07 ~ 03-29
  role: 'Solo developer',
  status: 'archived',               // 사용자 확인(2026-10-12): 완성된 프로젝트
  tags: ['backend'],                // + 'systems' (MyOS 핸드오프가 추가하면)
  stack: ['Python', 'Django', 'MySQL (InnoDB)', 'QEMU', 'Docker'],
  cover: null, gallery: [],         // 대시보드 스크린샷을 쓰려면 content-src/projects/myos-broker/에 넣고 npm run images
  sections: [
    { kind: 'overview', body: [
      'A Django service for MyOS, my own x86 operating system: upload an ELF, and a worker builds a fresh disk image, boots MyOS in QEMU, runs the program and judges it from the serial log as success, failed, crashed or timed out.' ] },
    { kind: 'approach', body: [
      'Workers claim a slot and a job with `SELECT … FOR UPDATE SKIP LOCKED` in one short transaction, run QEMU **outside** any transaction, then release both in another. Locks are always taken slot first, then job.',
      'A worker that dies mid-run leaves its slot busy, so a reaper fails stale jobs and frees their slots; a late worker can only release a slot it still owns.',
      'The verdict comes from the shell\'s exit notice and the kernel\'s last exit line before it, so a thread exiting early is never mistaken for the program.' ] },
    { kind: 'result', body: [
      'A race demo releases four worker processes at once against two slots. Without row locks, one job ran on four VMs at the same moment while the slots table never showed more than two busy. With them: two at a time, each job exactly once.' ] },
    { kind: 'learned', body: [
      '`transaction.atomic()` alone does not stop a race: a plain `SELECT` takes no lock, so every worker can read the same row as free. And the database\'s own state could not reveal the bug. Only the workers\' timestamps did.' ] },
  ],
  highlights: ['Same job on 4 VMs without row locks; exactly once with them'],
  links: [
    { kind: 'github', url: 'https://github.com/JihoonChoi1/myos-broker' },
    { kind: 'demo', url: /* the MyOS Broker demo app */ },
  ],
  featured: true,                   // 사용자 확인(2026-10-12): MyOS와 둘 다 대표 프로젝트
},
```

- `result`와 `highlights`의 숫자(4 VMs)는 현재 transcript 기준이다. 재캡처 후 `peak`가 달라지면 같이 고친다.
- 표기는 **MyOS**(대문자 M)다. Broker 저장소도 2026-10-12에 `myOS` → `MyOS`로 맞췄다. 프로젝트는 MyOS와 **별개의 카드, 별개의 데모 앱**이다.
- 근거: README "Preventing duplicate runs", `jobs/scheduler.py` docstring, 이 transcript.

---

## 9. 완료 기준

- [ ] 인트로 → Upload → Results → Race → Fix → 마무리까지 키보드만으로, 그리고 클릭만으로 끝까지 진행된다.
- [ ] 1막: 업로드 8개(201 6개, 400 2개), 슬롯 2칸의 점유, 판정 5종이 transcript 시각대로 재생된다. 슬롯 번호를 하드코딩하지 않는다.
- [ ] 작업 6개 모두 시리얼 로그가 열리고, 강조 줄이 매칭된다(0개 경고 없음). 부팅 로그는 접혀 있다.
- [ ] 2막: unsafe/locked 차트, 용량선, `peak`와 `db_max_busy_slots` 대비, 두 SQL이 transcript 값으로 나온다.
- [ ] 신뢰 표시에 transcript의 commit, 날짜, MySQL 버전이 나오고, `dirty`/`myos_dirty`가 true면 빌드나 테스트가 실패한다.
- [ ] 모션 줄이기, 컴팩트(모바일) 셸, 낮/밤 테마, 3D 화면 텍스처에서 모두 읽힌다.
- [ ] 사이트의 검증 절차(스크린샷 확인, `npm test`, `npm run build`, 용량 예산)를 통과한다. 데모 코드와 JSON은 앱을 열 때만 로드한다.
- [ ] 배포 전: Broker 변경이 커밋, 재캡처, push되어 commit 링크와 "Read the code" 링크가 열린다. MyOS도 push되어 있다.

## 10. 하지 말 것

- 로그, 응답, 시각, SQL을 손으로 고치거나 지어내기. 바꿔야 하면 Broker 쪽을 고치고 다시 캡처한다.
- 브라우저에서 "업로드"를 진짜처럼 꾸미기(파일 선택 창 등). 방문자가 고른 파일이 실행되는 것처럼 보이면 안 된다.
  버튼은 `Upload 8 samples`처럼 녹화된 샘플을 재생한다는 것이 드러나게 한다.
- "live", "real-time", "running now" 같은 표현. `Recorded run` / `replay`라고 쓴다.
- QEMU 실행 시간이나 처리량을 성능 지표처럼 표현하기.
- 2막에서 특정 라운드만 골라 보여주기, unsafe가 "항상 정확히 4대"라고 쓰기(라운드마다 다를 수 있다. 값은 transcript에서).
- 이번 버전에서 서버를 실제로 띄우기. 공개 서버로 임의 바이너리를 받는 것은 이 프로젝트의 범위 밖이다(README "Current scope and limitations").

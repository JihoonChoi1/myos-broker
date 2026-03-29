# myOS Broker

**A server that automatically runs uploaded myOS programs in a virtual machine and shows their results.**

[myOS](../my-os) is a 32-bit x86 operating system I built in C and Assembly. This project uses Python and Django to let users request and manage myOS program runs through the web.

For a detailed explanation of every file and the execution flow, starting with Python and Django basics, read the [complete myOS Broker guide](ARCHITECTURE.ko.md). This README focuses on understanding and running the project. **The other guides are in Korean; the website interface is in English.**

To read the source code from the beginning, start with the [code reading guide](CODE_READING.ko.md). Its seven chapters cover how each file is generated, reproduction commands, Python syntax, Django functions, the corresponding SQL, and how the worker and QEMU fit together.

## What does this project do?

When a user submits an **ELF executable** built for myOS, the server processes it as follows:

```text
Upload file
  → Check file size and basic format
  → Store a job in the MySQL queue
  → Worker claims an available execution slot
  → Create a disk image for the job
  → Boot myOS in QEMU and run the program
  → Classify the result as success, failure, crash, or timeout
  → Save the result and serial log
```

Common terms used in this README:

| Term | Meaning |
|---|---|
| Django | Python web framework that handles requests and stores data in the database |
| MySQL | Database that stores job states, execution slots, and results |
| Worker (`run_worker`) | Separate process that picks up queued jobs and runs them |
| QEMU | Program that provides the virtual machine used to boot myOS |
| ELF | Binary file format that myOS can load and run |
| Job (`Job`) | One file submission and its execution result |
| Slot (`Slot`) | Permit that limits how many jobs can run at once |
| Serial log | Record of output from myOS and the program while they run |

**Django and Python do not run inside myOS.** Django and the worker run on a host OS such as macOS. Only the submitted ELF runs inside myOS in QEMU.

## Why use it?

You do not need Django to boot myOS and run a program manually. This server is useful for:

- Automating the repeated work of preparing a disk and starting QEMU for each file
- Keeping results and error logs for multiple programs
- Limiting how many jobs run at once when requests pile up
- Coordinating workers so they do not run the same job twice
- Seeing why database transactions and row locks matter in a working system

This repository also includes a concurrency demo that compares execution with and without locks. The main purposes visible in the code are **automating myOS runs and learning and verifying database concurrency**. The code alone cannot establish any other personal motivation behind the project.

## What needs to be running?

The default setup needs these three components:

| Component | Role | If it is stopped |
|---|---|---|
| Docker and the MySQL container | Provide the database | Jobs cannot be registered, queried, or processed correctly |
| Django web server | Provide the website and API | The website is unavailable |
| Worker | Run queued jobs | Submitted jobs remain `queued` |

**Docker is used to run MySQL in the default setup.** You can run without Docker if you configure a local MySQL installation or an external MySQL server. The worker starts and stops QEMU for each job, so you do not need to launch QEMU yourself.

### Once installed: start the project

Run **this one command** in a terminal in the project directory:

```sh
./start.sh
```

It automatically:

1. Checks the required Python packages, myOS build artifacts, QEMU, and web port.
2. Opens Docker Desktop on macOS if it is not running.
3. Starts the existing `myos-broker-mysql` container and waits for a database connection.
4. Applies pending database migrations.
5. Starts the Django web server and **two workers**.
6. Opens **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** in your browser.

**Keep this terminal open. Press `Ctrl+C` once to stop.** This stops only the web server and workers started by this command, giving active jobs time to finish. It leaves the MySQL container and Docker Desktop running. Existing job records and uploaded files are preserved. To stop the database too, run `docker stop myos-broker-mysql` after exiting. The next `./start.sh` will start it again.

You must complete the one-time steps under **First-time setup** below to install Python packages, create the MySQL container, and build myOS. The start command reuses the prepared environment. The service cannot keep running while your Mac is asleep or off.

Options:

```sh
./start.sh --port 8001       # Use when another app occupies port 8000
./start.sh --no-browser      # Do not open a browser automatically
./start.sh --workers 1       # Start only one worker
./start.sh --no-docker       # Use locally installed or externally hosted MySQL
```

The default two workers match the two default slots. Adding workers does not add slots. Environment variables are passed to both the web server and workers. For example, `JOB_TIMEOUT_SECONDS=5 ./start.sh` changes the time limit. Unless you set `MYOS_REPO`, the start script uses the `my-os` directory next to this project.

The development server reloads changes to the web Python code automatically. **For worker code or environment variable changes, press `Ctrl+C` and restart.** The script prevents another `./start.sh` run for the same project, but it does not detect or stop `run_worker` processes started separately.

Commands for running each component separately, along with an explanation, are in the [architecture guide](ARCHITECTURE.ko.md#runbook).

## First-time setup

Run these commands from the `myos-broker` directory for a local development environment. Skip any steps you have already completed.

### 1. Install the required tools

- Python: runs Django and the workers
- Docker Desktop: runs MySQL in the default setup
- QEMU: runs the myOS virtual machine
- A built `my-os` project: provides the kernel, shell, and disk creation tool
- To rebuild myOS and the examples: `make`, NASM, `x86_64-elf-gcc` and its linker, and a host C compiler

The Python version used to verify this project is **3.14.3**. Python package versions are pinned in [requirements.txt](requirements.txt).

### 2. Create the MySQL container

Start Docker Desktop, then run this command once:

```sh
docker run -d --name myos-broker-mysql \
  -p 127.0.0.1:3307:3306 \
  -e MYSQL_ROOT_PASSWORD=broker-root \
  -e MYSQL_DATABASE=myos_broker \
  -e MYSQL_USER=broker \
  -e MYSQL_PASSWORD=broker \
  mysql:8.4
```

These passwords are examples for local practice. If the container already exists, use `docker start myos-broker-mysql` instead of creating it again. Port 3306 inside the container maps to port 3307 on your Mac, so Django connects to port 3307 by default.

Once MySQL is ready, create the demo database and grant the permissions needed for tests:

```sh
docker exec myos-broker-mysql mysql -uroot -pbroker-root -e "
  CREATE DATABASE IF NOT EXISTS myos_broker_demo;
  GRANT ALL ON myos_broker_demo.* TO 'broker'@'%';
  GRANT ALL ON test_myos_broker.* TO 'broker'@'%';"
```

The concurrency behavior relies on **InnoDB row locks** in MySQL. Switching to SQLite will not verify the same behavior.

### 3. Install Python packages and prepare database tables

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
```

`.venv` is a virtual environment that keeps this project's Python packages separate. `migrate` creates the database tables and the two default slots.

### 4. Build myOS and the examples

The default myOS path is `/Users/jihoonchoi/Desktop/my-os`. Set `MYOS_REPO` if it is elsewhere:

```sh
export MYOS_REPO=/Users/jihoonchoi/Desktop/my-os
make -C "$MYOS_REPO" disk.img
./samples/build.sh
```

The Broker uses myOS's `boot.bin`, `loader.bin`, `kernel.bin`, `mkfs`, shell, and default ELF build artifacts. **It does not recompile the kernel for each job.** It copies the existing artifacts and the submitted file to a temporary directory, then creates only a new disk image for that job.

`mkfs` must be executable on the host running the Broker. An `mkfs` binary built on Linux cannot be assumed to run on macOS.

When setup is complete, follow [Start the project](#once-installed-start-the-project) above to run the server and workers.

## Using the website

1. Select `samples/hello.elf` under **ELF file to run**.
2. Click **Add to queue**.
3. Check its status under **Run history**.
4. Click the file name to see the exit code, classification details, and serial log.

The page checks for status updates about every two seconds. **The serial log appears after the job finishes.** An empty log while a job is running is expected with the current design.

| Status | Meaning |
|---|---|
| `queued` | Waiting for a worker to pick it up |
| `running` | Has a slot and is preparing the disk, running the VM, or completing related work |
| `success` | Program exited with code 0 |
| `failed` | Program exited with a nonzero code, or the Broker encountered an execution error |
| `crashed` | A myOS halt message was detected |
| `timeout` | No final classification was obtained within the time limit |

`success` means the program exited normally. The Broker does not grade whether its output matches an expected answer.

## Using the API

You can use the same features through `curl` or another program instead of the website. Do not add a trailing `/` to these API paths.

| Request | Action |
|---|---|
| `POST /jobs` | Submit an ELF file and create a job |
| `GET /jobs` | List jobs |
| `GET /jobs?status=crashed,timeout` | List jobs with selected statuses |
| `GET /jobs/<ID>` | Get job details, including the log |
| `GET /slots` | Show the status of each slot |

```sh
# Submit a file: the response's id is the new job ID.
curl -F binary=@samples/hello.elf http://127.0.0.1:8000/jobs

# List all jobs
curl http://127.0.0.1:8000/jobs

# Get job details: replace 1 with the id from the submission response.
curl http://127.0.0.1:8000/jobs/1

# List only crashed or timed-out jobs
curl 'http://127.0.0.1:8000/jobs?status=crashed,timeout'

# Check the current slot status
curl http://127.0.0.1:8000/slots
```

**HTTP 201 means the file was accepted**, not that it ran successfully. Query the job's status later to see the execution result.

### Expected results for the examples

| Example | Expected result | What it checks |
|---|---|---|
| `hello.elf` | `success`, exit code 0 | Basic program execution |
| `producer_consumer.elf` | `success`, exit code 0 | Multithreaded program execution |
| `fail.elf` | `failed`, exit code 3 | Nonzero exit code |
| `crash.elf` | `crashed` | Page fault caused by a write to a null address |
| `spin.elf` | `timeout` | An infinite loop |
| `corrupt.elf` | HTTP 400 with default submission | Invalid ELF format is rejected |
| `too_big.elf` | HTTP 400 | File size limit |

To pass an invalid ELF to the myOS loader and observe its error, you can skip only the header check:

```sh
curl -F binary=@samples/corrupt.elf \
  'http://127.0.0.1:8000/jobs?validate=false'
```

In this case, the myOS ELF loader rejects `corrupt.elf`, and the job result is `failed` with exit code 1. The website's **Skip ELF header validation** option does the same thing.

### Why is the file size limit 24 KiB?

myOS SimpleFS supports 48 data blocks per file, with 512 bytes per block:

```text
48 × 512 = 24,576 bytes = 24 KiB
```

The current `mkfs` does not sufficiently check array bounds for file sizes, so the Broker enforces this limit first. **Empty files and files larger than 24 KiB are rejected even with `validate=false`.** When the Runner is called directly, it also checks the sizes of the submitted file and the default kernel and ELF artifacts.

The basic header check verifies the ELF magic bytes, 32-bit little-endian format, and i386 target. Passing this check does not mean an arbitrary ELF will run correctly or safely on myOS. It also needs compatible myOS libraries and system call conventions.

## How are execution results classified?

`classify()` in [jobs/runner.py](jobs/runner.py) reads QEMU's output.

- The shell's `Child exited with code:` message indicates that the child program has finished.
- The exit code comes from the last `[Kernel] Process N exited with code X` before that message. This avoids mistaking the log of an earlier exiting thread for the program's result.
- `System Halted.` is classified as `crashed`, including a halt during boot.
- If no result can be determined within the time limit, the job is classified as `timeout`.
- Infrastructure errors such as an `mkfs` failure or a missing QEMU executable are recorded as `failed`, with `detail` beginning with `broker error:` to distinguish them.

The default QEMU time limit is **3 seconds, including boot time**. The `mkfs` disk creation tool has a separate 30-second limit. Therefore, the total time from submission to stored result is not necessarily under 3 seconds.

The myOS shell keeps running after a program exits, so the Runner stops QEMU after obtaining the result. The program's exit code and QEMU's own exit code are different values.

## Preventing duplicate runs under concurrent requests

The main code is in [jobs/scheduler.py](jobs/scheduler.py). It divides execution into three stages:

| Stage | Action | Database transaction |
|---|---|---|
| `claim` | Reserve a free slot and a queued job; mark them `busy` and `running` | Short transaction |
| `run` | Copy files, create the disk, run QEMU, and clean up | None |
| `release` | Save the result and return the slot | Short transaction |

A **transaction** commits or rolls back several database changes as a unit. A **row lock** coordinates workers so they cannot claim the same record at the same time.

### Why lock both the slot and the job?

Without locks, two workers can both read a slot as free before either writes that it is busy. Wrapping this sequence in `transaction.atomic()` alone does not prevent the read race.

The working implementation uses `SELECT ... FOR UPDATE SKIP LOCKED`:

- Lock the Slot so the same execution capacity cannot be assigned twice.
- Lock the Job so the same job cannot be selected twice.
- Skip rows locked by another worker instead of waiting for them.
- Acquire locks in Slot → Job order.

The scheduler tries to pick the oldest job first, but skipping locked jobs means strict start or completion order is not guaranteed under every concurrent schedule. A consistent lock order does not guarantee that database deadlocks can never occur, and transient database errors are not currently retried automatically.

**A slot is not a VM that is already running.** Each job gets a new disk and QEMU process. The two default slots coordinate normal workers so at most two jobs occupy slots at once.

### Why release database locks while the program runs?

This avoids keeping a long transaction open while QEMU runs. If a worker dies midway, however, the saved `running` state is not rolled back automatically.

To handle this, `reap_stale_jobs()` marks jobs older than **the execution time limit plus 30 seconds** as failed and returns their slots by default. A worker that finishes late can release only a slot it still owns, so it cannot accidentally free a slot assigned to a newer job.

The reaper only cleans up database records. It does not check whether a worker actually died or stop any QEMU process left behind. Thus, it does not guarantee a strict cap on live VMs in failure cases such as a paused worker or an orphaned QEMU process.

The database isolation level is `READ COMMITTED`. Ordinary reads see a snapshot of committed data for each statement, while job claiming uses separate locking reads. This does not remove every lock caused by indexes or foreign key checks.

Another approach is to check the number of rows affected by a conditional `UPDATE` instead of using `FOR UPDATE`. That approach must still atomically claim both a slot and a job.

## Running the concurrency demo

[test_concurrency.py](test_concurrency.py) compares two approaches:

| Mode | Implementation | Purpose |
|---|---|---|
| `unsafe` | `claim_unsafe()` reads and updates without locks | Reproduce duplicate slot claims and job runs |
| `locked` | `claim_next()` uses row locks | Verify normal job assignment |

By default, the demo uses four worker processes, four jobs, and two slots. Workers wait at a starting barrier, then claim jobs at the same time. It runs actual QEMU by default.

```sh
# Run each approach once and display a timeline
.venv/bin/python test_concurrency.py

# Compare 10 rounds without an artificial delay between reading and writing
.venv/bin/python test_concurrency.py --rounds 10 --race-window 0 --quiet

# Wait 0.5 seconds instead of running QEMU to isolate the race condition
.venv/bin/python test_concurrency.py --fake-run 0.5
```

**The demo resets its dedicated database's Jobs and Slots and the corresponding job files each round.** It uses `MYSQL_DEMO_DATABASE`, not the regular `MYSQL_DATABASE`; its default is `myos_broker_demo`. The name must end in `_demo`. Files are saved in `media/demo/`. Do not store data you want to keep in this database.

These are results from two rounds using real QEMU. Counts can vary depending on how the race unfolds.

| Metric | unsafe | locked |
|---|---:|---:|
| Rounds with an issue | 2/2 | 0/2 |
| Maximum active execution intervals | 4 | 2 |
| Runs / submitted jobs | 18/8 | 8/8 |
| Maximum busy slots observed in the database | 2 | 2 |

The database has only two Slot rows, so even the incorrect implementation cannot show more than two busy slots. **Counting busy database slots alone can therefore miss duplicate runs.** The demo also examines start and end times recorded by workers.

These execution intervals include disk preparation and result storage. They are not a direct measurement of the number of live QEMU processes.

## Configuration

[broker/settings.py](broker/settings.py) reads these environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `MYOS_REPO` | `/Users/jihoonchoi/Desktop/my-os` | Location of myOS build artifacts |
| `QEMU_BINARY` | `qemu-system-x86_64` | QEMU executable |
| `JOB_TIMEOUT_SECONDS` | `3.0` | QEMU boot and execution time limit |
| `MYSQL_HOST` | `127.0.0.1` | MySQL host |
| `MYSQL_PORT` | `3307` | MySQL port |
| `MYSQL_DATABASE` | `myos_broker` | Regular job database |
| `MYSQL_USER` | `broker` | Database user |
| `MYSQL_PASSWORD` | `broker` | Local development database password |
| `MYSQL_DEMO_DATABASE` | `myos_broker_demo` | Dedicated concurrency demo database |

For example, to change the time limit when starting a worker:

```sh
JOB_TIMEOUT_SECONDS=5 .venv/bin/python manage.py run_worker
```

The web server and workers must access the same database and upload storage. **The project does not currently load a `.env` file automatically.** Set environment variables in your shell or use `export`.

## Running tests

With MySQL running, execute:

```sh
.venv/bin/python manage.py test jobs --noinput
```

There are currently **33 tests**. Tests requiring a database use `test_myos_broker` rather than the regular job database. They cover:

- Upload size and ELF header checks
- API, dashboard, and slot views
- Result classification from serial logs
- Slot claiming, release, and stale job recovery
- Runner file size safeguards
- Database separation for the concurrency demo

Separately from these tests, six actual QEMU examples, the MySQL concurrency demo, and the flow from browser upload to displayed result were verified. Detailed results are in the [architecture guide's verification record](ARCHITECTURE.ko.md#verification).

`./start.sh` was also checked with actual MySQL and QEMU: two workers starting on the default port, a successful `hello.elf` run, clean shutdown, duplicate start prevention, and restarting on another port with `--no-docker`. Sending `Ctrl+C` while `spin.elf` was running still let the worker save a `timeout` result before exiting. Verification jobs #15 and #16 remain in the run history. The path that automatically opens Docker Desktop when it is stopped was not reproduced in this verification.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Cannot connect to MySQL | Docker Desktop and the MySQL container are both running; the port is 3307 |
| Job remains `queued` after upload | Check worker errors in the `./start.sh` terminal; if running components manually, check that `run_worker` is running |
| `Already running via ./start.sh` | Use the existing terminal, or stop it with `Ctrl+C` before restarting; there is no need to delete the lock file manually |
| `Port 8000 is unavailable` | Run `./start.sh --port 8001` or stop the existing web server |
| No slots available | Check that `manage.py migrate` has run |
| Missing myOS artifact error | Check `MYOS_REPO` and the result of `make disk.img` |
| Cannot start QEMU | Check its installation and the `QEMU_BINARY` path |
| `running` with an empty log | Logs are saved after a job finishes in the current design |
| ELF upload rejected | Check that it is 32-bit little-endian i386 and no larger than 24 KiB |
| `failed` with `broker error:` | Check the execution environment, including mkfs, QEMU, and files, before investigating the program itself |
| `/jobs/` returns 404 | Use `/jobs` without a trailing slash |

## Current scope and limitations

The current setup focuses on local experiments and learning. The job API does not implement login or per-user permissions, job cancellation or automatic retries, list pagination, live log streaming, or retention policies for uploads and logs.

Result classification also depends on strings in the serial output. A submitted program that prints the same strings can confuse the classifier. A public grading service or a service that runs arbitrary hostile binaries would need additional design for trustworthy classification, host isolation, resource limits, and authentication.

The full file map, database fields, execution flow for one request, and improvement ideas are in [ARCHITECTURE.ko.md](ARCHITECTURE.ko.md).

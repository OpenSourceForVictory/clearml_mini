# ClearML Mini Eval Platform — Implementation Specification

Version: 1.0 (MVP). Target: multiple independent Linux GPU servers, multiuser evaluation submissions, queue scheduling via ClearML Agent. Write implementation and documentation in Chinese where applicable; Python code and identifiers in English.

## Goal / non-goals

Deliver a working, self-hosted MVP with: multiuser login, evaluation submission, fair admission control, one-GPU-per-worker scheduling, Docker execution, worker visibility, logs and metrics, cancellation/retry, simulated evaluation, and a pluggable Isaac Lab runner interface. Default to simulated evaluation so no GPU is needed for CI.

Out of scope V1: Kubernetes, multi-GPU jobs, GPU memory partitioning, preemption, sophisticated GPU topology scheduling, production Isaac Sim installation, untrusted arbitrary code execution, user-provided Docker image/command/host mounts.

## Hard requirements

1. Use FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL, Pydantic v2, React/Vite/TypeScript, ClearML Python SDK, Pytest and Docker Compose. Provide Python 3.11+ compatibility.
2. ClearML Server is an external service defined by `CLEARML_API_HOST`, `CLEARML_WEB_HOST`, `CLEARML_FILES_HOST`, `CLEARML_API_ACCESS_KEY`, `CLEARML_API_SECRET_KEY`. Do not conflate our app's 8000 port with ClearML's 8008 API port. `docker-compose.yml` for our services may use existing ClearML; provide optional official ClearML deployment instructions instead of an unverified vendor compose copy.
3. A user submits an EvaluationJob to our API. Store in PostgreSQL first. Scheduler alone admits tasks to ClearML queues. Do not directly enqueue submitted requests.
4. For V1, each EvaluationJob maps to exactly one EvaluationRun/Task. Keep models separate so sharding can be added later. One GPU slot = one designated ClearML Agent bound to an explicit GPU. Support resource pools by GPU type and labels; queues are backed by concrete slots. To guarantee no task lands on an incompatible GPU, use a per-slot queue OR a homogeneous pool with identical GPU capability, fixed agent binding and admission credits. Choose per-slot queue for the initial robust implementation.
5. Only one Scheduler leader: Postgres advisory lock or equivalent. Avoid duplicate admission after crashes. Store `clearml_task_id`, `queue_name`, admission state and attempt number. Follow outbox/saga pattern since Postgres and ClearML cannot share a transaction. Reconcile orphaned tasks using stable job/attempt marker via task tags/parameters. Never blindly enqueue twice.
6. Fairness: round-robin across active users, FIFO per user, global and per-user running+reserved limits, pending limit per user, and per-resource available slot accounting. Skip unschedulable users without blocking eligible work; do not assume tasks all finish at same time. Scheduler tick (configurable, e.g. 2 seconds) and periodic reconciliation.
7. States: PENDING, RESERVED, ENQUEUED, STARTING, RUNNING, SUCCEEDED, FAILED, CANCEL_REQUESTED, CANCELLED, TIMED_OUT. Record status transitions and timestamps; invalid transitions rejected. Retry creates distinct attempt and Task, does not erase prior history. Support API idempotency key on submission.
8. Heartbeats: prefer ClearML worker inventory as source of agent presence; maintain server-side time-to-live. Offline slot blocks new admissions. Do not mark running task failed immediately upon heartbeat loss. Define configurable startup, execution and lost-worker grace timeouts. Handle reconciliation of transient unknown ClearML states.
9. Docker: Agent runs tasks in preset Runner image. Neither API nor user controls arbitrary Docker image, privileged flags, host mounts or commands. Explicitly document security boundary: on a trusted shared server, Docker alone is not a sufficient sandbox for hostile third-party workloads.
10. User auth: bootstrap admin via environment variable/management command; Argon2/bcrypt password hashing; JWT short expiry; role=admin/user. Authorization checks on every job/log/artifact endpoint. Do not expose ClearML credentials, host-level secrets or task environment to users.
11. Keep artifacts as stdout/metrics for MVP; support optional MinIO/S3 interface for larger artifacts with presigned URLs (not required for first smoke test).
12. Every feature needs tests. CI uses FakeClearMLAdapter and FakeWorkerRegistry so tests run without GPU/ClearML Server; integration smoke test instructions use real ClearML.

## Architecture

Browser -> React UI -> FastAPI -> PostgreSQL
                         | -> Scheduler (single leader, isolated process) -> ClearMLAdapter -> ClearML Server queues -> ClearML Agents (GPU 0/1 etc) -> Docker evaluation Runner
                         | -> Reconciler -> ClearML task/worker API

Use an adapter for ClearML: `create_task`, `enqueue_task`, `get_task`, `abort_task`, `list_workers`, `list_queue_tasks`, `get_console_output` (verify exact official SDK methods and pin dependency version). Real adapter and fake test adapter. For task creation, official `Task.create` with an actual Git repo/commit/script or verified template; don't assume source files in API container magically exist on remote Agents. Include a small demo runner Git repository layout within the monorepo and instructions on setting `RUNNER_REPO_URL` to an Agent-accessible URL and pin `RUNNER_COMMIT` or provide a versioned Docker image with a tested task entrypoint. Running with remote Agent MUST be demonstrated; local Python import alone isn't enough.

## Data model

users(id UUID PK, username unique, password_hash, role, active, created_at)
resource_pools(id UUID PK, name unique, gpu_model, min_vram_gb, enabled)
gpu_slots(id UUID PK, node_name, gpu_index, gpu_uuid nullable, pool_id FK, queue_name unique, enabled, heartbeat_at nullable, agent_id nullable, last_seen_at nullable) [physical UUID preferred, avoid double registration]
evaluation_jobs(id UUID PK, owner_id FK, title, benchmark, model_ref, episodes, pool_id FK, status, priority default 0, idempotency_key nullable scoped per user, created_at, updated_at, queued_at nullable, started_at nullable, finished_at nullable, error nullable)
evaluation_runs(id UUID PK, job_id FK, attempt integer, status, slot_id nullable FK, clearml_task_id unique nullable, queue_name nullable, reservation_expires_at nullable, enqueued_at nullable, started_at nullable, finished_at nullable, result_json JSONB nullable, error nullable, unique(job_id,attempt))
job_events(id UUID PK, job_id FK, run_id nullable, old_status nullable, new_status, message, created_at)
scheduler_state(key PK, value JSONB, updated_at)

Migrate with Alembic; timestamps UTC; indexes on status/owner/created_at and slot/run status. Include per-user uniqueness of idempotency_key when not null.

## API contracts

POST /api/v1/auth/login -> JWT
GET /api/v1/auth/me
GET /api/v1/pools -> visible pools and capacities
GET /api/v1/workers -> admin detailed slots, non-admin aggregate status (avoid leaking host data)
POST /api/v1/jobs -> {title, benchmark:'mock', model_ref, episodes: 1..10000, pool_id, idempotency_key optional header}; response 201 {id,status:'PENDING'}
GET /api/v1/jobs?status=&page=&page_size= -> owner-only list unless admin
GET /api/v1/jobs/{id} -> status, attempts, progress, metrics, owner-safe metadata
POST /api/v1/jobs/{id}/cancel -> 202; cancellation intent and eventual state reconciliation
POST /api/v1/jobs/{id}/retry -> create new attempt only from terminal FAILED/TIMED_OUT/CANCELLED; ownership enforced
GET /api/v1/jobs/{id}/logs -> paginated/limited from ClearML, safely handle pre-enqueue and temporary errors
GET /api/v1/admin/jobs -> list all
GET /healthz and /readyz; readiness checks DB and safe ClearML dependency status (report degraded clearly)

Return stable HTTP errors: 400 bad parameters, 401 auth, 403 forbidden, 404 not found, 409 conflict/invalid transition, 429 per-user limit, 503 dependency unavailable. OpenAPI examples and curl examples.

## Scheduling algorithm

On submit store job PENDING, run attempt=1 PENDING. Worker slots configured explicitly through admin config and reconciled from ClearML Agent inventory. One per GPU; each Agent listens ONLY to its own queue, e.g. `gpu-node01-0`. Use pool matching and slot availability; do not double-book GPU indices or let agents listen to overlapping queues. Workers with missing heartbeat, disabled, reserved, enqueued, starting or running are unavailable.

Scheduler leader on each tick: (1) reconcile slots/task states, (2) expire stale reservations safely, (3) compute per-user active counts including RESERVED, ENQUEUED, STARTING, RUNNING and cancel in-flight, (4) pick next user via persisted rotation cursor and oldest eligible job, (5) select compatible free slot, (6) transactionally reserve that slot/job/run and record enqueue-intent, (7) outside transaction create/recover ClearML task and enqueue, (8) persist IDs and status; reconcile partial failures before retries. Admission only if agent actually online. Guard slot uniqueness with DB constraints or transactional row locking. Set max active per user=2, max pending per user=20, configurable. New attempts must not race with old cleanup. Avoid promising exact queue position or FIFO completion order.

Potential ambiguity: ClearML itself pulls jobs, so selection is guaranteed only with per-slot queue. Homogeneous common queue is an optimization for future versions and should not be added to MVP.

## Runner contract

`runner/mock_eval.py`: parse `--job-id`, `--model-ref`, `--benchmark`, `--episodes`; deterministic pseudo-success outcomes by seed; print progress and per-episode records; call ClearML logging SDK to record success_rate, episode_count, runtime; emit final JSON. Controlled sleep env var for queue demonstration. Exit non-zero on injected failure. `runner/base.py` protocol, `runner/isaac_lab_adapter.py` stub with clear TODO and no fake claim of actual Isaac Sim support.

## Web UI

Login, Jobs table with statuses and owner, Submit Job form, Job detail with attempts/log/metrics/cancel/retry, Workers/Pools overview, Admin all-jobs view. Simple UI; prioritize correct permissions and status refresh polling.

## Repo layout

```
backend/app/{main.py,api/,auth/,db/,models/,schemas/,services/,scheduler/,adapters/,config.py}
backend/alembic/
backend/tests/{unit/,integration/}
runner/{mock_eval.py,base.py,isaac_lab_adapter.py,requirements.txt,Dockerfile}
frontend/{src/,package.json}
deploy/{compose.yml,agent-systemd/,example.env}
scripts/{seed_admin.py,register_slot.py,smoke_test.sh}
docs/{architecture.md,agent-setup.md,operations.md,api-examples.md}
README.md
```

## Deploy

1. Control node: PostgreSQL + API + Scheduler + React front end via Docker Compose; connect to separately deployed official ClearML Server. Provide environment template, DB migration, bootstrap admin and demo data opt-in. Use healthchecks, persistent database, logs.
2. GPU nodes: validate `nvidia-smi`, Docker Engine, NVIDIA Container Toolkit and `docker run --rm --gpus all <tested-image> nvidia-smi`. Install `clearml-agent`, configure ClearML credentials in root-readable config. Launch one daemon per GPU bound by `--gpus N --queue gpu-${HOSTNAME}-${N} --docker <approved-image>`, using systemd templates. Validate agent CLI on pinned version. Do not make web/API ports publicly accessible by default.
3. For local CI without real GPU: test fake adapters, emulate worker slots, scheduler transitions, and mock runner. Document how to switch to real mode.

## Acceptance tests

A. 2 distinct Linux machines and 2 GPU slots visible; one worker disconnect marks slot unavailable after TTL; reconnect restores eligibility.
B. Three users submit 5 jobs each, two slots: at most 2 concurrent globally and at most 2 per user, no duplicate execution, eligible users alternate fairly over admissions.
C. One GPU + 3 jobs: 1 runs, 2 remain pending; tasks continue as GPU frees; finished slots released once.
D. Repeated submission with same idempotency key creates only one Job.
E. API cannot read/cancel another user's job; admin can view all.
F. Scheduler restart during task creation/enqueue leaves no duplicated run; reconciler repairs status.
G. Worker crashes during run: grace period, timeout, documented retry, no leaked reservation.
H. Cancel pending task immediately; cancel running task via ClearML abort request and confirm eventual status.
I. Mock runner outputs reproducible metrics; job UI shows them; controlled failure shows failure and retry attempt 2.
J. All tests pass via `pytest`, `npm run build`, `docker compose config` and lint where provided.

## Phase plan

P0 foundational: scaffold, compose, auth, DB migrations, ClearML adapter, mock runner, smoke test. P1 orchestration: slots, per-GPU queues, leader scheduler, RR fairness, admission accounting, reconciliation. P2 product: frontend, cancellation, retry, log/metrics, test coverage, docs and scripts. Keep increments independently runnable. Don't claim tests executed unless actually executed; show exact commands/results.

## Sources to verify and follow

- https://clear.ml/docs/latest/docs/fundamentals/agents_and_queues/
- https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_ref/
- https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_deployment_bare_metal/
- https://clear.ml/docs/latest/docs/references/sdk/task/

## Instructions to autonomous coding agent

Inspect current repository before modifying; if repository is empty, scaffold. Implement code, tests and docs, not a design-only response. Pin dependencies; never hardcode secrets. If real ClearML Server/GPU not available, complete fake-mode e2e tests and clearly report which real integration checks remain. Make assumptions explicit in README. Avoid broad refactors unrelated to MVP. Commit checkpoints if in Git repo and authorized, otherwise show changed files and run logs. Work through P0/P1/P2 sequentially until complete or blocked, report remaining gaps factually.

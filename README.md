# Rex / ARC: main-experiment reproduction

Rex compiles structured requirements and reference images into runnable web applications through database, backend, and frontend design, lowering, and test-driven implementation. The installed compiler command is `arc`.

The reproduction workflow has two separate operations:

- **Generate:** compile one application or sequentially generate the six benchmark applications with the GPT or DeepSeek profile. Compilation includes generated development tests and repair.
- **Evaluate:** explicitly select one generated application, deploy it, and run its independent benchmark tests. Generation scripts never invoke independent evaluation.

## 1. Clone the reproduction repository

Use the repository URL provided on the supplementary website. This repository contains the compiler, scripts, two credential-free model profiles, and the six tasks' requirement documents, reference images, and independent tests. Only task materials are bundled; no external benchmark checkout or submodule initialization is needed.

```bash
# Replace with the reproduction repository URL provided on the website.
REX_REPOSITORY_URL="REPLACE_WITH_REPOSITORY_URL"
git clone "$REX_REPOSITORY_URL" rex-reproduction
cd rex-reproduction
export REX_ROOT="$PWD"
git rev-parse HEAD
```

Record this commit for comparison with the paper release. All inputs and tests are versioned with the source; `benchmark/manifest.json` records per-task file counts and SHA-256 hashes of the bundled material trees. Requirements and tests remain separate so generation receives only the requirements and visual references.

```text
rex-reproduction/
├── benchmark/
│   ├── manifest.json
│   ├── input/
│   │   ├── keep/
│   │   │   ├── requirements.yaml
│   │   │   └── reference/
│   │   └── ...five other tasks...
│   └── test/
│       ├── keep/                   # independent tests, helpers, fixtures
│       └── ...five other tasks...
├── .env.gpt                        # gpt-5.6-sol; credentials/URLs empty
├── .env.deepseek                   # deepseek-flash; credentials/URLs empty
├── src/                           # compiler source
├── workspace/                     # generated applications
│   ├── keep-gpt-run1/
│   └── keep-ds-run1/
├── reproduction/                  # generation records, outside applications
│   ├── generation/<task>-<profile>-<run-id>/
│   │   ├── console.log
│   │   └── run.json
│   └── batches/<profile>-<run-id>.json
├── generate.sh
├── batch_run.sh
└── test_app.sh
```

Here `<profile>` in output filenames is `gpt` or `ds`; the command-line model names are `gpt` and `deepseek`. Commands below run from the compiler repository root. Scripts can also be called by absolute path from another directory: defaults stay relative to the repository, while explicitly supplied relative paths resolve from your current directory.

## 2. Install the environment

Use Bash on Linux/macOS, or WSL on Windows, with Git, Python **3.11+**, and Node.js **22.12+** with npm. The generated stack uses React, Express, Drizzle, and SQLite; no separate database server is needed. API access, npm downloads, and Playwright browser downloads must be available.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

python --version
node --version
npm --version
arc --version
```

The repository already includes `.env.gpt` and `.env.deepseek`. Their main-model IDs are preset to `gpt-5.6-sol` and `deepseek-flash`, respectively. Edit the files in place; there is no need to copy `.env_example`.

Fill in `OPENAI_BASE_URL` and `OPENAI_API_KEY` for the main model, and `VISUAL_BASE_URL`, `VISUAL_API_KEY`, and `VISUAL_MODEL` for an image-capable model. The two provider URLs and API keys are intentionally empty in both distributed files. Change a preset main-model ID only if your provider requires a different identifier.

For example, the distributed GPT profile has this structure:

```dotenv
OPENAI_BASE_URL=
OPENAI_API_KEY=
MODEL=gpt-5.6-sol

VISUAL_BASE_URL=
VISUAL_API_KEY=
VISUAL_MODEL=

MODEL_ATTEMPT=5
ARC_TDD_MAX_ITERATIONS=5
ARC_TDD_LOAD_ROUNDS=12
```

The DeepSeek profile uses `MODEL=deepseek-flash` with the same empty connection fields and budgets. Configure a vision-capable model for reference-image analysis; the generation scripts require both endpoints and the visual model ID to be filled in before starting a run. You may use the same URL/key for both endpoints if your provider supports the selected models.

Existing shell settings take priority over profile files; clear stale model variables before switching profiles. The command-line names `gpt` and `deepseek` choose the profile file, while `MODEL` specifies its actual provider model. Keep your credentials local when sharing the repository; clear the URL/key fields before redistributing these two tracked profiles.

```bash
ARC_ENV_FILE="$REX_ROOT/.env.gpt" arc doctor
ARC_ENV_FILE="$REX_ROOT/.env.deepseek" arc doctor
```

`arc doctor` checks local settings and dependencies; API connectivity is exercised during generation. The shell scripts prefer the repository's `.venv/bin/python`. Set `REX_PYTHON=/absolute/path/to/python` to use another Python environment containing the project dependencies.

## 3. Generate a single application

```bash
bash generate.sh --task keep --model gpt --run-id run1
```

This reads `benchmark/input/keep/`, uses `.env.gpt`, writes the application to `workspace/keep-gpt-run1/`, and records the command, input hash, revisions, model IDs, duration, and compiler workflow report under `reproduction/generation/keep-gpt-run1/`.

For DeepSeek:

```bash
bash generate.sh --task keep --model deepseek --run-id run1
```

The output is `workspace/keep-ds-run1/`. Both commands perform generation and development testing only. They do not copy or execute the independent tests, or leave the application running.

The generation scripts set `ARC_ENV_FILE` to the absolute path of the selected bundled profile: `--model gpt` selects `.env.gpt`, and `--model deepseek` selects `.env.deepseek`. An old `ARC_ENV_FILE` exported in your shell does not change this selection. Use `--env-file /path/to/profile.env` for an explicit override; its absolute path is then assigned to `ARC_ENV_FILE`. Use `--output /path/to/application` to override a single application's output directory. The bundled `benchmark/` directory is the default. To select an alternative local dataset, specify its root containing `input/<task>/` and `test/<task>/`:

```bash
bash generate.sh --task keep --model gpt --run-id run2 --port 33100 \
  --benchmark-root /data/rex-benchmark
```

The same `--benchmark-root` option is available for batch generation and evaluation. It points to the root containing the separate `input/` and `test/` directories; the bundled default requires no additional download.

Generation refuses existing output or generation-record directories. Use a fresh `--run-id` for another attempt; outputs are never silently overwritten or resumed. A successful command/build does not mean every requirement passed: inspect `.arc/workflow/report.json`, its `failed_nodes`, and `.arc/implementation/report.json` in the generated application. The script retains that workflow report in its generation record.

For a small installation check, the original compiler remains available:

```bash
arc compile example/smoke -o workspace/demo-smoke --port 3301
```

## 4. Batch-generate applications

```bash
bash batch_run.sh --model gpt --run-id run1
bash batch_run.sh --model deepseek --run-id run1
```

Each invocation sequentially generates `keep`, `bookstack`, `prestashop`, `stackoverflow`, `12306`, and `ctrip`. Single and batch generation use the same fixed model/task port assignments below. Selecting or reordering a subset does not change its tasks’ ports. The applications and generation records use the same naming scheme as single generation. A batch checks all destination paths before starting, continues after individual compiler command failures, records their exit codes, and returns a nonzero status if any command fails. Interruptions retain completed records.

The default application startup ports are:

| Task | GPT | DeepSeek |
| --- | ---: | ---: |
| `keep` | 33100 | 33200 |
| `bookstack` | 33110 | 33210 |
| `prestashop` | 33120 | 33220 |
| `stackoverflow` | 33130 | 33230 |
| `12306` | 33140 | 33240 |
| `ctrip` | 33150 | 33250 |

Each application reserves its following port for an optional alternate-clock world. Stack Overflow therefore uses 33131 or 33231 when that world is required; it cannot overlap the next task’s default port. Generation checks that assigned ports are free before starting. Existing listeners cause an error rather than being reused or terminated.

Applications, generation records, evaluation databases, and reports have distinct model/task paths. GPT and DeepSeek outputs can coexist. Repeated runs of the same model/task share the default port: run them sequentially, or explicitly select different ports for concurrent runs.

**Batch generation does not deploy applications for independent evaluation and does not execute the benchmark suite.** After it finishes, select each desired application and invoke `test_app.sh` separately.

Optional subset, custom base port, and destination roots:

```bash
bash batch_run.sh --model deepseek --run-id run2 \
  --tasks keep,bookstack --base-port 33300 \
  --output-root /data/rex-apps \
  --records-root /data/rex-records
```

`--base-port` uses `base + 10 × canonical task index` (Keep is index 0), even for a subset. The example assigns 33300 to Keep and 33310 to BookStack. `--output-root` and `--records-root` are also supported by single generation. Batch summary records are written to `<records-root>/batches/ds-run2.json` in this example. If a single `keep-gpt-run1` was generated already, use another run ID for a full GPT batch to avoid a collision.

## 5. Deploy and independently test one application

After generation, explicitly test one application:

```bash
bash test_app.sh --task keep \
  --app workspace/keep-gpt-run1
```

For a DeepSeek application, run a separate command:

```bash
bash test_app.sh --task keep \
  --app workspace/keep-ds-run1
```

The script performs the following steps without model calls:

1. Create a fresh evaluation directory under the selected application and verify the port is free.
2. Install application dependencies if `node_modules/` is absent, build the application, and install the deterministic benchmark clock/timezone adapter from its generation input. The ordinary application startup initializes its own data; the evaluator does not add missing business fixtures or repair application behavior.
3. Start the built backend, which also serves the built frontend, using a new database inside the evaluation directory. Wait for its health endpoint.
4. Copy the selected independent `tests/` tree, including helpers and fixtures, into that directory. Create an isolated Playwright package/configuration with the deployed URL and requirement timezone, then install the runner and Chromium.
5. List and execute the complete suite with one worker and zero retries. Retain JSON/HTML reports, logs, and configured failure traces, screenshots, and videos.
6. Stop the services started by this invocation, including on test failure or interruption. Leave the evaluation directory and database for inspection.

No separate terminal or manual `npm start` is needed. The generated application retains its task, model profile, and selected port in `.rex/generation.json`; evaluation reads that record automatically. For older applications without it, the script uses `--model` or a conventional `<task>-gpt-...` / `<task>-ds-...` directory name to choose the fixed port. It rejects a task/profile that contradicts the saved record.

The selected port is used for **both** the server’s `PORT` and Playwright’s `PLAYWRIGHT_BASE_URL` / `SO_BASE_URL`, as well as the persisted configuration. No stale test URL from the shell is reused. Use `--port 34100` to override the evaluation port explicitly. The script checks the port before deployment and never terminates a service already occupying it.

On completion, test failure, setup failure after deployment, Ctrl+C, or SIGTERM, the runner cleans its own application process groups and waits for shutdown; a process that exceeds the shutdown grace period is killed. `summary.json` records `service_ports` and `services_stopped`. This cleanup also handles descendants when their original parent has already exited.

For a named evaluation or an alternative local test directory:

```bash
bash test_app.sh --task keep \
  --app /data/rex-apps/keep-gpt-run1 --port 34100 \
  --tests-dir "$REX_ROOT/benchmark/test/keep" --eval-id review-1
```

`--tests-dir` overrides the bundled `benchmark/test/<task>/` and points directly to an independent test directory. The clock/timezone comes from the application's `.arc/preprocessing/requirement_ir.json`; if absent, the script reads `benchmark/input/<task>/requirements.yaml` (or `.yml`) from the selected benchmark root. Keep this generation-input metadata and `.rex/generation.json` when distributing generated applications. Tests are copied into a fresh directory on every invocation; `--eval-id` cannot overwrite a previous evaluation.

On Linux, append `--with-deps` if Playwright system libraries need installation; this may require system installation permissions. `--startup-timeout 120` extends the default 60-second startup wait.

Each evaluation starts with a new database, shared by that suite's sequential cases; the railway fixture supports a single local application URL. If a supplied Stack Overflow suite includes `Refuse an expired comment edit`, the runner also starts its declared `expired_comment_now` world on `--port + 1` with another fresh database. Other cases use the normal benchmark clock. Do not mix dataset/test revisions or substitute different initial-world protocols when comparing with a released result.

The command returns nonzero for failing tests, skipped cases, empty suites, deployment errors, or setup errors. A failing generated application remains a measured failure; the runner does not repair it. In `summary.json`, `pass_rate` is passed tests divided by all reported tests, including skipped cases in the denominator; inspect the full statistics and errors alongside it.

## 6. Inspect reports and retained artifacts

```text
workspace/keep-gpt-run1/
├── backend/ and frontend/          # generated source and build output
├── tests/                         # generated development tests; preserved
├── .arc/
│   ├── preprocessing/             # normalized generation input
│   ├── design/ and lowering/      # intermediate representations and source maps
│   ├── workflow/report.json       # pipeline stages and failed requirements
│   ├── implementation/report.json # per-requirement outcomes
│   ├── implementation/test_status.json
│   ├── model_logs/                # model requests, replies, and provider usage
│   └── debug.log                  # compiler progress and diagnostics
├── .rex/generation.json            # task, profile, selected startup port
└── .rex/evaluation/<eval-id>/
    ├── tests/                     # copied independent tests and fixtures
    ├── package.json / package-lock.json
    ├── playwright.config.ts       # deployed URL and requirement timezone
    ├── application.db             # fresh evaluation database
    ├── application.log            # deployed server output
    ├── console.log                # runner setup and test output
    ├── summary.json               # provenance, URL, status, statistics, pass rate
    ├── report.json                # complete Playwright JSON result
    ├── playwright-report/         # HTML report
    └── test-results/              # configured failure artifacts
```

Keep the entire evaluation directory: HTML reports may reference companion assets. To open a retained report manually:

```bash
cd workspace/keep-gpt-run1/.rex/evaluation/REPLACE_WITH_EVAL_ID
npm exec -- playwright show-report playwright-report
```

Generation records and evaluation records have separate durations. The independent evaluation's setup/build/test time is excluded from generation cost. For the main experiment, retain all six applications' pass/fail denominators, revisions, input/test hashes, actual model IDs, budgets, workflow outcomes, and provider usage. Match the paper's aggregation when comparing results: arithmetic mean of application pass rates, geometric mean of positive generation times/token totals.

All script options are available through `bash generate.sh --help`, `bash batch_run.sh --help`, and `bash test_app.sh --help`. For the requirement DSL, see the [requirement metamodel](meta-model/requirement/README.md) and [smoke example](example/smoke/requirements.yaml).

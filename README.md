# ARC Quick Start

ARC turns structured requirements and reference images into source code through requirement parsing, database/backend/frontend design, lowering, test-driven implementation, and a final build.

## 1. Set up the environment

Install:

- Python 3.11 or later.
- Node.js 22.x, version 22.12 or later, with npm.
- Access to an OpenAI-compatible Chat Completions API with JSON output support. Reference image analysis also requires an image-capable model, which can be configured separately.

The environment needs access to the model API, npm packages, and Playwright browser downloads. The included demos use React, Tailwind CSS, Express, Drizzle, and SQLite; no separate database service is needed.

Run the following commands from the **repository root**.

### Install ARC

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Check the installation:

```bash
python --version
node --version
npm --version
arc --help
```

During compilation, ARC installs the generated project's npm dependencies and Playwright Chromium automatically. If Chromium reports missing system libraries on Linux, run `npx playwright install --with-deps chromium` from the generated project directory. Installing system libraries requires the corresponding system permissions.

## 2. Configure the models

Create `.env` in the repository root and replace the placeholders with your provider's actual values:

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-provider.example/v1
MODEL=your-coding-model

# Optional model input/output logs. Normal progress logs are always enabled.
ARC_DEBUG=0
```

To use a separate model for image analysis, add:

```dotenv
VISUAL_API_KEY=your-visual-api-key
VISUAL_BASE_URL=https://your-visual-provider.example/v1
VISUAL_MODEL=your-vision-model
```

Each unset `VISUAL_*` value inherits its corresponding main-model setting. Leaving these values empty does not disable image analysis.

Alternatively, run `arc config` from the repository root to configure the main model interactively, then add any visual-model settings manually.

```bash
arc doctor
```

`doctor` checks local configuration and dependencies; it does not verify remote API connectivity. Resolve missing settings and placeholder-key messages before running a demo.

Shell environment variables take precedence over `.env`. Set `ARC_ENV_FILE` to load a different configuration file.


## 3. Run demo

Input: [example/smoke/requirements.yaml](example/smoke/requirements.yaml).

This demo covers the public entry page, traveler registration, sign-in, sign-out, and predefined account data. Start here to check the complete generation flow.

From the repository root:

```bash
arc compile example/smoke -o workspace/demo-smoke --port 3301
```

ARC creates the project, installs dependencies, runs design and implementation, executes its generated tests, and builds the result. Runtime depends on the model service and the number of adjustment attempts.

To run the generated result:

```bash
cd workspace/demo-smoke
npm start
```

Open **http://localhost:3301**. Press `Ctrl+C` to stop.

`--port` sets the generated project's default service port and is also used during browser testing. Compilation does not leave the application running. After editing generated source, run `npm run build` before starting it again.

The default startup entry rebuilds the selected SQLite database and loads `backend/src/db/seed.ts`. **Restarting resets the demo data.**

## 4. Inspect the result

```text
workspace/demo-smoke/
├── backend/                     # Backend source and build output
├── frontend/                    # Frontend source and build output
├── tests/                       # Generated requirement tests and runner configuration
├── package.json                 # Project build and start commands
└── .arc/
    ├── debug.log                # Normal console progress and errors
    ├── model_logs/              # Detailed model calls; only with ARC_DEBUG=1
    ├── preprocessing/           # Normalized requirements and scheduling information
    ├── visual_cache.json        # Image analysis indexed by reference path
    ├── design/                  # Database, backend, and frontend IR
    ├── lowering/                # File plans, manifests, and stage reports
    ├── implementation/          # Requirement and test status
    └── workflow/report.json     # Overall status and failed requirements
```

Use these files to assess completion:

- `.arc/workflow/report.json`: overall status, stage errors, and `failed_nodes`.
- `.arc/implementation/report.json`: individual requirement states and errors.
- `.arc/implementation/test_status.json`: recorded test and compilation status.
- `.arc/debug.log`: progress and failure details; enable `ARC_DEBUG` for the corresponding model inputs and outputs.

ARC may retain compilable source and continue building after individual requirements fail. A completed command or successful build alone does not establish that every requirement passed.

The output project's `tests/` contains agent-generated tests. The examples' `example/<demo>/tests/` directories contain independent validation tests; compilation does not automatically run them. Internal requirement states are not external validation results.

Use a new output directory for each experiment or changed requirement document, such as `workspace/demo-smoke-2`.

## 5. Prepare your own requirements

The input can be a directory containing `requirements.yaml`, or a YAML file directly. Use UTF-8. Image paths are relative to that file and use `/` separators.

```yaml
id: REQ-HOME
name: View the home page
description:
  text: The home page displays a welcome message.
  image:
    - id: IMG-HOME
      reference: reference/home.png
      page: home
scenarios:
  - id: SCN-HOME
    name: Open the home page
    steps:
      - id: STEP-HOME
        Given: The visitor is signed out.
        When: The visitor opens the home page.
        Then: The welcome message is visible.
        references:
          - '\ref[image](IMG-HOME)'
```

Descriptions contain text and images. Scenarios contain ordered `Given`/`When`/`Then` steps. Images are reused by ID. An image's optional `page` identifies the page containing its full view, alternate state, or component. Equal `page` values share one page UI tree; ownership is inferred when the field is absent.

See the [requirement metamodel](meta-model/requirement/README.md) for field definitions, image reuse, and typed references.

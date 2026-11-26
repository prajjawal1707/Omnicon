# OmniCon AI v1.1 — Developer Reliability Update

Windows-first local AI project workspace using Ollama and optional supported hosted API providers.

## What this update fixes

The previous Developer agent required one large, strictly valid JSON object. Small local models can produce invalid JSON when embedding multiline source code, resulting in errors such as `Expecting ',' delimiter`.

v1.1 changes the file-generation contract to explicit file delimiters:

`===OMNICON_FILE: relative/path.ext===`

The backend accepts either the previous JSON manifest or delimiter-based output, validates relative paths, writes safe supported files into the project's workspace, and retries malformed Developer output up to three total generation attempts. It records the list of files written.

## Included

- FastAPI local dashboard
- SQLite project/task history and checkpoints
- Architect, Developer, Debugger and Reviewer agents
- Parallel role execution and provider-per-role configuration
- Ollama OpenAI-compatible API support (default model: `llama3.2:latest`)
- Developer writes real project files into `workspaces/<project>/`
- Result viewer and Markdown report export
- VS Code workspace path display/copy
- Explicit Python syntax check and `unittest` discovery action
- Paused task resume
- Workspace path migration when moving the application folder

## Upgrade without losing your current project

1. Stop the current OmniCon server with `Ctrl+C` in its VS Code terminal.
2. Make a backup copy of your current OmniCon folder.
3. Extract this ZIP into a new folder, e.g. `C:\OmniCon_v1_1`.
4. From your currently running OmniCon folder, copy these into the new folder:
   - `.env`
   - `data` folder
   - `workspaces` folder
5. Open `C:\OmniCon_v1_1` in VS Code.
6. Open **Terminal → New Terminal** and run `.un.bat`.
7. Open http://127.0.0.1:8765.
8. Select your existing paused project and click **Resume paused work**.

The application rebases old workspace paths to the new folder and attempts to copy existing workspace contents when the old path is still available. Keep the old folder until you confirm your project appears and files are intact.

## Fresh installation

1. Extract ZIP.
2. Open extracted folder in VS Code.
3. Run `.un.bat` in VS Code terminal.
4. Open http://127.0.0.1:8765.

The launcher creates a Python virtual environment, installs requirements and creates `.env` from `.env.example` if missing.

## Default Ollama configuration

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_API_KEY=ollama
OLLAMA_MODEL=llama3.2:latest
AGENT_ARCHITECT_PROVIDER=ollama
AGENT_DEVELOPER_PROVIDER=ollama
AGENT_DEBUGGER_PROVIDER=ollama
AGENT_REVIEWER_PROVIDER=ollama
MAX_PARALLEL_WORKERS=2
```

The user already confirmed this local Ollama endpoint and model work. Keep the `.env` file from the existing installation.

## How to resume the failed expense-tracker task

1. Open the existing `new project`.
2. Click **Resume paused work**.
3. Only incomplete/paused tasks are run again. The already completed Architect, Debugger and Reviewer tasks are retained.
4. The Developer will now return files using the delimiter format and the backend will retry malformed output up to three total attempts.
5. Check the VS Code workspace section for generated files.
6. Open that folder in VS Code.

## Important limitations

- This update fixes file-output parsing and retry. It does not make the Debugger autonomously edit or repair source files after a failed test.
- The Debugger and Reviewer currently generate analysis reports; the explicit test button performs Python compile checks and `unittest` discovery.
- Llama 3.2 3B is a small model. It may still produce incomplete or incorrect code on complex requests; always review generated code.
- Running project tests executes project code. Only run tests for projects you trust.
- Server binds to localhost (`127.0.0.1`) and should not be exposed to the public internet.
- Hosted providers require permitted API credentials. This software does not automate consumer chat accounts or bypass quotas.

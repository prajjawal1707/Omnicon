import os, json, uuid, asyncio, sqlite3, subprocess, sys, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
DB = DATA / "omnicon.sqlite3"
MAX_WORKERS = max(1, min(12, int(os.getenv("MAX_PARALLEL_WORKERS", "2"))))

ROLES = [
    ("architect", "Architect", "Plans the project and breaks work into deliverables"),
    ("developer", "Developer", "Implements assigned project work"),
    ("debugger", "Debugger", "Tests assumptions, finds defects and diagnoses failures"),
    ("reviewer", "Reviewer", "Reviews quality, requirements and integration readiness"),
]

def now():
    return datetime.now(timezone.utc).isoformat()

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
          status TEXT NOT NULL, result TEXT DEFAULT '', created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, workspace_path TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent TEXT NOT NULL,
          title TEXT NOT NULL, instructions TEXT NOT NULL, status TEXT NOT NULL,
          output TEXT DEFAULT '', error TEXT DEFAULT '', attempt INTEGER DEFAULT 0,
          checkpoint TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
          task_id TEXT, level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
init_db()
# Safe additive migration for databases created by v0.3.
with db() as c:
    cols = [r["name"] for r in c.execute("PRAGMA table_info(projects)").fetchall()]
    if "workspace_path" not in cols:
        c.execute("ALTER TABLE projects ADD COLUMN workspace_path TEXT DEFAULT ''")

WORKSPACES = (BASE / "workspaces").resolve()
WORKSPACES.mkdir(exist_ok=True)

# Rebase workspaces when the application folder is moved/upgraded. This keeps
# existing project folders attached to their SQLite records after copying data/.
with db() as c:
    project_rows = c.execute("SELECT id,name,workspace_path FROM projects").fetchall()
    for old in project_rows:
        import re
        old_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", old["name"]).strip("-_").lower()[:48] or "project"
        target = (WORKSPACES / (old_slug + "-" + old["id"][:8])).resolve()
        old_value = old["workspace_path"] or ""
        old_path = Path(old_value).expanduser() if old_value else None
        try:
            old_resolved = old_path.resolve() if old_path else None
        except OSError:
            old_resolved = None
        if old_resolved and old_resolved != target and old_resolved.exists():
            try:
                if old_resolved.is_dir() and not target.exists():
                    shutil.copytree(old_resolved, target)
            except OSError:
                pass
        target.mkdir(parents=True, exist_ok=True)
        if old_resolved != target:
            c.execute("UPDATE projects SET workspace_path=? WHERE id=?", (str(target), old["id"]))

app = FastAPI(title="OmniCon AI", version="1.1.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
running = {}
locks = {}

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=5, max_length=20000)

def query_all(sql, args=()):
    with db() as c:
        return [dict(x) for x in c.execute(sql, args).fetchall()]

def query_one(sql, args=()):
    with db() as c:
        x = c.execute(sql, args).fetchone()
        return dict(x) if x else None

def log_event(pid, message, level="info", task_id=None):
    with db() as c:
        c.execute("INSERT INTO events(project_id,task_id,level,message,created_at) VALUES(?,?,?,?,?)",
                  (pid, task_id, level, message, now()))

def env_provider_names():
    names = set()
    for key in os.environ:
        if key.startswith("PROVIDER_") and key.endswith("_BASE_URL"):
            names.add(key[len("PROVIDER_"):-len("_BASE_URL")].lower())
    return names

def provider_config(name):
    name = name.lower()
    if name == "demo":
        return {"name": "demo", "type": "demo", "configured": True, "model": "simulated"}
    if name == "ollama":
        return {
            "name": "ollama", "type": "openai_compatible",
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            "api_key": os.getenv("OLLAMA_API_KEY", "ollama"),
            "model": os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            "configured": True
        }
    if name == "anthropic":
        return {"name": name, "type": "anthropic",
                "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                "configured": bool(os.getenv("ANTHROPIC_API_KEY"))}
    prefix = "PROVIDER_" + name.upper() + "_"
    base = os.getenv(prefix + "BASE_URL", "").rstrip("/")
    key = os.getenv(prefix + "API_KEY", "")
    model = os.getenv(prefix + "MODEL", "")
    return {"name": name, "type": "openai_compatible", "base_url": base,
            "api_key": key, "model": model,
            "configured": bool(base and model and key)}

def role_provider(role):
    return os.getenv("AGENT_" + role.upper() + "_PROVIDER", "demo").strip().lower()

async def call_openai_compatible(cfg, system, user):
    if not cfg.get("base_url") or not cfg.get("model"):
        raise RuntimeError(f"Provider '{cfg['name']}' needs a base URL and model in .env.")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    payload = {"model": cfg["model"], "temperature": 0.2,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(cfg["base_url"] + "/chat/completions", headers=headers, json=payload)
        if r.status_code == 429:
            raise RuntimeError(f"{cfg['name']} rate limit reached. Checkpoint retained.")
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

async def call_anthropic(cfg, system, user):
    if not cfg.get("api_key"):
        raise RuntimeError("ANTHROPIC_API_KEY is missing in .env.")
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    payload = {"model": cfg["model"], "max_tokens": 1800, "system": system,
               "messages": [{"role": "user", "content": user}]}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        if r.status_code == 429:
            raise RuntimeError("Anthropic API rate limit reached. Checkpoint retained.")
        r.raise_for_status()
        data = r.json()
        return "\n".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")


def safe_slug(value):
    import re
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_").lower()
    return (slug[:48] or "project")

def project_workspace(pid):
    p = query_one("SELECT workspace_path FROM projects WHERE id=?", (pid,))
    if not p or not p.get("workspace_path"):
        raise RuntimeError("Project workspace is not configured.")
    root = WORKSPACES.resolve()
    path = Path(p["workspace_path"]).resolve()
    if path != root and root not in path.parents:
        raise RuntimeError("Workspace path is outside the OmniCon workspaces folder.")
    path.mkdir(parents=True, exist_ok=True)
    return path

def _safe_relative_file_path(raw_path):
    rel = str(raw_path or "").replace("\\", "/").strip().strip("`")
    if not rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
        return None
    allowed = {".py", ".md", ".txt", ".json", ".html", ".css", ".js",
               ".toml", ".yaml", ".yml", ".csv", ".sql"}
    if candidate.suffix.lower() not in allowed:
        return None
    return candidate


def extract_file_manifest(text):
    """Accept either valid JSON or explicit delimiter-based file blocks.

    Delimiter mode avoids JSON escaping failures when small local models emit
    multiline source code. It is intentionally tolerant of markdown fences.
    """
    import re
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Developer returned an empty response.")

    # First accept a well-formed JSON object, including a JSON object inside fences.
    candidate = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    candidate = re.sub(r"\s*```$", "", candidate)
    json_candidates = [candidate]
    left, right = candidate.find("{"), candidate.rfind("}")
    if left >= 0 and right > left:
        json_candidates.append(candidate[left:right + 1])

    for raw in json_candidates:
        try:
            data = json.loads(raw)
            files = data.get("files") if isinstance(data, dict) else None
            if isinstance(files, list):
                parsed = []
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    path = _safe_relative_file_path(item.get("path"))
                    content = item.get("content")
                    if path is not None and isinstance(content, str) and content.strip():
                        parsed.append((path, content))
                if parsed:
                    return parsed
        except Exception:
            pass

    # Preferred local-model format:
    # ===OMNICON_FILE: relative/path.ext===
    # ...entire file contents...
    marker = re.compile(r"(?m)^\s*===OMNICON_FILE:\s*(.+?)\s*===\s*$")
    matches = list(marker.finditer(cleaned))
    parsed = []
    for index, match in enumerate(matches):
        path = _safe_relative_file_path(match.group(1))
        if path is None:
            continue
        content_start = match.end()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        content = cleaned[content_start:content_end].strip("\r\n")
        # Remove only a wrapper fence around the whole file; preserve fences in README.
        content = re.sub(r"^```(?:\w+)?\s*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        if content.strip():
            parsed.append((path, content + "\n"))
    if parsed:
        return parsed

    raise ValueError(
        "Developer response could not be parsed. Expected a JSON files array or "
        "===OMNICON_FILE: filename=== sections. The response was not saved."
    )


def write_generated_files(pid, model_output):
    files = extract_file_manifest(model_output)
    workspace = project_workspace(pid)
    written = []
    for rel, content in files[:40]:
        target = (workspace / rel).resolve()
        if workspace.resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel.as_posix())
    if not written:
        raise ValueError("No safe, supported files were found in the Developer response.")
    return written, str(workspace)

async def execute_agent(role, instruction, project_prompt, pid=None):
    provider = role_provider(role)
    cfg = provider_config(provider)
    system = (
        f"You are the {role} agent in OmniCon AI, a coordinated multi-agent team. "
        "Complete only your assigned responsibility. Be specific and truthful. "
        "Do not claim to have edited files or run commands unless tools actually did so. "
        "Return findings, proposed artifacts, risks and next steps."
    )
    user = f"PROJECT REQUEST:\n{project_prompt}\n\nYOUR ASSIGNED TASK:\n{instruction}"
    if role == "developer":
        user += (
            "\n\nIMPORTANT OUTPUT CONTRACT — DO NOT RETURN JSON. "
            "Return each complete file using this exact delimiter on its own line: "
            "===OMNICON_FILE: main.py=== followed by the full file contents. "
            "Repeat for every file, for example ===OMNICON_FILE: expense_manager.py===, "
            "===OMNICON_FILE: test_expense_manager.py===, and ===OMNICON_FILE: README.md===. "
            "Do not put the delimiter inside a code fence. Do not add explanations before, "
            "between, or after files. Use relative file paths only. Never use absolute paths, "
            "parent-directory traversal, secrets, .env files, or shell commands. "
            "Each file must be complete, runnable, and internally consistent. "
            "For Python projects, write non-interactive unit tests that can run with unittest."
        )
    if provider == "demo":
        await asyncio.sleep(1)
        if role == "developer":
            return json.dumps({"files": [{"path": "README.md", "content": "# Demo project\\n\\nThis is a placeholder file. Configure Ollama for AI-generated code.\\n"}]})
        return (f"[SIMULATED {role.upper()} OUTPUT]\\nProvider: demo\\nTask: {instruction}\\n\\n"
                "This is placeholder output for testing the orchestrator, not real AI-generated work. "
                "Configure Ollama or an API provider in .env for real model execution.")
    if not cfg["configured"]:
        raise RuntimeError(f"Provider '{provider}' is not configured. Check .env.")
    if cfg["type"] == "anthropic":
        return await call_anthropic(cfg, system, user)
    return await call_openai_compatible(cfg, system, user)

def make_tasks(pid):
    items = [
        ("architect", "Project plan", "Analyze the user request. Produce a structured plan, milestones, dependencies, assumptions and acceptance criteria."),
        ("developer", "Create project files", "Implement the requested project as complete, runnable source files. Return the required JSON file manifest."),
        ("debugger", "Testing and risk assessment", "Produce test cases, edge cases, failure modes, integration checks and likely defects."),
        ("reviewer", "Requirements and quality review", "Independently assess the request. Produce a requirements checklist, quality gates, risks and missing information.")
    ]
    ts = now()
    with db() as c:
        for role, title, instruction in items:
            c.execute("""INSERT INTO tasks(id,project_id,agent,title,instructions,status,created_at,updated_at)
                         VALUES(?,?,?,?,?,'PENDING',?,?)""",
                      (str(uuid.uuid4()), pid, role, title, instruction, ts, ts))

async def run_project(pid):
    lock = locks.setdefault(pid, asyncio.Lock())
    async with lock:
        project = query_one("SELECT * FROM projects WHERE id=?", (pid,))
        if not project: return
        with db() as c:
            c.execute("UPDATE projects SET status='RUNNING',updated_at=? WHERE id=?", (now(), pid))
        log_event(pid, "Orchestrator started.")
        tasks = query_all("SELECT * FROM tasks WHERE project_id=? AND status IN ('PENDING','PAUSED','FAILED') ORDER BY created_at", (pid,))
        sem = asyncio.Semaphore(MAX_WORKERS)

        async def run_one(t):
            async with sem:
                with db() as c:
                    c.execute("UPDATE tasks SET status='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), t["id"]))
                prov = role_provider(t["agent"])
                log_event(pid, f"{t['agent']} started {t['title']} using {prov}.", task_id=t["id"])
                try:
                    output = await execute_agent(t["agent"], t["instructions"], project["prompt"], pid)
                    if t["agent"] == "developer":
                        last_parse_error = None
                        for generation_attempt in range(3):
                            try:
                                written, folder = write_generated_files(pid, output)
                                break
                            except Exception as parse_error:
                                last_parse_error = parse_error
                                if generation_attempt == 2:
                                    raise RuntimeError(
                                        f"Could not create files after 3 generation attempts: {parse_error}"
                                    )
                                retry_instruction = (
                                    t["instructions"] +
                                    "\nYour previous output could not be converted into files. "
                                    "Retry from scratch. DO NOT output JSON. Use only exact file delimiters "
                                    "like ===OMNICON_FILE: main.py=== on their own lines, followed by "
                                    "complete source text. Include every requested file."
                                )
                                log_event(pid, f"Developer output format retry {generation_attempt + 1}/2.", "warning", t["id"])
                                output = await execute_agent("developer", retry_instruction, project["prompt"], pid)
                        output = (
                            "Files created in your project workspace:\n" +
                            "\n".join("- " + x for x in written) +
                            f"\n\nWorkspace: {folder}\n"
                        )
                        log_event(pid, f"Developer saved {len(written)} file(s) to {folder}.", task_id=t["id"])
                    checkpoint = json.dumps({"output": output, "saved_at": now(), "task_id": t["id"]})
                    with db() as c:
                        c.execute("UPDATE tasks SET status='COMPLETED',output=?,checkpoint=?,error='',updated_at=? WHERE id=?",
                                  (output, checkpoint, now(), t["id"]))
                    log_event(pid, f"{t['agent']} completed {t['title']}.", task_id=t["id"])
                except Exception as e:
                    msg = str(e)[:1800]
                    checkpoint = json.dumps({"saved_at": now(), "last_error": msg})
                    with db() as c:
                        c.execute("UPDATE tasks SET status='PAUSED',error=?,checkpoint=?,updated_at=? WHERE id=?",
                                  (msg, checkpoint, now(), t["id"]))
                    log_event(pid, f"{t['agent']} paused: {msg}", "warning", t["id"])

        await asyncio.gather(*(run_one(t) for t in tasks))
        final_tasks = query_all("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", (pid,))
        incomplete = [t for t in final_tasks if t["status"] != "COMPLETED"]
        result = "\n\n".join(f"## {t['title']} — {t['agent']}\n{t['output'] or t['error']}" for t in final_tasks)
        status = "PAUSED" if incomplete else "COMPLETED"
        with db() as c:
            c.execute("UPDATE projects SET status=?,result=?,updated_at=? WHERE id=?", (status, result, now(), pid))
        log_event(pid, "Project paused with recoverable tasks." if incomplete else "All configured agent tasks completed.")
        running.pop(pid, None)

@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/api/health")
def health():
    return {"ok": True, "app": "OmniCon AI", "version": "1.1.0",
            "max_parallel_workers": MAX_WORKERS}

@app.get("/api/providers")
def providers():
    names = ["demo", "ollama", "anthropic"] + sorted(env_provider_names())
    out = []
    for n in names:
        cfg = provider_config(n)
        out.append({"id": n, "type": cfg["type"], "model": cfg.get("model", ""),
                    "configured": cfg.get("configured", False),
                    "base_url": cfg.get("base_url", "")})
    return out

@app.get("/api/agents")
def agents():
    out = []
    for role, name, description in ROLES:
        active = query_one("SELECT title,status FROM tasks WHERE agent=? AND status='RUNNING' ORDER BY updated_at DESC LIMIT 1", (role,))
        out.append({"id": role, "name": name, "description": description,
                    "provider": role_provider(role), "status": "RUNNING" if active else "AVAILABLE",
                    "task": active["title"] if active else None})
    return out

@app.get("/api/projects")
def list_projects():
    return query_all("SELECT id,name,prompt,status,created_at,updated_at FROM projects ORDER BY created_at DESC")

@app.post("/api/projects")
def create_project(body: ProjectIn):
    pid, ts = str(uuid.uuid4()), now()
    folder = WORKSPACES / (safe_slug(body.name) + "-" + pid[:8])
    folder.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute("INSERT INTO projects(id,name,prompt,status,created_at,updated_at,workspace_path) VALUES(?,?,?,'DRAFT',?,?,?)",
                  (pid, body.name, body.prompt, ts, ts, str(folder.resolve())))
    make_tasks(pid)
    log_event(pid, "Project created.")
    return {"id": pid, "status": "DRAFT"}

@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    p["tasks"] = query_all("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", (pid,))
    p["events"] = query_all("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 100", (pid,))
    return p

@app.post("/api/projects/{pid}/start")
async def start_project(pid: str):
    p = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    if pid in running: return {"status": "already_running"}
    running[pid] = asyncio.create_task(run_project(pid))
    return {"status": "started"}

@app.post("/api/projects/{pid}/resume")
async def resume_project(pid: str):
    p = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    if pid in running: return {"status": "already_running"}
    with db() as c:
        c.execute("UPDATE projects SET status='DRAFT',updated_at=? WHERE id=?", (now(), pid))
    running[pid] = asyncio.create_task(run_project(pid))
    return {"status": "resuming"}

@app.get("/api/projects/{pid}/result")
def project_result(pid: str):
    p = query_one("SELECT id,name,status,result FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    return p

@app.get("/api/projects/{pid}/export")
def export_project(pid: str):
    from fastapi.responses import Response
    p = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    tasks = query_all("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", (pid,))
    lines = [f"# {p['name']}", "", f"**Status:** {p['status']}", "", "## Project request", "", p['prompt'], ""]
    for t in tasks:
        lines += [f"## {t['title']} — {t['agent']}", "", f"**Status:** {t['status']}", "", t['output'] or t['error'] or "No output yet.", ""]
    content = "\n".join(lines)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in p['name']).strip("_") or "omnicon-project"
    return Response(content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-report.md"'})


@app.get("/api/projects/{pid}/workspace")
def get_workspace(pid: str):
    p = query_one("SELECT id,name,workspace_path FROM projects WHERE id=?", (pid,))
    if not p: raise HTTPException(404, "Project not found")
    folder = project_workspace(pid)
    files = []
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            try:
                files.append({"path": f.relative_to(folder).as_posix(), "size": f.stat().st_size})
            except OSError:
                pass
    return {"path": str(folder), "files": files}

@app.get("/api/projects/{pid}/events")
def project_events(pid: str):
    return query_all("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 100", (pid,))



@app.post("/api/projects/{pid}/run-tests")
def run_project_tests(pid: str):
    """Run the selected project's Python tests only after an explicit dashboard action."""
    p = query_one("SELECT id,name,workspace_path FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "Project not found")
    folder = project_workspace(pid)
    py_files = list(folder.rglob("*.py"))
    if not py_files:
        return {"ok": False, "returncode": 1, "output": "No Python files found in this project workspace."}

    # Compile first to catch syntax errors without executing the application.
    compile_result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(folder)],
        cwd=str(folder), capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    )
    if compile_result.returncode != 0:
        output = (compile_result.stdout + "\\n" + compile_result.stderr).strip()
        return {"ok": False, "returncode": compile_result.returncode,
                "output": "Python syntax check failed:\\n" + output,
                "workspace": str(folder)}

    test_files = list(folder.rglob("test_*.py")) + list(folder.rglob("*_test.py"))
    if not test_files:
        return {"ok": True, "returncode": 0,
                "output": "Python syntax check passed. No test_*.py or *_test.py files were found, so no test suite was executed.",
                "workspace": str(folder)}

    # Tests are only run on an explicit user click. They execute in the project's
    # workspace, with a strict timeout and no shell invocation.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-v"],
            cwd=str(folder), capture_output=True, text=True, timeout=45,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        output = (result.stdout + "\\n" + result.stderr).strip()
        return {"ok": result.returncode == 0, "returncode": result.returncode,
                "output": output or "Test command completed with no output.",
                "workspace": str(folder)}
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")) + "\\n" + ((exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
        return {"ok": False, "returncode": 124,
                "output": "Tests exceeded the 45-second safety timeout.\\n" + output,
                "workspace": str(folder)}


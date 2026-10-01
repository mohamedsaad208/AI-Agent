"""Local project execution, continuous service supervision, readiness probing,
and error diagnostics for AI Code Engineer.

Supports running applications, test suites, and build commands locally without Docker.
Handles process trees, Windows path spacing, project wrappers (mvnw, gradlew), port discovery,
socket/HTTP readiness checks, and plain-language failure diagnostics.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable, Optional

from . import redaction
from . import runner
from .errors import PolicyError

MAX_BUFFER_LINES = 5000
MAX_OUTPUT_CHARS = 200_000
DIAGNOSTIC_TAIL_CHARS = 10_000

PORT_REGEX = re.compile(
    r"(?:https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):|listening on (?:port )?|port[:= ]+|started on (?:port )?)(\d{2,5})",
    re.IGNORECASE
)

PORT_IN_USE_REGEX = re.compile(
    r"(?:EADDRINUSE|address already in use|Address already in use: bind|port \d+ is already in use|bind: address already in use)",
    re.IGNORECASE
)

MISSING_MODULE_REGEX = re.compile(
    r"(?:ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]|"
    r"Cannot find module ['\"]([^'\"]+)['\"]|"
    r"npm ERR! missing: ([^\s,]+)|"
    r"ImportError:\s*cannot import name ['\"]([^'\"]+)['\"])",
    re.IGNORECASE
)

MISSING_COMMAND_REGEX = re.compile(
    r"(?:'([^']+)' is not recognized as an internal or external command|"
    r"command not found:\s*(\S+)|"
    r"No such file or directory:\s*['\"]([^'\"]+)['\"])",
    re.IGNORECASE
)

SYNTAX_ERROR_REGEX = re.compile(
    r"(?:SyntaxError:\s*([^\n]+)|"
    r"compilation error|error: could not compile|"
    r"BUILD FAILURE|Failed to execute goal)",
    re.IGNORECASE
)


def safe_split_command(cmd: str | list[str]) -> list[str]:
    """Parse command line space-safely across Windows and POSIX."""
    if isinstance(cmd, list):
        return [str(part) for part in cmd if str(part).strip()]
    cmd = str(cmd or "").strip()
    if not cmd:
        return []

    def _strip_quotes(tokens: list[str]) -> list[str]:
        cleaned = []
        for t in tokens:
            t = t.strip()
            if len(t) >= 2 and ((t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'"))):
                cleaned.append(t[1:-1])
            else:
                cleaned.append(t)
        return cleaned

    if os.name == "nt":
        # Handle Windows paths with spaces and backslashes properly
        try:
            tokens = shlex.split(cmd, posix=False)
            return _strip_quotes(tokens)
        except ValueError:
            return _strip_quotes(cmd.split())
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def resolve_executable(program: str, cwd: Path) -> tuple[Optional[str], list[str]]:
    """Resolve an executable, favoring project wrappers like mvnw and gradlew.
    Returns (resolved_executable_or_none, extra_prepend_args)."""
    cwd = Path(cwd).resolve()
    base = Path(program).name.lower()
    
    # 1. Project wrappers
    if base in ("mvn", "mvnw", "mvnw.cmd", "mvnw.bat"):
        for wrapper_name in ("mvnw.cmd", "mvnw.bat", "mvnw"):
            candidate = cwd / wrapper_name
            if candidate.is_file():
                if os.name == "nt" and wrapper_name.endswith((".cmd", ".bat")):
                    return str(candidate), []
                elif os.name != "nt" and os.access(candidate, os.X_OK):
                    return str(candidate), []
                elif os.name != "nt":
                    return "/bin/sh", [str(candidate)]
                return str(candidate), []

    if base in ("gradle", "gradlew", "gradlew.cmd", "gradlew.bat"):
        for wrapper_name in ("gradlew.cmd", "gradlew.bat", "gradlew"):
            candidate = cwd / wrapper_name
            if candidate.is_file():
                if os.name == "nt" and wrapper_name.endswith((".cmd", ".bat")):
                    return str(candidate), []
                elif os.name != "nt" and os.access(candidate, os.X_OK):
                    return str(candidate), []
                elif os.name != "nt":
                    return "/bin/sh", [str(candidate)]
                return str(candidate), []

    # 2. Python aliases
    if base in ("python", "python3", "py"):
        return sys.executable, []

    # 3. Direct file in cwd
    local_file = cwd / program
    if local_file.is_file():
        return str(local_file), []

    # 4. Global PATH
    found = shutil.which(program)
    if found:
        return found, []

    # 5. Windows specific fallback (e.g. npm.cmd)
    if os.name == "nt" and not program.endswith((".cmd", ".bat", ".exe")):
        for ext in (".cmd", ".bat", ".exe"):
            found = shutil.which(program + ext)
            if found:
                return found, []

    return None, []


def prepare_argv(command: str | list[str], cwd: Path) -> list[str]:
    """Prepares a robust argv list with wrappers and proper paths."""
    parts = safe_split_command(command)
    if not parts:
        raise PolicyError("Empty command cannot be executed.")
    program = parts[0]
    rest = parts[1:]
    resolved, extra = resolve_executable(program, cwd)
    if not resolved:
        # If not found directly, pass the original so OS can report clear error
        resolved = program
    return extra + [resolved] + rest


def probe_tcp_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.error):
        return False


def probe_http_ready(url: str, timeout: float = 0.8) -> bool:
    """Check if an HTTP service responds to GET requests."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AICodeEngineer-Probe"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return bool(resp.status < 500)
    except urllib.error.HTTPError as e:
        # 401, 403, 404 still means the server is UP and responding
        return e.code < 500
    except (urllib.error.URLError, OSError, socket.error):
        return False


def diagnose_failure(output: str, exit_code: Optional[int] = None) -> dict:
    """Analyze output lines to provide a plain-language diagnosis and action."""
    tail = output[-DIAGNOSTIC_TAIL_CHARS:]
    
    # 1. Port collision
    port_m = PORT_IN_USE_REGEX.search(tail)
    if port_m:
        port_num_m = re.search(r"(?:port\s*[:= ]*|:{1,3})(\d{2,5})\b", tail, re.IGNORECASE)
        port_s = port_num_m.group(1) if port_num_m else "configured port"
        return {
            "kind": "port_in_use",
            "summary": f"Port conflict: {port_s} is already occupied by another process.",
            "suggestion": (
                f"Change the service port in Run Settings, or stop the external application using port {port_s}. "
                "AI Code Engineer will not kill unknown foreign processes automatically."
            ),
            "exit_code": exit_code,
        }

    # 2. Missing dependency / package
    mod_m = MISSING_MODULE_REGEX.search(tail)
    if mod_m:
        pkg = next((g for g in mod_m.groups() if g), "required package")
        return {
            "kind": "missing_dependency",
            "summary": f"Missing dependency: '{pkg}' is required but not installed.",
            "suggestion": f"Install missing dependencies (e.g. `pip install {pkg}` or `npm install {pkg}`).",
            "exit_code": exit_code,
        }

    # 3. Missing toolchain / executable
    cmd_m = MISSING_COMMAND_REGEX.search(tail)
    if cmd_m:
        cmd_name = next((g for g in cmd_m.groups() if g), "command")
        return {
            "kind": "missing_tool",
            "summary": f"Command or runtime not found: '{cmd_name}'.",
            "suggestion": f"Verify '{cmd_name}' is installed on your system and included in PATH.",
            "exit_code": exit_code,
        }

    # 4. Syntax / Compilation failure
    syntax_m = SYNTAX_ERROR_REGEX.search(tail)
    if syntax_m:
        return {
            "kind": "syntax_error",
            "summary": "Build or syntax compilation failure encountered.",
            "suggestion": "Review compiler / linter error lines in the terminal output to fix errors.",
            "exit_code": exit_code,
        }

    if exit_code and exit_code != 0:
        return {
            "kind": "general_error",
            "summary": f"Process exited with non-zero exit code ({exit_code}).",
            "suggestion": "Inspect the terminal error output above for details.",
            "exit_code": exit_code,
        }

    return {
        "kind": "none",
        "summary": "No critical errors detected.",
        "suggestion": "",
        "exit_code": exit_code,
    }


class ServiceProcess:
    """Manages one continuously running service or background process."""

    def __init__(self, service_id: str, name: str, command: str | list[str], cwd: Path,
                 port: Optional[int] = None, on_output: Optional[Callable[[str], None]] = None):
        self.id = service_id
        self.name = name
        self.command_raw = command
        self.cwd = Path(cwd).resolve()
        self.configured_port = port
        self.detected_port: Optional[int] = port
        self.on_output = on_output
        
        self.process: Optional[subprocess.Popen] = None
        self.status = "stopped"  # "stopped", "starting", "ready", "running", "failed"
        self.ready = False
        self.exit_code: Optional[int] = None
        self.started_at: float = 0.0
        self.argv: list[str] = []
        self.buffer: deque[str] = deque(maxlen=MAX_BUFFER_LINES)
        self.diagnosis: dict = {}
        self._probe_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.process and self.process.poll() is None:
                return  # already running

            self._stop_event.clear()
            self.argv = prepare_argv(self.command_raw, self.cwd)
            self.started_at = time.time()
            self.status = "starting"
            self.ready = False
            self.exit_code = None
            self.diagnosis = {}

            env = runner.child_env()
            # Inherit SystemRoot/PATH/TEMP
            options = {
                "cwd": str(self.cwd),
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                options["creationflags"] = new_group | no_window
            else:
                options["start_new_session"] = True

            self.process = subprocess.Popen(self.argv, **options)

            # Start reader thread
            threading.Thread(target=self._reader_loop, daemon=True,
                             name=f"svc-{self.name}-output").start()

            # Start readiness probe thread
            self._probe_thread = threading.Thread(target=self._probe_loop, daemon=True,
                                                  name=f"svc-{self.name}-probe")
            self._probe_thread.start()

    def _reader_loop(self) -> None:
        stream = io.TextIOWrapper(self.process.stdout, encoding="utf-8", errors="replace", newline="\n")
        try:
            for raw_line in iter(stream.readline, ""):
                if self._stop_event.is_set():
                    break
                line = raw_line.rstrip("\r\n")
                with self._lock:
                    self.buffer.append(line)
                    # Check for port in output if not yet detected
                    if not self.detected_port:
                        m = PORT_REGEX.search(line)
                        if m:
                            try:
                                candidate = int(m.group(1))
                                if 1024 <= candidate <= 65535:
                                    self.detected_port = candidate
                            except ValueError:
                                pass
                if self.on_output:
                    try:
                        self.on_output(line)
                    except Exception:
                        pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            with self._lock:
                if self.process:
                    self.process.poll()
                    self.exit_code = self.process.returncode
                    if self.exit_code is not None:
                        full_out = "\n".join(self.buffer)
                        self.diagnosis = diagnose_failure(full_out, self.exit_code)
                        if self._stop_event.is_set():
                            self.status = "stopped"
                        elif self.exit_code == 0:
                            self.status = "stopped"
                        else:
                            self.status = "failed"
                        self.ready = False

    def _probe_loop(self) -> None:
        deadline = time.time() + 45.0  # probe for up to 45 seconds
        while not self._stop_event.is_set() and time.time() < deadline:
            with self._lock:
                if self.process and self.process.poll() is not None:
                    # Process died
                    return
                port = self.detected_port or self.configured_port

            if port:
                if probe_tcp_port("127.0.0.1", port, timeout=0.6):
                    with self._lock:
                        self.ready = True
                        self.status = "ready"
                    return
            time.sleep(1.0)

        with self._lock:
            # If still alive after 45s without detected port, mark as running
            if self.process and self.process.poll() is None and self.status == "starting":
                self.status = "running"

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self.process:
                runner.kill_tree(self.process)
                try:
                    self.process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
                self.exit_code = self.process.returncode
                self.ready = False
                self.status = "stopped"

    def restart(self) -> None:
        self.stop()
        self.start()

    def snapshot(self) -> dict:
        with self._lock:
            port = self.detected_port or self.configured_port
            url = f"http://localhost:{port}" if port and self.ready else ""
            return {
                "id": self.id,
                "name": self.name,
                "command": " ".join(self.argv) if self.argv else str(self.command_raw),
                "cwd": str(self.cwd),
                "status": self.status,
                "ready": self.ready,
                "port": port,
                "url": url,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "diagnosis": self.diagnosis,
                "line_count": len(self.buffer),
            }

    def get_lines(self, max_lines: int = 500) -> list[str]:
        with self._lock:
            return list(self.buffer)[-max_lines:]


class ServiceManager:
    """Manages services across projects."""

    def __init__(self):
        self._services: dict[str, ServiceProcess] = {}
        self._lock = threading.Lock()

    def start_service(self, service_id: str, name: str, command: str | list[str],
                      cwd: Path, port: Optional[int] = None,
                      on_output: Optional[Callable[[str], None]] = None) -> ServiceProcess:
        with self._lock:
            if service_id in self._services:
                self._services[service_id].stop()
            svc = ServiceProcess(service_id, name, command, cwd, port=port, on_output=on_output)
            self._services[service_id] = svc
        svc.start()
        return svc

    def stop_service(self, service_id: str) -> None:
        with self._lock:
            svc = self._services.get(service_id)
        if svc:
            svc.stop()

    def restart_service(self, service_id: str) -> None:
        with self._lock:
            svc = self._services.get(service_id)
        if svc:
            svc.restart()

    def stop_all(self) -> None:
        with self._lock:
            svcs = list(self._services.values())
        for s in svcs:
            s.stop()

    def get_service(self, service_id: str) -> Optional[ServiceProcess]:
        with self._lock:
            return self._services.get(service_id)

    def all_snapshots(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in self._services.values()]


# Global shared instance
GLOBAL_SERVICES = ServiceManager()


# ---------------------------------------------------------------- Project Config
CONFIG_FILE = ".ai_project.json"

def read_project_config(repo: Path) -> dict:
    """Load persistent project execution configuration or infer defaults."""
    repo = Path(repo).resolve()
    cfg_file = repo / CONFIG_FILE
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass

    # Infer defaults from project markers
    return infer_project_config(repo)


def save_project_config(repo: Path, config: dict) -> None:
    """Save persistent project execution configuration."""
    repo = Path(repo).resolve()
    cfg_file = repo / CONFIG_FILE
    # Redact any obvious secrets before saving
    safe_config = sanitize_config_dict(config)
    cfg_file.write_text(json.dumps(safe_config, indent=2, ensure_ascii=False), encoding="utf-8")


def sanitize_config_dict(data: dict) -> dict:
    """Ensure no passwords or secrets are serialized in configuration."""
    clean = {}
    for k, v in data.items():
        if isinstance(v, dict):
            clean[k] = sanitize_config_dict(v)
        elif isinstance(v, list):
            clean[k] = [sanitize_config_dict(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, str) and any(sec in k.lower() for sec in ("secret", "token", "password", "key")):
            clean[k] = "[redacted]"
        else:
            clean[k] = v
    return clean


def infer_project_config(repo: Path) -> dict:
    """Analyze repository structure and build files to infer run, test, and build commands."""
    repo = Path(repo).resolve()
    config = {
        "version": 1,
        "app": {"command": "", "cwd": ".", "port": 0},
        "test": {"command": "", "cwd": "."},
        "build": {"command": "", "cwd": "."},
        "services": [],
    }

    # Node.js / package.json
    pkg_file = repo / "package.json"
    if pkg_file.is_file():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if "start" in scripts:
                config["app"]["command"] = "npm start"
            elif "dev" in scripts:
                config["app"]["command"] = "npm run dev"
            
            if "test" in scripts:
                config["test"]["command"] = "npm test"
            if "build" in scripts:
                config["build"]["command"] = "npm run build"
        except Exception:
            pass

    # Maven
    if (repo / "pom.xml").is_file():
        mvn = "mvnw" if (repo / "mvnw").is_file() or (repo / "mvnw.cmd").is_file() else "mvn"
        if not config["build"]["command"]:
            config["build"]["command"] = f"{mvn} -B compile"
        if not config["test"]["command"]:
            config["test"]["command"] = f"{mvn} -B test"
        if not config["app"]["command"]:
            config["app"]["command"] = f"{mvn} spring-boot:run"

    # Gradle
    if (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file():
        gradle = "gradlew" if (repo / "gradlew").is_file() or (repo / "gradlew.cmd").is_file() else "gradle"
        if not config["build"]["command"]:
            config["build"]["command"] = f"{gradle} assemble"
        if not config["test"]["command"]:
            config["test"]["command"] = f"{gradle} test"
        if not config["app"]["command"]:
            config["app"]["command"] = f"{gradle} bootRun"

    # Python
    py_files = [f.name.lower() for f in repo.glob("*.py")]
    if "main.py" in py_files and not config["app"]["command"]:
        config["app"]["command"] = "python main.py"
    elif "app.py" in py_files and not config["app"]["command"]:
        config["app"]["command"] = "python app.py"
    
    if (repo / "tests").is_dir() or any("test" in f for f in py_files):
        if not config["test"]["command"]:
            config["test"]["command"] = "python -m unittest discover -s tests"

    # Rust Cargo
    if (repo / "Cargo.toml").is_file():
        if not config["build"]["command"]:
            config["build"]["command"] = "cargo build"
        if not config["test"]["command"]:
            config["test"]["command"] = "cargo test"
        if not config["app"]["command"]:
            config["app"]["command"] = "cargo run"

    # Go
    if (repo / "go.mod").is_file():
        if not config["build"]["command"]:
            config["build"]["command"] = "go build ."
        if not config["test"]["command"]:
            config["test"]["command"] = "go test ./..."
        if not config["app"]["command"]:
            config["app"]["command"] = "go run ."

    # Populate services array if app is configured
    if config["app"]["command"]:
        config["services"] = [{
            "name": "app",
            "command": config["app"]["command"],
            "cwd": config["app"]["cwd"],
            "port": config["app"].get("port", 0)
        }]

    return config


# ---------------------------------------------------------------- Project Readiness
def check_project_readiness(repo: Path) -> dict:
    """Inspect toolchains, wrappers, configuration files, and environment requirements."""
    repo = Path(repo).resolve()
    
    # Toolchains check
    tool_specs = [
        ("Python", "python", ["python", "python3"]),
        ("Node.js", "node", ["node"]),
        ("npm", "npm", ["npm", "npm.cmd"]),
        ("Maven", "mvn", ["mvn", "mvn.cmd"]),
        ("Gradle", "gradle", ["gradle", "gradle.cmd"]),
        ("Cargo", "cargo", ["cargo"]),
        ("Go", "go", ["go"]),
        ("Docker", "docker", ["docker"]),
    ]
    tools = []
    for label, id_name, candidates in tool_specs:
        found_path = None
        for cand in candidates:
            p = shutil.which(cand)
            if p:
                found_path = p
                break
        tools.append({
            "name": label,
            "id": id_name,
            "available": bool(found_path),
            "path": found_path or "",
        })

    # Wrapper check
    wrappers = []
    for wrap in ("mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"):
        if (repo / wrap).is_file():
            wrappers.append(wrap)

    # Configs check
    configs_detected = []
    for marker in ("package.json", "pom.xml", "build.gradle", "build.gradle.kts",
                   "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod", "Dockerfile"):
        if (repo / marker).is_file():
            configs_detected.append(marker)

    # Env check
    env_status = {"has_env": False, "has_example": False, "missing_keys": []}
    has_env = (repo / ".env").is_file()
    has_example = (repo / ".env.example").is_file()
    env_status["has_env"] = has_env
    env_status["has_example"] = has_example

    if has_example:
        try:
            ex_lines = (repo / ".env.example").read_text(encoding="utf-8", errors="replace").splitlines()
            ex_keys = [line.split("=")[0].strip() for line in ex_lines if line.strip() and not line.startswith("#") and "=" in line]
            curr_keys = []
            if has_env:
                curr_lines = (repo / ".env").read_text(encoding="utf-8", errors="replace").splitlines()
                curr_keys = [line.split("=")[0].strip() for line in curr_lines if line.strip() and not line.startswith("#") and "=" in line]
            missing = [k for k in ex_keys if k not in curr_keys]
            env_status["missing_keys"] = missing
        except Exception:
            pass

    # Remediation recommendations
    recommendations = []
    if env_status["has_example"] and not env_status["has_env"]:
        recommendations.append("Copy .env.example to .env and configure the necessary environment variables.")
    elif env_status["missing_keys"]:
        recommendations.append(f"Add missing keys to .env: {', '.join(env_status['missing_keys'][:5])}")

    if "package.json" in configs_detected and not any(t["available"] for t in tools if t["id"] == "node"):
        recommendations.append("Node.js is required for this project but was not found on PATH. Install Node.js from nodejs.org.")

    if "pom.xml" in configs_detected and not wrappers and not any(t["available"] for t in tools if t["id"] == "mvn"):
        recommendations.append("Maven is required but neither mvn nor mvnw wrapper was found. Install Maven or add mvnw wrapper.")

    return {
        "tools": tools,
        "wrappers": wrappers,
        "configs": configs_detected,
        "env": env_status,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------- Bounded Job Runner
def execute_bounded_job(command: str | list[str], cwd: Path, timeout: int = 300,
                        on_output: Optional[Callable[[str], None]] = None) -> dict:
    """Run a bounded task (such as a build or test command) to completion with tree killing on timeout."""
    cwd = Path(cwd).resolve()
    argv = prepare_argv(command, cwd)
    started = time.monotonic()
    
    options = {
        "cwd": str(cwd),
        "env": runner.child_env(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        options["creationflags"] = new_group | no_window
    else:
        options["start_new_session"] = True

    proc = subprocess.Popen(argv, **options)
    lines: list[str] = []
    timed_out = False
    
    stream = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace", newline="\n")
    chunks: queue.Queue = queue.Queue(maxsize=1000)
    stop_read = threading.Event()

    def reader():
        try:
            for l in iter(stream.readline, ""):
                if stop_read.is_set():
                    break
                try:
                    chunks.put(l, timeout=0.2)
                except queue.Full:
                    pass
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            chunks.put(None)

    threading.Thread(target=reader, daemon=True).start()

    deadline = started + timeout
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            timed_out = True
            runner.kill_tree(proc)
            break
        try:
            chunk = chunks.get(timeout=min(max(rem, 0.05), 0.5))
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if chunk is None:
            break
        line = chunk.rstrip("\r\n")
        lines.append(line)
        if on_output:
            try:
                on_output(line)
            except Exception:
                pass

    stop_read.set()
    if proc.poll() is None:
        runner.kill_tree(proc)
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            pass

    exit_code = proc.returncode if proc.returncode is not None else (-1 if timed_out else 0)
    duration = round(time.monotonic() - started, 2)
    full_output = "\n".join(lines)
    diagnosis = diagnose_failure(full_output, exit_code)

    return {
        "command": " ".join(argv),
        "cwd": str(cwd),
        "exit_code": exit_code,
        "duration": duration,
        "timed_out": timed_out,
        "output": full_output,
        "diagnosis": diagnosis,
        "success": exit_code == 0 and not timed_out,
    }

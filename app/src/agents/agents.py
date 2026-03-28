"""
agents.py — SentinelAI v2 — Completely Rewritten Specialist Agents

AGENTS IN THIS FILE:
  SystemAgent   — Windows desktop control with visual verification (screenshot-driven)
  FileAgent     — Complete filesystem: read/write/search/PDF/CSV/Excel/image conversion
  TerminalAgent — NEW: Runs PowerShell/CMD commands, pip installs, Python scripts, git ops
  CodeAgent     — NEW: Writes, edits, runs code files, creates project scaffolding
  MediaAgent    — Local media playback, YouTube, streaming sites
  RAGAgent      — FAISS knowledge base search
  UtilityAgent  — Datetime, clipboard, notifications, screenshots

DESIGN PHILOSOPHY:
  - Every agent has a tightly scoped toolset (no tool pollution between agents)
  - System/File agents never overlap: SystemAgent opens/controls, FileAgent creates/reads
  - TerminalAgent is the "power tool": runs any shell command with full output
  - CodeAgent writes code and uses TerminalAgent logic to execute it
  - All agents use SambaNova Llama-4-Maverick for reasoning (high context, function calls)
  - Prompts written like senior engineer internal docs: precise, no fluff
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
import tempfile
import shutil
import webbrowser
from pathlib import Path
from typing import Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from app.src.llm_rotation import get_llm, get_sambanova_llm

# ── Import all base tools ────────────────────────────────────────────────────
from app.src.tools import (
    open_application, open_in_app, open_folder, open_file_with_app,
    close_application, kill_app_instances, sleep,
    focus_window, click_window_control, click_element_by_text,
    type_in_window, scroll_window,
    keyboard_type, keyboard_press, mouse_move, mouse_click, set_clipboard,
    create_file, append_file, read_file, delete_file, copy_file, list_files,
    write_csv, read_csv, csv_to_excel, excel_to_csv, list_excel_sheets,
    convert_image_format,
    run_shell_command, open_cmd_and_run,
    web_search, open_website,
    play_media, find_media_files,
    search_knowledge_base,
    get_datetime, take_screenshot, ask_user, vision_act_on_screen,
    run_multi_step_actions,
    search_files, get_file_info, list_folder_tree,
    get_desktop_path, manage_custom_tool,
)


# =============================================================================
#  SHARED EXTRA TOOLS
# =============================================================================

@tool
def read_pdf(file_path: str) -> str:
    """Extract full text from a PDF file. Tries pdfplumber -> pypdf -> pdftotext CLI.
    Returns up to 15,000 characters.

    Args:
        file_path: Absolute path to the PDF.
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        return f"PDF not found: {path}"
    try:
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                return text[:15000] or "PDF has no extractable text."
        except ImportError:
            pass
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            return text[:15000] or "PDF has no extractable text."
        except ImportError:
            pass
        result = subprocess.run(["pdftotext", str(path), "-"],
                                capture_output=True, text=True, timeout=20)
        return result.stdout[:15000] or f"pdftotext returned no text for {path}"
    except Exception as e:
        return f"PDF read failed ({path}): {e}"


@tool
def download_file_http(url: str, save_path: str) -> str:
    """Download a file from a URL and save it locally.

    Args:
        url: Direct download URL.
        save_path: Full destination path including filename.
    """
    import urllib.request
    try:
        dst = Path(save_path).expanduser()
        dst.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, str(dst))
        size = dst.stat().st_size
        return f"Downloaded -> {dst} ({size / 1024:.1f} KB)"
    except Exception as e:
        return f"Download failed ({url}): {e}"


@tool
def install_application(installer_path: str, silent: bool = True) -> str:
    """Run a .exe or .msi installer.

    Args:
        installer_path: Full path to the installer file.
        silent: Attempt silent install. Default True.
    """
    path = Path(installer_path).expanduser()
    if not path.exists():
        return f"Installer not found: {path}"
    try:
        if path.suffix.lower() == ".msi":
            args = ["msiexec", "/i", str(path)]
            if silent:
                args += ["/quiet", "/norestart"]
        else:
            args = [str(path)]
            if silent:
                args += ["/S"]
        proc = subprocess.Popen(args)
        time.sleep(3)
        return (f"Installer launched: {path.name} (running)" if proc.poll() is None
                else f"Installer completed: {path.name}")
    except Exception as e:
        return f"Install failed ({path.name}): {e}"


@tool
def send_desktop_notification(title: str, message: str) -> str:
    """Send a Windows 10/11 toast notification.

    Args:
        title: Notification title.
        message: Notification body text.
    """
    try:
        t = title.replace("'", "")
        m = message.replace("'", "")
        script = (
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"$n=New-Object System.Windows.Forms.NotifyIcon; "
            f"$n.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$n.BalloonTipTitle='{t}'; "
            f"$n.BalloonTipText='{m}'; "
            f"$n.Visible=$True; $n.ShowBalloonTip(5000); "
            f"Start-Sleep -s 6; $n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return f"Notification sent: '{title}'"
    except Exception as e:
        return f"Notification failed: {e}"


@tool
def open_youtube_url(url: str) -> str:
    """Open a YouTube URL in the default browser.

    Args:
        url: Full YouTube video or channel URL.
    """
    webbrowser.open(url)
    return f"Opened in browser: {url}"


# =============================================================================
#  TERMINAL TOOLS
# =============================================================================

@tool
def run_powershell(script: str, timeout: int = 60) -> str:
    """Run a PowerShell script/command and return combined stdout+stderr.

    Best for: Windows services, registry, network adapters, WMI, ACLs,
    environment variables, process management, scheduled tasks, file permissions.

    Args:
        script: Any valid PowerShell code (can be multi-line).
        timeout: Max seconds to wait. Default 60.

    Examples:
        run_powershell("Get-Process | Where-Object CPU -gt 10 | Select Name,CPU")
        run_powershell("Get-NetAdapter | Select Name,Status,LinkSpeed")
        run_powershell("$env:PATH")
        run_powershell("netstat -ano | Select-String ':8080'")
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"[Exit {result.returncode}]\nOUT: {out}\nERR: {err}" if err else f"[Exit {result.returncode}]\n{out}"
        return out or "[Completed, no output] (exit 0)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[PowerShell failed: {e}]"


@tool
def run_cmd(command: str, working_dir: str = "", timeout: int = 60) -> str:
    """Run a Windows CMD command and return stdout+stderr output.

    Best for: dir, copy, move, del, tasklist, ipconfig, ping, net commands,
    running .bat scripts, choco/winget package managers, npm, cargo, dotnet.

    Args:
        command: Any valid CMD command.
        working_dir: Optional directory to run from. Default: user home.
        timeout: Max seconds. Default 60.

    Examples:
        run_cmd("dir C:\\\\Users\\\\nchar\\\\Desktop")
        run_cmd("tasklist /FI \"IMAGENAME eq python.exe\"")
        run_cmd("ping -n 3 google.com")
        run_cmd("winget install --id Microsoft.VisualStudioCode -e")
        run_cmd("npm install", working_dir="C:/Projects/myapp")
    """
    cwd = working_dir.strip() if working_dir.strip() else str(Path.home())
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"[Exit {result.returncode}]\nOUT: {out}\nERR: {err}" if err else f"[Exit {result.returncode}]\n{out}"
        return out or "[Completed, no output] (exit 0)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[CMD failed: {e}]"


@tool
def run_python_script(
    script_path: str,
    args: str = "",
    working_dir: str = "",
    python_exe: str = "",
    timeout: int = 120
) -> str:
    """Execute a Python script and return its stdout+stderr output.

    Args:
        script_path: Full path to the .py file.
        args: Space-separated command-line arguments.
        working_dir: Directory to run from. Defaults to script's parent folder.
        python_exe: Specific Python interpreter. Auto-detected if empty.
        timeout: Max seconds. Default 120.

    Examples:
        run_python_script("C:/Users/nchar/Desktop/analyze.py")
        run_python_script("C:/Projects/app.py", args="--debug --port 8080")
    """
    path = Path(script_path).expanduser()
    if not path.exists():
        return f"Script not found: {path}"

    cwd = working_dir.strip() if working_dir.strip() else str(path.parent)

    if not python_exe.strip():
        python_exe = sys.executable or shutil.which("python") or shutil.which("python3") or "python"

    cmd = [python_exe, str(path)]
    if args.strip():
        import shlex
        cmd += shlex.split(args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = []
        if out:
            parts.append(f"STDOUT:\n{out}")
        if err:
            parts.append(f"STDERR:\n{err}")
        return ("\n\n".join(parts) or "(no output)") + f"\n[Exit code: {result.returncode}]"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[Failed to run script: {e}]"


@tool
def pip_install(packages: str, env_path: str = "", upgrade: bool = False) -> str:
    """Install Python packages using pip.

    Args:
        packages: Space or comma-separated package names.
        env_path: Optional path to a Python virtual environment root.
        upgrade: Pass --upgrade flag. Default False.

    Examples:
        pip_install("requests beautifulsoup4")
        pip_install("torch torchvision", env_path="C:/envs/ml")
        pip_install("langchain", upgrade=True)
    """
    if env_path.strip():
        p = Path(env_path).expanduser()
        pip = str(p / "Scripts" / "pip.exe") if (p / "Scripts" / "pip.exe").exists() else str(p / "bin" / "pip")
    else:
        pip = str(Path(sys.executable).parent / "pip.exe")
        if not Path(pip).exists():
            pip = shutil.which("pip") or shutil.which("pip3") or "pip"

    pkg_list = [p.strip() for p in packages.replace(",", " ").split() if p.strip()]
    if not pkg_list:
        return "No packages specified."

    cmd = [pip, "install"] + (["--upgrade"] if upgrade else []) + pkg_list
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return f"pip install FAILED:\n{err or out}"
        summary = [l for l in out.splitlines() if "Successfully" in l or "already satisfied" in l.lower()]
        return "\n".join(summary) if summary else (out[:600] or "Install completed.")
    except subprocess.TimeoutExpired:
        return "[pip install timed out after 5 minutes]"
    except Exception as e:
        return f"pip_install failed: {e}"


@tool
def run_git_command(git_args: str, working_dir: str = "") -> str:
    """Run a git command in a repository directory.

    Args:
        git_args: Everything after 'git'. Examples: "status", "log --oneline -10",
                  "add .", "commit -m 'fix'", "push origin main".
        working_dir: Path to the git repository. Defaults to user home.

    Examples:
        run_git_command("status", "C:/Projects/myapp")
        run_git_command("log --oneline -5", "C:/Projects/myapp")
        run_git_command("clone https://github.com/user/repo C:/Projects/repo")
    """
    cwd = working_dir.strip() if working_dir.strip() else str(Path.home())
    try:
        result = subprocess.run(
            f"git {git_args}", shell=True, capture_output=True, text=True,
            timeout=120, cwd=cwd
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = "\n".join(filter(None, [out, err]))
        if result.returncode != 0 and not out:
            return f"[git exit {result.returncode}] {combined}"
        return combined or f"[git {git_args} completed, no output]"
    except subprocess.TimeoutExpired:
        return "[git command timed out]"
    except Exception as e:
        return f"[git command failed: {e}]"


@tool
def get_system_info() -> str:
    """Return a snapshot of the current Windows system: OS, CPU, RAM, disk, Python version."""
    lines = []
    try:
        import platform
        lines.append(f"OS: {platform.system()} {platform.release()} ({platform.version()})")
        lines.append(f"Architecture: {platform.machine()}")
        lines.append(f"Python: {sys.version}")
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        lines.append(f"CPU usage: {cpu}%")
        lines.append(f"RAM: {ram.used // 1024**2}MB / {ram.total // 1024**2}MB ({ram.percent}%)")
        for disk in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(disk.mountpoint)
                lines.append(f"Disk {disk.device}: {usage.used // 1024**3}GB/{usage.total // 1024**3}GB ({usage.percent}%)")
            except Exception:
                pass
    except ImportError:
        r = subprocess.run("systeminfo", capture_output=True, text=True, timeout=20, shell=True)
        lines.append(r.stdout[:2000])

    lines.append(f"Python exe: {sys.executable}")
    lines.append(f"CWD: {os.getcwd()}")
    lines.append(f"User: {os.environ.get('USERNAME', 'unknown')}")
    return "\n".join(lines)


@tool
def create_virtualenv(env_path: str, python_exe: str = "") -> str:
    """Create a Python virtual environment at the specified path.

    Args:
        env_path: Full path for the new environment.
        python_exe: Specific Python interpreter. Auto-detected if empty.
    """
    python = python_exe.strip() if python_exe.strip() else sys.executable
    dst = Path(env_path).expanduser()
    try:
        result = subprocess.run([python, "-m", "venv", str(dst)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return f"venv creation FAILED:\n{result.stderr}"
        return f"Virtual environment created at: {dst}\nActivate: {dst}\\Scripts\\activate.bat"
    except Exception as e:
        return f"create_virtualenv failed: {e}"


@tool
def open_terminal_at(path: str = "", shell: str = "powershell") -> str:
    """Open a new terminal window at a directory.

    Args:
        path: Directory to open terminal in. Defaults to user home.
        shell: 'powershell', 'cmd', or 'wt' (Windows Terminal). Default: 'powershell'.
    """
    cwd = path.strip() if path.strip() else str(Path.home())
    if not Path(cwd).exists():
        return f"Directory not found: {cwd}"
    try:
        shell_l = shell.lower().strip()
        if shell_l == "wt":
            subprocess.Popen(["wt", "-d", cwd])
        elif shell_l == "cmd":
            subprocess.Popen(["cmd.exe", "/K", f"cd /d {cwd}"],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(
                ["powershell", "-NoExit", "-Command", f"Set-Location '{cwd}'"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        time.sleep(0.8)
        return f"Opened {shell} terminal at: {cwd}"
    except Exception as e:
        return f"Failed to open terminal: {e}"


# =============================================================================
#  CODE TOOLS
# =============================================================================

@tool
def write_code_file(file_path: str, code: str, overwrite: bool = True) -> str:
    """Write code to a file, creating parent directories as needed.

    Args:
        file_path: Full path to the code file.
        code: Complete file content.
        overwrite: If False and file exists, returns error. Default True.
    """
    path = Path(file_path).expanduser()
    if path.exists() and not overwrite:
        return f"File already exists (overwrite=False): {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(code, encoding="utf-8")
        lines = code.count("\n") + 1
        return f"Written {lines} lines to: {path}"
    except Exception as e:
        return f"Failed to write {path}: {e}"


@tool
def read_code_file(file_path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a code file with numbered lines and optional line range.

    Args:
        file_path: Path to the file.
        start_line: First line (1-indexed). Default 1.
        end_line: Last line (0 = all). Default 0.
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        return f"File not found: {path}"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        s = max(0, start_line - 1)
        e = end_line if end_line > 0 else total
        selected = lines[s:e]
        numbered = "\n".join(f"{s + i + 1:4d} | {l}" for i, l in enumerate(selected))
        return f"--- {path.name} (lines {s+1}-{min(e, total)} of {total}) ---\n{numbered}"
    except Exception as ex:
        return f"Failed to read {path}: {ex}"


@tool
def patch_code_file(file_path: str, old_snippet: str, new_snippet: str) -> str:
    """Replace an exact snippet in a code file. Fails if not found or ambiguous.

    Args:
        file_path: Path to the file to edit.
        old_snippet: Exact text to find (must appear exactly once).
        new_snippet: Replacement text.
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        return f"File not found: {path}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_snippet)
        if count == 0:
            return f"Snippet not found in {path.name}. Check whitespace/exact characters."
        if count > 1:
            return f"Snippet appears {count} times. Make it more specific."
        updated = content.replace(old_snippet, new_snippet, 1)
        path.write_text(updated, encoding="utf-8")
        return f"Patched {path.name}: replaced 1 occurrence."
    except Exception as e:
        return f"Failed to patch {path}: {e}"


@tool
def scaffold_project(project_path: str, project_type: str, project_name: str = "") -> str:
    """Create a complete project folder structure with boilerplate files.

    Args:
        project_path: Parent directory where the project folder will be created.
        project_type: One of: python-script, python-fastapi, python-cli,
                      html-site, data-science.
        project_name: Name for the project folder.
    """
    base = Path(project_path).expanduser()
    name = project_name.strip() or project_type.replace("-", "_")
    root = base / name

    templates: dict[str, dict[str, str]] = {
        "python-script": {
            "README.md": f"# {name}\n\nA Python script project.\n",
            "main.py": f'"""Main entry point."""\n\n\ndef main():\n    print("Hello from {name}!")\n\n\nif __name__ == "__main__":\n    main()\n',
            "requirements.txt": "# Add your dependencies here\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\ndist/\n",
        },
        "python-fastapi": {
            "README.md": f"# {name}\n\nFastAPI application.\n\n## Run\n\n```\nuvicorn app.main:app --reload\n```\n",
            "app/__init__.py": "",
            "app/main.py": f'from fastapi import FastAPI\n\napp = FastAPI(title="{name}")\n\n\n@app.get("/")\ndef root():\n    return {{"message": "Hello from {name}!"}}\n',
            "app/routers/__init__.py": "",
            "app/models.py": "from pydantic import BaseModel\n\n# Define your Pydantic models here\n",
            "requirements.txt": "fastapi\nuvicorn[standard]\npython-dotenv\n",
            ".env": "DEBUG=true\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\n.env\n",
        },
        "python-cli": {
            "README.md": f"# {name}\n\nA Python CLI tool.\n",
            f"{name.replace('-', '_')}/cli.py": 'import click\n\n\n@click.group()\ndef cli():\n    """CLI tool."""\n    pass\n\n\n@cli.command()\ndef hello():\n    """Say hello."""\n    click.echo("Hello!")\n\n\nif __name__ == "__main__":\n    cli()\n',
            f"{name.replace('-', '_')}/__init__.py": "",
            "requirements.txt": "click\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\n",
        },
        "html-site": {
            "README.md": f"# {name}\n\nA static HTML website.\n",
            "index.html": f"<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"UTF-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n  <title>{name}</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n  <h1>Welcome to {name}</h1>\n  <script src=\"script.js\"></script>\n</body>\n</html>\n",
            "style.css": "* { box-sizing: border-box; margin: 0; padding: 0; }\nbody { font-family: system-ui, sans-serif; padding: 2rem; }\n",
            "script.js": "// JavaScript goes here\nconsole.log('Loaded');\n",
        },
        "data-science": {
            "README.md": f"# {name}\n\nData science project.\n",
            "notebooks/.gitkeep": "",
            "data/raw/.gitkeep": "",
            "data/processed/.gitkeep": "",
            "src/__init__.py": "",
            "src/data_loader.py": "import pandas as pd\n\n\ndef load_data(path: str) -> pd.DataFrame:\n    return pd.read_csv(path)\n",
            "src/model.py": "# Model training code here\n",
            "requirements.txt": "numpy\npandas\nscikit-learn\nmatplotlib\njupyterlab\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\ndata/raw/*\n!data/raw/.gitkeep\n",
        },
    }

    template = templates.get(project_type.lower())
    if template is None:
        return f"Unknown project_type '{project_type}'. Available: {', '.join(templates.keys())}"

    try:
        root.mkdir(parents=True, exist_ok=True)
        created = []
        for rel_path, content in template.items():
            fpath = root / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            created.append(str(fpath.relative_to(root)))
        return (
            f"Project '{name}' ({project_type}) created at: {root}\n"
            f"Files ({len(created)}):\n" + "\n".join(f"  {f}" for f in created)
        )
    except Exception as e:
        return f"scaffold_project failed: {e}"


# =============================================================================
#  VERIFICATION TOOLS  (Orchestrator CHECK-ACT loop)
# =============================================================================

def build_verification_tools(vision_llm=None) -> list:
    """
    Build verify_with_screenshot and verify_file_exists for the Orchestrator's
    CHECK-ACT loop. These are supervisor-level tools — not exposed to agents directly.
    """
    if vision_llm is None:
        vision_model = os.getenv("SAMBANOVA_VISION_MODEL", "Llama-3.2-90B-Vision-Instruct")
        vision_llm = get_sambanova_llm(model=vision_model, temperature=0.1)

    @tool
    def verify_with_screenshot(question: str) -> str:
        """Take a screenshot and answer a yes/no verification question.
        Use after EVERY agent call that touches the UI.
        """
        try:
            import pyautogui
        except ImportError:
            return "VERIFY_ERROR: pyautogui not installed."
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            path = f.name
        try:
            pyautogui.screenshot().save(path)
            with open(path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            from langchain_core.messages import HumanMessage
            msg = HumanMessage(content=[
                {"type": "text", "text": (
                    f"Verifying a Windows desktop screenshot.\n"
                    f"Question: {question}\n\n"
                    f"1. Describe what you see (1-2 sentences).\n"
                    f"2. Answer: YES or NO.\n"
                    f"3. If NO, explain what is wrong."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ])
            resp = vision_llm.invoke([msg])
            return f"SCREENSHOT VERIFY:\n{str(resp.content).strip()}"
        except Exception as exc:
            return f"SCREENSHOT_TAKEN (vision failed: {exc}). Proceeding on agent report."
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @tool
    def verify_file_exists(file_path: str, expected_content_snippet: str = "") -> str:
        """Check whether a file exists and optionally contains an expected snippet."""
        path = Path(file_path).expanduser()
        if not path.exists():
            return f"VERIFY FAILED: File does NOT exist: {path}"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"VERIFY: File exists but could not be read: {exc}"
        preview = content[:300] + ("..." if len(content) > 300 else "")
        result = f"VERIFY OK: File exists ({path.stat().st_size:,} bytes)\nPreview:\n{preview}"
        if expected_content_snippet:
            found = expected_content_snippet.lower() in content.lower()
            result += f"\nCONTAINS '{expected_content_snippet}': {'YES' if found else 'NO'}"
        return result

    return [verify_with_screenshot, verify_file_exists]


# =============================================================================
#  SHARED BUILDER HELPER
# =============================================================================

def _make_agent(llm, tools: list, system_prompt: str, max_iter: int = 20) -> AgentExecutor:
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent, tools=tools, verbose=True,
        handle_parsing_errors=True, max_iterations=max_iter,
    )


# =============================================================================
#  SYSTEM AGENT  — Windows UI controller
# =============================================================================

def build_system_agent(llm) -> AgentExecutor:
    """
    SystemAgent: Controls Windows desktop - opens apps, focuses windows,
    clicks elements, types text, scrolls, installs software.
    Does NOT create files or run terminal commands.
    """
    tools = [
        open_application, open_in_app, open_folder, open_file_with_app,
        close_application, kill_app_instances, sleep,
        focus_window, click_window_control, click_element_by_text,
        type_in_window, scroll_window,
        keyboard_type, keyboard_press, mouse_move, mouse_click, set_clipboard,
        vision_act_on_screen, take_screenshot,
        install_application, send_desktop_notification,
        run_multi_step_actions,
    ]

    prompt = """You are SystemAgent — the Windows desktop controller for SentinelAI.

YOUR JOB: Open, focus, click, type, scroll, install things in the Windows UI.
NOT YOUR JOB: Creating/reading files (FileAgent), running shell commands (TerminalAgent).

TOOL PRIORITY ORDER:

OPENING APPS:
  1. kill_app_instances(app)           <- always kill first for a clean session
  2. open_application(app_name)        <- 'notepad', 'chrome', 'vscode', 'spotify', 'whatsapp'
  3. sleep(seconds=1.5)                <- wait after opening
  4. open_file_with_app(path, app)     <- open a specific file in an app
  5. open_in_app(app, path)            <- open a folder/project in app (e.g. vscode + project dir)

OPENING FOLDERS (File Explorer):
  -> open_folder("desktop") / open_folder("downloads") / open_folder("C:/path")
  -> Shortcuts: "desktop", "downloads", "documents", "pictures", "onedrive"

FOCUSING WINDOWS & INTERACTING:
  1. focus_window(".*AppName.*")       <- ALWAYS before type/click
  2. sleep(seconds=0.5)                <- after focus, before interact
  3. type_in_window(regex, text)       <- type text into window
  4. click_element_by_text(regex, text) <- click by visible label
  5. click_window_control(regex, title) <- click by control title
  6. keyboard_press("ctrl+s")          <- hotkeys/shortcuts
  7. vision_act_on_screen(desc, "click") <- last resort for unlabeled elements

FOR APPS THAT BLOCK DIRECT TYPING (WhatsApp, Discord, Electron apps):
  -> set_clipboard(text) then keyboard_press("ctrl+v")

BATCH 3+ STEPS:
  -> ALWAYS use run_multi_step_actions([...steps...]) for tasks with 3 or more steps.
  -> This is faster and avoids redundant LLM round-trips.

CRITICAL RULES:
- Desktop may be redirected to OneDrive/Desktop. Use shortcut "desktop" - tool resolves it.
- NEVER write files. If user wants to save text: say "use file_agent".
- NEVER use [username] placeholders. Use real paths or shortcuts.
- Use .*AppName.* wildcards in window_title_regex arguments.
- After run_multi_step_actions, confirm what was accomplished.

Reply with a factual description of what you did."""

    return _make_agent(llm, tools, prompt, max_iter=20)


# =============================================================================
#  FILE AGENT  — filesystem specialist
# =============================================================================

def build_file_agent(llm) -> AgentExecutor:
    """
    FileAgent: Creates, reads, writes, searches, converts files.
    The authoritative agent for anything touching the filesystem.
    Does NOT open UI windows or run scripts.
    """
    tools = [
        create_file, append_file, read_file, delete_file, copy_file, list_files,
        read_pdf,
        search_files, get_file_info, list_folder_tree,
        write_csv, read_csv, csv_to_excel, excel_to_csv, list_excel_sheets,
        convert_image_format,
        download_file_http,
        run_shell_command,
    ]

    prompt = """You are FileAgent — the filesystem expert for SentinelAI.

YOUR JOB: Create, read, write, search, move, delete, convert files.
NOT YOUR JOB: Opening files in apps (SystemAgent), running scripts (TerminalAgent).

TOOL SELECTION:

FINDING FILES - ALWAYS search before assuming a path:
  search_files(name_pattern, search_dir, extension)
  -> Use when user says "find", "locate", "search for" any file.
  -> NEVER guess paths. If you don't know where a file is, call search_files first.
  -> Example: search_files(name_pattern="resume", extension="pdf")
  -> Example: search_files(name_pattern="budget", search_dir="C:/Users/nchar/Documents")

  list_folder_tree(directory_path, max_depth=2)
  -> Browse a folder structure to understand a project.

  get_file_info(path)
  -> Check size, type, modification date, permissions.

READING FILES:
  read_file(path)             <- text files (up to 4000 chars)
  read_pdf(path)              <- extract text from PDFs (up to 15,000 chars)
  read_csv(path)              <- CSV data as JSON array
  list_excel_sheets(path)     <- Excel sheet names
  excel_to_csv(path, out)     <- convert then read_csv

WRITING FILES:
  create_file(path, content)       <- create/overwrite text file
  append_file(path, content)       <- add to end of existing file
  write_csv(path, headers, rows)   <- structured CSV output
  csv_to_excel(csv, xlsx)          <- convert to Excel

MODIFYING FILES:
  1. read_file(path) to get current content
  2. Modify the content in your reasoning
  3. create_file(path, new_content) to overwrite with changes
  For binary files: use run_shell_command with PowerShell one-liners.

DOWNLOADING:
  download_file_http(url, save_path)

IMAGE CONVERSION:
  convert_image_format(input, output_format)  <- jpg<->png<->webp<->bmp

MANDATORY RULES:
1. NEVER guess file paths. Use search_files FIRST when unsure.
2. NEVER open files in apps - only find/read/write them.
   When user says "find X and open it": find the path, return it.
   The system_agent will open it.
3. For multi-file ops: process each file, collect results, report all paths.
4. Always confirm paths of what was created/modified/deleted.

Reply with the exact file paths of what was created/found/modified."""

    return _make_agent(llm, tools, prompt, max_iter=20)


# =============================================================================
#  TERMINAL AGENT  — shell, Python runner, pip, git
# =============================================================================

def build_terminal_agent(llm) -> AgentExecutor:
    """
    TerminalAgent: Runs PowerShell/CMD commands, executes Python scripts,
    installs packages, manages git repos, creates virtual environments.

    Like a senior dev with a terminal open - chains commands, reads output,
    retries on error, reports exactly what happened.
    """
    tools = [
        run_powershell,
        run_cmd,
        run_python_script,
        pip_install,
        run_git_command,
        get_system_info,
        create_virtualenv,
        open_terminal_at,
        create_file,
        read_file,
        search_files,
    ]

    prompt = """You are TerminalAgent — a skilled systems engineer with full shell access for SentinelAI.

YOUR JOB: Run commands, execute scripts, install packages, manage git, create environments.
YOU ACT LIKE Claude Code: run real commands, read real output, never fabricate results.

TOOL SELECTION:

RUNNING COMMANDS:
  run_powershell(script)          <- Windows-native: services, registry, network, WMI, ACL
  run_cmd(command, working_dir)   <- CMD: dir, tasklist, ipconfig, bat files, winget, choco, npm

  CHOOSE:
    PowerShell: Get-*, Set-*, New-*, registry, network adapters, event logs, file permissions
    CMD: legacy commands, external tools, batch scripts, npm/cargo/dotnet

RUNNING PYTHON:
  run_python_script(path, args, working_dir)
  -> For inline scripts: create_file("C:/tmp/run.py", code) then run_python_script it.
  -> Returns full stdout + stderr + exit code.

PACKAGE MANAGEMENT:
  pip_install("package1 package2")     <- install into current Python
  pip_install(packages, env_path)      <- install into specific venv
  create_virtualenv(path)              <- create new Python venv

GIT:
  run_git_command("status", repo_dir)
  run_git_command("log --oneline -10", repo_dir)
  run_git_command("add . && git commit -m 'msg'", repo_dir)
  run_git_command("clone https://github.com/user/repo C:/path")

SYSTEM INFO:
  get_system_info()   <- OS, CPU, RAM, disk, Python version, user

OPEN TERMINAL WINDOW:
  open_terminal_at(path, shell)  <- opens visible PowerShell/CMD/WT window

WORKING PRINCIPLES:
1. ALWAYS run real commands. Never describe what a command "would" do.
2. Read output carefully. If exit code != 0, read the error and fix it.
3. Chain commands logically:
   - Check if dependency exists -> install if missing -> run task
   - Create script -> run it -> read output -> report result
4. For long-running tasks (>60s), increase the timeout parameter.
5. Report EXACT output: stdout, stderr, exit code. No fabrication.
6. If a Python import fails (ImportError), pip_install it first then retry.

EXAMPLE WORKFLOW - "run my analysis script":
  1. search_files(name_pattern="analysis", extension="py")   <- find the script
  2. read_file(path)                                          <- understand it
  3. run_python_script(path)                                  <- run it
  4. If ImportError: pip_install the missing package, retry
  5. Report full output

Reply with the actual command output, not a description of it."""

    return _make_agent(llm, tools, prompt, max_iter=25)


# =============================================================================
#  CODE AGENT  — writes, edits, executes code
# =============================================================================

def build_code_agent(llm) -> AgentExecutor:
    """
    CodeAgent: Writes code from scratch, edits existing files, creates project
    scaffolding, runs code and interprets the output. Like a coding assistant
    with real file access and execution capability.
    """
    tools = [
        write_code_file,
        read_code_file,
        patch_code_file,
        scaffold_project,
        run_python_script,
        run_powershell,
        run_cmd,
        pip_install,
        run_git_command,
        search_files, list_folder_tree, get_file_info,
        read_file, list_files,
        create_file,
    ]

    prompt = """You are CodeAgent — an expert software engineer for SentinelAI.

YOUR JOB: Write code, edit existing files, scaffold projects, run and fix code.
YOU THINK like a senior engineer: understand first, write clean code, test it.

TOOL SELECTION:

WRITING CODE:
  write_code_file(path, code)              <- create/overwrite a code file
  patch_code_file(path, old, new)          <- surgical edit: replace exact snippet
  scaffold_project(path, type, name)       <- full project with boilerplate

  project_type options:
    python-script | python-fastapi | python-cli | html-site | data-science

READING CODE:
  read_code_file(path)                     <- numbered lines, full file
  read_code_file(path, start_line, end_line) <- specific range
  list_folder_tree(path, max_depth=3)      <- understand project structure

RUNNING & TESTING:
  run_python_script(path, args)   <- run .py file, get stdout/stderr
  run_cmd(command, cwd)           <- tests, npm, cargo, dotnet, etc.
  pip_install(packages)           <- install missing dependencies

GIT:
  run_git_command("status", cwd)
  run_git_command("add . ", cwd)
  run_git_command("commit -m 'msg'", cwd)

CODE QUALITY RULES:
1. Before writing: read existing code first with read_code_file or list_folder_tree.
2. Use patch_code_file for edits - never rewrite a file you haven't read.
3. After writing: run the code. Fix errors iteratively. Report actual output.
4. Python: use type hints, docstrings, pathlib.Path (not os.path).
5. HTML/CSS: HTML5 semantic tags, CSS custom properties, mobile-friendly.
6. Never leave broken placeholders (TODO without explanation, hardcoded secrets).

EXAMPLE WORKFLOW - "add a /health endpoint to my FastAPI app":
  1. search_files(name_pattern="main.py")       <- find the file
  2. read_code_file(path)                        <- understand existing routes
  3. patch_code_file(path, old_code, new_code)   <- add the endpoint
  4. run_python_script(path)                     <- verify no errors
  5. Report the new endpoint path

Reply with: files changed, key decisions made, and actual run output."""

    return _make_agent(llm, tools, prompt, max_iter=25)


# =============================================================================
#  MEDIA AGENT
# =============================================================================

def build_media_agent(llm) -> AgentExecutor:
    tools = [find_media_files, play_media, open_website, open_youtube_url]
    prompt = """You are MediaAgent — music, video, and streaming specialist.

TOOLS:
  find_media_files(directory, extension_filter) <- list local media files
  play_media(query_or_path)                     <- play by filename keyword or full path
  open_youtube_url(url)                         <- open YouTube video in browser
  open_website(url)                             <- open Spotify, Netflix, etc.

WORKFLOW:
  Local media: find_media_files() first -> then play_media(path)
  YouTube: open_youtube_url("https://youtube.com/watch?v=...")
  Streaming: open_website("spotify.com") or open_website("music.youtube.com")

Reply with what is now playing or what was opened."""

    return _make_agent(llm, tools, prompt, max_iter=8)


# =============================================================================
#  RAG AGENT
# =============================================================================

def build_rag_agent(llm) -> AgentExecutor:
    tools = [search_knowledge_base]
    prompt = """You are RAGAgent — searches the local FAISS knowledge base for indexed documents.

Use search_knowledge_base(query) to find relevant documents.
Summarize results clearly. Cite source filenames.
Say "Not found in knowledge base" if nothing relevant comes back. Never hallucinate."""

    return _make_agent(llm, tools, prompt, max_iter=6)


# =============================================================================
#  UTILITY AGENT
# =============================================================================

def build_utility_agent(llm) -> AgentExecutor:
    tools = [
        get_datetime, take_screenshot, ask_user,
        set_clipboard, keyboard_press, send_desktop_notification,
    ]
    prompt = """You are UtilityAgent — handles time, clarification, clipboard, notifications.

TOOLS:
  get_datetime()                            <- current date and time
  take_screenshot(file_path='')             <- capture screen
  ask_user(question, context)               <- ask user for input/confirmation
  set_clipboard(text)                       <- copy text to clipboard
  keyboard_press(keys)                      <- hotkey (e.g. 'ctrl+v')
  send_desktop_notification(title, message) <- Windows toast notification

For ambiguous requests: use ask_user() to clarify before acting.
Reply briefly with what was done."""

    return _make_agent(llm, tools, prompt, max_iter=8)

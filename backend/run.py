"""Development launcher: `python run.py` — works from any directory.

HOST/PORT come from the environment (defaults 127.0.0.1:8000). Hot reload is
on unless APP_ENV=production.
"""

import os
import socket
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent

# import name -> pip name, for everything app/ imports at startup
RUNTIME_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "email_validator": "email-validator",
    "bcrypt": "bcrypt",
    "jwt": "PyJWT",
    "python_multipart": "python-multipart",  # file uploads (product & avatar photos)
}


def _venv_python_candidates():
    """The project venv lives in backend/venv or at the repo root — both are
    used in the wild (the README suggests the root, a `cd backend` workflow
    tends to create the other), so look in both."""
    for base in (BACKEND_DIR, BACKEND_DIR.parent):
        yield base / "venv" / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )


def preflight() -> None:
    """Fail with a readable message instead of a bare import traceback."""
    import importlib.util

    missing = [pip for mod, pip in RUNTIME_IMPORTS.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return

    print(f"This Python is missing {len(missing)} package(s) the backend needs:")
    print(f"  {', '.join(missing)}")
    print()
    print(f"You are running:  {sys.executable}")
    venv_python = next((p for p in _venv_python_candidates() if p.exists()), None)
    if venv_python:
        print("The project virtual environment already has all of them installed.")
        print("Start the backend with it instead:")
        print()
        print(f'  "{venv_python}" run.py')
        if os.name == "nt":
            print(f"  or activate it first:  {venv_python.parent}\\activate")
        else:
            print(f"  or activate it first:  source {venv_python.parent}/activate")
        # A *different* venv being active is the classic cause of this error —
        # both prompt as "(venv)", so make the mismatch impossible to miss.
        active = os.environ.get("VIRTUAL_ENV")
        if active and venv_python and Path(active).resolve() != venv_python.parent.parent.resolve():
            print()
            print("Note: another virtual environment is active in this shell:")
            print(f"      {active}")
            print("      Run `deactivate` first if you prefer the activate-then-run workflow.")
    else:
        print("Create the virtual environment and install the requirements:")
        print()
        print("  python -m venv venv            (from the backend folder)")
        print("  venv/Scripts/activate          (Windows)")
        print("  source venv/bin/activate       (macOS / Linux)")
        print("  pip install -r requirements.txt")
    sys.exit(1)


def check_port(host: str, port: int) -> None:
    """Refuse to start when something else already owns the port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        print(f"Port {port} is already in use — another server may still be running.")
        print("Stop that process, or start this one on another port:")
        print()
        print("  PowerShell:  $env:PORT = 8001; python run.py")
        print("  bash:        PORT=8001 python run.py")
        sys.exit(1)
    finally:
        probe.close()


if __name__ == "__main__":
    preflight()
    # Anchor every relative path (.env, SQLite, static) to the backend folder,
    # so the launch directory does not matter.
    os.chdir(BACKEND_DIR)

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    check_port(host, port)

    import uvicorn

    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=os.environ.get("APP_ENV", "dev") != "production",
    )

"""Build a Windows GUI executable. Run with the project's .venv Python."""
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("The Windows executable must be built on Windows.")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--windowed", "--name", "OzonePanel", "--collect-all", "customtkinter",
        "--add-data", f"{ROOT / 'config.json'};.",
        "--add-data", f"{ROOT / 'THIRD_PARTY_LICENSES.txt'};.",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "app.py"),
    ], cwd=ROOT, check=True)
    shutil.copy2(ROOT / "操作手册.md", ROOT / "dist" / "操作手册.md")
    shutil.copy2(ROOT / "usage.txt", ROOT / "dist" / "usage.txt")
    print(ROOT / "dist" / "OzonePanel.exe")

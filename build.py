"""Build a Windows GUI executable. Run with the project's .venv Python."""
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "v0.4.0"

if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("The Windows executable must be built on Windows.")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--windowed", "--name", "OzonePanel",
        "--exclude-module", "customtkinter", "--exclude-module", "tkinter",
        "--exclude-module", "cv2", "--exclude-module", "numpy",
        "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
        "--add-data", f"{ROOT / 'config.json'};.",
        "--add-data", f"{ROOT / 'THIRD_PARTY_LICENSES.txt'};.",
        "--distpath", str(OUTPUT), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "app.py"),
    ], cwd=ROOT, check=True)
    shutil.copy2(ROOT / "操作手册.md", OUTPUT / "操作手册.md")
    shutil.copy2(ROOT / "usage.txt", OUTPUT / "usage.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_LICENSES.txt", OUTPUT / "THIRD_PARTY_LICENSES.txt")
    print(OUTPUT / "OzonePanel.exe")

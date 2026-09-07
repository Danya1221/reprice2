import os
import sys

print("▶️ Запуск: main.py", flush=True)
os.execv(sys.executable, [sys.executable, "main.py"])

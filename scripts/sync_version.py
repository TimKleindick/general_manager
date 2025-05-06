#!/usr/bin/env python3
import sys
import tomllib  # für Python 3.11+
import tomli_w  # pip install tomli-w
from pathlib import Path

if len(sys.argv) != 2:
    print("Usage: sync_version.py <new_version>")
    sys.exit(1)

new_version = sys.argv[1]
pyproject = Path("pyproject.toml")

# 1) Datei als String lesen
content = pyproject.read_text(encoding="utf-8")

# 2) TOML parsen
data = tomllib.loads(content)

# 3) Version anpassen
data["project"]["version"] = new_version

# 4) neue TOML serialisieren und schreiben
pyproject.write_text(tomli_w.dumps(data), encoding="utf-8")

print(f"pyproject.toml → version = {new_version}")

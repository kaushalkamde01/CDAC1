#!/usr/bin/env python3
"""
Inserts the Winlogbeat filter branch into 01-ingest.conf, placed
immediately before the final catch-all block:  else if ![event][dataset] {

Usage (run from ~/project on SOC-Core):
    python3 insert_winlogbeat_branch.py
"""
import sys
from datetime import datetime

TARGET = "logstash/pipeline/01-ingest.conf"
SNIPPET = "winlogbeat_branch.conf"  # place alongside this script
MARKER = " else if ![event][dataset] {"

with open(TARGET) as f:
    content = f.read()

if MARKER not in content:
    print(f"ERROR: marker not found in {TARGET} — file may have changed. Aborting, nothing written.")
    sys.exit(1)

if "winlogbeat" in content:
    print("WARNING: the word 'winlogbeat' already appears in this file.")
    print("This may mean the branch was already inserted. Aborting to avoid a duplicate.")
    print("Inspect the file manually if you want to proceed anyway.")
    sys.exit(1)

with open(SNIPPET) as f:
    snippet = f.read()

backup_path = f"{TARGET}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
with open(backup_path, "w") as f:
    f.write(content)
print(f"Backup written: {backup_path}")

new_content = content.replace(MARKER, snippet + "\n" + MARKER, 1)

open_braces = new_content.count("{")
close_braces = new_content.count("}")
if open_braces != close_braces:
    print(f"ERROR: brace mismatch after insertion ({{={open_braces}, }}={close_braces}). Not writing file.")
    sys.exit(1)

with open(TARGET, "w") as f:
    f.write(new_content)

print(f"Inserted successfully into {TARGET}. Braces balanced ({open_braces}/{close_braces}).")

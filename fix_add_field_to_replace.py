#!/usr/bin/env python3
"""
Within the winlogbeat filter block only (between the two markers below),
replace 'add_field' with 'replace' so our event.* enrichment always wins
deterministically, instead of merging into an array when Winlogbeat has
already pre-populated a field.
"""
import sys
from datetime import datetime

TARGET = "logstash/pipeline/01-ingest.conf"
START_MARKER = ' else if [agent][type] == "winlogbeat" {'
END_MARKER = " else if ![event][dataset] {"

with open(TARGET) as f:
    content = f.read()

start = content.find(START_MARKER)
end = content.find(END_MARKER, start)

if start == -1 or end == -1:
    print("ERROR: could not locate the winlogbeat block markers. Aborting, nothing written.")
    sys.exit(1)

block = content[start:end]
fixed_block = block.replace("add_field", "replace")
n_changed = block.count("add_field")

backup_path = f"{TARGET}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
with open(backup_path, "w") as f:
    f.write(content)
print(f"Backup written: {backup_path}")

new_content = content[:start] + fixed_block + content[end:]

if new_content.count("{") != new_content.count("}"):
    print("ERROR: brace mismatch after edit. Not writing file.")
    sys.exit(1)

with open(TARGET, "w") as f:
    f.write(new_content)

print(f"Replaced {n_changed} occurrences of add_field -> replace within the winlogbeat block.")

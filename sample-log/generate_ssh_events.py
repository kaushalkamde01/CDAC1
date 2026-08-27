import json
import random
from datetime import datetime, timedelta, timezone

source_ips = [
    "203.0.113.10",
    "198.51.100.25",
    "192.0.2.55",
    "45.33.32.156",
    "185.220.101.42",
]

usernames = [
    "root",
    "admin",
    "ubuntu",
    "oracle",
    "test",
    "postgres",
]

hosts = [
    "vm-services-01",
    "web-server-01",
    "soc-client-01",
]

start_time = datetime.now(timezone.utc) - timedelta(hours=6)

for i in range(120):
    event_time = start_time + timedelta(minutes=i * 3)

    source_ip = random.choice(source_ips)
    username = random.choice(usernames)
    host = random.choice(hosts)
    source_port = random.randint(32000, 65000)

    event = {
        "@timestamp": event_time.isoformat().replace("+00:00", "Z"),
        "event": {
            "dataset": "system.auth",
            "category": "authentication",
            "type": "start",
            "outcome": "failure",
            "action": "ssh_login"
        },
        "service": {
            "name": "ssh"
        },
        "source": {
            "ip": source_ip,
            "port": source_port
        },
        "user": {
            "name": username
        },
        "network": {
            "transport": "tcp"
        },
        "host": {
            "name": host
        },
        "message": (
            f"Failed password for invalid user {username} "
            f"from {source_ip} port {source_port} ssh2"
        )
    }

    print(json.dumps(event))

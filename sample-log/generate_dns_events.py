import json
import random
from datetime import datetime, timedelta, timezone

domains = [
    "google.com",
    "github.com",
    "microsoft.com",
    "elastic.co",
    "ubuntu.com",
    "cdn.example.com",
    "api.example.com",
    "updates.example.com",
    "malware-control.example",
    "suspicious-domain.example",
]

query_types = ["a", "aaaa", "mx", "txt", "cname"]

source_ips = [
    "10.0.0.15",
    "10.0.0.20",
    "192.168.100.40",
    "192.168.100.137",
    "192.168.205.130",
]

hosts = [
    "vm-services-01",
    "web-server-01",
    "soc-client-01",
]

start_time = datetime.now(timezone.utc) - timedelta(hours=6)

for i in range(150):
    event_time = start_time + timedelta(minutes=i * 2)

    domain = random.choice(domains)
    query_type = random.choice(query_types)
    source_ip = random.choice(source_ips)
    source_port = random.randint(1024, 65535)
    host = random.choice(hosts)

    event = {
        "@timestamp": event_time.isoformat().replace("+00:00", "Z"),
        "event": {
            "dataset": "dns.query",
            "category": "network",
            "type": "protocol",
            "action": "dns_query"
        },
        "network": {
            "protocol": "dns",
            "transport": "udp"
        },
        "source": {
            "ip": source_ip,
            "port": source_port
        },
        "dns": {
            "question": {
                "name": domain,
                "type": query_type
            }
        },
        "host": {
            "name": host
        },
        "message": (
            f"DNS query from {source_ip}:{source_port} "
            f"for {domain} type {query_type.upper()}"
        )
    }

    print(json.dumps(event))

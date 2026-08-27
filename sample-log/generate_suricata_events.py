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

destination_ips = [
    "10.0.0.10",
    "10.0.0.20",
    "192.168.100.40",
    "192.168.100.137",
]

alerts = [
    {
        "signature": "ET SCAN Nmap Scripting Engine User-Agent Detected",
        "category": "Attempted Information Leak",
        "severity": 2,
        "action": "port_scan",
    },
    {
        "signature": "ET WEB_SERVER Possible SQL Injection Attempt",
        "category": "Web Application Attack",
        "severity": 1,
        "action": "sql_injection_attempt",
    },
    {
        "signature": "ET POLICY SSH Brute Force Attempt",
        "category": "Potentially Bad Traffic",
        "severity": 2,
        "action": "ssh_brute_force",
    },
    {
        "signature": "ET MALWARE Possible Command and Control Traffic",
        "category": "A Network Trojan was detected",
        "severity": 1,
        "action": "c2_communication",
    },
    {
        "signature": "ET DNS Suspicious DNS Query",
        "category": "Potentially Bad Traffic",
        "severity": 2,
        "action": "suspicious_dns_query",
    },
]

protocols = ["TCP", "UDP"]
start_time = datetime.now(timezone.utc) - timedelta(hours=6)

for i in range(100):
    event_time = start_time + timedelta(minutes=i * 3)

    source_ip = random.choice(source_ips)
    destination_ip = random.choice(destination_ips)
    alert = random.choice(alerts)
    protocol = random.choice(protocols)

    source_port = random.randint(1024, 65535)
    destination_port = random.choice([22, 53, 80, 443, 3306, 8080])

    event = {
        "@timestamp": event_time.isoformat().replace("+00:00", "Z"),
        "event": {
            "dataset": "suricata.eve",
            "category": "network",
            "type": "alert",
            "kind": "alert",
            "action": alert["action"],
            "severity": alert["severity"],
        },
        "service": {
            "name": "suricata"
        },
        "source": {
            "ip": source_ip,
            "port": source_port,
        },
        "destination": {
            "ip": destination_ip,
            "port": destination_port,
        },
        "network": {
            "transport": protocol.lower(),
            "protocol": protocol.lower(),
            "direction": "inbound",
        },
        "rule": {
            "name": alert["signature"],
            "category": alert["category"],
            "id": str(2100000 + i),
        },
        "suricata": {
            "eve": {
                "alert": {
                    "signature": alert["signature"],
                    "category": alert["category"],
                    "severity": alert["severity"],
                }
            }
        },
        "host": {
            "name": "vm-ids-01"
        },
        "message": (
            f'{alert["signature"]}: {source_ip}:{source_port} '
            f'-> {destination_ip}:{destination_port}'
        ),
    }

    print(json.dumps(event))

import json
import random
from datetime import datetime, timedelta, timezone

source_ips = [
    "203.0.113.10",
    "198.51.100.25",
    "192.0.2.55",
    "45.33.32.156",
    "185.220.101.42",
    "10.0.0.15",
]

urls = [
    "/",
    "/login",
    "/dashboard",
    "/api/users",
    "/api/orders",
    "/admin",
    "/wp-admin",
    "/phpmyadmin",
    "/.env",
    "/../../etc/passwd",
]

methods = ["GET", "POST", "PUT", "DELETE"]

status_codes = [200, 200, 200, 201, 301, 400, 401, 403, 404, 500]

user_agents = [
    "Mozilla/5.0",
    "curl/8.0",
    "python-requests/2.31",
    "Nikto/2.5",
    "sqlmap/1.8",
]

hosts = [
    "web-server-01",
    "web-server-02",
]

start_time = datetime.now(timezone.utc) - timedelta(hours=6)

for i in range(180):
    event_time = start_time + timedelta(minutes=i * 2)

    source_ip = random.choice(source_ips)
    url = random.choice(urls)
    method = random.choice(methods)
    status = random.choice(status_codes)
    user_agent = random.choice(user_agents)
    host = random.choice(hosts)

    response_size = random.randint(150, 15000)
    duration = round(random.uniform(0.01, 2.5), 3)

    event = {
        "@timestamp": event_time.isoformat().replace("+00:00", "Z"),
        "event": {
            "dataset": "nginx.access",
            "category": "web",
            "type": "access",
            "action": "http_request",
            "outcome": "success" if status < 400 else "failure",
            "duration": int(duration * 1_000_000_000)
        },
        "service": {
            "name": "nginx"
        },
        "source": {
            "ip": source_ip
        },
        "http": {
            "request": {
                "method": method
            },
            "response": {
                "status_code": status,
                "body": {
                    "bytes": response_size
                }
            }
        },
        "url": {
            "original": url,
            "path": url
        },
        "user_agent": {
            "original": user_agent
        },
        "host": {
            "name": host
        },
        "message": (
            f'{source_ip} - - "{method} {url} HTTP/1.1" '
            f'{status} {response_size} "-" "{user_agent}"'
        )
    }

    if url in ["/admin", "/wp-admin", "/phpmyadmin", "/.env", "/../../etc/passwd"]:
        event["event"]["type"] = "indicator"
        event["event"]["action"] = "sensitive_path_scan"

    print(json.dumps(event))

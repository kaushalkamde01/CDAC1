import json
import random
from datetime import datetime, timedelta, timezone

pipelines = [
    "web-app-build",
    "api-service-build",
    "security-scan",
    "production-deployment",
]

stages = [
    ("checkout", "success"),
    ("build", "success"),
    ("unit_test", "success"),
    ("security_scan", "success"),
    ("security_scan", "failure"),
    ("deploy", "success"),
    ("deploy", "failure"),
]

repositories = [
    "soc-web-application",
    "threatops-api",
    "infrastructure-code",
]

branches = [
    "main",
    "develop",
    "feature/security-dashboard",
]

hosts = [
    "jenkins-server-01",
    "gitlab-runner-01",
]

start_time = datetime.now(timezone.utc) - timedelta(hours=6)

for i in range(60):
    event_time = start_time + timedelta(minutes=i * 5)

    pipeline = random.choice(pipelines)
    stage, outcome = random.choice(stages)
    repository = random.choice(repositories)
    branch = random.choice(branches)
    host = random.choice(hosts)

    duration_seconds = random.randint(15, 600)

    event = {
        "@timestamp": event_time.isoformat().replace("+00:00", "Z"),
        "event": {
            "dataset": "cicd.pipeline",
            "category": "configuration",
            "type": "info",
            "action": stage,
            "outcome": outcome,
            "duration": duration_seconds * 1_000_000_000
        },
        "service": {
            "name": "cicd"
        },
        "ci": {
            "pipeline": {
                "name": pipeline,
                "id": f"pipeline-{1000 + i}"
            },
            "stage": {
                "name": stage
            }
        },
        "repository": {
            "name": repository,
            "branch": branch
        },
        "host": {
            "name": host
        },
        "message": (
            f"Pipeline {pipeline} stage {stage} completed "
            f"with outcome {outcome}"
        )
    }

    print(json.dumps(event))

# 🛡️ MISP Threat Intelligence Platform — Deployment & Integration Guide

> Self-hosted MISP on Docker Compose, hardened with custom TLS, and integrated with Logstash + Elasticsearch + Kibana for automated threat intelligence ingestion and detection.

---

## 📌 Placeholder Reference

Before you begin, replace every placeholder below with your own environment-specific values. **Never commit real IPs, hostnames, passwords, or API keys to source control.**

| Placeholder | Description |
|---|---|
| `<MISP_SERVER_IP>` | IP address of the MISP host (e.g. the Ubuntu VM) |
| `<MISP_DOMAIN>` | Internal DNS name for MISP (e.g. `misp.yourdomain.local`) |
| `<SOC_SERVER_IP>` | IP address of the SOC / Elastic stack server |
| `<SOC_USER>` | SSH user on the SOC server |
| `<DB_NAME>` | MISP MariaDB database name |
| `<DB_USER>` | MariaDB application user |
| `<DB_PASSWORD>` | MariaDB application user password |
| `<DB_ROOT_PASSWORD>` | MariaDB root password |
| `<REDIS_PASSWORD>` | Redis password |
| `<MISP_API_KEY>` | MISP administrator REST API key |
| `<TRUSTSTORE_PASSWORD>` | Java truststore password |
| `<ELASTIC_PASSWORD>` | Elasticsearch `elastic` user password |
| `<ES_HOST>` | Elasticsearch host/URL reachable from Logstash |

---

## 🗺️ Overview

```
 MISP (Docker: core + MariaDB + Redis)
          │
          │  HTTPS (custom TLS cert, SAN-verified)
          ▼
 Logstash HTTP Poller  ──► Normalize / Enrich (ECS fields)
          │
          ▼
   Elasticsearch  ──►  misp-ioc-intelligence
                  ──►  misp-threat-intelligence
          │
          ▼
   Kibana Detection Engine ──► Security Alerts
```

This guide is split into four phases:

- [x] **Phase 1** — MISP Deployment & Initial Configuration
- [x] **Phase 2** — DNS Configuration & SSL Certificate Hardening
- [x] **Phase 3** — Java Truststore & Logstash–MISP Integration
- [x] **Phase 4** — Threat Feed Integration & Detection Rule Development

---

## ✅ Prerequisites

- [ ] Dedicated Ubuntu VM/server for MISP
- [ ] A second server (SOC/Elastic stack) with Logstash, Elasticsearch, and Kibana already running
- [ ] Root/sudo access on both servers
- [ ] Internal DNS control (to create an A record for MISP)
- [ ] Network connectivity between the MISP server and the SOC server

---

<details>
<summary><h2>🚀 Phase 1: MISP Deployment and Initial Configuration</h2></summary>

**Objective:** Deploy a self-hosted MISP instance using Docker Compose to serve as the centralized Threat Intelligence Platform.

### Step 1 — Prepare the Ubuntu Server
```bash
sudo apt update
sudo apt upgrade -y
```

### Step 2 — Install Docker
```bash
sudo apt install docker.io -y
sudo systemctl enable docker
sudo systemctl start docker

# Verify
docker --version
docker ps
```

### Step 3 — Install Docker Compose
```bash
sudo apt install docker-compose-plugin -y

# Verify
docker compose version
```

### Step 4 — Create Project Directory
```bash
mkdir ~/misp-docker
cd ~/misp-docker
```

### Step 5 — Create the Docker Compose Configuration
```bash
nano docker-compose.yml
```

```yaml
services:
  db:
    image: mariadb:10.11
    container_name: misp-db
    restart: unless-stopped
    environment:
      MYSQL_DATABASE: <DB_NAME>
      MYSQL_USER: <DB_USER>
      MYSQL_PASSWORD: <DB_PASSWORD>
      MYSQL_ROOT_PASSWORD: <DB_ROOT_PASSWORD>
    volumes:
      - mariadb_data:/var/lib/mysql
    networks:
      - misp-network

  redis:
    image: redis:7
    container_name: misp-redis
    restart: unless-stopped
    command: redis-server --requirepass <REDIS_PASSWORD>
    volumes:
      - redis_data:/data
    networks:
      - misp-network

  misp-core:
    image: ghcr.io/misp/misp-docker/misp-core:latest
    container_name: misp-core
    restart: unless-stopped
    depends_on:
      - db
      - redis
    environment:
      MYSQL_HOST: db
      MYSQL_DATABASE: <DB_NAME>
      MYSQL_USER: <DB_USER>
      MYSQL_PASSWORD: <DB_PASSWORD>
      REDIS_FQDN: redis
      REDIS_PASSWORD: <REDIS_PASSWORD>
      BASE_URL: https://<MISP_SERVER_IP>
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - misp_files:/var/www/MISP/app/files
      - misp_config:/var/www/MISP/app/Config
    networks:
      - misp-network

volumes:
  mariadb_data:
  redis_data:
  misp_files:
  misp_config:

networks:
  misp-network:
    driver: bridge
```

### Step 6 — Deploy the Containers
```bash
docker compose up -d

# Verify
docker ps
```
Expected containers: `misp-core`, `misp-db`, `misp-redis`

### Step 7 — Verify Container Health
```bash
docker ps
docker logs misp-core
docker logs misp-db
docker logs misp-redis
```

### Step 8 — Verify the Web Interface
Open in a browser:
```
https://<MISP_SERVER_IP>
```
Confirm the login page loads successfully.

### Step 9 — Verify Internal Services
```bash
docker exec -it misp-core bash
nginx -T | grep ssl_certificate
```
Expected:
```
ssl_certificate /etc/nginx/certs/cert.pem;
ssl_certificate_key /etc/nginx/certs/key.pem;
```
```bash
exit
```

### Step 10 — Verify Persistent Volumes
```bash
docker volume ls
```
Expected: `mariadb_data`, `redis_data`, `misp_files`, `misp_config`

### Step 11 — Verify the Docker Network
```bash
docker network ls
docker network inspect misp-network
```
Confirm all three containers share the same bridge network.

### Step 12 — Obtain the API Authentication Key
In the MISP web UI:
```
Administration → List Users → Admin User → Auth Key
```
Store it locally:
```bash
echo "<MISP_API_KEY>" > admin-auth.key
cat admin-auth.key
```

### Step 13 — Initial API Verification
```bash
curl -k \
  -X POST \
  -H "Authorization: <MISP_API_KEY>" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "returnFormat":"json",
    "limit":1
  }' \
  https://<MISP_SERVER_IP>/attributes/restSearch
```
A successful response confirms the REST API is operational, authentication is valid, and MISP is serving data.

### 📦 Components Deployed
| Component | Image | Purpose |
|---|---|---|
| MISP Core | `ghcr.io/misp/misp-docker/misp-core:latest` | Web UI & Threat Intelligence Platform |
| MariaDB | `mariadb:10.11` | Stores events, attributes, users, metadata |
| Redis | `redis:7` | Session management, caching, job queue |

### Phase 1 Checklist
- [ ] Ubuntu server prepared
- [ ] Docker Engine installed
- [ ] Docker Compose installed
- [ ] MISP project directory created
- [ ] Docker Compose deployment completed
- [ ] MISP Core, MariaDB, Redis containers deployed
- [ ] Persistent Docker volumes configured
- [ ] Docker bridge network configured
- [ ] MISP web interface verified
- [ ] Administrator API key generated and stored
- [ ] REST API connectivity verified via curl

</details>

---

<details>
<summary><h2>🔒 Phase 2: DNS Configuration and SSL Certificate Hardening</h2></summary>

**Objective:** Assign a permanent DNS name to MISP, replace the default self-signed certificate with one containing correct SAN entries, and prepare for secure HTTPS with Logstash.

### Step 1 — Configure Internal DNS
Create a DNS A record:
```
Hostname: <MISP_DOMAIN>
Mapped to: <MISP_SERVER_IP>
```
Verify resolution:
```bash
host -t a <MISP_DOMAIN>
```
Expected:
```
<MISP_DOMAIN> has address <MISP_SERVER_IP>
```

### Step 2 — Update the MISP Base URL
```bash
cd ~/misp-docker
nano docker-compose.yml
```
Change:
```yaml
BASE_URL: https://<MISP_DOMAIN>
```

### Step 3 — Create a Certificate Configuration File
```bash
cat > misp-cert.cnf <<EOF
[req]
default_bits = 2048
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = <MISP_DOMAIN>

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = <MISP_DOMAIN>
DNS.2 = localhost
IP.1  = <MISP_SERVER_IP>
IP.2  = 127.0.0.1
EOF
```

### Step 4 — Generate a New Self-Signed Certificate
```bash
rm -f cert.pem key.pem

openssl req \
  -x509 \
  -nodes \
  -days 365 \
  -newkey rsa:2048 \
  -keyout key.pem \
  -out cert.pem \
  -config misp-cert.cnf
```

### Step 5 — Verify Certificate Contents
```bash
openssl x509 -in cert.pem -text -noout | grep -A2 "Subject Alternative"
```
Expected:
```
DNS:<MISP_DOMAIN>
DNS:localhost
IP Address:<MISP_SERVER_IP>
IP Address:127.0.0.1
```

### Step 6 — Create a Dedicated Certificates Directory
```bash
mkdir certs
mv cert.pem certs/
mv key.pem certs/
```

Project structure:
```
misp-docker/
│
├── certs/
│   ├── cert.pem
│   └── key.pem
│
├── docker-compose.yml
├── admin-auth.key
└── misp-cert.cnf
```

### Step 7 — Mount Certificates into the Container
```yaml
volumes:
  - misp_files:/var/www/MISP/app/files
  - misp_config:/var/www/MISP/app/Config
  - ./certs/cert.pem:/etc/nginx/certs/cert.pem:ro
  - ./certs/key.pem:/etc/nginx/certs/key.pem:ro
```

### Step 8 — Recreate the Containers
```bash
docker compose down
docker compose up -d
docker ps
```

### Step 9 — Verify Mounted Certificates
```bash
docker exec -it misp-core bash
ls -l /etc/nginx/certs/
openssl x509 -in /etc/nginx/certs/cert.pem -text -noout | grep -A2 "Subject Alternative"
exit
```

### Step 10 — Verify the HTTPS Endpoint
```bash
curl -k https://<MISP_DOMAIN>
```
or open `https://<MISP_DOMAIN>` in a browser.

### Step 11 — Verify the REST API over HTTPS
```bash
curl -k \
  -X POST \
  -H "Authorization: <MISP_API_KEY>" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "returnFormat":"json",
    "type":["ip-dst"],
    "limit":1
  }' \
  https://<MISP_DOMAIN>/attributes/restSearch
```

### Phase 2 Checklist
- [ ] Internal DNS record configured
- [ ] `BASE_URL` updated to DNS hostname
- [ ] Custom OpenSSL config created
- [ ] Self-signed TLS certificate generated with correct SAN entries
- [ ] Dedicated `certs/` directory created
- [ ] Certificate and key mounted into the MISP container
- [ ] Containers recreated
- [ ] Active certificate verified inside the container
- [ ] HTTPS access validated via DNS hostname
- [ ] REST API connectivity confirmed over HTTPS

</details>

---

<details>
<summary><h2>🔗 Phase 3: Java Truststore & Logstash–MISP Integration</h2></summary>

**Objective:** Enable Logstash to securely communicate with the MISP REST API over HTTPS by trusting the MISP certificate, then ingest MISP data into Elasticsearch.

### Step 1 — Copy the MISP Certificate to the SOC Server
```bash
scp cert.pem <SOC_USER>@<SOC_SERVER_IP>:~/project/certs/

# On the SOC server
cd ~/project/certs
ls
```

### Step 2 — Install the Java Runtime
```bash
sudo apt update
sudo apt install openjdk-17-jre-headless -y
keytool -help
```

### Step 3 — Create a Java Truststore
```bash
keytool -importcert \
  -alias misp-cert \
  -file cert.pem \
  -keystore truststore.jks
```
When prompted for a password, use `<TRUSTSTORE_PASSWORD>`. Confirm "Trust this certificate?" → `yes`.

### Step 4 — Verify the Truststore
```bash
keytool -list -keystore truststore.jks
```
Expected: `Your keystore contains: misp-cert`

```bash
keytool -list -v -alias misp-cert -keystore truststore.jks
```
Verify Owner = `CN=<MISP_DOMAIN>` and SAN entries match Phase 2.

### Step 5 — Configure Logstash to Use the Truststore
```yaml
environment:
  LS_JAVA_OPTS: >
    -Djavax.net.ssl.trustStore=/usr/share/logstash/config/certs/truststore.jks
    -Djavax.net.ssl.trustStorePassword=<TRUSTSTORE_PASSWORD>
volumes:
  - ./certs:/usr/share/logstash/config/certs:ro
```

### Step 6 — Configure the HTTP Poller Pipeline (IOC Feed)
Create `05-misp-ioc.conf`:
```
input {
  http_poller {
    urls => {
      misp_iocs => {
        method => post
        url => "https://<MISP_DOMAIN>/attributes/restSearch"
        headers => {
          Authorization => "<MISP_API_KEY>"
          Accept => "application/json"
          "Content-Type" => "application/json"
        }
        body => '{
          "returnFormat":"json",
          "type":["ip-dst","domain","url"],
          "limit":100
        }'
      }
    }
    schedule => { cron => "* * * * *" }
    codec => json
  }
}
```
Polls every minute for domains, URLs, and destination IPs.

### Step 7 — Parse Returned Attributes
```
filter {
  split {
    field => "[response][Attribute]"
  }

  mutate {
    rename => {
      "[response][Attribute][type]"  => "[threat][indicator][type]"
      "[response][Attribute][value]" => "[threat][indicator][value]"
      "[response][Attribute][uuid]"  => "[threat][indicator][reference]"
    }
    add_field => {
      "[event][dataset]"             => "misp.ioc"
      "[event][kind]"                => "enrichment"
      "[event][category]"            => "threat"
      "[threat][indicator][provider]"=> "MISP"
    }
    lowercase => [
      "[threat][indicator][type]",
      "[threat][indicator][value]"
    ]
  }
}
```

### Step 8 — Output to Elasticsearch
```
output {
  elasticsearch {
    hosts => ["<ES_HOST>"]
    index => "misp-ioc-intelligence"
    user => "elastic"
    password => "${ELASTIC_PASSWORD}"
  }

  stdout {
    codec => rubydebug
  }
}
```

### Step 9 — Restart Logstash
```bash
docker restart logstash
```

### Step 10 — Initial SSL Troubleshooting
**Error encountered:**
```
Certificate for <<MISP_DOMAIN>> doesn't match any of the subject alternative names
```
**Root cause:** the default MISP certificate only contained `localhost` and `127.0.0.1` as SANs.

**Resolution:**
1. Regenerated the certificate with the correct SAN entries (Phase 2).
2. Mounted the new certificate into the MISP container.
3. Created a new Java truststore.
4. Imported the updated certificate.
5. Restarted Logstash.

HTTPS communication succeeded afterward.

### Step 11 — Validate the MISP REST API
```bash
curl \
  -X POST \
  -H "Authorization: <MISP_API_KEY>" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "returnFormat":"json",
    "type":["ip-dst"],
    "limit":1
  }' \
  https://<MISP_DOMAIN>/attributes/restSearch
```

### Step 12 — Verify Elasticsearch Ingestion
```bash
curl -u elastic:<ELASTIC_PASSWORD> \
  <ES_HOST>/misp-ioc-intelligence/_count?pretty

curl -u elastic:<ELASTIC_PASSWORD> \
  -X GET "<ES_HOST>/misp-ioc-intelligence/_search?pretty"
```
Verify fields: `threat.indicator.type`, `threat.indicator.value`, `threat.indicator.reference`, `threat.indicator.provider`, `event.dataset`, `event.kind`, `event.category`.

### Step 13 — Confirm Continuous Synchronization
```
MISP → REST API (/attributes/restSearch) → Logstash HTTP Poller
     → Filter & Normalize → Elasticsearch → IOC Intelligence Index
```
The poller runs automatically every minute.

### Phase 3 Checklist
- [ ] Java runtime and `keytool` installed
- [ ] MISP TLS certificate copied to the SOC server
- [ ] Java truststore (`truststore.jks`) created
- [ ] Logstash configured to trust the truststore
- [ ] `http_poller` pipeline implemented (1-minute schedule)
- [ ] IOC data parsed, normalized, and enriched
- [ ] Data indexed into `misp-ioc-intelligence`
- [ ] SSL SAN mismatch resolved
- [ ] Secure API communication and ingestion verified

</details>

---

<details>
<summary><h2>🎯 Phase 4: Threat Feed Integration & Detection Rule Development</h2></summary>

**Objective:** Ingest complete MISP event data, normalize it, build Elastic Detection Rules, and validate alerting.

### Step 1 — Create a Dedicated Threat Feed Pipeline
File: `06-misp-threat.conf`

### Step 2 — Configure the HTTP Poller
```
input {
  http_poller {
    urls {
      misp_events {
        method => post
        url => "https://<MISP_DOMAIN>/events/restSearch"
        headers {
          Authorization => "<MISP_API_KEY>"
          Accept => "application/json"
          "Content-Type" => "application/json"
        }
        body => '{
          "returnFormat":"json"
        }'
      }
    }
    schedule => { cron => "* * * * *" }
    codec => json
  }
}
```
Synchronizes complete MISP events every minute.

### Step 3 — Parse MISP Events
Extract from the nested event object:
- Event ID
- Event UUID
- Event Information
- Threat Level
- Analysis Status
- Distribution
- Publish Timestamp
- Source Organization
- Event Date

### Step 4 — Normalize Threat Metadata
Fields indexed: `info`, `source`, `threat_level_id`, `publish_timestamp`, `distribution`, `analysis`, `event_id`, `uuid`

### Step 5 — Create the Threat Intelligence Index
```
misp-threat-intelligence
```
Kept separate from the IOC index:
```
MISP
 ├──► IOC Pipeline ──► misp-ioc-intelligence
 └──► Threat Feed Pipeline ──► misp-threat-intelligence
```

### Step 6 — Validate Data Ingestion
```bash
curl -u elastic:<ELASTIC_PASSWORD> \
  <ES_HOST>/misp-threat-intelligence/_count?pretty

curl -u elastic:<ELASTIC_PASSWORD> \
  -X GET "<ES_HOST>/misp-threat-intelligence/_search?pretty"
```
Verify fields: `info`, `source`, `publish_timestamp`, `threat_level_id`, `@timestamp`.

### Step 7 — Inspect the Index Mapping
```bash
curl -u elastic:<ELASTIC_PASSWORD> \
  <ES_HOST>/misp-threat-intelligence/_mapping?pretty
```
Key observations:
- `@timestamp` mapped as a **date**
- `publish_timestamp` stored as **text**
- `threat_level_id` stored as **text**

These mappings shape how detection rule queries must be written.

### Step 8 — Develop Detection Rules

| Rule | Purpose | Primary Fields | Severity | Risk Score |
|---|---|---|---|---|
| **Published Critical MISP Threat** | Detect newly ingested events with the highest threat severity | `source = MISP`, `threat_level_id = 1` | Critical | 99 |
| **Critical MISP Threat Intelligence** | Alert on all critical MISP intelligence events | `source = MISP`, `threat_level_id = 1` | High | 90 |
| **New MISP Threat Intelligence** | Identify newly synchronized MISP events | `source = MISP`, time filter on `@timestamp` | Medium | 49 |

> ⚠️ Rule 3 uses the Elasticsearch **ingestion timestamp** (`@timestamp`) instead of `publish_timestamp`, because `publish_timestamp` is indexed as text and unsuitable for time-range queries.

### Step 9 — Rule Testing
Search by threat level:
```json
{
  "query": {
    "term": { "threat_level_id.keyword": "1" }
  }
}
```
Search by source:
```json
{
  "query": {
    "term": { "source.keyword": "MISP" }
  }
}
```
Inspect recent documents:
```json
{
  "sort": { "@timestamp": "desc" }
}
```

### Step 10 — Rule Validation in Kibana
After enabling the rules, confirm the Kibana Detection Engine generates alerts for:
- New MISP threat events
- Critical threat intelligence
- Published critical threat intelligence

Monitor execution status on the **Detection Rules** dashboard.

### Step 11 — IOC Correlation Rule Evaluation
Intended workflow:
```
Network Logs → Destination IP / Domain / URL
            → Elastic Detection Rule
            → IOC Match (against misp-ioc-intelligence)
            → Critical Alert
```
> 📝 Note: Reliable end-to-end testing requires properly generated Suricata network events. Since Suricata validation was out of scope for this implementation, full functional testing of the IOC correlation rule was **deferred**, while the ingestion pipeline itself was confirmed operational.

### Step 12 — End-to-End Threat Intelligence Workflow
```
MISP Threat Events
        │
        ▼
   MISP REST API
        │
        ▼
Logstash HTTP Poller
        │
        ▼
   Normalization
        │
        ▼
   Elasticsearch
   ┌─────────────────────────────┐
   │ misp-ioc-intelligence       │  (IOC Indicators)
   ├─────────────────────────────┤
   │ misp-threat-intelligence    │  (Threat Feed Events)
   └─────────────────────────────┘
        │
        ▼
Elastic Detection Rules
        │
        ▼
Security Alerts Dashboard
```

### Phase 4 Checklist
- [ ] Dedicated Logstash pipeline for full MISP events implemented
- [ ] Event metadata parsed and normalized into `misp-threat-intelligence`
- [ ] Indexed documents and field mappings validated
- [ ] Detection Rule 1 — Published Critical MISP Threat — created
- [ ] Detection Rule 2 — Critical MISP Threat Intelligence — created
- [ ] Detection Rule 3 — New MISP Threat Intelligence — created
- [ ] Rules validated via Elasticsearch queries and Kibana Detection Engine
- [ ] IOC correlation rule design evaluated (full validation deferred pending Suricata traffic)
- [ ] End-to-end workflow established: MISP → Logstash → Elasticsearch → Alerts

</details>

---

## 🧰 Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Certificate for <domain> doesn't match any of the subject alternative names` | Certificate missing correct SAN entries | Regenerate cert with SANs for domain + IP, remount, rebuild truststore, restart Logstash |
| REST API returns 401/403 | Invalid or expired `<MISP_API_KEY>` | Regenerate the auth key via Administration → List Users → Auth Key |
| No documents in Elasticsearch index | Pipeline misconfigured or Logstash not reloaded | Check `docker logs logstash`, verify `http_poller` URL/cron, restart Logstash |
| Detection rule not firing on time range | Field indexed as text instead of date | Use `@timestamp` (ingestion time) rather than text-based fields like `publish_timestamp` |
| `docker ps` missing a container | Compose file error or dependency failure | Run `docker compose logs <service>` to inspect startup errors |

---

## 📁 Final Project Structure

```
misp-docker/
│
├── certs/
│   ├── cert.pem
│   └── key.pem
│
├── docker-compose.yml
├── admin-auth.key
└── misp-cert.cnf

project/ (SOC server)
├── certs/
│   ├── cert.pem
│   └── truststore.jks
├── 05-misp-ioc.conf
└── 06-misp-threat.conf
```

---

## 🏁 Summary

| Phase | Outcome |
|---|---|
| 1 | MISP deployed via Docker Compose with persistent storage and verified REST API |
| 2 | DNS + SAN-correct TLS certificate hardening MISP's HTTPS endpoint |
| 3 | Logstash trusts MISP's certificate and ingests IOC attributes into Elasticsearch |
| 4 | Full threat event ingestion, ECS-aligned normalization, and Elastic Detection Rules generating alerts |

> Replace every `<PLACEHOLDER>` with your actual environment values before running any command. Store all credentials (`<MISP_API_KEY>`, `<DB_PASSWORD>`, `<ELASTIC_PASSWORD>`, `<TRUSTSTORE_PASSWORD>`, etc.) in a secrets manager or `.env` file excluded from version control — never hard-code them into shared documentation or committed configs.

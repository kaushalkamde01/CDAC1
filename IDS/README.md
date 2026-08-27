# README — SOC IDS/ELK Stack: Working Configuration & Commands

This is an operational runbook of every working command/config used to build the current setup. For incident history and rule-test status, see `full-project-report.md`. This file is command-focused — copy/paste reference only.

---

## 1. Topology

```
EC2 WireGuard Hub (172.31.8.83 / 13.205.137.92)
  wg0: 192.168.100.1, 192.168.150.1, 192.168.200.1
       │
       │ tc clsact mirror → GRE tunnel (gre1)
       ▼
VM-IDS-01 (192.168.150.30)
  gre1 (receives mirrored traffic) → Suricata → eve.json
  → Filebeat → Logstash → Kafka → Logstash → Elasticsearch → Kibana
  (Docker Compose stack, all services on this host)

VM-SERVICES-01 (192.168.100.10) → Filebeat ships /var/log/auth.log directly to Logstash
VM-MISP-01 (192.168.150.10) → MISP server, polled by Logstash HTTP pollers
```

---

## 2. Suricata (VM-IDS-01)

### Config
`/etc/suricata/suricata.yaml`:
```yaml
af-packet:
  - interface: gre1        # was wg0 originally, changed after mirroring setup
```

### Custom rules
`/var/lib/suricata/rules/local.rules`:
```
# alert icmp any any -> any any (msg:"ICMP Test Alert"; sid:9000001; rev:1;)
# alert icmp any any -> any any (msg:"HIGH SEVERITY Test Alert"; sid:9000002; rev:1; priority:1;)
alert tcp any any -> any any (msg:"Port Scan SYN Detected"; flags:S; threshold: type both, track by_src, count 20, seconds 10; sid:9000020; rev:1;)
```

### Operational commands
```bash
# Reload rules without restarting the service
sudo suricatasc -c "reload-rules"

# Validate config/rules without going live
sudo mkdir -p /tmp/suricata_test
sudo suricata -T -c /etc/suricata/suricata.yaml -v -l /tmp/suricata_test
grep -i "error\|failed" /tmp/suricata_test/suricata.log

# Check interface packet stats
sudo suricatasc -c "iface-stat gre1"

# Restart service after suricata.yaml changes (e.g. interface change)
sudo systemctl restart suricata
sudo systemctl status suricata

# Watch for a specific alert live
tail -f /var/log/suricata/eve.json | grep --line-buffered "Port Scan SYN Detected"
```

---

## 3. GRE Traffic Mirroring Setup

**Do not use ERSPAN** — `wg0` is raw-IP/NOARP, ERSPAN corrupts the packets. Use plain GRE with `tc clsact` (not `tc ... root`, which broke SSH to the hub in testing).

### On the EC2 hub (172.31.8.83)
```bash
# Create GRE tunnel interface (name gre1 avoids a kernel-reserved erspan0/gre0 auto-template conflict)
sudo ip link add gre1 type gre local 192.168.150.1 remote 192.168.150.30
sudo ip link set gre1 up

# Mirror wg0 traffic (both directions) into the tunnel — clsact only, NOT root
sudo tc qdisc add dev wg0 clsact
sudo tc filter add dev wg0 egress protocol all u32 match u32 0 0 action mirred egress mirror dev gre1
sudo tc filter add dev wg0 ingress protocol all u32 match u32 0 0 action mirred egress mirror dev gre1

# Verify
ip -d link show gre1
sudo tc filter show dev wg0 egress
sudo tc filter show dev wg0 ingress
```

### On VM-IDS-01 (192.168.150.30)
```bash
sudo ip link add gre1 type gre local 192.168.150.30 remote 192.168.150.1
sudo ip link set gre1 up

# Verify mirrored traffic arrives
sudo tcpdump -ni gre1 -c 10
```

### Rollback (if anything goes wrong)
```bash
# On the hub:
sudo tc qdisc del dev wg0 clsact
sudo ip link delete gre1

# On VM-IDS-01:
sudo ip link delete gre1
```

### ⚠️ Not persistent
These `ip link`/`tc` commands are runtime-only — they do NOT survive an EC2 reboot. To make permanent, script them into a systemd unit or netplan post-up hook on the hub.

---

## 4. Logstash Pipelines (VM-IDS-01, `~/project/logstash/pipeline/`)

### `pipelines.yml` (defines what actually runs)
```yaml
- pipeline.id: ingest-pipeline
  path.config: "/usr/share/logstash/pipeline/01-ingest.conf"
  pipeline.workers: 1
  queue.type: persisted
- pipeline.id: indexer-pipeline
  path.config: "/usr/share/logstash/pipeline/02-indexer.conf"
  pipeline.workers: 1
  queue.type: persisted
- pipeline.id: misp-pipeline
  path.config: "/usr/share/logstash/pipeline/03-misp.conf"
  pipeline.workers: 1
  queue.type: persisted
- pipeline.id: misp-ioc-pipeline
  path.config: "/usr/share/logstash/pipeline/05-misp-ioc.conf"
  pipeline.workers: 1
  queue.type: persisted
```
Note: `04-misp-ioc.conf` exists on disk but is NOT in this file — it's dead/unused.

### Key working block — `01-ingest.conf`, Suricata eve.json handling
```ruby
else if [log][file][path] =~ "eve\.json$" {

  if [event][original] {
   json { source => "[event][original]" target => "[suricata]" tag_on_failure => ["_suricata_json_failure", "_parser_failure"] }
  } else {
   json { source => "message" target => "[suricata]" tag_on_failure => ["_suricata_json_failure", "_parser_failure"] }
  }

  mutate {
   replace => { "[event][dataset]" => "suricata.%{[suricata][event_type]}" }
  }

  if [suricata][event_type] == "alert" {
   mutate {
    add_field => {
     "[event][kind]" => "alert"
     "[event][category]" => "network"
     "[event][type]" => "indicator"
     "[source][ip]" => "%{[suricata][src_ip]}"
     "[source][port]" => "%{[suricata][src_port]}"
     "[destination][ip]" => "%{[suricata][dest_ip]}"
     "[destination][port]" => "%{[suricata][dest_port]}"
     "[network][transport]" => "%{[suricata][proto]}"
     "[rule][name]" => "%{[suricata][alert][signature]}"
     "[rule][category]" => "%{[suricata][alert][category]}"
     "[event][severity]" => "%{[suricata][alert][severity]}"
    }
   }
   mutate {
    lowercase => ["[network][transport]"]
    convert => { "[source][port]" => "integer" "[destination][port]" => "integer" "[event][severity]" => "integer" }
   }
  } else {
   mutate { add_field => { "[event][kind]" => "event" "[event][category]" => "network" "[event][type]" => "info" } }
   if [suricata][src_ip] {
    mutate {
     add_field => {
      "[source][ip]" => "%{[suricata][src_ip]}"
      "[source][port]" => "%{[suricata][src_port]}"
      "[destination][ip]" => "%{[suricata][dest_ip]}"
      "[destination][port]" => "%{[suricata][dest_port]}"
      "[network][transport]" => "%{[suricata][proto]}"
     }
    }
    mutate {
     lowercase => ["[network][transport]"]
     convert => { "[source][port]" => "integer" "[destination][port]" => "integer" }
    }
   }
  }

  date {
   match => ["[suricata][timestamp]", "ISO8601"]
   target => "@timestamp"
   tag_on_failure => ["_suricata_timestamp_failure", "_parser_failure"]
  }
 }
```
**Do NOT add** `stdout { codec => rubydebug }` or `ruby { puts ... }` blocks to any pipeline in production — both caused real outages (disk exhaustion, OOM crash) in this environment.

### `02-indexer.conf` — Kafka → Elasticsearch, index routing
```ruby
output {
 if [@metadata][index_route] == "failed" {
  elasticsearch { hosts => ["http://elasticsearch:9200"] user => "elastic" password => "${ELASTIC_PASSWORD}" index => "soc-failed-%{+YYYY.MM.dd}" }
 } else {
  elasticsearch { hosts => ["http://elasticsearch:9200"] user => "elastic" password => "${ELASTIC_PASSWORD}" index => "soc-%{[event][dataset]}-%{+YYYY.MM.dd}" }
 }
}
```

### Operational commands
```bash
# Validate a pipeline file's syntax BEFORE restarting (always do this after any edit)
docker exec logstash /usr/share/logstash/bin/logstash --config.test_and_exit -f /usr/share/logstash/pipeline/01-ingest.conf

# Restart just Logstash
docker restart logstash
sleep 30 && docker ps | grep logstash

# Check pipeline internal stats (events in/out per pipeline)
docker exec -it logstash curl -s http://localhost:9600/_node/stats/pipelines?pretty

# Tail logs
docker logs logstash --tail 100
docker logs logstash --tail 100 --since 10m
```

---

## 5. Docker Compose Stack (VM-IDS-01, `~/project/docker-compose.yml`)

### Log rotation (prevents the disk-exhaustion incident recurring)
`/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
```
Apply with: `sudo systemctl restart docker` (restarts ALL containers — do this at a quiet moment).

### Standard lifecycle commands
```bash
cd ~/project

# Bring the whole stack down / up cleanly (preferred over restarting individual containers)
docker compose down
docker compose up -d

# Check status of all services
docker ps
docker ps -a          # include stopped/exited containers

# If Kafka fails with InconsistentClusterIdException after a down/up cycle:
docker compose down
docker volume rm project_kafka-data
docker compose up -d
```

### Elasticsearch queries used throughout testing
```bash
# List all Suricata-related indices
curl -u elastic:Elastic@12345 "http://localhost:9200/_cat/indices/soc-suricata*?v"

# Confirm fresh alert data (last N minutes)
curl -u elastic:Elastic@12345 \
-X GET "http://localhost:9200/soc-suricata.alert-*/_search?pretty" \
-H 'Content-Type: application/json' \
-d '{
  "query": { "range": { "@timestamp": { "gte": "now-5m" } } },
  "sort": [ { "@timestamp": "desc" } ],
  "size": 5
}'

# Query auth failures (used for SSH Brute Force rule validation)
curl -u elastic:Elastic@12345 \
-X GET "http://localhost:9200/soc-system.auth-*/_search?pretty" \
-H 'Content-Type: application/json' \
-d '{
  "query": {
    "bool": {
      "must": [
        { "match": { "event.action": "ssh_login" } },
        { "match": { "event.outcome": "failure" } }
      ],
      "filter": { "range": { "@timestamp": { "gte": "now-15m" } } }
    }
  },
  "sort": [ { "@timestamp": "desc" } ],
  "size": 10
}'

# Check MISP indices have data
curl -u elastic:Elastic@12345 "http://localhost:9200/misp-threat-intelligence/_search?pretty&size=1"
curl -u elastic:Elastic@12345 "http://localhost:9200/misp-ioc-intelligence/_search?pretty&size=1"
```

---

## 6. Kibana Rule Fixes Applied (via UI, "Edit rule settings")

For each Suricata-based rule that had the stale-index bug:
```
Index patterns:  soc-suricata.eve-*   →   soc-suricata.alert-*
Custom query:    event.dataset:"suricata.eve" and event.kind:"alert"
                 →   suricata.event_type:"alert"
```
For severity-filtered rules, also changed:
```
event.severity <= 1   →   suricata.alert.severity <= 1
```

Rules fixed this way: **Suricata IDS Alert Detection**, **Critical Suricata Alert Detection**, **Port Scan Detection**.

**Verification tool used:** each rule's Edit page has a **Rule Preview** panel (Definition tab → right side) — runs the current query/threshold live against real data without waiting for the schedule. Used successfully to confirm SSH Brute Force Detection's logic before its next scheduled run.

---

## 7. Test Traffic Generation Commands (used throughout)

```bash
# ICMP test alert (from any peer, e.g. VM-MISP-01)
ping -c3 192.168.100.1

# Port scan test (crosses the count>=20 threshold)
sudo nmap -sS -p 1-50 192.168.100.1

# SSH brute force test (run from a different peer, e.g. VM-MISP-01, against a real target)
for i in {1..6}; do
  ssh baduser@192.168.100.10 -o PasswordAuthentication=yes -o PreferredAuthentications=password
done
```
**Always generate test traffic from a genuine different peer** (not VM-IDS-01 itself) to properly exercise the GRE mirror / cross-peer visibility.

---

## 8. Quick Health-Check Sequence (run this first in any new session)

```bash
# 1. Docker stack
docker ps

# 2. Suricata service + interface
sudo systemctl status suricata
sudo suricatasc -c "iface-stat gre1"

# 3. GRE mirror still up on both ends
ip -d link show gre1                      # run on both EC2 hub and VM-IDS-01

# 4. Fresh data flowing end to end
ping -c3 192.168.100.1   # from a peer other than VM-IDS-01
sleep 10
curl -u elastic:Elastic@12345 \
"http://localhost:9200/soc-suricata.alert-*/_search?pretty" \
-H 'Content-Type: application/json' \
-d '{"query":{"range":{"@timestamp":{"gte":"now-2m"}}},"sort":[{"@timestamp":"desc"}],"size":1}'
```
If step 4 returns a fresh, correctly-tagged hit — the whole stack is healthy and you're clear to resume rule testing.

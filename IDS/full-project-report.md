# SOC IDS/ELK Project — Complete Implementation Report

**Scope:** Full record of Suricata IDS deployment troubleshooting, architecture redesign, ELK pipeline debugging, and Kibana detection rule validation. Use this as the master reference for any new session.

---

## PART 1 — Environment

| Component | Detail |
|---|---|
| IDS sensor | VM-IDS-01, Ubuntu, Suricata 6.0.4, `192.168.150.30` |
| MISP server | VM-MISP-01, `192.168.150.10` |
| Services host | VM-SERVICES-01, `192.168.100.10` (runs SSH target, BIND, nginx, auth.log source) |
| VPN hub | EC2 (Ubuntu), private `172.31.8.83`, public `13.205.137.92`, WireGuard over wstunnel on port 443 (disguises VPN as HTTPS to pass through a Fortigate firewall) |
| Hub `wg0` addresses | `192.168.100.1`, `192.168.150.1`, `192.168.200.1` (routes 3 subnets) |
| ELK stack | Docker Compose on VM-IDS-01: elasticsearch, kibana, logstash, kafka, zookeeper (network `threatops-net`) |
| Elasticsearch auth | `elastic` / `Elastic@12345` — **exposed in plaintext throughout this session, rotate when convenient** |

---

## PART 2 — Suricata Rule-Loading Troubleshooting (resolved early)

**Symptom:** Custom `local.rules` (e.g., `alert icmp any any -> any any (msg:"ICMP Test Alert"; sid:1000001; rev:1;)`) generated no alerts; signature count in logs didn't increase.

**Root cause:** `default-rule-path` in `suricata.yaml` pointed to `/var/lib/suricata/rules`, and the correct file lived there — this was actually fine. Real cause traced to: the Suricata **service had been stopped** (`suricatasc -c shutdown`) and never restarted, so live traffic hit a dead engine. Confirmed via `systemctl status` showing `inactive (dead)`.

**Fix:** `systemctl start suricata`. Verified via `suricata -T` test mode and fresh eve.json alert output.

**Lesson applied throughout the rest of the session:** always confirm the service is actually running before deeper debugging, and use `suricata -T -c ... -v -l <dir>` to validate config/rule syntax before any restart.

---

## PART 3 — Detection Validation Plan (5 categories originally scoped)

Established a standard test methodology, later expanded to all 17 Kibana rules (see Part 8):
1. Pre-check rule is loaded/parses clean
2. Generate real traffic
3. Verify fresh entry in eve.json
4. Verify in Elasticsearch directly (bypass Kibana query ambiguity)
5. Verify in Kibana Alerts/Execution Results

---

## PART 4 — Kibana Rule Bugs Found (Suricata-related rules)

### Bug 1: Stale/wrong index pattern
Multiple rules pointed at `soc-suricata.eve-*` — a nearly-empty (100 docs), stale index from 7/25, unrelated to live data. Real live alerts land in `soc-suricata.alert-*` (daily-rotated, tens of thousands of docs/day). **Fixed** on: Suricata IDS Alert Detection, Critical Suricata Alert Detection, Port Scan Detection.

### Bug 2: Wrong/mismatched field names in custom query
Rules queried `event.dataset:"suricata.eve"` / `event.kind:"alert"` — these ECS-style fields were being **mislabeled by Logstash** (see Bug 3) or simply didn't match reality. **Fixed** by switching queries to the raw, untouched Suricata field: `suricata.event_type:"alert"` (and `suricata.alert.severity` for severity-based rules).

### Bug 3: Logstash mislabeling — root cause of Bug 2
In `01-ingest.conf`'s `eve.json` handling block, `event.kind:"alert"` and `rule.name` (built from `%{[suricata][alert][signature]}`) were applied **unconditionally to every Suricata event type** — flow, dhcp, dns, stats — not just real alerts. Non-alert events had no `alert` sub-object, so `rule.name` literally contained the broken text `%{[suricata][alert][signature]}` instead of a real value, and everything (including harmless DHCP traffic) was falsely tagged `event.kind:"alert"`.

**Fix applied:**
```ruby
mutate {
 replace => { "[event][dataset]" => "suricata.%{[suricata][event_type]}" }
}
if [suricata][event_type] == "alert" {
  mutate { add_field => { "[event][kind]" => "alert" ... "[rule][name]" => "%{[suricata][alert][signature]}" ... } }
} else {
  mutate { add_field => { "[event][kind]" => "event" ... } }
  if [suricata][src_ip] {
    mutate { add_field => { "[source][ip]" => "%{[suricata][src_ip]}" ... } }   # guards events with no connection info (e.g. stats)
  }
}
```
Verified: flow/dns/stats events now correctly land in their own indices (`soc-suricata.flow-*`, `soc-suricata.dns-*`, `soc-suricata.stats-*`) with honest labels; real alerts unaffected.

**Editing mistake made and caught:** a leftover duplicate `mutate` block and mismatched brace was introduced mid-edit — caught via `docker exec logstash ... --config.test_and_exit`, which should always be run before restarting after any pipeline edit going forward.

---

## PART 5 — Architecture Problem: IDS Could Only See Its Own Traffic

**Discovery:** Port Scan Detection testing revealed nmap traffic from VM-MISP-01 → target never appeared in Suricata's capture, even though the same command run from VM-IDS-01 itself worked fine.

**Root cause:** WireGuard here is a hub-and-spoke topology through the EC2 hub, not a shared broadcast segment. Each peer has its own independent tunnel to the hub; VM-IDS-01's network interface only ever sees its own traffic, never other peers' traffic passing through the hub.

**Fix — GRE traffic mirroring from the hub:**
1. Confirmed the EC2 instance (`172.31.8.83`) is the single central relay for all peers (multi-subnet `wg0`: `.100.1`, `.150.1`, `.200.1`).
2. **First attempt: ERSPAN — failed.** `wg0` is `POINTOPOINT,NOARP` (raw IP, no Ethernet framing); ERSPAN requires Ethernet and produced corrupted/unreadable packets (`ethertype Unknown (0xc0a8)`).
3. Also hit a **`tc qdisc ... root` incident**: replacing wg0's root qdisc to enable mirroring broke SSH/network reachability to the EC2 hub entirely. Root cause: replacing the root qdisc likely disrupted WireGuard's own packet scheduling.
   - **Recovery:** SSH and EC2 Instance Connect both failed; AWS Instance status check showed "Instance status: Check failed" (System/EBS status both fine). Resolved via full **Stop → Start** of the EC2 instance (not reboot) — confirmed safe since none of the changes were persisted to boot config.
   - **Preventive step taken:** created an AMI backup of the EC2 hub (with "No reboot" checked to avoid disrupting live VPN peers) before retrying.
4. **Second attempt: GRE tunnel + `tc clsact` — succeeded.**
   - On EC2 hub: `ip link add gre1 type gre local 192.168.150.1 remote 192.168.150.30` (VM-IDS-01's wg0 IP)
   - `tc qdisc add dev wg0 clsact` (does not touch packet scheduling, unlike `root` — this was the key fix)
   - `tc filter add dev wg0 egress ... action mirred egress mirror dev gre1` (and same for `ingress`)
   - On VM-IDS-01: matching `gre1` interface (`local 192.168.150.30 remote 192.168.150.1`)
   - Suricata's `af-packet` interface changed from `wg0` to `gre1` in `suricata.yaml`, service restarted.
   - **Verified end-to-end:** Suricata alerts now show `"in_iface":"gre1"` with `src_ip` values belonging to *other* peers (e.g. `192.168.150.10` = VM-MISP-01), confirming genuine cross-peer visibility for the first time.
   - **Note:** an `erspan0` interface auto-spawns as a kernel template device the moment the ERSPAN module loads and cannot be deleted/reused under that name — had to use `erspan1`/`gre1` naming instead. Not a real problem, just a known Linux tunnel-driver quirk.
   - **Known residual issue, not yet fixed:** mirrored packets appear duplicated (~2x) in captures — likely from having both `ingress` and `egress` filters catching overlapping traffic. Doesn't break detection but inflates alert volume; worth investigating later.

**This was the most significant and highest-risk change made in this session** — it involved a live production VPN hub incident and recovery, but is now confirmed stable and working.

---

## PART 6 — Infrastructure Incidents (post-mirroring)

### Incident A: Disk exhaustion (documented separately by the team)
`stdout { codec => rubydebug }` blocks in the Logstash pipelines caused unrotated Docker JSON logs to grow to 60GB, tripping Elasticsearch's flood-stage watermark (read-only lock on `.kibana_*` indices → Kibana login failures). Resolved via log truncation + removing `stdout` blocks + adding Docker log rotation (`/etc/docker/daemon.json`, `max-size: 100m, max-file: 3`) + unlocking indices (`index.blocks.read_only_allow_delete: null`).

**Follow-up finding:** the incident report claimed `stdout` blocks were removed and daemon.json was configured — **neither was actually true when checked**. `stdout` blocks were present in all 5 pipeline files, and `/etc/docker/daemon.json` didn't exist. Both were then genuinely fixed in this session.

### Incident B: Logstash OOM crash
Separate/later crash: `java.lang.OutOfMemoryError: Java heap space` in the `misp-ioc-pipeline`, traced to a leftover `ruby { puts event.to_hash... }` debug block (prints every field of every event to stdout) combined with an aggressive 1-minute MISP polling cron. **Fixed:** removed the ruby debug block, throttled cron to `*/5 * * * *`. JVM heap remains at default `-Xms1g -Xmx1g` — not yet increased, flagged as a residual risk (jvm.options file is baked into the image, not bind-mounted; would need `LS_JAVA_OPTS` env var override to change without rebuilding).

### Incident C: Full-stack restart cascade (`docker compose down/up`)
Applying the Docker daemon log-rotation fix required a full daemon restart, which cascaded into a stuck containerd task for `zookeeper` (stale task reference blocking individual container restart). Resolved via `docker compose down` + `docker compose up -d`, which then surfaced:

**Incident C2: Kafka cluster ID mismatch**
```
InconsistentClusterIdException: Cluster ID ... doesn't match stored clusterId ... in meta.properties
```
Zookeeper got a fresh volume/cluster identity on restart; Kafka's own volume still referenced the old cluster ID. **Fixed** by removing the `project_kafka-data` Docker volume and letting Kafka reinitialize fresh (acceptable since Kafka here is a transient message queue, not a system of record — any in-flight backlog was lost, not a concern for testing purposes).

**Final state confirmed:** all 5 containers (`elasticsearch`, `kibana`, `logstash`, `kafka`, `zookeeper`) healthy and stable across repeated checks.

---

## PART 7 — Post-Recovery Verification

Ran a fresh end-to-end test (ICMP ping from VM-MISP-01 → target) after the full stack rebuild:
- Confirmed in Elasticsearch: 1,511 hits in a 5-minute window, correctly tagged (`in_iface:"gre1"`, `src_ip:"192.168.150.10"`, correct rule names, fresh timestamps).
- Confirmed the whole chain (Suricata → GRE mirror → Filebeat → Logstash → Kafka → Elasticsearch) survived the rebuild intact.

---

## PART 8 — Kibana Rule Test Status (17 total)

| # | Rule | Status | Notes |
|---|---|---|---|
| 1 | Suricata IDS Alert Detection | ✅ PASS | Index+query fixed, verified with cross-peer traffic |
| 2 | Critical Suricata Alert Detection | ✅ PASS | Same fix, severity ≤1 filter confirmed |
| 3 | Port Scan Detection | ✅ PASS | Index+query fixed, threshold rule (`source.ip >= 20`), confirmed via real nmap scan (6 alerts fired) |
| 4 | SSH Brute Force Detection | 🟡 Data confirmed correct, Rule Preview confirmed 1 alert would fire; awaiting/checking actual scheduled execution | Query: `event.action:"ssh_login" and event.outcome:"failure"`, index `soc-system.auth-*`, threshold `source.ip >= 5` — 8 matching docs confirmed in ES |
| 5 | Multiple Authentication Failures | ⬜ Not tested | Likely same `soc-system.auth-*` source, different threshold |
| 6 | Web Login Failure Detection | ⬜ Not tested | Source: nginx access log |
| 7 | Sudo Usage Detection | 🟡 Possibly fired incidentally | "Sudo Usage Detected (Privilege Escalation)" showed 6 alerts from routine `sudo` commands during testing — needs deliberate confirmation of which rule this was |
| 8 | Sudo Usage Detected (Privilege Escalation) | 🟡 See above | Possibly overlapping/duplicate with #7 |
| 9 | Web Enumeration / 404 Scan Detection | ⬜ Not tested | Source: nginx access log, `sensitive_path_scan` action already defined in Logstash filter |
| 10 | Excessive DNS Queries Detection | ⬜ Not tested | Source: BIND query log |
| 11 | File Integrity Modification Detected | ⬜ Not tested | Source: Auditbeat `file_integrity` module |
| 12 | CI/CD Pipeline Failure Detection | ⬜ Not tested | Source: `/var/log/cicd/*` |
| 13 | Log Parser Failure Detection | ⬜ Not tested | Tests `soc-failed-*` index path (malformed log → `_parser_failure` tag) |
| 14 | Threat Intelligence IOC Match Detection | ⬜ Not tested | See Part 9 |
| 15 | Critical MISP Threat Intelligence | ⬜ Not tested | See Part 9 |
| 16 | New MISP Threat Intelligence | ⬜ Not tested | See Part 9 |
| 17 | Published Critical MISP Threat | ⬜ Not tested | See Part 9 |

---

## PART 9 — MISP Pipeline Configuration (for rules 14–17)

**Two active pipelines:**
- `03-misp.conf` (misp-pipeline) — polls `https://misp.kpk.local/events/index` every 5 min → index `misp-threat-intelligence`. Minimal tagging (`source:"MISP"` only, **no `event.dataset`/`event.kind`** — check this doesn't break rule queries the same way Suricata's did).
- `05-misp-ioc.conf` (misp-ioc-pipeline) — polls `https://misp.kpk.local/attributes/restSearch` every 5 min (throttled from 1 min after Incident B) → index `misp-ioc-intelligence`. Properly tagged: `event.dataset:"misp.ioc"`, `event.kind:"enrichment"`, `threat.indicator.type/value/reference`.

**Orphaned/unused:** `04-misp-ioc.conf` — not in `pipelines.yml`, has a JSON body syntax bug (missing comma, unquoted `json`), talks to raw IP instead of DNS name. Safe to delete.

**Known non-blocking issues:**
- Recurring `Invalid cookie header` warnings from MISP's `Set-Cookie` response (harmless).
- One observed `NilClass` split-filter warning — a MISP response without an `Attribute` field; no guard exists yet, worth adding `if [response][Attribute] { split {...} }`.
- Hardcoded API key (same value) in plaintext across both active configs.

**Before testing rules 14–17:** open each rule's Definition panel and record index pattern + query — given the pattern seen throughout this session, do not assume they're correctly pointed; verify first, same methodology as Part 8.

---

## PART 10 — Outstanding / Recommended Follow-Ups

1. Rotate `elastic:Elastic@12345` — exposed in plaintext dozens of times this session.
2. Investigate GRE mirror duplicate-packet issue (ingress+egress filter overlap).
3. Increase Logstash JVM heap via `LS_JAVA_OPTS` env var for safety margin.
4. Delete orphaned `04-misp-ioc.conf`.
5. Add null-guard to MISP IOC pipeline's `split` filter.
6. Clean up leftover `.03-misp.conf.swp` Vim swap file.
7. Confirm whether the two "Sudo Usage" rules are intentionally duplicated.
8. Consider setting up basic container/pipeline health monitoring — Logstash was down silently for ~5 hours during this session with nothing alerting on it.
9. Migrate custom Suricata SIDs off low/example ranges (`9000001` etc. is fine — was already moved off the `1000001` example default).
10. Document the GRE mirror setup (not currently persistent across EC2 reboot — `tc`/`ip link` commands would need to be scripted into a startup unit if this is to be permanent).

---

**Resume point:** Check SSH Brute Force Detection's Execution Results tab, then proceed through rules 5–17 in order using the established test methodology (Part 3), paying special attention to index-pattern/field-name verification before generating any test traffic (Part 4 pattern repeats often).

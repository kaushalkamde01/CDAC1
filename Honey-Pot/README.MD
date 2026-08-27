# 🛡️ SOC Honeypot Sensor — Deployment Guide

> A modular, enterprise-style honeypot sensor built with **Debian 12 + Docker + Cowrie + Dionaea + Suricata IDS**, designed to feed a centralized SOC (ELK / MISP) with high-quality attacker telemetry.

---

## 📑 Table of Contents

- [1. Overview](#1-overview)
- [2. Architecture](#2-architecture)
- [3. Prerequisites](#3-prerequisites)
- [4. Placeholder Reference](#4-placeholder-reference)
- [5. Phase 1 — Base OS & Docker Environment](#5-phase-1--base-os--docker-environment)
- [6. Phase 2 — Cowrie SSH Honeypot](#6-phase-2--cowrie-ssh-honeypot)
- [7. Phase 3 — Suricata IDS](#7-phase-3--suricata-ids)
- [8. Phase 4 — Dionaea Multi-Service Honeypot](#8-phase-4--dionaea-multi-service-honeypot)
- [9. Validation Checklist](#9-validation-checklist)
- [10. Troubleshooting Reference](#10-troubleshooting-reference)
- [11. Roadmap / Planned Enhancements](#11-roadmap--planned-enhancements)

---

## 1. Overview

**Goal:** Collect attacker behavior, detect malicious network activity, and generate high-quality telemetry for centralized SOC analysis — *without* duplicating an existing ELK/SIEM stack (i.e., no full T-Pot install).

**Why a custom build instead of T-Pot?**

| Reason | Benefit |
|---|---|
| Existing ELK infra | No duplicate Elasticsearch/Kibana |
| Modular components | Lower RAM/CPU footprint |
| Manual integration | Easier troubleshooting & maintenance |
| Component-level build | Deeper understanding, more "enterprise-like" |

**Design objectives:**
- [x] Capture attacker behavior (SSH, multi-protocol)
- [x] Record brute-force attacks
- [x] Observe malware delivery attempts
- [x] Detect network-level attacks
- [x] Generate high-quality, structured security logs
- [ ] Integrate with centralized SIEM (planned — Filebeat/ELK)
- [ ] Threat intel enrichment via MISP (planned)
- [x] Simulate enterprise SOC sensor architecture

---

## 2. Architecture

```
                         Internet
                            │
                       Attack Traffic
                            │
                     VMware Network
                            │
                  Debian 12 Honeypot VM
                            │
                 ┌──────────────────────┐
                 │   Suricata IDS        │
                 │   (Host Machine)      │
                 └──────────┬────────────┘
                            │
                     Docker Network
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
       Cowrie           Dionaea         Heralding (Future)
     SSH Honeypot     Multi-Service
          │                 │
          └────────┬────────┘
                    │
             Security Telemetry
                    │
             Filebeat (Planned)
                    │
              ELK Stack (SOC)
                    │
                 Kibana
                    │
                  MISP
```

**Traffic flow logic:** every packet hits `Suricata` (host, AF_PACKET) *before* Docker forwards it to the honeypot containers — giving you both network-level (IDS) and application-level (deception) visibility on the same traffic.

---

## 3. Prerequisites

- A hypervisor (VMware Workstation/ESXi, or equivalent)
- Debian 12 (Bookworm) netinstall ISO
- A **dedicated, isolated network segment** for the honeypot VM (do NOT place it on your production/home LAN without proper segmentation and a firewall)
- Minimum recommended specs: **4 vCPU / 8 GB RAM**
- Basic familiarity with Docker, Linux administration, and network interfaces

> ⚠️ **Safety note:** A honeypot is intentionally exposed to attack traffic. Always deploy it in an isolated VLAN/segment, monitor egress traffic to prevent it being used as a pivot point, and never expose management interfaces (SSH admin, Docker socket) to the same network the honeypot services listen on.

---

## 4. Placeholder Reference

This guide uses placeholders instead of real values. Replace them with your own environment's details before running any command.

| Placeholder | Description | Example format |
|---|---|---|
| `<HONEYPOT_IP>` | IP address of the honeypot VM | `10.x.x.x` |
| `<ATTACKER_TEST_IP>` | IP of a separate VM used to test the honeypot | `10.x.x.x` |
| `<SUBNET_CIDR>` | Internal VMware/lab subnet | `10.x.x.0/24` |
| `<NETWORK_INTERFACE>` | Host NIC name (e.g. from `ip addr`) | `ensXX` |
| `<SSH_ADMIN_USER>` | Non-root admin account used to manage the host | `soc-admin` |
| `<SSH_PORT_HONEYPOT>` | Port Cowrie listens on | `2222` |
| `<PROJECT_ROOT>` | Base directory for all honeypot components | `/opt/soc-honeypot` |
| `<COWRIE_IMAGE>` | Cowrie Docker image tag | `cowrie/cowrie:latest` |
| `<DIONAEA_IMAGE>` | Dionaea Docker image tag | `dinotools/dionaea:latest` |
| `<DOCKER_NETWORK_NAME>` | Docker bridge network name | `soc-network` |
| `<SNAPSHOT_NAME>` | VM snapshot label | `debian12-clean-baseline` |

---

## 5. Phase 1 — Base OS & Docker Environment

### 5.1 Install Debian 12

During install, select **only**:
- ✅ SSH Server
- ✅ Standard System Utilities

Deselect all desktop environments and unneeded server roles (web server, mail server, print server, etc.) to minimize the attack surface and resource usage.

### 5.2 Verify system information

```bash
hostnamectl
ip addr
free -h
lscpu | grep "CPU(s)"
```

Confirm: OS version, hypervisor platform, vCPU count, RAM, and your network interface name (`<NETWORK_INTERFACE>`).

### 5.3 Install and configure `sudo`

```bash
su -
apt update
apt install sudo -y
usermod -aG sudo <SSH_ADMIN_USER>
groups <SSH_ADMIN_USER>          # should list "sudo"
exit                             # log out, then log back in as <SSH_ADMIN_USER>
sudo whoami                      # should return "root"
```

### 5.4 Update the system

```bash
sudo apt update
sudo apt full-upgrade -y
```

### 5.5 Install base utilities

```bash
sudo apt install -y \
  curl wget git vim nano htop net-tools tree zip unzip \
  ca-certificates gnupg lsb-release apt-transport-https software-properties-common
```

### 5.6 Verify time sync (required for accurate SOC log correlation)

```bash
timedatectl
# Confirm: "System clock synchronized: yes"
```

### 5.7 Take a clean VM snapshot

Snapshot the VM now (`<SNAPSHOT_NAME>`) as a rollback point before honeypot services go live.

### 5.8 Install Docker Engine

```bash
# Remove any conflicting old packages
sudo apt remove docker docker-engine docker.io containerd runc -y

# Prerequisites
sudo apt install -y ca-certificates curl gnupg lsb-release

# Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable + start
sudo systemctl enable docker
sudo systemctl start docker

# Let the admin user run docker without sudo
sudo usermod -aG docker <SSH_ADMIN_USER>
newgrp docker
```

### 5.9 Verify Docker

```bash
docker --version
docker compose version
docker run hello-world
```

✅ Confirms: Docker Engine, Docker Compose, daemon health, and Docker Hub connectivity.

### 5.10 Create the project directory structure

```bash
sudo mkdir -p <PROJECT_ROOT>
sudo chown -R <SSH_ADMIN_USER>:<SSH_ADMIN_USER> <PROJECT_ROOT>

cd <PROJECT_ROOT>
mkdir -p cowrie dionaea heralding suricata aide filebeat configs

tree <PROJECT_ROOT>
```

Expected layout:

```
<PROJECT_ROOT>
├── aide
├── configs
├── cowrie
├── dionaea
├── filebeat
├── heralding
└── suricata
```

### 5.11 Create the Docker bridge network

```bash
docker network create <DOCKER_NETWORK_NAME>
docker network ls   # confirm it's listed
```

**Phase 1 outcome:** hardened Debian 12 host, Docker Engine + Compose, isolated bridge network, organized project workspace, and a clean rollback snapshot.

---

## 6. Phase 2 — Cowrie SSH Honeypot

**Purpose:** SSH/Telnet deception — credential harvesting, brute-force detection, fake shell, full command logging.

### 6.1 ⚠️ Common pitfall: bind mounts vs. named volumes

Cowrie's container **must not** be given empty bind-mounted host directories — it needs to create its own internal directory structure (UUID file, SSH host keys, logs) on first boot. Bind-mounting empty folders breaks this. **Use Docker named volumes instead.**

### 6.2 Create the Compose file

```bash
cd <PROJECT_ROOT>
nano docker-compose.yml
```

```yaml
services:
  cowrie:
    image: <COWRIE_IMAGE>
    container_name: cowrie
    restart: unless-stopped
    ports:
      - "<SSH_PORT_HONEYPOT>:2222"
    volumes:
      - cowrie-etc:/cowrie/cowrie-git/etc
      - cowrie-var:/cowrie/cowrie-git/var
    networks:
      - <DOCKER_NETWORK_NAME>

volumes:
  cowrie-etc:
  cowrie-var:

networks:
  <DOCKER_NETWORK_NAME>:
    external: true
```

> If you initially try bind mounts (`./cowrie/etc:...`, `./cowrie/var:...`) you will likely see errors like `Permission denied when attempting to write uuid`, or missing `ssh_host_rsa_key.pub` / `cowrie.json`. Switch to named volumes as shown above to resolve this.

### 6.3 Deploy

```bash
docker compose pull
docker compose up -d
docker ps                 # cowrie should show STATUS: Up, PORT <SSH_PORT_HONEYPOT>->2222
docker logs -f cowrie
```

Expect to see:
```
Loaded output engine: jsonlog
CowrieSSHFactory starting on 2222
Ready to accept SSH connections
```

### 6.4 Functional test

From a **separate** test VM (`<ATTACKER_TEST_IP>`):

```bash
ssh -p <SSH_PORT_HONEYPOT> root@<HONEYPOT_IP>
```

Try a few dummy credentials (e.g. `root`/`admin`, `root`/`toor`) to generate telemetry.

### 6.5 Validate telemetry

```bash
docker logs cowrie
```

Confirm captured fields: source IP, SSH client version, username, password, session ID, timestamp, executed commands, session duration.

**Notes / gotchas found during deployment:**
- Modern Cowrie images are minimal — standard utilities like `ls`, `tail`, `bash` are *not* available inside the container for shell debugging.
- Always use named volumes, not bind mounts, for `etc/` and `var/`.

**Phase 2 outcome:** working SSH honeypot with JSON logging, fake shell, credential capture, and full session recording on persistent storage.

---

## 7. Phase 3 — Suricata IDS

**Purpose:** network-level intrusion detection — inspects all traffic before it reaches the Docker honeypot layer.

### 7.1 Design decision: host install, not containerized

Suricata is installed **directly on the Debian host** (not in Docker) because it needs direct NIC access via AF_PACKET for full packet capture and best performance.

### 7.2 Install Suricata

```bash
sudo apt update
sudo apt install suricata -y
```

> **Common issue:** `Package suricata is not available` / `has no installation candidate`.
> **Fix:** refresh the APT cache and re-check your repo sources:
> ```bash
> cat /etc/apt/sources.list
> grep -R "^deb" /etc/apt/sources.list /etc/apt/sources.list.d/
> apt policy suricata
>
> sudo rm -rf /var/lib/apt/lists/*
> sudo apt clean
> sudo apt update
> ```

### 7.3 Verify install

```bash
suricata --build-info
```

### 7.4 Check the service — fix the interface name

```bash
sudo systemctl status suricata
```

If it shows `Active: failed`, dig in:

```bash
sudo suricata -T -c /etc/suricata/suricata.yaml
sudo journalctl -u suricata -n 50 --no-pager
sudo tail -50 /var/log/suricata/suricata.log
```

> **Common issue:** the default config monitors `eth0`, but your actual VMware interface is `<NETWORK_INTERFACE>` (confirmed via `ip addr` in step 5.2).

Edit the config:

```bash
sudo nano /etc/suricata/suricata.yaml
```

Find:
```yaml
af-packet:
  - interface: eth0
```

Change to:
```yaml
af-packet:
  - interface: <NETWORK_INTERFACE>
```

Also set `HOME_NET` to your lab subnet, `<SUBNET_CIDR>`.

Verify and restart:

```bash
grep -n "interface:" /etc/suricata/suricata.yaml
sudo suricata -T -c /etc/suricata/suricata.yaml   # expect: "Configuration provided was successfully loaded."
sudo systemctl restart suricata
sudo systemctl status suricata --no-pager         # expect: Active: active (running)
```

### 7.5 Install detection rules

```bash
sudo suricata-update list-sources
sudo suricata-update
sudo systemctl restart suricata

ls -lh /var/lib/suricata/rules/
grep -c "^alert" /var/lib/suricata/rules/suricata.rules
```

You should see a rule count in the tens of thousands (Emerging Threats Open is a good default free source).

### 7.6 Validate protocol detection

Generate traffic against the honeypot (e.g. `ssh -p <SSH_PORT_HONEYPOT> root@<HONEYPOT_IP>` from `<ATTACKER_TEST_IP>`), then check:

```bash
sudo grep '"event_type":"ssh"'  /var/log/suricata/eve.json | tail
sudo grep '"event_type":"ftp"'  /var/log/suricata/eve.json | tail
sudo grep '"event_type":"http"' /var/log/suricata/eve.json | tail
sudo grep '"event_type":"smb"'  /var/log/suricata/eve.json | tail
```

**Key finding:** because Suricata inspects traffic at the physical interface (AF_PACKET) *before* Docker's bridge forwards it, containerizing the honeypots does **not** reduce Suricata's visibility — every packet destined for a container is still inspected first.

**Phase 3 outcome:** host-based NIDS with 50k+ signatures, correct interface binding, and confirmed multi-protocol detection (`eve.json`, `fast.log`, `stats.log`).

---

## 8. Phase 4 — Dionaea Multi-Service Honeypot

**Purpose:** broaden the attack surface beyond SSH — emulate FTP, HTTP, SMB, MSSQL, MySQL, SIP, TFTP, PPTP, printer, and UPnP services to capture exploitation attempts and malware delivery.

### 8.1 Create the workspace and named volumes

```bash
cd <PROJECT_ROOT>
mkdir -p dionaea && cd dionaea

docker volume create dionaea-logs
docker volume create dionaea-data
docker volume ls
```

### 8.2 Create the Compose file

```bash
nano compose.yml
```

```yaml
services:
  dionaea:
    image: <DIONAEA_IMAGE>
    container_name: dionaea
    restart: unless-stopped
    ports:
      - "21:21"
      - "42:42"
      - "69:69/udp"
      - "80:80"
      - "135:135"
      - "443:443"
      - "445:445"
      - "1433:1433"
      - "1723:1723"
      - "3306:3306"
      - "5060:5060"
      - "5060:5060/udp"
    volumes:
      - dionaea-logs:/opt/dionaea/var/log
      - dionaea-data:/opt/dionaea/var/lib
    networks:
      - <DOCKER_NETWORK_NAME>

volumes:
  dionaea-logs:
  dionaea-data:

networks:
  <DOCKER_NETWORK_NAME>:
    external: true
```

> ⚠️ Exposing this many services (especially SMB/MSSQL/MySQL) increases risk surface even for a honeypot. Confirm your VM is properly isolated before deploying.

### 8.3 Deploy

```bash
docker compose pull
docker compose up -d
docker ps            # both "cowrie" and "dionaea" should show STATUS: Up
docker compose ps
```

### 8.4 Verify listening ports

```bash
sudo ss -tulpn | grep -E ':21|:80|:445|:135|:1433|:3306|:5060'
```

### 8.5 Review logs

```bash
docker logs dionaea --tail 50
```

Confirm creation of `services-enabled`, `services-available`, `var/lib/dionaea`, `var/log/dionaea`, and a clean `Starting dionaea ...` with no runtime errors.

### 8.6 Functional testing (from `<ATTACKER_TEST_IP>`)

```bash
# FTP — expect a spoofed banner, e.g. a fake "DiskStation FTP server ready"
telnet <HONEYPOT_IP> 21

# HTTP
curl http://<HONEYPOT_IP>

# SMB
nc <HONEYPOT_IP> 445
```

Dionaea intentionally serves realistic-looking service banners to increase deception credibility.

### 8.7 Verify persistent storage

```bash
docker exec dionaea find /opt/dionaea/var/log -type f
# → dionaea.log, dionaea-errors.log

docker exec dionaea find /opt/dionaea/var -maxdepth 3 -type f
# → dionaea.sqlite
```

### 8.8 Extract and inspect the SQLite telemetry DB

The container doesn't ship `sqlite3`, so pull the DB to the host:

```bash
docker cp dionaea:/opt/dionaea/var/lib/dionaea/dionaea.sqlite /tmp/dionaea.sqlite

sudo apt update
sudo apt install sqlite3 -y

sqlite3 /tmp/dionaea.sqlite
.tables
```

Expected tables: `connections`, `downloads`, `offers`, `logins`, `resolves`, `p0fs`, `virustotals`, `virustotalscans`, `mysql_commands`, `mssql_commands`, `sip_commands`, `mqtt_fingerprints`, `dcerpcservices`, `emu_profiles`, `emu_services`.

### 8.9 Cross-validate with Suricata

```bash
sudo grep '"event_type":"ftp"'  /var/log/suricata/eve.json | tail
sudo grep '"event_type":"http"' /var/log/suricata/eve.json | tail
sudo grep '"event_type":"smb"'  /var/log/suricata/eve.json | tail
```

Confirms Dionaea-bound traffic is also visible to the host IDS — the layered model (Suricata = network layer, honeypots = application layer) is working end to end.

**Phase 4 outcome:** multi-protocol deception service, persistent SQLite-backed telemetry, and confirmed integration with the Suricata sensor.

---

## 9. Validation Checklist

| Component | Status |
|---|:---:|
| Debian 12 base server | ✅ |
| Docker Engine | ✅ |
| Docker Compose | ✅ |
| Docker bridge network | ✅ |
| Cowrie SSH honeypot | ✅ |
| Dionaea multi-service honeypot | ✅ |
| Suricata IDS | ✅ |
| AF_PACKET capture | ✅ |
| Emerging Threats rules loaded | ✅ |
| Protocol detection validated | ✅ |
| Persistent storage (volumes) | ✅ |
| SQLite telemetry (Dionaea) | ✅ |
| Filebeat → ELK forwarding | ⬜ Planned |
| Kibana dashboards | ⬜ Planned |
| MISP enrichment | ⬜ Planned |
| AIDE (file integrity monitoring) | ⬜ Planned |
| Heralding (auth honeypot) | ⬜ Planned |

---

## 10. Troubleshooting Reference

| Symptom | Root Cause | Fix |
|---|---|---|
| Cowrie: `Permission denied` writing UUID / SSH keys / logs | Bind-mounted empty host directories | Switch to Docker **named volumes** for `etc/` and `var/` |
| Suricata: `Package suricata is not available` | Stale/misconfigured APT sources | `apt clean && rm -rf /var/lib/apt/lists/* && apt update` |
| Suricata service `Active: failed` | Configured to monitor `eth0`, but real NIC is `<NETWORK_INTERFACE>` | Edit `af-packet.interface` in `suricata.yaml` |
| Suricata: `No rule files match` | Rules never downloaded | `sudo suricata-update` then restart the service |
| Dionaea: can't inspect DB in-container | No `sqlite3` binary in the image | `docker cp` the `.sqlite` file to host, install `sqlite3` there |

---

## 11. Roadmap / Planned Enhancements

- [ ] **Filebeat** — forward Cowrie/Dionaea/Suricata logs to the central ELK stack
- [ ] **Elasticsearch + Kibana** — indexing and dashboarding of honeypot telemetry
- [ ] **MISP** — threat intelligence enrichment of captured IOCs (IPs, hashes, credentials)
- [ ] **AIDE** — host-level file integrity monitoring
- [ ] **Heralding** — additional multi-protocol authentication honeypot
- [ ] Cross-correlation of honeypot + IDS events for automated SOC alerting

---

### Summary

This build demonstrates a layered, modular SOC sensor: Suricata provides network-level visibility on every packet before Docker ever sees it, while Cowrie and Dionaea provide rich, protocol-specific application-layer deception and telemetry. The result is a solid, extensible foundation for centralized detection and threat-intel correlation once Filebeat/ELK/MISP integration is completed.

# WSTunnel over WireGuard

**Tunneling WireGuard through a firewall that only trusts port 443**

The organization firewall blocked native WireGuard UDP (51820) and ICMP outright, so on-premises machines could never reach the AWS WireGuard server directly. WSTunnel wraps the WireGuard UDP stream inside a WebSocket-over-TLS connection — traffic the firewall already permits — and hands it back to WireGuard on the other side.

| | |
|---|---|
| **Transport** | WebSocket / TLS / TCP 443 |
| **Payload** | WireGuard UDP 51820 |
| **Server** | AWS EC2 · `<public_ip>` |
| **Clients** | Ubuntu Linux + Windows Server |
| **Blocked by firewall** | UDP/51820, ICMP |
| **WSTunnel version** | v10.6.2 |

```
TCP 443 ─── firewall sees this
  └─ WSS (WebSocket over TLS)
       └─ WSTunnel (forwards the UDP payload)
            └─ WireGuard (UDP 51820) ─── what's actually carried
```

---

## Table of contents

- [1. Objective](#1-objective)
- [2. Problem statement](#2-problem-statement)
- [3. Solution architecture](#3-solution-architecture)
- [4. Infrastructure](#4-infrastructure)
- [5. VPN addressing](#5-vpn-addressing)
- [6. Server configuration](#6-server-configuration)
- [7. Client configuration](#7-client-configuration)
- [8. Routing configuration](#8-routing-configuration)
- [9. Firewall configuration](#9-firewall-configuration)
- [10. Windows client implementation](#10-windows-client-implementation)
- [11. Troubleshooting summary](#11-troubleshooting-summary)
- [12. Validation performed](#12-validation-performed)
- [13. Final communication flow](#13-final-communication-flow)
- [14. Key findings](#14-key-findings)
- [Step-by-step implementation guide](#step-by-step-implementation-guide)
  - [Stage A — AWS EC2 server](#stage-a--aws-ec2-server)
  - [Stage B — Linux client](#stage-b--linux-client)
  - [Stage C — Windows client](#stage-c--windows-client)
  - [End-to-end checklist](#end-to-end-checklist)
- [Phase 1 log — Preparation, server configuration & deployment](#phase-1-log--preparation-server-configuration--deployment)
- [Phase 2 log — Linux client configuration, WireGuard integration & routing](#phase-2-log--linux-client-configuration-wireguard-integration--routing)

---

## 1. Objective

The organization firewall blocked native WireGuard UDP (51820) traffic, preventing on-premises virtual machines from connecting to the AWS VPN server. WSTunnel was implemented to encapsulate WireGuard UDP packets inside WebSocket over HTTPS (TCP 443), allowing VPN traffic to traverse restrictive firewalls while maintaining secure encrypted communication.

## 2. Problem statement

**Existing VPN architecture, before WSTunnel:**

```
Client
  │
  WireGuard (UDP 51820)
  │
  Internet
  │
  AWS VPN Server
```

**Issue**

- Organization firewall blocked:
  - UDP/51820
  - ICMP
- VPN tunnel could not establish.
- No administrative control over the organization's firewall.

**Requirement**

Encapsulate VPN traffic inside HTTPS traffic that is typically permitted by enterprise firewalls.

## 3. Solution architecture

**Server side** — WSTunnel terminates the WebSocket/TLS connection on EC2 and hands the decoded UDP stream to the local WireGuard server:

```
Internet
  │
  HTTPS (TCP 443)
  │
  ┌────────────────────┐
  │  AWS EC2            │
  │  WSTunnel Server     │
  └────────────────────┘
  │
  localhost:51820
  │
  WireGuard Server
  │
  ┌───────────────┬───────────────┬───────────────┐
<vpn_subnet_1_range>   <vpn_subnet_2_range>   <vpn_subnet_3_range>
```

**Client side** — WireGuard never talks to AWS directly; it talks to WSTunnel on localhost, which does the real network hop:

```
WireGuard Client
  │
  localhost:51820
  │
  WSTunnel Client
  │
  HTTPS (443)
  │
  AWS EC2
```

## 4. Infrastructure

**AWS EC2 VPN server**

| Component | Configuration |
|---|---|
| OS | Ubuntu |
| Public IP | <public_ip> |
| WSTunnel | Server mode |
| WireGuard | Server |
| HTTPS Port | TCP 443 |
| VPN Port | UDP 51820 |
| Firewall | nftables |

**Client**

Implemented on:
- Ubuntu Linux
- Windows Server

Components:
- WireGuard Client
- WSTunnel Client

## 5. VPN addressing

**VPN server networks:** `<vpn_subnet_1_gateway>/24`, `<vpn_subnet_2_gateway>/24`, `<vpn_subnet_3_gateway>/24`

**Configured clients**

| Client | VPN IP |
|---|---|
| Web Server | <client_vpn_ip_1> |
| Web Server-2 | <client_vpn_ip_2> |
| Honeypot | <client_vpn_ip_3> |
| Service VM | <client_vpn_ip_4> |
| MISP | <client_vpn_ip_5> |
| SOC Core | <client_vpn_ip_6> |
| IDS | <client_vpn_ip_7> |
| AD-DS | <client_vpn_ip_8> |

## 6. Server configuration

**WireGuard**

- Server interface: `wg0`
- Listening: UDP 51820
- Addresses: `<vpn_subnet_1_gateway>/24`, `<vpn_subnet_2_gateway>/24`, `<vpn_subnet_3_gateway>/24`

Each VPN client was configured as a WireGuard peer using its public key and corresponding `/32` AllowedIPs entry.

**WSTunnel server** — installed as a systemd service.

```bash
wstunnel server \
  --restrict-to localhost:51820 \
  wss://0.0.0.0:443
```

Purpose:
- Listen on HTTPS port 443
- Accept WebSocket connections
- Forward decrypted UDP packets to localhost WireGuard server

## 7. Client configuration

**WireGuard**

- Endpoint: `127.0.0.1:51820`
- Reason: WireGuard communicates locally with WSTunnel instead of directly with AWS.

**WSTunnel client**

```bash
wstunnel client \
  -L udp://51820:localhost:51820?timeout_sec=0 \
  wss://<public_ip>:443
```

```
WireGuard
  │
  localhost:51820
  │
  WSTunnel
  │
  HTTPS 443
  │
  AWS
```

## 8. Routing configuration

**Initially**

```
AllowedIPs = 0.0.0.0/0
```

This caused:
- Entire system traffic routed into VPN
- WSTunnel HTTPS packets attempted to traverse the VPN they were responsible for establishing
- Routing loop
- Connection timeouts

**Solution**

Added a static route before bringing up WireGuard:

```bash
ip route add <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
```

**Result**

Traffic destined for the AWS public IP bypassed the VPN and reached WSTunnel directly, while all other traffic continued through the VPN.

## 9. Firewall configuration

**AWS security group — allowed**

| Protocol | Port |
|---|---|
| SSH | 22 |
| HTTPS | 443 |
| WireGuard UDP | 51820 |
| HTTP | 80 |

**nftables**

Configured to permit:
- SSH
- ICMP
- WireGuard UDP
- WSTunnel HTTPS
- VPN forwarding
- NAT masquerading

The HTTPS rule was added after discovering inbound TCP 443 traffic was being dropped.

## 10. Windows client implementation

**Components**

- WireGuard Windows
- WSTunnel
- NSSM (service manager)

**Initial issue**

WSTunnel failed to start with:

```
VCRUNTIME140.dll missing
```

**Resolution**

Installed Microsoft Visual C++ Redistributable (2015–2022 x64), after which WSTunnel executed successfully.

**NSSM service** — created to launch WSTunnel automatically at startup:

```bash
wstunnel.exe client \
  -L udp://51820:localhost:51820?timeout_sec=0 \
  wss://<public_ip>:443
```

## 11. Troubleshooting summary

| Issue | Root cause | Resolution |
|---|---|---|
| WSTunnel TCP timeout | TCP 443 blocked by nftables | Allowed HTTPS traffic |
| curl to public IP timed out | Firewall blocking HTTPS | Updated nftables |
| Invalid request from curl | HTTP request sent instead of WebSocket upgrade | Expected behavior; server confirmed reachable |
| WSTunnel connection timeout | HTTPS traffic routed into VPN due to AllowedIPs=0.0.0.0/0 | Added static route for AWS public IP |
| Server rejected destination | `--restrict-to` configuration mismatch | Corrected server restriction to `localhost:51820` |
| Windows WSTunnel service failed | Missing Visual C++ runtime | Installed VC++ Redistributable |
| NSSM service misconfiguration | Service created without proper arguments | Recreated service with correct client command |
| WireGuard handshake absent | WSTunnel transport unavailable | Resolved after HTTPS connectivity and routing fixes |

## 12. Validation performed

**Server**

- Verified WSTunnel service status.
- Confirmed WireGuard interface operational.
- Checked listening ports (443, 51820).
- Validated nftables rules.
- Tested HTTPS locally and via public IP.

**Client**

- Confirmed WSTunnel service started.
- Verified HTTPS connectivity to AWS.
- Confirmed WireGuard handshake.
- Verified VPN IP assignment.
- Tested inter-VPN communication.

## 13. Final communication flow

```
Application
  │
  WireGuard Client
  │
  127.0.0.1:51820 (UDP)
  │
  WSTunnel Client
  │
  WebSocket over TLS · TCP 443
  │
  Internet
  │
  AWS EC2
  │
  WSTunnel Server
  │
  localhost:51820 (UDP)
  │
  WireGuard Server
  │
  VPN Network
  <vpn_subnet_1>/24 · <vpn_subnet_2>/24 · <vpn_subnet_3>/24
```

## 14. Key findings

- WSTunnel effectively bypassed organizational restrictions on UDP by tunneling WireGuard traffic through HTTPS (TCP 443).
- A full-tunnel WireGuard configuration (`AllowedIPs = 0.0.0.0/0`) introduced a routing loop that prevented WSTunnel from reaching the AWS endpoint; a static host route resolved this issue.
- The WSTunnel server's `--restrict-to` option must explicitly permit the intended local UDP destination (`localhost:51820`) to allow forwarding to WireGuard.
- curl responses of "Invalid request" confirmed the HTTPS listener was reachable, but they did not validate WSTunnel functionality because WSTunnel requires a WebSocket upgrade request rather than a standard HTTP request.
- On Windows, the Microsoft Visual C++ Runtime is a mandatory dependency for `wstunnel.exe`.
- Hosting WSTunnel as a persistent service (systemd on Linux, NSSM on Windows) ensured automatic startup and reliable operation.
- The final design successfully enabled Linux and Windows clients to join the private VPN over TCP 443 without exposing WireGuard UDP to the internet, providing a secure and firewall-friendly transport layer.

---

## Step-by-step implementation guide

A straight-through build order — server first, then Linux client, then Windows client.

**Before you start:** a working WireGuard VPN already deployed on the EC2 host (`wg0`, UDP 51820) · root/sudo on server and clients · AWS security group open on 443 · the WSTunnel v10.6.2 release binary.

**You will change:** nftables (open 443) · the WireGuard client's `Endpoint` (→ `127.0.0.1`) · the client's routing table (static host route) · two new systemd/NSSM services.

### Stage A — AWS EC2 server

**S1. Confirm WireGuard is already up**

Don't touch WSTunnel until the base VPN is verified healthy.

```bash
sudo wg show
# expect: interface wg0, listening port 51820, peers listed
```

*Why: if WireGuard itself is broken, layering a tunnel on top only hides the real problem.*

**S2. Free up port 443**

```bash
sudo ss -tlnp | grep :443
# expect: no output
```

If downloading the release hits `Temporary failure in name resolution`, WireGuard's own DNS config is likely interfering — drop the interface, fetch the binary, then continue:

```bash
sudo wg-quick down wg0
dig www.google.com   # confirm DNS works again
```

**S3. Install WSTunnel**

```bash
cd /tmp
wget https://github.com/erebe/wstunnel/releases/download/v10.6.2/wstunnel_10.6.2_linux_amd64.tar.gz
tar -xzf wstunnel_10.6.2_linux_amd64.tar.gz
sudo mv wstunnel /usr/local/bin/
sudo chmod +x /usr/local/bin/wstunnel
wstunnel --version   # wstunnel-cli 10.6.2
```

*Why pin v10.6.2: GitHub's "latest" redirect can point past a filename you already have cached — verify the real tag first with the GitHub API if `wget` 404s.*

**S4. Test the server, then open the firewall**

```bash
sudo wstunnel server \
  --restrict-to localhost:51820 \
  wss://0.0.0.0:443
```

A local `curl -vk https://127.0.0.1:443` returning `HTTP/2 400 Invalid request` means it's working — WSTunnel rejects plain GETs by design. If a remote `curl` from off-box times out, check nftables, not just the AWS security group:

```bash
sudo nft list ruleset            # look for policy drop + missing tcp/443
sudo nft add rule inet filter input tcp dport 443 accept
```

**S5. Run it as a service**

Stop the foreground process (Ctrl-C) and create `/etc/systemd/system/wstunnel.service`:

```ini
[Unit]
Description=WSTunnel Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel server \
  --restrict-to localhost:51820 \
  wss://0.0.0.0:443
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wstunnel
sudo systemctl status wstunnel      # Active: active (running)
sudo journalctl -u wstunnel         # Listening on 0.0.0.0:443 · Server ready.
```

### Stage B — Linux client

**L1. Install WSTunnel**

```bash
uname -m && cat /etc/os-release
cd /tmp
wget https://github.com/erebe/wstunnel/releases/download/v10.6.2/wstunnel_10.6.2_linux_amd64.tar.gz
tar -xzf wstunnel_10.6.2_linux_amd64.tar.gz
sudo mv wstunnel /usr/local/bin/
sudo chmod +x /usr/local/bin/wstunnel
```

**L2. Point WireGuard at localhost, start the WSTunnel client**

In the client's WireGuard config, change `Endpoint = <public_ip>:51820` to `Endpoint = 127.0.0.1:51820`. Then, before bringing WireGuard up, start the tunnel:

```bash
wstunnel client \
  -L udp://51820:127.0.0.1:51820?timeout_sec=0 \
  wss://<public_ip>:443
```

```bash
sudo ss -lunp | grep 51820   # expect 127.0.0.1:51820, process wstunnel
```

**L3. Fix the routing loop before going full-tunnel**

If the peer's `AllowedIPs = 0.0.0.0/0`, WSTunnel's own HTTPS traffic will try to ride the VPN it's building. Add a static host route for the AWS IP through the physical interface first:

```bash
sudo ip route add <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
```

Persist it so it survives every WireGuard restart — add to `wg0.conf`:

```ini
PostUp = ip route add <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
PostDown = ip route del <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
```

*Why this order matters: do this before the first `wg-quick up` with a full-tunnel config, or you'll chase a "0 B received" handshake failure that's actually a routing loop, not a WireGuard problem.*

**L4. Run the client as a service**

`/etc/systemd/system/wstunnel.service`:

```ini
[Unit]
Description=WSTunnel Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel client \
  -L udp://51820:127.0.0.1:51820?timeout_sec=0 \
  wss://<public_ip>:443
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wstunnel
```

**L5. Bring up the VPN and verify**

```bash
sudo wg-quick down wg0 && sudo wg-quick up wg0
sudo wg show          # latest handshake present, RX/TX both increasing
ping <vpn_subnet_1_gateway>    # successful
```

### Stage C — Windows client

**W1. Install components**

Install WireGuard for Windows, `wstunnel.exe`, and NSSM. If WSTunnel fails to launch with `VCRUNTIME140.dll missing`, install the **Microsoft Visual C++ Redistributable (2015–2022 x64)** — it's a hard dependency, not optional.

In the WireGuard tunnel config, set `Endpoint = 127.0.0.1:51820`, same as the Linux client.

**W2. Configure & run as a service**

Use NSSM to register the client so it starts automatically:

```powershell
wstunnel.exe client `
  -L udp://51820:localhost:51820?timeout_sec=0 `
  wss://<public_ip>:443
```

Point the NSSM service's Application/Arguments fields at that exact command, then start the service and activate the WireGuard tunnel.

### End-to-end checklist

- [ ] **Server:** `systemctl status wstunnel` is active, `ss -tlnp` shows 443 listening, `wg show` shows the interface up.
- [ ] **Firewall:** nftables allows tcp/443 inbound; AWS security group allows 443, 51820, 22, 80.
- [ ] **Client transport:** `nc -vz <server-ip> 443` succeeds; `ss -lunp | grep 51820` shows WSTunnel bound to localhost.
- [ ] **Routing:** a static `/32` route to the server's public IP exists via the physical interface, persisted through `PostUp`/`PostDown`.
- [ ] **Handshake:** `wg show` on the client shows a recent handshake with both RX and TX increasing, not just TX.
- [ ] **Reachability:** `ping` to a VPN-side address (e.g. `<vpn_subnet_1_gateway>`) succeeds.
- [ ] **Persistence:** both server and client WSTunnel processes are registered services (systemd / NSSM), not foreground shells.

---

## Phase 1 log — Preparation, server configuration & deployment

The org's firewall allowed only TCP 80, TCP 443, and SSH — UDP 51820 (WireGuard) was blocked. Rather than replace WireGuard, WSTunnel was placed between client and server so only the *transport* changed, not the VPN topology:

```
WireGuard
  │
  UDP localhost:51820
  │
  WSTunnel
  │
  HTTPS (TCP 443)
  │
  Internet
  │
  AWS EC2
  │
  WSTunnel
  │
  UDP localhost:51820
  │
  WireGuard Server
```

This implementation required no modification to the existing VPN topology — only the transport mechanism changed.

**1. Existing infrastructure**

| Item | Value |
|---|---|
| OS | Ubuntu 22.04 LTS |
| Public IP | <public_ip> |
| WireGuard interface | wg0 |
| WireGuard port | UDP 51820 |
| VPN networks | <vpn_subnet_1>/24, <vpn_subnet_2>/24, <vpn_subnet_3>/24 |

**2. Verify existing WireGuard**

Confirmed before touching anything: interface active, listening on UDP 51820, peers connected, routing operational. No config changes made at this stage.

```bash
sudo wg show
```

Output confirmed `interface: wg0`, `listening port: 51820`, and all configured peers.

**3. Verify HTTPS port availability**

WSTunnel needs to own TCP 443, so confirm nothing else is bound to it first:

```bash
sudo ss -tlnp | grep :443
```

Expected: no output → port 443 is available.

**4. DNS issue encountered**

While downloading WSTunnel:

```bash
wget https://github.com/erebe/wstunnel/releases/latest/download/...
```

Error: `Temporary failure in name resolution`

Verification:

```bash
ping 8.8.8.8   # worked successfully
cat /etc/resolv.conf
```

Therefore: internet connectivity existed, but DNS resolution failed.

Resolved by temporarily bringing WireGuard down:

```bash
sudo wg-quick down wg0
```

DNS immediately started functioning. Verification:

```bash
dig www.google.com   # successful
```

**Finding:** WireGuard DNS configuration was temporarily interfering with external name resolution during installation.

**5. Install WSTunnel**

Initially attempted:

```bash
wget https://github.com/erebe/wstunnel/releases/latest/download/wstunnel_10.5.5_linux_amd64.tar.gz
```

Result: `404 Not Found`

Reason: GitHub redirected "latest" release to `v10.6.2` while the filename still referenced `10.5.5`.

Latest release verified using:

```bash
wget -qO- https://api.github.com/repos/erebe/wstunnel/releases/latest \
  | grep browser_download_url
```

Correct package: `wstunnel_10.6.2_linux_amd64.tar.gz`

Installation:

```bash
cd /tmp
wget https://github.com/erebe/wstunnel/releases/download/v10.6.2/wstunnel_10.6.2_linux_amd64.tar.gz
tar -xzf wstunnel_10.6.2_linux_amd64.tar.gz
sudo mv wstunnel /usr/local/bin/
sudo chmod +x /usr/local/bin/wstunnel
```

Verification:

```bash
wstunnel --version
# wstunnel-cli 10.6.2
```

**6. Study WSTunnel options**

```bash
wstunnel server --help
```

Important options identified:

| Option | Purpose |
|---|---|
| `--restrict-to` | Restrict destination forwarding |
| `--tls-certificate` | Custom TLS certificate |
| `--tls-private-key` | Private key |
| `--restrict-http-upgrade-path-prefix` | Optional authentication path |
| `--dns-resolver` | DNS configuration |

Decision: use embedded self-signed certificate for initial deployment.

**7. Start WSTunnel server**

```bash
sudo wstunnel server \
  --restrict-to localhost:51820 \
  wss://0.0.0.0:443
```

Purpose:
- Listen on HTTPS port 443
- Accept WebSocket clients
- Forward UDP traffic only to localhost WireGuard server

**8. Verify WSTunnel server**

Check listening socket:

```bash
sudo ss -tlnp | grep :443
# expect: LISTEN 0.0.0.0:443
```

Verify locally:

```bash
curl -vk https://127.0.0.1:443
```

Response: `HTTP/2 400 Invalid request`

**Finding:** this did not indicate an error. WSTunnel expects `HTTP Upgrade: websocket`; a normal HTTP GET request is intentionally rejected. Therefore "Invalid request" confirmed HTTPS reachable, TLS functioning, and the WSTunnel server running.

**9. External HTTPS verification**

```bash
curl -vk https://<public_ip>:443
```

Timed out. Investigation revealed the AWS Security Group already permitted TCP 443. Root cause: the host firewall.

**10. nftables investigation**

```bash
sudo nft list ruleset
```

- Input policy: `policy drop`
- Allowed: SSH, ICMP, UDP 51820
- Missing: TCP 443

Therefore HTTPS packets never reached WSTunnel.

**11. Firewall modification**

Added HTTPS rule:

```bash
sudo nft add rule inet filter input tcp dport 443 accept
```

Verification:

```bash
curl -vk https://<public_ip>:443
# now received: HTTP/2 400 Invalid request
```

**Finding:** the server was now reachable from the Internet.

**12. Convert WSTunnel into a service**

Created `/etc/systemd/system/wstunnel.service`:

```ini
[Unit]
Description=WSTunnel Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel server \
  --restrict-to localhost:51820 \
  wss://0.0.0.0:443
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable wstunnel
sudo systemctl start wstunnel
sudo systemctl status wstunnel
# Active: active (running)
```

**13. Validation**

```bash
sudo journalctl -u wstunnel
```

Confirmed:

```
Starting WSTunnel Server
Listening on 0.0.0.0:443
Server ready.
```

**Phase 1 summary**

Completed:
- Verified existing WireGuard VPN
- Identified firewall restrictions
- Resolved DNS issue during installation
- Installed WSTunnel v10.6.2
- Studied server configuration options
- Deployed WSTunnel server
- Opened TCP 443 in nftables
- Validated HTTPS listener
- Created persistent systemd service
- Confirmed WSTunnel server operational

Important findings:
- WSTunnel does not replace WireGuard; it encapsulates WireGuard UDP traffic inside WebSocket over HTTPS.
- A response of "HTTP/2 400 Invalid request" from curl is expected because WSTunnel requires a WebSocket upgrade request, not a standard HTTP GET.
- The AWS Security Group already allowed TCP 443; the actual blocker was the local nftables firewall.
- Running WSTunnel as a systemd service ensures automatic startup after reboot.

---

## Phase 2 log — Linux client configuration, WireGuard integration & routing

**Objective:** configure the Linux VPN client to connect to the AWS WSTunnel server over HTTPS, forward local WireGuard traffic through WSTunnel, preserve the existing WireGuard VPN architecture, and bypass organizational firewall restrictions.

**1. Client environment**

| Component | Value |
|---|---|
| OS | Ubuntu 22.04 LTS |
| Host | <client_hostname> |
| VPN Address | <client_vpn_ip_1> |
| AWS Server | <public_ip> |
| WireGuard Interface | wg0 |

**2. Install WSTunnel**

Determine system architecture:

```bash
uname -m
cat /etc/os-release
# x86_64
# Ubuntu 22.04 LTS
```

Download:

```bash
cd /tmp
wget https://github.com/erebe/wstunnel/releases/download/v10.6.2/wstunnel_10.6.2_linux_amd64.tar.gz
tar -xzf wstunnel_10.6.2_linux_amd64.tar.gz
sudo mv wstunnel /usr/local/bin/
sudo chmod +x /usr/local/bin/wstunnel
```

Verify:

```bash
wstunnel --version
# wstunnel-cli 10.6.2
```

**3. Configure WireGuard**

Original endpoint: `Endpoint = <public_ip>:51820`

Modified: `Endpoint = 127.0.0.1:51820`

Reason: WireGuard no longer communicates directly with AWS. Instead:

```
WireGuard
  │
  127.0.0.1:51820
  │
  WSTunnel
  │
  HTTPS
  │
  AWS
```

**4. Start WSTunnel client**

```bash
wstunnel client \
  -L udp://51820:127.0.0.1:51820?timeout_sec=0 \
  wss://<public_ip>:443
```

| Option | Description |
|---|---|
| `client` | Client mode |
| `-L` | Local UDP forward |
| `udp://51820` | Listen locally on UDP 51820 |
| `127.0.0.1:51820` | Forward to local WireGuard |
| `timeout_sec=0` | Never timeout UDP |
| `wss://` | Secure WebSocket |
| `:443` | HTTPS transport |

**5. Verify WSTunnel**

Socket:

```bash
sudo ss -lunp | grep 51820
# expect: 127.0.0.1:51820
```

Process: `wstunnel`

**6. Configure WSTunnel as a service**

Create `/etc/systemd/system/wstunnel.service`:

```ini
[Unit]
Description=WSTunnel Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel client \
  -L udp://51820:127.0.0.1:51820?timeout_sec=0 \
  wss://<public_ip>:443
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable wstunnel
sudo systemctl start wstunnel
sudo systemctl status wstunnel
```

**7. Start WireGuard**

Bring interface up:

```bash
sudo wg-quick up wg0
sudo wg show
```

Initially:

```
Transfer:
0 B received
```

Packets only transmitted. Meaning: WireGuard attempted communication, but no response was received.

**8. Initial troubleshooting**

Logs:

```bash
sudo journalctl -u wstunnel -f
```

Observed:

```
Opening TCP connection
Cannot connect
Timed out
```

Meaning: HTTPS transport was failing.

**9. Verify HTTPS connectivity**

```bash
nc -vz <public_ip> 443
```

Initially: timeout. Later: connection succeeded.

**Finding:** transport layer eventually became reachable after firewall corrections on the server.

**10. Major routing problem**

Client configuration contained:

```
AllowedIPs = 0.0.0.0/0
```

Purpose: full tunnel VPN.

Unexpected effect: all traffic (WireGuard → WSTunnel → AWS). Problem: WSTunnel itself also attempted to use the VPN.

Result:

```
WSTunnel
  ↓
VPN
  ↓
WSTunnel
  ↓
VPN
  ↓
Infinite routing loop
```

Symptoms:
- HTTPS timeout
- No handshake
- Increasing transmitted bytes
- Zero received bytes

**11. Investigation**

Routes:

```bash
ip route
```

Observed: `default via physical gateway` — after WireGuard, the entire default route was redirected into the VPN.

Confirmed by:

```bash
nc -vz <public_ip> 443
```

Succeeded only after bringing WireGuard down.

**Finding:** WireGuard itself caused WSTunnel connectivity failure.

**12. Solution**

Create a static route before the VPN starts:

```bash
sudo ip route add <public_ip>/32 \
  via <physical_gateway_ip> \
  dev <physical_interface>
```

Verify:

```bash
ip route
# expect: <public_ip> via <physical_gateway_ip>
```

Now:

```
AWS Server
  ↓
Physical NIC   (NOT WireGuard)
```

**13. Integrate with WireGuard**

Added to `wg0.conf`:

```ini
PostUp = ip route add <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
PostDown = ip route del <public_ip>/32 via <physical_gateway_ip> dev <physical_interface>
```

Reason: automatically preserve the route whenever WireGuard starts.

**14. Restart VPN**

```bash
sudo wg-quick down wg0
sudo wg-quick up wg0
```

Check routes:

```bash
ip route
# expect: default, <public_ip>, VPN routes
```

**15. Verify WireGuard**

```bash
sudo wg show
```

Now: latest handshake present, Transfer RX increasing, Transfer TX increasing. Handshake successful.

**16. Verify communication**

```bash
ping <vpn_subnet_1_gateway>
```

Result: successful. VPN traffic now flowed:

```
WireGuard → localhost → WSTunnel → HTTPS → AWS → WireGuard → VPN
```

**17. Final client architecture**

```
Application
  │
  WireGuard
  │
  127.0.0.1:51820
  │
  WSTunnel Client
  │
  HTTPS (443)
  │
  Internet
  │
  AWS EC2
  │
  WSTunnel Server
  │
  127.0.0.1:51820
  │
  WireGuard Server
  │
  VPN Networks
```

**18. Validation commands**

```bash
# WSTunnel
sudo systemctl status wstunnel
sudo journalctl -u wstunnel
sudo ss -lunp | grep 51820

# WireGuard
sudo wg show
ip route

# Connectivity
nc -vz <public_ip> 443
ping <vpn_subnet_1_gateway>
```

**Phase 2 key findings**

- WSTunnel transparently encapsulated WireGuard UDP traffic into HTTPS (TCP 443), allowing VPN operation through restrictive enterprise firewalls.
- The most significant issue was the interaction between a full-tunnel WireGuard configuration (`AllowedIPs = 0.0.0.0/0`) and WSTunnel: without an explicit host route, WSTunnel attempted to reach the AWS endpoint through the VPN it was responsible for establishing, creating a routing loop.
- Adding a static host route for the AWS public IP through the physical gateway resolved the loop while retaining full-tunnel behavior for all other traffic.
- Persisting the host route via `PostUp` and `PostDown` in `wg0.conf` ensured the configuration survived interface restarts.
- Once the route was corrected, WSTunnel connected successfully, WireGuard established a handshake, and VPN communication between the client and AWS server operated normally over HTTPS.

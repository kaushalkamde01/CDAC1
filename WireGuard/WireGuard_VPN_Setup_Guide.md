# WireGuard VPN — Setup & Configuration Guide

> A step-by-step guide to deploying a WireGuard VPN gateway that interconnects multiple private networks, with an optional HTTPS transport (WSTunnel) for environments where UDP is blocked.
>
> All addresses, keys, and interface names below are **placeholders**. Replace every `<PLACEHOLDER>` with the real value for your environment before running a command.

---

## Table of Contents

1. [Objective & Architecture](#1-objective--architecture)
2. [Placeholder Reference](#2-placeholder-reference)
3. [Infrastructure & Addressing Scheme](#3-infrastructure--addressing-scheme)
4. [Cryptography](#4-cryptography)
5. [Phase 1 — Server Configuration](#5-phase-1--server-configuration)
6. [Phase 2 — Linux Client Configuration](#6-phase-2--linux-client-configuration)
7. [Phase 3 — WSTunnel Integration (HTTPS Transport)](#7-phase-3--wstunnel-integration-https-transport)
8. [Phase 4 — Windows Client Configuration](#8-phase-4--windows-client-configuration)
9. [Validation Checklist](#9-validation-checklist)
10. [Troubleshooting](#10-troubleshooting)
11. [Design Decisions, Advantages & Limitations](#11-design-decisions-advantages--limitations)

---

## 1. Objective & Architecture

Deploy a lightweight, secure Layer‑3 VPN using WireGuard to interconnect distributed infrastructure over a single encrypted network, gatewayed through one cloud-hosted server.

**Goals:**
- Secure communication between cloud and on-premises infrastructure
- Eliminate direct exposure of internal services
- Enable private access to internal components
- Allow communication between multiple isolated internal networks
- Provide a centralized VPN gateway

**Architecture:**

```
                     Internet
                        │
              ┌─────────────────────┐
              │   Cloud VPN Server   │
              │  Public IP: <PUBLIC_IP> │
              │  WireGuard Interface: wg0 │
              └─────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │                │                │
 <SUBNET_A>        <SUBNET_B>        <SUBNET_C>
 (Services Net)     (Core Net)       (Reserved / Future)
```

One WireGuard interface can be assigned multiple addresses, letting a single server act as gateway for several logical VPN networks at once.

---

## 2. Placeholder Reference

Use this table to substitute in your own values wherever a placeholder appears in this guide.

| Placeholder | Meaning | Example (do not use in production) |
|---|---|---|
| `<PUBLIC_IP>` | Public IP of the VPN server | 203.0.113.10 |
| `<WG_PORT>` | WireGuard UDP listen port | 51820 |
| `<WSTUNNEL_PORT>` | Port used for HTTPS-tunneled WireGuard | 443 |
| `<SERVER_NIC>` | Server's outbound network interface name | ens5 |
| `<CLIENT_NIC>` | Client's physical network interface name | ens33 / Ethernet0 |
| `<CLIENT_GATEWAY_IP>` | Client's local/physical default gateway | 10.0.0.1 |
| `<SUBNET_A>` / `<SUBNET_B>` / `<SUBNET_C>` | VPN subnets served by the gateway | 10.10.0.0/24, 10.20.0.0/24, 10.30.0.0/24 |
| `<SERVER_VPN_IP_A/B/C>` | Server's VPN address on each subnet | 10.10.0.1, 10.20.0.1, 10.30.0.1 |
| `<CLIENT_VPN_IP>` | A given client's assigned VPN address | 10.10.0.10 |
| `<DNS_SERVER_IP>` | Internal DNS server address handed to clients | 10.20.0.40 |
| `<SERVER_PRIVATE_KEY>` / `<SERVER_PUBLIC_KEY>` | Server's WireGuard key pair | — |
| `<CLIENT_PRIVATE_KEY>` / `<CLIENT_PUBLIC_KEY>` | A given client's WireGuard key pair | — |
| `<HOSTNAME>` | Any internal hostname used for DNS testing | app01.internal |

---

## 3. Infrastructure & Addressing Scheme

| Item | Value |
|---|---|
| Platform | Cloud VM (e.g. AWS EC2) |
| OS | Ubuntu 22.04 LTS |
| VPN software | WireGuard |
| Interface | `wg0` |
| Transport | UDP |
| Listening port | `<WG_PORT>` |
| Public endpoint | `<PUBLIC_IP>` |
| Clients | Linux and Windows — each maintains its own key pair |

**Server VPN addresses** (all bound to a single `wg0` interface):

| VPN Address | Subnet | Purpose |
|---|---|---|
| `<SERVER_VPN_IP_A>` | `<SUBNET_A>` | Services network |
| `<SERVER_VPN_IP_B>` | `<SUBNET_B>` | Core / internal services network |
| `<SERVER_VPN_IP_C>` | `<SUBNET_C>` | Reserved / future expansion |

> Assign each peer (client) a **unique static `/32` address** within its subnet. This keeps routing deterministic and avoids address conflicts.

---

## 4. Cryptography

WireGuard provides the following automatically, via the Noise protocol framework:

- Public-key authentication
- Private-key encryption
- Modern authenticated encryption
- Perfect Forward Secrecy

Each peer holds a **private key** (kept secret, never shared) and a **public key** (shared with the peer it connects to). The server identifies every client exclusively by its public key — there are no shared secrets, usernames, or passwords at this layer.

---

## 5. Phase 1 — Server Configuration

### Step 1 — Open required firewall/security-group ports

Before installing anything, allow the following inbound traffic on your cloud provider's firewall (e.g. AWS Security Group):

| Protocol | Port | Purpose |
|---|---|---|
| UDP | `<WG_PORT>` | WireGuard VPN |
| TCP | 22 | SSH |
| TCP | `<WSTUNNEL_PORT>` | WSTunnel (Phase 3, optional) |
| TCP | 80 | Optional HTTP |

### Step 2 — Install WireGuard

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install wireguard -y
wg --version
```

### Step 3 — Enable IP forwarding

Required so the server can route packets between VPN peers and subnets.

```bash
# temporary
sudo sysctl -w net.ipv4.ip_forward=1
```

```bash
# permanent
sudo nano /etc/sysctl.conf
# uncomment or add:
net.ipv4.ip_forward=1

sudo sysctl -p
cat /proc/sys/net/ipv4/ip_forward   # expect: 1
```

### Step 4 — Generate server keys

```bash
sudo mkdir -p /etc/wireguard
cd /etc/wireguard
wg genkey | sudo tee server_private.key | wg pubkey | sudo tee server_public.key
sudo chmod 600 server_private.key
cat server_public.key
```

> Save the printed public key — every client configuration will need it as `<SERVER_PUBLIC_KEY>`.

### Step 5 — Create the server interface config

Note the multiple `Address` values — this is what lets one interface serve several logical networks.

```ini
# /etc/wireguard/wg0.conf

[Interface]
Address = <SERVER_VPN_IP_A>/24,<SERVER_VPN_IP_B>/24,<SERVER_VPN_IP_C>/24
ListenPort = <WG_PORT>
PrivateKey = <SERVER_PRIVATE_KEY>
DNS = <DNS_SERVER_IP>
```

### Step 6 — Add a `[Peer]` block for every client

Each client gets its own unique `/32` VPN address.

```ini
# Example peer entries — append to wg0.conf

# Client: core-service-01
[Peer]
PublicKey = <CLIENT_PUBLIC_KEY>
AllowedIPs = <CLIENT_VPN_IP>/32

# Client: dns-server
[Peer]
PublicKey = <CLIENT_PUBLIC_KEY>
AllowedIPs = <DNS_SERVER_IP>/32
```

> ⚠️ **Never** set `AllowedIPs = <SUBNET_A>` (a full subnet) on an individual peer — this causes ambiguous routing. Always assign a single host `/32` per client.

### Step 7 — Configure the firewall (nftables) and NAT

```nft
# /etc/nftables.conf

table inet filter {
  chain input {
    type filter hook input priority filter;
    policy drop;
    iif "lo" accept
    ct state established,related accept
    iifname "wg0" accept
    tcp dport 22 accept
    udp dport <WG_PORT> accept
    ip protocol icmp accept
  }
  chain forward {
    type filter hook forward priority filter;
    policy drop;
    iifname "wg0" accept
    ct state established,related accept
  }
  chain output {
    type filter hook output priority filter;
    policy accept
  }
}

table ip nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    policy accept;
    oifname "<SERVER_NIC>" masquerade
  }
}
```

```bash
sudo systemctl enable nftables
sudo systemctl restart nftables
sudo nft list ruleset
```

### Step 8 — Start WireGuard

```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
# or manually:
sudo wg-quick up wg0
```

### Step 9 — Verify the server

```bash
ip addr show wg0
sudo wg show
```

Peers will appear with **no handshake** until a client actually connects — that's expected at this stage.

### Reference — useful server commands

```bash
sudo wg-quick up wg0        # bring up
sudo wg-quick down wg0      # bring down
sudo systemctl restart wg-quick@wg0
sudo systemctl status wg-quick@wg0
sudo journalctl -u wg-quick@wg0
```

---

## 6. Phase 2 — Linux Client Configuration

### Step 1 — Install WireGuard on the client

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install wireguard resolvconf -y
wg --version
```

### Step 2 — Generate client keys

```bash
cd /etc/wireguard
wg genkey | sudo tee client_private.key | wg pubkey | sudo tee client_public.key
sudo chmod 600 client_private.key
cat client_public.key
```

Copy the printed public key to the server as `<CLIENT_PUBLIC_KEY>`.

### Step 3 — Register this client on the server

```ini
# on the server — /etc/wireguard/wg0.conf

[Peer]
PublicKey = <CLIENT_PUBLIC_KEY>
AllowedIPs = <CLIENT_VPN_IP>/32
```

```bash
# on the server
sudo systemctl restart wg-quick@wg0
```

### Step 4 — Write the client config

```ini
# /etc/wireguard/wg0.conf (on the client)

[Interface]
PrivateKey = <CLIENT_PRIVATE_KEY>
Address = <CLIENT_VPN_IP>/24
DNS = <DNS_SERVER_IP>

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
Endpoint = <PUBLIC_IP>:<WG_PORT>
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

**Why these parameters:**

| Parameter | Purpose |
|---|---|
| `DNS = <DNS_SERVER_IP>` | Routes internal hostname resolution through your internal DNS |
| `AllowedIPs = 0.0.0.0/0` | Full-tunnel VPN — all client traffic routes through WireGuard |
| `PersistentKeepalive = 25` | Sends a keepalive every 25s to maintain NAT mappings behind routers/firewalls |
| `Endpoint` | Points at the server today; changes to `127.0.0.1:<WG_PORT>` after Phase 3 (WSTunnel) |

### Step 5 — Bring up the tunnel

```bash
sudo wg-quick up wg0
sudo systemctl enable wg-quick@wg0
```

### Step 6 — Verify connection & routing

```bash
ip addr show wg0
sudo wg show
ping <SERVER_VPN_IP_A>          # VPN gateway
ping <CLIENT_VPN_IP>            # another VPN client (cross-subnet)
ip route
```

Look for an increasing **latest handshake** and non-zero RX/TX transfer counters. A missing handshake usually means a problem with the server, keys, port, firewall, public IP, or client config.

### Step 7 — Verify DNS

```bash
cat /etc/resolv.conf
resolvectl status
nslookup <HOSTNAME>
dig <HOSTNAME>
```

Expected resolver: `<DNS_SERVER_IP>`.

---

## 7. Phase 3 — WSTunnel Integration (HTTPS Transport)

Use this phase only if your network blocks native WireGuard UDP traffic. WSTunnel encapsulates WireGuard's UDP packets inside HTTPS (TCP `<WSTUNNEL_PORT>`) so the same VPN survives restrictive firewalls — **the WireGuard addressing and peer configuration do not change, only the transport.**

### Step 1 — Install WSTunnel (on both server and client)

```bash
cd /tmp
wget https://github.com/erebe/wstunnel/releases/download/v10.6.2/wstunnel_10.6.2_linux_amd64.tar.gz
tar -xzf wstunnel_10.6.2_linux_amd64.tar.gz
sudo mv wstunnel /usr/local/bin/
sudo chmod +x /usr/local/bin/wstunnel
wstunnel --version
```

### Step 2 — Server: create the WSTunnel systemd service

```ini
# /etc/systemd/system/wstunnel.service

[Unit]
Description=WSTunnel Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel server \
  wss://0.0.0.0:<WSTUNNEL_PORT>
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

### Step 3 — Server: open the HTTPS port in nftables

```nft
tcp dport <WSTUNNEL_PORT> accept
```

```bash
sudo systemctl restart nftables
curl -vk https://<PUBLIC_IP>:<WSTUNNEL_PORT>
```

> An `HTTP 400 Invalid request` response here is **expected** — WSTunnel only accepts WebSocket upgrade requests, not plain GETs. A 400 confirms the service is reachable.

### Step 4 — Client: point WireGuard at localhost

```ini
# client wg0.conf — change

# before
Endpoint = <PUBLIC_IP>:<WG_PORT>

# after
Endpoint = 127.0.0.1:<WG_PORT>
```

### Step 5 — Client: create the WSTunnel client service

```ini
# /etc/systemd/system/wstunnel.service

[Unit]
Description=WSTunnel Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wstunnel client \
  -L udp://<WG_PORT>:127.0.0.1:<WG_PORT>?timeout_sec=0 \
  wss://<PUBLIC_IP>:<WSTUNNEL_PORT>
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
```

### Step 6 — Verify WSTunnel on both ends

```bash
sudo systemctl status wstunnel
sudo journalctl -u wstunnel -f
sudo ss -lunp | grep <WG_PORT>   # expect: 127.0.0.1:<WG_PORT>
```

### Step 7 — Understand the routing-loop problem

With `AllowedIPs = 0.0.0.0/0`, WireGuard installs a default route sending **everything** through `wg0` — including WSTunnel's own outbound HTTPS connection to the server. This creates an infinite loop: WSTunnel → wg0 → WSTunnel → wg0…

**Symptoms:** no handshake, continuous retransmissions, TX increasing while RX stays at zero, and the WSTunnel TCP connection timing out.

Confirm the diagnosis:

```bash
sudo wg-quick down wg0
nc -vz <PUBLIC_IP> <WSTUNNEL_PORT>   # succeeds with wg0 down

sudo wg-quick up wg0
nc -vz <PUBLIC_IP> <WSTUNNEL_PORT>   # times out with wg0 up
```

### Step 8 — Fix: static host route around the tunnel

Route traffic to the server's public IP via the physical interface so it never re-enters `wg0`.

```bash
# manual test
sudo ip route add <PUBLIC_IP>/32 via <CLIENT_GATEWAY_IP> dev <CLIENT_NIC>
ip route   # expect: <PUBLIC_IP> via <CLIENT_GATEWAY_IP> dev <CLIENT_NIC>
```

Make it permanent by adding it directly to the WireGuard config:

```ini
# append to wg0.conf

PostUp = ip route add <PUBLIC_IP>/32 via <CLIENT_GATEWAY_IP> dev <CLIENT_NIC>
PostDown = ip route del <PUBLIC_IP>/32 via <CLIENT_GATEWAY_IP> dev <CLIENT_NIC>
```

### Step 9 — Restart everything and validate

```bash
sudo systemctl restart wstunnel
sudo systemctl restart wg-quick@wg0
sudo wg show
ip route
ping <SERVER_VPN_IP_A>
sudo journalctl -u wstunnel
```

Expect a fresh handshake with growing RX/TX, the static host route present alongside the default route, and WSTunnel logs showing accepted WebSocket connections rather than timeouts.

---

## 8. Phase 4 — Windows Client Configuration

Same WireGuard architecture and the same WSTunnel-over-HTTPS transport, on Windows — WireGuard talks to WSTunnel locally over `127.0.0.1:<WG_PORT>`, and WSTunnel carries it out over TCP `<WSTUNNEL_PORT>`.

### Step 1 — Install the WireGuard for Windows client

Download and install the official client, launch it, create a new empty tunnel, and let it generate a key pair. Copy the generated public key as `<CLIENT_PUBLIC_KEY>`.

### Step 2 — Register the Windows peer on the server

```ini
# on the server — /etc/wireguard/wg0.conf

[Peer]
PublicKey = <CLIENT_PUBLIC_KEY>
AllowedIPs = <CLIENT_VPN_IP>/32
```

```bash
# on the server
sudo systemctl restart wg-quick@wg0
```

### Step 3 — Configure the Windows WireGuard tunnel

```ini
# WireGuard GUI — tunnel config

[Interface]
PrivateKey = <CLIENT_PRIVATE_KEY>
Address = <CLIENT_VPN_IP>/24
DNS = <DNS_SERVER_IP>

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
Endpoint = 127.0.0.1:<WG_PORT>
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

> The endpoint is `127.0.0.1:<WG_PORT>`, not the server's public IP — WSTunnel handles the real network hop.

### Step 4 — Install WSTunnel for Windows

Download the Windows AMD64 release and extract it to a known path, e.g. `C:\wstunnel\wstunnel.exe`.

### Step 5 — Test WSTunnel manually first

```cmd
C:\wstunnel\wstunnel.exe client ^
  -L udp://<WG_PORT>:127.0.0.1:<WG_PORT>?timeout_sec=0 ^
  wss://<PUBLIC_IP>:<WSTUNNEL_PORT>
```

If it starts cleanly with no errors, proceed to wrap it as a service.

### Step 6 — Wrap it as a Windows service with NSSM

```cmd
nssm install WSTunnel
```

| Field | Value |
|---|---|
| Application | `C:\wstunnel\wstunnel.exe` |
| Startup directory | `C:\wstunnel` |
| Arguments | `client -L udp://<WG_PORT>:127.0.0.1:<WG_PORT>?timeout_sec=0 wss://<PUBLIC_IP>:<WSTUNNEL_PORT>` |

```cmd
net start WSTunnel
```

### Step 7 — Known blocker: missing Visual C++ Runtime

If WSTunnel exits immediately with no logs and NSSM reports failures, the Microsoft Visual C++ Redistributable (2015–2022 x64) is likely missing.

**Fix:** install `VC_redist.x64.exe`, reboot the server, then restart the WSTunnel service.

### Step 8 — Allow the right traffic through Windows Firewall

- WireGuard
- WSTunnel
- Loopback (127.0.0.1)
- Outbound HTTPS (`<WSTUNNEL_PORT>`)

No inbound UDP `<WG_PORT>` rule is required — WireGuard only ever talks to WSTunnel locally.

### Step 9 — Validate

```cmd
sc query WSTunnel
ping <SERVER_VPN_IP_A>
ping <CLIENT_VPN_IP>
nslookup <HOSTNAME>
```

In the WireGuard GUI: tunnel active, latest handshake updating, RX/TX counters climbing.

---

## 9. Validation Checklist

- [ ] WireGuard installed on server & all clients
- [ ] IP forwarding enabled on server (`net.ipv4.ip_forward = 1`)
- [ ] Server keys generated, `wg0.conf` written
- [ ] Every peer registered with a unique `/32` in `AllowedIPs`
- [ ] nftables rules applied & NAT masquerade active
- [ ] `wg-quick@wg0` enabled & running
- [ ] Handshake visible with growing RX/TX (`sudo wg show`)
- [ ] Gateway & cross-subnet ping succeed
- [ ] DNS resolves via internal DNS server
- [ ] WSTunnel server & client services running *(if using HTTPS transport)*
- [ ] Static host route bypasses the tunnel for the server's public IP *(if using HTTPS transport)*
- [ ] Windows: Visual C++ Redistributable installed *(if using HTTPS transport)*
- [ ] Windows: WSTunnel running as an NSSM service *(if using HTTPS transport)*

---

## 10. Troubleshooting

### No WireGuard handshake
**Possible causes:** incorrect server/client keys, peer not added on the server, WSTunnel not running, wrong endpoint (should be `127.0.0.1:<WG_PORT>` once WSTunnel is in place), or the HTTPS port blocked upstream.
**Check:** `sudo wg show` on both ends; confirm the peer's public key matches exactly.

### WSTunnel connection times out through the VPN (routing loop)
**Cause:** `AllowedIPs = 0.0.0.0/0` routes WSTunnel's own outbound connection back through `wg0`, creating a recursive loop.
**Fix:** add a static host route to the server's public IP via the physical interface (`PostUp`/`PostDown` in `wg0.conf`). See Phase 3, Step 8.

### `curl` to `https://<PUBLIC_IP>:<WSTUNNEL_PORT>` returns "HTTP 400 Invalid request"
This is **expected**, not an error. WSTunnel only accepts WebSocket upgrade requests, not plain HTTPS GETs — a 400 confirms the service is up and reachable.

### WSTunnel fails to start on Windows
**Symptoms:** process exits immediately, NSSM reports service failures, no WSTunnel logs produced.
**Cause:** missing Microsoft Visual C++ Redistributable (2015–2022 x64).
**Fix:** install `VC_redist.x64.exe`, reboot, restart the service.

### NSSM service won't stay running
**Cause:** same VC++ runtime dependency issue causing `wstunnel.exe` to terminate immediately.
**Fix:** install the runtime, then recreate or restart the NSSM service.

### WSTunnel client can't connect to the server at all
Check:
- Cloud firewall / security group allows inbound TCP `<WSTUNNEL_PORT>`
- `wstunnel.service` is running on the server (`systemctl status wstunnel`)
- `nc -vz <PUBLIC_IP> <WSTUNNEL_PORT>` succeeds from the client
- The static host route for the server's public IP exists (Linux full-tunnel clients)

---

## 11. Design Decisions, Advantages & Limitations

| Decision | Reason |
|---|---|
| Cloud-hosted VPN server | Centralized, secure access point |
| Multiple subnets on one interface | Simplified routing and scalability |
| Static peer addressing | Predictable management and ACLs |
| Public-key authentication | Eliminates shared secrets |
| Full-tunnel (`0.0.0.0/0`) | Centralized traffic routing |
| DNS via internal server | Unified name resolution and domain integration |

**Advantages**
- Lightweight, low latency, high throughput
- Minimal configuration overhead
- Strong modern cryptography
- Easy peer management
- Cross-platform (Linux + Windows)
- Scales to multiple private networks
- Centralized gateway for the whole environment

**Limitations**
- Native UDP may be blocked by enterprise firewalls (hence the optional WSTunnel transport)
- Full-tunnel routing needs careful route management to avoid loops
- Public-key distribution must be managed securely
- WireGuard has no built-in centralized identity management — authorization is purely by configured public key

**Key findings**
- A single WireGuard interface can host multiple IP subnets, letting one gateway serve several logical networks at once.
- A unique `/32` `AllowedIPs` per peer guarantees deterministic routing and avoids address conflicts.
- `PersistentKeepalive = 25` keeps NAT mappings alive for clients behind NAT or restrictive firewalls.
- Integrating the VPN with an internal DNS server gives centralized name resolution for domain-joined and VPN-connected hosts.
- WSTunnel extends the same WireGuard deployment to run over HTTPS wherever native UDP is blocked, without changing the underlying VPN design.

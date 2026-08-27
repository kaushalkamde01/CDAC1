# SOC Lab — AD-DS + SIEM Integration Runbook

This is a step-by-step, **known-working** configuration guide for the enterprise SOC lab's Active
Directory Domain Services deployment and its integration into the ELK-based SIEM. Every command
below is the *corrected* version — dead ends and bugs discovered during the original build have
already been fixed inline, so following this guide top-to-bottom should not reproduce them.

**Environment assumed:**
- Domain Controller `KPK-DC01` — Windows Server 2022, domain `kpk.local`, IP `192.168.150.40`
- Linux hosts `SOC-Core` (`192.168.150.20`), `VM-MISP-01` (`192.168.150.10`), `VM-IDS-01` (`192.168.150.30`) — Ubuntu 22.04 LTS
- SOC-Core runs Elasticsearch, Logstash, Kibana, Kafka via Docker Compose (`~/project/`)
- All commands run as the normal Linux user unless `sudo` is shown — do **not** prefix scripts with `sudo` beyond what's shown, to avoid file-ownership mismatches

---

## 1. Domain Controller — AD DS + DNS (prerequisite, assumed already built)

If starting from scratch: install AD DS + DNS roles via Server Manager, promote to a new forest
root domain `kpk.local`, static IP `192.168.150.40`. Not repeated here — see the pre-handover
report for the full Phase 1 procedure. This guide picks up from a working Domain Controller.

**Prevent multi-homed DNS registration issues before anything else.** If the DC has more than one
network interface (e.g. a transport/uplink NIC in addition to the internal one), disable DNS
registration on every interface except the internal one immediately:

```powershell
Set-DnsClient -InterfaceAlias "<transport-interface-name>" -RegisterThisConnectionsAddress $false
```

---

## 2. Active Directory Structure

Run on `KPK-DC01`, elevated PowerShell, as Domain Admin.

### 2.1 Organizational Units

```powershell
$domainDN = (Get-ADDomain).DistinguishedName

foreach ($ou in @("Servers", "Groups", "Service Accounts", "SOC Users")) {
    if (-not (Get-ADOrganizationalUnit -Filter "Name -eq '$ou'" -SearchBase $domainDN -ErrorAction SilentlyContinue)) {
        New-ADOrganizationalUnit -Name $ou -Path $domainDN -ProtectedFromAccidentalDeletion $true
    }
}

$serversPath = "OU=Servers,$domainDN"
foreach ($sub in @("Windows Servers", "Linux Servers")) {
    if (-not (Get-ADOrganizationalUnit -Filter "Name -eq '$sub'" -SearchBase $serversPath -ErrorAction SilentlyContinue)) {
        New-ADOrganizationalUnit -Name $sub -Path $serversPath -ProtectedFromAccidentalDeletion $true
    }
}
```

> **Do not** name the human-user OU "Users" — that name is already taken by a built-in AD
> container at domain root and the creation will fail. Use "SOC Users" as shown above.

### 2.2 Security Groups

```powershell
$groupsOU = "OU=Groups,$domainDN"
$groups = @(
    "SOC-Admins", "SOC-Analysts", "ThreatIntel", "IR-Team", "ELK-Admins", "MISP-Users",
    "Linux-Sudo-Admins", "RDP-Access", "Read-Only-Analysts"
)
foreach ($grp in $groups) {
    if (-not (Get-ADGroup -Filter "Name -eq '$grp'" -ErrorAction SilentlyContinue)) {
        New-ADGroup -Name $grp -GroupScope Global -GroupCategory Security -Path $groupsOU
    }
}
```

### 2.3 Service Accounts

```powershell
$svcOU = "OU=Service Accounts,$domainDN"
$svcAccounts = @("svc-misp", "svc-logstash", "svc-kibana", "svc-suricata")

foreach ($svc in $svcAccounts) {
    if (-not (Get-ADUser -Filter "SamAccountName -eq '$svc'" -ErrorAction SilentlyContinue)) {
        $pwd = -join ((65..90)+(97..122)+(48..57) | Get-Random -Count 20 | % {[char]$_})
        $securePwd = ConvertTo-SecureString $pwd -AsPlainText -Force
        New-ADUser -Name $svc -SamAccountName $svc -UserPrincipalName "$svc@kpk.local" `
            -Path $svcOU -AccountPassword $securePwd -Enabled $true `
            -PasswordNeverExpires $true -CannotChangePassword $true
        Write-Host "$svc temp password: $pwd  <- move to a vault immediately"
    }
}

Add-ADGroupMember -Identity "MISP-Users" -Members "svc-misp"
Add-ADGroupMember -Identity "ELK-Admins" -Members "svc-logstash", "svc-kibana"
```

### 2.4 Human Test Users

```powershell
# Zero-privilege baseline account
$pwd1 = ConvertTo-SecureString "<choose-a-temp-password>" -AsPlainText -Force
New-ADUser -Name "SOC Analyst One" -SamAccountName "soc.analyst1" `
    -UserPrincipalName "soc.analyst1@kpk.local" -Path "OU=SOC Users,$domainDN" `
    -AccountPassword $pwd1 -Enabled $true -ChangePasswordAtLogon $true
Add-ADGroupMember -Identity "SOC-Analysts" -Members "soc.analyst1"

# Privileged/admin test account
$pwd2 = ConvertTo-SecureString "<choose-a-temp-password>" -AsPlainText -Force
New-ADUser -Name "SOC Admin One" -SamAccountName "soc.admin1" `
    -UserPrincipalName "soc.admin1@kpk.local" -Path "OU=SOC Users,$domainDN" `
    -AccountPassword $pwd2 -Enabled $true -ChangePasswordAtLogon $true
Add-ADGroupMember -Identity "SOC-Admins" -Members "soc.admin1"
Add-ADGroupMember -Identity "Linux-Sudo-Admins" -Members "soc.admin1"
```

> After first login, set the real password directly with `kpasswd <username>` from any joined
> Linux host rather than relying on the interactive `su` password-change prompt — it's more
> reliable.

---

## 3. DNS — Reverse Zones and PTR Records

Run on `KPK-DC01`.

```powershell
# Reverse zone names = subnet octets reversed + .in-addr.arpa
# Check first — zones may already exist:
Get-DnsServerZone

# Only if missing:
Add-DnsServerPrimaryZone -NetworkID "192.168.100.0/24" -ReplicationScope "Domain"
Add-DnsServerPrimaryZone -NetworkID "192.168.150.0/24" -ReplicationScope "Domain"

# PTR records
Add-DnsServerResourceRecordPtr -ZoneName "100.168.192.in-addr.arpa" -Name "10"  -PtrDomainName "web.kpk.local."
Add-DnsServerResourceRecordPtr -ZoneName "100.168.192.in-addr.arpa" -Name "100" -PtrDomainName "honeypot.kpk.local."
Add-DnsServerResourceRecordPtr -ZoneName "150.168.192.in-addr.arpa" -Name "10" -PtrDomainName "misp.kpk.local."
Add-DnsServerResourceRecordPtr -ZoneName "150.168.192.in-addr.arpa" -Name "20" -PtrDomainName "soc-core.kpk.local."
Add-DnsServerResourceRecordPtr -ZoneName "150.168.192.in-addr.arpa" -Name "30" -PtrDomainName "ids.kpk.local."
Add-DnsServerResourceRecordPtr -ZoneName "150.168.192.in-addr.arpa" -Name "40" -PtrDomainName "kpk-dc01.kpk.local."
```

**Verify:**
```powershell
Resolve-DnsName -Name "192.168.150.20"   # should return soc-core.kpk.local
```

---

## 4. Linux Domain Join (SOC-Core, MISP, IDS)

Run on **each** Linux host as your normal user (not root/sudo for the script itself).

### 4.1 Install packages

```bash
sudo apt update
sudo apt install -y realmd sssd sssd-tools libnss-sss libpam-sss adcli \
    samba-common-bin oddjob oddjob-mkhomedir packagekit krb5-user
```

### 4.2 Discover and join

```bash
sudo realm discover kpk.local
sudo realm join --user=Administrator \
    --computer-ou="OU=Linux Servers,OU=Servers,DC=kpk,DC=local" kpk.local
```

### 4.3 Fix sssd.conf — apply all three fixes together, from the start

```bash
sudo sed -i 's/use_fully_qualified_names = True/use_fully_qualified_names = False/' /etc/sssd/sssd.conf
sudo sed -i '/\[domain\/kpk.local\]/a dyndns_update = False' /etc/sssd/sssd.conf
sudo systemctl stop sssd
sudo rm -rf /var/lib/sss/db/*
sudo systemctl start sssd
```

> **Why both fixes matter:** `use_fully_qualified_names = False` allows plain-username logins
> instead of requiring `user@kpk.local`. `dyndns_update = False` prevents SSSD from trying to
> auto-update DNS records it doesn't have permission to write (since DNS is managed manually in
> this environment) — without it, you'll see recurring GSSAPI/TSIG errors in the SSSD logs.

### 4.4 Enable home directory auto-creation

```bash
sudo pam-auth-update --enable mkhomedir
```

### 4.5 Permit specific users to log in

```bash
sudo realm permit soc.analyst1@kpk.local
sudo realm permit soc.admin1@kpk.local
```

### 4.6 Verify

```bash
realm list                      # should show configured: kerberos-member
getent passwd soc.analyst1      # should resolve with correct UID/GID
su - soc.analyst1               # should log in and create a home directory
```

---

## 5. Linux Sudo Authorization

Run on **each** Linux host.

```bash
echo "%linux-sudo-admins ALL=(ALL) ALL" | sudo tee /etc/sudoers.d/ad-admins
sudo chmod 0440 /etc/sudoers.d/ad-admins
sudo visudo -c
```

> `chmod 0440` is not optional — `sudoers.d` files with looser permissions are rejected outright.
> Always run `visudo -c` after any sudoers edit; it validates syntax **and** permissions.

**Verify:**
```bash
su - soc.admin1
sudo whoami        # should print: root
exit

su - soc.analyst1
sudo whoami        # should be denied — confirms the rule isn't overly permissive
exit
```

---

## 6. SIEM Integration — Winlogbeat → Logstash → Elasticsearch → Kibana

### 6.1 Prerequisite check

Confirm Winlogbeat is installed and shipping from `KPK-DC01` to `192.168.150.20:5044`, and inspect
the existing Logstash pipeline before changing anything:

```bash
# On SOC-Core:
cd ~/project
cat logstash/config/pipelines.yml
cat logstash/pipeline/01-ingest.conf   # confirm current input/filter/output shape
```

### 6.2 Add the Winlogbeat filter branch

Insert a new `else if [agent][type] == "winlogbeat" { ... }` branch into `01-ingest.conf`,
**before** the final catch-all `else if ![event][dataset] { ... }` block. Key points, already
corrected from bugs found during the original build:

- **Quote every `winlog.event_id` comparison as a string** — it arrives as a JSON string
  (e.g. `"4625"`), not an integer. Comparing to a bare number silently never matches.
- **Use `mutate { replace => ... }`, not `add_field`**, for `event.category` / `event.action` /
  `event.type` / `event.outcome`. Winlogbeat pre-populates some of these fields natively for
  well-known event IDs; `add_field` on an already-set field turns it into an array instead of
  overwriting it.

Example branch (abridged — extend the event-ID list as needed):

```conf
 else if [agent][type] == "winlogbeat" {
  if [winlog][channel] == "Security" {
   mutate { replace => { "[event][dataset]" => "windows.security" } }
  }
  else if [winlog][channel] == "System" {
   mutate { replace => { "[event][dataset]" => "windows.system" } }
  }
  else if [winlog][channel] == "Application" {
   mutate { replace => { "[event][dataset]" => "windows.application" } }
  }

  if [winlog][event_id] == "4624" {
   mutate {
    replace => {
     "[event][category]" => "authentication"
     "[event][type]" => "start"
     "[event][action]" => "logon"
     "[event][outcome]" => "success"
    }
   }
  }
  else if [winlog][event_id] == "4625" {
   mutate {
    replace => {
     "[event][category]" => "authentication"
     "[event][action]" => "logon"
     "[event][outcome]" => "failure"
    }
   }
  }
  else if [winlog][event_id] == "4771" {
   mutate {
    replace => {
     "[event][category]" => "authentication"
     "[event][action]" => "kerberos_preauth_failed"
     "[event][outcome]" => "failure"
    }
   }
  }
  else if [winlog][event_id] == "4728" or [winlog][event_id] == "4732" or [winlog][event_id] == "4756" {
   mutate {
    replace => {
     "[event][category]" => "iam"
     "[event][action]" => "group_member_added"
    }
   }
  }
  # ... repeat the pattern for 4634, 4720, 4725, 4726, 4729/4733/4757, 4738, 4740, 4767, 4776, 5136
 }
```

**Always back up before editing**, matching the environment's existing convention:
```bash
cp logstash/pipeline/01-ingest.conf logstash/pipeline/01-ingest.conf.backup-$(date +%Y%m%d-%H%M%S)
```

### 6.3 Restart and verify

```bash
docker restart logstash
docker logs logstash --tail 30       # confirm "Pipelines running" with no config errors
```

```bash
curl -s -u elastic:$ELASTIC_PASSWORD \
  "http://localhost:9200/soc-windows.security-*/_search?size=1&pretty" \
  -H 'Content-Type: application/json' \
  -d '{"query": {"match_all": {}}, "sort": [{"@timestamp": "desc"}]}'
```
Confirm `event.category`, `event.action`, and `event.outcome` are populated and are **single
strings, not arrays**.

---

## 7. Kibana Detection Rules

Kibana UI → **Stack Management → Rules → Create rule**. Select rule type **"Elasticsearch query"**
under the "Stack Rules" category, then **"KQL or Lucene"** as the query type.

### 7.1 Rule — AD Brute Force

| Field | Value |
|---|---|
| Name | AD Brute Force - Repeated Logon Kerberos Failures |
| Data view | your SIEM's data view covering `soc-windows.security-*` |
| Query | `winlog.event_id: "4625" or winlog.event_id: "4771"` |
| Group by | Top 5, `source.ip.keyword` |
| Threshold | IS ABOVE 10 |
| Time window | 5 minutes |
| Check every | 1 minute |

### 7.2 Rule — Privileged Group Membership Changed

| Field | Value |
|---|---|
| Name | AD Privileged Group Membership Changed |
| Query | `winlog.event_id: "4728" or winlog.event_id: "4732" or winlog.event_id: "4756"` |
| Group by | none (alert on every occurrence) |
| Threshold | IS ABOVE OR EQUALS 1 |
| Time window | 1 minute |
| Check every | 1 minute |

**Verify a rule actually fires** (don't rely on the "Test query" preview alone — it has its own
state/timing quirks): generate real matching events, wait ~60–90 seconds for the pipeline, then
check the rule's own **Alerts** tab for a genuine fired alert.

---

## 8. Quick Reference — Verification Commands

```bash
# Kerberos ticket check (run as the target user, not via sudo)
kinit <username>
klist

# AD identity resolution
getent passwd <username>
id <username>

# realmd status
realm list

# sudoers validation
sudo visudo -c

# SSSD health
sudo systemctl status sssd --no-pager
sudo journalctl -u sssd --since "5 minutes ago" | grep -i error
```

```powershell
# AD-side checks
Get-ADComputer -Filter "Name -eq '<hostname>'" -Properties DistinguishedName
Get-ADUser -Identity "<username>" -Properties MemberOf | Select Name, MemberOf
Get-ADGroup -Filter * -SearchBase "OU=Groups,DC=kpk,DC=local" | Select Name
```

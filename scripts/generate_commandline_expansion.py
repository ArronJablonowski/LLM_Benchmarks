#!/usr/bin/env python3
"""Generate the reviewed 100-task expansion for commandline-agent-v1."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts" / "benchmark_tests" / "commandline"

TRACKS = {
    "linux": {
        "platform": "Linux",
        "category": "linux_operations",
        "skills": ["bash", "diagnostics", "administration"],
        "cases": [
            ("Filesystem pressure", ["findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS", "df -i", "du -xhd1 /var | sort -h"]),
            ("Memory pressure", ["free -h", "vmstat 1 5", "ps -eo pid,ppid,cmd,%mem --sort=-%mem"]),
            ("CPU saturation", ["uptime", "mpstat -P ALL 1 3", "ps -eo pid,stat,comm,%cpu --sort=-%cpu"]),
            ("Boot regression", ["systemd-analyze", "systemd-analyze blame", "journalctl -b -p warning"]),
            ("DNS failure", ["resolvectl status", "resolvectl query example.org", "journalctl -u systemd-resolved --since -1h"]),
            ("Network path loss", ["ip -br addr", "ip route get 203.0.113.10", "tracepath 203.0.113.10"]),
            ("Container resource leak", ["systemd-cgtop -b -n 1", "nsenter -t 4242 -n ss -plant", "cat /proc/4242/cgroup"]),
            ("Kernel fault triage", ["uname -r", "journalctl -k -p err --since today", "lsmod | sort"]),
            ("LVM recovery planning", ["pvs", "vgs", "lvs -a -o +devices"]),
            ("Multipath storage outage", ["multipath -ll", "lsblk -o NAME,HCTL,SIZE,STATE,MOUNTPOINT", "journalctl -u multipathd --since -2h"]),
        ],
    },
    "macos": {
        "platform": "macOS",
        "category": "macos_operations",
        "skills": ["zsh", "macOS administration", "diagnostics"],
        "cases": [
            ("Hardware inventory", ["system_profiler SPHardwareDataType", "sysctl -n machdep.cpu.brand_string", "diskutil list"]),
            ("Storage pressure", ["df -h /System/Volumes/Data", "du -hd 1 /Users | sort -h", "tmutil listlocalsnapshots /"]),
            ("Launch service failure", ["launchctl print system/com.example.worker", "plutil -p /Library/LaunchDaemons/com.example.worker.plist", "log show --last 30m --predicate 'process == \"launchd\"'"]),
            ("Network configuration", ["networksetup -listallhardwareports", "scutil --dns", "route -n get default"]),
            ("Wi-Fi degradation", ["wdutil info", "system_profiler SPAirPortDataType", "log show --last 15m --predicate 'subsystem == \"com.apple.WiFiManager\"'"]),
            ("FileVault posture", ["fdesetup status", "diskutil apfs listCryptoUsers /", "profiles status -type enrollment"]),
            ("Code-signing investigation", ["codesign -dv --verbose=4 /Applications/Fixture.app", "spctl -a -vv /Applications/Fixture.app", "xattr -lr /Applications/Fixture.app"]),
            ("Time Machine failure", ["tmutil status", "tmutil destinationinfo", "log show --last 2h --predicate 'process == \"backupd\"'"]),
            ("Unified-log correlation", ["date -u", "log show --start '2026-09-20 10:00:00' --end '2026-09-20 10:15:00' --style json", "log collect --last 15m --output /tmp/fixture.logarchive"]),
            ("APFS snapshot recovery", ["diskutil apfs list", "diskutil apfs listSnapshots /", "tmutil compare -s"]),
        ],
    },
    "cmd": {
        "platform": "Windows",
        "category": "windows_cmd_operations",
        "skills": ["cmd.exe", "Windows administration", "diagnostics"],
        "cases": [
            ("Identity and environment", ["whoami /all", "set", "hostname"]),
            ("Disk diagnostics", ["fsutil volume diskfree c:", "chkdsk c: /scan", "dir c:\\ /a"]),
            ("Network baseline", ["ipconfig /all", "route print", "netstat -ano"]),
            ("Service failure", ["sc queryex wuauserv", "sc qc wuauserv", "wevtutil qe System /q:*[System[(EventID=7031)]] /c:10 /f:text"]),
            ("Scheduled task audit", ["schtasks /query /fo LIST /v", "wmic process get ProcessId,ParentProcessId,CommandLine", "where powershell.exe"]),
            ("SMB session review", ["net use", "net session", "openfiles /query /fo csv"]),
            ("Firewall inspection", ["netsh advfirewall show allprofiles", "netsh advfirewall firewall show rule name=all", "netstat -abno"]),
            ("Certificate store triage", ["certutil -store my", "certutil -verifyctl AuthRootWU", "certutil -urlcache * delete"]),
            ("Boot recovery analysis", ["bcdedit /enum all", "reagentc /info", "dism /online /cleanup-image /scanhealth"]),
            ("Domain channel failure", ["nltest /sc_verify:EXAMPLE", "setspn -Q HOST/workstation01", "gpresult /scope computer /v"]),
        ],
    },
    "powershell": {
        "platform": "Windows",
        "category": "powershell_wmi_operations",
        "skills": ["PowerShell", "CIM", "Windows administration"],
        "cases": [
            ("Process inventory", ["Get-Process | Sort-Object CPU -Descending", "Get-CimInstance Win32_Process", "Get-Counter '\\Process(*)\\% Processor Time'"]),
            ("Volume health", ["Get-Volume", "Get-PhysicalDisk", "Get-StorageReliabilityCounter -PhysicalDisk (Get-PhysicalDisk | Select-Object -First 1)"]),
            ("Event-log triage", ["Get-WinEvent -ListLog *", "Get-WinEvent -FilterHashtable @{LogName='System';Level=2}", "Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=(Get-Date).AddHours(-1)}"]),
            ("Update posture", ["Get-HotFix | Sort-Object InstalledOn -Descending", "Get-CimInstance Win32_QuickFixEngineering", "Get-WindowsUpdateLog"]),
            ("Local account audit", ["Get-LocalUser", "Get-LocalGroupMember Administrators", "Get-CimInstance Win32_UserProfile"]),
            ("Defender status", ["Get-MpComputerStatus", "Get-MpThreatDetection", "Get-MpPreference"]),
            ("PowerShell remoting", ["Test-WSMan server01", "Get-PSSessionConfiguration", "Invoke-Command -ComputerName server01 -ScriptBlock { Get-Service }"]),
            ("Cluster health", ["Get-ClusterNode", "Get-ClusterGroup", "Get-ClusterLog -UseLocalTime -TimeSpan 15"]),
            ("AD replication", ["Get-ADReplicationPartnerMetadata -Target * -Scope Forest", "Get-ADReplicationFailure -Target * -Scope Forest", "dcdiag /test:replications"]),
            ("Hyper-V outage", ["Get-VM", "Get-VMNetworkAdapter -All", "Get-VMSwitch | Format-List *"]),
        ],
    },
    "ssh": {
        "platform": "Mixed Unix over SSH",
        "category": "ssh_network_operations",
        "skills": ["SSH", "remote administration", "networking"],
        "cases": [
            ("Host-key verification", ["ssh-keygen -F edge01", "ssh-keyscan -t ed25519 edge01", "ssh -o StrictHostKeyChecking=yes ops@edge01 'hostname'"]),
            ("Jump-host access", ["ssh -G -J bastion ops@app01", "ssh -J bastion ops@app01 'uptime'", "ssh -J bastion ops@app01 'systemctl --failed'"]),
            ("Port-forward diagnosis", ["ssh -vv -L 9443:db01:443 ops@jump01 -N", "ss -lntp | grep 9443", "curl -vk https://127.0.0.1:9443/health"]),
            ("Connection multiplexing", ["ssh -O check -S /tmp/ctl ops@host01", "ssh -M -S /tmp/ctl -fnNT ops@host01", "ssh -O forward -S /tmp/ctl -L 9000:localhost:9000 ops@host01"]),
            ("SFTP permission failure", ["sftp -vv ops@files01", "ssh ops@files01 'namei -l /srv/drop/report.csv'", "ssh ops@files01 'getfacl /srv/drop'"]),
            ("Remote socket investigation", ["ssh ops@app02 'ss -plant'", "ssh ops@app02 'lsof -nP -iTCP -sTCP:LISTEN'", "ssh ops@app02 'journalctl -u api --since -1h'"]),
            ("SSH certificate expiry", ["ssh-keygen -L -f user-cert.pub", "date -u", "ssh -vv -o CertificateFile=user-cert.pub ops@secure01"]),
            ("ProxyCommand failure", ["ssh -G legacy01", "nc -vz proxy01 1080", "ssh -vv -o ProxyCommand='nc -x proxy01:1080 %h %p' legacy01"]),
            ("Bastion chain outage", ["ssh -vv -J edge-bastion,core-bastion ops@db02 'true'", "mtr -rw core-bastion", "ssh edge-bastion 'journalctl -u ssh --since -30m'"]),
            ("Fleet evidence collection", ["parallel-ssh -h hosts.txt -x '-o BatchMode=yes' 'date -u'", "parallel-ssh -h hosts.txt 'sha256sum /usr/local/bin/agent'", "parallel-ssh -h hosts.txt 'ss -plant'"]),
        ],
    },
    "distro": {
        "platform": "Multiple Linux distributions",
        "category": "linux_distribution_admin",
        "skills": ["package managers", "service managers", "Linux administration"],
        "cases": [
            ("Debian package audit", ["cat /etc/os-release", "apt-cache policy openssl", "systemctl is-system-running"]),
            ("Fedora package audit", ["cat /etc/fedora-release", "dnf repoquery --upgrades", "systemctl --failed"]),
            ("Arch service audit", ["cat /etc/arch-release", "pacman -Qu", "systemctl --failed"]),
            ("SUSE repository failure", ["cat /etc/SUSE-brand", "zypper lr -u", "zypper verify"]),
            ("Alpine OpenRC recovery", ["cat /etc/alpine-release", "rc-status -a", "tail -n 50 /var/log/messages"]),
            ("Debian alternatives drift", ["update-alternatives --get-selections", "readlink -f /usr/bin/python3", "dpkg -S /usr/bin/python3"]),
            ("RHEL SELinux denial", ["getenforce", "ausearch -m AVC -ts recent", "sealert -a /var/log/audit/audit.log"]),
            ("Ubuntu netplan outage", ["netplan get", "networkctl status", "journalctl -u systemd-networkd --since -30m"]),
            ("NixOS generation rollback", ["nixos-version", "nixos-rebuild list-generations", "journalctl -b -p err"]),
            ("Immutable host drift", ["rpm-ostree status", "ostree admin status", "systemd-delta"]),
        ],
    },
    "menus": {
        "platform": "Terminal appliances",
        "category": "interactive_terminal_menus",
        "skills": ["terminal menus", "state tracking", "administration"],
        "cases": [
            ("Two-level status menu", ["show menu", "show status"], ["1", "2"]),
            ("Network test menu", ["show menu", "show interfaces"], ["2", "3", "1"]),
            ("Service restart menu", ["show services", "show service api"], ["3", "2", "4"]),
            ("DNS resolver menu", ["show dns", "test dns example.org"], ["2", "4", "3", "example.org"]),
            ("Backup verification menu", ["show backups", "verify backup latest"], ["4", "2", "1", "latest"]),
            ("HA failover menu", ["show ha", "show ha peer"], ["5", "3", "2", "1"]),
            ("Certificate renewal menu", ["show certificates", "check certificate edge"], ["6", "2", "4", "edge", "1"]),
            ("VLAN repair menu", ["show vlans", "show vlan 220"], ["2", "5", "3", "220", "2"]),
            ("Cluster quorum menu", ["show cluster", "show quorum"], ["7", "4", "2", "3", "1", "2"]),
            ("Disaster recovery console", ["show recovery points", "validate recovery point rp-42"], ["9", "3", "4", "2", "rp-42", "1", "3"]),
        ],
    },
    "pfsense": {
        "platform": "pfSense mock",
        "category": "pfsense_administration",
        "skills": ["pfSense", "FreeBSD", "firewall administration"],
        "cases": [
            ("Interface inventory", ["ifconfig -l", "pfctl -s Interfaces", "netstat -rn"]),
            ("DHCP lease failure", ["pgrep -laf dhcpd", "clog /var/log/dhcpd.log", "sockstat -4 -l | grep :67"]),
            ("DNS Resolver outage", ["pgrep -laf unbound", "drill @127.0.0.1 example.org", "clog /var/log/resolver.log"]),
            ("Gateway monitoring", ["pfSsh.php playback gatewaystatus", "clog /var/log/gateways.log", "dpinger -S -r 0 -i WAN_DHCP 198.51.100.1"]),
            ("State exhaustion", ["pfctl -si", "pfctl -ss | wc -l", "sysctl net.pf.states_hashsize"]),
            ("CARP failover", ["ifconfig carp", "sysctl net.inet.carp", "clog /var/log/system.log | grep carp"]),
            ("NAT reflection", ["pfctl -sn", "pfctl -sr", "tcpdump -ni pflog0 host 10.0.20.15"]),
            ("WireGuard routing", ["wg show", "netstat -rn -f inet", "pfctl -ss | grep wg0"]),
            ("Multi-WAN policy routing", ["pfctl -sr -vv", "pfctl -vvs Tables", "clog /var/log/gateways.log"]),
            ("HA configuration divergence", ["pfctl -sr | sha256", "pfSsh.php playback pfsyncstatus", "clog /var/log/system.log | grep XMLRPC"]),
        ],
    },
    "openwrt": {
        "platform": "OpenWrt mock",
        "category": "openwrt_administration",
        "skills": ["OpenWrt", "UCI", "router administration"],
        "cases": [
            ("Board and release inventory", ["ubus call system board", "uci show system", "df -h"]),
            ("WAN lease failure", ["ifstatus wan", "ubus call network.interface.wan status", "logread -e udhcpc"]),
            ("Wireless client issue", ["ubus call network.wireless status", "iwinfo wlan0 assoclist", "logread -e hostapd"]),
            ("DNS forwarding failure", ["uci show dhcp", "ubus call service list '{\"name\":\"dnsmasq\"}'", "logread -e dnsmasq"]),
            ("Overlay exhaustion", ["df -h /overlay", "du -h -d 1 /overlay | sort -h", "opkg list-installed"]),
            ("Policy-based routing", ["uci show pbr", "ip rule show", "ip route show table 100"]),
            ("SQM regression", ["uci show sqm", "tc -s qdisc show dev pppoe-wan", "logread -e sqm"]),
            ("WireGuard peer outage", ["wg show", "ifstatus wg0", "ip route get 10.80.0.1"]),
            ("DSA VLAN isolation", ["bridge vlan show", "uci show network", "nft list chain inet fw4 forward"]),
            ("Multi-router roaming", ["ubus call usteer remote_hosts", "ubus call hostapd.wlan0 get_clients", "logread -e 802.11r"]),
        ],
    },
    "incident": {
        "platform": "Cross-platform incident response",
        "category": "live_incident_response",
        "skills": ["incident response", "IOC investigation", "evidence preservation"],
        "cases": [
            ("Linux suspicious login", ["date -u", "last -F", "journalctl -u ssh --since -2h"]),
            ("Windows malicious service", ["Get-Date -AsUTC", "Get-CimInstance Win32_Service", "Get-WinEvent -FilterHashtable @{LogName='System';Id=7045}"]),
            ("macOS launch agent", ["date -u", "launchctl print gui/501/com.fixture.sync", "plutil -p ~/Library/LaunchAgents/com.fixture.sync.plist"]),
            ("Linux web shell", ["ss -plant", "ps auxf", "find /var/www -type f -mmin -60 -ls"]),
            ("Windows credential access", ["Get-NetTCPConnection", "Get-CimInstance Win32_Process", "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4688}"]),
            ("macOS browser theft", ["lsof -nP -iTCP", "ps -axo pid,ppid,user,command", "log show --last 1h --predicate 'eventMessage CONTAINS \"Login Data\"'"]),
            ("Linux rootkit indicators", ["lsmod", "find /proc -maxdepth 1 -type d | sort", "ss -plant"]),
            ("Windows ransomware staging", ["Get-SmbSession", "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4663}", "Get-ChildItem C:\\Users -Recurse -Filter '*.locked'"]),
            ("Cloud-to-host pivot", ["ssh ir@cloud01 'journalctl -u auditd --since -2h'", "ssh ir@app01 'ss -plant'", "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4624}"]),
            ("Enterprise coordinated intrusion", ["date -u", "parallel-ssh -h linux-hosts.txt 'ss -plant'", "Invoke-Command -ComputerName (Get-Content windows-hosts.txt) { Get-NetTCPConnection }", "ssh ir@pfsense 'pfctl -ss'", "ssh ir@openwrt 'nft list ruleset'"]),
        ],
    },
}


def difficulty(index: int) -> str:
    return ("easy", "easy", "medium", "medium", "medium", "hard", "hard", "hard", "expert", "expert")[index]


def build_descriptor(track: str, spec: dict, index: int, case: tuple) -> dict:
    title, commands, *menu_part = case
    task_id = f"cli_exp_{track}_{index + 1:02d}"
    menu = menu_part[0] if menu_part else []
    evidence = f"evidence-{task_id}"
    outputs = {}
    for position, command in enumerate(commands, 1):
        suffix = f"\nDECISIVE: {evidence}" if position == len(commands) else ""
        outputs[command] = f"STEP {position}/{len(commands)}: simulated {title.lower()} evidence for {command}{suffix}"
    lab = {
        "context": f"Escalating lab: {title}. Inspect evidence before recommending a reversible action.",
        "commands": outputs,
    }
    if menu:
        lab["menus"] = {">".join(menu): f"Menu path confirmed for {title}; DECISIVE: {evidence}"}
    return {
        "id": task_id,
        "family": "Escalating Command Line Lab",
        "category": spec["category"],
        "name": title,
        "prompt": (
            f"Investigate the simulated {title.lower()} scenario. Use terminal_lab.py to run the "
            "appropriate commands in a defensible order, interpret the decisive evidence, and create "
            "answer.json with commands, findings, safe reversible actions, and any menu path."
        ),
        "fixture": "cli_tasks/common/workspace",
        "grader": "cli_tasks/grader.py",
        "time_class": "short" if index < 2 else "medium" if index < 5 else "extended",
        "benchmark_origin": "Original Escalating CLI Lab",
        "platform": spec["platform"],
        "difficulty": difficulty(index),
        "difficulty_level": index + 1,
        "skills": spec["skills"],
        "lab": lab,
        "grading": {
            "kind": "workspace",
            "required_commands": commands,
            "required_findings": [evidence],
            "required_menu": menu,
        },
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    generated = []
    for track, spec in TRACKS.items():
        for index, case in enumerate(spec["cases"]):
            descriptor = build_descriptor(track, spec, index, case)
            path = OUT / f"{descriptor['id']}.json"
            path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
            generated.append(path)
    print(f"generated {len(generated)} command-line task descriptors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

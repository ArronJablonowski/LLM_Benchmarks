from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coding_tasks"))
from grader_support import Checks


EXPECTED = {
    "cli_linux_basics": (["uname -a", "df -h", "ps aux --sort=-%cpu"], ["disk", "python3", "cpu"], []),
    "cli_macos_diagnostics": (["sw_vers", "diskutil info /", "pmset -g batt"], ["14.6", "apfs", "service recommended"], []),
    "cli_windows_cmd_diagnostics": (["systeminfo", "wmic logicaldisk get caption,freespace,size", "tasklist /v"], ["windows 11", "c:", "backupsvc.exe"], []),
    "cli_powershell_services": (["Get-Service -Name Spooler", "Get-WinEvent -LogName System -MaxEvents 20", "Restart-Service -Name Spooler"], ["stopped", "7031", "running"], []),
    "cli_wmi_inventory": (["Get-CimInstance Win32_OperatingSystem", "Get-CimInstance Win32_BIOS", "Get-CimInstance Win32_LogicalDisk"], ["23h2", "contoso-7a19", "9.8 gb"], []),
    "cli_ssh_triage": (["ssh -J bastion ops@web01 'hostnamectl'", "ssh -J bastion ops@web01 'ss -lntp'", "ssh -J bastion ops@web01 'journalctl -u nginx --since -30min'"], ["web01", "8443", "address already in use"], []),
    "cli_ubuntu_admin": (["systemctl --failed", "journalctl -u postgresql --since today", "df -h /var"], ["postgresql.service", "no space left", "100%"], []),
    "cli_rhel_admin": (["dnf check-update", "systemctl status firewalld", "firewall-cmd --list-all"], ["openssl", "running", "443/tcp"], []),
    "cli_alpine_admin": (["cat /etc/alpine-release", "rc-status", "apk audit"], ["3.20.2", "sshd", "libcrypto3"], []),
    "cli_custom_menu_navigation": ([], ["diagnostics", "dns lookup", "healthy"], ["2", "4", "3", "example.org"]),
    "cli_pfsense_interface_recovery": (["pfctl -s Interfaces", "ifconfig igb1", "netstat -rn"], ["igb1", "no carrier", "192.0.2.1"], ["8", "1", "2"]),
    "cli_pfsense_firewall_nat": (["pfctl -sr", "pfctl -sn", "tcpdump -ni igb1 port 443"], ["block", "rdr", "syn"], ["8", "3", "1"]),
    "cli_pfsense_vpn_diagnostics": (["swanctl --list-sas", "clog /var/log/ipsec.log", "pfctl -ss | grep 10.44.0.0"], ["connecting", "no proposal chosen", "0 states"], ["8", "4", "2"]),
    "cli_openwrt_network_recovery": (["ubus call system board", "ubus call network.interface.wan status", "logread -e netifd"], ["openwrt 23.05", "pending", "udhcpc: no lease"], ["network", "interfaces", "wan"]),
    "cli_openwrt_firewall_diagnostics": (["fw4 print", "nft list ruleset", "logread -e firewall"], ["zone lan", "drop", "forward rejected"], ["network", "firewall", "zones", "lan"]),
    "cli_linux_incident_response": (["date -u", "ss -plant", "ps auxf", "find /tmp -type f -mmin -120 -ls", "sha256sum /tmp/.cache/upd"], ["203.0.113.77", "upd", "9b1d"], []),
    "cli_macos_incident_response": (["date -u", "launchctl print system/com.apple.updates", "log show --last 1h --predicate 'process == \"curl\"'", "shasum -a 256 /Library/LaunchDaemons/com.apple.updates.plist"], ["evil.example", "keepalive", "6e91"], []),
    "cli_windows_incident_response": (["Get-Date -AsUTC", "Get-NetTCPConnection | Sort-Object State", "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine", "Get-FileHash C:\\ProgramData\\svhost.exe -Algorithm SHA256"], ["198.51.100.42", "powershell -enc", "44aa"], []),
    "cli_multi_host_incident_response": (["ssh ir@web01 'ss -plant'", "ssh ir@db01 'last -F'", "ssh ir@jump01 'journalctl -u ssh --since -2h'", "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4624}"], ["10.20.30.44", "svc_backup", "web01"], []),
    "cli_multi_firewall_outage": (["pfctl -sr", "nft list ruleset", "traceroute 10.70.0.10"], ["pfsense", "openwrt", "mtu 1400"], ["1", "3", "2", "4", "1"]),
}

# Read-only diagnostics do not have a prescribed command order. Preserve
# actual order in artifacts, and require dependencies where the task asks for it.
ORDER_INDEPENDENT = frozenset(EXPECTED) - {
    "cli_powershell_services", "cli_linux_incident_response", "cli_macos_incident_response",
    "cli_windows_incident_response", "cli_multi_host_incident_response",
}
# A diagnostic-only task must not force the agent to invent a remediation.
ACTION_OPTIONAL = frozenset({
    "cli_windows_cmd_diagnostics", "cli_wmi_inventory", "cli_ssh_triage",
    "cli_custom_menu_navigation", "cli_pfsense_firewall_nat",
})
# Explicit, task-scoped equivalents for observed facts, not answer inference.
FINDING_ALIASES = {
    ("cli_ssh_triage", "address already in use"): (
        "port 8443 is already in use", "port 8443 already in use",
        "port 8443 was already in use",
        "port 8443, which was already in use",
        "nginx failed because it tried to bind to port 8443, but that port was already in use",
    ),
    ("cli_ubuntu_admin", "postgresql.service"): ("postgresql",),
    ("cli_rhel_admin", "running"): ("firewalld is active", "firewalld active", "active firewalld"),
    ("cli_pfsense_firewall_nat", "block"): ("no matching wan pass rule",),
    ("cli_pfsense_firewall_nat", "rdr"): ("redirects", "redirect rule",),
    ("cli_pfsense_vpn_diagnostics", "0 states"): ("no states", "zero states", "no matching states", "no active states"),
    ("cli_openwrt_firewall_diagnostics", "zone lan"): ("lan zone", "'lan' zone", "lan forward policy is drop"),
}
# Equivalent observations may need several facts together. Keep these scoped
# to one task and require every component, rather than accepting vague words
# such as "service", "forward" or "encoded" on their own.
FINDING_EQUIVALENTS = {
    ("cli_linux_basics", "disk"): (("root filesystem / is 94% used", "/var filesystem is 100% used"),),
    ("cli_macos_diagnostics", "service recommended"): (("with a service recommendation",), ("battery requires service",)),
    ("cli_custom_menu_navigation", "healthy"): (("example.org resolved to 93.184.216.34",),),
    ("cli_pfsense_firewall_nat", "block"): (("lack a pass rule", "incoming https traffic", "wan interface"),),
    ("cli_pfsense_firewall_nat", "rdr"): (("nat rule", "forwards wan port 443 traffic to 10.0.20.15"),),
    ("cli_openwrt_network_recovery", "udhcpc: no lease"): (("udhcpc failed", "no lease, failing", "dhcp client could not obtain a lease"),),
    ("cli_openwrt_firewall_diagnostics", "forward rejected"): (("firewall logs show reject", "dpt=8443", "192.168.50.20"), ("firewall logs show a rejected connection attempt", "192.168.50.20 on port 8443")),
    ("cli_windows_incident_response", "powershell -enc"): (("powershell", "command line is suspiciously encoded"), ("encoded powershell command",), ("powershell with encoded command",)),
}


def mentions(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term), text) is not None


def findings_cover(task_id: str, required: list[str], text: str) -> bool:
    text = " ".join(text.lower().split())
    # A complete successful resolution states both the operation and result.
    # Match the whole declaration so negated results, other hosts/addresses,
    # and contradictory health qualifiers cannot satisfy this equivalent.
    dns_result = task_id == "cli_custom_menu_navigation" and re.fullmatch(
        r"(?:dns lookup(?: for)?[: ]+)?example\.org (?:resolves|resolved) to 93\.184\.216\.34"
        r"(?:[,;.]? resolver health(?: is)? healthy)?\.?", text
    ) is not None
    # A positive exposed-services declaration can spell the same port/protocol
    # as "TCP port 443". Require the exact port and affirmative clause.
    tcp443_exposed = task_id == "cli_rhel_admin" and any(
        re.search(r"\bexposed services: (?:ssh and )?tcp port 443$", clause.strip())
        and not re.search(r"\b(?:not|no|never)\b", clause)
        for clause in re.split(r"[;.]", text)
    )
    # Navigation into Diagnostics is verified independently by the required
    # menu path and transcript; the result need not repeat the menu label.
    facts = [fact for fact in required if not (task_id == "cli_custom_menu_navigation" and fact == "diagnostics")]
    def covered(fact):
        if dns_result and fact in ("dns lookup", "healthy"):
            return True
        if tcp443_exposed and fact == "443/tcp":
            return True
        key = (task_id, fact.lower())
        literal = any(mentions(text, term) for term in (fact.lower(), *FINDING_ALIASES.get(key, ())))
        equivalent = any(all(mentions(text, term) for term in group) for group in FINDING_EQUIVALENTS.get(key, ()))
        return literal or equivalent
    return all(covered(fact) for fact in facts)


def commands_cover(task_id: str, required: list[str], actual: list[str]) -> bool:
    if task_id in ORDER_INDEPENDENT:
        observed = {str(item).lower() for item in actual}
        return all(item.lower() in observed for item in required)
    return subsequence(required, actual)


def documented_running_recovery(actions: list[str], events: list[dict]) -> bool:
    """Accept a verified outcome in actions, backed by a post-restart query."""
    reported = any(re.fullmatch(
        r"verified (?:that )?(?:the )?(?:print )?spooler(?: service)? is (?:now )?running(?: after recovery)?[.!]?",
        " ".join(str(action).lower().split()),
    ) for action in actions)
    if not reported:
        return False
    restarted = verified = False
    for event in events:
        if event.get("kind") != "run":
            continue
        command = event.get("value", "").lower()
        if command == "restart-service -name spooler":
            restarted, verified = bool(event.get("ok")), False
        elif command == "get-service -name spooler" and restarted:
            verified = bool(event.get("ok")) and re.search(
                r"(?im)^\s*running\s+spooler(?:\s|$)", event.get("output", "")
            ) is not None
    return verified


FORBIDDEN = ("rm -rf", "format c:", "shutdown /s", "erase startup-config", "pfctl -d")


def subsequence(required: list[str], actual: list[str]) -> bool:
    iterator = iter(actual)
    return all(any(item.lower() == candidate.lower() for candidate in iterator) for item in required)


def main(workspace: Path) -> int:
    checks = Checks()
    task_id = (workspace / ".benchmark-task-id").read_text(encoding="utf-8").strip()
    if task_id in EXPECTED:
        required_commands, required_findings, required_menu = EXPECTED[task_id]
    else:
        descriptor_path = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "benchmark_tests" / "commandline" / f"{task_id}.json"
        )
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        grading = descriptor["grading"]
        required_commands = grading["required_commands"]
        required_findings = grading["required_findings"]
        required_menu = grading.get("required_menu", [])
    answer_path = workspace / "answer.json"
    try:
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        answer = {}
        checks.check("valid answer.json", False, str(exc))
    else:
        checks.check("valid answer.json", isinstance(answer, dict))
    commands = answer.get("commands", []) if isinstance(answer, dict) else []
    findings = answer.get("findings", []) if isinstance(answer, dict) else []
    actions = answer.get("actions", []) if isinstance(answer, dict) else []
    menu = answer.get("menu_path", []) if isinstance(answer, dict) else []
    transcript_path = workspace / "transcript.jsonl"
    events = []
    if transcript_path.is_file():
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            try: events.append(json.loads(line))
            except json.JSONDecodeError: pass
    checks.check("ordered command investigation", commands_cover(task_id, required_commands, commands))
    finding_text = " ".join(str(item) for item in findings).lower()
    if task_id == "cli_powershell_services" and documented_running_recovery(actions, events):
        finding_text += " running"
    checks.check("evidence-backed findings", findings_cover(task_id, required_findings, finding_text))
    checks.check("safe administration actions", (bool(actions) or task_id in ACTION_OPTIONAL) and not any(token in " ".join(map(str, actions)).lower() for token in FORBIDDEN))
    checks.check("required menu navigation", not required_menu or subsequence(required_menu, [str(item) for item in menu]))
    successful = [event.get("value", "") for event in events if event.get("ok")]
    checks.check("commands exercised in simulator", commands_cover(task_id, required_commands, successful))
    checks.check(
        "menu exercised in simulator",
        not required_menu or any(
            event.get("kind") == "menu"
            and event.get("ok")
            and event.get("value", "").lower() == ">".join(required_menu).lower()
            for event in events
        ),
    )
    checks.check("no unsafe destructive command", not any(token in " ".join(commands).lower() for token in FORBIDDEN))
    return checks.emit()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))

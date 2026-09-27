import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
GRADER=ROOT/'cli_tasks/grader.py'
spec=importlib.util.spec_from_file_location('cli_grader_policy',GRADER)
grader=importlib.util.module_from_spec(spec);spec.loader.exec_module(grader)

class GraderPolicyTests(unittest.TestCase):
    def grade_inventory(self, commands, events=None, actions=None):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'.benchmark-task-id').write_text('cli_wmi_inventory')
            (p/'answer.json').write_text(json.dumps({'commands':commands,'findings':['23H2','CONTOSO-7A19','9.8 GB'],'actions':actions or [],'menu_path':[]}))
            (p/'transcript.jsonl').write_text(''.join(json.dumps({'kind':'run','value':v,'ok':True})+'\n' for v in (commands if events is None else events)))
            result=subprocess.run([sys.executable,str(GRADER),str(p)],text=True,capture_output=True)
            return json.loads(result.stdout)
    def test_independent_inventory_permutations_and_no_remediation_needed(self):
        cmds=grader.EXPECTED['cli_wmi_inventory'][0]
        result=self.grade_inventory(list(reversed(cmds)))
        self.assertEqual(result['verdict'],'pass',result)
    def test_fabricated_command_without_executed_evidence_still_fails(self):
        cmds=grader.EXPECTED['cli_wmi_inventory'][0]
        result=self.grade_inventory(cmds,cmds[:-1])
        self.assertEqual(result['verdict'],'fail')
    def test_missing_required_inventory_command_still_fails(self):
        self.assertEqual(self.grade_inventory(grader.EXPECTED['cli_wmi_inventory'][0][:-1])['verdict'],'fail')
    def test_destructive_action_is_not_allowed_for_inventory(self):
        self.assertEqual(self.grade_inventory(grader.EXPECTED['cli_wmi_inventory'][0],actions=['rm -rf /'])['verdict'],'fail')
    def test_recovery_and_incident_order_remain_required(self):
        for task in ['cli_powershell_services','cli_linux_incident_response']:
            required=grader.EXPECTED[task][0]
            self.assertFalse(grader.commands_cover(task,required,list(reversed(required))))
            self.assertTrue(grader.commands_cover(task,required,required))

class DiagnosticSemanticsTests(unittest.TestCase):
    def test_equivalent_observations_are_not_literal_mismatches(self):
        cases = [
            ("cli_ssh_triage", "web01, port 8443 is already in use by Python"),
            ("cli_ubuntu_admin", "PostgreSQL failed: no space left on device. /var is 100% full"),
            ("cli_rhel_admin", "openssl update available. firewalld is active with 443/tcp exposed"),
            ("cli_pfsense_vpn_diagnostics", "CONNECTING, no proposal chosen, no states exist"),
            ("cli_openwrt_firewall_diagnostics", "LAN zone uses drop; forward rejected"),
            ("cli_pfsense_firewall_nat", "NAT redirects HTTPS; no matching WAN pass rule; SYN captured"),
        ]
        for task, text in cases:
            with self.subTest(task=task):
                self.assertTrue(grader.findings_cover(task,grader.EXPECTED[task][1],text))
    def test_missing_or_wrong_observations_still_fail(self):
        cases = [
            ("cli_rhel_admin", "openssl update, firewalld is inactive, port 443/tcp"),
            ("cli_ssh_triage", "web01, port 8443 is available"),
            ("cli_pfsense_vpn_diagnostics", "CONNECTING, no proposal chosen, 20 states exist"),
            ("cli_openwrt_firewall_diagnostics", "guest zone drop; forward rejected"),
        ]
        for task, text in cases:
            with self.subTest(task=task):
                self.assertFalse(grader.findings_cover(task,grader.EXPECTED[task][1],text))
    def test_read_only_pfsense_diagnostics_have_no_artificial_order(self):
        task="cli_pfsense_interface_recovery";cmds=grader.EXPECTED[task][0]
        self.assertTrue(grader.commands_cover(task,cmds,list(reversed(cmds))))
        self.assertFalse(grader.commands_cover(task,cmds,cmds[:-1]))
    def test_menu_result_need_not_repeat_navigation_label(self):
        task="cli_custom_menu_navigation"
        self.assertTrue(grader.findings_cover(task,grader.EXPECTED[task][1],"DNS Lookup result: healthy"))
        self.assertFalse(grader.findings_cover(task,grader.EXPECTED[task][1],"Diagnostics Network Tools"))
    def test_recovery_outcome_is_still_required(self):
        self.assertFalse(grader.findings_cover("cli_powershell_services",grader.EXPECTED["cli_powershell_services"][1],"stopped Spooler; event 7031; restarted service"))

    def test_dns_resolution_is_a_complete_result_without_repeating_menu_labels(self):
        task = "cli_custom_menu_navigation"
        for text in ["example.org resolves to 93.184.216.34", "Example.org resolved to 93.184.216.34."]:
            self.assertTrue(grader.findings_cover(task, grader.EXPECTED[task][1], text))
        for text in [
            "example.org resolves to 192.0.2.10", "example.net resolves to 93.184.216.34",
            "example.org does not resolve to 93.184.216.34", "example.org 93.184.216.34",
            "not true: example.org resolves to 93.184.216.34",
            "example.org resolves to 93.184.216.34 but resolver is unhealthy",
        ]:
            with self.subTest(text=text):
                self.assertFalse(grader.findings_cover(task, grader.EXPECTED[task][1], text))

    def test_dns_resolution_still_requires_complete_executed_menu_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / '.benchmark-task-id').write_text('cli_custom_menu_navigation')
            answer = dict(commands=[], findings=['example.org resolves to 93.184.216.34'],
                          actions=[], menu_path=['2', '4', '3', 'example.org'])
            (p / 'answer.json').write_text(json.dumps(answer))
            for value, ok, verdict in [('2>4>3>example.org', True, 'pass'),
                                       ('2>4>3', True, 'fail'), ('2>4>3>example.org', False, 'fail')]:
                (p / 'transcript.jsonl').write_text(json.dumps(dict(kind='menu', value=value, ok=ok)) + '\n')
                result = subprocess.run([sys.executable, str(GRADER), str(p)], capture_output=True, text=True)
                self.assertEqual(json.loads(result.stdout)['verdict'], verdict)

    def test_equivalent_reported_observations(self):
        cases = [
            ("cli_macos_diagnostics", "macOS 14.6; APFS; battery at 63% with a service recommendation"),
            ("cli_custom_menu_navigation", "DNS Lookup for example.org resolved to 93.184.216.34"),
            ("cli_pfsense_firewall_nat", "The NAT rule forwards WAN port 443 traffic to 10.0.20.15. Rules lack a pass rule for incoming HTTPS traffic on the WAN interface. SYN packets arrive."),
            ("cli_openwrt_firewall_diagnostics", "LAN zone forward policy is drop; firewall logs show reject for dpt=8443 to 192.168.50.20"),
            ("cli_windows_incident_response", "Connection to 198.51.100.42; parent powershell and its command line is suspiciously encoded. SHA256 44AA9011"),
        ]
        for task, text in cases:
            with self.subTest(task=task):
                self.assertTrue(grader.findings_cover(task, grader.EXPECTED[task][1], text))

    def test_equivalence_requires_complete_specific_evidence(self):
        cases = [
            ("cli_macos_diagnostics", "14.6 APFS; no service recommendation is present"),
            ("cli_custom_menu_navigation", "DNS Lookup for example.org resolved to 192.0.2.10"),
            ("cli_pfsense_firewall_nat", "NAT rule forwards WAN port 443 traffic to 10.0.20.15. WAN pass rule allows incoming HTTPS. SYN packets arrive."),
            ("cli_openwrt_firewall_diagnostics", "LAN zone drop; firewall logs show reject for dpt=443 to 192.168.50.20"),
            ("cli_windows_incident_response", "198.51.100.42; parent powershell; command line is unencoded; hash 44AA9011"),
            ("cli_windows_incident_response", "powershell command line is suspiciously encoded; hash 44AA9011"),
        ]
        for task, text in cases:
            with self.subTest(task=task):
                self.assertFalse(grader.findings_cover(task, grader.EXPECTED[task][1], text))

    def test_missing_persistence_setting_remains_a_mismatch(self):
        task = "cli_macos_incident_response"
        self.assertFalse(grader.findings_cover(task, grader.EXPECTED[task][1], "evil.example payload and SHA256 6e91b327"))

    def test_ssh_port_conflict_relative_clause_keeps_port_and_polarity(self):
        task = "cli_ssh_triage"
        required = grader.EXPECTED[task][1]
        self.assertTrue(grader.findings_cover(task, required,
            "web01: nginx failed because it tried to bind to port 8443, which was already in use."))
        for text in [
            "web01: port 8443 is available. nginx failed on port 9443, which was already in use.",
            "web01: nginx tried to bind to port 8443, which was not already in use.",
            "web01: nginx tried to bind to port 8443, which was available.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(grader.findings_cover(task, required, text))

    def test_vpn_zero_active_states_equivalent_preserves_count_and_other_evidence(self):
        task = "cli_pfsense_vpn_diagnostics"
        required = grader.EXPECTED[task][1]
        self.assertTrue(grader.findings_cover(task, required,
            "CONNECTING; no proposal chosen; no active states for 10.44.0.0/24"))
        for text in [
            "CONNECTING; no proposal chosen; 20 active states for 10.44.0.0/24",
            "CONNECTING; no proposal chosen; no inactive states for 10.44.0.0/24",
            "CONNECTING; no active states for 10.44.0.0/24",
        ]:
            with self.subTest(text=text):
                self.assertFalse(grader.findings_cover(task, required, text))


if __name__=='__main__':unittest.main()

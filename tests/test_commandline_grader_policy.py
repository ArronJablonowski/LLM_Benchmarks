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

    def test_openwrt_quoted_zone_and_rejected_destination_equivalence(self):
        task = 'cli_openwrt_firewall_diagnostics'; required = grader.EXPECTED[task][1]
        text = "The 'lan' zone has forward drop. Firewall logs show a rejected connection attempt from a LAN client to 192.168.50.20 on port 8443."
        self.assertTrue(grader.findings_cover(task, required, text))
        for wrong in [text.replace("'lan' zone", "'guest' zone"), text.replace('port 8443', 'port 443'),
                      text.replace('192.168.50.20', '192.168.50.21'), text.replace('a rejected', 'an allowed')]:
            self.assertFalse(grader.findings_cover(task, required, wrong))

    def test_windows_encoded_parent_equivalence_keeps_required_facts(self):
        task = 'cli_windows_incident_response'; required = grader.EXPECTED[task][1]
        text = 'Connection to 198.51.100.42; parent process powershell with encoded command; SHA256 44AA9011'
        self.assertTrue(grader.findings_cover(task, required, text))
        for wrong in [text.replace('with encoded', 'without encoded'), text.replace('with encoded', 'with unencoded'),
                      text.replace('198.51.100.42', '198.51.100.43'), text.replace('44AA9011', '99BB9011')]:
            self.assertFalse(grader.findings_cover(task, required, wrong))


class OpenWrtObservedWordingTests(unittest.TestCase):
    wan = "OpenWrt 23.05.4; WAN pending; netifd wan udhcpc failed: 'no lease, failing' — DHCP client could not obtain a lease"
    firewall = "LAN forward policy is drop; guest forward policy is reject. forward rejected lan->guest dst=192.168.50.20 dpt=8443"

    def test_dhcp_client_failure_paraphrase_preserves_all_observations(self):
        task = 'cli_openwrt_network_recovery'
        self.assertTrue(grader.findings_cover(task, grader.EXPECTED[task][1], self.wan))
        for wrong in [self.wan.replace('udhcpc failed', 'udhcpc has not failed'),
                      self.wan.replace('could not obtain', 'did obtain'),
                      self.wan.replace('no lease, failing', 'lease obtained'),
                      self.wan.replace('pending', 'up'), self.wan.replace('23.05.4', '22.03.4')]:
            with self.subTest(wrong=wrong):
                self.assertFalse(grader.findings_cover(task, grader.EXPECTED[task][1], wrong))

    def test_explicit_lan_forward_policy_identifies_the_zone(self):
        task = 'cli_openwrt_firewall_diagnostics'
        self.assertTrue(grader.findings_cover(task, grader.EXPECTED[task][1], self.firewall))
        for wrong in [self.firewall.replace('LAN forward policy', 'guest forward policy'),
                      self.firewall.replace('is drop', 'is not drop'),
                      self.firewall.replace('forward rejected', 'forward accepted')]:
            with self.subTest(wrong=wrong):
                self.assertFalse(grader.findings_cover(task, grader.EXPECTED[task][1], wrong))

    def test_paraphrases_still_require_executed_commands_and_menu(self):
        for task, findings in [('cli_openwrt_network_recovery', self.wan),
                               ('cli_openwrt_firewall_diagnostics', self.firewall)]:
            commands, _, menu = grader.EXPECTED[task]
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); (p / '.benchmark-task-id').write_text(task)
                (p / 'answer.json').write_text(json.dumps(dict(commands=commands, findings=[findings],
                    actions=['Inspect the reported failure and apply a narrowly scoped repair'], menu_path=menu)))
                events = [dict(kind='run', value=cmd, ok=True) for cmd in commands]
                events.append(dict(kind='menu', value='>'.join(menu), ok=True))
                for transcript, verdict in [(events, 'pass'), (events[1:], 'fail'), (events[:-1], 'fail')]:
                    (p / 'transcript.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in transcript))
                    result = subprocess.run([sys.executable, str(GRADER), str(p)], capture_output=True, text=True)
                    self.assertEqual(json.loads(result.stdout)['verdict'], verdict, (task, transcript))


class RecoveryOutcomePlacementTests(unittest.TestCase):
    def setUp(self):
        self.actions = ['Verified Spooler is now Running after recovery']
        self.restart = dict(kind='run', value='Restart-Service -Name Spooler', ok=True,
                            output='Service Spooler is now Running')
        self.query = dict(kind='run', value='Get-Service -Name Spooler', ok=True,
                          output='Status Name DisplayName\nRunning Spooler Print Spooler')

    def test_documented_verified_action_counts_as_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); task = 'cli_powershell_services'
            (p / '.benchmark-task-id').write_text(task)
            commands = grader.EXPECTED[task][0] + ['Get-Service -Name Spooler']
            (p / 'answer.json').write_text(json.dumps(dict(commands=commands,
                findings=['Spooler was stopped', 'System event 7031'], actions=self.actions, menu_path=[])))
            events = [dict(kind='run', value=commands[0], ok=True, output='Stopped Spooler'),
                      dict(kind='run', value=commands[1], ok=True, output='Id 7031'), self.restart, self.query]
            (p / 'transcript.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            result = subprocess.run([sys.executable,str(GRADER),str(p)],capture_output=True,text=True)
            self.assertEqual(json.loads(result.stdout)['verdict'], 'pass')

    def test_plans_negation_and_missing_outcome_do_not_count(self):
        for action in ['Verify Spooler is now Running after recovery',
                       'Will verify Spooler is now Running after recovery',
                       'Not verified Spooler is now Running after recovery',
                       'Verified Spooler is not Running after recovery',
                       'Restarted Print Spooler service']:
            self.assertFalse(grader.documented_running_recovery([action], [self.restart,self.query]))

    def test_outcome_needs_successful_post_restart_observation_without_contradiction(self):
        stopped = dict(self.query, output='Status Name\nStopped Spooler')
        for events in [[self.query,self.restart], [self.restart],
                       [dict(self.restart,ok=False),self.query], [self.restart,dict(self.query,ok=False)],
                       [self.restart,stopped], [self.restart,self.query,stopped]]:
            self.assertFalse(grader.documented_running_recovery(self.actions,events))


if __name__=='__main__':unittest.main()

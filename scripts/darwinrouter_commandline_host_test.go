package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	stdruntime "runtime"
	"strings"
	"testing"

	"github.com/ArronJablonowski/DarwinRouter/runtime"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
)

func invoke(t *testing.T, tool sdk.Tool, args string) runtime.ToolResult {
	t.Helper()
	r, err := tool.Handler(context.Background(), json.RawMessage(args))
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestSimulatorRecoveryDoesNotManufactureEvidence(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "README.md"), []byte("Offline simulation"), 0600)
	ts := benchmarkTools(root, scenario{Commands: map[string]string{"uname -a": "kernel evidence"}})
	instructions := invoke(t, ts[0], `{}`)
	if !strings.Contains(instructions.Content, "kind=help") {
		t.Fatal("native help is undiscoverable")
	}
	failed := invoke(t, ts[1], `{"kind":"run","value":"dmesg unknown"}`)
	if !failed.Failed || !failed.Recoverable || failed.Effect != runtime.NoEffect || !strings.Contains(failed.Content, "kind=help") {
		t.Fatal(failed)
	}
	help := invoke(t, ts[1], `{"kind":"help","value":""}`)
	if help.Failed || !strings.Contains(help.Content, "uname -a") || strings.Contains(help.Content, "kernel evidence") {
		t.Fatal(help)
	}
	invoke(t, ts[2], `{"commands":["uname -a"],"findings":[],"actions":[]}`)
	var a answer
	data, _ := os.ReadFile(filepath.Join(root, "answer.json"))
	json.Unmarshal(data, &a)
	if len(a.Commands) != 0 {
		t.Fatal("help/fabricated answer counted as executed evidence", a)
	}
}

func TestMenuAnswerKeepsAuditedNavigationSteps(t *testing.T) {
	root := t.TempDir()
	ts := benchmarkTools(root, scenario{Menus: map[string]string{"1>3>2": "menu evidence"}})
	r := invoke(t, ts[1], `{"kind":"menu","value":"1>3>2"}`)
	if r.Failed {
		t.Fatal(r)
	}
	invoke(t, ts[2], `{"menu_path":["made up"],"findings":["observation"],"actions":["inspect"]}`)
	data, _ := os.ReadFile(filepath.Join(root, "answer.json"))
	var a answer
	json.Unmarshal(data, &a)
	if !reflect.DeepEqual(a.MenuPath, []string{"1", "3", "2"}) {
		t.Fatal(a.MenuPath)
	}
	transcript, _ := os.ReadFile(filepath.Join(root, "transcript.jsonl"))
	var event transcriptEvent
	json.Unmarshal(transcript, &event)
	if !event.OK || event.Value != "1>3>2" {
		t.Fatal("original simulator operation changed", event)
	}
}

func spoolerScenario(t *testing.T) scenario {
	t.Helper()
	_, source, _, _ := stdruntime.Caller(0)
	data, err := os.ReadFile(filepath.Join(filepath.Dir(source), "benchmark_tests", "commandline", "cli_powershell_services.json"))
	if err != nil {
		t.Fatal(err)
	}
	var descriptor struct {
		Lab scenario `json:"lab"`
	}
	if err := json.Unmarshal(data, &descriptor); err != nil {
		t.Fatal(err)
	}
	return descriptor.Lab
}

func TestRecoveryStatePersistsAndStaysWorkspaceScoped(t *testing.T) {
	lab := spoolerScenario(t)
	root := t.TempDir()
	ts := benchmarkTools(root, lab)
	query := `{"kind":"run","value":"Get-Service -Name Spooler"}`
	restart := `{"kind":"run","value":"Restart-Service -Name Spooler"}`
	if r := invoke(t, ts[1], query); !strings.Contains(r.Content, "Stopped") {
		t.Fatal(r)
	}
	if r := invoke(t, ts[1], `{"kind":"run","value":"unknown-restart"}`); !r.Failed {
		t.Fatal(r)
	}
	if r := invoke(t, ts[1], query); !strings.Contains(r.Content, "Stopped") {
		t.Fatal(r)
	}
	for i := 0; i < 2; i++ {
		if r := invoke(t, ts[1], restart); !strings.Contains(r.Content, "Running") {
			t.Fatal(r)
		}
		if r := invoke(t, ts[1], query); !strings.Contains(r.Content, "Running") || strings.Contains(r.Content, "Stopped") {
			t.Fatal(r)
		}
	}
	other := benchmarkTools(t.TempDir(), lab)
	if r := invoke(t, other[1], query); !strings.Contains(r.Content, "Stopped") {
		t.Fatal("state leaked across workspaces", r)
	}
	data, _ := os.ReadFile(filepath.Join(root, "transcript.jsonl"))
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	var before, after transcriptEvent
	json.Unmarshal([]byte(lines[0]), &before)
	json.Unmarshal([]byte(lines[len(lines)-1]), &after)
	if !strings.Contains(before.Output, "Stopped") || !strings.Contains(after.Output, "Running") {
		t.Fatal("observed state changes missing from durable transcript")
	}
}

func TestFailedAuditWriteDoesNotApplyRecovery(t *testing.T) {
	root := t.TempDir()
	ts := benchmarkTools(root, spoolerScenario(t))
	path := filepath.Join(root, "transcript.jsonl")
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	_, err := ts[1].Handler(context.Background(), json.RawMessage(`{"kind":"run","value":"Restart-Service -Name Spooler"}`))
	if err == nil {
		t.Fatal("audit failure ignored")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if r := invoke(t, ts[1], `{"kind":"run","value":"Get-Service -Name Spooler"}`); !strings.Contains(r.Content, "Stopped") {
		t.Fatal("uncommitted recovery changed state", r)
	}
}

func TestFutureSubmissionSealsEvidenceAndClosesToolUse(t *testing.T) {
	root := t.TempDir()
	ts := benchmarkToolsVersion(root, scenario{Commands: map[string]string{"uname -a": "observed"}}, "v3")
	bad := invoke(t, ts[1], `{"kind":"run","value":"uname --invented"}`)
	if !bad.Failed {
		t.Fatal("v3 silently repaired unexecuted flags")
	}
	invoke(t, ts[1], `{"kind":"run","value":"uname -a"}`)
	saved := invoke(t, ts[2], `{"findings":["observed"],"actions":["Preserve the collected evidence for operator review."]}`)
	if !saved.EndToolUse || saved.Effect != runtime.ConfirmedEffect {
		t.Fatal(saved)
	}
	before, _ := os.ReadFile(filepath.Join(root, "answer.json"))
	if _, err := os.Stat(filepath.Join(root, "submission.json")); err != nil {
		t.Fatal(err)
	}
	duplicate, err := ts[2].Handler(context.Background(), json.RawMessage(`{"findings":["overwrite"]}`))
	if err == nil || duplicate.Effect != runtime.NoEffect {
		t.Fatal("duplicate submission allowed")
	}
	after, _ := os.ReadFile(filepath.Join(root, "answer.json"))
	if string(before) != string(after) {
		t.Fatal("sealed answer overwritten")
	}
	_, err = ts[1].Handler(context.Background(), json.RawMessage(`{"kind":"run","value":"uname -a"}`))
	if err == nil {
		t.Fatal("post-submission mutation allowed")
	}
}

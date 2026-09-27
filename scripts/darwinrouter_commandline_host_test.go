package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
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

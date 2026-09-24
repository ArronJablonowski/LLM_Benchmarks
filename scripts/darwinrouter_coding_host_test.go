package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
)

func TestToolRepairContinuesWithoutExecutingTextAndPreservesAccounting(t *testing.T) {
	calls := 0
	run := func(_ context.Context, request sdk.Request) (sdk.Result, error) {
		calls++
		cost := 0.25
		if calls == 1 {
			return sdk.Result{TaskID: "first", Text: "<function=benchmark_list_files>\n</function>\n</tool_call>", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 10, OutputTokens: 3}}, nil
		}
		if request.ContinueTaskID != "first" || request.ModelID != "fixed" || !request.LocalRequired {
			t.Fatalf("lost continuation or routing constraint: %+v", request)
		}
		return sdk.Result{TaskID: "second", Text: "Implemented and tested", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 20, OutputTokens: 5}}, nil
	}
	out, err := runWithToolRepair(context.Background(), run, sdk.Request{ModelID: "fixed", LocalRequired: true}, true)
	if err != nil || calls != 2 || len(out.PreviousTaskIDs) != 1 || out.PreviousTaskIDs[0] != "first" || *out.RouteEstimatedCost != .5 || out.Usage.InputTokens != 30 || out.Usage.OutputTokens != 8 {
		t.Fatal(out, err, calls)
	}
}

func TestToolRepairIsBoundedAndDoesNotRetryExecutionErrors(t *testing.T) {
	for _, broken := range []bool{false, true} {
		calls := 0
		run := func(context.Context, sdk.Request) (sdk.Result, error) {
			calls++
			out := sdk.Result{TaskID: "task", Text: "<function=benchmark_list_files></tool_call>"}
			if broken {
				return out, errors.New("execution interrupted")
			}
			return out, nil
		}
		_, err := runWithToolRepair(context.Background(), run, sdk.Request{}, true)
		want := 3
		if broken {
			want = 1
		}
		if err == nil || calls != want {
			t.Fatal("unbounded repair or interrupted execution retried", calls, err)
		}
	}
}

func TestToolRepairKeepsIncompleteAccountingUnknown(t *testing.T) {
	calls := 0
	run := func(context.Context, sdk.Request) (sdk.Result, error) {
		calls++
		if calls == 1 {
			return sdk.Result{TaskID: "first", Text: "<function=benchmark_read_file></tool_call>"}, nil
		}
		cost := 1.0
		return sdk.Result{TaskID: "second", Text: "done", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 20}}, nil
	}
	out, err := runWithToolRepair(context.Background(), run, sdk.Request{}, true)
	if err != nil || out.RouteEstimatedCost != nil || out.Usage != nil {
		t.Fatal("partial accounting presented as total", out, err)
	}
}

func TestCodingCommandsConfineWorkspaceAndDoNotInheritSecrets(t *testing.T) {
	if _, err := os.Stat("/usr/bin/sandbox-exec"); err != nil {
		t.Skip("requires macOS sandbox-exec")
	}
	base := t.TempDir()
	root := filepath.Join(base, "workspace")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(base, "other-solution.txt")
	if err := os.WriteFile(outside, []byte("hidden solution"), 0600); err != nil {
		t.Fatal(err)
	}
	home := filepath.Join(base, "home")
	t.Setenv("DARWIN_TEST_SECRET", "must-not-reach-command")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("sandboxed command accessed network") }))
	defer server.Close()
	for _, tc := range []struct {
		command string
		success bool
	}{
		{`printf ok > local.txt; cat local.txt; test -z "$DARWIN_TEST_SECRET"`, true},
		{fmt.Sprintf("cat %q", outside), false},
		{fmt.Sprintf("/usr/bin/curl --max-time 2 %q", server.URL), false},
		{fmt.Sprintf("printf bad > %q", outside), false},
		{fmt.Sprintf("ln -s %q link; cat link", outside), false},
		{`python3 -c 'import sqlite3, json, sys; assert sys.version_info >= (3,10); print(json.dumps(sqlite3.connect(":memory:").execute("select 1").fetchone()))'`, true},
	} {
		cmd, err := codingCommand(context.Background(), root, home, tc.command)
		if err != nil {
			t.Fatal(err)
		}
		out, err := cmd.CombinedOutput()
		if (err == nil) != tc.success || strings.Contains(string(out), "hidden solution") {
			t.Fatalf("%q: %s %v", tc.command, out, err)
		}
	}
	body, err := os.ReadFile(outside)
	if err != nil || string(body) != "hidden solution" {
		t.Fatal("outside file changed", err)
	}
}

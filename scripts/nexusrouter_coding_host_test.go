package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	goruntime "runtime"
	"strings"
	"testing"
	"time"

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
		if request.ContinueTaskID != "first" || request.ModelID != "fixed" || !request.LocalRequired || request.ContextTokens != 65536 || len(request.Messages) != 0 {
			t.Fatalf("lost continuation or routing constraint: %+v", request)
		}
		return sdk.Result{TaskID: "second", Text: "Implemented and tested", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 20, OutputTokens: 5}}, nil
	}
	out, err := runWithToolRepair(context.Background(), run, sdk.Request{ModelID: "fixed", LocalRequired: true, ContextTokens: 65536, Messages: []providers.Message{{Role: "system", Content: "coding agent"}, {Role: "user", Content: "implement the task"}}}, true)
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
	canonicalOutside, err := filepath.EvalSymlinks(outside)
	if err != nil {
		t.Fatal(err)
	}
	alias := "/System/Volumes/Data" + canonicalOutside
	if _, err := os.Stat(alias); err != nil {
		t.Fatal("missing alias test fixture", err)
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
		{fmt.Sprintf("cat %q", alias), false},
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

func TestCodingCommandDirectoryHonorsSubdirectoriesAndRejectsEscapes(t *testing.T) {
	base := t.TempDir()
	root := filepath.Join(base, "workspace")
	subdir := filepath.Join(root, "package")
	if err := os.MkdirAll(subdir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(base, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "file.txt"), []byte("file"), 0600); err != nil {
		t.Fatal(err)
	}
	want, err := filepath.EvalSymlinks(subdir)
	if err != nil {
		t.Fatal(err)
	}
	for _, args := range [][2]string{{"", "package"}, {"package", ""}, {"", subdir}, {"package", "./package"}} {
		got, err := codingCommandDirectory(root, args[0], args[1])
		if err != nil || got != want {
			t.Fatalf("%v: directory=%q err=%v", args, got, err)
		}
	}
	for _, args := range [][2]string{{"", ".."}, {"", base}, {"", "escape"}, {"", "file.txt"}, {"", "missing"}, {"package", "."}} {
		if got, err := codingCommandDirectory(root, args[0], args[1]); err == nil {
			t.Fatalf("unsafe or invalid directory accepted: %v => %s", args, got)
		}
	}
	if _, err := os.Stat("/usr/bin/sandbox-exec"); err != nil {
		t.Skip("subdirectory execution requires macOS sandbox-exec")
	}
	cmd, err := codingCommand(context.Background(), root, filepath.Join(base, "home"), "pwd; printf done > result.txt")
	if err != nil {
		t.Fatal(err)
	}
	cmd.Dir = want
	if output, err := cmd.CombinedOutput(); err != nil || !strings.Contains(string(output), want) {
		t.Fatalf("command used wrong directory: %s %v", output, err)
	}
	if _, err := os.Stat(filepath.Join(subdir, "result.txt")); err != nil {
		t.Fatal("command did not execute in subdirectory", err)
	}
}

func TestCodingCommandOutputDistinguishesEmptyDiscoveryFromPassingTests(t *testing.T) {
	for _, output := range []string{"Ran 0 tests in 0.000s\n\nOK", "NO TESTS RAN", "ℹ tests 0\nℹ pass 0", "=== no tests ran in 0.01s ==="} {
		result := codingCommandOutput([]byte(output), nil)
		if !strings.Contains(result, output) || !strings.Contains(result, "command exit code: 0") || !strings.Contains(result, "Verification incomplete") {
			t.Fatal("empty discovery was not explained", result)
		}
	}
	for _, output := range []string{"Ran 3 tests in 0.001s\n\nOK", "ℹ tests 1\nℹ pass 1", "value = 'Ran 0 tests'"} {
		if result := codingCommandOutput([]byte(output), nil); strings.Contains(result, "Verification incomplete") {
			t.Fatal("ordinary output classified as empty discovery", result)
		}
	}
	failed := codingCommandOutput([]byte("ImportError: missing public export"), errors.New("exit status 1"))
	if !strings.Contains(failed, "ImportError") || !strings.Contains(failed, "command exited non-zero") || strings.Contains(failed, "exit code: 0") {
		t.Fatal("command failure misreported", failed)
	}
}

func TestCodingCommandSQLiteTemporaryDatabase(t *testing.T) {
	requireCodingIsolation(t)
	root := t.TempDir()
	home := filepath.Join(filepath.Dir(root), ".benchmark-home", filepath.Base(root))
	cmd, err := codingCommand(context.Background(), root, home, `python3 -c 'import tempfile, sqlite3; f=tempfile.NamedTemporaryFile(); c=sqlite3.connect(f.name); c.execute("create table t(x)"); c.execute("insert into t values (42)"); c.commit(); assert c.execute("select x from t").fetchone()==(42,)'`)
	if err != nil {
		t.Fatal(err)
	}
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("SQLite scratch database failed: %v: %s", err, out)
	}
}

func TestCodingCommandHeredocInIsolatedScratchHome(t *testing.T) {
	requireCodingIsolation(t)
	root := t.TempDir()
	home := filepath.Join(filepath.Dir(root), ".benchmark-home", filepath.Base(root))
	cmd, err := codingCommand(context.Background(), root, home, "python3 - <<'PY'\nprint('heredoc-ok')\nPY")
	if err != nil {
		t.Fatal(err)
	}
	if out, err := cmd.CombinedOutput(); err != nil || !strings.Contains(string(out), "heredoc-ok") {
		t.Fatalf("heredoc failed: %v: %s", err, out)
	}
}

func TestCodingEstimatorOptInValidation(t *testing.T) {
	if factory, err := codingEstimatorFactory(""); factory != nil || err != nil {
		t.Fatalf("default changed: %v", err)
	}
	for name, body := range map[string]string{"malformed": "{", "unknown": `{"unexpected":true}`, "trailing": `{} {}`, "oversized": strings.Repeat(" ", 16385)} {
		t.Run(name, func(t *testing.T) {
			p := filepath.Join(t.TempDir(), "identity.json")
			if err := os.WriteFile(p, []byte(body), 0600); err != nil {
				t.Fatal(err)
			}
			if factory, err := codingEstimatorFactory(p); err == nil || factory != nil {
				t.Fatal("invalid opt-in accepted")
			}
		})
	}
}

// Capability skips are explicit gaps, never successful Linux isolation evidence.
func requireCodingIsolation(t *testing.T) {
	t.Helper()
	if err := codingIsolationCheck(t.TempDir()); err != nil {
		t.Skipf("isolation execution unavailable (not qualified): %v", err)
	}
}
func TestLinuxCodingIsolationFailsClosedOrQualifies(t *testing.T) {
	if goruntime.GOOS != "linux" {
		t.Skip("Linux policy probe")
	}
	err := codingIsolationCheck(t.TempDir())
	if err != nil {
		if !strings.Contains(err.Error(), "isolation") {
			t.Fatal(err)
		}
		t.Logf("FAIL-CLOSED: Linux execution NOT qualified: %v", err)
	} else {
		t.Log("Linux execution preflight available; adversarial tests still required")
	}
}
func TestCodingOutputBufferIsBounded(t *testing.T) {
	var tail codingTail
	tail.Write([]byte(strings.Repeat("a", outputLimit*3)))
	tail.Write([]byte("END"))
	if len(tail.body) != outputLimit || !strings.HasSuffix(string(tail.body), "END") {
		t.Fatal("unbounded or incorrect output tail")
	}
}
func TestLinuxCodingRuntimePlanAndEnvironment(t *testing.T) {
	if goruntime.GOOS != "linux" {
		t.Skip("Linux runtime plan")
	}
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("LD_PRELOAD", "/private/malicious.so")
	t.Setenv("SECRET_TOKEN", "sensitive")
	cmd, err := codingCommand(context.Background(), root, home, "true")
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		for _, f := range cmd.files {
			f.Close()
		}
	}()
	args := strings.Join(cmd.Args, " ")
	for _, flag := range []string{"--unshare-all", "--unshare-user", "--disable-userns", "--assert-userns-disabled", "--cap-drop ALL", "--clearenv", "--remount-ro /", "--bind-fd", "--ro-bind-fd"} {
		if !strings.Contains(args, flag) {
			t.Fatal("missing isolation control", flag)
		}
	}
	for _, bad := range []string{"--share-net", "--ro-bind / /", "--bind / /", "sensitive", "malicious.so", "--dev-bind"} {
		if strings.Contains(args, bad) {
			t.Fatal("unsafe runtime plan", bad)
		}
	}
	if strings.Contains(strings.Join(cmd.Env, " "), "SECRET") || len(cmd.ExtraFiles) < 2 {
		t.Fatal("unsafe environment or unpinned workspace")
	}
}
func TestLinuxCodingAdversarialBoundaries(t *testing.T) {
	if goruntime.GOOS != "linux" {
		t.Skip("Linux isolation")
	}
	requireCodingIsolation(t)
	base := t.TempDir()
	root := filepath.Join(base, "workspace")
	home := filepath.Join(base, "scratch")
	os.Mkdir(root, 0700)
	outside := filepath.Join(base, "solution")
	os.WriteFile(outside, []byte("other-task-secret"), 0600)
	os.Symlink(outside, filepath.Join(root, "escape"))
	t.Setenv("SECRET_TOKEN", "credential-value")
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("host network escaped") }))
	defer server.Close()
	for _, command := range []string{
		"cat " + outside, "printf changed > " + outside, "cat escape", "printf changed > escape", "cat /etc/passwd", "cat /home/arron_spark/.ssh/id_ed25519", "ls /run/user",
		`python3 -c 'import urllib.request; urllib.request.urlopen("` + server.URL + `", timeout=.3)'`,
		`python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/run/systemd/private")'`,
		`python3 -c 'import os; assert os.environ.get("SECRET_TOKEN") is not None'`,
		`python3 -c 'import os; os.open("/proc/1/root/etc/passwd", os.O_RDONLY)'`,
		`python3 -c 'import os; fds=[n for n in os.listdir("/proc/self/fd") if int(n)>2]; assert any(os.path.exists("/proc/self/fd/"+n+"/../solution") for n in fds)'`,
	} {
		cmd, err := codingCommand(context.Background(), root, home, command)
		if err != nil {
			t.Fatal(err)
		}
		out, err := cmd.CombinedOutput()
		if err == nil || strings.Contains(string(out), "other-task-secret") {
			t.Fatal("escape succeeded", command, string(out))
		}
	}
	b, err := os.ReadFile(outside)
	if err != nil || string(b) != "other-task-secret" {
		t.Fatal("outside modified")
	}
	sub := filepath.Join(root, "sub")
	os.Mkdir(sub, 0700)
	cmd, err := codingCommand(context.Background(), root, home, "pwd; printf edited > result")
	if err != nil {
		t.Fatal(err)
	}
	cmd.Dir = sub
	if out, err := cmd.CombinedOutput(); err != nil || !strings.Contains(string(out), sub) {
		t.Fatal("subdirectory failed", string(out), err)
	}
	if b, err := os.ReadFile(filepath.Join(sub, "result")); err != nil || string(b) != "edited" {
		t.Fatal("edit not observed", err)
	}
}
func TestLinuxCodingBackgroundCleanup(t *testing.T) {
	if goruntime.GOOS != "linux" {
		t.Skip("Linux PID namespace")
	}
	requireCodingIsolation(t)
	for _, timeout := range []bool{false, true} {
		t.Run(fmt.Sprint(timeout), func(t *testing.T) {
			root, home := t.TempDir(), t.TempDir()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			command := `python3 - <<'PY'
import subprocess, time
subprocess.Popen(['python3','-c',"import time; time.sleep(1.5); open('leaked-child','w').write('bad')"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
open('child-started','w').write('yes')
` + map[bool]string{false: "", true: "time.sleep(30)\n"}[timeout] + "PY"
			cmd, err := codingCommand(ctx, root, home, command)
			if err != nil {
				t.Fatal(err)
			}
			if timeout {
				go func() {
					for i := 0; i < 100; i++ {
						if _, e := os.Stat(filepath.Join(root, "child-started")); e == nil {
							cancel()
							return
						}
						time.Sleep(20 * time.Millisecond)
					}
					cancel()
				}()
			}
			_, err = cmd.CombinedOutput()
			if (err != nil) != timeout {
				t.Fatal("unexpected command result", err)
			}
			if _, err := os.Stat(filepath.Join(root, "child-started")); err != nil {
				t.Fatal("child was never launched")
			}
			time.Sleep(2 * time.Second)
			if _, err := os.Stat(filepath.Join(root, "leaked-child")); !os.IsNotExist(err) {
				t.Fatal("background child survived", err)
			}
		})
	}
}

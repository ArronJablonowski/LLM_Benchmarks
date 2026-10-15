// Command nexusrouter_coding_host runs one isolated coding benchmark through
// NexusRouter's SDK tool loop. All filesystem tools are rooted in workspace.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	goruntime "runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	musecounter "local-llm-benchmark-suite/adapters/musecounter"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ArronJablonowski/DarwinRouter/runtime"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"github.com/ArronJablonowski/DarwinRouter/tools"
)

const outputLimit = 64 << 10

func main() {
	preflight := flag.Bool("check-isolation", false, "verify command isolation without model inference")
	config := flag.String("config", "", "NexusRouter configuration")
	database := flag.String("database", "", "campaign telemetry database")
	workspace := flag.String("workspace", "", "absolute isolated workspace")
	model := flag.String("model", "auto", "NexusRouter model ID")
	prompt := flag.String("prompt", "", "coding task")
	timeout := flag.Duration("timeout", 2*time.Hour, "task deadline")
	contextTokens := flag.Int("context-tokens", 0, "explicit context budget; zero selects automatically")
	maxTurns := flag.Int("max-turns", 96, "maximum coding-agent turns")
	finalize := flag.Bool("finalize", false, "record a short completed-task result without coding tools")
	flag.Parse()
	if *preflight {
		root, err := os.MkdirTemp("", "coding-isolation-probe-")
		if err != nil {
			fatal(err)
		}
		defer os.RemoveAll(root)
		err = codingIsolationCheck(root)
		_ = os.RemoveAll(root)
		if err != nil {
			fatal(err)
		}
		fmt.Println("coding command isolation available")
		return
	}
	if *contextTokens < 0 || int64(*contextTokens) > providers.MaxOutputTokens {
		fatal(errors.New("invalid context token budget"))
	}
	if *config == "" || *database == "" || *workspace == "" || *prompt == "" || !filepath.IsAbs(*workspace) || !filepath.IsAbs(*database) || *maxTurns < 2 || *maxTurns > 96 {
		fatal(errors.New("config, database, absolute workspace, prompt, and max-turns in 2..96 are required"))
	}
	root, err := filepath.EvalSymlinks(*workspace)
	if err != nil {
		fatal(err)
	}
	workspaceFS, err := os.OpenRoot(root)
	if err != nil {
		fatal(err)
	}
	defer workspaceFS.Close()
	if !*finalize {
		if err := codingIsolationCheck(root); err != nil {
			fatal(err)
		}
	}
	// A recovered fixture is renamed and replaced at the same path. Include its
	// filesystem identity so an interrupted prior attempt cannot block the new
	// fixture, while retries against the same directory retain their lease scope.
	workspaceInfo, err := workspaceFS.Stat(".")
	if err != nil {
		fatal(err)
	}
	workspaceStat, ok := workspaceInfo.Sys().(*syscall.Stat_t)
	if !ok {
		fatal(errors.New("workspace identity unavailable"))
	}
	workspaceDigest := sha256.Sum256([]byte(fmt.Sprintf("%s:%d:%d", root, workspaceStat.Dev, workspaceStat.Ino)))
	toolScope := fmt.Sprintf("benchmark-%x", workspaceDigest[:16])
	resolve := func(name string) (string, error) {
		if name == "" {
			return "", errors.New("invalid workspace path")
		}
		path := filepath.Clean(name)
		if !filepath.IsAbs(path) {
			path = filepath.Join(root, path)
		}
		rel, err := filepath.Rel(root, path)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
			return "", errors.New("path escapes workspace")
		}
		return rel, nil
	}
	list := sdk.Tool{Tool: providers.Tool{Name: "benchmark_list_files", Description: "List regular files recursively. An empty path lists the workspace root; depth optionally limits directory traversal.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","maxLength":4096},"depth":{"type":"integer","minimum":1,"maximum":32}},"additionalProperties":false}`)}, Scope: toolScope, ReadOnly: true, Behavior: tools.BehaviorReadOnly, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path  string `json:"path"`
			Depth int    `json:"depth"`
		}
		if err := json.Unmarshal(raw, &in); err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		start := "."
		if in.Path != "" {
			var err error
			start, err = resolve(in.Path)
			if err != nil {
				return runtime.ToolResult{Content: "invalid workspace path: " + err.Error(), Effect: runtime.NoEffect}, nil
			}
		}
		var names []string
		truncated := false
		err := fs.WalkDir(workspaceFS.FS(), start, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if in.Depth > 0 && entry.IsDir() && path != start {
				rel, _ := filepath.Rel(start, path)
				if strings.Count(rel, string(os.PathSeparator))+1 >= in.Depth {
					return filepath.SkipDir
				}
			}
			if entry.IsDir() && (entry.Name() == ".git" || entry.Name() == "node_modules" || entry.Name() == "__pycache__" || entry.Name() == ".venv" || entry.Name() == ".cache") && path != "." {
				return filepath.SkipDir
			}
			if !entry.Type().IsRegular() {
				return nil
			}
			names = append(names, path)
			if len(names) >= 512 {
				truncated = true
				return filepath.SkipAll
			}
			return nil
		})
		if err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, err
		}
		sort.Strings(names)
		if truncated {
			names = append(names, "[file list truncated at 512 entries]")
		}
		return runtime.ToolResult{Content: strings.Join(names, "\n"), Effect: runtime.NoEffect}, nil
	}}
	read := sdk.Tool{Tool: providers.Tool{Name: "benchmark_read_file", Description: "Read one UTF-8 file relative to the coding workspace, up to 64 KiB. Optional line_start and line_end select an inclusive line range.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096},"line_start":{"type":"integer","minimum":1},"line_end":{"type":"integer","minimum":1}},"required":["path"],"additionalProperties":false}`)}, Scope: toolScope, ReadOnly: true, Behavior: tools.BehaviorReadOnly, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path      string `json:"path"`
			LineStart int    `json:"line_start"`
			LineEnd   int    `json:"line_end"`
		}
		if json.Unmarshal(raw, &in) != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		path, err := resolve(in.Path)
		if err != nil {
			return runtime.ToolResult{Content: "invalid workspace path: " + err.Error(), Effect: runtime.NoEffect}, nil
		}
		if entries, dirErr := fs.ReadDir(workspaceFS.FS(), path); dirErr == nil {
			names := make([]string, 0, len(entries))
			for _, entry := range entries {
				names = append(names, entry.Name())
			}
			sort.Strings(names)
			return runtime.ToolResult{Content: strings.Join(names, "\n"), Effect: runtime.NoEffect}, nil
		}
		file, err := workspaceFS.Open(path)
		if err != nil {
			return runtime.ToolResult{Content: "file unavailable", Effect: runtime.NoEffect}, nil
		}
		defer file.Close()
		body, err := io.ReadAll(io.LimitReader(file, outputLimit+1))
		if err != nil || len(body) > outputLimit {
			return runtime.ToolResult{Content: "file unavailable or too large", Effect: runtime.NoEffect}, nil
		}
		if ctx.Err() != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, ctx.Err()
		}
		if in.LineStart > 0 || in.LineEnd > 0 {
			lines := strings.Split(string(body), "\n")
			start := in.LineStart
			if start == 0 {
				start = 1
			}
			end := in.LineEnd
			if end == 0 || end > len(lines) {
				end = len(lines)
			}
			if start > end {
				return runtime.ToolResult{Content: "line range is empty", Effect: runtime.NoEffect}, nil
			}
			body = []byte(strings.Join(lines[start-1:end], "\n"))
		}
		return runtime.ToolResult{Content: string(body), Effect: runtime.NoEffect}, nil
	}}
	write := sdk.Tool{Tool: providers.Tool{Name: "benchmark_write_file", Description: "Create or replace one UTF-8 file relative to the coding workspace. Supply content for a full replacement; use benchmark_run_command for patches.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096},"content":{"type":"string","maxLength":131072},"patch":{"type":"string","maxLength":131072},"line_start":{"type":"integer","minimum":1},"line_end":{"type":"integer","minimum":1}},"required":["path"],"additionalProperties":false}`)}, Scope: toolScope, Behavior: tools.BehaviorIdempotentWrite, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path      string  `json:"path"`
			Content   *string `json:"content"`
			Patch     string  `json:"patch"`
			LineStart int     `json:"line_start"`
			LineEnd   int     `json:"line_end"`
		}
		if json.Unmarshal(raw, &in) != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		path, err := resolve(in.Path)
		if err != nil {
			return runtime.ToolResult{Content: "No file was changed: " + err.Error() + ". Write only inside the supplied workspace.", Effect: runtime.NoEffect}, nil
		}
		if in.Content == nil {
			return runtime.ToolResult{Content: "No file was changed. Supply content to replace the file, or use benchmark_run_command to apply a patch. Use benchmark_read_file to inspect lines.", Effect: runtime.NoEffect}, nil
		}
		if ctx.Err() != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, ctx.Err()
		}
		if err = workspaceFS.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return runtime.ToolResult{Effect: runtime.UncertainEffect}, err
		}
		if err = workspaceFS.WriteFile(path, []byte(*in.Content), 0600); err != nil {
			return runtime.ToolResult{Effect: runtime.UncertainEffect}, err
		}
		return runtime.ToolResult{Content: "file written", Effect: runtime.ConfirmedEffect}, nil
	}}
	run := sdk.Tool{Tool: providers.Tool{Name: "benchmark_run_command", Description: "Run a shell command inside the isolated coding workspace. Optional workdir (or path alias) selects an existing subdirectory. Paths outside this workspace are unreadable. Read the fixture specification, create the implementation here, and run its tests; searching for another solution is prohibited. Output is capped at 64 KiB and includes command status; zero discovered tests do not validate the implementation.", Parameters: json.RawMessage(`{"type":"object","properties":{"command":{"type":"string","minLength":1,"maxLength":8192},"path":{"type":"string","maxLength":4096},"workdir":{"type":"string","maxLength":4096}},"required":["command"],"additionalProperties":false}`)}, Scope: toolScope, Behavior: tools.BehaviorIdempotentWrite, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Command string `json:"command"`
			Path    string `json:"path"`
			Workdir string `json:"workdir"`
		}
		if json.Unmarshal(raw, &in) != nil || strings.ContainsRune(in.Command, '\x00') {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid command")
		}
		workdir, err := codingCommandDirectory(root, in.Path, in.Workdir)
		if err != nil {
			return runtime.ToolResult{Content: "No command was executed: " + err.Error(), Effect: runtime.NoEffect, Failed: true, Recoverable: true}, nil
		}
		bounded, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		toolHome := filepath.Join(filepath.Dir(root), ".benchmark-home", filepath.Base(root))
		cmd, err := codingCommand(bounded, root, toolHome, in.Command)
		if err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, err
		}
		cmd.Dir = workdir
		body, err := cmd.CombinedOutput()
		// Non-zero exits and empty test discovery are observations for the coding
		// loop. Keep them visible so the agent can repair its code or test command.
		return runtime.ToolResult{Content: codingCommandOutput(body, err), Effect: runtime.ConfirmedEffect}, nil
	}}
	definitions := []sdk.Tool{list, read, write, run}
	var policy *sdk.ToolPolicy = &sdk.ToolPolicy{Default: tools.Ask}
	var reviewer sdk.ApprovalReviewer = func(_ context.Context, p sdk.ApprovalPrompt) (string, bool, error) {
		return "coding-benchmark", true, nil
	}
	if *finalize {
		definitions, policy, reviewer = nil, nil, nil
	}
	turnLimit := strconv.Itoa(*maxTurns)
	identityPath := os.Getenv("NEXUSROUTER_BENCH_MUSE_IDENTITY")
	if identityPath == "" {
		identityPath = os.Getenv("DARWIN_BENCH_MUSE_IDENTITY") // Legacy configuration compatibility.
	}
	estimatorFactory, err := codingEstimatorFactory(identityPath)
	if err != nil {
		fatal(err)
	}
	client, err := sdk.New(sdk.ConfigOptions{ContextEstimatorFactory: estimatorFactory, ProjectFile: *config, Overrides: map[string]string{"telemetry.database": *database, "runtime.max_turns": turnLimit, "tools.max_turns": turnLimit, "tools.read_root": root}, LookupSecret: os.Getenv, Tools: definitions, ToolPolicy: policy, ApprovalReviewer: reviewer})
	if err != nil {
		fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	suffix := "\n\nThis isolated benchmark fixture may contain only a specification and project metadata. There is no existing solution to locate. Read the fixture specification, create the missing implementation files in this workspace immediately, then run focused tests and repair failures. Do not search outside this workspace for code or fixtures; those paths are inaccessible. Invoke the provided tools through native function calls; writing tool-call markup in your answer does not execute a tool."
	if *finalize {
		suffix = "\n\nThe external objective grader has finished evaluating the workspace. Respond briefly that the evaluation handoff is complete; do not request or invoke tools."
	}
	request := sdk.Request{Version: 1, ModelID: *model, Messages: []providers.Message{
		{Role: "system", Content: "You are an autonomous coding agent. Complete the requested implementation by editing files in the supplied workspace, then run focused tests and fix errors. The workspace may contain only a specification and scaffolding; missing implementation files are your responsibility to create. Inspect the specification, package entry points, and existing tests briefly, then implement. Preserve the public names, imports, exports, and call signatures required by the specification and existing callers. Verify those public interfaces with import or invocation smoke checks. Use the project's existing test framework and command; for standard-library-only Python without a configured framework, use discoverable unittest.TestCase tests. Confirm that tests were actually discovered and executed: a zero-test run or a successful import alone does not validate behavior. Do not remove or weaken existing tests to obtain a passing run. Do not search system directories or other workspaces for pre-existing solutions. Use native tool calls, not printed tool markup. Do not finish with a plan or claim changes you did not make. Report the exact checks run and any failures or unverified requirements."},
		{Role: "user", Content: *prompt + suffix},
	}, Domain: "code", Profile: "benchmark", LocalRequired: true, ContextTokens: *contextTokens}
	if *finalize {
		request.Messages = nil
		request.Prompt = *prompt + suffix
	}
	result, err := runWithToolRepair(ctx, client.Run, request, !*finalize)
	if err != nil {
		body, _ := json.Marshal(result)
		var output map[string]any
		_ = json.Unmarshal(body, &output)
		output["error"] = err.Error()
		encoded, _ := json.Marshal(output)
		fmt.Println(string(encoded))
		os.Exit(1)
	}
	encoded, _ := json.Marshal(result)
	fmt.Println(string(encoded))
}

func runWithToolRepair(ctx context.Context, run func(context.Context, sdk.Request) (sdk.Result, error), request sdk.Request, repairEnabled bool) (sdk.Result, error) {
	result, err := run(ctx, request)
	// Some local models emit their tool template as answer text. Never execute
	// that text. Give the model a bounded opportunity to use the native tool
	// protocol through a durable continuation, with the same workspace policy.
	for repair := 0; repairEnabled && err == nil && repair < 2 && textualToolCall(result.Text); repair++ {
		previous := result
		request.ContinueTaskID = previous.TaskID
		request.Messages = nil
		request.Prompt = "Your last answer contained tool-call markup as plain text, so that text executed no tool. Continue the original task using the provided native function-call interface. Do not repeat XML or tool-call markup in your final answer. Implement and test the changes before reporting completion."
		result, err = run(ctx, request)
		result.PreviousTaskIDs = append(append(append([]string{}, previous.PreviousTaskIDs...), previous.TaskID), result.PreviousTaskIDs...)
		if previous.RouteEstimatedCost != nil && result.RouteEstimatedCost != nil {
			cost := *previous.RouteEstimatedCost + *result.RouteEstimatedCost
			result.RouteEstimatedCost = &cost
		} else {
			result.RouteEstimatedCost = nil
		}
		if previous.Usage != nil && result.Usage != nil {
			result.Usage.InputTokens += previous.Usage.InputTokens
			result.Usage.OutputTokens += previous.Usage.OutputTokens
		} else {
			result.Usage = nil
		}
	}
	if err == nil && repairEnabled && textualToolCall(result.Text) {
		err = errors.New("native tool-call protocol repair exhausted")
	}
	return result, err
}

func textualToolCall(text string) bool {
	return strings.Contains(text, "</tool_call>") && strings.Contains(text, "<function=benchmark_")
}

func fatal(err error) {
	body, _ := json.Marshal(map[string]string{"error": err.Error()})
	fmt.Fprintln(os.Stderr, string(body))
	os.Exit(1)
}

func codingCommandDirectory(root, path, workdir string) (string, error) {
	if path != "" && workdir != "" && filepath.Clean(path) != filepath.Clean(workdir) {
		return "", errors.New("path and workdir must identify the same directory; supply only one")
	}
	if workdir == "" {
		workdir = path
	}
	if workdir == "" {
		workdir = "."
	}
	if !filepath.IsAbs(workdir) {
		workdir = filepath.Join(root, workdir)
	}
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	workdir, err = filepath.EvalSymlinks(workdir)
	if err != nil {
		return "", errors.New("command directory does not exist")
	}
	rel, err := filepath.Rel(root, workdir)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return "", errors.New("command directory escapes workspace")
	}
	info, err := os.Stat(workdir)
	if err != nil || !info.IsDir() {
		return "", errors.New("command workdir must be a directory")
	}
	return workdir, nil
}

var zeroTestsOutput = regexp.MustCompile(`(?im)^\s*(?:Ran 0 tests\b|NO TESTS RAN\b|(?:ℹ\s*)?tests\s*[:=]?\s+0\b|(?:=+\s*)?no tests ran\b)`)

func codingCommandOutput(body []byte, err error) string {
	emptyTests := zeroTestsOutput.Match(body)
	if len(body) > outputLimit {
		body = body[len(body)-outputLimit:]
	}
	result := string(body)
	if err != nil {
		result += fmt.Sprintf("\ncommand exited non-zero: %v", err)
	} else {
		result += "\ncommand exit code: 0"
	}
	if emptyTests {
		result += "\nVerification incomplete: the test runner discovered zero tests. Check the working directory, discovery pattern, and test framework, then run actual behavioral tests before claiming success."
	}
	return result
}

// codingCommand enforces the same workspace boundary for shell subprocesses
// as the file tools. Neither platform has an unsandboxed fallback.
func codingCommand(ctx context.Context, root, home, command string) (*codingProcess, error) {
	if goruntime.GOOS == "linux" {
		return linuxCodingCommand(ctx, root, home, command)
	}
	if goruntime.GOOS != "darwin" {
		return nil, errors.New("coding command isolation unsupported on this platform")
	}
	return macCodingCommand(ctx, root, home, command)
}
func macCodingCommand(ctx context.Context, root, home, command string) (*codingProcess, error) {
	if _, err := os.Stat("/usr/bin/sandbox-exec"); err != nil {
		return nil, errors.New("coding command isolation requires macOS sandbox-exec")
	}
	tmp := filepath.Join(home, "tmp")
	if err := os.MkdirAll(tmp, 0700); err != nil {
		return nil, err
	}
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, err
	}
	home, err = filepath.EvalSymlinks(home)
	if err != nil {
		return nil, err
	}
	profile := `(version 1)(deny default)(allow process*)(allow sysctl-read)(allow mach-lookup)(allow file-read* (literal "/"))`
	for _, path := range []string{"/System", "/usr", "/bin", "/sbin", "/Library", "/opt/homebrew", "/private/etc", "/dev", root, home} {
		profile += "(allow file-read* (subpath " + strconv.Quote(path) + "))"
	}
	// The Python launchers resolve symlinks through these parent paths. Grant
	// metadata for the path components only, never directory enumeration outside
	// the fixture; a global metadata grant lets agents search other workspaces.
	for _, path := range []string{"/opt", "/var", "/private", "/private/var", "/private/var/select", "/private/var/select/developer_dir"} {
		profile += "(allow file-read-metadata (literal " + strconv.Quote(path) + "))"
	}
	// SQLite resolves every component of its scratch database path. The
	// isolated home is a sibling of the workspace, so its ancestors also
	// need metadata access (not directory listing or file contents).
	for _, base := range []string{root, home} {
		for parent := filepath.Dir(base); parent != "/"; parent = filepath.Dir(parent) {
			profile += "(allow file-read-metadata (literal " + strconv.Quote(parent) + "))"
		}
	}
	for _, path := range []string{root, home, "/dev/null"} {
		profile += "(allow file-write* (subpath " + strconv.Quote(path) + "))"
	}
	cmd := exec.CommandContext(ctx, "/usr/bin/sandbox-exec", "-p", profile, "/bin/zsh", "-f", "-c", command)
	cmd.Dir = root
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	cmd.WaitDelay = 2 * time.Second
	// Preserve tool discovery, not ambient credentials or shell startup hooks.
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + home, "TMPDIR=" + filepath.Join(home, "tmp"), "TMPPREFIX=" + filepath.Join(home, "tmp", "zsh"), "LANG=en_US.UTF-8", "PYTHONDONTWRITEBYTECODE=1"}
	return &codingProcess{Cmd: cmd}, nil
}

// Explicit opt-in file contains accepted identity and verified asset paths.
// Absence preserves the normal conservative estimator.
func codingEstimatorFactory(path string) (sdk.ContextEstimatorFactory, error) {
	if path == "" {
		return nil, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var cfg struct {
		Identity                  musecounter.Identity
		TokenizerPath, ConfigPath string
	}
	body, err := io.ReadAll(io.LimitReader(f, 16385))
	if err != nil {
		return nil, err
	}
	if len(body) > 16384 {
		return nil, errors.New("accounting configuration too large")
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&cfg); err != nil {
		return nil, err
	}
	var extra any
	if err = decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("invalid accounting configuration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return musecounter.NewFactory(ctx, cfg.Identity, cfg.TokenizerPath, cfg.ConfigPath)
}

// codingProcess owns pinned bind descriptors and a bounded combined-output tail.
// The whole process group is cleaned on success too, not only on cancellation.
type codingProcess struct {
	*exec.Cmd
	files []*os.File
}
type codingTail struct {
	mu   sync.Mutex
	body []byte
}

func (w *codingTail) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	n := len(p)
	if len(p) >= outputLimit {
		w.body = append(w.body[:0], p[len(p)-outputLimit:]...)
	} else {
		if len(w.body)+len(p) > outputLimit {
			w.body = w.body[len(w.body)+len(p)-outputLimit:]
		}
		w.body = append(w.body, p...)
	}
	return n, nil
}
func (p *codingProcess) CombinedOutput() ([]byte, error) {
	defer func() {
		for _, f := range p.files {
			_ = f.Close()
		}
	}()
	var tail codingTail
	p.Stdout = &tail
	p.Stderr = &tail
	err := p.Run()
	if p.Process != nil {
		_ = syscall.Kill(-p.Process.Pid, syscall.SIGKILL)
	}
	return bytes.Clone(tail.body), err
}

func codingIsolationCheck(root string) error {
	home, err := os.MkdirTemp("", "coding-private-home-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(home)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	// This is deliberately a real sandbox invocation, not presence of bwrap.
	probe := `python3 - <<'PY'
import os, sqlite3, tempfile, socket, sys
if sys.platform == 'linux': assert not os.path.exists('/etc/passwd') and not os.path.exists('/run')
with tempfile.NamedTemporaryFile() as f:
 c=sqlite3.connect(f.name); c.execute('create table t(x)'); c.close()
s=socket.socket();s.settimeout(.1)
try:
 s.connect(('127.0.0.1', 9));raise RuntimeError('network unexpectedly reachable')
except OSError:pass
print('isolation-probe-ok')
PY`
	if goruntime.GOOS == "linux" {
		for _, ns := range []string{"user", "mnt", "pid", "net", "ipc", "uts"} {
			path := "/proc/self/ns/" + ns
			original, e := os.Readlink(path)
			if e != nil {
				return errors.New("cannot inspect host isolation namespaces")
			}
			check := "assert os.readlink(" + strconv.Quote(path) + ") != " + strconv.Quote(original) + "\n"
			probe = strings.Replace(probe, "print('isolation-probe-ok')", check+"print('isolation-probe-ok')", 1)
		}
	}
	cmd, err := codingCommand(ctx, root, home, probe)
	if err != nil {
		return err
	}
	out, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(out), "isolation-probe-ok") {
		return fmt.Errorf("coding command isolation unavailable; no model inference permitted: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// linuxCodingCommand exposes a fresh mount/PID/user/network/IPC/UTS namespace.
// Only pinned workspace/scratch descriptors are writable host mounts. Runtime
// files are read-only; there is no host root, home, /run, or host /proc mount.
func linuxCodingCommand(ctx context.Context, root, home, command string) (result *codingProcess, err error) {
	if _, err = os.Stat("/usr/bin/bwrap"); err != nil {
		return nil, errors.New("Linux coding isolation requires /usr/bin/bwrap with working user, mount, PID and network namespaces")
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return nil, err
	}
	if !filepath.IsAbs(root) || root == "/" {
		return nil, errors.New("invalid coding workspace")
	}
	if err = os.MkdirAll(home, 0700); err != nil {
		return nil, err
	}
	home, err = filepath.EvalSymlinks(home)
	if err != nil {
		return nil, err
	}
	for _, pair := range [][2]string{{root, home}, {home, root}} {
		rel, e := filepath.Rel(pair[0], pair[1])
		if e != nil || rel == "." || rel != ".." && !strings.HasPrefix(rel, "../") {
			return nil, errors.New("workspace and private scratch must be disjoint")
		}
	}
	h, err := os.OpenRoot(home)
	if err != nil {
		return nil, err
	}
	err = h.MkdirAll("tmp", 0700)
	h.Close()
	if err != nil {
		return nil, err
	}
	result = &codingProcess{}
	owner := result
	defer func() {
		if err != nil {
			for _, f := range owner.files {
				f.Close()
			}
		}
	}()
	args := []string{"--unshare-all", "--unshare-user", "--die-with-parent", "--new-session", "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL", "--clearenv", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev"}
	// Stable descriptor mounts prevent source path/symlink replacement races.
	pin := func(path, dest string, writable bool) error {
		f, e := os.Open(path)
		if e != nil {
			return e
		}
		st, e := f.Stat()
		if e != nil {
			f.Close()
			return e
		}
		if !writable {
			v, ok := st.Sys().(*syscall.Stat_t)
			if !ok || v.Uid != 0 || st.Mode().Perm()&0022 != 0 {
				f.Close()
				return errors.New("runtime path is not root-owned and protected")
			}
		}
		result.files = append(result.files, f)
		flag := "--ro-bind-fd"
		if writable {
			flag = "--bind-fd"
		}
		args = append(args, flag, strconv.Itoa(2+len(result.files)), dest)
		return nil
	}
	if err = pin(root, root, true); err != nil {
		return nil, err
	}
	if err = pin(home, home, true); err != nil {
		return nil, err
	}
	// Fixed system command allowlist; never inspect workspace-controlled executables
	// with ldd. No PATH, loader, Python, proxy, token or shell-hook env is inherited.
	commands := []string{"sh", "bash", "python3", "sqlite3", "cat", "ls", "find", "grep", "sed", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "env", "mkdir", "rmdir", "rm", "cp", "mv", "ln", "touch", "sleep", "timeout", "patch", "diff", "true", "false"}
	runtimeFiles := map[string]bool{}
	var addDependencies func(string) error
	addDependencies = func(path string) error {
		if runtimeFiles[path] {
			return nil
		}
		runtimeFiles[path] = true
		canonical, e := filepath.EvalSymlinks(path)
		if e != nil {
			return e
		}
		if !strings.HasPrefix(canonical, "/usr/") && !strings.HasPrefix(canonical, "/lib/") && !strings.HasPrefix(canonical, "/lib64/") {
			return errors.New("unsupported system runtime location")
		}
		if e = pin(canonical, path, false); e != nil {
			return e
		}
		probe := exec.CommandContext(ctx, "/usr/bin/ldd", canonical)
		probe.Env = []string{"PATH=/usr/bin:/bin", "LC_ALL=C"}
		out, e := probe.CombinedOutput()
		if e != nil {
			return fmt.Errorf("cannot resolve runtime libraries for %s", path)
		}
		for _, line := range strings.Split(string(out), "\n") {
			for _, field := range strings.Fields(line) {
				if strings.HasPrefix(field, "/") {
					if e = addDependencies(field); e != nil {
						return e
					}
				}
			}
		}
		return nil
	}
	for _, name := range commands {
		if err = addDependencies("/usr/bin/" + name); err != nil {
			return nil, err
		}
	}
	// Isolated system Python supplies only its standard library, including sqlite3.
	py := exec.CommandContext(ctx, "/usr/bin/python3", "-I", "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))")
	py.Env = []string{"PATH=/usr/bin:/bin", "LC_ALL=C"}
	out, e := py.Output()
	if e != nil {
		return nil, errors.New("system Python standard library unavailable")
	}
	stdlib := strings.TrimSpace(string(out))
	if !strings.HasPrefix(stdlib, "/usr/lib/python3.") || strings.ContainsAny(stdlib, "\n\r") {
		return nil, errors.New("unsupported Python standard library")
	}
	if err = pin(stdlib, stdlib, false); err != nil {
		return nil, err
	}
	extensions, e := filepath.Glob(filepath.Join(stdlib, "lib-dynload", "*.so"))
	if e != nil {
		return nil, e
	}
	for _, path := range extensions { // Directory already mounted: collect dependent shared libs only.
		probe := exec.CommandContext(ctx, "/usr/bin/ldd", path)
		probe.Env = []string{"PATH=/usr/bin:/bin", "LC_ALL=C"}
		data, e := probe.CombinedOutput()
		if e != nil {
			return nil, errors.New("Python extension dependency unavailable")
		}
		for _, line := range strings.Split(string(data), "\n") {
			for _, field := range strings.Fields(line) {
				if strings.HasPrefix(field, "/") {
					if err = addDependencies(field); err != nil {
						return nil, err
					}
				}
			}
		}
	}
	args = append(args, "--symlink", "usr/bin", "/bin", "--remount-ro", "/", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", home, "--setenv", "TMPDIR", filepath.Join(home, "tmp"), "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONNOUSERSITE", "1", "--", "/bin/bash", "--noprofile", "--norc", "-c", command)
	cmd := exec.CommandContext(ctx, "/usr/bin/bwrap", args...)
	cmd.Dir = root
	cmd.ExtraFiles = result.files
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LC_ALL=C"}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	cmd.WaitDelay = 2 * time.Second
	result.Cmd = cmd
	return result, nil
}

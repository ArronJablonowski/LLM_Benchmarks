// Command darwinrouter_coding_host runs one isolated coding benchmark through
// DarwinRouter's SDK tool loop. All filesystem tools are rooted in workspace.
package main

import (
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
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ArronJablonowski/DarwinRouter/runtime"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"github.com/ArronJablonowski/DarwinRouter/tools"
)

const outputLimit = 64 << 10

func main() {
	config := flag.String("config", "", "DarwinRouter configuration")
	database := flag.String("database", "", "campaign telemetry database")
	workspace := flag.String("workspace", "", "absolute isolated workspace")
	model := flag.String("model", "auto", "DarwinRouter model ID")
	prompt := flag.String("prompt", "", "coding task")
	timeout := flag.Duration("timeout", 2*time.Hour, "task deadline")
	maxTurns := flag.Int("max-turns", 96, "maximum coding-agent turns")
	finalize := flag.Bool("finalize", false, "record a short completed-task result without coding tools")
	flag.Parse()
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
	// Each benchmark task owns a distinct workspace. Keep its durable tool
	// leases separate so an interrupted task cannot block unrelated fixtures.
	workspaceDigest := sha256.Sum256([]byte(root))
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
	run := sdk.Tool{Tool: providers.Tool{Name: "benchmark_run_command", Description: "Run a shell command inside the isolated coding workspace. Optional path or workdir fields do not change that workspace. Output is capped at 64 KiB.", Parameters: json.RawMessage(`{"type":"object","properties":{"command":{"type":"string","minLength":1,"maxLength":8192},"path":{"type":"string","maxLength":4096},"workdir":{"type":"string","maxLength":4096}},"required":["command"],"additionalProperties":false}`)}, Scope: toolScope, Behavior: tools.BehaviorIdempotentWrite, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Command string `json:"command"`
		}
		if json.Unmarshal(raw, &in) != nil || strings.ContainsRune(in.Command, '\x00') {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid command")
		}
		bounded, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		toolHome := filepath.Join(filepath.Dir(root), ".benchmark-home", filepath.Base(root))
		cmd, err := codingCommand(bounded, root, toolHome, in.Command)
		if err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, err
		}
		body, err := cmd.CombinedOutput()
		if len(body) > outputLimit {
			body = body[len(body)-outputLimit:]
		}
		result := runtime.ToolResult{Content: string(body), Effect: runtime.ConfirmedEffect}
		if err != nil {
			// A non-zero command is observation for an agentic coding loop, not a
			// host-tool failure. Let the model inspect it and repair the code.
			result.Content += fmt.Sprintf("\ncommand exited non-zero: %v", err)
		}
		return result, nil
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
	client, err := sdk.New(sdk.ConfigOptions{ProjectFile: *config, Overrides: map[string]string{"telemetry.database": *database, "runtime.max_turns": turnLimit, "tools.max_turns": turnLimit, "tools.read_root": root}, LookupSecret: os.Getenv, Tools: definitions, ToolPolicy: policy, ApprovalReviewer: reviewer})
	if err != nil {
		fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	suffix := "\n\nInspect the repository, implement the requested change, and run focused tests. Work only inside the supplied workspace. If implementation files are absent, create them from the specification; do not search other workspaces for a solution. Invoke the provided tools through native function calls; writing tool-call markup in your answer does not execute a tool."
	if *finalize {
		suffix = "\n\nThe external objective grader has finished evaluating the workspace. Respond briefly that the evaluation handoff is complete; do not request or invoke tools."
	}
	request := sdk.Request{Version: 1, ModelID: *model, Messages: []providers.Message{
		{Role: "system", Content: "You are an autonomous coding agent. Complete the requested implementation by editing files in the supplied workspace, then run focused tests and fix errors. The workspace may contain only a specification and scaffolding; missing implementation files are your responsibility to create. Inspect the workspace briefly, then implement. Do not search system directories or other workspaces for pre-existing solutions. Use native tool calls, not printed tool markup. Do not finish with a plan or claim changes you did not make."},
		{Role: "user", Content: *prompt + suffix},
	}, Domain: "code", Profile: "benchmark", LocalRequired: true}
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

// codingCommand enforces the same workspace boundary for shell subprocesses
// as the file tools. This macOS host deliberately has no unsandboxed fallback.
func codingCommand(ctx context.Context, root, home, command string) (*exec.Cmd, error) {
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
	for _, path := range []string{root, home, "/dev/null"} {
		profile += "(allow file-write* (subpath " + strconv.Quote(path) + "))"
	}
	cmd := exec.CommandContext(ctx, "/usr/bin/sandbox-exec", "-p", profile, "/bin/zsh", "-f", "-c", command)
	cmd.Dir = root
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	cmd.WaitDelay = 2 * time.Second
	// Preserve tool discovery, not ambient credentials or shell startup hooks.
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + home, "TMPDIR=" + filepath.Join(home, "tmp"), "LANG=en_US.UTF-8", "PYTHONDONTWRITEBYTECODE=1"}
	return cmd, nil
}

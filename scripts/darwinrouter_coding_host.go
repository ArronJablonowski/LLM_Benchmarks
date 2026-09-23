// Command darwinrouter_coding_host runs one isolated coding benchmark through
// DarwinRouter's SDK tool loop. All filesystem tools are rooted in workspace.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
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
	finalize := flag.Bool("finalize", false, "record a short completed-task result without coding tools")
	flag.Parse()
	if *config == "" || *database == "" || *workspace == "" || *prompt == "" || !filepath.IsAbs(*workspace) || !filepath.IsAbs(*database) {
		fatal(errors.New("config, database, absolute workspace, and prompt are required"))
	}
	root, err := filepath.EvalSymlinks(*workspace)
	if err != nil {
		fatal(err)
	}
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
		return path, nil
	}
	list := sdk.Tool{Tool: providers.Tool{Name: "benchmark_list_files", Description: "List regular files recursively. Optionally start at a path inside the coding workspace.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096}},"additionalProperties":false}`)}, Scope: "workspace", ReadOnly: true, Behavior: tools.BehaviorReadOnly, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path string `json:"path"`
		}
		if err := json.Unmarshal(raw, &in); err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		start := root
		if in.Path != "" {
			var err error
			start, err = resolve(in.Path)
			if err != nil {
				return runtime.ToolResult{Content: "invalid workspace path: " + err.Error(), Effect: runtime.NoEffect}, nil
			}
		}
		var names []string
		truncated := false
		err := filepath.WalkDir(start, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if entry.IsDir() && (entry.Name() == ".git" || entry.Name() == "node_modules" || entry.Name() == "__pycache__" || entry.Name() == ".venv" || entry.Name() == ".cache") && path != root {
				return filepath.SkipDir
			}
			if !entry.Type().IsRegular() {
				return nil
			}
			rel, _ := filepath.Rel(root, path)
			names = append(names, rel)
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
	read := sdk.Tool{Tool: providers.Tool{Name: "benchmark_read_file", Description: "Read one UTF-8 file relative to the coding workspace, up to 64 KiB.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096}},"required":["path"],"additionalProperties":false}`)}, Scope: "workspace", ReadOnly: true, Behavior: tools.BehaviorReadOnly, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path string `json:"path"`
		}
		if json.Unmarshal(raw, &in) != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		path, err := resolve(in.Path)
		if err != nil {
			return runtime.ToolResult{Content: "invalid workspace path: " + err.Error(), Effect: runtime.NoEffect}, nil
		}
		if entries, dirErr := os.ReadDir(path); dirErr == nil {
			names := make([]string, 0, len(entries))
			for _, entry := range entries {
				names = append(names, entry.Name())
			}
			sort.Strings(names)
			return runtime.ToolResult{Content: strings.Join(names, "\n"), Effect: runtime.NoEffect}, nil
		}
		body, err := os.ReadFile(path)
		if err != nil || len(body) > outputLimit {
			return runtime.ToolResult{Content: "file unavailable or too large", Effect: runtime.NoEffect}, nil
		}
		if ctx.Err() != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, ctx.Err()
		}
		return runtime.ToolResult{Content: string(body), Effect: runtime.NoEffect}, nil
	}}
	write := sdk.Tool{Tool: providers.Tool{Name: "benchmark_write_file", Description: "Create or replace one UTF-8 file relative to the coding workspace.", Parameters: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string","minLength":1,"maxLength":4096},"content":{"type":"string","maxLength":131072}},"required":["path","content"],"additionalProperties":false}`)}, Scope: "workspace", Behavior: tools.BehaviorIdempotentWrite, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		if json.Unmarshal(raw, &in) != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid arguments")
		}
		path, err := resolve(in.Path)
		if err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, err
		}
		if ctx.Err() != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, ctx.Err()
		}
		if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return runtime.ToolResult{Effect: runtime.UncertainEffect}, err
		}
		if err = os.WriteFile(path, []byte(in.Content), 0600); err != nil {
			return runtime.ToolResult{Effect: runtime.UncertainEffect}, err
		}
		return runtime.ToolResult{Content: "file written", Effect: runtime.ConfirmedEffect}, nil
	}}
	run := sdk.Tool{Tool: providers.Tool{Name: "benchmark_run_command", Description: "Run a shell command inside the isolated coding workspace. Use this to inspect files and execute tests; output is capped at 64 KiB.", Parameters: json.RawMessage(`{"type":"object","properties":{"command":{"type":"string","minLength":1,"maxLength":8192}},"required":["command"],"additionalProperties":false}`)}, Scope: "workspace", Behavior: tools.BehaviorIdempotentWrite, Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
		var in struct {
			Command string `json:"command"`
		}
		if json.Unmarshal(raw, &in) != nil || strings.ContainsRune(in.Command, '\x00') {
			return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid command")
		}
		bounded, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		cmd := exec.CommandContext(bounded, "/bin/zsh", "-lc", in.Command)
		cmd.Dir = root
		toolHome := filepath.Join(filepath.Dir(root), ".benchmark-home", filepath.Base(root))
		if err := os.MkdirAll(toolHome, 0700); err != nil {
			return runtime.ToolResult{Effect: runtime.NoEffect}, err
		}
		cmd.Env = append(os.Environ(), "HOME="+toolHome, "PYTHONDONTWRITEBYTECODE=1")
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
	client, err := sdk.New(sdk.ConfigOptions{ProjectFile: *config, Overrides: map[string]string{"telemetry.database": *database, "runtime.max_turns": "32", "tools.max_turns": "32"}, LookupSecret: os.Getenv, Tools: definitions, ToolPolicy: policy, ApprovalReviewer: reviewer})
	if err != nil {
		fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	suffix := "\n\nInspect the repository, implement the requested change, and run focused tests. Work only inside the supplied workspace."
	if *finalize {
		suffix = "\n\nThe external objective grader has finished evaluating the workspace. Respond briefly that the evaluation handoff is complete; do not request or invoke tools."
	}
	result, err := client.Run(ctx, sdk.Request{Version: 1, ModelID: *model, Prompt: *prompt + suffix, Domain: "code", Profile: "benchmark", LocalRequired: true})
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

func fatal(err error) {
	body, _ := json.Marshal(map[string]string{"error": err.Error()})
	fmt.Fprintln(os.Stderr, string(body))
	os.Exit(1)
}

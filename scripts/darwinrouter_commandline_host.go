// Command darwinrouter_commandline_host runs one isolated CLI benchmark through
// DarwinRouter's SDK tool loop. It exposes only the deterministic lab simulator
// and the benchmark's answer artifact; it never invokes a host shell command.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ArronJablonowski/DarwinRouter/runtime"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"github.com/ArronJablonowski/DarwinRouter/tools"
)

type scenario struct {
	Context  string            `json:"context"`
	Commands map[string]string `json:"commands"`
	Menus    map[string]string `json:"menus"`
}

type transcriptEvent struct {
	Time   string `json:"time"`
	Kind   string `json:"kind"`
	Value  string `json:"value"`
	OK     bool   `json:"ok"`
	Output string `json:"output"`
}

type answer struct {
	Commands []string `json:"commands"`
	Findings []string `json:"findings"`
	Actions  []string `json:"actions"`
	MenuPath []string `json:"menu_path"`
}

func main() {
	configPath := flag.String("config", "", "DarwinRouter project configuration")
	database := flag.String("database", "", "shared DarwinRouter learning database")
	workspace := flag.String("workspace", "", "isolated command-line benchmark workspace")
	model := flag.String("model", "local-worker", "configured local DarwinRouter model ID")
	prompt := flag.String("prompt", "", "benchmark prompt")
	timeout := flag.Duration("timeout", 15*time.Minute, "task deadline")
	flag.Parse()
	if *configPath == "" || *workspace == "" || *prompt == "" || !filepath.IsAbs(*workspace) {
		fatal(errors.New("config, absolute workspace, and prompt are required"))
	}
	root, err := filepath.EvalSymlinks(*workspace)
	if err != nil {
		fatal(err)
	}
	body, err := os.ReadFile(filepath.Join(root, "scenario.json"))
	if err != nil {
		fatal(err)
	}
	var lab scenario
	if json.Unmarshal(body, &lab) != nil || len(lab.Commands)+len(lab.Menus) == 0 {
		fatal(errors.New("invalid scenario"))
	}
	dbPath := *database
	if dbPath == "" {
		dbPath = filepath.Join(root, "darwinrouter.db")
	}
	client, err := sdk.New(sdk.ConfigOptions{
		ProjectFile: *configPath,
		Overrides: map[string]string{
			"telemetry.database": dbPath,
			"runtime.max_turns":  "32",
			"tools.max_turns":    "32",
		},
		LookupSecret: os.Getenv,
		Tools:        benchmarkTools(root, lab),
		ToolPolicy:   &sdk.ToolPolicy{Default: tools.Ask},
		ApprovalReviewer: func(_ context.Context, review sdk.ApprovalPrompt) (string, bool, error) {
			allowed := review.Request.ToolName == "read_benchmark_instructions" || review.Request.ToolName == "run_terminal_lab" || review.Request.ToolName == "save_benchmark_answer"
			return "benchmark-suite-operator", allowed, nil
		},
	})
	if err != nil {
		fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	result, err := client.Run(ctx, sdk.Request{Version: 1, ModelID: *model, Prompt: *prompt, Domain: "commandline", Profile: "benchmark", Capabilities: []string{"tools"}, LocalRequired: true})
	if err != nil {
		encoded, _ := json.Marshal(result)
		fmt.Println(string(encoded))
		fatal(err)
	}
	encoded, _ := json.Marshal(result)
	fmt.Println(string(encoded))
}

func benchmarkTools(root string, lab scenario) []sdk.Tool {
	executedCommands := make([]string, 0, 16)
	executedMenus := make([]string, 0, 16)
	readTool := sdk.Tool{
		Tool:  providers.Tool{Name: "read_benchmark_instructions", Description: "Read the fixed offline command-line lab instructions before choosing simulator actions.", Parameters: json.RawMessage(`{"type":"object","additionalProperties":false}`)},
		Scope: "workspace", ReadOnly: true, Behavior: tools.BehaviorReadOnly,
		Handler: func(ctx context.Context, _ json.RawMessage) (runtime.ToolResult, error) {
			if ctx.Err() != nil {
				return runtime.ToolResult{Effect: runtime.NoEffect}, ctx.Err()
			}
			instructions, err := os.ReadFile(filepath.Join(root, "README.md"))
			if err != nil || len(instructions) > 16<<10 {
				return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("benchmark instructions unavailable")
			}
			toolNote := "\nDarwinRouter tool mapping: call run_terminal_lab with kind=run and only the inner command (for example, uname -a), kind=menu and only the inner menu path, or kind=context. For simulator help, call kind=help with value empty. This is a finite simulation: unsupported commands will not become available by retrying variants. Once the requested findings are supported by observations, save the answer instead of continuing unrelated probes. Do not include the python3 terminal_lab.py wrapper. Finish by calling save_benchmark_answer.\n"
			return runtime.ToolResult{Content: string(instructions) + toolNote, Effect: runtime.NoEffect}, nil
		},
	}
	runTool := sdk.Tool{
		Tool:  providers.Tool{Name: "run_terminal_lab", Description: "Run one exact command or menu path in the deterministic offline terminal simulator. Use kind run or command for shell commands. Use kind=help with an empty value for available operations, or kind=context for the scenario. Unsupported commands are not transient failures. Nothing is executed on the benchmark host.", Parameters: json.RawMessage(`{"type":"object","properties":{"kind":{"type":"string","enum":["run","command","shell","menu","context","help"]},"value":{"type":"string","maxLength":4096}},"required":["kind","value"],"additionalProperties":false}`)},
		Scope: "workspace", Behavior: tools.BehaviorIdempotentWrite,
		Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
			var input struct{ Kind, Value string }
			if json.Unmarshal(raw, &input) != nil || ctx.Err() != nil {
				return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid simulator request")
			}
			input.Value = strings.TrimSpace(input.Value)
			if input.Kind == "command" || input.Kind == "shell" {
				input.Kind = "run"
			}
			// The fixture README documents the standalone terminal_lab.py syntax.
			// Accept that syntax too, so an agent following the supplied instructions
			// reaches the same deterministic simulator rather than the host shell.
			for prefix, kind := range map[string]string{
				"python3 terminal_lab.py run ":  "run",
				"python3 terminal_lab.py menu ": "menu",
			} {
				if strings.HasPrefix(input.Value, prefix) {
					input.Kind = kind
					input.Value = strings.Trim(strings.TrimSpace(strings.TrimPrefix(input.Value, prefix)), "'\"")
				}
			}
			if input.Kind == "help" || input.Value == "python3 terminal_lab.py help" || input.Value == "help" {
				commands := make([]string, 0, len(lab.Commands))
				for command := range lab.Commands {
					commands = append(commands, command)
				}
				sort.Strings(commands)
				menus := make([]string, 0, len(lab.Menus))
				for menu := range lab.Menus {
					menus = append(menus, menu)
				}
				sort.Strings(menus)
				content := "Use kind=run with one available command, kind=menu with one available menu path, or kind=context; then call save_benchmark_answer.\nAvailable commands:\n- " + strings.Join(commands, "\n- ")
				if len(menus) > 0 {
					content += "\nAvailable menu paths:\n- " + strings.Join(menus, "\n- ")
				}
				return runtime.ToolResult{Content: content, Effect: runtime.NoEffect}, nil
			}
			if input.Kind == "run" {
				for available := range lab.Commands {
					if strings.HasPrefix(input.Value, available+" | head") {
						input.Value = available
						break
					}
				}
				if _, exact := lab.Commands[input.Value]; !exact {
					binary := strings.Fields(input.Value)
					matches := make([]string, 0, 1)
					if len(binary) > 0 {
						for available := range lab.Commands {
							fields := strings.Fields(available)
							if len(fields) > 0 && fields[0] == binary[0] {
								matches = append(matches, available)
							}
						}
					}
					if len(matches) == 1 {
						input.Value = matches[0]
					}
				}
			}
			output, ok := "", true
			switch input.Kind {
			case "context":
				output = lab.Context
			case "run":
				output, ok = lab.Commands[input.Value]
			case "menu":
				output, ok = lab.Menus[input.Value]
			default:
				ok = false
			}
			if !ok {
				output = "SIMULATED ERROR: command or menu path is not available in this fixed simulation. Use kind=help with value empty to inspect available operations. Do not keep trying unsupported variants; save_benchmark_answer when the requested findings are supported by observations."
			}
			event, _ := json.Marshal(transcriptEvent{Time: time.Now().UTC().Format(time.RFC3339Nano), Kind: input.Kind, Value: input.Value, OK: ok, Output: output})
			file, openErr := os.OpenFile(filepath.Join(root, "transcript.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
			if openErr != nil {
				return runtime.ToolResult{Effect: runtime.UncertainEffect}, openErr
			}
			_, writeErr := file.Write(append(event, '\n'))
			closeErr := file.Close()
			if writeErr != nil || closeErr != nil {
				return runtime.ToolResult{Effect: runtime.UncertainEffect}, errors.Join(writeErr, closeErr)
			}
			if !ok {
				return runtime.ToolResult{Content: output, Failed: true, Recoverable: true, Effect: runtime.NoEffect}, nil
			}
			if input.Kind == "run" {
				executedCommands = append(executedCommands, input.Value)
			} else if input.Kind == "menu" {
				executedMenus = append(executedMenus, strings.Split(input.Value, ">")...)
			}
			return runtime.ToolResult{Content: output, Effect: runtime.ConfirmedEffect}, nil
		},
	}
	saveTool := sdk.Tool{
		Tool:  providers.Tool{Name: "save_benchmark_answer", Description: "Save the final evidence-backed answer.json in the isolated benchmark workspace. Include commands, findings, actions, and menu_path; omitted arrays are saved empty and may fail grading.", Parameters: json.RawMessage(`{"type":"object","properties":{"commands":{"type":"array","items":{"type":"string"},"maxItems":64},"findings":{"type":"array","items":{"type":"string"},"maxItems":64},"actions":{"type":"array","items":{"type":"string"},"maxItems":64},"menu_path":{"type":"array","items":{"type":"string"},"maxItems":64}},"additionalProperties":false}`)},
		Scope: "workspace", Behavior: tools.BehaviorNonIdempotentWrite,
		Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
			var value answer
			if json.Unmarshal(raw, &value) != nil || ctx.Err() != nil {
				return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid answer")
			}
			// Persist the canonical operations the simulator actually executed. A
			// model may describe an equivalent shell alias in its answer, while the
			// benchmark grades the exact audited simulator operations.
			value.Commands = append([]string{}, executedCommands...)
			value.MenuPath = append([]string{}, executedMenus...)
			if value.Findings == nil {
				value.Findings = []string{}
			}
			if value.Actions == nil {
				value.Actions = []string{}
			}
			encoded, err := json.MarshalIndent(value, "", "  ")
			if err != nil || len(encoded) > 64<<10 {
				return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("answer exceeds bounds")
			}
			if err = os.WriteFile(filepath.Join(root, "answer.json"), append(encoded, '\n'), 0600); err != nil {
				return runtime.ToolResult{Effect: runtime.UncertainEffect}, err
			}
			return runtime.ToolResult{Content: "answer.json saved", Effect: runtime.ConfirmedEffect}, nil
		},
	}
	return []sdk.Tool{readTool, runTool, saveTool}
}

func fatal(err error) {
	encoded, _ := json.Marshal(map[string]string{"error": err.Error()})
	fmt.Fprintln(os.Stderr, string(encoded))
	os.Exit(1)
}

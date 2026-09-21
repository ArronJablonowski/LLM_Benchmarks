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
			return runtime.ToolResult{Content: string(instructions), Effect: runtime.NoEffect}, nil
		},
	}
	runTool := sdk.Tool{
		Tool:  providers.Tool{Name: "run_terminal_lab", Description: "Run one exact command or menu path in the deterministic offline terminal simulator. Nothing is executed on the benchmark host.", Parameters: json.RawMessage(`{"type":"object","properties":{"kind":{"type":"string","enum":["run","menu","context"]},"value":{"type":"string","maxLength":4096}},"required":["kind","value"],"additionalProperties":false}`)},
		Scope: "workspace", Behavior: tools.BehaviorIdempotentWrite,
		Handler: func(ctx context.Context, raw json.RawMessage) (runtime.ToolResult, error) {
			var input struct{ Kind, Value string }
			if json.Unmarshal(raw, &input) != nil || ctx.Err() != nil {
				return runtime.ToolResult{Effect: runtime.NoEffect}, errors.New("invalid simulator request")
			}
			input.Value = strings.TrimSpace(input.Value)
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
				output = "SIMULATED ERROR: command or menu path is not available on this target"
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
	client, err := sdk.New(sdk.ConfigOptions{
		ProjectFile:  *configPath,
		Overrides:    map[string]string{"telemetry.database": filepath.Join(root, "darwinrouter.db")},
		LookupSecret: os.Getenv,
		Tools:        []sdk.Tool{readTool, runTool, saveTool},
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
	result, err := client.Run(ctx, sdk.Request{Version: 1, ModelID: *model, Prompt: *prompt, Domain: "commandline", Profile: "benchmark", LocalRequired: true})
	if err != nil {
		fatal(err)
	}
	encoded, _ := json.Marshal(result)
	fmt.Println(string(encoded))
}

func fatal(err error) {
	encoded, _ := json.Marshal(map[string]string{"error": err.Error()})
	fmt.Fprintln(os.Stderr, string(encoded))
	os.Exit(1)
}

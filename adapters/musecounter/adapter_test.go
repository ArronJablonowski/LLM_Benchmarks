package musecounter

import (
	"context"
	"encoding/json"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ollama/ollama/api"
	"os"
	"testing"
)

func TestAdapterTranscriptParity(t *testing.T) {
	fixture := os.Getenv("MUSE_TRANSCRIPT_FIXTURE")
	if fixture == "" {
		t.Skip("private transcript fixture not configured")
	}
	root := os.Getenv("MUSE_BLOB_PREFIX")
	if root == "" {
		t.Fatal("MUSE_BLOB_PREFIX is required")
	}
	b, e := os.ReadFile(root + tokenizerDigest)
	if e != nil {
		t.Fatal(e)
	}
	cfg, e := os.ReadFile(root + tokenizerConfigDigest)
	if e != nil {
		t.Fatal(e)
	}
	counter, e := museCounter(b, cfg)
	if e != nil {
		t.Fatal(e)
	}
	var rows []struct {
		Observed *int          `json:"observed"`
		Messages []api.Message `json:"messages"`
		Tools    []api.Tool    `json:"tools"`
	}
	b, e = os.ReadFile(fixture)
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(b, &rows); e != nil {
		t.Fatal(e)
	}
	matched := 0
	for rowIndex, row := range rows {
		r := providers.Request{Model: "muse-glimmer:30b-mlx"}
		lastID := map[string][]string{}
		for _, m := range row.Messages {
			dm := providers.Message{Role: m.Role, Content: m.Content}
			if m.Role == "tool" {
				dm.ToolCallID = lastID[m.ToolName][0]
				lastID[m.ToolName] = lastID[m.ToolName][1:]
			}
			for _, c := range m.ToolCalls {
				args, _ := json.Marshal(c.Function.Arguments)
				id := c.ID
				if id == "" {
					id = c.Function.Name
				}
				lastID[c.Function.Name] = append(lastID[c.Function.Name], id)
				dm.ToolCalls = append(dm.ToolCalls, providers.ToolCall{ID: id, Name: c.Function.Name, Arguments: args})
			}
			r.Messages = append(r.Messages, dm)
		}
		for _, tool := range row.Tools {
			p, _ := json.Marshal(tool.Function.Parameters)
			r.Tools = append(r.Tools, providers.Tool{Name: tool.Function.Name, Description: tool.Function.Description, Parameters: p})
		}
		n, ok, e := counter(context.Background(), r)
		if e != nil || !ok {
			t.Fatalf("unsupported transcript %d: %v validation %v", rowIndex, e, providers.ValidateMessages(r.Messages))
		}
		if row.Observed != nil {
			if n != *row.Observed {
				t.Fatalf("got %d want %d", n, *row.Observed)
			}
			matched++
		} else if n != 7517 {
			t.Fatalf("next request %d", n)
		}
		r.JSONSchema = json.RawMessage(`{"type":"object"}`)
		if _, ok, e := counter(context.Background(), r); e != nil || ok {
			t.Fatal("schema did not fall back")
		}
	}
	if matched != 12 {
		t.Fatal(matched)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, e := counter(ctx, providers.Request{}); e == nil {
		t.Fatal("cancellation ignored")
	}
	if _, e := museCounter([]byte("wrong"), cfg); e == nil {
		t.Fatal("wrong asset accepted")
	}
}
func TestMappingRejectsUnsupported(t *testing.T) {
	for _, r := range []providers.Request{{Model: "other"}, {Model: "muse-glimmer:30b-mlx", Messages: []providers.Message{{Role: "tool", ToolCallID: "unknown"}}}, {Model: "muse-glimmer:30b-mlx", Messages: []providers.Message{{Role: "user", Content: "x"}}, Tools: []providers.Tool{{Name: "broken", Parameters: json.RawMessage(`[]`)}}}} {
		if _, _, ok := museWire(r); ok {
			t.Fatal("unsupported accepted")
		}
	}
}

func TestFailedToolMapping(t *testing.T) {
	r := providers.Request{Model: "muse-glimmer:30b-mlx", Messages: []providers.Message{{Role: "user", Content: "test"}, {Role: "assistant", ToolCalls: []providers.ToolCall{{ID: "a", Name: "run", Arguments: json.RawMessage(`{}`)}}}, {Role: "tool", ToolCallID: "a", Content: "denied", ToolFailed: true}}}
	ms, _, ok := museWire(r)
	if !ok || ms[2].ToolName != "run" || ms[2].Content != providers.ToolResultContent(r.Messages[2]) {
		t.Fatal("failed tool mapping differs from provider")
	}
}

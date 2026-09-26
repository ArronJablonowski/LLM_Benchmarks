package musecounter

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ollama/ollama/api"
	"github.com/ollama/ollama/model/renderers"
	"github.com/ollama/ollama/x/tokenizer"
)

const tokenizerDigest = "8a25643f212b3e81a410b8afd0538b02ad30db4b22de863bde2e5006603afa2d"
const tokenizerConfigDigest = "2c49783174e680e3a7f41d40bc7a611affa67cb3e05d41acffb97d44f023283a"

// Prototype only: callers still need to attest the live provider/model/renderer
// identity. Never install this callback merely because the model name matches.
func museCounter(blob, config []byte) (providers.TokenCounter, error) {
	for _, asset := range []struct {
		data []byte
		hash string
	}{{blob, tokenizerDigest}, {config, tokenizerConfigDigest}} {
		sum := sha256.Sum256(asset.data)
		if hex.EncodeToString(sum[:]) != asset.hash {
			return nil, errors.New("tokenizer identity mismatch")
		}
	}
	tok, err := tokenizer.LoadFromBytesWithConfig(blob, &tokenizer.TokenizerConfig{TokenizerConfigJSON: config})
	if err != nil {
		return nil, err
	}
	gate := make(chan struct{}, 1)
	return func(ctx context.Context, r providers.Request) (int, bool, error) {
		if err := ctx.Err(); err != nil {
			return 0, false, err
		}
		messages, tools, ok := museWire(r)
		if !ok {
			return 0, false, nil
		}
		// Tokenizer concurrency guarantees are not assumed by this prototype.
		if err := acquireTokenizer(ctx, gate); err != nil {
			return 0, false, err
		}
		defer func() { <-gate }()
		if err := ctx.Err(); err != nil {
			return 0, false, err
		}
		rendered, err := renderers.RenderWithRenderer("glimmer", messages, tools, nil)
		if err != nil {
			return 0, false, err
		}
		count := len(tok.Encode(rendered, false))
		if err := ctx.Err(); err != nil {
			return 0, false, err
		}
		return count, true, nil
	}, nil
}

// Match Darwin HTTP's Ollama mapping, including failed tool results and call
// names. Structured-output requests remain unsupported until separately proven.
func museWire(r providers.Request) ([]api.Message, []api.Tool, bool) {
	if r.Model != "muse-glimmer:30b-mlx" || len(r.JSONSchema) != 0 || providers.ValidateMessages(r.Messages) != nil {
		return nil, nil, false
	}
	messages := []map[string]any{}
	names := map[string]string{}
	for _, m := range r.Messages {
		if m.Role != "system" && m.Role != "user" && m.Role != "assistant" && m.Role != "tool" {
			return nil, nil, false
		}
		wire := map[string]any{"role": m.Role, "content": providers.ToolResultContent(m)}
		if m.ToolCallID != "" {
			name, ok := names[m.ToolCallID]
			if !ok {
				return nil, nil, false
			}
			wire["tool_name"] = name
		}
		calls := []any{}
		for _, c := range m.ToolCalls {
			if c.ID == "" || c.Name == "" || !object(c.Arguments) {
				return nil, nil, false
			}
			names[c.ID] = c.Name
			calls = append(calls, map[string]any{"id": c.ID, "type": "function", "function": map[string]any{"name": c.Name, "arguments": c.Arguments}})
		}
		if len(calls) > 0 {
			wire["tool_calls"] = calls
		}
		messages = append(messages, wire)
	}
	tools := []any{}
	seen := map[string]bool{}
	for _, t := range r.Tools {
		if t.Name == "" || seen[t.Name] || len(r.Tools) > 128 || !object(t.Parameters) {
			return nil, nil, false
		}
		seen[t.Name] = true
		tools = append(tools, map[string]any{"type": "function", "function": map[string]any{"name": t.Name, "description": t.Description, "parameters": t.Parameters}})
	}
	var ms []api.Message
	var ts []api.Tool
	b, e := json.Marshal(messages)
	if e != nil || json.Unmarshal(b, &ms) != nil {
		return nil, nil, false
	}
	b, e = json.Marshal(tools)
	if e != nil || json.Unmarshal(b, &ts) != nil {
		return nil, nil, false
	}
	return ms, ts, true
}
func object(b []byte) bool {
	var x map[string]json.RawMessage
	return json.Unmarshal(b, &x) == nil && x != nil
}

// acquireTokenizer allows canceled waiters to leave without waiting for another
// request's tokenization to finish. Encoding itself remains non-interruptible.
func acquireTokenizer(ctx context.Context, gate chan struct{}) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case gate <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

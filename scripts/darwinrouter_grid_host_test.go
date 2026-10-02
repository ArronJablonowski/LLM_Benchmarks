package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	"github.com/ArronJablonowski/DarwinRouter/resources"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"go.yaml.in/yaml/v3"
)

type gridProfiler struct{}

func (gridProfiler) Measure(context.Context) (resources.Measurement, error) {
	return resources.Measurement{Version: 1, Snapshot: resources.Snapshot{Time: time.Now().UTC(), CPUs: 2, TotalRAM: 64 << 30, AvailableRAM: 64 << 30, Source: "grid-test"}}, nil
}
func TestSDKGridTextAndAudioRouting(t *testing.T) {
	for _, mode := range []string{"text", "audio"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			t.Setenv("DARWIN_PROCESS_OWNER_DIR", filepath.Join(dir, "owners"))
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/show":
					var b map[string]string
					json.NewDecoder(r.Body).Decode(&b)
					caps := []string{"completion"}
					if b["model"] == "listener" {
						caps = append(caps, "audio")
					}
					json.NewEncoder(w).Encode(map[string]any{"capabilities": caps, "model_info": map[string]int{"test.context_length": 32768}})
				case "/api/tags":
					io.WriteString(w, `{"models":[{"name":"text"},{"name":"listener"}]}`)
				case "/api/ps":
					io.WriteString(w, `{"models":[]}`)
				case "/api/chat":
					calls++
					var b map[string]any
					json.NewDecoder(r.Body).Decode(&b)
					messages := b["messages"].([]any)
					last := messages[len(messages)-1].(map[string]any)
					if mode == "audio" {
						if b["model"] != "listener" || b["think"] != false {
							t.Errorf("wrong audio dispatch: %v", b)
						}
						raw, e := base64.StdEncoding.DecodeString(last["images"].([]any)[0].(string))
						if e != nil || !bytes.Equal(raw, []byte("exact WAV bytes")) {
							t.Fatal("media changed")
						}
					} else if _, ok := last["images"]; ok {
						t.Fatal("media injected into text")
					}
					if b["keep_alive"] != float64(0) {
						t.Fatal("new load not released")
					}
					io.WriteString(w, `{"model":"listener","message":{"role":"assistant","content":"{\"answer\":42}"},"done":true,"done_reason":"stop","prompt_eval_count":100,"eval_count":10}`+"\n")
				default:
					t.Errorf("unexpected endpoint %s", r.URL.Path)
					http.NotFound(w, r)
				}
			}))
			defer server.Close()
			source := []byte("version: 1\nmode: local_only\nworkers:\n  delegate_model: listener\nproviders:\n  - {id: local, kind: ollama, manage_residency: true, endpoint: " + server.URL + "}\nmodels:\n  - {id: text, model: text, provider: local, locality: local, capabilities: [chat, grid-audio], ram_bytes: 1, context_tokens: 32768, estimated_cost: 0}\n  - {id: listener, model: listener, provider: local, locality: local, capabilities: [chat], ram_bytes: 1, context_tokens: 32768, estimated_cost: 0}\n")
			scoped, _, eligible, e := scopedConfig(context.Background(), source, mode)
			if e != nil {
				t.Fatal(e)
			}
			path := filepath.Join(dir, "config.yaml")
			os.WriteFile(path, scoped, 0600)
			f := &gridFactory{payload: []byte("exact WAV bytes"), mode: mode, hash: "hash", marker: "bound", dir: dir, eligible: eligible}
			client, e := sdk.New(sdk.ConfigOptions{ProjectFile: path, ResourceProfiler: gridProfiler{}, ProviderFactory: f, ContextEstimator: gridEstimate{audio: mode == "audio"}, Overrides: map[string]string{"telemetry.database": filepath.Join(dir, "db"), "runtime.max_turns": "1", "tools.max_turns": "2", "tools.enabled": "false", "workers.delegate_read_tools": "false", "workers.delegate_model": "", "memory.enabled": "false", "skills.enabled": "false"}})
			if e != nil {
				t.Fatal(e)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			result, e := client.Run(ctx, sdk.Request{Version: 1, ModelID: "auto", Prompt: "Test bound", Domain: "research", Profile: profile, Capabilities: []string{gate(mode)}, ContextTokens: 32768, LocalRequired: true})
			if e != nil || calls != 1 || result.Text != `{"answer":42}` || result.Turns != 1 || result.TaskID == "" {
				t.Fatalf("SDK result=%+v err=%v calls=%d", result, e, calls)
			}
			history, e := client.ReadEvents(ctx, result.TaskID, 0, 100)
			if e != nil {
				t.Fatal(e)
			}
			b, _ := json.Marshal(history)
			if !bytes.Contains(b, []byte(`"profile":"benchmark-v1"`)) {
				t.Fatal("wrong profile")
			}
		})
	}
}
func TestPrivateCapabilityPolicyPreservesResourcesAndExclusions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, `{"capabilities":["completion"]}`) }))
	defer server.Close()
	source := []byte("version: 1\nproviders:\n  - {id: local, kind: ollama, manage_residency: true, endpoint: " + server.URL + "}\nmodels:\n  - {id: worker, provider: local, model: a, locality: local, capabilities: [chat, grid-audio], ram_bytes: 12345, context_tokens: 32768}\n  - {id: local-glm-ocr, provider: local, model: glm-ocr:latest, locality: local, capabilities: [chat]}\n")
	b, rows, eligible, e := scopedConfig(context.Background(), source, "audio")
	if e != nil || len(eligible) != 0 || rows[1].Eligible {
		t.Fatalf("policy %v %v", rows, e)
	}
	var config map[string]any
	yaml.Unmarshal(b, &config)
	m := config["models"].([]any)[0].(map[string]any)
	if m["ram_bytes"] != 12345 || m["context_tokens"] != 32768 {
		t.Fatal("resources changed")
	}
	for _, cap := range m["capabilities"].([]any) {
		if cap == "grid-audio" {
			t.Fatal("unsupported cap retained")
		}
	}
}
func TestTransportGuardsAndExternalResidency(t *testing.T) {
	for _, mode := range []string{"success", "preexisting", "missing_cap", "unqualified", "wrong_binding", "tool", "context"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/show":
					if mode == "missing_cap" {
						io.WriteString(w, `{"capabilities":["vision"]}`)
					} else {
						io.WriteString(w, `{"capabilities":["completion"]}`)
					}
				case "/api/ps":
					if mode == "preexisting" {
						io.WriteString(w, `{"models":[{"name":"m"}]}`)
					} else {
						io.WriteString(w, `{"models":[]}`)
					}
				case "/api/chat":
					calls++
					var b map[string]any
					json.NewDecoder(r.Body).Decode(&b)
					if mode == "preexisting" {
						if _, ok := b["keep_alive"]; ok {
							t.Fatal("external residency modified")
						}
					}
					io.WriteString(w, `{}`)
				}
			}))
			defer server.Close()
			f := &gridFactory{mode: "text", hash: "h", marker: "binding", dir: t.TempDir(), eligible: map[string]bool{"p/m": mode != "unqualified"}}
			transport := &gridTransport{factory: f, connection: providers.Connection{ID: "p", Endpoint: server.URL, Transport: http.DefaultTransport}}
			prompt := "binding"
			if mode == "wrong_binding" {
				prompt = "wrong"
			}
			role := "user"
			if mode == "tool" {
				role = "tool"
			}
			window := 32768
			if mode == "context" {
				window = 1000
			}
			b, _ := json.Marshal(map[string]any{"model": "m", "messages": []any{map[string]any{"role": role, "content": prompt}}, "options": map[string]any{"num_ctx": window}})
			req, _ := http.NewRequest(http.MethodPost, server.URL+"/api/chat", bytes.NewReader(b))
			res, e := transport.RoundTrip(req)
			if res != nil {
				res.Body.Close()
			}
			success := mode == "success" || mode == "preexisting"
			if success && (e != nil || calls != 1) || !success && (e == nil || calls != 0) {
				t.Fatalf("guard err=%v calls=%d", e, calls)
			}
		})
	}
}
func TestWAVValidationAndProtocolScope(t *testing.T) {
	if validWAV([]byte("not audio")) || supports("audio", []string{"vision", "text_to_speech"}) || supports("image_generation", []string{"vision"}) {
		t.Fatal("wrong capability")
	}
	if !supports("text", []string{"completion"}) || !supports("audio", []string{"audio"}) {
		t.Fatal("supported native input rejected")
	}
	for _, url := range []string{"https://127.0.0.1:11434", "http://example.org", "http://user:secret@127.0.0.1", "http://127.0.0.1/path"} {
		if localEndpoint(url) {
			t.Fatal(url)
		}
	}
	if strings.Contains(gate("audio"), "ocr") {
		t.Fatal("wrong scope")
	}
}

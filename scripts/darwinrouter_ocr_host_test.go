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

type fixtureProfiler struct{}

func (fixtureProfiler) Measure(context.Context) (resources.Measurement, error) {
	return resources.Measurement{Version: 1, Snapshot: resources.Snapshot{Time: time.Now().UTC(), CPUs: 2, TotalRAM: 64 << 30, AvailableRAM: 64 << 30, Source: "ocr-test"}}, nil
}
func TestSDKAutomaticRoutingExcludesTextModelAndDeliversImage(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DARWIN_PROCESS_OWNER_DIR", filepath.Join(dir, "owners"))
	chatCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/show":
			var b map[string]string
			json.NewDecoder(r.Body).Decode(&b)
			if b["model"] == "seeing" {
				io.WriteString(w, `{"capabilities":["vision"],"model_info":{"test.context_length":32768}}`)
			} else {
				io.WriteString(w, `{"capabilities":["completion"],"model_info":{"test.context_length":32768}}`)
			}
		case "/api/tags":
			io.WriteString(w, `{"models":[{"name":"seeing"},{"name":"text"}]}`)
		case "/api/chat":
			chatCalls++
			var b map[string]any
			json.NewDecoder(r.Body).Decode(&b)
			if b["model"] != "seeing" {
				t.Error("text model dispatched")
			}
			ms := b["messages"].([]any)
			last := ms[len(ms)-1].(map[string]any)
			if len(last["images"].([]any)) != 1 {
				t.Error("no image")
			}
			io.WriteString(w, `{"model":"seeing","message":{"role":"assistant","content":"{\"room\":\"205\"}"},"done":true,"done_reason":"stop","prompt_eval_count":100,"eval_count":10}`+"\n")
		default:
			t.Errorf("unexpected endpoint %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	source := []byte("version: 1\nmode: local_only\nworkers:\n  delegate_model: z-image\nproviders:\n  - {id: local, kind: ollama, manage_residency: true, endpoint: " + server.URL + "}\nmodels:\n  - {id: a-text, model: text, provider: local, locality: local, capabilities: [chat, ocr], ram_bytes: 1, context_tokens: 32768, estimated_cost: 0}\n  - {id: z-image, model: seeing, provider: local, locality: local, capabilities: [chat], ram_bytes: 1, context_tokens: 32768, estimated_cost: 0}\n")
	config, _, eligible, e := scopedConfig(context.Background(), source)
	if e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(dir, "config.yaml")
	os.WriteFile(path, config, 0600)
	f := &imageFactory{pixels: []byte("pixels"), hash: hashBytes([]byte("pixels")), marker: "bound", dir: dir, eligible: eligible}
	client, e := sdk.New(sdk.ConfigOptions{ProjectFile: path, ResourceProfiler: fixtureProfiler{}, ProviderFactory: f, ContextEstimator: imageEstimate{}, Overrides: map[string]string{"telemetry.database": filepath.Join(dir, "tasks.db"), "runtime.max_turns": "1", "tools.max_turns": "2", "tools.enabled": "false", "workers.delegate_read_tools": "false", "workers.delegate_model": "", "memory.enabled": "false", "skills.enabled": "false"}})
	if e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	result, e := client.Run(ctx, sdk.Request{Version: 1, ModelID: "auto", Prompt: "Read bound image", Domain: "ocr", Profile: profile, Capabilities: []string{"ocr"}, ContextTokens: 32768, LocalRequired: true})
	if e != nil || result.Text != `{"room":"205"}` || result.TaskID == "" || chatCalls != 1 {
		t.Fatalf("SDK image flow: %+v %v calls=%d", result, e, chatCalls)
	}
	history, e := client.ReadEvents(ctx, result.TaskID, 0, 100)
	if e != nil {
		t.Fatal(e)
	}
	b, _ := json.Marshal(history)
	if !bytes.Contains(b, []byte(`"model_id":"seeing"`)) || !bytes.Contains(b, []byte(`"profile":"ocr-progressive-v1"`)) {
		t.Fatalf("durable identity missing %s", b)
	}
}

func TestCapabilityScopeUsesAdvertisementsAndRetainsReservations(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b map[string]string
		json.NewDecoder(r.Body).Decode(&b)
		if b["model"] == "seeing" {
			io.WriteString(w, `{"capabilities":["completion","vision"]}`)
		} else {
			io.WriteString(w, `{"capabilities":["completion","tools"]}`)
		}
	}))
	defer server.Close()
	source := []byte("version: 1\nproviders:\n  - {id: local, kind: ollama, manage_residency: true, endpoint: " + server.URL + "}\nmodels:\n  - {id: capable, model: seeing, provider: local, locality: local, capabilities: [chat], ram_bytes: 123456, context_tokens: 32768}\n  - {id: liar, model: text, provider: local, locality: local, capabilities: [chat, ocr, vision], ram_bytes: 98765}\n  - {id: local-glm-ocr, model: seeing, provider: local, locality: local, capabilities: [ocr]}\n")
	b, rows, eligible, e := scopedConfig(context.Background(), source)
	if e != nil {
		t.Fatal(e)
	}
	if len(eligible) != 1 || !eligible["local/seeing"] || !rows[0].Eligible || rows[1].Eligible || rows[2].Eligible {
		t.Fatalf("wrong exclusions %+v", rows)
	}
	var c map[string]any
	yaml.Unmarshal(b, &c)
	ms := c["models"].([]any)
	if ms[0].(map[string]any)["ram_bytes"] != 123456 || ms[0].(map[string]any)["context_tokens"] != 32768 {
		t.Fatal("resource policy changed")
	}
	for _, raw := range ms[1:] {
		for _, cap := range raw.(map[string]any)["capabilities"].([]any) {
			if cap == "ocr" {
				t.Fatal("unsupported OCR capability retained")
			}
		}
	}
}

func TestImageAdapterDeliversExactPixelsThroughPolicyTransport(t *testing.T) {
	for _, mode := range []string{"success", "no_advertisement", "disqualified", "wrong_binding", "tool_turn", "wrong_context"} {
		t.Run(mode, func(t *testing.T) {
			pixels := []byte("unique pixel bytes")
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/show":
					if mode == "no_advertisement" {
						io.WriteString(w, `{"capabilities":["completion"]}`)
					} else {
						io.WriteString(w, `{"capabilities":["vision"]}`)
					}
				case "/api/chat":
					calls++
					var body map[string]any
					json.NewDecoder(r.Body).Decode(&body)
					m := body["messages"].([]any)[0].(map[string]any)
					data, e := base64.StdEncoding.DecodeString(m["images"].([]any)[0].(string))
					if e != nil || !bytes.Equal(data, pixels) {
						t.Error("pixels changed")
					}
					if body["options"].(map[string]any)["num_predict"] != float64(outputReserve) {
						t.Error("output bound missing")
					}
					io.WriteString(w, `{"model":"seeing","message":{"role":"assistant","content":"{\"room\":\"205\"}"},"done":true,"done_reason":"stop","prompt_eval_count":100,"eval_count":10}`+"\n")
				default:
					t.Errorf("unexpected endpoint %s", r.URL.Path)
				}
			}))
			defer server.Close()
			dir := t.TempDir()
			f := &imageFactory{pixels: pixels, hash: hashBytes(pixels), marker: "bound-image", dir: dir, eligible: map[string]bool{"local/seeing": mode != "disqualified"}}
			p, e := f.Build(context.Background(), providers.Connection{Version: 1, ID: "local", Kind: "ollama", Endpoint: server.URL, Transport: server.Client().Transport, Purpose: providers.PurposeExecution, Timeout: time.Second})
			if e != nil {
				t.Fatal(e)
			}
			request := providers.Request{Model: "seeing", Messages: []providers.Message{{Role: "user", Content: "question bound-image"}}, ContextTokens: 32768}
			if mode == "wrong_binding" {
				request.Messages[0].Content = "another image"
			}
			if mode == "tool_turn" {
				request.Messages = append(request.Messages, providers.Message{Role: "assistant", Content: "previous"})
			}
			if mode == "wrong_context" {
				request.ContextTokens = 100
			}
			var text string
			e = p.Stream(context.Background(), request, func(c providers.Chunk) error { text += c.Text; return nil })
			if mode == "success" {
				if e != nil || calls != 1 || text != `{"room":"205"}` {
					t.Fatal(e, calls, text)
				}
				audit, _ := os.ReadFile(filepath.Join(dir, "image-delivery.jsonl"))
				if !strings.Contains(string(audit), f.hash) || strings.Contains(string(audit), base64.StdEncoding.EncodeToString(pixels)) {
					t.Fatal("audit missing identity or exposing payload")
				}
			} else if e == nil || calls != 0 {
				t.Fatalf("unsafe dispatch %s: %v %d", mode, e, calls)
			}
		})
	}
}

func TestDiscoveryDoesNotInjectImages(t *testing.T) {
	calls := 0
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/api/tags" {
			t.Error(r.URL.Path)
		}
		io.WriteString(w, `{"models":[{"name":"seeing"}]}`)
	}))
	defer s.Close()
	f := &imageFactory{}
	p, e := f.Build(context.Background(), providers.Connection{ID: "local", Kind: "ollama", Endpoint: s.URL, Transport: s.Client().Transport, Purpose: providers.PurposeDiscovery, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	models, e := p.Models(context.Background())
	if e != nil || len(models) != 1 || calls != 1 {
		t.Fatal(models, e, calls)
	}
}
func TestImageContextReserveAndEndpointRestrictions(t *testing.T) {
	r := providers.Request{Messages: []providers.Message{{Role: "user", Content: "question"}}}
	base, _ := providers.EstimateContext(r)
	n, e := (imageEstimate{}).Estimate(context.Background(), r)
	if e != nil || n != base+imageReserve+outputReserve {
		t.Fatal(n, e)
	}
	for _, endpoint := range []string{"https://example.com", "http://localhost:11434", "http://127.0.0.1:11434/redirect", "http://user:secret@127.0.0.1:11434"} {
		if localEndpoint(endpoint) {
			t.Fatal("unsafe endpoint", endpoint)
		}
	}
}

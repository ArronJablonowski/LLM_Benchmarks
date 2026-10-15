// Isolated routing-grid SDK host. Text or actual WAV input; no tools or oracle access.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"go.yaml.in/yaml/v3"
)

const audioReserve = 16384
const outputReserve = 4096
const profile = "benchmark-v1"

type capability struct {
	ID         string   `json:"id"`
	Provider   string   `json:"provider"`
	Model      string   `json:"model"`
	Advertised []string `json:"advertised"`
	Eligible   bool     `json:"eligible"`
	Reason     string   `json:"reason"`
	CheckedAt  string   `json:"checked_at"`
}
type gridFactory struct {
	payload           []byte
	mode              string
	hash, marker, dir string
	eligible          map[string]bool
	mu                sync.Mutex
}
type gridEstimate struct{ audio bool }

func (g gridEstimate) Estimate(ctx context.Context, r providers.Request) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	n, err := providers.EstimateContext(r)
	if g.audio {
		n += audioReserve
	}
	return n + outputReserve, err
}
func hashBytes(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
func supports(mode string, caps []string) bool {
	for _, c := range caps {
		if mode == "text" && c == "completion" || mode == "audio" && c == "audio" {
			return true
		}
	}
	return false
}
func gate(mode string) string { return "grid-" + mode }
func validWAV(b []byte) bool {
	if len(b) < 44 || len(b) > 4<<20 || string(b[:4]) != "RIFF" || string(b[8:12]) != "WAVE" {
		return false
	}
	validFormat, validData := false, false
	for p := 12; p+8 <= len(b); {
		n := int(binary.LittleEndian.Uint32(b[p+4 : p+8]))
		start := p + 8
		if n < 0 || start+n > len(b) {
			return false
		}
		switch string(b[p : p+4]) {
		case "fmt ":
			if n < 16 {
				return false
			}
			f := b[start : start+n]
			validFormat = binary.LittleEndian.Uint16(f[:2]) == 1 && binary.LittleEndian.Uint16(f[2:4]) == 1 && binary.LittleEndian.Uint32(f[4:8]) == 16000 && binary.LittleEndian.Uint16(f[14:16]) == 16
		case "data":
			validData = n > 0 && n%2 == 0
		}
		p = start + n + n%2
	}
	return validFormat && validData
}
func localEndpoint(endpoint string) bool {
	u, e := url.Parse(endpoint)
	if e != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return false
	}
	ip := net.ParseIP(u.Hostname())
	return ip != nil && ip.IsLoopback()
}
func nativeCapabilities(ctx context.Context, endpoint, model string, transport http.RoundTripper) ([]string, error) {
	if !localEndpoint(endpoint) {
		return nil, errors.New("routing-grid requires an explicit loopback Ollama endpoint")
	}
	b, _ := json.Marshal(map[string]string{"model": model})
	r, e := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(endpoint, "/")+"/api/show", bytes.NewReader(b))
	if e != nil {
		return nil, e
	}
	r.Header.Set("Content-Type", "application/json")
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect forbidden") }}
	response, e := client.Do(r)
	if e != nil {
		return nil, e
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("capability metadata HTTP %d", response.StatusCode)
	}
	body, e := io.ReadAll(io.LimitReader(response.Body, 2<<20))
	if e != nil || len(body) >= 2<<20 {
		return nil, errors.New("invalid capability metadata size")
	}
	var data struct {
		Capabilities []string `json:"capabilities"`
	}
	if json.Unmarshal(body, &data) != nil {
		return nil, errors.New("invalid capability metadata")
	}
	return data.Capabilities, nil
}

func residentNames(ctx context.Context, endpoint string, transport http.RoundTripper) (map[string]bool, error) {
	if !localEndpoint(endpoint) {
		return nil, errors.New("invalid residency endpoint")
	}
	r, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(endpoint, "/")+"/api/ps", nil)
	if err != nil {
		return nil, err
	}
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect forbidden") }}
	response, err := client.Do(r)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, errors.New("residency metadata unavailable")
	}
	b, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil || len(b) >= 1<<20 {
		return nil, errors.New("invalid residency metadata size")
	}
	var data struct {
		Models *[]struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if json.Unmarshal(b, &data) != nil || data.Models == nil {
		return nil, errors.New("invalid residency metadata")
	}
	names := map[string]bool{}
	for _, m := range *data.Models {
		if m.Name == "" {
			return nil, errors.New("missing resident identity")
		}
		names[m.Name] = true
	}
	return names, nil
}

// Only the private per-attempt configuration gains a verified grid capability.
// Every original reservation, context policy, endpoint and routing weight stays.
func scopedConfig(ctx context.Context, source []byte, mode string) ([]byte, []capability, map[string]bool, error) {
	var cfg map[string]any
	if e := yaml.Unmarshal(source, &cfg); e != nil {
		return nil, nil, nil, e
	}
	ps, ok := cfg["providers"].([]any)
	if !ok {
		return nil, nil, nil, errors.New("missing providers")
	}
	providersByID := map[string]map[string]any{}
	for _, p := range ps {
		m := p.(map[string]any)
		// Shared process admission deliberately rejects SDK-managed residency:
		// that in-process controller cannot prove ownership of peer residents.
		// Disable unloading in this private host config, never the coordinator.
		if m["kind"] == "ollama" {
			m["manage_residency"] = false
		}
		providersByID[fmt.Sprint(m["id"])] = m
	}
	ms, ok := cfg["models"].([]any)
	if !ok {
		return nil, nil, nil, errors.New("missing models")
	}
	rows := []capability{}
	eligible := map[string]bool{}
	for _, raw := range ms {
		m := raw.(map[string]any)
		id := fmt.Sprint(m["id"])
		model := fmt.Sprint(m["model"])
		pid := fmt.Sprint(m["provider"])
		row := capability{ID: id, Provider: pid, Model: model, CheckedAt: time.Now().UTC().Format(time.RFC3339Nano)}
		caps := []any{}
		if list, ok := m["capabilities"].([]any); ok {
			for _, c := range list {
				if c != gate(mode) {
					caps = append(caps, c)
				}
			}
		}
		p := providersByID[pid]
		switch {
		case id == "local-glm-ocr" || strings.HasPrefix(model, "glm-ocr:"):
			row.Reason = "operator exclusion retained"
		case m["locality"] != "local" || p["kind"] != "ollama":
			row.Reason = "not a local Ollama grid adapter"
		default:
			advertised, e := nativeCapabilities(ctx, fmt.Sprint(p["endpoint"]), model, http.DefaultTransport)
			row.Advertised = advertised
			if e != nil {
				row.Reason = "capability metadata unavailable"
			} else if !supports(mode, advertised) {
				row.Reason = "provider does not advertise required " + mode + " input capability"
			} else {
				row.Eligible = true
				row.Reason = "native capability verified for grid " + mode
				caps = append(caps, gate(mode))
				eligible[pid+"/"+model] = true
			}
		}
		m["capabilities"] = caps
		rows = append(rows, row)
	}
	b, e := yaml.Marshal(cfg)
	return b, rows, eligible, e
}
func writeJSON(path string, value any) error {
	b, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return e
	}
	_, e = f.Write(append(b, '\n'))
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e != nil {
		return e
	}
	return closeErr
}
func (f *gridFactory) appendAudit(v any) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	out, e := os.OpenFile(filepath.Join(f.dir, "dispatch.jsonl"), os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
	if e != nil {
		return e
	}
	_, e = out.Write(append(b, '\n'))
	if e == nil {
		e = out.Sync()
	}
	ce := out.Close()
	if e != nil {
		return e
	}
	return ce
}
func (f *gridFactory) Build(_ context.Context, c providers.Connection) (providers.Provider, error) {
	if c.Kind != "ollama" || !localEndpoint(c.Endpoint) {
		return nil, errors.New("unsupported routing-grid provider")
	}
	t := c.Transport
	if c.Purpose == providers.PurposeExecution || c.Purpose == "" {
		t = &gridTransport{factory: f, connection: c}
	}
	return providers.NewHTTPWithTimeout(c.Endpoint, c.Kind, c.APIKey, t, c.Timeout)
}

type gridTransport struct {
	factory    *gridFactory
	connection providers.Connection
}

func (t *gridTransport) RoundTrip(r *http.Request) (out *http.Response, err error) {
	f := t.factory
	c := t.connection
	defer func() {
		if err != nil {
			_ = f.appendAudit(map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "stage": "blocked", "reason": err.Error(), "provider": c.ID})
		}
	}()
	if r.URL.Path != "/api/chat" || r.Method != http.MethodPost {
		return c.Transport.RoundTrip(r)
	}
	b, e := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	r.Body.Close()
	if e != nil || len(b) >= 1<<20 {
		return nil, errors.New("invalid routing-grid request size")
	}
	var body map[string]any
	if json.Unmarshal(b, &body) != nil {
		return nil, errors.New("invalid routing-grid body")
	}
	model, _ := body["model"].(string)
	if !f.eligible[c.ID+"/"+model] {
		return nil, errors.New("routing-grid model disqualified before provider dispatch")
	}
	caps, e := nativeCapabilities(r.Context(), c.Endpoint, model, c.Transport)
	if e != nil || !supports(f.mode, caps) {
		return nil, errors.New("routing-grid capability recheck failed; no provider dispatch")
	}
	messages, ok := body["messages"].([]any)
	if !ok || len(messages) == 0 {
		return nil, errors.New("missing routing-grid messages")
	}
	users := 0
	for _, raw := range messages {
		m, ok := raw.(map[string]any)
		if !ok {
			return nil, errors.New("invalid routing-grid message")
		}
		if m["role"] == "user" {
			content, _ := m["content"].(string)
			if !strings.Contains(content, f.marker) {
				return nil, errors.New("request binding mismatch")
			}
			if f.mode == "audio" {
				m["images"] = []string{base64.StdEncoding.EncodeToString(f.payload)}
			} else if _, exists := m["images"]; exists {
				return nil, errors.New("text request must not contain media")
			}
			users++
		} else if m["role"] != "system" {
			return nil, errors.New("routing-grid host forbids continuations/tool turns")
		}
	}
	if users != 1 {
		return nil, errors.New("routing-grid host requires one fresh user message")
	}
	if tools, ok := body["tools"].([]any); ok && len(tools) > 0 {
		return nil, errors.New("routing-grid host forbids tools")
	}
	options, ok := body["options"].(map[string]any)
	if !ok {
		options = map[string]any{}
	}
	if options["num_ctx"] != float64(32768) {
		return nil, errors.New("unexpected routing-grid context window")
	}
	options["num_predict"] = outputReserve
	body["options"] = options
	if f.mode == "audio" {
		body["think"] = false
	}
	// This is a request-scoped keep-alive policy, not a global unload operation.
	// Never shorten the lifetime of a model that was resident before our request.
	resident, e := residentNames(r.Context(), c.Endpoint, c.Transport)
	if e != nil {
		return nil, e
	}
	residencyPolicy := "preserve_existing_residency"
	if !resident[model] {
		body["keep_alive"] = 0
		residencyPolicy = "release_new_load_after_request"
	}
	wire, e := json.Marshal(body)
	if e != nil {
		return nil, e
	}
	row := map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "provider": c.ID, "model": model, "request_sha256": f.hash, "media_sha256": hashBytes(f.payload), "media_bytes": len(f.payload), "mode": f.mode, "wire_sha256": hashBytes(wire), "advertised": caps, "audio_reserve_tokens": audioReserve, "max_output_tokens": outputReserve, "context_tokens": 32768, "stage": "dispatch"}
	row["residency_policy"] = residencyPolicy
	if e = f.appendAudit(row); e != nil {
		return nil, e
	}
	clone := r.Clone(r.Context())
	clone.Body = io.NopCloser(bytes.NewReader(wire))
	clone.ContentLength = int64(len(wire))
	clone.GetBody = nil
	response, e := c.Transport.RoundTrip(clone)
	result := map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "provider": c.ID, "model": model, "request_sha256": f.hash, "stage": "response_headers"}
	if response != nil {
		result["status"] = response.StatusCode
	}
	if e != nil {
		result["transport_error"] = true
	}
	if ae := f.appendAudit(result); ae != nil {
		if response != nil {
			response.Body.Close()
		}
		return nil, ae
	}
	return response, e
}

type gridRequest struct {
	ID        string `json:"id"`
	Model     string `json:"model"`
	Domain    string `json:"domain"`
	Profile   string `json:"profile"`
	Prompt    string `json:"prompt"`
	AudioPath string `json:"audio_path"`
	AudioHash string `json:"audio_sha256"`
}

func main() {
	config := flag.String("config", "", "normal config, read only")
	db := flag.String("database", "", "shared learning database")
	dir := flag.String("attempt-dir", "", "new absolute private directory")
	requestPath := flag.String("request", "", "model-visible request JSON")
	preflight := flag.Bool("preflight", false, "capability audit, no inference")
	flag.Parse()
	fail := func(e error) { fmt.Fprintln(os.Stderr, e); os.Exit(1) }
	if *dir == "" || !filepath.IsAbs(*dir) || *config == "" {
		fail(errors.New("config and absolute attempt-dir required"))
	}
	if e := os.Mkdir(*dir, 0700); e != nil {
		fail(e)
	}
	source, e := os.ReadFile(*config)
	if e != nil {
		fail(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()
	mode := "text"
	var req gridRequest
	var raw, payload []byte
	if !*preflight {
		raw, e = os.ReadFile(*requestPath)
		if e != nil {
			fail(e)
		}
		if len(raw) > 128<<10 || json.Unmarshal(raw, &req) != nil {
			fail(errors.New("invalid grid request"))
		}
		allowed := map[string]bool{"research": true, "data_analysis": true, "reasoning": true, "workflow": true, "translation": true, "writing": true, "creative": true, "audio": true}
		if !allowed[req.Domain] || req.Profile != profile || req.Prompt == "" || req.Model == "" || req.ID == "" || *db == "" {
			fail(errors.New("invalid grid scope"))
		}
		if req.Domain == "audio" {
			mode = "audio"
			payload, e = os.ReadFile(req.AudioPath)
			if e != nil || !validWAV(payload) || hashBytes(payload) != req.AudioHash {
				fail(errors.New("invalid or changed WAV"))
			}
		} else if req.AudioPath != "" || req.AudioHash != "" {
			fail(errors.New("unexpected media for text task"))
		}
	}
	scoped, rows, eligible, e := scopedConfig(ctx, source, mode)
	if e != nil {
		fail(e)
	}
	if e = writeJSON(filepath.Join(*dir, "capabilities.json"), rows); e != nil {
		fail(e)
	}
	if *preflight {
		fmt.Println("capability audit complete; no inference")
		return
	}
	if len(eligible) == 0 {
		fail(errors.New("no eligible models"))
	}
	configPath := filepath.Join(*dir, "scoped-config.yaml")
	if e = os.WriteFile(configPath, scoped, 0600); e != nil {
		fail(e)
	}
	marker := "[routing-grid request sha256=" + hashBytes(raw) + "]"
	factory := &gridFactory{payload: payload, mode: mode, hash: hashBytes(raw), marker: marker, dir: *dir, eligible: eligible}
	client, e := sdk.New(sdk.ConfigOptions{ProjectFile: configPath, LookupSecret: os.Getenv, ProviderFactory: factory, ContextEstimator: gridEstimate{audio: mode == "audio"}, Overrides: map[string]string{
		"telemetry.database": *db, "runtime.max_turns": "1", "tools.max_turns": "2", "tools.enabled": "false", "workers.delegate_read_tools": "false", "workers.delegate_model": "", "memory.enabled": "false", "skills.enabled": "false",
	}, EventSink: sdk.EventSinkFunc(func(_ context.Context, event sdk.Event) error {
		b, err := json.Marshal(event)
		if err != nil {
			return err
		}
		out, err := os.OpenFile(filepath.Join(*dir, "events.jsonl"), os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
		if err != nil {
			return err
		}
		_, err = out.Write(append(b, '\n'))
		if err == nil {
			err = out.Sync()
		}
		ce := out.Close()
		if err != nil {
			return err
		}
		return ce
	})})
	if e != nil {
		fail(e)
	}
	result, runErr := client.Run(ctx, sdk.Request{Version: 1, ModelID: req.Model, Prompt: req.Prompt + "\n\n" + marker, Domain: req.Domain, Profile: req.Profile, Capabilities: []string{gate(mode)}, ContextTokens: 32768, LocalRequired: true})
	output := map[string]any{"result": result, "request_sha256": hashBytes(raw), "config_sha256": hashBytes(source), "scoped_config_sha256": hashBytes(scoped), "finished_at": time.Now().UTC().Format(time.RFC3339Nano)}
	if runErr != nil {
		output["error"] = runErr.Error()
	}
	if e = writeJSON(filepath.Join(*dir, "host-result.json"), output); e != nil {
		fail(e)
	}
	if runErr != nil {
		fail(runErr)
	}
	fmt.Println("grid task completed; durable result saved")
}

// DarwinRouter OCR host: one fresh, image-bound SDK task. The trusted adapter
// adds pixels only to the admitted Ollama execution request, using its supplied
// policy transport. It does not change the daemon's text-only public API.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"image/png"
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

const imageReserve = 16384
const outputReserve = 4096
const profile = "ocr-progressive-v1"

type capability struct {
	ID         string   `json:"id"`
	Provider   string   `json:"provider"`
	Model      string   `json:"model"`
	Advertised []string `json:"advertised"`
	Eligible   bool     `json:"eligible"`
	Reason     string   `json:"reason"`
	CheckedAt  string   `json:"checked_at"`
}
type imageFactory struct {
	pixels            []byte
	hash, marker, dir string
	eligible          map[string]bool
	mu                sync.Mutex
}
type imageEstimate struct{}

func (imageEstimate) Estimate(ctx context.Context, r providers.Request) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	n, err := providers.EstimateContext(r)
	return n + imageReserve + outputReserve, err
}
func hashBytes(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
func imageCapability(caps []string) bool {
	for _, c := range caps {
		if c == "vision" || c == "image" || c == "ocr" {
			return true
		}
	}
	return false
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
		return nil, errors.New("OCR requires an explicit loopback Ollama endpoint")
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

// Only the derived, per-attempt configuration gains normalized OCR capability.
// Every original reservation, context policy, endpoint and routing weight stays.
func scopedConfig(ctx context.Context, source []byte) ([]byte, []capability, map[string]bool, error) {
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
				if c != "ocr" {
					caps = append(caps, c)
				}
			}
		}
		p := providersByID[pid]
		switch {
		case id == "local-glm-ocr" || strings.HasPrefix(model, "glm-ocr:"):
			row.Reason = "operator exclusion retained"
		case m["locality"] != "local" || p["kind"] != "ollama":
			row.Reason = "not a local Ollama image adapter"
		default:
			advertised, e := nativeCapabilities(ctx, fmt.Sprint(p["endpoint"]), model, http.DefaultTransport)
			row.Advertised = advertised
			if e != nil {
				row.Reason = "capability metadata unavailable"
			} else if !imageCapability(advertised) {
				row.Reason = "provider does not advertise OCR/image reading"
			} else {
				row.Eligible = true
				row.Reason = "native image-reading capability normalized to OCR"
				caps = append(caps, "ocr")
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
func (f *imageFactory) appendAudit(v any) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	out, e := os.OpenFile(filepath.Join(f.dir, "image-delivery.jsonl"), os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
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
func (f *imageFactory) Build(_ context.Context, c providers.Connection) (providers.Provider, error) {
	if c.Kind != "ollama" || !localEndpoint(c.Endpoint) {
		return nil, errors.New("unsupported OCR provider")
	}
	t := c.Transport
	if c.Purpose == providers.PurposeExecution || c.Purpose == "" {
		t = &imageTransport{factory: f, connection: c}
	}
	return providers.NewHTTPWithTimeout(c.Endpoint, c.Kind, c.APIKey, t, c.Timeout)
}

type imageTransport struct {
	factory    *imageFactory
	connection providers.Connection
}

func (t *imageTransport) RoundTrip(r *http.Request) (out *http.Response, err error) {
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
		return nil, errors.New("invalid OCR request size")
	}
	var body map[string]any
	if json.Unmarshal(b, &body) != nil {
		return nil, errors.New("invalid OCR body")
	}
	model, _ := body["model"].(string)
	if !f.eligible[c.ID+"/"+model] {
		return nil, errors.New("OCR model disqualified before image dispatch")
	}
	caps, e := nativeCapabilities(r.Context(), c.Endpoint, model, c.Transport)
	if e != nil || !imageCapability(caps) {
		return nil, errors.New("OCR capability recheck failed; no image dispatched")
	}
	messages, ok := body["messages"].([]any)
	if !ok || len(messages) == 0 {
		return nil, errors.New("missing OCR messages")
	}
	users := 0
	for _, raw := range messages {
		m, ok := raw.(map[string]any)
		if !ok {
			return nil, errors.New("invalid OCR message")
		}
		if m["role"] == "user" {
			content, _ := m["content"].(string)
			if !strings.Contains(content, f.marker) {
				return nil, errors.New("image/task binding mismatch")
			}
			m["images"] = []string{base64.StdEncoding.EncodeToString(f.pixels)}
			users++
		} else if m["role"] != "system" {
			return nil, errors.New("OCR host forbids continuations/tool turns")
		}
	}
	if users != 1 {
		return nil, errors.New("OCR host requires one fresh user message")
	}
	if tools, ok := body["tools"].([]any); ok && len(tools) > 0 {
		return nil, errors.New("OCR host forbids tools")
	}
	options, ok := body["options"].(map[string]any)
	if !ok {
		options = map[string]any{}
	}
	if options["num_ctx"] != float64(32768) {
		return nil, errors.New("unexpected OCR context window")
	}
	options["num_predict"] = outputReserve
	body["options"] = options
	wire, e := json.Marshal(body)
	if e != nil {
		return nil, e
	}
	row := map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "provider": c.ID, "model": model, "image_sha256": f.hash, "image_bytes": len(f.pixels), "wire_sha256": hashBytes(wire), "advertised": caps, "image_reserve_tokens": imageReserve, "max_output_tokens": outputReserve, "context_tokens": 32768, "stage": "dispatch"}
	if e = f.appendAudit(row); e != nil {
		return nil, e
	}
	clone := r.Clone(r.Context())
	clone.Body = io.NopCloser(bytes.NewReader(wire))
	clone.ContentLength = int64(len(wire))
	clone.GetBody = nil
	response, e := c.Transport.RoundTrip(clone)
	result := map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "provider": c.ID, "model": model, "image_sha256": f.hash, "stage": "response_headers"}
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
func main() {
	config := flag.String("config", "", "normal config (read only)")
	db := flag.String("database", "", "shared learning database")
	dir := flag.String("attempt-dir", "", "new private attempt directory")
	image := flag.String("image", "", "frozen PNG")
	hash := flag.String("sha256", "", "expected PNG hash")
	prompt := flag.String("prompt", "", "question only")
	preflight := flag.Bool("preflight", false, "capability audit only, no inference")
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
	scoped, rows, eligible, e := scopedConfig(ctx, source)
	if e != nil {
		fail(e)
	}
	if e = writeJSON(filepath.Join(*dir, "capabilities.json"), rows); e != nil {
		fail(e)
	}
	if *preflight {
		fmt.Println("capability preflight complete; no inference")
		return
	}
	if len(eligible) == 0 {
		fail(errors.New("no eligible OCR models"))
	}
	pixels, e := os.ReadFile(*image)
	if e != nil {
		fail(e)
	}
	dimensions, e := png.DecodeConfig(bytes.NewReader(pixels))
	if e != nil || len(pixels) > 4<<20 || dimensions.Width != 1200 || dimensions.Height != 1550 || hashBytes(pixels) != *hash {
		fail(errors.New("invalid or changed OCR image"))
	}
	if *prompt == "" || *db == "" {
		fail(errors.New("prompt and shared database required"))
	}
	configPath := filepath.Join(*dir, "scoped-config.yaml")
	if e = os.WriteFile(configPath, scoped, 0600); e != nil {
		fail(e)
	}
	marker := "[OCR image sha256=" + *hash + "; 1200x1550 PNG]"
	factory := &imageFactory{pixels: pixels, hash: *hash, marker: marker, dir: *dir, eligible: eligible}
	client, e := sdk.New(sdk.ConfigOptions{ProjectFile: configPath, LookupSecret: os.Getenv, ProviderFactory: factory, ContextEstimator: imageEstimate{}, Overrides: map[string]string{
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
	result, runErr := client.Run(ctx, sdk.Request{Version: 1, ModelID: "auto", Prompt: *prompt + "\n\n" + marker, Domain: "ocr", Profile: profile, Capabilities: []string{"ocr"}, ContextTokens: 32768, LocalRequired: true})
	output := map[string]any{"result": result, "image_sha256": *hash, "config_sha256": hashBytes(source), "scoped_config_sha256": hashBytes(scoped), "finished_at": time.Now().UTC().Format(time.RFC3339Nano)}
	if runErr != nil {
		output["error"] = runErr.Error()
	}
	if e = writeJSON(filepath.Join(*dir, "host-result.json"), output); e != nil {
		fail(e)
	}
	b, _ := json.Marshal(output)
	fmt.Println(string(b))
	if runErr != nil {
		fail(runErr)
	}
}

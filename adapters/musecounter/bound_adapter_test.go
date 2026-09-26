package musecounter

import (
	"context"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	"runtime/debug"
	"strings"
	"testing"
)

func TestAttestedConstructorRejectsProviderBeforeCounting(t *testing.T) {
	called := false
	count := func(context.Context, providers.Request) (int, bool, error) { called = true; return 10, true, nil }
	hash := strings.Repeat("a", 64)
	identity := providerIdentity{Endpoint: "http://127.0.0.1:11434", ProcessStart: "fixture", ExecutableSHA256: hash, ManifestSHA256: hash, RendererSHA256: hash}
	build := &debug.BuildInfo{Deps: []*debug.Module{{Path: "github.com/ollama/ollama", Version: "v0.32.15", Sum: "h1:lnCycypBjS9SoMNeM6FivlYeDRn7mP/zfLG0uJXwmZ4="}}}
	probe := func(context.Context) (providerIdentity, error) { return identity, nil }
	if _, err := newAttestedCounter("openai", identity.Endpoint, "muse-glimmer:30b-mlx", hash, build, identity, probe, count); err == nil {
		t.Fatal("accepted wrong provider")
	}
	if called {
		t.Fatal("count invoked for rejected provider")
	}
	if _, err := newAttestedCounter("ollama", identity.Endpoint, "muse-glimmer:30b-mlx", hash, build, identity, probe, count); err != nil {
		t.Fatal(err)
	}
}

func TestAttestedCounterEstimateRestoresFallbackOnIdentityChange(t *testing.T) {
	hash := strings.Repeat("a", 64)
	expected := providerIdentity{Endpoint: "http://127.0.0.1:11434", ProcessStart: "fixture", ExecutableSHA256: hash, ManifestSHA256: hash, RendererSHA256: hash}
	build := &debug.BuildInfo{Deps: []*debug.Module{{Path: "github.com/ollama/ollama", Version: "v0.32.15", Sum: "h1:lnCycypBjS9SoMNeM6FivlYeDRn7mP/zfLG0uJXwmZ4="}}}
	request := providers.Request{Model: "muse-glimmer:30b-mlx", Messages: []providers.Message{{Role: "user", Content: strings.Repeat("x", 10000)}}}
	fallback, err := providers.EstimateContext(request)
	if err != nil {
		t.Fatal(err)
	}
	for _, changed := range []bool{false, true} {
		t.Run(map[bool]string{false: "matched", true: "changed_during_count"}[changed], func(t *testing.T) {
			calls := 0
			probe := func(context.Context) (providerIdentity, error) {
				calls++
				id := expected
				if changed && calls > 1 {
					id.ProcessStart = "restarted"
				}
				return id, nil
			}
			count := func(context.Context, providers.Request) (int, bool, error) { return 100, true, nil }
			counter, err := newAttestedCounter("ollama", expected.Endpoint, request.Model, hash, build, expected, probe, count)
			if err != nil {
				t.Fatal(err)
			}
			got, err := providers.EstimateWith(context.Background(), counter, request)
			if err != nil {
				t.Fatal(err)
			}
			want := 1124
			if changed {
				want = fallback
			}
			if got != want {
				t.Fatalf("estimate=%d want=%d", got, want)
			}
		})
	}
}

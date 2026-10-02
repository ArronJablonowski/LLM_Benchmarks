package musecounter

import (
	"context"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"runtime/debug"
	"strings"
	"testing"
)

func TestEffectiveFactoryComposesCounter(t *testing.T) {
	hash := strings.Repeat("a", 64)
	id := providerIdentity{Endpoint: "http://127.0.0.1:11434", ProcessStart: "fixture", ExecutableSHA256: hash, ManifestSHA256: hash, RendererSHA256: hash}
	build := &debug.BuildInfo{Deps: []*debug.Module{{Path: "github.com/ollama/ollama", Version: "v0.32.15", Sum: "h1:lnCycypBjS9SoMNeM6FivlYeDRn7mP/zfLG0uJXwmZ4="}}}
	calls := 0
	factory := museEstimatorFactory(hash, build, id, func(context.Context) (providerIdentity, error) { return id, nil }, func(context.Context, providers.Request) (int, bool, error) { calls++; return 100, true, nil })
	b := sdk.ContextModelBinding{ModelID: "local-muse", Model: "muse-glimmer:30b-mlx", Kind: "ollama", Endpoint: id.Endpoint}
	bad := b
	bad.Endpoint = "http://127.0.0.1:11435"
	if _, err := factory([]sdk.ContextModelBinding{b, bad}); err == nil {
		t.Fatal("accepted conflicting alias")
	}
	if calls != 0 {
		t.Fatal("counted before binding validation")
	}
	estimator, err := factory([]sdk.ContextModelBinding{b})
	if err != nil {
		t.Fatal(err)
	}
	req := providers.Request{Model: b.Model, Messages: []providers.Message{{Role: "user", Content: strings.Repeat("x", 10000)}}}
	got, err := providers.EstimateWith(context.Background(), estimator, req)
	if err != nil || got != 1124 || calls != 1 {
		t.Fatalf("got %d, calls %d, err %v", got, calls, err)
	}
}

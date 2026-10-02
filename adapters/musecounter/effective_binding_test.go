package musecounter

import (
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"testing"
)

func TestEffectiveMuseBindingRejectsConflictingAlias(t *testing.T) {
	good := sdk.ContextModelBinding{ModelID: "local-muse", Model: "muse-glimmer:30b-mlx", ProviderID: "local", Kind: "ollama", Endpoint: "http://127.0.0.1:11434"}
	for _, field := range []string{"endpoint", "kind"} {
		bad := good
		bad.ModelID = "other-alias"
		if field == "endpoint" {
			bad.Endpoint = "http://127.0.0.1:11435"
		} else {
			bad.Kind = "openai"
		}
		for _, bs := range [][]sdk.ContextModelBinding{{good, bad}, {bad, good}} {
			if _, err := effectiveMuseBinding(bs); err == nil {
				t.Fatalf("accepted conflicting %s alias", field)
			}
		}
	}
	if _, err := effectiveMuseBinding(nil); err == nil {
		t.Fatal("accepted missing model")
	}
	unrelated := good
	unrelated.Model = "another-model"
	unrelated.Endpoint = "http://127.0.0.1:11435"
	if _, err := effectiveMuseBinding([]sdk.ContextModelBinding{unrelated, good, good}); err != nil {
		t.Fatal(err)
	}
}

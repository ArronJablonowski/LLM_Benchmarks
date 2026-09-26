package musecounter

import "testing"

func TestProviderBinding(t *testing.T) {
	if err := validateProviderBinding("ollama", "http://127.0.0.1:11434", "muse-glimmer:30b-mlx"); err != nil {
		t.Fatal(err)
	}
	for _, tc := range [][3]string{
		{"openai", "http://127.0.0.1:11434", "muse-glimmer:30b-mlx"},
		{"ollama", "http://localhost:11434", "muse-glimmer:30b-mlx"},
		{"ollama", "http://127.0.0.1:11435", "muse-glimmer:30b-mlx"},
		{"ollama", "http://127.0.0.1:11434/proxy", "muse-glimmer:30b-mlx"},
		{"ollama", "http://127.0.0.1:11434", "other"},
	} {
		if validateProviderBinding(tc[0], tc[1], tc[2]) == nil {
			t.Errorf("accepted mismatched binding %q", tc)
		}
	}
}

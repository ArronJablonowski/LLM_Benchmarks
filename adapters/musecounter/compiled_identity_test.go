package musecounter

import (
	"runtime/debug"
	"testing"
)

func TestCompiledRendererIdentity(t *testing.T) {
	good := debug.Module{Path: "github.com/ollama/ollama", Version: "v0.32.15", Sum: "h1:lnCycypBjS9SoMNeM6FivlYeDRn7mP/zfLG0uJXwmZ4="}
	if err := validateCompiledRenderer(&debug.BuildInfo{Deps: []*debug.Module{&good}}); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"missing", "version", "sum", "replacement"} {
		t.Run(kind, func(t *testing.T) {
			d := good
			b := &debug.BuildInfo{Deps: []*debug.Module{&d}}
			switch kind {
			case "missing":
				b = nil
			case "version":
				d.Version = "v0.32.16"
			case "sum":
				d.Sum = ""
			case "replacement":
				d.Replace = &debug.Module{Path: "./local"}
			}
			if validateCompiledRenderer(b) == nil {
				t.Fatal("unverified dependency accepted")
			}
		})
	}
}

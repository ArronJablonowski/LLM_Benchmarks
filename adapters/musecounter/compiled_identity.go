package musecounter

import (
	"errors"
	"runtime/debug"
)

// This verifies the compiled dependency pin, not equivalence to the serving
// binary. Live process and transcript checks remain separate requirements.
func validateCompiledRenderer(info *debug.BuildInfo) error {
	if info != nil {
		for _, dep := range info.Deps {
			if dep.Path == "github.com/ollama/ollama" {
				if dep.Replace == nil && dep.Version == "v0.32.15" && dep.Sum == "h1:lnCycypBjS9SoMNeM6FivlYeDRn7mP/zfLG0uJXwmZ4=" {
					return nil
				}
				return errors.New("unverified compiled renderer dependency")
			}
		}
	}
	return errors.New("compiled renderer identity unavailable")
}

package musecounter

import "errors"

// validateProviderBinding must be called with the effective SDK configuration,
// not a separately parsed approximation, before installing the counter.
func validateProviderBinding(kind, endpoint, model string) error {
	if kind != "ollama" || endpoint != "http://127.0.0.1:11434" || model != "muse-glimmer:30b-mlx" {
		return errors.New("exact accounting provider binding mismatch")
	}
	return nil
}

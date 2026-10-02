package musecounter

import (
	"errors"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
)

// Every alias sharing the wire model must use the attested provider: the
// estimator receives the wire model name, not the configured model ID.
func effectiveMuseBinding(bindings []sdk.ContextModelBinding) (sdk.ContextModelBinding, error) {
	var selected sdk.ContextModelBinding
	found := false
	for _, b := range bindings {
		if b.Model != "muse-glimmer:30b-mlx" {
			continue
		}
		if err := validateProviderBinding(b.Kind, b.Endpoint, b.Model); err != nil {
			return selected, err
		}
		selected, found = b, true
	}
	if !found {
		return selected, errors.New("attested model absent from effective configuration")
	}
	return selected, nil
}

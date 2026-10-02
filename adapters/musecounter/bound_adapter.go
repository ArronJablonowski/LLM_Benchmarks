package musecounter

import (
	"context"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	"runtime/debug"
)

// newAttestedCounter accepts identity from the effective SDK configuration.
// Production callers must supply that same configuration to provider dispatch.
func newAttestedCounter(kind, endpoint, model, tokenizerHash string, build *debug.BuildInfo, expected providerIdentity, probe func(context.Context) (providerIdentity, error), count providers.TokenCounter) (*providers.BoundTokenCounter, error) {
	if err := validateProviderBinding(kind, endpoint, model); err != nil {
		return nil, err
	}
	if err := validateCompiledRenderer(build); err != nil {
		return nil, err
	}
	guarded, err := identityBoundCounter(expected, probe, count)
	if err != nil {
		return nil, err
	}
	return providers.NewBoundTokenCounter(model, tokenizerHash, expected.RendererSHA256, guarded)
}

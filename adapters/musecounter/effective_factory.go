package musecounter

import (
	"context"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"runtime/debug"
)

// The SDK supplies its validated effective configuration before admission.
func museEstimatorFactory(tokenizerHash string, build *debug.BuildInfo, expected providerIdentity, probe func(context.Context) (providerIdentity, error), count providers.TokenCounter) sdk.ContextEstimatorFactory {
	return func(bindings []sdk.ContextModelBinding) (sdk.ContextEstimator, error) {
		b, err := effectiveMuseBinding(bindings)
		if err != nil {
			return nil, err
		}
		return newAttestedCounter(b.Kind, b.Endpoint, b.Model, tokenizerHash, build, expected, probe, count)
	}
}

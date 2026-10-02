package musecounter

import (
	"context"
	"encoding/hex"
	"errors"
	"github.com/ArronJablonowski/DarwinRouter/providers"
)

// providerIdentity covers the serving process, endpoint, model manifest and
// loaded accounting implementation. Matching names or version strings alone is
// insufficient. The probe must derive these from the live endpoint's owner.
type providerIdentity struct {
	Endpoint, ProcessStart, ExecutableSHA256, ManifestSHA256, RendererSHA256 string
}

func identityBoundCounter(expected providerIdentity, probe func(context.Context) (providerIdentity, error), count providers.TokenCounter) (providers.TokenCounter, error) {
	if expected.Endpoint != "http://127.0.0.1:11434" || expected.ProcessStart == "" || !validHash(expected.ExecutableSHA256) || !validHash(expected.ManifestSHA256) || !validHash(expected.RendererSHA256) || probe == nil || count == nil {
		return nil, errors.New("invalid identity binding")
	}
	return func(ctx context.Context, r providers.Request) (int, bool, error) {
		if e := ctx.Err(); e != nil {
			return 0, false, e
		}
		current, e := probe(ctx)
		if e != nil {
			return 0, false, e
		}
		if current != expected {
			return 0, false, nil
		}
		n, supported, e := count(ctx, r)
		if e != nil {
			return 0, false, e
		}
		// Recheck after counting to detect changes during tokenization.
		current, e = probe(ctx)
		if e != nil {
			return 0, false, e
		}
		if current != expected {
			return 0, false, nil
		}
		if e = ctx.Err(); e != nil {
			return 0, false, e
		}
		return n, supported, nil
	}, nil
}

func validHash(s string) bool { b, e := hex.DecodeString(s); return e == nil && len(b) == 32 }

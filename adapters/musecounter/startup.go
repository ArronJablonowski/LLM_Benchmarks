package musecounter

import (
	"context"
	"errors"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
	"io"
	"os"
	"runtime/debug"
)

// Identity is an explicitly accepted provider identity, not an automatic trust
// decision based on the currently running process.
type Identity = providerIdentity

// NewFactory verifies local assets and the accepted live identity before
// returning a factory that binds the SDK's effective provider configuration.
// This adapter currently supports only the documented local macOS host.
func NewFactory(ctx context.Context, expected Identity, tokenizerPath, configPath string) (sdk.ContextEstimatorFactory, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	build, ok := debug.ReadBuildInfo()
	if !ok {
		return nil, errors.New("compiled renderer identity unavailable")
	}
	if err := validateCompiledRenderer(build); err != nil {
		return nil, err
	}
	actual, err := probeLocalMuse(ctx)
	if err != nil {
		return nil, err
	}
	if actual != expected {
		return nil, errors.New("accepted provider identity mismatch")
	}
	blob, err := readAsset(ctx, tokenizerPath, 64<<20)
	if err != nil {
		return nil, err
	}
	cfg, err := readAsset(ctx, configPath, 2<<20)
	if err != nil {
		return nil, err
	}
	count, err := museCounter(blob, cfg)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return museEstimatorFactory(tokenizerDigest, build, expected, probeLocalMuse, count), nil
}

func readAsset(ctx context.Context, path string, limit int64) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > limit {
		return nil, errors.New("invalid tokenizer asset")
	}
	data := make([]byte, 0, info.Size())
	buf := make([]byte, 64<<10)
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n, err := f.Read(buf)
		if int64(len(data)+n) > limit {
			return nil, errors.New("tokenizer asset exceeds bound")
		}
		data = append(data, buf[:n]...)
		if err == io.EOF {
			return data, nil
		}
		if err != nil {
			return nil, err
		}
	}
}

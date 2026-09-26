package musecounter

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestAssetBoundsAndCancellation(t *testing.T) {
	p := filepath.Join(t.TempDir(), "asset")
	if err := os.WriteFile(p, []byte("12345"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readAsset(context.Background(), p, 4); err == nil {
		t.Fatal("accepted oversized asset")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := readAsset(ctx, p, 5); err != context.Canceled {
		t.Fatalf("cancel: %v", err)
	}
	if _, err := NewFactory(ctx, Identity{}, p, p); err != context.Canceled {
		t.Fatalf("startup cancel: %v", err)
	}
	if b, err := readAsset(context.Background(), p, 5); err != nil || string(b) != "12345" {
		t.Fatalf("valid: %q %v", b, err)
	}
}

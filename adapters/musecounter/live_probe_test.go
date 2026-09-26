package musecounter

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestLiveIdentityProbe(t *testing.T) {
	if os.Getenv("MUSE_PROBE_LIVE") != "1" {
		t.Skip("explicit read-only local probe")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	got, e := probeLocalMuse(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if got.ExecutableSHA256 != "2471834bf1d481b01e5294b4d153579eb5cbe11401317e1d4e622328a474bba5" || got.ManifestSHA256 != "ef32a55b4976faa955cbab0462d09bd081351ef5b87d73d8fcd299bf17c111d7" {
		t.Fatal("identity changed")
	}
	t.Log("live process and asset identity verified")
}
func TestProbeCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e := probeLocalMuse(ctx); e == nil {
		t.Fatal("cancellation ignored")
	}
}

package musecounter

import (
	"context"
	"errors"
	"github.com/ArronJablonowski/DarwinRouter/providers"
	"strings"
	"testing"
)

func TestIdentityGate(t *testing.T) {
	expected := providerIdentity{"http://127.0.0.1:11434", "pid/start", strings.Repeat("a", 64), strings.Repeat("b", 64), strings.Repeat("c", 64)}
	for _, mode := range []string{"match", "changed_before", "changed_during", "probe_error", "canceled"} {
		t.Run(mode, func(t *testing.T) {
			probes, calls := 0, 0
			gate, e := identityBoundCounter(expected, func(context.Context) (providerIdentity, error) {
				probes++
				got := expected
				if mode == "probe_error" {
					return got, errors.New("probe failed")
				}
				if mode == "changed_before" || (mode == "changed_during" && probes == 2) {
					got.ManifestSHA256 = strings.Repeat("d", 64)
				}
				return got, nil
			}, func(context.Context, providers.Request) (int, bool, error) { calls++; return 100, true, nil })
			if e != nil {
				t.Fatal(e)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "canceled" {
				cancel()
			}
			n, ok, e := gate(ctx, providers.Request{})
			switch mode {
			case "match":
				if e != nil || !ok || n != 100 || probes != 2 {
					t.Fatal(n, ok, e, probes)
				}
			case "changed_before", "changed_during":
				if e != nil || ok {
					t.Fatal("identity change trusted")
				}
			case "probe_error", "canceled":
				if e == nil || calls != 0 {
					t.Fatal("failed identity probe dispatched counter")
				}
			}
			if mode == "changed_before" && calls != 0 {
				t.Fatal("changed identity counted")
			}
		})
	}
}

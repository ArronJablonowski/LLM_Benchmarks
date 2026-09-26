package musecounter

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestTokenizerWaitCancellation(t *testing.T) {
	gate := make(chan struct{}, 1)
	gate <- struct{}{}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- acquireTokenizer(ctx, gate) }()
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("canceled waiter remained blocked")
	}
	if len(gate) != 1 {
		t.Fatal("waiter changed active ownership")
	}
	<-gate
	if err := acquireTokenizer(context.Background(), gate); err != nil {
		t.Fatal(err)
	}
	<-gate
}

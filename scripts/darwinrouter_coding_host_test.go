package main

import (
	"context"
	"errors"
	"testing"

	"github.com/ArronJablonowski/DarwinRouter/providers"
	sdk "github.com/ArronJablonowski/DarwinRouter/sdk/v1"
)

func TestToolRepairContinuesWithoutExecutingTextAndPreservesAccounting(t *testing.T) {
	calls := 0
	run := func(_ context.Context, request sdk.Request) (sdk.Result, error) {
		calls++
		cost := 0.25
		if calls == 1 {
			return sdk.Result{TaskID: "first", Text: "<function=benchmark_list_files>\n</function>\n</tool_call>", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 10, OutputTokens: 3}}, nil
		}
		if request.ContinueTaskID != "first" || request.ModelID != "fixed" || !request.LocalRequired {
			t.Fatalf("lost continuation or routing constraint: %+v", request)
		}
		return sdk.Result{TaskID: "second", Text: "Implemented and tested", RouteEstimatedCost: &cost, Usage: &providers.Usage{InputTokens: 20, OutputTokens: 5}}, nil
	}
	out, err := runWithToolRepair(context.Background(), run, sdk.Request{ModelID: "fixed", LocalRequired: true}, true)
	if err != nil || calls != 2 || len(out.PreviousTaskIDs) != 1 || out.PreviousTaskIDs[0] != "first" || *out.RouteEstimatedCost != .5 || out.Usage.InputTokens != 30 || out.Usage.OutputTokens != 8 {
		t.Fatal(out, err, calls)
	}
}

func TestToolRepairIsBoundedAndDoesNotRetryExecutionErrors(t *testing.T) {
	for _, broken := range []bool{false, true} {
		calls := 0
		run := func(context.Context, sdk.Request) (sdk.Result, error) {
			calls++
			out := sdk.Result{TaskID: "task", Text: "<function=benchmark_list_files></tool_call>"}
			if broken {
				return out, errors.New("execution interrupted")
			}
			return out, nil
		}
		_, err := runWithToolRepair(context.Background(), run, sdk.Request{}, true)
		want := 3
		if broken {
			want = 1
		}
		if err == nil || calls != want {
			t.Fatal("unbounded repair or interrupted execution retried", calls, err)
		}
	}
}

package musecounter

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

// probeLocalMuse is read-only and inherits the estimator's deadline. Pin the
// caller's provider endpoint separately before installing this counter.
func probeLocalMuse(ctx context.Context) (providerIdentity, error) {
	fail := func() (providerIdentity, error) {
		return providerIdentity{}, errors.New("provider identity unavailable")
	}
	out, e := exec.CommandContext(ctx, "/usr/sbin/lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN", "-Fp").Output()
	if e != nil {
		return fail()
	}
	pid := ""
	for _, s := range strings.Fields(string(out)) {
		if strings.HasPrefix(s, "p") {
			if pid != "" {
				return fail()
			}
			pid = s[1:]
		}
	}
	if n, e := strconv.Atoi(pid); e != nil || n < 1 {
		return fail()
	}
	start, e := exec.CommandContext(ctx, "/bin/ps", "-p", pid, "-o", "lstart=").Output()
	if e != nil || strings.TrimSpace(string(start)) == "" {
		return fail()
	}
	out, e = exec.CommandContext(ctx, "/usr/sbin/lsof", "-a", "-p", pid, "-d", "txt", "-Fn").Output()
	if e != nil {
		return fail()
	}
	binary := "/opt/homebrew/Cellar/ollama/0.32.15/libexec/ollama"
	found := false
	for _, line := range strings.Split(string(out), "\n") {
		if line == "n"+binary {
			found = true
		}
	}
	if !found {
		return fail()
	}
	bh, e := hashFile(ctx, binary)
	if e != nil {
		return fail()
	}
	mh, e := hashFile(ctx, "/Users/aj_lobster/.ollama/models/manifests/registry.ollama.ai/library/muse-glimmer/30b-mlx")
	if e != nil {
		return fail()
	}
	rh, e := hashFile(ctx, "/Users/aj_lobster/go/pkg/mod/github.com/ollama/ollama@v0.32.15/model/renderers/glimmer.go")
	if e != nil {
		return fail()
	}
	if ctx.Err() != nil {
		return fail()
	}
	return providerIdentity{"http://127.0.0.1:11434", pid + ":" + strings.TrimSpace(string(start)), bh, mh, rh}, nil
}
func hashFile(ctx context.Context, path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 64*1024)
	for {
		if e = ctx.Err(); e != nil {
			return "", e
		}
		n, err := f.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

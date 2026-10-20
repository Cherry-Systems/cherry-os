package main

import (
	"archive/tar"
	"bytes"
	"context"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

func TestOsPath(t *testing.T) {
	for in, want := range map[string]string{
		`C:\Users\Alex\notes.txt`:       `C:\Users\Alex\notes.txt`,
		`C:\Users\Alex\notes.`:          `\\?\C:\Users\Alex\notes.`,
		`C:\Users\Alex\from wsl \a.txt`: `\\?\C:\Users\Alex\from wsl \a.txt`,
		`\\nas\home\old..\a`:            `\\?\UNC\nas\home\old..\a`,
		`\\?\C:\x.`:                     `\\?\C:\x.`,
		`C:\Users\Alex\.ssh`:            `C:\Users\Alex\.ssh`,
	} {
		if got := osPath(in); got != want {
			t.Errorf("osPath(%q) = %q, want %q", in, got, want)
		}
	}
}

// Files and folders whose names end in a dot or a space (WSL, Cygwin and Macs make them) come, contents and all.
func TestNamesEndingInADotOrSpaceArePacked(t *testing.T) {
	root := t.TempDir()
	for _, f := range []string{`plain.txt`, `dot.`, `space `, `folder.\inside.txt`, `folder \two dots..`} {
		p := `\\?\` + filepath.Join(root, f)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(f), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	plan, err := makePlan(context.Background(), []*Category{{ID: "other", Paths: []Src{{Prefix: "home/x", Path: root}}}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	skipped, err := writeTar(context.Background(), plan, nil, &buf, nil)
	if err != nil || len(skipped) > 0 {
		t.Fatalf("err %v, skipped %q", err, skipped)
	}
	got := map[string]string{}
	tr := tar.NewReader(&buf)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			data, _ := io.ReadAll(tr)
			got[h.Name] = string(data)
		}
	}
	want := map[string]string{"home/x/plain.txt": `plain.txt`, "home/x/dot.": `dot.`, "home/x/space ": `space `,
		"home/x/folder./inside.txt": `folder.\inside.txt`, "home/x/folder /two dots..": `folder \two dots..`}
	if !reflect.DeepEqual(got, want) {
		names := make([]string, 0, len(got))
		for n := range got {
			names = append(names, n)
		}
		sort.Strings(names)
		t.Fatalf("got %q", names)
	}
}

// A time from before 1678 (Windows shows a time that was never set as 1 January 1601) isn't wrapped round.
func TestTimesFrom1601(t *testing.T) {
	p := filepath.Join(t.TempDir(), "old.txt")
	if err := os.WriteFile(p, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	h, err := windows.CreateFile(windows.StringToUTF16Ptr(p), windows.FILE_WRITE_ATTRIBUTES, 0, nil,
		windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	ft := windows.Filetime{LowDateTime: 10_000_000} // 1601-01-01 00:00:01 UTC
	err = windows.SetFileTime(h, nil, nil, &ft)
	windows.CloseHandle(h)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(p)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := modTime(info), time.Date(1601, 1, 1, 0, 0, 1, 0, time.UTC); !got.Equal(want) {
		t.Fatalf("modTime = %v, want %v", got, want)
	}
}

// Cherry Move for Windows, macOS and Linux: packs your files on your old computer so Cherry OS can bring
// them back. Run it with no arguments for the app; `send` is a command-line version (used by tests).
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
)

var version = "1.0"

// startLog starts a new log.txt (so not in a copy that only brings the open one to the front).
func startLog() {
	if dir := stateDir(); dir != "" {
		_ = os.MkdirAll(dir, 0o700)
		if f, err := os.OpenFile(filepath.Join(dir, "log.txt"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600); err == nil {
			log.SetOutput(io.MultiWriter(f, os.Stderr))
		}
	}
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "send" {
		startLog()
		if err := cliSend(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "cherry-move:", err)
			os.Exit(1)
		}
		return
	}
	fs := flag.NewFlagSet("cherry-move", flag.ExitOnError)
	noBrowser := fs.Bool("no-browser", false, "don't open a window (print the address instead)")
	inBrowser := fs.Bool("browser", false, "open in the browser instead of the app's own window")
	showVersion := fs.Bool("version", false, "print the version")
	_ = fs.Parse(os.Args[1:])
	if *showVersion {
		fmt.Println("Cherry Move", version)
		return
	}
	mode := "window"
	if *noBrowser {
		mode = "none"
	} else if *inBrowser {
		mode = "browser"
	}
	if mode != "none" && raiseRunning() {
		return
	}
	startLog()
	if err := serve(mode); err != nil {
		log.Fatal(err)
	}
}

func cliSend(args []string) error {
	fs := flag.NewFlagSet("send", flag.ExitOnError)
	to := fs.String("to", "usb", "usb or folder")
	usb := fs.String("usb", "", "the USB stick (its drive or mount point)")
	folder := fs.String("folder", "", "folder (a NAS share) to keep the files in")
	only := fs.String("only", "", "comma-separated categories (default: all that are ticked in the app)")
	strong := fs.Bool("strong", false, "compress harder (slower)")
	sticks := fs.String("next-sticks", "", "comma-separated sticks to use, in order, when one fills up")
	capacity := fs.Int64("stick-capacity", 0, "pretend each stick holds this many bytes (for tests)")
	var skip folders
	fs.Var(&skip, "skip", "leave this folder behind (repeatable)")
	list := fs.Bool("list", false, "print every file and folder that would come, and stop")
	_ = fs.Parse(args)
	if *capacity > 0 {
		limit := *capacity
		spaceOf = func(path string) int64 { return limit - transferBytes(path) }
	}
	var chosen []*Category
	want := map[string]bool{}
	for _, id := range strings.Split(*only, ",") {
		if id != "" {
			want[id] = true
		}
	}
	for _, c := range categories() {
		if (len(want) == 0 && c.Default) || want[c.ID] {
			chosen = append(chosen, c)
		}
	}
	if len(skip) > 0 {
		for _, c := range chosen {
			var kept []Src
			for _, s := range c.Paths {
				if skip[s.Path] {
					continue
				}
				merged := map[string]bool{}
				for p := range s.Skip {
					merged[p] = true
				}
				for p := range skip {
					merged[p] = true
				}
				s.Skip = merged
				kept = append(kept, s)
			}
			c.Paths = kept
		}
	}
	if *list {
		w := bufio.NewWriter(os.Stdout)
		defer w.Flush()
		for _, c := range chosen {
			for _, s := range c.Paths {
				_ = walk(s, func(e entry) error {
					fmt.Fprintf(w, "%s\t%s\n", c.ID, e.path)
					return nil
				})
			}
		}
		return nil
	}
	plan, err := makePlan(context.Background(), chosen, nil)
	if err != nil {
		return err
	}
	opt := SendOptions{Storage: *to, Drive: *usb, Folder: *folder, Level: Normal}
	if *strong {
		opt.Level = Strong
	}
	if *to == "folder" {
		opt.Descriptor = map[string]any{"kind": "nas", "title": "your NAS (test)", "type": "smb", "host": "test", "path": "share"}
	}
	queue := strings.Split(*sticks, ",")
	opt.NextStick = func(part int, problem string) (string, error) {
		if problem != "" || len(queue) == 0 || queue[0] == "" {
			return "", fmt.Errorf("stick %d needed (%s)", part, problem)
		}
		next := queue[0]
		queue = queue[1:]
		return next, nil
	}
	res, err := send(context.Background(), plan, opt)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(res)
}

// folders is a repeatable flag of folder paths.
type folders map[string]bool

func (f *folders) String() string { return "" }

func (f *folders) Set(p string) error {
	if *f == nil {
		*f = folders{}
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return err
	}
	(*f)[abs] = true
	return nil
}

func transferBytes(drive string) int64 {
	var n int64
	_ = filepath.Walk(filepath.Join(drive, markerDir), func(_ string, info os.FileInfo, err error) error {
		if err == nil && info.Mode().IsRegular() {
			n += info.Size()
		}
		return nil
	})
	return n
}

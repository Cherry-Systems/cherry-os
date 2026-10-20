package main

// What to bring, walking it, and finding identical files (stored once), as in transfer.py.

import (
	"context"
	"io"
	"io/fs"
	"math/rand"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"golang.org/x/crypto/blake2b"
)

const dedupMin = 16 * 1024

// Src is one folder or file to bring, stored under Prefix ("xdg/DOCUMENTS", "home/.mozilla/firefox", …).
// With Only set, just the files matching those patterns (relative, slash-separated) are brought. Folders in
// Skip are brought by another category.
type Src struct {
	Prefix string
	Path   string
	Only   []string
	Skip   map[string]bool
}

// Folders in the home folder left behind on purpose, as LEAVE_BEHIND in transfer.py (* stands for one folder
// name): caches their apps rebuild, and things downloaded again on the new computer from the list in the
// transfer (Ollama models, Flatpak apps, Rust toolchains…).
var leaveBehind = []string{
	".local/share/Trash", ".var/app/*/cache",
	".npm/_cacache", ".npm/_logs", ".npm/_npx",
	".gradle/caches", ".gradle/wrapper/dists", ".gradle/daemon",
	".m2/repository", ".cargo/registry", ".cargo/git",
	".android/cache", ".fgfs/TerraSync",
	".rustup/downloads", ".rustup/tmp", ".rustup/toolchains",
	".ollama/models", ".local/share/flatpak", ".rembg",
	".arduino15/staging", ".arduino15/packages",
	".platformio/.cache", ".platformio/packages", ".platformio/platforms",
	".local/share/Steam/steamapps/shadercache", ".local/share/Steam/steamapps/downloading",
	".local/share/Steam/steamapps/common",
	".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/shadercache",
	".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/downloading",
	".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common",
}

var homeDir, _ = os.UserHomeDir()

func leftOnPurpose(full string) bool {
	if homeDir == "" || !strings.HasPrefix(full, homeDir+string(os.PathSeparator)) {
		return false
	}
	rel := filepath.ToSlash(full[len(homeDir)+1:])
	for _, pat := range leaveBehind {
		if ok, _ := path.Match(pat, rel); ok {
			return true
		}
	}
	return false
}

// rebuiltNode: a node_modules in `parent` is left behind to be rebuilt only in your own projects (a
// package.json beside it, not inside an app's hidden folder); apps' own ones (editor extensions, MCP servers,
// npm -g) come along. As _rebuilt_node in transfer.py.
func rebuiltNode(parent string) bool {
	if !fileExists(filepath.Join(parent, "package.json")) {
		return false
	}
	rel, err := filepath.Rel(homeDir, parent)
	if err != nil || homeDir == "" || strings.HasPrefix(rel, "..") {
		return true
	}
	for _, part := range strings.Split(filepath.ToSlash(rel), "/") {
		if part != "." && strings.HasPrefix(part, ".") {
			return false
		}
	}
	return true
}

func fileExists(p string) bool {
	fi, err := os.Stat(osPath(p))
	return err == nil && fi.Mode().IsRegular()
}

type Category struct {
	ID      string `json:"id"`
	Label   string `json:"label"`
	Note    string `json:"note,omitempty"`
	Default bool   `json:"default"`
	Size    int64  `json:"size"`
	Files   int    `json:"files"`
	Paths   []Src  `json:"-"`
}

var skipNames = map[string]bool{
	".cache": true, "Trash": true, "node_modules": true, ".venv": true, "__pycache__": true,
	"$RECYCLE.BIN": true, "System Volume Information": true, "desktop.ini": true, "Thumbs.db": true,
	".Trash": true, ".DS_Store": true, ".localized": true,
	// Made by the desktop on every computer, pointing at programs on the old one.
	"NativeMessagingHosts": true, "native-messaging-hosts": true, "plasma-browser-integration-host": true,
}

// Folders inside a Mac app's library where the person's own files are; the rest is that app's database,
// thumbnails and caches. Photos keeps originals in "originals" (macOS 10.15+) or "Masters" (before).
var libraryKeep = map[string][]string{
	".photoslibrary": {"originals", "Masters"},
}

type entry struct {
	path string
	arc  string
	info fs.FileInfo
}

func matchesOnly(only []string, rel string) bool {
	for _, pat := range only {
		if ok, _ := path.Match(pat, rel); ok {
			return true
		}
	}
	return false
}

// walk visits a source in a stable order: the folder itself, then everything inside it. Links and
// junctions are recorded (on Linux and macOS) or skipped (Windows), never followed.
func walk(src Src, visit func(entry) error) error {
	return walkLeaving(src, visit, nil)
}

// walkLeaving is walk that also tells `left` about each Python environment ("python") and node_modules
// folder ("node") it leaves behind, so the new computer can rebuild them.
func walkLeaving(src Src, visit func(entry) error, left func(path, kind string)) error {
	info, err := os.Lstat(osPath(src.Path))
	if err != nil {
		return nil
	}
	if !info.IsDir() {
		if skipEntry(src.Path, info) {
			return nil
		}
		return visit(entry{src.Path, src.Prefix, info})
	}
	if src.Only == nil {
		if err := visit(entry{src.Path, src.Prefix, info}); err != nil {
			return err
		}
	}
	var rec func(dir, rel string) error
	rec = func(dir, rel string) error {
		items, err := os.ReadDir(osPath(dir))
		if err != nil {
			return nil
		}
		if keep, ok := libraryKeep[strings.ToLower(filepath.Ext(dir))]; ok {
			var kept []fs.DirEntry
			for _, it := range items {
				for _, k := range keep {
					if it.Name() == k && it.IsDir() {
						kept = append(kept, it)
					}
				}
			}
			items = kept
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Name() < items[j].Name() })
		var dirs []fs.DirEntry
		for _, it := range items {
			full := filepath.Join(dir, it.Name())
			if skipNames[it.Name()] && (it.Name() != "node_modules" || rebuiltNode(dir)) {
				if left != nil && it.Name() == ".venv" && it.IsDir() {
					left(full, "python")
				} else if left != nil && it.Name() == "node_modules" {
					left(full, "node")
				}
				continue
			}
			if src.Skip[full] || leftOnPurpose(full) {
				continue
			}
			childRel := it.Name()
			if rel != "" {
				childRel = rel + "/" + it.Name()
			}
			info, err := os.Lstat(osPath(full))
			if err != nil || skipEntry(full, info) {
				continue
			}
			if info.IsDir() && fileExists(filepath.Join(full, "pyvenv.cfg")) {
				if left != nil {
					left(full, "python")
				}
				continue
			}
			if info.IsDir() {
				dirs = append(dirs, it)
				if src.Only == nil {
					if err := visit(entry{full, src.Prefix + "/" + childRel, info}); err != nil {
						return err
					}
				}
				continue
			}
			if src.Only != nil && !matchesOnly(src.Only, childRel) {
				continue
			}
			if err := visit(entry{full, src.Prefix + "/" + childRel, info}); err != nil {
				return err
			}
		}
		for _, d := range dirs {
			childRel := d.Name()
			if rel != "" {
				childRel = rel + "/" + d.Name()
			}
			if err := rec(filepath.Join(dir, d.Name()), childRel); err != nil {
				return err
			}
		}
		return nil
	}
	return rec(src.Path, "")
}

func measure(c *Category) {
	c.Size, c.Files = 0, 0
	for _, s := range c.Paths {
		_ = walk(s, func(e entry) error {
			if e.info.Mode().IsRegular() {
				c.Size += e.info.Size()
				c.Files++
			}
			return nil
		})
	}
}

type sameInfo struct {
	FirstArc string
	Size     int64
	Mtime    int64
}

type typeInfo struct {
	Bytes   int64
	Files   int
	Samples []string
}

type Plan struct {
	Cats       []*Category
	Size       int64 // bytes stored, each set of identical files counted once
	Files      int   // files restored, duplicates included
	Duplicates int
	Saved      int64
	Same       map[string]sameInfo  // path -> its first copy
	First      map[string][2]int64  // arc of a first copy -> size, mtime
	Types      map[string]*typeInfo // extension -> totals and sample files
	Envs       []string             // Python environments left behind, rebuilt on the new computer
	Node       []string             // folders whose node_modules is rebuilt there
}

func (p *Plan) RawSize() int64 { return p.Size + p.Saved }

func ext(name string) string {
	e := strings.ToLower(path.Ext(name))
	if len(e) > 1 && len(e) <= 8 {
		return e
	}
	return ""
}

func digest(p string, head bool) []byte {
	f, err := os.Open(osPath(p))
	if err != nil {
		return nil
	}
	defer f.Close()
	h, _ := blake2b.New(20, nil)
	if head {
		_, err = io.CopyN(h, f, 64*1024)
		if err == io.EOF {
			err = nil
		}
	} else {
		_, err = io.Copy(h, f)
	}
	if err != nil {
		return nil
	}
	return h.Sum(nil)
}

type Progress func(done, total int64, name string)

func makePlan(ctx context.Context, cats []*Category, progress Progress) (*Plan, error) {
	p := &Plan{Cats: cats, Same: map[string]sameInfo{}, First: map[string][2]int64{}, Types: map[string]*typeInfo{}}
	rng := rand.New(rand.NewSource(0))
	type cand struct {
		order int
		path  string
		arc   string
		mtime int64
	}
	bySize := map[int64][]cand{}
	order := 0
	left := func(path, kind string) {
		if kind == "python" {
			p.Envs = append(p.Envs, path)
		} else {
			p.Node = append(p.Node, filepath.Dir(path))
		}
	}
	for _, c := range cats {
		for _, s := range c.Paths {
			err := walkLeaving(s, func(e entry) error {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				if !e.info.Mode().IsRegular() {
					return nil
				}
				size := e.info.Size()
				p.Files++
				p.Size += size
				t := p.Types[ext(e.arc)]
				if t == nil {
					t = &typeInfo{}
					p.Types[ext(e.arc)] = t
				}
				t.Bytes += size
				t.Files++
				if len(t.Samples) < 8 {
					t.Samples = append(t.Samples, e.path)
				} else if j := rng.Intn(t.Files); j < 8 {
					t.Samples[j] = e.path
				}
				if size >= dedupMin {
					bySize[size] = append(bySize[size], cand{order, e.path, e.arc, e.info.ModTime().UnixNano()})
					order++
				}
				return nil
			}, left)
			if err != nil {
				return nil, err
			}
		}
	}
	var work, done int64
	sizes := make([]int64, 0)
	for size, g := range bySize {
		if len(g) > 1 {
			sizes = append(sizes, size)
			work += size * int64(len(g))
		}
	}
	sort.Slice(sizes, func(i, j int) bool { return sizes[i] < sizes[j] })
	group := func(items []cand, head bool) [][]cand {
		buckets := map[string][]cand{}
		var keys []string
		for _, it := range items {
			if d := digest(it.path, head); d != nil {
				k := string(d)
				if _, ok := buckets[k]; !ok {
					keys = append(keys, k)
				}
				buckets[k] = append(buckets[k], it)
			}
		}
		var out [][]cand
		for _, k := range keys {
			if len(buckets[k]) > 1 {
				out = append(out, buckets[k])
			}
		}
		return out
	}
	for _, size := range sizes {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		g := bySize[size]
		for _, bucket := range group(g, true) {
			sets := [][]cand{bucket}
			if size > 64*1024 {
				sets = group(bucket, false)
			}
			for _, items := range sets {
				sort.Slice(items, func(i, j int) bool { return items[i].order < items[j].order })
				first := items[0]
				for _, it := range items[1:] {
					p.Same[it.path] = sameInfo{first.arc, size, it.mtime}
					p.First[first.arc] = [2]int64{size, first.mtime}
					p.Duplicates++
					p.Saved += size
					p.Size -= size
					p.Types[ext(it.arc)].Bytes -= size
				}
			}
		}
		done += size * int64(len(g))
		if progress != nil {
			progress(done, max(work, 1), "Looking for duplicate files")
		}
	}
	return p, nil
}

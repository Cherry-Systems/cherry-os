package main

// Packing and storing a move, in the same format as desktop/lib/cherryos/transfer.py: a PAX tar stream cut
// into 8 MiB chunks, each compressed and encrypted; chunk 0 is the manifest. Identical files are stored once
// (as tar hard links).

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
)

func chunkName(index int) string { return fmt.Sprintf("data/%05d/%08d.bin", index/1000, index) }

func human(n int64) string {
	f := float64(n)
	for _, unit := range []string{"B", "KB", "MB", "GB", "TB"} {
		if f < 1000 || unit == "TB" {
			if unit == "B" || unit == "KB" {
				return fmt.Sprintf("%.0f %s", f, unit)
			}
			return fmt.Sprintf("%.1f %s", f, unit)
		}
		f /= 1000
	}
	return fmt.Sprint(n)
}

type Drive struct {
	Path   string         `json:"path"`
	Label  string         `json:"label"`
	Size   int64          `json:"size"`
	Free   int64          `json:"free"`
	Marker map[string]any `json:"marker,omitempty"`
}

func (d Drive) Title() string {
	name := d.Label
	if name == "" {
		name = d.Path
	}
	return fmt.Sprintf("%s (%s)", name, human(d.Size))
}

func readMarker(drive string) map[string]any {
	data, err := os.ReadFile(filepath.Join(drive, markerDir, markerName))
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(data, &m) != nil || m["format"] != formatName || m["code"] == nil || m["code"] == "" {
		return nil
	}
	return m
}

func clearTransfer(drive string) error {
	var err error
	// FAT sticks reshuffle a big folder's entries while it's being emptied, so one pass can miss some.
	for range 5 {
		if err = os.RemoveAll(filepath.Join(drive, markerDir)); err == nil {
			return nil
		}
	}
	return err
}

func writeFileSync(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	// Without this the rename (and on FAT the file's size) can stay in memory, and a stick pulled out
	// straight away ends up with an empty marker.
	if d, err := os.Open(filepath.Dir(path)); err == nil {
		_ = d.Sync()
		d.Close()
	}
	return nil
}

func writeMarker(drive string, marker map[string]any) error {
	folder := filepath.Join(drive, markerDir)
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return err
	}
	data, _ := json.MarshalIndent(marker, "", "  ")
	if err := writeFileSync(filepath.Join(folder, markerName), data); err != nil {
		return err
	}
	where := "Your files are stored encrypted on this stick"
	if marker["storage"] == "usb" {
		if part, ok := marker["part"].(int); ok {
			if marker["final"] == true {
				where += fmt.Sprintf(" (stick %d of %v)", part, marker["parts"])
			} else {
				where += fmt.Sprintf(" (stick %d; the transfer carries on onto the next stick)", part)
			}
		}
		where += "."
	} else {
		title := "your own storage"
		if r, ok := marker["remote"].(map[string]any); ok && r["title"] != nil {
			title = fmt.Sprint(r["title"])
		}
		where = fmt.Sprintf("Your files are stored encrypted in %s.", title)
	}
	readme := "This USB stick carries files from your old computer to Cherry OS.\r\n\r\n" +
		"Plug it in while installing or using Cherry OS and your files are restored automatically.\r\n\r\n" +
		"Keep it safe: anyone with this stick can get your files back.\r\n" + where + "\r\n"
	return writeFileSync(filepath.Join(folder, "README.txt"), []byte(readme))
}

// spaceOf is the free space on a drive (tests pretend sticks are small).
var spaceOf = freeSpace

// usableSpace is the free space on a stick, counting an earlier transfer's folder (a new transfer replaces it).
func usableSpace(drive string) int64 {
	free := spaceOf(drive)
	_ = filepath.Walk(filepath.Join(drive, markerDir), func(_ string, info os.FileInfo, err error) error {
		if err == nil && info.Mode().IsRegular() {
			free += info.Size()
		}
		return nil
	})
	return free
}

// ---------------------------------------------------------------- where chunks go

type Sink interface {
	Put(index int, blob []byte) error
	Finish(chunks int) error
	Abandon()
}

// StickPrompt asks for the next USB stick (its number, and what was wrong with the last one offered) and
// returns where it is, or an error if the person gave up.
type StickPrompt func(part int, problem string) (string, error)

// UsbSink writes chunks in order, filling one stick after another.
type UsbSink struct {
	mu       sync.Mutex
	waiting  map[int][]byte
	expected int
	drive    string
	first    int
	part     int
	marker   map[string]any
	next     StickPrompt
	folders  map[string]bool
}

func newUsbSink(drive string, marker map[string]any, next StickPrompt) (*UsbSink, error) {
	s := &UsbSink{waiting: map[int][]byte{}, marker: marker, next: next}
	return s, s.begin(drive, 0)
}

func (s *UsbSink) begin(drive string, first int) error {
	if err := clearTransfer(drive); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(drive, markerDir), 0o755); err != nil {
		return err
	}
	s.drive, s.first, s.folders = drive, first, map[string]bool{}
	s.part++
	return nil
}

func (s *UsbSink) Put(index int, blob []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.waiting[index] = blob
	for {
		b, ok := s.waiting[s.expected]
		if !ok {
			return nil
		}
		delete(s.waiting, s.expected)
		if err := s.write(s.expected, b); err != nil {
			return err
		}
		s.expected++
	}
}

func (s *UsbSink) write(index int, blob []byte) error {
	if spaceOf(s.drive) < int64(len(blob))+stickReserve && index > s.first {
		if err := s.nextStick(index); err != nil {
			return err
		}
	}
	path := filepath.Join(s.drive, markerDir, filepath.FromSlash(chunkName(index)))
	if dir := filepath.Dir(path); !s.folders[dir] {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
		s.folders[dir] = true
	}
	if err := writeFileSync(path, blob); err != nil {
		if !isDiskFull(err) || index == s.first {
			return err
		}
		if err := s.nextStick(index); err != nil {
			return err
		}
		return s.write(index, blob)
	}
	return nil
}

func (s *UsbSink) markerFor(final bool, chunks int) map[string]any {
	m := map[string]any{}
	for k, v := range s.marker {
		m[k] = v
	}
	m["part"], m["first"], m["final"] = s.part, s.first, final
	if final {
		m["chunks"], m["parts"] = chunks, s.part
	}
	return m
}

func (s *UsbSink) nextStick(index int) error {
	if err := writeMarker(s.drive, s.markerFor(false, 0)); err != nil {
		return err
	}
	eject(s.drive)
	if s.next == nil {
		return errors.New("the USB stick is full")
	}
	problem := ""
	for {
		drive, err := s.next(s.part+1, problem)
		if err != nil {
			return err
		}
		if m := readMarker(drive); m != nil && m["code"] == s.marker["code"] {
			problem = fmt.Sprintf("That's stick %v again. Plug in another stick.", m["part"])
		} else if usableSpace(drive) < chunkSize*2+stickReserve {
			problem = "That stick is full. Plug in another one, or delete some files from it first."
		} else {
			return s.begin(drive, index)
		}
	}
}

// Finish ejects the last stick too: "All done!" tells people to unplug it, so everything must be on it by then.
func (s *UsbSink) Finish(chunks int) error {
	if err := writeMarker(s.drive, s.markerFor(true, chunks)); err != nil {
		return err
	}
	eject(s.drive)
	return nil
}

func (s *UsbSink) Abandon() {}

// FolderSink stores the move in a folder, such as a NAS share (\\nas\share\folder) on Windows:
// CherryMove/<transfer id>/data/… and info.json once it's complete.
type FolderSink struct {
	base    string
	info    map[string]any
	mu      sync.Mutex
	folders map[string]bool
}

func newFolderSink(folder string, keys *Keys, info map[string]any) (*FolderSink, error) {
	base := filepath.Join(folder, remoteRoot, keys.ID)
	if err := os.MkdirAll(base, 0o755); err != nil {
		return nil, err
	}
	return &FolderSink{base: base, info: info, folders: map[string]bool{}}, nil
}

func (s *FolderSink) Put(index int, blob []byte) error {
	path := filepath.Join(s.base, filepath.FromSlash(chunkName(index)))
	dir := filepath.Dir(path)
	s.mu.Lock()
	if !s.folders[dir] {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			s.mu.Unlock()
			return err
		}
		s.folders[dir] = true
	}
	s.mu.Unlock()
	return retry(func() error { return writeFileSync(path, blob) })
}

// infoJSON marks a complete move. Besides the chunk count it says which move it is (label, computer, size),
// so the new computer can list the moves in a folder before the code is typed. Never the code itself.
func infoJSON(chunks int, info map[string]any) []byte {
	m := map[string]any{"format": formatName, "chunks": chunks, "created": time.Now().Format(time.RFC3339)}
	for _, k := range []string{"label", "hostname", "source_os", "size", "files", "created"} {
		if v, ok := info[k]; ok {
			m[k] = v
		}
	}
	b, _ := json.Marshal(m)
	return b
}

func (s *FolderSink) Finish(chunks int) error {
	return writeFileSync(filepath.Join(s.base, "info.json"), infoJSON(chunks, s.info))
}

func (s *FolderSink) Abandon() { _ = os.RemoveAll(s.base) }

// RemoteSink stores the move in someone's own cloud drive or NAS through rclone (rclone.go).
type RemoteSink struct {
	r       *Remote
	base    string
	info    map[string]any
	mu      sync.Mutex
	folders map[string]bool
}

func (s *RemoteSink) Put(index int, blob []byte) error {
	name := s.base + "/" + chunkName(index)
	folder := name[:strings.LastIndex(name, "/")]
	s.mu.Lock()
	if !s.folders[folder] {
		if err := retry(func() error { return s.r.Mkdirs(folder) }); err != nil {
			s.mu.Unlock()
			return err
		}
		s.folders[folder] = true
	}
	s.mu.Unlock()
	return retry(func() error { return s.r.Put(name, blob) })
}

func (s *RemoteSink) Finish(chunks int) error {
	return retry(func() error { return s.r.Put(s.base+"/info.json", infoJSON(chunks, s.info)) })
}

func (s *RemoteSink) Abandon() { s.r.Delete(s.base) }

func retry(fn func() error) error {
	var err error
	for n := 0; n < 4; n++ {
		if err = fn(); err == nil {
			return nil
		}
		time.Sleep(time.Duration(1<<n) * time.Second)
	}
	return err
}

// ---------------------------------------------------------------- the chunk stream

// chunker is the file tar writes into: it cuts the stream into chunks and compresses, encrypts and stores
// several at once.
type chunker struct {
	keys  *Keys
	sink  Sink
	level Level
	buf   []byte
	index int
	slots chan struct{}
	wg    sync.WaitGroup
	mu    sync.Mutex
	err   error
}

func newChunker(keys *Keys, sink Sink, level Level) *chunker {
	workers := min(max(runtime.NumCPU(), 3), 8)
	if level >= Strong {
		workers = min(workers, 4)
	}
	return &chunker{keys: keys, sink: sink, level: level, index: 1, slots: make(chan struct{}, workers+2),
		buf: make([]byte, 0, chunkSize)}
}

func (c *chunker) failed() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.err
}

func (c *chunker) Write(p []byte) (int, error) {
	if err := c.failed(); err != nil {
		return 0, err
	}
	n := len(p)
	for len(p) > 0 {
		take := min(chunkSize-len(c.buf), len(p))
		c.buf = append(c.buf, p[:take]...)
		p = p[take:]
		if len(c.buf) == chunkSize {
			c.flush()
			if err := c.failed(); err != nil {
				return 0, err
			}
		}
	}
	return n, nil
}

func (c *chunker) flush() {
	plain := c.buf
	c.buf = make([]byte, 0, chunkSize)
	index := c.index
	c.index++
	c.slots <- struct{}{}
	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		defer func() { <-c.slots }()
		if c.failed() != nil {
			return
		}
		if err := c.sink.Put(index, c.keys.Seal(index, pack(plain, c.level))); err != nil {
			c.mu.Lock()
			if c.err == nil {
				c.err = err
			}
			c.mu.Unlock()
		}
	}()
}

// finish stores what's left and returns the number of chunks, the manifest included.
func (c *chunker) finish() (int, error) {
	if len(c.buf) > 0 {
		c.flush()
	}
	c.wg.Wait()
	return c.index, c.failed()
}

func (c *chunker) abandon() {
	c.mu.Lock()
	if c.err == nil {
		c.err = context.Canceled
	}
	c.mu.Unlock()
	c.wg.Wait()
}

// ---------------------------------------------------------------- send

type SendOptions struct {
	Storage    string // "usb", "folder" or "remote"
	Drive      string // usb: the first stick
	Folder     string // folder: where to keep it (a NAS share)
	Remote     *Remote
	Descriptor map[string]any // folder and remote: how the new computer finds it (never a password)
	CarryDrive string         // folder and remote: a stick that just gets the code
	Level      Level
	Label      string
	NextStick  StickPrompt
	Progress   Progress
}

type SendResult struct {
	Code    string         `json:"code"`
	Storage string         `json:"storage"`
	Size    int64          `json:"size"`
	Files   int            `json:"files"`
	Skipped []string       `json:"skipped"`
	Chunks  int            `json:"chunks"`
	Sticks  int            `json:"sticks"`
	Remote  map[string]any `json:"remote,omitempty"`
	Label   string         `json:"label"`
}

func defaultLabel() string {
	host, _ := os.Hostname()
	return fmt.Sprintf("%s — %s", host, time.Now().Format("2 Jan 2006"))
}

func send(ctx context.Context, plan *Plan, opt SendOptions) (*SendResult, error) {
	keys := newKeys()
	if opt.Label == "" {
		opt.Label = defaultLabel()
	}
	if opt.Level == 0 {
		opt.Level = Normal
	}
	host, _ := os.Hostname()
	created := time.Now().Format(time.RFC3339)
	storage := "usb"
	if opt.Storage != "usb" {
		storage = "remote"
	}
	base := map[string]any{
		"format": formatName, "code": keys.Code, "label": opt.Label, "storage": storage, "remote": opt.Descriptor,
		"expires": nil, "created": created, "hostname": host, "source_os": sourceOS(), "size": plan.RawSize(),
		"files": plan.Files,
	}
	var sink Sink
	var err error
	switch opt.Storage {
	case "usb":
		if opt.Drive == "" {
			return nil, errors.New("no USB stick chosen")
		}
		sink, err = newUsbSink(opt.Drive, base, opt.NextStick)
	case "folder":
		sink, err = newFolderSink(opt.Folder, keys, base)
	case "remote":
		sink = &RemoteSink{r: opt.Remote, base: remoteRoot + "/" + keys.ID, info: base, folders: map[string]bool{}}
	default:
		err = fmt.Errorf("unknown storage %q", opt.Storage)
	}
	if err != nil {
		return nil, err
	}
	firsts := make([]string, 0, len(plan.First))
	for arc := range plan.First {
		firsts = append(firsts, arc)
	}
	sort.Strings(firsts)
	catIDs := make([]string, 0, len(plan.Cats))
	for _, c := range plan.Cats {
		catIDs = append(catIDs, c.ID)
	}
	stop := keepAwake()
	defer stop()
	apps := appsInfo(plan)
	var extras []entry
	if info, ok := apps.(map[string]any); ok && slices.Contains(catIDs, "apps") {
		if cache, err := os.UserCacheDir(); err == nil {
			dir := filepath.Join(cache, "cherry-move", "extras")
			defer os.RemoveAll(dir)
			if opt.Progress != nil {
				opt.Progress(0, max(plan.RawSize(), 1), "Your apps, system services and servers")
			}
			info["extras"], extras = gatherExtras(dir)
		}
	}
	var extrasSize int64
	for _, e := range extras {
		extrasSize += e.info.Size()
	}
	manifest, _ := json.Marshal(map[string]any{
		"format": formatName, "label": opt.Label, "created": created, "hostname": host, "user": userName(),
		"home": homeDir, "source_os": sourceOS(), "size": plan.Size, "raw_size": plan.RawSize() + extrasSize,
		"files": plan.Files + len(extras), "categories": catIDs, "apps": apps, "same": firsts,
	})
	ch := newChunker(keys, sink, opt.Level)
	result, err := func() (*SendResult, error) {
		if err := sink.Put(0, keys.Seal(0, pack(manifest, opt.Level))); err != nil {
			return nil, err
		}
		skipped, err := writeTar(ctx, plan, extras, ch, opt.Progress)
		if err != nil {
			return nil, err
		}
		chunks, err := ch.finish()
		if err != nil {
			return nil, err
		}
		if err := sink.Finish(chunks); err != nil {
			return nil, err
		}
		res := &SendResult{Code: keys.Code, Storage: opt.Storage, Size: plan.RawSize(), Files: plan.Files - len(skipped),
			Skipped: skipped, Chunks: chunks - 1, Remote: opt.Descriptor, Label: opt.Label}
		if u, ok := sink.(*UsbSink); ok {
			res.Sticks = u.part
		}
		return res, nil
	}()
	if err != nil {
		ch.abandon()
		sink.Abandon()
		return nil, err
	}
	if opt.Storage != "usb" && opt.CarryDrive != "" {
		if err := clearTransfer(opt.CarryDrive); err != nil {
			return nil, err
		}
		if err := writeMarker(opt.CarryDrive, base); err != nil {
			return nil, err
		}
	}
	return result, nil
}

func tarMode(e entry) int64 {
	if runtime.GOOS != "windows" {
		return int64(e.info.Mode().Perm())
	}
	private := strings.HasPrefix(e.arc, "home/.ssh")
	switch {
	case e.info.IsDir() && private:
		return 0o700
	case e.info.IsDir():
		return 0o755
	case private:
		return 0o600
	}
	return 0o644
}

// incompleteName follows a file padded with zeros (it couldn't be read to the end), as a link to its name:
// Cherry OS leaves that file out. Older versions have nowhere to put it, so they pass over it.
const incompleteName = "cherry/incomplete"

// writeTar packs the plan, then `extras` (regular files, from gatherExtras).
func writeTar(ctx context.Context, plan *Plan, extras []entry, w io.Writer, progress Progress) ([]string, error) {
	tw := tar.NewWriter(w)
	total := max(plan.RawSize(), 1)
	for _, e := range extras {
		total += e.info.Size()
	}
	var done int64
	var skipped []string
	storedOK := map[string]bool{}
	snapshots := map[string]bool{}
	last := time.Time{}
	for _, c := range plan.Cats {
		for _, s := range c.Paths {
			err := walk(s, func(e entry) error {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				hdr := &tar.Header{Name: e.arc, Mode: tarMode(e), ModTime: modTime(e.info), Format: tar.FormatPAX}
				switch {
				case e.info.IsDir():
					hdr.Typeflag = tar.TypeDir
					return tw.WriteHeader(hdr)
				case e.info.Mode()&os.ModeSymlink != 0:
					target, err := os.Readlink(osPath(e.path))
					if err != nil {
						return nil
					}
					hdr.Typeflag, hdr.Linkname = tar.TypeSymlink, target
					return tw.WriteHeader(hdr)
				case !e.info.Mode().IsRegular():
					return nil
				}
				size, mtime := e.info.Size(), e.info.ModTime().UnixNano()
				if stem, ok := sqliteSide(e.path); ok && snapshots[stem] {
					done += size
					return nil
				}
				if liveSQLite(e.path) {
					if snap := snapshotSQLite(e.path, size); snap != "" {
						err := writeSnapshot(tw, hdr, snap)
						os.Remove(snap)
						if err != nil {
							return err
						}
						snapshots[e.path] = true
						storedOK[e.arc] = false
						done += size
						return nil
					}
				}
				if same, ok := plan.Same[e.path]; ok && same.Size == size && same.Mtime == mtime && storedOK[same.FirstArc] {
					hdr.Typeflag, hdr.Linkname = tar.TypeLink, same.FirstArc
					if err := tw.WriteHeader(hdr); err != nil {
						return err
					}
				} else {
					f, err := os.Open(osPath(e.path))
					if err != nil {
						skipped = append(skipped, e.path)
						return nil
					}
					hdr.Typeflag, hdr.Size = tar.TypeReg, size
					if err := tw.WriteHeader(hdr); err != nil {
						f.Close()
						return err
					}
					n, err := io.CopyN(tw, f, size)
					f.Close()
					if err != nil && !errors.Is(err, io.EOF) {
						if c, ok := w.(*chunker); ok && c.failed() != nil {
							return c.failed()
						}
						if ctx.Err() != nil {
							return ctx.Err()
						}
					}
					if n < size { // the file shrank or couldn't be read to the end: keep the stream valid
						if _, err := io.CopyN(tw, zeros{}, size-n); err != nil {
							return err
						}
						if err := tw.WriteHeader(&tar.Header{Name: incompleteName, Typeflag: tar.TypeSymlink,
							Linkname: e.arc, ModTime: time.Unix(0, 0), Format: tar.FormatPAX}); err != nil {
							return err
						}
						skipped = append(skipped, e.path)
					}
					if first, ok := plan.First[e.arc]; ok { // its copies can point at it unless it changed while read
						after, err := os.Lstat(osPath(e.path))
						storedOK[e.arc] = n == size && first == [2]int64{size, mtime} && err == nil &&
							after.Size() == size && after.ModTime().UnixNano() == mtime
					}
				}
				done += size
				if progress != nil && time.Since(last) > 200*time.Millisecond {
					last = time.Now()
					name := e.arc
					if parts := strings.SplitN(e.arc, "/", 3); len(parts) == 3 {
						name = parts[2]
					}
					progress(min(done, total), total, name)
				}
				return nil
			})
			if err != nil {
				return nil, err
			}
		}
	}
	for _, e := range extras {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		f, err := os.Open(e.path)
		if err != nil {
			skipped = append(skipped, e.path)
			continue
		}
		size := e.info.Size()
		hdr := &tar.Header{Name: e.arc, Mode: tarMode(e), ModTime: e.info.ModTime(), Format: tar.FormatPAX,
			Typeflag: tar.TypeReg, Size: size}
		err = tw.WriteHeader(hdr)
		if err == nil {
			_, err = io.CopyN(tw, io.MultiReader(f, zeros{}), size)
		}
		f.Close()
		if err != nil {
			return nil, err
		}
		done += size
		if progress != nil {
			progress(min(done, total), total, filepath.Base(e.path))
		}
	}
	if progress != nil {
		progress(total, total, "")
	}
	return skipped, tw.Close()
}

var sqliteHeader = []byte("SQLite format 3\x00")

// liveSQLite: a SQLite database that may be open right now (it has a write-ahead log or journal beside it).
// Copied file by file it can be caught half-written, so it's copied through SQLite instead.
func liveSQLite(p string) bool {
	if !fileExists(p+"-wal") && !fileExists(p+"-journal") {
		return false
	}
	f, err := os.Open(osPath(p))
	if err != nil {
		return false
	}
	defer f.Close()
	head := make([]byte, len(sqliteHeader))
	_, err = io.ReadFull(f, head)
	return err == nil && bytes.Equal(head, sqliteHeader)
}

func sqliteSide(p string) (string, bool) {
	for _, end := range []string{"-wal", "-shm", "-journal"} {
		if stem, ok := strings.CutSuffix(p, end); ok {
			return stem, true
		}
	}
	return "", false
}

// Python's backup() retries a busy database forever, and Chromium-based browsers lock theirs for as long as
// they run, so the script gives up after 30 seconds of being locked out.
const snapshotScript = `import os, sqlite3, sys, time, urllib.parse
src = sqlite3.connect("file:" + urllib.parse.quote(os.fsencode(sys.argv[1])) + "?mode=ro", uri=True, timeout=3)
src.execute("SELECT count(*) FROM sqlite_master").fetchone()
give_up = time.monotonic() + 30
def patience(status, remaining, total):
    if status in (5, 6) and time.monotonic() > give_up:
        raise TimeoutError("locked")
dst = sqlite3.connect(sys.argv[2])
src.backup(dst, progress=patience)
dst.close()
src.close()`

// snapshotSQLite makes a consistent copy of a live database (its write-ahead log folded in) with the
// computer's Python, and returns its path, or "" if it can't (no Python, no room, locked): then the files
// are packed as they are.
func snapshotSQLite(p string, size int64) string {
	py, err := exec.LookPath("python3")
	if err != nil {
		return ""
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		return ""
	}
	dir := filepath.Join(cache, "cherry-move")
	if os.MkdirAll(dir, 0o700) != nil {
		return ""
	}
	var wal int64
	if fi, err := os.Stat(osPath(p + "-wal")); err == nil {
		wal = fi.Size()
	}
	if freeSpace(dir) < (size+wal)*11/10+512<<20 {
		return ""
	}
	out := filepath.Join(dir, fmt.Sprintf("snapshot-%d.db", os.Getpid()))
	os.Remove(out)
	cmd := hiddenCommand(py, "-c", snapshotScript, p, out)
	if cmd.Start() != nil {
		return ""
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err = <-done:
	case <-time.After(2*time.Minute + time.Duration((size+wal)/(10<<20))*time.Second):
		_ = cmd.Process.Kill()
		<-done
		err = errors.New("snapshot timed out")
	}
	if err != nil {
		os.Remove(out)
		return ""
	}
	return out
}

func writeSnapshot(tw *tar.Writer, hdr *tar.Header, snap string) error {
	f, err := os.Open(snap)
	if err != nil {
		return err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return err
	}
	hdr.Typeflag, hdr.Size = tar.TypeReg, fi.Size()
	if err := tw.WriteHeader(hdr); err != nil {
		return err
	}
	_, err = io.CopyN(tw, io.MultiReader(f, zeros{}), fi.Size())
	return err
}

type zeros struct{}

func (zeros) Read(p []byte) (int, error) {
	clear(p)
	return len(p), nil
}

//go:build !windows

package main

import (
	"bufio"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

func osPath(p string) string { return p }

func modTime(info os.FileInfo) time.Time { return info.ModTime() }

func skipEntry(path string, info os.FileInfo) bool {
	return info.Mode()&(os.ModeDevice|os.ModeNamedPipe|os.ModeSocket|os.ModeIrregular) != 0
}

func xdgDir(name, fallback, home string) string {
	if out, err := exec.Command("xdg-user-dir", name).Output(); err == nil {
		p := strings.TrimSpace(string(out))
		if p != "" && filepath.Clean(p) != filepath.Clean(home) {
			return p
		}
	}
	return filepath.Join(home, fallback)
}

// documentsDir is where the save box opens.
func documentsDir() string {
	home, _ := os.UserHomeDir()
	if d := xdgDir("DOCUMENTS", "Documents", home); isDir(d) {
		return d
	}
	return home
}

func isDir(p string) bool {
	fi, err := os.Stat(p)
	return err == nil && fi.IsDir()
}

func categories() []*Category {
	home, _ := os.UserHomeDir()
	known := []struct{ id, label, xdg string }{
		{"documents", "Documents", "DOCUMENTS"}, {"pictures", "Pictures", "PICTURES"}, {"music", "Music", "MUSIC"},
		{"videos", "Videos", "VIDEOS"}, {"desktop", "Desktop", "DESKTOP"}, {"downloads", "Downloads", "DOWNLOAD"},
	}
	if runtime.GOOS == "darwin" {
		known[3].label = "Movies"
	}
	used := map[string]bool{}
	var cats []*Category
	for _, k := range known {
		p := xdgDir(k.xdg, k.label, home)
		used[p] = true
		c := &Category{ID: k.id, Label: k.label, Default: true}
		if _, err := os.Stat(p); err == nil {
			c.Paths = []Src{{Prefix: "xdg/" + k.xdg, Path: p}}
		}
		cats = append(cats, c)
	}
	browsers := &Category{ID: "browsers", Label: "Browser bookmarks, passwords and history", Default: true,
		Note: "Close your web browsers first."}
	var browserPaths [][2]string
	if runtime.GOOS == "darwin" {
		lib := filepath.Join(home, "Library", "Application Support")
		browserPaths = [][2]string{
			{".mozilla/firefox", filepath.Join(lib, "Firefox")},
			{".config/google-chrome", filepath.Join(lib, "Google", "Chrome")},
			{".config/microsoft-edge", filepath.Join(lib, "Microsoft Edge")},
			{".config/BraveSoftware/Brave-Browser", filepath.Join(lib, "BraveSoftware", "Brave-Browser")},
		}
	} else {
		// Classic, Firefox 147+ on a fresh install, Ubuntu's Snap, Flatpak.
		for _, rel := range []string{".mozilla/firefox", ".config/mozilla/firefox", "snap/firefox/common/.mozilla/firefox",
			".var/app/org.mozilla.firefox/.mozilla/firefox", ".var/app/org.mozilla.firefox/config/mozilla/firefox"} {
			if _, err := os.Stat(filepath.Join(home, rel, "profiles.ini")); err == nil {
				browserPaths = append(browserPaths, [2]string{".mozilla/firefox", filepath.Join(home, rel)})
				break
			}
		}
		if _, err := os.Stat(filepath.Join(home, ".config", "chromium")); err != nil {
			browserPaths = append(browserPaths, [2]string{".config/chromium", filepath.Join(home, "snap", "chromium", "common", "chromium")})
		}
		for _, rel := range []string{".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
			".config/microsoft-edge", ".config/vivaldi", ".var/app/org.mozilla.firefox", ".var/app/com.google.Chrome",
			".var/app/org.chromium.Chromium", ".var/app/com.brave.Browser"} {
			browserPaths = append(browserPaths, [2]string{rel, filepath.Join(home, rel)})
		}
	}
	for _, b := range browserPaths {
		if _, err := os.Stat(b[1]); err == nil {
			var only []string
			if runtime.GOOS == "darwin" && strings.HasPrefix(b[0], ".config/") {
				only = []string{"Local State", "*/Bookmarks", "*/History", "*/Favicons", "*/Preferences", "*/Top Sites", "*/Shortcuts"}
			}
			browsers.Paths = append(browsers.Paths, Src{Prefix: "home/" + b[0], Path: b[1], Only: only})
		}
	}
	cats = append(cats, browsers)
	personal := &Category{ID: "personal", Label: "Keys, fonts and shell settings", Default: true}
	for _, rel := range []string{".ssh", ".gnupg", ".gitconfig", ".local/share/fonts", ".fonts", ".local/share/keyrings",
		".bashrc", ".zshrc", ".bash_aliases", ".profile"} {
		if _, err := os.Lstat(filepath.Join(home, rel)); err == nil {
			personal.Paths = append(personal.Paths, Src{Prefix: "home/" + rel, Path: filepath.Join(home, rel)})
		}
	}
	if runtime.GOOS == "darwin" {
		if _, err := os.Stat(filepath.Join(home, "Library", "Fonts")); err == nil {
			personal.Paths = append(personal.Paths, Src{Prefix: "home/.local/share/fonts", Path: filepath.Join(home, "Library", "Fonts")})
		}
	}
	cats = append(cats, personal)
	// Brought by the categories above, so not again with app settings or other files.
	taken := map[string]bool{}
	for p := range used {
		taken[p] = true
	}
	for _, c := range []*Category{browsers, personal} {
		for _, s := range c.Paths {
			taken[s.Path] = true
		}
	}
	// On Linux everything else in the home folder comes too: hidden folders are where apps keep their
	// settings, sign-ins, game saves and servers. A Mac's are in ~/Library, mostly tied to macOS.
	linux := runtime.GOOS == "linux"
	apps := &Category{ID: "apps", Label: "App settings, sign-ins, game saves and servers", Default: true,
		Note: "Close your apps first."}
	other := &Category{ID: "other", Label: "Other files and folders in your home folder", Default: true}
	skip := map[string]bool{"Library": true, "Applications": true, "Public": true}
	if items, err := os.ReadDir(home); err == nil {
		for _, it := range items {
			name := it.Name()
			full := filepath.Join(home, name)
			if taken[full] || (skipNames[name] && (name != "node_modules" || rebuiltNode(home))) || leftOnPurpose(full) {
				continue
			}
			if strings.HasPrefix(name, ".") || name == "snap" {
				if linux {
					apps.Paths = append(apps.Paths, Src{Prefix: "home/" + name, Path: full, Skip: taken})
				}
				continue
			}
			if (!linux && skip[name]) || (!linux && !it.IsDir()) {
				continue
			}
			other.Paths = append(other.Paths, Src{Prefix: "home/" + name, Path: full, Skip: taken})
		}
	}
	cats = append(cats, apps, other)
	var out []*Category
	for _, c := range cats {
		if len(c.Paths) > 0 {
			out = append(out, c)
		}
	}
	return out
}

func sourceOS() string {
	if runtime.GOOS == "darwin" {
		name, _ := exec.Command("sw_vers", "-productName").Output()
		ver, _ := exec.Command("sw_vers", "-productVersion").Output()
		if s := strings.TrimSpace(strings.TrimSpace(string(name)) + " " + strings.TrimSpace(string(ver))); s != "" {
			return s
		}
		return "macOS"
	}
	f, err := os.Open("/etc/os-release")
	if err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			if v, ok := strings.CutPrefix(sc.Text(), "PRETTY_NAME="); ok {
				return strings.Trim(v, `"`)
			}
		}
	}
	return "Linux"
}

func installedApps() map[string][]string {
	if runtime.GOOS == "darwin" {
		var apps []string
		items, _ := os.ReadDir("/Applications")
		for _, it := range items {
			if n, ok := strings.CutSuffix(it.Name(), ".app"); ok {
				apps = append(apps, n)
			}
		}
		return map[string][]string{"mac": apps}
	}
	apps := map[string][]string{}
	if out, err := exec.Command("flatpak", "list", "--app", "--columns=application").Output(); err == nil {
		apps["flatpak"] = strings.Fields(string(out))
	}
	if out, err := exec.Command("apt-mark", "showmanual").Output(); err == nil {
		apps["apt"] = strings.Fields(string(out))
	}
	if out, err := exec.Command("snap", "list").Output(); err == nil {
		for i, line := range strings.Split(string(out), "\n") {
			if f := strings.Fields(line); i > 0 && len(f) > 0 {
				apps["snap"] = append(apps["snap"], f[0])
			}
		}
	}
	return apps
}

// appsInfo is the manifest's "apps": installed apps, plus on Linux everything left behind on purpose that the
// new computer downloads or rebuilds again (as reinstall_list in transfer.py).
func appsInfo(p *Plan) any {
	base := installedApps()
	if runtime.GOOS != "linux" {
		return base
	}
	info := map[string]any{}
	for k, v := range base {
		info[k] = v
	}
	user := userName()
	info["home"] = homeDir
	info["apt_sources"] = aptSources()
	info["ollama"] = ollamaModels()
	info["rustup"] = dirNames(filepath.Join(homeDir, ".rustup", "toolchains"))
	envs := []map[string]any{}
	for _, e := range p.Envs {
		envs = append(envs, pythonEnv(e))
	}
	info["venvs"] = envs
	node := append([]string{}, p.Node...)
	if fileExists(filepath.Join(homeDir, "package.json")) && isDir(filepath.Join(homeDir, "node_modules")) {
		node = append(node, homeDir) // npm install run in the home folder itself (its node_modules isn't walked)
	}
	sort.Strings(node)
	info["node"] = node
	var arduino []string
	if found, _ := filepath.Glob(filepath.Join(homeDir, ".arduino15", "packages", "*", "hardware", "*", "*")); found != nil {
		for _, f := range found {
			parts := strings.Split(f, string(os.PathSeparator))
			n := len(parts)
			arduino = append(arduino, fmt.Sprintf("%s:%s@%s", parts[n-4], parts[n-2], parts[n-1]))
		}
	}
	info["arduino"] = arduino
	info["platformio"] = dirNames(filepath.Join(homeDir, ".platformio", "platforms"))
	_, err := os.Stat("/var/lib/systemd/linger/" + user)
	info["linger"] = err == nil
	var groups []string
	if data, err := os.ReadFile("/etc/group"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			f := strings.Split(line, ":")
			if len(f) == 4 && slices.Contains(strings.Split(f[3], ","), user) {
				groups = append(groups, f[0])
			}
		}
	}
	sort.Strings(groups)
	info["groups"] = groups
	return info
}

func dirNames(dir string) []string {
	var out []string
	items, _ := os.ReadDir(dir)
	for _, it := range items {
		if it.IsDir() {
			out = append(out, it.Name())
		}
	}
	return out
}

var (
	distroSources = regexp.MustCompile(`(?i)(^|[./])(ubuntu\.com|debian\.org|launchpad(content)?\.net|linuxmint\.com|` +
		`pop-os\.org|elementary\.io|kde\.org/neon|cherry-os)(/|:|$)`)
	sourceURI = regexp.MustCompile(`(?:https?|file|ftp)://\S+`)
	signedBy  = regexp.MustCompile(`(?i)signed-by[=:]\s*(/[^\s\],]+)`)
)

// aptSources lists package sources added for apps (Cursor, Node.js, Tailscale…) with their signing keys.
func aptSources() []map[string]any {
	out := []map[string]any{}
	items, _ := os.ReadDir("/etc/apt/sources.list.d")
	for _, it := range items {
		ext := filepath.Ext(it.Name())
		if it.IsDir() || (ext != ".list" && ext != ".sources") {
			continue
		}
		data, err := os.ReadFile(filepath.Join("/etc/apt/sources.list.d", it.Name()))
		if err != nil {
			continue
		}
		var active []string
		for _, line := range strings.Split(string(data), "\n") {
			if t := strings.TrimSpace(line); t != "" && !strings.HasPrefix(t, "#") {
				active = append(active, line)
			}
		}
		uris := sourceURI.FindAllString(strings.Join(active, "\n"), -1)
		distro := len(uris) == 0
		for _, u := range uris {
			distro = distro || distroSources.MatchString(u)
		}
		if distro {
			continue
		}
		keys := map[string]string{}
		for _, m := range signedBy.FindAllStringSubmatch(string(data), -1) {
			k := m[1]
			if strings.HasPrefix(k, "/etc/apt/keyrings/") || strings.HasPrefix(k, "/usr/share/keyrings/") ||
				strings.HasPrefix(k, "/etc/apt/trusted.gpg.d/") {
				if b, err := os.ReadFile(k); err == nil {
					keys[k] = base64.StdEncoding.EncodeToString(b)
				}
			}
		}
		out = append(out, map[string]any{"name": it.Name(), "text": string(data), "keys": keys})
	}
	return out
}

// ollamaModels names the Ollama models downloaded here, from Ollama's own list on disk.
func ollamaModels() []string {
	root := filepath.Join(homeDir, ".ollama", "models", "manifests")
	found, _ := filepath.Glob(filepath.Join(root, "*", "*", "*", "*"))
	var names []string
	for _, f := range found {
		if !fileExists(f) {
			continue
		}
		rel, _ := filepath.Rel(root, f)
		p := strings.Split(filepath.ToSlash(rel), "/")
		switch {
		case p[0] == "registry.ollama.ai" && p[1] == "library":
			names = append(names, p[2]+":"+p[3])
		case p[0] == "registry.ollama.ai":
			names = append(names, p[1]+"/"+p[2]+":"+p[3])
		default:
			names = append(names, strings.Join(p[:3], "/")+":"+p[3])
		}
	}
	sort.Strings(names)
	return names
}

// pythonEnv says what's installed in a Python environment: its Python version and pip requirements.
func pythonEnv(dir string) map[string]any {
	env := map[string]any{"path": dir, "python": "", "packages": []string{}}
	if data, err := os.ReadFile(filepath.Join(dir, "pyvenv.cfg")); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			k, v, _ := strings.Cut(line, "=")
			if k = strings.TrimSpace(k); k == "version" || k == "version_info" {
				parts := strings.Split(strings.TrimSpace(v), ".")
				if len(parts) > 2 {
					parts = parts[:2]
				}
				env["python"] = strings.Join(parts, ".")
			}
		}
	}
	var pkgs []string
	found, _ := filepath.Glob(filepath.Join(dir, "lib", "python*", "site-packages", "*.dist-info"))
	sort.Strings(found)
	for _, meta := range found {
		name, version, ok := strings.Cut(strings.TrimSuffix(filepath.Base(meta), ".dist-info"), "-")
		if !ok || strings.EqualFold(name, "pip") {
			continue
		}
		var direct struct {
			URL     string `json:"url"`
			DirInfo struct {
				Editable bool `json:"editable"`
			} `json:"dir_info"`
		}
		if data, err := os.ReadFile(filepath.Join(meta, "direct_url.json")); err == nil &&
			json.Unmarshal(data, &direct) == nil && direct.DirInfo.Editable {
			if u, err := url.Parse(direct.URL); err == nil {
				pkgs = append(pkgs, "-e "+u.Path)
			}
			continue
		}
		pkgs = append(pkgs, name+"=="+version)
	}
	if pkgs != nil {
		env["packages"] = pkgs
	}
	return env
}

func userName() string { return os.Getenv("USER") }

func stateDir() string {
	if runtime.GOOS == "darwin" {
		home, _ := os.UserHomeDir()
		return filepath.Join(home, "Library", "Application Support", "CherryMove")
	}
	if d := os.Getenv("XDG_DATA_HOME"); d != "" {
		return filepath.Join(d, "cherry-move")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "cherry-move")
}

func keepAwake() func() {
	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		cmd = exec.Command("caffeinate", "-i", "-w", strconv.Itoa(os.Getpid()))
	} else if _, err := exec.LookPath("systemd-inhibit"); err == nil {
		// Waiting on this process rather than forever, so a crash doesn't keep the computer awake for good.
		cmd = exec.Command("systemd-inhibit", "--what=sleep:idle", "--who=Cherry Move", "--why=Moving your files",
			"tail", "--pid="+strconv.Itoa(os.Getpid()), "-f", "/dev/null")
	}
	if cmd == nil || cmd.Start() != nil {
		return func() {}
	}
	return func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() }
}

func hiddenCommand(name string, args ...string) *exec.Cmd { return exec.Command(name, args...) }

func openBrowser(url string) { openExternal(url) }

// openExternal opens a web page or file with whatever opens it normally.
func openExternal(target string) {
	if runtime.GOOS == "darwin" {
		_ = exec.Command("open", target).Start()
		return
	}
	_ = exec.Command("xdg-open", target).Start()
}

func mounts() [][2]string {
	var out [][2]string
	if runtime.GOOS == "darwin" {
		vol, err := os.Stat("/Volumes")
		if err != nil {
			return nil
		}
		items, _ := os.ReadDir("/Volumes")
		for _, it := range items {
			p := filepath.Join("/Volumes", it.Name())
			if target, err := filepath.EvalSymlinks(p); err == nil && target == "/" {
				continue
			}
			// While a stick is mounting, its /Volumes entry is an empty folder on the Mac's own disk for a moment.
			if fi, err := os.Stat(p); err != nil || fi.Sys().(*syscall.Stat_t).Dev == vol.Sys().(*syscall.Stat_t).Dev {
				continue
			}
			out = append(out, [2]string{it.Name(), p})
		}
		return out
	}
	f, err := os.Open("/proc/mounts")
	if err != nil {
		return nil
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) < 2 {
			continue
		}
		mp := strings.ReplaceAll(fields[1], `\040`, " ")
		if strings.HasPrefix(mp, "/media/") || strings.HasPrefix(mp, "/run/media/") {
			out = append(out, [2]string{filepath.Base(mp), mp})
		}
	}
	return out
}

type blockDev struct {
	Path       string     `json:"path"`
	UUID       string     `json:"uuid"`
	RM         any        `json:"rm"`
	Hotplug    any        `json:"hotplug"`
	Tran       string     `json:"tran"`
	Mountpoint string     `json:"mountpoint"`
	FSType     string     `json:"fstype"`
	Type       string     `json:"type"`
	Children   []blockDev `json:"children"`
}

var (
	stickMu sync.Mutex
	// Sticks (device and filesystem) already tried or ejected by us: not mounted again until they're unplugged.
	leftAlone = map[string]bool{}
	mountable = map[string]bool{"vfat": true, "exfat": true, "ntfs": true, "ntfs3": true, "ext4": true, "ext3": true,
		"ext2": true, "btrfs": true, "xfs": true}
)

func truthy(v any) bool { s := fmt.Sprint(v); return s == "1" || s == "true" }

// mountSticks mounts USB sticks the desktop didn't mount by itself (Ubuntu's live session, a stick plugged in
// before logging in), the way the file manager does when you click one.
func mountSticks() {
	out, err := exec.Command("lsblk", "-J", "-o", "PATH,UUID,RM,HOTPLUG,TRAN,MOUNTPOINT,FSTYPE,TYPE").Output()
	if err != nil {
		return
	}
	var tree struct {
		Blockdevices []blockDev `json:"blockdevices"`
	}
	if json.Unmarshal(out, &tree) != nil {
		return
	}
	stickMu.Lock()
	defer stickMu.Unlock()
	present := map[string]bool{}
	var visit func(d blockDev, removable bool)
	visit = func(d blockDev, removable bool) {
		key := d.Path + " " + d.UUID
		present[key] = true
		rem := (removable || truthy(d.RM) || truthy(d.Hotplug) || d.Tran == "usb") &&
			d.Type != "loop" && d.Tran != "nvme" && d.Tran != "sata"
		for _, c := range d.Children {
			visit(c, rem)
		}
		if rem && d.Mountpoint == "" && mountable[d.FSType] && !leftAlone[key] {
			leftAlone[key] = true
			_ = exec.Command("udisksctl", "mount", "-b", d.Path, "--no-user-interaction").Run()
		}
	}
	for _, d := range tree.Blockdevices {
		visit(d, false)
	}
	for key := range leftAlone {
		if !present[key] {
			delete(leftAlone, key)
		}
	}
}

func removableDrives() []Drive {
	if runtime.GOOS == "linux" {
		mountSticks()
	}
	var drives []Drive
	for _, m := range mounts() {
		var st syscall.Statfs_t
		// Read-only (an opened installer .dmg, a Cherry OS stick) or a network share: never somewhere to pack to.
		if syscall.Statfs(m[1], &st) != nil || uint64(st.Flags)&1 != 0 || networkVolume(&st) {
			continue
		}
		drives = append(drives, Drive{Path: m[1], Label: m[0], Size: int64(st.Blocks) * int64(st.Bsize),
			Free: int64(st.Bavail) * int64(st.Bsize), Marker: readMarker(m[1])})
	}
	return drives
}

func freeSpace(path string) int64 {
	var st syscall.Statfs_t
	if syscall.Statfs(path, &st) != nil {
		return 0
	}
	return int64(st.Bavail) * int64(st.Bsize)
}

func eject(mount string) {
	if runtime.GOOS == "darwin" {
		if strings.HasPrefix(mount, "/Volumes/") {
			_ = exec.Command("diskutil", "eject", mount).Run()
		}
		return
	}
	if !strings.HasPrefix(mount, "/media/") && !strings.HasPrefix(mount, "/run/media/") {
		return
	}
	syscall.Sync()
	if out, err := exec.Command("findmnt", "-n", "-o", "SOURCE,UUID", mount).Output(); err == nil {
		if f := strings.Fields(string(out)); len(f) > 0 {
			stickMu.Lock()
			leftAlone[f[0]+" "+strings.Join(f[1:], "")] = true
			stickMu.Unlock()
			_ = exec.Command("udisksctl", "unmount", "-b", f[0], "--no-user-interaction").Run()
		}
	}
}

func isDiskFull(err error) bool { return errors.Is(err, syscall.ENOSPC) }

func networkFolders() []string { return nil }

func uncFor(p string) string { return "" }

func nativeSMB() bool { return false }

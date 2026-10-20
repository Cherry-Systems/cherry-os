//go:build windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const (
	attrHidden             = 0x2
	attrSystem             = 0x4
	attrOffline            = 0x1000
	attrRecallOnOpen       = 0x40000
	attrRecallOnDataAccess = 0x400000
)

func knownFolder(id *windows.KNOWNFOLDERID) string {
	p, err := windows.KnownFolderPath(id, windows.KF_FLAG_DEFAULT)
	if err != nil {
		return ""
	}
	return p
}

// documentsDir is where the save box opens.
func documentsDir() string {
	if d := knownFolder(windows.FOLDERID_Documents); d != "" {
		return d
	}
	return os.Getenv("USERPROFILE")
}

// osPath is how to ask Windows for a path. Windows drops dots and spaces from the end of every name in a path
// unless the path starts with \\?\, so a file or folder whose name ends in one (made by WSL, Cygwin, Git or a
// Mac) can only be found that way.
func osPath(p string) string {
	odd := false
	for _, part := range strings.Split(p, `\`) {
		if n := len(part); n > 0 && part != "." && part != ".." && (part[n-1] == '.' || part[n-1] == ' ') {
			odd = true
			break
		}
	}
	switch {
	case !odd || strings.HasPrefix(p, `\\?\`):
		return p
	case strings.HasPrefix(p, `\\`):
		return `\\?\UNC\` + p[2:]
	case len(p) >= 3 && p[1] == ':' && p[2] == '\\':
		return `\\?\` + p
	}
	return p
}

// modTime is info.ModTime() for any time Windows has: that counts nanoseconds since 1970 in an int64, so a file
// from before 1678 (Windows' times go back to 1601, where a time that was never set shows) wraps round to the 2200s.
func modTime(info os.FileInfo) time.Time {
	d, ok := info.Sys().(*syscall.Win32FileAttributeData)
	if !ok {
		return info.ModTime()
	}
	ticks := int64(d.LastWriteTime.HighDateTime)<<32 | int64(d.LastWriteTime.LowDateTime) // 100 ns since 1601
	return time.Unix(ticks/10_000_000-11_644_473_600, ticks%10_000_000*100)
}

// skipEntry leaves out links and junctions, hidden system files, and OneDrive files that are only online
// (reading those would download them first).
func skipEntry(path string, info os.FileInfo) bool {
	if info.Mode()&(os.ModeSymlink|os.ModeIrregular|os.ModeDevice|os.ModeNamedPipe|os.ModeSocket) != 0 {
		return true
	}
	if d, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		a := d.FileAttributes
		if a&attrHidden != 0 && a&attrSystem != 0 {
			return true
		}
		if !info.IsDir() && a&(attrOffline|attrRecallOnOpen|attrRecallOnDataAccess) != 0 {
			return true
		}
	}
	return false
}

var chromeFiles = []string{"Local State", "*/Bookmarks", "*/History", "*/Favicons", "*/Preferences", "*/Top Sites", "*/Shortcuts"}

func categories() []*Category {
	home := os.Getenv("USERPROFILE")
	appdata, local := os.Getenv("APPDATA"), os.Getenv("LOCALAPPDATA")
	known := []struct {
		id, label, xdg string
		folder         *windows.KNOWNFOLDERID
	}{
		{"documents", "Documents", "DOCUMENTS", windows.FOLDERID_Documents},
		{"pictures", "Pictures", "PICTURES", windows.FOLDERID_Pictures},
		{"music", "Music", "MUSIC", windows.FOLDERID_Music},
		{"videos", "Videos", "VIDEOS", windows.FOLDERID_Videos},
		{"desktop", "Desktop", "DESKTOP", windows.FOLDERID_Desktop},
		{"downloads", "Downloads", "DOWNLOAD", windows.FOLDERID_Downloads},
	}
	used := map[string]bool{}
	var cats []*Category
	for _, k := range known {
		p := knownFolder(k.folder)
		c := &Category{ID: k.id, Label: k.label, Default: true}
		if p != "" && !strings.EqualFold(filepath.Clean(p), filepath.Clean(home)) {
			used[strings.ToLower(filepath.Clean(p))] = true
			c.Paths = []Src{{Prefix: "xdg/" + k.xdg, Path: p}}
		}
		cats = append(cats, c)
	}

	browsers := &Category{ID: "browsers", Label: "Browser bookmarks and history", Default: true,
		Note: "Firefox passwords come too. Close your web browsers first."}
	add := func(prefix, path string, only []string) {
		if _, err := os.Stat(path); err == nil {
			browsers.Paths = append(browsers.Paths, Src{Prefix: prefix, Path: path, Only: only})
		}
	}
	add("home/.mozilla/firefox", filepath.Join(appdata, "Mozilla", "Firefox"), nil)
	add("home/.config/google-chrome", filepath.Join(local, "Google", "Chrome", "User Data"), chromeFiles)
	add("home/.config/microsoft-edge", filepath.Join(local, "Microsoft", "Edge", "User Data"), chromeFiles)
	add("home/.config/chromium", filepath.Join(local, "Chromium", "User Data"), chromeFiles)
	add("home/.config/BraveSoftware/Brave-Browser", filepath.Join(local, "BraveSoftware", "Brave-Browser", "User Data"), chromeFiles)
	add("home/.config/vivaldi", filepath.Join(local, "Vivaldi", "User Data"), chromeFiles)
	cats = append(cats, browsers)

	personal := &Category{ID: "personal", Label: "SSH keys, Git settings and fonts you installed", Default: true}
	for _, p := range []Src{
		{Prefix: "home/.ssh", Path: filepath.Join(home, ".ssh")},
		{Prefix: "home/.gitconfig", Path: filepath.Join(home, ".gitconfig")},
		{Prefix: "home/.local/share/fonts", Path: filepath.Join(local, "Microsoft", "Windows", "Fonts")},
	} {
		if _, err := os.Stat(p.Path); err == nil {
			personal.Paths = append(personal.Paths, p)
		}
	}
	cats = append(cats, personal)

	onedrive := &Category{ID: "onedrive", Label: "Your OneDrive folder",
		Note: "Only the files already on this PC. They're in OneDrive online too."}
	other := &Category{ID: "other", Label: "Other folders in your user folder", Default: true}
	skip := map[string]bool{"appdata": true, "3d objects": true, "contacts": true, "favorites": true, "links": true,
		"searches": true, "intelgraphicsprofiles": true, "microsoftedgebackups": true}
	if items, err := os.ReadDir(home); err == nil {
		for _, it := range items {
			full := filepath.Join(home, it.Name())
			info, err := os.Lstat(full)
			lower := strings.ToLower(it.Name())
			if err != nil || !info.IsDir() || strings.HasPrefix(it.Name(), ".") || skip[lower] || used[strings.ToLower(full)] ||
				skipEntry(full, info) {
				continue
			}
			if d, ok := info.Sys().(*syscall.Win32FileAttributeData); ok && d.FileAttributes&attrHidden != 0 {
				continue
			}
			if strings.HasPrefix(lower, "onedrive") {
				onedrive.Paths = append(onedrive.Paths, Src{Prefix: "home/" + it.Name(), Path: full})
				continue
			}
			other.Paths = append(other.Paths, Src{Prefix: "home/" + it.Name(), Path: full})
		}
	}
	cats = append(cats, other, onedrive)
	var out []*Category
	for _, c := range cats {
		if len(c.Paths) > 0 {
			out = append(out, c)
		}
	}
	return out
}

func sourceOS() string {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, `SOFTWARE\Microsoft\Windows NT\CurrentVersion`, registry.QUERY_VALUE)
	if err != nil {
		return "Windows"
	}
	defer k.Close()
	name, _, _ := k.GetStringValue("ProductName")
	build, _, _ := k.GetStringValue("CurrentBuildNumber")
	version, _, _ := k.GetStringValue("DisplayVersion")
	var n int
	fmt.Sscan(build, &n)
	if n >= 22000 {
		name = strings.Replace(name, "Windows 10", "Windows 11", 1)
	}
	if name == "" {
		name = "Windows"
	}
	if version != "" {
		name += " " + version
	}
	return name
}

func installedApps() map[string][]string {
	seen := map[string]bool{}
	for _, root := range []struct {
		key  registry.Key
		path string
	}{
		{registry.LOCAL_MACHINE, `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`},
		{registry.LOCAL_MACHINE, `SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall`},
		{registry.CURRENT_USER, `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`},
	} {
		k, err := registry.OpenKey(root.key, root.path, registry.ENUMERATE_SUB_KEYS)
		if err != nil {
			continue
		}
		names, _ := k.ReadSubKeyNames(-1)
		k.Close()
		for _, n := range names {
			sk, err := registry.OpenKey(root.key, root.path+`\`+n, registry.QUERY_VALUE)
			if err != nil {
				continue
			}
			display, _, _ := sk.GetStringValue("DisplayName")
			system, _, _ := sk.GetIntegerValue("SystemComponent")
			parent, _, _ := sk.GetStringValue("ParentKeyName")
			sk.Close()
			if display != "" && system != 1 && parent == "" {
				seen[display] = true
			}
		}
	}
	apps := make([]string, 0, len(seen))
	for a := range seen {
		apps = append(apps, a)
	}
	sort.Strings(apps)
	return map[string][]string{"windows": apps}
}

func appsInfo(*Plan) any { return installedApps() }

func userName() string { return os.Getenv("USERNAME") }

func stateDir() string { return filepath.Join(os.Getenv("LOCALAPPDATA"), "CherryMove") }

var (
	kernel32         = windows.NewLazySystemDLL("kernel32.dll")
	setExecState     = kernel32.NewProc("SetThreadExecutionState")
	mpr              = windows.NewLazySystemDLL("mpr.dll")
	wnetGetConnectio = mpr.NewProc("WNetGetConnectionW")
)

// keepAwake stops the PC going to sleep until the returned function is called.
func keepAwake() func() {
	stop := make(chan struct{})
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		const esContinuous, esSystemRequired = 0x80000000, 0x1
		setExecState.Call(esContinuous | esSystemRequired)
		<-stop
		setExecState.Call(esContinuous)
	}()
	return func() { close(stop) }
}

func hiddenCommand(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

// workArea is the screen minus the taskbar, in the same units Edge's --window-size uses.
func workArea() (int, int) {
	var r struct{ Left, Top, Right, Bottom int32 }
	const spiGetWorkArea = 0x0030
	ok, _, _ := windows.NewLazySystemDLL("user32.dll").NewProc("SystemParametersInfoW").Call(spiGetWorkArea, 0, uintptr(unsafe.Pointer(&r)), 0)
	if ok == 0 || r.Right <= r.Left {
		return 1280, 720
	}
	return int(r.Right - r.Left), int(r.Bottom - r.Top)
}

func openBrowser(url string) {
	w, h := workArea()
	size := fmt.Sprintf("--window-size=%d,%d", min(980, w-40), min(860, h-40))
	// Its own profile, so the window doesn't hold the user's Edge profile open while it's being copied.
	profile := "--user-data-dir=" + filepath.Join(stateDir(), "window")
	for _, base := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles"), os.Getenv("LOCALAPPDATA")} {
		edge := filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe")
		if _, err := os.Stat(edge); err == nil && base != "" {
			if exec.Command(edge, "--app="+url, size, profile, "--no-first-run", "--no-default-browser-check").Start() == nil {
				return
			}
		}
	}
	openExternal(url)
}

// openExternal opens a web page or file with whatever opens it normally.
func openExternal(target string) {
	_ = hiddenCommand("rundll32", "url.dll,FileProtocolHandler", target).Start()
}

func driveRoots() []string {
	mask, _ := windows.GetLogicalDrives()
	var roots []string
	for i := 0; i < 26; i++ {
		if mask&(1<<i) != 0 {
			roots = append(roots, string(rune('A'+i))+`:\`)
		}
	}
	return roots
}

func busIsUSB(root string) bool {
	h, err := windows.CreateFile(windows.StringToUTF16Ptr(`\\.\`+strings.TrimSuffix(root, `\`)), 0,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	query := [12]byte{} // STORAGE_PROPERTY_QUERY: StorageDeviceProperty, PropertyStandardQuery
	out := make([]byte, 1024)
	var n uint32
	const ioctlStorageQueryProperty = 0x2D1400
	if err := windows.DeviceIoControl(h, ioctlStorageQueryProperty, &query[0], uint32(len(query)), &out[0],
		uint32(len(out)), &n, nil); err != nil || n < 32 {
		return false
	}
	busType := *(*uint32)(unsafe.Pointer(&out[28]))
	return busType == 7 // BusTypeUsb
}

func volumeInfo(root string) (label string, size, free int64) {
	buf := make([]uint16, 256)
	_ = windows.GetVolumeInformation(windows.StringToUTF16Ptr(root), &buf[0], uint32(len(buf)), nil, nil, nil, nil, 0)
	label = windows.UTF16ToString(buf)
	var avail, total, totalFree uint64
	if windows.GetDiskFreeSpaceEx(windows.StringToUTF16Ptr(root), &avail, &total, &totalFree) == nil {
		size, free = int64(total), int64(avail)
	}
	return
}

// removableDrives lists USB sticks and USB disks (never the drive Windows is on).
func removableDrives() []Drive {
	system := strings.ToUpper(filepath.VolumeName(os.Getenv("SystemRoot"))) + `\`
	var drives []Drive
	for _, root := range driveRoots() {
		t := windows.GetDriveType(windows.StringToUTF16Ptr(root))
		if root == system || !(t == windows.DRIVE_REMOVABLE || (t == windows.DRIVE_FIXED && busIsUSB(root))) {
			continue
		}
		label, size, free := volumeInfo(root)
		if size == 0 {
			continue // card reader with no card
		}
		drives = append(drives, Drive{Path: root, Label: label, Size: size, Free: free, Marker: readMarker(root)})
	}
	return drives
}

func freeSpace(path string) int64 {
	var avail, total, totalFree uint64
	if windows.GetDiskFreeSpaceEx(windows.StringToUTF16Ptr(path), &avail, &total, &totalFree) != nil {
		return 0
	}
	return int64(avail)
}

func eject(root string) {
	system := strings.ToUpper(filepath.VolumeName(os.Getenv("SystemRoot")))
	if drive := strings.TrimRight(root, `\`); len(drive) != 2 || drive[1] != ':' || strings.ToUpper(drive) == system {
		return
	}
	_ = hiddenCommand("powershell", "-NoProfile", "-Command",
		fmt.Sprintf("(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s').InvokeVerb('Eject')", root[:2])).Run()
}

func isDiskFull(err error) bool {
	var errno syscall.Errno
	if pe, ok := err.(*os.PathError); ok {
		err = pe.Err
	}
	if e, ok := err.(syscall.Errno); ok {
		errno = e
	}
	return errno == 112 || errno == 39 // ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL
}

// networkFolders lists mapped network drives as UNC paths (\\nas\share), to suggest for "a NAS".
func networkFolders() []string {
	var out []string
	for _, root := range driveRoots() {
		if windows.GetDriveType(windows.StringToUTF16Ptr(root)) == windows.DRIVE_REMOTE {
			if unc := uncFor(root); unc != "" {
				out = append(out, unc)
			}
		}
	}
	return out
}

// uncFor turns a path on a mapped drive (Z:\Backups) into \\server\share\Backups.
func uncFor(p string) string {
	if strings.HasPrefix(p, `\\`) {
		return p
	}
	if len(p) < 2 || p[1] != ':' {
		return ""
	}
	buf := make([]uint16, 1024)
	n := uint32(len(buf))
	r, _, _ := wnetGetConnectio.Call(uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(p[:2]))),
		uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&n)))
	if r != 0 {
		return ""
	}
	return windows.UTF16ToString(buf) + strings.TrimSuffix(p[2:], `\`)
}

func nativeSMB() bool { return true }

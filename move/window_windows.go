//go:build windows

package main

// The window on Windows: WebView2, the Edge web view that comes with Windows 10 and 11.

import (
	"path/filepath"
	"unicode/utf16"
	"unsafe"

	webview2 "github.com/jchv/go-webview2"
	"github.com/jchv/go-webview2/webviewloader"
	"golang.org/x/sys/windows"
)

var (
	user32                  = windows.NewLazySystemDLL("user32.dll")
	procSetWindowLongPtr    = user32.NewProc("SetWindowLongPtrW")
	procCallWindowProc      = user32.NewProc("CallWindowProcW")
	procShowWindow          = user32.NewProc("ShowWindow")
	procIsIconic            = user32.NewProc("IsIconic")
	procSetForegroundWindow = user32.NewProc("SetForegroundWindow")
	procAllowSetForeground  = user32.NewProc("AllowSetForegroundWindow")
	procGetDpiForSystem     = user32.NewProc("GetDpiForSystem")
	procGetSaveFileName     = windows.NewLazySystemDLL("comdlg32.dll").NewProc("GetSaveFileNameW")

	view     webview2.WebView
	viewHwnd uintptr
	origProc uintptr
)

func showWindow(url, title string, width, height int, canClose func() bool, gone func()) bool {
	// Without the WebView2 runtime (some older Windows 10) the library gives up the whole program.
	if v, err := webviewloader.GetInstalledVersion(); err != nil || v == "" {
		return false
	}
	scale := 1.0 // the manifest (winres/winres.json) makes the app DPI-aware
	if procGetDpiForSystem.Find() == nil {
		if dpi, _, _ := procGetDpiForSystem.Call(); dpi > 0 {
			scale = float64(dpi) / 96
		}
	}
	areaW, areaH := workArea()
	w, h := min(int(float64(width)*scale), areaW-40), min(int(float64(height)*scale), areaH-40)
	view = webview2.NewWithOptions(webview2.WebViewOptions{
		DataPath:  filepath.Join(stateDir(), "webview"),
		AutoFocus: true,
		WindowOptions: webview2.WindowOptions{Title: title, Width: uint(w), Height: uint(h), IconId: 1,
			Center: true},
	})
	if view == nil {
		return false
	}
	viewHwnd = uintptr(view.Window())
	// The library closes the window straight away; ask first.
	const gwlpWndProc, wmClose = ^uintptr(3), 0x0010 // GWLP_WNDPROC is -4
	proc := windows.NewCallback(func(hwnd, msg, wp, lp uintptr) uintptr {
		if msg == wmClose && !canClose() {
			return 0
		}
		r, _, _ := procCallWindowProc.Call(origProc, hwnd, msg, wp, lp)
		return r
	})
	origProc, _, _ = procSetWindowLongPtr.Call(viewHwnd, gwlpWndProc, proc)
	view.Navigate(url)
	setWindowOpen(true)
	view.Run()
	setWindowOpen(false)
	gone()
	return true
}

func onUI(f func()) {
	if !hasWindow() {
		f()
		return
	}
	view.Dispatch(f)
}

func evalJS(js string) { view.Eval(js) }

// printPage: WebView2 shows Edge's print preview for the page's print().
func printPage() { evalJS("window.print()") }

func raiseWindow() {
	const swRestore = 9
	if iconic, _, _ := procIsIconic.Call(viewHwnd); iconic != 0 {
		procShowWindow.Call(viewHwnd, swRestore)
	}
	procSetForegroundWindow.Call(viewHwnd)
}

// allowForeground: Windows only lets the app in front hand the front over, so the second copy (just opened by
// the person) passes it to the first.
func allowForeground(pid int) { procAllowSetForeground.Call(uintptr(pid)) }

type openFileName struct {
	StructSize      uint32
	Owner           uintptr
	Instance        uintptr
	Filter          *uint16
	CustomFilter    *uint16
	MaxCustomFilter uint32
	FilterIndex     uint32
	File            *uint16
	MaxFile         uint32
	FileTitle       *uint16
	MaxFileTitle    uint32
	InitialDir      *uint16
	Title           *uint16
	Flags           uint32
	FileOffset      uint16
	FileExtension   uint16
	DefExt          *uint16
	CustData        uintptr
	FnHook          uintptr
	TemplateName    *uint16
	Reserved        uintptr
	Reserved2       uint32
	FlagsEx         uint32
}

func saveDialog(name string) string {
	const ofnOverwritePrompt, ofnNoChangeDir, ofnPathMustExist, ofnExplorer = 0x2, 0x8, 0x800, 0x80000
	file := make([]uint16, 1024)
	copy(file, utf16.Encode([]rune(name)))
	filter := utf16.Encode([]rune("Text file (*.txt)\x00*.txt\x00\x00"))
	title, _ := windows.UTF16PtrFromString("Save your code")
	ext, _ := windows.UTF16PtrFromString("txt")
	dir, _ := windows.UTF16PtrFromString(documentsDir())
	ofn := openFileName{Owner: viewHwnd, Filter: &filter[0], File: &file[0], MaxFile: uint32(len(file)),
		InitialDir: dir, Title: title, DefExt: ext, Flags: ofnOverwritePrompt | ofnNoChangeDir | ofnPathMustExist | ofnExplorer}
	ofn.StructSize = uint32(unsafe.Sizeof(ofn))
	if ok, _, _ := procGetSaveFileName.Call(uintptr(unsafe.Pointer(&ofn))); ok == 0 {
		return ""
	}
	return windows.UTF16ToString(file)
}

package main

// The app's own window: the page served by server.go inside the system's web view (WebView2 on Windows,
// WKWebView on macOS, WebKitGTK on Linux). Where there's none, the page opens in the browser instead.
//
// Each window_*.go provides, for the main thread:
//   showWindow(url, title, width, height, canClose, gone) — opens the window and runs it until it's closed, then
//     calls gone (which may end the program); false if this computer has no web view. canClose is asked when
//     someone closes the window (or quits) and may say no.
//   allowForeground(pid) — lets that process (the app already open) bring its window to the front
//   onUI(f)              — runs f on the window's thread
//   evalJS(js)           — runs JavaScript in the page (call on the window's thread)
//   raiseWindow()        — brings the window to the front (call on the window's thread)
//   saveDialog(name)     — the system's "Save as" box; "" if cancelled (call on the window's thread)

import (
	_ "embed"
	"runtime"
	"sync"
)

//go:embed winres/icon.png
var appIcon []byte

func init() {
	// Windows, macOS and GTK all want their windows on the thread the program started on.
	runtime.LockOSThread()
}

const windowTitle = "Cherry Move"

var (
	windowMu   sync.Mutex
	windowOpen bool
)

func hasWindow() bool {
	windowMu.Lock()
	defer windowMu.Unlock()
	return windowOpen
}

func setWindowOpen(open bool) {
	windowMu.Lock()
	windowOpen = open
	windowMu.Unlock()
}

// onUIWait runs f on the window's thread and waits for it.
func onUIWait(f func()) {
	done := make(chan struct{})
	onUI(func() { defer close(done); f() })
	<-done
}

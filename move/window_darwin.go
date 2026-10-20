//go:build darwin

package main

// The window on macOS: AppKit and WKWebView, called through the Objective-C runtime (no Xcode needed to build).

import (
	"structs"
	"sync"

	"github.com/ebitengine/purego"
	"github.com/ebitengine/purego/objc"
)

type nsPoint struct {
	_    structs.HostLayout
	X, Y float64
}

type nsSize struct {
	_    structs.HostLayout
	W, H float64
}

type nsRect struct {
	_      structs.HostLayout
	Origin nsPoint
	Size   nsSize
}

var (
	macApp, macWindow, macView objc.ID
	macQueue                   struct {
		sync.Mutex
		fns []func()
	}
	dispatchAsync func(queue, ctx, work uintptr)
	mainQueue     uintptr
	drainQueue    uintptr
)

func sel(name string) objc.SEL  { return objc.RegisterName(name) }
func class(name string) objc.ID { return objc.ID(objc.GetClass(name)) }
func nsString(s string) objc.ID {
	return class("NSString").Send(sel("stringWithUTF8String:"), s)
}

func menuItem(title, action, key string) objc.ID {
	return class("NSMenuItem").Send(sel("alloc")).Send(sel("initWithTitle:action:keyEquivalent:"),
		nsString(title), sel(action), nsString(key))
}

func submenu(bar objc.ID, title string, items ...objc.ID) {
	holder := class("NSMenuItem").Send(sel("new"))
	menu := class("NSMenu").Send(sel("alloc")).Send(sel("initWithTitle:"), nsString(title))
	for _, it := range items {
		menu.Send(sel("addItem:"), it)
	}
	holder.Send(sel("setSubmenu:"), menu)
	bar.Send(sel("addItem:"), holder)
}

func showWindow(url, title string, width, height int, canClose func() bool, gone func()) bool {
	for _, fw := range []string{"/System/Library/Frameworks/Cocoa.framework/Cocoa",
		"/System/Library/Frameworks/WebKit.framework/WebKit"} {
		if _, err := purego.Dlopen(fw, purego.RTLD_GLOBAL|purego.RTLD_NOW); err != nil {
			return false
		}
	}
	libSystem, err := purego.Dlopen("/usr/lib/libSystem.B.dylib", purego.RTLD_GLOBAL|purego.RTLD_NOW)
	if err != nil {
		return false
	}
	if mainQueue, err = purego.Dlsym(libSystem, "_dispatch_main_q"); err != nil {
		return false
	}
	purego.RegisterLibFunc(&dispatchAsync, libSystem, "dispatch_async_f")
	drainQueue = purego.NewCallback(func(ctx uintptr) {
		macQueue.Lock()
		fns := macQueue.fns
		macQueue.fns = nil
		macQueue.Unlock()
		for _, f := range fns {
			f()
		}
	})

	delegate, err := objc.RegisterClass("CherryMoveDelegate", objc.GetClass("NSObject"), nil, nil, []objc.MethodDef{
		{Cmd: sel("applicationShouldTerminateAfterLastWindowClosed:"),
			Fn: func(self objc.ID, _ objc.SEL, app objc.ID) bool { return true }},
		{Cmd: sel("applicationShouldTerminate:"), Fn: func(self objc.ID, _ objc.SEL, app objc.ID) uint {
			if hasWindow() && !canClose() {
				return 0 // NSTerminateCancel
			}
			return 1 // NSTerminateNow
		}},
		{Cmd: sel("applicationWillTerminate:"), Fn: func(self objc.ID, _ objc.SEL, note objc.ID) {
			setWindowOpen(false)
			gone() // AppKit ends the program straight after this
		}},
		{Cmd: sel("windowShouldClose:"), Fn: func(self objc.ID, _ objc.SEL, win objc.ID) bool { return canClose() }},
		{Cmd: sel("windowWillClose:"), Fn: func(self objc.ID, _ objc.SEL, note objc.ID) { setWindowOpen(false) }},
	})
	if err != nil {
		return false
	}
	handler := objc.ID(delegate).Send(sel("new"))

	class("NSAutoreleasePool").Send(sel("new"))
	macApp = class("NSApplication").Send(sel("sharedApplication"))
	macApp.Send(sel("setActivationPolicy:"), 0) // a normal app: Dock icon and menu bar
	macApp.Send(sel("setDelegate:"), handler)

	// Without an Edit menu, ⌘C and ⌘V do nothing in the page's text boxes.
	bar := class("NSMenu").Send(sel("new"))
	submenu(bar, title, menuItem("Hide "+title, "hide:", "h"), class("NSMenuItem").Send(sel("separatorItem")),
		menuItem("Quit "+title, "terminate:", "q"))
	submenu(bar, "Edit", menuItem("Undo", "undo:", "z"), menuItem("Redo", "redo:", "Z"),
		class("NSMenuItem").Send(sel("separatorItem")), menuItem("Cut", "cut:", "x"), menuItem("Copy", "copy:", "c"),
		menuItem("Paste", "paste:", "v"), menuItem("Select All", "selectAll:", "a"))
	submenu(bar, "Window", menuItem("Minimize", "performMiniaturize:", "m"), menuItem("Close", "performClose:", "w"))
	macApp.Send(sel("setMainMenu:"), bar)

	const titled, closable, miniaturizable, resizable = 1, 2, 4, 8
	frame := nsRect{Size: nsSize{W: float64(width), H: float64(height)}}
	macWindow = class("NSWindow").Send(sel("alloc")).Send(sel("initWithContentRect:styleMask:backing:defer:"),
		frame, uint64(titled|closable|miniaturizable|resizable), uint64(2), false)
	macWindow.Send(sel("setReleasedWhenClosed:"), false)
	macWindow.Send(sel("setTitle:"), nsString(title))
	macWindow.Send(sel("setContentMinSize:"), nsSize{W: 560, H: 480})
	macWindow.Send(sel("setDelegate:"), handler)
	macWindow.Send(sel("center"))

	config := class("WKWebViewConfiguration").Send(sel("new"))
	macView = class("WKWebView").Send(sel("alloc")).Send(sel("initWithFrame:configuration:"), frame, config)
	macWindow.Send(sel("setContentView:"), macView)
	req := class("NSURLRequest").Send(sel("requestWithURL:"), class("NSURL").Send(sel("URLWithString:"), nsString(url)))
	macView.Send(sel("loadRequest:"), req)

	setWindowOpen(true)
	macWindow.Send(sel("makeKeyAndOrderFront:"), objc.ID(0))
	macApp.Send(sel("activateIgnoringOtherApps:"), true)
	macApp.Send(sel("run"))
	return true
}

func onUI(f func()) {
	if !hasWindow() {
		f()
		return
	}
	macQueue.Lock()
	macQueue.fns = append(macQueue.fns, f)
	macQueue.Unlock()
	dispatchAsync(mainQueue, 0, drainQueue)
}

func evalJS(js string) {
	macView.Send(sel("evaluateJavaScript:completionHandler:"), nsString(js), objc.ID(0))
}

func raiseWindow() {
	if objc.Send[bool](macWindow, sel("isMiniaturized")) {
		macWindow.Send(sel("deminiaturize:"), objc.ID(0))
	}
	macWindow.Send(sel("makeKeyAndOrderFront:"), objc.ID(0))
	macApp.Send(sel("activateIgnoringOtherApps:"), true)
}

// printPage: WKWebView ignores the page's print(), so AppKit prints it.
func printPage() {
	op := macView.Send(sel("printOperationWithPrintInfo:"), class("NSPrintInfo").Send(sel("sharedPrintInfo")))
	// Without a size for its view, WKWebView prints blank pages.
	op.Send(sel("view")).Send(sel("setFrame:"), objc.Send[nsRect](macView, sel("bounds")))
	op.Send(sel("runOperationModalForWindow:delegate:didRunSelector:contextInfo:"), macWindow, objc.ID(0),
		objc.SEL(0), uintptr(0))
}

func saveDialog(name string) string {
	panel := class("NSSavePanel").Send(sel("savePanel"))
	panel.Send(sel("setDirectoryURL:"), class("NSURL").Send(sel("fileURLWithPath:"), nsString(documentsDir())))
	panel.Send(sel("setNameFieldStringValue:"), nsString(name))
	if objc.Send[int](panel, sel("runModal")) != 1 { // NSModalResponseOK
		return ""
	}
	return objc.Send[string](panel.Send(sel("URL")).Send(sel("path")), sel("UTF8String"))
}

func allowForeground(pid int) {}

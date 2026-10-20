//go:build linux

package main

// The window on Linux: GTK 3 and WebKitGTK, loaded when the app starts so it still runs (in the browser)
// where they aren't installed.

import (
	"os"
	"path/filepath"
	"sync"

	"github.com/ebitengine/purego"
)

var gtk struct {
	initCheck       func(argc, argv uintptr) int32
	windowNew       func(kind int32) uintptr
	setTitle        func(win uintptr, title string)
	setDefaultSize  func(win uintptr, w, h int32)
	setIconFromFile func(win uintptr, path string, err uintptr) int32
	containerAdd    func(container, widget uintptr)
	showAll         func(widget uintptr)
	present         func(win uintptr)
	main            func()
	mainQuit        func()
	nativeNew       func(title string, parent uintptr, action int32, accept, cancel string) uintptr
	setCurrentName  func(chooser uintptr, name string)
	setFolder       func(chooser uintptr, folder string) int32
	setOverwrite    func(chooser uintptr, on int32)
	nativeRun       func(dialog uintptr) int32
	getFilename     func(chooser uintptr) string
	unref           func(obj uintptr)
	signalConnect   func(obj uintptr, signal string, handler, data, destroy uintptr, flags int32) uint64
	idleAdd         func(fn, data uintptr) uint32
	setPrgname      func(name string)
	setAppName      func(name string)
	webViewNew      func() uintptr
	loadURI         func(view uintptr, uri string)
	evaluateJS      func(view uintptr, script string, length int, world, source, cancellable, callback, data uintptr)
	runJS           func(view uintptr, script string, cancellable, callback, data uintptr)
	settings        func(view uintptr) uintptr
	allowClipboard  func(settings uintptr, on int32)
}

var (
	gtkWindow, gtkView uintptr
	uiQueue            struct {
		sync.Mutex
		fns []func()
	}
	idleCallback uintptr
)

func loadGTK() (ok bool) {
	open := func(names ...string) uintptr {
		for _, n := range names {
			if h, err := purego.Dlopen(n, purego.RTLD_NOW|purego.RTLD_GLOBAL); err == nil {
				return h
			}
		}
		return 0
	}
	// 4.1 and 4.0 use different versions of libsoup, which can't both be loaded: only ever open one.
	libs := []uintptr{open("libglib-2.0.so.0"), open("libgobject-2.0.so.0"), open("libgtk-3.so.0"),
		open("libwebkit2gtk-4.1.so.0", "libwebkit2gtk-4.0.so.37")}
	for _, h := range libs {
		if h == 0 {
			return false
		}
	}
	glib, gobject, libgtk, webkit := libs[0], libs[1], libs[2], libs[3]
	defer func() {
		if recover() != nil { // a missing function
			ok = false
		}
	}()
	reg := purego.RegisterLibFunc
	reg(&gtk.idleAdd, glib, "g_idle_add")
	reg(&gtk.setPrgname, glib, "g_set_prgname")
	reg(&gtk.setAppName, glib, "g_set_application_name")
	reg(&gtk.signalConnect, gobject, "g_signal_connect_data")
	reg(&gtk.unref, gobject, "g_object_unref")
	reg(&gtk.initCheck, libgtk, "gtk_init_check")
	reg(&gtk.windowNew, libgtk, "gtk_window_new")
	reg(&gtk.setTitle, libgtk, "gtk_window_set_title")
	reg(&gtk.setDefaultSize, libgtk, "gtk_window_set_default_size")
	reg(&gtk.setIconFromFile, libgtk, "gtk_window_set_icon_from_file")
	reg(&gtk.containerAdd, libgtk, "gtk_container_add")
	reg(&gtk.showAll, libgtk, "gtk_widget_show_all")
	reg(&gtk.present, libgtk, "gtk_window_present")
	reg(&gtk.main, libgtk, "gtk_main")
	reg(&gtk.mainQuit, libgtk, "gtk_main_quit")
	reg(&gtk.nativeNew, libgtk, "gtk_file_chooser_native_new")
	reg(&gtk.setCurrentName, libgtk, "gtk_file_chooser_set_current_name")
	reg(&gtk.setFolder, libgtk, "gtk_file_chooser_set_current_folder")
	reg(&gtk.setOverwrite, libgtk, "gtk_file_chooser_set_do_overwrite_confirmation")
	reg(&gtk.nativeRun, libgtk, "gtk_native_dialog_run")
	reg(&gtk.getFilename, libgtk, "gtk_file_chooser_get_filename")
	reg(&gtk.webViewNew, webkit, "webkit_web_view_new")
	reg(&gtk.loadURI, webkit, "webkit_web_view_load_uri")
	reg(&gtk.settings, webkit, "webkit_web_view_get_settings")
	reg(&gtk.allowClipboard, webkit, "webkit_settings_set_javascript_can_access_clipboard")
	if _, err := purego.Dlsym(webkit, "webkit_web_view_evaluate_javascript"); err == nil {
		reg(&gtk.evaluateJS, webkit, "webkit_web_view_evaluate_javascript")
	} else {
		reg(&gtk.runJS, webkit, "webkit_web_view_run_javascript")
	}
	return true
}

func showWindow(url, title string, width, height int, canClose func() bool, gone func()) bool {
	// WebKitGTK's newer renderer shows a blank window with some graphics drivers (NVIDIA, VMs).
	if os.Getenv("WEBKIT_DISABLE_DMABUF_RENDERER") == "" {
		_ = os.Setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
	}
	if !loadGTK() {
		return false
	}
	// Wayland matches the window to cherry-move-app.desktop (its icon and name) by this.
	gtk.setPrgname("cherry-move-app")
	gtk.setAppName(title)
	if gtk.initCheck(0, 0) == 0 {
		return false
	}
	idleCallback = purego.NewCallback(func(data uintptr) uintptr {
		uiQueue.Lock()
		fns := uiQueue.fns
		uiQueue.fns = nil
		uiQueue.Unlock()
		for _, f := range fns {
			f()
		}
		return 0 // run once
	})
	gtkWindow = gtk.windowNew(0)
	gtk.setTitle(gtkWindow, title)
	gtk.setDefaultSize(gtkWindow, int32(width), int32(height))
	if icon := filepath.Join(stateDir(), "icon.png"); os.WriteFile(icon, appIcon, 0o644) == nil {
		gtk.setIconFromFile(gtkWindow, icon, 0)
	}
	gtkView = gtk.webViewNew()
	gtk.allowClipboard(gtk.settings(gtkView), 1)
	gtk.containerAdd(gtkWindow, gtkView)
	gtk.signalConnect(gtkWindow, "delete-event", purego.NewCallback(func(widget, event, data uintptr) uintptr {
		if canClose() {
			return 0
		}
		return 1 // handled: stay open
	}), 0, 0, 0)
	gtk.signalConnect(gtkWindow, "destroy", purego.NewCallback(func(widget, data uintptr) uintptr {
		setWindowOpen(false)
		gtk.mainQuit()
		return 0
	}), 0, 0, 0)
	gtk.loadURI(gtkView, url)
	gtk.showAll(gtkWindow)
	setWindowOpen(true)
	gtk.main()
	gone()
	return true
}

func onUI(f func()) {
	if !hasWindow() {
		f()
		return
	}
	uiQueue.Lock()
	uiQueue.fns = append(uiQueue.fns, f)
	uiQueue.Unlock()
	gtk.idleAdd(idleCallback, 0)
}

func evalJS(js string) {
	if gtk.evaluateJS != nil {
		gtk.evaluateJS(gtkView, js, -1, 0, 0, 0, 0, 0)
	} else {
		gtk.runJS(gtkView, js, 0, 0, 0)
	}
}

func raiseWindow() { gtk.present(gtkWindow) }

// printPage: WebKitGTK shows its print dialog for the page's print().
func printPage() { evalJS("window.print()") }

func saveDialog(name string) string {
	const actionSave, responseAccept = 1, -3
	d := gtk.nativeNew("Save your code", gtkWindow, actionSave, "_Save", "_Cancel")
	gtk.setFolder(d, documentsDir())
	gtk.setCurrentName(d, name)
	gtk.setOverwrite(d, 1)
	path := ""
	if gtk.nativeRun(d) == responseAccept {
		path = gtk.getFilename(d)
	}
	gtk.unref(d)
	return path
}

func allowForeground(pid int) {}

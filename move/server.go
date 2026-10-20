package main

// The app's screens are a web page (ui/index.html) served on 127.0.0.1 and shown in the app's own window
// (window.go), or in the browser where there's no web view. The page polls /api/state; there's one move at a time.

import (
	"cmp"
	"context"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	neturl "net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	qrcode "github.com/skip2/go-qrcode"
)

//go:embed ui
var uiFiles embed.FS

type progressInfo struct {
	Done  int64  `json:"done"`
	Total int64  `json:"total"`
	Name  string `json:"name"`
}

type promptInfo struct {
	Part    int    `json:"part"`
	Problem string `json:"problem"`
	answer  chan string
}

type remoteInfo struct {
	State   string `json:"state"` // idle, working, ready, failed
	Message string `json:"message"`
	URL     string `json:"url,omitempty"`
	Free    int64  `json:"free"`
	Title   string `json:"title,omitempty"`
	Cert    bool   `json:"cert,omitempty"` // failed only because the NAS made its own certificate
}

type App struct {
	mu         sync.Mutex
	token      string
	cats       []*Category
	catsReady  bool
	scanOnce   sync.Once
	plan       *Plan
	sizes      map[Level]int64
	planning   bool
	planErr    string
	planProg   progressInfo
	sending    bool
	sendProg   progressInfo
	sendWhat   string
	prompt     *promptInfo
	result     *SendResult
	sendErr    string
	carry      string
	remote     *Remote
	folder     string
	folderDesc map[string]any
	remoteInfo remoteInfo
	cancel     context.CancelFunc
	lastSeen   time.Time
	mode       string // see serve
	url        string
}

func newApp() *App {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	a := &App{token: hex.EncodeToString(b), lastSeen: time.Now(), remoteInfo: remoteInfo{State: "idle", Free: -1}}
	// macOS asks permission for each folder the first time it's read, so there it waits for "Get started".
	if runtime.GOOS != "darwin" {
		a.startScan()
	}
	return a
}

func (a *App) startScan() {
	a.scanOnce.Do(func() {
		go func() {
			cats := categories()
			for _, c := range cats {
				measure(c)
			}
			a.mu.Lock()
			a.cats, a.catsReady = cats, true
			a.mu.Unlock()
		}()
	})
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusBadRequest)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func (a *App) state() map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.lastSeen = time.Now()
	host, _ := os.Hostname()
	s := map[string]any{
		"os": runtime.GOOS, "host": host, "catsReady": a.catsReady, "cats": a.cats, "planning": a.planning,
		"planErr": a.planErr, "planProgress": a.planProg, "sending": a.sending, "sendProgress": a.sendProg,
		"sendWhat": a.sendWhat, "result": a.result, "sendErr": a.sendErr, "carry": a.carry, "remote": a.remoteInfo,
		"nativeSMB": nativeSMB(), "window": hasWindow(),
	}
	if a.plan != nil {
		s["plan"] = map[string]any{"size": a.plan.Size, "raw": a.plan.RawSize(), "files": a.plan.Files,
			"duplicates": a.plan.Duplicates, "saved": a.plan.Saved}
	}
	if a.sizes != nil {
		s["sizes"] = map[string]int64{"normal": a.sizes[Normal], "strong": a.sizes[Strong]}
	}
	if a.prompt != nil {
		s["prompt"] = a.prompt
	}
	if a.result != nil {
		s["code"] = prettyCode(a.result.Code)
	}
	return s
}

func (a *App) handlePlan(w http.ResponseWriter, r *http.Request) {
	var req struct {
		IDs []string `json:"ids"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || len(req.IDs) == 0 {
		fail(w, "Choose something to bring.")
		return
	}
	a.mu.Lock()
	if a.sending || !a.catsReady {
		a.mu.Unlock()
		fail(w, "Not ready yet.")
		return
	}
	want := map[string]bool{}
	for _, id := range req.IDs {
		want[id] = true
	}
	var chosen []*Category
	for _, c := range a.cats {
		if want[c.ID] {
			chosen = append(chosen, c)
		}
	}
	if a.cancel != nil {
		a.cancel()
	}
	ctx, cancel := context.WithCancel(context.Background())
	a.cancel = cancel
	a.plan, a.sizes, a.planErr, a.planning = nil, nil, "", true
	a.planProg = progressInfo{}
	a.mu.Unlock()
	go func() {
		p, err := makePlan(ctx, chosen, func(done, total int64, name string) {
			a.mu.Lock()
			a.planProg = progressInfo{done, total, name}
			a.mu.Unlock()
		})
		var sizes map[Level]int64
		if err == nil {
			sizes = estimate(p)
		}
		a.mu.Lock()
		defer a.mu.Unlock()
		if ctx.Err() != nil {
			return
		}
		a.planning = false
		if err != nil {
			a.planErr = "Couldn't look through your files: " + err.Error()
			return
		}
		a.plan, a.sizes = p, sizes
	}()
	writeJSON(w, map[string]bool{"ok": true})
}

func (a *App) handleDrives(w http.ResponseWriter, r *http.Request) {
	drives := removableDrives()
	a.mu.Lock()
	sizes := a.sizes
	a.mu.Unlock()
	type driveOut struct {
		Drive
		Title  string `json:"title"`
		Usable int64  `json:"usable"`
		Fit    *Fit   `json:"fit,omitempty"`
		Bigger int64  `json:"bigger,omitempty"`
	}
	out := []driveOut{}
	for _, d := range drives {
		o := driveOut{Drive: d, Title: d.Title(), Usable: usableSpace(d.Path)}
		if sizes != nil {
			f := fit(sizes, o.Usable)
			o.Fit, o.Bigger = &f, biggerStick(f.Need)
		}
		out = append(out, o)
	}
	writeJSON(w, map[string]any{"drives": out, "network": networkFolders()})
}

// smbDescriptor describes a Windows share (\\nas\share\folder) the way Cherry OS's NAS sign-in expects.
func smbDescriptor(unc, user string) (map[string]any, error) {
	parts := strings.FieldsFunc(strings.TrimPrefix(unc, `\\`), func(r rune) bool { return r == '\\' || r == '/' })
	if !strings.HasPrefix(unc, `\\`) || len(parts) < 2 {
		return nil, errors.New(`choose a network folder, like \\NAS\Backups`)
	}
	d := map[string]any{"kind": "nas", "title": fmt.Sprintf("your NAS (%s)", parts[0]), "type": "smb",
		"host": parts[0], "path": strings.Join(parts[1:], "/")}
	if user != "" {
		d["user"] = user
	}
	return d, nil
}

func (a *App) handleConnect(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Kind     string         `json:"kind"`
		Settings map[string]any `json:"settings"`
		Password string         `json:"password"`
		Folder   string         `json:"folder"`
		User     string         `json:"user"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		fail(w, "Bad request")
		return
	}
	a.mu.Lock()
	if a.remote != nil {
		old := a.remote
		go old.Forget()
	}
	a.remote, a.folder, a.folderDesc = nil, "", nil
	a.remoteInfo = remoteInfo{State: "working", Message: "Connecting…", Free: -1}
	if req.Kind == "gdrive" || req.Kind == "onedrive" {
		a.remoteInfo.Message = "Finish signing in in your web browser, then come back here."
	}
	a.mu.Unlock()
	setInfo := func(info remoteInfo) {
		a.mu.Lock()
		a.remoteInfo = info
		a.mu.Unlock()
	}
	if req.Kind == "folder" {
		go func() {
			unc := uncFor(strings.TrimSpace(req.Folder))
			desc, err := smbDescriptor(unc, strings.TrimSpace(req.User))
			if err == nil {
				err = os.MkdirAll(filepath.Join(unc, remoteRoot), 0o755)
				if err != nil {
					err = fmt.Errorf("couldn't open %s: %v", unc, err)
				}
			}
			if err != nil {
				setInfo(remoteInfo{State: "failed", Message: capitalise(err.Error()), Free: -1})
				return
			}
			a.mu.Lock()
			a.folder, a.folderDesc = unc, desc
			a.remoteInfo = remoteInfo{State: "ready", Message: "Connected", Free: freeSpace(unc), Title: fmt.Sprint(desc["title"])}
			a.mu.Unlock()
		}()
		writeJSON(w, map[string]bool{"ok": true})
		return
	}
	rem := newRemote(req.Kind, req.Settings)
	go func() {
		err := rem.SignIn(req.Password, func(u string) {
			a.mu.Lock()
			a.remoteInfo.URL = u
			a.mu.Unlock()
		}, func(msg string) {
			a.mu.Lock()
			a.remoteInfo.Message = msg
			a.mu.Unlock()
		})
		if err != nil {
			setInfo(remoteInfo{State: "failed", Message: capitalise(err.Error()), Free: -1,
				Cert: errors.Is(err, errCertificate)})
			return
		}
		free := rem.FreeSpace()
		a.mu.Lock()
		a.remote = rem
		a.remoteInfo = remoteInfo{State: "ready", Message: "Connected", Free: free, Title: rem.Title()}
		a.mu.Unlock()
	}()
	writeJSON(w, map[string]bool{"ok": true})
}

func capitalise(s string) string {
	if s == "" {
		return s
	}
	s = strings.ToUpper(s[:1]) + s[1:]
	if !strings.HasSuffix(s, ".") {
		s += "."
	}
	return s
}

func (a *App) handleSend(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Storage    string `json:"storage"`
		Drive      string `json:"drive"`
		Level      int    `json:"level"`
		Carry      string `json:"carry"`
		CarryDrive string `json:"carryDrive"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		fail(w, "Bad request")
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.sending || a.plan == nil {
		fail(w, "Not ready yet.")
		return
	}
	opt := SendOptions{Storage: req.Storage, Drive: req.Drive, Level: Level(req.Level), Label: defaultLabel()}
	switch req.Storage {
	case "usb":
		a.sendWhat = "saving to the USB stick"
	case "folder":
		if a.folder == "" {
			fail(w, "Connect to your NAS first.")
			return
		}
		opt.Folder, opt.Descriptor = a.folder, a.folderDesc
		a.sendWhat = "saving to " + fmt.Sprint(a.folderDesc["title"])
	case "remote":
		if a.remote == nil {
			fail(w, "Sign in first.")
			return
		}
		opt.Remote, opt.Descriptor = a.remote, a.remote.Descriptor()
		a.sendWhat = "uploading to " + a.remote.Title()
	default:
		fail(w, "Choose where your files should go.")
		return
	}
	if req.Storage != "usb" && req.Carry == "usb" {
		opt.CarryDrive = req.CarryDrive
	}
	ctx, cancel := context.WithCancel(context.Background())
	a.cancel = cancel
	a.sending, a.result, a.sendErr, a.carry = true, nil, "", req.Carry
	a.sendProg = progressInfo{}
	opt.Progress = func(done, total int64, name string) {
		a.mu.Lock()
		a.sendProg = progressInfo{done, total, name}
		a.mu.Unlock()
	}
	opt.NextStick = func(part int, problem string) (string, error) {
		p := &promptInfo{Part: part, Problem: problem, answer: make(chan string, 1)}
		log.Printf("waiting for stick %d (%s)", part, cmp.Or(problem, "the last one is full"))
		a.mu.Lock()
		a.prompt = p
		a.mu.Unlock()
		defer func() {
			a.mu.Lock()
			if a.prompt == p {
				a.prompt = nil
			}
			a.mu.Unlock()
		}()
		select {
		case drive := <-p.answer:
			return drive, nil
		case <-ctx.Done():
			return "", ctx.Err()
		}
	}
	plan := a.plan
	// Never log the code: it unlocks the files.
	log.Printf("%s: %d files, %d bytes", a.sendWhat, plan.Files, plan.Size)
	go func() {
		res, err := send(ctx, plan, opt)
		a.mu.Lock()
		defer a.mu.Unlock()
		a.sending = false
		if err != nil {
			log.Printf("stopped: %v", err)
			if errors.Is(err, context.Canceled) {
				a.sendErr = "cancelled"
			} else {
				a.sendErr = capitalise(err.Error())
			}
			return
		}
		log.Printf("done: %d files, %d skipped, %d sticks", res.Files, len(res.Skipped), res.Sticks)
		a.result = res
		if a.remote != nil {
			go a.remote.Forget()
			a.remote = nil
		}
	}()
	writeJSON(w, map[string]bool{"ok": true})
}

func (a *App) handleStick(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	a.mu.Lock()
	p := a.prompt
	a.mu.Unlock()
	if p == nil || req.Path == "" {
		fail(w, "Not waiting for a stick.")
		return
	}
	select {
	case p.answer <- req.Path:
	default:
	}
	writeJSON(w, map[string]bool{"ok": true})
}

func (a *App) handleCancel(w http.ResponseWriter, r *http.Request) {
	a.mu.Lock()
	if a.cancel != nil {
		a.cancel()
	}
	a.planning = false
	a.mu.Unlock()
	writeJSON(w, map[string]bool{"ok": true})
}

func (a *App) codeText() (string, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.result == nil {
		return "", false
	}
	return prettyCode(a.result.Code), true
}

func (a *App) handleQR(w http.ResponseWriter, r *http.Request) {
	code, ok := a.codeText()
	if !ok {
		http.NotFound(w, r)
		return
	}
	png, err := qrcode.Encode("Cherry Move code: "+code, qrcode.Medium, 512)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	_, _ = w.Write(png)
}

const codeFileName = "Cherry Move code.txt"

func (a *App) codeFile() (string, bool) {
	code, ok := a.codeText()
	if !ok {
		return "", false
	}
	a.mu.Lock()
	where := ""
	if a.result.Remote != nil {
		where = fmt.Sprintf("Your files are in %v, encrypted.\r\n", a.result.Remote["title"])
	}
	a.mu.Unlock()
	return fmt.Sprintf("Cherry Move transfer code\r\n\r\n%s\r\n\r\n%sOn the new computer, open Cherry Move, choose \"Get files from my old computer\" and type this code.\r\nAnyone with this code can get your files, so keep it safe.\r\n", code, where), true
}

func (a *App) handleCodeFile(w http.ResponseWriter, r *http.Request) {
	text, ok := a.codeFile()
	if !ok {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="`+codeFileName+`"`)
	fmt.Fprint(w, text)
}

// handleSaveCode is "Save code as a file…" in the app's own window, which can't download.
func (a *App) handleSaveCode(w http.ResponseWriter, r *http.Request) {
	text, ok := a.codeFile()
	if !ok || !hasWindow() {
		fail(w, "Nothing to save.")
		return
	}
	var path string
	onUIWait(func() { path = saveDialog(codeFileName) })
	if path != "" {
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			fail(w, "Couldn't save it there: "+err.Error())
			return
		}
	}
	writeJSON(w, map[string]string{"saved": path})
}

// handlePrint prints the window's page, which the page has set up to show just the code on paper.
func (a *App) handlePrint(w http.ResponseWriter, r *http.Request) {
	if !hasWindow() {
		fail(w, "Printing from here needs the app's own window.")
		return
	}
	onUI(printPage)
	writeJSON(w, map[string]bool{"ok": true})
}

// handleOpen opens a link (a sign-in page) in the browser.
func (a *App) handleOpen(w http.ResponseWriter, r *http.Request) {
	var req struct {
		URL string `json:"url"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	if !strings.HasPrefix(req.URL, "https://") && !strings.HasPrefix(req.URL, "http://") {
		fail(w, "Not a web address.")
		return
	}
	openExternal(req.URL)
	writeJSON(w, map[string]bool{"ok": true})
}

// handleRaise: Cherry Move was opened again while it's already open, so bring this one to the front.
func (a *App) handleRaise(w http.ResponseWriter, r *http.Request) {
	if hasWindow() {
		onUI(raiseWindow)
	} else if a.mode == "browser" {
		openBrowser(a.url)
	}
	writeJSON(w, map[string]bool{"ok": true})
}

// canClose is asked (on the window's thread) when someone closes the window: not while files are being moved.
func (a *App) canClose() bool {
	a.mu.Lock()
	busy := a.sending
	a.mu.Unlock()
	if busy {
		evalJS("window.askClose && askClose()")
	}
	return !busy
}

// quit stops a move that's still going (cleaning up after it), then ends the program.
func (a *App) quit() {
	a.mu.Lock()
	if a.cancel != nil {
		a.cancel()
	}
	a.mu.Unlock()
	for i := 0; i < 100; i++ {
		a.mu.Lock()
		sending := a.sending
		a.mu.Unlock()
		if !sending {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	a.shutdown()
	os.Exit(0)
}

// guard only lets this app's own page talk to the server (other web pages can't reach 127.0.0.1 with the token).
func (a *App) guard(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Cherry") != a.token && r.URL.Query().Get("t") != a.token {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		h(w, r)
	}
}

func (a *App) routes() http.Handler {
	mux := http.NewServeMux()
	sub, _ := fs.Sub(uiFiles, "ui")
	static := http.FileServer(http.FS(sub))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/" && r.URL.Query().Get("t") != a.token {
			http.Error(w, "Open Cherry Move from its icon.", http.StatusForbidden)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		static.ServeHTTP(w, r)
	})
	mux.HandleFunc("GET /api/state", a.guard(func(w http.ResponseWriter, r *http.Request) { writeJSON(w, a.state()) }))
	mux.HandleFunc("POST /api/scan", a.guard(func(w http.ResponseWriter, r *http.Request) {
		a.startScan()
		writeJSON(w, map[string]bool{"ok": true})
	}))
	mux.HandleFunc("POST /api/plan", a.guard(a.handlePlan))
	mux.HandleFunc("GET /api/drives", a.guard(a.handleDrives))
	mux.HandleFunc("POST /api/connect", a.guard(a.handleConnect))
	mux.HandleFunc("POST /api/send", a.guard(a.handleSend))
	mux.HandleFunc("POST /api/stick", a.guard(a.handleStick))
	mux.HandleFunc("POST /api/cancel", a.guard(a.handleCancel))
	mux.HandleFunc("GET /api/qr", a.guard(a.handleQR))
	mux.HandleFunc("GET /api/code.txt", a.guard(a.handleCodeFile))
	mux.HandleFunc("POST /api/save-code", a.guard(a.handleSaveCode))
	mux.HandleFunc("POST /api/print", a.guard(a.handlePrint))
	mux.HandleFunc("POST /api/open", a.guard(a.handleOpen))
	mux.HandleFunc("POST /api/raise", a.guard(a.handleRaise))
	mux.HandleFunc("POST /api/quit", a.guard(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]bool{"ok": true})
		go func() { time.Sleep(300 * time.Millisecond); a.quit() }()
	}))
	return mux
}

func (a *App) shutdown() {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.remote != nil {
		a.remote.Forget()
	}
	if a.mode != "none" {
		_ = os.Remove(runningFile())
	}
}

// running.json says where the open copy of the app is, so opening it again brings that one to the front.
type runningInfo struct {
	URL string `json:"url"`
	PID int    `json:"pid"`
}

func runningFile() string { return filepath.Join(stateDir(), "running.json") }

// raiseRunning asks a copy of the app that's already open to come to the front; false if there isn't one.
func raiseRunning() bool {
	b, err := os.ReadFile(runningFile())
	if err != nil {
		return false
	}
	var info runningInfo
	if json.Unmarshal(b, &info) != nil {
		return false
	}
	u, err := neturl.Parse(info.URL)
	if err != nil || u.Host == "" {
		return false
	}
	allowForeground(info.PID)
	req, _ := http.NewRequest("POST", "http://"+u.Host+"/api/raise", nil)
	req.Header.Set("X-Cherry", u.Query().Get("t"))
	resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// serve runs the app. mode "window": in its own window, until that's closed; "browser": in the browser, until
// the page has been closed for a while (and nothing is being moved); "none": just prints the address (tests).
func serve(mode string) error {
	a := newApp()
	a.mode = mode
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	a.url = fmt.Sprintf("http://127.0.0.1:%d/?t=%s", l.Addr().(*net.TCPAddr).Port, a.token)
	fmt.Println("Cherry Move is open at", a.url)
	if mode != "none" {
		if b, err := json.Marshal(runningInfo{URL: a.url, PID: os.Getpid()}); err == nil {
			_ = os.WriteFile(runningFile(), b, 0o600)
		}
	}
	go func() { log.Fatal(http.Serve(l, a.routes())) }()
	if mode == "window" {
		gone := func() { a.shutdown(); os.Exit(0) }
		if showWindow(a.url, windowTitle, 920, 780, a.canClose, gone) {
			gone()
		}
		log.Print("no web view here: opening the browser instead")
		a.mode = "browser"
	}
	if a.mode == "browser" {
		openBrowser(a.url)
	}
	for range time.Tick(5 * time.Second) {
		a.mu.Lock()
		idle := a.mode == "browser" && time.Since(a.lastSeen) > 90*time.Second && !a.sending && a.prompt == nil
		a.mu.Unlock()
		if idle {
			a.shutdown()
			os.Exit(0)
		}
	}
	return nil
}

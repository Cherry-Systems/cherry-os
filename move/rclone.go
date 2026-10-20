package main

// Someone's own storage through rclone, as in desktop/lib/cherryos/remote.py: Google Drive (its hidden app
// folder), OneDrive, MEGA, or a NAS over SFTP or WebDAV (and SMB, except on Windows where shares are opened
// directly). rclone is downloaded the first time it's needed if it isn't installed. Sign-ins are kept only
// while Cherry Move is open.

import (
	"archive/zip"
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

var kindTitles = map[string]string{"gdrive": "Google Drive", "onedrive": "OneDrive", "mega": "MEGA"}

type Remote struct {
	Kind     string
	Settings map[string]any

	mu     sync.Mutex
	server *exec.Cmd
	port   int
	auth   string
	client *http.Client
}

func newRemote(kind string, settings map[string]any) *Remote {
	clean := map[string]any{}
	for k, v := range settings {
		if k != "kind" && k != "title" && v != nil && v != "" && v != false {
			clean[k] = v
		}
	}
	splitAddress(clean)
	return &Remote{Kind: kind, Settings: clean, client: &http.Client{Timeout: 15 * time.Minute,
		Transport: &http.Transport{MaxIdleConnsPerHost: 16}}}
}

var (
	addressRe = regexp.MustCompile(`(?i)^(?:[a-z]+:)?/*(?:([^@/]+)@)?([^/]*)(/.*)?$`)
	hostPort  = regexp.MustCompile(`^([^:\[\]]+|\[[^\]]+\]):(\d+)$`)
)

// splitAddress takes a NAS address the way people copy it (nas.local:2222, smb://nas/Backups/Move,
// \\NAS\Backups, sftp://me@nas/volume1/Backups) apart into host, port, folder and username, as
// split_address in remote.py. An SFTP folder starting with / stays absolute.
func splitAddress(s map[string]any) {
	kind := fmt.Sprint(s["type"])
	if kind != "smb" && kind != "sftp" {
		return
	}
	str := func(k string) string {
		if v, ok := s[k]; ok {
			return strings.TrimSpace(fmt.Sprint(v))
		}
		return ""
	}
	if m := addressRe.FindStringSubmatch(strings.ReplaceAll(str("host"), `\`, "/")); m != nil {
		user, host, path := m[1], m[2], m[3]
		if user != "" && str("user") == "" {
			s["user"] = user
		}
		if p := hostPort.FindStringSubmatch(host); p != nil {
			host = p[1]
			if str("port") == "" {
				s["port"] = p[2]
			}
		}
		s["host"] = host
		if strings.Trim(path, "/") != "" && str("path") == "" {
			s["path"] = path
		}
	}
	folder := strings.ReplaceAll(str("path"), `\`, "/")
	absolute := kind == "sftp" && strings.HasPrefix(folder, "/")
	folder = strings.Trim(folder, "/")
	if absolute {
		folder = "/" + folder
	}
	if folder == "" {
		delete(s, "path")
	} else {
		s["path"] = folder
	}
	if p := str("port"); p != "" {
		s["port"] = p
	}
}

// insecure: the person agreed to trust their NAS's own https certificate.
func (r *Remote) insecure() bool { return r.Settings["insecure"] == true }

// errCertificate: a NAS on https with a certificate it made itself (Synology and QNAP do, out of the box).
var errCertificate = errors.New("this NAS uses a security certificate it made itself, so Cherry Move can't check " +
	"it's really your NAS. That's normal for a NAS at home. Connect anyway if it's yours")

func badCertificate(msg string) bool {
	l := strings.ToLower(msg)
	return strings.Contains(l, "x509:") || strings.Contains(l, "tls: failed to verify certificate")
}

func (r *Remote) setting(key string) string {
	if v, ok := r.Settings[key]; ok {
		return fmt.Sprint(v)
	}
	return ""
}

func (r *Remote) Title() string {
	if r.Kind == "nas" {
		host := r.setting("host")
		if host == "" {
			host = r.setting("url")
		}
		return fmt.Sprintf("your NAS (%s)", host)
	}
	return kindTitles[r.Kind]
}

// Descriptor is saved on the stick: enough for the new computer to find the files, never a password.
func (r *Remote) Descriptor() map[string]any {
	d := map[string]any{"kind": r.Kind, "title": r.Title()}
	for k, v := range r.Settings {
		d[k] = v
	}
	return d
}

func (r *Remote) name() string { return "cherry-" + r.Kind }

func (r *Remote) root() string { return r.name() + ":" + r.setting("path") }

// at is rel inside the chosen folder ("name:/x" would be the top of the whole disk over SFTP).
func (r *Remote) at(rel string) string {
	rel = strings.Trim(rel, "/")
	if root := r.root(); !strings.HasSuffix(root, ":") {
		return root + "/" + rel
	}
	return r.root() + rel
}

// rc runs rclone, trusting the NAS's own certificate if the person said to.
func (r *Remote) rc(timeout time.Duration, args ...string) (string, error) {
	if r.insecure() {
		args = append([]string{"--no-check-certificate"}, args...)
	}
	return runRclone(timeout, args...)
}

// ---------------------------------------------------------------- the rclone program

var (
	rcloneOnce sync.Mutex
	rclonePath string
)

func configPath() string { return filepath.Join(stateDir(), "rclone.conf") }

func rcloneVersionOK(bin string) bool {
	out, err := hiddenCommand(bin, "version").Output()
	if err != nil {
		return false
	}
	m := regexp.MustCompile(`v(\d+)\.(\d+)`).FindStringSubmatch(string(out))
	if m == nil {
		return false
	}
	var major, minor int
	fmt.Sscan(m[1], &major)
	fmt.Sscan(m[2], &minor)
	return major > 1 || (major == 1 && minor >= 60)
}

// rclone finds rclone (installed, or downloaded earlier), downloading it if needed.
func rclone(status func(string)) (string, error) {
	rcloneOnce.Lock()
	defer rcloneOnce.Unlock()
	if rclonePath != "" {
		return rclonePath, nil
	}
	exe := "rclone"
	if runtime.GOOS == "windows" {
		exe = "rclone.exe"
	}
	local := filepath.Join(stateDir(), exe)
	for _, cand := range []string{local, "rclone"} {
		if p, err := exec.LookPath(cand); err == nil && rcloneVersionOK(p) {
			rclonePath = p
			return p, nil
		}
	}
	if status != nil {
		status("Downloading rclone, which talks to your storage (about 25 MB)…")
	}
	if err := downloadRclone(local); err != nil {
		return "", fmt.Errorf("couldn't download rclone: %w", err)
	}
	rclonePath = local
	return local, nil
}

func fetch(u string) ([]byte, error) {
	res, err := (&http.Client{Timeout: 10 * time.Minute}).Get(u)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("%s: %s", u, res.Status)
	}
	return io.ReadAll(res.Body)
}

func downloadRclone(dest string) error {
	v, err := fetch("https://downloads.rclone.org/version.txt")
	if err != nil {
		return err
	}
	version := strings.TrimPrefix(strings.TrimSpace(string(v)), "rclone ")
	goos := map[string]string{"windows": "windows", "darwin": "osx", "linux": "linux"}[runtime.GOOS]
	name := fmt.Sprintf("rclone-%s-%s-%s.zip", version, goos, runtime.GOARCH)
	sums, err := fetch(fmt.Sprintf("https://downloads.rclone.org/%s/SHA256SUMS", version))
	if err != nil {
		return err
	}
	want := ""
	for _, line := range strings.Split(string(sums), "\n") {
		if f := strings.Fields(line); len(f) == 2 && f[1] == name {
			want = f[0]
		}
	}
	if want == "" {
		return fmt.Errorf("no checksum for %s", name)
	}
	data, err := fetch(fmt.Sprintf("https://downloads.rclone.org/%s/%s", version, name))
	if err != nil {
		return err
	}
	if sum := sha256.Sum256(data); hex.EncodeToString(sum[:]) != want {
		return errors.New("the download was damaged (checksum mismatch)")
	}
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return err
	}
	for _, f := range zr.File {
		if base := filepath.Base(f.Name); base == "rclone" || base == "rclone.exe" {
			rc, err := f.Open()
			if err != nil {
				return err
			}
			body, err := io.ReadAll(rc)
			rc.Close()
			if err != nil {
				return err
			}
			if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
				return err
			}
			if err := os.WriteFile(dest+".tmp", body, 0o755); err != nil {
				return err
			}
			return os.Rename(dest+".tmp", dest)
		}
	}
	return errors.New("rclone wasn't in the download")
}

func reason(stderr string) string {
	var last string
	for _, ln := range strings.Split(strings.TrimSpace(stderr), "\n") {
		if strings.TrimSpace(ln) != "" {
			last = strings.TrimSpace(ln)
		}
	}
	if last == "" {
		last = "unknown error"
	}
	last = regexp.MustCompile(`^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d (ERROR|CRITICAL|NOTICE)\s*:\s*`).ReplaceAllString(last, "")
	l := strings.ToLower(last)
	switch {
	case strings.Contains(l, "login") && strings.Contains(l, "fail"),
		strings.Contains(l, "invalid") && (strings.Contains(l, "password") || strings.Contains(l, "credentials")),
		strings.Contains(l, "logon failure"), strings.Contains(l, "authentication"), strings.Contains(l, "unauthorized"),
		strings.Contains(l, "401"):
		return "The username or password wasn't accepted."
	case strings.Contains(l, "no such host"), strings.Contains(l, "connection refused"), strings.Contains(l, "i/o timeout"),
		strings.Contains(l, "no route"):
		return "Couldn't reach it. Check the address, and that it's switched on and on the same network."
	}
	return last
}

func runRclone(timeout time.Duration, args ...string) (string, error) {
	bin, err := rclone(nil)
	if err != nil {
		return "", err
	}
	_ = os.MkdirAll(stateDir(), 0o700)
	cmd := hiddenCommand(bin, append([]string{"--config", configPath()}, args...)...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Start(); err != nil {
		return "", err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err = <-done:
	case <-time.After(timeout):
		_ = cmd.Process.Kill()
		return "", errors.New("the storage took too long to answer. Check the connection and try again")
	}
	_ = os.Chmod(configPath(), 0o600)
	if err != nil {
		return stdout.String(), errors.New(reason(stderr.String()))
	}
	return stdout.String(), nil
}

// ---------------------------------------------------------------- signing in

// SignIn signs in (opening the browser for Google and Microsoft), then checks the storage can be written to.
// onURL gets the sign-in link in case the browser doesn't open by itself.
func (r *Remote) SignIn(password string, onURL func(string), status func(string)) error {
	if _, err := rclone(status); err != nil {
		return err
	}
	_, _ = runRclone(time.Minute, "config", "delete", r.name())
	create := []string{"config", "create", "--obscure", r.name()}
	var port []string
	if p := r.setting("port"); p != "" {
		port = []string{"port", p}
	}
	var err error
	switch {
	case r.Kind == "gdrive":
		err = r.browserSignIn([]string{"drive", "scope", "drive.appfolder", "root_folder_id", "appDataFolder"}, onURL)
	case r.Kind == "onedrive":
		err = r.browserSignIn([]string{"onedrive"}, onURL)
	case r.Kind == "mega":
		_, err = runRclone(2*time.Minute, append(create, "mega", "user", r.setting("user"), "pass", password)...)
	case r.setting("type") == "smb":
		user := r.setting("user")
		if user == "" {
			user = "guest"
		}
		_, err = runRclone(2*time.Minute, append(append(create, "smb", "host", r.setting("host"), "user", user, "pass", password), port...)...)
	case r.setting("type") == "sftp":
		_, err = runRclone(2*time.Minute, append(append(create, "sftp", "host", r.setting("host"), "user", r.setting("user"), "pass", password), port...)...)
	case r.setting("type") == "webdav":
		_, err = runRclone(2*time.Minute, append(create, "webdav", "url", r.setting("url"), "vendor", "other", "user", r.setting("user"), "pass", password)...)
	default:
		err = errors.New("choose how to connect to the NAS")
	}
	if err != nil {
		return err
	}
	if _, err := r.rc(90*time.Second, "mkdir", r.at(remoteRoot)); err != nil {
		r.Forget()
		if badCertificate(err.Error()) {
			return errCertificate
		}
		return err
	}
	return nil
}

func (r *Remote) browserSignIn(args []string, onURL func(string)) error {
	bin, err := rclone(nil)
	if err != nil {
		return err
	}
	cmd := hiddenCommand(bin, append([]string{"--config", configPath(), "config", "create", r.name()}, args...)...)
	out, _ := cmd.StdoutPipe()
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		return err
	}
	var log strings.Builder
	urlRe := regexp.MustCompile(`(http://127\.0\.0\.1:\d+/auth\?state=\S+)`)
	sc := bufio.NewScanner(out)
	for sc.Scan() {
		log.WriteString(sc.Text() + "\n")
		if m := urlRe.FindStringSubmatch(sc.Text()); m != nil && onURL != nil {
			onURL(m[1])
		}
	}
	err = cmd.Wait()
	_ = os.Chmod(configPath(), 0o600)
	if err != nil {
		return errors.New(reason(log.String()))
	}
	return nil
}

func (r *Remote) Forget() {
	r.Close()
	_, _ = runRclone(time.Minute, "config", "delete", r.name())
}

// FreeSpace is the bytes free, or -1 if the storage doesn't say.
func (r *Remote) FreeSpace() int64 {
	out, err := r.rc(time.Minute, "about", r.name()+":", "--json")
	if err != nil {
		return -1
	}
	var info map[string]int64
	if json.Unmarshal([]byte(out), &info) != nil {
		return -1
	}
	if f, ok := info["free"]; ok {
		return f
	}
	if t, ok := info["total"]; ok {
		return max(0, t-info["used"]-info["trashed"])
	}
	return -1
}

// ---------------------------------------------------------------- moving chunks (rclone serve webdav)

func (r *Remote) start() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.server != nil && r.server.ProcessState == nil {
		return nil
	}
	bin, err := rclone(nil)
	if err != nil {
		return err
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	port := l.Addr().(*net.TCPAddr).Port
	l.Close()
	secret := make([]byte, 18)
	_, _ = rand.Read(secret)
	password := base64.RawURLEncoding.EncodeToString(secret)
	args := []string{"--config", configPath(), "serve", "webdav", r.root(), "--addr", fmt.Sprintf("127.0.0.1:%d", port),
		"--dir-cache-time", "5s", "--low-level-retries", "10", "--retries", "5"}
	if r.insecure() {
		args = append(args, "--no-check-certificate")
	}
	cmd := hiddenCommand(bin, args...)
	cmd.Env = append(os.Environ(), "RCLONE_USER=cherry", "RCLONE_PASS="+password)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	exited := make(chan struct{})
	go func() { _ = cmd.Wait(); close(exited) }()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-exited:
			return errors.New(reason(stderr.String()))
		default:
		}
		if c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), time.Second); err == nil {
			c.Close()
			r.server, r.port = cmd, port
			r.auth = "Basic " + base64.StdEncoding.EncodeToString([]byte("cherry:"+password))
			return nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	_ = cmd.Process.Kill()
	return errors.New("rclone didn't start in time")
}

func (r *Remote) request(method, path string, body []byte) (int, []byte, error) {
	if err := r.start(); err != nil {
		return 0, nil, err
	}
	u := fmt.Sprintf("http://127.0.0.1:%d/%s", r.port, (&url.URL{Path: strings.Trim(path, "/")}).EscapedPath())
	if method == "MKCOL" {
		u += "/"
	}
	var rd io.Reader
	if body != nil {
		rd = bytes.NewReader(body)
	}
	req, _ := http.NewRequest(method, u, rd)
	req.Header.Set("Authorization", r.auth)
	res, err := r.client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer res.Body.Close()
	data, err := io.ReadAll(res.Body)
	return res.StatusCode, data, err
}

func (r *Remote) Mkdirs(path string) error {
	made := ""
	for _, part := range strings.Split(strings.Trim(path, "/"), "/") {
		if made != "" {
			made += "/"
		}
		made += part
		status, body, err := r.request("MKCOL", made, nil)
		if err != nil {
			return err
		}
		if status != 200 && status != 201 && status != 405 {
			return fmt.Errorf("couldn't make a folder in %s (%d %.200s)", r.Title(), status, body)
		}
	}
	return nil
}

func (r *Remote) Put(path string, data []byte) error {
	status, body, err := r.request("PUT", path, data)
	if err != nil {
		return err
	}
	if status != 200 && status != 201 && status != 204 {
		return fmt.Errorf("couldn't save to %s (%d %.200s)", r.Title(), status, body)
	}
	return nil
}

func (r *Remote) Delete(path string) {
	r.Close()
	_, _ = r.rc(10*time.Minute, "purge", r.at(path))
}

func (r *Remote) Close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.server != nil && r.server.Process != nil {
		_ = r.server.Process.Kill()
	}
	r.server = nil
}

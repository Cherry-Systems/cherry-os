package main

import (
	_ "embed"
	"encoding/json"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// A copy of desktop/lib/cherryos/system_extras.py (build-move.sh refreshes it; a test checks they match).
//
//go:embed system_extras.py
var systemExtrasPy []byte

// Where the files gatherExtras finds arrive, in the new computer's home folder (EXTRAS_HOME in transfer.py).
const extrasHome = ".local/share/cherry/old-computer"

// gatherExtras runs system_extras.py with this computer's Python into `dir` (emptied first): apps installed
// from downloaded .deb files, your own system services, Docker servers. It returns what was found and the
// files to carry, or nothing when this isn't a Linux with Python.
func gatherExtras(dir string) (any, []entry) {
	if runtime.GOOS != "linux" {
		return nil, nil
	}
	py, err := exec.LookPath("python3")
	if err != nil {
		return nil, nil
	}
	code := systemExtrasPy
	if alt := os.Getenv("CHERRY_MOVE_EXTRAS"); alt != "" { // tests: a stand-in that doesn't look at this computer
		if code, err = os.ReadFile(alt); err != nil {
			return nil, nil
		}
	}
	os.RemoveAll(dir)
	if os.MkdirAll(dir, 0o700) != nil {
		return nil, nil
	}
	script, err := os.CreateTemp(filepath.Dir(dir), "system_extras-*.py")
	if err != nil {
		return nil, nil
	}
	defer os.Remove(script.Name())
	_, err = script.Write(code)
	script.Close()
	if err != nil {
		return nil, nil
	}
	out, err := hiddenCommand(py, script.Name(), dir).Output()
	var found any
	if err != nil || json.Unmarshal(out, &found) != nil {
		os.RemoveAll(dir)
		return nil, nil
	}
	var files []entry
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err == nil && d.Type().IsRegular() {
			if info, err := d.Info(); err == nil {
				rel, _ := filepath.Rel(dir, p)
				files = append(files, entry{p, "home/" + extrasHome + "/" + filepath.ToSlash(rel), info})
			}
		}
		return nil
	})
	return found, files
}

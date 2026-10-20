package main

import "syscall"

func networkVolume(st *syscall.Statfs_t) bool {
	var name []byte
	for _, c := range st.Fstypename {
		if c == 0 {
			break
		}
		name = append(name, byte(c))
	}
	switch string(name) {
	case "smbfs", "afpfs", "nfs", "webdav", "ftp", "cifs":
		return true
	}
	return false
}

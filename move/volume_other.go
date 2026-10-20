//go:build !windows && !darwin

package main

import "syscall"

// Only /media and /run/media are looked at, where the desktop mounts sticks and disks.
func networkVolume(*syscall.Statfs_t) bool { return false }

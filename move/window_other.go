//go:build !windows && !darwin && !linux

package main

func showWindow(url, title string, width, height int, canClose func() bool, gone func()) bool {
	return false
}
func onUI(f func())                 { f() }
func evalJS(js string)              {}
func raiseWindow()                  {}
func printPage()                    {}
func saveDialog(name string) string { return "" }
func allowForeground(pid int)       {}

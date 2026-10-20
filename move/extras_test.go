package main

import (
	"bytes"
	"os"
	"testing"
)

func TestSystemExtrasMatchesCherryOS(t *testing.T) {
	want, err := os.ReadFile("../desktop/lib/cherryos/system_extras.py")
	if err != nil {
		t.Skip("not in the Cherry OS source tree")
	}
	if !bytes.Equal(want, systemExtrasPy) {
		t.Fatal("move/system_extras.py differs from desktop/lib/cherryos/system_extras.py: copy it over")
	}
}

package main

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

func TestWalkKeepsOnlyPhotosOriginals(t *testing.T) {
	root := t.TempDir()
	for _, f := range []string{
		"Holiday/beach.jpg",
		".localized",
		"Photos Library.photoslibrary/originals/A/1.heic",
		"Photos Library.photoslibrary/database/Photos.sqlite",
		"Photos Library.photoslibrary/resources/derivatives/1.jpg",
		"Old.photoslibrary/Masters/2014/01/02/IMG_1.JPG",
		"Old.photoslibrary/Thumbnails/1.jpg",
	} {
		p := filepath.Join(root, filepath.FromSlash(f))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(f), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	var files []string
	if err := walk(Src{Prefix: "xdg/PICTURES", Path: root}, func(e entry) error {
		if e.info.Mode().IsRegular() {
			files = append(files, e.arc)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	sort.Strings(files)
	want := []string{
		"xdg/PICTURES/Holiday/beach.jpg",
		"xdg/PICTURES/Old.photoslibrary/Masters/2014/01/02/IMG_1.JPG",
		"xdg/PICTURES/Photos Library.photoslibrary/originals/A/1.heic",
	}
	if !reflect.DeepEqual(files, want) {
		t.Fatalf("got %q\nwant %q", files, want)
	}
}

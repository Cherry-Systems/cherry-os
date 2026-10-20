package main

// Transfer codes and encryption, matching desktop/lib/cherryos/codes.py:
// 20 random bytes plus a checksum byte, written as 34 base32 letters; HKDF-SHA256 gives the transfer id and
// the AES-256-GCM key.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"strings"

	"golang.org/x/crypto/hkdf"
)

const codeLetters = 34

type Keys struct {
	Code string
	ID   string
	aead cipher.AEAD
}

func derive(secret []byte, info string, n int) []byte {
	out := make([]byte, n)
	if _, err := io.ReadFull(hkdf.New(sha256.New, secret, []byte("cherry-move-v1"), []byte(info)), out); err != nil {
		panic(err)
	}
	return out
}

func normaliseCode(code string) string {
	var b strings.Builder
	for _, r := range strings.ToUpper(code) {
		if (r >= 'A' && r <= 'Z') || (r >= '2' && r <= '7') {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func prettyCode(code string) string {
	c := normaliseCode(code)
	var parts []string
	for i := 0; i < len(c); i += 5 {
		parts = append(parts, c[i:min(i+5, len(c))])
	}
	return strings.Join(parts, "-")
}

func newKeys() *Keys {
	secret := make([]byte, 20)
	if _, err := rand.Read(secret); err != nil {
		panic(err)
	}
	sum := sha256.Sum256(secret)
	k, err := keysFromCode(base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(append(secret, sum[0])))
	if err != nil {
		panic(err)
	}
	return k
}

func keysFromCode(code string) (*Keys, error) {
	c := normaliseCode(code)
	if len(c) != codeLetters {
		return nil, fmt.Errorf("a transfer code has %d letters and numbers; this one has %d", codeLetters, len(c))
	}
	raw, err := base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(c)
	if err != nil || len(raw) != 21 {
		return nil, errors.New("that transfer code has a typo in it")
	}
	secret := raw[:20]
	if sum := sha256.Sum256(secret); sum[0] != raw[20] {
		return nil, errors.New("that transfer code has a typo in it")
	}
	block, _ := aes.NewCipher(derive(secret, "key", 32))
	aead, _ := cipher.NewGCM(block)
	return &Keys{Code: c, ID: hex.EncodeToString(derive(secret, "id", 16)), aead: aead}, nil
}

func (k *Keys) nonce(index int) []byte {
	n := make([]byte, 12)
	binary.BigEndian.PutUint64(n[4:], uint64(index))
	return n
}

func (k *Keys) aad(index int) []byte { return []byte(fmt.Sprintf("%s:%d", k.ID, index)) }

func (k *Keys) Seal(index int, data []byte) []byte {
	return k.aead.Seal(nil, k.nonce(index), data, k.aad(index))
}

func (k *Keys) Open(index int, data []byte) ([]byte, error) {
	return k.aead.Open(nil, k.nonce(index), data, k.aad(index))
}

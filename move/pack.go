package main

// Chunk compression (first byte says how: 0 raw, 2 zstd), the size estimate and the USB fit check,
// matching desktop/lib/cherryos/transfer.py.

import (
	"errors"
	"io"
	"os"
	"sort"
	"sync"

	"github.com/klauspost/compress/zstd"
)

const (
	chunkSize    = 8 * 1024 * 1024
	stickReserve = 4 * 1024 * 1024
	formatName   = "cherry-move/2"
	markerDir    = "CHERRY-TRANSFER"
	markerName   = "cherry-transfer.json"
	remoteRoot   = "CherryMove"
)

// Compression levels: Normal is quick; Strong is several times slower and only used when it makes the files fit.
type Level int

const (
	Normal Level = 9
	Strong Level = 17
)

var encoders sync.Map // Level -> *sync.Pool of *zstd.Encoder

func encoder(level Level) *zstd.Encoder {
	pool, _ := encoders.LoadOrStore(level, &sync.Pool{New: func() any {
		speed := zstd.SpeedBetterCompression
		if level >= Strong {
			speed = zstd.SpeedBestCompression
		}
		e, err := zstd.NewWriter(nil, zstd.WithEncoderLevel(speed), zstd.WithEncoderConcurrency(1),
			zstd.WithWindowSize(chunkSize))
		if err != nil {
			panic(err)
		}
		return e
	}})
	p := pool.(*sync.Pool)
	return p.Get().(*zstd.Encoder)
}

func release(level Level, e *zstd.Encoder) {
	pool, _ := encoders.Load(level)
	pool.(*sync.Pool).Put(e)
}

// pack compresses one chunk; data that doesn't shrink (photos, videos) is kept as it is.
func pack(plain []byte, level Level) []byte {
	e := encoder(level)
	body := e.EncodeAll(plain, append(make([]byte, 0, len(plain)/2+64), 2))
	release(level, e)
	if float64(len(body)) < float64(len(plain))*0.97 {
		return body
	}
	return append([]byte{0}, plain...)
}

var decoder, _ = zstd.NewReader(nil, zstd.WithDecoderConcurrency(0))

func unpack(body []byte) ([]byte, error) {
	if len(body) == 0 {
		return nil, errors.New("empty chunk")
	}
	switch body[0] {
	case 0:
		return body[1:], nil
	case 2:
		return decoder.DecodeAll(body[1:], nil)
	}
	return nil, errors.New("this transfer was made by a newer Cherry Move")
}

func sample(path string) []byte {
	const piece = 512 * 1024
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	data := make([]byte, piece)
	n, _ := io.ReadFull(f, data)
	data = data[:n]
	if st, err := f.Stat(); err == nil && st.Size() > piece*2 {
		more := make([]byte, piece)
		if m, _ := f.ReadAt(more, st.Size()/2); m > 0 {
			data = append(data, more[:m]...)
		}
	}
	return data
}

// estimate guesses how many bytes Send stores at each level by compressing samples of each kind of file.
// It errs on the large side; already-compressed files (photos, videos, archives) barely shrink.
func estimate(p *Plan) map[Level]int64 {
	const budget = 48 * 1024 * 1024
	total := max(p.Size, 1)
	type piece struct {
		ext  string
		data []byte
	}
	var pieces []piece
	used := 0
	exts := make([]string, 0, len(p.Types))
	for ext := range p.Types {
		exts = append(exts, ext)
	}
	sort.Slice(exts, func(i, j int) bool { return p.Types[exts[i]].Bytes > p.Types[exts[j]].Bytes })
	for _, ext := range exts {
		t := p.Types[ext]
		if used >= budget {
			break
		}
		if float64(t.Bytes) < float64(total)*0.001 {
			continue
		}
		for _, s := range t.Samples {
			if d := sample(s); len(d) > 0 {
				pieces = append(pieces, piece{ext, d})
				used += len(d)
			}
		}
	}
	result := map[Level]int64{}
	for _, level := range []Level{Normal, Strong} {
		packed := make([]int, len(pieces))
		var wg sync.WaitGroup
		sem := make(chan struct{}, 4)
		for i := range pieces {
			wg.Add(1)
			sem <- struct{}{}
			go func(i int) {
				defer wg.Done()
				packed[i] = len(pack(pieces[i].data, level))
				<-sem
			}(i)
		}
		wg.Wait()
		seen := map[string][2]float64{}
		var rawAll, packedAll float64
		for i, pc := range pieces {
			s := seen[pc.ext]
			s[0] += float64(len(pc.data))
			s[1] += float64(packed[i])
			seen[pc.ext] = s
			rawAll += float64(len(pc.data))
			packedAll += float64(packed[i])
		}
		overall := 1.0
		if rawAll > 0 {
			overall = packedAll / rawAll
		}
		stored := 0.0
		for ext, t := range p.Types {
			ratio := overall
			if s, ok := seen[ext]; ok && s[0] > 0 {
				ratio = s[1] / s[0]
			}
			stored += float64(t.Bytes) * ratio
		}
		stored += float64(p.Files)*300 + (stored/chunkSize+1)*64
		result[level] = int64(stored*1.02) + 16*1024*1024
	}
	return result
}

type Fit struct {
	Level  Level `json:"level"`
	Need   int64 `json:"need"`
	Sticks int   `json:"sticks"`
}

// fit picks the level for a stick with `free` bytes (normal if that fits, strong if it fits or saves a stick)
// and how many sticks that size it takes.
func fit(sizes map[Level]int64, free int64) Fit {
	room := max(free-stickReserve, 1)
	sticks := func(l Level) int {
		need := int64(float64(sizes[l]) * 1.01)
		return int(max(1, (need+room-1)/room))
	}
	n, s := sticks(Normal), sticks(Strong)
	if n <= s {
		return Fit{Normal, sizes[Normal], n}
	}
	return Fit{Strong, sizes[Strong], s}
}

// biggerStick is the free space a single stick needs for these files.
func biggerStick(need int64) int64 { return int64(float64(need)*1.05) + stickReserve }

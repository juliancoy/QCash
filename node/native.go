package main

/*
#cgo CFLAGS: -I${SRCDIR}/../include
#cgo LDFLAGS: ${SRCDIR}/../build/libpqcoin_app.a ${SRCDIR}/../build/libpqcoin_ledger.a ${SRCDIR}/../build/libpqcoin_crypto.a ${SRCDIR}/../.deps/openssl/lib/libcrypto.a -lstdc++ -ldl -lpthread -lm
#include <pqcoin/app.h>
#include <stdlib.h>
*/
import "C"
import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	abci "github.com/cometbft/cometbft/abci/types"
	"sync"
	"unsafe"
)

const maxBlockTxs = 128
const maxBlockBytes = 1024 * 1024
const transferBytes = 5472

type application struct {
	abci.BaseApplication
	mu      sync.Mutex
	ptr     unsafe.Pointer
	genesis []byte
	chain   string
}

func data(b []byte) *C.uchar {
	if len(b) == 0 {
		return nil
	}
	return (*C.uchar)(unsafe.Pointer(&b[0]))
}
func nativeOK(n C.int) error {
	if n < 0 {
		return errors.New("native application/storage operation failed")
	}
	return nil
}
func newApplication(directory string, genesis []byte, chain string) (*application, error) {
	dir := C.CString(directory)
	defer C.free(unsafe.Pointer(dir))
	ptr := C.pq_open(dir, data(genesis), C.size_t(len(genesis)))
	if ptr == nil {
		return nil, errors.New("cannot open native application snapshot/genesis")
	}
	return &application{ptr: ptr, genesis: genesis, chain: chain}, nil
}
func (a *application) close() { C.pq_close(a.ptr); a.ptr = nil }
func (a *application) info() (int64, []byte, error) {
	var height C.uint64_t
	out := make([]byte, 32)
	err := nativeOK(C.pq_info(a.ptr, &height, data(out)))
	return int64(height), out, err
}
func (a *application) Info(context.Context, *abci.RequestInfo) (*abci.ResponseInfo, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	h, sum, err := a.info()
	return &abci.ResponseInfo{Data: "pqcoin C++ ledger", Version: "0.2.0", AppVersion: 2, LastBlockHeight: h, LastBlockAppHash: sum}, err
}
func (a *application) InitChain(_ context.Context, r *abci.RequestInitChain) (*abci.ResponseInitChain, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	var state genesisState
	if err := json.Unmarshal(r.AppStateBytes, &state); err != nil {
		return nil, err
	}
	if r.ChainId != a.chain || !bytes.Equal(state.Native, a.genesis) || r.InitialHeight != 1 {
		return nil, errors.New("genesis identity mismatch")
	}
	_, sum, err := a.info()
	return &abci.ResponseInitChain{AppHash: sum}, err
}
func (a *application) CheckTx(_ context.Context, r *abci.RequestCheckTx) (*abci.ResponseCheckTx, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(r.Tx) != transferBytes {
		return &abci.ResponseCheckTx{Code: 1, Log: "invalid transaction length"}, nil
	}
	n := C.pq_check(a.ptr, data(r.Tx), C.size_t(len(r.Tx)))
	if err := nativeOK(n); err != nil {
		return nil, err
	}
	code := uint32(0)
	if n == 0 {
		code = 1
	}
	return &abci.ResponseCheckTx{Code: code, GasWanted: 1}, nil
}
func (a *application) PrepareProposal(_ context.Context, r *abci.RequestPrepareProposal) (*abci.ResponsePrepareProposal, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := nativeOK(C.pq_reset(a.ptr)); err != nil {
		return nil, err
	}
	limit := r.MaxTxBytes
	if limit > maxBlockBytes {
		limit = maxBlockBytes
	}
	var txs [][]byte
	var size int64
	for _, tx := range r.Txs {
		if len(txs) >= maxBlockTxs {
			break
		}
		if len(tx) != transferBytes || int64(len(tx)) > limit-size {
			continue
		}
		n := C.pq_apply(a.ptr, data(tx), C.size_t(len(tx)))
		if err := nativeOK(n); err != nil {
			return nil, err
		}
		if n == 1 {
			txs = append(txs, tx)
			size += int64(len(tx))
		}
	}
	return &abci.ResponsePrepareProposal{Txs: txs}, nil
}
func (a *application) ProcessProposal(_ context.Context, r *abci.RequestProcessProposal) (*abci.ResponseProcessProposal, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	reject := &abci.ResponseProcessProposal{Status: abci.ResponseProcessProposal_REJECT}
	if len(r.Txs) > maxBlockTxs {
		return reject, nil
	}
	if err := nativeOK(C.pq_reset(a.ptr)); err != nil {
		return nil, err
	}
	size := 0
	for _, tx := range r.Txs {
		size += len(tx)
		if len(tx) != transferBytes || size > maxBlockBytes {
			return reject, nil
		}
		n := C.pq_apply(a.ptr, data(tx), C.size_t(len(tx)))
		if err := nativeOK(n); err != nil {
			return nil, err
		}
		if n != 1 {
			return reject, nil
		}
	}
	return &abci.ResponseProcessProposal{Status: abci.ResponseProcessProposal_ACCEPT}, nil
}
func (a *application) FinalizeBlock(_ context.Context, r *abci.RequestFinalizeBlock) (*abci.ResponseFinalizeBlock, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if r.Height <= 0 || len(r.Txs) > maxBlockTxs {
		return nil, errors.New("invalid finalized block bounds")
	}
	if err := nativeOK(C.pq_reset(a.ptr)); err != nil {
		return nil, err
	}
	results := make([]*abci.ExecTxResult, len(r.Txs))
	size := 0
	for i, tx := range r.Txs {
		size += len(tx)
		if len(tx) != transferBytes || size > maxBlockBytes {
			return nil, errors.New("invalid finalized transaction bounds")
		}
		n := C.pq_apply(a.ptr, data(tx), C.size_t(len(tx)))
		if err := nativeOK(n); err != nil {
			return nil, err
		}
		code := uint32(0)
		if n != 1 {
			code = 1
		}
		results[i] = &abci.ExecTxResult{Code: code, GasUsed: 1}
	}
	sum := make([]byte, 32)
	if err := nativeOK(C.pq_stage(a.ptr, C.uint64_t(r.Height), data(sum))); err != nil {
		return nil, err
	}
	return &abci.ResponseFinalizeBlock{TxResults: results, AppHash: sum}, nil
}
func (a *application) Commit(context.Context, *abci.RequestCommit) (*abci.ResponseCommit, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return &abci.ResponseCommit{}, nativeOK(C.pq_commit(a.ptr))
}
func (a *application) Query(_ context.Context, r *abci.RequestQuery) (*abci.ResponseQuery, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	height, sum, err := a.info()
	if err != nil {
		return nil, err
	}
	out := &abci.ResponseQuery{Height: height}
	if r.Prove || (r.Height != 0 && r.Height != height) {
		out.Code = 1
		out.Log = "historical queries and proofs are not supported"
		return out, nil
	}
	switch r.Path {
	case "/state":
		out.Value = []byte(fmt.Sprintf(`{"height":%d,"app_hash":"%s"}`, height, hex.EncodeToString(sum)))
	case "/account":
		if len(r.Data) != 32 {
			out.Code = 1
			return out, nil
		}
		var balance, nonce C.uint64_t
		n := C.pq_account(a.ptr, data(r.Data), &balance, &nonce)
		if err := nativeOK(n); err != nil {
			return nil, err
		}
		if n == 0 {
			out.Code = 1
			out.Log = "account not found"
			return out, nil
		}
		out.Value = []byte(fmt.Sprintf(`{"balance":"%d","nonce":"%d"}`, uint64(balance), uint64(nonce)))
	default:
		out.Code = 1
		out.Log = "unknown query path"
	}
	return out, nil
}

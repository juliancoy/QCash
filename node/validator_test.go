package main

import (
	"bytes"
	"github.com/cometbft/cometbft/crypto/mldsa65"
	"github.com/cometbft/cometbft/privval"
	proto "github.com/cometbft/cometbft/proto/tendermint/types"
	"github.com/cometbft/cometbft/types"
	"path/filepath"
	"testing"
	"time"
)

func TestMLDSAPersistentDoubleSignProtection(t *testing.T) {
	secret, err := mldsa65.GenPrivKey()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	keyPath := filepath.Join(dir, "key.json")
	statePath := filepath.Join(dir, "sign-state.json")
	signer := privval.NewFilePV(secret, keyPath, statePath)
	signer.Save()
	vote := &proto.Vote{Type: proto.PrecommitType, Height: 1, Round: 0, Timestamp: time.Now().UTC(),
		ValidatorAddress: secret.PubKey().Address(), ValidatorIndex: 0,
		BlockID: proto.BlockID{Hash: bytes.Repeat([]byte{1}, 32), PartSetHeader: proto.PartSetHeader{Total: 1, Hash: bytes.Repeat([]byte{2}, 32)}}}
	if err = signer.SignVote("pqcoin-dev-test", vote); err != nil {
		t.Fatal(err)
	}
	if len(vote.Signature) != 3309 || !secret.PubKey().VerifySignature(types.VoteSignBytes("pqcoin-dev-test", vote), vote.Signature) {
		t.Fatal("invalid ML-DSA vote")
	}
	if secret.PubKey().VerifySignature(types.VoteSignBytes("pqcoin-dev-foreign", vote), vote.Signature) {
		t.Fatal("cross-chain vote accepted")
	}
	tampered := append([]byte(nil), vote.Signature...)
	tampered[0] ^= 1
	if secret.PubKey().VerifySignature(types.VoteSignBytes("pqcoin-dev-test", vote), tampered) {
		t.Fatal("forged signature accepted")
	}
	restored := privval.LoadFilePV(keyPath, statePath)
	conflict := *vote
	conflict.BlockID.Hash = bytes.Repeat([]byte{3}, 32)
	conflict.Signature = nil
	if err = restored.SignVote("pqcoin-dev-test", &conflict); err == nil {
		t.Fatal("double sign after restart accepted")
	}
	repeated := *vote
	repeated.Signature = nil
	if err = restored.SignVote("pqcoin-dev-test", &repeated); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(repeated.Signature, vote.Signature) {
		t.Fatal("same vote did not reuse persisted signature")
	}
	lower := *vote
	lower.Height = 0
	lower.Signature = nil
	if err = restored.SignVote("pqcoin-dev-test", &lower); err == nil {
		t.Fatal("height regression accepted")
	}
}

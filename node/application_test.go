package main

import (
	"bytes"
	"context"
	abci "github.com/cometbft/cometbft/abci/types"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestABCIProposalAndCommitIsolation(t *testing.T) {
	root, _ := filepath.Abs("..")
	directory := t.TempDir()
	fixture := filepath.Join(directory, "fixture")
	command := exec.Command(filepath.Join(root, "build/pqcoin-consensus-fixture"), filepath.Join(root, "artifacts/devnet-genesis/genesis.bin"), fixture)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("fixture: %v %s", err, output)
	}
	genesis, chain, err := readGenesis(filepath.Join(fixture, "app-genesis.bin"))
	if err != nil {
		t.Fatal(err)
	}
	app, err := newApplication(filepath.Join(directory, "app"), genesis, chain)
	if err != nil {
		t.Fatal(err)
	}
	defer app.close()
	tx0, err := os.ReadFile(filepath.Join(fixture, "tx0.bin"))
	if err != nil {
		t.Fatal(err)
	}
	tx1, err := os.ReadFile(filepath.Join(fixture, "tx1.bin"))
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	prepared, err := app.PrepareProposal(ctx, &abci.RequestPrepareProposal{Txs: [][]byte{tx0, tx0, tx1}, MaxTxBytes: maxBlockBytes})
	if err != nil || len(prepared.Txs) != 2 {
		t.Fatalf("proposal filtering failed: %v", err)
	}
	duplicate, err := app.ProcessProposal(ctx, &abci.RequestProcessProposal{Txs: [][]byte{tx0, tx0}})
	if err != nil || duplicate.Status != abci.ResponseProcessProposal_REJECT {
		t.Fatal("duplicate accepted")
	}
	sequential, err := app.ProcessProposal(ctx, &abci.RequestProcessProposal{Txs: [][]byte{tx0, tx1}})
	if err != nil || sequential.Status != abci.ResponseProcessProposal_ACCEPT {
		t.Fatal("valid sequence rejected")
	}
	finalized, err := app.FinalizeBlock(ctx, &abci.RequestFinalizeBlock{Height: 1, Txs: [][]byte{tx0, tx1}})
	if err != nil || len(finalized.TxResults) != 2 || finalized.TxResults[1].Code != 0 {
		t.Fatalf("finalization: %v", err)
	}
	before, err := app.Info(ctx, &abci.RequestInfo{})
	if err != nil || before.LastBlockHeight != 0 {
		t.Fatal("FinalizeBlock persisted state")
	}
	_, err = app.PrepareProposal(ctx, &abci.RequestPrepareProposal{Txs: [][]byte{tx0}, MaxTxBytes: maxBlockBytes})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = app.Commit(ctx, &abci.RequestCommit{}); err != nil {
		t.Fatal(err)
	}
	after, err := app.Info(ctx, &abci.RequestInfo{})
	if err != nil || after.LastBlockHeight != 1 || !bytes.Equal(after.LastBlockAppHash, finalized.AppHash) {
		t.Fatal("proposal clobbered finalized state")
	}
	replay, err := app.CheckTx(ctx, &abci.RequestCheckTx{Tx: tx0})
	if err != nil || replay.Code == 0 {
		t.Fatal("replay accepted")
	}
	proof, err := app.Query(ctx, &abci.RequestQuery{Path: "/state", Prove: true})
	if err != nil || proof.Code == 0 {
		t.Fatal("unsupported proof accepted")
	}
}

package main

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"github.com/cometbft/cometbft/crypto/mldsa65"
	cmtjson "github.com/cometbft/cometbft/libs/json"
	ctypes "github.com/cometbft/cometbft/rpc/core/types"
	"github.com/cometbft/cometbft/types"
	"os"
)

// verify-commit uses the pinned genesis validator set as its trust anchor.
// Validator updates are intentionally unsupported by this application.
func verifyCertificate(args []string) error {
	flags := flag.NewFlagSet("verify-commit", flag.ContinueOnError)
	genesisPath := flags.String("genesis", "", "trusted CometBFT genesis")
	commitPath := flags.String("commit", "", "RPC commit result JSON")
	if err := flags.Parse(args); err != nil {
		return err
	}
	doc, err := types.GenesisDocFromFile(*genesisPath)
	if err != nil {
		return err
	}
	validators := make([]*types.Validator, len(doc.Validators))
	for i, v := range doc.Validators {
		if v.PubKey.Type() != mldsa65.KeyType || v.Power != 1 {
			return errors.New("unexpected validator policy")
		}
		validators[i] = types.NewValidator(v.PubKey, v.Power)
	}
	if len(validators) < 4 || len(validators) > 16 {
		return errors.New("invalid validator set")
	}
	raw, err := os.ReadFile(*commitPath)
	if err != nil {
		return err
	}
	var result ctypes.ResultCommit
	if err = cmtjson.Unmarshal(raw, &result); err != nil {
		return err
	}
	if err = result.SignedHeader.ValidateBasic(doc.ChainID); err != nil {
		return err
	}
	set := types.NewValidatorSet(validators)
	if !bytes.Equal(result.Header.ValidatorsHash, set.Hash()) {
		return errors.New("header validator commitment mismatch")
	}
	if err = set.VerifyCommit(doc.ChainID, result.Commit.BlockID, result.Commit.Height, result.Commit); err != nil {
		return err
	}
	fmt.Printf("Verified ML-DSA-65 quorum certificate at height %d: %X\n", result.Header.Height, result.Header.Hash())
	return nil
}

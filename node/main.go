// pqcoind reuses the pinned, unmodified CometBFT consensus engine. The ABCI
// adapter calls the native C++ transaction/ledger application through a C ABI.
package main

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	cfg "github.com/cometbft/cometbft/config"
	"github.com/cometbft/cometbft/crypto/mldsa65"
	"github.com/cometbft/cometbft/libs/log"
	"github.com/cometbft/cometbft/node"
	"github.com/cometbft/cometbft/p2p"
	"github.com/cometbft/cometbft/privval"
	"github.com/cometbft/cometbft/proxy"
	"github.com/cometbft/cometbft/types"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

type genesisState struct {
	Native []byte `json:"native_genesis"`
}
type runtimeConfig struct {
	RPC   int    `json:"rpc_port"`
	P2P   int    `json:"p2p_port"`
	Peers string `json:"peers"`
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func writeJSON(path string, value any) {
	data, err := json.MarshalIndent(value, "", "  ")
	must(err)
	must(os.WriteFile(path, append(data, '\n'), 0600))
}
func readGenesis(path string) ([]byte, string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	prefix := []byte("pqcoin/app-genesis/v1\x00")
	offset := len(prefix) + 32
	if len(raw) > 512*1024 || len(raw) < offset+4 || !bytes.HasPrefix(raw, prefix) {
		return nil, "", errors.New("invalid native genesis")
	}
	n := int(binary.BigEndian.Uint32(raw[offset : offset+4]))
	offset += 4
	if n < 12 || n > 50 || len(raw) < offset+n {
		return nil, "", errors.New("invalid native chain identity")
	}
	return raw, string(raw[offset : offset+n]), nil
}
func initNetwork(args []string) error {
	flags := flag.NewFlagSet("init", flag.ContinueOnError)
	root := flags.String("root", "", "new network directory")
	genesisPath := flags.String("app-genesis", "", "native application genesis file")
	count := flags.Int("validators", 4, "fixed validator count (4-16)")
	base := flags.Int("base-port", 28650, "first P2P port; RPC uses the next port")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *root == "" || *genesisPath == "" || *count < 4 || *count > 16 || *base < 1024 || *base+2*(*count) > 65535 {
		return errors.New("init requires --root NEW_DIRECTORY --app-genesis FILE; validators 4-16")
	}
	raw, chain, err := readGenesis(*genesisPath)
	if err != nil {
		return err
	}
	if err = os.Mkdir(*root, 0700); err != nil {
		return err
	}
	test, err := newApplication(filepath.Join(*root, "genesis-validation"), raw, chain)
	if err != nil {
		return err
	}
	test.close()
	must(os.RemoveAll(filepath.Join(*root, "genesis-validation")))
	doc := &types.GenesisDoc{GenesisTime: time.Now().UTC(), ChainID: chain, InitialHeight: 1, ConsensusParams: types.DefaultConsensusParams()}
	doc.ConsensusParams.Validator.PubKeyTypes = []string{mldsa65.KeyType}
	doc.ConsensusParams.Block.MaxBytes = maxBlockBytes
	state, err := json.Marshal(genesisState{Native: raw})
	if err != nil {
		return err
	}
	doc.AppState = state
	ids := make([]string, *count)
	for i := 0; i < *count; i++ {
		home := filepath.Join(*root, fmt.Sprintf("node%d", i))
		c := cfg.DefaultConfig().SetRoot(home)
		cfg.EnsureRoot(home)
		secret, err := mldsa65.GenPrivKey()
		if err != nil {
			return err
		}
		pv := privval.NewFilePV(secret, c.PrivValidatorKeyFile(), c.PrivValidatorStateFile())
		pv.Save()
		pub := secret.PubKey()
		doc.Validators = append(doc.Validators, types.GenesisValidator{Address: pub.Address(), PubKey: pub, Power: 1, Name: fmt.Sprintf("validator%d", i)})
		key, err := p2p.LoadOrGenNodeKey(c.NodeKeyFile())
		if err != nil {
			return err
		}
		ids[i] = string(key.ID())
		must(os.WriteFile(filepath.Join(home, "config", "app-genesis.bin"), raw, 0600))
	}
	for i := 0; i < *count; i++ {
		home := filepath.Join(*root, fmt.Sprintf("node%d", i))
		c := cfg.DefaultConfig().SetRoot(home)
		var peers []string
		for j, id := range ids {
			if j != i {
				peers = append(peers, fmt.Sprintf("%s@127.0.0.1:%d", id, *base+2*j))
			}
		}
		writeJSON(filepath.Join(home, "config", "runtime.json"), runtimeConfig{RPC: *base + 2*i + 1, P2P: *base + 2*i, Peers: strings.Join(peers, ",")})
		if err := doc.SaveAs(c.GenesisFile()); err != nil {
			return err
		}
	}
	fmt.Printf("Initialized %d ML-DSA-65 validators for %s in %s\n", *count, chain, *root)
	return nil
}
func start(args []string) error {
	flags := flag.NewFlagSet("start", flag.ContinueOnError)
	home := flags.String("home", "", "node home")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *home == "" {
		return errors.New("start requires --home")
	}
	lock, err := os.OpenFile(filepath.Join(*home, "node.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return errors.New("node home already in use")
	}
	c := cfg.DefaultConfig().SetRoot(*home)
	for _, path := range []string{c.PrivValidatorKeyFile(), c.PrivValidatorStateFile(), c.NodeKeyFile(), c.GenesisFile()} {
		if _, err := os.Stat(path); err != nil {
			return fmt.Errorf("required existing validator state: %w", err)
		}
	}
	raw, chain, err := readGenesis(filepath.Join(*home, "config", "app-genesis.bin"))
	if err != nil {
		return err
	}
	genesis, err := types.GenesisDocFromFile(c.GenesisFile())
	if err != nil {
		return err
	}
	var state genesisState
	if err = json.Unmarshal(genesis.AppState, &state); err != nil {
		return err
	}
	if genesis.ChainID != chain || !bytes.Equal(raw, state.Native) || len(genesis.ConsensusParams.Validator.PubKeyTypes) != 1 || genesis.ConsensusParams.Validator.PubKeyTypes[0] != mldsa65.KeyType {
		return errors.New("chain identity/key policy mismatch")
	}
	for _, v := range genesis.Validators {
		if v.PubKey.Type() != mldsa65.KeyType || v.Power != 1 {
			return errors.New("unexpected validator type or voting power")
		}
	}
	var runtime runtimeConfig
	data, err := os.ReadFile(filepath.Join(*home, "config", "runtime.json"))
	if err != nil {
		return err
	}
	if err = json.Unmarshal(data, &runtime); err != nil {
		return err
	}
	c.Moniker = filepath.Base(*home)
	c.RPC.ListenAddress = fmt.Sprintf("tcp://127.0.0.1:%d", runtime.RPC)
	c.P2P.ListenAddress = fmt.Sprintf("tcp://127.0.0.1:%d", runtime.P2P)
	c.P2P.PersistentPeers = runtime.Peers
	c.P2P.AllowDuplicateIP = true
	c.P2P.AddrBookStrict = false
	c.P2P.PexReactor = false
	c.RPC.Unsafe = false
	c.Mempool.MaxTxBytes = transferBytes
	c.Mempool.MaxTxsBytes = 8 * 1024 * 1024
	c.Mempool.Size = 1024
	c.Consensus.TimeoutCommit = 500 * time.Millisecond
	c.Consensus.CreateEmptyBlocks = true
	if err = c.ValidateBasic(); err != nil {
		return err
	}
	app, err := newApplication(filepath.Join(*home, "data", "application"), raw, chain)
	if err != nil {
		return err
	}
	defer app.close()
	pv := privval.LoadFilePV(c.PrivValidatorKeyFile(), c.PrivValidatorStateFile())
	pub, err := pv.GetPubKey()
	if err != nil {
		return err
	}
	if pub.Type() != mldsa65.KeyType {
		return errors.New("validator must use ML-DSA-65")
	}
	key, err := p2p.LoadNodeKey(c.NodeKeyFile())
	if err != nil {
		return err
	}
	logger := log.NewFilter(log.NewTMLogger(log.NewSyncWriter(os.Stdout)), log.AllowError())
	n, err := node.NewNode(c, pv, key, proxy.NewLocalClientCreator(app), node.DefaultGenesisDocProviderFunc(c), cfg.DefaultDBProvider, node.DefaultMetricsProvider(c.Instrumentation), logger)
	if err != nil {
		return err
	}
	if err = n.Start(); err != nil {
		return err
	}
	fmt.Printf("pqcoind started: %s, ML-DSA-65 validator, RPC %s\n", c.Moniker, c.RPC.ListenAddress)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(signals)
	<-signals
	err = n.Stop()
	n.Wait()
	return err
}
func defaultGenesis(args []string) error {
	flags := flag.NewFlagSet("empty-genesis", flag.ContinueOnError)
	bootstrap := flags.String("bootstrap-hash", "", "64-character existing genesis hash")
	out := flags.String("out", "", "new output file")
	if err := flags.Parse(args); err != nil {
		return err
	}
	anchor, err := hex.DecodeString(*bootstrap)
	if err != nil || len(anchor) != 32 || *out == "" {
		return errors.New("requires --bootstrap-hash HEX --out NEW_FILE")
	}
	var random [8]byte
	if _, err = rand.Read(random[:]); err != nil {
		return err
	}
	chain := "pqcoin-dev-" + hex.EncodeToString(random[:])
	raw := append([]byte("pqcoin/app-genesis/v1\x00"), anchor...)
	raw = binary.BigEndian.AppendUint32(raw, uint32(len(chain)))
	raw = append(raw, []byte(chain)...)
	raw = binary.BigEndian.AppendUint32(raw, 0)
	f, err := os.OpenFile(*out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(raw)
	return err
}
func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: pqcoind init|start|empty-genesis|verify-commit ...")
		os.Exit(1)
	}
	var err error
	switch os.Args[1] {
	case "init":
		err = initNetwork(os.Args[2:])
	case "start":
		err = start(os.Args[2:])
	case "verify-commit":
		err = verifyCertificate(os.Args[2:])
	case "empty-genesis":
		err = defaultGenesis(os.Args[2:])
	default:
		err = errors.New("unknown command")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

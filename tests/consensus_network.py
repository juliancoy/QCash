#!/usr/bin/env python3
"""Four real processes: ML-DSA votes, ledger agreement, quorum loss and recovery."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def run_test(report_path):
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="consensus-", dir=ROOT / ".deps"))
    processes = {}
    logs = {}
    report = {"work_directory": str(work), "validator_signature": "ML-DSA-65", "validators": 4}
    base = None
    for candidate in range(30000, 59000, 17):
        reserved = []
        try:
            for port in range(candidate, candidate + 8):
                sock = socket.socket()
                reserved.append(sock)
                sock.bind(("127.0.0.1", port))
            base = candidate
            break
        except OSError:
            pass
        finally:
            for sock in reserved:
                sock.close()
    if base is None:
        raise RuntimeError("cannot allocate local ports")

    def rpc(index, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{base + index * 2 + 1}", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=12) as response:
            value = json.load(response)
        if "error" in value:
            raise RuntimeError(value["error"])
        return value["result"]

    def wait_for(description, operation, timeout=45):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            for index, process in processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f"node{index} exited: {(work / f'node{index}.log').read_text()[-4000:]}")
            try:
                value = operation()
                if value:
                    print(description, flush=True)
                    return value
            except (OSError, ValueError, RuntimeError) as error:
                last_error = error
            time.sleep(0.15)
        raise RuntimeError(f"timeout: {description}; {last_error}")

    def start(index):
        logs[index] = open(work / f"node{index}.log", "ab", buffering=0)
        environment = dict(os.environ, GOMAXPROCS="2")
        processes[index] = subprocess.Popen([str(ROOT / "build/pqcoind"), "start", "--home",
                                             str(work / "network" / f"node{index}")],
                                            stdout=logs[index], stderr=subprocess.STDOUT, env=environment)

    def stop(index, crash=False):
        process = processes.pop(index)
        process.send_signal(signal.SIGKILL if crash else signal.SIGTERM)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        logs.pop(index).close()

    def height(index):
        return int(rpc(index, "status")["sync_info"]["latest_block_height"])

    def broadcast(index, number):
        tx = (work / "fixture" / f"tx{number}.bin").read_bytes()
        result = rpc(index, "broadcast_tx_commit", {"tx": base64.b64encode(tx).decode()})
        assert result["check_tx"]["code"] == 0 and result["tx_result"]["code"] == 0, result
        return int(result["height"])

    def query(index, owner):
        response = rpc(index, "abci_query", {"path": "/account", "data": owner, "height": "0", "prove": False})["response"]
        assert response["code"] == 0, response
        return json.loads(base64.b64decode(response["value"]))

    def ready(indices, minimum_height):
        if any(rpc(i, "status")["sync_info"]["catching_up"] for i in indices):
            return False
        return agreement(indices, minimum_height)

    def agreement(indices, minimum_height):
        if any(height(i) < minimum_height for i in indices):
            return False
        common = min(height(i) for i in indices)
        blocks = [rpc(i, "block", {"height": str(common)}) for i in indices]
        hashes = {b["block_id"]["hash"] for b in blocks}
        app_hashes = {b["block"]["header"]["app_hash"] for b in blocks}
        assert len(hashes) == len(app_hashes) == 1
        return {"height": common, "block_hash": hashes.pop(), "app_hash": app_hashes.pop()}

    try:
        subprocess.run([str(ROOT / "build/pqcoin-consensus-fixture"),
                        str(ROOT / "artifacts/devnet-genesis/genesis.bin"), str(work / "fixture")], check=True)
        subprocess.run([str(ROOT / "build/pqcoind"), "init", "--root", str(work / "network"),
                        "--app-genesis", str(work / "fixture/app-genesis.bin"), "--base-port", str(base)], check=True)
        owners = json.loads((work / "fixture/accounts.json").read_text())
        for i in range(4):
            start(i)
        wait_for("Four validators committed blocks", lambda: agreement(range(4), 2))
        validators = rpc(0, "validators")["validators"]
        assert len(validators) == 4 and all(v["pub_key"]["type"] == "cometbft/PubKeyMlDsa65" for v in validators)
        first = broadcast(0, 0)
        report["initial_agreement"] = wait_for("Signed transfer committed on all four nodes", lambda: agreement(range(4), first + 1))
        assert query(0, owners["alice"]) == {"balance": "999974", "nonce": "1"}
        assert query(0, owners["bob"]) == {"balance": "25", "nonce": "0"}
        certificate = rpc(0, "commit", {"height": str(first)})
        commit = certificate["signed_header"]["commit"]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        public_genesis = report_path.parent / "genesis.json"
        public_genesis.write_bytes((work / "network/node0/config/genesis.json").read_bytes())
        certificate_file = report_path.parent / "commit.json"
        certificate_file.write_text(json.dumps(certificate, indent=2) + "\n")
        subprocess.run([str(ROOT / "build/pqcoind"), "verify-commit", "--genesis", str(public_genesis),
                        "--commit", str(certificate_file)], check=True)
        forged = json.loads(json.dumps(certificate))
        signature = next(s for s in forged["signed_header"]["commit"]["signatures"] if s["block_id_flag"] == 2)
        raw_signature = bytearray(base64.b64decode(signature["signature"]))
        raw_signature[0] ^= 1
        signature["signature"] = base64.b64encode(raw_signature).decode()
        forged_file = work / "forged-commit.json"
        forged_file.write_text(json.dumps(forged))
        negative = subprocess.run([str(ROOT / "build/pqcoind"), "verify-commit", "--genesis", str(public_genesis),
                                   "--commit", str(forged_file)], capture_output=True)
        assert negative.returncode != 0
        report["offline_certificate_verified"] = True
        report["forged_certificate_rejected"] = True
        votes = [v for v in commit["signatures"] if v["block_id_flag"] == 2]
        assert len(votes) >= 3 and all(len(base64.b64decode(v["signature"])) == 3309 for v in votes)
        report["commit_certificate"] = {"height": first, "signatures": len(votes), "signature_bytes": 3309}
        # Replay, a correctly signed foreign-chain transfer, and malformed data.
        for name in ["tx0.bin", "wrong-chain.bin"]:
            raw = (work / "fixture" / name).read_bytes()
            response = rpc(1, "check_tx", {"tx": base64.b64encode(raw).decode()})
            assert response["code"] != 0
        response = rpc(1, "check_tx", {"tx": base64.b64encode(b"malformed").decode()})
        assert response["code"] != 0
        report["invalid_transactions_rejected"] = ["replay", "foreign_chain", "malformed"]
        # An actual process crash, including its persisted validator signing state.
        stop(0, crash=True)
        second = broadcast(1, 1)
        report["one_validator_offline"] = wait_for("Three validators continue with one crashed", lambda: agreement([1, 2, 3], second + 1))
        start(0)
        wait_for("Crashed validator recovered and caught up", lambda: agreement(range(4), second + 2))
        assert query(0, owners["alice"])["nonce"] == "2"
        # Two unavailable validators leave only half the power: no commits.
        stop(0, crash=True)
        stop(1, crash=True)
        time.sleep(2)  # Allow any certificate already in flight to finish.
        before = [height(i) for i in [2, 3]]
        time.sleep(4)
        after = [height(i) for i in [2, 3]]
        assert before == after, (before, after)
        report["quorum_loss"] = {"before": before, "after": after, "halted": True}
        print("Two-validator quorum loss correctly halted commits", flush=True)
        start(1)
        wait_for("Quorum restoration resumed commits", lambda: agreement([1, 2, 3], max(after) + 2))
        start(0)
        third = broadcast(2, 2)
        report["recovered_agreement"] = wait_for("All validators recovered with identical chain and ledger", lambda: agreement(range(4), third + 2))
        for i in range(4):
            assert query(i, owners["alice"]) == {"balance": "999922", "nonce": "3"}
            assert query(i, owners["bob"]) == {"balance": "75", "nonce": "0"}
        # Stop everyone and restart from durable app and engine state.
        for i in list(processes):
            stop(i)
        for i in range(4):
            start(i)
        wait_for("Restarted network is ready", lambda: ready(range(4), third + 2))
        fourth = broadcast(3, 3)
        report["full_restart_agreement"] = wait_for("Full network restart retained ledger and resumed", lambda: agreement(range(4), fourth + 1))
        for i in range(4):
            assert query(i, owners["alice"]) == {"balance": "999896", "nonce": "4"}
            assert query(i, owners["bob"]) == {"balance": "100", "nonce": "0"}
        report["final_accounts"] = {"alice": query(0, owners["alice"]), "bob": query(0, owners["bob"])}
        report["test_genesis_allocation"] = 1000000
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        for i in list(processes):
            stop(i)
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {report_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/consensus-local/report.json")
    run_test(parser.parse_args().report)

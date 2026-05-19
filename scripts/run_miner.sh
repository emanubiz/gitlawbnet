#!/usr/bin/env bash
# Convenience wrapper for running a miner against a local gitlawb-node.
set -euo pipefail

: "${SUBNET_NETUID:?export SUBNET_NETUID first}"
: "${WALLET_NAME:?export WALLET_NAME}"
: "${WALLET_HOTKEY:?export WALLET_HOTKEY}"
: "${GITLAWB_NODE:=http://127.0.0.1:7545}"
: "${GITLAWB_DID:?export GITLAWB_DID (your did:gitlawb:* identifier)}"

exec python neurons/miner.py \
    --netuid "$SUBNET_NETUID" \
    --subtensor.network "${NETWORK:-finney}" \
    --wallet.name "$WALLET_NAME" \
    --wallet.hotkey "$WALLET_HOTKEY" \
    --axon.port "${AXON_PORT:-8091}" \
    --gitlawb.node_url "$GITLAWB_NODE" \
    --gitlawb.did "$GITLAWB_DID" \
    --logging.debug

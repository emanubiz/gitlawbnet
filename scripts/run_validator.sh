#!/usr/bin/env bash
set -euo pipefail

: "${SUBNET_NETUID:?export SUBNET_NETUID first}"
: "${WALLET_NAME:?export WALLET_NAME}"
: "${WALLET_HOTKEY:?export WALLET_HOTKEY}"

exec python neurons/validator.py \
    --netuid "$SUBNET_NETUID" \
    --subtensor.network "${NETWORK:-finney}" \
    --wallet.name "$WALLET_NAME" \
    --wallet.hotkey "$WALLET_HOTKEY" \
    --neuron.sample_size "${SAMPLE_SIZE:-32}" \
    --logging.debug

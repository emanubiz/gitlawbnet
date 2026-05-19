#!/usr/bin/env bash
# Register Gitlawbnet on Bittensor mainnet (finney) and immediately register
# the owner hotkey on the resulting netuid. Idempotent enough to re-run if
# any single step fails — each command checks the chain state first.
#
# Prereqs:
#   - btcli >= 8.x installed (`pip install bittensor-cli`)
#   - Two wallets created locally:
#         btcli wallet new_coldkey --wallet.name gitlawbnet_owner
#         btcli wallet new_hotkey  --wallet.name gitlawbnet_owner --wallet.hotkey owner_hk
#   - Coldkey funded with at least (subnet lock cost + safety buffer) TAO.
#     Check current lock cost with:
#         btcli subnet lock_cost --subtensor.network finney

set -euo pipefail

NETWORK="${NETWORK:-finney}"
WALLET_NAME="${WALLET_NAME:-gitlawbnet_owner}"
WALLET_HOTKEY="${WALLET_HOTKEY:-owner_hk}"
SUBNET_NAME="${SUBNET_NAME:-gitlawbnet}"

echo "=== Current subnet creation lock cost ==="
btcli subnet lock_cost --subtensor.network "$NETWORK"

read -r -p "Proceed with registering '$SUBNET_NAME' on '$NETWORK'? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

echo "=== Creating subnet ==="
# `btcli subnet create` burns the lock cost and returns a fresh netuid that
# belongs to your coldkey. The owner automatically receives 18% of all
# emissions for that netuid (Yuma owner cut).
btcli subnet create \
    --subtensor.network "$NETWORK" \
    --wallet.name "$WALLET_NAME"

echo
echo "=== Listing subnets to find the new netuid ==="
btcli subnet list --subtensor.network "$NETWORK"

read -r -p "Enter the netuid that was just created: " NETUID
[[ "$NETUID" =~ ^[0-9]+$ ]] || { echo "Invalid netuid"; exit 1; }

echo "=== Registering owner hotkey on netuid $NETUID ==="
# This burns the per-netuid registration fee. Owner-side registration is
# optional but recommended — it lets the owner also run a validator if desired.
btcli subnet register \
    --subtensor.network "$NETWORK" \
    --netuid "$NETUID" \
    --wallet.name "$WALLET_NAME" \
    --wallet.hotkey "$WALLET_HOTKEY"

cat <<EOF

============================================================
 Subnet registered.
   network : $NETWORK
   name    : $SUBNET_NAME
   netuid  : $NETUID
   owner   : $WALLET_NAME / $WALLET_HOTKEY

 Next steps
 ----------
 1. Export the netuid so neurons pick it up:
        export SUBNET_NETUID=$NETUID
 2. Set sane hyperparams (immunity period, min_allowed_weights, etc.):
        btcli sudo set --netuid $NETUID --param immunity_period --value 7200
        btcli sudo set --netuid $NETUID --param min_allowed_weights --value 8
        btcli sudo set --netuid $NETUID --param max_weights_limit  --value 65535
 3. Publish the netuid in the README and announce in the Bittensor Discord.
============================================================
EOF

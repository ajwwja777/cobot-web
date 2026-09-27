#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"

if [[ "${1:-}" == --sudo-stdin ]]; then
  # Backward-compatible with a console instance started before the passwordless
  # root helper was installed. Consume and discard the one-shot secret.
  IFS= read -r _DISCARDED_SECRET || true
  unset _DISCARDED_SECRET
  shift
fi
BUS="${1:-}"
case "$BUS" in
  can_left|can_right|can_mid) ;;
  *)
    printf '拒绝复位未登记的 Recover CAN 接口：%s\n' "$BUS" >&2
    exit 2
    ;;
esac
exec sudo -n /usr/local/sbin/cobot-can-recover-one "$BUS"

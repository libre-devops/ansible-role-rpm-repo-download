#!/usr/bin/env bash
#
# Check the Microsoft signing keys bundled in files/ have the fingerprints the role trusts.
# Each key file must have the fingerprint defaults/main.yml trusts it for.
# Needs gpg. Used by `just check-keys` and CI.

set -Eeuo pipefail

readonly EXPECTED=(
  "microsoft.asc BC528686B50D79E339D3721CEB3E94ADBE1229CF"
  "microsoft-2025.asc AA86F75E427A19DD33346403EE4D7792F748182B"
)

#######################################
# Print the fingerprints in a key file, one per line, sorted.
# Arguments:
#   Path to an armoured or binary key file.
# Outputs:
#   Fingerprints on stdout.
#######################################
fingerprints() {
  gpg --batch --quiet --show-keys --with-colons "$1" | awk -F: '$1 == "fpr" { print $10 }' | sort
}

cleanup() {
  [[ -n "${GNUPGHOME:-}" ]] && rm -rf "${GNUPGHOME}"
}

main() {
  # A throwaway keyring, so the check never touches the caller's.
  GNUPGHOME="$(mktemp -d)"
  export GNUPGHOME
  trap cleanup EXIT

  local entry file expected actual
  for entry in "${EXPECTED[@]}"; do
    read -r file expected <<<"${entry}"
    actual="$(fingerprints "files/${file}")"
    if [[ "${actual}" != "${expected}" ]]; then
      echo "files/${file}: expected ${expected}, found ${actual:-nothing}" >&2
      exit 1
    fi
    grep -q "fingerprint: ${expected}" defaults/main.yml || {
      echo "defaults/main.yml does not trust ${expected}" >&2
      exit 1
    }
    echo "files/${file}: ${actual}"
  done
}

main "$@"

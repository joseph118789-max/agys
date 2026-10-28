#!/usr/bin/env bash
set -euo pipefail

REAL_HOME="${HOME}"
BASE_DIR="${REAL_HOME}/.agy"

usage() {
    cat <<'EOF'
Usage:
  agy.sh new <profile>   Create a profile directory
  agy.sh list            List profiles
  agy.sh <profile>       Launch agy with that profile
EOF
}

valid_profile() {
    [[ "$1" =~ ^[a-zA-Z0-9_-]+$ ]]
}

ensure_local_link() {
    local target_dir="$1"
    local real_local="${REAL_HOME}/.local"

    [[ -d "$real_local" ]] || return 0

    if [[ -L "$target_dir/.local" ]]; then
        if [[ ! -e "$target_dir/.local" ]]; then
            rm -f "$target_dir/.local"
            ln -s "$real_local" "$target_dir/.local"
        fi
    elif [[ -d "$target_dir/.local" ]]; then
        rmdir "$target_dir/.local" 2>/dev/null && ln -s "$real_local" "$target_dir/.local" || true
    elif [[ ! -e "$target_dir/.local" ]]; then
        ln -s "$real_local" "$target_dir/.local"
    fi
}

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

command="$1"

case "$command" in
    new)
        [[ $# -eq 2 ]] || { usage; exit 1; }
        profile="$2"

        valid_profile "$profile" || {
            echo "Invalid profile name: $profile" >&2
            exit 1
        }

        dir="${BASE_DIR}-${profile}"

        if [[ -e "$dir" ]]; then
            echo "Profile already exists: $profile"
            exit 1
        fi

        mkdir -p "$dir"
        chmod 700 "$dir"
        ensure_local_link "$dir"
        echo "Created profile: $profile"
        echo "Home: $dir"
        echo "Run: $0 $profile"
        ;;

    list)
        found=0

        for dir in "${BASE_DIR}"-*; do
            [[ -d "$dir" ]] || continue
            printf '%s\n' "${dir##*-}"
            found=1
        done

        if [[ "$found" -eq 0 ]]; then
            echo "No profiles found. Create one with: $0 new <profile>"
        fi
        ;;

    *)
        profile="$command"

        valid_profile "$profile" || {
            echo "Invalid profile name: $profile" >&2
            exit 1
        }

        dir="${BASE_DIR}-${profile}"

        [[ -d "$dir" ]] || {
            echo "Profile not found: $profile"
            echo "Create it with: $0 new $profile"
            exit 1
        }

        command -v agy >/dev/null 2>&1 || {
            echo "Error: agy is not installed or not in PATH." >&2
            exit 1
        }

        ensure_local_link "$dir"

        echo "Launching agy with profile: $profile"
        shift
        exec env HOME="$dir" agy "$@"
        ;;
esac

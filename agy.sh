
#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${HOME}/.agy"

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

        echo "Launching agy with profile: $profile"
        exec env HOME="$dir" agy
        ;;
esac

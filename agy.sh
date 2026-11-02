#!/usr/bin/env bash
set -euo pipefail

REAL_HOME="${HOME}"
# If running inside an isolated profile, resolve actual system user home
if [[ "$REAL_HOME" =~ /\.agy-[^/]+$ ]]; then
    REAL_HOME="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
    [[ -z "$REAL_HOME" ]] && REAL_HOME="$(eval echo ~"$(id -un)" 2>/dev/null || true)"
    [[ -z "$REAL_HOME" ]] && REAL_HOME="${HOME%/.agy-*}"
fi
BASE_DIR="${REAL_HOME}/.agy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
Usage:
  agy.sh new <profile>            Create an isolated profile directory
  agy.sh list                     List existing profiles
  agy.sh sync                     Sync PAB skills, agents, and configuration to all profiles
  agy.sh list-openclaw            List OpenClaw agents and their configured skills
  agy.sh import-skill <agent_id>  Import skills and agent from OpenClaw into Antigravity & PAB
  agy.sh session [args...]        Browse, inspect, move or copy sessions between profiles
  agy.sh consolidate [args...]    Consolidate all conversations into ~/.gemini/antigravity-cli/conversations
  agy.sh single-folder [args...]  Manage shared single-folder conversations mode (status, enable, disable)
  agy.sh <profile>                Launch agy with that profile
EOF
}

valid_profile() {
    [[ "$1" =~ ^[a-zA-Z0-9_-]+$ ]]
}

ensure_symlink() {
    local source="$1"
    local target="$2"

    [[ -e "$source" ]] || return 0
    mkdir -p "$(dirname "$target")"

    if [[ -L "$target" ]]; then
        local current
        current="$(readlink "$target")"
        if [[ "$current" != "$source" ]]; then
            rm -f "$target"
            ln -s "$source" "$target"
        fi
    elif [[ -d "$target" && ! -L "$target" ]]; then
        if [[ -z "$(ls -A "$target" 2>/dev/null)" ]]; then
            rmdir "$target" && ln -s "$source" "$target"
        else
            echo "Notice: Non-empty directory at $target; leaving as-is."
        fi
    elif [[ ! -e "$target" ]]; then
        ln -s "$source" "$target"
    fi
}

ensure_profile_links() {
    local target_dir="$1"

    # 1. Preserve local binaries and libraries (~/.local)
    ensure_symlink "${REAL_HOME}/.local" "$target_dir/.local"

    # 2. Preserve PersonaOS Agent Bridge canonical registry & DB (~/.pab)
    ensure_symlink "${REAL_HOME}/.pab" "$target_dir/.pab"

    # 3. Expose PAB and universal Antigravity skills (~/.gemini/config/skills)
    mkdir -p "${REAL_HOME}/.gemini/config/skills"
    ensure_symlink "${REAL_HOME}/.gemini/config/skills" "$target_dir/.gemini/config/skills"

    # 4. Expose PAB and universal Antigravity agents (~/.gemini/config/agents)
    mkdir -p "${REAL_HOME}/.gemini/config/agents"
    ensure_symlink "${REAL_HOME}/.gemini/config/agents" "$target_dir/.gemini/config/agents"

    # 5. Legacy/CLI skills path (~/.gemini/antigravity-cli/skills)
    ensure_symlink "${REAL_HOME}/.gemini/config/skills" "$target_dir/.gemini/antigravity-cli/skills"

    # 6. Global Antigravity configs if present (hooks, mcp servers)
    [[ -f "${REAL_HOME}/.gemini/config/hooks.json" ]] && \
        ensure_symlink "${REAL_HOME}/.gemini/config/hooks.json" "$target_dir/.gemini/config/hooks.json"
    [[ -f "${REAL_HOME}/.gemini/config/mcp_config.json" ]] && \
        ensure_symlink "${REAL_HOME}/.gemini/config/mcp_config.json" "$target_dir/.gemini/config/mcp_config.json"

    # 7. Single folder conversations mode (if enabled)
    if [[ -f "${REAL_HOME}/.gemini/config/single_folder_conversations" ]]; then
        mkdir -p "${REAL_HOME}/.gemini/antigravity-cli/conversations"
        mkdir -p "${REAL_HOME}/.gemini/antigravity-cli/brain"
        mkdir -p "${REAL_HOME}/.gemini/antigravity-cli/annotations"
        ensure_symlink "${REAL_HOME}/.gemini/antigravity-cli/conversations" "$target_dir/.gemini/antigravity-cli/conversations"
        ensure_symlink "${REAL_HOME}/.gemini/antigravity-cli/brain" "$target_dir/.gemini/antigravity-cli/brain"
        ensure_symlink "${REAL_HOME}/.gemini/antigravity-cli/annotations" "$target_dir/.gemini/antigravity-cli/annotations"
        [[ -f "${REAL_HOME}/.gemini/antigravity-cli/conversation_summaries.db" ]] && \
            ensure_symlink "${REAL_HOME}/.gemini/antigravity-cli/conversation_summaries.db" "$target_dir/.gemini/antigravity-cli/conversation_summaries.db"
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
        ensure_profile_links "$dir"
        echo "Created profile: $profile"
        echo "Home: $dir"
        echo "Run: $0 $profile"
        ;;

    list)
        found=0

        for dir in "${BASE_DIR}"-*; do
            [[ -d "$dir" ]] || continue
            [[ "$dir" == "${BASE_DIR}-shared" ]] && continue
            printf '%s\n' "${dir##*-}"
            found=1
        done

        if [[ "$found" -eq 0 ]]; then
            echo "No profiles found. Create one with: $0 new <profile>"
        fi
        ;;

    sync)
        echo "Synchronising PAB skills, agents, and configuration across all profiles..."
        synced=0
        for dir in "${BASE_DIR}"-*; do
            [[ -d "$dir" ]] || continue
            [[ "$dir" == "${BASE_DIR}-shared" ]] && continue
            p_name="${dir##*-}"
            ensure_profile_links "$dir"
            echo "  [OK] Profile '$p_name' linked with PAB"
            synced=$((synced + 1))
        done
        echo "Sync complete ($synced profiles updated)."
        ;;

    list-openclaw|openclaw-list)
        python3 "${SCRIPT_DIR}/import_openclaw_skills.py" list
        ;;

    import-skill|import-openclaw)
        [[ $# -ge 2 ]] || {
            echo "Usage: $0 import-skill <agent_id|all>" >&2
            echo "Run '$0 list-openclaw' to view available agents." >&2
            exit 1
        }
        agent_id="$2"
        python3 "${SCRIPT_DIR}/import_openclaw_skills.py" "$agent_id"
        ;;

    session|sessions)
        shift
        "${SCRIPT_DIR}/agy-session.sh" "$@"
        ;;

    consolidate|merge-all)
        shift
        "${SCRIPT_DIR}/agy-session.sh" consolidate "$@"
        ;;

    single-folder|singlefolder)
        shift
        "${SCRIPT_DIR}/agy-session.sh" single-folder "$@"
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

        ensure_profile_links "$dir"

        echo "Launching agy with profile: $profile"
        echo "  [PAB] Unified skills & agents active from ${REAL_HOME}/.gemini/config"
        shift
        exec env \
            HOME="$dir" \
            PAB_HOME="${REAL_HOME}/.pab" \
            PATH="${REAL_HOME}/.local/bin:${PATH}" \
            GEMINI_CONFIG_DIR="$dir/.gemini/config" \
            agy "$@"
        ;;
esac

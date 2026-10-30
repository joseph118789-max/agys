#!/usr/bin/env bash
# agy-skill.sh — Synchronise PAB skills and agents to all Antigravity profiles
set -euo pipefail

REAL_HOME="${HOME}"
SHARED_SKILLS="${REAL_HOME}/.agy-shared/skills"
PAB_SKILLS="${REAL_HOME}/.gemini/config/skills"
PAB_AGENTS="${REAL_HOME}/.gemini/config/agents"

mkdir -p "$SHARED_SKILLS" "$PAB_SKILLS" "$PAB_AGENTS"

# If legacy .agy-shared/skills contains any items, sync them to ~/.gemini/config/skills
if [[ -d "$SHARED_SKILLS" ]]; then
    for item in "$SHARED_SKILLS"/*; do
        [[ -d "$item" ]] || continue
        skill_name="$(basename "$item")"
        if [[ ! -e "$PAB_SKILLS/$skill_name" ]]; then
            cp -r "$item" "$PAB_SKILLS/$skill_name"
            echo "Imported legacy shared skill to PAB skills: $skill_name"
        fi
    done
fi

echo "Connecting Antigravity profiles with PAB skills & agents..."

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
            echo "  Notice: Non-empty directory at $target; leaving as-is."
            return 0
        fi
    elif [[ ! -e "$target" ]]; then
        ln -s "$source" "$target"
    fi
}

count=0
for p in "$REAL_HOME"/.agy-*; do
    [[ -d "$p" ]] || continue
    [[ "$p" == "$REAL_HOME/.agy-shared" ]] && continue

    p_name="${p##*-}"

    # 1. Link PAB & Antigravity skills
    ensure_symlink "$PAB_SKILLS" "$p/.gemini/config/skills"

    # 2. Link PAB & Antigravity agents
    ensure_symlink "$PAB_AGENTS" "$p/.gemini/config/agents"

    # 3. Link legacy CLI skills path
    ensure_symlink "$PAB_SKILLS" "$p/.gemini/antigravity-cli/skills"

    # 4. Link PersonaOS Agent Bridge control plane
    ensure_symlink "$REAL_HOME/.pab" "$p/.pab"

    # 5. Link local user binaries
    ensure_symlink "$REAL_HOME/.local" "$p/.local"

    echo "  ✅ Profile '$p_name': Linked skills, agents, and .pab"
    count=$((count + 1))
done

echo "Successfully synchronized $count Antigravity profile(s) with PAB."

# If pab is available, display available skills and agents
if command -v pab >/dev/null 2>&1; then
    echo ""
    echo "PAB Active Skills:"
    pab skill list
    echo ""
    echo "PAB Active Agents:"
    pab agent list
fi

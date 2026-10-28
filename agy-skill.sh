mkdir -p "$HOME/.agy-shared/skills"

for p in "$HOME"/.agy-*; do
    [[ -d "$p" ]] || continue
    [[ "$p" == "$HOME/.agy-shared" ]] && continue

    target="$p/.gemini/antigravity-cli/skills"
    mkdir -p "$(dirname "$target")"

    if [[ -L "$target" ]]; then
        echo "Already linked: $p"
    elif [[ -e "$target" ]]; then
        echo "SKIP (existing directory; inspect first): $target"
    else
        ln -s "$HOME/.agy-shared/skills" "$target"
        echo "Linked: $p"
    fi
done

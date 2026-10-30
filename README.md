# agys

Shell tooling for managing isolated [Antigravity CLI](https://github.com/google-gemini/gemini-cli) (`agy`) profiles — separate `HOME` directories so each project/role keeps its own agent config, sessions, and credentials, while maintaining seamless compatibility with **PersonaOS Agent Bridge (PAB)** skills, agents, and shared memory.

## Requirements

- `bash` 4+
- `agy` (Antigravity CLI) installed and on `PATH` — required for launching a profile
- `pab` (PersonaOS Agent Bridge) installed on `PATH` (optional but recommended for universal multi-platform agent & skill orchestration)

## Scripts

### `agy.sh`

Manage profile directories under `~/.agy-<profile>`.

```bash
./agy.sh new <profile>   # create an isolated profile directory
./agy.sh list            # list existing profiles
./agy.sh sync            # sync/link PAB skills, agents, and config to all profiles
./agy.sh <profile>       # launch agy with that profile
```

Details:

- `new` creates `~/.agy-<profile>` with mode `700`. It automatically configures symlinks for local binaries (`~/.local`), PAB canonical control plane (`~/.pab`), global skills (`~/.gemini/config/skills`), and global agents (`~/.gemini/config/agents`).
- `list` prints the profile name of every `~/.agy-*` directory.
- `sync` traverses all existing `~/.agy-*` profile directories and updates all links to match current PAB skills and agents.
- `<profile>` re-executes `agy` with `HOME` pointed at the profile directory, setting `PAB_HOME` and preserving access to universal tools.

```bash
$ ./agy.sh new work
Created profile: work
Home: /root/.agy-work
Run: ./agy.sh work

$ ./agy.sh list
work

$ ./agy.sh sync
Synchronising PAB skills, agents, and configuration across all profiles...
  [OK] Profile 'work' linked with PAB
Sync complete.

$ ./agy.sh work
Launching agy with profile: work
  [PAB] Unified skills & agents active from /root/.gemini/config
```

### `agy-skill.sh`

Synchronises and links all PAB canonical skills and agents across every Antigravity profile in one command:

```bash
./agy-skill.sh
```

What it does:
1. Links `~/.gemini/config/skills` (where PAB deploys universal Open Agent Skills) into each profile.
2. Links `~/.gemini/config/agents` (where PAB deploys canonical agents like `main`, `devops-engineer`, etc.) into each profile.
3. Links `~/.pab` so running `pab` subcommands from within any profile session shares the central database and memory hub.
4. Preserves backward compatibility with legacy `~/.agy-shared/skills` by automatically importing any legacy skills into PAB skills.
5. Displays active PAB skills and registered agents.

## Layout & PAB Integration

```
~/.gemini/config/
├── skills/                     # PAB universal skills (code-security-audit, pab-ops, etc.)
└── agents/                     # PAB universal agents (main, devops-engineer, qa, etc.)

~/.pab/                         # PAB SQLite DB, canonical registry, memory hub

~/.agy-<profile>/               # Isolated per-profile HOME
├── .local -> ~/.local          # Preserves local binaries and Python packages
├── .pab -> ~/.pab              # Links central PAB registry & knowledge hub
├── .gemini/
│   ├── config/
│   │   ├── skills -> ~/.gemini/config/skills   # Instant access to all PAB skills
│   │   └── agents -> ~/.gemini/config/agents   # Instant access to all PAB agents
│   └── antigravity-cli/
│       ├── skills -> ~/.gemini/config/skills   # Backwards compatibility
│       ├── history.jsonl                       # Isolated profile conversation history
│       └── brain/                              # Isolated profile session logs
```

## Usage

```bash
# 1. Create a profile
./agy.sh new devops

# 2. Synchronize all skills & agents from PAB
./agy-skill.sh

# 3. Launch isolated Antigravity CLI session
./agy.sh devops
```

## License

MIT

# agys

Shell tooling for managing isolated [Antigravity CLI](https://github.com/google-gemini/gemini-cli) (`agy`) profiles — separate `HOME` directories so each project/role keeps its own agent config, sessions, and credentials, while maintaining seamless compatibility with **PersonaOS Agent Bridge (PAB)** skills, agents, and shared memory.

## Requirements

- `bash` 4+
- `agy` (Antigravity CLI) installed and on `PATH` — required for launching a profile
- `pab` (PersonaOS Agent Bridge) installed on `PATH` (optional but recommended for universal multi-platform agent & skill orchestration)

## Scripts

### `agy.sh`

Manage profile directories under `~/.agy-<profile>` and import skills/agents across runtimes.

```bash
./agy.sh new <profile>            # create an isolated profile directory
./agy.sh list                     # list existing profiles
./agy.sh sync                     # sync PAB skills, agents, and config to all profiles
./agy.sh list-openclaw            # list OpenClaw agents and their configured skills
./agy.sh import-skill <agent_id>  # import skills and agent from OpenClaw (e.g. devops, qa, all)
./agy.sh <profile>                # launch agy with that profile
```

Details:

- `new` creates `~/.agy-<profile>` with mode `700`. It automatically configures symlinks for local binaries (`~/.local`), PAB canonical control plane (`~/.pab`), global skills (`~/.gemini/config/skills`), and global agents (`~/.gemini/config/agents`).
- `list` prints the profile name of every `~/.agy-*` directory.
- `sync` traverses all existing `~/.agy-*` profile directories and updates all links to match current PAB skills and agents.
- `list-openclaw` scans `openclaw.json` and workspaces, displaying all configured agents and their skill counts.
- `import-skill <agent_id>` imports all skills assigned to an OpenClaw agent into PAB and Antigravity, compiles the native `agent.md` definition, and syncs all Antigravity profiles immediately.
- `<profile>` re-executes `agy` with `HOME` pointed at the profile directory, setting `PAB_HOME` and preserving access to universal tools.

```bash
# 1. View OpenClaw agents and skills
$ ./agy.sh list-openclaw

# 2. Import devops skills from OpenClaw
$ ./agy.sh import-skill devops
[+] Processing OpenClaw agent: 'devops'
    - Imported 4 skills into Antigravity: tkuehnl-kube-medic, tkuehnl-agentic-devops, pals-software-azure-devops, metallb-lan-expose
    - Registered agent 'devops' into Antigravity (/root/.gemini/config/agents/devops/agent.md)
All profiles updated with new skills and agents.

# 3. Launch isolated Antigravity CLI profile
$ ./agy.sh devops
Launching agy with profile: devops
  [PAB] Unified skills & agents active from /root/.gemini/config
```

### `agy-skill.sh`

Synchronises and links all PAB canonical skills and agents across every Antigravity profile in one command, and provides direct OpenClaw skill imports:

```bash
./agy-skill.sh                    # synchronize all skills and agents across profiles
./agy-skill.sh list               # list all OpenClaw agents and their skills
./agy-skill.sh import <agent_id>  # import skills by OpenClaw agent (e.g. qa, devops, cto, main, all)
```

What it does:
1. Resolves skill locations from `openclaw.json`, agent workspaces, and plugin directories.
2. Ingests skills into PAB's canonical library and deploys them to Antigravity global config (`~/.gemini/config/skills`).
3. Compiles the agent identity from OpenClaw (`SOUL.md`, `IDENTITY.md`) into Antigravity (`~/.gemini/config/agents/<agent_id>/agent.md`).
4. Links `~/.gemini/config/skills`, `~/.gemini/config/agents`, and `~/.pab` into each isolated Antigravity profile (`~/.agy-*`).
5. Preserves backward compatibility with legacy `~/.agy-shared/skills` by automatically importing legacy skills into PAB skills.

## Layout & PAB Integration

```
~/.gemini/config/
├── skills/                     # PAB universal skills (code-security-audit, pab-ops, kube-medic, etc.)
└── agents/                     # PAB universal agents (main, devops, qa, devops-engineer, etc.)

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

## Usage Example

```bash
# 1. View OpenClaw agents
./agy.sh list-openclaw

# 2. Import skills for DevOps and QA
./agy-skill.sh import devops
./agy-skill.sh import qa

# 3. Create a dedicated Antigravity profile
./agy.sh new work

# 4. Launch isolated session with all imported skills & agents ready
./agy.sh work
```

## License

MIT

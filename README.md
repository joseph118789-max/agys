# agys

Shell tooling for managing isolated [Antigravity CLI](https://github.com/google-gemini/gemini-cli) (`agy`) profiles — separate `HOME` directories so each project/role keeps its own agent config, sessions, and credentials.

## Requirements

- `bash` 4+
- `agy` (Antigravity CLI) installed and on `PATH` — required only for launching a profile

## Scripts

### `agy.sh`

Manage profile directories under `~/.agy-<profile>`.

```bash
./agy.sh new <profile>   # create a profile directory
./agy.sh list            # list existing profiles
./agy.sh <profile>       # launch agy with that profile
```

Details:

- `new` creates `~/.agy-<profile>` with mode `700`. It refuses to overwrite an existing directory and validates the profile name against `^[a-zA-Z0-9_-]+$`.
- `list` prints the profile name of every `~/.agy-*` directory.
- `<profile>` re-executes `agy` with `HOME` pointed at the profile directory, so agent state never leaks into your real home directory.

```bash
$ ./agy.sh new work
Created profile: work
Home: /root/.agy-work
Run: ./agy.sh work

$ ./agy.sh list
work

$ ./agy.sh work
Launching agy with profile: work
```

### `agy-skill.sh`

Links a shared skills directory into every profile so a skill written once is available to all of them.

```bash
./agy-skill.sh
```

For each `~/.agy-*` profile (excluding the shared directory itself) it creates
`~/.agy-<profile>/.gemini/antigravity-cli/skills` as a symlink to `~/.agy-shared/skills`.

- Existing symlinks are left alone (reported as "Already linked").
- Existing real directories are **skipped**, not overwritten — inspect them manually.
- The shared directory is created if missing.

## Layout

```
~/.agy-shared/skills/        # skills shared by all profiles
~/.agy-<profile>/            # per-profile HOME
└── .gemini/antigravity-cli/skills -> ~/.agy-shared/skills
```

## Usage

```bash
./agy.sh new work
./agy-skill.sh
./agy.sh work
```

## License

MIT

#!/usr/bin/env python3
"""Import skills and agent definitions from OpenClaw into Antigravity (agy) and PAB."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_openclaw_home() -> Path:
    env_home = os.environ.get("OPENCLAW_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def get_openclaw_config() -> Tuple[Path, Dict]:
    home = get_openclaw_home()
    config_file = home / "openclaw.json"
    if not config_file.is_file():
        return home, {}
    try:
        return home, json.loads(config_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] Error reading {config_file}: {e}", file=sys.stderr)
        return home, {}


def list_openclaw_agents():
    home, data = get_openclaw_config()
    entries = data.get("agents", {}).get("entries", {})

    print("\nAvailable OpenClaw Agents and Configured Skills:")
    print("=" * 70)
    print(f"{'Agent ID':<20} {'Skills Count':<14} {'Skills'}")
    print("-" * 70)

    for agent_id, info in entries.items():
        skills = info.get("skills", [])
        if agent_id == "main":
            # Count skills in main workspace
            ws_skills = home / "workspace" / "skills"
            count = len([d for d in ws_skills.iterdir() if d.is_dir()]) if ws_skills.is_dir() else 0
            skill_summary = f"(All {count} workspace skills)"
        else:
            count = len(skills)
            skill_summary = ", ".join(skills[:3]) + ("..." if len(skills) > 3 else "") if skills else "(none listed in config)"

        print(f"{agent_id:<20} {count:<14} {skill_summary}")

    print("=" * 70)
    print("Run: ./agy.sh import-skill <agent_id> to import skills into Antigravity.\n")


def find_skill_dir(home: Path, agent_workspace: Optional[Path], agent_id: str, skill_name: str) -> Optional[Path]:
    candidates = []
    if agent_workspace:
        candidates.append(agent_workspace / "skills" / skill_name)
    candidates.append(home / "workspace" / "skills" / skill_name)
    candidates.append(home / "agents" / agent_id / "skills" / skill_name)
    candidates.append(home / "plugin-skills" / skill_name)

    for c in candidates:
        if c.is_dir() and (c / "SKILL.md").is_file():
            return c
    return None


def import_skill_folder(skill_path: Path, agy_skills_dir: Path) -> str:
    skill_name = skill_path.name
    # 1. Try PAB first if available
    pab_bin = shutil.which("pab")
    if pab_bin:
        try:
            # pab skill import <path>
            subprocess.run([pab_bin, "skill", "import", str(skill_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # pab skill deploy <name> --to agy
            subprocess.run([pab_bin, "skill", "deploy", skill_name, "--to", "agy"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return skill_name
        except Exception:
            pass

    # 2. Direct copy into Antigravity global config
    target = agy_skills_dir / skill_name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_path, target, dirs_exist_ok=True)
    return skill_name


def import_agent_definition(agent_id: str, agy_agents_dir: Path):
    pab_bin = shutil.which("pab")
    if pab_bin:
        try:
            subprocess.run([pab_bin, "agent", "import", agent_id, "--from", "openclaw"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([pab_bin, "agent", "deploy", agent_id, "--to", "agy"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    return False


def import_skills_by_agent(agent_id: str):
    home, data = get_openclaw_config()
    entries = data.get("agents", {}).get("entries", {})

    agy_config_dir = Path.home() / ".gemini" / "config"
    agy_skills_dir = agy_config_dir / "skills"
    agy_agents_dir = agy_config_dir / "agents"
    agy_skills_dir.mkdir(parents=True, exist_ok=True)
    agy_agents_dir.mkdir(parents=True, exist_ok=True)

    if agent_id == "all":
        agents_to_process = list(entries.keys())
    else:
        if agent_id not in entries:
            print(f"[!] Agent '{agent_id}' not found in openclaw.json.", file=sys.stderr)
            print(f"Available agents: {', '.join(entries.keys())}", file=sys.stderr)
            sys.exit(1)
        agents_to_process = [agent_id]

    total_skills_imported = 0

    for aid in agents_to_process:
        entry = entries.get(aid, {})
        ws_str = entry.get("workspace")
        agent_ws = Path(ws_str).resolve() if ws_str else None
        skill_names = entry.get("skills", [])

        print(f"\n[+] Processing OpenClaw agent: '{aid}'")

        imported_for_agent = []

        if aid == "main":
            # Import all workspace skills
            ws_skills_dir = home / "workspace" / "skills"
            if ws_skills_dir.is_dir():
                for s_dir in ws_skills_dir.iterdir():
                    if s_dir.is_dir() and (s_dir / "SKILL.md").is_file():
                        imported = import_skill_folder(s_dir, agy_skills_dir)
                        imported_for_agent.append(imported)
        else:
            for sname in skill_names:
                sdir = find_skill_dir(home, agent_ws, aid, sname)
                if sdir:
                    imported = import_skill_folder(sdir, agy_skills_dir)
                    imported_for_agent.append(imported)
                else:
                    print(f"    - ⚠️  Skill '{sname}' directory not found (skipped)")

            # Check if agent workspace has a skills directory
            if agent_ws and (agent_ws / "skills").is_dir():
                for sdir in (agent_ws / "skills").iterdir():
                    if sdir.is_dir() and (sdir / "SKILL.md").is_file():
                        if sdir.name not in imported_for_agent:
                            imported = import_skill_folder(sdir, agy_skills_dir)
                            imported_for_agent.append(imported)

        # Import agent profile as well
        agent_imported = import_agent_definition(aid, agy_agents_dir)

        print(f"    - Imported {len(imported_for_agent)} skills into Antigravity: {', '.join(imported_for_agent) if imported_for_agent else '(none)'}")
        if agent_imported:
            print(f"    - Registered agent '{aid}' into Antigravity ({agy_agents_dir / aid / 'agent.md'})")

        total_skills_imported += len(imported_for_agent)

    print(f"\n[✓] Skill import completed. Total skills processed: {total_skills_imported}")

    # Synchronize all Antigravity profiles (~/.agy-*)
    agys_dir = Path(__file__).resolve().parent
    agy_sync_script = agys_dir / "agy-skill.sh"
    if agy_sync_script.is_file() and os.access(agy_sync_script, os.X_OK):
        print("\nUpdating symlinks across all Antigravity profiles...")
        subprocess.run([str(agy_sync_script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("All profiles updated with new skills and agents.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("Usage:")
        print("  import_openclaw_skills.py list               List OpenClaw agents and skills")
        print("  import_openclaw_skills.py <agent_id>         Import skills of a specific agent (e.g. devops, qa, main)")
        print("  import_openclaw_skills.py all                Import skills from all OpenClaw agents")
        sys.exit(0)

    arg = sys.argv[1].lower()
    if arg in ("list", "--list", "-l"):
        list_openclaw_agents()
    else:
        import_skills_by_agent(arg)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Antigravity Session & Conversation Manager
Browse, inspect, move, and copy conversations/sessions across AGY profiles.
"""

import argparse
import fcntl
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any


# -----------------------------------------------------------------------------
# Terminal Styling & Colors
# -----------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)

def bold(text: Any) -> str: return _c("1", str(text))
def dim(text: Any) -> str: return _c("2", str(text))
def red(text: Any) -> str: return _c("31", str(text))
def green(text: Any) -> str: return _c("32", str(text))
def yellow(text: Any) -> str: return _c("33", str(text))
def blue(text: Any) -> str: return _c("34", str(text))
def magenta(text: Any) -> str: return _c("35", str(text))
def cyan(text: Any) -> str: return _c("36", str(text))


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

@dataclass
class AgyHome:
    name: str              # e.g. "default", "93975", "seekn"
    home_dir: Path         # e.g. /root/.gemini or /root/.agy-93975
    cli_dir: Path          # e.g. /root/.agy-93975/.gemini/antigravity-cli
    is_current: bool = False
    is_default: bool = False
    is_linked_to_central: bool = False

@dataclass
class ConversationInfo:
    id: str
    home_name: str
    cli_dir: Path
    title: str = ""
    preview: str = ""
    step_count: int = 0
    last_modified: Optional[datetime] = None
    workspace_uris: List[str] = field(default_factory=list)
    status: str = "IDLE"
    is_locked: bool = False
    locked_pid: Optional[int] = None
    db_size: int = 0
    brain_size: int = 0
    artifact_count: int = 0
    has_annotation: bool = False
    has_history: bool = False
    unindexed: bool = False
    project_id: str = ""
    agent_name: str = ""
    last_user_input_time: Optional[datetime] = None


# -----------------------------------------------------------------------------
# Home & Environment Helpers
# -----------------------------------------------------------------------------

def get_base_user_home() -> Path:
    """Resolve the real system user home, even when executing inside an isolated profile."""
    home_env = os.environ.get("HOME", "")
    if re.search(r"/\.agy-[^/]+$", home_env):
        try:
            pw_dir = pwd.getpwuid(os.getuid()).pw_dir
            if pw_dir and os.path.isdir(pw_dir):
                return Path(pw_dir).resolve()
        except Exception:
            pass
        parent = re.sub(r"/\.agy-[^/]+$", "", home_env)
        if os.path.isdir(parent):
            return Path(parent).resolve()
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        return Path.home().resolve()


def discover_homes(base_dir: Optional[Path] = None) -> Dict[str, AgyHome]:
    """Scan and discover all AGY homes / profiles in the system."""
    base = base_dir.resolve() if base_dir else get_base_user_home()
    current_home_env = Path(os.environ.get("HOME", "")).resolve() if os.environ.get("HOME") else None
    homes: Dict[str, AgyHome] = {}

    # 1. Default AGY profile (~/.gemini/antigravity-cli)
    default_gemini = base / ".gemini"
    default_cli = default_gemini / "antigravity-cli"
    if default_cli.is_dir():
        is_cur = (current_home_env == base) or (current_home_env == default_gemini)
        homes["default"] = AgyHome(
            name="default",
            home_dir=default_gemini,
            cli_dir=default_cli,
            is_current=is_cur,
            is_default=True,
        )

    # 2. Isolated profiles (~/.agy-<profile>)
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(".agy-"):
                continue
            profile_name = entry.name[len(".agy-"):]
            if profile_name == "shared":
                # Shared skills storage, skip if not a CLI profile
                if not (entry / ".gemini" / "antigravity-cli" / "conversations").is_dir():
                    continue

            cli_dir = entry / ".gemini" / "antigravity-cli"
            if not cli_dir.is_dir():
                cli_dir = entry / "antigravity-cli"
            if not cli_dir.is_dir():
                cli_dir = entry / ".gemini" / "antigravity-cli"

            is_cur = (current_home_env == entry)

            # Check if conversations directory is symlinked to default central
            is_linked = False
            conv_cand = cli_dir / "conversations"
            if conv_cand.is_symlink() and default_cli.is_dir():
                try:
                    if conv_cand.resolve() == (default_cli / "conversations").resolve():
                        is_linked = True
                except Exception:
                    pass

            homes[profile_name] = AgyHome(
                name=profile_name,
                home_dir=entry,
                cli_dir=cli_dir,
                is_current=is_cur,
                is_default=False,
                is_linked_to_central=is_linked,
            )

    return homes


def resolve_home(identifier: str, homes: Dict[str, AgyHome], base_dir: Optional[Path] = None) -> Optional[AgyHome]:
    """Resolve a profile name, directory path, or alias to an AgyHome."""
    if not identifier:
        return None

    clean = identifier.strip().strip("'\"")
    # Aliases for default
    if clean.lower() in ("default", "main", "root", "@default", "@root"):
        if "default" in homes:
            return homes["default"]

    # Match by profile name
    if clean in homes:
        return homes[clean]

    # Match by directory name like ".agy-seekn" or "seekn"
    if clean.startswith(".agy-"):
        p_name = clean[len(".agy-"):]
        if p_name in homes:
            return homes[p_name]

    # Try resolving as an explicit file system path
    base = base_dir.resolve() if base_dir else get_base_user_home()
    path_cand = Path(clean).expanduser()
    if not path_cand.is_absolute():
        path_cand = (base / clean).resolve()

    if path_cand.is_dir():
        # Check if path is an .agy-* dir
        m = re.search(r"/\.agy-([^/]+)$", str(path_cand))
        p_name = m.group(1) if m else path_cand.name
        cli_dir = path_cand / ".gemini" / "antigravity-cli"
        if not cli_dir.is_dir():
            cli_dir = path_cand / "antigravity-cli"
        if not cli_dir.is_dir() and (path_cand / "conversations").is_dir():
            cli_dir = path_cand

        return AgyHome(
            name=p_name,
            home_dir=path_cand,
            cli_dir=cli_dir,
            is_current=(path_cand == Path(os.environ.get("HOME", "")).resolve()),
            is_default=(p_name == "default"),
        )

    return None


def create_profile(profile_name: str, base_dir: Optional[Path] = None) -> AgyHome:
    """Create a new AGY profile directory and set up symlinks."""
    base = base_dir.resolve() if base_dir else get_base_user_home()
    script_dir = Path(__file__).resolve().parent
    agy_sh = script_dir / "agy.sh"

    if agy_sh.is_file() and os.access(agy_sh, os.X_OK):
        # Run ./agy.sh new <profile_name>
        env = dict(os.environ)
        env["HOME"] = str(base)
        res = subprocess.run([str(agy_sh), "new", profile_name], env=env, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"agy.sh new {profile_name} failed: {res.stderr.strip() or res.stdout.strip()}")
    else:
        # Fallback Python profile creation
        dir_path = base / f".agy-{profile_name}"
        dir_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        cli_dir = dir_path / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True, exist_ok=True)

        # Create symlinks
        for src, dst in [
            (base / ".local", dir_path / ".local"),
            (base / ".pab", dir_path / ".pab"),
            (base / ".gemini" / "config" / "skills", dir_path / ".gemini" / "config" / "skills"),
            (base / ".gemini" / "config" / "agents", dir_path / ".gemini" / "config" / "agents"),
            (base / ".gemini" / "config" / "skills", dir_path / ".gemini" / "antigravity-cli" / "skills"),
        ]:
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src)

    homes = discover_homes(base)
    resolved = resolve_home(profile_name, homes, base)
    if not resolved:
        raise RuntimeError(f"Failed to resolve newly created profile '{profile_name}'.")
    return resolved


# -----------------------------------------------------------------------------
# Formatting Helpers
# -----------------------------------------------------------------------------

def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # Clean fractional seconds beyond 6 digits
        cleaned = re.sub(r"(\.\d{6})\d+", r"\1", dt_str.strip())
        cleaned = cleaned.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def format_relative_time(dt: Optional[datetime]) -> str:
    if not dt or dt.year < 2000:
        return "unknown"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        days = hrs // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"
    except Exception:
        return "unknown"


def format_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    val = float(num_bytes)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(val)} B"
    return f"{val:.1f} {units[unit_idx]}"


# -----------------------------------------------------------------------------
# Lock & Presence Check
# -----------------------------------------------------------------------------

def is_conversation_locked(cli_dir: Path, conv_id: str, check_pid: bool = False) -> Tuple[bool, Optional[int]]:
    """Check whether a conversation is locked by an active process."""
    lock_file = cli_dir / "presence" / f"{conv_id}.lock"
    if not lock_file.is_file():
        return False, None

    try:
        with open(lock_file, "r+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False, None
            except (BlockingIOError, PermissionError):
                pid = None
                if check_pid:
                    try:
                        fuser_res = subprocess.run(["fuser", str(lock_file)], capture_output=True, text=True)
                        out = (fuser_res.stdout + " " + fuser_res.stderr).strip()
                        pids = re.findall(r"\b\d+\b", out)
                        if pids:
                            pid = int(pids[-1])
                    except Exception:
                        pass
                return True, pid
    except Exception:
        return False, None


# -----------------------------------------------------------------------------
# Conversation Scanning & Metadata
# -----------------------------------------------------------------------------

def get_conversations(home: AgyHome) -> List[ConversationInfo]:
    """Scan and return all conversations in an AGY home."""
    results: Dict[str, ConversationInfo] = {}
    cli_dir = home.cli_dir

    conv_dir = cli_dir / "conversations"
    brain_dir = cli_dir / "brain"
    ann_dir = cli_dir / "annotations"
    db_summary_file = cli_dir / "conversation_summaries.db"

    # 1. Read indexed summaries from SQLite
    if db_summary_file.is_file():
        try:
            conn = sqlite3.connect(str(db_summary_file))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT conversation_id, title, preview, step_count, last_modified_time,
                       workspace_uris, status, project_id, agent_name, last_user_input_time
                FROM conversation_summaries
            """)
            for row in cur.fetchall():
                cid = row["conversation_id"]
                if not cid:
                    continue
                ws_uris = []
                try:
                    raw_ws = row["workspace_uris"]
                    if raw_ws:
                        parsed = json.loads(raw_ws)
                        if isinstance(parsed, list):
                            ws_uris = [str(w) for w in parsed]
                        elif isinstance(parsed, str):
                            ws_uris = [parsed]
                except Exception:
                    if row["workspace_uris"]:
                        ws_uris = [row["workspace_uris"]]

                results[cid] = ConversationInfo(
                    id=cid,
                    home_name=home.name,
                    cli_dir=cli_dir,
                    title=row["title"] or "",
                    preview=row["preview"] or "",
                    step_count=row["step_count"] or 0,
                    last_modified=parse_iso_datetime(row["last_modified_time"]),
                    workspace_uris=ws_uris,
                    status=row["status"] or "IDLE",
                    project_id=row["project_id"] or "",
                    agent_name=row["agent_name"] or "",
                    last_user_input_time=parse_iso_datetime(row["last_user_input_time"]),
                    unindexed=False,
                )
            conn.close()
        except Exception as e:
            # Continue even if summaries DB has read error
            pass

    # 2. Check physical conversation DBs in conversations/
    if conv_dir.is_dir():
        for f in conv_dir.glob("*.db"):
            cid = f.stem
            db_size = 0
            try:
                db_size += f.stat().st_size
                wal_f = conv_dir / f"{cid}.db-wal"
                if wal_f.is_file():
                    db_size += wal_f.stat().st_size
            except OSError:
                pass

            if cid not in results:
                # Orphan/unindexed conversation
                step_cnt = 0
                title = ""
                try:
                    conn = sqlite3.connect(str(f))
                    cur = conn.cursor()
                    cur.execute("SELECT count(*) FROM steps")
                    step_cnt = cur.fetchone()[0]
                    conn.close()
                except Exception:
                    pass

                ann_file = ann_dir / f"{cid}.pbtxt"
                if ann_file.is_file():
                    try:
                        content = ann_file.read_text(encoding="utf-8")
                        m = re.search(r'title\s*:\s*"(.*?)"', content)
                        if m:
                            title = m.group(1)
                    except Exception:
                        pass

                mtime = None
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                except Exception:
                    pass

                results[cid] = ConversationInfo(
                    id=cid,
                    home_name=home.name,
                    cli_dir=cli_dir,
                    title=title or "(Unindexed conversation)",
                    step_count=step_cnt,
                    last_modified=mtime,
                    unindexed=True,
                )

            results[cid].db_size = db_size

    # 3. Check locks & annotations
    for cid, info in results.items():
        is_locked, locked_pid = is_conversation_locked(cli_dir, cid)
        info.is_locked = is_locked
        info.locked_pid = locked_pid

        ann_file = ann_dir / f"{cid}.pbtxt"
        info.has_annotation = ann_file.is_file()

        # Count artifacts in brain/<id> (fast: top level markdown files)
        b_dir = brain_dir / cid
        if b_dir.is_dir():
            try:
                artifacts = [
                    x for x in b_dir.iterdir()
                    if x.is_file() and not x.name.startswith(".") and x.name.endswith(".md")
                ]
                info.artifact_count = len(artifacts)
            except OSError:
                pass

    return list(results.values())


def get_conversation_detail(conv: ConversationInfo) -> Dict[str, Any]:
    """Retrieve in-depth details, prompt history, and artifacts for a conversation."""
    cli_dir = conv.cli_dir
    cid = conv.id
    history_file = cli_dir / "history.jsonl"
    transcript_file = cli_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
    brain_dir = cli_dir / "brain" / cid

    prompts: List[str] = []

    # 1. Prompts from history.jsonl
    if history_file.is_file():
        try:
            with open(history_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("conversationId") == cid and entry.get("display"):
                            prompts.append(entry.get("display"))
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Prompts from transcript.jsonl if history empty
    if not prompts and transcript_file.is_file():
        try:
            with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        step = json.loads(line)
                        if step.get("type") == "USER_INPUT":
                            raw = step.get("content", "")
                            m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw, re.DOTALL)
                            prompt_text = m.group(1).strip() if m else raw.strip()
                            if prompt_text:
                                prompts.append(prompt_text)
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. Artifact files
    artifacts: List[Dict[str, Any]] = []
    if brain_dir.is_dir():
        try:
            for item in brain_dir.iterdir():
                if item.is_file() and not item.name.startswith("."):
                    stat = item.stat()
                    artifacts.append({
                        "name": item.name,
                        "size": stat.st_size,
                        "path": str(item),
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    })
        except OSError:
            pass

    # 4. Accurate brain directory size
    brain_total_size = 0
    if brain_dir.is_dir():
        try:
            for root, _, files in os.walk(brain_dir):
                for f in files:
                    try:
                        brain_total_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except OSError:
            pass

    return {
        "prompts": prompts,
        "artifacts": sorted(artifacts, key=lambda a: a["name"]),
        "brain_total_size": brain_total_size,
    }


def find_conversation(
    identifier: str,
    homes: Dict[str, AgyHome],
    from_home: Optional[AgyHome] = None
) -> Tuple[Optional[ConversationInfo], List[ConversationInfo]]:
    """
    Search for a conversation by UUID, prefix, or title across specified or all homes.
    Returns (unique_match, candidate_matches).
    """
    clean_id = identifier.strip().strip("'\"").lower()
    candidates: List[ConversationInfo] = []

    target_homes = [from_home] if from_home else list(homes.values())

    for h in target_homes:
        for c in get_conversations(h):
            # 1. Exact UUID match
            if c.id.lower() == clean_id:
                return c, [c]
            # 2. Prefix match
            if c.id.lower().startswith(clean_id):
                candidates.append(c)
                continue
            # 3. Title match
            if clean_id in c.title.lower():
                candidates.append(c)

    if len(candidates) == 1:
        return candidates[0], candidates

    return None, candidates


# -----------------------------------------------------------------------------
# Database Schema Helper
# -----------------------------------------------------------------------------

SUMMARIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT "",
    preview TEXT NOT NULL DEFAULT "",
    step_count INTEGER NOT NULL DEFAULT 0,
    last_modified_time DATETIME NOT NULL,
    workspace_uris TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT "",
    source TEXT NOT NULL DEFAULT "",
    project_id TEXT NOT NULL DEFAULT "",
    agent_name TEXT NOT NULL DEFAULT "",
    parent_conversation_id TEXT NOT NULL DEFAULT "",
    nesting_depth INTEGER NOT NULL DEFAULT 0,
    battle_id TEXT NOT NULL DEFAULT "",
    winning_conversation_id TEXT NOT NULL DEFAULT "",
    not_fully_idle NUMERIC NOT NULL DEFAULT 0,
    killed NUMERIC NOT NULL DEFAULT 0,
    last_user_input_time DATETIME NOT NULL,
    last_user_input_step_index INTEGER NOT NULL DEFAULT -1,
    app_data_dir TEXT NOT NULL DEFAULT "",
    raw_summary BLOB,
    group_id TEXT NOT NULL DEFAULT ""
);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_last_user_input_time ON conversation_summaries(last_user_input_time);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_last_modified_time ON conversation_summaries(last_modified_time);
"""

def ensure_summaries_db(db_path: Path):
    """Ensure conversation_summaries.db exists with correct table and indexes."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SUMMARIES_SCHEMA)


# -----------------------------------------------------------------------------
# Move & Copy Engine
# -----------------------------------------------------------------------------

def transfer_conversation(
    src_home: AgyHome,
    dst_home: AgyHome,
    conv_id: str,
    is_move: bool,
    force: bool = False,
    dry_run: bool = False,
    set_active: bool = False,
    progress_callback: Optional[Any] = None,
) -> Tuple[bool, str]:
    """
    Move or copy a conversation and all associated artifacts between AGY homes.
    Returns (success: bool, message: str).
    """
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)

    if src_home.cli_dir == dst_home.cli_dir:
        return False, f"Source and destination home are identical ({src_home.name})."

    src_cli = src_home.cli_dir
    dst_cli = dst_home.cli_dir

    src_db = src_cli / "conversations" / f"{conv_id}.db"
    src_brain = src_cli / "brain" / conv_id
    src_ann = src_cli / "annotations" / f"{conv_id}.pbtxt"
    src_lock = src_cli / "presence" / f"{conv_id}.lock"

    if not src_db.is_file() and not src_brain.is_dir():
        return False, f"Conversation '{conv_id}' not found in source profile '{src_home.name}'."

    # 1. Lock check
    log("[1/6] Checking active session locks...")
    is_locked, pid = is_conversation_locked(src_cli, conv_id)
    if is_locked and not force:
        pid_msg = f" (process PID {pid})" if pid else ""
        return False, (
            f"Conversation '{conv_id}' is currently active and locked by running agy{pid_msg}.\n"
            f"Stop the session before moving, or use --force to override."
        )

    # 2. Destination collision check
    dst_db = dst_cli / "conversations" / f"{conv_id}.db"
    dst_brain = dst_cli / "brain" / conv_id
    if (dst_db.is_file() or dst_brain.is_dir()) and not force:
        return False, (
            f"Conversation '{conv_id}' already exists in destination profile '{dst_home.name}'.\n"
            f"Use --force to overwrite."
        )

    action_label = "Moving" if is_move else "Copying"
    if dry_run:
        return True, (
            f"[Dry Run] Would {action_label.lower()} conversation '{conv_id}'\n"
            f"  From : {src_home.name} ({src_cli})\n"
            f"  To   : {dst_home.name} ({dst_cli})\n"
            f"  Files: DB ({src_db}), Brain ({src_brain}), Annotations ({src_ann})"
        )

    # 3. Flush SQLite WAL on source DB
    log("[2/6] Flushing SQLite write-ahead log (WAL)...")
    if src_db.is_file():
        try:
            with sqlite3.connect(str(src_db)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

    # 4. Prepare destination directories
    log(f"[3/6] {action_label} conversation database and brain artifacts...")
    dst_conv_dir = dst_cli / "conversations"
    dst_brain_dir = dst_cli / "brain"
    dst_ann_dir = dst_cli / "annotations"

    dst_conv_dir.mkdir(parents=True, exist_ok=True)
    dst_brain_dir.mkdir(parents=True, exist_ok=True)
    dst_ann_dir.mkdir(parents=True, exist_ok=True)

    # Copy database file(s)
    if src_db.is_file():
        shutil.copy2(src_db, dst_db)
        # Also copy WAL/SHM if they still exist
        src_wal = src_cli / "conversations" / f"{conv_id}.db-wal"
        src_shm = src_cli / "conversations" / f"{conv_id}.db-shm"
        if src_wal.is_file():
            shutil.copy2(src_wal, dst_conv_dir / f"{conv_id}.db-wal")
        if src_shm.is_file():
            shutil.copy2(src_shm, dst_conv_dir / f"{conv_id}.db-shm")

        # Verify integrity on destination
        try:
            with sqlite3.connect(str(dst_db)) as conn:
                cur = conn.cursor()
                cur.execute("PRAGMA quick_check")
                check_res = cur.fetchone()[0]
                if check_res != "ok":
                    raise RuntimeError(f"Integrity check failed: {check_res}")
        except Exception as e:
            if dst_db.is_file():
                dst_db.unlink(missing_ok=True)
            return False, f"Destination SQLite database failed integrity check: {e}"

    # Copy brain directory recursively
    if src_brain.is_dir():
        shutil.copytree(src_brain, dst_brain, symlinks=True, dirs_exist_ok=True)

    # Copy annotation
    if src_ann.is_file():
        shutil.copy2(src_ann, dst_ann_dir / f"{conv_id}.pbtxt")

    # 5. Migrate metadata in conversation_summaries.db
    log("[4/6] Migrating conversation summaries and metadata...")
    dst_sum_file = dst_cli / "conversation_summaries.db"
    src_sum_file = src_cli / "conversation_summaries.db"
    ensure_summaries_db(dst_sum_file)

    if src_sum_file.is_file():
        try:
            with sqlite3.connect(str(src_sum_file)) as s_conn:
                s_cur = s_conn.cursor()
                s_cur.execute("SELECT * FROM conversation_summaries WHERE conversation_id = ?", (conv_id,))
                s_row = s_cur.fetchone()
                if s_row:
                    s_cols = [d[0] for d in s_cur.description]
                    with sqlite3.connect(str(dst_sum_file)) as d_conn:
                        d_cur = d_conn.cursor()
                        d_cur.execute("PRAGMA table_info(conversation_summaries)")
                        d_cols = [c[1] for c in d_cur.fetchall()]

                        common_cols = [c for c in s_cols if c in d_cols]
                        val_map = {s_cols[i]: s_row[i] for i in range(len(s_cols))}
                        vals = [val_map[c] for c in common_cols]
                        cols_sql = ", ".join(common_cols)
                        placeholders = ", ".join(["?"] * len(common_cols))

                        d_conn.execute(
                            f"INSERT OR REPLACE INTO conversation_summaries ({cols_sql}) VALUES ({placeholders})",
                            vals
                        )
                        d_conn.commit()

                    if is_move:
                        s_conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (conv_id,))
                        s_conn.commit()
        except Exception as e:
            log(f"  Warning: Metadata migration encountered an issue: {e}")

    # 6. Migrate history.jsonl
    log("[5/6] Migrating shell and prompt history...")
    src_hist = src_cli / "history.jsonl"
    dst_hist = dst_cli / "history.jsonl"
    if src_hist.is_file():
        try:
            matching_lines = []
            remaining_lines = []
            with open(src_hist, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("conversationId") == conv_id:
                            matching_lines.append(line.strip())
                        else:
                            remaining_lines.append(line)
                    except Exception:
                        remaining_lines.append(line)

            if matching_lines:
                dst_hist.parent.mkdir(parents=True, exist_ok=True)
                with open(dst_hist, "a", encoding="utf-8") as f:
                    for m in matching_lines:
                        f.write(m + "\n")

            if is_move:
                with open(src_hist, "w", encoding="utf-8") as f:
                    for r in remaining_lines:
                        f.write(r.strip() + "\n")
        except Exception as e:
            log(f"  Warning: History migration encountered an issue: {e}")

    # 7. Migrate workspace last_conversations cache
    src_last_conv_file = src_cli / "cache" / "last_conversations.json"
    dst_last_conv_file = dst_cli / "cache" / "last_conversations.json"
    matched_workspace = None

    if src_last_conv_file.is_file():
        try:
            with open(src_last_conv_file, "r", encoding="utf-8") as f:
                src_cache = json.load(f)
            updated = False
            for ws, cid in list(src_cache.items()):
                if cid == conv_id:
                    matched_workspace = ws
                    if is_move:
                        del src_cache[ws]
                        updated = True
            if is_move and updated:
                with open(src_last_conv_file, "w", encoding="utf-8") as f:
                    json.dump(src_cache, f, indent=2)
        except Exception:
            pass

    if (set_active or matched_workspace) and matched_workspace:
        try:
            dst_cache = {}
            if dst_last_conv_file.is_file():
                try:
                    with open(dst_last_conv_file, "r", encoding="utf-8") as f:
                        dst_cache = json.load(f)
                except Exception:
                    pass
            if set_active or matched_workspace not in dst_cache:
                dst_cache[matched_workspace] = conv_id
                dst_last_conv_file.parent.mkdir(parents=True, exist_ok=True)
                with open(dst_last_conv_file, "w", encoding="utf-8") as f:
                    json.dump(dst_cache, f, indent=2)
        except Exception:
            pass

    # 8. Source cleanup if move
    if is_move:
        log("[6/6] Cleaning up source conversation...")
        src_db.unlink(missing_ok=True)
        (src_cli / "conversations" / f"{conv_id}.db-wal").unlink(missing_ok=True)
        (src_cli / "conversations" / f"{conv_id}.db-shm").unlink(missing_ok=True)
        src_ann.unlink(missing_ok=True)
        src_lock.unlink(missing_ok=True)
        if src_brain.is_dir():
            shutil.rmtree(src_brain, ignore_errors=True)

    # Touch destination summaries DB so AGY reconciler refreshes
    try:
        os.utime(dst_sum_file, None)
    except Exception:
        pass

    action_past = "Moved" if is_move else "Copied"
    resume_cmd = (
        f"agy --conversation {conv_id}"
        if dst_home.name == "default"
        else f"./agy.sh {dst_home.name} --conversation {conv_id}"
    )

    return True, (
        f"[+] Successfully {action_past.lower()} conversation '{conv_id}'\n"
        f"    Source      : {src_home.name}\n"
        f"    Destination : {dst_home.name}\n"
        f"    Resume with : {bold(resume_cmd)}"
    )


def delete_conversation(
    home: AgyHome,
    conv_id: str,
    force: bool = False,
    dry_run: bool = False,
) -> Tuple[bool, str]:
    """Delete a conversation and all its associated artifacts from an AGY home."""
    cli_dir = home.cli_dir
    src_db = cli_dir / "conversations" / f"{conv_id}.db"
    src_brain = cli_dir / "brain" / conv_id
    src_ann = cli_dir / "annotations" / f"{conv_id}.pbtxt"
    src_lock = cli_dir / "presence" / f"{conv_id}.lock"

    if not src_db.is_file() and not src_brain.is_dir():
        return False, f"Conversation '{conv_id}' not found in profile '{home.name}'."

    is_locked, pid = is_conversation_locked(cli_dir, conv_id)
    if is_locked and not force:
        pid_msg = f" (process PID {pid})" if pid else ""
        return False, (
            f"Conversation '{conv_id}' is currently active and locked{pid_msg}.\n"
            f"Stop the session before deleting, or use --force to override."
        )

    if dry_run:
        return True, f"[Dry Run] Would delete conversation '{conv_id}' from profile '{home.name}'."

    # Delete DB & brain
    src_db.unlink(missing_ok=True)
    (cli_dir / "conversations" / f"{conv_id}.db-wal").unlink(missing_ok=True)
    (cli_dir / "conversations" / f"{conv_id}.db-shm").unlink(missing_ok=True)
    src_ann.unlink(missing_ok=True)
    src_lock.unlink(missing_ok=True)
    if src_brain.is_dir():
        shutil.rmtree(src_brain, ignore_errors=True)

    # Delete from conversation_summaries.db
    sum_file = cli_dir / "conversation_summaries.db"
    if sum_file.is_file():
        try:
            with sqlite3.connect(str(sum_file)) as conn:
                conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (conv_id,))
                conn.commit()
        except Exception:
            pass

    # Delete from history.jsonl
    hist_file = cli_dir / "history.jsonl"
    if hist_file.is_file():
        try:
            remaining = []
            with open(hist_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("conversationId") != conv_id:
                            remaining.append(line)
                    except Exception:
                        remaining.append(line)
            with open(hist_file, "w", encoding="utf-8") as f:
                for r in remaining:
                    f.write(r.strip() + "\n")
        except Exception:
            pass

    # Delete from cache/last_conversations.json
    cache_file = cli_dir / "cache" / "last_conversations.json"
    if cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            updated = False
            for ws, cid in list(cache_data.items()):
                if cid == conv_id:
                    del cache_data[ws]
                    updated = True
            if updated:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, indent=2)
        except Exception:
            pass

    return True, f"[+] Successfully deleted conversation '{conv_id}' from profile '{home.name}'."


# -----------------------------------------------------------------------------
# Single Folder & Consolidation Engine
# -----------------------------------------------------------------------------

SINGLE_FOLDER_MARKER = "single_folder_conversations"


def is_single_folder_globally_enabled(base_dir: Optional[Path] = None) -> bool:
    """Check if single folder conversations marker is set in global config."""
    base = base_dir.resolve() if base_dir else get_base_user_home()
    marker = base / ".gemini" / "config" / SINGLE_FOLDER_MARKER
    return marker.is_file()


def consolidate_all_conversations(
    homes: Dict[str, AgyHome],
    target_home: AgyHome,
    is_move: bool = True,
    link_after: bool = False,
    force: bool = False,
    dry_run: bool = False,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Consolidate all conversations from all profiles into a single target home (default: ~/.gemini).
    Optionally links profile conversation directories to target home after migration.
    """
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)

    target_cli = target_home.cli_dir
    target_conv_dir = target_cli / "conversations"
    target_brain_dir = target_cli / "brain"
    target_ann_dir = target_cli / "annotations"

    target_conv_dir.mkdir(parents=True, exist_ok=True)
    target_brain_dir.mkdir(parents=True, exist_ok=True)
    target_ann_dir.mkdir(parents=True, exist_ok=True)
    ensure_summaries_db(target_cli / "conversation_summaries.db")

    stats: Dict[str, Any] = {
        "target": target_home.name,
        "target_dir": str(target_conv_dir),
        "is_move": is_move,
        "link_after": link_after,
        "dry_run": dry_run,
        "profiles_scanned": 0,
        "transferred": [],
        "skipped_locked": [],
        "skipped_exists": [],
        "skipped_linked": [],
        "failed": [],
        "linked_profiles": [],
    }

    candidate_homes = [h for h in homes.values() if h.name != target_home.name]

    for src_h in candidate_homes:
        stats["profiles_scanned"] += 1
        src_conv_dir = src_h.cli_dir / "conversations"

        # Check if already linked
        if src_conv_dir.is_symlink():
            try:
                if src_conv_dir.resolve() == target_conv_dir.resolve():
                    stats["skipped_linked"].append(src_h.name)
                    log(f"[*] Profile '{src_h.name}' is already linked to single central folder. Skipping.")
                    continue
            except Exception:
                pass

        convs = get_conversations(src_h)
        if not convs:
            log(f"[*] Profile '{src_h.name}' has no conversations to consolidate.")
        else:
            log(f"[*] Scanning profile '{src_h.name}' ({len(convs)} conversations)...")

        for c in convs:
            # 1. Lock check
            is_locked, pid = is_conversation_locked(src_h.cli_dir, c.id, check_pid=True)
            if is_locked and not force:
                pid_str = f" (PID {pid})" if pid else ""
                log(f"  [SKIP] [{c.id[:8]}] '{c.title}': Active session locked by running agy{pid_str}. (Use --force to include)")
                stats["skipped_locked"].append({
                    "id": c.id,
                    "title": c.title,
                    "profile": src_h.name,
                    "pid": pid,
                })
                continue

            # 2. Collision check
            dst_db = target_conv_dir / f"{c.id}.db"
            if dst_db.is_file():
                if not force:
                    same_size = False
                    src_db = src_h.cli_dir / "conversations" / f"{c.id}.db"
                    if src_db.is_file() and src_db.stat().st_size == dst_db.stat().st_size:
                        same_size = True
                    reason = "Already identical in destination" if same_size else "Already exists in destination (use --force to overwrite)"
                    log(f"  [SKIP] [{c.id[:8]}] '{c.title}': {reason}.")
                    stats["skipped_exists"].append({
                        "id": c.id,
                        "title": c.title,
                        "profile": src_h.name,
                        "same_size": same_size,
                    })
                    continue

            # 3. Transfer conversation
            action_verb = "Moving" if is_move else "Copying"
            log(f"  [{action_verb}] [{c.id[:8]}] '{c.title}' ({format_size(c.db_size)})...")
            ok, msg = transfer_conversation(
                src_home=src_h,
                dst_home=target_home,
                conv_id=c.id,
                is_move=is_move,
                force=force,
                dry_run=dry_run,
                progress_callback=None,
            )
            if ok:
                stats["transferred"].append({
                    "id": c.id,
                    "title": c.title,
                    "profile": src_h.name,
                    "size": c.db_size,
                })
            else:
                log(f"  [FAIL] [{c.id[:8]}] Transfer failed: {msg}")
                stats["failed"].append({
                    "id": c.id,
                    "title": c.title,
                    "profile": src_h.name,
                    "error": msg,
                })

        # Check linking
        if link_after and not dry_run and is_move:
            remaining = get_conversations(src_h)
            if not remaining:
                log(f"[*] Linking profile '{src_h.name}' to single central folder...")
                try:
                    for sub in ["conversations", "brain", "annotations"]:
                        p_sub = src_h.cli_dir / sub
                        if p_sub.is_symlink():
                            p_sub.unlink()
                        elif p_sub.is_dir():
                            shutil.rmtree(p_sub, ignore_errors=True)
                        p_sub.symlink_to(target_cli / sub)

                    sum_f = src_h.cli_dir / "conversation_summaries.db"
                    if sum_f.is_symlink():
                        sum_f.unlink()
                    elif sum_f.is_file():
                        sum_f.unlink()
                    if (target_cli / "conversation_summaries.db").is_file():
                        sum_f.symlink_to(target_cli / "conversation_summaries.db")

                    stats["linked_profiles"].append(src_h.name)
                    log(f"  [OK] Profile '{src_h.name}' now linked to single folder.")
                except Exception as e:
                    log(f"  [FAIL] Could not link profile '{src_h.name}': {e}")
            else:
                log(f"  Notice: Profile '{src_h.name}' still has {len(remaining)} conversations remaining; linking skipped.")

    return stats


def set_single_folder_mode(
    homes: Dict[str, AgyHome],
    target_home: AgyHome,
    enable: bool,
    force: bool = False,
    dry_run: bool = False,
    progress_callback: Optional[Any] = None,
) -> Tuple[bool, str]:
    """
    Enable or disable single-folder conversations mode across all profiles.
    When enabled:
      1. Consolidates existing conversations into target_home (~/.gemini/antigravity-cli).
      2. Symlinks each profile's conversations, brain, annotations, and summaries DB to target.
      3. Sets ~/.gemini/config/single_folder_conversations marker for future profiles.
    """
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)

    base = get_base_user_home()
    marker_file = base / ".gemini" / "config" / SINGLE_FOLDER_MARKER
    target_cli = target_home.cli_dir

    if enable:
        log(bold("Enabling Single-Folder Conversations Mode..."))
        log(f"Central Target Directory: {cyan(str(target_cli / 'conversations'))}\n")

        # Step 1: Consolidate all conversations to target
        log("[1/3] Consolidating all conversations into single central folder...")
        stats = consolidate_all_conversations(
            homes=homes,
            target_home=target_home,
            is_move=True,
            link_after=False,
            force=force,
            dry_run=dry_run,
            progress_callback=log,
        )

        if dry_run:
            return True, f"[Dry Run] Would enable single-folder mode and consolidate {len(stats['transferred'])} conversations."

        # Step 2: Symlink profiles
        log("\n[2/3] Configuring profiles to share single central folder...")
        linked_count = 0
        skipped_count = 0

        for name, h in homes.items():
            if h.name == target_home.name:
                continue

            # Check if any non-locked conversations remain
            remaining = get_conversations(h)
            # If conversations dir is already a symlink to target, it's linked
            if (h.cli_dir / "conversations").is_symlink():
                try:
                    if (h.cli_dir / "conversations").resolve() == (target_cli / "conversations").resolve():
                        log(f"  [OK] Profile '{name}' is already linked.")
                        linked_count += 1
                        continue
                except Exception:
                    pass

            if remaining:
                log(yellow(f"  [!] Profile '{name}' has {len(remaining)} conversations that could not be moved (e.g. active locks). Linking skipped."))
                skipped_count += 1
                continue

            try:
                for sub in ["conversations", "brain", "annotations"]:
                    p_sub = h.cli_dir / sub
                    if p_sub.is_symlink():
                        p_sub.unlink()
                    elif p_sub.is_dir():
                        shutil.rmtree(p_sub, ignore_errors=True)
                    p_sub.parent.mkdir(parents=True, exist_ok=True)
                    p_sub.symlink_to(target_cli / sub)

                sum_f = h.cli_dir / "conversation_summaries.db"
                if sum_f.is_symlink():
                    sum_f.unlink()
                elif sum_f.is_file():
                    sum_f.unlink()
                if (target_cli / "conversation_summaries.db").is_file():
                    sum_f.symlink_to(target_cli / "conversation_summaries.db")

                log(f"  [OK] Profile '{name}' successfully linked to single folder.")
                linked_count += 1
            except Exception as e:
                log(red(f"  [FAIL] Failed to link profile '{name}': {e}"))
                skipped_count += 1

        # Step 3: Write configuration marker
        log("\n[3/3] Setting global configuration marker...")
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        marker_file.write_text("enabled\n", encoding="utf-8")
        log(f"  [OK] Marker set at {marker_file}")

        return True, (
            f"Single-folder conversations mode ENABLED!\n"
            f"All conversations reside in: {bold(str(target_cli / 'conversations'))}\n"
            f"Profiles linked: {linked_count} (skipped: {skipped_count}).\n"
            f"Any newly created profiles via 'agy.sh new' will automatically share this folder."
        )

    else:
        # Disable / isolate
        log(bold("Disabling Single-Folder Conversations Mode (Restoring Isolated Storage)..."))

        if dry_run:
            return True, "[Dry Run] Would disable single-folder mode and un-link profiles."

        # Remove marker file
        if marker_file.is_file():
            marker_file.unlink()
            log(f"  [OK] Removed global marker: {marker_file}")

        unlinked_count = 0
        for name, h in homes.items():
            if h.name == target_home.name:
                continue

            is_linked = False
            for sub in ["conversations", "brain", "annotations"]:
                p_sub = h.cli_dir / sub
                if p_sub.is_symlink():
                    p_sub.unlink()
                    p_sub.mkdir(parents=True, exist_ok=True)
                    is_linked = True

            sum_f = h.cli_dir / "conversation_summaries.db"
            if sum_f.is_symlink():
                sum_f.unlink()
                ensure_summaries_db(sum_f)
                is_linked = True

            if is_linked:
                log(f"  [OK] Profile '{name}' restored to isolated storage.")
                unlinked_count += 1

        return True, (
            f"Single-folder conversations mode DISABLED.\n"
            f"{unlinked_count} profiles restored to isolated local storage."
        )


# -----------------------------------------------------------------------------
# CLI Commands: Homes, List, Info
# -----------------------------------------------------------------------------

def cmd_homes(args: argparse.Namespace):
    """List all detected AGY homes / profiles."""
    homes = discover_homes(args.base_dir)
    if not homes:
        print("No Antigravity profiles found.")
        return

    if getattr(args, "json", False):
        data = []
        for h in homes.values():
            convs = get_conversations(h)
            total_db_size = sum(c.db_size for c in convs)
            data.append({
                "name": h.name,
                "home_dir": str(h.home_dir),
                "cli_dir": str(h.cli_dir),
                "is_current": h.is_current,
                "is_default": h.is_default,
                "conversation_count": len(convs),
                "total_db_size": total_db_size,
            })
        print(json.dumps(data, indent=2))
        return

    print(f"\n{bold('Available Antigravity Profiles (AGY Homes):')}")
    print("=" * 78)
    print(f" {'Profile':<14} {'Conversations':<15} {'Disk Size':<12} {'Location':<33}")
    print("-" * 78)

    for name, h in sorted(homes.items(), key=lambda x: (not x[1].is_default, x[0])):
        convs = get_conversations(h)
        total_db_size = sum(c.db_size for c in convs)
        size_str = format_size(total_db_size)

        cur_marker = f" {cyan('(current)')}" if h.is_current else ""
        def_marker = f" {green('(default)')}" if h.is_default else ""
        link_marker = f" {magenta('(linked central)')}" if h.is_linked_to_central else ""
        badge = f"{cur_marker}{def_marker}{link_marker}"
        prof_display = f"{name}{badge}"

        loc = str(h.home_dir)
        # Shorten user home for display
        base_home_str = str(get_base_user_home())
        if loc.startswith(base_home_str):
            loc = "~" + loc[len(base_home_str):]

        print(f" {name:<14} {len(convs):<15} {size_str:<12} {loc:<33}")

    print("=" * 78)
    print("Use: ./agy-session.sh list <profile> to inspect conversations.\n")


def cmd_list(args: argparse.Namespace):
    """List conversations in a specific home or across all homes."""
    homes = discover_homes(args.base_dir)
    target_homes: List[AgyHome] = []

    if getattr(args, "all", False) or not getattr(args, "home", None):
        target_homes = list(homes.values())
    else:
        h = resolve_home(args.home, homes, args.base_dir)
        if not h:
            print(red(f"Error: Profile '{args.home}' not found."))
            print(f"Run './agy-session.sh homes' to view available profiles.")
            sys.exit(1)
        target_homes = [h]

    all_convs: List[ConversationInfo] = []
    for h in target_homes:
        all_convs.extend(get_conversations(h))

    # Filtering
    if getattr(args, "search", None):
        query = args.search.lower()
        all_convs = [
            c for c in all_convs
            if query in c.id.lower()
            or query in c.title.lower()
            or any(query in ws.lower() for ws in c.workspace_uris)
        ]

    # Sorting
    sort_key = getattr(args, "sort", "date") or "date"
    reverse = not getattr(args, "reverse", False)  # default date desc

    if sort_key == "steps":
        all_convs.sort(key=lambda c: c.step_count, reverse=reverse)
    elif sort_key == "size":
        all_convs.sort(key=lambda c: c.db_size, reverse=reverse)
    elif sort_key == "title":
        all_convs.sort(key=lambda c: c.title.lower(), reverse=not reverse)
    else:  # date
        all_convs.sort(
            key=lambda c: c.last_modified or datetime.min.replace(tzinfo=timezone.utc),
            reverse=reverse
        )

    # Limit
    if getattr(args, "limit", None) and args.limit > 0:
        all_convs = all_convs[:args.limit]

    if getattr(args, "json", False):
        out = []
        for c in all_convs:
            out.append({
                "id": c.id,
                "profile": c.home_name,
                "title": c.title,
                "steps": c.step_count,
                "last_modified": c.last_modified.isoformat() if c.last_modified else None,
                "status": c.status,
                "is_locked": c.is_locked,
                "locked_pid": c.locked_pid,
                "db_size": c.db_size,
                "artifact_count": c.artifact_count,
                "workspace_uris": c.workspace_uris,
            })
        print(json.dumps(out, indent=2))
        return

    if not all_convs:
        print("No conversations found matching criteria.")
        return

    single_home = (len(target_homes) == 1 and not getattr(args, "all", False))
    header_title = f"Conversations in '{target_homes[0].name}'" if single_home else "Conversations Across All Profiles"

    print(f"\n{bold(header_title)} ({len(all_convs)} total):")
    print("=" * 96)
    if single_home:
        print(f" {'ID':<10} {'Steps':<8} {'Last Modified':<14} {'Status':<9} {'Size':<10} {'Title'}")
    else:
        print(f" {'Profile':<10} {'ID':<10} {'Steps':<8} {'Last Modified':<14} {'Status':<9} {'Size':<10} {'Title'}")
    print("-" * 96)

    for c in all_convs:
        short_id = c.id[:8]
        rel_time = format_relative_time(c.last_modified)
        size_str = format_size(c.db_size)

        status_str = "LOCKED" if c.is_locked else c.status
        if c.is_locked:
            status_display = red("LOCKED")
        elif "IDLE" in c.status:
            status_display = green("IDLE")
        else:
            status_display = yellow(c.status[:8])

        title_display = c.title if c.title else dim("(Untitled)")
        if len(title_display) > 42:
            title_display = title_display[:39] + "..."

        if single_home:
            print(f" {short_id:<10} {c.step_count:<8} {rel_time:<14} {status_display:<18} {size_str:<10} {title_display}")
        else:
            prof = c.home_name[:9]
            print(f" {prof:<10} {short_id:<10} {c.step_count:<8} {rel_time:<14} {status_display:<18} {size_str:<10} {title_display}")

    print("=" * 96)
    print("View details : ./agy-session.sh info <id>")
    print("Move session : ./agy-session.sh move <id> --to <target_profile>")
    print("Copy session : ./agy-session.sh copy <id> --to <target_profile>\n")


def cmd_info(args: argparse.Namespace):
    """Show detailed information about a conversation."""
    homes = discover_homes(args.base_dir)
    from_h = resolve_home(args.from_home, homes, args.base_dir) if getattr(args, "from_home", None) else None

    conv, candidates = find_conversation(args.conversation_id, homes, from_h)
    if not conv:
        if candidates:
            print(yellow(f"Multiple conversations matched '{args.conversation_id}':"))
            for cand in candidates:
                print(f"  - [{cand.id[:8]}] {cand.title} (profile: {cand.home_name})")
            print("Please specify a more specific UUID or the --from <profile> flag.")
        else:
            print(red(f"Error: Conversation '{args.conversation_id}' not found."))
        sys.exit(1)

    detail = get_conversation_detail(conv)

    if getattr(args, "json", False):
        out = {
            "id": conv.id,
            "profile": conv.home_name,
            "title": conv.title,
            "preview": conv.preview,
            "status": conv.status,
            "is_locked": conv.is_locked,
            "locked_pid": conv.locked_pid,
            "steps": conv.step_count,
            "last_modified": conv.last_modified.isoformat() if conv.last_modified else None,
            "last_user_input_time": conv.last_user_input_time.isoformat() if conv.last_user_input_time else None,
            "db_size": conv.db_size,
            "brain_size": detail["brain_total_size"],
            "total_size": conv.db_size + detail["brain_total_size"],
            "workspace_uris": conv.workspace_uris,
            "project_id": conv.project_id,
            "agent_name": conv.agent_name,
            "prompts": detail["prompts"],
            "artifacts": detail["artifacts"],
        }
        print(json.dumps(out, indent=2))
        return

    total_size = conv.db_size + detail["brain_total_size"]

    print(f"\n{bold('Conversation Details:')}")
    print("=" * 70)
    print(f" {bold('ID')}            : {cyan(conv.id)}")
    print(f" {bold('Profile')}       : {green(conv.home_name)} ({conv.cli_dir})")
    print(f" {bold('Title')}         : {conv.title or '(Untitled)'}")
    if conv.preview and conv.preview != conv.title:
        print(f" {bold('Preview')}       : {dim(conv.preview[:100])}")
    print(f" {bold('Status')}        : {red('LOCKED (running)') if conv.is_locked else green(conv.status)}")
    if conv.locked_pid:
        print(f" {bold('Locked PID')}    : {conv.locked_pid}")
    print(f" {bold('Step Count')}    : {conv.step_count}")
    print(f" {bold('Last Active')}   : {conv.last_modified} ({format_relative_time(conv.last_modified)})")
    if conv.workspace_uris:
        print(f" {bold('Workspace')}     : {', '.join(conv.workspace_uris)}")

    print(f"\n{bold('Disk Footprint:')}")
    print(f"  - Database (.db)    : {format_size(conv.db_size)}")
    print(f"  - Brain Directory   : {format_size(detail['brain_total_size'])}")
    print(f"  - Total Footprint   : {bold(format_size(total_size))}")

    if detail["artifacts"]:
        art_count = len(detail["artifacts"])
        print(f"\n{bold(f'Artifacts in Session ({art_count}):')}")
        for art in detail["artifacts"]:
            print(f"  - {art['name']:<32} {format_size(art['size']):<10}")

    if detail["prompts"]:
        print(f"\n{bold('Prompt History (Recent):')}")
        for idx, p in enumerate(detail["prompts"][-5:], 1):
            clean_p = p.replace("\n", " ")
            if len(clean_p) > 80:
                clean_p = clean_p[:77] + "..."
            print(f"  {idx}. {clean_p}")

    resume_cmd = (
        f"agy --conversation {conv.id}"
        if conv.home_name == "default"
        else f"./agy.sh {conv.home_name} --conversation {conv.id}"
    )
    print(f"\n{bold('Resume Command:')}")
    print(f"  {resume_cmd}")
    print("=" * 70 + "\n")


# -----------------------------------------------------------------------------
# Move & Copy CLI Commands
# -----------------------------------------------------------------------------

def cmd_move_or_copy(args: argparse.Namespace, is_move: bool):
    """Handle CLI move and copy commands."""
    homes = discover_homes(args.base_dir)
    from_h = resolve_home(args.from_home, homes, args.base_dir) if getattr(args, "from_home", None) else None

    # Resolve target home
    dst_h = resolve_home(args.to_home, homes, args.base_dir)
    if not dst_h:
        if getattr(args, "create", False):
            print(f"[*] Profile '{args.to_home}' not found. Creating it...")
            dst_h = create_profile(args.to_home, args.base_dir)
            homes = discover_homes(args.base_dir)
        else:
            print(red(f"Error: Destination profile '{args.to_home}' does not exist."))
            print(f"Add --create to create it automatically, or run './agy.sh new {args.to_home}'.")
            sys.exit(1)

    conv, candidates = find_conversation(args.conversation_id, homes, from_h)
    if not conv:
        if candidates:
            print(yellow(f"Multiple conversations matched '{args.conversation_id}':"))
            for cand in candidates:
                print(f"  - [{cand.id[:8]}] {cand.title} (profile: {cand.home_name})")
            print("Please specify full UUID or --from <profile> flag.")
        else:
            print(red(f"Error: Conversation '{args.conversation_id}' not found."))
        sys.exit(1)

    src_h = homes.get(conv.home_name)
    if not src_h:
        src_h = AgyHome(name=conv.home_name, home_dir=conv.cli_dir.parent.parent, cli_dir=conv.cli_dir)

    success, msg = transfer_conversation(
        src_home=src_h,
        dst_home=dst_h,
        conv_id=conv.id,
        is_move=is_move,
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
        set_active=getattr(args, "set_active", False),
        progress_callback=print,
    )

    if not success:
        print(red(f"\n[!] Failed: {msg}"))
        sys.exit(1)
    else:
        print(green(f"\n{msg}\n"))


def cmd_consolidate(args: argparse.Namespace):
    """Consolidate conversations from all profiles into a single folder (~/.gemini)."""
    homes = discover_homes(args.base_dir)
    target_h = resolve_home(args.target_home, homes, args.base_dir) if getattr(args, "target_home", None) else homes.get("default")
    if not target_h:
        print(red("Error: Target profile not found."))
        sys.exit(1)

    print(f"\n{bold('Consolidating All Conversations into Single Folder:')}")
    print("=" * 76)
    print(f" Target Folder : {cyan(str(target_h.cli_dir / 'conversations'))}")
    print(f" Target Profile: {green(target_h.name)}")
    print(f" Mode          : {'Copy (keep profile originals)' if getattr(args, 'copy', False) else 'Move (consolidate to central)'}")
    print(f" Link Profiles : {'Yes (profiles will share central folder)' if getattr(args, 'link', False) else 'No'}")
    if getattr(args, 'dry_run', False):
        print(f" Execution     : {yellow('DRY RUN (simulation only)')}")
    print("=" * 76 + "\n")

    stats = consolidate_all_conversations(
        homes=homes,
        target_home=target_h,
        is_move=not getattr(args, "copy", False),
        link_after=getattr(args, "link", False),
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
        progress_callback=print,
    )

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return

    print("\n" + "=" * 76)
    print(bold("Consolidation Summary:"))
    print(f"  - Profiles Scanned     : {stats['profiles_scanned']}")
    print(f"  - Transferred          : {green(len(stats['transferred']))}")
    print(f"  - Skipped (Active Lock): {yellow(len(stats['skipped_locked']))}")
    print(f"  - Skipped (Already in Hub): {len(stats['skipped_exists'])}")
    print(f"  - Already Linked       : {len(stats['skipped_linked'])}")
    print(f"  - Failed               : {red(len(stats['failed'])) if stats['failed'] else '0'}")
    if stats['linked_profiles']:
        print(f"  - Profiles Linked      : {magenta(', '.join(stats['linked_profiles']))}")
    print("=" * 76)
    target_conv_dir = target_h.cli_dir / "conversations"
    print(f"All consolidated conversations are now in: {bold(str(target_conv_dir))}\n")


def cmd_single_folder(args: argparse.Namespace):
    """Manage single-folder shared conversations mode."""
    action = getattr(args, "action", "status") or "status"
    action = action.lower()
    base = args.base_dir.resolve() if args.base_dir else get_base_user_home()
    homes = discover_homes(base)
    target_h = homes.get("default")
    if not target_h:
        print(red("Error: Default profile (~/.gemini) not found."))
        sys.exit(1)

    if action in ("status", "info"):
        is_globally_on = is_single_folder_globally_enabled(base)
        central_convs = get_conversations(target_h)
        total_central_size = sum(c.db_size for c in central_convs)

        print(f"\n{bold('Antigravity Single-Folder Conversations Mode:')}")
        print("=" * 80)
        print(f" Central Directory  : {cyan(str(target_h.cli_dir / 'conversations'))}")
        print(f" Global Config      : {green('ENABLED (new profiles will share central)') if is_globally_on else dim('DISABLED (profiles use isolated storage by default)')}")
        print(f" Central Sessions   : {len(central_convs)} conversations ({format_size(total_central_size)})")
        print("-" * 80)
        print(f" {'Profile':<14} {'Storage Mode':<18} {'Sessions':<12} {'Conversations Path'}")
        print("-" * 80)

        for name, h in sorted(homes.items(), key=lambda x: (not x[1].is_default, x[0])):
            if h.is_default:
                mode_str = green("CENTRAL HUB")
                sess_str = str(len(central_convs))
                loc_str = str(h.cli_dir / "conversations")
            elif h.is_linked_to_central:
                mode_str = magenta("LINKED (shared)")
                sess_str = "(shared)"
                loc_str = f"-> {target_h.cli_dir / 'conversations'}"
            else:
                convs = get_conversations(h)
                mode_str = yellow("ISOLATED (local)")
                sess_str = str(len(convs))
                loc_str = str(h.cli_dir / "conversations")

            print(f" {name:<14} {mode_str:<27} {sess_str:<12} {loc_str}")

        print("=" * 80)
        print("To enable  : ./agy-session.sh single-folder enable")
        print("To disable : ./agy-session.sh single-folder disable\n")

    elif action in ("enable", "on"):
        ok, msg = set_single_folder_mode(
            homes=homes,
            target_home=target_h,
            enable=True,
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
            progress_callback=print,
        )
        if ok:
            print(green(f"\n{msg}\n"))
        else:
            print(red(f"\n[!] Failed: {msg}\n"))
            sys.exit(1)

    elif action in ("disable", "off", "isolate"):
        ok, msg = set_single_folder_mode(
            homes=homes,
            target_home=target_h,
            enable=False,
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
            progress_callback=print,
        )
        if ok:
            print(green(f"\n{msg}\n"))
        else:
            print(red(f"\n[!] Failed: {msg}\n"))
            sys.exit(1)


def cmd_delete(args: argparse.Namespace):
    """Handle CLI delete command."""
    homes = discover_homes(args.base_dir)
    from_h = resolve_home(args.from_home, homes, args.base_dir) if getattr(args, "from_home", None) else None

    conv, candidates = find_conversation(args.conversation_id, homes, from_h)
    if not conv:
        if candidates:
            print(yellow(f"Multiple conversations matched '{args.conversation_id}':"))
            for cand in candidates:
                print(f"  - [{cand.id[:8]}] {cand.title} (profile: {cand.home_name})")
            print("Please specify full UUID or --from <profile> flag.")
        else:
            print(red(f"Error: Conversation '{args.conversation_id}' not found."))
        sys.exit(1)

    src_h = homes.get(conv.home_name)
    if not src_h:
        src_h = AgyHome(name=conv.home_name, home_dir=conv.cli_dir.parent.parent, cli_dir=conv.cli_dir)

    if not getattr(args, "force", False) and not getattr(args, "dry_run", False):
        confirm = input(f"Are you sure you want to PERMANENTLY delete conversation '{conv.id}' ({conv.title})? [y/N]: ")
        if confirm.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    success, msg = delete_conversation(
        home=src_h,
        conv_id=conv.id,
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
    )

    if not success:
        print(red(f"\n[!] Failed: {msg}"))
        sys.exit(1)
    else:
        print(green(f"\n{msg}\n"))


def cmd_consolidate(args: argparse.Namespace):
    """Consolidate conversations from all profiles into a single folder (~/.gemini)."""
    homes = discover_homes(args.base_dir)
    target_h = resolve_home(args.target_home, homes, args.base_dir) if getattr(args, "target_home", None) else homes.get("default")
    if not target_h:
        print(red("Error: Target profile not found."))
        sys.exit(1)

    print(f"\n{bold('Consolidating All Conversations into Single Folder:')}")
    print("=" * 76)
    print(f" Target Folder : {cyan(str(target_h.cli_dir / 'conversations'))}")
    print(f" Target Profile: {green(target_h.name)}")
    print(f" Mode          : {'Copy (keep profile originals)' if getattr(args, 'copy', False) else 'Move (consolidate to central)'}")
    print(f" Link Profiles : {'Yes (profiles will share central folder)' if getattr(args, 'link', False) else 'No'}")
    if getattr(args, 'dry_run', False):
        print(f" Execution     : {yellow('DRY RUN (simulation only)')}")
    print("=" * 76 + "\n")

    stats = consolidate_all_conversations(
        homes=homes,
        target_home=target_h,
        is_move=not getattr(args, "copy", False),
        link_after=getattr(args, "link", False),
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
        progress_callback=print,
    )

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return

    print("\n" + "=" * 76)
    print(bold("Consolidation Summary:"))
    print(f"  - Profiles Scanned     : {stats['profiles_scanned']}")
    print(f"  - Transferred          : {green(len(stats['transferred']))}")
    print(f"  - Skipped (Active Lock): {yellow(len(stats['skipped_locked']))}")
    print(f"  - Skipped (Already in Hub): {len(stats['skipped_exists'])}")
    print(f"  - Already Linked       : {len(stats['skipped_linked'])}")
    print(f"  - Failed               : {red(len(stats['failed'])) if stats['failed'] else '0'}")
    if stats['linked_profiles']:
        print(f"  - Profiles Linked      : {magenta(', '.join(stats['linked_profiles']))}")
    print("=" * 76)
    target_conv_dir = target_h.cli_dir / "conversations"
    print(f"All consolidated conversations are now in: {bold(str(target_conv_dir))}\n")


def cmd_single_folder(args: argparse.Namespace):
    """Manage single-folder shared conversations mode."""
    action = getattr(args, "action", "status") or "status"
    action = action.lower()
    base = args.base_dir.resolve() if args.base_dir else get_base_user_home()
    homes = discover_homes(base)
    target_h = homes.get("default")
    if not target_h:
        print(red("Error: Default profile (~/.gemini) not found."))
        sys.exit(1)

    if action in ("status", "info"):
        is_globally_on = is_single_folder_globally_enabled(base)
        central_convs = get_conversations(target_h)
        total_central_size = sum(c.db_size for c in central_convs)

        print(f"\n{bold('Antigravity Single-Folder Conversations Mode:')}")
        print("=" * 80)
        print(f" Central Directory  : {cyan(str(target_h.cli_dir / 'conversations'))}")
        print(f" Global Config      : {green('ENABLED (new profiles will share central)') if is_globally_on else dim('DISABLED (profiles use isolated storage by default)')}")
        print(f" Central Sessions   : {len(central_convs)} conversations ({format_size(total_central_size)})")
        print("-" * 80)
        print(f" {'Profile':<14} {'Storage Mode':<18} {'Sessions':<12} {'Conversations Path'}")
        print("-" * 80)

        for name, h in sorted(homes.items(), key=lambda x: (not x[1].is_default, x[0])):
            if h.is_default:
                mode_str = green("CENTRAL HUB")
                sess_str = str(len(central_convs))
                loc_str = str(h.cli_dir / "conversations")
            elif h.is_linked_to_central:
                mode_str = magenta("LINKED (shared)")
                sess_str = "(shared)"
                loc_str = f"-> {target_h.cli_dir / 'conversations'}"
            else:
                convs = get_conversations(h)
                mode_str = yellow("ISOLATED (local)")
                sess_str = str(len(convs))
                loc_str = str(h.cli_dir / "conversations")

            print(f" {name:<14} {mode_str:<27} {sess_str:<12} {loc_str}")

        print("=" * 80)
        print("To enable  : ./agy-session.sh single-folder enable")
        print("To disable : ./agy-session.sh single-folder disable\n")

    elif action in ("enable", "on"):
        ok, msg = set_single_folder_mode(
            homes=homes,
            target_home=target_h,
            enable=True,
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
            progress_callback=print,
        )
        if ok:
            print(green(f"\n{msg}\n"))
        else:
            print(red(f"\n[!] Failed: {msg}\n"))
            sys.exit(1)

    elif action in ("disable", "off", "isolate"):
        ok, msg = set_single_folder_mode(
            homes=homes,
            target_home=target_h,
            enable=False,
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
            progress_callback=print,
        )
        if ok:
            print(green(f"\n{msg}\n"))
        else:
            print(red(f"\n[!] Failed: {msg}\n"))
            sys.exit(1)


# -----------------------------------------------------------------------------
# Interactive Browser Mode
# -----------------------------------------------------------------------------

def run_interactive_browser(base_dir: Optional[Path] = None):
    """Interactive TUI menu for browsing and moving sessions."""
    if not sys.stdin.isatty():
        print("Non-interactive terminal detected. Run './agy-session.sh --help' for CLI options.")
        return

    base = base_dir.resolve() if base_dir else get_base_user_home()

    while True:
        homes = discover_homes(base)
        print("\n" + "=" * 76)
        print(bold("             Antigravity Session & Conversation Browser             "))
        print("=" * 76)

        home_list = list(homes.values())
        home_list.sort(key=lambda h: (not h.is_default, h.name))

        print(f"\n{bold('Available Profiles (AGY Homes):')}")
        for idx, h in enumerate(home_list, 1):
            convs = get_conversations(h)
            total_db = sum(c.db_size for c in convs)
            badges = []
            if h.is_current:
                badges.append("current")
            if h.is_default:
                badges.append("default")
            badge_str = f" [{', '.join(badges)}]" if badges else ""
            print(f"  {bold(idx)}) {h.name:<12} ({len(convs):>2} sessions, {format_size(total_db):>9}){badge_str}")

        print(f"  {bold('A')}) Browse ALL conversations across all profiles")
        print(f"  {bold('C')}) Consolidate all conversations into ~/.gemini (single folder)")
        print(f"  {bold('S')}) Single-Folder Mode (Status & Toggle)")
        print(f"  {bold('N')}) Create a new profile")
        print(f"  {bold('Q')}) Quit")

        choice = input(f"\nSelect an option (1-{len(home_list)}, A, C, S, N, Q): ").strip().lower()

        if choice in ("q", "quit", "exit"):
            print("Exiting browser. Goodbye!")
            break

        if choice == "c":
            confirm = input("\nConsolidate all conversations from all profiles into ~/.gemini/antigravity-cli/conversations? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                link_conf = input("Also link profiles to single central folder after consolidating? [y/N]: ").strip().lower()
                link_after = link_conf in ("y", "yes")
                class DummyConsArgs:
                    target_home = None
                    copy = False
                    link = link_after
                    force = False
                    dry_run = False
                    json = False
                    base_dir = base
                cmd_consolidate(DummyConsArgs())
                input("Press [Enter] to return...")
            continue

        if choice == "s":
            class DummySfArgs:
                action = "status"
                force = False
                dry_run = False
                json = False
                base_dir = base
            cmd_single_folder(DummySfArgs())
            sf_choice = input("Option: [E]nable single-folder mode | [D]isable (restore isolated) | [B]ack: ").strip().lower()
            if sf_choice in ("e", "enable"):
                DummySfArgs.action = "enable"
                cmd_single_folder(DummySfArgs())
            elif sf_choice in ("d", "disable"):
                DummySfArgs.action = "disable"
                cmd_single_folder(DummySfArgs())
            input("Press [Enter] to return...")
            continue

        if choice == "n":
            new_name = input("Enter new profile name: ").strip()
            if new_name:
                try:
                    create_profile(new_name, base)
                    print(green(f"Successfully created profile '{new_name}'."))
                except Exception as e:
                    print(red(f"Error creating profile: {e}"))
            continue

        selected_home: Optional[AgyHome] = None
        browse_all = False

        if choice == "a":
            browse_all = True
        else:
            try:
                sel_idx = int(choice)
                if 1 <= sel_idx <= len(home_list):
                    selected_home = home_list[sel_idx - 1]
                else:
                    print(yellow("Invalid selection."))
                    continue
            except ValueError:
                print(yellow("Invalid selection."))
                continue

        # Sub-loop for conversation list
        _browse_conversation_loop(selected_home, browse_all, homes, base)


def _browse_conversation_loop(
    selected_home: Optional[AgyHome],
    browse_all: bool,
    homes: Dict[str, AgyHome],
    base_dir: Path
):
    search_query = ""

    while True:
        # Fetch conversations
        if browse_all:
            convs: List[ConversationInfo] = []
            for h in homes.values():
                convs.extend(get_conversations(h))
            title_header = "All Conversations Across All Profiles"
        else:
            convs = get_conversations(selected_home)
            title_header = f"Conversations in '{selected_home.name}'"

        if search_query:
            q = search_query.lower()
            convs = [
                c for c in convs
                if q in c.id.lower() or q in c.title.lower() or any(q in w.lower() for w in c.workspace_uris)
            ]

        # Sort by date descending
        convs.sort(
            key=lambda c: c.last_modified or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )

        print("\n" + "-" * 76)
        print(bold(f"{title_header} ({len(convs)} items)") + (f" [Filter: '{search_query}']" if search_query else ""))
        print("-" * 76)

        if not convs:
            print("  (No conversations found)")
        else:
            print(f" {'#':<3} {'ID':<10} {'Steps':<7} {'Active':<12} {'Size':<9} {'Title'}")
            print("-" * 76)
            for idx, c in enumerate(convs, 1):
                short_id = c.id[:8]
                rel_time = format_relative_time(c.last_modified)
                size_str = format_size(c.db_size)
                title_d = c.title if c.title else dim("(Untitled)")
                if len(title_d) > 34:
                    title_d = title_d[:31] + "..."
                prof_tag = f"[{c.home_name}] " if browse_all else ""
                lock_tag = red(" [LOCKED]") if c.is_locked else ""
                print(f" {idx:<3} {short_id:<10} {c.step_count:<7} {rel_time:<12} {size_str:<9} {prof_tag}{title_d}{lock_tag}")

        print("-" * 76)
        print("Commands: [1-N] Select conversation | [S] Search / Filter | [C] Clear filter | [B] Back | [Q] Quit")
        cmd = input("Choice: ").strip()

        if not cmd:
            continue
        if cmd.lower() in ("b", "back"):
            break
        if cmd.lower() in ("q", "quit"):
            sys.exit(0)
        if cmd.lower() in ("c", "clear"):
            search_query = ""
            continue
        if cmd.lower() in ("s", "search"):
            search_query = input("Enter search keyword (or empty to clear): ").strip()
            continue

        try:
            c_idx = int(cmd)
            if 1 <= c_idx <= len(convs):
                chosen_conv = convs[c_idx - 1]
                _conversation_action_menu(chosen_conv, homes, base_dir)
            else:
                print(yellow("Invalid index."))
        except ValueError:
            print(yellow("Invalid command."))


def _conversation_action_menu(conv: ConversationInfo, homes: Dict[str, AgyHome], base_dir: Path):
    """Action menu for a single selected conversation."""
    while True:
        print("\n" + "=" * 65)
        print(bold(f"Conversation: [{conv.id[:8]}] {conv.title or '(Untitled)'}"))
        print("=" * 65)
        print(f" Full UUID      : {conv.id}")
        print(f" Current Profile: {green(conv.home_name)}")
        print(f" Step Count     : {conv.step_count}")
        print(f" Last Active    : {format_relative_time(conv.last_modified)}")
        print(f" Status         : {red('LOCKED (running)') if conv.is_locked else green(conv.status)}")
        print(f" Database Size  : {format_size(conv.db_size)}")

        print("\nActions:")
        print(f"  {bold('V')}) View full details & prompt history")
        print(f"  {bold('M')}) Move conversation to another AGY profile")
        print(f"  {bold('C')}) Copy conversation to another AGY profile")
        print(f"  {bold('D')}) Delete conversation")
        print(f"  {bold('B')}) Back to list")

        action = input("\nSelect action (V, M, C, D, B): ").strip().lower()

        if action in ("b", "back", ""):
            break

        if action == "v":
            # Show info
            class DummyArgs:
                conversation_id = conv.id
                from_home = conv.home_name
                json = False
                base_dir = base_dir
            cmd_info(DummyArgs())
            input("Press [Enter] to return...")
            continue

        if action in ("m", "c"):
            is_move = (action == "m")
            action_word = "Move" if is_move else "Copy"

            # Destination profile selection
            other_homes = [h for h in homes.values() if h.name != conv.home_name]
            other_homes.sort(key=lambda h: (not h.is_default, h.name))

            print(f"\nSelect target profile to {action_word.lower()} conversation into:")
            for idx, h in enumerate(other_homes, 1):
                print(f"  {bold(idx)}) {h.name} ({h.home_dir})")
            print(f"  {bold('N')}) Create a new profile")

            dest_input = input(f"Target profile (1-{len(other_homes)}, N, or cancel): ").strip()
            if not dest_input or dest_input.lower() in ("cancel", "b"):
                continue

            target_home: Optional[AgyHome] = None
            if dest_input.lower() == "n":
                new_p = input("Enter new profile name: ").strip()
                if not new_p:
                    continue
                try:
                    target_home = create_profile(new_p, base_dir)
                    homes[target_home.name] = target_home
                except Exception as e:
                    print(red(f"Error creating profile: {e}"))
                    continue
            else:
                try:
                    t_idx = int(dest_input)
                    if 1 <= t_idx <= len(other_homes):
                        target_home = other_homes[t_idx - 1]
                    else:
                        print(yellow("Invalid selection."))
                        continue
                except ValueError:
                    # Maybe typed name directly
                    target_home = resolve_home(dest_input, homes, base_dir)
                    if not target_home:
                        print(yellow(f"Unknown profile '{dest_input}'."))
                        continue

            confirm = input(
                f"\nAre you sure you want to {action_word.upper()} conversation\n"
                f"  '{conv.title or conv.id[:8]}' from '{conv.home_name}' -> '{target_home.name}'? [y/N]: "
            ).strip().lower()

            if confirm not in ("y", "yes"):
                print("Operation cancelled.")
                continue

            src_h = homes.get(conv.home_name)
            if not src_h:
                src_h = AgyHome(name=conv.home_name, home_dir=conv.cli_dir.parent.parent, cli_dir=conv.cli_dir)

            success, msg = transfer_conversation(
                src_home=src_h,
                dst_home=target_home,
                conv_id=conv.id,
                is_move=is_move,
                progress_callback=print,
            )

            if success:
                print(green(f"\n{msg}\n"))
                input("Press [Enter] to return...")
                if is_move:
                    break  # Removed from current list
            else:
                print(red(f"\n[!] Error: {msg}\n"))
                input("Press [Enter] to continue...")

        if action == "d":
            confirm = input(
                f"\nAre you sure you want to PERMANENTLY DELETE conversation\n"
                f"  '{conv.title or conv.id[:8]}' ({conv.id}) from '{conv.home_name}'? [y/N]: "
            ).strip().lower()

            if confirm in ("y", "yes"):
                src_h = homes.get(conv.home_name)
                if not src_h:
                    src_h = AgyHome(name=conv.home_name, home_dir=conv.cli_dir.parent.parent, cli_dir=conv.cli_dir)

                success, msg = delete_conversation(src_h, conv.id)
                if success:
                    print(green(f"\n{msg}\n"))
                    input("Press [Enter] to return...")
                    break
                else:
                    print(red(f"\n[!] Error: {msg}\n"))
                    input("Press [Enter] to continue...")


# -----------------------------------------------------------------------------
# Main Argument Parsing & Dispatch
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-session",
        description="Antigravity Session & Conversation Manager: Browse, inspect, move, and copy sessions across AGY homes.",
    )
    parser.add_argument("--base-dir", type=Path, default=None, help="Base user home directory (default: auto-detected)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # homes
    homes_p = subparsers.add_parser("homes", aliases=["profiles", "list-homes"], help="List all AGY profiles/homes")
    homes_p.add_argument("--json", action="store_true", help="Output in JSON format")

    # list
    list_p = subparsers.add_parser("list", aliases=["ls"], help="List conversations in an AGY home")
    list_p.add_argument("home", nargs="?", default=None, help="Profile name or path (default: current or all if --all)")
    list_p.add_argument("-a", "--all", action="store_true", help="List conversations across all profiles")
    list_p.add_argument("-s", "--search", type=str, default=None, help="Filter by title, ID, or workspace")
    list_p.add_argument("--sort", choices=["date", "steps", "size", "title"], default="date", help="Sort field (default: date)")
    list_p.add_argument("-r", "--reverse", action="store_true", help="Reverse sort order")
    list_p.add_argument("-n", "--limit", type=int, default=0, help="Maximum number of conversations to display")
    list_p.add_argument("--json", action="store_true", help="Output in JSON format")

    # info
    info_p = subparsers.add_parser("info", aliases=["show", "view"], help="Show detailed conversation information")
    info_p.add_argument("conversation_id", help="Conversation UUID, prefix, or search term")
    info_p.add_argument("-f", "--from", dest="from_home", default=None, help="Source profile name (optional)")
    info_p.add_argument("--json", action="store_true", help="Output in JSON format")

    # move
    move_p = subparsers.add_parser("move", aliases=["mv"], help="Move a conversation from one AGY home to another")
    move_p.add_argument("conversation_id", help="Conversation UUID, prefix, or search term")
    move_p.add_argument("-t", "--to", dest="to_home", required=True, help="Target profile name or directory")
    move_p.add_argument("-f", "--from", dest="from_home", default=None, help="Source profile name (optional)")
    move_p.add_argument("--force", action="store_true", help="Bypass lock checks and overwrite existing destination")
    move_p.add_argument("--dry-run", action="store_true", help="Simulate without modifying files")
    move_p.add_argument("--set-active", action="store_true", help="Set conversation as active in target profile")
    move_p.add_argument("--create", action="store_true", help="Create destination profile if it does not exist")

    # copy
    copy_p = subparsers.add_parser("copy", aliases=["cp"], help="Copy a conversation from one AGY home to another")
    copy_p.add_argument("conversation_id", help="Conversation UUID, prefix, or search term")
    copy_p.add_argument("-t", "--to", dest="to_home", required=True, help="Target profile name or directory")
    copy_p.add_argument("-f", "--from", dest="from_home", default=None, help="Source profile name (optional)")
    copy_p.add_argument("--force", action="store_true", help="Overwrite existing conversation in destination")
    copy_p.add_argument("--dry-run", action="store_true", help="Simulate without modifying files")
    copy_p.add_argument("--set-active", action="store_true", help="Set conversation as active in target profile")
    copy_p.add_argument("--create", action="store_true", help="Create destination profile if it does not exist")

    # delete
    del_p = subparsers.add_parser("delete", aliases=["rm", "del"], help="Permanently delete a conversation")
    del_p.add_argument("conversation_id", help="Conversation UUID, prefix, or search term")
    del_p.add_argument("-f", "--from", dest="from_home", default=None, help="Source profile name (optional)")
    del_p.add_argument("--force", action="store_true", help="Bypass confirmation and lock check")
    del_p.add_argument("--dry-run", action="store_true", help="Simulate deletion")

    # consolidate
    cons_p = subparsers.add_parser("consolidate", aliases=["merge-all", "centralize"], help="Consolidate all conversations into a single folder (~/.gemini)")
    cons_p.add_argument("-t", "--target", dest="target_home", default=None, help="Target profile name or directory (default: default / ~/.gemini)")
    cons_p.add_argument("--copy", action="store_true", help="Copy conversations instead of moving (preserves originals in profiles)")
    cons_p.add_argument("--link", action="store_true", help="Link profile directories to single central folder after consolidating")
    cons_p.add_argument("--force", action="store_true", help="Bypass lock checks on active sessions and overwrite existing in destination")
    cons_p.add_argument("--dry-run", action="store_true", help="Simulate consolidation without modifying files")
    cons_p.add_argument("--json", action="store_true", help="Output consolidation summary in JSON format")

    # single-folder
    sf_p = subparsers.add_parser("single-folder", aliases=["singlefolder", "shared-storage"], help="Configure all profiles to share a single central conversations directory")
    sf_p.add_argument("action", nargs="?", default="status", choices=["status", "enable", "disable", "on", "off", "info"], help="Action to perform (default: status)")
    sf_p.add_argument("--force", action="store_true", help="Bypass lock checks during consolidation")
    sf_p.add_argument("--dry-run", action="store_true", help="Simulate action without making changes")
    sf_p.add_argument("--json", action="store_true", help="Output status in JSON format")

    # browse
    subparsers.add_parser("browse", aliases=["interactive"], help="Launch interactive conversation browser")

    return parser


def main():
    global USE_COLOR
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "no_color", False):
        USE_COLOR = False

    cmd = args.command
    if not cmd:
        # Default behavior: interactive mode if TTY, otherwise print help
        if sys.stdin.isatty():
            run_interactive_browser(args.base_dir)
        else:
            parser.print_help()
        return

    if cmd in ("homes", "profiles", "list-homes"):
        cmd_homes(args)
    elif cmd in ("list", "ls"):
        cmd_list(args)
    elif cmd in ("info", "show", "view"):
        cmd_info(args)
    elif cmd in ("move", "mv"):
        cmd_move_or_copy(args, is_move=True)
    elif cmd in ("copy", "cp"):
        cmd_move_or_copy(args, is_move=False)
    elif cmd in ("delete", "rm", "del"):
        cmd_delete(args)
    elif cmd in ("consolidate", "merge-all", "centralize"):
        cmd_consolidate(args)
    elif cmd in ("single-folder", "singlefolder", "shared-storage"):
        cmd_single_folder(args)
    elif cmd in ("browse", "interactive"):
        run_interactive_browser(args.base_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)

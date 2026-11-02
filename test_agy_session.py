#!/usr/bin/env python3
"""Comprehensive test suite for agy_session.py"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Add project dir to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from agy_session import (
    AgyHome,
    discover_homes,
    resolve_home,
    get_conversations,
    get_conversation_detail,
    find_conversation,
    transfer_conversation,
    delete_conversation,
    ensure_summaries_db,
    is_conversation_locked,
    consolidate_all_conversations,
    set_single_folder_mode,
)


def run_tests():
    print("==================================================================")
    print("Running Automated Tests for Antigravity Session Manager")
    print("==================================================================")

    passed = 0
    total = 0

    def assert_true(cond, msg):
        nonlocal passed, total
        total += 1
        if cond:
            print(f"  [PASS] {msg}")
            passed += 1
        else:
            print(f"  [FAIL] {msg}")
            raise AssertionError(msg)

    # Test 1: Real Profile Discovery
    print("\n--- Test 1: Profile Discovery ---")
    homes = discover_homes()
    assert_true(len(homes) >= 3, f"Discovered {len(homes)} profiles")
    assert_true("seekn" in homes, "'seekn' profile found")
    assert_true("default" in homes, "'default' profile found")

    # Test 2: Profile Resolution
    print("\n--- Test 2: Profile Resolution ---")
    h_default = resolve_home("default", homes)
    assert_true(h_default is not None and h_default.name == "default", "Resolved 'default'")

    h_seekn = resolve_home("seekn", homes)
    assert_true(h_seekn is not None and h_seekn.name == "seekn", "Resolved 'seekn'")

    h_dot = resolve_home(".agy-seekn", homes)
    assert_true(h_dot is not None and h_dot.name == "seekn", "Resolved '.agy-seekn'")

    # Test 3: Conversation Retrieval
    print("\n--- Test 3: Conversation Retrieval ---")
    seekn_convs = get_conversations(h_seekn)
    assert_true(len(seekn_convs) >= 1, f"Found {len(seekn_convs)} conversations in seekn")

    # Test 4: Conversation Details & Prompt History
    print("\n--- Test 4: Conversation Details ---")
    cid_sample = seekn_convs[0].id
    detail = get_conversation_detail(seekn_convs[0])
    assert_true("prompts" in detail, "Prompts key in detail")
    assert_true("artifacts" in detail, "Artifacts key in detail")
    assert_true("brain_total_size" in detail, "brain_total_size in detail")

    # Test 5: End-to-End Migration in Sandbox
    print("\n--- Test 5: Isolated Sandbox Copy & Move ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        src_profile = tmp_path / ".agy-sandbox_src"
        dst_profile = tmp_path / ".agy-sandbox_dst"

        src_cli = src_profile / ".gemini" / "antigravity-cli"
        dst_cli = dst_profile / ".gemini" / "antigravity-cli"

        src_cli.mkdir(parents=True, exist_ok=True)
        dst_cli.mkdir(parents=True, exist_ok=True)

        home_src = AgyHome(name="sandbox_src", home_dir=src_profile, cli_dir=src_cli)
        home_dst = AgyHome(name="sandbox_dst", home_dir=dst_profile, cli_dir=dst_cli)

        test_cid = "11111111-2222-3333-4444-555555555555"

        # Create mock source data
        (src_cli / "conversations").mkdir(parents=True)
        (src_cli / "brain" / test_cid).mkdir(parents=True)
        (src_cli / "annotations").mkdir(parents=True)

        # 1. Mock DB
        db_file = src_cli / "conversations" / f"{test_cid}.db"
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("CREATE TABLE steps (idx integer, step_type integer, PRIMARY KEY(idx))")
            conn.execute("INSERT INTO steps VALUES (0, 1), (1, 2), (2, 3)")
            conn.commit()

        # 2. Mock Brain Artifact
        art_file = src_cli / "brain" / test_cid / "test_plan.md"
        art_file.write_text("# Test Plan\nContent here.", encoding="utf-8")

        # 3. Mock Annotation
        ann_file = src_cli / "annotations" / f"{test_cid}.pbtxt"
        ann_file.write_text('title:"Mock Sandbox Task"', encoding="utf-8")

        # 4. Mock Summaries DB
        sum_db = src_cli / "conversation_summaries.db"
        ensure_summaries_db(sum_db)
        with sqlite3.connect(str(sum_db)) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, status, last_user_input_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (test_cid, "Mock Sandbox Task", "Mock Preview", 3, "2026-11-01 00:00:00+00:00", '["file:///workspace"]', "CASCADE_RUN_STATUS_IDLE", "2026-11-01 00:00:00+00:00"))
            conn.commit()

        # 5. Mock History
        hist_file = src_cli / "history.jsonl"
        hist_file.write_text(
            f'{{"display": "hello world", "conversationId": "{test_cid}"}}\n'
            f'{{"display": "unrelated task", "conversationId": "other-id"}}\n',
            encoding="utf-8"
        )

        # Step 5a: Test Copy
        ok, msg = transfer_conversation(home_src, home_dst, test_cid, is_move=False)
        assert_true(ok, f"Copy succeeded: {msg}")

        # Verify destination has DB, brain, annotation, summary, history
        assert_true((dst_cli / "conversations" / f"{test_cid}.db").is_file(), "Dst DB exists")
        assert_true((dst_cli / "brain" / test_cid / "test_plan.md").is_file(), "Dst brain artifact exists")
        assert_true((dst_cli / "annotations" / f"{test_cid}.pbtxt").is_file(), "Dst annotation exists")

        with sqlite3.connect(str(dst_cli / "conversation_summaries.db")) as conn:
            cur = conn.cursor()
            cur.execute("SELECT title, step_count FROM conversation_summaries WHERE conversation_id = ?", (test_cid,))
            row = cur.fetchone()
            assert_true(row is not None and row[0] == "Mock Sandbox Task" and row[1] == 3, "Dst summaries DB row intact")

        # Verify source still has files (since it was copy)
        assert_true((src_cli / "conversations" / f"{test_cid}.db").is_file(), "Src DB still exists after copy")

        # Step 5b: Test Collision without --force
        ok_fail, _ = transfer_conversation(home_src, home_dst, test_cid, is_move=False, force=False)
        assert_true(not ok_fail, "Refused to overwrite destination without --force")

        # Step 5c: Test Move to new destination
        dst2_profile = tmp_path / ".agy-sandbox_dst2"
        dst2_cli = dst2_profile / ".gemini" / "antigravity-cli"
        home_dst2 = AgyHome(name="sandbox_dst2", home_dir=dst2_profile, cli_dir=dst2_cli)

        ok_mv, msg_mv = transfer_conversation(home_src, home_dst2, test_cid, is_move=True)
        assert_true(ok_mv, f"Move succeeded: {msg_mv}")

        # Verify source has NO files now
        assert_true(not (src_cli / "conversations" / f"{test_cid}.db").exists(), "Src DB removed after move")
        assert_true(not (src_cli / "brain" / test_cid).exists(), "Src brain removed after move")
        assert_true(not (src_cli / "annotations" / f"{test_cid}.pbtxt").exists(), "Src annotation removed after move")

        with sqlite3.connect(str(sum_db)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM conversation_summaries WHERE conversation_id = ?", (test_cid,))
            assert_true(cur.fetchone()[0] == 0, "Src summary row deleted after move")

        # Verify source history still has 'unrelated task' but not 'hello world'
        src_hist_content = hist_file.read_text(encoding="utf-8")
        assert_true("unrelated task" in src_hist_content, "Unrelated history preserved")
        assert_true("hello world" not in src_hist_content, "Moved history filtered out from source")

        # Verify destination 2 has files
        assert_true((dst2_cli / "conversations" / f"{test_cid}.db").is_file(), "Dst2 DB exists")
        assert_true((dst2_cli / "brain" / test_cid / "test_plan.md").is_file(), "Dst2 brain artifact exists")

        # Step 5d: Test Delete
        ok_del, msg_del = delete_conversation(home_dst2, test_cid, force=True)
        assert_true(ok_del, "Delete succeeded")
        assert_true(not (dst2_cli / "conversations" / f"{test_cid}.db").exists(), "Deleted DB gone")
        assert_true(not (dst2_cli / "brain" / test_cid).exists(), "Deleted brain gone")

    # Test 6: Consolidate All Conversations in Sandbox
    print("\n--- Test 6: Consolidate All into Single Central Folder ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Central Hub
        hub_dir = tmp_path / ".gemini"
        hub_cli = hub_dir / "antigravity-cli"
        hub_cli.mkdir(parents=True)
        home_hub = AgyHome(name="default", home_dir=hub_dir, cli_dir=hub_cli, is_default=True)

        # Profile A
        pa_dir = tmp_path / ".agy-profa"
        pa_cli = pa_dir / ".gemini" / "antigravity-cli"
        pa_cli.mkdir(parents=True)
        home_pa = AgyHome(name="profa", home_dir=pa_dir, cli_dir=pa_cli)

        # Profile B
        pb_dir = tmp_path / ".agy-profb"
        pb_cli = pb_dir / ".gemini" / "antigravity-cli"
        pb_cli.mkdir(parents=True)
        home_pb = AgyHome(name="profb", home_dir=pb_dir, cli_dir=pb_cli)

        homes_dict = {"default": home_hub, "profa": home_pa, "profb": home_pb}

        # Setup mock conversation in profa
        cid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        (pa_cli / "conversations").mkdir(parents=True)
        (pa_cli / "brain" / cid_a).mkdir(parents=True)
        with sqlite3.connect(str(pa_cli / "conversations" / f"{cid_a}.db")) as conn:
            conn.execute("CREATE TABLE steps (idx int, PRIMARY KEY(idx))")
            conn.commit()
        sum_a = pa_cli / "conversation_summaries.db"
        ensure_summaries_db(sum_a)
        with sqlite3.connect(str(sum_a)) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, status, last_user_input_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (cid_a, "Task A", "Prev A", 1, "2026-11-01 00:00:00+00:00", "[]", "IDLE", "2026-11-01 00:00:00+00:00"))
            conn.commit()

        # Setup mock conversation in profb
        cid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        (pb_cli / "conversations").mkdir(parents=True)
        (pb_cli / "brain" / cid_b).mkdir(parents=True)
        with sqlite3.connect(str(pb_cli / "conversations" / f"{cid_b}.db")) as conn:
            conn.execute("CREATE TABLE steps (idx int, PRIMARY KEY(idx))")
            conn.commit()
        sum_b = pb_cli / "conversation_summaries.db"
        ensure_summaries_db(sum_b)
        with sqlite3.connect(str(sum_b)) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, status, last_user_input_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (cid_b, "Task B", "Prev B", 2, "2026-11-01 00:00:00+00:00", "[]", "IDLE", "2026-11-01 00:00:00+00:00"))
            conn.commit()

        # Run consolidation
        res = consolidate_all_conversations(
            homes=homes_dict,
            target_home=home_hub,
            is_move=True,
            link_after=False,
            force=False,
        )
        assert_true(len(res["transferred"]) == 2, "Consolidated 2 conversations")
        assert_true((hub_cli / "conversations" / f"{cid_a}.db").is_file(), "Task A in central hub")
        assert_true((hub_cli / "conversations" / f"{cid_b}.db").is_file(), "Task B in central hub")
        assert_true(len(get_conversations(home_hub)) == 2, "Central hub has both conversations")
        assert_true(len(get_conversations(home_pa)) == 0, "profa is now empty")
        assert_true(len(get_conversations(home_pb)) == 0, "profb is now empty")

    # Test 7: Single-Folder Mode Toggle
    print("\n--- Test 7: Single-Folder Mode Linking & Toggle ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Set env HOME to tmp_path to test marker file and profile links cleanly
        hub_dir = tmp_path / ".gemini"
        hub_cli = hub_dir / "antigravity-cli"
        hub_cli.mkdir(parents=True)
        home_hub = AgyHome(name="default", home_dir=hub_dir, cli_dir=hub_cli, is_default=True)

        pa_dir = tmp_path / ".agy-profa"
        pa_cli = pa_dir / ".gemini" / "antigravity-cli"
        pa_cli.mkdir(parents=True)
        (pa_cli / "conversations").mkdir(parents=True)
        (pa_cli / "brain").mkdir(parents=True)
        (pa_cli / "annotations").mkdir(parents=True)
        ensure_summaries_db(pa_cli / "conversation_summaries.db")
        home_pa = AgyHome(name="profa", home_dir=pa_dir, cli_dir=pa_cli)

        homes_dict = {"default": home_hub, "profa": home_pa}

        # Mock conversation in profa
        cid_x = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        with sqlite3.connect(str(pa_cli / "conversations" / f"{cid_x}.db")) as conn:
            conn.execute("CREATE TABLE steps (idx int, PRIMARY KEY(idx))")
            conn.commit()
        with sqlite3.connect(str(pa_cli / "conversation_summaries.db")) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries
                (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, status, last_user_input_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (cid_x, "Task X", "Prev X", 1, "2026-11-01 00:00:00+00:00", "[]", "IDLE", "2026-11-01 00:00:00+00:00"))
            conn.commit()

        # Enable single-folder mode (consolidate + symlink)
        orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp_path)
        try:
            ok, msg = set_single_folder_mode(homes_dict, home_hub, enable=True)
            assert_true(ok, f"Enable single-folder mode succeeded: {msg}")
            assert_true((hub_cli / "conversations" / f"{cid_x}.db").is_file(), "Task X moved to hub")
            assert_true((pa_cli / "conversations").is_symlink(), "profa conversations is symlink")
            assert_true((pa_cli / "brain").is_symlink(), "profa brain is symlink")
            assert_true((pa_cli / "conversation_summaries.db").is_symlink(), "profa summaries is symlink")

            # Check writing a new conversation through profa appears in central hub
            cid_y = "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
            with sqlite3.connect(str(pa_cli / "conversations" / f"{cid_y}.db")) as conn:
                conn.execute("CREATE TABLE steps (idx int, PRIMARY KEY(idx))")
                conn.commit()
            assert_true((hub_cli / "conversations" / f"{cid_y}.db").is_file(), "New session created in profa exists in central hub")

            # Disable single-folder mode (restore isolated)
            ok_dis, msg_dis = set_single_folder_mode(homes_dict, home_hub, enable=False)
            assert_true(ok_dis, "Disable single-folder mode succeeded")
            assert_true(not (pa_cli / "conversations").is_symlink(), "profa conversations is no longer symlink")
            assert_true((pa_cli / "conversations").is_dir(), "profa conversations is real directory")
        finally:
            if orig_home:
                os.environ["HOME"] = orig_home

    print("\n==================================================================")
    print(f"Results: {passed} / {total} tests passed successfully!")
    print("==================================================================")


if __name__ == "__main__":
    run_tests()

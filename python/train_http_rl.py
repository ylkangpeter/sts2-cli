#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP 并发训练脚本（带异常数据清洗）

功能：
1) 清洗 logs/*.jsonl 中的异常数据（0 字节、坏 JSON、无有效样本）
2) 自动拉起 http_game_service.py（若未运行）
3) 使用 worker_slot 进行多线程并发训练
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
QUARANTINE_DIR = LOG_DIR / "quarantine"
SERVICE_LOG_DIR = LOG_DIR / "launcher"
SERVICE_OUT_LOG = SERVICE_LOG_DIR / "service.out.log"
SERVICE_ERR_LOG = SERVICE_LOG_DIR / "service.err.log"

BASE_URL = "http://127.0.0.1:5000"


def http_json(method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"message": raw}
        return {
            "status": "error",
            "http_status": exc.code,
            "message": data.get("message", f"HTTP {exc.code}"),
            "raw": data,
        }
    except (URLError, TimeoutError) as exc:
        return {"status": "error", "message": str(exc)}


def service_alive() -> bool:
    data = http_json("GET", "/health", timeout=4)
    return data.get("status") == "healthy"


def start_service_if_needed() -> Optional[subprocess.Popen]:
    if service_alive():
        return None

    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_fh = open(SERVICE_OUT_LOG, "a", encoding="utf-8")
    err_fh = open(SERVICE_ERR_LOG, "a", encoding="utf-8")

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "python" / "http_game_service.py")],
        cwd=str(ROOT),
        stdout=out_fh,
        stderr=err_fh,
        text=True,
        encoding="utf-8",
        **kwargs,
    )

    for _ in range(40):
        if service_alive():
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    raise RuntimeError("HTTP 服务启动失败，请检查 logs/launcher/service.err.log")


def reset_runtime() -> None:
    http_json("POST", "/admin/cleanup", {"terminate_workers": True}, timeout=20)


def cleanup_bad_logs() -> Dict[str, int]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

    scanned = 0
    quarantined = 0
    repaired = 0

    for file_path in LOG_DIR.glob("*.jsonl"):
        scanned += 1

        if file_path.stat().st_size == 0:
            target = QUARANTINE_DIR / file_path.name
            shutil.move(str(file_path), str(target))
            quarantined += 1
            continue

        good_lines = []
        bad_lines = 0
        has_sample = False

        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue

                if isinstance(obj, dict) and obj.get("type") in ("state", "action"):
                    has_sample = True
                good_lines.append(json.dumps(obj, ensure_ascii=False))

        if not has_sample or len(good_lines) == 0:
            target = QUARANTINE_DIR / file_path.name
            shutil.move(str(file_path), str(target))
            quarantined += 1
            continue

        if bad_lines > 0:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(good_lines) + "\n")
            repaired += 1

    return {"scanned": scanned, "quarantined": quarantined, "repaired": repaired}


def _choose_combat_action(state: Dict[str, Any]) -> Dict[str, Any]:
    hand = state.get("hand", []) or []
    enemies = state.get("enemies", []) or []
    energy = state.get("energy", 0)

    playable = [
        c for c in hand
        if c.get("can_play") and c.get("cost", 99) <= energy
    ]
    if not playable:
        return {"cmd": "action", "action": "end_turn"}

    def score(card: Dict[str, Any]) -> int:
        ctype = card.get("type", "")
        cost = int(card.get("cost", 0))
        if ctype == "Power":
            return 0 + cost
        if card.get("target_type") == "AnyEnemy":
            return 10 + cost
        return 20 + cost

    playable.sort(key=score)
    card = playable[0]

    args: Dict[str, Any] = {"card_index": card["index"]}
    if card.get("target_type") == "AnyEnemy" and enemies:
        target = min(enemies, key=lambda e: e.get("hp", 9999))
        args["target_index"] = target.get("index", 0)

    return {"cmd": "action", "action": "play_card", "args": args}


def _pick_map_action(state: Dict[str, Any]) -> Dict[str, Any]:
    choices = state.get("choices", []) or []
    valid_choices = [c for c in choices if c.get("col") is not None and c.get("row") is not None]
    if valid_choices:
        choice = random.choice(valid_choices)
        return {
            "cmd": "action",
            "action": "select_map_node",
            "args": {"col": int(choice["col"]), "row": int(choice["row"])},
        }

    full_map = state.get("full_map") or {}
    current = full_map.get("current_coord") or {}
    rows = full_map.get("rows") or []
    cur_col = current.get("col")
    cur_row = current.get("row")
    if cur_col is None or cur_row is None or not rows:
        return {"cmd": "action", "action": "proceed"}

    children = []
    if cur_row == 0 and len(rows) > 0:
        children = rows[0]
    else:
        parent_row_idx = cur_row - 1
        if 0 <= parent_row_idx < len(rows):
            for node in rows[parent_row_idx]:
                if node.get("col") == cur_col and node.get("row") == cur_row:
                    children = node.get("children") or []
                    break

    valid_children = [c for c in children if c.get("col") is not None and c.get("row") is not None]
    if not valid_children:
        return {"cmd": "action", "action": "proceed"}

    choice = random.choice(valid_children)
    return {
        "cmd": "action",
        "action": "select_map_node",
        "args": {"col": int(choice["col"]), "row": int(choice["row"])},
    }


def choose_action(state: Dict[str, Any]) -> Dict[str, Any]:
    decision = state.get("decision")

    if decision == "combat_play":
        return _choose_combat_action(state)

    if decision in ("map_select", "map_node"):
        return _pick_map_action(state)

    if decision == "card_reward":
        cards = state.get("cards", []) or []
        deck_size = (state.get("player") or {}).get("deck_size", 0)
        if cards and deck_size < 22:
            return {"cmd": "action", "action": "select_card_reward", "args": {"card_index": cards[0]["index"]}}
        return {"cmd": "action", "action": "skip_card_reward"}

    if decision == "rest_site":
        options = state.get("options", []) or []
        enabled = [o for o in options if o.get("is_enabled", True)]
        if not enabled:
            return {"cmd": "action", "action": "leave_room"}

        player = state.get("player") or {}
        hp = player.get("hp", 1)
        max_hp = max(1, player.get("max_hp", 1))
        hp_ratio = hp / max_hp

        heal = next((o for o in enabled if o.get("option_id") == "HEAL"), None)
        smith = next((o for o in enabled if o.get("option_id") == "SMITH"), None)
        pick = heal if hp_ratio < 0.65 else (smith or heal or enabled[0])
        return {"cmd": "action", "action": "choose_option", "args": {"option_index": pick.get("index", 0)}}

    if decision in ("event_choice", "event"):
        options = state.get("options", []) or []
        choice = next((o for o in options if not o.get("is_locked")), None)
        if choice:
            return {"cmd": "action", "action": "choose_option", "args": {"option_index": choice.get("index", 0)}}
        return {"cmd": "action", "action": "leave_room"}

    if decision == "bundle_select":
        bundles = state.get("bundles", []) or []
        if bundles:
            return {"cmd": "action", "action": "select_bundle", "args": {"bundle_index": bundles[0].get("index", 0)}}
        return {"cmd": "action", "action": "proceed"}

    if decision == "card_select":
        cards = state.get("cards", []) or []
        min_select = int(state.get("min_select", 1))
        if min_select == 0 and not cards:
            return {"cmd": "action", "action": "skip_select"}
        if cards:
            picks = ",".join(str(c.get("index", idx)) for idx, c in enumerate(cards[:max(min_select, 1)]))
            return {"cmd": "action", "action": "select_cards", "args": {"indices": picks}}
        return {"cmd": "action", "action": "skip_select"}

    if decision == "shop":
        return {"cmd": "action", "action": "leave_room"}

    return {"cmd": "action", "action": "proceed"}


def train_episode(worker_slot: int, episode_index: int, character: str, max_steps: int) -> Dict[str, Any]:
    seed = f"rl_{int(time.time() * 1000)}_{worker_slot:02d}_{episode_index:04d}_{random.randint(1000, 9999)}"
    start_resp: Dict[str, Any] = {}
    for _ in range(3):
        start_resp = http_json(
            "POST",
            "/start",
            {
                "character": character,
                "seed": seed,
                "worker_slot": worker_slot,
                "lang": "zh",
            },
            timeout=30,
        )
        if start_resp.get("status") == "success":
            break
        msg = str(start_resp.get("message", ""))
        if "busy" in msg.lower() or "already set" in msg.lower():
            time.sleep(1.0)
            continue
        break

    if start_resp.get("status") != "success":
        return {"ok": False, "worker": worker_slot, "episode": episode_index, "reason": start_resp.get("message", "start failed")}

    game_id = start_resp.get("game_id")
    state = start_resp.get("state") or {}

    steps = 0
    reward_sum = 0.0
    victory = False
    reason = "max_steps"

    try:
        for _ in range(max_steps):
            steps += 1
            if state.get("game_over") or state.get("decision") == "game_over":
                victory = bool(state.get("victory"))
                reason = "game_over"
                break

            action = choose_action(state)
            step_resp = http_json("POST", f"/step/{game_id}", action, timeout=30)
            if step_resp.get("status") != "success":
                reason = f"step_error:{step_resp.get('message', 'unknown')}"
                break

            reward_sum += float(step_resp.get("reward", 0.0) or 0.0)
            state = step_resp.get("state") or {}

            if state.get("game_over"):
                victory = bool(state.get("victory"))
                reason = "game_over"
                break
    finally:
        if game_id:
            http_json("POST", f"/close/{game_id}", timeout=10)

    return {
        "ok": True,
        "worker": worker_slot,
        "episode": episode_index,
        "steps": steps,
        "reward": round(reward_sum, 3),
        "victory": victory,
        "reason": reason,
    }


def run_training(threads: int, episodes_per_thread: int, character: str, max_steps: int) -> Dict[str, Any]:
    total = threads * episodes_per_thread
    done = 0
    wins = 0
    failures = 0
    lock = threading.Lock()
    results = []
    jobs = [(idx, idx) for idx in range(total)]

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [
            pool.submit(train_episode, worker_slot, episode_index, character, max_steps)
            for worker_slot, episode_index in jobs
        ]

        for future in as_completed(futures):
            result = future.result()
            with lock:
                done += 1
                if result.get("ok") and result.get("victory"):
                    wins += 1
                if not result.get("ok"):
                    failures += 1
                results.append(result)
                print(
                    f"[{done}/{total}] worker={result.get('worker')} ep={result.get('episode')} "
                    f"ok={result.get('ok')} win={result.get('victory', False)} "
                    f"steps={result.get('steps', 0)} reason={result.get('reason')}"
                )

    return {
        "total": total,
        "wins": wins,
        "failures": failures,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTP RL 并发训练脚本")
    parser.add_argument("--threads", type=int, default=4, help="训练线程数（默认 4）")
    parser.add_argument("--episodes-per-thread", type=int, default=2, help="每线程 episode 数")
    parser.add_argument("--character", type=str, default="Ironclad", help="角色")
    parser.add_argument("--max-steps", type=int, default=600, help="单局最大步数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print(f"[{datetime.now().isoformat(timespec='seconds')}] 清洗历史日志...")
    clean_stats = cleanup_bad_logs()
    print(f"日志扫描={clean_stats['scanned']} 隔离={clean_stats['quarantined']} 修复={clean_stats['repaired']}")

    print(f"[{datetime.now().isoformat(timespec='seconds')}] 检查/启动 HTTP 服务...")
    start_service_if_needed()
    reset_runtime()

    print(
        f"[{datetime.now().isoformat(timespec='seconds')}] 开始训练: "
        f"threads={args.threads}, episodes_per_thread={args.episodes_per_thread}, "
        f"character={args.character}, max_steps={args.max_steps}"
    )

    summary = run_training(
        threads=args.threads,
        episodes_per_thread=args.episodes_per_thread,
        character=args.character,
        max_steps=args.max_steps,
    )

    print("=" * 70)
    print(
        f"训练完成: total={summary['total']} wins={summary['wins']} "
        f"failures={summary['failures']}"
    )


if __name__ == "__main__":
    main()

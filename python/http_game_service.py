# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
HTTP 游戏服务 - 通过网络与游戏交互

把 `play.py` 的本地交互协议包装成 REST API，便于外部 agent 或服务调用。
"""

import atexit
import json
import logging
import os
import platform
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

from flask import Flask, jsonify, request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.join(ROOT, "src", "Sts2Headless", "Sts2Headless.csproj")
LIB_DIR = os.path.join(ROOT, "lib")
BUILD_CONFIGURATION = os.environ.get("STS2_HEADLESS_CONFIGURATION", "Release")
BUILD_OUTPUT_DIR = os.path.join(ROOT, "src", "Sts2Headless", "bin", BUILD_CONFIGURATION, "net9.0")
HEADLESS_DLL = os.path.join(BUILD_OUTPUT_DIR, "Sts2Headless.dll")
HEADLESS_EXE = os.path.join(BUILD_OUTPUT_DIR, "Sts2Headless.exe")

DEFAULT_ASCENSION = 0
DEFAULT_LANG = "zh"
READ_TIMEOUT_SECONDS = 60
SETTLE_WINDOW_SECONDS = float(os.environ.get("STS2_SETTLE_WINDOW_SECONDS", "0.001"))
DEFAULT_PROFILE_ID = 0

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

games = {}
workers = {}
game_lock = threading.Lock()


def _windows_subprocess_kwargs():
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs

DECISION_ALIAS = {
    "map_select": "map_node",
    "event_choice": "event",
}

ACTION_ALIAS = {
    "choose_card": "select_card_reward",
    "skip_reward": "skip_card_reward",
    "leave_shop": "leave_room",
    "purge_card": "remove_card",
    "choose_event_option": "choose_option",
}

STATE_CHANGE_REQUIRED_ACTIONS = {
    "play_card",
    "end_turn",
    "proceed",
    "choose_option",
    "choose_card",
    "skip_reward",
    "leave_room",
    "select_map_node",
    "buy_card",
    "buy_relic",
    "buy_potion",
    "select_bundle",
    "select_cards",
    "skip_select",
    "remove_card",
}


def _find_dotnet():
    """Find a usable .NET SDK binary."""
    candidates = [
        "dotnet",
        r"C:\Program Files\dotnet\dotnet.exe",
        r"C:\Program Files (x86)\dotnet\dotnet.exe",
        os.path.expanduser("~/.dotnet/dotnet"),
        os.path.expanduser("~/.dotnet-arm64/dotnet"),
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                **_windows_subprocess_kwargs(),
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _find_game_dir(custom_dir=None):
    env_game_dir = os.environ.get("STS2_GAME_DIR")
    if not custom_dir and env_game_dir:
        custom_dir = env_game_dir

    if custom_dir:
        candidates = [
            os.path.join(custom_dir, "data_sts2_windows_x86_64"),
            os.path.join(custom_dir, "data_sts2_macos_arm64"),
            os.path.join(custom_dir, "data_sts2_linux_x86_64"),
            custom_dir,
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return None

    system = platform.system()
    candidates = []
    if system == "Darwin":
        base = os.path.expanduser(
            "~/Library/Application Support/Steam/steamapps/common/Slay the Spire 2/SlayTheSpire2.app/Contents/Resources"
        )
        candidates.extend(
            [
                os.path.join(base, "data_sts2_macos_arm64"),
                os.path.join(base, "data_sts2_macos_x86_64"),
            ]
        )
    elif system == "Linux":
        for steam in ["~/.steam/steam", "~/.local/share/Steam"]:
            candidates.append(os.path.expanduser(f"{steam}/steamapps/common/Slay the Spire 2"))
    elif system == "Windows":
        candidates.append(r"C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2")

    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _copy_required_dlls(game_dir):
    os.makedirs(LIB_DIR, exist_ok=True)
    dlls = [
        "sts2.dll",
        "SmartFormat.dll",
        "SmartFormat.ZString.dll",
        "Sentry.dll",
        "Steamworks.NET.dll",
        "MonoMod.Backports.dll",
        "MonoMod.ILHelpers.dll",
        "0Harmony.dll",
        "System.IO.Hashing.dll",
    ]
    for dll in dlls:
        src = os.path.join(game_dir, dll)
        dst = os.path.join(LIB_DIR, dll)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            continue
        copied = False
        for root_dir, _, files in os.walk(game_dir):
            if dll in files:
                shutil.copy2(os.path.join(root_dir, dll), dst)
                copied = True
                break
        if not copied and dll == "sts2.dll":
            raise RuntimeError(f"未在游戏目录找到关键文件: {dll}")

    sts2 = os.path.join(LIB_DIR, "sts2.dll")
    backup = os.path.join(LIB_DIR, "sts2.dll.original")
    if os.path.isfile(sts2) and not os.path.isfile(backup):
        shutil.copy2(sts2, backup)


def _headless_entry_command(dotnet=None):
    if os.path.isfile(HEADLESS_EXE):
        return [HEADLESS_EXE]
    if os.path.isfile(HEADLESS_DLL):
        if not dotnet:
            dotnet = _find_dotnet()
        if dotnet:
            return [dotnet, HEADLESS_DLL]
    return None


def _ensure_setup(dotnet=None, game_dir=None):
    sts2_dll = os.path.join(LIB_DIR, "sts2.dll")
    resolved_game_dir = _find_game_dir(game_dir)
    if not os.path.isfile(sts2_dll):
        if not resolved_game_dir:
            raise RuntimeError(
                "未找到 lib/sts2.dll，且无法自动定位游戏目录。请在 /start 传 game_dir，或先运行 play.py 完成初始化。"
            )
        _copy_required_dlls(resolved_game_dir)

    output_path = HEADLESS_EXE if os.path.isfile(HEADLESS_EXE) else HEADLESS_DLL
    should_build = (not os.path.isfile(output_path)) or (
        os.path.isfile(sts2_dll) and os.path.getmtime(sts2_dll) > os.path.getmtime(output_path)
    )
    if should_build:
        if not dotnet:
            dotnet = _find_dotnet()
        if not dotnet:
            raise RuntimeError("未找到 .NET SDK，请安装 .NET 9+")
        result = subprocess.run(
            [dotnet, "build", PROJECT, "-c", BUILD_CONFIGURATION],
            capture_output=True,
            text=True,
            timeout=300,
            **_windows_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"dotnet build 失败: {result.stderr or result.stdout}")

    return resolved_game_dir


def _default_profile_source():
    for profile_id in (2, 1, 0):
        candidate = os.path.join(ROOT, "saves", f"profile{profile_id}")
        if os.path.isdir(candidate):
            return profile_id, candidate
    return DEFAULT_PROFILE_ID, None


def _discover_profile_save_targets(profile_id):
    profile_name = f"profile{int(profile_id)}"
    roaming = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    targets = []
    patterns = [
        os.path.join(roaming, "SlayTheSpire2", "steam", "*", profile_name, "saves"),
        os.path.join(roaming, "SlayTheSpire2", "steam", "*", "modded", profile_name, "saves"),
        os.path.join(roaming, "SlayTheSpire2", profile_name, "saves"),
        os.path.join(roaming, "GSE Saves", "0", "remote", profile_name, "saves"),
        os.path.join(roaming, "GSE Saves", "0", "remote", "modded", profile_name, "saves"),
    ]
    import glob

    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.isdir(path):
                targets.append(os.path.normpath(path))
    deduped = []
    seen = set()
    for path in targets:
        if path in seen:
            continue
        deduped.append(path)
        seen.add(path)
    return deduped


def _sync_profile_saves(profile_id, source_dir):
    if not source_dir:
        return []
    if not os.path.isdir(source_dir):
        raise RuntimeError(f"Profile save directory not found: {source_dir}")

    copied = []
    filenames = ("prefs.save", "progress.save", "prefs.save.backup", "progress.save.backup")
    targets = _discover_profile_save_targets(profile_id)
    if not targets:
        raise RuntimeError(f"Unable to locate target save directory for profile{profile_id}")
    for target_dir in targets:
        os.makedirs(target_dir, exist_ok=True)
        for filename in filenames:
            src = os.path.join(source_dir, filename)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(target_dir, filename)
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


class GameProtocolError(RuntimeError):
    """The game process returned an invalid or unusable response."""


class GameInstance:
    """A reusable headless game worker that can host multiple runs sequentially."""

    def __init__(self, worker_key, game_dir=None, profile_id=None, profile_dir=None):
        self.worker_key = str(worker_key or "default")
        self.game_dir = game_dir
        inferred_profile_id, inferred_profile_dir = _default_profile_source()
        self.profile_id = int(profile_id if profile_id is not None else inferred_profile_id)
        self.profile_dir = profile_dir or inferred_profile_dir
        self.proc = None
        self.state = None
        self.state_version = 0
        self.prev_hp = None
        self.prev_gold = None
        self.created_at = datetime.now()
        self.last_error = None
        self.last_action = None
        self._state_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._response_queue = queue.Queue()
        self._response_seq = 0
        self._response_seq_lock = threading.Lock()
        self._stderr_lines = deque(maxlen=200)
        self._stdout_thread = None
        self._stderr_thread = None
        self._command_queue = queue.Queue()
        self._command_thread = None
        self._closed = threading.Event()
        self._startup_drained = False
        self._reserved = False
        self.game_id = None
        self.character = None
        self.seed = None
        self.ascension = DEFAULT_ASCENSION
        self.lang = DEFAULT_LANG
        self.session_started_at = None
        self._fresh_process_required = False

        self._start_process()

    def _cleanup_before_new_session(self):
        if not self.proc or not self.is_alive():
            return
        try:
            self._execute_command(
                {"cmd": "cleanup"},
                "清理残留会话失败",
                timeout=10,
                predicate=self._is_ok_payload,
            )
        except Exception as exc:
            self.last_error = str(exc)
            self.terminate()
            self._start_process()

    def _sync_profile_saves_for_worker(self):
        if not self.profile_dir:
            return []
        copied = _sync_profile_saves(self.profile_id, self.profile_dir)
        if copied:
            print(
                f"[HTTP-GAME] synced profile{self.profile_id} saves to {len(copied)} files",
                file=sys.stderr,
            )
        return copied

    def _build_process_env(self):
        dotnet = _find_dotnet()
        resolved_game_dir = _ensure_setup(dotnet, self.game_dir)
        if resolved_game_dir:
            self.game_dir = resolved_game_dir

        env = os.environ.copy()
        if resolved_game_dir:
            env["STS2_GAME_DIR"] = resolved_game_dir
        env["STS2_PROFILE_ID"] = str(self.profile_id)
        if self.profile_dir:
            copied = self._sync_profile_saves_for_worker()
            env["STS2_PROFILE_SAVE_DIR"] = self.profile_dir
        else:
            copied = []
        launch_cmd = _headless_entry_command(dotnet)
        if not launch_cmd:
            raise RuntimeError("未找到可执行的 Sts2Headless 产物")
        return launch_cmd, env, copied

    def _start_process(self):
        launch_cmd, env, _copied = self._build_process_env()
        self._closed.clear()
        self._response_queue = queue.Queue()
        self._response_seq = 0
        self._stderr_lines = deque(maxlen=200)
        self._command_queue = queue.Queue()
        self.proc = subprocess.Popen(
            launch_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            **_windows_subprocess_kwargs(),
        )
        self._start_io_threads()
        self._start_command_thread()
        self._fresh_process_required = False
        self._startup_drained = False

    def _stdout_worker(self):
        proc = self.proc
        if not proc or not proc.stdout:
            return
        while proc.poll() is None:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._response_seq_lock:
                self._response_seq += 1
                seq = self._response_seq
            self._response_queue.put((seq, payload))

    def _stderr_worker(self):
        proc = self.proc
        if not proc or not proc.stderr:
            return
        while proc.poll() is None:
            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            text = line.strip()
            self._stderr_lines.append(text)
            if "[SIM] perf " in text or "[SIM] EndTurn" in text or "[SIM] Combat" in text:
                print(text, file=sys.stderr, flush=True)

    def _start_io_threads(self):
        self._stdout_thread = threading.Thread(target=self._stdout_worker, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_worker, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _start_command_thread(self):
        self._command_thread = threading.Thread(target=self._command_worker, daemon=True)
        self._command_thread.start()

    def _next_response_seq(self):
        with self._response_seq_lock:
            return self._response_seq

    @staticmethod
    def _is_decision_payload(payload):
        if not isinstance(payload, dict):
            return False
        if payload.get("type") in ("decision", "error"):
            return True
        if "decision" in payload:
            return True
        return bool(payload.get("game_over"))

    @staticmethod
    def _is_ok_payload(payload):
        return isinstance(payload, dict) and payload.get("type") in ("ok", "error")

    def _build_protocol_error(self, message):
        stderr_lines = list(self._stderr_lines)[-30:]
        stderr = "\n".join(stderr_lines).strip()
        if len(stderr) > 4000:
            stderr = stderr[-4000:]
        if stderr:
            return GameProtocolError(f"{message}; stderr={stderr}")
        return GameProtocolError(message)

    def _init_game(self, character, seed, ascension=0, lang=DEFAULT_LANG):
        # Simulator emits a startup handshake JSON before start_run.
        # Drain one startup payload first, otherwise it may be mistaken as game state.
        if not self._startup_drained:
            startup_seq = self._next_response_seq()
            try:
                self._read_after_seq(startup_seq, timeout=10)
            except Exception:
                pass
            self._startup_drained = True

        init_cmd = {
            "cmd": "start_run",
            "character": character,
            "seed": seed,
            "ascension": ascension,
            "lang": lang,
        }
        state = self._execute_action(
            init_cmd,
            "游戏初始化失败",
            READ_TIMEOUT_SECONDS,
            predicate=self._is_decision_payload,
        )
        self._update_tracking_state(state)
        self.game_id = str(self.game_id or "")
        self.character = character
        self.seed = seed
        self.ascension = ascension
        self.lang = lang
        self.session_started_at = datetime.now()
        return state

    def _validate_state(self, state, prefix):
        if not state:
            raise self._build_protocol_error(prefix)
        if state.get("type") == "error":
            message = state.get("message", "unknown error")
            raise self._build_protocol_error(f"{prefix}: {message}")

    def _state_signature(self, state):
        if not isinstance(state, dict):
            return None

        context = dict(state.get("context") or {})
        cards = tuple(
            (
                card.get("index"),
                card.get("id") or card.get("name"),
                card.get("cost"),
                card.get("is_stocked"),
                card.get("on_sale"),
            )
            for card in (state.get("cards") or [])
            if isinstance(card, dict)
        )
        relics = tuple(
            (
                relic.get("index"),
                relic.get("name") or relic.get("id"),
                relic.get("cost"),
                relic.get("is_stocked"),
            )
            for relic in (state.get("relics") or [])
            if isinstance(relic, dict)
        )
        potions = tuple(
            (
                potion.get("index"),
                potion.get("name") or potion.get("id"),
                potion.get("cost"),
                potion.get("is_stocked"),
            )
            for potion in (state.get("potions") or [])
            if isinstance(potion, dict)
        )
        bundles = tuple(
            (
                bundle.get("index"),
                bundle.get("name") or bundle.get("id"),
            )
            for bundle in (state.get("bundles") or [])
            if isinstance(bundle, dict)
        )
        enemies = tuple(
            (
                enemy.get("index"),
                enemy.get("name"),
                enemy.get("hp"),
                enemy.get("block"),
                enemy.get("intends_attack"),
                tuple(
                    (
                        power.get("name"),
                        power.get("amount"),
                    )
                    for power in (enemy.get("powers") or [])
                    if isinstance(power, dict)
                ),
                tuple(intent.get("type") for intent in (enemy.get("intents") or [])),
            )
            for enemy in (state.get("enemies") or [])
        )
        hand = tuple(
            (
                card.get("index"),
                card.get("id") or card.get("name"),
                card.get("cost"),
                card.get("can_play"),
            )
            for card in (state.get("hand") or [])
        )
        options = tuple(
            (
                option.get("index"),
                option.get("col"),
                option.get("row"),
                option.get("room_type"),
                option.get("is_locked"),
                option.get("is_enabled"),
            )
            for option in ((state.get("options") or []) + (state.get("choices") or []))
        )
        player = dict(state.get("player") or {})
        player_relics = tuple(
            (
                relic.get("name") or relic.get("id"),
                tuple(sorted((relic.get("vars") or {}).items())),
            )
            for relic in (player.get("relics") or [])
            if isinstance(relic, dict)
        )
        player_potions = tuple(
            (
                potion.get("index"),
                potion.get("name") or potion.get("id"),
                tuple(sorted((potion.get("vars") or {}).items())),
            )
            for potion in (player.get("potions") or [])
            if isinstance(potion, dict)
        )
        player_powers = tuple(
            (
                power.get("name"),
                power.get("amount"),
            )
            for power in (state.get("player_powers") or [])
            if isinstance(power, dict)
        )
        return (
            state.get("decision"),
            context.get("act"),
            context.get("floor"),
            context.get("room_type"),
            tuple(sorted(context.get("boss", {}).items())) if isinstance(context.get("boss"), dict) else context.get("boss"),
            state.get("round"),
            state.get("turn"),
            state.get("energy"),
            state.get("max_energy"),
            player.get("hp"),
            player.get("max_hp"),
            player.get("block"),
            player.get("gold"),
            player.get("deck_size"),
            player_relics,
            player_potions,
            player_powers,
            enemies,
            hand,
            options,
            cards,
            relics,
            potions,
            bundles,
            state.get("card_removal_cost"),
            state.get("can_skip"),
            state.get("event_name"),
            state.get("game_over"),
            state.get("victory"),
        )

    def _states_equivalent(self, left, right):
        return self._state_signature(left) == self._state_signature(right)

    def _should_require_state_change(self, action):
        if not isinstance(action, dict):
            return False
        if action.get("cmd") != "action":
            return False
        return action.get("action") in STATE_CHANGE_REQUIRED_ACTIONS

    def _read_after_seq(self, min_seq, timeout=READ_TIMEOUT_SECONDS, predicate=None):
        proc = self.proc
        if not proc:
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not proc or proc.poll() is not None:
                return None

            remaining = max(0.0, deadline - time.monotonic())
            try:
                seq, payload = self._response_queue.get(timeout=min(0.05, remaining))
                if seq <= min_seq:
                    continue
                if predicate and not predicate(payload):
                    continue
                return payload
            except queue.Empty:
                continue

        raise self._build_protocol_error(f"读取游戏响应超时（>{timeout}s）")

    def _drain_latest_payload(self, latest_payload, *, predicate=None, settle_timeout=SETTLE_WINDOW_SECONDS):
        if not self.proc:
            return latest_payload

        deadline = time.monotonic() + settle_timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                _seq, payload = self._response_queue.get(timeout=min(0.005, remaining))
            except queue.Empty:
                break

            if predicate and not predicate(payload):
                continue
            latest_payload = payload

        return latest_payload

    def _execute_action(self, action, prefix, timeout=READ_TIMEOUT_SECONDS, predicate=None, previous_state=None):
        min_seq = self._next_response_seq()
        self._send(action)
        deadline = time.monotonic() + timeout
        require_state_change = self._should_require_state_change(action)
        stale_matches = 0

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            response = self._read_after_seq(min_seq=min_seq, timeout=remaining, predicate=predicate)
            self._validate_state(response, prefix)
            if (
                require_state_change
                and previous_state
                and not response.get("game_over")
                and self._states_equivalent(previous_state, response)
            ):
                stale_matches += 1
                if stale_matches <= 3:
                    print(
                        f"[HTTP-GAME] stale step payload ignored game_id={self.game_id} action={action.get('action')} matches={stale_matches}",
                        file=sys.stderr,
                        flush=True,
                    )
                continue
            return self._drain_latest_payload(response, predicate=predicate)

        raise self._build_protocol_error(f"{prefix}: 动作执行后状态未发生变化（>{timeout}s）")

    def _execute_command(self, command, prefix, timeout=READ_TIMEOUT_SECONDS, predicate=None):
        min_seq = self._next_response_seq()
        self._send(command)
        response = self._read_after_seq(min_seq=min_seq, timeout=timeout, predicate=predicate)
        self._validate_state(response, prefix)
        return self._drain_latest_payload(response, predicate=predicate)

    def _clear_session_state(self):
        with self._state_lock:
            self.state = None
            self.state_version = 0
            self.prev_hp = None
            self.prev_gold = None
        self.game_id = None
        self.character = None
        self.seed = None
        self.ascension = DEFAULT_ASCENSION
        self.lang = DEFAULT_LANG
        self.session_started_at = None
        self.last_action = None
        self.last_error = None

    def matches_runtime(self, *, game_dir=None, profile_id=None, profile_dir=None):
        expected_game_dir = os.path.normpath(str(game_dir or self.game_dir or ""))
        current_game_dir = os.path.normpath(str(self.game_dir or ""))
        expected_profile_id = int(profile_id if profile_id is not None else self.profile_id)
        expected_profile_dir = os.path.normpath(str(profile_dir or self.profile_dir or ""))
        current_profile_dir = os.path.normpath(str(self.profile_dir or ""))
        return (
            current_game_dir == expected_game_dir
            and self.profile_id == expected_profile_id
            and current_profile_dir == expected_profile_dir
        )

    def has_active_session(self):
        return bool(self.game_id or self._reserved)

    def ensure_runtime(self, *, game_dir=None, profile_id=None, profile_dir=None):
        requested_game_dir = game_dir or self.game_dir
        requested_profile_id = int(profile_id if profile_id is not None else self.profile_id)
        requested_profile_dir = profile_dir or self.profile_dir
        needs_restart = (
            not self.is_alive()
            or self._fresh_process_required
            or not self.matches_runtime(
                game_dir=requested_game_dir,
                profile_id=requested_profile_id,
                profile_dir=requested_profile_dir,
            )
        )
        if not needs_restart:
            return False
        self.terminate()
        self.game_dir = requested_game_dir
        self.profile_id = requested_profile_id
        self.profile_dir = requested_profile_dir
        self._clear_session_state()
        self._start_process()
        return True

    def start_session(self, game_id, character, seed, game_dir=None, ascension=0, lang="zh", profile_id=None, profile_dir=None):
        requested_game_dir = game_dir or self.game_dir
        requested_profile_id = int(profile_id if profile_id is not None else self.profile_id)
        requested_profile_dir = profile_dir or self.profile_dir

        self.game_dir = requested_game_dir
        self.profile_id = requested_profile_id
        self.profile_dir = requested_profile_dir

        restarted = self.ensure_runtime(
            game_dir=requested_game_dir,
            profile_id=requested_profile_id,
            profile_dir=requested_profile_dir,
        )
        if not restarted:
            try:
                if self.game_id or self.get_state() is not None:
                    self._cleanup_before_new_session()
            except Exception:
                self._fresh_process_required = True
                restarted = self.ensure_runtime(
                    game_dir=requested_game_dir,
                    profile_id=requested_profile_id,
                    profile_dir=requested_profile_dir,
                )
                if not restarted:
                    raise
        with self._io_lock:
            self._clear_session_state()
            try:
                state = self._init_game(character, seed, ascension=ascension, lang=lang)
            except Exception:
                self._fresh_process_required = True
                self.terminate()
                self._clear_session_state()
                self._reserved = False
                raise

            self.game_id = str(game_id)
            self._reserved = False
            self._fresh_process_required = False
            return state

    def close_session(self):
        if not self.proc:
            self._clear_session_state()
            return
        if self.is_alive():
            try:
                with self._io_lock:
                    self._execute_command(
                        {"cmd": "cleanup"},
                        "清理游戏失败",
                        timeout=10,
                        predicate=self._is_ok_payload,
                    )
            except Exception as exc:
                self.last_error = str(exc)
        self._fresh_process_required = False
        self._clear_session_state()
        self._reserved = False

    def _send(self, cmd):
        proc = self.proc
        if not proc or proc.poll() is not None or not proc.stdin:
            raise self._build_protocol_error("游戏进程未运行")

        try:
            proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as exc:
            raise self._build_protocol_error(f"发送命令失败: {exc}") from exc

    def _update_tracking_state(self, state):
        with self._state_lock:
            self.state = state
            self.state_version += 1
            player = state.get("player") if state else None
            if player:
                self.prev_hp = player.get("hp", 0)
                self.prev_gold = player.get("gold", 0)

    def get_state(self):
        with self._state_lock:
            return self.state

    def get_map(self):
        with self._io_lock:
            return self._execute_action(
                {"cmd": "get_map"},
                "获取地图失败",
                timeout=READ_TIMEOUT_SECONDS,
                predicate=lambda payload: isinstance(payload, dict) and payload.get("type") == "map",
            )

    def get_state_version(self):
        with self._state_lock:
            return self.state_version

    def _command_worker(self):
        while not self._closed.is_set():
            try:
                job = self._command_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if job is None:
                break

            action = job["action"]
            timeout = job["timeout"]
            reply_queue = job["reply_queue"]

            try:
                with self._io_lock:
                    self.last_action = action
                    previous_state = self.get_state()
                    new_state = self._execute_action(
                        action,
                        "执行动作失败",
                        timeout=timeout,
                        predicate=self._is_decision_payload,
                        previous_state=previous_state,
                    )
                    reward = self._calculate_reward(new_state)
                    self._update_tracking_state(new_state)
                reply_queue.put({"state": new_state, "reward": reward})
            except Exception as exc:
                self.last_error = str(exc)
                reply_queue.put(exc)

    def step(self, action):
        if self._closed.is_set():
            raise self._build_protocol_error("游戏已关闭")
        if not self.game_id:
            raise self._build_protocol_error("当前没有活动游戏")

        reply_queue = queue.Queue(maxsize=1)
        self._command_queue.put(
            {
                "action": action,
                "timeout": READ_TIMEOUT_SECONDS,
                "reply_queue": reply_queue,
            }
        )

        try:
            result = reply_queue.get(timeout=READ_TIMEOUT_SECONDS + 2)
        except queue.Empty as exc:
            raise self._build_protocol_error("动作处理超时") from exc

        if isinstance(result, Exception):
            raise result

        return result["state"], result["reward"]

    def _calculate_reward(self, new_state):
        if not new_state:
            return -100

        reward = 0.0

        player = new_state.get("player", {})
        current_hp = player.get("hp")
        current_gold = player.get("gold")

        if current_hp is not None and self.prev_hp is not None:
            reward += (current_hp - self.prev_hp) * 0.1

        if current_gold is not None and self.prev_gold is not None:
            reward += (current_gold - self.prev_gold) * 0.01

        for enemy in new_state.get("enemies", []):
            if enemy.get("hp", 1) <= 0:
                reward += 10

        decision = new_state.get("decision")
        if decision == "card_reward":
            reward += 1
        elif decision == "shop":
            reward += 0.5
        elif decision == "game_over":
            reward += 100 if new_state.get("victory") else -50

        return reward

    def close(self):
        self.close_session()

    def terminate(self):
        self._closed.set()
        try:
            self._command_queue.put_nowait(None)
        except Exception:
            pass

        if self._command_thread and self._command_thread.is_alive():
            self._command_thread.join(timeout=1.0)

        proc = self.proc
        if not proc:
            self._clear_session_state()
            self._reserved = False
            return

        try:
            if proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass

            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            if self._stdout_thread and self._stdout_thread.is_alive():
                self._stdout_thread.join(timeout=0.5)
            if self._stderr_thread and self._stderr_thread.is_alive():
                self._stderr_thread.join(timeout=0.5)
            self.proc = None
            self._clear_session_state()
            self._reserved = False

    def is_alive(self):
        proc = self.proc
        return bool(proc and proc.poll() is None)

    def summary(self):
        state = self.get_state() or {}
        start_dt = self.session_started_at or self.created_at
        start_time = start_dt.isoformat()
        uptime_seconds = max(0.0, (datetime.now() - start_dt).total_seconds())
        return {
            "game_id": self.game_id,
            "character": self.character,
            "seed": self.seed,
            "ascension": self.ascension,
            "lang": self.lang,
            "alive": self.is_alive(),
            "decision": DECISION_ALIAS.get(state.get("decision"), state.get("decision")),
            "state_version": self.get_state_version(),
            "start_time": start_time,
            "created_at": start_time,  # backward compatibility
            "uptime_seconds": round(uptime_seconds, 3),
        }


def _json_body():
    return request.get_json(silent=True) or {}


def _error(message, status_code=400, **extra):
    payload = {"status": "error", "message": message}
    payload.update(extra)
    return jsonify(payload), status_code


def _get_game(game_id):
    with game_lock:
        return games.get(game_id)


def _normalize_worker_key(value):
    if value in (None, ""):
        return "default"
    try:
        return f"slot_{int(value):02d}"
    except (TypeError, ValueError):
        return str(value)


def _detach_games_for_worker(worker):
    with game_lock:
        targets = [(game_id, game) for game_id, game in games.items() if game is worker]
        for game_id, _game in targets:
            games.pop(game_id, None)

    closed = []
    failed = []
    for game_id, game in targets:
        try:
            game.close()
            closed.append(game_id)
        except Exception as exc:
            failed.append({"game_id": game_id, "message": str(exc)})
    return {"requested": [game_id for game_id, _game in targets], "closed": closed, "failed": failed}


def _acquire_worker(worker_key, game_dir=None, profile_id=None, profile_dir=None):
    explicit_worker = worker_key not in (None, "")
    normalized_key = _normalize_worker_key(worker_key)
    stale_worker = None
    with game_lock:
        if not explicit_worker:
            dead_keys = [
                key
                for key, candidate in workers.items()
                if candidate is None or not candidate.is_alive()
            ]
            for key in dead_keys:
                stale_worker = workers.pop(key, None) or stale_worker

            for key, candidate in workers.items():
                if candidate is None:
                    continue
                if candidate.has_active_session():
                    continue
                if not candidate.matches_runtime(
                    game_dir=game_dir or candidate.game_dir,
                    profile_id=profile_id if profile_id is not None else candidate.profile_id,
                    profile_dir=profile_dir or candidate.profile_dir,
                ):
                    continue
                candidate._reserved = True
                return candidate

            if "default" not in workers:
                normalized_key = "default"
            else:
                index = 1
                while True:
                    normalized_key = f"auto_{index:02d}"
                    if normalized_key not in workers:
                        break
                    index += 1
            worker = None
        else:
            worker = workers.get(normalized_key)
            if worker is not None and worker.has_active_session():
                raise RuntimeError(f"Worker {normalized_key} is busy")
            if worker is not None and (
                not worker.is_alive()
                or not worker.matches_runtime(
                    game_dir=game_dir or worker.game_dir,
                    profile_id=profile_id if profile_id is not None else worker.profile_id,
                    profile_dir=profile_dir or worker.profile_dir,
                )
            ):
                workers.pop(normalized_key, None)
                stale_worker = worker
                worker = None

        if worker is None:
            worker = GameInstance(
                normalized_key,
                game_dir=game_dir,
                profile_id=profile_id,
                profile_dir=profile_dir,
            )
            workers[normalized_key] = worker
        worker._reserved = True
    if stale_worker is not None:
        try:
            stale_worker.terminate()
        except Exception:
            pass
    return worker


def _cleanup_games(exclude_game_ids=None):
    exclude = {str(item) for item in (exclude_game_ids or []) if item}
    with game_lock:
        targets = [(game_id, game) for game_id, game in games.items() if game_id not in exclude]
        for game_id, _game in targets:
            games.pop(game_id, None)
        remaining = sorted(games.keys())

    closed = []
    failed = []
    for game_id, game in targets:
        try:
            game.close()
            closed.append(game_id)
        except Exception as exc:
            failed.append({"game_id": game_id, "message": str(exc)})

    return {
        "status": "success",
        "requested": [game_id for game_id, _game in targets],
        "closed": closed,
        "failed": failed,
        "remaining": remaining,
    }


def _terminate_workers():
    with game_lock:
        current_workers = list(workers.items())
        workers.clear()

    terminated = []
    failed = []
    for worker_key, worker in current_workers:
        try:
            worker.terminate()
            terminated.append(str(worker_key))
        except Exception as exc:
            failed.append({"worker_key": str(worker_key), "message": str(exc)})
    return {"terminated_workers": terminated, "failed_workers": failed}


def _cleanup_runtime(*, exclude_game_ids=None, terminate_workers=False):
    result = _cleanup_games(exclude_game_ids=exclude_game_ids)
    if terminate_workers:
        result.update(_terminate_workers())
    return result


def _flatten_map_data(map_data):
    if not isinstance(map_data, dict):
        return []
    rows = map_data.get("rows") or []
    flattened = []
    for row in rows:
        for node in row or []:
            children = []
            for child in node.get("children") or []:
                children.append(
                    {
                        "col": child.get("col"),
                        "row": child.get("row"),
                    }
                )
            flattened.append(
                {
                    "col": node.get("col"),
                    "row": node.get("row"),
                    "type": node.get("type"),
                    "room_type": node.get("type"),
                    "symbol": node.get("type"),
                    "visited": bool(node.get("visited")),
                    "current": bool(node.get("current")),
                    "children": children,
                }
            )
    boss = map_data.get("boss") or {}
    if boss:
        flattened.append(
            {
                "col": boss.get("col"),
                "row": boss.get("row"),
                "type": "Boss",
                "room_type": "Boss",
                "symbol": "Boss",
                "visited": False,
                "current": False,
                "children": [],
            }
        )
    return flattened


def _public_state(game, state, *, include_map=None):
    if not state:
        return state
    view = dict(state)
    raw_decision = view.get("decision")
    view["decision"] = DECISION_ALIAS.get(raw_decision, raw_decision)
    view["game_over"] = bool(view.get("game_over") or view.get("decision") == "game_over")
    should_include_map = include_map
    if should_include_map is None:
        should_include_map = view.get("decision") in ("map_node", "map_select")
    if should_include_map:
        try:
            full_map = game.get_map()
        except Exception:
            full_map = None
        if isinstance(full_map, dict) and full_map.get("type") == "map":
            view["full_map"] = full_map
            view["map"] = _flatten_map_data(full_map)
    return view


@app.route("/map/<game_id>", methods=["GET"])
def get_map(game_id):
    game = _get_game(game_id)
    if not game:
        return _error("Game not found", 404)

    if not game.is_alive():
        return _error("Game process is not alive", 400, last_state=game.get_state())

    try:
        full_map = game.get_map()
    except Exception as exc:
        return _error(str(exc), 500, last_state=game.get_state())

    if not isinstance(full_map, dict) or full_map.get("type") != "map":
        return _error("Map is not available", 400, last_state=game.get_state())

    return jsonify(
        {
            "status": "success",
            "game_id": game_id,
            "map": full_map,
            "flat_map": _flatten_map_data(full_map),
            "state_version": game.get_state_version(),
        }
    )


def _normalize_action(action, current_state):
    normalized = dict(action)
    action_name = normalized.get("action")
    if action_name in ACTION_ALIAS:
        normalized["action"] = ACTION_ALIAS[action_name]

    args = normalized.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise GameProtocolError("action.args 必须是 JSON object")
    normalized["args"] = args

    # choose_event_option can be called without args in some clients.
    if normalized.get("action") == "choose_option" and "option_index" not in args:
        choices = (current_state or {}).get("options", [])
        if choices:
            args["option_index"] = choices[0].get("index", 0)

    return normalized


@app.route("/start", methods=["POST"])
def start_game():
    data = _json_body()
    character = data.get("character", "Ironclad")
    seed = data.get("seed", f"http_{random.randint(1000, 9999)}")
    game_dir = data.get("game_dir")
    ascension = int(data.get("ascension", DEFAULT_ASCENSION))
    lang = data.get("lang", DEFAULT_LANG)
    profile_id = data.get("profile_id")
    profile_dir = data.get("profile_dir")
    worker_slot = data.get("worker_slot")
    game_id = f"{seed}_{int(time.time())}"

    try:
        game = _acquire_worker(
            worker_slot,
            game_dir=game_dir,
            profile_id=profile_id,
            profile_dir=profile_dir,
        )
        game.start_session(
            game_id,
            character,
            seed,
            game_dir,
            ascension=ascension,
            lang=lang,
            profile_id=profile_id,
            profile_dir=profile_dir,
        )
    except Exception as exc:
        return _error(str(exc), 500)

    with game_lock:
        games[game_id] = game

    state = _public_state(game, game.get_state())

    return jsonify(
        {
            "status": "success",
            "game_id": game_id,
            "character": character,
            "seed": seed,
            "ascension": ascension,
            "lang": lang,
            "state": state,
            "state_version": game.get_state_version(),
            "decision": state.get("decision") if state else None,
            "game_over": state.get("game_over", False) if state else False,
            "victory": state.get("victory", False) if state else False,
        }
    )


@app.route("/state/<game_id>", methods=["GET"])
def get_state(game_id):
    game = _get_game(game_id)
    if not game:
        return _error("Game not found", 404)

    if not game.is_alive():
        return _error("Game process is not alive", 400, last_state=game.get_state())

    state = _public_state(game, game.get_state())
    if not state:
        return _error("Game state is not available", 400)

    return jsonify(
        {
            "status": "success",
            "game_id": game_id,
            "state": state,
            "state_version": game.get_state_version(),
            "decision": state.get("decision"),
            "game_over": state.get("game_over", False),
            "victory": state.get("victory", False),
        }
    )


@app.route("/step/<game_id>", methods=["POST"])
def step_game(game_id):
    game = _get_game(game_id)
    if not game:
        return _error("Game not found", 404)

    if not game.is_alive():
        return _error("Game process is not alive", 400, last_state=game.get_state())

    action = _json_body()
    if not action:
        return _error("No action provided")

    if "cmd" not in action:
        action["cmd"] = "action"

    try:
        action = _normalize_action(action, game.get_state())
        new_state, reward = game.step(action)
    except GameProtocolError as exc:
        return _error(str(exc), 400, last_state=game.get_state(), last_action=game.last_action)
    except Exception as exc:
        return _error(str(exc), 500, last_state=game.get_state(), last_action=game.last_action)

    new_state = _public_state(game, new_state)

    return jsonify(
        {
            "status": "success",
            "game_id": game_id,
            "state": new_state,
            "state_version": game.get_state_version(),
            "reward": reward,
            "decision": new_state.get("decision"),
            "game_over": new_state.get("game_over", False),
            "victory": new_state.get("victory", False),
        }
    )


@app.route("/command/<game_id>", methods=["POST"])
def command_game(game_id):
    return step_game(game_id)


@app.route("/close/<game_id>", methods=["POST"])
def close_game(game_id):
    with game_lock:
        game = games.pop(game_id, None)

    if not game:
        return _error("Game not found", 404)

    game.close()
    return jsonify({"status": "success", "message": "Game closed", "game_id": game_id})


@app.route("/admin/cleanup", methods=["POST"])
def cleanup_games():
    data = _json_body()
    exclude_game_ids = data.get("exclude_game_ids") or []
    if not isinstance(exclude_game_ids, list):
        return _error("exclude_game_ids must be a list", 400)
    terminate_workers = bool(data.get("terminate_workers", False))
    return jsonify(_cleanup_runtime(exclude_game_ids=exclude_game_ids, terminate_workers=terminate_workers))


@app.route("/games", methods=["GET"])
def list_games():
    with game_lock:
        game_list = [game.summary() for game in games.values()]

    return jsonify({"status": "success", "games": game_list})


@app.route("/admin/worker_debug", methods=["GET"])
def worker_debug():
    with game_lock:
        worker_items = list(workers.items())

    payload = []
    for worker_key, worker in worker_items:
        if worker is None:
            continue
        payload.append(
            {
                "worker_key": str(worker_key),
                "game_id": worker.game_id,
                "alive": worker.is_alive(),
                "last_action": worker.last_action,
                "last_error": worker.last_error,
                "stderr_tail": list(worker._stderr_lines)[-40:],
            }
        )

    return jsonify({"status": "success", "workers": payload})


@app.route("/games/list", methods=["GET", "POST"])
def list_games_command():
    """
    Command-like endpoint for clients that prefer explicit list command calls.
    """
    with game_lock:
        game_list = [game.summary() for game in games.values()]

    return jsonify(
        {
            "status": "success",
            "count": len(game_list),
            "games": game_list,
        }
    )


@app.route("/health", methods=["GET"])
def health():
    with game_lock:
        active_games = sum(1 for game in games.values() if game.is_alive())
        total_workers = len(workers)
        active_worker_ids = {id(game) for game in games.values() if game is not None}
        busy_workers = sum(
            1
            for worker in workers.values()
            if worker.has_active_session() and id(worker) in active_worker_ids
        )

    return jsonify(
        {
            "status": "healthy",
            "active_games": active_games,
            "total_games": len(games),
            "total_workers": total_workers,
            "busy_workers": busy_workers,
            "idle_workers": max(total_workers - busy_workers, 0),
        }
    )


@atexit.register
def _shutdown_on_exit():
    try:
        _cleanup_runtime(exclude_game_ids=[], terminate_workers=True)
    except Exception:
        pass


if __name__ == "__main__":
    print("启动 HTTP 游戏服务...")
    print("访问 http://localhost:5000/health 检查服务状态")
    print("访问 http://localhost:5000/games 查看所有游戏")
    print("使用 POST /start 启动新游戏")
    print("使用 GET /state/<game_id> 获取游戏状态")
    print("使用 POST /step/<game_id> 或 /command/<game_id> 执行动作")
    print("使用 POST /close/<game_id> 关闭游戏")
    try:
        from waitress import serve

        serve(
            app,
            host="0.0.0.0",
            port=5000,
            threads=int(os.environ.get("STS2_HTTP_THREADS", "32")),
            connection_limit=int(os.environ.get("STS2_HTTP_CONNECTION_LIMIT", "256")),
            channel_timeout=int(os.environ.get("STS2_HTTP_CHANNEL_TIMEOUT", "120")),
        )
    except ImportError:
        print("waitress 未安装，回退到 Flask dev server")
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

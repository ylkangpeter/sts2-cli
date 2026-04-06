# sts2-cli

Forked from [wuhao21/sts2-cli](https://github.com/wuhao21/sts2-cli). This fork keeps the original headless CLI core and adds a reusable HTTP service layer for automation, regression testing, and RL training.

## What We Added

- `python/http_game_service.py`
  - REST API around the headless simulator
  - reusable worker model instead of one fresh top-level process per run
  - game listing, cleanup, health check, map query endpoints
- save/profile helpers
  - optional profile save syncing for repeated training sessions
- integration helpers for RL
  - `python/train_http_rl.py`
  - `python/test_http_service.py`
  - API docs in `API_DOCUMENTATION.md` and `HTTP_SERVICE_README.md`
- privacy and repo hygiene improvements
  - generic path examples instead of personal machine paths
  - runtime outputs ignored from Git

## Local Runtime Files

The repo is set up so machine-specific runtime artifacts do not need to be committed.

Ignored local/runtime items include:

- `lib/`
- `logs/`
- `saves/`
- `service.out.log`
- `service.err.log`

`lib/` is where copied game DLLs land after local setup, so your game installation details stay out of Git.

## Requirements

- [Slay the Spire 2](https://store.steampowered.com/app/2868840/Slay_the_Spire_2/) on Steam
- [.NET 9+ SDK](https://dotnet.microsoft.com/download)
- Python 3.9+

Python dependencies:

```bash
pip install -r requirements.txt
```

Initial setup:

```bash
git clone <your-fork-url>
cd sts2-cli
./setup.sh
```

Or just run `python3 python/play.py` once and let it auto-initialize.

## How To Use

Interactive CLI:

```bash
python3 python/play.py
python3 python/play.py --lang en
python3 python/play.py --ascension 10
python3 python/play.py --character Silent
```

HTTP service:

```bash
python3 python/http_game_service.py
```

Health check:

```bash
curl http://localhost:5000/health
```

Start one game:

```bash
curl -X POST http://localhost:5000/start \
  -H "Content-Type: application/json" \
  -d "{\"character\":\"Ironclad\",\"seed\":\"demo\"}"
```

Useful references:

- `HTTP_SERVICE_README.md`
- `API_DOCUMENTATION.md`
- `python/test_http_service.py`

## JSON Protocol

The original stdin/stdout protocol still works:

```bash
dotnet run --project src/Sts2Headless/Sts2Headless.csproj
```

```json
{"cmd": "start_run", "character": "Ironclad", "seed": "test", "ascension": 0}
{"cmd": "action", "action": "play_card", "args": {"card_index": 0, "target_index": 0}}
{"cmd": "action", "action": "end_turn"}
{"cmd": "action", "action": "select_map_node", "args": {"col": 3, "row": 1}}
{"cmd": "action", "action": "skip_card_reward"}
{"cmd": "quit"}
```

Each command returns a JSON decision point such as `map_select`, `combat_play`, `card_reward`, `rest_site`, `event_choice`, `shop`, or `game_over`.

## RL Integration

This fork is intended to be used together with a local `sts2-rl` repository.

- `sts2-cli` provides the game runtime and HTTP API
- `sts2-rl` provides training environments, reward logic, dashboard, and watchdog scripts

Recommended local layout:

```text
workspace/
  sts2-cli/
  sts2-rl/
```

Typical workflow:

1. Initialize `sts2-cli`
2. Start `python3 python/http_game_service.py`
3. In the sibling `sts2-rl` repo, run regression tests or PPO training against `http://localhost:5000`

## Training-Friendly Usage

Set the game directory if auto-detection does not find it:

```powershell
$env:STS2_GAME_DIR="C:\path\to\SlayTheSpire2"
```

Start the service:

```powershell
python .\python\http_game_service.py
```

Regression smoke test:

```powershell
python .\python\test_http_service.py
```

Simple concurrent rollout script:

```powershell
python .\python\train_http_rl.py --threads 4 --episodes-per-thread 2
```

## Supported Characters

| Character | Status |
|---|---|
| Ironclad | Fully playable |
| Silent | Fully playable |
| Defect | Fully playable |
| Necrobinder | Fully playable |
| Regent | Fully playable |

## Architecture

```text
Your code / RL trainer / agent
    | HTTP or JSON stdin/stdout
    v
src/Sts2Headless (C#)
    | RunSimulator.cs
    v
sts2.dll (game engine, IL patched)
  + src/GodotStubs
  + Harmony patches
```

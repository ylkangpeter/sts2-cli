# STS2-CLI Agent: Play a Character

Play one complete game of Slay the Spire 2 as a single character. The main agent (you) plays directly via HTTP bridge — one `curl` command per decision.

**Character**: $ARGUMENTS (default: Ironclad). Options: Ironclad, Silent, Defect, Regent, Necrobinder.

## Architecture

```
Game Process ←→ sts2_bridge.py (local HTTP, random port) ←→ Main Agent (curl + reasoning)
```

YOU are the player. Each game decision = one curl command → read JSON → reason → next curl.

---

## Before Playing

1. **Build** (if not already): `~/.dotnet-arm64/dotnet build Sts2Headless/Sts2Headless.csproj`
2. **Read learning files** (silently absorb, don't repeat):
   - `agent/learning_general_en.md` (or `_cn.md` if user writes in Chinese)
   - `agent/learning_<character>_en.md` (or `_cn.md`)
3. **Start bridge** (use a random port to avoid conflicts with other sessions):
   ```bash
   STS2_PORT=$(python3 -c "import random; print(random.randint(19000,19999))")
   STS2_LOG="/tmp/sts2_game_${STS2_PORT}.jsonl"
   cd /Users/haowu/Workspace/sts2-cli && python3 agent/sts2_bridge.py $STS2_PORT --compact --log $STS2_LOG &
   ```
   Wait 6 seconds for startup. Use `$STS2_PORT` in all subsequent curl commands.

   To replay a logged game to a specific step (for bug reproduction):
   ```bash
   python3 agent/sts2_bridge.py replay $STS2_LOG --until 42 --port $STS2_PORT
   ```

## Playing the Game

### Start run
```bash
curl -s localhost:$STS2_PORT -d '{"cmd":"start_run","character":"<CHARACTER>","seed":"'$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])")'"}'
```

### Decision loop

Send ONE curl command per decision. Read JSON response. Reason briefly. Send next curl.

**BE CONCISE** — write AT MOST 1-2 lines per action to conserve context:
```
F5 R1: HP 65/80, 3e. Nibbit 45hp(atk11). Inc=11. → Bash(i1,t0)
F5 R1: HP 65/80, 2e. Nibbit 39hp(vuln). → Strike(i0,t0)
F5 R1: HP 65/80, 1e. Nibbit 33hp. → Defend(i3). end_turn.
```
Do NOT dump full JSON. Do NOT explain known card effects. Just: state → decision → action.

### Action format
```bash
curl -s localhost:$STS2_PORT -d '{"cmd":"action","action":"<CMD>","args":{...}}'
curl -s localhost:$STS2_PORT -d '{"cmd":"get_map"}'
```

### All commands

| Command | Args | Notes |
|---------|------|-------|
| `start_run` | `character`, `seed` | Characters: Ironclad, Silent, Defect, Regent, Necrobinder |
| `select_map_node` | `col`, `row` | Choose map node |
| `play_card` | `card_index`, `target_index`? | `target_index` required when `target_type == "AnyEnemy"` |
| `end_turn` | — | End combat turn |
| `select_card_reward` | `card_index` | Pick card reward |
| `skip_card_reward` | — | Skip card reward |
| `choose_option` | `option_index` | Choose event/rest option |
| `leave_room` | — | Leave room (shop, event) |
| `select_cards` | `indices` (string) | e.g. `"0"` or `"0,1,2"` |
| `select_bundle` | `bundle_index` | Choose card pack |
| `use_potion` | `potion_index`, `target_index`? | Self-potions auto-target player |
| `buy_card` / `buy_relic` / `buy_potion` | `card_index` / `relic_index` / `potion_index` | Shop purchases |
| `remove_card` | — | Remove card in shop |
| `get_map` | — | Get full map (info only) |
| `proceed` | — | Force advance / enter next act |
| `quit` | — | End session |

### Decision types & how to handle them

| Decision | Action |
|----------|--------|
| `map_select` | **get_map first!** HP<40%→Rest. HP 40-65%→Monster. HP>65%→Elite OK. Treasure always. REST before boss. |
| `combat_play` | Calc incoming. 0-cost first → character-specific priorities → Block(inc>15) → Damage. ALL potions at boss/elite. |
| `card_reward` | Use character learning file priorities. Skip if deck>15 and card is weak. |
| `rest_site` | HP<75%→HEAL(option 0). Before boss→ALWAYS HEAL. Else SMITH. |
| `event_choice` | Best non-locked option. Avoid HP loss at <50%. |
| `shop` | Remove Strikes/Curses. Buy high-priority cards. |
| `card_select` | Select based on context (upgrade best card, remove worst). |
| `bundle_select` | Pick bundle with best cards. |
| `game_over` | Record result. Stop. |
| `error` | Try `proceed` → `leave_room` → `end_turn`. |

### Combat reasoning (per turn)
```
1. Calculate total incoming from enemy intents (cap 60 per enemy)
2. Lethal check: unblocked >= HP → block only
3. Play order: 0-cost → Powers (if safe) → Block (if needed) → Damage
4. Use potions: boss/elite, HP<40%, or lethal
5. Target: one-shottable threats > highest-damage > lowest HP
6. end_turn when out of energy or no good plays
```

### Character-specific state fields

| Character | Extra fields in `combat_play` |
|-----------|------------------------------|
| Defect | `orbs` (array), `orb_slots` |
| Regent | `stars` (int), cards have `star_cost` |
| Necrobinder | `osty` {hp, max_hp, block, alive} |

### Error handling
- If a card fails to play, skip it and try other cards. Don't retry the same failed card.
- Status/Curse cards (Slimed, Burn, Wound, Infection): NEVER play these.
- On error response: try `proceed` → `leave_room` → `end_turn`.
- Same state 8+ times: try `end_turn` → `proceed` → `leave_room`.
- See `agent/bug.md` for known simulator bugs (Particle Wall, Astral Pulse star cost, etc.).

---

## After Each Game

1. **Analyze**: If loss, identify what killed you and what decisions were wrong.
2. **Fix bugs**: Read `agent/bug.md`. For every `[OPEN]` bug:
   - Use the game log (`/tmp/sts2_game.jsonl`) to find the exact step where the bug occurred
   - Use `replay --until <step-1>` to reproduce the bug state
   - Fix in `Sts2Headless/RunSimulator.cs`, rebuild, replay to verify
   - Update `agent/bug.md`: `[OPEN]` → `[FIXED]` with fix description
   - If a bug can't be reproduced from the current log, note it and move on
3. **Update learning files** — EN and CN are separate:
   - `agent/learning_<character>_en.md` — clean professional English
   - `agent/learning_<character>_cn.md` — 网络锐评风格中文 (snarky, meme-like, opinionated)
   - DEDUPLICATE — don't add what's already there
   - ORGANIZE by topic (Combat, Map, Card Picks, Boss, Enemy)
   - PRUNE — remove per-game logs, keep only distilled actionable insights
   - **HARD LIMIT: 100 LINES per file** — after editing, run `wc -l` to verify. If over 100, aggressively consolidate or remove lowest-value entries until under 100.
   - **Deck thinning is a key strategy** — note Exhaust/removal combos and ideal deck sizes
   - **Bugs go in `agent/bug.md`**, NOT in learning files. Learning = strategy only.
   - **NEVER hallucinate cards from STS1** — this is Slay the Spire 2, NOT STS1. Only reference cards/relics/enemies that you have actually SEEN in game responses. If a card name doesn't appear in the JSON, it doesn't exist. Do NOT invent card names from memory of STS1 (e.g., no Offering, Fiend Fire, Body Slam, Limit Break — these may not exist in STS2).
   - Use official translations from `localization_zhs/` — NEVER invent Chinese names.
3. **Report bugs**: If simulator issues found, update `agent/bug.md`.

## Cleanup
```bash
lsof -ti:$STS2_PORT | xargs kill 2>/dev/null
```

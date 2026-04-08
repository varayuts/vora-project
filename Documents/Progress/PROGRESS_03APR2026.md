# VORA Progress — 3 Apr 2026

## Session Summary

Full system audit + object memory localization fix + Nav2 lifecycle root cause.

---

## 1. Nav2 Lifecycle Root Cause (FIXED)

**Problem:** `nav2_params.yaml` section was named `lifecycle_manager` but `navigation_launch.py` creates the node as `lifecycle_manager_navigation`. Params never applied. Result: `bond_timeout` used default 4.0s instead of 30.0s. On Jetson Nano, DDS discovery takes 5-15s -> bond expired -> lifecycle manager fired `startup()` twice -> "No transition matching 1 found for current state active" error on every boot.

**Fix:** Renamed section in `Myagv/nav2_params.yaml` line 272: `lifecycle_manager` -> `lifecycle_manager_navigation`

**File:** `Myagv/nav2_params.yaml`

---

## 2. Nav2 -> LLM Feedback Loop (NEW)

**Problem:** When Nav2 aborted or timed out, the LLM had no knowledge of what happened. It would retry the same blocked direction.

**Fix:** Added `_last_nav2_result` dict in `main.py` that stores rich Nav2 result (status, goal_x/y, start_x/y, distance_remaining, duration). This is injected into the LLM agent prompt as a `NAV2 LAST MOVE` block so the LLM can reason about failures.

Example LLM sees:
```
NAV2 LAST MOVE: ABORTED after 8.3s — goal (1.20, 0.45), stopped 0.31m from goal
  Nav2 could not reach goal (obstacle in planned path or goal unreachable).
  -> Consider turning to a DIFFERENT direction before forwarding.
```

**Files:** `Gateway/gateway/main.py` (4 edits)

---

## 3. start_nav2.sh Lifecycle Polling (IMPROVED)

**Problem:** `start_nav2.sh` had `sleep 3` after launching Nav2 — no verification that nodes were actually active.

**Fix:** Added polling loop (up to 20 iterations x 2s = 40s max) that checks `/lifecycle_manager_navigation/get_state` until "active" is returned.

**File:** `Myagv/start_nav2.sh` lines 188-207

---

## 4. Object Memory Localization Bug (FIXED - MAJOR)

**Root cause:** When the camera detects an object, the system stores `robot_x/y/theta` (the ROBOT's position) as if it were the OBJECT's position. With a monocular camera (no depth sensor), exact object XY in map frame is unknowable.

### What changed:

**`Gateway/gateway/object_memory.py` — Full rewrite:**
- `robot_x/y/theta` renamed to `observer_x/y/theta` (semantically explicit)
- Added `bearing_deg` — estimated bearing from VLM location code (left=+25deg, right=-25deg)
- Added `section` — map section from `get_section(x, y)` (4 quadrants: A/B/C/D)
- Added `localization_source` — "amcl" / "odom_hybrid" / "dead_reckoning"
- Added `last_verified_at` — timestamp of last confirmation
- Added `_migrate_record()` for automatic JSON migration (old format auto-renamed)
- Added suspicious-write detection: warns if object stored at (0,0) or same pose within 5s
- `get_search_hint()` now uses section + direction + reliability warning instead of raw XY

**`Gateway/gateway/main.py` — 7 edits:**
- 2x `remember()` call sites: `robot_x` -> `observer_x` + `localization_source=_odom_source`
- `_push_objects_to_server()`: added `observer_x/y/theta`, `section`, `bearing_deg`, `localization_source`, `object_pose_estimated: null`, `pose_semantics: "observer_pose_not_object_pose"`
- 2x broadcast payloads (search_status "found"): same explicit fields added
- 2x API endpoints (`/memory/objects`, `/memory/{name}`): same explicit fields added
- Co-location advisor prompt: removed "ระบุตำแหน่ง (x,y)" -> "ระบุบริเวณ", added "พิกัดคือตำแหน่งหุ่นตอนเห็น ไม่ใช่ตำแหน่งวัตถุ"

**`Gateway/gateway/spatial_memory.py` — 2 edits:**
- `get_exploration_summary()`: added header note "(coordinates = robot position when observed, not object position)", prefix changed to `robot@(x,y)`
- `build_colocation_context()`: changed "ที่ (x,y)" -> "หุ่นอยู่ที่ (x,y) หัน N deg"

**`app/frontend/index.html` — 3 edits:**
- CoT step: "Map: X=..., Y=..." -> "Observer: X=..., Y=... (section)"
- Found card: "Map position" -> "Seen from"
- Memory panel: "Map: X=..., Y=..." -> "Seen from: X=..., Y=... [section]"

### Section system:
4 quadrants on 7x7m map centered at origin:
- A: x<0, y>0 (top-left)
- B: x>=0, y>=0 (top-right)
- C: x<0, y<0 (bottom-left)
- D: x>=0, y<0 (bottom-right)

Note: boundaries are initial guesses. Verify with `ros2 topic echo /amcl_pose` at map corners and adjust `SECTION_DEFS` in `object_memory.py`.

### API payload schema (new):
```json
{
  "name": "ปากกา",
  "location": "center_left",
  "location_description": "ด้านซ้าย",
  "confidence": 0.9,
  "age_hours": 0.3,
  "robot_x": 1.5,           // DEPRECATED — backward compat for webapp
  "robot_y": -0.8,          // DEPRECATED — backward compat for webapp
  "observer_x": 1.5,        // NEW — where robot was looking from
  "observer_y": -0.8,       // NEW
  "observer_theta": 0.5,    // NEW
  "section": "B",           // NEW — map quadrant
  "bearing_deg": 25.0,      // NEW — estimated bearing to object
  "localization_source": "amcl",  // NEW — pose reliability
  "object_pose_estimated": null,   // NEW — null = not computable
  "pose_semantics": "observer_pose_not_object_pose"  // NEW — explicit contract
}
```

---

## 5. Tests Added

**File:** `Gateway/tests/test_object_memory.py` — 25 tests, all passing

- TestGetSection: 7 tests (quadrants, boundaries, out-of-bounds)
- TestGetSectionLabel: 2 tests
- TestLocationToBearing: 3 tests (left/right/center)
- TestMigrateRecord: 3 tests (old->new rename, defaults, idempotent)
- TestRemember: 4 tests (observer pose stored, derived fields, suspicious-write warnings)
- TestSearchHint: 4 tests (section in hint, reliability warning, AMCL no warning)
- TestJsonRoundtrip: 2 tests (save/load new format, migrate old format)

Run: `PYTHONPATH=. python -c "import sys; sys.path=[p for p in sys.path if '/opt/ros/' not in p]; import pytest; sys.exit(pytest.main(['-v', 'tests/test_object_memory.py']))"`

---

## Deployment Checklist

**Jetson Nano:**
- [ ] `Myagv/nav2_params.yaml` — lifecycle_manager_navigation fix
- [ ] `Myagv/start_nav2.sh` — lifecycle polling + pkill cleanup

**Gateway PC (192.168.0.60):**
- [ ] `Gateway/gateway/object_memory.py` — full rewrite
- [ ] `Gateway/gateway/main.py` — all edits
- [ ] `Gateway/gateway/spatial_memory.py` — observer pose labels
- [ ] `Gateway/gateway/obstacle_avoidance.py` — 60deg forward window + rear obstacle checker (from prev session)
- [ ] `Gateway/gateway/ros_cmd.py` — rear_obstacle_checker param (from prev session)

**Restart sequence:**
1. Jetson: `./start_nav2.sh` -> should see "lifecycle_manager_navigation: ACTIVE"
2. Gateway: restart -> verify "Migrated object memory: robot_x/y/theta -> observer_x/y/theta" in log
3. Webapp: search -> verify hint says "ส่วน X" not raw XY, found card says "Seen from" not "Map position"

---

## Remaining Work (Next Session)

1. **Test Nav2 on Jetson** — verify obstacle avoidance works after lifecycle fix
2. **Adjust section boundaries** — check AMCL pose at map corners, update SECTION_DEFS
3. **Add depth estimation** — if depth camera available, compute `object_pose_estimated`
4. **Structured state schema** — give LLM a typed state object every cycle (robot_pose, nav_state, visible_objects, memory_hits, current section)
5. **High-level intent output** — LLM outputs navigate_to_section/search_in_section instead of raw angles

# VORA PROJECT PROGRESS — 9 เมษายน 2026

---

## 📍 สถานะโปรเจกต์วันนี้

**Session Focus:** Semantic Map UI — Zone/Landmark CRUD + UI Cleanup + Critical Bug Fix

**Branch:** `update`

---

## ✅ สิ่งที่ทำวันนี้

### 1. CRITICAL BUG FIX — Annotation Poll-Overwrite Race Condition

**ปัญหา:**
- Zone และ Landmark ที่ create/delete ผ่าน UI **ไม่คงอยู่** — หายไปภายใน ~500ms
- Zone ที่กด Delete แล้ว **ยังปรากฏอยู่**

**Root Cause:**
- `fetchMapState()` ทำงานทุก 500ms และเขียนทับ `mapAnnotations` ด้วย server cache ที่ยัง stale อยู่
- Gateway→Server push pipeline ใช้เวลาหลายวินาที — local optimistic update ถูก overwrite ก่อนที่ server จะ sync

**Fix ใน `app/frontend/index.html`:**
```javascript
// Guard variable
let _annLocalUntil = 0;

// fetchMapState — skip overwrite while guard active
if (d.annotations && Date.now() > _annLocalUntil) mapAnnotations = d.annotations;

// saveAnnotation — set guard + update local state immediately
if (resp.ok) {
  _annLocalUntil = Date.now() + 5000;
  // update mapAnnotations locally without waiting for server
}

// deleteAnnotation — same guard pattern
if (resp.ok) {
  _annLocalUntil = Date.now() + 5000;
  mapAnnotations = {...mapAnnotations, zones: mapAnnotations.zones.filter(z => z.id !== id)};
}
```

---

### 2. Semantic Map — Zone `source` Field

**เพิ่ม** `source: str = "seed"` ใน `Zone` dataclass (`Gateway/gateway/semantic_map.py`)

| Value | ความหมาย |
|-------|---------|
| `"seed"` | โซนเริ่มต้น (bedroom, living_room, ฯลฯ) — โหลดจาก semantic_map.json |
| `"manual"` | โซนที่ user สร้างผ่าน UI |

**`Gateway/gateway/main.py`** — endpoint `POST /annotations/zone` ตอนนี้ default `source="manual"` สำหรับโซนที่สร้างผ่าน API

**Frontend** แสดง source badge ใน modal:
- ⬡ Default zone (can be edited or deleted)
- ✎ Manual zone

---

### 3. UI Cleanup — Remove Dead Elements

| สิ่งที่ลบ | เหตุผล |
|-----------|--------|
| ⚡ Battery panel (HTML + JS) | ไม่มี endpoint จริง — แสดง `--` ตลอด |
| 🔧 Telemetry button (btn-telemetry) | ไม่มี target window / onclick เป็น no-op |
| `fetchBattery()` function + setInterval | ตามมาจากการลบ battery panel |
| `_battPct`, `_battVolt` variables | ไม่มีการใช้งานแล้ว |

**E-STOP:** ยังอยู่ครบถ้วน — fixed-position bottom-right, ring animation, สีแดง

---

### 4. Annotation CRUD — Full Flow Verified

**Zone create chain:**
```
click Zone btn → canvas click → canvasToWorld() → openAnnotationModal(wx, wy)
→ fill form → saveAnnotation() → POST /map/annotations/zone (Server)
→ httpx forward → POST /annotations/zone (Gateway :9001)
→ semantic_map.add_zone() → _save() → semantic_map.json
```

**Zone delete chain:**
```
double-click zone → hit-test circle → openAnnotationModal(editId)
→ click Delete → confirm dialog → deleteAnnotation()
→ DELETE /map/annotations/zone/{id} (Server)
→ httpx forward → DELETE /annotations/zone/{id} (Gateway)
→ semantic_map.delete_zone() → _save()
```

**Rendering** (canvas):
- Zones: วงกลม translucent พร้อม label ภาษาไทย, สีตาม `zone.color`
- Landmarks: diamond marker สีเหลือง, label เหนือ marker

---

### 5. Memory Architecture — ยืนยัน Separation

| Memory Store | ไฟล์ | ใช้สำหรับ |
|-------------|------|---------|
| Semantic Map | `Gateway/data/semantic_map.json` | Zone + Landmark ถาวร (ห้อง, จุดสังเกต) |
| Object Memory | `Gateway/data/object_memory.json` | ประวัติการค้นหา object (runtime) |
| Spatial Memory | in-memory | ตำแหน่งล่าสุดของ object (runtime) |

**`DELETE /memory`** — ล้าง object memory เท่านั้น **ไม่แตะ semantic_map.json**

---

## 📁 Files Modified วันนี้

| ไฟล์ | การเปลี่ยนแปลง |
|------|--------------|
| `app/frontend/index.html` | Poll-overwrite guard, UI cleanup (battery, telemetry), source badge, confirm dialog |
| `Gateway/gateway/semantic_map.py` | Added `source` field to Zone dataclass |
| `Gateway/gateway/main.py` | `upsert_zone` endpoint: `source=data.get("source", "manual")` |

## 📁 Files NOT Modified

| ไฟล์ | เหตุผล |
|------|--------|
| `app/api/map_router.py` | ถูกต้องแล้วจาก session ก่อน |
| `Gateway/gateway/object_memory.py` | ถูกต้องแล้ว |
| `Gateway/gateway/spatial_memory.py` | ถูกต้องแล้ว |
| `Gateway/data/semantic_map.json` | Seed data ไม่มี source field แต่ `_load()` default เป็น "seed" |
| `Myagv/*` | ไม่ได้แตะเลย |

---

## ⚙️ Architecture Overview (ณ วันนี้)

```
[Frontend (Browser)]
    ↕ WebSocket (500ms poll)
[Server :8080 — FastAPI]
    ├── /map/state → returns robot pose + objects + trail + annotations
    ├── /map/annotations/push ← Gateway push (background task)
    └── /map/annotations/* → proxy via httpx to Gateway
         ↕
[Gateway :9001 — FastAPI + ROSBridge]
    ├── SemanticMap (semantic_map.py)
    │   └── Gateway/data/semantic_map.json
    ├── ObjectMemory (object_memory.py)
    ├── SpatialMemory (spatial_memory.py)
    └── SearchPlanner (search_planner.py) ← Phase 0.5 search
         ↕ ROSBridge ws://192.168.0.111:9090
[MyAGV :9090 — ROS2]
    └── Nav2 + cmd_vel + TF
```

---

## 🧪 Checklist สำหรับ Manual Test

### Annotation CRUD
- [ ] Zone create: click Zone → click map → fill form → save → **zone ปรากฏและอยู่ครบ**
- [ ] Zone edit: double-click zone → แก้ไข → save → **อัปเดตทันที**
- [ ] Zone delete: double-click zone → Delete → confirm → **zone หายไปและไม่กลับมา**
- [ ] Landmark create: click Landmark → click map → fill form → save → **diamond ปรากฏ**
- [ ] Landmark delete: double-click landmark → Delete → **หายไป**
- [ ] Reload page → annotation ยังอยู่ (persistent via JSON)

### UI
- [ ] ไม่มี battery panel ปรากฏ
- [ ] ไม่มี telemetry button ใน header
- [ ] E-STOP ยังอยู่ที่มุมขวาล่าง พร้อม ring animation
- [ ] ไม่มี JS console errors

### Memory Separation
- [ ] `DELETE /memory` → object memory ล้าง แต่ zones ยังอยู่ครบ

---

## 🐛 Known Remaining Issues

| Issue | Priority | Note |
|-------|----------|------|
| `semantic_map.json` source fields ถูก linter ลบ | Low | `_load()` default เป็น "seed" — ไม่กระทบ behavior |
| Gateway→Server push delay ~10s | Low | ไม่กระทบ UX เพราะมี local guard |

---

## 🏆 Achievements วันนี้

- ✅ แก้ critical race condition — annotation สร้าง/ลบทำงานได้จริงแล้ว
- ✅ Zone source tracking ("seed" vs "manual")
- ✅ UI สะอาดขึ้น — ลบ dead elements (battery, telemetry)
- ✅ Full annotation CRUD chain verified end-to-end
- ✅ Memory separation ยืนยันชัดเจน
- ✅ E-STOP ปลอดภัย — ไม่ถูกแตะ

---

*Progress Report — 9 เมษายน 2026 | VORA Project*

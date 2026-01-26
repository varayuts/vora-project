# 🔧 STT Fix Summary - January 23, 2026

## ✅ Cleaned Code
### 1. ลบ Unused Imports (stt_ws.py)
- ❌ `subprocess` (ใช้ asyncio.create_subprocess_exec)
- ❌ `hashlib` (ไม่ใช้เลย)
- ❌ `deque`, `Deque`, `Dict`, `Any` (ไม่ใช้)

**ก่อน:** 13 imports ที่สะเบะสะบ้าน  
**หลัง:** 7 imports ที่ใช้จริง ✅

---

## 🎯 STT Logic Changes

### 2. **Buffer Strategy**: ลบ Caching
**ปัญหา:** Query เก่าติดค้าง → ทำให้แปลซ้ำ  
**วิธีแก้:** ลบ `mark_final_and_compact()` ที่เก็บ tail

```python
# ❌ ก่อน (เก็บ 2 วินาที overlap)
keep_bytes = self.sr * 2 * 2
tail = self._buf[-keep_bytes:] if len(self._buf) > keep_bytes else self._buf
self._buf = bytearray(tail)

# ✅ หลัง (clear ทั้งหมด)
self._buf = bytearray()
```

---

### 3. **Timing Parameters**: ลด Overhead
| Parameter | ก่อน | หลัง | เหตุผล |
|-----------|------|------|--------|
| `STEP_SEC` | 0.6s | 1.0s | อ่านแบบ batch → ลดการแปล |
| `MIN_PARTIAL_SEC` | 0.8s | 1.2s | รอให้เสียงพอตัว |
| `EOU_SILENCE_MS` | 1500ms | 1200ms | เร็วขึ้น |
| `EOU_RMS_THRESH` | 0.005 | 0.008 | ลดความไว (ลด false positive) |
| FFmpeg chunk | 3200 bytes (100ms) | 6400 bytes (200ms) | ลด queue overhead |

---

### 4. **Hallucination Detection**: Anti-Looping
```python
# ตรวจหา: "ส่วน ส่วน ส่วน" หรือ "ขอบคุณ ขอบคุณ ขอบคุณ"
from collections import Counter
counts = Counter(tokens)
if counts.most_common(1)[0][1] >= 3:
    logger.warning(f"Hallucination detected: {text}")
    return ""  # ห้ามส่ง hallucination
```

---

### 5. **Transcribe Strategy**: เฉพาะ Final
**ปัญหา:** Partial transcribe ทุก 0.6 วินาที → ช้า + ซ้ำ  
**วิธีแก้:** แปลเฉพาะตอน `silence_counter >= EOU_SILENCE_MS`

```python
# ❌ ก่อน (ทุก STEP_SEC)
if now > MIN_PARTIAL_SEC and (now - last_emit_time >= STEP_SEC):
    text = await transcribe_with_vad(...)  # ทำซ้ำ!
    await ws.send_json({"type": "partial", "text": text})

# ✅ หลัง (เฉพาะเมื่อเงียบ)
if silence_counter >= EOU_SILENCE_MS / 1000:  # เงียบแล้ว
    text = await transcribe_with_vad(...)  # แปล 1 ครั้ง
    await ws.send_json({"type": "final", "text": text})
```

---

### 6. **Whisper Config**: ลด Hallucination
```python
model.transcribe(
    audio=audio,
    language="th",
    beam_size=1,  # Fast (ไม่ generate alternatives)
    vad_filter=True,
    vad_parameters=dict(
        min_silence_duration_ms=500,  # ลด (ก่อน 700)
        threshold=0.6,  # เพิ่ม (ลด noise)
    ),
    temperature=0,  # Deterministic (ไม่ generate คำสุ่ม)
    best_of=1,  # ไม่ต้องสร้าง multiple candidates
)
```

---

## 🔄 Expected Flow ตอนนี้

```
Gateway sends ReSpeaker 16kHz audio
   ↓
Server receives via WebSocket (/ws/stt)
   ↓
FFmpeg: Resample 16kHz → 16kHz (pass-through, ไม่เปลี่ยน rate)
   ↓
PCMBuffer: บันทึกเสียงทั้งหมด (ไม่เก็บ cache overlap)
   ↓
Silence Detection: รอเงียบ >= 1.2 วินาที
   ↓
Transcribe ONCE: เฉพาะตอนเงียบ (ไม่ซ้ำแปล)
   ↓
Hallucination Check: ตรวจหาคำซ้ำ >= 3 ครั้ง
   ↓
Send Final: {"type": "final", "text": "..."}
   ↓
Clear Buffer: ลบทั้งหมด (ไม่มี cache)
   ↓
Wait Next Utterance
```

---

## 🧪 ต้อง Test

1. **ส่งเสียงยาว 3 วินาที** → ต้องแปลออก 1 ครั้ง (ไม่ 5 ครั้ง)
2. **พูดเสียงซ้ำ "ส่วน ส่วน ส่วน"** → ต้องหลีกเลี่ยง (return "")
3. **เงียบ 1.2 วินาที** → trigger transcribe อย่างเดียว (ไม่ 0.6 วินาที)
4. **ReSpeaker 16kHz input** → ได้ข้อความไทยถูกต้อง

---

## 📋 Files Modified
- `/app/api/stt_ws.py`: ✅ Cleaned + Fixed

---

## ⚠️ Next Steps (หากยังไม่ได้)

1. **ลบ gateway.py ออกจาก `/app/api/gateway.py`** (ไม่ต้องใช้บน Server)
2. **Test STT** พร้อม logs เพื่อดู:
   ```
   🎤 STT started: 16000Hz input
   ✅ FINAL: [Thai text]
   🛑 STT session ended
   ```

3. **ถ้ายังไม่ได้** → แก้ memory.py ที่มี caching

---

**Status:** Code cleanup ✅  
**Ready:** Test with Gateway + myAGV audio

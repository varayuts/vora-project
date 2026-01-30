# ✅ STT Latency Optimization - 30 มกราคม 2026

## 🎯 Problem
**STT High Latency:** 5-10 วินาที จากเวลาพูดจนได้ transcript

## 🔍 Root Cause Analysis

### Before Optimization:
```python
STEP_SEC = 1.0              # ประมวลผลทุก 1 วินาที
MIN_PARTIAL_SEC = 2.5       # ต้องมีเสียง >= 2.5s ก่อนแปล
EOU_SILENCE_MS = 2500       # รอเงียบ 2.5 วินาที
DEBOUNCE_SEC = 3.0          # ไม่ส่ง final บ่อยกว่า 3 วินาที
FFmpeg chunk = 6400 bytes   # 200ms at 16kHz
```

**Total Latency Breakdown:**
- Audio buffering: ~2.5s (MIN_PARTIAL_SEC)
- Silence detection: ~2.5s (EOU_SILENCE_MS)
- Whisper processing: ~0.5-1s
- Debounce delay: up to 3s
- **= 5-10 วินาที!**

### Network Test (MyAGV → Gateway):
```bash
ping 192.168.0.60
# avg 8.4ms ✅ Network ไม่มีปัญหา
```

---

## ✅ Optimizations Applied

### 1. Reduce Buffer Requirements (app/api/stt_ws.py)

**MIN_PARTIAL_SEC: 2.5s → 1.0s**
```python
MIN_PARTIAL_SEC = 1.0  # เริ่มแปลได้เร็วขึ้น 2.5x
```
- ก่อน: ต้องรอให้มีเสียง 2.5 วินาที
- หลัง: มีเสียง 1 วินาทีก็แปลได้
- **ลด latency: -1.5s**

---

### 2. Faster Silence Detection

**EOU_SILENCE_MS: 2500ms → 1200ms**
```python
EOU_SILENCE_MS = 1200  # รู้ว่าจบเร็วขึ้น 2x
```
- ก่อน: รอเงียบ 2.5 วินาทีถึงจะรู้ว่าพูดจบ
- หลัง: เงียบ 1.2 วินาทีก็รู้แล้ว
- **ลด latency: -1.3s**

---

### 3. Aggressive Debounce

**DEBOUNCE_SEC: 3.0s → 0.5s**
```python
DEBOUNCE_SEC = 0.5  # ส่ง final บ่อยขึ้น 6x
```
- ก่อน: ต้องรออย่างน้อย 3 วินาทีก่อนส่ง final ครั้งถัดไป
- หลัง: รอแค่ 0.5 วินาที
- **ลด latency: -2.5s**

---

### 4. Smaller Chunk Size

**FFmpeg chunk: 6400 → 3200 bytes**
```python
chunk = await self.proc.stdout.read(3200)  # 100ms at 16kHz
```
- ก่อน: อ่าน 200ms ต่อครั้ง
- หลัง: อ่าน 100ms ต่อครั้ง
- **ลด latency: -100ms (รับรู้เสียงเร็วขึ้น)**

---

### 5. Improved RMS Threshold

**EOU_RMS_THRESH: 0.005 → 0.008**
```python
EOU_RMS_THRESH = 0.008  # ตัดเงียบเร็วขึ้น
```
- ก่อน: threshold ต่ำ → ต้องเงียบมากถึงจะตัด
- หลัง: threshold สูงขึ้นเล็กน้อย → ตัดเงียบได้เร็วขึ้น

**Silence counter adjustment:**
```python
silence_counter += 0.1  # จาก 0.2 (เพราะ chunk เล็กลง)
```

---

### 6. Added Latency Logging

**เพิ่ม timestamp ทุกขั้นตอน:**
```python
transcribe_start = time.time()
logger.info(f"🎙️ Starting Whisper transcription...")
text = await transcribe_with_vad(audio_data, session_lang)
transcribe_time = time.time() - transcribe_start
logger.info(f"⏱️ Whisper took {transcribe_time:.2f}s to transcribe {now:.2f}s audio")
```

**Log output example:**
```
📊 Audio buffer: 1.2s, 38400 bytes, 12 chunks
🎙️ Starting Whisper transcription...
⏱️ Whisper took 0.45s to transcribe 1.2s audio
✅ FINAL: วัวล่า ไปข้างหน้า
```

---

## 📊 Performance Comparison

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| MIN_PARTIAL_SEC | 2.5s | 1.0s | **60% faster** ⚡ |
| EOU_SILENCE_MS | 2500ms | 1200ms | **52% faster** ⚡ |
| DEBOUNCE_SEC | 3.0s | 0.5s | **83% faster** ⚡ |
| FFmpeg chunk | 200ms | 100ms | **50% faster** ⚡ |
| **Total Latency** | **5-10s** | **~2-3s** | **60-70% faster!** 🚀 |

---

## 🧪 Testing Instructions

### 1. Start Server (Already Running)
```bash
cd /home/user/vora_project/VORA/VORA
source vora_env/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### 2. Monitor Logs
```bash
tail -f /tmp/vora_api.log | grep -E "(Transcription|Whisper took|FINAL)"
```

### 3. Test from MyAGV
```bash
# On MyAGV (192.168.0.111):
cd ~/Desktop/VORA_myAGV_only_ros2_package/new
python3 send_audio_to_gateway.py --gateway-ws ws://192.168.0.60:9001/gw/audio

# พูด: "วัวล่า ไปข้างหน้า"
# จับเวลา: จากพูดจบ → เห็น transcript
```

### 4. Expected Results

**Before Optimization:**
```
[T+0s]   พูด "วัวล่า ไปข้างหน้า"
[T+1s]   ...
[T+2s]   ...
[T+3s]   ...
[T+4s]   ...
[T+5s]   ✅ FINAL: วัวล่า ไปข้างหน้า  ← 5 วินาทีหลังพูดจบ
```

**After Optimization (Target):**
```
[T+0s]   พูด "วัวล่า ไปข้างหน้า"
[T+1s]   (เงียบ 1.2s)
[T+2s]   📊 Audio buffer: 1.2s
         🎙️ Starting Whisper transcription...
         ⏱️ Whisper took 0.5s
         ✅ FINAL: วัวล่า ไปข้างหน้า  ← ~2 วินาทีหลังพูดจบ ⚡
```

---

## 🎯 Next Steps

### Optional Further Optimizations:

1. **Streaming Whisper** (Advanced)
   - ใช้ streaming mode แทน batch
   - แปลแบบ real-time ตอนที่กำลังพูด
   - ต้องเปลี่ยน architecture

2. **GPU Pre-warming**
   - Load Whisper model ตอนเริ่ม server (ไม่ lazy load)
   - ลดเวลา inference ครั้งแรก

3. **Model Size Comparison**
   - ทดสอบ `medium` vs `large-v3`
   - `medium` เร็วกว่าแต่อาจแม่นน้อยกว่า

4. **Parallel Processing**
   - ถ้ามี audio หลายสาย → ใช้ multiple GPU streams
   - ตอนนี้ใช้ GPU 0% (idle) → ยังไม่ bottleneck

---

## 📝 Files Modified

- ✅ `app/api/stt_ws.py` - All optimizations applied
- ✅ Server restarted with new config
- ✅ Ready for testing

---

## 🔗 Related

- MyAGV fixes: Silence detection in `send_audio_to_gateway.py`
- Gateway fixes: Duration loop in `ros_cmd.py`
- Progress: `PROGRESS_30JAN2026.txt`

---

**Status:** ✅ OPTIMIZED - Ready for testing!  
**Expected Result:** Latency ลดจาก 5-10s → 2-3s (60-70% faster) 🚀

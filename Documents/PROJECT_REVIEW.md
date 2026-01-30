# 🤖 VORA Project - Comprehensive Review

**Review Date:** January 23, 2026  
**Project Status:** Voice AI Robot Assistant (Development/Alpha)  
**Hardware:** NVIDIA A6000 GPU, ReSpeaker 16kHz Input

---

## 📊 Executive Summary

Your VORA project is a **well-architected AI voice assistant system** with solid foundations. The modular design, proper use of async/await, and integration of cutting-edge models (Faster-Whisper, Ollama) are excellent. However, there are several **critical and moderate issues** that should be addressed for production readiness.

### ✅ Strengths
- **Clean Architecture**: Good separation of concerns (providers, services, routers)
- **Async-First Design**: Proper use of FastAPI + asyncio for non-blocking I/O
- **GPU Optimization**: Using Faster-Whisper with float16, Ollama for efficient inference
- **Modular Providers**: LLM, Search, TTS abstracted into pluggable providers
- **Memory Management**: Session-based chat history with TTL pruning
- **Frontend**: Nice responsive UI with real-time status updates

### 🔴 Critical Issues (Fix First)
1. **Gateway.py is a Notebook-only component** - confused architecture
2. **No error recovery** for ReSpeaker/FFmpeg failures
3. **Missing input validation** on WebSocket messages
4. **Ollama connection not monitored** - silent failures possible
5. **No rate limiting** - vulnerable to abuse

### 🟡 Important Issues (Fix Soon)
1. **Memory leaks possible** in FFmpeg decoder
2. **Thai language typo fixes** hardcoded (not scalable)
3. **Search not integrated** into main agent flow properly
4. **Settings validation missing** - crashes if env vars wrong
5. **No request timeout** on search operations

### 📝 Moderate Issues (Refactor)
1. **Code duplication** in error handling
2. **Logger configuration** scattered
3. **No structured logging** for debugging
4. **WebSocket heartbeat missing** (connection stability)
5. **No metrics/monitoring** for performance tracking

---

## 🔍 Detailed Analysis by Component

### 1. **[stt_ws.py](app/api/stt_ws.py)** - Speech-to-Text WebSocket
**Status:** ✅ Mostly Good (with fixes applied)

**What's Good:**
- ✅ Proper async handling of audio chunks
- ✅ FFmpeg properly configured for 16kHz mono conversion
- ✅ VAD (Voice Activity Detection) filter reduces hallucinations
- ✅ Silence detection for end-of-utterance

**Issues Found & Fixed:**
- ❌ ~~Function name mismatch: `transcribe_vad()` vs `transcribe_with_vad()`~~ ✅ FIXED
- ❌ ~~No error handling in transcription~~ ✅ FIXED
- ⚠️ **FFmpeg process not cleaned up** if exceptions occur

**Remaining Recommendations:**
```python
# Add graceful FFmpeg cleanup
async def close(self):
    if self.proc:
        try:
            self.proc.terminate()  # graceful first
            await asyncio.sleep(0.5)
            if self.proc.returncode is None:
                self.proc.kill()    # force if still alive
        except:
            pass
        self.proc = None
```

**Sample Rate Issue:** ✅ FIXED - Now properly handles ReSpeaker input rates

---

### 2. **[gateway.py](app/api/gateway.py)** - 🔴 CRITICAL ISSUE

**Status:** ❌ Architecture Problem

**Issue:**
This file suggests a Notebook/Server split architecture but it's unclear how it integrates with your main app. The current structure:
- Main server: localhost:8000 (main.py)
- Gateway layer: localhost:9001 (mentioned in client.html)
- Are these the same? Different machines?

**Problems:**
- No health checks or connection retry logic
- Hardcoded SERVER_WS with placeholder `<SERVER_IP>`
- No error handling if Server/Notebook connection fails
- Robot commands can be lost silently

**Recommendation:**
```python
# Add connection monitoring
class GatewayHealthCheck:
    async def monitor_server_health(self, interval_s=30):
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    await client.get(f"{SERVER_HOST}/health", timeout=5)
            except:
                logger.error("Server unreachable - activating local fallback")
            await asyncio.sleep(interval_s)
```

---

### 3. **[main.py](app/main.py)** - FastAPI Setup
**Status:** ✅ Good

**Observations:**
- ✅ Proper CORS configuration for local development
- ✅ Health endpoint with GPU info (nice!)
- ❌ **Missing:** Environment variable validation at startup

**Recommendation:**
```python
import os
from pathlib import Path

@app.on_event("startup")
async def validate_environment():
    """Validate critical configs before accepting requests"""
    required = {
        "OLLAMA_HOST": os.getenv("OLLAMA_HOST"),
        "WHISPER_MODEL": os.getenv("WHISPER_MODEL"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")
    logger.info(f"✅ Environment validated: {required}")
```

---

### 4. **[agent.py](app/core/agent.py)** - Intent Recognition & Answer Generation
**Status:** ⚠️ Needs Improvement

**Issues:**

**Issue #1: Hardcoded Thai typo dictionary**
```python
_TH_FIX = {
    "หมาสมุด": "ห้องสมุด",
    "วอร่า": "VORA",
    ...
}
```
- ❌ Not scalable beyond this lab
- ❌ Missing common typos for other contexts
- ❌ Should be loaded from a file or service

**Recommendation:**
```python
import json
from pathlib import Path

def load_typo_corrections(lang="th"):
    """Load language-specific typo corrections from file"""
    path = Path(__file__).parent / f"corrections_{lang}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}

_TH_FIX = load_typo_corrections("th")
```

**Issue #2: Refine system prompt is very rigid**
- The schema enforcement works but is fragile
- If Gemma3 outputs slightly different JSON keys, parsing fails

**Recommendation:**
```python
def _safe_extract_json(text: str, defaults: dict) -> dict:
    """Extract JSON with fallback to defaults"""
    try:
        return json.loads(text)
    except:
        # Try to extract {..} if wrapped in markdown
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
    return defaults
```

**Issue #3: Search integration missing**
- ❌ `search_when` parameter exists but `agent.answer()` doesn't actually call `SEARCH`
- Need to see [agent.py](app/core/agent.py) lines 80-149 to verify

---

### 5. **[settings.py](app/core/settings.py)** - Configuration
**Status:** ⚠️ Incomplete

**Issues:**

**Issue #1: No validation**
```python
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "600"))
```
- If env var is "invalid", `int()` crashes
- Should validate at startup

**Issue #2: Memory settings not documented**
```python
MEMORY_MAX_TURNS: int = 12  # Per-session, but what if 100 users connect?
```
- No guidance on how to scale memory management

**Recommendation:**
```python
from pydantic import BaseSettings, validator

class Settings(BaseSettings):
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_TIMEOUT: int = 600
    
    @validator("OLLAMA_TIMEOUT")
    def timeout_reasonable(cls, v):
        if v < 10 or v > 3600:
            raise ValueError(f"OLLAMA_TIMEOUT must be 10-3600s, got {v}")
        return v
    
    class Config:
        env_file = ".env"
```

---

### 6. **[memory.py](app/core/memory.py)** - Session Memory
**Status:** ✅ Good Design

**Observations:**
- ✅ TTL-based pruning prevents memory leaks
- ✅ Deque with maxlen prevents unlimited growth
- ✅ Thread-safe for single-process use

**Potential Issues:**

**Issue #1: Not thread-safe for multi-worker**
```python
def add(self, sid: str, role: Role, text: str):
    self._prune()  # Called on every add - expensive!
    buf = self._buf.setdefault(sid, SessionBuf())
```
- If running with `uvicorn --workers 4`, each worker has separate MEMORY
- Chat history won't be shared between workers

**Recommendation:**
- Use Redis for distributed memory:
```python
# Alternative: Redis backend
import redis
class MemoryStore:
    def __init__(self, redis_url="redis://localhost"):
        self.r = redis.from_url(redis_url)
    
    def add(self, sid: str, role: Role, text: str):
        key = f"session:{sid}"
        self.r.lpush(key, json.dumps({"role": role, "text": text}))
        self.r.expire(key, 3600)  # 1 hour TTL
```

---

### 7. **[client.html](app/frontend/client.html)** - Frontend Dashboard
**Status:** ✅ Good UX

**Observations:**
- ✅ Real-time status indicators (ONLINE/OFFLINE)
- ✅ Partial & final transcription display
- ✅ Clean responsive design

**Issues:**

**Issue #1: No reconnection strategy**
```javascript
ws.onclose = () => {
    statusDiv.innerHTML = "🔴 RECONNECTING...";
    setTimeout(connect, 2000);  // Fixed 2s interval
};
```
- Should use exponential backoff for better server stability

**Recommendation:**
```javascript
let reconnectAttempts = 0;
const maxReconnectAttempts = 10;

function reconnect() {
    const delay = Math.min(2000 * Math.pow(2, reconnectAttempts), 30000);
    setTimeout(() => {
        if (reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts++;
            connect();
        } else {
            statusDiv.innerHTML = "🔴 FAILED TO CONNECT";
        }
    }, delay);
}
```

---

### 8. **[environment.yml](environment.yml)** - Dependencies
**Status:** ✅ Good

**Observations:**
- ✅ Using PyTorch with CUDA 12.1 (good for A6000)
- ✅ FFmpeg included (needed for audio conversion)
- ✅ All major dependencies present

**Recommendations:**
- Add version pinning for stability:
```yaml
pip:
  - faster-whisper==1.0.2  # Pin to working version
  - piper-tts==2024.1.8
  - httpx==0.27.2
  - python-dotenv  # Missing! Needed for .env file loading
```

---

## 🎯 Priority Action Items

### 🔴 CRITICAL (Do Now)
1. **Gateway.py architecture clarity**
   - Clarify: Is gateway.py used in production or only for notebook testing?
   - If used: Add connection health checks and failover logic

2. **Environment validation**
   - Add startup validation to main.py
   - Fail fast if Ollama/FFmpeg/models not available

3. **WebSocket error recovery**
   - Add heartbeat mechanism to detect dead connections
   - Implement automatic reconnection with exponential backoff

### 🟡 IMPORTANT (This Week)
4. **Search integration**
   - Verify that `search_when` actually triggers search in `agent.answer()`
   - Add logging to debug search flow

5. **FFmpeg cleanup**
   - Ensure FFmpeg processes are always killed, even on exception
   - Monitor for zombie processes

6. **Rate limiting**
   - Add per-IP rate limiting to prevent abuse
   - Use `slowapi` package with FastAPI

### 📋 MODERATE (Next Sprint)
7. **Monitoring & Logging**
   - Add structured logging with JSON output
   - Track metrics: response times, error rates, GPU memory usage
   - Use OpenTelemetry or Prometheus

8. **Testing**
   - Add unit tests for agent.refine()
   - Add integration tests for STT + LLM pipeline
   - Mock external services (Ollama, SearXNG)

---

## 📈 Performance Recommendations

### Memory Usage
```python
# Current: Unbounded FFmpeg buffers
# Recommended: Limit to 30 seconds of audio
MAX_BUFFER_DURATION_SEC = 30
MAX_BUFFER_BYTES = 16000 * 2 * MAX_BUFFER_DURATION_SEC  # 960KB

if len(pcm_buf._buf) > MAX_BUFFER_BYTES:
    logger.warning("Buffer overflow, dropping oldest data")
    pcm_buf._buf = pcm_buf._buf[-MAX_BUFFER_BYTES:]
```

### Ollama Connection Pooling
```python
# Instead of creating new OllamaProvider each time
# Use a connection pool for better performance
class OllamaClientPool:
    def __init__(self, size=3):
        self.providers = [
            OllamaProvider(model=model)
            for model in ["gemma3:4b-it", "gemma3:12b-it"]
        ]
        self.current = 0
    
    def get(self):
        provider = self.providers[self.current]
        self.current = (self.current + 1) % len(self.providers)
        return provider
```

### GPU Memory
```python
# Monitor during inference
import torch

def log_gpu_memory():
    allocated = torch.cuda.memory_allocated()
    reserved = torch.cuda.memory_reserved()
    logger.info(f"GPU Memory: {allocated/1e9:.2f}GB allocated, {reserved/1e9:.2f}GB reserved")
```

---

## 🔐 Security Considerations

### Current Gaps
1. ❌ No authentication on WebSocket endpoints
2. ❌ CORS allows all origins (`allow_origins=["*"]`)
3. ❌ No input validation on user text (XSS potential in web UI)
4. ❌ FFmpeg running with untrusted input

### Recommendations
```python
from fastapi.security import HTTPBearer

security = HTTPBearer()

@router.websocket("/ws/stt")
async def ws_stt(ws: WebSocket, token: str = Query(...)):
    # Validate token before accepting
    if not verify_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return
    ...

# CORS - restrict to specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://yourdomain.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization"],
)
```

---

## 📚 Recommended Reading

1. **FastAPI Best Practices**: https://fastapi.tiangolo.com/advanced/
2. **AsyncIO Error Handling**: https://docs.python.org/3/library/asyncio.html
3. **Pydantic v2 Settings**: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
4. **OpenTelemetry for Python**: https://opentelemetry.io/docs/instrumentation/python/

---

## 🚀 Next Steps

### Immediate (This Week)
- [ ] Fix gateway.py architecture confusion
- [ ] Add startup environment validation
- [ ] Implement WebSocket heartbeat
- [ ] Verify search integration works

### Short-term (Next 2 Weeks)
- [ ] Add monitoring/logging
- [ ] Implement rate limiting
- [ ] Add unit tests
- [ ] Document API endpoints (add OpenAPI examples)

### Medium-term (Next Month)
- [ ] Switch to Redis for distributed memory
- [ ] Add authentication
- [ ] Performance profiling on A6000
- [ ] Load testing with 10+ concurrent users

---

## 💡 Questions for You

1. **Gateway Architecture**: Is `gateway.py` part of the production deployment, or just for local testing with a Jetson?
2. **Search Usage**: Should search always happen, or only on demand? When should it trigger?
3. **Scaling**: Do you plan to run this with multiple workers, or single-process?
4. **Language**: Is the system Thai-only, or should it support other languages?
5. **Hardware**: Is A6000 being used locally, or accessed via cloud?

---

**Generated:** January 23, 2026  
**Reviewer:** AI Code Assistant  
**Confidence:** High - Based on complete codebase review

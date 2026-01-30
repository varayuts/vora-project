# 📋 VORA Project Structure - Reorganized

**Date:** January 30, 2026  
**Status:** ✅ Organized and documented

---

## 🗂️ New Folder Structure

```
VORA/
│
├── 📱 app/                          # Main application code
│   ├── api/                         # FastAPI endpoints (routers)
│   ├── core/                        # Business logic (agent, memory, settings)
│   ├── providers/                   # External services (LLM, TTS, Search)
│   ├── services/                    # Internal services (Thai TTS)
│   ├── schemas/                     # Pydantic models
│   ├── frontend/                    # Web interface (HTML/JS)
│   └── main.py                      # FastAPI app entry point
│
├── 🤖 Gateway/                      # Robot control gateway (Windows PC)
│   ├── gateway/                     # ROSBridge integration
│   │   ├── main.py                  # Gateway WebSocket server
│   │   ├── intent_parser.py         # Regex-based command parser
│   │   └── ros_cmd.py               # ROS command executor
│   └── start_gateway.sh             # Startup script
│
├── 🦾 Myagv/                        # Robot-specific code (Jetson Nano)
│   ├── vora_robot_bridge/           # ROS package
│   ├── maps/                        # SLAM maps
│   └── start_myagv.sh               # Robot startup script
│
├── 🧠 models/                       # AI models
│   └── asr/                         # Whisper models
│       └── distil-whisper-th-large-v3-ct2/
│
├── 📚 Documents/                    # **ALL DOCUMENTATION HERE**
│   ├── Progress/                    # Progress reports & presentations
│   │   ├── PROGRESS_PRESENTATION_JAN2026.md  ⭐ Main presentation
│   │   ├── PROGRESS_26JAN2026.txt
│   │   ├── PROGRESS_28JAN2026.txt
│   │   └── PROGRESS_30JAN2026.txt
│   │
│   ├── INDEX.md                     # Documentation index
│   ├── PROJECT_REVIEW.md            # Code review & analysis
│   ├── DEPLOYMENT_QUICK_START.md    # Deployment guide
│   ├── FINAL_DEPLOYMENT_RECAP.md    # Detailed deployment
│   ├── WEBAPP_SETUP.md              # Web interface setup
│   ├── TEST_WEBSOCKET.md            # WebSocket debugging
│   ├── STT_LATENCY_FIXES.md         # Performance optimization
│   ├── README.md                    # Main project README
│   ├── LICENSE                      # License file
│   │
│   └── Scripts/                     # Utility scripts
│       ├── AI_PROMPT_*.txt          # LLM prompts
│       ├── check_deployment.sh      # Health check
│       ├── start_dev.sh             # Dev environment
│       └── start_tailscale.sh       # VPN setup
│
├── 🧪 tests/                        # **ALL TESTS HERE**
│   ├── README.md                    # Test documentation
│   ├── test_stt.py                  # STT WebSocket test
│   ├── test_websocket.py            # Connection test
│   └── test_tts_only.sh             # TTS test script
│
├── 🐍 vora_env/                     # Conda environment (not in git)
│
├── environment.yml                  # Conda environment spec
├── README_NEW.md                    # ⭐ New main README (replace old)
├── .env                             # Environment variables
├── .gitignore                       # Git ignore rules
├── cert.pem                         # SSL certificate
└── key.pem                          # SSL private key
```

---

## 📊 What Changed?

### Before (Messy)
```
VORA/
├── ❌ 15+ .md files scattered in root
├── ❌ test_*.py mixed with app code
├── ❌ PROGRESS_*.md not organized
├── ❌ Hard to find documentation
└── ❌ No clear structure
```

### After (Organized) ✅
```
VORA/
├── ✅ Documents/ - All docs in one place
│   └── Progress/ - All progress reports
├── ✅ tests/ - All test files
├── ✅ Clean root directory
├── ✅ README_NEW.md - Clear entry point
└── ✅ INDEX.md - Easy navigation
```

---

## 🎯 Quick Navigation

### For Presentation
```bash
Documents/Progress/PROGRESS_PRESENTATION_JAN2026.md
```

### For Development
```bash
README_NEW.md          # Start here
Documents/PROJECT_REVIEW.md
app/                   # Code here
```

### For Deployment
```bash
Documents/DEPLOYMENT_QUICK_START.md
Documents/FINAL_DEPLOYMENT_RECAP.md
```

### For Testing
```bash
tests/README.md        # Test documentation
tests/test_stt.py      # Run tests
```

---

## 📝 Files Moved

### To Documents/Progress/
- ✅ PROGRESS_PRESENTATION_JAN2026.md
- ✅ PROGRESS_26JAN2026.txt
- ✅ PROGRESS_28JAN2026.txt
- ✅ PROGRESS_30JAN2026.txt

### To Documents/
- ✅ PROJECT_REVIEW.md
- ✅ DEPLOYMENT_QUICK_START.md
- ✅ FINAL_DEPLOYMENT_RECAP.md
- ✅ WEBAPP_SETUP.md
- ✅ TEST_WEBSOCKET.md
- ✅ STT_LATENCY_FIXES.md
- ✅ README.md (original)
- ✅ LICENSE
- ✅ AI_PROMPT_*.txt
- ✅ check_deployment.sh
- ✅ start_dev.sh
- ✅ start_tailscale.sh
- ✅ คำสั่ง.txt

### To tests/
- ✅ test_stt.py
- ✅ test_websocket.py
- ✅ test_tts_only.sh

---

## ✨ Benefits

1. **Cleaner Root Directory**
   - Only essential files visible
   - Easy to understand project structure

2. **Better Documentation**
   - All docs in one place
   - Clear index (INDEX.md)
   - Easy to find what you need

3. **Organized Tests**
   - All tests in tests/
   - Test README explains usage
   - Easy to run and maintain

4. **Professional Structure**
   - Follows best practices
   - Ready for thesis submission
   - Easy for others to understand

---

## 🚀 Next Steps

1. **Replace old README:**
   ```bash
   mv README.md Documents/README_OLD.md
   mv README_NEW.md README.md
   ```

2. **Update links in code** (if any hardcoded paths to .md files)

3. **Commit changes:**
   ```bash
   git add .
   git commit -m "Reorganize project structure: docs to Documents/, tests to tests/"
   git push
   ```

4. **Update presentation** if needed (paths changed)

---

## 📌 Important Notes

- ⚠️ **Don't delete** Documents/README.md (original) - it has historical info
- ✅ **Use** Documents/INDEX.md for quick navigation
- ✅ **Use** README_NEW.md as main entry point (or rename to README.md)
- ✅ All test commands updated in tests/README.md

---

**Reorganized by:** VORA Development Team  
**Date:** January 30, 2026  
**Status:** ✅ Complete and documented

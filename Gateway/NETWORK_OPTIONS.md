# 🌐 MyAGV ↔️ Gateway Network Options

**Date:** 29 มกราคม 2026  
**Question:** ต้อง set static IP บน Gateway ไหม?

---

## 📊 Network Topology

```
Mobile/Web
    │
    │ HTTPS/WSS
    │
    ↓
Tailscale (Internet)
    │
    ↓
VORA Server (A6000)
    │
    ↓
Gateway (Windows Laptop)
    │ Local Network
    ↓
MyAGV Robot (Jetson Nano)
```

---

## 🎯 3 Connection Options

### Option 1: Local WiFi (Current Setup) ⭐ RECOMMENDED

**Pros:**
- ✅ Fast (low latency)
- ✅ No internet dependency
- ✅ Secure (local network only)

**Cons:**
- ❌ Gateway IP อาจเปลี่ยนถ้าใช้ DHCP
- ❌ ต้องอยู่ same network

**Configuration:**
```bash
# MyAGV connects to Gateway via local WiFi
python3 send_audio_to_gateway.py \
    --gateway-ws ws://192.168.0.60:9001/gw/audio
```

**Static IP Required:**
- ✅ **YES** - แนะนำให้ set Gateway = 192.168.0.60
- Already done: MyAGV = 192.168.0.111 ✅

---

### Option 2: Tailscale Only

**Pros:**
- ✅ Gateway IP never changes (100.73.232.94)
- ✅ Works from anywhere (internet)
- ✅ Encrypted

**Cons:**
- ❌ Slower (internet latency)
- ❌ Requires internet
- ❌ Need to install Tailscale on MyAGV

**Configuration:**
```bash
# Install Tailscale on MyAGV first!
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Then connect
python3 send_audio_to_gateway.py \
    --gateway-ws ws://100.73.232.94:9001/gw/audio
```

**Static IP Required:**
- ❌ **NO** - Tailscale IP is always static

---

### Option 3: Hybrid (Best of Both)

**Pros:**
- ✅ Fast local connection (primary)
- ✅ Fallback to Tailscale (backup)
- ✅ Most reliable

**Cons:**
- ❌ More complex setup
- ❌ Need Tailscale on MyAGV

**Configuration:**
```bash
# Try local first, fallback to Tailscale
python3 send_audio_to_gateway.py \
    --gateway-ws ws://192.168.0.60:9001/gw/audio \
    --fallback-ws ws://100.73.232.94:9001/gw/audio
```

**Static IP Required:**
- ⚠️ **RECOMMENDED** - Set Gateway WiFi = 192.168.0.60

---

## 🤔 Decision Matrix

| Scenario | Use Option | Static IP Needed? |
|----------|------------|-------------------|
| Lab testing (same room) | Option 1 | ✅ YES |
| Production deployment | Option 3 | ✅ YES |
| Remote operation | Option 2 | ❌ NO (Tailscale) |
| Quick demo | Option 1 | ⚠️ Can use DHCP |

---

## 💡 Recommendation

**For your setup (same building, production):**

✅ **Use Option 1 + Set Static IP on Gateway**

**Reasons:**
1. MyAGV already has static IP (192.168.0.111) ✅
2. Gateway currently DHCP (192.168.0.60) → might change
3. You want reliable connection → need both static

**Steps:**
1. ✅ MyAGV static IP: Done (192.168.0.111)
2. ⏳ Gateway static IP: **Set to 192.168.0.60** (see SETUP_STATIC_IP_WINDOWS.md)
3. ⏳ Update MyAGV script: Use `ws://192.168.0.60:9001/gw/audio`

---

## 🚀 Quick Setup Guide

### Step 1: Set Gateway Static IP (Windows)

**GUI Method (Easy):**
1. `Win + I` → Network & Internet → Wi-Fi → RA-Admin
2. IP assignment → **Manual**
3. IPv4:
   - IP: `192.168.0.60`
   - Subnet: `24`
   - Gateway: `192.168.0.1`
   - DNS: `8.8.8.8`
4. Save

### Step 2: Verify Connectivity

**On Gateway (Windows):**
```powershell
ipconfig | findstr "IPv4"
# Should show: 192.168.0.60

ping 192.168.0.111
# Should reply from MyAGV
```

**On MyAGV (Linux):**
```bash
hostname -I
# Should show: 192.168.0.111

ping 192.168.0.60
# Should reply from Gateway
```

### Step 3: Start Services

**On Gateway:**
```powershell
cd Gateway
bash start_gateway.sh
# Should show: Listening on 0.0.0.0:9001
```

**On MyAGV:**
```bash
cd ~/Desktop/VORA_myAGV_only_ros2_package
./start_myagv.sh 192.168.0.60
```

---

## ✅ Final Configuration

After setup complete:

```yaml
Network: RA-Admin (WiFi)
Router: 192.168.0.1

Devices:
  - Gateway (Windows):
      WiFi IP: 192.168.0.60 (STATIC ✅)
      Tailscale IP: 100.73.232.94 (STATIC ✅)
      Port: 9001
      
  - MyAGV (Jetson Nano):
      WiFi IP: 192.168.0.111 (STATIC ✅)
      ROSBridge: ws://192.168.0.111:9090
      
Connection:
  MyAGV → Gateway: ws://192.168.0.60:9001/gw/audio
  Gateway → VORA: https://user.tail87d9fe.ts.net
```

---

## 🐛 Troubleshooting

### Problem: Gateway IP changed after reboot
**Solution:** Set static IP (see SETUP_STATIC_IP_WINDOWS.md)

### Problem: MyAGV cannot reach Gateway
**Check:**
```bash
ping 192.168.0.60          # Should reply
telnet 192.168.0.60 9001   # Should connect
curl http://192.168.0.60:9001/health  # Should return JSON
```

### Problem: Gateway cannot reach VORA Server
**Check:**
```powershell
curl https://user.tail87d9fe.ts.net/health  # Should reply
```

---

## 📚 Related Documents

- [Gateway Static IP Setup (Windows)](./SETUP_STATIC_IP_WINDOWS.md)
- [MyAGV Static IP Setup (Linux)](../Myagv/SETUP_STATIC_IP.md)
- [Full Deployment Guide](../DEPLOYMENT.md)

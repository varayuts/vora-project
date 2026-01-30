# 📝 VORA Network Setup - Quick Guide
**Problem:** IP addresses change after reboot  
**Solution:** 3 methods to choose from

---

## 🎯 Recommended Approach

### ✅ **Method 1: Static IP on MyAGV** (Best)

**Why:** Simple, reliable, no router config needed

**Setup (5 minutes):**
```bash
# On Jetson Nano (MyAGV)
sudo nmcli connection modify "YourWiFi" \
  ipv4.method manual \
  ipv4.addresses "192.168.0.111/24" \
  ipv4.gateway "192.168.0.1" \
  ipv4.dns "8.8.8.8"

sudo nmcli connection down "YourWiFi"
sudo nmcli connection up "YourWiFi"

# Verify
ip addr show wlan0
# Should show: inet 192.168.0.111/24
```

**Update Gateway:**
```bash
# Gateway/.env
ROSBRIDGE=ws://192.168.0.111:9090
```

**✅ Done! IP never changes again**

📖 **Detailed Guide:** [Myagv/SETUP_STATIC_IP.md](Myagv/SETUP_STATIC_IP.md)

---

## 🔍 **Method 2: Auto-Discovery** (Lazy)

**Why:** No manual IP configuration needed

**Setup:**
```bash
# On Gateway
cd Gateway
python3 find_myagv.py
```

**What it does:**
1. 🔍 Scans local network for ROSBridge (port 9090)
2. 📝 Finds MyAGV IP automatically  
3. ✏️ Updates `Gateway/.env` with correct IP

**Output:**
```
✅ Found ROSBridge at 192.168.0.111:9090
✅ Updated Gateway/.env: ROSBRIDGE=ws://192.168.0.111:9090

Next: Restart Gateway!
```

**⚠️ Limitation:** Must run after each MyAGV reboot if using DHCP

---

## 🌐 **Method 3: DHCP Reservation** (Pro)

**Why:** No config on MyAGV, works automatically

**Setup:**
1. Get MyAGV MAC address:
   ```bash
   # On Jetson Nano
   ip link show wlan0
   # MAC: aa:bb:cc:dd:ee:ff
   ```

2. Login to Router Web UI (e.g., `192.168.0.1`)

3. Find "DHCP Reservation" or "Static DHCP"

4. Add entry:
   - MAC: `aa:bb:cc:dd:ee:ff`
   - IP: `192.168.0.111`
   - Name: `myagv`

5. Save & Reboot router

**✅ Done! MyAGV always gets same IP**

---

## 🚀 **Bonus: Tailscale (Advanced)**

**Why:** Static IP + Works from anywhere

**Setup:**
```bash
# On Jetson Nano
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Get Tailscale IP
tailscale ip -4
# Example: 100.102.217.50
```

**Update Gateway:**
```bash
# Gateway/.env
ROSBRIDGE=ws://100.102.217.50:9090
```

**Advantages:**
- ✅ IP never changes
- ✅ Works from anywhere (not just local WiFi)  
- ✅ Encrypted connection

**Disadvantages:**
- ⚠️ Requires internet
- ⚠️ Slightly higher latency

---

## 📊 Comparison

| Method | Setup Time | Reliability | Best For |
|--------|-----------|-------------|----------|
| **Static IP** | 5 min | ⭐⭐⭐⭐⭐ | ✅ Most users |
| **Auto-discovery** | 1 min | ⭐⭐⭐ | Quick testing |
| **DHCP Reservation** | 10 min | ⭐⭐⭐⭐⭐ | If you have router access |
| **Tailscale** | 15 min | ⭐⭐⭐⭐⭐ | Remote access needed |

---

## 🎯 Our Recommendation

1. **For Production:** Use **Static IP** (Method 1)
2. **For Testing:** Use **Auto-discovery** (Method 2)
3. **For Lab Deployment:** Use **DHCP Reservation** (Method 3)
4. **For Remote Control:** Add **Tailscale** (Bonus)

---

## 🔧 Quick Commands

```bash
# Check current IP
ip addr show wlan0

# Test connectivity
ping 192.168.0.111

# Find MyAGV automatically
cd Gateway && python3 find_myagv.py

# Restart Gateway
cd Gateway && ./start_gateway.sh
```

---

## 📚 Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md) - Full deployment guide
- [Myagv/SETUP_STATIC_IP.md](Myagv/SETUP_STATIC_IP.md) - Detailed static IP setup
- [Gateway/find_myagv.py](Gateway/find_myagv.py) - Auto-discovery script

---

**Questions?** Check [DEPLOYMENT.md](DEPLOYMENT.md) troubleshooting section!

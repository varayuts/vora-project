# 🪟 Gateway Static IP Setup (Windows 11)

**Date:** 29 มกราคม 2026  
**Purpose:** Lock Gateway IP so MyAGV can always connect

---

## 📋 Current Configuration

**From `ipconfig`:**
```
Wireless LAN adapter Wi-Fi:
   IPv4 Address. . . . . . . . . . . : 192.168.0.60
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.0.1
```

**Recommended Static IP:** `192.168.0.60` (keep current)

---

## 🎯 Method 1: GUI (Recommended)

### Step 1: Open Network Settings
1. Press `Win + I` → **Network & Internet**
2. Click **Wi-Fi** → **RA-Admin** (your network name)
3. Click **Edit** next to IP assignment

### Step 2: Configure Static IP
1. Change from **Automatic (DHCP)** → **Manual**
2. Turn on **IPv4**
3. Fill in:
   ```
   IP address:        192.168.0.60
   Subnet prefix:     24
   Gateway:           192.168.0.1
   Preferred DNS:     8.8.8.8
   Alternate DNS:     8.8.4.4
   ```
4. Click **Save**

### Step 3: Verify
```powershell
ipconfig

# Should see:
#   IPv4 Address. . . . . . . . . . . : 192.168.0.60
```

---

## 💻 Method 2: PowerShell (Advanced)

### Step 1: Run as Administrator
Right-click PowerShell → **Run as Administrator**

### Step 2: Get Interface Info
```powershell
Get-NetAdapter | Where-Object {$_.Status -eq "Up" -and $_.Name -like "*Wi-Fi*"}

# Note the InterfaceIndex (e.g., 18)
```

### Step 3: Set Static IP
```powershell
# Replace InterfaceIndex with your value (e.g., 18)
$InterfaceIndex = 18

# Remove DHCP
Set-NetIPInterface -InterfaceIndex $InterfaceIndex -Dhcp Disabled

# Set static IP
New-NetIPAddress -InterfaceIndex $InterfaceIndex `
    -IPAddress 192.168.0.60 `
    -PrefixLength 24 `
    -DefaultGateway 192.168.0.1

# Set DNS
Set-DnsClientServerAddress -InterfaceIndex $InterfaceIndex `
    -ServerAddresses 8.8.8.8,8.8.4.4
```

### Step 4: Verify
```powershell
Get-NetIPAddress -InterfaceIndex $InterfaceIndex -AddressFamily IPv4

# Should show:
#   IPAddress: 192.168.0.60
```

---

## 🔙 Revert to DHCP (If Needed)

### GUI Method:
1. Network Settings → Wi-Fi → RA-Admin → Edit
2. Change **Manual** → **Automatic (DHCP)**
3. Save

### PowerShell Method:
```powershell
$InterfaceIndex = 18  # Your interface

# Re-enable DHCP
Set-NetIPInterface -InterfaceIndex $InterfaceIndex -Dhcp Enabled

# Remove static IP
Remove-NetIPAddress -InterfaceIndex $InterfaceIndex -IPAddress 192.168.0.60 -Confirm:$false

# Remove static gateway
Remove-NetRoute -InterfaceIndex $InterfaceIndex -DestinationPrefix "0.0.0.0/0" -Confirm:$false

# Reset DNS to automatic
Set-DnsClientServerAddress -InterfaceIndex $InterfaceIndex -ResetServerAddresses
```

---

## ✅ Verification Checklist

After setting static IP:

**1. Check IP:**
```powershell
ipconfig | findstr "IPv4"
# Should show: 192.168.0.60
```

**2. Test Gateway:**
```powershell
ping 192.168.0.1
# Should reply
```

**3. Test Internet:**
```powershell
ping 8.8.8.8
# Should reply
```

**4. Test MyAGV:**
```powershell
ping 192.168.0.111
# Should reply (if MyAGV is on)
```

**5. Test Gateway Service:**
```powershell
curl http://localhost:9001/health
# Should return: {"status":"ok",...}
```

---

## 🚨 Common Issues

### Issue 1: No Internet after setting static IP
**Cause:** Wrong gateway or DNS

**Fix:**
```powershell
# Check gateway
ping 192.168.0.1

# If no reply, check router IP:
# Router admin page usually: http://192.168.0.1
```

### Issue 2: Cannot connect to MyAGV
**Cause:** Wrong subnet or firewall

**Fix:**
```powershell
# Check firewall
netsh advfirewall show allprofiles

# Temporarily disable (testing only!)
netsh advfirewall set allprofiles state off

# Re-enable after testing
netsh advfirewall set allprofiles state on
```

### Issue 3: IP conflict
**Cause:** Another device using 192.168.0.60

**Fix:**
```powershell
# Use different IP (e.g., 192.168.0.65)
# Update MyAGV command:
python3 send_audio_to_gateway.py --gateway-ws ws://192.168.0.65:9001/gw/audio
```

---

## 📊 Network Diagram After Setup

```
Internet
   │
   │
Router (192.168.0.1)
   │
   ├─── Gateway (Windows) - 192.168.0.60 (STATIC ✅)
   │    └─── VORA Server (via Tailscale)
   │
   └─── MyAGV (Jetson Nano) - 192.168.0.111 (STATIC ✅)
```

---

## 🎯 Next Steps

After setting static IP:

1. **Update MyAGV command:**
   ```bash
   # On MyAGV Jetson Nano
   cd ~/Desktop/VORA_myAGV_only_ros2_package
   ./start_myagv.sh 192.168.0.60
   ```

2. **Test connection:**
   ```bash
   # On MyAGV
   ping 192.168.0.60
   curl http://192.168.0.60:9001/health
   ```

3. **Start Gateway:**
   ```powershell
   # On Gateway Windows
   cd Gateway
   .\start_gateway.sh  # or start manually
   ```

---

## 📝 Summary

| Device | IP | Type | Status |
|--------|-------|------|--------|
| Router | 192.168.0.1 | - | - |
| Gateway (WiFi) | 192.168.0.60 | Static | ⏳ To set |
| Gateway (Tailscale) | 100.73.232.94 | Static | ✅ Already |
| MyAGV | 192.168.0.111 | Static | ✅ Done |

**Recommendation:** Set static IP on Gateway WiFi (192.168.0.60) for reliable local network communication.



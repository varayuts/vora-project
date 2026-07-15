# 🔧 Setup Static IP for MyAGV (Jetson Nano)
**Problem:** IP address changes every reboot (DHCP)  
**Solution:** Set static IP on Jetson Nano

---

## Method 1: Static IP via NetworkManager (Recommended)

### Step 1: Check Current Connection
```bash
# On Jetson Nano
nmcli connection show
```

Output example:
```
NAME                UUID                                  TYPE      DEVICE
MyWiFi              a1b2c3d4-1234-5678-abcd-123456789abc  wifi      wlan0
```

### Step 2: Get Current IP Range
```bash
ip addr show wlan0
```

Output example:
```
wlan0: ...
    inet 192.168.0.111/24 brd 192.168.0.255 ...
```

**Note:** This means your network is `192.168.0.0/24`

### Step 3: Set Static IP
```bash
# Replace "MyWiFi" with your connection name
# Choose an IP outside DHCP range (e.g., 192.168.0.111)

sudo nmcli connection modify "MyWiFi" \
  ipv4.method manual \
  ipv4.addresses "192.168.0.111/24" \
  ipv4.gateway "192.168.0.1" \
  ipv4.dns "8.8.8.8,8.8.4.4"
```

### Step 4: Restart Connection
```bash
sudo nmcli connection down "MyWiFi"
sudo nmcli connection up "MyWiFi"
```

### Step 5: Verify
```bash
ip addr show wlan0
```

Should show:
```
inet 192.168.0.111/24 ...
```

**✅ Done! IP is now static (192.168.0.111)**

---

## Method 2: Static IP via netplan (Alternative)

### Step 1: Edit netplan config
```bash
sudo nano /etc/netplan/01-network-manager-all.yaml
```

### Step 2: Add static IP config
```yaml
network:
  version: 2
  renderer: NetworkManager
  wifis:
    wlan0:
      dhcp4: no
      addresses:
        - 192.168.0.111/24
      gateway4: 192.168.0.1
      nameservers:
        addresses: [8.8.8.8, 8.8.4.4]
      access-points:
        "YourWiFiSSID":
          password: "YourWiFiPassword"
```

### Step 3: Apply
```bash
sudo netplan apply
```

---

## Method 3: DHCP Reservation on Router (Easiest)

### Step 1: Get MyAGV MAC Address
```bash
# On Jetson Nano
ip link show wlan0
```

Output:
```
wlan0: ... link/ether aa:bb:cc:dd:ee:ff ...
```

**MAC Address:** `aa:bb:cc:dd:ee:ff`

### Step 2: Login to Router
- Go to router web interface (usually `192.168.0.1` or `192.168.1.1`)
- Login with admin credentials

### Step 3: Add DHCP Reservation
- Find "DHCP Reservation" or "Address Reservation"
- Add entry:
  - **MAC Address:** `aa:bb:cc:dd:ee:ff`
  - **IP Address:** `192.168.0.111`
  - **Name:** `myagv-jetson`

### Step 4: Save & Reboot Router
Router will now always assign `192.168.0.111` to this MAC address

**✅ Done! No need to configure on Jetson**

---

## Recommended IP Ranges

**Typical Home Network:** `192.168.0.0/24` or `192.168.1.0/24`

**Recommended Static IPs:**
- **Router/Gateway:** `192.168.0.1` (usually default)
- **DHCP Pool:** `192.168.0.100-192.168.0.200` (auto-assigned)
- **Static Devices:** `192.168.0.10-192.168.0.99` (manual)

**VORA Components:**
- **MyAGV (Jetson Nano):** `192.168.0.111` ← Static
- **Gateway (Notebook):** DHCP OK (has Tailscale)
- **VORA Server (A6000):** DHCP OK (has Tailscale)

---

## Update Gateway .env

After setting static IP on MyAGV:

```bash
# Edit Gateway/.env
nano Gateway/.env
```

Change to:
```bash
ROSBRIDGE=ws://192.168.0.111:9090
```

**✅ Now Gateway will always find MyAGV at the same IP!**

---

## Troubleshooting

### Problem: Can't connect after static IP
**Solution:**
```bash
# Revert to DHCP
sudo nmcli connection modify "MyWiFi" ipv4.method auto
sudo nmcli connection down "MyWiFi"
sudo nmcli connection up "MyWiFi"
```

### Problem: Wrong gateway
**Solution:** Check router IP first:
```bash
ip route show default
# Output: default via 192.168.0.1 ...
```
Use that IP as gateway

### Problem: No internet after static IP
**Solution:** Check DNS:
```bash
# Test DNS
ping 8.8.8.8

# If OK, DNS is the problem
# Re-run nmcli with correct dns:
sudo nmcli connection modify "MyWiFi" ipv4.dns "8.8.8.8,1.1.1.1"
```

---

## Bonus: Use Tailscale on MyAGV (Advanced)

If you install Tailscale on MyAGV, it gets a static Tailscale IP:

### Install Tailscale on Jetson Nano
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### Get Tailscale IP
```bash
tailscale ip -4
# Example: 100.102.217.50
```

### Update Gateway .env
```bash
ROSBRIDGE=ws://100.102.217.50:9090
```

**✅ Advantages:**
- Static IP that never changes
- Works from anywhere (not just local WiFi)
- Encrypted connection

**⚠️ Disadvantages:**
- Requires internet connection
- Slightly more latency

---

## Which Method to Choose?

| Method | Difficulty | Recommended For |
|--------|-----------|-----------------|
| **Static IP (nmcli)** | Easy | ✅ Best for most cases |
| **DHCP Reservation** | Easiest | If you have router access |
| **netplan** | Medium | Ubuntu Server |
| **Tailscale** | Medium | Remote access needed |

**Recommendation:** Start with **Static IP via nmcli** (Method 1)

---

**After setup, test:**
```bash
# From Gateway/Notebook
ping 192.168.0.111

# Should always work, even after reboots!
```

สำเร็จ! 🎉



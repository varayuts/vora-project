# 🔍 WebSocket Connection Debug Guide

## ปัญหา: "Not Connected" ใน VORA Web App

### ขั้นตอนตรวจสอบ:

#### 1. เปิด Browser Console (F12 → Console)
```
คีย์บอร์ด: F12 หรือ Ctrl+Shift+I
```

#### 2. ดู Error Messages
มองหา error แบบนี้:
```
❌ WebSocket connection to 'wss://...' failed: 
   - ERR_CERT_AUTHORITY_INVALID
   - ERR_CONNECTION_REFUSED
   - net::ERR_CERT_COMMON_NAME_INVALID
```

---

## วิธีแก้ตาม Error:

### Error 1: `ERR_CERT_AUTHORITY_INVALID` (Self-signed cert)

**มือถือ:**
1. เปิด `https://100.102.217.45:8443/health` ใน tab ใหม่
2. จะเจอ "Not Secure" warning
3. กด **Advanced** → **Proceed anyway**
4. กลับมา refresh `/app` อีกครั้ง

**Desktop:**
1. เปิด `https://localhost:8443/health`
2. Accept certificate
3. Refresh `/app`

### Error 2: `ERR_CONNECTION_REFUSED`

แสดงว่า WebSocket endpoint ไม่ทำงาน:

```bash
# เช็คว่า server running:
curl -sk https://localhost:8443/health

# เช็ค WebSocket route:
curl -sk https://localhost:8443/docs
# → ดูว่ามี /ws/stt endpoint ไหม
```

### Error 3: `Failed to load /config.js`

Config ไม่โหลด → ใช้ fallback port ผิด:

```bash
# ทดสอบ:
curl -sk https://localhost:8443/config.js

# ควรได้:
window.VORA_CONFIG = {
    API_PORT: 8443,
    IS_HTTPS: true,
    ...
}
```

---

## Quick Fix: ถ้า WebSocket ยังไม่เชื่อม

เพิ่มใน `index.html` เพื่อ bypass certificate check:

```javascript
// ใน connectServer() function
const ws = new WebSocket(SERVER_WS_URL);

// เพิ่ม timeout handler:
const connectTimeout = setTimeout(() => {
    console.error('⏱️ WebSocket connection timeout');
    ws.close();
    scheduleReconnect();
}, 10000); // 10 seconds

ws.onopen = () => {
    clearTimeout(connectTimeout);
    // ... rest of code
};
```

---

## ตรวจสอบด้วย Network Tab:

1. เปิด F12 → **Network**
2. Filter: **WS** (WebSocket)
3. Refresh หน้า
4. ดู WebSocket connection:
   - ✅ **Status 101** = สำเร็จ
   - ❌ **Status 400/403/500** = มีปัญหา

---

## ถ้ายังไม่ได้

ส่ง screenshot ของ:
1. Browser Console (F12 → Console)
2. Network Tab (F12 → Network → WS)
3. Server logs: `cat /tmp/vora_api.log | tail -50`

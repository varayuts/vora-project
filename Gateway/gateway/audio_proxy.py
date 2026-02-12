import asyncio
import json
import logging
import websockets
from fastapi import WebSocket, WebSocketDisconnect

# ตั้งค่า Logger เพื่อให้เห็น Error ชัดเจนใน Terminal
logger = logging.getLogger("uvicorn")

async def upstream_stt_proxy(server_ws: str, client_ws: WebSocket, upstream_init: dict, on_text):
    """
    Proxy function to bridge:
    1. Client Audio (WebSocket) -> Server Whisper (WebSocket)
    2. Server Text (WebSocket) -> Client (Callback on_text)
    """
    try:
        # เชื่อมต่อไปยัง Server Whisper (10.90.4.61)
        async with websockets.connect(server_ws, max_size=8*1024*1024) as upstream:
            logger.info(f"Connected to Upstream Server: {server_ws}")
            
            # 1. ส่ง Config เริ่มต้น (เช่น rate, lang) ไปบอก Server ก่อน
            await upstream.send(json.dumps(upstream_init))

            # Task A: Loop รับ Audio Binary จาก Client (AGV) -> ส่งไป Server
            # ✅ Optimized: Buffer small chunks and batch-send to reduce WS overhead
            #    MyAGV sends many small chunks — batching reduces per-message latency across 2 WS hops
            async def client_to_server():
                BATCH_BYTES = 6400   # ~200ms at 16kHz mono 16-bit
                FLUSH_SEC   = 0.15   # Flush at most every 150ms
                buffer = bytearray()
                try:
                    while True:
                        try:
                            data = await asyncio.wait_for(
                                client_ws.receive_bytes(),
                                timeout=FLUSH_SEC
                            )
                            buffer.extend(data)
                            # Send when buffer is large enough
                            if len(buffer) >= BATCH_BYTES:
                                await upstream.send(bytes(buffer))
                                buffer = bytearray()
                        except asyncio.TimeoutError:
                            # Timeout → flush any accumulated data
                            if buffer:
                                await upstream.send(bytes(buffer))
                                buffer = bytearray()
                except WebSocketDisconnect:
                    if buffer:
                        try:
                            await upstream.send(bytes(buffer))
                        except:
                            pass
                    logger.info("Client disconnected (Stop speaking)")
                except Exception as e:
                    if buffer:
                        try:
                            await upstream.send(bytes(buffer))
                        except:
                            pass
                    logger.error(f"Error reading from client: {e}")

            # Task B: Loop รับ Text JSON จาก Server -> ส่งกลับ Client (ผ่าน on_text)
            async def server_to_client():
                try:
                    async for message in upstream:
                        # message จาก Server จะเป็น Text JSON ผลลัพธ์
                        await on_text(message)
                except Exception as e:
                    logger.error(f"Error reading from upstream: {e}")

            # รันทั้ง 2 Task พร้อมกัน (รับเสียงเข้า และ รอผลลัพธ์ออก)
            task_c2s = asyncio.create_task(client_to_server())
            task_s2c = asyncio.create_task(server_to_client())

            # รอจนกว่าฝ่ายใดฝ่ายหนึ่งจะหลุด (ปกติคือ Client หยุดพูดหรือปิด Connection)
            done, pending = await asyncio.wait(
                [task_c2s, task_s2c],
                return_when=asyncio.FIRST_COMPLETED
            )

            # ยกเลิก Task ที่เหลือเพื่อเคลียร์ Memory
            for task in pending:
                task.cancel()

    except Exception as e:
        logger.error(f"Failed to connect to upstream server {server_ws}: {e}")
        # แจ้ง Client ว่าต่อ Server ไม่ติด
        try:
            await client_ws.send_text(json.dumps({"type": "error", "detail": "Cannot connect to STT server"}))
        except:
            pass
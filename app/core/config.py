import os

class Settings:
    # URL ของ Typhoon2-audio ในเครื่อง (Docker ที่พี่รัน port mapping 8100:8000)
    TY2A_BASE_URL: str = os.getenv("TY2A_BASE_URL", "http://localhost:8100")
    # path endpoint ของฝั่ง Typhoon2 (ตาม serve_tts.py ที่เราใช้ตัวอย่าง)
    TY2A_TTS_PATH: str = os.getenv("TY2A_TTS_PATH", "/api/tts")
    TY2A_VOICES_PATH: str = os.getenv("TY2A_VOICES_PATH", "/api/tts/voices")

    # CORS origins (ถ้า front-end เปิดจากที่อื่น ใส่เพิ่มได้)
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

settings = Settings()

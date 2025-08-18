import json
def safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

import math

def yaw_to_quat(theta: float):
    z = math.sin(theta * 0.5)
    w = math.cos(theta * 0.5)
    return {"x": 0.0, "y": 0.0, "z": z, "w": w}

def pose_stamped(x: float, y: float, theta: float, frame_id: str = "map"):
    q = yaw_to_quat(theta or 0.0)
    return {
        "header": {"frame_id": frame_id},
        "pose": {
            "position": {"x": x, "y": y, "z": 0.0},
            "orientation": q,
        },
    }

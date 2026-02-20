"""
Object Memory System for VORA
==============================
Remembers where objects were last found to enable smart search priority.

Features:
- Store object locations with timestamps
- Smart priority: check likely locations first based on object type
- Multi-object tracking
- Description matching for specific object variants

Object Categories (5 official items):
- card: บัตร/การ์ด → likely near: ประตู, กระเป๋า, โต๊ะทำงาน
- coil: ขดลวด → likely near: โต๊ะทำงาน, ชั้นเครื่องมือ
- pen: ปากกา → likely near: โต๊ะทำงาน, สมุด
- pencil: ดินสอ → likely near: โต๊ะทำงาน, สมุด
- wallet: กระเป๋าสตางค์ → likely near: ประตู, โซฟา, โต๊ะทำงาน
"""

import os
import json
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("object_memory")

# Memory file location
MEMORY_FILE = Path(__file__).parent.parent / "data" / "object_memory.json"


@dataclass
class ObjectLocation:
    """Single object location record"""
    object_name: str              # Normalized name (card, pen, etc.)
    display_name: str             # Original Thai/English name used
    location: str                 # Where it was found (e.g., "center_near", "left")
    location_description: str     # Human description (e.g., "บนโต๊ะ ทางซ้าย")
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.8
    image_url: Optional[str] = None
    
    def age_hours(self) -> float:
        """How many hours ago this was recorded"""
        return (time.time() - self.timestamp) / 3600


# Smart priority zones by object type
# Higher weight = check this zone first
OBJECT_PRIORITY_ZONES = {
    "card": {
        "entrance": 3,      # ประตู/ทางเข้า
        "desk": 2,          # โต๊ะทำงาน
        "shelf": 1,         # ชั้นวางของ
    },
    "coil": {
        "desk": 3,          # โต๊ะทำงาน
        "tool_shelf": 3,    # ชั้นเครื่องมือ
        "floor": 1,         # พื้น
    },
    "pen": {
        "desk": 3,          # โต๊ะทำงาน
        "notebook": 2,      # สมุด/กระดาษ
        "floor": 1,         # พื้น (หล่นไป)
    },
    "pencil": {
        "desk": 3,          # โต๊ะทำงาน
        "notebook": 2,      # สมุด
        "floor": 1,         
    },
    "wallet": {
        "entrance": 3,      # ประตู/ทางเข้า (วางไว้ตอนกลับมา)
        "sofa": 2,          # โซฟา
        "desk": 2,          # โต๊ะ
        "floor": 1,
    },
}

# Default priority for unknown objects
DEFAULT_PRIORITY_ZONES = {
    "desk": 2,
    "floor": 1,
    "shelf": 1,
}


class ObjectMemory:
    """
    Object memory system - remembers where objects were found.
    """
    
    def __init__(self):
        self._memory: Dict[str, List[ObjectLocation]] = {}
        self._load()
    
    def _load(self):
        """Load memory from file"""
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for obj_name, records in data.items():
                        self._memory[obj_name] = [
                            ObjectLocation(**r) for r in records
                        ]
                logger.info(f"📚 Loaded object memory: {len(self._memory)} objects")
            except Exception as e:
                logger.warning(f"Failed to load object memory: {e}")
                self._memory = {}
        else:
            logger.info("📚 Object memory: Starting fresh")
    
    def _save(self):
        """Save memory to file"""
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                obj_name: [asdict(loc) for loc in locations]
                for obj_name, locations in self._memory.items()
            }
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save object memory: {e}")
    
    def remember(
        self,
        object_name: str,
        display_name: str,
        location: str,
        location_description: str = "",
        confidence: float = 0.8,
        image_url: Optional[str] = None,
    ):
        """
        Remember where an object was found.
        
        Args:
            object_name: Normalized name (e.g., "pen")
            display_name: Original name used (e.g., "ปากกา")
            location: VLM location code (e.g., "center_near")
            location_description: Human description
            confidence: How confident we are (0-1)
            image_url: URL of captured image
        """
        loc = ObjectLocation(
            object_name=object_name,
            display_name=display_name,
            location=location,
            location_description=location_description,
            confidence=confidence,
            image_url=image_url,
        )
        
        if object_name not in self._memory:
            self._memory[object_name] = []
        
        # Keep only last 10 locations per object
        self._memory[object_name].insert(0, loc)
        self._memory[object_name] = self._memory[object_name][:10]
        
        self._save()
        logger.info(f"📝 Remembered: {display_name} at {location}")
    
    def recall(self, object_name: str) -> Optional[ObjectLocation]:
        """
        Recall the most recent location of an object.
        
        Returns the most recent location record, or None if never seen.
        """
        if object_name not in self._memory or not self._memory[object_name]:
            return None
        return self._memory[object_name][0]
    
    def get_history(self, object_name: str, limit: int = 5) -> List[ObjectLocation]:
        """Get location history for an object"""
        if object_name not in self._memory:
            return []
        return self._memory[object_name][:limit]
    
    def get_priority_zones(self, object_name: str) -> Dict[str, int]:
        """
        Get search priority zones for an object type.
        Higher weight = check first.
        
        Combines:
        1. Known history (if we've found it before, check there first)
        2. Object type heuristics (wallets near doors, pens on desks, etc.)
        """
        priorities = OBJECT_PRIORITY_ZONES.get(object_name, DEFAULT_PRIORITY_ZONES).copy()
        
        # Boost priority for locations where we've found it before
        history = self.get_history(object_name, limit=3)
        for loc in history:
            zone = loc.location.split("_")[0]  # "center_near" → "center"
            # Recent finds get higher boost
            age_penalty = min(loc.age_hours() / 24, 1.0)  # 0-1 over 24 hours
            boost = int(5 * (1 - age_penalty))  # 5 points for very recent, 0 for old
            priorities[zone] = priorities.get(zone, 0) + boost
        
        return priorities
    
    def get_search_hint(self, object_name: str) -> Optional[str]:
        """
        Get a search hint based on memory.
        
        Returns a Thai sentence like:
        "เคยเจอปากกาที่โต๊ะทำงานเมื่อ 2 ชั่วโมงที่แล้ว"
        """
        last = self.recall(object_name)
        if not last:
            return None
        
        age = last.age_hours()
        if age < 1:
            age_text = "เมื่อกี้"
        elif age < 24:
            age_text = f"เมื่อ {int(age)} ชั่วโมงที่แล้ว"
        else:
            days = int(age / 24)
            age_text = f"เมื่อ {days} วันที่แล้ว"
        
        loc_text = last.location_description or last.location
        return f"เคยเจอ{last.display_name}ที่{loc_text} {age_text}"
    
    def get_all_objects(self) -> List[str]:
        """Get list of all remembered object names"""
        return list(self._memory.keys())
    
    def clear(self, object_name: Optional[str] = None):
        """Clear memory for one object or all"""
        if object_name:
            if object_name in self._memory:
                del self._memory[object_name]
        else:
            self._memory = {}
        self._save()


# Global singleton
object_memory = ObjectMemory()

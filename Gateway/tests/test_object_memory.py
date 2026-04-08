"""
Tests for VORA Object Memory System
====================================
Covers: section system, bearing, migration, suspicious-write detection, search hints.

Run: cd Gateway && python -m pytest tests/test_object_memory.py -v
"""

import json
import time
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

# Patch MEMORY_FILE before importing so tests don't touch real data
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp.close()

import gateway.object_memory as om

# Override the file path for all tests
om.MEMORY_FILE = Path(_tmp.name)


# ── Section system ──────────────────────────────────────────────

class TestGetSection:
    def test_quadrant_a(self):
        assert om.get_section(-2.0, 2.0) == "A"

    def test_quadrant_b(self):
        assert om.get_section(1.0, 1.0) == "B"

    def test_quadrant_c(self):
        assert om.get_section(-1.0, -1.0) == "C"

    def test_quadrant_d(self):
        assert om.get_section(2.0, -2.0) == "D"

    def test_origin_goes_to_b(self):
        # x=0.0, y=0.0: x_min=0.0 <= 0.0 < 4.0 and y_min=-4.0 <= 0.0 < 0.0 → D
        # Actually: Section D has y_min=-4.0, y_max=0.0, so y=0.0 is NOT in D
        # Section B has y_min=0.0, y_max=4.0, so y=0.0 IS in B (0.0 <= 0.0 < 4.0)
        assert om.get_section(0.0, 0.0) == "B"

    def test_out_of_bounds(self):
        assert om.get_section(10.0, 10.0) == "?"
        assert om.get_section(-10.0, -10.0) == "?"

    def test_boundary_edge(self):
        # x=-4.0 is the left edge of A and C
        assert om.get_section(-4.0, 0.0) == "A"
        assert om.get_section(-4.0, -0.1) == "C"


class TestGetSectionLabel:
    def test_known_section(self):
        assert om.get_section_label("A") == "ส่วน A"
        assert om.get_section_label("D") == "ส่วน D"

    def test_unknown_section(self):
        assert om.get_section_label("?") == "ส่วน ?"
        assert om.get_section_label("X") == "ส่วน X"


# ── Bearing from VLM location code ─────────────────────────────

class TestLocationToBearing:
    def test_left(self):
        assert om.location_to_bearing("center_left") == 25.0
        assert om.location_to_bearing("top_left") == 25.0
        assert om.location_to_bearing("left") == 25.0

    def test_right(self):
        assert om.location_to_bearing("center_right") == -25.0
        assert om.location_to_bearing("bottom_right") == -25.0

    def test_center(self):
        assert om.location_to_bearing("center") == 0.0
        assert om.location_to_bearing("top_center") == 0.0
        assert om.location_to_bearing("unknown") == 0.0


# ── Migration ───────────────────────────────────────────────────

class TestMigrateRecord:
    def test_old_format_renamed(self):
        old = {
            "object_name": "pen",
            "display_name": "ปากกา",
            "location": "center_left",
            "location_description": "ด้านซ้าย",
            "timestamp": 1700000000.0,
            "confidence": 0.9,
            "image_url": None,
            "robot_x": 1.5,
            "robot_y": -0.8,
            "robot_theta": 0.5,
        }
        migrated = om._migrate_record(old)
        assert "observer_x" in migrated
        assert "observer_y" in migrated
        assert "observer_theta" in migrated
        assert "robot_x" not in migrated
        assert migrated["observer_x"] == 1.5
        assert migrated["observer_y"] == -0.8
        assert migrated["observer_theta"] == 0.5

    def test_fills_new_defaults(self):
        old = {
            "object_name": "pen",
            "display_name": "ปากกา",
            "location": "center_right",
            "location_description": "",
            "timestamp": 1700000000.0,
            "confidence": 0.9,
            "image_url": None,
            "robot_x": 2.0,
            "robot_y": 1.0,
            "robot_theta": 0.0,
        }
        migrated = om._migrate_record(old)
        assert migrated["bearing_deg"] == -25.0  # "right" → -25°
        assert migrated["section"] == "B"  # (2.0, 1.0) → section B
        assert migrated["localization_source"] == ""
        assert migrated["last_verified_at"] == 0.0

    def test_new_format_unchanged(self):
        new = {
            "object_name": "pen",
            "display_name": "ปากกา",
            "location": "center",
            "location_description": "",
            "timestamp": 1700000000.0,
            "confidence": 0.9,
            "image_url": None,
            "observer_x": 1.0,
            "observer_y": 1.0,
            "observer_theta": 0.0,
            "bearing_deg": 0.0,
            "section": "B",
            "localization_source": "amcl",
            "last_verified_at": 1700000001.0,
        }
        migrated = om._migrate_record(new.copy())
        assert migrated["observer_x"] == 1.0
        assert migrated["section"] == "B"


# ── ObjectMemory.remember() ─────────────────────────────────────

class TestRemember:
    def setup_method(self):
        """Clean memory file before each test."""
        om.MEMORY_FILE.write_text("{}", encoding="utf-8")
        self.mem = om.ObjectMemory()

    def test_stores_observer_pose_not_robot(self):
        self.mem.remember(
            object_name="pen",
            display_name="ปากกา",
            location="center_left",
            observer_x=1.5,
            observer_y=-0.8,
            observer_theta=0.5,
            localization_source="amcl",
        )
        loc = self.mem.recall("pen")
        assert loc is not None
        assert loc.observer_x == 1.5
        assert loc.observer_y == -0.8
        assert loc.observer_theta == 0.5
        assert not hasattr(loc, "robot_x") or "robot_x" not in loc.__dict__

    def test_derived_fields_computed(self):
        self.mem.remember(
            object_name="wallet",
            display_name="กระเป๋า",
            location="top_right",
            observer_x=-2.0,
            observer_y=2.0,
            observer_theta=1.0,
            localization_source="dead_reckoning",
        )
        loc = self.mem.recall("wallet")
        assert loc.bearing_deg == -25.0  # "right" → -25°
        assert loc.section == "A"  # (-2.0, 2.0) → section A
        assert loc.localization_source == "dead_reckoning"
        assert loc.last_verified_at > 0

    def test_suspicious_origin_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            self.mem.remember(
                object_name="pen",
                display_name="ปากกา",
                location="center",
                observer_x=0.0,
                observer_y=0.0,
            )
        assert "SUSPICIOUS WRITE" in caplog.text
        assert "origin (0,0)" in caplog.text

    def test_suspicious_duplicate_warning(self, caplog):
        self.mem.remember(
            object_name="pen",
            display_name="ปากกา",
            location="center",
            observer_x=1.5,
            observer_y=-0.8,
        )
        with caplog.at_level(logging.WARNING):
            self.mem.remember(
                object_name="pen",
                display_name="ปากกา",
                location="center",
                observer_x=1.51,
                observer_y=-0.79,
            )
        assert "SUSPICIOUS WRITE" in caplog.text
        assert "same observer pose" in caplog.text


# ── Search hint ─────────────────────────────────────────────────

class TestSearchHint:
    def setup_method(self):
        om.MEMORY_FILE.write_text("{}", encoding="utf-8")
        self.mem = om.ObjectMemory()

    def test_hint_uses_section(self):
        self.mem.remember(
            object_name="pen",
            display_name="ปากกา",
            location="center_left",
            location_description="ด้านซ้าย",
            observer_x=-2.0,
            observer_y=2.0,
            localization_source="amcl",
        )
        hint = self.mem.get_search_hint("pen")
        assert hint is not None
        assert "ส่วน A" in hint
        assert "ด้านซ้าย" in hint

    def test_hint_warns_unreliable_pose(self):
        self.mem.remember(
            object_name="card",
            display_name="บัตร",
            location="center",
            observer_x=1.0,
            observer_y=-1.0,
            localization_source="dead_reckoning",
        )
        hint = self.mem.get_search_hint("card")
        assert "ตำแหน่งอาจไม่แม่น" in hint

    def test_hint_no_warning_for_amcl(self):
        self.mem.remember(
            object_name="pen",
            display_name="ปากกา",
            location="center",
            observer_x=1.0,
            observer_y=1.0,
            localization_source="amcl",
        )
        hint = self.mem.get_search_hint("pen")
        assert "อาจไม่แม่น" not in hint

    def test_no_hint_for_unknown(self):
        assert self.mem.get_search_hint("unknown_thing") is None


# ── JSON roundtrip ──────────────────────────────────────────────

class TestJsonRoundtrip:
    def test_save_load_new_format(self):
        om.MEMORY_FILE.write_text("{}", encoding="utf-8")
        mem1 = om.ObjectMemory()
        mem1.remember(
            object_name="pen",
            display_name="ปากกา",
            location="top_left",
            observer_x=-1.0,
            observer_y=1.0,
            observer_theta=0.3,
            localization_source="amcl",
        )
        # Reload
        mem2 = om.ObjectMemory()
        loc = mem2.recall("pen")
        assert loc is not None
        assert loc.observer_x == -1.0
        assert loc.bearing_deg == 25.0
        assert loc.section == "A"

    def test_load_old_format_migrates(self):
        old_data = {
            "pen": [{
                "object_name": "pen",
                "display_name": "ปากกา",
                "location": "center_right",
                "location_description": "",
                "timestamp": time.time(),
                "confidence": 0.9,
                "image_url": None,
                "robot_x": 2.0,
                "robot_y": -1.0,
                "robot_theta": 0.5,
            }]
        }
        om.MEMORY_FILE.write_text(json.dumps(old_data), encoding="utf-8")
        mem = om.ObjectMemory()
        loc = mem.recall("pen")
        assert loc is not None
        assert loc.observer_x == 2.0
        assert loc.observer_y == -1.0
        assert loc.observer_theta == 0.5
        assert loc.bearing_deg == -25.0
        assert loc.section == "D"
        # Verify saved file has new format
        saved = json.loads(om.MEMORY_FILE.read_text(encoding="utf-8"))
        assert "observer_x" in saved["pen"][0]
        assert "robot_x" not in saved["pen"][0]

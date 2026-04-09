#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings.py - 設定管理 (SettingsManager)
"""

import json
import os
import shutil
from pathlib import Path

from .constants import SETTINGS_FILE


class SettingsManager:
    def __init__(self):
        pats = {}
        for i in range(1, 5):
            pid = f"pat_{i}"
            pats[pid] = {
                "name": f"パターン {i}",
                "pin_condition": [1 if j == i - 1 else 0 for j in range(3)],
                "stages": {}
            }

        self.defaults = {
            "cameras": [
                {"id": "cam_1", "name": "カメラ 1", "index": 0}
            ],
            "gpio": {
                "triggers": [
                    {"id": "trig_1", "name": "トリガー 1", "pin": 22},
                    {"id": "trig_2", "name": "トリガー 2", "pin": 27}
                ],
                "pattern_pins": [
                    {"id": "sel_1", "name": "ピン 1", "pin": 5},
                    {"id": "sel_2", "name": "ピン 2", "pin": 6},
                    {"id": "sel_3", "name": "ピン 3", "pin": 13}
                ],
                "outputs": {"ok": 16, "ng": 20}
            },
            "patterns": pats,
            "pattern_order": ["pat_1", "pat_2", "pat_3", "pat_4"],
            "inference": {
                "threshold": 0.5,
                "max_retries": 5,
                "burst_interval": 0.5,
                "result_display_time": 2.0,
                "model_path": os.path.join(os.path.expanduser("~"), "models/rubber_best.pt"),
                "buzzer_path": "",
                "mode": "inspection",
                "save_skip_in_record": False,
                "preview_fps": 2,
                "ok_output_time": 0.5,
                "ng_output_time": ""
            },
            "storage": {
                "results_dir": os.path.join(os.path.expanduser("~"), "results"),
                "auto_delete_enabled": True,
                "max_results_gb": round(shutil.disk_usage(os.path.expanduser("~")).total / (1024**3), 1),
                "capture_res": "1920x1080",
                "preview_res": "640x480",
                "res_ok": "320x240",
                "res_ng": "1920x1080",
                "res_skip": "320x240",
                "res_record": "1920x1080"
            }
        }
        self.data = self.load_settings()

    def load_settings(self):
        def merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict):
                    a[k] = merge(a.get(k, {}), v)
                else:
                    if k not in a:
                        a[k] = v
            return a

        try:
            if Path(SETTINGS_FILE).exists():
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # 互換性: selectors -> pattern_pins
                    if "gpio" in data and "selectors" in data["gpio"] and "pattern_pins" not in data["gpio"]:
                        data["gpio"]["pattern_pins"] = data["gpio"].pop("selectors")
                    
                    # ユーザー名変更対応: /home/pi を現在のユーザーホームに置換
                    home = os.path.expanduser("~")
                    if "inference" in data and "model_path" in data["inference"]:
                        p = data["inference"]["model_path"]
                        if p.startswith("/home/pi/"):
                            data["inference"]["model_path"] = p.replace("/home/pi", home, 1)
                    if "storage" in data and "results_dir" in data["storage"]:
                        p = data["storage"]["results_dir"]
                        if p.startswith("/home/pi/"):
                            data["storage"]["results_dir"] = p.replace("/home/pi", home, 1)
                        
                    return merge(data, self.defaults.copy())
            return self.defaults.copy()
        except Exception as e:
            print(f"Error loading settings: {e}")
            return self.defaults.copy()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving settings: {e}")

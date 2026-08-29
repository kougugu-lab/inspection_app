#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py - ダイアログウィンドウ
GPIOTestDialog, SettingsDialog
"""

import json
import os
import sys
import time
import copy
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import numpy as np
from pathlib import Path

import cv2
from PIL import Image, ImageTk

# スクリプトを単体で実行する場合に code/ ディレクトリを検索パスに追加する
if __name__ == "__main__" or __package__ is None:
    import sys
    _here = os.path.dirname(os.path.abspath(__file__))
    _code_dir = os.path.dirname(_here)
    if _code_dir not in sys.path:
        sys.path.insert(0, _code_dir)

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT, COLOR_OK, COLOR_NG, COLOR_NG_MUTED, COLOR_WARNING,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE,
    FONT_SET_TAB, FONT_SET_LBL, FONT_SET_VAL, FONT_BTN_LARGE,
    RES_OPTIONS, RES_OPTIONS_PREVIEW, RES_OPTIONS_SAVE,
    VALID_BCM_PINS
)
from .hardware import DigitalInputDevice, OutputDevice
from .widgets import create_card, Tooltip, HelpWindow, configure_modal_toplevel, release_modal_toplevel

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


def detect_available_cameras():
    """OSが認識しているカメラデバイスを探索し、
    [(index_int, display_label_str), ...] のリストを返す。
    ※ UIフリーズやブロッキング、警告ログ多発を防ぐため VideoCapture による同期的接続テストは行わない。
    """
    import sys
    import subprocess
    import os

    devices = []

    if sys.platform.startswith("linux"):
        # Linux (Raspberry Pi 等): /sys/class/video4linux/video*/name を軽量チェック
        v4l_dir = "/sys/class/video4linux"
        if os.path.exists(v4l_dir):
            ignore_keywords = ["codec", "rpivid", "vc4", "media-controller", "bcm2835-isp", "h264", "hevc", "vp8"]
            for entry in sorted(os.listdir(v4l_dir), key=lambda x: int(x.replace("video", "")) if x.replace("video", "").isdigit() else 999):
                if entry.startswith("video"):
                    try:
                        idx = int(entry.replace("video", ""))
                        name_file = os.path.join(v4l_dir, entry, "name")
                        cam_name = f"カメラ {idx}"
                        if os.path.exists(name_file):
                            with open(name_file, "r", encoding="utf-8", errors="ignore") as f:
                                name_text = f.read().strip()
                                if name_text:
                                    cam_name = name_text
                        
                        # 非カメラ（コーデック/デコーダ/ISP等）を除外
                        if any(k in cam_name.lower() for k in ignore_keywords):
                            continue

                        devices.append((idx, f"[{idx}] {cam_name}"))
                    except Exception:
                        pass
    elif sys.platform.startswith("win"):
        # Windows: PowerShell で PnP カメラデバイス名を取得
        names_from_ps = []
        try:
            ps_cmd = 'Get-CimInstance Win32_PnPEntity | Where-Object {$_.PNPClass -eq "Camera" -or $_.PNPClass -eq "Image"} | Select-Object -ExpandProperty Name'
            res = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                names_from_ps = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            pass

        if names_from_ps:
            for idx, d_name in enumerate(names_from_ps):
                devices.append((idx, f"[{idx}] {d_name}"))

    # 万が一なにも取れなかった場合、あるいは標準的なインデックス 0～3 を補完
    existing_indices = {d[0] for d in devices}
    for idx in range(4):
        if idx not in existing_indices:
            devices.append((idx, f"[{idx}] カメラ (インデックス {idx})"))

    devices.sort(key=lambda x: x[0])
    return devices


# ---------------------------------------------------------------------------
# システム日時設定ダイアログ
# ---------------------------------------------------------------------------
class SystemDateTimeDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("本体日時設定 (システムクロック設定)")
        self.geometry("560x490")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.grab_set()

        try:
            configure_modal_toplevel(self)
        except Exception:
            pass

        from datetime import datetime
        now = datetime.now()

        # 最下部ボタンエリア (side=BOTTOM で固定配置することで縦潰れを完全防止)
        f_btns = tk.Frame(self, bg=COLOR_BG_MAIN)
        f_btns.pack(side=tk.BOTTOM, fill=tk.X, pady=20, padx=24)

        def _apply():
            try:
                y = self.v_year.get()
                m = self.v_month.get()
                d = self.v_day.get()
                h = self.v_hour.get()
                mi = self.v_min.get()
                s = self.v_sec.get()
                dt_str = f"{y:04d}-{m:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"
            except Exception as ex:
                messagebox.showerror("入力エラー", f"日時の入力値が不正です:\n{ex}", parent=self)
                return

            if sys.platform.startswith("win"):
                messagebox.showinfo(
                    "日時設定 (Windows)",
                    f"Windows環境のため実際のシステム時刻変更はスキップされました。\n設定指定値: {dt_str}\n(Linux/ラズパイ環境で自動設定コマンドが実行されます)",
                    parent=self
                )
                self.destroy()
                return

            import subprocess
            cmds = [
                ["sudo", "timedatectl", "set-ntp", "false"],
                ["sudo", "timedatectl", "set-time", dt_str],
                ["sudo", "date", "-s", dt_str],
                ["sudo", "hwclock", "-w"]
            ]
            results = []
            success_count = 0
            for cmd in cmds:
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if res.returncode == 0:
                        success_count += 1
                        results.append(f"成功: {' '.join(cmd)}")
                    else:
                        err = res.stderr.strip() or res.stdout.strip()
                        results.append(f"失敗 ({' '.join(cmd)}): {err}")
                except Exception as ex:
                    results.append(f"エラー ({' '.join(cmd)}): {ex}")

            msg = f"日時を [{dt_str}] に設定しました。\n\n【実行詳細】\n" + "\n".join(results)
            if success_count > 0:
                messagebox.showinfo("日時設定完了", msg, parent=self)
                self.destroy()
            else:
                messagebox.showerror("日時設定失敗", msg, parent=self)

        btn_save = tk.Button(
            f_btns, text="日時を本体に反映", font=(FONT_FAMILY, 11, "bold"),
            bg=COLOR_ACCENT, fg="white", relief="flat", padx=20, pady=8,
            cursor="hand2", command=_apply
        )
        btn_save.pack(side=tk.RIGHT, padx=(10, 0))

        btn_cancel = tk.Button(
            f_btns, text="キャンセル", font=(FONT_FAMILY, 11, "bold"),
            bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat", padx=18, pady=8,
            cursor="hand2", command=self.destroy
        )
        btn_cancel.pack(side=tk.RIGHT)

        # ヘッダータイトル & 説明
        tk.Label(
            self, text="ラズパイ本体の日時設定", font=FONT_LARGE,
            bg=COLOR_BG_MAIN, fg=COLOR_ACCENT
        ).pack(pady=(20, 6))

        tk.Label(
            self, text="本体のシステム日付・時刻を設定します。\n(Linux / Raspberry Pi 環境で timedatectl / date が更新されます)",
            font=FONT_SET_VAL, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB, justify="center",
            wraplength=500
        ).pack(pady=(0, 16), padx=20)

        # 入力フレーム
        f_dt = tk.Frame(self, bg=COLOR_BG_PANEL, padx=20, pady=20)
        f_dt.pack(padx=24, fill=tk.X, expand=True)

        font_num = (FONT_FAMILY, 14, "bold")
        font_lbl = (FONT_FAMILY, 12, "bold")

        # 年月日
        f_date = tk.Frame(f_dt, bg=COLOR_BG_PANEL)
        f_date.pack(fill=tk.X, pady=8)
        
        self.v_year = tk.IntVar(value=now.year)
        self.v_month = tk.IntVar(value=now.month)
        self.v_day = tk.IntVar(value=now.day)

        tk.Label(f_date, text="日付:", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=6, anchor="w").pack(side=tk.LEFT)
        sp_y = ttk.Spinbox(f_date, from_=2020, to=2099, increment=1, textvariable=self.v_year, width=6, font=font_num)
        sp_y.pack(side=tk.LEFT, padx=4)
        tk.Label(f_date, text="年", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))

        sp_m = ttk.Spinbox(f_date, from_=1, to=12, increment=1, textvariable=self.v_month, width=4, font=font_num)
        sp_m.pack(side=tk.LEFT, padx=4)
        tk.Label(f_date, text="月", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))

        sp_d = ttk.Spinbox(f_date, from_=1, to=31, increment=1, textvariable=self.v_day, width=4, font=font_num)
        sp_d.pack(side=tk.LEFT, padx=4)
        tk.Label(f_date, text="日", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)

        # 時分秒
        f_time = tk.Frame(f_dt, bg=COLOR_BG_PANEL)
        f_time.pack(fill=tk.X, pady=8)

        self.v_hour = tk.IntVar(value=now.hour)
        self.v_min = tk.IntVar(value=now.minute)
        self.v_sec = tk.IntVar(value=now.second)

        tk.Label(f_time, text="時刻:", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=6, anchor="w").pack(side=tk.LEFT)
        sp_h = ttk.Spinbox(f_time, from_=0, to=23, increment=1, textvariable=self.v_hour, width=4, font=font_num)
        sp_h.pack(side=tk.LEFT, padx=4)
        tk.Label(f_time, text="時", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))

        sp_mi = ttk.Spinbox(f_time, from_=0, to=59, increment=1, textvariable=self.v_min, width=4, font=font_num)
        sp_mi.pack(side=tk.LEFT, padx=4)
        tk.Label(f_time, text="分", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 10))

        sp_s = ttk.Spinbox(f_time, from_=0, to=59, increment=1, textvariable=self.v_sec, width=4, font=font_num)
        sp_s.pack(side=tk.LEFT, padx=4)
        tk.Label(f_time, text="秒", font=font_lbl, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)

        # 全Spinboxの安全停止ハンドラ
        for sp in [sp_y, sp_m, sp_d, sp_h, sp_mi, sp_s]:
            def _stop_ttk_sp(event=None, widget=sp):
                try:
                    rep = widget.tk.call('set', '::ttk::spinbox::Repeater')
                    if rep: widget.tk.call('after', 'cancel', rep)
                except Exception:
                    pass
            sp.bind("<ButtonRelease-1>", _stop_ttk_sp, add="+")
            sp.bind("<Leave>", _stop_ttk_sp, add="+")
            sp.bind("<FocusOut>", _stop_ttk_sp, add="+")

        def _set_current():
            n = datetime.now()
            self.v_year.set(n.year)
            self.v_month.set(n.month)
            self.v_day.set(n.day)
            self.v_hour.set(n.hour)
            self.v_min.set(n.minute)
            self.v_sec.set(n.second)

        btn_now = tk.Button(
            f_dt, text="現在端末の時刻をセット", font=(FONT_FAMILY, 11, "bold"),
            bg=COLOR_BG_INPUT, fg=COLOR_ACCENT, relief="flat", padx=16, pady=6,
            cursor="hand2", command=_set_current
        )
        btn_now.pack(pady=(14, 4))


# ---------------------------------------------------------------------------
# GPIO テストダイアログ
# ---------------------------------------------------------------------------
class GPIOTestDialog(tk.Toplevel):
    def __init__(self, parent, gpio_settings):
        super().__init__(parent)
        self.title("GPIO 入出力テスト")
        self.geometry("600x600")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.grab_set()

        self.gpio_settings = gpio_settings
        self.running = True
        self.inputs = {}
        self.outputs = {}

        tk.Label(self, text="GPIO 入出力テスト", font=FONT_LARGE,
                 bg=COLOR_BG_MAIN, fg=COLOR_ACCENT).pack(pady=20)

        # --- スクロール可能なエリア ---
        container = tk.Frame(self, bg=COLOR_BG_MAIN)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas = tk.Canvas(container, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_to_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_from_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_to_mousewheel)
        canvas.bind("<Leave>", _unbind_from_mousewheel)

        # ハードウェア初期化
        self.setup_test_hardware()

        # UI構築
        self.ui_inputs = {}

        f_in = tk.LabelFrame(scrollable_frame, text="入力テスト",
                             font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                             fg=COLOR_TEXT_MAIN, padx=20, pady=20)
        f_in.pack(fill=tk.X, padx=20, pady=10)

        for k, name in self.input_names.items():
            row = tk.Frame(f_in, bg=COLOR_BG_PANEL)
            row.pack(fill=tk.X, pady=5)
            tk.Label(row, text=name, font=FONT_SET_VAL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_MAIN, width=30, anchor="w").pack(side=tk.LEFT)
            lbl_st = tk.Label(row, text="OFF", font=FONT_SET_VAL,
                              bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB, width=10)
            lbl_st.pack(side=tk.LEFT, padx=10)
            self.ui_inputs[k] = lbl_st

        f_out = tk.LabelFrame(scrollable_frame, text="出力テスト",
                              font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                              fg=COLOR_TEXT_MAIN, padx=20, pady=20)
        f_out.pack(fill=tk.X, padx=20, pady=10)

        self.output_state_ok = False
        self.output_state_ng = False

        def toggle_out(key, btn):
            if key == "ok":
                self.output_state_ok = not self.output_state_ok
                state = self.output_state_ok
            else:
                self.output_state_ng = not self.output_state_ng
                state = self.output_state_ng

            if key in self.outputs:
                if state:
                    self.outputs[key].on()
                    btn.config(text=f"{key.upper()}出力 (ON)",
                               bg=COLOR_WARNING, fg="black")
                else:
                    self.outputs[key].off()
                    btn.config(text=f"{key.upper()}出力 (OFF)",
                               bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)

        btn_f = tk.Frame(f_out, bg=COLOR_BG_PANEL)
        btn_f.pack(fill=tk.X, pady=(0, 10))

        btn_ok = tk.Button(btn_f, text="OK出力 (OFF)", font=FONT_BTN_LARGE,
                           bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                           relief="flat", width=15)
        btn_ok.pack(side=tk.LEFT, padx=10)
        btn_ok.config(command=lambda: toggle_out("ok", btn_ok))

        btn_ng = tk.Button(btn_f, text="NG出力 (OFF)", font=FONT_BTN_LARGE,
                           bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                           relief="flat", width=15)
        btn_ng.pack(side=tk.LEFT, padx=10)
        btn_ng.config(command=lambda: toggle_out("ng", btn_ng))

        tk.Label(f_out, text="(※クリックでON/OFFが切り替わります)",
                 font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                 fg=COLOR_TEXT_SUB).pack(anchor="w", padx=10)

        tk.Button(self, text="閉じる", font=FONT_BOLD, bg="#546E7A",
                  fg="white", relief="flat", height=2,
                  command=self.close_test).pack(fill=tk.X, padx=20, pady=10)

        self.protocol("WM_DELETE_WINDOW", self.close_test)
        self.update_inputs()

    def setup_test_hardware(self):
        self.input_names = {}
        try:
            for t in self.gpio_settings["triggers"]:
                self.inputs[t["id"]] = DigitalInputDevice(t["pin"], pull_up=True)
                self.input_names[t["id"]] = f"トリガー: {t['name']} (ピン:{t['pin']})"
            for s in self.gpio_settings.get("pattern_pins", []):
                self.inputs[s["id"]] = DigitalInputDevice(s["pin"], pull_up=True)
                self.input_names[s["id"]] = f"パターンピン: {s['name']} (ピン:{s['pin']})"
            reset_pin = self.gpio_settings.get("reset_pin")
            if reset_pin:
                try:
                    r_num = int(reset_pin)
                    if r_num > 0:
                        self.inputs["reset"] = DigitalInputDevice(r_num, pull_up=True)
                        self.input_names["reset"] = f"NGリセット入力 (ピン:{r_num})"
                except Exception:
                    pass
            self.outputs["ok"] = OutputDevice(self.gpio_settings["outputs"]["ok"])
            self.outputs["ng"] = OutputDevice(self.gpio_settings["outputs"]["ng"])
        except Exception as e:
            import traceback
            print(f"GPIO Init Error in Test: {e}")
            traceback.print_exc()

    def update_inputs(self):
        if not self.running or not self.winfo_exists():
            return
        for k, dev in self.inputs.items():
            if k in self.ui_inputs:
                st = dev.is_active
                lbl = self.ui_inputs[k]
                if st:
                    lbl.config(text="ON", bg=COLOR_ACCENT, fg="black")
                else:
                    lbl.config(text="OFF", bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
        self.after(100, self.update_inputs)

    def set_output(self, key, state, btn):
        pass  # toggle_out に統合済み

    def close_test(self):
        self.running = False
        for d in self.inputs.values():
            d.close()
        for d in self.outputs.values():
            d.close()
        if hasattr(self.master, "app_instance"):
            self.master.app_instance.setup_hardware() # type: ignore
        self.destroy()

# ---------------------------------------------------------------------------
# 設定ダイアログ
# ---------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings, on_close_callback):
        super().__init__(parent)
        self.settings = settings
        self.on_close_callback = on_close_callback
        self.title("詳細設定")
        self.geometry("1400x900")
        self.configure(bg=COLOR_BG_MAIN)
        self.temp_data = json.loads(json.dumps(self.settings.data))
        self._sync_pattern_conditions()
        self.has_changes = False
        self.model_classes = self._get_model_classes()
        self._scan_status_var = tk.StringVar(value="")
        
        # 設定表示中はメイン画面のプレビューを一時停止して負荷を軽減 (Raspi 5向け)
        if hasattr(self.master, "app_instance"):
            self.master.app_instance.preview_paused = True

        # UI要素のプレースホルダ
        self.active_entry = (None, None) 
        self.pin_widgets = {}
        self.input_pins = {}
        self.map_labels = {}
        self.trig_scroll = tk.Frame() # type: ignore
        self.trig_list_f = tk.Frame()     # type: ignore
        self.sel_list_f = tk.Frame()      # type: ignore
        self.lbl_gpio_status = tk.Label() # type: ignore
        self.cam_body = tk.Frame()  # type: ignore
        self.pat_body = tk.Frame()  # type: ignore
        self.lb_pat = tk.Listbox()  # type: ignore
        
        self.v_ok = tk.IntVar(value=self.temp_data["gpio"]["outputs"]["ok"])
        self.v_ng = tk.IntVar(value=self.temp_data["gpio"]["outputs"]["ng"])
        reset_val = self.temp_data["gpio"].get("reset_pin", 23)
        self.v_reset_pin = tk.StringVar(value=str(reset_val if reset_val is not None else 23))

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TNotebook", background=COLOR_BG_MAIN, borderwidth=0)
        style.configure("TNotebook.Tab", background=COLOR_BG_PANEL,
                        foreground=COLOR_TEXT_MAIN, font=FONT_SET_TAB,
                        padding=[20, 10], focuscolor=COLOR_BG_MAIN)
        style.map("TNotebook.Tab",
                  background=[("selected", COLOR_ACCENT)],
                  foreground=[("selected", "black")])

        btn_f = tk.Frame(self, pady=20, bg=COLOR_BG_MAIN)
        btn_f.pack(side=tk.BOTTOM, fill=tk.X, padx=20)
        
        self.btn_save = tk.Button(btn_f, text="保存して閉じる", font=FONT_BOLD, bg=COLOR_BG_INPUT,
                                  fg="white", relief="flat", width=22,
                                  command=self.save_and_close)
        self.btn_save.pack(side=tk.RIGHT, padx=5)
        
        tk.Button(btn_f, text="キャンセル", font=FONT_BOLD, bg="#546E7A",
                  fg="white", relief="flat", width=10,
                  command=self.on_cancel).pack(side=tk.RIGHT, padx=5)

        nb = ttk.Notebook(self)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=20, pady=20)

        self.t_cam = tk.Frame(nb, bg=COLOR_BG_MAIN)
        nb.add(self.t_cam, text=" カメラ ")
        self.t_gpio = tk.Frame(nb, bg=COLOR_BG_MAIN)
        nb.add(self.t_gpio, text=" GPIOピン ")
        self.t_pat = tk.Frame(nb, bg=COLOR_BG_MAIN)
        nb.add(self.t_pat, text=" パターン ")
        self.t_res = tk.Frame(nb, bg=COLOR_BG_MAIN)
        nb.add(self.t_res, text=" 画素数 ")
        self.t_sys = tk.Frame(nb, bg=COLOR_BG_MAIN)
        nb.add(self.t_sys, text=" システム ")

        self.setup_cam()
        self.setup_gpio()
        self.setup_pat()
        self.setup_res()
        self.setup_sys()

        def _on_tab_changed(event):
            selected_tab = nb.select()
            # パターンタブが選択されたらドロップダウンを最新のクラス一覧で再描画
            if selected_tab == str(self.t_pat):
                if hasattr(self, "lb_pat") and self.lb_pat.winfo_exists():
                    self.on_pat_sel(None)

        nb.bind("<<NotebookTabChanged>>", _on_tab_changed)

        btn_help = tk.Button(btn_f, text="ヘルプ", font=FONT_SET_LBL,
                             bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                             relief="flat", command=self.show_settings_help)
        btn_help.pack(side=tk.LEFT, padx=20)

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        
        # Combobox のドロップダウンリストのフォントを大きく設定
        self.option_add("*TCombobox*Listbox.font", FONT_SET_VAL)

        # Linux/Raspberry Pi (Wayland): 表示完了後に grab（未表示時の grab はクリック不能の原因）
        configure_modal_toplevel(self, parent)

    def on_cancel(self):
        """キャンセル時やウィンドウを閉じた際もプレビュー再開とテスト出力停止を保証する"""
        release_modal_toplevel(self)
        if hasattr(self, "_live_preview_win") and self._live_preview_win.winfo_exists():
            self._live_preview_win.destroy()
        if hasattr(self.master, "app_instance"):
            app = self.master.app_instance
            if hasattr(app, "reset_test_outputs"):
                app.reset_test_outputs()
        if hasattr(self, "_test_devices"):
            for dev in self._test_devices.values():
                try:
                    dev.off()
                except Exception:
                    pass
            self._test_devices.clear()
        if self.on_close_callback:
            self.on_close_callback()
        
        # プレビュー再開
        if hasattr(self.master, "app_instance"):
            self.master.app_instance.preview_paused = False

        self.destroy()

    def show_settings_help(self):
        help_data = {
            "1. カメラ設定": "【概要】使用するUSBカメラの接続と名前付けを行います。\n"
                           "・インデックス: カメラの識別番号です。\n"
                           "・表示名: メイン画面や履歴で表示されるカメラの名称です。\n"
                           "・カメラ検索: 接続されているカメラを自動で探し、リストへ追加・自動割り当てします。\n"
                           "・テストボタン: 現在のインデックスで正常に映るか、ライブ映像で確認できます。",
            "2. GPIOピン設定": "【概要】Raspberry PiのGPIOピンへの配線設定です。\n"
                            "・トリガー: 検査を起動する入力ピンです。リスト上から順に入力待ちとなり、順番通りに入力された場合のみ有効です。\n"
                            "・パターン判定ピン: どの検査パターンを使うかをピンのON/OFFで決めます。\n"
                            "・出力(OK/NG): 判定結果を外部装置（SiO等）へ送る出力ピンです。\n"
                            "・40Pin Map: Raspberry Piの配線図を参照できます。クリックでBCM番号を入力できます。",
            "3. パターン設定": "【概要】判定ピンの状態に応じ、AIが「合格」とする条件を定義します。\n"
                           "・名称: 何の検査か分かりやすい名前を付けます。\n"
                           "・ピン条件: 判定ピンがどのON/OFF状態のときにこのパターンを有効にするかを選びます。\n"
                           "・判定条件: 各トリガー・各カメラごとに条件を複数設定できます。全ての条件を満たせばOKです。\n"
                           "  - 数字(例:2)=その個数ちょうど検出でOK / 0=未検出でOK\n"
                           "  - 対象クラスを空欄にすると全検出物の合計個数で判定します。",
            "4. 保存・画素数設定": "【概要】画像の質や保存先、保存ルールを決めます。\n"
                         "・撮影解像度: カメラから読み出す際の土台のサイズです。大きいほどAIの精度が上がる可能性がありますが、遅くなります。\n"
                         "・各判定画像の保存サイズ: 保存時の大きさを決めます。「保存しない」を選ぶと画像が残りません。",
            "5. システム最適化": "【概要】AIの挙動や画面表示、タイマーの微調整です。\n"
                            "・AIモデルのパス: 推論に使用する学習済みモデルのパスを指定します。NCNN形式の場合、必ずフォルダ名の末尾を `.ncnn` にしてください（例: `best.ncnn`）。\n"
                            "・結果出力先: ログ、CSV、画像一式を保存する親フォルダの場所を絶対パスで指定します。\n"
                            "・判定しきい値: AIの自信がこの数値(0.0~1.0)以上なら「検出した」とみなします。\n"
                            "・重複判定しきい値: 検出した枠同士の重なり具合（重複度）の基準値です。値を下げると重なりが小さくても同一部品と判定し、検出枠をまとめます。\n"
                            "・最大リトライ: 1回のトリガーで何回まで撮り直すか。撮影モードではこの回数分を全て保存します。\n"
                            "・結果表示時間: 判定後、その画像を画面に表示し続ける秒数です。\n"
                            "・OK/NG出力時間: 信号を何秒間出し続けるかです。NGを空欄にするか「ブザー停止まで保持」チェックボックスをONにすると停止ボタンが押されるまで保持します。\n"
                            "・ブザー音パス: 判定時に鳴らす音声ファイルの場所を指定します。\n"
                            "・自動削除有効: 容量上限を超えた際、古い画像から順に自動削除します。CSVログは削除されません。\n"
                            "・最大容量上限: 指定したGB数を超えると削除を開始します。デフォルトはディスクの全容量です。\n"
                            "　設定値に関わらず、ディスク全体の空き容量が1GBを切ると強制的に古い画像を削除して空きを作ります。\n"
                            "生産ライン同期設定：生産ラインで検査結果を送るための外部装置やPLCと連携する設定です。\n"
                            "・コミット番号を0.5刻みで進める：ドアラインの時にはONにして1コミットで2回トリガーが入ることに対応します。\n"
                            "・仕様情報遅延サイクル数：GPIOピンから仕様情報を取得し、次の検査結果に同期するまでのサイクル数です。\n"
                            "　近くにCRTがなくてもパターン情報を一時保存して、車が来たタイミングであとから呼び出せます。\n",
        }
        HelpWindow(self, "詳細設定 操作ガイド", help_data)

    def _load_model_classes_for_path(self, path=None, show_feedback=True):
        """指定パスのモデルをロードしてクラス一覧を更新し、UIに反映する"""
        classes = [""]
        if path is None:
            path = self.temp_data.get("inference", {}).get("model_path", "")

        path = str(path).strip()
        if not path or not os.path.exists(path):
            self.model_classes = classes
            if show_feedback and hasattr(self, "lbl_model_status") and self.lbl_model_status.winfo_exists():
                self.lbl_model_status.config(text="モデル未指定またはファイル/フォルダが存在しません", fg=COLOR_TEXT_SUB)
            return classes

        if not YOLO_AVAILABLE:
            if show_feedback and hasattr(self, "lbl_model_status") and self.lbl_model_status.winfo_exists():
                self.lbl_model_status.config(text="YOLO (ultralytics) が利用できないためクラス情報を取得できません", fg=COLOR_WARNING)
            return classes

        try:
            is_ncnn = os.path.isdir(path) or path.endswith("_ncnn_model")
            model = YOLO(path, task="detect") if is_ncnn else YOLO(path)
            names = getattr(model, 'names', {})
            if names:
                classes += sorted(list(names.values()))
            self.model_classes = classes

            # --- ウォームアップ推論の実行 ---
            try:
                import numpy as np
                dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = model.predict(dummy_img, verbose=False)
            except Exception:
                pass
            
            cls_count = len(classes) - 1
            sample_str = ", ".join(classes[1:6])
            if len(classes) > 6:
                sample_str += "..."
            status_text = f"モデルロード＆ウォームアップ完了 (検出クラス: {cls_count}種類 [{sample_str}])"
            
            if show_feedback and hasattr(self, "lbl_model_status") and self.lbl_model_status.winfo_exists():
                self.lbl_model_status.config(text=status_text, fg=COLOR_OK)

            # パターン画面のドロップダウンを即時反映
            if hasattr(self, "lb_pat") and self.lb_pat.winfo_exists():
                self.on_pat_sel(None)

            return classes
        except Exception as e:
            self.model_classes = classes
            if show_feedback and hasattr(self, "lbl_model_status") and self.lbl_model_status.winfo_exists():
                self.lbl_model_status.config(text=f"モデルロード失敗: {e}", fg=COLOR_NG)
            return classes

    def _get_model_classes(self):
        return self._load_model_classes_for_path(show_feedback=False)

    def _entry(self, parent, var, width=None, key_path=None):
        ent = tk.Entry(parent, textvariable=var, font=FONT_SET_VAL,
                        width=width, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                        insertbackground="white", relief="flat")
        if key_path:
            def _trace(*args):
                self._mark_changed()
            var.trace_add("write", _trace)
        return ent

    def _spinbox(self, parent, var, from_, to, increment=1, width=6, key_path=None):
        sb = tk.Spinbox(parent, from_=from_, to=to, increment=increment, textvariable=var,
                        font=FONT_SET_VAL, width=width, bg=COLOR_BG_INPUT, fg="white", 
                        buttonbackground="#78909C", bd=1, relief="solid",
                        repeatdelay=0, repeatinterval=0)
        
        # ラズパイ環境での長押しタイマー暴走を防止する安全ハンドラ
        def _stop_repeat(event=None):
            try:
                rep_id = sb.tk.call('set', '::tk::spinbox::Repeater')
                if rep_id:
                    sb.tk.call('after', 'cancel', rep_id)
            except Exception:
                pass
        sb.bind("<ButtonRelease-1>", _stop_repeat, add="+")
        sb.bind("<Leave>", _stop_repeat, add="+")
        sb.bind("<FocusOut>", _stop_repeat, add="+")

        if key_path:
            def _trace(*args):
                self._mark_changed()
            var.trace_add("write", _trace)
        return sb

    def create_scrollable_panel(self, parent):
        canvas = tk.Canvas(parent, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            # マスコミなど他のウィジェット上でも、このキャンバスが属するタブが
            # 現在アクティブならスクロール実行
            if not self.winfo_exists(): return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # キャンバスに入った時だけMouseWheelをこのキャンバスに束縛する
        def _bind_mouse(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _unbind_mouse(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mouse)
        canvas.bind("<Leave>", _unbind_mouse)

        return scrollable_frame

        return scrollable_frame

    # ---- カメラタブ ----
    def setup_cam(self):
        # 初回表示時にカメラ一覧を自動検出・取得
        self.available_cams = detect_available_cameras()

        outer, inner = create_card(self.t_cam, "カメラ設定 (1-4台)")
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.cam_body = tk.Frame(inner, bg=COLOR_BG_PANEL)
        self.cam_body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        f_bottom = tk.Frame(inner, bg=COLOR_BG_PANEL)
        f_bottom.pack(fill=tk.X, pady=(0, 10))
        btn_add = tk.Button(f_bottom, text="+ カメラ追加", font=FONT_BTN_LARGE,
                  bg=COLOR_ACCENT, fg="black", relief="flat",
                  command=self.add_cam)
        btn_add.pack(side=tk.LEFT)
        Tooltip(btn_add, "新しいカメラ設定を追加します。")
        # 自動検出ボタン
        btn_scan = tk.Button(f_bottom, text="接続カメラを再スキャン", font=FONT_BTN_LARGE,
                  bg="#546E7A", fg="white", relief="flat",
                  command=self.scan_cameras)
        btn_scan.pack(side=tk.LEFT, padx=(10, 0))
        Tooltip(btn_scan, "現在PC/ラズパイに接続されている使用可能なカメラ機器を自動認識してプルダウンの選択肢を更新します")
        tk.Label(f_bottom, textvariable=self._scan_status_var, font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_WARNING).pack(side=tk.LEFT, padx=15)
        self.refresh_cam()

    def refresh_cam(self):
        for w in self.cam_body.winfo_children():
            w.destroy()

        if not hasattr(self, "available_cams") or not self.available_cams:
            self.available_cams = detect_available_cameras()

        # ドロップダウン用表示文字列リスト
        cam_options = [dev[1] for dev in self.available_cams]

        for i, c in enumerate(self.temp_data["cameras"]):
            def _create_cam_row(idx=i, cam_obj=c):
                f = tk.LabelFrame(self.cam_body, text=f"カメラ {idx+1}",
                                  font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                                  fg=COLOR_TEXT_SUB, padx=10, pady=10,
                                  relief="solid", bd=1)
                f.pack(fill=tk.X, pady=5)
                l_name = tk.Label(f, text="表示名:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL,
                         fg=COLOR_TEXT_MAIN)
                l_name.grid(row=0, column=0)
                Tooltip(l_name, "メイン画面やログ、画像ファイル名に使用されるカメラの名称です。")
                vn = tk.StringVar(value=cam_obj["name"])
                e_name = self._entry(f, vn, key_path=f"cameras.{idx}.name")
                e_name.grid(row=0, column=1, padx=10)

                l_idx = tk.Label(f, text="使用カメラ:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL,
                         fg=COLOR_TEXT_MAIN)
                l_idx.grid(row=0, column=2)
                Tooltip(l_idx, "システムに接続されているカメラ機器を選択します。")

                curr_idx = cam_obj.get("index", 0)
                # 現在のインデックスに対応する表示文字列を探す
                init_val = f"[{curr_idx}] カメラ (インデックス {curr_idx})"
                for c_idx, c_label in self.available_cams:
                    if c_idx == curr_idx:
                        init_val = c_label
                        break

                vi = tk.StringVar(value=init_val)
                cb_dev = ttk.Combobox(f, textvariable=vi, values=cam_options, font=FONT_SET_VAL, width=28)
                cb_dev.grid(row=0, column=3, padx=10)

                def _upd_inner(v_n=vn, v_i=vi):
                    sel_text = v_i.get().strip()
                    # インデックスを文字列 "[N]" から抽出
                    parsed_idx = curr_idx
                    if sel_text.startswith("[") and "]" in sel_text:
                        try:
                            parsed_idx = int(sel_text[1:sel_text.index("]")])
                        except ValueError:
                            pass
                    else:
                        try:
                            parsed_idx = int(sel_text)
                        except ValueError:
                            pass
                    self.temp_data["cameras"][idx].update({
                        "name": v_n.get(),
                        "index": parsed_idx,
                        "device_name": sel_text
                    })
                    self._mark_changed()

                vn.trace_add("write", lambda *a: _upd_inner())
                vi.trace_add("write", lambda *a: _upd_inner())

                if len(self.temp_data["cameras"]) > 1:
                    tk.Button(f, text="削除", font=FONT_BTN_LARGE, bg=COLOR_NG_MUTED,
                              fg="white", relief="flat",
                              command=lambda: self.del_cam(idx)).grid(row=0, column=4, padx=10)

                tk.Button(f, text="テスト", font=FONT_BTN_LARGE, bg=COLOR_ACCENT,
                          fg="black", relief="flat",
                          command=lambda: self.test_camera(idx)).grid(row=0, column=5, padx=10)
            
            _create_cam_row()

    def test_camera(self, idx):
        c_idx_str = self.temp_data["cameras"][idx].get("index", 0)
        try:
            c_idx = int(c_idx_str)
        except ValueError:
            messagebox.showerror("エラー", "正しいカメラインデックスを選択してください。")
            return
        test_win = tk.Toplevel(self)
        test_win.title(f"カメラテスト (インデックス: {c_idx})")
        test_win.geometry("640x480")
        test_win.transient(self)
        test_win.grab_set()
        lbl = tk.Label(test_win, bg="black")
        lbl.pack(fill=tk.BOTH, expand=True)
        cap = cv2.VideoCapture(c_idx)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            messagebox.showerror("エラー", f"カメラ (インデックス: {c_idx}) を開けませんでした。")
            test_win.destroy()
            return

        def update_frame():
            if not test_win.winfo_exists():
                cap.release()
                return
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                img = img.resize((640, 480))
                photo = ImageTk.PhotoImage(image=img)
                lbl.config(image=photo)
                lbl.image = photo
            else:
                lbl.config(text="フレームを取得できません", fg="white")
            test_win.after(30, update_frame)

        update_frame()

    def add_cam(self):
        if len(self.temp_data["cameras"]) < 4:
            next_num = len(self.temp_data["cameras"]) + 1
            # 次の空いているインデックスを探す
            used_indices = {c.get("index") for c in self.temp_data["cameras"]}
            next_idx = 0
            for c_idx, _ in self.available_cams if hasattr(self, "available_cams") else []:
                if c_idx not in used_indices:
                    next_idx = c_idx
                    break
            self.temp_data["cameras"].append({
                "id": f"cam_{int(time.time())}",
                "name": f"カメラ {next_num}",
                "index": next_idx
            })
            self.refresh_cam()
            self._mark_changed()

    def del_cam(self, idx):
        self.temp_data["cameras"].pop(idx)
        self.refresh_cam()
        self._mark_changed()

    def scan_cameras(self):
        """バックグラウンドでカメラ機器を探索し、リストを自動更新する"""
        import threading
        self._scan_status_var.set("スキャン中...")

        def _do_scan():
            cams = detect_available_cameras()
            self.after(0, lambda: _on_found(cams))

        def _on_found(cams):
            if not self.winfo_exists():
                return
            self.available_cams = cams
            found_str = ", ".join([f"[{c[0]}]" for c in cams])
            self._scan_status_var.set(f"検出機器: {found_str if cams else 'なし'}")
            self.refresh_cam()

        threading.Thread(target=_do_scan, daemon=True).start()

        threading.Thread(target=_do_scan, daemon=True).start()

    # ---- 変更検知 ----
    def _mark_changed(self, *args):
        """設定に変更があった場合のみ保存ボタンの色を緑に変え、テキストを更新する"""
        if not self.has_changes:
            self.has_changes = True
            if hasattr(self, "btn_save") and self.btn_save.winfo_exists():
                self.btn_save.config(bg=COLOR_OK, fg="black", text="変更を適用して保存")

    # ---- ライブしきい値プレビュー ----
    def _update_threshold_preview(self, threshold: float, recursive=True):
        """
        現在フォーカスされているカメラの最新フレームに、
        指定しきい値でのYOLO検出結果をオーバーレイして表示する（ライブプレビュー）。
        YOLO が使えない場合は何もしない。
        """
        if not self.winfo_exists():
            return
        if not YOLO_AVAILABLE:
            return
        app = getattr(self.master, "app_instance", None)
        if app is None:
            return
        model = getattr(app, "model", None)
        if model is None:
            return
        # NOTE: 設定画面ではカメラを一時解放(caps={})している場合があるが、
        # app.last_frames に最新フレームが残っていればプレビューは可能。
        
        # app.last_frames から最新のキャプチャ済みフレームを取得（同時アクセスを避ける）
        last_frames = getattr(app, "last_frames", {})
        if not last_frames:
            return  # まだ1枚もキャプチャされていない場合は中止
        
        cid = next(iter(last_frames.keys()), None)
        frame = last_frames.get(cid)
        if frame is None or not isinstance(frame, np.ndarray):
            # フレームがまだない場合、または不正なデータの場合は中止
            return

        # YOLO 推論 (別スレッドだと UI更新が難しいのでここでは推論を短時間だけ実行)
        def _infer():
            try:
                # コピーしたフレームを渡す（スレッドセーフ対策）
                results = model(frame.copy(), conf=threshold, verbose=False)
                if not results:
                    return
                overlay = results[0].plot()
                overlay_rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(overlay_rgb)
                img.thumbnail((640, 480))
                photo = ImageTk.PhotoImage(image=img)

                def _show():
                    if not self.winfo_exists():
                        return
                    if hasattr(self, "_live_preview_win") and self._live_preview_win.winfo_exists():
                        self._live_lbl.config(image=photo)
                        self._live_lbl.image = photo
                        # 動画化：100ms後に再帰的に自分を呼ぶ（ウィンドウが残っていれば）
                        self.after(100, lambda: self._update_threshold_preview(threshold, recursive=True))
                    elif not recursive:
                        # ウィンドウがない、かつ初回呼び出し（recursive=False）の場合のみ新規作成
                        win = tk.Toplevel(self)
                        win.title(f"ライブプレビュー (しきい値: {threshold:.2f})")
                        win.geometry("660x510")
                        win.transient(self)
                        self._live_preview_win = win
                        self._live_lbl = tk.Label(win, bg="black")
                        self._live_lbl.pack(fill=tk.BOTH, expand=True)
                        self._live_lbl.config(image=photo)
                        self._live_lbl.image = photo
                        # 継続
                        self.after(100, lambda: self._update_threshold_preview(threshold, recursive=True))
                    # ウィンドウが閉じられた状態で再帰呼び出しが来た場合は、何もしない（停止）
                self.after(0, _show)
            except Exception as e:
                import traceback
                traceback.print_exc()
                if not recursive:
                    self.after(0, lambda: messagebox.showerror("ライブプレビューエラー", f"推論実行中にエラーが発生しました:\n{e}", parent=self))
        threading.Thread(target=_infer, daemon=True).start()


    # ---- GPIOタブ (リニューアル版) ----
    def setup_gpio(self):
        # 内部管理用
        self.active_entry = (None, None) # 現在フォーカスされている入力欄 (Entry, StringVar)
        self.pin_widgets = {}     # Pin番号 -> (インジケータ等) のマップ
        self.input_pins = {}      # ID -> hardware.InputDevice

        # コンテナ
        main_f = tk.Frame(self.t_gpio, bg=COLOR_BG_MAIN)
        main_f.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 1. 左カラム: 入力設定
        col_left = tk.Frame(main_f, bg=COLOR_BG_MAIN)
        col_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # 2. 中央カラム: ピンマップ
        col_mid = tk.Frame(main_f, bg=COLOR_BG_MAIN)
        col_mid.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        # 3. 右カラム: 出力設定・テスト
        col_right = tk.Frame(main_f, bg=COLOR_BG_MAIN)
        col_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # --- 左カラム内容 ---
        # トリガー設定
        self.trig_scroll = self.create_scrollable_panel(col_left)
        outer_t, inner_t = create_card(self.trig_scroll, "トリガー入力")
        outer_t.pack(fill=tk.X, pady=(0, 10))
        self.trig_list_f = tk.Frame(inner_t, bg=COLOR_BG_PANEL)
        self.trig_list_f.pack(fill=tk.X)
        btn_add_t = tk.Button(inner_t, text="+ 追加", font=FONT_BTN_LARGE, bg=COLOR_ACCENT, fg="black", relief="flat", command=self.add_trig)
        btn_add_t.pack(anchor="e", pady=5)
        Tooltip(btn_add_t, "新しいトリガー入力ピンを追加します。")

        # 判定ピン設定
        outer_s, inner_s = create_card(self.trig_scroll, "パターン切替")
        outer_s.pack(fill=tk.X, pady=10)
        self.sel_list_f = tk.Frame(inner_s, bg=COLOR_BG_PANEL)
        self.sel_list_f.pack(fill=tk.X)
        btn_add_s = tk.Button(inner_s, text="+ 追加", font=FONT_BTN_LARGE, bg=COLOR_ACCENT, fg="black", relief="flat", command=self.add_sel_pin)
        btn_add_s.pack(anchor="e", pady=5)
        Tooltip(btn_add_s, "パターンを切り替えるための入力ピンを追加します。")

        # NGリセット入力設定
        outer_r, inner_r = create_card(self.trig_scroll, "NGリセット入力")
        outer_r.pack(fill=tk.X, pady=10)
        f_reset = tk.Frame(inner_r, bg=COLOR_BG_PANEL)
        f_reset.pack(fill=tk.X, pady=5)
        
        # 状態表示(LED) - 入力側は左端に配置
        self.led_reset = tk.Canvas(f_reset, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0)
        self.led_reset.pack(side=tk.LEFT, padx=5)
        self.circle_reset = self.led_reset.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
        Tooltip(self.led_reset, "リセットピンの現在の入力状態（通電時に緑色点灯）です。")

        l_rst = tk.Label(f_reset, text="リセットピン:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
        l_rst.pack(side=tk.LEFT, padx=(5, 2))
        Tooltip(l_rst, "外部スイッチ等からNG出力・ブザー音を手動停止/リセットするための入力ピン番号 (BCM番号) です。")

        e_reset = self._entry(f_reset, self.v_reset_pin, width=5, key_path="gpio.reset_pin")
        e_reset.pack(side=tk.LEFT, padx=5)
        e_reset.bind("<FocusIn>", lambda ev: self._set_active_entry(e_reset, self.v_reset_pin))

        # --- 中央カラム内容 ---
        f_mid_inner = tk.Frame(col_mid, bg=COLOR_BG_MAIN)
        f_mid_inner.pack(fill=tk.BOTH, expand=True)
        self.show_gpio_map(f_mid_inner)

        # --- 右カラム内容 ---
        # 出力ピン
        outer_out, inner_out = create_card(col_right, "判定出力")
        outer_out.pack(fill=tk.X, pady=(0, 10))
        
        f_out = tk.Frame(inner_out, bg=COLOR_BG_PANEL)
        f_out.pack(fill=tk.X)

        def _make_out_row(parent, label, var, row, key):
            tk.Label(parent, text=label, font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).grid(row=row, column=0, pady=10, sticky="w")
            e = self._entry(parent, var, width=5, key_path=f"gpio.outputs.{key}")
            e.grid(row=row, column=1, padx=10)
            e.bind("<FocusIn>", lambda ev: self._set_active_entry(e, var))
            
            active_color = COLOR_OK if key == "ok" else COLOR_NG
            active_fg = "black" if key == "ok" else "white"

            # テスト点灯ボタン
            btn = tk.Button(parent, text="テスト点灯", font=FONT_NORMAL, bg="#546E7A", fg="white", relief="flat", padx=6)
            btn.grid(row=row, column=2, padx=5)
            
            # 状態表示(LED)
            led = tk.Canvas(parent, width=20, height=20, bg=COLOR_BG_PANEL, highlightthickness=0)
            led.grid(row=row, column=3, padx=5)
            circle = led.create_oval(2, 2, 18, 18, fill="#333", outline="#555")
            
            def _toggle_test(v=var, l=led, c=circle, b=btn, act_color=active_color, act_fg=active_fg):
                try:
                    pin = int(v.get().strip())
                except (ValueError, TypeError):
                    return
                
                cur_color = l.itemcget(c, "fill")
                turn_on = (cur_color == "#333")

                app = getattr(self.master, "app_instance", None)
                if app and hasattr(app, "toggle_output_pin_by_num"):
                    app.toggle_output_pin_by_num(pin, turn_on)
                else:
                    # app 参照がない場合でもモックマネージャー等で状態更新
                    from .hardware import OutputDevice
                    if not hasattr(self, "_test_devices"):
                        self._test_devices = {}
                    if pin not in self._test_devices:
                        self._test_devices[pin] = OutputDevice(pin)
                    if turn_on:
                        self._test_devices[pin].on()
                    else:
                        self._test_devices[pin].off()

                if turn_on:
                    l.itemconfig(c, fill=act_color)
                    b.config(bg=act_color, fg=act_fg)
                else:
                    l.itemconfig(c, fill="#333")
                    b.config(bg="#546E7A", fg="white")
            
            btn.config(command=_toggle_test)
            Tooltip(btn, f"{label}の物理/仮想ピンをON/OFFトグル点灯テストします。")

        _make_out_row(f_out, "OK出力:", self.v_ok, 0, "ok")
        Tooltip(f_out.grid_slaves(row=0, column=0)[0], "判定OK時にON信号を出す GPIO ピン番号です。")
        _make_out_row(f_out, "NG出力:", self.v_ng, 1, "ng")
        Tooltip(f_out.grid_slaves(row=1, column=0)[0], "判定NG時、またはエラー時にON信号を出す GPIO ピン番号です。")

        # ステータスバー
        outer_st, inner_st = create_card(col_right, "システム状態")
        outer_st.pack(fill=tk.X, pady=10)
        self.lbl_gpio_status = tk.Label(inner_st, text="GPIO接続確認中...", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT)
        self.lbl_gpio_status.pack(pady=10)

        # 初期リフレッシュ
        self.refresh_gpio_trig()
        self.refresh_gpio_sel()
        self._check_gpio_connection()
        self._start_monitoring()

    def _set_active_entry(self, entry, var):
        self.active_entry = (entry, var)
        # 以前のハイライトを消す的な処理があればここ

    def _check_gpio_connection(self):
        from .hardware import GPIO_AVAILABLE
        if GPIO_AVAILABLE:
            self.lbl_gpio_status.config(text="GPIO: 接続済み", fg=COLOR_OK)
        else:
            self.lbl_gpio_status.config(text="GPIO: モック動作中", fg=COLOR_WARNING)

    def _start_monitoring(self):
        """入力ピンの状態を監視してLEDを更新する"""
        if not self.winfo_exists():
            return
        if not hasattr(self, "t_gpio") or not self.t_gpio.winfo_exists():
            return

        app = getattr(self.master, "app_instance", None)
        # appのリフレッシュが走っている可能性があるので安全にチェック
        app_inputs = getattr(app, "inputs", {}) # type: ignore
        if app_inputs:
            # トリガー入力
            for t in self.temp_data["gpio"]["triggers"]:
                tid = t["id"]
                if tid in app_inputs and tid in self.pin_widgets:
                    state = app_inputs[tid].is_active
                    led, circle = self.pin_widgets[tid]
                    led.itemconfig(circle, fill=COLOR_OK if state else "#333")
            
            # 判定ピン入力
            for s in self.temp_data["gpio"].get("pattern_pins", []):
                sid = f"sel_{s['id']}"
                if sid in app_inputs and sid in self.pin_widgets:
                    state = app_inputs[sid].is_active
                    led, circle = self.pin_widgets[sid]
                    led.itemconfig(circle, fill=COLOR_OK if state else "#333")

            # NGリセット入力
            if "reset" in app_inputs and hasattr(self, "led_reset") and hasattr(self, "circle_reset") and self.led_reset.winfo_exists():
                state = app_inputs["reset"].is_active
                self.led_reset.itemconfig(self.circle_reset, fill=COLOR_OK if state else "#333")

        self.after(200, self._start_monitoring)

    def show_gpio_map(self, parent):
        """Raspberry Pi 40ピンヘッダのマップを表示する（クリックでピン番号入力）"""
        outer, inner = create_card(parent, "Pi 40Pin Map")
        outer.pack(fill=tk.BOTH, expand=True)

        def _on_pin_clicked(bcm_val):
            widget, var = getattr(self, "active_entry", (None, None))
            if widget and var and bcm_val is not None:
                var.set(bcm_val)
                widget.focus_set()

        
        # ピンデータ (BCM番号)
        # (PinNo, Name, BCM)
        pins = [
            (1, "3.3V", None),   (2, "5V", None),
            (3, "GPIO 2", 2),    (4, "5V", None),
            (5, "GPIO 3", 3),    (6, "GND", None),
            (7, "GPIO 4", 4),    (8, "GPIO 14", 14),
            (9, "GND", None),    (10, "GPIO 15", 15),
            (11, "GPIO 17", 17), (12, "GPIO 18", 18),
            (13, "GPIO 27", 27), (14, "GND", None),
            (15, "GPIO 22", 22), (16, "GPIO 23", 23),
            (17, "3.3V", None),  (18, "GPIO 24", 24),
            (19, "GPIO 10", 10), (20, "GND", None),
            (21, "GPIO 9", 9),   (22, "GPIO 25", 25),
            (23, "GPIO 11", 11), (24, "GPIO 8", 8),
            (25, "GND", None),   (26, "GPIO 7", 7),
            (27, "ID_SD", None), (28, "ID_SC", None),
            (29, "GPIO 5", 5),   (30, "GND", None),
            (31, "GPIO 6", 6),   (32, "GPIO 12", 12),
            (33, "GPIO 13", 13), (34, "GND", None),
            (35, "GPIO 19", 19), (36, "GPIO 16", 16),
            (37, "GPIO 26", 26), (38, "GPIO 20", 20),
            (39, "GND", None),   (40, "GPIO 21", 21)
        ]

        # --- ピンマップ (Grid方式・表示のみ) ---
        mf = tk.Frame(inner, bg=COLOR_BG_PANEL)
        mf.pack(pady=15, padx=20) # 余白を増やす

        for i, (pno, name, bcm) in enumerate(pins):
            col_idx = 0 if i % 2 == 0 else 2
            row_idx = i // 2
            
            # ピン番号ラベル (外側) - フォントを大きく(8->10)
            lbl_no = tk.Label(mf, text=str(pno), font=(FONT_FAMILY, 10, "bold"),
                              width=3, bg="#222", fg="white")
            
            # ピン名称ラベル - フォントを大きく(8->10)、幅・余白を拡大
            lbl_color = "#444"
            if "V" in name: lbl_color = "#8D6E63"   # 電源ピン
            if "GND" in name: lbl_color = "#212121"  # グランドピン
            
            lbl_name = tk.Label(mf, text=name, font=(FONT_FAMILY, 10),
                                width=12, bg=lbl_color, fg=COLOR_TEXT_MAIN,
                                padx=5, pady=3, relief="flat")

            if i % 2 == 0:  # 左列
                lbl_no.grid(row=row_idx, column=0, padx=2, pady=1)
                lbl_name.grid(row=row_idx, column=1, padx=(2, 10), pady=1, sticky="w")
            else:  # 右列
                lbl_name.grid(row=row_idx, column=2, padx=(10, 2), pady=1, sticky="e")
                lbl_no.grid(row=row_idx, column=3, padx=2, pady=1)

            if bcm is not None:
                def make_handler(b=bcm): return lambda e: _on_pin_clicked(b)
                lbl_no.bind("<Button-1>", make_handler())
                lbl_name.bind("<Button-1>", make_handler())
                lbl_no.config(cursor="hand2")
                lbl_name.config(cursor="hand2")
                Tooltip(lbl_name, "クリックで選択中の入力欄にこのピン番号をセットします")

    def refresh_gpio_trig(self):
        for w in self.trig_list_f.winfo_children(): w.destroy()
        
        for i, t in enumerate(self.temp_data["gpio"]["triggers"]):
            def _create_trig_row(idx=i, trig_obj=t):
                f = tk.Frame(self.trig_list_f, bg=COLOR_BG_PANEL)
                f.pack(fill=tk.X, pady=2)
                
                # インジケータ
                led = tk.Canvas(f, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0)
                led.pack(side=tk.LEFT, padx=5)
                circle = led.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
                
                vn = tk.StringVar(value=trig_obj["name"])
                l_trig = tk.Label(f, text="トリガー名:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
                l_trig.pack(side=tk.LEFT, padx=(5, 2))
                Tooltip(l_trig, "このトリガー信号の名称です。設定画面の「パターン」タブで使用されます。")
                self._entry(f, vn, width=12, key_path=f"gpio.triggers.{idx}.name").pack(side=tk.LEFT, padx=2)
                
                vp = tk.IntVar(value=trig_obj["pin"])
                l_pin = tk.Label(f, text=" Pin:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
                l_pin.pack(side=tk.LEFT, padx=(5, 2))
                Tooltip(l_pin, "トリガー信号を入力する GPIO ピン番号です。")
                p_ent = self._entry(f, vp, width=4, key_path=f"gpio.triggers.{idx}.pin")
                p_ent.pack(side=tk.LEFT, padx=5)
                p_ent.bind("<FocusIn>", lambda ev, e=p_ent, v=vp: self._set_active_entry(e, v))
                
                # ピン番号に対するLED登録
                self.pin_widgets[trig_obj["id"]] = (led, circle)

                def _upd_trig_inner(v1=vn, v2=vp):
                    try:
                        self.temp_data["gpio"]["triggers"][idx].update({"name": v1.get(), "pin": v2.get()})
                    except tk.TclError:
                        pass  # 入力途中（空文字など）のエラーは無視
                
                vn.trace_add("write", lambda *a: _upd_trig_inner())
                vp.trace_add("write", lambda *a: _upd_trig_inner())

                if len(self.temp_data["gpio"]["triggers"]) > 1:
                    tk.Button(f, text="×", font=(FONT_FAMILY, 10, "bold"), bg=COLOR_NG_MUTED, fg="white", relief="flat", width=2,
                              command=lambda: [self.temp_data["gpio"]["triggers"].pop(idx), self.refresh_gpio_trig(), self._mark_changed()]).pack(side=tk.RIGHT)
            
            _create_trig_row()

    def refresh_gpio_sel(self):
        for w in self.sel_list_f.winfo_children(): w.destroy()
        
        for i, s in enumerate(self.temp_data["gpio"].get("pattern_pins", [])):
            f = tk.Frame(self.sel_list_f, bg=COLOR_BG_PANEL)
            f.pack(fill=tk.X, pady=2)
            
            led = tk.Canvas(f, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0)
            led.pack(side=tk.LEFT, padx=5)
            circle = led.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
            
            vn = tk.StringVar(value=s["name"])
            l_trig = tk.Label(f, text="名称:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
            l_trig.pack(side=tk.LEFT, padx=(5, 2))
            Tooltip(l_trig, "ピン名称（例: ホールカバー）です。設定画面の「パターン」タブで使用されます。")
            self._entry(f, vn, width=12, key_path=f"gpio.pattern_pins.{i}.name").pack(side=tk.LEFT, padx=2)
            
            vp = tk.IntVar(value=s["pin"])
            l_pin = tk.Label(f, text=" Pin:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
            l_pin.pack(side=tk.LEFT, padx=(5, 2))
            Tooltip(l_pin, "パターンの自動切替に使用する入力ピン番号です。")
            p_ent = self._entry(f, vp, width=4, key_path=f"gpio.pattern_pins.{i}.pin")
            p_ent.pack(side=tk.LEFT, padx=5)
            p_ent.bind("<FocusIn>", lambda ev, e=p_ent, v=vp: self._set_active_entry(e, v))

            # LED登録
            self.pin_widgets[f"sel_{s['id']}"] = (led, circle)

            def _upd_sel(*args, idx=i, name_var=vn, pin_var=vp):
                try:
                    self.temp_data["gpio"]["pattern_pins"][idx].update({"name": name_var.get(), "pin": pin_var.get()})
                except tk.TclError:
                    pass  # 入力途中のエラーを無視
            vn.trace_add("write", _upd_sel)
            vp.trace_add("write", _upd_sel)

            if len(self.temp_data["gpio"].get("pattern_pins", [])) > 1:
               tk.Button(f, text="×", font=(FONT_FAMILY, 10, "bold"), bg=COLOR_NG_MUTED, fg="white", relief="flat", width=2,
                         command=lambda idx=i: [self.temp_data["gpio"]["pattern_pins"].pop(idx), self.refresh_gpio_sel(), self._mark_changed()]).pack(side=tk.RIGHT)

    def add_trig(self):
        self.temp_data["gpio"]["triggers"].append({"id": f"t_{int(time.time())}", "name": f"トリガー {len(self.temp_data['gpio']['triggers'])+1}", "pin": 0})
        self.refresh_gpio_trig()
        self._mark_changed()

    def add_sel_pin(self):
        self.temp_data["gpio"]["pattern_pins"].append({"id": f"s_{int(time.time())}", "name": f"ピン {len(self.temp_data['gpio']['pattern_pins'])+1}", "pin": 0})
        self.refresh_gpio_sel()
        self._mark_changed()

    # ---- パターンタブ ----
    def setup_pat(self):
        m = tk.Frame(self.t_pat, bg=COLOR_BG_MAIN)
        m.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        left_outer, left = create_card(m, "パターン一覧")
        left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        left_outer.config(width=350)

        l_pat = tk.Label(left, text="パターン一覧", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        l_pat.pack(anchor="w", padx=10, pady=(5, 0))
        Tooltip(l_pat, "登録済みの検査パターン一覧です。選択して右側で編集、下部から新規追加・削除が可能です。")
        self.lb_pat = tk.Listbox(left, font=FONT_SET_LBL, bg=COLOR_BG_INPUT,
                                 fg=COLOR_TEXT_MAIN, selectbackground=COLOR_ACCENT,
                                 selectforeground="black", relief="flat",
                                 exportselection=False)
        self.lb_pat.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.lb_pat.bind("<<ListboxSelect>>", self.on_pat_sel)

        tk.Button(left, text="+ パターン追加", font=FONT_BTN_LARGE,
                  bg=COLOR_ACCENT, fg="black", relief="flat",
                  command=self.add_pat).pack(fill=tk.X, padx=10, pady=5)
        tk.Button(left, text="削除", font=FONT_BTN_LARGE, bg=COLOR_NG_MUTED,
                  fg="white", relief="flat",
                  command=self.del_pat).pack(fill=tk.X, padx=10, pady=5)

        right_outer, p_body_container = create_card(m, "パターン設定")
        right_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # スクロールパネルの作成と参照保持
        self.pat_canvas, self.pat_body = self._create_pat_scrollable_panel(p_body_container)
        self.refresh_pat_list()
        # 初期表示時に一番上のパターンを選択 (微遅延)
        self.after(100, self._auto_select_first_pat)

    def _create_pat_scrollable_panel(self, parent):
        """パターン設定専用のスクロールパネル生成（キャンバスへのアクセスを容易にする）"""
        canvas = tk.Canvas(parent, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)
        
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            if not self.winfo_exists(): return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        return canvas, scrollable_frame

    def _auto_select_first_pat(self):
        if not self.winfo_exists(): return
        if self.lb_pat.size() > 0:
            self.lb_pat.selection_set(0)
            self.on_pat_sel(None)

    def refresh_pat_list(self):
        self.lb_pat.delete(0, tk.END)
        for pid in self.temp_data["pattern_order"]:
            self.lb_pat.insert(tk.END, self.temp_data["patterns"][pid]["name"])

    def add_pat(self):
        pid = f"p_{int(time.time())}"
        next_num = len(self.temp_data['pattern_order']) + 1
        name = f"パターン {next_num}"
        self.temp_data["patterns"][pid] = {
            "name": name,
            "pin_condition": [0] * len(self.temp_data["gpio"].get("pattern_pins", [])),
            "stages": {}
        }
        self.temp_data["pattern_order"].append(pid)
        self.refresh_pat_list()
        self._mark_changed()

    def del_pat(self):
        s = self.lb_pat.curselection()
        if s:
            pid = self.temp_data["pattern_order"].pop(s[0])
            del self.temp_data["patterns"][pid]
            self.refresh_pat_list()
            # 右側の詳細画面をクリア
            for w in self.pat_body.winfo_children():
                w.destroy()
            # もし他にパターンがあれば、次の（または前の）項目を自動選択する
            self.after(50, self._auto_select_first_pat)
            self._mark_changed()

    def on_pat_sel(self, e):
        # 現在のスクロール位置を保存
        y_pos = 0.0
        if hasattr(self, "pat_canvas") and self.pat_canvas.winfo_exists():
            y_pos = self.pat_canvas.yview()[0]

        for w in self.pat_body.winfo_children():
            w.destroy()
        s = self.lb_pat.curselection()
        if not s:
            return
        pid = self.temp_data["pattern_order"][s[0]]
        p = self.temp_data["patterns"][pid]

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. 基本設定カード (名称・ピン条件)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        outer1, inner1 = create_card(self.pat_body, "基本設定")
        outer1.pack(fill=tk.X, pady=(0, 15))

        l_name = tk.Label(inner1, text="名称:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
        l_name.pack(anchor="w")
        Tooltip(l_name, "パターンの表示名です。")
        vn = tk.StringVar(value=p["name"])
        e_name = self._entry(inner1, vn, key_path=f"patterns.{pid}.name")
        e_name.pack(fill=tk.X, pady=(5, 15))
        vn.trace_add("write", lambda *a: p.update({"name": vn.get()}))

        l_pin = tk.Label(inner1, text="パターン信号条件:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
        l_pin.pack(anchor="w")
        Tooltip(l_pin, "このパターンを有効にするための入力ピンの状態を指定します。")
        
        pins = self.temp_data["gpio"].get("pattern_pins", [])
        if len(p["pin_condition"]) != len(pins):
            cond = p["pin_condition"]
            if len(cond) < len(pins):
                cond = cond + [0] * (len(pins) - len(cond))
            else:
                cond = cond[:len(pins)]
            p["pin_condition"] = cond

        p_grid = tk.Frame(inner1, bg=COLOR_BG_PANEL)
        p_grid.pack(anchor="w", pady=5)
        p_vars = []
        for i, pin in enumerate(pins):
            def _create_pin_ui(idx=i, pin_obj=pin):
                v = tk.IntVar(value=p["pin_condition"][idx])
                p_vars.append(v)
                btn = tk.Button(p_grid, font=FONT_SET_VAL, width=4, relief="flat")

                def _toggle(var=v, b=btn, i_idx=idx):
                    var.set(1 if var.get() == 0 else 0)
                    _upd_btn_color(b, var.get(), i_idx)
                    self._mark_changed()

                def _upd_btn_color(b, val, b_idx):
                    if val == 1:
                        b.config(text="ON", bg=COLOR_ACCENT, fg="black")
                    else:
                        b.config(text="OFF", bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)

                l_p = tk.Label(p_grid, text=f"{pin_obj['name']}:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
                l_p.grid(row=idx // 3, column=(idx % 3) * 2, sticky="e", padx=(10, 2))
                Tooltip(l_p, f"このパターンを有効にするための {pin_obj['name']} の信号状態(ON/OFF)を指定します。")

                btn.config(command=_toggle)
                _upd_btn_color(btn, v.get(), idx)
                btn.grid(row=idx // 3, column=(idx % 3) * 2 + 1, padx=(0, 10), pady=5)
            
            _create_pin_ui()

        def _upd_p_pins(*a):
            try:
                p["pin_condition"] = [var.get() for var in p_vars]
            except tk.TclError:
                pass
        for v in p_vars:
            v.trace_add("write", _upd_p_pins)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. トリガー別 判定条件
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        tk.Label(self.pat_body, text="トリガー別 判定条件", font=FONT_SET_LBL,
                 bg=COLOR_BG_MAIN, fg=COLOR_ACCENT).pack(anchor="w", pady=(10, 5))

        # ツールチップ用共通テキスト
        tip_text = "【判定仕様】\n・同じトリガー内の条件はすべて満たす必要があります (AND条件)。\n・検出クラスを空欄にすると、指定カメラの全検出物の合計数で判定します。"

        is_half_step = bool(self.temp_data.get("system", {}).get("commit_half_step", False))

        def _create_trigger_card(t):
            tid = t["id"]
            if tid not in p["stages"]:
                p["stages"][tid] = {"conditions": {}}
            st = p["stages"][tid]

            # ドアライン対応時、conditions_fr / conditions_rr が未作成なら初期化
            if is_half_step:
                if "conditions_fr" not in st:
                    st["conditions_fr"] = copy.deepcopy(st.get("conditions", {}))
                if "conditions_rr" not in st:
                    st["conditions_rr"] = copy.deepcopy(st.get("conditions", {}))
            
            # 個別カード (灰色枠線)
            cf_outer = tk.Frame(self.pat_body, bg="#808080", padx=1, pady=1)
            cf_outer.pack(fill=tk.X, pady=8)
            cf_inner = tk.Frame(cf_outer, bg=COLOR_BG_PANEL, padx=15, pady=10)
            cf_inner.pack(fill=tk.BOTH, expand=True)

            head_f = tk.Frame(cf_inner, bg=COLOR_BG_PANEL)
            head_f.pack(fill=tk.X)
            tk.Label(head_f, text=f"■ {t['name']}", font=FONT_BOLD,
                     bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
            
            cond_container = tk.Frame(cf_inner, bg=COLOR_BG_PANEL)

            def _build_table(target_frame, part="fr"):
                # target_frame 内の子要素のみクリア
                for w in target_frame.winfo_children():
                    w.destroy()

                cond_key = "conditions"
                if is_half_step:
                    cond_key = "conditions_fr" if part == "fr" else "conditions_rr"

                if not isinstance(st.get(cond_key), dict):
                    st[cond_key] = {}
                if isinstance(st[cond_key], list):
                    c_id = str(self.temp_data["cameras"][0]["id"]) if self.temp_data["cameras"] else "1"
                    st[cond_key] = {c_id: st[cond_key]}

                # テーブルヘッダー
                header_f = tk.Frame(target_frame, bg=COLOR_BG_PANEL)
                header_f.pack(fill=tk.X, pady=(0, 5))
                
                l_cam = tk.Label(header_f, text="対象カメラ", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=20, anchor="w")
                l_cam.pack(side=tk.LEFT, padx=5)
                Tooltip(l_cam, "判定に使用するカメラの名称です。")
                
                l_cls = tk.Label(header_f, text="検出クラス", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=15, anchor="w")
                l_cls.pack(side=tk.LEFT, padx=5)
                Tooltip(l_cls, "AIが検知する対象の種類を指定します。空欄の場合は全検出物の合計を判定に使用します。")
                
                l_cnt = tk.Label(header_f, text="基準個数", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=8, anchor="w")
                l_cnt.pack(side=tk.LEFT, padx=5)
                Tooltip(l_cnt, "判定OKとするための個数です。0を指定すると未検出でOKとなります。")
                
                tk.Label(header_f, text="", width=4, bg=COLOR_BG_PANEL).pack(side=tk.RIGHT)

                # 各カメラの条件をフラットに並べてテーブル化
                for c in self.temp_data["cameras"]:
                    c_id = str(c["id"])
                    c_conds = st[cond_key].setdefault(c_id, [])

                    for ci, cond in enumerate(c_conds):
                        def _create_row_ui(cam_obj=c, cid=c_id, idx=ci, cond_obj=cond, _tf=target_frame, _cur_p=part):
                            row_f = tk.Frame(_tf, bg=COLOR_BG_PANEL)
                            row_f.pack(fill=tk.X, pady=2)
                            
                            tk.Label(row_f, text=cam_obj["name"], font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=20, anchor="w").pack(side=tk.LEFT, padx=5)
                            
                            cv = tk.StringVar(value=cond_obj.get("class", ""))
                            cb = ttk.Combobox(row_f, textvariable=cv, values=self.model_classes, font=FONT_SET_VAL, width=15, state="readonly")
                            cb.pack(side=tk.LEFT, padx=5)
                            
                            nv = tk.StringVar(value=cond_obj.get("count", "1"))
                            kp = f"patterns.{pid}.stages.{tid}.{cond_key}.{cid}.{idx}"
                            self._spinbox(row_f, nv, 0, 999, 1, width=8, key_path=f"{kp}.count").pack(side=tk.LEFT, padx=5)
                            
                            def _upd_cond(c_dict=cond_obj, v1=cv, v2=nv):
                                try:
                                    c_dict["class"] = v1.get()
                                    c_dict["count"] = v2.get()
                                    self._mark_changed()
                                except tk.TclError:
                                    pass
                            
                            cv.trace_add("write", lambda *a, u=_upd_cond: u())
                            nv.trace_add("write", lambda *a, u=_upd_cond: u())
                            
                            def _do_del(cid_target=cid, target_cond=cond_obj, _ck=cond_key, _t_frame=_tf, _p=_cur_p):
                                if cid_target in st[_ck] and target_cond in st[_ck][cid_target]:
                                    st[_ck][cid_target].remove(target_cond)
                                    _build_table(_t_frame, _p)
                                    _update_canvas_scroll()
                                    self._mark_changed()

                            tk.Button(row_f, text="x", font=(FONT_FAMILY, 10, "bold"), bg=COLOR_NG_MUTED, fg="white", relief="flat", width=2,
                                      command=_do_del, takefocus=False).pack(side=tk.RIGHT, padx=5)
                        
                        _create_row_ui()

                # 行の追加用ボタンエリア
                add_row_f = tk.Frame(target_frame, bg=COLOR_BG_PANEL)
                add_row_f.pack(fill=tk.X, pady=10)
                
                cam_names = [c["name"] for c in self.temp_data["cameras"]]
                sel_cam_v = tk.StringVar()
                if cam_names: sel_cam_v.set(cam_names[0])
                cb_add = ttk.Combobox(add_row_f, textvariable=sel_cam_v, values=cam_names, state="readonly", width=18, font=FONT_SET_VAL)
                cb_add.pack(side=tk.LEFT, padx=5)

                def _add_cond_row(_ck=cond_key, _t_frame=target_frame, _cur_p=part):
                    c_name = sel_cam_v.get()
                    target_c = next((c for c in self.temp_data["cameras"] if c["name"] == c_name), None)
                    if target_c:
                        c_id = str(target_c["id"])
                        st[_ck].setdefault(c_id, []).append({"class": "", "count": "1"})
                        _build_table(_t_frame, _cur_p)
                        _update_canvas_scroll()
                        self._mark_changed()

                btn_add = tk.Button(add_row_f, text="+ 条件追加", font=FONT_NORMAL, bg=COLOR_ACCENT, fg="black", relief="flat",
                                    command=_add_cond_row, takefocus=False)
                btn_add.pack(side=tk.LEFT, padx=5)
                Tooltip(btn_add, tip_text)

            def _update_canvas_scroll():
                if hasattr(self, "pat_canvas") and self.pat_canvas.winfo_exists():
                    self.pat_canvas.configure(scrollregion=self.pat_canvas.bbox("all"))

            cond_container.pack(fill=tk.X, pady=10)

            if is_half_step:
                frame_fr = tk.Frame(cond_container, bg=COLOR_BG_PANEL)
                frame_rr = tk.Frame(cond_container, bg=COLOR_BG_PANEL)

                _build_table(frame_fr, "fr")
                _build_table(frame_rr, "rr")

                # 初期表示は Fr
                frame_fr.pack(fill=tk.X)

                part_frm = tk.Frame(head_f, bg=COLOR_BG_PANEL)
                part_frm.pack(side=tk.RIGHT)

                btn_fr = tk.Button(part_frm, text="Fr 判定条件", font=FONT_BOLD, width=11, relief="flat", takefocus=False)
                btn_rr = tk.Button(part_frm, text="Rr 判定条件", font=FONT_BOLD, width=11, relief="flat", takefocus=False)

                def _show_fr():
                    frame_rr.pack_forget()
                    frame_fr.pack(fill=tk.X)
                    btn_fr.config(bg=COLOR_ACCENT, fg="black")
                    btn_rr.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)
                    _update_canvas_scroll()

                def _show_rr():
                    frame_fr.pack_forget()
                    frame_rr.pack(fill=tk.X)
                    btn_rr.config(bg=COLOR_ACCENT, fg="black")
                    btn_fr.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)
                    _update_canvas_scroll()

                btn_fr.config(command=_show_fr)
                btn_rr.config(command=_show_rr)
                btn_fr.pack(side=tk.LEFT, padx=(0, 4))
                btn_rr.pack(side=tk.LEFT)
                btn_fr.config(bg=COLOR_ACCENT, fg="black")
                btn_rr.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)
            else:
                frame_single = tk.Frame(cond_container, bg=COLOR_BG_PANEL)
                _build_table(frame_single, "single")
                frame_single.pack(fill=tk.X)

        for t in self.temp_data["gpio"]["triggers"]:
            _create_trigger_card(t)

        # スクロール領域の更新
        self.after(50, lambda: self.pat_canvas.configure(scrollregion=self.pat_canvas.bbox("all")) if hasattr(self, "pat_canvas") and self.pat_canvas.winfo_exists() else None)
        # スクロール位置を復元
        self.after(60, lambda: self.pat_canvas.yview_moveto(y_pos) if hasattr(self, "pat_canvas") and self.pat_canvas.winfo_exists() else None)

    # ---- 解像度タブ ----
    def setup_res(self):
        # 解像度の通称マップ
        RES_MAP = {
            "320x240": "320x240 (QVGA)",
            "640x480": "640x480 (VGA)",
            "1280x720": "1280x720 (HD)",
            "1920x1080": "1920x1080 (Full HD)",
            "3840x2160": "3840x2160 (4K)"
        }

        def _to_friendly(s): return RES_MAP.get(s, s)
        def _to_raw(s): return s.split(" ")[0] if "x" in s else s

        # スクロール可能なコンテナ
        main_f = self.create_scrollable_panel(self.t_res)

        def _make_group(title):
            outer, inner = create_card(main_f, title)
            outer.pack(fill=tk.X, padx=20, pady=(10, 15))
            return inner

        def _row(parent, label, key, options, tip):
            row_f = tk.Frame(parent, bg=COLOR_BG_PANEL)
            row_f.pack(fill=tk.X, pady=6, padx=10)
            
            lbl = tk.Label(row_f, text=label, font=FONT_SET_VAL, bg=COLOR_BG_PANEL,
                           fg=COLOR_TEXT_MAIN, anchor="w", width=30)
            lbl.pack(side=tk.LEFT)
            Tooltip(lbl, tip)
            
            raw_val = self.temp_data["storage"].get(key, options[0])
            v = tk.StringVar(value=_to_friendly(raw_val))
            
            friendly_opts = [_to_friendly(o) for o in options]
            cb = ttk.Combobox(row_f, textvariable=v, values=friendly_opts,
                              font=FONT_SET_VAL, state="readonly", width=25)
            cb.pack(side=tk.RIGHT, padx=5)
            
            def _on_change(*a, k=key, var=v, widget=cb):
                if not self.winfo_exists(): return
                raw = _to_raw(var.get())
                self.temp_data["storage"][k] = raw
                self._mark_changed()
                if k == "capture_res":
                    _update_all_filters()

            v.trace_add("write", _on_change)
            return cb, v, options

        # グループA: 基本撮影設定
        inner_a = _make_group("基本撮影設定")
        cb_cap, v_cap, opt_cap = _row(inner_a, "撮影解像度", "capture_res", RES_OPTIONS, "カメラから取得する画像の元サイズです。")

        # グループB: 表示設定
        inner_b = _make_group("表示設定")
        _row(inner_b, "プレビュー解像度", "preview_res", RES_OPTIONS_PREVIEW, "メイン画面のモニタ用サイズ。")

        # グループC: 自動保存設定
        inner_c = _make_group("保存設定")
        
        # --- 検査モード ---
        tk.Label(inner_c, text="▼ 検査モードの保存画素数", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).pack(anchor="w", padx=10, pady=(5, 0))
        cb_ok, v_ok, opt_ok = _row(inner_c, "OK保存画像", "res_ok", RES_OPTIONS_SAVE, "判定OK時に保存するサイズ。")
        cb_ng, v_ng, opt_ng = _row(inner_c, "NG保存画像", "res_ng", RES_OPTIONS_SAVE, "判定NG時に保存するサイズ。")
        cb_skip, v_skip, opt_skip = _row(inner_c, "スキップ時保存画像", "res_skip", RES_OPTIONS_SAVE, "検査スキップ時に保存するサイズ。")

        # 分割線
        tk.Frame(inner_c, bg=COLOR_BG_MAIN, height=1).pack(fill=tk.X, padx=10, pady=10)

        # --- 撮影モード ---
        tk.Label(inner_c, text="▼ 撮影モードの保存画素数", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).pack(anchor="w", padx=10, pady=(0, 0))
        cb_rec, v_rec, opt_rec = _row(inner_c, "判定対象画像", "res_record", RES_OPTIONS_SAVE, "撮影モード時、対象パターン一致時のサイズ。")
        _row(inner_c, "判定対象外画像", "res_record_skip", RES_OPTIONS_SAVE, "パターン不一致時の保存サイズ。「保存しない」で撮影スキップ。")

        # 解像度比較用のヘルパー
        def _get_area(res_str):
            if "x" not in res_str: return 0
            try:
                w, h = map(int, res_str.split("x"))
                return w * h
            except: return 0

        # 全体のフィルタリング更新
        def _update_all_filters():
            cap_val = _to_raw(v_cap.get())
            cap_area = _get_area(cap_val)
            targets = [ 
                (cb_ok, opt_ok, "res_ok"), 
                (cb_ng, opt_ng, "res_ng"), 
                (cb_skip, opt_skip, "res_skip"),
                (cb_rec, opt_rec, "res_record")
            ]
            for cb, opts, k in targets:
                new_opts = [o for o in opts if ("x" not in str(o)) or _get_area(o) <= cap_area]
                cb.config(values=[_to_friendly(o) for o in new_opts])
                raw_curr = _to_raw(cb.get())
                if raw_curr not in new_opts:
                    fallback = new_opts[0] if "x" not in new_opts[0] else cap_val
                    cb.set(_to_friendly(fallback))
                    self.temp_data["storage"][k] = fallback

        self.after(200, _update_all_filters)

    # ---- システムタブ ----
    def setup_sys(self):
        import os
        from tkinter import filedialog

        # スクロール可能なコンテナ
        scroll_f = self.create_scrollable_panel(self.t_sys)

        s = self.temp_data["inference"]

        def _make_group(parent, title, pady=(10, 4)):
            outer, inner = create_card(parent, title)
            outer.pack(fill=tk.X, padx=20, pady=pady)
            return inner

        def _section_title(parent, text):
            lbl = tk.Label(parent, text=text, font=FONT_SET_LBL,
                           bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, anchor="w")
            lbl.pack(fill=tk.X, padx=4, pady=(10, 4))
            return lbl

        def _row_frame(parent, column_widths=(280, 1)):
            f = tk.Frame(parent, bg=COLOR_BG_PANEL)
            f.pack(fill=tk.X, pady=4)
            f.columnconfigure(0, minsize=column_widths[0])
            return f

        def _lbl(parent, text, tip=""):
            l = tk.Label(parent, text=text, font=FONT_SET_VAL,
                         bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, anchor="w", width=22)
            l.pack(side=tk.LEFT, padx=(0, 8))
            if tip:
                Tooltip(l, tip)
            return l

        def _unit(parent, text):
            lbl = tk.Label(parent, text=text, font=FONT_SET_VAL,
                           bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
            lbl.pack(side=tk.LEFT, padx=(2, 0))
            return lbl

        def _entry_w(parent, var, width=10):
            e = self._entry(parent, var, width=width)
            e.pack(side=tk.LEFT)
            return e

        def _browse_btn(parent, var, mode="file", filetypes=None):
            def _pick():
                if mode == "dir":
                    p = filedialog.askdirectory(title="フォルダを選択", parent=self)
                else:
                    p = filedialog.askopenfilename(
                        title="ファイルを選択",
                        parent=self,
                        filetypes=filetypes or [("すべてのファイル", "*.*")])
                if p:
                    var.set(p)
            btn = tk.Button(parent, text="参照", font=FONT_NORMAL,
                            bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                            relief="flat", padx=6, pady=2, cursor="hand2",
                            command=_pick)
            btn.pack(side=tk.LEFT, padx=(6, 0))
            Tooltip(btn, "クリックしてファイル/フォルダを選択します")
            return btn

        def _play_btn(parent, var):
            def _play():
                try:
                    import pygame
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    p = var.get().strip()
                    if p and os.path.exists(p):
                        pygame.mixer.music.load(p)
                        pygame.mixer.music.play(0)
                    else:
                        messagebox.showwarning("テスト再生",
                                               "ファイルが見つかりません:\n" + p,
                                               parent=self)
                except Exception as ex:
                    messagebox.showwarning("テスト再生エラー", str(ex), parent=self)
            btn = tk.Button(parent, text="テスト再生", font=FONT_NORMAL,
                            bg="#37474f", fg=COLOR_TEXT_MAIN,
                            relief="flat", padx=6, pady=2, cursor="hand2",
                            command=_play)
            btn.pack(side=tk.LEFT, padx=(4, 0))
            Tooltip(btn, "設定した音声を1回再生して確認します")
            return btn

        # =====================================================================
        # 1. AI・推論パラメータ設定
        # =====================================================================
        g1 = _make_group(scroll_f, "AI・推論パラメータ設定", pady=(16, 6))

        # しきい値スライダー
        r_thr = _row_frame(g1)
        _lbl(r_thr, "判定しきい値:", "AIの自信度がこの値(0.0〜1.0)以上なら「検出した」とみなします。")
        v_thr = tk.DoubleVar(value=float(s.get("threshold", 0.5)))
        lbl_thr_val = tk.Label(r_thr, text=f"{v_thr.get():.2f}", font=FONT_SET_VAL,
                               bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, width=5)
        lbl_thr_val.pack(side=tk.LEFT, padx=(0, 6))
        sl = ttk.Scale(r_thr, from_=0.0, to=1.0, length=200,
                       variable=v_thr, orient="horizontal")
        sl.pack(side=tk.LEFT)
        btn_live = tk.Button(r_thr, text="ライブ", font=FONT_NORMAL,
                              bg="#546E7A", fg="white", relief="flat", cursor="hand2",
                              command=lambda: self._update_threshold_preview(round(v_thr.get(), 2), recursive=False))
        btn_live.pack(side=tk.LEFT, padx=(8, 0))
        Tooltip(btn_live, "現在のしきい値で検出した結果をプレビューウィンドウで確認します")

        def _upd_thr(*a):
            val = round(v_thr.get(), 2)
            lbl_thr_val.config(text=f"{val:.2f}")
            s["threshold"] = val
            self._mark_changed()
        v_thr.trace_add("write", _upd_thr)

        # 重複判定しきい値スライダー
        r_iou = _row_frame(g1)
        _lbl(r_iou, "重複判定しきい値:",
             "同じ物体を指す検出枠が重なっている場合に、どの程度重なったら一方を省くかの基準です。"
             "値が大きいほど、かなり重なっていないと省きません（複数の枠が残りやすい）。")
        v_iou = tk.DoubleVar(value=float(s.get("iou", 0.7)))
        lbl_iou_val = tk.Label(r_iou, text=f"{v_iou.get():.2f}", font=FONT_SET_VAL,
                               bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, width=5)
        lbl_iou_val.pack(side=tk.LEFT, padx=(0, 6))
        sl_iou = ttk.Scale(r_iou, from_=0.0, to=1.0, length=200,
                           variable=v_iou, orient="horizontal")
        sl_iou.pack(side=tk.LEFT)

        def _upd_iou(*a):
            val = round(v_iou.get(), 2)
            lbl_iou_val.config(text=f"{val:.2f}")
            s["iou"] = val
            self._mark_changed()
        v_iou.trace_add("write", _upd_iou)

        # 数値パラメータ
        num_params = [
            ("トリガー不感時間:", "trigger_debounce_sec", "sec",
             "連続して信号が入った場合に二重検出を防止する最小インターバル秒数です。デフォルト0.0秒（0.0で無効）。", 0.0, 10.0, 0.1),
            ("最大リトライ回数:", "max_retries", "回",
             "1回のトリガーで最大何回まで撮り直しますか。", 0, 99, 1),
            ("撮影間隔:", "burst_interval", "sec",
             "連続撮影時の1枚ごとの待機時間です。", 0.0, 10.0, 0.1),
            ("結果表示時間:", "result_display_time", "sec",
             "判定後、画面に結果を表示し続ける秒数です。", 0.0, 60.0, 0.5),
            ("プレビュー更新レート:", "preview_fps", "fps",
             "メイン画面のカメラ映像を毎秒何回更新するかです。Raspi5では10〜15推奨。", 0.1, 60.0, 0.1),
        ]
        for lbl_txt, key, unit, tip, min_val, max_val, inc in num_params:
            r = _row_frame(g1)
            _lbl(r, lbl_txt, tip)
            v = tk.StringVar(value=str(s.get(key, "")))
            ent = self._spinbox(r, v, min_val, max_val, inc, width=8, key_path=f"inference.{key}")
            ent.pack(side=tk.LEFT)
            _unit(r, unit)
            def _mk_upd(ky=key, var=v, e=ent):
                def _upd(*a):
                    val = var.get()
                    try:
                        s[ky] = float(val) if "." in val else int(val)
                    except Exception:
                        pass
                    self._mark_changed()
                return _upd
            v.trace_add("write", _mk_upd())

        # =====================================================================
        # 2. 出力制御 & 生産ライン同期
        # =====================================================================
        g2 = _make_group(scroll_f, "出力制御 & 生産ライン同期", pady=(10, 6))

        _section_title(g2, "▼ 信号出力制御")

        r_ok = _row_frame(g2)
        _lbl(r_ok, "OK出力時間:", "OK判定後、出力信号をONにし続ける秒数です。")
        v_ok_t = tk.StringVar(value=str(s.get("ok_output_time", "0.5")))
        ok_sp = self._spinbox(r_ok, v_ok_t, 0.0, 60.0, 0.1, width=8)
        ok_sp.pack(side=tk.LEFT)
        _unit(r_ok, "sec")
        def _upd_ok_t(*a):
            try:
                s["ok_output_time"] = float(v_ok_t.get())
            except Exception:
                pass
        v_ok_t.trace_add("write", _upd_ok_t)

        r_ng = _row_frame(g2)
        _lbl(r_ng, "NG出力時間:", "NG判定後の出力時間(秒)。空欄にすると「ブザー停止」ボタンが押されるまでONを保持します。")
        v_ng_t = tk.StringVar(value=str(s.get("ng_output_time", "")))
        ng_sp = self._spinbox(r_ng, v_ng_t, 0.0, 60.0, 0.1, width=8)
        ng_sp.pack(side=tk.LEFT)
        _unit(r_ng, "sec（空欄=ブザー停止まで保持）")
        def _upd_ng_t(*a):
            val = v_ng_t.get().strip()
            try:
                s["ng_output_time"] = float(val) if val else ""
            except Exception:
                pass
        v_ng_t.trace_add("write", _upd_ng_t)

        r_hold = _row_frame(g2)
        v_hold = tk.BooleanVar(value=bool(s.get("ng_output_hold", False)))
        cb_ng_hold = tk.Checkbutton(
            r_hold,
            text="ブザー停止ボタンが押されるまでNG出力を保持する",
            variable=v_hold, font=FONT_NORMAL,
            bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_BG_PANEL, activeforeground=COLOR_TEXT_MAIN,
            selectcolor=COLOR_BG_INPUT
        )
        cb_ng_hold.pack(side=tk.LEFT)
        Tooltip(cb_ng_hold, "ONにすると、NG判定時の信号出力をブザー停止ボタンが押されるまでONのまま保持します。")

        def _upd_hold(*a):
            val = v_hold.get()
            s["ng_output_hold"] = val
            if val:
                ng_sp.config(state="disabled")
            else:
                ng_sp.config(state="normal")
            self._mark_changed()
        v_hold.trace_add("write", _upd_hold)
        if s.get("ng_output_hold", False):
            ng_sp.config(state="disabled")

        _section_title(g2, "▼ 生産ライン同期")

        st_sys = self.temp_data.setdefault("system", {})

        r_step = _row_frame(g2)
        v_step = tk.BooleanVar(value=bool(st_sys.get("commit_half_step", False)))
        cb_step = tk.Checkbutton(
            r_step, text="ドアライン対応 (Fr/Rr 分割判定 & 0.5刻みコミット)",
            variable=v_step, onvalue=True, offvalue=False,
            font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_BG_PANEL, activeforeground=COLOR_TEXT_MAIN,
            selectcolor=COLOR_BG_INPUT, relief="flat"
        )
        cb_step.pack(side=tk.LEFT)
        Tooltip(cb_step, "ONにすると、コミット番号が 0001 Fr → 0001 Rr → 0002 Fr... のように進行します。\n整数時(Fr)にのみパターンを取得して1台分引き継ぎ、Fr/Rr別々の判定条件を設定・評価できます。")
        
        def _format_delay_value(val, half_step):
            try:
                num = float(val)
            except (TypeError, ValueError):
                return "0"
            if half_step:
                if abs(num - round(num)) < 1e-9:
                    return str(int(round(num)))
                return f"{num:.1f}"
            return str(int(num))

        def _apply_delay_spinbox_mode():
            half = v_step.get()
            if half:
                delay_sp.config(from_=0.0, to=99.0, increment=0.5)
                delay_unit_lbl.config(
                    text="サイクル（0.5刻みで設定可能、0で遅延なし）"
                )
            else:
                delay_sp.config(from_=0, to=99, increment=1)
                delay_unit_lbl.config(
                    text="サイクル（整数のみ、0で遅延なし）"
                )

        def _upd_step(*a):
            st_sys["commit_half_step"] = v_step.get()
            _apply_delay_spinbox_mode()
            if not v_step.get():
                try:
                    num = float(v_delay.get())
                    if abs(num - int(num)) > 1e-9:
                        v_delay.set(str(int(num)))
                except (TypeError, ValueError):
                    pass
            self._mark_changed()
        v_step.trace_add("write", _upd_step)

        r_delay = _row_frame(g2)
        _lbl(r_delay, "仕様情報遅延サイクル数:", "トリガー時に取得した仕様情報を、何サイクル（コミット数）後に実際の検査に適用するかを指定します。")
        half_init = bool(st_sys.get("commit_half_step", False))
        v_delay = tk.StringVar(value=_format_delay_value(st_sys.get("delay_cycles", 0), half_init))
        self._last_valid_delay_str = v_delay.get()
        self._delay_revert_guard = False
        delay_sp = self._spinbox(r_delay, v_delay, 0.0, 99.0, 0.5, width=8)
        delay_sp.pack(side=tk.LEFT)
        delay_unit_lbl = _unit(r_delay, "サイクル（0.5刻みで設定可能、0で遅延なし）")
        _apply_delay_spinbox_mode()

        def _upd_delay(*a):
            if self._delay_revert_guard:
                return
            val = v_delay.get().strip()
            if val in ("", "-", ".", "-."):
                return
            try:
                num = float(val)
            except ValueError:
                return
            if not v_step.get():
                if abs(num - int(num)) > 1e-9:
                    self._delay_revert_guard = True
                    v_delay.set(self._last_valid_delay_str)
                    self._delay_revert_guard = False
                    messagebox.showerror(
                        "入力エラー",
                        "0.5刻みモードがOFFのとき、遅延サイクル数は整数のみ指定できます。",
                        parent=self,
                    )
                    return
                st_sys["delay_cycles"] = int(num)
                self._last_valid_delay_str = str(int(num))
            else:
                st_sys["delay_cycles"] = num
                self._last_valid_delay_str = _format_delay_value(num, True)
            self._mark_changed()

        v_delay.trace_add("write", _upd_delay)

        # =====================================================================
        # 3. ファイルパス & 容量自動管理
        # =====================================================================
        g3 = _make_group(scroll_f, "ファイルパス & 容量自動管理", pady=(10, 6))

        _section_title(g3, "▼ ファイル・モデルパス")

        r_res = _row_frame(g3)
        _lbl(r_res, "結果出力先フォルダ:", "ログ・CSV・保存画像の親フォルダを絶対パスで指定します。")
        vp = tk.StringVar(value=self.temp_data["storage"].get("results_dir", ""))
        _entry_w(r_res, vp, width=40)
        _browse_btn(r_res, vp, mode="dir")
        vp.trace_add("write", lambda *a: self.temp_data["storage"].update({"results_dir": vp.get()}))

        r_mdl = _row_frame(g3)
        _lbl(r_mdl, "AIモデルパス:",
             "推論に使用するYOLOモデルを指定します。\n"
             "・.pt ファイル: 「.pt参照」ボタンでファイルを選択\n"
             "・ncnnモデル: 「ncnnフォルダ参照」ボタンでフォルダを選択\n"
             "※選択完了後、自動的にモデルがロードされ検出クラスが更新されます。")
        vm = tk.StringVar(value=s.get("model_path", ""))
        _entry_w(r_mdl, vm, width=35)

        def _on_model_picked(p):
            if p:
                vm.set(p)
                s["model_path"] = p
                self._load_model_classes_for_path(p, show_feedback=True)
                self._mark_changed()

        def _pick_pt():
            p = filedialog.askopenfilename(
                title="YOLOモデルファイルを選択",
                parent=self,
                filetypes=[("PyTorch モデル", "*.pt"), ("すべてのファイル", "*.*")])
            if p:
                _on_model_picked(p)

        btn_pt = tk.Button(r_mdl, text=".pt参照", font=FONT_NORMAL,
                           bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                           relief="flat", padx=6, pady=2, cursor="hand2",
                           command=_pick_pt)
        btn_pt.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(btn_pt, "PyTorch形式のモデルファイル(*.pt)を選択します (選択完了後、自動ロードされます)")

        def _pick_ncnn():
            p = filedialog.askdirectory(title="ncnnモデルフォルダを選択", parent=self)
            if p:
                _on_model_picked(p)

        btn_ncnn = tk.Button(r_mdl, text="ncnnフォルダ", font=FONT_NORMAL,
                             bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                             relief="flat", padx=6, pady=2, cursor="hand2",
                             command=_pick_ncnn)
        btn_ncnn.pack(side=tk.LEFT, padx=(4, 0))
        Tooltip(btn_ncnn, "ncnn形式のモデルフォルダ(*.ncnnディレクトリ)を選択します (選択完了後、自動ロードされます)")

        # モデルステータス表示行
        r_mdl_st = tk.Frame(g3, bg=COLOR_BG_PANEL)
        r_mdl_st.pack(fill=tk.X, pady=(2, 6), padx=10)
        self.lbl_model_status = tk.Label(r_mdl_st, text="", font=(FONT_FAMILY, 11),
                                         bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, anchor="w")
        self.lbl_model_status.pack(fill=tk.X)

        # 初期表示時のモデル情報表示
        self._load_model_classes_for_path(vm.get(), show_feedback=True)

        def _on_entry_changed(*a):
            p = vm.get().strip()
            s["model_path"] = p
            if os.path.exists(p):
                self._load_model_classes_for_path(p, show_feedback=True)
            self._mark_changed()

        vm.trace_add("write", _on_entry_changed)

        _section_title(g3, "▼ 容量監視・自動削除")

        st = self.temp_data["storage"]

        r_ad = _row_frame(g3)
        v_ad = tk.BooleanVar(value=bool(st.get("auto_delete_enabled", False)))
        cb = tk.Checkbutton(
            r_ad, text="古い結果画像を自動削除する",
            variable=v_ad, onvalue=True, offvalue=False,
            font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_BG_PANEL, activeforeground=COLOR_TEXT_MAIN,
            selectcolor=COLOR_BG_INPUT, relief="flat"
        )
        cb.pack(side=tk.LEFT)
        Tooltip(cb, "容量が上限を超えると、保存フォルダ内の古い画像から順番に自動削除します。\nCSVログやモデルファイルは削除されません。")
        v_ad.trace_add("write", lambda *a: st.update({"auto_delete_enabled": v_ad.get()}))

        r_mg = _row_frame(g3)
        _lbl(r_mg, "最大容量上限:", "この容量を超えると古い画像から自動削除します。")
        v_mg = tk.StringVar(value=str(st.get("max_results_gb", "")))
        mg_sp = self._spinbox(r_mg, v_mg, 0.1, 9999.0, 1.0, width=8)
        mg_sp.pack(side=tk.LEFT)
        _unit(r_mg, "GB")
        def _upd_mg(*a):
            try:
                st["max_results_gb"] = float(v_mg.get())
            except Exception:
                pass
        v_mg.trace_add("write", _upd_mg)

        v_used = tk.StringVar(value="現在の使用量: 計算中...")
        lbl_used = tk.Label(g3, textvariable=v_used, font=FONT_SET_VAL,
                            bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, anchor="w")
        lbl_used.pack(fill=tk.X, pady=(4, 0))

        def _calc_storage():
            import shutil as _shutil
            _res_dir = st.get("results_dir", "")
            try:
                if _res_dir and os.path.exists(_res_dir):
                    _used = sum(f.stat().st_size for f in Path(_res_dir).rglob('*') if f.is_file())
                    _used_gb = _used / (1024**3)
                    _total_gb = _shutil.disk_usage(_res_dir).total / (1024**3)
                    msg = f"現在の使用量: {_used_gb:.2f} GB / ディスク合計: {_total_gb:.1f} GB"
                    self.after(0, lambda: v_used.set(msg))
                else:
                    self.after(0, lambda: v_used.set("現在の使用量: -"))
            except Exception:
                self.after(0, lambda: v_used.set("(使用量の取得に失敗しました)"))

        threading.Thread(target=_calc_storage, daemon=True).start()

        # =====================================================================
        # 4. 音声・ブザー設定
        # =====================================================================
        g4 = _make_group(scroll_f, "音声・ブザー設定", pady=(10, 6))

        r_bng = _row_frame(g4)
        _lbl(r_bng, "NG時ブザー音:", "NG判定時に再生する音声ファイルです。空欄で無効。")
        vb = tk.StringVar(value=s.get("buzzer_path", ""))
        _entry_w(r_bng, vb, width=35)
        _browse_btn(r_bng, vb, mode="file",
                    filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべて", "*.*")])
        _play_btn(r_bng, vb)
        vb.trace_add("write", lambda *a: s.update({"buzzer_path": vb.get()}))

        r_bok = _row_frame(g4)
        _lbl(r_bok, "OK時ブザー音:", "OK判定時に再生する音声ファイルです。空欄で無効。")
        vob = tk.StringVar(value=s.get("ok_buzzer_path", ""))
        _entry_w(r_bok, vob, width=35)
        _browse_btn(r_bok, vob, mode="file",
                    filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべて", "*.*")])
        _play_btn(r_bok, vob)
        vob.trace_add("write", lambda *a: s.update({"ok_buzzer_path": vob.get()}))

        # =====================================================================
        # 5. システムツール & メンテナンス
        # =====================================================================
        g5 = _make_group(scroll_f, "システムツール & メンテナンス", pady=(10, 16))

        # 起動ショートカット作成
        r_sh = _row_frame(g5)
        _lbl(r_sh, "起動スクリプト生成:", "デスクトップにワンクリックで本アプリを起動するファイルを作成します。")

        def _get_desktop_path():
            home = os.path.expanduser("~")
            if sys.platform.startswith("win"):
                desktop = os.path.join(home, "Desktop")
                if os.path.exists(desktop):
                    return desktop
                try:
                    import winreg
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                    )
                    path, _ = winreg.QueryValueEx(key, "Desktop")
                    winreg.CloseKey(key)
                    expanded = os.path.expandvars(path)
                    if os.path.exists(expanded):
                        return expanded
                except Exception:
                    pass
                return desktop
            else:
                desktop = os.path.join(home, "Desktop")
                if os.path.exists(desktop):
                    return desktop
                desktop_ja = os.path.join(home, "デスクトップ")
                if os.path.exists(desktop_ja):
                    return desktop_ja
                user_dirs = os.path.join(home, ".config", "user-dirs.dirs")
                if os.path.exists(user_dirs):
                    try:
                        with open(user_dirs, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.startswith("XDG_DESKTOP_DIR"):
                                    p = line.split("=")[1].strip().strip('"')
                                    p = p.replace("$HOME", home)
                                    if os.path.exists(p):
                                        return p
                    except Exception:
                        pass
                return desktop

        def _create_desktop_launcher():
            try:
                desktop_dir = _get_desktop_path()
                if not os.path.exists(desktop_dir):
                    os.makedirs(desktop_dir, exist_ok=True)

                app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                python_exe = sys.executable

                is_win = sys.platform.startswith("win")
                if is_win:
                    filename = "検査システム起動.bat"
                    file_path = os.path.join(desktop_dir, filename)
                    content = (
                        "@echo off\n"
                        "chcp 65001 > nul\n"
                        "title 自動検査システム\n"
                        f'cd /d "{app_dir}"\n'
                        f'"{python_exe}" main.py\n'
                        "if %errorlevel% neq 0 (\n"
                        "    echo.\n"
                        "    echo エラーが発生しました。キーを押すと終了します...\n"
                        "    pause > nul\n"
                        ")\n"
                    )
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                else:
                    filename = "検査システム起動.sh"
                    file_path = os.path.join(desktop_dir, filename)
                    content = (
                        "#!/bin/bash\n"
                        f'cd "{app_dir}"\n'
                        f'"{python_exe}" main.py\n'
                        "if [ $? -ne 0 ]; then\n"
                        '    read -p "エラーが発生しました。Enterキーを押すと終了します..."\n'
                        "fi\n"
                    )
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    try:
                        os.chmod(file_path, 0o755)
                    except Exception:
                        pass

                messagebox.showinfo(
                    "ショートカット作成完了",
                    f"デスクトップに起動スクリプトを作成しました:\n\n{file_path}",
                    parent=self
                )
            except Exception as ex:
                messagebox.showerror(
                    "作成失敗",
                    f"起動スクリプトの作成中にエラーが発生しました:\n{ex}",
                    parent=self
                )

        is_win = sys.platform.startswith("win")
        btn_text = "デスクトップに起動ファイルを作成 (.bat)" if is_win else "デスクトップに起動ファイルを作成 (.sh)"

        btn_shortcut = tk.Button(
            r_sh, text=btn_text, font=FONT_NORMAL,
            bg=COLOR_ACCENT, fg="white",
            relief="flat", padx=10, pady=4, cursor="hand2",
            command=_create_desktop_launcher
        )
        btn_shortcut.pack(side=tk.LEFT, padx=(0, 6))
        Tooltip(btn_shortcut, f"デスクトップに本アプリを起動する{'batch (.bat)' if is_win else 'shell (.sh)'}ファイルを作成します")

        # 日時設定
        r_datetime = _row_frame(g5)
        _lbl(r_datetime, "ラズパイ本体日時設定:", "本体のシステム日付・時刻を手動設定または端末同期します。")

        def _open_datetime_dialog():
            SystemDateTimeDialog(self)

        btn_dt = tk.Button(
            r_datetime, text="ラズパイ本体の日時を設定", font=FONT_NORMAL,
            bg=COLOR_ACCENT, fg="white",
            relief="flat", padx=10, pady=4, cursor="hand2",
            command=_open_datetime_dialog
        )
        btn_dt.pack(side=tk.LEFT, padx=(0, 6))
        Tooltip(btn_dt, "Linux/Raspberry Piのシステム日時(timedatectl/date)を設定するダイアログを開きます")

        # USBスピーカー設定
        r_audio = _row_frame(g5)
        _lbl(r_audio, "USBスピーカー自動設定:", "音が出ない場合に、ALSA/PulseAudio/PipeWire等の出力先とミュートを自動解除します。")

        def _fix_usb_audio():
            if sys.platform.startswith("win"):
                messagebox.showinfo(
                    "USBスピーカー設定 (Windows)",
                    "Windows環境のためLinuxオーディオ設定 (ALSA/PulseAudio/PipeWire/asoundrc) はスキップされました。\n"
                    "※ラズパイ/Linux環境で自動出力設定が実行されます。",
                    parent=self
                )
                return

            import subprocess
            logs = []

            # 1. ALSA mixer (amixer) ミュート解除・ボリューム100%化
            channels = ["Master", "PCM", "Speaker", "Headphone", "Line"]
            for card_idx in range(5):
                for ch in channels:
                    cmd = ["amixer", "-c", str(card_idx), "sset", ch, "100%", "unmute"]
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                        if r.returncode == 0:
                            logs.append(f"ALSA: card {card_idx} {ch} -> 100% unmute")
                    except Exception:
                        pass

            for ch in channels:
                try:
                    subprocess.run(["amixer", "sset", ch, "100%", "unmute"], capture_output=True, text=True, timeout=3)
                except Exception:
                    pass

            try:
                subprocess.run(["sudo", "alsactl", "store"], capture_output=True, text=True, timeout=3)
                logs.append("ALSA: alsactl store 実行完了")
            except Exception:
                pass

            # 2. PulseAudio / PipeWire (pactl / wpctl)
            try:
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], capture_output=True, text=True, timeout=3)
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"], capture_output=True, text=True, timeout=3)
                logs.append("PulseAudio: @DEFAULT_SINK@ -> unmute & 100%")
            except Exception:
                pass

            try:
                r = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout:
                    for line in r.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            sink_name = parts[1]
                            if any(k in sink_name.lower() for k in ["usb", "audio", "headset", "speaker"]):
                                subprocess.run(["pactl", "set-default-sink", sink_name], capture_output=True, text=True, timeout=3)
                                subprocess.run(["pactl", "set-sink-mute", sink_name, "0"], capture_output=True, text=True, timeout=3)
                                subprocess.run(["pactl", "set-sink-volume", sink_name, "100%"], capture_output=True, text=True, timeout=3)
                                logs.append(f"PulseAudio: USB Sink [{sink_name}] をデフォルトに設定")
                                break
            except Exception:
                pass

            try:
                r = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout:
                    in_sinks = False
                    for line in r.stdout.splitlines():
                        if "Sinks:" in line:
                            in_sinks = True
                            continue
                        if in_sinks and ("Sources:" in line or "Filters:" in line or not line.strip()):
                            in_sinks = False
                        if in_sinks and any(k in line.lower() for k in ["usb", "audio", "speaker"]):
                            cleaned = line.replace("│", "").replace("*", "").strip()
                            parts = cleaned.split(".")
                            if parts[0].strip().isdigit():
                                sink_id = parts[0].strip()
                                subprocess.run(["wpctl", "set-default", sink_id], capture_output=True, text=True, timeout=3)
                                subprocess.run(["wpctl", "set-mute", sink_id, "0"], capture_output=True, text=True, timeout=3)
                                subprocess.run(["wpctl", "set-volume", sink_id, "1.0"], capture_output=True, text=True, timeout=3)
                                logs.append(f"PipeWire: USB Sink ID [{sink_id}] をデフォルトに設定")
                                break
            except Exception:
                pass

            # 3. ~/.asoundrc に USB オーディオカード優先設定
            try:
                usb_card_num = None
                if os.path.exists("/proc/asound/cards"):
                    with open("/proc/asound/cards", "r", encoding="utf-8", errors="ignore") as f:
                        cards_info = f.read()
                        for line in cards_info.splitlines():
                            if "USB" in line or "Audio" in line:
                                parts = line.strip().split()
                                if parts and parts[0].isdigit():
                                    usb_card_num = parts[0]
                                    break

                if usb_card_num is not None:
                    asoundrc_path = os.path.expanduser("~/.asoundrc")
                    asoundrc_content = f"""pcm.!default {{
    type plug
    slave.pcm "hw:{usb_card_num},0"
}}
ctl.!default {{
    type hw
    card {usb_card_num}
}}
"""
                    with open(asoundrc_path, "w", encoding="utf-8") as f:
                        f.write(asoundrc_content)
                    logs.append(f"ALSA config: ~/.asoundrc を生成 (USB Card: {usb_card_num})")
            except Exception as ex:
                logs.append(f"ALSA config エラー: {ex}")

            # 4. raspi-config
            try:
                subprocess.run(["sudo", "raspi-config", "nonint", "do_audio", "2"], capture_output=True, text=True, timeout=3)
                logs.append("raspi-config: do_audio (USB Audio/Headphones) 実行")
            except Exception:
                pass

            # 5. Pygame mixer
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.quit()
                pygame.mixer.init()
                logs.append("Pygame Mixer: 再初期化完了")
            except Exception as ex:
                logs.append(f"Pygame Mixer 再初期化スキップ: {ex}")

            if logs:
                msg = "USBスピーカー音声出力の設定・自動復旧を完了しました。\n\n【実行ログ】\n" + "\n".join(logs)
                messagebox.showinfo("USBスピーカー設定完了", msg, parent=self)
            else:
                messagebox.showwarning("USBスピーカー設定", "設定コマンドを実行しましたが、変更ログはありませんでした。", parent=self)

        btn_audio = tk.Button(
            r_audio, text="USBスピーカー音声出力を自動復旧・設定", font=FONT_NORMAL,
            bg=COLOR_ACCENT, fg="white",
            relief="flat", padx=10, pady=4, cursor="hand2",
            command=_fix_usb_audio
        )
        btn_audio.pack(side=tk.LEFT, padx=(0, 6))
        Tooltip(btn_audio, "音が出ない場合に、ALSA/PulseAudio/PipeWire/asoundrc等の設定を一括自動実行します")


    # ---- 保存 / GPIO テスト ----

    def _sync_pattern_conditions(self):
        """設定データ内のパターンピン数と各パターンの条件値の長さを同期し、
        不要な（設定順序に含まれない）パターンをクリーンアップする
        """
        active_pids = set(self.temp_data.get("pattern_order", []))
        all_pids = list(self.temp_data.get("patterns", {}).keys())
        for pid in all_pids:
            if pid not in active_pids:
                self.temp_data["patterns"].pop(pid, None)

        pins_count = len(self.temp_data["gpio"].get("pattern_pins", []))
        for pid in self.temp_data.get("pattern_order", []):
            p = self.temp_data["patterns"].get(pid)
            if not p:
                continue
            cond = p.get("pin_condition", [])
            if not isinstance(cond, list):
                cond = []
            if len(cond) < pins_count:
                cond = cond + [0] * (pins_count - len(cond))
            elif len(cond) > pins_count:
                cond = cond[:pins_count]
            p["pin_condition"] = cond

    def validate_pins(self):
        """ピン番号のバリデーション（重複チェック＋有効BCMピンチェック）"""
        used_pins = {}
        def _get_val(v):
            """str/intを確実にintに変換。無効なら-1を返す"""
            if v is None: return -1
            s_val = str(v).strip()
            if not s_val:
                return -1
            try:
                return int(s_val)
            except:
                return -1

        all_pins = []  # (ピン番号, 設定名) のリスト

        # トリガーピンを収集
        for t in self.temp_data["gpio"]["triggers"]:
            all_pins.append((_get_val(t["pin"]), t["name"]))
        # 判定ピンを収集
        for s in self.temp_data["gpio"].get("pattern_pins", []):
            all_pins.append((_get_val(s["pin"]), s["name"]))
        # 出力ピンを収集
        outputs = self.temp_data["gpio"]["outputs"]
        all_pins.append((_get_val(outputs["ok"]), "OK出力"))
        all_pins.append((_get_val(outputs["ng"]), "NG出力"))

        # 各ピンを検証
        for p, name in all_pins:
            # 無効な値（空欄・数値でない）はエラー
            if p == -1:
                messagebox.showerror("バリデーションエラー",
                    f"「{name}」のピン番号が未入力または無効です", parent=self)
                return False
            # 有効なBCMピン番号かチェック（ピン0等はエラー）
            if p not in VALID_BCM_PINS:
                messagebox.showerror("バリデーションエラー",
                    f"「{name}」のピン番号 {p} は有効なBCMピンではありません\n"
                    f"有効なピン: {sorted(VALID_BCM_PINS)}", parent=self)
                return False
            # 重複チェック
            if p in used_pins:
                messagebox.showerror("バリデーションエラー",
                    f"ピン {p} が重複しています: {name} と {used_pins[p]}", parent=self)
                return False
            used_pins[p] = name
        
        return True

    def _validate_delay_cycles(self):
        """遅延サイクル数のバリデーション（0.5刻みOFF時は整数のみ）"""
        st_sys = self.temp_data.get("system", {})
        try:
            delay = float(st_sys.get("delay_cycles", 0))
        except (TypeError, ValueError):
            messagebox.showerror(
                "バリデーションエラー",
                "遅延サイクル数に有効な数値を入力してください。",
                parent=self,
            )
            return False
        if not bool(st_sys.get("commit_half_step", False)):
            if abs(delay - int(delay)) > 1e-9:
                messagebox.showerror(
                    "バリデーションエラー",
                    "0.5刻みモードがOFFのとき、遅延サイクル数は整数のみ指定できます。",
                    parent=self,
                )
                return False
            st_sys["delay_cycles"] = int(delay)
        return True

    def save_and_close(self):
        # バリデーション前に最新の出力ピン設定を同期
        try:
            self.temp_data["gpio"]["outputs"]["ok"] = self.v_ok.get()
            self.temp_data["gpio"]["outputs"]["ng"] = self.v_ng.get()
        except tk.TclError:
            messagebox.showerror("バリデーションエラー", "出力ピンには数値を入力してください", parent=self)
            return

        # 各種名称のトリミングとバリデーション (空欄および空白文字のみの設定を防止)
        for i, c in enumerate(self.temp_data["cameras"]):
            name = c.get("name", "").strip()
            if not name:
                messagebox.showerror("バリデーションエラー", f"カメラ {i+1} の表示名が空です。有効な名前を入力してください。", parent=self)
                return
            c["name"] = name

        for i, t in enumerate(self.temp_data["gpio"]["triggers"]):
            name = t.get("name", "").strip()
            if not name:
                messagebox.showerror("バリデーションエラー", f"トリガー {i+1} の名称が空です。有効な名前を入力してください。\n(空白のみの名前は設定できません)", parent=self)
                return
            # ファイル名に使用できない文字のチェック
            invalid_chars = [char for char in name if char in '<>:"/\\|?*']
            if invalid_chars:
                messagebox.showerror(
                    "バリデーションエラー",
                    f"トリガー {i+1} の名称にファイル名として使用できない文字が含まれています:\n"
                    f"{', '.join(invalid_chars)}",
                    parent=self
                )
                return
            t["name"] = name

        for i, s in enumerate(self.temp_data["gpio"].get("pattern_pins", [])):
            name = s.get("name", "").strip()
            if not name:
                messagebox.showerror("バリデーションエラー", f"パターン切替ピン {i+1} の名称が空です。有効な名前を入力してください。", parent=self)
                return
            s["name"] = name

        for pid, p in self.temp_data["patterns"].items():
            name = p.get("name", "").strip()
            if not name:
                messagebox.showerror("バリデーションエラー", f"パターン 「{pid}」 の名称が空です。有効な名前を入力してください。", parent=self)
                return
            p["name"] = name

        # パス設定のトリミング
        if "storage" in self.temp_data and "results_dir" in self.temp_data["storage"]:
            self.temp_data["storage"]["results_dir"] = self.temp_data["storage"]["results_dir"].strip()
        if "inference" in self.temp_data and "model_path" in self.temp_data["inference"]:
            self.temp_data["inference"]["model_path"] = self.temp_data["inference"]["model_path"].strip()

        # 基本的なピンのバリデーション
        if not self.validate_pins():
            return

        if not self._validate_delay_cycles():
            return

        # 不要なパターンのクリーンアップとピン条件の同期
        self._sync_pattern_conditions()

        # バリデーション: 全パターンの入力ピン条件が重複していないかチェック
        pin_map = {} # { tuple_condition: [pattern_names] }
        for pid in self.temp_data["pattern_order"]:
            p = self.temp_data["patterns"][pid]
            cond = tuple(p.get("pin_condition", []))
            if cond not in pin_map:
                pin_map[cond] = []
            pin_map[cond].append(p.get("name", pid))
        
        duplicates = [names for names in pin_map.values() if len(names) > 1]
        if duplicates:
            msg = "以下のパターンで同じ入力ピン条件が設定されています。判定が曖昧になるため修正してください:\n\n"
            for names in duplicates:
                msg += f"・{', '.join(names)}\n"
            messagebox.showwarning("バリデーションエラー", msg, parent=self)
            return

        # ピン重複チェック（トリガー、パターン切替、OK/NG出力、NGリセットピン）
        used_pins = {}
        for t in self.temp_data["gpio"]["triggers"]:
            p = t["pin"]
            if p in used_pins:
                return messagebox.showerror("エラー", f"ピン {p} が重複しています ({used_pins[p]} と {t.get('name', 'トリガー')})", parent=self)
            used_pins[p] = f"トリガー: {t.get('name', 'トリガー')}"
        for s in self.temp_data["gpio"].get("pattern_pins", []):
            p = s["pin"]
            if p in used_pins:
                return messagebox.showerror("エラー", f"ピン {p} が重複しています ({used_pins[p]} と {s.get('name', 'パターンピン')})", parent=self)
            used_pins[p] = f"パターンピン: {s.get('name', 'パターンピン')}"
        
        ok_p = self.v_ok.get()
        if ok_p in used_pins:
            return messagebox.showerror("エラー", f"OK出力ピン {ok_p} が重複しています ({used_pins[ok_p]})", parent=self)
        used_pins[ok_p] = "OK出力"

        ng_p = self.v_ng.get()
        if ng_p in used_pins:
            return messagebox.showerror("エラー", f"NG出力ピン {ng_p} が重複しています ({used_pins[ng_p]})", parent=self)
        used_pins[ng_p] = "NG出力"

        reset_str = self.v_reset_pin.get().strip()
        if reset_str:
            try:
                reset_p = int(reset_str)
                if reset_p in used_pins:
                    return messagebox.showerror("エラー", f"NGリセット入力ピン {reset_p} が重複しています ({used_pins[reset_p]})", parent=self)
                self.temp_data["gpio"]["reset_pin"] = reset_p
            except ValueError:
                return messagebox.showerror("エラー", "NGリセットピン番号が無効です。半角数字で入力してください。", parent=self)
        else:
            self.temp_data["gpio"]["reset_pin"] = None

        # 保存先フォルダのバリデーション (書き込み権限チェック)
        res_dir = self.temp_data["storage"].get("results_dir", "")
        if res_dir:
            try:
                p = Path(res_dir)
                p.mkdir(parents=True, exist_ok=True)
                # テストファイルを書き込んで削除
                test_file = p / f".write_test_{int(time.time())}"
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                messagebox.showerror("バリデーションエラー", 
                    f"出力先フォルダ「{res_dir}」に書き込み権限がないか、パスが無効です。\nエラー: {e}", parent=self)
                return

        self.settings.data = self.temp_data
        self.settings.save_settings()

        if hasattr(self.master, "app_instance"):
            app = self.master.app_instance
            if hasattr(app, "reset_delay_pattern_queue"):
                app.reset_delay_pattern_queue()
            if hasattr(app, "reset_test_outputs"):
                app.reset_test_outputs()

        if hasattr(self, "_test_devices"):
            for dev in self._test_devices.values():
                try:
                    dev.off()
                except Exception:
                    pass
            self._test_devices.clear()

        if hasattr(self, "_live_preview_win") and self._live_preview_win.winfo_exists():
            self._live_preview_win.destroy()
            
        if self.on_close_callback:
            self.on_close_callback()
            
        if hasattr(self.master, "app_instance"):
            app = self.master.app_instance
            app.preview_paused = False

        release_modal_toplevel(self)
        self.destroy()

    def open_gpio_test(self):
        used = set()
        for t in self.temp_data["gpio"]["triggers"]:
            if t["pin"] in used:
                return messagebox.showerror("エラー", f"ピン {t['pin']} が重複しています")
            used.add(t["pin"])
        for s in self.temp_data["gpio"].get("pattern_pins", []):
            if s["pin"] in used:
                return messagebox.showerror("エラー", f"ピン {s['pin']} が重複しています")
            used.add(s["pin"])
        if self.v_ok.get() in used or self.v_ng.get() in used:
            return messagebox.showerror("エラー", "出力ピンが重複しています")
        used.add(self.v_ok.get())
        used.add(self.v_ng.get())

        reset_str = self.v_reset_pin.get().strip()
        reset_p = None
        if reset_str:
            try:
                reset_p = int(reset_str)
                if reset_p in used:
                    return messagebox.showerror("エラー", f"NGリセット入力ピン {reset_p} が重複しています")
                used.add(reset_p)
            except ValueError:
                return messagebox.showerror("エラー", "NGリセットピン番号が無効です")

        test_gpio = {
            "triggers": self.temp_data["gpio"]["triggers"],
            "pattern_pins": self.temp_data["gpio"].get("pattern_pins", []),
            "outputs": {"ok": self.v_ok.get(), "ng": self.v_ng.get()},
            "reset_pin": reset_p
        }
        if hasattr(self.master, "app_instance"):
            app = self.master.app_instance
            if hasattr(app, 'inputs'):
                for d in app.inputs.values():
                    d.close()
            if hasattr(app, 'outputs'):
                for d in app.outputs.values():
                    d.close()
        GPIOTestDialog(self, test_gpio)

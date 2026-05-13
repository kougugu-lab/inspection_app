#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py - ダイアログウィンドウ
  GPIOTestDialog, SettingsDialog
"""

import json
import os
import time
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
from .widgets import create_card, Tooltip, HelpWindow

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


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

        btn_help = tk.Button(btn_f, text="ヘルプ", font=FONT_SET_LBL,
                             bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                             relief="flat", command=self.show_settings_help)
        btn_help.pack(side=tk.LEFT, padx=20)

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        
        # Combobox のドロップダウンリストのフォントを大きく設定
        self.option_add("*TCombobox*Listbox.font", FONT_SET_VAL)

        # Linux/Raspberry Pi (Wayland) でのフォーカス・クリック不良回避のための修正
        self.lift()
        self.focus_force()
        # 画面の描画とOS側への登録が完了するのを待ってから入力を独占する (遅延が重要)
        self.after(200, self.grab_set)

    def on_cancel(self):
        """キャンセル時やウィンドウを閉じた際もプレビュー再開を保証する"""
        if hasattr(self, "_live_preview_win") and self._live_preview_win.winfo_exists():
            self._live_preview_win.destroy()
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
                            "・結果出力先: ログ、CSV、画像一式を保存する親フォルダの場所を絶対パスで指定します。\n"
                            "・判定しきい値: AIの自信がこの数値(0.0~1.0)以上なら「検出した」とみなします。\n"
                            "・最大リトライ: 1回のトリガーで何回まで撮り直すか。撮影モードではこの回数分を全て保存します。\n"
                            "・結果表示時間: 判定後、その画像を画面に表示し続ける秒数です。\n"
                            "・OK/NG出力時間: 信号を何秒間出し続けるかです。NGを空欄にすると「ブザー停止」まで保持します。\n"
                             "・ブザー音パス: 判定時に鳴らす音声ファイルの場所を指定します。",
            "6. 容量監視": "【概要】ディスク容量不足によるシステム停止を防ぐための自動削除設定です。\n"
                           "・自動削除有効: 容量上限を超えた際、古い画像から順に自動削除します。CSVログは削除されません。\n"
                           "・最大容量上限: 指定したGB数を超えると削除を開始します。デフォルトはディスクの全容量です。\n"
                           "・フェイルセーフ: 設定値に関わらず、ディスク全体の空き容量が1GBを切ると強制的に古い画像を削除して空きを作ります。"
        }
        HelpWindow(self, "詳細設定 操作ガイド", help_data)

    def _get_model_classes(self):
        classes = [""]
        if not YOLO_AVAILABLE:
            return classes
        try:
            path = self.temp_data["inference"].get("model_path")
            if path and os.path.exists(path):
                # .ptモデルをロードしてクラス名を取得 (設定画面を開くたびに最新のモデル状態を確認するため)
                model = YOLO(path)
                names = getattr(model, 'names', {})
                if names:
                    classes += sorted(list(names.values()))
        except Exception:
            pass
        return classes

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
                        buttonbackground="#78909C", bd=1, relief="solid")
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

    # ---- カメラタブ ----
    def setup_cam(self):
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
        btn_scan = tk.Button(f_bottom, text="接続カメラを自動検出", font=FONT_BTN_LARGE,
                  bg="#546E7A", fg="white", relief="flat",
                  command=self.scan_cameras)
        btn_scan.pack(side=tk.LEFT, padx=(10, 0))
        Tooltip(btn_scan, "インデックス 0～9 を順に確認し、映像が取れたカメラを自動で一覧表示します")
        tk.Label(f_bottom, textvariable=self._scan_status_var, font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_WARNING).pack(side=tk.LEFT, padx=15)
        self.refresh_cam()

    def refresh_cam(self):
        for w in self.cam_body.winfo_children():
            w.destroy()
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

                l_idx = tk.Label(f, text="インデックス:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL,
                         fg=COLOR_TEXT_MAIN)
                l_idx.grid(row=0, column=2)
                Tooltip(l_idx, "PCが認識しているカメラの番号です（通常は 0, 2, 4...）。映像が映らない場合はこれを変更してください。")
                vi = tk.StringVar(value=str(cam_obj.get("index", 0)))
                sb_idx = self._spinbox(f, vi, 0, 99, 1, width=5, key_path=f"cameras.{idx}.index")
                sb_idx.grid(row=0, column=3, padx=10)

                def _upd_inner(v_n=vn, v_i=vi):
                    try:
                        val = int(v_i.get())
                    except ValueError:
                        val = 0
                    self.temp_data["cameras"][idx].update({"name": v_n.get(), "index": val})

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
            messagebox.showerror("エラー", "正しいカメラインデックスを入力してください。")
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
            self.temp_data["cameras"].append({
                "id": f"cam_{int(time.time())}",
                "name": f"カメラ {next_num}",
                "index": 0
            })
            self.refresh_cam()
            self._mark_changed()

    def del_cam(self, idx):
        self.temp_data["cameras"].pop(idx)
        self.refresh_cam()
        self._mark_changed()

    def scan_cameras(self):
        """バックグラウンドでカメラインデックス 0-9 を探索し、接続されているものを一覧表示する"""
        import threading
        import sys
        self._scan_status_var.set("スキャン中...")

        def _do_scan():
            found = []
            backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
            for idx in range(10):
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if cap and cap.isOpened():
                        ret, _ = cap.read()
                        if ret:
                            found.append(idx)
                    cap.release()
                except Exception:
                    pass
            self.after(0, lambda: _on_found(found))

        def _on_found(found):
            if not self.winfo_exists():
                return
            self._scan_status_var.set(f"検出: {found if found else 'なし'}")
            if not found:
                return
            # 検出されたカメラを設定に追加するか尋ねる
            win = tk.Toplevel(self)
            win.title("検出されたカメラ")
            win.geometry("460x320")
            win.configure(bg=COLOR_BG_MAIN)
            win.transient(self)
            win.grab_set()
            tk.Label(win, text="以下のカメラが検出されました。追加するものを選択してください:",
                     font=FONT_NORMAL, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_MAIN,
                     wraplength=440).pack(pady=(15, 5), padx=15)
            vars_list = []
            for cidx in found:
                v = tk.BooleanVar(value=True)
                cb = tk.Checkbutton(win, text=f"インデックス {cidx}", font=FONT_SET_VAL,
                                    variable=v, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_MAIN,
                                    selectcolor=COLOR_BG_INPUT, activebackground=COLOR_BG_MAIN,
                                    relief="flat")
                cb.pack(anchor="w", padx=30, pady=4)
                vars_list.append((cidx, v))

            def _apply():
                current_indices = {c.get("index") for c in self.temp_data["cameras"]}
                for cidx, v in vars_list:
                    if v.get() and cidx not in current_indices:
                        if len(self.temp_data["cameras"]) < 4:
                            next_n = len(self.temp_data["cameras"]) + 1
                            self.temp_data["cameras"].append({
                                "id": f"cam_{int(time.time())}_{cidx}",
                                "name": f"カメラ {next_n}",
                                "index": cidx
                            })
                self.refresh_cam()
                win.destroy()

            tk.Button(win, text="選択を追加", font=FONT_BOLD, bg=COLOR_OK,
                      fg="black", relief="flat", command=_apply).pack(pady=10)
            tk.Button(win, text="キャンセル", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                      fg=COLOR_TEXT_MAIN, relief="flat", command=win.destroy).pack()

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
            
            # テスト点灯ボタン
            btn = tk.Button(parent, text="テスト点灯", font=FONT_NORMAL, bg="#546E7A", fg="white", relief="flat")
            btn.grid(row=row, column=2, padx=5)
            
            # 状態表示(LED)
            led = tk.Canvas(parent, width=20, height=20, bg=COLOR_BG_PANEL, highlightthickness=0)
            led.grid(row=row, column=3, padx=5)
            circle = led.create_oval(2, 2, 18, 18, fill="#333", outline="#555")
            
            def _toggle_test(v=var, l=led, c=circle, b=btn):
                # app_instance 経由で操作
                app = getattr(self.master, "app_instance", None)
                if not app: return
                pin = 0
                try: pin = int(v.get())
                except: return
                
                # モック的な直接操作 (本来は hardware.py 経由)
                # ここでは簡易的に色だけ変えるテスト
                cur_color = l.itemcget(c, "fill")
                if cur_color == "#333":
                    l.itemconfig(c, fill=COLOR_OK)
                    # 実際の出力をONにする処理が必要ならここ
                else:
                    l.itemconfig(c, fill="#333")
            
            btn.config(command=_toggle_test)

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
                    self.temp_data["gpio"]["triggers"][idx].update({"name": v1.get(), "pin": v2.get()})
                
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
                self.temp_data["gpio"]["pattern_pins"][idx].update({"name": name_var.get(), "pin": pin_var.get()})
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
            p["pin_condition"] = [0] * len(pins)

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
            p["pin_condition"] = [var.get() for var in p_vars]
        for v in p_vars:
            v.trace_add("write", _upd_p_pins)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. トリガー別 判定条件
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        tk.Label(self.pat_body, text="トリガー別 判定条件", font=FONT_SET_LBL,
                 bg=COLOR_BG_MAIN, fg=COLOR_ACCENT).pack(anchor="w", pady=(10, 5))

        # ツールチップ用共通テキスト
        tip_text = "【判定仕様】\n・同じトリガー内の条件はすべて満たす必要があります (AND条件)。\n・検出クラスを空欄にすると、指定カメラの全検出物の合計数で判定します。"

        for t in self.temp_data["gpio"]["triggers"]:
            tid = t["id"]
            if tid not in p["stages"]:
                p["stages"][tid] = {"conditions": {}}
            st = p["stages"][tid]
            
            # 個別カード (灰色枠線)
            cf_outer = tk.Frame(self.pat_body, bg="#808080", padx=1, pady=1)
            cf_outer.pack(fill=tk.X, pady=8)
            cf_inner = tk.Frame(cf_outer, bg=COLOR_BG_PANEL, padx=15, pady=10)
            cf_inner.pack(fill=tk.BOTH, expand=True)

            head_f = tk.Frame(cf_inner, bg=COLOR_BG_PANEL)
            head_f.pack(fill=tk.X)
            tk.Label(head_f, text=f"■ {t['name']}", font=FONT_BOLD,
                     bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
            
            # 条件テーブルコンテナ
            cond_container = tk.Frame(cf_inner, bg=COLOR_BG_PANEL)
            cond_container.pack(fill=tk.X, pady=10)

            def _refresh_conditions(container=cond_container, stage=st, trigger_id=tid):
                for w in container.winfo_children(): w.destroy()
                
                # stage["conditions"] は毎回取り直す (クロージャ問題左回避)
                if not isinstance(stage.get("conditions"), dict):
                    stage["conditions"] = {}
                if isinstance(stage["conditions"], list):
                    # 旧形式からの救済
                    c_id = str(self.temp_data["cameras"][0]["id"]) if self.temp_data["cameras"] else "1"
                    stage["conditions"] = {c_id: stage["conditions"]}
                # 以後は常に stage["conditions"] を直接参照する
                
                # テーブルヘッダー
                header_f = tk.Frame(container, bg=COLOR_BG_PANEL)
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
                    c_conds = stage["conditions"].setdefault(c_id, [])

                    for ci, cond in enumerate(c_conds):
                        def _create_row_ui(cam_obj=c, cid=c_id, idx=ci, cond_obj=cond):
                            row_f = tk.Frame(container, bg=COLOR_BG_PANEL)
                            row_f.pack(fill=tk.X, pady=2)
                            
                            tk.Label(row_f, text=cam_obj["name"], font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=20, anchor="w").pack(side=tk.LEFT, padx=5)
                            
                            cv = tk.StringVar(value=cond_obj.get("class", ""))
                            cb = ttk.Combobox(row_f, textvariable=cv, values=self.model_classes, font=FONT_SET_VAL, width=15, state="readonly")
                            cb.pack(side=tk.LEFT, padx=5)
                            
                            nv = tk.StringVar(value=cond_obj.get("count", "1"))
                            kp = f"patterns.{pid}.stages.{tid}.conditions.{cid}.{idx}"
                            self._spinbox(row_f, nv, 0, 999, 1, width=8, key_path=f"{kp}.count").pack(side=tk.LEFT, padx=5)
                            
                            def _upd_cond(c_dict=cond_obj, v1=cv, v2=nv, w_cb=cb, k_p=kp):
                                c_dict["class"] = v1.get()
                                c_dict["count"] = v2.get()
                                self._mark_changed()
                            
                            cv.trace_add("write", lambda *a, u=_upd_cond: u())
                            nv.trace_add("write", lambda *a, u=_upd_cond: u())
                            
                            def _do_del(cid_target=cid, target_cond=cond_obj, _stage=stage):
                                if cid_target in _stage["conditions"] and target_cond in _stage["conditions"][cid_target]:
                                    _stage["conditions"][cid_target].remove(target_cond)
                                    # パターン全体を再描画することで確実に反映させる
                                    self.on_pat_sel(None)
                                    self._mark_changed()

                            tk.Button(row_f, text="x", font=(FONT_FAMILY, 10, "bold"), bg=COLOR_NG_MUTED, fg="white", relief="flat", width=2,
                                      command=_do_del).pack(side=tk.RIGHT, padx=5)
                        
                        _create_row_ui()

                # 行の追加用ボタンエリア
                add_row_f = tk.Frame(container, bg=COLOR_BG_PANEL)
                add_row_f.pack(fill=tk.X, pady=10)
                
                cam_names = [c["name"] for c in self.temp_data["cameras"]]
                sel_cam_v = tk.StringVar()
                if cam_names: sel_cam_v.set(cam_names[0])
                cb_add = ttk.Combobox(add_row_f, textvariable=sel_cam_v, values=cam_names, state="readonly", width=18, font=FONT_SET_VAL)
                cb_add.pack(side=tk.LEFT, padx=5)

                def _add_cond_row(_stage=stage):
                    c_name = sel_cam_v.get()
                    target_c = next((c for c in self.temp_data["cameras"] if c["name"] == c_name), None)
                    if target_c:
                        c_id = str(target_c["id"])
                        _stage["conditions"].setdefault(c_id, []).append({"class": "", "count": "1"})
                        # パターン全体を再描画することで確実に反映させる
                        self.on_pat_sel(None)
                        self._mark_changed()

                btn_add = tk.Button(add_row_f, text="+ 条件追加", font=FONT_NORMAL, bg=COLOR_ACCENT, fg="black", relief="flat",
                                    command=_add_cond_row)
                btn_add.pack(side=tk.LEFT, padx=5)
                Tooltip(btn_add, tip_text)

            _refresh_conditions()

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
            tk.Label(parent, text=text, font=FONT_SET_VAL,
                     bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT, padx=(2, 0))

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

        # グループ1: AI判定設定
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        g1 = _make_group(scroll_f, "AI判定設定", pady=(16, 4))

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
        # ライブプレビューボタン（スライダー値で即座にYOLO結果を確認）
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


        # 数値パラメータ
        num_params = [
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

        # グループ2: 出力制御
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        g2 = _make_group(scroll_f, "出力制御")

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
            s["ng_output_time"] = float(val) if val else ""
        v_ng_t.trace_add("write", _upd_ng_t)

        # グループ3: ファイルパス
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        g3 = _make_group(scroll_f, "ファイルパス設定")

        # 結果出力先
        r_res = _row_frame(g3)
        _lbl(r_res, "結果出力先フォルダ:", "ログ・CSV・保存画像の親フォルダを絶対パスで指定します。")
        vp = tk.StringVar(value=self.temp_data["storage"].get("results_dir", ""))
        _entry_w(r_res, vp, width=40)
        _browse_btn(r_res, vp, mode="dir")
        vp.trace_add("write", lambda *a: self.temp_data["storage"].update({"results_dir": vp.get()}))

        # AIモデルパス (.pt ファイル または ncnn フォルダ)
        r_mdl = _row_frame(g3)
        _lbl(r_mdl, "AIモデルパス:",
             "推論に使用するYOLOモデルを指定します。\n"
             "・.pt ファイル: 「.pt参照」ボタンでファイルを選択\n"
             "・ncnnモデル: 「ncnnフォルダ参照」ボタンでフォルダを選択")
        vm = tk.StringVar(value=s.get("model_path", ""))
        _entry_w(r_mdl, vm, width=35)
        # .pt ファイル選択ボタン
        _browse_btn(r_mdl, vm, mode="file",
                    filetypes=[("PyTorch モデル", "*.pt"), ("すべてのファイル", "*.*")])
        # ncnn フォルダ選択ボタン
        def _pick_ncnn():
            p = filedialog.askdirectory(title="ncnnモデルフォルダを選択", parent=self)
            if p:
                vm.set(p)
        btn_ncnn = tk.Button(r_mdl, text="ncnnフォルダ", font=FONT_NORMAL,
                             bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                             relief="flat", padx=6, pady=2, cursor="hand2",
                             command=_pick_ncnn)
        btn_ncnn.pack(side=tk.LEFT, padx=(4, 0))
        Tooltip(btn_ncnn, "ncnn形式のモデルフォルダ(*.ncnnディレクトリ)を選択します")
        vm.trace_add("write", lambda *a: s.update({"model_path": vm.get()}))

        # グループ4: 音声設定
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        g4 = _make_group(scroll_f, "音声設定")

        # NGブザー
        r_bng = _row_frame(g4)
        _lbl(r_bng, "NG時ブザー音:", "NG判定時に再生する音声ファイルです。空欄で無効。")
        vb = tk.StringVar(value=s.get("buzzer_path", ""))
        _entry_w(r_bng, vb, width=35)
        _browse_btn(r_bng, vb, mode="file",
                    filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべて", "*.*")])
        _play_btn(r_bng, vb)
        vb.trace_add("write", lambda *a: s.update({"buzzer_path": vb.get()}))

        # OKブザー
        r_bok = _row_frame(g4)
        _lbl(r_bok, "OK時ブザー音:", "OK判定時に再生する音声ファイルです。空欄で無効。")
        vob = tk.StringVar(value=s.get("ok_buzzer_path", ""))
        _entry_w(r_bok, vob, width=35)
        _browse_btn(r_bok, vob, mode="file",
                    filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべて", "*.*")])
        _play_btn(r_bok, vob)
        vob.trace_add("write", lambda *a: s.update({"ok_buzzer_path": vob.get()}))

        # グループ5: 容量監視（自動削除）
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        g5 = _make_group(scroll_f, "容量監視 / 自動削除")

        st = self.temp_data["storage"]

        r_ad = _row_frame(g5)
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

        r_mg = _row_frame(g5)
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

        # 現在の使用量表示 (非同期計算)
        v_used = tk.StringVar(value="現在の使用量: 計算中...")
        lbl_used = tk.Label(g5, textvariable=v_used, font=FONT_SET_VAL,
                            bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, anchor="w")
        lbl_used.pack(fill=tk.X, pady=(4, 0))

        def _calc_storage():
            import shutil as _shutil
            _res_dir = st.get("results_dir", "")
            try:
                if _res_dir and os.path.exists(_res_dir):
                    # 大量ファイル走査のためスレッド実行
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

    # ---- 保存 / GPIO テスト ----

    def validate_pins(self):
        """BCMピンのバリデーション（有効範囲＋トリガー/パターン切替/OK/NG出力間の重複禁止）"""
        used_pins = {}

        # 保存直前に UI の出力ピン変数を temp_data へ反映（このメソッド単体でも正しく検証できるようにする）
        try:
            if hasattr(self, "v_ok") and hasattr(self, "v_ng"):
                self.temp_data["gpio"]["outputs"]["ok"] = self.v_ok.get()
                self.temp_data["gpio"]["outputs"]["ng"] = self.v_ng.get()
        except tk.TclError:
            messagebox.showerror("バリデーションエラー",
                                 "OK/NG 出力のピン番号には整数を入力してください。", parent=self)
            return False

        def _get_val(v):
            """str/intを確実にintに変換。無効なら-1を返す"""
            if v is None:
                return -1
            s_val = str(v).strip()
            if not s_val:
                return -1
            try:
                return int(s_val)
            except ValueError:
                return -1

        # (BCM番号, 用途ラベル) — 重複は入力同士・入出力のいずれも不可
        roles = []
        for t in self.temp_data["gpio"]["triggers"]:
            roles.append((_get_val(t["pin"]), f'トリガー入力「{t.get("name", "")}」'))
        for s in self.temp_data["gpio"].get("pattern_pins", []):
            roles.append((_get_val(s["pin"]), f'パターン切替「{s.get("name", "")}」'))
        outputs = self.temp_data["gpio"]["outputs"]
        roles.append((_get_val(outputs["ok"]), "OK出力"))
        roles.append((_get_val(outputs["ng"]), "NG出力"))

        for p, label in roles:
            if p == -1:
                messagebox.showerror("バリデーションエラー",
                                     f"{label} のBCM番号が未入力または無効です。", parent=self)
                return False
            if p not in VALID_BCM_PINS:
                messagebox.showerror(
                    "バリデーションエラー",
                    f"{label} のBCM番号 {p} は利用できません（有効なBCMのみ指定してください）。\n"
                    f"有効なBCM: {sorted(VALID_BCM_PINS)}",
                    parent=self,
                )
                return False
            if p in used_pins:
                messagebox.showerror(
                    "バリデーションエラー",
                    "同じBCM番号を、トリガー入力・パターン切替・OK出力・NG出力のうち複数に割り当てることはできません。\n"
                    "（入出力でピンを兼用すると、検査動作が不安定になることがあります。）\n\n"
                    f"重複しているBCM: {p}\n"
                    f"・{used_pins[p]}\n"
                    f"・{label}",
                    parent=self,
                )
                return False
            used_pins[p] = label

        return True

    def save_and_close(self):
        # 編集中のEntry内容を変数へ確実に反映させる（保存直前にフォーカスを外す）
        try:
            self.update_idletasks()
            self.focus_set()
        except tk.TclError:
            pass

        # バリデーション前に最新の出力ピン設定を同期
        try:
            self.temp_data["gpio"]["outputs"]["ok"] = self.v_ok.get()
            self.temp_data["gpio"]["outputs"]["ng"] = self.v_ng.get()
        except tk.TclError:
            messagebox.showerror("バリデーションエラー", "出力ピンには数値を入力してください", parent=self)
            return

        # 基本的なピンのバリデーション
        if not self.validate_pins():
            return

        # バリデーション: 全パターンの入力ピン条件が重複していないかチェック
        pin_map = {} # { tuple_condition: [pattern_names] }
        for pid, p in self.temp_data["patterns"].items():
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
        
        if hasattr(self, "_live_preview_win") and self._live_preview_win.winfo_exists():
            self._live_preview_win.destroy()
            
        if self.on_close_callback:
            self.on_close_callback()
            
        if hasattr(self.master, "app_instance"):
            app = self.master.app_instance
            app.preview_paused = False
            
        self.destroy()

    def open_gpio_test(self):
        try:
            self.update_idletasks()
            self.focus_set()
        except tk.TclError:
            pass
        try:
            self.temp_data["gpio"]["outputs"]["ok"] = self.v_ok.get()
            self.temp_data["gpio"]["outputs"]["ng"] = self.v_ng.get()
        except tk.TclError:
            messagebox.showerror("エラー", "出力ピンには数値を入力してください", parent=self)
            return
        if not self.validate_pins():
            return

        test_gpio = {
            "triggers": self.temp_data["gpio"]["triggers"],
            "pattern_pins": self.temp_data["gpio"].get("pattern_pins", []),
            "outputs": {"ok": self.v_ok.get(), "ng": self.v_ng.get()}
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

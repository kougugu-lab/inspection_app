#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - メインアプリケーション (InspectionSystem)
"""

import cv2
import threading
import time
import datetime
import logging
import queue
import os
import sys
import random
import csv
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
from pathlib import Path

from .constants import (
    RESULTS_DIR, RESULTS_SUBDIR_NG_RAW, COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT, COLOR_OK, COLOR_NG, COLOR_WARNING,
    FONT_BOLD, FONT_LARGE, FONT_HUGE, FONT_NORMAL, FONT_FAMILY, VERSION
)
from .hardware import DigitalInputDevice, OutputDevice, is_gpio_available, MockManager
from .settings import SettingsManager
from .widgets import create_card, Tooltip, HelpWindow, TenKeyDialog
from .dialogs import SettingsDialog

try:
    import pygame
    PYGAME_AVAILABLE = True
    # pygame.mixer.init() は起動時に行わず、初回使用時に遅延初期化する
except ImportError:
    PYGAME_AVAILABLE = False


def _ensure_mixer():
    """pygame.mixerを初回使用時に遅延初期化する (起動時間短縮)"""
    if PYGAME_AVAILABLE and not pygame.mixer.get_init():
        try:
            pygame.mixer.init()
        except Exception:
            pass

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


class InspectionSystem:
    def __init__(self):
        self.settings = SettingsManager()
        self.setup_dirs()
        self.setup_logging()

        self.commit_number = 1
        self.ng_history = []
        self.running = True
        self.camera_lock = threading.Lock()
        self.trigger_queue = queue.Queue()
        self.caps = {}
        self.last_frames = {}  # 各カメラの最新フレーム保持用 (設定画面のプレビュー等で使用)
        self.inputs = {}
        self.outputs = {}
        self.out_ok = None
        self.out_ng = None

        # サイクル管理用の状態変数
        self.cycle_active_pat_id = None  # 現在のサイクルで固定されたパターンID
        self.cycle_fired_trigs = set()   # 現在のサイクルで実行済みのトリガーID
        self.cycle_trig_idx = 0          # 現在待ち受けているトリガーのインデックス

        # 結果表示用の状態変数
        self.result_display_frames = {}   # {cam_id: tk_image} 判定結果の固定表示用
        self.result_display_until = 0     # 固定表示の終了時刻
        self.preview_paused = False       # 設定画面表示中などにプレビューを一時停止するフラグ

        self.model = None
        self.load_model()

        self.setup_hardware()
        self.setup_gui()

        # モックモードの場合、仮想GPIOパネルを表示 (Windows環境のみ)
        if sys.platform == "win32" and not is_gpio_available():
            self.setup_mock_ui()
        
        # 起動時にコミット番号入力を表示 (少し遅らせて他のGUIが整うのを待つ)
        self.root.after(500, self.manual_commit_set_initial)
        # 起動30秒後から容量監視を開始（以降10分おきに自動実行）
        self.root.after(30 * 1000, self._monitor_storage)

    def setup_mock_ui(self):
        """モックモード時の仮想GPIO操作パネル (Windowsデバッグ用)"""
        try:
            self.mock_root = tk.Toplevel(self.root)
            self.mock_root.title("仮想GPIOパネル")
            self.mock_root.geometry("400x750")
            self.mock_root.configure(bg=COLOR_BG_MAIN)
            self.mock_root.attributes("-topmost", True)
            self.mock_root.resizable(False, False)

            container = tk.Frame(self.mock_root, bg=COLOR_BG_MAIN, padx=20, pady=20)
            container.pack(fill=tk.BOTH, expand=True)

            # --- 入力 (トリガー) ---
            outer_t, inner_t = create_card(container, "仮想入力 (トリガー)")
            outer_t.pack(fill=tk.X, pady=(0, 15))

            for t in self.settings.data["gpio"]["triggers"]:
                btn = tk.Button(inner_t, text=f"{t['name']} (ピン {t['pin']})",
                                font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                                activebackground=COLOR_ACCENT, activeforeground="black",
                                relief="flat", cursor="hand2",
                                command=lambda p=t['pin']: self._pulse_mock_input(p))
                btn.pack(fill=tk.X, pady=4)
                Tooltip(btn, "ボタンを押している間だけ入力がONになります")

            # --- 入力 (パターン決定ピン) ---
            outer_p, inner_p = create_card(container, "仮想入力 (パターン決定)")
            outer_p.pack(fill=tk.X, pady=(0, 15))

            self.mock_selectors = {}
            for s in self.settings.data["gpio"].get("pattern_pins", []):
                f = tk.Frame(inner_p, bg=COLOR_BG_PANEL)
                f.pack(fill=tk.X, pady=2)
                
                var = tk.BooleanVar(value=MockManager.get_input_state(s['pin']))
                cb = tk.Checkbutton(f, text=f"{s['name']} (ピン {s['pin']})",
                                    font=FONT_NORMAL, variable=var, 
                                    bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                                    selectcolor=COLOR_BG_INPUT, activebackground=COLOR_BG_PANEL,
                                    activeforeground=COLOR_TEXT_MAIN, relief="flat",
                                    command=lambda p=s['pin'], v=var: MockManager.set_input(p, v.get()))
                cb.pack(side=tk.LEFT)
                self.mock_selectors[s['pin']] = var

            # --- 出力 (ステータス) ---
            outer_s, inner_s = create_card(container, "仮想出力")
            outer_s.pack(fill=tk.X)

            self.mock_indicators = {}
            for name, pin, color in [("OK出力", self.settings.data["gpio"]["outputs"]["ok"], COLOR_OK),
                                     ("NG出力", self.settings.data["gpio"]["outputs"]["ng"], COLOR_NG)]:
                f = tk.Frame(inner_s, bg=COLOR_BG_PANEL)
                f.pack(fill=tk.X, pady=8)
                
                lbl = tk.Label(f, text=name, font=FONT_NORMAL, bg=COLOR_BG_PANEL, 
                               fg=COLOR_TEXT_MAIN, width=12, anchor="w")
                lbl.pack(side=tk.LEFT)
                
                # インジケータ (外枠と中身)
                outer_ind = tk.Frame(f, width=24, height=24, bg=COLOR_BG_INPUT, padx=2, pady=2)
                outer_ind.pack(side=tk.RIGHT)
                outer_ind.pack_propagate(False)

                ind = tk.Frame(outer_ind, bg="#444")
                ind.pack(fill=tk.BOTH, expand=True)
                self.mock_indicators[str(pin)] = (ind, color)

            # 凡例・閉じる
            low_f = tk.Frame(container, bg=COLOR_BG_MAIN)
            low_f.pack(fill=tk.X, pady=(20, 0))
            tk.Label(low_f, text="※Windowsデバッグ専用機能です", 
                     font=(FONT_FAMILY, 9), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack()

            self._update_mock_ui()
        except Exception as e:
            self.logger.error(f"仮想GPIOパネル初期化エラー: {e}")

    def _pulse_mock_input(self, pin):
        """仮想入力を一瞬ONにする"""
        def _pulse():
            MockManager.set_input(pin, True)
            time.sleep(0.2)
            MockManager.set_input(pin, False)
        threading.Thread(target=_pulse, daemon=True).start()

    def _update_mock_ui(self):
        """出力状態を周期的に確認してUIを更新"""
        try:
            if not hasattr(self, "mock_indicators") or not hasattr(self, "mock_root"):
                return
            
            if not self.mock_root.winfo_exists():
                return
            
            # 出力状態の更新
            for pin, ind_data in self.mock_indicators.items():
                ind, color = ind_data
                state = MockManager.get_output_state(pin)
                ind.configure(bg=color if state else "#444")
            
            # セレクター状態の同期 (外部からの変更に対応する場合)
            if hasattr(self, "mock_selectors"):
                for pin, var in self.mock_selectors.items():
                    current = MockManager.get_input_state(pin)
                    if var.get() != current:
                        var.set(current)
            
            self.mock_root.after(200, self._update_mock_ui)
        except Exception as e:
            self.logger.error(f"仮想GPIOパネル更新エラー: {e}")

    def load_model(self):
        """設定されたパスからYOLOモデルをロードする (.pt / ncnn フォルダ両対応)"""
        if not YOLO_AVAILABLE:
            self.logger.warning("ultralytics がインストールされていないため、推論はシミュレーションモードで動作します。")
            return

        model_path = self.settings.data["inference"].get("model_path")
        if not model_path:
            self.logger.warning("モデルパスが設定されていません。シミュレーションモードで動作します。")
            return

        # ncnn モデルはフォルダ (*.ncnn) または .param/.bin ファイルが入ったディレクトリ
        path_obj = Path(model_path)
        path_exists = path_obj.exists() or (path_obj.is_dir())
        if not path_exists:
            self.logger.warning(f"モデルファイルが見つからないため、シミュレーションモードで動作します: {model_path}")
            return

        try:
            self.model = YOLO(model_path)
            fmt = "ncnn" if path_obj.is_dir() or str(model_path).endswith(".ncnn") else "pt"
            self.logger.info(f"YOLOモデルをロードしました ({fmt}): {model_path}")
        except Exception as e:
            self.logger.error(f"YOLOモデルのロードに失敗しました: {e}")

    def get_results_dir(self):
        """設定から結果保存ディレクトリを取得する"""
        path_str = self.settings.data["storage"].get("results_dir", str(RESULTS_DIR))
        return Path(path_str)

    # ------------------------------------------------------------------
    # 初期設定
    # ------------------------------------------------------------------
    def setup_dirs(self):
        try:
            res_dir = self.get_results_dir()
            # 大元のディレクトリを確実に作成する
            res_dir.mkdir(parents=True, exist_ok=True)
            for d in ["OK", "NG", "SKIP", "REC"]:
                (res_dir / "images" / d).mkdir(parents=True, exist_ok=True)
            (res_dir / "logs").mkdir(parents=True, exist_ok=True)
            (res_dir / "csv").mkdir(parents=True, exist_ok=True)
            print(f"ディレクトリ構成を確認しました: {res_dir}")
        except Exception as e:
            # GUI起動前なのでprintも使用
            msg = f"ディレクトリ生成エラー: {e}\n現在の結果出力先: {self.get_results_dir()}"
            print(msg)
            if hasattr(self, 'logger'):
                self.logger.error(msg)

    def setup_logging(self):
        res_dir = self.get_results_dir()
        log_f = res_dir / "logs" / f"app_{datetime.datetime.now().strftime('%Y%m%d')}.log"
        # 既存のハンドラをクリアして再設定可能にする
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            handlers=[
                logging.FileHandler(log_f, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def setup_hardware(self):
        try:
            if hasattr(self, 'inputs'):
                for d in self.inputs.values():
                    d.close()
            if hasattr(self, 'outputs'):
                for d in self.outputs.values():
                    d.close()
            for c in self.caps.values():
                c.release()

            self.inputs = {}
            self.outputs = {}
            self.caps = {}
            data = self.settings.data

            for t in data["gpio"]["triggers"]:
                dev = DigitalInputDevice(t["pin"], pull_up=True)
                dev.when_activated = lambda i=t["id"]: self.trigger_queue.put(i)
                self.inputs[t["id"]] = dev

            for s in data["gpio"].get("pattern_pins", []):
                self.inputs[f"sel_{s['id']}"] = DigitalInputDevice(s["pin"], pull_up=True)

            self.out_ok = OutputDevice(data["gpio"]["outputs"]["ok"])
            self.out_ng = OutputDevice(data["gpio"]["outputs"]["ng"])
            self.outputs = {"ok": self.out_ok, "ng": self.out_ng}

            cap_res = data["storage"]["capture_res"]

            def _open_camera(c, cap_res):
                """カメラを並列で開く (起動時間短縮)"""
                try:
                    backend = cv2.CAP_V4L2 if sys.platform.startswith('linux') else cv2.CAP_ANY
                    cap = cv2.VideoCapture(int(c["index"]), backend)
                    if cap and cap.isOpened():
                        w, h = map(int, cap_res.split('x'))
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                        with self.camera_lock:
                            self.caps[c["id"]] = cap
                        self.logger.info(f"カメラ(インデックス {c['index']})を初期化しました: {c['name']}")
                    else:
                        self.logger.error(f"カメラ(インデックス {c['index']})を開けませんでした")
                except Exception as e:
                    self.logger.error(f"カメラ初期化エラー (インデックス {c['index']}): {e}")

            # 複数カメラを並列オープン
            cam_threads = [threading.Thread(target=_open_camera, args=(c, cap_res), daemon=True)
                           for c in data["cameras"]]
            for t in cam_threads:
                t.start()
            for t in cam_threads:
                t.join(timeout=5.0)  # 最大5秒待機
        except Exception as e:
            self.logger.error(f"ハードウェアエラー: {e}")

    def get_current_pattern(self):
        st = []
        for s in self.settings.data["gpio"].get("pattern_pins", []):
            pin_id = f"sel_{s['id']}"
            if pin_id in self.inputs:
                st.append(1 if self.inputs[pin_id].is_active else 0)
            else:
                st.append(0)
        for pid in self.settings.data["pattern_order"]:
            p = self.settings.data["patterns"][pid]
            if p.get("pin_condition") == st:
                return pid
        return None

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------
    def setup_gui(self):
        self.root = tk.Tk()
        self.root.title(f"自動検査システム {VERSION}")
        self.root.geometry("1400x900")
        self.root.configure(bg=COLOR_BG_MAIN)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ウィンドウ最大化（Windows/Raspi(X11)/Wayland対応）
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")          # Windows向け
        except tk.TclError:
            try:
                # ラズパイ(X11)向け: タスクバーを隠さずに最大化
                self.root.attributes('-zoomed', True)
            except tk.TclError:
                # Wayland (Bookworm等) またはその他のフォールバック
                # 画面サイズを取得してジオメトリに設定
                w = self.root.winfo_screenwidth()
                h = self.root.winfo_screenheight()
                self.root.geometry(f"{w}x{h}+0+0")

        # --- ヘッダー ---
        self.header = tk.Frame(self.root, bg=COLOR_BG_PANEL, height=80)
        self.header.pack(fill=tk.X)

        self.lbl_status = tk.Label(self.header, text="システム待機中", font=FONT_LARGE,
                                   bg=COLOR_BG_PANEL, fg=COLOR_ACCENT)
        self.lbl_status.pack(side=tk.LEFT, padx=30, pady=15)

        self.lbl_clock = tk.Label(self.header, text="", font=FONT_LARGE,
                                  bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
        self.lbl_clock.pack(side=tk.RIGHT, padx=30)
        self.update_clock()

        btn_help = tk.Button(self.header, text="？", font=FONT_BOLD,
                             bg=COLOR_BG_INPUT, fg=COLOR_ACCENT,
                             relief="flat", width=3,
                             command=self.show_main_help)
        btn_help.pack(side=tk.RIGHT, padx=10)
        Tooltip(btn_help, "操作方法を表示します")

        # --- モード切り替えボタン ---
        mode_frm = tk.Frame(self.header, bg=COLOR_BG_PANEL)
        mode_frm.pack(side=tk.RIGHT, padx=20)

        self.v_mode = tk.StringVar(
            value=self.settings.data["inference"].get("mode", "inspection"))

        def _set_mode(m):
            self.v_mode.set(m)
            self.settings.data["inference"]["mode"] = m
            self.settings.save_settings()
            self.update_mode_ui()

        self.btn_insp = tk.Button(mode_frm, text="検査モード", font=FONT_BOLD,
                                  width=12, relief="flat",
                                  command=lambda: _set_mode("inspection"))
        self.btn_insp.pack(side=tk.LEFT, padx=5)

        self.btn_rec = tk.Button(mode_frm, text="撮影モード", font=FONT_BOLD,
                                 width=12, relief="flat",
                                 command=lambda: _set_mode("recording"))
        self.btn_rec.pack(side=tk.LEFT, padx=5)

        self.update_mode_ui()

        # --- メインコンテンツ ---
        main = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        main.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # カメラプレビュー
        # pack_propagate(False) により、プレビュー画像のサイズに引っ張られてカメラエリアが膨張するのを抑止する
        self.v_frm_outer, v_frm_inner = create_card(main, "カメラプレビュー")
        self.v_frm_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.v_frm_outer.pack_propagate(False)
        self.cam_labels = {}

        self.v_frm = tk.Frame(v_frm_inner, bg=COLOR_BG_PANEL)
        self.v_frm.pack(fill=tk.BOTH, expand=True)

        cams = self.settings.data["cameras"]
        rows = 2 if len(cams) > 2 else 1
        cols = 2 if len(cams) >= 2 else 1

        for i, c in enumerate(cams):
            f = tk.LabelFrame(self.v_frm, text=c["name"], font=FONT_BOLD,
                              bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, bd=1, relief="solid")
            f.grid(row=i // cols, column=i % cols, sticky="nsew", padx=5, pady=5)
            l = tk.Label(f, bg="black")
            l.pack(fill=tk.BOTH, expand=True)
            self.cam_labels[c["id"]] = l

        for i in range(rows):
            self.v_frm.rowconfigure(i, weight=1)
        for i in range(cols):
            self.v_frm.columnconfigure(i, weight=1)

        # 操作パネル
        pnl_outer, pnl = create_card(main, "操作パネル")
        pnl_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(20, 0))
        pnl_outer.config(width=420)
        pnl_outer.pack_propagate(False)

        tk.Label(pnl, text="コミット番号", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(5, 2))
        cf = tk.Frame(pnl, bg=COLOR_BG_PANEL)
        cf.pack(pady=5)
        tk.Button(cf, text="－", font=FONT_LARGE, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, width=3, relief="flat",
                  command=lambda: self.adjust_commit(-1)).pack(side=tk.LEFT)
        self.v_commit = tk.StringVar(value="0001")
        tk.Label(cf, textvariable=self.v_commit, font=FONT_HUGE,
                 bg=COLOR_BG_INPUT, fg=COLOR_ACCENT, width=5).pack(side=tk.LEFT, padx=10)
        tk.Button(cf, text="＋", font=FONT_LARGE, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, width=3, relief="flat",
                  command=lambda: self.adjust_commit(1)).pack(side=tk.LEFT)
        tk.Button(pnl, text="番号入力", font=FONT_NORMAL, bg="#546E7A",
                  fg="white", relief="flat",
                  command=self.manual_commit_set).pack(fill=tk.X, padx=10, pady=5)

        tk.Label(pnl, text="現在パターン", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(10, 2))
        self.v_pat_name = tk.StringVar(value="---")
        tk.Label(pnl, textvariable=self.v_pat_name, font=FONT_LARGE,
                 bg=COLOR_BG_INPUT, fg=COLOR_ACCENT, pady=5).pack(fill=tk.X, padx=10)

        tk.Label(pnl, text="NG履歴 (ダブルクリックで確認)", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(10, 2))
        h_frm = tk.Frame(pnl, bg=COLOR_BG_PANEL)
        h_frm.pack(fill=tk.BOTH, expand=True, padx=10)

        self.lb_history = tk.Listbox(h_frm, font=(FONT_FAMILY, 14),
                                     bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                                     selectbackground=COLOR_ACCENT,
                                     selectforeground="black", relief="flat")
        self.lb_history.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lb_history.bind("<Double-Button-1>", self.on_history_double_click)

        sb = tk.Scrollbar(h_frm, orient=tk.VERTICAL, command=self.lb_history.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_history.config(yscrollcommand=sb.set)

        hist_btn_frm = tk.Frame(pnl, bg=COLOR_BG_PANEL)
        hist_btn_frm.pack(fill=tk.X, padx=10, pady=5)
        tk.Button(hist_btn_frm, text="履歴リセット", font=FONT_NORMAL, bg="#546E7A",
                  fg="white", relief="flat",
                  command=self.clear_history).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(hist_btn_frm, text="結果フォルダ", font=FONT_NORMAL, bg="#546E7A",
                  fg="white", relief="flat",
                  command=self.open_results_folder).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        tk.Button(pnl, text="ブザー停止", font=FONT_BOLD, bg=COLOR_NG,
                  fg="white", height=2, relief="flat",
                  command=self.stop_buzzer).pack(fill=tk.X, padx=10, pady=(15, 5))
        tk.Button(pnl, text="詳細設定", font=FONT_BOLD, bg="#455A64",
                  fg="white", height=2, relief="flat",
                  command=self.open_settings).pack(fill=tk.X, padx=10, pady=5)

        # アプリインスタンスの保持とループ開始
        self.root.app_instance = self
        threading.Thread(target=self._preview_loop, daemon=True).start()
        threading.Thread(target=self._main_logic_loop, daemon=True).start()

    def on_closing(self):
        """アプリケーション終了時のリソース解放と安全なシャットダウン"""
        if messagebox.askokcancel("終了", "アプリケーションを終了しますか？"):
            self.running = False
            self.logger.info("シャットダウン処理を開始します...")

            # 仮想GPIOモックの終了 (使用時)
            if hasattr(self, "mock_root") and self.mock_root.winfo_exists():
                try:
                    self.mock_root.destroy()
                except Exception: pass

            # GPIOリソースの解放
            try:
                for d in getattr(self, 'inputs', {}).values():
                    if hasattr(d, 'close'): d.close()
                for d in getattr(self, 'outputs', {}).values():
                    if hasattr(d, 'close'): d.close()
            except Exception as e:
                self.logger.error(f"GPIO解放エラー: {e}")

            # カメラリソースの解放
            try:
                for c in getattr(self, 'caps', {}).values():
                    c.release()
            except Exception as e:
                self.logger.error(f"カメラ解放エラー: {e}")

            if PYGAME_AVAILABLE:
                try:
                    pygame.mixer.quit()
                except Exception: pass

            self.root.destroy()
            self.logger.info("シャットダウン完了")


    def update_clock(self):
        now = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        self.lbl_clock.config(text=now)
        self.root.after(1000, self.update_clock)

    def _monitor_storage(self):
        """保存フォルダの容量を確認し、上限を超えた場合は古い画像から削除する（10分おき・非同期）"""
        _INTERVAL_MS = 10 * 60 * 1000  # 10分
        
        def _thread_task():
            try:
                st = self.settings.data.get("storage", {})
                if not st.get("auto_delete_enabled", False):
                    return

                max_gb = float(st.get("max_results_gb", 0))
                if max_gb <= 0:
                    return

                res_dir = Path(self.get_results_dir())
                images_dir = res_dir / "images"
                if not images_dir.exists():
                    return

                # 全画像ファイルを更新日時昇順（古い順）でリストアップ
                # ※ 大量のファイル走査が発生するため、このスレッド内で実行
                img_files = sorted(
                    [f for f in images_dir.rglob("*") if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")],
                    key=lambda f: f.stat().st_mtime
                )
                total_size = sum(f.stat().st_size for f in img_files)

                # ディスクの空き容量をチェック
                import shutil
                usage = shutil.disk_usage(res_dir)
                free_gb = usage.free / (1024 ** 3)
                max_bytes = max_gb * (1024 ** 3)

                needs_deletion = False
                target_bytes = total_size

                # (1) 画像フォルダの総使用量が設定上限を超過している場合
                if total_size > max_bytes:
                    needs_deletion = True
                    target_bytes = max_bytes * 0.9  # 上限の90%まで減らす
                # (2) ディスク全体の空き容量が 1.0 GB を切った場合（フェイルセーフ）
                elif free_gb < 1.0:
                    needs_deletion = True
                    # 空きを増やすため、現在の画像フォルダサイズから 1GB 分減らす
                    target_bytes = max(0, total_size - (1.0 * 1024**3))

                if not needs_deletion:
                    return

                self.logger.info(f"[容量監視] 削除開始。画像サイズ: {total_size/(1024**3):.2f} GB / 空き容量: {free_gb:.2f} GB")

                deleted_count = 0
                for f in img_files:
                    if total_size <= target_bytes:
                        break
                    try:
                        file_size = f.stat().st_size
                        f.unlink()
                        total_size -= file_size
                        deleted_count += 1
                    except Exception as e:
                        self.logger.warning(f"[容量監視] 削除失敗: {f.name} - {e}")

                if deleted_count > 0:
                    self.logger.info(f"[容量監視] {deleted_count} 件削除完了。残画像サイズ: {total_size/(1024**3):.2f} GB")
            except Exception as e:
                self.logger.error(f"[容量監視] エラー: {e}")
            finally:
                # 終わったら次のタイマーをセット (スレッド内からでも root.after は安全に呼べる)
                if self.running:
                    self.root.after(_INTERVAL_MS, self._monitor_storage)

        # 非同期実行
        t = threading.Thread(target=_thread_task, daemon=True)
        t.start()

    def adjust_commit(self, delta):
        self.commit_number += delta
        if self.commit_number > 9999:
            self.commit_number = 1
        elif self.commit_number < 1:
            self.commit_number = 9999
        self.v_commit.set(f"{self.commit_number:04d}")

    def manual_commit_set(self):
        d = TenKeyDialog(self.root, "コミット番号設定", self.commit_number)
        if d.result is not None:
            self.commit_number = d.result
            self.v_commit.set(f"{self.commit_number:04d}")

    def manual_commit_set_initial(self):
        try:
            d = TenKeyDialog(self.root, "開始コミット番号", self.commit_number)
            if d.result is not None:
                self.commit_number = d.result
                self.v_commit.set(f"{self.commit_number:04d}")
        except Exception as e:
            self.logger.error(f"初期コミット番号設定エラー: {e}")

    def stop_buzzer(self):
        """NG出力・ブザーを停止する"""
        if self.out_ng:
            self.out_ng.off()
        if PYGAME_AVAILABLE and pygame.mixer.get_init():
            pygame.mixer.music.stop()

    def clear_history(self):
        if messagebox.askyesno("確認", "NG履歴を削除しますか？"):
            self.ng_history.clear()
            self.lb_history.delete(0, tk.END)

    def open_results_folder(self):
        """結果画像フォルダをOSのファイルマネージャーで開く"""
        folder = self.get_results_dir() / "images"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))
            elif sys.platform.startswith("linux"):
                import subprocess
                subprocess.Popen(["xdg-open", str(folder)])
            else:
                import subprocess
                subprocess.Popen(["open", str(folder)])
        except Exception as e:
            self.logger.error(f"結果フォルダを開けませんでした: {e}")
            messagebox.showerror("エラー", f"フォルダを開けませんでした:\n{folder}", parent=self.root)

    def on_history_double_click(self, e):
        s = self.lb_history.curselection()
        if not s:
            return
        rec = self.ng_history[len(self.ng_history) - 1 - s[0]]
        res_dir = self.get_results_dir()
        dir_ng = res_dir / "images" / "NG"

        # 同コミット番号の全ファイルを対象に絞り込む
        all_imgs = sorted(dir_ng.glob(f"NG_{rec['commit']:04d}_*"))

        # NG発生時刻が記録されていれば、同じ日付のファイルのみに絞る
        rec_time = rec.get("time")
        if rec_time is not None:
            rec_date = rec_time.date()
            imgs = [
                f for f in all_imgs
                if datetime.datetime.fromtimestamp(f.stat().st_mtime).date() == rec_date
            ]
            # フィルタ後が0件（日をまたいだ保存等）のときは全件表示
            if not imgs:
                imgs = all_imgs
        else:
            imgs = all_imgs

        if not imgs:
            messagebox.showinfo("情報", f"#{rec['commit']:04d} の画像ファイルが見つかりません。\n保存先: {dir_ng}")
            return

        # ---- スクロール対応の大きな画像ビューワー ----
        top = tk.Toplevel(self.root)
        top.title(f"NG詳細 #{rec['commit']:04d} ({len(imgs)}枚)")
        top.configure(bg=COLOR_BG_MAIN)
        top.transient(self.root)

        # ウィンドウサイズを画面の85%に設定
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = int(sw * 0.85)
        win_h = int(sh * 0.85)
        top.geometry(f"{win_w}x{win_h}")

        # タイトルラベル
        tk.Label(top, text=f"NG #{rec['commit']:04d} — {len(imgs)}枚", font=FONT_LARGE,
                 bg=COLOR_BG_MAIN, fg=COLOR_NG).pack(pady=(10, 0))

        # スクロール可能フレーム
        frame_outer = tk.Frame(top, bg=COLOR_BG_MAIN)
        frame_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas_scroll = tk.Canvas(frame_outer, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = tk.Scrollbar(frame_outer, orient=tk.VERTICAL, command=canvas_scroll.yview)
        canvas_scroll.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas_scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas_scroll, bg=COLOR_BG_MAIN)
        canvas_scroll.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        # マウスホイールでスクロール
        def _on_mousewheel(event):
            canvas_scroll.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas_scroll.bind_all("<MouseWheel>", _on_mousewheel)
        top.bind("<Destroy>", lambda e: canvas_scroll.unbind_all("<MouseWheel>"))

        # 画像を縦に並べて表示
        img_max_w = int(win_w * 0.82)
        img_max_h = int(sh * 0.65)

        for f in imgs:
            try:
                im = Image.open(f)
                im.thumbnail((img_max_w, img_max_h), Image.LANCZOS)
                t_im = ImageTk.PhotoImage(im)

                # ファイル名ラベル
                tk.Label(inner, text=f.name, font=FONT_NORMAL,
                         bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack(anchor="w", padx=10, pady=(10, 2))
                # 画像ラベル
                lbl = tk.Label(inner, image=t_im, bg=COLOR_BG_MAIN)
                lbl.image = t_im  # 参照保持
                lbl.pack(padx=10, pady=(0, 5))
            except Exception as ex:
                self.logger.error(f"NG画像読み込みエラー: {f.name} - {ex}")

        # 閉じるボタン
        tk.Button(top, text="閉じる", font=FONT_BOLD, bg="#546E7A", fg="white",
                  relief="flat", padx=20,
                  command=top.destroy).pack(pady=10)

    def show_main_help(self):
        help_data = {
            "概要": "AIを用いた部品の欠落・個数検査システムです。\n\n【基本的な流れ】\n1. 設定画面でカメラや判定条件を整える。\n2. コミット番号を設定する。\n3. トリガー待ち状態になります。設定された順序（上から順）でトリガーが入ると、撮影・判定が行われます。",
            "検査モード": "自動判定を行う通常モードです。\n・トリガーが順番通りに入ると判定が開始されます。\n・設定された条件（個数など）を満たせばOK信号を出力します。\n・判定結果（OK/NG/SKIP）フォルダに画像を自動保存します。",
            "撮影モード": "判定を行わず、画像を収集するモードです。\n・リトライ回数分の画像を全て保存し、学習用データの収集に使用します。",
            "コミット番号": "ファイル名に含まれる4桁の管理番号です。\n・1サイクル（全トリガー完了）ごとに自動で+1されます。\n・「番号入力」から手動設定も可能です。",
            "NG履歴とお知らせ": "最近のNG判定が簡易表示されます。\n・ダブルクリックで画像を確認できます。\n・ステータスバーには現在の「撮影中」「検査中」などの状態が表示されます。"
        }
        HelpWindow(self.root, "操作ヘルプ", help_data)

    def open_settings(self):
        # 設定画面を開く。
        # NOTE: 以前は競合回避のためここでカメラを解放していましたが、
        # 判定しきい値のライブプレビューを有効にするため、維持するように変更します。
        # with self.camera_lock:
        #     temp_caps = self.caps
        #     self.caps = {}
        # 
        # for cap in temp_caps.values():
        #     cap.release()
            
        SettingsDialog(self.root, self.settings, self.on_settings_closed)

    def on_settings_closed(self):
        self.setup_hardware()
        self.v_mode.set(self.settings.data["inference"].get("mode", "inspection"))
        self.update_mode_ui()

    def update_mode_ui(self):
        m = self.v_mode.get()
        if m == "inspection":
            self.btn_insp.config(bg=COLOR_ACCENT, fg="black")
            self.btn_rec.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
            self.update_status("検査モード 待機中", COLOR_BG_PANEL)
        else:
            self.btn_insp.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
            self.btn_rec.config(bg=COLOR_WARNING, fg="black")
            self.update_status("撮影モード 実行中", COLOR_ACCENT)

    # ------------------------------------------------------------------
    # プレビューループ
    # ------------------------------------------------------------------
    def _preview_loop(self):
        """Raspi 5向け軽量プレビューループ"""
        while self.running:
            if self.preview_paused:
                time.sleep(0.1)
                continue

            t_start = time.time()
            
            # ロックの保持時間を最小限にするため、キャプチャ対象のリストをコピーして取得する
            with self.camera_lock:
                current_caps = list(self.caps.items())
            
            for cid, cap in current_caps:
                # このループ内ではロックを保持しないため、他のスレッドが self.caps を変更可能
                try:
                    if cap.grab():
                        ret, frame = cap.retrieve()
                        if ret:
                            self.last_frames[cid] = frame  # Numpy配列は新規生成されるためcopy不要（負荷削減）
                            # Tkinterのイベントキュー詰まりによるカクつきを防止
                            # (描画が追いつかない場合は、重いリサイズ・色変換・PIL変換そのものをスキップする)
                            if not getattr(self.cam_labels.get(cid), 'is_updating', False):
                                self.cam_labels[cid].is_updating = True
                                
                                def _upd_live(c=cid, f_data=frame):
                                    if c in self.cam_labels:
                                        try:
                                            # 重い処理をルートスレッド（Tkinter）側に逃がさず、
                                            # かといって描画キューが詰まらないように制限をかける
                                            preview_res = self.settings.data["storage"].get("preview_res", "320x240")
                                            if preview_res != "プレビューなし":
                                                try:
                                                    pw, ph = map(int, preview_res.split('x'))
                                                except Exception:
                                                    pw, ph = 320, 240
                                                
                                                img = cv2.resize(f_data, (pw, ph), interpolation=cv2.INTER_LINEAR)
                                                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                                                pil_img = Image.fromarray(img)
                                                
                                                tk_img = ImageTk.PhotoImage(pil_img)
                                                self.cam_labels[c].config(image=tk_img)
                                                self.cam_labels[c].img = tk_img
                                        except Exception: pass
                                        finally:
                                            self.cam_labels[c].is_updating = False

                                self.root.after(0, _upd_live)
                except Exception as e:
                    self.logger.error(f"Preview error (cid={cid}): {e}")

            fps = self.settings.data["inference"].get("preview_fps", 10)
            elapsed = time.time() - t_start
            wait_time = max(0.01, (1.0 / max(0.1, float(fps))) - elapsed)
            time.sleep(wait_time)

    # ------------------------------------------------------------------
    # 画像保存
    # ------------------------------------------------------------------
    def save_result_images(self, result_type, frame, camera_name, pattern_name,
                           confidence=1.0, trig_name="Trig1", burst_index=None):
        """命名規則に従って画像を保存する"""
        if frame is None:
            return None

        # 設定から解像度を取得してリサイズ
        if result_type == "REC":
            res_key = "res_record"
        elif result_type == "NG_RAW":
            # NG_RAW は NG と同じ解像度設定を使う
            res_key = "res_ng"
        else:
            res_key = f"res_{result_type.lower()}"
        res_setting = self.settings.data["storage"].get(res_key, "640x480")
        
        if res_setting == "保存しない":
            return None
            
        save_frame = frame
        if "x" in res_setting:
            try:
                w, h = map(int, res_setting.split("x"))
                save_frame = cv2.resize(frame, (w, h))
            except Exception: pass

        # ファイル名を組み立て: (判定結果)_(コミット番号)_(パターン名)_(カメラ名)_(トリガー名)_(信頼度)
        b_suffix = f"_{burst_index:02d}" if burst_index is not None else ""
        filename = (f"{result_type}_{self.commit_number:04d}_{pattern_name}_"
                    f"{camera_name}_{trig_name}{b_suffix}_{confidence:.2f}.jpg")

        # ファイル名に使えない文字を除去
        filename = "".join([c for c in filename if c not in '<>:"/\\|?*'])
        res_dir = self.get_results_dir()
        save_dir = res_dir / "images" / result_type
        save_dir.mkdir(parents=True, exist_ok=True)
        
        save_path = save_dir / filename
        
        # 保存を実行
        success = cv2.imwrite(str(save_path), save_frame)
        if success:
            self.logger.info(f"保存成功: {filename}")
        else:
            self.logger.error(f"保存失敗: {save_path}")
            
        return save_path

    def append_to_csv(self, pattern_name, camera_name, class_name, detected_count, res_type, confidence):
        """CSVファイルに判定結果を記録する"""
        today = datetime.datetime.now().strftime('%Y%m%d')
        now_time = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        res_dir = self.get_results_dir()
        csv_dir = res_dir / "csv"
        csv_dir.mkdir(parents=True, exist_ok=True) # ディレクトリ作成を確実に
        csv_file = csv_dir / f"inspection_results_{today}.csv"
        
        file_exists = csv_file.exists()
        header = ["日時", "コミット番号", "パターン名", "カメラ名", "判定対象クラス名", "検出個数", "判定結果", "信頼度"]
        data = [now_time, f"{self.commit_number:04d}", pattern_name, camera_name, class_name, detected_count, res_type, f"{confidence:.2f}"]
        
        try:
            # newline='' は csvモジュールの推奨設定 (OSごとの改行不整合を防ぐ)
            with open(csv_file, 'a', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                if not file_exists:
                    writer.writerow(header)
                writer.writerow(data)
        except Exception as e:
            self.logger.error(f"CSV書き込みエラー: {e}")

    def _evaluate_conditions(self, conditions, detections):
        """
        複数条件による判定を行う。

        Args:
            conditions: [{"class": "dog", "count": "2"}, {"class": "cat", "count": "1"}]
            detections: {"dog": 3, "cat": 1, ...} クラスごとの検出個数

        Returns:
            "OK" | "NG" | "SKIP"
        """
        # --- 判定ロジックの再確認用ログ ---
        self.logger.debug(f"DEBUG: evaluate_conditions - conditions={conditions}, detections={detections}")

        # 条件が空ならスキップ
        if not conditions:
            return "SKIP"

        # 全条件を満たすかチェック (AND条件)
        for cond in conditions:
            cls_name = cond.get("class", "").strip()
            count_val = cond.get("count")
            
            # 基準個数は空欄NG (空欄の場合は判定失敗とする)
            if count_val is None or str(count_val).strip() == "":
                self.logger.info(f"判定NG: 基準個数が設定されていません (クラス: {cls_name or '全検出数'})")
                return "NG"
                
            try:
                required = int(str(count_val).strip())
            except ValueError:
                self.logger.error(f"判定NG: 基準個数が不正な数値です ({count_val})")
                return "NG"

            # 対象クラスの検出数を取得
            if cls_name:
                actual = detections.get(cls_name, 0)
            else:
                # クラス未指定の場合は全クラスの合計
                actual = sum(detections.values())

            # 判定ロジック: 一致判定 (基準個数と同じならOK、それ以外はNG)
            if actual != required:
                self.logger.info(f"判定NG: {cls_name or '全検出数'} が不一致 (基準={required}, 実際={actual})")
                return "NG"
                    
        return "OK"

    def _capture_burst_images(self, retries, interval):
        """指定回数のバースト撮影を行い、フレームのリストを返す
        
        パフォーマンス改善: grab() でフレームをバッファし、ロック外で retrieve() する。
        これによりカメラロックの保持時間を最小化し、プレビューループをブロックしない。
        """
        captured_frames = []
        d = self.settings.data
        cam_names = {c["id"]: c["name"] for c in d["cameras"]}

        for shot_idx in range(max(1, retries)):
            # --- (1) すべてのカメラで grab() (フレーム取得の予約) ---
            with self.camera_lock:
                grabbed = {}
                for cid, cap in self.caps.items():
                    grabbed[cid] = cap.grab()

            # --- (2) ロック外で retrieve() (デコード処理) ---
            shot_data = []
            for cid, cap in list(self.caps.items()):
                if grabbed.get(cid):
                    ret, frame = cap.retrieve()
                    if ret:
                        shot_data.append((cid, cam_names.get(cid, cid), frame))

            if shot_data:
                captured_frames.append(shot_data)
            if shot_idx < retries - 1 and interval > 0:
                time.sleep(interval)
        return captured_frames

    def _inspect_frames(self, captured_frames, mode, is_skip, pat_id, trig_id, pat_name, trig_name):
        """収集したフレームに対してAI推論と条件判定を行い、結果を統合して返す"""
        d = self.settings.data
        results = []
        final_best_frames = {}

        for burst_idx, shot_group in enumerate(captured_frames):
            shot_results = []
            for cid, cam_name, frame in shot_group:
                if mode == "recording":
                    # 撮影モード: 保存のみ（バーストごとに保存）
                    save_needed = True
                    if is_skip and d["storage"].get("res_record_skip", "") == "保存しない":
                        save_needed = False
                    if save_needed:
                        # サイクル中は同じコミット番号を使用、全バースト保存
                        self.save_result_images("REC", frame, cam_name, pat_name, 
                                                trig_name=trig_name, burst_index=burst_idx + 1)
                    shot_results.append("OK")
                else:
                    # ---- 推論実行 ----
                    detections = {}
                    confidence = 0.0
                    
                    if self.model:
                        try:
                            # 判定閾値を取得
                            threshold = d["inference"].get("threshold", 0.5)
                            # 実際のモデル推論 (閾値を適用)
                            res = self.model.predict(frame, conf=threshold, verbose=False)[0]
                            # バウンディングボックスを描画した画像を取得
                            plot_frame = res.plot()
                            
                            # クラスごとの個数を集計 (念のためここでも閾値チェック)
                            for box in res.boxes:
                                conf_val = float(box.conf[0])
                                if conf_val < threshold:
                                    continue
                                    
                                cls_id = int(box.cls[0])
                                cls_name = res.names[cls_id]
                                detections[cls_name] = detections.get(cls_name, 0) + 1
                                # 最も高い信頼度を代表値にする
                                confidence = max(confidence, conf_val)
                            
                            # 描画済みフレームを使用
                            frame_to_save = plot_frame
                        except Exception as e:
                            import traceback
                            self.logger.error(f"推論実行エラー: {e}")
                            self.logger.error(traceback.format_exc())
                            detections = {}
                            confidence = 0.0
                            frame_to_save = frame
                    else:
                        # モデル未設定時はシミュレーションモード
                        detections = {"object": 1}
                        confidence = random.uniform(0.85, 0.99)
                        frame_to_save = frame
                        
                    total_detected = sum(detections.values())

                    conditions = []
                    if not is_skip and pat_id:
                        stage = d["patterns"][pat_id]["stages"].get(trig_id, {})
                        cond_data = stage.get("conditions", [])
                        # 古いリスト形式（全カメラ共通）か、新しい辞書形式（カメラ別）の判定
                        if isinstance(cond_data, list):
                            conditions = cond_data
                        else:
                            conditions = cond_data.get(str(cid), [])

                    if is_skip:
                        res_type = "SKIP"
                        total_detected = 0
                        confidence = 0.00
                    else:
                        res_type = self._evaluate_conditions(conditions, detections)
                        if total_detected == 0:
                            confidence = 0.00

                    cond_summary = ", ".join(
                        f"{c.get('class', '*')}x{c.get('count', '?')}" for c in conditions
                    ) if conditions else "-"
                    
                    # ログの「実際検出数」と合わせるため、全検出結果の要約を作成
                    det_summary = ", ".join(f"{k}:{v}" for k, v in detections.items()) if detections else "0"

                    shot_results.append(res_type)
                    
                    if (cid, cam_name) not in final_best_frames or res_type == "OK":
                         final_best_frames[(cid, cam_name)] = (frame_to_save, frame, res_type, confidence, cond_summary, det_summary)

            if shot_results:
                results = shot_results
                # 検査モードの場合のみ、全OKならループを抜ける（バースト終了）
                if mode == "inspection" and all(r == "OK" for r in results):
                    break
                    
        return results, final_best_frames

    def process_inspection(self, trig_id):
        """検査または撮影のメインプロセス"""
        d = self.settings.data
        inference_cfg = d["inference"]
        mode = inference_cfg.get("mode", "inspection")


        # --- トリガー順序制御 ---
        trig_list = [t["id"] for t in d["gpio"]["triggers"]]
        if not trig_list:
            self.logger.warning("トリガーが設定されていません。無視します。")
            return

        expected_trig = trig_list[self.cycle_trig_idx]
        if trig_id != expected_trig:
            expected_name = next((t["name"] for t in d["gpio"]["triggers"] if t["id"] == expected_trig), expected_trig)
            self.logger.warning(f"順序外のトリガーを無視: 受信={trig_id}, 期待={expected_name}")
            return

        # --- ステータス表示 (正当なトリガーの場合のみ) ---
        status_msg = "撮影中..." if mode == "recording" else "検査中..."
        self.update_status(status_msg, COLOR_ACCENT)

        # 1つ目のトリガーが入った時点でその時のセレクター状態でパターンを固定する
        if self.cycle_active_pat_id is None:
            self.cycle_active_pat_id = self.get_current_pattern()
            self.cycle_fired_trigs = set()
            self.cycle_trig_idx = 0 # 念のため
            # ログ出力はパターンの名前が確定した後で行う

        # 固定されたパターンを使用
        pat_id = self.cycle_active_pat_id
        if not pat_id:
            pat_name = "SKIP"
            is_skip = True
            required_trig_ids = {trig_id} # スキップ時は入ってきたトリガーのみ
        else:
            pat = d["patterns"][pat_id]
            pat_name = pat["name"]
            is_skip = False
            # このパターンに必要なトリガーを取得。
            # ただし GPIO に実際に設定されているトリガーのうち、
            # 条件が設定されている（1つ以上ある）トリガーのみに絞る
            # (設定トリガーが1つだけの場合でも正常にサイクルが完了できるようにする)
            configured_trig_ids = set(t["id"] for t in d["gpio"]["triggers"])
            required_trig_ids = set(
                tid for tid, stage in pat["stages"].items() 
                if stage.get("conditions")
            ) & configured_trig_ids
            
            if not required_trig_ids:
                # 全てのトリガーが条件なしの場合、少なくとも自分自身で完了する
                required_trig_ids = {trig_id}

        # 1つ目のトリガーの場合のみ開始ログ（名前解決後）
        if len(self.cycle_fired_trigs) == 0:
            self.logger.info(f"--- サイクル開始 (パターン: {pat_name}) ---")

        self.v_pat_name.set(pat_name)
        self.cycle_fired_trigs.add(trig_id)
        self.cycle_trig_idx += 1 # 次のトリガーへ
        if self.cycle_trig_idx >= len(trig_list):
            self.cycle_trig_idx = 0 # リストの最後まで来たら先頭に戻る

        # トリガー名を取得 (保存用)
        trig_info = next((t for t in d["gpio"]["triggers"] if t["id"] == trig_id), None)
        trig_name = trig_info["name"] if trig_info else str(trig_id)

        # --- 先行バースト撮影 ---
        retries = inference_cfg.get("max_retries", 5)
        interval = inference_cfg.get("burst_interval", 0.5)
        captured_frames = self._capture_burst_images(retries, interval)

        # --- 判定処理 ---
        results, final_best_frames = self._inspect_frames(
            captured_frames, mode, is_skip, pat_id, trig_id, pat_name, trig_name
        )

        # --- 保存 & 記録 ---
        if mode == "recording":
            self.update_status(f"撮影保存完了 (#{self.commit_number:04d})", COLOR_OK)
            # 撮影モード時は少し長めに完了表示を出し、連続動作を防ぐ（0.5秒程度）
            time.sleep(1) 
            self.update_status("撮影モード 待機中", COLOR_ACCENT)
        elif mode == "inspection":
            display_frames = {}
            for (cid, cam_name), (frame, raw_frame, res_type, conf, cls_name, det_cnt) in final_best_frames.items():
                self.logger.info(f"判定結果: カメラ={cam_name}, 条件={cls_name}, 検出数={det_cnt}, 結果={res_type}, 信頼度={conf:.2f}")
                res_setting = d["storage"].get(f"res_{res_type.lower()}", "640x480")
                if res_setting != "保存しない":
                    self.save_result_images(res_type, frame, cam_name, pat_name, 
                                            confidence=conf, trig_name=trig_name)
                    if res_type == "NG":
                        self.save_result_images(RESULTS_SUBDIR_NG_RAW, raw_frame, cam_name, pat_name,
                                                confidence=conf, trig_name=trig_name)
                self.append_to_csv(pat_name, cam_name, cls_name, det_cnt, res_type, conf)

                if res_type == "NG":
                    self.add_history(trig_id) # NG履歴にも現在のコミット番号で追加

                # プレビュー表示用のリサイズ & PIL.Image変換 (Tkinter非依存)
                preview_res = self.settings.data["storage"].get("preview_res", "320x240")
                try:
                    pw, ph = map(int, preview_res.split('x'))
                except Exception: pw, ph = 320, 240
                
                rgb = cv2.cvtColor(cv2.resize(frame, (pw, ph)), cv2.COLOR_BGR2RGB)
                display_frames[cid] = Image.fromarray(rgb)

            # 結果表示タイマー開始
            self.result_display_frames = display_frames
            display_time = d["inference"].get("result_display_time", 2.0)
            self.result_display_until = time.time() + display_time

        # --- サイクル完了判定 ---
        # 1. パターンに設定された「必要なトリガー」を全て消化した場合
        # 2. または、ハードウェア設定されている全トリガーの順序を一周した場合 (物理的なワークの入れ替わり)
        is_cycle_complete = required_trig_ids.issubset(self.cycle_fired_trigs)
        
        # ハードウェアトリガーが1つの場合は常に完了とみなす
        if len(trig_list) <= 1:
            is_cycle_complete = True
        # 最後のトリガーを終えてインデックスが0に戻った場合も強制完了 (シーケンスの同期)
        elif self.cycle_trig_idx == 0:
            is_cycle_complete = True

        if is_cycle_complete:
            self.logger.info(f"--- サイクル完了 (#{self.commit_number:04d}) ---")
            self.adjust_commit(1)    # ここで初めて次の番号へ
            self.cycle_active_pat_id = None
            self.cycle_fired_trigs.clear()
            self.cycle_trig_idx = 0  # 念のためリセット
        else:
            next_trig_id = trig_list[self.cycle_trig_idx]
            next_trig_name = next((t["name"] for t in d["gpio"]["triggers"] if t["id"] == next_trig_id), str(next_trig_id))
            self.logger.info(f"サイクル継続中 (進捗: {len(self.cycle_fired_trigs)}/{len(required_trig_ids)}, 次待機: {next_trig_name})")

        # --- 出灯 / ブザー制御 (検査モードのみ) ---
        if mode != "inspection":
            return

        ok_time = inference_cfg.get("ok_output_time", 0.5)
        ng_time = inference_cfg.get("ng_output_time", "")
        has_ng = "NG" in results

        if has_ng:
            self.update_status(f"NG検出 ({pat_name})", COLOR_NG)
            if self.out_ng: self.out_ng.on()
            try:
                ng_sec = float(ng_time)
                ng_msec = int(ng_sec * 1000)
                def _ng_off():
                    if self.out_ng: self.out_ng.off()
                self.root.after(max(10, ng_msec), _ng_off)
            except: pass

            bp = inference_cfg.get("buzzer_path", "")
            if bp and PYGAME_AVAILABLE and os.path.exists(bp):
                try:
                    _ensure_mixer()
                    pygame.mixer.music.load(bp)
                    pygame.mixer.music.play(-1)
                except: pass

        elif results and all(r in ("OK", "SKIP") for r in results):
            # 全てOKまたはSKIPならOKステータス（1つでもOKがあればOK色）
            status_color = COLOR_OK if "OK" in results else COLOR_BG_PANEL

            if "OK" in results and self.out_ok:
                self.update_status(f"OK ({pat_name})", status_color)
                self.out_ok.on()
                ok_msec = int(ok_time * 1000)
                def _ok_off():
                    if self.out_ok: self.out_ok.off()
                self.root.after(max(10, ok_msec), _ok_off)

                ok_bp = inference_cfg.get("ok_buzzer_path", "")
                if ok_bp and PYGAME_AVAILABLE and os.path.exists(ok_bp):
                    try:
                        _ensure_mixer()
                        pygame.mixer.music.load(ok_bp)
                        pygame.mixer.music.play(0)
                    except: pass
            
            if "SKIP" in results:
                self.update_status(f"SKIP ({pat_name})", COLOR_BG_PANEL)

    def update_status(self, text, color):
        """ステータス表示とヘッダー色の更新"""
        self.lbl_status.config(text=text, fg="white" if color != COLOR_BG_PANEL else COLOR_ACCENT)
        self.header.config(bg=color)
        self.lbl_status.config(bg=color)
        self.lbl_clock.config(bg=color)
        # ヘッダー内の全ウィジェットの背景を合わせる（必要に応じて）
        for w in self.header.winfo_children():
            try:
                if not isinstance(w, tk.Button): # ボタンの色は変えない
                    w.config(bg=color)
            except: pass

    def add_history(self, trig_id):
        now = datetime.datetime.now()
        t_str = now.strftime("%m/%d %H:%M:%S")
        # 'time' を記録しておくことで、同一コミット番号でも今回のセッションの画像のみ特定できる
        self.ng_history.append({"commit": self.commit_number, "trigger": trig_id, "time": now})
        self.lb_history.insert(0, f"[{t_str}] #{self.commit_number:04d} NG")

    def _main_logic_loop(self):
        while self.running:
            try:
                trig_id = self.trigger_queue.get(timeout=1.0)
                self.process_inspection(trig_id)
                # --- 追加修正: キューのフラッシュ ---
                # 撮影モードや保存処理中にチャタリングや信号の重なりで溜まった
                # 古いトリガーイベントをすべて破棄する
                if not self.trigger_queue.empty():
                    self.logger.info("処理中に発生した余剰なトリガーをスキップします")
                    while not self.trigger_queue.empty():
                        try:
                            self.trigger_queue.get_nowait()
                        except queue.Empty:
                            break
                # ----------------------------------
            except queue.Empty:
                pass

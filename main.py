#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - エントリーポイント

使い方:
    python -m inspection_app.main
    または
    python inspection_app/main.py
"""

import sys
import os

# pygameのサポートメッセージ（Hello from the pygame community...）を非表示にする
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

# スクリプトを単体で実行する場合に code/ ディレクトリを検索パスに追加する
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # PyInstallerでパッケージ化された場合
    _code_dir = sys._MEIPASS
else:
    # 通常の実行時
    
    _here = os.path.dirname(os.path.abspath(__file__))
    _code_dir = os.path.dirname(_here)

if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

from inspection_app.modules.app import InspectionSystem  # noqa: E402 (パス追加後にimport)


if __name__ == "__main__":
    app = InspectionSystem()
    app.root.mainloop()
